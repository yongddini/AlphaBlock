"""팔 종목 수집(WAN-451 ③) — 수집 계획 · 3h 생성 · 웹소켓 분할 · 계획이 바뀌면 다시 붙기."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import data.collector as collector
import data.stream as stream_mod
from backtest import wan424_stoch_ob_arm as w424
from data.collection_plan import (
    ArmCollection,
    CollectionPlan,
    backfill_pairs,
    build_plan,
    held_symbols,
)
from data.derived_bars import build_derived, source_closes_bucket
from data.models import Candle
from data.resample import resample_ohlcv
from data.storage import OhlcvStore
from data.universe import ARM_STREAM_TIMEFRAMES, ARM_TIMEFRAMES, BUILT_TIMEFRAMES
from data.universe_pool import RotationSnapshot, UniverseStore

H = 3_600_000
DAY = 24 * H


def test_arm_timeframes_match_the_canon() -> None:
    assert set(ARM_TIMEFRAMES) == {*w424.TIMEFRAMES, "15m"} and len(ARM_TIMEFRAMES) == 10
    # 원천 kline = 팔 TF − 저장소 파생(2h) − 수집기 생성(3h) + 1분봉
    assert set(ARM_STREAM_TIMEFRAMES) == (set(ARM_TIMEFRAMES) - {"2h"} - set(BUILT_TIMEFRAMES)) | {
        "1m"
    }


def test_plan_unions_base_grid_and_arm_symbols() -> None:
    plan = build_plan(
        ["BTC/USDT:USDT", "ETH/USDT:USDT"], ["1m", "5m", "1h"], ["BTC/USDT:USDT", "X"]
    )
    assert plan.timeframes["ETH/USDT:USDT"] == ("1m", "5m", "1h")
    assert plan.timeframes["X"] == ARM_STREAM_TIMEFRAMES
    assert plan.timeframes["BTC/USDT:USDT"] == (
        "1m",
        "5m",
        "15m",
        "1h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
        "1w",
    )
    assert plan.arm_symbols == {"BTC/USDT:USDT", "X"}
    before = build_plan(["BTC/USDT:USDT"], ["1m"])
    assert ("X", "1w") in plan.new_pairs(before) and ("BTC/USDT:USDT", "1m") not in plan.new_pairs(
        before
    )


def test_held_symbols_reads_open_positions_read_only(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    assert held_symbols(db) == set()  # 파일 없음
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x)")
    conn.commit()
    assert held_symbols(db) == set()  # 테이블 없음
    conn.execute("CREATE TABLE open_positions (symbol TEXT, timeframe TEXT)")
    conn.executemany(
        "INSERT INTO open_positions VALUES (?, ?)", [("A", "1h"), ("A", "4h"), ("B", "1d")]
    )
    conn.commit()
    conn.close()
    assert held_symbols(db) == {"A", "B"}


def _hours(store: OhlcvStore, sym: str, start: int, n: int) -> None:
    store.upsert_candles(
        Candle(sym, "1h", start + i * H, 10 + i, 12 + i, 9 + i, 11 + i, 1.5 + i) for i in range(n)
    )


def test_build_derived_matches_resample_and_is_incremental(tmp_path: Path) -> None:
    with OhlcvStore(tmp_path / "d.db") as store:
        _hours(store, "S", 1000 * DAY + H, 3 * 10)  # 첫 버킷은 1h 하나 모자라 안 만든다
        n = build_derived(store, "S", "3h")
        got = store.load("S", "3h")
        want = resample_ohlcv(store.load("S", "1h"), "1h", "3h")
        assert n == len(got) == len(want) == 9
        cols = ["open_time", "open", "high", "low", "close", "volume"]
        assert got[cols].reset_index(drop=True).equals(want[cols].reset_index(drop=True))
        assert build_derived(store, "S", "3h") == 0  # 이미 있는 건 다시 안 만든다
        _hours(store, "S", 1000 * DAY + 31 * H, 2)  # 다음 버킷 완성
        assert build_derived(store, "S", "3h") == 1


def test_source_closes_bucket_only_on_the_last_hour() -> None:
    t = 1000 * DAY
    assert source_closes_bucket("1h", t + 2 * H) == ["3h"]
    assert source_closes_bucket("1h", t + H) == []
    assert source_closes_bucket("15m", t + 2 * H + 45 * 60_000) == []


class _Ex:
    def __init__(self, fail: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.calls.append((symbol, timeframe))
        if symbol in self.fail:
            raise RuntimeError("boom")
        assert since is not None
        return [[since, 1.0, 2.0, 0.5, 1.5, 3.0]]


def test_backfill_pairs_paces_and_survives_failures(tmp_path: Path) -> None:
    now = 2000 * DAY
    sleeps: list[float] = []
    ex = _Ex(fail=frozenset({"B"}))
    with OhlcvStore(tmp_path / "b.db") as store:
        n = backfill_pairs(
            ex,
            store,
            [("A", "1h"), ("B", "1h"), ("C", "1d")],
            lookback_days=lambda tf: 10,
            pause_seconds=0.5,
            sleeper=sleeps.append,
            now_ms=lambda: now,
        )
        assert n == 2 and [c[0] for c in ex.calls] == ["A", "B", "C"]
        assert sleeps.count(0.5) == 2
        ex.calls.clear()
        store.upsert_candles([Candle("A", "1h", now - H, 1, 1, 1, 1, 1)])
        backfill_pairs(
            ex,
            store,
            [("A", "1h")],
            lookback_days=lambda tf: 10,
            pause_seconds=0,
            now_ms=lambda: now,
        )
        assert ex.calls == []  # 새로 닫힌 봉이 없으면 요청하지 않는다


def _arm(tmp_path: Path, members: list[str]) -> tuple[ArmCollection, OhlcvStore, UniverseStore]:
    db = tmp_path / "a.db"
    store = OhlcvStore(db)
    universe = UniverseStore(db)
    t = 4 * DAY + 2900 * 7 * DAY
    universe.save(
        RotationSnapshot(
            rebalance_ms=t,
            computed_ms=t,
            pool_size=5,
            stale_size=0,
            ranked=tuple((s, 1.0) for s in members),
            top_n=31,
        )
    )
    arm = ArmCollection(
        exchange=_Ex(),
        store=store,
        universe=universe,
        db_path=db,
        base_symbols=["BTC/USDT:USDT"],
        base_timeframes=["1m", "1h"],
        lookback_days=lambda tf: 1,
        pause_seconds=0,
        now_ms=lambda: t + DAY,
    )
    return arm, store, universe


def test_arm_collection_computes_members_plus_held(tmp_path: Path) -> None:
    arm, store, universe = _arm(tmp_path, ["X", "BTC/USDT:USDT"])
    conn = sqlite3.connect(tmp_path / "a.db")
    conn.execute("CREATE TABLE open_positions (symbol TEXT, timeframe TEXT)")
    conn.execute("INSERT INTO open_positions VALUES ('OLD', '4h')")
    conn.commit()
    conn.close()
    plan = arm.compute()
    assert plan.arm_symbols == {"X", "BTC/USDT:USDT", "OLD"}
    assert ("BTC/USDT:USDT", "1m") not in arm.arm_pairs(plan)  # 기본 격자는 기본 경로가 채운다
    assert ("BTC/USDT:USDT", "1w") in arm.arm_pairs(plan)
    added, removed = arm.adopt(plan)
    assert added == ["BTC/USDT:USDT", "OLD", "X"] and removed == []
    store.close()
    universe.close()


def test_on_candle_builds_3h_only_for_arm_symbols(tmp_path: Path) -> None:
    arm, store, universe = _arm(tmp_path, ["X"])
    arm.adopt(arm.compute())
    start = 1000 * DAY
    for sym in ("X", "NOT"):
        _hours(store, sym, start, 3)
        arm.on_candle(Candle(sym, "1h", start + 2 * H, 1, 1, 1, 1, 1))
    assert len(store.load("X", "3h")) == 1 and store.load("NOT", "3h").empty
    store.close()
    universe.close()


def test_pair_stream_splits_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[tuple[str, str]]] = []

    async def fake_conn(store: Any, pairs: Any, **kw: Any) -> None:
        seen.append(list(pairs))

    monkeypatch.setattr(stream_mod, "_stream_connection", fake_conn)
    pairs = [(f"S{i}/USDT:USDT", "1m") for i in range(450)]
    asyncio.run(stream_mod.stream_kline_pairs(None, pairs))  # type: ignore[arg-type]
    assert [len(c) for c in seen] == [200, 200, 50]
    assert stream_mod.build_pair_stream_path([("BTC/USDT:USDT", "1w"), ("X/USDT:USDT", "1m")]) == (
        "/stream?streams=btcusdt@kline_1w/xusdt@kline_1m"
    )


def test_one_dead_connection_takes_the_others_down(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled: list[int] = []

    async def fake_conn(store: Any, pairs: Any, **kw: Any) -> None:
        if pairs[0][0] == "S0":
            await asyncio.sleep(0.01)
            raise ConnectionError("dead")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    monkeypatch.setattr(stream_mod, "_stream_connection", fake_conn)
    pairs = [(f"S{i}", "1m") for i in range(400)]
    with pytest.raises(ExceptionGroup):
        asyncio.run(stream_mod.stream_kline_pairs(None, pairs))  # type: ignore[arg-type]
    assert cancelled == [1]


class _FakeArm:
    """계획이 한 번 바뀌는 가짜 — `_stream_arm_plan`의 재접속 흐름만 본다."""

    def __init__(self) -> None:
        self.plan = build_plan(["A"], ["1m"])
        self.next = build_plan(["A"], ["1m"], ["N"])
        self.filled: list[tuple[list[tuple[str, str]], frozenset[str]]] = []

    def compute(self) -> CollectionPlan:
        return self.next

    def fill(self, pairs: list[tuple[str, str]], arm_symbols: frozenset[str]) -> int:
        self.filled.append((pairs, arm_symbols))
        return 7

    def adopt(self, plan: CollectionPlan) -> tuple[list[str], list[str]]:
        self.plan = plan
        return ["N"], []

    def extend_history(self) -> int:
        return 0

    def on_candle(self, candle: Candle) -> None:
        return None


def test_plan_change_fills_new_pairs_then_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    streams: list[list[tuple[str, str]]] = []

    async def fake_pairs(store: Any, pairs: Any, **kw: Any) -> None:
        streams.append(list(pairs))
        if len(streams) == 1:
            await asyncio.sleep(10)  # 계획이 바뀌어 취소된다
        raise ConnectionError("두 번째 연결은 바로 끊긴다")

    monkeypatch.setattr(collector, "stream_kline_pairs", fake_pairs)
    arm = _FakeArm()
    with pytest.raises(ConnectionError):
        asyncio.run(
            collector._stream_arm_plan(
                None,  # type: ignore[arg-type]
                arm,  # type: ignore[arg-type]
                heartbeat=lambda: None,
                idle_timeout_seconds=None,
                on_connect=lambda: None,
                check_seconds=0.01,
            )
        )
    assert streams[0] == [("A", "1m")]
    assert ("N", "1w") in streams[1] and len(streams[1]) == 1 + len(ARM_STREAM_TIMEFRAMES)
    new_pairs, symbols = arm.filled[0]
    assert ("A", "1m") not in new_pairs and symbols == {"N"}


# ------------------------------------------------------------------ 이력 깊이(오래된 존 기억)


class _History:
    """요청한 `since`부터 `until`(없으면 지금)까지 1시간 간격 봉을 주는 가짜 거래소."""

    def __init__(self, listed: int) -> None:
        self.listed = listed
        self.calls: list[tuple[str, str, int]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        from data.models import timeframe_to_ms

        assert since is not None and limit is not None
        self.calls.append((symbol, timeframe, since))
        tf = timeframe_to_ms(timeframe)
        start = max(since, self.listed)
        start = -(-start // tf) * tf
        return [[t, 1.0, 2.0, 0.5, 1.5, 1.0] for t in range(start, start + limit * tf, tf)]


def test_backfill_pauses_between_full_pages(tmp_path: Path) -> None:
    from data.backfill import backfill_symbol

    sleeps: list[float] = []
    with OhlcvStore(tmp_path / "p.db") as store:
        backfill_symbol(
            _History(0),
            store,
            "A",
            "1h",
            0,
            until_ms=5 * 1000 * H,
            limit=1000,
            sleeper=sleeps.append,
            now_ms=lambda: 10**13,
            page_pause_seconds=0.2,
        )
    assert sleeps.count(0.2) == 4  # 5쪽 사이 4번


def test_extend_history_reaches_back_once_and_rebuilds_3h(tmp_path: Path) -> None:
    arm, store, universe = _arm(tmp_path, ["X"])
    now = arm._now_ms()
    ex = _History(listed=now - 100 * DAY)
    arm._exchange = ex
    arm._history_days = 60
    arm._now_ms = lambda: now
    arm.adopt(arm.compute())
    _hours(store, "X", now - 10 * DAY, 24 * 9)  # 최근 9일치만 있는 1h
    build_derived(store, "X", "3h")
    first_3h = store.first_open_time("X", "3h")
    assert first_3h is not None and first_3h >= now - 10 * DAY
    arm.extend_history()
    assert store.first_open_time("X", "1h") == now - 60 * DAY
    assert store.first_open_time("X", "3h") == now - 60 * DAY  # 3h도 앞 구간까지 다시 접었다
    one_hour = [c for c in ex.calls if c[:2] == ("X", "1h")]
    assert one_hour and all(c[2] == now - 60 * DAY for c in one_hour[:1])
    assert not any(c[1] == "1m" for c in ex.calls)  # 1분봉은 늘리지 않는다
    ex.calls.clear()
    arm.extend_history()
    assert ex.calls == []  # 한 번만 시도한다
    store.close()
    universe.close()


def test_new_pairs_start_at_the_history_depth(tmp_path: Path) -> None:
    arm, store, universe = _arm(tmp_path, ["X"])
    now = arm._now_ms()
    ex = _History(listed=0)
    arm._exchange = ex
    arm._history_days = 60
    arm._now_ms = lambda: now
    plan = arm.compute()
    arm.adopt(plan)
    arm.fill([("X", "4h"), ("X", "1m")], plan.arm_symbols)
    since: dict[str, int] = {}
    for _, tf, at in ex.calls:
        since.setdefault(tf, at)  # 첫 쪽의 시작
    assert since["4h"] == now - 60 * DAY  # 룩백(1일)보다 이력 깊이가 길다
    assert since["1m"] == now - 1 * DAY  # 1분봉은 룩백 그대로
    store.close()
    universe.close()
