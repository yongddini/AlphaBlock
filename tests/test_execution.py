"""주문 실행 · 포지션 · 리스크 관리 (WAN-9) 테스트.

핵심 완료 기준을 검증한다:
* 시그널(진입 의도) → 드라이런 주문 → 포지션 추적 → 청산 손익 정산의 end-to-end.
* 리스크 한도(명목/동시포지션/일일손실 서킷브레이커) 초과 시 신규 진입 차단.
* 기본값이 페이퍼이며, live_trading 없이는 실주문 브로커가 생성/호출되지 않음.
* 재시도·부분체결 처리.
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from execution.broker import CcxtLiveBroker, PaperBroker, build_live_broker
from execution.engine import (
    EntryIntent,
    ExecutionEngine,
    build_execution_engine,
)
from execution.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    side_for_entry,
    side_for_exit,
)
from execution.risk import RiskManager, RiskParams
from execution.sizing import PositionSizingParams
from strategy.models import OrderBlockDirection, SignalExitReason

_DAY0 = 1_700_000_000_000  # 2023-11-14 UTC 근처의 임의 기준 시각(ms).
_DAY_MS = 86_400_000


def _sizing(**kw: float) -> PositionSizingParams:
    base: dict[str, float] = {"risk_per_trade": 0.01, "leverage": 100.0}
    base.update(kw)
    return PositionSizingParams(**base)


def _engine(
    *,
    broker: PaperBroker | None = None,
    risk: RiskParams | None = None,
    sizing: PositionSizingParams | None = None,
    equity: float = 10_000.0,
) -> ExecutionEngine:
    return ExecutionEngine(
        broker=broker if broker is not None else PaperBroker(),
        risk_manager=RiskManager(risk if risk is not None else RiskParams(max_leverage=100.0)),
        sizing_params=sizing if sizing is not None else _sizing(),
        equity=equity,
    )


def _long_intent(price: float = 100.0, stop: float = 90.0) -> EntryIntent:
    return EntryIntent(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_price=price,
        entry_time=_DAY0,
        stop_price=stop,
        take_profit_price=120.0,
    )


# -- 방향 매핑 ----------------------------------------------------------------


def test_side_mapping() -> None:
    assert side_for_entry(OrderBlockDirection.BULLISH) is OrderSide.BUY
    assert side_for_entry(OrderBlockDirection.BEARISH) is OrderSide.SELL
    assert side_for_exit(OrderBlockDirection.BULLISH) is OrderSide.SELL
    assert side_for_exit(OrderBlockDirection.BEARISH) is OrderSide.BUY


# -- 페이퍼 브로커 ------------------------------------------------------------


def test_paper_broker_fills_market_at_mark() -> None:
    broker = PaperBroker()
    order = Order(symbol="BTC/USDT:USDT", side=OrderSide.BUY, type=OrderType.MARKET, quantity=2.0)
    fill = broker.place_order(order, mark_price=100.0)
    assert fill.status is OrderStatus.FILLED
    assert fill.filled_quantity == pytest.approx(2.0)
    assert fill.average_price == pytest.approx(100.0)
    assert broker.orders == [order]


def test_paper_broker_rejects_market_without_mark() -> None:
    broker = PaperBroker()
    order = Order(symbol="BTC/USDT:USDT", side=OrderSide.BUY, type=OrderType.MARKET, quantity=1.0)
    fill = broker.place_order(order, mark_price=None)
    assert fill.status is OrderStatus.REJECTED
    assert not fill.is_filled


def test_paper_broker_limit_fills_at_order_price() -> None:
    broker = PaperBroker()
    order = Order(
        symbol="ETH/USDT:USDT",
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        quantity=1.0,
        price=50.0,
    )
    fill = broker.place_order(order, mark_price=999.0)
    assert fill.average_price == pytest.approx(50.0)  # 지정가는 주문 가격으로 체결


# -- end-to-end: 진입 → 추적 → 청산 -----------------------------------------


def test_entry_opens_tracked_position() -> None:
    engine = _engine()
    outcome = engine.on_entry(_long_intent(), now_ms=_DAY0)
    assert outcome.accepted
    assert outcome.position is not None
    # 리스크 100 / 손절거리 10 = 수량 10.
    assert outcome.position.quantity == pytest.approx(10.0)
    assert engine.position("BTC/USDT:USDT", "1h") is not None
    assert engine.open_notional == pytest.approx(1000.0)


def test_exit_realizes_pnl_and_updates_equity() -> None:
    engine = _engine()
    engine.on_entry(_long_intent(), now_ms=_DAY0)
    outcome = engine.on_exit(
        "BTC/USDT:USDT",
        "1h",
        exit_price=110.0,
        reason=SignalExitReason.TAKE_PROFIT,
        now_ms=_DAY0,
    )
    assert outcome.accepted
    # 수량 10, (110-100)*10 = +100.
    assert outcome.realized_pnl == pytest.approx(100.0)
    assert engine.equity == pytest.approx(10_100.0)
    assert engine.position("BTC/USDT:USDT", "1h") is None


def test_short_exit_pnl_direction() -> None:
    engine = _engine()
    short = EntryIntent(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BEARISH,
        entry_price=100.0,
        entry_time=_DAY0,
        stop_price=110.0,
    )
    engine.on_entry(short, now_ms=_DAY0)
    # 숏은 하락이 이익: 100 → 90, 수량 10 → +100.
    outcome = engine.on_exit(
        "BTC/USDT:USDT", "1h", exit_price=90.0, reason=SignalExitReason.TAKE_PROFIT, now_ms=_DAY0
    )
    assert outcome.realized_pnl == pytest.approx(100.0)


def test_second_entry_same_series_blocked() -> None:
    engine = _engine()
    assert engine.on_entry(_long_intent(), now_ms=_DAY0).accepted
    blocked = engine.on_entry(_long_intent(), now_ms=_DAY0)
    assert not blocked.accepted
    assert "이미 오픈" in blocked.reason


def test_entry_without_stop_skipped() -> None:
    engine = _engine()
    intent = EntryIntent(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_price=100.0,
        entry_time=_DAY0,
        stop_price=None,
    )
    outcome = engine.on_entry(intent, now_ms=_DAY0)
    assert not outcome.accepted
    assert "손절" in outcome.reason


def test_exit_without_position_rejected() -> None:
    engine = _engine()
    outcome = engine.on_exit(
        "BTC/USDT:USDT", "1h", exit_price=100.0, reason=SignalExitReason.STOP_LOSS, now_ms=_DAY0
    )
    assert not outcome.accepted


# -- 리스크: 한도 초과 시 진입 차단 ------------------------------------------


def test_notional_cap_blocks_entry() -> None:
    # leverage=1 → 명목 상한 = 자본 10_000. 사이징 leverage도 1이라 수량이 상한에 걸린다.
    engine = _engine(
        risk=RiskParams(max_leverage=1.0, max_concurrent_positions=5),
        sizing=_sizing(leverage=2.0),  # 사이징은 명목 20_000까지 허용
    )
    outcome = engine.on_entry(_long_intent(stop=99.0), now_ms=_DAY0)
    # 사이징: 리스크 100 / 거리 1 = 100주(명목 10_000) → 상한과 동일, 허용.
    assert outcome.accepted
    # 두 번째 다른 시리즈 진입은 명목 상한 초과로 차단.
    intent2 = _long_intent(stop=99.0).model_copy(update={"timeframe": "4h"})
    blocked = engine.on_entry(intent2, now_ms=_DAY0)
    assert not blocked.accepted
    assert "명목가치 한도" in blocked.reason


def test_max_concurrent_positions_blocks() -> None:
    engine = _engine(
        risk=RiskParams(max_leverage=100.0, max_concurrent_positions=1),
    )
    assert engine.on_entry(_long_intent(), now_ms=_DAY0).accepted
    intent2 = _long_intent().model_copy(update={"timeframe": "4h"})
    blocked = engine.on_entry(intent2, now_ms=_DAY0)
    assert not blocked.accepted
    assert "동시 오픈 포지션 한도" in blocked.reason


def test_zero_size_skips_entry() -> None:
    # min_qty가 커서 사이징이 0을 반환 → 진입 스킵.
    engine = _engine(sizing=_sizing(min_qty=1000.0))
    outcome = engine.on_entry(_long_intent(), now_ms=_DAY0)
    assert not outcome.accepted
    assert "사이징" in outcome.reason


# -- 일일 손실 서킷브레이커 --------------------------------------------------


def test_daily_loss_circuit_breaker_blocks_new_entries() -> None:
    # 일일 손실 한도 5% = 기준자본 10_000 × 0.05 = 500.
    engine = _engine(
        risk=RiskParams(
            max_leverage=100.0,
            max_concurrent_positions=5,
            daily_loss_limit_fraction=0.05,
        ),
    )
    # 큰 손실을 내는 진입→청산으로 서킷브레이커를 발동시킨다.
    engine.on_entry(_long_intent(stop=90.0), now_ms=_DAY0)  # 수량 10
    # 100 → 40 청산: (40-100)*10 = -600 손실 → 한도 500 초과.
    exit_out = engine.on_exit(
        "BTC/USDT:USDT", "1h", exit_price=40.0, reason=SignalExitReason.STOP_LOSS, now_ms=_DAY0
    )
    assert exit_out.realized_pnl == pytest.approx(-600.0)
    # 같은 날 신규 진입은 서킷브레이커로 차단.
    blocked = engine.on_entry(_long_intent().model_copy(update={"timeframe": "4h"}), now_ms=_DAY0)
    assert not blocked.accepted
    assert "서킷브레이커" in blocked.reason
    # 다음 UTC 날에는 카운터가 리셋되어 다시 진입 가능.
    ok = engine.on_entry(
        _long_intent().model_copy(update={"timeframe": "4h"}), now_ms=_DAY0 + _DAY_MS
    )
    assert ok.accepted


def test_risk_manager_resets_daily_counter_on_new_day() -> None:
    rm = RiskManager(RiskParams(daily_loss_limit_fraction=0.05))
    rm.register_realized_pnl(-600.0, now_ms=_DAY0, equity=10_000.0)
    assert rm.circuit_breaker_tripped(_DAY0, 10_000.0)
    assert not rm.circuit_breaker_tripped(_DAY0 + _DAY_MS, 10_000.0)
    assert rm.daily_realized_pnl == pytest.approx(0.0)


def test_circuit_breaker_disabled_when_none() -> None:
    rm = RiskManager(RiskParams(daily_loss_limit_fraction=None))
    rm.register_realized_pnl(-9_999.0, now_ms=_DAY0, equity=10_000.0)
    assert not rm.circuit_breaker_tripped(_DAY0, 10_000.0)


# -- 재시도 · 부분체결 --------------------------------------------------------


class _FlakyBroker:
    """처음 `fail_times`번은 예외, 그 다음엔 정상 체결하는 브로커."""

    def __init__(self, fail_times: int) -> None:
        self._remaining = fail_times
        self.calls = 0

    def place_order(self, order: Order, *, mark_price: float | None = None) -> Fill:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise ConnectionError("일시적 네트워크 오류")
        price = mark_price if mark_price is not None else 0.0
        return Fill(
            order=order,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            average_price=price,
        )


def test_retry_recovers_from_transient_error() -> None:
    broker = _FlakyBroker(fail_times=2)
    engine = ExecutionEngine(
        broker=broker,
        risk_manager=RiskManager(RiskParams(max_leverage=100.0)),
        sizing_params=_sizing(),
        equity=10_000.0,
        max_retries=2,
    )
    outcome = engine.on_entry(_long_intent(), now_ms=_DAY0)
    assert outcome.accepted
    assert broker.calls == 3  # 2회 실패 + 1회 성공


def test_retry_exhaustion_rejects() -> None:
    broker = _FlakyBroker(fail_times=5)
    engine = ExecutionEngine(
        broker=broker,
        risk_manager=RiskManager(RiskParams(max_leverage=100.0)),
        sizing_params=_sizing(),
        equity=10_000.0,
        max_retries=2,
    )
    outcome = engine.on_entry(_long_intent(), now_ms=_DAY0)
    assert not outcome.accepted
    assert engine.position("BTC/USDT:USDT", "1h") is None


class _PartialBroker:
    """요청 수량의 절반만 체결하는 브로커(부분체결)."""

    def place_order(self, order: Order, *, mark_price: float | None = None) -> Fill:
        price = mark_price if mark_price is not None else (order.price or 0.0)
        return Fill(
            order=order,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=order.quantity / 2.0,
            average_price=price,
        )


def test_partial_fill_tracks_filled_quantity() -> None:
    engine = ExecutionEngine(
        broker=_PartialBroker(),
        risk_manager=RiskManager(RiskParams(max_leverage=100.0)),
        sizing_params=_sizing(),
        equity=10_000.0,
    )
    outcome = engine.on_entry(_long_intent(), now_ms=_DAY0)
    assert outcome.accepted
    assert outcome.position is not None
    # 사이징 10주 → 부분체결 5주만 포지션에 반영.
    assert outcome.position.quantity == pytest.approx(5.0)


# -- 안전: 기본 페이퍼, live_trading 가드 ------------------------------------


class _StubExecSettings:
    """`build_execution_engine`용 설정 스텁(Protocol 구조 만족)."""

    def __init__(self, *, live_trading: bool) -> None:
        self.live_trading = live_trading
        self.paper_equity = 5_000.0
        self.risk_limits = RiskParams(max_leverage=100.0)
        self.risk_sizing = _sizing()


def test_build_engine_defaults_to_paper_broker() -> None:
    engine = build_execution_engine(_StubExecSettings(live_trading=False))
    assert isinstance(engine._broker, PaperBroker)  # noqa: SLF001 - 안전성 검증
    assert not engine.live_trading
    assert engine.equity == pytest.approx(5_000.0)
    # 페이퍼 경로로 실제 진입까지 동작하는지(네트워크 없음).
    assert engine.on_entry(_long_intent(), now_ms=_DAY0).accepted


def test_build_engine_refuses_live_without_explicit_broker() -> None:
    with pytest.raises(RuntimeError, match="live_trading"):
        build_execution_engine(_StubExecSettings(live_trading=True))


def test_ccxt_live_broker_requires_live_flag() -> None:
    # live_trading=False로는 실거래 브로커를 만들 수 없다(실주문 API 접근 불가).
    with pytest.raises(RuntimeError, match="live_trading"):
        CcxtLiveBroker(exchange=object(), live_trading=False)


# -- 테스트넷 브로커 팩토리 (WAN-27) ------------------------------------------


class _StubExchange:
    """create_order를 기록하는 최소 ccxt 대역."""

    def __init__(self) -> None:
        self.orders: list[dict[str, object]] = []

    def create_order(
        self,
        *,
        symbol: str,
        type: str,  # noqa: A002 - ccxt 시그니처와 일치시킨다.
        side: str,
        amount: float,
        price: float | None,
        params: dict[str, object],
    ) -> dict[str, object]:
        self.orders.append({"symbol": symbol, "side": side, "amount": amount})
        return {"filled": amount, "average": 100.0, "price": 100.0}


def test_build_live_broker_refuses_without_live_trading() -> None:
    # 기본(live_trading=False)에서는 테스트넷이라도 실주문 브로커를 만들지 않는다.
    settings = Settings.model_validate({"use_testnet": True, "live_trading": False})
    with pytest.raises(RuntimeError, match="live_trading"):
        build_live_broker(settings, exchange=_StubExchange())


def test_build_live_broker_wires_injected_exchange() -> None:
    # live_trading=True면 주입한 (테스트넷) 거래소를 그대로 브로커에 배선한다.
    settings = Settings.model_validate({"use_testnet": True, "live_trading": True})
    stub = _StubExchange()
    broker = build_live_broker(settings, exchange=stub)
    assert isinstance(broker, CcxtLiveBroker)
    assert broker.exchange is stub
    # 브로커의 place_order가 주입한 거래소로 실제 주문을 낸다(WAN-9 로직 재사용).
    fill = broker.place_order(
        Order(symbol="BTC/USDT:USDT", side=OrderSide.BUY, type=OrderType.MARKET, quantity=1.0)
    )
    assert fill.status is OrderStatus.FILLED
    assert stub.orders[0]["symbol"] == "BTC/USDT:USDT"


def test_position_realized_pnl_helper() -> None:
    pos = Position(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        quantity=2.0,
        entry_price=100.0,
        entry_time=_DAY0,
    )
    assert pos.realized_pnl(110.0) == pytest.approx(20.0)
    assert pos.notional == pytest.approx(200.0)
