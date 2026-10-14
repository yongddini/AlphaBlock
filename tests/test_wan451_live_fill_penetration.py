"""라이브 관통 체결(WAN-451 ⑤) — 백테스트 `pen_5bp`와 같은 문턱 · 같은 체결 시각."""

from __future__ import annotations

import pytest

from backtest.substep import SubStep, simulate_zone_limit_trade
from live.limit_orders import PendingLimitOrder
from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

LIMIT = 100.0
STOP = 95.0
M = 60_000


def _order(
    bps: float, direction: OrderBlockDirection = OrderBlockDirection.BULLISH
) -> PendingLimitOrder:
    stop = STOP if direction is OrderBlockDirection.BULLISH else 105.0
    return PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=direction,
        limit_price=LIMIT,
        stop_price=stop,
        rsi_state=RealtimeRsi(length=3),
        fill_penetration_bps=bps,
        limit_valid_bars=None,
    )


def test_default_zero_still_fills_on_touch() -> None:
    order = _order(0.0)
    fill = order.on_price(LIMIT, 101.0, 100.5, now_ms=M)
    assert fill is not None and fill.price == LIMIT


def test_five_bp_needs_real_penetration_and_fills_at_limit() -> None:
    order = _order(5.0)
    assert order.on_price(LIMIT, 101.0, 100.5, now_ms=M) is None  # 닿기만 함
    assert order.on_price(LIMIT * (1 - 4 / 10_000), 101.0, 100.2, now_ms=2 * M) is None  # 4bp
    fill = order.on_price(LIMIT * (1 - 5 / 10_000), 101.0, 100.0, now_ms=3 * M)
    assert fill is not None
    assert fill.price == LIMIT  # 체결가는 여전히 지정가
    assert fill.penetration_bps == pytest.approx(5.0)
    assert fill.time == 3 * M


def test_short_mirror() -> None:
    order = _order(5.0, OrderBlockDirection.BEARISH)
    assert order.on_price(99.0, LIMIT, 99.5, now_ms=M) is None
    fill = order.on_price(99.0, LIMIT * (1 + 5 / 10_000), 99.8, now_ms=2 * M)
    assert fill is not None and fill.price == LIMIT


def test_rejects_negative_requirement() -> None:
    with pytest.raises(ValueError, match="음수"):
        _order(-1.0)


@pytest.mark.parametrize("bps", [0.0, 5.0])
def test_same_fill_minute_as_the_backtest_simulator(bps: float) -> None:
    """같은 1분봉 경로면 라이브 주문과 백테스트 시뮬레이터가 같은 분·같은 가격에 체결한다."""
    lows = [100.3, 100.0, 99.97, 99.95, 99.5]  # 터치 → 3bp → 5bp → 깊게
    steps = [
        SubStep(time=k * M, high=101.0, low=lo, close=lo + 0.2, htf_bar_time=0)
        for k, lo in enumerate(lows)
    ]
    outcome = simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=LIMIT,
        stop_price=STOP,
        substeps=steps,
        rsi_state=RealtimeRsi(length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        rsi_gate_mode="unconditional",
        limit_valid_bars=None,
        penetration_bps=bps,
    )
    order = _order(bps)
    live_fill = None
    for s in steps:
        live_fill = order.on_price(s.low, s.high, s.close, now_ms=s.time)
        if live_fill is not None:
            break
    assert outcome.filled and live_fill is not None
    assert live_fill.time == outcome.entry_time
    assert live_fill.price == outcome.entry_price
