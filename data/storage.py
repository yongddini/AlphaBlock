"""OHLCV SQLite 저장소.

`(symbol, timeframe, open_time)`을 기본키로 두고 UPSERT하므로 재수집/중복이
무해하다. 조회는 pandas `DataFrame`으로 반환한다.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

import pandas as pd

from data.models import Candle, timeframe_to_ms
from data.resample import resample_ohlcv

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

# 거래소에서 직접 수집하지 않고 하위 TF에서 리샘플로 파생하는 타임프레임 → 원본 TF.
# 2h는 미수집(WAN-6은 1h까지만)이라 1h 두 봉을 합쳐 무손실로 만든다 (WAN-24).
_DERIVED_TIMEFRAMES: dict[str, str] = {"2h": "1h"}

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

    스레드 안전하다: 백필은 ``asyncio.to_thread``로 워커 스레드에서, 실시간
    스트림은 이벤트 루프 스레드에서 같은 저장소를 사용하므로, 커넥션을
    ``check_same_thread=False``로 열고 모든 접근을 락으로 직렬화한다.

    컨텍스트 매니저로 사용할 수 있다::

        with OhlcvStore("data/ohlcv.db") as store:
            store.upsert_candles(candles)
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        # ":memory:"가 아니면 상위 디렉터리를 보장한다.
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + 락: 백필(워커 스레드)과 스트림(루프 스레드)이
        # 같은 커넥션을 사용해도 안전하도록 모든 접근을 self._lock으로 직렬화한다.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
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
        with self._lock, self._conn:  # 락으로 스레드 직렬화 + 트랜잭션 자동 커밋/롤백
            self._conn.executemany(_UPSERT, rows)
        return len(rows)

    def last_open_time(self, symbol: str, timeframe: str) -> int | None:
        """해당 심볼·타임프레임의 가장 최근 `open_time`. 없으면 None."""
        with self._lock:
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
        with self._lock:
            cur = self._conn.execute(f"SELECT COUNT(*) FROM ohlcv{where}", params)
            (value,) = cur.fetchone()
        return int(value)

    def open_times(self, symbol: str, timeframe: str) -> list[int]:
        """해당 심볼·타임프레임의 모든 `open_time`을 오름차순으로 반환한다.

        전체 시리즈를 `load()`로 DataFrame에 올리지 않고 시각열만 가볍게 가져와
        갭·중복·연속성 검증(WAN-44)에 쓴다.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT open_time FROM ohlcv WHERE symbol = ? AND timeframe = ? "
                "ORDER BY open_time ASC",
                (symbol, timeframe),
            )
            return [int(row[0]) for row in cur.fetchall()]

    def list_series(self) -> list[tuple[str, str]]:
        """저장된 (symbol, timeframe) 조합 목록을 정렬해 반환한다."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT symbol, timeframe FROM ohlcv ORDER BY symbol, timeframe"
            )
            rows = cur.fetchall()
        return [(row[0], row[1]) for row in rows]

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

        `2h`처럼 파생 타임프레임(`_DERIVED_TIMEFRAMES`)은 저장소에 없으므로 원본
        TF(1h)를 조회해 리샘플한 결과를 다른 TF와 동일한 인터페이스로 반환한다.
        """
        if timeframe in _DERIVED_TIMEFRAMES:
            return self._load_derived(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
        return self._load_native(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)

    def _load_derived(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int | None,
        end_ms: int | None,
    ) -> pd.DataFrame:
        """파생 TF를 원본 TF 리샘플로 조회한다(경계 버킷이 온전하도록 창을 넓힘)."""
        source_tf = _DERIVED_TIMEFRAMES[timeframe]
        tgt_ms = timeframe_to_ms(timeframe)
        # 경계 상위 봉을 구성하려면 요청 창 밖의 원본 봉도 필요하다: 시작은 버킷
        # 경계로 내림, 끝은 한 버킷만큼 넓혀 로드한 뒤 결과를 [start, end)로 자른다.
        src_start = None if start_ms is None else (start_ms // tgt_ms) * tgt_ms
        src_end = None if end_ms is None else end_ms + tgt_ms
        base = self._load_native(symbol, source_tf, start_ms=src_start, end_ms=src_end)
        out = resample_ohlcv(base, source_tf, timeframe)
        if start_ms is not None:
            out = out[out["open_time"] >= start_ms]
        if end_ms is not None:
            out = out[out["open_time"] < end_ms]
        return out.reset_index(drop=True)

    def _load_native(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int | None,
        end_ms: int | None,
    ) -> pd.DataFrame:
        """저장소에 직접 저장된 TF를 조회한다."""
        query = "SELECT " + ", ".join(_COLUMNS) + " FROM ohlcv WHERE symbol = ? AND timeframe = ?"
        params: list[object] = [symbol, timeframe]
        if start_ms is not None:
            query += " AND open_time >= ?"
            params.append(start_ms)
        if end_ms is not None:
            query += " AND open_time < ?"
            params.append(end_ms)
        query += " ORDER BY open_time ASC"

        with self._lock:
            df = pd.read_sql_query(query, self._conn, params=params)
        if df.empty:
            df = pd.DataFrame(columns=_COLUMNS)
        df["closed"] = df["closed"].astype(bool)
        df["open_datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    def close(self) -> None:
        """연결을 닫는다."""
        with self._lock:
            self._conn.close()
