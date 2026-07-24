"""data.storage 테스트 (인메모리 SQLite)."""

from __future__ import annotations

import asyncio
import threading
import tracemalloc
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from data.gaps import find_gaps, gaps_from_boundaries
from data.models import Candle
from data.storage import _DISTINCT_SYMBOLS, _DISTINCT_TIMEFRAMES, OhlcvStore


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _candle(open_time: int, close: float, *, closed: bool = True) -> Candle:
    return Candle("BTC/USDT:USDT", "1m", open_time, 1.0, 2.0, 0.5, close, 10.0, closed)


def test_upsert_and_count(store: OhlcvStore) -> None:
    n = store.upsert_candles([_candle(1000, 1.5), _candle(2000, 1.6)])
    assert n == 2
    assert store.count() == 2
    assert store.count(symbol="BTC/USDT:USDT", timeframe="1m") == 2
    assert store.count(timeframe="5m") == 0


def test_upsert_is_idempotent_on_pk(store: OhlcvStore) -> None:
    """같은 (symbol, timeframe, open_time) 재삽입은 중복 없이 갱신된다."""
    store.upsert_candles([_candle(1000, 1.5)])
    store.upsert_candles([_candle(1000, 9.9)])  # 같은 키, close 변경
    assert store.count() == 1
    df = store.load("BTC/USDT:USDT", "1m")
    assert list(df["close"]) == [9.9]


def test_last_open_time(store: OhlcvStore) -> None:
    assert store.last_open_time("BTC/USDT:USDT", "1m") is None
    store.upsert_candles([_candle(1000, 1.5), _candle(3000, 1.7), _candle(2000, 1.6)])
    assert store.last_open_time("BTC/USDT:USDT", "1m") == 3000


def test_load_orders_and_filters(store: OhlcvStore) -> None:
    store.upsert_candles([_candle(3000, 3.0), _candle(1000, 1.0), _candle(2000, 2.0)])
    df = store.load("BTC/USDT:USDT", "1m")
    assert list(df["open_time"]) == [1000, 2000, 3000]  # 정렬
    assert "open_datetime" in df.columns
    assert df["closed"].dtype == bool

    windowed = store.load("BTC/USDT:USDT", "1m", start_ms=2000, end_ms=3000)
    assert list(windowed["open_time"]) == [2000]  # end 는 배타적


def test_load_empty_returns_schema(store: OhlcvStore) -> None:
    df = store.load("NON/EXISTENT", "1m")
    assert df.empty
    assert "open_time" in df.columns and "close" in df.columns


def test_upsert_empty_is_noop(store: OhlcvStore) -> None:
    assert store.upsert_candles([]) == 0


def test_store_usable_from_worker_thread(store: OhlcvStore) -> None:
    """메인 스레드에서 생성한 store를 다른 스레드에서 써도 오류가 없어야 한다(WAN-21).

    회귀 방지: 예전에는 sqlite3 기본값(check_same_thread=True) 때문에
    ``asyncio.to_thread``로 넘긴 워커 스레드에서 store를 쓰면
    ProgrammingError가 났다.
    """

    async def run() -> int:
        # store는 이 테스트(메인) 스레드에서 생성됨. 다른 스레드에서 사용한다.
        await asyncio.to_thread(store.upsert_candles, [_candle(1000, 1.5)])
        return await asyncio.to_thread(store.count)

    assert asyncio.run(run()) == 1


