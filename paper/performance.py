"""페이퍼 거래 성과 집계 (WAN-33, 순수 함수).

저장된 페이퍼 거래(`paper.store.PaperTradeRecord`)로부터 총 PnL(R 배수 및 %)·승률·
손익비·MDD·거래 수를 계산한다. 백테스트 거래도 동일한 지표로 비교하려고, 지표 계산은
최소 통계 단위(`TradeStat`: 순손익률·R 배수·청산시각)에만 의존하도록 분리한다. 이렇게
하면 패리티(`paper.parity`)가 페이퍼·백테스트 양쪽을 **같은 함수**로 집계할 수 있다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from backtest.metrics import max_drawdown
from paper.store import PaperTradeRecord


@dataclass(frozen=True)
class TradeStat:
    """지표 계산에 필요한 거래 최소 통계.

    `net_pct`는 모든 비용을 반영한 순손익률(%), `r_multiple`은 리스크 대비 손익 배수
    (없으면 None), `exit_time`은 자본곡선(MDD) 정렬용 청산 시각이다.
    """

    net_pct: float
    r_multiple: float | None
    exit_time: int


def record_to_stat(record: PaperTradeRecord) -> TradeStat:
    """`PaperTradeRecord`를 `TradeStat`으로 축약한다."""
    return TradeStat(
        net_pct=record.net_pct,
        r_multiple=record.r_multiple,
        exit_time=record.exit_time,
    )


class PerfMetrics(BaseModel):
    """페이퍼(또는 백테스트) 거래 묶음의 성과 지표."""

    model_config = ConfigDict(frozen=True)

    num_trades: int
    num_wins: int
    num_losses: int
    win_rate: float
    """승률(순손익>0 비율). 거래가 없으면 0."""
    total_return_pct: float
    """순손익률을 복리로 누적한 총수익률(%)."""
    sum_net_pct: float
    """순손익률 단순 합(%)."""
    total_r: float
    """R 배수 합(R 배수가 있는 거래만)."""
    avg_r: float | None
    """평균 R 배수(R 배수가 있는 거래 대상). 없으면 None."""
    avg_win_pct: float
    """이긴 거래의 평균 순손익률(%). 없으면 0."""
    avg_loss_pct: float
    """진 거래의 평균 순손익률(%, 음수). 없으면 0."""
    payoff_ratio: float | None
    """손익비 = 평균이익 / |평균손실|. 손실 거래가 없으면 None."""
    profit_factor: float | None
    """총이익 / 총손실. 손실이 없으면 None."""
    max_drawdown_pct: float
    """복리 자본곡선의 최대 낙폭(%). 예: 25.0 = 고점 대비 25% 하락."""


def compute_metrics(stats: Iterable[TradeStat]) -> PerfMetrics:
    """거래 통계로부터 성과 지표를 조립한다(거래가 없어도 안전)."""
    ordered = sorted(stats, key=lambda s: s.exit_time)
    num_trades = len(ordered)
    if num_trades == 0:
        return PerfMetrics(
            num_trades=0,
            num_wins=0,
            num_losses=0,
            win_rate=0.0,
            total_return_pct=0.0,
            sum_net_pct=0.0,
            total_r=0.0,
            avg_r=None,
            avg_win_pct=0.0,
            avg_loss_pct=0.0,
            payoff_ratio=None,
            profit_factor=None,
            max_drawdown_pct=0.0,
        )

    wins = [s for s in ordered if s.net_pct > 0.0]
    losses = [s for s in ordered if s.net_pct < 0.0]
    gross_profit = sum(s.net_pct for s in wins)
    gross_loss = -sum(s.net_pct for s in losses)  # 양수

    sum_net_pct = sum(s.net_pct for s in ordered)
    win_rate = len(wins) / num_trades
    avg_win_pct = gross_profit / len(wins) if wins else 0.0
    avg_loss_pct = -gross_loss / len(losses) if losses else 0.0
    payoff_ratio = (avg_win_pct / abs(avg_loss_pct)) if avg_loss_pct != 0.0 else None
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0.0 else None

    r_values = [s.r_multiple for s in ordered if s.r_multiple is not None]
    total_r = sum(r_values)
    avg_r = (total_r / len(r_values)) if r_values else None

    # 복리 자본곡선(초기 1.0)으로 총수익률·MDD를 산출한다.
    equity = 1.0
    curve = [equity]
    for s in ordered:
        equity *= 1.0 + s.net_pct / 100.0
        curve.append(equity)
    total_return_pct = (curve[-1] - 1.0) * 100.0
    max_drawdown_pct = max_drawdown(curve) * 100.0

    return PerfMetrics(
        num_trades=num_trades,
        num_wins=len(wins),
        num_losses=len(losses),
        win_rate=win_rate,
        total_return_pct=total_return_pct,
        sum_net_pct=sum_net_pct,
        total_r=total_r,
        avg_r=avg_r,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        payoff_ratio=payoff_ratio,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
    )


class SeriesPerformance(BaseModel):
    """한 시리즈(심볼·TF)의 성과 지표."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    metrics: PerfMetrics


class PaperPerformance(BaseModel):
    """전체 + 시리즈별 페이퍼 성과 집계."""

    model_config = ConfigDict(frozen=True)

    overall: PerfMetrics
    by_series: list[SeriesPerformance]


def build_performance(records: Sequence[PaperTradeRecord]) -> PaperPerformance:
    """페이퍼 거래 목록에서 전체·시리즈별 성과를 집계한다.

    시리즈는 (symbol, timeframe)로 그룹핑하고 결정적 순서(정렬)로 반환한다.
    """
    overall = compute_metrics(record_to_stat(r) for r in records)

    grouped: dict[tuple[str, str], list[PaperTradeRecord]] = {}
    for record in records:
        grouped.setdefault((record.symbol, record.timeframe), []).append(record)

    by_series = [
        SeriesPerformance(
            symbol=symbol,
            timeframe=timeframe,
            metrics=compute_metrics(record_to_stat(r) for r in group),
        )
        for (symbol, timeframe), group in sorted(grouped.items())
    ]
    return PaperPerformance(overall=overall, by_series=by_series)
