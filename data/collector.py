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
from data.repair import RepairStateStore, alert_on_failure, repair_all
from data.storage import OhlcvStore
from data.stream import stream_klines
from live.heartbeat import HeartbeatStore

logger = logging.getLogger(__name__)


async def run_collector(
    settings: Settings | None = None,
    *,
    run_stream: bool = True,
    repair_on_start: bool | None = None,
) -> None:
    """백필 후 실시간 스트림을 시작한다.

    `run_stream=False`이면 백필까지만 수행하고 반환한다(일회성 수집/테스트용).
    스트림 구동 중에는 수신 메시지마다 하트비트를 남겨 Health 대시보드(WAN-30/31)가
    수집기 생존을 확인할 수 있게 한다.

    `repair_on_start`(None이면 설정값 `settings.repair_on_start`)가 참이면, 백필
    직후 저장된 시리즈의 내부 갭을 1회 자동 복구한다(WAN-35). 복구 중 오류가 나면
    WAN-32 텔레그램 경고 경로로 알린다.
    """
    settings = settings or get_settings()
    do_repair = settings.repair_on_start if repair_on_start is None else repair_on_start
    exchange = create_exchange(settings)
    store = OhlcvStore(settings.db_path)
    heartbeat = HeartbeatStore(
        settings.collector_heartbeat_path,
        label="collector",
        min_interval_ms=settings.collector_heartbeat_min_interval_seconds * 1000,
    )
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

        if do_repair:
            summary = await asyncio.to_thread(repair_all, exchange, store)
            RepairStateStore(settings.repair_state_path).save(summary)
            logger.info(
                "갭 자동 복구: %d 시리즈에서 %d봉 채움%s",
                len(summary.repaired_series),
                summary.total_filled,
                f", {summary.total_remaining}봉 잔여" if summary.total_remaining else "",
            )
            alert_on_failure(summary, settings)

        heartbeat.beat()  # 백필 완료 = 첫 하트비트(스트림 접속 전에도 생존 표시).

        def _beat() -> None:  # stream_klines 는 None 반환 콜백을 기대한다.
            heartbeat.beat()

        if run_stream:
            await stream_klines(
                store,
                settings.symbols,
                settings.timeframes,
                heartbeat=_beat,
            )
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
