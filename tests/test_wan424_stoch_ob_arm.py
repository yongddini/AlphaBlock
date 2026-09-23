"""WAN-424 §1: 스토캐스틱 × 오더블록 팔 — %K 정의·인과성·시간 청산을 동작으로 고정한다."""

from __future__ import annotations

import dataclasses

import pytest

from backtest import wan424_stoch_ob_arm as m
from backtest.models import ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.zone_limit_backtest import _Candidate
from data.models import timeframe_to_ms

H = 3_600_000


def _cand(
    *,
    entry_time: int,
    entry_price: float = 100.0,
    stop_price: float = 95.0,
    side: PositionSide = PositionSide.LONG,
    tap_index: int = 0,
    is_reentry: bool = False,
) -> _Candidate:
    return _Candidate(
        side=side,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=entry_time + H,
        exit_price=entry_price * 1.01,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=stop_price,
        take_profit_price=entry_price * 1.075,
        trigger_time=entry_time,
        tap_index=tap_index,
        is_reentry=is_reentry,
    )


def _steps(n_bars: int, *, per_bar: int = 60, low: float = 99.0) -> list[SubStep]:
    """1h 봉 `n_bars`개 × 1분 서브스텝. 종가는 분마다 0.01씩 오른다."""
    out = []
    for i in range(n_bars * per_bar):
        t = i * 60_000
        close = 100.0 + 0.01 * i
        bar = (t // H) * H
        out.append(SubStep(time=t, high=close + 0.5, low=low, close=close, htf_bar_time=bar))
    return out


# --- %K 정의 --------------------------------------------------------------------------


def test_stoch_k_line_matches_hand_computation() -> None:
    high = [float(i + 2) for i in range(12)]
    low = [float(i) for i in range(12)]
    close = [float(i + 1) for i in range(12)]
    k = m.stoch_k_line(high, low, close, length=3, smooth=2)
    # 워밍업: raw는 i≥2부터, 평활(2개)은 i≥3부터 정의된다.
    assert k[:3] == [None, None, None]
    # i=2: 최근 3봉 최저 0 · 최고 4 · 종가 3 → 75. i=3: 최저 1 · 최고 5 · 종가 4 → 75.
    assert k[3] == pytest.approx(75.0)


def test_stoch_k_line_flat_window_is_fifty() -> None:
    k = m.stoch_k_line([1.0] * 5, [1.0] * 5, [1.0] * 5, length=2, smooth=1)
    assert k[1:] == [50.0] * 4


def test_k_before_reads_previous_closed_bar_not_entry_bar() -> None:
    """🚨 인과성: 진입 봉(형성 중)의 값을 바꿔도 결과가 안 바뀌어야 한다 — 동작으로 건다."""
    times = [i * H for i in range(10)]
    k: list[float | None] = [float(i) for i in range(10)]
    entry = 5 * H + 17 * 60_000  # 봉 5 안
    assert m.k_before(times, k, entry, H) == 4.0
    mutated = list(k)
    mutated[5] = 999.0  # 진입 봉 자신의 %K(룩어헤드)
    assert m.k_before(times, mutated, entry, H) == 4.0
    mutated[4] = -1.0  # 직전 확정봉은 실제로 읽힌다
    assert m.k_before(times, mutated, entry, H) == -1.0


def test_k_before_gap_falls_back_one_bar_staler() -> None:
    times = [0, H, 2 * H, 4 * H]  # 3H 봉이 빠졌다
    k: list[float | None] = [10.0, 11.0, 12.0, 14.0]
    # 진입 봉 3H가 없으면 3H 이하 마지막 봉(2H)의 한 칸 앞(1H) — 스크래치 규약(보수적).
    assert m.k_before(times, k, 3 * H + 5, H) == 11.0
    assert m.k_before([], [], 5, H) is None
    assert m.k_before(times, k, 10, H) is None  # 첫 봉 안이면 직전 확정봉이 없다


# --- 시간 청산 ----------------------------------------------------------------------


def test_time_exit_holds_n_bars_including_entry_bar() -> None:
    steps = _steps(6)
    cand = _cand(entry_time=steps[30].time, stop_price=90.0)
    out = m.time_exit(cand, index=30, bars=4, substeps=steps)
    # 진입 봉(0) 포함 4봉 = 봉 0~3, 마지막 1분봉은 봉 3의 끝(분 239).
    assert out.exit_time == steps[239].time
    assert out.exit_price == steps[239].close
    assert out.reason is ExitReason.END_OF_DATA
    assert out.take_profit_price is None
    assert out.entry_time == cand.entry_time and out.entry_price == cand.entry_price
    assert out.stop_price == cand.stop_price


def test_time_exit_stop_first_including_entry_step() -> None:
    steps = _steps(6)
    steps[30] = dataclasses.replace(steps[30], low=94.0)
    cand = _cand(entry_time=steps[30].time, stop_price=95.0)
    out = m.time_exit(cand, index=30, bars=4, substeps=steps)
    assert out.reason is ExitReason.STOP_LOSS
    assert out.exit_price == 95.0 and out.exit_time == steps[30].time


def test_time_exit_truncates_at_window_end() -> None:
    steps = _steps(2)
    cand = _cand(entry_time=steps[10].time, stop_price=90.0)
    out = m.time_exit(cand, index=10, bars=8, substeps=steps)
    assert out.exit_time == steps[-1].time and out.reason is ExitReason.END_OF_DATA


def test_arm_pool_keeps_long_first_tap_wide_only() -> None:
    wide = _cand(entry_time=0, stop_price=95.0)  # 5%
    narrow = _cand(entry_time=1, stop_price=99.0)  # 1%
    retap = _cand(entry_time=2, stop_price=95.0, tap_index=1)
    reentry = _cand(entry_time=3, stop_price=95.0, is_reentry=True)
    short = _cand(entry_time=4, stop_price=105.0, side=PositionSide.SHORT)
    assert m.arm_pool([wide, narrow, retap, reentry, short], min_width=0.03) == [wide]


def test_derive_hold_arms_skips_entry_not_in_substeps() -> None:
    steps = _steps(3)
    ok = _cand(entry_time=steps[5].time, stop_price=90.0)
    missing = _cand(entry_time=steps[5].time + 1, stop_price=90.0)
    arms = m.derive_hold_arms([ok, missing], substeps=steps, holds=(1, 2))
    assert [len(v) for v in arms.values()] == [1, 1]
    assert arms[1][0].exit_time == steps[59].time
    assert arms[2][0].exit_time == steps[119].time


def test_register_3h_is_in_process() -> None:
    assert timeframe_to_ms("3h") == 3 * H


def test_equity_path_fixed_vs_compound() -> None:
    ret, mdd, dead = m._equity_path([1.0, -1.0], compound=False)
    assert ret == pytest.approx(0.0) and mdd == pytest.approx(0.01 / 1.01) and not dead
    _, _, ruined = m._equity_path([-200.0], compound=True)
    assert ruined


def test_reference_check_flags_mismatch() -> None:
    rows = [
        m.ArmRow(4, 0.04, 25.0, seg, n, r, 0.01, 0.0, 0.0, 0.0, 0.0, False)
        for seg, (r, n) in m.WAN423_REFERENCE[(4, 0.04, 25.0)].items()
    ]
    lines = m.reference_check(rows)
    assert all(line.startswith("✅") for line in lines[:3])
    assert all(line.startswith("❌") for line in lines[3:])  # K<15 행이 없다
