"""펀딩비(funding rate) 수집·저장·조회.

무기한 선물의 펀딩비를 과거 백필 + 지속 최신화하여 SQLite에 저장하고, 백테스트·
실행에서 재사용할 조회/비용 헬퍼를 제공한다. 네트워크 의존부는 `FundingRateSource`
프로토콜로 추상화해 테스트에서 가짜 구현을 주입할 수 있다.

WAN-6의 거래소 클라이언트·SQLite UPSERT·지수 백오프 패턴을 그대로 따른다.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Protocol

import pandas as pd

from config.settings import Settings, get_settings
from data.exchange import create_exchange
from data.models import (
    FundingRate,
    funding_from_ccxt_current,
    funding_from_ccxt_history,
)

logger = logging.getLogger(__name__)

# ccxt fetch_funding_rate_history 페이지 최대 크기(바이낸스).
DEFAULT_LIMIT = 1000

Direction = Literal["long", "short"]

# 재시도 대상 예외. ccxt는 네트워크/레이트리밋 오류를 이 계층으로 던진다.
_RETRYABLE: tuple[type[Exception], ...]
try:  # pragma: no cover - import 형태만 분기
    import ccxt

    _RETRYABLE = (ccxt.NetworkError, ccxt.DDoSProtection, ccxt.RateLimitExceeded)
except Exception:  # pragma: no cover
    _RETRYABLE = (Exception,)


# --------------------------------------------------------------------------- #
# 저장소
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_rate (
    symbol            TEXT    NOT NULL,
    funding_time      INTEGER NOT NULL,
    rate              REAL    NOT NULL,
    mark_price        REAL,
    next_funding_time INTEGER,
    is_predicted      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, funding_time)
)
"""

# 확정값(is_predicted=0)은 예측값을 덮어써 예측→확정 전환을 반영한다. 반대로 이미
# 확정된 행을 나중에 들어온 예측값이 덮어쓰지 않도록 WHERE 로 가드한다.
# mark_price/next_funding_time 은 새 값이 NULL이면 기존 값을 보존(COALESCE)한다.
_UPSERT = """
INSERT INTO funding_rate
    (symbol, funding_time, rate, mark_price, next_funding_time, is_predicted)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, funding_time) DO UPDATE SET
    rate              = excluded.rate,
    mark_price        = COALESCE(excluded.mark_price, funding_rate.mark_price),
    next_funding_time = COALESCE(excluded.next_funding_time, funding_rate.next_funding_time),
    is_predicted      = excluded.is_predicted
WHERE excluded.is_predicted = 0 OR funding_rate.is_predicted = 1
"""

# `load`가 반환하는 컬럼 순서.
_COLUMNS = [
    "symbol",
    "funding_time",
    "rate",
    "mark_price",
    "next_funding_time",
    "is_predicted",
]


