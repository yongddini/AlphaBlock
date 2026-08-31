"""WAN-394 §1 — 재탭 × 재진입 × 익절 배수 격자의 회귀 테스트.

이 파일이 지키는 것은 **라벨이 아니라 동작**이다(WAN-91/95/112/123/159가 반복해 경계한
자리). 이 모듈의 새 배선은 **하나**다 — 팔 후보(base+재진입 합본)에서 `is_reentry`를 빼서
재진입 끈 팔을 만드는 것. 그 필터가 안 걸리면 「재진입 끔」이 이름만 그렇고 §2 판정 줄
전체가 무효이므로, 그것을 **실제 후보 집합**으로 건다.
"""

from __future__ import annotations

from typing import Any

import pytest

from backtest import harness
from backtest import wan394_retap_reentry_tp as tri
from backtest.confirmation_arm import ARM_BASE
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload, arm_key
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan381_exit_scales import ADOPTED_MULTIPLE, MULTIPLES
from backtest.wan388_merge_retap_census import ADOPTED_COMBINE_OBS, ADOPTED_RETAP_MODE
from backtest.wan388_merge_x_retap import NOISE_R
from backtest.wan389_retap_attribution import RETAP_MODES
from backtest.zone_limit_backtest import _Candidate

# --------------------------------------------------------------------------- #
# 격자 — 세 축이 실제로 만나는가
# --------------------------------------------------------------------------- #


def test_the_grid_is_a_two_by_two_by_four() -> None:
    """🚨 **세 축이 만나는 칸이 없으면 이 이슈가 존재할 이유가 없다.**"""
    points = [p for mode in RETAP_MODES for p in tri.points_for(mode)]
    assert len(points) == 2 * 2 * 4
    assert {p.retap_mode for p in points} == {"every_tap", "once"}
    assert {p.reentry for p in points} == {True, False}
    assert {p.multiple for p in points} == set(MULTIPLES)


def test_exactly_one_point_is_the_adopted_book() -> None:
    points = [p for mode in RETAP_MODES for p in tri.points_for(mode)]
    adopted = [p for p in points if p.is_adopted]
    assert len(adopted) == 1
    assert adopted[0] == tri.ADOPTED_POINT
    assert (adopted[0].retap_mode, adopted[0].reentry, adopted[0].multiple) == (
        ADOPTED_RETAP_MODE,
        True,
        ADOPTED_MULTIPLE,
    )


def test_compute_unit_is_the_retap_mode_not_the_point() -> None:
    """📌 재탭 모드 하나가 점 **여덟**을 먹인다 — 컴퓨트를 4분의 1로 만드는 성질이다."""
    for mode in RETAP_MODES:
        points = tri.points_for(mode)
        assert len(points) == 8
        assert {p.reentry for p in points} == {True, False}
        assert {p.multiple for p in points} == set(MULTIPLES)


def test_rulers_are_inherited_not_re_chosen() -> None:
    """배수 점·노이즈선을 이 모듈이 따로 고르면 WAN-381/389 표와 **다른 자로** 읽게 된다."""
    assert MULTIPLES == (0.6, 0.8, 1.0, 1.5)
    assert tri.LOW_MULTIPLE == 0.6
    assert NOISE_R == 0.005
    assert tri._fmt(0.0049).endswith("(≈0)")
    assert not tri._fmt(0.0051).endswith("(≈0)")


# --------------------------------------------------------------------------- #
# 배선 — 축·회계가 실제로 넘어가나
# --------------------------------------------------------------------------- #


