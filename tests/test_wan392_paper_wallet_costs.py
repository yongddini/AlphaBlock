"""WAN-392: 페이퍼 지갑이 수수료·슬리피지를 실제로 문다 — 「라벨이 아니라 동작으로」.

버그: `PaperBroker()`가 비용 모델 없이 생성돼 `fill.fee == 0`이었고, 지갑에는 그로스가
쌓였다. 장부(`paper.store.build_record`)는 `CostModel`로 비용을 정확히 계산하고 있었는데
지갑이 그걸 안 썼다 — 두 곳이 갈라진 것이다(실측 100거래에 1,783 · 성적 +8.5% → −9.4%).

이 테스트들은 **배선 줄이 있는지**로 걸지 않는다(그러면 다시 샌다). 비용이 0이 아닌
`CostModel`을 준 페이퍼 청산에서 **지갑 변화량이 그로스와 다른지**, 그리고 그 값이 장부의
`net_pct`와 **같은 자**인지로 건다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from common.costs import CostModel
from config.settings import Settings
from execution.broker import PaperBroker
from execution.engine import EntryIntent, ExecutionEngine, build_execution_engine
from execution.risk import RiskManager
from execution.sizing import PositionSizingParams
from live.executor import PaperExecutor
from paper.store import PaperTradeRecord, PaperTradeRecorder, PaperTradeStore
from strategy.models import OrderBlockDirection, SignalExitReason

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
_T0 = 1_700_000_000_000
_EQUITY = 10_000.0

#: 비용이 0이 아닌 모델 — 이 값들이 지갑에 실제로 반영되는지가 이 파일의 주제다.
_COSTS = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_bps=5.0)

#: 손절폭 5(= 100 → 95). 리스크 100(자본 1% ) ÷ 5 = 수량 20 · 진입명목 2,000.
_ENTRY = 100.0
_STOP = 95.0


@pytest.fixture
def store() -> Iterator[PaperTradeStore]:
    s = PaperTradeStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _intent() -> EntryIntent:
    return EntryIntent(
        symbol=SYMBOL,
        timeframe=TF,
        direction=OrderBlockDirection.BULLISH,
        entry_price=_ENTRY,
        entry_time=_T0,
        stop_price=_STOP,
        take_profit_price=107.5,  # 1.5R
    )


def _executor(store: PaperTradeStore, *, cost_model: CostModel | None) -> PaperExecutor:
    sizing = PositionSizingParams()
    engine = ExecutionEngine(
        broker=PaperBroker(),
        risk_manager=RiskManager(),
        sizing_params=sizing,
        equity=_EQUITY,
    )
    recorder = PaperTradeRecorder(store, cost_model=cost_model)
    return PaperExecutor(engine=engine, store=store, recorder=recorder, sizing=sizing)


def _round_trip(
    store: PaperTradeStore,
    *,
    exit_price: float,
    reason: SignalExitReason,
    cost_model: CostModel | None = _COSTS,
) -> tuple[float, PaperTradeRecord]:
    """진입 → 청산 한 바퀴. `(지갑 변화량, 장부 행)`을 돌려준다."""
    executor = _executor(store, cost_model=cost_model)
    equity_before = executor.equity
    executor.enter(_intent(), now_ms=_T0)
    # 진입은 지갑을 건드리지 않는다(페이퍼 브로커 수수료 0) — 비용은 청산에서 한 번에 문다.
    assert executor.equity == pytest.approx(equity_before)
    executor.exit(SYMBOL, TF, exit_price=exit_price, exit_time=_T0 + 1, reason=reason, now_ms=_T0)
    records = store.list_records()
    assert len(records) == 1
    return executor.equity - equity_before, records[0]


def test_wallet_delta_is_net_not_gross() -> None:
    """완료기준 1 — 청산 뒤 지갑 변화량 == `net_pct × 진입명목 / 100`(그로스가 아니다)."""
    s = PaperTradeStore(":memory:")
    try:
        delta, record = _round_trip(s, exit_price=107.5, reason=SignalExitReason.TAKE_PROFIT)
        notional = _ENTRY * 20.0
        assert record.notional == pytest.approx(notional)
        assert delta == pytest.approx(record.net_pct / 100.0 * notional)
        # 그리고 그 값은 그로스와 **다르다** — 비용이 실제로 빠졌다는 뜻(버그의 반대).
        gross_delta = record.gross_pct / 100.0 * notional
        assert delta < gross_delta
        assert record.fee_pct > 0.0
        assert record.slippage_pct > 0.0
    finally:
        s.close()


def test_wallet_delta_records_the_same_number_as_the_ledger() -> None:
    """지갑이 정산한 실현손익과 장부에 적힌 `realized_pnl`·`equity_after`가 같은 값."""
    s = PaperTradeStore(":memory:")
    try:
        delta, record = _round_trip(s, exit_price=107.5, reason=SignalExitReason.TAKE_PROFIT)
        assert record.realized_pnl is not None
        assert record.realized_pnl == pytest.approx(delta)
        assert record.equity_after is not None
        assert record.equity_after == pytest.approx(_EQUITY + delta)
    finally:
        s.close()


def test_stop_is_worse_than_minus_one_r_and_target_is_short_of_plus_1p5r() -> None:
    """완료기준 2 — 손절이 −1.0R보다 나쁘고 익절이 +1.5R에 못 미친다(비용만큼)."""
    losing = PaperTradeStore(":memory:")
    winning = PaperTradeStore(":memory:")
    try:
        risk_dollars = abs(_ENTRY - _STOP) * 20.0  # 손절폭 × 수량
        stop_delta, _ = _round_trip(losing, exit_price=_STOP, reason=SignalExitReason.STOP_LOSS)
        tp_delta, _ = _round_trip(winning, exit_price=107.5, reason=SignalExitReason.TAKE_PROFIT)
        assert stop_delta / risk_dollars < -1.0
        assert tp_delta / risk_dollars < 1.5
        # 버그 시절에는 정확히 −1.000 / +1.500이었다 — 그 값이 다시 나오면 샌 것이다.
        assert stop_delta / risk_dollars != pytest.approx(-1.0, abs=1e-6)
        assert tp_delta / risk_dollars != pytest.approx(1.5, abs=1e-6)
    finally:
        losing.close()
        winning.close()


@pytest.mark.parametrize(
    ("exit_price", "reason"),
    [
        (_STOP, SignalExitReason.STOP_LOSS),
        (107.5, SignalExitReason.TAKE_PROFIT),
    ],
)
def test_realized_pnl_and_r_multiple_share_one_ruler(
    exit_price: float, reason: SignalExitReason
) -> None:
    """완료기준 3 — `realized_pnl ÷ (손절폭 × 수량) == r_multiple`(예전엔 1.8배 어긋났다)."""
    s = PaperTradeStore(":memory:")
    try:
        delta, record = _round_trip(s, exit_price=exit_price, reason=reason)
        risk_dollars = abs(_ENTRY - _STOP) * 20.0
        assert record.r_multiple is not None
        assert delta / risk_dollars == pytest.approx(record.r_multiple)
    finally:
        s.close()


def test_zero_cost_model_reproduces_gross_settlement() -> None:
    """비용이 0이면 예전과 같은 값이다 — 이 수정은 비용을 **만들지** 않는다."""
    free = CostModel(taker_fee_rate=0.0, maker_fee_rate=0.0, slippage_bps=0.0)
    s = PaperTradeStore(":memory:")
    try:
        delta, record = _round_trip(
            s, exit_price=107.5, reason=SignalExitReason.TAKE_PROFIT, cost_model=free
        )
        notional = _ENTRY * 20.0
        assert delta == pytest.approx(record.gross_pct / 100.0 * notional)
        assert delta == pytest.approx(7.5 / 100.0 * notional)
    finally:
        s.close()


def test_factory_paper_path_charges_nothing_on_entry() -> None:
    """이중 계상 방지 — 팩토리가 만든 페이퍼 경로는 **진입에서** 지갑을 건드리지 않는다.

    브로커가 수수료를 물면 진입에서 한 번, 장부 정산(왕복 수수료 포함)에서 또 한 번
    빠져 완료기준 1이 깨진다. 「비용을 계산하는 곳이 하나」라는 성질을 배선 줄이 아니라
    **지갑 잔고의 움직임**으로 고정한다.
    """
    settings = Settings(live_trading=False)
    engine = build_execution_engine(settings)
    before = engine.equity
    outcome = engine.on_entry(_intent(), now_ms=_T0)

    assert outcome.accepted
    assert engine.equity == pytest.approx(before)
