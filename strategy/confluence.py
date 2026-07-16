"""오더블록 + RSI 진입 / EMA·VWMA 선 익절 / 오더블록 무효화 손절 전략 (WAN-23).

오더블록 탐지(`strategy.order_blocks`)가 만든 탭(tap) 시그널을 **기준 신호**로 두고,
RSI 과매수/과매도를 **필수 진입 조건**으로 확정한다. EMA·VWMA는 진입에 쓰지 않고
**익절 목표선**으로만 쓴다. 손절은 진입 근거 오더블록의 무효화(breaker)로 발생한다.
규칙·임계값은 모두 `ConfluenceParams`로 조정·on/off 가능하다.

익절(선 도달)과 손절(오더블록 무효화)은 모두 **동적**(봉마다 달라짐)이라, 전략이
진입가를 기준으로 청산 봉·참조가·사유를 미리 계산해 `PlannedExit`로 시그널에 실어
보낸다. 확정 진입은 `ConfluenceResult.order_block_signals`를 통해 WAN-8 백테스트
엔진(`backtest.run_backtest`)이 **바로 소비**할 수 있는 `OrderBlockSignal`(계획 청산
포함) 리스트로 변환된다.

입력 DataFrame은 오더블록·지표 모듈과 동일한 스키마(`open_time`(ms), `open`,
`high`, `low`, `close`, `volume`, 선택적 `closed`)를 따른다. `closed` 컬럼이 있으면
확정봉만 사용하고 `open_time` 오름차순으로 정렬한 뒤 계산한다.
"""

from __future__ import annotations

import logging
import math
from enum import StrEnum
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from strategy.indicators import atr, emas, rsi, sma, stdev, vwma
from strategy.models import (
    ConfluenceParams,
    DeviationFilterParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
    PlannedExit,
    SignalExitReason,
    deviation_entry_price,
)
from strategy.order_blocks import OrderBlockDetector

_logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


class SignalKind(StrEnum):
    """컨플루언스 신호 종류."""

    ENTRY = "entry"
    EXIT = "exit"


class IndicatorSnapshot(BaseModel):
    """신호 발생 봉에서의 지표 스냅샷(재현·디버깅용)."""

    model_config = ConfigDict(frozen=True)

    time: int
    close: float
    rsi: float | None
    lines: dict[str, float]
    """익절 목표선 스냅샷. 키는 `ema_<길이>`·`vwma_<길이>`, 값은 그 봉의 선 가격(NaN 제외)."""


class ConfluenceSignal(BaseModel):
    """컨플루언스 진입/청산 신호 하나.

    진입 신호(`kind=ENTRY`)는 오더블록 탭에 RSI 조건을 적용한 결과이며,
    `confirmed`가 True인 것만 실제 진입으로 채택된다. 확정 진입은 `planned_exit`에
    계획된 청산(익절 선 도달 또는 손절 오더블록 무효화)을 담는다. 청산 신호
    (`kind=EXIT`)는 그 계획 청산을 명시적 이벤트로 다시 내보낸 것이다.
    """

    model_config = ConfigDict(frozen=True)

    kind: SignalKind
    direction: OrderBlockDirection
    """진입은 진입 방향, 청산은 청산되는 포지션의 방향."""
    time: int
    """신호 봉의 `open_time`(ms)."""
    price: float
    """진입은 탭 봉 종가(진입 참조가), 청산은 청산 참조가."""
    confirmed: bool
    """진입 확정 여부(RSI 조건 충족). 청산은 항상 True."""
    rsi: float | None
    """신호(진입) 봉의 RSI. 워밍업(NaN)이면 None."""
    order_block: OrderBlock | None
    """근거 오더블록. 없으면 None."""
    indicators: IndicatorSnapshot
    planned_exit: PlannedExit | None = None
    """확정 진입이 계획한 청산. 진입가 너머에 익절선이 없고 무효화도 없으면 None."""
    exit_reason: SignalExitReason | None = None
    """청산 신호(`kind=EXIT`)의 사유. 진입은 None."""


