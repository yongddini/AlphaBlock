"""수집기 스트림 자동 복구 워치독 — 조용히 멈춘 웹소켓을 스스로 살린다 (WAN-173).

WAN-156이 **감지**(꼬리 정지 → 텔레그램 경고)까지 넣었다면 이 모듈은 그 후속인
**복구**다. 두 층으로 나뉜다:

1. **스트림 레벨(유휴 워치독)** — `guard_idle`이 웹소켓 수신 이터레이터를 감싸,
   `idle_timeout_seconds` 안에 다음 메시지가 안 오면 `StreamStalled`를 던진다. 바이낸스
   kline 스트림은 미확정 봉 갱신을 수초 간격으로 밀어 주므로, 저조한 TF만 구독해도
   정상 상태에서는 수초 안에 무언가가 온다 — 수십 초간 **무수신**이면 half-open(반쯤
   죽은) 소켓이다. `ConnectionClosed`(정상 종료 신호)를 못 받는 그 stall을 예외로 바꿔
   상위 재접속 루프가 잡게 한다.

2. **프로세스 레벨(하트비트 워치독)** — `HeartbeatWatchdog`(별도 스레드)가 진행
   시각(`ProgressTracker`)을 폴링해, 이벤트 루프가 통째로 멎어 in-process 재접속조차
   못 도는 최악의 경우 `on_stale`(기본: 프로세스 종료)로 hang을 exit으로 바꾼다. 그러면
   systemd `Restart=always`(맥이면 launchd `KeepAlive`)가 수집기를 되살린다. 스레드로
   두는 것이 핵심이다 — 이벤트 루프가 멎으면 asyncio 태스크로 짠 감시자도 같이 멎는다.

두 층 사이를 잇는 것이 `run_with_recovery`다: 스트림을 한 번 돌리고, 어떤 이유로든
끝나거나 stall/절단 예외가 나면 백오프 후 다시 접속한다. 복구 이벤트를 `on_recover`로
흘려 로그·텔레그램에 남긴다(2주 방치 중 몇 번 살아났는지 보이게).

네트워크·이벤트 루프 의존을 콜러블로 주입해 단위 테스트가 쉽다(모의 stall·가짜 시계).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: 재접속 루프가 「복구할 만한 실패」로 보고 다시 붙는 예외들. 조용한 stall
#: (`StreamStalled`)·정상 종료(`ConnectionClosed`)·네트워크 오류(`OSError`,
#: DNS 실패 등)·타임아웃을 아우른다.
RECOVERABLE_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionClosed,
    TimeoutError,
    OSError,
)


class StreamStalled(RuntimeError):
    """유휴 타임아웃 안에 웹소켓 메시지가 오지 않았다(half-open 소켓 의심).

    `ConnectionClosed`는 소켓이 **닫혔다는 신호**지만, 이쪽은 소켓이 열린 채
    **조용히 멎은** 경우다 — WAN-173이 진단한 2026-07-22 22:07 stall의 모양이다.
    """

    def __init__(self, idle_timeout_seconds: float) -> None:
        self.idle_timeout_seconds = idle_timeout_seconds
        super().__init__(f"웹소켓 유휴 {idle_timeout_seconds:g}s 초과 무수신 — half-open 소켓 의심")


async def guard_idle(
    source: AsyncIterator[_T],
    *,
    idle_timeout_seconds: float,
) -> AsyncIterator[_T]:
    """`source`의 다음 항목이 `idle_timeout_seconds` 안에 안 오면 `StreamStalled`.

    정상 종료(`StopAsyncIteration`)는 그대로 흘려 조용히 끝난다. 유휴 초과면 대기 중인
    `__anext__`를 취소하고 `StreamStalled`를 올려 상위 재접속 루프가 잡게 한다.
    """
    if idle_timeout_seconds <= 0:
        raise ValueError("idle_timeout_seconds는 양수여야 합니다")
    while True:
        try:
            item = await asyncio.wait_for(anext(source), idle_timeout_seconds)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            raise StreamStalled(idle_timeout_seconds) from exc
        yield item


class ProgressTracker:
    """마지막으로 진행이 있었던 시각을 단조 시계로 기록한다(스레드 안전).

    수집기가 메시지를 받을 때마다 `mark()`를 부르고, 워치독 스레드가 `idle_seconds()`로
    얼마나 조용했는지 읽는다. 벽시계가 아니라 `time.monotonic`을 써 시스템 시간이
    뒤로 점프해도 오탐하지 않는다.
    """

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._last = now()

    def mark(self) -> None:
        """진행 시각을 현재로 갱신한다(메시지 수신마다 호출)."""
        with self._lock:
            self._last = self._now()

    def idle_seconds(self) -> float:
        """마지막 진행 이후 경과 초."""
        with self._lock:
            return self._now() - self._last


def force_exit(exit_code: int = 1) -> Callable[[float], None]:
    """`HeartbeatWatchdog`의 기본 `on_stale`: 프로세스를 즉시 종료한다.

    이벤트 루프가 멎어 정상 종료(태스크 취소·`store.close()`)를 기다릴 수 없는 상황이라
    `os._exit`로 곧장 빠진다 — systemd `Restart=always`/launchd `KeepAlive`가 재시작한다
    (hang → exit 전환, WAN-173 작업 범위 (2a)). 반환 콜백은 idle 초를 받아 로깅한다.
    """

    def _exit(idle_seconds: float) -> None:
        logger.critical(
            "하트비트 %0.1fs 정지 — 프로세스를 종료해 서비스 매니저 재시작을 유도합니다(WAN-173)",
            idle_seconds,
        )
        os._exit(exit_code)

    return _exit


class HeartbeatWatchdog:
    """진행 정지를 감시하는 데몬 스레드. `timeout_seconds` 넘게 조용하면 `on_stale`.

    `on_stale`은 정확히 한 번만 호출하고 스레드를 끝낸다(기본값 `force_exit`는 프로세스를
    종료하므로 한 번이면 충분하다). 테스트는 `on_stale`을 주입해 종료 없이 트리거를
    관찰하고, `now`로 가짜 시계를 넣어 실제 대기 없이 판정할 수 있다.
    """

    def __init__(
        self,
        tracker: ProgressTracker,
        *,
        timeout_seconds: float,
        poll_seconds: float = 15.0,
        on_stale: Callable[[float], None] | None = None,
        name: str = "collector-watchdog",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds는 양수여야 합니다")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds는 양수여야 합니다")
        self._tracker = tracker
        self._timeout_seconds = timeout_seconds
        self._poll_seconds = poll_seconds
        self._on_stale = on_stale if on_stale is not None else force_exit()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def poll_once(self) -> bool:
        """한 번 점검한다. 정지로 판정해 `on_stale`을 부르면 True(그 뒤엔 안 부른다)."""
        idle = self._tracker.idle_seconds()
        if idle >= self._timeout_seconds:
            self._on_stale(idle)
            return True
        return False

    def _run(self) -> None:
        # `Event.wait`가 poll 간격을 재우면서 stop 신호도 함께 기다린다 —
        # `stop()`이 즉시 스레드를 깨운다.
        while not self._stop.wait(self._poll_seconds):
            if self.poll_once():
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        """감시를 멈추고 스레드가 끝나길 기다린다(정상 종료 경로)."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def __enter__(self) -> HeartbeatWatchdog:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


