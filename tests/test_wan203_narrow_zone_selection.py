"""WAN-203 좁은 존 선별 심화 — 절대 문턱 선별 · 단조성 판정 · 검산 축.

이 파일이 **동작으로** 고정하는 것:

1. **절대 문턱이 분위가 아니다** — 필터 팔이 `zone_width_atr ≤ 문턱`으로 골라지지, IS 분위
   (하위 1/3)로 골라지지 않는다. 둘을 헷갈리면 「1.28」이 라벨만 붙고 분위로 도는 조용한
   실패가 된다(WAN-91/95/112/159 계열).
2. **`abs_threshold=None`이면 예전 분위 경로와 같다** — WAN-152/154 CSV가 재현된다.
3. **단조성 판정 네 분기가 의도한 입력에서 나온다**(문장이 아니라 열거형 — WAN-142 교훈).
4. **매칭 대조군 크기 = 필터 크기** · 구간을 넘나들지 않는다(룩어헤드 금지).
5. **CSV 왕복이 문턱·렌즈 좌표를 보존한다**.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import ExitReason, PositionSide
from backtest.wan133_geometry_vs_selection import ARM_FILTER
from backtest.wan142_zone_width_filter_verdict import ARM_MATCHED, MATCH_SEEDS, SEED_AGGREGATE
from backtest.wan152_selection_vs_geometry import (
    BARRIER_ATR,
    BARRIER_ZONE,
    LENS_PRIMARY,
    GeoCell,
    PnlRow,
    is_threshold,
    pnl_rows_for_cell,
)
from backtest.wan203_narrow_zone_selection import (
    THRESHOLDS_ATR,
    VERDICT_BARRIER,
    SweepPnlRow,
    SweepResult,
    SweepTestRow,
    TrajectoryKind,
    _build_tests,
    _load_from_csv,
    _partition,
    gate_report,
    trajectory,
)
from backtest.zone_limit_backtest import _Candidate

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"
_HOUR_MS = 3_600_000


# --------------------------------------------------------------------------- #
# 손으로 만든 셀 (WAN-152 테스트 관행 — 합성 1h는 후보가 한둘뿐)
# --------------------------------------------------------------------------- #


def _cand(entry: int, *, win: bool, stop_price: float) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry,
        entry_price=100.0,
        exit_time=entry + _HOUR_MS // 2,
        exit_price=101.5 if win else 99.0,
        reason=ExitReason.TAKE_PROFIT if win else ExitReason.STOP_LOSS,
        stop_price=stop_price,
        trigger_time=entry,
    )


def _hand_made_cell(n: int = 60) -> tuple[GeoCell, MarketData]:
    """존폭(zwa)이 0..9로 순환하는 후보 n개. IS 2/3 · OOS 1/3."""
    t0 = 1_700_000_000_000
    zone: list[_Candidate] = []
    atr_arm: list[_Candidate] = []
    zwa: list[float | None] = []
    for i in range(n):
        entry = t0 + i * _HOUR_MS
        win = bool(i % 2)
        zone.append(_cand(entry, win=win, stop_price=99.0))
        atr_arm.append(_cand(entry, win=win, stop_price=98.0))
        zwa.append(float(i % 10))
    cell = GeoCell(symbol=_SYMBOL, timeframe=_TIMEFRAME)
    cell.by_barrier = {BARRIER_ZONE: zone, BARRIER_ATR: atr_arm}
    cell.zwa = zwa
    cell.is_boundary = t0 + (n * 2 // 3) * _HOUR_MS
    return cell, MarketData(_SYMBOL, _TIMEFRAME, pd.DataFrame(), pd.DataFrame(), [])


def _seg_of(cell: GeoCell, i: int, segment: str) -> bool:
    return (cell.by_barrier[BARRIER_ZONE][i].trigger_time < cell.is_boundary) == (
        segment == SEGMENT_IS
    )


def _filter_row(rows: list[PnlRow], *, barrier: str, segment: str) -> PnlRow:
    """한 (장벽, 구간)의 필터 팔 행을 집어낸다."""
    return next(
        r for r in rows if r.barrier == barrier and r.segment == segment and r.arm == ARM_FILTER
    )


# --------------------------------------------------------------------------- #
# 1·2. 절대 문턱이 분위가 아니다
# --------------------------------------------------------------------------- #


def test_abs_threshold_selects_by_absolute_cut_not_quantile() -> None:
    """필터 팔 크기 = 그 구간에서 `zwa ≤ 문턱`인 셋업 수(분위가 아니라 절대 컷)."""
    cell, market = _hand_made_cell()
    threshold = 3.0
    rows = pnl_rows_for_cell(cell, market, abs_threshold=threshold)
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        expected = sum(
            1
            for i, z in enumerate(cell.zwa)
            if z is not None and z <= threshold and _seg_of(cell, i, segment)
        )
        filt = _filter_row(rows, barrier=BARRIER_ZONE, segment=segment)
        assert filt.num_candidates == expected
        assert expected > 0  # 공허하게 참(0==0) 방지.


def test_abs_threshold_differs_from_quantile_selection() -> None:
    """같은 셀에서 절대 문턱과 분위 문턱이 다른 필터 크기를 낸다(둘이 동의어가 아니다)."""
    cell, market = _hand_made_cell()
    # 분위 하위 1/3 문턱값과 다르도록 넉넉히 큰 절대 문턱을 고른다.
    quantile_val = is_threshold(cell)
    assert quantile_val is not None
    abs_rows = pnl_rows_for_cell(cell, market, abs_threshold=9.0)
    quant_rows = pnl_rows_for_cell(cell, market)
    abs_filt = _filter_row(abs_rows, barrier=BARRIER_ZONE, segment=SEGMENT_OOS)
    quant_filt = _filter_row(quant_rows, barrier=BARRIER_ZONE, segment=SEGMENT_OOS)
    assert abs_filt.num_candidates != quant_filt.num_candidates


def test_abs_threshold_none_reproduces_quantile_path() -> None:
    """`abs_threshold=None`이면 분위 경로와 비트 단위로 같다(WAN-152/154 재현)."""
    cell, market = _hand_made_cell()
    a = pnl_rows_for_cell(cell, market)
    b = pnl_rows_for_cell(cell, market, abs_threshold=None)
    assert [r.model_dump() for r in a] == [r.model_dump() for r in b]


def test_matched_arm_matches_filter_count_under_abs_threshold() -> None:
    """매칭 대조군 크기 = 필터 크기(절대 문턱)이고 시드 수가 맞다."""
    cell, market = _hand_made_cell()
    rows = pnl_rows_for_cell(cell, market, abs_threshold=4.0)
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        seg = [r for r in rows if r.barrier == BARRIER_ZONE and r.segment == segment]
        filt = next(r for r in seg if r.arm == ARM_FILTER)
        matched = [r for r in seg if r.arm == ARM_MATCHED and r.seed != SEED_AGGREGATE]
        assert len(matched) == len(MATCH_SEEDS)
        assert {r.num_candidates for r in matched} == {filt.num_candidates}


# --------------------------------------------------------------------------- #
# 3. 단조성 판정 네 분기
# --------------------------------------------------------------------------- #


def _test_row(
    threshold: float,
    *,
    margin: float,
    p: float,
    n_symbols: int = 6,
    barrier: str = VERDICT_BARRIER,
    lens: str = LENS_PRIMARY,
    timeframe: str = _TIMEFRAME,
    segment: str = SEGMENT_OOS,
) -> SweepTestRow:
    return SweepTestRow(
        threshold_atr=threshold,
        lens=lens,
        barrier=barrier,
        timeframe=timeframe,
        segment=segment,
        n_symbols=n_symbols,
        n_seeds=len(MATCH_SEEDS),
        filter_return=margin,
        matched_return_mean=0.0,
        margin_return=margin,
        p_return=p,
        filter_win_rate=0.55,
        matched_win_rate_mean=0.47,
        margin_win_rate=0.08,
        p_win_rate=p,
        filter_mdd=0.1,
        matched_mdd_mean=0.12,
        p_mdd=0.2,
        filter_trades=50.0,
        matched_trades=50.0,
        trade_gap_pct=0.0,
    )


def test_trajectory_strengthens_when_margin_rises_monotonically() -> None:
    """조일수록 마진이 단조 증가하고 유의하면 STRENGTHENS."""
    margins = {1.6: 0.00, 1.28: 0.02, 1.0: 0.04, 0.8: 0.06, 0.6: 0.08}
    ps = {1.6: 0.5, 1.28: 0.10, 1.0: 0.048, 0.8: 0.048, 0.6: 0.048}
    tests = [_test_row(t, margin=margins[t], p=ps[t]) for t in THRESHOLDS_ATR]
    got = trajectory(tests, timeframe=_TIMEFRAME)
    assert got.kind is TrajectoryKind.STRENGTHENS


def test_trajectory_flat_when_margin_peaks_then_falls() -> None:
    """1.28 근처에서 정점 후 감소하면 FLAT_OR_REVERSAL."""
    margins = {1.6: 0.00, 1.28: 0.06, 1.0: 0.05, 0.8: 0.04, 0.6: 0.03}
    tests = [_test_row(t, margin=margins[t], p=0.048) for t in THRESHOLDS_ATR]
    got = trajectory(tests, timeframe=_TIMEFRAME)
    assert got.kind is TrajectoryKind.FLAT_OR_REVERSAL


def test_trajectory_no_edge_when_never_significant() -> None:
    """어느 문턱도 유의하지 않으면 NO_EDGE."""
    tests = [_test_row(t, margin=0.05, p=0.5) for t in THRESHOLDS_ATR]
    got = trajectory(tests, timeframe=_TIMEFRAME)
    assert got.kind is TrajectoryKind.NO_EDGE


def test_trajectory_indeterminate_when_samples_collapse() -> None:
    """모든 문턱의 유효 심볼이 3 미만이면 판정 불가."""
    tests = [_test_row(t, margin=0.05, p=0.048, n_symbols=2) for t in THRESHOLDS_ATR]
    got = trajectory(tests, timeframe=_TIMEFRAME)
    assert got.kind is TrajectoryKind.INDETERMINATE


# --------------------------------------------------------------------------- #
# 4. 4관문 · 마진 · CSV 왕복
# --------------------------------------------------------------------------- #


def test_sweep_test_row_margin_is_filter_minus_matched() -> None:
    from backtest.wan152_selection_vs_geometry import MatchedTestRow

    t = MatchedTestRow(
        barrier=BARRIER_ATR,
        timeframe=_TIMEFRAME,
        segment=SEGMENT_OOS,
        n_symbols=6,
        n_seeds=20,
        filter_return=0.10,
        matched_return_mean=0.03,
        p_return=0.048,
        filter_win_rate=0.55,
        matched_win_rate_mean=0.47,
        p_win_rate=0.048,
        filter_mdd=0.1,
        matched_mdd_mean=0.12,
        p_mdd=0.2,
        default_mdd=0.11,
        filter_trades=50.0,
        matched_trades=52.0,
        trade_gap_pct=4.0,
    )
    row = SweepTestRow.build(t, threshold_atr=1.28, lens=LENS_PRIMARY)
    assert row.margin_return == pytest.approx(0.07)
    assert row.margin_win_rate == pytest.approx(0.08)


def test_gate_report_reads_four_gates() -> None:
    """4관문이 baseline/pen 검정에서 올바로 읽힌다."""
    base = _test_row(1.28, margin=0.05, p=0.048, lens=LENS_PRIMARY)
    from backtest.wan154_stop_width_audit import LENS_PEN

    pen = _test_row(1.28, margin=0.03, p=0.20, lens=LENS_PEN)
    # 필터 팔 pnl 행(ETH LOO용) — atr 장벽 OOS, 유효 표본.
    pnl = [
        SweepPnlRow(
            threshold_atr=1.28,
            lens=LENS_PRIMARY,
            inner=PnlRow(
                barrier=BARRIER_ATR,
                symbol=sym,
                timeframe=_TIMEFRAME,
                segment=SEGMENT_OOS,
                arm=ARM_FILTER,
                seed=SEED_AGGREGATE,
                num_candidates=50.0,
                num_trades=50.0,
                total_return=0.1,
                max_drawdown=0.1,
                win_rate=0.55,
            ),
        )
        for sym in ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
    ]
    result = SweepResult(pnl_rows=pnl, test_rows=[base, pen])
    g = gate_report(result, threshold=1.28, timeframe=_TIMEFRAME)
    assert g.gate1_matched_null is True  # base p=0.048 ≤ 0.05
    assert g.gate2_oos is True  # margin > 0
    assert g.gate3_pen_survives is False  # pen p=0.20 > 0.05
    assert "−ETH" in g.gate4_eth_loo


def test_csv_round_trip_preserves_threshold_and_lens(tmp_path: Path) -> None:
    """pnl CSV 왕복이 문턱·렌즈 좌표를 보존한다."""
    from backtest.wan203_narrow_zone_selection import _pnl_frame

    cell, market = _hand_made_cell()
    rows = [
        SweepPnlRow(threshold_atr=t, lens=LENS_PRIMARY, inner=pr)
        for t in (1.28, 0.6)
        for pr in pnl_rows_for_cell(cell, market, abs_threshold=t)
    ]
    path = tmp_path / "pnl.csv"
    _pnl_frame(rows).to_csv(path, index=False)
    loaded = _load_from_csv(path)
    got = {(r.threshold_atr, r.lens) for r in loaded.pnl_rows}
    assert (1.28, LENS_PRIMARY) in got
    assert (0.6, LENS_PRIMARY) in got
    # 문턱별 필터 크기가 보존된다.
    part_tight = _partition(loaded.pnl_rows, threshold=0.6, lens=LENS_PRIMARY)
    part_loose = _partition(loaded.pnl_rows, threshold=1.28, lens=LENS_PRIMARY)
    tight_filt = _filter_row(part_tight, barrier=BARRIER_ZONE, segment=SEGMENT_OOS)
    loose_filt = _filter_row(part_loose, barrier=BARRIER_ZONE, segment=SEGMENT_OOS)
    assert tight_filt.num_candidates <= loose_filt.num_candidates


def test_build_tests_partitions_by_threshold() -> None:
    """`_build_tests`가 문턱별로 매칭 검정을 낸다(문턱이 섞이지 않는다)."""
    cell, market = _hand_made_cell()
    rows = [
        SweepPnlRow(threshold_atr=t, lens=LENS_PRIMARY, inner=pr)
        for t in (1.28, 0.6)
        for pr in pnl_rows_for_cell(cell, market, abs_threshold=t)
    ]
    tests = _build_tests(rows, timeframes=[_TIMEFRAME])
    thresholds = {t.threshold_atr for t in tests}
    assert thresholds == {1.28, 0.6}
