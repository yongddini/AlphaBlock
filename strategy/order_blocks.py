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

from dataclasses import dataclass, field

import pandas as pd

from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
    select_active,
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
    """탐지 진행 중 상태를 갖는 오더블록. 원본 `orderBlockInfo`에 대응.

    WAN-47: 존은 삭제되지 않고 전체 생애주기를 기록한다. `swept`로 소멸 여부를,
    `swept_time`으로 소멸 시각을, `tapped_times`로 재진입 시각들을 보존한다.
    `_inside`는 tap 전이(바깥→안) 판정을 위한 내부 상태다.
    """

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
    swept: bool = False
    swept_time: int | None = None
    tapped_times: list[int] = field(default_factory=list)
    _inside: bool = False

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
            swept_time=self.swept_time,
            tapped_times=tuple(self.tapped_times),
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

    WAN-47: `order_blocks`는 **전체 아카이브**(살아남은 존뿐 아니라 깨지고 소멸한
    존까지)를 받는다. 이로써 생존자 편향 없이 모든 존의 탭·손절이 백테스트에
    반영된다. **look-ahead 금지** — 각 존의 신호는 그 존이 확정(`confirmed_time`)된
    이후, 무효화(`break_time`) 이전(무효화 봉 포함)의 자기 시간축에서만 나온다.
    시각 `t`의 신호는 `t` 시점에 이미 확정·미소멸한 존만 근거로 하므로, 데이터를
    미래에서 잘라도 과거 신호는 바뀌지 않는다.
    """
    signals: list[OrderBlockSignal] = []
    n = len(times)
    for ob in order_blocks:
        start_pos = 0
        while start_pos < n and times[start_pos] <= ob.confirmed_time:
            start_pos += 1
        # 무효화 봉의 위치. 이 봉까지(포함) 탭을 살핀다.
        break_pos: int | None = None
        if ob.break_time is not None:
            pos = start_pos
            while pos < n and times[pos] < ob.break_time:
                pos += 1
            break_pos = pos
        end_pos = n if break_pos is None else min(n, break_pos + 1)

        for i in range(start_pos, end_pos):
            if lows[i] <= ob.top and highs[i] >= ob.bottom:
                # WAN-47: 상태는 존의 **최종** breaker 여부가 아니라 **이 탭이 무효화
                # 전인지**로 정한다. 무효화 봉 자체에서의 탭만 cancelled고, 그 전의
                # 탭은 유효한 진입(나중에 무효화되면 손절)이다. 최종 상태로 판정하면
                # 결국 깨질 존의 정상 진입까지 모두 배제돼 생존자 편향이 재발한다.
                is_break_bar = break_pos is not None and i >= break_pos
                signals.append(
                    OrderBlockSignal(
                        direction=ob.direction,
                        trigger_time=times[i],
                        price=closes[i],
                        order_block=ob,
                        status="cancelled" if is_break_bar else "active",
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

        # WAN-47: 탐지는 **전체 히스토리**를 스캔한다. 원본의 max_distance_to_last_bar는
        # "마지막 봉에서 N봉 이내만 탐지"하는 스캔 상한이었지만, 그러면 백테스트 기간의
        # 앞부분에서 당시 유효했던 존이 아카이브에서 통째로 빠진다(생존자 편향의 또 다른
        # 얼굴). 탐지/렌더 분리에 따라 이 상한은 **렌더 최근성 필터**로 옮겨(아래
        # rendered 계산), 아카이브는 생성된 모든 존을 담는다.
        for t in range(n):
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

        # WAN-47: 탐지(archive)와 렌더링(view)을 분리한다. 아카이브는 생성된 모든
        # 존의 전체 생애주기를 담고(트리밍·삭제 없음), 신호는 아카이브 전체에서
        # 생성한다(생존자 편향 제거). "지금 차트에 그릴 박스"는 렌더링 뷰가 파생한다.
        archive = [ob.to_model() for ob in bullish_obs] + [ob.to_model() for ob in bearish_obs]
        signals = _generate_signals(archive, times, highs, lows, closes)

        # 렌더 뷰(트레이딩뷰 "현재 그림"): 마지막 봉에서 max_distance_to_last_bar봉 이내에
        # 확정된 존만 대상으로, 방향별 zone_limit개를 병합해 낸다. 데이터가 스캔 상한보다
        # 짧으면(대부분의 테스트·픽스처) 필터는 무효라 기존 동작과 동일하다.
        cutoff_index = max(0, (n - 1) - params.max_distance_to_last_bar + 1)
        cutoff_time = times[cutoff_index]
        recent = [ob for ob in archive if ob.confirmed_time >= cutoff_time]
        rendered = select_active(
            recent, times[-1], limit=params.zone_limit, combine=params.combine_obs
        )

        return OrderBlockResult(
            order_blocks=archive, signals=signals, rendered_order_blocks=rendered
        )

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
        for ob in obs:
            if ob.swept:
                # 이미 소멸한 존은 더 이상 상태가 바뀌지 않는다(생애 종료, 기록 보존).
                continue
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
                # WAN-47: 되쓸린 존을 리스트에서 지우지 않고 소멸 시각만 기록한다.
                # (원본은 여기서 box.delete() — 렌더링에는 옳지만 백테스트 기록을 지운다.)
                if is_bullish:
                    if highs[t] > ob.top:
                        ob.swept = True
                        ob.swept_time = times[t]
                else:
                    if lows[t] < ob.bottom:
                        ob.swept = True
                        ob.swept_time = times[t]

            # tap(재진입) 전이 기록: 확정 이후, 존 범위에 바깥→안으로 진입한 시각.
            if not ob.swept:
                inside = lows[t] <= ob.top and highs[t] >= ob.bottom
                if inside and not ob._inside and times[t] > ob.confirmed_time:
                    ob.tapped_times.append(times[t])
                ob._inside = inside

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
            # WAN-47: 아카이브는 개수 캡으로 오래된 존을 버리지 않는다(전체 생애 보존).
            # 표시 개수 제한은 렌더링 뷰(`select_active`)에서만 적용한다.
            bullish_obs.insert(0, new_ob)
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
