"""통합 트레이딩 웹 대시보드 (WAN-15 · WAN-30).

**분석 탭**: 캔들+오더블록 차트 위에, 적재된 **채택 엔진(B안 존-지정가)** 실행의 거래
마커·성과를 **조회로** 얹는다(WAN-199). 화면에서 B안 백테스트(1분봉 substep, 단일 조합
~7분)를 다시 돌리지 않는다 — 손익·거래는 `backtest.run --persist`가 넣어 둔 결과를
저장된 거래 탭과 **같은 인프라**(`BacktestRunStore`)로 읽는다. 차트의 존은 컨플루언스
파라미터와 무관한 오더블록 탐지(상위TF에서 수 초)로 그리고, 기간 슬라이더는 그 **차트
뷰**만 좁힌다(성과 지표는 적재된 전체 실행 기준). 적재본이 없으면 재계산 대신 넣는
방법을 안내한다.
**저장된 거래 탭(WAN-106)**: `backtest.run --persist`가 적재해 둔 **채택 엔진(B안
지정가)** 거래를 계산 없이 조회한다(손절/익절 필터 · 미체결 셋업 · 차트 점프). 분석 탭과
같은 조회 인프라를 쓰되 존을 그리지 않는다(거래 감사 전용).
**운영 상태(Health) 탭**: 데이터 신선도·펀딩·러너 생존·페이퍼 포지션·최근 신호를
한눈에 보여, 수집이 멈췄는지/러너가 살아있는지 즉시 식별한다.

로컬 실행형이며 외부 노출/인증은 범위 밖이다.

실행::

    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from backtest.models import BacktestConfig, BacktestMetrics, BacktestResult
from backtest.report import COL_EXIT_REASON, trades_to_dataframe, trades_to_display_frame
from backtest.trade_store import BacktestRunStore, RunFingerprint, RunSummary, engine_revision
from common.timefmt import KST, format_kst_zoned
from config import get_settings
from config.settings import Settings
from dashboard.charts import (
    ZONE_CATEGORY_LABELS,
    ZoneCategory,
    build_equity_chart,
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
)
from dashboard.health_data import HealthView, OpenPositionView, build_health_view
from dashboard.lightweight_chart import BAND_LINE_COLOR, build_chart_html
from dashboard.live_chart import LIVE_INTERVALS, build_live_config
from dashboard.saved_trades import (
    exit_reason_options,
    filter_by_exit_reason,
    run_label,
    selected_trade_no,
    setups_display_frame,
    zone_limit_runs,
)
from dashboard.trade_table import (
    engine_label_caption,
    parse_selected_rows,
    selected_trade_window,
    style_trade_frame,
)
from live.order_journal import LedgerEntry, OrderJournal
from live.runtime_state import EventRecord
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
    갈라지므로, 선택은 분석 탭이 한 번만 만들고 나머지는 그 상태를 읽는다.
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


def _selected_trade_rows() -> list[int]:
    """거래 표에서 선택된 행 위치. 아직 표가 없거나 선택이 없으면 빈 목록."""
    return parse_selected_rows(st.session_state.get(_TRADE_TABLE_KEY))


def _render_trade_table(
    backtest: BacktestResult,
    conf_params: ConfluenceParams,
    ob_params: OrderBlockParams,
    bt_config: BacktestConfig,
) -> None:
    """거래 표 (WAN-146) — KST 시각·진입금액·시드 변화 + 행 선택 → 차트 점프.

    표의 내용은 `trades_to_display_frame`(대시보드와 CSV 내보내기 공용)이 만들고, 여기서는
    Streamlit 위젯으로 그리기만 한다. 매 행에서 값이 같던 엔진 라벨 6개는 표 본문에서
    빼되 아래 expander에 원본 컬럼 전체와 함께 **보존**한다(WAN-65 — 삭제가 아니다).
    """
    st.subheader("거래 목록")
    st.caption(
        "시각은 **한국시간(KST)** 입니다(내부 계산·저장은 UTC 그대로). "
        "행을 누르면 위 차트가 그 거래의 진입~청산 구간으로 이동합니다."
    )
    st.dataframe(
        style_trade_frame(trades_to_display_frame(backtest)),
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

    chart_theme = _resolve_chart_theme()

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

    # 거래 표에서 고른 행 → 차트 이동 구간(WAN-146). 표는 차트보다 아래에 그려지므로
    # 지난 실행의 선택 상태를 읽는다. 시점 재생 중이면(거래 마커 자체를 안 그린다) 이동도
    # 하지 않고, 선택된 거래가 현재 기간 밖이면 빈 화면으로 뛰지 않게 무시한다.
    focus = selected_trade_window(result, _selected_trade_rows()) if replay_ms is None else None
    if focus is not None and not (start_ms <= focus[0] <= end_ms):
        focus = None

    st.subheader(f"{symbol} · {timeframe}")
    # 지금 보고 있는 게 어느 엔진의 거래인지 항상 드러낸다(WAN-65/95) — 저장된 거래 탭과
    # 같은 지문 배지. 그 아래 실행 설정 배지는 지문의 `entry_mode`를 읽어 "B안(존-지정가)".
    st.caption(f"🔒 실행 지문: {fingerprint.label()} · run_id `{summary.run_id}`")
    _render_run_config_badge(conf_params, ob_params, bt_config, result.metrics)
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

    metrics = result.metrics
    cols = st.columns(6)
    cols[0].metric("Total Return", f"{metrics.total_return * 100:.2f}%")
    cols[1].metric("Max Drawdown", f"{metrics.max_drawdown * 100:.2f}%")
    cols[2].metric("Win Rate", f"{metrics.win_rate * 100:.2f}%")
    profit_factor = metrics.profit_factor
    cols[3].metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor is not None else "N/A")
    sharpe = metrics.sharpe
    cols[4].metric("Sharpe", f"{sharpe:.2f}" if sharpe is not None else "N/A")
    cols[5].metric("Trades", str(metrics.num_trades))

    st.plotly_chart(build_equity_chart(result, theme=chart_theme), use_container_width=True)

    _render_trade_table(result, conf_params, ob_params, bt_config)

    # 미체결 셋업 — 저장된 거래 탭과 같은 조회(WAN-106). "살 뻔했는데 못 산 자리"는
    # 규칙 판단에 체결된 거래만큼 중요하다.
    unfilled = setups[~setups["filled"]] if not setups.empty else setups
    with st.expander(f"미체결 셋업 — 살 뻔했는데 못 산 자리 ({len(unfilled)}건)"):
        if setups.empty:
            st.caption(
                "이 실행에는 셋업 진단이 없습니다(종가 진입·다중 포지션 경로는 미체결이라는 "
                "개념이 없거나 진단을 내지 않습니다)."
            )
        else:
            st.dataframe(setups_display_frame(unfilled), use_container_width=True, hide_index=True)


# --- 저장된 거래 탭 (WAN-106) ------------------------------------------------
#
# 분석 탭과 정반대 성격이다: 저기는 화면을 열 때마다 다시 계산하고(그래서 1분봉을 못 읽어
# A안으로 내려가 있다), 여기는 `backtest.run --persist`가 한 번 계산해 DB에 넣어 둔
# **채택 엔진(B안 지정가)** 거래를 **계산 없이 조회**만 한다.

_SAVED_TABLE_KEY = "saved_trade_table_selection"
_SAVED_CHART_HEIGHT = 520


@st.cache_data(ttl=_SERIES_TTL_SECONDS, show_spinner=False)
def _cached_saved_runs(db_path: str) -> list[RunSummary]:
    with BacktestRunStore(db_path) as store:
        return store.list_runs()


@st.cache_data(ttl=_HEAVY_TTL_SECONDS, show_spinner=False)
def _cached_saved_run(db_path: str, run_id: str) -> tuple[BacktestResult, pd.DataFrame]:
    """적재된 실행 하나를 복원한다 — **조회일 뿐 백테스트가 아니다**."""
    with BacktestRunStore(db_path) as store:
        return store.load_result(run_id), store.setups_frame(run_id)


def _saved_run_empty_hint(db_path: str) -> None:
    st.info(
        "적재된 백테스트 거래가 없습니다. 채택 엔진(B안 지정가) 거래를 한 번 계산해 "
        "넣어 두면 여기서 계산 없이 조회할 수 있습니다:\n\n"
        "```bash\n"
        "uv run python -m backtest.run --symbol BTCUSDT --tf 15m --persist\n"
        "```\n\n"
        f"적재 대상 DB: `{db_path}`"
    )


def _render_saved_trades(settings: Settings) -> None:
    db_path = settings.db_path
    summaries = _cached_saved_runs(db_path)
    if not summaries:
        _saved_run_empty_hint(db_path)
        return

    labels = {run_label(s): s for s in summaries}
    with st.sidebar:
        st.header("저장된 거래")
        chosen = st.selectbox("실행(실행 지문)", list(labels), key="saved_run_choice")
    summary = labels[chosen]
    fingerprint = summary.fingerprint

    # 실행 지문 배지 — 지금 보고 있는 게 어느 엔진의 거래인지 항상 드러낸다(WAN-65/95).
    # 분석 탭의 "A안(봉 마감 종가)" 배지가 하던 역할을 이 탭에서는 이 줄이 이어받는다.
    st.subheader(f"{fingerprint.symbol} · {fingerprint.timeframe}")
    st.caption(f"🔒 실행 지문: {fingerprint.label()} · run_id `{summary.run_id}`")

    result, setups = _cached_saved_run(db_path, summary.run_id)
    # 지표는 **적재된 요약**을 쓴다(복원 결과의 지표가 아니다) — 종가(A안) 실행은 원본
    # 자본곡선이 봉 단위라 거래 단위로 다시 만든 곡선과 MDD가 다르다
    # (`BacktestRunStore.load_result` 독스트링). 복원 결과는 표·차트 마커에만 쓴다.
    cols = st.columns(6)
    cols[0].metric("Total Return", f"{summary.total_return * 100:.2f}%")
    cols[1].metric("Max Drawdown", f"{summary.max_drawdown * 100:.2f}%")
    cols[2].metric("Win Rate", f"{summary.win_rate * 100:.2f}%")
    cols[3].metric("Trades", str(summary.num_trades))
    cols[4].metric(
        "체결률", "—" if summary.fill_rate is None else f"{summary.fill_rate * 100:.2f}%"
    )
    cols[5].metric("최종 시드", f"{summary.final_equity:,.0f}")

    reason = st.radio(
        "청산사유",
        options=exit_reason_options(),
        horizontal=True,
        key="saved_exit_reason",
        help="손절만 / 익절만 골라 봅니다. 시드(전)·시드(후)는 전체 실행 기준 그대로입니다.",
    )
    frame = filter_by_exit_reason(trades_to_display_frame(result), reason, column=COL_EXIT_REASON)

    trade_no = selected_trade_no(frame, parse_selected_rows(st.session_state.get(_SAVED_TABLE_KEY)))
    focus = None if trade_no is None else selected_trade_window(result, [trade_no - 1])
    _render_saved_chart(db_path, fingerprint, result, focus)

    st.caption(
        "시각은 **한국시간(KST)** 입니다(저장·계산은 UTC 그대로). "
        "행을 누르면 위 차트가 그 거래의 진입~청산 구간으로 이동합니다."
    )
    st.dataframe(
        style_trade_frame(frame),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=_SAVED_TABLE_KEY,
    )

    unfilled = setups[~setups["filled"]] if not setups.empty else setups
    with st.expander(f"미체결 셋업 — 살 뻔했는데 못 산 자리 ({len(unfilled)}건)"):
        if setups.empty:
            st.caption(
                "이 실행에는 셋업 진단이 없습니다(종가 진입·다중 포지션 경로는 미체결이라는 "
                "개념이 없거나 진단을 내지 않습니다)."
            )
        else:
            st.dataframe(setups_display_frame(unfilled), use_container_width=True, hide_index=True)


def _render_saved_chart(
    db_path: str,
    fingerprint: RunFingerprint,
    result: BacktestResult,
    focus: tuple[int, int] | None,
) -> None:
    """적재된 거래의 진입·청산 마커를 캔들 위에 그린다(존은 그리지 않는다).

    오더블록을 다시 탐지하면 이 탭의 약속("계산이 아니라 조회")이 깨진다 — 존 표시가
    필요하면 분석 탭이 그 일을 한다. 여기서 필요한 건 **거래가 어디서 났는지**다.
    """
    candles = _cached_ohlcv(
        db_path,
        fingerprint.symbol,
        fingerprint.timeframe,
        fingerprint.start_time,
        fingerprint.end_time,
    )
    if candles.empty:
        st.warning(
            "이 실행 창의 캔들이 DB에 없어 차트를 그릴 수 없습니다(거래 표는 그대로 조회됩니다)."
        )
        return
    st.iframe(
        build_chart_html(
            candles,
            [],
            result,
            theme=_current_chart_theme(),
            height=_SAVED_CHART_HEIGHT,
            focus=focus,
        ),
        height=_SAVED_CHART_HEIGHT,
    )
    if focus is not None:
        st.caption(
            "🔎 선택한 거래 구간을 보고 있습니다. 표에서 선택을 해제하면 전체 구간으로 돌아갑니다."
        )


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
    cols = st.columns(3)
    cols[0].metric("상태", _LEVEL_BADGE[runner.level])
    cols[1].metric("마지막 폴링", _fmt_lag(runner.lag_ms) + " 전")
    cols[2].metric("마지막 알림", _fmt_time(runner.last_notification_ms))
    if runner.level is HealthLevel.STALE:
        st.error("러너 하트비트가 끊겼습니다 — 프로세스가 멈췄을 수 있습니다.")


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
    _render_repair(view)
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


# --- 페이퍼 성과 탭 (WAN-33) -------------------------------------------------


def _latest_equity_after(records: Sequence[PaperTradeRecord]) -> float | None:
    """가장 최근(청산시각 최신) 거래의 청산 직후 자본 = 현재 지갑 잔고(WAN-212).

    옛 %-only 행은 `equity_after`가 None이라 건너뛰고, 값이 있는 거래 중 청산이 가장
    늦은 것을 고른다. 하나도 없으면 None(재구성 불가)을 반환한다 — 폴백 역산은 실제와
    어긋나므로 하지 않는다(WAN-207/95 교훈).
    """
    dated = [r for r in records if r.equity_after is not None]
    if not dated:
        return None
    return max(dated, key=lambda r: r.exit_time).equity_after


def _render_paper(settings: Settings) -> None:
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
    cols = st.columns(6)
    cols[0].metric("총수익률(지갑)", f"{overall.total_return_pct:+.2f}%")
    cols[1].metric("총 손익($)", _usd(overall.total_realized_pnl))
    cols[2].metric("승률", f"{overall.win_rate * 100:.1f}%")
    payoff = overall.payoff_ratio
    cols[3].metric("손익비", f"{payoff:.2f}" if payoff is not None else "N/A")
    cols[4].metric("MDD", f"{overall.max_drawdown_pct:.2f}%")
    cols[5].metric("거래 수", str(overall.num_trades))

    invested = overall.total_notional
    risk_total = overall.total_risk_amount
    cols2 = st.columns(6)
    cols2[0].metric("총 투입($)", "N/A" if invested is None else f"{invested:,.2f}")
    cols2[1].metric("총 리스크($)", "N/A" if risk_total is None else f"{risk_total:,.2f}")
    cols2[2].metric("총 R", f"{overall.total_r:+.2f}")

    # 현재 잔고($) — 정본은 마지막(청산시각 최신) 거래의 청산 직후 자본(`equity_after`).
    # 옛 %-only 행은 NULL이라, 폴백 %로 역산하면 실제 잔고와 어긋난다(WAN-207 사례:
    # -2.73%로 역산하면 ~$9,727인데 실제 +$10,179) — 잘못된 잔고를 찍느니 안 찍는다
    # (WAN-95 교훈). NULL이면 러너 재배포(WAN-185) 전까지 재구성 불가로 명시한다(WAN-212).
    balance = _latest_equity_after(records)
    initial_cap = settings.paper_equity
    if balance is None:
        cols2[3].metric(
            "현재 잔고($)",
            "재구성 불가",
            help=(
                "청산 직후 자본(equity_after)이 기록되지 않았습니다(WAN-207 이전 서버 코드). "
                "억지 %-역산은 실제 잔고와 어긋나므로 표시하지 않습니다 — 러너 재배포(WAN-185) "
                "후 새 거래부터 채워집니다."
            ),
        )
    else:
        delta = None if initial_cap is None else f"{balance - initial_cap:+,.2f}"
        cols2[3].metric("현재 잔고($)", f"{balance:,.2f}", delta=delta)

    st.subheader("시리즈별 성과")
    # 화면은 한글 컬럼, CSV 내보내기는 데이터 축이라 영문·UTC 그대로(WAN-190/172).
    st.dataframe(
        performance_to_display_frame(performance), use_container_width=True, hide_index=True
    )
    st.download_button(
        "성과 요약 CSV",
        performance_to_dataframe(performance).to_csv(index=False),
        file_name="paper_performance.csv",
        mime="text/csv",
    )

    st.subheader("거래 원장")
    st.dataframe(records_to_display_frame(records), use_container_width=True, hide_index=True)
    st.download_button(
        "거래 원장 CSV",
        records_to_dataframe(records).to_csv(index=False),
        file_name="paper_trades.csv",
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


def main() -> None:
    st.set_page_config(page_title="AlphaBlock Dashboard", layout="wide")
    st.title("AlphaBlock — 통합 트레이딩 대시보드")

    settings = get_settings()

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

    analysis_tab, saved_tab, paper_tab, ledger_tab, health_tab = st.tabs(
        ["분석", "저장된 거래", "페이퍼 성과", "진입/미진입 장부", "운영 상태(Health)"]
    )
    with analysis_tab:
        _render_analysis(settings)
    with saved_tab:
        _render_saved_trades(settings)
    with paper_tab:
        _render_paper(settings)
    with ledger_tab:
        _render_funnel_ledger(settings)
    with health_tab:
        _render_health(settings, run_every=run_every)


main()
