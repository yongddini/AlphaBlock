"""통합 트레이딩 웹 대시보드 (WAN-15 · WAN-30).

**분석 탭**: 캔들+오더블록+시그널 차트와 백테스트 성과(화면을 열 때마다 재계산 —
1분봉을 안 읽으므로 A안 종가 진입이다).
**저장된 거래 탭(WAN-106)**: `backtest.run --persist`가 적재해 둔 **채택 엔진(B안
지정가)** 거래를 계산 없이 조회한다(손절/익절 필터 · 미체결 셋업 · 차트 점프).
**운영 상태(Health) 탭**: 데이터 신선도·펀딩·러너 생존·페이퍼 포지션·최근 신호를
한눈에 보여, 수집이 멈췄는지/러너가 살아있는지 즉시 식별한다.

로컬 실행형이며 외부 노출/인증은 범위 밖이다.

실행::

    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from backtest.models import BacktestConfig, BacktestMetrics, BacktestResult
from backtest.report import COL_EXIT_REASON, trades_to_dataframe, trades_to_display_frame
from backtest.sweep import default_backtest_config
from backtest.trade_store import BacktestRunStore, RunFingerprint, RunSummary
from config import get_settings
from config.settings import Settings
from dashboard.charts import (
    DEFAULT_ZONE_CATEGORIES,
    ZONE_CATEGORY_LABELS,
    ZoneCategory,
    build_equity_chart,
    entered_zone_keys,
    filter_zones,
)
from dashboard.data_access import list_series, load_ohlcv
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
from dashboard.pipeline import PipelineResult, run_pipeline
from dashboard.saved_trades import (
    exit_reason_options,
    filter_by_exit_reason,
    run_label,
    selected_trade_no,
    setups_display_frame,
)
from dashboard.trade_table import (
    engine_label_caption,
    parse_selected_rows,
    selected_trade_window,
    style_trade_frame,
)
from live.runtime_state import EventRecord
from paper.parity import build_parity_report
from paper.performance import build_performance
from paper.report import performance_to_dataframe, records_to_dataframe
from paper.store import PaperTradeStore
from strategy.confluence import SignalKind
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    SignalExitReason,
    select_active,
)


def _ms_to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _datetime_to_ms(value: datetime) -> int:
    return int(value.replace(tzinfo=UTC).timestamp() * 1000)


# --- 포맷 헬퍼 (Health) ------------------------------------------------------


def _fmt_time(ms: int | None) -> str:
    if ms is None:
        return "—"
    return _ms_to_datetime(ms).strftime("%Y-%m-%d %H:%M UTC")


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


@st.cache_data(ttl=_HEAVY_TTL_SECONDS, show_spinner="오더블록 탐지·백테스트 계산 중…")
def _cached_pipeline(
    db_path: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    params_key: str,
    _ob_params: OrderBlockParams,
    _conf_params: ConfluenceParams,
    _bt_config: BacktestConfig,
) -> PipelineResult:
    df = _cached_ohlcv(db_path, symbol, timeframe, start_ms, end_ms)
    return run_pipeline(df, _ob_params, _conf_params, _bt_config)


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
    result: PipelineResult,
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
    - 그 외: 선택된 범주(진입/활성/지지/깨짐/소멸)로 필터.
    """
    if replay_ms is not None:
        chart_df = df[df["open_time"] <= replay_ms]
        zones = select_active(
            result.order_blocks,
            replay_ms,
            limit=ob_params.zone_limit,
            combine=ob_params.combine_obs,
        )
        return chart_df, zones
    if show_all_archive:
        return df, list(result.order_blocks)
    entered = entered_zone_keys(result.signals)
    return df, filter_zones(result.order_blocks, categories, entered)


