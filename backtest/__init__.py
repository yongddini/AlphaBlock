"""백테스팅 엔진 패키지.

지정가(B안, `zone_limit`) 백테스트는 `backtest.zone_limit_backtest`가, 격자·스윕은
범용 CLI `backtest.run`이 담당한다. 이 패키지 최상위는 그 진입점들이 공유하는 모델·
지표·리포트·합성데이터·타임프레임 유틸을 재-export한다.

⚠️ 옛 A안(종가 진입) 엔진(`BacktestEngine`/`run_backtest`)과 A안 스윕 기계
(`evaluate`/`run_sweep`/`Sweep*`)는 WAN-208/WAN-215로 제거됐다.
"""

from __future__ import annotations

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
from backtest.sweep import bars_per_year, default_backtest_config, timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv

__all__ = [
    "BacktestConfig",
    "BacktestMetrics",
    "BacktestResult",
    "EquityPoint",
    "ExitReason",
    "PositionSide",
    "Trade",
    "TradeFill",
    "bars_per_year",
    "build_metrics",
    "default_backtest_config",
    "equity_to_dataframe",
    "format_summary",
    "make_synthetic_ohlcv",
    "max_drawdown",
    "sharpe_ratio",
    "summary_dict",
    "timeframe_to_ms",
    "trades_to_dataframe",
    "write_equity_csv",
    "write_trades_csv",
]
