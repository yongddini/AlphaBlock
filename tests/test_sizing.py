"""리스크 기반 포지션 사이징(WAN-26) 순수 함수 테스트.

손절 거리 반비례·한도 clamp·최소단위 내림·엣지(0 거리·상한·최소 수량)를
손으로 계산한 값으로 검증한다.
"""

from __future__ import annotations

import pytest

from execution import PositionSizingParams, position_size


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
