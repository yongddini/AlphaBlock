"""통합 트레이딩 웹 대시보드 (WAN-15).

캔들+오더블록+시그널 차트와 백테스트 성과를 한 화면에서 확인한다.
로컬 실행형이며 외부 노출/인증은 범위 밖이다.

실행::

    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from backtest.models import BacktestConfig
from backtest.report import trades_to_dataframe
from config import get_settings
from dashboard.charts import build_equity_chart, build_price_chart
from dashboard.data_access import list_series, load_ohlcv
from dashboard.pipeline import run_pipeline
from strategy.models import OrderBlockParams


def _ms_to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _datetime_to_ms(value: datetime) -> int:
    return int(value.replace(tzinfo=UTC).timestamp() * 1000)


def main() -> None:
    st.set_page_config(page_title="AlphaBlock Dashboard", layout="wide")
    st.title("AlphaBlock — 통합 트레이딩 대시보드")

    settings = get_settings()
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


main()
