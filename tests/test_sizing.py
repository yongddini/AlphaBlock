"""리스크 기반 포지션 사이징(WAN-26) 순수 함수 테스트.

손절 거리 반비례·한도 clamp·최소단위 내림·엣지(0 거리·상한·최소 수량)를
손으로 계산한 값으로 검증한다.
"""

from __future__ import annotations

import pytest

from execution import PositionSizingParams, position_size, size_with_reason


def test_quantity_inversely_proportional_to_stop_distance() -> None:
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0)
    # 리스크 = 10_000 × 0.01 = 100. 손절 거리 10 → 수량 10.
    qty_far = position_size(equity=10_000.0, entry_price=100.0, stop_price=90.0, params=params)
    # 손절 거리 5 → 수량 20 (거리 절반이면 수량 2배).
    qty_near = position_size(equity=10_000.0, entry_price=100.0, stop_price=95.0, params=params)
    assert qty_far == pytest.approx(10.0)
    assert qty_near == pytest.approx(20.0)
    assert qty_near == pytest.approx(qty_far * 2.0)


def test_short_uses_absolute_distance() -> None:
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0)
    # 손절가가 진입가 위(숏)여도 절대 거리(10)로 동일하게 산출.
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=110.0, params=params)
    assert qty == pytest.approx(10.0)


def test_notional_capped_by_leverage() -> None:
    # 손절 거리 1 → 미제한 수량 100 (명목가 10_000). leverage=1 → 명목가 상한 10_000/100=100주.
    # 실제로는 1×자본이라 100주가 상한과 같아 딱 맞는다. 거리 0.5로 낮추면 상한이 물린다.
    params = PositionSizingParams(risk_per_trade=0.01, leverage=1.0)
    # 거리 0.5 → 미제한 수량 200(명목가 20_000). 상한 = 자본×leverage/진입가 = 100주.
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.5, params=params)
    assert qty == pytest.approx(100.0)  # 레버리지 상한에 clamp


def test_max_notional_fraction_tightens_cap() -> None:
    # leverage=10이지만 max_notional_fraction=2 → 실제 상한 = min(10, 2) = 자본×2.
    params = PositionSizingParams(risk_per_trade=0.05, leverage=10.0, max_notional_fraction=2.0)
    # 미제한: 리스크 500 / 거리 0.5 = 1000주(명목가 100_000). 상한 자본×2=20_000 → 200주.
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.5, params=params)
    assert qty == pytest.approx(200.0)


def test_qty_step_rounds_down() -> None:
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0, qty_step=0.5)
    # 미제한 수량 = 100 / 7 ≈ 14.2857 → 0.5 단위 내림 = 14.0.
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=93.0, params=params)
    assert qty == pytest.approx(14.0)


def test_min_qty_skips_when_below() -> None:
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0, min_qty=15.0)
    # 산출 수량 10 < min_qty 15 → 스킵(0).
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=90.0, params=params)
    assert qty == pytest.approx(0.0)


def test_zero_stop_distance_skips() -> None:
    params = PositionSizingParams()
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=100.0, params=params)
    assert qty == pytest.approx(0.0)


def test_min_stop_distance_fraction_skips_when_too_close() -> None:
    params = PositionSizingParams(min_stop_distance_fraction=0.02)  # 최소 2%
    # 손절 거리 1% (99.0) < 최소 2% → 스킵.
    too_close = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.0, params=params)
    assert too_close == pytest.approx(0.0)
    # 손절 거리 3% (97.0) ≥ 최소 2% → 진입.
    params_ok = PositionSizingParams(
        risk_per_trade=0.01, leverage=100.0, min_stop_distance_fraction=0.02
    )
    ok = position_size(equity=10_000.0, entry_price=100.0, stop_price=97.0, params=params_ok)
    assert ok > 0.0


def test_default_min_stop_distance_floor_enabled() -> None:
    """WAN-79: 기본값이 0.003으로 켜져, 손절폭 0.3% 미만 진입이 하한에 걸려 스킵된다."""
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0)
    assert params.min_stop_distance_fraction == pytest.approx(0.003)
    # 손절 거리 0.1%(99.9) < 기본 하한 0.3% → 스킵(0).
    too_close = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.9, params=params)
    assert too_close == pytest.approx(0.0)
    # 하한이 원인임을 격리: 같은 거래를 하한 0으로 두면 진입한다.
    no_floor = PositionSizingParams(
        risk_per_trade=0.01, leverage=100.0, min_stop_distance_fraction=0.0
    )
    assert position_size(equity=10_000.0, entry_price=100.0, stop_price=99.9, params=no_floor) > 0.0
    # 손절 거리 0.5%(99.5) ≥ 기본 하한 → 진입.
    ok = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.5, params=params)
    assert ok > 0.0


