"""통합 트레이딩 웹 대시보드 (WAN-15 · WAN-30).

**차트-우선 배치(WAN-245, 사용자 결정 2026-08-04 · 목업 승인 2026-08-11)**: 첫 화면이
**라이브 차트**다 — 트레이딩뷰처럼 차트가 앞에 오고 그 아래 지금 상태(오픈 포지션)가
보인다. 그 뒤로 지갑(잔고·거래내역) → 진입/미진입 장부(체결률, WAN-217/219) → 거래
타임라인 → 운영 상태(Health) 순이고, **백테스트는 「분석·거래」 한 탭으로 합쳐 맨 뒤로
강등**되고 **지연 로딩**된다(WAN-220 원칙 유지 — 제거가 아니라 강등. 지우지 않은 이유는
백테스트가 라이브 실측과 대조하는 **잣대**이기 때문이다: 약속·기대수익이 아니라 대조용).

**차트 탭(메인)**: 채택 유니버스(`DEFAULT_SYMBOLS`) × 작업 TF(15m·1h·2h·4h) 중 하나를 골라 봉과
**활성 오더블록 6개**(+ 그 구간의 죽은 존 회색, WAN-289 사용자 결정 2026-08-12)만
그린다. 분석 탭 cold load ~10초(WAN-202)의 원인이던 "6년치 재계산 + 통째 전송"이 이
화면에는 **구조적으로 없다** — 읽는 양 자체가 다르다.

**잔고·거래내역 탭(구 「페이퍼 성과」)**: 페이퍼 러너가 적재한 거래·잔고·성과를 조회하고,
지갑 에쿼티 곡선에 **MDD 구간**을 빨갛게 표시한다.
**진입/미진입 장부 탭(WAN-217/219)**: 페이퍼 러너의 진입 깔때기(체결/미체결/스킵/거부
사유)를 계산 없이 조회 — 체결률·미진입 사유 분포·칸별 필터.
**운영 상태(Health) 탭**: 데이터 신선도·펀딩·러너 생존·페이퍼 포지션·최근 신호를
한눈에 보여, 수집이 멈췄는지/러너가 살아있는지 즉시 식별한다.
**분석·거래 탭(참고·대조, 지연 로딩)**: 옛 「분석」과 「저장된 거래」(WAN-106)를 **진짜
한 화면**으로 병합했다(WAN-289 목업 정렬). 성과 카드 6종(총수익·MDD·승률·거래수·체결률·
최종 시드) + 캔들+오더블록 차트 하나(적재된 **채택 엔진(B안 존-지정가)** 실행의 진입·청산
마커, WAN-199) + 청산사유 칩(잔고 탭과 같은 어휘) + 거래 리스트 하나(행 클릭 → 차트 점프)
+ 미체결 셋업. 화면에서 B안 백테스트(1분봉 substep, 단일 조합 ~7분)를 다시 돌리지 않는다
— 손익·거래는 `backtest.run --persist`가 넣어 둔 결과를 조회(`BacktestRunStore`)한다.
차트의 존은 컨플루언스 파라미터와 무관한 오더블록 탐지(상위TF에서 수 초)로 그리고,
기간 슬라이더는 그 **차트 뷰**만 좁힌다(성과 지표는 적재된 전체 실행 기준). 적재본이
없으면 재계산 대신 넣는 방법을 안내한다.

로컬 실행형이며 외부 노출/인증은 범위 밖이다.

실행::

    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from backtest.models import BacktestConfig, BacktestMetrics, BacktestResult
from backtest.report import COL_EXIT_REASON, trades_to_dataframe, trades_to_display_frame
from backtest.trade_store import BacktestRunStore, RunSummary, engine_revision
from common.timefmt import KST, format_kst, format_kst_zoned
from config import get_settings
from config.settings import Settings
from dashboard.charts import (
    ZONE_CATEGORY_LABELS,
    ZoneCategory,
    build_equity_chart,
    build_wallet_equity_chart,
    filter_zones,
)
from dashboard.data_access import list_series, load_ohlcv, series_bounds
from dashboard.funnel_ledger import (
    cell_options,
    fill_rate_by_cell,
    filter_entries,
    ledger_frame,
    reason_distribution,
    reason_options,
    to_funnel_counts,
)
from dashboard.health import (
    CollectorStatus,
    FundingFreshness,
    HealthLevel,
    RunnerStatus,
    SeriesFreshness,
    compute_runner_status,
    runner_cycle_budget_ms,
)
from dashboard.health_data import HealthView, OpenPositionView, build_health_view, latest_close
from dashboard.lightweight_chart import BAND_LINE_COLOR, build_chart_html
from dashboard.live_board import (
    ACTIVE_ZONE_LIMIT,
    REASON_FILTER_ALL,
    REASON_FILTER_OPTIONS,
    RIGHT_PAD_RATIO,
    OpenPositionRow,
    build_open_position_row,
    chart_start_ms,
    chart_symbols,
    chart_timeframes,
    display_zones,
    filter_records_by_choice,
    legend_title,
    max_drawdown_window,
    open_positions_frame,
    total_unrealized_usd,
    wallet_equity_points,
    wallet_trade_frame,
    zone_view_start_ms,
)
from dashboard.live_chart import LIVE_INTERVALS, build_live_config
from dashboard.saved_trades import (
    filter_by_reason_chip,
    run_label,
    selected_trade_no,
    setups_display_frame,
    zone_limit_runs,
)
from dashboard.setup_compare_view import setup_compare_html
from dashboard.trade_table import (
    engine_label_caption,
    parse_selected_rows,
    selected_trade_window,
    style_trade_frame,
)
from dashboard.trade_timeline_view import (
    backtest_day_summary,
    backtest_only_note,
    chart_window,
    selected_row,
    timeline_frame,
)
from data.integrity import IntegrityReport
from data.integrity import inspect as inspect_db_integrity
from data.storage import OhlcvStore, source_timeframe
from live.order_journal import LedgerEntry, OrderJournal
from live.runtime_state import EventRecord, RuntimeStateStore
from live.setup_compare import build_setup_comparisons
from live.timeline_cache import (
    TimelineCacheStore,
    compute_and_persist_day,
    current_engine_label,
    load_cached_day,
    load_full_universe_day,
)
from live.trade_timeline import (
    STATUS_BACKTEST_CLOSED,
    DayTimeline,
    TimelineRow,
    backtest_setup_rows,
    live_timeline_rows,
    resolve_day_window,
)
from paper.performance import build_performance
from paper.report import (
    performance_to_dataframe,
    performance_to_display_frame,
    records_to_dataframe,
    records_to_display_frame,
)
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.confluence import SignalKind
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    SignalExitReason,
    select_active,
)
from strategy.order_blocks import OrderBlockDetector


def _ms_to_datetime(ms: int) -> datetime:
    """epoch ms → **KST** 벽시계 datetime — 기간/재생 위젯이 KST 날짜로 보이게 한다(WAN-193).

    ⚠️ 표시·입력 **경계 전용** 변환이다(WAN-172/146 원칙). 여기서 나온 datetime을
    질의로 다시 넣을 때는 반드시 `_datetime_to_ms`로 되돌린다 — 저장·질의·백테스트는
    epoch ms(UTC 등가)로만 오간다. 사용자 결정(2026-07-26): 화면에 보이는 모든 날짜는
    한국시간이다(차트 축·현재봉은 lightweight_chart.py의 표시 포맷터가 담당).
    """
    return datetime.fromtimestamp(ms / 1000, tz=KST)


def _datetime_to_ms(value: datetime) -> int:
    """KST 벽시계 datetime(위젯이 돌려준 값) → epoch ms. 경계에서만 변환(WAN-193).

    Streamlit 슬라이더는 넘긴 벽시계 값을 그대로 돌려주므로 KST로 해석한다. `replace`는
    tz-aware(KST)든 naive든 같은 벽시계를 KST로 고정해 `_ms_to_datetime`와 정확히
    왕복한다(저장·질의는 UTC 등가 ms 불변 — 테스트가 왕복을 고정).
    """
    return int(value.replace(tzinfo=KST).timestamp() * 1000)


# --- 포맷 헬퍼 (Health) ------------------------------------------------------


def _fmt_time(ms: int | None) -> str:
    """Health 탭의 시각(KST, WAN-172). 거래 표와 같은 공용 포맷터를 쓴다 —
    화면 안에서 탭마다 시간대가 다르면 같은 사건이 다른 시각으로 보인다.
    ⚠️ 기간/재생 위젯도 KST다(WAN-193 — `_ms_to_datetime`가 경계에서 변환). 차트
    시간축·현재봉은 lightweight_chart.py의 표시 포맷터가 KST로 그린다. 내부
    저장·질의·백테스트·CSV는 UTC 등가 ms 그대로다."""
    return format_kst_zoned(ms)


def _fmt_lag(lag_ms: int | None) -> str:
    """지연(ms)을 사람이 읽기 좋게. 음수(미래 예측값)는 '실시간'."""
    if lag_ms is None:
        return "—"
    if lag_ms < 0:
        return "실시간"
    minutes = lag_ms / 60_000
    if minutes < 60:
        return f"{minutes:.0f}분"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}시간"
    return f"{hours / 24:.1f}일"


_LEVEL_BADGE = {
    HealthLevel.OK: "🟢 정상",
    HealthLevel.STALE: "🔴 지연",
    HealthLevel.UNKNOWN: "⚪ 없음",
}


def _direction_label(direction: OrderBlockDirection) -> str:
    return "롱" if direction is OrderBlockDirection.BULLISH else "숏"


def _kind_label(kind: SignalKind, exit_reason: SignalExitReason | None) -> str:
    if kind is SignalKind.ENTRY:
        return "진입"
    if exit_reason is SignalExitReason.TAKE_PROFIT:
        return "익절"
    if exit_reason is SignalExitReason.STOP_LOSS:
        return "손절"
    return "청산"


# --- 캐시 계층 (WAN-49) ------------------------------------------------------
#
# Streamlit은 위젯 조작(슬라이더 이동 등)마다 스크립트를 처음부터 재실행하므로,
# 캐시가 없으면 심볼/기간을 조금만 바꿔도 OHLCV 로드·오더블록 탐지·백테스트가
# 통째로 재계산된다(3년치에서 수십 초). 아래 래퍼는 각 단계를 `st.cache_data`로
# 감싸 캐시 키(심볼·타임프레임·기간·파라미터)가 같으면 즉시(캐시 히트) 응답한다.
#
# TTL: 시리즈 목록처럼 자주 바뀌는 가벼운 읽기는 짧게(WAN-48 자동 새로고침 주기와
# 정합), 무거운 계산(OHLCV·파이프라인)은 길게 둔다. 파라미터는 해시 불가능한
# pydantic 객체라 `_` 접두(해시 제외) 인자로 넘기고, 대신 직렬화한 `params_key`를
# 캐시 키에 포함시킨다 — 키에서 빠지면 잘못된 결과를 캐시하게 되므로 주의.

_SERIES_TTL_SECONDS = 60
_HEAVY_TTL_SECONDS = 3600

#: 분석 탭 기간 슬라이더의 기본 폭(일). 전 구간이 기본이면 6년 데이터를 매번 탐지·
#: 백테스트하고 브라우저로 보낸다 — 최근 구간만 기본으로 두고 나머지는 옵트인이다(WAN-188).
_DEFAULT_WINDOW_DAYS = 180


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_series(db_path: str) -> list[tuple[str, str]]:
    return list_series(db_path)


@st.cache_data(ttl=_HEAVY_TTL_SECONDS, show_spinner=False)
def _cached_ohlcv(
    db_path: str,
    symbol: str,
    timeframe: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> pd.DataFrame:
    return load_ohlcv(db_path, symbol, timeframe, start_ms=start_ms, end_ms=end_ms)


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_bounds(db_path: str, symbol: str, timeframe: str) -> tuple[int, int] | None:
    """시리즈의 첫/마지막 봉(WAN-188) — 인덱스만 읽는다."""
    return series_bounds(db_path, symbol, timeframe)


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_revision() -> str:
    """분석 캐시 키에 실을 코드 리비전(WAN-188).

    `git` 호출이 재실행마다 반복되지 않게 짧게 캐시한다 — 슬라이더를 한 칸 옮길 때마다
    프로세스를 두 번 띄우는 비용(실측 ~57ms)은 성능 개선의 취지에 어긋난다.
    """
    return engine_revision()


@st.cache_data(ttl=_HEAVY_TTL_SECONDS, show_spinner="오더블록 탐지 중…")
def _cached_detection(
    params_key: str,
    _df: pd.DataFrame,
    _ob_params: OrderBlockParams,
) -> tuple[list[OrderBlock], list[OrderBlock]]:
    """차트용 오더블록 탐지(전체 아카이브 + 마지막 봉 렌더 뷰) — **조회이지 백테스트가
    아니다**(WAN-199).

    오더블록 탐지는 컨플루언스 파라미터와 무관하고(WAN-59) 상위TF에서 수 초면 끝난다
    (WAN-188 실측: 6년 15m ≈ 6.80초). B안 진입가·손익을 만드는 1분봉 substep 백테스트
    (단일 조합 ~7분)와 전혀 다른 계산이라, 화면에서 그 7분을 다시 돌리지 않는다는 약속을
    깨지 않는다. 진입 마커·성과는 적재된 B안 실행에서 조회한다(`_cached_saved_run`).

    `params_key`(심볼·TF·기간·오더블록 파라미터·코드 리비전)가 캐시 키다 — 리비전이 들어가
    엔진이 바뀌면 옛 탐지를 꺼내 오지 않는다(WAN-106 원칙). `_df`는 해시 불가라 키에서
    빼되 `params_key`가 그 구간을 유일하게 가리킨다.
    """
    detection = OrderBlockDetector(_ob_params).run(_df)
    return detection.order_blocks, detection.rendered_order_blocks


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_open_positions(db_path: str) -> list[OpenPositionRow]:
    """페이퍼 러너의 오픈 포지션 + 현재가 기준 미실현 손익(WAN-245 메인 탭).

    소스는 **`open_positions` 테이블**이다(이슈 본문이 못 박은 소스). 러너 상태파일
    스냅샷을 쓰면 **수량이 없어** 달러 미실현 손익을 낼 수 없다 — 목업의
    `+58.1 (+1.08%)`가 달러와 %를 함께 요구한다.

    Health 탭이 쓰는 `build_health_view`는 신선도·펀딩·이벤트까지 통째로 조립하므로
    표 하나 그리자고 부르기엔 무겁다. 여기서는 포지션 + 최신 종가만 읽는다(짧은 TTL —
    러너가 포지션을 열고 닫는 주기를 따라간다).
    """
    with PaperTradeStore(db_path) as paper_store:
        positions = [p.position for p in paper_store.load_open_positions()]
    if not positions:
        return []
    with OhlcvStore(db_path) as store:
        return [
            build_open_position_row(
                position, latest_close(store, position.symbol, position.timeframe)
            )
            for position in positions
        ]


# --- 차트(메인) 탭 (WAN-245) -------------------------------------------------
#
# 대시보드 첫 화면 = 라이브 차트다(사용자 결정 2026-08-04, 목업 승인 2026-08-11). 분석
# 탭과 성격이 정반대다: 저기는 적재된 백테스트 실행을 **대조용**으로 되짚는 화면이고,
# 여기는 **지금 시장이 어떻게 생겼고 내 포지션이 어디에 있나**를 보는 화면이다.
#
# 🔑 cold load가 가벼운 이유는 캐시가 아니라 **읽는 양**이다(WAN-202 흡수) — 6년 전량을
# 탐지·전송하던 분석 탭과 달리 최근 `CHART_BARS`봉만 읽고, 존은 활성 `ACTIVE_ZONE_LIMIT`개
# (+ 그 구간의 죽은 존 회색, WAN-289)만 그린다. 심볼·TF를 바꿔도 그 크기는 그대로다.


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_runner_status(
    runtime_state_path: str, poll_seconds: int, stale: float, cycle_budget_ms: int | None
) -> RunnerStatus:
    """상단 상태 pill용 러너 생존 판정 — 상태파일 한 번만 읽는다.

    Health 탭과 **같은 판정 함수**(`compute_runner_status`)를 쓴다 — 두 벌로 갈라지면
    같은 러너가 위에서는 생존, 아래에서는 멈춤으로 보인다. 완주 지표(WAN-313)도 같은
    이유로 여기서 함께 판정한다.
    """
    runtime = RuntimeStateStore(runtime_state_path).load()
    return compute_runner_status(
        last_poll_ms=runtime.updated_at,
        last_notification_ms=runtime.last_notification_at,
        now_ms=int(time.time() * 1000),
        poll_interval_seconds=poll_seconds,
        stale_multiplier=stale,
        last_cycle_ms=runtime.last_cycle_completed_at,
        cycle_duration_ms=runtime.last_cycle_duration_ms,
        cycle_budget_ms=cycle_budget_ms,
    )


def _render_status_pill(settings: Settings) -> None:
    """목업 상단 오른쪽의 상태 pill — `● 페이퍼 러너 · 틱 피드 · KST hh:mm`.

    ⚠️ 점 색은 **실제 판정**에서 온다(Health 탭과 같은 함수) — 늘 초록인 장식 배지를
    달면 러너가 죽어도 화면이 멀쩡해 보인다.
    """
    status = _cached_runner_status(
        settings.live_runtime_state_path,
        settings.live_poll_interval_seconds,
        settings.health_stale_multiplier,
        runner_cycle_budget_ms(settings.live_signal_timeframes),
    )
    dot = {HealthLevel.OK: "🟢", HealthLevel.UNKNOWN: "⚪"}.get(status.level, "🔴")
    feed = "틱 피드" if settings.live_tick_feed_enabled else "1분봉 폴링"
    last = "폴링 기록 없음" if status.last_poll_ms is None else format_kst(status.last_poll_ms)
    st.caption(f"{dot} 페이퍼 러너 · {feed} · 마지막 폴링 {last} KST")


def _zone_swatch(fill: str, line: str, *, left: int = 0) -> str:
    """존 색 범례의 색칩 한 개(HTML) — 목업 상단 오른쪽 줄."""
    return (
        f"<span style='display:inline-block;width:10px;height:10px;border-radius:2px;"
        f"background:{fill};border:1px solid {line};margin-left:{left}px;"
        "margin-right:5px;vertical-align:-1px;'></span>"
    )


def _render_live_chart(settings: Settings) -> None:
    """차트(메인) 탭 = 차트 + 그 아래 오픈 포지션.

    ⚠️ 두 부분은 **서로 독립**이다 — 고른 (심볼·TF)에 봉이 없어 차트를 못 그려도 오픈
    포지션 표는 그린다(포지션은 다른 칸에 있을 수 있고, 그게 "지금 상태"를 보러 온
    사용자가 첫 화면에서 잃으면 안 되는 정보다).
    """
    _render_chart_panel(settings)
    _render_open_positions(settings)


def _render_chart_panel(settings: Settings) -> None:
    db_path = settings.db_path
    symbols = chart_symbols(settings)
    timeframes = chart_timeframes(settings)

    # 목업 상단 컨트롤 줄: 심볼 드롭다운 · TF 세그먼트 · 오른쪽에 존 색 범례.
    # ⚠️ TF 라벨은 **원문 그대로**(`15m`·`1h`·`2h`·`4h`)다 — 목업의 토글이 그렇고,
    # 한글로 바꾸면 트레이딩뷰 감각과 어긋난다. 차트 좌상단 OHLC 범례에서만 한글로
    # 읽어 준다(`1시간`) — 거기는 문장이라 목업도 한글이다.
    head_left, head_mid, head_right = st.columns([2, 3, 3])
    symbol = head_left.selectbox("심볼", symbols, key="live_chart_symbol")
    timeframe = head_mid.radio(
        "타임프레임",
        timeframes,
        horizontal=True,
        key="live_chart_timeframe",
        help=(
            "채택 좌표의 작업 TF입니다(WAN-182·WAN-252). 2h는 저장된 1h를 무손실 "
            "리샘플해 만듭니다(WAN-24)."
        ),
    )
    head_right.markdown(
        f"<div style='text-align:right;padding-top:30px;font-size:12px;color:#787b86;'>"
        f"{_zone_swatch('rgba(38,166,154,.6)', '#26a69a')}수요·활성"
        f"{_zone_swatch('rgba(239,83,80,.6)', '#ef5350', left=12)}공급·숏"
        f"{_zone_swatch('rgba(150,158,170,.4)', '#9aa0ac', left=12)}무효화"
        f"<span style='margin-left:12px;color:#d1d4dc;'>활성 {ACTIVE_ZONE_LIMIT}개</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ⚠️ 경계는 **물리 저장 TF**에서 읽는다 — 2h는 `ohlcv` 테이블에 행이 없어(`파생`)
    # 인덱스 경로가 "없음"을 내고, 그러면 폴백이 전 구간을 통째로 리샘플한다(이 화면이
    # 피하려는 바로 그 비용).
    bounds = _cached_bounds(db_path, symbol, source_timeframe(timeframe))
    if bounds is None:
        st.warning(
            f"`{symbol}`의 데이터가 없습니다. 수집기(`python -m data.collector`)가 채우면 "
            "여기에 차트가 그려집니다."
        )
        return

    last_ms = bounds[1]
    start_ms = chart_start_ms(last_ms, timeframe)
    df = _cached_ohlcv(db_path, symbol, timeframe, start_ms, last_ms + 1)
    if df.empty:
        st.warning("선택한 심볼·타임프레임의 최근 구간에 봉이 없습니다.")
        return

    # 존은 오더블록 탐지로 그린다(컨플루언스 파라미터와 무관 — WAN-59). 최근 창만 보므로
    # 6년 탐지(~7초)가 아니라 즉시 끝난다. 채택 탐지 기본값(`OrderBlockParams()`)을 쓴다.
    ob_params = OrderBlockParams()
    detection_key = (
        f"live|{symbol}|{timeframe}|{start_ms}|{last_ms}"
        f"|{ob_params.model_dump_json()}|{_cached_revision()}"
    )
    order_blocks, _rendered = _cached_detection(detection_key, df, ob_params)
    # 활성 존 6개 + 그 구간의 죽은 존(회색) — 첫 화면 창도 가장 오래된 활성 존까지
    # 넓힌다(사용자 결정 2026-08-12, WAN-289).
    zones = display_zones(order_blocks)
    view_from_ms = zone_view_start_ms(zones, timeframe)

    # 진입 기준선(볼린저 하단)은 채택 기본값으로 그린다 — 표시선(EMA/VWMA)은 채택 규칙이
    # 쓰지 않으므로 이 화면에서는 아예 싣지 않는다(페이로드의 절반이던 것, WAN-188).
    conf_params = ConfluenceParams()
    live_config = build_live_config(
        df,
        symbol=symbol,
        timeframe=timeframe,
        conf_params=conf_params,
        band_color=BAND_LINE_COLOR,
    )

    chart_height = 620
    st.iframe(
        build_chart_html(
            df,
            zones,
            None,
            conf_params=conf_params,
            visible_lines=frozenset(),
            theme=_current_chart_theme(),
            height=chart_height,
            live=live_config,
            ohlc_legend_title=legend_title(symbol, timeframe),
            independent_axis=True,
            right_pad_ratio=RIGHT_PAD_RATIO,
            view_from_ms=view_from_ms,
        ),
        height=chart_height,
    )

    live_note = (
        "🟢 형성 중인 봉과 볼린저 하단선이 라이브로 갱신됩니다(브라우저가 바이낸스 웹소켓에 "
        "직접 구독 · 저장하지 않음)."
        if live_config is not None
        else f"⚪ {timeframe}은 바이낸스 kline 스트림 인터벌이 아니라 확정봉까지만 그립니다."
    )
    st.caption(
        f"{live_note} 최근 {len(df):,}봉 · 무효화된 존은 생성부터 무효화 봉까지만 회색으로 "
        "그립니다. 휠 = 좌우 확대/축소 · 가격축 드래그 = 세로 확대/축소 · 가격축 더블클릭 = "
        "세로 맞춤."
    )


def _render_open_positions(settings: Settings) -> None:
    """차트 아래 「현재 오픈 포지션」(WAN-245 완료 기준 3).

    ⚠️ 페이퍼 러너가 쓰는 **서버 DB·상태파일** 기준이다 — 로컬 스냅샷을 보고 있으면
    "오픈 포지션 없음"이 정상이다(WAN-195).
    """
    st.subheader("현재 오픈 포지션")
    rows = _cached_open_positions(settings.db_path)
    if not rows:
        st.info(
            "오픈 포지션이 없습니다. 페이퍼 러너(`python -m live.runner`)가 진입하면 "
            "여기에 표시됩니다."
        )
        return
    st.dataframe(open_positions_frame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "칸=(종목,TF)마다 1포지션 · 여러 칸이 한 지갑을 공유하는 레버리지 북입니다"
        "(WAN-213). 미실현손익은 최신 확정봉 종가 기준입니다."
    )


# --- 분석 탭 ----------------------------------------------------------------


def _resolve_chart_theme() -> str:
    """차트 테마(`"light"`/`"dark"`)를 결정한다 (WAN-55).

    사이드바 오버라이드(자동/라이트/다크)가 우선하고, "자동"이면
    `st.get_option("theme.base")`(Streamlit 설정 ⋮ → Settings → Theme)를 따라간다.
    선택은 위젯 `key`로 `st.session_state`에 유지돼 재실행 후에도 초기화되지 않는다.
    기본은 "자동"이며, 기본 Streamlit 테마가 다크(`.streamlit/config.toml`)라 처음엔
    다크로 뜬다.
    """
    with st.sidebar:
        st.subheader("차트 테마")
        choice = st.radio(
            "테마",
            options=("자동", "라이트", "다크"),
            index=0,
            key="chart_theme_choice",
            help="자동은 Streamlit 테마를 따라갑니다(⋮ → Settings → Theme). 기본은 다크.",
        )
    return _chart_theme_from_choice(choice)


def _chart_theme_from_choice(choice: str) -> str:
    if choice == "라이트":
        return "light"
    if choice == "다크":
        return "dark"
    base = st.get_option("theme.base")
    return "light" if base == "light" else "dark"


def _current_chart_theme() -> str:
    """이미 만들어진 테마 위젯의 선택값을 **읽기만** 한다 (WAN-106).

    `_resolve_chart_theme`를 두 번째 탭에서 다시 부르면 같은 `key`의 사이드바 위젯을
    또 만들게 되어 Streamlit이 중복 키로 죽는다. 탭마다 각자 위젯을 두면 두 탭의 테마가
    갈라지므로, 위젯은 `main()`이 **한 번만** 만들고 나머지는 그 상태를 읽는다
    (WAN-245 이전에는 분석 탭이 만들었는데, 그 탭이 지연 로딩이라 메인 차트만 보는
    동안에는 테마 선택이 화면에서 사라졌다).
    """
    return _chart_theme_from_choice(str(st.session_state.get("chart_theme_choice", "자동")))


def _select_chart_zones(
    order_blocks: list[OrderBlock],
    df: pd.DataFrame,
    ob_params: OrderBlockParams,
    *,
    replay_ms: int | None,
    categories: frozenset[ZoneCategory],
    show_all_archive: bool,
) -> tuple[pd.DataFrame, list[OrderBlock]]:
    """표시 옵션에 따라 차트에 넘길 (캔들 df, 존 목록)을 고른다 (WAN-52).

    - **시점 재생**(``replay_ms``): 그 시각 T에 트레이딩뷰가 그렸을 존(방향별
      ``zone_limit``개, 병합)만 `select_active`로 파생하고, 캔들도 T까지 잘라 그
      시점 화면을 정확히 재현한다(≤6개).
    - **전체 아카이브**: 생성된 모든 존(무거움).
    - 그 외: 선택된 범주(활성/지지/깨짐/소멸)로 필터.

    ⚠️ **"진입한 존"(ENTERED) 범주는 여기서 못 만든다** — 그건 A안 시그널 재생의
    산물인데(WAN-52) 분석 탭은 이제 B안 실행을 **조회**만 하므로 시그널이 없다(WAN-199).
    실제 진입 자리는 적재된 B안 거래 마커가 차트에 직접 보여 준다.
    """
    if replay_ms is not None:
        chart_df = df[df["open_time"] <= replay_ms]
        zones = select_active(
            order_blocks,
            replay_ms,
            limit=ob_params.zone_limit,
            combine=ob_params.combine_obs,
        )
        return chart_df, zones
    if show_all_archive:
        return df, list(order_blocks)
    return df, filter_zones(order_blocks, categories)


def _run_config_badge_text(
    conf_params: ConfluenceParams, ob_params: OrderBlockParams, bt_config: BacktestConfig
) -> str:
    """현재 실행 설정을 한 줄로 요약한다(WAN-65).

    "구현은 됐는데 실제 실행 경로에 안 붙어서 조용히 잘못된 값이 나온다"는 이
    프로젝트의 반복 버그 패턴(WAN-47/56/59/63/65)에 대한 방어책 — 대시보드가 지금
    무슨 설정으로 백테스트를 돌리고 있는지 화면에 항상 드러낸다.
    """
    # 채택 진입은 존-지정가(B안) 단독이다 — A안(종가) 경로는 WAN-208에서 제거됐다.
    entry_label = "B안(존-지정가)"
    rsi_label = "확정봉" if conf_params.rsi_mode == "closed_bar" else "실시간"
    if bt_config.risk_sizing is not None:
        sizing_label = f"리스크 {bt_config.risk_sizing.risk_per_trade * 100:.1f}%"
    else:
        sizing_label = f"전액({bt_config.position_fraction * 100:.0f}%, 사이징 미적용)"
    merge_label = "ON" if ob_params.combine_obs else "OFF"
    funding_label = "반영됨" if bt_config.funding_enabled else "미반영"
    return (
        f"진입: {entry_label} · RSI: {rsi_label} · 사이징: {sizing_label} · "
        f"병합: {merge_label} · 펀딩비: {funding_label}"
    )


def _render_run_config_badge(
    conf_params: ConfluenceParams,
    ob_params: OrderBlockParams,
    bt_config: BacktestConfig,
    metrics: BacktestMetrics,
) -> None:
    """분석 탭 상단 실행 설정 배지. 비정상 설정(사이징 미적용·펀딩 커버리지 미달)은
    경고 색으로 강조한다(WAN-65).
    """
    text = _run_config_badge_text(conf_params, ob_params, bt_config)
    coverage = metrics.funding_coverage
    abnormal = bt_config.risk_sizing is None or (coverage is not None and coverage < 1.0)
    if abnormal:
        st.warning(f"⚙️ {text}")
    else:
        st.caption(f"⚙️ {text}")


#: 거래 표 위젯 key. 차트가 표보다 **위에** 그려지므로, 선택된 행을 알려면 위젯을 만들기
#: 전에 지난 실행의 선택 상태를 `st.session_state`에서 꺼내야 한다(WAN-146).
_TRADE_TABLE_KEY = "trade_table_selection"

#: 청산사유 칩 위젯 key(WAN-289 병합 화면). 표와 같은 이유로 — 칩이 차트보다 **아래**
#: 그려지므로 선택된 거래 → 차트 점프 계산은 지난 실행의 칩 상태를 읽는다.
_REFERENCE_REASON_KEY = "reference_exit_reason"


def _selected_trade_rows() -> list[int]:
    """거래 표에서 선택된 행 위치. 아직 표가 없거나 선택이 없으면 빈 목록."""
    return parse_selected_rows(st.session_state.get(_TRADE_TABLE_KEY))


def _render_trade_table(
    frame: pd.DataFrame,
    backtest: BacktestResult,
    conf_params: ConfluenceParams,
    ob_params: OrderBlockParams,
    bt_config: BacktestConfig,
) -> None:
    """거래 표 (WAN-146) — KST 시각·진입금액·시드 변화 + 행 선택 → 차트 점프.

    표의 내용은 `trades_to_display_frame`(대시보드와 CSV 내보내기 공용)이 만들고
    청산사유 칩으로 좁혀진 `frame`을 받아 여기서는 Streamlit 위젯으로 그리기만 한다.
    매 행에서 값이 같던 엔진 라벨 6개는 표 본문에서 빼되 아래 expander에 원본 컬럼
    전체와 함께 **보존**한다(WAN-65 — 삭제가 아니다).
    """
    st.subheader("거래 목록")
    st.caption(
        "시각은 **한국시간(KST)** 입니다(내부 계산·저장은 UTC 그대로). "
        "행을 누르면 위 차트가 그 거래의 진입~청산 구간으로 이동합니다."
    )
    st.radio(
        "청산사유",
        options=REASON_FILTER_OPTIONS,
        horizontal=True,
        key=_REFERENCE_REASON_KEY,
        help=(
            "잔고 탭과 같은 세 갈래(전체/익절만/손절만)입니다. 시드(전)·시드(후)·행 "
            "번호(#)는 좁혀도 전체 실행 기준 그대로입니다."
        ),
    )
    st.dataframe(
        style_trade_frame(frame),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=_TRADE_TABLE_KEY,
    )
    with st.expander("실행 설정 · 원본 컬럼(모든 거래 공통)"):
        st.caption(f"⚙️ {engine_label_caption(backtest, conf_params, ob_params, bt_config)}")
        st.dataframe(
            trades_to_dataframe(backtest, confluence=conf_params, order_block=ob_params),
            use_container_width=True,
        )


#: 분석 탭 존 표시 필터의 기본/선택지. "진입한 존"(ENTERED)은 A안 시그널 재생이 있어야
#: 만들 수 있는데 분석 탭은 이제 B안 실행을 **조회**만 하므로(WAN-199) 제외한다 — 실제
#: 진입 자리는 적재된 거래 마커가 차트에 직접 보여 준다. 기본은 활성 + 지지(탭)한 존.
_ANALYSIS_ZONE_CATEGORIES: tuple[ZoneCategory, ...] = tuple(
    c for c in ZoneCategory if c is not ZoneCategory.ENTERED
)
_ANALYSIS_DEFAULT_CATEGORIES: frozenset[ZoneCategory] = frozenset(
    {ZoneCategory.ACTIVE, ZoneCategory.TAPPED}
)


def _analysis_persist_hint(db_path: str, symbol: str, timeframe: str) -> None:
    """이 (심볼·TF)에 적재된 B안 실행이 없을 때 — **빈 화면 대신 넣는 방법**을 보여준다.

    분석 탭은 더 이상 화면에서 A안으로 재계산하지 않는다(WAN-199). 채택 엔진(B안 지정가)
    거래를 한 번 적재해 두면 여기서 계산 없이 조회한다 — 저장된 거래 탭과 같은 인프라다.
    """
    st.info(
        f"**{symbol} · {timeframe}** 에 적재된 채택 엔진(B안 존-지정가) 실행이 없습니다.\n\n"
        "분석 탭은 화면에서 B안 백테스트(1분봉 substep, ~7분)를 다시 돌리지 않고 적재된 "
        "결과를 조회합니다(WAN-199). 아래처럼 한 번 계산해 넣어 두세요:\n\n"
        "```bash\n"
        "uv run python -m backtest.run --symbol BTCUSDT --tf 15m --persist\n"
        "```\n\n"
        "적재하면 이 탭이 캔들·오더블록 위에 그 실행의 거래 마커·성과를 조회로 얹습니다. "
        f"적재 대상 DB: `{db_path}`"
    )


def _render_analysis(settings: Settings) -> None:
    db_path = settings.db_path

    series = _cached_series(db_path)
    if not series:
        st.warning(
            f"저장된 OHLCV 데이터가 없습니다 ({db_path}). 먼저 데이터 수집(WAN-6)을 실행하세요."
        )
        return

    # 테마 위젯은 `main()`이 한 번만 만든다(WAN-245) — 이 탭은 지연 로딩이라 여기서
    # 만들면 메인 차트가 열려 있는 동안 사이드바에 테마 선택이 아예 없다.
    chart_theme = _current_chart_theme()

    symbols = sorted({symbol for symbol, _ in series})
    with st.sidebar:
        st.header("선택")
        symbol = st.selectbox("심볼", symbols)
        timeframes = sorted({tf for s, tf in series if s == symbol})
        timeframe = st.selectbox("타임프레임", timeframes)

    # 채택 엔진(B안 지정가) 실행을 **조회**한다 — 저장된 거래 탭과 같은 인프라(WAN-199).
    # 화면에서 B안 백테스트(1분봉 substep, ~7분)를 다시 돌리지 않는다. 적재본이 없으면
    # 재계산 대신 넣는 방법을 안내한다(조용한 7분 대기 금지).
    runs = zone_limit_runs(_cached_saved_runs(db_path), symbol=symbol, timeframe=timeframe)
    if not runs:
        _analysis_persist_hint(db_path, symbol, timeframe)
        return

    with st.sidebar:
        if len(runs) > 1:
            run_labels = {run_label(s): s for s in runs}
            chosen = st.selectbox(
                "적재된 실행(실행 지문)", list(run_labels), key="analysis_run_choice"
            )
            summary = run_labels[chosen]
        else:
            summary = runs[0]
    fingerprint = summary.fingerprint
    # 배지·표시선·존 탐지에 쓰는 파라미터는 **적재된 실행의 지문**에서 복원한다 — 화면이
    # 임의 값을 지어내지 않고, 배지가 지문의 `entry_mode`를 읽어 자동으로 "B안(존-지정가)"가
    # 된다(라벨과 실제가 갈라지는 WAN-95 부류 방지).
    conf_params = ConfluenceParams.model_validate_json(fingerprint.confluence_json)
    ob_params = OrderBlockParams.model_validate_json(fingerprint.order_block_json)
    bt_config = BacktestConfig.model_validate_json(fingerprint.config_json)

    # 거래·성과·미체결 셋업은 **조회**다(계산 없음). 지표의 정본은 적재된 요약이지만
    # B안은 엔진이 `build_result_from_trades`로 결과를 만들어 복원 결과와 같다(WAN-106).
    result, setups = _cached_saved_run(db_path, summary.run_id)

    # 차트 뷰(기간) 슬라이더 — 성과·거래는 적재된 **전체 실행** 기준이고, 이 슬라이더는
    # 캔들·존 탐지만 좁혀 페이로드를 관리한다(WAN-188 규율 유지). 범위는 경계 두 개면
    # 충분하다(전 구간을 min/max 구하려 통째로 읽지 않는다).
    bounds = _cached_bounds(db_path, symbol, timeframe)
    if bounds is None:
        st.warning("선택한 심볼/타임프레임에 데이터가 없습니다.")
        return

    first_ms, last_ms = bounds
    min_dt = _ms_to_datetime(first_ms)
    max_dt = _ms_to_datetime(last_ms)
    with st.sidebar:
        # 기본은 **최근 구간**이다(WAN-188). 전 구간이 기본이면 6년치 캔들·존을 매번 탐지해
        # 브라우저로 보낸다(실측: 탐지 6.80초 + 페이로드 수십 MB). 옛 구간은 아래 체크박스로.
        show_full_range = st.checkbox(
            "전 구간 보기(느림)",
            value=False,
            help=(
                f"기본은 최근 {_DEFAULT_WINDOW_DAYS}일입니다. 전 구간을 켜면 가진 캔들 전부에서 "
                "오더블록을 탐지해 차트로 보냅니다(6년 15m 기준 수십 MB — 느립니다). 성과·거래는 "
                "어느 경우든 적재된 전체 실행 기준입니다(이 슬라이더는 차트 뷰만 좁힙니다)."
            ),
        )
        default_start_dt = (
            min_dt
            if show_full_range
            else max(min_dt, max_dt - timedelta(days=_DEFAULT_WINDOW_DAYS))
        )
        if min_dt < max_dt:
            start_dt, end_dt = st.slider(
                "기간",
                min_value=min_dt,
                max_value=max_dt,
                value=(default_start_dt, max_dt),
                format="YYYY-MM-DD HH:mm",
            )
        else:
            start_dt, end_dt = min_dt, max_dt

    start_ms = _datetime_to_ms(start_dt)
    end_ms = _datetime_to_ms(end_dt)
    # 고른 구간만 읽는다. `load_ohlcv`의 `end_ms`는 배타라 `+1`로 마지막 봉을 포함시킨다 —
    # 예전 `full_df[... <= end_ms]` 슬라이스와 **같은 캔들 집합**이어야 화면이 안 바뀐다.
    df = _cached_ohlcv(db_path, symbol, timeframe, start_ms, end_ms + 1)

    if df.empty:
        st.warning("선택한 기간에 데이터가 없습니다.")
        return

    # 존은 오더블록 탐지로 그린다 — 탐지는 컨플루언스 파라미터와 무관하고(WAN-59) 상위TF에서
    # 수 초면 끝나므로 B안 백테스트의 1분봉 재계산(~7분)이 아니다(WAN-199). 진입가·손익은
    # 위에서 조회한 적재 실행(`result`)이 갖고 있다.
    detection_key = (
        f"{symbol}|{timeframe}|{start_ms}|{end_ms}"
        f"|{ob_params.model_dump_json()}|{_cached_revision()}"
    )
    order_blocks, _rendered = _cached_detection(detection_key, df, ob_params)

    label_to_category = {label: cat for cat, label in ZONE_CATEGORY_LABELS.items()}
    with st.sidebar:
        st.subheader("오더블록 표시")
        replay_on = st.checkbox(
            "시점 재생",
            value=False,
            help=(
                "특정 시각 T에 트레이딩뷰가 그렸을 존(방향별 최대 3개)과 그때까지의 "
                "캔들만 재현합니다. '그때 내 화면이 뭘 보여줬나'를 정확히 되짚습니다."
            ),
        )
        replay_ms: int | None = None
        categories = _ANALYSIS_DEFAULT_CATEGORIES
        show_all_archive = False
        if replay_on:
            chart_min = _ms_to_datetime(int(df["open_time"].min()))
            chart_max = _ms_to_datetime(int(df["open_time"].max()))
            if chart_min < chart_max:
                replay_dt = st.slider(
                    "재생 시각(T)",
                    min_value=chart_min,
                    max_value=chart_max,
                    value=chart_max,
                    format="YYYY-MM-DD HH:mm",
                )
            else:
                replay_dt = chart_max
            replay_ms = _datetime_to_ms(replay_dt)
        else:
            default_labels = [
                ZONE_CATEGORY_LABELS[c]
                for c in _ANALYSIS_ZONE_CATEGORIES
                if c in _ANALYSIS_DEFAULT_CATEGORIES
            ]
            selected_labels = st.multiselect(
                "표시 필터",
                options=[ZONE_CATEGORY_LABELS[c] for c in _ANALYSIS_ZONE_CATEGORIES],
                default=default_labels,
                help=(
                    "활성·지지(탭)·깨짐(무효화)·소멸 중 골라 봅니다. 기본은 활성 + 지지한 존. "
                    "실제 진입 자리는 아래 차트의 거래 마커가 보여 줍니다(조회 경로라 '진입한 존' "
                    "필터는 없습니다 — WAN-199)."
                ),
            )
            categories = frozenset(label_to_category[label] for label in selected_labels)
            show_all_archive = st.checkbox(
                "전체 아카이브 표시(무거움)",
                value=False,
                help=(
                    "깨지고 소멸한 존까지 생성된 모든 존을 그립니다. "
                    "3년 15m에서는 느릴 수 있습니다."
                ),
            )
            if show_all_archive:
                st.warning("전체 아카이브는 존이 매우 많아 렌더가 느릴 수 있습니다.")

        st.subheader("실시간")
        live_supported = timeframe in LIVE_INTERVALS
        live_on = st.checkbox(
            "실시간 캔들 갱신",
            value=live_supported,
            disabled=not live_supported,
            help=(
                "브라우저가 바이낸스 웹소켓에 직접 붙어 형성 중인 봉과 볼린저 하단선을 "
                "갱신합니다(트레이딩뷰와 같은 방식). 표시 계층 전용이라 아래 거래 표·"
                "성과 지표는 적재된 백테스트 결과 그대로이고, 받은 데이터는 "
                "저장하지 않습니다."
            ),
        )
        if not live_supported:
            st.caption(f"{timeframe}은 바이낸스 kline 스트림이 지원하지 않는 인터벌입니다.")

        st.subheader("차트 표시선 (EMA/VWMA)")
        # 기본 꺼짐(WAN-188). 채택 규칙은 이 선들을 **하나도 쓰지 않는다** — RSI 게이트가
        # 없고(WAN-123) 익절은 고정 1.5R이라 `use_line_take_profit=False`다. 즉 순수
        # 장식인데 선마다 봉 개수만큼 긴 배열이라 페이로드의 절반을 넘게 먹는다
        # (6년 15m 실측: 75.65MB → 끄면 31.72MB = **58%가 표시선**). 정보 손실은 0이고,
        # 보고 싶으면 아래 체크박스로 언제든 켠다.
        # ⚠️ 진입 기준선인 **볼린저 하단선은 이 토글과 무관하게 계속 그려진다**
        # (`build_chart_html`이 `deviation_filter`에서 따로 만든다) — 끄는 건 장식뿐이다.
        st.caption(
            "차트에 그리는 선입니다(기본 꺼짐 — 채택 규칙은 쓰지 않습니다). 익절 판정은 이 중 EMA "
            f"{'/'.join(str(n) for n in conf_params.sorted_tp_ema_lengths)}"
            + (f" + VWMA {conf_params.tp_vwma_length}" if conf_params.tp_vwma_length else "")
            + "에서만 일어납니다(WAN-66). 진입 기준선인 볼린저 하단선은 항상 그려집니다."
        )
        line_keys = [f"ema_{length}" for length in conf_params.sorted_display_ema_lengths]
        if conf_params.tp_vwma_length is not None:
            line_keys.append(f"vwma_{conf_params.tp_vwma_length}")
        visible_lines: set[str] = set()
        for key in line_keys:
            kind, _, length = key.partition("_")
            label = f"{kind.upper()} {length}"
            if st.checkbox(label, value=False, key=f"line_toggle_{key}"):
                visible_lines.add(key)

    chart_df, zones = _select_chart_zones(
        order_blocks,
        df,
        ob_params,
        replay_ms=replay_ms,
        categories=categories,
        show_all_archive=show_all_archive,
    )
    # 시점 재생은 그 시점 화면 재현이 목적이라 미래 거래 마커를 겹치지 않는다.
    chart_backtest = None if replay_ms is not None else result

    # 실시간 차트(WAN-147) — 표시 계층 전용이다. 브라우저가 바이낸스 웹소켓에 직접 붙어
    # 형성 중인 봉과 볼린저 하단선만 갱신하고, 아래 거래 표·성과 지표는 적재된 백테스트
    # 결과 그대로다(실시간 값이 섞이지 않는다). 시점 재생 중이거나 기간을 과거로 잘라 본
    # 화면에서는 켜지 않는다 — 지나간 구간에 현재 봉을 붙이면 화면이 "그때 무엇을 봤나"를
    # 더는 재현하지 못한다.
    showing_tail = end_ms >= last_ms
    live_config = (
        build_live_config(
            chart_df,
            symbol=symbol,
            timeframe=timeframe,
            conf_params=conf_params,
            band_color=BAND_LINE_COLOR,
        )
        if live_on and replay_ms is None and showing_tail
        else None
    )

    # 거래 표에서 고른 행 → 차트 이동 구간(WAN-146). 표·청산사유 칩은 차트보다 아래에
    # 그려지므로 지난 실행의 상태를 읽고, 칩으로 좁혀진 표의 행 위치는 `#` 열을 거쳐
    # 전체 실행 기준 거래 번호로 되돌린다(WAN-289 병합). 시점 재생 중이면(거래 마커
    # 자체를 안 그린다) 이동도 하지 않고, 선택된 거래가 현재 기간 밖이면 빈 화면으로
    # 뛰지 않게 무시한다.
    reason_choice = str(st.session_state.get(_REFERENCE_REASON_KEY, REASON_FILTER_ALL))
    trades_frame = filter_by_reason_chip(
        trades_to_display_frame(result), reason_choice, column=COL_EXIT_REASON
    )
    trade_no = selected_trade_no(trades_frame, _selected_trade_rows())
    focus = (
        None
        if replay_ms is not None or trade_no is None
        else selected_trade_window(result, [trade_no - 1])
    )
    if focus is not None and not (start_ms <= focus[0] <= end_ms):
        focus = None

    st.subheader(f"{symbol} · {timeframe}")
    # 지금 보고 있는 게 어느 엔진의 거래인지 항상 드러낸다(WAN-65/95) — 저장된 거래 탭이
    # 하던 지문 배지를 병합 화면이 이어받는다. 그 아래 실행 설정 배지는 지문의
    # `entry_mode`를 읽어 "B안(존-지정가)".
    st.caption(f"🔒 실행 지문: {fingerprint.label()} · run_id `{summary.run_id}`")
    _render_run_config_badge(conf_params, ob_params, bt_config, result.metrics)

    # 성과 카드 6종(목업 확정, WAN-289) — 차트보다 **위**다. 값은 적재된 요약(`RunSummary`)
    # 이다: 지표의 정본은 적재된 요약이지 복원 결과가 아니다(WAN-106).
    cards = st.columns(6)
    cards[0].metric("총수익", f"{summary.total_return * 100:.2f}%")
    cards[1].metric("MDD", f"{summary.max_drawdown * 100:.2f}%")
    cards[2].metric("승률", f"{summary.win_rate * 100:.2f}%")
    cards[3].metric("거래수", f"{summary.num_trades:,}")
    cards[4].metric(
        "체결률", "—" if summary.fill_rate is None else f"{summary.fill_rate * 100:.2f}%"
    )
    cards[5].metric("최종 시드", f"{summary.final_equity:,.0f}")

    chart_height = 700
    st.iframe(
        build_chart_html(
            chart_df,
            zones,
            chart_backtest,
            conf_params=conf_params,
            visible_lines=frozenset(visible_lines),
            theme=chart_theme,
            height=chart_height,
            live=live_config,
            focus=focus,
        ),
        height=chart_height,
    )
    st.caption(
        "성과 지표·거래·미체결 셋업은 적재된 **전체 실행** 기준입니다(조회). 위 기간 슬라이더는 "
        "차트에 그릴 캔들·존만 좁힙니다 — 뷰 밖의 거래 마커는 화면에 안 보일 뿐 지표에는 그대로 "
        "반영됩니다(WAN-199)."
    )
    if focus is not None:
        st.caption(
            "🔎 선택한 거래 구간을 보고 있습니다. 표에서 선택을 해제하면 전체 구간으로 돌아갑니다."
        )
    if live_config is not None:
        st.caption(
            "🟢 실시간: 형성 중인 봉과 볼린저 하단선만 옅은 색으로 라이브 갱신됩니다"
            "(바이낸스 웹소켓 직접 구독 · 저장하지 않음). **아래 거래 표·성과 지표는 "
            "적재된 백테스트 결과**라 실시간 값에 영향받지 않습니다."
        )
    elif live_on:
        st.caption("⚪ 실시간 갱신 꺼짐: 시점 재생 중이거나 기간 끝을 과거로 잘라 본 화면입니다.")

    _render_trade_table(trades_frame, result, conf_params, ob_params, bt_config)

    # 미체결 셋업 — "살 뻔했는데 못 산 자리"는 규칙 판단에 체결된 거래만큼 중요하다
    # (WAN-106). 병합 화면(WAN-289)에서도 거래 리스트 바로 아래 한 자리다.
    unfilled = setups[~setups["filled"]] if not setups.empty else setups
    with st.expander(f"미체결 셋업 — 살 뻔했는데 못 산 자리 ({len(unfilled)}건)"):
        if setups.empty:
            st.caption(
                "이 실행에는 셋업 진단이 없습니다(종가 진입·다중 포지션 경로는 미체결이라는 "
                "개념이 없거나 진단을 내지 않습니다)."
            )
        else:
            st.dataframe(setups_display_frame(unfilled), use_container_width=True, hide_index=True)

    # 자본곡선은 expander로 강등 — 목업의 한 화면(카드+차트+거래 리스트)에는 없지만
    # 삭제가 아니라 이동이다(WAN-65 원칙). 지갑 에쿼티 곡선은 잔고 탭 소관이고 이건
    # **백테스트 실행**의 곡선이다(참고·대조).
    with st.expander("백테스트 자본곡선(참고·대조)"):
        st.plotly_chart(build_equity_chart(result, theme=chart_theme), use_container_width=True)


# --- 적재된 실행 조회 인프라 (WAN-106) ---------------------------------------
#
# `backtest.run --persist`가 한 번 계산해 DB에 넣어 둔 **채택 엔진(B안 지정가)** 거래를
# **계산 없이 조회**만 한다. 옛 「저장된 거래」 탭의 화면은 WAN-289에서 분석 탭과 한
# 화면으로 병합됐고(위 `_render_analysis`), 조회 캐시·빈 화면 안내는 그대로 여기 산다.


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_saved_runs(db_path: str) -> list[RunSummary]:
    with BacktestRunStore(db_path) as store:
        return store.list_runs()


@st.cache_data(ttl=_HEAVY_TTL_SECONDS, show_spinner=False)
def _cached_saved_run(db_path: str, run_id: str) -> tuple[BacktestResult, pd.DataFrame]:
    """적재된 실행 하나를 복원한다 — **조회일 뿐 백테스트가 아니다**."""
    with BacktestRunStore(db_path) as store:
        return store.load_result(run_id), store.setups_frame(run_id)


# --- 거래 타임라인 탭 (WAN-234) ---------------------------------------------

_TIMELINE_TABLE_KEY = "trade_timeline_selection"
_TIMELINE_CHART_HEIGHT = 520
#: 선택한 거래 구간의 좌우로 이만큼 더 캔들을 실어 문맥을 준다(차트 여백).
_TIMELINE_CHART_PAD_MS = 12 * 3_600_000

#: 백테스트 대조 대상 라디오(WAN-290). 「라이브 칸만」은 WAN-234 그대로, 「전부」는 임의
#: 날짜 × 채택 좌표 전부(종목 × TF) 온디맨드 실행이다.
_TARGET_LIVE_CELLS = "라이브 칸만 (WAN-234)"
#: 임의 날짜 × 채택 좌표 전부 실행 결과의 세션 캐시(날짜별) — **디스크 캐시 위의 즉시 히트
#: 계층**이다(WAN-297 §1-1). 진짜 저장소는 `TimelineCacheStore`이고, 이 dict는 같은 세션 안의
#: 필터·선택 재실행에서 SQLite를 다시 읽지 않기 위한 것뿐이다(옛 판은 이것만 있어서 앱
#: 재시작·새 브라우저 세션이면 결과가 통째로 날아갔다 — 이 이슈가 고친 자리).
_TIMELINE_FULL_RESULT_KEY = "timeline_full_backtest_by_day"


def full_universe_shape() -> tuple[int, int, int]:
    """채택 좌표의 (종목 수, TF 수, 셀 수)를 코드 기본값에서 읽는다(WAN-318 §6).

    화면 라벨을 **하드코딩하지 않기 위한** 한 곳이다. 예전엔 「9종목」이 문자열 상수였는데
    셀 수는 `len(DEFAULT_SYMBOLS) * len(DEFAULT_TIMEFRAMES)`로 계산돼, 유니버스가 12종목이
    된 뒤(WAN-307) 화면에 **「9종목 × 4TF = 48셀」이라는 자기모순**이 떴다. 이 저장소가
    가장 경계하는 「라벨과 동작이 어긋남」(WAN-91/95/112/123/159 계열)이 사용자 화면에
    그대로 노출된 것이라, 라벨도 좌표에서 파생시킨다.
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES

    return (
        len(DEFAULT_SYMBOLS),
        len(DEFAULT_TIMEFRAMES),
        len(DEFAULT_SYMBOLS) * len(DEFAULT_TIMEFRAMES),
    )


