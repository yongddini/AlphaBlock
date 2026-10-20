"""WAN-461: 웹소켓 연결 하나가 조용히 먹통이 되면 감지해서 전부 다시 붙는다.

2026-10-20 05:20 KST부터 연결 2/2의 11종목이 136분 동안 1분봉을 남기지 못했는데 끊김·재접속·
에러 기록이 하나도 없었다. 연결마다 걸린 유휴 감시(WAN-173)는 「메시지가 오나」만 보므로, 이
테스트들은 두 모양을 **연결 2개 중 하나만** 멎게 해서 확인한다:

* **무수신**(half-open) — 유휴 감시가 그 연결을 「연결 2/2」로 짚어 끝내고 나머지도 닫는다.
* **수신은 되는데 저장 0건** — 새 「저장 없음」 감시(`StreamNoProgress`)가 같은 일을 한다.

어느 쪽이든 상위 재접속 루프가 다시 붙이고, 접속 직후 훅(= 꼬리 따라잡기 예약)이 다시 불린다.
덤: 연속 재접속 카운터가 정상 접속 뒤 1로 돌아간다(옛 구현은 프로세스 수명 내내 누적했다).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

import data.collector as collector
import data.stream as stream_mod
from config.settings import Settings
from data.models import Candle
from data.stream import consume_messages, progress_timeout_for
from data.watchdog import (
    RecoveryEvent,
    StreamNoProgress,
    StreamStalled,
    run_with_recovery,
)

M = 60_000
#: 연결 2개로 나뉘는 쌍(상한 200) — 앞 200개가 연결 1/2, 나머지가 연결 2/2.
PAIRS = [(f"S{i:03d}/USDT:USDT", "1m") for i in range(300)]


def _closed_kline(symbol: str, open_time: int) -> str:
    ws_symbol = symbol.split(":", 1)[0].replace("/", "")
    return json.dumps(
        {
            "stream": f"{ws_symbol.lower()}@kline_1m",
            "data": {
                "e": "kline",
                "k": {
                    "s": ws_symbol,
                    "i": "1m",
                    "t": open_time,
                    "o": "1",
                    "h": "1",
                    "l": "1",
                    "c": "1",
                    "v": "1",
                    "x": True,
                },
            },
        }
    )


class _Store:
    """저장 호출만 세는 가짜 저장소."""

    def __init__(self) -> None:
        self.candles: list[Candle] = []

    def upsert_candles(self, candles: list[Candle]) -> int:
        self.candles.extend(candles)
        return len(candles)


class _Ws:
    """연결 하나의 가짜 웹소켓.

    `behaviour`: healthy(확정봉) · silent(무수신) · junk(저장 안 되는 메시지).
    """

    def __init__(self, index: int, log: list[str], behaviour: str) -> None:
        self._index = index
        self._log = log
        self._behaviour = behaviour
        self._n = 0

    async def recv(self) -> str:
        self._n += 1
        try:
            if self._behaviour == "silent":
                await asyncio.sleep(30)  # 열린 채 아무것도 안 온다(half-open)
                raise AssertionError("unreachable")
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self._log.append(f"cancel{self._index}")
            raise
        if self._behaviour == "junk":
            return "{}"  # 받기는 하는데 kline이 아니다 → 저장 0건
        return _closed_kline(PAIRS[0][0], self._n * M)


def _install(monkeypatch: pytest.MonkeyPatch, behaviours: list[str]) -> list[str]:
    """`connect`를 가짜로 — 접속 순서대로 `behaviours`를 하나씩 쓴다(재접속하면 다음 것)."""
    log: list[str] = []
    counter = {"n": 0}

    class _Connect:
        def __init__(self, url: str) -> None:
            self._index = counter["n"]
            counter["n"] += 1

        async def __aenter__(self) -> _Ws:
            log.append(f"connect{self._index}")
            return _Ws(self._index, log, behaviours[self._index])

        async def __aexit__(self, *exc: object) -> bool:
            log.append(f"close{self._index}")
            return False

    monkeypatch.setattr(stream_mod, "connect", lambda url: _Connect(url))
    return log


def _run(coro: Any, timeout: float = 5.0) -> None:
    async def main() -> None:
        await asyncio.wait_for(coro, timeout=timeout)

    asyncio.run(main())


# --- (4) 연결 2개 중 하나만 무수신 ------------------------------------------------------


def test_one_silent_connection_is_named_and_ends_the_whole_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """연결 1/2는 계속 저장하는데 연결 2/2만 조용하다 — 그 연결을 짚어 전부 끝낸다."""
    log = _install(monkeypatch, ["healthy", "silent"])
    store = _Store()
    with pytest.raises(StreamStalled) as info:
        _run(
            stream_mod.stream_kline_pairs(
                store,  # type: ignore[arg-type]
                PAIRS,
                idle_timeout_seconds=0.3,
            )
        )
    assert (info.value.connection, info.value.connections) == (2, 2)
    assert "연결 2/2" in str(info.value)
    assert not isinstance(info.value, StreamNoProgress)
    assert store.candles, "건강한 연결 1/2는 그동안 계속 저장했다(전체 하트비트로는 못 잡는 모양)"
    assert "cancel0" in log  # 건강한 연결도 닫는다(반쪽만 남기지 않는다)
    assert {"close0", "close1"} <= set(log)


def test_silent_connection_reconnects_and_reschedules_the_catchup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재접속 루프까지 — 제한 시간 안에 다시 붙고, 접속 직후 훅(따라잡기 예약)이 다시 불린다."""
    log = _install(monkeypatch, ["healthy", "silent", "healthy", "silent"])
    store = _Store()
    catchups: list[int] = []
    events: list[RecoveryEvent] = []

    async def stream_once() -> None:
        await stream_mod.stream_kline_pairs(
            store,  # type: ignore[arg-type]
            PAIRS,
            idle_timeout_seconds=0.3,
            on_connect=lambda: catchups.append(1),
        )

    async def no_sleep(_: float) -> None:
        return None

    _run(run_with_recovery(stream_once, on_recover=events.append, max_reconnects=1, sleep=no_sleep))
    assert log.count("connect0") == 1 and "connect2" in log and "connect3" in log
    assert len(catchups) == 2  # 처음 접속 + 재접속 — 빠진 봉을 따라잡기로 메운다
    assert len(events) == 1
    assert "연결 2/2" in events[0].reason


