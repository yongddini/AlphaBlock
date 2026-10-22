"""WAN-470: 수집기가 「살아서 아무것도 안 하는」 상태로 4.5시간 멈췄는데 세 겹 감시가 다 안 걸렸다.

2026-10-22 04:00 KST에 웹소켓 연결 1/2가 메시지는 계속 보내면서 새 봉을 하나도 안 줬다(모든 TF가
정확히 04:00 마감 봉에서 멈춤 — 이미 저장한 확정봉의 반복으로 본다). 감시 셋이 모두 「메시지가
오나」 또는 「저장 호출을 했나」를 봐서 통과시켰다. 이 테스트들은 **동작으로** 고정한다:

* §0 — 웹소켓이 같은 확정봉만 되풀이해도 REST 이중 수집이 1분봉 최신 봉을 2분 안으로 계속 민다.
* 진행 = 새 봉: 같은 봉 반복은 「저장 없음」 감시에 걸리고, 하트비트·워치독을 찍지 않는다.
* 프로세스 워치독: 메시지는 오는데 새 봉이 없으면 종료를 부른다.
* 워치 백스톱: 기본 격자가 오래 비면 수집기에 SIGTERM(쿨다운·하루 상한 · 팔 쌍은 무시).
* 진단: SIGUSR1 태스크 덤프 · 복구 불가 예외는 시끄럽게.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import signal
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

import data.collector as collector
from common.heartbeat import HeartbeatStore
from config.settings import Settings
from dashboard.health import (
    CollectorStatus,
    HealthLevel,
    OverallBadge,
    RunnerStatus,
    SeriesFreshness,
)
from dashboard.health_data import HealthView
from data.models import Candle
from data.rest_poll import ClosedBarPoller, missing_since, next_poll_delay
from data.storage import OhlcvStore
from data.stream import consume_messages
from data.watchdog import (
    HeartbeatWatchdog,
    StreamNoProgress,
    dump_asyncio_tasks,
    install_task_dump_signal,
    run_with_recovery,
)
from live import health_watch
from live.health_watch import (
    COLLECTOR_RESTART_ALERT_KEY,
    CollectorRestartPolicy,
    HealthWatch,
    WatchState,
    WatchStateStore,
    collector_restart_reason,
    decide_collector_restart,
    signal_collector,
)

M = 60_000
H = 3_600_000
SYM = "BTC/USDT:USDT"
SYMBOL_MAP = {"BTCUSDT": SYM}
#: 사고 시각에 해당하는 분 경계(임의 — 2026-10-21 19:00 UTC = 10-22 04:00 KST).
T0 = 1_761_073_200_000


def _kline(open_time: int, *, symbol: str = "BTCUSDT", tf: str = "1m", closed: bool = True) -> str:
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@kline_{tf}",
            "data": {
                "e": "kline",
                "k": {
                    "s": symbol,
                    "i": tf,
                    "t": open_time,
                    "o": "1",
                    "h": "2",
                    "l": "0.5",
                    "c": "1.5",
                    "v": "10",
                    "x": closed,
                },
            },
        }
    )


class _Clock:
    """테스트 시계(초) — 메시지를 줄 때마다 앞으로 간다."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def _messages(items: list[str], clock: _Clock, step: float) -> AsyncIterator[str]:
    for item in items:
        clock.t += step
        yield item


# --- 진행 = 새 봉 (코멘트 할 일 1) -------------------------------------------------------


