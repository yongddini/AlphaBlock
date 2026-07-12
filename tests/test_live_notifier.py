"""live.notifier 테스트 — 메시지 포맷, 이벤트 수집, 페이퍼 연동."""

from __future__ import annotations

from common.telegram import TelegramClient, TelegramResponse
from live.notifier import (
    Notifier,
    SignalEvent,
    collect_events,
    format_entry,
    format_exit,
)
from live.paper import ClosedTrade, PaperBook, PaperPosition
from strategy.confluence import ConfluenceResult, ConfluenceSignal, IndicatorSnapshot, SignalKind
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    SignalExitReason,
)


def _order_block(direction: OrderBlockDirection, *, top: float, bottom: float) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _entry(
    *,
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    time: int = 1_700_000_000_000,
    price: float = 65_000.0,
    rsi: float | None = 28.4,
    lines: dict[str, float] | None = None,
    ob: OrderBlock | None = None,
) -> ConfluenceSignal:
    resolved_lines = lines if lines is not None else {"ema_20": 64_000.0, "ema_240": 66_500.0}
    resolved_ob = ob if ob is not None else _order_block(direction, top=65_200.0, bottom=64_500.0)
    return ConfluenceSignal(
        kind=SignalKind.ENTRY,
        direction=direction,
        time=time,
        price=price,
        confirmed=True,
        rsi=rsi,
        order_block=resolved_ob,
        indicators=IndicatorSnapshot(time=time, close=price, rsi=rsi, lines=resolved_lines),
    )


def _exit(
    *,
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    time: int = 1_700_003_600_000,
    price: float = 66_500.0,
    reason: SignalExitReason = SignalExitReason.TAKE_PROFIT,
) -> ConfluenceSignal:
    return ConfluenceSignal(
        kind=SignalKind.EXIT,
        direction=direction,
        time=time,
        price=price,
        confirmed=True,
        rsi=None,
        order_block=None,
        indicators=IndicatorSnapshot(time=time, close=price, rsi=None, lines={}),
        exit_reason=reason,
    )


def test_format_entry_long_contains_key_fields() -> None:
    event = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=_entry())
    msg = format_entry(event)
    assert "진입 신호" in msg
    assert "BTC/USDT:USDT" in msg and "1h" in msg
    assert "롱" in msg
    assert "65,000" in msg  # 가격 천 단위 포맷
    assert "28.4" in msg and "과매도" in msg
    assert "64,500 ~ 65,200" in msg  # 오더블록 존
    assert "손절가: `64,500`" in msg  # 롱 손절 = 존 하단
    assert "EMA240 66,500" in msg  # 진입가 너머 가장 가까운 선


def test_format_entry_short_take_profit_line_below() -> None:
    ob = _order_block(OrderBlockDirection.BEARISH, top=66_000.0, bottom=65_500.0)
    event = SignalEvent(
        symbol="ETH/USDT:USDT",
        timeframe="4h",
        signal=_entry(
            direction=OrderBlockDirection.BEARISH,
            price=65_000.0,
            rsi=74.0,
            lines={"ema_20": 66_000.0, "vwma_100": 64_000.0},
            ob=ob,
        ),
    )
    msg = format_entry(event)
    assert "숏" in msg and "과매수" in msg
    assert "손절가: `66,000`" in msg  # 숏 손절 = 존 상단
    assert "VWMA100 64,000" in msg  # 진입가 아래 가장 가까운 선


def test_format_entry_without_take_profit_line() -> None:
    event = SignalEvent(
        symbol="BTC/USDT:USDT", timeframe="1h", signal=_entry(price=65_000.0, lines={})
    )
    msg = format_entry(event)
    assert "익절 목표선: `없음`" in msg


def test_format_exit_with_realized_pnl() -> None:
    trade = ClosedTrade(
        position=PaperPosition(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=1_700_000_000_000,
            entry_price=65_000.0,
        ),
        exit_time=1_700_003_600_000,
        exit_price=66_500.0,
        reason=SignalExitReason.TAKE_PROFIT,
    )
    event = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=_exit())
    msg = format_exit(event, trade)
    assert "청산 신호" in msg
    assert "익절" in msg
    assert "+2.31%" in msg  # (66500-65000)/65000 = 2.3077%


def test_format_exit_stop_loss_without_trade() -> None:
    event = SignalEvent(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        signal=_exit(reason=SignalExitReason.STOP_LOSS),
    )
    msg = format_exit(event, None)
    assert "손절" in msg
    assert "손익" not in msg  # trade 없으면 손익 줄 생략


def test_signal_id_is_stable_and_distinguishes_kind() -> None:
    entry = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=_entry())
    exit_tp = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=_exit())
    assert entry.signal_id == entry.signal_id
    assert entry.signal_id != exit_tp.signal_id
    assert "entry" in entry.signal_id and "exit" in exit_tp.signal_id


def test_collect_events_orders_by_time_entry_before_exit() -> None:
    result = ConfluenceResult(
        params=ConfluenceParams(),
        entries=[
            _entry(time=3000),
            _entry(time=1000),
            _entry(time=2000, rsi=50.0).model_copy(update={"confirmed": False}),
        ],
        exits=[_exit(time=1000)],
    )
    events = collect_events(result, "BTC/USDT:USDT", "1h")
    # 미확정 진입(confirmed=False, time=2000)은 제외. 같은 시각이면 진입이 청산보다 앞.
    kinds_times = [(e.signal.kind, e.time) for e in events]
    assert kinds_times == [
        (SignalKind.ENTRY, 1000),
        (SignalKind.EXIT, 1000),
        (SignalKind.ENTRY, 3000),
    ]


def test_notifier_sends_and_updates_paper_book() -> None:
    sent: list[str] = []

    def transport(url: str, payload: dict[str, object]) -> TelegramResponse:
        sent.append(str(payload["text"]))
        return TelegramResponse(ok=True, status_code=200)

    book = PaperBook()
    notifier = Notifier(TelegramClient("t", "c", transport=transport), book)

    entry_event = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=_entry(price=65_000.0))
    assert notifier.handle(entry_event) is True
    assert book.position("BTC/USDT:USDT", "1h") is not None

    exit_event = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=_exit(price=66_500.0))
    assert notifier.handle(exit_event) is True
    assert book.position("BTC/USDT:USDT", "1h") is None  # 청산됨
    assert len(book.closed) == 1
    assert len(sent) == 2  # 진입·청산 각각 전송


def test_notifier_dry_run_without_telegram() -> None:
    book = PaperBook()
    notifier = Notifier(None, book)
    event = SignalEvent(symbol="BTC/USDT:USDT", timeframe="1h", signal=_entry())
    # 드라이런: 전송은 False지만 페이퍼 장부는 갱신된다.
    assert notifier.handle(event) is False
    assert book.position("BTC/USDT:USDT", "1h") is not None
