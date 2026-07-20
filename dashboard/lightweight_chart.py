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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

from backtest.models import BacktestResult, ExitReason, PositionSide
from dashboard.live_chart import LIVE_BAND_JS, LiveChartConfig
from strategy.confluence import ConfluenceStrategy
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

#: 캔들 몸통(강세/약세) 색 — **상승 하양 / 하락 빨강**(WAN-67 사용자 스펙 2026-07-20).
#: 흰 몸통은 어두운 배경을 전제한 색이라(`DEFAULT_THEME = "dark"`) 라이트에서는 몸통을
#: 흰색으로 두되 **테두리를 켜서** 배경과 가른다(고전적인 할로우 캔들). 그래서 캔들
#: 색은 테마별 값(`ChartTheme.bull_candle` 등)이고, 아래 상수는 그 기본값이다.
#: ⚠️ 옛 값(트레이딩뷰 기본 `#26a69a`/`#ef5350`)은 존 채움색으로 여전히 쓰인다 —
#: 존은 강세/약세 오더블록을 구분하는 별개 축이라 캔들 스펙을 따라가지 않는다.
_BULL_COLOR = "#ffffff"
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
    #: 차트 표시선 색(WAN-67). `ema_length_colors`는 **길이**를 키로 하는 고정 매핑이고
    #: (사용자 스펙: 20 빨강 / 60 주황 / 120 노랑 / 240 초록 / 365 파랑), 스펙에 없는
    #: 길이가 오면 `_EMA_LINE_PALETTE`(순번 기반)로 폴백한다. `vwma_line`은 EMA 5색과
    #: 겹치지 않는 색이다. 두 값 모두 테마별로 명도를 달리 잡되 색상(hue)은 스펙을 지킨다.
    #: `hash=False`는 dict 필드가 `ChartTheme`의 자동 `__hash__`를 깨지 않게 한다.
    ema_length_colors: Mapping[int, str] = field(hash=False, default_factory=dict)
    vwma_line: str = "#d81b60"
    #: 캔들 몸통·테두리·형성 중인 봉 색(WAN-67 — 상승 하양 / 하락 빨강). 흰 몸통이
    #: 배경에 묻히는 라이트 테마에서만 테두리를 켜 가른다(`candle_border_visible`).
    bull_candle: str = _BULL_COLOR
    bear_candle: str = _BEAR_COLOR
    candle_border_visible: bool = False
    bull_candle_border: str = _BULL_COLOR
    bear_candle_border: str = _BEAR_COLOR
    #: 형성 중인 봉(라이브)은 같은 색을 옅게 — 확정봉과 구분해야 한다(WAN-147).
    bull_candle_live: str = "rgba(255, 255, 255, 0.45)"
    bear_candle_live: str = "rgba(239, 83, 80, 0.45)"

    def exit_marker_colors(self) -> dict[ExitReason, str]:
        return {
            ExitReason.TAKE_PROFIT: self.exit_take_profit,
            ExitReason.PARTIAL_TAKE_PROFIT: self.exit_take_profit,
            ExitReason.STOP_LOSS: self.exit_stop_loss,
            ExitReason.END_OF_DATA: self.exit_end_of_data,
        }


#: 표시선 색 — **길이별 고정 매핑**(WAN-67 사용자 스펙: 20 빨강 / 60 주황 / 120 노랑 /
#: 240 초록 / 365 파랑). 다크가 기준 테마다(`DEFAULT_THEME = "dark"` · 사용자 지시
#: "무조건 다크테마로") — 어두운 배경에서 대비가 최대가 되도록 명도를 올린 값이다.
#: 캔들 몸통색(#26a69a/#ef5350)과 겹치지 않도록 빨강·초록은 톤을 달리 잡았다.
_EMA_LENGTH_COLORS_DARK: Mapping[int, str] = {
    20: "#ff5252",  # red
    60: "#ff9800",  # orange
    120: "#ffee58",  # yellow
    240: "#4caf50",  # green
    365: "#42a5f5",  # blue
}

#: 라이트 테마용 같은 색상(hue) · 낮은 명도. 흰 배경에서 노랑은 그대로 쓰면 안 보여
#: 앰버로 내린다(색상 스펙은 유지, 명도만 조정 — 이슈 §작업 범위가 정한 원칙).
_EMA_LENGTH_COLORS_LIGHT: Mapping[int, str] = {
    20: "#d32f2f",  # red
    60: "#ef6c00",  # orange
    120: "#f9a825",  # yellow(amber)
    240: "#2e7d32",  # green
    365: "#1565c0",  # blue
}

#: VWMA 100 — 다크에서 **흰색**(사용자 스펙 2026-07-20). EMA 5색·볼린저 시안과 겹치지
#: 않고 어두운 배경에서 대비가 가장 크다. 라이트 배경에서는 흰 선이 사라지므로 같은
#: 역할(무채색 중립)을 하는 짙은 회색으로 대응한다.
_VWMA_LINE_COLOR_DARK = "#ffffff"  # white
_VWMA_LINE_COLOR_LIGHT = "#212121"  # near-black

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
    ema_length_colors=_EMA_LENGTH_COLORS_LIGHT,
    vwma_line=_VWMA_LINE_COLOR_LIGHT,
    # 흰 배경 + 흰 몸통이라 테두리 없이는 상승봉이 사라진다. 몸통은 스펙(하양)대로
    # 두고 회색 테두리로 가른다 — 색을 바꾸는 대신 형태로 대응한 것이다.
    candle_border_visible=True,
    bull_candle_border="#787b86",
    bear_candle_border="#c62828",
    bull_candle_live="rgba(120, 123, 134, 0.35)",
    bear_candle_live="rgba(239, 83, 80, 0.45)",
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
    ema_length_colors=_EMA_LENGTH_COLORS_DARK,
    vwma_line=_VWMA_LINE_COLOR_DARK,
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

#: 표시선(EMA) 오버레이 **폴백** 팔레트. 기본 색은 길이별 고정 매핑
#: (`ChartTheme.ema_length_colors`, WAN-67)이고, 사용자가 `display_ema_lengths`를 바꿔
#: **스펙에 없는 길이**가 오면 팔레트가 무너지지 않도록 여기서 순번으로 순환 배정한다
#: (WAN-59의 순번 매핑 의도를 폴백으로 보존한 것이다).
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

#: 표시선(EMA/VWMA) 굵기(WAN-67). 1은 캔들 위에서 잘 안 보인다는 사용자 지적이라 2로
#: 올린다. RSI 패인의 선·가이드는 캔들과 겹치지 않으므로 1 그대로 둔다.
_MA_LINE_WIDTH = 2

#: 볼린저 하단선 굵기(WAN-67 사용자 지시 2026-07-20: "좀 얇게"). 이동평균선보다 얇게 둬
#: 진입 기준선이 표시선 다발에 묻히지 않으면서도 캔들을 덜 가린다.
_BAND_LINE_WIDTH = 1

#: 볼린저 하단선(진입가 기준선) 색. EMA 팔레트·VWMA와 겹치지 않는 시안 계열로 둔다
#: (WAN-147 — 이 선이 라이브로 움직이는 대상이라 한눈에 구분돼야 한다).
BAND_LINE_COLOR = "#00e5ff"

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

    ⚠️ **화면에는 더 이상 그리지 않는다**(WAN-146 사용자 스펙: 마커는 화살표만) — 긴
    문자열이 거래가 몰린 구간에서 서로 겹쳐 읽을 수 없었다. 그 정보는 거래 표가 컬럼으로
    보여준다(차트는 눈으로 훑고, 표는 숫자를 읽는다). 함수를 남기는 이유는 여기 담긴
    **R 배수의 계약**(리스크 기준 = 오더블록 무효화 경계 = `BacktestEngine._sizing_stop_price`와
    동일 규칙) 때문이다 — 지우면 그 지식이 사라지고, 나중에 툴팁·표 컬럼으로 되살릴
    여지도 남는다.

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
    theme: ChartTheme,
) -> list[dict[str, object]]:
    """진입/청산 마커 — **화살표만**(WAN-146 사용자 스펙).

    텍스트는 전부 뺐다. 진입의 `"롱 RSI28"`도, 청산의
    `"익절 · EMA60 @ 43,250.5 · +1.82% · R+1.51"`도 거래가 몰린 구간에서 서로 겹쳐
    차트를 읽을 수 없게 만들었다. 그 숫자들은 거래 표가 컬럼으로 보여준다.

    모양은 **전부 화살표**다(청산의 `circle`도 화살표로 바꿨다). 위치는 그대로 둔다 —
    진입은 캔들 밑(`belowBar`), 청산은 캔들 위(`aboveBar`, 숏은 반대). 위아래로 갈려
    있어야 어느 쪽인지 안 헷갈린다는 사용자 판단이라 `position` 로직은 건드리지 않았고,
    그래서 화살표 방향은 **붙는 쪽을 가리키게** 잡는다(밑에서 위를 찌르는 `arrowUp`,
    위에서 아래를 찌르는 `arrowDown`).

    색도 그대로다 — 진입 파랑 / 익절 초록 / 손절 빨강(테마 필드 `entry_marker`·
    `exit_take_profit`·`exit_stop_loss`).
    """
    exit_colors = theme.exit_marker_colors()
    markers: list[dict[str, object]] = []
    for trade in backtest.trades:
        is_long = trade.side is PositionSide.LONG
        markers.append(
            {
                "time": trade.entry_time // 1000,
                "position": "belowBar" if is_long else "aboveBar",
                "color": theme.entry_marker,
                "shape": "arrowUp" if is_long else "arrowDown",
            }
        )
        for fill in trade.exits:
            markers.append(
                {
                    "time": fill.time // 1000,
                    "position": "aboveBar" if is_long else "belowBar",
                    "color": exit_colors.get(fill.reason, theme.exit_default),
                    "shape": "arrowDown" if is_long else "arrowUp",
                }
            )
    markers.sort(key=lambda m: int(m["time"]))  # type: ignore[call-overload]
    return markers


#: 표 행을 눌러 이동한 구간의 좌우 여유 — 구간 길이 대비 비율과 최소 봉 수 중 큰 쪽
#: (WAN-146). 손절 시점 한 점만 보여주면 왜 손절됐는지 맥락이 안 보이고, 짧은 거래
#: (진입·청산이 같은 봉)는 비율만으로는 여유가 0이 된다.
_FOCUS_PAD_RATIO = 0.3
_FOCUS_PAD_MIN_BARS = 12


def _bar_interval_ms(times_ms: Sequence[int]) -> int:
    """봉 간격(ms). 봉이 하나뿐이면 0 — 호출부가 여유 계산에서 알아서 접힌다."""
    if len(times_ms) < 2:
        return 0
    diffs = [b - a for a, b in zip(times_ms, times_ms[1:], strict=False) if b > a]
    if not diffs:
        return 0
    return sorted(diffs)[len(diffs) // 2]


def _focus_range(times_ms: Sequence[int], focus: tuple[int, int]) -> dict[str, int]:
    """(진입 ms, 청산 ms) → 차트에 보여줄 (from, to) 초 단위 구간 + 좌우 여유.

    거래 **전체**가 화면에 들어와야 "왜 여기서 손절됐지"에 답이 된다 — 점이 아니라 구간으로
    잡고, 앞뒤로 여유를 둔다.
    """
    start_ms, end_ms = (focus[0], focus[1]) if focus[0] <= focus[1] else (focus[1], focus[0])
    interval = _bar_interval_ms(times_ms)
    pad = max(int((end_ms - start_ms) * _FOCUS_PAD_RATIO), interval * _FOCUS_PAD_MIN_BARS)
    return {"from": (start_ms - pad) // 1000, "to": (end_ms + pad) // 1000}


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
    timeScale: {
      borderVisible: false,
      timeVisible: true,
      secondsVisible: false,
      // 과거 거래를 보고 있는데(payload.focus) 라이브로 새 봉이 들어와 화면이 최신으로
      // 끌려가면 안 된다(WAN-146 × WAN-147).
      shiftVisibleRangeOnNewBar: !payload.focus,
    },
  });

  // 상승 하양 / 하락 빨강(WAN-67). 흰 몸통은 라이트 배경에서 사라지므로 그 테마에서만
  // 테두리를 켠다 — payload가 테마별로 색·on/off를 다 실어 온다.
  const candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: payload.priceColors.up,
    downColor: payload.priceColors.down,
    borderVisible: payload.priceColors.borderVisible,
    borderUpColor: payload.priceColors.borderUp,
    borderDownColor: payload.priceColors.borderDown,
    wickUpColor: payload.priceColors.borderUp,
    wickDownColor: payload.priceColors.borderDown,
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

  // 표시선(EMA/VWMA) 오버레이 — 사이드바 토글로 켜진 선만 payload.lines에 담겨
  // 온다(WAN-59 후속). 캔들 패인(0)에 LineSeries로 겹쳐 그린다. 굵기는 파이썬 쪽
  // `_MA_LINE_WIDTH`가 정한다(WAN-67 — 두 곳에 숫자를 흩뿌리지 않는다).
  const lineSeriesList = [];
  (payload.lines || []).forEach(function (line) {
    const s = chart.addSeries(LightweightCharts.LineSeries, {
      color: line.color,
      lineWidth: __MA_LINE_WIDTH__,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    }, 0);
    lineSeriesList.push({ series: s, points: line.points });
  });

  const legendItems = (payload.lines || []).slice();
  if (payload.band) {
    legendItems.push({ color: payload.band.color, label: payload.band.label });
  }
  if (legendItems.length) {
    const legend = document.createElement("div");
    legend.style.cssText =
      "position:absolute;top:8px;left:8px;z-index:5;background:" + theme.legendBg + ";" +
      "padding:4px 8px;border-radius:4px;font:11px -apple-system,sans-serif;" +
      "line-height:1.6;pointer-events:none;color:" + theme.legendText + ";";
    legendItems.forEach(function (line) {
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

  // 볼린저 하단선(진입가 기준선) — 라이브 갱신 대상이다(WAN-147). EMA/VWMA 토글과
  // 별개로 payload.band가 있을 때만 그린다.
  let bandSeries = null;
  if (payload.band) {
    bandSeries = chart.addSeries(LightweightCharts.LineSeries, {
      color: payload.band.color,
      lineWidth: __BAND_LINE_WIDTH__,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: false,
    }, 0);
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

  // 라이브로 받은 봉(형성 중인 봉 + 화면을 연 뒤 확정된 봉)은 payload.candles에 없다.
  // 좌측 지연 로딩이 setData로 캔버스를 다시 채울 때마다 지워지므로, 시각별로 들고
  // 있다가 다시 얹는다. 메모리는 화면을 열어 둔 시간에 비례할 뿐이고 어디에도 저장하지
  // 않는다(브라우저 안에서만 산다).
  const liveBars = new Map();
  const liveBandPoints = new Map();
  function reapplyLive() {
    Array.from(liveBars.keys()).sort(function (a, b) { return a - b; }).forEach(function (t) {
      candleSeries.update(liveBars.get(t));
    });
    if (!bandSeries) return;
    Array.from(liveBandPoints.keys()).sort(function (a, b) { return a - b; }).forEach(function (t) {
      bandSeries.update(liveBandPoints.get(t));
    });
  }

  let loadedFrom = Math.max(0, payload.candles.length - payload.initialBars);
  function applyFrom(idx) {
    loadedFrom = Math.max(0, idx);
    idx = loadedFrom;
    candleSeries.setData(payload.candles.slice(idx));
    if (rsiSeries) {
      rsiSeries.setData(payload.rsi.slice(idx).filter(Boolean));
    }
    if (bandSeries) {
      bandSeries.setData(payload.band.points.slice(idx).filter(Boolean));
    }
    lineSeriesList.forEach(function (entry) {
      entry.series.setData(entry.points.slice(idx).filter(Boolean));
    });
    reapplyLive();
  }
  applyFrom(loadedFrom);

  // 표에서 고른 거래로 점프(WAN-146). 그 구간이 아직 안 실렸으면 **먼저 로드 범위를
  // 넓힌다** — 지연 로딩 때문에 1년 전 손절 거래를 누르면 빈 화면으로 점프하기 때문이다.
  // 넓히는 수단은 좌측 스크롤이 쓰는 것과 같은 `applyFrom` 하나다(로직 이중화 금지).
  if (payload.focus) {
    let target = loadedFrom;
    while (target > 0 && payload.candles[target].time > payload.focus.from) {
      target = Math.max(0, target - payload.initialBars);
    }
    if (target !== loadedFrom) applyFrom(target);
    chart.timeScale().setVisibleRange({ from: payload.focus.from, to: payload.focus.to });
  } else {
    chart.timeScale().fitContent();
  }

  let loading = false;
  chart.timeScale().subscribeVisibleLogicalRangeChange(function (range) {
    if (!range || loading || loadedFrom <= 0) return;
    if (range.from < 20) {
      loading = true;
      const savedRange = chart.timeScale().getVisibleRange();
      applyFrom(loadedFrom - payload.initialBars);
      if (savedRange) chart.timeScale().setVisibleRange(savedRange);
      loading = false;
    }
  });

  // ---- 실시간 kline (WAN-147) --------------------------------------------
  // 표시 계층 전용이다: 여기서 갱신되는 건 캔들과 밴드 선뿐이고, 거래 표·성과 지표는
  // 확정봉 기준 백테스트 결과 그대로다(이 스코프에서 그 값들을 건드리지 않는다).
  // 받은 데이터는 어디에도 저장하지 않는다 — 흘려보내며 그리기만 한다.
  __LIVE_BAND_JS__
  const live = payload.live;
  if (live) {
    container.style.position = "relative";
    const badge = document.createElement("div");
    badge.style.cssText =
      "position:absolute;top:8px;right:8px;z-index:6;background:" + theme.legendBg + ";" +
      "padding:3px 8px;border-radius:4px;font:11px -apple-system,sans-serif;" +
      "pointer-events:none;color:" + theme.legendText + ";";
    container.appendChild(badge);

    let bandWindow = live.bandCloses ? live.bandCloses.slice() : null;
    // 밴드 창은 "차트 마지막 확정봉 다음 봉"에만 유효하다. DB가 오래돼 그 사이에 빈
    // 봉이 있으면 낡은 창으로 계산한 값을 라이브인 척 그리지 않고 밴드를 멈춘다.
    let expectedOpenMs = live.lastBarOpenMs + live.intervalMs;
    let bandStale = false;
    let lastCandleTime = payload.candles.length
      ? payload.candles[payload.candles.length - 1].time
      : null;
    let connState = "connecting";

    function renderBadge() {
      const dot = connState === "open" ? "🟢" : (connState === "connecting" ? "🟡" : "🔴");
      const head = connState === "open" ? "LIVE" :
        (connState === "connecting" ? "연결 중" : "연결 끊김 · 재연결 중");
      let tail = "";
      if (!bandWindow) {
        tail = " · 캔들만";
      } else if (bandStale) {
        tail = " · 밴드 정지(데이터 공백)";
      }
      badge.textContent = dot + " " + head + " · " + live.symbol + " " + live.timeframe + tail;
    }
    renderBadge();

    function handleKline(k) {
      const openMs = Number(k.t);
      if (!Number.isFinite(openMs)) return;
      const barTime = Math.floor(openMs / 1000);
      if (lastCandleTime !== null && barTime < lastCandleTime) return;  // 지연 도착 무시
      const closed = k.x === true;
      const bar = {
        time: barTime,
        open: Number(k.o),
        high: Number(k.h),
        low: Number(k.l),
        close: Number(k.c),
      };
      if (!closed) {
        // 형성 중인 봉은 옅은 몸통으로 그려 확정봉과 구분한다(완료 기준).
        bar.color = bar.close >= bar.open ? live.liveColors.up : live.liveColors.down;
        bar.wickColor = bar.color;
      }
      candleSeries.update(bar);
      lastCandleTime = barTime;
      liveBars.set(barTime, bar);

      if (bandWindow && bandSeries && !bandStale) {
        if (openMs > expectedOpenMs) {
          bandStale = true;  // 창과 이어지지 않는다 — 새로고침 전까지 밴드는 확정봉까지만.
        } else if (openMs === expectedOpenMs) {
          const value = computeLiveBand(bandWindow, bar.close, live.bandParams);
          if (value !== null) {
            const point = { time: barTime, value: value };
            bandSeries.update(point);
            liveBandPoints.set(barTime, point);
          }
          if (closed) {
            bandWindow.push(bar.close);
            bandWindow.shift();
            expectedOpenMs += live.intervalMs;
          }
        }
      }
      renderBadge();
    }

    let socket = null;
    let retries = 0;
    let stopped = false;
    function connect() {
      connState = "connecting";
      renderBadge();
      try {
        socket = new WebSocket(live.streamUrl);
      } catch (err) {
        scheduleReconnect();
        return;
      }
      socket.onopen = function () {
        retries = 0;
        connState = "open";
        renderBadge();
      };
      socket.onmessage = function (event) {
        try {
          const msg = JSON.parse(event.data);
          const data = msg.data || msg;
          if (data && data.k) handleKline(data.k);
        } catch (err) {
          // 파싱 실패 메시지는 버린다 — 화면이 깨지는 것보다 한 틱을 놓치는 게 낫다.
        }
      };
      socket.onclose = function () {
        if (stopped) return;
        connState = "closed";
        renderBadge();
        scheduleReconnect();
      };
      socket.onerror = function () {
        if (socket) socket.close();
      };
    }
    function scheduleReconnect() {
      // 지수 백오프(최대 30초). 끊김·복구가 반복돼도 화면은 마지막 값을 유지한다.
      const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(retries, 5)));
      retries += 1;
      setTimeout(function () { if (!stopped) connect(); }, delay);
    }
    // 심볼·기간을 바꾸면 Streamlit이 이 iframe을 통째로 갈아끼운다 — 그때 소켓을 닫아
    // 옛 구독이 남지 않게 한다.
    window.addEventListener("pagehide", function () {
      stopped = true;
      if (socket) socket.close();
    });
    connect();
  }
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


def _band_points(
    frame: pd.DataFrame,
    times_sec: Sequence[int],
    conf_params: ConfluenceParams,
    *,
    direction_sign: int = 1,
) -> list[dict[str, float] | None] | None:
    """볼린저 하단선(진입가 기준선) 시계열. 이격 필터가 꺼져 있으면 `None`.

    값은 전략이 실제로 쓰는 함수(`ConfluenceStrategy.deviation_filter_components` +
    `deviation_band_at`)에서 그대로 가져온다 — 화면에 따로 계산한 선을 그리면 "차트에
    보이는 선"과 "엔진이 쓰는 값"이 갈라진다.

    ⚠️ 확정봉 시리즈다. 형성 중인 봉의 값은 브라우저가 `computeLiveBand`로 얹는다
    (`dashboard.live_chart`) — 그 JS가 파이썬 `RealtimeBand`와 같은 값을 냄은 테스트가
    Node로 검증한다.
    """
    filter_params = conf_params.deviation_filter
    if filter_params is None:
        return None
    anchor_vals, width_vals = ConfluenceStrategy.deviation_filter_components(
        frame, filter_params, conf_params.source
    )
    points: list[dict[str, float] | None] = []
    for pos, time_sec in enumerate(times_sec):
        # 밴드 봉 기준은 봉 단위 표현이 가능한 것만 쓴다 — `intrabar_live`(채택 기본값)는
        # 봉 단위에서 `tap`과 같은 값이고(`deviation_band_at` 독스트링), 확정된 봉의
        # 선을 그리는 이 함수에는 그 접힘이 정확히 맞는다.
        band_bar = filter_params.band_bar
        if band_bar == "intrabar_causal":
            band_bar = "intrabar_live"
        value = ConfluenceStrategy.deviation_band_at(
            pos, direction_sign, anchor_vals, width_vals, band_bar=band_bar
        )
        points.append(None if value is None else {"time": time_sec, "value": round(value, 2)})
    return points


def _ema_key_length(key: str) -> int | None:
    """`"ema_60"` → `60`. EMA 키가 아니거나 길이를 못 읽으면 `None`."""
    if not key.startswith("ema_"):
        return None
    suffix = key[len("ema_") :]
    return int(suffix) if suffix.isdigit() else None


def _line_color(key: str, ema_index: int, theme: ChartTheme) -> str:
    """표시선 색: 길이별 고정 매핑(WAN-67), 스펙 밖 길이는 순번 팔레트로 폴백.

    사용자 스펙은 EMA 20/60/120/240/365 = 빨강/주황/노랑/초록/파랑이다. 그 다섯 길이는
    `theme.ema_length_colors`에서 **길이를 키로** 꺼내므로 `display_ema_lengths` 순서가
    바뀌어도 같은 선은 같은 색이다(트레이딩뷰 화면과 눈으로 대조하기 위한 요구). 목록에
    없는 길이를 사용자가 추가해도 렌더가 깨지지 않도록 옛 순번 팔레트로 떨어뜨린다.
    """
    if key.startswith("vwma_"):
        return theme.vwma_line
    length = _ema_key_length(key)
    if length is not None:
        fixed = theme.ema_length_colors.get(length)
        if fixed is not None:
            return fixed
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
    live: LiveChartConfig | None = None,
    focus: tuple[int, int] | None = None,
) -> str:
    """캔들+오더블록+RSI+익절 목표선 패널을 그리는 자족형 HTML을 만든다.

    `st.components.v1.html(build_chart_html(...), height=height)`로 임베드한다.
    반환된 HTML은 벤더링된 JS 라이브러리를 인라인 포함해 오프라인에서도 동작한다.

    `conf_params`를 주면 `display_ema_lengths`(차트 표시선)/`tp_vwma_length` 선을
    캔들 패널에 오버레이하고(익절 판정선 `tp_ema_lengths`와 별개 — WAN-66)
    (`visible_lines`로 표시할 키만 필터, `None`이면 전부), `backtest`가 있으면 진입·청산
    **화살표 마커**를 얹는다(색만으로 구분 — 진입 파랑 / 익절 초록 / 손절 빨강).

    ⚠️ **마커에 텍스트는 없다**(WAN-146) — 사유·손익%·R 배수는 거래 표가 컬럼으로
    보여준다. `signals`는 그 텍스트를 만들던 `_exit_marker_text` 경로의 입력이라
    시그니처에 남겨 뒀지만 **지금 렌더에는 쓰이지 않는다**(WAN-59 후속에서 도입).

    `theme`(`"light"`/`"dark"`, 기본 다크)는 배경·격자·존·마커·RSI 색을 결정한다 —
    Streamlit 테마에 맞춰 호출부에서 넘긴다(WAN-55).

    `conf_params.deviation_filter`가 켜져 있으면 **볼린저 하단선**(진입가 기준선)을 함께
    그린다. `live`(`dashboard.live_chart.build_live_config`)를 주면 브라우저가 바이낸스
    웹소켓에 직접 붙어 **형성 중인 봉과 그 밴드 값**을 갱신한다(WAN-147) — 표시 계층
    전용이라 `backtest` 표·지표는 확정봉 기준 그대로다.

    `focus`(진입 ms, 청산 ms)를 주면 그 거래 구간(+앞뒤 여유)으로 화면을 맞춘다
    (WAN-146 — 거래 표에서 행을 고른 경우). 그 구간이 지연 로딩 범위 밖이면 캔들을 먼저
    더 실은 뒤 이동하므로 오래된 거래를 골라도 빈 화면이 되지 않는다. 이동은 **보는
    구간만** 바꾼다 — 거래·지표 계산에는 전혀 관여하지 않는다.
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
    rsi_points: list[dict[str, float] | None] = [
        None if (v is None or math.isnan(v)) else {"time": t, "value": round(v, 4)}
        for t, v in zip(times_sec, rsi_full.tolist(), strict=True)
    ]

    tp_lines = _tp_line_series(frame, conf_params) if conf_params is not None else {}
    times_ms: list[int] = frame["open_time"].tolist()
    allowed_lines = tp_lines.keys() if visible_lines is None else visible_lines
    lines_payload: list[dict[str, object]] = []
    ema_index = 0
    for key, series in tp_lines.items():
        color = _line_color(key, ema_index, chart_theme)
        if not key.startswith("vwma_"):
            ema_index += 1
        if key not in allowed_lines:
            continue
        # 소수점 2자리로 반올림한다(가격 표시 정밀도로 충분 — `_fmt_price`와 동일).
        # EMA/VWMA는 부동소수점 나눗셈 결과라 반올림 없이는 유효숫자가 15~17자리까지
        # 늘어나 6개 선 전체(3년 15m ≈ 10만 봉)를 실으면 페이로드가 크게 불어난다.
        # (`_touched_line_label`이 선을 되짚을 때는 이 반올림값이 아니라 원본 정밀도
        # 시리즈를 쓴다 — 그 경로는 지금 화면에 안 그려진다, WAN-146.)
        points: list[dict[str, float] | None] = [
            None if math.isnan(v) else {"time": t, "value": round(v, 2)}
            for t, v in zip(times_sec, series.tolist(), strict=True)
        ]
        lines_payload.append(
            {"key": key, "label": _line_label(key), "color": color, "points": points}
        )

    band_payload: dict[str, object] | None = None
    if conf_params is not None and conf_params.deviation_filter is not None:
        band_points = _band_points(frame, times_sec, conf_params)
        if band_points is not None:
            length = conf_params.deviation_filter.sma_length
            band_payload = {
                "label": f"볼린저 하단 (SMA{length} 진입 기준선)",
                "color": BAND_LINE_COLOR,
                "points": band_points,
            }

    live_payload: dict[str, object] | None = None
    if live is not None:
        live_payload = live.to_payload()
        # 형성 중인 봉 색은 캔들 색을 따라간다(WAN-67) — `live_chart`는 테마를 모르므로
        # 여기서 덮어쓴다. 두 곳에 색을 두면 캔들만 바꿨을 때 라이브 봉이 옛 색으로 남는다.
        live_payload["liveColors"] = {
            "up": chart_theme.bull_candle_live,
            "down": chart_theme.bear_candle_live,
        }
        if band_payload is None:
            # 밴드 선 자체를 안 그리는 화면이면 라이브 밴드도 끈다 — 그리지 않는 선을
            # 갱신하는 배선을 남기면 "켜져 있다고 믿는" 조용한 실패가 된다.
            live_payload["bandCloses"] = None
            live_payload["bandParams"] = None

    boxes = _zone_boxes(order_blocks, last_bar_ms, chart_theme)
    markers = _entry_exit_markers(backtest, chart_theme) if backtest is not None else []
    focus_payload = _focus_range(times_ms, focus) if focus is not None else None

    payload: dict[str, object] = {
        "candles": candles,
        "rsi": rsi_points,
        "boxes": boxes,
        "markers": markers,
        "lines": lines_payload,
        "band": band_payload,
        "live": live_payload,
        "focus": focus_payload,
        "initialBars": min(initial_bars, len(candles)),
        "priceColors": {
            "up": chart_theme.bull_candle,
            "down": chart_theme.bear_candle,
            "borderVisible": chart_theme.candle_border_visible,
            "borderUp": chart_theme.bull_candle_border,
            "borderDown": chart_theme.bear_candle_border,
        },
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
    html = html.replace("__LIVE_BAND_JS__", LIVE_BAND_JS)
    html = html.replace("__PAYLOAD_JSON__", json.dumps(payload, separators=(",", ":")))
    html = html.replace("__LINE_STYLE_DASHED__", str(_LINE_STYLE_DASHED))
    html = html.replace("__LINE_STYLE_DOTTED__", str(_LINE_STYLE_DOTTED))
    html = html.replace("__RSI_OVERBOUGHT__", str(_RSI_OVERBOUGHT))
    html = html.replace("__RSI_MIDLINE__", str(_RSI_MIDLINE))
    html = html.replace("__RSI_OVERSOLD__", str(_RSI_OVERSOLD))
    html = html.replace("__RSI_PANE_HEIGHT_RATIO__", str(_RSI_PANE_HEIGHT_RATIO))
    html = html.replace("__MA_LINE_WIDTH__", str(_MA_LINE_WIDTH))
    html = html.replace("__BAND_LINE_WIDTH__", str(_BAND_LINE_WIDTH))
    return html
