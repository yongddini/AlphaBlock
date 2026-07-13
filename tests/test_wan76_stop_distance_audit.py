"""backtest.wan76_stop_distance_audit 단위/스모크 테스트 (WAN-76).

3심볼×4TF 전체 실데이터 산출(손절 거리 분포·clamp·민감도·과거 판정 재검증)은
`backtest/reports/wan76_*.csv`·`wan76_summary.md`(재현: `python -m
backtest.wan76_stop_distance_audit`)로 별도 확인한다. 여기서는 결정적 합성
데이터·수기 구성 진단으로 핵심 로직(거리 계산·clamp 판정·구간 집계·민감도
단조성·하한 패치)만 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan76_stop_distance_audit import (
    CURRENT_DEFAULT_PARAMS,
    TradeDiagnostic,
    _diagnose_trades,
    _patched_default_backtest_config,
    clamp_diagnostic_table,
    distance_distribution_table,
    run_all,
    sensitivity_sweep,
)
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from execution.sizing import PositionSizingParams

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


# ------------------------------------------------------------------------ 거리·clamp 진단


def test_diagnose_trades_flags_clamp_when_stop_distance_tiny() -> None:
    """손절 거리가 극단적으로 좁으면 명목 상한(leverage)에 clamp되고, 그 결과 실효
    리스크가 의도한 `risk_per_trade`보다 훨씬 작아진다(이슈 본문의 핵심 주장)."""
    cfg = BacktestConfig(
        risk_sizing=PositionSizingParams(
            risk_per_trade=0.5, leverage=1.0, min_stop_distance_fraction=0.0
        )
    )
    cand = _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=1,
        exit_price=101.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.99,  # 진입가 대비 0.01% 손절 거리.
    )
    diagnostics = _diagnose_trades([cand], cfg, symbol="TEST/USDT:USDT", timeframe="1h")

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.stop_distance_fraction == pytest.approx(0.0001, rel=1e-2)
    assert d.is_clamped is True
    assert d.intended_risk_fraction == pytest.approx(0.5)
    assert d.effective_risk_fraction < d.intended_risk_fraction


def test_diagnose_trades_not_clamped_for_ordinary_stop_distance() -> None:
    """평범한 손절 거리(수 %)면 리스크 기반 수량이 명목 상한에 걸리지 않는다."""
    cfg = BacktestConfig(
        risk_sizing=PositionSizingParams(
            risk_per_trade=0.01, leverage=1.0, min_stop_distance_fraction=0.0
        )
    )
    cand = _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=1,
        exit_price=105.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=98.0,  # 진입가 대비 2% 손절 거리.
    )
    diagnostics = _diagnose_trades([cand], cfg, symbol="TEST/USDT:USDT", timeframe="1h")

    assert len(diagnostics) == 1
    d = diagnostics[0]
    assert d.is_clamped is False
    assert d.effective_risk_fraction == pytest.approx(d.intended_risk_fraction, rel=1e-6)


def test_diagnose_trades_smoke_on_synthetic_zone_limit_candidates() -> None:
    """실제 B안 파이프라인(1분봉 서브스텝)이 낸 후보에도 진단이 그대로 배선되는지."""
    htf, one_min = _synthetic_pair()
    cfg = default_backtest_config("1h")
    candidates, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=CURRENT_DEFAULT_PARAMS, cfg=cfg
    )
    diagnostics = _diagnose_trades(candidates, cfg, symbol="BTC/USDT:USDT", timeframe="1h")
    assert all(d.stop_distance_fraction > 0 for d in diagnostics)
    assert all(d.effective_risk_fraction >= 0 for d in diagnostics)


# ------------------------------------------------------------------------ 구간 집계


def _diag(
    stop_distance_fraction: float, realized_pnl: float, *, is_clamped: bool = False
) -> TradeDiagnostic:
    return TradeDiagnostic(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        entry_time=0,
        stop_distance_fraction=stop_distance_fraction,
        is_clamped=is_clamped,
        intended_risk_fraction=0.01,
        effective_risk_fraction=0.01 if not is_clamped else 0.001,
        realized_pnl=realized_pnl,
        return_pct=realized_pnl / 10_000,
    )


def test_distance_distribution_table_bins_and_pnl_share() -> None:
    diagnostics = [
        _diag(0.0005, realized_pnl=100.0),  # < 0.1% ~ < 1% 전 구간 포함
        _diag(0.004, realized_pnl=-50.0),  # 0.3% 이상, 0.5%/1% 미만
        _diag(0.02, realized_pnl=30.0),  # 모든 구간 밖(1% 이상)
    ]
    table = distance_distribution_table(diagnostics)
    by_threshold = {row["threshold_stop_distance_fraction"]: row for _, row in table.iterrows()}

    assert by_threshold[0.001]["num_trades"] == 1
    assert by_threshold[0.001]["sum_pnl"] == pytest.approx(100.0)
    assert by_threshold[0.005]["num_trades"] == 2
    assert by_threshold[0.005]["sum_pnl"] == pytest.approx(50.0)
    assert by_threshold[0.01]["num_trades"] == 2
    total_row = by_threshold[float("inf")]
    assert total_row["num_trades"] == 3
    assert total_row["sum_pnl"] == pytest.approx(80.0)
    assert total_row["pct_of_total_pnl"] == pytest.approx(1.0)


def test_distance_distribution_table_empty_is_safe() -> None:
    table = distance_distribution_table([])
    assert (table["num_trades"] == 0).all()
    assert table["pct_of_all_trades"].isna().all()


def test_clamp_diagnostic_table_aggregates_only_clamped_subset() -> None:
    diagnostics = [
        _diag(0.02, realized_pnl=10.0, is_clamped=False),
        _diag(0.0001, realized_pnl=5.0, is_clamped=True),
        _diag(0.0002, realized_pnl=-2.0, is_clamped=True),
    ]
    table = clamp_diagnostic_table(diagnostics)
    by_group = {row["group"]: row for _, row in table.iterrows()}

    assert by_group["all"]["num_trades"] == 3
    assert by_group["all"]["clamp_rate"] == pytest.approx(2 / 3)
    assert by_group["clamped_only"]["num_trades"] == 2
    assert by_group["clamped_only"]["mean_effective_risk_fraction"] == pytest.approx(0.001)


# ------------------------------------------------------------------------ 민감도 단조성


def test_sensitivity_sweep_trade_count_is_monotonic_non_increasing() -> None:
    """하한을 올릴수록 스킵되는 거래만 늘 뿐, 새로 생기지는 않는다(재시퀀싱 유무와
    무관하게 거래 수는 단조 비증가여야 한다)."""
    htf, one_min = _synthetic_pair()
    cfg = default_backtest_config("1h")
    candidates, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=CURRENT_DEFAULT_PARAMS, cfg=cfg
    )
    rows = sensitivity_sweep({("BTC/USDT:USDT", "1h"): candidates})
    by_floor = {r.min_stop_distance_fraction: r.num_trades for r in rows}
    floors_sorted = sorted(by_floor)
    counts = [by_floor[f] for f in floors_sorted]
    assert counts == sorted(counts, reverse=True)


# ------------------------------------------------------------------------ 하한 패치


def test_patched_default_backtest_config_overrides_only_floor() -> None:
    baseline = default_backtest_config("1h")
    assert baseline.risk_sizing is not None
    wrapped = _patched_default_backtest_config(0.005)
    patched = wrapped("1h")

    assert patched.risk_sizing is not None
    assert patched.risk_sizing.min_stop_distance_fraction == pytest.approx(0.005)
    assert patched.risk_sizing.leverage == pytest.approx(baseline.risk_sizing.leverage)
    assert patched.risk_sizing.risk_per_trade == pytest.approx(baseline.risk_sizing.risk_per_trade)
    assert patched.initial_capital == pytest.approx(baseline.initial_capital)


# ------------------------------------------------------------------------ 데이터 없음 폴백


def test_run_all_returns_none_when_db_missing() -> None:
    result = run_all(db_path=Path("/nonexistent/wan76.db"), run_recheck=False)
    assert result is None
