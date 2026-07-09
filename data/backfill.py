"""과거 OHLCV 백필.

ccxt `fetch_ohlcv`를 페이징 루프로 호출해 과거 봉을 SQLite에 저장한다.
429/네트워크 오류는 지수 백오프로 재시도한다. 네트워크 의존부는 `FetchOHLCV`
프로토콜로 추상화해 테스트에서 가짜 구현을 주입할 수 있다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from config.settings import Settings, get_settings
from data.models import Candle, candle_from_ccxt, timeframe_to_ms
from data.storage import OhlcvStore

logger = logging.getLogger(__name__)

# ccxt fetch_ohlcv 페이지 최대 크기(바이낸스).
DEFAULT_LIMIT = 1000

# 재시도 대상 예외. ccxt는 네트워크/레이트리밋 오류를 이 계층으로 던진다.
_RETRYABLE: tuple[type[Exception], ...]
try:  # pragma: no cover - import 형태만 분기
    import ccxt

    _RETRYABLE = (ccxt.NetworkError, ccxt.DDoSProtection, ccxt.RateLimitExceeded)
except Exception:  # pragma: no cover
    _RETRYABLE = (Exception,)


class FetchOHLCV(Protocol):
    """`fetch_ohlcv`를 제공하는 최소 인터페이스 (ccxt 거래소가 만족)."""

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = ...,
        since: int | None = ...,
        limit: int | None = ...,
        params: dict[str, object] = ...,
    ) -> list[list[float]]: ...


def _fetch_with_retry(
    exchange: FetchOHLCV,
    symbol: str,
    timeframe: str,
    since: int,
    limit: int,
    *,
    max_retries: int,
    backoff_base: float,
    sleeper: Callable[[float], None],
) -> list[list[float]]:
    """지수 백오프로 `fetch_ohlcv`를 재시도한다."""
    attempt = 0
    while True:
        try:
            return exchange.fetch_ohlcv(symbol, timeframe, since, limit)
        except _RETRYABLE as exc:
            attempt += 1
            if attempt > max_retries:
                logger.error(
                    "백필 실패(재시도 소진) %s %s since=%s: %s",
                    symbol,
                    timeframe,
                    since,
                    exc,
                )
                raise
            delay = backoff_base * (2 ** (attempt - 1))
            logger.warning(
                "fetch_ohlcv 재시도 %d/%d (%s %s): %s — %.1fs 대기",
                attempt,
                max_retries,
                symbol,
                timeframe,
                exc,
                delay,
            )
            sleeper(delay)


def backfill_symbol(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    symbol: str,
    timeframe: str,
    since_ms: int,
    *,
    until_ms: int | None = None,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> int:
    """`since_ms`부터 `until_ms`(기본 현재)까지 페이징 백필한다.

    저장한 봉 수를 반환한다. 진행이 없으면(빈 응답/커서 정체) 루프를 종료한다.
    """
    tf_ms = timeframe_to_ms(timeframe)
    end = until_ms if until_ms is not None else now_ms()
    since = since_ms
    total = 0

    while since < end:
        batch = _fetch_with_retry(
            exchange,
            symbol,
            timeframe,
            since,
            limit,
            max_retries=max_retries,
            backoff_base=backoff_base,
            sleeper=sleeper,
        )
        if not batch:
            break

        # 종료 시점 이후의 봉은 제외(미래/미확정 봉 방지).
        rows: Sequence[list[float]] = [r for r in batch if int(r[0]) < end]
        candles: list[Candle] = [candle_from_ccxt(symbol, timeframe, r) for r in rows]
        total += store.upsert_candles(candles)

        last_open = int(batch[-1][0])
        next_since = last_open + tf_ms
        # 커서가 전진하지 않으면(거래소가 같은 봉 반환) 무한루프 방지.
        if next_since <= since:
            break
        since = next_since

        # 마지막 페이지(가득 차지 않음)면 종료.
        if len(batch) < limit:
            break

    logger.info("백필 완료 %s %s: %d 봉", symbol, timeframe, total)
    return total


def backfill_all(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    settings: Settings | None = None,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> dict[tuple[str, str], int]:
    """모든 심볼×타임프레임을 백필한다.

    각 (심볼, 타임프레임)에 대해 저장된 마지막 봉 다음부터(재시작 복구), 없으면
    설정 룩백일수만큼 과거부터 수집한다. (심볼, 타임프레임)→저장 봉수 맵을 반환한다.
    """
    settings = settings or get_settings()
    results: dict[tuple[str, str], int] = {}

    for symbol in symbols:
        for timeframe in timeframes:
            tf_ms = timeframe_to_ms(timeframe)
            last = store.last_open_time(symbol, timeframe)
            if last is not None:
                since = last + tf_ms
            else:
                lookback_days = settings.lookback_days_for(timeframe)
                since = now_ms() - lookback_days * 86_400_000
            count = backfill_symbol(
                exchange,
                store,
                symbol,
                timeframe,
                since,
                limit=limit,
                max_retries=max_retries,
                backoff_base=backoff_base,
                sleeper=sleeper,
                now_ms=now_ms,
            )
            results[(symbol, timeframe)] = count

    return results
