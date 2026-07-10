"""캔들 + 오더블록 + 시그널/트레이드 오버레이 plotly 차트.

`strategy.parity.chart`(matplotlib, 정적 패리티 검증용)와 별개로, 대시보드는
확대·축소·호버 등 인터랙션이 필요해 plotly로 구현한다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import plotly.graph_objects as go

from backtest.models import BacktestResult, PositionSide
from strategy.models import OrderBlock, OrderBlockDirection

_BULL_COLOR = "#26a69a"
_BEAR_COLOR = "#ef5350"
_BULL_ZONE_COLOR = "rgba(38, 166, 154, 0.9)"
_BEAR_ZONE_COLOR = "rgba(239, 83, 80, 0.9)"
_BULL_ZONE_FILL = "rgba(38, 166, 154, 0.20)"
_BEAR_ZONE_FILL = "rgba(239, 83, 80, 0.20)"
_INVALIDATED_OPACITY = 0.45


def _to_datetime(ms_series: pd.Series) -> pd.Series:
    return pd.to_datetime(ms_series, unit="ms", utc=True)


def build_price_chart(
    df: pd.DataFrame,
    order_blocks: Sequence[OrderBlock],
    backtest: BacktestResult | None = None,
    *,
    title: str = "",
) -> go.Figure:
    """캔들 차트 위에 오더블록 존과 (있다면) 진입/청산 마커를 오버레이한다."""
    frame = df.sort_values("open_time").reset_index(drop=True)
    x = _to_datetime(frame["open_time"])

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=x,
                open=frame["open"],
                high=frame["high"],
                low=frame["low"],
                close=frame["close"],
                name="price",
                increasing_line_color=_BULL_COLOR,
                decreasing_line_color=_BEAR_COLOR,
            )
        ]
    )

    end_time = int(frame["open_time"].iloc[-1]) if len(frame) else 0
    for ob in order_blocks:
        is_bull = ob.direction == OrderBlockDirection.BULLISH
        fill = _BULL_ZONE_FILL if is_bull else _BEAR_ZONE_FILL
        line_color = _BULL_ZONE_COLOR if is_bull else _BEAR_ZONE_COLOR
        end = ob.break_time if ob.break_time is not None else end_time
        fig.add_shape(
            type="rect",
            x0=pd.Timestamp(ob.start_time, unit="ms", tz="UTC"),
            x1=pd.Timestamp(max(end, ob.start_time), unit="ms", tz="UTC"),
            y0=ob.bottom,
            y1=ob.top,
            fillcolor=fill,
            opacity=_INVALIDATED_OPACITY if ob.breaker else 1.0,
            line={"color": line_color, "dash": "dot" if ob.breaker else "solid", "width": 1},
            layer="below",
        )

    if backtest is not None:
        _add_trade_markers(fig, backtest)

    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        height=600,
        legend={"orientation": "h", "y": 1.02, "yanchor": "bottom"},
    )
    return fig


def _add_trade_markers(fig: go.Figure, backtest: BacktestResult) -> None:
    entry_x: list[pd.Timestamp] = []
    entry_y: list[float] = []
    entry_symbol: list[str] = []
    exit_x: list[pd.Timestamp] = []
    exit_y: list[float] = []

    for trade in backtest.trades:
        entry_x.append(pd.Timestamp(trade.entry_time, unit="ms", tz="UTC"))
        entry_y.append(trade.entry_price)
        entry_symbol.append("triangle-up" if trade.side is PositionSide.LONG else "triangle-down")
        for fill in trade.exits:
            exit_x.append(pd.Timestamp(fill.time, unit="ms", tz="UTC"))
            exit_y.append(fill.price)

    if entry_x:
        fig.add_trace(
            go.Scatter(
                x=entry_x,
                y=entry_y,
                mode="markers",
                marker={
                    "symbol": entry_symbol,
                    "size": 11,
                    "color": "#1e88e5",
                    "line": {"width": 1, "color": "white"},
                },
                name="entry",
            )
        )
    if exit_x:
        fig.add_trace(
            go.Scatter(
                x=exit_x,
                y=exit_y,
                mode="markers",
                marker={"symbol": "x", "size": 9, "color": "#6d4c41"},
                name="exit",
            )
        )


def build_equity_chart(backtest: BacktestResult) -> go.Figure:
    """자본곡선(equity curve) Figure."""
    times = [pd.Timestamp(p.time, unit="ms", tz="UTC") for p in backtest.equity_curve]
    equities = [p.equity for p in backtest.equity_curve]
    fig = go.Figure(
        data=[
            go.Scatter(x=times, y=equities, mode="lines", name="equity", line={"color": "#1e88e5"})
        ]
    )
    fig.update_layout(title="Equity Curve", template="plotly_white", height=300)
    return fig
