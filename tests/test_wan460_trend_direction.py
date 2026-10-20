"""WAN-460 토대 — 추세 신호의 인과성(검산 c) · tsmom 등식 · 갈래 배정(검산 b 부분집합)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from backtest import wan424_stoch_ob_arm as w424
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan460_trend_direction as t
from backtest.models import ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate
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


# --- 팔 숏 거울(WAN-460 · 옵트인 — 롱 경로는 비트 그대로) -------------------------------------

MIN = 60_000


def _short_cand(entry: float = 100.0, stop: float = 105.0) -> _Candidate:
    return _Candidate(
        side=PositionSide.SHORT,
        trigger_time=0,
        entry_time=0,
        entry_price=entry,
        stop_price=stop,
        exit_time=0,
        exit_price=entry,
        reason=ExitReason.END_OF_DATA,
    )


def _short_entry(highs: list[float], closes: list[float]) -> w.ArmEntry:
    times = np.arange(len(highs), dtype=np.int64) * MIN
    return w.ArmEntry(
        "X",
        "1h",
        _short_cand(),
        0.0,
        times,
        np.array(closes) - 1.0,
        np.array(closes),
        highs=np.array(highs),
    )


def test_base_cell_kwargs_default_unchanged_short_opt_in() -> None:
    """안 켜면 dict가 예전과 같다(캐시 키 · 결과 비트 동일) — 켤 때만 `short_enabled`."""
    assert "short_enabled" not in w424.base_cell_kwargs()
    assert w424.base_cell_kwargs(short_enabled=True)["short_enabled"] is True
    rest = w424.base_cell_kwargs(short_enabled=True)
    del rest["short_enabled"]
    assert rest == w424.base_cell_kwargs()


def test_arm_pool_side_filter() -> None:
    long_c = dataclasses.replace(_short_cand(), side=PositionSide.LONG, stop_price=90.0)
    short_c = _short_cand(stop=110.0)
    assert w424.arm_pool([long_c, short_c], min_width=0.04) == [long_c]
    assert w424.arm_pool([long_c, short_c], min_width=0.04, side=-1) == [short_c]
    with pytest.raises(ValueError):
        w424.arm_pool([long_c], min_width=0.04, side=0)


def test_short_arm_exit_mirrors_long() -> None:
    """숏은 고가가 손절가 이상이면 손절 · `bar_low`는 그 1분 고가 · 2배 손절은 위로 두 배."""
    e = _short_entry([101, 104, 106, 103, 99], [100, 103, 105, 101, 98])
    c1, off1 = w.arm_exit(e, 1.0)
    assert (c1.reason, off1, c1.exit_price) == (ExitReason.STOP_LOSS, 2, 105.0)
    hi, _ = w.arm_exit(e, 1.0, stop_fill="bar_low")
    assert hi.exit_price == 106.0
    c2, off2 = w.arm_exit(e, 2.0)  # 손절 110 → 안 닿음 → 마지막 1분 종가
    assert (c2.reason, off2, c2.exit_price, c2.stop_price) == (ExitReason.END_OF_DATA, 4, 98, 110.0)


def test_short_entry_needs_highs() -> None:
    e = dataclasses.replace(_short_entry([101.0], [100.0]), highs=None)
    with pytest.raises(ValueError, match="고가"):
        _ = e.adverse
    long_e = w.ArmEntry(
        "X",
        "1h",
        dataclasses.replace(_short_cand(), side=PositionSide.LONG, stop_price=95.0),
        0.0,
        np.array([0], dtype=np.int64),
        np.array([99.0]),
        np.array([99.5]),
    )
    assert long_e.adverse is long_e.lows  # 롱은 예전과 같은 배열


def _placed(short: bool, entry: float, stop: float, stopped: bool) -> w.PlacedTrade:
    z = np.array([], dtype=np.int64)
    return w.PlacedTrade(
        "X", "1h", 0, 10, -1.0 if stopped else 0.5, stopped, False, 1.0, entry, stop, z, z, z, short
    )


def test_fill_before_exit_short_and_mixed() -> None:
    # 숏끼리: 앞 숏이 105에서 손절(오르는 가격) — 다음 숏 지정가 103(손절가 아래)이 먼저 닿는다.
    prev = _placed(True, 100.0, 105.0, True)
    assert w.fill_before_exit(prev, _placed(True, 103.0, 110.0, False))
    assert not w.fill_before_exit(prev, _placed(True, 106.0, 110.0, False))
    # 방향이 다르면 순서를 못 가른다 → 보수적으로 체결이 먼저(칸이 차 있다).
    assert w.fill_before_exit(prev, _placed(False, 100.0, 95.0, False))
    # 롱은 예전과 같다.
    lp = _placed(False, 100.0, 95.0, True)
    assert w.fill_before_exit(lp, _placed(False, 96.0, 90.0, False))
    assert not w.fill_before_exit(lp, _placed(False, 94.0, 90.0, False))


def test_mtm_path_short_marks_against_highs_and_profits_on_fall() -> None:
    """숏 평가손은 고가 쪽 · 손절선(105)까지 오르면 −1R · 명목은 양수로 센다(상한 위반 0)."""
    times = np.arange(0, 4 * MIN, MIN, dtype=np.int64)
    t = w.PlacedTrade(
        "X",
        "1h",
        0,
        4 * MIN,
        0.5,
        False,
        False,
        1.0,
        100.0,
        105.0,
        times,
        np.array([101.0, 105.0, 102.0, 99.0]),  # 숏은 고가
        np.array([100.0, 104.0, 101.0, 98.0]),
        True,
    )
    p = w.mtm_path([t], risk=0.01)
    assert p.breaches == 0
    assert float(p.mtm_low.min()) == pytest.approx(1.0 - 0.01)
    assert float(p.mtm_close[-1]) == pytest.approx(1.005)
