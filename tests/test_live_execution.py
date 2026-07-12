"""live.executor 테스트 — 시그널→페이퍼 집행→영속화, 재시작 복구, 실거래 안전.

청산 라운드트립은 WAN-33 성과 스키마(`paper_trades`)에 위임 기록되므로, 청산 검증은
`store.count()` / `store.list_records()`로 확인한다. 열린 포지션은 `open_positions`
테이블(`load_open_positions`)로 확인한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from config.settings import Settings
from execution.broker import PaperBroker
from execution.engine import EntryIntent, ExecutionEngine, build_execution_engine
from execution.risk import RiskManager
from execution.sizing import PositionSizingParams
from live.executor import PaperExecutor
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.models import OrderBlockDirection, SignalExitReason

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
_DAY0 = 1_700_000_000_000


def _long_intent(price: float = 100.0, stop: float = 95.0) -> EntryIntent:
    return EntryIntent(
        symbol=SYMBOL,
        timeframe=TF,
        direction=OrderBlockDirection.BULLISH,
        entry_price=price,
        entry_time=_DAY0,
        stop_price=stop,
        take_profit_price=110.0,
    )


@pytest.fixture
def store() -> Iterator[PaperTradeStore]:
    s = PaperTradeStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _make_executor(store: PaperTradeStore, *, equity: float = 10_000.0) -> PaperExecutor:
    sizing = PositionSizingParams()
    engine = ExecutionEngine(
        broker=PaperBroker(),
        risk_manager=RiskManager(),
        sizing_params=sizing,
        equity=equity,
    )
    # fee_rate=0으로 두어 손익률(gross=net) 검증을 단순화한다.
    recorder = PaperTradeRecorder(store, fee_rate=0.0)
    return PaperExecutor(engine=engine, store=store, recorder=recorder, sizing=sizing)


def test_enter_opens_and_persists(store: PaperTradeStore) -> None:
    executor = _make_executor(store)
    report = executor.enter(_long_intent(), now_ms=_DAY0)

    assert report.accepted
    assert report.outcome.position is not None
    assert report.risk_amount == pytest.approx(100.0)  # 10000 × 0.01
    assert len(executor.open_positions) == 1
    # DB에 열린 포지션이 남는다.
    persisted = store.load_open_positions()
    assert len(persisted) == 1
    assert persisted[0].position.quantity == pytest.approx(20.0)  # risk 100 / 손절거리 5


def test_exit_closes_persists_and_realizes_pnl(store: PaperTradeStore) -> None:
    executor = _make_executor(store)
    executor.enter(_long_intent(), now_ms=_DAY0)
    report = executor.exit(
        SYMBOL,
        TF,
        exit_price=110.0,
        exit_time=_DAY0 + 1,
        reason=SignalExitReason.TAKE_PROFIT,
        now_ms=_DAY0,
    )

    assert report.accepted
    assert report.outcome.realized_pnl == pytest.approx(200.0)  # (110-100) × 20
    assert executor.open_positions == []
    assert store.load_open_positions() == []
    # 청산 라운드트립은 성과 테이블(paper_trades)에 기록된다.
    records = store.list_records()
    assert len(records) == 1
    assert records[0].reason is SignalExitReason.TAKE_PROFIT
    assert records[0].gross_pct == pytest.approx(10.0)  # (110-100)/100
    assert records[0].entry_price == pytest.approx(100.0)
    assert records[0].exit_price == pytest.approx(110.0)


def test_restart_recovers_open_position(store: PaperTradeStore) -> None:
    """새 executor가 같은 저장소를 열면 열린 포지션이 엔진에 복구된다."""
    first = _make_executor(store)
    first.enter(_long_intent(), now_ms=_DAY0)
    assert len(first.open_positions) == 1

    # 재시작: 같은 저장소로 새 executor 생성.
    second = _make_executor(store)
    assert len(second.open_positions) == 1
    recovered = second.open_positions[0]
    assert recovered.symbol == SYMBOL
    assert recovered.quantity == pytest.approx(20.0)

    # 복구된 포지션을 청산할 수 있다(이력이 이어진다).
    report = second.exit(
        SYMBOL,
        TF,
        exit_price=110.0,
        exit_time=_DAY0 + 1,
        reason=SignalExitReason.TAKE_PROFIT,
        now_ms=_DAY0,
    )
    assert report.accepted
    assert store.load_open_positions() == []
    assert store.count() == 1


def test_rejected_entry_not_persisted(store: PaperTradeStore) -> None:
    """손절 참조가가 없으면 사이징 불가로 진입이 스킵되고 DB에 남지 않는다."""
    executor = _make_executor(store)
    intent = EntryIntent(
        symbol=SYMBOL,
        timeframe=TF,
        direction=OrderBlockDirection.BULLISH,
        entry_price=100.0,
        entry_time=_DAY0,
        stop_price=None,
    )
    report = executor.enter(intent, now_ms=_DAY0)
    assert not report.accepted
    assert report.risk_amount is None
    assert store.load_open_positions() == []


def test_paper_mode_uses_paper_broker_no_live_api() -> None:
    """live_trading=false면 build_execution_engine이 PaperBroker를 써 실주문 경로가 없다."""
    settings = Settings(live_trading=False)
    engine = build_execution_engine(settings)
    assert not engine.live_trading
    # 페이퍼 브로커로 집행 — 어떤 실주문 API도 호출하지 않는다.
    outcome = engine.on_entry(_long_intent(), now_ms=_DAY0)
    assert outcome.accepted
    assert outcome.fill is not None


def test_live_trading_true_requires_explicit_broker() -> None:
    """live_trading=true인데 브로커 미주입이면 실거래 경로를 자동 생성하지 않고 거부."""
    settings = Settings(live_trading=True)
    with pytest.raises(RuntimeError):
        build_execution_engine(settings)