def _run_config_badge_text(
    conf_params: ConfluenceParams, ob_params: OrderBlockParams, bt_config: BacktestConfig
) -> str:
    """현재 실행 설정을 한 줄로 요약한다(WAN-65).

    "구현은 됐는데 실제 실행 경로에 안 붙어서 조용히 잘못된 값이 나온다"는 이
    프로젝트의 반복 버그 패턴(WAN-47/56/59/63/65)에 대한 방어책 — 대시보드가 지금
    무슨 설정으로 백테스트를 돌리고 있는지 화면에 항상 드러낸다.
    """
    entry_label = "A안(봉 마감 종가)" if conf_params.entry_mode == "close" else "B안(존-지정가)"
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

    full_df = _cached_ohlcv(db_path, symbol, timeframe)
    if full_df.empty:
        st.warning("선택한 심볼/타임프레임에 데이터가 없습니다.")
        return

    min_dt = _ms_to_datetime(int(full_df["open_time"].min()))
    max_dt = _ms_to_datetime(int(full_df["open_time"].max()))
    with st.sidebar:
        if min_dt < max_dt:
            start_dt, end_dt = st.slider(
                "기간",
                min_value=min_dt,
                max_value=max_dt,
                value=(min_dt, max_dt),
                format="YYYY-MM-DD HH:mm",
            )
        else:
            start_dt, end_dt = min_dt, max_dt

    start_ms = _datetime_to_ms(start_dt)
    end_ms = _datetime_to_ms(end_dt)
    df = full_df[(full_df["open_time"] >= start_ms) & (full_df["open_time"] <= end_ms)]

    if df.empty:
        st.warning("선택한 기간에 데이터가 없습니다.")
        return

    ob_params = OrderBlockParams()
    # 대시보드는 상위TF OHLCV만 로드하므로 지정가 체결(채택 기본값 `zone_limit`)에
    # 필요한 1분봉 서브스텝을 돌릴 수 없다 → **종가 진입(A안)으로 명시적으로 내린다**
    # (WAN-95). 이 차트/성과는 사용자의 실매매(존 지정가)가 아니라 근사치이며, 화면
    # 배지가 "A안(봉 마감 종가)"로 그 사실을 항상 드러낸다. 대시보드를 채택 기본값과
    # 일치시키려면 1분봉 로딩 + `run_zone_limit_backtest` 배선이 필요하다(WAN-45 계열).
    conf_params = ConfluenceParams(
        entry_mode="close",
        rsi_mode="closed_bar",
        zone_limit_offset_bps=0.0,
        # A안은 존폭 필터(B안 전용)를 읽지 않는다 — 채택 기본값 1.28을 들고 있으면
        # `evaluate`가 거부한다(WAN-159, `CLOSE_ENTRY_DEFAULTS`와 같은 처리).
        max_zone_width_atr=None,
    )
    # CLI 리포트와 동일한 설정 소스(`default_backtest_config`)에서 백테스트 설정을
    # 가져온다 — 대시보드와 CLI가 서로 다른 설정을 들고 갈라지지 않게 한다(WAN-59).
    bt_config = default_backtest_config(timeframe)
    # 캐시 키에 심볼·타임프레임·기간·파라미터를 모두 포함시킨다(누락 시 잘못된
    # 결과를 캐시하게 됨). 파라미터는 직렬화해 params_key로 키에 싣는다.
    params_key = (
        f"{ob_params.model_dump_json()}|{conf_params.model_dump_json()}"
        f"|{bt_config.model_dump_json()}"
    )
    result = _cached_pipeline(
        db_path, symbol, timeframe, start_ms, end_ms, params_key, ob_params, conf_params, bt_config
    )
    backtest = result.backtest

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
        categories = DEFAULT_ZONE_CATEGORIES
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
                ZONE_CATEGORY_LABELS[c] for c in ZoneCategory if c in DEFAULT_ZONE_CATEGORIES
            ]
            selected_labels = st.multiselect(
                "표시 필터",
                options=list(ZONE_CATEGORY_LABELS.values()),
                default=default_labels,
                help=(
                    "진입·활성·지지(탭)·깨짐(무효화)·소멸 중 골라 봅니다. "
                    "기본은 진입한 존 + 활성 존(전체 아님)."
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
                "성과 지표는 확정봉 기준 백테스트 결과 그대로이고, 받은 데이터는 "
                "저장하지 않습니다."
            ),
        )
        if not live_supported:
            st.caption(f"{timeframe}은 바이낸스 kline 스트림이 지원하지 않는 인터벌입니다.")

        st.subheader("차트 표시선 (EMA/VWMA)")
        st.caption(
            "차트에 그리는 선입니다. 익절 판정은 이 중 EMA "
            f"{'/'.join(str(n) for n in conf_params.sorted_tp_ema_lengths)}"
            + (f" + VWMA {conf_params.tp_vwma_length}" if conf_params.tp_vwma_length else "")
            + "에서만 일어납니다(WAN-66)."
        )
        line_keys = [f"ema_{length}" for length in conf_params.sorted_display_ema_lengths]
        if conf_params.tp_vwma_length is not None:
            line_keys.append(f"vwma_{conf_params.tp_vwma_length}")
        visible_lines: set[str] = set()
        for key in line_keys:
            kind, _, length = key.partition("_")
            label = f"{kind.upper()} {length}"
            if st.checkbox(label, value=True, key=f"line_toggle_{key}"):
                visible_lines.add(key)

    chart_df, zones = _select_chart_zones(
        result,
        df,
        ob_params,
        replay_ms=replay_ms,
        categories=categories,
        show_all_archive=show_all_archive,
    )
    # 시점 재생은 그 시점 화면 재현이 목적이라 미래 거래 마커를 겹치지 않는다.
    chart_backtest = None if replay_ms is not None else backtest

    # 실시간 차트(WAN-147) — 표시 계층 전용이다. 브라우저가 바이낸스 웹소켓에 직접 붙어
    # 형성 중인 봉과 볼린저 하단선만 갱신하고, 아래 거래 표·성과 지표는 확정봉 기준
    # 백테스트 결과 그대로다(실시간 값이 섞이지 않는다). 시점 재생 중이거나 기간을
    # 과거로 잘라 본 화면에서는 켜지 않는다 — 지나간 구간에 현재 봉을 붙이면 화면이
    # "그때 무엇을 봤나"를 더는 재현하지 못한다.
    showing_tail = end_ms >= int(full_df["open_time"].max())
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
    focus = selected_trade_window(backtest, _selected_trade_rows()) if replay_ms is None else None
    if focus is not None and not (start_ms <= focus[0] <= end_ms):
        focus = None

    st.subheader(f"{symbol} · {timeframe}")
    _render_run_config_badge(conf_params, ob_params, bt_config, backtest.metrics)
    chart_height = 700
    st.iframe(
        build_chart_html(
            chart_df,
            zones,
            chart_backtest,
            result.signals,
            conf_params=conf_params,
            visible_lines=frozenset(visible_lines),
            theme=chart_theme,
            height=chart_height,
            live=live_config,
            focus=focus,
        ),
        height=chart_height,
    )
    if focus is not None:
        st.caption(
            "🔎 선택한 거래 구간을 보고 있습니다. 표에서 선택을 해제하면 전체 구간으로 돌아갑니다."
        )
    if live_config is not None:
        st.caption(
            "🟢 실시간: 형성 중인 봉과 볼린저 하단선만 옅은 색으로 라이브 갱신됩니다"
            "(바이낸스 웹소켓 직접 구독 · 저장하지 않음). **아래 거래 표·성과 지표는 "
            "확정봉 기준 백테스트 결과**라 실시간 값에 영향받지 않습니다."
        )
    elif live_on:
        st.caption("⚪ 실시간 갱신 꺼짐: 시점 재생 중이거나 기간 끝을 과거로 잘라 본 화면입니다.")

    metrics = backtest.metrics
    cols = st.columns(6)
    cols[0].metric("Total Return", f"{metrics.total_return * 100:.2f}%")
    cols[1].metric("Max Drawdown", f"{metrics.max_drawdown * 100:.2f}%")
    cols[2].metric("Win Rate", f"{metrics.win_rate * 100:.2f}%")
    profit_factor = metrics.profit_factor
    cols[3].metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor is not None else "N/A")
    sharpe = metrics.sharpe
    cols[4].metric("Sharpe", f"{sharpe:.2f}" if sharpe is not None else "N/A")
    cols[5].metric("Trades", str(metrics.num_trades))

    st.plotly_chart(build_equity_chart(backtest, theme=chart_theme), use_container_width=True)

    _render_trade_table(backtest, conf_params, ob_params, bt_config)


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
            "최신 봉(UTC)": _fmt_time(r.last_open_time),
            "지연": _fmt_lag(r.lag_ms),
            "봉 수": r.bar_count,
            "상태": _LEVEL_BADGE[r.level],
        }
        for r in rows
    )


