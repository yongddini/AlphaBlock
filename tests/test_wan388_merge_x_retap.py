"""WAN-388 — 존 병합 × 재탭 차단 축의 회귀 테스트.

이 파일이 지키는 것은 **라벨이 아니라 동작**이다(WAN-91/95/112/123/159가 반복해 경계한
자리): 축을 안 주면 채택 북이 비트 단위로 재현되고, 주면 **후보 집합이 실제로 달라진다**.
"""

from __future__ import annotations

import pytest

from backtest import harness
from backtest import wan388_merge_retap_census as census
from backtest import wan388_merge_x_retap as grid
from backtest.leverage_book import PlacedSetup
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload, _Task
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.zone_limit_backtest import _Candidate
from strategy.confluence import entry_candidate_signals
from strategy.models import (
    OrderBlock,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
)

# --------------------------------------------------------------------------- #
# §0 엔진 — 안 주면 비트 동일
# --------------------------------------------------------------------------- #


def test_task_defaults_are_the_adopted_rules() -> None:
    """새 두 축의 기본값이 채택 규칙이라야 「인자 없는 실행 = 채택 북」이 유지된다."""
    task = _Task(symbol="BTC/USDT:USDT", timeframe="1h", start_ms=0, end_ms=1)
    assert task.combine_obs is census.ADOPTED_COMBINE_OBS
    assert task.retap_mode is None  # `None` = build_params가 손대지 않음 = every_tap


def test_default_axes_produce_the_adopted_objects() -> None:
    """기본값이 **같은 객체**를 내야 비트 동일이다(같은 이름이 아니라 같은 값)."""
    assert OrderBlockParams(combine_obs=census.ADOPTED_COMBINE_OBS) == OrderBlockParams()
    assert harness.build_params(retap_mode=None) == harness.build_params()
    assert harness.build_params().retap_mode == census.ADOPTED_RETAP_MODE


def test_retap_axis_changes_the_params_object() -> None:
    assert harness.build_params(retap_mode="once").retap_mode == "once"
    with pytest.raises(ValueError):
        harness.build_params(retap_mode="every_tax")  # 오타는 조용히 통과하면 안 된다


def _fake_result() -> OrderBlockResult:
    """첫 탭 1건 + 재탭 2건짜리 최소 결과."""
    block = OrderBlock(
        direction="bull",
        top=110.0,
        bottom=100.0,
        start_time=0,
        confirmed_time=1,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    first = OrderBlockSignal(
        direction="bull", trigger_time=10, price=105.0, order_block=block, tap_index=0
    )
    retaps = [
        first,
        first.model_copy(update={"trigger_time": 20, "tap_index": 1}),
        first.model_copy(update={"trigger_time": 30, "tap_index": 2}),
    ]
    return OrderBlockResult(order_blocks=[block], signals=[first], retap_signals=retaps)


def test_once_consumes_first_taps_and_every_tap_consumes_all() -> None:
    """🚨 축이 걸리는 자리는 「어느 시그널 목록을 소비하나」다 — 그것을 동작으로 고정한다."""
    result = _fake_result()
    times: list[int] = []
    closes: list[float] = []
    once = entry_candidate_signals(
        result, harness.build_params(retap_mode="once"), times, closes, {}
    )
    every = entry_candidate_signals(
        result, harness.build_params(retap_mode="every_tap"), times, closes, {}
    )
    assert [s.tap_index for s in once] == [0]
    assert [s.tap_index for s in every] == [0, 1, 2]


def test_placed_setup_labels_default_to_the_candidate_defaults() -> None:
    """새 라벨 둘이 기본값이라 옛 배치 기록이 비트 단위로 같다."""
    placed = PlacedSetup(
        cell=("BTC/USDT:USDT", "1h"), equity=1.0, risk_amount=1.0, realized_pnl=0.0
    )
    assert placed.tap_index == 0
    assert placed.zone_key is None


# --------------------------------------------------------------------------- #
# §1 인구조사
# --------------------------------------------------------------------------- #


def test_gate_line_is_pinned() -> None:
    """관문을 **상수 + 테스트**로 못 박는다 — 결과를 보고 선을 옮기지 못하게."""
    assert census.BITE_GATE == 0.05
    assert grid.BOOK_RETAP_GATE == 0.05
    assert grid.NOISE_R == 0.005


def test_gate_refuses_to_judge_an_empty_census() -> None:
    passed, note = census.gate_verdict([])
    assert passed is False
    assert "판정하지 않는다" in note


def _census_row(**overrides: object) -> census.CensusRow:
    base: dict[str, object] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "num_bars": 100,
        "split_zones": 10,
        "split_first_taps": 10,
        "split_all_taps": 20,
        "split_retaps": 10,
        "split_retap_share": 0.5,
        "merged_zones": 9,
        "merged_first_taps": 10,
        "merged_all_taps": 100,
        "merged_retaps": 8,
        "merged_retap_share": 0.44,
        "bite_rate": 0.2,
        "bite_taps": 20,
        "cluster_members_p50": 1.0,
        "cluster_members_p90": 2.0,
        "cluster_members_max": 3,
        "width_mult_p50": 1.7,
        "width_mult_p90": 4.5,
        "all_tap_change": -0.08,
        "first_tap_change": 0.0,
        "retap_change": -0.22,
    }
    base.update(overrides)
    return census.CensusRow.model_validate(base)


