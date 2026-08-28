"""확인 진입 팔 — 트리거 판독과 후보 변환 (WAN-386 §0 엔진).

WAN-383 §0이 만든 것은 **관측**(`ConfirmationProbe` — 트리거가 언제 오나)이지 **팔**(그때
실제로 진입하는 경로)이 아니었다. 이 모듈이 그 잔여를 채운다: 채택 북이 이미 낸 후보를
받아 **진입 시각·진입가·주문 종류·익절 목표만** 갈아끼운 후보를 낸다.

## 왜 「셋업은 그대로」인가

이슈(WAN-383/386)의 설계가 *「셋업·나머지 전부 동일 · 진입 시점만 다르다」*이다. 그래서 이
변환은 **후보를 새로 만들지 않는다** — 같은 탭·같은 존·같은 손절 참조가를 쓰고, 바뀌는 것은
`entry_time`·`entry_price`·`entry_liquidity`·청산뿐이다. 두 팔이 다른 셋업을 매매하면 그
차이가 「진입 시점의 값어치」인지 「다른 셋업을 골랐다」인지 못 가른다.

📌 그 대가로 **재무장 일정(재진입)은 기준 팔의 것을 쓴다** — 재진입 후보는 base 후보의
per-cell 시퀀싱에서 나오므로(WAN-261) 팔마다 다시 파생하면 재무장 시점까지 팔에 따라
움직인다. 이 이슈가 재는 것은 **진입 시점 하나**라 그 축을 고정한다(알려진 한계 · 결정문에
명시). 변환 자체는 base 후보와 재진입 후보 **양쪽에** 걸린다(WAN-345 부류 방지).

## 비용을 싸게 잡지 않는다

`기준`은 존 근단에 지정가를 걸어 두고 가격이 **내려오면** 체결이라 메이커(2bp · 슬리피지 0)
지만, 확인 팔의 트리거는 전부 **위쪽**이라 지정가로 걸면 즉시 시장가로 체결된다. 그래서
확인 팔 후보는 `entry_liquidity=TAKER`(4bp + 슬리피지 5bp)를 달고 나간다 — 이 이슈가 지는
가장 흔한 방식이 「비용을 실제보다 싸게 잡는 것」이다(WAN-370).

같은 이유로 팔 `2`의 진입가는 `P*`가 아니라 **`max(P*, 그 순간 현재가)`** 다(WAN-383 §3-3 —
`P*`가 이미 현재가 아래인 「시그널선 따라잡기」 부류에 없는 가격 이점을 지어내지 않는다).
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence

from backtest.models import ExitReason
from backtest.substep import (
    FixedEntryExit,
    SubStep,
    ZoneLimitStatus,
    simulate_fixed_entry_exits,
)
from backtest.zone_limit_backtest import CONFIRMATION_MAX_OFFSET, ConfirmationProbe, _Candidate
from common.costs import Liquidity
from strategy.models import OrderBlockDirection, SignalExitReason

ARM_BASE = "기준"
ARM_BAR_CLOSE = "1_봉마감"
ARM_CROSS = "2_교차"
ARM_OFFSET = "C_고정오프셋"
#: 표·격자의 팔 순서(기준 팔 포함). WAN-383 §1이 살려 보낸 세 팔 + 대조.
ARM_ORDER: tuple[str, ...] = (ARM_BASE, ARM_BAR_CLOSE, ARM_CROSS, ARM_OFFSET)
CONFIRMATION_ARMS: tuple[str, ...] = (ARM_BAR_CLOSE, ARM_CROSS, ARM_OFFSET)

#: 팔 `C`의 고정 오프셋 — **데이터가 정한 값이지 자유 파라미터가 아니다**.
#:
#: 팔 `2`가 실제로 기다린 평균 상대 거리(`full` 구간 전수)이고, WAN-383 §1이 채택 북 위에서
#: 실측해 `wan383_confirmation_reach.csv`의 `C_고정오프셋` 행에 그대로 실려 있다(1.026%).
#: 🚨 여기서 다시 재면 모집단이 달라져(`§3`은 후보 층, §1은 배치 층) `2 − C` 통제가 **다른
#: 오프셋 두 개를 비교**하게 된다 — 그래서 §1이 낸 값을 **입력으로 못 박는다**(WAN-131이
#: 볼린저에서 쓴 통제의 규약: 통제 팔은 실험 팔의 평균에 맞춰야 뜻이 있다).
ARM_C_OFFSET = 0.010259806091708956

_EXIT_REASON: dict[SignalExitReason, ExitReason] = {
    SignalExitReason.STOP_LOSS: ExitReason.STOP_LOSS,
    SignalExitReason.TAKE_PROFIT: ExitReason.TAKE_PROFIT,
}


def arm_trigger(
    probe: ConfirmationProbe | None, arm: str, *, offset: float = ARM_C_OFFSET
) -> tuple[int, float] | None:
    """이 팔의 (트리거 시각, **실제로 물릴 진입가**). 트리거가 안 왔으면 `None`.

    🚨 팔 `2`의 진입가는 `P*`가 아니라 `max(P*, 그 순간 현재가)`다 — `P*`가 이미 현재가
    아래면(시그널선 따라잡기) 지정가로 걸어도 즉시 시장가로 체결된다. 그 부류를 `P*`로
    체결시키면 **없는 가격 이점을 지어내는 것**이고, 이 이슈가 지는 가장 흔한 방식이
    「비용을 실제보다 싸게 잡는 것」이다(WAN-370).
    """
    if probe is None:
        return None
    if arm == ARM_BAR_CLOSE:
        if probe.bar_close_time is None or probe.bar_close_price is None:
            return None
        return probe.bar_close_time, probe.bar_close_price
    if arm == ARM_CROSS:
        if probe.cross_time is None or probe.cross_price is None:
            return None
        ref = probe.cross_ref_price if probe.cross_ref_price is not None else probe.entry_price
        return probe.cross_time, max(probe.cross_price, ref)
    if arm == ARM_OFFSET:
        if offset > CONFIRMATION_MAX_OFFSET:
            raise ValueError(
                f"오프셋 {offset:.4%}가 사다리 상한 {CONFIRMATION_MAX_OFFSET:.2%}를 "
                "넘습니다 — 첫 터치를 답할 수 없습니다."
            )
        return probe.first_touch(offset)
    raise ValueError(f"알 수 없는 팔: {arm!r}")


def take_profit_price(
    *, is_long: bool, entry_price: float, stop_price: float, multiple: float
) -> float | None:
    """고정 R 익절 목표 — `zone_limit_backtest._resolve_take_profit`의 `fixed_r` 갈래와 같은 식."""
    risk = entry_price - stop_price if is_long else stop_price - entry_price
    if risk <= 0:
        return None
    signed = risk * multiple
    return entry_price + (signed if is_long else -signed)


def derive_arm_candidates(
    candidates: Sequence[_Candidate],
    *,
    arm: str,
    multiples: Sequence[float],
    substeps: Sequence[SubStep],
    substep_times: Sequence[int],
    offset: float = ARM_C_OFFSET,
) -> dict[float, list[_Candidate]]:
    """한 팔의 「익절 배수 → 후보」. 후보 **집합**은 안 만들고 진입·청산만 갈아끼운다.

    - `기준` 팔은 후보의 자기 체결(`entry_time`/`entry_price`)을 그대로 쓰고 메이커를 문다 —
      배수 1.5R에서는 엔진이 낸 청산과 **비트 단위로 같아야** 한다(검산 ①/②).
    - 확인 팔은 `arm_trigger`가 낸 (시각, 가격)으로 시장가 진입하고 **테이커**를 문다.
      트리거가 안 온 셋업은 매매하지 않으므로 **후보에서 빠진다**.
    - 손절 참조가·존·탭 메타데이터는 팔 사이에서 **불변**이다(1R의 분모가 진입가라 손절폭은
      팔마다 달라지지만 그것은 진입가가 움직인 결과이지 손절선을 옮긴 것이 아니다).

    반환은 `multiples` 각 값 → 후보 목록이고, **같은 팔의 배수들은 진입 집합이 비트 일치**
    한다(익절은 청산만 바꾼다 — WAN-137/143 훅과 같은 성질. 회귀 테스트가 고정한다).
    """
    out: dict[float, list[_Candidate]] = {m: [] for m in multiples}
    if len(out) != len(multiples):
        raise ValueError(f"익절 배수가 중복입니다: {list(multiples)}")
    taker = arm != ARM_BASE
    for cand in candidates:
        is_long = cand.side.sign > 0
        if taker:
            trigger = arm_trigger(cand.confirmation, arm, offset=offset)
            if trigger is None:
                continue
            entry_time, entry_price = trigger
        else:
            entry_time, entry_price = cand.entry_time, cand.entry_price
        index = bisect.bisect_left(substep_times, entry_time)
        if index >= len(substep_times) or substep_times[index] != entry_time:
            # 트리거 시각은 서브스텝에서 나온 값이라 여기 오면 배선이 어긋난 것이다 —
            # 조용히 건너뛰면 팔마다 다른 표본을 재게 된다(WAN-95 부류).
            raise ValueError(f"트리거 시각 {entry_time}이 서브스텝에 없습니다({arm}).")
        targets = [
            take_profit_price(
                is_long=is_long,
                entry_price=entry_price,
                stop_price=cand.stop_price,
                multiple=m,
            )
            for m in multiples
        ]
        exits = simulate_fixed_entry_exits(
            direction=OrderBlockDirection.BULLISH if is_long else OrderBlockDirection.BEARISH,
            entry_index=index,
            entry_price=entry_price,
            stop_price=cand.stop_price,
            take_profit_prices=targets,
            substeps=substeps,
        )
        for multiple, target, done in zip(multiples, targets, exits, strict=True):
            out[multiple].append(
                _rebuilt(
                    cand,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    take_profit=target,
                    liquidity=Liquidity.TAKER if taker else Liquidity.MAKER,
                    done=done,
                    substeps=substeps,
                )
            )
    return out


def _rebuilt(
    cand: _Candidate,
    *,
    entry_time: int,
    entry_price: float,
    take_profit: float | None,
    liquidity: Liquidity,
    done: FixedEntryExit,
    substeps: Sequence[SubStep],
) -> _Candidate:
    """청산이 확정된 팔 후보 하나. 데이터 종료까지 열려 있으면 마지막 종가로 강제 청산한다."""
    if done.status is ZoneLimitStatus.FILLED_EXITED:
        assert done.exit_time is not None and done.exit_price is not None
        exit_time, exit_price = done.exit_time, done.exit_price
        reason = _EXIT_REASON[done.exit_reason] if done.exit_reason else ExitReason.STOP_LOSS
    else:
        exit_time, exit_price = substeps[-1].time, substeps[-1].close
        reason = ExitReason.END_OF_DATA
    return _Candidate(
        side=cand.side,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        reason=reason,
        stop_price=cand.stop_price,
        take_profit_price=take_profit,
        entry_liquidity=liquidity,
        is_reentry=cand.is_reentry,
        penetration=done.penetration,
        same_step_take_profit=done.same_step_take_profit,
        entry_after_invalidation=cand.entry_after_invalidation,
        order_block=cand.order_block,
        tap_index=cand.tap_index,
        zone_key=cand.zone_key,
        zone_width_atr=cand.zone_width_atr,
        macd_hist=cand.macd_hist,
        macd_hist_prev=cand.macd_hist_prev,
        confirmation=cand.confirmation,
        trigger_time=cand.trigger_time,
        mfe_r=done.mfe_r,
        mae_r=done.mae_r,
        exit_extreme=done.exit_extreme,
        refinement_tf=cand.refinement_tf,
        adv_usd=cand.adv_usd,
    )