def full_universe_label() -> str:
    """대조 대상 라디오의 「전부」 선택지 라벨 — 좌표에서 파생된다(WAN-318 §6)."""
    n_symbols, n_timeframes, _ = full_universe_shape()
    return f"채택 {n_symbols}종목×{n_timeframes}TF 전부 (WAN-290)"


@dataclass(frozen=True)
class _FullRunResult:
    """임의 날짜 × 채택 좌표 전부 한 판의 세션 캐시 값(WAN-290 · WAN-297).

    행은 튜플(불변)로 담아 세션에 안전히 보관하고, 엔진 배지를 함께 둔다 — 필터·선택
    재실행에서 다시 계산하지 않고 이 값을 그대로 다시 그린다. `elapsed`는 **이번 세션에서
    직접 계산했을 때만** 값이 있다(디스크 캐시에서 읽었으면 `None` — 안 잰 시간을 지어내지
    않는다). `from_cache`가 그 출처를 화면에 밝힌다.
    """

    rows: tuple[TimelineRow, ...]
    elapsed: float | None
    engine: str
    from_cache: bool


def _timeline_live_cell_backtest(
    db_path: str,
    day_key: str,
    start_ms: int,
    end_ms: int,
    live_rows: list[TimelineRow],
) -> list[TimelineRow]:
    """「라이브 칸만」 대조 — 그날 라이브가 있던 (심볼, TF)만 재산출한다(WAN-234 그대로).

    기본은 야간 크론 캐시만 읽고(WAN-239), 체크박스로만 즉시 재계산한다(무겁다). 라이브
    예약이 없던 날은 대조 대상 셀이 없다.
    """
    include_bt = st.checkbox(
        "백테스트 대조 병기 (야간 크론이 미리 계산한 캐시를 읽습니다 — WAN-239)",
        value=False,
        key="timeline_include_bt",
    )
    recompute = st.checkbox(
        "캐시 무시하고 즉시 재계산 (그날 라이브 셀만 · 워밍업 연속 — 무겁습니다)",
        value=False,
        key="timeline_recompute",
    )
    if not include_bt:
        return []

    symbols = sorted({r.symbol for r in live_rows})
    timeframes = sorted({r.timeframe for r in live_rows})
    if not symbols or not timeframes:
        n_symbols, n_timeframes, _ = full_universe_shape()
        st.info(
            "이 날 라이브 예약이 없어 백테스트 대조 대상 셀이 없습니다. 라이브와 무관하게 "
            f"그날 하루치 백테를 보려면 위에서 **채택 {n_symbols}종목×{n_timeframes}TF "
            "전부**를 고르세요(WAN-290)."
        )
        return []

    if recompute:
        # 명시적 온디맨드 재계산(캐시 무시, 무겁다) — 사용자가 골랐을 때만(WAN-239).
        # 캐시가 담는 것과 **같은 셋업 행**을 낸다(WAN-297) — 캐시 경로와 재계산 경로가
        # 다른 모양을 내면 체크박스 하나로 3열 대조의 행 수가 달라진다.
        st.caption(f"백테 대조 엔진: **{current_engine_label()}** · 즉시 재계산(캐시 무시)")
        with st.spinner("백테스트 대조 재산출 중… (그날 라이브 셀만)"):
            return backtest_setup_rows(
                day_start_ms=start_ms,
                day_end_ms=end_ms,
                symbols=symbols,
                timeframes=timeframes,
            )

    # 기본: 캐시만 읽는다. 미스는 폴백하지 않고 명시한다(WAN-239 §3).
    cache = TimelineCacheStore(db_path)
    try:
        cached = load_cached_day(cache, day_key=day_key, symbols=symbols, timeframes=timeframes)
    finally:
        cache.close()
    st.caption(f"백테 대조 엔진: **{cached.label}**")
    if cached.misses:
        st.warning(
            f"🚨 백테 대조 **아직 계산 안 됨** — {len(cached.misses)}/"
            f"{len(symbols) * len(timeframes)}칸 캐시 미스입니다. 야간 크론이 적재하거나 "
            "`alphablock trades --day … --persist-cache`로 미리 계산하세요. 위 "
            "체크박스로 즉시 재계산할 수 있습니다(무겁습니다). 조회 시 자동 재계산은 "
            "하지 않습니다."
        )
    return list(cached.rows)


