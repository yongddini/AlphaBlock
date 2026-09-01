"""백테스트 엔진의 설정·거래·결과 값 객체.

`strategy.order_blocks`가 생성한 `OrderBlockSignal`을 입력으로, 과거 OHLCV에
대해 거래를 시뮬레이션한 결과를 담는 불변(frozen) 모델들이다. 모든 손익은
수수료·슬리피지가 반영된 순(net) 값이다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from common.costs import CostModel, Liquidity
from execution.sizing import PositionSizingParams


class PositionSide(StrEnum):
    """포지션 방향. 오더블록 방향(bull/bear)과 1:1로 대응."""

    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> float:
        """손익 계산 부호. 롱=+1, 숏=-1."""
        return 1.0 if self is PositionSide.LONG else -1.0


class ExitReason(StrEnum):
    """포지션(또는 일부)이 청산된 사유."""

    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    PARTIAL_TAKE_PROFIT = "partial_take_profit"
    END_OF_DATA = "end_of_data"


class BacktestConfig(BaseModel):
    """백테스트 파라미터. 재현을 위해 결과(`BacktestResult`)에 그대로 기록된다.

    비율(`*_pct`)은 진입 체결가 대비 분수이며, `fee_rate`/`slippage`는 각각
    한 방향(진입 또는 청산) 체결마다 적용된다. 슬리피지는 항상 불리한 방향으로
    적용된다(진입은 더 비싸게, 청산은 더 싸게 — 숏은 반대).
    """

    model_config = ConfigDict(frozen=True)

    initial_capital: float = Field(default=10_000.0, gt=0)
    fee_rate: float = Field(default=0.0004, ge=0)
    """테이커(시장가) 체결 노셔널 대비 수수료율. 예: 0.0004 = 0.04%."""
    maker_fee_rate: float | None = Field(default=0.0002, ge=0)
    """메이커(지정가) 수수료율. **기본 0.0002(2bp, WAN-95)**. None이면 `fee_rate`
    (테이커)와 동일하게 본다.

    지정가 진입(B안, `entry_liquidity=maker`)에 이 요율이 적용된다. 청산은 손절·익절
    도달 시 시장가 성격이라 항상 테이커(`fee_rate`)로 본다.

    WAN-95 이전 기본값은 `None`이라 **지정가 진입에도 테이커 4bp가 붙었다** — 채택
    기본값이 지정가(B안)로 바뀐 이상 이 폴백은 실제보다 비용을 2bp 과대 계상한다.
    2bp는 `common.costs.CostModel.maker_fee_rate`의 기존 기본값과 같은 값으로 맞춘
    것이다(두 곳이 서로 다른 상수를 들면 패리티 비교가 무의미해진다 — WAN-37).
    테이커 4bp→5bp 등 비용 가정 현실화는 WAN-92 범위이므로 여기서 건드리지 않는다.
    """
    slippage: float = Field(default=0.0005, ge=0)
    """테이커 체결가에 불리하게 적용되는 슬리피지 분수. 예: 0.0005 = 0.05%.

    메이커(지정가) 체결에는 적용되지 않는다(`entry_liquidity=maker`면 진입 슬리피지 0).
    """
    entry_liquidity: Liquidity = Field(default=Liquidity.TAKER)
    """🚨 **읽지 마십시오 — 아무도 읽지 않는 사문화된 필드다**(WAN-396).

    원래 뜻은 「진입 체결의 유동성 구분(WAN-37) — 시장가 진입(A안)=taker, 지정가
    진입(B안)=maker」였다. 그런데 **A안은 제거됐고**(WAN-200/215) 남은 B안 엔진은 이 값이
    아니라 **후보가 들고 오는 `_Candidate.entry_liquidity`**(기본 **메이커**)를 쓴다
    (`zone_limit_backtest._to_trade`). `harness`는 이 필드를 아무 데도 안 넘기므로 여기
    기본값 **테이커**가 그대로 남아 있고, 그래서 이 값을 읽는 코드는 **엔진이 실제로 한
    것과 반대**를 본다.

    실제로 그렇게 갈라진 적이 있다 — 비용 분해(WAN-370)가 이 필드를 읽어 붙지도 않은
    진입 슬리피지 5bp를 계상했고, `gross`와 `slippage`가 **같은 크기로 부풀어 상쇄되므로
    net R로는 안 보였다**(WAN-396 판정 (가)). 지금은 거래가 자기 값을 들고 다닌다
    (`Trade.entry_liquidity`) — **그쪽이 정본**이다.

    필드를 지우지 않는 이유는 `config_json` 실행 지문(WAN-106 `trade_store`)에 실려 있어
    지우면 적재된 실행의 `run_id`가 통째로 바뀌기 때문이다. 새 코드가 이 필드를 읽지
    않는지는 `tests/test_wan396_entry_liquidity.py`의 배선 가드가 **동작으로** 지킨다.

    ⚠️ **청산은 「항상 taker」가 아니다** — 사유별로 갈린다(`take_profit_liquidity` ·
    `exit_liquidity`, WAN-370).
    """
    take_profit_liquidity: Liquidity = Field(default=Liquidity.MAKER)
    """**익절**(부분 익절 포함) 청산 체결의 유동성 구분 — 채택 기본값 **maker**(WAN-370).

    익절은 목표가를 미리 아는 청산이라 실거래에서 **지정가 reduce-only**로 낸다
    (`docs/decisions/wan235.md` §3-2가 정한 집행). 그런데 백테스트 비용만 테이커로 남아
    있었다(같은 문서 §3-4가 「별도 재-베이스라인」으로 미뤄 둔 자리) — WAN-95가 **진입**
    쪽에서 한 교정("실매매가 지정가면 계산도 지정가")을 익절 쪽에서 뒤늦게 한 것이다.

    🚨 **근거는 정확성이지 수익이 아니다**(WAN-132가 진입가 정본을 옮길 때와 같은 성격).
    ⚠️ **체결 판정은 안 바뀐다** — 「닿으면 체결」 그대로라, 수수료는 지정가인데 체결은
    낙관인 상태다(사용자 결정 ①). 그 낙관은 `pen_5bp` 부류의 별도 축이다.

    손절·만료·데이터 종료는 **테이커 그대로**다(가격이 반대로 가서 시장가로 터는 청산).
    옛 동작(익절도 테이커)은 `harness.LEGACY_TAKE_PROFIT_LIQUIDITY`로 **명시 고정**한다.
    """
    position_fraction: float = Field(default=1.0, gt=0, le=1)
    """진입 시 노셔널로 사용할 자본(equity) 비율. `risk_sizing`이 None일 때만 쓰인다."""
    risk_sizing: PositionSizingParams | None = Field(default=None)
    """리스크 기반 포지션 사이징(WAN-26). None이면 `position_fraction` 고정 사이징을 쓴다.

    설정 시, 진입 수량을 손절 거리(오더블록 distal 경계 기준)에 반비례해 산출하고
    한도·최소단위로 clamp한다. 손절 거리가 최소치 미만이면 그 진입은 스킵된다.
    """
    stop_loss_pct: float | None = Field(default=None, gt=0)
    """진입가 대비 손절 폭. None이면 손절 없음."""
    take_profit_pct: float | None = Field(default=None, gt=0)
    """진입가 대비 익절 폭. None이면 익절 없음."""
    partial_take_profit_pct: float | None = Field(default=None, gt=0)
    """부분 익절 폭(전체 익절보다 앞선 목표). None이면 부분 익절 없음."""
    partial_exit_fraction: float = Field(default=0.5, gt=0, lt=1)
    """부분 익절 시 청산할 최초 수량 비율."""
    allow_long: bool = True
    allow_short: bool = True
    funding_enabled: bool = False
    """펀딩비(funding rate)를 손익에 반영할지 여부. 기본 False(기존 동작 보존).

    True이면 `run()`에 전달된 펀딩비 데이터로 보유 구간의 각 정산 시점마다
    `명목가치 × 요율 × 방향`을 손익에 가감한다.
    """
    funding_include_predicted: bool = False
    """예측(미정산) 펀딩비까지 반영할지 여부. 기본 False(확정값만 반영)."""
    funding_missing_policy: Literal["zero", "error"] = "zero"
    """`funding_enabled`인데 펀딩비 데이터가 전혀 없을 때의 정책.

    - ``"zero"``(기본): 펀딩비용을 0으로 두고 백테스트를 진행한다.
    - ``"error"``: 데이터 결측을 오류로 보고 `ValueError`를 발생시킨다.
    """
    annualization_factor: float | None = Field(default=None, gt=0)
    """샤프 지수 연율화 계수(연간 봉 수). None이면 봉 단위 샤프를 그대로 사용."""
    seed: int = 0
    """재현용 시드. 현재 엔진은 결정적이지만 기록·향후 확장을 위해 보존한다."""

    @model_validator(mode="after")
    def _validate(self) -> BacktestConfig:
        if not (self.allow_long or self.allow_short):
            raise ValueError("allow_long, allow_short 중 최소 하나는 True여야 합니다.")
        if (
            self.partial_take_profit_pct is not None
            and self.take_profit_pct is not None
            and self.partial_take_profit_pct >= self.take_profit_pct
        ):
            raise ValueError("partial_take_profit_pct는 take_profit_pct보다 작아야 합니다.")
        return self

    def exit_liquidity(self, reason: ExitReason) -> Liquidity:
        """청산 사유별 유동성 구분 — 이 저장소의 **단일 소스**(WAN-370).

        익절(부분 익절 포함)은 `take_profit_liquidity`, 나머지(손절·데이터 종료)는 항상
        테이커다. 🚨 **호출부가 「청산」을 한 덩어리로 취급하면 안 된다** — 사유를 안 보고
        `Liquidity.TAKER`를 그대로 넘기면 라벨만 바뀌고 실제로는 전부 테이커로 도는,
        이 저장소가 반복해 겪은 조용한 실패가 된다(WAN-91/95/112/123/159). 그래서 분기를
        여기 한 곳에만 두고 회귀 테스트가 **동작으로**(익절 거래와 손절 거래의 수수료가
        실제로 다른지) 고정한다.
        """
        if reason in (ExitReason.TAKE_PROFIT, ExitReason.PARTIAL_TAKE_PROFIT):
            return self.take_profit_liquidity
        return Liquidity.TAKER

    @property
    def cost_model(self) -> CostModel:
        """이 설정의 수수료·슬리피지를 공용 `CostModel`로 노출한다(WAN-37).

        `maker_fee_rate`가 None이면 테이커율과 같게 두어(보수적) 기존 동작을 보존한다.
        슬리피지 분수는 bps로 환산해 싣는다. 백테스트 엔진·B안 파이프라인·페이퍼가
        모두 이 모델을 통해 동일 산식으로 비용을 적용한다.
        """
        return CostModel(
            taker_fee_rate=self.fee_rate,
            maker_fee_rate=self.fee_rate if self.maker_fee_rate is None else self.maker_fee_rate,
            slippage_bps=self.slippage * 10_000.0,
        )

    @property
    def sizing_mode(self) -> str:
        """사이징 방식 라벨(WAN-65). `"risk_sizing"`(리스크 기반) 또는
        `"full_position"`(고정 비율 `position_fraction`).

        리포트 CSV/요약에 실어, 파일만 봐도 어떤 사이징으로 나온 성과 수치인지
        알 수 있게 한다.
        """
        return "risk_sizing" if self.risk_sizing is not None else "full_position"

    @property
    def risk_per_trade(self) -> float | None:
        """`risk_sizing`이 설정됐을 때의 거래당 리스크 비율. 없으면 None(WAN-65)."""
        return self.risk_sizing.risk_per_trade if self.risk_sizing is not None else None


class TradeFill(BaseModel):
    """포지션의 (부분) 청산 체결 하나."""

    model_config = ConfigDict(frozen=True)

    time: int
    """체결 봉의 `open_time`(ms)."""
    price: float
    """슬리피지가 반영된 체결가."""
    quantity: float
    fee: float
    reason: ExitReason


class Trade(BaseModel):
    """진입부터 완전 청산까지의 거래 하나. 부분 익절이 있으면 `exits`가 여럿."""

    model_config = ConfigDict(frozen=True)

    side: PositionSide
    entry_time: int
    entry_price: float
    """슬리피지가 반영된 진입 체결가."""
    quantity: float
    """진입 수량(부분 청산 전 전체)."""
    entry_fee: float
    entry_liquidity: Liquidity = Liquidity.MAKER
    """이 거래의 **진입** 체결 유동성 — 엔진이 실제로 쓴 값 그대로다 (WAN-396).

    `entry_price`(슬리피지 반영 체결가)와 `entry_fee`를 만들어 낸 바로 그 구분이라,
    사후에 비용을 분해하는 쪽은 **이 값을 봐야 한다**. 기본값은 `_Candidate.entry_liquidity`
    와 같은 **메이커**(B안 지정가 진입)라 예전과 비트 단위로 같다.

    🚨 **`BacktestConfig.entry_liquidity`를 보면 안 된다.** 그 필드의 기본값은 테이커인데
    B안 엔진은 후보의 값(기본 메이커)을 쓴다 — 두 곳이 갈라져 있던 것이 WAN-396이 잡은
    버그다(비용 분해가 붙지도 않은 진입 슬리피지 5bp를 계상했고, gross와 slippage가 같은
    크기로 부풀어 **상쇄되므로 net R로는 안 보였다**). 「라벨과 동작이 어긋남」
    (WAN-91/95/112/123/159/194)의 회계 축 변종이다.

    ⚠️ 손익 계산에는 쓰이지 않는다(체결가·수수료에 이미 녹아 있다) — 순수 기록이다."""
    exits: list[TradeFill]
    funding_cost: float = 0.0
    """보유 구간 누적 펀딩비용(양수=지불, 음수=수취). `realized_pnl`에 이미 반영됨."""
    realized_pnl: float
    """모든 수수료(진입·청산)와 펀딩비용을 차감·가감한 순손익."""
    return_pct: float
    """`realized_pnl`을 진입 노셔널로 나눈 수익률."""
    mfe_r: float | None = None
    """보유 구간의 최대유리이탈(MFE), R 단위(WAN-90). 1R = 진입가 → 무효화 경계.

    지정가(B안, `zone_limit`) 경로에서만 채워진다 — 봉 내부 경로를 1분봉 서브스텝으로
    아는 유일한 엔진이기 때문이다. 종가(A안) 엔진 거래는 None이다. 값의 정의·검열
    주의는 `backtest.substep.ZoneLimitOutcome.mfe_r` 참고. 비용·사이징과 무관한 순수
    관측 지표라 손익 계산에는 전혀 쓰이지 않는다(측정 전용, WAN-90).
    """
    mae_r: float | None = None
    """보유 구간의 최대불리이탈(MAE), R 단위(WAN-90). 정의는 위 `mfe_r`와 대칭."""

    @property
    def exit_time(self) -> int:
        return self.exits[-1].time

    @property
    def is_win(self) -> bool:
        return self.realized_pnl > 0


class EquityPoint(BaseModel):
    """봉 종가 시점의 계좌 평가금(현금 + 미실현손익)."""

    model_config = ConfigDict(frozen=True)

    time: int
    equity: float


class BacktestMetrics(BaseModel):
    """백테스트 성과 지표."""

    model_config = ConfigDict(frozen=True)

    initial_capital: float
    final_equity: float
    total_return: float
    max_drawdown: float
    """최대 낙폭(양수 분수). 예: 0.25 = 고점 대비 25% 하락."""
    win_rate: float
    profit_factor: float | None
    """총이익 / 총손실. 손실이 없으면 None."""
    sharpe: float | None
    """봉 수익률 기반 샤프 지수. 표준편차가 0이거나 표본 부족이면 None."""
    num_trades: int
    num_wins: int
    num_losses: int
    gross_profit: float
    gross_loss: float
    avg_win: float
    avg_loss: float
    total_funding_cost: float = 0.0
    """모든 거래에 걸친 누적 펀딩비용(양수=순지불, 음수=순수취)."""
    funding_coverage: float | None = None
    """백테스트 구간의 펀딩 데이터 커버리지 비율(0.0~1.0). 펀딩 미사용이면 None.

    1.0 미만이면 결측 구간의 펀딩비를 0으로 때웠다는 뜻이므로 비용이 **과소 계상**됐을
    수 있다(WAN-63의 조용한 실패 신호). 리포트가 이 값을 노출하고 1.0 미만이면 경고한다.
    """


class BacktestResult(BaseModel):
    """`BacktestEngine.run()`의 반환값. 파라미터·거래·자본곡선·지표를 모두 포함."""

    model_config = ConfigDict(frozen=True)

    config: BacktestConfig
    trades: list[Trade]
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics
