"""WAN-442 — 모멘텀 점수(인과) · 목록 · 시장 적용(새 진입만 막기 / 강제 청산) · 판정."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan442_momentum_rotation as m
from backtest.models import ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate

DAY = m.DAY_MS


def _daily(closes: list[float], *, start: int = 0, volumes: list[float] | None = None) -> m.Daily:
    ot = np.arange(len(closes), dtype=np.int64) * DAY + start
    cl = np.asarray(closes, dtype=float)
    vol = np.asarray(volumes if volumes is not None else [1.0] * len(closes), dtype=float)
    return m.Daily(ot, cl, vol * cl)


def test_constants_are_the_issue_spec() -> None:
    assert m.TOP_N == 31 and m.REBALANCE_DAYS == 7 and m.STAGE1_LOOKBACK == 90
    assert m.DEFINITIONS == (m.DEF_SIMPLE, m.DEF_SKIP, m.DEF_VOLADJ, m.DEF_TREND, m.DEF_VOLUME)
    assert m.STAGE2_LOOKBACKS == (30, 60, 90, 120, 180)
    assert m.RANK_AVG_LOOKBACKS == (30, 90, 180)
    assert m.RANDOM_DRAWS == 20 and m.RANDOM_SEED == 442 and m.PASS_MAX_BEATEN == 1
    assert m.MODES == (m.MODE_DROP, m.MODE_FORCE)


def test_rebalance_grid_is_monday_utc() -> None:
    times = m.rebalance_times(10 * DAY, 40 * DAY)
    # 1970-01-05가 첫 월요일(1970-01-01은 목요일)
    assert all((t - 4 * DAY) % m.WEEK_MS == 0 for t in times)
    assert times[0] <= 10 * DAY < times[0] + m.WEEK_MS
    assert np.all(np.diff(times) == 7 * DAY)


def test_last_closed_excludes_the_forming_bar() -> None:
    d = _daily([1.0] * 10)
    t = 5 * DAY  # 이 시각에 open_time 4일 봉이 막 닫혔고 5일 봉은 막 열렸다
    assert m.last_closed(d, t) == 4
    assert m.last_closed(d, t - 1) == 3


def test_score_is_causal_future_bars_do_not_matter() -> None:
    """t 이후 일봉을 바꿔도 점수 · 목록이 비트 동일(룩어헤드 없음)."""
    rng = np.random.default_rng(0)
    base = list(np.exp(np.cumsum(rng.normal(0, 0.02, 400))))
    t = 300 * DAY
    a = {"A": _daily(base), "B": _daily(base[::-1])}
    moved = [x * (3.0 if i >= 300 else 1.0) for i, x in enumerate(base)]
    b = {"A": _daily(moved), "B": _daily(base[::-1])}
    for defn in m.DEFINITIONS:
        j = m.eligible(a["A"], t)
        assert j is not None
        sa = m.score(defn, a["A"], j, 90)
        sb = m.score(defn, b["A"], j, 90)
        assert sa == sb
        assert m.select(a, t, defn, (90,), n=1) == m.select(b, t, defn, (90,), n=1)


def test_simple_and_skip_recent_returns() -> None:
    closes = [100.0] * 91 + [110.0] * 7 + [121.0]
    d = _daily(closes)
    j = len(closes) - 1
    assert m.score(m.DEF_SIMPLE, d, j, 90) == pytest.approx(121.0 / 100.0 - 1.0)
    assert m.score(m.DEF_SKIP, d, j, 90) == pytest.approx(110.0 / 100.0 - 1.0)


def test_trend_quality_prefers_clean_trend() -> None:
    n = 120
    clean = list(np.exp(np.linspace(0, 0.5, n)))
    noisy = [x * (1.1 if i % 2 else 0.9) for i, x in enumerate(clean)]
    j = n - 1
    s_clean = m.score(m.DEF_TREND, _daily(clean), j, 90)
    s_noisy = m.score(m.DEF_TREND, _daily(noisy), j, 90)
    assert s_clean is not None and s_noisy is not None
    assert s_clean > s_noisy > 0


def test_volume_weight_scales_return() -> None:
    closes = list(np.linspace(100, 120, 200))
    even = [1.0 / x for x in closes]  # 거래대금(= 거래량 × 종가)이 매일 1
    hot = [v * (3.0 if i >= 170 else 1.0) for i, v in enumerate(even)]
    flat_s = m.score(m.DEF_VOLUME, _daily(closes, volumes=even), 199, 90)
    hot_s = m.score(m.DEF_VOLUME, _daily(closes, volumes=hot), 199, 90)
    simple = m.score(m.DEF_SIMPLE, _daily(closes), 199, 90)
    assert flat_s is not None and hot_s is not None and simple is not None
    assert flat_s == pytest.approx(simple)  # 거래대금이 평평하면 단순 수익률 그대로
    assert hot_s > flat_s


def test_eligibility_drops_stale_and_young_symbols() -> None:
    young = _daily([1.0] * 50)
    assert m.eligible(young, 50 * DAY) is None
    old = _daily([1.0] * 200)
    assert m.eligible(old, 200 * DAY) == 199
    # 상폐 — 마지막 봉 뒤 FRESH_DAYS를 넘기면 자격이 없다(옛 점수로 자리를 차지하지 않는다)
    assert m.eligible(old, (200 + m.FRESH_DAYS + 1) * DAY) is None


def test_select_top_bottom_and_rank_average() -> None:
    n = 250
    up = _daily(list(np.linspace(100, 200, n)))
    down = _daily(list(np.linspace(200, 100, n)))
    flat = _daily([100.0] * n)
    daily = {"UP": up, "DOWN": down, "FLAT": flat}
    t = n * DAY
    assert m.select(daily, t, m.DEF_SIMPLE, (90,), n=1) == frozenset({"UP"})
    assert m.select(daily, t, m.DEF_SIMPLE, (90,), n=1, top=False) == frozenset({"DOWN"})
    assert m.select(daily, t, m.DEF_SIMPLE, (30, 90, 180), n=2) == frozenset({"UP", "FLAT"})
    # 자격 종목이 n보다 적으면 전부
    assert m.select(daily, t, m.DEF_SIMPLE, (90,), n=31) == frozenset(daily)


def test_random_members_are_seeded_and_capped() -> None:
    daily = {f"S{i}": _daily([100.0 + i] * 200) for i in range(40)}
    times = [150 * DAY, 157 * DAY]
    a = m.random_members(daily, times, 7)
    b = m.random_members(daily, times, 7)
    assert a == b and all(len(x) == m.TOP_N for x in a)
    assert m.random_members(daily, times, 8) != a


def _entry(symbol: str, t0: int, n: int = 10, low: float = 99.0) -> w.ArmEntry:
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
    times = np.arange(n, dtype=np.int64) * 60_000 + t0
    closes = np.linspace(100.0, 110.0, n)
    return w.ArmEntry(symbol, "1h", cand, 0.0, times, np.full(n, low), closes)


def _market(entries: list[w.ArmEntry]) -> c.Market:
    return c.Market(
        "선물",
        [],
        entries,
        w.signal_counts([int(e.cand.entry_time) for e in entries]),
        [0.0] * len(entries),
        [0.0] * len(entries),
    )


def test_apply_rotation_filters_at_entry_and_recounts() -> None:
    minute = 60_000
    rot = m.Rotation((0, 100 * minute), (frozenset({"A"}), frozenset({"B"})))
    es = [_entry("A", 10 * minute), _entry("B", 20 * minute), _entry("B", 110 * minute)]
    mk, cut = m.apply_rotation(_market(es), rot, force_close=False)
    assert [(e.symbol, int(e.cand.entry_time)) for e in mk.entries] == [
        ("A", 10 * minute),
        ("B", 110 * minute),
    ]
    assert mk.counts == [0, 1]  # 빠진 B 20분 신호는 다시 셀 때 안 들어간다
    assert _market(es).counts == [0, 1, 2]
    assert cut == 0


def test_force_close_truncates_at_removal_and_exits_at_last_close() -> None:
    minute = 60_000
    rot = m.Rotation((0, 5 * minute), (frozenset({"A"}), frozenset({"B"})))
    e = _entry("A", 0, n=10)
    kept, cut = m.apply_rotation(_market([e]), rot, force_close=False)
    forced, cut_f = m.apply_rotation(_market([e]), rot, force_close=True)
    assert cut == 0 and len(kept.entries[0].times) == 10
    assert cut_f == 1
    fe = forced.entries[0]
    assert list(fe.times) == [i * minute for i in range(5)]  # 빠지는 시각 직전 분까지
    cand, off = w.arm_exit(fe, 1.0)
    assert off == 4 and cand.reason == ExitReason.END_OF_DATA
    assert cand.exit_price == pytest.approx(float(fe.closes[-1]))


def test_force_close_leaves_a_member_or_stopped_trade_alone() -> None:
    minute = 60_000
    stay = m.Rotation((0, 5 * minute), (frozenset({"A"}), frozenset({"A"})))
    mk, cut = m.apply_rotation(_market([_entry("A", 0)]), stay, force_close=True)
    assert cut == 0 and len(mk.entries[0].times) == 10
    # 손절이 빠지는 시각보다 먼저면 결과가 같다
    gone = m.Rotation((0, 5 * minute), (frozenset({"A"}), frozenset()))
    stopped = _entry("A", 0, low=80.0)
    a, _ = m.apply_rotation(_market([stopped]), gone, force_close=False)
    b, _ = m.apply_rotation(_market([stopped]), gone, force_close=True)
    assert w.arm_exit(a.entries[0], 1.0)[0] == w.arm_exit(b.entries[0], 1.0)[0]


def _pick(config: str, front: float, back: float, stage: str = "1단계") -> m.Pick:
    return m.Pick(
        mode=m.MODE_DROP,
        config=config,
        stage=stage,
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


def test_best_picks_front_cagr_and_ties_go_first() -> None:
    picks = [_pick("a", 0.10, 9.0), _pick("b", 0.20, -1.0), _pick("c", 0.20, 0.0)]
    assert m.best(picks).config == "b"  # 뒷구간 값은 고르는 데 안 쓴다 · 동점은 앞쪽


def test_verdict_counts_randoms_at_or_above() -> None:
    chosen = _pick("x", 0.1, 1.0)
    randoms = [_pick(f"r{i}", 0.1, 0.5, "무작위") for i in range(19)] + [
        _pick("r19", 0.1, 1.0, "무작위")
    ]
    assert m.verdict(chosen, randoms) == (True, 1)
    two = [*randoms[:-2], _pick("r18", 0.1, 2.0, "무작위"), randoms[-1]]
    assert m.verdict(chosen, two) == (False, 2)
    with pytest.raises(ValueError):
        m.verdict(chosen, randoms[:5])


def test_picks_roundtrip_through_frame() -> None:
    picks = [_pick("a", 0.1, 0.2), dataclasses.replace(_pick("b", 0.3, 0.4), forced=3)]
    assert m.picks_from_frame(m.picks_frame(picks)) == picks
