"""1분봉 서브스텝 존-지정가 시뮬레이션 테스트 (WAN-41).

필수 완료기준: **같은 스텝 진입+손절(관통)**이 정확히 처리됨을 검증한다. 존을
관통해 손절선까지 내려간 1분 스텝은 반드시 체결→손절(손실)로 처리되어야 하며,
누락하면 "좋은 진입가만 챙기고 손실은 안 나는" 가짜 성과가 나온다. 그 밖에 정상
체결·미체결 취소(기간 경과·오더블록 무효화)·조건 미충족도 함께 검증한다.
"""

from __future__ import annotations

import pandas as pd

from backtest.substep import (
    SubStep,
    ZoneLimitOutcome,
    ZoneLimitStatus,
    build_substeps,
    simulate_zone_limit_trade,
)
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

# 롱 셋업 공통: 존 상단(=proximal 지정가)=100, 존 하단(=distal 손절)=90, 익절=110.
_LIMIT = 100.0
_STOP = 90.0
_TP = 110.0

# 강한 하락 시딩 → 실시간 RSI가 과매도(≤30)로 유지된다(롱 진입 조건 충족).
_OVERSOLD_SEED = [140.0, 130.0, 120.0, 110.0, 105.0]
# 강한 상승 시딩 → 실시간 RSI가 과매수(≥70)로 유지된다(롱 조건 미충족 유도).
_OVERBOUGHT_SEED = [90.0, 95.0, 100.0, 105.0, 110.0]


def _long_state() -> RealtimeRsi:
    return RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3)


def _step(t: int, high: float, low: float, close: float, htf: int = 0) -> SubStep:
    return SubStep(time=t, high=high, low=low, close=close, htf_bar_time=htf)


def _simulate_long(
    steps: list[SubStep],
    *,
    state: RealtimeRsi | None = None,
    limit_valid_bars: int = 24,
    invalidation_time: int | None = None,
    cancel_on_condition_fail: bool = False,
    take_profit_price: float | None = _TP,
) -> ZoneLimitOutcome:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        substeps=steps,
        rsi_state=state or _long_state(),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=take_profit_price,
        limit_valid_bars=limit_valid_bars,
        invalidation_time=invalidation_time,
        cancel_on_condition_fail=cancel_on_condition_fail,
    )


# ---------------------------------------------------- 정상 체결


def test_clean_fill_then_take_profit() -> None:
    steps = [
        _step(0, high=101, low=99, close=99),  # 지정가 100 터치(low<=100) + 과매도 → 체결
        _step(60_000, high=111, low=100, close=110),  # 익절선 110 도달
    ]
    out = _simulate_long(steps)
    assert out.status is ZoneLimitStatus.FILLED_EXITED
    assert out.entry_price == _LIMIT
    assert out.exit_price == _TP
    assert out.exit_reason is SignalExitReason.TAKE_PROFIT


# ---------------------------------------------------- ⚠️ 관통(같은 스텝 진입+손절)


def test_pierce_same_step_entry_and_stop_is_a_loss() -> None:
    """존을 관통해 손절선까지 간 스텝은 체결→손절(손실)로 처리된다(낙관 편향 방지)."""
    steps = [
        # 한 1분봉이 지정가 100을 찍고(low<=100) 손절선 90 아래(88)까지 관통.
        _step(0, high=101, low=88, close=95),
    ]
    out = _simulate_long(steps)
    assert out.status is ZoneLimitStatus.FILLED_EXITED
    assert out.entry_price == _LIMIT  # 좋은 진입가는 인정하되
    assert out.exit_price == _STOP  # 같은 스텝에서 손절로 청산
    assert out.exit_reason is SignalExitReason.STOP_LOSS
    # 관통은 진입·청산이 같은 스텝(시각)이라는 신호로 드러난다 — WAN-46 감사 카운터가
    # 이 조건(entry_time == exit_time & STOP_LOSS)으로 관통 건수를 센다.
    assert out.entry_time == out.exit_time


