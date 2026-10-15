"""존-지정가 라이브 엔진 — 오더블록 감시 · 탭마다 독립 지정가 · 체결/취소 판정 (WAN-45 · WAN-451).

스토캐스틱 팔(WAN-451 ③)의 진입 경로(존 근단 지정가 + 볼린저 재산정 + 오프셋 2bp,
`entry_mode="zone_limit"`)를 실시간(페이퍼)에서 굴리는 순수 로직 계층이다. 러너
(`live.zone_limit_runner`)가 데이터를 공급하고, 이 엔진은 두 입력만 받는다:

* `on_htf_bars(...)` — 새 **확정 상위TF 봉** 창. 오더블록 탐지(`OrderBlockDetector`)를
  다시 돌려 존 대장을 갱신하고(창 밖으로 밀려난 살아 있는 존은 엔진이 기억한다), 무효화된
  존의 대기 주문을 취소한다.
* `on_substep(...)` — **확정 1분봉** 하나(= 백테스트 서브스텝과 같은 모양). 상위TF 봉
  경계를 넘으면 RSI·밴드 상태를 커밋하고(만료 계수 포함), 존 탭을 감지해 주문을 걸고,
  걸린 주문의 체결/취소를 판정한다.

## 탭마다 독립 지정가 (WAN-451)

정본 팔(`wan436.place` → 채택 북)은 탭마다 지정가를 **독립으로** 시뮬레이션하고, 칸에
포지션이 없으면 먼저 체결된 것을 잡는다. 그래서 이 엔진도 칸당 주문 하나(옛 슬롯)를 두지
않고 **새 탭(바깥→안 전이)마다 주문을 하나씩** 건다 — 같은 칸에 여러 주문이 동시에 걸릴 수
있다. 엔진은 체결을 `filled` 이벤트(체결 + 존)로 내기만 하고, **진입 여부는 러너가 정한다**
(칸에 포지션이 있나 · 팔 규칙 · 몰림 규칙). 장부(`OrderJournal`)와 세션이 주어지면 주문
생애(예약 · 진행 · 체결 · 만료 · 무효화 · 폐기)를 장부에 남기고, 없으면 장부 없이 돈다
(재생 대조 · 단위 테스트).

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
  구간을 보는 백테스트와 RSI(무한 기억 RMA)가 미세하게 다를 수 있다.
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
from backtest.zone_limit_backtest import IntrabarLiveLimit
from live.engine_state import PersistedPendingOrder, PersistedZoneWatch, SeriesStateSnapshot
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder
from live.order_journal import (
    ORDER_ORIGIN_TAP,
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
    """주문의 존 — 몰림 규칙이 「같은 탭의 쌍둥이」를 빼고 신호 장부가 존을 적는 데 쓴다."""


@dataclass
class _ZoneWatch:
    """감시 중인 활성 존 하나의 라이브 상태."""

    ob: OrderBlock
    was_inside: bool
    """마지막 **확정** 상위TF 봉이 존 범위와 겹쳤는지 — 탭은 바깥→안 전이만 센다
    (탐지기 `_invalidate`의 `_inside` 상태와 같은 규칙)."""
    tap_bar: int | None = None
    """이번 형성 중 봉에서 이미 탭을 처리한(주문을 걸었거나 걸러낸) 봉 시각 — 탭 하나에 주문
    하나다(같은 봉 안의 연속 서브스텝이 같은 탭으로 주문을 또 걸지 않게)."""
    carried: bool = False
    """탐지 창 밖으로 밀려나 엔진이 직접 무효화·탭을 따라가는 존(WAN-451)."""
    bar_inside: bool = False
    """마지막으로 반영한 **확정 봉**이 존과 겹쳤는지 — 탐지기 `_inside`와 같은 뜻(확정 봉 자신은
    거짓). `was_inside`는 봉 경계에서 형성 봉 범위로 미리 바뀌므로 기억하는 존의 탭 판정에는
    이 값을 따로 둔다."""
    checked_through: int | None = None
    """존 상태(무효화·탭)가 반영된 마지막 확정 봉 시각."""
    skip_recorded_forming_bar: int | None = None
    """이번 형성 중 봉에서 이미 미진입 사유를 장부에 남긴 봉 시각(WAN-217) — 같은 봉·같은 존의
    사유가 여러 번 찍히지 않게 한다."""


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
    refreshed: bool = False
    """`on_htf_bars`가 최소 한 번 존 대장을 세웠는지 — 그 전에는 예약하지 않는다."""
    #: 걸려 있는 주문 — (존, 주문). 탭마다 하나씩 독립으로 걸린다(WAN-451).
    orders: list[tuple[ZoneId, PendingLimitOrder]] = field(default_factory=list)


class ZoneLimitLiveEngine:
    """존-지정가 라이브 엔진(순수 로직 — I/O는 러너가 담당).

    `journal`·`session_id`가 주어지면 주문 생애와 미진입 사유를 장부에 남긴다. 진입 판정은
    하지 않는다 — 체결은 `filled` 이벤트로 내고, 칸에 포지션이 있는지·팔 규칙은 러너가 본다.
    """

    def __init__(
        self,
        *,
        params: ConfluenceParams,
        order_block_params: OrderBlockParams | None = None,
        journal: OrderJournal | None = None,
        session_id: int | None = None,
        skip_listener: Callable[[str, str, str, int], None] | None = None,
    ) -> None:
        _validate_live_params(params)
        self._params = params
        self._ob_params = order_block_params
        self._journal = journal
        self._session_id = session_id
        #: 미진입 사유 실시간 알림 훅(reason, symbol, timeframe, time_ms) — 옵트인 건별
        #: 텔레그램용(WAN-221). None이면 부수효과 없음(이벤트 스트림·저널 기록 불변).
        #: 장부에 사유가 실제로 남을 때만(봉당 1회) 호출돼 텔레그램 카운트와 저널이 어긋나지
        #: 않는다. ⚠️ 이벤트를 추가로 내지 **않는다**.
        self._skip_listener = skip_listener
        self._series: dict[SeriesKey, _SeriesState] = {}

    # -- 상위TF 확정봉: 존 대장 갱신 -----------------------------------------

    def on_htf_bars(self, symbol: str, timeframe: str, df: pd.DataFrame) -> list[EngineEvent]:
        """새 확정 상위TF 봉 창으로 존 대장을 갱신한다.

        `df`는 시간 오름차순 **확정봉만** 담은 창이다(러너가 갭 검사를 마친 값 —
        구멍이 있으면 볼린저·RSI가 조용히 틀린 값을 내고 그 값이 곧 주문 가격이다,
        WAN-156 §3). 탐지를 다시 돌려 활성 존을 세우고(창 밖으로 밀려난 살아 있는 존은
        이어 든다), 대장에서 사라진 존의 대기 주문을 취소하며, 지표 시딩용 확정 종가를
        저장소 기준으로 다시 맞춘다.
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
                # 존 내용(탭 기록 등)은 갱신하되 형성 중 봉의 탭 처리 기록은 유지.
                watch.ob = ob
                watch.was_inside = was_inside
            watch.carried = False
            watch.bar_inside = was_inside
            watch.checked_through = times[-1]
            fresh[zid] = watch
        self._carry_old_zones(state, df, times, detected, fresh)
        state.zones = fresh
        state.refreshed = True

        # 대장에서 사라진(무효화된) 존의 주문을 취소한다. 백테스트는 `break_time`(무효화 봉
        # 시작)에 취소하지만 라이브는 그 봉이 닫혀 탐지가 확인된 지금이다 — 한 봉 늦는 알려진
        # 근사(모듈 독스트링).
        alive: list[tuple[ZoneId, PendingLimitOrder]] = []
        for zid, order in state.orders:
            if zid in state.zones:
                alive.append((zid, order))
                continue
            order.cancel_invalidated()
            if self._journal is not None and order.journal_id is not None:
                self._journal.record_cancelled(order.journal_id, order.status, now_ms=now_ms)
            events.append(
                EngineEvent(
                    kind="cancelled_invalidated",
                    symbol=symbol,
                    timeframe=timeframe,
                    time_ms=now_ms,
                    order=order,
                    zone=zid,
                )
            )
        state.orders = alive
        return events

    # -- 1분봉 서브스텝: 커밋·예약·체결 --------------------------------------

    def on_substep(
        self, symbol: str, timeframe: str, *, time_ms: int, low: float, high: float, close: float
    ) -> list[EngineEvent]:
        """확정 1분봉 하나를 반영한다(백테스트 서브스텝과 같은 판정 순서).

        1. 상위TF 봉 경계를 넘으면 직전 봉을 `running_close`로 커밋(만료 계수 포함).
        2. 활성 존의 탭(바깥→안 전이)을 감지해 탭마다 주문을 건다.
        3. 걸린 주문 전부에 이 1분봉을 반영해 체결/취소를 판정한다.
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
                    # 지표 상태를 지어낼 수 없으므로 걸린 주문을 폐기한다(측정 오염 방지,
                    # 재시작 폐기와 같은 부류). 존 대장·시딩은 다음 `on_htf_bars`가 맞춘다.
                    events.extend(self._discard_orders(state, symbol, timeframe, time_ms))
                else:
                    events.extend(
                        self._commit_orders(state, symbol, timeframe, state.running_close, time_ms)
                    )
                    self._append_seed(state, state.forming_bar, state.running_close)
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
            state.forming_bar = htf_bar
            state.forming_low = None
            state.forming_high = None
        elif state.forming_bar is None:
            state.forming_bar = htf_bar

        state.forming_low = low if state.forming_low is None else min(state.forming_low, low)
        state.forming_high = high if state.forming_high is None else max(state.forming_high, high)

        # 2) 탭 감지 → 주문. 체결 판정보다 먼저다 — 백테스트는 탭 봉의 첫 서브스텝부터
        # 주문이 걸려 있으므로, 전이 서브스텝에서 예약과 체결이 같은 스텝에 일어날 수 있다.
        events.extend(self._arm_orders(state, symbol, timeframe, time_ms))

        # 3) 걸린 주문 체결/취소 판정.
        if state.orders:
            events.extend(
                self._apply_price(
                    state, symbol, timeframe, low=low, high=high, close=close, time_ms=time_ms
                )
            )

        state.running_close = close
        state.last_substep_time = time_ms
        return events

    def expire_overdue(self, *, now_ms: int) -> list[EngineEvent]:
        """만료 기한을 넘긴 대기 주문을 벽시계로 만료시킨다(WAN-298 백스톱).

        봉 계수 만료(`on_substep`의 경계 커밋)는 **소비된 1분봉**으로만 전진하므로, 수집이
        멈추면(새 1분봉 없음 → 서브스텝 없음) 주문이 기한을 넘겨도 만료되지 않고 남는다.
        이 백스톱은 러너가 매 폴링에 벽시계로 기한(`expiry_deadline_ms` — 서브스텝 경로와
        같은 시각)을 검사해, 유예(`_EXPIRY_GRACE_MS`)를 지나서도 대기 중인 주문을
        `cancelled_expired`로 정리한다.

        * **정상 경로와 경합하지 않는다** — 데이터가 흐르면 서브스텝 경로가 기한 도달 직후
          (1분봉 확정 + 폴링 지연) 먼저 만료시키고, 백스톱은 유예 뒤에만 발동한다.
        * **무기한 주문(`limit_valid_bars=None`)은 건드리지 않는다.**
        * 장부 종결 시각은 벽시계가 아니라 **기한 시각**이다 — 주문이 유효하기를 멈춘
          시각이고, 서브스텝 경로가 기록했을 시각과 같다.
        * ⚠️ 뒤늦게 도착하는 과거 봉(백필)의 체결 가능성은 포기한다 — 죽어 있던 구간의 가격
          경로를 지어내지 않는 재시작 폐기 정책과 같은 부류의 보수적 처리다.
        """
        events: list[EngineEvent] = []
        for (symbol, timeframe), state in self._series.items():
            alive: list[tuple[ZoneId, PendingLimitOrder]] = []
            for zid, order in state.orders:
                deadline = order.expiry_deadline_ms(state.htf_ms)
                if deadline is None or now_ms < deadline + _EXPIRY_GRACE_MS:
                    alive.append((zid, order))
                    continue
                order.cancel_expired()
                if self._journal is not None and order.journal_id is not None:
                    # 마지막 진행 상태(첫 걸림 시각·마지막 지정가)를 종결 전에 남긴다 — 경계
                    # 커밋(record_progress)이 못 돈 채 만료되면 걸렸던 주문이 `first_rested_ms
                    # IS NULL`로 남아 순수 미체결이 밴드 기각(deviation)으로 오분류된다.
                    self._journal.record_progress(order.journal_id, order)
                    self._journal.record_cancelled(
                        order.journal_id, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=deadline
                    )
                _logger.warning(
                    "%s %s: 대기 지정가가 만료 기한을 넘긴 채 남아 있어 벽시계로 만료합니다"
                    " (봉 계수가 얼어 있음 — 데이터 공급 확인 필요, WAN-298)",
                    symbol,
                    timeframe,
                )
                events.append(
                    EngineEvent(
                        kind="cancelled_expired",
                        symbol=symbol,
                        timeframe=timeframe,
                        time_ms=deadline,
                        order=order,
                        zone=zid,
                    )
                )
            state.orders = alive
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

        탐지 창(`live_signal_lookback_bars`)이 미끄러져 존의 시작이 창 밖으로 나가면 탐지기는
        그 존을 다시 못 만든다 — 정본(전 이력 탐지)은 몇 년 된 존에도 들어간다(팔 후보의 약
        7%가 1500봉보다 오래된 존). 이어 들 존: 직전 대장에 있었고 · 이번 탐지 결과(아카이브 —
        무효화된 존도 든다)에는 없고 · 시작이 창 앞쪽(`_CARRY_MARGIN_BARS` 안)이다. 창 시작
        근처의 존은 스윙 맥락이 잘려 탐지가 달라질 수 있어 여유를 둔다(WAN-343: 창 시작
        +5~25봉). 그 존들은 새 확정 봉마다 `strategy.order_blocks._invalidate`와 같은 규칙으로
        갱신한다 — 무효화(강세: 저가가 바닥 아래 · `zone_invalidation="close"`면 시·종가 중 낮은
        값)면 버리고, 바깥→안이면 탭을 단다.
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

    def _arm_orders(
        self, state: _SeriesState, symbol: str, timeframe: str, time_ms: int
    ) -> list[EngineEvent]:
        """새 탭(바깥→안 전이)마다 지정가 주문을 하나 건다 — 칸에 포지션·주문이 있어도(WAN-451).

        탭 판정은 탐지기와 같은 규칙이다(바깥→안 전이만 · 존이 확정되는 봉은 탭이 아니다 ·
        같은 존·같은 봉에서 한 번만). 재탭(`retap_mode="once"`에서 첫 탭 이후)과 존폭 필터
        기각(WAN-159)은 주문을 걸지 않고 미진입 사유를 장부에 남긴다(WAN-217). ⚠️ 볼린저
        규칙 3 기각(deviation)은 여기서 남기지 않는다 — 채택 경로(`intrabar_live`)는 밴드가 봉
        안에서 움직여 예약 시점에 규칙 3을 판정하지 않고, 주문이 걸린 뒤 밴드가 한 번도 유리하지
        않은 채 만료되는 형태(`first_rested_ms IS NULL`)로 나타난다.
        """
        if not state.refreshed or state.forming_bar is None:
            return []
        low, high = state.forming_low, state.forming_high
        if low is None or high is None:
            return []
        params = self._params
        events: list[EngineEvent] = []
        for zid, watch in state.zones.items():
            ob = watch.ob
            if watch.was_inside or watch.tap_bar == state.forming_bar:
                continue  # 탭은 바깥→안 전이만(연속 체류는 첫 진입만) — 탐지기와 같은 규칙.
            if state.forming_bar <= ob.confirmed_time:
                continue  # 확정 봉 자신은 탭이 될 수 없다(탐지기 `times[t] > confirmed_time`).
            if not (low <= ob.top and high >= ob.bottom):
                continue
            watch.tap_bar = state.forming_bar
            tap_index = len(ob.tapped_times)
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
            state.orders.append((zid, order))
            if self._journal is not None and self._session_id is not None:
                order.journal_id = self._journal.record_placed(
                    order,
                    session_id=self._session_id,
                    zone_start_time=ob.start_time,
                    zone_confirmed_time=ob.confirmed_time,
                    origin=ORDER_ORIGIN_TAP,
                )
            events.append(
                EngineEvent(
                    kind="placed",
                    symbol=symbol,
                    timeframe=timeframe,
                    time_ms=time_ms,
                    order=order,
                    zone=zid,
                )
            )
        return events

    def _apply_price(
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
        """걸린 주문 전부에 1분봉을 반영한다 — 체결되면 `filled`, 체결 없이 종결되면(옵트인
        게이트 미충족 또는 live 밴드 청산 규칙 불가, WAN-143) `cancelled_condition_failed`."""
        events: list[EngineEvent] = []
        alive: list[tuple[ZoneId, PendingLimitOrder]] = []
        for zid, order in state.orders:
            fill = order.on_price(low, high, close, now_ms=time_ms)
            if fill is not None:
                if self._journal is not None and order.journal_id is not None:
                    self._journal.record_filled(order.journal_id, fill)
                events.append(
                    EngineEvent(
                        kind="filled",
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
            else:
                if self._journal is not None and order.journal_id is not None:
                    self._journal.record_cancelled(order.journal_id, order.status, now_ms=time_ms)
                events.append(
                    EngineEvent(
                        kind="cancelled_condition_failed",
                        symbol=symbol,
                        timeframe=timeframe,
                        time_ms=time_ms,
                        order=order,
                        zone=zid,
                    )
                )
        state.orders = alive
        return events

    def _commit_orders(
        self, state: _SeriesState, symbol: str, timeframe: str, close: float, time_ms: int
    ) -> list[EngineEvent]:
        """상위TF 봉 경계 — 걸린 주문의 지표 커밋과 만료 계수(만료면 `cancelled_expired`)."""
        events: list[EngineEvent] = []
        alive: list[tuple[ZoneId, PendingLimitOrder]] = []
        for zid, order in state.orders:
            order.on_bar_close(close)
            if not order.status.is_terminal:
                alive.append((zid, order))
                if self._journal is not None and order.journal_id is not None:
                    self._journal.record_progress(order.journal_id, order)
                continue
            if self._journal is not None and order.journal_id is not None:
                self._journal.record_cancelled(order.journal_id, order.status, now_ms=time_ms)
            events.append(
                EngineEvent(
                    kind="cancelled_expired",
                    symbol=symbol,
                    timeframe=timeframe,
                    time_ms=time_ms,
                    order=order,
                    zone=zid,
                )
            )
        state.orders = alive
        return events

    def _discard_orders(
        self, state: _SeriesState, symbol: str, timeframe: str, time_ms: int
    ) -> list[EngineEvent]:
        """1분봉 공백(상위TF 봉 건너뜀) — 걸린 주문을 버린다.

        데이터 구멍 폐기는 무효화가 아니라 측정 무효다 — 재시작 폐기와 같은 상태로 남겨
        체결률 분모에서 뺀다.
        """
        if not state.orders:
            return []
        _logger.warning(
            "%s %s: 1분봉 공백(상위TF 봉 건너뜀) — 대기 주문 %d개 폐기(측정 오염 방지)",
            symbol,
            timeframe,
            len(state.orders),
        )
        events: list[EngineEvent] = []
        for zid, order in state.orders:
            if self._journal is not None and order.journal_id is not None:
                self._journal.record_discarded(order.journal_id, now_ms=time_ms)
            events.append(
                EngineEvent(
                    kind="discarded",
                    symbol=symbol,
                    timeframe=timeframe,
                    time_ms=time_ms,
                    order=order,
                    zone=zid,
                )
            )
        state.orders = []
        return events

    def open_orders(self) -> list[PendingLimitOrder]:
        """걸려 있는 주문 전부(모든 시리즈)."""
        return [o for state in self._series.values() for _z, o in state.orders]

    def journal_ids(self, symbol: str, timeframe: str) -> set[int]:
        """이 시리즈에 걸려 있는 주문의 장부 행(재시작 복원에서 기동 폐기를 막을 때)."""
        state = self._series.get((symbol, timeframe))
        if state is None:
            return set()
        return {o.journal_id for _z, o in state.orders if o.journal_id is not None}

    def drop_series(self, symbol: str, timeframe: str) -> bool:
        """감시에서 빠진 시리즈의 상태를 버린다 — 대기 주문이 걸려 있으면 버리지 않는다(False).

        스토캐스틱 팔(WAN-451 ③)은 매주 종목이 바뀐다. 빠진 칸을 계속 들고 있으면 존 대장·시딩이
        쌓이기만 한다. 대기 주문이 있는 칸은 그 주문이 끝날 때까지 러너가 계속 폴링한다.
        """
        state = self._series.get((symbol, timeframe))
        if state is not None and state.orders:
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

    # -- 재시작 영속화 (WAN-306) ----------------------------------------------

    def snapshot_series(
        self, symbol: str, timeframe: str, *, last_htf_time: int, saved_ms: int
    ) -> SeriesStateSnapshot | None:
        """이 시리즈의 재시작 복원용 스냅샷을 만든다(서브스텝을 소비한 적 없으면 None).

        재파생 가능한 것(탐지로 다시 세워지는 존·시딩·ATR)은 담지 않는다 — 복원이 저장소
        창을 `on_htf_bars`로 다시 먹여 재구성한다(`live.engine_state` 모듈 독스트링).
        `last_htf_time`은 러너가 마지막으로 먹인 확정 상위TF 봉 시각이다(복원 창의 앵커).
        """
        state = self._series.get((symbol, timeframe))
        if state is None or state.last_substep_time is None:
            return None
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
                    skip_recorded_forming_bar=watch.skip_recorded_forming_bar,
                    shadow_bar=watch.tap_bar,
                    carried_ob=watch.ob if watch.carried else None,
                    bar_inside=watch.bar_inside,
                    checked_through=watch.checked_through,
                )
                for zid, watch in state.zones.items()
            ],
            shadows=[
                PersistedPendingOrder(
                    journal_id=order.journal_id,
                    zone=zid,
                    tap_index=order.tap_index,
                    placed_ms=order.placed_ms,
                    limit_valid_bars=order.limit_valid_bars,
                    bars_elapsed=order.bars_elapsed,
                    first_rested_ms=order.first_rested_ms,
                    last_limit_price=order.last_limit_price,
                )
                for zid, order in state.orders
                if order.placed_ms is not None
            ],
        )

    def _rebuild(
        self, state: _SeriesState, symbol: str, timeframe: str, persisted: PersistedPendingOrder
    ) -> PendingLimitOrder | None:
        """저장된 주문 생애로 주문을 다시 만든다(존이 사라졌거나 재구성이 거부되면 None).

        탭 예약과 **같은 `_build_order`**로 만든다 — 지표 상태는 재시딩(= 예약 시점 시딩 + 경계
        커밋과 동일)이고, 생애 스칼라는 스냅샷 값으로 되돌린다.
        """
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
        order.journal_id = persisted.journal_id
        return order

    def restore_series(
        self, symbol: str, timeframe: str, snapshot: SeriesStateSnapshot
    ) -> list[PendingLimitOrder]:
        """스냅샷의 세션 상태를 되살린다 — 반드시 스냅샷 시점 창을 `on_htf_bars`로 먹인
        **뒤에** 불러야 한다(존 대장·시딩·ATR이 그 호출에서 재구성된다).

        전이/탭 표식·형성 중 봉 누적·기억하던 존을 복원하고, 걸려 있던 주문을 재구성해
        되건다. 되살린 주문 목록을 반환한다 — 존이 대장에서 사라졌거나 재구성이 거부된
        주문은 복원하지 않는다(장부 행은 기존 재시작 폐기 경로로 남는다).
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
            watch.tap_bar = persisted.shadow_bar
            watch.was_inside = persisted.was_inside
            watch.skip_recorded_forming_bar = persisted.skip_recorded_forming_bar
        state.orders = []
        dropped = 0
        for saved in snapshot.shadows:
            restored = self._rebuild(state, symbol, timeframe, saved)
            if restored is None:
                dropped += 1
                continue
            state.orders.append((saved.zone, restored))
        if dropped:
            _logger.warning(
                "%s %s: 대기 주문 %d개를 재구성할 수 없어 복원하지 않습니다(재시작 폐기로 남음)",
                symbol,
                timeframe,
                dropped,
            )
        return [o for _z, o in state.orders]

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

        봉당 존 하나에 한 번만 남긴다(측정 이중 계수 방지)."""
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
        # 재시작 복원은 저장된 유효기간을 명시로 넘긴다 — 미지정이면 채택 기본 만료
        # (`params.limit_valid_bars`, 24봉 WAN-222)를 쓴다.
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
