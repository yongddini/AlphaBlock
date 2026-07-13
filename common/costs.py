"""체결 비용 모델 — 백테스트·페이퍼 공용 단일 소스 (WAN-37).

메이커/테이커 수수료와 슬리피지를 한 곳에서 정의해, 백테스트(`backtest.engine`,
`backtest.zone_limit_backtest`)와 페이퍼(`paper.store`)가 **같은 비용 파라미터·같은
산식**을 쓰게 한다. 두 경로가 서로 다른 상수를 들고 있으면 패리티(`paper.parity`)
비교가 무의미해지므로, 비용은 이 모듈의 `CostModel` 하나로 흐른다.

## 진입 방식별 비대칭 (A안 vs B안)

- **시장가(테이커) 진입(A안)**: 테이커 수수료 + 슬리피지가 붙는다.
- **지정가(메이커) 체결(B안)**: 메이커 수수료가 붙고, **슬리피지는 원칙적으로 없다**
  (지정가는 가격을 지정하므로 불리한 미끄러짐이 없음 — 대신 미체결 위험은 별도로
  `fill_rate`로 추적한다). 청산은 손절·익절 도달 시 시장가 성격이라 테이커로 본다.

`Liquidity`(maker/taker)로 이 비대칭을 표현하고, `CostModel.entry_fill` /
`exit_fill` / `fee` / `trade_costs`가 이를 일관되게 적용한다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Liquidity(StrEnum):
    """체결이 유동성을 **제공**(maker)했는지 **소비**(taker)했는지."""

    MAKER = "maker"
    TAKER = "taker"


class CostBreakdown(BaseModel):
    """한 거래의 비용 분해(모두 **진입 원(raw) 노셔널** 대비 분수).

    `gross_frac`는 슬리피지 미반영 원가격 손익률, `slippage_frac`·`fee_frac`는 각각
    슬리피지·왕복 수수료 비용률(항상 ≥0), `net_frac = gross − slippage − fee`이다.
    펀딩비용은 이 분해에 포함하지 않는다(보유 구간 데이터가 필요하므로 호출부에서
    별도 가감한다).
    """

    model_config = ConfigDict(frozen=True)

    gross_frac: float
    """슬리피지 미반영 가격 손익률(방향 반영). 롱은 상승이 +, 숏은 하락이 +."""
    slippage_frac: float
    """슬리피지 비용률(≥0). 진입·청산 체결가가 불리하게 미끄러진 만큼."""
    fee_frac: float
    """왕복(진입+청산) 수수료 비용률(≥0)."""
    net_frac: float
    """모든 비용(펀딩 제외)을 반영한 순손익률 = gross − slippage − fee."""


class CostModel(BaseModel):
    """메이커/테이커 수수료 + 슬리피지의 단일 소스.

    수수료율은 한 방향(진입 또는 청산) 체결 노셔널 대비 분수다(예: 0.0004 = 0.04%).
    슬리피지는 **테이커 체결에만** 불리하게 적용되며(메이커=지정가는 0), bps로 지정한다
    (예: 5.0 bps = 0.05%). 값 객체(frozen)라 설정·결과에 그대로 실어 재현할 수 있다.
    """

    model_config = ConfigDict(frozen=True)

    taker_fee_rate: float = Field(default=0.0004, ge=0)
    """테이커(시장가) 수수료율. 예: 0.0004 = 0.04%."""
    maker_fee_rate: float = Field(default=0.0002, ge=0)
    """메이커(지정가) 수수료율. 예: 0.0002 = 0.02%."""
    slippage_bps: float = Field(default=5.0, ge=0)
    """테이커 체결에 불리하게 적용되는 슬리피지(bps). 메이커에는 적용 안 함."""

    @property
    def slippage_fraction(self) -> float:
        """테이커 슬리피지 분수(bps → 분수). 예: 5.0 bps → 0.0005."""
        return self.slippage_bps / 10_000.0

    def fee_rate(self, liquidity: Liquidity) -> float:
        """체결 유동성 구분에 맞는 수수료율."""
        return self.taker_fee_rate if liquidity is Liquidity.TAKER else self.maker_fee_rate

    def slippage_for(self, liquidity: Liquidity) -> float:
        """체결 유동성 구분에 맞는 슬리피지 분수. 메이커는 0."""
        return self.slippage_fraction if liquidity is Liquidity.TAKER else 0.0

    def fee(self, notional: float, liquidity: Liquidity) -> float:
        """체결 노셔널에 대한 수수료(비음수)."""
        return abs(notional) * self.fee_rate(liquidity)

    def entry_fill(self, price: float, *, is_long: bool, liquidity: Liquidity) -> float:
        """진입 참조가에 슬리피지를 **불리하게** 적용한 체결가.

        롱은 더 비싸게, 숏은 더 싸게 체결된다(메이커는 슬리피지 0이라 참조가 그대로).
        """
        slip = self.slippage_for(liquidity)
        return price * (1.0 + slip) if is_long else price * (1.0 - slip)

    def exit_fill(self, price: float, *, is_long: bool, liquidity: Liquidity) -> float:
        """청산 참조가에 슬리피지를 **불리하게** 적용한 체결가.

        롱은 더 싸게, 숏은 더 비싸게 체결된다(메이커는 슬리피지 0).
        """
        slip = self.slippage_for(liquidity)
        return price * (1.0 - slip) if is_long else price * (1.0 + slip)

    def trade_costs(
        self,
        entry_ref: float,
        exit_ref: float,
        *,
        is_long: bool,
        entry_liquidity: Liquidity,
        exit_liquidity: Liquidity,
    ) -> CostBreakdown:
        """진입·청산 **참조가**(슬리피지 미반영)로부터 비용을 분해한다.

        반환 분수는 모두 진입 원 노셔널(`entry_ref`) 대비다. 수량이 상쇄되므로 단위
        수량 기준으로 계산하며, `net_frac × 진입노셔널`은 백테스트 엔진이 산출하는
        실현손익(펀딩 제외)과 정확히 일치한다 — 그래서 페이퍼와 백테스트가 동일
        진입/청산에 대해 같은 순손익을 낸다.
        """
        if entry_ref <= 0.0:
            return CostBreakdown(gross_frac=0.0, slippage_frac=0.0, fee_frac=0.0, net_frac=0.0)
        sign = 1.0 if is_long else -1.0
        entry_price = self.entry_fill(entry_ref, is_long=is_long, liquidity=entry_liquidity)
        exit_price = self.exit_fill(exit_ref, is_long=is_long, liquidity=exit_liquidity)
        gross = sign * (exit_ref - entry_ref) / entry_ref
        fill_move = sign * (exit_price - entry_price) / entry_ref
        slippage = gross - fill_move  # = (진입·청산 미끄러짐)/진입참조가 ≥ 0
        fee = (
            entry_price * self.fee_rate(entry_liquidity)
            + exit_price * self.fee_rate(exit_liquidity)
        ) / entry_ref
        net = gross - slippage - fee
        return CostBreakdown(gross_frac=gross, slippage_frac=slippage, fee_frac=fee, net_frac=net)