def test_repeated_closed_bar_is_not_progress_and_trips_no_progress(tmp_path: Path) -> None:
    """같은 확정봉만 되풀이해서 오면 그건 진행이 아니다 — 한도 안에 `StreamNoProgress`.

    옛 판정은 같은 봉을 다시 저장해도 진행으로 세서 4.5시간 동안 아무것도 안 걸렸다. 되돌리면
    (같은 봉 재저장을 진행으로 세면) 이 테스트가 실패한다.
    """
    clock = _Clock()
    beats: list[float] = []
    progress: list[int] = []
    stale = _kline(T0 - M)  # 03:59 봉(04:00 마감)을 계속 되풀이
    with OhlcvStore(tmp_path / "s.db") as store, pytest.raises(StreamNoProgress) as info:
        asyncio.run(
            consume_messages(
                _messages([stale] * 500, clock, step=1.0),
                store,
                SYMBOL_MAP,
                heartbeat=lambda: beats.append(clock.t),
                on_progress=lambda c: progress.append(c.open_time),
                progress_timeout_seconds=180.0,
                clock=clock,
            )
        )
    exc = info.value
    assert progress == [T0 - M], "첫 봉 한 번만 새 봉이고 그 뒤 반복은 진행이 아니다"
    assert 180.0 <= clock.t <= 182.0, "한도(가장 짧은 TF + 여유) 안에 걸린다"
    assert len(beats) >= 180, "메시지마다 하트비트는 찍혔다 — 그래서 하트비트로는 못 잡았다"
    assert exc.recent_repeats and all("x=true" in r for r in exc.recent_repeats)
    assert all("BTC/USDT:USDT 1m" in r for r in exc.recent_repeats)


def test_advancing_bars_never_trip_and_signal_each_new_bar(tmp_path: Path) -> None:
    clock = _Clock()
    progress: list[int] = []
    items = []
    for minute in range(10):
        items += [_kline(T0 + minute * M, closed=False)] * 59 + [_kline(T0 + minute * M)]
    with OhlcvStore(tmp_path / "s.db") as store:
        stored = asyncio.run(
            consume_messages(
                _messages(items, clock, step=1.0),
                store,
                SYMBOL_MAP,
                on_progress=lambda c: progress.append(c.open_time),
                progress_timeout_seconds=180.0,
                clock=clock,
            )
        )
        assert store.last_open_time(SYM, "1m") == T0 + 9 * M
    assert stored == 10
    assert progress == [T0 + m * M for m in range(10)]


def test_stream_progress_ignores_old_bar_replayed_after_reconnect(tmp_path: Path) -> None:
    """재접속하면 연결 단위 판정은 처음부터지만, 프로세스 신호는 옛 봉 반복을 진행으로 안 센다."""
    signals = collector.ProgressSignals(HeartbeatStore(tmp_path / "hb.json"))
    now = {"t": 0.0}
    signals.data._now = signals.stream._now = lambda: now["t"]  # type: ignore[method-assign]
    signals.data.mark()
    signals.stream.mark()
    bar = Candle(symbol=SYM, timeframe="1m", open_time=T0, open=1, high=1, low=1, close=1, volume=1)
    signals.on_stream_progress(bar)
    now["t"] = 600.0
    signals.on_stream_progress(bar)  # 재접속 뒤 같은 옛 봉
    assert signals.stream.idle_seconds() == 600.0
    assert signals.data.idle_seconds() == 600.0
    signals.on_stream_progress(dataclasses.replace(bar, open_time=T0 + M))
    assert signals.stream.idle_seconds() == 0.0


def test_startup_marks_both_trackers_and_heartbeat(tmp_path: Path) -> None:
    hb = HeartbeatStore(tmp_path / "hb.json")
    signals = collector.ProgressSignals(hb)
    now = {"t": 100.0}
    signals.data._now = signals.stream._now = lambda: now["t"]  # type: ignore[method-assign]
    now["t"] = 200.0
    signals.mark_startup()
    assert signals.data.idle_seconds() == 0.0 and signals.stream.idle_seconds() == 0.0
    beat = hb.load()
    assert beat.updated_at is not None and beat.pid == os.getpid()


# --- 프로세스 워치독 (할 일 5) -------------------------------------------------------------


