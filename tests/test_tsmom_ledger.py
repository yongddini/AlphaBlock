"""엔진 C 페이퍼 원장(WAN-451 ⑨) — 매일 정산 · 실제 거래 명목 비용 · 이중 계상 거부."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.models import FundingRate
from paper.tsmom_ledger import (
    LEDGER_TABLES,
    EquityMark,
    Holding,
    TsmomLedgerStore,
    plan_rebalance,
)
from strategy import tsmom

DAY = tsmom.DAY_MS
T0 = 1_600_000_000_000 // DAY * DAY
BTC = "BTC/USDT:USDT"


def _target(weight: float, as_of: int, asset: str = BTC) -> tsmom.TsmomTarget:
    return tsmom.TsmomTarget(asset=asset, as_of_ms=as_of, signal=0.5, annual_vol=0.6, weight=weight)


def _rate(t: int, r: float) -> FundingRate:
    return FundingRate(symbol=BTC, funding_time=t, rate=r)


def test_first_day_opens_target_and_pays_cost_on_traded_notional() -> None:
    row = plan_rebalance(_target(1.0, T0), price=50_000.0, equity_before=10_000.0, holding=None)
    frac = tsmom.notional_fraction(1.0)
    assert row.settled_pnl == 0.0 and row.funding == 0.0
    assert row.target_notional == pytest.approx(10_000.0 * frac)
    assert row.new_qty == pytest.approx(10_000.0 * frac / 50_000.0)
    assert row.traded_notional == pytest.approx(10_000.0 * frac)
    assert row.cost == pytest.approx(10_000.0 * frac * 0.0009)
    assert row.executed_ms == T0 + DAY
    assert row.net_pnl == pytest.approx(-row.cost)


def test_settlement_is_held_fraction_times_daily_return() -> None:
    """정산 손익 ÷ 어제 기준 자본 = 어제 배율의 명목 비율 × 오늘 수익 — 백테스트 `held × r`."""
    rng = np.random.default_rng(3)
    prices = 40_000.0 * np.cumprod(1.0 + rng.normal(0, 0.03, 40))
    weights = rng.uniform(-2, 2, 40)
    equity = 10_000.0
    holding: Holding | None = None
    prev_basis = prev_w = None
    for i, (p, w) in enumerate(zip(prices, weights, strict=True)):
        row = plan_rebalance(
            _target(float(w), T0 + i * DAY), price=float(p), equity_before=equity, holding=holding
        )
        if prev_basis is not None and prev_w is not None:
            r = p / prices[i - 1] - 1.0
            assert row.settled_pnl == pytest.approx(
                tsmom.notional_fraction(prev_w) * prev_basis * r, rel=1e-12
            )
        equity += row.net_pnl
        holding = Holding(BTC, row.new_qty, row.price, row.as_of_ms)
        prev_basis, prev_w = row.equity_basis, float(w)


def test_cost_uses_actual_traded_notional_not_weight_change() -> None:
    """가격이 움직이면 같은 배율이어도 다시 맞추는 거래가 생긴다(drift) — 그 거래에 비용을 문다."""
    first = plan_rebalance(_target(1.0, T0), price=100.0, equity_before=10_000.0, holding=None)
    h = Holding(BTC, first.new_qty, 100.0, T0)
    second = plan_rebalance(
        _target(1.0, T0 + DAY), price=110.0, equity_before=10_000.0 - first.cost, holding=h
    )
    assert second.weight == first.weight
    assert second.traded_notional > 0.0
    assert second.cost == pytest.approx(second.traded_notional * tsmom.COST)


def test_short_profits_when_price_falls_and_flip_trades_both_legs() -> None:
    first = plan_rebalance(_target(-1.0, T0), price=100.0, equity_before=10_000.0, holding=None)
    assert first.new_qty < 0
    h = Holding(BTC, first.new_qty, 100.0, T0)
    flip = plan_rebalance(_target(1.0, T0 + DAY), price=90.0, equity_before=10_000.0, holding=h)
    assert flip.settled_pnl == pytest.approx(first.new_qty * -10.0) and flip.settled_pnl > 0
    assert flip.new_qty > 0
    assert flip.traded_notional == pytest.approx((flip.new_qty - first.new_qty) * 90.0)


def test_funding_long_pays_short_receives_only_inside_held_window() -> None:
    h_long = Holding(BTC, 1.0, 100.0, T0)
    rates = [
        _rate(T0 + DAY - 1, 0.01),  # 보유 시작(D+1 00:00) 전 — 제외
        _rate(T0 + DAY, 0.001),
        _rate(T0 + DAY + 8 * 3_600_000, 0.002),
        _rate(T0 + 2 * DAY, 0.05),  # 지금(거래 시각) — 배타
    ]
    row = plan_rebalance(
        _target(0.0, T0 + DAY),
        price=100.0,
        equity_before=10_000.0,
        holding=h_long,
        funding_rates=rates,
    )
    assert row.funding == pytest.approx(100.0 * 0.003)
    h_short = Holding(BTC, -1.0, 100.0, T0)
    row_s = plan_rebalance(
        _target(0.0, T0 + DAY),
        price=100.0,
        equity_before=10_000.0,
        holding=h_short,
        funding_rates=rates,
    )
    assert row_s.funding == pytest.approx(-100.0 * 0.003)


@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [
        ({"price": 0.0, "equity_before": 1.0, "holding": None}, "가격이 비양수"),
        ({"price": 1.0, "equity_before": 0.0, "holding": None}, "자본이 비양수"),
        (
            {
                "price": 1.0,
                "equity_before": 1.0,
                "holding": Holding("ETH/USDT:USDT", 1, 1, T0 - DAY),
            },
            "목표 자산",
        ),
        ({"price": 1.0, "equity_before": 1.0, "holding": Holding(BTC, 1, 1, T0)}, "이중 계상"),
    ],
)
def test_plan_refuses(kwargs: dict[str, object], msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        plan_rebalance(_target(1.0, T0), **kwargs)  # type: ignore[arg-type]


def test_store_roundtrip_restore_and_double_booking(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    with TsmomLedgerStore(db) as store:
        assert store.holding(BTC) is None and store.total_net_pnl() == 0.0
        a = plan_rebalance(_target(1.0, T0), price=100.0, equity_before=10_000.0, holding=None)
        store.append(a)
        with pytest.raises(ValueError, match="이중 계상"):
            store.append(a)
        h = store.holding(BTC)
        assert h == Holding(BTC, a.new_qty, 100.0, T0)
        b = plan_rebalance(
            _target(0.5, T0 + DAY), price=105.0, equity_before=10_000.0 + a.net_pnl, holding=h
        )
        store.append(b)
    with TsmomLedgerStore(db) as reopened:
        assert reopened.rows() == [a, b]
        assert reopened.total_net_pnl() == pytest.approx(a.net_pnl + b.net_pnl)
        assert reopened.holding(BTC) == Holding(BTC, b.new_qty, 105.0, T0 + DAY)


def test_equity_marks_upsert_by_time(tmp_path: Path) -> None:
    with TsmomLedgerStore(tmp_path / "m.db") as store:
        store.record_mark(EquityMark(T0, 10_000.0, 0.0, 0))
        store.record_mark(EquityMark(T0, 10_100.0, -50.0, 2))
        store.record_mark(EquityMark(T0 + DAY, 10_200.0, 25.0, 1))
        marks = store.marks()
    assert [m.mark_ms for m in marks] == [T0, T0 + DAY]
    assert marks[0].equity == 10_050.0 and marks[0].open_positions == 2


def test_ledger_tables_named_for_reset() -> None:
    assert LEDGER_TABLES == ("tsmom_rebalances", "paper_equity_marks")


def test_end_to_end_from_daily_closes_matches_target_module() -> None:
    """종가 → 목표(단일 소스) → 원장: 원장의 배율이 `tsmom_weights`의 그날 값 그대로다."""
    rng = np.random.default_rng(11)
    n = 300
    close = pd.Series(20_000.0 * np.cumprod(1.0 + rng.normal(0.001, 0.03, n)))
    times = pd.Series(T0 + DAY * np.arange(n), dtype="int64")
    t = tsmom.target_from_daily_closes(BTC, times, close)
    row = plan_rebalance(t, price=float(close.iloc[-1]), equity_before=10_000.0, holding=None)
    returns = close.pct_change()
    expected = tsmom.tsmom_weights((1.0 + returns.fillna(0.0)).cumprod(), returns).iloc[-1]
    assert row.weight == expected
    assert row.target_notional == pytest.approx(10_000.0 * tsmom.notional_fraction(expected))
