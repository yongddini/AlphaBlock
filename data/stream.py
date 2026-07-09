"""바이낸스 USDⓈ-M 선물 실시간 kline 스트림.

`wss://fstream.binance.com`의 결합 스트림(`<symbol>@kline_<interval>`)을 asyncio로
구독한다. 봉 확정(`k.x == true`)일 때만 저장하고, 미확정 봉은 무시(갱신만 원하면
`only_closed=False`). 메시지 파싱(`parse_kline_message`)과 소비 루프
(`consume_messages`)는 네트워크와 분리해 단위 테스트가 가능하다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Sequence

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from data.models import Candle
from data.storage import OhlcvStore

logger = logging.getLogger(__name__)

FUTURES_WS_BASE = "wss://fstream.binance.com"


def to_ws_symbol(symbol: str) -> str:
    """ccxt 심볼을 바이낸스 웹소켓 심볼로 변환한다.

    예: ``"BTC/USDT:USDT" -> "btcusdt"``, ``"ETH/USDT" -> "ethusdt"``.
    """
    base = symbol.split(":", 1)[0]  # 선물 정산통화(":USDT") 제거
    return base.replace("/", "").lower()


def build_symbol_map(symbols: Iterable[str]) -> dict[str, str]:
    """웹소켓 심볼(대문자) → 원본 ccxt 심볼 매핑. 수신 메시지 역매핑용."""
    return {to_ws_symbol(s).upper(): s for s in symbols}


def build_stream_path(symbols: Sequence[str], timeframes: Sequence[str]) -> str:
    """결합 스트림 경로(`/stream?streams=...`)를 만든다."""
    streams = [
        f"{to_ws_symbol(symbol)}@kline_{timeframe}"
        for symbol in symbols
        for timeframe in timeframes
    ]
    return "/stream?streams=" + "/".join(streams)


def parse_kline_message(
    raw: str | bytes | dict[str, object],
    symbol_map: dict[str, str],
    *,
    only_closed: bool = True,
) -> Candle | None:
    """웹소켓 kline 메시지를 `Candle`로 파싱한다.

    - 결합 스트림 봉투(`{"stream":..,"data":{..}}`)와 단일 스트림 페이로드 모두 지원.
    - `only_closed=True`(기본)이면 확정봉(`k.x == true`)만 반환.
    - kline 이벤트가 아니거나, 미확정(only_closed), 또는 매핑에 없는 심볼이면 None.
    """
    payload = json.loads(raw) if isinstance(raw, str | bytes) else raw
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict) or data.get("e") != "kline":
        return None

    kline = data.get("k")
    if not isinstance(kline, dict):
        return None

    closed = bool(kline.get("x", False))
    if only_closed and not closed:
        return None

    ws_symbol = str(kline.get("s", "")).upper()
    symbol = symbol_map.get(ws_symbol)
    if symbol is None:
        logger.debug("매핑에 없는 심볼 무시: %s", ws_symbol)
        return None

    return Candle(
        symbol=symbol,
        timeframe=str(kline["i"]),
        open_time=int(kline["t"]),
        open=float(kline["o"]),
        high=float(kline["h"]),
        low=float(kline["l"]),
        close=float(kline["c"]),
        volume=float(kline["v"]),
        closed=closed,
    )


async def consume_messages(
    messages: AsyncIterator[str | bytes],
    store: OhlcvStore,
    symbol_map: dict[str, str],
    *,
    only_closed: bool = True,
    on_candle: Callable[[Candle], None] | None = None,
) -> int:
    """메시지 스트림을 소비해 확정봉을 저장한다. 저장한 봉 수를 반환한다."""
    stored = 0
    async for raw in messages:
        candle = parse_kline_message(raw, symbol_map, only_closed=only_closed)
        if candle is None:
            continue
        store.upsert_candles([candle])
        stored += 1
        if on_candle is not None:
            on_candle(candle)
    return stored


async def _recv_stream(ws: ClientConnection) -> AsyncIterator[str | bytes]:
    """웹소켓 연결을 메시지 async 이터레이터로 감싼다(정상 종료 시 조용히 끝)."""
    try:
        while True:
            yield await ws.recv()
    except ConnectionClosed:
        return


async def stream_klines(
    store: OhlcvStore,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    ws_base: str = FUTURES_WS_BASE,
    only_closed: bool = True,
    on_candle: Callable[[Candle], None] | None = None,
) -> None:
    """선물 kline 결합 스트림에 접속해 확정봉을 계속 저장한다.

    네트워크 예외가 나면 로깅 후 예외를 다시 던진다(상위 오케스트레이터가
    재접속 정책을 결정). 무한 스트림이므로 정상 반환은 하지 않는다.
    """
    url = ws_base + build_stream_path(symbols, timeframes)
    symbol_map = build_symbol_map(symbols)
    logger.info("웹소켓 접속: %d 심볼 × %d 타임프레임", len(symbols), len(timeframes))

    async with connect(url) as ws:
        await consume_messages(
            _recv_stream(ws),
            store,
            symbol_map,
            only_closed=only_closed,
            on_candle=on_candle,
        )
