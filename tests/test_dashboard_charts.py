"""dashboard.charts 테스트.

plotly Figure의 트레이스 개수·종류와 존 필터/분류 순수 함수를 검증한다(렌더링
픽셀 비교는 범위 밖). 데이터는 backtest 테스트와 동일한 방식의 최소 픽스처를 쓴다.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from backtest.engine import run_backtest
from backtest.models import BacktestConfig
from dashboard.charts import (
    ZoneCategory,
    build_equity_chart,
    build_price_chart,
    entered_zone_keys,
    filter_zones,
    zone_categories,
)
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


def _order_block(
    *,
    breaker: bool = False,
    break_time: int | None = None,
    swept_time: int | None = None,
    tapped_times: tuple[int, ...] = (),
    top: float = 101.0,
    bottom: float = 99.0,
) -> OrderBlock:
    if breaker and break_time is None:
        break_time = 2 * _STEP
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=_STEP,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=breaker,
        break_time=break_time,
        swept_time=swept_time,
        tapped_times=tapped_times,
    )


def _zone_traces(fig: go.Figure) -> list[go.Scatter]:
    """채워진 존 폴리곤 트레이스만 추린다(이름에 '존' 포함)."""
    return [t for t in fig.data if isinstance(t, go.Scatter) and t.name and "존" in t.name]


def test_build_price_chart_consolidates_zones_into_filled_traces() -> None:
    df = _df(5)
    # 활성/깨짐 두 상태의 강세 존 → shape가 아니라 방향·상태별 통합 트레이스로.
    order_blocks = [_order_block(breaker=False), _order_block(breaker=True)]

    fig = build_price_chart(df, order_blocks, title="test")

    assert isinstance(fig, go.Figure)
    assert isinstance(fig.data[0], go.Candlestick)
    # add_shape는 더 이상 쓰지 않는다(줌·팬 재계산 병목 제거).
    assert len(fig.layout.shapes) == 0
    zone_traces = _zone_traces(fig)
    # 강세 활성 1개 + 강세 깨짐 1개 = 트레이스 2개. 존 3,000개여도 상수 개로 유지된다.
    assert len(zone_traces) == 2
    for trace in zone_traces:
        assert trace.fill == "toself"


def test_build_price_chart_trace_count_stays_constant_regardless_of_zone_count() -> None:
    df = _df(5)
    many = [_order_block(breaker=False) for _ in range(50)]

    fig = build_price_chart(df, many)

    # 같은 방향·상태의 존 50개도 트레이스 1개로 합쳐진다.
    assert len(_zone_traces(fig)) == 1


def test_zone_span_ends_at_break_not_last_bar() -> None:
    df = _df(10)
    broken = _order_block(breaker=True, break_time=3 * _STEP)

    fig = build_price_chart(df, [broken])
    trace = _zone_traces(fig)[0]

    # 폴리곤의 오른쪽 변(x1)이 마지막 봉이 아니라 무효화 시각에서 끝난다(수명 기반).
    xs = [x for x in trace.x if x is not None]
    right_edge = max(pd.Timestamp(x) for x in xs)
    assert right_edge == pd.Timestamp(3 * _STEP, unit="ms", tz="UTC")


def test_build_price_chart_downsamples_large_candle_counts() -> None:
    from dashboard.charts import _MAX_CANDLES

    df = _df(_MAX_CANDLES * 3)
    fig = build_price_chart(df, [])

    candle = fig.data[0]
    assert isinstance(candle, go.Candlestick)
    # 다운샘플링으로 렌더 봉 수가 상한 이하로 줄어든다(원본 개수보다 훨씬 적음).
    assert len(candle.x) <= _MAX_CANDLES
    # 가격 범위는 보존된다(집계로 개수만 줄이고 고저는 유지).
    assert float(max(candle.high)) == float(df["high"].max())
    assert float(min(candle.low)) == float(df["low"].min())


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


def test_zone_categories_classify_lifecycle_states() -> None:
    active = _order_block()
    assert zone_categories(active) == {ZoneCategory.ACTIVE}

    supported = _order_block(tapped_times=(2 * _STEP,))
    assert zone_categories(supported) == {ZoneCategory.ACTIVE, ZoneCategory.TAPPED}

    broken = _order_block(breaker=True, break_time=3 * _STEP)
    assert zone_categories(broken) == {ZoneCategory.BROKEN}

    swept = _order_block(breaker=True, break_time=3 * _STEP, swept_time=5 * _STEP)
    assert zone_categories(swept) == {ZoneCategory.SWEPT}


def test_entered_zone_keys_and_filter_pick_entered_zones() -> None:
    entered_ob = _order_block(top=101.0, bottom=99.0)
    other_ob = _order_block(top=201.0, bottom=199.0)
    signals = [
        OrderBlockSignal(
            direction=OrderBlockDirection.BULLISH,
            trigger_time=2 * _STEP,
            price=100.0,
            order_block=entered_ob,
            status="active",
        ),
        # cancelled(무효화 봉 탭)은 진입이 아니다 → 진입 집합에서 제외.
        OrderBlockSignal(
            direction=OrderBlockDirection.BULLISH,
            trigger_time=3 * _STEP,
            price=100.0,
            order_block=other_ob,
            status="cancelled",
        ),
    ]
    keys = entered_zone_keys(signals)

    assert zone_categories(entered_ob, keys) == {ZoneCategory.ENTERED, ZoneCategory.ACTIVE}
    assert ZoneCategory.ENTERED not in zone_categories(other_ob, keys)

    only_entered = filter_zones([entered_ob, other_ob], {ZoneCategory.ENTERED}, keys)
    assert only_entered == [entered_ob]


def test_filter_zones_empty_selection_returns_nothing() -> None:
    assert filter_zones([_order_block()], set()) == []


def test_build_price_chart_adds_rsi_subpanel_without_shapes() -> None:
    df = _df(30)

    fig = build_price_chart(df, [], title="test")

    rsi_traces = [t for t in fig.data if t.name and t.name.startswith("RSI(")]
    assert len(rsi_traces) == 1
    # 기준선(30/50/70)도 add_hline이 아니라 데이터 트레이스로 그려 shape가 늘지 않는다.
    assert len(fig.layout.shapes) == 0
    assert isinstance(fig.data[0], go.Candlestick)


def test_build_price_chart_downsamples_rsi_line_with_candles() -> None:
    from dashboard.charts import _MAX_CANDLES

    df = _df(_MAX_CANDLES * 3)
    fig = build_price_chart(df, [])

    candle = fig.data[0]
    rsi_trace = next(t for t in fig.data if t.name and t.name.startswith("RSI("))
    # RSI 라인도 캔들과 같은 버킷 수로 줄어 x좌표 개수가 일치한다(정렬 보장).
    assert len(rsi_trace.x) == len(candle.x)


def test_build_price_chart_entry_marker_hover_includes_rsi() -> None:
    df = _df(30)
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=20 * _STEP,
        price=100.0,
        order_block=_order_block(),
        status="active",
    )
    backtest = run_backtest(df, [signal], BacktestConfig(take_profit_pct=0.5))

    fig = build_price_chart(df, [], backtest)

    entry_trace = next(t for t in fig.data if t.name == "entry")
    assert "RSI" in entry_trace.hovertemplate
    rsi_entry_traces = [t for t in fig.data if t.name == "entry (RSI)"]
    assert len(rsi_entry_traces) == 1
    assert rsi_entry_traces[0].x == entry_trace.x


def test_build_equity_chart_has_one_point_per_equity_curve_entry() -> None:
    df = _df(5)
    backtest = run_backtest(df, [])

    fig = build_equity_chart(backtest)

    assert len(fig.data) == 1
    assert len(fig.data[0].x) == len(backtest.equity_curve)