# --- 「수신은 되는데 저장 0건」 ---------------------------------------------------------


def test_connection_receiving_but_storing_nothing_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """유휴 감시가 안 걸리는 모양(메시지는 계속 온다) — 저장 없음 감시가 연결 2/2를 짚는다."""
    log = _install(monkeypatch, ["healthy", "junk"])
    monkeypatch.setattr(stream_mod, "progress_timeout_for", lambda pairs, grace: 0.3)
    store = _Store()
    with pytest.raises(StreamNoProgress) as info:
        _run(
            stream_mod.stream_kline_pairs(
                store,  # type: ignore[arg-type]
                PAIRS,
                idle_timeout_seconds=0.2,  # 메시지가 0.01초마다 와서 이건 안 걸린다
                progress_grace_seconds=1.0,
            )
        )
    exc = info.value
    assert (exc.connection, exc.connections) == (2, 2)
    assert exc.messages > 0 and exc.last_message == "{}"
    assert "저장 0건" in str(exc) and "연결 2/2" in str(exc)
    assert "cancel0" in log


def test_without_progress_grace_the_junk_connection_is_not_judged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """감시를 안 켜면(None) 예전 동작 그대로 — 메시지만 오면 끝나지 않는다."""
    _install(monkeypatch, ["healthy", "junk"])

    async def main() -> None:
        task = asyncio.create_task(
            stream_mod.stream_kline_pairs(_Store(), PAIRS, idle_timeout_seconds=0.2)  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.6)
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


