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

from backtest.models import BacktestConfig
from backtest.sweep import (
    ParamGrid,
    apply_sweep_point,
    bars_per_year,
    default_backtest_config,
    evaluate,
    run_sweep,
    timeframe_to_ms,
    write_sweep_csv,
)
from backtest.synthetic import make_synthetic_ohlcv
from config.settings import Settings
from data.models import FundingRate
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
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


def test_sweep_row_reports_sizing_mode_from_default_config() -> None:
    """WAN-65: 파일만 봐도 어떤 사이징으로 나온 숫자인지 알 수 있어야 한다.

    `base_backtest`를 안 주면 `default_backtest_config`가 기본 켜진
    `settings.effective_risk_sizing`을 실으므로, 스윕 행은 sizing_mode="risk_sizing"과
    그때의 risk_per_trade를 보고한다.
    """
    df = make_synthetic_ohlcv(bars=200, seed=3)
    report = run_sweep(df, symbol="X", timeframe="1h")
    for row in report.rows:
        assert row.sizing_mode == "risk_sizing"
        assert row.risk_per_trade == pytest.approx(0.01)
    frame = report.to_dataframe()
    assert "sizing_mode" in frame.columns
    assert "risk_per_trade" in frame.columns


def test_default_backtest_config_wires_funding_enabled_from_settings() -> None:
    """WAN-91: `default_backtest_config`가 `settings.backtest_funding_enabled`을
    `BacktestConfig.funding_enabled`에 싣는다(`risk_sizing`과 동일 패턴, 드리프트 가드).

    이 배선이 빠지면 funding_rates를 넘겨도 `funding_enabled=False`라 조용히
    무시되므로(펀딩비 0 취급), `funding_missing_policy`가 다시 조용히 꺼진 채로
    되돌아가지 않도록 여기서 고정한다.
    """
    cfg = default_backtest_config("1h")
    assert cfg.funding_enabled is True
    assert cfg.funding_missing_policy == "zero"

    disabled_settings = Settings(backtest_funding_enabled=False)
    cfg_disabled = default_backtest_config("1h", settings=disabled_settings)
    assert cfg_disabled.funding_enabled is False


def test_sweep_row_reports_full_position_when_risk_sizing_disabled() -> None:
    df = make_synthetic_ohlcv(bars=200, seed=3)
    unsized = BacktestConfig(annualization_factor=bars_per_year("1h"), risk_sizing=None)
    report = run_sweep(df, symbol="X", timeframe="1h", base_backtest=unsized)
    for row in report.rows:
        assert row.sizing_mode == "full_position"
        assert row.risk_per_trade is None


def test_sweep_row_reports_entry_mode_rsi_mode_combine_obs() -> None:
    """WAN-65: 스윕 행에 진입 방식·RSI 모드·병합 여부도 함께 기록된다.

    이 필드들은 트레이딩뷰 대비 A안/B안, wick/close 무효화 같은 실행 경로 차이를
    구분하는 핵심 정보라, 파일만 봐서는 A안인지 B안인지조차 알 수 없었던 문제
    (WAN-47/56/59/63과 동일 패턴)를 막는다.
    """
    df = make_synthetic_ohlcv(bars=200, seed=3)
    zone_limit_conf = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    no_merge_ob = OrderBlockParams(combine_obs=False)
    report = run_sweep(
        df,
        symbol="X",
        timeframe="1h",
        base_confluence=zone_limit_conf,
        order_block_params=no_merge_ob,
    )
    frame = report.to_dataframe()
    for col in ("entry_mode", "rsi_mode", "combine_obs", "funding_coverage"):
        assert col in frame.columns
    for row in report.rows:
        assert row.entry_mode == "zone_limit"
        assert row.rsi_mode == "realtime"
        assert row.combine_obs is False
        # WAN-91: default_backtest_config가 funding_enabled=True를 기본으로 실은 뒤로는,
        # funding_rates를 안 넘겨도 "펀딩 미사용"이 아니라 "커버리지 0%"로 명시적으로
        # 드러난다(비용을 조용히 0으로 채우고 반영했다고 하지 않기 위해, WAN-63).
        assert row.funding_coverage == pytest.approx(0.0)


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
    params = ConfluenceParams(
        rsi_overbought=50.0,
        rsi_oversold=30.0,
        short_enabled=True,
        retap_mode="once",
        rsi_gate_mode="extreme",
        deviation_filter=None,
        use_line_take_profit=False,
        take_profit_mode="line",
    )

    result = evaluate(
        df,
        confluence_params=params,
        order_block_result=ob_result,
    )
    assert result.metrics.num_trades >= 1