def test_non_positive_equity_skips() -> None:
    params = PositionSizingParams()
    assert position_size(equity=0.0, entry_price=100.0, stop_price=90.0, params=params) == 0.0
    assert position_size(equity=-5.0, entry_price=100.0, stop_price=90.0, params=params) == 0.0


def test_non_positive_entry_price_raises() -> None:
    params = PositionSizingParams()
    with pytest.raises(ValueError, match="entry_price"):
        position_size(equity=10_000.0, entry_price=0.0, stop_price=90.0, params=params)


def test_params_validation_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        PositionSizingParams(risk_per_trade=0.0)
    with pytest.raises(ValueError):
        PositionSizingParams(risk_per_trade=1.5)
    with pytest.raises(ValueError):
        PositionSizingParams(leverage=0.0)
    with pytest.raises(ValueError):
        PositionSizingParams(min_stop_distance_fraction=1.0)


# --------------------------------------------------------------------------- #
# 사이징 2안: 명목 고정 (WAN-108)
# --------------------------------------------------------------------------- #


def test_default_sizing_mode_is_risk_pct() -> None:
    """기본값 불변 — 2안을 추가해도 채택 경로는 손절 역산 그대로다."""
    assert PositionSizingParams().sizing_mode == "risk_pct"


def test_fixed_notional_ignores_stop_distance() -> None:
    """명목 고정: 손절이 멀든 가깝든 같은 명목 = 자본 × f (1안과 정반대 성질)."""
    params = PositionSizingParams(
        sizing_mode="fixed_notional",
        notional_fraction=1.0,
        leverage=5.0,
        min_stop_distance_fraction=0.0,
    )
    far = position_size(equity=10_000.0, entry_price=100.0, stop_price=90.0, params=params)
    near = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.0, params=params)
    # 명목 = 자본 전액(10_000) → 진입가 100에서 100주. 손절 거리와 무관하게 동일.
    assert far == pytest.approx(100.0)
    assert near == pytest.approx(100.0)


def test_fixed_notional_loss_scales_with_stop_distance() -> None:
    """2안의 핵심 위험: 손절 시 손실이 자리마다 다르고 **상한이 없다**.

    1안은 어느 자리든 손절 손실이 자본의 1%지만, 2안은 손절 거리에 비례한다 —
    `docs/decisions/wan103.md` §5가 말로만 남긴 경고가 이 산식이다.
    """
    params = PositionSizingParams(
        sizing_mode="fixed_notional",
        notional_fraction=1.0,
        leverage=5.0,
        min_stop_distance_fraction=0.0,
    )
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=96.0, params=params)
    loss = abs(100.0 - 96.0) * qty
    assert loss == pytest.approx(400.0)  # 자본의 4% — 1%가 아니다.


def test_fixed_notional_fraction_scales_quantity() -> None:
    params = PositionSizingParams(sizing_mode="fixed_notional", notional_fraction=0.5, leverage=5.0)
    qty = position_size(equity=10_000.0, entry_price=100.0, stop_price=90.0, params=params)
    assert qty == pytest.approx(50.0)  # 명목 = 5_000.


def test_fixed_notional_respects_portfolio_notional_cap() -> None:
    """f=1 · 천장 5배면 5자리까지 차고 6번째는 여유가 없어 스킵된다(사용자 확정 규칙)."""
    params = PositionSizingParams(sizing_mode="fixed_notional", notional_fraction=1.0, leverage=5.0)
    fifth = position_size(
        equity=10_000.0,
        entry_price=100.0,
        stop_price=90.0,
        params=params,
        open_notional=40_000.0,
    )
    assert fifth == pytest.approx(100.0)  # 남은 여유 10_000 → 온전한 한 자리.
    sixth = position_size(
        equity=10_000.0,
        entry_price=100.0,
        stop_price=90.0,
        params=params,
        open_notional=50_000.0,
    )
    assert sixth == 0.0  # 천장 소진.


