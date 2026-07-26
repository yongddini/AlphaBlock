"""data.watchdog 테스트 — 스트림 자동 복구 워치독 (WAN-173).

네트워크·실제 대기 없이 모의 stall(느린/멈춘 async 이터레이터)과 가짜 시계로 검증한다.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from data.watchdog import (
    HeartbeatWatchdog,
    ProgressTracker,
    RecoveryEvent,
    StreamStalled,
    compute_backoff,
    guard_idle,
    run_with_recovery,
)

# -- guard_idle: 스트림 레벨 유휴 워치독 -------------------------------------


async def _yield_then_hang(items: list[str], hang_seconds: float) -> AsyncIterator[str]:
    """항목들을 즉시 내보낸 뒤, 다음 항목 자리에서 오래 멈춘다(half-open 흉내)."""
    for item in items:
        yield item
    await asyncio.sleep(hang_seconds)
    yield "unreachable"


async def _fast_iter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        await asyncio.sleep(0)  # 즉시(유휴 타임아웃보다 훨씬 빠름)
        yield item


def test_guard_idle_raises_on_stall() -> None:
    """조용한 stall(무수신)에서 StreamStalled를 던진다 — 완료기준 1."""

    async def _run() -> list[str]:
        seen: list[str] = []
        async for msg in guard_idle(
            _yield_then_hang(["a", "b"], hang_seconds=10.0),
            idle_timeout_seconds=0.05,
        ):
            seen.append(msg)
        return seen

    with pytest.raises(StreamStalled) as exc_info:
        asyncio.run(_run())
    # stall 전에 도착한 메시지는 정상 통과했어야 한다.
    assert exc_info.value.idle_timeout_seconds == 0.05


def test_guard_idle_passes_messages_when_timely() -> None:
    """정상 간격이면 모든 메시지를 그대로 흘린다 — 오탐 없음(완료기준 3)."""

    async def _run() -> list[str]:
        seen: list[str] = []
        async for msg in guard_idle(_fast_iter(["x", "y", "z"]), idle_timeout_seconds=0.5):
            seen.append(msg)
        return seen

    assert asyncio.run(_run()) == ["x", "y", "z"]


def test_guard_idle_returns_on_normal_end() -> None:
    """소스가 정상 종료(StopAsyncIteration)하면 조용히 끝난다."""

    async def _run() -> int:
        count = 0
        async for _ in guard_idle(_fast_iter(["a"]), idle_timeout_seconds=0.5):
            count += 1
        return count

    assert asyncio.run(_run()) == 1


def test_guard_idle_rejects_nonpositive_timeout() -> None:
    async def _run() -> None:
        async for _ in guard_idle(_fast_iter(["a"]), idle_timeout_seconds=0.0):
            pass

    with pytest.raises(ValueError):
        asyncio.run(_run())


# -- ProgressTracker ---------------------------------------------------------


def test_progress_tracker_idle_seconds() -> None:
    clock = {"t": 100.0}
    tracker = ProgressTracker(now=lambda: clock["t"])
    assert tracker.idle_seconds() == 0.0
    clock["t"] = 130.0
    assert tracker.idle_seconds() == 30.0
    tracker.mark()  # 진행 갱신 → idle 리셋
    assert tracker.idle_seconds() == 0.0
    clock["t"] = 145.0
    assert tracker.idle_seconds() == 15.0


# -- HeartbeatWatchdog: 프로세스 레벨 워치독 --------------------------------


def test_watchdog_poll_triggers_when_stale() -> None:
    """하트비트가 타임아웃 넘게 멈추면 on_stale을 부른다 — 완료기준 2."""
    clock = {"t": 0.0}
    tracker = ProgressTracker(now=lambda: clock["t"])
    fired: list[float] = []
    wd = HeartbeatWatchdog(
        tracker,
        timeout_seconds=90.0,
        poll_seconds=1.0,
        on_stale=fired.append,
    )
    clock["t"] = 60.0
    assert wd.poll_once() is False  # 아직 안 멈춤
    assert fired == []
    clock["t"] = 100.0
    assert wd.poll_once() is True  # 100s > 90s → 재시작 유도
    assert fired == [100.0]


def test_watchdog_poll_no_false_restart_during_normal_operation() -> None:
    """정상 갱신 중에는 재시작을 트리거하지 않는다 — 오탐 방지(완료기준 3)."""
    clock = {"t": 0.0}
    tracker = ProgressTracker(now=lambda: clock["t"])
    fired: list[float] = []
    wd = HeartbeatWatchdog(
        tracker,
        timeout_seconds=90.0,
        poll_seconds=1.0,
        on_stale=fired.append,
    )
    # 정상 봉 간격(타임아웃보다 짧음)으로 계속 진행 갱신 → 절대 트리거 안 됨.
    for step in range(1, 50):
        clock["t"] = step * 30.0  # 30s마다 메시지 수신
        tracker.mark()
        assert wd.poll_once() is False
    assert fired == []


def test_watchdog_thread_fires_on_real_stall() -> None:
    """스레드가 실제로 stall을 감지해 on_stale을 부르고 스스로 끝난다."""
    tracker = ProgressTracker()  # 실제 단조 시계, 이후 mark 없음 → 곧 stale
    fired = threading.Event()
    wd = HeartbeatWatchdog(
        tracker,
        timeout_seconds=0.05,
        poll_seconds=0.01,
        on_stale=lambda _idle: fired.set(),
    )
    wd.start()
    try:
        assert fired.wait(2.0), "워치독 스레드가 stall을 감지하지 못했다"
    finally:
        wd.stop()


def test_watchdog_rejects_invalid_config() -> None:
    tracker = ProgressTracker()
    with pytest.raises(ValueError):
        HeartbeatWatchdog(tracker, timeout_seconds=0.0)
    with pytest.raises(ValueError):
        HeartbeatWatchdog(tracker, timeout_seconds=10.0, poll_seconds=0.0)


# -- compute_backoff ---------------------------------------------------------


def test_compute_backoff_exponential_capped() -> None:
    assert compute_backoff(1, base_seconds=1.0, factor=2.0, max_seconds=60.0) == 1.0
    assert compute_backoff(2, base_seconds=1.0, factor=2.0, max_seconds=60.0) == 2.0
    assert compute_backoff(3, base_seconds=1.0, factor=2.0, max_seconds=60.0) == 4.0
    assert compute_backoff(10, base_seconds=1.0, factor=2.0, max_seconds=60.0) == 60.0  # 상한
    assert compute_backoff(0) == 1.0  # 방어적


# -- run_with_recovery -------------------------------------------------------


def test_run_with_recovery_reconnects_on_stall() -> None:
    """stall 후 재접속하고, 복구 이벤트를 남긴다 — 완료기준 1·4."""
    calls = {"n": 0}
    events: list[RecoveryEvent] = []
    delays: list[float] = []

    async def _stream_once() -> None:
        calls["n"] += 1
        raise StreamStalled(90.0)

    async def _fake_sleep(delay: float) -> None:
        delays.append(delay)

    asyncio.run(
        run_with_recovery(
            _stream_once,
            on_recover=events.append,
            max_reconnects=2,
            sleep=_fake_sleep,
        )
    )
    # 초기 1회 + 재접속 2회 = 3회 호출, 복구 이벤트 2회.
    assert calls["n"] == 3
    assert len(events) == 2
    assert events[0].attempt == 1
    assert "stall" in events[0].reason
    assert delays == [1.0, 2.0]  # 지수 백오프


def test_run_with_recovery_reconnects_on_normal_end() -> None:
    """스트림이 조용히 정상 종료해도 재접속한다(무한 스트림은 정상 반환이 없다)."""
    calls = {"n": 0}
    events: list[RecoveryEvent] = []

    async def _stream_once() -> None:
        calls["n"] += 1  # 조용히 반환 = 스트림이 끝남

    async def _fake_sleep(_delay: float) -> None:
        pass

    asyncio.run(
        run_with_recovery(
            _stream_once, on_recover=events.append, max_reconnects=1, sleep=_fake_sleep
        )
    )
    assert calls["n"] == 2
    assert events[0].reason == "스트림 정상 종료(재접속)"


def test_run_with_recovery_recovers_from_network_error() -> None:
    """OSError(DNS 실패 등)도 복구 대상이다."""
    calls = {"n": 0}

    async def _stream_once() -> None:
        calls["n"] += 1
        raise OSError("Failed to resolve 'api.binance.com'")

    async def _fake_sleep(_delay: float) -> None:
        pass

    asyncio.run(run_with_recovery(_stream_once, max_reconnects=1, sleep=_fake_sleep))
    assert calls["n"] == 2


def test_run_with_recovery_propagates_unrecoverable() -> None:
    """복구 불가 예외(프로그래밍 오류)는 삼키지 않고 올린다."""

    async def _stream_once() -> None:
        raise ValueError("boom")

    async def _fake_sleep(_delay: float) -> None:
        pass

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(run_with_recovery(_stream_once, max_reconnects=3, sleep=_fake_sleep))
