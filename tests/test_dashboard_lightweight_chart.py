"""dashboard.lightweight_chart 테스트.

브라우저 렌더링(픽셀) 비교는 범위 밖. HTML에 임베드된 JSON 페이로드(캔들·존
박스·RSI·마커)의 구조와 값을 검증한다(backtest 테스트와 동일한 최소 픽스처).
"""

from __future__ import annotations

import json

import pandas as pd

from backtest.engine import run_backtest
from backtest.models import BacktestConfig
from dashboard.lightweight_chart import _RSI_LENGTH, build_chart_html
from strategy.indicators import ema
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockSignal,
    PlannedExit,
    SignalExitReason,
)

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
    )


def _payload(html: str) -> dict[str, object]:
    start = html.index("const payload = ") + len("const payload = ")
    end = html.index(";\n  const container")
    return json.loads(html[start:end])  # type: ignore[no-any-return]


def test_build_chart_html_embeds_library_and_candles() -> None:
    df = _df(5)

    html = build_chart_html(df, [])

    assert "LightweightCharts" in html
    assert "TradingView Lightweight Charts" in html
    payload = _payload(html)
    assert len(payload["candles"]) == 5  # type: ignore[arg-type]


def test_build_chart_html_empty_df_returns_placeholder() -> None:
    html = build_chart_html(_df(0), [])

    assert "표시할 데이터가 없습니다" in html
    assert "LightweightCharts" not in html


def test_zone_box_spans_break_time_not_last_bar() -> None:
    df = _df(10)
    broken = _order_block(breaker=True, break_time=3 * _STEP)

    payload = _payload(build_chart_html(df, [broken]))

    boxes = payload["boxes"]
    assert len(boxes) == 1  # type: ignore[arg-type]
    box = boxes[0]  # type: ignore[index]
    assert box["start"] == 0
    assert box["end"] == (3 * _STEP) // 1000
    assert box["top"] == 101.0
    assert box["bottom"] == 99.0


def test_zone_box_extends_to_last_bar_when_still_active() -> None:
    df = _df(10)
    active = _order_block(breaker=False)

    payload = _payload(build_chart_html(df, [active]))

    box = payload["boxes"][0]  # type: ignore[index]
    last_bar_ms = 9 * _STEP
    assert box["end"] == last_bar_ms // 1000


def test_build_chart_html_one_box_per_zone_no_consolidation() -> None:
    df = _df(5)
    zones = [_order_block(breaker=False) for _ in range(50)]

    payload = _payload(build_chart_html(df, zones))

    assert len(payload["boxes"]) == 50  # type: ignore[arg-type]


def test_build_chart_html_adds_entry_marker_with_rsi_in_text() -> None:
    df = _df(30)
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=20 * _STEP,
        price=100.0,
        order_block=_order_block(),
        status="active",
    )
    backtest = run_backtest(df, [signal], BacktestConfig(take_profit_pct=0.5))

    payload = _payload(build_chart_html(df, [], backtest))

    markers = payload["markers"]
    assert len(markers) >= 1  # type: ignore[arg-type]
    entry = markers[0]  # type: ignore[index]
    assert "롱" in entry["text"]
    assert "RSI" in entry["text"]


def test_build_chart_html_no_markers_without_backtest() -> None:
    df = _df(5)

    payload = _payload(build_chart_html(df, []))

    assert payload["markers"] == []


def test_rsi_points_null_during_warmup_then_populated() -> None:
    df = _df(30)

    payload = _payload(build_chart_html(df, []))

    rsi_points = payload["rsi"]
    assert len(rsi_points) == 30  # type: ignore[arg-type]
    assert all(p is None for p in rsi_points[:_RSI_LENGTH])  # type: ignore[index]
    assert any(p is not None for p in rsi_points[_RSI_LENGTH:])  # type: ignore[index]


def test_initial_bars_capped_to_available_candles() -> None:
    df = _df(5)

    payload = _payload(build_chart_html(df, [], initial_bars=1_500))

    assert payload["initialBars"] == 5


def test_build_chart_html_adds_tp_line_overlays_from_conf_params() -> None:
    df = _df(30)
    # 차트에 그리는 선은 display_ema_lengths(익절 판정선 tp_ema_lengths와 별개, WAN-66).
    conf_params = ConfluenceParams(display_ema_lengths=(20,), tp_vwma_length=None)

    payload = _payload(build_chart_html(df, [], conf_params=conf_params))

    lines = payload["lines"]
    assert len(lines) == 1  # type: ignore[arg-type]
    line = lines[0]  # type: ignore[index]
    assert line["key"] == "ema_20"
    assert line["label"] == "EMA 20"
    assert len(line["points"]) == 30


