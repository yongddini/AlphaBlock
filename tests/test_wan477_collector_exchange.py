"""WAN-477: 수집기의 거래소 연결 셋은 USDⓈ-M 시장만 받고, 받는 봉·펀딩은 옛 연결과 같다.

맥 실측(`scripts/wan477_collector_conn_probe.py`): 수집기가 만드는 연결 셋이 각자 시장 4,708개를
들고 있어 RSS +390~412MB · 파이썬 힙 313.6MB였다(공유 없음). USDⓈ-M만 받게 하면 +43~47MB다.
결과가 같아야 하므로 동작으로 건다(네트워크 없음):

1. 새 연결은 시장 목록 요청이 USDⓈ-M 하나다(현물·COIN-M 요청 0).
2. 같은 거래소 응답에서 `fetch_ohlcv` · `fetch_funding_rate` · `fetch_funding_rate_history`의
   **결과와 보낸 요청 URL이** 옛 연결과 같다(수집기가 쓰는 메서드 전부).
3. 수집기 세 경로(기동·펀딩 · 팔 수집 · REST 이중 수집)가 실제로 새 연결을 쓴다.
4. 현물 설정에서는 제한을 걸지 않는다(그 시장이 빠진다).
5. GC 진단 훅은 기본 꺼짐이고, 켜면 세대 2 수거만 문턱 이상일 때 남긴다.
"""

from __future__ import annotations

import gc
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from config.settings import Settings
from data import collector
from data.exchange import create_collector_exchange, create_exchange
from data.gc_watch import GcPauseLogger, freeze_startup_objects, install_gc_pause_logging
from data.storage import OhlcvStore
from tests.test_wan476_pool_exchange import _wire

SYMBOL = "BTC/USDT:USDT"

KLINES = [
    [
        1_700_000_000_000,
        "100.0",
        "101.0",
        "99.0",
        "100.5",
        "12.3",
        1_700_000_059_999,
        "0",
        1,
        "0",
        "0",
        "0",
    ],
    [
        1_700_000_060_000,
        "100.5",
        "102.0",
        "100.0",
        "101.5",
        "7.1",
        1_700_000_119_999,
        "0",
        1,
        "0",
        "0",
        "0",
    ],
]
PREMIUM = {
    "symbol": "BTCUSDT",
    "markPrice": "101.5",
    "indexPrice": "101.4",
    "estimatedSettlePrice": "101.4",
    "lastFundingRate": "0.00010000",
    "interestRate": "0.00010000",
    "nextFundingTime": 1_700_006_400_000,
    "time": 1_700_000_100_000,
}
FUNDING = [
    {"symbol": "BTCUSDT", "fundingTime": 1_699_977_600_000, "fundingRate": "0.00010000"},
    {"symbol": "BTCUSDT", "fundingTime": 1_700_006_400_000, "fundingRate": "-0.00005000"},
]
REST = {
    "/fapi/v1/klines": KLINES,
    "/fapi/v1/premiumIndex": PREMIUM,
    "/fapi/v1/fundingRate": FUNDING,
}


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def _wire_rest(exchange: Any, urls: list[str]) -> Any:
    """시장 목록은 WAN-476 고정 응답, 봉·펀딩은 위 고정 응답으로 돌린다(그 밖은 실패)."""
    _wire(exchange, [])

    def fetch(url: str, method: str = "GET", headers: Any = None, body: Any = None) -> Any:
        path = urlparse(url).path
        if path not in REST:
            raise AssertionError(f"고정 응답 밖의 네트워크 요청: {url}")
        urls.append(url)
        return json.loads(json.dumps(REST[path]))  # 매번 새 객체(파싱이 원본을 바꿔도 안전)

    exchange.fetch = fetch
    return exchange


def _collector_calls(exchange: Any) -> dict[str, Any]:
    return {
        "ohlcv": exchange.fetch_ohlcv(SYMBOL, "1m", limit=2),
        "funding_now": exchange.fetch_funding_rate(SYMBOL),
        "funding_history": exchange.fetch_funding_rate_history(SYMBOL, since=0, limit=10),
    }


def test_collector_exchange_requests_only_usdm_markets() -> None:
    calls: list[str] = []
    ex = _wire(create_collector_exchange(_settings()), calls)
    markets = ex.load_markets(True)
    assert calls == ["fapi"]
    assert all(m["linear"] for m in markets.values())


