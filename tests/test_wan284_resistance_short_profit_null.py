"""WAN-284 저항-존 숏 「수익 자」 매칭 널의 배선을 **동작으로** 고정한다 (완료기준 4).

지키는 것:

1. **널 기하 = 실제와 맞춘다** — 널 숏의 손절은 진입가 **위**이고 1R 비율은 공급된 경험분포에서
   나오며 익절은 그 1R의 정확히 `take_profit_r` 아래다(채택 규칙). 기하를 안 맞추면 널이
   퇴화해 타이밍이 아니라 1R 크기를 재게 된다(WAN-255 함정).
2. **개수 맞춤** — 널 추출 수는 그 (칸, 구간)의 실제 숏 후보 수이고, 과제 조립이 그 수를
   **셀에서** 읽는다(워커가 다시 세면 실제 팔과 갈라진다).
3. **렌즈가 실제·널에 동일 배선** — 관통 벌점이 널 시뮬레이터에 실제로 전달된다(라벨이 아니라
   체결 여부가 바뀐다).
4. **널 북은 롱을 안 건드린다** — `_replace_shorts`가 숏만 갈아끼우고 롱 후보는 비트 동일이며,
   `oos_warm`은 북(`_segment_cells`)과 **같은 키**(`full`)를 바꾼다. 안 맞추면 널이 라벨만
   달고 실제 숏으로 돈다(WAN-91/95/112/123/159 부류의 조용한 실패).
5. **재현성** — 같은 시드는 같은 추출, 다른 시드는 다른 추출.
6. **판정·병합·프레임 왕복** — 판정이 행에서 계산되고(문장에 안 박힘), 단일 TF `--append`가
   `all` 라벨을 덮지 않으며(WAN-283 PM 교정), CSV가 None p값을 보존한다.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.models import ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan169_leverage_book import CellPayload
from backtest.wan284_resistance_short_profit_null import (
    CROSSCHECK_TOL,
    LOO_ALL,
    MIN_TRADES_GATE,
    NULL_SEGMENTS,
    SEEDS,
    BookNullRow,
    ShortNullRow,
    _drop_shorts,
    _median,
    _null_short_candidate,
    _replace_shorts,
    _sample_indices,
    _stop_ratios,
    book_from_csv,
    book_to_frame,
    build_null_tasks,
    build_summary_markdown,
    crosscheck_wan282,
    describe_engine,
    draw_null_shorts,
    merge_book,
    merge_short,
    short_from_csv,
    short_to_frame,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams

_MINUTE = 60_000
_HTF_MS = 4 * 60 * 60 * 1000


def _substeps(n: int = 600, base: float = 100.0, drift: float = 0.0) -> list[SubStep]:
    """결정론적 1분 서브스텝 — 톱니 + 선형 드리프트(존·규칙 없이 순수 기하만 잰다)."""
    out: list[SubStep] = []
    for i in range(n):
        close = base + drift * i + (1.0 if i % 2 else -1.0)
        out.append(
            SubStep(
                time=i * _MINUTE,
                high=close * 1.004,
                low=close * 0.996,
                close=close,
                htf_bar_time=(i * _MINUTE) // _HTF_MS * _HTF_MS,
            )
        )
    return out


def _htf_arrays(steps: list[SubStep]) -> tuple[list[int], list[float]]:
    times = sorted({s.htf_bar_time for s in steps})
    closes = [next(s.close for s in steps if s.htf_bar_time == t) for t in times]
    return times, closes


def _params(*, penetration_bps: float = 0.0) -> ConfluenceParams:
    return harness.build_params(short_enabled=True).model_copy(
        update={"fill_penetration_bps": penetration_bps}
    )


def _cand(
    side: PositionSide,
    *,
    entry_time: int,
    entry_price: float = 100.0,
    stop_price: float = 105.0,
    trigger_time: int | None = None,
) -> _Candidate:
    return _Candidate(
        side=side,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=entry_time + _MINUTE,
        exit_price=entry_price,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=stop_price,
        trigger_time=entry_time if trigger_time is None else trigger_time,
    )


def _cell(boundary_ms: int = 500 * _MINUTE) -> CellPayload:
    """롱 2 + 숏 2(경계 앞뒤 각각) + 재진입 숏 1을 실은 합성 칸."""
    full = (
        _cand(PositionSide.LONG, entry_time=100 * _MINUTE),
        _cand(PositionSide.SHORT, entry_time=200 * _MINUTE),
        _cand(PositionSide.LONG, entry_time=700 * _MINUTE),
        _cand(PositionSide.SHORT, entry_time=800 * _MINUTE),
    )
    return CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="4h",
        boundary_ms=boundary_ms,
        candidates={
            SEGMENT_FULL: full,
            SEGMENT_IS: full[:2],
            SEGMENT_OOS: full[2:],
        },
        funding={SEGMENT_FULL: (), SEGMENT_IS: (), SEGMENT_OOS: ()},
        rows=(),
        reentry_candidates={
            SEGMENT_FULL: (_cand(PositionSide.SHORT, entry_time=900 * _MINUTE),),
            SEGMENT_IS: (),
            SEGMENT_OOS: (_cand(PositionSide.SHORT, entry_time=900 * _MINUTE),),
        },
    )


# --------------------------------------------------------------------------- #
# 1. 널 기하 — 실제와 맞춘다
# --------------------------------------------------------------------------- #


def test_null_short_stop_is_above_entry_and_tp_is_fixed_r_below() -> None:
    """숏 널의 손절은 진입 **위**, 익절은 그 1R의 정확히 `take_profit_r` 아래(채택 규칙)."""
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    params = _params()
    cand = _null_short_candidate(
        start=10,
        stop_ratio=0.02,
        substeps=steps,
        htf_times=times,
        htf_closes=closes,
        params=params,
    )
    assert cand is not None
    assert cand.side is PositionSide.SHORT
    entry = steps[10].close
    assert cand.stop_price == pytest.approx(entry * 1.02)
    assert cand.stop_price > cand.entry_price
    # 익절 목표는 시뮬레이터에 넘어가므로 청산가로 확인한다(익절로 닫혔다면 그 값).
    if cand.reason is ExitReason.TAKE_PROFIT:
        risk = entry * 0.02
        assert cand.exit_price == pytest.approx(entry - params.take_profit_r * risk, rel=1e-6)


def test_null_short_trigger_time_equals_entry_time() -> None:
    """널은 탭이 없으므로 경계 필터 키(`trigger_time`)가 진입 시각이어야 북이 옳게 거른다."""
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    cand = _null_short_candidate(
        start=42,
        stop_ratio=0.01,
        substeps=steps,
        htf_times=times,
        htf_closes=closes,
        params=_params(),
    )
    assert cand is not None
    assert cand.trigger_time == cand.entry_time


def test_null_stop_ratio_comes_from_supplied_distribution() -> None:
    """1R 비율은 **공급된 경험분포**에서만 나온다 — 널이 자기 기하를 지어내지 않는다."""
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    ratios = (0.01, 0.05)
    drawn = draw_null_shorts(
        seed=1,
        k=25,
        stop_ratios=ratios,
        pool=tuple(range(len(steps) - 50)),
        substeps=steps,
        htf_times=times,
        htf_closes=closes,
        params=_params(),
    )
    assert drawn
    for c in drawn:
        actual = (c.stop_price - c.entry_price) / c.entry_price
        assert min(abs(actual - r) for r in ratios) < 1e-9


def test_stop_ratios_reads_1r_from_actual_shorts() -> None:
    cands = [
        _cand(PositionSide.SHORT, entry_time=0, entry_price=100.0, stop_price=102.0),
        _cand(PositionSide.SHORT, entry_time=1, entry_price=200.0, stop_price=206.0),
    ]
    assert _stop_ratios(cands) == pytest.approx((0.02, 0.03))


def test_stop_ratios_drops_degenerate_geometry() -> None:
    """1R이 0이면 널이 무한 R을 내므로 분포에서 뺀다."""
    cands = [_cand(PositionSide.SHORT, entry_time=0, entry_price=100.0, stop_price=100.0)]
    assert _stop_ratios(cands) == ()


# --------------------------------------------------------------------------- #
# 2. 개수 맞춤 — 셀에서 읽는다
# --------------------------------------------------------------------------- #


def test_tasks_take_counts_and_geometry_from_the_cell() -> None:
    """과제는 실제 숏 개수·기하를 **셀에서** 읽는다(워커가 다시 세면 실제 팔과 갈라진다)."""
    tasks = build_null_tasks(
        [_cell()], start="2020-09-15", end="2026-07-22", fill_name="baseline", seeds=7
    )
    assert len(tasks) == 1
    task = tasks[0]
    counts = dict(task.counts)
    assert set(counts) == set(NULL_SEGMENTS)
    # 경계(500분) 앞 숏 1건 = IS · 뒤 숏 1건 + 재진입 숏 1건 = 따뜻한 OOS.
    assert counts[SEGMENT_IS] == 1
    assert counts[SEGMENT_OOS_WARM] == 2
    assert task.seeds == 7
    assert task.boundary_ms == _cell().boundary_ms
    ratios = dict(task.stop_ratios)
    assert all(r > 0 for r in ratios[SEGMENT_OOS_WARM])


def test_draw_count_never_exceeds_k() -> None:
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    drawn = draw_null_shorts(
        seed=3,
        k=5,
        stop_ratios=(0.01,),
        pool=tuple(range(100)),
        substeps=steps,
        htf_times=times,
        htf_closes=closes,
        params=_params(),
    )
    assert len(drawn) <= 5


def test_zero_count_draws_nothing() -> None:
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    assert (
        draw_null_shorts(
            seed=3,
            k=0,
            stop_ratios=(0.01,),
            pool=tuple(range(100)),
            substeps=steps,
            htf_times=times,
            htf_closes=closes,
            params=_params(),
        )
        == []
    )


def test_sample_indices_is_without_replacement_when_pool_allows() -> None:
    import random

    picked = _sample_indices(random.Random(0), tuple(range(50)), 10)
    assert len(picked) == 10 and len(set(picked)) == 10


def test_sample_indices_falls_back_to_replacement_on_small_pool() -> None:
    import random

    picked = _sample_indices(random.Random(0), (1, 2), 5)
    assert len(picked) == 5


# --------------------------------------------------------------------------- #
# 3. 렌즈가 실제·널에 동일 배선 (완료기준 4)
# --------------------------------------------------------------------------- #


def test_penetration_lens_reaches_the_null_simulator() -> None:
    """관통 벌점은 라벨이 아니라 **체결 여부**를 바꾼다 — 널에도 실제와 같은 자를 물린다.

    벌점이 극단적이면 서브스텝 종가에 건 지정가는 관통 요구를 못 채워 체결되지 않는다.
    `baseline`(0bp)에서는 같은 자리가 체결되므로, 두 렌즈가 실제로 갈린다는 뜻이다.
    """
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    kw = dict(start=10, stop_ratio=0.02, substeps=steps, htf_times=times, htf_closes=closes)
    assert _null_short_candidate(params=_params(penetration_bps=0.0), **kw) is not None  # type: ignore[arg-type]
    assert _null_short_candidate(params=_params(penetration_bps=50_000.0), **kw) is None  # type: ignore[arg-type]


def test_fill_preset_carries_penetration_into_params() -> None:
    assert harness.build_params(fill=harness.fill_preset("baseline")).fill_penetration_bps == 0.0
    assert harness.build_params(fill=harness.fill_preset("pen_5bp")).fill_penetration_bps == 5.0


# --------------------------------------------------------------------------- #
# 4. 널 북은 롱을 안 건드린다
# --------------------------------------------------------------------------- #


def test_replace_shorts_keeps_longs_bit_identical() -> None:
    cell = _cell()
    nulls = (_cand(PositionSide.SHORT, entry_time=600 * _MINUTE),)
    grafted = _replace_shorts(cell, SEGMENT_OOS_WARM, nulls)
    longs_before = [c for c in cell.candidates[SEGMENT_FULL] if c.side is PositionSide.LONG]
    longs_after = [c for c in grafted.candidates[SEGMENT_FULL] if c.side is PositionSide.LONG]
    assert longs_before == longs_after


def test_replace_shorts_swaps_only_shorts() -> None:
    cell = _cell()
    nulls = (_cand(PositionSide.SHORT, entry_time=600 * _MINUTE),)
    grafted = _replace_shorts(cell, SEGMENT_OOS_WARM, nulls)
    shorts = [c for c in grafted.candidates[SEGMENT_FULL] if c.side is PositionSide.SHORT]
    assert shorts == list(nulls)
    # 재진입 숏도 걷어낸다 — 널 `k`가 base + 재진입 숏의 합이라 두 번 세면 안 된다.
    assert all(c.side is not PositionSide.SHORT for c in grafted.reentry_candidates[SEGMENT_FULL])


def test_replace_shorts_for_warm_oos_touches_the_full_key() -> None:
    """북(`_segment_cells`)이 따뜻한 OOS에서 읽는 키는 `full`이다 — 다른 키를 바꾸면 널이
    라벨만 달고 **실제 숏으로 돈다**(조용한 실패)."""
    cell = _cell()
    grafted = _replace_shorts(cell, SEGMENT_OOS_WARM, ())
    assert not [c for c in grafted.candidates[SEGMENT_FULL] if c.side is PositionSide.SHORT]
    # `is` 키는 그대로여야 한다(구간별로 독립적으로 갈아끼운다).
    assert cell.candidates[SEGMENT_IS] == grafted.candidates[SEGMENT_IS]


def test_replace_shorts_for_is_touches_the_is_key() -> None:
    cell = _cell()
    grafted = _replace_shorts(cell, SEGMENT_IS, ())
    assert not [c for c in grafted.candidates[SEGMENT_IS] if c.side is PositionSide.SHORT]
    assert cell.candidates[SEGMENT_FULL] == grafted.candidates[SEGMENT_FULL]


def test_drop_shorts_clears_every_segment_and_reentry() -> None:
    grafted = _drop_shorts(_cell())
    for cands in grafted.candidates.values():
        assert all(c.side is not PositionSide.SHORT for c in cands)
    for cands in grafted.reentry_candidates.values():
        assert all(c.side is not PositionSide.SHORT for c in cands)
    # 롱은 남는다(기준선은 「숏 0개인 같은 북」이지 빈 북이 아니다).
    assert any(c.side is PositionSide.LONG for c in grafted.candidates[SEGMENT_FULL])


def test_graft_does_not_mutate_the_source_cell() -> None:
    cell = _cell()
    before = dict(cell.candidates)
    _replace_shorts(cell, SEGMENT_OOS_WARM, ())
    _drop_shorts(cell)
    assert cell.candidates == before


# --------------------------------------------------------------------------- #
# 5. 재현성
# --------------------------------------------------------------------------- #


def test_same_seed_reproduces_the_same_draw() -> None:
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    kw = dict(
        k=12,
        stop_ratios=(0.01, 0.02),
        pool=tuple(range(400)),
        substeps=steps,
        htf_times=times,
        htf_closes=closes,
        params=_params(),
    )
    a = draw_null_shorts(seed=5, **kw)  # type: ignore[arg-type]
    b = draw_null_shorts(seed=5, **kw)  # type: ignore[arg-type]
    assert [(c.entry_time, c.stop_price) for c in a] == [(c.entry_time, c.stop_price) for c in b]


def test_different_seed_changes_the_draw() -> None:
    steps = _substeps()
    times, closes = _htf_arrays(steps)
    kw = dict(
        k=12,
        stop_ratios=(0.01, 0.02),
        pool=tuple(range(400)),
        substeps=steps,
        htf_times=times,
        htf_closes=closes,
        params=_params(),
    )
    a = draw_null_shorts(seed=5, **kw)  # type: ignore[arg-type]
    b = draw_null_shorts(seed=6, **kw)  # type: ignore[arg-type]
    assert [c.entry_time for c in a] != [c.entry_time for c in b]


# --------------------------------------------------------------------------- #
# 6. 자·게이트·판정
# --------------------------------------------------------------------------- #


def _short_row(**over: object) -> ShortNullRow:
    base: dict[str, object] = dict(
        symbol="BTC/USDT:USDT",
        timeframe="4h",
        fill="baseline",
        segment=SEGMENT_OOS_WARM,
        seeds=SEEDS,
        buy_hold=-0.20,
        actual_candidates=40,
        actual_trades=30,
        actual_net_r=12.0,
        null_mean_trades=34.0,
        null_mean_net_r=3.0,
        null_median_net_r=3.0,
        null_min_net_r=-5.0,
        null_max_net_r=9.0,
        p_value=0.048,
        null_mean_per_trade_r=0.05,
        p_value_per_trade=0.048,
    )
    base.update(over)
    return ShortNullRow.model_validate(base)


def test_sample_gate_blocks_significance() -> None:
    """20거래 미만이면 p가 아무리 작아도 유의가 아니다(WAN-84 유효 기준)."""
    row = _short_row(actual_trades=MIN_TRADES_GATE - 1)
    assert not row.sample_ok
    assert not row.significant and not row.significant_per_trade


def test_significance_requires_beating_the_null_mean() -> None:
    """p가 작아도 실제가 널 평균보다 나쁘면 유의가 아니다."""
    assert not _short_row(actual_net_r=-1.0, p_value=0.048).significant


def test_sum_and_per_trade_rulers_are_independent() -> None:
    """거래 수 비대칭 통제 — 합은 유의인데 거래당은 아닐 수 있고 그 반대도 가능하다."""
    sum_only = _short_row(p_value=0.048, p_value_per_trade=0.9)
    assert sum_only.significant and not sum_only.significant_per_trade
    pt_only = _short_row(p_value=0.9, p_value_per_trade=0.048)
    assert not pt_only.significant and pt_only.significant_per_trade


def test_alpha_is_actual_minus_null_mean() -> None:
    assert _short_row(actual_net_r=12.0, null_mean_net_r=3.0).alpha_net_r == pytest.approx(9.0)


def test_per_trade_r_divides_by_trades_not_candidates() -> None:
    row = _short_row(actual_net_r=15.0, actual_trades=30, actual_candidates=40)
    assert row.actual_per_trade_r == pytest.approx(0.5)


def test_verdict_reads_beta_when_no_cell_is_significant() -> None:
    rows = [_short_row(p_value=0.9, p_value_per_trade=0.9, actual_net_r=1.0)]
    text = verdict(rows, [])
    assert "(b) 베타" in text
    assert "1.0" in text or "+1.0" in text


def test_verdict_reads_alpha_when_a_majority_is_significant() -> None:
    rows = [_short_row(symbol=f"S{i}/USDT:USDT") for i in range(3)]
    assert "(a) 알파" in verdict(rows, [])


def test_verdict_is_split_when_a_minority_is_significant() -> None:
    rows = [_short_row()] + [
        _short_row(symbol=f"S{i}/USDT:USDT", p_value=0.9, p_value_per_trade=0.9) for i in range(3)
    ]
    assert "(c) 갈린다" in verdict(rows, [])


def test_verdict_numbers_follow_the_rows() -> None:
    """판정 숫자는 행에서 계산된다 — 문장에 박으면 재실행 뒤 거짓말(WAN-164 관행)."""
    a = verdict([_short_row(actual_net_r=12.0)], [])
    b = verdict([_short_row(actual_net_r=99.0)], [])
    assert a != b


def test_verdict_flags_missing_pen_5bp_rows() -> None:
    assert "pen_5bp 행 없음" in verdict([_short_row()], [])


def test_verdict_without_eligible_cells_is_undecidable() -> None:
    assert "판정 불가" in verdict([_short_row(actual_trades=1)], [])


def test_median_is_the_middle_value() -> None:
    assert _median([3.0, 1.0, 2.0]) == pytest.approx(2.0)
    assert _median([4.0, 1.0, 2.0, 3.0]) == pytest.approx(2.5)
    assert _median([]) == 0.0


# --------------------------------------------------------------------------- #
# 7. 병합 · 프레임 왕복
# --------------------------------------------------------------------------- #


def _book_row(**over: object) -> BookNullRow:
    base: dict[str, object] = dict(
        scope="all",
        fill="baseline",
        segment=SEGMENT_OOS_WARM,
        exclude_symbol="",
        seeds=SEEDS,
        num_cells=9,
        long_only_return=1.5,
        long_only_mdd=0.14,
        long_only_return_over_mdd=10.7,
        actual_trades=300,
        actual_return=2.5,
        actual_mdd=0.20,
        actual_return_over_mdd=12.5,
        null_mean_trades=310.0,
        null_mean_return=1.9,
        null_mean_mdd=0.22,
        null_mean_return_over_mdd=8.6,
        null_median_return_over_mdd=8.4,
        p_return=0.19,
        p_return_over_mdd=0.24,
    )
    base.update(over)
    return BookNullRow.model_validate(base)


def test_single_tf_append_does_not_overwrite_the_all_scope() -> None:
    """단일 TF `--append`가 `all` 라벨을 조용히 덮으면 안 된다(WAN-283 PM 교정과 같은 규칙)."""
    existing = [_book_row(scope="all"), _book_row(scope="4h")]
    merged = merge_book(existing, [_book_row(scope="15m")])
    scopes = sorted(r.scope for r in merged)
    assert scopes == ["15m", "4h", "all"]


def test_merge_book_replaces_same_scope_and_lens_only() -> None:
    existing = [_book_row(scope="4h", fill="baseline"), _book_row(scope="4h", fill="pen_5bp")]
    merged = merge_book(existing, [_book_row(scope="4h", fill="baseline", actual_return=9.9)])
    assert len(merged) == 2
    base = next(r for r in merged if r.fill == "baseline")
    assert base.actual_return == pytest.approx(9.9)
    assert any(r.fill == "pen_5bp" for r in merged)


def test_merge_short_keeps_other_lenses_and_timeframes() -> None:
    existing = [
        _short_row(timeframe="4h", fill="baseline"),
        _short_row(timeframe="4h", fill="pen_5bp"),
        _short_row(timeframe="1h", fill="baseline"),
    ]
    merged = merge_short(existing, [_short_row(timeframe="4h", fill="baseline", actual_net_r=1.0)])
    assert len(merged) == 3
    assert any(r.timeframe == "1h" for r in merged)
    assert any(r.fill == "pen_5bp" for r in merged)


def test_short_frame_round_trip_preserves_values(tmp_path: Path) -> None:
    rows = [_short_row(), _short_row(symbol="ETH/USDT:USDT", p_value=None, p_value_per_trade=None)]
    path = tmp_path / "s.csv"
    short_to_frame(rows).to_csv(path, index=False)
    back = short_from_csv(path)
    assert [r.model_dump() for r in back] == [r.model_dump() for r in rows]


def test_book_frame_round_trip_preserves_none(tmp_path: Path) -> None:
    rows = [_book_row(), _book_row(scope="4h", p_return_over_mdd=None, actual_return_over_mdd=None)]
    path = tmp_path / "b.csv"
    book_to_frame(rows).to_csv(path, index=False)
    back = book_from_csv(path)
    assert [r.model_dump() for r in back] == [r.model_dump() for r in rows]


# --------------------------------------------------------------------------- #
# 8. 좌표·성격 (기본값·토대 불변)
# --------------------------------------------------------------------------- #


def test_engine_fingerprint_shows_adopted_defaults() -> None:
    """산출물만 봐도 어떤 엔진인지 드러나야 한다(핀 없음 = 오늘의 채택 기본값)."""
    text = describe_engine()
    assert "max_zone_width_atr=1.28" in text
    assert "band_bar=intrabar_live" in text
    assert "short_enabled=False(기본)" in text  # 측정용 숏이지 재활성화 아님


def test_short_enabled_default_is_still_false() -> None:
    """이 이슈는 측정 전용 — 숏 기본값을 건드리지 않는다."""
    assert ConfluenceParams().short_enabled is False


def test_loo_axis_covers_the_issue_symbols() -> None:
    from backtest.wan284_resistance_short_profit_null import LOO_SYMBOLS

    assert set(LOO_SYMBOLS) == {"ETH", "DOGE", "LINK"}
    assert LOO_ALL == "ETH+DOGE+LINK"


def test_summary_renders_both_rulers_and_the_warnings() -> None:
    text = build_summary_markdown(
        [_short_row()], [_book_row()], short_csv=Path("s.csv"), book_csv=Path("b.csv")
    )
    assert "p(거래당)" in text  # 거래 수 비대칭 통제 자
    assert "베타" in text and "알파" in text
    assert "엣지 없음" in text  # 옛 판정 불변 경고
    assert "ALPHABLOCK_LIVE_TRADING=false" in text
    assert "복리 착시" in text


def test_null_segments_are_the_canonical_two() -> None:
    """널은 WAN-166 정본 구간에서만 돈다(oos_warm 주 · is 맥락) — wan231 선례."""
    assert NULL_SEGMENTS == (SEGMENT_IS, SEGMENT_OOS_WARM)


def test_cellpayload_graft_is_a_dataclass_replace() -> None:
    """그래프팅이 `dataclasses.replace`라 새 필드가 생겨도 조용히 유실되지 않는다."""
    assert dataclasses.is_dataclass(CellPayload)
    grafted = _replace_shorts(_cell(), SEGMENT_IS, ())
    assert grafted.symbol == _cell().symbol
    assert grafted.boundary_ms == _cell().boundary_ms
    assert grafted.funding == _cell().funding


# --------------------------------------------------------------------------- #
# 9. 검산 — 실제 팔 ≡ WAN-282 §3 (완료기준 4)
# --------------------------------------------------------------------------- #


def _diag_csv(tmp_path: Path, *, net_r: float, trades: int) -> Path:
    path = tmp_path / "diag.csv"
    pd.DataFrame(
        [
            {
                "symbol": "BTC/USDT:USDT",
                "timeframe": "4h",
                "fill": "baseline",
                "segment": SEGMENT_OOS_WARM,
                "short_trades": trades,
                "net_r_sum": net_r,
            }
        ]
    ).to_csv(path, index=False)
    return path


def test_crosscheck_reports_bit_match(tmp_path: Path) -> None:
    rows = [_short_row(actual_net_r=12.0, actual_trades=30)]
    text, worst, compared = crosscheck_wan282(rows, _diag_csv(tmp_path, net_r=12.0, trades=30))
    assert "✅" in text and compared == 1 and worst == 0.0


def test_crosscheck_flags_a_mismatch(tmp_path: Path) -> None:
    """실제 팔이 WAN-282와 갈라지면 **조용히 통과하지 않는다**(WAN-91/95/112 부류 방지)."""
    rows = [_short_row(actual_net_r=12.0, actual_trades=30)]
    text, _worst, compared = crosscheck_wan282(rows, _diag_csv(tmp_path, net_r=99.0, trades=30))
    assert "🚨" in text and compared == 1


def test_crosscheck_flags_a_trade_count_mismatch(tmp_path: Path) -> None:
    rows = [_short_row(actual_net_r=12.0, actual_trades=30)]
    text, _worst, _n = crosscheck_wan282(rows, _diag_csv(tmp_path, net_r=12.0, trades=31))
    assert "🚨" in text


def test_crosscheck_distinguishes_float_noise_from_a_match(tmp_path: Path) -> None:
    """일치 · 잡음 · 불일치를 다르게 찍는다(WAN-151 패턴)."""
    rows = [_short_row(actual_net_r=12.0 + 1e-12, actual_trades=30)]
    text, worst, _n = crosscheck_wan282(rows, _diag_csv(tmp_path, net_r=12.0, trades=30))
    assert "✅" in text and "부동소수 잡음" in text and 0.0 < worst <= CROSSCHECK_TOL


def test_crosscheck_says_so_when_there_is_nothing_to_compare(tmp_path: Path) -> None:
    text, _worst, compared = crosscheck_wan282(
        [_short_row(timeframe="1h")], _diag_csv(tmp_path, net_r=12.0, trades=30)
    )
    assert "검산 불가" in text and compared == 0


def test_crosscheck_says_so_when_the_reference_csv_is_absent(tmp_path: Path) -> None:
    text, _worst, compared = crosscheck_wan282([_short_row()], tmp_path / "missing.csv")
    assert "검산 불가" in text and compared == 0


def test_summary_includes_the_crosscheck_section() -> None:
    text = build_summary_markdown(
        [_short_row()], [_book_row()], short_csv=Path("s.csv"), book_csv=Path("b.csv")
    )
    assert "검산" in text and "WAN-282" in text
