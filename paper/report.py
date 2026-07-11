"""페이퍼 성과 표·CSV·요약 텍스트 (WAN-33).

`paper.store`/`paper.performance`가 만든 값 객체를 사람이 읽는 요약 문자열과 pandas
DataFrame(CSV 내보내기·대시보드 표)으로 변환한다. 스크립트(`scripts/paper_report.py`)와
대시보드(`dashboard/app.py`)가 공용으로 쓴다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from paper.performance import PaperPerformance, PerfMetrics
from paper.store import PaperTradeRecord

# 거래 CSV 컬럼 순서.
_TRADE_COLUMNS = [
    "symbol",
    "timeframe",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "reason",
    "gross_pct",
    "fee_pct",
    "funding_pct",
    "net_pct",
    "risk_pct",
    "r_multiple",
    "stop_price",
    "take_profit_price",
]

# 성과 요약 표 컬럼 순서.
_PERF_COLUMNS = [
    "scope",
    "num_trades",
    "num_wins",
    "num_losses",
    "win_rate",
    "total_return_pct",
    "total_r",
    "avg_r",
    "payoff_ratio",
    "profit_factor",
    "max_drawdown_pct",
]


def records_to_dataframe(records: Sequence[PaperTradeRecord]) -> pd.DataFrame:
    """페이퍼 거래 목록을 DataFrame으로(CSV 내보내기용)."""
    rows = [
        {
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "direction": r.direction.value,
            "entry_time": r.entry_time,
            "entry_price": r.entry_price,
            "exit_time": r.exit_time,
            "exit_price": r.exit_price,
            "reason": r.reason.value,
            "gross_pct": r.gross_pct,
            "fee_pct": r.fee_pct,
            "funding_pct": r.funding_pct,
            "net_pct": r.net_pct,
            "risk_pct": r.risk_pct,
            "r_multiple": r.r_multiple,
            "stop_price": r.stop_price,
            "take_profit_price": r.take_profit_price,
        }
        for r in records
    ]
    return pd.DataFrame(rows, columns=_TRADE_COLUMNS)


def _metrics_row(scope: str, m: PerfMetrics) -> dict[str, object]:
    return {
        "scope": scope,
        "num_trades": m.num_trades,
        "num_wins": m.num_wins,
        "num_losses": m.num_losses,
        "win_rate": m.win_rate,
        "total_return_pct": m.total_return_pct,
        "total_r": m.total_r,
        "avg_r": m.avg_r,
        "payoff_ratio": m.payoff_ratio,
        "profit_factor": m.profit_factor,
        "max_drawdown_pct": m.max_drawdown_pct,
    }


def performance_to_dataframe(perf: PaperPerformance) -> pd.DataFrame:
    """전체 + 시리즈별 성과를 한 DataFrame으로(맨 위 행이 전체)."""
    rows = [_metrics_row("ALL", perf.overall)]
    rows += [_metrics_row(f"{s.symbol} {s.timeframe}", s.metrics) for s in perf.by_series]
    return pd.DataFrame(rows, columns=_PERF_COLUMNS)


def _fmt(value: float | None, *, pct: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%" if pct else f"{value:.2f}"


def _metrics_line(scope: str, m: PerfMetrics) -> str:
    return (
        f"{scope:<20} {m.num_trades:>4} {m.win_rate * 100:>6.1f} "
        f"{m.total_return_pct:>+8.2f} {_fmt(m.total_r):>7} {_fmt(m.avg_r):>6} "
        f"{_fmt(m.payoff_ratio):>6} {_fmt(m.profit_factor):>6} {m.max_drawdown_pct:>6.2f}"
    )


def format_performance(perf: PaperPerformance) -> str:
    """전체·시리즈별 성과를 정렬된 표 문자열로 반환한다."""
    header = (
        f"{'scope':<20} {'trd':>4} {'win%':>6} "
        f"{'ret%':>8} {'totR':>7} {'avgR':>6} {'payf':>6} {'pf':>6} {'mdd%':>6}"
    )
    lines = ["=== Paper Trading Performance ===", header, "-" * len(header)]
    lines.append(_metrics_line("ALL", perf.overall))
    for s in perf.by_series:
        lines.append(_metrics_line(f"{s.symbol} {s.timeframe}", s.metrics))
    return "\n".join(lines)
