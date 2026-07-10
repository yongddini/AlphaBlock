"""백테스트 엔진의 설정·거래·결과 값 객체.

`strategy.order_blocks`가 생성한 `OrderBlockSignal`을 입력으로, 과거 OHLCV에
대해 거래를 시뮬레이션한 결과를 담는 불변(frozen) 모델들이다. 모든 손익은
수수료·슬리피지가 반영된 순(net) 값이다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    """체결 노셔널 대비 수수료율(한 방향). 예: 0.0004 = 0.04%."""
    slippage: float = Field(default=0.0005, ge=0)
    """체결가에 불리하게 적용되는 슬리피지 분수. 예: 0.0005 = 0.05%."""
    position_fraction: float = Field(default=1.0, gt=0, le=1)
    """진입 시 노셔널로 사용할 자본(equity) 비율."""
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
    exits: list[TradeFill]
    realized_pnl: float
    """모든 수수료(진입·청산)를 차감한 순손익."""
    return_pct: float
    """`realized_pnl`을 진입 노셔널로 나눈 수익률."""

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


class BacktestResult(BaseModel):
    """`BacktestEngine.run()`의 반환값. 파라미터·거래·자본곡선·지표를 모두 포함."""

    model_config = ConfigDict(frozen=True)

    config: BacktestConfig
    trades: list[Trade]
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics
