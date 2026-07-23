"""data.stream 테스트 (파싱·소비 로직, 네트워크 없음)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import pytest

from data.models import Candle
from data.storage import OhlcvStore
from data.stream import (
    FUTURES_WS_BASE,
    build_stream_path,
    build_symbol_map,
    consume_messages,
    parse_kline_message,
    to_ws_symbol,
)

SYMBOL_MAP = build_symbol_map(["BTC/USDT:USDT", "ETH/USDT:USDT"])


def _kline_msg(
    ws_symbol: str,
    interval: str,
    open_time: int,
    close: float,
    *,
    closed: bool,
    combined: bool = True,
) -> dict[str, object]:
    data: dict[str, object] = {
        "e": "kline",
        "s": ws_symbol,
        "k": {
            "t": open_time,
            "s": ws_symbol,
            "i": interval,
            "o": "1.0",
            "h": "2.0",
            "l": "0.5",
            "c": str(close),
            "v": "10.0",
            "x": closed,
        },
    }
    if combined:
        return {"stream": f"{ws_symbol.lower()}@kline_{interval}", "data": data}
    return data


def test_to_ws_symbol() -> None:
    assert to_ws_symbol("BTC/USDT:USDT") == "btcusdt"
    assert to_ws_symbol("ETH/USDT") == "ethusdt"


def test_build_symbol_map() -> None:
    assert SYMBOL_MAP == {"BTCUSDT": "BTC/USDT:USDT", "ETHUSDT": "ETH/USDT:USDT"}


def test_build_stream_path() -> None:
    path = build_stream_path(["BTC/USDT:USDT"], ["1m", "1h"])
    assert path == "/stream?streams=btcusdt@kline_1m/btcusdt@kline_1h"


def test_futures_ws_base_keeps_market_prefix() -> None:
    """선물 베이스의 `/market` 접두사를 동작으로 고정한다(WAN-174).

    접두사가 빠지면 바이낸스는 **101 핸드셰이크까지 성공시키고 데이터 프레임을 한 건도
    안 보낸다** — 예외도 연결 종료도 없어 수집기가 조용히 멈춘 것처럼 보인다. 라벨이
    아니라 **최종 조립 URL**로 고정해야 이 조용한 실패가 회귀로 잡힌다.
    """
    assert FUTURES_WS_BASE == "wss://fstream.binance.com/market"

    url = FUTURES_WS_BASE + build_stream_path(["BTC/USDT:USDT"], ["1m"])
    assert url == ("wss://fstream.binance.com/market/stream?streams=btcusdt@kline_1m")


def test_parse_closed_candle_combined() -> None:
    msg = _kline_msg("BTCUSDT", "1m", 1000, 1.5, closed=True)
    candle = parse_kline_message(json.dumps(msg), SYMBOL_MAP)
    assert candle == Candle("BTC/USDT:USDT", "1m", 1000, 1.0, 2.0, 0.5, 1.5, 10.0, True)


def test_parse_single_stream_payload() -> None:
    msg = _kline_msg("ETHUSDT", "5m", 2000, 3.0, closed=True, combined=False)
    candle = parse_kline_message(msg, SYMBOL_MAP)
    assert candle is not None
    assert candle.symbol == "ETH/USDT:USDT" and candle.timeframe == "5m"


def test_parse_unclosed_returns_none_by_default() -> None:
    msg = _kline_msg("BTCUSDT", "1m", 1000, 1.5, closed=False)
    assert parse_kline_message(msg, SYMBOL_MAP) is None


def test_parse_unclosed_returned_when_only_closed_false() -> None:
    msg = _kline_msg("BTCUSDT", "1m", 1000, 1.5, closed=False)
    candle = parse_kline_message(msg, SYMBOL_MAP, only_closed=False)
    assert candle is not None and candle.closed is False


def test_parse_non_kline_event_returns_none() -> None:
    assert parse_kline_message({"data": {"e": "aggTrade"}}, SYMBOL_MAP) is None


def test_parse_unknown_symbol_returns_none() -> None:
    msg = _kline_msg("XRPUSDT", "1m", 1000, 1.5, closed=True)
    assert parse_kline_message(msg, SYMBOL_MAP) is None


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


def test_consume_messages_stores_only_closed(store: OhlcvStore) -> None:
    messages = [
        json.dumps(_kline_msg("BTCUSDT", "1m", 1000, 1.1, closed=False)),  # 무시
        json.dumps(_kline_msg("BTCUSDT", "1m", 1000, 1.2, closed=True)),  # 저장
        json.dumps(_kline_msg("ETHUSDT", "5m", 2000, 3.0, closed=True)),  # 저장
        json.dumps({"data": {"e": "aggTrade"}}),  # 무시
    ]
    seen: list[Candle] = []
    stored = asyncio.run(
        consume_messages(_aiter(messages), store, SYMBOL_MAP, on_candle=seen.append)
    )
    assert stored == 2
    assert len(seen) == 2
    assert store.count(symbol="BTC/USDT:USDT", timeframe="1m") == 1
    assert store.count(symbol="ETH/USDT:USDT", timeframe="5m") == 1


def test_consume_messages_heartbeats_every_message(store: OhlcvStore) -> None:
    """하트비트는 저장 여부와 무관하게 수신 메시지마다 호출된다(WAN-31)."""
    messages = [
        json.dumps(_kline_msg("BTCUSDT", "1m", 1000, 1.1, closed=False)),  # 미확정
        json.dumps(_kline_msg("BTCUSDT", "1m", 1000, 1.2, closed=True)),  # 확정
        json.dumps({"data": {"e": "aggTrade"}}),  # kline 아님
    ]
    beats = {"n": 0}

    def beat() -> None:
        beats["n"] += 1

    asyncio.run(consume_messages(_aiter(messages), store, SYMBOL_MAP, heartbeat=beat))
    assert beats["n"] == 3  # 3개 메시지 모두 하트비트
