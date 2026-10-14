"""스토캐스틱 %K 단일 소스(WAN-451) — 라이브(확정봉만)가 백테스트(전 이력)와 같은 값을 낸다."""

from __future__ import annotations

import numpy as np
import pytest

from backtest import wan424_stoch_ob_arm as w424
from strategy import stoch

M = 60_000
H = 60 * M
DAY = 24 * H
WEEK = 7 * DAY
MONDAY0 = 4 * DAY  # epoch(1970-01-01)은 목요일 — +4일이 첫 월요일 00:00 UTC


def _bars(n: int, seed: int) -> tuple[list[float], list[float], list[float]]:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, n))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    return list(map(float, high)), list(map(float, low)), list(map(float, close))


def test_backtest_module_uses_the_single_source() -> None:
    assert w424.stoch_k_line is stoch.stoch_k_line
    assert w424.k_before is stoch.k_before
    assert (w424.K_LENGTH, w424.K_SMOOTH) == (20, 10)


@pytest.mark.parametrize(
    ("tf_ms", "origin"),
    [(15 * M, 0), (4 * H, 0), (WEEK, MONDAY0)],  # 1w = 저장은 월요일 정렬 · 판정은 epoch 주
)
def test_live_lookup_equals_backtest_on_full_history(tf_ms: int, origin: int) -> None:
    n = 200
    high, low, close = _bars(n, seed=tf_ms % 997)
    times = [origin + i * tf_ms for i in range(n)]
    full_k = stoch.stoch_k_line(high, low, close)
    rng = np.random.default_rng(1)
    checked = 0
    for _ in range(400):
        entry = int(rng.integers(times[40], times[-2]))
        expected = stoch.k_before(times, full_k, entry, tf_ms)  # 백테스트: 진입 봉도 저장돼 있다
        closed = [i for i, t in enumerate(times) if t + tf_ms <= entry]  # 라이브: 확정봉만
        live_times = [times[i] for i in closed]
        live_k = stoch.stoch_k_line(
            [high[i] for i in closed], [low[i] for i in closed], [close[i] for i in closed]
        )
        assert stoch.k_before_live(live_times, live_k, entry, tf_ms) == expected, entry
        checked += expected is not None
    assert checked > 300


def test_weekly_monday_to_wednesday_reproduces_the_canonical_quirk() -> None:
    """정본 그대로(결정 ③ 가): 월~수 진입은 한 주 더 오래된 봉의 %K를 쓴다."""
    times = [MONDAY0 + i * WEEK for i in range(40)]
    k: list[float | None] = [float(i) for i in range(40)]
    monday = times[30]
    # 목요일 이후 진입 → 직전 주(29)의 값
    assert stoch.k_before_live(times[:30], k[:30], monday + 3 * DAY + H, WEEK) == 29.0
    # 화요일 진입 → epoch 주 경계가 지난 목요일이라 28(한 주 더 오래된 값)
    assert stoch.k_before_live(times[:30], k[:30], monday + DAY, WEEK) == 28.0


def test_unknown_when_the_just_closed_bar_is_not_stored_yet() -> None:
    """방금 닫힌 봉이 아직 저장 전이면 낡은 값을 조용히 쓰지 않고 None."""
    tf = 15 * M
    times = [i * tf for i in range(50)]
    k: list[float | None] = [float(i) for i in range(50)]
    entry = times[-1] + 2 * tf + 30_000  # 마지막 확정봉 다음 봉이 이미 닫혔다
    assert stoch.k_before_live(times, k, entry, tf) is None
    assert stoch.k_before_live(times, k, times[-1] + tf + 30_000, tf) == 49.0
    assert stoch.k_before_live([], [], 0, tf) is None
