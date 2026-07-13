"""백테스트 결과 리포트 (표·CSV·요약 텍스트).

`BacktestResult`를 사람이 읽는 요약 문자열, pandas DataFrame, CSV 파일로
변환한다. 재현을 위해 요약에는 파라미터(설정)와 시드를 함께 출력한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.models import BacktestResult, PositionSide


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
        "funding_coverage": m.funding_coverage,
        "fee_rate": c.fee_rate,
        "funding_enabled": c.funding_enabled,
        "slippage": c.slippage,
        "position_fraction": c.position_fraction,
        "sizing_mode": c.sizing_mode,
        "risk_per_trade": c.risk_per_trade,
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
        f"{'Funding Coverage':<20}{_fmt_coverage(m.funding_coverage):>16}",
        "--- Params ---",
        f"fee_rate={c.fee_rate} slippage={c.slippage} "
        f"position_fraction={c.position_fraction} seed={c.seed}",
        f"sizing_mode={c.sizing_mode} risk_per_trade={c.risk_per_trade}",
        f"stop_loss_pct={c.stop_loss_pct} take_profit_pct={c.take_profit_pct} "
        f"partial_take_profit_pct={c.partial_take_profit_pct}",
        f"funding_enabled={c.funding_enabled} "
        f"funding_include_predicted={c.funding_include_predicted} "
        f"funding_missing_policy={c.funding_missing_policy}",
    ]
    sizing_banner = sizing_mode_banner(result)
    if sizing_banner:
        lines.append(sizing_banner)
    banner = funding_coverage_banner(result)
    if banner:
        lines.append(banner)
    return "\n".join(lines)


def sizing_mode_banner(result: BacktestResult) -> str | None:
    """`risk_sizing=None`(전액 진입 모드)이면 리포트 상단에 띄울 경고 배너, 아니면 None.

    리스크 기반 사이징이 켜져 있으면(기본) 배너가 없다. 꺼져 있으면 매 거래가
    손절 거리와 무관하게 동일 비율의 자본을 쓴다는 것을 명시한다(WAN-65 조용한
    실패 방지 — `BacktestEngine`도 같은 조건에서 로그 경고를 낸다).
    """
    if result.config.risk_sizing is not None:
        return None
    return (
        f"⚠️  risk_sizing=None (전액 진입 모드, position_fraction="
        f"{result.config.position_fraction:.0%}): 손절 거리와 무관하게 매 거래가 동일 "
        "비율의 자본을 씁니다. 손익비·MDD·R 배수가 리스크 정규화되지 않았으므로 성과 "
        "판단에 주의하세요."
    )


def _fmt_coverage(value: float | None) -> str:
    """펀딩 커버리지 표시: 미사용이면 'N/A (off)', 그 외 백분율."""
    if value is None:
        return "N/A (off)"
    return f"{value * 100:.1f}%"


def funding_coverage_banner(result: BacktestResult) -> str | None:
    """펀딩 커버리지가 1.0 미만이면 리포트 상단에 띄울 경고 배너 문자열, 아니면 None.

    커버리지가 완전(1.0)하거나 펀딩 미사용(None)이면 배너가 없다. 1.0 미만이면
    "결측 구간을 0으로 때웠다 → 비용 과소 계상"임을 명시한다(WAN-63 조용한 실패 방지).
    """
    coverage = result.metrics.funding_coverage
    if coverage is None or coverage >= 1.0:
        return None
    return (
        f"⚠️  펀딩 데이터 커버리지 {coverage:.1%} (<100%): 결측 구간의 펀딩비를 0으로 "
        "처리했습니다. 표시된 비용·순손익은 실제보다 과소 계상됐을 수 있습니다. "
        "백테스트 구간 전체의 펀딩 이력을 백필한 뒤 재산출하세요."
    )


def long_short_breakdown(result: BacktestResult) -> pd.DataFrame:
    """롱/숏 방향별 성과 분해표. 펀딩비의 방향별 비대칭을 드러낸다(WAN-63).

    무기한 선물에서 펀딩비는 롱/숏에 반대 부호로 작용하므로, 방향을 합쳐 보면
    숏이 받은(또는 낸) 펀딩비가 가려진다. 각 방향에 대해 거래 수·승률·순손익·평균
    수익률·누적 펀딩비·수수료 합계를 따로 집계한다.
    """
    rows: list[dict[str, object]] = []
    for side in (PositionSide.LONG, PositionSide.SHORT):
        side_trades = [t for t in result.trades if t.side is side]
        n = len(side_trades)
        wins = sum(1 for t in side_trades if t.is_win)
        realized = sum(t.realized_pnl for t in side_trades)
        funding = sum(t.funding_cost for t in side_trades)
        fees = sum(t.entry_fee + sum(f.fee for f in t.exits) for t in side_trades)
        avg_ret = sum(t.return_pct for t in side_trades) / n if n else 0.0
        rows.append(
            {
                "side": side.value,
                "num_trades": n,
                "num_wins": wins,
                "win_rate": wins / n if n else 0.0,
                "realized_pnl": realized,
                "avg_return_pct": avg_ret,
                "funding_cost": funding,
                "fees": fees,
            }
        )
    columns = [
        "side",
        "num_trades",
        "num_wins",
        "win_rate",
        "realized_pnl",
        "avg_return_pct",
        "funding_cost",
        "fees",
    ]
    return pd.DataFrame(rows, columns=columns)


def format_long_short(result: BacktestResult) -> str:
    """`long_short_breakdown`을 사람이 읽는 표 문자열로 반환한다."""
    df = long_short_breakdown(result)
    lines = ["=== Long / Short Breakdown ==="]
    header = (
        f"{'side':>5} {'trades':>7} {'wins':>5} {'win%':>7} "
        f"{'realized':>12} {'avg_ret':>9} {'funding':>12} {'fees':>12}"
    )
    lines.append(header)
    for _, r in df.iterrows():
        lines.append(
            f"{str(r['side']):>5} {int(r['num_trades']):>7} {int(r['num_wins']):>5} "
            f"{float(r['win_rate']) * 100:>6.1f}% {float(r['realized_pnl']):>12,.2f} "
            f"{float(r['avg_return_pct']) * 100:>8.2f}% {float(r['funding_cost']):>12,.2f} "
            f"{float(r['fees']):>12,.2f}"
        )
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
