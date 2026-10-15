"""팔 몰림 규칙(WAN-451 ③) — 그림자 주문 · 신호 장부 · 정본 판정 함수와 같은 값."""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from backtest import wan436_stoch_crowd_rules as w436
from backtest import wan439_crash_size_layer as w439
from live import arm_rules as ar
from live.arm_signals import ArmSignal, ArmSignalStore
from live.engine_state import SeriesStateSnapshot
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import FunnelCounts, OrderJournal
from live.zone_limit_notifier import ARM_REJECT_PHRASES, format_daily_summary
from live.zone_limit_runner import ZoneLimitPaperRunner
from strategy.models import OrderBlock, OrderBlockDirection
from tests.test_limit_engine import (
    _DESC_CLOSES,
    _FORMING,
    _H,
    _M,
    _SYMBOL,
    _TF,
    _htf_df,
    _install_stub_detector,
    _params,
)

P = ar.ArmParams()


# ------------------------------------------------------------------ 판정 함수 = 정본


def test_crowd_params_are_the_canonical_rule_set() -> None:
    rules = w439.RULES
    assert rules.btc_gate == P.crowd_btc_gate and rules.budget == P.crowd_budget
    assert rules.skip_early and rules.stop_multiple == P.stop_multiple
    assert (P.crowd_skip_low, P.crowd_skip_high) == (w436.SKIP_LOW, w436.SKIP_HIGH)
    assert P.crowd_window_ms == w436.SIGNAL_WINDOW_MS
    assert P.crowd_btc_lookback_minutes == w436.BTC_LOOKBACK_MIN


@pytest.mark.parametrize("count", [0, 1, 5, 10, 11, 12, 30, 200])
@pytest.mark.parametrize("btc", [None, -0.10, -0.025, -0.0249, 0.0, 0.05])
def test_crowd_decision_equals_backtest_rule(count: int, btc: float | None) -> None:
    want = w436.rule_decision(w439.RULES, count, math.nan if btc is None else btc)
    got = ar.crowd_decision(P, count, btc)
    assert got.size == pytest.approx(want, abs=0)
    if want == 0.0:
        assert got.reason_code in (ar.REJECT_CODE_ARM_CROWD_SKIP, ar.REJECT_CODE_ARM_CROWD_BTC)


def test_btc_4h_return_equals_backtest() -> None:
    rng = np.random.default_rng(436)
    times = np.array(sorted({int(t) * _M for t in rng.integers(0, 3000, 2500)}), dtype=np.int64)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.002, len(times)))
    for entry in map(int, rng.integers(400, 3000, 200)):
        t = entry * _M
        mine = ar.btc_lookback_return(list(map(int, times)), list(map(float, closes)), t, 240)
        theirs = w436.btc_return(times, closes, t)
        assert (mine is None and math.isnan(theirs)) or mine == pytest.approx(theirs, abs=0)


def test_signal_window_matches_backtest_counts() -> None:
    """같은 시각들이면 장부 창 조회 = `wan436.signal_counts`(같은 ms는 세지 않는다)."""
    rng = np.random.default_rng(1)
    times = sorted(int(t) * _M for t in rng.integers(0, 5000, 300))
    store = ArmSignalStore(":memory:")
    for i, t in enumerate(times):
        store.record(ArmSignal("S", "1h", i, i, t, 1.0, 0.9, True))
    want = w436.signal_counts(times)
    got = [store.count(t - P.crowd_window_ms, t) for t in times]
    assert got == want


def test_store_is_idempotent_and_excludes_twin_and_uncounted() -> None:
    store = ArmSignalStore(":memory:")
    sig = ArmSignal("A", "1h", 10, 11, 5 * _M, 1.0, 0.9, True)
    assert store.record(sig) and not store.record(sig)
    store.record(ArmSignal("B", "4h", 1, 2, 6 * _M, 1.0, 0.9, False, ar.REJECT_CODE_ARM_STOCH))
    store.record(ArmSignal("C", "4h", 1, 2, 7 * _M, 1.0, 0.9, True))
    assert store.count(0, 10 * _M) == 2
    assert store.count(0, 10 * _M, exclude=("A", "1h", 10, 11)) == 1
    assert len(store.rows(0, 10 * _M)) == 3


