"""WAN-389 — 재탭 차단 × 재진입 귀속 격자의 회귀 테스트.

이 파일이 지키는 것은 **라벨이 아니라 동작**이다(WAN-91/95/112/123/159가 반복해 경계한
자리). 이 모듈은 같은 payload를 **두 번 배치**해 두 팔을 만드는 구조라, 「재진입 끔」이
이름만 그렇고 조용히 채택 팔로 도는 실패가 **특히 쉽다** — 그래서 축이 실제로 걸리는지를
`include_reentry`가 배치까지 도달하는지로, 그리고 재진입 거래 0건으로 이중으로 고정한다.
"""

from __future__ import annotations

import pytest

from backtest import harness
from backtest import wan388_merge_x_retap as wan388
from backtest import wan389_retap_attribution as attr
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan388_merge_retap_census import ADOPTED_COMBINE_OBS, ADOPTED_RETAP_MODE

# --------------------------------------------------------------------------- #
# 팔 — 격자가 2×2인가, 그리고 채택 팔이 하나인가
# --------------------------------------------------------------------------- #


def test_grid_is_a_two_by_two_of_retap_and_reentry() -> None:
    """재탭 차단 효과를 **두 번** 못 내면 귀속이 성립하지 않는다."""
    assert {(a.retap_mode, a.reentry) for a in attr.ARMS} == {
        ("every_tap", True),
        ("every_tap", False),
        ("once", True),
        ("once", False),
    }
    # 존은 넷 다 채택값(분리) — 병합 축은 WAN-388이 ≈0으로 닫았다.
    assert {a.combine_obs for a in attr.ARMS} == {ADOPTED_COMBINE_OBS}


def test_exactly_one_arm_is_the_adopted_book() -> None:
    adopted = [a for a in attr.ARMS if a.is_adopted]
    assert [a.name for a in adopted] == [attr.ADOPTED_ARM]
    assert adopted[0].retap_mode == ADOPTED_RETAP_MODE
    assert adopted[0].reentry is True


def test_compute_unit_is_the_retap_mode_not_the_arm() -> None:
    """📌 재탭 모드 하나가 팔 **둘**을 먹인다 — 이것이 컴퓨트를 절반으로 만드는 성질이다."""
    for mode in attr.RETAP_MODES:
        arms = attr.arms_for_retap(mode)
        assert len(arms) == 2
        assert {a.reentry for a in arms} == {True, False}


def test_noise_line_is_inherited_not_re_chosen() -> None:
    """판정선을 이 모듈이 따로 고르면 WAN-388 표와 **다른 자로** 읽게 된다.

    라벨(상수 이름)이 아니라 **동작**으로 건다 — 그 자를 실제로 쓰는 곳은 표시(`_fmt`)와
    판정(`Verdict.label`) 둘이고, 둘 다 WAN-388이 못 박은 0.005R에서 갈려야 한다.
    """
    assert wan388.NOISE_R == 0.005
    assert attr._fmt(0.0049).endswith("(≈0)")
    assert not attr._fmt(0.0051).endswith("(≈0)")
    # 판정도 같은 자를 쓴다 — 효과 0.0049R은 「사라졌다」로 읽힌다.
    assert attr.verdict_for(_grid(-0.12, -0.10, -0.13, -0.1251), PRIMARY_OOS).label == (
        "재진입이 채운 몫"
    )


# --------------------------------------------------------------------------- #
# 배선 — 축·회계가 실제로 넘어가나
# --------------------------------------------------------------------------- #


