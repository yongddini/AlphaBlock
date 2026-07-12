"""data.gaps 테스트 — 순수 갭 탐지 로직 (WAN-35)."""

from __future__ import annotations

import pytest

from data.gaps import Gap, find_gaps, total_missing

TF_MS = 3_600_000  # 1h
T0 = 1_700_000_000_000


def _series(count: int, *, start: int = T0, step: int = TF_MS) -> list[int]:
    return [start + i * step for i in range(count)]


def test_no_gaps_returns_empty() -> None:
    assert find_gaps(_series(10), "1h") == []


def test_single_and_empty_series_has_no_gaps() -> None:
    assert find_gaps([], "1h") == []
    assert find_gaps([T0], "1h") == []


def test_detects_single_missing_bar() -> None:
    # T0, (누락 T0+1h), T0+2h
    timestamps = [T0, T0 + 2 * TF_MS]
    gaps = find_gaps(timestamps, "1h")
    assert gaps == [Gap(start_ms=T0 + TF_MS, end_ms=T0 + TF_MS, missing=1)]


def test_detects_multi_bar_gap() -> None:
    # T0, (누락 3봉), T0+4h
    timestamps = [T0, T0 + 4 * TF_MS]
    gaps = find_gaps(timestamps, "1h")
    assert gaps == [Gap(start_ms=T0 + TF_MS, end_ms=T0 + 3 * TF_MS, missing=3)]
    assert total_missing(gaps) == 3


def test_detects_multiple_gaps() -> None:
    # 0,1 [누락2] 3 [누락4,5] 6
    timestamps = [T0, T0 + TF_MS, T0 + 3 * TF_MS, T0 + 6 * TF_MS]
    gaps = find_gaps(timestamps, "1h")
    assert gaps == [
        Gap(start_ms=T0 + 2 * TF_MS, end_ms=T0 + 2 * TF_MS, missing=1),
        Gap(start_ms=T0 + 4 * TF_MS, end_ms=T0 + 5 * TF_MS, missing=2),
    ]
    assert total_missing(gaps) == 3


def test_ignores_pre_listing_and_forming_bar() -> None:
    """첫 봉 이전(상장 전)·마지막 봉 이후(형성 중)는 갭이 아니다.

    저장 구간은 [T0, T0+2h]로 연속이며 그 바깥은 보지 않는다.
    """
    timestamps = _series(3)  # T0, T0+1h, T0+2h — 내부 갭 없음
    assert find_gaps(timestamps, "1h") == []


def test_defensive_against_unsorted_and_duplicates() -> None:
    timestamps = [T0 + 2 * TF_MS, T0, T0, T0 + 2 * TF_MS]  # 역순 + 중복, 1h 하나 누락
    gaps = find_gaps(timestamps, "1h")
    assert gaps == [Gap(start_ms=T0 + TF_MS, end_ms=T0 + TF_MS, missing=1)]


def test_respects_timeframe() -> None:
    # 1d 간격 데이터에서 하루 누락
    day = 86_400_000
    timestamps = [T0, T0 + 2 * day]
    gaps = find_gaps(timestamps, "1d")
    assert gaps == [Gap(start_ms=T0 + day, end_ms=T0 + day, missing=1)]


def test_unsupported_timeframe_raises() -> None:
    with pytest.raises(ValueError):
        find_gaps([T0], "7h")