# ------------------------------------------------------------------ 그림자 주문


def _zone(start: int, top: float = 99.5, bottom: float = 90.0) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=start,
        confirmed_time=_H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _shadow_engine(
    monkeypatch: pytest.MonkeyPatch, zones: list[OrderBlock], *, shadows: bool = True
) -> ZoneLimitLiveEngine:
    _install_stub_detector(monkeypatch, zones)
    engine = ZoneLimitLiveEngine(params=_params(), track_shadows=shadows)
    engine.on_htf_bars(_SYMBOL, _TF, _htf_df(closes=_DESC_CLOSES))
    return engine


def test_every_tap_gets_a_shadow_even_when_the_slot_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """두 존을 같이 탭 — 진짜 주문은 슬롯이 하나라 하나뿐, 그림자는 둘."""
    engine = _shadow_engine(monkeypatch, [_zone(0), _zone(-_H, top=99.6)])
    events = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=99.4, high=101.5, close=100.0)
    assert [e.kind for e in events] == ["placed"]
    state = engine._series[(_SYMBOL, _TF)]
    assert len(state.shadows) == 2
    # 둘 다 닿는 가격 → 진짜 체결 1 + 그림자 체결 2(쌍둥이 포함), 이벤트에 존이 실린다
    events = engine.on_substep(
        _SYMBOL, _TF, time_ms=_FORMING + _M, low=95.0, high=100.0, close=96.0
    )
    kinds = [e.kind for e in events]
    assert kinds.count("filled") == 1 and kinds.count("shadow_filled") == 2
    zones = {e.zone for e in events if e.kind == "shadow_filled"}
    assert len(zones) == 2 and all(e.zone is not None for e in events)
    real = next(e for e in events if e.kind == "filled")
    twin = next(e for e in events if e.kind == "shadow_filled" and e.zone == real.zone)
    assert twin.fill is not None and real.fill is not None
    assert twin.fill.price == real.fill.price  # 같은 사슬 · 같은 입력 → 같은 체결가
    assert state.shadows == []


def test_shadows_are_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _shadow_engine(monkeypatch, [_zone(0)], shadows=False)
    engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=99.4, high=101.5, close=100.0)
    assert engine._series[(_SYMBOL, _TF)].shadows == []


def test_shadow_expires_and_invalidates_like_the_real_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _shadow_engine(monkeypatch, [_zone(0)])
    engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=99.4, high=101.5, close=100.0)
    state = engine._series[(_SYMBOL, _TF)]
    shadow = state.shadows[0][1]
    real = engine.book.pending(_SYMBOL, _TF)
    assert real is not None
    for k in range(1, 30):  # 닿지 않는 봉들 — 만료 계수
        engine.on_substep(
            _SYMBOL, _TF, time_ms=_FORMING + k * _H, low=100.2, high=101.0, close=100.5
        )
        assert (shadow.status, shadow.bars_elapsed) == (real.status, real.bars_elapsed)
        if real.status.is_terminal:
            break
    assert real.status.is_terminal and state.shadows == []
    # 무효화: 존이 대장에서 사라지면 그림자도 끝난다
    engine2 = _shadow_engine(monkeypatch, [_zone(0)])
    engine2.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=99.4, high=101.5, close=100.0)
    _install_stub_detector(monkeypatch, [])
    engine2.on_htf_bars(_SYMBOL, _TF, _htf_df(closes=_DESC_CLOSES))
    assert engine2._series[(_SYMBOL, _TF)].shadows == []


