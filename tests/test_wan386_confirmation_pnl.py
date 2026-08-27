"""WAN-386 §3: 확인 진입 팔 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 여덟:

1. **`simulate_fixed_entry_exits`가 시뮬레이터와 같은 규칙을 쓴다** — 진입 스텝부터 보고,
   같은 스텝에서 손절·익절이 함께 닿으면 손절이 이기며, 안 닫히면 `FILLED_OPEN`이다.
   두 경로가 갈라지면 「기준 팔 ≡ 인자 없는 채택 북」 검산이 통째로 무의미해진다.
2. **한 순회로 배수 여러 개** — 목표마다 다시 훑은 것과 값이 같아야 한다(성능이 숫자를
   바꾸면 그 순간 이 격자는 무효다 — WAN-203 선례).
3. **기준 팔 후보 ≡ 엔진이 낸 후보**(실데이터) — 진입·청산·손절가·사유가 전부 같아야 한다.
   여기가 이 PR의 유일한 「엔진을 다시 만든」 자리라 값으로 못 박는다.
4. **확인 팔은 테이커 · 기준 팔은 메이커**(값으로) — 「비용을 싸게 잡는 것」이 이 이슈가
   지는 가장 흔한 방식이다(WAN-370). 그리고 그 유동성이 **실제 손익에 반영**된다.
5. **팔 `2`의 진입가는 `max(P*, 현재가)`** — `P*`를 그대로 쓰면 없는 가격 이점을 지어낸다
   (WAN-383 §3-3).
6. **트리거가 안 온 셋업은 빠진다** · 같은 팔의 배수들은 **진입 집합이 비트 일치**.
7. **안 켜면 비트 동일** — `confirmation_arms`가 비면 `arm_candidates`가 비어 있고 base·
   재진입 후보가 하나도 안 움직인다(옵트인 규약).
8. **잘못된 조합은 시끄럽게 죽는다**(한쪽만 준 짝 · 사다리 상한 밖 오프셋 · 없는 트리거 시각)
   그리고 요약이 빈 표에서 판정을 지어내지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from backtest import harness
from backtest.confirmation_arm import (
    ARM_BAR_CLOSE,
    ARM_BASE,
    ARM_C_OFFSET,
    ARM_CROSS,
    ARM_OFFSET,
    ARM_ORDER,
    arm_trigger,
    derive_arm_candidates,
    take_profit_price,
)
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.substep import (
    SubStep,
    ZoneLimitStatus,
    simulate_fixed_entry_exits,
)
from backtest.wan169_leverage_book import _Task, arm_key, run_cell
from backtest.wan383_confirmation_entry import arm_trigger as wan383_arm_trigger
from backtest.wan386_confirmation_pnl import (
    ADOPTED_MULTIPLE,
    GUARD_POINTS,
    MULTIPLES,
    build_summary_markdown,
    guard_census,
    rank_stability,
)
from backtest.zone_limit_backtest import ConfirmationProbe, _Candidate
from common.costs import Liquidity
from strategy.models import OrderBlockDirection

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-10-01"


def _skip_without_real_data() -> None:
    """🚨 게이트는 무거운 호출 **전에** 판정한다 — 안 그러면 CI의 빈 DB가 실패로 끝난다."""
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")


def _step(t: int, high: float, low: float, close: float, htf: int = 0) -> SubStep:
    return SubStep(time=t, high=high, low=low, close=close, htf_bar_time=htf)


def _task(**overrides: Any) -> _Task:
    base: dict[str, Any] = {
        "symbol": harness.normalize_symbol(_REAL_SYMBOL),
        "timeframe": _REAL_TF,
        "start_ms": parse_date_ms(_REAL_START),
        "end_ms": parse_date_ms(_REAL_END),
        "adv_fraction": harness.UNSET,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        "reentry": True,
        "cold_segments": False,
        "engine_check": False,
    }
    base.update(overrides)
    return _Task(**base)


# --------------------------------------------------------------------------- #
# 1~2. 청산 판정기
# --------------------------------------------------------------------------- #


def test_stop_wins_when_both_touch_in_the_same_step() -> None:
    """같은 스텝에서 손절·익절이 함께 닿으면 손절이 이긴다(`stop_before_tp` 관행)."""
    steps = [_step(0, high=115.0, low=89.0, close=100.0)]
    (done,) = simulate_fixed_entry_exits(
        direction=OrderBlockDirection.BULLISH,
        entry_index=0,
        entry_price=100.0,
        stop_price=90.0,
        take_profit_prices=[115.0],
        substeps=steps,
    )
    assert done.exit_price == 90.0
    assert done.exit_reason is not None and done.exit_reason.value == "stop_loss"
    assert done.penetration is True  # 진입과 손절이 같은 1분(WAN-46 관통 감사의 자)


def test_one_pass_matches_one_pass_per_target() -> None:
    """한 순회로 배수 여럿 = 목표마다 따로 훑은 것. 빨라졌는데 값이 달라지면 버그다."""
    steps = [
        _step(0, high=101.0, low=99.0, close=100.0),
        _step(60_000, high=112.0, low=100.0, close=111.0),
        _step(120_000, high=118.0, low=95.0, close=96.0),
        _step(180_000, high=97.0, low=89.0, close=90.0),
    ]
    targets = [105.0, 115.0, 130.0]
    together = simulate_fixed_entry_exits(
        direction=OrderBlockDirection.BULLISH,
        entry_index=0,
        entry_price=100.0,
        stop_price=90.0,
        take_profit_prices=targets,
        substeps=steps,
    )
    for target, done in zip(targets, together, strict=True):
        (alone,) = simulate_fixed_entry_exits(
            direction=OrderBlockDirection.BULLISH,
            entry_index=0,
            entry_price=100.0,
            stop_price=90.0,
            take_profit_prices=[target],
            substeps=steps,
        )
        assert done == alone, target
    # 105는 2번째 스텝에 익절 · 115는 3번째 · 130은 끝까지 안 닿아 손절.
    assert together[0].exit_time == 60_000
    assert together[1].exit_time == 120_000
    assert together[2].exit_reason is not None
    assert together[2].exit_reason.value == "stop_loss"


def test_mfe_stops_at_each_targets_own_exit() -> None:
    """MFE/MAE는 **그 목표의 청산 스텝까지만** 본다(WAN-90 규약을 목표별로)."""
    steps = [
        _step(0, high=101.0, low=99.0, close=100.0),
        _step(60_000, high=105.0, low=100.0, close=104.0),
        _step(120_000, high=140.0, low=80.0, close=130.0),
    ]
    early, late = simulate_fixed_entry_exits(
        direction=OrderBlockDirection.BULLISH,
        entry_index=0,
        entry_price=100.0,
        stop_price=90.0,
        take_profit_prices=[105.0, 135.0],
        substeps=steps,
    )
    assert early.mfe_r == pytest.approx(0.5)  # 3번째 스텝의 고가를 보지 않는다
    assert late.mfe_r == pytest.approx(4.0)
    assert early.mae_r == pytest.approx(-0.1)


def test_open_position_at_data_end() -> None:
    """끝까지 안 닫히면 `FILLED_OPEN` — 호출부가 마지막 종가로 강제 청산한다."""
    steps = [_step(0, high=101.0, low=99.0, close=100.0)]
    (done,) = simulate_fixed_entry_exits(
        direction=OrderBlockDirection.BULLISH,
        entry_index=0,
        entry_price=100.0,
        stop_price=90.0,
        take_profit_prices=[200.0],
        substeps=steps,
    )
    assert done.status is ZoneLimitStatus.FILLED_OPEN
    assert done.exit_time is None


def test_entry_index_outside_the_substeps_is_rejected() -> None:
    with pytest.raises(ValueError, match="entry_index"):
        simulate_fixed_entry_exits(
            direction=OrderBlockDirection.BULLISH,
            entry_index=5,
            entry_price=100.0,
            stop_price=90.0,
            take_profit_prices=[110.0],
            substeps=[_step(0, high=101.0, low=99.0, close=100.0)],
        )


# --------------------------------------------------------------------------- #
# 5~6·8. 트리거 판독 · 팔 변환 (합성)
# --------------------------------------------------------------------------- #


def _probe(**overrides: Any) -> ConfirmationProbe:
    base: dict[str, Any] = {"entry_time": 0, "entry_price": 100.0}
    base.update(overrides)
    return ConfirmationProbe(**base)


def test_cross_arm_never_invents_a_price_advantage() -> None:
    """`P*`가 이미 현재가 아래면 진입가는 **현재가**다(WAN-383 §3-3)."""
    probe = _probe(cross_time=60_000, cross_price=97.0, cross_ref_price=101.0)
    assert arm_trigger(probe, ARM_CROSS) == (60_000, 101.0)
    # 반대로 `P*`가 위면 그 값 그대로.
    higher = _probe(cross_time=60_000, cross_price=104.0, cross_ref_price=101.0)
    assert arm_trigger(higher, ARM_CROSS) == (60_000, 104.0)


def test_arm_trigger_agrees_with_wan383() -> None:
    """§1 표와 §3 팔이 **같은 트리거**를 읽는다 — 갈라지면 두 표가 다른 규칙을 잰다."""
    probe = _probe(
        bar_close_time=120_000,
        bar_close_price=103.0,
        cross_time=60_000,
        cross_price=97.0,
        cross_ref_price=101.0,
        rise_ladder=((60_000, 101.5), (120_000, 103.0)),
    )
    for arm in (ARM_BAR_CLOSE, ARM_CROSS, ARM_OFFSET):
        assert arm_trigger(probe, arm, offset=0.01) == wan383_arm_trigger(probe, arm, offset=0.01)


def test_offset_outside_the_ladder_is_rejected() -> None:
    """사다리가 기록 안 한 위를 물으면 「못 닿음」과 구분되지 않는다 — 지어내지 않고 죽는다."""
    with pytest.raises(ValueError, match="사다리 상한"):
        arm_trigger(_probe(), ARM_OFFSET, offset=0.5)


def _synthetic_candidate(probe: ConfirmationProbe | None) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=120_000,
        exit_price=90.0,
        reason=ExitReason.STOP_LOSS,
        stop_price=90.0,
        confirmation=probe,
    )


_SYNTH_STEPS = [
    _step(0, high=101.0, low=99.0, close=100.0),
    _step(60_000, high=104.0, low=100.0, close=103.0),
    _step(120_000, high=130.0, low=95.0, close=128.0),
]
_SYNTH_TIMES = [s.time for s in _SYNTH_STEPS]


def test_confirmation_arm_pays_taker_and_base_pays_maker() -> None:
    """유동성은 라벨이 아니라 **후보의 값**이다(WAN-370: 비용을 싸게 잡으면 이 표가 진다)."""
    probe = _probe(cross_time=60_000, cross_price=103.0, cross_ref_price=100.0)
    cands = [_synthetic_candidate(probe)]
    base = derive_arm_candidates(
        cands,
        arm=ARM_BASE,
        multiples=[1.5],
        substeps=_SYNTH_STEPS,
        substep_times=_SYNTH_TIMES,
    )[1.5]
    cross = derive_arm_candidates(
        cands,
        arm=ARM_CROSS,
        multiples=[1.5],
        substeps=_SYNTH_STEPS,
        substep_times=_SYNTH_TIMES,
    )[1.5]
    assert base[0].entry_liquidity is Liquidity.MAKER
    assert base[0].entry_price == 100.0
    assert cross[0].entry_liquidity is Liquidity.TAKER
    assert cross[0].entry_price == 103.0
    # 확인 팔은 늦게 사서 **손절폭이 넓다**(1R이 커진다 = 고정 배수 목표가 멀어진다).
    assert abs(cross[0].entry_price - cross[0].stop_price) > abs(
        base[0].entry_price - base[0].stop_price
    )


def test_setups_without_a_trigger_drop_out() -> None:
    """트리거가 안 오면 그 셋업은 매매하지 않는다 — 후보에서 빠진다."""
    derived = derive_arm_candidates(
        [_synthetic_candidate(_probe())],
        arm=ARM_CROSS,
        multiples=[1.5],
        substeps=_SYNTH_STEPS,
        substep_times=_SYNTH_TIMES,
    )
    assert derived[1.5] == []


def test_multiples_share_one_entry_set() -> None:
    """익절은 청산만 바꾼다(WAN-137/143 훅) — 배수마다 진입이 달라지면 팔 비교가 깨진다."""
    probe = _probe(cross_time=60_000, cross_price=103.0, cross_ref_price=100.0)
    derived = derive_arm_candidates(
        [_synthetic_candidate(probe)],
        arm=ARM_CROSS,
        multiples=list(MULTIPLES),
        substeps=_SYNTH_STEPS,
        substep_times=_SYNTH_TIMES,
    )
    entries = {tuple((c.entry_time, c.entry_price) for c in derived[m]) for m in MULTIPLES}
    assert len(entries) == 1
    targets = {derived[m][0].take_profit_price for m in MULTIPLES}
    assert len(targets) == len(MULTIPLES)  # 목표는 배수마다 달라야 한다


def test_trigger_time_outside_the_substeps_is_loud() -> None:
    """트리거 시각이 서브스텝에 없으면 조용히 건너뛰지 않는다(팔마다 다른 표본이 된다)."""
    probe = _probe(cross_time=999, cross_price=103.0, cross_ref_price=100.0)
    with pytest.raises(ValueError, match="서브스텝에 없습니다"):
        derive_arm_candidates(
            [_synthetic_candidate(probe)],
            arm=ARM_CROSS,
            multiples=[1.5],
            substeps=_SYNTH_STEPS,
            substep_times=_SYNTH_TIMES,
        )


def test_duplicate_multiples_are_rejected() -> None:
    with pytest.raises(ValueError, match="중복"):
        derive_arm_candidates(
            [],
            arm=ARM_BASE,
            multiples=[1.5, 1.5],
            substeps=_SYNTH_STEPS,
            substep_times=_SYNTH_TIMES,
        )


def test_take_profit_price_matches_the_engine_formula() -> None:
    """`_resolve_take_profit`의 `fixed_r` 갈래와 같은 식이어야 한다."""
    assert take_profit_price(
        is_long=True, entry_price=100.0, stop_price=90.0, multiple=1.5
    ) == pytest.approx(115.0)
    assert take_profit_price(
        is_long=False, entry_price=100.0, stop_price=110.0, multiple=2.0
    ) == pytest.approx(80.0)
    # 위험이 0 이하이면 목표가 정의되지 않는다 — 지어내지 않는다.
    assert take_profit_price(is_long=True, entry_price=90.0, stop_price=90.0, multiple=1.5) is None


# --------------------------------------------------------------------------- #
# 7~8. 배선 (합성) — 옵트인 규약과 짝 검사
# --------------------------------------------------------------------------- #


def test_arms_and_multiples_are_a_pair() -> None:
    """한쪽만 주면 팔이 조용히 0개가 되거나 배수가 무시된다 — 라벨만 남는 실패라 거부한다."""
    from backtest.wan169_leverage_book import run_cell_variants

    with pytest.raises(ValueError, match="짝입니다"):
        run_cell_variants(_task(confirmation_arms=(ARM_BASE,)), (None,), log=False)


# --------------------------------------------------------------------------- #
# 3~4·7. 실데이터 — 팔 변환이 엔진을 다시 만든 자리
# --------------------------------------------------------------------------- #


def test_base_arm_reproduces_the_engine_exits() -> None:
    """🚨 이 PR의 정본 검산 — 기준 팔 @1.5R 후보 ≡ 엔진이 낸 base+재진입 후보."""
    _skip_without_real_data()
    payload = run_cell(
        _task(confirmation_arms=ARM_ORDER, confirmation_multiples=(1.0, ADOPTED_MULTIPLE)),
        log=False,
    )
    engine = [*payload.candidates["full"], *payload.reentry_candidates["full"]]
    arm = payload.arm_candidates[arm_key(ARM_BASE, ADOPTED_MULTIPLE)]["full"]
    assert len(arm) == len(engine) > 0

    def key(cand: _Candidate) -> tuple[int | None, float | None]:
        return cand.entry_time, cand.entry_price

    for left, right in zip(sorted(engine, key=key), sorted(arm, key=key), strict=True):
        assert left.entry_time == right.entry_time
        assert left.entry_price == right.entry_price
        assert left.exit_time == right.exit_time
        assert left.exit_price == right.exit_price
        assert left.reason is right.reason
        assert left.stop_price == right.stop_price
        assert right.entry_liquidity is Liquidity.MAKER


def test_confirmation_arms_are_a_strict_subset_that_pays_taker() -> None:
    """확인 팔은 기준 팔보다 적게 매매하고 **전부 테이커**다(값으로 센다)."""
    _skip_without_real_data()
    payload = run_cell(
        _task(confirmation_arms=ARM_ORDER, confirmation_multiples=(ADOPTED_MULTIPLE,)),
        log=False,
    )
    base = payload.arm_candidates[arm_key(ARM_BASE, ADOPTED_MULTIPLE)]["full"]
    for arm in (ARM_BAR_CLOSE, ARM_CROSS, ARM_OFFSET):
        cands = payload.arm_candidates[arm_key(arm, ADOPTED_MULTIPLE)]["full"]
        assert 0 < len(cands) <= len(base), arm
        assert all(c.entry_liquidity is Liquidity.TAKER for c in cands), arm


def test_not_asking_for_arms_leaves_everything_untouched() -> None:
    """옵트인 규약 — 안 켜면 `arm_candidates`가 비고 base·재진입이 비트 동일하다."""
    _skip_without_real_data()
    off = run_cell(_task(), log=False)
    on = run_cell(
        _task(confirmation_arms=ARM_ORDER, confirmation_multiples=(ADOPTED_MULTIPLE,)),
        log=False,
    )
    assert off.arm_candidates == {}
    assert on.arm_candidates != {}

    def fingerprint(cands: Sequence[_Candidate]) -> list[tuple[Any, ...]]:
        return [
            (c.trigger_time, c.entry_time, c.entry_price, c.exit_time, c.exit_price, c.reason)
            for c in cands
        ]

    assert fingerprint(off.candidates["full"]) == fingerprint(on.candidates["full"])
    assert fingerprint(off.reentry_candidates["full"]) == fingerprint(on.reentry_candidates["full"])


def test_taker_entry_actually_costs_more() -> None:
    """유동성이 **손익에 반영**된다 — 라벨만 바뀌면 이 이슈는 비용을 싸게 잡은 것이다."""
    _skip_without_real_data()
    from backtest.zone_limit_backtest import _to_trade

    cfg = harness.build_config(_REAL_TF)
    maker = _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=60_000,
        exit_price=110.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
    )
    taker = _Candidate(**{**maker.__dict__, "entry_liquidity": Liquidity.TAKER})
    maker_trade = _to_trade(maker, 10_000.0, cfg)
    taker_trade = _to_trade(taker, 10_000.0, cfg)
    assert maker_trade is not None and taker_trade is not None
    assert taker_trade.entry_price > maker_trade.entry_price  # 슬리피지
    assert taker_trade.realized_pnl < maker_trade.realized_pnl


def test_guard_census_counts_what_sizing_actually_rejects() -> None:
    """가드 인구조사가 사이징과 **같은 식**을 쓴다 — 두 팔이 다른 셋업을 매매하는 정도의 자."""
    _skip_without_real_data()
    payload = run_cell(
        _task(confirmation_arms=ARM_ORDER, confirmation_multiples=(ADOPTED_MULTIPLE,)),
        log=False,
    )
    total = len(payload.arm_candidates[arm_key(ARM_BASE, ADOPTED_MULTIPLE)]["full"])
    counts = [guard_census([payload], arm=ARM_BASE, guard=g) for g in GUARD_POINTS]
    assert all(cut + kept == total for cut, kept in counts)
    # 가드를 조이면 잘리는 셋업이 늘어난다(단조).
    assert [cut for cut, _kept in counts] == sorted(cut for cut, _kept in counts)


# --------------------------------------------------------------------------- #
# 8. 요약은 빈 표에서 판정을 지어내지 않는다
# --------------------------------------------------------------------------- #


def test_summary_does_not_invent_a_verdict_from_an_empty_grid() -> None:
    assert "판정 불가" in rank_stability([], segment="oos_warm", multiple=ADOPTED_MULTIPLE)
    text = build_summary_markdown([], [], [])
    assert "WAN-386" in text
    assert "판정 불가" in text
    # 경고는 표가 비어도 남아 있어야 한다(결론만 사라지고 주의는 남는다).
    assert "엣지 없음" in text


def test_arm_c_offset_is_the_measured_value_not_a_free_parameter() -> None:
    """팔 `C`의 오프셋은 WAN-383 §1이 실측한 팔 `2`의 평균 거리다(1.026%)."""
    assert pytest.approx(0.010259806091708956) == ARM_C_OFFSET


# --------------------------------------------------------------------------- #
# 검산 ③ — 미래 절단 불변 (WAN-166/377 자를 이 경로에도)
# --------------------------------------------------------------------------- #


def test_future_cut_does_not_change_an_already_closed_trade() -> None:
    """봉 안에서 잘라도 **이미 끝난** 거래는 비트 동일하다 — 팔이 미래를 안 본다는 직접 증거.

    확인 팔의 진입은 트리거 시각(과거)이고 청산은 앞으로 걸어가며 찾으므로 원리적으로
    인과적이지만, 「원리적으로」는 배선 실수를 막지 못한다(WAN-345 선례). 값으로 고정한다.
    """
    steps = [
        _step(0, high=101.0, low=99.0, close=100.0),
        _step(60_000, high=116.0, low=100.0, close=115.0),
        _step(120_000, high=140.0, low=80.0, close=130.0),
        _step(180_000, high=145.0, low=120.0, close=140.0),
    ]
    full = simulate_fixed_entry_exits(
        direction=OrderBlockDirection.BULLISH,
        entry_index=0,
        entry_price=100.0,
        stop_price=90.0,
        take_profit_prices=[115.0],
        substeps=steps,
    )
    # 그 거래는 60_000에 끝났다 — 그 뒤를 통째로 잘라도 같은 결과여야 한다.
    for cut in (2, 3):
        truncated = simulate_fixed_entry_exits(
            direction=OrderBlockDirection.BULLISH,
            entry_index=0,
            entry_price=100.0,
            stop_price=90.0,
            take_profit_prices=[115.0],
            substeps=steps[:cut],
        )
        assert truncated == full, cut


def test_future_cut_leaves_finished_arm_candidates_untouched() -> None:
    """팔 변환 층에서도 같다 — 잘린 창에서 끝까지 살아 있는 거래만 `END_OF_DATA`로 바뀐다."""
    probe = _probe(cross_time=60_000, cross_price=103.0, cross_ref_price=100.0)
    steps = [
        *_SYNTH_STEPS,
        _step(180_000, high=131.0, low=127.0, close=130.0),
    ]
    times = [s.time for s in steps]
    long_run = derive_arm_candidates(
        [_synthetic_candidate(probe)],
        arm=ARM_CROSS,
        multiples=[1.5],
        substeps=steps,
        substep_times=times,
    )[1.5]
    short_run = derive_arm_candidates(
        [_synthetic_candidate(probe)],
        arm=ARM_CROSS,
        multiples=[1.5],
        substeps=_SYNTH_STEPS,
        substep_times=_SYNTH_TIMES,
    )[1.5]
    assert long_run[0].exit_time == short_run[0].exit_time
    assert long_run[0].exit_price == short_run[0].exit_price
    assert long_run[0].reason is short_run[0].reason
