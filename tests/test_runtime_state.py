"""live.runtime_state 영속화 테스트 (WAN-30)."""

from __future__ import annotations

from pathlib import Path

from live.notifier import SignalEvent
from live.paper import PaperPosition
from live.runtime_state import PositionSnapshot, RuntimeStateStore
from strategy.confluence import ConfluenceSignal, IndicatorSnapshot, SignalKind
from strategy.models import OrderBlockDirection, SignalExitReason


def _entry_event(symbol: str, time: int, price: float) -> SignalEvent:
    sig = ConfluenceSignal(
        kind=SignalKind.ENTRY,
        direction=OrderBlockDirection.BULLISH,
        time=time,
        price=price,
        confirmed=True,
        rsi=25.0,
        order_block=None,
        indicators=IndicatorSnapshot(time=time, close=price, rsi=25.0, lines={}),
    )
    return SignalEvent(symbol=symbol, timeframe="1h", signal=sig)


def _position(symbol: str) -> PaperPosition:
    return PaperPosition(
        symbol=symbol,
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_time=1_000,
        entry_price=100.0,
        stop_price=95.0,
        take_profit_price=110.0,
    )


def test_position_snapshot_carries_stop_and_take_profit() -> None:
    snap = PositionSnapshot.from_position(_position("BTC/USDT:USDT"))
    assert snap.stop_price == 95.0
    assert snap.take_profit_price == 110.0
    assert snap.direction is OrderBlockDirection.BULLISH


def test_record_persists_heartbeat_positions_and_events(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)

    state = store.record(
        now_ms=5_000,
        open_positions=[_position("BTC/USDT:USDT")],
        new_events=[_entry_event("BTC/USDT:USDT", 1_000, 100.0)],
    )
    assert state.updated_at == 5_000
    assert state.last_notification_at == 5_000
    assert len(state.open_positions) == 1
    assert len(state.recent_events) == 1

    # 새 프로세스가 파일에서 상태를 복원한다.
    reloaded = RuntimeStateStore(path).load()
    assert reloaded.updated_at == 5_000
    assert reloaded.open_positions[0].symbol == "BTC/USDT:USDT"
    assert reloaded.recent_events[0].kind is SignalKind.ENTRY


def test_record_without_events_keeps_last_notification(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)
    store.record(now_ms=1_000, open_positions=[], new_events=[_entry_event("BTC", 1, 100.0)])
    # 신호 없는 폴링: 하트비트는 오르지만 마지막 알림 시각은 유지된다.
    state = store.record(now_ms=2_000, open_positions=[], new_events=[])
    assert state.updated_at == 2_000
    assert state.last_notification_at == 1_000


def test_recent_events_are_trimmed_to_max(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path, max_events=3)
    for i in range(5):
        store.record(
            now_ms=i,
            open_positions=[],
            new_events=[_entry_event("BTC", i, 100.0 + i)],
        )
    state = store.load()
    assert len(state.recent_events) == 3
    # 가장 최근 3건만 남는다(오래된→최신 순).
    assert [e.time for e in state.recent_events] == [2, 3, 4]


def test_corrupt_state_file_recovers_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text("{ not json", encoding="utf-8")
    state = RuntimeStateStore(path).load()
    assert state.updated_at is None
    assert state.open_positions == []


def test_exit_event_records_reason(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)
    exit_sig = ConfluenceSignal(
        kind=SignalKind.EXIT,
        direction=OrderBlockDirection.BULLISH,
        time=2_000,
        price=110.0,
        confirmed=True,
        rsi=None,
        order_block=None,
        indicators=IndicatorSnapshot(time=2_000, close=110.0, rsi=None, lines={}),
        exit_reason=SignalExitReason.TAKE_PROFIT,
    )
    event = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=exit_sig)
    state = store.record(now_ms=3_000, open_positions=[], new_events=[event])
    assert state.recent_events[0].exit_reason is SignalExitReason.TAKE_PROFIT
