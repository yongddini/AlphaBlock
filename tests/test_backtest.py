"""backtest 엔진·지표에 대한 테스트.

손으로 계산한 고정 시나리오에 대해 손익·자본곡선·성과 지표가 기대값과
일치하는지 검증한다(수수료·슬리피지 포함). 지표 순수 함수(MDD·샤프)는
알려진 수열로 직접 검증한다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from backtest import (
    BacktestConfig,
    ExitReason,
    PositionSide,
    format_summary,
    max_drawdown,
    run_backtest,
    sharpe_ratio,
    summary_dict,
    trades_to_dataframe,
    write_equity_csv,
    write_trades_csv,
)
from backtest.metrics import bar_returns
from data.models import FundingRate
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockSignal

# 봉 간격(ms)
_STEP = 60_000


def _funding(funding_time: int, rate: float, *, is_predicted: bool = False) -> FundingRate:
    """테스트용 펀딩비 관측(심볼은 엔진이 무시하므로 형식만)."""
    return FundingRate(
        symbol="BTC/USDT:USDT",
        funding_time=funding_time,
        rate=rate,
        is_predicted=is_predicted,
    )


def _make_df(bars: Sequence[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    """(open, high, low, close, volume) 시퀀스를 OHLCV DataFrame으로."""
    return pd.DataFrame(
        {
            "open_time": [i * _STEP for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
    )


def _signal(
    direction: OrderBlockDirection,
    trigger_index: int,
    price: float,
    *,
    status: Literal["active", "cancelled"] = "active",
) -> OrderBlockSignal:
    """테스트용 최소 시그널. order_block은 형식만 갖춘 더미."""
    ob = OrderBlock(
        direction=direction,
        top=price * 1.01,
        bottom=price * 0.99,
        start_time=0,
        confirmed_time=trigger_index * _STEP,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    return OrderBlockSignal(
        direction=direction,
        trigger_time=trigger_index * _STEP,
        price=price,
        order_block=ob,
        status=status,
    )


# 진입가 100 근처, 손절(95)/익절(110)에 닿지 않는 진입·중간 봉을 공유한다.
_ENTRY_BAR = (100.0, 101.0, 99.0, 100.0, 10.0)
_MID_BAR = (100.0, 105.0, 98.0, 104.0, 10.0)


def test_long_take_profit_no_costs() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        position_fraction=1.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
    )
    result = run_backtest(df, signals, cfg)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side is PositionSide.LONG
    assert trade.quantity == pytest.approx(100.0)
    assert trade.exits[-1].reason is ExitReason.TAKE_PROFIT
    assert trade.realized_pnl == pytest.approx(1_000.0)
    assert trade.return_pct == pytest.approx(0.10)

    m = result.metrics
    assert m.final_equity == pytest.approx(11_000.0)
    assert m.total_return == pytest.approx(0.10)
    assert m.win_rate == pytest.approx(1.0)
    assert m.num_trades == 1
    assert m.max_drawdown == pytest.approx(0.0)
    assert m.profit_factor is None  # 손실 거래 없음
    # 자본곡선: 진입봉 10000 → 중간봉 미실현 400 → 익절 11000
    equities = [p.equity for p in result.equity_curve]
    assert equities == pytest.approx([10_000.0, 10_400.0, 11_000.0])


def test_fee_reduces_pnl() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.001,
        slippage=0.0,
        position_fraction=1.0,
        take_profit_pct=0.10,
        stop_loss_pct=0.05,
    )
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]
    # 진입수수료 10, 청산수수료 11 → 순손익 1000-21 = 979
    assert trade.entry_fee == pytest.approx(10.0)
    assert trade.realized_pnl == pytest.approx(979.0)
    assert result.metrics.final_equity == pytest.approx(10_979.0)


def test_slippage_reduces_pnl() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 120.0, 103.0, 115.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    slip = 0.01
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=slip,
        position_fraction=1.0,
        take_profit_pct=0.10,
    )
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]

    entry_fill = 100.0 * (1 + slip)
    qty = 10_000.0 / entry_fill
    tp_level = entry_fill * (1 + 0.10)
    exit_fill = tp_level * (1 - slip)
    expected_pnl = (exit_fill - entry_fill) * qty

    assert trade.entry_price == pytest.approx(entry_fill)
    assert trade.realized_pnl == pytest.approx(expected_pnl)
    assert trade.realized_pnl < 1_000.0  # 무비용 대비 손익 감소


def test_long_stop_loss() -> None:
    df = _make_df([_ENTRY_BAR, (100.0, 101.0, 94.0, 96.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        position_fraction=1.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
    )
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]
    assert trade.exits[-1].reason is ExitReason.STOP_LOSS
    assert trade.realized_pnl == pytest.approx(-500.0)
    m = result.metrics
    assert m.total_return == pytest.approx(-0.05)
    assert m.win_rate == pytest.approx(0.0)
    assert m.profit_factor == pytest.approx(0.0)  # 이익 0 / 손실 500
    assert m.max_drawdown == pytest.approx(0.05)


def test_short_take_profit() -> None:
    df = _make_df([_ENTRY_BAR, (100.0, 101.0, 88.0, 92.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BEARISH, 0, 100.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        position_fraction=1.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
    )
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]
    assert trade.side is PositionSide.SHORT
    assert trade.exits[-1].reason is ExitReason.TAKE_PROFIT
    assert trade.realized_pnl == pytest.approx(1_000.0)
    assert result.metrics.total_return == pytest.approx(0.10)


def test_partial_take_profit() -> None:
    df = _make_df(
        [
            _ENTRY_BAR,
            (100.0, 106.0, 101.0, 104.0, 10.0),  # 부분 익절(105) 도달, 전체(110) 미도달
            (104.0, 112.0, 103.0, 110.0, 10.0),  # 전체 익절(110) 도달
        ]
    )
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        position_fraction=1.0,
        stop_loss_pct=0.20,
        take_profit_pct=0.10,
        partial_take_profit_pct=0.05,
        partial_exit_fraction=0.5,
    )
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]
    assert len(trade.exits) == 2
    assert trade.exits[0].reason is ExitReason.PARTIAL_TAKE_PROFIT
    assert trade.exits[1].reason is ExitReason.TAKE_PROFIT
    # 50주 @105(+5) + 50주 @110(+10) = 250 + 500 = 750
    assert trade.realized_pnl == pytest.approx(750.0)
    assert trade.return_pct == pytest.approx(0.075)


def test_stop_prioritized_over_take_profit_same_bar() -> None:
    # 한 봉에서 손절(95)과 익절(110)이 모두 걸리면 손절이 우선.
    df = _make_df([_ENTRY_BAR, (100.0, 112.0, 94.0, 100.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        position_fraction=1.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
    )
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]
    assert trade.exits[-1].reason is ExitReason.STOP_LOSS
    assert trade.realized_pnl == pytest.approx(-500.0)


def test_open_position_force_closed_at_end() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 106.0, 103.0, 106.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0, fee_rate=0.0, slippage=0.0, position_fraction=1.0
    )  # 손절·익절 없음 → 데이터 끝에서 강제 청산
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]
    assert trade.exits[-1].reason is ExitReason.END_OF_DATA
    # 마지막 종가 106에 강제 청산: (106-100)*100 = 600
    assert trade.realized_pnl == pytest.approx(600.0)
    assert result.metrics.final_equity == pytest.approx(10_600.0)


def test_cancelled_signal_is_ignored() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0, status="cancelled")]
    cfg = BacktestConfig(take_profit_pct=0.10, stop_loss_pct=0.05)
    result = run_backtest(df, signals, cfg)
    assert result.trades == []
    assert result.metrics.num_trades == 0


def test_allow_short_disabled_skips_bearish() -> None:
    df = _make_df([_ENTRY_BAR, (100.0, 101.0, 88.0, 92.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BEARISH, 0, 100.0)]
    cfg = BacktestConfig(allow_short=False, take_profit_pct=0.10, stop_loss_pct=0.05)
    result = run_backtest(df, signals, cfg)
    assert result.trades == []


def test_no_signals_flat_equity() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 106.0, 103.0, 105.0, 10.0)])
    result = run_backtest(df, [], BacktestConfig(initial_capital=10_000.0))
    assert result.trades == []
    equities = [p.equity for p in result.equity_curve]
    assert equities == pytest.approx([10_000.0, 10_000.0, 10_000.0])
    m = result.metrics
    assert m.total_return == pytest.approx(0.0)
    assert m.max_drawdown == pytest.approx(0.0)
    assert m.sharpe is None  # 변동 없음 → 표준편차 0


def test_single_position_no_pyramiding() -> None:
    # 보유 중 도착한 시그널은 무시된다.
    df = _make_df(
        [
            _ENTRY_BAR,
            (100.0, 105.0, 98.0, 104.0, 10.0),  # 두 번째 시그널(무시)
            (104.0, 112.0, 103.0, 110.0, 10.0),
        ]
    )
    signals = [
        _signal(OrderBlockDirection.BULLISH, 0, 100.0),
        _signal(OrderBlockDirection.BULLISH, 1, 104.0),
    ]
    cfg = BacktestConfig(take_profit_pct=0.10, stop_loss_pct=0.05)
    result = run_backtest(df, signals, cfg)
    assert len(result.trades) == 1


def test_closed_column_filters_unconfirmed_bars() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    df["closed"] = [True, True, False]  # 마지막 봉 미확정 → 제외
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(take_profit_pct=0.10, stop_loss_pct=0.05)
    result = run_backtest(df, signals, cfg)
    # 익절 봉이 제외되어 데이터 끝(중간봉 종가 104)에서 강제 청산
    assert len(result.equity_curve) == 2
    assert result.trades[0].exits[-1].reason is ExitReason.END_OF_DATA


# --- 펀딩비 (WAN-20) ---

# 롱 익절 시나리오: 진입 bar0(open_time 0), 익절 bar2(open_time 120_000).
# 보유 구간 [0, 120_000), 진입가 100, 수량 100 → 명목가 10_000.
_LONG_TP_DF = [_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)]


def _long_tp_cfg(**kw: object) -> BacktestConfig:
    base: dict[str, object] = dict(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        position_fraction=1.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
    )
    base.update(kw)
    return BacktestConfig(**base)


def test_funding_long_pays_when_rate_positive() -> None:
    df = _make_df(_LONG_TP_DF)
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    rates = [_funding(30_000, 0.001), _funding(90_000, 0.001)]  # 구간 내 2회 정산
    result = run_backtest(df, signals, _long_tp_cfg(funding_enabled=True), rates)

    trade = result.trades[0]
    # 명목가 10_000 × 0.001 × 2회 = 20 (롱은 rate>0에서 지불)
    assert trade.funding_cost == pytest.approx(20.0)
    assert trade.realized_pnl == pytest.approx(980.0)  # 1000 - 20
    assert result.metrics.total_funding_cost == pytest.approx(20.0)
    assert result.metrics.final_equity == pytest.approx(10_980.0)


def test_funding_disabled_ignores_rates() -> None:
    df = _make_df(_LONG_TP_DF)
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    rates = [_funding(30_000, 0.001), _funding(90_000, 0.001)]
    result = run_backtest(df, signals, _long_tp_cfg(funding_enabled=False), rates)

    trade = result.trades[0]
    assert trade.funding_cost == pytest.approx(0.0)
    assert trade.realized_pnl == pytest.approx(1_000.0)
    assert result.metrics.total_funding_cost == pytest.approx(0.0)


def test_funding_short_receives_when_rate_positive() -> None:
    df = _make_df([_ENTRY_BAR, (100.0, 101.0, 88.0, 92.0, 10.0)])  # 숏 익절 bar1
    signals = [_signal(OrderBlockDirection.BEARISH, 0, 100.0)]
    rates = [_funding(30_000, 0.001)]  # 보유 [0, 60_000)
    result = run_backtest(df, signals, _long_tp_cfg(funding_enabled=True), rates)

    trade = result.trades[0]
    assert trade.side is PositionSide.SHORT
    # 숏은 부호 반대 → 10_000 × 0.001 × (-1) = -10 (수취)
    assert trade.funding_cost == pytest.approx(-10.0)
    assert trade.realized_pnl == pytest.approx(1_010.0)  # 1000 - (-10)


def test_funding_partial_exit_scales_notional() -> None:
    df = _make_df(
        [
            _ENTRY_BAR,
            (100.0, 106.0, 101.0, 104.0, 10.0),  # 부분 익절(105) bar1
            (104.0, 112.0, 103.0, 110.0, 10.0),  # 전체 익절(110) bar2
        ]
    )
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = _long_tp_cfg(
        funding_enabled=True,
        stop_loss_pct=0.20,
        partial_take_profit_pct=0.05,
        partial_exit_fraction=0.5,
    )
    rates = [_funding(30_000, 0.001), _funding(90_000, 0.001)]
    result = run_backtest(df, signals, cfg, rates)

    trade = result.trades[0]
    # [0,60_000) 수량 100 → 10_000×0.001=10, [60_000,120_000) 수량 50 → 5_000×0.001=5
    assert trade.funding_cost == pytest.approx(15.0)
    assert trade.realized_pnl == pytest.approx(735.0)  # 750 - 15


def test_funding_entry_inclusive_exit_exclusive() -> None:
    df = _make_df(_LONG_TP_DF)
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    # 진입시각(0)의 펀딩은 포함, 청산시각(120_000)의 펀딩은 제외.
    rates = [_funding(0, 0.001), _funding(120_000, 0.005)]
    result = run_backtest(df, signals, _long_tp_cfg(funding_enabled=True), rates)

    trade = result.trades[0]
    assert trade.funding_cost == pytest.approx(10.0)  # 0시각 1회만 반영


def test_funding_missing_policy_error_raises() -> None:
    df = _make_df(_LONG_TP_DF)
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = _long_tp_cfg(funding_enabled=True, funding_missing_policy="error")
    with pytest.raises(ValueError, match="펀딩비 데이터가 없습니다"):
        run_backtest(df, signals, cfg)  # funding_rates 미전달


def test_funding_missing_policy_zero_proceeds() -> None:
    df = _make_df(_LONG_TP_DF)
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = _long_tp_cfg(funding_enabled=True, funding_missing_policy="zero")
    result = run_backtest(df, signals, cfg)  # 데이터 없음 → 0으로 진행

    assert result.trades[0].funding_cost == pytest.approx(0.0)
    assert result.trades[0].realized_pnl == pytest.approx(1_000.0)


def test_funding_predicted_excluded_by_default_included_when_enabled() -> None:
    df = _make_df(_LONG_TP_DF)
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    rates = [_funding(30_000, 0.001, is_predicted=True)]

    excluded = run_backtest(df, signals, _long_tp_cfg(funding_enabled=True), rates)
    assert excluded.trades[0].funding_cost == pytest.approx(0.0)

    included = run_backtest(
        df, signals, _long_tp_cfg(funding_enabled=True, funding_include_predicted=True), rates
    )
    assert included.trades[0].funding_cost == pytest.approx(10.0)


# --- 펀딩 커버리지 · 조용한 실패 방지 (WAN-63) ---

_EIGHT_H = 8 * 3_600_000


def _wide_df() -> pd.DataFrame:
    """진입(t=0)부터 마지막 봉(t=3*8h)까지 청산 없이 보유하는 2봉 프레임.

    커버리지 창 `[0, 3*8h)` 안에 8h 정산 경계가 3개(0, 8h, 16h) 있으므로,
    공급하는 펀딩 개수로 커버리지(0/3·2/3·3/3)를 정확히 만들 수 있다.
    """
    return pd.DataFrame(
        {
            "open_time": [0, 3 * _EIGHT_H],
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [10.0, 10.0],
        }
    )


def _wide_cfg(**kw: object) -> BacktestConfig:
    base: dict[str, object] = {
        "initial_capital": 10_000.0,
        "fee_rate": 0.0,
        "slippage": 0.0,
        "position_fraction": 1.0,
        "funding_enabled": True,
    }
    base.update(kw)
    return BacktestConfig(**base)


def test_funding_missing_data_warns_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    """펀딩 데이터가 전무한 구간을 0으로 때울 때 조용히 넘어가지 않고 경고를 남긴다."""
    df = _wide_df()
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    with caplog.at_level(logging.WARNING):
        result = run_backtest(df, signals, _wide_cfg())  # 펀딩 데이터 미전달
    assert result.metrics.funding_coverage == pytest.approx(0.0)
    assert result.trades[0].funding_cost == pytest.approx(0.0)  # 0으로 진행하되
    assert any("커버리지" in r.message for r in caplog.records)  # 소리는 낸다


def test_funding_partial_coverage_warns_and_reports(caplog: pytest.LogCaptureFixture) -> None:
    """일부만 있는 커버리지(2/3)를 지표에 노출하고 경고한다."""
    df = _wide_df()
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    rates = [_funding(0, 0.0), _funding(_EIGHT_H, 0.0)]  # 3개 중 2개(16h 결측)
    with caplog.at_level(logging.WARNING):
        result = run_backtest(df, signals, _wide_cfg(), rates)
    assert result.metrics.funding_coverage == pytest.approx(2 / 3)
    assert any("커버리지" in r.message for r in caplog.records)


def test_funding_strict_costs_raises_on_partial_coverage() -> None:
    """strict(error) 정책에서 커버리지가 100% 미만이면 실행을 중단한다."""
    df = _wide_df()
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    rates = [_funding(0, 0.0), _funding(_EIGHT_H, 0.0)]  # 커버리지 2/3 < 1.0
    cfg = _wide_cfg(funding_missing_policy="error")
    with pytest.raises(ValueError, match="커버리지"):
        run_backtest(df, signals, cfg, rates)


def test_funding_full_coverage_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """커버리지가 완전(3/3)하면 경고 없이 1.0을 보고한다."""
    df = _wide_df()
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    rates = [_funding(0, 0.0), _funding(_EIGHT_H, 0.0), _funding(2 * _EIGHT_H, 0.0)]
    with caplog.at_level(logging.WARNING):
        result = run_backtest(df, signals, _wide_cfg(), rates)
    assert result.metrics.funding_coverage == pytest.approx(1.0)
    assert not any("커버리지" in r.message for r in caplog.records)


# --- 리스크 기반 포지션 사이징 (WAN-26) ---


def _signal_ob(
    direction: OrderBlockDirection,
    trigger_index: int,
    price: float,
    *,
    top: float,
    bottom: float,
) -> OrderBlockSignal:
    """손절 참조가(오더블록 distal 경계)를 명시적으로 지정하는 시그널."""
    ob = OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=trigger_index * _STEP,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    return OrderBlockSignal(
        direction=direction,
        trigger_time=trigger_index * _STEP,
        price=price,
        order_block=ob,
    )


def test_risk_sizing_scales_quantity_by_stop_distance() -> None:
    from execution import PositionSizingParams

    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    # 롱: 진입가 100, 오더블록 distal(bottom)=90 → 손절 거리 10.
    signals = [_signal_ob(OrderBlockDirection.BULLISH, 0, 100.0, top=101.0, bottom=90.0)]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        take_profit_pct=0.10,
        risk_sizing=PositionSizingParams(risk_per_trade=0.01, leverage=100.0),
    )
    result = run_backtest(df, signals, cfg)
    trade = result.trades[0]
    # 리스크 = 10_000 × 0.01 = 100, 손절 거리 10 → 수량 10.
    assert trade.quantity == pytest.approx(10.0)
    # 익절 110 도달 → (110-100)×10 = 100.
    assert trade.realized_pnl == pytest.approx(100.0)
    assert trade.return_pct == pytest.approx(0.10)


def test_risk_sizing_differs_from_fixed_fraction() -> None:
    from execution import PositionSizingParams

    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal_ob(OrderBlockDirection.BULLISH, 0, 100.0, top=101.0, bottom=90.0)]
    base = dict(initial_capital=10_000.0, fee_rate=0.0, slippage=0.0, take_profit_pct=0.10)

    fixed = run_backtest(df, signals, BacktestConfig(position_fraction=1.0, **base))
    risk = run_backtest(
        df,
        signals,
        BacktestConfig(
            risk_sizing=PositionSizingParams(risk_per_trade=0.01, leverage=100.0), **base
        ),
    )
    # 고정 사이징: 수량 100(전 자본). 리스크 사이징: 수량 10. 성과는 비교 가능하게 산출된다.
    assert fixed.trades[0].quantity == pytest.approx(100.0)
    assert risk.trades[0].quantity == pytest.approx(10.0)
    assert risk.trades[0].realized_pnl == pytest.approx(fixed.trades[0].realized_pnl / 10.0)


def test_risk_sizing_skips_entry_when_stop_too_close() -> None:
    from execution import PositionSizingParams

    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    # 손절 거리 0.1(bottom=99.9) < 최소 2% → 진입 스킵.
    signals = [_signal_ob(OrderBlockDirection.BULLISH, 0, 100.0, top=101.0, bottom=99.9)]
    cfg = BacktestConfig(
        take_profit_pct=0.10,
        risk_sizing=PositionSizingParams(min_stop_distance_fraction=0.02),
    )
    result = run_backtest(df, signals, cfg)
    assert result.trades == []
    assert result.metrics.num_trades == 0


def test_risk_sizing_none_warns_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    """risk_sizing=None(전액 진입 모드)이면 조용히 넘어가지 않고 경고를 남긴다(WAN-65)."""
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(take_profit_pct=0.10)  # risk_sizing 기본값 None
    with caplog.at_level(logging.WARNING):
        run_backtest(df, signals, cfg)
    assert any("risk_sizing" in r.message and "전액 진입" in r.message for r in caplog.records)


def test_risk_sizing_set_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """risk_sizing이 설정돼 있으면 전액 진입 경고를 내지 않는다."""
    from execution import PositionSizingParams

    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal_ob(OrderBlockDirection.BULLISH, 0, 100.0, top=101.0, bottom=90.0)]
    cfg = BacktestConfig(
        take_profit_pct=0.10, risk_sizing=PositionSizingParams(risk_per_trade=0.01)
    )
    with caplog.at_level(logging.WARNING):
        run_backtest(df, signals, cfg)
    assert not any("전액 진입" in r.message for r in caplog.records)


# --- 지표 순수 함수 ---


def test_max_drawdown_known_series() -> None:
    assert max_drawdown([100, 120, 90, 110, 80]) == pytest.approx(40 / 120)
    assert max_drawdown([100, 110, 120]) == pytest.approx(0.0)
    assert max_drawdown([]) == pytest.approx(0.0)


def test_bar_returns() -> None:
    assert bar_returns([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])
    assert bar_returns([100.0]) == []


def test_sharpe_ratio() -> None:
    assert sharpe_ratio([100.0]) is None  # 표본 부족
    assert sharpe_ratio([100.0, 100.0, 100.0]) is None  # 표준편차 0
    sharpe = sharpe_ratio([100.0, 110.0, 121.0])  # 일정 수익률 → std 0
    assert sharpe is None
    varied = sharpe_ratio([100.0, 110.0, 105.0, 120.0])
    assert varied is not None


def test_annualized_sharpe_scales() -> None:
    equities = [100.0, 110.0, 105.0, 120.0, 118.0]
    base = sharpe_ratio(equities)
    annual = sharpe_ratio(equities, annualization_factor=4.0)
    assert base is not None and annual is not None
    assert annual == pytest.approx(base * 2.0)  # sqrt(4) = 2


# --- 리포트 ---


def test_report_dataframes_and_summary() -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(take_profit_pct=0.10, stop_loss_pct=0.05)
    result = run_backtest(df, signals, cfg)

    trades_df = trades_to_dataframe(result)
    assert len(trades_df) == 1
    assert "realized_pnl" in trades_df.columns

    summary = summary_dict(result)
    assert summary["num_trades"] == 1
    assert "seed" in summary

    text = format_summary(result)
    assert "Total Return" in text
    assert "Params" in text


def test_summary_reports_sizing_mode() -> None:
    """WAN-65: 요약·리포트 텍스트에 사이징 방식이 드러나고, 전액 진입이면 배너가 뜬다."""
    from backtest.report import sizing_mode_banner
    from execution import PositionSizingParams

    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]

    unsized = run_backtest(df, signals, BacktestConfig(take_profit_pct=0.10))
    summary = summary_dict(unsized)
    assert summary["sizing_mode"] == "full_position"
    assert summary["risk_per_trade"] is None
    assert "sizing_mode=full_position" in format_summary(unsized)
    assert sizing_mode_banner(unsized) is not None

    sized_cfg = BacktestConfig(
        take_profit_pct=0.10, risk_sizing=PositionSizingParams(risk_per_trade=0.02)
    )
    sized = run_backtest(df, signals, sized_cfg)
    summary = summary_dict(sized)
    assert summary["sizing_mode"] == "risk_sizing"
    assert summary["risk_per_trade"] == pytest.approx(0.02)
    assert sizing_mode_banner(sized) is None


def test_reports_carry_entry_mode_rsi_mode_combine_obs() -> None:
    """WAN-65: 거래/요약 리포트에 진입 방식·RSI 모드·병합 여부가 함께 기록된다.

    이 컬럼들이 없으면 CSV 파일만 봐서는 A안/B안인지, 병합이 켜졌는지 알 수 없다
    (WAN-47/56/59/63과 동일 패턴의 재발 방지).
    """
    from strategy.models import ConfluenceParams, OrderBlockParams

    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    result = run_backtest(df, signals, BacktestConfig(take_profit_pct=0.10))
    conf = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    ob = OrderBlockParams(combine_obs=False)

    summary = summary_dict(result, confluence=conf, order_block=ob)
    assert summary["entry_mode"] == "zone_limit"
    assert summary["rsi_mode"] == "realtime"
    assert summary["combine_obs"] is False

    text = format_summary(result, confluence=conf, order_block=ob)
    assert "entry_mode=zone_limit" in text
    assert "combine_obs=False" in text

    trades_df = trades_to_dataframe(result, confluence=conf, order_block=ob)
    assert (trades_df["entry_mode"] == "zone_limit").all()
    assert (trades_df["combine_obs"] == False).all()  # noqa: E712

    # 명시하지 않으면 기본값(A안·병합 ON)으로 채워지되 컬럼 자체는 항상 존재한다.
    default_summary = summary_dict(result)
    assert default_summary["entry_mode"] == "close"
    assert default_summary["combine_obs"] is True


def test_csv_writers(tmp_path: Path) -> None:
    df = _make_df([_ENTRY_BAR, _MID_BAR, (104.0, 112.0, 103.0, 110.0, 10.0)])
    signals = [_signal(OrderBlockDirection.BULLISH, 0, 100.0)]
    cfg = BacktestConfig(fee_rate=0.0, slippage=0.0, take_profit_pct=0.10, stop_loss_pct=0.05)
    result = run_backtest(df, signals, cfg)

    trades_path = write_trades_csv(result, tmp_path / "trades.csv")
    equity_path = write_equity_csv(result, tmp_path / "equity.csv")
    assert trades_path.exists()
    assert equity_path.exists()

    loaded = pd.read_csv(trades_path)
    assert len(loaded) == 1
    assert loaded["realized_pnl"].iloc[0] == pytest.approx(1_000.0)


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError):
        BacktestConfig(allow_long=False, allow_short=False)
    with pytest.raises(ValueError):
        BacktestConfig(take_profit_pct=0.05, partial_take_profit_pct=0.05)


def test_missing_columns_raise() -> None:
    bad = pd.DataFrame({"open_time": [0], "open": [1.0]})
    with pytest.raises(ValueError, match="필요한 컬럼"):
        run_backtest(bad, [])
