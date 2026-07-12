"""A vs B 비교 리포트 테스트 (WAN-41).

같은 심볼·TF에서 진입 방식만 바꾼 결과를 나란히 놓고 CSV로 재현 가능하게 낸다.
심볼·TF별 행 + 변형별 합산 행(거래 풀링)이 나오는지, 합산 지표가 맞는지 검증한다.
"""

from __future__ import annotations

import csv
import io

from backtest.ab_report import ABEntry, build_ab_report
from backtest.metrics import build_metrics
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)


def _trade(pnl: float, return_pct: float) -> Trade:
    reason = ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS
    return Trade(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=0.0,
        exits=[TradeFill(time=60_000, price=100.0 + pnl, quantity=1.0, fee=0.0, reason=reason)],
        realized_pnl=pnl,
        return_pct=return_pct,
    )


def _result(trades: list[Trade], equities: list[float]) -> BacktestResult:
    metrics = build_metrics(initial_capital=equities[0], equities=equities, trades=trades)
    return BacktestResult(config=BacktestConfig(), trades=trades, equity_curve=[], metrics=metrics)


def _parse(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_report_has_per_setup_and_aggregate_rows() -> None:
    a = ABEntry("BTC", "1h", "A", _result([_trade(100, 0.1), _trade(-50, -0.05)], [10_000, 10_050]))
    b = ABEntry("BTC", "1h", "B", _result([_trade(200, 0.2), _trade(-40, -0.04)], [10_000, 10_160]))
    rows = _parse(build_ab_report([a, b]))

    per_setup = [r for r in rows if r["symbol"] == "BTC"]
    aggregates = [r for r in rows if r["symbol"] == "ALL"]
    assert len(per_setup) == 2
    assert {r["variant"] for r in aggregates} == {"A", "B"}


def test_aggregate_profit_factor_pools_trades() -> None:
    # A: 두 셋업(BTC/ETH) 거래를 풀링 → 총이익 300, 총손실 50 → PF=6.0.
    eth_trades = [_trade(200, 0.2), _trade(-50, -0.05)]
    entries = [
        ABEntry("BTC", "1h", "A", _result([_trade(100, 0.1)], [10_000, 10_100])),
        ABEntry("ETH", "1h", "A", _result(eth_trades, [10_000, 10_150])),
    ]
    rows = _parse(build_ab_report(entries))
    agg = next(r for r in rows if r["symbol"] == "ALL" and r["variant"] == "A")
    assert agg["num_trades"] == "3"
    assert agg["num_wins"] == "2"
    assert float(agg["profit_factor"]) == 6.0
    # 자본곡선 의존 지표는 합산 행에서 비운다.
    assert agg["total_return"] == ""
    assert agg["max_drawdown"] == ""


def test_report_is_deterministic() -> None:
    entries = [
        ABEntry("ETH", "4h", "B", _result([_trade(10, 0.01)], [10_000, 10_010])),
        ABEntry("BTC", "1h", "A", _result([_trade(20, 0.02)], [10_000, 10_020])),
    ]
    assert build_ab_report(entries) == build_ab_report(entries)


def test_empty_entries_yields_header_only() -> None:
    rows = _parse(build_ab_report([]))
    assert rows == []  # 헤더만, 데이터 행 없음
