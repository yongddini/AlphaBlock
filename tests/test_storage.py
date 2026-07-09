"""data.storage 테스트 (인메모리 SQLite)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from data.models import Candle
from data.storage import OhlcvStore


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _candle(open_time: int, close: float, *, closed: bool = True) -> Candle:
    return Candle("BTC/USDT:USDT", "1m", open_time, 1.0, 2.0, 0.5, close, 10.0, closed)


def test_upsert_and_count(store: OhlcvStore) -> None:
    n = store.upsert_candles([_candle(1000, 1.5), _candle(2000, 1.6)])
    assert n == 2
    assert store.count() == 2
    assert store.count(symbol="BTC/USDT:USDT", timeframe="1m") == 2
    assert store.count(timeframe="5m") == 0


def test_upsert_is_idempotent_on_pk(store: OhlcvStore) -> None:
    """같은 (symbol, timeframe, open_time) 재삽입은 중복 없이 갱신된다."""
    store.upsert_candles([_candle(1000, 1.5)])
    store.upsert_candles([_candle(1000, 9.9)])  # 같은 키, close 변경
    assert store.count() == 1
    df = store.load("BTC/USDT:USDT", "1m")
    assert list(df["close"]) == [9.9]


def test_last_open_time(store: OhlcvStore) -> None:
    assert store.last_open_time("BTC/USDT:USDT", "1m") is None
    store.upsert_candles([_candle(1000, 1.5), _candle(3000, 1.7), _candle(2000, 1.6)])
    assert store.last_open_time("BTC/USDT:USDT", "1m") == 3000


def test_load_orders_and_filters(store: OhlcvStore) -> None:
    store.upsert_candles([_candle(3000, 3.0), _candle(1000, 1.0), _candle(2000, 2.0)])
    df = store.load("BTC/USDT:USDT", "1m")
    assert list(df["open_time"]) == [1000, 2000, 3000]  # 정렬
    assert "open_datetime" in df.columns
    assert df["closed"].dtype == bool

    windowed = store.load("BTC/USDT:USDT", "1m", start_ms=2000, end_ms=3000)
    assert list(windowed["open_time"]) == [2000]  # end 는 배타적


def test_load_empty_returns_schema(store: OhlcvStore) -> None:
    df = store.load("NON/EXISTENT", "1m")
    assert df.empty
    assert "open_time" in df.columns and "close" in df.columns


def test_upsert_empty_is_noop(store: OhlcvStore) -> None:
    assert store.upsert_candles([]) == 0
