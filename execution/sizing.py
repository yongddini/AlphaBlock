"""리스크 기반 포지션 사이징 (WAN-26).

손절이 "오더블록 무효화"라 **손절 거리가 매 진입마다 다르다.** 고정 수량 대신
거래당 리스크 비율(`risk_per_trade`)로 수량을 역산해, 손절에 걸리면 어떤 진입이든
거의 동일한 금액만 잃도록 만든다. 백테스트(WAN-8)와 주문 실행(WAN-9)이 이 순수
함수를 공용으로 쓴다.

## 규칙

* 거래당 리스크 금액 = `equity × risk_per_trade`.
* 손절 거리 = ``|entry_price − stop_price|`` (오더블록 distal 경계 기준, WAN-23 규칙).
* 수량 = 리스크 금액 / 손절 거리 → 손절 거리에 **반비례**.
* 상한: 명목가치(수량 × 진입가)를 레버리지·정책 한도로 clamp. 여러 포지션을 동시에
  들면 그 한도는 **포트폴리오 전체**의 것이다(`open_notional`, WAN-103).
* 최소 주문 단위(`qty_step`)로 내림하고, `min_qty` 미만이면 진입하지 않는다(0 반환).
* 손절 거리가 0에 가깝거나(`min_stop_distance_fraction` 미만) 자본이 없으면 진입
  스킵(0 반환).

## 사이징 모드 두 가지 (WAN-108)

위 규칙은 **`sizing_mode="risk_pct"`**(기본값, 채택 경로)의 것이다. WAN-108이 사용자의
실제 모델("1/N 시드 × N배")을 재려고 **`"fixed_notional"`** 을 추가했다: 명목가치를 손절
거리와 **무관하게** `equity × notional_fraction`으로 고정한다.

둘의 차이는 **손절 시 손실의 성격**이다. `risk_pct`는 손실을 자본의 `risk_per_trade`로
**고정**하고 명목을 그 결과로 낸다(손절이 멀면 작게 산다). `fixed_notional`은 명목을
고정하고 손실을 그 결과로 낸다 — 그래서 **손절이 먼 자리가 손실을 지배하고 상한이
없다**(청산이 실재한다). `risk_pct`에서 레버리지가 사실상 명목 천장일 뿐이었던 것과 달리
(`docs/decisions/wan103.md` §5), `fixed_notional`에서는 레버리지가 **위험 배수**다.

`position_size()`는 부작용 없는 순수 함수이며, 실주문·`live_trading`은 여기서
건드리지 않는다.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SizingMode = Literal["risk_pct", "fixed_notional"]
"""포지션 크기를 무엇으로 정하는가 (WAN-108).

* `risk_pct` — **손절 거리에서 역산**(거래당 리스크 = 자본 × `risk_per_trade`). 채택
  기본값이며 WAN-26 이래의 유일한 모드였다.
* `fixed_notional` — **명목 고정**(명목 = 자본 × `notional_fraction`). 손절 거리를 보지
  않으므로 손절 시 손실이 자리마다 다르고 상한이 없다.
