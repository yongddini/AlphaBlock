"""WAN-424 §2: 매칭 대조군 — 무작위 시각 청산의 등식 · 층 · 자(ruler)를 동작으로 고정한다."""

from __future__ import annotations

import random

import pytest

from backtest import wan248_zone_position_null as ruler
from backtest import wan424_matched_null as mn
from backtest import wan424_stoch_ob_arm as arm
from backtest.models import ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.zone_limit_backtest import _Candidate

H = 3_600_000


def _walk(n_bars: int, *, seed: int = 7) -> list[SubStep]:
    rng = random.Random(seed)
    price = 100.0
    out = []
    for i in range(n_bars * 60):
        t = i * 60_000
        price *= 1.0 + rng.uniform(-0.004, 0.004)
        low = price * (1.0 - rng.uniform(0.0, 0.003))
        out.append(
            SubStep(time=t, high=price * 1.002, low=low, close=price, htf_bar_time=(t // H) * H)
        )
    return out


def test_random_time_exit_equals_arm_time_exit() -> None:
    """🚨 널 B의 넘파이 청산은 §1 `time_exit`와 **같은 규칙**이어야 한다 — 칸마다 맞춰 본다."""
    steps = _walk(30)
    arrays = mn.MinuteArrays.from_substeps(steps)
    rng = random.Random(3)
    for _ in range(300):
        index = rng.randrange(len(steps))
        width = rng.choice([0.003, 0.01, 0.04])
        bars = rng.choice([1, 4, 8, 40])
        t_in, entry, stop, t_out, exit_price, is_stop = mn.random_time_exit(
            arrays, index=index, width=width, bars=bars
        )
        cand = _Candidate(
            side=PositionSide.LONG,
            entry_time=steps[index].time,
            entry_price=entry,
            exit_time=0,
            exit_price=0.0,
            reason=ExitReason.TAKE_PROFIT,
            stop_price=stop,
        )
        ref = arm.time_exit(cand, index=index, bars=bars, substeps=steps)
        assert (t_in, t_out) == (ref.entry_time, ref.exit_time)
        assert exit_price == ref.exit_price
        assert is_stop == (ref.reason is ExitReason.STOP_LOSS)


def test_minute_arrays_bar_bookkeeping() -> None:
    steps = _walk(3)
    arrays = mn.MinuteArrays.from_substeps(steps)
    assert list(arrays.bar_end) == [59, 119, 179]
    assert arrays.bar_id[0] == 0 and arrays.bar_id[60] == 1 and arrays.bar_id[-1] == 2


def test_full_segment_is_stratified_at_warm_boundary() -> None:
    assert mn.strata("full", 500) == (("pre", None, 500), ("post", 500, None))
    assert mn.strata("is", 500) == (("all", None, None),)


def test_p_value_is_wan70_fraction_at_or_above() -> None:
    assert mn.p_value(0.5, [0.1, 0.5, 0.9, 0.2]) == pytest.approx(0.5)
    assert mn.p_value(1.0, [0.1, 0.2]) == 0.0


def test_ruler_is_imported_not_rewritten() -> None:
    assert mn.MIN_TRADES_FOR_VERDICT is ruler.MIN_TRADES_FOR_VERDICT
    assert mn.ALPHA is ruler.ALPHA
    assert mn.BOOTSTRAP_ITERATIONS is ruler.BOOTSTRAP_ITERATIONS


def test_significant_needs_sample_and_beating_the_mean() -> None:
    nulls = [0.0] * 100
    assert mn.significant(0.2, nulls, n_real=20)
    assert not mn.significant(0.2, nulls, n_real=19)  # 표본 게이트
    assert not mn.significant(-0.1, [-0.5] * 94 + [0.0] * 6, n_real=50)  # p>α


def _cell() -> mn.NullCell:
    cands = tuple(
        _Candidate(
            side=PositionSide.LONG,
            entry_time=t,
            entry_price=100.0,
            exit_time=t + 1,
            exit_price=101.0,
            reason=ExitReason.END_OF_DATA,
            stop_price=95.0,
            trigger_time=t,
        )
        for t in range(0, 1000, 100)
    )
    k = (10.0, 50.0, 20.0, 60.0, 5.0, 70.0, 30.0, 12.0, 80.0, 1.0)
    return mn.NullCell(
        symbol="X",
        timeframe="1h",
        boundary_ms=500,
        pool={4: {"full": cands}},
        k_value={"full": k},
        null_b={},
    )


def test_null_a_matches_count_per_stratum() -> None:
    cell = _cell()
    real = mn.real_indices(cell, "full", 25.0)
    assert real == [0, 2, 4, 7, 9]  # 앞 층 3개 · 뒤 층 2개
    for draw in range(20):
        picks = mn._null_a_indices(cell, "full", real, draw, 4, 25.0)
        pre = [i for i in picks if cell.pool[4]["full"][i].trigger_time < 500]
        post = [i for i in picks if cell.pool[4]["full"][i].trigger_time >= 500]
        assert (len(pre), len(post)) == (3, 2)
        assert len(set(picks)) == len(picks)


def test_null_a_is_deterministic() -> None:
    cell = _cell()
    real = mn.real_indices(cell, "full", 25.0)
    a = mn._null_a_indices(cell, "full", real, 3, 4, 25.0)
    b = mn._null_a_indices(cell, "full", real, 3, 4, 25.0)
    assert a == b


def _fact(entry: int, exit_: int, r: float) -> mn.Fact:
    return mn.Fact("X", "1h", entry, exit_, r < 0, r)


def test_excluding_days_drops_by_kst_entry_day() -> None:
    # 2024-01-01 00:30 KST = 2023-12-31 15:30 UTC
    ms = 1_704_036_600_000
    facts = [_fact(ms, ms + 1, 1.0), _fact(ms + 86_400_000, ms + 86_400_001, -1.0)]
    kept = mn.excluding_days(facts, {"2024-01-01"})
    assert [f.net_r for f in kept] == [-1.0]


def test_pooled_bucket_gap_weights_real_counts() -> None:
    real = [_fact(0, 1, 1.0), _fact(2, 3, 1.0)]
    null = [_fact(0, 1, 0.0), _fact(2, 3, 0.0)]
    assert mn.pooled_bucket_gap(real, null) == pytest.approx(1.0)
