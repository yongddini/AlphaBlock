"""이벤트 구동 존-지정가 엔진 — QC 포팅 골격 (WAN-181 파일럿).

정본 엔진(`backtest.zone_limit_backtest`)은 **셋업별 배치** 모델이다: 셋업 하나를
`simulate_zone_limit_trade`로 끝까지 독립 시뮬레이션한 뒤, 완성된 후보들을
`sequence_with_candidates`가 (진입시각, 청산시각) 정렬로 일괄 배치한다. QC의 `OnData`
루프는 그 배치를 허락하지 않는다 — 모든 상태를 **시간 순서 단일 패스**에서 증분으로
굴려야 한다. 이 모듈은 그 재배열이다.

## 무엇을 재사용하고 무엇을 새로 쓰나

**규칙 원천은 전부 정본에서 import 한다** (재구현 금지 — WAN-100의 교훈: 두 벌이
갈라지면 어느 쪽이 맞는지 알 수 없다):

- 지정가 사슬(밴드 → `deviation_entry_price` → 오프셋): `IntrabarLiveLimit` **그 객체**
  (라이브 러너 WAN-45가 쓰는 것과 같은 공개 별칭).
- 밴드 상태 머신: `RealtimeBand`(시딩·커밋·현재가 표본).
- 청산 산출: `IntrabarLiveLimit.resolve_exits`(고정 R 포함).
- 비용·사이징·펀딩: `_to_trade`(메이커 진입 · 테이커 청산 · `position_size` ·
  `cumulative_funding_cost`) — 회계가 정본과 비트 단위로 같아진다.

**새로 쓰는 것은 오케스트레이션뿐이다**: 셋업 여러 개의 주문 수명주기(등록 → 대기 →
취소/체결 → 청산)를 분봉 스위프 하나에서 병렬로 굴리고, 동시 1포지션 북을 **온라인**
으로 관리한다. 이 재배열이 정본과 같은 거래를 내는지가 `backtest.wan181_qc_pilot`
감사의 검증 대상이다.

## ⚠️ 정본과 의미가 갈릴 수 있는 딱 한 지점 — 동일 분 체결 타이

정본 시퀀서는 후보를 `(entry_time, exit_time)`으로 정렬한다 — 같은 분에 두 셋업이
체결되면 **청산이 이른 쪽**이 슬롯을 가진다. 그 청산 시각은 그 시점의 미래 정보라
온라인 북은 쓸 수 없다(실거래·QC도 마찬가지다). 이 엔진은 같은 분 타이를 **등록
순서**(탭 순서)로 깨고, 타이 발생 횟수를 `EventBacktestOutcome.same_minute_fill_ties`
로 세어 감사 표에 드러낸다 — 타이가 0이면 시퀀싱 축 불일치의 여지 자체가 없다.

같은 분이라도 **청산 → 신규 체결** 순서는 정본과 같다(정본 규칙 `entry_time <
busy_until`은 경계 일치를 허용한다): 스텝마다 열린 포지션의 청산을 먼저 판정하고
신규 체결을 그다음에 본다.

## 파일럿 지원 범위 (조용히 무시하지 않고 거부)

채택 기본값(`ConfluenceParams()`) 경로만 지지한다. 그 밖의 조합은 `ValueError`로
거부한다 — 지원 안 되는 파라미터를 받고 라벨만 붙이는 것이 이 저장소가 반복해서
데인 실패 유형이다(WAN-95/112/123).

- `entry_mode="zone_limit"` + `band_bar="intrabar_live"` 전용(정적 밴드·인과 밴드 미지원).
- `rsi_gate_mode="unconditional"` 전용 — 이 모드에서 진입 판정은 RSI를 읽지 않으므로
  (`simulate_zone_limit_trade`의 단락 평가, WAN-124가 기록) 실시간 RSI 상태 머신 포팅
  없이 정본과 동치다.
- `fill_dropout_rate=0` 전용 — 탈락 추첨은 정본의 **셋업 순회 순서**로 난수를 뽑아
  순회 순서가 다른 이 엔진과 재현이 원리적으로 어긋난다.
- 다중TF 겹침(WAN-126)·오버라이드 훅(WAN-133/137)·다중 포지션(WAN-103)·선 익절 미지원.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.substep import SubStep, ZoneLimitStatus, build_substeps
from backtest.sweep import timeframe_to_ms
from backtest.zone_limit_backtest import (
    IntrabarLiveLimit,
    ZoneLimitStats,
    _Candidate,
    _prepare_htf,
    _to_trade,
)
from common.costs import Liquidity
from data.models import FundingRate
from execution.sizing import position_size
from strategy.confluence import entry_candidate_signals
from strategy.indicators import atr
from strategy.models import (
    ConfluenceParams,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
)
from strategy.realtime_band import RealtimeBand


def validate_supported(params: ConfluenceParams) -> None:
    """이 엔진이 정본과의 동치를 주장할 수 있는 파라미터 조합인지 검사한다.

    벗어나면 `ValueError` — 파일럿 범위 밖 조합을 조용히 받으면 "포팅했다고 믿는데
    다른 규칙이 도는" WAN-95 부류의 실패가 된다.
    """
    if params.entry_mode != "zone_limit":
        raise ValueError(f"이벤트 엔진은 지정가(B안) 전용입니다: entry_mode={params.entry_mode!r}")
    deviation = params.deviation_filter
    if deviation is None or deviation.band_bar != "intrabar_live":
        band = None if deviation is None else deviation.band_bar
        raise ValueError(
            f"파일럿은 band_bar='intrabar_live' 전용입니다: {band!r} — 정적 밴드는 지정가가 "
            "탭 봉 상수라 다른 주문 수명주기를 갖고, 인과 밴드는 지연선 계약이 다릅니다."
        )
    if params.rsi_gate_mode != "unconditional":
        raise ValueError(
            f"파일럿은 rsi_gate_mode='unconditional' 전용입니다: {params.rsi_gate_mode!r} — "
            "다른 모드는 실시간 RSI 상태 머신 포팅이 필요합니다(파일럿 범위 밖)."
        )
    if params.fill_dropout_rate > 0.0:
        raise ValueError(
            "fill_dropout_rate>0은 지원하지 않습니다 — 탈락 추첨이 정본의 셋업 순회 순서에 "
            "묶여 있어 순회 순서가 다른 이벤트 엔진과 재현이 어긋납니다."
        )
    if params.min_rr is not None:
        raise ValueError(
            "band_bar='intrabar_live'는 min_rr 게이트를 지원하지 않습니다(정본과 동일)."
        )
    if params.long_max_deviation is not None:
        raise ValueError("long_max_deviation 게이트는 파일럿 범위 밖입니다.")
    if params.use_line_take_profit:
        raise ValueError("선 도달 익절(use_line_take_profit)은 파일럿 범위 밖입니다.")
    if params.source != "close":
        raise ValueError(f"band_bar='intrabar_live'는 source='close' 전용입니다: {params.source!r}")


@dataclass
class _SetupState:
    """주문판에 올라온 셋업 하나의 증분 상태.

    `simulate_zone_limit_trade`의 지역 변수들을 이벤트 패스에서 살아남는 상태로 옮긴
    것이다 — 필드 대응이 1:1이라 두 구현을 나란히 읽으며 감사할 수 있다.
    """

    signal: OrderBlockSignal
    is_long: bool
    stop_price: float
    live_limit: IntrabarLiveLimit
    invalidation_time: int | None
    current_htf: int
    active_stop: float
    htf_elapsed: int = 0
    order_rested: bool = False
    position_open: bool = False
    entry_time: int | None = None
    entry_price: float | None = None
    active_tp: float | None = None
    hold_high: float | None = None
    hold_low: float | None = None
    status: ZoneLimitStatus | None = None
    """터미널 상태. None이면 아직 진행 중."""
    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None

    @property
    def done(self) -> bool:
        return self.status is not None

    def excursions(self) -> tuple[float | None, float | None]:
        """(MFE_R, MAE_R) — `simulate_zone_limit_trade._excursions`와 같은 식."""
        if self.hold_high is None or self.hold_low is None or self.entry_price is None:
            return None, None
        risk = abs(self.entry_price - self.active_stop)
        if risk <= 0:
            return None, None
        if self.is_long:
            return (
                (self.hold_high - self.entry_price) / risk,
                (self.hold_low - self.entry_price) / risk,
            )
        return (
            (self.entry_price - self.hold_low) / risk,
            (self.entry_price - self.hold_high) / risk,
        )


@dataclass(frozen=True)
class EventBacktestOutcome:
    """이벤트 엔진 한 번의 실행 결과."""

    candidates: list[_Candidate]
    """셋업별 **가상** 체결·청산 후보 — 정본 `build_zone_limit_candidates`의 반환과 같은
    타입이라 감사 모듈이 필드 단위로 대조하고, 정본 시퀀서에 그대로 넣어 볼 수도 있다."""
    trades: list[Trade]
    """온라인 북(동시 1포지션)이 실제로 배치한 거래 — 정본 회계(`_to_trade`) 그대로."""
    stats: ZoneLimitStats
    same_minute_fill_ties: int
    """같은 분에 배치된 체결과 경합이 생겨 **등록 순서가 슬롯 주인을 정한** 횟수.

    정본 시퀀서는 같은 분 경합을 청산 시각(미래 정보)으로 깨므로, 이 값이 0이 아니면
    그만큼 정본과 다른 거래가 배치됐을 수 있다. 0이면 시퀀싱 축은 정본과 동치다."""
    skipped_fills: int
    """북이 잡혀 있어(동시 1포지션) 배치되지 못한 가상 체결 수 — 정본 시퀀서의 스킵에 대응."""


class ZoneLimitEventEngine:
    """분봉 이벤트를 받아 셋업 수명주기·동시 1포지션 북을 증분으로 굴리는 엔진.

    호출 계약(QC `OnData`가 지켜야 하는 것과 동일):

    1. 분봉을 **시간 오름차순으로 정확히 한 번씩** `on_minute`에 넣는다.
    2. 상위TF 봉 경계는 `on_minute`가 스스로 감지해 모든 활성 셋업의 밴드에 직전 봉
       마지막 분봉 종가를 커밋한다 — 별도 호출이 없다.
    3. 탭 시그널은 그 탭 봉 슬롯의 **첫 분봉을 넣기 전에** `register_signal`로 등록한다.
       (로컬 드라이버 `run_event_backtest`가 이 순서를 지키고, QC 셸도 같은 순서다.)
    4. 데이터가 끝나면 `finish`로 미청산 상태를 정리해 결과를 받는다.
    """

    def __init__(
        self,
        *,
        params: ConfluenceParams,
        cfg: BacktestConfig,
        timeframe: str,
        funding_rates: Sequence[FundingRate] | None = None,
    ) -> None:
        validate_supported(params)
        self._params = params
        self._cfg = cfg
        self._htf_ms = timeframe_to_ms(timeframe)
        self._funding_rates = funding_rates
        self._deviation = params.deviation_filter
        assert self._deviation is not None  # validate_supported가 보장.

        self._setups: list[_SetupState] = []
        self._candidates: list[_Candidate] = []
        self._running_close: float | None = None
        self._last_step: SubStep | None = None

        # 온라인 북(동시 1포지션). `_book_setup`이 열려 있는 동안 다른 가상 체결은
        # 배치되지 않는다 — 정본 시퀀서의 `entry_time < busy_until` 스킵에 대응한다.
        self._book_setup: _SetupState | None = None
        self._book_taken_at: int | None = None
        """북이 현재 점유로 넘어간 스텝 시각 — 같은 분 체결 경합(타이) 탐지용."""
        self._book_cash: float = cfg.initial_capital
        self._trades: list[Trade] = []

        self._eligible = 0
        self._filled = 0
        self._penetrations = 0
        self._same_minute_fill_ties = 0
        self._skipped_fills = 0

    # ------------------------------------------------------------------ #
    # 셋업 등록
    # ------------------------------------------------------------------ #

    def register_signal(
        self,
        signal: OrderBlockSignal,
        *,
        band_seed_closes: Sequence[float],
        width_atr: float | None,
    ) -> bool:
        """탭 시그널 하나를 주문판 후보로 등록한다. 게이트에 걸리면 False(등록 안 함).

        정본 `build_zone_limit_candidates`의 셋업별 사전 게이트와 같은 순서·같은 규칙이다.
        `band_seed_closes`는 탭 봉 **직전까지의** 상위TF 확정봉 종가(정본의 `closes[:cut]`),
        `width_atr`는 탭 봉 **직전 확정봉**의 ATR(WAN-158 룩어헤드 금지 규칙)이다 — 둘 다
        데이터 공급은 호출부 몫이고 **판정 규칙은 이 안**이다.
        """
        params = self._params
        if signal.status != "active":
            return False
        ob = signal.order_block
        is_long = ob.direction is OrderBlockDirection.BULLISH
        if (is_long and not self._cfg.allow_long) or (not is_long and not self._cfg.allow_short):
            return False
        if not is_long and not params.short_enabled:
            return False
        if params.max_zone_width_atr is not None and not params.zone_width_filter_passes(
            ob, width_atr
        ):
            return False

        assert self._deviation is not None
        stop_price = ob.bottom if is_long else ob.top
        setup = _SetupState(
            signal=signal,
            is_long=is_long,
            stop_price=stop_price,
            live_limit=IntrabarLiveLimit(
                band=RealtimeBand.seed_from_closed(list(band_seed_closes), self._deviation),
                order_block=ob,
                is_long=is_long,
                params=params,
                stop_price=stop_price,
                lines=[],
                trigger_time=signal.trigger_time,
            ),
            invalidation_time=ob.break_time if params.use_order_block_stop else None,
            current_htf=(signal.trigger_time // self._htf_ms) * self._htf_ms,
            active_stop=stop_price,
        )
        self._setups.append(setup)
        return True

    # ------------------------------------------------------------------ #
    # 분봉 이벤트
    # ------------------------------------------------------------------ #

    def on_minute(self, step: SubStep) -> None:
        """분봉 하나를 처리한다 — 경계 커밋 → 열린 포지션 청산 → 대기 주문 체결."""
        if self._last_step is not None and step.time <= self._last_step.time:
            raise ValueError(
                f"분봉이 시간 오름차순이 아닙니다: {self._last_step.time} -> {step.time}"
            )
        # 상위TF 경계: 직전 봉의 마지막 분봉 종가를 활성 셋업의 밴드에 커밋한다.
        # (`simulate_zone_limit_trade`의 경계 블록과 같은 규칙 — 셋업별 `current_htf`로
        # 자기 첫 슬롯 이후의 경계만 세므로, 등록 시점이 다른 셋업끼리 어긋나지 않는다.)
        for setup in self._setups:
            if not setup.done and step.htf_bar_time != setup.current_htf:
                if self._running_close is not None:
                    setup.live_limit.commit(self._running_close)
                setup.current_htf = step.htf_bar_time
                setup.htf_elapsed += 1
        self._running_close = step.close
        self._last_step = step

        # 1단: 열린 포지션의 청산을 먼저 판정한다 — 같은 분에 「청산 → 신규 체결」이
        # 겹치면 정본 시퀀서(`entry_time < busy_until`, 경계 일치 허용)와 같은 순서가 된다.
        for setup in self._setups:
            if not setup.done and setup.position_open:
                self._step_open_position(setup, step)

        # 2단: 대기 주문의 취소·체결(체결 즉시 같은 스텝 청산 재판정 포함).
        for setup in self._setups:
            if not setup.done and not setup.position_open:
                self._step_pending_order(setup, step)

    def _step_open_position(self, setup: _SetupState, step: SubStep) -> None:
        """보유 중 셋업의 극값 갱신·손절/익절 판정 (`simulate_zone_limit_trade`와 동일)."""
        setup.hold_high = step.high if setup.hold_high is None else max(setup.hold_high, step.high)
        setup.hold_low = step.low if setup.hold_low is None else min(setup.hold_low, step.low)
        stop_hit = (
            step.low <= setup.active_stop if setup.is_long else step.high >= setup.active_stop
        )
        tp_hit = setup.active_tp is not None and (
            step.high >= setup.active_tp if setup.is_long else step.low <= setup.active_tp
        )
        if stop_hit and (not tp_hit or self._params.stop_before_take_profit):
            self._close_position(setup, step.time, setup.active_stop, ExitReason.STOP_LOSS)
        elif tp_hit:
            assert setup.active_tp is not None
            self._close_position(setup, step.time, setup.active_tp, ExitReason.TAKE_PROFIT)

    def _step_pending_order(self, setup: _SetupState, step: SubStep) -> None:
        """대기 주문의 취소·재호가·체결 판정 (`simulate_zone_limit_trade`와 동일 순서)."""
        params = self._params
        if setup.invalidation_time is not None and step.time >= setup.invalidation_time:
            self._terminate(setup, ZoneLimitStatus.CANCELLED_INVALIDATED)
            return
        if params.limit_valid_bars is not None and setup.htf_elapsed >= params.limit_valid_bars:
            self._terminate(setup, ZoneLimitStatus.CANCELLED_EXPIRED)
            return
        current_limit = setup.live_limit.limit_price(step.close)
        if current_limit is None:
            return  # 워밍업이거나 밴드가 존보다 불리(WAN-75 규칙 3) — 지금은 주문이 없다.
        setup.order_rested = True
        penetration = current_limit * (params.fill_penetration_bps / 10_000.0)
        trigger = current_limit - penetration if setup.is_long else current_limit + penetration
        touched = step.low <= trigger if setup.is_long else step.high >= trigger
        if not touched:
            return
        # rsi_gate_mode="unconditional"(validate_supported가 보장)이라 게이트는 항상 통과.
        exits = setup.live_limit.resolve_exits(current_limit)
        if exits is None:
            self._terminate(setup, ZoneLimitStatus.CANCELLED_CONDITION_FAILED)
            return
        setup.active_stop, setup.active_tp = exits
        setup.position_open = True
        setup.entry_time = step.time
        setup.entry_price = current_limit
        self._try_book(setup, step)
        # 관통 방지: 체결 스텝에서 곧바로 청산을 재판정한다(정본과 동일).
        self._step_open_position(setup, step)

    # ------------------------------------------------------------------ #
    # 온라인 북 (동시 1포지션)
    # ------------------------------------------------------------------ #

    def _try_book(self, setup: _SetupState, step: SubStep) -> None:
        """가상 체결을 북에 배치 시도한다 — 정본 시퀀서의 온라인 판이다.

        북이 잡혀 있으면 스킵(정본의 `entry_time < busy_until`), 비어 있으면 사이징을
        먼저 확인해 수량 0이면 배치하지 않는다(정본의 `_to_trade -> None` 스킵 — 슬롯을
        잠그지 않아 다른 셋업이 그 자리를 쓸 수 있다).
        """
        if self._book_setup is not None:
            self._skipped_fills += 1
            if self._book_taken_at == step.time:
                # 같은 분에 먼저 처리된 체결이 슬롯을 가져갔다 — 정본은 이 경합을 청산
                # 시각(미래 정보)으로 깨므로, 등록 순서로 깬 여기와 다를 수 있다.
                self._same_minute_fill_ties += 1
            return
        assert setup.entry_price is not None
        if self._cfg.risk_sizing is not None:
            entry_fill = self._cfg.cost_model.entry_fill(
                setup.entry_price, is_long=setup.is_long, liquidity=Liquidity.MAKER
            )
            qty = position_size(
                equity=self._book_cash,
                entry_price=entry_fill,
                stop_price=setup.active_stop,
                params=self._cfg.risk_sizing,
            )
            if qty <= 0.0:
                return  # 사이징 스킵 — 북을 잠그지 않는다(정본과 동일).
        self._book_setup = setup
        self._book_taken_at = step.time

    def _release_book(self, setup: _SetupState, cand: _Candidate) -> None:
        """북에 배치된 셋업이 청산됐다 — 정본 `_to_trade`로 거래를 확정하고 북을 비운다."""
        if self._book_setup is not setup:
            return
        trade = _to_trade(cand, self._book_cash, self._cfg, self._funding_rates)
        self._book_setup = None
        self._book_taken_at = None
        if trade is None:  # 배치 시점 사이징과 같은 입력이라 원칙상 도달하지 않는다.
            return
        self._book_cash += trade.realized_pnl
        self._trades.append(trade)

    # ------------------------------------------------------------------ #
    # 터미널 처리
    # ------------------------------------------------------------------ #

    def _close_position(
        self, setup: _SetupState, exit_time: int, exit_price: float, reason: ExitReason
    ) -> None:
        setup.status = ZoneLimitStatus.FILLED_EXITED
        setup.exit_time = exit_time
        setup.exit_price = exit_price
        setup.exit_reason = reason
        cand = self._finalize_candidate(setup)
        if cand is not None:
            self._release_book(setup, cand)

    def _terminate(self, setup: _SetupState, status: ZoneLimitStatus) -> None:
        setup.status = status
        self._finalize_candidate(setup)

    def _finalize_candidate(self, setup: _SetupState) -> _Candidate | None:
        """터미널 상태의 셋업을 정본 집계 규칙대로 통계·후보에 반영한다.

        `build_zone_limit_candidates`의 시뮬레이션 후 블록과 같은 규칙: 주문이 한 번도
        걸리지 않았으면(eligible 아님) 세지 않고, 체결·청산이 확정된 셋업만 후보가 된다.
        """
        if not setup.order_rested:
            return None
        self._eligible += 1
        if not setup.position_open or setup.entry_time is None or setup.entry_price is None:
            return None
        self._filled += 1
        assert setup.exit_time is not None and setup.exit_price is not None
        assert setup.exit_reason is not None
        penetration = (
            setup.exit_reason is ExitReason.STOP_LOSS and setup.exit_time == setup.entry_time
        )
        if penetration:
            self._penetrations += 1
        mfe_r, mae_r = setup.excursions()
        cand = _Candidate(
            side=PositionSide.LONG if setup.is_long else PositionSide.SHORT,
            entry_time=setup.entry_time,
            entry_price=setup.entry_price,
            exit_time=setup.exit_time,
            exit_price=setup.exit_price,
            reason=setup.exit_reason,
            stop_price=setup.active_stop,
            penetration=penetration,
            order_block=setup.signal.order_block,
            tap_index=setup.signal.tap_index,
            zone_key=setup.signal.zone_key,
            trigger_time=setup.signal.trigger_time,
            mfe_r=mfe_r,
            mae_r=mae_r,
        )
        self._candidates.append(cand)
        return cand

    def finish(self) -> EventBacktestOutcome:
        """데이터 끝 — 미청산 셋업을 정본 규칙대로 정리하고 결과를 낸다."""
        last = self._last_step
        for setup in self._setups:
            if setup.done:
                continue
            if setup.position_open and last is not None:
                # 데이터 종료까지 보유 → 마지막 분봉 종가로 강제 청산(정본 END_OF_DATA).
                self._close_position(setup, last.time, last.close, ExitReason.END_OF_DATA)
            else:
                self._terminate(setup, ZoneLimitStatus.NO_TOUCH)
        stats = ZoneLimitStats(
            eligible=self._eligible,
            filled=self._filled,
            penetrations=self._penetrations,
            dropped=0,
        )
        return EventBacktestOutcome(
            candidates=self._candidates,
            trades=self._trades,
            stats=stats,
            same_minute_fill_ties=self._same_minute_fill_ties,
            skipped_fills=self._skipped_fills,
        )


def run_event_backtest(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    order_block_result: OrderBlockResult,
    funding_rates: Sequence[FundingRate] | None = None,
) -> EventBacktestOutcome:
    """로컬 드라이버 — QC 셸(`qc.algorithm`)이 QC 이벤트로 할 일을 저장소 데이터로 한다.

    분봉 스위프 순서(경계 커밋 → 그 슬롯 탭 시그널 등록 → 스텝 처리)가 QC `OnData`
    셸과 같은 계약이고, 탭 봉의 1분봉 커버 검사·밴드 시딩(`closes[:cut]`)·직전 확정봉
    ATR 등 **데이터 공급 규칙**은 정본 `build_zone_limit_candidates`와 같은 식이다.
    """
    frame = _prepare_htf(htf_df)
    engine = ZoneLimitEventEngine(
        params=params, cfg=cfg, timeframe=timeframe, funding_rates=funding_rates
    )
    if len(frame) == 0:
        return engine.finish()
    htf_ms = timeframe_to_ms(timeframe)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    substeps = build_substeps(df_1m, htf_ms)

    width_atr: list[float] | None = None
    if params.max_zone_width_atr is not None:
        width_atr = [float(v) for v in atr(frame, length=params.zone_width_atr_length).tolist()]

    time_to_pos = {t: i for i, t in enumerate(times)}
    signals = sorted(
        (
            s
            for s in entry_candidate_signals(order_block_result, params, times, closes, time_to_pos)
            if s.trigger_time in time_to_pos
        ),
        key=lambda s: s.trigger_time,
    )

    sig_idx = 0
    for step in substeps:
        # 이 스텝 시각까지 탭이 난 시그널을 등록한다. 탭 봉 슬롯에 1분봉 커버가 없으면
        # (= 첫 후속 분봉이 다른 슬롯이면) 정본과 같이 평가에서 제외한다.
        while sig_idx < len(signals) and signals[sig_idx].trigger_time <= step.time:
            signal = signals[sig_idx]
            sig_idx += 1
            tap_htf = (signal.trigger_time // htf_ms) * htf_ms
            if step.htf_bar_time != tap_htf:
                continue  # 탭 봉에 1분봉 커버 없음 → 평가 제외(정본과 동일).
            pos = time_to_pos[signal.trigger_time]
            cut = bisect.bisect_left(times, tap_htf)
            atr_prev = width_atr[pos - 1] if width_atr is not None and pos >= 1 else None
            engine.register_signal(signal, band_seed_closes=closes[:cut], width_atr=atr_prev)
        engine.on_minute(step)
    return engine.finish()
