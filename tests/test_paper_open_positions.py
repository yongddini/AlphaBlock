"""paper.store 열린 포지션 영속·복구 테스트 (WAN-34).

WAN-33 `paper_trades` 스키마에 얹은 `open_positions` 테이블(진입 시 upsert, 청산 시
삭제, 재시작 시 로드)만 다룬다. 청산 라운드트립 기록은 `PaperTradeRecorder`에
위임하므로 `test_paper_store.py`(WAN-33)가 담당한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from execution.models import Position
from paper.store import PaperTradeStore
from strategy.models import OrderBlockDirection

SYMBOL = "BTC/USDT:USDT"
TF = "1h"


def _position(
    *,
    symbol: str = SYMBOL,
    timeframe: str = TF,
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    quantity: float = 2.0,
    entry_price: float = 100.0,
    entry_time: int = 1_000,
) -> Position:
    return Position(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        entry_time=entry_time,
        stop_price=90.0,
        take_profit_price=120.0,
    )


@pytest.fixture
def store() -> Iterator[PaperTradeStore]:
    s = PaperTradeStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def test_record_open_then_load(store: PaperTradeStore) -> None:
    store.record_open(_position(), risk_amount=100.0, entry_fee=0.5)
    loaded = store.load_open_positions()
    assert len(loaded) == 1
    op = loaded[0]
    assert op.position.symbol == SYMBOL
    assert op.position.quantity == pytest.approx(2.0)
    assert op.position.direction is OrderBlockDirection.BULLISH
    assert op.position.stop_price == pytest.approx(90.0)
    assert op.position.take_profit_price == pytest.approx(120.0)
    assert op.risk_amount == pytest.approx(100.0)
    assert op.entry_fee == pytest.approx(0.5)


def test_record_open_upserts_same_series(store: PaperTradeStore) -> None:
    store.record_open(_position(quantity=2.0), risk_amount=100.0, entry_fee=0.0)
    store.record_open(_position(quantity=3.0, entry_price=110.0), risk_amount=90.0, entry_fee=0.1)
    loaded = store.load_open_positions()
    assert len(loaded) == 1  # 같은 (symbol, tf)는 덮어쓴다
    assert loaded[0].position.quantity == pytest.approx(3.0)
    assert loaded[0].position.entry_price == pytest.approx(110.0)


def test_remove_open_position(store: PaperTradeStore) -> None:
    store.record_open(_position(), risk_amount=100.0, entry_fee=0.5)
    store.remove_open_position(SYMBOL, TF)
    assert store.load_open_positions() == []
    # 없는 시리즈를 지워도 무해하다.
    store.remove_open_position(SYMBOL, TF)


def test_open_positions_do_not_leak_into_paper_trades(store: PaperTradeStore) -> None:
    """열린 포지션 저장은 성과 테이블(paper_trades)을 건드리지 않는다."""
    store.record_open(_position(), risk_amount=100.0, entry_fee=0.5)
    assert store.count() == 0
    assert store.list_records() == []


def test_persistence_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "paper_trades.db"
    s1 = PaperTradeStore(db)
    s1.record_open(_position(), risk_amount=100.0, entry_fee=0.0)
    s1.close()

    s2 = PaperTradeStore(db)
    try:
        loaded = s2.load_open_positions()
        assert len(loaded) == 1
        assert loaded[0].position.symbol == SYMBOL
    finally:
        s2.close()


def test_multiple_series_open(store: PaperTradeStore) -> None:
    store.record_open(_position(symbol="BTC/USDT:USDT"), risk_amount=10.0, entry_fee=0.0)
    store.record_open(_position(symbol="ETH/USDT:USDT"), risk_amount=10.0, entry_fee=0.0)
    assert len(store.load_open_positions()) == 2