def _funding_frame(rows: list[FundingFreshness]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "심볼": r.symbol,
            "펀딩비": "—" if r.rate is None else f"{r.rate * 100:.4f}%",
            "다음 정산(UTC)": _fmt_time(r.next_funding_time),
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
            "진입시각(UTC)": _fmt_time(v.snapshot.entry_time),
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
            "시각(UTC)": _fmt_time(e.time),
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


def _render_health_body(settings: Settings) -> None:
    # 마지막 갱신 시각(UTC). 자동 새로고침이 켜져 있으면 fragment가 주기적으로
    # 재실행되며 이 값이 갱신돼, 화면이 실제로 최신인지 한눈에 확인할 수 있다.
    now_local = datetime.now(tz=UTC)
    st.caption(f"마지막 갱신: {now_local.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if st.button("🔄 지금 새로고침"):
        # fragment 범위만 다시 그린다(분석·백테스트 등 무거운 탭은 건드리지 않음).
        st.rerun(scope="fragment")

    view = build_health_view(
        settings.db_path,
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

    performance = build_performance(records)
    overall = performance.overall

    st.subheader("전체 성과")
    cols = st.columns(6)
    cols[0].metric("총수익률(복리)", f"{overall.total_return_pct:+.2f}%")
    cols[1].metric("총 R", f"{overall.total_r:+.2f}")
    cols[2].metric("승률", f"{overall.win_rate * 100:.1f}%")
    payoff = overall.payoff_ratio
    cols[3].metric("손익비", f"{payoff:.2f}" if payoff is not None else "N/A")
    cols[4].metric("MDD", f"{overall.max_drawdown_pct:.2f}%")
    cols[5].metric("거래 수", str(overall.num_trades))

    st.subheader("시리즈별 성과")
    st.dataframe(performance_to_dataframe(performance), use_container_width=True, hide_index=True)
    st.download_button(
        "성과 요약 CSV",
        performance_to_dataframe(performance).to_csv(index=False),
        file_name="paper_performance.csv",
        mime="text/csv",
    )

    st.subheader("거래 원장")
    trades_df = records_to_dataframe(records)
    st.dataframe(trades_df, use_container_width=True, hide_index=True)
    st.download_button(
        "거래 원장 CSV",
        trades_df.to_csv(index=False),
        file_name="paper_trades.csv",
        mime="text/csv",
    )

    st.subheader("백테스트 대비 패리티")
    st.caption("같은 기간·시리즈를 백테스트로 재실행해 거래 수·승률·평균 R을 비교합니다.")
    try:
        report = build_parity_report(db_path, settings=settings, series=series)
    except Exception as exc:  # noqa: BLE001 — 대시보드는 실패해도 다른 섹션을 살린다.
        st.warning(f"패리티 리포트를 만들지 못했습니다: {exc}")
        return
    parity_df = report.to_dataframe()
    st.dataframe(parity_df, use_container_width=True, hide_index=True)
    st.download_button(
        "패리티 CSV",
        parity_df.to_csv(index=False),
        file_name="paper_parity.csv",
        mime="text/csv",
    )
    if report.flagged_rows:
        flagged = ", ".join(f"{r.symbol} {r.timeframe}" for r in report.flagged_rows)
        st.warning(f"⚠ 페이퍼와 백테스트 차이가 큰 시리즈: {flagged}")
    else:
        st.success("모든 시리즈가 백테스트와 임계값 내로 일치합니다.")


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

    analysis_tab, saved_tab, paper_tab, health_tab = st.tabs(
        ["분석", "저장된 거래", "페이퍼 성과", "운영 상태(Health)"]
    )
    with analysis_tab:
        _render_analysis(settings)
    with saved_tab:
        _render_saved_trades(settings)
    with paper_tab:
        _render_paper(settings)
    with health_tab:
        _render_health(settings, run_every=run_every)


main()
