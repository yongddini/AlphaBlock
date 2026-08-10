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
  들면 그 한도는 **포트폴리오 전체**의 것이다(`open_notional`, WAN-103). 옵트인으로
  일거래량(ADV) 비례 **절대** 명목 상한도 얹을 수 있다(`max_notional_adv_fraction`,
  WAN-244 — 자본에 안 비례해 복리 착시를 깬다).
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

SizingRejectReason = Literal[
    "ok",
    "no_equity",
    "stop_too_tight",
    "notional_exhausted",
    "capacity_cap",
    "below_min_qty",
]
"""사이징이 왜 그 수량을 냈는지 (WAN-275).

`position_size`는 **서로 다른 이유로 0.0을 반환**하는데(손절폭 가드·명목 상한 소진·
용량 상한·자본 부족), 예전엔 호출부가 그걸 전부 "수량 0"으로 뭉갰다. `size_with_reason`이
그 바닥 이유를 함께 돌려줘 진입 거부 사유를 **증상**이 아니라 **원인**으로 표기하게 한다.

* `ok` — 진입 가능한 양수 수량이 나왔다.
* `no_equity` — 자본이 0 이하다(진입 불가).
* `stop_too_tight` — 손절 거리가 0 이하이거나 `min_stop_distance_fraction`(0.3% 하한,
  WAN-79) 미만이다. LINK 15m 같은 극단 근접 손절이 여기 걸린다.
* `notional_exhausted` — 남은 명목 여유(레버리지·`max_notional_fraction` 상한 − 열린 명목)가
  0 이하다(WAN-103).
* `capacity_cap` — 용량 상한(ADV 비례 절대 명목 상한, WAN-244)이 명목을 0으로 clamp했다.
* `below_min_qty` — 내림·최소 주문 수량(`qty_step`·`min_qty`)에 걸려 0이 됐다.

⚠️ **라벨일 뿐 수량 계산 로직·값은 불변이다** — `position_size`는 이 함수의 첫 성분만
꺼내 쓰므로 반환 수량이 비트 단위로 같다(회귀 테스트가 동작으로 고정)."""


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
    max_notional_adv_fraction: float | None = Field(default=None, gt=0)
    """용량 상한 = `이 값 × ADV_usd`(일거래량 비례 **절대(달러)** 명목 상한, WAN-244, 옵트인).

    ⚠️ **위 두 상한(`leverage`·`max_notional_fraction`)과 성격이 다르다** — 그 둘은 자본에
    비례해 커지지만 이 항은 **자본과 무관한 절대 달러 상한**이다(`ADV_usd`는 시장 유동성이지
    내 계좌가 아니다). 그래서 자본이 수백만 배로 커져도 포지션은 시장 용량에 걸려 잘린다 —
    복리 착시를 깨는 것이 이 항의 유일한 목적이다(WAN-90 「레버리지는 위험의 모양만 바꾼다」).

    `None`(기본)이면 이 상한을 쓰지 않는다 — 그때 `position_size(adv_usd=...)`는 무시되고
    실행이 예전과 비트 단위로 같다. 설정하면 `position_size`에 넘어온 `adv_usd`가 있을 때만
    발동한다(`adv_usd`가 `None`이면 = ADV 정보 없음(워밍업 등) → 이 항은 걸지 않는다).

    단위는 **ADV 대비 분수**다(예: `0.005` = 0.5% ADV). 자본 비율이 아니다."""
    adv_window_days: int = Field(default=30, gt=0)
    """`max_notional_adv_fraction`이 쓰는 ADV(평균 일 달러거래량)의 트레일링 창(일).

    ADV는 탭 봉 **직전까지 완료된** 일자들에서만 잰다(룩어헤드 금지) — 산출은 후보 생성
    (`backtest.zone_limit_backtest.build_zone_limit_candidates`) 쪽이 이 값으로 하고, 여기
    사이징 함수는 이미 산출된 `adv_usd`를 상한으로 곱하기만 한다. `max_notional_adv_fraction`이
    `None`이면 이 필드는 읽히지 않는다."""
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