def _timeline_full_universe_backtest(
    db_path: str, day_key: str, start_ms: int, end_ms: int
) -> list[TimelineRow]:
    """「채택 좌표 전부」 대조 — 디스크 캐시를 먼저 읽고, 미스면 버튼으로 계산·적재한다.

    ⚠️ 화면 라벨의 종목 수·TF 수는 **하드코딩하지 않는다** — `full_universe_shape()`가
    `DEFAULT_SYMBOLS`·`DEFAULT_TIMEFRAMES`에서 뽑는다(WAN-318 §6: 좌표가 9→12종목이 된 뒤
    라벨만 안 따라가 「9종목 × 4TF = 48셀」이 떴다).

    📌 **읽는 계층이 세 겹이다(WAN-297 §1)** — (1) 세션 캐시(같은 세션 안 즉시 히트) →
    (2) **디스크 캐시**(`TimelineCacheStore`, 야간 크론이 채운다) → (3) 미스면 버튼 안내.
    옛 판은 (1)뿐이라 야간 크론이 디스크에 48셀을 잘 넣어 두어도 이 모드는 **쳐다보지도
    않았고**, 앱 재시작·새 브라우저 세션이면 무조건 버튼을 다시 눌러야 했다(사용자가
    2026-08-15 날짜에서 겪은 화면). 미스여도 **자동 재계산은 하지 않는다**(WAN-239 §3).

    무거우므로(전 셀 × 워밍업 연속, 12종목 48셀 cold ~55초 실측 — 아래 ⚠️) **버튼을 눌렀을
    때만** 돈다. 버튼 경로는 `compute_and_persist_day`를 타므로 **계산 결과가 곧 디스크에
    담기고**, 화면은 그 담긴 행을 다시 읽어 그린다 — "화면에는 떴는데 캐시에는 없다"가
    구조적으로 불가능하다. 적재는 야간 크론과 **같은 `persist_day`**다(완료 기준 4).

    ⚠️ **직렬(jobs=1)로 돈다** — 셀마다 120일치 1분봉을 로드하므로 프로세스 풀 병렬은
    메모리 압박으로 워커가 죽을 수 있다(M1 실측 `BrokenProcessPool`). 화면 버튼은 크래시
    없이 도는 게 우선이라 직렬을 쓴다. 대량 격자·야간 되채우기는 CLI가 담당한다
    (`alphablock trades --persist-cache --days N`).
    """
    from backtest.harness import DEFAULT_TIMEFRAMES

    n_symbols, n_timeframes, n_cells = full_universe_shape()
    st.caption(
        f"채택 좌표 **{n_symbols}종목 × {n_timeframes}TF({', '.join(DEFAULT_TIMEFRAMES)}) = "
        f"{n_cells}셀** · 인자 없는 "
        "`backtest.run`과 같은 엔진·기본값 · 워밍업 연속(warm)으로 **탭이 그날인 셋업만** "
        "평가합니다."
    )
    st.caption(
        "⚠️ 백테 수익률은 기대수익이 아닙니다 — 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 "
        "이 엔진에 통계적 엣지는 확인되지 않았습니다. 페이퍼와 정확히 안 맞을 수 있습니다"
        "(페이퍼=틱 · 백테=1분봉, 큐 우선순위 미모델 — 둘 다 상한)."
    )

    results: dict[str, _FullRunResult] = st.session_state.setdefault(_TIMELINE_FULL_RESULT_KEY, {})
    if st.button(
        f"▶ {day_key} 백테 실행 ({n_symbols}종목×{n_timeframes}TF · 무겁습니다)",
        key="timeline_full_run",
        help=(
            "누른 날짜만 계산하고 그 결과를 디스크 캐시에 적재합니다(다음 접속엔 버튼 없이 "
            "바로 뜹니다). 날짜를 바꿔도 자동 실행하지 않습니다(WAN-239)."
        ),
    ):
        started = time.time()
        with st.spinner(f"{day_key} 백테스트 실행 중… ({n_cells}셀 · 워밍업 연속)"):
            # WAN-295: 셋업 전부(청산·미진입·미체결·건너뜀)를 낸다 — 라이브와 셋업 단위로
            # 대칭인 대조가 가능하게. 표·요약은 「청산」 행만 추려 WAN-290과 같게 본다.
            # WAN-297: 계산과 적재가 한 함수라 화면이 그리는 행 == 디스크에 담긴 행이다.
            cache = TimelineCacheStore(db_path)
            try:
                _report, computed = compute_and_persist_day(
                    cache,
                    day_start_ms=start_ms,
                    day_end_ms=end_ms,
                    day_key=day_key,
                    jobs=1,
                )
            finally:
                cache.close()
        results[day_key] = _FullRunResult(
            rows=computed.rows,
            elapsed=time.time() - started,
            engine=computed.label,
            from_cache=False,
        )

    result = results.get(day_key)
    if result is None:
        # 세션에 없으면 **디스크 캐시**를 읽는다(야간 크론이 채워 뒀을 수 있다, WAN-297 §1-2).
        cache = TimelineCacheStore(db_path)
        try:
            cached = load_full_universe_day(cache, day_key=day_key)
        finally:
            cache.close()
        if cached.all_hit:
            result = _FullRunResult(
                rows=cached.rows, elapsed=None, engine=cached.label, from_cache=True
            )
            results[day_key] = result
        else:
            st.info(
                f"**아직 계산 안 됨** — {len(cached.misses)}/{n_cells}칸 캐시 미스입니다. 위 "
                "**실행** 버튼을 누르면 계산하고 디스크에 적재합니다(다음 접속엔 버튼 없이 "
                "바로 뜹니다). 야간 크론이 미리 채우기도 합니다"
                "(`alphablock trades --day … --persist-cache`). 조회 시 자동 재계산은 "
                "하지 않습니다(WAN-239). 라이브가 없던 날도 백테만으로 대조할 수 "
                "있습니다(WAN-290)."
            )
            return []

    rows = list(result.rows)
    # 요약은 「청산」 거래만 센다(WAN-290 의미 유지) — 미진입·미체결·건너뜀 셋업은 제외.
    summary = backtest_day_summary([r for r in rows if r.status == STATUS_BACKTEST_CLOSED])
    origin = (
        "디스크 캐시" if result.from_cache else f"이번 실행 {result.elapsed:.1f}초 · 캐시 적재됨"
    )
    st.caption(
        f"백테 대조 엔진: **{result.engine}** · {origin} · "
        f"거래 {summary.trades}건({summary.cells_with_trades}셀 · 승 {summary.wins} · "
        f"패 {summary.losses})"
    )
    return rows


