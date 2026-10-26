"""데이터 수집 오케스트레이션.

재시작 복구 → 과거 백필 → 실시간 스트림 순으로 실행한다. 백필(ccxt 동기 호출)은
스레드로 오프로딩해 이벤트 루프를 막지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from functools import partial

from common.heartbeat import HeartbeatStore
from common.telegram import build_telegram_client
from common.timefmt import kst_log_format, use_kst_logging
from config.settings import Settings, get_settings
from data.backfill import DEFAULT_LIMIT, FetchOHLCV, backfill_all
from data.collection_plan import (
    ArmCollection,
    CollectionPlan,
    backfill_pairs,
    build_plan,
    current_arm_pairs,
    monitor_scope_for,
)
from data.derived_bars import build_derived
from data.exchange import create_exchange
from data.funding import (
    FundingRateSource,
    FundingRateStore,
    backfill_funding_all,
    run_funding_refresh,
)
from data.models import Candle, timeframe_to_ms
from data.repair import RefillStale, RepairStateStore, alert_on_failure, repair_all
from data.rest_poll import ClosedBarPoller, report_poller_task_exit
from data.storage import OhlcvStore
from data.stream import stream_kline_pairs, stream_klines
from data.universe import ARM_STREAM_TIMEFRAMES, BUILT_TIMEFRAMES
from data.universe_pool import UniverseStore
from data.watchdog import (
    HeartbeatWatchdog,
    ProgressTracker,
    RecoveryEvent,
    force_exit,
    install_task_dump_signal,
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
        # 평문 — 사유에 종목·테이블 이름이 섞이면 레거시 Markdown이 깨진다(WAN-321).
        text = (
            "🔄 수집기 스트림 복구 (WAN-173/461)\n"
            f"{event.reason} → 재접속\n"
            f"연속 재접속 #{event.attempt} · {event.delay_seconds:g}s 후"
        )
        try:
            telegram.send_message(text, parse_mode=None)
        except Exception:  # noqa: BLE001 - 알림 실패가 수집기를 죽이면 안 된다
            logger.exception("복구 이벤트 텔레그램 전송 실패(무시하고 계속)")

    return _notify


#: 시리즈 → 접속 순간의 마지막 저장 봉 `open_time`(없으면 None) — WAN-456 스냅샷.
LastOpenSnapshot = dict[tuple[str, str], int | None]


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

    🚨 **시작점은 「접속 순간」의 마지막 저장 봉이다(WAN-456 §d).** 예전에는 시리즈를
    **처리하는 순간** 저장소를 읽었는데, 팔 쪽 따라잡기는 쌍 사이에 간격을 둬 몇 분이
    걸리므로 그 사이 스트림이 다음 봉을 넣어 버리면 그 앞의 구멍을 건너뛰었다 —
    2026-10-17 09:03 재접속에서 팔 24종목의 09:02 1분봉이 그렇게 영구히 빠졌다(기본 격자
    9종목은 먼저 처리돼 무사했다). 그래서 `schedule()`이 **소비가 시작되기 전에** 시리즈마다
    마지막 저장 봉을 찍어 두고 그 값에서 받는다.

    따라잡기가 아직 도는 중에 새 접속이 오면 건너뛰지 않고 그 스냅샷을 **보류**했다가 지금
    실행이 끝나면 한 번 더 돈다(보류가 여러 번 겹치면 시리즈마다 더 이른 값으로 합쳐 한 번만).
    건너뛰면 이미 처리를 마친 시리즈의 새 공백을 아무도 다시 보지 않는다.
    """

    def __init__(
        self,
        exchange: FetchOHLCV,
        store: OhlcvStore,
        settings: Settings,
        *,
        extra: Callable[[Mapping[tuple[str, str], int | None]], int] | None = None,
        extra_pairs: Callable[[], Sequence[tuple[str, str]]] | None = None,
    ) -> None:
        self._exchange = exchange
        self._store = store
        self._settings = settings
        #: 기본 격자 뒤에 이어 도는 따라잡기(WAN-451 ③ — 팔 종목 쌍 · 3h 생성). 스냅샷을 받는다.
        #: None이면 예전 그대로.
        self._extra = extra
        #: 스냅샷에 넣을 팔 쪽 쌍(WAN-456) — `extra`가 받을 시리즈.
        self._extra_pairs = extra_pairs
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending: LastOpenSnapshot | None = None
        #: 끝까지 성공한 따라잡기 횟수 — 0이면 아직 기동 따라잡기 전이다(WAN-474).
        self._completed = 0

    def startup_pending(self) -> bool:
        """기동 뒤 첫 따라잡기가 아직 안 끝났나 — REST 폴러가 긴 공백을 건드리지 않게(WAN-474).

        첫 접속 전이면 스냅샷조차 안 찍혔다. 그때 폴러가 긴 공백 시리즈를 「최근 N봉만」 받으면
        마지막 봉이 앞으로 뛰어, 뒤에 찍힐 스냅샷이 그 앞을 영구 구멍으로 남긴다.
        """
        return self._completed == 0

    def snapshot(self) -> LastOpenSnapshot:
        """지금 저장소의 시리즈별 마지막 봉(기본 격자 + 팔 쪽 쌍)."""
        series = [(s, tf) for s in self._settings.symbols for tf in self._settings.timeframes]
        if self._extra_pairs is not None:
            series += list(self._extra_pairs())
        return {pair: self._store.last_open_time(*pair) for pair in dict.fromkeys(series)}

    def schedule(self) -> None:
        """따라잡기 태스크를 예약한다(이전 실행이 아직 돌고 있으면 보류했다가 이어서).

        `stream_klines(on_connect=...)`에서 호출되므로 이벤트 루프 안이고, **소비가 시작되기
        전**이다 — 여기서 스냅샷을 찍는 것이 핵심이다(WAN-456 §d).
        """
        snapshot = self.snapshot()
        if any(not t.done() for t in self._tasks):
            self._pending = _merge_snapshots(self._pending, snapshot)
            logger.info("꼬리 따라잡기가 이미 진행 중 — 이번 접속분은 끝난 뒤 이어서 돈다")
            return
        task = asyncio.get_running_loop().create_task(self._run(snapshot))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, snapshot: LastOpenSnapshot) -> None:
        current: LastOpenSnapshot | None = snapshot
        while current is not None:
            if await self._run_once(current):
                self._completed += 1
            current, self._pending = self._pending, None

    async def _run_once(self, snapshot: LastOpenSnapshot) -> bool:
        """한 번 따라잡는다 — 끝까지 돌았으면 True(실패는 로그만 남기고 False)."""
        try:
            results = await asyncio.to_thread(
                backfill_all,
                self._exchange,
                self._store,
                self._settings.symbols,
                self._settings.timeframes,
                settings=self._settings,
                last_open_times=snapshot,
            )
            filled = sum(results.values())
            if self._extra is not None:
                filled += await asyncio.to_thread(self._extra, snapshot)
            if filled:
                logger.info(
                    "접속 직후 꼬리 따라잡기: %d봉 채움(스트림이 주지 않는 접속 전 확정봉)",
                    filled,
                )
        except Exception:  # noqa: BLE001 — 따라잡기 실패가 스트림을 죽이면 안 된다.
            logger.exception("꼬리 따라잡기 백필 실패 — 다음 (재)접속에서 다시 시도합니다")
            return False
        return True


