"""페이퍼 거래 성과 집계 (WAN-33, 순수 함수).

저장된 페이퍼 거래(`paper.store.PaperTradeRecord`)로부터 총 PnL(R 배수 및 %)·승률·
손익비·MDD·거래 수를 계산한다. 백테스트 거래도 동일한 지표로 비교하려고, 지표 계산은
최소 통계 단위(`TradeStat`: 순손익률·R 배수·청산시각)에만 의존하도록 분리한다.
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

    달러 필드(WAN-207)는 청산 시 실제 지갑에서 집계한 값으로, 자본곡선을 전액배팅이
    아니라 실제 지갑 기준으로 재구성하는 데 쓴다. 옛 %-only 행에는 없어서 None이다.
    """

    net_pct: float
    r_multiple: float | None
    exit_time: int
    notional: float | None = None
    risk_amount: float | None = None
    realized_pnl: float | None = None
    equity_after: float | None = None


def record_to_stat(record: PaperTradeRecord) -> TradeStat:
    """`PaperTradeRecord`를 `TradeStat`으로 축약한다."""
    return TradeStat(
        net_pct=record.net_pct,
        r_multiple=record.r_multiple,
        exit_time=record.exit_time,
        notional=record.notional,
        risk_amount=record.risk_amount,
        realized_pnl=record.realized_pnl,
        equity_after=record.equity_after,
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
    """**리스크-사이징 지갑 기준** 총수익률(%) — 전액배팅이 아니다(WAN-207).

    옛 구현은 매 거래에 자본 100%를 넣었다고 치고 `net_pct`를 복리로 누적해(전액배팅)
    실제 잔고와 부호·크기가 어긋났다. 지금은 실제 지갑 자본 곡선으로 낸다: 달러 데이터가
    있으면 청산 직후 자본(`equity_after`)을 그대로 쓰고(정확), 없으면 매 거래
    `r_multiple × risk_per_trade`로 복리(리스크-사이징 정규화)한다 — 둘 다 백테스트와
    같은 자(사이징)다."""
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
    """자본곡선(리스크-사이징 지갑 기준)의 최대 낙폭(%). 예: 25.0 = 고점 대비 25% 하락."""
    total_notional: float | None = None
    """총 투입 명목 금액(달러, "얼마 들어갔는지"). 달러 데이터가 없으면 None(WAN-207)."""
    total_risk_amount: float | None = None
    """총 리스크 금액(달러). 달러 데이터가 없으면 None(WAN-207)."""
    total_realized_pnl: float | None = None
    """총 실현 달러 손익. 달러 데이터가 없으면 None(WAN-207)."""


#: 채택 사이징의 거래당 리스크 비율(자본 대비). 달러 데이터가 없는 옛 행의 자본곡선을
#: 리스크-사이징으로 정규화할 때 쓴다(백테스트·지갑과 같은 자).
DEFAULT_RISK_PER_TRADE = 0.01


def _equity_curve(
    ordered: list[TradeStat],
    *,
    risk_per_trade: float,
    wallet_basis: bool,
    initial_equity: float | None,
) -> list[float]:
    """전액배팅이 아닌 자본곡선(WAN-207/237).

    * **지갑 기준**(`wallet_basis` + 모든 거래에 달러 실현손익 존재): 초기 자본에서 매 거래
      `realized_pnl`을 **누적 합산**해 지갑 잔고 경로를 재구성한다. 여러 칸(종목·TF)이 **한
      지갑을 공유**하므로(WAN-213) 곡선은 마지막 거래의 스냅샷(`equity_after`)이 아니라 모든
      칸의 실현손익 합이어야 한다 — 옛 구현은 행마다 적힌 `equity_after`를 그대로 이어 붙여,
      각 칸이 독립 자본으로 기록된 장부(WAN-171 배선 전 서버)에서는 마지막 칸만 반영되고
      지갑을 합산하지 못했다(WAN-237 실측: 손절 2건인데 잔고는 한 건만 빠졌다). 공유 엔진이
      순차로 기록한 정상 장부에서는 `equity_after` 체인과 **비트 단위로 동일**하다(엔진 달러
      자본은 브로커 수수료 0으로 흐르므로 `초기 + Σrealized_pnl == equity_after`). 시작점은
      주어진 초기 자본, 없으면 첫 거래 직전 자본으로 근사한다.
    * **리스크-사이징 정규화**(그 외): 초기 1.0에서 매 거래 `r_multiple × risk_per_trade`만큼
      복리한다 — 전액배팅(`net_pct` 복리)이 아니라 백테스트와 같은 사이징 자다. 시리즈별
      곡선은 지갑이 공유돼 격리되지 않으므로 이 정규화 곡선을 쓴다.
    """
    if wallet_basis and all(s.realized_pnl is not None for s in ordered):
        base = initial_equity
        if base is None:
            first = ordered[0]
            # 초기 자본 미지정: 첫 거래 직전 자본으로 근사(청산 직후 자본 − 그 거래 실현손익).
            after = first.equity_after
            base = (0.0 if after is None else float(after)) - (first.realized_pnl or 0.0)
        curve = [base]
        running = base
        for s in ordered:
            running += s.realized_pnl or 0.0
            curve.append(running)
        return curve

    equity = 1.0
    curve = [equity]
    for s in ordered:
        equity *= 1.0 + (s.r_multiple or 0.0) * risk_per_trade
        curve.append(equity)
    return curve


def compute_metrics(
    stats: Iterable[TradeStat],
    *,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    wallet_basis: bool = False,
    initial_equity: float | None = None,
) -> PerfMetrics:
    """거래 통계로부터 성과 지표를 조립한다(거래가 없어도 안전).

    총수익률·MDD는 **전액배팅이 아니라** 리스크-사이징 지갑 기준으로 낸다(WAN-207).
    `wallet_basis=True`(전체 성과)이고 모든 거래에 달러 데이터가 있으면 실제 지갑 자본
    곡선(`equity_after`)을 그대로 써 러너 잔고와 정확히 일치시킨다. 시리즈별 등 그 외
    경우는 `r_multiple × risk_per_trade` 복리로 정규화한다.
    """
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
            total_notional=None,
            total_risk_amount=None,
            total_realized_pnl=None,
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

    # 자본곡선(리스크-사이징 지갑 기준)으로 총수익률·MDD를 산출한다(WAN-207).
    curve = _equity_curve(
        ordered,
        risk_per_trade=risk_per_trade,
        wallet_basis=wallet_basis,
        initial_equity=initial_equity,
    )
    total_return_pct = (curve[-1] / curve[0] - 1.0) * 100.0 if curve[0] != 0.0 else 0.0
    max_drawdown_pct = max_drawdown(curve) * 100.0

    # 달러 집계(값이 있는 거래만; 하나도 없으면 None으로 남겨 "데이터 없음"을 구분).
    notionals = [s.notional for s in ordered if s.notional is not None]
    risks = [s.risk_amount for s in ordered if s.risk_amount is not None]
    pnls = [s.realized_pnl for s in ordered if s.realized_pnl is not None]

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
        total_notional=sum(notionals) if notionals else None,
        total_risk_amount=sum(risks) if risks else None,
        total_realized_pnl=sum(pnls) if pnls else None,
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


def build_performance(
    records: Sequence[PaperTradeRecord],
    *,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    initial_equity: float | None = None,
) -> PaperPerformance:
    """페이퍼 거래 목록에서 전체·시리즈별 성과를 집계한다.

    시리즈는 (symbol, timeframe)로 그룹핑하고 결정적 순서(정렬)로 반환한다.

    **전체 성과**는 실제 지갑 기준(`wallet_basis`)으로 낸다 — 지갑은 시리즈가 공유하므로
    청산시각으로 정렬한 `equity_after` 곡선이 곧 러너의 실제 잔고 경로다(WAN-207).
    `initial_equity`(러너 초기 자본, `settings.paper_equity`)를 주면 그 자본 기준 총수익률로
    정확해진다. **시리즈별**은 지갑이 격리되지 않으므로 리스크-사이징 정규화 곡선을 쓴다.
    """
    overall = compute_metrics(
        (record_to_stat(r) for r in records),
        risk_per_trade=risk_per_trade,
        wallet_basis=True,
        initial_equity=initial_equity,
    )

    grouped: dict[tuple[str, str], list[PaperTradeRecord]] = {}
    for record in records:
        grouped.setdefault((record.symbol, record.timeframe), []).append(record)

    by_series = [
        SeriesPerformance(
            symbol=symbol,
            timeframe=timeframe,
            metrics=compute_metrics(
                (record_to_stat(r) for r in group), risk_per_trade=risk_per_trade
            ),
        )
        for (symbol, timeframe), group in sorted(grouped.items())
    ]
    return PaperPerformance(overall=overall, by_series=by_series)
