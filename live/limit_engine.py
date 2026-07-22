"""존-지정가 라이브 엔진 — 오더블록 감시 · 주문 예약 · 체결/취소 판정 (WAN-45).

채택 진입 경로(B안: 존 근단 지정가 + 볼린저 재산정 + 오프셋 2bp, `entry_mode=
"zone_limit"`)를 실시간(페이퍼)에서 굴리는 순수 로직 계층이다. 러너
(`live.zone_limit_runner`)가 데이터를 공급하고, 이 엔진은 두 입력만 받는다:

* `on_htf_bars(...)` — 새 **확정 상위TF 봉** 창. 오더블록 탐지(`OrderBlockDetector`)를
  다시 돌려 존 대장을 갱신하고, 무효화된 존의 대기 주문을 취소한다.
* `on_substep(...)` — **확정 1분봉** 하나(= 백테스트 서브스텝과 같은 모양). 상위TF 봉
  경계를 넘으면 RSI·밴드 상태를 커밋하고(만료 계수 포함), 존 탭을 감지해 주문을
  예약하고, 대기 주문의 체결/취소를 판정한다.

## 백테스트와의 파리티 (완료 기준: 로직 이중화 금지)

틱의 해상도·판정 규칙이 `backtest.substep.simulate_zone_limit_trade`와 같다:
지정가 재산정은 `backtest.zone_limit_backtest.IntrabarLiveLimit`(**같은 객체** — 밴드 →
`deviation_entry_price` → `apply_zone_limit_offset` 사슬), 실시간 RSI는
`strategy.realtime_rsi.RealtimeRsi`, 터치 판정은 롱 `low <= 지정가`(1분봉 저가), 진입
시각·대기 시간은 벽시계가 아니라 **1분봉 시각**으로 잰다. 그래서 이 엔진의 체결/미체결은
같은 1분봉을 넣은 백테스트와 일치한다(`tests/test_limit_engine.py`가 동작으로 고정).

## 알려진 근사 (백테스트가 사후에 아는 것을 라이브는 모른다)

* **탭 봉 안의 예약 시점**: 백테스트는 "이 봉이 탭 봉이다"를 사후에 알고 탭 봉의 첫
  서브스텝부터 주문을 걸지만, 라이브는 가격이 실제로 존에 닿는 순간(전이) 예약한다.
  지정가가 존 근단보다 체결 쉬운 쪽(오프셋 2bp)일 때 그 틈(≤2bp)에서 먼저 난 체결은
  라이브가 놓칠 수 있다 — 오프셋 크기만큼의 희귀한 차이라 기록만 한다.
* **무효화 취소 시점**: 백테스트는 `break_time`(무효화 봉의 시작)에 취소하지만, 라이브는
  그 봉이 **닫혀 탐지기가 무효화를 확인한 뒤** 취소한다(한 봉 늦음 — 무효화 봉 안에서
  라이브만 체결이 날 수 있다).
* **지표 시딩 창**: 러너가 공급하는 창(`live_signal_lookback_bars`)에서 시딩하므로 전
  구간을 보는 백테스트와 RSI(무한 기억 RMA)가 미세하게 다를 수 있다 — `SignalRunner`가
  이미 갖는 성질과 같다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from backtest.sweep import timeframe_to_ms
from backtest.zone_limit_backtest import IntrabarLiveLimit
from live.limit_orders import LimitFill, LimitOrderBook, PendingLimitOrder
from live.order_journal import OrderJournal
from strategy.confluence import fixed_r_take_profit_price
from strategy.indicators import atr
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
)
from strategy.order_blocks import OrderBlockDetector
from strategy.realtime_band import RealtimeBand
from strategy.realtime_rsi import RealtimeRsi

_logger = logging.getLogger(__name__)

#: 공유 부품을 명시적으로 재수출한다 — `tests/test_limit_engine.py`가 "라이브가 백테스트와
#: **같은 객체**를 쓴다"를 `is`로 고정하는 데 쓰는 공개 표면이다(로직 이중화 금지).
__all__ = [
    "EngineEvent",
    "IntrabarLiveLimit",
    "RealtimeBand",
    "RealtimeRsi",
    "ZoneLimitLiveEngine",
    "fixed_r_take_profit_price",
]

#: (symbol, timeframe) 시리즈 키.
SeriesKey = tuple[str, str]

#: 존의 안정적 식별자. 탐지기를 창마다 다시 돌리므로 `OrderBlock` 인스턴스 동일성이나
#: 아카이브 인덱스로는 "같은 존"을 추적할 수 없다(창이 미끄러지면 인덱스가 밀린다).
#: (방향, start_time, confirmed_time)은 존 내용이 진화해도(탭·무효화 기록 추가) 불변이다.
ZoneId = tuple[str, int, int]

EventKind = Literal[
    "placed",
    "filled",
    "cancelled_expired",
    "cancelled_invalidated",
    "cancelled_condition_failed",
    "discarded",
]


def _zone_id(ob: OrderBlock) -> ZoneId:
    return (ob.direction.value, ob.start_time, ob.confirmed_time)


@dataclass(frozen=True)
class EngineEvent:
    """엔진이 러너에게 알리는 주문 생애 이벤트."""

    kind: EventKind
    symbol: str
    timeframe: str
    time_ms: int
    order: PendingLimitOrder
    fill: LimitFill | None = None


@dataclass
class _ZoneWatch:
    """감시 중인 활성 존 하나의 라이브 상태."""

    ob: OrderBlock
    was_inside: bool
    """마지막 **확정** 상위TF 봉이 존 범위와 겹쳤는지 — 탭은 바깥→안 전이만 센다
    (탐지기 `_invalidate`의 `_inside` 상태와 같은 규칙)."""
    armed_forming_bar: int | None = None
    """이번 형성 중 봉에서 이미 예약을 시도한 봉 시각(중복 예약 시도 방지)."""


@dataclass
class _SeriesState:
    """한 (symbol, timeframe) 시리즈의 엔진 상태."""

    htf_ms: int
    #: 확정 상위TF 봉 시각·종가(지표 시딩용). 저장소 갱신 + 서브스텝 경계 넘김으로 유지.
    seed_times: list[int] = field(default_factory=list)
    seed_closes: list[float] = field(default_factory=list)
    zones: dict[ZoneId, _ZoneWatch] = field(default_factory=dict)
    #: 존폭 필터(WAN-159)가 읽는 "탭 봉 직전 확정봉"의 ATR. 필터가 꺼져 있으면 None.
    last_closed_atr: float | None = None
    #: 현재 형성 중인 상위TF 봉 시각과 그 누적 저가/고가(탭 전이 감지용).
    forming_bar: int | None = None
    forming_low: float | None = None
    forming_high: float | None = None
    #: 직전 서브스텝 종가(경계 커밋·`intrabar_causal` 지연선 시딩용).
    running_close: float | None = None
    last_substep_time: int | None = None
    #: 대기 주문이 어느 존의 것인지(무효화 취소 매칭용).
    pending_zone: ZoneId | None = None
    pending_journal_id: int | None = None
    refreshed: bool = False
    """`on_htf_bars`가 최소 한 번 존 대장을 세웠는지 — 그 전에는 예약하지 않는다."""


class ZoneLimitLiveEngine:
    """존-지정가 라이브 엔진(순수 로직 — I/O는 러너가 담당).

    `has_position`은 시리즈에 오픈 포지션이 있는지 알려 주는 콜백이다(단일 포지션
    규칙: 대기 주문 또는 오픈 포지션이 슬롯 하나를 차지한다). 엔진은 실행 계층을
    모르므로 러너가 `PaperExecutor` 장부를 보고 답한다.
    """

    def __init__(
        self,
        *,
        params: ConfluenceParams,
        order_block_params: OrderBlockParams | None = None,
        book: LimitOrderBook | None = None,
        journal: OrderJournal | None = None,
        session_id: int | None = None,
        has_position: Callable[[str, str], bool] | None = None,
    ) -> None:
        _validate_live_params(params)
        self._params = params
        self._ob_params = order_block_params
        self.book = book if book is not None else LimitOrderBook()
        self._journal = journal
        self._session_id = session_id
        self._has_position: Callable[[str, str], bool] = (
            has_position if has_position is not None else (lambda _s, _t: False)
        )
        self._series: dict[SeriesKey, _SeriesState] = {}

    # -- 상위TF 확정봉: 존 대장 갱신 -----------------------------------------

    def on_htf_bars(self, symbol: str, timeframe: str, df: pd.DataFrame) -> list[EngineEvent]:
        """새 확정 상위TF 봉 창으로 존 대장을 갱신한다.

        `df`는 시간 오름차순 **확정봉만** 담은 창이다(러너가 갭 검사를 마친 값 —
        구멍이 있으면 볼린저·RSI가 조용히 틀린 값을 내고 그 값이 곧 주문 가격이다,
        WAN-156 §3). 탐지를 다시 돌려 활성 존을 세우고, 무효화된 존의 대기 주문을
        취소하며, 지표 시딩용 확정 종가를 저장소 기준으로 다시 맞춘다.
        """
        state = self._state(symbol, timeframe)
        if df.empty:
            return []
        times = [int(t) for t in df["open_time"].astype("int64").tolist()]
        closes = [float(v) for v in df["close"].astype(float).tolist()]
        state.seed_times = times
        state.seed_closes = closes

        ob_result = OrderBlockDetector(self._ob_params).run(df)

        # 존폭 필터(WAN-159)의 ATR: "탭 봉 직전 확정봉" = 마지막 확정봉. 탭 봉(형성 중
        # 봉)의 ATR은 그 봉 종가를 알아야 나오므로 룩어헤드다.
        if self._params.max_zone_width_atr is not None:
            atr_series = atr(df, length=self._params.zone_width_atr_length)
            last_atr = float(atr_series.iloc[-1])
            state.last_closed_atr = last_atr
        else:
            state.last_closed_atr = None

        last_low = float(df["low"].astype(float).iloc[-1])
        last_high = float(df["high"].astype(float).iloc[-1])

        events: list[EngineEvent] = []
        now_ms = times[-1] + state.htf_ms  # 마지막 확정봉의 닫힌 시각.
        fresh: dict[ZoneId, _ZoneWatch] = {}
        for ob in ob_result.order_blocks:
            if ob.break_time is not None:
                continue  # 무효화된 존 — 아래에서 대기 주문 취소로만 반영.
            if not ob.alive_at(times[-1]):
                continue
            is_long = ob.direction is OrderBlockDirection.BULLISH
            if not is_long and not self._params.short_enabled:
                continue  # WAN-87: 숏 비활성(기본).
            zid = _zone_id(ob)
            was_inside = last_low <= ob.top and last_high >= ob.bottom
            watch = state.zones.get(zid)
            if watch is None:
                fresh[zid] = _ZoneWatch(ob=ob, was_inside=was_inside)
            else:
                # 존 내용(탭 기록 등)은 갱신하되 형성 중 봉의 예약 시도 기록은 유지.
                watch.ob = ob
                watch.was_inside = was_inside
                fresh[zid] = watch
        state.zones = fresh
        state.refreshed = True

        # 대기 주문의 존이 무효화됐거나 대장에서 사라졌으면 취소한다. 백테스트는
        # `break_time`(무효화 봉 시작)에 취소하지만 라이브는 그 봉이 닫혀 탐지가 확인된
        # 지금이다 — 한 봉 늦는 알려진 근사(모듈 독스트링).
        if state.pending_zone is not None and state.pending_zone not in state.zones:
            cancelled = self.book.cancel_invalidated(symbol, timeframe)
            if cancelled is not None:
                self._record_cancel(state, cancelled, now_ms=now_ms)
                events.append(
                    EngineEvent(
                        kind="cancelled_invalidated",
                        symbol=symbol,
                        timeframe=timeframe,
                        time_ms=now_ms,
                        order=cancelled,
                    )
                )
            state.pending_zone = None
            state.pending_journal_id = None
        return events

    # -- 1분봉 서브스텝: 커밋·예약·체결 --------------------------------------

    def on_substep(
        self, symbol: str, timeframe: str, *, time_ms: int, low: float, high: float, close: float
    ) -> list[EngineEvent]:
        """확정 1분봉 하나를 반영한다(백테스트 서브스텝과 같은 판정 순서).

        1. 상위TF 봉 경계를 넘으면 직전 봉을 `running_close`로 커밋(만료 계수 포함).
        2. 활성 존의 탭(바깥→안 전이)을 감지해 주문을 예약한다.
        3. 대기 주문에 틱을 반영해 체결/취소를 판정한다.
        """
        state = self._state(symbol, timeframe)
        events: list[EngineEvent] = []
        htf_bar = (time_ms // state.htf_ms) * state.htf_ms

        # 1) 상위TF 봉 경계 — 시뮬레이터의 커밋 규칙 그대로: 직전 봉의 마지막 1분봉
        # 종가가 그 봉의 확정 종가다.
        if state.forming_bar is not None and htf_bar != state.forming_bar:
            if state.running_close is not None:
                gap_bars = (htf_bar - state.forming_bar) // state.htf_ms
                if gap_bars > 1:
                    # 1분봉이 상위TF 봉 하나를 통째로 건너뛰었다 — 죽어 있던 구간의
                    # 지표 상태를 지어낼 수 없으므로 대기 주문을 폐기한다(측정 오염 방지,
                    # 재시작 폐기와 같은 부류). 존 대장·시딩은 다음 `on_htf_bars`가 맞춘다.
                    events.extend(self._discard_pending(state, symbol, timeframe, time_ms))
                else:
                    pending = self.book.pending(symbol, timeframe)
                    status = self.book.on_bar_close(symbol, timeframe, state.running_close)
                    self._append_seed(state, state.forming_bar, state.running_close)
                    if status is not None and pending is not None:
                        # 만료(현재 경계 커밋의 유일한 종결 상태) — 장부에서 이미 제거됨.
                        journal_id = self._take_pending_meta(state)
                        if journal_id is not None and self._journal is not None:
                            self._journal.record_cancelled(journal_id, status, now_ms=time_ms)
                        events.append(
                            EngineEvent(
                                kind="cancelled_expired",
                                symbol=symbol,
                                timeframe=timeframe,
                                time_ms=time_ms,
                                order=pending,
                            )
                        )
                    elif pending is not None and self._journal is not None:
                        if state.pending_journal_id is not None:
                            self._journal.record_progress(state.pending_journal_id, pending)
            # 방금 닫힌 형성 봉의 누적 범위로 존 포함 상태를 갱신한다 — 다음
            # `on_htf_bars`(저장소 폴링)를 기다리면 새 봉의 틱이 낡은 `was_inside`로
            # 판정돼, 존 안에 머무는 연속 봉을 새 탭으로 오인한다(전이 규칙 위반).
            if state.forming_low is not None and state.forming_high is not None:
                low_done, high_done = state.forming_low, state.forming_high
                for watch in state.zones.values():
                    watch.was_inside = low_done <= watch.ob.top and high_done >= watch.ob.bottom
            for watch in state.zones.values():
                watch.armed_forming_bar = None
            state.forming_bar = htf_bar
            state.forming_low = None
            state.forming_high = None
        elif state.forming_bar is None:
            state.forming_bar = htf_bar

        state.forming_low = low if state.forming_low is None else min(state.forming_low, low)
        state.forming_high = high if state.forming_high is None else max(state.forming_high, high)

        # 2) 탭 감지 → 예약. 체결 판정보다 먼저다 — 백테스트는 탭 봉의 첫 서브스텝부터
        # 주문이 걸려 있으므로, 전이 서브스텝에서 예약과 체결이 같은 스텝에 일어날 수 있다.
        events.extend(self._maybe_arm(state, symbol, timeframe, time_ms))

        # 3) 대기 주문 체결/취소 판정.
        order = self.book.pending(symbol, timeframe)
        if order is not None:
            fill = self.book.on_price(symbol, timeframe, low, high, close, now_ms=time_ms)
            if fill is not None:
                journal_id = state.pending_journal_id
                if journal_id is not None and self._journal is not None:
                    self._journal.record_filled(journal_id, fill)
                state.pending_zone = None
                state.pending_journal_id = None
                events.append(
                    EngineEvent(
                        kind="filled",
                        symbol=symbol,
                        timeframe=timeframe,
                        time_ms=time_ms,
                        order=order,
                        fill=fill,
                    )
                )
            elif order.status.is_terminal:
                # 체결 없이 종결 = 조건 미충족 취소(옵트인 게이트) 또는 live 밴드의
                # 청산 규칙 불가(WAN-143 대응 경로).
                journal_id = self._take_pending_meta(state)
                if journal_id is not None and self._journal is not None:
                    self._journal.record_cancelled(journal_id, order.status, now_ms=time_ms)
                events.append(
                    EngineEvent(
                        kind="cancelled_condition_failed",
                        symbol=symbol,
                        timeframe=timeframe,
                        time_ms=time_ms,
                        order=order,
                    )
                )

        state.running_close = close
        state.last_substep_time = time_ms
        return events

    # -- 내부 ----------------------------------------------------------------

    def _state(self, symbol: str, timeframe: str) -> _SeriesState:
        key = (symbol, timeframe)
        state = self._series.get(key)
        if state is None:
            state = _SeriesState(htf_ms=timeframe_to_ms(timeframe))
            self._series[key] = state
        return state

    @staticmethod
    def _append_seed(state: _SeriesState, bar_time: int, close: float) -> None:
        """경계를 넘어 확정된 봉을 시딩 시퀀스에 잇는다(저장소 갱신 전의 공백 방지)."""
        if state.seed_times and state.seed_times[-1] >= bar_time:
            return  # 저장소 갱신이 이미 반영했다.
        state.seed_times.append(bar_time)
        state.seed_closes.append(close)

    def _take_pending_meta(self, state: _SeriesState) -> int | None:
        journal_id = state.pending_journal_id
        state.pending_zone = None
        state.pending_journal_id = None
        return journal_id

    def _record_cancel(self, state: _SeriesState, order: PendingLimitOrder, *, now_ms: int) -> None:
        if state.pending_journal_id is not None and self._journal is not None:
            self._journal.record_cancelled(state.pending_journal_id, order.status, now_ms=now_ms)

    def _discard_pending(
        self, state: _SeriesState, symbol: str, timeframe: str, time_ms: int
    ) -> list[EngineEvent]:
        order = self.book.cancel_invalidated(symbol, timeframe)
        if order is None:
            state.pending_zone = None
            state.pending_journal_id = None
            return []
        journal_id = self._take_pending_meta(state)
        if journal_id is not None and self._journal is not None:
            # 데이터 구멍 폐기는 무효화가 아니라 측정 무효다 — 재시작 폐기와 같은 상태로
            # 남겨 체결률 분모에서 뺀다.
            self._journal.record_discarded(journal_id, now_ms=time_ms)
        _logger.warning(
            "%s %s: 1분봉 공백(상위TF 봉 건너뜀) — 대기 주문 폐기(측정 오염 방지)",
            symbol,
            timeframe,
        )
        return [
            EngineEvent(
                kind="discarded", symbol=symbol, timeframe=timeframe, time_ms=time_ms, order=order
            )
        ]

    def _maybe_arm(
        self, state: _SeriesState, symbol: str, timeframe: str, time_ms: int
    ) -> list[EngineEvent]:
        """활성 존의 탭(바깥→안 전이)을 감지해 대기 지정가 주문을 예약한다."""
        if not state.refreshed or state.forming_bar is None:
            return []
        if self.book.pending(symbol, timeframe) is not None:
            return []
        if self._has_position(symbol, timeframe):
            return []  # 단일 포지션 규칙: 슬롯이 차 있으면 새 주문을 걸지 않는다.
        low = state.forming_low
        high = state.forming_high
        if low is None or high is None:
            return []

        params = self._params
        for watch in state.zones.values():
            ob = watch.ob
            if watch.was_inside or watch.armed_forming_bar == state.forming_bar:
                continue  # 탭은 바깥→안 전이만(연속 체류는 첫 진입만) — 탐지기와 같은 규칙.
            if state.forming_bar <= ob.confirmed_time:
                continue  # 확정 봉 자신은 탭이 될 수 없다(탐지기 `times[t] > confirmed_time`).
            is_inside = low <= ob.top and high >= ob.bottom
            if not is_inside:
                continue
            watch.armed_forming_bar = state.forming_bar

            tap_index = len(ob.tapped_times)
            if params.retap_mode == "once" and tap_index > 0:
                continue
            if not params.zone_width_filter_passes(ob, state.last_closed_atr):
                continue  # WAN-159: 넓은 존은 주문을 걸지 않는다(판정 불가도 기각).

            order = self._build_order(state, symbol, timeframe, ob, tap_index, time_ms)
            if order is None:
                continue
            placed = self.book.place(order)
            if placed is None:
                continue
            zid = _zone_id(ob)
            state.pending_zone = zid
            if self._journal is not None and self._session_id is not None:
                order.journal_id = self._journal.record_placed(
                    order,
                    session_id=self._session_id,
                    zone_start_time=ob.start_time,
                    zone_confirmed_time=ob.confirmed_time,
                )
                state.pending_journal_id = order.journal_id
            return [
                EngineEvent(
                    kind="placed", symbol=symbol, timeframe=timeframe, time_ms=time_ms, order=order
                )
            ]
        return []

    def _build_order(
        self,
        state: _SeriesState,
        symbol: str,
        timeframe: str,
        ob: OrderBlock,
        tap_index: int,
        time_ms: int,
    ) -> PendingLimitOrder | None:
        params = self._params
        is_long = ob.direction is OrderBlockDirection.BULLISH
        stop_price = ob.bottom if is_long else ob.top
        rsi_state = RealtimeRsi.seed_from_closed(state.seed_closes, params.rsi_length)

        deviation = params.deviation_filter
        live_limit: IntrabarLiveLimit | None = None
        static_price: float | None = None
        static_tp: float | None = None
        if deviation is not None:
            # 채택 경로(WAN-132): 밴드가 봉 안에서 움직이므로 지정가를 매 틱 재산정한다.
            # **백테스트와 같은 공급자 객체**라 가격 사슬이 두 경로에서 갈라질 수 없다.
            causal = deviation.band_bar == "intrabar_causal"
            live_limit = IntrabarLiveLimit(
                band=RealtimeBand.seed_from_closed(state.seed_closes, deviation),
                order_block=ob,
                is_long=is_long,
                params=params,
                stop_price=stop_price,
                lines=[],
                trigger_time=time_ms,
                pending_price=state.running_close if causal else None,
                causal=causal,
            )
        else:
            # 볼린저 없음: 존 근단 + 오프셋의 상수 지정가(예약 시점 확정).
            price = params.apply_zone_limit_offset(params.zone_limit_price(ob), is_long=is_long)
            risk = price - stop_price if is_long else stop_price - price
            if risk <= 0:
                return None
            static_price = price
            static_tp = fixed_r_take_profit_price(ob.direction, price, ob, params.take_profit_r)

        first_tap_free = params.rsi_gate_mode == "first_tap_free" and tap_index == 0
        return PendingLimitOrder(
            symbol=symbol,
            timeframe=timeframe,
            direction=ob.direction,
            stop_price=stop_price,
            rsi_state=rsi_state,
            limit_price=static_price,
            take_profit_price=static_tp,
            live_limit=live_limit,
            rsi_gate_mode=params.rsi_gate_mode,
            first_tap_free=first_tap_free,
            rsi_oversold=params.rsi_oversold,
            rsi_overbought=params.rsi_overbought,
            limit_valid_bars=params.limit_valid_bars,
            cancel_on_condition_fail=params.cancel_limit_on_condition_fail,
            placed_ms=time_ms,
            tap_index=tap_index,
        )


def _validate_live_params(params: ConfluenceParams) -> None:
    """라이브 엔진이 재현할 수 없는 설정을 **조용히 무시하지 않고 거부**한다.

    라벨만 붙고 실행이 다른 것(WAN-95의 교훈)을 막는 가드다 — 지원 범위는 채택
    기본값(`ConfluenceParams()`) 전부와, 라이브에서 의미가 같은 옵트인 일부다.
    """
    if params.entry_mode != "zone_limit":
        raise ValueError(
            f"존-지정가 라이브 엔진은 entry_mode='zone_limit' 전용입니다: {params.entry_mode!r}"
            " — 종가 진입(A안)은 기존 SignalRunner가 담당합니다."
        )
    deviation = params.deviation_filter
    if deviation is not None and deviation.band_bar in ("tap", "prev_closed"):
        raise ValueError(
            f"band_bar={deviation.band_bar!r}는 라이브에서 재현할 수 없습니다 — 'tap'은 탭 봉"
            " 종가(미래)를 요구하는 룩어헤드고, 'prev_closed'는 배선하지 않았습니다"
            " (채택 기본값은 'intrabar_live', WAN-132)."
        )
    if params.take_profit_mode != "fixed_r":
        raise ValueError(
            f"라이브 엔진은 take_profit_mode='fixed_r'만 지원합니다: {params.take_profit_mode!r}"
        )
    if params.min_rr is not None:
        raise ValueError("라이브 엔진은 min_rr 게이트를 지원하지 않습니다(채택 기본값 None).")
    if params.long_max_deviation is not None:
        raise ValueError(
            "라이브 엔진은 long_max_deviation 게이트를 배선하지 않았습니다(채택 기본값 None)."
        )
    if params.fill_penetration_bps != 0.0 or params.fill_dropout_rate != 0.0:
        raise ValueError(
            "체결 보수화 렌즈(fill_penetration_bps/fill_dropout_rate)는 백테스트 민감도"
            " 전용입니다 — 라이브는 실제 체결을 재는 쪽이라 적용할 수 없습니다."
        )
