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

from common.costs import CostModel, Liquidity
from execution.models import Fill, Order, OrderStatus, OrderType

if TYPE_CHECKING:  # pragma: no cover - 타입 체크 전용(런타임 임포트 회피)
    from ccxt import Exchange

    from config.settings import Settings

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

    ## 수수료 (WAN-37)

    `cost_model`(공용 `CostModel`)을 주면 시장가=테이커, 지정가=메이커로 수수료를
    구분해 부과한다(엔진의 운영 자본 정산이 현실적이도록). 없으면 레거시 `fee_rate`
    (플랫)만 쓴다. **슬리피지는 여기서 체결가에 넣지 않는다** — 페이퍼 성과(paper_trades)
    의 권위 있는 비용 산정은 `paper.store.build_record`가 원(raw) 참조가에 공용
    `CostModel`을 적용해 백테스트와 동일 산식으로 수행하므로, 브로커가 체결가를 미리
    미끄러뜨리면 이중 반영이 된다. 그래서 브로커는 체결가를 참조가 그대로 두고
    수수료만 모델링한다.
    """

    def __init__(
        self,
        *,
        fee_rate: float = 0.0,
        cost_model: CostModel | None = None,
    ) -> None:
        self.fee_rate = fee_rate
        self.cost_model = cost_model
        self.orders: list[Order] = []

    def _fee(self, notional: float, order_type: OrderType) -> float:
        """체결 수수료. `cost_model`이 있으면 메이커/테이커 구분, 없으면 플랫 요율."""
        if self.cost_model is None:
            return notional * self.fee_rate
        liquidity = Liquidity.MAKER if order_type is OrderType.LIMIT else Liquidity.TAKER
        return self.cost_model.fee(notional, liquidity)

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
        fee = self._fee(fill_price * order.quantity, order.type)
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

    @property
    def exchange(self) -> Exchange:
        """포지션·잔고 조회, 주문 취소 등 검증에 쓰는 하부 ccxt 인스턴스."""
        return self._exchange

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


def build_live_broker(
    settings: Settings,
    *,
    exchange: Exchange | None = None,
) -> CcxtLiveBroker:
    """설정에서 실거래/테스트넷 브로커를 만든다(WAN-27).

    안전 가드: `live_trading=True`가 아니면 만들지 않는다. `exchange`를 주지 않으면
    `create_exchange(settings)`로 인스턴스를 만드는데, `use_testnet=True`면 그 경로가
    자동으로 sandbox(테스트넷) 모드 + 테스트넷 키를 쓴다. 이 팩토리는 데이터 수집용
    거래소 생성 로직을 그대로 재사용해 실주문 브로커에 배선한다(주문 로직 중복 없음).
    """
    if not settings.live_trading:
        raise RuntimeError(
            "live_trading=True일 때만 실거래/테스트넷 브로커를 만듭니다(WAN-27). "
            "기본값은 페이퍼(드라이런)이며, 실주문 경로는 명시적 승인이 필요합니다."
        )
    if exchange is None:
        # 런타임 순환 임포트 회피를 위해 지연 임포트.
        from data.exchange import create_exchange

        exchange = create_exchange(settings)
    return CcxtLiveBroker(exchange, live_trading=settings.live_trading)


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
