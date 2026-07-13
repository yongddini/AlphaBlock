"""캔들 + 오더블록 + RSI 서브패널 — TradingView Lightweight Charts 임베드 (WAN-54).

`dashboard.charts`(Plotly)는 3,000개 이상의 존을 `add_shape`로 그려 줌·팬마다
전량 재계산돼 브라우저가 멈췄고, 조작감도 트레이딩뷰와 달랐다(WAN-52). 이 모듈은
TradingView가 오픈소스로 공개한 캔들 엔진 `lightweight-charts`(Apache-2.0, JS)를
`st.components.v1.html`로 직접 임베드해 대체한다.

## 왜 `streamlit-lightweight-charts` 패키지가 아니라 직접 임베드인가

그 패키지는 v3/v4 시절 옵션 스키마를 감싼 래퍼라 v5의 네이티브 멀티패인
API(`chart.addSeries(Type, opts, paneIndex)`)와 `subscribeVisibleLogicalRangeChange`
콜백을 그대로 노출하지 않는다. RSI 패널 시간축 동기화와 좌측 끝 지연 로딩이
핵심 요구사항이라, 이슈에 명시된 대로("래퍼가 콜백을 막으면 직접 임베드") JS
라이브러리(v5.2.0, `dashboard/static/`에 벤더링)를 직접 임베드한다.

## 지연 로딩 설계: "사전 청크 적재" (컴포넌트 이벤트 대신)

Streamlit의 매 위젯 조작마다 스크립트를 처음부터 재실행하는 모델과, 좌측 끝
스크롤마다 서버 왕복이 필요한 "컴포넌트 이벤트" 방식은 궁합이 나쁘다(왕복마다
전체 스크립트 재실행 + 재계산 비용). 대신 이 모듈은 선택된 기간의 전체 캔들을
**한 번에 HTML에 임베드**하고, 초기에는 최근 `initial_bars`개만 `setData`로
캔버스에 그리게 한 뒤, 좌측 끝 스크롤 시 이미 임베드된 배열에서 다음 청크를
**클라이언트 사이드에서만** 이어붙인다(서버 왕복 없음). 트레이딩뷰 전체
아카이브(3년 15m ≈ 10~20MB JSON)를 한 번 전송하는 비용은 로컬 실행형
대시보드(외부 노출 없음)에서 감내 가능하고, 초기 **렌더**(캔버스에 그리는 봉 수)는
`initial_bars`로 제한돼 완료 기준의 "2초 이내" 목표를 캔들 수 상한으로 보장한다.

## 오더블록 박스 렌더링: 캔버스 프리미티브 하나로 전부

처음엔 존마다 `BaselineSeries`(기준값 위/아래로 다른 색을 채우는 시리즈, `baseValue`를
존의 `bottom`으로 두고 `[{time:start,value:top},{time:end,value:top}]` 두 점만 주면
정확히 그 구간의 사각형이 되는 성질)를 하나씩 만들어 그렸다. 그런데 기본 필터
(진입+활성)만으로도 실제 3년 15m 데이터에서 존이 2,000개를 넘어, **시리즈
2,000개를 추가할 때마다 차트가 전체 시리즈에 대해 오토스케일을 재계산**해 브라우저
탭이 응답 불능이 됐다 — Plotly `add_shape`가 겪은 것과 같은 부류의 O(n²) 문제가
엔진만 바뀐 채 재발한 것이다(로컬에서 3년치 실데이터로 재현·확인함).

그래서 최종적으로는 시리즈를 전혀 늘리지 않는다. 캔들 시리즈에
`ISeriesPrimitive` **하나**(`attachPrimitive`)를 붙이고, 그 프리미티브의
`renderer().draw()` 안에서 모든 존을 순회하며 `series.priceToCoordinate`/
`chart.timeScale().timeToCoordinate`로 좌표를 구해 캔버스에 직접
`fillRect`/`strokeRect`로 그린다(TradingView 공식 plugin-examples의
rectangle-drawing-tool과 같은 패턴). 존이 몇 개든 시리즈 개수는 1개로 고정되고,
줌·팬마다 다시 그리는 비용은 캔버스 그리기 명령 수천 번(수 ms)뿐이라 시리즈
누적 문제가 구조적으로 사라진다.

## RSI 서브패널

v5 네이티브 멀티패인(`addSeries(..., paneIndex=1)`)을 쓰면 시간축이 한 차트
인스턴스 안에서 자동 동기화되고 크로스헤어도 패인 전체에 걸쳐 하나로 그려진다
(WAN-52 시절의 "별도 차트 인스턴스 + 수동 이벤트 동기화"보다 단순하고 정확하다).

## 이 모듈이 다루지 않는 것

`dashboard.pipeline`(탐지·백테스트 계산), `dashboard.charts`의
`ZoneCategory`/`filter_zones`/`entered_zone_keys`(표시 필터 분류 — 렌더 엔진과
무관해 그대로 재사용) — 모두 건드리지 않는다. `build_equity_chart`(단순 자본곡선,
점 개수가 적어 성능 문제가 없음)도 Plotly로 남긴다.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from backtest.models import BacktestResult, ExitReason, PositionSide
from strategy.indicators import emas as compute_emas
from strategy.indicators import rsi as compute_rsi
from strategy.indicators import vwma as compute_vwma
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockSignal,
    PlannedExit,
    SignalExitReason,
)

#: 캔들 몸통(강세/약세) 색. 트레이딩뷰 기본값이라 밝은/어두운 배경 양쪽에서 잘 보여
#: 테마와 무관하게 공유한다.
_BULL_COLOR = "#26a69a"
_BEAR_COLOR = "#ef5350"


@dataclass(frozen=True)
class ChartTheme:
    """차트 색 팔레트 한 벌(WAN-55).

    배경·격자·글자·범례·오더블록 존·RSI·진입/청산 마커 색을 밝은/어두운 배경 각각에
    맞춰 뽑아 둔 것이다. 값을 상수에 흩뿌리지 않고 이 객체 하나로 모아, 렌더 함수들이
    테마만 바꿔 끼우면 되도록 한다. 다크 값은 트레이딩뷰 기본 다크에 준한다.
    """

    name: str
    background: str
    text_color: str
    grid_color: str
    legend_bg: str
    legend_text: str
    bull_zone_line: str
    bear_zone_line: str
    bull_zone_fill: str
    bear_zone_fill: str
    #: breaker(무효화)로 전환됐던 존은 옅게 칠해 "깨졌던 것"임을 구분한다.
    bull_zone_fill_faded: str
    bear_zone_fill_faded: str
    rsi_line: str
    rsi_guide: str
    entry_marker: str
    exit_take_profit: str
    exit_stop_loss: str
    exit_end_of_data: str
    exit_default: str

    def exit_marker_colors(self) -> dict[ExitReason, str]:
        return {
            ExitReason.TAKE_PROFIT: self.exit_take_profit,
            ExitReason.PARTIAL_TAKE_PROFIT: self.exit_take_profit,
            ExitReason.STOP_LOSS: self.exit_stop_loss,
            ExitReason.END_OF_DATA: self.exit_end_of_data,
        }


_LIGHT_THEME = ChartTheme(
    name="light",
    background="#ffffff",
    text_color="#333333",
    grid_color="rgba(220, 220, 220, 0.5)",
    legend_bg="rgba(255, 255, 255, 0.85)",
    legend_text="#333333",
    bull_zone_line="rgba(38, 166, 154, 0.9)",
    bear_zone_line="rgba(239, 83, 80, 0.9)",
    bull_zone_fill="rgba(38, 166, 154, 0.20)",
    bear_zone_fill="rgba(239, 83, 80, 0.20)",
    bull_zone_fill_faded="rgba(38, 166, 154, 0.09)",
    bear_zone_fill_faded="rgba(239, 83, 80, 0.09)",
    rsi_line="#7e57c2",
    rsi_guide="rgba(120, 120, 120, 0.55)",
    entry_marker="#1e88e5",
    exit_take_profit="#2e7d32",
    exit_stop_loss="#c62828",
    exit_end_of_data="#616161",
    exit_default="#6d4c41",
)

#: 다크 테마: 트레이딩뷰 기본 다크에 준한다(배경 #131722, 글자 #d1d4dc, 격자
#: rgba(70,74,86,0.4)). 밝은 테마에서 고른 진한 색(진입 파랑·청산 초록/빨강/회색·RSI
#: 보라)은 어두운 배경에서 묻혀, 명도를 높인 값으로 분리한다. 존 채움은 어두운
#: 배경에서 더 묻히므로 알파를 약간 올린다.
_DARK_THEME = ChartTheme(
    name="dark",
    background="#131722",
    text_color="#d1d4dc",
    grid_color="rgba(70, 74, 86, 0.4)",
    legend_bg="rgba(30, 34, 45, 0.85)",
    legend_text="#d1d4dc",
    bull_zone_line="rgba(38, 166, 154, 0.9)",
    bear_zone_line="rgba(239, 83, 80, 0.9)",
    bull_zone_fill="rgba(38, 166, 154, 0.22)",
    bear_zone_fill="rgba(239, 83, 80, 0.22)",
    bull_zone_fill_faded="rgba(38, 166, 154, 0.12)",
    bear_zone_fill_faded="rgba(239, 83, 80, 0.12)",
    rsi_line="#b39ddb",
    rsi_guide="rgba(150, 150, 150, 0.5)",
    entry_marker="#42a5f5",
    exit_take_profit="#66bb6a",
    exit_stop_loss="#ef5350",
    exit_end_of_data="#9e9e9e",
    exit_default="#a1887f",
)

_THEMES: dict[str, ChartTheme] = {"light": _LIGHT_THEME, "dark": _DARK_THEME}

#: 기본 테마는 다크다(사용자의 실제 트레이딩뷰 사용 환경 — WAN-55).
DEFAULT_THEME = "dark"


def resolve_theme(name: str | None) -> ChartTheme:
    """테마 이름(`"light"`/`"dark"`)을 팔레트로 바꾼다. 미상이면 기본(다크)."""
    return _THEMES.get(name or DEFAULT_THEME, _DARK_THEME)


#: 초기(첫 렌더) 캔버스에 그릴 봉 수. 완료 기준("2초 이내 초기 렌더")을 만족시키는
#: 상한. 좌측 끝으로 스크롤하면 이 크기만큼 청크가 더 붙는다(WAN-54).
_INITIAL_BARS = 1_500

_RSI_LENGTH = 14
_RSI_OVERBOUGHT = 70.0
_RSI_MIDLINE = 50.0
_RSI_OVERSOLD = 30.0
#: 캔들:RSI 패인 높이 비율 ≈ 3:1.
_RSI_PANE_HEIGHT_RATIO = 0.25

_LINE_STYLE_DOTTED = 1
_LINE_STYLE_DASHED = 2

#: 익절 목표선(EMA) 오버레이 색 팔레트. 길이 순서대로 순환 배정한다(WAN-59 후속:
#: 사용자가 `tp_ema_lengths`를 바꿔도 팔레트가 무너지지 않도록 길이가 아닌 순번으로 매핑).
#: 밝은/어두운 배경 양쪽에서 식별 가능하도록 중간 채도·명도를 고른다(WAN-55 대비 고려).
_EMA_LINE_PALETTE: tuple[str, ...] = (
    "#2962ff",  # blue
    "#f9a825",  # amber
    "#ab47bc",  # purple
    "#ff7043",  # deep orange
    "#6d4c41",  # brown
    "#00838f",  # teal
    "#7cb342",  # olive green
)
_VWMA_LINE_COLOR = "#d81b60"  # magenta — VWMA는 항상 이 색으로 고정해 EMA들과 구분.

_STATIC_DIR = Path(__file__).parent / "static"
_LIBRARY_JS_PATH = _STATIC_DIR / "lightweight-charts.standalone.production.js"


@lru_cache(maxsize=1)
def _load_library_js() -> str:
    """벤더링된 Lightweight Charts 표준판 번들을 읽는다(CDN 의존 없음, 오프라인 동작)."""
    return _LIBRARY_JS_PATH.read_text(encoding="utf-8")


def _zone_span_end(ob: OrderBlock, last_bar_ms: int) -> int:
    """존 박스의 오른쪽 변(수명 종료 시점) ms를 구한다.

    무효화(`break_time`)나 소멸(`swept_time`)이 있으면 그 시점에서 끝나고, 아직
    살아있으면 마지막 봉까지 늘인다.
    """
    if ob.break_time is not None:
        return ob.break_time
    if ob.swept_time is not None:
        return ob.swept_time
    return last_bar_ms


def _zone_boxes(
    order_blocks: Sequence[OrderBlock], last_bar_ms: int, theme: ChartTheme
) -> list[dict[str, object]]:
    """존마다 `BaselineSeries` 두 점 + 스타일 정보를 담은 dict를 만든다."""
    boxes: list[dict[str, object]] = []
    for ob in order_blocks:
        is_bull = ob.direction is OrderBlockDirection.BULLISH
        faded = ob.breaker
        start = ob.start_time
        end = max(_zone_span_end(ob, last_bar_ms), start)
        if is_bull:
            fill = theme.bull_zone_fill_faded if faded else theme.bull_zone_fill
            line = theme.bull_zone_line
        else:
            fill = theme.bear_zone_fill_faded if faded else theme.bear_zone_fill
            line = theme.bear_zone_line
        boxes.append(
            {
                "start": start // 1000,
                "end": end // 1000,
                "top": ob.top,
                "bottom": ob.bottom,
                "fill": fill,
                "line": line,
                "dashed": faded,
            }
        )
    return boxes


def _rsi_at(rsi_by_time: dict[int, float], time_ms: int) -> float | None:
    value = rsi_by_time.get(time_ms)
    if value is None or math.isnan(value):
        return None
    return value


def _line_label(key: str) -> str:
    """`ema_120`/`vwma_100` 같은 내부 키를 화면 표시용 라벨(`EMA 120`)로 바꾼다."""
    kind, _, length = key.partition("_")
    return f"{kind.upper()} {length}"


def _signals_by_trigger_time(
    signals: Sequence[OrderBlockSignal],
) -> dict[int, OrderBlockSignal]:
    """활성 시그널을 진입 봉 시각(`trigger_time`) 기준으로 찾아볼 수 있게 색인한다.

    `BacktestEngine`이 `sig.trigger_time == t`에서 포지션을 여는 것과 대응되므로
    (`backtest/engine.py::run`), `Trade.entry_time`으로 이 색인을 찾으면 그 거래를
    발생시킨 오더블록·계획 청산(`PlannedExit`)을 역추적할 수 있다.
    """
    return {s.trigger_time: s for s in signals if s.status == "active"}


def _touched_line_label(
    planned: PlannedExit | None, tp_lines_by_time: dict[str, dict[int, float]]
) -> str | None:
    """익절 시 실제로 닿은 EMA/VWMA 선의 라벨을 찾는다.

    `PlannedExit.price`는 `ConfluenceStrategy`가 익절 목표선의 값을 그대로 옮겨 실은
    것이므로(수수료·슬리피지 반영 전), 그 시각(`planned.time`)의 선 값들과 정확히
    일치하는 키를 찾으면 "어느 선인지"를 재구성할 수 있다.
    """
    if planned is None or planned.reason is not SignalExitReason.TAKE_PROFIT:
        return None
    for key, series_by_time in tp_lines_by_time.items():
        value = series_by_time.get(planned.time)
        if value is None or math.isnan(value):
            continue
        if math.isclose(value, planned.price, rel_tol=1e-9, abs_tol=1e-9):
            return _line_label(key)
    return None


def _fmt_price(price: float) -> str:
    return f"{price:,.1f}"


def _exit_marker_text(
    fill_price: float,
    fill_reason: ExitReason,
    trade_side: PositionSide,
    entry_price: float,
    signal: OrderBlockSignal | None,
    tp_lines_by_time: dict[str, dict[int, float]],
) -> str:
    """청산 마커 텍스트: 사유(익절/손절/강제청산) · 닿은 선(익절) · 손익%· R 배수.

    R 배수의 리스크 기준은 오더블록 무효화 경계(`BacktestEngine._sizing_stop_price`와
    동일 규칙 — 롱은 존 하단, 숏은 존 상단)다. 체결가는 슬리피지·수수료가 반영된
    값이라 손익%는 근사치(수수료 제외 가격 기준)다.
    """
    pnl_pct = (
        trade_side.sign * (fill_price - entry_price) / entry_price * 100.0 if entry_price else 0.0
    )

    risk_pct: float | None = None
    if signal is not None and entry_price:
        is_long = trade_side is PositionSide.LONG
        stop_ref = signal.order_block.bottom if is_long else signal.order_block.top
        candidate = abs(entry_price - stop_ref) / entry_price * 100.0
        risk_pct = candidate if candidate > 1e-9 else None
    r_text = f" · R{pnl_pct / risk_pct:+.2f}" if risk_pct is not None else ""

    planned = signal.planned_exit if signal is not None else None
    if fill_reason in (ExitReason.TAKE_PROFIT, ExitReason.PARTIAL_TAKE_PROFIT):
        line_label = _touched_line_label(planned, tp_lines_by_time)
        head = f"익절 · {line_label}" if line_label else "익절"
    elif fill_reason is ExitReason.STOP_LOSS:
        head = "손절 · OB 무효화"
    else:
        head = "청산 · 데이터 종료"
    return f"{head} @ {_fmt_price(fill_price)} · {pnl_pct:+.2f}%{r_text}"


def _entry_exit_markers(
    backtest: BacktestResult,
    rsi_by_time: dict[int, float],
    signals: Sequence[OrderBlockSignal],
    tp_lines_by_time: dict[str, dict[int, float]],
    theme: ChartTheme,
) -> list[dict[str, object]]:
    """진입/청산 마커. 진입 마커는 방향·RSI를, 청산 마커는 사유·닿은 선·손익%·R을 담는다."""
    signal_by_entry = _signals_by_trigger_time(signals)
    exit_colors = theme.exit_marker_colors()
    markers: list[dict[str, object]] = []
    for trade in backtest.trades:
        is_long = trade.side is PositionSide.LONG
        rsi_value = _rsi_at(rsi_by_time, trade.entry_time)
        rsi_text = f"{rsi_value:.0f}" if rsi_value is not None else "—"
        markers.append(
            {
                "time": trade.entry_time // 1000,
                "position": "belowBar" if is_long else "aboveBar",
                "color": theme.entry_marker,
                "shape": "arrowUp" if is_long else "arrowDown",
                "text": f"{'롱' if is_long else '숏'} RSI{rsi_text}",
            }
        )
        signal = signal_by_entry.get(trade.entry_time)
        for fill in trade.exits:
            text = _exit_marker_text(
                fill.price, fill.reason, trade.side, trade.entry_price, signal, tp_lines_by_time
            )
            markers.append(
                {
                    "time": fill.time // 1000,
                    "position": "aboveBar" if is_long else "belowBar",
                    "color": exit_colors.get(fill.reason, theme.exit_default),
                    "shape": "circle",
                    "text": text,
                }
            )
    markers.sort(key=lambda m: int(m["time"]))  # type: ignore[call-overload]
    return markers


_TEMPLATE = """
<div id="__CONTAINER_ID__" style="width:100%;height:__HEIGHT__px;"></div>
<script>__LIBRARY_JS__</script>
<script>
(function () {
  const payload = __PAYLOAD_JSON__;
  const container = document.getElementById("__CONTAINER_ID__");
  if (!payload.candles.length) {
    container.innerHTML = '<div style="padding:2rem;color:#888;">표시할 데이터가 없습니다.</div>';
    return;
  }

  const theme = payload.theme;
  const chart = LightweightCharts.createChart(container, {
    autoSize: true,
    layout: { background: { type: "solid", color: theme.background }, textColor: theme.textColor },
    grid: {
      vertLines: { color: theme.gridColor },
      horzLines: { color: theme.gridColor },
    },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
  });

  const candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: payload.priceColors.up,
    downColor: payload.priceColors.down,
    borderVisible: false,
    wickUpColor: payload.priceColors.up,
    wickDownColor: payload.priceColors.down,
  }, 0);

  class OrderBlockBoxesPrimitive {
    constructor(boxes) {
      this._boxes = boxes;
      this._chart = null;
      this._series = null;
      this._paneViews = [
        {
          renderer: () => ({
            draw: (target) => {
              const chart = this._chart;
              const series = this._series;
              if (!chart || !series) return;
              target.useBitmapCoordinateSpace((scope) => {
                const ctx = scope.context;
                const timeScale = chart.timeScale();
                for (const box of this._boxes) {
                  const x1 = timeScale.timeToCoordinate(box.start);
                  const x2 = timeScale.timeToCoordinate(box.end);
                  const y1 = series.priceToCoordinate(box.top);
                  const y2 = series.priceToCoordinate(box.bottom);
                  if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
                  const left = Math.min(x1, x2) * scope.horizontalPixelRatio;
                  const right = Math.max(x1, x2) * scope.horizontalPixelRatio;
                  const top = Math.min(y1, y2) * scope.verticalPixelRatio;
                  const bottom = Math.max(y1, y2) * scope.verticalPixelRatio;
                  ctx.fillStyle = box.fill;
                  ctx.fillRect(left, top, right - left, bottom - top);
                  ctx.strokeStyle = box.line;
                  ctx.lineWidth = 1;
                  ctx.setLineDash(box.dashed ? [4, 3] : []);
                  ctx.strokeRect(left, top, right - left, bottom - top);
                }
              });
            },
          }),
        },
      ];
    }
    attached(param) {
      this._chart = param.chart;
      this._series = param.series;
    }
    detached() {
      this._chart = null;
      this._series = null;
    }
    updateAllViews() {}
    paneViews() {
      return this._paneViews;
    }
  }

  // 존 박스를 존마다 시리즈로 만들지 않고(수천 개면 시리즈별 오토스케일 재계산이
  // O(n²)로 쌓여 브라우저가 멈춘다 — WAN-52가 겪은 문제와 같은 종류) 캔버스
  // 프리미티브 하나가 전부를 그린다(WAN-54). fillRect/strokeRect 수천 번은
  // 시리즈 수천 개보다 훨씬 싸다.
  if (payload.boxes.length) {
    candleSeries.attachPrimitive(new OrderBlockBoxesPrimitive(payload.boxes));
  }

  // 익절 목표선(EMA/VWMA) 오버레이 — 사이드바 토글로 켜진 선만 payload.lines에 담겨
  // 온다(WAN-59 후속). 캔들 패인(0)에 얇은 LineSeries로 겹쳐 그린다.
  const lineSeriesList = [];
  (payload.lines || []).forEach(function (line) {
    const s = chart.addSeries(LightweightCharts.LineSeries, {
      color: line.color,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    }, 0);
    lineSeriesList.push({ series: s, points: line.points });
  });

  if (payload.lines && payload.lines.length) {
    const legend = document.createElement("div");
    legend.style.cssText =
      "position:absolute;top:8px;left:8px;z-index:5;background:" + theme.legendBg + ";" +
      "padding:4px 8px;border-radius:4px;font:11px -apple-system,sans-serif;" +
      "line-height:1.6;pointer-events:none;color:" + theme.legendText + ";";
    payload.lines.forEach(function (line) {
      const item = document.createElement("div");
      item.innerHTML =
        '<span style="display:inline-block;width:10px;height:2px;background:' +
        line.color +
        ';margin-right:6px;vertical-align:middle;"></span>' +
        line.label;
      legend.appendChild(item);
    });
    container.style.position = "relative";
    container.appendChild(legend);
  }

  let rsiSeries = null;
  if (payload.rsi.some(Boolean)) {
    rsiSeries = chart.addSeries(LightweightCharts.LineSeries, {
      color: payload.rsiColor,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
    }, 1);
    [__RSI_OVERBOUGHT__, __RSI_MIDLINE__, __RSI_OVERSOLD__].forEach(function (level, i) {
      rsiSeries.createPriceLine({
        price: level,
        color: payload.guideColor,
        lineWidth: 1,
        lineStyle: i === 1 ? __LINE_STYLE_DASHED__ : __LINE_STYLE_DOTTED__,
        axisLabelVisible: false,
      });
    });
    const panes = chart.panes();
    if (panes.length > 1) {
      panes[1].setHeight(Math.round(payload.height * __RSI_PANE_HEIGHT_RATIO__));
    }
  }

  if (payload.markers.length) {
    LightweightCharts.createSeriesMarkers(candleSeries, payload.markers);
  }

  let loadedFrom = Math.max(0, payload.candles.length - payload.initialBars);
  function applyFrom(idx) {
    candleSeries.setData(payload.candles.slice(idx));
    if (rsiSeries) {
      rsiSeries.setData(payload.rsi.slice(idx).filter(Boolean));
    }
    lineSeriesList.forEach(function (entry) {
      entry.series.setData(entry.points.slice(idx).filter(Boolean));
    });
  }
  applyFrom(loadedFrom);
  chart.timeScale().fitContent();

  let loading = false;
  chart.timeScale().subscribeVisibleLogicalRangeChange(function (range) {
    if (!range || loading || loadedFrom <= 0) return;
    if (range.from < 20) {
      loading = true;
      const savedRange = chart.timeScale().getVisibleRange();
      const newFrom = Math.max(0, loadedFrom - payload.initialBars);
      loadedFrom = newFrom;
      applyFrom(loadedFrom);
      if (savedRange) chart.timeScale().setVisibleRange(savedRange);
      loading = false;
    }
  });
})();
</script>
"""


def _tp_line_series(frame: pd.DataFrame, conf_params: ConfluenceParams) -> dict[str, pd.Series]:
    """차트 표시선(EMA/VWMA)을 `frame`과 위치가 정렬된 Series로 계산한다.

    **차트 표시선(`display_ema_lengths`, 기본 5개)은 익절 판정선(`tp_ema_lengths`,
    기본 EMA 60)과 다르다**(WAN-66) — 사용자는 EMA 5개 전부를 눈으로 보되 익절은
    EMA 60 + VWMA 100에서만 한다. 그래서 여기서는 `display_ema_lengths`를 그린다.
    지표 계산은 `strategy.indicators`(전략이 백테스트에 쓰는 것과 동일 함수)를
    재사용한다. 키 순서(EMA 오름차순 → VWMA)가 팔레트·범례 순서를 결정한다.
    """
    cols: dict[str, pd.Series] = {}
    lengths = conf_params.sorted_display_ema_lengths
    if lengths:
        ema_frame = compute_emas(frame, lengths=lengths, source=conf_params.source)
        for length in lengths:
            cols[f"ema_{length}"] = ema_frame[f"ema_{length}"]
    if conf_params.tp_vwma_length is not None:
        key = conf_params.tp_vwma_key
        assert key is not None
        cols[key] = compute_vwma(
            frame, length=conf_params.tp_vwma_length, source=conf_params.source
        )
    return cols


def _line_color(key: str, ema_index: int) -> str:
    if key.startswith("vwma_"):
        return _VWMA_LINE_COLOR
    return _EMA_LINE_PALETTE[ema_index % len(_EMA_LINE_PALETTE)]


def build_chart_html(
    df: pd.DataFrame,
    order_blocks: Sequence[OrderBlock],
    backtest: BacktestResult | None = None,
    signals: Sequence[OrderBlockSignal] = (),
    *,
    conf_params: ConfluenceParams | None = None,
    visible_lines: frozenset[str] | None = None,
    theme: str = DEFAULT_THEME,
    height: int = 700,
    initial_bars: int = _INITIAL_BARS,
) -> str:
    """캔들+오더블록+RSI+익절 목표선 패널을 그리는 자족형 HTML을 만든다.

    `st.components.v1.html(build_chart_html(...), height=height)`로 임베드한다.
    반환된 HTML은 벤더링된 JS 라이브러리를 인라인 포함해 오프라인에서도 동작한다.

    `conf_params`를 주면 `display_ema_lengths`(차트 표시선)/`tp_vwma_length` 선을
    캔들 패널에 오버레이하고(익절 판정선 `tp_ema_lengths`와 별개 — WAN-66)
    (`visible_lines`로 표시할 키만 필터, `None`이면 전부), `backtest`가 있으면 청산
    마커에 사유(익절/손절)·닿은 선·손익%·R 배수를 담는다. `signals`는 각 거래를 발생시킨
    시그널(오더블록·계획 청산)을 역추적하는 데 쓰인다(WAN-59 후속).

    `theme`(`"light"`/`"dark"`, 기본 다크)는 배경·격자·존·마커·RSI 색을 결정한다 —
    Streamlit 테마에 맞춰 호출부에서 넘긴다(WAN-55).
    """
    chart_theme = resolve_theme(theme)
    frame = df.sort_values("open_time").reset_index(drop=True)
    if frame.empty:
        return '<div style="padding:2rem;color:#888;">표시할 데이터가 없습니다.</div>'

    last_bar_ms = int(frame["open_time"].iloc[-1])
    times_sec = (frame["open_time"] // 1000).tolist()
    candles: list[dict[str, float]] = [
        {"time": t, "open": o, "high": h, "low": low, "close": c}
        for t, o, h, low, c in zip(
            times_sec,
            frame["open"].tolist(),
            frame["high"].tolist(),
            frame["low"].tolist(),
            frame["close"].tolist(),
            strict=True,
        )
    ]

    rsi_full = compute_rsi(frame, length=_RSI_LENGTH)
    rsi_by_time: dict[int, float] = dict(
        zip(frame["open_time"].tolist(), rsi_full.tolist(), strict=True)
    )
    rsi_points: list[dict[str, float] | None] = [
        None if (v is None or math.isnan(v)) else {"time": t, "value": round(v, 4)}
        for t, v in zip(times_sec, rsi_full.tolist(), strict=True)
    ]

    tp_lines = _tp_line_series(frame, conf_params) if conf_params is not None else {}
    times_ms: list[int] = frame["open_time"].tolist()
    tp_lines_by_time: dict[str, dict[int, float]] = {
        key: dict(zip(times_ms, series.tolist(), strict=True)) for key, series in tp_lines.items()
    }
    allowed_lines = tp_lines.keys() if visible_lines is None else visible_lines
    lines_payload: list[dict[str, object]] = []
    ema_index = 0
    for key, series in tp_lines.items():
        color = _line_color(key, ema_index)
        if not key.startswith("vwma_"):
            ema_index += 1
        if key not in allowed_lines:
            continue
        # 소수점 2자리로 반올림한다(가격 표시 정밀도로 충분 — `_fmt_price`와 동일).
        # EMA/VWMA는 부동소수점 나눗셈 결과라 반올림 없이는 유효숫자가 15~17자리까지
        # 늘어나 6개 선 전체(3년 15m ≈ 10만 봉)를 실으면 페이로드가 크게 불어난다.
        # 매칭(`_touched_line_label`)은 이 반올림값이 아니라 `tp_lines_by_time`의
        # 원본 정밀도 값을 쓰므로 정확도에 영향 없다.
        points: list[dict[str, float] | None] = [
            None if math.isnan(v) else {"time": t, "value": round(v, 2)}
            for t, v in zip(times_sec, series.tolist(), strict=True)
        ]
        lines_payload.append(
            {"key": key, "label": _line_label(key), "color": color, "points": points}
        )

    boxes = _zone_boxes(order_blocks, last_bar_ms, chart_theme)
    markers = (
        _entry_exit_markers(backtest, rsi_by_time, signals, tp_lines_by_time, chart_theme)
        if backtest is not None
        else []
    )

    payload: dict[str, object] = {
        "candles": candles,
        "rsi": rsi_points,
        "boxes": boxes,
        "markers": markers,
        "lines": lines_payload,
        "initialBars": min(initial_bars, len(candles)),
        "priceColors": {"up": _BULL_COLOR, "down": _BEAR_COLOR},
        "rsiColor": chart_theme.rsi_line,
        "guideColor": chart_theme.rsi_guide,
        "theme": {
            "background": chart_theme.background,
            "textColor": chart_theme.text_color,
            "gridColor": chart_theme.grid_color,
            "legendBg": chart_theme.legend_bg,
            "legendText": chart_theme.legend_text,
        },
        "height": height,
    }
    container_id = f"lwc-{uuid.uuid4().hex}"

    html = _TEMPLATE
    html = html.replace("__CONTAINER_ID__", container_id)
    html = html.replace("__HEIGHT__", str(height))
    html = html.replace("__LIBRARY_JS__", _load_library_js())
    html = html.replace("__PAYLOAD_JSON__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__LINE_STYLE_DASHED__", str(_LINE_STYLE_DASHED))
    html = html.replace("__LINE_STYLE_DOTTED__", str(_LINE_STYLE_DOTTED))
    html = html.replace("__RSI_OVERBOUGHT__", str(_RSI_OVERBOUGHT))
    html = html.replace("__RSI_MIDLINE__", str(_RSI_MIDLINE))
    html = html.replace("__RSI_OVERSOLD__", str(_RSI_OVERSOLD))
    html = html.replace("__RSI_PANE_HEIGHT_RATIO__", str(_RSI_PANE_HEIGHT_RATIO))
    return html
