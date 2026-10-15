"""WAL 증식 사고(WAN-451 ③, 2026-10-16 서버) 회귀 — WAL 크기 상한 · 엔진 상태 중복 저장 제거.

사고: 수집기와 러너가 같은 `ohlcv.db`(WAL)를 쓰는데 WAL이 **2.46GB**까지 자랐고, 그 위에서
자동 체크포인트를 탄 수집기 커밋이 이벤트 루프를 300초+ 멈춰 워치독이 프로세스를 죽였다.
고친 것 둘을 **동작**으로 고정한다:

1. `configure_connection`이 `journal_size_limit`을 건다 — 체크포인트가 WAL을 리셋할 때 파일이
   최고 수위 크기로 남지 않고 상한으로 잘린다(설정값만이 아니라 실제 파일 크기로 확인).
2. `EngineStateStore.save`가 `saved_ms`만 다른 같은 스냅샷은 DB에 다시 쓰지 않는다 — 러너가
   시리즈 ~310개 × (서브스텝 + 폴링 끝)마다 커밋하던 것이 WAL 쓰기량의 대부분이었다.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import data.sqlite_util as sqlite_util
from data.sqlite_util import WAL_JOURNAL_SIZE_LIMIT_BYTES, configure_connection
from live.engine_state import EngineStateStore, PersistedZoneWatch, SeriesStateSnapshot

# -- 1. WAL 크기 상한 -----------------------------------------------------------


def test_configure_connection_sets_journal_size_limit(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "a.db")
    try:
        configure_connection(conn)
        (limit,) = conn.execute("PRAGMA journal_size_limit").fetchone()
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert int(limit) == WAL_JOURNAL_SIZE_LIMIT_BYTES == 64 * 1024 * 1024
    assert str(mode).lower() == "wal"


def test_configure_connection_is_idempotent(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "a.db")
    try:
        configure_connection(conn)
        configure_connection(conn)
        (limit,) = conn.execute("PRAGMA journal_size_limit").fetchone()
    finally:
        conn.close()
    assert int(limit) == WAL_JOURNAL_SIZE_LIMIT_BYTES


def _grow_then_reset_wal(db: Path) -> tuple[int, int]:
    """WAL을 키우고 체크포인트 → 다음 쓰기로 리셋시킨다 → (리셋 전 크기, 리셋 후 크기)."""
    conn = sqlite3.connect(db)
    try:
        configure_connection(conn)
        conn.execute("CREATE TABLE t (x BLOB)")
        with conn:  # 자동 체크포인트(1000페이지)보다 작게 한 트랜잭션으로 키운다.
            conn.executemany("INSERT INTO t VALUES (?)", [(os.urandom(1000),) for _ in range(2500)])
        grown = os.path.getsize(f"{db}-wal")
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        with conn:  # 체크포인트가 다 옮긴 뒤의 첫 쓰기가 WAL을 처음부터 다시 쓴다(reset).
            conn.execute("INSERT INTO t VALUES (x'00')")
        after = os.path.getsize(f"{db}-wal")
    finally:
        conn.close()
    return grown, after


def test_wal_file_is_truncated_to_limit_on_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🚨 핵심: 상한이 있으면 리셋 때 파일이 상한으로 잘리고, 없으면 최고 수위로 남는다.

    64MiB를 실제로 채우면 테스트가 무거워 상한만 작게 바꿔 같은 메커니즘을 본다(설정값은
    `configure_connection`이 호출 시점에 모듈 상수에서 읽는다).
    """
    small = 64 * 1024
    monkeypatch.setattr(sqlite_util, "WAL_JOURNAL_SIZE_LIMIT_BYTES", small)
    grown, after = _grow_then_reset_wal(tmp_path / "capped.db")
    assert grown > 1_000_000  # 이 시험이 「원래 작아서 작다」가 아니게.
    assert after <= small

    monkeypatch.setattr(sqlite_util, "WAL_JOURNAL_SIZE_LIMIT_BYTES", -1)  # 상한 없음 = 옛 동작
    grown_u, after_u = _grow_then_reset_wal(tmp_path / "uncapped.db")
    assert after_u == grown_u  # 옛 동작: 최고 수위 크기 그대로 — 이게 2.46GB의 기전이다.


# -- 2. 엔진 상태 중복 저장 제거 -------------------------------------------------


def _snap(*, saved_ms: int, cursor: int = 1_000, inside: bool = False) -> SeriesStateSnapshot:
    return SeriesStateSnapshot(
        saved_ms=saved_ms,
        last_substep_time=cursor,
        last_htf_time=0,
        forming_bar=None,
        forming_low=None,
        forming_high=None,
        running_close=None,
        zones=[PersistedZoneWatch(zone=("long", 1, 2), was_inside=inside)],
    )


def _row(db: Path) -> tuple[int, str]:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT saved_ms, state_json FROM live_engine_state WHERE symbol='S' AND timeframe='1h'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return int(row[0]), str(row[1])


