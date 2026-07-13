"""공용 체결 비용 모델 테스트 (WAN-37).

`common.costs.CostModel`의 메이커/테이커 수수료·슬리피지 산식과, 그 모델이 페이퍼
(`paper.store.build_record`)와 백테스트(`backtest.engine`)에 **동일하게** 적용돼
같은 진입/청산에 대해 같은 순손익을 내는지(패리티) 검증한다.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, ExitReason
from common.costs import CostModel, Liquidity
from data.models import FundingRate
from live.paper import ClosedTrade, PaperPosition
from paper.store import build_record
from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockSignal,
    SignalExitReason,
)

_STEP = 3_600_000  # 1h in ms


# --------------------------------------------------------------------------- #
# CostModel 단위
# --------------------------------------------------------------------------- #


def test_fee_rate_distinguishes_maker_and_taker() -> None:
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_bps=5.0)
    assert model.fee_rate(Liquidity.TAKER) == 0.0004
    assert model.fee_rate(Liquidity.MAKER) == 0.0002
    assert model.fee(1_000.0, Liquidity.TAKER) == pytest.approx(0.4)
    assert model.fee(1_000.0, Liquidity.MAKER) == pytest.approx(0.2)


def test_slippage_applies_to_taker_only() -> None:
    model = CostModel(slippage_bps=5.0)
    assert model.slippage_fraction == pytest.approx(0.0005)
    assert model.slippage_for(Liquidity.TAKER) == pytest.approx(0.0005)
    assert model.slippage_for(Liquidity.MAKER) == 0.0
    # 메이커(지정가) 체결은 참조가 그대로, 테이커는 불리하게 미끄러진다.
    assert model.entry_fill(100.0, is_long=True, liquidity=Liquidity.MAKER) == 100.0
    assert model.entry_fill(100.0, is_long=True, liquidity=Liquidity.TAKER) == pytest.approx(100.05)
    assert model.entry_fill(100.0, is_long=False, liquidity=Liquidity.TAKER) == pytest.approx(99.95)
    assert model.exit_fill(100.0, is_long=True, liquidity=Liquidity.TAKER) == pytest.approx(99.95)
    assert model.exit_fill(100.0, is_long=False, liquidity=Liquidity.TAKER) == pytest.approx(100.05)


def test_trade_costs_breakdown_components_are_nonnegative_and_sum() -> None:
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_bps=10.0)
    bd = model.trade_costs(
        100.0,
        110.0,
        is_long=True,
        entry_liquidity=Liquidity.TAKER,
        exit_liquidity=Liquidity.TAKER,
    )
    assert bd.gross_frac == pytest.approx(0.10)  # (110-100)/100 원가격 손익
    assert bd.slippage_frac > 0.0  # 테이커 진입·청산 슬리피지
    assert bd.fee_frac > 0.0
    assert bd.net_frac == pytest.approx(bd.gross_frac - bd.slippage_frac - bd.fee_frac)


def test_trade_costs_maker_entry_has_no_entry_slippage() -> None:
    """지정가(메이커) 진입은 진입 슬리피지가 없어 테이커 진입보다 순손익이 높다."""
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0004, slippage_bps=10.0)
    taker = model.trade_costs(
        100.0, 110.0, is_long=True, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )
    maker = model.trade_costs(
        100.0, 110.0, is_long=True, entry_liquidity=Liquidity.MAKER, exit_liquidity=Liquidity.TAKER
    )
    assert maker.slippage_frac < taker.slippage_frac
    assert maker.net_frac > taker.net_frac


# --------------------------------------------------------------------------- #
# build_record — 각 비용이 개별적으로 반영되는지
# --------------------------------------------------------------------------- #


def _closed_long(entry: float = 100.0, exit_price: float = 110.0) -> ClosedTrade:
    return ClosedTrade(
        position=PaperPosition(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=1_000,
            entry_price=entry,
            stop_price=95.0,
        ),
        exit_time=2_000,
        exit_price=exit_price,
        reason=SignalExitReason.TAKE_PROFIT,
    )


def test_build_record_reflects_fee_slippage_funding_individually() -> None:
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_bps=5.0)
    funding = [FundingRate(symbol="BTC/USDT:USDT", funding_time=1_500, rate=0.0001)]
    rec = build_record(
        _closed_long(),
        cost_model=model,
        entry_liquidity=Liquidity.TAKER,
        exit_liquidity=Liquidity.TAKER,
        funding_rates=funding,
    )
    # 개별 비용이 각각 잡힌다.
    assert rec.gross_pct == pytest.approx(10.0)
    assert rec.fee_pct > 0.0
    assert rec.slippage_pct > 0.0
    assert rec.funding_pct == pytest.approx(0.01)  # 1.0 × 0.0001 × 100 (롱 지불)
    # net = gross − fee − slippage − funding.
    assert rec.net_pct == pytest.approx(
        rec.gross_pct - rec.fee_pct - rec.slippage_pct - rec.funding_pct
    )
    assert rec.net_pct < rec.gross_pct  # 비용이 순손익을 깎는다


def test_build_record_maker_entry_beats_taker_entry() -> None:
    """지정가(메이커) 진입 기록이 시장가(테이커) 진입보다 슬리피지가 적어 유리하다."""
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0004, slippage_bps=10.0)
    taker = build_record(_closed_long(), cost_model=model, entry_liquidity=Liquidity.TAKER)
    maker = build_record(_closed_long(), cost_model=model, entry_liquidity=Liquidity.MAKER)
    assert maker.slippage_pct < taker.slippage_pct
    assert maker.net_pct > taker.net_pct


def test_build_record_legacy_fee_rate_path_unchanged() -> None:
    """cost_model 미지정 시 레거시(왕복 fee_rate·슬리피지 0) 동작을 보존한다."""
    rec = build_record(_closed_long(), fee_rate=0.0004)
    assert rec.gross_pct == pytest.approx(10.0)
    assert rec.fee_pct == pytest.approx(0.08)  # 2 × 0.0004 × 100
    assert rec.slippage_pct == 0.0
    assert rec.net_pct == pytest.approx(9.92)


# --------------------------------------------------------------------------- #
# 페이퍼 ↔ 백테스트 패리티: 동일 진입/청산 → 동일 순손익
# --------------------------------------------------------------------------- #


def _signal(direction: OrderBlockDirection, price: float) -> OrderBlockSignal:
    ob = OrderBlock(
        direction=direction,
        top=price * 1.01,
        bottom=price * 0.99,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    return OrderBlockSignal(
        direction=direction, trigger_time=0, price=price, order_block=ob, status="active"
    )


def _make_df(closes: list[float]) -> pd.DataFrame:
    # 손절·익절에 닿지 않는 완만한 봉들. 마지막 봉 종가로 강제 청산(END_OF_DATA)된다.
    rows = [(c, c + 0.5, c - 0.5, c, 10.0) for c in closes]
    return pd.DataFrame(
        {
            "open_time": [i * _STEP for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [r[4] for r in rows],
        }
    )


def _parity_check(
    direction: OrderBlockDirection,
    *,
    entry: float,
    exit_close: float,
    cfg: BacktestConfig,
    entry_liquidity: Liquidity,
    funding: list[FundingRate] | None,
) -> None:
    """같은 진입/청산·같은 비용 모델에서 백테스트와 페이퍼 순손익률이 일치하는지."""
    # 진입가에 닿게 첫 봉을 entry로, 이후 완만히 이동해 마지막 봉 종가에서 강제 청산.
    df = _make_df([entry, (entry + exit_close) / 2.0, exit_close])
    result = run_backtest(df, [_signal(direction, entry)], cfg, funding)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exits[-1].reason is ExitReason.END_OF_DATA

    is_long = direction is OrderBlockDirection.BULLISH
    closed = ClosedTrade(
        position=PaperPosition(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=direction,
            entry_time=0,
            entry_price=entry,
        ),
        exit_time=2 * _STEP,
        exit_price=exit_close,
        reason=SignalExitReason.TAKE_PROFIT if is_long else SignalExitReason.STOP_LOSS,
    )
    rec = build_record(
        closed,
        cost_model=cfg.cost_model,
        entry_liquidity=entry_liquidity,
        exit_liquidity=Liquidity.TAKER,
        funding_rates=funding,
    )

    # 백테스트 실현손익을 진입 원(raw) 노셔널로 정규화하면 페이퍼 net_pct와 같아야 한다
    # (net cash는 노셔널 기준에 무관하므로 정확히 일치).
    raw_notional = entry * trade.quantity
    backtest_net_pct = trade.realized_pnl / raw_notional * 100.0
    assert math.isclose(rec.net_pct, backtest_net_pct, rel_tol=1e-9, abs_tol=1e-9)


def test_parity_taker_fees_and_slippage_long() -> None:
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0004,
        maker_fee_rate=0.0002,
        slippage=0.0005,
        position_fraction=1.0,
        entry_liquidity=Liquidity.TAKER,
    )
    _parity_check(
        OrderBlockDirection.BULLISH,
        entry=100.0,
        exit_close=108.0,
        cfg=cfg,
        entry_liquidity=Liquidity.TAKER,
        funding=None,
    )


def test_parity_taker_fees_and_slippage_short() -> None:
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0004,
        slippage=0.0005,
        position_fraction=1.0,
        entry_liquidity=Liquidity.TAKER,
    )
    _parity_check(
        OrderBlockDirection.BEARISH,
        entry=100.0,
        exit_close=94.0,
        cfg=cfg,
        entry_liquidity=Liquidity.TAKER,
        funding=None,
    )


def test_parity_with_funding_matches_when_no_slippage() -> None:
    """슬리피지 0(진입 체결가=참조가)이면 펀딩까지 포함해 순손익이 정확히 일치한다."""
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0004,
        slippage=0.0,
        position_fraction=1.0,
        entry_liquidity=Liquidity.TAKER,
        funding_enabled=True,
    )
    funding = [
        FundingRate(symbol="BTC/USDT:USDT", funding_time=_STEP, rate=0.0002),
        FundingRate(symbol="BTC/USDT:USDT", funding_time=2 * _STEP - 1, rate=-0.0001),
    ]
    _parity_check(
        OrderBlockDirection.BULLISH,
        entry=100.0,
        exit_close=106.0,
        cfg=cfg,
        entry_liquidity=Liquidity.TAKER,
        funding=funding,
    )
