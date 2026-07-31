"""레버리지 북을 실시간 페이퍼 러너에 배선한 것의 통합 테스트 (WAN-171).

`ZoneLimitPaperRunner`가 한 `PaperExecutor`(공유 자본·공유 `equity`) 위에서 여러
(종목,TF) 지정가를 관리한다. 여기서 저장소(실제 SQLite) + 합성 틱으로 고정하는 것:

* **여러 칸이 한 지갑을 공유하며 예약·체결·청산**된다(완료 기준 1).
* **N=1(중립) 북 = 북 없는 러너**(단일 시리즈 회귀 — 완료 기준 4).
* **straddle(b) 비점유** — 러너 프라이밍이 과거 탭을 재생하지 않으므로 워밍업 탭이
  지갑·칸을 점유하지 않는다(완료 기준 2의 실시간판).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from config.settings import Settings
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from execution.leverage import LEGACY_BOOK_PARAMS, LeverageBookParams, book_per_trade_sizing
from live import limit_engine
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.zone_limit_runner import ZoneLimitPaperRunner
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
)

_H = 3_600_000
_M = 60_000
_TF = "1h"
_N_CLOSED = 30
_FORMING = _N_CLOSED * _H


def _zone() -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=95.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=_H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _install_stub_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    """모든 심볼에 같은 존 하나를 내는 스텁 — 존 탐지는 이 테스트의 관심이 아니다."""
    result = OrderBlockResult(order_blocks=[_zone()], signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)


def _htf(symbol: str, i: int, close: float = 100.0) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=_TF,
        open_time=i * _H,
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=1.0,
        closed=True,
    )


def _m1(symbol: str, t: int, low: float, high: float, close: float) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe="1m",
        open_time=t,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        closed=True,
    )


def _build_runner(
    *,
    db: Path,
    symbols: list[str],
    book: LeverageBookParams | None,
) -> tuple[ZoneLimitPaperRunner, PaperExecutor, OhlcvStore, OrderJournal, PaperTradeStore]:
    store = OhlcvStore(db)
    for symbol in symbols:
        store.upsert_candles([_htf(symbol, i) for i in range(_N_CLOSED)])
    settings = Settings(db_path=str(db))
    params = ConfluenceParams(max_zone_width_atr=None)  # 합성 데이터 ATR로는 존이 걸러진다.
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    paper_store = PaperTradeStore(db)
    recorder = PaperTradeRecorder(paper_store, cost_model=settings.costs, funding_store=None)
    executor = PaperExecutor(
        engine=build_execution_engine(settings, leverage_book=book),
        store=paper_store,
        recorder=recorder,
        sizing=(
            book_per_trade_sizing(settings.risk_sizing, book)
            if book is not None
            else settings.risk_sizing
        ),
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
        series=[(symbol, _TF) for symbol in symbols],
        lookback_bars=500,
        poll_interval_seconds=1.0,
        now_ms=lambda: 999_999,
    )
    return runner, executor, store, journal, paper_store


# --------------------------------------------------------------------------- #
# 완료 기준 1: 여러 칸이 한 지갑을 공유하며 예약·체결·청산
# --------------------------------------------------------------------------- #


def test_two_cells_share_one_wallet_fill_and_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BTC·ETH 두 칸이 한 `PaperExecutor`(공유 자본)에서 각각 체결·청산된다(채택 북 기본값)."""
    _install_stub_detector(monkeypatch)
    db = tmp_path / "ohlcv.db"
    runner, executor, store, journal, paper_store = _build_runner(
        db=db, symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"], book=LeverageBookParams()
    )
    try:
        # 두 심볼 모두 존을 탭·터치하는 1분봉 → 같은 폴링에서 예약·체결.
        for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
            store.upsert_candles([_m1(symbol, _FORMING + _M, 94.9, 99.0, 95.2)])
        runner.poll_once()

        positions = executor.open_positions
        assert {p.symbol for p in positions} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
        # 공유 지갑: 열린 명목 합 > 한 칸의 명목(둘이 한 자본을 나눠 썼다).
        assert executor._engine.open_notional > positions[0].notional
        # 두 칸 모두 체결이 장부에 남았다(칸별 체결률 실측).
        assert sum(s.filled for s in journal.fill_stats()) == 2

        # 두 칸 모두 익절 관통 → 청산. 공유 자본이 두 실현손익을 모두 반영한다.
        for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
            store.upsert_candles([_m1(symbol, _FORMING + 2 * _M, 95.0, 103.5, 103.0)])
        runner.poll_once()
        assert executor.open_positions == []
        assert paper_store.count("BTC/USDT:USDT", _TF) == 1
        assert paper_store.count("ETH/USDT:USDT", _TF) == 1
    finally:
        store.close()
        journal.close()
        paper_store.close()


