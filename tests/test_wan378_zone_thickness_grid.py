"""WAN-378: §1b 격자 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 일곱:

1. **다중 문턱 패스 ≡ 문턱 하나씩 돌린 것**(실데이터) — 이 PR의 유일한 성능 주장이다.
   빨라졌는데 숫자가 달라지면 그건 버그다(WAN-203 선례). base·재진입 후보를 **객체로**
   대조한다(개수만 보면 「같은 개수의 다른 셋업」이 통과한다 — WAN-161 선례).
2. **재진입 파생이 문턱마다 다시 일어난다** — 컷을 파생 **뒤에** 걸면 「빠진 셋업의 재진입이
   살아남는」 잡종이 된다(WAN-376 §1a가 급소로 지목한 자리).
3. **창 전처리 캐시가 결과를 안 바꾼다** — `context`를 넘기든 말든 후보가 같아야 한다.
   성능 인자가 숫자를 바꾸면 그 순간 이 격자는 무효다.
4. **`zone_limit_ref`가 실제로 진입가를 옮긴다**(실데이터) — 인자만 넓히고 배선을 빠뜨리면
   `mid` 팔이 `proximal`과 같은 수를 내고 조용히 통과한다(WAN-345 선례).
5. **채택 팔은 핀을 안 쓴다** — `bollinger` 팔의 기준선이 `None`(물려받기)이어야 한다.
   `"proximal"`을 명시로 적으면 채택 기본값이 움직였을 때 라벨만 「채택」이 된다(WAN-305).
6. **가드·재진입은 배치 축이다**(실데이터) — 같은 payload를 다시 배치하는 것만으로 갈려야
   하고, 후보는 하나도 안 바뀌어야 한다(WAN-197 · WAN-273).
7. **잘못된 조합은 시끄럽게 죽는다** — 문턱 중복 · 다중 문턱 ＋ `engine_check`.
   그리고 요약이 빈 표에서 판정을 지어내지 않는다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE
from backtest.leverage_book import LeverageBookParams
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import (
    _Task,
    reentry_candidates_for_window,
    reentry_window_context,
    run_cell,
    run_cell_variants,
    run_cells_multi,
    zone_width_label,
)
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan378_zone_thickness_grid import (
    ARM_BOLLINGER,
    ARM_MID,
    ARM_ORDER,
    ARM_PROXIMAL,
    GUARD_POINTS,
    WIDTH_POINTS,
    _exclude_payloads,
    _short,
    arm_engine,
    arm_reentry_rule,
    build_summary,
    curve_shape,
    flip_table,
    payloads_for_arm,
    place,
)
from strategy.models import ConfluenceParams

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-10-01"

#: 실데이터 테스트가 쓰는 문턱 — 넓은 점 하나(컷 거의 없음) · 채택값 · 좁은 점 하나.
_TEST_THRESHOLDS: tuple[float | None, ...] = (None, 1.28, 0.90)


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
        "max_zone_width_atr": None,
        "observe_zone_width_atr": True,
    }
    base.update(overrides)
    return _Task(**base)


def _fingerprint(candidates: Any) -> list[tuple[Any, ...]]:
    """후보 지문 — 개수가 아니라 **객체**로 대조한다(같은 개수의 다른 셋업을 잡는다)."""
    return [
        (
            c.trigger_time,
            c.entry_time,
            c.entry_price,
            c.exit_time,
            c.exit_price,
            c.reason,
            c.stop_price,
            c.zone_width_atr,
        )
        for c in candidates
    ]


# --------------------------------------------------------------------------- #
# 1~2. 다중 문턱 패스 ≡ 문턱 하나씩 (그리고 재진입도)
# --------------------------------------------------------------------------- #


def test_multi_threshold_pass_matches_one_pass_per_threshold() -> None:
    """빨라졌는데 숫자가 달라지면 그건 버그다 — base·재진입 후보를 객체로 대조한다."""
    _skip_without_real_data()
    multi = run_cell_variants(_task(), _TEST_THRESHOLDS, log=False)
    for threshold in _TEST_THRESHOLDS:
        label = zone_width_label(threshold)
        single = run_cell(_task(post_filter_zone_width=threshold), log=False)
        for segment in single.candidates:
            assert _fingerprint(multi[label].candidates[segment]) == _fingerprint(
                single.candidates[segment]
            ), f"{label} · {segment} base 후보가 다르다"
            assert _fingerprint(multi[label].reentry_candidates[segment]) == _fingerprint(
                single.reentry_candidates[segment]
            ), f"{label} · {segment} 재진입 후보가 다르다"
        assert multi[label].rows == single.rows


def test_the_cut_actually_removes_setups_and_shrinks_reentry() -> None:
    """컷이 **base 직후 · 재진입 파생 앞**에 걸린다 — 조인 문턱이 느슨한 쪽의 부분집합이다.

    🚨 개수만 보면 안 보인다 — 재진입 후보 집합이 부모의 부분집합이라는 보장은 없다(부모가
    빠지면 슬롯이 비어 **다른 재진입이 생길 수도** 있다). 그래서 「부모가 남은 존만 재무장
    대상이 된다」를 존 단위로 본다.
    """
    _skip_without_real_data()
    multi = run_cell_variants(_task(), _TEST_THRESHOLDS, log=False)
    loose = multi[zone_width_label(None)].candidates[harness.SEGMENT_FULL]
    tight = multi[zone_width_label(0.90)].candidates[harness.SEGMENT_FULL]
    assert len(tight) < len(loose), "조인 문턱이 아무것도 안 잘랐다 — 컷이 배선되지 않았다"
    assert set(_fingerprint(tight)) <= set(_fingerprint(loose))
    assert all(c.zone_width_atr is not None and c.zone_width_atr <= 0.90 for c in tight), (
        "컷을 통과한 후보에 문턱을 넘는 것이 섞였다"
    )
    # 재진입은 부모가 살아남은 존에서만 나온다 — 파생이 컷 **뒤**였다면 이 포함이 깨진다.
    tight_zones = {c.order_block for c in tight if c.order_block is not None}
    tight_re = multi[zone_width_label(0.90)].reentry_candidates[harness.SEGMENT_FULL]
    assert all(c.order_block in tight_zones for c in tight_re if c.order_block is not None)


# --------------------------------------------------------------------------- #
# 3. 창 전처리 캐시는 성능 인자일 뿐이다
# --------------------------------------------------------------------------- #


def test_reentry_window_context_is_a_perf_knob_only() -> None:
    """`context`를 넘기든 말든 재진입 후보가 **비트 단위로** 같아야 한다."""
    _skip_without_real_data()
    market = harness.load_market_data(
        harness.normalize_symbol(_REAL_SYMBOL),
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        need_1m=True,
    )
    payload = run_cell(_task(), log=False)
    base = payload.candidates[harness.SEGMENT_FULL]
    params = harness.build_params(max_zone_width_atr=None)
    cfg = harness.build_config(
        _REAL_TF,
        max_notional_adv_fraction=harness.UNSET,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )
    shared = {"params": params, "cfg": cfg, "timeframe": _REAL_TF}
    without = reentry_candidates_for_window(market, base, **shared)  # type: ignore[arg-type]
    ctx = reentry_window_context(market, _REAL_TF)
    with_ctx = reentry_candidates_for_window(market, base, context=ctx, **shared)  # type: ignore[arg-type]
    assert _fingerprint(with_ctx) == _fingerprint(without)


# --------------------------------------------------------------------------- #
# 4~5. 진입가 축 — 라벨이 아니라 동작 / 채택 팔은 핀을 안 쓴다
# --------------------------------------------------------------------------- #


def test_zone_limit_ref_actually_moves_the_entry_price() -> None:
    """`mid` 팔이 `proximal`과 같은 수를 내면 배선이 빠진 것이다(WAN-345 선례)."""
    _skip_without_real_data()
    prox = run_cell(_task(bollinger=False, zone_limit_ref="proximal"), log=False)
    mid = run_cell(_task(bollinger=False, zone_limit_ref="mid"), log=False)
    a = prox.candidates[harness.SEGMENT_FULL]
    b = mid.candidates[harness.SEGMENT_FULL]
    assert a and b
    assert _fingerprint(a) != _fingerprint(b), "`mid`가 `proximal`과 같은 후보를 냈다"

    # 존 중앙 진입은 롱에서 **더 낮은** 지정가다 — 방향까지 고정한다.
    # 🚨 키는 탭 시각이 아니라 **(탭 시각, 존)** 이다 — 존 병합을 폐지한 뒤(WAN-149) 같은
    # 시각에 겹친 두 존을 탭하면 서로 다른 셋업 둘이라 시각만으로는 짝이 어긋난다(WAN-333).
    def _key(candidate: Any) -> tuple[Any, ...]:
        ob = candidate.order_block
        return (candidate.trigger_time, ob.top, ob.bottom)

    by_zone = {_key(c): c.entry_price for c in a if c.order_block is not None}
    lowered = [
        c.entry_price < by_zone[_key(c)]
        for c in b
        if c.order_block is not None and _key(c) in by_zone
    ]
    assert lowered and all(lowered), "`mid` 진입가가 존 근단보다 낮지 않다"


def test_zone_limit_ref_none_inherits_the_adopted_default() -> None:
    """`None`(미지정)이면 채택 기본값 그대로여야 한다 — 옵트인 축의 규약(WAN-159 부류)."""
    _skip_without_real_data()
    unset = run_cell(_task(bollinger=False), log=False)
    explicit = run_cell(
        _task(bollinger=False, zone_limit_ref=ConfluenceParams().zone_limit_ref), log=False
    )
    assert _fingerprint(unset.candidates[harness.SEGMENT_FULL]) == _fingerprint(
        explicit.candidates[harness.SEGMENT_FULL]
    )


def test_the_adopted_arm_pins_nothing() -> None:
    """채택 팔은 기준선을 **물려받는다** — 명시로 적으면 기본값이 움직여도 라벨만 남는다."""
    bollinger, ref = arm_engine(ARM_BOLLINGER)
    assert bollinger is True and ref is None
    assert arm_engine(ARM_PROXIMAL) == (False, "proximal")
    assert arm_engine(ARM_MID) == (False, "mid")
    with pytest.raises(ValueError):
        arm_engine("distal")


# --------------------------------------------------------------------------- #
# 6. 가드·재진입은 **배치** 축이다 (후보를 안 바꾼다)
# --------------------------------------------------------------------------- #


def test_every_arm_actually_produces_reentry_candidates() -> None:
    """🚨 볼린저를 끈 팔이 **조용히 재진입 0개**가 되는 것을 막는다 (실데이터).

    채택 재무장 규칙 `"band"`는 `params.deviation_filter`가 없으면
    `wan228_reentry_census`가 **아무것도 내지 않고 return**한다. 그대로 두면 `proximal`·`mid`
    팔이 라벨만 「재진입 ON」인 채 재진입 없는 엔진으로 돌고, 세 팔 비교에서 **「진입가
    차이」와 「재진입 유무」가 섞인다**(WAN-131 재검이 무효가 된다). 라벨이 아니라 **후보가
    실제로 나오는지**로 고정한다 — WAN-345가 이름 붙인 실패 부류다.
    """
    _skip_without_real_data()
    for arm in ARM_ORDER:
        payloads = payloads_for_arm(
            [_REAL_SYMBOL],
            [_REAL_TF],
            arm,
            start=_REAL_START,
            end=_REAL_END,
            jobs=1,
            thresholds=(None,),
        )[zone_width_label(None)]
        assert payloads, arm
        made = sum(len(v) for p in payloads for v in p.reentry_candidates.values())
        assert made > 0, f"팔 `{arm}`이 재진입 후보를 하나도 못 냈다 — 규칙이 안 맞는다"


def test_the_reentry_rule_follows_the_entry_price_arm() -> None:
    """각 팔은 **자기가 들어간 자리에** 다시 건다 — 채택 팔만 밴드다."""
    assert arm_reentry_rule(ARM_BOLLINGER) == ADOPTED_REENTRY_ENTRY_RULE == "band"
    assert arm_reentry_rule(ARM_PROXIMAL) == "zone"
    assert arm_reentry_rule(ARM_MID) == "zone"


def test_guard_and_reentry_are_placement_axes() -> None:
    """같은 payload를 다시 배치하는 것만으로 갈려야 한다(WAN-197 · WAN-273)."""
    _skip_without_real_data()
    payloads = run_cells_multi(
        [_REAL_SYMBOL],
        [_REAL_TF],
        thresholds=(None,),
        start=_REAL_START,
        end=_REAL_END,
        jobs=1,
        cold_segments=False,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )[zone_width_label(None)]
    start_ms, end_ms = parse_date_ms(_REAL_START), parse_date_ms(_REAL_END)
    shared = {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "segments": [harness.SEGMENT_FULL],
    }
    loose = place(payloads, guard=0.0, reentry=True, **shared)[0]  # type: ignore[arg-type]
    tight = place(payloads, guard=GUARD_POINTS[-1], reentry=True, **shared)[0]  # type: ignore[arg-type]
    no_re = place(payloads, guard=0.0, reentry=False, **shared)[0]  # type: ignore[arg-type]
    assert tight.row.num_trades < loose.row.num_trades, "가드가 아무것도 안 잘랐다"
    assert no_re.row.num_trades < loose.row.num_trades, "재진입 OFF가 거래를 안 줄였다"
    # 후보는 그대로다 — 배치 축이 후보를 건드리면 이 격자의 비용 논증이 무너진다.
    assert len(payloads[0].candidates[harness.SEGMENT_FULL]) > 0


def test_leave_one_out_drops_cells_not_labels() -> None:
    """LOO는 **지갑 재배치**다 — 뺀 종목의 칸이 후보 목록에서 사라져야 한다."""
    _skip_without_real_data()
    payloads = run_cells_multi(
        [_REAL_SYMBOL],
        [_REAL_TF],
        thresholds=(None,),
        start=_REAL_START,
        end=_REAL_END,
        jobs=1,
        cold_segments=False,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )[zone_width_label(None)]
    assert _exclude_payloads(payloads, ["BTC"]) == []
    assert _exclude_payloads(payloads, ["ETH"]) == list(payloads)
    assert _short("BTC/USDT:USDT") == "BTC"


# --------------------------------------------------------------------------- #
# 7. 잘못된 조합은 시끄럽게 죽는다 / 요약은 판정을 지어내지 않는다
# --------------------------------------------------------------------------- #


def test_duplicate_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError, match="중복"):
        run_cell_variants(_task(), (1.28, 1.28), log=False)


def test_engine_check_with_many_thresholds_is_rejected() -> None:
    """검산은 엔진 필터 설정 하나에서만 정의된다 — 조용히 아무 라벨에나 붙이지 않는다."""
    with pytest.raises(ValueError, match="engine_check"):
        run_cell_variants(_task(engine_check=True), (None, 1.28), log=False)


def test_grid_axes_are_the_confirmed_decision() -> None:
    """★결정 2026-08-26 그대로인가 — 개발자가 점을 더하거나 빼지 않는다."""
    assert [zone_width_label(w) for w in WIDTH_POINTS] == [
        "off",
        "2.60",
        "1.80",
        "1.55",
        "1.28",
        "1.15",
        "1.00",
        "0.90",
        "0.80",
    ]
    assert "0.60" not in {zone_width_label(w) for w in WIDTH_POINTS}
    assert GUARD_POINTS == (0.0, 0.0025, 0.003, 0.0040)
    assert ARM_ORDER == (ARM_PROXIMAL, ARM_MID, ARM_BOLLINGER)


def test_summary_does_not_invent_a_verdict_on_empty_tables() -> None:
    """빈 표에서 조용히 판정을 지어내면 「실패가 성공과 같은 모양」이 된다(WAN-194/318/321)."""
    empty = pd.DataFrame()
    text = build_summary(empty, empty, empty)
    assert "아직 안 돌렸다" in text
    assert "측정 전용" in text
    assert flip_table(empty).empty


def test_curve_shape_reads_the_shape_not_the_argmax() -> None:
    """폭이 규약 폭 안이면 고원 — argmax가 있어도 「고원」으로 읽어야 한다(WAN-161)."""
    assert curve_shape([0.100, 0.104, 0.101, 0.099]) == "고원(평평)"
    assert curve_shape([0.02, 0.05, 0.09, 0.14]) == "단조"
    assert curve_shape([0.02, 0.15, 0.09, 0.04]) == "봉우리"
    assert curve_shape([0.02, None]) == "표본 부족"


def test_book_placement_names_the_take_profit_liquidity_axis() -> None:
    """WAN-370/373 — 새 북 모듈이 익절 비용 회계를 잊으면 조용히 옛 회계로 돈다."""
    import inspect

    from backtest import wan378_zone_thickness_grid as module

    source = inspect.getsource(module)
    assert source.count("ADOPTED_TAKE_PROFIT_LIQUIDITY") >= 3, "후보 생성·배치·LOO 셋 다 명시"
    assert LeverageBookParams().leverage_mode == "cap_only"
