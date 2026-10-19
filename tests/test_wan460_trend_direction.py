"""WAN-460 토대 — 추세 신호의 인과성(검산 c) · tsmom 등식 · 갈래 배정(검산 b 부분집합)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import wan460_trend_direction as t
from strategy import tsmom

DAY = t.DAY_MS
T0 = 1_514_764_800_000  # 2018-01-01 UTC


def _bars(n: int, seed: int = 460) -> tuple[list[int], list[float]]:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 0.03, n)
    # 추세가 위·아래로 다 나오게 구간마다 드리프트를 바꾼다.
    r += np.where((np.arange(n) // 200) % 2 == 0, 0.004, -0.004)
    close = 10_000.0 * np.cumprod(1.0 + r)
    return [T0 + i * DAY for i in range(n)], [float(c) for c in close]


def test_signal_matches_tsmom_target_every_day() -> None:
    """페이퍼가 쓰는 `target_from_daily_closes`와 날마다 같은 값(같은 경로)."""
    times, close = _bars(400)
    sig = t.daily_trend_signal(times, close)
    for i in (250, 251, 300, 399):
        ref = tsmom.target_from_daily_closes(
            "BTC/USDT:USDT", pd.Series(times[: i + 1]), pd.Series(close[: i + 1])
        )
        assert ref.as_of_ms == times[i]
        assert sig.iloc[i] == ref.signal
    assert np.isnan(sig.iloc[249])  # 250일 룩백 이력 부족


def test_future_cut_invariance() -> None:
    """검산 (c) — 미래 일봉을 잘라도 그날까지의 신호가 비트로 같다."""
    times, close = _bars(700)
    full = t.daily_trend_signal(times, close)
    for cut in (260, 451, 699):
        part = t.daily_trend_signal(times[:cut], close[:cut])
        pd.testing.assert_series_equal(part, full.iloc[:cut])


def test_lookup_uses_only_closed_bars() -> None:
    """진입 시각에 닫힌 마지막 일봉만 — 그날 형성 중인 봉은 안 쓴다."""
    times, close = _bars(400)
    lk = t.TrendLookup.from_series(t.daily_trend_signal(times, close))
    d = times[300]
    # D 봉은 D+1일 00:00에 닫힌다 → 그 순간부터 D의 신호.
    assert lk.signal_at(d + DAY) == lk.signals[300]
    assert lk.signal_at(d + DAY - 1) == lk.signals[299]
    assert lk.signal_at(d + 5 * 3_600_000) == lk.signals[299]
    assert np.isnan(lk.signal_at(times[0]))  # 닫힌 봉 없음


def test_lookup_invariant_to_future_bars() -> None:
    """진입 시각 이후의 일봉을 지워도 그 진입의 부호가 같다(룩어헤드 없음)."""
    times, close = _bars(700)
    full = t.TrendLookup.from_series(t.daily_trend_signal(times, close))
    for k in (300, 520, 690):
        entry = times[k] + 7 * 3_600_000
        cut = t.TrendLookup.from_series(t.daily_trend_signal(times[: k + 1], close[: k + 1]))
        assert cut.sign_at(entry) == full.sign_at(entry)


def test_gaps_rejected() -> None:
    times, close = _bars(300)
    del times[100], close[100]
    with pytest.raises(ValueError, match="연속"):
        t.daily_trend_signal(times, close)


@pytest.mark.parametrize(
    ("branch", "sign", "side"),
    [
        ("A", 1, "long"),
        ("A", 0, "long"),
        ("A", -1, "long"),
        ("B", 1, "long"),
        ("B", 0, "long"),
        ("B", -1, None),
        ("C", 1, "long"),
        ("C", 0, "long"),
        ("C", -1, "short"),
    ],
)
def test_branch_table(branch: t.Branch, sign: int, side: t.Side | None) -> None:
    """이슈 표 그대로 — 신호 0(또는 NaN)은 갈래 A처럼."""
    assert t.branch_side(branch, sign) == side


def test_branch_b_long_is_subset_of_a_long() -> None:
    """검산 (b)의 형태 — B 롱 = A 롱 중 추세 아래가 아닌 것 · C 롱 = B 롱."""
    times, close = _bars(900)
    lk = t.TrendLookup.from_series(t.daily_trend_signal(times, close))
    entries = [times[0] + i * 6 * 3_600_000 for i in range(3_000)]
    a = t.select_for_branch(entries, branch="A", side="long", entry_ms=int, lookup=lk)
    b = t.select_for_branch(entries, branch="B", side="long", entry_ms=int, lookup=lk)
    c_long = t.select_for_branch(entries, branch="C", side="long", entry_ms=int, lookup=lk)
    c_short = t.select_for_branch(entries, branch="C", side="short", entry_ms=int, lookup=lk)
    assert a == entries
    assert b == [e for e in a if lk.sign_at(e) >= 0]
    assert c_long == b
    assert c_short == [e for e in a if lk.sign_at(e) < 0]
    # 하드 제로 경보(WAN-367): 합성 자료가 두 추세를 다 내야 이 검산이 뜻이 있다.
    assert 0 < len(c_short) < len(a)
    assert t.select_for_branch(entries, branch="B", side="short", entry_ms=int, lookup=lk) == []
