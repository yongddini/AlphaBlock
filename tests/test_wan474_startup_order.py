"""수집기 재시작 때 팔 종목 따라잡기가 웹소켓 접속을 막던 것 (WAN-474) 회귀 테스트.

2026-10-26(주간 교체 날) 배포 재시작에서 팔 종목 따라잡기가 **웹소켓 접속 전**에 동기로 돌았다.
279쌍 × 0.5초에 새로 들어온 종목의 이력 전체(AGT 1m 742,949봉 · 3분 25초, MOVR 15m 99,312봉 …)
라 13분 넘게 접속이 안 붙었고, 그동안 **기본 격자(BTC·ADA 등)까지** 새 봉이 끊겼다. 외부 감시
(`alphablock watch`)가 그것을 「수집기가 멎었다」로 보고 09:31에 다시 재시작했다.

이 파일은 **동작**을 고정한다(라벨이 아니라):

* (a) 팔 따라잡기가 아무리 오래 걸려도 **웹소켓이 먼저 붙는다** — 그리고 팔 따라잡기는 접속
  **뒤**에(접속 직후 따라잡기 `TailCatchup`) 실제로 돈다(데이터를 버린 게 아니다).
  돌연변이: 따라잡기를 접속 전으로 되돌리면 실패한다.
* (b) 시작 판정 전 꼬리 받기는 **짧은 꼬리만** 받는다 — 수십만 봉짜리 꼬리는 받지 않고 「채우는
  중」으로 넘기며 🚨를 보내지 않는다. `status`에도 그렇게 보인다.
* (c) 짧은 꼬리는 예전처럼 받고 판정한다(WAN-472 동작 유지 · 기존 테스트가 더 자세히 본다).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import data.collector as collector
from cli.main import format_status
from config.settings import Settings
from dashboard.health_data import build_health_view
from data.collection_plan import CollectionPlan, build_plan
from data.models import Candle
from data.repair import RepairStateStore
from data.storage import OhlcvStore
from data.universe_pool import RotationSnapshot, UniverseStore

MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR

BTC = "BTC/USDT:USDT"  # 기본 격자
ARM = "AGT/USDT:USDT"  # 이번 주 팔 목록(사고 종목 자리)


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
        "collector_watchdog_enabled": False,
        "collector_rest_poll_enabled": False,
        "repair_state_path": str(tmp_path / "repair_state.json"),
        "collector_heartbeat_path": str(tmp_path / "hb.json"),
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    }
    base.update(overrides)
    return Settings(**base)


async def _wait_until(cond: Any, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("조건이 제한 시간 안에 충족되지 않았다")
        await asyncio.sleep(0.01)


# -- (a) 웹소켓이 먼저 붙는다 ------------------------------------------------------------


class _SlowArm:
    """`catch_up`이 오래 걸리는 팔(새 종목 이력 채우기 흉내) — 호출 순서를 기록한다."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.release = threading.Event()
        self.plan: CollectionPlan = build_plan([BTC], ["1h"])

    def compute(self) -> CollectionPlan:
        return build_plan([BTC], ["1h"], [ARM])

    def adopt(self, plan: CollectionPlan) -> tuple[list[str], list[str]]:
        self.plan = plan
        return [], []

    def arm_pairs(self, plan: CollectionPlan | None = None) -> list[tuple[str, str]]:
        return [(ARM, "1m")]

    def catch_up(self, progress: Any = None, last_open_times: Any = None) -> int:
        self.events.append("catch_up_start")
        # 테스트(접속 쪽)가 풀어 줄 때까지 막힌다 — 접속 전에 불리면 접속이 영영 안 온다.
        self.release.wait(timeout=8)
        self.events.append("catch_up_end")
        return 0

    def extend_history(self, plan: CollectionPlan | None = None) -> int:
        return 0

    def on_candle(self, candle: Candle) -> None:
        return None


