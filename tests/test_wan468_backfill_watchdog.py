"""WAN-468 — 주간 팔 목록 교체 날 수집기가 백필 중 워치독에 연속으로 죽던 고리.

「진행이 있으면 살아 있다」를 동작으로 건다:

* (a) 300초를 훌쩍 넘는 백필도 **쪽을 저장하는 동안은** 워치독이 죽이지 않는다 — 쌍 하나가 쪽이
  많아도(새로 든 종목의 2년치) 그렇다(옛 코드는 쌍을 시작할 때만 찍었다).
* (b) 거래소가 응답을 안 주면(쪽이 안 쌓이면) 워치독이 **죽인다** — 백필 중에 워치독을 끄지 않았다.
* (c) 운영 중 계획 교체: 새 쌍을 채우는 동안 스트림이 끊기면 채우기를 기다리지 않고 곧바로 올려
  다시 붙고, 채우기는 재접속을 넘어 **이어 간다**(처음부터 다시 하지 않는다).
* 재시작 고리 방지: 이력 확장이 중간에 죽어도 저장분에 구멍이 안 남고 다음 기동이 이어 간다.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import data.collector as collector
from data.backfill import backfill_all
from data.collection_plan import ArmCollection, CollectionPlan, backfill_pairs, build_plan
from data.models import Candle
from data.storage import OhlcvStore
from data.universe_pool import RotationSnapshot, UniverseStore
from data.watchdog import HeartbeatWatchdog, ProgressTracker, StreamStalled

H = 3_600_000
DAY = 24 * H
LIMIT = 1000  # data.backfill.DEFAULT_LIMIT


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _SlowExchange:
    """쪽마다 가짜 시계를 `page_seconds`씩 흘리는 거래소. `hang_after`번째 요청부터는 응답 없이
    `hang_seconds`를 흘린다(거래소 무응답 흉내). 요청마다 워치독을 한 번 점검한다."""

    def __init__(
        self,
        clock: _Clock,
        watchdog: HeartbeatWatchdog,
        *,
        page_seconds: float,
        hang_after: int | None = None,
        hang_seconds: float = 0.0,
    ) -> None:
        self.clock = clock
        self.watchdog = watchdog
        self.page_seconds = page_seconds
        self.hang_after = hang_after
        self.hang_seconds = hang_seconds
        self.calls = 0

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        assert since is not None and limit is not None
        self.calls += 1
        if self.hang_after is not None and self.calls >= self.hang_after:
            self.clock.t += self.hang_seconds
            self.watchdog.poll_once()
            raise RuntimeError("거래소 무응답")
        self.clock.t += self.page_seconds
        self.watchdog.poll_once()
        start = -(-since // H) * H
        return [[t, 1.0, 2.0, 0.5, 1.5, 1.0] for t in range(start, start + limit * H, H)]


def _watch(clock: _Clock) -> tuple[ProgressTracker, HeartbeatWatchdog, list[float]]:
    tracker = ProgressTracker(now=clock)
    stale: list[float] = []
    wd = HeartbeatWatchdog(tracker, timeout_seconds=300, on_stale=stale.append)
    return tracker, wd, stale


NOW = 4000 * DAY


def test_long_single_pair_backfill_with_progress_is_not_killed(tmp_path: Path) -> None:
    """(a) 쌍 하나가 10쪽 × 60초 = 600초 — 쪽마다 진행이 있으니 죽이지 않는다.

    옛 코드(쌍을 시작할 때만 찍음)로 되돌리면 300초에 `on_stale`이 불린다.
    """
    clock = _Clock()
    tracker, wd, stale = _watch(clock)
    ex = _SlowExchange(clock, wd, page_seconds=60)
    with OhlcvStore(tmp_path / "a.db") as store:
        n = backfill_pairs(
            ex,
            store,
            [("NEW/USDT:USDT", "1h")],
            lookback_days=lambda _tf: (10 * LIMIT) // 24,  # 약 10쪽
            pause_seconds=0,
            now_ms=lambda: NOW,
            progress=tracker.mark,
        )
    assert ex.calls >= 10 and clock.t >= 600  # 정말로 300초를 훌쩍 넘었다
    assert n > 9 * LIMIT
    assert stale == []


def test_stalled_exchange_during_backfill_is_killed(tmp_path: Path) -> None:
    """(b) 두 쪽을 받고 거래소가 400초 응답이 없다 — 진행이 없으니 죽인다(백필 중에도 감시)."""
    clock = _Clock()
    tracker, wd, stale = _watch(clock)
    ex = _SlowExchange(clock, wd, page_seconds=30, hang_after=3, hang_seconds=400)
    with OhlcvStore(tmp_path / "b.db") as store:
        backfill_pairs(
            ex,
            store,
            [("NEW/USDT:USDT", "1h")],
            lookback_days=lambda _tf: (10 * LIMIT) // 24,
            pause_seconds=0,
            now_ms=lambda: NOW,
            progress=tracker.mark,
        )
    assert len(stale) == 1 and stale[0] >= 300


def test_startup_backfill_all_reports_progress_per_page(tmp_path: Path) -> None:
    """기동 백필(`backfill_all`)도 쪽마다 진행을 알린다(하트비트 파일 · WAN-468)."""
    clock = _Clock()
    tracker, wd, stale = _watch(clock)
    ex = _SlowExchange(clock, wd, page_seconds=60)
    marks: list[int] = []

    def _mark() -> None:
        marks.append(1)
        tracker.mark()

    class _S:
        def lookback_days_for(self, _tf: str) -> int:
            return (6 * LIMIT) // 24

    with OhlcvStore(tmp_path / "c.db") as store:
        backfill_all(
            ex,
            store,
            ["A/USDT:USDT"],
            ["1h"],
            settings=_S(),  # type: ignore[arg-type]
            now_ms=lambda: NOW,
            progress=_mark,
        )
    assert len(marks) >= ex.calls  # 시리즈 시작 + 쪽마다
    assert clock.t >= 360 and stale == []


# ---------------------------------------------------------------- (c) 운영 중 계획 교체


class _BlockingArm:
    """계획이 한 번 바뀌고, 새 쌍 채우기가 `release`될 때까지 막히는 가짜 팔."""

    def __init__(self) -> None:
        self.plan = build_plan(["A"], ["1m"])
        self.next = build_plan(["A"], ["1m"], ["N"])
        self.release = threading.Event()
        self.fills = 0
        self.extended_with: list[CollectionPlan | None] = []

    def compute(self) -> CollectionPlan:
        return self.next

    def fill(self, pairs: list[tuple[str, str]], arm_symbols: frozenset[str]) -> int:
        self.fills += 1
        assert self.release.wait(timeout=10), "테스트가 채우기를 풀지 않았다"
        return 7

    def extend_history(self, plan: CollectionPlan | None = None) -> int:
        self.extended_with.append(plan)
        return 0

    def adopt(self, plan: CollectionPlan) -> tuple[list[str], list[str]]:
        self.plan = plan
        return ["N"], []

    def on_candle(self, candle: Candle) -> None:
        return None


def test_stream_death_during_fill_reconnects_and_fill_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) 채우는 중 스트림이 멎으면 채우기를 기다리지 않고 곧바로 올린다(옛 계획으로 재접속).

    다시 붙은 뒤에는 같은 채우기를 이어 기다리고(다시 시작하지 않는다), 끝나면 새 계획을 채택해
    새 쌍으로 다시 붙는다. 옛 코드는 채우기가 끝날 때까지 스트림을 아무도 안 봐서, 그 사이
    워치독 한도가 지나면 프로세스가 죽었다(2026-10-19 09:17·09:30).
    """
    streams: list[list[tuple[str, str]]] = []

    async def fake_pairs(store: Any, pairs: Any, **kw: Any) -> None:
        streams.append(list(pairs))
        n = len(streams)
        if n == 1:
            await asyncio.sleep(0.2)  # 그 사이 계획 교체 감지 → 채우기 시작
            raise StreamStalled(90)  # 채우는 중 연결이 멎는다
        if n == 2:
            await asyncio.sleep(30)  # 옛 계획으로 다시 붙어 채우기가 끝날 때까지 산다
        raise ConnectionError("새 계획 연결 — 테스트 끝")

    monkeypatch.setattr(collector, "stream_kline_pairs", fake_pairs)
    arm = _BlockingArm()
    transition = collector.PlanTransition()

    def _run() -> Any:
        return collector._stream_arm_plan(
            None,  # type: ignore[arg-type]
            arm,  # type: ignore[arg-type]
            on_progress=lambda _c: None,
            idle_timeout_seconds=None,
            on_connect=lambda: None,
            check_seconds=0.01,
            transition=transition,
        )

    async def main() -> None:
        started = time.monotonic()
        with pytest.raises(StreamStalled):
            await _run()
        # 채우기는 아직 막혀 있다 — 기다리지 않고 올렸다.
        assert time.monotonic() - started < 2.0
        assert transition.task is not None and not transition.task.done()
        assert arm.plan.arm_symbols == frozenset()  # 아직 채택 전(옛 계획으로 재접속)
        asyncio.get_running_loop().call_later(0.2, arm.release.set)
        with pytest.raises(ConnectionError):
            await _run()

    try:
        asyncio.run(main())
    finally:
        arm.release.set()
    assert arm.fills == 1  # 재접속을 넘어 이어 갔다(처음부터 다시 하지 않았다)
    assert streams[0] == streams[1] == [("A", "1m")]  # 재접속은 옛 계획으로
    assert ("N", "1m") in streams[2]  # 채우기가 끝난 뒤 새 계획으로
    assert arm.extended_with == [arm.next]  # 이력 확장도 채택 전에 새 계획으로
    assert transition.task is None


