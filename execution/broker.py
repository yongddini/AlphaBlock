"""브로커 추상화: 페이퍼(드라이런) · ccxt 실거래 (WAN-9).

`Broker` 프로토콜을 실행 엔진이 소비하고, 두 구현을 제공한다:

* `PaperBroker` — **기본값**. 네트워크·거래소 API를 전혀 호출하지 않고 주문을 즉시
  전량 체결로 시뮬레이션한다. 드라이런/페이퍼 검증용.
* `CcxtLiveBroker` — ccxt로 실제 주문을 낸다. **생성 시 `live_trading=True`를 명시적으로
  넘겨야만** 인스턴스화된다(그렇지 않으면 `RuntimeError`). 실계좌 연결·검증은 WAN-27
  범위이며, 이 이슈에서는 자동으로 켜지 않는다.

안전 원칙: `live_trading`이 꺼진 경로에서는 어떤 실주문 API도 호출되지 않는다.
실행 엔진은 이 플래그에 따라 브로커를 고른다(`execution.engine.build_execution_engine`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from execution.models import Fill, Order, OrderStatus, OrderType

if TYPE_CHECKING:  # pragma: no cover - 타입 체크 전용(런타임 임포트 회피)
    from ccxt import Exchange

_logger = logging.getLogger(__name__)


class Broker(Protocol):
    """주문을 실행하고 체결(`Fill`)을 돌려주는 최소 인터페이스."""

    def place_order(self, order: Order, *, mark_price: float | None = None) -> Fill:
        """주문을 실행한다. `mark_price`는 시장가 시뮬레이션용 참조가(페이퍼)."""
        ...


class PaperBroker:
    """네트워크 없이 주문을 즉시 전량 체결로 시뮬레이션하는 드라이런 브로커.

    체결가는 지정가면 주문 가격, 시장가면 `place_order(mark_price=...)`로 받은
    참조가를 쓴다. 실행된 모든 주문을 `orders`에 기록해 검증에 쓴다.
    """

    def __init__(self, *, fee_rate: float = 0.0) -> None:
        self.fee_rate = fee_rate
        self.orders: list[Order] = []

    def place_order(self, order: Order, *, mark_price: float | None = None) -> Fill:
        self.orders.append(order)
        fill_price = order.price if order.type is OrderType.LIMIT else mark_price
        if fill_price is None or fill_price <= 0.0:
            _logger.warning("체결 참조가가 없어 페이퍼 주문 거부: %s", order.symbol)
            return Fill(
                order=order,
                status=OrderStatus.REJECTED,
                filled_quantity=0.0,
                average_price=0.0,
            )
        fee = fill_price * order.quantity * self.fee_rate
        _logger.info(
            "[페이퍼] %s %s %s qty=%s @ %s",
            order.symbol,
            order.side.value,
            order.type.value,
            order.quantity,
            fill_price,
        )
        return Fill(
            order=order,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            average_price=fill_price,
            fee=fee,
        )


class CcxtLiveBroker:
    """ccxt 기반 실거래 브로커. **명시적 `live_trading=True`가 없으면 생성 불가.**

    실계좌 연결·주문 검증은 WAN-27(테스트넷)에서 안전하게 다룬다. 이 클래스는 실주문
    경로를 구현해두되, 실행 엔진 팩토리는 `live_trading`이 꺼져 있으면 절대 이 브로커를
    만들지 않는다.
    """

    def __init__(self, exchange: Exchange, *, live_trading: bool) -> None:
        if not live_trading:
            raise RuntimeError(
                "CcxtLiveBroker는 live_trading=True일 때만 생성할 수 있습니다. "
                "실거래 활성화는 사용자 승인 후 수행하세요(WAN-27)."
            )
        self._exchange = exchange

    def place_order(self, order: Order, *, mark_price: float | None = None) -> Fill:
        params = {"reduceOnly": True} if order.reduce_only else {}
        raw = self._exchange.create_order(
            symbol=order.symbol,
            type=order.type.value,
            side=order.side.value,
            amount=order.quantity,
            price=order.price,
            params=params,
        )
        return _fill_from_ccxt(order, raw)


def _fill_from_ccxt(order: Order, raw: dict[str, object]) -> Fill:
    """ccxt `create_order` 응답을 `Fill`로 변환한다."""
    filled = _as_float(raw.get("filled"), default=0.0)
    average = _as_float(raw.get("average") or raw.get("price"), default=0.0)
    fee_info = raw.get("fee")
    fee = 0.0
    if isinstance(fee_info, dict):
        fee = _as_float(fee_info.get("cost"), default=0.0)

    if filled <= 0.0:
        status = OrderStatus.REJECTED
    elif filled < order.quantity:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.FILLED
    return Fill(
        order=order,
        status=status,
        filled_quantity=filled,
        average_price=average,
        fee=fee,
    )


def _as_float(value: object, *, default: float) -> float:
    """ccxt 응답의 느슨한 숫자 필드를 안전하게 float로 변환한다."""
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default
