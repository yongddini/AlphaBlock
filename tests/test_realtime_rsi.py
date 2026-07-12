"""실시간(봉내) RSI 계산기 테스트 (WAN-41).

핵심 완료기준: **라이브·백테스트가 동일한 값을 낸다** — 즉 증분 상태 머신
(`RealtimeRsi`)이 배치 계산(`strategy.indicators.rsi`, Wilder RMA)과 정확히
일치한다. 확정봉 시퀀스 `closes`에 대해, `closes[:i]`를 커밋한 뒤
`value(closes[i])`가 `indicators.rsi`의 `i`번째 값과 같아야 한다.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from strategy.indicators import rsi as batch_rsi
from strategy.realtime_rsi import RealtimeRsi

_TOL = 1e-9


def _make_df(close: list[float]) -> pd.DataFrame:
    n = len(close)
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(n)],
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "volume": [1.0] * n,
        }
    )


# 손계산 추적용 짧은 시퀀스 + 무작위성 있는 긴 시퀀스.
_SHORT = [10.0, 11.0, 10.5, 11.5, 11.0, 12.0]
_LONG = [
    100.0,
    101.5,
    99.0,
    98.5,
    102.0,
    103.5,
    101.0,
    100.0,
    104.0,
    106.5,
    105.0,
    103.0,
    107.0,
    110.0,
    108.5,
    107.0,
    111.0,
    109.0,
    112.5,
    115.0,
    113.0,
    116.0,
    114.5,
    118.0,
    120.0,
]


@pytest.mark.parametrize("closes", [_SHORT, _LONG])
def test_value_matches_batch_rsi_at_every_index(closes: list[float]) -> None:
    """모든 인덱스에서 증분 실시간 RSI == 배치 RSI(라이브·백테스트 값 일치)."""
    length = 3
    expected = [float(v) for v in batch_rsi(_make_df(closes), length=length)]

    state = RealtimeRsi(length=length)
    for i, close in enumerate(closes):
        live = state.value(close)  # closes[:i] 커밋된 상태에서 closes[i]를 얹음
        exp = expected[i]
        if math.isnan(exp):
            assert live is None, f"idx {i}: 워밍업이어야 하는데 {live}"
        else:
            assert live is not None, f"idx {i}: 값이 있어야 하는데 None"
            assert abs(live - exp) < _TOL, f"idx {i}: {live} != {exp}"
        state.commit(close)


def test_seed_from_closed_equivalent_to_sequential_commit() -> None:
    length = 14
    seeded = RealtimeRsi.seed_from_closed(_LONG[:-1], length=length)
    manual = RealtimeRsi(length=length)
    for c in _LONG[:-1]:
        manual.commit(c)
    assert seeded.value(_LONG[-1]) == manual.value(_LONG[-1])


def test_realtime_value_does_not_mutate_state() -> None:
    """value()는 순수 조회 — 여러 번 호출해도 상태가 바뀌지 않는다(리페인트 아님)."""
    state = RealtimeRsi.seed_from_closed(_LONG[:-1], length=14)
    first = state.value(200.0)
    _ = state.value(50.0)  # 다른 현재가로 조회
    again = state.value(200.0)
    assert first == again


def test_warmup_returns_none_before_seed_formed() -> None:
    state = RealtimeRsi(length=14)
    for c in _LONG[:10]:  # 14봉 미만 → 시드 미형성
        assert state.value(c) is None
        state.commit(c)


def test_all_gains_gives_rsi_100() -> None:
    state = RealtimeRsi.seed_from_closed([1.0, 2.0, 3.0, 4.0, 5.0], length=3)
    assert state.value(6.0) == 100.0


def test_all_losses_gives_rsi_0() -> None:
    state = RealtimeRsi.seed_from_closed([5.0, 4.0, 3.0, 2.0, 1.0], length=3)
    assert state.value(0.5) == 0.0


def test_invalid_length_raises() -> None:
    with pytest.raises(ValueError):
        RealtimeRsi(length=0)