def test_watchdog_fires_when_messages_flow_but_no_new_bar(tmp_path: Path) -> None:
    """메시지(옛 하트비트)는 계속 오는데 새 봉이 0건 → 워치독이 종료를 부른다.

    수집기 배선 그대로다: 메시지마다 부르는 콜백은 워치독을 안 찍고, 새 봉 신호(`on_progress` →
    `ProgressSignals.on_stream_progress`)만 찍는다. 옛 배선(메시지마다 `tracker.mark`)으로 되돌리면
    워치독이 끝내 안 걸려 이 테스트가 실패한다.
    """
    clock = _Clock()
    signals = collector.ProgressSignals(HeartbeatStore(tmp_path / "hb.json"))
    signals.data._now = signals.stream._now = clock  # type: ignore[method-assign]
    signals.data.mark()
    fired: list[float] = []
    watchdog = HeartbeatWatchdog(signals.data, timeout_seconds=300.0, on_stale=fired.append)
    messages = 0

    class _Fired(Exception):
        pass

    def _on_message() -> None:
        nonlocal messages
        messages += 1
        if watchdog.poll_once():
            raise _Fired

    with OhlcvStore(tmp_path / "s.db") as store, pytest.raises(_Fired):
        asyncio.run(
            consume_messages(
                _messages([_kline(T0 - M)] * 1000, clock, step=1.0),
                store,
                SYMBOL_MAP,
                heartbeat=_on_message,
                on_progress=signals.on_stream_progress,
            )
        )
    assert fired and 300.0 <= fired[0] <= 302.0
    assert messages >= 300, "메시지는 계속 왔다 — 메시지 기준이었다면 영원히 살아 있음"


# --- §0 REST 이중 수집 ------------------------------------------------------------------