def _render_trade_timeline(settings: Settings) -> None:
    """당일(KST) 거래별 타임라인 — 예약→체결가→청산가→손익, 라이브|백테스트(WAN-234/290).

    라이브(주문 장부 + 페이퍼 라운드트립)를 주인공으로 그리고, 백테스트 대조는 무거우니
    (셀마다 워밍업 연속) **옵트인**이다. 대조 대상을 두 가지로 고른다(WAN-290):
    「라이브 칸만」(그날 라이브가 있던 셀만 — WAN-234 그대로)과 「채택 좌표 전부」
    (라이브 유무와 무관하게 임의 날짜의 하루치 백테를 버튼으로 온디맨드 실행). 행을 누르면
    그 거래 지점으로 차트가 이동한다(저장된 거래 탭 패턴). 뒤쪽 라디오 라벨의 종목 수는
    `full_universe_label()`이 채택 좌표에서 뽑는다(WAN-318 §6 — 하드코딩 금지).
    """
    db_path = settings.db_path
    st.subheader("당일 거래별 타임라인")
    st.caption(
        "들어간 셋업이 **언제 예약 → 얼마에 체결 → 어디서 청산 → 손익 얼마**였는지 거래 한 "
        "줄로 봅니다. 라이브가 주인공, 백테스트는 대조입니다. 시각은 **한국시간(KST)** 입니다."
    )

    default_day = datetime.now(tz=KST).date()
    day = st.date_input("날짜(KST)", value=default_day, key="timeline_day")
    full_universe = full_universe_label()
    target = st.radio(
        "백테스트 대조 대상",
        [_TARGET_LIVE_CELLS, full_universe],
        horizontal=True,
        key="timeline_target",
        help=(
            "라이브 칸만: 그날 라이브 예약이 있던 (심볼, TF)만 대조합니다(WAN-234).  "
            f"{full_universe}: 라이브 유무와 무관하게 임의 날짜의 하루치 백테를 버튼으로 "
            "온디맨드 실행합니다(WAN-290)."
        ),
    )

    start_ms, end_ms, day_key = resolve_day_window(day.isoformat())
    journal = OrderJournal(db_path)
    store = PaperTradeStore(db_path)
    try:
        live_rows = live_timeline_rows(journal, store, start_ms=start_ms, end_ms=end_ms)
    finally:
        store.close()
        journal.close()

    if target == full_universe:
        backtest_rows = _timeline_full_universe_backtest(db_path, day_key, start_ms, end_ms)
    else:
        backtest_rows = _timeline_live_cell_backtest(db_path, day_key, start_ms, end_ms, live_rows)

    # WAN-295: 백테 셋업 행에는 미진입·미체결·건너뜀도 섞여 있다(라이브 대칭). 아래의 시각순
    # 표·「백테만」 경고는 WAN-290 의미 그대로 **청산** 행만 본다(요약이 부풀지 않게).
    closed_backtest = [r for r in backtest_rows if r.status == STATUS_BACKTEST_CLOSED]

    # 셋업 단위 3열 대조(목업 정본) — 라이브·백테를 셋업으로 조인해 페이퍼|차이|백테로 본다.
    _render_setup_compare(live_rows, backtest_rows, day_key)

    timeline = DayTimeline(day_key=day_key, live=tuple(live_rows), backtest=tuple(closed_backtest))
    # 「백테만 있는 줄」 신호는 라이브 진입이 하나라도 있어 대조가 성립할 때만 뜻이 있다.
    # 라이브 러너가 아예 안 돌던 과거 날짜(전부 백테만)는 이 경고가 잡음이라 숨긴다(WAN-290).
    live_entered = any(r.status in ("진입", "청산") for r in timeline.live)
    note = backtest_only_note(timeline) if live_entered else None
    if note is not None:
        st.warning(note)

    frame = timeline_frame(timeline)
    if frame.empty:
        st.info(f"{day_key}: 라이브 예약·백테스트 진입이 모두 없습니다.")
        return

    row = selected_row(timeline, parse_selected_rows(st.session_state.get(_TIMELINE_TABLE_KEY)))
    if row is not None:
        _render_timeline_chart(db_path, row)

    st.markdown("##### 시각순 거래 표 (라이브 | 백테스트 · 청산 거래)")
    st.caption(
        "라이브 칸이 비고 **백테스트 줄만 있는 행**이 핵심 신호입니다 — 백테는 진입했는데 "
        "라이브가 어느 단계에서 끊겼는지(상태 열)로 원인을 가릅니다. 행을 누르면 위 차트가 그 "
        "거래 지점으로 이동합니다."
    )
    st.dataframe(
        frame,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=_TIMELINE_TABLE_KEY,
    )


