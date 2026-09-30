"""WAN-448 — 손절 체결가 α · 두 극단 가중합 · 표본 · 판정."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan447_funding_carry as fc
from backtest import wan448_stop_fill_ticks as st


def test_constants_are_the_issue_spec() -> None:
    assert (st.CRASH_SAMPLE, st.NORMAL_SAMPLE, st.SEED) == (100, 60, 448)
    assert st.VERDICT_FRACTION == 0.5 and st.TARGET_CAGR == 0.25


def test_alpha_spans_stop_to_bar_low_and_clips() -> None:
    assert st.alpha_of(100.0, 100.0, 90.0) == 0.0
    assert st.alpha_of(100.0, 95.0, 90.0) == pytest.approx(0.5)
    assert st.alpha_of(100.0, 90.0, 90.0) == 1.0
    assert st.alpha_of(100.0, 101.0, 90.0) == 0.0  # 손절가보다 좋게 나가면 0
    assert st.alpha_of(100.0, 99.0, 100.0) == 0.0  # 저가가 곧 손절가


def test_blend_is_linear_between_the_two_extremes() -> None:
    assert st.blend(-1.0, -1.5, 0.0) == -1.0
    assert st.blend(-1.0, -1.5, 1.0) == -1.5
    assert st.blend(-1.0, -1.5, 0.4) == pytest.approx(-1.2)


def _trade(entry: int, net_r: float, *, stopped: bool = True) -> w.PlacedTrade:
    empty = np.array([], dtype=np.int64)
    return w.PlacedTrade(
        symbol="A",
        timeframe="1h",
        entry_time=entry,
        exit_time=entry + 60_000,
        net_r=net_r,
        stopped=stopped,
        back=False,
        size=1.0,
        entry_price=100.0,
        stop_price=90.0,
        path_times=empty,
        path_lows=np.array([]),
        path_closes=np.array([]),
    )


def test_blended_trades_only_touches_stops_and_rejects_mismatch() -> None:
    stop = [_trade(0, -1.0), _trade(1, 2.0, stopped=False)]
    low = [_trade(0, -1.4), _trade(1, 2.0, stopped=False)]
    out = st.blended_trades(stop, low, [0.5, 0.5])
    assert [t.net_r for t in out] == pytest.approx([-1.2, 2.0])
    with pytest.raises(AssertionError):
        st.blended_trades(stop, [dataclasses.replace(low[0], entry_time=9), low[1]], [0.5, 0.5])


def _row(stratum: str, i: int) -> st.StopRow:
    return st.StopRow("A", "1h", i, i + 1, stratum, 90.0, 89.0, 1.0)


def test_sample_is_stratified_and_seeded() -> None:
    rows = [_row(st.CRASH, i) for i in range(150)] + [_row(st.NORMAL, i) for i in range(30)]
    a, b = st.sample_rows(rows), st.sample_rows(rows)
    assert a == b
    assert sum(r.stratum == st.CRASH for r in a) == st.CRASH_SAMPLE
    assert sum(r.stratum == st.NORMAL for r in a) == 30  # 모자라면 있는 만큼


def test_verdict_reads_the_preregistered_cell() -> None:
    g = [
        st.GridRow("측정 α", fc.SERIES_TRAILING, 0.5, 0.02, 0.26, 0.35),
        st.GridRow("측정 α", fc.SERIES_ACTUAL, 0.5, 0.02, 0.10, 0.35),
    ]
    assert st.verdict(g) == (True, 0.26)
    with pytest.raises(ValueError):
        st.verdict(g[1:])