def _merge_snapshots(older: LastOpenSnapshot | None, newer: LastOpenSnapshot) -> LastOpenSnapshot:
    """시리즈마다 더 이른 값을 남긴다(None = 저장분 없음 = 가장 이름)."""
    if older is None:
        return dict(newer)
    merged = dict(older)
    for pair, last in newer.items():
        if pair not in merged:
            merged[pair] = last
            continue
        prev = merged[pair]
        merged[pair] = None if prev is None or last is None else min(prev, last)
    return merged


def _arm_catch_up(arm: ArmCollection, snapshot: Mapping[tuple[str, str], int | None]) -> int:
    """`TailCatchup`의 팔 쪽 이어 돌기 — 접속 순간 스냅샷에서 시작한다(WAN-456)."""
    return arm.catch_up(last_open_times=snapshot)


def _arm_tail_refill(
    settings: Settings,
    exchange: FetchOHLCV,
    store: OhlcvStore,
    *,
    progress: Callable[[], None] | None = None,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    sleeper: Callable[[float], None] = time.sleep,
    max_bars: int = DEFAULT_LIMIT,
) -> RefillStale:
    """시작 갭 복구의 `refill_stale` — 정지로 보이는 **팔 쪽** 시리즈의 **짧은** 꼬리만 받는다.

    팔 종목 꼬리는 접속 뒤 따라잡기(`TailCatchup`)가 채우는데, 시작 갭 복구는 그보다 먼저
    신선도를 잰다. 그대로 재면 수집기가 꺼져 있던 몇 분이 「🚨 수집 정지」로 찍힌다(2026-10-23 ·
    1m 21건 · WAN-472). 그래서 판정 직전에 **정지로 보이는 쌍만** 받는다(보통 1m 수십 개). 시작
    갭 복구는 이 함수를 **쌍마다 한 번씩** 불러 받자마자 판정한다.

    🚨 **꼬리가 한 요청(`max_bars`봉)보다 길면 받지 않고 돌려준다(WAN-474) — 「채우는 중」.**
    새로 들어온 종목의 이력이나 재시작에 끊긴 채우기는 수십만 봉이다(2026-10-26 AGT 1m
    742,949봉 · 3분 25초). 그걸 여기서 받으면 웹소켓 접속이 그만큼 늦고 기본 격자까지 끊긴다.
    긴 꼬리는 접속 뒤 따라잡기가 같은 데이터를 백그라운드로 받는다.

    기본 격자 쌍은 받지도 돌려주지도 않는다 — 방금 기동 백필이 채웠으니 그래도 정지면 진짜
    정지다. 그 주 목록을 못 읽으면 받을 쌍이 없고 판정은 옛 동작 그대로다.
    """

    arm_pairs: set[tuple[str, str]] | None = None
    calls = 0

    def refill(pairs: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
        # 시작 갭 복구가 쌍마다 한 번씩 부른다(받자마자 판정하려고) — 팔 목록은 한 번만 읽고,
        # 거래소 요청 사이에는 팔 따라잡기와 같은 간격을 둔다.
        nonlocal arm_pairs, calls
        if arm_pairs is None:
            arm_pairs = set(
                current_arm_pairs(
                    settings.db_path,
                    base_symbols=settings.symbols,
                    base_timeframes=settings.timeframes,
                    now_ms=now_ms(),
                )
            )
        now = now_ms()
        short: list[tuple[str, str]] = []
        long: list[tuple[str, str]] = []
        for pair in pairs:
            if pair not in arm_pairs:
                continue
            last = store.last_open_time(*pair)
            missing = None if last is None else (now - last) // timeframe_to_ms(pair[1])
            (long if missing is None or missing > max_bars else short).append(pair)
        if short:
            if calls == 0:
                logger.info("정지로 보이는 팔 종목 시리즈의 꼬리를 판정 전에 받습니다(WAN-472)")
            elif settings.universe_request_interval_seconds > 0:
                sleeper(settings.universe_request_interval_seconds)
            calls += 1
            backfill_pairs(
                exchange,
                store,
                short,
                lookback_days=settings.lookback_days_for,
                pause_seconds=settings.universe_request_interval_seconds,
                progress=progress,
                now_ms=now_ms,
            )
        return long

    return refill


def report_funding_task_exit(task: asyncio.Task[None]) -> None:
    """펀딩 최신화 태스크가 끝났을 때 **조용히 끝나지 않게** 한다(WAN-422 §3).

    「펀딩 루프가 죽어도 스트림은 유지된다」는 설계(펀딩 때문에 봉 수집이 멈추면 더
    나쁘다)는 그대로 지킨다 — 여기서 바꾸는 것은 *조용히* 뿐이다. 무한 루프라 정상
    종료는 없으므로, 취소가 아닌 모든 종료(예외 포함)를 `ERROR`로 남긴다. 감시 쪽은
    `alphablock watch`가 **마지막 확정 정산 지연**으로 잡는다(`dashboard.health`) — 태스크가
    죽으면 확정이 더 이상 안 쌓여 그 지연이 문턱을 넘는다.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "펀딩 최신화 태스크가 예외로 죽었습니다 — 봉 수집은 계속됩니다: %r",
            exc,
            exc_info=exc,
        )
    else:
        logger.error(
            "펀딩 최신화 태스크가 종료됐습니다(정상 종료가 없는 루프) — 봉 수집은 계속됩니다"
        )


def report_universe_task_exit(task: asyncio.Task[None]) -> None:
    """후보 풀 루프가 끝나면 시끄럽게 남긴다(정상 종료가 없는 루프 · WAN-422 §3과 같은 규약)."""
    if task.cancelled():
        return
    exc = task.exception()
    logger.error(
        "후보 풀·종목 목록 태스크가 종료됐습니다 — 봉 수집은 계속됩니다: %r",
        exc,
        exc_info=exc,
    )


async def _run_universe(settings: Settings, store: OhlcvStore) -> None:
    """후보 풀 일봉 + 매주 목록 루프(WAN-451 ③). 거래소 인스턴스는 따로 만든다(스레드 공유 회피)."""
    from data.universe_pool import run_universe_loop

    telegram = build_telegram_client(settings)

    def _notify(text: str) -> None:
        if telegram is not None:
            telegram.send_message(text, parse_mode=None)

    universe = UniverseStore(settings.db_path)
    try:
        await run_universe_loop(
            create_exchange(settings),
            store,
            universe,
            lookback_days=settings.lookback_days_for("1d"),
            request_interval_seconds=settings.universe_request_interval_seconds,
            delay_seconds=settings.universe_refresh_delay_seconds,
            notify=_notify,
        )
    finally:
        universe.close()


def _build_arm_collection(settings: Settings, store: OhlcvStore) -> ArmCollection:
    """팔 수집 관리 객체 — 거래소 인스턴스는 따로 만든다(스레드에서 기본 격자와 겹쳐 돈다)."""
    return ArmCollection(
        exchange=create_exchange(settings),
        store=store,
        universe=UniverseStore(settings.db_path),
        db_path=settings.db_path,
        base_symbols=settings.symbols,
        base_timeframes=settings.timeframes,
        lookback_days=settings.lookback_days_for,
        pause_seconds=settings.universe_request_interval_seconds,
        history_days=settings.live_zone_memory_days,
    )


#: 운영 중 교체에서 채우기 태스크가 돌려주는 것 — (새 계획, 새 쌍, 받은 봉 수).
_FillResult = tuple[CollectionPlan, list[tuple[str, str]], int]


class PlanTransition:
    """운영 중 계획 교체의 「새 쌍을 채우는 중」 상태 — **재접속을 넘어 산다**(WAN-468).

    새 쌍 채우기(`arm.fill` + `arm.extend_history`)는 주간 교체 날 수 분이 걸린다. 그동안 옛
    스트림은 계속 돌아야 하고 **감시도 받아야 한다** — 스트림이 그 사이 끊기면 곧바로 복구
    루프로 올려 다시 붙인다. 그때 채우기를 처음부터 다시 하지 않도록 진행 중인 태스크를 여기
    들고 있다가, 다시 붙은 `_stream_arm_plan`이 이어서 기다린다.
    """

    def __init__(self) -> None:
        self.task: asyncio.Task[_FillResult] | None = None


async def _fill_new_plan(arm: ArmCollection, new_plan: CollectionPlan) -> _FillResult:
    """새 계획의 새 쌍을 채우고 이력을 늘린다(채택 **전** — 옛 스트림은 그대로 돈다)."""
    new_pairs = new_plan.new_pairs(arm.plan)
    filled = await asyncio.to_thread(arm.fill, new_pairs, new_plan.arm_symbols)
    # 원래 기본 격자로 모으던 종목이 팔로 들어오면 이력이 얕을 수 있다 — 새로 든 쌍만 늘린다
    # (이미 시도한 쌍은 건너뛴다, WAN-451 오래된 존 기억).
    filled += await asyncio.to_thread(arm.extend_history, new_plan)
    return new_plan, new_pairs, filled


async def _stream_arm_plan(
    store: OhlcvStore,
    arm: ArmCollection,
    *,
    on_progress: Callable[[Candle], None],
    idle_timeout_seconds: float | None,
    on_connect: Callable[[], None],
    check_seconds: float,
    progress_grace_seconds: float | None = None,
    transition: PlanTransition | None = None,
) -> None:
    """지금 계획으로 스트림을 돌리다 계획이 바뀌면 새 쌍을 채우고 다시 붙는다(WAN-451 ③).

    계획 교체는 오류가 아니다 — 복구 루프(`run_with_recovery`)로 돌려보내지 않고 여기서 다시
    붙는다(복구 알림이 매주 울리지 않게). 스트림이 끊기거나 멎으면 예외를 그대로 올린다.

    🚨 **새 쌍을 채우는 동안에도 스트림을 지켜본다(WAN-468).** 예전에는 계획이 바뀌면 채우기
    (`arm.fill` → `extend_history`, 주간 교체 날 수 분)를 다 끝낼 때까지 스트림 태스크를 아무도
    기다리지 않았다. 그 사이 스트림이 끊기면 재접속도 진행 표시도 없이 워치독 한도가 지나 프로세스가
    죽었고, 재시작해도 같은 교체를 다시 시작해 고리가 됐다(2026-10-19 09:17·09:30). 이제 채우기는
    태스크로 돌고(`transition` — 재접속을 넘어 산다) 스트림과 함께 기다린다: 스트림이 먼저 끝나면
    예외를 곧바로 올려 복구 루프가 **옛 계획으로** 다시 붙이고, 채우기가 끝나면 그때 새 계획을
    채택하고 다시 붙는다.
    """
    transition = transition if transition is not None else PlanTransition()
    while True:
        stream = asyncio.create_task(
            stream_kline_pairs(
                store,
                arm.plan.pairs(),
                on_progress=on_progress,
                idle_timeout_seconds=idle_timeout_seconds,
                on_connect=on_connect,
                on_candle=arm.on_candle,
                progress_grace_seconds=progress_grace_seconds,
            )
        )
        while True:
            filling = transition.task
            waiter: asyncio.Task[CollectionPlan] | asyncio.Task[_FillResult] = (
                filling
                if filling is not None
                else asyncio.create_task(_wait_plan_change(arm, check_seconds))
            )
            done, _ = await asyncio.wait({stream, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if stream in done:
                if filling is None:
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
                elif not filling.done():
                    logger.warning(
                        "팔 새 쌍 채우는 중 스트림이 끝남 — 옛 계획으로 다시 붙고 "
                        "채우기는 이어 갑니다"
                    )
                stream.result()  # 예외면 그대로 올린다(복구 루프가 다시 붙인다)
                return
            if filling is None:
                new_plan = waiter.result()
                assert isinstance(new_plan, CollectionPlan)
                transition.task = asyncio.create_task(_fill_new_plan(arm, new_plan))
                continue  # 스트림은 그대로 두고 채우기와 함께 기다린다
            transition.task = None
            try:
                new_plan, new_pairs, filled = filling.result()
            except Exception:  # noqa: BLE001 — 옛 계획으로 계속 돌고 다음 주기에 다시 본다
                logger.exception("팔 새 쌍 채우기 실패 — 옛 계획으로 계속(다음 주기에 다시)")
                continue
            break
        added, removed = arm.adopt(new_plan)
        logger.info(
            "팔 수집 종목 교체: 들어옴 %s · 빠짐 %s · 새 쌍 %d개 백필 %d봉 — 웹소켓 재접속",
            ",".join(added) or "없음",
            ",".join(removed) or "없음",
            len(new_pairs),
            filled,
        )
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)


async def _wait_plan_change(arm: ArmCollection, check_seconds: float) -> CollectionPlan:
    """계획이 바뀔 때까지 주기적으로 다시 계산한다(바뀐 계획을 돌려준다)."""
    while True:
        await asyncio.sleep(check_seconds)
        try:
            plan = await asyncio.to_thread(arm.compute)
        except Exception:  # noqa: BLE001 — 다음 주기에 다시 본다
            logger.exception("팔 수집 계획 계산 실패(다음 주기에 다시)")
            continue
        if plan.pairs() != arm.plan.pairs():
            return plan


class ProgressSignals:
    """수집기의 「진행」 신호 한 곳(WAN-470) — 하트비트 파일과 두 워치독을 무엇이 찍는가.

    🚨 「진행」은 **메시지 수신이 아니라 새 봉**이다. 2026-10-22 04:00 KST 사고에서 연결 1/2는
    4.5시간 동안 메시지는 계속 보내면서 새 봉을 하나도 안 줬고, 메시지마다 찍던 하트비트·워치독은
    내내 「살아 있음」이었다(`alphablock status` [OK] 0분 전).

    * `data` = 데이터 진행: 웹소켓 새 확정봉 · REST 수집 한 바퀴(봉을 넣었거나 이미 현재까지 와
      있음) · 기동 백필 진척. 하트비트 파일도 같은 순간에 찍는다.
    * `stream` = 웹소켓 진행: 웹소켓이 **이 프로세스에서 처음 보는 더 늦은** 확정봉을 줬을 때만.
      재접속 뒤 같은 옛 봉을 다시 줘도 세지 않는다(연결 단위 판정은 `consume_messages`가 따로).
    """

    def __init__(self, heartbeat: HeartbeatStore) -> None:
        self._heartbeat = heartbeat
        self.data = ProgressTracker()
        self.stream = ProgressTracker()
        self._stream_newest: dict[tuple[str, str], int] = {}

    def mark_data(self) -> None:
        self._heartbeat.beat()
        self.data.mark()

    def mark_startup(self) -> None:
        """기동 백필 진척 — 웹소켓 접속 전이라 둘 다 찍는다(WAN-451 — 안 찍으면 워치독이 죽인다)."""
        self.mark_data()
        self.stream.mark()

    def on_stream_progress(self, candle: Candle) -> None:
        key = (candle.symbol, candle.timeframe)
        seen = self._stream_newest.get(key)
        if seen is not None and candle.open_time <= seen:
            return
        self._stream_newest[key] = candle.open_time
        self.mark_data()
        self.stream.mark()


def _build_plan_pairs(settings: Settings) -> list[tuple[str, str]]:
    """팔이 꺼져 있을 때의 수집 계획(기본 격자)."""
    return build_plan(settings.symbols, settings.timeframes).pairs()


def watchdog_timeouts(settings: Settings) -> tuple[float, float]:
    """(데이터, 웹소켓) 워치독 한도(초) — 둘 다 「가장 짧은 TF + 여유」보다 짧지 않게(WAN-470).

    진행이 「새 봉」이 됐으므로 가장 짧은 TF가 길면(1분봉 없이 15m만 모으는 설정) 정상인데도 그 TF
    간격만큼 진행이 없다. 설정값이 그보다 짧으면 늘려 쓴다(오살 방지). 팔이 켜져 있으면 팔 TF도
    본다(팔은 1분봉을 늘 받는다 — `ARM_STREAM_TIMEFRAMES`).
    """
    timeframes = list(settings.timeframes)
    if settings.universe_enabled:
        timeframes += list(ARM_STREAM_TIMEFRAMES)
    shortest = min((timeframe_to_ms(tf) for tf in timeframes), default=60_000) / 1000
    floor = shortest + settings.collector_stream_progress_grace_seconds
    data_timeout = max(settings.collector_watchdog_timeout_seconds, floor)
    stream_timeout = max(
        settings.collector_stream_watchdog_timeout_seconds,
        floor + settings.collector_stream_idle_timeout_seconds,
        data_timeout,
    )
    return data_timeout, stream_timeout


def _build_rest_poller(
    settings: Settings,
    store: OhlcvStore,
    arm: ArmCollection | None,
    *,
    on_progress: Callable[[], None],
    hold_long_gaps: Callable[[], bool] | None = None,
) -> ClosedBarPoller:
    """확정봉 REST 이중 수집기(WAN-470 §0). 거래소 인스턴스는 따로 만든다(스레드 공유 회피).

    대상 = 지금 수집 계획(팔이 있으면 팔 계획 — 주간 교체를 따라간다). 팔 종목의 1h를 넣으면 그
    버킷의 3h를 이어 만든다(스트림 훅 `ArmCollection.on_candle`과 같은 일).
    """
    base_pairs = _build_plan_pairs(settings)

    def _pairs() -> list[tuple[str, str]]:
        return arm.plan.pairs() if arm is not None else base_pairs

    def _on_filled(symbol: str, timeframe: str, _n: int) -> None:
        if arm is None or symbol not in arm.plan.arm_symbols:
            return
        for target, source in BUILT_TIMEFRAMES.items():
            if source == timeframe:
                build_derived(store, symbol, target)

    return ClosedBarPoller(
        create_exchange(settings),
        store,
        _pairs,
        offset_seconds=settings.collector_rest_poll_offset_seconds,
        max_bars=settings.collector_rest_poll_max_bars,
        on_progress=on_progress,
        hold_long_gaps=hold_long_gaps,
        on_filled=_on_filled,
    )


def _beat_file(heartbeat: HeartbeatStore) -> None:
    """하트비트 파일만 찍는다(기동 백필 쪽마다 · WAN-468 — 워치독은 아직 안 돈다)."""
    heartbeat.beat()


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

    # 멎은 것 같으면 `kill -USR1 <pid>`로 태스크 스택을 로그에 받는다(WAN-470 · pid는 하트비트).
    install_task_dump_signal(asyncio.get_running_loop())

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
        # 쪽마다 하트비트 파일을 찍는다(WAN-468) — 워치독은 아직 안 돌지만 `status`·워치가 보는
        # 「마지막 하트비트」가 긴 기동 백필 동안 멎은 것처럼 보이지 않게(진행이 있으면 살아 있다).
        results = await asyncio.to_thread(
            partial(
                backfill_all,
                exchange,
                store,
                settings.symbols,
                settings.timeframes,
                settings=settings,
                progress=partial(_beat_file, heartbeat),
            )
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
            # 판정 범위는 감시(WAN-458)와 같은 「지금 수집 계획」이다(WAN-466) — 저장된 전부를
            # 보면 팔 목록에서 빠진 종목이 재시작마다 「🚨 수집 정지」로 울린다. 계획을 못
            # 읽으면 None → 옛 동작(전부 판정).
            scope = monitor_scope_for(settings)
            # 정지로 보이는 팔 종목은 판정 전에 짧은 꼬리를 받는다(WAN-472/474) — 팔 종목 꼬리는
            # 접속 뒤 따라잡기가 채우므로 그대로 재면 꺼져 있던 몇 분이 「🚨 수집 정지」로 찍힌다.
            # 긴 꼬리는 받지 않고 「채우는 중」으로 넘긴다. 팔이 꺼져 있으면 None → 옛 동작.
            refill = (
                _arm_tail_refill(settings, exchange, store, progress=partial(_beat_file, heartbeat))
                if settings.universe_enabled
                else None
            )
            summary = await asyncio.to_thread(
                partial(
                    repair_all,
                    exchange,
                    store,
                    lookback_ms=lookback_ms,
                    scope=scope,
                    refill_stale=refill,
                )
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

        # 진행 신호(WAN-470) — 메시지가 아니라 **새 봉**이 진행이다(`ProgressSignals`).
        signals = ProgressSignals(heartbeat)
        tracker, stream_tracker = signals.data, signals.stream
        _mark_data, _mark_startup = signals.mark_data, signals.mark_startup
        _on_stream_progress = signals.on_stream_progress

        if run_stream:
            # 실시간 스트림과 함께 펀딩 현재값을 주기적으로 최신화한다(백필은 위에서
            # 이미 했으므로 backfill=False). 펀딩 루프가 죽어도 스트림은 유지된다.
            funding_task: asyncio.Task[None] | None = None
            if settings.funding_enabled:
                funding_task = asyncio.create_task(
                    run_funding_refresh(settings, exchange=exchange, backfill=False)
                )
                funding_task.add_done_callback(report_funding_task_exit)

            # 팔의 후보 풀 일봉 + 매주 종목 목록(WAN-451 ③). 스트림 접속을 늦추지 않게
            # 백그라운드로 돈다(첫 적재는 수백 종목이라 수 분 — WAN-314 기동 지연 사고와 같은 자리).
            universe_task: asyncio.Task[None] | None = None
            if settings.universe_enabled:
                universe_task = asyncio.create_task(_run_universe(settings, store))
                universe_task.add_done_callback(report_universe_task_exit)

            # 프로세스 레벨 워치독 스레드(WAN-173 (2)). in-process 재접속(아래 유휴
            # 워치독)조차 못 돌 만큼 이벤트 루프가 멎으면 hang→exit로 바꿔 서비스
            # 매니저가 재시작하게 한다. 진행 시각은 백필 완료 직후로 초기화한다.
            watchdogs: list[HeartbeatWatchdog] = []
            if settings.collector_watchdog_enabled:
                _mark_startup()
                data_timeout, stream_timeout = watchdog_timeouts(settings)
                watchdogs = [
                    HeartbeatWatchdog(
                        tracker,
                        timeout_seconds=data_timeout,
                        poll_seconds=settings.collector_watchdog_poll_seconds,
                        on_stale=force_exit(what="새 봉(데이터 진행)"),
                        name="collector-watchdog",
                    ),
                    HeartbeatWatchdog(
                        stream_tracker,
                        timeout_seconds=stream_timeout,
                        poll_seconds=settings.collector_watchdog_poll_seconds,
                        on_stale=force_exit(what="웹소켓 새 확정봉"),
                        name="collector-stream-watchdog",
                    ),
                ]
                for wd in watchdogs:
                    wd.start()
                logger.info(
                    "워치독: 새 봉 %gs · 웹소켓 새 확정봉 %gs 넘게 없으면 종료(WAN-470)",
                    data_timeout,
                    stream_timeout,
                )

            # 팔 종목 수집(WAN-451 ③): 그 주 목록 + 보유 종목을 1분봉·전 TF로. 목록 기능이
            # 꺼져 있으면 None이고 아래는 예전 경로 그대로다.
            arm = _build_arm_collection(settings, store) if settings.universe_enabled else None
            history_task: asyncio.Task[int] | None = None
            if arm is not None:
                plan = arm.compute()
                arm.adopt(plan)
                # 🚨 팔 종목 따라잡기를 여기(접속 전)서 돌리지 않는다(WAN-474). 279쌍 × 0.5초 + 새로
                # 들어온 종목의 이력 전체라 교체 날엔 10분을 넘고, 그동안 웹소켓·REST 이중 수집이
                # 안 붙어 **기본 격자까지** 새 봉이 끊긴다 — 2026-10-26 배포 재시작에서 13분 공백 →
                # 외부 감시가 「수집기가 멎었다」로 다시 재시작했다. 같은 일은 접속 직후
                # `TailCatchup`(`extra=_arm_catch_up`)가 접속 순간 스냅샷에서 백그라운드로 한다
                # (WAN-456) — 받는 데이터는 같고 막히는 시간만 없어진다.
                # 이력을 뒤로 늘리는 일(오래된 존 기억, WAN-451)도 백그라운드다(WAN-314).
                history_task = asyncio.create_task(asyncio.to_thread(arm.extend_history))
                logger.info(
                    "팔 종목 수집 계획: 팔 %d종목 · 스트림 %d개 — 따라잡기는 접속 뒤(WAN-474)",
                    len(plan.arm_symbols),
                    len(plan.pairs()),
                )

            # 접속 직후 꼬리 따라잡기(WAN-314) — 기동 백필~접속 사이·재접속 공백에
            # 닫힌 봉은 스트림이 다시 주지 않으므로 매 접속마다 REST로 메운다.
            catchup = (
                TailCatchup(
                    exchange,
                    store,
                    settings,
                    extra=partial(_arm_catch_up, arm),
                    extra_pairs=arm.arm_pairs,
                )
                if arm is not None
                else TailCatchup(exchange, store, settings)
            )

            # 확정봉 REST 이중 수집(WAN-470 §0) — 웹소켓이 얼어도 매 분 빠진 봉을 받는다.
            # 기동 백필이 끝난 뒤에 붙인다(그 전에 돌면 같은 꼬리를 두 번 받는다).
            poller_task: asyncio.Task[None] | None = None
            if settings.collector_rest_poll_enabled:
                # 첫 따라잡기가 끝나기 전엔 긴 공백을 건드리지 않는다(WAN-474 — 영구 구멍 방지).
                poller = _build_rest_poller(
                    settings,
                    store,
                    arm,
                    on_progress=_mark_data,
                    hold_long_gaps=catchup.startup_pending,
                )
                poller_task = asyncio.create_task(poller.run())
                poller_task.add_done_callback(report_poller_task_exit)

            # 운영 중 계획 교체의 「채우는 중」 상태 — 재접속을 넘어 이어 간다(WAN-468).
            transition = PlanTransition()

            async def _stream_once() -> None:
                # 한 번의 접속·소비. 유휴 타임아웃을 걸어(WAN-173 (1)) half-open stall을
                # StreamStalled 예외로 바꾼다 — run_with_recovery가 잡아 재접속한다.
                if arm is not None:
                    await _stream_arm_plan(
                        store,
                        arm,
                        on_progress=_on_stream_progress,
                        idle_timeout_seconds=settings.collector_stream_idle_timeout_seconds,
                        on_connect=catchup.schedule,
                        check_seconds=settings.universe_plan_check_seconds,
                        progress_grace_seconds=settings.collector_stream_progress_grace_seconds,
                        transition=transition,
                    )
                    return
                await stream_klines(
                    store,
                    settings.symbols,
                    settings.timeframes,
                    on_progress=_on_stream_progress,
                    idle_timeout_seconds=settings.collector_stream_idle_timeout_seconds,
                    on_connect=catchup.schedule,
                    progress_grace_seconds=settings.collector_stream_progress_grace_seconds,
                )

            try:
                await run_with_recovery(
                    _stream_once,
                    on_recover=_build_recovery_notifier(settings),
                    max_backoff_seconds=settings.collector_reconnect_max_backoff_seconds,
                    healthy_after_seconds=settings.collector_reconnect_healthy_after_seconds,
                )
            finally:
                if funding_task is not None:
                    funding_task.cancel()
                if universe_task is not None:
                    universe_task.cancel()
                if history_task is not None:
                    history_task.cancel()
                if poller_task is not None:
                    poller_task.cancel()
                for wd in watchdogs:
                    wd.stop()
    finally:
        store.close()


def main() -> None:
    """CLI 엔트리포인트: `python -m data.collector`."""
    use_kst_logging()  # 로그 시각도 KST(WAN-172)
    logging.basicConfig(level=logging.INFO, format=kst_log_format())
    asyncio.run(run_collector())


if __name__ == "__main__":
    main()
