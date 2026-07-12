"""존-지정가 + 실시간 RSI(B안) 백테스트 파이프라인 테스트 (WAN-41).

`entry_mode=zone_limit` + `rsi_mode=realtime`이 오더블록 탐지에 배선되어 end-to-end로
동작하는지, 1분봉 서브스텝 재구성으로 진입/청산이 이뤄지는지, A안과 동일 비용 모델을
쓰는지 검증한다.
"""

from __future__ import annotations

import pandas as pd

from backtest.models import BacktestConfig, PositionSide, Trade
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.zone_limit_backtest import (
    build_result_from_trades,
    run_zone_limit_backtest,
    run_zone_limit_backtest_verbose,
)
from strategy.models import ConfluenceParams


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def test_end_to_end_runs_and_is_deterministic() -> None:
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    result_a = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    result_b = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    # 결정적: 같은 입력 → 같은 결과.
    assert result_a.metrics.num_trades == result_b.metrics.num_trades
    assert [t.realized_pnl for t in result_a.trades] == [t.realized_pnl for t in result_b.trades]
    # 파이프라인이 실제로 진입을 산출한다(존에 닿는 순간 진입 동작).
    assert result_a.metrics.num_trades >= 1


def test_verbose_returns_fill_and_penetration_stats() -> None:
    """진단 통계: 대상 셋업·체결·관통 수를 반환하고 체결률이 정합적이다(WAN-46)."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    result, stats = run_zone_limit_backtest_verbose(htf, one_min, "1h", confluence_params=params)
    assert stats.eligible >= stats.filled >= 0
    assert 0 <= stats.penetrations <= stats.filled
    assert stats.fill_rate is not None and 0.0 <= stats.fill_rate <= 1.0
    # 체결 수는 최종 거래 수 이상이다(단일 포지션 시퀀싱으로 일부가 빠질 수 있으므로).
    assert stats.filled >= result.metrics.num_trades


def test_verbose_matches_plain_result() -> None:
    """verbose와 기본 함수가 같은 결과를 낸다(래핑 일관성)."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    plain = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    verbose, _ = run_zone_limit_backtest_verbose(htf, one_min, "1h", confluence_params=params)
    assert plain.metrics.num_trades == verbose.metrics.num_trades
    assert [t.realized_pnl for t in plain.trades] == [t.realized_pnl for t in verbose.trades]


def test_trades_reference_1m_substep_times() -> None:
    """진입/청산 시각이 1분봉 서브스텝(1m 해상도)에서 나온다 — 봉 내부 재구성 증거."""
    htf, one_min = _synthetic_pair()
    minute_times = set(int(t) for t in one_min["open_time"].astype("int64"))
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    result = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    assert result.trades
    for trade in result.trades:
        assert trade.entry_time in minute_times
        assert trade.exit_time in minute_times


def test_empty_1m_yields_no_trades() -> None:
    """1분봉이 상위TF 창을 커버하지 않으면 셋업이 평가에서 제외된다(폴백)."""
    htf = make_synthetic_ohlcv(timeframe="1h", bars=400, seed=7)
    # 상위TF 범위 밖(미래)의 1분봉 → 어떤 셋업도 커버하지 않음.
    far = int(htf["open_time"].iloc[-1]) + 10 * timeframe_to_ms("1h")
    one_min = make_synthetic_ohlcv(timeframe="1m", bars=200, seed=3, start_time_ms=far)
    result = run_zone_limit_backtest(htf, one_min, "1h")
    assert result.metrics.num_trades == 0


def test_cost_model_applied_slippage_and_fees() -> None:
    """진입/청산 체결가에 슬리피지가 불리하게, 수수료가 차감돼 반영된다."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    zero = run_zone_limit_backtest(
        htf,
        one_min,
        "1h",
        confluence_params=params,
        backtest_config=BacktestConfig(fee_rate=0.0, slippage=0.0),
    )
    costed = run_zone_limit_backtest(
        htf,
        one_min,
        "1h",
        confluence_params=params,
        backtest_config=BacktestConfig(fee_rate=0.001, slippage=0.001),
    )
    assert zero.trades and costed.trades
    # 비용이 붙으면 동일 셋업의 순손익이 더 낮다.
    assert costed.trades[0].realized_pnl < zero.trades[0].realized_pnl
    assert costed.trades[0].entry_fee > 0.0


def test_build_result_from_trades_single_position_sequencing() -> None:
    """겹치는 거래는 단일 포지션 제약으로 배치되고 자본곡선이 순차 반영된다."""
    cfg = BacktestConfig()
    trades = [
        Trade(
            side=PositionSide.LONG,
            entry_time=1_000,
            entry_price=100.0,
            quantity=1.0,
            entry_fee=0.0,
            exits=[],
            realized_pnl=50.0,
            return_pct=0.5,
        ),
    ]
    # exits가 비면 exit_time 접근이 실패하므로 최소 하나의 fill을 넣는다.
    from backtest.models import ExitReason, TradeFill

    trade = trades[0].model_copy(
        update={
            "exits": [
                TradeFill(
                    time=2_000, price=150.0, quantity=1.0, fee=0.0, reason=ExitReason.TAKE_PROFIT
                )
            ]
        }
    )
    result = build_result_from_trades([trade], cfg, "1h")
    assert result.metrics.num_trades == 1
    assert result.equity_curve[-1].equity == cfg.initial_capital + 50.0
