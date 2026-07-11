"""러너 ↔ execution 배선 통합 테스트 (WAN-34).

시그널 러너가 새 신호를 execution 엔진으로 집행해 페이퍼 포지션을 자동으로 열고
닫으며, 열린 포지션이 `open_positions`에 남고 재시작 시 복구되며, **청산
라운드트립이 WAN-33 성과 테이블(`paper_trades`)에 집계**되는지(리포트가 곧바로 읽을
수 있는지) 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from data.models import Candle
from data.storage import OhlcvStore
from execution.broker import PaperBroker
from execution.engine import ExecutionEngine
from execution.risk import RiskManager
from execution.sizing import PositionSizingParams
from live.executor import PaperExecutor
from live.notifier import Notifier
from live.runner import SignalRunner, WatermarkStore
from live.telegram import TelegramClient, TelegramResponse
from paper.performance import build_performance
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.confluence import ConfluenceResult, ConfluenceSignal, IndicatorSnapshot, SignalKind
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection, SignalExitReason

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
HOUR_MS = 3_600_000


class _StubStrategy:
    def __init__(self) -> None:
        self.result = ConfluenceResult(params=ConfluenceParams(), entries=[], exits=[])

    def run(self, df: pd.DataFrame, order_block_result: object | None = None) -> ConfluenceResult:
        return self.result


def _order_block() -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=105.0,
        bottom=95.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _entry(time: int, price: float = 100.0) -> ConfluenceSignal:
    return ConfluenceSignal(
        kind=SignalKind.ENTRY,
        direction=OrderBlockDirection.BULLISH,
        time=time,
        price=price,
        confirmed=True,
        rsi=25.0,
        order_block=_order_block(),
        indicators=IndicatorSnapshot(time=time, close=price, rsi=25.0, lines={"ema_20": 110.0}),
    )


def _exit(time: int, price: float = 110.0) -> ConfluenceSignal:
    return ConfluenceSignal(
        kind=SignalKind.EXIT,
        direction=OrderBlockDirection.BULLISH,
        time=time,
        price=price,
        confirmed=True,
        rsi=None,
        order_block=_order_block(),
        indicators=IndicatorSnapshot(time=time, close=price, rsi=None, lines={}),
        exit_reason=SignalExitReason.TAKE_PROFIT,
    )


def _candle(open_time: int, *, closed: bool = True) -> Candle:
    return Candle(SYMBOL, TF, open_time, 100.0, 105.0, 95.0, 100.0, 10.0, closed)


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, url: str, payload: dict[str, object]) -> TelegramResponse:
        self.messages.append(str(payload["text"]))
        return TelegramResponse(ok=True, status_code=200)


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _make_executor(trade_store: PaperTradeStore) -> PaperExecutor:
    sizing = PositionSizingParams()
    engine = ExecutionEngine(
        broker=PaperBroker(),
        risk_manager=RiskManager(),
        sizing_params=sizing,
        equity=10_000.0,
    )
    recorder = PaperTradeRecorder(trade_store, fee_rate=0.0)
    return PaperExecutor(engine=engine, store=trade_store, recorder=recorder, sizing=sizing)


def _make_runner(
    store: OhlcvStore,
    strategy: _StubStrategy,
    state_path: Path,
    recorder: _Recorder,
    executor: PaperExecutor,
) -> SignalRunner:
    notifier = Notifier(TelegramClient("t", "c", transport=recorder), executor=executor)
    return SignalRunner(
        store=store,
        strategy=strategy,  # type: ignore[arg-type]  # 덕타이핑 스텁
        notifier=notifier,
        state=WatermarkStore(state_path),
        series=[(SYMBOL, TF)],
        lookback_bars=1000,
        poll_interval_seconds=0.0,
        sleep=lambda _seconds: None,
        now_ms=lambda: 1_700_000_000_000,
    )


def test_signal_auto_opens_and_closes_paper_position(store: OhlcvStore, tmp_path: Path) -> None:
    trade_store = PaperTradeStore(tmp_path / "paper.db")
    executor = _make_executor(trade_store)
    strategy = _StubStrategy()
    recorder = _Recorder()
    runner = _make_runner(store, strategy, tmp_path / "state.json", recorder, executor)

    # 프라이밍(워터마크만 올림)
    store.upsert_candles([_candle(0)])
    runner.poll_once()

    # 진입 봉 → 페이퍼 포지션 자동 오픈 + open_positions 기록
    store.upsert_candles([_candle(HOUR_MS)])
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(), entries=[_entry(HOUR_MS)], exits=[]
    )
    runner.poll_once()
    assert len(executor.open_positions) == 1
    assert len(trade_store.load_open_positions()) == 1
    assert trade_store.count() == 0  # 아직 청산 전 → paper_trades 비어 있음
    assert any("페이퍼 집행" in m and "체결가" in m for m in recorder.messages)

    # 청산 봉 → 자동 청산 + 라운드트립이 성과 테이블(paper_trades)에 집계
    store.upsert_candles([_candle(2 * HOUR_MS)])
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(),
        entries=[_entry(HOUR_MS)],
        exits=[_exit(2 * HOUR_MS, price=110.0)],
    )
    runner.poll_once()
    assert executor.open_positions == []
    assert trade_store.load_open_positions() == []
    records = trade_store.list_records()
    assert len(records) == 1
    assert records[0].reason is SignalExitReason.TAKE_PROFIT
    assert records[0].gross_pct == pytest.approx(10.0)  # 100 → 110

    # WAN-33 성과 집계가 이 집행 결과를 그대로 읽는다(리포트 파이프라인 연결 확인).
    performance = build_performance(records)
    assert performance.overall.num_trades == 1
    assert performance.overall.num_wins == 1
    trade_store.close()


def test_restart_recovers_open_position_from_db(store: OhlcvStore, tmp_path: Path) -> None:
    db_path = tmp_path / "paper.db"
    state_path = tmp_path / "state.json"

    # 1) 진입까지 진행하고 포지션을 연 채로 "종료".
    trade_store1 = PaperTradeStore(db_path)
    executor1 = _make_executor(trade_store1)
    strategy = _StubStrategy()
    runner1 = _make_runner(store, strategy, state_path, _Recorder(), executor1)
    store.upsert_candles([_candle(0)])
    runner1.poll_once()  # 프라이밍
    store.upsert_candles([_candle(HOUR_MS)])
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(), entries=[_entry(HOUR_MS)], exits=[]
    )
    runner1.poll_once()
    assert len(executor1.open_positions) == 1
    trade_store1.close()

    # 2) 재시작: 같은 DB로 새 executor → 열린 포지션 복구.
    trade_store2 = PaperTradeStore(db_path)
    executor2 = _make_executor(trade_store2)
    assert len(executor2.open_positions) == 1
    assert executor2.open_positions[0].symbol == SYMBOL
    trade_store2.close()
