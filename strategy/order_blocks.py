"""오더블록(Order Block) 탐지 & 시그널 생성.

Fluxchart "Volumized Order Blocks" (TradingView, MPL-2.0)의 탐지 로직을
`strategy/reference/README.md` 명세를 기준으로 pandas OHLCV 입력에 대해
이식한 순수 함수/클래스. 원본은 존 탐지·무효화까지만 하며, 진입 시그널
레이어는 AlphaBlock의 확장이다 (`strategy/reference/README.md` 참고).

입력 DataFrame은 `data.storage.OhlcvStore.load()`가 반환하는 스키마
(`open_time`(ms), `open`, `high`, `low`, `close`, `volume`, 선택적 `closed`)
를 따른다. `closed` 컬럼이 있으면 확정봉(`closed=True`)만 사용한다 — 원본이
`barstate.isconfirmed`에서만 갱신하는 것과 동일한 제약.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
)

_REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


@dataclass(eq=False)
class _SwingPoint:
    """지연 스윙 지점. 원본 `obSwing`에 대응."""

    index: int
    price: float
    crossed: bool = False


@dataclass(eq=False)
class _RawOrderBlock:
    """탐지 진행 중 상태를 갖는 오더블록. 원본 `orderBlockInfo`에 대응."""

    top: float
    bottom: float
    ob_volume: float
    direction: OrderBlockDirection
    start_time: int
    confirmed_time: int
    ob_low_volume: float
    ob_high_volume: float
    breaker: bool = False
    break_time: int | None = None

    def to_model(self, *, combined: bool = False) -> OrderBlock:
        return OrderBlock(
            direction=self.direction,
            top=self.top,
            bottom=self.bottom,
            start_time=self.start_time,
            confirmed_time=self.confirmed_time,
            ob_volume=self.ob_volume,
            ob_low_volume=self.ob_low_volume,
            ob_high_volume=self.ob_high_volume,
            breaker=self.breaker,
            break_time=self.break_time,
            combined=combined,
        )


def _true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    n = len(highs)
    tr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
    return tr


def _wilder_rma(values: list[float], length: int) -> list[float | None]:
    """Wilder's RMA (`ta.rma`). 처음 `length-1`개는 `None`(미확정)."""
    n = len(values)
    result: list[float | None] = [None] * n
    if n < length:
        return result
    seed = sum(values[:length]) / length
    result[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = (prev * (length - 1) + values[i]) / length
        result[i] = prev
    return result


def _atr(
    highs: list[float], lows: list[float], closes: list[float], length: int
) -> list[float | None]:
    return _wilder_rma(_true_range(highs, lows, closes), length)


def _rolling_max(values: list[float], length: int) -> list[float]:
    result = pd.Series(values).rolling(window=length, min_periods=1).max().tolist()
    return [float(v) for v in result]


def _rolling_min(values: list[float], length: int) -> list[float]:
    result = pd.Series(values).rolling(window=length, min_periods=1).min().tolist()
    return [float(v) for v in result]


def _combine_same_direction(obs: list[_RawOrderBlock], now: int) -> list[OrderBlock]:
    """겹치는(IoU 교집합>0) 동일 방향 존을 병합. 원본 `combineOBsFunc`에 대응."""

    def area(ob: _RawOrderBlock) -> float:
        end = ob.break_time if ob.break_time is not None else now
        return (end - ob.start_time) * (ob.top - ob.bottom)

    def touch(a: _RawOrderBlock, b: _RawOrderBlock) -> bool:
        a_end = a.break_time if a.break_time is not None else now
        b_end = b.break_time if b.break_time is not None else now
        intersection_time = max(0, min(a_end, b_end) - max(a.start_time, b.start_time))
        intersection_price = max(0.0, min(a.top, b.top) - max(a.bottom, b.bottom))
        intersection = intersection_time * intersection_price
        union = area(a) + area(b) - intersection
        if union <= 0:
            return False
        return (intersection / union) * 100.0 > 0

    items = list(obs)
    combined_flags = [False] * len(items)
    merged = True
    while merged:
        merged = False
        for i in range(len(items)):
            for j in range(len(items)):
                if i == j:
                    continue
                a, b = items[i], items[j]
                if touch(a, b):
                    new_ob = _RawOrderBlock(
                        top=max(a.top, b.top),
                        bottom=min(a.bottom, b.bottom),
                        ob_volume=a.ob_volume + b.ob_volume,
                        direction=a.direction,
                        start_time=min(a.start_time, b.start_time),
                        confirmed_time=min(a.confirmed_time, b.confirmed_time),
                        ob_low_volume=a.ob_low_volume + b.ob_low_volume,
                        ob_high_volume=a.ob_high_volume + b.ob_high_volume,
                        breaker=a.breaker or b.breaker,
                        break_time=(
                            max(a.break_time, b.break_time)
                            if a.break_time is not None and b.break_time is not None
                            else (a.break_time if a.break_time is not None else b.break_time)
                        ),
                    )
                    remove_indices = {i, j}
                    items = [x for k, x in enumerate(items) if k not in remove_indices]
                    combined_flags = [
                        f for k, f in enumerate(combined_flags) if k not in remove_indices
                    ]
                    items.append(new_ob)
                    combined_flags.append(True)
                    merged = True
                    break
            if merged:
                break

    return [ob.to_model(combined=flag) for ob, flag in zip(items, combined_flags, strict=True)]


def _generate_signals(
    order_blocks: list[OrderBlock],
    times: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[OrderBlockSignal]:
    """활성(비-breaker) 존에 가격이 재진입(tap)하면 진입 후보 시그널 생성.

    원본에는 없는 AlphaBlock 확장(기본 골격). 존이 확정된 이후 첫 재진입만
    시그널로 기록하고, breaker로 전환된 존은 `status="cancelled"`로 표시한다.
    세부 규칙(리테스트 확인, 손절 등)은 WAN-8/9에서 확정한다.
    """
    signals: list[OrderBlockSignal] = []
    n = len(times)
    for ob in order_blocks:
        start_pos = 0
        while start_pos < n and times[start_pos] <= ob.confirmed_time:
            start_pos += 1
        end_pos = n
        if ob.break_time is not None:
            pos = start_pos
            while pos < n and times[pos] < ob.break_time:
                pos += 1
            end_pos = min(end_pos, pos + 1)

        for i in range(start_pos, end_pos):
            if lows[i] <= ob.top and highs[i] >= ob.bottom:
                signals.append(
                    OrderBlockSignal(
                        direction=ob.direction,
                        trigger_time=times[i],
                        price=closes[i],
                        order_block=ob,
                        status="cancelled" if ob.breaker else "active",
                    )
                )
                break
    return signals


class OrderBlockDetector:
    """오더블록 탐지 & 시그널 생성기.

    사용법::

        detector = OrderBlockDetector(OrderBlockParams())
        result = detector.run(ohlcv_df)
    """

    def __init__(self, params: OrderBlockParams | None = None) -> None:
        self.params = params or OrderBlockParams()

    def run(self, df: pd.DataFrame) -> OrderBlockResult:
        frame = self._prepare(df)
        n = len(frame)
        if n == 0:
            return OrderBlockResult(order_blocks=[], signals=[])

        highs = frame["high"].astype(float).tolist()
        lows = frame["low"].astype(float).tolist()
        closes = frame["close"].astype(float).tolist()
        opens = frame["open"].astype(float).tolist()
        volumes = frame["volume"].astype(float).tolist()
        times = frame["open_time"].astype("int64").tolist()

        params = self.params
        swing_length = params.swing_length
        atr = _atr(highs, lows, closes, params.atr_length)
        upper = _rolling_max(highs, swing_length)
        lower = _rolling_min(lows, swing_length)

        use_wick = params.zone_invalidation == "wick"

        swing_type = 0
        top: _SwingPoint | None = None
        bottom: _SwingPoint | None = None

        bullish_obs: list[_RawOrderBlock] = []
        bearish_obs: list[_RawOrderBlock] = []

        last_index = n - 1
        start_active = max(0, last_index - params.max_distance_to_last_bar + 1)

        for t in range(start_active, n):
            if t >= swing_length:
                lag = t - swing_length
                if highs[lag] > upper[t]:
                    new_swing_type = 0
                elif lows[lag] < lower[t]:
                    new_swing_type = 1
                else:
                    new_swing_type = swing_type
                if new_swing_type == 0 and swing_type != 0:
                    top = _SwingPoint(index=lag, price=highs[lag])
                if new_swing_type == 1 and swing_type != 1:
                    bottom = _SwingPoint(index=lag, price=lows[lag])
                swing_type = new_swing_type

            self._invalidate(
                bullish_obs,
                is_bullish=True,
                use_wick=use_wick,
                t=t,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                times=times,
            )
            top = self._create_bullish(
                top, bullish_obs, params, t, highs, lows, closes, volumes, times, atr
            )

            self._invalidate(
                bearish_obs,
                is_bullish=False,
                use_wick=use_wick,
                t=t,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                times=times,
            )
            bottom = self._create_bearish(
                bottom, bearish_obs, params, t, highs, lows, closes, volumes, times, atr
            )

        zone_limit = params.zone_limit
        selected_bull = bullish_obs[:zone_limit]
        selected_bear = bearish_obs[:zone_limit]
        now_sentinel = times[-1] + 1

        if params.combine_obs:
            final_bull = _combine_same_direction(selected_bull, now_sentinel)
            final_bear = _combine_same_direction(selected_bear, now_sentinel)
        else:
            final_bull = [ob.to_model() for ob in selected_bull]
            final_bear = [ob.to_model() for ob in selected_bear]

        order_blocks = final_bull + final_bear
        signals = _generate_signals(order_blocks, times, highs, lows, closes)

        return OrderBlockResult(order_blocks=order_blocks, signals=signals)

    @staticmethod
    def _invalidate(
        obs: list[_RawOrderBlock],
        *,
        is_bullish: bool,
        use_wick: bool,
        t: int,
        opens: list[float],
        highs: list[float],
        lows: list[float],
        closes: list[float],
        times: list[int],
    ) -> None:
        for ob in list(obs):
            if not ob.breaker:
                if is_bullish:
                    cmp_value = lows[t] if use_wick else min(opens[t], closes[t])
                    if cmp_value < ob.bottom:
                        ob.breaker = True
                        ob.break_time = times[t]
                else:
                    cmp_value = highs[t] if use_wick else max(opens[t], closes[t])
                    if cmp_value > ob.top:
                        ob.breaker = True
                        ob.break_time = times[t]
            else:
                if is_bullish:
                    if highs[t] > ob.top:
                        obs.remove(ob)
                else:
                    if lows[t] < ob.bottom:
                        obs.remove(ob)

    @staticmethod
    def _create_bullish(
        top: _SwingPoint | None,
        bullish_obs: list[_RawOrderBlock],
        params: OrderBlockParams,
        t: int,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        volumes: list[float],
        times: list[int],
        atr: list[float | None],
    ) -> _SwingPoint | None:
        if top is None or top.crossed or closes[t] <= top.price:
            return top
        top.crossed = True

        lo, hi = 1, t - top.index - 1
        sel = min(range(t - hi, t - lo + 1), key=lambda i: lows[i]) if hi >= lo else t - 1
        box_bottom, box_top, box_loc = lows[sel], highs[sel], times[sel]

        ob_volume = volumes[t] + volumes[t - 1] + volumes[t - 2]
        ob_low_volume = volumes[t - 2]
        ob_high_volume = volumes[t] + volumes[t - 1]

        atr_t = atr[t]
        if atr_t is not None and abs(box_top - box_bottom) <= atr_t * params.max_atr_mult:
            new_ob = _RawOrderBlock(
                top=box_top,
                bottom=box_bottom,
                ob_volume=ob_volume,
                direction=OrderBlockDirection.BULLISH,
                start_time=box_loc,
                confirmed_time=times[t],
                ob_low_volume=ob_low_volume,
                ob_high_volume=ob_high_volume,
            )
            bullish_obs.insert(0, new_ob)
            if len(bullish_obs) > params.max_order_blocks:
                bullish_obs.pop()
        return top

    @staticmethod
    def _create_bearish(
        bottom: _SwingPoint | None,
        bearish_obs: list[_RawOrderBlock],
        params: OrderBlockParams,
        t: int,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        volumes: list[float],
        times: list[int],
        atr: list[float | None],
    ) -> _SwingPoint | None:
        if bottom is None or bottom.crossed or closes[t] >= bottom.price:
            return bottom
        bottom.crossed = True

        lo, hi = 1, t - bottom.index - 1
        sel = max(range(t - hi, t - lo + 1), key=lambda i: highs[i]) if hi >= lo else t - 1
        box_top, box_bottom, box_loc = highs[sel], lows[sel], times[sel]

        ob_volume = volumes[t] + volumes[t - 1] + volumes[t - 2]
        ob_low_volume = volumes[t] + volumes[t - 1]
        ob_high_volume = volumes[t - 2]

        atr_t = atr[t]
        if atr_t is not None and abs(box_top - box_bottom) <= atr_t * params.max_atr_mult:
            new_ob = _RawOrderBlock(
                top=box_top,
                bottom=box_bottom,
                ob_volume=ob_volume,
                direction=OrderBlockDirection.BEARISH,
                start_time=box_loc,
                confirmed_time=times[t],
                ob_low_volume=ob_low_volume,
                ob_high_volume=ob_high_volume,
            )
            bearish_obs.insert(0, new_ob)
            if len(bearish_obs) > params.max_order_blocks:
                bearish_obs.pop()
        return bottom

    @staticmethod
    def _prepare(df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"OHLCV DataFrame에 필요한 컬럼이 없습니다: {missing}")
        frame = df
        if "closed" in df.columns:
            frame = frame[frame["closed"].astype(bool)]
        return frame.sort_values("open_time").reset_index(drop=True)


def detect_order_blocks(
    df: pd.DataFrame, params: OrderBlockParams | None = None
) -> OrderBlockResult:
    """`OrderBlockDetector(params).run(df)`의 편의 함수."""
    return OrderBlockDetector(params).run(df)
