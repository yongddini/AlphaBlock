"""WAN-445 — 손절폭 비례 크기 층 · 기준 폭(앞구간만) · 판정 두 조건."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan442_momentum_rotation as m
from backtest import wan445_width_sizing as ws
from backtest.models import ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate


def _entry(t0: int, stop: float) -> w.ArmEntry:
    cand = _Candidate(
        side=PositionSide.LONG,
        trigger_time=t0,
        entry_time=t0,
        entry_price=100.0,
        stop_price=stop,
        exit_time=t0,
        exit_price=100.0,
        reason=ExitReason.END_OF_DATA,
    )
    times = np.arange(5, dtype=np.int64) * 60_000 + t0
    return w.ArmEntry("A", "1h", cand, 0.0, times, np.full(5, 99.0), np.full(5, 100.0))


def _market(entries: list[w.ArmEntry], btc_24h: list[float]) -> c.Market:
    return c.Market(
        "선물",
        [],
        entries,
        w.signal_counts([int(e.cand.entry_time) for e in entries]),
        btc_24h,
        [0.0] * len(entries),
    )


def test_constants_are_the_issue_spec() -> None:
    assert ws.PASS_MIN_IMPROVED == 15
    assert m.RANDOM_DRAWS == 20


def test_stop_width_is_fraction_of_entry_and_rejects_nonpositive() -> None:
    assert ws.stop_width(_entry(0, 90.0)) == pytest.approx(0.10)
    with pytest.raises(ValueError):
        ws.stop_width(_entry(0, 100.0))


def test_reference_width_uses_front_only() -> None:
    spot = _market([_entry(0, 90.0)], [0.0])  # 현물은 전부 앞구간
    fut = _market([_entry(10, 96.0), _entry(1_000, 50.0)], [0.0, 0.0])  # 뒤 거래는 제외
    assert ws.reference_width(spot, fut, boundary_ms=500) == pytest.approx((0.10 + 0.04) / 2)


def test_width_layer_multiplies_crash_layer_by_d_over_ref() -> None:
    mk = _market([_entry(0, 90.0), _entry(1, 95.0)], [-0.10, 0.0])  # 첫 진입은 폭락 국면
    assert mk.layer(c.ARM_B) == [c.CRASH_MULT, 1.0]
    wm = ws.with_width(mk, 0.05)
    assert wm.layer(c.ARM_B) == pytest.approx([c.CRASH_MULT * 2.0, 1.0])
    assert wm.layer(c.ARM_A) == pytest.approx([2.0, 1.0])  # A도 폭 배율은 받는다
    assert wm.entries is mk.entries and wm.counts is mk.counts  # 후보 · 신호 수는 그대로


def _pick(config: str, front: float, back: float) -> m.Pick:
    return m.Pick(
        mode=m.MODE_DROP,
        config=config,
        stage=ws.STAGE_RANDOM,
        trades=10,
        forced=0,
        front_risk=0.02,
        front_cagr=front,
        back_ratio=back,
        back_return=0.1,
        back_mdd=0.1,
        back_cagr=0.05,
        back_stop_ratio=back,
        full_risk=0.02,
        full_cagr=0.1,
        full_stop_cagr=0.1,
        fixed_cagr=0.1,
        fixed_mdd=0.3,
    )


def _pairs(front_up: int, back_up: int) -> list[tuple[m.Pick, m.Pick]]:
    out = []
    for i in range(20):
        a = _pick(f"r{i}", 0.1, 1.0)
        b = dataclasses.replace(
            a, front_cagr=0.2 if i < front_up else 0.05, back_ratio=2.0 if i < back_up else 0.5
        )
        out.append((a, b))
    return out


def test_verdict_needs_15_of_20_on_both_segments_and_hand31() -> None:
    hand = (_pick("h", 0.1, 1.0), _pick("h", 0.2, 2.0))
    assert ws.verdict(_pairs(15, 15), hand)[0]
    assert not ws.verdict(_pairs(14, 20), hand)[0]
    assert not ws.verdict(_pairs(20, 14), hand)[0]
    worse_hand = (_pick("h", 0.1, 1.0), _pick("h", 0.2, 0.9))  # 31종목 뒷구간이 나빠짐
    assert not ws.verdict(_pairs(20, 20), worse_hand)[0]
    with pytest.raises(ValueError):
        ws.verdict(_pairs(15, 15)[:19], hand)


def test_ties_do_not_count_as_improvement() -> None:
    pairs = [(_pick(f"r{i}", 0.1, 1.0), _pick(f"r{i}", 0.1, 1.0)) for i in range(20)]
    hand = (_pick("h", 0.1, 1.0), _pick("h", 0.2, 2.0))
    ok, lines = ws.verdict(pairs, hand)
    assert not ok and "좋아진 판 0개" in lines[0]
