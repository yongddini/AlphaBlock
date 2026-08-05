"""WAN-246 §1: 웹소켓 틱 피드로 대기 지정가 체결을 앞당긴다 (페이퍼 한정, 옵트인).

라벨이 아니라 **동작**으로 고정한다:

- 기본값(피드 없음/`NullPriceFeed`)은 예전과 같다 — 틱을 안 내면 러너가 확정 1분봉만 소비.
- `ZoneLimitLiveEngine.on_tick`이 이미 걸린 주문을 체결하고(예약은 안 함), 대기 주문이
  없으면 부수효과 없이 빈 목록.
- 러너 `_drain_ticks`가 심볼 틱을 그 심볼의 각 (symbol, timeframe) 시리즈에 팬아웃하고,
  체결 이벤트를 1분봉 경로와 **같은** `_handle_events`로 집행해 페이퍼 진입을 낸다.
- `CcxtProTickFeed`가 주입 거래소로 틱을 큐에 쌓고 `drain`으로 비우며 `close`로 정리한다
  (네트워크 없이 소비 루프·큐·수명주기를 검증).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from config.settings import Settings
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.limit_orders import PendingLimitOrder
from live.order_journal import OrderJournal
from live.price_feed import CcxtProTickFeed, NullPriceFeed, SymbolTick
from live.zone_limit_runner import ZoneLimitPaperRunner
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.models import ConfluenceParams, OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

_SYMBOL = "BTC/USDT:USDT"


def _static_long_order(symbol: str = _SYMBOL, timeframe: str = "1h") -> PendingLimitOrder:
    """상수 지정가 롱 주문(존 상단 100, 손절 90, 익절 115) — 결정론적 체결 판정용."""
    return PendingLimitOrder(
        symbol=symbol,
        timeframe=timeframe,
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        take_profit_price=115.0,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=0,
    )


# -- 엔진 on_tick -------------------------------------------------------------


def test_engine_on_tick_fills_pending_order() -> None:
    """이미 걸린 주문을 틱이 체결하고 filled 이벤트를 낸다."""
    engine = ZoneLimitLiveEngine(params=ConfluenceParams())
    engine.book.place(_static_long_order())
    events = engine.on_tick(_SYMBOL, "1h", price=99.0, time_ms=5)
    assert len(events) == 1
    assert events[0].kind == "filled"
    assert events[0].fill is not None
    assert events[0].fill.price == 100.0
    # 체결되면 장부에서 제거된다.
    assert engine.book.pending(_SYMBOL, "1h") is None


def test_engine_on_tick_no_pending_is_noop() -> None:
    """대기 주문이 없으면 틱은 부수효과 없이 빈 목록 — 상태도 만들지 않는다."""
    engine = ZoneLimitLiveEngine(params=ConfluenceParams())
    assert engine.on_tick(_SYMBOL, "1h", price=99.0, time_ms=5) == []


def test_engine_on_tick_does_not_arm() -> None:
    """틱은 예약(arming)을 하지 않는다 — 존을 안 줬으니 걸린 주문이 없으면 아무 일도 없다."""
    engine = ZoneLimitLiveEngine(params=ConfluenceParams())
    # 존 대장을 세우지 않고 틱만 넣어도 주문이 생기지 않는다.
    engine.on_tick(_SYMBOL, "1h", price=50.0, time_ms=1)
    assert engine.book.open_orders == []


# -- CcxtProTickFeed (주입 거래소, 네트워크 없음) ------------------------------


class _FakeExchange:
    """ccxt.pro 인터페이스 스텁 — watch_trades로 정해진 체결을 한 번씩 내고 이후 블록."""

    def __init__(self, trades_by_symbol: dict[str, list[list[dict[str, Any]]]]) -> None:
        self._trades = trades_by_symbol
        self._idx = {s: 0 for s in trades_by_symbol}
        self.closed = False

    async def watch_trades(self, symbol: str) -> list[dict[str, Any]]:
        i = self._idx[symbol]
        batches = self._trades[symbol]
        if i < len(batches):
            self._idx[symbol] += 1
            return batches[i]
        await asyncio.Event().wait()  # 더 없으면 취소될 때까지 블록.
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True


def test_ccxtpro_feed_delivers_ticks_and_closes() -> None:
    """주입 거래소의 체결이 SymbolTick으로 큐에 쌓이고 drain으로 비워지며 close가 정리한다."""
    ex = _FakeExchange(
        {
            _SYMBOL: [
                [
                    {"price": 100.0, "timestamp": 1},
                    {"price": 101.5, "timestamp": 2},
                ]
            ]
        }
    )
    feed = CcxtProTickFeed([_SYMBOL], exchange=ex)
    feed.start()
    try:
        ticks: list[SymbolTick] = []
        deadline = time.time() + 5.0
        while time.time() < deadline and len(ticks) < 2:
            ticks += feed.drain()
            time.sleep(0.02)
    finally:
        feed.close()

    assert len(ticks) == 2
    assert ticks[0] == SymbolTick(symbol=_SYMBOL, price=100.0, time_ms=1)
    assert ticks[1] == SymbolTick(symbol=_SYMBOL, price=101.5, time_ms=2)
    assert ex.closed is True


def test_null_feed_drains_nothing() -> None:
    feed = NullPriceFeed()
    assert feed.drain() == []
    feed.close()  # 부작용 없음.


# -- 러너 _drain_ticks 팬아웃 + 통합 진입 --------------------------------------


class _FakeFeed:
    def __init__(self, ticks: list[SymbolTick]) -> None:
        self._ticks = list(ticks)
        self.closed = False

    def drain(self) -> list[SymbolTick]:
        out = self._ticks
        self._ticks = []
        return out

    def close(self) -> None:
        self.closed = True


def _build_runner(
    tmp_path: Path,
    *,
    series: list[tuple[str, str]],
    price_feed: object | None = None,
) -> tuple[ZoneLimitPaperRunner, PaperExecutor, OrderJournal, OhlcvStore]:
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    settings = Settings(db_path=str(db))
    params = ConfluenceParams(max_zone_width_atr=None)
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    paper_store = PaperTradeStore(db)
    recorder = PaperTradeRecorder(paper_store, cost_model=settings.costs, funding_store=None)
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper_store,
        recorder=recorder,
        sizing=settings.risk_sizing,
    )
    engine = ZoneLimitLiveEngine(
        params=params,
        journal=journal,
        session_id=session,
        has_position=lambda s, t: any(
            p.symbol == s and p.timeframe == t for p in executor.open_positions
        ),
    )
    runner = ZoneLimitPaperRunner(
        store=store,
        engine=engine,
        journal=journal,
        session_id=session,
        executor=executor,
        params=params,
        series=series,
        lookback_bars=500,
        poll_interval_seconds=1.0,
        price_feed=price_feed,  # type: ignore[arg-type]
        now_ms=lambda: 999_999,
    )
    return runner, executor, journal, store


class _SpyEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int]] = []

    def on_tick(self, symbol: str, timeframe: str, *, price: float, time_ms: int) -> list[object]:
        self.calls.append((symbol, timeframe, price, time_ms))
        return []


def test_drain_ticks_fans_out_to_matching_series(tmp_path: Path) -> None:
    """심볼 틱이 그 심볼의 각 (symbol, timeframe) 시리즈로 팬아웃하고 다른 심볼은 건너뛴다."""
    feed = _FakeFeed([SymbolTick(_SYMBOL, 100.0, 5)])
    runner, _executor, _journal, store = _build_runner(
        tmp_path,
        series=[(_SYMBOL, "1h"), (_SYMBOL, "2h"), ("ETH/USDT:USDT", "1h")],
        price_feed=feed,
    )
    try:
        spy = _SpyEngine()
        runner._engine = spy  # type: ignore[assignment]
        events = runner._drain_ticks()
        assert events == []
        # BTC의 두 시리즈에만 팬아웃, ETH는 건너뛴다.
        assert spy.calls == [(_SYMBOL, "1h", 100.0, 5), (_SYMBOL, "2h", 100.0, 5)]
    finally:
        store.close()
        _journal.close()


def test_no_feed_drains_nothing(tmp_path: Path) -> None:
    """피드가 없으면 _drain_ticks는 빈 목록(기본 동작 불변)."""
    runner, _executor, journal, store = _build_runner(
        tmp_path, series=[(_SYMBOL, "1h")], price_feed=None
    )
    try:
        assert runner._drain_ticks() == []
    finally:
        store.close()
        journal.close()


def test_tick_drives_paper_entry_through_poll_once(tmp_path: Path) -> None:
    """틱이 대기 주문을 체결해 poll_once에서 페이퍼 포지션이 열린다(완료 기준 1).

    예약은 1분봉 경로가 하지만, 여기서는 이미 걸린 주문을 틱이 체결하는 경로만 격리해
    검증한다 — 주문을 직접 장부에 걸고, 저장소엔 1분봉이 없어 폴링은 무동작이며, 피드의
    틱이 유일한 체결 트리거다.
    """
    feed = _FakeFeed([SymbolTick(_SYMBOL, 99.0, 999_000)])  # 지정가 100에 닿는다.
    runner, executor, journal, store = _build_runner(
        tmp_path, series=[(_SYMBOL, "1h")], price_feed=feed
    )
    try:
        runner._engine.book.place(_static_long_order())
        assert executor.open_positions == []

        runner.poll_once()

        positions = executor.open_positions
        assert len(positions) == 1
        assert positions[0].symbol == _SYMBOL
        assert positions[0].entry_price == 100.0
        assert positions[0].stop_price == 90.0
    finally:
        store.close()
        journal.close()
