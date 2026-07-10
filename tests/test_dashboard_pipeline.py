"""dashboard.pipeline 테스트.

`run_pipeline`이 `OrderBlockDetector` + `BacktestEngine`을 직접 호출한
결과와 동일한 값을 내는지 확인한다(스키마 결합 확인용 스모크 테스트).
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from dashboard.pipeline import run_pipeline
from strategy.order_blocks import OrderBlockDetector

_STEP = 3_600_000


def _make_df(bars: Sequence[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * _STEP for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
    )


# strategy/order_blocks 테스트와 동일한 강세 오더블록 시나리오(재사용 가능한 최소 픽스처).
_BARS = [
    (100, 102, 90, 95, 10),
    (95, 100, 93, 98, 10),
    (98, 101, 94, 99, 10),
    (99, 103, 95, 101, 10),
    (101, 110, 100, 108, 10),
    (108, 109, 104, 106, 15),
    (106, 107, 103, 105, 20),
    (105, 106, 102, 104, 25),
    (104, 105, 100, 102, 10),
    (102, 104, 99, 101, 10),
    (101, 103, 98, 100, 10),
    (100, 105, 99, 112, 30),
    (112, 113, 95, 96, 10),
]


def test_run_pipeline_matches_direct_detector_and_engine_calls() -> None:
    df = _make_df(_BARS)

    result = run_pipeline(df)

    expected_detection = OrderBlockDetector().run(df)
    expected_backtest = BacktestEngine().run(df, expected_detection.signals)

    assert result.order_blocks == expected_detection.order_blocks
    assert result.signals == expected_detection.signals
    assert result.backtest == expected_backtest


def test_run_pipeline_forwards_custom_configs() -> None:
    df = _make_df(_BARS)
    config = BacktestConfig(take_profit_pct=0.05, stop_loss_pct=0.02, initial_capital=5_000.0)

    result = run_pipeline(df, backtest_config=config)

    assert result.backtest.config == config
    assert result.backtest.metrics.initial_capital == 5_000.0