class ConfluenceResult(BaseModel):
    """`ConfluenceStrategy.run()`의 반환값."""

    model_config = ConfigDict(frozen=True)

    params: ConfluenceParams
    entries: list[ConfluenceSignal]
    """평가된 모든 진입 후보(확정·기각 포함). `confirmed`로 구분."""
    exits: list[ConfluenceSignal]
    """확정 진입이 계획한 청산(익절·손절)을 명시적 이벤트로 내보낸 것. 시간 오름차순."""

    @property
    def confirmed_entries(self) -> list[ConfluenceSignal]:
        """RSI 조건을 통과한 진입 신호만."""
        return [e for e in self.entries if e.confirmed]

    @property
    def order_block_signals(self) -> list[OrderBlockSignal]:
        """확정 진입을 WAN-8 백테스트가 소비하는 `OrderBlockSignal`로 변환.

        `backtest.run_backtest(df, result.order_block_signals)` 형태로 바로
        사용한다. 확정 진입만 `status="active"`로 포함하며, 계획된 청산
        (`planned_exit`)을 함께 실어 보낸다.
        """
        signals: list[OrderBlockSignal] = []
        for entry in self.entries:
            if not entry.confirmed or entry.order_block is None:
                continue
            signals.append(
                OrderBlockSignal(
                    direction=entry.direction,
                    trigger_time=entry.time,
                    price=entry.price,
                    order_block=entry.order_block,
                    status="active",
                    planned_exit=entry.planned_exit,
                )
            )
        return signals


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """오더블록·지표 모듈과 동일한 입력 준비(확정봉 필터 → 시간 정렬)."""
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV DataFrame에 필요한 컬럼이 없습니다: {missing}")
    frame = df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    return frame.sort_values("open_time").reset_index(drop=True)


def _direction_sign(direction: OrderBlockDirection) -> int:
    return 1 if direction is OrderBlockDirection.BULLISH else -1


