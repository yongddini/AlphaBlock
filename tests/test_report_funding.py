"""리포트의 펀딩 커버리지 노출·롱숏 분해 테스트 (WAN-63).

`net`이라 이름 붙은 성과가 실제로는 펀딩비를 빠뜨린 값이 되지 않도록, 리포트가
펀딩 커버리지를 드러내고(1.0 미만이면 경고 배너), 펀딩비의 롱/숏 비대칭을 볼 수
있게 방향별 성과를 분해하는지 검증한다.
"""

from __future__ import annotations

from backtest.metrics import build_metrics
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)
from backtest.report import (
    format_summary,
    funding_coverage_banner,
    long_short_breakdown,
    summary_dict,
)


def _trade(side: PositionSide, pnl: float, funding_cost: float) -> Trade:
    reason = ExitReason.TAKE_PROFIT if pnl > 0 else ExitReason.STOP_LOSS
    return Trade(
        side=side,
        entry_time=0,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=1.0,
        exits=[TradeFill(time=60_000, price=100.0 + pnl, quantity=1.0, fee=1.0, reason=reason)],
        funding_cost=funding_cost,
        realized_pnl=pnl,
        return_pct=pnl / 100.0,
    )


def _result(trades: list[Trade], *, funding_coverage: float | None) -> BacktestResult:
    metrics = build_metrics(
        initial_capital=10_000.0,
        equities=[10_000.0, 10_000.0],
        trades=trades,
        funding_coverage=funding_coverage,
    )
    return BacktestResult(
        config=BacktestConfig(funding_enabled=True),
        trades=trades,
        equity_curve=[],
        metrics=metrics,
    )


def test_banner_shown_when_coverage_incomplete() -> None:
    result = _result([_trade(PositionSide.LONG, 10.0, 0.0)], funding_coverage=0.5)
    banner = funding_coverage_banner(result)
    assert banner is not None
    assert "50.0%" in banner
    assert banner in format_summary(result)


def test_no_banner_when_coverage_full_or_off() -> None:
    full = _result([_trade(PositionSide.LONG, 10.0, 0.0)], funding_coverage=1.0)
    assert funding_coverage_banner(full) is None
    off = _result([_trade(PositionSide.LONG, 10.0, 0.0)], funding_coverage=None)
    assert funding_coverage_banner(off) is None


def test_summary_dict_exposes_coverage() -> None:
    result = _result([_trade(PositionSide.LONG, 10.0, 0.0)], funding_coverage=0.75)
    assert summary_dict(result)["funding_coverage"] == 0.75


def test_long_short_breakdown_splits_funding_by_direction() -> None:
    # 롱은 펀딩 지불(+), 숏은 펀딩 수취(-) — 방향별로 따로 집계돼야 한다.
    trades = [
        _trade(PositionSide.LONG, 20.0, 5.0),
        _trade(PositionSide.LONG, -10.0, 5.0),
        _trade(PositionSide.SHORT, 15.0, -8.0),
    ]
    df = long_short_breakdown(_result(trades, funding_coverage=1.0))
    by_side = {row["side"]: row for _, row in df.iterrows()}

    assert by_side["long"]["num_trades"] == 2
    assert by_side["long"]["num_wins"] == 1
    assert by_side["long"]["funding_cost"] == 10.0  # 5 + 5 (지불)
    assert by_side["short"]["num_trades"] == 1
    assert by_side["short"]["funding_cost"] == -8.0  # 수취
    # 방향을 합치면 숏이 받은 펀딩(-8)이 가려진다 — 분해가 그것을 드러낸다.
    assert by_side["long"]["realized_pnl"] == 10.0  # 20 - 10
    assert by_side["short"]["realized_pnl"] == 15.0
