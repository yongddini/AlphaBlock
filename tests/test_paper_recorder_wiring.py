"""Notifier ↔ PaperTradeRecorder 연동 테스트 (WAN-33).

러너가 청산을 낼 때 페이퍼 거래가 `paper_trades`에 누적되는지 확인한다.
"""

from __future__ import annotations

from live.notifier import Notifier, SignalEvent
from live.paper import ClosedTrade, PaperPosition
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.confluence import ConfluenceSignal, IndicatorSnapshot, SignalKind
from strategy.models import OrderBlock, OrderBlockDirection, SignalExitReason


def _order_block() -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=65_200.0,
        bottom=64_500.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _entry_event() -> SignalEvent:
    signal = ConfluenceSignal(
        kind=SignalKind.ENTRY,
        direction=OrderBlockDirection.BULLISH,
        time=1_700_000_000_000,
        price=65_000.0,
        confirmed=True,
        rsi=28.0,
        order_block=_order_block(),
        indicators=IndicatorSnapshot(
            time=1_700_000_000_000,
            close=65_000.0,
            rsi=28.0,
            lines={"ema_240": 66_500.0},
        ),
    )
    return SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=signal)


def _exit_event() -> SignalEvent:
    signal = ConfluenceSignal(
        kind=SignalKind.EXIT,
        direction=OrderBlockDirection.BULLISH,
        time=1_700_003_600_000,
        price=66_500.0,
        confirmed=True,
        rsi=None,
        order_block=None,
        indicators=IndicatorSnapshot(time=1_700_003_600_000, close=66_500.0, rsi=None, lines={}),
        exit_reason=SignalExitReason.TAKE_PROFIT,
    )
    return SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=signal)


def test_notifier_records_closed_trade_via_sink() -> None:
    with PaperTradeStore(":memory:") as store:
        recorder = PaperTradeRecorder(store, fee_rate=0.0004)
        notifier = Notifier(None, trade_sink=recorder)  # 드라이런(텔레그램 없음)

        notifier.handle(_entry_event())
        assert store.count() == 0  # 진입만으론 아직 청산 거래 없음

        notifier.handle(_exit_event())
        assert store.count() == 1

        record = store.list_records()[0]
        assert record.symbol == "BTC/USDT:USDT"
        assert record.reason is SignalExitReason.TAKE_PROFIT
        # 손절가(오더블록 하단)로 리스크가 잡혀 R이 계산된다.
        assert record.stop_price == 64_500.0
        assert record.r_multiple is not None


def test_recorder_sink_never_raises_on_failure() -> None:
    """저장 실패가 러너 흐름을 막지 않아야 한다(견고성)."""

    class BrokenStore:
        def upsert_record(self, record: object) -> None:
            raise RuntimeError("disk full")

    recorder = PaperTradeRecorder(BrokenStore(), fee_rate=0.0)  # type: ignore[arg-type]
    trade = ClosedTrade(
        position=PaperPosition(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=1000,
            entry_price=100.0,
            stop_price=95.0,
        ),
        exit_time=2000,
        exit_price=110.0,
        reason=SignalExitReason.TAKE_PROFIT,
    )
    assert recorder.record(trade) is None  # 예외를 삼키고 None 반환