def size_with_reason(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    params: PositionSizingParams,
    open_notional: float = 0.0,
    adv_usd: float | None = None,
) -> tuple[float, SizingRejectReason]:
    """진입 수량과 **왜 그 수량이 나왔는지**를 함께 산출한다 (WAN-275).

    `position_size`와 계산 로직·값이 **동일**하고, 다른 점은 0을 반환한 바닥 이유를
    `SizingRejectReason`으로 함께 돌려주는 것뿐이다. 호출부(`execution.engine`)가 진입
    거부 사유를 "수량 0"이라는 증상 대신 구체 가드(손절폭 하한 미달·명목 상한 소진·
    용량 상한·자본 부족)로 표기하는 데 쓴다.

    Args:
        equity: 현재 계좌 자본. 0 이하이면 `(0.0, "no_equity")`.
        entry_price: 진입(체결) 가격. 반드시 양수.
        stop_price: 손절 참조가(오더블록 distal 경계). `risk_pct` 모드에서는 진입가와의
            절대 거리가 곧 리스크 단위이고, `fixed_notional` 모드에서는 수량에 영향을
            주지 않는다 — 다만 `min_stop_distance_fraction` 가드는 **두 모드 모두**에
            적용된다(아래 참고).
        params: 사이징 파라미터.
        open_notional: 이미 열려 있는 포지션들의 명목가치 합(WAN-103). 명목 상한은
            포트폴리오 전체에 걸리므로, 이 값을 뺀 **남은 여유분**만 새 포지션에
            배정한다. 여유가 없으면(상한 소진) `(0.0, "notional_exhausted")`. 기본
            `0.0`이면 동시 1포지션 시절과 동일한 per-trade clamp가 된다.
        adv_usd: 이 진입 시점의 평균 일 달러거래량(ADV, USD, WAN-244). 용량 상한
            (`params.max_notional_adv_fraction`)이 설정됐을 때만 쓰인다 — 이 포지션의
            명목을 `max_notional_adv_fraction × adv_usd`로 clamp한다(자본과 무관한 **절대**
            상한이라 복리 착시를 깬다). `None`이면(기본, 또는 ADV 정보 없음) 용량 상한을
            걸지 않는다 — 그러면 실행이 이 항을 넣기 전과 비트 단위로 같다.

    Returns:
        `(수량, 사유)`. 수량이 양수면 사유는 `"ok"`. 진입을 스킵해야 하면 0.0과 함께
        그 이유(`SizingRejectReason`)를 돌려준다.

    Raises:
        ValueError: `entry_price`가 양수가 아니거나 `open_notional`이 음수일 때.
    """
    if entry_price <= 0:
        raise ValueError("entry_price는 양수여야 합니다.")
    if open_notional < 0:
        raise ValueError("open_notional은 음수일 수 없습니다.")
    if equity <= 0:
        return 0.0, "no_equity"

    # ⚠️ 손절 거리 가드는 `fixed_notional`에도 건다. 그 모드에서 손절 거리는 수량을 정하지
    # 않지만, 이 가드가 거르는 건 **사이징이 아니라 셋업**이다(손절폭이 체결 비용에 묻히는
    # 극단 근접 손절). 모드마다 다른 셋업을 받으면 WAN-108의 사이징 대조표에서 두 열의
    # 차이가 "사이징 효과 + 셋업 풀 차이"가 돼 축이 오염된다.
    stop_distance = abs(entry_price - stop_price)
    min_distance = params.min_stop_distance_fraction * entry_price
    if stop_distance <= 0.0 or stop_distance < min_distance:
        return 0.0, "stop_too_tight"

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
        return 0.0, "notional_exhausted"
    max_qty = remaining / entry_price
    qty = min(qty, max_qty)

    # 용량 상한(WAN-244, 옵트인): 포지션 명목 ≤ `max_notional_adv_fraction × ADV_usd`.
    # 위 clamp들과 달리 이 상한은 **자본에 비례하지 않는 절대 달러 값**이라(ADV = 시장
    # 유동성) 자본이 커져도 포지션이 시장 용량에 걸려 잘린다 — 복리 착시를 깨는 항이다.
    # 포지션당 상한이므로 `open_notional`(포트폴리오 여유)과 무관하게 이 진입 하나에 건다.
    # `adv_usd`가 없으면(워밍업 등 ADV 정보 부재) 걸지 않는다 — 조용히 0으로 만들지 않는다.
    if params.max_notional_adv_fraction is not None and adv_usd is not None:
        adv_notional_cap = params.max_notional_adv_fraction * adv_usd
        qty = min(qty, adv_notional_cap / entry_price)
        # 용량 상한이 명목을 0으로 clamp했다(ADV 0 등). 아래 최종 검사가 어차피 0으로
        # 거르지만, 여기서 사유를 확정해야 "최소 수량 미달"과 구분된다. 반환 수량은 동일.
        if qty <= 0.0:
            return 0.0, "capacity_cap"

    # 최소 주문 단위로 내림.
    if params.qty_step > 0:
        qty = math.floor(qty / params.qty_step) * params.qty_step

    if qty <= 0.0 or qty < params.min_qty:
        return 0.0, "below_min_qty"
    return qty, "ok"


def position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    params: PositionSizingParams,
    open_notional: float = 0.0,
    adv_usd: float | None = None,
) -> float:
    """진입 수량을 산출한다 — `params.sizing_mode`에 따라 손절 역산 또는 명목 고정.

    `size_with_reason`의 수량 성분만 꺼내는 얇은 래퍼다(WAN-275). 반환 수량은 이 함수가
    독립적으로 계산하던 시절과 **비트 단위로 동일**하다 — 사유가 필요하면(진입 거부
    표기 등) `size_with_reason`을 직접 쓴다.

    Args/Returns/Raises: `size_with_reason` 참고. 반환은 수량(float) 하나뿐.
    """
    qty, _reason = size_with_reason(
        equity=equity,
        entry_price=entry_price,
        stop_price=stop_price,
        params=params,
        open_notional=open_notional,
        adv_usd=adv_usd,
    )
    return qty
