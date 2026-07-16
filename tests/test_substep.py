"""1분봉 서브스텝 존-지정가 시뮬레이션 테스트 (WAN-41).

필수 완료기준: **같은 스텝 진입+손절(관통)**이 정확히 처리됨을 검증한다. 존을
관통해 손절선까지 내려간 1분 스텝은 반드시 체결→손절(손실)로 처리되어야 하며,
누락하면 "좋은 진입가만 챙기고 손실은 안 나는" 가짜 성과가 나온다. 그 밖에 정상
체결·미체결 취소(기간 경과·오더블록 무효화)·조건 미충족도 함께 검증한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.substep import (
    SubStep,
    ZoneLimitOutcome,
    ZoneLimitStatus,
    build_substeps,
    simulate_zone_limit_trade,
)
from strategy.models import OrderBlockDirection, RsiGateMode, SignalExitReason
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
    limit_valid_bars: int | None = 24,
    invalidation_time: int | None = None,
    cancel_on_condition_fail: bool = False,
    take_profit_price: float | None = _TP,
    rsi_gate_mode: RsiGateMode = "extreme",
    rsi_neutral_band: tuple[float, float] = (40.0, 60.0),
    penetration_bps: float = 0.0,
    first_tap_free: bool = False,
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
        rsi_gate_mode=rsi_gate_mode,
        rsi_neutral_band=rsi_neutral_band,
        penetration_bps=penetration_bps,
        first_tap_free=first_tap_free,
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


# ---------------------------------------------------- 관통 요구(WAN-96 체결 보수화)

# 지정가 100 · 50bp(0.5%) 관통 요구 → 체결 문턱은 롱 99.5, 숏 100.5.
_PENETRATION_BPS = 50.0


def test_penetration_requirement_rejects_bare_touch() -> None:
    """지정가에 '닿기만' 한 스텝은 관통 요구가 있으면 체결되지 않는다.

    이 규칙이 WAN-96의 핵심이다 — 실거래에서는 큐 우선순위 때문에 가격이 스치듯 찍고
    되돌아가면 체결되지 않는데, 기본 시뮬레이터는 이를 체결로 본다.
    """
    steps = [
        # low=99.8은 지정가 100을 지나쳤지만 문턱 99.5까지는 못 갔다.
        _step(0, high=101, low=99.8, close=99.9),
    ]
    out = _simulate_long(steps, penetration_bps=_PENETRATION_BPS, take_profit_price=None)
    assert out.status is ZoneLimitStatus.NO_TOUCH
    # 같은 스텝이 기본(터치 체결)에서는 체결된다 — 차이를 만드는 건 관통 요구뿐이다.
    assert _simulate_long(steps, take_profit_price=None).filled


def test_penetration_requirement_fills_when_price_goes_through() -> None:
    """문턱을 넘어 관통한 스텝은 체결되며, 체결가는 여전히 지정가 그대로다."""
    steps = [
        _step(0, high=101, low=99.4, close=99.4),  # 99.4 <= 99.5 문턱
        _step(60_000, high=111, low=100, close=110),
    ]
    out = _simulate_long(steps, penetration_bps=_PENETRATION_BPS)
    assert out.status is ZoneLimitStatus.FILLED_EXITED
    # 관통은 체결 여부의 대리 변수일 뿐 — 더 유리한 가격을 받는 게 아니다.
    assert out.entry_price == _LIMIT
    assert out.exit_reason is SignalExitReason.TAKE_PROFIT


def test_penetration_zero_is_touch_fill_unchanged() -> None:
    """기본값 0.0은 현행 '닿으면 체결'과 동일하다(WAN-95 결과 재현 보장)."""
    steps = [
        _step(0, high=101, low=100, close=100),  # 지정가에 정확히 닿기만 함
        _step(60_000, high=111, low=100, close=110),
    ]
    assert _simulate_long(steps, penetration_bps=0.0) == _simulate_long(steps)


def test_short_penetration_requires_price_above_limit() -> None:
    """숏은 반대 방향으로 관통해야 한다(high >= 지정가×(1+bps))."""

    def _short(high: float) -> ZoneLimitOutcome:
        return simulate_zone_limit_trade(
            direction=OrderBlockDirection.BEARISH,
            limit_price=100.0,
            stop_price=110.0,
            substeps=[_step(0, high=high, low=99, close=99.5)],
            rsi_state=RealtimeRsi.seed_from_closed([70.0, 80.0, 90.0, 100.0], length=3),
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            take_profit_price=None,
            penetration_bps=_PENETRATION_BPS,
        )

    assert _short(100.2).status is ZoneLimitStatus.NO_TOUCH  # 터치했지만 문턱 100.5 미달
    assert _short(100.6).filled  # 문턱 관통 → 체결


def test_negative_penetration_bps_is_rejected() -> None:
    """음수 관통 폭은 '지정가에 닿기 전에 체결'이라는 뜻이라 거부한다."""
    with pytest.raises(ValueError, match="penetration_bps"):
        _simulate_long([_step(0, high=101, low=99, close=99)], penetration_bps=-1.0)


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


# ---------------------------------------------------- WAN-73: limit_valid_bars=None


def test_limit_valid_bars_none_never_expires() -> None:
    """`limit_valid_bars=None`이면 유효기간 경과로 취소되지 않고 무기한 대기한다."""
    steps = [
        _step(i * 60_000, high=105, low=101, close=103, htf=i * 60_000) for i in range(50)
    ]  # 50 상위TF 봉 동안 미터치 — 기본(24)이면 진작에 만료됐을 것.
    out = _simulate_long(steps, limit_valid_bars=None)
    assert out.status is ZoneLimitStatus.NO_TOUCH  # 만료가 아니라 단순 미터치


def test_limit_valid_bars_none_still_cancels_on_invalidation() -> None:
    """`limit_valid_bars=None`이어도 오더블록 무효화는 여전히 즉시 취소한다."""
    steps = [
        _step(0, high=105, low=101, close=103),
        _step(60_000, high=105, low=101, close=103),
    ]
    out = _simulate_long(steps, limit_valid_bars=None, invalidation_time=60_000)
    assert out.status is ZoneLimitStatus.CANCELLED_INVALIDATED


# ---------------------------------------------------- WAN-73: rsi_gate_mode


def test_rsi_gate_mode_extreme_matches_current_default_behavior() -> None:
    """`rsi_gate_mode="extreme"`(기본)은 현행 동작과 동일하다."""
    steps = [_step(0, high=101, low=99, close=99)]
    out = _simulate_long(steps, rsi_gate_mode="extreme")
    assert out.status is ZoneLimitStatus.FILLED_OPEN  # 과매도 → 체결(단일 스텝, 익절 미도달)


def test_rsi_gate_mode_neutral_rejects_extreme_oversold_rsi() -> None:
    """중립 게이트는 극단(과매도) RSI에서는 통과하지 않는다."""
    steps = [_step(0, high=101, low=99, close=99)]  # 터치는 하지만 시딩이 과매도(극단)
    out = _simulate_long(
        steps, rsi_gate_mode="neutral", cancel_on_condition_fail=False, take_profit_price=None
    )
    assert out.status is ZoneLimitStatus.NO_TOUCH  # 조건 미충족 → 대기 유지 → 미체결


def test_rsi_gate_mode_neutral_accepts_rsi_within_band() -> None:
    """중립 게이트는 밴드 안의 RSI에서 방향과 무관하게 통과한다."""
    # 시딩 [100,105,100,105] 이후 현재가 102 → RSI≈51.3(밴드 40~60 안).
    neutral_state = RealtimeRsi.seed_from_closed([100.0, 105.0, 100.0, 105.0], length=3)
    steps = [_step(0, high=103, low=99, close=102)]
    out = _simulate_long(
        steps,
        state=neutral_state,
        rsi_gate_mode="neutral",
        rsi_neutral_band=(40.0, 60.0),
        take_profit_price=None,
    )
    assert out.status is ZoneLimitStatus.FILLED_OPEN  # 중립 RSI → 체결(청산은 없음)


def test_rsi_gate_mode_none_always_passes() -> None:
    """게이트 없음(`none`)은 RSI 값과 무관하게 항상 통과한다(워밍업만 지나면 — 아래 참고)."""
    steps = [_step(0, high=116, low=99, close=115)]  # 극단 과매수 시딩이어도
    out = _simulate_long(
        steps,
        state=RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3),
        rsi_gate_mode="none",
        take_profit_price=None,
    )
    assert out.status is ZoneLimitStatus.FILLED_OPEN


# --------------------------- WAN-123: `unconditional` — 게이트 자체가 없다(채택 기본값)


def test_unconditional_fills_retap_despite_failing_rsi_gate() -> None:
    """`unconditional`은 **호출부 통보 없이도**(first_tap_free=False = 재탭) 체결한다.

    `first_tap_free`와 달리 탭 순서를 보지 않으므로 시뮬레이터가 스스로 판정할 수 있다.
    """
    steps = [_step(0, high=116, low=99, close=115)]  # 지정가 터치 + 극단 과매수(롱 게이트 미충족)
    out = _simulate_long(
        steps,
        state=RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3),
        rsi_gate_mode="unconditional",
        take_profit_price=None,
        first_tap_free=False,  # 재탭이라는 뜻 — 옛 기본값이었다면 여기서 막혔다.
    )
    assert out.status is ZoneLimitStatus.FILLED_OPEN
    assert out.entry_price == _LIMIT


def test_unconditional_fills_during_warmup_but_none_does_not() -> None:
    """워밍업(RSI 없음)에서 `unconditional`과 `none`이 갈린다 — **둘은 동의어가 아니다**.

    `none`은 게이트 판정만 통과시킬 뿐 `live_rsi is not None` 요구가 남아 막히고
    (WAN-114 `L0r`이 그 의미로 고정돼 있다), `unconditional`은 RSI를 아예 보지 않는다.
    게이트를 끄려던 자리에 `none`을 넣는 조용한 절반짜리 제거를 이 대조가 막는다.
    """
    warmup = RealtimeRsi.seed_from_closed([100.0], length=3)  # 시드 미형성 → value()가 None
    steps = [_step(0, high=101, low=99, close=99)]
    assert warmup.value(99.0) is None  # 전제 확인: 정말 워밍업이다.

    def _run(mode: str) -> ZoneLimitStatus:
        return _simulate_long(
            steps,
            state=RealtimeRsi.seed_from_closed([100.0], length=3),
            rsi_gate_mode=mode,  # type: ignore[arg-type]
            take_profit_price=None,
            cancel_on_condition_fail=False,
        ).status

    assert _run("unconditional") is ZoneLimitStatus.FILLED_OPEN
    assert _run("none") is ZoneLimitStatus.NO_TOUCH  # 워밍업에 막혀 체결 없음


# ------------------------------------- WAN-100: 첫 탭 면제(first_tap_free)가 B안에도 적용된다
#
# 채택 진입 경로(B안 zone_limit)가 `tap_index`를 읽지 않아 CLAUDE.md의 「첫 탭은 RSI
# 무관 무조건 진입」(WAN-81)이 통째로 빠져 있었다. 아래 3종이 그 회귀를 고정한다.
# `first_tap_free`는 호출부(`build_zone_limit_backtest`)가 `tap_index==0`을 보고 넘긴다.


def test_first_tap_free_fills_despite_failing_rsi_gate() -> None:
    """첫 탭이면 게이트 미충족 RSI(롱인데 과매수)여도 무조건 체결한다.

    수정 전에는 `rsi_gate_passes`가 `first_tap_free`를 `extreme`으로 폴백해 롱
    `RSI<=30`을 요구했고, 과매수 시딩이라 첫 탭이 통째로 누락됐다.
    """
    steps = [_step(0, high=116, low=99, close=115)]  # 지정가 100 터치 + 극단 과매수(롱 조건 미충족)
    out = _simulate_long(
        steps,
        state=RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3),
        rsi_gate_mode="first_tap_free",
        take_profit_price=None,
        first_tap_free=True,
    )
    assert out.status is ZoneLimitStatus.FILLED_OPEN
    assert out.entry_price == _LIMIT


def test_first_tap_free_fills_during_rsi_warmup() -> None:
    """첫 탭이면 RSI 워밍업(값 없음)이어도 체결한다.

    수정 전에는 `live_rsi is not None` 조건이 워밍업 구간의 첫 탭을 막았다 —
    CLAUDE.md의 "첫 탭은 워밍업 NaN이어도 무조건 진입"과 정반대였다.
    """
    warmup = RealtimeRsi.seed_from_closed([100.0], length=3)  # 시드 미형성 → value()가 None
    steps = [_step(0, high=101, low=99, close=99)]
    assert warmup.value(99.0) is None  # 전제 확인: 정말 워밍업이다.
    out = _simulate_long(
        steps,
        state=warmup,
        rsi_gate_mode="first_tap_free",
        take_profit_price=None,
        first_tap_free=True,
    )
    assert out.status is ZoneLimitStatus.FILLED_OPEN


def test_retap_still_applies_rsi_gate_under_first_tap_free_mode() -> None:
    """재탭(`first_tap_free=False`)은 같은 모드에서도 기존 극단 게이트를 그대로 받는다.

    면제가 모드 전체가 아니라 **첫 탭에만** 걸린다는 뜻 — 재탭은 롱 `RSI<=30`이 필요하다.
    """
    steps = [_step(0, high=116, low=99, close=115)]  # 터치하지만 과매수 → 재탭이면 미체결
    out = _simulate_long(
        steps,
        state=RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3),
        rsi_gate_mode="first_tap_free",
        take_profit_price=None,
        first_tap_free=False,
    )
    assert out.status is ZoneLimitStatus.NO_TOUCH  # 조건 미충족 → 대기 유지 → 미체결


def test_first_tap_free_does_not_trigger_condition_fail_cancel() -> None:
    """첫 탭 면제는 조건 실패 취소(`cancel_on_condition_fail`) 경로를 타지 않는다.

    면제가 "조건 통과"로 판정되므로 취소 분기(elif)에 도달하지 않는다 — 면제와
    취소 옵션을 함께 켠 조합에서 첫 탭이 취소돼 사라지면 안 된다.
    """
    steps = [_step(0, high=116, low=99, close=115)]  # 게이트만 보면 조건 실패할 터치
    out = _simulate_long(
        steps,
        state=RealtimeRsi.seed_from_closed(_OVERBOUGHT_SEED, length=3),
        rsi_gate_mode="first_tap_free",
        cancel_on_condition_fail=True,
        take_profit_price=None,
        first_tap_free=True,
    )
    assert out.status is ZoneLimitStatus.FILLED_OPEN


# ---------------------------------------------------- 봉내 움직이는 지정가 (WAN-119)


class _StubLiveLimit:
    """대본대로 지정가를 내는 `LiveLimitProvider` — 시뮬레이터 계약만 격리해 본다.

    실제 밴드 수식은 `tests/test_realtime_band.py`가, 가격 확정 순서는
    `tests/test_zone_limit_backtest.py`가 본다. 여기서는 시뮬레이터가 **매 서브스텝
    다시 묻고**, 봉 마감에 `commit`하고, 체결 순간의 가격으로 익절을 잡는지만 본다.
    """

    def __init__(self, prices: list[float | None], tp: float | None = _TP) -> None:
        self._prices = list(prices)
        self._tp = tp
        self.asked: list[float] = []
        self.committed: list[float] = []
        self.tp_called_with: list[float] = []

    def commit(self, closed_price: float) -> None:
        self.committed.append(closed_price)

    def limit_price(self, live_price: float) -> float | None:
        self.asked.append(live_price)
        return self._prices.pop(0) if self._prices else None

    def take_profit_price(self, limit_price: float) -> float | None:
        self.tp_called_with.append(limit_price)
        return self._tp


def _simulate_live(steps: list[SubStep], provider: _StubLiveLimit) -> ZoneLimitOutcome:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        live_limit=provider,
        stop_price=_STOP,
        substeps=steps,
        rsi_state=_long_state(),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        limit_valid_bars=24,
    )


def test_live_limit_is_re_asked_every_substep_with_the_live_price() -> None:
    """WAN-119: 지정가가 상수가 아니다 — 매 스텝 현재가로 다시 묻는다."""
    steps = [
        _step(0, high=101, low=99.5, close=99.5),
        _step(60_000, high=100, low=98.5, close=98.5),
    ]
    # 첫 스텝의 지정가(99)는 그 스텝 저가(99.5)에 안 닿고, 둘째 스텝(98.6)은 닿는다.
    provider = _StubLiveLimit([99.0, 98.6])
    out = _simulate_live(steps, provider)
    assert provider.asked == [99.5, 98.5]  # 그 스텝의 현재가로 물었다
    assert out.status is ZoneLimitStatus.FILLED_OPEN
    assert out.entry_price == 98.6  # 상수 _LIMIT(100)이 아니라 그 순간의 지정가


def test_live_limit_none_means_no_order_and_does_not_end_the_setup() -> None:
    """지금 주문이 없어도(밴드 워밍업·WAN-75 규칙 3) 다음 스텝에 생길 수 있다."""
    steps = [
        _step(0, high=101, low=90, close=99),  # 주문 없음 → 통과했어도 체결 없음
        _step(60_000, high=101, low=99, close=99),  # 이제 주문이 걸린다
    ]
    provider = _StubLiveLimit([None, 100.0])
    out = _simulate_live(steps, provider)
    assert out.status is ZoneLimitStatus.FILLED_OPEN
    assert out.entry_time == 60_000  # 첫 스텝 저가 90은 체결로 세지 않았다


def test_order_rested_is_false_when_the_band_never_yields_a_price() -> None:
    """끝까지 주문이 안 걸린 셋업 — 체결률 분모(eligible)에서 빠져야 한다.

    정적 모드는 같은 셋업을 탭 봉에서 걸러내므로, 이 값이 없으면 live 모드만 분모가
    부풀어 같은 표의 체결률 열이 서로 다른 것을 잰다.
    """
    steps = [_step(0, high=101, low=90, close=99), _step(60_000, high=101, low=90, close=99)]
    out = _simulate_live(steps, _StubLiveLimit([None, None]))
    assert out.status is ZoneLimitStatus.NO_TOUCH
    assert out.order_rested is False


def test_order_rested_is_true_once_an_order_existed_even_if_unfilled() -> None:
    steps = [_step(0, high=101, low=99.5, close=99.5)]
    out = _simulate_live(steps, _StubLiveLimit([99.0]))
    assert out.status is ZoneLimitStatus.NO_TOUCH
    assert out.order_rested is True


def test_static_limit_always_counts_as_rested() -> None:
    """상수 지정가는 탭 봉부터 걸려 있다 — 기존 모드의 분모는 바뀌지 않는다."""
    out = _simulate_long([_step(0, high=101, low=100.5, close=100.5)])
    assert out.status is ZoneLimitStatus.NO_TOUCH
    assert out.order_rested is True


def test_live_take_profit_is_resolved_from_the_realized_entry_price() -> None:
    """1R은 체결가에서 나온다 — 탭 봉의 옛 가격으로 잡으면 없는 손익비로 청산한다."""
    steps = [
        _step(0, high=101, low=98, close=98),
        _step(60_000, high=106, low=98, close=105),
    ]
    provider = _StubLiveLimit([98.5], tp=105.0)
    out = _simulate_live(steps, provider)
    assert provider.tp_called_with == [98.5]  # 체결가로 익절을 잡았다
    assert out.status is ZoneLimitStatus.FILLED_EXITED
    assert out.exit_price == 105.0
    assert out.exit_reason is SignalExitReason.TAKE_PROFIT


def test_live_limit_commits_closed_bars_like_the_rsi_state() -> None:
    """상위TF 봉이 마감되면 그 확정 종가로 밴드 상태를 굴린다(RSI와 같은 생애주기)."""
    steps = [
        _step(0, high=101, low=99.5, close=99.4, htf=0),
        _step(60_000, high=101, low=99.5, close=99.6, htf=0),
        _step(120_000, high=101, low=99.5, close=99.8, htf=3_600_000),  # 봉 경계
    ]
    provider = _StubLiveLimit([99.0, 99.0, 99.0])
    _simulate_live(steps, provider)
    # 경계 직전 스텝의 종가(=그 상위TF 봉의 확정 종가)가 커밋된다.
    assert provider.committed == [99.6]


def test_limit_price_and_live_limit_are_mutually_exclusive() -> None:
    """둘 다/둘 다 아님을 거부한다 — 조용히 하나를 이기게 하면 라벨과 실행이 갈라진다."""
    steps = [_step(0, high=101, low=99, close=99)]
    with pytest.raises(ValueError, match="정확히 하나"):
        simulate_zone_limit_trade(
            direction=OrderBlockDirection.BULLISH,
            limit_price=_LIMIT,
            live_limit=_StubLiveLimit([100.0]),
            stop_price=_STOP,
            substeps=steps,
            rsi_state=_long_state(),
            rsi_oversold=30.0,
            rsi_overbought=70.0,
        )
    with pytest.raises(ValueError, match="정확히 하나"):
        simulate_zone_limit_trade(
            direction=OrderBlockDirection.BULLISH,
            stop_price=_STOP,
            substeps=steps,
            rsi_state=_long_state(),
            rsi_oversold=30.0,
            rsi_overbought=70.0,
        )


def test_live_limit_rejects_a_static_take_profit() -> None:
    """익절이 체결 순간에 정해지는데 상수 목표까지 주면 어느 쪽이 이겼는지 알 수 없다."""
    with pytest.raises(ValueError, match="take_profit_price"):
        simulate_zone_limit_trade(
            direction=OrderBlockDirection.BULLISH,
            live_limit=_StubLiveLimit([100.0]),
            stop_price=_STOP,
            substeps=[_step(0, high=101, low=99, close=99)],
            rsi_state=_long_state(),
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            take_profit_price=_TP,
        )
