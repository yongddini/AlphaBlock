"""WAN-119: 봉내 실시간 밴드(`RealtimeBand`) — 수식 패리티·워밍업·라이브 성질.

이 상태 머신이 지켜야 할 계약은 셋이다:

1. **수식은 기존 밴드와 같다** — "실시간"은 수식이 아니라 20번째 표본에 무엇을 넣느냐의
   문제다. 봉이 닫히는 순간(현재가 = 그 봉 종가)에는 `tap` 모드와 **한 값으로 만나야**
   한다. 이 동치가 깨지면 3자 비교표의 `L2i` 열이 `L2`와 다른 이유가 "봉내 움직임"인지
   "수식이 갈라졌기 때문"인지 구분할 수 없다.
2. **워밍업이 `tap`과 같은 봉에서 풀린다** — 확정봉 19개 + 현재가 = 20표본.
3. **봉내에 실제로 움직인다** — 현재가가 다르면 밴드도 다르다(이 모드의 존재 이유).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from strategy.confluence import ConfluenceStrategy
from strategy.models import DeviationFilterParams
from strategy.realtime_band import RealtimeBand

_BOLLINGER = DeviationFilterParams(anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0)


def _closes() -> list[float]:
    """결정적 합성 종가 — 추세 + 진폭이 변하는 진동(σ가 상수가 아니어야 의미가 있다)."""
    return [100.0 + i * 0.4 + (i % 7) * (1.0 + i * 0.05) for i in range(60)]


def _frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(len(closes))],
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1.0] * len(closes),
        }
    )


@pytest.mark.parametrize("direction_sign", [1, -1])
def test_live_band_at_bar_close_equals_tap_band(direction_sign: int) -> None:
    """현재가 = 그 봉 종가면 `intrabar_live`는 `tap`과 **같은 값**이다.

    두 모드가 갈라지는 유일한 이유는 20번째 표본이고, 봉이 닫히는 순간 그 둘은 같은
    값이기 때문이다. `RealtimeBand`(파이썬 루프)와 `deviation_filter_components`
    (pandas rolling)는 알고리즘이 달라 마지막 비트는 다를 수 있으므로 상대 오차로 본다.
    """
    closes = _closes()
    anchor_vals, width_vals = ConfluenceStrategy.deviation_filter_components(
        _frame(closes), _BOLLINGER, "close"
    )
    for pos in range(19, len(closes)):
        expected = ConfluenceStrategy.deviation_band_at(
            pos, direction_sign, anchor_vals, width_vals, "tap"
        )
        assert expected is not None
        # 탭 봉 **직전까지** 시딩하고, 20번째 표본 자리에 그 봉 종가를 현재가로 얹는다.
        band = RealtimeBand.seed_from_closed(closes[:pos], _BOLLINGER)
        actual = band.value(closes[pos], direction_sign)
        assert actual == pytest.approx(expected, rel=1e-12)


def test_warmup_unlocks_on_the_same_bar_as_tap() -> None:
    """확정봉 19개 + 현재가 = 20표본 → `tap`과 같은 봉(pos=19)에서 값이 나온다.

    `prev_closed`처럼 한 봉 늦으면 표본이 달라져 3자 비교가 셋업 수부터 어긋난다.
    """
    closes = _closes()
    assert RealtimeBand.seed_from_closed(closes[:18], _BOLLINGER).ready is False
    assert RealtimeBand.seed_from_closed(closes[:18], _BOLLINGER).value(closes[18], 1) is None

    ready = RealtimeBand.seed_from_closed(closes[:19], _BOLLINGER)
    assert ready.ready is True
    assert ready.value(closes[19], 1) is not None


def test_band_moves_within_the_bar() -> None:
    """현재가가 다르면 밴드도 다르다 — 이 모드의 존재 이유(사용자 관찰)."""
    closes = _closes()
    band = RealtimeBand.seed_from_closed(closes[:30], _BOLLINGER)
    low = band.value(closes[29] * 0.97, 1)
    high = band.value(closes[29] * 1.03, 1)
    assert low is not None and high is not None
    assert low != high
    # 상태를 바꾸지 않는 조회여야 한다(`RealtimeRsi.value`와 같은 계약).
    assert band.value(closes[29] * 0.97, 1) == low


def test_commit_rolls_the_window() -> None:
    """`commit`으로 굴린 상태 = 그 종가까지 시딩한 상태(`RealtimeRsi`와 같은 계약)."""
    closes = _closes()
    rolled = RealtimeBand.seed_from_closed(closes[:25], _BOLLINGER)
    for close in closes[25:30]:
        rolled.commit(close)
    seeded = RealtimeBand.seed_from_closed(closes[:30], _BOLLINGER)
    assert rolled.value(closes[30], 1) == seeded.value(closes[30], 1)


def test_population_stdev_matches_indicator_definition() -> None:
    """σ는 모표준편차(`ddof=0`) — `indicators.stdev`/트레이딩뷰 `ta.stdev`와 같은 정의.

    표본표준편차(`ddof=1`)를 쓰면 밴드 폭이 체계적으로 넓어져 진입가가 통째로 어긋난다.
    """
    closes = _closes()
    window = closes[10:30]
    mean = sum(window) / len(window)
    population = math.sqrt(sum((c - mean) ** 2 for c in window) / len(window))

    band = RealtimeBand.seed_from_closed(closes[10:29], _BOLLINGER)
    value = band.value(closes[29], 1)
    assert value is not None
    # band = anchor - 1*width  →  width = anchor - band
    assert mean - value == pytest.approx(population * 2.0, rel=1e-12)


def test_atr_width_is_rejected_rather_than_silently_approximated() -> None:
    """ATR 폭은 거부한다 — 실시간 값이 존재할 수 없기 때문이다.

    조용히 확정 ATR로 대체하면 `intrabar_live` 라벨을 달고 다른 걸 돌리게 된다(WAN-95의
    "라벨과 실제 실행이 갈라진다").
    """
    atr_filter = DeviationFilterParams(width_kind="atr", width_value=2.0, atr_length=14)
    with pytest.raises(ValueError, match="atr"):
        RealtimeBand(filter_params=atr_filter)


def test_close_anchor_uses_live_price() -> None:
    """`anchor="close"`의 실시간 기준선은 확정 종가가 아니라 **현재가**다."""
    pct = DeviationFilterParams(anchor="close", width_kind="pct", width_value=0.02)
    band = RealtimeBand.seed_from_closed(_closes()[:30], pct)
    assert band.value(200.0, 1) == pytest.approx(200.0 * 0.98, rel=1e-12)