def test_collector_exchange_skips_currencies_even_with_keys() -> None:
    keyed = {"binance_api_key": "k", "binance_api_secret": "s"}
    old_calls: list[str] = []
    _wire(create_exchange(_settings(**keyed)), old_calls).load_markets(True)
    assert "currencies" in old_calls  # 옛 연결은 받는다(테스트가 헛돌지 않게)
    new_calls: list[str] = []
    _wire(create_collector_exchange(_settings(**keyed)), new_calls).load_markets(True)
    assert new_calls == ["fapi"]


def test_collector_results_and_requests_identical_to_old_exchange() -> None:
    old_urls: list[str] = []
    new_urls: list[str] = []
    old = _collector_calls(_wire_rest(create_exchange(_settings()), old_urls))
    new = _collector_calls(_wire_rest(create_collector_exchange(_settings()), new_urls))
    assert new == old
    assert new_urls == old_urls and len(new_urls) == 3
    assert new["ohlcv"][0][:5] == [1_700_000_000_000, 100.0, 101.0, 99.0, 100.5]
    assert [r["fundingRate"] for r in new["funding_history"]] == [0.0001, -0.00005]


def test_spot_market_type_is_not_restricted() -> None:
    ex = create_collector_exchange(_settings(market_type="spot"))
    assert "fetchMarkets" not in ex.options or ex.options["fetchMarkets"] != {"types": ["linear"]}
    assert ex.options.get("fetchCurrencies") is not False


def test_collector_exchange_options_do_not_leak() -> None:
    create_collector_exchange(_settings())
    plain = create_exchange(_settings())
    assert plain.options.get("fetchMarkets") != {"types": ["linear"]}
    assert plain.options.get("fetchCurrencies") is not False


def test_collector_has_no_unrestricted_exchange_path() -> None:
    """수집기 모듈이 제한 없는 `create_exchange`를 아예 들여오지 않는다(옛 연결로 새지 않게)."""
    assert not hasattr(collector, "create_exchange")


def test_arm_collection_and_rest_poller_use_collector_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[str] = []
    sentinel = object()

    def fake(settings: Settings) -> object:
        made.append("collector")
        return sentinel

    monkeypatch.setattr(collector, "create_collector_exchange", fake)
    settings = _settings(db_path=str(tmp_path / "x.db"))
    store = OhlcvStore(settings.db_path)
    try:
        arm = collector._build_arm_collection(settings, store)
        assert arm._exchange is sentinel
        poller = collector._build_rest_poller(settings, store, None, on_progress=lambda: None)
        assert poller._exchange is sentinel
    finally:
        store.close()
    assert made == ["collector", "collector"]


# --- GC 진단 ---------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_gc_settings_default_off() -> None:
    s = _settings()
    assert s.collector_gc_log_min_ms is None
    assert s.collector_gc_freeze_after_start is False


def test_install_gc_pause_logging_none_is_noop() -> None:
    before = list(gc.callbacks)
    assert install_gc_pause_logging(None) is None
    assert gc.callbacks == before


def test_gc_pause_logger_logs_only_slow_gen2(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    hook = GcPauseLogger(100.0, clock=clock)
    caplog.set_level(logging.WARNING, logger="data.gc_watch")

    # 세대 0·1은 무시한다
    hook("start", {"generation": 1})
    clock.t = 5.0
    hook("stop", {"generation": 1, "collected": 0, "uncollectable": 0})
    assert hook.last_ms is None and not caplog.records

    # 세대 2 · 문턱 아래
    clock.t = 10.0
    hook("start", {"generation": 2})
    clock.t = 10.05
    hook("stop", {"generation": 2, "collected": 3, "uncollectable": 0})
    assert hook.last_ms == pytest.approx(50.0)
    assert not caplog.records

    # 세대 2 · 문턱 이상
    clock.t = 20.0
    hook("start", {"generation": 2})
    clock.t = 63.0
    hook("stop", {"generation": 2, "collected": 7, "uncollectable": 0})
    assert hook.last_ms == pytest.approx(43_000.0)
    assert len(caplog.records) == 1 and "43000.0ms" in caplog.records[0].getMessage()


def test_gc_pause_logger_real_collection() -> None:
    """실제 `gc.collect()`에서 훅이 세대 2 소요 시간을 채운다(라벨이 아니라 동작)."""
    hook = install_gc_pause_logging(1e9)
    assert hook is not None
    try:
        gc.collect()
        assert hook.last_ms is not None and hook.last_ms >= 0
    finally:
        gc.callbacks.remove(hook)


def test_freeze_startup_objects_moves_objects_to_permanent_generation() -> None:
    try:
        frozen = freeze_startup_objects()
        assert frozen > 0 and gc.get_freeze_count() == frozen
    finally:
        gc.unfreeze()
