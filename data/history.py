"""과거 구간(window) 1분봉 등 대량 백필 실행 (WAN-44).

`data.collector`의 `backfill_all`은 저장된 마지막 봉 *다음*부터만 채우므로(순방향
재시작 복구), 이미 소량이 저장된 시리즈의 **과거 방향**을 6개월/3년으로 넓게
채우지 못한다. 이 모듈은 명시한 `days`만큼의 창 `[now-days, now)`를 심볼·TF별로
페이징 백필한다. 저장소가 `(symbol, timeframe, open_time)` UPSERT라 겹치는 구간을
다시 받아도 무해하며(멱등), 중단 후 재실행하면 이미 채운 구간은 그대로 두고
빠진 구간만 다시 받는다.

WAN-41(1분봉 서브스텝 백테스트)의 선행 데이터 공급이 목적이다. 네트워크 의존부는
`data.backfill`의 `FetchOHLCV` 프로토콜로 추상화돼 있어 테스트에서 가짜 거래소를
주입할 수 있다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from config.settings import Settings, get_settings
from data.backfill import DEFAULT_LIMIT, FetchOHLCV, backfill_symbol
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class SeriesBackfillResult:
    """한 (심볼, TF) 백필의 실측 결과."""

    symbol: str
    timeframe: str
    since_ms: int
    """백필 창의 시작 open_time(요청값)."""
    bars_written: int
    """이번 실행에서 UPSERT한 봉 수(겹침 포함, 반드시 신규는 아님)."""
    stored_after: int
    """실행 후 저장소에 존재하는 이 시리즈의 총 봉 수."""
    first_open_ms: int | None
    """실행 후 저장소에 존재하는 이 시리즈의 가장 오래된 open_time(없으면 None)."""
    elapsed_s: float
    """이 시리즈 백필 소요 시간(초)."""

    def reached_requested_start(self, *, tolerance_bars: int = 1) -> bool:
        """저장된 가장 오래된 봉이 요청한 창 시작(`since_ms`)에 도달했는지.

        BTC 1h가 4개월에서 멈춘 사고(WAN-51)의 재발 방지용 완료 확인. 백필이 조용히
        덜 돌면(레이트리밋·에러·중도 종료·거래소 상장일 이후) 창 시작에 못 미친다.
        `tolerance_bars`는 창 시작 이전 상장 등 정상적인 근소 미달을 흡수한다.
        """
        if self.first_open_ms is None:
            return False
        tf_ms = timeframe_to_ms(self.timeframe)
        return self.first_open_ms <= self.since_ms + tolerance_bars * tf_ms


def _log_progress(
    symbol: str,
    timeframe: str,
    *,
    every: int,
) -> Callable[[int, int, int], None]:
    """`every`봉마다 진행률을 로그하는 `backfill_symbol` progress 콜백을 만든다."""
    state = {"last_logged": 0}

    def _cb(total: int, last_open_ms: int, end_ms: int) -> None:
        if total - state["last_logged"] < every:
            return
        state["last_logged"] = total
        # 남은 구간을 대략적인 % 로 표시(창 시작을 모르므로 last_open 기준 근사).
        logger.info(
            "백필 진행 %s %s: %d봉 (커서 %s)",
            symbol,
            timeframe,
            total,
            time.strftime("%Y-%m-%d %H:%M", time.gmtime(last_open_ms / 1000)),
        )

    return _cb


def run_history_backfill(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    days: int,
    limit: int = DEFAULT_LIMIT,
    max_retries: int = 5,
    backoff_base: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    monotonic: Callable[[], float] = time.monotonic,
    progress_every: int = 20_000,
) -> list[SeriesBackfillResult]:
    """`[now-days, now)` 창을 심볼×TF별로 백필한다.

    `backfill_all`(순방향 재시작)과 달리 저장된 마지막 봉과 무관하게 **창 시작부터**
    받으므로 과거 방향을 넓게 메운다. UPSERT 멱등성 덕분에 겹치는 구간은 무해하게
    덮어써진다. 각 시리즈의 실측(쓴 봉 수·저장 총량·소요시간)을 리스트로 반환한다.
    """
    if days <= 0:
        raise ValueError(f"days는 양수여야 합니다: {days}")

    end = now_ms()
    since = end - days * _DAY_MS
    results: list[SeriesBackfillResult] = []

    for symbol in symbols:
        for timeframe in timeframes:
            # 창 시작을 TF 격자에 정렬(정합성·중복 방지).
            tf_ms = timeframe_to_ms(timeframe)
            aligned_since = (since // tf_ms) * tf_ms
            logger.info(
                "백필 시작 %s %s: %s부터 (%d일 창)",
                symbol,
                timeframe,
                time.strftime("%Y-%m-%d", time.gmtime(aligned_since / 1000)),
                days,
            )
            started = monotonic()
            written = backfill_symbol(
                exchange,
                store,
                symbol,
                timeframe,
                aligned_since,
                until_ms=end,
                limit=limit,
                max_retries=max_retries,
                backoff_base=backoff_base,
                sleeper=sleeper,
                now_ms=now_ms,
                progress=_log_progress(symbol, timeframe, every=progress_every),
            )
            elapsed = monotonic() - started
            stored = store.count(symbol, timeframe)
            first_open = store.first_open_time(symbol, timeframe)
            logger.info(
                "백필 완료 %s %s: %d봉 처리, 저장 총 %d봉, %.1fs",
                symbol,
                timeframe,
                written,
                stored,
                elapsed,
            )
            result = SeriesBackfillResult(
                symbol=symbol,
                timeframe=timeframe,
                since_ms=aligned_since,
                bars_written=written,
                stored_after=stored,
                first_open_ms=first_open,
                elapsed_s=elapsed,
            )
            # 완료 확인: 창 시작에 도달하지 못하면 경고(WAN-51 재발 방지).
            if not result.reached_requested_start():
                logger.warning(
                    "백필 미완 %s %s: 창 시작 %s 요청했으나 최오래 저장봉은 %s "
                    "— 레이트리밋·에러·중도 종료 또는 상장일 이후 여부 확인 필요",
                    symbol,
                    timeframe,
                    time.strftime("%Y-%m-%d", time.gmtime(aligned_since / 1000)),
                    time.strftime("%Y-%m-%d", time.gmtime(first_open / 1000))
                    if first_open is not None
                    else "없음",
                )
            results.append(result)

    return results


def run_history_backfill_with_settings(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    days: int,
    settings: Settings | None = None,
) -> list[SeriesBackfillResult]:
    """설정으로 거래소·저장소를 구성해 `run_history_backfill`을 실행하는 얇은 래퍼."""
    from data.exchange import create_exchange

    settings = settings or get_settings()
    exchange = create_exchange(settings)
    store = OhlcvStore(settings.db_path)
    try:
        return run_history_backfill(exchange, store, symbols, timeframes, days=days)
    finally:
        store.close()