def test_build_chart_html_no_conf_params_means_no_lines() -> None:
    payload = _payload(build_chart_html(_df(10), []))

    assert payload["lines"] == []


def test_build_chart_html_visible_lines_filters_overlay() -> None:
    df = _df(30)
    conf_params = ConfluenceParams(display_ema_lengths=(20, 60), tp_vwma_length=100)

    payload = _payload(
        build_chart_html(df, [], conf_params=conf_params, visible_lines=frozenset({"ema_20"}))
    )

    lines = payload["lines"]
    assert isinstance(lines, list)
    keys = {line["key"] for line in lines}
    assert keys == {"ema_20"}


def test_exit_marker_labels_touched_ema_line_and_pnl() -> None:
    df = _df(30)
    conf_params = ConfluenceParams(display_ema_lengths=(20,), tp_vwma_length=None)
    frame = df.sort_values("open_time").reset_index(drop=True)
    ema_20 = ema(frame, length=20)
    exit_time = 25 * _STEP
    tp_price = float(ema_20.iloc[25])

    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=20 * _STEP,
        price=100.0,
        order_block=_order_block(),
        status="active",
        planned_exit=PlannedExit(
            time=exit_time, price=tp_price, reason=SignalExitReason.TAKE_PROFIT
        ),
    )
    backtest = run_backtest(df, [signal], BacktestConfig())

    payload = _payload(build_chart_html(df, [], backtest, [signal], conf_params=conf_params))

    markers = payload["markers"]
    assert len(markers) >= 1  # type: ignore[arg-type]
    exit_marker = markers[-1]  # type: ignore[index]
    text = exit_marker["text"]
    assert "익절 · EMA 20" in text
    assert "%" in text


def test_default_theme_is_dark() -> None:
    payload = _payload(build_chart_html(_df(5), []))

    theme = payload["theme"]
    assert isinstance(theme, dict)
    assert theme["background"] == "#131722"
    assert theme["textColor"] == "#d1d4dc"
    # RSI/격자/범례 색도 다크로 함께 온다 — 흰 배경이 남는 영역이 없어야 한다.
    assert payload["rsiColor"] == "#b39ddb"
    assert "70, 74, 86" in theme["gridColor"]
    assert "30, 34, 45" in theme["legendBg"]


def test_light_theme_overrides_all_surfaces() -> None:
    payload = _payload(build_chart_html(_df(5), [], theme="light"))

    theme = payload["theme"]
    assert isinstance(theme, dict)
    assert theme["background"] == "#ffffff"
    assert theme["textColor"] == "#333333"
    assert payload["rsiColor"] == "#7e57c2"
    assert theme["legendText"] == "#333333"


def test_zone_fill_follows_theme() -> None:
    df = _df(10)
    active = _order_block(breaker=False)

    dark_box = _payload(build_chart_html(df, [active]))["boxes"][0]  # type: ignore[index]
    light_box = _payload(build_chart_html(df, [active], theme="light"))["boxes"][0]  # type: ignore[index]

    assert dark_box["fill"] != light_box["fill"]
    assert light_box["fill"] == "rgba(38, 166, 154, 0.20)"


def test_markers_follow_theme() -> None:
    df = _df(30)
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=20 * _STEP,
        price=100.0,
        order_block=_order_block(),
        status="active",
    )
    backtest = run_backtest(df, [signal], BacktestConfig(take_profit_pct=0.5))

    dark_entry = _payload(build_chart_html(df, [], backtest))["markers"][0]  # type: ignore[index]
    light_entry = _payload(build_chart_html(df, [], backtest, theme="light"))["markers"][0]  # type: ignore[index]

    assert dark_entry["color"] == "#42a5f5"
    assert light_entry["color"] == "#1e88e5"


def test_exit_marker_labels_stop_loss_as_order_block_invalidation() -> None:
    df = _df(30)
    ob = _order_block(top=95.0, bottom=90.0)
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=10 * _STEP,
        price=100.0,
        order_block=ob,
        status="active",
        planned_exit=PlannedExit(time=15 * _STEP, price=92.0, reason=SignalExitReason.STOP_LOSS),
    )
    backtest = run_backtest(df, [signal], BacktestConfig())

    payload = _payload(build_chart_html(df, [], backtest, [signal]))

    markers = payload["markers"]
    assert len(markers) >= 1  # type: ignore[arg-type]
    exit_marker = markers[-1]  # type: ignore[index]
    text = exit_marker["text"]
    assert "손절 · OB 무효화" in text
    assert "R" in text
