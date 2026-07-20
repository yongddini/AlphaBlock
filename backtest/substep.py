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
from typing import Protocol

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


class LiveLimitProvider(Protocol):
    """봉내에 **움직이는 지정가**를 공급하는 계약 (WAN-119).

    `band_bar="intrabar_live"`는 밴드의 20번째 표본이 현재가라 봉 내부에서 값이 계속
    변한다 — 즉 지정가가 탭 봉 시점에 한 번 정해지는 상수가 아니다. 이 시뮬레이터는
    오더블록·밴드·오프셋 규칙을 모르므로(셋업 하나의 체결·청산만 본다), 그 재산정을
    호출부가 이 계약으로 주입한다. 구현은 `backtest.zone_limit_backtest`에 있다.

    `RealtimeRsi`와 **같은 생애주기**를 갖는다: 상위TF 봉이 마감되면 `commit`으로 상태를
    굴리고, 매 서브스텝 `limit_price(현재가)`로 그 순간의 주문 가격을 읽는다.
    """

    def commit(self, closed_price: float) -> None:
        """상위TF 봉 마감 — 그 확정 종가로 밴드 상태를 굴린다."""
        ...

    def limit_price(self, live_price: float) -> float | None:
        """이 순간 주문판에 걸려 있는 지정가. `None`이면 **주문이 없다**.

        `None`인 경우: 밴드 워밍업이라 값을 못 내거나(WAN-75), 밴드가 존 전체보다 불리해
        진입하지 않는 구간(WAN-75 규칙 3). 밴드가 움직이므로 이 판정은 서브스텝마다
        달라질 수 있다 — 지금 주문이 없어도 다음 스텝에 생길 수 있다.
        """
        ...

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
        """체결가가 정해진 **그 순간** 산출하는 `(손절 참조가, 익절 목표가)`.

        진입가가 봉내에 정해지므로 1R(진입가→무효화 경계)도, 그 배수인 고정 R 익절
        목표도 체결 전에는 알 수 없다. 익절이 `None`이면 목표 없음(무효화까지 홀딩)이고,
        **반환값 전체가 `None`이면** 이 셋업은 유효한 청산 규칙을 못 만든다는 뜻이라
        진입하지 않는다(WAN-143: `stop_loss_override`가 장벽을 못 낼 때).

        ⚠️ 손절과 익절을 **한 번에** 내는 이유는 순서 의존을 없애기 위해서다 — 익절
        오버라이드(WAN-137/143)는 손절가를 문맥으로 받으므로 손절이 먼저 정해져야
        하는데, 두 메서드로 나누면 호출 순서가 조용한 계약이 된다.
        """
        ...


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
    mfe_r: float | None = None
    """보유 구간의 최대유리이탈(MFE), **R 단위**(WAN-90). 체결됐을 때만 값이 있다.

    1R = 진입가 → 무효화 경계(손절 참조가)까지의 거리다. 롱이면
    `(구간 최고가 − 진입가) / 1R`, 숏이면 `(진입가 − 구간 최저가) / 1R`. 구간은
    체결 스텝부터 청산 스텝까지(둘 다 포함)이며 **청산 이후 봉은 보지 않는다**(look-ahead
    금지). 손절/익절이 진입가에 매우 가깝게 붙지 않는 한 보통 0 이상이지만, 진입 봉에서
    곧바로 손절된 경우 음수가 될 수 있다(순수 관측값이라 0에서 절단하지 않는다).

    ⚠️ 이 값은 **실제로 일어난 청산까지의** 경로만 잰다 — 고정 R 익절이 켜진 채택
    엔진에서는 승자가 익절 목표에서 잘리므로 MFE도 대체로 그 목표 부근에서 검열된다.
    "거래가 익절 없이 어디까지 갔는가"를 보려면 익절을 끈(먼 목표) 실행에서 재야 한다.
    """
    mae_r: float | None = None
    """보유 구간의 최대불리이탈(MAE), **R 단위**(WAN-90). 체결됐을 때만 값이 있다.

    롱이면 `(구간 최저가 − 진입가) / 1R`, 숏이면 `(진입가 − 구간 최고가) / 1R`. 통상
    0 이하이며, 손절로 청산된 거래는 손절선을 관통했다면 −1R 아래로도 내려갈 수 있다.
    """
    stop_price: float | None = None
    """이 거래에 **실제로 적용된** 손절 참조가 (WAN-143). 체결됐을 때만 값이 있다.

    보통은 호출부가 넘긴 `stop_price` 그대로다. `live_limit`(봉내 라이브 밴드)에서
    `stop_loss_override`가 걸려 있으면 손절이 **체결 순간**에 정해지므로, 호출부가
    1R 사이징에 쓸 값을 여기로 돌려준다 — 지어내지 않게 하려는 것이다.
    """
    order_rested: bool = True
    """이 셋업에 주문이 **한 번이라도 주문판에 걸렸는지** (WAN-119).

    상수 지정가(`limit_price`)면 언제나 참이다 — 주문 가격이 탭 봉에서 이미 정해져 있다.
    `live_limit`이면 밴드가 움직이며 "지금은 주문 없음"(워밍업·WAN-75 규칙 3 기각)이
    나올 수 있고, 끝까지 한 번도 걸리지 않으면 거짓이다.

    체결률(`filled/eligible`)의 **분모를 모드 간에 맞추는** 값이다: 정적 모드는 밴드가
    기각한 셋업을 탭 봉에서 걸러내 분모에 넣지 않는데, live 모드가 그것까지 세면 같은
    표의 체결률 열이 서로 다른 것을 재게 된다."""

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
    limit_price: float | None = None,
    live_limit: LiveLimitProvider | None = None,
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
    호출부의 통보다 — `rsi_gate_mode="first_tap_free"`(WAN-81~122 기본값)의 첫 탭 면제는
    `tap_index`를 아는 호출부만 판정할 수 있고, 이 시뮬레이터는 셋업 하나만 보므로
    스스로 알 수 없다. 참이면 RSI 게이트를 건너뛰고 **워밍업(RSI None)이어도** 지정가
    터치 즉시 체결한다(따라서 `cancel_on_condition_fail`의 조건 실패 취소도 타지 않는다).

    `rsi_gate_mode="unconditional"`(WAN-123 채택 기본값)은 그 면제를 **모든 탭**으로
    넓힌 것이라 `first_tap_free`와 같은 자리에서 판정한다 — 호출부의 통보가 필요 없다
    (탭 순서를 안 보므로 시뮬레이터가 스스로 안다). ⚠️ `"none"`은 이것과 **다르다**:
    게이트 판정만 통과시킬 뿐 `live_rsi is not None`(워밍업) 요구는 그대로라 워밍업
    구간 탭이 막힌다(WAN-114 `L0r`이 그 의미로 고정돼 있다).

    지정가는 `limit_price`(상수) **또는** `live_limit`(봉내 재산정, WAN-119) 중 정확히
    하나로 준다. `live_limit`을 쓰면 익절 목표도 체결 순간에 그 계약이 내므로
    `take_profit_price`를 함께 줄 수 없다 — 둘 다 주면 어느 쪽이 이겼는지 결과만 보고는
    알 수 없어(WAN-95의 "라벨과 실제 실행이 갈라진다") 조용히 무시하지 않고 거부한다.

    `live_limit`이면 **손절 참조가도 체결 순간에** 그 계약이 낼 수 있다(WAN-143
    `resolve_exits`). 인자 `stop_price`는 그때까지의 기본값(존 무효화 경계)이고, 계약이
    다른 값을 내면 그것이 청산·MFE/MAE 기준이 되며 `ZoneLimitOutcome.stop_price`로
    돌려준다. 계약이 `None`을 내면 유효한 청산 규칙이 없다는 뜻이라 체결시키지 않고
    `CANCELLED_CONDITION_FAILED`로 끝낸다(정적 경로가 탭 봉에서 셋업을 빼는 것의 봉내 판(版)).
    """
    if not substeps:
        # 서브스텝이 없으면 live 밴드는 값을 낼 기회조차 없었다 = 주문이 걸린 적 없다.
        return ZoneLimitOutcome(status=ZoneLimitStatus.NO_TOUCH, order_rested=live_limit is None)
    if penetration_bps < 0.0:
        raise ValueError(f"penetration_bps는 음수일 수 없습니다: {penetration_bps}")
    if (limit_price is None) == (live_limit is None):
        raise ValueError("limit_price와 live_limit 중 정확히 하나를 줘야 합니다.")
    if live_limit is not None and take_profit_price is not None:
        raise ValueError(
            "live_limit을 쓰면 익절 목표는 체결 순간에 산출되므로 "
            "take_profit_price를 함께 줄 수 없습니다."
        )

    is_long = direction is OrderBlockDirection.BULLISH

    # WAN-90: 보유 구간의 유리/불리 극값을 추적해 MFE/MAE를 R 단위로 낸다. 체결 스텝부터
    # 청산 스텝까지(둘 다 포함)의 서브스텝 고가/저가만 보고, 청산 이후는 보지 않는다.
    hold_high: float | None = None
    hold_low: float | None = None

    def _excursions() -> tuple[float | None, float | None]:
        """추적한 극값으로 (MFE_R, MAE_R)을 낸다. 1R을 못 재면 (None, None)."""
        if hold_high is None or hold_low is None or entry_price is None:
            return None, None
        risk = abs(entry_price - active_stop)
        if risk <= 0:
            return None, None
        if is_long:
            return (hold_high - entry_price) / risk, (hold_low - entry_price) / risk
        return (entry_price - hold_low) / risk, (entry_price - hold_high) / risk

    def _fill_trigger(price: float) -> float:
        """체결로 인정할 가격 문턱. 롱은 지정가 아래로, 숏은 위로 그만큼 관통해야 한다."""
        penetration = price * (penetration_bps / 10_000.0)
        return price - penetration if is_long else price + penetration

    # 상수 지정가면 문턱도 상수다(`live_limit`이면 서브스텝마다 다시 낸다).
    static_trigger = None if limit_price is None else _fill_trigger(limit_price)
    # 청산 판정에 쓰는 익절 목표·손절선. `live_limit`이면 둘 다 체결 순간에 정해진다(WAN-143).
    active_tp = take_profit_price
    active_stop = stop_price

    # 상수 지정가는 탭 봉부터 이미 주문판에 걸려 있다. live는 밴드가 값을 낸 순간부터다.
    order_rested = live_limit is None
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
                if live_limit is not None:
                    live_limit.commit(running_close)
            current_htf = step.htf_bar_time
            htf_elapsed += 1
        running_close = step.close

        if not position_open:
            # 미체결 취소: 오더블록 무효화가 먼저(보수적), 그다음 유효기간 경과.
            if invalidation_time is not None and step.time >= invalidation_time:
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.CANCELLED_INVALIDATED, order_rested=order_rested
                )
            if limit_valid_bars is not None and htf_elapsed >= limit_valid_bars:
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.CANCELLED_EXPIRED, order_rested=order_rested
                )

            if live_limit is None:
                assert static_trigger is not None
                current_limit, fill_trigger = limit_price, static_trigger
            else:
                # WAN-119: 밴드가 현재가를 표본으로 쓰므로 지정가가 봉내에 움직인다.
                # `None`이면 지금 주문판에 주문이 없다 — 다음 스텝에 생길 수 있으므로
                # 셋업을 끝내지 않고 그냥 넘어간다.
                current_limit = live_limit.limit_price(step.close)
                if current_limit is None:
                    continue
                order_rested = True
                fill_trigger = _fill_trigger(current_limit)

            assert current_limit is not None
            touched = step.low <= fill_trigger if is_long else step.high >= fill_trigger
            if touched:
                live_rsi = rsi_state.value(step.close)
                # WAN-100: 첫 탭 면제는 RSI 값 자체를 보지 않는다 — 워밍업(None)이어도
                # 통과다. A안 `ConfluenceStrategy._evaluate_entry`와 같은 규칙이다.
                # WAN-123: `unconditional`은 그 면제를 모든 탭으로 넓힌 것이라 여기서
                # 함께 판정한다(`none`은 아래 워밍업 요구를 그대로 받는다 — 둘은 다르다).
                condition = (
                    first_tap_free
                    or rsi_gate_mode == "unconditional"
                    or (
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
                )
                if condition:
                    if live_limit is not None:
                        # 1R = 진입가→무효화 경계라 체결가가 정해진 지금에야 손절·익절이
                        # 나온다(WAN-143: 오버라이드가 걸려 있으면 그 규칙이 낸다).
                        exits = live_limit.resolve_exits(current_limit)
                        if exits is None:
                            return ZoneLimitOutcome(
                                status=ZoneLimitStatus.CANCELLED_CONDITION_FAILED,
                                order_rested=order_rested,
                            )
                        active_stop, active_tp = exits
                    position_open = True
                    entry_time = step.time
                    entry_price = current_limit
                    entry_rsi = live_rsi
                    # 관통 방지: 같은 스텝에서 손절/익절을 곧바로 재판정한다(아래로 진행).
                elif cancel_on_condition_fail:
                    return ZoneLimitOutcome(
                        status=ZoneLimitStatus.CANCELLED_CONDITION_FAILED,
                        order_rested=order_rested,
                    )

        if position_open:
            # WAN-90: 이 스텝(진입 스텝·청산 스텝 포함)의 고가/저가를 극값에 반영한 뒤
            # 청산을 판정한다 — 청산 봉의 범위까지가 보유 구간이고 그 이후는 보지 않는다.
            hold_high = step.high if hold_high is None else max(hold_high, step.high)
            hold_low = step.low if hold_low is None else min(hold_low, step.low)
            stop_hit = step.low <= active_stop if is_long else step.high >= active_stop
            tp_hit = active_tp is not None and (
                step.high >= active_tp if is_long else step.low <= active_tp
            )
            if stop_hit and (not tp_hit or stop_before_tp):
                mfe_r, mae_r = _excursions()
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.FILLED_EXITED,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    entry_rsi=entry_rsi,
                    exit_time=step.time,
                    exit_price=active_stop,
                    exit_reason=SignalExitReason.STOP_LOSS,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    stop_price=active_stop,
                    order_rested=order_rested,
                )
            if tp_hit:
                mfe_r, mae_r = _excursions()
                return ZoneLimitOutcome(
                    status=ZoneLimitStatus.FILLED_EXITED,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    entry_rsi=entry_rsi,
                    exit_time=step.time,
                    exit_price=active_tp,
                    exit_reason=SignalExitReason.TAKE_PROFIT,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    stop_price=active_stop,
                    order_rested=order_rested,
                )

    if position_open:
        mfe_r, mae_r = _excursions()
        return ZoneLimitOutcome(
            status=ZoneLimitStatus.FILLED_OPEN,
            entry_time=entry_time,
            entry_price=entry_price,
            entry_rsi=entry_rsi,
            mfe_r=mfe_r,
            mae_r=mae_r,
            stop_price=active_stop,
            order_rested=order_rested,
        )
    return ZoneLimitOutcome(status=ZoneLimitStatus.NO_TOUCH, order_rested=order_rested)
