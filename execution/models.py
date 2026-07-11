"""주문·체결·포지션 값 객체 (WAN-9).

전략 시그널(WAN-23)을 거래소 주문으로 옮기는 실행 레이어의 불변 모델들이다.
브로커(페이퍼/실거래)와 실행 엔진이 공유한다. 자금이 오가는 영역이라 상태를
명시적으로 추적한다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from strategy.models import OrderBlockDirection


class OrderSide(StrEnum):
    """주문 방향(거래소 관점). 롱 진입·숏 청산=BUY, 숏 진입·롱 청산=SELL."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """주문 유형."""

    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    """체결 결과 상태."""

    FILLED = "filled"
    """요청 수량이 전량 체결됨."""
    PARTIALLY_FILLED = "partially_filled"
    """일부만 체결됨(나머지는 미체결/취소)."""
    REJECTED = "rejected"
    """거래소·리스크 등으로 주문이 거부됨(체결 수량 0)."""


def side_for_entry(direction: OrderBlockDirection) -> OrderSide:
    """진입 방향(롱/숏)에 대응하는 주문 방향."""
    return OrderSide.BUY if direction is OrderBlockDirection.BULLISH else OrderSide.SELL


def side_for_exit(direction: OrderBlockDirection) -> OrderSide:
    """오픈 포지션(롱/숏)을 청산할 때의 주문 방향(진입의 반대)."""
    return OrderSide.SELL if direction is OrderBlockDirection.BULLISH else OrderSide.BUY


class Order(BaseModel):
    """거래소로 보내는(또는 시뮬레이션하는) 단일 주문 요청."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    side: OrderSide
    type: OrderType
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    """지정가 주문의 가격. 시장가면 None."""
    reduce_only: bool = False
    """청산 전용(포지션 축소만) 플래그. 청산 주문에 True."""
    client_id: str | None = None
    """멱등·추적용 클라이언트 주문 식별자."""


class Fill(BaseModel):
    """주문 체결 결과. 브로커가 `Order`를 실행하고 돌려준다."""

    model_config = ConfigDict(frozen=True)

    order: Order
    status: OrderStatus
    filled_quantity: float = Field(ge=0)
    average_price: float = Field(ge=0)
    """체결 평균가. 미체결(REJECTED)이면 0."""
    fee: float = Field(default=0.0, ge=0)
    """체결 수수료(견적 통화). 페이퍼는 0 가능."""

    @property
    def is_filled(self) -> bool:
        """일부라도 체결됐는지."""
        return self.filled_quantity > 0.0

    @property
    def notional(self) -> float:
        """체결 명목가치(체결가 × 체결수량)."""
        return self.average_price * self.filled_quantity


class Position(BaseModel):
    """추적 중인 오픈 포지션 하나. 심볼·타임프레임별로 최대 하나."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    quantity: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    entry_time: int
    stop_price: float | None = None
    """손절 참조가(오더블록 무효화). 없으면 None."""
    take_profit_price: float | None = None
    """가장 가까운 익절 목표선 가격. 없으면 None."""

    @property
    def is_long(self) -> bool:
        return self.direction is OrderBlockDirection.BULLISH

    @property
    def notional(self) -> float:
        """진입 명목가치(진입가 × 수량)."""
        return self.entry_price * self.quantity

    def realized_pnl(self, exit_price: float) -> float:
        """주어진 청산가에서의 실현 손익(견적 통화). 롱은 상승이 +, 숏은 하락이 +."""
        sign = 1.0 if self.is_long else -1.0
        return sign * (exit_price - self.entry_price) * self.quantity
