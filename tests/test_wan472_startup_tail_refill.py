"""수집기 재시작마다 팔 종목 1분봉이 「🚨 수집 정지」로 울리던 것 (WAN-472) 회귀 테스트.

2026-10-23 WAN-471 배포로 수집기를 재시작하자 텔레그램에 「🚨 수집 정지(꼬리 신선도)」가 팔
종목 1m 21건으로 왔다(전부 「마지막 갱신 09:24 · 기대 주기의 3.0배」 = 꺼져 있던 몇 분). 기동
순서가 원인이었다: 기본 격자만 꼬리를 채우는 `backfill_all` → **팔 종목까지** 판정하는 시작 갭
복구 → 그 뒤에야 팔 종목 꼬리를 채우는 `arm.catch_up`.

이 파일은 **동작**을 고정한다(라벨이 아니라):

* (a) 팔 종목 1m 꼬리가 몇 분 늦은 채로 기동해도, 꼬리를 받을 수 있으면 경보 0건이고 상태
  파일의 정지 목록도 비어 있다(경보와 `status`가 같은 말을 한다).
* (a′) 대조군 — 판정 전 꼬리 받기를 끄면(= 판정을 원래 자리로) 같은 DB가 울린다. 즉 (a)는
  배선이 실제로 걸려야만 통과한다(돌연변이 확인).
* (b) 받아도 꼬리가 문턱을 넘으면(거래소 무응답 · 받기 실패) 경보가 그대로 나간다.
* (c) 기본 격자 시리즈는 판정 전에 다시 받지 않는다 — 방금 기동 백필이 채웠으니 그래도 정지면
  진짜 정지다.
* (d) 팔이 꺼져 있으면(`universe_enabled=False`) 옛 동작 그대로 — 거래소를 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from config.settings import Settings
from data.collector import run_collector
from data.models import Candle
from data.repair import RepairStateStore, repair_all
from data.storage import OhlcvStore
from data.universe_pool import RotationSnapshot, UniverseStore

MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR

BTC = "BTC/USDT:USDT"  # 기본 격자
ARM = "ARB/USDT:USDT"  # 이번 주 팔 목록(사고 종목 자리)


def _now_min() -> int:
    return int(time.time() * 1000) // MIN * MIN


def _bar(symbol: str, timeframe: str, open_time: int) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
    )


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "db_path": str(tmp_path / "ohlcv.db"),
        "symbols": [BTC],
        "timeframes": ["1h"],
        "universe_enabled": True,
        "universe_request_interval_seconds": 0.0,
        "funding_enabled": False,
        "repair_state_path": str(tmp_path / "repair_state.json"),
        "collector_heartbeat_path": str(tmp_path / "hb.json"),
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    }
    base.update(overrides)
    return Settings(**base)


def _seed(tmp_path: Path, *, arm_lag_min: int = 5, base_1m_lag_min: int | None = None) -> None:
    """BTC 1h는 신선 · 팔 종목 1m은 `arm_lag_min`분 전에 멈춘 채(= 수집기가 꺼져 있던 공백)."""
    now = _now_min()
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store:
        bars = [
            _bar(BTC, "1h", now // HOUR * HOUR - HOUR),
            _bar(ARM, "1m", now - arm_lag_min * MIN),
        ]
        if base_1m_lag_min is not None:
            bars.append(_bar(BTC, "1m", now - base_1m_lag_min * MIN))
        store.upsert_candles(bars)
    with UniverseStore(db) as universe:
        t = now - 3 * HOUR
        universe.save(
            RotationSnapshot(
                rebalance_ms=t,
                computed_ms=t,
                pool_size=10,
                stale_size=0,
                ranked=((ARM, 1.0),),
                top_n=31,
            )
        )


class _FakeTelegram:
    def __init__(self, sent: list[str]) -> None:
        self._sent = sent

    def send_message(self, text: str, **_: Any) -> bool:
        self._sent.append(text)
        return True


class _TailExchange:
    """요청받은 `since`부터 지금 직전까지 닫힌 1분봉을 준다 — `respond=False`면 빈 응답(무응답)."""

    def __init__(self, *, respond: bool = True, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._respond = respond
        self._fail = fail

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.calls.append((symbol, timeframe))
        if self._fail:
            raise RuntimeError("거래소 응답 없음")
        if not self._respond or timeframe != "1m" or since is None:
            return []
        now = int(time.time() * 1000)
        return [[float(t), 1.0, 1.0, 1.0, 1.0, 1.0] for t in range(since, now - MIN + 1, MIN)][
            : limit or 1500
        ]


def _stub_collector(monkeypatch: pytest.MonkeyPatch, exchange: _TailExchange) -> list[str]:
    """백필·펀딩만 걷어내고 **진짜** `repair_all`·`alert_on_failure`·꼬리 받기를 돌린다."""
    sent: list[str] = []
    monkeypatch.setattr("data.collector.create_exchange", lambda settings: exchange)
    monkeypatch.setattr("data.collector.backfill_all", lambda *a, **k: {})
    monkeypatch.setattr("data.repair.build_telegram_client", lambda settings: _FakeTelegram(sent))

    async def no_funding(settings: Settings, exchange: Any) -> None:
        return None

    monkeypatch.setattr("data.collector._backfill_funding", no_funding)
    return sent


# -- (a) 꼬리를 받을 수 있으면 울리지 않는다 ------------------------------------------------


def test_restart_gap_on_arm_1m_does_not_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) 팔 종목 1m이 5분 늦은 채 기동 → 판정 전에 꼬리를 받아 경보 0건 · 상태 파일도 빈다."""
    exchange = _TailExchange()
    sent = _stub_collector(monkeypatch, exchange)
    _seed(tmp_path)
    settings = _settings(tmp_path)

    asyncio.run(run_collector(settings, run_stream=False))

    assert sent == []
    assert (ARM, "1m") in exchange.calls
    saved = RepairStateStore(settings.repair_state_path).load()
    assert saved is not None
    assert saved.stale_series == []  # 경보와 `status`가 같은 판정을 본다
    with OhlcvStore(tmp_path / "ohlcv.db") as store:
        last = store.last_open_time(ARM, "1m")
    assert last is not None and last >= _now_min() - 2 * MIN