class ConfluenceStrategy:
    """오더블록+RSI 진입 / EMA·VWMA 선 익절 / 오더블록 무효화 손절 신호 생성기.

    사용법::

        strategy = ConfluenceStrategy()
        result = strategy.run(ohlcv_df)
        backtest_signals = result.order_block_signals  # WAN-8 백테스트 입력
    """

    def __init__(
        self,
        params: ConfluenceParams | None = None,
        order_block_params: OrderBlockParams | None = None,
    ) -> None:
        self.params = params or ConfluenceParams()
        self.order_block_params = order_block_params or OrderBlockParams()

    def run(
        self, df: pd.DataFrame, order_block_result: OrderBlockResult | None = None
    ) -> ConfluenceResult:
        params = self.params
        frame = _prepare(df)
        n = len(frame)
        if n == 0:
            return ConfluenceResult(params=params, entries=[], exits=[])

        times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
        highs = [float(v) for v in frame["high"].astype(float).tolist()]
        lows = [float(v) for v in frame["low"].astype(float).tolist()]
        closes = [float(v) for v in frame["close"].astype(float).tolist()]
        time_to_pos = {t: i for i, t in enumerate(times)}

        # 지표를 동일 정렬·필터 프레임 기준으로 계산(위치 인덱스 0..n-1 정렬).
        rsi_vals = [float(v) for v in rsi(df, length=params.rsi_length, source=params.source)]
        line_cols = self._line_columns(df)
        deviation_ema_vals = self._deviation_ema(df)
        filter_components = (
            self.deviation_filter_components(df, params.deviation_filter, params.source)
            if params.deviation_filter is not None
            else None
        )

        ob_result = order_block_result or OrderBlockDetector(self.order_block_params).run(df)

        entries: list[ConfluenceSignal] = []
        exits: list[ConfluenceSignal] = []

        for signal in entry_candidate_signals(ob_result, params, times, closes, time_to_pos):
            pos = time_to_pos.get(signal.trigger_time)
            if pos is None:
                continue
            ob = signal.order_block
            break_pos = self._break_pos(ob, time_to_pos)
            # 탭이 무효화 시점 이후면 활성 오더블록 탭이 아니므로 건너뛴다.
            if break_pos is not None and break_pos <= pos:
                continue

            entry = self._evaluate_entry(
                signal,
                pos,
                times,
                closes,
                rsi_vals,
                line_cols,
                deviation_ema_vals,
                filter_components,
            )
            if entry.confirmed:
                planned = self._plan_exit(
                    entry, pos, break_pos, n, times, highs, lows, closes, line_cols
                )
                if planned is None:
                    _logger.debug(
                        "확정 진입(time=%s, %s)에 익절 목표선·무효화가 없어 계획 청산 없음",
                        entry.time,
                        entry.direction.value,
                    )
                entry = entry.model_copy(update={"planned_exit": planned})
                if planned is not None:
                    exits.append(self._exit_signal(entry, planned, line_cols, time_to_pos))
            entries.append(entry)

        exits.sort(key=lambda s: s.time)
        return ConfluenceResult(params=params, entries=entries, exits=exits)

    # -- 지표/스냅샷 -----------------------------------------------------------

    def _line_columns(self, df: pd.DataFrame) -> dict[str, list[float]]:
        """익절 목표선(EMA·VWMA)을 위치 인덱스 정렬 배열로 계산해 모은다."""
        params = self.params
        cols: dict[str, list[float]] = {}
        ema_lengths = params.sorted_tp_ema_lengths
        if ema_lengths:
            ema_frame = emas(df, lengths=ema_lengths, source=params.source)
            for length in ema_lengths:
                cols[f"ema_{length}"] = [float(v) for v in ema_frame[f"ema_{length}"]]
        if params.tp_vwma_length is not None:
            key = f"vwma_{params.tp_vwma_length}"
            cols[key] = [
                float(v) for v in vwma(df, length=params.tp_vwma_length, source=params.source)
            ]
        return cols

    def _deviation_ema(self, df: pd.DataFrame) -> list[float] | None:
        """롱 이격도 게이트용 EMA(위치 인덱스 정렬). 게이트가 꺼져 있으면 계산하지 않는다."""
        params = self.params
        if params.long_max_deviation is None:
            return None
        ema_frame = emas(df, lengths=(params.long_deviation_gate_ema_length,), source=params.source)
        return [float(v) for v in ema_frame[f"ema_{params.long_deviation_gate_ema_length}"]]

    @staticmethod
    def deviation_filter_components(
        df: pd.DataFrame, filter_params: DeviationFilterParams, source: str
    ) -> tuple[list[float], list[float]]:
        """이격 필터(WAN-75)의 기준선(anchor)·폭(width) 시리즈를 위치 인덱스 정렬로 계산.

        밴드 값은 호출부가 `anchor - direction_sign*width`로 방향별 조합한다(롱은
        하단선, 숏은 상단선 — `deviation_entry_price` 참고). A안(`ConfluenceStrategy`)·
        B안(`backtest.zone_limit_backtest`)이 공유한다. 워밍업 구간은 `NaN`.
        """
        if filter_params.anchor == "sma":
            sma_vals = sma(df, length=filter_params.sma_length, source=source)
            anchor_vals = [float(v) for v in sma_vals]
        else:
            anchor_vals = [float(v) for v in _prepare(df)[source].astype(float)]

        if filter_params.width_kind == "pct":
            width_vals = [a * filter_params.width_value for a in anchor_vals]
        elif filter_params.width_kind == "stdev":
            sd_vals = [float(v) for v in stdev(df, length=filter_params.sma_length, source=source)]
            width_vals = [v * filter_params.width_value for v in sd_vals]
        else:  # "atr"
            atr_vals = [float(v) for v in atr(df, length=filter_params.atr_length)]
            width_vals = [v * filter_params.width_value for v in atr_vals]

        return anchor_vals, width_vals

    @staticmethod
    def deviation_band_at(
        pos: int,
        direction_sign: int,
        anchor_vals: list[float],
        width_vals: list[float],
        band_bar: Literal["tap", "prev_closed", "intrabar_live", "intrabar_causal"] = "tap",
    ) -> float | None:
        """탭 봉 `pos`에서 쓸 밴드 값(`anchor - direction_sign*width`). 판정 불가면 `None`.

        `band_bar="prev_closed"`(WAN-115)면 탭 봉이 아니라 **직전 확정봉**의 밴드를 읽는다
        — 탭 봉 자신의 SMA20은 그 봉 종가를 포함하므로 B안(봉 내부 체결)에서 룩어헤드다.
        구간 첫 봉(`pos=0`)에는 직전 봉이 없어 판정 불가다.

        `intrabar_live`(WAN-119)는 **봉 단위 함수인 여기서는 `tap`과 같다** — 이 함수를
        쓰는 A안은 탭 봉 **종가**에 진입하므로 그 시점의 "현재가"가 곧 탭 봉 종가이고,
        20번째 표본이 `tap`과 한 값으로 만난다(= A안에는 이 룩어헤드가 없다는 WAN-115
        관찰의 다른 표현이다). 봉 **내부**에서 현재가가 움직이는 성질은 봉 단위로
        표현할 수 없으므로, B안(지정가)은 이 함수 대신 서브스텝마다 값을 다시 내는
        `strategy.realtime_band.RealtimeBand`를 쓴다.

        `intrabar_causal`(WAN-120)은 **거부한다**. `intrabar_live`처럼 `tap`으로 접을 수
        없기 때문이다 — 그 모드가 `tap`과 만나는 건 A안 진입 시점의 현재가가 정확히 탭 봉
        종가라서인데, 이 모드가 쓰는 값은 **직전 1분봉 종가**라 탭 봉 종가와 다르고 봉
        단위 시리즈에는 그 값이 없다. 조용히 `tap`으로 접으면 "인과 라벨을 달고 룩어헤드
        값을 돌리는" 결과가 되므로(WAN-95의 교훈) 값을 지어내지 않고 거부한다.
        """
        if band_bar == "intrabar_causal":
            raise ValueError(
                "band_bar='intrabar_causal'는 봉 단위 밴드로 표현할 수 없습니다 — "
                "직전 1분봉 종가가 필요하므로 B안(지정가) 서브스텝 경로에서만 유효합니다."
            )
        band_pos = pos - 1 if band_bar == "prev_closed" else pos
        if band_pos < 0:
            return None
        anchor_val = anchor_vals[band_pos]
        width_val = width_vals[band_pos]
        if math.isnan(anchor_val) or math.isnan(width_val):
            return None
        return anchor_val - direction_sign * width_val

    @staticmethod
    def _break_pos(ob: OrderBlock | None, time_to_pos: dict[int, int]) -> int | None:
        """오더블록 무효화(breaker) 봉의 위치 인덱스. 무효화되지 않았으면 None."""
        if ob is None or ob.break_time is None:
            return None
        return time_to_pos.get(ob.break_time)

    @staticmethod
    def _lines_at(pos: int, line_cols: dict[str, list[float]]) -> dict[str, float]:
        """해당 봉의 익절 선 값들(NaN 제외)."""
        snapshot: dict[str, float] = {}
        for key, values in line_cols.items():
            value = values[pos]
            if not math.isnan(value):
                snapshot[key] = value
        return snapshot

    def _evaluate_entry(
        self,
        signal: OrderBlockSignal,
        pos: int,
        times: list[int],
        closes: list[float],
        rsi_vals: list[float],
        line_cols: dict[str, list[float]],
        deviation_ema_vals: list[float] | None,
        filter_components: tuple[list[float], list[float]] | None,
    ) -> ConfluenceSignal:
        params = self.params
        d = _direction_sign(signal.direction)
        rsi_val = rsi_vals[pos]
        rsi_opt = None if math.isnan(rsi_val) else rsi_val

        if params.rsi_gate_mode == "unconditional":
            # WAN-123: 게이트 자체가 없다 — 탭 순서·워밍업(NaN)을 가리지 않고 통과.
            # `none`과 달리 `rsi_opt is not None`을 요구하지 않는 것이 핵심이다.
            confirmed = True
        elif params.rsi_gate_mode == "first_tap_free" and signal.tap_index == 0:
            # WAN-81 갭A: 존(병합 존 포함) 확정 후 첫 탭은 RSI 무관(워밍업 NaN
            # 포함)하게 무조건 통과한다. 재탭(tap_index>=1)부터 extreme 규칙 적용.
            confirmed = True
        else:
            confirmed = rsi_opt is not None and params.rsi_gate_passes(d > 0, rsi_opt)

        if confirmed and d < 0 and not params.short_enabled:
            confirmed = False

        entry_price = signal.price
        if confirmed and filter_components is not None:
            assert params.deviation_filter is not None
            anchor_vals, width_vals = filter_components
            band = self.deviation_band_at(
                pos, d, anchor_vals, width_vals, params.deviation_filter.band_bar
            )
            if band is None:
                confirmed = False
            else:
                new_price = deviation_entry_price(d, signal.order_block, band)
                if new_price is None:
                    confirmed = False
                else:
                    entry_price = new_price

        lines = self._lines_at(pos, line_cols)

        if confirmed and params.min_rr is not None:
            confirmed = self._passes_min_rr(
                d, entry_price, signal.order_block, lines, params.min_rr
            )

        if confirmed and d > 0 and params.long_max_deviation is not None:
            assert deviation_ema_vals is not None
            confirmed = self._passes_deviation_gate(
                pos, closes, deviation_ema_vals, params.long_max_deviation
            )

        snapshot = IndicatorSnapshot(
            time=times[pos],
            close=closes[pos],
            rsi=rsi_opt,
            lines=lines,
        )
        return ConfluenceSignal(
            kind=SignalKind.ENTRY,
            direction=signal.direction,
            time=signal.trigger_time,
            price=entry_price,
            confirmed=confirmed,
            rsi=rsi_opt,
            order_block=signal.order_block,
            indicators=snapshot,
        )

    @staticmethod
    def _nearest_beyond(
        direction_sign: int, entry_price: float, lines: dict[str, float]
    ) -> float | None:
        """진입가 너머(롱=위·숏=아래)에 있는 선들 중 가장 가까운 값. 없으면 None."""
        if direction_sign > 0:
            beyond = [v for v in lines.values() if v > entry_price]
            return min(beyond) if beyond else None
        beyond = [v for v in lines.values() if v < entry_price]
        return max(beyond) if beyond else None

    @classmethod
    def _passes_min_rr(
        cls,
        direction_sign: int,
        entry_price: float,
        ob: OrderBlock,
        lines: dict[str, float],
        min_rr: float,
    ) -> bool:
        """최소 손익비 게이트(WAN-68). 잃을 거리 대비 먹을 거리 비율이 `min_rr` 이상인지."""
        boundary = ob.bottom if direction_sign > 0 else ob.top
        risk = entry_price - boundary if direction_sign > 0 else boundary - entry_price
        if risk <= 0:
            return False
        nearest = cls._nearest_beyond(direction_sign, entry_price, lines)
        reward = 0.0 if nearest is None else abs(nearest - entry_price)
        return (reward / risk) >= min_rr

    @staticmethod
    def _passes_deviation_gate(
        pos: int,
        closes: list[float],
        ema_vals: list[float],
        threshold: float,
    ) -> bool:
        """롱 이격도 게이트(WAN-68). `(종가−EMA)/종가`가 임계값보다 더 음수인지."""
        ema_val = ema_vals[pos]
        if math.isnan(ema_val):
            return False
        close = closes[pos]
        if close == 0:
            return False
        deviation = (close - ema_val) / close
        return deviation < threshold

    # -- 청산 계획 -------------------------------------------------------------

    def _plan_exit(
        self,
        entry: ConfluenceSignal,
        entry_pos: int,
        break_pos: int | None,
        n: int,
        times: list[int],
        highs: list[float],
        lows: list[float],
        closes: list[float],
        line_cols: dict[str, list[float]],
    ) -> PlannedExit | None:
        """진입가 기준으로 익절(선 도달)·손절(무효화) 중 먼저 오는 청산을 계획한다."""
        params = self.params
        d = _direction_sign(entry.direction)
        entry_price = entry.price
        stop_pos = break_pos if params.use_order_block_stop else None

        fixed_tp_target: float | None = None
        if params.take_profit_mode == "fixed_r":
            assert entry.order_block is not None
            fixed_tp_target = self._fixed_r_target(
                d, entry_price, entry.order_block, params.take_profit_r
            )

        for j in range(entry_pos + 1, n):
            sl_hit = stop_pos is not None and j == stop_pos
            if params.take_profit_mode == "fixed_r":
                tp_price = (
                    fixed_tp_target
                    if fixed_tp_target is not None
                    and self._reached(d, fixed_tp_target, highs[j], lows[j])
                    else None
                )
            else:
                tp_price = (
                    self._take_profit_price(
                        d, entry_price, highs[j], lows[j], self._lines_at(j, line_cols)
                    )
                    if params.use_line_take_profit
                    else None
                )
            # 동일 봉에서 손절·익절 동시 충족 시 기본은 손절 우선(보수적).
            if sl_hit and (tp_price is None or params.stop_before_take_profit):
                # sl_hit는 break_pos(오더블록 무효화 봉)가 있을 때만 True이고,
                # break_pos는 entry.order_block에서 유도되므로 여기선 항상 존재한다.
                assert entry.order_block is not None
                stop_price = self._stop_loss_price(d, entry.order_block, closes[j])
                return PlannedExit(
                    time=times[j], price=stop_price, reason=SignalExitReason.STOP_LOSS
                )
            if tp_price is not None:
                return PlannedExit(
                    time=times[j], price=tp_price, reason=SignalExitReason.TAKE_PROFIT
                )
        return None

    @staticmethod
    def _stop_loss_price(direction_sign: int, ob: OrderBlock, close_price: float) -> float:
        """손절 체결가: 무효화 봉 종가를 오더블록 무효화 경계로 clamp한다(WAN-65).

        무효화(breaker)는 저가/고가(wick) 기준으로 판정되므로, 무효화 봉이 반전해
        경계 반대편(진입가에 유리한 방향)으로 마감하면 종가를 그대로 쓸 경우
        "손절인데 이익"이라는 모순이 생긴다. 종가와 경계 중 진입가에 더 불리한
        쪽을 체결가로 쓴다 — 경계(`ob.bottom`/`ob.top`)는 탭 진입가보다 항상
        불리하므로, 이렇게 하면 손절은 절대 이익을 낼 수 없다.
        """
        boundary = ob.bottom if direction_sign > 0 else ob.top
        if direction_sign > 0:
            return min(close_price, boundary)
        return max(close_price, boundary)

    @classmethod
    def _take_profit_price(
        cls,
        direction_sign: int,
        entry_price: float,
        high: float,
        low: float,
        lines: dict[str, float],
    ) -> float | None:
        """진입가 너머 가장 가까운 선에 봉이 도달했으면 그 선 가격, 아니면 None."""
        nearest = cls._nearest_beyond(direction_sign, entry_price, lines)
        if nearest is None:
            return None
        reached = high >= nearest if direction_sign > 0 else low <= nearest
        return nearest if reached else None

    @staticmethod
    def _fixed_r_target(
        direction_sign: int, entry_price: float, ob: OrderBlock, r: float
    ) -> float | None:
        """`take_profit_mode="fixed_r"`의 고정 익절가(WAN-73).

        진입가로부터 진입 근거 오더블록 무효화 경계까지의 거리(위험 1R)의 `r`배를
        진입가 너머(롱=위·숏=아래)에 둔다. 진입 시점에 한 번만 계산하고 이후 봉마다
        재평가하지 않는다(선 기반 익절과 달리 고정).
        """
        boundary = ob.bottom if direction_sign > 0 else ob.top
        risk = entry_price - boundary if direction_sign > 0 else boundary - entry_price
        if risk <= 0:
            return None
        return entry_price + direction_sign * risk * r

    @staticmethod
    def _reached(direction_sign: int, target: float, high: float, low: float) -> bool:
        return high >= target if direction_sign > 0 else low <= target

    def _exit_signal(
        self,
        entry: ConfluenceSignal,
        planned: PlannedExit,
        line_cols: dict[str, list[float]],
        time_to_pos: dict[int, int],
    ) -> ConfluenceSignal:
        pos = time_to_pos[planned.time]
        snapshot = IndicatorSnapshot(
            time=planned.time,
            close=planned.price,
            rsi=None,
            lines=self._lines_at(pos, line_cols),
        )
        return ConfluenceSignal(
            kind=SignalKind.EXIT,
            direction=entry.direction,
            time=planned.time,
            price=planned.price,
            confirmed=True,
            rsi=None,
            order_block=entry.order_block,
            indicators=snapshot,
            exit_reason=planned.reason,
        )


