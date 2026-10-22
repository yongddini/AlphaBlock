"""확정봉 REST 이중 수집 — 웹소켓이 얼어도 1~2분 안에 봉이 들어오게 한다 (WAN-470 §0).

바이낸스 웹소켓 연결이 며칠에 한 번 「살아서 옛 데이터만 보내는」 상태가 된다(2026-10-20 연결
2/2 · 2026-10-22 연결 1/2 — 4.5시간). 원인은 거래소 쪽이라 우리가 막을 수 없다. 그래서 확정봉의
정본 경로를 웹소켓 하나에 맡기지 않는다: 매 분 마감 몇 초 뒤, 수집 계획의 시리즈마다 **저장된
마지막 봉 다음 봉이 이미 닫혔는데 아직 없으면** REST(`fetch_ohlcv`)로 받아 저장한다.

* 1분봉은 매분, 상위 TF는 그 TF 경계가 지나야 「없는 봉」이 생기므로 경계에서만 요청이 나간다
  (주봉도 같은 규칙 — 다음 봉 = 마지막 봉 + 1주라 월요일 정렬이 저절로 맞는다).
* 웹소켓이 건강하면 확정 이벤트가 오프셋(기본 5초)보다 먼저 와 **요청이 거의 안 나간다.** 같은
  봉이 둘 다로 오면 `ON CONFLICT`로 한 행이다(값이 같다).
* 저장분이 하나도 없는 시리즈(새로 들어와 아직 안 채운 쌍)는 건드리지 않는다 — 그건 팔 백필
  (`ArmCollection.fill`)의 몫이고, 여기서 룩백 전체를 받으면 가중치 한도를 넘는다.
* 한 번에 거슬러 받는 깊이는 `max_bars`까지다. 그보다 긴 구멍은 접속 직후 꼬리 따라잡기
  (`TailCatchup`)·시작 갭 복구가 메운다.

요청 가중치: 바이낸스 선물 klines는 `limit < 100`이면 요청당 1이다(분당 한도 2400). 페이지를
99봉으로 받아 1을 지킨다. 숫자는 `docs/decisions/wan470.md` §0.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from data.backfill import FetchOHLCV, backfill_symbol
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

logger = logging.getLogger(__name__)

Pair = tuple[str, str]

#: 요청당 가중치 1을 지키는 페이지 크기(바이낸스 선물 klines: limit 1~99 → 1, 100~499 → 2).
POLL_PAGE_LIMIT = 99
#: 한 시리즈를 한 번에 거슬러 받는 최대 봉 수(1분봉이면 약 8시간 — 그보다 긴 구멍은 따라잡기 몫).
DEFAULT_MAX_BARS = 500
#: 분 마감 뒤 이만큼 기다렸다 본다 — 웹소켓 확정 이벤트가 먼저 와서 요청이 안 나가게.
DEFAULT_OFFSET_SECONDS = 5.0
_MINUTE_MS = 60_000


@dataclass(frozen=True)
class PollResult:
    """한 번 돈 결과."""

    checked: int
    """저장분이 있어 판정한 시리즈 수."""
    requested: int
    """REST를 실제로 부른 시리즈 수(= 닫혔는데 없는 봉이 있던 시리즈)."""
    filled: int
    """저장한 봉 수."""
    still_missing: int
    """돌고 난 뒤에도 닫힌 봉이 빠져 있는 시리즈 수(요청 실패·깊이 상한)."""
    errors: int

    @property
    def progressed(self) -> bool:
        """「데이터가 현재까지 와 있다」 또는 「새 봉을 넣었다」 — 수집기 생존 신호(WAN-470).

        둘 다 아니면(받을 게 있는데 못 받았다) False다 — 그때는 워치독이 진행으로 세면 안 된다.
        """
        return self.filled > 0 or (self.still_missing == 0 and self.errors == 0)


def missing_since(
    last_open_time: int | None, timeframe: str, now_ms: int, *, max_bars: int
) -> int | None:
    """받아야 할 첫 봉의 `open_time` — 닫혔는데 없는 봉이 없으면 None.

    다음 봉 = 마지막 봉 + TF이고, 그 봉이 닫힌 것은 `다음 봉 + TF ≤ now`일 때다. 너무 많이 밀렸으면
    최근 `max_bars`개만 받도록 시작점을 TF 격자 위에서 당긴다(시리즈 정렬이 안 깨진다).
    """
    if last_open_time is None:
        return None
    tf_ms = timeframe_to_ms(timeframe)
    since = last_open_time + tf_ms
    if since + tf_ms > now_ms:
        return None
    behind = (now_ms - since) // tf_ms
    if behind > max_bars:
        since += (behind - max_bars) * tf_ms
    return since


def next_poll_delay(now_ms: int, offset_seconds: float) -> float:
    """다음 「분 마감 + 오프셋」까지 남은 초(지금이 정확히 그 순간이면 다음 분)."""
    offset_ms = int(offset_seconds * 1000) % _MINUTE_MS
    target = (now_ms // _MINUTE_MS) * _MINUTE_MS + offset_ms
    if target <= now_ms:
        target += _MINUTE_MS
    return (target - now_ms) / 1000


class ClosedBarPoller:
    """매 분 마감 뒤 빠진 확정봉을 REST로 받는다(웹소켓과 이중 수집 · WAN-470 §0).

    `pairs`는 부를 때마다 **지금 수집 계획**을 돌려준다(팔 목록이 바뀌면 따라간다). `on_progress`는
    한 바퀴가 `PollResult.progressed`일 때 불린다 — 수집기는 여기서 하트비트·워치독을 찍는다.
    `on_filled(symbol, timeframe, n)`은 봉을 넣은 시리즈마다 불린다(팔 3h 생성 등).
    """

    def __init__(
        self,
        exchange: FetchOHLCV,
        store: OhlcvStore,
        pairs: Callable[[], Sequence[Pair]],
        *,
        offset_seconds: float = DEFAULT_OFFSET_SECONDS,
        max_bars: int = DEFAULT_MAX_BARS,
        page_limit: int = POLL_PAGE_LIMIT,
        max_retries: int = 2,
        on_progress: Callable[[], None] | None = None,
        on_filled: Callable[[str, str, int], None] | None = None,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_bars < 1:
            raise ValueError("max_bars는 1 이상이어야 합니다")
        self._exchange = exchange
        self._store = store
        self._pairs = pairs
        self._offset = offset_seconds
        self._max_bars = max_bars
        self._page_limit = page_limit
        self._max_retries = max_retries
        self._on_progress = on_progress
        self._on_filled = on_filled
        self._now_ms = now_ms
        self._sleep = sleep
        self._sleeper = sleeper

    def poll_once(self) -> PollResult:
        """지금 빠진 확정봉을 한 번 받는다(동기 — 스레드에서 돈다)."""
        checked = requested = filled = still_missing = errors = 0
        for symbol, timeframe in dict.fromkeys(self._pairs()):
            last = self._store.last_open_time(symbol, timeframe)
            if last is None:
                continue
            checked += 1
            since = missing_since(last, timeframe, self._now_ms(), max_bars=self._max_bars)
            if since is None:
                continue
            requested += 1
            try:
                n = backfill_symbol(
                    self._exchange,
                    self._store,
                    symbol,
                    timeframe,
                    since,
                    limit=self._page_limit,
                    max_retries=self._max_retries,
                    sleeper=self._sleeper,
                    now_ms=self._now_ms,
                )
            except Exception:  # noqa: BLE001 — 한 시리즈 실패가 나머지를 막으면 안 된다
                errors += 1
                logger.warning("REST 확정봉 수집 실패: %s %s(다음 분에 다시)", symbol, timeframe)
                continue
            filled += n
            if n and self._on_filled is not None:
                try:
                    self._on_filled(symbol, timeframe, n)
                except Exception:  # noqa: BLE001 — 후처리 실패가 수집을 멈추면 안 된다
                    logger.exception("REST 확정봉 후처리 실패: %s %s", symbol, timeframe)
            after = self._store.last_open_time(symbol, timeframe)
            if missing_since(after, timeframe, self._now_ms(), max_bars=self._max_bars) is not None:
                still_missing += 1
        return PollResult(
            checked=checked,
            requested=requested,
            filled=filled,
            still_missing=still_missing,
            errors=errors,
        )

    async def run_once(self) -> PollResult:
        """한 바퀴(스레드) + 진행 신호."""
        result = await asyncio.to_thread(self.poll_once)
        if result.filled:
            logger.info(
                "REST 확정봉 수집: %d개 시리즈에서 %d봉 저장(웹소켓보다 먼저 · WAN-470)%s",
                result.requested,
                result.filled,
                f" · 아직 빠짐 {result.still_missing}" if result.still_missing else "",
            )
        if result.progressed and self._on_progress is not None:
            self._on_progress()
        return result

    async def run(self, *, max_cycles: int | None = None) -> None:
        """매 분 마감 + 오프셋마다 한 바퀴(무한 · 한 바퀴 실패는 로그만 남기고 계속)."""
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            await self._sleep(next_poll_delay(self._now_ms(), self._offset))
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 — 다음 분에 다시
                logger.exception("REST 확정봉 수집 한 바퀴 실패(다음 분에 다시)")
            cycles += 1


def report_poller_task_exit(task: asyncio.Task[None]) -> None:
    """REST 수집 루프가 끝나면 시끄럽게 남긴다(정상 종료가 없는 루프 · WAN-422 §3 규약)."""
    if task.cancelled():
        return
    exc = task.exception()
    logger.error(
        "REST 확정봉 수집 태스크가 종료됐습니다 — 웹소켓만 남습니다: %r",
        exc,
        exc_info=exc,
    )
