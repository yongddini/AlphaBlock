"""데이터 수집 오케스트레이션.

재시작 복구 → 과거 백필 → 실시간 스트림 순으로 실행한다. 백필(ccxt 동기 호출)은
스레드로 오프로딩해 이벤트 루프를 막지 않는다.
"""

from __future__ import annotations

import asyncio
import logging

from config.settings import Settings, get_settings
from data.backfill import backfill_all
from data.exchange import create_exchange
from data.storage import OhlcvStore
from data.stream import stream_klines

logger = logging.getLogger(__name__)


async def run_collector(
    settings: Settings | None = None,
    *,
    run_stream: bool = True,
) -> None:
    """백필 후 실시간 스트림을 시작한다.

    `run_stream=False`이면 백필까지만 수행하고 반환한다(일회성 수집/테스트용).
    """
    settings = settings or get_settings()
    exchange = create_exchange(settings)
    store = OhlcvStore(settings.db_path)
    try:
        logger.info(
            "백필 시작: %d 심볼 × %d 타임프레임 → %s",
            len(settings.symbols),
            len(settings.timeframes),
            settings.db_path,
        )
        results = await asyncio.to_thread(
            backfill_all,
            exchange,
            store,
            settings.symbols,
            settings.timeframes,
            settings=settings,
        )
        logger.info("백필 총 %d 봉 저장", sum(results.values()))

        if run_stream:
            await stream_klines(store, settings.symbols, settings.timeframes)
    finally:
        store.close()


def main() -> None:
    """CLI 엔트리포인트: `python -m data.collector`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_collector())


if __name__ == "__main__":
    main()
