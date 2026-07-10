"""오더블록 + 지표(RSI·EMA·VWMA) 컨플루언스 진입·청산 규칙 (WAN-18).

오더블록 탐지(`strategy.order_blocks`)가 만든 탭(tap) 시그널을 **기준 신호**로 두고,
기술지표(`strategy.indicators`)로 필터·확정하는 규칙 계층이다. 세 게이트(EMA 추세
배열·RSI 과매수/과매도·VWMA 추세)의 합의 점수로 진입을 확정하고, 지표 이탈 시
청산 신호를 낸다. 규칙·임계값은 모두 `ConfluenceParams`로 조정·on/off 가능하다.

출력 `ConfluenceSignal`은 타임스탬프·방향·강도(strength)를 담는 시그널 인터페이스로,
확정 진입 신호는 `ConfluenceResult.order_block_signals`를 통해 WAN-8 백테스트
엔진(`backtest.run_backtest`)이 **바로 소비**할 수 있는 `OrderBlockSignal` 리스트로
변환된다. 지표 이탈 청산 신호는 후속 주문 실행(WAN-9)이 소비한다.

입력 DataFrame은 오더블록·지표 모듈과 동일한 스키마(`open_time`(ms), `open`,
`high`, `low`, `close`, `volume`, 선택적 `closed`)를 따른다. `closed` 컬럼이 있으면
확정봉만 사용하고 `open_time` 오름차순으로 정렬한 뒤 계산한다.
"""

from __future__ import annotations

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
)
from strategy.order_blocks import OrderBlockDetector

_REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


class SignalKind(StrEnum):
    """컨플루언스 신호 종류."""

    ENTRY = "entry"
    EXIT = "exit"


class ExitTrigger(StrEnum):
    """지표 이탈 청산의 발동 사유."""

    TREND_FLIP = "trend_flip"
    """EMA 추세가 포지션과 반대로 뒤집힘."""
    VWMA_CROSS = "vwma_cross"
    """종가가 VWMA를 포지션과 반대 방향으로 돌파."""


class IndicatorSnapshot(BaseModel):
    """신호 발생 봉에서의 지표 스냅샷(재현·디버깅용)."""

    model_config = ConfigDict(frozen=True)

    time: int
    close: float
    rsi: float | None
    vwma: float | None
    ema_trend: int
    """+1=정배열(상승), -1=역배열(하락), 0=중립/워밍업."""
    emas: dict[str, float]


class ConfluenceSignal(BaseModel):
    """컨플루언스 진입/청산 신호 하나 (타임스탬프·방향·강도).

    진입 신호(`kind=ENTRY`)는 오더블록 탭에 지표 게이트를 적용한 결과이며,
    `confirmed`가 True인 것만 실제 진입으로 채택된다. 청산 신호(`kind=EXIT`)는
    확정 진입 이후 첫 지표 이탈 봉에서 생성된다.
    """

    model_config = ConfigDict(frozen=True)

    kind: SignalKind
    direction: OrderBlockDirection
    """진입은 진입 방향, 청산은 청산되는 포지션의 방향."""
    time: int
    """신호 봉의 `open_time`(ms)."""
    price: float
    """신호 봉의 종가(진입/청산 참조가)."""
    strength: float
    """신호 강도 [0, 1]. 진입은 게이트 합의 점수 비율, 청산은 1.0."""
    score: int
    """게이트 표의 합(진입만 유효). 청산은 0."""
    threshold: int
    """진입 확정 임계 점수(진입만 유효)."""
    confirmed: bool
    """진입 확정 여부. 청산은 항상 True."""
    order_block: OrderBlock | None
    """근거 오더블록(진입) 또는 진입 시 오더블록(청산). 없으면 None."""
    indicators: IndicatorSnapshot
    exit_trigger: ExitTrigger | None = None