def test_unchanged_snapshot_is_written_once(tmp_path: Path) -> None:
    """`saved_ms`만 다른 같은 스냅샷 두 번 → DB 쓰기는 한 번(행 변경 수로 잰다)."""
    db = tmp_path / "s.db"
    store = EngineStateStore(db)
    try:
        before = store._conn.total_changes  # noqa: SLF001
        assert store.save("S", "1h", _snap(saved_ms=100)) is True
        mid = store._conn.total_changes  # noqa: SLF001
        assert store.save("S", "1h", _snap(saved_ms=200)) is False
        after = store._conn.total_changes  # noqa: SLF001
        assert mid - before == 1 and after == mid
        assert (store.writes, store.skipped) == (1, 1)
        # DB에는 처음 쓴 내용이 그대로 — `saved_ms`는 「내용이 마지막으로 바뀐 시각」이다.
        assert _row(db)[0] == 100
        loaded = store.load("S", "1h")
        assert loaded is not None and loaded.model_dump(exclude={"saved_ms"}) == _snap(
            saved_ms=200
        ).model_dump(exclude={"saved_ms"})
    finally:
        store.close()


@pytest.mark.parametrize(
    "changed",
    [
        {"cursor": 1_060},  # 커서 전진 = 매 분 서브스텝 저장 — 반드시 쓴다(크래시 안전성).
        {"inside": True},  # 존 표식만 바뀌어도 쓴다.
    ],
)
def test_changed_snapshot_is_written(tmp_path: Path, changed: dict[str, Any]) -> None:
    db = tmp_path / "s.db"
    store = EngineStateStore(db)
    try:
        store.save("S", "1h", _snap(saved_ms=100))
        assert store.save("S", "1h", _snap(saved_ms=200, **changed)) is True
        assert store.writes == 2
        saved_ms, raw = _row(db)
        assert saved_ms == 200
        assert SeriesStateSnapshot.model_validate_json(raw) == _snap(saved_ms=200, **changed)
    finally:
        store.close()


def test_series_are_compared_independently(tmp_path: Path) -> None:
    store = EngineStateStore(tmp_path / "s.db")
    try:
        assert store.save("S", "1h", _snap(saved_ms=1)) is True
        assert store.save("S", "4h", _snap(saved_ms=2)) is True  # 같은 내용이라도 다른 시리즈.
        assert store.save("T", "1h", _snap(saved_ms=3)) is True
        assert store.save("S", "4h", _snap(saved_ms=4)) is False
    finally:
        store.close()


def test_delete_forgets_so_the_same_content_is_written_again(tmp_path: Path) -> None:
    """삭제 뒤 같은 내용 저장이 건너뛰어지면 행이 영영 없다 — 복원 앵커를 잃는다."""
    db = tmp_path / "s.db"
    store = EngineStateStore(db)
    try:
        store.save("S", "1h", _snap(saved_ms=1))
        store.delete("S", "1h")
        assert store.load("S", "1h") is None
        assert store.save("S", "1h", _snap(saved_ms=2)) is True
        assert store.load("S", "1h") is not None
    finally:
        store.close()


def test_fresh_store_after_restart_always_writes_first(tmp_path: Path) -> None:
    """기억은 프로세스 안에만 있다 — 재시작한 저장소는 `load` 뒤에도 첫 저장을 쓴다."""
    db = tmp_path / "s.db"
    first = EngineStateStore(db)
    first.save("S", "1h", _snap(saved_ms=1))
    first.close()
    second = EngineStateStore(db)
    try:
        assert second.load("S", "1h") is not None
        assert second.save("S", "1h", _snap(saved_ms=2)) is True
        assert _row(db)[0] == 2
    finally:
        second.close()


class _FailingConn:
    """INSERT만 실패시키는 연결 대역 — 실패한 쓰기를 「썼다」로 기억하는지 본다."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.fail = True

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        if self.fail and sql.startswith("INSERT"):
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args)

    def __enter__(self) -> _FailingConn:
        self._real.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._real.__exit__(*exc)  # type: ignore[arg-type]


def test_failed_write_is_not_remembered(tmp_path: Path) -> None:
    """🚨 쓰기가 실패하면(서버 실측: `database is locked`) 다음 같은 내용 저장은 반드시 쓴다."""
    db = tmp_path / "s.db"
    store = EngineStateStore(db)
    real = store._conn  # noqa: SLF001
    failing = _FailingConn(real)
    store._conn = failing  # type: ignore[assignment]  # noqa: SLF001
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.save("S", "1h", _snap(saved_ms=1))
        assert store.writes == 0
        failing.fail = False
        assert store.save("S", "1h", _snap(saved_ms=2)) is True
    finally:
        store._conn = real  # noqa: SLF001
        store.close()
    assert _row(db)[0] == 2
