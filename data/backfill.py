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
    progress: Callable[[int, int, int], None] | None = None,
) -> int:
    """`since_ms`부터 `until_ms`(기본 현재)까지 페이징 백필한다.

    저장한 봉 수를 반환한다. 진행이 없으면(빈 응답/커서 정체) 루프를 종료한다.

    `progress`가 주어지면 매 페이지 저장 후 `(total, last_open_ms, end)`로 호출된다.
    장시간(수십만 봉) 실행의 진행률 로깅에 쓴다(테스트에서 주입 가능).

    ⚠️ **형성 중 봉은 저장하지 않는다(WAN-314).** ccxt `fetch_ohlcv`는 아직 닫히지
    않은 현재 봉도 돌려주는데, 이 경로는 모든 행을 `closed=True`로 저장한다
    (`candle_from_ccxt` 기본값). 스트림이 살아 있으면 봉 확정 이벤트가 덮어써 자가
    치유되지만, 확정 순간 스트림이 죽어 있으면 **부분 봉이 확정 라벨을 달고 영구히
    남는다** — 2026-08-16 사고에서 15m 22:45 봉이 그렇게 남았다(구멍은 보이는데
    부분 봉은 어떤 점검에도 안 보인다). 그래서 `open_time + tf <= now`인, 이미 닫힌
    봉만 저장한다. 닫힌 봉만 저장하면 형성 중 봉의 몫은 스트림(확정 이벤트) 또는
    다음 꼬리 백필이 정확한 값으로 채운다.
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

        # 종료 시점 이후의 봉과 아직 닫히지 않은 봉은 제외(미래/미확정 봉 방지, WAN-314).
        closed_by = now_ms()
        rows: Sequence[list[float]] = [
            r for r in batch if int(r[0]) < end and int(r[0]) + tf_ms <= closed_by
        ]
        candles: list[Candle] = [candle_from_ccxt(symbol, timeframe, r) for r in rows]
        total += store.upsert_candles(candles)

        last_open = int(batch[-1][0])
        if progress is not None:
            progress(total, last_open, end)
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
