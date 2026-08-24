"""WAN-366: 인과 엔진 부품 사다리 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 여섯:

1. **사다리 꼭대기가 오늘의 채택 기본값이다** — 존폭 문턱·취소 시점·손절폭 가드 셋 중
   하나라도 움직이면 `_assert_adopted_base`가 시끄럽게 죽는다. 라벨만 남고 표가 다른
   엔진을 가리키는 것이 이 저장소가 반복해 겪은 실패다(WAN-91/95/112/123/159).
2. **가드는 사이징 축이라 후보를 못 바꾼다** — 이 사다리의 컴퓨트 설계(생성 3회)가 통째로
   그 성질에 걸려 있어, 어긋나면 `_check_guard_axis`가 죽는다.
3. **가드 노브가 실제로 걸린다** — `build_config(min_stop_distance_fraction=)`이 값을
   바꾸고, 안 주면 채택 0.3%를 **손대지 않는다**(비트 재현).
4. **볼린저 끄기가 실제로 후보를 바꾼다**(실데이터) — 인자를 넓히고 배선을 빠뜨리면
   기준선과 같은 수가 나와 조용히 통과한다(WAN-345 선례).
5. **존폭 필터 끄기는 후보의 상위집합을 만든다**(실데이터) — 개수가 아니라 **집합**으로
   건다(개수만 보면 같은 개수의 다른 셋업이 통과한다, WAN-161 선례).
6. **요약이 판정을 지어내지 않는다** — 단이 덜 찼으면 「판정 불가」, 부품이 없으면 「없다」,
   앞구간→뒷구간 부호 반전은 경고로 찍는다.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import run_cells
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan366_causal_ablation import (
    ADOPTED_RUNG,
    ADOPTED_STOP_GUARD,
    ADOPTED_ZONE_WIDTH,
    BRANCH_RUNG,
    CHAIN,
    GUARD_PAIRS,
    LADDER,
    NET_R_NOISE,
    RUNGS,
    RUNGS_BY_NAME,
    STEPS,
    LadderRow,
    _assert_adopted_base,
    _assert_guard_pairs,
    _check_guard_axis,
    _guard_arg,
    bucket_label,
    build_summary,
    census_cell,
    generation_of,
    increments,
    rows_to_frame,
    rungs_to_generations,
)
from execution.sizing import PositionSizingParams
from strategy.models import ConfluenceParams

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-07-01"


# --------------------------------------------------------------------------- #
# 1 · 사다리 꼭대기 = 오늘의 채택 기본값
# --------------------------------------------------------------------------- #


def test_adopted_base_matches_today_defaults() -> None:
    """오늘의 기본값에서 통과한다 — 이 테스트가 깨지면 표의 제목이 거짓이 된 것이다."""
    _assert_adopted_base()
    assert ConfluenceParams().max_zone_width_atr == ADOPTED_ZONE_WIDTH
    assert ConfluenceParams().invalidation_cancel == "bar_close"
    assert PositionSizingParams().min_stop_distance_fraction == ADOPTED_STOP_GUARD


def test_adopted_base_rejects_retrospective_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """취소 시점이 소급으로 되돌아가면 죽는다 — 이 표는 **인과 엔진** 위의 사다리다."""

    def _retro() -> Any:
        return ConfluenceParams().model_copy(update={"invalidation_cancel": "bar_open"})

    monkeypatch.setattr(
        "backtest.wan366_causal_ablation.ConfluenceParams", lambda: _retro(), raising=True
    )
    with pytest.raises(AssertionError, match="취소 시점"):
        _assert_adopted_base()


def test_only_the_top_rung_is_adopted() -> None:
    """`is_adopted`가 정확히 한 단이다 — 검산 (a)를 걸 수 있는 단이 여럿이면 뜻을 잃는다."""
    adopted = [r.name for r in RUNGS if r.is_adopted]
    assert adopted == [ADOPTED_RUNG]
    assert LADDER[-1] == ADOPTED_RUNG


def test_generation_grouping_is_three_not_five() -> None:
    """다섯 단이 **생성 세 번**을 나눠 쓴다 — 이 모듈의 컴퓨트 설계 전부다."""
    assert rungs_to_generations(LADDER) == ("G0", "G1", "G2")
    assert [r.name for r in generation_of("G2")] == ["L2", "L3", "L4"]
    # 같은 그룹의 단들은 후보 생성 축(볼린저·존폭)이 **같아야** 한다 — 다르면 한 번의
    # 생성으로 못 먹인다.
    for gen in ("G0", "G1", "G2"):
        rungs = generation_of(gen)
        assert len({(r.bollinger, r.zone_width) for r in rungs}) == 1


def test_guard_arg_is_none_at_adopted_value() -> None:
    """채택 가드면 배치에 `None`(손대지 않는다)을 넘긴다 — 비트 재현의 조건."""
    assert _guard_arg(RUNGS_BY_NAME[ADOPTED_RUNG]) is None
    assert _guard_arg(RUNGS_BY_NAME["L2"]) == 0.0


# --------------------------------------------------------------------------- #
# 2·3 · 가드는 사이징 축이다
# --------------------------------------------------------------------------- #


def test_build_config_guard_knob() -> None:
    """안 주면 채택 0.3% 그대로, 주면 그 값으로 — `offset_bps` 규약."""
    default = harness.build_config("1h")
    assert default.risk_sizing is not None
    assert default.risk_sizing.min_stop_distance_fraction == ADOPTED_STOP_GUARD

    off = harness.build_config("1h", min_stop_distance_fraction=0.0)
    assert off.risk_sizing is not None
    assert off.risk_sizing.min_stop_distance_fraction == 0.0
    # 같이 얹는 다른 사이징 필드를 지우지 않는다.
    assert (
        off.risk_sizing.max_notional_adv_fraction == default.risk_sizing.max_notional_adv_fraction
    )


def _row(level: str, segment: str, **over: Any) -> LadderRow:
    rung = RUNGS_BY_NAME[level]
    base: dict[str, Any] = dict(
        level=level,
        adds=rung.adds,
        generation=rung.gen,
        bollinger=rung.bollinger,
        zone_width_atr=rung.zone_width,
        stop_guard=rung.guard,
        reentry=rung.reentry,
        segment=segment,
        num_cells=48,
        num_candidates=1000,
        num_trades=500,
        win_rate=0.5,
        total_return=0.1,
        mean_net_r=0.0,
        net_r=0.0,
        profit_factor=1.0,
        max_drawdown=0.2,
        return_over_mdd=0.5,
        ruin=False,
        peak_concurrency=5,
        max_concurrent_risk=0.1,
        liquidation_events=0,
        reentry_trades=0,
    )
    base.update(over)
    return LadderRow(**base)


def test_guard_axis_check_rejects_candidate_drift() -> None:
    """가드만 바꿨는데 후보 수가 달라지면 죽는다 — 컴퓨트 설계가 틀렸다는 뜻이다."""
    ok = {
        "L2": [_row("L2", PRIMARY_OOS, num_candidates=900)],
        "L3": [_row("L3", PRIMARY_OOS, num_candidates=900)],
    }
    _check_guard_axis(ok)  # 예외 없음.

    drifted = {
        "L2": [_row("L2", PRIMARY_OOS, num_candidates=900)],
        "L3": [_row("L3", PRIMARY_OOS, num_candidates=880)],
    }
    with pytest.raises(AssertionError, match="검산\\(b\\)"):
        _check_guard_axis(drifted)


# --------------------------------------------------------------------------- #
# §0 인구조사
# --------------------------------------------------------------------------- #


def test_bucket_edges_split_at_the_adopted_threshold() -> None:
    """채택 문턱이 **버킷 경계**다 — 그래야 「필터가 사는 쪽」이 표에서 갈라진다."""
    label_at, _lo, hi = bucket_label(ADOPTED_ZONE_WIDTH)
    assert hi == ADOPTED_ZONE_WIDTH  # 문턱은 「이하」라 좁은 쪽에 붙는다(엔진과 같은 부등호).
    _label_over, lo_over, _hi_over = bucket_label(ADOPTED_ZONE_WIDTH + 1e-6)
    assert lo_over == ADOPTED_ZONE_WIDTH
    assert label_at != _label_over
    # 아주 넓은 존은 마지막 열린 버킷으로 간다(경계를 넘겨도 안 떨어진다).
    _label_far, _lo_far, hi_far = bucket_label(99.0)
    assert math.isinf(hi_far)


def test_census_uses_the_engine_ruler() -> None:
    """실데이터 — 존폭 등급이 매겨지고 무효화 봉 탭이 실제로 잡힌다."""
    probe = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        need_1m=False,
        funding=False,
    )
    if probe.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")
    rows = census_cell(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    assert rows
    graded = [r for r in rows if not math.isnan(r.bucket_lo)]
    assert graded, "등급이 매겨진 버킷이 하나도 없습니다 — ATR 배선을 확인하세요."
    assert any(r.break_bar_taps > 0 for r in rows), "무효화 봉 탭이 0건 — 인구조사가 헛돌았다."
    # 「좁다」 판정은 채택 문턱과 같은 부등호를 쓴다.
    for row in graded:
        assert row.narrow == (row.bucket_hi <= ADOPTED_ZONE_WIDTH)


# --------------------------------------------------------------------------- #
# 4·5 · 후보 생성 축이 실제로 걸린다 (실데이터)
# --------------------------------------------------------------------------- #


def _shared_kwargs() -> dict[str, Any]:
    return {
        "start": _REAL_START,
        "end": _REAL_END,
        "jobs": 1,
        "cold_segments": False,
        "engine_check": False,
        "reentry": False,
        "adv_fraction": ADOPTED_CELL_KWARGS["adv_fraction"],
    }


def _skip_without_real_data() -> None:
    """🚨 게이트는 `run_cells` **호출 전에** 판정한다 — 안 그러면 CI의 빈 DB가 skip이 아니라
    실패로 끝난다(이 저장소가 이미 겪은 실패)."""
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")


def test_bollinger_knob_changes_candidates() -> None:
    """볼린저를 끄면 진입가가 존 근단에 남아 **후보가 달라진다** — 인자만 넓히고 배선을
    빠뜨리면 기준선과 같은 수가 나와 조용히 통과한다(WAN-345 선례)."""
    _skip_without_real_data()
    on = run_cells([_REAL_SYMBOL], [_REAL_TF], **_shared_kwargs())
    off = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        bollinger=False,
        **_shared_kwargs(),
    )
    on_prices = [c.entry_price for c in on[0].candidates[harness.SEGMENT_FULL]]
    off_prices = [c.entry_price for c in off[0].candidates[harness.SEGMENT_FULL]]
    assert len(off_prices) > len(on_prices), (
        "볼린저를 껐는데 후보가 안 늘었습니다(규칙 3 기각이 사라져야 한다)."
    )


def test_zone_width_filter_off_is_a_superset() -> None:
    """필터를 끄면 켠 판의 셋업이 **부분집합**이다 — 개수가 아니라 집합으로 건다.

    개수만 보면 「같은 개수의 다른 셋업이 통과」한 경우를 놓친다(WAN-161이 같은 자리에서
    쓴 불변식). 필터는 후보를 **거르기만** 하므로 상위집합이어야 맞다.
    """
    _skip_without_real_data()
    on = run_cells([_REAL_SYMBOL], [_REAL_TF], **_shared_kwargs())
    off = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        max_zone_width_atr=None,
        **_shared_kwargs(),
    )
    keys_on = {c.trigger_time for c in on[0].candidates[harness.SEGMENT_FULL]}
    keys_off = {c.trigger_time for c in off[0].candidates[harness.SEGMENT_FULL]}
    assert keys_on, "필터 켠 판에 후보가 없어 부분집합 검사가 성립하지 않습니다."
    assert keys_on < keys_off, "필터를 껐는데 켠 판이 진부분집합이 아닙니다."


def test_defaults_are_bit_identical_to_adopted() -> None:
    """인자를 안 주면 **채택 후보** 그대로 — 사다리 축이 기본 실행에 새지 않는다."""
    _skip_without_real_data()
    a = run_cells([_REAL_SYMBOL], [_REAL_TF], **_shared_kwargs())
    b = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        bollinger=True,
        max_zone_width_atr=harness.UNSET,
        **_shared_kwargs(),
    )
    left = [
        (c.trigger_time, c.entry_price, c.stop_price) for c in a[0].candidates[harness.SEGMENT_FULL]
    ]
    right = [
        (c.trigger_time, c.entry_price, c.stop_price) for c in b[0].candidates[harness.SEGMENT_FULL]
    ]
    assert left == right


# --------------------------------------------------------------------------- #
# 6 · 요약은 판정을 지어내지 않는다
# --------------------------------------------------------------------------- #


def test_increments_need_both_rungs() -> None:
    """이웃 단이 없으면 증분을 **안 낸다**(없는 단을 0으로 메우지 않는다)."""
    frame = rows_to_frame([_row("L0", PRIMARY_OOS), _row("L2", PRIMARY_OOS)])
    assert increments(frame) == []

    paired = rows_to_frame(
        [
            _row("L0", PRIMARY_OOS, mean_net_r=-0.10),
            _row("L1", PRIMARY_OOS, mean_net_r=-0.02),
        ]
    )
    incs = increments(paired)
    assert [i.step for i in incs] == ["L0→L1"]
    assert incs[0].d_mean_net_r == pytest.approx(0.08)


def test_summary_reports_no_adder_when_there_is_none() -> None:
    """두 구간 다 음수면 「값을 더하는 부품이 없다」로 찍고, 다음 줄도 「쓸 축이 없다」다."""
    rows = []
    for segment in (harness.SEGMENT_IS, PRIMARY_OOS):
        rows += [
            _row("L0", segment, mean_net_r=-0.05),
            _row("L1", segment, mean_net_r=-0.09),
        ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert "부품이 없다" in text
    assert "쓸 축이 없다" in text


def test_summary_flags_is_to_oos_flip() -> None:
    """앞구간에서만 좋은 부품은 **채택 근거가 아니다** — 요약이 그 사실을 찍는다."""
    rows = [
        _row("L0", harness.SEGMENT_IS, mean_net_r=0.0),
        _row("L1", harness.SEGMENT_IS, mean_net_r=0.06),
        _row("L0", PRIMARY_OOS, mean_net_r=0.0),
        _row("L1", PRIMARY_OOS, mean_net_r=-0.04),
    ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert "부호가 뒤집힌" in text
    assert "부품이 없다" in text


def test_summary_ignores_noise_sized_increments() -> None:
    """±0.005R 안의 증분은 「0과 구분되지 않는다」로 본다(WAN-120 함정 방지)."""
    tiny = NET_R_NOISE / 2
    rows = [
        _row("L0", harness.SEGMENT_IS, mean_net_r=0.0),
        _row("L1", harness.SEGMENT_IS, mean_net_r=tiny),
        _row("L0", PRIMARY_OOS, mean_net_r=0.0),
        _row("L1", PRIMARY_OOS, mean_net_r=tiny),
    ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert "부품이 없다" in text


def test_summary_is_undecided_without_the_ladder() -> None:
    frame = rows_to_frame([_row("L0", PRIMARY_OOS)])
    text = build_summary(frame, pd.DataFrame(), pd.DataFrame())
    assert "판정 불가" in text


# --------------------------------------------------------------------------- #
# 7 · WAN-368 — 분기 단 `L0g`(존 단독 ＋ 가드)
# --------------------------------------------------------------------------- #


def test_branch_rung_shares_the_base_generation() -> None:
    """`L0g`는 `L0`이 만든 후보를 **재시퀀싱만** 한다 — 이 모듈의 컴퓨트 예산 전부다.

    가드는 사이징 축이라 후보를 안 바꾸므로 둘은 한 생성(`G0`)을 나눠 써야 한다. 그룹이
    갈리면 후보가 따로 만들어져 검산 (b)가 **비교할 것이 없어 조용히 통과한다**.
    """
    assert [r.name for r in generation_of("G0")] == ["L0", "L0g"]
    assert rungs_to_generations(["L0g"]) == ("G0",)
    # 생성 그룹은 여전히 셋이다 — 단이 하나 늘어도 컴퓨트는 안 는다.
    assert rungs_to_generations(LADDER) == ("G0", "G1", "G2")


def test_branch_rung_turns_off_the_loss_components() -> None:
    """`L0g`가 실제로 「볼린저 끔 · 존폭 필터 끔 · 가드 켬 · 재진입 끔」이다.

    🚨 존폭 필터는 **명시적 `None`(끈다)**이어야 한다 — 센티넬(`harness.UNSET`)이 「채택
    1.28」이라, 안 가르면 「필터 끔」 라벨을 달고 조용히 1.28로 도는 이중 필터가 된다
    (WAN-159가 못 박은 자리).
    """
    branch = RUNGS_BY_NAME[BRANCH_RUNG]
    assert branch.bollinger is False
    assert branch.zone_width is None
    assert branch.guard == ADOPTED_STOP_GUARD
    assert branch.reentry is False
    assert branch.is_adopted is False
    assert branch.branch is True
    # 채택값이면 배치에 `None`(손대지 않는다)을 넘긴다 — 라벨이 아니라 배선.
    assert _guard_arg(branch) is None
    # `L0`과 후보 생성 축이 같아야 한 번의 생성으로 먹일 수 있다.
    base = RUNGS_BY_NAME["L0"]
    assert (branch.bollinger, branch.zone_width) == (base.bollinger, base.zone_width)


def test_branch_generation_passes_explicit_none_to_the_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """생성 그룹 `G0`이 존폭 필터에 **명시적 `None`**을 넘긴다(센티넬이 아니라).

    `generation_payloads`가 실제로 `run_cells`에 넘기는 인자를 가로채 확인한다 — 라벨이
    아니라 호출부로 건다.
    """
    import backtest.wan366_causal_ablation as mod

    seen: dict[str, Any] = {}

    def _spy(*args: Any, **kwargs: Any) -> list[Any]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(mod, "run_cells", _spy, raising=True)
    mod.generation_payloads(
        [_REAL_SYMBOL], [_REAL_TF], "G0", start=_REAL_START, end=_REAL_END, jobs=1
    )

    assert seen["bollinger"] is False
    assert seen["max_zone_width_atr"] is None, (
        "존폭 필터에 센티넬이 넘어갔습니다 — 「끔」 라벨을 달고 1.28로 도는 이중 필터가 "
        "됩니다(WAN-159)."
    )
    assert seen["reentry"] is False
    assert seen["engine_check"] is False


def test_guard_pair_invariants_hold() -> None:
    """가드 짝은 **가드만** 다르고 같은 생성 그룹이다 — 검산 (b)의 전제."""
    _assert_guard_pairs()
    assert ("L0", "L0g") in GUARD_PAIRS
    assert ("L2", "L3") in GUARD_PAIRS


def test_guard_pair_invariant_rejects_split_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """짝이 다른 그룹에 놓이면 죽는다 — 그러면 검산이 조용히 통과한다."""
    import backtest.wan366_causal_ablation as mod

    split = {
        name: (rung.__class__(**{**rung.__dict__, "gen": "GX"}) if name == "L0g" else rung)
        for name, rung in mod.RUNGS_BY_NAME.items()
    }
    monkeypatch.setattr(mod, "RUNGS_BY_NAME", split, raising=True)
    with pytest.raises(AssertionError, match="생성 그룹이 다릅니다"):
        _assert_guard_pairs()


def test_guard_axis_check_covers_the_branch_pair() -> None:
    """`L0`·`L0g`도 검산 (b)에 걸린다 — 새 짝이 검사에서 빠지면 검산이 아니다."""
    drifted = {
        "L0": [_row("L0", PRIMARY_OOS, num_candidates=900)],
        "L0g": [_row("L0g", PRIMARY_OOS, num_candidates=880)],
    }
    with pytest.raises(AssertionError, match="검산\\(b\\)"):
        _check_guard_axis(drifted)


def test_guard_axis_check_falls_back_to_the_persisted_csv() -> None:
    """짝의 한쪽만 이번에 돌려도 **적재된 CSV의 짝 행과** 대조한다.

    한쪽만 돌렸다고 검산이 조용히 건너뛰면 그건 검산이 아니다(WAN-194/318/321 「실패가
    성공과 같은 모양」).
    """
    previous = rows_to_frame([_row("L0", PRIMARY_OOS, num_candidates=900)])
    fresh_ok = {"L0g": [_row("L0g", PRIMARY_OOS, num_candidates=900)]}
    _check_guard_axis(fresh_ok, previous)  # 예외 없음.

    fresh_drift = {"L0g": [_row("L0g", PRIMARY_OOS, num_candidates=880)]}
    with pytest.raises(AssertionError, match="검산\\(b\\)"):
        _check_guard_axis(fresh_drift, previous)


def test_branch_is_not_a_ladder_step() -> None:
    """`L0g`는 **분기**라 이웃 차가 뜻이 없다 — `L0`→`L0g`만 증분이다.

    표시 순서(`LADDER`)에서 `L0g`가 `L0`과 `L1` 사이에 있으므로, 증분을 이웃 zip으로
    되돌리면 「볼린저의 순기여」(`L0`→`L1`)가 조용히 다른 양으로 바뀐다.
    """
    assert LADDER.index("L0g") == 1  # 표시 순서상 사다리 한가운데다.
    assert "L0g" not in CHAIN
    assert ("L0", "L0g") in STEPS
    assert ("L0", "L1") in STEPS  # 볼린저의 순기여가 살아 있다.
    assert not any(lo == "L0g" for lo, _hi in STEPS)

    rows = []
    for segment in (harness.SEGMENT_IS, PRIMARY_OOS):
        rows += [
            _row("L0", segment, mean_net_r=-0.20),
            _row("L0g", segment, mean_net_r=0.02),
            _row("L1", segment, mean_net_r=-0.30),
        ]
    steps = {i.step for i in increments(rows_to_frame(rows))}
    assert steps == {"L0→L1", "L0→L0g"}


def test_branch_section_reports_interaction_when_the_guard_differs() -> None:
    """맨몸 가드와 쌓은 가드의 크기가 다르면 **상호작용한다**로 찍는다."""
    rows = []
    for segment in (harness.SEGMENT_IS, PRIMARY_OOS):
        rows += [
            _row("L0", segment, mean_net_r=-0.20, num_trades=1000),
            _row("L0g", segment, mean_net_r=-0.15, num_trades=900),  # 맨몸 +0.05R
            _row("L2", segment, mean_net_r=-0.40),
            _row("L3", segment, mean_net_r=-0.14),  # 쌓은 +0.26R
        ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert "상호작용한다" in text
    assert "단순 덧셈은 성립하지 않는다" in text
    # 가드가 쳐낸 거래 수가 함께 나온다(PM 시나리오 표를 가르는 그 한 숫자).
    assert "+100건(+10.0%)" in text


def test_branch_section_reports_no_interaction_when_the_guard_matches() -> None:
    """두 자리에서 크기가 같으면 **단순 덧셈이 성립한다**로 찍는다."""
    rows = []
    for segment in (harness.SEGMENT_IS, PRIMARY_OOS):
        rows += [
            _row("L0", segment, mean_net_r=-0.20),
            _row("L0g", segment, mean_net_r=0.06),  # 맨몸 +0.26R
            _row("L2", segment, mean_net_r=-0.40),
            _row("L3", segment, mean_net_r=-0.14),  # 쌓은 +0.26R
        ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert "상호작용하지 않고" in text


def test_branch_section_compares_against_the_best_chain_rung() -> None:
    """`L0g` vs `L3` 대조가 **앞구간에서 고르고 뒷구간에서 확인**한다."""
    rows = []
    for segment in (harness.SEGMENT_IS, PRIMARY_OOS):
        rows += [
            _row("L0", segment, mean_net_r=-0.20),
            _row("L0g", segment, mean_net_r=-0.30),
            _row("L2", segment, mean_net_r=-0.40),
            _row("L3", segment, mean_net_r=-0.14),
        ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert f"`{harness.SEGMENT_IS}`(선택)" in text
    assert f"`{PRIMARY_OOS}`(확인)" in text
    assert "나쁘다" in text


def test_branch_section_is_absent_without_the_branch_row() -> None:
    """분기 단이 없으면 §2를 아예 안 그린다 — 없는 판정을 지어내지 않는다."""
    rows = [
        _row("L0", PRIMARY_OOS, mean_net_r=-0.20),
        _row("L1", PRIMARY_OOS, mean_net_r=-0.30),
    ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert "§2 분기 단" not in text


def test_verdict_marks_branch_steps_as_not_a_ladder_rung() -> None:
    """§1 판정에 분기 단이 섞이면 **그 사실을 밝힌다**.

    가드는 `L2`→`L3`와 `L0`→`L0g` **두 자리**에서 잡히므로, 안 밝히면 「값을 더하는 단이
    2개」가 서로 다른 부품 둘로 읽힌다.
    """
    rows = []
    for segment in (harness.SEGMENT_IS, PRIMARY_OOS):
        rows += [
            _row("L0", segment, mean_net_r=-0.20),
            _row("L0g", segment, mean_net_r=0.06),
            _row("L2", segment, mean_net_r=-0.40),
            _row("L3", segment, mean_net_r=-0.14),
        ]
    text = build_summary(rows_to_frame(rows), pd.DataFrame(), pd.DataFrame())
    assert "값을 더하는 단이 2개" in text
    assert "서로 다른 부품으로 세지 말 것" in text
