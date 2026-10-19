"""웹소켓 여러 연결 중 하나가 끝나면 전체가 끝난다 + 따라잡기는 접속 순간에서 시작한다(WAN-456).

2026-10-17 사고: 바이낸스가 두 연결 중 하나를 11:56에 닫았는데 `_recv_stream`이
`ConnectionClosed`를 삼켜 그 연결은 **정상 종료**했고, `asyncio.TaskGroup`은 예외가 날 때만
형제를 취소하므로 남은 연결이 14:31에 끝날 때까지 절반 종목의 봉이 2.5시간 끊겼다(하트비트·
유휴 감시는 남은 연결이 계속 갱신해 아무도 몰랐다). 그리고 같은 날 09:03 재접속에서는
꼬리 따라잡기가 「처리하는 순간」의 마지막 봉에서 시작해, 그 사이 스트림이 넣은 09:03 봉에
가려 09:02 1분봉 하나를 영구히 남겼다.

여기서는 그 두 경로를 **동작으로** 고정한다 — 옛 코드로 되돌리면 (a)·(d)가 실패한다.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

import data.stream as stream_mod
from config.settings import Settings
from data.backfill import backfill_all
from data.collection_plan import backfill_pairs
from data.collector import TailCatchup
from data.models import Candle
from data.storage import OhlcvStore
from data.watchdog import run_with_recovery

M = 60_000
#: 아주 옛날 시각 — 따라잡기의 「닫힌 봉만」 필터가 지금 시각 기준이라 무관해진다.
T0 = 1_000_000 * M
SYMBOL = "BTC/USDT:USDT"


class _Ws:
    """`behaviour`대로 움직이는 가짜 웹소켓."""

    def __init__(self, index: int, log: list[str], behaviour: str) -> None:
        self._index = index
        self._log = log
        self._behaviour = behaviour

    async def recv(self) -> str:
        self._log.append(f"recv{self._index}")
        if self._behaviour == "close":
            await asyncio.sleep(0.01)
            raise ConnectionClosed(None, None)
        if self._behaviour == "oserror":
            await asyncio.sleep(0.01)
            raise OSError("network down")
        try:
            await asyncio.sleep(30)  # 살아서 계속 받는 연결
        except asyncio.CancelledError:
            self._log.append(f"cancel{self._index}")
            raise
        raise AssertionError("unreachable")


def _install(monkeypatch: pytest.MonkeyPatch, behaviours: list[str]) -> list[str]:
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


#: 연결 2개로 나뉘는 쌍(상한 200).
PAIRS = [(f"S{i}/USDT:USDT", "1m") for i in range(300)]


def _run_pairs(**kw: Any) -> None:
    async def main() -> None:
        await asyncio.wait_for(
            stream_mod.stream_kline_pairs(None, PAIRS, **kw),  # type: ignore[arg-type]
            timeout=3,
        )

    asyncio.run(main())


def test_one_connection_ending_normally_ends_the_whole_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a) 한 연결이 정상 종료하면 다른 연결도 취소되고 즉시 끝난다(옛 `TaskGroup`은 기다렸다)."""
    log = _install(monkeypatch, ["close", "alive"])
    _run_pairs()  # 3초 안에 끝나지 않으면 TimeoutError — 옛 동작
    assert "cancel1" in log
    assert {"close0", "close1"} <= set(log)  # 두 소켓 모두 닫는다


def test_one_connection_ending_normally_triggers_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a′) 상위 재접속 루프까지 — 반쪽 종료가 곧바로 재접속(「조용히 끝남」)이 된다."""
    _install(monkeypatch, ["close", "alive", "close", "alive"])
    attempts: list[int] = []

    async def stream_once() -> None:
        attempts.append(1)
        await stream_mod.stream_kline_pairs(None, PAIRS)  # type: ignore[arg-type]

    async def no_sleep(_: float) -> None:
        return None

    async def main() -> None:
        await asyncio.wait_for(
            run_with_recovery(stream_once, max_reconnects=1, sleep=no_sleep), timeout=3
        )

    asyncio.run(main())
    assert len(attempts) == 2


def test_one_connection_failing_cancels_the_others_and_raises_plainly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(b) 예외로 끝나면 나머지를 취소하고 그 예외를 **묶지 않고** 올린다.

    옛 `TaskGroup`은 `ExceptionGroup`으로 묶어 올려 `run_with_recovery`의 `except`(OSError ·
    StreamStalled …)에 걸리지 않았다 — 재접속 대신 수집기가 죽었다.
    """
    log = _install(monkeypatch, ["alive", "oserror"])
    with pytest.raises(OSError, match="network down"):
        _run_pairs()
    assert "cancel0" in log


