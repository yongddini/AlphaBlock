"""재시작 시 누적 북 자본 복원 — 사이징이 손실/이익을 잇는다 (WAN-238).

버그: 러너가 재시작될 때마다 엔진 자본이 초기값(`settings.paper_equity`=10,000)으로
리셋돼, 다음 거래가 손실을 모르는 초기 자본에서 사이징됐다
(`risk_amount = equity × risk_per_trade`). WAN-237이 **표시**를 고쳤고, 이 이슈는
**사이징 기준**(엔진 자본)을 원장에서 복원한다.

이 파일이 고정하는 계약(완료 기준):

1. 손절 1건 후 재시작 → 다음 거래의 `risk_amount`가 **줄어든 자본** 기준(리셋 10,000 아님).
2. 재시작 전후 지갑 자본이 이어짐(초기자본 + Σrealized_pnl = `equity_after` 체인).
3. 복원 단위가 **북 전체 자본**(전 칸 실현손익 합)이지 칸별 독립이 아니다(WAN-213 공유 지갑).
4. 옛 %-only 장부(realized_pnl NULL)가 섞이면 **초기 자본으로 안전 폴백**(부분 복원 안 함).
5. 재시작 경계 오픈 포지션의 미실현 손익을 자본 복원이 **이중 계산하지 않는다**.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from execution.broker import PaperBroker
from execution.engine import EntryIntent, ExecutionEngine
from execution.risk import RiskManager
from execution.sizing import PositionSizingParams
from live.executor import PaperExecutor
from live.paper import ClosedTrade, PaperPosition
from paper.store import (
    PaperTradeRecord,
    PaperTradeRecorder,
    PaperTradeStore,
    build_record,
)
from strategy.models import OrderBlockDirection, SignalExitReason

_DAY0 = 1_700_000_000_000
_INITIAL = 10_000.0
_BTC = "BTC/USDT:USDT"
_ETH = "ETH/USDT:USDT"
_TF = "1h"


# -- 헬퍼 ----------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "paper.db"


def _intent(
    *,
    symbol: str = _BTC,
    timeframe: str = _TF,
    price: float = 100.0,
    stop: float = 95.0,
    tp: float = 200.0,
) -> EntryIntent:
    return EntryIntent(
        symbol=symbol,
        timeframe=timeframe,
        direction=OrderBlockDirection.BULLISH,
        entry_price=price,
        entry_time=_DAY0,
        stop_price=stop,
        take_profit_price=tp,
    )


def _make_executor(store: PaperTradeStore, *, equity: float = _INITIAL) -> PaperExecutor:
    """프로덕션 러너와 같은 조립(레버리지 북 없는 경로 — 자본 회계는 북 유무와 무관).

    자본(`_equity`)은 두 모드 모두 한 엔진에 **공유**되므로, 북 없는 경로에서 여러 칸이
    같은 자본에 누적되는 것을 보이면 그대로 북 전체 자본 복원을 증명한다(WAN-213).
    """
    sizing = PositionSizingParams()
    engine = ExecutionEngine(
        broker=PaperBroker(),
        risk_manager=RiskManager(),
        sizing_params=sizing,
        equity=equity,
    )
    recorder = PaperTradeRecorder(store, fee_rate=0.0)  # 브로커 수수료 0 → gross=net.
    return PaperExecutor(engine=engine, store=store, recorder=recorder, sizing=sizing)


def _round_trip(executor: PaperExecutor, intent: EntryIntent, *, exit_price: float) -> float:
    """진입→청산 라운드트립을 집행하고 실현손익을 돌려준다."""
    executor.enter(intent, now_ms=_DAY0)
    report = executor.exit(
        intent.symbol,
        intent.timeframe,
        exit_price=exit_price,
        exit_time=_DAY0 + 1,
        reason=SignalExitReason.STOP_LOSS,
        now_ms=_DAY0,
    )
    assert report.outcome.realized_pnl is not None
    return report.outcome.realized_pnl


def _legacy_null_record(*, exit_time: int) -> PaperTradeRecord:
    """옛 %-only 장부 행 — 금액(`realized_pnl`·`equity_after`)이 NULL (WAN-207 이전)."""
    trade = ClosedTrade(
        position=PaperPosition(
            symbol=_BTC,
            timeframe=_TF,
            direction=OrderBlockDirection.BULLISH,
            entry_time=_DAY0,
            entry_price=100.0,
            stop_price=95.0,
            take_profit_price=110.0,
        ),
        exit_time=exit_time,
        exit_price=98.0,
        reason=SignalExitReason.STOP_LOSS,
    )
    return build_record(trade, fee_rate=0.0, dollars=None)


# -- 완료기준 1·2: 손절 후 재시작 → 줄어든 자본 기준 사이징 ------------------------


def test_restart_after_loss_sizes_from_reduced_equity(db_path: Path) -> None:
    store = PaperTradeStore(db_path)
    ex1 = _make_executor(store)
    # 진입가 100, 손절 95 → qty 20. 청산가 98 → 실현 (98-100)×20 = −40.
    realized = _round_trip(ex1, _intent(), exit_price=98.0)
    assert realized == pytest.approx(-40.0)
    assert ex1.equity == pytest.approx(_INITIAL - 40.0)
    store.close()

    # 재시작: 새 executor가 초기 자본(10,000)으로 시드된 뒤 원장에서 자본을 복원한다.
    store2 = PaperTradeStore(db_path)
    ex2 = _make_executor(store2)
    # 완료기준 2: 재시작 전후 자본이 이어진다 = 초기 + Σrealized = equity_after 체인.
    assert ex2.equity == pytest.approx(9_960.0)
    assert ex2.equity == pytest.approx(_INITIAL + (store2.total_realized_pnl() or 0.0))
    assert ex2.equity == pytest.approx(store2.latest_equity_after())

    # 완료기준 1: 다음 거래의 리스크 금액이 **줄어든 자본**(9,960) 기준이다 — 리셋 10,000 아님.
    report = ex2.enter(_intent(), now_ms=_DAY0)
    assert report.risk_amount == pytest.approx(99.6)  # 9,960 × 0.01
    assert report.risk_amount != pytest.approx(100.0)  # 리셋됐다면 10,000 × 0.01.
    store2.close()


# -- 완료기준 3: 복원 단위 = 북 전체 자본(전 칸 실현손익 합) --------------------------


def test_restart_restores_book_wide_equity_not_per_cell(db_path: Path) -> None:
    store = PaperTradeStore(db_path)
    ex1 = _make_executor(store)
    # 서로 다른 칸(BTC·ETH)이 한 지갑을 공유한다 — 순차로 각각 청산.
    r_btc = _round_trip(ex1, _intent(symbol=_BTC, price=100.0, stop=95.0), exit_price=98.0)
    r_eth = _round_trip(ex1, _intent(symbol=_ETH, price=200.0, stop=190.0), exit_price=230.0)
    assert r_btc < 0.0 < r_eth  # 한 칸은 손실, 한 칸은 이익.
    assert store.count() == 2
    store.close()

    store2 = PaperTradeStore(db_path)
    ex2 = _make_executor(store2)
    # 자본은 **두 칸의 합**이다 — 마지막 칸(ETH)만도, 첫 칸(BTC)만도 아니다.
    assert ex2.equity == pytest.approx(_INITIAL + r_btc + r_eth)
    assert ex2.equity != pytest.approx(_INITIAL + r_eth)  # 칸별 독립이면 이 값이 됐을 것.
    assert ex2.equity != pytest.approx(_INITIAL + r_btc)
    assert store2.total_realized_pnl() == pytest.approx(r_btc + r_eth)
    store2.close()


# -- 완료기준 4: 옛 %-only 장부 → 초기 자본으로 안전 폴백 ---------------------------


def test_legacy_null_ledger_falls_back_to_initial_equity(
    db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = PaperTradeStore(db_path)
    store.upsert_record(_legacy_null_record(exit_time=_DAY0 + 1))
    assert store.total_realized_pnl() is None  # 금액 미상 → 복원 불가 신호.
    store.close()

    store2 = PaperTradeStore(db_path)
    with caplog.at_level(logging.WARNING, logger="live.executor"):
        ex2 = _make_executor(store2)
    assert ex2.equity == pytest.approx(_INITIAL)  # 폴백: 초기 자본 그대로.
    assert "복원 불가" in caplog.text
    store2.close()


def test_mixed_null_ledger_does_not_partially_restore(db_path: Path) -> None:
    """유효 행 + NULL 행이 섞이면 부분 복원(초기+유효분)하지 않고 초기로 폴백한다."""
    store = PaperTradeStore(db_path)
    ex1 = _make_executor(store)
    _round_trip(ex1, _intent(), exit_price=98.0)  # 유효 행: 실현 −40.
    store.upsert_record(_legacy_null_record(exit_time=_DAY0 + 5))  # 금액 미상 행.
    assert store.total_realized_pnl() is None
    store.close()

    store2 = PaperTradeStore(db_path)
    ex2 = _make_executor(store2)
    assert ex2.equity == pytest.approx(_INITIAL)  # 9,960(부분 복원) 아님 — 안전 폴백.
    store2.close()


# -- 완료기준 5: 오픈 포지션 미실현 손익 이중계산 없음 ------------------------------


def test_open_position_unrealized_not_double_counted(db_path: Path) -> None:
    store = PaperTradeStore(db_path)
    ex1 = _make_executor(store)
    # 칸 A: 청산 손실 −80 → 자본 9,920.
    r_a = _round_trip(ex1, _intent(symbol=_BTC, price=100.0, stop=95.0), exit_price=96.0)
    assert r_a == pytest.approx(-80.0)
    # 칸 B: 진입만(오픈, 미청산). 진입 수수료 0이라 자본은 그대로 9,920.
    open_intent = _intent(symbol=_ETH, price=200.0, stop=190.0, tp=260.0)
    ex1.enter(open_intent, now_ms=_DAY0)
    assert ex1.equity == pytest.approx(9_920.0)
    store.close()

    store2 = PaperTradeStore(db_path)
    ex2 = _make_executor(store2)
    # 복원 자본 = 초기 + 청산된 A만(−80). 오픈 B의 미실현은 아직 안 실린다(이중계산 없음).
    assert ex2.equity == pytest.approx(9_920.0)
    assert len(ex2.open_positions) == 1  # 오픈 B가 복구됐다.
    assert ex2.open_positions[0].symbol == _ETH

    # B를 이익 청산 → 그 실현손익이 **정확히 한 번** 자본에 더해진다.
    equity_before_close = ex2.equity
    report = ex2.exit(
        _ETH,
        _TF,
        exit_price=230.0,
        exit_time=_DAY0 + 10,
        reason=SignalExitReason.TAKE_PROFIT,
        now_ms=_DAY0,
    )
    assert report.outcome.realized_pnl is not None
    assert ex2.equity == pytest.approx(equity_before_close + report.outcome.realized_pnl)
    store2.close()


# -- 저장소 단위: total_realized_pnl 계약 ------------------------------------------


def test_total_realized_pnl_semantics() -> None:
    with PaperTradeStore(":memory:") as store:
        assert store.total_realized_pnl() == pytest.approx(0.0)  # 거래 없음.
        ex = _make_executor(store)
        r1 = _round_trip(ex, _intent(symbol=_BTC, price=100.0, stop=95.0), exit_price=98.0)
        r2 = _round_trip(ex, _intent(symbol=_ETH, price=200.0, stop=190.0), exit_price=230.0)
        assert store.total_realized_pnl() == pytest.approx(r1 + r2)  # 전 칸 합.
        store.upsert_record(_legacy_null_record(exit_time=_DAY0 + 9))
        assert store.total_realized_pnl() is None  # NULL 하나라도 섞이면 복원 불가.
