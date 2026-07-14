"""backtest.wan91_funding_cost_report 단위 테스트 (WAN-91).

3심볼×4TF×3년 실데이터 재산출은 `backtest/reports/wan91_funding_cost*.csv`·
`wan91_funding_cost_summary.md`(재현: `python -m backtest.wan91_funding_cost_report`)로
별도 확인한다. 여기서는 결정적 합성 데이터로 엔진×비용×펀딩×IS/OOS 배선과 리포트
테이블 생성만 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan91_funding_cost_report import (
    COST_MODES,
    ENGINE_PRESETS,
    FUNDING_MODES,
    _funding_delta_table,
    _gross_net_table,
    _rows_to_frame,
    _short_flip_summary,
    _tf_verdict_table,
    build_summary_markdown,
    run_symbol_timeframe,
)
from data.models import FundingRate
from strategy.order_blocks import OrderBlockDetector

_SYMBOL = "TEST/USDT:USDT"
_TIMEFRAME = "1h"
_HOUR_MS = 3_600_000
_FUNDING_INTERVAL_MS = 8 * _HOUR_MS


def _synthetic_funding_rates(df: pd.DataFrame) -> list[FundingRate]:
    start = int(df["open_time"].iloc[0])
    end = int(df["open_time"].iloc[-1]) + _HOUR_MS
    first_settlement = (start // _FUNDING_INTERVAL_MS + 1) * _FUNDING_INTERVAL_MS
    rates = []
    t = first_settlement
    while t < end:
        rates.append(FundingRate(symbol=_SYMBOL, funding_time=t, rate=0.0001))
        t += _FUNDING_INTERVAL_MS
    return rates


def test_engine_presets_match_current_and_prior_defaults() -> None:
    assert set(ENGINE_PRESETS) == {"long_only", "short_enabled"}
    assert ENGINE_PRESETS["long_only"].short_enabled is False
    assert ENGINE_PRESETS["short_enabled"].short_enabled is True


def test_run_symbol_timeframe_produces_full_matrix_with_funding_coverage() -> None:
    """엔진×비용×펀딩×IS/OOS 2x2x2x2=16행이 나오고, 펀딩 on 행만 커버리지를 보고한다."""
    df = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=2200, seed=11)
    ob_result = OrderBlockDetector().run(df)
    funding_rates = _synthetic_funding_rates(df)

    rows = run_symbol_timeframe(
        df,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        order_block_result=ob_result,
        funding_rates=funding_rates,
    )
    assert len(rows) == len(ENGINE_PRESETS) * len(COST_MODES) * len(FUNDING_MODES) * 2

    off_rows = [r for r in rows if r.funding_mode == "off"]
    on_rows = [r for r in rows if r.funding_mode == "on"]
    assert all(r.funding_coverage is None for r in off_rows)
    # 거래가 있는 구간은 실제 커버리지(1.0에 가까움)를, 거래가 없는 구간은 None을 보고한다.
    for r in on_rows:
        if r.num_trades > 0:
            assert r.funding_coverage is not None
            assert r.funding_coverage == pytest.approx(1.0, abs=0.01)

    long_only_rows = [r for r in rows if r.engine == "long_only"]
    assert all(r.num_trades >= 0 for r in long_only_rows)


def test_gross_cost_never_worse_than_net_for_same_trades() -> None:
    """같은 거래 집합이면 비용 0(그로스)의 total_return이 비용 포함(넷)보다 항상 크거나 같다."""
    df = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=2200, seed=11)
    ob_result = OrderBlockDetector().run(df)
    rows = run_symbol_timeframe(
        df,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        order_block_result=ob_result,
        funding_rates=[],
    )
    frame = _rows_to_frame(rows)
    pivot = frame[frame["funding_mode"] == "off"].pivot_table(
        index=["segment", "engine"], columns="cost_mode", values="total_return"
    )
    assert (pivot["gross"] >= pivot["net"] - 1e-9).all()


def test_report_tables_and_summary_render_without_error() -> None:
    df = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=2200, seed=11)
    ob_result = OrderBlockDetector().run(df)
    funding_rates = _synthetic_funding_rates(df)
    rows = run_symbol_timeframe(
        df,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        order_block_result=ob_result,
        funding_rates=funding_rates,
    )
    frame = _rows_to_frame(rows)

    assert "delta" in _funding_delta_table(frame) or "심볼" in _funding_delta_table(frame)
    assert "판정" in _gross_net_table(frame)
    assert "권고" in _tf_verdict_table(frame)
    assert "OOS 숏" in _short_flip_summary(frame)

    summary = build_summary_markdown(frame, csv_path=Path("dummy.csv"))
    assert "WAN-91" in summary
    assert "비용 가정 현실성" in summary
