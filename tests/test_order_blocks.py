"""strategy.order_blocks 에 대한 테스트.

`strategy/reference/README.md` 명세(Fluxchart Volumized Order Blocks 이식)를
기준으로, 손으로 직접 추적해 도출한 고정 시나리오에 대해 오더블록의
top/bottom/방향/startTime/breaker 상태가 기대값과 일치하는지 검증한다
(패리티 테스트 방침). TradingView 원본 차트 출력에 대한 직접 대조는 이
환경에서 재현할 수 없어, 명세를 기준으로 손으로 도출한 기대값을 진실源으로
삼는다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pytest

from strategy.models import OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import (
    OrderBlockDetector,
    _combine_same_direction,
    _RawOrderBlock,
    _true_range,
    _wilder_rma,
    detect_order_blocks,
)

# 손으로 추적한 강세 오더블록 시나리오. swing_length=3으로 계산 과정을 추적:
#   - idx0의 저가(90)가 idx1~3 저가보다 낮게 유지되어 t=3에서 저점 스윙 확정
#     (bottom = index 0, price 90).
#   - idx4의 고가(110)가 idx5~7 고가보다 높게 유지되어 t=7에서 고점 스윙 확정
#     (top = index 4, price 110).
#   - t=11의 종가(112)가 top.price(110)를 상향 돌파 → 오더블록 생성 트리거.
#     탐색 구간(offset 1..6, 절대 인덱스 5..10)에서 가장 낮은 저가는 idx10
#     (low=98) → box_top=high[10]=103, box_bottom=low[10]=98.
#   - obVolume = V11+V10+V9 = 30+10+10 = 50, obLowVolume = V9 = 10,
#     obHighVolume = V11+V10 = 40.
#   - idx12의 저가(95)가 bottom(98) 아래로 wick → breaker.
_BULL_BARS = [
    # open, high, low, close, volume
    (100, 102, 90, 95, 10),
    (95, 100, 93, 98, 10),
    (98, 101, 94, 99, 10),
    (99, 103, 95, 101, 10),
    (101, 110, 100, 108, 10),
    (108, 109, 104, 106, 15),
    (106, 107, 103, 105, 20),
    (105, 106, 102, 104, 25),
    (104, 105, 100, 102, 10),
    (102, 104, 99, 101, 10),
    (101, 103, 98, 100, 10),
    (100, 115, 99, 112, 30),
    (112, 113, 95, 97, 10),
]


def _make_df(bars: Sequence[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
    )


def _bull_params(**overrides: object) -> OrderBlockParams:
    base = {
        "swing_length": 3,
        "atr_length": 3,
        "max_atr_mult": 100.0,
        "combine_obs": False,
        "zone_count": "high",
    }
    base.update(overrides)
    return OrderBlockParams.model_validate(base)


def test_bullish_order_block_parity() -> None:
    """고정 시나리오에서 강세 OB의 top/bottom/startTime/volume/breaker 일치."""
    df = _make_df(_BULL_BARS)
    result = detect_order_blocks(df, _bull_params())

    assert len(result.order_blocks) == 1
    ob = result.order_blocks[0]
    assert ob.direction == OrderBlockDirection.BULLISH
    assert ob.top == 103.0
    assert ob.bottom == 98.0
    assert ob.start_time == 10 * 60_000
    assert ob.confirmed_time == 11 * 60_000
    assert ob.ob_volume == 50.0
    assert ob.ob_low_volume == 10.0
    assert ob.ob_high_volume == 40.0
    assert ob.breaker is True
    assert ob.break_time == 12 * 60_000
    assert ob.combined is False


def test_bullish_signal_cancelled_on_breaker_tap() -> None:
    """breaker 봉이 곧 재진입(tap) 봉이면 시그널 상태가 cancelled."""
    df = _make_df(_BULL_BARS)
    result = detect_order_blocks(df, _bull_params())

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.direction == OrderBlockDirection.BULLISH
    assert signal.trigger_time == 12 * 60_000
    assert signal.price == 97.0
    assert signal.status == "cancelled"


def test_bullish_order_block_removed_after_breaker_high_exceeds_top() -> None:
    """breaker 이후 고가가 top을 넘으면 리스트에서 제거된다."""
    bars = [*_BULL_BARS, (97, 105, 96, 100, 10)]  # high=105 > top(103)
    df = _make_df(bars)
    result = detect_order_blocks(df, _bull_params())
    assert result.order_blocks == []


def test_bearish_order_block_parity_via_price_mirror() -> None:
    """가격을 (200 - price)로 미러링하면 강세 시나리오가 대칭적으로 약세가 된다.

    미러링 하에서 high/low가 서로 바뀌므로, 원본의 저점 스윙↔고점 스윙 조건이
    맞바뀌어 동일한 알고리즘이 약세 오더블록을 만든다. obLowVolume/obHighVolume이
    강세와 반대로 배정되는지까지 함께 검증한다.
    """
    mirrored = [(200 - o, 200 - low, 200 - high, 200 - c, v) for o, high, low, c, v in _BULL_BARS]
    df = _make_df(mirrored)
    result = detect_order_blocks(df, _bull_params())

    assert len(result.order_blocks) == 1
    ob = result.order_blocks[0]
    assert ob.direction == OrderBlockDirection.BEARISH
    assert ob.top == 102.0
    assert ob.bottom == 97.0
    assert ob.start_time == 10 * 60_000
    assert ob.confirmed_time == 11 * 60_000
    assert ob.ob_volume == 50.0
    # 강세의 obLow/obHigh가 뒤바뀐다 (README 명세: 약세는 대칭).
    assert ob.ob_low_volume == 40.0
    assert ob.ob_high_volume == 10.0
    assert ob.breaker is True
    assert ob.break_time == 12 * 60_000


def test_atr_filter_rejects_oversized_order_block() -> None:
    """OB 높이가 ATR*max_atr_mult를 초과하면 채택하지 않는다."""
    df = _make_df(_BULL_BARS)
    params = _bull_params(max_atr_mult=0.001)
    result = detect_order_blocks(df, params)
    assert result.order_blocks == []


def test_zone_invalidation_close_mode_uses_body_not_wick() -> None:
    """`zone_invalidation="close"`이면 몸통(open/close)만으로 무효화 판정."""
    # idx12의 저가(wick)는 95로 bottom(98) 아래지만, open=112/close=97이라
    # min(open,close)=97 역시 98 아래이므로 close 모드에서도 breaker.
    # wick과 차이를 보려면 몸통은 bottom 위, 꼬리만 아래인 봉이 필요하다.
    bars = [*_BULL_BARS[:-1], (99, 113, 90, 99, 10)]  # low=90(wick) but min(open,close)=99>=98
    df = _make_df(bars)

    wick_result = detect_order_blocks(df, _bull_params(zone_invalidation="wick"))
    close_result = detect_order_blocks(df, _bull_params(zone_invalidation="close"))

    assert wick_result.order_blocks[0].breaker is True
    assert close_result.order_blocks[0].breaker is False


def test_closed_column_filters_unconfirmed_bars() -> None:
    """`closed=False`인 미확정봉은 입력에서 제외된다."""
    df = _make_df(_BULL_BARS)
    df["closed"] = True
    df.loc[len(df) - 1, "closed"] = False  # 마지막(breaker) 봉을 미확정 처리

    result = detect_order_blocks(df, _bull_params())
    ob = result.order_blocks[0]
    assert ob.breaker is False  # breaker 봉이 제외되었으므로 아직 무효화 안됨


def test_missing_required_column_raises() -> None:
    df = _make_df(_BULL_BARS).drop(columns=["volume"])
    with pytest.raises(ValueError, match="volume"):
        detect_order_blocks(df, _bull_params())


def test_empty_dataframe_returns_empty_result() -> None:
    df = _make_df([])
    result = detect_order_blocks(df, _bull_params())
    assert result.order_blocks == []
    assert result.signals == []


def test_max_distance_to_last_bar_skips_older_history() -> None:
    """탐지 윈도우를 좁히면(최근 N봉만) 오래된 스윙/OB는 탐지되지 않는다."""
    df = _make_df(_BULL_BARS)
    result = detect_order_blocks(df, _bull_params(max_distance_to_last_bar=1))
    assert result.order_blocks == []


def test_zone_count_limits_selected_order_blocks() -> None:
    """`zone_count="one"`이면 방향별로 최신 1개만 채택한다."""
    params = _bull_params(zone_count="one")
    assert params.zone_limit == 1


class TestCombineSameDirection:
    def test_merges_overlapping_zones(self) -> None:
        a = _RawOrderBlock(
            top=100,
            bottom=90,
            ob_volume=10,
            direction=OrderBlockDirection.BULLISH,
            start_time=0,
            confirmed_time=5,
            ob_low_volume=3,
            ob_high_volume=7,
        )
        b = _RawOrderBlock(
            top=95,
            bottom=85,
            ob_volume=20,
            direction=OrderBlockDirection.BULLISH,
            start_time=2,
            confirmed_time=8,
            ob_low_volume=8,
            ob_high_volume=12,
        )
        merged = _combine_same_direction([a, b], now=100)

        assert len(merged) == 1
        ob = merged[0]
        assert ob.top == 100
        assert ob.bottom == 85
        assert ob.ob_volume == 30
        assert ob.ob_low_volume == 11
        assert ob.ob_high_volume == 19
        assert ob.combined is True

    def test_does_not_merge_non_overlapping_zones(self) -> None:
        a = _RawOrderBlock(
            top=50,
            bottom=40,
            ob_volume=5,
            direction=OrderBlockDirection.BULLISH,
            start_time=0,
            confirmed_time=1,
            ob_low_volume=1,
            ob_high_volume=4,
        )
        b = _RawOrderBlock(
            top=200,
            bottom=190,
            ob_volume=5,
            direction=OrderBlockDirection.BULLISH,
            start_time=0,
            confirmed_time=1,
            ob_low_volume=1,
            ob_high_volume=4,
        )
        merged = _combine_same_direction([a, b], now=100)
        assert len(merged) == 2
        assert all(not ob.combined for ob in merged)


class TestWilderAtrHelpers:
    def test_true_range_first_bar_is_high_minus_low(self) -> None:
        tr = _true_range([10.0, 12.0], [8.0, 9.0], [9.0, 11.0])
        assert tr[0] == 2.0  # high[0]-low[0], no previous close
        assert tr[1] == max(12.0 - 9.0, abs(12.0 - 9.0), abs(9.0 - 9.0))

    def test_wilder_rma_seeds_with_sma_then_recurses(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _wilder_rma(values, length=3)
        assert result[0] is None
        assert result[1] is None
        seed = result[2]
        assert seed is not None
        assert seed == pytest.approx((1.0 + 2.0 + 3.0) / 3)
        expected_3 = (seed * 2 + 4.0) / 3
        assert result[3] == pytest.approx(expected_3)

    def test_wilder_rma_insufficient_length_returns_all_none(self) -> None:
        result = _wilder_rma([1.0, 2.0], length=5)
        assert result == [None, None]


def test_order_block_detector_class_matches_convenience_function() -> None:
    df = _make_df(_BULL_BARS)
    params = _bull_params()
    via_detector = OrderBlockDetector(params).run(df)
    via_function = detect_order_blocks(df, params)
    assert via_detector == via_function
