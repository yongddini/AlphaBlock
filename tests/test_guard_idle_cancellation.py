"""`guard_idle`가 바깥 취소를 잃지 않는다 — Python 3.11 `asyncio.wait_for` 결함 회귀 테스트.

3.11의 `wait_for`는 기다리던 항목이 막 준비된 순간에 바깥에서 취소가 오면 그 취소를 삼키고
항목을 돌려준다(CPython gh-86296 · 3.12에서 고침). `guard_idle`가 그것을 쓰던 동안
`_first_to_end`가 「나머지 연결을 닫는다」며 취소한 연결이 계속 돌았고, 그 연결을 기다리는
`gather`가 영원히 안 끝났다. 바쁜 기계에서는 `tests/test_wan461_silent_connection.py`가
몇 시간씩 멈추는 모양으로 드러났다(2026-10-23 전체 pytest가 92%에서 2시간 넘게 정지).

이 테스트는 메시지가 끊임없이 오는 소스를 소비하다가 **여러 시점**에 취소를 걸어, 매번 몇
스텝 안에 끝나는지 본다. 옛 구현(`wait_for`)으로 되돌리면 40번 중 대부분이 안 끝난다
(실측 30/40) — 라벨이 아니라 동작으로 고정한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from data.watchdog import StreamStalled, guard_idle


async def _busy_source() -> AsyncIterator[int]:
    """메시지가 끊임없이 오는 연결 — 매 이벤트 루프 스텝마다 하나씩."""
    while True:
        await asyncio.sleep(0)
        yield 1


async def _consume() -> None:
    async for _ in guard_idle(_busy_source(), idle_timeout_seconds=5):
        pass


async def _cancel_after(steps: int) -> bool:
    """`steps` 스텝 뒤 취소하고, 몇 스텝 안에 끝나면 True. 안 끝나면 억지로 정리하고 False."""
    task = asyncio.create_task(_consume())
    for _ in range(steps):
        await asyncio.sleep(0)
    task.cancel()
    for _ in range(50):
        await asyncio.sleep(0)
        if task.done():
            return task.cancelled()
    # 취소를 잃었다 — 테스트가 매달리지 않게 끝날 때까지 다시 취소한다.
    for _ in range(10_000):
        task.cancel()
        await asyncio.sleep(0)
        if task.done():
            break
    return False


def test_outside_cancellation_is_never_lost() -> None:
    """메시지가 오가는 와중 어느 시점에 취소해도 소비가 곧바로 「취소됨」으로 끝난다."""

    async def main() -> list[bool]:
        return [await _cancel_after(steps) for steps in range(1, 41)]

    results = asyncio.run(main())
    lost = [steps for steps, ok in zip(range(1, 41), results, strict=True) if not ok]
    assert lost == [], f"취소를 잃은 시점(스텝): {lost}"


def test_idle_timeout_still_raises_stream_stalled() -> None:
    """고친 뒤에도 메시지가 안 오면 예전처럼 `StreamStalled`다(유휴 감시는 그대로)."""

    async def silent() -> AsyncIterator[int]:
        await asyncio.sleep(30)
        yield 1

    async def main() -> None:
        async for _ in guard_idle(silent(), idle_timeout_seconds=0.05):
            pass

    with pytest.raises(StreamStalled):
        asyncio.run(main())
