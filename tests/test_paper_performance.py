"""paper.performance 테스트 — 성과 지표 집계 (WAN-33)."""

from __future__ import annotations

import math

from paper.performance import (
    PerfMetrics,
    TradeStat,
    build_performance,
    compute_metrics,
)
from paper.store import PaperTradeRecord
from strategy.models import OrderBlockDirection, SignalExitReason


def _stat(net_pct: float, r: float | None, exit_time: int) -> TradeStat:
    return TradeStat(net_pct=net_pct, r_multiple=r, exit_time=exit_time)


def test_compute_metrics_empty_is_zeroed() -> None:
    m = compute_metrics([])
    assert m == PerfMetrics(
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


def test_compute_metrics_known_fixture() -> None:
    stats = [
        _stat(2.0, 2.0, 1),
        _stat(-1.0, -1.0, 2),
        _stat(1.0, None, 3),  # R 없음(손절가 미상)
        _stat(-0.5, -0.5, 4),
    ]
    m = compute_metrics(stats)

    assert m.num_trades == 4
    assert m.num_wins == 2
    assert m.num_losses == 2
    assert m.win_rate == 0.5
    assert math.isclose(m.sum_net_pct, 1.5, rel_tol=1e-9)
    assert math.isclose(m.avg_win_pct, 1.5, rel_tol=1e-9)  # (2+1)/2
    assert math.isclose(m.avg_loss_pct, -0.75, rel_tol=1e-9)  # (-1-0.5)/2
    assert math.isclose(m.payoff_ratio or 0.0, 2.0, rel_tol=1e-9)
    assert math.isclose(m.profit_factor or 0.0, 2.0, rel_tol=1e-9)  # 3.0 / 1.5
    # R은 3개 거래에만 존재: 2 - 1 - 0.5 = 0.5, 평균 0.5/3
    assert math.isclose(m.total_r, 0.5, rel_tol=1e-9)
    assert math.isclose(m.avg_r or 0.0, 0.5 / 3.0, rel_tol=1e-9)
    # 자본곡선은 전액배팅이 아니라 리스크-사이징(WAN-207): 매 거래 r×risk_per_trade(1%) 복리.
    # R 없는 거래(None)는 리스크로 사이징할 수 없으므로 자본을 안 움직인다(계수 0).
    e = [1.0, 1.02, 1.02 * 0.99, 1.02 * 0.99, 1.02 * 0.99 * 0.995]
    assert math.isclose(m.total_return_pct, (e[-1] - 1.0) * 100.0, rel_tol=1e-9)
    # 고점 1.02 뒤 최저 e[-1] → 낙폭 (1.02-e[-1])/1.02
    assert math.isclose(m.max_drawdown_pct, (1.02 - e[-1]) / 1.02 * 100.0, rel_tol=1e-9)


def test_wallet_basis_uses_actual_equity_curve() -> None:
    """지갑 기준(전체 성과)은 청산 직후 실제 자본(equity_after)을 그대로 쓴다(WAN-207).

    전액배팅도, r×risk 정규화도 아닌 러너의 실제 잔고 경로 그 자체 — 초기 자본 대비
    총수익률·MDD가 나온다.
    """
    stats = [
        TradeStat(
            net_pct=1.0, r_multiple=1.5, exit_time=1, realized_pnl=60.0, equity_after=10_060.0
        ),
        TradeStat(
            net_pct=-0.5, r_multiple=-1.0, exit_time=2, realized_pnl=-40.0, equity_after=10_020.0
        ),
    ]
    m = compute_metrics(stats, wallet_basis=True, initial_equity=10_000.0)
    # 곡선 [10000, 10060, 10020] → 총수익률 (10020-10000)/10000 = +0.20%.
    assert math.isclose(m.total_return_pct, 0.20, rel_tol=1e-9)
    # 고점 10060 뒤 10020 → MDD (10060-10020)/10060.
    assert math.isclose(m.max_drawdown_pct, (10_060.0 - 10_020.0) / 10_060.0 * 100.0, rel_tol=1e-9)
    assert m.total_realized_pnl == 20.0


def test_dollar_aggregates_none_when_absent() -> None:
    """달러 데이터가 없는 옛 행만 있으면 달러 집계는 None이다(값 없음과 0 구분)."""
    m = compute_metrics([_stat(1.0, 1.0, 1), _stat(-0.5, -0.5, 2)])
    assert m.total_notional is None
    assert m.total_risk_amount is None
    assert m.total_realized_pnl is None


def test_compute_metrics_all_wins_has_no_profit_factor() -> None:
    m = compute_metrics([_stat(1.0, 1.0, 1), _stat(2.0, 2.0, 2)])
    assert m.win_rate == 1.0
    assert m.profit_factor is None  # 손실 없음
    assert m.payoff_ratio is None
    assert m.max_drawdown_pct == 0.0


def _record(
    symbol: str, timeframe: str, net_pct: float, r: float | None, exit_time: int
) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol=symbol,
        timeframe=timeframe,
        direction=OrderBlockDirection.BULLISH,
        entry_time=exit_time - 100,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=100.0 + net_pct,
        reason=SignalExitReason.TAKE_PROFIT,
        gross_pct=net_pct,
        fee_pct=0.0,
        funding_pct=0.0,
        net_pct=net_pct,
        risk_pct=None if r is None else abs(net_pct / r),
        r_multiple=r,
    )


def test_build_performance_groups_by_series() -> None:
    records = [
        _record("BTC/USDT:USDT", "1h", 2.0, 2.0, 1000),
        _record("BTC/USDT:USDT", "1h", -1.0, -1.0, 2000),
        _record("ETH/USDT:USDT", "4h", 3.0, 1.5, 1500),
    ]
    perf = build_performance(records)

    assert perf.overall.num_trades == 3
    assert [(s.symbol, s.timeframe) for s in perf.by_series] == [
        ("BTC/USDT:USDT", "1h"),
        ("ETH/USDT:USDT", "4h"),
    ]
    btc = perf.by_series[0].metrics
    assert btc.num_trades == 2
    assert btc.win_rate == 0.5
    eth = perf.by_series[1].metrics
    assert eth.num_trades == 1
    assert eth.win_rate == 1.0
