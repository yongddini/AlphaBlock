"""dashboard.lightweight_chart 테스트.

브라우저 렌더링(픽셀) 비교는 범위 밖. HTML에 임베드된 JSON 페이로드(캔들·존
박스·RSI·마커)의 구조와 값을 검증한다(backtest 테스트와 동일한 최소 픽스처).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, ExitReason, PositionSide
from dashboard.lightweight_chart import (
    _BAND_LINE_WIDTH,
    _EMA_LENGTH_COLORS_DARK,
    _EMA_LINE_PALETTE,
    _INITIAL_BARS,
    _MA_LINE_WIDTH,
    _RSI_LENGTH,
    BAND_LINE_COLOR,
    _exit_marker_text,
    build_chart_html,
)
from dashboard.live_chart import LiveChartConfig, build_live_config
from strategy.confluence import ConfluenceStrategy
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


def test_markers_are_text_free_arrows() -> None:
    """WAN-146: 마커는 화살표만 — 진입의 RSI 문구도, 청산의 손익 문구도 없다."""
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
    assert isinstance(markers, list)
    assert len(markers) >= 2
    assert all("text" not in m for m in markers)
    # 롱: 진입은 캔들 밑에서 위를 찌르고(현행 위치 유지), 청산은 캔들 위에서 아래를 찌른다.
    entry, exit_marker = markers[0], markers[-1]
    assert (entry["position"], entry["shape"]) == ("belowBar", "arrowUp")
    assert (exit_marker["position"], exit_marker["shape"]) == ("aboveBar", "arrowDown")


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


def test_exit_marker_text_still_labels_touched_ema_line_and_pnl() -> None:
    """WAN-146: 화면에서는 뺐지만 텍스트 조립 함수(와 그 R 배수 계약)는 살아 있다."""
    df = _df(30)
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

    text = _exit_marker_text(
        tp_price,
        ExitReason.TAKE_PROFIT,
        PositionSide.LONG,
        100.0,
        signal,
        {"ema_20": {exit_time: tp_price}},
    )

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


def _line_colors(payload: dict[str, object]) -> dict[str, str]:
    lines = payload["lines"]
    assert isinstance(lines, list)
    return {line["key"]: line["color"] for line in lines}


def test_ema_colors_are_fixed_by_length_not_order() -> None:
    """WAN-67: 20 빨강 / 60 주황 / 120 노랑 / 240 초록 / 365 파랑 (다크 기준)."""
    df = _df(30)
    conf_params = ConfluenceParams(display_ema_lengths=(20, 60, 120, 240, 365), tp_vwma_length=None)

    colors = _line_colors(_payload(build_chart_html(df, [], conf_params=conf_params)))

    assert colors == {
        "ema_20": "#ff5252",
        "ema_60": "#ff9800",
        "ema_120": "#ffee58",
        "ema_240": "#4caf50",
        "ema_365": "#42a5f5",
    }
    # 색 5개가 서로 다르다(같은 값이 겹치면 눈으로 선을 못 가른다).
    assert len(set(colors.values())) == 5


def test_ema_color_survives_dropping_a_length() -> None:
    """목록에서 하나를 빼도 남은 선의 색은 그대로다 — 순번 매핑이면 밀린다."""
    df = _df(30)
    full = ConfluenceParams(display_ema_lengths=(20, 60, 120), tp_vwma_length=None)
    dropped = ConfluenceParams(display_ema_lengths=(60, 120), tp_vwma_length=None)

    full_colors = _line_colors(_payload(build_chart_html(df, [], conf_params=full)))
    dropped_colors = _line_colors(_payload(build_chart_html(df, [], conf_params=dropped)))

    assert dropped_colors["ema_60"] == full_colors["ema_60"]
    assert dropped_colors["ema_120"] == full_colors["ema_120"]


def test_unspecified_ema_length_falls_back_to_index_palette() -> None:
    """스펙에 없는 길이를 넣어도 렌더가 깨지지 않는다(폴백 팔레트)."""
    df = _df(30)
    conf_params = ConfluenceParams(display_ema_lengths=(7, 60), tp_vwma_length=None)

    colors = _line_colors(_payload(build_chart_html(df, [], conf_params=conf_params)))

    assert colors["ema_60"] == "#ff9800"  # 스펙 길이는 그대로 고정색
    assert colors["ema_7"] in _EMA_LINE_PALETTE  # 스펙 밖 길이는 순번 팔레트
    assert colors["ema_7"] != colors["ema_60"]


def test_vwma_is_white_on_dark_and_dark_on_light() -> None:
    """사용자 스펙(2026-07-20): VWMA는 다크에서 흰색. 라이트에선 배경 대비 중립색."""
    df = _df(30)
    conf_params = ConfluenceParams(display_ema_lengths=(20,), tp_vwma_length=100)

    dark = _line_colors(_payload(build_chart_html(df, [], conf_params=conf_params)))
    light = _line_colors(_payload(build_chart_html(df, [], conf_params=conf_params, theme="light")))

    assert dark["vwma_100"] == "#ffffff"
    assert light["vwma_100"] == "#212121"
    # EMA 5색·볼린저 시안 어느 것과도 겹치지 않는다.
    assert dark["vwma_100"] not in set(_EMA_LENGTH_COLORS_DARK.values()) | {BAND_LINE_COLOR}


def test_ema_colors_differ_between_themes_but_keep_hue_order() -> None:
    df = _df(30)
    conf_params = ConfluenceParams(display_ema_lengths=(20, 365), tp_vwma_length=None)

    dark = _line_colors(_payload(build_chart_html(df, [], conf_params=conf_params)))
    light = _line_colors(_payload(build_chart_html(df, [], conf_params=conf_params, theme="light")))

    assert dark["ema_20"] != light["ema_20"]
    assert light["ema_20"] == "#d32f2f"
    assert light["ema_365"] == "#1565c0"


def test_display_line_width_is_bumped() -> None:
    """WAN-67: 표시선 굵기 상향. RSI 패인의 선은 그대로 1이다."""
    html = build_chart_html(_df(5), [])

    assert _MA_LINE_WIDTH >= 2
    assert "__MA_LINE_WIDTH__" not in html
    assert f"lineWidth: {_MA_LINE_WIDTH}," in html


def test_band_line_is_thinner_than_moving_averages() -> None:
    """WAN-67 사용자 지시: 볼린저 하단선은 이동평균선보다 얇게."""
    html = build_chart_html(_df(5), [])

    assert _BAND_LINE_WIDTH < _MA_LINE_WIDTH
    assert "__BAND_LINE_WIDTH__" not in html


def test_candles_are_white_up_red_down_on_dark() -> None:
    """WAN-67 사용자 스펙: 상승 캔들 하양 · 하락 캔들 빨강."""
    payload = _payload(build_chart_html(_df(5), []))

    colors = payload["priceColors"]
    assert isinstance(colors, dict)
    assert colors["up"] == "#ffffff"
    assert colors["down"] == "#ef5350"
    # 다크 배경에서는 흰 몸통이 그대로 보이므로 테두리를 켜지 않는다.
    assert colors["borderVisible"] is False


def test_light_theme_keeps_white_body_but_adds_border() -> None:
    """흰 배경 + 흰 몸통이라 테두리 없이는 상승봉이 사라진다 — 색이 아니라 형태로 대응."""
    colors = _payload(build_chart_html(_df(5), [], theme="light"))["priceColors"]

    assert isinstance(colors, dict)
    assert colors["up"] == "#ffffff"  # 몸통 색 스펙은 라이트에서도 유지
    assert colors["borderVisible"] is True
    assert colors["borderUp"] != colors["up"]


def test_live_bar_colors_follow_candle_theme() -> None:
    """형성 중인 봉도 캔들 색을 따라간다 — 확정봉과 색이 갈리면 안 된다(WAN-147 유지)."""
    df = _df(30)
    config = _live_config(df)  # 아래 WAN-147 절의 헬퍼(호출 시점에 해석된다)

    dark = _payload(build_chart_html(df, [], live=config))["live"]
    light = _payload(build_chart_html(df, [], live=config, theme="light"))["live"]

    assert isinstance(dark, dict) and isinstance(light, dict)
    assert dark["liveColors"] == {
        "up": "rgba(255, 255, 255, 0.45)",
        "down": "rgba(239, 83, 80, 0.45)",
    }
    # 라이트에서는 옅은 흰색이 배경에 묻히므로 회색으로 내린다.
    assert light["liveColors"]["up"] != dark["liveColors"]["up"]


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


def test_exit_marker_text_labels_stop_loss_as_order_block_invalidation() -> None:
    ob = _order_block(top=95.0, bottom=90.0)
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=10 * _STEP,
        price=100.0,
        order_block=ob,
        status="active",
        planned_exit=PlannedExit(time=15 * _STEP, price=92.0, reason=SignalExitReason.STOP_LOSS),
    )

    text = _exit_marker_text(92.0, ExitReason.STOP_LOSS, PositionSide.LONG, 100.0, signal, {})

    assert "손절 · OB 무효화" in text
    # R 배수의 리스크 기준은 오더블록 무효화 경계(롱=존 하단 90) — 진입가 100 대비 10%.
    assert "R-0.80" in text


# --- 볼린저 하단선 + 실시간 갱신 (WAN-147) -----------------------------------


def _live_config(df: pd.DataFrame) -> LiveChartConfig:
    config = build_live_config(
        df,
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        conf_params=ConfluenceParams(),
        band_color=BAND_LINE_COLOR,
    )
    assert config is not None
    return config


def test_band_points_match_strategy_definition() -> None:
    """차트에 그리는 밴드 값 == 전략이 실제로 쓰는 `deviation_band_at` (WAN-147)."""
    df = _df(40)
    conf_params = ConfluenceParams()
    filter_params = conf_params.deviation_filter
    assert filter_params is not None

    payload = _payload(build_chart_html(df, [], conf_params=conf_params))
    band = payload["band"]
    assert isinstance(band, dict)

    anchor_vals, width_vals = ConfluenceStrategy.deviation_filter_components(
        df, filter_params, conf_params.source
    )
    for pos, point in enumerate(band["points"]):
        expected = ConfluenceStrategy.deviation_band_at(pos, 1, anchor_vals, width_vals)
        if expected is None:
            assert point is None
        else:
            assert point is not None
            assert point["value"] == pytest.approx(expected, abs=5e-3)


def test_band_absent_when_deviation_filter_off() -> None:
    payload = _payload(
        build_chart_html(_df(40), [], conf_params=ConfluenceParams(deviation_filter=None))
    )
    assert payload["band"] is None


def test_live_payload_absent_by_default() -> None:
    """기존 화면·기존 테스트는 그대로다 — 라이브는 명시적으로 켤 때만 실린다."""
    payload = _payload(build_chart_html(_df(40), [], conf_params=ConfluenceParams()))
    assert payload["live"] is None


def test_live_payload_carries_stream_and_band_seed() -> None:
    df = _df(40)
    payload = _payload(
        build_chart_html(df, [], conf_params=ConfluenceParams(), live=_live_config(df))
    )
    live = payload["live"]
    assert isinstance(live, dict)
    assert live["streamUrl"] == "wss://fstream.binance.com/ws/btcusdt@kline_1h"
    assert live["lastBarOpenMs"] == int(df["open_time"].iloc[-1])
    assert isinstance(live["bandCloses"], list) and len(live["bandCloses"]) == 19
    assert live["bandParams"]["directionSign"] == 1


def test_live_band_disabled_when_band_line_not_drawn() -> None:
    """밴드 선을 안 그리는 화면에서는 라이브 밴드 배선도 꺼진다(조용한 실패 방지)."""
    df = _df(40)
    payload = _payload(
        build_chart_html(
            df,
            [],
            conf_params=ConfluenceParams(deviation_filter=None),
            live=_live_config(df),
        )
    )
    live = payload["live"]
    assert isinstance(live, dict)
    assert live["bandCloses"] is None
    assert live["bandParams"] is None
    assert payload["band"] is None


# --- 표 행 선택 → 차트 구간 점프 (WAN-146) -----------------------------------


def test_focus_absent_by_default() -> None:
    payload = _payload(build_chart_html(_df(10), []))

    assert payload["focus"] is None


def test_focus_spans_the_trade_with_padding_on_both_sides() -> None:
    """점(點)이 아니라 구간이다 — 진입~청산 전체 + 앞뒤 여유가 화면에 들어와야 한다."""
    df = _df(30)

    payload = _payload(build_chart_html(df, [], focus=(10 * _STEP, 14 * _STEP)))

    focus = payload["focus"]
    assert isinstance(focus, dict)
    assert focus["from"] < 10 * _STEP // 1000
    assert focus["to"] > 14 * _STEP // 1000


def test_focus_pads_even_when_entry_and_exit_share_a_bar() -> None:
    """같은 봉에서 진입·청산한 거래도 여유가 0이 되면 안 된다(빈 구간 점프 방지)."""
    df = _df(30)

    payload = _payload(build_chart_html(df, [], focus=(10 * _STEP, 10 * _STEP)))

    focus = payload["focus"]
    assert isinstance(focus, dict)
    assert focus["to"] - focus["from"] >= 2 * _STEP // 1000


def test_focus_older_than_initial_render_window_still_has_candles_loaded() -> None:
    """🚨 지연 로딩 범위 밖(오래된) 거래를 골라도 빈 화면이 되면 안 된다.

    캔들은 처음부터 전량 임베드되고 초기 렌더만 `initialBars`로 제한되므로, JS가
    `payload.focus.from`까지 로드 범위를 넓힐 수 있어야 한다 — 그 구간의 캔들이
    페이로드에 실제로 들어 있는지(그리고 초기 창 밖인지) 확인한다.
    """
    df = _df(60)
    initial_bars = 10
    focus_ms = 5 * _STEP

    payload = _payload(
        build_chart_html(df, [], initial_bars=initial_bars, focus=(focus_ms, 6 * _STEP))
    )

    candles = payload["candles"]
    assert isinstance(candles, list)
    assert payload["initialBars"] == initial_bars
    first_rendered = candles[len(candles) - initial_bars]["time"]
    assert first_rendered > focus_ms // 1000  # 초기 창 밖 = 확장이 필요한 상황
    assert candles[0]["time"] <= focus_ms // 1000  # 그래도 캔들은 실려 있다


def test_focus_freezes_auto_shift_on_new_live_bar() -> None:
    """과거 구간을 보는 중에 라이브 새 봉이 화면을 최신으로 끌어가면 안 된다."""
    df = _df(30)

    focused = build_chart_html(df, [], focus=(5 * _STEP, 6 * _STEP))
    plain = build_chart_html(df, [])

    assert "shiftVisibleRangeOnNewBar: !payload.focus" in focused
    assert _payload(focused)["focus"] is not None
    assert _payload(plain)["focus"] is None


def test_default_initial_bars_constant_is_used_when_not_overridden() -> None:
    payload = _payload(build_chart_html(_df(3), []))

    assert payload["initialBars"] == min(_INITIAL_BARS, 3)


def test_chart_script_is_valid_javascript() -> None:
    """템플릿 치환 결과가 파싱 가능한 JS인지 Node로 확인한다(오타 방지)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node가 없어 JS 문법을 검증할 수 없다")
    df = _df(40)
    html = build_chart_html(df, [], conf_params=ConfluenceParams(), live=_live_config(df))
    assert "__LIVE_BAND_JS__" not in html
    assert "computeLiveBand" in html
    # 마지막 <script> 블록이 이 모듈이 쓴 코드다(그 앞은 벤더링된 라이브러리).
    script = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chart.js"
        path.write_text(script, encoding="utf-8")
        subprocess.run([node, "--check", str(path)], check=True, capture_output=True)  # noqa: S603
