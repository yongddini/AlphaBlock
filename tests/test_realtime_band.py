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


def _naive_seed(
    closes: list[float], filter_params: DeviationFilterParams, *, end: int | None = None
) -> RealtimeBand:
    """WAN-204 최적화 **이전**의 시딩 — 전체 이력을 순서대로 `commit`한다.

    존 탭마다 `closes[:end]` 전체를 커밋하던 O(N×M) 병목의 원본 동작이다. 최적화판
    (`RealtimeBand.seed_from_closed`, 꼬리 `window_size`개만 커밋)이 이것과 **비트 단위로
    같은 상태**를 내는지가 회귀 검증의 핵심이다.
    """
    state = RealtimeBand(filter_params=filter_params)
    seq = closes if end is None else closes[:end]
    for close in seq:
        state.commit(float(close))
    return state


@pytest.mark.parametrize(
    "filt",
    [
        _BOLLINGER,  # anchor=sma, width=stdev → 창 필요
        DeviationFilterParams(anchor="sma", sma_length=5, width_kind="stdev", width_value=2.0),
        DeviationFilterParams(anchor="close", width_kind="pct", width_value=0.02),  # 창 불필요
        DeviationFilterParams(anchor="sma", sma_length=1, width_kind="stdev", width_value=2.0),
    ],
)
def test_tail_seeding_is_bit_identical_to_full_history(filt: DeviationFilterParams) -> None:
    """WAN-204: 꼬리만 커밋해도 창 상태·`value()`가 전체 이력 커밋과 비트 단위로 같다.

    `_window`가 bounded deque(`sma_length-1`)라 그보다 오래된 종가는 커밋 즉시 굴러
    나간다 — 그래서 O(cut) 재커밋을 O(window_size)로 줄여도 최종 상태가 동일하다.
    존 탭마다 새로 시딩하는 긴 창(15m·6년) 백테스트의 O(N×M) 병목을 없앤 이 등가성이
    깨지면 채택 엔진의 진입가가 통째로 달라진다.
    """
    closes = _closes()
    # cut < window / == window / > window / == len / 경계값을 모두 훑는다.
    for cut in [0, 1, 2, 18, 19, 20, 30, 45, len(closes)]:
        opt = RealtimeBand.seed_from_closed(closes, filt, end=cut)
        ref = _naive_seed(closes, filt, end=cut)
        # 창 내용(밴드 상태)이 비트 단위로 같다.
        assert list(opt._window) == list(ref._window), f"window mismatch at cut={cut}"
        assert opt.ready == ref.ready
        # 조회값도 같다(다양한 현재가·부호에서).
        for price in (closes[min(cut, len(closes) - 1)], 123.456, 200.0):
            for sign in (1, -1):
                assert opt.value(price, sign) == ref.value(price, sign), (
                    f"value mismatch at cut={cut} price={price} sign={sign}"
                )


def test_end_argument_matches_pre_sliced_closes() -> None:
    """`end=cut`가 `closes[:cut]`를 미리 잘라 넘긴 것과 같다(호출부 사본 제거의 등가성).

    최적화가 호출부의 `closes[:cut]` 사본을 없애려고 `end` 인자를 도입했으므로, 두
    형태가 같은 상태를 내는지를 못 박는다.
    """
    closes = _closes()
    for cut in [0, 5, 19, 25, len(closes), len(closes) + 10]:
        via_end = RealtimeBand.seed_from_closed(closes, _BOLLINGER, end=cut)
        via_slice = RealtimeBand.seed_from_closed(closes[:cut], _BOLLINGER)
        assert list(via_end._window) == list(via_slice._window)
        assert via_end.value(150.0, 1) == via_slice.value(150.0, 1)
