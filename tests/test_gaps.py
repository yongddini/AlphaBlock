"""data.gaps 테스트 — 순수 갭 탐지 로직 (WAN-35)."""

from __future__ import annotations

import pytest

from data.gaps import Gap, find_gaps, gaps_from_boundaries, total_missing

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


# --- 경계 쌍 입구 (WAN-187) ---------------------------------------------------


def test_gaps_from_boundaries_matches_find_gaps() -> None:
    """같은 시리즈를 「시각열 전부」와 「벌어진 쌍만」으로 넣으면 답이 같다."""
    timestamps = [T0 + i * TF_MS for i in (0, 1, 2, 6, 7, 12, 13)]
    boundaries = [
        (prev, cur)
        for prev, cur in zip(timestamps, timestamps[1:], strict=False)
        if cur - prev > TF_MS
    ]
    assert gaps_from_boundaries(boundaries, "1h") == find_gaps(timestamps, "1h")
    assert gaps_from_boundaries(boundaries, "1h") == [
        Gap(start_ms=T0 + 3 * TF_MS, end_ms=T0 + 5 * TF_MS, missing=3),
        Gap(start_ms=T0 + 8 * TF_MS, end_ms=T0 + 11 * TF_MS, missing=4),
    ]


def test_gaps_from_boundaries_tolerates_a_loose_filter() -> None:
    """간격이 TF 이하인 쌍이 섞여 와도 `find_gaps`와 똑같이 무시한다.

    저장소가 거는 필터가 느슨해져도(또는 TF 정렬이 안 된 데이터라도) 답이 달라지지
    않아야 두 입구가 하나의 계산부를 공유한다는 말이 성립한다.
    """
    pairs = [
        (T0, T0 + TF_MS),  # 정상 인접 — 갭 아님
        (T0 + TF_MS, T0 + TF_MS),  # 동일 봉 — 갭 아님
        (T0 + 2 * TF_MS, T0 + 2 * TF_MS + TF_MS // 2),  # TF 미만 간격
        (T0 + 3 * TF_MS, T0 + 5 * TF_MS),  # 진짜 갭 1개
    ]
    assert gaps_from_boundaries(pairs, "1h") == [
        Gap(start_ms=T0 + 4 * TF_MS, end_ms=T0 + 4 * TF_MS, missing=1)
    ]


def test_gaps_from_boundaries_unsupported_timeframe_raises() -> None:
    with pytest.raises(ValueError):
        gaps_from_boundaries([(T0, T0 + TF_MS)], "7h")
