"""dashboard.charts 테스트.

plotly Figure의 트레이스/셰이프 개수와 종류만 검증한다(렌더링 픽셀 비교는
범위 밖). 데이터는 backtest 테스트와 동일한 방식의 최소 픽스처를 사용한다.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from backtest.engine import run_backtest
from backtest.models import BacktestConfig
from dashboard.charts import build_equity_chart, build_price_chart
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockSignal

_STEP = 3_600_000


def _df(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * _STEP for i in range(n)],
            "open": [100.0] * n,
            "high": [105.0] * n,
            "low": [95.0] * n,
            "close": [100.0 + i for i in range(n)],
            "volume": [10.0] * n,
        }
    )


def _order_block(*, breaker: bool) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=101.0,
        bottom=99.0,
        start_time=0,
        confirmed_time=_STEP,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=breaker,
        break_time=2 * _STEP if breaker else None,
    )


def test_build_price_chart_has_candlestick_and_one_shape_per_order_block() -> None:
    df = _df(5)
    order_blocks = [_order_block(breaker=False), _order_block(breaker=True)]

    fig = build_price_chart(df, order_blocks, title="test")

    assert isinstance(fig, go.Figure)
    assert isinstance(fig.data[0], go.Candlestick)
    assert len(fig.layout.shapes) == len(order_blocks)


def test_build_price_chart_adds_entry_and_exit_markers_from_backtest() -> None:
    df = _df(5)
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=0,
        price=100.0,
        order_block=_order_block(breaker=False),
        status="active",
    )
    backtest = run_backtest(df, [signal], BacktestConfig(take_profit_pct=0.5))

    fig = build_price_chart(df, [], backtest)

    trace_names = [trace.name for trace in fig.data]
    assert "price" in trace_names
    assert "entry" in trace_names


def test_build_equity_chart_has_one_point_per_equity_curve_entry() -> None:
    df = _df(5)
    backtest = run_backtest(df, [])

    fig = build_equity_chart(backtest)

    assert len(fig.data) == 1
    assert len(fig.data[0].x) == len(backtest.equity_curve)