# --------------------------------------------------------------------------- 펀딩비 반영 (WAN-29)


def _forced_short_setup() -> tuple[pd.DataFrame, OrderBlockResult, ConfluenceParams]:
    """상승 구간 약세 오더블록 탭으로 숏 1건이 확정·보유되는 결정적 설정.

    포지션은 bar 50에서 진입해 데이터 끝(bar 199)에서 강제 청산되므로, 그 사이에
    정산되는 펀딩은 모두 보유 구간에 포함된다.
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
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BEARISH,
        trigger_time=50 * 60_000,
        price=float(df["close"][50]),
        order_block=ob,
        status="active",
    )
    ob_result = OrderBlockResult(order_blocks=[ob], signals=[signal])
    params = ConfluenceParams(
        rsi_overbought=50.0,
        rsi_oversold=30.0,
        short_enabled=True,
        retap_mode="once",
        rsi_gate_mode="extreme",
        deviation_filter=None,
        use_line_take_profit=False,
        take_profit_mode="line",
    )
    return df, ob_result, params


def _funding_rates_over_hold() -> list[FundingRate]:
    """보유 구간 `[bar 50, bar 199)` 안에서 정산되는 확정 펀딩비들."""
    return [
        FundingRate(symbol="X", funding_time=bar * 60_000, rate=0.001) for bar in (60, 120, 180)
    ]


def test_evaluate_reflects_funding_when_enabled() -> None:
    """`funding_enabled=True` + funding_rates면 evaluate 손익에 펀딩비가 실제 반영된다."""
    df, ob_result, params = _forced_short_setup()
    rates = _funding_rates_over_hold()

    off = evaluate(
        df,
        confluence_params=params,
        order_block_result=ob_result,
        backtest_config=BacktestConfig(funding_enabled=False),
        funding_rates=rates,
    )
    on = evaluate(
        df,
        confluence_params=params,
        order_block_result=ob_result,
        backtest_config=BacktestConfig(funding_enabled=True),
        funding_rates=rates,
    )

    assert off.metrics.num_trades >= 1
    assert on.metrics.num_trades == off.metrics.num_trades
    # 펀딩 미반영 시 비용 0, 반영 시 0이 아니어야 한다(요율 3회 × 명목가).
    assert off.metrics.total_funding_cost == 0.0
    assert on.metrics.total_funding_cost != 0.0
    # 손익(총수익률)이 펀딩 반영으로 달라져야 한다.
    assert on.metrics.total_return != off.metrics.total_return


def test_evaluate_ignores_funding_rates_when_disabled() -> None:
    """funding_rates를 넘겨도 `funding_enabled=False`면 손익이 바뀌지 않는다(기존 동작 보존)."""
    df, ob_result, params = _forced_short_setup()

    no_rates = evaluate(
        df,
        confluence_params=params,
        order_block_result=ob_result,
        backtest_config=BacktestConfig(funding_enabled=False),
    )
    with_rates = evaluate(
        df,
        confluence_params=params,
        order_block_result=ob_result,
        backtest_config=BacktestConfig(funding_enabled=False),
        funding_rates=_funding_rates_over_hold(),
    )

    assert with_rates.metrics.total_return == no_rates.metrics.total_return
    assert with_rates.metrics.total_funding_cost == 0.0


def test_run_sweep_forwards_funding_to_every_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_sweep이 funding_rates를 그리드의 모든 조합 평가(evaluate)에 전달한다.

    스윕은 오더블록을 내부에서 재탐지하므로 합성 데이터로 거래를 강제하기 어렵다.
    대신 원래 버그(evaluate 호출에서 funding_rates 인자를 흘려버림)를 직접 막도록,
    각 조합 평가가 동일한 funding_rates를 받는지 스파이로 검증한다. 펀딩비가 실제
    손익에 반영되는 경로는 `test_evaluate_reflects_funding_when_enabled`가 확인한다.
    """
    import backtest.sweep as sweep_mod

    rates = _funding_rates_over_hold()
    seen: list[object] = []
    real_evaluate = sweep_mod.evaluate

    def spy_evaluate(df: pd.DataFrame, **kwargs: object) -> object:
        seen.append(kwargs.get("funding_rates"))
        return real_evaluate(df, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sweep_mod, "evaluate", spy_evaluate)

    grid = ParamGrid(rsi_overbought=(70.0, 75.0, 80.0))
    df = _arch_df()
    run_sweep(
        df,
        symbol="X",
        timeframe="1m",
        grid=grid,
        base_backtest=BacktestConfig(funding_enabled=True),
        funding_rates=rates,
    )

    assert len(seen) == grid.size
    assert all(fr is rates for fr in seen)