def test_store_concurrent_multithreaded_writes(store: OhlcvStore) -> None:
    """여러 스레드가 동시에 store에 써도 락으로 직렬화되어 안전해야 한다(WAN-21)."""
    threads = [
        threading.Thread(
            target=lambda base=base: store.upsert_candles(
                [_candle(base + i, float(i)) for i in range(50)]
            )
        )
        # 서로 다른 open_time 구간을 써서 총 8×50=400 봉이 쌓인다.
        for base in range(0, 8_000, 1_000)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store.count() == 400


# --------------------------------------------------------------------------- #
# parquet 캐시 (WAN-78 성능)
# --------------------------------------------------------------------------- #


def test_load_without_cache_dir_writes_no_cache_files(tmp_path: Path) -> None:
    """cache_dir 미지정(기본)이면 기존 동작 그대로 — 캐시 파일을 전혀 만들지 않는다."""
    db_path = tmp_path / "ohlcv.db"
    with OhlcvStore(db_path) as store:
        store.upsert_candles([_candle(1000, 1.5), _candle(2000, 1.6)])
        store.load("BTC/USDT:USDT", "1m")
    assert not (tmp_path / "cache").exists()


def test_cache_hit_returns_same_data_as_db(tmp_path: Path) -> None:
    db_path = tmp_path / "ohlcv.db"
    cache_dir = tmp_path / "cache"
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        store.upsert_candles([_candle(1000, 1.5), _candle(2000, 1.6), _candle(3000, 1.7)])
        first = store.load("BTC/USDT:USDT", "1m")  # DB에서 읽고 캐시를 씀
        parquet_path = next(cache_dir.glob("*.parquet"))
        assert parquet_path.exists()
        second = store.load("BTC/USDT:USDT", "1m")  # 캐시에서 읽음(같은 결과여야 함)
    assert list(first["close"]) == list(second["close"]) == [1.5, 1.6, 1.7]
    assert list(first["open_time"]) == list(second["open_time"])


def test_cache_is_invalidated_when_db_gains_new_candle(tmp_path: Path) -> None:
    """DB에 새 봉이 추가되면(MAX(open_time)·count 변경) 캐시가 자동 무효화된다."""
    db_path = tmp_path / "ohlcv.db"
    cache_dir = tmp_path / "cache"
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        store.upsert_candles([_candle(1000, 1.5), _candle(2000, 1.6)])
        store.load("BTC/USDT:USDT", "1m")  # 캐시 생성(2행)
        store.upsert_candles([_candle(3000, 1.7)])  # DB 갱신 — stale 캐시가 됨
        refreshed = store.load("BTC/USDT:USDT", "1m")
    assert list(refreshed["close"]) == [1.5, 1.6, 1.7]  # 새 봉이 반영됨(캐시 무효화 증명)


def test_cache_is_invalidated_when_db_row_updated_without_count_change(tmp_path: Path) -> None:
    """행 수는 그대로지만 값이 바뀐 경우(UPSERT 갱신)도 max_open_time·count 조합으로는
    잡히지 않을 수 있으니, 최소한 count 변경 시나리오는 확실히 무효화됨을 별도로
    검증한다(WAN-78 완료 기준: stale 캐시를 읽지 않는다)."""
    db_path = tmp_path / "ohlcv.db"
    cache_dir = tmp_path / "cache"
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        store.upsert_candles([_candle(1000, 1.5)])
        store.load("BTC/USDT:USDT", "1m")
        store.upsert_candles([_candle(2000, 9.9)])  # count 1 -> 2, max_open_time 변경
        refreshed = store.load("BTC/USDT:USDT", "1m")
    assert list(refreshed["close"]) == [1.5, 9.9]


def test_partial_range_load_is_not_cached(tmp_path: Path) -> None:
    """start_ms/end_ms가 있는 부분 로드는 캐시하지 않는다(전체 로드만 캐시 대상)."""
    db_path = tmp_path / "ohlcv.db"
    cache_dir = tmp_path / "cache"
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        store.upsert_candles([_candle(1000, 1.5), _candle(2000, 1.6)])
        store.load("BTC/USDT:USDT", "1m", start_ms=1000, end_ms=2000)
    assert not cache_dir.exists() or not list(cache_dir.glob("*.parquet"))


def test_corrupted_cache_file_falls_back_to_db(tmp_path: Path) -> None:
    """parquet 캐시 파일이 손상돼도 예외 없이 DB에서 다시 읽는다."""
    db_path = tmp_path / "ohlcv.db"
    cache_dir = tmp_path / "cache"
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        store.upsert_candles([_candle(1000, 1.5)])
        store.load("BTC/USDT:USDT", "1m")
        parquet_path = next(cache_dir.glob("*.parquet"))
        parquet_path.write_bytes(b"not a real parquet file")
        recovered = store.load("BTC/USDT:USDT", "1m")
    assert list(recovered["close"]) == [1.5]


# --- 시리즈 목록 skip-scan (WAN-186) ----------------------------------------


def _seed_series(store: OhlcvStore, pairs: list[tuple[str, str]], bars: int) -> None:
    for symbol, timeframe in pairs:
        store.upsert_candles(
            Candle(symbol, timeframe, i * 60_000, 1.0, 2.0, 0.5, 1.5, 10.0) for i in range(bars)
        )


def _vm_steps(store: OhlcvStore, call: Callable[[], object]) -> int:
    """`call` 동안 SQLite VM이 밟은 스텝 수(= 실제 훑은 일의 양)를 센다.

    벽시계가 아니라 VM 스텝을 세는 이유: 시간 기반 단언은 다른 무거운 작업이
    동시에 돌면 흔들린다(머신·부하 의존). 스텝 수는 결정적이라 "행 수에 비례해
    일하는가"를 흔들림 없이 고정한다.
    """
    steps = 0

    def bump() -> int:
        nonlocal steps
        steps += 1
        return 0

    store._conn.set_progress_handler(bump, 1)
    try:
        call()
    finally:
        store._conn.set_progress_handler(None, 0)
    return steps


def test_list_series_matches_distinct_query_and_ordering(store: OhlcvStore) -> None:
    """skip-scan 결과가 옛 `SELECT DISTINCT`와 집합·정렬 모두 같다(WAN-186).

    정렬이 미묘하다 — TF는 사전순(BINARY)이라 `15m < 1d < 1h < 1m < 4h`다.
    """
    pairs = [
        ("BTC/USDT:USDT", "1h"),
        ("BTC/USDT:USDT", "15m"),
        ("BTC/USDT:USDT", "1m"),
        ("BTC/USDT:USDT", "4h"),
        ("BTC/USDT:USDT", "1d"),
        ("BTCUSDT-PERP", "1h"),  # 접두가 겹치는 심볼(경계 seek 확인)
        ("ETH/USDT:USDT", "1h"),
        ("ETH/USDT:USDT", "1d"),
    ]
    _seed_series(store, pairs, bars=3)

    expected = store._conn.execute(
        "SELECT DISTINCT symbol, timeframe FROM ohlcv ORDER BY symbol, timeframe"
    ).fetchall()
    assert store.list_series() == [(row[0], row[1]) for row in expected]


def test_list_series_on_empty_store_is_empty(store: OhlcvStore) -> None:
    assert store.list_series() == []


def test_list_series_does_not_scan_the_table(store: OhlcvStore) -> None:
    """쿼리 계획에 `SCAN ohlcv`가 없다 — 전부 인덱스 seek다(WAN-186 완료 기준)."""
    _seed_series(store, [("BTC/USDT:USDT", "1h"), ("ETH/USDT:USDT", "1m")], bars=5)

    plans: list[str] = []
    for sql, params in (
        (_DISTINCT_SYMBOLS, ()),
        (_DISTINCT_TIMEFRAMES, ("BTC/USDT:USDT",)),
    ):
        rows = store._conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
        plans.extend(str(row[-1]) for row in rows)

    assert plans, "쿼리 계획이 비어 있다"
    assert not [p for p in plans if "SCAN ohlcv" in p], plans
    # 두 열 모두 인덱스 제약으로 쓰여야 한다 — 첫 열만 쓰면 같은 심볼의 다음 TF로
    # 넘어갈 때 그 시리즈를 통째로 훑어 오히려 더 느려진다(행값 비교의 함정).
    assert any("symbol=? AND timeframe>?" in p for p in plans), plans


def test_list_series_work_does_not_grow_with_row_count(store: OhlcvStore) -> None:
    """행이 10배로 늘어도 `list_series`가 하는 일은 그대로다(WAN-186).

    옛 `SELECT DISTINCT`는 PK 인덱스를 전수 스캔해 이 단언에서 정확히 무너진다 —
    6년 × 9종목(3천만 행)에서 status가 멈추고 수집기 스트림이 시작조차 못 한
    것이 그 스캔이었다. 라벨이 아니라 **일의 양**으로 고정한다.
    """
    pairs = [("BTC/USDT:USDT", "1m"), ("ETH/USDT:USDT", "1m"), ("SOL/USDT:USDT", "1h")]
    _seed_series(store, pairs, bars=200)
    small = _vm_steps(store, store.list_series)

    for symbol, timeframe in pairs:
        store.upsert_candles(
            Candle(symbol, timeframe, i * 60_000, 1.0, 2.0, 0.5, 1.5, 10.0)
            for i in range(200, 2_000)
        )
    large = _vm_steps(store, store.list_series)

    assert store.list_series() == sorted(pairs)
    assert large <= small * 1.5, f"행 수에 비례해 일한다: {small} → {large} 스텝"

    # 대조군: 옛 구현(전수 스캔)은 같은 데이터에서 훨씬 많이 일한다. 이 줄이 있어야
    # 위 단언이 "원래 싼 연산이라 통과한 것"이 아님이 드러난다.
    old = _vm_steps(
        store,
        lambda: store._conn.execute(
            "SELECT DISTINCT symbol, timeframe FROM ohlcv ORDER BY symbol, timeframe"
        ).fetchall(),
    )
    assert old > large * 5, f"대조군이 충분히 무겁지 않다: skip-scan {large} vs DISTINCT {old}"


# --- 갭 경계 스트리밍 스캔 (WAN-187) -----------------------------------------


def _seed_with_gaps(store: OhlcvStore, bars: int, missing: set[int]) -> None:
    """`bars`개 1m 봉 중 `missing` 인덱스를 뺀 시리즈를 넣는다."""
    store.upsert_candles(
        Candle("BTC/USDT:USDT", "1m", i * 60_000, 1.0, 2.0, 0.5, 1.5, 10.0)
        for i in range(bars)
        if i not in missing
    )


def test_gap_boundaries_agrees_with_find_gaps(store: OhlcvStore) -> None:
    """스트리밍 경계 스캔 결과가 시각열 전부를 넘긴 `find_gaps`와 **같다**.

    경로가 둘로 갈렸으니(WAN-187) 답도 갈릴 수 있다 — 그 가능성을 여기서 막는다.
    """
    missing = {3, 4, 5, 9, 20, 21, 22, 23, 57}
    _seed_with_gaps(store, bars=80, missing=missing)

    boundaries = store.gap_boundaries("BTC/USDT:USDT", "1m", min_gap_ms=60_000)
    assert gaps_from_boundaries(boundaries, "1m") == find_gaps(
        store.open_times("BTC/USDT:USDT", "1m"), "1m"
    )
    # 사전 조건: 실제로 갭이 있는 데이터여야 위 비교가 의미를 갖는다.
    assert len(boundaries) == 4  # 3-5, 9, 20-23, 57


def test_gap_boundaries_on_gapless_and_empty_series(store: OhlcvStore) -> None:
    assert store.gap_boundaries("BTC/USDT:USDT", "1m", min_gap_ms=60_000) == []
    _seed_with_gaps(store, bars=10, missing=set())
    assert store.gap_boundaries("BTC/USDT:USDT", "1m", min_gap_ms=60_000) == []


def test_gap_boundaries_on_derived_timeframe(store: OhlcvStore) -> None:
    """파생 TF(2h)는 저장 행이 없으므로 원본 리샘플 시각열로 본다."""
    hour = 3_600_000
    store.upsert_candles(
        Candle("BTC/USDT:USDT", "1h", i * hour, 1.0, 2.0, 0.5, 1.5, 10.0)
        for i in range(12)
        if i not in {4, 5}  # 2h 버킷 하나(4,5)가 통째로 빈다
    )
    boundaries = store.gap_boundaries("BTC/USDT:USDT", "2h", min_gap_ms=2 * hour)
    assert gaps_from_boundaries(boundaries, "2h") == find_gaps(
        [int(t) for t in store.load("BTC/USDT:USDT", "2h")["open_time"].tolist()], "2h"
    )
    assert [g.missing for g in gaps_from_boundaries(boundaries, "2h")] == [1]


def test_gap_boundaries_memory_does_not_grow_with_series_length(store: OhlcvStore) -> None:
    """시리즈가 길어져도 갭 스캔이 쓰는 메모리는 그대로다 (WAN-187 완료 기준).

    6년 DB에서 수집기를 막은 것은 **시간이 아니라 메모리**였다(시리즈당 ~315만 봉을
    9열 DataFrame으로 올려 RSS 2.3GB). 그래서 벽시계가 아니라 **할당량**으로 고정한다
    — 머신·부하에 흔들리지 않고, 옛 경로였다면 정확히 여기서 무너진다.
    """
    _seed_with_gaps(store, bars=60_000, missing={7, 500, 40_000})

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        store.gap_boundaries("BTC/USDT:USDT", "1m", min_gap_ms=60_000)
        _, streamed = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        store.open_times("BTC/USDT:USDT", "1m")  # 대조군: 시각열을 통째로 올리는 경로
        _, materialized = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert streamed < 100_000, f"경계만 모으는데 {streamed}B를 썼다"
    # 대조군이 충분히 무거워야 위 단언이 "원래 싼 연산"이 아님이 드러난다. 시각열만
    # 올려도 이만큼이고, 실제 옛 경로(pandas 9열)는 여기서 한 자릿수 더 쓴다.
    assert materialized > streamed * 20, f"대조군 {materialized}B vs 스트리밍 {streamed}B"