def test_fill_failure_keeps_old_plan_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """채우기가 예외로 끝나도 스트림은 옛 계획으로 계속 돈다(다음 주기에 다시 본다)."""
    streams: list[list[tuple[str, str]]] = []

    async def fake_pairs(store: Any, pairs: Any, **kw: Any) -> None:
        streams.append(list(pairs))
        await asyncio.sleep(0.3)
        raise ConnectionError("끝")

    class _FailingArm(_BlockingArm):
        def fill(self, pairs: list[tuple[str, str]], arm_symbols: frozenset[str]) -> int:
            self.fills += 1
            raise RuntimeError("채우기 실패")

    monkeypatch.setattr(collector, "stream_kline_pairs", fake_pairs)
    arm = _FailingArm()
    with pytest.raises(ConnectionError):
        asyncio.run(
            collector._stream_arm_plan(
                None,  # type: ignore[arg-type]
                arm,  # type: ignore[arg-type]
                on_progress=lambda _c: None,
                idle_timeout_seconds=None,
                on_connect=lambda: None,
                check_seconds=0.01,
            )
        )
    assert arm.fills >= 1
    assert streams == [[("A", "1m")]]  # 재접속 없이 옛 계획 스트림 하나가 끝까지 돌았다
    assert arm.plan.arm_symbols == frozenset()


