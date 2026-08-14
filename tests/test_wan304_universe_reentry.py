"""WAN-304 재진입 켠 유니버스 사다리의 자(尺)를 동작으로 고정한다.

지키는 것:

1. **팔 스위치가 실제로 갈린다** — 같은 payload 세트에서 off 팔은 base만 북에 넣어
   재진입 dict를 지운 payload와 **행 단위로 동일**하고(비트 재현 = 완료기준 2의 단위
   수준), band 팔은 재진입 후보를 실제로 더 배치한다(라벨만 붙는 실패 방지 — WAN-95/112
   부류).
2. **wan300 꺼짐 판 비트 대조** — off 팔·base 셀이 wan300 CSV와 다르면 RuntimeError로
   죽고(움직인 축이 재진입만이 아니라는 뜻), 2h·`all`(4TF) 행은 대조 대상이 아니다.
3. **CSV 왕복·병합** — 팔 축(`reentry`)이 키에 들어가 off/band가 서로 덮어쓰지 않는다.
4. **요약 렌더** — 두 팔 판정 병기 · Δ 헤드라인 · 경고(복리 착시 · band 낙관 · 측정 전용 ·
   wan300 `all`과 직접 비교 금지)가 나온다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import SEGMENT_FULL, SEGMENT_OOS_WARM
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload
from backtest.wan176_nine_symbol_rebaseline import NINE_SYMBOLS
from backtest.wan300_universe_size import REF_LENS, UniverseCellRow, build_rows_for_cells
from backtest.wan304_universe_reentry import (
    ARMS,
    DEFAULT_TIMEFRAMES,
    WAN300_CHECK_TFS,
    ReentryUniverseRow,
    arm_rows,
    build_summary_markdown,
    cross_check_wan300_cells,
    cross_check_wan300_grid,
    grid_from_csv,
    grid_to_frame,
    merge_grid,
)
from backtest.zone_limit_backtest import _Candidate

# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #


def _cand(entry_time: int) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=entry_time + 10,
        exit_price=101.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.0,
        trigger_time=entry_time,
    )


def _payload(symbol: str, *, idx: int = 0, with_reentry: bool = True) -> CellPayload:
    # 심볼마다 시각을 어긋나게(idx*20) 잡아 북 명목 상한(cap_only 5배)이 겹침을 깎지
    # 않게 한다 — 이 테스트의 축은 팔 스위치이지 용량이 아니다.
    base: tuple[_Candidate, ...] = (_cand(10 + idx * 20), _cand(2_000 + idx * 20))
    reentry: dict[str, tuple[_Candidate, ...]] = (
        {SEGMENT_FULL: (_cand(50 + idx * 20), _cand(3_000 + idx * 20))} if with_reentry else {}
    )
    return CellPayload(
        symbol=f"{symbol[:-4]}/USDT:USDT",
        timeframe="1h",
        boundary_ms=1_000,
        candidates={SEGMENT_FULL: base},
        funding={SEGMENT_FULL: ()},
        rows=(),
        reentry_candidates=reentry,
    )


def _row(
    *,
    reentry: str = "band",
    lens: str = REF_LENS,
    universe: int = 9,
    scope: str = "all",
    segment: str = SEGMENT_OOS_WARM,
    exclude: str = "",
    ret: float = 0.5,
    mdd: float = 0.10,
    trades: int = 100,
) -> ReentryUniverseRow:
    return ReentryUniverseRow(
        reentry=reentry,
        lens=lens,
        universe=universe,
        scope=scope,
        segment=segment,
        exclude_symbol=exclude,
        num_cells=universe,
        num_trades=trades,
        win_rate=0.5,
        total_return=ret,
        max_drawdown=mdd,
        peak_concurrency=3,
        max_concurrent_risk=0.05,
        max_open_notional_ratio=1.2,
        liquidation_events=0,
        clamped_entries=2,
        skipped_cell_busy=5,
        skipped_notional=0,
    )


# --------------------------------------------------------------------------- #
# 1. 팔 스위치
# --------------------------------------------------------------------------- #


def test_off_arm_is_bit_identical_to_reentry_free_payloads() -> None:
    """off 팔(include_reentry=False)은 재진입 dict를 아예 안 실은 payload와 행 단위로
    같아야 한다 — 완료기준 2(꺼짐 판 비트 재현)의 단위 수준 고정."""
    with_re = [_payload(s, idx=i) for i, s in enumerate(NINE_SYMBOLS)]
    without = [_payload(s, idx=i, with_reentry=False) for i, s in enumerate(NINE_SYMBOLS)]
    rows_off = build_rows_for_cells(with_re, lens=REF_LENS, sizes=(9,), include_reentry=False)
    rows_ref = build_rows_for_cells(without, lens=REF_LENS, sizes=(9,))
    assert rows_off == rows_ref


def test_band_arm_actually_places_reentry_trades() -> None:
    """band 팔은 재진입 후보를 실제로 더 배치한다(거래 수 증가) — 라벨만 붙는 실패 방지."""
    payloads = [_payload(s, idx=i) for i, s in enumerate(NINE_SYMBOLS)]
    off = build_rows_for_cells(payloads, lens=REF_LENS, sizes=(9,), include_reentry=False)
    band = build_rows_for_cells(payloads, lens=REF_LENS, sizes=(9,), include_reentry=True)
    off_full = next(r for r in off if r.segment == SEGMENT_FULL and not r.exclude_symbol)
    band_full = next(r for r in band if r.segment == SEGMENT_FULL and not r.exclude_symbol)
    assert band_full.num_trades > off_full.num_trades
    # 재진입은 칸당 2개씩 실렸다 — 전부 배치되면 base(2) + 재진입(2) = 4/칸.
    assert band_full.num_trades == off_full.num_trades * 2


def test_band_arm_oos_warm_filters_reentry_by_boundary() -> None:
    """oos_warm은 재진입도 칸 경계로 거른다(straddle (b)) — 경계 전 재진입은 배치조차 안 한다."""
    payloads = [_payload(s, idx=i) for i, s in enumerate(NINE_SYMBOLS)]
    band = build_rows_for_cells(payloads, lens=REF_LENS, sizes=(9,), include_reentry=True)
    warm = next(r for r in band if r.segment == SEGMENT_OOS_WARM and not r.exclude_symbol)
    # 경계(1,000) 이후 후보만: base 1(2,000) + 재진입 1(3,000) = 칸당 2.
    assert warm.num_trades == len(NINE_SYMBOLS) * 2


def test_arm_rows_filters_by_arm() -> None:
    rows = [_row(reentry="off"), _row(reentry="band", ret=0.9)]
    assert [r.total_return for r in arm_rows(rows, "band")] == [0.9]
    assert [r.total_return for r in arm_rows(rows, "off")] == [0.5]


def test_arms_and_timeframes_constants() -> None:
    assert ARMS == ("off", "band")
    assert DEFAULT_TIMEFRAMES == ("15m", "1h", "2h", "4h"), "WAN-252 채택 작업 TF 4축"
    assert "2h" not in WAN300_CHECK_TFS, "2h는 꺼짐 판에 없어 신규(대조 대상 아님)"


# --------------------------------------------------------------------------- #
# 2. wan300 꺼짐 판 비트 대조
# --------------------------------------------------------------------------- #


def _wan300_grid_csv(tmp_path: Path, *, total_return: float = 0.5) -> Path:
    row = _row(reentry="off", scope="1h", ret=total_return)
    frame = pd.DataFrame([{k: v for k, v in row.model_dump().items() if k != "reentry"}])
    path = tmp_path / "wan300_grid.csv"
    frame.to_csv(path, index=False)
    return path


def test_grid_cross_check_bit_match(tmp_path: Path) -> None:
    path = _wan300_grid_csv(tmp_path)
    lines = cross_check_wan300_grid([_row(reentry="off", scope="1h")], path)
    assert any("완료기준 2" in line for line in lines)


def test_grid_cross_check_mismatch_raises(tmp_path: Path) -> None:
    path = _wan300_grid_csv(tmp_path, total_return=0.6)
    with pytest.raises(RuntimeError, match="재진입"):
        cross_check_wan300_grid([_row(reentry="off", scope="1h")], path)


def test_grid_cross_check_skips_band_all_and_2h(tmp_path: Path) -> None:
    """band 팔·`all` 스코프·2h 행은 대조 대상이 아니다 — 겹침이 없으면 생략을 알린다."""
    path = _wan300_grid_csv(tmp_path, total_return=0.6)  # 값이 달라도 대조 자체가 안 걸려야 함
    rows = [
        _row(reentry="band", scope="1h", ret=0.9),
        _row(reentry="off", scope="all", ret=0.9),
        _row(reentry="off", scope="2h", ret=0.9),
    ]
    lines = cross_check_wan300_grid(rows, path)
    assert any("생략" in line for line in lines)


def test_float_noise_tolerance_is_relative() -> None:
    """복리 total_return(1e5+ 규모)에서 CSV 파서의 마지막 ulp는 「잡음」이지 불일치가
    아니다 — 절대 허용치였던 첫 판은 (19종목·1h·full)에서 실제로 죽었다(동작 고정)."""
    from backtest.wan304_universe_reentry import _GRID_FIELDS, _compare_numbers

    base = _row(reentry="off").model_dump()
    ref = dict(base)
    ref["total_return"] = 130_000.0
    near = dict(base)
    near["total_return"] = 130_000.0 + 1e-11  # 1 ulp급(상대 ~8e-17)
    assert _compare_numbers(ref, near, _GRID_FIELDS) == "noise"
    far = dict(base)
    far["total_return"] = 130_001.0
    assert _compare_numbers(ref, far, _GRID_FIELDS) == "total_return"


def test_grid_cross_check_missing_file_skips(tmp_path: Path) -> None:
    lines = cross_check_wan300_grid([_row(reentry="off", scope="1h")], tmp_path / "absent.csv")
    assert any("생략" in line for line in lines)


def _cell_row(*, timeframe: str = "1h", total_return: float = 0.1) -> UniverseCellRow:
    return UniverseCellRow(
        lens=REF_LENS,
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        segment=SEGMENT_FULL,
        num_candidates=10,
        num_trades=8,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.05,
    )


def _wan300_cells_csv(tmp_path: Path, *, total_return: float = 0.1) -> Path:
    frame = pd.DataFrame([_cell_row(total_return=total_return).model_dump()])
    path = tmp_path / "wan300_cells.csv"
    frame.to_csv(path, index=False)
    return path


def test_cells_cross_check_bit_match(tmp_path: Path) -> None:
    path = _wan300_cells_csv(tmp_path)
    lines = cross_check_wan300_cells([_cell_row()], path)
    assert any("base" in line for line in lines)


def test_cells_cross_check_mismatch_raises(tmp_path: Path) -> None:
    path = _wan300_cells_csv(tmp_path, total_return=0.2)
    with pytest.raises(RuntimeError, match="base"):
        cross_check_wan300_cells([_cell_row()], path)


def test_cells_cross_check_skips_2h(tmp_path: Path) -> None:
    path = _wan300_cells_csv(tmp_path, total_return=0.2)
    lines = cross_check_wan300_cells([_cell_row(timeframe="2h")], path)
    assert any("생략" in line for line in lines)


# --------------------------------------------------------------------------- #
# 3. CSV 왕복 · 병합 (팔 축)
# --------------------------------------------------------------------------- #


def test_grid_roundtrip(tmp_path: Path) -> None:
    rows = [_row(reentry="off"), _row(reentry="band", exclude="ETH")]
    path = tmp_path / "grid.csv"
    grid_to_frame(rows).to_csv(path, index=False)
    assert grid_from_csv(path) == rows


def test_merge_grid_key_includes_arm() -> None:
    """off/band 팔이 같은 좌표라도 서로 덮어쓰지 않는다 — 팔 축이 키에 들어간다."""
    merged = merge_grid([_row(reentry="off")], [_row(reentry="band")])
    assert len(merged) == 2
    overwritten = merge_grid(merged, [_row(reentry="band", ret=0.9)])
    assert len(overwritten) == 2
    assert arm_rows(overwritten, "band")[0].total_return == pytest.approx(0.9)


# --------------------------------------------------------------------------- #
# 4. 요약 렌더
# --------------------------------------------------------------------------- #


def test_summary_renders_key_sections() -> None:
    rows = [
        _row(reentry=arm, universe=size, ret=0.5 + size * 0.01 + (0.1 if arm == "band" else 0.0))
        for arm in ARMS
        for size in (9, 12, 15, 19)
    ] + [_row(reentry=arm, scope="1h", universe=size) for arm in ARMS for size in (9, 12, 15, 19)]
    md = build_summary_markdown(rows)
    assert "WAN-304" in md
    assert "band 팔" in md and "off" in md, "두 팔 판정 병기"
    assert "Δ 헤드라인" in md
    assert "직접 비교 금지" in md, "wan300 3TF all과의 비교 금지 경고"
    assert "복리 착시" in md
    assert "재-베이스라인 = 사용자 결정" in md
    assert "leave-one-out" in md
    assert "ΔMDD" in md
