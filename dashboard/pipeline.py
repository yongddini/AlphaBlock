"""전략 파이프라인: OHLCV → 오더블록 탐지 → 컨플루언스 전략 → 백테스트 (WAN-59).

대시보드 분석 탭은 이 모듈이 반환하는 `PipelineResult`(오더블록·확정 진입 시그널·
백테스트 결과) 스키마에만 의존한다.

백테스트는 CLI 리포트(`scripts/backtest_report.py`)와 **동일한 함수**
(`backtest.sweep.evaluate`)를 호출해 실행한다. 즉 대시보드와 CLI가 같은 코드 경로로
컨플루언스 전략(WAN-23: 오더블록+RSI 진입 / EMA·VWMA 선 도달 익절 / 오더블록 무효화
손절)을 태우므로, 두 화면의 거래 수·수익률이 갈라질 수 없다(WAN-59 재발 방지). 오더블록
탐지는 컨플루언스 파라미터와 무관하므로 한 번만 실행해 차트용 존 아카이브와 백테스트에
함께 재사용한다.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.models import BacktestConfig, BacktestResult
from backtest.sweep import CLOSE_ENTRY_DEFAULTS, evaluate
from strategy.confluence import ConfluenceStrategy
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockParams,
    OrderBlockSignal,
)
from strategy.order_blocks import OrderBlockDetector


class PipelineResult(BaseModel):
    """오더블록 탐지 결과 + 백테스트 결과를 함께 담는 값 객체."""

    model_config = ConfigDict(frozen=True)

    order_blocks: list[OrderBlock]
    """생성된 모든 존의 전체 생애주기 아카이브(WAN-47). "전 구간" 뷰용."""
    rendered_order_blocks: list[OrderBlock]
    """마지막 봉 시점의 렌더링 뷰(트레이딩뷰 패리티). "현재 활성 존" 뷰용."""
    signals: list[OrderBlockSignal]
    """백테스트가 실제로 소비한 확정 진입(진입 조건 통과 + 계획 청산 포함). 차트의
    "진입한 존" 분류가 이 시그널을 기준으로 하므로 화면과 성과가 일치한다.

    ⚠️ 채택 기본값에는 **RSI 게이트가 없다**(WAN-123 `rsi_gate_mode="unconditional"`) —
    기본 설정에서 이 목록은 "선별된 진입"이 아니라 **탭이 곧 진입**이다."""
    backtest: BacktestResult


def run_pipeline(
    df: pd.DataFrame,
    order_block_params: OrderBlockParams | None = None,
    confluence_params: ConfluenceParams | None = None,
    backtest_config: BacktestConfig | None = None,
) -> PipelineResult:
    """OHLCV DataFrame에 오더블록 탐지·컨플루언스 전략·백테스트를 순서대로 실행한다.

    백테스트는 CLI 리포트와 공유하는 `backtest.sweep.evaluate`로 실행해, 두 경로가
    항상 같은 전략·설정으로 동일한 결과를 내도록 한다(WAN-59). `signals`에는 백테스트가
    소비한 확정 진입(컨플루언스가 진입 조건으로 선별하고 계획 청산을 실은 것)을 담는다 —
    채택 기본값에는 RSI 게이트가 없으므로(WAN-123) 그 선별은 볼린저 재산정이 전부다.

    파라미터 미지정 시 A안 기본값(`CLOSE_ENTRY_DEFAULTS`)을 **한 번 해석해 시그널 생성과
    백테스트에 같은 값을 넘긴다** — 이 경로는 상위TF만 로드해 지정가 체결(채택 기본값)을
    시뮬레이션할 수 없다(WAN-95). 두 곳이 서로 다른 기본값을 집으면 화면의 시그널과
    성과가 갈라진다(WAN-59가 막으려던 바로 그 문제).
    """
    resolved_conf = confluence_params or CLOSE_ENTRY_DEFAULTS
    detection = OrderBlockDetector(order_block_params).run(df)
    confluence = ConfluenceStrategy(resolved_conf, order_block_params).run(df, detection)
    backtest = evaluate(
        df,
        confluence_params=resolved_conf,
        order_block_params=order_block_params,
        backtest_config=backtest_config,
        order_block_result=detection,
    )
    return PipelineResult(
        order_blocks=detection.order_blocks,
        rendered_order_blocks=detection.rendered_order_blocks,
        signals=confluence.order_block_signals,
        backtest=backtest,
    )
