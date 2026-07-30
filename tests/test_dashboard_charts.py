"""dashboard.charts 테스트.

존 생애주기 분류/필터 순수 함수와 자본곡선 Figure를 검증한다(캔들+오더블록+RSI
메인 차트는 dashboard.lightweight_chart로 이관 — tests/test_dashboard_lightweight_chart.py
참고).
"""

from __future__ import annotations

import pandas as pd

from backtest.metrics import build_metrics
from backtest.models import BacktestConfig, BacktestResult, EquityPoint
from dashboard.charts import (
    ZoneCategory,
    build_equity_chart,
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


def _flat_backtest(n: int) -> BacktestResult:
    """거래 없는 평평한 자본곡선(봉당 1점)의 결과 — 옛 A안 `run_backtest(df, [])`의 대체.

    A안 엔진이 WAN-208/WAN-215로 제거돼, 자본곡선 차트가 읽는 `equity_curve`만 직접
    구성한다(엔진 없이 렌더링 계층만 격리 검증).
    """
    equities = [10_000.0] * n
    metrics = build_metrics(initial_capital=10_000.0, equities=equities or [10_000.0], trades=[])
    equity_curve = [EquityPoint(time=i * _STEP, equity=10_000.0) for i in range(n)]
    return BacktestResult(
        config=BacktestConfig(), trades=[], equity_curve=equity_curve, metrics=metrics
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


def test_build_equity_chart_has_one_point_per_equity_curve_entry() -> None:
    backtest = _flat_backtest(5)

    fig = build_equity_chart(backtest)

    assert len(fig.data) == 1
    assert len(fig.data[0].x) == len(backtest.equity_curve)


def test_build_equity_chart_theme_selects_template() -> None:
    backtest = _flat_backtest(5)

    dark = build_equity_chart(backtest)  # 기본 다크
    light = build_equity_chart(backtest, theme="light")

    assert dark.data[0].line.color == "#42a5f5"
    assert light.data[0].line.color == "#1e88e5"
    # 배경은 투명이라 Streamlit 테마와 섞인다.
    assert dark.layout.paper_bgcolor == "rgba(0,0,0,0)"
    assert light.layout.paper_bgcolor == "rgba(0,0,0,0)"