def test_cell_kwargs_keep_reentry_on_and_name_the_take_profit_liquidity() -> None:
    """🚨 후보에는 재진입을 **항상** 실어 두고 배치에서 고른다 — 검산 (c)·(f)의 전제다."""
    kwargs = tri._cell_kwargs()
    assert kwargs["reentry"] is True
    assert kwargs["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


def test_build_payloads_forwards_all_three_axes_and_the_cache(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def fake_run_cells(symbols: Any, timeframes: Any, **kwargs: Any) -> list[CellPayload]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(tri, "run_cells", fake_run_cells)
    sentinel = object()
    tri.build_payloads(
        ["BTC/USDT:USDT"],
        ["4h"],
        retap_mode="once",
        start="2020-01-01",
        end="2021-01-01",
        jobs=1,
        cache=sentinel,  # type: ignore[arg-type]
    )
    assert seen["retap_mode"] == "once"
    assert seen["combine_obs"] is ADOPTED_COMBINE_OBS
    assert seen["confirmation_arms"] == (ARM_BASE,)
    assert seen["confirmation_multiples"] == MULTIPLES  # 배수 넷이 **한 순회**에 나온다
    assert seen["payload_cache"] is sentinel
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


def test_place_uses_the_adopted_guard_and_never_double_counts_reentry(
    monkeypatch: Any,
) -> None:
    """🚨 팔 후보가 base+재진입 합본이라 배치에서 켜면 **이중 계상**이다(WAN-386 관행)."""
    seen: dict[str, Any] = {}

    def fake_iter(payloads: Any, **kwargs: Any) -> list[Any]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(tri, "iter_book_segments", fake_iter)
    tri.place([], start_ms=0, end_ms=1, segments=["full"])
    assert seen["include_reentry"] is False
    assert seen["min_stop_distance_fraction"] == ADOPTED_STOP_GUARD
    assert seen["compound_sizing"] is False  # 복리 총수익은 판정 자가 아니다(WAN-346)
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


# --------------------------------------------------------------------------- #
# 🚨 새 배선 — `is_reentry` 필터가 실제로 후보를 뺀다
# --------------------------------------------------------------------------- #


def _cand(price: float, *, is_reentry: bool, target: float | None = None) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=int(price * 1000),
        entry_price=price,
        exit_time=int(price * 1000) + 60_000,
        exit_price=price - 10.0,
        reason=ExitReason.STOP_LOSS,
        stop_price=price - 10.0,
        take_profit_price=target,
        is_reentry=is_reentry,
    )


def _payload_with_arms() -> CellPayload:
    """배수마다 base 2 · 재진입 1을 담은 합성 칸(WAN-386 팔 후보의 모양 그대로).

    🚨 **진입은 배수 사이에서 같고 목표만 다르다** — 실제 `derive_arm_candidates`가 그렇고
    (익절은 청산만 바꾼다), 픽스처가 그 성질을 깨면 검산 (e)를 시험할 수 없다.
    """
    arms: dict[str, dict[str, tuple[_Candidate, ...]]] = {
        arm_key(ARM_BASE, m): {
            "full": (
                _cand(100.0, is_reentry=False, target=100.0 + 10.0 * m),
                _cand(200.0, is_reentry=False, target=200.0 + 10.0 * m),
                _cand(300.0, is_reentry=True, target=300.0 + 10.0 * m),
            )
        }
        for m in MULTIPLES
    }
    return CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="4h",
        boundary_ms=0,
        candidates={"full": (_cand(100.0, is_reentry=False),)},
        funding={"full": ()},
        rows=(),
        reentry_candidates={"full": (_cand(300.0, is_reentry=True),)},
        arm_candidates=arms,
    )


def test_reentry_off_actually_drops_the_reentry_candidates() -> None:
    """🚨 **이 모듈의 유일한 새 배선**이다 — 안 걸리면 두 팔이 같은 판을 돈다."""
    payloads = [_payload_with_arms()]
    on = tri.scoped(payloads, multiple=ADOPTED_MULTIPLE, reentry=True)[0]
    off = tri.scoped(payloads, multiple=ADOPTED_MULTIPLE, reentry=False)[0]
    assert len(on.candidates["full"]) == 3
    assert len(off.candidates["full"]) == 2
    assert all(not c.is_reentry for c in off.candidates["full"])
    # base 후보는 **글자 그대로 같다** — 재진입만 빠진다.
    assert [c.entry_price for c in off.candidates["full"]] == [
        c.entry_price for c in on.candidates["full"] if not c.is_reentry
    ]


def test_scoped_clears_the_engine_reentry_list_so_placement_cannot_double_count() -> None:
    """팔 후보가 합본이라 `reentry_candidates`가 남아 있으면 배치에서 또 들어간다."""
    for reentry in (True, False):
        view = tri.scoped([_payload_with_arms()], multiple=ADOPTED_MULTIPLE, reentry=reentry)
        assert view[0].reentry_candidates == {}