def test_cell_kwargs_match_wan388_and_name_the_take_profit_liquidity() -> None:
    """🚨 후보 생성 회계가 WAN-388과 갈리면 검산 (a)가 성립할 수 없다(WAN-370/373)."""
    kwargs = attr._cell_kwargs()
    assert kwargs["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert kwargs["reentry"] is True  # 후보에는 **항상** 실어 두고 배치에서 고른다
    assert kwargs == wan388._cell_kwargs()


def test_build_payloads_forwards_the_retap_axis_and_the_adopted_accounting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def fake_run_cells(symbols, timeframes, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return []

    monkeypatch.setattr(attr, "run_cells", fake_run_cells)
    attr.build_payloads(
        ["BTC/USDT:USDT"],
        ["1h"],
        retap_mode="once",
        start="2024-01-01",
        end="2024-02-01",
        jobs=1,
    )
    assert seen["retap_mode"] == "once"
    assert seen["combine_obs"] is ADOPTED_COMBINE_OBS
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert seen["reentry"] is True


@pytest.mark.parametrize("reentry", [True, False])
def test_place_forwards_include_reentry_and_the_adopted_accounting(  # type: ignore[no-untyped-def]
    monkeypatch, reentry: bool
) -> None:
    """🚨 이 모듈의 축은 **배치 인자 하나**다 — 그것이 도달하지 않으면 두 팔이 같은 판이다."""
    seen: dict[str, object] = {}

    def fake_iter(payloads, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return []

    monkeypatch.setattr(attr, "iter_book_segments", fake_iter)
    attr.place([], start_ms=0, end_ms=1, segments=["full"], include_reentry=reentry)
    assert seen["include_reentry"] is reentry
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert seen["min_stop_distance_fraction"] == ADOPTED_STOP_GUARD
    assert seen["compound_sizing"] is False  # 복리 총수익은 판정 자가 아니다(WAN-346)


def test_arm_rows_place_with_the_arms_own_reentry_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """팔의 축이 배치까지 흘러가는지 — 여기가 끊기면 재진입 끈 팔이 조용히 채택 팔이 된다."""
    seen: list[bool] = []

    def fake_place(payloads, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(bool(kwargs["include_reentry"]))
        return []

    monkeypatch.setattr(attr, "place", fake_place)
    for arm in attr.ARMS:
        attr.build_arm_rows([], arm=arm, start_ms=0, end_ms=1, num_symbols=1)
    assert seen == [arm.reentry for arm in attr.ARMS]


# --------------------------------------------------------------------------- #
# 판정 줄
# --------------------------------------------------------------------------- #


def _row(
    arm: str, *, net: float, trades: int = 1000, reentry_trades: int = 50
) -> attr.AttributionRow:
    spec = attr.ARMS_BY_NAME[arm]
    return attr.AttributionRow(
        arm=spec.name,
        label=spec.label,
        combine_obs=spec.combine_obs,
        retap_mode=spec.retap_mode,
        reentry=spec.reentry,
        segment=PRIMARY_OOS,
        adopted_arm=spec.is_adopted,
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
        retap_trades=100 if spec.retap_mode == "every_tap" else 0,
        retap_trade_share=0.1 if spec.retap_mode == "every_tap" else 0.0,
        reentry_trades=reentry_trades if spec.reentry else 0,
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


def _grid(
    on_every: float, on_once: float, off_every: float, off_once: float
) -> list[attr.AttributionRow]:
    return [
        _row("split_every", net=on_every),
        _row("split_once", net=on_once),
        _row("split_every_no_reentry", net=off_every),
        _row("split_once_no_reentry", net=off_once),
    ]


def test_verdict_gives_the_retap_effect_twice_and_their_difference() -> None:
    rows = _grid(on_every=-0.12, on_once=-0.10, off_every=-0.13, off_once=-0.125)
    v = attr.verdict_for(rows, PRIMARY_OOS)
    assert v.retap_effect_reentry_on == pytest.approx(0.02)
    assert v.retap_effect_reentry_off == pytest.approx(0.005)
    assert v.reentry_fill == pytest.approx(0.015)


def test_label_says_reentry_filled_it_when_turning_reentry_off_kills_the_effect() -> None:
    """재진입을 끄면 효과가 노이즈선 아래로 — 그러면 값은 재탭이 아니라 슬롯 배분의 몫이다."""
    rows = _grid(on_every=-0.12, on_once=-0.10, off_every=-0.13, off_once=-0.1305)
    v = attr.verdict_for(rows, PRIMARY_OOS)
    assert v.label == "재진입이 채운 몫"
    text = attr.build_summary_markdown(rows, [], [])
    assert "재진입이 채운 몫이다" in text
    assert "슬롯 배분" in text


def test_label_says_retap_when_the_effect_survives_reentry_off() -> None:
    rows = _grid(on_every=-0.12, on_once=-0.10, off_every=-0.13, off_once=-0.109)
    v = attr.verdict_for(rows, PRIMARY_OOS)
    assert v.label == "재탭을 뺀 몫"  # 효과 +0.021R · 차 −0.001R(노이즈선 안)
    assert "재탭을 뺀 몫이다" in attr.build_summary_markdown(rows, [], [])


def test_label_says_both_when_the_effect_survives_but_the_gap_is_real() -> None:
    rows = _grid(on_every=-0.12, on_once=-0.10, off_every=-0.13, off_once=-0.120)
    v = attr.verdict_for(rows, PRIMARY_OOS)
    assert v.label == "둘 다"
    assert "둘 다 섞여 있다" in attr.build_summary_markdown(rows, [], [])


def test_residual_share_is_withheld_when_the_reference_is_noise() -> None:
    """🚨 기준이 0 언저리면 비율이 뜻을 잃는다 — 내지 않는다(WAN-115 함정)."""
    noisy = _grid(on_every=-0.12, on_once=-0.1199, off_every=-0.13, off_once=-0.05)
    assert attr.verdict_for(noisy, PRIMARY_OOS).residual_share is None
    real = _grid(on_every=-0.12, on_once=-0.10, off_every=-0.13, off_once=-0.12)
    assert attr.verdict_for(real, PRIMARY_OOS).residual_share == pytest.approx(0.5)


def test_summary_refuses_to_invent_a_verdict_from_an_empty_grid() -> None:
    assert "판정하지 않는다" in attr.build_summary_markdown([], [], [])


def test_summary_warns_when_the_two_by_two_is_incomplete() -> None:
    """팔이 셋뿐이면 재탭 효과를 두 번 못 낸다 — 그 사실을 **표에 찍는다**."""
    rows = _grid(-0.12, -0.10, -0.13, -0.12)[:3]
    text = attr.build_summary_markdown(rows, [], [])
    assert "2×2가 아직 안 찼다" in text


def test_summary_always_prints_the_trade_counts_next_to_net_r() -> None:
    """완료기준 4 — 재진입을 끄면 거래가 크게 주므로 「덜 매매해서」와 구분해야 한다."""
    rows = _grid(on_every=-0.12, on_once=-0.10, off_every=-0.13, off_once=-0.12)
    rows[3] = rows[3].model_copy(update={"num_trades": 400})
    text = attr.build_summary_markdown(rows, [], [])
    assert "거래 수 병기" in text
    assert "400" in text


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def test_reentry_axis_check_catches_a_label_only_arm() -> None:
    """`reentry=False`인데 재진입 거래가 남아 있으면 축이 안 걸린 것이다."""
    clean = _row("split_every_no_reentry", net=-0.13)
    dirty = clean.model_copy(update={"reentry_trades": 4})
    assert [c.abs_diff for c in attr.check_reentry_axis([clean])] == [0.0]
    assert [c.abs_diff for c in attr.check_reentry_axis([dirty])] == [4.0]
    # 재진입 켠 팔은 재진입 거래가 있는 게 정상이라 이 검산의 대상이 아니다.
    assert attr.check_reentry_axis([_row("split_every", net=-0.12)]) == []


def test_retap_axis_check_is_inherited_from_wan388() -> None:
    clean = _row("split_once", net=-0.10)
    dirty = clean.model_copy(update={"retap_trades": 3})
    assert [c.abs_diff for c in attr.check_retap_axis([clean])] == [0.0]
    assert [c.abs_diff for c in attr.check_retap_axis([dirty])] == [3.0]
    assert attr.check_retap_axis([_row("split_every", net=-0.12)]) == []


def test_arm_invariant_check_compares_coordinates_not_pnl() -> None:
    rows = attr.check_arm_invariants(_grid(-0.12, -0.10, -0.13, -0.12))
    assert {r.metric for r in rows} == {"num_cells", "num_symbols"}
    assert all(r.abs_diff == 0.0 for r in rows)

    shifted = _grid(-0.12, -0.10, -0.13, -0.12)
    shifted[1] = shifted[1].model_copy(update={"num_cells": 47})
    assert any(r.abs_diff == 1.0 for r in attr.check_arm_invariants(shifted))


def test_wan388_check_compares_the_adopted_arm_against_the_published_csv(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """검산 (a) — WAN-388 CSV의 같은 팔과 대조한다(다른 모듈·다른 실행의 같은 숫자)."""
    row = _row("split_every", net=-0.1194147862373233)
    csv = tmp_path / "wan388.csv"
    frame = attr.grid_to_frame([row]).assign(arm=attr.WAN388_REFERENCE_ARM)
    frame.to_csv(csv, index=False)

    checks = attr.check_against_wan388([row], path=csv)
    assert checks and all(c.abs_diff == 0.0 for c in checks)
    assert {c.metric for c in checks} == set(attr._WAN388_CHECK_METRICS)

    moved = row.model_copy(update={"num_trades": row.num_trades + 1})
    assert max(c.abs_diff for c in attr.check_against_wan388([moved], path=csv)) == 1.0


def test_wan388_check_refuses_to_compare_a_different_coordinate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """🚨 좌표가 다르면 아예 내지 않는다 — 파일럿 수를 48칸 판에서 빼면 배선 오류로 읽힌다."""
    published = _row("split_every", net=-0.119)
    csv = tmp_path / "wan388.csv"
    attr.grid_to_frame([published]).assign(arm=attr.WAN388_REFERENCE_ARM).to_csv(csv, index=False)

    pilot = published.model_copy(update={"num_cells": 1, "num_symbols": 1, "num_trades": 62})
    assert attr.check_against_wan388([pilot], path=csv) == []
    text = attr.build_summary_markdown([pilot], [], attr.check_reentry_axis([pilot]) or [])
    # 검산 표가 비어 있지 않은 실행에서만 안내가 뜬다 — 아래는 (b)만 있는 판.
    assert "(a)가 없다" in attr.build_summary_markdown(
        [pilot], [], attr.check_arm_invariants(_grid(-0.1, -0.1, -0.1, -0.1))
    )
    assert isinstance(text, str)


def test_wan388_check_is_silent_when_the_csv_is_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert (
        attr.check_against_wan388([_row("split_every", net=-0.1)], path=tmp_path / "nope.csv") == []
    )


# --------------------------------------------------------------------------- #
# 진입가 깊이 열 — 재진입 후보를 셀지 팔이 정한다
# --------------------------------------------------------------------------- #


def test_entry_in_zone_excludes_reentry_candidates_for_the_reentry_off_arm() -> None:
    """🚨 배치되지도 않은 재진입 주문이 열을 움직이면 라벨이 거짓이 된다."""
    from backtest.models import ExitReason, PositionSide
    from backtest.wan169_leverage_book import CellPayload
    from backtest.zone_limit_backtest import _Candidate
    from strategy.models import OrderBlock

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

    def cand(price: float) -> _Candidate:
        return _Candidate(
            side=PositionSide.LONG,
            entry_time=10,
            entry_price=price,
            exit_time=20,
            exit_price=price,
            reason=ExitReason.TAKE_PROFIT,
            stop_price=100.0,
            take_profit_price=120.0,
            order_block=block,
            trigger_time=10,
        )

    payload = CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        boundary_ms=0,
        candidates={harness.SEGMENT_FULL: (cand(110.0),)},  # 깊이 0.0
        reentry_candidates={harness.SEGMENT_FULL: (cand(100.0),)},  # 깊이 1.0
        funding={harness.SEGMENT_FULL: ()},
        rows=(),
    )
    without = attr.entry_in_zone([payload], harness.SEGMENT_FULL, include_reentry=False)
    with_reentry = attr.entry_in_zone([payload], harness.SEGMENT_FULL, include_reentry=True)
    assert without == pytest.approx(0.0)
    assert with_reentry == pytest.approx(0.5)  # 두 후보의 중앙값


# --------------------------------------------------------------------------- #
# 검산 등급 — 「성공이 실패와 같은 모양」 방지
# --------------------------------------------------------------------------- #


def test_checksum_grade_separates_a_match_from_float_noise_from_a_real_break() -> None:
    """🚨 CSV 텍스트 왕복은 1e-17을 남긴다 — 그걸 「불일치」로 찍으면 진짜 오류가 묻힌다.

    WAN-151/161이 세운 관행(일치 · 잡음 · 불일치를 **다르게** 찍는다)을 동작으로 고정한다.
    """
    assert attr.checksum_grade(0.0) == "비트 일치"
    assert "잡음" in attr.checksum_grade(8.5e-17)
    assert "잡음" in attr.checksum_grade(1e-12)
    assert "불일치" in attr.checksum_grade(1.0)
    # 경계 자체도 못 박는다 — 느슨해지면 진짜 배선 오류가 「잡음」으로 통과한다.
    assert attr.CHECKSUM_NOISE == 1e-9
    assert "불일치" in attr.checksum_grade(attr.CHECKSUM_NOISE)


def test_summary_does_not_cry_mismatch_over_float_noise() -> None:
    rows = _grid(-0.12, -0.10, -0.13, -0.12)
    noisy = [
        wan388.ChecksumRow(
            check="a WAN-388 같은 팔",
            arm="split_every",
            segment=PRIMARY_OOS,
            metric="mean_net_r",
            left=-0.119415,
            right=-0.119415,
            abs_diff=1.39e-17,
        )
    ]
    text = attr.build_summary_markdown(rows, [], noisy)
    assert "잡음" in text
    assert "⚠️ 불일치" not in text


# --------------------------------------------------------------------------- #
# 부호가 이슈 가설과 반대인 경우 — 실측이 그랬다
# --------------------------------------------------------------------------- #


def test_negative_fill_is_reported_as_reentry_eating_the_gain() -> None:
    """🚨 「채운 몫」이 음수면 재진입은 효과를 **부풀린 게 아니라 깎은** 것이다.

    분류 규칙(`label`)은 착수 전에 못 박은 그대로 두고 **부호대로 적는지**만 본다 —
    실측(`oos_warm`: 켬 +0.0152R · 끔 +0.0440R · 차 −0.0288R)이 이 자리였다.
    """
    rows = _grid(on_every=-0.1194, on_once=-0.1042, off_every=-0.1098, off_once=-0.0658)
    v = attr.verdict_for(rows, PRIMARY_OOS)
    assert v.retap_effect_reentry_on == pytest.approx(0.0152, abs=1e-4)
    assert v.retap_effect_reentry_off == pytest.approx(0.0440, abs=1e-4)
    assert v.reentry_fill == pytest.approx(-0.0288, abs=1e-4)
    assert v.eaten_share == pytest.approx(0.655, abs=0.01)

    text = attr.build_summary_markdown(rows, [], [])
    assert "부호가 반대다" in text
    assert "오히려 깎는다" in text
    # 🚨 「재진입이 채운 몫이다」(부풀렸다는 뜻)로 읽히면 안 된다.
    assert "재진입이 채운 몫이다" not in text


def test_eaten_share_is_withheld_when_it_has_no_meaning() -> None:
    positive_fill = _grid(on_every=-0.12, on_once=-0.10, off_every=-0.13, off_once=-0.125)
    v_pos = attr.verdict_for(positive_fill, PRIMARY_OOS)
    assert v_pos.reentry_fill is not None and v_pos.reentry_fill > 0
    assert v_pos.eaten_share is None

    # 분모가 노이즈선 안이면 비율이 뜻을 잃는다(WAN-115 함정).
    tiny_off = _grid(on_every=-0.12, on_once=-0.100, off_every=-0.13, off_once=-0.1301)
    assert attr.verdict_for(tiny_off, PRIMARY_OOS).eaten_share is None
