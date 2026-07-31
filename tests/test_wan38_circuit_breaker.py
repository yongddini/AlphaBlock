"""일일 손실 서킷브레이커 — DB 재계산·KST 경계·재시작 내구성 (WAN-38).

이 파일이 고정하는 계약:

1. **DB 재계산** — `realized_pnl_source`를 물리면 서킷브레이커가 인메모리 누적이 아니라
   원장(`paper_trades`)의 "오늘(KST) 청산 손익 합"으로 판정한다.
2. **재시작 내구성** — 새 `RiskManager`/`PaperExecutor`(인메모리 0)를 같은 원장으로 열어도
   당일 손실이 한도를 넘었으면 차단이 유지된다.
3. **KST 일자 경계** — 하루가 지나면(KST) 차단이 자동 해제된다.
4. **알림 상태 영속** — 발동/해제 알림 중복 방지 상태가 원장에 남아 재시작을 견딘다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.timefmt import kst_day_bounds
from execution.broker import PaperBroker
from execution.engine import EntryIntent, ExecutionEngine
from execution.risk import RealizedPnlSource, RiskManager, RiskParams
from execution.sizing import PositionSizingParams
from live.executor import PaperExecutor
from live.paper import ClosedTrade, PaperPosition
from paper.store import (
    PaperTradeRecord,
    PaperTradeRecorder,
    PaperTradeStore,
    TradeDollars,
    build_record,
)
from strategy.models import OrderBlockDirection, SignalExitReason

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
_DAY0 = 1_700_000_000_000  # 2023-11-14 근처(ms).
_DAY_MS = 86_400_000
_LIMIT_5PCT = RiskParams(daily_loss_limit_fraction=0.05, max_concurrent_positions=5)


# -- RiskManager: DB 소스 판정 -------------------------------------------------


def _fixed_source(value: float) -> RealizedPnlSource:
    return lambda _start, _end: value


def test_circuit_breaker_reads_daily_loss_from_db_source() -> None:
    realized = {"v": 0.0}
    rm = RiskManager(_LIMIT_5PCT, realized_pnl_source=lambda _s, _e: realized["v"])

    # 손실 없음 → 미발동.
    assert not rm.circuit_breaker_tripped(_DAY0, 10_000.0)
    # DB가 오늘 −600을 돌려주면(기준자본 10_000 × 5% = 500 초과) 발동.
    realized["v"] = -600.0
    assert rm.circuit_breaker_tripped(_DAY0, 9_400.0)
    # 판정 훅이 DB 값으로 동기화하므로 누적 손익 조회도 −600.
    assert rm.daily_realized_pnl == pytest.approx(-600.0)


def test_circuit_breaker_survives_restart_via_db_source() -> None:
    # 새로 만든(인메모리 0) RiskManager라도 원장이 오늘 손실을 돌려주면 여전히 발동.
    rm = RiskManager(_LIMIT_5PCT, realized_pnl_source=_fixed_source(-600.0))
    assert rm.circuit_breaker_tripped(_DAY0, 9_400.0)


def test_circuit_breaker_clears_on_next_kst_day() -> None:
    # 소스는 _DAY0의 KST 창에서만 −600을 돌려준다 — 다음 KST일 창은 비었다.
    day0_bounds = kst_day_bounds(_DAY0)

    def source(start: int, end: int) -> float:
        return -600.0 if (start, end) == day0_bounds else 0.0

    rm = RiskManager(_LIMIT_5PCT, realized_pnl_source=source)
    assert rm.circuit_breaker_tripped(_DAY0, 9_400.0)
    assert not rm.circuit_breaker_tripped(_DAY0 + _DAY_MS, 9_400.0)


def test_status_reports_amounts() -> None:
    rm = RiskManager(_LIMIT_5PCT, realized_pnl_source=_fixed_source(-600.0))
    status = rm.status(_DAY0, 9_400.0)
    assert status.enabled and status.tripped
    assert status.daily_realized_pnl == pytest.approx(-600.0)
    assert status.loss_limit == pytest.approx(500.0)  # baseline 10_000 × 5%.
    assert status.baseline_equity == pytest.approx(10_000.0)  # 9_400 − (−600).


def test_status_disabled_when_limit_none() -> None:
    rm = RiskManager(RiskParams(daily_loss_limit_fraction=None))
    status = rm.status(_DAY0, 10_000.0)
    assert not status.enabled
    assert not status.tripped
    assert status.loss_limit == pytest.approx(0.0)


def test_source_can_be_unbound_back_to_in_memory() -> None:
    rm = RiskManager(_LIMIT_5PCT, realized_pnl_source=_fixed_source(-600.0))
    assert rm.circuit_breaker_tripped(_DAY0, 9_400.0)
    rm.bind_realized_pnl_source(None)  # 인메모리로 되돌림.
    # 다음 KST일: 인메모리 카운터가 리셋되고 소스가 없어 재계산도 없다 → 미발동.
    assert not rm.circuit_breaker_tripped(_DAY0 + _DAY_MS, 9_400.0)


# -- PaperTradeStore: 서킷브레이커 지원 조회 -----------------------------------


def _record(*, exit_time: int, entry_time: int, dollars: TradeDollars | None) -> PaperTradeRecord:
    trade = ClosedTrade(
        position=PaperPosition(
            symbol=SYMBOL,
            timeframe=TF,
            direction=OrderBlockDirection.BULLISH,
            entry_time=entry_time,
            entry_price=100.0,
            stop_price=95.0,
            take_profit_price=110.0,
        ),
        exit_time=exit_time,
        exit_price=70.0,
        reason=SignalExitReason.STOP_LOSS,
    )
    return build_record(trade, fee_rate=0.0, dollars=dollars)


def test_realized_pnl_between_sums_window_and_skips_null() -> None:
    with PaperTradeStore(":memory:") as store:
        store.upsert_record(
            _record(
                exit_time=5_000,
                entry_time=1_000,
                dollars=TradeDollars(realized_pnl=-600.0, equity_after=9_400.0),
            )
        )
        store.upsert_record(
            _record(
                exit_time=15_000,
                entry_time=1_001,
                dollars=TradeDollars(realized_pnl=100.0, equity_after=9_500.0),
            )
        )
        # 옛 %-only 행(realized_pnl NULL)은 합에서 빠진다.
        store.upsert_record(_record(exit_time=6_000, entry_time=1_002, dollars=None))

        assert store.realized_pnl_between(0, 10_000) == pytest.approx(-600.0)
        assert store.realized_pnl_between(0, 20_000) == pytest.approx(-500.0)
        assert store.realized_pnl_between(20_000, 30_000) == pytest.approx(0.0)


def test_latest_equity_after_picks_latest_exit() -> None:
    with PaperTradeStore(":memory:") as store:
        assert store.latest_equity_after() is None
        store.upsert_record(
            _record(
                exit_time=5_000,
                entry_time=1_000,
                dollars=TradeDollars(realized_pnl=-600.0, equity_after=9_400.0),
            )
        )
        store.upsert_record(
            _record(
                exit_time=15_000,
                entry_time=1_001,
                dollars=TradeDollars(realized_pnl=100.0, equity_after=9_500.0),
            )
        )
        assert store.latest_equity_after() == pytest.approx(9_500.0)


def test_circuit_breaker_notice_roundtrip_and_persists(tmp_path: Path) -> None:
    db = tmp_path / "paper.db"
    with PaperTradeStore(db) as store:
        assert store.get_circuit_breaker_notice() == (None, False)
        store.set_circuit_breaker_notice("2026-07-22", tripped=True)
        assert store.get_circuit_breaker_notice() == ("2026-07-22", True)
    # 재시작(같은 파일 재오픈) 후에도 상태가 남는다.
    with PaperTradeStore(db) as store2:
        assert store2.get_circuit_breaker_notice() == ("2026-07-22", True)
        store2.set_circuit_breaker_notice("2026-07-23", tripped=False)
    with PaperTradeStore(db) as store3:
        assert store3.get_circuit_breaker_notice() == ("2026-07-23", False)


# -- PaperExecutor: 통합 재시작 내구성 -----------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "paper.db"


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


def _make_executor(store: PaperTradeStore, *, equity: float = 10_000.0) -> PaperExecutor:
    sizing = PositionSizingParams()
    engine = ExecutionEngine(
        broker=PaperBroker(),
        risk_manager=RiskManager(_LIMIT_5PCT),
        sizing_params=sizing,
        equity=equity,
    )
    recorder = PaperTradeRecorder(store, fee_rate=0.0)
    return PaperExecutor(engine=engine, store=store, recorder=recorder, sizing=sizing)


def test_executor_circuit_breaker_survives_restart(db_path: Path) -> None:
    store = PaperTradeStore(db_path)
    ex1 = _make_executor(store)
    ex1.enter(_long_intent(), now_ms=_DAY0)
    # 100 → 70 청산: (70-100)×20 = −600 손실 → 한도 500 초과.
    exit_report = ex1.exit(
        SYMBOL,
        TF,
        exit_price=70.0,
        exit_time=_DAY0 + 1,
        reason=SignalExitReason.STOP_LOSS,
        now_ms=_DAY0,
    )
    assert exit_report.outcome.realized_pnl == pytest.approx(-600.0)
    assert ex1.circuit_breaker_status(_DAY0).tripped
    blocked = ex1.enter(_long_intent().model_copy(update={"timeframe": "4h"}), now_ms=_DAY0)
    assert not blocked.accepted and "서킷브레이커" in blocked.outcome.reason
    store.close()

    # 재시작: 인메모리 카운터가 리셋된 새 executor라도 원장에서 오늘 손실을 다시 읽어 차단 유지.
    store2 = PaperTradeStore(db_path)
    ex2 = _make_executor(store2)
    assert ex2.circuit_breaker_status(_DAY0).tripped
    still_blocked = ex2.enter(_long_intent().model_copy(update={"timeframe": "4h"}), now_ms=_DAY0)
    assert not still_blocked.accepted and "서킷브레이커" in still_blocked.outcome.reason

    # 다음 KST 일자에는 오늘 창이 비어 차단이 자동 해제된다.
    ok = ex2.enter(_long_intent().model_copy(update={"timeframe": "4h"}), now_ms=_DAY0 + _DAY_MS)
    assert ok.accepted
    store2.close()