def test_scoped_picks_the_asked_multiple() -> None:
    """배수는 **목표**로 구분한다 — 진입은 배수 사이에서 같아야 하기 때문이다(검산 (e))."""
    for multiple in MULTIPLES:
        view = tri.scoped([_payload_with_arms()], multiple=multiple, reentry=True)
        assert [c.take_profit_price for c in view[0].candidates["full"]] == [
            100.0 + 10.0 * multiple,
            200.0 + 10.0 * multiple,
            300.0 + 10.0 * multiple,
        ]


def test_a_missing_arm_is_loud_not_silently_empty() -> None:
    """조용히 빈 목록을 돌려주면 「그 배수는 거래가 0건」이라는 표가 만들어진다(WAN-95 부류)."""
    payload = _payload_with_arms()
    stripped = CellPayload(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        boundary_ms=payload.boundary_ms,
        candidates=payload.candidates,
        funding=payload.funding,
        rows=payload.rows,
        arm_candidates={},
    )
    with pytest.raises(KeyError, match="팔 후보가 없습니다"):
        tri.scoped([stripped], multiple=ADOPTED_MULTIPLE, reentry=True)


def test_entry_sets_are_identical_across_multiples() -> None:
    """검산 (e)를 동작으로 — 익절은 청산만 바꾼다(WAN-137/143 훅). 진입이 갈리면 「배수의
    값어치」가 「다른 셋업을 골랐다」와 섞인다."""
    checks = tri.check_entry_sets([_payload_with_arms()])
    assert checks  # 비면 검산이 조용히 통과한 것이다
    assert all(c.abs_diff == 0.0 for c in checks)


def test_entry_set_check_catches_a_broken_multiple() -> None:
    """돌연변이 확인 — 한 배수의 진입가를 흔들면 검산 (e)가 **실제로 잡는다**."""
    payload = _payload_with_arms()
    broken = dict(payload.arm_candidates)
    broken[arm_key(ARM_BASE, 0.6)] = {"full": (_cand(999.0, is_reentry=False, target=1000.0),)}
    mutated = CellPayload(
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        boundary_ms=payload.boundary_ms,
        candidates=payload.candidates,
        funding=payload.funding,
        rows=payload.rows,
        arm_candidates=broken,
    )
    assert any(c.abs_diff > 0 for c in tri.check_entry_sets([mutated]))


# --------------------------------------------------------------------------- #
# 판정 줄 — 순진한 덧셈과 실측의 차
# --------------------------------------------------------------------------- #


def _row(
    point: tri.Point, *, net: float, stderr: float = 0.001, trades: int = 1000
) -> tri.TriaxialRow:
    return tri.TriaxialRow(
        **tri._point_fields(point),
        segment=PRIMARY_OOS,
        num_cells=48,
        num_symbols=12,
        num_trades=trades,
        win_rate=0.5,
        mean_net_r=net,
        gross_r=0.1,
        slippage_r=0.05,
        entry_fee_r=0.05,
        take_profit_fee_r=0.03,
        stop_fee_r=0.05,
        other_fee_r=0.0,
        funding_r=0.02,
        cost_r=0.2,
        identity_max_abs=0.0,
        stop_width_p50=0.005,
        stop_width_p90=0.01,
        entry_in_zone_p50=0.3,
        retap_trades=100 if point.retap_mode == "every_tap" else 0,
        retap_trade_share=0.1 if point.retap_mode == "every_tap" else 0.0,
        reentry_trades=50 if point.reentry else 0,
        zone_retap_and_reentry=7,
        total_return_flat=-0.8,
        max_drawdown=0.9,
        return_over_mdd=-0.9,
        peak_concurrency=14,
        max_concurrent_risk=0.11,
        max_effective_concurrent_risk=0.17,
        liquidation_events=0,
        symbols_below_gate=0,
        min_symbol_trades=30,
        net_r_stderr=stderr,
        mean_gross_r_after_slippage=0.05,
        same_step_tp_trades=400,
        same_step_tp_trade_share=0.4,
        same_step_tp_net_r_share=0.48,
    )


def _grid(
    adopted: float, axes: float, multiple: float, both: float, **kw: Any
) -> list[tri.TriaxialRow]:
    return [
        _row(tri.ADOPTED_POINT, net=adopted),
        _row(tri.Point("once", False, ADOPTED_MULTIPLE), net=axes),
        _row(tri.Point(ADOPTED_RETAP_MODE, True, tri.LOW_MULTIPLE), net=multiple),
        _row(tri.Point("once", False, tri.LOW_MULTIPLE), net=both, **kw),
    ]


