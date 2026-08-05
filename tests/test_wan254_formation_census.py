"""WAN-254 §1 형성 진입 census 회귀 테스트.

엔진(변위 필드)·census 형성 진입 walk·판정 게이트를 **동작**으로 고정한다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from backtest.wan254_formation_census import (
    CellRow,
    _cost_r,
    _formation_outcome,
    premise_verdict,
    rows_to_frame,
)
from strategy.models import OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import detect_order_blocks

# test_order_blocks.py에서 손으로 추적한 강세 OB 시나리오(swing_length=3).
# t=11 종가 112가 top.price 110을 돌파 → OB 확정.
_BULL_BARS = [
    (100, 102, 90, 95, 10),
    (95, 100, 93, 98, 10),
    (98, 101, 94, 99, 10),
    (99, 103, 95, 101, 10),
    (101, 110, 100, 108, 10),
    (108, 109, 104, 106, 15),
    (106, 107, 103, 105, 20),
    (105, 106, 102, 104, 25),
    (104, 105, 100, 102, 10),
    (102, 104, 99, 101, 10),
    (101, 103, 98, 100, 10),
    (100, 115, 99, 112, 30),
    (112, 113, 95, 97, 10),
]


def _make_df(bars: Sequence[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
    )


def _bull_params() -> OrderBlockParams:
    return OrderBlockParams(
        swing_length=3,
        atr_length=3,
        max_atr_mult=100.0,
        combine_obs=False,
        zone_count="high",
    )


# --------------------------------------------------------------------------- #
# 엔진: displacement_atr 측정 필드
# --------------------------------------------------------------------------- #


def test_displacement_populated_and_positive() -> None:
    result = detect_order_blocks(_make_df(_BULL_BARS), _bull_params())
    bulls = [ob for ob in result.order_blocks if ob.direction is OrderBlockDirection.BULLISH]
    assert bulls, "강세 OB가 하나는 나와야 한다"
    ob = bulls[0]
    assert ob.displacement_atr is not None
    # 종가 112가 스윙고 110을 넘었으므로 변위는 양수여야 한다.
    assert ob.displacement_atr > 0


def test_displacement_no_lookahead() -> None:
    """확정 이후 봉을 잘라도 그 OB의 변위는 비트 단위로 같다(룩어헤드 없음)."""
    params = _bull_params()
    full = detect_order_blocks(_make_df(_BULL_BARS), params)
    # 확정 봉(t=11)까지만 — 이후 봉(t=12, breaker)을 제거.
    truncated = detect_order_blocks(_make_df(_BULL_BARS[:12]), params)

    def _disp_by_confirm(res: object) -> dict[int, float | None]:
        return {
            ob.confirmed_time: ob.displacement_atr
            for ob in res.order_blocks  # type: ignore[attr-defined]
            if ob.direction is OrderBlockDirection.BULLISH
        }

    full_map = _disp_by_confirm(full)
    trunc_map = _disp_by_confirm(truncated)
    assert trunc_map, "확정 봉까지만 잘라도 OB가 있어야 한다"
    for confirm_time, disp in trunc_map.items():
        assert confirm_time in full_map
        assert full_map[confirm_time] == disp


def test_displacement_default_none_on_plain_construction() -> None:
    """옛 픽스처 호환 — 필드를 안 주면 None."""
    from strategy.models import OrderBlock

    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=60_000,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    assert ob.displacement_atr is None


# --------------------------------------------------------------------------- #
# census: 형성 진입 walk (_formation_outcome)
# --------------------------------------------------------------------------- #


def _long(
    highs: list[float], lows: list[float], closes: list[float]
) -> tuple[float, float, float, bool] | None:
    return _formation_outcome(
        is_long=True,
        entry_price=100.0,
        stop=90.0,
        entry_idx=0,
        highs=highs,
        lows=lows,
        closes=closes,
        tp_r=1.5,
    )


def test_formation_take_profit_first() -> None:
    out = _long([100, 116], [100, 96], [100, 110])
    assert out is not None
    mfe_r, mae_r, gross_r, broke = out
    assert gross_r == 1.5
    assert not broke
    assert mfe_r == 1.6  # (116-100)/10, 무검열은 익절 너머까지 잰다


def test_formation_stop_first() -> None:
    out = _long([100, 101], [100, 89], [100, 95])
    assert out is not None
    _mfe, mae_r, gross_r, broke = out
    assert gross_r == -1.0
    assert broke
    assert mae_r == 1.1  # (100-89)/10


def test_formation_same_bar_is_conservative_stop() -> None:
    out = _long([100, 116], [100, 89], [100, 95])
    assert out is not None
    _mfe, _mae, gross_r, broke = out
    assert gross_r == -1.0  # 같은 봉 익절+손절 → 보수적 손절
    assert broke


def test_formation_terminal_partial_r() -> None:
    out = _long([100, 105, 108], [100, 98, 99], [100, 104, 107])
    assert out is not None
    _mfe, _mae, gross_r, broke = out
    assert not broke
    assert abs(gross_r - 0.7) < 1e-9  # (107-100)/10


def test_formation_mfe_uncensored_past_tp_then_stop() -> None:
    out = _long([100, 116, 130, 125], [100, 100, 120, 89], [100, 110, 125, 95])
    assert out is not None
    mfe_r, _mae, gross_r, broke = out
    assert gross_r == 1.5  # 규칙상 익절
    assert broke  # 뒤에 무효화
    assert mfe_r == 3.0  # (130-100)/10 — 익절 너머 최대 변위


def test_formation_short_stop_first() -> None:
    out = _formation_outcome(
        is_long=False,
        entry_price=100.0,
        stop=110.0,
        entry_idx=0,
        highs=[100, 111],
        lows=[100, 99],
        closes=[100, 105],
        tp_r=1.5,
    )
    assert out is not None
    _mfe, mae_r, gross_r, broke = out
    assert gross_r == -1.0
    assert broke
    assert mae_r == 1.1  # (111-100)/10


def test_formation_invalid_risk_returns_none() -> None:
    # 진입가가 손절 이하 → risk<=0 → None(진입 불가).
    assert (
        _long([80], [70], [75]) is None
        or _formation_outcome(
            is_long=True,
            entry_price=90.0,
            stop=90.0,
            entry_idx=0,
            highs=[90],
            lows=[90],
            closes=[90],
            tp_r=1.5,
        )
        is None
    )


def test_cost_r_scales_with_inverse_risk() -> None:
    # 좁은 손절(작은 R)일수록 R당 비용이 크다.
    tight = _cost_r(entry_price=100.0, stop=99.0, is_long=True)
    wide = _cost_r(entry_price=100.0, stop=90.0, is_long=True)
    assert tight > wide > 0


# --------------------------------------------------------------------------- #
# 판정 게이트
# --------------------------------------------------------------------------- #


def _cell(**over: object) -> CellRow:
    base: dict[str, object] = dict(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="oos_warm",
        direction="long",
        n_obs=50,
        retrace_rate=0.7,
        disp_median=1.0,
        retrace_rate_lo_disp=0.8,
        retrace_rate_hi_disp=0.6,
        disp_retrace_delta=-0.2,
        median_bars_to_retrace=5.0,
        bars_to_retrace_lo_disp=4.0,
        bars_to_retrace_hi_disp=7.0,
        bars_to_retrace_delta=3.0,
        n_never=15,
        never_frac_broke=0.0,
        never_mfe_r_median=2.0,
        never_mae_r_median=0.3,
        never_frac_reach_tp=0.6,
        never_gross_r_mean=0.5,
        never_net_r_mean=0.4,
    )
    base.update(over)
    return CellRow(**base)


def test_verdict_premise_holds_both_axes() -> None:
    # 비율(−0.2)·시간(+3.0) 둘 다 지지.
    frame = rows_to_frame([_cell(symbol="A"), _cell(symbol="B")])
    verdict, _ = premise_verdict(frame, "1h", "long", "oos_warm")
    assert verdict.startswith("(a) 전제 성립") and "양축" in verdict


def test_verdict_premise_holds_time_axis_only() -> None:
    # 비율은 무관(−0.003)이지만 시간축이 강하게 양수(+10봉) → 성립(시간축).
    frame = rows_to_frame(
        [
            _cell(symbol="A", disp_retrace_delta=-0.003, bars_to_retrace_delta=10.0),
            _cell(symbol="B", disp_retrace_delta=-0.004, bars_to_retrace_delta=12.0),
        ]
    )
    verdict, _ = premise_verdict(frame, "1h", "long", "oos_warm")
    assert verdict.startswith("(a) 전제 성립") and "시간축" in verdict


def test_verdict_premise_false_when_both_axes_flat() -> None:
    # 비율 무관(−0.003) + 시간축도 미미(+1봉) → (b) 실패.
    frame = rows_to_frame(
        [
            _cell(symbol="A", disp_retrace_delta=-0.003, bars_to_retrace_delta=1.0),
            _cell(symbol="B", disp_retrace_delta=-0.005, bars_to_retrace_delta=0.5),
        ]
    )
    verdict, _ = premise_verdict(frame, "1h", "long", "oos_warm")
    assert "전제 거짓" in verdict


def test_verdict_premise_false_when_formation_loses() -> None:
    # (c) 손실이면 시간·비율이 지지해도 전제 거짓.
    frame = rows_to_frame(
        [_cell(symbol="A", never_net_r_mean=-0.3), _cell(symbol="B", never_net_r_mean=-0.1)]
    )
    verdict, _ = premise_verdict(frame, "1h", "long", "oos_warm")
    assert "전제 거짓" in verdict


def test_verdict_sample_gate() -> None:
    frame = rows_to_frame([_cell(symbol="A", n_obs=10), _cell(symbol="B", n_obs=12)])
    verdict, _ = premise_verdict(frame, "1h", "long", "oos_warm")
    assert "판정 불가(대조군)" in verdict