def test_gate_is_tap_weighted_not_a_mean_of_cell_ratios() -> None:
    """🚨 칸마다의 비율을 단순 평균하면 얇은 칸이 과대 대표된다."""
    thick = _census_row(bite_taps=1, merged_all_taps=1000)  # 0.1%
    thin = _census_row(bite_taps=1, merged_all_taps=1)  # 100%
    passed, _note = census.gate_verdict([thick, thin])
    assert passed is False  # 가중하면 2/1001 = 0.2% < 5%


def test_gate_passes_when_merge_actually_bites() -> None:
    passed, note = census.gate_verdict([_census_row()])
    assert passed is True
    assert "20.00%" in note


def test_census_counts_bites_and_width_multiple() -> None:
    """무는 탭만 폭 배수에 들어가고, 배수의 분모는 구성 존 높이의 **중앙값**이다."""
    thin = OrderBlock(
        direction="bull",
        top=101.0,
        bottom=100.0,
        start_time=0,
        confirmed_time=1,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    other = thin.model_copy(update={"top": 103.0, "bottom": 102.0})
    cluster = thin.model_copy(
        update={"top": 103.0, "bottom": 100.0, "combined": True, "num_component_obs": 2}
    )
    sig = OrderBlockSignal(
        direction="bull",
        trigger_time=10,
        price=101.0,
        order_block=cluster,
        zone_key=frozenset({0, 1}),
    )
    heights = census._member_heights(sig, [thin, other])
    assert heights == [1.0, 1.0]
    # 클러스터 높이 3.0 ÷ 구성 존 중앙값 1.0 = 3.00배
    assert (cluster.top - cluster.bottom) / 1.0 == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# §2 격자 — 판정 줄
# --------------------------------------------------------------------------- #


def _grid_row(arm: str, *, net: float, cost: float = 0.2, gross: float = 0.1) -> grid.GridRow:
    spec = grid.ARMS_BY_NAME[arm]
    return grid.GridRow(
        arm=spec.name,
        label=spec.label,
        combine_obs=spec.combine_obs,
        retap_mode=spec.retap_mode,
        segment=PRIMARY_OOS,
        adopted_arm=spec.is_adopted,
        num_cells=48,
        num_symbols=12,
        num_trades=1000,
        win_rate=0.5,
        mean_net_r=net,
        gross_r=gross,
        slippage_r=0.05,
        entry_fee_r=0.05,
        take_profit_fee_r=0.03,
        stop_fee_r=0.05,
        other_fee_r=0.0,
        funding_r=0.02,
        cost_r=cost,
        identity_max_abs=0.0,
        stop_width_p50=0.005,
        stop_width_p90=0.01,
        entry_in_zone_p50=0.3,
        retap_trades=100 if spec.retap_mode == "every_tap" else 0,
        retap_trade_share=0.1 if spec.retap_mode == "every_tap" else 0.0,
        reentry_trades=50,
        zone_retap_and_reentry=7,
        total_return_flat=1.0,
        max_drawdown=0.2,
        return_over_mdd=5.0,
        peak_concurrency=14,
        max_concurrent_risk=0.11,
        max_effective_concurrent_risk=0.17,
        liquidation_events=0,
        symbols_below_gate=0,
        min_symbol_trades=30,
    )


def test_verdict_decomposes_the_two_by_two() -> None:
    """상호작용 = 2×2 잔차 — 대각선 둘만으로는 어느 축의 몫인지 못 가른다(WAN-131 함정)."""
    rows = [
        _grid_row("split_every", net=0.10),
        _grid_row("merge_every", net=0.13),
        _grid_row("split_once", net=0.12),
        _grid_row("merge_once", net=0.20),
    ]
    v = grid.verdict_for(rows, PRIMARY_OOS)
    assert v.merge_effect_every == pytest.approx(0.03)
    assert v.merge_effect_once == pytest.approx(0.08)
    assert v.retap_effect_split == pytest.approx(0.02)
    assert v.retap_effect_merge == pytest.approx(0.07)
    assert v.interaction == pytest.approx(0.05)
    assert v.headline == pytest.approx(0.10)


def test_headline_below_the_noise_line_is_not_an_adoption() -> None:
    rows = [
        _grid_row("split_every", net=0.10),
        _grid_row("merge_once", net=0.1002),
    ]
    v = grid.verdict_for(rows, PRIMARY_OOS)
    assert v.passes_noise is False
    text = grid.build_summary_markdown(rows, [], [])
    assert "채택 권고 없음" in text


def test_mechanism_needs_cost_saving_to_beat_the_gross_drop() -> None:
    """🚨 net R이 좋아졌어도 「비용 절감 > gross 감소」가 아니면 우연으로 적는다."""
    rows = [
        _grid_row("split_every", net=0.10, cost=0.20, gross=0.30),
        # 비용은 0.05 줄었는데 gross는 0.04만 줄었다 → 메커니즘 성립
        _grid_row("merge_once", net=0.20, cost=0.15, gross=0.26),
    ]
    assert grid.verdict_for(rows, PRIMARY_OOS).mechanism_holds is True

    rows_bad = [
        _grid_row("split_every", net=0.10, cost=0.20, gross=0.30),
        # 비용은 0.01만 줄었는데 gross가 0.10 줄었다 → 미성립
        _grid_row("merge_once", net=0.20, cost=0.19, gross=0.20),
    ]
    v = grid.verdict_for(rows_bad, PRIMARY_OOS)
    assert v.mechanism_holds is False
    assert "메커니즘 미성립" in grid.build_summary_markdown(rows_bad, [], [])


def test_summary_refuses_to_invent_a_verdict_from_an_empty_grid() -> None:
    text = grid.build_summary_markdown([], [], [])
    assert "판정하지 않는다" in text


def test_summary_warns_when_the_two_by_two_is_incomplete() -> None:
    """대각선만 있으면 그 사실을 **표에 찍는다** — 조용히 판정하면 WAN-131 함정이다."""
    text = grid.build_summary_markdown(
        [_grid_row("split_every", net=0.1), _grid_row("merge_once", net=0.2)], [], []
    )
    assert "2×2가 아직 안 찼다" in text


# --------------------------------------------------------------------------- #
# §2 검산
# --------------------------------------------------------------------------- #


def test_retap_axis_check_catches_a_label_only_arm() -> None:
    """`retap_mode="once"`인데 재탭 거래가 남아 있으면 축이 안 걸린 것이다."""
    clean = _grid_row("merge_once", net=0.2)
    dirty = clean.model_copy(update={"retap_trades": 3})
    assert [c.abs_diff for c in grid.check_retap_axis([clean])] == [0.0]
    assert [c.abs_diff for c in grid.check_retap_axis([dirty])] == [3.0]
    # 매탭 팔은 재탭이 있는 게 정상이라 이 검산의 대상이 아니다.
    assert grid.check_retap_axis([_grid_row("split_every", net=0.1)]) == []


def test_arm_invariant_check_compares_coordinates_not_pnl() -> None:
    base = _grid_row("split_every", net=0.1)
    other = _grid_row("merge_once", net=0.9)
    rows = grid.check_arm_invariants([base, other])
    assert {r.metric for r in rows} == {"num_cells", "num_symbols"}
    assert all(r.abs_diff == 0.0 for r in rows)

    shifted = other.model_copy(update={"num_cells": 47})
    assert any(r.abs_diff == 1.0 for r in grid.check_arm_invariants([base, shifted]))


# --------------------------------------------------------------------------- #
# 배선 — 축·회계가 실제로 넘어가나
# --------------------------------------------------------------------------- #


def test_build_payloads_forwards_both_axes_and_the_adopted_accounting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def fake_run_cells(symbols, timeframes, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return []

    monkeypatch.setattr(grid, "run_cells", fake_run_cells)
    grid.build_payloads(
        ["BTC/USDT:USDT"],
        ["1h"],
        arm=grid.ARMS_BY_NAME["merge_once"],
        start="2020-09-15",
        end="2026-07-22",
        jobs=1,
    )
    assert seen["combine_obs"] is True
    assert seen["retap_mode"] == "once"
    # 🚨 잊으면 조용히 옛 비용 회계로 돈다(WAN-370/373).
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    # 채택 규칙(WAN-273/305) — 네 팔 전부에서 재진입은 켠 채다.
    assert seen["reentry"] is True


def test_place_uses_the_adopted_book_accounting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def fake_iter(payloads, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return []

    monkeypatch.setattr(grid, "iter_book_segments", fake_iter)
    grid.place([], start_ms=0, end_ms=1, segments=["full"])
    assert seen["include_reentry"] is True
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert seen["compound_sizing"] is False  # 판정 판은 복리를 끈다(WAN-346)
    assert seen["min_stop_distance_fraction"] == ADOPTED_STOP_GUARD


def test_zone_overlap_counts_zones_not_trades() -> None:
    """§1-4 — 같은 존에서 재탭 거래와 재진입 거래가 **둘 다** 난 존의 수."""

    def placed(*, tap_index: int, is_reentry: bool, zone: int) -> PlacedSetup:
        return PlacedSetup(
            cell=("BTC/USDT:USDT", "1h"),
            equity=1.0,
            risk_amount=1.0,
            realized_pnl=0.0,
            is_reentry=is_reentry,
            tap_index=tap_index,
            zone_key=frozenset({zone}),
        )

    pairs = [
        (None, placed(tap_index=1, is_reentry=False, zone=1)),
        (None, placed(tap_index=0, is_reentry=True, zone=1)),
        (None, placed(tap_index=2, is_reentry=False, zone=2)),  # 재진입 없음
        (None, placed(tap_index=0, is_reentry=True, zone=3)),  # 재탭 없음
    ]
    assert grid._zone_overlap(pairs) == 1  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 존 내 깊이 — 방향과 구간 키 (둘 다 실제로 났던 버그다)
# --------------------------------------------------------------------------- #


def _candidate(*, entry: float, ob: OrderBlock, trigger: int, long: bool = True) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG if long else PositionSide.SHORT,
        entry_time=trigger,
        entry_price=entry,
        exit_time=trigger + 1,
        exit_price=entry,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=ob.bottom if long else ob.top,
        order_block=ob,
        trigger_time=trigger,
    )


def _payload(candidates: dict[str, tuple[_Candidate, ...]], *, boundary: int) -> CellPayload:
    return CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        boundary_ms=boundary,
        candidates=candidates,
        funding={},
        rows=(),
    )


def _zone(*, top: float, bottom: float, long: bool = True) -> OrderBlock:
    return OrderBlock(
        direction="bull" if long else "bear",
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=1,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )


def test_entry_depth_is_measured_from_the_proximal_edge() -> None:
    """🚨 롱의 근단은 존 **상단**이다(먼저 닿는다) — 뒤집으면 이 열이 정반대를 말한다."""
    zone = _zone(top=110.0, bottom=100.0)
    at_proximal = _candidate(entry=110.0, ob=zone, trigger=0)
    deep = _candidate(entry=102.0, ob=zone, trigger=0)
    payload = _payload({"full": (at_proximal,)}, boundary=0)
    assert grid.entry_in_zone([payload], "full") == pytest.approx(0.0)
    assert grid.entry_in_zone([_payload({"full": (deep,)}, boundary=0)], "full") == pytest.approx(
        0.8
    )
    # 숏은 거울 — 근단이 하단이다.
    short_zone = _zone(top=110.0, bottom=100.0, long=False)
    short = _candidate(entry=100.0, ob=short_zone, trigger=0, long=False)
    assert grid.entry_in_zone([_payload({"full": (short,)}, boundary=0)], "full") == pytest.approx(
        0.0
    )


def test_entry_depth_reads_warm_oos_from_the_full_candidates() -> None:
    """🚨 `oos_warm`은 payload에 **없는 키**다 — 그냥 찾으면 주 수치 구간이 조용히 0이 된다."""
    zone = _zone(top=110.0, bottom=100.0)
    before = _candidate(entry=110.0, ob=zone, trigger=10)  # 깊이 0.0 · 경계 이전
    after = _candidate(entry=102.0, ob=zone, trigger=100)  # 깊이 0.8 · 경계 이후
    payload = _payload({"full": (before, after)}, boundary=50)
    assert grid.entry_in_zone([payload], "full") == pytest.approx(0.4)  # 두 값의 중앙
    assert grid.entry_in_zone([payload], PRIMARY_OOS) == pytest.approx(0.8)


# --------------------------------------------------------------------------- #
# 지갑 층 열 — 뜻을 잃으면 비율을 내지 않는다 (WAN-115 관행 · wan386과 같은 술어)
# --------------------------------------------------------------------------- #


def test_wallet_columns_are_undefined_when_the_wallet_goes_negative() -> None:
    """🚨 실측 그대로의 값 — 복리를 끄면 이 좌표에서 자본이 0을 뚫는다."""
    row = _grid_row("split_every", net=-0.1194).model_copy(
        update={"max_drawdown": 9.956213, "total_return_flat": -11.061572}
    )
    assert grid.wallet_defined(row) is False
    text = grid.build_summary_markdown([row], [], [])
    assert "정의 상실" in text
    # 995.62%를 퍼센트처럼 찍으면 안 된다 — 그게 이 가드가 막는 실패다.
    assert "995.62%" not in text
    assert "이 격자는 위험의 모양을 재지 않았다" in text


def test_wallet_columns_survive_when_the_wallet_stays_solvent() -> None:
    row = _grid_row("split_every", net=0.05).model_copy(
        update={"max_drawdown": 0.1543, "total_return_flat": 0.82, "liquidation_events": 0}
    )
    assert grid.wallet_defined(row) is True
    text = grid.build_summary_markdown([row], [], [])
    assert "15.43%" in text
    assert "정의 상실" not in text


def test_per_trade_columns_are_never_suppressed() -> None:
    """⚠️ 판정 자는 잔고와 무관하다 — 지갑이 무너져도 net R은 그대로 찍혀야 한다."""
    row = _grid_row("split_every", net=-0.1194).model_copy(
        update={"max_drawdown": 9.956213, "total_return_flat": -11.061572}
    )
    text = grid.build_summary_markdown([row], [], [])
    assert "-0.1194" in text