@dataclass(frozen=True)
class RecoveryEvent:
    """재접속 한 번의 요약(로그·텔레그램 메시지에 담긴다)."""

    attempt: int
    """이번이 몇 번째 재접속 시도인지(연속 실패 카운터, 성공 수신 시 1로 리셋)."""
    reason: str
    """직전 스트림이 끝난 이유(사람이 읽는 한 줄)."""
    delay_seconds: float
    """다음 접속까지 대기할 백오프 초."""


def compute_backoff(
    attempt: int,
    *,
    base_seconds: float = 1.0,
    factor: float = 2.0,
    max_seconds: float = 60.0,
) -> float:
    """지수 백오프(상한 있음). `attempt`는 1부터.

    재시작 폭주를 막되(systemd `RestartSec`과 같은 취지) 상한을 둬 stall이 길어져도
    복구 시도를 완전히 멈추지 않는다.
    """
    if attempt < 1:
        return base_seconds
    return min(max_seconds, base_seconds * factor ** (attempt - 1))


def _describe_end(error: BaseException | None) -> str:
    if error is None:
        return "스트림 정상 종료(재접속)"
    if isinstance(error, StreamStalled):
        return f"조용한 stall — {error}"
    if isinstance(error, ConnectionClosed):
        return f"연결 종료 — {error}"
    return f"{type(error).__name__} — {error}"


async def run_with_recovery(
    stream_once: Callable[[], Awaitable[None]],
    *,
    on_recover: Callable[[RecoveryEvent], None] | None = None,
    max_reconnects: int | None = None,
    base_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 60.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """스트림을 돌리고 stall/절단/정상종료 시 백오프 후 무한 재접속한다.

    `stream_once`는 한 번의 접속·소비를 수행하는 코루틴 팩토리다(정상 반환 = 스트림이
    조용히 끝남 → 재접속, `RECOVERABLE_ERRORS`/`StreamStalled` = 실패 → 재접속). 복구
    불가 예외(프로그래밍 오류 등)는 그대로 올려 조용히 삼키지 않는다.

    `max_reconnects`가 주어지면 그만큼 재접속한 뒤 반환한다(테스트용 · 기본 None = 무한).
    `sleep`을 주입해 테스트에서 실제 대기 없이 백오프를 소비한다.
    """
    reconnects = 0
    while True:
        error: BaseException | None = None
        try:
            await stream_once()
        except StreamStalled as exc:
            error = exc
        except RECOVERABLE_ERRORS as exc:
            error = exc

        if max_reconnects is not None and reconnects >= max_reconnects:
            return

        reconnects += 1
        delay = compute_backoff(
            reconnects,
            base_seconds=base_backoff_seconds,
            max_seconds=max_backoff_seconds,
        )
        reason = _describe_end(error)
        if error is None:
            logger.warning("스트림이 조용히 끝남 — %.1fs 후 재접속(#%d)", delay, reconnects)
        else:
            logger.warning("스트림 복구: %s — %.1fs 후 재접속(#%d)", reason, delay, reconnects)
        if on_recover is not None:
            on_recover(RecoveryEvent(attempt=reconnects, reason=reason, delay_seconds=delay))
        await sleep(delay)