def test_failure_is_recoverable_by_the_reconnect_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, ["alive", "oserror", "alive", "oserror"])

    async def stream_once() -> None:
        await stream_mod.stream_kline_pairs(None, PAIRS)  # type: ignore[arg-type]

    async def no_sleep(_: float) -> None:
        return None

    async def main() -> None:
        await asyncio.wait_for(
            run_with_recovery(stream_once, max_reconnects=1, sleep=no_sleep), timeout=3
        )

    asyncio.run(main())  # 예외가 새지 않는다


def test_on_connect_once_after_every_connection_and_before_any_recv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """여러 연결이면 `on_connect`는 전부 접속한 뒤 · 어느 연결도 받기 전에 한 번만."""
    log = _install(monkeypatch, ["close", "alive"])
    _run_pairs(on_connect=lambda: log.append("on_connect"))
    assert log.count("on_connect") == 1
    first_recv = min(i for i, x in enumerate(log) if x.startswith("recv"))
    assert log.index("on_connect") < first_recv
    assert log.index("connect1") < log.index("on_connect")


def test_outer_cancel_cancels_every_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """계획 교체(`_stream_arm_plan`)가 바깥에서 취소해도 연결 태스크가 남지 않는다."""
    log = _install(monkeypatch, ["alive", "alive"])

    async def main() -> None:
        task = asyncio.create_task(stream_mod.stream_kline_pairs(None, PAIRS))  # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert {"cancel0", "cancel1", "close0", "close1"} <= set(log)


# ---------------------------------------------------------------- (d) 따라잡기 시작점


def _bar(minute: int) -> Candle:
    return Candle(SYMBOL, "1m", T0 + minute * M, 1, 1, 1, 1, 1)


class _Exchange:
    """09:00~09:03 네 봉을 아는 거래소 — `since` 이후 것만 준다."""

    def __init__(self) -> None:
        self.rows = [[T0 + m * M, 1.0, 1.0, 1.0, 1.0, 1.0] for m in range(4)]

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        return [r for r in self.rows if since is None or r[0] >= since]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        symbols=[SYMBOL],
        timeframes=["1m"],
        db_path=str(tmp_path / "ohlcv.db"),
        funding_enabled=False,
        collector_watchdog_enabled=False,
    )


