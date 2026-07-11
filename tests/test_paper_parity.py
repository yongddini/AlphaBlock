"""paper.parity 테스트 — 페이퍼 vs 백테스트 패리티 (WAN-33)."""

from __future__ import annotations

import pandas as pd

from backtest.synthetic import make_synthetic_ohlcv
from config.settings import Settings
from paper.parity import (
    ParityThresholds,
    backtest_trade_stats,
    build_parity_report,
    build_parity_row,
    run_series_backtest,
)
from paper.performance import TradeStat
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.models import OrderBlockDirection, SignalExitReason

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"


def _record(net_pct: float, r: float | None, entry_time: int) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=entry_time + 100,
        exit_price=100.0 + net_pct,
        reason=SignalExitReason.TAKE_PROFIT,
        gross_pct=net_pct,
        fee_pct=0.0,
        funding_pct=0.0,
        net_pct=net_pct,
        risk_pct=None if r is None else abs(net_pct / r),
        r_multiple=r,
    )


def test_build_parity_row_flags_on_win_rate_gap() -> None:
    paper = [_record(2.0, 2.0, 1000), _record(1.0, 1.0, 2000)]  # 승률 100%, 평균 R 1.5
    backtest = [
        TradeStat(net_pct=2.0, r_multiple=2.0, exit_time=1100),
        TradeStat(net_pct=-1.0, r_multiple=-1.0, exit_time=2100),  # 승률 50%, 평균 R 0.5
    ]
    row = build_parity_row(
        symbol=_SYMBOL,
        timeframe=_TF,
        paper_records=paper,
        backtest_stats=backtest,
        thresholds=ParityThresholds(),
    )

    assert row.paper.num_trades == 2
    assert row.backtest.num_trades == 2
    assert row.trade_count_diff == 0
    assert row.win_rate_diff == 0.5  # 1.0 - 0.5
    assert row.avg_r_diff == 1.0  # 1.5 - 0.5
    assert row.flagged is True  # 승률·R 차이가 임계값 초과


def test_build_parity_row_not_flagged_when_close() -> None:
    paper = [_record(2.0, 2.0, 1000), _record(-1.0, -1.0, 2000)]
    backtest = [
        TradeStat(net_pct=2.0, r_multiple=2.0, exit_time=1100),
        TradeStat(net_pct=-1.0, r_multiple=-1.0, exit_time=2100),
    ]
    row = build_parity_row(
        symbol=_SYMBOL,
        timeframe=_TF,
        paper_records=paper,
        backtest_stats=backtest,
        thresholds=ParityThresholds(),
    )
    assert row.trade_count_diff == 0
    assert row.win_rate_diff == 0.0
    assert row.avg_r_diff == 0.0
    assert row.flagged is False


def test_avg_r_diff_none_when_one_side_missing() -> None:
    paper = [_record(1.0, None, 1000)]  # R 없음
    backtest = [TradeStat(net_pct=1.0, r_multiple=1.0, exit_time=1100)]
    row = build_parity_row(
        symbol=_SYMBOL,
        timeframe=_TF,
        paper_records=paper,
        backtest_stats=backtest,
        thresholds=ParityThresholds(),
    )
    assert row.paper.avg_r is None
    assert row.avg_r_diff is None


def test_backtest_trade_stats_matches_result() -> None:
    df = make_synthetic_ohlcv(symbol=_SYMBOL, timeframe=_TF, bars=600, seed=3)
    from backtest.models import BacktestConfig

    result, signals = run_series_backtest(
        df,
        confluence_params=None,
        order_block_params=None,
        backtest_config=BacktestConfig(slippage=0.0),
        funding_rates=None,
    )
    stats = backtest_trade_stats(result, signals)
    assert len(stats) == len(result.trades)
    for stat, trade in zip(stats, result.trades, strict=True):
        assert stat.net_pct == trade.return_pct * 100.0
        assert stat.exit_time == trade.exit_time


def test_build_parity_report_end_to_end(tmp_path: object) -> None:
    db_path = str(tmp_path / "paper.db")  # type: ignore[operator]
    with PaperTradeStore(db_path) as store:
        store.upsert_record(_record(2.0, 2.0, 1000))
        store.upsert_record(_record(-1.0, -1.0, 2000))

    df = make_synthetic_ohlcv(symbol=_SYMBOL, timeframe=_TF, bars=600, seed=3)

    def loader(
        symbol: str, timeframe: str, start_ms: int | None, end_ms: int | None
    ) -> pd.DataFrame:
        assert symbol == _SYMBOL and timeframe == _TF
        return df

    report = build_parity_report(
        db_path,
        settings=Settings(funding_enabled=False),
        series=[(_SYMBOL, _TF)],
        ohlcv_loader=loader,
    )

    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.symbol == _SYMBOL
    assert row.paper.num_trades == 2  # 저장한 페이퍼 거래 수
    assert isinstance(row.flagged, bool)
    # 백테스트가 재실행되어 지표가 채워진다(거래가 0일 수도 있으나 타입은 유효).
    assert row.backtest.num_trades >= 0
    assert list(report.to_dataframe().columns)  # DataFrame 변환 동작
