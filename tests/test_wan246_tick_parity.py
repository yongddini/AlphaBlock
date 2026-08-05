"""WAN-246 §2: 틱 체결 vs 1분봉 체결 파리티 측정 프리미티브 (`live.tick_parity`).

핵심 두 사실을 동작으로 고정한다:

1. **상수 지정가는 결코 갈리지 않는다** — 1분봉이 그 분의 저가·고가를 담으므로 틱을
   아무리 촘촘히 넣어도 체결 여부·체결가가 같다(볼린저 없는 경로의 불변).
2. **`intrabar_live` 밴드만 갈린다** — 지정가가 표본(현재가)에 따라 움직이므로 1분봉
   종가로 잰 지정가와 틱 순간 지정가가 달라, 경계 근처 체결의 여부를 뒤집거나 체결가를
   미세하게 바꾼다. 그 크기를 잰다.
"""

from __future__ import annotations

from collections.abc import Callable

from live.limit_orders import PendingLimitOrder
from live.tick_parity import (
    FillDivergence,
    fill_path_divergence,
    ohlc_tick_path,
    summarize_divergences,
)
from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"


def test_ohlc_tick_path_orders_extremes_by_direction() -> None:
    """초록봉 O→L→H→C, 빨강봉 O→H→L→C(봉이 담은 극단을 순서대로 방문)."""
    assert ohlc_tick_path(100.0, 98.0, 102.0, 101.0) == [100.0, 98.0, 102.0, 101.0]
    assert ohlc_tick_path(100.0, 98.0, 102.0, 99.0) == [100.0, 102.0, 98.0, 99.0]


def _static_order_factory() -> Callable[[], PendingLimitOrder]:
    def make() -> PendingLimitOrder:
        return PendingLimitOrder(
            symbol=_SYMBOL,
            timeframe=_TF,
            direction=OrderBlockDirection.BULLISH,
            limit_price=100.0,
            stop_price=90.0,
            take_profit_price=115.0,
            rsi_state=RealtimeRsi(length=3),
            placed_ms=0,
        )

    return make


class _PriceDependentLimit:
    """봉내 지정가가 **표본 가격의 함수**인 공급자 — `intrabar_live` 밴드 이동의 모델."""

    def __init__(self, fn: Callable[[float], float]) -> None:
        self._fn = fn

    def commit(self, closed_price: float) -> None:
        return None

    def limit_price(self, live_price: float) -> float | None:
        return self._fn(live_price)

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None]:
        return (90.0, None)


def _live_order_factory(fn: Callable[[float], float]) -> Callable[[], PendingLimitOrder]:
    def make() -> PendingLimitOrder:
        return PendingLimitOrder(
            symbol=_SYMBOL,
            timeframe=_TF,
            direction=OrderBlockDirection.BULLISH,
            stop_price=90.0,
            rsi_state=RealtimeRsi(length=3),
            live_limit=_PriceDependentLimit(fn),
            placed_ms=0,
        )

    return make


def test_static_limit_never_diverges() -> None:
    """상수 지정가는 어떤 봉에서도 체결 여부·체결가가 갈리지 않는다(불변)."""
    make = _static_order_factory()
    bars = [
        # (open, low, high, close): 터치, 비터치, 봉내 되돌림 등 다양하게.
        (100.0, 98.0, 102.0, 101.0),  # 저가가 지정가 관통 → 둘 다 체결.
        (101.0, 100.5, 103.0, 102.0),  # 저가가 지정가 위 → 둘 다 미체결.
        (105.0, 99.5, 106.0, 104.0),  # 되돌림 저가만 관통 → 둘 다 체결(같은 가격).
    ]
    for open_, low, high, close in bars:
        div = fill_path_divergence(make, open_=open_, low=low, high=high, close=close, time_ms=1)
        assert not div.verdict_differs
        if div.bar_filled and div.tick_filled:
            assert div.price_delta_bps == 0.0
            assert div.bar_price == div.tick_price == 100.0


def test_intrabar_live_verdict_can_flip() -> None:
    """`intrabar_live`: 1분봉 경로가 틱보다 낙관적일 수 있다 — 봉은 체결, 틱은 미체결.

    지정가 = 표본가 − 1(밴드가 현재가 1 아래). 1분봉 경로는 지정가를 **종가**로 잡고
    저가로 터치를 보므로 저가가 종가−1 아래면 체결하지만, 틱 경로는 각 가격에서 지정가를
    다시 잡아 단일가가 자기−1을 넘지 못해 결코 안 닿는다. 1분봉이 더 낙관적이라는 실측.
    """
    make = _live_order_factory(lambda p: p - 1.0)
    div = fill_path_divergence(make, open_=100.0, low=98.0, high=102.0, close=101.0, time_ms=1)
    assert div.bar_filled is True
    assert div.tick_filled is False
    assert div.verdict_differs is True
    assert div.price_delta_bps is None  # 한쪽만 체결.


def test_intrabar_live_price_can_differ_when_both_fill() -> None:
    """둘 다 체결해도 체결가가 갈린다 — 밴드가 표본에 따라 움직인 폭이 체결가 차이다."""
    # 지정가 = 100 + (표본−100)*0.1 (밴드가 현재가를 따라 완만히 움직인다).
    make = _live_order_factory(lambda p: 100.0 + (p - 100.0) * 0.1)
    div = fill_path_divergence(make, open_=100.0, low=98.0, high=102.0, close=101.0, time_ms=1)
    assert div.bar_filled is True
    assert div.tick_filled is True
    assert not div.verdict_differs
    # 1분봉: limit(close=101)=100.1 @체결. 틱: 시가 100 → limit(100)=100.0 @체결.
    assert div.bar_price == 100.1
    assert div.tick_price == 100.0
    assert div.price_delta_bps is not None
    assert 9.0 < div.price_delta_bps < 11.0  # ≈10bp — 봉내 밴드 이동폭에 유계.


def test_summarize_counts_and_bounds() -> None:
    divergences = [
        FillDivergence(bar_filled=True, tick_filled=False, bar_price=100.0, tick_price=None),
        FillDivergence(bar_filled=True, tick_filled=True, bar_price=100.1, tick_price=100.0),
        FillDivergence(bar_filled=False, tick_filled=False, bar_price=None, tick_price=None),
    ]
    summary = summarize_divergences(divergences)
    assert summary.total == 3
    assert summary.verdict_differs == 1
    assert summary.verdict_differ_rate == 1 / 3
    assert summary.max_price_delta_bps > 0.0
    assert summary.mean_price_delta_bps > 0.0


def test_summarize_empty() -> None:
    summary = summarize_divergences([])
    assert summary.total == 0
    assert summary.verdict_differ_rate == 0.0
    assert summary.max_price_delta_bps == 0.0