def test_shadows_survive_snapshot_and_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _shadow_engine(monkeypatch, [_zone(0), _zone(-_H, top=99.6)])
    engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=99.4, high=101.5, close=100.0)
    snap = engine.snapshot_series(_SYMBOL, _TF, last_htf_time=_FORMING - _H, saved_ms=0)
    assert snap is not None and len(snap.shadows) == 2
    snap = SeriesStateSnapshot.model_validate_json(snap.model_dump_json())  # 왕복
    fresh = _shadow_engine(monkeypatch, [_zone(0), _zone(-_H, top=99.6)])
    fresh.restore_series(_SYMBOL, _TF, snap)
    restored = fresh._series[(_SYMBOL, _TF)].shadows
    assert sorted(z for z, _ in restored) == sorted(tuple(s.zone) for s in snap.shadows)
    # 옛 스냅샷(그림자 필드 없음)도 읽힌다
    legacy = snap.model_dump()
    legacy.pop("shadows")
    assert SeriesStateSnapshot.model_validate(legacy).shadows == []


# ------------------------------------------------------------------ 일일 요약


def test_arm_rejections_are_summarised_not_sent_one_by_one() -> None:
    assert set(ARM_REJECT_PHRASES) == set(ar.ARM_REJECT_CODES)
    funnel = FunnelCounts(arm=((ar.REJECT_CODE_ARM_CROWD_SKIP, 7), (ar.REJECT_CODE_ARM_STOCH, 3)))
    from datetime import date

    text = format_daily_summary(date(2026, 10, 15), placed=1, filled=10, expired=0, funnel=funnel)
    assert "팔 규칙으로 건너뜀" in text and "급락 초반(신호 1~10개) …… 7" in text


def test_journal_funnel_groups_arm_codes(tmp_path: Path) -> None:
    from live.order_journal import OrderJournal
    from tests.test_arm_rules import _fill_event

    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    for code in (ar.REJECT_CODE_ARM_CROWD_SKIP, ar.REJECT_CODE_ARM_CROWD_SKIP, "notional"):
        event = _fill_event(journal, session, price=140.0, ob_stop=133.0)
        assert event.order.journal_id is not None
        journal.record_entry_result(
            event.order.journal_id, entered=False, reason="x", reason_code=code
        )
    funnel = journal.funnel_counts(start_ms=0, end_ms=10**13)
    assert funnel.arm == ((ar.REJECT_CODE_ARM_CROWD_SKIP, 2),)
    assert funnel.notional == 1 and funnel.other == 0
    journal.close()


# ------------------------------------------------------------------ 러너


def _crowd_runner(
    tmp_path: Path, members: list[str], *, btc_drop: float = 0.0
) -> tuple[ZoneLimitPaperRunner, OrderJournal, int, Path]:
    from data.storage import OhlcvStore
    from data.universe_pool import UniverseStore
    from tests.test_arm_rotation import MONDAY, _runner, _snap
    from tests.test_arm_rules import TF, _seed

    db = tmp_path / "c.db"
    _seed(db, falling=True)  # %K 낮음 · BTC 1분봉 평탄
    if btc_drop:
        t = 60 * TF + 2 * _M
        with OhlcvStore(db) as store:  # 직전 확정 1분봉만 떨어뜨린다(4h 수익률 = btc_drop)
            from data.models import Candle

            px = 100.0 * (1 + btc_drop)
            store.upsert_candles([Candle("BTC/USDT:USDT", "1m", t - _M, px, px, px, px, 1.0)])
    with UniverseStore(db) as u:
        u.save(_snap(MONDAY, members))
    runner, journal, session = _runner(db, [60 * TF + 2 * _M])
    runner._signals = ArmSignalStore(db)
    return runner, journal, session, db


def _prior_signals(store: ArmSignalStore, n: int, before_ms: int) -> None:
    for i in range(n):
        store.record(ArmSignal(f"X{i}", "1h", i, i, before_ms - (i + 1) * _M, 1.0, 0.9, True))


def _decision(db: Path) -> tuple[str, str]:
    import sqlite3

    row = (
        sqlite3.connect(db)
        .execute("SELECT entry_status, COALESCE(entry_reject_code, '') FROM live_limit_orders")
        .fetchone()
    )
    return str(row[0]), str(row[1])


