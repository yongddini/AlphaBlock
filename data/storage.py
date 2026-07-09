"""OHLCV SQLite 저장소.

`(symbol, timeframe, open_time)`을 기본키로 두고 UPSERT하므로 재수집/중복이
무해하다. 조회는 pandas `DataFrame`으로 반환한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

import pandas as pd

from data.models import Candle

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    open_time  INTEGER NOT NULL,
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    closed     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (symbol, timeframe, open_time)
)
"""

_UPSERT = """
INSERT INTO ohlcv
    (symbol, timeframe, open_time, open, high, low, close, volume, closed)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, timeframe, open_time) DO UPDATE SET
    open   = excluded.open,
    high   = excluded.high,
    low    = excluded.low,
    close  = excluded.close,
    volume = excluded.volume,
    closed = excluded.closed
"""

# `load`가 반환하는 컬럼 순서.
_COLUMNS = [
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "closed",
]


class OhlcvStore:
    """OHLCV 봉을 저장·조회하는 SQLite 래퍼.

    컨텍스트 매니저로 사용할 수 있다::

        with OhlcvStore("data/ohlcv.db") as store:
            store.upsert_candles(candles)
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        # ":memory:"가 아니면 상위 디렉터리를 보장한다.
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> OhlcvStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def upsert_candles(self, candles: Iterable[Candle]) -> int:
        """봉들을 UPSERT하고 처리한 행 수를 반환한다."""
        rows = [c.as_row() for c in candles]
        if not rows:
            return 0
        with self._conn:  # 트랜잭션 자동 커밋/롤백
            self._conn.executemany(_UPSERT, rows)
        return len(rows)

    def last_open_time(self, symbol: str, timeframe: str) -> int | None:
        """해당 심볼·타임프레임의 가장 최근 `open_time`. 없으면 None."""
        cur = self._conn.execute(
            "SELECT MAX(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        )
        (value,) = cur.fetchone()
        return int(value) if value is not None else None

    def count(self, symbol: str | None = None, timeframe: str | None = None) -> int:
        """저장된 봉 개수. 심볼·타임프레임으로 선택 필터링."""
        clauses: list[str] = []
        params: list[str] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if timeframe is not None:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = self._conn.execute(f"SELECT COUNT(*) FROM ohlcv{where}", params)
        (value,) = cur.fetchone()
        return int(value)

    def load(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> pd.DataFrame:
        """심볼·타임프레임(+기간)으로 조회해 `open_time` 오름차순 DataFrame 반환.

        `open_datetime` 컬럼(UTC)을 파생해 함께 제공한다. 결과가 없으면 동일한
        컬럼 스키마의 빈 DataFrame을 반환한다.
        """
        query = "SELECT " + ", ".join(_COLUMNS) + " FROM ohlcv WHERE symbol = ? AND timeframe = ?"
        params: list[object] = [symbol, timeframe]
        if start_ms is not None:
            query += " AND open_time >= ?"
            params.append(start_ms)
        if end_ms is not None:
            query += " AND open_time < ?"
            params.append(end_ms)
        query += " ORDER BY open_time ASC"

        df = pd.read_sql_query(query, self._conn, params=params)
        if df.empty:
            df = pd.DataFrame(columns=_COLUMNS)
        df["closed"] = df["closed"].astype(bool)
        df["open_datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    def close(self) -> None:
        """연결을 닫는다."""
        self._conn.close()
