"""live.paper 테스트 — 가상 포지션 추적."""

from __future__ import annotations

from live.paper import PaperBook, PaperPosition
from strategy.models import OrderBlockDirection, SignalExitReason


def _long() -> PaperPosition:
    return PaperPosition(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_time=1000,
        entry_price=100.0,
    )


def _short() -> PaperPosition:
    return PaperPosition(
        symbol="ETH/USDT:USDT",
        timeframe="4h",
        direction=OrderBlockDirection.BEARISH,
        entry_time=2000,
        entry_price=200.0,
    )


def test_pnl_pct_long_and_short() -> None:
    assert _long().pnl_pct(110.0) == 10.0  # 롱: 상승이 +
    assert _long().pnl_pct(90.0) == -10.0
    assert _short().pnl_pct(180.0) == 10.0  # 숏: 하락이 +
    assert _short().pnl_pct(220.0) == -10.0


def test_open_and_close_records_trade() -> None:
    book = PaperBook()
    pos = _long()
    assert book.open(pos) is pos
    assert book.position("BTC/USDT:USDT", "1h") == pos

    trade = book.close(
        "BTC/USDT:USDT",
        "1h",
        exit_time=5000,
        exit_price=110.0,
        reason=SignalExitReason.TAKE_PROFIT,
    )
    assert trade is not None
    assert trade.realized_pct == 10.0
    assert trade.reason is SignalExitReason.TAKE_PROFIT
    assert book.position("BTC/USDT:USDT", "1h") is None  # 청산 후 비어 있음
    assert book.closed == [trade]


def test_second_open_on_same_series_ignored() -> None:
    """한 시리즈에는 동시 1포지션만 — 두 번째 진입은 무시된다."""
    book = PaperBook()
    book.open(_long())
    again = PaperPosition(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BEARISH,
        entry_time=1500,
        entry_price=105.0,
    )
    assert book.open(again) is None
    assert book.position("BTC/USDT:USDT", "1h") == _long()  # 원래 포지션 유지


def test_close_without_open_returns_none() -> None:
    book = PaperBook()
    trade = book.close(
        "BTC/USDT:USDT", "1h", exit_time=1, exit_price=1.0, reason=SignalExitReason.STOP_LOSS
    )
    assert trade is None
    assert book.closed == []


def test_open_positions_lists_across_series() -> None:
    book = PaperBook()
    book.open(_long())
    book.open(_short())
    assert len(book.open_positions) == 2