def _stored_minutes(store: OhlcvStore) -> list[int]:
    return [int((t - T0) // M) for t in store.load(SYMBOL, "1m")["open_time"]]


def test_catchup_starts_from_the_snapshot_not_from_the_latest_bar(tmp_path: Path) -> None:
    """(d) 09:02가 빠진 채 스트림이 09:03을 먼저 넣어도, 접속 순간 스냅샷에서 09:02를 메운다.

    2026-10-17 09:03 재접속: 팔 24종목의 09:02 1분봉이 영구히 빠졌다 — 따라잡기가 그 쌍을
    처리할 땐 스트림이 이미 09:03을 넣어 「마지막 봉 다음 = 09:04」부터 받았다.
    """
    settings = _settings(tmp_path)
    store = OhlcvStore(settings.db_path)
    store.upsert_candles([_bar(0), _bar(1)])  # 09:00 · 09:01 — 09:02는 연결이 닫혀 못 받았다
    catchup = TailCatchup(_Exchange(), store, settings)
    snapshot = catchup.snapshot()  # 접속 순간(소비 전)
    store.upsert_candles([_bar(3)])  # 스트림이 먼저 09:03을 넣었다
    filled = backfill_all(
        _Exchange(), store, [SYMBOL], ["1m"], settings=settings, last_open_times=snapshot
    )
    assert filled[(SYMBOL, "1m")] >= 1
    assert _stored_minutes(store) == [0, 1, 2, 3]
    store.close()


def test_without_a_snapshot_the_gap_is_skipped(tmp_path: Path) -> None:
    """옛 시작점(처리 순간의 마지막 봉)이면 09:02가 남는다 — 위 테스트가 막는 그 사고다."""
    settings = _settings(tmp_path)
    store = OhlcvStore(settings.db_path)
    store.upsert_candles([_bar(0), _bar(1), _bar(3)])
    backfill_all(_Exchange(), store, [SYMBOL], ["1m"], settings=settings)
    assert _stored_minutes(store) == [0, 1, 3]
    store.close()


def test_arm_pairs_also_start_from_the_snapshot(tmp_path: Path) -> None:
    """팔 쪽(`backfill_pairs` — 쌍 사이 간격 때문에 가장 오래 걸린다)도 스냅샷에서 시작한다."""
    store = OhlcvStore(tmp_path / "ohlcv.db")
    store.upsert_candles([_bar(0), _bar(1)])
    snapshot = {(SYMBOL, "1m"): store.last_open_time(SYMBOL, "1m")}
    store.upsert_candles([_bar(3)])
    backfill_pairs(
        _Exchange(),
        store,
        [(SYMBOL, "1m")],
        lookback_days=lambda tf: 1,
        pause_seconds=0,
        last_open_times=snapshot,
    )
    assert _stored_minutes(store) == [0, 1, 2, 3]
    store.close()


def test_tail_catchup_runs_from_the_schedule_time_snapshot(tmp_path: Path) -> None:
    """`schedule()`이 스냅샷을 찍고 그 값으로 기본 격자 · 팔 쪽을 모두 돌린다."""
    settings = _settings(tmp_path)
    store = OhlcvStore(settings.db_path)
    store.upsert_candles([_bar(0), _bar(1)])
    seen: list[dict[tuple[str, str], int | None]] = []

    def extra(snapshot: Any) -> int:
        seen.append(dict(snapshot))
        return 0

    async def main() -> None:
        catchup = TailCatchup(
            _Exchange(), store, settings, extra=extra, extra_pairs=lambda: [("ARM", "1m")]
        )
        catchup.schedule()
        store.upsert_candles([_bar(3)])  # 소비가 시작돼 스트림이 09:03을 넣었다
        while catchup._tasks:  # noqa: SLF001 — 완료 대기
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert _stored_minutes(store) == [0, 1, 2, 3]
    assert seen == [{(SYMBOL, "1m"): T0 + M, ("ARM", "1m"): None}]
    store.close()


def test_overlapping_schedule_runs_once_more_with_the_earliest_snapshot(tmp_path: Path) -> None:
    """도는 중에 다시 접속하면 건너뛰지 않고 끝난 뒤 한 번 더 돈다(여러 번 겹쳐도 한 번)."""
    settings = _settings(tmp_path)
    store = OhlcvStore(settings.db_path)
    store.upsert_candles([_bar(0)])
    release = threading.Event()
    snapshots: list[Any] = []

    def slow_extra(snapshot: Any) -> int:
        snapshots.append(dict(snapshot))
        release.wait(timeout=5)
        return 0

    async def main() -> None:
        empty = _Exchange()
        empty.rows = []  # 저장소를 건드리지 않게 — 스냅샷 값만 본다
        catchup = TailCatchup(empty, store, settings, extra=slow_extra)
        catchup.schedule()
        while not snapshots:
            await asyncio.sleep(0.01)
        store.upsert_candles([_bar(1)])
        catchup.schedule()  # 재접속 #1 — 보류
        store.upsert_candles([_bar(2)])
        catchup.schedule()  # 재접속 #2 — 보류에 합쳐진다(더 이른 값)
        release.set()
        while catchup._tasks:  # noqa: SLF001
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert len(snapshots) == 2
    assert snapshots[0] == {(SYMBOL, "1m"): T0}
    # 둘째 실행은 보류된 두 스냅샷(09:01 · 09:02) 중 더 이른 값에서 시작한다.
    assert snapshots[1] == {(SYMBOL, "1m"): T0 + M}
    store.close()