def test_shadow_fill_is_recorded_with_its_population_verdict(tmp_path: Path) -> None:
    from live.limit_engine import EngineEvent
    from tests.test_arm_rules import _fill_event

    runner, journal, session, db = _crowd_runner(tmp_path, ["SYN/USDT:USDT"])
    real = _fill_event(journal, session, price=140.0, ob_stop=133.0)
    real_fill = real.fill
    assert real_fill is not None
    shadow = EngineEvent(
        kind="shadow_filled",
        symbol=real.symbol,
        timeframe=real.timeframe,
        time_ms=real.time_ms,
        order=real.order,
        fill=real.fill,
        zone=("bullish", 7, 8),
    )
    runner._handle_events([shadow])
    assert runner._signals is not None
    (row,) = runner._signals.rows(0, 10**13)
    assert row.counted and (row.zone_start, row.zone_confirmed) == (7, 8)
    narrow = EngineEvent(  # 손절폭 1% — 모집단 밖
        kind="shadow_filled",
        symbol=real.symbol,
        timeframe=real.timeframe,
        time_ms=real.time_ms + _M,
        order=real.order,
        fill=dataclasses.replace(real_fill, stop_price=138.6, time=real.time_ms + _M),
        zone=("bullish", 9, 9),
    )
    runner._handle_events([narrow])
    rows = runner._signals.rows(0, 10**13)
    assert [r.reason_code for r in rows] == ["", ar.REJECT_CODE_ARM_STOP_FLOOR]


@pytest.mark.parametrize(
    ("prior", "btc_drop", "want"),
    [
        (0, 0.0, ("entered", "")),
        (3, 0.0, ("rejected", ar.REJECT_CODE_ARM_CROWD_SKIP)),
        (10, -0.10, ("rejected", ar.REJECT_CODE_ARM_CROWD_SKIP)),
        (12, 0.0, ("rejected", ar.REJECT_CODE_ARM_CROWD_BTC)),
        (12, -0.03, ("entered", "")),
    ],
)
def test_real_fill_runs_the_crowd_rule(
    tmp_path: Path, prior: int, btc_drop: float, want: tuple[str, str]
) -> None:
    from tests.test_arm_rules import _fill_event

    runner, journal, session, db = _crowd_runner(tmp_path, ["SYN/USDT:USDT"], btc_drop=btc_drop)
    event = _fill_event(journal, session, price=140.0, ob_stop=133.0)
    assert runner._signals is not None and event.fill is not None
    _prior_signals(runner._signals, prior, event.fill.time)
    runner._handle_events([event])
    assert _decision(db) == want
    if want[0] == "entered":
        (pos,) = runner._executor.open_positions
        assert pos.stop_price == pytest.approx(126.0)


def test_budget_scales_the_size(tmp_path: Path) -> None:
    """신호 12개 · BTC −3% → 예산 10/13. 같은 체결의 신호 0개 판과 수량 비가 그만큼이다."""
    from tests.test_arm_rules import _fill_event

    qty = {}
    for prior in (0, 12):
        runner, journal, session, _ = _crowd_runner(
            tmp_path / str(prior), ["SYN/USDT:USDT"], btc_drop=-0.03
        )
        event = _fill_event(journal, session, price=140.0, ob_stop=133.0)
        assert runner._signals is not None and event.fill is not None
        _prior_signals(runner._signals, prior, event.fill.time)
        runner._handle_events([event])
        (pos,) = runner._executor.open_positions
        qty[prior] = pos.quantity
    assert qty[12] / qty[0] == pytest.approx(10 / 13)


def test_twin_shadow_is_not_counted_against_its_own_fill(tmp_path: Path) -> None:
    """진짜 주문의 쌍둥이 그림자(같은 칸·같은 존)는 「다른 신호」가 아니다."""
    from tests.test_arm_rules import _fill_event

    runner, journal, session, db = _crowd_runner(tmp_path, ["SYN/USDT:USDT"])
    event = _fill_event(journal, session, price=140.0, ob_stop=133.0)
    assert runner._signals is not None and event.fill is not None
    fill_ms = event.fill.time
    event = dataclasses.replace(event, zone=("bullish", 0, 1))
    runner._signals.record(  # 쌍둥이가 1분 먼저 체결된 것으로 기록된 경우
        ArmSignal(event.symbol, event.timeframe, 0, 1, fill_ms - _M, 1.0, 0.9, True)
    )
    runner._handle_events([event])
    assert _decision(db) == ("entered", "")