def test_fixed_notional_still_honors_min_stop_distance() -> None:
    """손절 가드는 **셋업**을 거르는 것이라 두 모드에 똑같이 걸린다.

    모드마다 다른 셋업을 받으면 WAN-108 사이징 대조표에서 두 열의 차이가
    "사이징 효과 + 셋업 풀 차이"가 돼 축이 오염된다.
    """
    params = PositionSizingParams(sizing_mode="fixed_notional", notional_fraction=1.0, leverage=5.0)
    # 손절 거리 0.1% < 기본 하한 0.3% → 두 모드 모두 스킵.
    assert position_size(equity=10_000.0, entry_price=100.0, stop_price=99.9, params=params) == 0.0


def test_fixed_notional_rejects_bad_fraction() -> None:
    with pytest.raises(ValueError):
        PositionSizingParams(notional_fraction=0.0)
    with pytest.raises(ValueError):
        PositionSizingParams(sizing_mode="nonsense")


# --------------------------------------------------------------------------- #
# WAN-244 — 용량 상한(일거래량 비례 절대 명목 상한)
# --------------------------------------------------------------------------- #


def test_adv_cap_default_off_ignores_adv_usd() -> None:
    """기본(`max_notional_adv_fraction=None`)이면 `adv_usd`를 넘겨도 결과가 안 변한다 —
    이 항을 넣기 전과 비트 단위로 같다(기본 실행 재현의 사이징 단위 보장)."""
    params = PositionSizingParams(risk_per_trade=0.01, leverage=5.0)
    base = position_size(equity=1_000_000.0, entry_price=100.0, stop_price=90.0, params=params)
    with_adv = position_size(
        equity=1_000_000.0, entry_price=100.0, stop_price=90.0, params=params, adv_usd=1.0
    )
    assert with_adv == base  # None 상한 → adv_usd 완전 무시.


def test_adv_cap_binds_absolute_not_scaled_by_equity() -> None:
    """용량 상한은 **절대 달러**다 — 자본이 커져도 명목이 `k×ADV_usd`로 고정된다."""
    params = PositionSizingParams(
        risk_per_trade=0.01, leverage=5.0, max_notional_adv_fraction=0.005
    )
    # ADV = 1_000_000 → 상한 = 0.5% × 1_000_000 = 5_000 명목 → 진입가 100 → 50주.
    qty = position_size(
        equity=1_000_000.0,
        entry_price=100.0,
        stop_price=90.0,
        params=params,
        adv_usd=1_000_000.0,
    )
    assert qty == pytest.approx(50.0)
    assert qty * 100.0 == pytest.approx(5_000.0)  # 명목 = 상한.
    # 자본을 10배로 키워도 상한이 절대값이라 명목은 그대로 5_000이다(복리 착시 차단).
    qty_10x = position_size(
        equity=10_000_000.0,
        entry_price=100.0,
        stop_price=90.0,
        params=params,
        adv_usd=1_000_000.0,
    )
    assert qty_10x * 100.0 == pytest.approx(5_000.0)


def test_adv_cap_not_binding_when_liquidity_ample() -> None:
    """ADV가 충분히 크면 상한이 안 물리고 리스크 사이징 결과 그대로다."""
    params = PositionSizingParams(
        risk_per_trade=0.01, leverage=5.0, max_notional_adv_fraction=0.005
    )
    # 자본 10_000, 손절거리 10 → 리스크 사이징 수량 10(명목 1_000). 상한 = 0.5%×1e10 = 5e7 ≫ 1_000.
    qty = position_size(
        equity=10_000.0,
        entry_price=100.0,
        stop_price=90.0,
        params=params,
        adv_usd=10_000_000_000.0,
    )
    assert qty == pytest.approx(10.0)  # 상한 미발동 = 리스크 사이징 값.


def test_adv_cap_none_adv_usd_is_no_cap() -> None:
    """상한이 켜져 있어도 `adv_usd`가 없으면(워밍업 등) 걸지 않는다 — 조용히 0으로 만들지 않는다."""
    params = PositionSizingParams(
        risk_per_trade=0.01, leverage=5.0, max_notional_adv_fraction=0.005
    )
    qty = position_size(
        equity=1_000_000.0, entry_price=100.0, stop_price=90.0, params=params, adv_usd=None
    )
    # ADV 정보 없음 → 상한 없음 → 리스크 사이징(명목 100_000 = leverage 5×1M 한참 아래).
    assert qty == pytest.approx(1_000.0)


def test_adv_cap_rejects_non_positive_fraction() -> None:
    with pytest.raises(ValueError):
        PositionSizingParams(max_notional_adv_fraction=0.0)
    with pytest.raises(ValueError):
        PositionSizingParams(adv_window_days=0)


