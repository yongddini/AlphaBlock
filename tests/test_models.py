"""data.models 테스트."""

from __future__ import annotations

import pytest

from data.models import Candle, candle_from_ccxt, timeframe_to_ms


def test_timeframe_to_ms_known() -> None:
    assert timeframe_to_ms("1m") == 60_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("1d") == 86_400_000


def test_timeframe_to_ms_unknown_raises() -> None:
    with pytest.raises(ValueError):
        timeframe_to_ms("7m")


def test_candle_from_ccxt_row() -> None:
    row = [1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 123.0]
    c = candle_from_ccxt("BTC/USDT:USDT", "1m", row)
    assert c.open_time == 1_700_000_000_000
    assert c.open == 1.0 and c.high == 2.0 and c.low == 0.5 and c.close == 1.5
    assert c.volume == 123.0
    assert c.closed is True


def test_candle_as_row_serializes_closed_as_int() -> None:
    c = Candle("ETH/USDT:USDT", "5m", 1, 1.0, 1.0, 1.0, 1.0, 1.0, closed=False)
    assert c.as_row() == ("ETH/USDT:USDT", "5m", 1, 1.0, 1.0, 1.0, 1.0, 1.0, 0)