def test_collector_shares_one_transition_across_reconnects() -> None:
    import inspect

    src = inspect.getsource(collector.run_collector)
    assert "transition = PlanTransition()" in src and "transition=transition" in src


# ---------------------------------------------------------------- 재시작 고리 방지(이력 확장)


class _History:
    """`since`부터 1시간 간격 봉을 주는 거래소. `fail_on`번째 요청에서 예외(프로세스 죽음 흉내)."""

    def __init__(self, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.calls = 0

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        assert since is not None and limit is not None
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise RuntimeError("중간에 죽음")
        start = -(-since // H) * H
        return [[t, 1.0, 2.0, 0.5, 1.5, 1.0] for t in range(start, start + limit * H, H)]


def _arm(db: Path, store: OhlcvStore, universe: UniverseStore, exchange: Any) -> ArmCollection:
    t = 4 * DAY + 2900 * 7 * DAY
    universe.save(
        RotationSnapshot(
            rebalance_ms=t,
            computed_ms=t,
            pool_size=5,
            stale_size=0,
            ranked=(("X", 1.0),),
            top_n=31,
        )
    )
    arm = ArmCollection(
        exchange=exchange,
        store=store,
        universe=universe,
        db_path=db,
        base_symbols=["BTC/USDT:USDT"],
        base_timeframes=["1m"],
        lookback_days=lambda tf: 1,
        pause_seconds=0,
        history_days=60,
        page_pause_seconds=0,
        now_ms=lambda: t + DAY,
    )
    arm._page_limit = 100  # 100시간 쪽 — 60일을 여러 쪽으로
    arm.adopt(arm.compute())
    return arm


def _contiguous(store: OhlcvStore, sym: str) -> bool:
    times = store.open_times(sym, "1h")
    return all(b - a == H for a, b in zip(times, times[1:], strict=False))


def test_extend_history_interrupted_leaves_no_hole_and_resumes(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    store = OhlcvStore(db)
    universe = UniverseStore(db)
    try:
        arm = _arm(db, store, universe, _History(fail_on=3))
        now = arm._now_ms()
        want = now - 60 * DAY
        store.upsert_candles(
            Candle("X", "1h", now - 10 * DAY + i * H, 1, 2, 0.5, 1.5, 1) for i in range(24 * 9)
        )
        arm.extend_history()  # 두 쪽을 받고 세 번째 요청에서 죽는다
        first = store.first_open_time("X", "1h")
        assert first is not None and want < first < now - 10 * DAY  # 일부만 늘었다
        assert _contiguous(store, "X")  # 🚨 받다 만 구간과 옛 첫 봉 사이에 구멍이 없다

        # 재시작 — 새 프로세스(시도 기록 없음)가 저장된 첫 봉에서 이어 간다.
        arm2 = _arm(db, store, universe, _History())
        arm2.extend_history()
        assert store.first_open_time("X", "1h") == want
        assert _contiguous(store, "X")
    finally:
        store.close()
        universe.close()
