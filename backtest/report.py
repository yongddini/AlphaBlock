"""백테스트 결과 리포트 (표·CSV·요약 텍스트).

`BacktestResult`를 사람이 읽는 요약 문자열, pandas DataFrame, CSV 파일로
변환한다. 재현을 위해 요약에는 파라미터(설정)와 시드를 함께 출력한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.models import BacktestResult


def trades_to_dataframe(result: BacktestResult) -> pd.DataFrame:
    """거래 목록을 DataFrame으로. 부분 청산은 마지막 청산 사유로 대표한다."""
    rows: list[dict[str, object]] = []
    for tr in result.trades:
        rows.append(
            {
                "side": tr.side.value,
                "entry_time": tr.entry_time,
                "entry_price": tr.entry_price,
                "quantity": tr.quantity,
                "exit_time": tr.exit_time,
                "num_exits": len(tr.exits),
                "last_exit_reason": tr.exits[-1].reason.value,
                "entry_fee": tr.entry_fee,
                "exit_fees": sum(f.fee for f in tr.exits),
                "funding_cost": tr.funding_cost,
                "realized_pnl": tr.realized_pnl,
                "return_pct": tr.return_pct,
            }
        )
    columns = [
        "side",
        "entry_time",
        "entry_price",
        "quantity",
        "exit_time",
        "num_exits",
        "last_exit_reason",
        "entry_fee",
        "exit_fees",
        "funding_cost",
        "realized_pnl",
        "return_pct",
    ]
    return pd.DataFrame(rows, columns=columns)


def equity_to_dataframe(result: BacktestResult) -> pd.DataFrame:
    """자본곡선을 DataFrame으로 (`time`, `equity`)."""
    return pd.DataFrame(
        {
            "time": [p.time for p in result.equity_curve],
            "equity": [p.equity for p in result.equity_curve],
        }
    )


def summary_dict(result: BacktestResult) -> dict[str, object]:
    """지표·핵심 파라미터를 담은 평면 딕셔너리 (직렬화·로깅용)."""
    m = result.metrics
    c = result.config
    return {
        "initial_capital": m.initial_capital,
        "final_equity": m.final_equity,
        "total_return": m.total_return,
        "max_drawdown": m.max_drawdown,
        "win_rate": m.win_rate,
        "profit_factor": m.profit_factor,
        "sharpe": m.sharpe,
        "num_trades": m.num_trades,
        "num_wins": m.num_wins,
        "num_losses": m.num_losses,
        "gross_profit": m.gross_profit,
        "gross_loss": m.gross_loss,
        "avg_win": m.avg_win,
        "avg_loss": m.avg_loss,
        "total_funding_cost": m.total_funding_cost,
        "fee_rate": c.fee_rate,
        "funding_enabled": c.funding_enabled,
        "slippage": c.slippage,
        "position_fraction": c.position_fraction,
        "stop_loss_pct": c.stop_loss_pct,
        "take_profit_pct": c.take_profit_pct,
        "seed": c.seed,
    }


def _fmt(value: float | None, *, pct: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%" if pct else f"{value:,.2f}"


def format_summary(result: BacktestResult) -> str:
    """성과 지표를 정렬된 표 형태의 문자열로 반환."""
    m = result.metrics
    c = result.config
    lines = [
        "=== Backtest Summary ===",
        f"{'Initial Capital':<20}{_fmt(m.initial_capital):>16}",
        f"{'Final Equity':<20}{_fmt(m.final_equity):>16}",
        f"{'Total Return':<20}{_fmt(m.total_return, pct=True):>16}",
        f"{'Max Drawdown':<20}{_fmt(m.max_drawdown, pct=True):>16}",
        f"{'Win Rate':<20}{_fmt(m.win_rate, pct=True):>16}",
        f"{'Profit Factor':<20}{_fmt(m.profit_factor):>16}",
        f"{'Sharpe':<20}{_fmt(m.sharpe):>16}",
        f"{'Trades':<20}{m.num_trades:>16}",
        f"{'Wins / Losses':<20}{f'{m.num_wins} / {m.num_losses}':>16}",
        f"{'Avg Win / Loss':<20}{f'{m.avg_win:,.2f} / {m.avg_loss:,.2f}':>16}",
        f"{'Funding Cost':<20}{_fmt(m.total_funding_cost):>16}",
        "--- Params ---",
        f"fee_rate={c.fee_rate} slippage={c.slippage} "
        f"position_fraction={c.position_fraction} seed={c.seed}",
        f"stop_loss_pct={c.stop_loss_pct} take_profit_pct={c.take_profit_pct} "
        f"partial_take_profit_pct={c.partial_take_profit_pct}",
        f"funding_enabled={c.funding_enabled} "
        f"funding_include_predicted={c.funding_include_predicted} "
        f"funding_missing_policy={c.funding_missing_policy}",
    ]
    return "\n".join(lines)


def write_trades_csv(result: BacktestResult, path: str | Path) -> Path:
    """거래 목록을 CSV로 저장하고 경로를 반환."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    trades_to_dataframe(result).to_csv(out, index=False)
    return out


def write_equity_csv(result: BacktestResult, path: str | Path) -> Path:
    """자본곡선을 CSV로 저장하고 경로를 반환."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    equity_to_dataframe(result).to_csv(out, index=False)
    return out