# --------------------------------------------------------------------------- #
# 완료 기준 4: N=1(중립) 북 = 북 없는 러너 (단일 시리즈 회귀)
# --------------------------------------------------------------------------- #


def test_neutral_book_runner_matches_no_book_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """단일 시리즈에서 `LEGACY_BOOK_PARAMS`(배수1) 러너는 북 없는 러너와 같은 거래를 낸다."""
    _install_stub_detector(monkeypatch)

    def _run(db: Path, book: LeverageBookParams | None) -> tuple[float, float]:
        runner, executor, store, journal, paper_store = _build_runner(
            db=db, symbols=["BTC/USDT:USDT"], book=book
        )
        try:
            store.upsert_candles([_m1("BTC/USDT:USDT", _FORMING + _M, 94.9, 99.0, 95.2)])
            runner.poll_once()
            qty = executor.open_positions[0].quantity
            store.upsert_candles([_m1("BTC/USDT:USDT", _FORMING + 2 * _M, 95.0, 103.5, 103.0)])
            runner.poll_once()
            return qty, executor.equity
        finally:
            store.close()
            journal.close()
            paper_store.close()

    qty_none, equity_none = _run(tmp_path / "nobook.db", None)
    qty_book, equity_book = _run(tmp_path / "book.db", LEGACY_BOOK_PARAMS)
    assert qty_book == pytest.approx(qty_none)
    assert equity_book == pytest.approx(equity_none)


# --------------------------------------------------------------------------- #
# 완료 기준 2(실시간판): straddle(b) 비점유 — 프라이밍이 과거 탭을 재생하지 않는다
# --------------------------------------------------------------------------- #


def test_warmup_tap_before_priming_does_not_occupy_wallet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """프라이밍 이전(과거)에 존을 탭한 1분봉은 예약·체결되지 않아 지갑을 점유하지 않는다.

    러너는 `since = 마지막확정봉 + htf − 1m`(형성 중 상위TF 봉 시작)부터만 1분봉을
    소비한다 — 그 전 탭을 재생하면 이미 지나간 자리에 뒷북 주문을 건다(측정 오염). 이것이
    백테스트 straddle 회계 (b)의 실시간판이다: 경계 이전 워밍업 탭은 자본·칸을 안 잡는다.
    """
    _install_stub_detector(monkeypatch)
    db = tmp_path / "ohlcv.db"
    runner, executor, store, journal, paper_store = _build_runner(
        db=db, symbols=["BTC/USDT:USDT"], book=LeverageBookParams()
    )
    try:
        # 프라이밍 경계(_FORMING) 한참 이전에 존을 관통하는 1분봉 — 재생되면 체결됐을 자리.
        store.upsert_candles([_m1("BTC/USDT:USDT", 5 * _M, 94.0, 99.0, 95.0)])
        runner.poll_once()
        # 뒷북 주문이 없다: 포지션도, 지갑 점유도 없다.
        assert executor.open_positions == []
        assert executor._engine.open_notional == 0.0
    finally:
        store.close()
        journal.close()
        paper_store.close()
