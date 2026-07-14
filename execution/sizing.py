"""리스크 기반 포지션 사이징 (WAN-26).

손절이 "오더블록 무효화"라 **손절 거리가 매 진입마다 다르다.** 고정 수량 대신
거래당 리스크 비율(`risk_per_trade`)로 수량을 역산해, 손절에 걸리면 어떤 진입이든
거의 동일한 금액만 잃도록 만든다. 백테스트(WAN-8)와 주문 실행(WAN-9)이 이 순수
함수를 공용으로 쓴다.

## 규칙

* 거래당 리스크 금액 = `equity × risk_per_trade`.
* 손절 거리 = ``|entry_price − stop_price|`` (오더블록 distal 경계 기준, WAN-23 규칙).
* 수량 = 리스크 금액 / 손절 거리 → 손절 거리에 **반비례**.
* 상한: 명목가치(수량 × 진입가)를 레버리지·정책 한도로 clamp.
* 최소 주문 단위(`qty_step`)로 내림하고, `min_qty` 미만이면 진입하지 않는다(0 반환).
* 손절 거리가 0에 가깝거나(`min_stop_distance_fraction` 미만) 자본이 없으면 진입
  스킵(0 반환).

`position_size()`는 부작용 없는 순수 함수이며, 실주문·`live_trading`은 여기서
건드리지 않는다.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field


class PositionSizingParams(BaseModel):
    """리스크 기반 사이징 파라미터. 백테스트·실행이 공유한다."""

    model_config = ConfigDict(frozen=True)

    risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    """거래당 리스크 = 자본 × 이 비율. 예: 0.01 = 1%."""
    leverage: float = Field(default=1.0, gt=0)
    """레버리지 상한. 명목가치는 `자본 × leverage`를 넘지 못한다."""
    max_notional_fraction: float | None = Field(default=None, gt=0)
    """추가 명목가치 상한 = `자본 × 이 값`. None이면 `leverage`만 상한으로 쓴다.
    설정 시 `leverage`와 함께 더 작은 쪽이 실제 상한이 된다."""
    qty_step: float = Field(default=0.0, ge=0)
    """최소 주문 수량 단위(lot). 0이면 반올림하지 않는다. 산출 수량을 이 배수로 내림."""
    min_qty: float = Field(default=0.0, ge=0)
    """최소 주문 수량. 내림 후 이 값 미만이면 진입 스킵(0 반환)."""
    min_stop_distance_fraction: float = Field(default=0.003, ge=0, lt=1)
    """진입가 대비 최소 손절 거리(분수). 손절이 이보다 가까우면 진입 스킵(0 반환).

    기본값 `0.003`(0.3%)은 신뢰성 가드다(WAN-79, WAN-76 감사 권고). 왕복 체결 비용
    ≈0.11%(슬리피지 5bps + 테이커 4bps + 메이커 2bps, `common.costs`) 대비 약 3배
    여유를 둬, 손절폭이 체결 비용에 묻히는 극단 근접 손절 거래를 사이징에서 배제한다.
    `0.0`으로 두면 하한이 꺼진다(과거 동작)."""


def position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    params: PositionSizingParams,
) -> float:
    """손절 거리에 반비례하는 진입 수량을 산출한다.

    Args:
        equity: 현재 계좌 자본. 0 이하이면 0을 반환한다.
        entry_price: 진입(체결) 가격. 반드시 양수.
        stop_price: 손절 참조가(오더블록 distal 경계). 진입가와의 절대 거리를 리스크로 본다.
        params: 사이징 파라미터.

    Returns:
        진입 수량. 진입을 스킵해야 하면(손절 거리 과소·자본 없음·최소 수량 미달) 0.0.

    Raises:
        ValueError: `entry_price`가 양수가 아닐 때.
    """
    if entry_price <= 0:
        raise ValueError("entry_price는 양수여야 합니다.")
    if equity <= 0:
        return 0.0

    stop_distance = abs(entry_price - stop_price)
    min_distance = params.min_stop_distance_fraction * entry_price
    if stop_distance <= 0.0 or stop_distance < min_distance:
        return 0.0

    risk_amount = equity * params.risk_per_trade
    qty = risk_amount / stop_distance

    # 명목가치 상한(레버리지·정책)으로 clamp.
    max_notional = equity * params.leverage
    if params.max_notional_fraction is not None:
        max_notional = min(max_notional, equity * params.max_notional_fraction)
    max_qty = max_notional / entry_price
    qty = min(qty, max_qty)

    # 최소 주문 단위로 내림.
    if params.qty_step > 0:
        qty = math.floor(qty / params.qty_step) * params.qty_step

    if qty <= 0.0 or qty < params.min_qty:
        return 0.0
    return qty
