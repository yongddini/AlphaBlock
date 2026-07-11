"""통합 트레이딩 웹 대시보드 (WAN-15 · WAN-30).

**분석 탭**: 캔들+오더블록+시그널 차트와 백테스트 성과.
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

from backtest.models import BacktestConfig
from backtest.report import trades_to_dataframe
from config import get_settings
from config.settings import Settings
from dashboard.charts import build_equity_chart, build_price_chart
from dashboard.data_access import list_series, load_ohlcv
from dashboard.health import FundingFreshness, HealthLevel, RunnerStatus, SeriesFreshness
from dashboard.health_data import HealthView, OpenPositionView, build_health_view
from dashboard.pipeline import run_pipeline
from live.runtime_state import EventRecord
from strategy.confluence import SignalKind
from strategy.models import OrderBlockDirection, OrderBlockParams, SignalExitReason


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


# --- 분석 탭 ----------------------------------------------------------------


def _render_analysis(settings: Settings) -> None:
    db_path = settings.db_path

    series = list_series(db_path)
    if not series:
        st.warning(
            f"저장된 OHLCV 데이터가 없습니다 ({db_path}). 먼저 데이터 수집(WAN-6)을 실행하세요."
        )
        return

    symbols = sorted({symbol for symbol, _ in series})
    with st.sidebar:
        st.header("선택")
        symbol = st.selectbox("심볼", symbols)
        timeframes = sorted({tf for s, tf in series if s == symbol})
        timeframe = st.selectbox("타임프레임", timeframes)

    full_df = load_ohlcv(db_path, symbol, timeframe)
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

    result = run_pipeline(df, OrderBlockParams(), BacktestConfig())
    backtest = result.backtest

    st.subheader(f"{symbol} · {timeframe}")
    st.plotly_chart(
        build_price_chart(df, result.order_blocks, backtest, title=f"{symbol} {timeframe}"),
        use_container_width=True,
    )

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

    st.plotly_chart(build_equity_chart(backtest), use_container_width=True)

    st.subheader("거래 목록")
    st.dataframe(trades_to_dataframe(backtest), use_container_width=True)


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


def _render_health(settings: Settings) -> None:
    if st.button("🔄 새로고침"):
        st.rerun()

    view = build_health_view(
        settings.db_path,
        runtime_state_path=settings.live_runtime_state_path,
        poll_interval_seconds=settings.live_poll_interval_seconds,
        stale_multiplier=settings.health_stale_multiplier,
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

    _render_runner(view.runner)

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


def main() -> None:
    st.set_page_config(page_title="AlphaBlock Dashboard", layout="wide")
    st.title("AlphaBlock — 통합 트레이딩 대시보드")

    settings = get_settings()
    analysis_tab, health_tab = st.tabs(["분석", "운영 상태(Health)"])
    with analysis_tab:
        _render_analysis(settings)
    with health_tab:
        _render_health(settings)


main()
