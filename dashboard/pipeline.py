"""전략 파이프라인: OHLCV → 오더블록 탐지 → 백테스트.

대시보드는 이 모듈이 반환하는 `PipelineResult`(오더블록·시그널·백테스트
결과) 스키마에만 의존한다. 시그널 생성 로직(현재 WAN-7 기본 오더블록)이
추후 WAN-18 컨플루언스 전략 등으로 교체되어도, 이 스키마만 유지되면
대시보드 쪽 코드는 변경할 필요가 없다.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, BacktestResult
from strategy.models import OrderBlock, OrderBlockParams, OrderBlockSignal
from strategy.order_blocks import OrderBlockDetector


class PipelineResult(BaseModel):
    """오더블록 탐지 결과 + 백테스트 결과를 함께 담는 값 객체."""

    model_config = ConfigDict(frozen=True)

    order_blocks: list[OrderBlock]
    signals: list[OrderBlockSignal]
    backtest: BacktestResult


def run_pipeline(
    df: pd.DataFrame,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
) -> PipelineResult:
    """OHLCV DataFrame에 오더블록 탐지와 백테스트를 순서대로 실행한다."""
    detection = OrderBlockDetector(order_block_params).run(df)
    backtest = BacktestEngine(backtest_config).run(df, detection.signals)
    return PipelineResult(
        order_blocks=detection.order_blocks,
        signals=detection.signals,
        backtest=backtest,
    )
