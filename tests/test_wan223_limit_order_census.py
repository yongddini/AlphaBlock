"""WAN-223 §1 census의 자(尺)를 동작으로 고정한다.

백테스트를 돌리지 않는다 — 동시대기 스위프라인·판정·CSV 왕복·파생 속성은 전부 순수
함수·모델이라 합성 값으로 검증한다. 여기서 고정하는 함정들:

1. **동시대기 반개구간** — 대기 구간 `[trigger, trigger+horizon)`이 맞닿으면 겹침이 아니다.
2. **핵심 지표 = filled_wait − filled_base** — 만료로 놓친 재진입이 음수가 되지 않는 불변.
3. **판정 두 자** — 놓친 재진입 비율 **그리고** 수익 델타가 GO/STOP을 함께 가른다
   (문턱이 문장이 아니라 코드 상수 · (a)/(b)/(c)가 실제로 갈린다).
4. **진입 빈도·CSV 왕복** — 파생 속성과 프레임 왕복이 값을 보존한다.
"""

from __future__ import annotations

from pathlib import Path

from backtest.wan223_limit_order_census import (
    MATERIAL_RETURN_DELTA_PCT,
    NEGLIGIBLE_MISS_SHARE,
    SIGNIFICANT_MISS_SHARE,
    CellRow,
    aggregate_symbol_mean,
    cells_from_csv,
    cells_to_frame,
    peak_and_mean_concurrent,
    verdict,
)

WINDOW = (0, 1_000)


def _cell(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    eligible: int = 100,
    filled_base: int = 60,
    trades_base: int = 40,
    filled_wait: int = 68,
    trades_wait: int = 45,
    total_return_base: float = 10.0,
    total_return_wait: float = 12.0,
) -> CellRow:
    return CellRow(
        symbol=symbol,
        timeframe=timeframe,
        segment="full",
        window_start=WINDOW[0],
        window_end=WINDOW[1],
        window_days=100.0,
        eligible=eligible,
        filled_base=filled_base,
        trades_base=trades_base,
        no_touch=0,
        expired=filled_wait - filled_base,
        invalidated=eligible - filled_base - (filled_wait - filled_base),
        cond_failed=0,
        filled_wait=filled_wait,
        trades_wait=trades_wait,
        missed_reentries=filled_wait - filled_base,
        slot_busy_skips=filled_base - trades_base,
        peak_concurrent_waiting=3,
        mean_concurrent_waiting=1.2,
        total_return_base=total_return_base,
        total_return_wait=total_return_wait,
        mdd_base=5.0,
        mdd_wait=6.0,
        funding_coverage=1.0,
    )


# --------------------------------------------------------------------------- #
# 동시대기 스위프라인
# --------------------------------------------------------------------------- #


def test_concurrent_half_open_no_overlap_when_touching() -> None:
    # [0,100)과 [100,200)은 맞닿을 뿐 겹치지 않는다 → peak 1.
    peak, mean = peak_and_mean_concurrent(
        [0, 100], horizon_ms=100, window_start=0, window_end=1_000
    )
    assert peak == 1
    # 각 구간 100ms씩 = 200ms가 수준 1 → 평균 = 200/1000 = 0.2.
    assert mean == 200 / 1000


def test_concurrent_stacks_when_overlapping() -> None:
    # 세 트리거가 50ms 간격, 지평 100ms → 최대 2~3 동시.
    peak, _ = peak_and_mean_concurrent(
        [0, 40, 80], horizon_ms=100, window_start=0, window_end=1_000
    )
    assert peak == 3  # [0,100)·[40,140)·[80,180)이 [80,100)에서 셋 다 겹침


def test_concurrent_clips_to_window() -> None:
    # 창 밖으로 나가는 대기 구간은 잘린다.
    peak, mean = peak_and_mean_concurrent([900], horizon_ms=1_000, window_start=0, window_end=1_000)
    assert peak == 1
    assert mean == 100 / 1000  # [900,1000)만 창 안


def test_concurrent_empty() -> None:
    assert peak_and_mean_concurrent([], horizon_ms=100, window_start=0, window_end=10) == (0, 0.0)


# --------------------------------------------------------------------------- #
# 판정 — 두 자가 함께 가른다
# --------------------------------------------------------------------------- #


def test_verdict_go_needs_both_big_miss_and_return() -> None:
    # 놓친 재진입이 진입의 50%(20/40)이고 수익 델타 +5%p → (a) GO.
    rows = [_cell(trades_base=40, filled_base=60, filled_wait=80, total_return_wait=15.0)]
    text = verdict(rows)
    assert "(a) GO" in text


def test_verdict_stop_when_miss_rare() -> None:
    # 놓친 재진입 1/40 = 2.5%로 문턱 미만 → (b) STOP(수익 델타가 커도).
    rows = [_cell(trades_base=40, filled_base=60, filled_wait=61, total_return_wait=20.0)]
    text = verdict(rows)
    assert "(b) STOP" in text


def test_verdict_stop_when_return_immaterial_despite_big_miss() -> None:
    # 놓친 재진입은 큰데(50%) 수익 델타가 0.1%p뿐 → (b) STOP.
    rows = [
        _cell(
            trades_base=40,
            filled_base=60,
            filled_wait=80,
            total_return_base=10.0,
            total_return_wait=10.1,
        )
    ]
    text = verdict(rows)
    assert "(b) STOP" in text


def test_verdict_borderline() -> None:
    # 놓친 재진입 비율이 STOP과 GO 사이(약 12.5%)이고 수익 델타는 유의(+3%p) → (c).
    share = (SIGNIFICANT_MISS_SHARE + NEGLIGIBLE_MISS_SHARE) / 2
    trades = 40
    miss = round(trades * share)
    rows = [
        _cell(
            trades_base=trades,
            filled_base=60,
            filled_wait=60 + miss,
            total_return_base=10.0,
            total_return_wait=10.0 + MATERIAL_RETURN_DELTA_PCT + 2.0,
        )
    ]
    text = verdict(rows)
    assert "(c) 경계" in text


# --------------------------------------------------------------------------- #
# 파생 속성 · 집계 · CSV 왕복
# --------------------------------------------------------------------------- #


def test_days_per_entry_and_missed_share() -> None:
    row = _cell(trades_base=50, filled_base=60, filled_wait=70)
    assert row.window_days == 100.0
    assert row.days_per_entry == 2.0  # 100일 / 50진입
    assert row.missed_share == 10 / 50


def test_derived_none_when_zero_entries() -> None:
    row = _cell(trades_base=0, filled_base=0, filled_wait=0)
    assert row.days_per_entry is None
    assert row.missed_share is None


def test_aggregate_symbol_mean() -> None:
    rows = [_cell(total_return_base=10.0), _cell(total_return_base=20.0)]
    assert aggregate_symbol_mean(rows, "total_return_base") == 15.0
    assert aggregate_symbol_mean([], "total_return_base") == 0.0


def test_csv_roundtrip(tmp_path: Path) -> None:
    rows = [_cell(symbol="BTC/USDT:USDT"), _cell(symbol="ETH/USDT:USDT", timeframe="4h")]
    path = tmp_path / "cells.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    restored = cells_from_csv(path)
    assert [r.model_dump() for r in restored] == [r.model_dump() for r in rows]
