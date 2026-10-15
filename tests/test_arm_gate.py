"""팔 켜기 전 관문(WAN-451) — 거래 대조 로직이 어긋남을 실제로 잡는가."""

from __future__ import annotations

import numpy as np

from backtest import wan436_stoch_crowd_rules as w
from data.universe import rebalance_at
from live import arm_gate as g

_E = np.array([], dtype=np.int64)


def _canon(t: int, *, stop: float = 90.0, size: float = 1.0, exit_: int = 600_000) -> w.PlacedTrade:
    return w.PlacedTrade(
        "A", "1h", t, exit_, 0.0, True, False, size, 100.0, stop, _E, np.array([]), np.array([])
    )


def _live(t: int, **kw: object) -> g.LiveTrade:
    base = {
        "symbol": "A",
        "timeframe": "1h",
        "entry_time": t,
        "entry_price": 100.0,
        "stop_price": 90.0,
        "scale": 1.0,
        "exit_time": 600_000,
        "exit_price": 90.0,
        "reason": "stop_loss",
    }
    base.update(kw)
    return g.LiveTrade(**base)  # type: ignore[arg-type]


def test_identical_trades_have_no_problems() -> None:
    checks = g.compare_trades({("A", "1h", 0): _canon(0)}, [_live(0)], 0, 10**9)
    assert [c.problems for c in checks] == [()]


def test_each_field_mismatch_is_reported() -> None:
    canon = {("A", "1h", 0): _canon(0)}
    cases = {
        "손절가": _live(0, stop_price=91.0),
        "크기 배율": _live(0, scale=0.5),
        "청산 시각": _live(0, exit_time=660_000),
        "청산 사유": _live(0, reason="time_exit"),
        "진입가": _live(0, entry_price=101.0),
    }
    for word, lt in cases.items():
        (check,) = g.compare_trades(canon, [lt], 0, 10**9)
        assert any(word in p for p in check.problems), word


def test_one_sided_trades_and_window() -> None:
    canon = {("A", "1h", 0): _canon(0), ("A", "1h", 10**10): _canon(10**10)}
    checks = g.compare_trades(canon, [_live(5)], 0, 10**9)
    assert sorted(p for c in checks for p in c.problems) == ["라이브만 진입", "정본만 진입"]


def test_fixed_universe_is_this_weeks_snapshot() -> None:
    snap = g.FixedUniverse(["A", "B"]).at(1_790_000_000_000)
    assert snap.rebalance_ms == rebalance_at(1_790_000_000_000) and snap.members == {"A", "B"}
