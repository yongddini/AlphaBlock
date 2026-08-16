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
from data.backfill import FetchOHLCV, backfill_all
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


class TailCatchup:
    """웹소켓 접속 직후 꼬리 따라잡기 백필 (WAN-314).

    스트림은 **접속 이후에 닫힌 봉만** 준다(`k.x` 확정 이벤트). 그래서 기동 백필이
    끝나고 스트림이 붙기까지의 구간(신규 심볼 초기 백필이 길면 수십 분 — 2026-08-16
    사고에서 9종목 15m 23:00 KST 봉이 이렇게 빠졌다)과 재접속 공백에 닫힌 봉은
    **아무도 다시 요청하지 않아 영구 구멍**이 됐다. 기동 갭 복구(`repair_on_start`)도
    못 잡는다 — 그 시점엔 아직 꼬리라 내부 갭이 아니기 때문이다(`data.gaps` 경계 처리).

    이 클래스는 `stream_klines`의 `on_connect` 훅에서 매 (재)접속마다 꼬리 백필을
    **백그라운드 태스크로** 예약한다. 접속 후에 돌므로 「접속 이후 확정 = 스트림」과
    「따라잡기 호출 이전 확정 = REST」의 합집합이 전 구간을 덮고, 저장소는 락으로
    직렬화돼(`OhlcvStore`, 백필 스레드 + 스트림 루프) 동시 접근이 안전하다. 밀린 봉이
    없으면 시리즈당 요청 1건(빈 손)으로 끝난다. 실패해도 스트림은 죽이지 않는다.
    """

    def __init__(
        self,
        exchange: FetchOHLCV,
        store: OhlcvStore,
        settings: Settings,
    ) -> None:
        self._exchange = exchange
        self._store = store
        self._settings = settings
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self) -> None:
        """따라잡기 태스크를 예약한다(이전 실행이 아직 돌고 있으면 건너뜀).

        `stream_klines(on_connect=...)`에서 호출되므로 이벤트 루프 안이다. 빠른 재접속
        루프에서 태스크가 겹겹이 쌓이지 않게 미완료 태스크가 있으면 예약하지 않는다
        (UPSERT라 겹쳐도 무해하지만 낭비다).
        """
        if any(not t.done() for t in self._tasks):
            logger.info("꼬리 따라잡기가 이미 진행 중 — 이번 접속에서는 예약 생략")
            return
        task = asyncio.get_running_loop().create_task(self._run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self) -> None:
        try:
            results = await asyncio.to_thread(
                backfill_all,
                self._exchange,
                self._store,
                self._settings.symbols,
                self._settings.timeframes,
                settings=self._settings,
            )
            filled = sum(results.values())
            if filled:
                logger.info(
                    "접속 직후 꼬리 따라잡기: %d봉 채움(스트림이 주지 않는 접속 전 확정봉)",
                    filled,
                )
        except Exception:  # noqa: BLE001 — 따라잡기 실패가 스트림을 죽이면 안 된다.
            logger.exception("꼬리 따라잡기 백필 실패 — 다음 (재)접속에서 다시 시도합니다")


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

    # 환경변수가 채택 좌표를 덮어쓰고 있으면 기동 시점에 보이게 한다(WAN-309).
    # 값은 존중한다 — 낡은 `.env`로 9종목만 수집하면서 아무도 모르는 것이 사고다.
    from config.drift import check_coordinate_drift, render_drift_lines

    for line in render_drift_lines(check_coordinate_drift(settings)):
        logger.warning(line)

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

            # 접속 직후 꼬리 따라잡기(WAN-314) — 기동 백필~접속 사이·재접속 공백에
            # 닫힌 봉은 스트림이 다시 주지 않으므로 매 접속마다 REST로 메운다.
            catchup = TailCatchup(exchange, store, settings)

            async def _stream_once() -> None:
                # 한 번의 접속·소비. 유휴 타임아웃을 걸어(WAN-173 (1)) half-open stall을
                # StreamStalled 예외로 바꾼다 — run_with_recovery가 잡아 재접속한다.
                await stream_klines(
                    store,
                    settings.symbols,
                    settings.timeframes,
                    heartbeat=_beat,
                    idle_timeout_seconds=settings.collector_stream_idle_timeout_seconds,
                    on_connect=catchup.schedule,
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