def test_restart_gap_alerts_when_judged_before_the_tail_refill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a′) 돌연변이 — 꼬리 받기를 끄면(판정을 원래 자리로) (a)의 DB가 「수집 정지」로 울린다."""
    exchange = _TailExchange()
    sent = _stub_collector(monkeypatch, exchange)
    monkeypatch.setattr("data.collector._arm_tail_refill", lambda *a, **k: None)
    _seed(tmp_path)
    settings = _settings(tmp_path)

    asyncio.run(run_collector(settings, run_stream=False))

    assert len(sent) == 1
    assert "수집 정지" in sent[0]
    assert ARM in sent[0]


# -- (b) 받아도 늦으면 진짜 정지 ----------------------------------------------------------


@pytest.mark.parametrize(
    "exchange", [_TailExchange(respond=False), _TailExchange(fail=True)], ids=["무응답", "실패"]
)
def test_still_stale_after_refill_alerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exchange: _TailExchange
) -> None:
    """(b) 거래소가 꼬리를 안 주면(빈 응답·예외) 문턱 그대로 경보가 나가고 상태 파일에도 남는다."""
    sent = _stub_collector(monkeypatch, exchange)
    _seed(tmp_path)
    settings = _settings(tmp_path)

    asyncio.run(run_collector(settings, run_stream=False))

    assert (ARM, "1m") in exchange.calls  # 받으려고는 했다
    assert len(sent) == 1
    assert "수집 정지" in sent[0]
    assert ARM in sent[0]
    saved = RepairStateStore(settings.repair_state_path).load()
    assert saved is not None
    assert [(s.symbol, s.timeframe) for s in saved.stale_series] == [(ARM, "1m")]


# -- (c) 기본 격자는 다시 받지 않는다 -----------------------------------------------------


def test_base_grid_series_is_not_refilled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) 기본 격자 1m이 멈춰 있으면 다시 받지 않고 그대로 경보한다(기동 백필이 이미 채운 자리)."""
    exchange = _TailExchange()
    sent = _stub_collector(monkeypatch, exchange)
    _seed(tmp_path, base_1m_lag_min=10)
    settings = _settings(tmp_path, timeframes=["1m", "1h"])

    asyncio.run(run_collector(settings, run_stream=False))

    assert (BTC, "1m") not in exchange.calls
    assert len(sent) == 1
    assert BTC in sent[0]
    assert ARM not in sent[0]  # 팔 종목은 받아서 벗어났다


# -- (d) 팔이 꺼져 있으면 옛 동작 ----------------------------------------------------------