# -- 사이징 거부 사유 (WAN-275) -------------------------------------------------
#
# `size_with_reason`이 0을 낸 **바닥 이유**를 네 갈래(+최소수량)로 구분해 돌려주는지를
# 동작으로 고정한다. 진입 거부 표기가 "수량 0"이라는 증상 대신 어느 가드에 걸렸는지를
# 보이려면 이 구분이 정확해야 한다. 각 케이스는 그 사유로 실제로 0이 나오도록 구성한다.


def test_reason_ok_when_quantity_positive() -> None:
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0)
    qty, reason = size_with_reason(
        equity=10_000.0, entry_price=100.0, stop_price=90.0, params=params
    )
    assert qty > 0.0
    assert reason == "ok"


def test_reason_no_equity() -> None:
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0)
    qty, reason = size_with_reason(equity=0.0, entry_price=100.0, stop_price=90.0, params=params)
    assert qty == 0.0
    assert reason == "no_equity"


def test_reason_stop_too_tight_below_guard() -> None:
    """LINK 15m 케이스: 손절폭이 0.3% 하한 미만이라 사이징이 0을 낸다."""
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0)  # 하한 기본 0.3%
    # 진입 8.316663 · 손절 8.3 → 손절폭 ≈ 0.20% < 0.30% 하한(이슈 원 관찰).
    qty, reason = size_with_reason(
        equity=10_000.0, entry_price=8.316663, stop_price=8.3, params=params
    )
    assert qty == 0.0
    assert reason == "stop_too_tight"


def test_reason_stop_too_tight_when_distance_zero() -> None:
    """손절 참조가가 진입가와 겹쳐(거리 0) 사이징 불가여도 stop_too_tight로 묶인다."""
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0)
    qty, reason = size_with_reason(
        equity=10_000.0, entry_price=100.0, stop_price=100.0, params=params
    )
    assert qty == 0.0
    assert reason == "stop_too_tight"


def test_reason_notional_exhausted() -> None:
    """이미 열린 명목이 상한을 다 써 남은 여유가 없으면 notional_exhausted."""
    params = PositionSizingParams(risk_per_trade=0.01, leverage=1.0)
    # 상한 = 자본×leverage = 10_000. 이미 그만큼 열려 있으면 여유 0.
    qty, reason = size_with_reason(
        equity=10_000.0,
        entry_price=100.0,
        stop_price=90.0,
        params=params,
        open_notional=10_000.0,
    )
    assert qty == 0.0
    assert reason == "notional_exhausted"


def test_reason_capacity_cap_when_adv_zero() -> None:
    """용량 상한(ADV)이 명목을 0으로 clamp하면 capacity_cap — below_min_qty와 구분된다."""
    params = PositionSizingParams(
        risk_per_trade=0.01, leverage=100.0, max_notional_adv_fraction=0.005
    )
    qty, reason = size_with_reason(
        equity=10_000.0,
        entry_price=100.0,
        stop_price=90.0,
        params=params,
        adv_usd=0.0,  # ADV 0 → 상한 0 → 명목 0.
    )
    assert qty == 0.0
    assert reason == "capacity_cap"


def test_reason_below_min_qty() -> None:
    """내림·최소 수량에 걸려 0이 되면 below_min_qty(용량 상한과 다른 갈래)."""
    params = PositionSizingParams(risk_per_trade=0.01, leverage=100.0, min_qty=1_000.0)
    # 리스크 사이징 수량은 작은데 min_qty가 커서 걸린다.
    qty, reason = size_with_reason(
        equity=10_000.0, entry_price=100.0, stop_price=90.0, params=params
    )
    assert qty == 0.0
    assert reason == "below_min_qty"


def test_position_size_matches_size_with_reason_quantity() -> None:
    """`position_size`는 `size_with_reason`의 수량 성분과 비트 단위로 같다(래퍼 검산)."""
    params = PositionSizingParams(
        risk_per_trade=0.01, leverage=5.0, max_notional_adv_fraction=0.005, qty_step=0.1
    )
    for entry, stop, adv in [
        (100.0, 90.0, 10_000_000.0),
        (8.316663, 8.3, None),
        (100.0, 99.9, 1_000.0),
        (50.0, 40.0, 0.0),
    ]:
        qty_wrapper = position_size(
            equity=12_345.0,
            entry_price=entry,
            stop_price=stop,
            params=params,
            adv_usd=adv,
        )
        qty_detail, _reason = size_with_reason(
            equity=12_345.0,
            entry_price=entry,
            stop_price=stop,
            params=params,
            adv_usd=adv,
        )
        assert qty_wrapper == qty_detail  # 부동소수까지 동일.
