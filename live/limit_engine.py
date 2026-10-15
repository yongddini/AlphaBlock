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

import bisect
import enum
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from backtest.sweep import timeframe_to_ms
from backtest.wan228_reentry_census import ReentryEntryRule
from backtest.zone_limit_backtest import IntrabarLiveLimit
from live.engine_state import PersistedPendingOrder, PersistedZoneWatch, SeriesStateSnapshot
from live.limit_orders import LimitFill, LimitOrderBook, LimitOrderStatus, PendingLimitOrder
from live.order_journal import (
    ORDER_ORIGIN_REENTRY,
    ORDER_ORIGIN_TAP,
    SKIP_REASON_CELL_BUSY,
    SKIP_REASON_RETAP,
    SKIP_REASON_ZONE_WIDTH,
    OrderJournal,
)
from strategy.confluence import fixed_r_take_profit_price
from strategy.indicators import atr
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    SignalExitReason,
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
    "ReentryEntryRule",
    "ZoneLimitLiveEngine",
    "fixed_r_take_profit_price",
]

#: (symbol, timeframe) 시리즈 키.
SeriesKey = tuple[str, str]

#: 오래된 존 기억(WAN-451): 시작이 창 앞쪽 이만큼 안이면 탐지에서 빠져도 이어 든다(스윙 맥락이
#: 잘려 탐지가 달라지는 창 시작 근처 — WAN-343 실측 +5~25봉).
_CARRY_MARGIN_BARS = 50

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
    "shadow_filled",
]


class _Unset(enum.Enum):
    """`_build_order`의 `limit_valid_bars` 미지정 센티넬 — `None`(무기한)과 "채택 기본값을
    쓴다"를 가른다(`--max-zone-width-atr` none vs 미지정과 같은 규약, WAN-159)."""

    token = 0


_UNSET = _Unset.token

#: 벽시계 만료 백스톱의 유예(ms, WAN-298). 정상 운영에서는 서브스텝 경로(1분봉 경계 커밋)가
#: 기한 도달 후 「1분봉 확정 + 저장 + 폴링(기본 60초)」 안에 먼저 만료시키므로, 백스톱은 그
#: 지연을 넉넉히 넘긴 뒤에만 발동해 정상 경로와 경합하지 않는다. 유예를 지나서도 대기 중인
#: 주문은 데이터가 멈춰 봉 계수(`bars_elapsed`)가 얼어 있다는 뜻이다.
_EXPIRY_GRACE_MS = 5 * 60_000


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
    zone: ZoneId | None = None
    """주문의 존(체결·그림자 체결만 채운다) — 몰림 규칙이 「같은 탭의 쌍둥이」를 빼는 데 쓴다."""


