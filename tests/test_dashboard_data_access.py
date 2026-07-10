"""dashboard.data_access 테스트 (WAN-6 SQLite 저장소 래핑)."""

from __future__ import annotations

from pathlib import Path

from dashboard.data_access import list_series, load_ohlcv
from data.models import Candle
from data.storage import OhlcvStore


def _candle(symbol: str, timeframe: str, open_time: int, close: float) -> Candle:
    return Candle(symbol, timeframe, open_time, close, close, close, close, 10.0)


def test_list_series_returns_distinct_symbol_timeframe_pairs(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ohlcv.db")
    with OhlcvStore(db_path) as store:
        store.upsert_candles(
            [
                _candle("BTC/USDT:USDT", "1h", 0, 100.0),
                _candle("BTC/USDT:USDT", "1h", 3_600_000, 101.0),
                _candle("BTC/USDT:USDT", "1d", 0, 100.0),
                _candle("ETH/USDT:USDT", "1h", 0, 10.0),
            ]
        )

    assert list_series(db_path) == [
        ("BTC/USDT:USDT", "1d"),
        ("BTC/USDT:USDT", "1h"),
        ("ETH/USDT:USDT", "1h"),
    ]


def test_load_ohlcv_filters_by_symbol_timeframe_and_period(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ohlcv.db")
    step = 3_600_000
    with OhlcvStore(db_path) as store:
        store.upsert_candles(
            [_candle("BTC/USDT:USDT", "1h", i * step, 100.0 + i) for i in range(5)]
        )

    df = load_ohlcv(db_path, "BTC/USDT:USDT", "1h", start_ms=step, end_ms=3 * step)
    assert list(df["open_time"]) == [step, 2 * step]

    empty = load_ohlcv(db_path, "BTC/USDT:USDT", "5m")
    assert empty.empty
