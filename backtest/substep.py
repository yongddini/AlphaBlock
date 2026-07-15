"""1분봉 서브스텝 존-지정가 진입 시뮬레이션 (WAN-41, B안).

상위TF(15m/1h/4h/1d) OHLC만으로는 봉 내부 경로를 알 수 없어 "존에 닿는 순간 진입 +
그 순간 실시간 RSI" 규칙을 검증할 수 없다. 이 모듈은 **1분봉을 서브스텝**으로 써서
봉 형성 과정을 재구성하고, 한 오더블록 셋업의 지정가 대기 → 체결 → 청산을 1분
해상도로 시뮬레이션한다.

## 규칙 (이슈 WAN-41)

1. 활성 오더블록 존 근단(proximal, `ConfluenceParams.zone_limit_price`)에 지정가를
   걸어 둔다. 각 1분 스텝에서 가격이 지정가에 **닿으면**(롱 `low <= 지정가`, 숏
   `high >= 지정가`) 체결 후보가 된다. 이 "닿으면 체결"은 **낙관적 가정**이다 —
   실거래에는 큐 우선순위가 있어 닿아도 체결되지 않을 수 있다. `penetration_bps`
   (WAN-96)로 일정 폭 관통을 요구해 이 가정을 보수화할 수 있다(기본은 현행 유지).
2. 체결 후보 스텝에서 **실시간 RSI**(`strategy.realtime_rsi`, 진행 중 상위TF 봉의
   임시 종가 = 그 1분봉 종가)를 계산해 조건을 판정한다 — 롱: `RSI <= rsi_oversold`,
   숏: `RSI >= rsi_overbought`. 충족하면 그 시점·지정가로 진입, 아니면 주문을
   유지하거나(기본) 취소(`cancel_on_condition_fail`)한다.
3. 미체결 주문은 `limit_valid_bars` 상위TF 봉이 경과하면 취소하고, 오더블록이
   무효화(`invalidation_time`)되면 즉시 취소한다.

## ⚠️ 낙관 편향 방지 (이 모듈의 핵심)

가격이 존을 **관통**해 손절선까지 내려간 1분 스텝에서는 **같은 스텝에서 체결 + 손절**이
발생한다. 이를 누락하면 "좋은 진입가만 챙기고 손실은 안 나는" 가짜 성과가 나온다.
따라서 체결이 일어난 스텝에서 곧바로 손절·익절을 재판정하며, 손절·익절이 같은 스텝에
동시 충족되면 **손절을 우선**한다(`stop_before_tp`, 보수적). 1분봉 내부 경로는 여전히
알 수 없으므로 애매하면 항상 불리한 쪽으로 가정한다.

라이브·백테스트가 동일한 `RealtimeRsi` 상태 머신을 공유하므로 실시간 RSI 값이 두
경로에서 일치한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from strategy.models import OrderBlockDirection, RsiGateMode, SignalExitReason, rsi_gate_passes
from strategy.realtime_rsi import RealtimeRsi

_SUBSTEP_COLUMNS = ("open_time", "high", "low", "close")


class ZoneLimitStatus(StrEnum):
    """존-지정가 셋업의 최종 상태."""

    NO_TOUCH = "no_touch"
    """유효 기간 내 지정가에 닿지 않음(또는 데이터 종료까지 미체결)."""
    CANCELLED_EXPIRED = "cancelled_expired"
    """`limit_valid_bars` 경과로 미체결 취소. `limit_valid_bars=None`이면 발생하지
    않는다(WAN-73 — 존 무효화까지 무기한 대기)."""
    CANCELLED_INVALIDATED = "cancelled_invalidated"
    """오더블록 무효화로 미체결 취소."""
    CANCELLED_CONDITION_FAILED = "cancelled_condition_failed"
    """지정가에 닿았으나 실시간 RSI 조건 미충족 + `cancel_on_condition_fail`."""
    FILLED_OPEN = "filled_open"
    """체결됐으나 데이터 종료까지 청산되지 않음(보유 중)."""
    FILLED_EXITED = "filled_exited"
    """체결 후 손절/익절로 청산 완료."""


@dataclass(frozen=True)
class SubStep:
    """1분봉 서브스텝 하나.

    `htf_bar_time`은 이 1분봉이 속한 상위TF 봉의 `open_time`(ms)으로, 이 값이 바뀌면
    직전 상위TF 봉이 마감된 것으로 보고 실시간 RSI 상태를 커밋한다.
    """

    time: int
    high: float
    low: float
    close: float
    htf_bar_time: int


@dataclass(frozen=True)
class ZoneLimitOutcome:
    """`simulate_zone_limit_trade`의 결과."""

    status: ZoneLimitStatus
    entry_time: int | None = None
    entry_price: float | None = None
    entry_rsi: float | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: SignalExitReason | None = None

    @property
    def filled(self) -> bool:
        return self.status in (ZoneLimitStatus.FILLED_OPEN, ZoneLimitStatus.FILLED_EXITED)


def build_substeps(df_1m: pd.DataFrame, htf_ms: int) -> list[SubStep]:
    """1분봉 DataFrame과 상위TF 주기(ms)로 `SubStep` 리스트를 만든다.

    각 1분봉의 상위TF 봉 시각은 `floor(open_time / htf_ms) * htf_ms`로 정렬한다.
    입력은 `open_time`(ms)·`high`·`low`·`close` 컬럼을 가지며 시간 오름차순으로
    정렬한다. `closed` 컬럼이 있으면 확정봉만 사용한다.
    """
    if htf_ms <= 0:
        raise ValueError(f"htf_ms는 양수여야 합니다: {htf_ms}")
    missing = [c for c in _SUBSTEP_COLUMNS if c not in df_1m.columns]
    if missing:
        raise ValueError(f"1분봉 DataFrame에 필요한 컬럼이 없습니다: {missing}")
    frame = df_1m
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    frame = frame.sort_values("open_time")
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    highs = [float(v) for v in frame["high"].astype(float).tolist()]
    lows = [float(v) for v in frame["low"].astype(float).tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    return [
        SubStep(time=t, high=h, low=lo, close=c, htf_bar_time=(t // htf_ms) * htf_ms)
        for t, h, lo, c in zip(times, highs, lows, closes, strict=True)
    ]


def simulate_zone_limit_trade(
    *,
    direction: OrderBlockDirection,
    limit_price: float,
    stop_price: float,
    substeps: Sequence[SubStep],
    rsi_state: RealtimeRsi,
    rsi_oversold: float,
    rsi_overbought: float,
    take_profit_price: float | None = None,
    limit_valid_bars: int | None = 24,
    invalidation_time: int | None = None,
    cancel_on_condition_fail: bool = False,
    stop_before_tp: bool = True,
    rsi_gate_mode: RsiGateMode = "extreme",
    rsi_neutral_band: tuple[float, float] = (40.0, 60.0),
    penetration_bps: float = 0.0,
    first_tap_free: bool = False,
) -> ZoneLimitOutcome:
    """한 오더블록 셋업의 존-지정가 진입·청산을 1분 서브스텝으로 시뮬레이션한다.

    `rsi_state`는 이 셋업의 첫 서브스텝이 속한 상위TF 봉 **직전까지** 확정봉으로
    시딩돼 있어야 한다(`RealtimeRsi.seed_from_closed`). 서브스텝이 상위TF 봉 경계를
    넘을 때마다 직전 봉 종가를 커밋해 상태를 굴린다. `rsi_state`는 호출 중 갱신되므로
    재사용하려면 복사해 넘긴다.

    반환값은 체결·취소·청산 여부와 진입/청산 시각·가격·사유를 담는다. 수수료·슬리피지
    등 비용 모델은 이 시뮬레이터의 관심사가 아니며, 집계 계층에서 A·B 동일하게
    적용한다.

    `penetration_bps`(WAN-96)를 0보다 크게 주면 가격이 지정가를 그만큼(bp) **관통해야**
    체결로 인정한다 — 기본값 0.0은 현행 "닿으면 체결"이다. 체결가는 관통 여부와 무관하게
    항상 `limit_price`다(관통은 체결 여부의 대리 변수일 뿐 더 유리한 체결가가 아니다).

    `first_tap_free`(WAN-100)는 이 셋업이 존(병합 존 포함) 확정 후 **첫 탭**이라는
    호출부의 통보다 — `rsi_gate_mode="first_tap_free"`(WAN-81 기본값)의 첫 탭 면제는
    `tap_index`를 아는 호출부만 판정할 수 있고, 이 시뮬레이터는 셋업 하나만 보므로
    스스로 알 수 없다. 참이면 RSI 게이트를 건너뛰고 **워밍업(RSI None)이어도** 지정가
    터치 즉시 체결한다(따라서 `cancel_on_condition_fail`의 조건 실패 취소도 타지 않는다).
    """
    if not substeps:
        return ZoneLimitOutcome(status=ZoneLimitStatus.NO_TOUCH)
    if penetration_bps < 0.0:
        raise ValueError(f"penetration_bps는 음수일 수 없습니다: {penetration_bps}")

    is_long = direction is OrderBlockDirection.BULLISH
    # 관통 요구: 체결로 인정할 가격 문턱. 롱은 지정가 아래로, 숏은 위로 그만큼 지나가야 한다.
    penetration = limit_price * (penetration_bps / 10_000.0)
    fill_trigger = limit_price - penetration if is_long else limit_price + penetration

    current_htf = substeps[0].htf_bar_time
    htf_elapsed = 0  # 주문 이후 마감된 상위TF 봉 수
    running_close: float | None = None
    position_open = False
    entry_time: int | None = None
    entry_price: float | None = None
    entry_rsi: float | None = None

    for step in substeps:
        # 상위TF 봉 경계: 직전 봉을 확정 종가로 커밋하고 경과 봉 수를 늘린다.
        if step.htf_bar_time != current_htf:
            if running_close is not None:
                rsi_state.commit(running_close)
            current_htf = step.htf_bar_time
            htf_elapsed += 1
        running_close = step.close

        if not position_open:
            # 미체결 취소: 오더블록 무효화가 먼저(보수적), 그다음 유효기간 경과.
            if invalidation_time is not None and step.time >= invalidation_time:
                return ZoneLimitOutcome(status=ZoneLimitStatus.CANCELLED_INVALIDATED)
            if limit_valid_bars is not None and htf_elapsed >= limit_valid_bars:
                return ZoneLimitOutcome(status=ZoneLimitStatus.CANCELLED_EXPIRED)

            touched = step.low <= fill_trigger if is_long else step.high >= fill_trigger
            if touched:
                live_rsi = rsi_state.value(step.close)
                # WAN-100: 첫 탭 면제는 RSI 값 자체를 보지 않는다 — 워밍업(None)이어도
                # 통과다. A안 `ConfluenceStrategy._evaluate_entry`와 같은 규칙이다.
                condition = first_tap_free or (
                    live_rsi is not None
                    and rsi_gate_passes(
                        live_rsi,
                        is_long=is_long,
                        mode=rsi_gate_mode,
                        rsi_oversold=rsi_oversold,
                        rsi_overbought=rsi_overbought,
                        rsi_neutral_band=rsi_neutral_band,
                    )
                )
                if condition:
                    position_open = True
                    entry_time = step.time
                    entry_price = limit_price
                    entry_rsi = live_rsi
                    # 관통 방지: 같은 스텝에서 손절/익절을 곧바로 재판정한다(아래로 진행).
                elif cancel_on_condition_fail:
                    return ZoneLimitOutcome(status=ZoneLimitStatus.CANCELLED_CONDITION_FAILED)

        if position_open:
            stop_hit = step.low <= stop_price if is_long else step.high >= stop_price
            tp_hit = take_profit_price is not None and (
                step.high >= take_profit_price if is_long else step.low <= take_profit_price
            )
            if stop_hit and (not tp_hit or stop_before_tp):
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.FILLED_EXITED,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    entry_rsi=entry_rsi,
                    exit_time=step.time,
                    exit_price=stop_price,
                    exit_reason=SignalExitReason.STOP_LOSS,
                )
            if tp_hit:
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.FILLED_EXITED,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    entry_rsi=entry_rsi,
                    exit_time=step.time,
                    exit_price=take_profit_price,
                    exit_reason=SignalExitReason.TAKE_PROFIT,
                )

    if position_open:
        return ZoneLimitOutcome(
            status=ZoneLimitStatus.FILLED_OPEN,
            entry_time=entry_time,
            entry_price=entry_price,
            entry_rsi=entry_rsi,
        )
    return ZoneLimitOutcome(status=ZoneLimitStatus.NO_TOUCH)
