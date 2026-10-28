"""WAN-476: 후보 풀 갱신 인스턴스는 USDⓈ-M 시장만 받고, 풀 결과는 옛 인스턴스와 같다.

매일 09:02 KST `load_markets(True)`가 현물·USDⓈ-M·COIN-M 시장 전부(키가 있으면 화폐 목록까지)를
파이썬 객체로 올려 서버가 스왑에 빠졌다. 고친 뒤에도 **후보 풀 · 주간 목록은 비트 단위로 같아야**
하므로 셋을 동작으로 건다:

1. 새 인스턴스는 USDⓈ-M 엔드포인트 **하나만** 부른다(현물·COIN-M·화폐 목록 요청 0).
2. 같은 거래소 응답에서 옛 인스턴스와 새 인스턴스의 `pool_symbols`가 같다 — 빠진 시장은 전부
   `pool_symbols`가 어차피 버리던 것이다.
3. 수집기의 후보 풀 루프가 실제로 새 인스턴스를 쓴다(배선 — 라벨이 아니라 호출 인자로).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import ccxt
import pytest

from config.settings import Settings
from data import collector
from data.exchange import POOL_MARKET_TYPES, create_exchange, create_pool_exchange
from data.universe_pool import pool_symbols


def _fut(
    sym: str,
    base: str,
    *,
    ct: str = "PERPETUAL",
    status: str = "TRADING",
    quote: str = "USDT",
    margin: str | None = None,
    delivery: int = 4133404800000,
) -> dict[str, Any]:
    return {
        "symbol": sym,
        "pair": sym.split("_")[0],
        "contractType": ct,
        "status": status,
        "contractStatus": status,
        "baseAsset": base,
        "quoteAsset": quote,
        "marginAsset": margin or quote,
        "pricePrecision": 2,
        "quantityPrecision": 3,
        "baseAssetPrecision": 8,
        "quotePrecision": 8,
        "filters": [],
        "orderTypes": [],
        "timeInForce": [],
        "onboardDate": 1569398400000,
        "deliveryDate": delivery,
        "contractSize": 100 if quote == "USD" else 1,
    }


def _spot(sym: str, base: str, quote: str = "USDT") -> dict[str, Any]:
    return {
        "symbol": sym,
        "status": "TRADING",
        "baseAsset": base,
        "quoteAsset": quote,
        "baseAssetPrecision": 8,
        "quotePrecision": 8,
        "filters": [],
        "orderTypes": [],
        "isSpotTradingAllowed": True,
        "isMarginTradingAllowed": False,
        "permissions": ["SPOT"],
    }


#: USDⓈ-M — 풀에 들어갈 둘 · 분기물 · 상폐 절차 · USDC 무기한(전부 `pool_symbols`가 가린다).
FAPI = {
    "symbols": [
        _fut("BTCUSDT", "BTC"),
        _fut("ETHUSDT", "ETH"),
        _fut("BTCUSDT_261225", "BTC", ct="CURRENT_QUARTER", delivery=1798185600000),
        _fut("EPICUSDT", "EPIC", status="SETTLING"),
        _fut("BTCUSDC", "BTC", quote="USDC"),
    ]
}
SPOT = {"symbols": [_spot("BTCUSDT", "BTC"), _spot("ETHBTC", "ETH", "BTC")]}
DAPI = {"symbols": [_fut("BTCUSD_PERP", "BTC", quote="USD", margin="BTC")]}


def _wire(exchange: ccxt.binance, calls: list[str]) -> ccxt.binance:
    """거래소 응답을 고정 응답으로 바꾸고 어느 엔드포인트를 불렀는지 남긴다(네트워크 없음)."""

    def endpoint(name: str, payload: Any) -> Callable[..., Any]:
        def call(params: Any = None) -> Any:
            calls.append(name)
            return payload

        return call

    exchange.fapiPublicGetExchangeInfo = endpoint("fapi", FAPI)
    exchange.publicGetExchangeInfo = endpoint("spot", SPOT)
    exchange.dapiPublicGetExchangeInfo = endpoint("dapi", DAPI)
    exchange.eapiPublicGetExchangeInfo = endpoint("eapi", {"optionSymbols": []})
    exchange.sapiGetCapitalConfigGetall = endpoint("currencies", [])
    exchange.sapiGetMarginAllAssets = endpoint("currencies", [])
    exchange.sapiGetMarginAllPairs = endpoint("margin", [])
    exchange.sapiGetMarginIsolatedAllPairs = endpoint("margin", [])

    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"고정 응답 밖의 네트워크 요청: {args[:1]}")

    exchange.fetch = no_network  # 위에서 안 바꾼 엔드포인트로 새면 바로 실패한다
    return exchange


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def test_pool_exchange_requests_only_usdm_markets() -> None:
    calls: list[str] = []
    ex = _wire(create_pool_exchange(_settings()), calls)
    markets = ex.load_markets(True)
    assert calls == ["fapi"]
    assert all(m["linear"] for m in markets.values())
    assert tuple(ex.options["fetchMarkets"]["types"]) == POOL_MARKET_TYPES


def test_pool_exchange_skips_currencies_even_with_keys() -> None:
    """키가 있으면 옛 인스턴스는 `sapi` 화폐 목록까지 받는다 — 새 인스턴스는 안 받는다."""
    keyed = {"binance_api_key": "k", "binance_api_secret": "s"}
    old_calls: list[str] = []
    _wire(create_exchange(_settings(**keyed)), old_calls).load_markets(True)
    assert "currencies" in old_calls  # 옛 동작 확인(이 테스트가 헛돌지 않게)
    new_calls: list[str] = []
    _wire(create_pool_exchange(_settings(**keyed)), new_calls).load_markets(True)
    assert new_calls == ["fapi"]


def test_pool_symbols_identical_to_old_exchange() -> None:
    old_calls: list[str] = []
    old = _wire(create_exchange(_settings()), old_calls).load_markets(True)
    assert {"spot", "fapi", "dapi"} <= set(old_calls)  # 옛 인스턴스는 셋 다 받았다
    new = _wire(create_pool_exchange(_settings()), []).load_markets(True)
    assert len(new) < len(old)
    assert pool_symbols(new) == pool_symbols(old) == ["BTC/USDT:USDT", "ETH/USDT:USDT"]


def test_pool_exchange_does_not_mutate_shared_defaults() -> None:
    """새 인스턴스의 옵션이 다른 `create_exchange` 인스턴스로 새면 안 된다(공유 dict 함정)."""
    create_pool_exchange(_settings())
    other = create_exchange(_settings())
    assert other.options.get("fetchMarkets") != {"types": list(POOL_MARKET_TYPES)}
    assert other.options.get("fetchCurrencies") is not False


def test_collector_universe_loop_uses_pool_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_loop(exchange: Any, *args: Any, **kwargs: Any) -> None:
        seen["exchange"] = exchange

    import data.universe_pool as up

    monkeypatch.setattr(up, "run_universe_loop", fake_loop)
    settings = _settings(db_path=":memory:", telegram_bot_token="", telegram_chat_id="")
    asyncio.run(collector._run_universe(settings, store=None))  # type: ignore[arg-type]
    ex = seen["exchange"]
    assert ex.options["fetchMarkets"] == {"types": list(POOL_MARKET_TYPES)}
    assert ex.options["fetchCurrencies"] is False