def entry_candidate_signals(
    ob_result: OrderBlockResult,
    params: ConfluenceParams,
    times: list[int],
    closes: list[float],
    time_to_pos: dict[int, int],
) -> list[OrderBlockSignal]:
    """진입 후보 시그널(WAN-73/81). A안(`ConfluenceStrategy`)·B안
    (`backtest.zone_limit_backtest`)이 공유한다.

    `retap_mode="once"`면 오더블록 탐지기가 존(병합 존 포함)당 첫 탭 하나로 제한해
    낸 `ob_result.signals`를 그대로 쓴다. `retap_mode="every_tap"`(기본, WAN-81)이면
    존이 무효화되기 전까지의 **모든** 탭(재탭 포함, `tap_index` 부착)을 담은
    `ob_result.retap_signals`를 쓴다 — `combine_obs=True`면 이 시그널도 병합 존
    경계·병합 상태 기준으로 생성돼 있다(WAN-81 갭B: 과거엔 재탭 경로가 병합을
    무시하고 원본 존 단위로 되돌아갔다). 각 시그널의 `tap_index`는
    `rsi_gate_mode="first_tap_free"`가 첫 탭 면제를 판정하는 데 쓰인다(갭A).
    동시 포지션 1개 제약은 백테스트 엔진(플랫일 때만 진입)이 이미 강제하므로
    별도 상태 추적 없이 후보를 그대로 늘어놓아도 "청산 후 재진입"이 자연히 성립한다.
    """
    if params.retap_mode == "once":
        return ob_result.signals
    return ob_result.retap_signals


def generate_confluence_signals(
    df: pd.DataFrame,
    params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    order_block_result: OrderBlockResult | None = None,
) -> ConfluenceResult:
    """`ConfluenceStrategy(...).run(...)`의 편의 함수."""
    return ConfluenceStrategy(params, order_block_params).run(df, order_block_result)


def fixed_r_take_profit_price(
    direction: OrderBlockDirection, entry_price: float, order_block: OrderBlock, r: float
) -> float | None:
    """`take_profit_mode="fixed_r"`의 고정 익절 목표가(WAN-73).

    미래 봉 없이 진입가·오더블록만으로 진입 시점에 확정되므로(`_fixed_r_target`과
    동일 산식), 백테스트 엔진뿐 아니라 live 알림·집행 경로(WAN-85)도 이 함수로 같은
    값을 계산해 화면 표기와 실제 청산 목표가 어긋나지 않게 한다.
    """
    return ConfluenceStrategy._fixed_r_target(
        _direction_sign(direction), entry_price, order_block, r
    )
