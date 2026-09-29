"""WAN-440 — 종목을 고르지 않는 규칙 · 유니버스 좁히기 · 판정."""

from __future__ import annotations

import numpy as np
import pytest

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as m
from backtest.models import ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate


def test_rule_constants_are_the_issue_spec() -> None:
    assert m.LISTED_BEFORE == "2020-09-15"
    assert m.QUOTE == "USDT"
    assert m.VARIANTS == ("9TF", "+15m")
    assert [r[0] for r in m.RULERS][0] == m.PRIMARY_RULER
    assert m.RULERS[0][1:3] == ("bar_low", 0.35)
    assert m.RULERS[-1][3] == 0.03  # 사용자가 고른 고정 크기


def test_usdt_perp_filter() -> None:
    assert m.is_usdt_perp("BTCUSDT")
    assert m.is_usdt_perp("LENDUSDT")  # 상폐도 목록에 남는다 — 규칙이 거르지 않는다
    assert not m.is_usdt_perp("BTCUSDT_210625")  # 만기물
    assert not m.is_usdt_perp("BTCBUSD")
    assert not m.is_usdt_perp("币安人生USDT")


def test_listing_cutoff_is_strict() -> None:
    """상장 시각이 경계보다 1ms라도 늦으면 대상 밖 — 「이전」은 미포함이다."""
    cut = m.ms(m.LISTED_BEFORE)
    assert cut - 1 < cut and not cut < cut


def _entry(symbol: str, tf: str, t0: int) -> w.ArmEntry:
    cand = _Candidate(
        side=PositionSide.LONG,
        trigger_time=t0,
        entry_time=t0,
        entry_price=100.0,
        stop_price=90.0,
        exit_time=t0,
        exit_price=100.0,
        reason=ExitReason.END_OF_DATA,
    )
    times = np.arange(3, dtype=np.int64) * 60_000 + t0
    return w.ArmEntry(symbol, tf, cand, 0.0, times, np.full(3, 99.0), np.full(3, 100.0))


def test_restrict_recounts_signals_within_the_universe() -> None:
    """유니버스를 좁히면 신호 수(몰림 규칙의 입력)를 **남은 후보로 다시** 센다."""
    es = [
        _entry("A/USDT:USDT", "1h", 0),
        _entry("B/USDT:USDT", "1h", 1),
        _entry("A/USDT:USDT", "1h", 2),
    ]
    mk = c.Market("선물", [], es, w.signal_counts([0, 1, 2]), [0.0] * 3, [0.0] * 3)
    only_a = m.restrict(mk, {"A/USDT:USDT"})
    assert [e.cand.entry_time for e in only_a.entries] == [0, 2]
    assert only_a.counts == [0, 1]  # B가 빠져 둘째 A의 직전 신호는 1개
    assert mk.counts == [0, 1, 2]


def _row(u: str, v: str, basis: str, seg: str, ret: float, mdd: float, cg: float) -> c.Row:
    return c.Row(m.label(u, v), True, "bar_low", basis, 0.03, seg, 10, 0, ret, cg, mdd)


def test_verdict_needs_cagr_and_three_segments_at_31_size() -> None:
    r = m.PRIMARY_RULER
    own, base = f"{r} · 자기 맞춤", f"{r} · {m.U31} 맞춤 크기"
    rows = [
        _row(m.U31, "+15m", own, c.SEG_CHAIN, 1.0, 0.35, 0.10),
        _row(m.URULE, "+15m", own, c.SEG_CHAIN, 1.2, 0.35, 0.12),
    ]
    for seg in c.VERDICT_SEGMENTS:
        rows += [
            _row(m.U31, "+15m", base, seg, 0.5, 0.2, 0.1),
            _row(m.URULE, "+15m", base, seg, 0.5, 0.2, 0.1),
        ]
    ok, lines = m.verdict(rows, r, "+15m")
    assert ok and len(lines) == 4
    worse = [
        _row(m.URULE, "+15m", base, c.SEG_BACK, 0.4, 0.2, 0.1)
        if (x.arm, x.size_basis, x.segment) == (m.label(m.URULE, "+15m"), base, c.SEG_BACK)
        else x
        for x in rows
    ]
    assert not m.verdict(worse, r, "+15m")[0]
    with pytest.raises(KeyError):
        m.verdict(rows, r, "9TF")
