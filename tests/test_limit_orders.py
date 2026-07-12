"""라이브/페이퍼 대기 지정가 주문 상태 머신 테스트 (WAN-41).

`live.limit_orders`가 백테스트 서브스텝(`backtest.substep`)과 동일한 규칙으로 대기
지정가 주문을 굴리는지 검증한다: 닿음 + 실시간 RSI 조건 → 체결, 조건 미충족 처리,
만료·무효화 취소. 실시간 RSI 상태 머신을 공유하므로 값이 백테스트와 일치한다.
"""

from __future__ import annotations

from live.limit_orders import (
    LimitOrderBook,
    LimitOrderStatus,
    PendingLimitOrder,
)
from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

# 롱 셋업: 지정가(존 상단)=100, 손절(존 하단)=90.
_LIMIT = 100.0
_STOP = 90.0
# 강한 하락 시딩 → 실시간 RSI 과매도(≤30) 유지(롱 조건 충족).
_OVERSOLD_SEED = [140.0, 130.0, 120.0, 110.0, 105.0]
# 강한 상승 시딩 → 과매수(≥70)(롱 조건 미충족).
_OVERBOUGHT_SEED = [90.0, 95.0, 100.0, 105.0, 110.0]


def _long_order(seed: list[float], **kw: object) -> PendingLimitOrder:
    return PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        rsi_state=RealtimeRsi.seed_from_closed(seed, length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        **kw,  # type: ignore[arg-type]
    )


def test_fills_on_touch_when_rsi_condition_met() -> None:
    order = _long_order(_OVERSOLD_SEED)
    # 지정가 위에서는 미체결.
    assert order.on_price(101.0, now_ms=1) is None
    assert not order.status.is_terminal
    # 지정가에 닿고 실시간 RSI 과매도 → 체결.
    fill = order.on_price(100.0, now_ms=2)
    assert fill is not None
    assert fill.price == _LIMIT
    assert fill.time == 2
    assert fill.rsi <= 30.0
    assert fill.stop_price == _STOP
    assert order.status is LimitOrderStatus.FILLED


def test_realtime_rsi_matches_backtest_state_machine() -> None:
    """라이브 주문의 실시간 RSI가 동일 시딩·현재가의 `RealtimeRsi`와 일치한다."""
    order = _long_order(_OVERSOLD_SEED)
    reference = RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3)
    fill = order.on_price(100.0, now_ms=5)
    assert fill is not None
    assert fill.rsi == reference.value(100.0)


def test_condition_fail_keeps_order_by_default() -> None:
    order = _long_order(_OVERBOUGHT_SEED)
    # 닿았지만 과매수라 롱 조건 미충족 → 기본은 유지.
    assert order.on_price(100.0, now_ms=1) is None
    assert order.status is LimitOrderStatus.PENDING


def test_condition_fail_cancels_when_configured() -> None:
    order = _long_order(_OVERBOUGHT_SEED, cancel_on_condition_fail=True)
    assert order.on_price(100.0, now_ms=1) is None
    assert order.status is LimitOrderStatus.CANCELLED_CONDITION_FAILED


def test_expiry_cancels_after_valid_bars() -> None:
    order = _long_order(_OVERSOLD_SEED, limit_valid_bars=2)
    order.on_bar_close(104.0)
    assert not order.status.is_terminal
    order.on_bar_close(103.0)
    assert order.status is LimitOrderStatus.CANCELLED_EXPIRED
    # 만료 후에는 닿아도 체결되지 않는다.
    assert order.on_price(100.0, now_ms=9) is None


def test_invalidation_cancels_immediately() -> None:
    order = _long_order(_OVERSOLD_SEED)
    order.cancel_invalidated()
    assert order.status is LimitOrderStatus.CANCELLED_INVALIDATED
    assert order.on_price(100.0, now_ms=9) is None


def test_book_single_order_per_series_and_fill_removes() -> None:
    book = LimitOrderBook()
    order = _long_order(_OVERSOLD_SEED)
    assert book.place(order) is order
    # 같은 시리즈 중복 예약은 무시.
    assert book.place(_long_order(_OVERSOLD_SEED)) is None
    assert book.pending("BTC/USDT:USDT", "1h") is order

    assert book.on_price("BTC/USDT:USDT", "1h", 101.0, now_ms=1) is None
    fill = book.on_price("BTC/USDT:USDT", "1h", 100.0, now_ms=2)
    assert fill is not None
    # 체결되면 장부에서 제거된다.
    assert book.pending("BTC/USDT:USDT", "1h") is None
    assert book.open_orders == []


def test_book_bar_close_expiry_and_invalidation() -> None:
    book = LimitOrderBook()
    book.place(_long_order(_OVERSOLD_SEED, limit_valid_bars=1))
    status = book.on_bar_close("BTC/USDT:USDT", "1h", 104.0)
    assert status is LimitOrderStatus.CANCELLED_EXPIRED
    assert book.pending("BTC/USDT:USDT", "1h") is None

    book.place(_long_order(_OVERSOLD_SEED))
    assert book.cancel_invalidated("BTC/USDT:USDT", "1h") is True
    assert book.cancel_invalidated("BTC/USDT:USDT", "1h") is False
