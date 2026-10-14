"""WAN-452 — 순수 함수의 동작을 고정한다(진입가 차 규칙 · 손절폭 막기 · 격리 청산가 · 구간 자)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
import pandas as pd
import pytest

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan452_adopted_optimism as x
from data.agg_trade_archive import Tick


def _ticks(prices: list[float], qty: float = 1.0) -> list[Tick]:
    return [Tick(time_ms=i, price=p, qty=qty) for i, p in enumerate(prices)]


def test_fixed_point_first_tick_at_or_below_limit() -> None:
    i, lim = x.fixed_point(_ticks([105.0, 101.0, 99.0]), lambda p: 100.0)
    assert (i, lim) == (2, 100.0)


def test_fixed_point_none_when_never_crossed() -> None:
    assert x.fixed_point(_ticks([105.0, 101.0]), lambda p: 100.0) == (None, None)


def test_entry_gap_zero_when_tick_fill_equals_recorded_and_volume_enough() -> None:
    out = x.entry_gap(
        _ticks([101.0, 99.0, 98.0], qty=5.0),
        lambda p: 100.0,
        entry_price=100.0,
        quantity=8.0,
        minute_close=103.0,
    )
    assert out["gap"] == 0.0
    assert out["fill_frac"] == 1.0


def test_entry_gap_partial_fill_chases_remainder_at_close() -> None:
    # 체결 뒤 지정가 이하 인쇄량 2 · 우리 수량 4 → 절반을 종가 104에 추격.
    out = x.entry_gap(
        _ticks([101.0, 99.0, 102.0], qty=2.0),
        lambda p: 100.0,
        entry_price=100.0,
        quantity=4.0,
        minute_close=104.0,
    )
    assert out["fill_frac"] == pytest.approx(0.5)
    assert out["gap"] == pytest.approx(0.5 * 4.0 / 100.0)


def test_entry_gap_tick_fill_above_recorded_counts_as_cost() -> None:
    out = x.entry_gap(
        _ticks([100.5]), lambda p: 101.0, entry_price=100.0, quantity=0.5, minute_close=100.0
    )
    assert out["gap"] == pytest.approx(0.01)


def test_entry_gap_no_tick_fill_chases_with_taker_fee() -> None:
    out = x.entry_gap(
        _ticks([102.0]), lambda p: 100.0, entry_price=100.0, quantity=1.0, minute_close=103.0
    )
    assert out["touched"] == 0.0
    assert out["gap"] == pytest.approx(0.03 + x.CHASE_FEE)


def _trade(entry: float = 100.0, stop: float = 80.0, net_r: float = 1.0) -> w.PlacedTrade:
    empty = np.array([], dtype=np.int64)
    return w.PlacedTrade(
        "AAA/USDT:USDT",
        "1h",
        0,
        60_000,
        net_r,
        False,
        False,
        1.0,
        entry,
        stop,
        empty,
        np.array([]),
        np.array([]),
    )


@dataclass(frozen=True)
class _Cand:
    entry_time: int
    entry_price: float
    stop_price: float


@dataclass(frozen=True)
class _Entry:
    cand: _Cand
    timeframe: str = "1h"
    symbol: str = "AAA/USDT:USDT"

    @property
    def key(self) -> tuple[str, str, int, float]:
        return (self.symbol, self.timeframe, self.cand.entry_time, self.cand.entry_price)


def _market(entries: list[_Entry], b24: list[float] | None = None) -> c.Market:
    n = len(entries)
    return c.Market(
        "t",
        [],
        cast(list[w.ArmEntry], entries),
        w.signal_counts([e.cand.entry_time for e in entries]),
        b24 or [0.0] * n,
        [1.0] * n,
    )


def test_apply_entry_gap_is_exactly_linear_in_r() -> None:
    t = _trade()
    mk = _market([_Entry(_Cand(0, 100.0, 90.0))], b24=[-0.10])
    (out,) = x.apply_entry_gap([t], mk, {x.CRASH: 0.002, x.NORMAL: 0.0})
    # 1R = 20 · 진입가 0.2 비싸짐 → −0.01R.
    assert out.net_r == pytest.approx(1.0 - 0.002 * 100.0 / 20.0)
    (same,) = x.apply_entry_gap([t], mk, {x.CRASH: 0.0, x.NORMAL: 0.0})
    assert same.net_r == t.net_r


def test_skip_and_clamp_wide_use_post_multiple_width() -> None:
    narrow = _Entry(_Cand(0, 100.0, 90.0))  # 배수 2 뒤 20%
    wide = _Entry(_Cand(60_000, 100.0, 40.0))  # 배수 2 뒤 120% — 손절가 음수
    mk = _market([narrow, wide])
    assert x.entry_width(cast(w.ArmEntry, wide)) == pytest.approx(1.2)
    assert [e.cand.entry_time for e in x.skip_wide(mk, 1.0).entries] == [0]
    clamped = x.clamp_wide(mk, 0.5)
    assert x.entry_width(clamped.entries[0]) == pytest.approx(0.2)
    assert x.entry_width(clamped.entries[1]) == pytest.approx(0.5)
    assert clamped.counts == mk.counts


def test_isolated_liq_price_without_maintenance_is_one_over_leverage() -> None:
    assert x.isolated_liq_price(100.0, 1.0, 5, 0.0, 0.0) == pytest.approx(80.0)
    # 유지증거금이 있으면 청산가가 더 높다(더 빨리 청산).
    assert x.isolated_liq_price(100.0, 1.0, 5, 0.01, 0.0) > 80.0


def test_bracket_for_picks_notional_band() -> None:
    table = [
        {
            "bracketNotionalFloor": 0,
            "bracketNotionalCap": 100,
            "bracketMaintenanceMarginRate": 0.01,
            "cumFastMaintenanceAmount": 0,
            "maxOpenPosLeverage": 50,
        },
        {
            "bracketNotionalFloor": 100,
            "bracketNotionalCap": 1000,
            "bracketMaintenanceMarginRate": 0.02,
            "cumFastMaintenanceAmount": 1,
            "maxOpenPosLeverage": 20,
        },
    ]
    assert x.bracket_for(table, 50) == (0.01, 0.0, 50)
    assert x.bracket_for(table, 500) == (0.02, 1.0, 20)
    assert x.bracket_for(table, 5000) == (0.02, 1.0, 20)


def test_segment_stats_resets_peak_at_segment_start() -> None:
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    eq = pd.Series([2.0, 1.0, 1.5, 1.2], index=idx)
    low = eq.copy()
    total, mdd, _ = x.segment_stats(eq, low, np.array([False, False, True, True]))
    # 뒷구간 기준점 = 1.0 → 1.5 → 1.2 : 수익 +20% · MDD 20%(앞구간의 2.0 고점은 안 센다).
    assert total == pytest.approx(0.2)
    assert mdd == pytest.approx(0.2)


def _panel(name: str, cagr: float, back_return: float, back_mdd: float) -> x.Panel:
    return x.Panel(
        "§4",
        name,
        0.03,
        1.0,
        1.0,
        cagr,
        0.35,
        "2021-05-19",
        0.0,
        0.1,
        back_return,
        back_mdd,
        0.0,
        0.3,
        0,
        {},
    )


@pytest.mark.parametrize(
    ("cagr", "back", "expected"),
    [
        (0.40, 0.5, "15m 유지 근거 있음"),
        (0.50, 2.0, "15m 몫 확인 안 됨"),
        (0.40, 2.0, "갈린다(한쪽만 나빠진다)"),
    ],
)
def test_verdict_15m(cagr: float, back: float, expected: str) -> None:
    panels = [
        _panel("정본 · 합성", 0.46, 1.0, 1.0),
        _panel("15m 제외 · 합성 · 크기 재맞춤 · k·λ 재맞춤", cagr, back, 1.0),
    ]
    assert x.verdict_15m(panels)[0] == expected


def test_resolved_matches_wan449_rule() -> None:
    p = _panel("p", 0.4, 0.0, 0.1)
    assert x.resolved(p)
    assert not x.resolved(dataclasses_replace(p, median=0.19))
    assert not math.isnan(p.back_ratio)


def dataclasses_replace(p: x.Panel, **kw: object) -> x.Panel:
    import dataclasses

    return dataclasses.replace(p, **kw)  # type: ignore[arg-type]
