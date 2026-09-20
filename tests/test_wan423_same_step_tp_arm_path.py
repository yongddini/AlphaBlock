"""WAN-423 — 「같은 분 익절 금지」가 **배수별 팔 경로에서도 실제로 동작하는지**.

이 테스트가 막는 사고는 하나다: `run_cells(no_same_step_tp=True)`가 인자를 받아 놓고
배수별 팔(`derive_arm_candidates` → `simulate_fixed_entry_exits`)에는 **안 넘겨서**,
「보수화했다」는 라벨만 붙고 숫자는 낙관 그대로 나오던 것(WAN-91/95/112/123/159/194 부류).

그래서 **라벨이 아니라 동작으로** 건다 — 산출된 후보에 `same_step_take_profit=True`가
남아 있는지, 그리고 끄면 예전과 **비트 단위로 같은지**.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backtest.confirmation_arm import ARM_BASE, derive_arm_candidates
from backtest.models import ExitReason, PositionSide
from backtest.substep import FixedEntryExit, SubStep, simulate_fixed_entry_exits
from backtest.zone_limit_backtest import _Candidate
from strategy.models import OrderBlock, OrderBlockDirection

HTF_MS = 3_600_000
MINUTE = 60_000


def _steps(rows: list[tuple[int, float, float, float]]) -> list[SubStep]:
    return [
        SubStep(time=t, high=high, low=low, close=close, htf_bar_time=(t // HTF_MS) * HTF_MS)
        for t, high, low, close in rows
    ]


def _same_minute_round_trip() -> list[SubStep]:
    """진입 분에서 **저가로 체결되고 같은 분 안에 고가가 익절가를 넘는** 경로."""
    return _steps(
        [
            (0, 100.0, 99.0, 99.5),
            (MINUTE, 103.0, 99.0, 102.0),  # 진입 분: 저가 99 · 고가 103
            (2 * MINUTE, 101.0, 100.5, 100.8),
            (3 * MINUTE, 101.0, 90.0, 90.5),  # 손절(90)
        ]
    )


def _run(
    steps: list[SubStep],
    *,
    no_same_step_tp: bool = False,
    minutes: frozenset[int] | None = None,
) -> FixedEntryExit:
    (done,) = simulate_fixed_entry_exits(
        direction=OrderBlockDirection.BULLISH,
        entry_index=1,
        entry_price=100.0,
        stop_price=90.0,
        take_profit_prices=[102.0],
        substeps=steps,
        no_same_step_tp=no_same_step_tp,
        no_same_step_tp_minutes=minutes,
    )
    return done


def test_fixed_entry_same_step_tp_is_blocked_only_when_asked() -> None:
    steps = _same_minute_round_trip()

    baseline = _run(steps)
    assert baseline.same_step_take_profit is True
    assert baseline.exit_time == MINUTE

    blocked = _run(steps, no_same_step_tp=True)
    # 진입 분의 익절은 인정하지 않는다 — 다음 분부터 다시 본다. 이 경로는 손절로 끝난다.
    assert blocked.same_step_take_profit is False
    assert blocked.exit_reason is not None and blocked.exit_reason.name == "STOP_LOSS"
    assert blocked.exit_time == 3 * MINUTE


def test_fixed_entry_targeted_minutes_block_only_that_minute() -> None:
    steps = _same_minute_round_trip()

    other = _run(steps, minutes=frozenset({7 * MINUTE}))
    assert other.same_step_take_profit is True  # 다른 분만 막았으니 그대로 난다

    hit = _run(steps, minutes=frozenset({MINUTE}))
    assert hit.same_step_take_profit is False


def test_fixed_entry_rejects_both_axes_together() -> None:
    with pytest.raises(ValueError, match="같은 축의 두 값"):
        simulate_fixed_entry_exits(
            direction=OrderBlockDirection.BULLISH,
            entry_index=0,
            entry_price=100.0,
            stop_price=90.0,
            take_profit_prices=[102.0],
            substeps=_same_minute_round_trip(),
            no_same_step_tp=True,
            no_same_step_tp_minutes=frozenset({0}),
        )


def _candidate(steps: list[SubStep]) -> _Candidate:
    block = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=101.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=MINUTE,
        entry_price=100.0,
        exit_time=MINUTE,
        exit_price=102.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
        take_profit_price=102.0,
        order_block=block,
        zone_key=frozenset({0}),
    )


def test_arm_path_honours_the_flag() -> None:
    """배수별 팔에서도 같은 분 익절이 **실제로** 사라진다(라벨이 아니라 후보의 값으로)."""
    steps = _same_minute_round_trip()
    times = [s.time for s in steps]
    cand = _candidate(steps)

    loose = derive_arm_candidates(
        [cand], arm=ARM_BASE, multiples=[0.2], substeps=steps, substep_times=times
    )
    assert [c.same_step_take_profit for c in loose[0.2]] == [True]

    strict = derive_arm_candidates(
        [cand],
        arm=ARM_BASE,
        multiples=[0.2],
        substeps=steps,
        substep_times=times,
        no_same_step_tp=True,
    )
    assert [c.same_step_take_profit for c in strict[0.2]] == [False]
    assert strict[0.2][0].reason is ExitReason.STOP_LOSS


def test_run_cells_wires_the_flag_into_the_arm_path() -> None:
    """🚨 배선 가드 — `run_cells`의 팔 호출이 `task.no_same_step_tp`를 **넘기는지**.

    이 줄이 빠져 있던 것이 WAN-423이다. 실데이터 격자를 돌리지 않고 소스에서 확인한다
    (격자를 돌려 잡으려면 칸 하나에 수십 분이고, CI에는 DB가 없다).
    """
    source = Path("backtest/wan169_leverage_book.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "derive_arm_candidates"
    ]
    assert calls, "derive_arm_candidates 호출부를 못 찾았습니다 — 배선 가드가 무력해졌습니다."
    for call in calls:
        passed = {kw.arg for kw in call.keywords}
        assert "no_same_step_tp" in passed, "팔 호출이 no_same_step_tp를 안 넘깁니다(WAN-423)."
        assert "no_same_step_tp_minutes" in passed, (
            "팔 호출이 no_same_step_tp_minutes를 안 넘깁니다(WAN-423)."
        )