def test_verdict_contrasts_the_naive_sum_with_the_measurement() -> None:
    """이슈가 인용한 실측 그대로 — 순진한 덧셈은 **+0.047R**을 약속한다."""
    rows = _grid(adopted=-0.1194, axes=-0.0658, multiple=-0.0064, both=-0.02)
    v = tri.verdict_for(rows, PRIMARY_OOS)
    assert v.delta_axes == pytest.approx(0.0536, abs=1e-4)
    assert v.delta_multiple == pytest.approx(0.1130, abs=1e-4)
    assert v.naive == pytest.approx(0.0472, abs=1e-4)
    assert v.measured == pytest.approx(-0.02)
    assert v.interaction == pytest.approx(-0.0672, abs=1e-4)
    assert v.label == "덧셈이 과대평가한다"


def test_an_additive_grid_is_labelled_so() -> None:
    rows = _grid(adopted=-0.12, axes=-0.07, multiple=-0.02, both=0.0299)
    v = tri.verdict_for(rows, PRIMARY_OOS)
    assert v.naive == pytest.approx(0.03, abs=1e-4)
    assert v.label == "덧셈이 성립한다"


def test_a_positive_measurement_still_needs_its_sign_decided() -> None:
    """🚨 WAN-381 최선이 −0.0023 ± 0.0057이라 **부호를 못 정했다** — 양수도 같은 검사다."""
    loud = _grid(adopted=-0.12, axes=-0.07, multiple=-0.02, both=0.05, stderr=0.001)
    assert tri.verdict_for(loud, PRIMARY_OOS).sign_is_decided is True
    assert tri.verdict_for(loud, PRIMARY_OOS).crossed_zero is True

    noisy = _grid(adopted=-0.12, axes=-0.07, multiple=-0.02, both=0.005, stderr=0.006)
    v = tri.verdict_for(noisy, PRIMARY_OOS)
    assert v.measured is not None and v.measured > 0
    assert v.sign_is_decided is False
    assert v.crossed_zero is False  # 양수인데도 「넘었다」고 말하지 않는다


def test_summary_refuses_to_call_a_noisy_positive_a_crossing() -> None:
    text = tri.build_summary_markdown(
        _grid(adopted=-0.12, axes=-0.07, multiple=-0.02, both=0.005, stderr=0.006), [], []
    )
    assert "부호가 정해지지 않았다" in text
    assert "0을 넘었다" not in text


def test_summary_names_the_wallet_columns_next_to_the_per_trade_ruler() -> None:
    """🚨 WAN-381에서 −0.0023R짜리 칸도 계좌는 −82% · 청산 6,807건이었다."""
    text = tri.build_summary_markdown(_grid(-0.12, -0.07, -0.02, -0.02), [], [])
    assert "거래당 0 ≠ 계좌 본전" in text
    assert "청산" in text
    assert "같은 분 익절" in text
    assert "gross 자가 둘이다" in text  # WAN-393 §2 함정


def test_summary_says_nothing_when_there_are_no_rows() -> None:
    text = tri.build_summary_markdown([], [], [])
    assert "판정하지 않는다" in text


def test_summary_flags_an_unfilled_grid() -> None:
    rows = [_row(tri.ADOPTED_POINT, net=-0.12)]
    assert "격자가 아직 안 찼다" in tri.build_summary_markdown(rows, [], [])


# --------------------------------------------------------------------------- #
# 검산 · 자
# --------------------------------------------------------------------------- #


def test_reentry_and_retap_axis_checks_are_zero_only_when_the_axis_bit() -> None:
    """(c)·(d)는 **동작**으로 건다 — 라벨만 붙고 거래가 남아 있으면 잡아야 한다."""
    good = _grid(-0.12, -0.07, -0.02, -0.02)
    assert all(c.abs_diff == 0.0 for c in tri.check_reentry_axis(good))
    assert all(c.abs_diff == 0.0 for c in tri.check_retap_axis(good))

    broken = _row(tri.Point("once", False, ADOPTED_MULTIPLE), net=-0.07)
    leaky = broken.model_copy(update={"reentry_trades": 12, "retap_trades": 34})
    assert any(c.abs_diff > 0 for c in tri.check_reentry_axis([leaky]))
    assert any(c.abs_diff > 0 for c in tri.check_retap_axis([leaky]))


