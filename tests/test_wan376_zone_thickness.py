"""WAN-376: 존의 두께 층 재측정 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 여섯:

1. **관측 필드는 순수하다** — `observe_zone_width_atr`를 켜도 후보·체결·손익이 하나도 안
   움직인다(실데이터). 관측이 대상을 바꾸면 그 순간 이 측정은 무효다(WAN-328 선례).
2. **지름길 팔이 실제로 컷한다** — 인자만 넓히고 배선을 빠뜨리면 기준선과 같은 수가 나와
   조용히 통과한다(WAN-345 선례).
3. **컷이 재진입 파생 앞에 걸린다**(실데이터) — 이 이슈의 급소다. 뒤에 걸면 「빠진 셋업의
   재진입이 살아남는」 잡종이 되는데, 개수만 보면 안 보인다.
4. **이중 필터를 거부한다** — 엔진 필터를 켠 채 후처리 컷을 주면 라벨이 거짓이 된다.
5. **§0의 자가 엔진의 자와 같다**(실데이터) — 모듈이 ATR을 자기 손으로 계산하므로, 엔진의
   관측 필드와 **값으로** 대조한다. 사본은 갈라진다(WAN-77).
6. **지도는 컷이지 재측정이 아니다** — 조인 문턱·높은 가드의 생존 집합이 느슨한 쪽의
   부분집합이고, 요약이 판정을 지어내지 않는다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import MIN_TRADES_PER_SYMBOL
from backtest.wan169_leverage_book import run_cells
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan376_zone_thickness import (
    ADOPTED_STOP_GUARD,
    ADOPTED_ZONE_WIDTH,
    GUARD_POINTS,
    MAP_ARMS,
    WIDTH_POINTS,
    ParityRow,
    TapThickness,
    _book_diffs,
    assert_adopted_base,
    build_summary,
    collapse_points,
    map_to_frame,
    parity_to_frame,
    survival_rows,
    tap_thickness,
    width_label,
)
from execution.sizing import PositionSizingParams
from strategy.models import ConfluenceParams

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
_REAL_START = "2024-01-01"
_REAL_END = "2024-10-01"


def _shared_kwargs() -> dict[str, Any]:
    return {
        "start": _REAL_START,
        "end": _REAL_END,
        "jobs": 1,
        "cold_segments": False,
        "engine_check": False,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        **ADOPTED_CELL_KWARGS,
    }


def _skip_without_real_data() -> None:
    """🚨 게이트는 `run_cells`·`tap_thickness` **호출 전에** 판정한다 — 안 그러면 CI의 빈
    DB가 skip이 아니라 실패로 끝난다(이 저장소가 이미 겪은 실패)."""
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")


def _bare(candidates: Any) -> list[tuple[Any, ...]]:
    """관측 필드를 뺀 후보 지문 — 「관측이 대상을 안 바꿨나」를 재는 자."""
    return [
        (c.entry_time, c.entry_price, c.exit_time, c.exit_price, c.reason, c.stop_price)
        for c in candidates
    ]


# --------------------------------------------------------------------------- #
# 0 · 라벨이 오늘의 채택 기본값과 같다
# --------------------------------------------------------------------------- #


def test_labels_match_today_defaults() -> None:
    """이 테스트가 깨지면 표의 제목(1.28 · 0.3%)이 거짓이 된 것이다.

    🚨 **존폭 축만 예외다**(WAN-384) — 이 격자는 필터를 켠 채(1.28) 낸 기록이라 중심점이
    **명시 핀**이고, 그래서 「기본값과 같은가」가 아니라 「그 시절 채택값과 같은가」를 본다.
    """
    assert_adopted_base()
    assert ConfluenceParams().max_zone_width_atr is None  # 오늘의 채택은 꺼짐
    assert ADOPTED_ZONE_WIDTH == harness.LEGACY_ZONE_WIDTH_FILTER_ON == 1.28
    assert PositionSizingParams().min_stop_distance_fraction == ADOPTED_STOP_GUARD
    assert ADOPTED_ZONE_WIDTH in WIDTH_POINTS
    assert ADOPTED_STOP_GUARD in GUARD_POINTS


def test_adopted_base_rejects_a_moved_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """문턱이 움직이면 **시끄럽게** 죽는다 — 조용히 새 값으로 도는 것이 이 저장소의 사고다."""
    import backtest.wan376_zone_thickness as module

    monkeypatch.setattr(module, "ADOPTED_ZONE_WIDTH", 9.99)
    with pytest.raises(AssertionError, match="존폭 중심점"):
        module.assert_adopted_base()


def test_width_label_separates_off_from_a_number() -> None:
    """`off`(끔)와 숫자를 **문자로** 가른다 — 둘이 같은 키가 되면 표에서 섞인다."""
    assert width_label(None) == "off"
    assert width_label(ADOPTED_ZONE_WIDTH) == "1.28"
    assert width_label(None) != width_label(ADOPTED_ZONE_WIDTH)


# --------------------------------------------------------------------------- #
# 1 · 관측 필드는 순수하다 (실데이터)
# --------------------------------------------------------------------------- #


def test_observation_field_moves_nothing() -> None:
    """켜도 후보·체결·청산이 **비트 단위로 같다** — 관측이 대상을 바꾸면 이 측정은 무효다."""
    _skip_without_real_data()
    # ⚠️ 두 팔의 **문턱이 같아야** 이 검사가 「관측 축」을 재지 「필터 축」을 재지 않는다
    # (WAN-384 이후 문턱은 명시다 — 기본값은 꺼짐).
    off = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        max_zone_width_atr=ADOPTED_ZONE_WIDTH,
        **_shared_kwargs(),
    )
    on = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        max_zone_width_atr=ADOPTED_ZONE_WIDTH,
        observe_zone_width_atr=True,
        **_shared_kwargs(),
    )
    a = off[0].candidates[harness.SEGMENT_FULL]
    b = on[0].candidates[harness.SEGMENT_FULL]
    assert a, "후보가 없어 검사가 성립하지 않습니다."
    assert _bare(a) == _bare(b)
    assert all(c.zone_width_atr is None for c in a), "안 켰는데 값이 실렸습니다."
    assert any(c.zone_width_atr is not None for c in b), "켰는데 값이 안 실렸습니다."
    # 재진입 파생도 같은 후보에서 나온다.
    assert _bare(off[0].reentry_candidates[harness.SEGMENT_FULL]) == _bare(
        on[0].reentry_candidates[harness.SEGMENT_FULL]
    )


def test_observed_ratio_respects_the_adopted_threshold() -> None:
    """필터를 켠 판의 후보는 **정의상** 문턱 이하다 — 아니면 엔진과 관측이 다른 값을 본다."""
    _skip_without_real_data()
    on = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        # WAN-384 이후 문턱은 명시다(기본값은 꺼짐) — 안 주면 「필터 켠 판」이 아니게 된다.
        max_zone_width_atr=ADOPTED_ZONE_WIDTH,
        observe_zone_width_atr=True,
        **_shared_kwargs(),
    )
    ratios = [
        c.zone_width_atr
        for c in on[0].candidates[harness.SEGMENT_FULL]
        if c.zone_width_atr is not None
    ]
    assert ratios
    assert max(ratios) <= ADOPTED_ZONE_WIDTH


# --------------------------------------------------------------------------- #
# 2·3 · 지름길 팔 — 실제로 컷하고, 컷이 재진입 **앞**에 걸린다 (실데이터)
# --------------------------------------------------------------------------- #


def test_shortcut_arm_reproduces_the_straight_arm() -> None:
    """§1a의 본체를 한 칸으로 — base와 **재진입 파생**이 함께 같아야 한다.

    🚨 재진입이 급소다: 컷을 파생 **뒤에** 걸면 「빠진 셋업의 재진입」이 살아남는데, base만
    보면 통과한다. 그래서 두 리스트를 따로 건다.
    """
    _skip_without_real_data()
    straight = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        # WAN-384 이후 문턱은 명시다 — 센티넬은 「필터 꺼짐」으로 풀려 등식이 무의미해진다.
        max_zone_width_atr=ADOPTED_ZONE_WIDTH,
        observe_zone_width_atr=True,
        **_shared_kwargs(),
    )
    shortcut = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        max_zone_width_atr=None,
        post_filter_zone_width=ADOPTED_ZONE_WIDTH,
        **_shared_kwargs(),
    )
    a, b = straight[0], shortcut[0]
    assert a.candidates[harness.SEGMENT_FULL], "후보가 없어 검사가 성립하지 않습니다."
    assert list(a.candidates[harness.SEGMENT_FULL]) == list(b.candidates[harness.SEGMENT_FULL])
    assert a.reentry_candidates[harness.SEGMENT_FULL], "재진입 후보가 없어 급소를 못 잽니다."
    assert list(a.reentry_candidates[harness.SEGMENT_FULL]) == list(
        b.reentry_candidates[harness.SEGMENT_FULL]
    )


def test_shortcut_arm_actually_cuts() -> None:
    """컷을 안 걸면 필터 끈 판(더 많은 후보)이 그대로 나온다 — 배선 누락을 잡는다."""
    _skip_without_real_data()
    uncut = run_cells([_REAL_SYMBOL], [_REAL_TF], max_zone_width_atr=None, **_shared_kwargs())
    cut = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        max_zone_width_atr=None,
        post_filter_zone_width=ADOPTED_ZONE_WIDTH,
        **_shared_kwargs(),
    )
    n_uncut = len(uncut[0].candidates[harness.SEGMENT_FULL])
    n_cut = len(cut[0].candidates[harness.SEGMENT_FULL])
    assert 0 < n_cut < n_uncut, f"컷이 안 걸렸습니다({n_cut} vs {n_uncut})."


# --------------------------------------------------------------------------- #
# 4 · 이중 필터를 거부한다
# --------------------------------------------------------------------------- #


def test_double_filter_is_refused() -> None:
    """엔진 필터를 켠 채 후처리 컷을 주면 라벨이 거짓이 된다 — 조용히 접지 않는다."""
    with pytest.raises(ValueError, match="이중 필터"):
        run_cells(
            [_REAL_SYMBOL],
            [_REAL_TF],
            post_filter_zone_width=ADOPTED_ZONE_WIDTH,
            **_shared_kwargs(),
        )
    with pytest.raises(ValueError, match="이중 필터"):
        run_cells(
            [_REAL_SYMBOL],
            [_REAL_TF],
            max_zone_width_atr=1.0,
            post_filter_zone_width=ADOPTED_ZONE_WIDTH,
            **_shared_kwargs(),
        )


def test_dropout_lens_is_refused() -> None:
    """🚨 탈락 렌즈에서는 지름길이 **원리적으로** 깨진다 — 조용히 돌면 표가 거짓이 된다.

    추첨 순서가 「어느 셋업이 체결됐나」에 달려 있어, 넓은 셋업을 안 만들면 뒤 셋업의 난수가
    통째로 밀린다. `baseline`은 `dropout_rate=0`이라 난수를 뽑지도 않아 무관하다.
    """
    dropout = next(f for f in harness.FILL_PRESETS if f.dropout_rate > 0)
    with pytest.raises(ValueError, match="탈락 렌즈"):
        run_cells(
            [_REAL_SYMBOL],
            [_REAL_TF],
            max_zone_width_atr=None,
            post_filter_zone_width=ADOPTED_ZONE_WIDTH,
            fill=dropout,
            **_shared_kwargs(),
        )


def test_baseline_lens_is_allowed_with_the_shortcut() -> None:
    """가드가 **탈락이 있는 렌즈만** 막는지 — 전부 막으면 지름길 자체가 못 돈다."""
    baseline = harness.BASELINE_FILL
    assert baseline.dropout_rate == 0.0
    _skip_without_real_data()
    payloads = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        max_zone_width_atr=None,
        post_filter_zone_width=ADOPTED_ZONE_WIDTH,
        fill=baseline,
        **_shared_kwargs(),
    )
    assert payloads[0].candidates[harness.SEGMENT_FULL]


# --------------------------------------------------------------------------- #
# 5 · §0의 자 == 엔진의 자 (실데이터)
# --------------------------------------------------------------------------- #


def test_map_ruler_matches_the_engine_ruler() -> None:
    """§0는 탐지 층에서 ATR을 직접 계산한다 — **값으로** 엔진과 대조해야 사본이 안 갈라진다.

    엔진이 후보에 실은 비율의 **집합**이 §0가 낸 비율 집합의 부분집합이어야 한다(후보는
    체결까지 간 셋업만이라 §0의 탭 집합보다 작다).
    """
    _skip_without_real_data()
    taps = tap_thickness(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    assert taps
    engine = run_cells(
        [_REAL_SYMBOL],
        [_REAL_TF],
        max_zone_width_atr=None,
        observe_zone_width_atr=True,
        **_shared_kwargs(),
    )
    from_engine = {
        round(c.zone_width_atr, 10)
        for c in engine[0].candidates[harness.SEGMENT_FULL]
        if c.zone_width_atr is not None
    }
    from_map = {round(t.zone_width_atr, 10) for t in taps if t.zone_width_atr is not None}
    assert from_engine, "엔진이 비율을 안 실었습니다."
    assert from_engine <= from_map, (
        "엔진이 본 비율이 §0 지도에 없습니다 — 두 자가 갈라졌습니다(WAN-77 부류)."
    )


def test_map_arms_split_the_stop_width() -> None:
    """`mid`는 진입가를 존 중앙으로 내려 **손절폭이 절반 안팎**이다 — 팔이 실제로 갈린다."""
    _skip_without_real_data()
    taps = tap_thickness(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
    )
    assert taps
    for tap in taps:
        assert set(tap.stop_fraction) == set(MAP_ARMS)
        assert tap.stop_fraction["mid"] < tap.stop_fraction["proximal"]


# --------------------------------------------------------------------------- #
# 6 · 지도는 컷이지 재측정이 아니다 (합성)
# --------------------------------------------------------------------------- #


def _synthetic(widths: list[float | None], stops: list[float]) -> list[TapThickness]:
    return [
        TapThickness(zone_width_atr=w, stop_fraction={"proximal": s, "mid": s / 2.0})
        for w, s in zip(widths, stops, strict=True)
    ]


def test_tighter_points_are_subsets() -> None:
    """조인 문턱·높은 가드의 생존 수가 느슨한 쪽을 넘을 수 없다 — 컷의 정의다."""
    taps = _synthetic(
        [0.5, 0.85, 1.2, 1.5, 2.0, None] * 8,
        [0.001, 0.0022, 0.0031, 0.0045, 0.006, 0.0035] * 8,
    )
    grid, _q = survival_rows("BTC/USDT:USDT", "4h", taps)
    frame = map_to_frame(grid)
    prox = frame[frame["arm"] == "proximal"]
    for guard in GUARD_POINTS:
        column = prox[prox["guard"] == guard].set_index("width_label")["surviving_taps"]
        ordered = [column[width_label(w)] for w in WIDTH_POINTS]
        assert ordered == sorted(ordered, reverse=True), f"문턱 축이 단조가 아닙니다(가드 {guard})"
    for threshold in WIDTH_POINTS:
        row = prox[prox["width_label"] == width_label(threshold)].sort_values("guard")
        alive = list(row["surviving_taps"])
        assert alive == sorted(alive, reverse=True), f"가드 축이 단조가 아닙니다(문턱 {threshold})"


def test_unrated_taps_pass_only_when_the_filter_is_off() -> None:
    """판정 불가(ATR 워밍업)는 필터가 켜지면 **기각**이다 — 엔진(WAN-158)과 같은 규칙."""
    taps = _synthetic([None] * 30, [0.01] * 30)
    grid, _q = survival_rows("BTC/USDT:USDT", "4h", taps)
    by_label = {r.width_label: r for r in grid if r.arm == "proximal" and r.guard == 0.0}
    assert by_label["off"].surviving_taps == 30
    assert by_label[width_label(ADOPTED_ZONE_WIDTH)].surviving_taps == 0


def test_sample_gate_is_an_upper_bound_flag() -> None:
    """탭이 게이트 미만이면 **확실히** 판정 불가 — 그 한 방향만 찍는다."""
    taps = _synthetic([0.5] * 5, [0.01] * 5)
    grid, _q = survival_rows("BTC/USDT:USDT", "4h", taps)
    assert all(r.below_sample_gate for r in grid if r.surviving_taps < MIN_TRADES_PER_SYMBOL)
    assert not any(r.below_sample_gate for r in grid if r.surviving_taps >= MIN_TRADES_PER_SYMBOL)


def test_adopted_point_is_marked_exactly_once_per_arm() -> None:
    """지도에 「지금 여기」가 팔마다 정확히 하나여야 한다."""
    taps = _synthetic([1.0] * 25, [0.01] * 25)
    grid, _q = survival_rows("BTC/USDT:USDT", "4h", taps)
    for arm in MAP_ARMS:
        marked = [r for r in grid if r.arm == arm and r.adopted_point]
        assert len(marked) == 1


def test_collapse_points_report_the_worst_cell() -> None:
    taps = _synthetic([0.5, 2.0] * 15, [0.001, 0.01] * 15)
    grid, _q = survival_rows("BTC/USDT:USDT", "4h", taps)
    frame = collapse_points(map_to_frame(grid))
    assert len(frame) == 1
    assert int(frame["total_taps"].iloc[0]) == 30
    assert int(frame["worst_alive"].iloc[0]) <= int(frame["adopted_survivors"].iloc[0])


# --------------------------------------------------------------------------- #
# 7 · 요약은 판정을 지어내지 않는다
# --------------------------------------------------------------------------- #


def _parity(level: str, **over: float) -> ParityRow:
    base: dict[str, Any] = {
        "scope": "4h",
        "level": level,
        "segment": "oos_warm",
        "num_cells": 12,
        "num_candidates": 100,
        "num_trades": 100,
        "win_rate": 0.55,
        "total_return": 1.5,
        "mean_net_r": 0.11,
        "max_drawdown": 0.2,
        "peak_concurrency": 4,
        "liquidation_events": 0,
    }
    base.update(over)
    return ParityRow(**base)


def test_cell_parity_carries_the_scope() -> None:
    """칸 대조 행에 **스코프**가 실린다 — 지갑이 다르면 같은 칸이라도 따로 남아야 한다."""
    from backtest.wan376_zone_thickness import CELL_KEYS, CellParity

    assert CELL_KEYS[0] == "scope"
    row = CellParity(
        scope="15m+1h",
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        segment="full",
        straight_base=1,
        shortcut_base=1,
        straight_reentry=0,
        shortcut_reentry=0,
        base_identical=True,
        reentry_identical=True,
        width_identical=True,
    )
    assert row.scope == "15m+1h"


def test_book_diffs_are_empty_when_the_arms_agree() -> None:
    frame = parity_to_frame([_parity("straight"), _parity("shortcut")])
    assert _book_diffs(frame) == []
    text = build_summary(pd.DataFrame(), pd.DataFrame(), frame, pd.DataFrame())
    assert "지름길이 성립한다" in text


def test_book_diffs_surface_a_mismatch() -> None:
    """어긋나면 **어느 열이 얼마나** 어긋났는지 찍는다 — 조용히 통과하지 않는다."""
    frame = parity_to_frame([_parity("straight"), _parity("shortcut", mean_net_r=0.12)])
    diffs = _book_diffs(frame)
    assert [c for _s, c, _d in diffs] == ["mean_net_r"]
    text = build_summary(pd.DataFrame(), pd.DataFrame(), frame, pd.DataFrame())
    assert "지름길이 성립하지 않는다" in text
    assert "3N패스" in text


def test_summary_says_it_has_not_run_yet() -> None:
    """빈 표에 판정을 지어내지 않는다 — 두 절이 모두 「아직 안 돌렸다」로 남는다."""
    text = build_summary(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert text.count("_아직 안 돌렸다._") == 2
    assert "지름길이 성립한다" not in text