"""


class PositionSizingParams(BaseModel):
    """포지션 사이징 파라미터. 백테스트·실행이 공유한다."""

    model_config = ConfigDict(frozen=True)

    sizing_mode: SizingMode = "risk_pct"
    """수량을 정하는 방식(WAN-108). 기본값은 채택 경로인 리스크 기반 역산이다.

    `"fixed_notional"`로 바꾸면 `risk_per_trade`는 **읽히지 않고** `notional_fraction`이
    그 자리를 대신한다. 두 필드가 동시에 쓰이는 조합은 없다 — 한 모드에서 다른 모드의
    노브를 조용히 무시하는 대신, 이 필드가 어느 쪽이 산 결과인지 CSV에 남게 한다.
    """
    risk_per_trade: float = Field(default=0.01, gt=0, le=1)
    """거래당 리스크 = 자본 × 이 비율. 예: 0.01 = 1%. `sizing_mode="risk_pct"` 전용."""
    notional_fraction: float = Field(default=1.0, gt=0)
    """포지션 명목 = 자본 × 이 비율. `sizing_mode="fixed_notional"` 전용(WAN-108의 `f`).

    `1.0`이면 자리마다 **시드 전액**을 명목으로 잡는다 — 동시 N자리면 총 명목이 자본의
    N배가 되고, 그 N이 곧 유효 레버리지다(`leverage` 천장이 N을 제한한다).
    """
    leverage: float = Field(default=1.0, gt=0)
    """레버리지 상한. **열린 포지션 전체의** 명목가치 합이 `자본 × leverage`를 넘지 못한다.

    동시 1포지션이던 시절(WAN-26~102)엔 "이 거래 하나의 명목 ≤ 자본 × leverage"와 같은
    뜻이었다. WAN-103이 동시 다중 포지션을 열면서 이 한도는 **포트폴리오 전체**의 것으로
    승격됐다 — `position_size(open_notional=...)`에 이미 열린 명목을 넘기면 남은 여유분만
    새 포지션에 배정한다. `open_notional=0.0`(기본)이면 정확히 예전 per-trade clamp다.
    """
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
    open_notional: float = 0.0,
) -> float:
    """진입 수량을 산출한다 — `params.sizing_mode`에 따라 손절 역산 또는 명목 고정.

    Args:
        equity: 현재 계좌 자본. 0 이하이면 0을 반환한다.
        entry_price: 진입(체결) 가격. 반드시 양수.
        stop_price: 손절 참조가(오더블록 distal 경계). `risk_pct` 모드에서는 진입가와의
            절대 거리가 곧 리스크 단위이고, `fixed_notional` 모드에서는 수량에 영향을
            주지 않는다 — 다만 `min_stop_distance_fraction` 가드는 **두 모드 모두**에
            적용된다(아래 참고).
        params: 사이징 파라미터.
        open_notional: 이미 열려 있는 포지션들의 명목가치 합(WAN-103). 명목 상한은
            포트폴리오 전체에 걸리므로, 이 값을 뺀 **남은 여유분**만 새 포지션에
            배정한다. 여유가 없으면(상한 소진) 0을 반환해 진입을 스킵한다. 기본
            `0.0`이면 동시 1포지션 시절과 동일한 per-trade clamp가 된다.

    Returns:
        진입 수량. 진입을 스킵해야 하면(손절 거리 과소·자본 없음·명목 상한 소진·
        최소 수량 미달) 0.0.

    Raises:
        ValueError: `entry_price`가 양수가 아니거나 `open_notional`이 음수일 때.
    """
    if entry_price <= 0:
        raise ValueError("entry_price는 양수여야 합니다.")
    if open_notional < 0:
        raise ValueError("open_notional은 음수일 수 없습니다.")
    if equity <= 0:
        return 0.0

    # ⚠️ 손절 거리 가드는 `fixed_notional`에도 건다. 그 모드에서 손절 거리는 수량을 정하지
    # 않지만, 이 가드가 거르는 건 **사이징이 아니라 셋업**이다(손절폭이 체결 비용에 묻히는
    # 극단 근접 손절). 모드마다 다른 셋업을 받으면 WAN-108의 사이징 대조표에서 두 열의
    # 차이가 "사이징 효과 + 셋업 풀 차이"가 돼 축이 오염된다.
    stop_distance = abs(entry_price - stop_price)
    min_distance = params.min_stop_distance_fraction * entry_price
    if stop_distance <= 0.0 or stop_distance < min_distance:
        return 0.0

    if params.sizing_mode == "fixed_notional":
        # 명목 고정: 손절 거리를 보지 않는다. 손절 시 손실 = 명목 × 손절거리라 상한이 없다.
        qty = (equity * params.notional_fraction) / entry_price
    else:
        risk_amount = equity * params.risk_per_trade
        qty = risk_amount / stop_distance

    # 명목가치 상한(레버리지·정책)으로 clamp. 상한은 포트폴리오 전체에 걸리므로 이미 열린
    # 명목을 빼고 남은 여유분이 이 진입의 천장이다(WAN-103).
    max_notional = equity * params.leverage
    if params.max_notional_fraction is not None:
        max_notional = min(max_notional, equity * params.max_notional_fraction)
    remaining = max_notional - open_notional
    if remaining <= 0.0:
        return 0.0
    max_qty = remaining / entry_price
    qty = min(qty, max_qty)

    # 최소 주문 단위로 내림.
    if params.qty_step > 0:
        qty = math.floor(qty / params.qty_step) * params.qty_step

    if qty <= 0.0 or qty < params.min_qty:
        return 0.0
    return qty
