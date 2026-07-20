"""WAN-89 숏 부검 — 팔 배선·부검 지표·요약 로직 테스트.

격자 실행(DB·수십 분)이 아니라 **팔이 실제로 의도한 엔진을 타는지**(WAN-95/112/123이
반복해 겪은 "라벨만 붙는" 실패의 방지), 진입 경로 분류·손절 사후 추적·CSV 왕복·판정
문장을 손으로 만든 값으로 고정한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.models import PositionSide
from backtest.wan89_short_autopsy import (
    ARMS,
    ARMS_BY_NAME,
    DIAG_ARM,
    DiagRow,
    PnlRow,
    _buy_hold,
    _entry_path,
    _old_gate_pass,
    _revert_rate,
    diag_from_csv,
    diag_to_frame,
    pnl_from_csv,
    pnl_to_frame,
    verdict_for_tf,
)
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

# --------------------------------------------------------------------------- #
# 팔 배선 — 라벨이 아니라 실제 파라미터·설정을 본다
# --------------------------------------------------------------------------- #


def test_long_only_arm_is_the_adopted_default() -> None:
    """대조 팔은 채택 기본값 **그대로**여야 한다 — 여기가 어긋나면 표 전체가 기울어진다."""
    params = ARMS_BY_NAME["long_only"].params()
    default = ConfluenceParams()
    assert params.short_enabled is False
    assert params.rsi_gate_mode == default.rsi_gate_mode == "unconditional"
    assert params.retap_mode == default.retap_mode
    assert params.zone_limit_offset_bps == default.zone_limit_offset_bps
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"


def test_short_only_arm_disables_longs_in_config_not_just_params() -> None:
    """숏 단독은 `cfg.allow_long=False`로 **엔진 수준에서** 롱을 막아야 한다.

    `short_enabled=True`만 켜면 롱도 함께 돌아 `both`와 구분되지 않는다 — 그러면
    "숏 단독 OOS 부호"라는 0단 판정이 조용히 롱 성과를 재게 된다.
    """
    arm = ARMS_BY_NAME["short_only"]
    assert arm.params().short_enabled is True
    cfg = arm.config("1h")
    assert cfg.allow_long is False
    assert cfg.allow_short is True
    assert ARMS_BY_NAME["both"].config("1h").allow_long is True


@pytest.mark.parametrize(
    ("arm_name", "expected_gate"),
    [
        ("short_only", "unconditional"),
        ("short_gate_first_tap", "first_tap_free"),
        ("short_gate_extreme", "extreme"),
    ],
)
def test_gate_arms_pin_the_intended_rsi_gate(arm_name: str, expected_gate: str) -> None:
    assert ARMS_BY_NAME[arm_name].params().rsi_gate_mode == expected_gate


def test_retap_arm_only_changes_retap_mode() -> None:
    once = ARMS_BY_NAME["short_once"].params()
    base = ARMS_BY_NAME["short_only"].params()
    assert once.retap_mode == "once"
    assert base.retap_mode == "every_tap"
    differing = {
        field
        for field in ConfluenceParams.model_fields
        if getattr(once, field) != getattr(base, field)
    }
    assert differing == {"retap_mode"}


def test_diag_arm_runs_both_sides() -> None:
    """부검 지표는 롱·숏이 **같은 실행 안에서** 슬롯을 다투는 조건에서 나와야 한다."""
    assert DIAG_ARM.name == "both"
    assert DIAG_ARM.params().short_enabled is True
    assert DIAG_ARM.config("1h").allow_long is True


def test_arm_names_are_unique() -> None:
    assert len(ARMS_BY_NAME) == len(ARMS)


# --------------------------------------------------------------------------- #
# 진입 경로 분류 (가설 B)
# --------------------------------------------------------------------------- #


def _ob(direction: OrderBlockDirection, top: float, bottom: float) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=10.0,
        ob_low_volume=4.0,
        ob_high_volume=6.0,
    )


def test_entry_path_proximal_matches_offset_price() -> None:
    """규칙 1 체결가 = 오프셋을 얹은 근단가. 오프셋을 빼먹으면 전부 `band`로 오분류된다."""
    params = ConfluenceParams()
    ob = _ob(OrderBlockDirection.BULLISH, 100.0, 90.0)
    expected = params.apply_zone_limit_offset(params.zone_limit_price(ob), is_long=True)
    path, proximal_off = _entry_path(params, PositionSide.LONG, expected, ob)
    assert path == "proximal"
    assert proximal_off == expected
    assert proximal_off > 100.0  # 2bp가 체결이 쉬워지는 쪽(롱=위)으로 밀었다.


def test_entry_path_band_when_price_is_inside_zone() -> None:
    params = ConfluenceParams()
    ob = _ob(OrderBlockDirection.BULLISH, 100.0, 90.0)
    path, _ = _entry_path(params, PositionSide.LONG, 95.0, ob)
    assert path == "band"


def test_entry_path_short_uses_zone_bottom_as_proximal() -> None:
    """숏의 근단은 존 **하단**이고 오프셋은 아래로 민다 — 부호를 뒤집으면 분류가 반전된다."""
    params = ConfluenceParams()
    ob = _ob(OrderBlockDirection.BEARISH, 110.0, 100.0)
    _, proximal_off = _entry_path(params, PositionSide.SHORT, 105.0, ob)
    assert proximal_off < 100.0
    path, _ = _entry_path(params, PositionSide.SHORT, proximal_off, ob)
    assert path == "proximal"
    assert _entry_path(params, PositionSide.SHORT, 105.0, ob)[0] == "band"


# --------------------------------------------------------------------------- #
# 손절 사후 추적
# --------------------------------------------------------------------------- #


def _frame(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(len(prices))],
            "open": prices,
            "high": [p + 1.0 for p in prices],
            "low": [p - 1.0 for p in prices],
            "close": prices,
            "volume": [1.0] * len(prices),
        }
    )


def test_revert_rate_counts_only_the_favourable_direction() -> None:
    """숏은 진입가 **아래로** 되돌아와야 "털렸다"다. 롱 기준을 그대로 쓰면 부호가 뒤집힌다."""
    frame = _frame([100.0] * 3 + [80.0] * 10)
    stops = [(2 * 60_000, 100.0, PositionSide.SHORT)]
    assert _revert_rate(frame, stops, 5) == 1.0
    long_stops = [(2 * 60_000, 100.0, PositionSide.LONG)]
    assert _revert_rate(frame, long_stops, 5) == 0.0


def test_revert_rate_excludes_windows_past_the_end() -> None:
    """창이 데이터 끝을 넘으면 **판정 불가**로 빼야 한다 — 세면 구간 끝에서 비율이 눌린다."""
    frame = _frame([100.0] * 6)
    late = [(5 * 60_000, 100.0, PositionSide.SHORT)]
    assert _revert_rate(frame, late, 10) is None
    assert _revert_rate(frame, [], 10) is None


def test_revert_rate_mixes_judged_only() -> None:
    frame = _frame([100.0] * 3 + [80.0] * 4)
    stops = [
        (0, 100.0, PositionSide.SHORT),  # 판정 가능 · 되돌아옴
        (2 * 60_000, 100.0, PositionSide.SHORT),  # 판정 가능 · 되돌아옴
        (6 * 60_000, 100.0, PositionSide.SHORT),  # 창이 끝을 넘음 → 제외
    ]
    assert _revert_rate(frame, stops, 4) == 1.0


# --------------------------------------------------------------------------- #
# 옛 게이트 통과 판정 (가설 D)
# --------------------------------------------------------------------------- #


def test_old_gate_pass_is_direction_asymmetric() -> None:
    params = ConfluenceParams()
    assert _old_gate_pass(PositionSide.SHORT, 75.0, params) is True
    assert _old_gate_pass(PositionSide.SHORT, 55.0, params) is False
    assert _old_gate_pass(PositionSide.LONG, 25.0, params) is True
    assert _old_gate_pass(PositionSide.LONG, 55.0, params) is False


def test_old_gate_pass_is_inclusive_at_the_threshold() -> None:
    """옛 규칙은 `>=`/`<=`였다 — 경계를 배타로 바꾸면 통과율이 조용히 낮아진다."""
    params = ConfluenceParams()
    assert _old_gate_pass(PositionSide.SHORT, params.rsi_overbought, params) is True
    assert _old_gate_pass(PositionSide.LONG, params.rsi_oversold, params) is True


# --------------------------------------------------------------------------- #
# 장세 라벨 · 판정 문장 · 직렬화
# --------------------------------------------------------------------------- #


def test_buy_hold_uses_segment_endpoints() -> None:
    assert _buy_hold(_frame([100.0, 120.0, 50.0])) == pytest.approx(-0.5)


def _pnl(arm: str, segment: str, total_return: float, symbol: str = "BTC/USDT:USDT") -> PnlRow:
    return PnlRow(
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        arm=arm,
        num_bars=100,
        num_trades=10,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.1,
        sharpe=1.0,
        mean_r=0.2,
        fill_rate=0.8,
        eligible_setups=20,
        num_filled=16,
        funding_coverage=1.0,
        buy_hold=-0.4,
    )


def test_verdict_reports_sign_and_long_side_delta() -> None:
    rows = [
        _pnl("short_only", "oos", 0.0978),
        _pnl("long_only", "oos", 0.0875),
        _pnl("both", "oos", 0.2282),
    ]
    text = verdict_for_tf(pnl_to_frame(rows), "1h")
    assert "부호가 뒤집혔다" in text
    assert "+9.78%" in text
    assert "+14.07%p" in text  # both − long_only


def test_verdict_says_negative_when_short_still_loses() -> None:
    text = verdict_for_tf(pnl_to_frame([_pnl("short_only", "oos", -0.05)]), "1h")
    assert "여전히 마이너스" in text
    assert "0/1심볼" in text


def test_verdict_without_data_is_explicit() -> None:
    assert "판정 불가" in verdict_for_tf(pnl_to_frame([]), "15m")


def test_pnl_csv_round_trip(tmp_path: Path) -> None:
    rows = [_pnl("short_only", "oos", 0.1), _pnl("both", "is", -0.2, symbol="ETH/USDT:USDT")]
    path = tmp_path / "pnl.csv"
    pnl_to_frame(rows).to_csv(path, index=False)
    assert pnl_from_csv(path) == rows


def _diag(side: str) -> DiagRow:
    return DiagRow(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="oos",
        side=side,
        setups_no_band=100,
        setups_with_band=96,
        reject_rate=0.04,
        fill_rate_no_band=0.9,
        fill_rate_with_band=0.72,
        filled=69,
        path_proximal=50,
        path_band=19,
        r_shrink_median=0.8,
        qty_amp_median=1.25,
        stops=30,
        revert_10=0.4,
        revert_20=0.5,
        revert_50=None,
        fee_pct=0.0006,
        funding_pct=-0.0001,
        funding_total=-12.5,
        old_gate_pass_frac=0.37,
    )


def test_diag_csv_round_trip(tmp_path: Path) -> None:
    rows = [_diag("long"), _diag("short")]
    path = tmp_path / "diag.csv"
    diag_to_frame(rows).to_csv(path, index=False)
    assert diag_from_csv(path) == rows
