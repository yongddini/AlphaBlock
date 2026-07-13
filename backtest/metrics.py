"""백테스트 성과 지표 계산 (순수 함수).

자본곡선(equity curve)과 거래 목록으로부터 총수익률·MDD·승률·손익비·샤프
등을 산출한다. 엔진과 분리해 단위 테스트가 쉽도록 한다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from backtest.models import BacktestMetrics, Trade


def max_drawdown(equities: Sequence[float]) -> float:
    """자본곡선의 최대 낙폭을 양수 분수로 반환. 값이 없으면 0.0."""
    peak = -math.inf
    mdd = 0.0
    for value in equities:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak
            if drawdown > mdd:
                mdd = drawdown
    return mdd


def bar_returns(equities: Sequence[float]) -> list[float]:
    """봉 간 단순 수익률 시퀀스. 직전 값이 0 이하이면 0으로 취급."""
    returns: list[float] = []
    for prev, curr in zip(equities[:-1], equities[1:], strict=True):
        returns.append((curr / prev - 1.0) if prev > 0 else 0.0)
    return returns


def sharpe_ratio(
    equities: Sequence[float], annualization_factor: float | None = None
) -> float | None:
    """봉 수익률 기반 샤프 지수. 표본이 부족하거나 표준편차가 0이면 None.

    `annualization_factor`(연간 봉 수)가 주어지면 `sqrt(factor)`를 곱해 연율화한다.
    """
    returns = bar_returns(equities)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    sharpe = mean / std
    if annualization_factor is not None:
        sharpe *= math.sqrt(annualization_factor)
    return sharpe


def build_metrics(
    *,
    initial_capital: float,
    equities: Sequence[float],
    trades: Sequence[Trade],
    annualization_factor: float | None = None,
    funding_coverage: float | None = None,
) -> BacktestMetrics:
    """자본곡선·거래로부터 전체 성과 지표를 조립한다.

    `funding_coverage`(0.0~1.0)는 펀딩비를 반영한 경우 백테스트 구간의 펀딩 데이터
    커버리지 비율이다. 펀딩 미사용이면 None으로 둔다(WAN-63).
    """
    final_equity = equities[-1] if equities else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital if initial_capital else 0.0

    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl < 0]
    gross_profit = sum(t.realized_pnl for t in wins)
    gross_loss = -sum(t.realized_pnl for t in losses)  # 양수
    num_trades = len(trades)

    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    win_rate = len(wins) / num_trades if num_trades else 0.0
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    total_funding_cost = sum(t.funding_cost for t in trades)

    return BacktestMetrics(
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_return=total_return,
        max_drawdown=max_drawdown(equities),
        win_rate=win_rate,
        profit_factor=profit_factor,
        sharpe=sharpe_ratio(equities, annualization_factor),
        num_trades=num_trades,
        num_wins=len(wins),
        num_losses=len(losses),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        avg_win=avg_win,
        avg_loss=avg_loss,
        total_funding_cost=total_funding_cost,
        funding_coverage=funding_coverage,
    )
