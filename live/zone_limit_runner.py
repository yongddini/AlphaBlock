"""존-지정가 페이퍼 러너 — 채택 진입 경로(B안)의 실시간 배선 (WAN-45).

채택 기본값(`entry_mode="zone_limit"`)에서 도는 러너다. 목적은 두 가지:

1. **체결률 실측(1순위)** — 이 저장소의 모든 백테스트 판정은 `baseline`("닿으면 체결")
   낙관 가정 위에 서 있는데, 그 가정을 풀 예정이던 틱·호가 수집(WAN-98)이 Canceled라
   실측이 유일한 통로다. 존에 지정가를 걸어두면 체결되는지 안 되는지 그냥 알게 된다 —
   걸린/체결된/만료·취소된 주문을 `live.order_journal`에 누적하고, 요약은
   `python -m live.fill_report`가 백테스트 가정과 나란히 찍는다.
2. **백테스트↔라이브 간극 닫기** — 옛 러너(`live.runner.SignalRunner`)는 A안(봉 마감
   종가) 시그널 생성기라, WAN-95 이후 채택 기본값이 지정가로 바뀐 뒤에도 사용자가 하지
   않는 매매를 알리고 있었다.

## 틱 소스 — 웹소켓 → 수집기 → 저장소 → 러너 (1분봉 해상도)

수집 데몬(`alphablock collect`)이 바이낸스 웹소켓 kline 스트림(`data.stream`)으로 확정
1분봉을 저장소에 쌓고, 이 러너는 그 1분봉을 **백테스트 서브스텝과 같은 자**로 소비한다
(`ZoneLimitLiveEngine.on_substep` — 터치 판정 `low <= 지정가`, 밴드·RSI 표본 = 1분봉
종가). 러너가 웹소켓을 직접 구독하지 않는 이유:

* **측정 대상이 백테스트의 가정 그 자체다** — 백테스트가 1분봉 저가로 체결을 판정하므로
  같은 자로 재야 "라이브에서 그 가정대로 체결되는가"에 답이 된다. 더 촘촘한 틱은 체결
  판정을 바꾸지 않으면서(판정은 봉 범위 기준) 비교 축만 흐린다.
* PM 실측(이슈 코멘트 §1)대로 밴드는 봉 안에서 중앙값 1.6~4.1bp밖에 안 움직인다 —
  분 단위 재산정이면 충분하고, 페이퍼에는 주문 정정 비용도 없다.
* 수집기가 이미 웹소켓·재접속·하트비트를 소유한다 — 러너가 제 소켓을 열면 같은 걱정을
  두 벌 관리하게 된다.

그 대가로 체결 감지가 1분봉 확정 + 폴링 간격만큼 늦지만, 체결 시각·대기 시간은 벽시계가
아니라 **봉 시각**으로 기록하므로 측정값은 지연과 무관하다.

## 로컬 맥 운영의 한계 처리 (사용자 결정 2026-07-21)

* **가동 시간 기록** — 러너 세션(시작·마지막 하트비트)을 장부에 남긴다. 체결률의 분모가
  "러너가 살아 있던 시간"임을 표에서 명시할 수 있다.
* **재시작 정책 = 버리고 새로 걸기** — 이전 세션의 대기 주문은 복원하지 않고
  `discarded_restart`로 마감한다(체결률 분모에서 제외). 죽어 있던 구간의 가격 경로를
  지어내지 않는다. 오픈 포지션은 `PaperExecutor`가 저장소에서 복구한다(기존 WAN-34 배선).
* **중단 구간은 데이터로 남는다** — 세션 행 사이의 틈이 곧 다운타임이다.

페이퍼 한정: 실주문 API는 부르지 않는다(`build_execution_engine`이 `live_trading=false`에서
`PaperBroker`를 보장). `ALPHABLOCK_LIVE_TRADING` 기본값 `false` 불변.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from backtest.sweep import timeframe_to_ms
from common.timefmt import format_kst_zoned, kst_day_key
from config.settings import Settings
from data.freshness import window_gap_summary
from data.storage import OhlcvStore
from execution.engine import EntryIntent
from live.executor import PaperExecutor
from live.limit_engine import EngineEvent, ReentryEntryRule, ZoneLimitLiveEngine
from live.order_journal import SKIP_REASON_ZONE_WIDTH, OrderJournal
from live.paper import PaperPosition
from live.price_feed import PriceFeed
from live.runtime_state import PendingOrderSnapshot, RuntimeStateStore
from live.zone_limit_notifier import ZoneLimitNotifier
from strategy.models import ConfluenceParams, OrderBlockDirection, SignalExitReason

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
Series = tuple[str, str]

_MINUTE_MS = 60_000


class ZoneLimitPaperRunner:
    """저장소를 폴링해 존-지정가 주문을 상시 관리하는 페이퍼 러너."""

    def __init__(
        self,
        *,
        store: OhlcvStore,
        engine: ZoneLimitLiveEngine,
        journal: OrderJournal,
        session_id: int,
        executor: PaperExecutor,
        params: ConfluenceParams,
        series: list[Series],
        lookback_bars: int,
        poll_interval_seconds: float,
        runtime_state: RuntimeStateStore | None = None,
        notifier: ZoneLimitNotifier | None = None,
        price_feed: PriceFeed | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._store = store
        self._engine = engine
        self._journal = journal
        self._session_id = session_id
        self._executor = executor
        self._params = params
        self._series = series
        self._lookback_bars = lookback_bars
        self._poll_interval = poll_interval_seconds
        self._runtime_state = runtime_state
        self._notifier = notifier
        #: 옵트인 웹소켓 틱 피드(WAN-246). None이면 확정 1분봉만 소비(예전과 비트 동일).
        self._price_feed = price_feed
        #: 심볼 → 그 심볼을 감시하는 (symbol, timeframe) 시리즈들(틱 팬아웃용).
        self._series_by_symbol: dict[str, list[Series]] = {}
        for sym, tf in series:
            self._series_by_symbol.setdefault(sym, []).append((sym, tf))
        self._sleep = sleep
        self._now_ms = now_ms
        #: 시리즈별 마지막으로 반영한 확정 상위TF 봉 시각.
        self._last_htf: dict[Series, int] = {}
        #: 시리즈별 마지막으로 소비한 1분봉 시각(그 이후만 새로 읽는다).
        self._last_substep: dict[Series, int] = {}

    # -- 폴링 ---------------------------------------------------------------

    def poll_series(self, symbol: str, timeframe: str) -> list[EngineEvent]:
        """한 시리즈를 한 번 폴링한다: 확정 상위TF 봉 반영 → 새 1분봉을 서브스텝으로 소비."""
        events: list[EngineEvent] = []
        htf_ms = self._store_htf_ms(timeframe)

        df = self._store.load(symbol, timeframe)
        if df.empty:
            return []
        recent = df.tail(self._lookback_bars)
        closed = recent[recent["closed"].astype(bool)]
        if closed.empty:
            return []
        last_closed_time = int(closed["open_time"].iloc[-1])

        # 지표·존을 계산하기 전에 창이 연속인지 본다 — 구멍이 있으면 볼린저·RSI가 조용히
        # 틀린 값을 내고, 그 값이 곧 주문 가격이다(WAN-156 §3, SignalRunner와 같은 거부).
        discontinuity = window_gap_summary(
            [int(t) for t in closed["open_time"].tolist()], timeframe
        )
        if discontinuity is not None:
            _logger.error(
                "%s %s: %s — 평가를 건너뜁니다. `alphablock backfill`로 갭을 메우세요.",
                symbol,
                timeframe,
                discontinuity,
            )
            return []

        key = (symbol, timeframe)
        if self._last_htf.get(key) != last_closed_time:
            events += self._engine.on_htf_bars(symbol, timeframe, closed)
            self._last_htf[key] = last_closed_time

        # 1분봉 서브스텝. 첫 관측(프라이밍)은 현재 형성 중인 상위TF 봉의 시작부터다 —
        # 과거를 재생하면 이미 지나간 탭에 뒷북 주문을 걸게 된다(측정 오염).
        since = self._last_substep.get(key)
        if since is None:
            since = last_closed_time + htf_ms - _MINUTE_MS
        df_1m = self._store.load(symbol, "1m", start_ms=since + 1)
        if df_1m.empty:
            self._handle_events(events)
            return events
        if "closed" in df_1m.columns:
            df_1m = df_1m[df_1m["closed"].astype(bool)]
        for row in df_1m.itertuples(index=False):
            t = int(row.open_time)
            low, high, close = float(row.low), float(row.high), float(row.close)
            step_events = self._engine.on_substep(
                symbol, timeframe, time_ms=t, low=low, high=high, close=close
            )
            self._handle_events(step_events)
            events += step_events
            self._check_exits(symbol, timeframe, low=low, high=high, time_ms=t)
            self._last_substep[key] = t
        return events

    def poll_once(self) -> list[EngineEvent]:
        """모든 시리즈를 한 번씩 폴링하고 하트비트·상태 스냅샷을 남긴다."""
        emitted: list[EngineEvent] = []
        for symbol, timeframe in self._series:
            try:
                emitted += self.poll_series(symbol, timeframe)
            except Exception:  # noqa: BLE001 — 한 시리즈 오류가 루프 전체를 멈추지 않도록.
                _logger.exception("시리즈 폴링 실패: %s %s", symbol, timeframe)
        # 웹소켓 틱으로 대기 주문 체결을 앞당긴다(WAN-246, 옵트인). 예약은 위 1분봉
        # 경로가 이미 끝냈으므로, 여기서는 걸려 있는 주문의 체결만 더 촘촘히 잡는다.
        emitted += self._drain_ticks()
        now = self._now_ms()
        self._journal.heartbeat(self._session_id, now_ms=now)
        self._check_circuit_breaker(now)
        if self._notifier is not None:
            # KST 날짜가 바뀌면 전날 예약·체결·만료 일일 요약을 보낸다(만료 실시간 대체).
            self._notifier.tick(self._executor.equity, now_ms=now)
        if self._runtime_state is not None:
            self._runtime_state.record(
                now_ms=now,
                open_positions=self._paper_positions(),
                new_events=[],
                pending_orders=self._pending_snapshots(),
            )
        return emitted

    def _drain_ticks(self) -> list[EngineEvent]:
        """옵트인 틱 피드에 쌓인 실시간 체결가를 대기 주문에 반영한다(WAN-246).

        심볼 단위 체결가를 그 심볼을 감시하는 각 (symbol, timeframe) 시리즈의 대기 주문에
        `on_tick`으로 넣는다(단일가라 low=high=close=price). 체결 이벤트는 1분봉 경로와
        **같은** `_handle_events`로 집행·기록·알림한다. 청산 판정(`_check_exits`)은 1분봉
        경로에 남긴다 — 진입 체결만 앞당기고 청산 해상도는 백테스트(1분봉)와 맞춘다.
        """
        feed = self._price_feed
        if feed is None:
            return []
        ticks = feed.drain()
        if not ticks:
            return []
        events: list[EngineEvent] = []
        for tick in ticks:
            for symbol, timeframe in self._series_by_symbol.get(tick.symbol, ()):
                step = self._engine.on_tick(
                    symbol, timeframe, price=tick.price, time_ms=tick.time_ms
                )
                if step:
                    self._handle_events(step)
                    events += step
        return events

    def run(self, *, max_polls: int | None = None) -> None:
        """폴링 루프. `max_polls`가 None이면 무한 반복(테스트에서는 유한 지정)."""
        poll = 0
        while max_polls is None or poll < max_polls:
            self.poll_once()
            poll += 1
            if max_polls is not None and poll >= max_polls:
                break
            self._sleep(self._poll_interval)

    # -- 서킷브레이커 알림 (WAN-38) ------------------------------------------

    def _check_circuit_breaker(self, now_ms: int) -> None:
        """일일 손실 서킷브레이커 상태 전환을 감지해 발동/해제 텔레그램을 각 1회 보낸다.

        중복 방지 상태(마지막으로 알린 `(KST일, 발동여부)`)는 `paper_trades` 옆 원장에
        영속되므로 러너 재시작 후에도 발동 알림이 재전송되지 않는다(WAN-38). 판정 자체는
        `executor.circuit_breaker_status`가 DB 재계산으로 내므로 표시와 실제 차단이
        어긋나지 않는다.
        """
        status = self._executor.circuit_breaker_status(now_ms)
        if not status.enabled:
            return
        day = kst_day_key(now_ms)
        notice_day, notice_tripped = self._executor.get_circuit_breaker_notice()
        if status.tripped:
            if not (notice_day == day and notice_tripped):
                if self._notifier is not None:
                    self._notifier.handle_circuit_breaker_tripped(status, now_ms=now_ms)
                self._executor.set_circuit_breaker_notice(day, tripped=True)
        elif notice_tripped:
            # 발동 중이었다가 풀렸다(대개 KST 일자 전환) — 해제 알림 1회.
            if self._notifier is not None:
                self._notifier.handle_circuit_breaker_cleared(status, now_ms=now_ms)
            self._executor.set_circuit_breaker_notice(day, tripped=False)

    # -- 집행·청산 -----------------------------------------------------------

    def _handle_events(self, events: list[EngineEvent]) -> None:
        for event in events:
            if event.kind == "placed":
                order = event.order
                if self._notifier is not None:
                    self._notifier.note_placed()  # 예약은 실시간 알림 없음 — 일일 요약에만.
                # 시각은 KST로 찍는다(WAN-172) — 로그·텔레그램·`status`가 같은 자다.
                # 판정·저장에 쓰는 값은 UTC epoch ms 그대로다.
                _logger.info(
                    "지정가 예약: %s %s %s %s 지정가=%s 손절=%.6g (탭 #%d)",
                    event.symbol,
                    event.timeframe,
                    order.direction.value,
                    format_kst_zoned(order.placed_ms),
                    "봉내 재산정" if order.live_limit is not None else f"{order.limit_price:.6g}",
                    order.stop_price,
                    order.tap_index,
                )
            elif event.kind == "filled" and event.fill is not None:
                self._handle_fill(event)
            else:
                if self._notifier is not None and event.kind == "cancelled_expired":
                    self._notifier.note_expired()  # 만료는 실시간 없음 — 일일 요약에만.
                    # 밴드가 한 번도 유리하지 않아 주문판에 실린 적 없는 만료 = 볼린저 규칙 3
                    # 기각(deviation). 옵트인 건별 알림에만 넘긴다(no_fill은 넘기지 않는다 —
                    # WAN-221: 걸렸다 안 닿은 순수 미체결은 건별로 보내지 않는다).
                    if event.order.first_rested_ms is None:
                        self._notifier.note_filter_skip(
                            "deviation",
                            symbol=event.symbol,
                            timeframe=event.timeframe,
                            time_ms=event.time_ms,
                        )
                _logger.info(
                    "지정가 주문 종결: %s %s %s", event.symbol, event.timeframe, event.kind
                )

    def _handle_fill(self, event: EngineEvent) -> None:
        """체결 이벤트를 집행·처분 기록·알림한다 — 셋 중 하나가 끊겨도 나머지를 유실하지 않는다.

        체결(장부 `filled` 기록됨) 직후 진입을 집행하는데, WAN-207 이전에는 세 가지가
        연쇄로 묶여 하나만 어긋나도 **처분 기록과 진입 알림이 함께 조용히 유실**됐다:

        * ⚠️ **근본 원인(WAN-207)**: 처분 기록이 `order.journal_id`를 읽었는데 `order`는
          위쪽 `placed` 분기에서만 대입되는 지역 변수였다. 체결이 예약과 **다른 폴링
          배치**에서 나면(주문이 걸린 뒤 몇 분 후 가격이 닿는 흔한 경우) 이 배치엔
          `placed`가 없어 `order`가 **미대입** → `UnboundLocalError`가 `enter()`로
          포지션을 연 **직후** 터졌다. 그래서 `entry_status`는 NULL로 남고(WAN-194가
          "쓰기 사이의 유실"로 부르던 그 모양) 진입 텔레그램도 건너뛰어졌는데, 러너는
          죽지 않고(폴링 루프의 시리즈별 `except`가 삼킴) 이후 청산은 정상 처리돼
          "진입 알림 없이 익절 알림만 온" 거래가 됐다. 이제 체결 이벤트가 실어 온
          `event.order`(= 그 주문 자신)를 쓴다.
        * **belt-and-suspenders**: 처분은 `try/finally`로 **반드시** 남기고(예외가 나면
          최소 사유를 붙여 `rejected`로), 알림 실패는 삼키지 않고 로그로 드러낸다. 그래야
          `filled` + `entry_status IS NULL`이 도입 이후에는 진짜 "쓰기 사이 유실"만
          가리키고(WAN-194의 고아 판정), 진입 알림 누락이 조용히 넘어가지 않는다.
        """
        fill = event.fill
        assert fill is not None
        journal_id = event.order.journal_id
        report = None
        disposition_done = False
        try:
            report = self._executor.enter(
                EntryIntent(
                    symbol=fill.symbol,
                    timeframe=fill.timeframe,
                    direction=fill.direction,
                    entry_price=fill.price,
                    entry_time=fill.time,
                    stop_price=fill.stop_price,
                    take_profit_price=fill.take_profit_price,
                ),
                now_ms=fill.time,
            )
            if journal_id is not None:
                self._journal.record_entry_result(
                    journal_id,
                    entered=report.accepted,
                    reason=report.outcome.reason,
                    reason_code=report.outcome.reason_code,
                )
            disposition_done = True
            # 거부는 WARNING이다 — 체결이 거래가 되지 않은 것이라 조용히 흘리면
            # 장부와 성과가 어긋난 채로 운영이 계속된다.
            log = _logger.info if report.accepted else _logger.warning
            log(
                "지정가 체결: %s %s %s @%.6g 관통=%.2fbp 대기=%s → 페이퍼 진입 %s",
                fill.symbol,
                fill.timeframe,
                format_kst_zoned(fill.time),
                fill.price,
                fill.penetration_bps,
                f"{fill.waited_ms}ms" if fill.waited_ms is not None else "?",
                "성공" if report.accepted else f"거부({report.outcome.reason})",
            )
            if self._notifier is not None:
                try:
                    self._notifier.handle_fill(fill, report)
                except Exception:  # noqa: BLE001 — 알림 실패가 처분·루프를 막지 않도록(로그로 드러낸다).
                    _logger.exception("진입 알림 실패: %s %s", fill.symbol, fill.timeframe)
        finally:
            if not disposition_done and journal_id is not None:
                # 진입 처리가 예외로 끊겼다 — 처분을 조용히 유실하지 않도록 남긴다(WAN-207).
                # 포지션이 실제로 열렸으면(enter 성공 후 기록 단계 실패) entered로, 그 전에
                # 끊겼으면 사유를 붙여 rejected로 남긴다. NULL로 두는 것보다 항상 낫다.
                entered = report is not None and report.accepted
                reason = (
                    ""
                    if entered
                    else (
                        report.outcome.reason
                        if report is not None
                        else "진입 처리 중 예외(처분 유실 방지, WAN-207)"
                    )
                )
                reason_code = report.outcome.reason_code if report is not None else ""
                try:
                    self._journal.record_entry_result(
                        journal_id, entered=entered, reason=reason, reason_code=reason_code
                    )
                except Exception:  # noqa: BLE001 — 폴백 처분마저 실패하면 로그만 남긴다.
                    _logger.exception("처분 기록 폴백 실패: journal_id=%d", journal_id)

    def _check_exits(
        self, symbol: str, timeframe: str, *, low: float, high: float, time_ms: int
    ) -> None:
        """오픈 페이퍼 포지션의 손절/익절을 이 1분봉 범위로 판정한다(백테스트와 같은 규칙:
        손절 우선 `stop_before_take_profit`, 청산가는 참조가 그대로)."""
        position = next(
            (
                p
                for p in self._executor.open_positions
                if p.symbol == symbol and p.timeframe == timeframe
            ),
            None,
        )
        if position is None:
            return
        is_long = position.direction is OrderBlockDirection.BULLISH
        stop = position.stop_price
        tp = position.take_profit_price
        stop_hit = stop is not None and (low <= stop if is_long else high >= stop)
        tp_hit = tp is not None and (high >= tp if is_long else low <= tp)
        if stop_hit and (not tp_hit or self._params.stop_before_take_profit):
            assert stop is not None
            self._exit(symbol, timeframe, stop, time_ms, SignalExitReason.STOP_LOSS)
        elif tp_hit:
            assert tp is not None
            self._exit(symbol, timeframe, tp, time_ms, SignalExitReason.TAKE_PROFIT)

    def _exit(
        self, symbol: str, timeframe: str, price: float, time_ms: int, reason: SignalExitReason
    ) -> None:
        report = self._executor.exit(
            symbol, timeframe, exit_price=price, exit_time=time_ms, reason=reason, now_ms=time_ms
        )
        _logger.info(
            "페이퍼 청산(%s): %s %s %s @%.6g %s",
            reason.value,
            symbol,
            timeframe,
            format_kst_zoned(time_ms),
            price,
            "정산" if report.accepted else f"거부({report.outcome.reason})",
        )
        if self._notifier is not None:
            self._notifier.handle_exit(report, exit_price=price, reason=reason, exit_time=time_ms)
        # 익절 후 존 내 재진입(WAN-273/274) — 청산이 실제로 성사됐을 때만. 익절이면 엔진이
        # 같은 존에 band 지정가를 다시 걸고(재진입 켜짐일 때), 손절이면 그 존을 끝내 재무장
        # 상태를 비운다. 재진입 규칙은 엔진이 설정에서 물려받은 채택 규약이다(러너가 자기
        # 버전을 새로 만들지 않는다). placed 이벤트는 예약 로그·장부·알림을 태운다.
        if report.accepted:
            self._handle_events(
                self._engine.on_position_exit(symbol, timeframe, reason=reason, time_ms=time_ms)
            )

    # -- 스냅샷 --------------------------------------------------------------

    def _paper_positions(self) -> list[PaperPosition]:
        return [
            PaperPosition(
                symbol=p.symbol,
                timeframe=p.timeframe,
                direction=p.direction,
                entry_time=p.entry_time,
                entry_price=p.entry_price,
                stop_price=p.stop_price,
                take_profit_price=p.take_profit_price,
            )
            for p in self._executor.open_positions
        ]

    def _pending_snapshots(self) -> list[PendingOrderSnapshot]:
        return [
            PendingOrderSnapshot(
                symbol=o.symbol,
                timeframe=o.timeframe,
                direction=o.direction,
                limit_price=o.last_limit_price,
                stop_price=o.stop_price,
                placed_ms=o.placed_ms,
                bars_elapsed=o.bars_elapsed,
                limit_valid_bars=o.limit_valid_bars,
                tap_index=o.tap_index,
            )
            for o in self._engine.book.open_orders
        ]

    @staticmethod
    def _store_htf_ms(timeframe: str) -> int:
        return timeframe_to_ms(timeframe)


def build_series(settings: Settings) -> list[Series]:
    """설정의 심볼×타임프레임 조합을 시리즈 목록으로 만든다."""
    return [
        (symbol, timeframe)
        for symbol in settings.live_signal_symbols
        for timeframe in settings.live_signal_timeframes
    ]


def run_zone_limit_runner(settings: Settings, *, once: bool = False) -> None:
    """존-지정가 페이퍼 러너를 실행한다(`live.runner`가 기본값에서 위임하는 진입점)."""
    from common.telegram import build_telegram_client
    from data.funding import FundingRateStore
    from execution.engine import build_execution_engine
    from execution.leverage import book_per_trade_sizing
    from paper.store import PaperTradeRecorder, PaperTradeStore

    store = OhlcvStore(settings.db_path)
    journal = OrderJournal(settings.db_path)
    paper_store = PaperTradeStore(settings.db_path)
    funding_store = FundingRateStore(settings.db_path) if settings.funding_enabled else None
    recorder = PaperTradeRecorder(
        paper_store, cost_model=settings.costs, funding_store=funding_store
    )
    # try 이전에 정의한다 — try 안에서 예외가 나도 finally의 정리가 미정의를 참조하지 않게.
    price_feed: PriceFeed | None = None
    # 레버리지 북(WAN-171 = WAN-45의 2단계): 칸=(종목,TF)마다 1포지션 · 여러 칸 동시 · 한
    # 지갑(공유 자본) · 배수 N. 기본값 = 채택 북(cap_only 5배, WAN-213). 엔진이 백테스트와
    # **같은** `resolve_book_sizing`으로 사이징하고, 거래당 리스크 금액(장부)은 북의 거래당
    # 사이징(cap_only=1배·combined=N배)으로 재야 라벨과 실제가 어긋나지 않는다.
    book = settings.live_leverage_book
    executor = PaperExecutor(
        engine=build_execution_engine(settings, leverage_book=book),
        store=paper_store,
        recorder=recorder,
        sizing=book_per_trade_sizing(settings.risk_sizing, book),
    )
    try:
        now = int(time.time() * 1000)
        # 재시작 정책: 이전 세션의 대기 주문은 복원하지 않고 폐기로 마감한다(모듈 독스트링).
        discarded = journal.discard_stale_pending(now_ms=now)
        if discarded:
            _logger.info("이전 세션 대기 주문 %d건 폐기(discarded_restart)", discarded)
        session_id = journal.start_session(now_ms=now)

        series = build_series(settings)
        # 매매 이벤트 알림(WAN-189, 페이퍼 한정). 텔레그램 미설정이면 build_telegram_client가
        # None을 주고 알림기는 드라이런(로그만)으로 돈다 — `notifier`의 기존 관행 그대로다.
        # 엔진보다 **먼저** 만든다 — 엔진의 스킵 훅이 알림기를 참조하기 때문이다(WAN-221).
        notifier: ZoneLimitNotifier | None = None
        if settings.paper_trade_notify_enabled:
            notifier = ZoneLimitNotifier(
                build_telegram_client(settings),
                events=frozenset(settings.paper_trade_notify_events),
                # 일일 요약의 체결률·사유 카운트는 DB 장부 창 조회로 낸다(재계산 아님, WAN-221).
                funnel_provider=lambda s, e: journal.funnel_counts(start_ms=s, end_ms=e),
            )

        # 존폭 필터 기각의 건별 알림(옵트인, WAN-221). deviation은 만료 이벤트에서 따로 넘기고,
        # cell_busy·retap은 no_fill처럼 시끄러워 넘기지 않는다(존폭만 전달).
        skip_listener: Callable[[str, str, str, int], None] | None = None
        if notifier is not None:

            def _forward_skip(reason: str, symbol: str, timeframe: str, time_ms: int) -> None:
                if reason == SKIP_REASON_ZONE_WIDTH:
                    assert notifier is not None
                    notifier.note_filter_skip(
                        reason, symbol=symbol, timeframe=timeframe, time_ms=time_ms
                    )

            skip_listener = _forward_skip

        # 재진입 규칙(WAN-273/274): 채택 값 "band"를 엔진이 물려받는다. "off"면 None(재진입
        # 없음). 백테스트 채택 북과 같은 규칙이라 러너가 자기 버전을 새로 만들지 않는다.
        reentry_rule: ReentryEntryRule | None = (
            None if settings.live_reentry_entry_rule == "off" else "band"
        )
        engine = ZoneLimitLiveEngine(
            params=settings.confluence,
            book=None,
            journal=journal,
            session_id=session_id,
            has_position=lambda s, t: any(
                p.symbol == s and p.timeframe == t for p in executor.open_positions
            ),
            skip_listener=skip_listener,
            reentry_entry_rule=reentry_rule,
        )
        # 옵트인 웹소켓 틱 피드(WAN-246, 페이퍼 한정 · 공개 체결 스트림만). 기본 꺼짐이면
        # None → 러너는 확정 1분봉만 소비(예전과 비트 동일). 켜면 심볼별 실시간 체결가로
        # 대기 주문 체결을 앞당긴다.
        if settings.live_tick_feed_enabled:
            from live.price_feed import CcxtProTickFeed

            symbols = sorted({sym for sym, _ in series})
            feed = CcxtProTickFeed(symbols, exchange_id=settings.live_tick_feed_exchange)
            feed.start()
            price_feed = feed
            _logger.info(
                "웹소켓 틱 피드 켜짐(WAN-246): %d 심볼 · %s (페이퍼 한정, 공개 체결 스트림)",
                len(symbols),
                settings.live_tick_feed_exchange,
            )

        runner = ZoneLimitPaperRunner(
            store=store,
            engine=engine,
            journal=journal,
            session_id=session_id,
            executor=executor,
            params=settings.confluence,
            series=series,
            lookback_bars=settings.live_signal_lookback_bars,
            poll_interval_seconds=settings.live_poll_interval_seconds,
            runtime_state=RuntimeStateStore(settings.live_runtime_state_path),
            notifier=notifier,
            price_feed=price_feed,
        )
        _logger.info(
            "존-지정가 페이퍼 러너 시작(WAN-45): %d 시리즈, 폴링 %ds, 세션 #%d"
            " · 레버리지 북 %s×%.4g (WAN-171, 공유 자본)"
            " (live_trading=%s — 페이퍼 한정, 체결률 실측이 목적)",
            len(series),
            settings.live_poll_interval_seconds,
            session_id,
            book.leverage_mode,
            book.leverage_multiple,
            settings.live_trading,
        )
        runner.run(max_polls=1 if once else None)
    finally:
        if price_feed is not None:
            price_feed.close()
        store.close()
        journal.close()
        paper_store.close()
        if funding_store is not None:
            funding_store.close()
