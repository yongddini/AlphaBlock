"""라이브/페이퍼 대기 지정가 주문 상태 머신 (WAN-41).

백테스트(`backtest.substep`)가 1분봉 서브스텝으로 시뮬레이션하는 "존-지정가 대기 →
닿으면 실시간 RSI 확인 → 체결/취소"를, 라이브·페이퍼에서 **웹소켓 실시간 가격**으로
동일하게 굴리는 상태 머신이다. 백테스트와 **동일한 `RealtimeRsi` 상태 머신**을
공유하므로 실시간 RSI 값이 두 경로에서 일치한다(이슈 WAN-41 완료 기준).

## 상태 전이

    PENDING ──(닿음 + 실시간 RSI 조건 충족)──▶ FILLED
        │
        ├─(닿음 + 조건 미충족 + cancel_on_condition_fail)─▶ CANCELLED_CONDITION_FAILED
        ├─(오더블록 무효화)──────────────────────────────▶ CANCELLED_INVALIDATED
        └─(limit_valid_bars 경과)───────────────────────▶ CANCELLED_EXPIRED

`on_price`는 매 틱(웹소켓 가격) 호출하고, 상위TF 봉이 마감되면 `on_bar_close`로
실시간 RSI 상태를 굴리며 경과 봉 수를 센다. 무효화는 `cancel_invalidated`로 즉시
반영한다. 한 (symbol, timeframe) 시리즈에는 대기 주문 하나만 둔다(단일 포지션 규칙,
WAN-23).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
SeriesKey = tuple[str, str]


class LimitOrderStatus(StrEnum):
    """대기 지정가 주문의 상태."""

    PENDING = "pending"
    FILLED = "filled"
    CANCELLED_EXPIRED = "cancelled_expired"
    CANCELLED_INVALIDATED = "cancelled_invalidated"
    CANCELLED_CONDITION_FAILED = "cancelled_condition_failed"

    @property
    def is_terminal(self) -> bool:
        return self is not LimitOrderStatus.PENDING


@dataclass(frozen=True)
class LimitFill:
    """지정가 체결 결과."""

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    price: float
    """체결가(지정가)."""
    time: int
    """체결 시각(ms)."""
    rsi: float
    """체결 순간의 실시간 RSI."""
    stop_price: float
    """진입 근거 오더블록의 무효화 경계(손절 참조가)."""


@dataclass
class PendingLimitOrder:
    """한 오더블록에 예약한 대기 지정가 주문.

    `rsi_state`는 이 주문이 걸린 시점 **직전까지**의 확정봉으로 시딩돼 있어야 한다
    (`RealtimeRsi.seed_from_closed`). 백테스트 서브스텝 시뮬레이터와 동일한 상태
    머신을 그대로 쓴다.
    """

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    limit_price: float
    stop_price: float
    rsi_state: RealtimeRsi
    rsi_oversold: float
    rsi_overbought: float
    limit_valid_bars: int = 24
    cancel_on_condition_fail: bool = False
    status: LimitOrderStatus = LimitOrderStatus.PENDING
    bars_elapsed: int = field(default=0)

    @property
    def is_long(self) -> bool:
        return self.direction is OrderBlockDirection.BULLISH

    def on_bar_close(self, closed_price: float) -> None:
        """상위TF 봉 마감: 실시간 RSI 상태를 굴리고 경과 봉 수를 늘린다.

        경과 봉 수가 `limit_valid_bars`에 도달하면 만료로 취소한다.
        """
        if self.status.is_terminal:
            return
        self.rsi_state.commit(closed_price)
        self.bars_elapsed += 1
        if self.bars_elapsed >= self.limit_valid_bars:
            self.status = LimitOrderStatus.CANCELLED_EXPIRED

    def on_price(self, price: float, *, now_ms: int) -> LimitFill | None:
        """실시간 가격 틱을 반영한다. 체결되면 `LimitFill`, 아니면 None.

        가격이 지정가에 닿으면(롱 `price <= 지정가`, 숏 `price >= 지정가`) 그 순간의
        실시간 RSI로 조건을 판정한다 — 롱 `RSI <= oversold`, 숏 `RSI >= overbought`.
        충족하면 체결, 미충족이면 주문을 유지하거나(기본) 취소한다.
        """
        if self.status.is_terminal:
            return None
        touched = price <= self.limit_price if self.is_long else price >= self.limit_price
        if not touched:
            return None
        live_rsi = self.rsi_state.value(price)
        condition = live_rsi is not None and (
            live_rsi <= self.rsi_oversold if self.is_long else live_rsi >= self.rsi_overbought
        )
        if condition:
            assert live_rsi is not None
            self.status = LimitOrderStatus.FILLED
            return LimitFill(
                symbol=self.symbol,
                timeframe=self.timeframe,
                direction=self.direction,
                price=self.limit_price,
                time=now_ms,
                rsi=live_rsi,
                stop_price=self.stop_price,
            )
        if self.cancel_on_condition_fail:
            self.status = LimitOrderStatus.CANCELLED_CONDITION_FAILED
        return None

    def cancel_invalidated(self) -> None:
        """오더블록 무효화로 대기 주문을 즉시 취소한다."""
        if not self.status.is_terminal:
            self.status = LimitOrderStatus.CANCELLED_INVALIDATED


class LimitOrderBook:
    """심볼·타임프레임별 대기 지정가 주문 장부.

    한 시리즈에는 대기 주문 하나만 둔다(단일 포지션 규칙). 체결·취소된 주문은
    장부에서 제거되며, 체결은 `LimitFill`로 반환해 실행/페이퍼 엔진이 포지션 오픈에
    쓰도록 한다.
    """

    def __init__(self) -> None:
        self._pending: dict[SeriesKey, PendingLimitOrder] = {}

    def pending(self, symbol: str, timeframe: str) -> PendingLimitOrder | None:
        """해당 시리즈의 대기 주문. 없으면 None."""
        return self._pending.get((symbol, timeframe))

    @property
    def open_orders(self) -> list[PendingLimitOrder]:
        return list(self._pending.values())

    def place(self, order: PendingLimitOrder) -> PendingLimitOrder | None:
        """대기 지정가 주문을 예약한다. 이미 대기 주문이 있으면 무시하고 None."""
        key = (order.symbol, order.timeframe)
        if key in self._pending:
            _logger.warning("이미 대기 지정가 주문 존재 — 예약 무시: %s %s", *key)
            return None
        self._pending[key] = order
        return order

    def on_price(
        self, symbol: str, timeframe: str, price: float, *, now_ms: int
    ) -> LimitFill | None:
        """시리즈의 대기 주문에 실시간 가격을 반영한다. 체결되면 장부에서 제거."""
        key = (symbol, timeframe)
        order = self._pending.get(key)
        if order is None:
            return None
        fill = order.on_price(price, now_ms=now_ms)
        if order.status.is_terminal:
            del self._pending[key]
        return fill

    def on_bar_close(
        self, symbol: str, timeframe: str, closed_price: float
    ) -> LimitOrderStatus | None:
        """상위TF 봉 마감을 반영한다. 만료 취소되면 장부에서 제거하고 상태를 반환."""
        key = (symbol, timeframe)
        order = self._pending.get(key)
        if order is None:
            return None
        order.on_bar_close(closed_price)
        if order.status.is_terminal:
            del self._pending[key]
            return order.status
        return None

    def cancel_invalidated(self, symbol: str, timeframe: str) -> bool:
        """오더블록 무효화로 대기 주문을 취소한다. 취소했으면 True."""
        key = (symbol, timeframe)
        order = self._pending.pop(key, None)
        if order is None:
            return False
        order.cancel_invalidated()
        return True