#: 3열 대조 iframe 높이(px). 헤더(카드·칩·범례) + 행당 높이로 잡고, 넘치면 iframe 안에서
#: 스크롤한다(전 행이 클라이언트 필터로 살아 있다).
_COMPARE_HEADER_PX = 250
_COMPARE_ROW_PX = 66
_COMPARE_MAX_PX = 1600


def _render_setup_compare(
    live_rows: list[TimelineRow], backtest_rows: list[TimelineRow], day_key: str
) -> None:
    """셋업 단위 페이퍼↔백테 3열 대조(목업 정본)를 그린다 (WAN-295).

    백테 대조가 없으면(대상 셀 미계산) 그리지 않는다 — 라이브만으론 대조가 성립하지 않는다.
    조인·집계는 순수 계층(`live.setup_compare`)이 하고, 여기서는 그 결과를 목업 HTML로
    임베드하고 알려진 괴리(틱 vs 1분봉·낙관 렌즈)를 한 줄 경고로 얹는다.
    """
    if not backtest_rows:
        return
    result = build_setup_comparisons(live_rows, backtest_rows)
    if result.summary.total == 0:
        return

    st.markdown("##### 셋업별 대조 (페이퍼 | 차이 | 백테)")
    height = min(_COMPARE_HEADER_PX + _COMPARE_ROW_PX * result.summary.total, _COMPARE_MAX_PX)
    st.iframe(setup_compare_html(result, day_key=day_key), height=height)
    st.caption(
        "🔴 **판정 갈림**(한쪽만 진입)이 핵심 신호입니다. 🟠 **가격 벗어남**은 진입가차가 틱 "
        f"오차(측정 임계 {result.price_delta_threshold_bps:.1f}bp)를 넘은 경우입니다 — 가격이 "
        "몇 bp 다른 건 정상이라 표시하지 않습니다. 페이퍼↔백테 차이는 **설계상 알려진 것**"
        "입니다(틱 vs 1분봉 WAN-256 · 신규 3종목 펀딩 대리 · 큐 우선순위 미모델 WAN-98). 전부 "
        "`baseline`(닿으면 체결) 낙관 렌즈 위 값입니다."
    )


