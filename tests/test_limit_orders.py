"""라이브/페이퍼 대기 지정가 주문 상태 머신 테스트 (WAN-41 · WAN-45 개정).

`live.limit_orders`가 백테스트 서브스텝(`backtest.substep`)과 동일한 규칙으로 대기
지정가 주문을 굴리는지 검증한다. WAN-45 개정의 핵심:

* **기본 게이트는 없음**(`rsi_gate_mode="unconditional"`, WAN-123) — 닿으면 워밍업
  (RSI None)이어도 즉시 체결한다. 옛 극단 게이트는 옵트인으로만 돈다.
* **지정가는 봉내에 움직인다**(WAN-132) — `live_limit`(LiveLimitProvider)로 매 틱
  재산정하고, 손절·익절도 체결 순간 그 계약이 낸다.
* 체결률 실측 필드(관통 폭·대기 시간)가 `LimitFill`에 실린다.
"""

from __future__ import annotations

import pytest

from live.limit_orders import (
    LimitFill,
    LimitOrderBook,
    LimitOrderStatus,
    PendingLimitOrder,
)
from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

# 롱 셋업: 지정가(존 상단)=100, 손절(존 하단)=90.
_LIMIT = 100.0
_STOP = 90.0
# 강한 하락 시딩 → 실시간 RSI 과매도(≤30) 유지(옵트인 극단 게이트의 롱 조건 충족).
_OVERSOLD_SEED = [140.0, 130.0, 120.0, 110.0, 105.0]
# 강한 상승 시딩 → 과매수(≥70)(옵트인 극단 게이트의 롱 조건 미충족).
_OVERBOUGHT_SEED = [90.0, 95.0, 100.0, 105.0, 110.0]


def _long_order(seed: list[float], **kw: object) -> PendingLimitOrder:
    return PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        rsi_state=RealtimeRsi.seed_from_closed(seed, length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        **kw,  # type: ignore[arg-type]
    )


def _tick(order: PendingLimitOrder, price: float, *, now_ms: int) -> LimitFill | None:
    """단일 가격 틱(저가=고가=종가)을 넣는다 — 웹소켓 현재가에 대응."""
    return order.on_price(price, price, price, now_ms=now_ms)


class TestDefaultUnconditionalGate:
    """WAN-123 채택 기본값: RSI 게이트가 아예 없다 — 닿으면 즉시 체결."""

    def test_fills_on_touch_even_when_rsi_overbought(self) -> None:
        # 옛 부품(WAN-41)은 여기서 롱 `RSI<=30`을 요구해 안 채웠다 — 오늘 엔진에는
        # 게이트가 없으므로 체결돼야 한다(안 채우면 체결률 실측이 코드 탓에 오염된다).
        order = _long_order(_OVERBOUGHT_SEED)
        assert _tick(order, 101.0, now_ms=1) is None
        fill = order.on_price(100.0, 100.0, 100.0, now_ms=2)
        assert fill is not None
        assert fill.price == _LIMIT
        assert fill.stop_price == _STOP
        assert order.status is LimitOrderStatus.FILLED

    def test_fills_during_rsi_warmup(self) -> None:
        # 워밍업(RSI None)이어도 체결한다 — WAN-123의 `unconditional`은 `none`과 다르다.
        order = _long_order([], placed_ms=0)
        fill = order.on_price(99.5, 100.5, 100.0, now_ms=7)
        assert fill is not None
        assert fill.rsi is None
        assert fill.waited_ms == 7

    def test_penetration_bps_recorded(self) -> None:
        # 관통 폭 = 지정가 대비 저가가 지나친 정도(bp). 스치듯 닿으면 0이다 —
        # `baseline` 낙관 가정의 비용을 재는 1급 실측값(WAN-96과 같은 자).
        touched = _long_order(_OVERSOLD_SEED)
        fill = touched.on_price(100.0, 101.0, 100.5, now_ms=1)
        assert fill is not None
        assert fill.penetration_bps == 0.0

        penetrated = _long_order(_OVERSOLD_SEED)
        fill2 = penetrated.on_price(99.9, 101.0, 100.5, now_ms=1)
        assert fill2 is not None
        assert fill2.penetration_bps == pytest.approx(10.0, rel=1e-6)


