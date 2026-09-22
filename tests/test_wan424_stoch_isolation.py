"""WAN-424 §3: 하한 필터 정렬·부분집합·%K 선별 인덱스를 동작으로 고정한다."""

from __future__ import annotations

from backtest import wan424_stoch_isolation as iso
from backtest.models import ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate


def _cell() -> iso.IsoCell:
    # 손절폭 = (진입 − 손절)/진입: 5%, 3%, 1%, 4.5%, 2% ...
    specs = [
        (0, 100.0, 95.0),  # 5%
        (100, 100.0, 97.0),  # 3%
        (200, 100.0, 99.0),  # 1%
        (300, 100.0, 95.5),  # 4.5%
        (400, 100.0, 98.0),  # 2%
    ]
    cands = tuple(
        _Candidate(
            side=PositionSide.LONG,
            entry_time=t,
            entry_price=e,
            exit_time=t + 1,
            exit_price=e * 1.01,
            reason=ExitReason.END_OF_DATA,
            stop_price=s,
            trigger_time=t,
        )
        for t, e, s in specs
    )
    k = (10.0, 50.0, 5.0, 30.0, 80.0)  # %K per candidate
    return iso.IsoCell(
        symbol="X",
        timeframe="1h",
        boundary_ms=1_000_000,  # 모두 앞 층
        pool={4: {"full": cands}},
        k_value={"full": k},
    )


def test_floor_idx_is_monotone_nested() -> None:
    cell = _cell()
    f0 = iso._floor_idx(cell, 4, "full", 0.0)
    f3 = iso._floor_idx(cell, 4, "full", 0.03)
    f4 = iso._floor_idx(cell, 4, "full", 0.04)
    assert f0 == [0, 1, 2, 3, 4]  # 전부 (width>0)
    assert f3 == [0, 1, 3]  # 5%, 3%, 4.5%
    assert f4 == [0, 3]  # 5%, 4.5%
    # 하한이 높을수록 부분집합
    assert set(f4) <= set(f3) <= set(f0)


def test_real_idx_is_subset_of_floor_idx() -> None:
    cell = _cell()
    for floor in (0.0, 0.03, 0.04):
        floor_idx = set(iso._floor_idx(cell, 4, "full", floor))
        real = set(iso._real_idx(cell, 4, "full", floor, 25.0))
        assert real <= floor_idx  # %K로 고른 건 하한 통과 풀의 부분집합


def test_real_idx_applies_threshold() -> None:
    cell = _cell()
    # 하한 0 · %K<25 → k=(10,50,5,30,80) 중 <25 = 인덱스 0,2
    assert iso._real_idx(cell, 4, "full", 0.0, 25.0) == [0, 2]
    # %K<15 → 0,2 (10,5) 유지; %K<8 → 2만
    assert iso._real_idx(cell, 4, "full", 0.0, 8.0) == [2]


def test_null_idx_matches_real_count_and_is_deterministic() -> None:
    cell = _cell()
    real = iso._real_idx(cell, 4, "full", 0.0, 25.0)
    a = iso._null_idx_seeded(cell, 4, "full", 0.0, 25.0, real, draw=3)
    b = iso._null_idx_seeded(cell, 4, "full", 0.0, 25.0, real, draw=3)
    assert a == b  # 결정적
    assert len(a) == len(real)  # 같은 개수
    assert set(a) <= set(iso._floor_idx(cell, 4, "full", 0.0))  # 풀에서 뽑음
