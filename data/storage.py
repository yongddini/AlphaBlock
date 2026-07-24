"""OHLCV SQLite 저장소.

`(symbol, timeframe, open_time)`을 기본키로 두고 UPSERT하므로 재수집/중복이
무해하다. 조회는 pandas `DataFrame`으로 반환한다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

import pandas as pd

from data.models import Candle, timeframe_to_ms
from data.resample import resample_ohlcv
from data.sqlite_util import configure_connection

logger = logging.getLogger(__name__)

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

# 백필 전용 삽입: 이미 있는 행은 절대 건드리지 않는다 (WAN-175).
# 과거 구간 집계 봉을 채울 때 기존(거래소 수집) 봉이 비트 단위로 불변이어야
# 하므로, UPSERT가 아니라 충돌 시 무시로 넣는다.
_INSERT_IGNORE = """
INSERT INTO ohlcv
    (symbol, timeframe, open_time, open, high, low, close, volume, closed)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, timeframe, open_time) DO NOTHING
"""

# 시리즈 목록 skip-scan (WAN-186).
#
# `SELECT DISTINCT symbol, timeframe FROM ohlcv`는 SQLite가 loose index scan으로
# 최적화하지 못해 **PK 인덱스를 전수 스캔**한다. 3년·6종목일 땐 참을 만했지만
# WAN-182가 창을 6년 × 9종목으로 넓히자 3천만 행 스캔이 되어(실측 15초 이상)
# `alphablock status`가 멈추고, 더 나쁘게는 수집기 시작 갭 복구(`repair.repair_all`
# → `list_series`)가 **웹소켓 접속 직전에 매달려 스트림이 시작되지 못했다**.
#
# 재귀 CTE로 "다음 값"만 seek하면 비용이 행 수가 아니라 조합 수(~45)에 비례한다.
# 두 단으로 나누는 것이 핵심이다 — 한 단으로 `(symbol, timeframe) > (?, ?)` 행값
# 비교를 쓰면 SQLite가 인덱스 제약에 **첫 열만** 쓰기 때문에(`symbol>?`) 같은
# 심볼 안에서 다음 TF로 넘어갈 때 그 시리즈의 행을 전부 훑어 오히려 더 느리다
# (실측 51초). 아래처럼 `symbol = ? AND timeframe > ?`로 걸어야 두 열 모두 seek다.
_DISTINCT_SYMBOLS = """
WITH RECURSIVE syms(symbol) AS (
    SELECT (SELECT MIN(symbol) FROM ohlcv)
    UNION ALL
    SELECT (SELECT o.symbol FROM ohlcv o WHERE o.symbol > s.symbol ORDER BY o.symbol LIMIT 1)
    FROM syms s WHERE s.symbol IS NOT NULL
)
SELECT symbol FROM syms WHERE symbol IS NOT NULL
"""

_DISTINCT_TIMEFRAMES = """
WITH RECURSIVE tfs(timeframe) AS (
    SELECT (SELECT MIN(timeframe) FROM ohlcv WHERE symbol = ?1)
    UNION ALL
    SELECT (
        SELECT o.timeframe FROM ohlcv o
        WHERE o.symbol = ?1 AND o.timeframe > t.timeframe
        ORDER BY o.timeframe LIMIT 1
    )
    FROM tfs t WHERE t.timeframe IS NOT NULL
)
SELECT timeframe FROM tfs WHERE timeframe IS NOT NULL
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

    def __init__(self, db_path: str | Path, *, cache_dir: str | Path | None = None) -> None:
        self._path = str(db_path)
        # ":memory:"가 아니면 상위 디렉터리를 보장한다.
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + 락: 백필(워커 스레드)과 스트림(루프 스레드)이
        # 같은 커넥션을 사용해도 안전하도록 모든 접근을 self._lock으로 직렬화한다.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        # 락은 **이 프로세스 안**만 직렬화한다 — 수집기·러너·대시보드·백테스트는
        # 별개 프로세스라 SQLite 파일 락으로 부딪힌다(WAN-156 §4).
        configure_connection(self._conn)
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        # 심볼×TF 전체(start_ms/end_ms 없는) `load()`용 parquet 캐시(WAN-78 성능).
        # None(기본)이면 캐시를 쓰지 않는다 — 기존 동작·테스트 격리 보존.
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None

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

    def insert_candles_ignore(self, candles: Iterable[Candle]) -> int:
        """봉들을 삽입하되 **이미 있는 행은 건드리지 않고** 새로 넣은 행 수를 반환한다.

        `upsert_candles`와 달리 충돌 시 기존 행이 비트 단위로 보존된다(WAN-175
        과거 구간 집계 백필 전용 — 거래소에서 직접 수집한 봉을 리샘플 봉이
        덮어쓰는 사고를 SQL 수준에서 막는다).
        """
        rows = [c.as_row() for c in candles]
        if not rows:
            return 0
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(_INSERT_IGNORE, rows)
            return self._conn.total_changes - before

    def last_open_time(self, symbol: str, timeframe: str) -> int | None:
        """해당 심볼·타임프레임의 가장 최근 `open_time`. 없으면 None."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT MAX(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            )
            (value,) = cur.fetchone()
        return int(value) if value is not None else None

    def first_open_time(self, symbol: str, timeframe: str) -> int | None:
        """해당 심볼·타임프레임의 가장 오래된 `open_time`. 없으면 None."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT MIN(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = ?",
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
        """저장된 (symbol, timeframe) 조합 목록을 정렬해 반환한다.

        PK 인덱스를 **건너뛰며 seek**한다(skip-scan) — 결과·정렬은 예전
        `SELECT DISTINCT symbol, timeframe`과 같고 비용만 다르다(WAN-186).
        """
        with self._lock:
            symbols = [row[0] for row in self._conn.execute(_DISTINCT_SYMBOLS)]
            return [
                (symbol, row[0])
                for symbol in symbols
                for row in self._conn.execute(_DISTINCT_TIMEFRAMES, (symbol,))
            ]

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

    def _cache_paths(self, symbol: str, timeframe: str) -> tuple[Path, Path]:
        safe_symbol = symbol.replace("/", "-").replace(":", "-")
        assert self._cache_dir is not None
        base = self._cache_dir / f"{safe_symbol}__{timeframe}"
        return base.with_suffix(".parquet"), base.with_suffix(".meta.json")

    def _read_cache(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        """캐시가 있고 DB의 MAX(open_time)·행 수와 일치하면 캐시 DataFrame을 반환.

        불일치·손상·부재 시 None을 반환해 호출부가 DB에서 다시 읽게 한다(무효화).
        """
        parquet_path, meta_path = self._cache_paths(symbol, timeframe)
        if not parquet_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        fresh_max = self.last_open_time(symbol, timeframe)
        fresh_count = self.count(symbol=symbol, timeframe=timeframe)
        if meta.get("max_open_time") != fresh_max or meta.get("count") != fresh_count:
            return None
        try:
            return pd.read_parquet(parquet_path)
        except Exception:  # pragma: no cover - 손상된 캐시 파일 방어
            logger.warning("parquet 캐시 손상, DB에서 재조회: %s", parquet_path)
            return None

    def _write_cache(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        """전체 로드 결과를 parquet + 메타(max_open_time, count)로 캐시한다."""
        parquet_path, meta_path = self._cache_paths(symbol, timeframe)
        try:
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(parquet_path, index=False)
            meta = {
                "max_open_time": self.last_open_time(symbol, timeframe),
                "count": self.count(symbol=symbol, timeframe=timeframe),
            }
            meta_path.write_text(json.dumps(meta))
        except OSError:  # pragma: no cover - 캐시 쓰기 실패는 치명적이지 않음
            logger.warning("parquet 캐시 쓰기 실패(무시하고 계속): %s", parquet_path)

    def _load_native(
        self,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int | None,
        end_ms: int | None,
    ) -> pd.DataFrame:
        """저장소에 직접 저장된 TF를 조회한다.

        `cache_dir`가 설정돼 있고 전체 로드(`start_ms`/`end_ms` 모두 None)면 심볼×TF
        parquet 캐시를 먼저 확인한다 — 실험 스크립트가 같은 전체 히스토리를 반복 로드할
        때 매번 SQLite 전체 스캔을 피한다(WAN-78 성능). 부분 범위 로드는 캐시하지
        않는다.
        """
        cacheable = self._cache_dir is not None and start_ms is None and end_ms is None
        if cacheable:
            cached = self._read_cache(symbol, timeframe)
            if cached is not None:
                return cached

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
        if cacheable and not df.empty:
            self._write_cache(symbol, timeframe, df)
        return df

    def close(self) -> None:
        """연결을 닫는다."""
        with self._lock:
            self._conn.close()