class TestOptInRsiGate:
    """옛 극단 게이트(WAN-41~122)는 옵트인으로만 돈다."""

    def test_extreme_gate_fills_when_condition_met(self) -> None:
        order = _long_order(_OVERSOLD_SEED, rsi_gate_mode="extreme")
        fill = _tick(order, 100.0, now_ms=2)
        assert fill is not None
        assert fill.rsi is not None and fill.rsi <= 30.0

    def test_extreme_gate_realtime_rsi_matches_shared_state_machine(self) -> None:
        """게이트가 읽는 실시간 RSI가 동일 시딩·현재가의 `RealtimeRsi`와 일치한다."""
        order = _long_order(_OVERSOLD_SEED, rsi_gate_mode="extreme")
        reference = RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3)
        fill = _tick(order, 100.0, now_ms=5)
        assert fill is not None
        assert fill.rsi == reference.value(100.0)

    def test_condition_fail_keeps_order_by_default(self) -> None:
        order = _long_order(_OVERBOUGHT_SEED, rsi_gate_mode="extreme")
        assert _tick(order, 100.0, now_ms=1) is None
        assert order.status is LimitOrderStatus.PENDING

    def test_condition_fail_cancels_when_configured(self) -> None:
        order = _long_order(
            _OVERBOUGHT_SEED, rsi_gate_mode="extreme", cancel_on_condition_fail=True
        )
        assert _tick(order, 100.0, now_ms=1) is None
        assert order.status is LimitOrderStatus.CANCELLED_CONDITION_FAILED

    def test_first_tap_free_bypasses_gate(self) -> None:
        # WAN-100 계약: 첫 탭 면제는 `tap_index`를 아는 호출부가 판정해 넘긴다.
        order = _long_order(_OVERBOUGHT_SEED, rsi_gate_mode="first_tap_free", first_tap_free=True)
        assert _tick(order, 100.0, now_ms=1) is not None

    def test_retap_applies_gate_under_first_tap_free_mode(self) -> None:
        order = _long_order(
            _OVERBOUGHT_SEED, rsi_gate_mode="first_tap_free", first_tap_free=False, tap_index=1
        )
        assert _tick(order, 100.0, now_ms=1) is None
        assert order.status is LimitOrderStatus.PENDING


class _FakeProvider:
    """봉내 재산정 공급자 스텁 — `LiveLimitProvider` 계약."""

    def __init__(self, prices: list[float | None], exits: tuple[float, float | None] | None):
        self.prices = prices
        self.exits = exits
        self.committed: list[float] = []

    def commit(self, closed_price: float) -> None:
        self.committed.append(closed_price)

    def limit_price(self, live_price: float) -> float | None:
        return self.prices.pop(0) if self.prices else None

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
        return self.exits