def _render_timeline_chart(db_path: str, row: TimelineRow) -> None:
    """선택한 타임라인 행의 (심볼, TF) 캔들 위에 그 거래 구간을 비춘다(존은 안 그린다)."""
    window = chart_window(row)
    if window is None:
        st.caption("이 행은 기준 시각이 없어 차트를 띄울 수 없습니다(예약·체결 시각 미상).")
        return
    focus_start, focus_end = window
    candles = _cached_ohlcv(
        db_path,
        row.symbol,
        row.timeframe,
        focus_start - _TIMELINE_CHART_PAD_MS,
        focus_end + _TIMELINE_CHART_PAD_MS,
    )
    if candles.empty:
        st.warning(
            "이 구간의 캔들이 DB에 없어 차트를 그릴 수 없습니다(거래 표는 그대로 조회됩니다)."
        )
        return
    st.iframe(
        build_chart_html(
            candles,
            [],
            None,
            theme=_current_chart_theme(),
            height=_TIMELINE_CHART_HEIGHT,
            focus=window,
        ),
        height=_TIMELINE_CHART_HEIGHT,
    )
    st.caption(f"🔎 {row.source} · {row.symbol} · {row.timeframe} — 선택한 거래 구간")


# --- 운영 상태(Health) 탭 ---------------------------------------------------


def _freshness_frame(rows: list[SeriesFreshness]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "심볼": r.symbol,
            "TF": r.timeframe,
            "최신 봉(KST)": _fmt_time(r.last_open_time),
            "지연": _fmt_lag(r.lag_ms),
            # 안 셌으면 "—" (WAN-186) — 0봉으로 보이면 데이터가 없는 것처럼 읽힌다.
            "봉 수": "—" if r.bar_count is None else f"{r.bar_count:,}",
            "상태": _LEVEL_BADGE[r.level],
        }
        for r in rows
    )


def _funding_frame(rows: list[FundingFreshness]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "심볼": r.symbol,
            "펀딩비": "—" if r.rate is None else f"{r.rate * 100:.4f}%",
            "다음 정산(KST)": _fmt_time(r.next_funding_time),
            "구분": "예측" if r.is_predicted else "확정",
            "지연": _fmt_lag(r.lag_ms),
            "상태": _LEVEL_BADGE[r.level],
        }
        for r in rows
    )


def _positions_frame(views: list[OpenPositionView]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "심볼": v.snapshot.symbol,
            "TF": v.snapshot.timeframe,
            "방향": _direction_label(v.snapshot.direction),
            "진입시각(KST)": _fmt_time(v.snapshot.entry_time),
            "진입가": v.snapshot.entry_price,
            "현재가": "—" if v.current_price is None else v.current_price,
            "미실현 손익": "—" if v.unrealized_pct is None else f"{v.unrealized_pct:+.2f}%",
            "익절선": "—" if v.snapshot.take_profit_price is None else v.snapshot.take_profit_price,
            "손절가": "—" if v.snapshot.stop_price is None else v.snapshot.stop_price,
        }
        for v in views
    )


def _events_frame(events: list[EventRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시각(KST)": _fmt_time(e.time),
            "심볼": e.symbol,
            "TF": e.timeframe,
            "종류": _kind_label(e.kind, e.exit_reason),
            "방향": _direction_label(e.direction),
            "가격": e.price,
        }
        for e in events
    )


def _render_overall_badge(view: HealthView) -> None:
    label = f"종합 상태: **{view.overall.label}**  ·  기준시각 {_fmt_time(view.now_ms)}"
    if view.overall.level is HealthLevel.OK:
        st.success(label)
    else:
        st.error(label)


def _render_collector(collector: CollectorStatus) -> None:
    st.subheader("데이터 수집기")
    if not collector.ran:
        st.info("수집기가 실행된 흔적이 없습니다(미실행). `alphablock collect` 로 시작하세요.")
        return
    cols = st.columns(2)
    cols[0].metric("상태", _LEVEL_BADGE[collector.level])
    cols[1].metric("마지막 하트비트", _fmt_lag(collector.lag_ms) + " 전")
    if collector.level is HealthLevel.STALE:
        st.error("수집기 하트비트가 끊겼습니다 — 수집 프로세스가 멈췄을 수 있습니다.")


def _render_runner(runner: RunnerStatus) -> None:
    st.subheader("실시간 러너")
    if not runner.ran:
        st.info("러너가 실행된 흔적이 없습니다(미실행). `python -m live.runner` 로 시작하세요.")
        return
    cols = st.columns(4)
    cols[0].metric("상태", _LEVEL_BADGE[runner.level])
    cols[1].metric("마지막 폴링", _fmt_lag(runner.lag_ms) + " 전")
    cols[2].metric("마지막 알림", _fmt_time(runner.last_notification_ms))
    # 한 바퀴 완주 소요(WAN-313) — 하트비트와 별개의 「따라가고 있는가」 지표.
    cols[3].metric(
        "한 바퀴 소요",
        "-" if runner.cycle_duration_ms is None else _fmt_lag(runner.cycle_duration_ms),
    )
    if runner.level is HealthLevel.STALE:
        if runner.cycle_stale and not runner.heartbeat_stale:
            st.error(
                "러너는 살아 있지만 전체 시리즈 한 바퀴가 최단 TF 주기를 넘고 있습니다"
                " — 시리즈 수·데이터 로드 병목을 확인하세요(WAN-313)."
            )
        else:
            st.error("러너 하트비트가 끊겼습니다 — 프로세스가 멈췄을 수 있습니다.")


#: DB 무결성 점검 캐시 TTL(WAN-289 완료 기준 2). `PRAGMA quick_check`는 3.5GB DB에서
#: 실측 12초라(WAN-194) **화면 로드마다 돌리면 안 된다** — Health 탭은 60초 자동 갱신
#: fragment인데 매 갱신마다 12초씩 멈추면 탭이 못 쓰게 된다. 한 시간에 한 번이면
#: 손상 감지 용도로 충분하다(doctor의 cron 주기 감각).
_DB_CHECK_TTL_SECONDS = 3600


@st.cache_data(ttl=_DB_CHECK_TTL_SECONDS, show_spinner="DB 무결성 점검 중(quick_check)…")
def _cached_db_integrity(db_path: str) -> tuple[IntegrityReport, int]:
    """`alphablock doctor`와 **같은 판정**(`data.integrity.inspect`)을 캐시해 재사용한다.

    화면에서 새로 짜지 않는다(WAN-289 §3) — 판정 로직이 두 벌이 되면 doctor는
    경고하는데 화면은 초록인 어긋남이 생긴다. 반환에 점검 시각(ms)을 실어 카드가
    "언제 본 판정인지"를 밝힌다(캐시된 값이 최신인 척하지 않게).
    """
    return inspect_db_integrity(db_path), int(time.time() * 1000)


def _render_db_integrity(db_path: str) -> None:
    """Health 탭 「DB 무결성」 카드(WAN-289 §3, 목업 `● 정상 / quick_check OK · 3.5GB`)."""
    st.subheader("DB 무결성")
    try:
        report, checked_at_ms = _cached_db_integrity(db_path)
    except FileNotFoundError:
        st.caption("DB 파일이 없습니다 — 수집(WAN-6)이 만들면 여기서 점검합니다.")
        return
    cols = st.columns(3)
    cols[0].metric("상태", "🟢 정상" if report.healthy else "🔴 경고")
    cols[1].metric("quick_check", "OK" if report.quick_check_ok else "실패")
    cols[2].metric("DB 크기", f"{report.space.db_bytes / 1e9:.1f}GB")
    st.caption(
        f"마지막 점검 {format_kst_zoned(checked_at_ms)} · `alphablock doctor`와 같은 판정을 "
        f"{_DB_CHECK_TTL_SECONDS // 60}분 캐시로 재사용합니다(quick_check가 무거워 매 로드마다 "
        "돌리지 않습니다)."
    )
    if not report.healthy:
        issues: list[str] = []
        if not report.quick_check_ok:
            issues.append("quick_check 실패(페이지 손상 의심)")
        if report.recovery_artifacts:
            names = ", ".join(t.name for t in report.recovery_artifacts)
            issues.append(f"복구 산출물 테이블 잔존({names})")
        if report.orphan_fills:
            issues.append(f"처분 미기록 체결 {len(report.orphan_fills)}건")
        if report.empty_cumulative_ledgers:
            names = ", ".join(t.name for t in report.empty_cumulative_ledgers)
            issues.append(f"빈 누적 장부 테이블({names})")
        st.error(
            "doctor 판정 경고 — "
            + " · ".join(issues)
            + ". 상세·처방은 터미널에서 `alphablock doctor`를 실행하세요."
        )


