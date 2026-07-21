"""동시 DB 접근 회귀 테스트 (WAN-156 §4).

`sqlite3.OperationalError: database is locked` — 사용자가 백필 중 실제로 만난 오류다.
원인은 연결 설정이 없어 SQLite 기본값(`journal_mode=delete` · `busy_timeout=5000`)으로
돌았던 것이고, `delete` 모드는 **쓰는 동안 다른 연결이 읽지도 못한다.**

여기서 고정하는 것은 **동작**이다: 한 연결이 쓰기 트랜잭션을 열어 둔 채로 다른 연결이
읽어도 오류가 나지 않는다. 상시 DB를 읽는 페이퍼 러너(WAN-45)가 수집기의 대량
삽입과 겹치는 상황이 정확히 이 모양이다.

⚠️ `:memory:`는 WAL을 지원하지 않고 연결마다 DB가 따로라 이 테스트는 **파일 DB**로 돈다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from data.models import Candle
from data.storage import OhlcvStore

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
T0 = 1_700_000_000_000


def _candle(i: int) -> Candle:
    return Candle(SYMBOL, TF, T0 + i * 3_600_000, 100.0, 105.0, 95.0, 100.0, 10.0)


def test_store_enables_wal(tmp_path: Path) -> None:
    """파일 DB는 WAL로 열린다 — 읽기가 쓰기를 기다리지 않는 전제."""
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store:
        store.upsert_candles([_candle(0)])
        probe = sqlite3.connect(db)
        try:
            (mode,) = probe.execute("PRAGMA journal_mode").fetchone()
        finally:
            probe.close()
    assert str(mode).lower() == "wal"


def test_store_sets_busy_timeout(tmp_path: Path) -> None:
    """`busy_timeout`은 파일 속성이 아니라 **연결마다** 설정해야 한다."""
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store:
        (timeout,) = store._conn.execute("PRAGMA busy_timeout").fetchone()  # noqa: SLF001
    assert int(timeout) >= 30_000


def test_read_during_open_write_transaction_does_not_lock(tmp_path: Path) -> None:
    """🚨 회귀의 핵심: 쓰기 트랜잭션이 열려 있어도 다른 연결이 읽을 수 있다.

    WAL 이전(`journal_mode=delete`)에는 이 읽기가 `database is locked`로 죽었다.
    """
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as seed:
        seed.upsert_candles([_candle(i) for i in range(5)])

    with OhlcvStore(db) as writer, OhlcvStore(db) as reader:
        # 쓰기 트랜잭션을 커밋하지 않은 채 열어 둔다(대량 삽입 중인 수집기 흉내).
        writer._conn.execute("BEGIN IMMEDIATE")  # noqa: SLF001
        writer._conn.executemany(  # noqa: SLF001
            "INSERT OR REPLACE INTO ohlcv"
            " (symbol, timeframe, open_time, open, high, low, close, volume, closed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_candle(i).as_row() for i in range(5, 50)],
        )

        # 러너가 같은 DB를 읽는 순간 — 여기서 OperationalError가 나면 회귀다.
        df = reader.load(SYMBOL, TF)
        assert len(df) == 5  # 아직 커밋 전이라 예전 스냅샷을 본다(WAL 격리)

        writer._conn.commit()  # noqa: SLF001
        assert len(reader.load(SYMBOL, TF)) == 50


def test_memory_db_still_works(tmp_path: Path) -> None:
    """`:memory:`는 WAL을 못 켜지만 저장소 생성이 실패해선 안 된다(설정은 멱등·관대)."""
    with OhlcvStore(":memory:") as store:
        store.upsert_candles([_candle(0)])
        assert store.count(SYMBOL, TF) == 1
