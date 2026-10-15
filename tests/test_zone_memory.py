"""오래된 존 기억(WAN-451) — 탐지 창 밖으로 밀려난 살아 있는 존을 탐지기 규칙으로 이어 든다."""

from __future__ import annotations

import pandas as pd
import pytest

from live.engine_state import SeriesStateSnapshot
from live.limit_engine import ZoneLimitLiveEngine
from strategy.models import OrderBlock, OrderBlockDirection
from tests.test_limit_engine import _H, _SYMBOL, _TF, _install_stub_detector, _params

ZONE = OrderBlock(
    direction=OrderBlockDirection.BULLISH,
    top=99.5,
    bottom=90.0,
    start_time=0,
    confirmed_time=_H,
    ob_volume=1.0,
    ob_low_volume=0.5,
    ob_high_volume=0.5,
)
ZID = ("bull", 0, _H)


def _bars(start: int, n: int, *, lows: dict[int, float] | None = None) -> pd.DataFrame:
    """종가 101 근처의 1h 봉 `n`개(시작 봉 번호 `start`) — `lows`로 특정 봉의 저가를 바꾼다."""
    lows = lows or {}
    times = [(start + i) * _H for i in range(n)]
    low = [lows.get(start + i, 100.5) for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [101.0] * n,
            "high": [102.0] * n,
            "low": low,
            "close": [101.0] * n,
            "volume": [1.0] * n,
            "closed": [True] * n,
        }
    )


def _engine(monkeypatch: pytest.MonkeyPatch, *, carry: bool = True) -> ZoneLimitLiveEngine:
    _install_stub_detector(monkeypatch, [ZONE])
    engine = ZoneLimitLiveEngine(params=_params(), carry_zones=carry)
    engine.on_htf_bars(_SYMBOL, _TF, _bars(0, 30))
    _install_stub_detector(monkeypatch, [])  # 창이 미끄러져 탐지기가 더는 그 존을 못 만든다
    return engine


def _zones(engine: ZoneLimitLiveEngine) -> dict[tuple[str, int, int], object]:
    return dict(engine._series[(_SYMBOL, _TF)].zones)


def test_zone_slid_out_of_the_window_is_carried(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(monkeypatch)
    engine.on_htf_bars(_SYMBOL, _TF, _bars(1, 30))
    watch = engine._series[(_SYMBOL, _TF)].zones[ZID]
    assert watch.carried and watch.checked_through == 30 * _H
    off = _engine(monkeypatch, carry=False)
    off.on_htf_bars(_SYMBOL, _TF, _bars(1, 30))
    assert ZID not in _zones(off)  # 끄면 예전처럼 잊는다


def test_carried_zone_breaks_like_the_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(monkeypatch)
    engine.on_htf_bars(_SYMBOL, _TF, _bars(1, 30))
    engine.on_htf_bars(_SYMBOL, _TF, _bars(2, 30, lows={31: 89.9}))  # 저가가 바닥 아래 = 무효화
    assert ZID not in _zones(engine)


def test_carried_zone_counts_taps_bar_by_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(monkeypatch)
    engine.on_htf_bars(_SYMBOL, _TF, _bars(1, 30, lows={30: 99.0}))  # 바깥 → 안 = 탭
    watch = engine._series[(_SYMBOL, _TF)].zones[ZID]
    assert watch.ob.tapped_times == (30 * _H,) and watch.bar_inside
    engine.on_htf_bars(_SYMBOL, _TF, _bars(2, 30, lows={30: 99.0, 31: 99.2}))  # 안 → 안
    assert engine._series[(_SYMBOL, _TF)].zones[ZID].ob.tapped_times == (30 * _H,)
    engine.on_htf_bars(_SYMBOL, _TF, _bars(4, 30, lows={30: 99.0, 31: 99.2, 33: 98.0}))
    assert engine._series[(_SYMBOL, _TF)].zones[ZID].ob.tapped_times == (30 * _H, 33 * _H)


def test_zone_in_the_detector_archive_is_not_carried(monkeypatch: pytest.MonkeyPatch) -> None:
    """탐지기가 아직 보는 존(무효화돼 아카이브에만 남은 것 포함)은 기억으로 넘기지 않는다."""
    engine = _engine(monkeypatch)
    broken = ZONE.model_copy(update={"breaker": True, "break_time": 25 * _H})
    _install_stub_detector(monkeypatch, [broken])
    engine.on_htf_bars(_SYMBOL, _TF, _bars(1, 30))
    assert ZID not in _zones(engine)


def test_carried_zone_survives_snapshot_and_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _engine(monkeypatch)
    engine.on_htf_bars(_SYMBOL, _TF, _bars(1, 30, lows={30: 99.0}))
    engine.on_substep(_SYMBOL, _TF, time_ms=31 * _H, low=101.0, high=102.0, close=101.0)
    snap = engine.snapshot_series(_SYMBOL, _TF, last_htf_time=30 * _H, saved_ms=0)
    assert snap is not None
    snap = SeriesStateSnapshot.model_validate_json(snap.model_dump_json())
    fresh = ZoneLimitLiveEngine(params=_params(), carry_zones=True)
    fresh.on_htf_bars(_SYMBOL, _TF, _bars(1, 30, lows={30: 99.0}))  # 탐지기는 그 존을 못 만든다
    assert ZID not in _zones(fresh)
    fresh.restore_series(_SYMBOL, _TF, snap)
    watch = fresh._series[(_SYMBOL, _TF)].zones[ZID]
    assert watch.carried and watch.ob.tapped_times == (30 * _H,) and watch.bar_inside
    # 되살린 뒤에도 이어서 따라간다
    fresh.on_htf_bars(_SYMBOL, _TF, _bars(2, 30, lows={30: 99.0, 31: 89.0}))
    assert ZID not in _zones(fresh)


def test_runner_uses_the_long_window_only_the_first_time() -> None:
    from live.zone_limit_runner import ZoneLimitPaperRunner

    runner = ZoneLimitPaperRunner.__new__(ZoneLimitPaperRunner)
    runner._bootstrap_days = 730
    runner._lookback_bars = 1500
    runner._last_htf = {}
    assert runner._window_bars("X", "15m") == 730 * 96
    assert runner._window_bars("X", "1d") == 1500  # 2년치가 창보다 짧으면 창
    runner._last_htf[("X", "15m")] = 0
    assert runner._window_bars("X", "15m") == 1500
    runner._bootstrap_days = 0
    assert runner._window_bars("Y", "15m") == 1500
