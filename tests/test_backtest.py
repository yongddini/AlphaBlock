"""backtest 엔진·지표에 대한 테스트.

손으로 계산한 고정 시나리오에 대해 손익·자본곡선·성과 지표가 기대값과
일치하는지 검증한다(수수료·슬리피지 포함). 지표 순수 함수(MDD·샤프)는
알려진 수열로 직접 검증한다.
"""

from __future__ import annotations

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
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockSignal

# 봉 간격(ms)
_STEP = 60_000


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
