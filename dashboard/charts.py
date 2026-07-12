"""캔들 + 오더블록 + 시그널/트레이드 오버레이 plotly 차트.

`strategy.parity.chart`(matplotlib, 정적 패리티 검증용)와 별개로, 대시보드는
확대·축소·호버 등 인터랙션이 필요해 plotly로 구현한다.

## 렌더링 전략 (WAN-52)

3년치 15m 아카이브는 존이 3,000개를 넘어, 존마다 `add_shape`(layout 객체)를
호출하면 줌·팬마다 전량 재계산돼 브라우저가 멈춘다. 그래서:

1. **수명 기반 박스** — 각 존을 `start_time`부터 **무효화/소멸 시점**(없으면 마지막
   봉)까지만 그린다. 오른쪽 끝까지 늘이지 않아 트레이딩뷰 타임랩스처럼 겹침이 준다.
2. **트레이스 통합** — 모든 박스를 방향·상태별 **소수의 채워진 `Scatter`**(폴리곤을
   `None` 구분자로 이어붙임)로 합친다. layout shape가 아니라 data 트레이스가 되어
   줌·팬 비용이 급감한다.
3. **표시 필터·시점 재생** — 어떤 존 집합을 넘길지는 호출자(`dashboard.app`)가 정한다.
   이 모듈은 넘어온 존을 위 두 방식으로 그리기만 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

import pandas as pd
import plotly.graph_objects as go

from backtest.models import BacktestResult, PositionSide
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockSignal

_BULL_COLOR = "#26a69a"
_BEAR_COLOR = "#ef5350"
_BULL_ZONE_LINE = "rgba(38, 166, 154, 0.9)"
_BEAR_ZONE_LINE = "rgba(239, 83, 80, 0.9)"
_BULL_ZONE_FILL = "rgba(38, 166, 154, 0.20)"
_BEAR_ZONE_FILL = "rgba(239, 83, 80, 0.20)"
#: breaker(무효화)로 전환됐던 존은 옅게 칠해 "깨졌던 것"임을 구분한다.
_BULL_ZONE_FILL_FADED = "rgba(38, 166, 154, 0.09)"
_BEAR_ZONE_FILL_FADED = "rgba(239, 83, 80, 0.09)"

#: 초기 렌더에 그릴 캔들 상한. 넘으면 시간 버킷으로 OHLC 다운샘플링해 이 개수
#: 안팎으로 줄인다(WAN-52). 3년 15m(105,121봉)에서도 브라우저가 감당할 봉 수만
#: 넘겨 초기 렌더가 빨라진다. 오더블록·거래 마커는 원본 시각 그대로 유지된다.
_MAX_CANDLES = 4_000


class ZoneCategory(StrEnum):
    """오더블록의 생애주기 상태 분류(표시 필터용, WAN-52).

    한 존은 여러 범주에 동시에 속할 수 있다(예: 진입이 발생한 활성 존).
    """

    ENTERED = "entered"
    """실제 진입(활성 시그널)이 발생한 존."""
    ACTIVE = "active"
    """아직 무효화·소멸하지 않은 존."""
    TAPPED = "tapped"
    """가격이 닿았고(tap) 아직 깨지지 않은 존 — "지지했던" 존."""
    BROKEN = "broken"
    """breaker로 무효화됐으나 아직 소멸하지 않은 존 — "깨졌던" 존."""
    SWEPT = "swept"
    """되쓸려 완전히 소멸한 존."""


#: 사이드바 표시 필터의 기본 선택(전체가 아닌 "진입한 존 + 활성 존").
DEFAULT_ZONE_CATEGORIES: frozenset[ZoneCategory] = frozenset(
    {ZoneCategory.ENTERED, ZoneCategory.ACTIVE}
)

ZONE_CATEGORY_LABELS: dict[ZoneCategory, str] = {
    ZoneCategory.ENTERED: "진입한 존",
    ZoneCategory.ACTIVE: "활성 존",
    ZoneCategory.TAPPED: "지지한 존(탭)",
    ZoneCategory.BROKEN: "깨진 존(무효화)",
    ZoneCategory.SWEPT: "소멸한 존",
}


def zone_key(ob: OrderBlock) -> tuple[str, int, int, float, float]:
    """존을 식별하는 해시 가능한 키(방향·시작·확정·상하단)."""
    return (str(ob.direction), ob.start_time, ob.confirmed_time, ob.top, ob.bottom)


def entered_zone_keys(
    signals: Iterable[OrderBlockSignal],
) -> set[tuple[str, int, int, float, float]]:
    """진입(활성 시그널)이 발생한 존들의 키 집합을 만든다 (WAN-52).

    사용자의 실제 매매 규칙(존당 첫 탭 1회 + RSI)을 시계열로 재생해 나온 진입
    시그널이 `status="active"`다. cancelled(무효화 봉에서의 탭)는 진입이 아니다.
    """
    return {zone_key(s.order_block) for s in signals if s.status == "active"}


def zone_categories(
    ob: OrderBlock,
    entered_keys: set[tuple[str, int, int, float, float]] | None = None,
) -> set[ZoneCategory]:
    """존이 속한 생애주기 범주 집합을 반환한다 (WAN-52).

    소멸(`swept_time`)이 최우선 종말 상태, 그다음 무효화(`break_time`)다. 둘 다
    없으면 활성이며, 활성이면서 탭 기록이 있으면 "지지"로도 분류한다. 진입 여부는
    `entered_keys`(있으면)로 판정해 독립적으로 추가한다.
    """
    cats: set[ZoneCategory] = set()
    if entered_keys is not None and zone_key(ob) in entered_keys:
        cats.add(ZoneCategory.ENTERED)
    if ob.swept_time is not None:
        cats.add(ZoneCategory.SWEPT)
    elif ob.break_time is not None:
        cats.add(ZoneCategory.BROKEN)
    else:
        cats.add(ZoneCategory.ACTIVE)
        if ob.tapped_times:
            cats.add(ZoneCategory.TAPPED)
    return cats


def filter_zones(
    order_blocks: Sequence[OrderBlock],
    selected: Iterable[ZoneCategory],
    entered_keys: set[tuple[str, int, int, float, float]] | None = None,
) -> list[OrderBlock]:
    """선택된 범주 중 하나라도 해당하는 존만 남긴다 (WAN-52)."""
    wanted = set(selected)
    if not wanted:
        return []
    return [ob for ob in order_blocks if zone_categories(ob, entered_keys) & wanted]


def _to_datetime(ms_series: pd.Series) -> pd.Series:
    return pd.to_datetime(ms_series, unit="ms", utc=True)


def _downsample_ohlc(frame: pd.DataFrame, max_bars: int) -> pd.DataFrame:
    """봉이 `max_bars`를 넘으면 균등 시간 버킷으로 OHLC를 집계해 봉 수를 줄인다.

    각 버킷은 첫 시가·최고 고가·최저 저가·마지막 종가·거래량 합으로 하나의 봉이
    된다(표준 OHLC 리샘플링). 개수만 줄일 뿐 가격 범위·추세는 보존돼, 전체 구간
    개요에서 캔들 렌더 부담을 크게 낮춘다(WAN-52). `max_bars` 이하면 원본 그대로.
    """
    n = len(frame)
    if n <= max_bars:
        return frame
    bucket_size = (n + max_bars - 1) // max_bars
    buckets = frame.index // bucket_size
    grouped = frame.groupby(buckets)
    return pd.DataFrame(
        {
            "open_time": grouped["open_time"].first().to_numpy(),
            "open": grouped["open"].first().to_numpy(),
            "high": grouped["high"].max().to_numpy(),
            "low": grouped["low"].min().to_numpy(),
            "close": grouped["close"].last().to_numpy(),
            "volume": grouped["volume"].sum().to_numpy(),
        }
    )


def _zone_span_end(ob: OrderBlock, last_bar_ms: int) -> int:
    """존 박스의 오른쪽 변(수명 종료 시점) ms를 구한다 (WAN-52).

    무효화(`break_time`)나 소멸(`swept_time`)이 있으면 그 시점에서 끝나고, 아직
    살아있으면 마지막 봉까지 늘인다. 오른쪽 끝까지 무조건 늘이던 이전 동작과 달리
    수명 구간에만 그려 겹침을 줄인다.
    """
    if ob.break_time is not None:
        return ob.break_time
    if ob.swept_time is not None:
        return ob.swept_time
    return last_bar_ms


def _add_zone_traces(fig: go.Figure, order_blocks: Sequence[OrderBlock], last_bar_ms: int) -> None:
    """모든 존 박스를 방향·상태별 소수의 채워진 Scatter 트레이스로 합쳐 그린다.

    각 박스는 5개 모서리 점 + `None` 구분자로 폴리곤을 이어붙여, 방향(강세/약세)과
    상태(활성/깨짐)별로 최대 4개의 트레이스만 만든다(WAN-52).
    """
    # (방향, breaker) → 박스 폴리곤 좌표. breaker 여부는 존의 최종 상태로 색을 정한다.
    groups: dict[tuple[OrderBlockDirection, bool], tuple[list[object], list[object]]] = {}
    for ob in order_blocks:
        key = (ob.direction, ob.breaker)
        xs, ys = groups.setdefault(key, ([], []))
        start = ob.start_time
        end = max(_zone_span_end(ob, last_bar_ms), start)
        x0 = pd.Timestamp(start, unit="ms", tz="UTC")
        x1 = pd.Timestamp(end, unit="ms", tz="UTC")
        xs.extend([x0, x1, x1, x0, x0, None])
        ys.extend([ob.bottom, ob.bottom, ob.top, ob.top, ob.bottom, None])

    for direction in (OrderBlockDirection.BULLISH, OrderBlockDirection.BEARISH):
        is_bull = direction is OrderBlockDirection.BULLISH
        line_color = _BULL_ZONE_LINE if is_bull else _BEAR_ZONE_LINE
        for breaker in (False, True):
            coords = groups.get((direction, breaker))
            if coords is None:
                continue
            xs, ys = coords
            if is_bull:
                fill = _BULL_ZONE_FILL_FADED if breaker else _BULL_ZONE_FILL
            else:
                fill = _BEAR_ZONE_FILL_FADED if breaker else _BEAR_ZONE_FILL
            side = "강세" if is_bull else "약세"
            state = "깨짐" if breaker else "활성"
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    fill="toself",
                    fillcolor=fill,
                    mode="lines",
                    line={
                        "color": line_color,
                        "width": 1,
                        "dash": "dot" if breaker else "solid",
                    },
                    name=f"{side} 존({state})",
                    legendgroup=f"zone-{direction}-{breaker}",
                    hoverinfo="skip",
                )
            )


def build_price_chart(
    df: pd.DataFrame,
    order_blocks: Sequence[OrderBlock],
    backtest: BacktestResult | None = None,
    *,
    title: str = "",
) -> go.Figure:
    """캔들 차트 위에 오더블록 존과 (있다면) 진입/청산 마커를 오버레이한다.

    존은 수명 구간에만, 방향·상태별 통합 트레이스로 그린다(WAN-52). 캔들이
    `_MAX_CANDLES`를 넘으면 OHLC 다운샘플링해 초기 렌더 부담을 낮춘다. 오더블록
    존과 거래 마커는 원본 시각 그대로 유지되므로 위치는 정확하다.
    """
    frame = df.sort_values("open_time").reset_index(drop=True)
    last_bar_ms = int(frame["open_time"].iloc[-1]) if len(frame) else 0
    candles = _downsample_ohlc(frame, _MAX_CANDLES)
    x = _to_datetime(candles["open_time"])

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=x,
                open=candles["open"],
                high=candles["high"],
                low=candles["low"],
                close=candles["close"],
                name="price",
                increasing_line_color=_BULL_COLOR,
                decreasing_line_color=_BEAR_COLOR,
            )
        ]
    )

    if len(frame) and len(order_blocks):
        _add_zone_traces(fig, order_blocks, last_bar_ms)

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
    entry_text: list[str] = []
    exit_x: list[pd.Timestamp] = []
    exit_y: list[float] = []

    for trade in backtest.trades:
        entry_x.append(pd.Timestamp(trade.entry_time, unit="ms", tz="UTC"))
        entry_y.append(trade.entry_price)
        is_long = trade.side is PositionSide.LONG
        entry_symbol.append("triangle-up" if is_long else "triangle-down")
        entry_text.append("롱" if is_long else "숏")
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
                text=entry_text,
                hovertemplate="진입 %{text}<br>%{x}<br>가격 %{y}<extra></extra>",
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
                hovertemplate="청산<br>%{x}<br>가격 %{y}<extra></extra>",
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