def test_stream_connects_before_the_slow_arm_catch_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) 팔 따라잡기가 오래 걸려도 웹소켓이 먼저 붙고, 따라잡기는 접속 뒤에 실제로 돈다."""
    events: list[str] = []
    arm = _SlowArm(events)

    monkeypatch.setattr(collector, "create_collector_exchange", lambda settings: object())
    monkeypatch.setattr(collector, "backfill_all", lambda *a, **k: {})
    monkeypatch.setattr(collector, "_build_arm_collection", lambda settings, store: arm)

    async def no_funding(settings: Settings, exchange: Any) -> None:
        return None

    async def idle_universe(settings: Settings, store: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(collector, "_backfill_funding", no_funding)
    monkeypatch.setattr(collector, "_run_universe", idle_universe)

    async def fake_stream(store: Any, arm_: Any, **kwargs: Any) -> None:
        events.append("stream")
        kwargs["on_connect"]()  # 접속 성립 — 접속 직후 따라잡기 예약(팔 쪽 포함)
        await _wait_until(lambda: "catch_up_start" in events)
        arm.release.set()
        await _wait_until(lambda: "catch_up_end" in events)
        raise RuntimeError("stop")  # 복구 불가 예외 → run_with_recovery가 올린다

    monkeypatch.setattr(collector, "_stream_arm_plan", fake_stream)
    settings = _settings(tmp_path)

    try:
        with pytest.raises(RuntimeError, match="stop"):
            asyncio.run(collector.run_collector(settings, run_stream=True, repair_on_start=False))
    finally:
        arm.release.set()

    assert events[0] == "stream", f"접속보다 팔 따라잡기가 먼저 돌았다: {events}"
    assert events.count("catch_up_start") == 1  # 접속 뒤 따라잡기가 팔 쪽을 맡았다


# -- (b) 긴 꼬리는 「채우는 중」으로 넘긴다 --------------------------------------------------


class _RecordingExchange:
    """요청을 기록하고 지금 직전까지 닫힌 1분봉을 준다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.calls.append((symbol, timeframe))
        if timeframe != "1m" or since is None:
            return []
        now = int(time.time() * 1000)
        return [[float(t), 1.0, 1.0, 1.0, 1.0, 1.0] for t in range(since, now - MIN + 1, MIN)][
            : limit or 1000
        ]


class _FakeTelegram:
    def __init__(self, sent: list[str]) -> None:
        self._sent = sent

    def send_message(self, text: str, **_: Any) -> bool:
        self._sent.append(text)
        return True


def _seed(tmp_path: Path, *, arm_lag_min: int) -> None:
    now = _now_min()
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store:
        store.upsert_candles(
            [_bar(BTC, "1h", now // HOUR * HOUR - HOUR), _bar(ARM, "1m", now - arm_lag_min * MIN)]
        )
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


def _run_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, arm_lag_min: int
) -> tuple[list[str], _RecordingExchange, Settings]:
    exchange = _RecordingExchange()
    sent: list[str] = []
    monkeypatch.setattr(collector, "create_collector_exchange", lambda settings: exchange)
    monkeypatch.setattr(collector, "backfill_all", lambda *a, **k: {})
    monkeypatch.setattr("data.repair.build_telegram_client", lambda settings: _FakeTelegram(sent))

    async def no_funding(settings: Settings, exchange: Any) -> None:
        return None

    monkeypatch.setattr(collector, "_backfill_funding", no_funding)
    _seed(tmp_path, arm_lag_min=arm_lag_min)
    settings = _settings(tmp_path)
    asyncio.run(collector.run_collector(settings, run_stream=False))
    return sent, exchange, settings


def test_long_tail_is_handed_off_not_fetched_and_not_alerted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) 600일 비어 있는 팔 종목 1m — 기동 때 받지 않고, 🚨도 없이 「채우는 중」으로 남는다."""
    started = time.monotonic()
    sent, exchange, settings = _run_startup(tmp_path, monkeypatch, arm_lag_min=600 * 24 * 60)

    assert time.monotonic() - started < 10.0  # 수십만 봉을 받지 않았다
    assert (ARM, "1m") not in exchange.calls
    assert sent == []
    saved = RepairStateStore(settings.repair_state_path).load()
    assert saved is not None
    assert saved.stale_series == []
    assert [(f.symbol, f.timeframe) for f in saved.filling_series] == [(ARM, "1m")]


def test_short_tail_is_still_fetched_and_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) 몇 분짜리 꼬리는 예전처럼 받아 정지에서 벗어난다(WAN-472 동작 유지)."""
    sent, exchange, settings = _run_startup(tmp_path, monkeypatch, arm_lag_min=5)

    assert (ARM, "1m") in exchange.calls
    assert sent == []
    saved = RepairStateStore(settings.repair_state_path).load()
    assert saved is not None
    assert saved.stale_series == [] and saved.filling_series == []


