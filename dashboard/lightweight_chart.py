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
from functools import lru_cache
from pathlib import Path

import pandas as pd

from backtest.models import BacktestResult, PositionSide
from strategy.indicators import rsi as compute_rsi
from strategy.models import OrderBlock, OrderBlockDirection

_BULL_COLOR = "#26a69a"
_BEAR_COLOR = "#ef5350"
_BULL_ZONE_LINE = "rgba(38, 166, 154, 0.9)"
_BEAR_ZONE_LINE = "rgba(239, 83, 80, 0.9)"
_BULL_ZONE_FILL = "rgba(38, 166, 154, 0.20)"
_BEAR_ZONE_FILL = "rgba(239, 83, 80, 0.20)"
#: breaker(무효화)로 전환됐던 존은 옅게 칠해 "깨졌던 것"임을 구분한다.
_BULL_ZONE_FILL_FADED = "rgba(38, 166, 154, 0.09)"
_BEAR_ZONE_FILL_FADED = "rgba(239, 83, 80, 0.09)"

#: 초기(첫 렌더) 캔버스에 그릴 봉 수. 완료 기준("2초 이내 초기 렌더")을 만족시키는
#: 상한. 좌측 끝으로 스크롤하면 이 크기만큼 청크가 더 붙는다(WAN-54).
_INITIAL_BARS = 1_500

_RSI_LENGTH = 14
_RSI_OVERBOUGHT = 70.0
_RSI_MIDLINE = 50.0
_RSI_OVERSOLD = 30.0
_RSI_LINE_COLOR = "#7e57c2"
_RSI_GUIDE_COLOR = "rgba(120, 120, 120, 0.55)"
#: 캔들:RSI 패인 높이 비율 ≈ 3:1.
_RSI_PANE_HEIGHT_RATIO = 0.25

_LINE_STYLE_DOTTED = 1
_LINE_STYLE_DASHED = 2

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


def _zone_boxes(order_blocks: Sequence[OrderBlock], last_bar_ms: int) -> list[dict[str, object]]:
    """존마다 `BaselineSeries` 두 점 + 스타일 정보를 담은 dict를 만든다."""
    boxes: list[dict[str, object]] = []
    for ob in order_blocks:
        is_bull = ob.direction is OrderBlockDirection.BULLISH
        faded = ob.breaker
        start = ob.start_time
        end = max(_zone_span_end(ob, last_bar_ms), start)
        if is_bull:
            fill = _BULL_ZONE_FILL_FADED if faded else _BULL_ZONE_FILL
            line = _BULL_ZONE_LINE
        else:
            fill = _BEAR_ZONE_FILL_FADED if faded else _BEAR_ZONE_FILL
            line = _BEAR_ZONE_LINE
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


def _entry_exit_markers(
    backtest: BacktestResult, rsi_by_time: dict[int, float]
) -> list[dict[str, object]]:
    """진입/청산 마커. 진입 마커 텍스트에 방향·그 시점 RSI를 함께 담는다."""
    markers: list[dict[str, object]] = []
    for trade in backtest.trades:
        is_long = trade.side is PositionSide.LONG
        rsi_value = _rsi_at(rsi_by_time, trade.entry_time)
        rsi_text = f"{rsi_value:.0f}" if rsi_value is not None else "—"
        markers.append(
            {
                "time": trade.entry_time // 1000,
                "position": "belowBar" if is_long else "aboveBar",
                "color": "#1e88e5",
                "shape": "arrowUp" if is_long else "arrowDown",
                "text": f"{'롱' if is_long else '숏'} RSI{rsi_text}",
            }
        )
        for fill in trade.exits:
            markers.append(
                {
                    "time": fill.time // 1000,
                    "position": "aboveBar" if is_long else "belowBar",
                    "color": "#6d4c41",
                    "shape": "circle",
                    "text": "청산",
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

  const chart = LightweightCharts.createChart(container, {
    autoSize: true,
    layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#333333" },
    grid: {
      vertLines: { color: "rgba(220,220,220,0.5)" },
      horzLines: { color: "rgba(220,220,220,0.5)" },
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


def build_chart_html(
    df: pd.DataFrame,
    order_blocks: Sequence[OrderBlock],
    backtest: BacktestResult | None = None,
    *,
    height: int = 700,
    initial_bars: int = _INITIAL_BARS,
) -> str:
    """캔들+오더블록+RSI 패널을 그리는 자족형 HTML을 만든다.

    `st.components.v1.html(build_chart_html(...), height=height)`로 임베드한다.
    반환된 HTML은 벤더링된 JS 라이브러리를 인라인 포함해 오프라인에서도 동작한다.
    """
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

    boxes = _zone_boxes(order_blocks, last_bar_ms)
    markers = _entry_exit_markers(backtest, rsi_by_time) if backtest is not None else []

    payload: dict[str, object] = {
        "candles": candles,
        "rsi": rsi_points,
        "boxes": boxes,
        "markers": markers,
        "initialBars": min(initial_bars, len(candles)),
        "priceColors": {"up": _BULL_COLOR, "down": _BEAR_COLOR},
        "rsiColor": _RSI_LINE_COLOR,
        "guideColor": _RSI_GUIDE_COLOR,
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