class FundingRateStore:
    """펀딩비를 저장·조회하는 SQLite 래퍼.

    `(symbol, funding_time)`을 기본키로 두고 UPSERT하므로 재수집/중복이 무해하다.
    컨텍스트 매니저로 사용할 수 있다::

        with FundingRateStore("data/ohlcv.db") as store:
            store.upsert_rates(rates)
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: 주기적 최신화 루프가 asyncio.to_thread(직렬 실행)로
        # 워커 스레드에서 저장소를 사용한다. 동시 접근은 없으므로 안전하다.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> FundingRateStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def upsert_rates(self, rates: Iterable[FundingRate]) -> int:
        """펀딩비들을 UPSERT하고 처리한 행 수를 반환한다."""
        rows = [r.as_row() for r in rates]
        if not rows:
            return 0
        with self._conn:  # 트랜잭션 자동 커밋/롤백
            self._conn.executemany(_UPSERT, rows)
        return len(rows)

    def last_funding_time(self, symbol: str, *, confirmed_only: bool = True) -> int | None:
        """해당 심볼의 가장 최근 `funding_time`. 없으면 None.

        `confirmed_only=True`(기본)면 확정값만 대상으로 한다(백필 재시작 기준).
        """
        query = "SELECT MAX(funding_time) FROM funding_rate WHERE symbol = ?"
        if confirmed_only:
            query += " AND is_predicted = 0"
        cur = self._conn.execute(query, (symbol,))
        (value,) = cur.fetchone()
        return int(value) if value is not None else None

    def latest(self, symbol: str) -> FundingRate | None:
        """가장 최근 `funding_time`의 펀딩비(예측 포함). 없으면 None.

        현재 펀딩비/다음 정산 시각을 조회할 때 사용한다.
        """
        cur = self._conn.execute(
            "SELECT " + ", ".join(_COLUMNS) + " FROM funding_rate "
            "WHERE symbol = ? ORDER BY funding_time DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_funding(row)

    def count(self, symbol: str | None = None) -> int:
        """저장된 펀딩비 개수. 심볼로 선택 필터링."""
        if symbol is None:
            cur = self._conn.execute("SELECT COUNT(*) FROM funding_rate")
        else:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM funding_rate WHERE symbol = ?", (symbol,)
            )
        (value,) = cur.fetchone()
        return int(value)

    def get_rates(
        self,
        symbol: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        include_predicted: bool = True,
    ) -> list[FundingRate]:
        """심볼·기간으로 `FundingRate` 목록을 `funding_time` 오름차순 반환한다."""
        query = "SELECT " + ", ".join(_COLUMNS) + " FROM funding_rate WHERE symbol = ?"
        params: list[object] = [symbol]
        if start_ms is not None:
            query += " AND funding_time >= ?"
            params.append(start_ms)
        if end_ms is not None:
            query += " AND funding_time < ?"
            params.append(end_ms)
        if not include_predicted:
            query += " AND is_predicted = 0"
        query += " ORDER BY funding_time ASC"
        cur = self._conn.execute(query, params)
        return [_row_to_funding(row) for row in cur.fetchall()]

    def load(
        self,
        symbol: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        include_predicted: bool = True,
    ) -> pd.DataFrame:
        """심볼·기간(+예측 포함 여부)으로 조회해 `funding_time` 오름차순 DataFrame 반환.

        `funding_datetime` 컬럼(UTC)을 파생해 함께 제공한다. 결과가 없으면 동일한
        컬럼 스키마의 빈 DataFrame을 반환한다.
        """
        query = "SELECT " + ", ".join(_COLUMNS) + " FROM funding_rate WHERE symbol = ?"
        params: list[object] = [symbol]
        if start_ms is not None:
            query += " AND funding_time >= ?"
            params.append(start_ms)
        if end_ms is not None:
            query += " AND funding_time < ?"
            params.append(end_ms)
        if not include_predicted:
            query += " AND is_predicted = 0"
        query += " ORDER BY funding_time ASC"

        df = pd.read_sql_query(query, self._conn, params=params)
        if df.empty:
            df = pd.DataFrame(columns=_COLUMNS)
        df["is_predicted"] = df["is_predicted"].astype(bool)
        df["funding_datetime"] = pd.to_datetime(df["funding_time"], unit="ms", utc=True)
        return df

    def close(self) -> None:
        """연결을 닫는다."""
        self._conn.close()


def _row_to_funding(row: Sequence[Any]) -> FundingRate:
    """`_COLUMNS` 순서의 SQLite 행을 `FundingRate`로 변환한다."""
    symbol, funding_time, rate, mark_price, next_funding_time, is_predicted = row
    return FundingRate(
        symbol=str(symbol),
        funding_time=int(funding_time),
        rate=float(rate),
        mark_price=float(mark_price) if mark_price is not None else None,
        next_funding_time=int(next_funding_time) if next_funding_time is not None else None,
        is_predicted=bool(is_predicted),
    )


# --------------------------------------------------------------------------- #
# 수집 (현재값 / 이력 백필)
# --------------------------------------------------------------------------- #


class FundingRateSource(Protocol):
    """펀딩비 조회 최소 인터페이스 (ccxt 거래소가 만족)."""

    def fetch_funding_rate(
        self,
        symbol: str,
        params: dict[str, Any] = ...,
    ) -> dict[str, Any]: ...

    def fetch_funding_rate_history(
        self,
        symbol: str = ...,
        since: int | None = ...,
        limit: int | None = ...,
        params: dict[str, Any] = ...,
    ) -> list[dict[str, Any]]: ...


def _fetch_current_with_retry(
    exchange: FundingRateSource,
    symbol: str,
    *,
    max_retries: int,
    backoff_base: float,
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    """지수 백오프로 `fetch_funding_rate`를 재시도한다."""
    attempt = 0
    while True:
        try:
            return exchange.fetch_funding_rate(symbol)
        except _RETRYABLE as exc:
            attempt += 1
            if attempt > max_retries:
                logger.error("펀딩 현재값 조회 실패(재시도 소진) %s: %s", symbol, exc)
                raise
            delay = backoff_base * (2 ** (attempt - 1))
            logger.warning(
                "fetch_funding_rate 재시도 %d/%d (%s): %s — %.1fs 대기",
                attempt,
                max_retries,
                symbol,
                exc,
                delay,
            )
            sleeper(delay)


def _fetch_history_with_retry(
    exchange: FundingRateSource,
    symbol: str,
    since: int,
    limit: int,
    *,
    max_retries: int,
    backoff_base: float,
    sleeper: Callable[[float], None],
) -> list[dict[str, Any]]:
    """지수 백오프로 `fetch_funding_rate_history`를 재시도한다."""
    attempt = 0
    while True:
        try:
            return exchange.fetch_funding_rate_history(symbol, since, limit)
        except _RETRYABLE as exc:
            attempt += 1
            if attempt > max_retries:
                logger.error("펀딩 이력 조회 실패(재시도 소진) %s since=%s: %s", symbol, since, exc)
                raise
            delay = backoff_base * (2 ** (attempt - 1))
            logger.warning(
                "fetch_funding_rate_history 재시도 %d/%d (%s): %s — %.1fs 대기",
                attempt,
                max_retries,
                symbol,
                exc,
                delay,
            )
            sleeper(delay)


def fetch_current_funding(
    exchange: FundingRateSource,
    symbol: str,
    *,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> FundingRate:
    """심볼의 현재(예측) 펀딩비 + 다음 정산 시각을 조회한다."""
    data = _fetch_current_with_retry(
        exchange,
        symbol,
        max_retries=max_retries,
        backoff_base=backoff_base,
        sleeper=sleeper,
    )
    return funding_from_ccxt_current(symbol, data)


def refresh_funding(
    exchange: FundingRateSource,
    store: FundingRateStore,
    symbols: Sequence[str],
    *,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, FundingRate]:
    """모든 심볼의 현재 펀딩비를 조회해 저장하고 (심볼→FundingRate) 맵을 반환한다.

    한 심볼이 실패해도 나머지는 계속 갱신한다.
    """
    results: dict[str, FundingRate] = {}
    for symbol in symbols:
        try:
            fr = fetch_current_funding(
                exchange,
                symbol,
                max_retries=max_retries,
                backoff_base=backoff_base,
                sleeper=sleeper,
            )
        except _RETRYABLE as exc:  # pragma: no cover - 개별 심볼 실패 격리
            logger.error("펀딩 최신화 건너뜀 %s: %s", symbol, exc)
            continue
        store.upsert_rates([fr])
        results[symbol] = fr
    return results


def backfill_funding_symbol(
    exchange: FundingRateSource,
    store: FundingRateStore,
    symbol: str,
    since_ms: int,
    *,
    until_ms: int | None = None,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> int:
    """`since_ms`부터 `until_ms`(기본 현재)까지 펀딩 이력을 페이징 백필한다.

    저장한 확정 펀딩비 수를 반환한다. 진행이 없으면(빈 응답/커서 정체) 종료한다.
    """
    end = until_ms if until_ms is not None else now_ms()
    since = since_ms
    total = 0

    while since < end:
        batch = _fetch_history_with_retry(
            exchange,
            symbol,
            since,
            limit,
            max_retries=max_retries,
            backoff_base=backoff_base,
            sleeper=sleeper,
        )
        if not batch:
            break

        rates = [funding_from_ccxt_history(symbol, e) for e in batch if int(e["timestamp"]) < end]
        total += store.upsert_rates(rates)

        last_ts = int(batch[-1]["timestamp"])
        next_since = last_ts + 1
        # 커서가 전진하지 않으면(거래소가 같은 항목 반환) 무한루프 방지.
        if next_since <= since:
            break
        since = next_since

        # 마지막 페이지(가득 차지 않음)면 종료.
        if len(batch) < limit:
            break

    logger.info("펀딩 백필 완료 %s: %d 건", symbol, total)
    return total


def backfill_funding_all(
    exchange: FundingRateSource,
    store: FundingRateStore,
    symbols: Sequence[str],
    *,
    settings: Settings | None = None,
    lookback_days: int | None = None,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> dict[str, int]:
    """모든 심볼의 펀딩 이력을 백필한다.

    각 심볼은 저장된 마지막 확정 펀딩 다음부터(재시작 복구), 없으면 설정 룩백일수만큼
    과거부터 수집한다. (심볼→저장 건수) 맵을 반환한다.
    """
    settings = settings or get_settings()
    if lookback_days is not None:
        lookback = lookback_days
    else:
        lookback = settings.funding_backfill_lookback_days
    results: dict[str, int] = {}

    for symbol in symbols:
        last = store.last_funding_time(symbol, confirmed_only=True)
        since = last + 1 if last is not None else now_ms() - lookback * 86_400_000
        results[symbol] = backfill_funding_symbol(
            exchange,
            store,
            symbol,
            since,
            limit=limit,
            max_retries=max_retries,
            backoff_base=backoff_base,
            sleeper=sleeper,
            now_ms=now_ms,
        )
    return results


# --------------------------------------------------------------------------- #
# 비용 헬퍼
# --------------------------------------------------------------------------- #


def cumulative_funding_cost(
    rates: Iterable[FundingRate],
    *,
    position_notional: float,
    direction: Direction = "long",
    start_ms: int | None = None,
    end_ms: int | None = None,
    include_predicted: bool = False,
) -> float:
    """보유 구간에 정산된 펀딩들의 누적 비용을 계산한다.

    각 정산 비용 = `position_notional * rate * sign`.
    - 롱(long): rate>0면 지불(양수 비용), rate<0면 수취(음수).
    - 숏(short): 부호가 반대.

    `[start_ms, end_ms)` 구간(end 배타적)의 정산만 포함한다. 기본은 확정값만 집계하며
    (`include_predicted=False`), 명목가(`position_notional`)는 구간 내 일정하다고 가정한다.
    """
    sign = 1.0 if direction == "long" else -1.0
    total = 0.0
    for r in rates:
        if start_ms is not None and r.funding_time < start_ms:
            continue
        if end_ms is not None and r.funding_time >= end_ms:
            continue
        if r.is_predicted and not include_predicted:
            continue
        total += position_notional * r.rate * sign
    return total


def funding_cost_for_position(
    store: FundingRateStore,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    position_notional: float,
    direction: Direction = "long",
    include_predicted: bool = False,
) -> float:
    """저장소에서 `[start_ms, end_ms)` 펀딩을 읽어 누적 비용을 계산한다(편의 함수)."""
    rates = store.get_rates(
        symbol,
        start_ms=start_ms,
        end_ms=end_ms,
        include_predicted=include_predicted,
    )
    return cumulative_funding_cost(
        rates,
        position_notional=position_notional,
        direction=direction,
        start_ms=start_ms,
        end_ms=end_ms,
        include_predicted=include_predicted,
    )


# --------------------------------------------------------------------------- #
# 주기적 최신화 루프 / CLI
# --------------------------------------------------------------------------- #


async def _default_async_sleep(delay: float) -> None:  # pragma: no cover - 얇은 래퍼
    await asyncio.sleep(delay)


async def run_funding_refresh(
    settings: Settings | None = None,
    *,
    exchange: FundingRateSource | None = None,
    store: FundingRateStore | None = None,
    backfill: bool = True,
    max_cycles: int | None = None,
    sleeper: Callable[[float], Awaitable[None]] = _default_async_sleep,
) -> None:
    """펀딩비를 백필한 뒤 설정 간격으로 현재 펀딩비를 지속 최신화한다.

    `funding_enabled=False`이면 즉시 반환한다. `max_cycles`를 주면 그만큼만 갱신하고
    종료한다(테스트/일회성). 동기 ccxt 호출은 스레드로 오프로딩해 루프를 막지 않는다.
    """
    settings = settings or get_settings()
    if not settings.funding_enabled:
        logger.info("펀딩 수집 비활성화 (funding_enabled=False)")
        return

    owns_store = store is None
    exchange = exchange if exchange is not None else create_exchange(settings)
    store = store if store is not None else FundingRateStore(settings.db_path)
    interval = float(settings.funding_refresh_interval_seconds)

    try:
        if backfill:
            results = await asyncio.to_thread(
                backfill_funding_all, exchange, store, settings.symbols, settings=settings
            )
            logger.info("펀딩 백필 총 %d 건 저장", sum(results.values()))

        cycles = 0
        while True:
            await asyncio.to_thread(refresh_funding, exchange, store, settings.symbols)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            await sleeper(interval)
    finally:
        if owns_store:
            store.close()


def main() -> None:
    """CLI 엔트리포인트: `python -m data.funding`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_funding_refresh())


if __name__ == "__main__":
    main()
