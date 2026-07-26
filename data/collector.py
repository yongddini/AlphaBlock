"""데이터 수집 오케스트레이션.

재시작 복구 → 과거 백필 → 실시간 스트림 순으로 실행한다. 백필(ccxt 동기 호출)은
스레드로 오프로딩해 이벤트 루프를 막지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import partial

from common.heartbeat import HeartbeatStore
from common.telegram import build_telegram_client
from common.timefmt import kst_log_format, use_kst_logging
from config.settings import Settings, get_settings
from data.backfill import backfill_all
from data.exchange import create_exchange
from data.funding import (
    FundingRateSource,
    FundingRateStore,
    backfill_funding_all,
    run_funding_refresh,
)
from data.repair import RepairStateStore, alert_on_failure, repair_all
from data.storage import OhlcvStore
from data.stream import stream_klines
from data.watchdog import (
    HeartbeatWatchdog,
    ProgressTracker,
    RecoveryEvent,
    force_exit,
    run_with_recovery,
)

logger = logging.getLogger(__name__)


def _build_recovery_notifier(settings: Settings) -> Callable[[RecoveryEvent], None]:
    """복구 이벤트를 로그 + (설정 시) 텔레그램으로 남기는 콜백을 만든다(WAN-173).

    2주 방치 중 수집기가 몇 번 스스로 살아났는지 폰에서도 보이게 한다. WAN-25/32의
    `TelegramClient`를 재사용하며, 미설정이면 조용히 로그만 남긴다(수집기를 죽이지 않는다).
    """
    telegram = build_telegram_client(settings)

    def _notify(event: RecoveryEvent) -> None:
        if telegram is None:
            return
        text = (
            "🔄 *수집기 스트림 복구* (WAN-173)\n"
            f"사유: {event.reason}\n"
            f"재접속 #{event.attempt} · {event.delay_seconds:g}s 후 재접속"
        )
        try:
            telegram.send_message(text)
        except Exception:  # noqa: BLE001 - 알림 실패가 수집기를 죽이면 안 된다
            logger.exception("복구 이벤트 텔레그램 전송 실패(무시하고 계속)")

    return _notify


async def _backfill_funding(settings: Settings, exchange: FundingRateSource) -> None:
    """OHLCV 백필 직후 펀딩 이력을 백필한다(WAN-63).

    이전에는 수집기가 펀딩 수집 경로를 **아예 호출하지 않아** `funding_rate`가 0행이었고,
    백테스트가 경고 없이 펀딩비를 0으로 처리했다. 이제 수집기가 펀딩도 백필한다.
    실패는 조용히 삼키지 않고 크게 로깅해 드러낸다(조용한 실패 → 시끄러운 실패).
    """
    if not settings.funding_enabled:
        logger.warning(
            "펀딩 수집 비활성화(funding_enabled=False) — 백테스트에서 펀딩비가 0으로 처리됩니다"
        )
        return
    try:
        store = FundingRateStore(settings.db_path)
        try:
            results = await asyncio.to_thread(
                backfill_funding_all,
                exchange,
                store,
                settings.symbols,
                settings=settings,
            )
            total = sum(results.values())
            logger.info("펀딩 백필 총 %d 건 저장: %s", total, results)
            if total == 0 and store.count() == 0:
                logger.error(
                    "펀딩 백필 결과가 0행입니다 — funding_rate 테이블이 비어 백테스트 비용이 "
                    "과소 계상됩니다. 거래소 펀딩 조회 경로를 점검하세요."
                )
        finally:
            store.close()
    except Exception:  # noqa: BLE001 - 수집기를 죽이지 않되 실패를 크게 남긴다
        logger.exception("펀딩 백필 실패 — funding_rate가 비어 성과 리포트가 왜곡될 수 있습니다")


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
    WAN-32 텔레그램 경고 경로로 알린다. 이 점검은 **최근 창만** 본다
    (`settings.repair_on_start_lookback_days`, 기본 7일 · 0이면 전 구간) — 6년 DB에서
    전 구간 스캔은 스트림 접속을 ~40초 늦춘다(WAN-187).
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
            # 시작 점검은 최근 창만 본다 (WAN-187) — 6년 DB 전 구간 스캔(~40초)이
            # 스트림 접속을 늦추기 때문이다. 창 밖 갭은 `alphablock backfill --repair`
            # 소관이고, 「어디까지 봤는지」는 요약(`RepairSummary.lookback_ms`)에 남는다.
            lookback_days = settings.repair_on_start_lookback_days
            lookback_ms = lookback_days * 86_400_000 if lookback_days else None
            logger.info(
                "갭 자동 복구 점검 시작: %s",
                f"최근 {lookback_days}일" if lookback_ms else "전 구간",
            )
            summary = await asyncio.to_thread(
                partial(repair_all, exchange, store, lookback_ms=lookback_ms)
            )
            RepairStateStore(settings.repair_state_path).save(summary)
            logger.info(
                "갭 자동 복구: %d 시리즈에서 %d봉 채움%s",
                len(summary.repaired_series),
                summary.total_filled,
                f", {summary.total_remaining}봉 잔여" if summary.total_remaining else "",
            )
            alert_on_failure(summary, settings)

        # 펀딩 이력 백필(WAN-63). 스트림 접속 전에 1회 수행해 백테스트에 필요한 전체
        # 구간 펀딩을 채운다. 실패해도 수집기는 계속 살아 있게 하되 크게 로깅한다.
        await _backfill_funding(settings, exchange)

        heartbeat.beat()  # 백필 완료 = 첫 하트비트(스트림 접속 전에도 생존 표시).

        # 프로세스 레벨 워치독의 진행 시각. 메시지 수신마다 mark 하고, 감시 스레드가
        # 이걸 폴링해 이벤트 루프가 통째로 멎으면 프로세스를 종료한다(WAN-173 (2)).
        tracker = ProgressTracker()

        def _beat() -> None:  # stream_klines 는 None 반환 콜백을 기대한다.
            heartbeat.beat()
            tracker.mark()

        if run_stream:
            # 실시간 스트림과 함께 펀딩 현재값을 주기적으로 최신화한다(백필은 위에서
            # 이미 했으므로 backfill=False). 펀딩 루프가 죽어도 스트림은 유지된다.
            funding_task: asyncio.Task[None] | None = None
            if settings.funding_enabled:
                funding_task = asyncio.create_task(
                    run_funding_refresh(settings, exchange=exchange, backfill=False)
                )

            # 프로세스 레벨 워치독 스레드(WAN-173 (2)). in-process 재접속(아래 유휴
            # 워치독)조차 못 돌 만큼 이벤트 루프가 멎으면 hang→exit로 바꿔 서비스
            # 매니저가 재시작하게 한다. 진행 시각은 백필 완료 직후로 초기화한다.
            watchdog: HeartbeatWatchdog | None = None
            if settings.collector_watchdog_enabled:
                tracker.mark()
                watchdog = HeartbeatWatchdog(
                    tracker,
                    timeout_seconds=settings.collector_watchdog_timeout_seconds,
                    poll_seconds=settings.collector_watchdog_poll_seconds,
                    on_stale=force_exit(),
                )
                watchdog.start()

            async def _stream_once() -> None:
                # 한 번의 접속·소비. 유휴 타임아웃을 걸어(WAN-173 (1)) half-open stall을
                # StreamStalled 예외로 바꾼다 — run_with_recovery가 잡아 재접속한다.
                await stream_klines(
                    store,
                    settings.symbols,
                    settings.timeframes,
                    heartbeat=_beat,
                    idle_timeout_seconds=settings.collector_stream_idle_timeout_seconds,
                )

            try:
                await run_with_recovery(
                    _stream_once,
                    on_recover=_build_recovery_notifier(settings),
                    max_backoff_seconds=settings.collector_reconnect_max_backoff_seconds,
                )
            finally:
                if funding_task is not None:
                    funding_task.cancel()
                if watchdog is not None:
                    watchdog.stop()
    finally:
        store.close()


def main() -> None:
    """CLI 엔트리포인트: `python -m data.collector`."""
    use_kst_logging()  # 로그 시각도 KST(WAN-172)
    logging.basicConfig(level=logging.INFO, format=kst_log_format())
    asyncio.run(run_collector())


if __name__ == "__main__":
    main()