def test_status_shows_filling_series(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) `alphablock status`가 「채우는 중」을 보여 준다 — 조용히 사라지지 않는다."""
    _, _, settings = _run_startup(tmp_path, monkeypatch, arm_lag_min=600 * 24 * 60)

    view = build_health_view(
        str(tmp_path / "empty.db"),
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=str(tmp_path / "no_hb.json"),
        repair_state_path=settings.repair_state_path,
        now_ms=int(time.time() * 1000),
    )
    text = format_status(view)
    assert "채우는 중" in text
    assert ARM in text


def test_refill_hook_returns_only_long_arm_pairs(tmp_path: Path) -> None:
    """훅 단위 — 짧은 팔 꼬리는 받고, 긴 팔 꼬리는 돌려주고, 기본 격자는 둘 다 아니다."""
    now = _now_min()
    _seed(tmp_path, arm_lag_min=5)
    settings = _settings(tmp_path)
    exchange = _RecordingExchange()
    with OhlcvStore(tmp_path / "ohlcv.db") as store:
        store.upsert_candles([_bar(BTC, "1m", now - 9 * MIN)])
        refill = collector._arm_tail_refill(settings, exchange, store, max_bars=3)
        long_pairs: Sequence[tuple[str, str]] = list(refill([(ARM, "1m")]) or ())
        base: Sequence[tuple[str, str]] = list(refill([(BTC, "1m")]) or ())

    assert long_pairs == [(ARM, "1m")]  # 5봉 > 한도 3봉 → 넘긴다
    assert base == [] and exchange.calls == []  # 기본 격자는 받지도 넘기지도 않는다


# -- (d) REST 폴러가 접속 스냅샷보다 먼저 돌아도 영구 구멍이 안 생긴다 (PM 변경 요청) ---------


def _bars_between(db: Path, symbol: str, timeframe: str, start: int, end: int) -> list[int]:
    import sqlite3

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT open_time FROM ohlcv WHERE symbol=? AND timeframe=?"
            " AND open_time BETWEEN ? AND ? ORDER BY open_time",
            (symbol, timeframe, start, end),
        ).fetchall()
    return [int(r[0]) for r in rows]


def _poller_then_connect(tmp_path: Path, *, hold: bool) -> tuple[int, list[int], int]:
    """새 종목 1m이 3,000분 비어 있는 채로 기동 — 폴러 첫 바퀴가 접속(스냅샷)보다 먼저 온다.

    돌려주는 값: (원래 마지막 봉, 그 뒤 저장된 봉들, 따라잡기 뒤 마지막 봉).
    """
    from data.collection_plan import backfill_pairs
    from data.rest_poll import ClosedBarPoller

    now = _now_min()
    old_last = now - 3_000 * MIN  # 폴러 깊이 상한(500)보다도, 기동 꼬리 한도(1,000)보다도 길다
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store:
        store.upsert_candles(
            [_bar(BTC, "1h", now // HOUR * HOUR - HOUR), _bar(ARM, "1m", old_last)]
        )
    settings = _settings(tmp_path, collector_rest_poll_enabled=True)
    exchange = _RecordingExchange()
    pair = (ARM, "1m")

    with OhlcvStore(db) as store:

        def arm_catch_up(snapshot: Any) -> int:
            return backfill_pairs(
                exchange,
                store,
                [pair],
                lookback_days=settings.lookback_days_for,
                pause_seconds=0,
                last_open_times=snapshot,
            )

        catchup = collector.TailCatchup(
            exchange,
            store,
            settings,
            extra=arm_catch_up,
            extra_pairs=lambda: [pair],
        )
        poller = ClosedBarPoller(
            exchange,
            store,
            lambda: [pair],
            max_bars=500,
            hold_long_gaps=catchup.startup_pending if hold else None,
            sleeper=lambda _s: None,
        )

        first = poller.poll_once()  # 기동 직후 첫 바퀴 — 아직 접속 전(스냅샷 없음)
        assert first.held == (1 if hold else 0)

        async def connect_and_catch_up() -> None:
            catchup.schedule()  # 접속 성립 — 접속 순간 스냅샷
            await _wait_until(lambda: not catchup.startup_pending(), timeout=10)

        asyncio.run(connect_and_catch_up())
        poller.poll_once()  # 따라잡기 뒤의 평소 바퀴
        last_after = store.last_open_time(*pair)

    assert last_after is not None
    return old_last, _bars_between(db, ARM, "1m", old_last, last_after), last_after


def test_poller_before_connect_leaves_no_hole_in_a_long_gap(tmp_path: Path) -> None:
    """(d) 폴러 켠 채로 첫 바퀴가 접속보다 먼저 와도, 따라잡기 뒤 그 시리즈에 내부 구멍이 없다."""
    old_last, bars, last_after = _poller_then_connect(tmp_path, hold=True)

    assert last_after >= _now_min() - 2 * MIN  # 현재까지 따라왔다
    expected = (last_after - old_last) // MIN + 1
    assert len(bars) == expected, f"구멍 {expected - len(bars)}봉"


def test_without_the_hold_the_poller_would_leave_a_hole(tmp_path: Path) -> None:
    """(d) 돌연변이 대조 — 기다림을 빼면(옛 동작) 최근 500봉만 들어가고 그 앞이 구멍으로 남는다."""
    old_last, bars, last_after = _poller_then_connect(tmp_path, hold=False)

    expected = (last_after - old_last) // MIN + 1
    assert len(bars) < expected  # 이 모양이 이번 수정이 막는 영구 구멍이다


def test_collector_hands_the_catchup_gate_to_the_poller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(d) 배선 — 수집기는 폴러에 「첫 따라잡기 전」 신호를 넘기고, 따라잡기가 끝나면 풀린다."""
    events: list[str] = []
    arm = _SlowArm(events)
    captured: dict[str, Any] = {}

    monkeypatch.setattr(collector, "create_collector_exchange", lambda settings: object())
    monkeypatch.setattr(collector, "backfill_all", lambda *a, **k: {})
    monkeypatch.setattr(collector, "_build_arm_collection", lambda settings, store: arm)

    async def no_funding(settings: Settings, exchange: Any) -> None:
        return None

    async def idle_universe(settings: Settings, store: Any) -> None:
        await asyncio.Event().wait()

    class _IdlePoller:
        async def run(self) -> None:
            await asyncio.Event().wait()

    def fake_build_poller(*a: Any, **kw: Any) -> _IdlePoller:
        captured.update(kw)
        return _IdlePoller()

    monkeypatch.setattr(collector, "_backfill_funding", no_funding)
    monkeypatch.setattr(collector, "_run_universe", idle_universe)
    monkeypatch.setattr(collector, "_build_rest_poller", fake_build_poller)

    observed: list[bool] = []

    async def fake_stream(store: Any, arm_: Any, **kwargs: Any) -> None:
        hold = captured["hold_long_gaps"]
        observed.append(hold())  # 접속 전 — 첫 따라잡기 전이라 참
        kwargs["on_connect"]()
        await _wait_until(lambda: "catch_up_start" in events)
        observed.append(hold())  # 따라잡기 도는 중 — 여전히 참
        arm.release.set()
        await _wait_until(lambda: not hold())  # 끝나면 풀린다
        observed.append(hold())
        raise RuntimeError("stop")

    monkeypatch.setattr(collector, "_stream_arm_plan", fake_stream)
    settings = _settings(tmp_path, collector_rest_poll_enabled=True)

    try:
        with pytest.raises(RuntimeError, match="stop"):
            asyncio.run(collector.run_collector(settings, run_stream=True, repair_on_start=False))
    finally:
        arm.release.set()

    assert observed == [True, True, False]
