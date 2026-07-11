"""paper.store 테스트 — 거래 영속화·손익 산출·기록 싱크 (WAN-33)."""

from __future__ import annotations

import math

from data.models import FundingRate
from live.paper import ClosedTrade, PaperPosition
from paper.store import (
    PaperTradeRecord,
    PaperTradeRecorder,
    PaperTradeStore,
    build_record,
)
from strategy.models import OrderBlockDirection, SignalExitReason


def _closed_long_trade() -> ClosedTrade:
    """진입 100 → 익절 110, 손절 참조가 95(리스크 5%)인 롱 거래."""
    return ClosedTrade(
        position=PaperPosition(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=1000,
            entry_price=100.0,
            stop_price=95.0,
            take_profit_price=110.0,
        ),
        exit_time=2000,
        exit_price=110.0,
        reason=SignalExitReason.TAKE_PROFIT,
    )


def test_build_record_pnl_fee_funding_and_r() -> None:
    funding = [
        FundingRate(symbol="BTC/USDT:USDT", funding_time=1500, rate=0.0001, is_predicted=False)
    ]
    record = build_record(_closed_long_trade(), fee_rate=0.0004, funding_rates=funding)

    assert record.gross_pct == 10.0  # (110-100)/100
    assert record.fee_pct == 0.08  # 왕복 2 × 0.0004 × 100
    assert record.funding_pct == 0.01  # 1.0 × 0.0001 × 100 (롱 지불)
    assert math.isclose(record.net_pct, 9.91, rel_tol=1e-9)
    assert record.risk_pct == 5.0  # |100-95|/100
    assert math.isclose(record.r_multiple or 0.0, 9.91 / 5.0, rel_tol=1e-9)
    assert record.is_win is True


def test_build_record_without_stop_has_no_r() -> None:
    trade = ClosedTrade(
        position=PaperPosition(
            symbol="ETH/USDT:USDT",
            timeframe="4h",
            direction=OrderBlockDirection.BEARISH,
            entry_time=1000,
            entry_price=200.0,
        ),
        exit_time=2000,
        exit_price=220.0,  # 숏이 상승 → 손실
        reason=SignalExitReason.STOP_LOSS,
    )
    record = build_record(trade, fee_rate=0.0)
    assert record.gross_pct == -10.0  # 숏은 상승이 손실
    assert record.funding_pct == 0.0  # 펀딩 데이터 없음
    assert record.risk_pct is None
    assert record.r_multiple is None
    assert record.is_win is False


def test_store_upsert_is_idempotent_and_lists() -> None:
    record = build_record(_closed_long_trade(), fee_rate=0.0004)
    with PaperTradeStore(":memory:") as store:
        store.upsert_record(record)
        store.upsert_record(record)  # 같은 키 → 갱신, 중복 아님
        assert store.count() == 1
        assert store.count(symbol="BTC/USDT:USDT", timeframe="1h") == 1
        assert store.list_series() == [("BTC/USDT:USDT", "1h")]
        loaded = store.list_records()
        assert len(loaded) == 1
        assert loaded[0] == record
        assert store.time_span() == (1000, 2000)


def test_store_list_records_filters_by_entry_time() -> None:
    early = build_record(_closed_long_trade(), fee_rate=0.0)
    late = build_record(
        ClosedTrade(
            position=PaperPosition(
                symbol="BTC/USDT:USDT",
                timeframe="1h",
                direction=OrderBlockDirection.BULLISH,
                entry_time=5000,
                entry_price=100.0,
                stop_price=95.0,
            ),
            exit_time=6000,
            exit_price=90.0,
            reason=SignalExitReason.STOP_LOSS,
        ),
        fee_rate=0.0,
    )
    with PaperTradeStore(":memory:") as store:
        store.upsert_record(early)
        store.upsert_record(late)
        assert [r.entry_time for r in store.list_records()] == [1000, 5000]
        assert [r.entry_time for r in store.list_records(start_ms=2000)] == [5000]
        assert [r.entry_time for r in store.list_records(end_ms=2000)] == [1000]


def test_store_empty_span_is_none() -> None:
    with PaperTradeStore(":memory:") as store:
        assert store.time_span() is None
        assert store.list_records() == []


def test_recorder_persists_closed_trade() -> None:
    with PaperTradeStore(":memory:") as store:
        recorder = PaperTradeRecorder(store, fee_rate=0.0004)
        record = recorder.record(_closed_long_trade())
        assert isinstance(record, PaperTradeRecord)
        assert store.count() == 1
        assert store.list_records()[0].net_pct == record.net_pct
