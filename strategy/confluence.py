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

import pandas as pd
from pydantic import BaseModel, ConfigDict

from strategy.indicators import emas, rsi, vwma
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
    PlannedExit,
    SignalExitReason,
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

        ob_result = order_block_result or OrderBlockDetector(self.order_block_params).run(df)

        entries: list[ConfluenceSignal] = []
        exits: list[ConfluenceSignal] = []

        for signal in ob_result.signals:
            pos = time_to_pos.get(signal.trigger_time)
            if pos is None:
                continue
            ob = signal.order_block
            break_pos = self._break_pos(ob, time_to_pos)
            # 탭이 무효화 시점 이후면 활성 오더블록 탭이 아니므로 건너뛴다.
            if break_pos is not None and break_pos <= pos:
                continue

            entry = self._evaluate_entry(signal, pos, times, closes, rsi_vals, line_cols)
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
    ) -> ConfluenceSignal:
        params = self.params
        d = _direction_sign(signal.direction)
        rsi_val = rsi_vals[pos]
        rsi_opt = None if math.isnan(rsi_val) else rsi_val

        confirmed = rsi_opt is not None and (
            rsi_opt <= params.rsi_oversold if d > 0 else rsi_opt >= params.rsi_overbought
        )

        snapshot = IndicatorSnapshot(
            time=times[pos],
            close=closes[pos],
            rsi=rsi_opt,
            lines=self._lines_at(pos, line_cols),
        )
        return ConfluenceSignal(
            kind=SignalKind.ENTRY,
            direction=signal.direction,
            time=signal.trigger_time,
            price=signal.price,
            confirmed=confirmed,
            rsi=rsi_opt,
            order_block=signal.order_block,
            indicators=snapshot,
        )

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

        for j in range(entry_pos + 1, n):
            sl_hit = stop_pos is not None and j == stop_pos
            tp_price = (
                self._take_profit_price(
                    d, entry_price, highs[j], lows[j], self._lines_at(j, line_cols)
                )
                if params.use_line_take_profit
                else None
            )
            # 동일 봉에서 손절·익절 동시 충족 시 기본은 손절 우선(보수적).
            if sl_hit and (tp_price is None or params.stop_before_take_profit):
                return PlannedExit(
                    time=times[j], price=closes[j], reason=SignalExitReason.STOP_LOSS
                )
            if tp_price is not None:
                return PlannedExit(
                    time=times[j], price=tp_price, reason=SignalExitReason.TAKE_PROFIT
                )
        return None

    @staticmethod
    def _take_profit_price(
        direction_sign: int,
        entry_price: float,
        high: float,
        low: float,
        lines: dict[str, float],
    ) -> float | None:
        """진입가 너머 가장 가까운 선에 봉이 도달했으면 그 선 가격, 아니면 None."""
        if direction_sign > 0:
            beyond = [v for v in lines.values() if v > entry_price]
            if not beyond:
                return None
            nearest = min(beyond)
            return nearest if high >= nearest else None
        beyond = [v for v in lines.values() if v < entry_price]
        if not beyond:
            return None
        nearest = max(beyond)
        return nearest if low <= nearest else None

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


def generate_confluence_signals(
    df: pd.DataFrame,
    params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    order_block_result: OrderBlockResult | None = None,
) -> ConfluenceResult:
    """`ConfluenceStrategy(...).run(...)`의 편의 함수."""
    return ConfluenceStrategy(params, order_block_params).run(df, order_block_result)