@dataclass
class _ZoneWatch:
    """감시 중인 활성 존 하나의 라이브 상태."""

    ob: OrderBlock
    was_inside: bool
    """마지막 **확정** 상위TF 봉이 존 범위와 겹쳤는지 — 탭은 바깥→안 전이만 센다
    (탐지기 `_invalidate`의 `_inside` 상태와 같은 규칙)."""
    armed_forming_bar: int | None = None
    """이번 형성 중 봉에서 이미 예약을 시도한 봉 시각(중복 예약 시도 방지)."""
    shadow_bar: int | None = None
    """이번 형성 중 봉에서 이미 그림자 주문을 만든 봉 시각(WAN-451 몰림 규칙 — 탭 하나에 하나)."""
    carried: bool = False
    """탐지 창 밖으로 밀려나 엔진이 직접 무효화·탭을 따라가는 존(WAN-451 · `carry_zones`)."""
    bar_inside: bool = False
    """마지막으로 반영한 **확정 봉**이 존과 겹쳤는지 — 탐지기 `_inside`와 같은 뜻(확정 봉 자신은
    거짓). `was_inside`는 봉 경계에서 형성 봉 범위로 미리 바뀌므로 기억하는 존의 탭 판정에는
    이 값을 따로 둔다."""
    checked_through: int | None = None
    """존 상태(무효화·탭)가 반영된 마지막 확정 봉 시각."""
    skip_recorded_forming_bar: int | None = None
    """이번 형성 중 봉에서 이미 미진입 사유를 장부에 남긴 봉 시각(WAN-217). `_maybe_arm`이
    매 서브스텝 재평가하므로 같은 봉·같은 존의 사유가 여러 번 찍히지 않게 한다. `armed`와
    분리한 이유: 슬롯 점유(cell_busy) 사유는 armed를 설정하면 안 된다(같은 봉 뒤에 슬롯이
    비면 예약이 살아나야 파리티가 유지된다) — 그래서 중복 방지용 별도 표식이 필요하다."""


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
    #: 현재 오픈 포지션이 어느 존의 것인지 — 익절 후 재진입(WAN-274)이 같은 존에 band
    #: 지정가를 다시 걸 때 읽는다. 체결 순간 `pending_zone`에서 넘겨받고, 청산(익절 재무장
    #: 또는 손절 종료)에서 비운다. 재진입이 꺼져 있어도 채워지지만(무해) 읽히지 않는다.
    occupied_zone: ZoneId | None = None
    refreshed: bool = False
    """`on_htf_bars`가 최소 한 번 존 대장을 세웠는지 — 그 전에는 예약하지 않는다."""
    #: 그림자 주문(WAN-451 ③ 몰림 규칙) — (존, 주문). 슬롯과 무관하게 탭마다 하나씩 따라간다.
    shadows: list[tuple[ZoneId, PendingLimitOrder]] = field(default_factory=list)


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
        skip_listener: Callable[[str, str, str, int], None] | None = None,
        reentry_entry_rule: ReentryEntryRule | None = None,
        track_shadows: bool = False,
        carry_zones: bool = False,
    ) -> None:
        _validate_live_params(params)
        _validate_reentry_rule(params, reentry_entry_rule)
        self._params = params
        self._ob_params = order_block_params
        #: 익절 후 존 내 재진입(WAN-273/274) 규칙. None이면 재진입 없음(옛 동작, 비트 동일).
        #: 채택 값은 "band"(봉내 라이브 밴드 재산정 = 엔진 본 진입과 같은 사슬). 러너가
        #: 설정에서 이 값을 정하고, 백테스트 채택 북(`reentry_entry_rule="band"`)과 같은
        #: 규칙을 물려받는다 — 러너가 자기 버전을 새로 만들지 않는다(WAN-273 규약).
        self._reentry_entry_rule = reentry_entry_rule
        self.book = book if book is not None else LimitOrderBook()
        self._journal = journal
        self._session_id = session_id
        self._has_position: Callable[[str, str], bool] = (
            has_position if has_position is not None else (lambda _s, _t: False)
        )
        #: 미진입 사유 실시간 알림 훅(reason, symbol, timeframe, time_ms) — 옵트인 건별
        #: 텔레그램용(WAN-221). None이면 부수효과 없음(이벤트 스트림·저널 기록 불변).
        #: 장부에 사유가 실제로 남을 때만(봉당 1회) 호출돼 텔레그램 카운트와 저널이 어긋나지
        #: 않는다. ⚠️ 이벤트를 추가로 내지 **않는다** — 스킵의 `events == []` 파리티 보존.
        self._skip_listener = skip_listener
        #: 그림자 주문(WAN-451 ③). 팔의 몰림 규칙은 「우리가 들어갔는지와 무관하게」 팔 후보
        #: **전부**의 가상 체결을 센다(백테스트 `build_zone_limit_candidates`는 탭마다 주문을
        #: 독립으로 시뮬레이션한다). 이 엔진은 칸마다 주문 하나뿐이라(슬롯) 칸이 차 있을 때의
        #: 탭·겹친 탭을 못 본다. 켜면 첫 탭마다 **같은 `_build_order`·같은 판정**의 독립 주문을
        #: 하나씩 더 따라가 체결되면 `shadow_filled`를 낸다(장부·슬롯과 무관). 끄면 비트 동일.
        self._track_shadows = track_shadows
        #: 오래된 존 기억(WAN-451). 탐지 창(`live_signal_lookback_bars`)이 미끄러져 존의 시작이 창
        #: 밖으로 나가면 탐지기는 그 존을 다시 못 만든다 — 정본(전 이력 탐지)은 몇 년 된 존에도
        #: 들어가는데(팔 후보의 약 7%가 1500봉보다 오래된 존) 라이브는 잊어버렸다. 켜면 창 밖으로
        #: 밀려난 살아 있는 존을 엔진이 들고 **탐지기와 같은 규칙**(무효화 · 바깥→안 탭)으로 새 확정
        #: 봉마다 갱신한다. 끄면 비트 동일.
        self._carry_zones = carry_zones
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
        detected: set[ZoneId] = {_zone_id(ob) for ob in ob_result.order_blocks}
        for ob in ob_result.order_blocks:
            if ob.break_time is not None:
                continue  # 무효화된 존 — 아래에서 대기 주문 취소로만 반영.
            if not ob.alive_at(times[-1]):
                continue
            is_long = ob.direction is OrderBlockDirection.BULLISH
            if not is_long and not self._params.short_enabled:
                continue  # WAN-87: 숏 비활성(기본).
            zid = _zone_id(ob)
            # 탐지기는 존이 확정되는 봉을 탭 판정에 쓰지 않는다(그 봉에서 `_inside`를 세우지
            # 않으므로 다음 봉의 겹침이 첫 탭이다). 방금 확정된 존(확정 봉 = 마지막 확정봉)을
            # 「이미 안」으로 시작하면 첫 탭을 놓친다 — 실데이터 대조에서 드러났다(WAN-451).
            was_inside = (
                times[-1] > ob.confirmed_time and last_low <= ob.top and last_high >= ob.bottom
            )
            watch = state.zones.get(zid)
            if watch is None:
                watch = _ZoneWatch(ob=ob, was_inside=was_inside)
            else:
                # 존 내용(탭 기록 등)은 갱신하되 형성 중 봉의 예약 시도 기록은 유지.
                watch.ob = ob
                watch.was_inside = was_inside
            watch.carried = False
            watch.bar_inside = was_inside
            watch.checked_through = times[-1]
            fresh[zid] = watch
        if self._carry_zones:
            self._carry_old_zones(state, df, times, detected, fresh)
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
        if state.shadows:
            # 그림자도 같은 규칙으로 무효화 취소한다(존이 대장에서 사라지면 끝).
            state.shadows = [(z, o) for z, o in state.shadows if z in state.zones]
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
                    state.shadows = []  # 그림자도 같은 이유로 버린다
                else:
                    for _zone, shadow in state.shadows:
                        shadow.on_bar_close(state.running_close)  # 만료 계수(진짜 주문과 같은 규칙)
                    state.shadows = [(z, o) for z, o in state.shadows if not o.status.is_terminal]
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
                    # 확정 봉 자신은 탭 판정에 안 쓴다(`on_htf_bars`와 같은 규칙, WAN-451).
                    watch.was_inside = (
                        state.forming_bar > watch.ob.confirmed_time
                        and low_done <= watch.ob.top
                        and high_done >= watch.ob.bottom
                    )
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
        if self._track_shadows:
            self._arm_shadows(state, symbol, timeframe, time_ms)
        events.extend(self._maybe_arm(state, symbol, timeframe, time_ms))

        # 3) 대기 주문 체결/취소 판정(틱 경로와 공용 — 로직 이중화 금지).
        events.extend(
            self._apply_pending_price(
                state, symbol, timeframe, low=low, high=high, close=close, time_ms=time_ms
            )
        )
        if state.shadows:
            events.extend(
                self._apply_shadow_price(
                    state, symbol, timeframe, low=low, high=high, close=close, time_ms=time_ms
                )
            )

        state.running_close = close
        state.last_substep_time = time_ms
        return events

    def on_tick(
        self, symbol: str, timeframe: str, *, price: float, time_ms: int
    ) -> list[EngineEvent]:
        """웹소켓 실시간 체결가 하나를 대기 주문에 반영한다(WAN-246, 체결 감지 전용).

        예약(arming)·상위TF 존 커밋·만료 계수는 하지 **않는다** — 그 셋은 확정 1분봉 경로
        (`on_substep`/`on_htf_bars`)가 안정적으로 담당하고, 이 훅은 *이미 걸린* 주문의
        체결을 폴링 간격보다 빨리 잡을 뿐이다. 단일 체결가라 백테스트 서브스텝과 같은
        `on_price` 계약대로 low=high=close=price로 넣는다. 대기 주문이 없으면 상태를
        만들지 않고 빈 목록을 반환한다(부수효과 없음).

        ⚠️ 틱 경로는 백테스트(1분봉)보다 미세하다 — `intrabar_live` 밴드가 봉 안에서 표본을
        현재가로 다시 잡아 체결 순간의 지정가가 1분봉 종가로 잰 값과 갈릴 수 있다
        (`docs/decisions/wan246.md` §2/§3, 판정 (b): 백테스트는 1분봉 유지 · 라이브 틱은
        옵트인으로 더 미세함을 명시).
        """
        if self.book.pending(symbol, timeframe) is None:
            return []
        state = self._state(symbol, timeframe)
        return self._apply_pending_price(
            state, symbol, timeframe, low=price, high=price, close=price, time_ms=time_ms
        )

    def expire_overdue(self, *, now_ms: int) -> list[EngineEvent]:
        """만료 기한을 넘긴 대기 주문을 벽시계로 만료시킨다(WAN-298 백스톱).

        봉 계수 만료(`on_substep`의 경계 커밋)는 **소비된 1분봉**으로만 전진하므로, 수집이
        멈추면(새 1분봉 없음 → 서브스텝 없음) 주문이 기한을 넘겨도 만료되지 않고 슬롯을
        무기한 점유했다 — WAN-295에서 발견된 라이브↔백테 진입 괴리의 원인(DOGE 15m 47시간).
        이 백스톱은 러너가 매 폴링에 벽시계로 기한(`expiry_deadline_ms` — 서브스텝 경로와
        같은 시각)을 검사해, 유예(`_EXPIRY_GRACE_MS`)를 지나서도 대기 중인 주문을
        `cancelled_expired`로 정리하고 슬롯을 비운다.

        * **정상 경로와 경합하지 않는다** — 데이터가 흐르면 서브스텝 경로가 기한 도달 직후
          (1분봉 확정 + 폴링 지연) 먼저 만료시키고, 백스톱은 유예 뒤에만 발동한다.
        * **무기한 주문(재진입 재무장, `limit_valid_bars=None`)은 건드리지 않는다.**
        * 장부 종결 시각은 벽시계가 아니라 **기한 시각**이다 — 주문이 유효하기를 멈춘
          시각이고, 서브스텝 경로가 기록했을 시각과 같다(재현 쿼리의 wait가 기준대로 읽힌다).
        * ⚠️ 뒤늦게 도착하는 과거 봉(백필)의 체결 가능성은 포기한다 — 죽어 있던 구간의 가격
          경로를 지어내지 않는 재시작 폐기 정책과 같은 부류의 보수적 처리다.
        """
        events: list[EngineEvent] = []
        for order in self.book.open_orders:
            state = self._state(order.symbol, order.timeframe)
            deadline = order.expiry_deadline_ms(state.htf_ms)
            if deadline is None or now_ms < deadline + _EXPIRY_GRACE_MS:
                continue
            cancelled = self.book.cancel_expired(order.symbol, order.timeframe)
            if cancelled is None:
                continue
            journal_id = self._take_pending_meta(state)
            if journal_id is not None and self._journal is not None:
                # 마지막 진행 상태(첫 걸림 시각·마지막 지정가)를 종결 전에 남긴다 — 경계
                # 커밋(record_progress)이 못 돈 채 만료되면 걸렸던 주문이 `first_rested_ms
                # IS NULL`로 남아 순수 미체결(no_fill)이 밴드 기각(deviation)으로 오분류된다.
                self._journal.record_progress(journal_id, cancelled)
                self._journal.record_cancelled(
                    journal_id, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=deadline
                )
            _logger.warning(
                "%s %s: 대기 지정가가 만료 기한을 넘긴 채 남아 있어 벽시계로 만료합니다"
                " (봉 계수가 얼어 있음 — 데이터 공급 확인 필요, WAN-298)",
                order.symbol,
                order.timeframe,
            )
            events.append(
                EngineEvent(
                    kind="cancelled_expired",
                    symbol=order.symbol,
                    timeframe=order.timeframe,
                    time_ms=deadline,
                    order=cancelled,
                )
            )
        return events

    def _apply_pending_price(
        self,
        state: _SeriesState,
        symbol: str,
        timeframe: str,
        *,
        low: float,
        high: float,
        close: float,
        time_ms: int,
    ) -> list[EngineEvent]:
        """대기 주문에 가격을 반영해 체결/조건취소를 판정한다(서브스텝·틱 공용).

        예약이나 상위TF 커밋은 하지 않는다 — 이미 걸린 주문의 체결 감지만 한다. 체결되면
        장부에 기록하고 슬롯을 비우며 `filled` 이벤트를, 체결 없이 종결되면(옵트인 게이트
        미충족 또는 live 밴드 청산 규칙 불가, WAN-143) `cancelled_condition_failed`를 낸다.
        """
        order = self.book.pending(symbol, timeframe)
        if order is None:
            return []
        events: list[EngineEvent] = []
        fill = self.book.on_price(symbol, timeframe, low, high, close, now_ms=time_ms)
        if fill is not None:
            journal_id = state.pending_journal_id
            if journal_id is not None and self._journal is not None:
                self._journal.record_filled(journal_id, fill)
            # 체결된 존을 오픈 포지션의 존으로 넘긴다 — 익절 후 재진입(WAN-274)이 같은 존에
            # 다시 band 지정가를 걸 때 읽는다(재진입이 꺼져 있어도 채우되 읽히지 않는다).
            state.occupied_zone = state.pending_zone
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
                    zone=state.occupied_zone,
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
        return events

    def _carry_old_zones(
        self,
        state: _SeriesState,
        df: pd.DataFrame,
        times: list[int],
        detected: set[ZoneId],
        fresh: dict[ZoneId, _ZoneWatch],
    ) -> None:
        """창 밖으로 밀려나 탐지에서 빠진 살아 있는 존을 이어 들고 간다(WAN-451).

        이어 들 존: 직전 대장에 있었고 · 이번 탐지 결과(아카이브 — 무효화된 존도 든다)에는 없고 ·
        시작이 창 앞쪽(`_CARRY_MARGIN_BARS` 안)이다. 창 시작 근처의 존은 스윙 맥락이 잘려 탐지가
        달라질 수 있어 여유를 둔다(WAN-343: 창 시작 +5~25봉). 그 존들은 새 확정 봉마다
        `strategy.order_blocks._invalidate`와 같은 규칙으로 갱신한다 — 무효화(강세: 저가가 바닥
        아래 · `zone_invalidation="close"`면 시·종가 중 낮은 값)면 버리고, 바깥→안이면 탭을 단다.
        """
        if not state.zones or not times:
            return
        margin = _CARRY_MARGIN_BARS * state.htf_ms
        use_wick = (self._ob_params or OrderBlockParams()).zone_invalidation == "wick"
        opens = df["open"].astype(float).to_numpy()
        highs = df["high"].astype(float).to_numpy()
        lows = df["low"].astype(float).to_numpy()
        closes = df["close"].astype(float).to_numpy()
        for zid, watch in state.zones.items():
            if zid in fresh or zid in detected:
                continue
            ob = watch.ob
            if ob.start_time >= times[0] + margin or watch.checked_through is None:
                continue
            if watch.checked_through < times[0] - state.htf_ms:
                _logger.warning(
                    "기억하던 존 %s를 버립니다 — 마지막 반영 봉과 새 창 사이가 끊겼다", zid
                )
                continue
            is_long = ob.direction is OrderBlockDirection.BULLISH
            inside = watch.bar_inside
            taps = list(ob.tapped_times)
            broken = False
            for i in range(bisect.bisect_right(times, watch.checked_through), len(times)):
                t = times[i]
                if is_long:
                    low_cmp = lows[i] if use_wick else min(opens[i], closes[i])
                    broken = bool(low_cmp < ob.bottom)
                else:
                    high_cmp = highs[i] if use_wick else max(opens[i], closes[i])
                    broken = bool(high_cmp > ob.top)
                if broken:
                    break
                now_inside = bool(lows[i] <= ob.top and highs[i] >= ob.bottom)
                if now_inside and not inside and t > ob.confirmed_time:
                    taps.append(t)
                inside = now_inside
            if broken:
                continue
            if len(taps) != len(ob.tapped_times):
                watch.ob = ob.model_copy(update={"tapped_times": tuple(taps)})
            watch.carried = True
            watch.bar_inside = inside
            watch.was_inside = inside
            watch.checked_through = times[-1]
            fresh[zid] = watch

    def _arm_shadows(self, state: _SeriesState, symbol: str, timeframe: str, time_ms: int) -> None:
        """새 탭(바깥→안 전이)마다 그림자 주문을 하나 만든다 — 슬롯·포지션과 무관(WAN-451).

        탭 판정·첫 탭·존폭 조건은 `_maybe_arm`과 같다(같은 존·같은 봉에서 한 번만). 주문은 같은
        `_build_order`로 만든다 — 진짜 주문과 가격 사슬이 갈라질 수 없다.
        """
        if not state.refreshed or state.forming_bar is None:
            return
        low, high = state.forming_low, state.forming_high
        if low is None or high is None:
            return
        params = self._params
        for zid, watch in state.zones.items():
            ob = watch.ob
            if watch.was_inside or watch.shadow_bar == state.forming_bar:
                continue
            if state.forming_bar <= ob.confirmed_time:
                continue
            if not (low <= ob.top and high >= ob.bottom):
                continue
            watch.shadow_bar = state.forming_bar
            tap_index = len(ob.tapped_times)
            if params.retap_mode == "once" and tap_index > 0:
                continue
            if not params.zone_width_filter_passes(ob, state.last_closed_atr):
                continue
            order = self._build_order(state, symbol, timeframe, ob, tap_index, time_ms)
            if order is not None:
                state.shadows.append((zid, order))

    def _apply_shadow_price(
        self,
        state: _SeriesState,
        symbol: str,
        timeframe: str,
        *,
        low: float,
        high: float,
        close: float,
        time_ms: int,
    ) -> list[EngineEvent]:
        """그림자 주문에 1분봉을 반영한다 — 체결되면 `shadow_filled`(장부·슬롯과 무관)."""
        events: list[EngineEvent] = []
        alive: list[tuple[ZoneId, PendingLimitOrder]] = []
        for zid, order in state.shadows:
            fill = order.on_price(low, high, close, now_ms=time_ms)
            if fill is not None:
                events.append(
                    EngineEvent(
                        kind="shadow_filled",
                        symbol=symbol,
                        timeframe=timeframe,
                        time_ms=time_ms,
                        order=order,
                        fill=fill,
                        zone=zid,
                    )
                )
            elif not order.status.is_terminal:
                alive.append((zid, order))
        state.shadows = alive
        return events

    def drop_series(self, symbol: str, timeframe: str) -> bool:
        """감시에서 빠진 시리즈의 상태를 버린다 — 대기 주문이 걸려 있으면 버리지 않는다(False).

        스토캐스틱 팔(WAN-451 ③)은 매주 종목이 바뀐다. 빠진 칸을 계속 들고 있으면 존 대장·시딩이
        쌓이기만 한다. 대기 주문이 있는 칸은 그 주문이 끝날 때까지 러너가 계속 폴링한다.
        """
        if self.book.pending(symbol, timeframe) is not None:
            return False
        self._series.pop((symbol, timeframe), None)
        return True

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
        """활성 존의 탭(바깥→안 전이)을 감지해 대기 지정가 주문을 예약한다.

        예약에 실패한 셋업은 미진입 사유를 장부에 남긴다(WAN-217) — 슬롯 점유
        (`cell_busy`) · 존폭 필터(`zone_width`) · 재탭(`retap`). 사유 기록은 순수한
        **부수효과**이고 예약/이벤트 흐름은 옛 동작과 같다(`tests/test_limit_engine.py`가
        파리티 고정). ⚠️ 볼린저 규칙 3 기각(deviation)은 여기서 남기지 않는다 — 채택
        경로(`intrabar_live`)는 밴드가 봉 안에서 움직여 예약 시점에 규칙 3을 판정하지 않고,
        주문이 걸린 뒤 밴드가 한 번도 유리하지 않은 채 만료되는 형태(`first_rested_ms
        IS NULL`)로 나타난다(`SeriesFillStats.unfilled_no_band`가 실측)."""
        if not state.refreshed or state.forming_bar is None:
            return []
        low = state.forming_low
        high = state.forming_high
        if low is None or high is None:
            return []

        # 슬롯이 차 있으면(오픈 포지션 또는 대기 주문) 새 주문을 걸 수 없다 — 옛 동작은
        # 여기서 즉시 반환이었다. 이제는 이 봉에 새로 탭한 존을 `cell_busy`로 남기되
        # armed는 설정하지 않아, 같은 봉 뒤에 슬롯이 비면 예약이 살아난다(파리티 보존).
        slot_busy = self.book.pending(symbol, timeframe) is not None or self._has_position(
            symbol, timeframe
        )

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
            # 이 봉에서 이 존으로 새 탭 전이가 확인됐다.
            tap_index = len(ob.tapped_times)

            if slot_busy:
                # armed를 설정하지 않는다 — 슬롯이 비면 이 봉 안에서 재시도할 수 있어야 한다.
                self._record_skip(
                    state, watch, symbol, timeframe, ob, tap_index, time_ms, SKIP_REASON_CELL_BUSY
                )
                continue

            watch.armed_forming_bar = state.forming_bar
            if params.retap_mode == "once" and tap_index > 0:
                self._record_skip(
                    state, watch, symbol, timeframe, ob, tap_index, time_ms, SKIP_REASON_RETAP
                )
                continue
            if not params.zone_width_filter_passes(ob, state.last_closed_atr):
                self._record_skip(
                    state, watch, symbol, timeframe, ob, tap_index, time_ms, SKIP_REASON_ZONE_WIDTH
                )
                continue  # WAN-159: 넓은 존은 주문을 걸지 않는다(판정 불가도 기각).

            order = self._build_order(state, symbol, timeframe, ob, tap_index, time_ms)
            if order is None:
                continue
            placed = self._place(state, symbol, timeframe, ob, order, time_ms)
            if placed:
                return placed
        return []

    def _place(
        self,
        state: _SeriesState,
        symbol: str,
        timeframe: str,
        ob: OrderBlock,
        order: PendingLimitOrder,
        time_ms: int,
        *,
        origin: str = ORDER_ORIGIN_TAP,
    ) -> list[EngineEvent]:
        """주문을 주문판에 걸고 존·장부 상태를 갱신해 `placed` 이벤트를 낸다.

        탭 예약(`_maybe_arm`)과 익절 후 재진입 재무장(`on_position_exit`, WAN-274)이 **같은**
        이 헬퍼를 쓴다 — 두 곳이 place/journal 배선을 복제하면 갈라진다(로직 이중화 금지).
        `origin`(WAN-305)이 그 두 출처를 장부에 남긴다 — 재무장 경로가 `"reentry"`를 넘긴다."""
        placed = self.book.place(order)
        if placed is None:
            return []
        state.pending_zone = _zone_id(ob)
        if self._journal is not None and self._session_id is not None:
            order.journal_id = self._journal.record_placed(
                order,
                session_id=self._session_id,
                zone_start_time=ob.start_time,
                zone_confirmed_time=ob.confirmed_time,
                origin=origin,
            )
            state.pending_journal_id = order.journal_id
        return [
            EngineEvent(
                kind="placed", symbol=symbol, timeframe=timeframe, time_ms=time_ms, order=order
            )
        ]

    def on_position_exit(
        self, symbol: str, timeframe: str, *, reason: SignalExitReason, time_ms: int
    ) -> list[EngineEvent]:
        """오픈 포지션 청산 후 존 재진입을 처리한다(WAN-274, 러너가 청산마다 호출).

        채택 규칙(WAN-273)을 그대로 물려받는다 — **익절**로 닫힌 존에만, 재진입이 켜져 있고
        그 존이 여전히 활성일 때, 재무장 지정가를 band(봉내 라이브 밴드 재산정)로 다시 건다.
        백테스트 재진입 루프(`backtest.wan228_reentry_census._iter_reentries`)의 라이브 대응:

        * **익절이라야 재무장**한다 — 손절·무효화면 그 존은 끝이다(`_iter_reentries`의
          `if not is_win: break`와 같은 규칙). 손절 청산은 여기서 `occupied_zone`만 비운다.
        * **무효화된 존은 재진입하지 않는다** — 존이 대장(`state.zones`)에서 사라졌으면
          (`on_htf_bars`가 무효화로 제거) 재무장하지 않는다(`_iter_reentries`의 무효화 break).
        * 재무장 주문은 **무기한 대기**(`limit_valid_bars=None`)한다 — 백테스트 재진입과 같은
          "형성-즉시 예약" 대리(WAN-223 §1). 채택 base 주문의 24봉 만료와 다르다.

        재무장 지정가는 탭 예약과 **같은 `_build_order`**가 만든다 — 채택 진입가 사슬
        (`IntrabarLiveLimit`, WAN-132)을 그대로 재사용하므로 재진입도 백테스트 band 팔과
        같은 가격 규칙을 탄다(로직 이중화 금지). `has_position`은 러너가 이미 포지션을 닫은
        뒤라 False이고, 대기 주문도 없어 슬롯은 비어 있다.
        """
        state = self._state(symbol, timeframe)
        zid = state.occupied_zone
        state.occupied_zone = None  # 재무장에 성공하면 체결 시 다시 채워진다.
        if self._reentry_entry_rule is None:
            return []
        if reason is not SignalExitReason.TAKE_PROFIT:
            return []  # 손절·무효화면 그 존은 끝(익절이라야 또 무장한다).
        if zid is None:
            return []
        watch = state.zones.get(zid)
        if watch is None:
            return []  # 무효화로 대장에서 사라진 존은 재진입하지 않는다.
        if self.book.pending(symbol, timeframe) is not None:
            return []  # 슬롯이 이미 차 있다(정상 청산 후엔 없어야 한다 — 방어).
        ob = watch.ob
        tap_index = len(ob.tapped_times)
        order = self._build_order(
            state, symbol, timeframe, ob, tap_index, time_ms, limit_valid_bars=None
        )
        if order is None:
            return []
        return self._place(
            state, symbol, timeframe, ob, order, time_ms, origin=ORDER_ORIGIN_REENTRY
        )

    # -- 재시작 영속화 (WAN-306) ----------------------------------------------

    def snapshot_series(
        self, symbol: str, timeframe: str, *, last_htf_time: int, saved_ms: int
    ) -> SeriesStateSnapshot | None:
        """이 시리즈의 재시작 복원용 스냅샷을 만든다(서브스텝을 소비한 적 없으면 None).

        재파생 가능한 것(존 대장·시딩·ATR)은 담지 않는다 — 복원이 저장소 창을
        `on_htf_bars`로 다시 먹여 재구성한다(`live.engine_state` 모듈 독스트링).
        `last_htf_time`은 러너가 마지막으로 먹인 확정 상위TF 봉 시각이다(복원 창의 앵커).
        """
        state = self._series.get((symbol, timeframe))
        if state is None or state.last_substep_time is None:
            return None
        pending = self.book.pending(symbol, timeframe)
        persisted_pending: PersistedPendingOrder | None = None
        if pending is not None and state.pending_zone is not None and pending.placed_ms is not None:
            persisted_pending = PersistedPendingOrder(
                journal_id=pending.journal_id,
                zone=state.pending_zone,
                tap_index=pending.tap_index,
                placed_ms=pending.placed_ms,
                limit_valid_bars=pending.limit_valid_bars,
                bars_elapsed=pending.bars_elapsed,
                first_rested_ms=pending.first_rested_ms,
                last_limit_price=pending.last_limit_price,
            )
        return SeriesStateSnapshot(
            saved_ms=saved_ms,
            last_substep_time=state.last_substep_time,
            last_htf_time=last_htf_time,
            forming_bar=state.forming_bar,
            forming_low=state.forming_low,
            forming_high=state.forming_high,
            running_close=state.running_close,
            zones=[
                PersistedZoneWatch(
                    zone=zid,
                    was_inside=watch.was_inside,
                    armed_forming_bar=watch.armed_forming_bar,
                    skip_recorded_forming_bar=watch.skip_recorded_forming_bar,
                    shadow_bar=watch.shadow_bar,
                    carried_ob=watch.ob if watch.carried else None,
                    bar_inside=watch.bar_inside,
                    checked_through=watch.checked_through,
                )
                for zid, watch in state.zones.items()
            ],
            pending=persisted_pending,
            occupied_zone=state.occupied_zone,
            shadows=[
                PersistedPendingOrder(
                    journal_id=None,
                    zone=zid,
                    tap_index=order.tap_index,
                    placed_ms=order.placed_ms,
                    limit_valid_bars=order.limit_valid_bars,
                    bars_elapsed=order.bars_elapsed,
                    first_rested_ms=order.first_rested_ms,
                    last_limit_price=order.last_limit_price,
                )
                for zid, order in state.shadows
                if order.placed_ms is not None
            ],
        )

    def _rebuild(
        self, state: _SeriesState, symbol: str, timeframe: str, persisted: PersistedPendingOrder
    ) -> PendingLimitOrder | None:
        """저장된 주문 생애로 주문을 다시 만든다(그림자 복원 — 존이 사라졌으면 None)."""
        watch = state.zones.get(persisted.zone)
        if watch is None:
            return None
        order = self._build_order(
            state,
            symbol,
            timeframe,
            watch.ob,
            persisted.tap_index,
            persisted.placed_ms,
            limit_valid_bars=persisted.limit_valid_bars,
        )
        if order is None:
            return None
        order.bars_elapsed = persisted.bars_elapsed
        order.first_rested_ms = persisted.first_rested_ms
        order.last_limit_price = persisted.last_limit_price
        return order

    def restore_series(
        self, symbol: str, timeframe: str, snapshot: SeriesStateSnapshot
    ) -> PendingLimitOrder | None:
        """스냅샷의 세션 상태를 되살린다 — 반드시 스냅샷 시점 창을 `on_htf_bars`로 먹인
        **뒤에** 불러야 한다(존 대장·시딩·ATR이 그 호출에서 재구성된다).

        전이/시도 표식·형성 중 봉 누적·`occupied_zone`(재진입 재무장 상태)을 복원하고,
        대기 주문이 있으면 탭 예약과 **같은 `_build_order`**로 재구성해 주문판에 되건다
        (지표 상태는 재시딩 = 예약 시점 시딩 + 경계 커밋과 동일 · 생애 스칼라는 스냅샷
        값으로 되돌린다). 복원된 주문(또는 None)을 반환한다 — 존이 대장에서 사라졌거나
        재구성이 거부되면 주문은 복원하지 않는다(장부 행은 기존 재시작 폐기 경로로 남는다).
        """
        state = self._state(symbol, timeframe)
        if not state.refreshed:
            raise ValueError("restore_series는 on_htf_bars로 존 대장을 세운 뒤 불러야 합니다.")
        state.forming_bar = snapshot.forming_bar
        state.forming_low = snapshot.forming_low
        state.forming_high = snapshot.forming_high
        state.running_close = snapshot.running_close
        state.last_substep_time = snapshot.last_substep_time
        for persisted in snapshot.zones:
            watch = state.zones.get(persisted.zone)
            if watch is None and persisted.carried_ob is not None:
                # 기억하던 존(WAN-451) — 탐지로 다시 세울 수 없으므로 스냅샷의 존을 그대로 되살린다.
                watch = _ZoneWatch(ob=persisted.carried_ob, was_inside=persisted.was_inside)
                watch.carried = True
                watch.bar_inside = persisted.bar_inside
                watch.checked_through = persisted.checked_through
                state.zones[tuple(persisted.zone)] = watch  # type: ignore[index]
            if watch is None:
                continue  # 스냅샷 이후 대장에서 사라진 존 — 탐지 결과가 정본이다.
            watch.shadow_bar = persisted.shadow_bar
            watch.was_inside = persisted.was_inside
            watch.armed_forming_bar = persisted.armed_forming_bar
            watch.skip_recorded_forming_bar = persisted.skip_recorded_forming_bar
        state.occupied_zone = snapshot.occupied_zone
        state.shadows = []
        for saved in snapshot.shadows:
            restored = self._rebuild(state, symbol, timeframe, saved)
            if restored is not None:
                state.shadows.append((saved.zone, restored))
        pending = snapshot.pending
        if pending is None:
            return None
        watch = state.zones.get(pending.zone)
        if watch is None:
            _logger.warning(
                "%s %s: 대기 주문의 존이 현재 대장에 없어 복원하지 않습니다(재시작 폐기로 남음)",
                symbol,
                timeframe,
            )
            return None
        order = self._build_order(
            state,
            symbol,
            timeframe,
            watch.ob,
            pending.tap_index,
            pending.placed_ms,
            limit_valid_bars=pending.limit_valid_bars,
        )
        if order is None:
            _logger.warning(
                "%s %s: 대기 주문을 재구성할 수 없어 복원하지 않습니다(재시작 폐기로 남음)",
                symbol,
                timeframe,
            )
            return None
        order.bars_elapsed = pending.bars_elapsed
        order.first_rested_ms = pending.first_rested_ms
        order.last_limit_price = pending.last_limit_price
        order.journal_id = pending.journal_id
        if self.book.place(order) is None:
            return None
        state.pending_zone = pending.zone
        state.pending_journal_id = pending.journal_id
        return order

    def _record_skip(
        self,
        state: _SeriesState,
        watch: _ZoneWatch,
        symbol: str,
        timeframe: str,
        ob: OrderBlock,
        tap_index: int,
        time_ms: int,
        reason: str,
    ) -> None:
        """윗단계에서 걸러진 셋업을 미진입 사유로 장부에 남긴다(WAN-217, 부수효과).

        봉당 존 하나에 한 번만 남긴다 — `_maybe_arm`이 매 서브스텝 재평가하므로 그대로
        두면 같은 사유가 봉 길이만큼 중복 적재된다. `armed`와 별도 표식을 쓰는 이유는
        `cell_busy`가 armed를 설정하면 안 되기 때문이다(모듈·`_ZoneWatch` 문단). 첫 사유가
        남고, 같은 봉에 슬롯이 비어 사유가 바뀌어도 다시 찍지 않는다(측정 이중 계수 방지)."""
        if self._journal is None or self._session_id is None:
            return
        if watch.skip_recorded_forming_bar == state.forming_bar:
            return
        watch.skip_recorded_forming_bar = state.forming_bar
        self._journal.record_skipped(
            session_id=self._session_id,
            symbol=symbol,
            timeframe=timeframe,
            direction=ob.direction.value,
            tap_index=tap_index,
            placed_ms=time_ms,
            reason=reason,
            zone_start_time=ob.start_time,
            zone_confirmed_time=ob.confirmed_time,
        )
        # 저널에 실제로 남은 사유만(봉당 1회) 건별 알림 훅에 넘긴다(WAN-221 옵트인).
        if self._skip_listener is not None:
            self._skip_listener(reason, symbol, timeframe, time_ms)

    def _build_order(
        self,
        state: _SeriesState,
        symbol: str,
        timeframe: str,
        ob: OrderBlock,
        tap_index: int,
        time_ms: int,
        limit_valid_bars: int | None | _Unset = _UNSET,
    ) -> PendingLimitOrder | None:
        params = self._params
        # 재진입(WAN-274)은 `limit_valid_bars=None`(무기한)을 명시로 넘긴다 — 미지정이면
        # 채택 base 주문의 기본 만료(`params.limit_valid_bars`, 24봉 WAN-222)를 쓴다.
        valid_bars = params.limit_valid_bars if limit_valid_bars is _UNSET else limit_valid_bars
        is_long = ob.direction is OrderBlockDirection.BULLISH
        stop_price = ob.bottom if is_long else ob.top
        rsi_state = RealtimeRsi.seed_from_closed(state.seed_closes, params.rsi_length)

        # 통과한 존폭(÷ATR) — 필터가 켜져 ATR을 알 때만 산출한다(`zone_width_filter_passes`
        # 와 같은 정의: 존폭 = top−bottom, ATR = 탭 봉 직전 확정봉). 판정에는 이미 통과했고
        # 여기선 알림(WAN-189)이 찍을 값으로만 실어 나른다.
        atr_value = state.last_closed_atr
        zone_width_atr: float | None = None
        if atr_value is not None and atr_value > 0.0:
            zone_width_atr = (ob.top - ob.bottom) / atr_value

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
            limit_valid_bars=valid_bars,
            cancel_on_condition_fail=params.cancel_limit_on_condition_fail,
            fill_penetration_bps=params.fill_penetration_bps,
            placed_ms=time_ms,
            tap_index=tap_index,
            zone_width_atr=zone_width_atr,
        )


