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
from common.timefmt import format_kst_zoned
from config.settings import Settings
from data.freshness import window_gap_summary
from data.storage import OhlcvStore
from execution.engine import EntryIntent
from live.executor import PaperExecutor
from live.limit_engine import EngineEvent, ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.paper import PaperPosition
from live.runtime_state import PendingOrderSnapshot, RuntimeStateStore
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
        now = self._now_ms()
        self._journal.heartbeat(self._session_id, now_ms=now)
        if self._runtime_state is not None:
            self._runtime_state.record(
                now_ms=now,
                open_positions=self._paper_positions(),
                new_events=[],
                pending_orders=self._pending_snapshots(),
            )
        return emitted

    def run(self, *, max_polls: int | None = None) -> None:
        """폴링 루프. `max_polls`가 None이면 무한 반복(테스트에서는 유한 지정)."""
        poll = 0
        while max_polls is None or poll < max_polls:
            self.poll_once()
            poll += 1
            if max_polls is not None and poll >= max_polls:
                break
            self._sleep(self._poll_interval)

    # -- 집행·청산 -----------------------------------------------------------

    def _handle_events(self, events: list[EngineEvent]) -> None:
        for event in events:
            if event.kind == "placed":
                order = event.order
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
                fill = event.fill
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
                _logger.info(
                    "지정가 체결: %s %s %s @%.6g 관통=%.2fbp 대기=%s → 페이퍼 진입 %s",
                    fill.symbol,
                    fill.timeframe,
                    format_kst_zoned(fill.time),
                    fill.price,
                    fill.penetration_bps,
                    f"{fill.waited_ms}ms" if fill.waited_ms is not None else "?",
                    "성공" if report.accepted else f"거부({report.outcome.reason})",
                )
            else:
                _logger.info(
                    "지정가 주문 종결: %s %s %s", event.symbol, event.timeframe, event.kind
                )

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
    from data.funding import FundingRateStore
    from execution.engine import build_execution_engine
    from paper.store import PaperTradeRecorder, PaperTradeStore

    store = OhlcvStore(settings.db_path)
    journal = OrderJournal(settings.db_path)
    paper_store = PaperTradeStore(settings.db_path)
    funding_store = FundingRateStore(settings.db_path) if settings.funding_enabled else None
    recorder = PaperTradeRecorder(
        paper_store, cost_model=settings.costs, funding_store=funding_store
    )
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper_store,
        recorder=recorder,
        sizing=settings.risk_sizing,
    )
    try:
        now = int(time.time() * 1000)
        # 재시작 정책: 이전 세션의 대기 주문은 복원하지 않고 폐기로 마감한다(모듈 독스트링).
        discarded = journal.discard_stale_pending(now_ms=now)
        if discarded:
            _logger.info("이전 세션 대기 주문 %d건 폐기(discarded_restart)", discarded)
        session_id = journal.start_session(now_ms=now)

        engine = ZoneLimitLiveEngine(
            params=settings.confluence,
            book=None,
            journal=journal,
            session_id=session_id,
            has_position=lambda s, t: any(
                p.symbol == s and p.timeframe == t for p in executor.open_positions
            ),
        )
        series = build_series(settings)
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
        )
        _logger.info(
            "존-지정가 페이퍼 러너 시작(WAN-45): %d 시리즈, 폴링 %ds, 세션 #%d"
            " (live_trading=%s — 페이퍼 한정, 체결률 실측이 목적)",
            len(series),
            settings.live_poll_interval_seconds,
            session_id,
            settings.live_trading,
        )
        runner.run(max_polls=1 if once else None)
    finally:
        store.close()
        journal.close()
        paper_store.close()
        if funding_store is not None:
            funding_store.close()
