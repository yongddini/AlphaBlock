"""백테스팅 엔진 패키지.

오더블록 전략 시그널(`strategy.order_blocks`)을 과거 OHLCV에 대해
시뮬레이션해 성과 지표를 산출한다.

    from backtest import BacktestConfig, run_backtest, format_summary

    result = run_backtest(ohlcv_df, signals, BacktestConfig(take_profit_pct=0.05))
    print(format_summary(result))
"""

from __future__ import annotations

from backtest.engine import BacktestEngine, run_backtest
from backtest.metrics import build_metrics, max_drawdown, sharpe_ratio
from backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    EquityPoint,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)
from backtest.report import (
    equity_to_dataframe,
    format_summary,
    summary_dict,
    trades_to_dataframe,
    write_equity_csv,
    write_trades_csv,
)
from backtest.sweep import (
    MultiSweepReport,
    ParamGrid,
    SweepPoint,
    SweepReport,
    SweepRunRow,
    apply_sweep_point,
    bars_per_year,
    evaluate,
    run_sweep,
    timeframe_to_ms,
    write_sweep_csv,
)
from backtest.synthetic import make_synthetic_ohlcv
from backtest.walkforward import (
    WalkForwardReport,
    WalkForwardRow,
    WalkForwardWindow,
    generate_windows,
    run_walk_forward,
    write_walk_forward_csv,
)

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "EquityPoint",
    "ExitReason",
    "MultiSweepReport",
    "ParamGrid",
    "PositionSide",
    "SweepPoint",
    "SweepReport",
    "SweepRunRow",
    "Trade",
    "TradeFill",
    "WalkForwardReport",
    "WalkForwardRow",
    "WalkForwardWindow",
    "apply_sweep_point",
    "bars_per_year",
    "build_metrics",
    "equity_to_dataframe",
    "evaluate",
    "format_summary",
    "generate_windows",
    "make_synthetic_ohlcv",
    "max_drawdown",
    "run_backtest",
    "run_sweep",
    "run_walk_forward",
    "sharpe_ratio",
    "summary_dict",
    "timeframe_to_ms",
    "trades_to_dataframe",
    "write_equity_csv",
    "write_sweep_csv",
    "write_trades_csv",
    "write_walk_forward_csv",
]