def _validate_reentry_rule(
    params: ConfluenceParams, reentry_entry_rule: ReentryEntryRule | None
) -> None:
    """라이브가 배선하지 않은 재진입 규칙을 **조용히 무시하지 않고 거부**한다(WAN-95 교훈).

    채택 값은 `"band"`(봉내 라이브 밴드 재산정)뿐이다 — `"freeze"`/`"zone"`는 백테스트
    측정 팔(WAN-267)이라 라이브에 배선하지 않았고, 라벨만 붙고 다른 규칙으로 돌면 파리티가
    깨진다. `None`은 재진입 없음(옛 동작)이라 항상 허용된다."""
    if reentry_entry_rule is None:
        return
    if reentry_entry_rule != "band":
        raise ValueError(
            f"라이브 엔진은 재진입 규칙 'band'만 배선했습니다: {reentry_entry_rule!r}"
            " ('freeze'/'zone'은 백테스트 측정 팔 전용, WAN-267). 채택 값은 'band'(WAN-273)."
        )
    if params.deviation_filter is None:
        raise ValueError(
            "band 재진입은 볼린저 필터가 있어야 재무장 지정가를 재산정할 수 있습니다"
            " (채택 기본값은 켜짐)."
        )


def _validate_live_params(params: ConfluenceParams) -> None:
    """라이브 엔진이 재현할 수 없는 설정을 **조용히 무시하지 않고 거부**한다.

    라벨만 붙고 실행이 다른 것(WAN-95의 교훈)을 막는 가드다 — 지원 범위는 채택
    기본값(`ConfluenceParams()`) 전부와, 라이브에서 의미가 같은 옵트인 일부다.
    """
    if params.entry_mode != "zone_limit":
        raise ValueError(
            f"존-지정가 라이브 엔진은 entry_mode='zone_limit' 전용입니다: {params.entry_mode!r}"
            " (종가 진입 A안 경로는 WAN-208에서 제거됐습니다)."
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
    # 관통 요구(`fill_penetration_bps`)는 라이브에서도 같은 산식으로 판정한다(WAN-451 ⑤ —
    # 팔은 `pen_5bp`로 판정했다). 무작위 탈락(`fill_dropout_rate`)은 큐 우선순위의 **통계적
    # 근사**라 개별 주문에 재현할 수 없다 — 그것만 거부한다.
    if params.fill_dropout_rate != 0.0:
        raise ValueError(
            "체결 탈락 렌즈(fill_dropout_rate)는 백테스트 민감도 전용입니다 — 무작위 탈락은"
            " 개별 라이브 주문에 재현할 수 없습니다(관통 요구 fill_penetration_bps는 지원)."
        )