def test_stop_precedes_take_profit_in_same_step() -> None:
    """손절·익절이 같은 스텝에 동시 충족되면 손절 우선(보수적)."""
    steps = [
        # low가 손절선(88<=90)과 지정가를, high가 익절선(111>=110)을 동시에 건드림.
        _step(0, high=111, low=88, close=95),
    ]
    out = _simulate_long(steps)
    assert out.status is ZoneLimitStatus.FILLED_EXITED
    assert out.exit_reason is SignalExitReason.STOP_LOSS


# ---------------------------------------------------- 미체결


def test_no_touch_never_fills() -> None:
    steps = [
        _step(0, high=105, low=101, close=103),  # 항상 지정가 100 위 → 미터치
        _step(60_000, high=106, low=102, close=104),
    ]
    out = _simulate_long(steps)
    assert out.status is ZoneLimitStatus.NO_TOUCH
    assert not out.filled


def test_expired_after_valid_bars() -> None:
    """유효 상위TF 봉 수 경과 시 미체결 취소."""
    steps = [
        _step(0, high=105, low=101, close=103, htf=0),  # htf 봉0, 미터치
        _step(60_000, high=106, low=102, close=104, htf=60_000),  # htf 봉1 진입 → 경과 1
    ]
    out = _simulate_long(steps, limit_valid_bars=1)
    assert out.status is ZoneLimitStatus.CANCELLED_EXPIRED


def test_cancelled_on_invalidation() -> None:
    """오더블록 무효화 시각 도달 시 미체결 즉시 취소."""
    steps = [
        _step(0, high=105, low=101, close=103),  # 미터치
        _step(60_000, high=105, low=101, close=103),  # 무효화 시각 도달
    ]
    out = _simulate_long(steps, invalidation_time=60_000)
    assert out.status is ZoneLimitStatus.CANCELLED_INVALIDATED


# ---------------------------------------------------- RSI 조건


def test_condition_fail_cancels_when_configured() -> None:
    steps = [_step(0, high=116, low=99, close=115)]  # 터치하지만 과매수(RSI 조건 실패)
    out = _simulate_long(
        steps,
        state=RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3),
        cancel_on_condition_fail=True,
    )
    assert out.status is ZoneLimitStatus.CANCELLED_CONDITION_FAILED


def test_condition_fail_keeps_order_when_not_configured() -> None:
    steps = [_step(0, high=116, low=99, close=115)]  # 터치하지만 조건 실패, 취소 안 함
    out = _simulate_long(
        steps,
        state=RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3),
        cancel_on_condition_fail=False,
    )
    assert out.status is ZoneLimitStatus.NO_TOUCH  # 대기 유지 → 데이터 종료까지 미체결


# ---------------------------------------------------- 숏 방향


def test_short_fill_then_take_profit() -> None:
    state = RealtimeRsi.seed_from_closed([70.0, 80.0, 90.0, 100.0], length=3)  # 과매수
    steps = [
        _step(0, high=105, low=99, close=101),  # 숏 지정가 100 터치(high>=100) + 과매수
        _step(60_000, high=95, low=89, close=90),  # 익절선 90 도달(low<=90)
    ]
    out = simulate_zone_limit_trade(
        direction=OrderBlockDirection.BEARISH,
        limit_price=100.0,
        stop_price=110.0,
        substeps=steps,
        rsi_state=state,
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=90.0,
    )
    assert out.status is ZoneLimitStatus.FILLED_EXITED
    assert out.entry_price == 100.0
    assert out.exit_reason is SignalExitReason.TAKE_PROFIT


# ---------------------------------------------------- build_substeps


def test_build_substeps_groups_by_htf_bar() -> None:
    htf_ms = 180_000  # 3분 상위TF
    df = pd.DataFrame(
        {
            "open_time": [0, 60_000, 120_000, 180_000, 240_000],
            "high": [1.0, 2.0, 3.0, 4.0, 5.0],
            "low": [0.5, 1.5, 2.5, 3.5, 4.5],
            "close": [0.8, 1.8, 2.8, 3.8, 4.8],
        }
    )
    steps = build_substeps(df, htf_ms=htf_ms)
    assert [s.htf_bar_time for s in steps] == [0, 0, 0, 180_000, 180_000]
    assert steps[0].close == 0.8
