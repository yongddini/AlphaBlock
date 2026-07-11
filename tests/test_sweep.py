"""컨플루언스 성과 평가 & 파라미터 스윕 스모크·단위 테스트 (WAN-19).

`backtest.sweep`(엔드투엔드 평가·그리드 스윕·리포트)과 `backtest.synthetic`
(재현 가능한 합성 OHLCV)을 검증한다. 컨플루언스 전략은 선별적이라 임의 데이터에서
확정 진입이 드물 수 있으므로, 거래 수 자체보다 **파이프라인 실행·재현성·리포트
구조**를 주로 검증하고, 거래가 실제로 체결되는 경로는 오더블록 결과를 주입해 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.sweep import (
    ParamGrid,
    apply_sweep_point,
    bars_per_year,
    evaluate,
    run_sweep,
    timeframe_to_ms,
    write_sweep_csv,
)
from backtest.synthetic import make_synthetic_ohlcv
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
)

# --------------------------------------------------------------------------- 타임프레임 헬퍼


def test_timeframe_to_ms_known_values() -> None:
    assert timeframe_to_ms("1m") == 60_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("4h") == 4 * 3_600_000
    assert timeframe_to_ms("1d") == 86_400_000


def test_timeframe_to_ms_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="지원하지 않는 타임프레임"):
        timeframe_to_ms("2w")


def test_bars_per_year_scales_with_timeframe() -> None:
    year_ms = 365 * 24 * 60 * 60 * 1000
    assert bars_per_year("1h") == pytest.approx(year_ms / 3_600_000)
    assert bars_per_year("1d") == pytest.approx(365.0)
    # 더 짧은 타임프레임 → 연간 봉 수 증가
    assert bars_per_year("1m") > bars_per_year("1h") > bars_per_year("1d")


# --------------------------------------------------------------------------- 합성 데이터


def test_synthetic_is_deterministic() -> None:
    a = make_synthetic_ohlcv(bars=300, seed=3)
    b = make_synthetic_ohlcv(bars=300, seed=3)
    pd.testing.assert_frame_equal(a, b)


def test_synthetic_different_seed_differs() -> None:
    a = make_synthetic_ohlcv(bars=300, seed=1)
    b = make_synthetic_ohlcv(bars=300, seed=2)
    assert not a["close"].equals(b["close"])


def test_synthetic_schema_and_ohlc_invariants() -> None:
    df = make_synthetic_ohlcv(bars=200, timeframe="15m")
    for col in ("open_time", "open", "high", "low", "close", "volume", "closed"):
        assert col in df.columns
    assert len(df) == 200
    # high는 open/close 이상, low는 이하
    assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all()
    # open_time 간격이 타임프레임과 일치
    assert int(df["open_time"].iloc[1] - df["open_time"].iloc[0]) == timeframe_to_ms("15m")


def test_synthetic_rejects_nonpositive_bars() -> None:
    with pytest.raises(ValueError, match="bars"):
        make_synthetic_ohlcv(bars=0)


# --------------------------------------------------------------------------- 그리드


def test_param_grid_size_and_points_distinct() -> None:
    grid = ParamGrid()
    points = list(grid.points())
    assert grid.size == 3
    assert len(points) == 3
    # 각 조합(RSI 임계값)은 유일
    keys = {p.rsi_overbought for p in points}
    assert len(keys) == 3
    # 과매도는 과매수에 대칭
    for p in points:
        assert p.rsi_oversold == pytest.approx(100.0 - p.rsi_overbought)


def test_apply_sweep_point_overrides_only_rsi_threshold() -> None:
    base_conf = ConfluenceParams()
    point = next(ParamGrid(rsi_overbought=(80.0,)).points())
    conf = apply_sweep_point(base_conf, point)

    assert conf.rsi_overbought == pytest.approx(80.0)
    assert conf.rsi_oversold == pytest.approx(20.0)
    # 지표·청산 규칙은 고정(손대지 않음)
    assert conf.tp_ema_lengths == base_conf.tp_ema_lengths
    assert conf.tp_vwma_length == base_conf.tp_vwma_length
    assert conf.rsi_length == base_conf.rsi_length


# --------------------------------------------------------------------------- 스윕 (엔드투엔드)


def test_run_sweep_produces_full_grid_and_records_identity() -> None:
    df = make_synthetic_ohlcv(bars=800, timeframe="1h", seed=5)
    report = run_sweep(df, symbol="BTC/USDT:USDT", timeframe="1h")

    assert len(report.rows) == ParamGrid().size
    for row in report.rows:
        assert row.symbol == "BTC/USDT:USDT"
        assert row.timeframe == "1h"
        assert row.num_bars == 800
        assert row.start_time == int(df["open_time"].min())
        assert row.end_time == int(df["open_time"].max())


def test_run_sweep_is_deterministic() -> None:
    df = make_synthetic_ohlcv(bars=600, seed=8)
    a = run_sweep(df, symbol="X", timeframe="1h")
    b = run_sweep(df, symbol="X", timeframe="1h")
    assert [r.model_dump() for r in a.rows] == [r.model_dump() for r in b.rows]


def test_run_sweep_sorted_descending_by_metric() -> None:
    df = make_synthetic_ohlcv(bars=600, seed=4)
    report = run_sweep(df, symbol="X", timeframe="1h", sort_by="total_return")
    returns = [r.total_return for r in report.rows]
    assert returns == sorted(returns, reverse=True)
    assert report.best() is report.rows[0]


def test_run_sweep_rejects_bad_sort_by() -> None:
    df = make_synthetic_ohlcv(bars=200, seed=1)
    with pytest.raises(ValueError, match="sort_by"):
        run_sweep(df, symbol="X", timeframe="1h", sort_by="max_drawdown")


def test_sweep_report_dataframe_and_table() -> None:
    df = make_synthetic_ohlcv(bars=400, seed=2)
    report = run_sweep(df, symbol="ETH/USDT:USDT", timeframe="1h")
    frame = report.to_dataframe()
    assert len(frame) == ParamGrid().size
    for col in ("symbol", "timeframe", "total_return", "sharpe", "num_trades", "seed"):
        assert col in frame.columns
    table = report.to_table()
    assert "Parameter Sweep" in table
    assert "sharpe" in table


def test_write_sweep_csv_roundtrip(tmp_path: Path) -> None:
    df = make_synthetic_ohlcv(bars=400, seed=6)
    report = run_sweep(df, symbol="X", timeframe="1h")
    path = write_sweep_csv(report, tmp_path / "sweep.csv")
    assert path.exists()
    loaded = pd.read_csv(path)
    assert len(loaded) == ParamGrid().size
    assert "total_return" in loaded.columns


def test_empty_dataframe_yields_zero_trade_rows() -> None:
    empty = pd.DataFrame({c: [] for c in ("open_time", "open", "high", "low", "close", "volume")})
    report = run_sweep(empty, symbol="X", timeframe="1h")
    assert len(report.rows) == ParamGrid().size
    for row in report.rows:
        assert row.num_trades == 0
        assert row.num_bars == 0
        assert row.start_time is None


# --------------------------------------------------------------------------- 거래 체결 경로


def _arch_df(volume: float = 10.0) -> pd.DataFrame:
    """상승 후 하락하는 아치형 종가(test_confluence와 동일한 형태)."""
    rising = [100.0 + i * 0.5 for i in range(120)]
    falling = [rising[-1] - (i + 1) * 0.5 for i in range(80)]
    closes = rising + falling
    n = len(closes)
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(n)],
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [volume] * n,
        }
    )


def test_evaluate_executes_trade_with_injected_order_block() -> None:
    """오더블록 결과를 주입해 컨플루언스 → 백테스트 엔드투엔드로 거래가 체결됨을 확인.

    WAN-23 규칙에서 진입은 오더블록 탭 + RSI 게이트가 필수다. 상승 구간(pos=50)의
    약세 오더블록 탭은 RSI가 과매수 상태라 **숏 진입이 확정**된다. 근거 오더블록에
    무효화(break_time)가 없고 진입가 아래 선 도달 익절도 발생하지 않으므로, 포지션은
    데이터 끝에서 강제 청산되어 최소 1건의 거래로 기록된다.
    """
    df = _arch_df()
    ob = OrderBlock(
        direction=OrderBlockDirection.BEARISH,
        top=127.0,
        bottom=124.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    # 상승 구간(pos=50) 약세 오더블록 탭 → RSI 과매수 → 숏 확정.
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BEARISH,
        trigger_time=50 * 60_000,
        price=float(df["close"][50]),
        order_block=ob,
        status="active",
    )
    ob_result = OrderBlockResult(order_blocks=[ob], signals=[signal])
    params = ConfluenceParams(rsi_overbought=50.0, rsi_oversold=30.0)

    result = evaluate(
        df,
        confluence_params=params,
        order_block_result=ob_result,
    )
    assert result.metrics.num_trades >= 1