def test_consume_messages_progress_clock() -> None:
    """가짜 시계 — 저장이 있으면 한도가 리셋되고, 저장 없이 한도를 넘기면 그 메시지에서 던진다."""
    now = {"t": 0.0}

    def clock() -> float:
        return now["t"]

    symbol_map = {"S000USDT": PAIRS[0][0]}
    seq = ["{}", _closed_kline(PAIRS[0][0], M), "{}", "{}", "{}"]
    times = iter([50.0, 170.0, 200.0, 300.0, 351.0])

    async def stepping() -> AsyncIterator[str]:
        for item in seq:
            now["t"] = next(times)
            yield item

    store = _Store()
    with pytest.raises(StreamNoProgress) as info:
        asyncio.run(
            consume_messages(
                stepping(),
                store,  # type: ignore[arg-type]
                symbol_map,
                progress_timeout_seconds=180.0,
                clock=clock,
            )
        )
    # 170초에 저장 → 그 뒤 180초가 지난 351초 메시지에서 걸린다(저장 뒤 받은 3건).
    assert len(store.candles) == 1
    assert info.value.messages == 3
    assert info.value.connection is None and "연결" not in str(info.value)

    # 저장 없이도 한도 안이면 끝까지 소비한다.
    store2 = _Store()
    assert (
        asyncio.run(
            consume_messages(
                _aiter(["{}", "{}"]),
                store2,  # type: ignore[arg-type]
                symbol_map,
                progress_timeout_seconds=180.0,
            )
        )
        == 0
    )


def test_progress_timeout_is_shortest_timeframe_plus_grace() -> None:
    assert progress_timeout_for([("A", "15m"), ("A", "1m"), ("B", "4h")], 120.0) == 180.0
    assert progress_timeout_for([("A", "15m"), ("B", "1h")], 120.0) == 900.0 + 120.0
    assert progress_timeout_for([("A", "1m")], None) is None
    assert progress_timeout_for([], 120.0) is None


# --- (3) 알림 한 줄 -------------------------------------------------------------------


def test_recovery_notifier_names_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str | None]] = []

    class _Telegram:
        def send_message(self, text: str, *, parse_mode: str | None = "Markdown") -> bool:
            sent.append((text, parse_mode))
            return True

    monkeypatch.setattr(collector, "build_telegram_client", lambda settings: _Telegram())
    notify = collector._build_recovery_notifier(Settings())
    stalled = StreamStalled(90.0)
    stalled.tag_connection(2, 2)
    from data.watchdog import _describe_end

    notify(RecoveryEvent(attempt=1, reason=_describe_end(stalled), delay_seconds=1.0))
    ((text, parse_mode),) = sent
    assert "연결 2/2" in text and "90s" in text and "→ 재접속" in text
    assert parse_mode is None  # 평문(WAN-321)


# --- (5) 연속 재접속 카운터 리셋 ---------------------------------------------------------


def test_reconnect_streak_resets_after_a_healthy_connection() -> None:
    """빠른 실패는 백오프가 자라고, 오래 버틴 접속 뒤에는 #1 · 1초로 돌아간다."""
    now = {"t": 0.0}
    durations = iter([0.0, 0.0, 0.0, 500.0, 0.0, 0.0])  # 처음 1회 + 재접속 5회
    events: list[RecoveryEvent] = []

    async def stream_once() -> None:
        now["t"] += next(durations)
        raise StreamStalled(90.0)

    async def no_sleep(_: float) -> None:
        return None

    asyncio.run(
        run_with_recovery(
            stream_once,
            on_recover=events.append,
            max_reconnects=5,
            sleep=no_sleep,
            clock=lambda: now["t"],
            healthy_after_seconds=120.0,
        )
    )
    assert [e.attempt for e in events] == [1, 2, 3, 1, 2]
    assert [e.delay_seconds for e in events] == [1.0, 2.0, 4.0, 1.0, 2.0]


def test_default_settings_wire_the_new_guards() -> None:
    settings = Settings()
    assert settings.collector_stream_progress_grace_seconds == 120.0
    assert settings.collector_reconnect_healthy_after_seconds == 120.0
    assert settings.live_trading is False