def test_arm_disabled_keeps_old_behaviour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) `universe_enabled=False`면 판정 전 꼬리 받기가 없다 — 멈춘 기본 격자는 예전처럼 운다."""
    exchange = _TailExchange()
    sent = _stub_collector(monkeypatch, exchange)
    _seed(tmp_path, base_1m_lag_min=10)
    settings = _settings(tmp_path, timeframes=["1m", "1h"], universe_enabled=False)

    asyncio.run(run_collector(settings, run_stream=False))

    assert exchange.calls == []
    assert len(sent) == 1
    assert BTC in sent[0]


# -- repair_all 단위 ---------------------------------------------------------------------


def test_repair_all_rejudges_only_the_stale_pairs(tmp_path: Path) -> None:
    """`refill_stale`은 정지로 보인 쌍만 받고, 받은 쌍만 벗어난다(나머지 판정은 그대로)."""
    now = _now_min()
    with OhlcvStore(tmp_path / "x.db") as store:
        store.upsert_candles(
            [
                _bar(ARM, "1m", now - 5 * MIN),
                _bar(BTC, "1m", now - 6 * MIN),
                _bar(BTC, "1h", now // HOUR * HOUR - HOUR),
            ]
        )
        asked: list[list[tuple[str, str]]] = []

        def refill(pairs: Sequence[tuple[str, str]]) -> None:
            asked.append(list(pairs))
            if (ARM, "1m") in pairs:
                store.upsert_candles([_bar(ARM, "1m", now - MIN)])

        summary = repair_all(_TailExchange(respond=False), store, refill_stale=refill)

    # 쌍마다 한 번씩 부른다(받자마자 그 쌍만 판정) — 정지로 보인 두 쌍만, 신선한 1h는 안 부른다.
    assert sorted(asked) == sorted([[(ARM, "1m")], [(BTC, "1m")]])
    assert [(s.symbol, s.timeframe) for s in summary.stale_series] == [(BTC, "1m")]


# -- 2026-10-26 배포 재시작: 받기가 느려도 거짓 경보가 안 남는다 ------------------------------


class _Clock:
    """받기 한 번에 `step_ms`씩 흐르는 가짜 시계 — 느린 받기(긴 이력 채우기)를 흉내 낸다."""

    def __init__(self, start_ms: int) -> None:
        self.t = start_ms

    def __call__(self) -> int:
        return self.t


def _slow_refill(store: OhlcvStore, clock: _Clock, *, step_ms: int, respond: bool = True) -> Any:
    """요청 순간까지 닫힌 봉을 채우고(응답하면) 시계를 `step_ms` 흘린다(거래소 받기와 같은 모양)."""

    def refill(pairs: Sequence[tuple[str, str]]) -> None:
        asked_at = clock.t
        if respond:
            store.upsert_candles([_bar(sym, tf, asked_at // MIN * MIN - MIN) for sym, tf in pairs])
        clock.t += step_ms

    return refill


SLOW_PAIRS = [("1000PEPE/USDT:USDT", "1m"), ("AAVE/USDT:USDT", "1m"), ("AGT/USDT:USDT", "1m")]


def test_slow_refill_does_not_make_already_filled_series_stale_again(tmp_path: Path) -> None:
    """2026-10-26 사고 모양 — 한 쌍 받는 데 3분 25초(AGT 742,949봉)가 걸려도 경보 0건.

    옛 구현(전부 받은 뒤 한꺼번에 · 받은 「뒤」 시각으로 판정)은 앞에서 채운 1000PEPE·AAVE까지
    다시 문턱(2.5분)을 넘겨 「🚨 수집 정지」 3건을 냈다. 판정 시각이 받기 「직전」이면 그 시리즈가
    달라고 한 순간까지 따라왔는지만 본다.
    """
    start = _now_min() + 30_000  # 분 중간(닫힌 봉 경계와 겹치지 않게)
    clock = _Clock(start)
    with OhlcvStore(tmp_path / "x.db") as store:
        store.upsert_candles(
            [_bar(sym, tf, start // MIN * MIN - 9 * MIN) for sym, tf in SLOW_PAIRS]
        )
        summary = repair_all(
            _TailExchange(respond=False),
            store,
            now_ms=clock,
            refill_stale=_slow_refill(store, clock, step_ms=205_000),
        )

    assert summary.stale_series == []
    assert clock.t - start == 3 * 205_000  # 정말 느렸다(테스트가 재는 것이 바뀌지 않았다)


def test_slow_refill_that_returns_nothing_still_alerts(tmp_path: Path) -> None:
    """받기가 느리고 **아무것도 안 오면** 판정 시각을 앞당겨도 셋 다 정지다(진짜 정지는 그대로)."""
    start = _now_min() + 30_000
    clock = _Clock(start)
    with OhlcvStore(tmp_path / "x.db") as store:
        store.upsert_candles(
            [_bar(sym, tf, start // MIN * MIN - 9 * MIN) for sym, tf in SLOW_PAIRS]
        )
        summary = repair_all(
            _TailExchange(respond=False),
            store,
            now_ms=clock,
            refill_stale=_slow_refill(store, clock, step_ms=205_000, respond=False),
        )

    assert sorted((s.symbol, s.timeframe) for s in summary.stale_series) == sorted(SLOW_PAIRS)


def test_repair_all_without_refill_is_unchanged(tmp_path: Path) -> None:
    """`refill_stale`을 안 주면 옛 동작 — 같은 DB에서 두 쌍 모두 정지다."""
    now = _now_min()
    with OhlcvStore(tmp_path / "x.db") as store:
        store.upsert_candles([_bar(ARM, "1m", now - 5 * MIN), _bar(BTC, "1m", now - 6 * MIN)])
        summary = repair_all(_TailExchange(respond=False), store)

    assert {(s.symbol, s.timeframe) for s in summary.stale_series} == {(ARM, "1m"), (BTC, "1m")}