class _Exchange:
    """since부터 지금까지(형성 중 봉 포함) 1분봉을 주는 가짜 거래소."""

    def __init__(self, now_ms: list[int]) -> None:
        self._now = now_ms
        self.calls: list[tuple[str, str, int, int | None]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        assert since is not None and limit is not None
        self.calls.append((symbol, timeframe, since, limit))
        step = {"1m": M, "15m": 15 * M, "1h": H}[timeframe]
        out: list[list[float]] = []
        t = since
        while t <= self._now[0] and len(out) < limit:
            out.append([t, 1.0, 2.0, 0.5, 1.5, 10.0])
            t += step
        return out


def test_frozen_websocket_still_advances_1m_within_two_minutes(tmp_path: Path) -> None:
    """웹소켓 연결이 같은 확정봉만 되풀이해도 그 종목 1분봉 최신 봉이 2분 안에 계속 앞으로 간다.

    완료 기준(§0): 얼린 연결 + REST 이중 수집을 함께 돌리고, 분마다 「최신 봉이 지금에서 2분 안」을
    확인한다.
    """
    now = [T0 + 5_000]
    exchange = _Exchange(now)
    lags: list[int] = []

    with OhlcvStore(tmp_path / "s.db") as store:
        store.upsert_candles(
            [
                Candle(
                    symbol=SYM,
                    timeframe="1m",
                    open_time=T0 - M,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                )
            ]
        )

        async def _sleep(seconds: float) -> None:
            now[0] += int(seconds * 1000)
            await asyncio.sleep(0)

        def _on_progress() -> None:
            last = store.last_open_time(SYM, "1m")
            assert last is not None
            lags.append(now[0] - last)

        poller = ClosedBarPoller(
            exchange,
            store,
            lambda: [(SYM, "1m")],
            offset_seconds=5.0,
            on_progress=_on_progress,
            now_ms=lambda: now[0],
            sleep=_sleep,
            sleeper=lambda _s: None,
        )

        async def _frozen_ws() -> None:
            # 얼린 연결: 03:59 봉(04:00 마감)만 계속 — 저장은 하되 새 봉은 아니다.
            async def _forever() -> AsyncIterator[str]:
                while True:
                    await asyncio.sleep(0)
                    yield _kline(T0 - M)

            await consume_messages(_forever(), store, SYMBOL_MAP)

        async def _main() -> None:
            ws = asyncio.create_task(_frozen_ws())
            await poller.run(max_cycles=30)
            ws.cancel()
            await asyncio.gather(ws, return_exceptions=True)

        asyncio.run(_main())
        assert store.last_open_time(SYM, "1m") == (now[0] // M - 1) * M

    assert len(lags) == 30, "분마다 진행 신호(봉을 넣었다)"
    assert max(lags) <= 2 * M, f"1분봉이 2분 안에 계속 앞으로 간다: {max(lags) / M:.2f}분"
    assert all(limit < 100 for *_, limit in exchange.calls), "요청당 가중치 1(limit < 100)"


def test_poller_makes_no_request_when_websocket_already_stored_the_bar(tmp_path: Path) -> None:
    now = [T0 + 5_000]
    exchange = _Exchange(now)
    with OhlcvStore(tmp_path / "s.db") as store:
        store.upsert_candles(
            [
                Candle(
                    symbol=SYM,
                    timeframe=tf,
                    open_time=open_time,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                )
                for tf, open_time in (("1m", T0 - M), ("15m", T0 - 15 * M), ("1h", T0 - H))
            ]
        )
        progressed: list[bool] = []
        poller = ClosedBarPoller(
            exchange,
            store,
            lambda: [(SYM, "1m"), (SYM, "15m"), (SYM, "1h"), ("NEW/USDT:USDT", "1m")],
            on_progress=lambda: progressed.append(True),
            now_ms=lambda: now[0],
        )
        result = asyncio.run(poller.run_once())
    assert exchange.calls == [], "웹소켓이 이미 넣었으면 요청이 안 나간다"
    assert result.checked == 3, "저장분 없는 새 쌍은 건드리지 않는다(팔 백필 몫)"
    assert result.progressed and progressed == [True], "현재까지 와 있음 = 진행"


def test_poller_without_success_is_not_progress(tmp_path: Path) -> None:
    class _Broken:
        def fetch_ohlcv(self, *a: object, **k: object) -> list[list[float]]:
            raise RuntimeError("REST 실패")

    now = [T0 + 5 * M]
    with OhlcvStore(tmp_path / "s.db") as store:
        store.upsert_candles(
            [
                Candle(
                    symbol=SYM,
                    timeframe="1m",
                    open_time=T0 - M,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                )
            ]
        )
        progressed: list[bool] = []
        poller = ClosedBarPoller(
            _Broken(),
            store,
            lambda: [(SYM, "1m")],
            on_progress=lambda: progressed.append(True),
            now_ms=lambda: now[0],
        )
        result = asyncio.run(poller.run_once())
    assert result.errors == 1 and not result.progressed and progressed == []


def test_missing_since_only_after_the_next_bar_closed() -> None:
    assert missing_since(None, "1m", T0, max_bars=500) is None
    assert missing_since(T0 - M, "1m", T0 + 5_000, max_bars=500) is None  # 03:59 있음 = 현재
    assert missing_since(T0 - 2 * M, "1m", T0 + 5_000, max_bars=500) == T0 - M
    # 4시간 밀림 → 최근 max_bars개만(1분 격자 유지)
    since = missing_since(T0 - 240 * M, "1m", T0 + 5_000, max_bars=100)
    assert since is not None and since % M == T0 % M and (T0 - since) // M == 100
    # 주봉: 다음 봉 = 마지막 봉 + 1주(월요일 정렬이 그대로) · 그 주가 끝나야 빠진 봉
    monday = 1_760_918_400_000  # 2025-10-20 00:00 UTC(월)
    week = 7 * 24 * H
    assert missing_since(monday, "1w", monday + 2 * week - 1, max_bars=500) is None
    assert missing_since(monday, "1w", monday + 2 * week, max_bars=500) == monday + week


def test_next_poll_delay_lands_on_minute_plus_offset() -> None:
    assert next_poll_delay(T0, 5.0) == 5.0
    assert next_poll_delay(T0 + 5_000, 5.0) == 60.0
    assert next_poll_delay(T0 + 30_000, 5.0) == 35.0


def test_watchdog_timeouts_never_shorter_than_shortest_tf(tmp_path: Path) -> None:
    base = Settings(_env_file=None, db_path=str(tmp_path / "x.db"))
    assert collector.watchdog_timeouts(base) == (300.0, 900.0)
    slow = Settings(
        _env_file=None,
        db_path=str(tmp_path / "x.db"),
        timeframes=["15m", "1h"],
        universe_enabled=False,
    )
    data_timeout, stream_timeout = collector.watchdog_timeouts(slow)
    assert data_timeout == 15 * 60 + 120.0  # 15m이 가장 짧으면 그 간격 + 여유
    assert stream_timeout >= data_timeout


# --- 워치 백스톱 (코멘트 할 일 3) ---------------------------------------------------------


def _fresh(symbol: str, tf: str, last: int, now: int, level: HealthLevel) -> SeriesFreshness:
    return SeriesFreshness(
        symbol=symbol,
        timeframe=tf,
        last_open_time=last,
        bar_count=10,
        expected_interval_ms=M,
        lag_ms=now - last,
        level=level,
    )


def _view(series: list[SeriesFreshness]) -> HealthView:
    return HealthView(
        now_ms=T0,
        overall=OverallBadge(level=HealthLevel.STALE, label="x"),
        freshness=series,
        funding=[],
        collector=CollectorStatus(ran=True, last_beat_ms=0, lag_ms=0, level=HealthLevel.OK),
        runner=RunnerStatus(
            ran=False,
            last_poll_ms=None,
            last_notification_ms=None,
            lag_ms=None,
            level=HealthLevel.UNKNOWN,
        ),
        positions=[],
        recent_events=[],
    )


def _policy(**kw: object) -> CollectorRestartPolicy:
    defaults: dict[str, object] = {
        "base_pairs": frozenset({(SYM, "1m")}),
        "after_ms": 10 * M,
        "cooldown_ms": 30 * M,
        "max_per_day": 2,
    }
    defaults.update(kw)
    return CollectorRestartPolicy(**defaults)  # type: ignore[arg-type]


def test_restart_reason_only_for_base_grid_beyond_threshold() -> None:
    now = T0 + 4 * H
    stalled = _fresh(SYM, "1m", T0 - M, now, HealthLevel.STALE)
    arm = _fresh("NEW/USDT:USDT", "1m", T0 - 10 * H, now, HealthLevel.STALE)
    reason = collector_restart_reason(_view([stalled, arm]), _policy(), now_ms=now)
    assert reason is not None and "기본 격자 1개" in reason and SYM in reason
    # 팔 쌍만 비면(주간 교체 날 새 종목) 재시작하지 않는다.
    assert collector_restart_reason(_view([arm]), _policy(), now_ms=now) is None
    # 다음 봉이 닫힌 지 10분이 안 됐으면 아직
    recent = _fresh(SYM, "1m", now - 2 * M - 9 * M, now, HealthLevel.STALE)
    assert collector_restart_reason(_view([recent]), _policy(), now_ms=now) is None


def test_restart_decision_cooldown_and_daily_cap() -> None:
    now = T0
    assert decide_collector_restart(WatchState(), _policy(), now_ms=now) == ("restart", 0)
    one = WatchState(collector_restarts=[now - 5 * M])
    assert decide_collector_restart(one, _policy(), now_ms=now)[0] == "cooldown"
    two = WatchState(collector_restarts=[now - 3 * H, now - 2 * H])
    assert decide_collector_restart(two, _policy(), now_ms=now) == ("capped", 2)
    old = WatchState(collector_restarts=[now - 30 * H, now - 25 * H])
    assert decide_collector_restart(old, _policy(), now_ms=now) == ("restart", 0)


def test_health_watch_restarts_collector_once_then_waits(tmp_path: Path) -> None:
    now = [T0 + 4 * H]
    view = _view([_fresh(SYM, "1m", T0 - M, now[0], HealthLevel.STALE)])
    sent: list[str] = []
    restarts: list[int] = []

    def _restart() -> tuple[bool, str]:
        restarts.append(now[0])
        return True, "pid 4242에 SIGTERM"

    watch = HealthWatch(
        view_provider=lambda: view,
        notify=lambda text: sent.append(text) or True,
        store=WatchStateStore(tmp_path / "w.json"),
        cooldown_seconds=3600,
        interval_seconds=600,
        now_ms=lambda: now[0],
        restart_policy=_policy(),
        restart_collector=_restart,
    )
    watch.check_once()
    assert len(restarts) == 1
    assert any("수집기 자동 재시작함" in t and "1/2" in t for t in sent)
    state = WatchStateStore(tmp_path / "w.json").load()
    assert state.collector_restarts == [now[0]]

    now[0] += 10 * M  # 쿨다운 안 — 다시 안 죽인다
    watch.check_once()
    assert len(restarts) == 1

    now[0] += 30 * M
    watch.check_once()  # 두 번째(상한 2)
    now[0] += 31 * M
    sent.clear()
    watch.check_once()  # 상한 도달 → 재시작 대신 사람을 부른다
    assert len(restarts) == 2
    assert any("자동 재시작 중단" in t for t in sent)
    assert COLLECTOR_RESTART_ALERT_KEY in WatchStateStore(tmp_path / "w.json").load().active


def test_health_watch_reports_failed_restart(tmp_path: Path) -> None:
    now = T0 + 4 * H
    sent: list[str] = []
    watch = HealthWatch(
        view_provider=lambda: _view([_fresh(SYM, "1m", T0 - M, now, HealthLevel.STALE)]),
        notify=lambda text: sent.append(text) or True,
        store=WatchStateStore(tmp_path / "w.json"),
        cooldown_seconds=3600,
        interval_seconds=600,
        now_ms=lambda: now,
        restart_policy=_policy(),
        restart_collector=lambda: (False, "pid 없음"),
    )
    watch.check_once()
    assert any("자동 재시작 실패" in t and "pid 없음" in t for t in sent)
    assert WatchStateStore(tmp_path / "w.json").load().collector_restarts == []


def test_signal_collector_checks_the_process_before_killing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hb.json"
    assert signal_collector(path)[0] is False  # 파일·pid 없음
    HeartbeatStore(path, label="collector").beat()  # 이 테스트 프로세스의 pid
    ok, detail = signal_collector(path)
    assert ok is False and "수집기가 아닙니다" in detail, "pytest 프로세스에는 보내지 않는다"

    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(health_watch, "_process_command", lambda pid: "uv run alphablock collect")
    monkeypatch.setattr(health_watch.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    ok, detail = signal_collector(path)
    assert ok and killed == [(os.getpid(), signal.SIGTERM)]


def test_watch_policy_uses_collector_base_grid(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, db_path=str(tmp_path / "x.db"))
    policy = health_watch.collector_restart_policy(settings)
    assert policy.base_pairs == frozenset(collector._build_plan_pairs(settings))
    assert policy.enabled and policy.after_ms == 600_000 and policy.max_per_day == 6


# --- 진단 (할 일 4 · 3) -----------------------------------------------------------------


def test_sigusr1_dumps_asyncio_task_stacks(caplog: pytest.LogCaptureFixture) -> None:
    if not hasattr(signal, "SIGUSR1"):
        pytest.skip("SIGUSR1 없음")

    async def _stuck_somewhere() -> None:
        await asyncio.sleep(30)

    async def _main() -> str:
        task = asyncio.create_task(_stuck_somewhere(), name="frozen-stream")
        await asyncio.sleep(0)
        text = dump_asyncio_tasks()
        assert install_task_dump_signal(asyncio.get_running_loop())
        os.kill(os.getpid(), signal.SIGUSR1)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return text

    with caplog.at_level(logging.WARNING, logger="data.watchdog"):
        text = asyncio.run(_main())
    assert "frozen-stream" in text and "_stuck_somewhere" in text
    assert any("frozen-stream" in r.getMessage() for r in caplog.records)


def test_unrecoverable_stream_error_is_logged_loudly(caplog: pytest.LogCaptureFixture) -> None:
    async def _boom() -> None:
        raise ValueError("프로그래밍 오류")

    with caplog.at_level(logging.CRITICAL, logger="data.watchdog"), pytest.raises(ValueError):
        asyncio.run(run_with_recovery(_boom))
    assert any("복구 불가" in r.getMessage() for r in caplog.records)