class TestLiveLimitProvider:
    """WAN-132 채택 경로: 지정가가 틱마다 재산정되고 손절·익절은 체결 순간 나온다."""

    def _order(self, provider: _FakeProvider) -> PendingLimitOrder:
        return PendingLimitOrder(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            stop_price=_STOP,
            rsi_state=RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3),
            live_limit=provider,
            placed_ms=0,
        )

    def test_no_order_resting_while_provider_rejects(self) -> None:
        provider = _FakeProvider([None, 99.0], exits=(_STOP, 112.5))
        order = self._order(provider)
        # 첫 틱: 밴드 워밍업/기각 — 주문판에 주문이 없다(터치해도 체결 없음).
        assert order.on_price(90.0, 90.0, 90.0, now_ms=1) is None
        assert order.first_rested_ms is None
        # 둘째 틱: 밴드가 99를 낸다 → 걸리고, 저가 98이 닿아 체결.
        fill = order.on_price(98.0, 99.5, 99.0, now_ms=2)
        assert fill is not None
        assert fill.price == 99.0
        assert order.first_rested_ms == 2
        assert fill.stop_price == _STOP
        assert fill.take_profit_price == 112.5

    def test_resolve_exits_none_cancels_instead_of_filling(self) -> None:
        # WAN-143 대응 경로: 청산 규칙을 못 만들면 체결시키지 않는다.
        provider = _FakeProvider([99.0], exits=None)
        order = self._order(provider)
        assert order.on_price(98.0, 99.5, 99.0, now_ms=1) is None
        assert order.status is LimitOrderStatus.CANCELLED_CONDITION_FAILED

    def test_bar_close_commits_provider_band(self) -> None:
        provider = _FakeProvider([], exits=(_STOP, None))
        order = self._order(provider)
        order.on_bar_close(104.0)
        assert provider.committed == [104.0]

    def test_rejects_both_static_and_live_price(self) -> None:
        provider = _FakeProvider([], exits=None)
        with pytest.raises(ValueError, match="정확히 하나"):
            PendingLimitOrder(
                symbol="BTC/USDT:USDT",
                timeframe="1h",
                direction=OrderBlockDirection.BULLISH,
                limit_price=_LIMIT,
                stop_price=_STOP,
                rsi_state=RealtimeRsi(length=3),
                live_limit=provider,
            )

    def test_rejects_static_tp_with_live_provider(self) -> None:
        provider = _FakeProvider([], exits=None)
        with pytest.raises(ValueError, match="take_profit_price"):
            PendingLimitOrder(
                symbol="BTC/USDT:USDT",
                timeframe="1h",
                direction=OrderBlockDirection.BULLISH,
                stop_price=_STOP,
                take_profit_price=110.0,
                rsi_state=RealtimeRsi(length=3),
                live_limit=provider,
            )


def test_expiry_cancels_after_valid_bars() -> None:
    order = _long_order(_OVERSOLD_SEED, limit_valid_bars=2)
    order.on_bar_close(104.0)
    assert not order.status.is_terminal
    order.on_bar_close(103.0)
    assert order.status is LimitOrderStatus.CANCELLED_EXPIRED
    # 만료 후에는 닿아도 체결되지 않는다.
    assert _tick(order, 100.0, now_ms=9) is None


def test_unlimited_validity_waits_until_invalidation() -> None:
    # `limit_valid_bars=None`(WAN-73): 만료 없이 존 무효화까지 대기.
    order = _long_order(_OVERSOLD_SEED, limit_valid_bars=None)
    for close in (104.0, 103.0, 102.0):
        order.on_bar_close(close)
    assert order.status is LimitOrderStatus.PENDING


def test_invalidation_cancels_immediately() -> None:
    order = _long_order(_OVERSOLD_SEED)
    order.cancel_invalidated()
    assert order.status is LimitOrderStatus.CANCELLED_INVALIDATED
    assert _tick(order, 100.0, now_ms=9) is None


def test_book_single_order_per_series_and_fill_removes() -> None:
    book = LimitOrderBook()
    order = _long_order(_OVERSOLD_SEED)
    assert book.place(order) is order
    # 같은 시리즈 중복 예약은 무시.
    assert book.place(_long_order(_OVERSOLD_SEED)) is None
    assert book.pending("BTC/USDT:USDT", "1h") is order

    assert book.on_price("BTC/USDT:USDT", "1h", 101.0, 101.0, 101.0, now_ms=1) is None
    fill = book.on_price("BTC/USDT:USDT", "1h", 100.0, 100.0, 100.0, now_ms=2)
    assert fill is not None
    # 체결되면 장부에서 제거된다.
    assert book.pending("BTC/USDT:USDT", "1h") is None
    assert book.open_orders == []


def test_book_bar_close_expiry_and_invalidation() -> None:
    book = LimitOrderBook()
    book.place(_long_order(_OVERSOLD_SEED, limit_valid_bars=1))
    status = book.on_bar_close("BTC/USDT:USDT", "1h", 104.0)
    assert status is LimitOrderStatus.CANCELLED_EXPIRED
    assert book.pending("BTC/USDT:USDT", "1h") is None

    placed = book.place(_long_order(_OVERSOLD_SEED))
    assert placed is not None
    cancelled = book.cancel_invalidated("BTC/USDT:USDT", "1h")
    assert cancelled is placed
    assert cancelled.status is LimitOrderStatus.CANCELLED_INVALIDATED
    assert book.cancel_invalidated("BTC/USDT:USDT", "1h") is None