class ConfluenceResult(BaseModel):
    """`ConfluenceStrategy.run()`의 반환값."""

    model_config = ConfigDict(frozen=True)

    params: ConfluenceParams
    entries: list[ConfluenceSignal]
    """평가된 모든 진입 후보(확정·기각 포함). `confirmed`로 구분."""
    exits: list[ConfluenceSignal]
    """확정 진입 이후 지표 이탈 청산 신호."""

    @property
    def confirmed_entries(self) -> list[ConfluenceSignal]:
        """지표 컨플루언스를 통과한 진입 신호만."""
        return [e for e in self.entries if e.confirmed]

    @property
    def order_block_signals(self) -> list[OrderBlockSignal]:
        """확정 진입을 WAN-8 백테스트가 소비하는 `OrderBlockSignal`로 변환.

        `backtest.run_backtest(df, result.order_block_signals)` 형태로 바로
        사용한다. 확정 진입만 `status="active"`로 포함한다.
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


def _ema_trend(values: list[float], required_pairs: int) -> int:
    """짧은→긴 길이 순 EMA 값 배열에서 추세를 판정.

    정배열(상승)=짧은 EMA가 위 → 값이 내림차순, 역배열(하락)=오름차순.
    어느 하나라도 NaN이면 0(중립). `required_pairs` 이상의 인접쌍이 한 방향으로
    정렬돼야 그 추세로 인정한다.
    """
    if any(math.isnan(v) for v in values):
        return 0
    descending = sum(1 for k in range(len(values) - 1) if values[k] > values[k + 1])
    ascending = sum(1 for k in range(len(values) - 1) if values[k] < values[k + 1])
    if descending >= required_pairs:
        return 1
    if ascending >= required_pairs:
        return -1
    return 0


class ConfluenceStrategy:
    """오더블록 + 지표 컨플루언스 진입·청산 신호 생성기.

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
        closes = [float(c) for c in frame["close"].astype(float).tolist()]
        time_to_pos = {t: i for i, t in enumerate(times)}

        # 지표를 동일 정렬·필터 프레임 기준으로 계산(위치 인덱스 0..n-1 정렬).
        rsi_vals = [float(v) for v in rsi(df, length=params.rsi_length, source=params.source)]
        vwma_vals = [float(v) for v in vwma(df, length=params.vwma_length, source=params.source)]
        sorted_lengths = sorted(params.ema_lengths)
        ema_frame = emas(df, lengths=sorted_lengths, source=params.source)
        ema_cols = {
            length: [float(v) for v in ema_frame[f"ema_{length}"]] for length in sorted_lengths
        }

        ob_result = order_block_result or OrderBlockDetector(self.order_block_params).run(df)

        entries: list[ConfluenceSignal] = []
        exits: list[ConfluenceSignal] = []

        for signal in ob_result.signals:
            if signal.status != "active":
                continue
            pos = time_to_pos.get(signal.trigger_time)
            if pos is None:
                continue
            entry = self._evaluate_entry(
                signal, pos, times, closes, rsi_vals, vwma_vals, ema_cols, sorted_lengths
            )
            entries.append(entry)
            if entry.confirmed:
                exit_signal = self._find_exit(
                    entry, pos, n, times, closes, vwma_vals, ema_cols, sorted_lengths
                )
                if exit_signal is not None:
                    exits.append(exit_signal)

        exits.sort(key=lambda s: s.time)
        return ConfluenceResult(params=params, entries=entries, exits=exits)

    def _snapshot(
        self,
        pos: int,
        times: list[int],
        closes: list[float],
        rsi_vals: list[float],
        vwma_vals: list[float],
        ema_cols: dict[int, list[float]],
        sorted_lengths: list[int],
    ) -> tuple[IndicatorSnapshot, float | None, float | None, int]:
        rsi_val = rsi_vals[pos]
        vwma_val = vwma_vals[pos]
        ema_values = [ema_cols[length][pos] for length in sorted_lengths]
        trend = _ema_trend(ema_values, self.params.required_aligned_pairs)
        rsi_opt = None if math.isnan(rsi_val) else rsi_val
        vwma_opt = None if math.isnan(vwma_val) else vwma_val
        snapshot = IndicatorSnapshot(
            time=times[pos],
            close=closes[pos],
            rsi=rsi_opt,
            vwma=vwma_opt,
            ema_trend=trend,
            emas={f"ema_{length}": ema_cols[length][pos] for length in sorted_lengths},
        )
        return snapshot, rsi_opt, vwma_opt, trend

    def _evaluate_entry(
        self,
        signal: OrderBlockSignal,
        pos: int,
        times: list[int],
        closes: list[float],
        rsi_vals: list[float],
        vwma_vals: list[float],
        ema_cols: dict[int, list[float]],
        sorted_lengths: list[int],
    ) -> ConfluenceSignal:
        params = self.params
        d = _direction_sign(signal.direction)
        snapshot, rsi_opt, vwma_opt, trend = self._snapshot(
            pos, times, closes, rsi_vals, vwma_vals, ema_cols, sorted_lengths
        )
        close = closes[pos]

        score = 0
        if params.use_ema_trend:
            score += 1 if trend == d else (-1 if trend == -d else 0)
        if params.use_rsi_gate:
            score += self._rsi_vote(d, rsi_opt)
        if params.use_vwma_trend:
            score += self._vwma_vote(d, close, vwma_opt)

        enabled = params.enabled_gate_count
        threshold = params.entry_threshold
        confirmed = score >= threshold
        strength = 1.0 if enabled == 0 else min(1.0, max(0.0, score / enabled))

        return ConfluenceSignal(
            kind=SignalKind.ENTRY,
            direction=signal.direction,
            time=signal.trigger_time,
            price=signal.price,
            strength=strength,
            score=score,
            threshold=threshold,
            confirmed=confirmed,
            order_block=signal.order_block,
            indicators=snapshot,
        )

    def _rsi_vote(self, direction_sign: int, rsi_val: float | None) -> int:
        """RSI 과매수/과매도 게이트 표. NaN(워밍업)은 0.

        롱은 과매수(>=overbought)면 −1, 아니면 +1. 숏은 과매도(<=oversold)면 −1,
        아니면 +1.
        """
        if rsi_val is None:
            return 0
        if direction_sign > 0:
            return -1 if rsi_val >= self.params.rsi_overbought else 1
        return -1 if rsi_val <= self.params.rsi_oversold else 1

    @staticmethod
    def _vwma_vote(direction_sign: int, close: float, vwma_val: float | None) -> int:
        if vwma_val is None:
            return 0
        if direction_sign > 0:
            return 1 if close >= vwma_val else -1
        return 1 if close <= vwma_val else -1

    def _find_exit(
        self,
        entry: ConfluenceSignal,
        entry_pos: int,
        n: int,
        times: list[int],
        closes: list[float],
        vwma_vals: list[float],
        ema_cols: dict[int, list[float]],
        sorted_lengths: list[int],
    ) -> ConfluenceSignal | None:
        params = self.params
        d = _direction_sign(entry.direction)
        for j in range(entry_pos + 1, n):
            trigger: ExitTrigger | None = None
            if params.exit_on_trend_flip:
                ema_values = [ema_cols[length][j] for length in sorted_lengths]
                trend = _ema_trend(ema_values, params.required_aligned_pairs)
                if trend == -d:
                    trigger = ExitTrigger.TREND_FLIP
            if trigger is None and params.exit_on_vwma_cross:
                vwma_j = vwma_vals[j]
                if not math.isnan(vwma_j):
                    crossed = closes[j] < vwma_j if d > 0 else closes[j] > vwma_j
                    if crossed:
                        trigger = ExitTrigger.VWMA_CROSS
            if trigger is not None:
                snapshot, _, _, _ = self._snapshot(
                    j, times, closes, [math.nan] * n, vwma_vals, ema_cols, sorted_lengths
                )
                return ConfluenceSignal(
                    kind=SignalKind.EXIT,
                    direction=entry.direction,
                    time=times[j],
                    price=closes[j],
                    strength=1.0,
                    score=0,
                    threshold=0,
                    confirmed=True,
                    order_block=entry.order_block,
                    indicators=snapshot,
                    exit_trigger=trigger,
                )
        return None


def generate_confluence_signals(
    df: pd.DataFrame,
    params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    order_block_result: OrderBlockResult | None = None,
) -> ConfluenceResult:
    """`ConfluenceStrategy(...).run(...)`의 편의 함수."""
    return ConfluenceStrategy(params, order_block_params).run(df, order_block_result)