def _render_repair(view: HealthView) -> None:
    st.subheader("데이터 갭 복구 (WAN-35)")
    rep = view.last_repair
    if rep is None:
        st.caption(
            "갭 복구가 실행된 흔적이 없습니다. `alphablock backfill --repair` 로 점검하세요."
        )
        return
    cols = st.columns(3)
    cols[0].metric("마지막 복구", _fmt_time(rep.ran_at_ms))
    cols[1].metric("채운 봉", str(rep.total_filled))
    cols[2].metric("잔여 봉", str(rep.total_remaining))
    if rep.repaired_series:
        frame = pd.DataFrame(
            {
                "심볼": s.symbol,
                "TF": s.timeframe,
                "갭": s.gaps_found,
                "채움": s.bars_filled,
                "잔여": s.bars_remaining,
                "오류": s.error or "",
            }
            for s in rep.repaired_series
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
    else:
        st.caption("마지막 점검에서 갭이 없었습니다.")
    if rep.lookback_ms:
        # 창을 좁힌 실행의 「갭 없음」은 「그 창 안에서」라는 뜻이다 — 전 구간 무결로
        # 읽히면 WAN-156과 같은 종류의 침묵이 된다(WAN-187).
        st.caption(
            f"이 점검은 최근 {rep.lookback_ms // 86_400_000}일 창만 봤습니다 — "
            "전 구간은 `alphablock backfill --repair`로 점검하세요."
        )
    if rep.has_error:
        st.error("일부 시리즈 갭 복구에 실패했습니다 — 로그/텔레그램 경고를 확인하세요.")
    if rep.untracked_series:
        # 판정에서 뺐다고 화면에서까지 지우면 WAN-156과 같은 침묵이 된다(WAN-157).
        names = ", ".join(f"{u.symbol} {u.timeframe}" for u in rep.untracked_series)
        st.warning(
            f"저장돼 있으나 수집 대상이 아님(낡습니다): {names}"
            " — 판정에서 제외했습니다. 계속 쓸 TF면 `ALPHABLOCK_TIMEFRAMES`에 넣고"
            " 수집기를 재시작하세요."
        )


def _render_data_gap_skips(view: HealthView) -> None:
    """러너가 데이터 결측으로 건너뛴 평가 구간(WAN-314).

    「기회가 없었다」와 「기회를 놓쳤다」가 성적표에서 똑같이 빈칸으로 보이는 것을
    막는 카드다 — 결측 구간은 체결률·괴리 실측의 분모에서 빠졌음을 여기서 알 수 있다.
    """
    st.subheader("데이터 결측으로 건너뛴 평가 (WAN-314)")
    if not view.data_gap_skips:
        st.caption("결측으로 건너뛴 평가가 없습니다.")
        return
    frame = pd.DataFrame(
        {
            "심볼": s.symbol,
            "TF": s.timeframe,
            "결측 구간(KST)": f"{_fmt_time(s.gap_start_ms)} ~ {_fmt_time(s.gap_end_ms)}",
            "첫 관측": _fmt_time(s.first_seen_ms),
            "마지막 관측": _fmt_time(s.last_seen_ms),
            "건너뛴 횟수": s.skip_count,
            "상태": "✅ 해소" if s.resolved_ms is not None else "🔴 진행 중",
        }
        for s in view.data_gap_skips
    )
    st.dataframe(frame, use_container_width=True, hide_index=True)
    if any(s.resolved_ms is None for s in view.data_gap_skips):
        st.error(
            "진행 중인 결측이 있습니다 — 그 시리즈의 평가가 멈춰 있습니다. "
            "`uv run alphablock backfill --repair`로 구멍을 메우세요."
        )


def _render_circuit_breaker(settings: Settings) -> None:
    """일일 손실 서킷브레이커 상태(WAN-38): 정상/발동 · 당일 손익 · 한도.

    러너의 진입 게이트와 **같은 판정**(`RiskManager.status`, DB 재계산)을 써서 화면과
    실제 차단이 어긋나지 않게 한다. 시각·경계는 KST(WAN-172).
    """
    from execution.risk import RiskManager

    st.subheader("일일 손실 서킷브레이커")
    now_ms = int(time.time() * 1000)
    with PaperTradeStore(settings.db_path) as store:
        equity = store.latest_equity_after()
        base_equity = equity if equity is not None else settings.paper_equity
        rm = RiskManager(settings.risk_limits, realized_pnl_source=store.realized_pnl_between)
        status = rm.status(now_ms, base_equity)

    if not status.enabled:
        st.caption("서킷브레이커가 비활성입니다(`daily_loss_limit_fraction` 미설정).")
        return

    frac = settings.risk_limits.daily_loss_limit_fraction or 0.0
    state = "🔴 발동 — 신규 진입 차단" if status.tripped else "🟢 정상"
    rows = [
        {"항목": "상태", "값": state},
        {"항목": "당일 손익(KST)", "값": f"{status.daily_realized_pnl:,.2f} USDT"},
        {"항목": f"손실 한도(자본 {frac:.1%})", "값": f"−{status.loss_limit:,.2f} USDT"},
        {"항목": "기준 자본", "값": f"{status.baseline_equity:,.2f} USDT"},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_health_body(settings: Settings) -> None:
    # 마지막 갱신 시각(KST, WAN-172). 자동 새로고침이 켜져 있으면 fragment가 주기적으로
    # 재실행되며 이 값이 갱신돼, 화면이 실제로 최신인지 한눈에 확인할 수 있다.
    st.caption(f"마지막 갱신: {format_kst_zoned(int(time.time() * 1000), seconds=True)}")
    if st.button("🔄 지금 새로고침"):
        # fragment 범위만 다시 그린다(분석·백테스트 등 무거운 탭은 건드리지 않음).
        st.rerun(scope="fragment")

    # 봉 수 계산은 시리즈마다 인덱스 구간 전체를 훑어 6년 DB에서 수십 초다(WAN-186).
    # 신선도 판정에는 안 쓰이므로 기본은 끔 — 필요할 때만 켠다.
    include_bar_count = st.checkbox(
        "봉 수 세기(느림)",
        value=False,
        help="시리즈별 저장 봉 수를 셉니다. 대용량 DB에서는 수십 초 걸릴 수 있습니다.",
    )

    view = build_health_view(
        settings.db_path,
        include_bar_count=include_bar_count,
        runtime_state_path=settings.live_runtime_state_path,
        poll_interval_seconds=settings.live_poll_interval_seconds,
        stale_multiplier=settings.health_stale_multiplier,
        collector_heartbeat_path=settings.collector_heartbeat_path,
        collector_heartbeat_interval_seconds=settings.collector_heartbeat_interval_seconds,
        repair_state_path=settings.repair_state_path,
        cycle_budget_ms=runner_cycle_budget_ms(settings.live_signal_timeframes),
    )

    _render_overall_badge(view)

    st.subheader("데이터 신선도")
    if view.freshness:
        st.dataframe(_freshness_frame(view.freshness), use_container_width=True, hide_index=True)
    else:
        st.warning("저장된 OHLCV 데이터가 없습니다. 먼저 수집(WAN-6)을 실행하세요.")

    st.subheader("펀딩비 상태")
    if view.funding:
        st.dataframe(_funding_frame(view.funding), use_container_width=True, hide_index=True)
    else:
        st.caption("표시할 펀딩비 심볼이 없습니다.")

    _render_collector(view.collector)
    _render_runner(view.runner)
    # 목업 Health 카드 3종의 셋째 — 실시간 러너 · 수집기 · **DB 무결성**(WAN-289 §3).
    _render_db_integrity(settings.db_path)
    _render_repair(view)
    _render_data_gap_skips(view)
    _render_circuit_breaker(settings)

    st.subheader("현재 페이퍼 포지션")
    if view.positions:
        st.dataframe(_positions_frame(view.positions), use_container_width=True, hide_index=True)
    else:
        st.caption("오픈 중인 페이퍼 포지션이 없습니다.")

    st.subheader("최근 신호/알림")
    if view.recent_events:
        st.dataframe(_events_frame(view.recent_events), use_container_width=True, hide_index=True)
    else:
        st.caption("기록된 신호가 없습니다(러너 미실행이거나 신호 미발생).")


def _render_health(settings: Settings, *, run_every: int | None) -> None:
    """운영 상태 탭을 자동 새로고침 fragment로 감싸 렌더한다(WAN-48).

    ``run_every``(초)가 주어지면 Streamlit이 이 fragment만 그 주기로 재실행해,
    분석·백테스트 등 무거운 탭을 다시 계산하지 않고 운영 상태(가벼운 파일·DB
    읽기)만 최신으로 유지한다. ``None``이면 자동 새로고침을 끈다.
    """

    @st.fragment(run_every=run_every)
    def _auto_refresh_fragment() -> None:
        _render_health_body(settings)

    _auto_refresh_fragment()


# --- 잔고·거래내역 탭 (WAN-33 · WAN-245로 개편) ------------------------------
#
# 옛 「페이퍼 성과」 탭이다. WAN-245에서 차트가 메인으로 올라가면서 이 탭은 **지갑**을
# 보는 자리가 됐다 — 잔고 카드 + 에쿼티 곡선(MDD 구간 강조) + 거래 원장.


def _wallet_balance(
    records: Sequence[PaperTradeRecord], *, initial_equity: float | None
) -> float | None:
    """공유 지갑 잔고 = 초기 자본 + 모든 거래의 실현손익 합(WAN-237).

    러너는 칸=(종목,TF)이 **한 지갑을 공유**하는 레버리지 북이다(WAN-213). 따라서 "현재
    잔고"는 마지막 청산 1건의 스냅샷(`equity_after`)이 아니라 **모든 칸의 실현손익 합**이다 —
    여러 칸이 동시에 청산되면 그 스냅샷은 마지막 칸만 반영해 지갑을 합산하지 못한다(WAN-237
    실측: 손절 2건인데 잔고는 한 건만 빠졌다). `초기 자본 + Σrealized_pnl`로 재구성하면
    "현재 잔고 − 초기 자본 == 총 손익"이 부동소수 오차 내에서 항상 성립한다.

    옛 %-only 행(WAN-207 이전)은 달러 실현손익이 없어, 하나라도 섞이면 None(재구성 불가)을
    반환한다 — 억지 %-역산은 실제 잔고와 어긋나므로 하지 않는다(WAN-207/95 교훈).
    """
    if not records or initial_equity is None:
        return None
    pnls = [r.realized_pnl for r in records]
    if any(pnl is None for pnl in pnls):
        return None
    return initial_equity + sum(pnl for pnl in pnls if pnl is not None)


def _render_balance(settings: Settings) -> None:
    db_path = settings.db_path
    with PaperTradeStore(db_path) as store:
        series = store.list_series()
        records = [r for s, tf in series for r in store.list_records(s, tf)]

    if not records:
        st.info(
            "누적된 페이퍼 거래가 없습니다. 러너(`python -m live.runner`)가 청산을 내면 "
            "여기에 성과가 집계됩니다."
        )
        return

    # 전체 성과는 실제 지갑 기준(전액배팅 아님, WAN-207) — 러너 초기 자본·사이징 비율을
    # 주면 총수익률이 실제 잔고 변화와 부호·크기로 정합한다.
    performance = build_performance(
        records,
        risk_per_trade=settings.risk_sizing.risk_per_trade,
        initial_equity=settings.paper_equity,
    )
    overall = performance.overall

    def _usd(value: float | None) -> str:
        return "N/A" if value is None else f"{value:+,.2f}"

    st.subheader("전체 성과")

    # 현재 잔고($) — 공유 지갑(WAN-213)이므로 초기 자본 + 모든 칸의 실현손익 합이다(WAN-237).
    # 마지막 거래의 청산 직후 자본(`equity_after`) 스냅샷을 쓰면 여러 칸 동시거래에서 마지막
    # 칸만 반영돼 지갑을 합산하지 못한다(WAN-237 실측). 옛 %-only 행이 섞이면 달러 손익이 없어
    # 재구성 불가(None)로 두고 억지 역산은 하지 않는다(WAN-207/95 교훈).
    initial_cap = settings.paper_equity
    balance = _wallet_balance(records, initial_equity=initial_cap)

    # 목업의 카드 5개 — 지갑 잔고 · 누적 실현손익 · 미실현손익 · MDD · 승률·거래.
    # ⚠️ 나머지 지표(총 R·손익비·총 투입·총 리스크 등)는 **지운 게 아니라** 아래
    # 「세부 지표」로 내렸다 — 목업이 첫 줄을 다섯 칸으로 못 박았지 정보를 버리라고 한
    # 것은 아니다.
    rows = _cached_open_positions(settings.db_path)
    unrealized = total_unrealized_usd(rows)
    cards = st.columns(5)
    if balance is None:
        cards[0].metric(
            "지갑 잔고",
            "재구성 불가",
            help=(
                "달러 실현손익이 없는 옛 %-only 거래가 섞여 있어 지갑 잔고를 재구성할 수 "
                "없습니다(WAN-207 이전 서버 코드). 억지 %-역산은 실제 잔고와 어긋나므로 "
                "표시하지 않습니다 — 러너 재배포(WAN-185) 후 새 거래부터 채워집니다."
            ),
        )
    else:
        delta = None if initial_cap is None else f"{balance - initial_cap:+,.2f}"
        cards[0].metric("지갑 잔고", f"{balance:,.0f}", delta=delta)
    cards[1].metric("누적 실현손익", f"{overall.total_return_pct:+.2f}%")
    cards[2].metric(
        "미실현손익",
        "—" if unrealized is None else f"{unrealized:+,.1f}",
        help=(
            f"열려 있는 포지션 {len(rows)}건의 미실현 손익 **달러 합**(최신 확정봉 종가 "
            "기준). 같은 지갑의 돈이라 더해도 뜻이 있습니다."
        ),
    )
    cards[3].metric("MDD (최대 낙폭)", f"−{overall.max_drawdown_pct:.2f}%")
    cards[4].metric("승률 · 거래", f"{overall.win_rate * 100:.1f}% · {overall.num_trades}")

    invested = overall.total_notional
    risk_total = overall.total_risk_amount
    payoff = overall.payoff_ratio
    with st.expander("세부 지표"):
        more = st.columns(5)
        more[0].metric("총 손익($)", _usd(overall.total_realized_pnl))
        more[1].metric("총 R", f"{overall.total_r:+.2f}")
        more[2].metric("손익비", f"{payoff:.2f}" if payoff is not None else "N/A")
        more[3].metric("총 투입($)", "N/A" if invested is None else f"{invested:,.2f}")
        more[4].metric("총 리스크($)", "N/A" if risk_total is None else f"{risk_total:,.2f}")

    # 에쿼티 곡선 — "언제 얼마였나"와 "어디서 얼마나 깨졌나"(MDD 구간)를 함께 본다.
    points = wallet_equity_points(records, initial_equity=initial_cap)
    drawdown = max_drawdown_window(points) if points else None
    mdd_note = (
        f" · :red[빨강 = 최대 낙폭 구간(−{drawdown.drawdown_pct:.2f}%)]"
        if drawdown is not None
        else ""
    )
    st.caption(f"에쿼티 곡선{mdd_note}")
    if not points:
        st.caption(
            "달러 실현손익이 없는 옛 %-only 거래(WAN-207 이전)가 섞여 있어 지갑 곡선을 "
            "재구성할 수 없습니다 — 억지 %-역산은 실제 잔고와 어긋나므로 그리지 않습니다."
        )
    else:
        st.plotly_chart(
            build_wallet_equity_chart(points, drawdown, theme=_current_chart_theme()),
            use_container_width=True,
        )
        if drawdown is not None:
            st.caption(
                f"MDD −{drawdown.drawdown_pct:.2f}%: {format_kst_zoned(drawdown.peak_time_ms)} "
                f"고점 ${drawdown.peak_equity:,.2f} → "
                f"{format_kst_zoned(drawdown.trough_time_ms)} 저점 ${drawdown.trough_equity:,.2f}."
            )

    # 청산 거래 리스트 — 목업의 8열 압축 표(읽는 표). 전체 원장(20열)은 아래 확장에
    # 그대로 있고 CSV도 전체다.
    choice = st.radio(
        "청산사유",
        REASON_FILTER_OPTIONS,
        horizontal=True,
        key="paper_exit_reason_filter",
        help="표시할 거래만 좁힙니다. 위 카드·곡선은 **전체 거래** 기준 그대로입니다.",
    )
    shown = filter_records_by_choice(records, choice)
    if choice != REASON_FILTER_ALL:
        st.caption(f"{len(shown):,} / {len(records):,}건 표시")
    st.dataframe(wallet_trade_frame(shown), use_container_width=True, hide_index=True)
    st.caption(
        "MDD = 고점 대비 최대로 깨진 폭. 시각 KST(저장·계산은 UTC 불변). "
        "⚠️ 입금/출금·TWR은 범위 밖입니다(WAN-286) — 여기선 실현손익 기반 잔고만."
    )

    with st.expander("전체 원장 · 시리즈별 성과 (CSV 내보내기)"):
        st.caption("시리즈별 성과")
        st.dataframe(
            performance_to_display_frame(performance), use_container_width=True, hide_index=True
        )
        st.download_button(
            "성과 요약 CSV",
            performance_to_dataframe(performance).to_csv(index=False),
            file_name="paper_performance.csv",
            mime="text/csv",
        )
        st.caption("거래 원장(전체 열)")
        st.dataframe(records_to_display_frame(shown), use_container_width=True, hide_index=True)
        st.download_button(
            "거래 원장 CSV",
            records_to_dataframe(records).to_csv(index=False),
            file_name="paper_trades.csv",
            help="CSV는 필터와 무관하게 **전체 원장**입니다(데이터 축 — WAN-190).",
            mime="text/csv",
        )


# --- 진입/미진입 사유 장부 탭 (WAN-219) --------------------------------------
#
# WAN-217이 페이퍼 러너의 진입 깔때기(체결/미체결/스킵/거부 사유)를 DB에 적재한다. 이 탭은
# 그 장부를 **계산 없이 조회**해 체결률·미진입 사유 분포·진입/미진입 목록으로 보여 준다
# (저장된 거래 탭과 같은 원칙 — 화면에서 재계산 금지). 무거운 부분(DB 조회)은 전체 창을
# 한 번 캐시하고, 기간·칸·사유 필터는 캐시된 목록을 메모리에서 좁힌다(캐시 히트 즉시).

#: 조회 창 상한 — 실제 사건 시각은 항상 이보다 작다. 창을 [0, 이 값)으로 고정해 캐시 키가
#: `now`에 흔들리지 않게 하고(캐시 히트 즉시), 기간 필터는 메모리에서 자른다.
_LEDGER_FAR_FUTURE_MS = 10_000_000_000_000  # ≈ 서기 2286년

_LEDGER_PERIODS: dict[str, int | None] = {
    "전체": None,
    "최근 7일": 7 * 86_400_000,
    "최근 30일": 30 * 86_400_000,
}


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_ledger(db_path: str) -> list[LedgerEntry]:
    """전체 창의 진입 깔때기 행을 한 번 조회해 캐시한다 — **조회일 뿐 재계산이 아니다**.

    기간 필터가 `now`에 따라 창을 좁혀도 캐시 키는 `db_path` 하나라 즉시 히트한다(좁히기는
    호출부가 메모리에서 한다). 빈 DB·미배포 장부여도 `OrderJournal`이 스키마를 만들어 빈
    목록을 돌려주므로 화면이 깨지지 않는다.
    """
    journal = OrderJournal(db_path)
    try:
        return journal.ledger_entries(start_ms=0, end_ms=_LEDGER_FAR_FUTURE_MS)
    finally:
        journal.close()


def _render_funnel_ledger(settings: Settings) -> None:
    db_path = settings.db_path
    st.header("진입/미진입 사유 장부")
    st.caption(
        "WAN-217이 적재한 페이퍼 러너의 **진입 깔때기**를 계산 없이 조회합니다 — 걸어놓은 "
        "지정가 중 몇 %가 실제로 채워졌나(체결률)와 안 들어간 이유의 분포입니다. 시각은 "
        "한국시간(KST)입니다."
    )

    entries = _cached_ledger(db_path)
    if not entries:
        st.info(
            "기록된 진입/미진입 사유가 없습니다. 페이퍼 러너(`python -m live.runner`)가 돌면 "
            "지정가 주문의 진입 깔때기(체결·미체결·스킵·거부)가 여기에 쌓입니다.\n\n"
            f"조회 대상 DB: `{db_path}`"
        )
        return

    period = st.radio("기간", list(_LEDGER_PERIODS), horizontal=True, key="ledger_period")
    span_ms = _LEDGER_PERIODS[period]
    if span_ms is None:
        windowed = entries
    else:
        cutoff = int(time.time() * 1000) - span_ms
        windowed = [e for e in entries if e.event_ms >= cutoff]
    if not windowed:
        st.info("이 기간에는 기록이 없습니다.")
        return

    funnel = to_funnel_counts(windowed)
    entered = sum(1 for e in windowed if e.entered)
    cols = st.columns(4)
    cols[0].metric(
        "체결률",
        "—" if funnel.fill_rate is None else f"{funnel.fill_rate * 100:.1f}%",
        help="체결 ÷ (체결 + 미체결). 스킵·거부는 분모에 넣지 않습니다(주문이 걸린 표본만).",
    )
    cols[1].metric("체결", str(funnel.filled))
    cols[2].metric("미체결(안 닿음)", str(funnel.no_fill))
    cols[3].metric(
        "진입",
        str(entered),
        help=(
            "체결이 페이퍼 포지션으로 실제 열린 수 — 체결됐어도 집행 가드가 거부하면 "
            "진입이 아닙니다(WAN-194)."
        ),
    )

    st.subheader("미진입 사유 분포")
    dist = reason_distribution(windowed)
    if int(dist["건수"].sum()) == 0:
        st.caption("이 기간에는 미진입 사유가 없습니다(모두 진입/체결).")
    else:
        st.dataframe(dist, use_container_width=True, hide_index=True)

    st.subheader("칸별 체결률")
    st.dataframe(fill_rate_by_cell(windowed), use_container_width=True, hide_index=True)

    st.subheader("진입 vs 미진입 목록")
    filter_cols = st.columns(2)
    with filter_cols[0]:
        cell = st.selectbox("칸(심볼·TF)", cell_options(windowed), key="ledger_cell")
    with filter_cols[1]:
        reason = st.selectbox("사유", reason_options(windowed), key="ledger_reason")
    listing = filter_entries(windowed, cell=cell, reason=reason)
    st.caption(
        "**체결**은 지정가에 닿았는지, **사유**는 그 결과입니다 — 닿았는데 거부(체결·미진입)와 "
        "안 닿음(미체결)이 한 표에서 갈립니다."
    )
    st.dataframe(ledger_frame(listing), use_container_width=True, hide_index=True)


#: 백테스트(참고·대조) 탭의 "참고·대조" 성격을 화면이 드러내는 한 줄(WAN-220). 강등의
#: 핵심 이유 — 이 숫자는 라이브 실측과 대조하는 **잣대**이지 약속·기대수익이 아니다.
_BACKTEST_REFERENCE_NOTE = (
    "📊 **참고·대조** — 채택 엔진 백테스트 리플레이입니다. 라이브 페이퍼 실측과 "
    '대조하는 잣대이지 기대수익이 아닙니다("닿으면 체결" 가정 위, 엣지 미확인).'
)


def _render_backtest_tab_lazy(
    *,
    state_key: str,
    button_key: str,
    load_label: str,
    render: Callable[[], None],
) -> None:
    """백테스트 탭을 **지연 로딩**한다(WAN-220 · WAN-202).

    Streamlit `st.tabs`는 활성 탭과 무관하게 매 실행마다 모든 탭 본문을 렌더하므로,
    탭 순서만 바꿔서는 cold start에서 무거운 분석 탭(cold load ~10초)이 여전히 돈다.
    사용자가 "불러오기"를 누른 뒤에만 무거운 조회를 실행하고, 한 번 열면 세션 동안
    유지한다 — 첫 화면에서는 버튼과 안내만 그린다(빠른 cold start).
    """
    st.caption(_BACKTEST_REFERENCE_NOTE)
    if st.session_state.get(state_key) or st.button(
        load_label,
        key=button_key,
        help="cold start를 빠르게 유지하려고 이 대조용 탭은 열 때만 로드합니다(WAN-220).",
    ):
        st.session_state[state_key] = True
        render()
    else:
        st.info(
            "참고·대조용 백테스트 탭입니다. 위 **불러오기** 버튼을 눌러야 로드됩니다 — "
            "첫 화면(라이브·운영)을 빠르게 유지하기 위한 지연 로딩입니다(WAN-220)."
        )


def _render_backtest_reference(settings: Settings) -> None:
    """「분석」 + 「저장된 거래」를 합친 **진짜 한 화면**(WAN-245 → WAN-289).

    WAN-245는 두 옛 탭을 한 탭 안에 **두 섹션으로 쌓아** 차트가 두 번 그려졌다.
    WAN-289가 목업대로 병합했다 — 성과 카드 6종(총수익·MDD·승률·거래수·체결률·최종
    시드) + 존 차트 하나(진입·청산 마커) + 청산사유 칩(잔고 탭과 같은 어휘) + 거래
    리스트 하나(행 클릭 → 차트 점프) + 미체결 셋업. 강등 원칙(WAN-220)은 그대로다.
    """
    _render_analysis(settings)


def main() -> None:
    st.set_page_config(page_title="AlphaBlock Dashboard", layout="wide")
    st.title("AlphaBlock — 통합 트레이딩 대시보드")

    settings = get_settings()
    _render_status_pill(settings)

    # 자동 새로고침 컨트롤(WAN-48). 운영 상태 탭만 주기적으로 스스로 갱신되게 한다.
    # 기본 주기는 ALPHABLOCK_DASHBOARD_REFRESH_SECONDS(0이면 기본 꺼짐). 토글로 끌 수 있다.
    refresh_seconds = settings.dashboard_refresh_seconds
    with st.sidebar:
        st.header("자동 새로고침")
        auto_refresh = st.toggle(
            "운영 상태 자동 갱신",
            value=refresh_seconds > 0,
            help=(
                f"켜면 운영 상태(Health) 탭이 {refresh_seconds or 60}초마다 스스로 갱신됩니다. "
                "주기는 ALPHABLOCK_DASHBOARD_REFRESH_SECONDS로 설정합니다(0이면 기본 꺼짐)."
            ),
        )
    run_every = refresh_seconds if (auto_refresh and refresh_seconds > 0) else None

    # 차트 테마 위젯은 여기서 **한 번만** 만든다(WAN-245) — 예전에는 분석 탭이 만들었는데
    # 그 탭이 지연 로딩이라 메인 차트만 보는 동안 테마 선택이 화면에서 사라졌다.
    _resolve_chart_theme()

    # 차트-우선 배치(WAN-245): 첫 화면이 라이브 차트고, 그 뒤로 지갑 → 운영 → 대조용
    # 백테스트 순이다. 백테스트(분석·저장된 거래)는 **한 탭으로 합쳐** 맨 뒤에 강등되고
    # 지연 로딩된다(WAN-220 원칙 유지 — 제거가 아니라 강등).
    (
        chart_tab,
        balance_tab,
        ledger_tab,
        timeline_tab,
        health_tab,
        reference_tab,
    ) = st.tabs(
        [
            "차트",
            "잔고 · 거래내역",
            "진입/미진입 장부",
            "거래 타임라인",
            "Health",
            "분석 · 거래 (참고·대조)",
        ]
    )
    with chart_tab:
        _render_live_chart(settings)
    with balance_tab:
        _render_balance(settings)
    with ledger_tab:
        _render_funnel_ledger(settings)
    with timeline_tab:
        _render_trade_timeline(settings)
    with health_tab:
        _render_health(settings, run_every=run_every)
    # 백테스트 탭은 지연 로딩한다 — cold start에서 무거운 분석(~10초)을 자동 로드하지
    # 않아 첫 화면(차트)이 빠르다(WAN-220 · WAN-202).
    with reference_tab:
        _render_backtest_tab_lazy(
            state_key="_backtest_reference_loaded",
            button_key="load_reference_tab",
            load_label="분석·거래 탭 불러오기",
            render=lambda: _render_backtest_reference(settings),
        )


main()