def test_cross_module_checks_use_the_matching_gross_ruler() -> None:
    """🚨 `gross_r`(슬립 전)과 `mean_gross_r`(슬립 후)를 섞으면 0.1186R이 통째로 차로 나온다."""
    assert "gross_r" in tri._WAN389_METRICS
    assert ("mean_gross_r_after_slippage", "mean_gross_r") in tri._WAN381_METRICS
    assert all(mine != "gross_r" for mine, _ in tri._WAN381_METRICS)


def test_cross_module_checks_refuse_a_different_coordinate(tmp_path: Any) -> None:
    """좌표가 다르면 **아예 안 낸다** — 다른 두 표를 빼면 배선 오류처럼 읽힌다."""
    import pandas as pd

    path = tmp_path / "wan389.csv"
    pd.DataFrame(
        [
            {
                "arm": "split_every",
                "segment": PRIMARY_OOS,
                "num_cells": 4,  # ← 다른 좌표
                "num_symbols": 1,
                **{m: 0.0 for m in tri._WAN389_METRICS},
            }
        ]
    ).to_csv(path, index=False)
    rows = _grid(-0.12, -0.07, -0.02, -0.02)
    assert tri.check_against_wan389(rows, path=path) == []


def test_checksum_grade_separates_a_match_from_float_noise_from_a_real_break() -> None:
    assert tri.checksum_grade(0.0) == "비트 일치"
    assert "잡음" in tri.checksum_grade(8.5e-17)
    assert "불일치" in tri.checksum_grade(1.0)
    assert tri.CHECKSUM_NOISE == 1e-9
    assert "불일치" in tri.checksum_grade(tri.CHECKSUM_NOISE)


def test_same_step_share_is_withheld_when_the_denominator_is_meaningless(
    monkeypatch: Any,
) -> None:
    """🚨 **분모가 음수면 비중이 부호가 뒤집힌 채 나온다** — 파일럿에서 `-384%`가 찍혔다.

    이 좌표는 거래당 기대값이 음수라 순손익 합이 대개 음수다. 그러면 「48%」 같은 수가
    무의미해지므로 **양수이고 충분히 클 때만** 낸다(WAN-115 함정 · WAN-336 관행). 대신
    표가 쓰는 **거래 수 몫**은 언제나 정의된다.
    """

    class _Fake:
        segment = PRIMARY_OOS

        def trades_with_placements(self) -> list[Any]:
            return []

    # 빈 구간 — 분모가 아예 없다.
    extra = tri._extra_kwargs(_Fake())  # type: ignore[arg-type]
    assert extra["same_step_tp_net_r_share"] is None
    assert extra["same_step_tp_trade_share"] == 0.0
    assert extra["net_r_stderr"] == 0.0

    # 분모가 **음수**인 경우 — 이 좌표의 실제 모습이다.
    monkeypatch.setattr(
        tri,
        "classify_trades",
        lambda pairs: {"tp_trades": 5.0, "tp_net_r": 3.0, "net_r": -50.0},
    )
    assert tri._extra_kwargs(_Fake())["same_step_tp_net_r_share"] is None  # type: ignore[arg-type]

    # 분모가 양수이고 문턱 위 — 그때만 낸다.
    monkeypatch.setattr(
        tri,
        "classify_trades",
        lambda pairs: {"tp_trades": 5.0, "tp_net_r": 30.0, "net_r": 100.0},
    )
    assert tri._extra_kwargs(_Fake())["same_step_tp_net_r_share"] == pytest.approx(0.3)  # type: ignore[arg-type]


def test_summary_flags_a_missing_cold_cut() -> None:
    """차가운 절단이 없으면 IS→OOS 뒤집힘을 **답하지 않는다**고 표에 적는다."""
    rows = _grid(-0.12, -0.07, -0.02, -0.02)
    text = tri.build_summary_markdown(rows, [], [])
    assert "차가운 절단" in text
    assert "뒤집히는가" in text


def test_segments_default_to_the_four_that_answer_is_versus_oos() -> None:
    """앞구간·뒷구간(완료기준 6)은 **차가운 절단이 있어야** 낼 수 있다."""
    assert SEGMENT_ORDER == ("full", "is", PRIMARY_OOS, "oos")
    assert tri.LOO_SEGMENTS == ("full", PRIMARY_OOS)
