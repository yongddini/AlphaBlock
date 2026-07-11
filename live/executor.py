"""실시간 러너 ↔ execution 엔진 배선 (WAN-34).

시그널 러너가 낸 진입/청산 의도를 execution 엔진(`ExecutionEngine`)으로 집행하고,
그 결과(체결·포지션·실현손익)를 페이퍼 저장소(`PaperTradeStore`)에 영속화한다.

* **진입**: 사이징·리스크 검사를 엔진에 위임하고, 체결되면 열린 포지션을
  `open_positions` 테이블에 저장한다(재시작 복구용).
* **청산**: 엔진으로 정산한 뒤 라운드트립을 WAN-33 `PaperTradeRecorder`에 위임해
  `paper_trades` 테이블에 기록한다 — 성과·패리티 리포트(WAN-33)가 읽는 바로 그
  테이블이므로 집행 결과가 즉시 리포트에 집계된다. 동시에 열린 포지션 행을 지운다.
* **복구**: 생성 시 저장소의 열린 포지션을 엔진 장부로 복구해 청산 평가를 잇는다.

엔진은 기본이 페이퍼(`PaperBroker`)이므로 `live_trading=false`에서는 어떤 실주문
API도 호출되지 않는다(안전 기본값은 `build_execution_engine`이 보장).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

from execution.engine import EntryIntent, ExecutionEngine, ExecutionOutcome
from execution.models import Position
from execution.sizing import PositionSizingParams
from live.paper import ClosedTrade, PaperPosition
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.models import SignalExitReason

_logger = logging.getLogger(__name__)


class TradeReport(BaseModel):
    """진입/청산 집행 결과 요약(알림·상태 표시용)."""

    model_config = ConfigDict(frozen=True)

    outcome: ExecutionOutcome
    #: 이번 진입에서 감수한 리스크 금액(= 자본 × risk_per_trade). 청산·거부면 None.
    risk_amount: float | None = None
    #: 집행 직후 열려 있는 모든 포지션(요약용 스냅샷).
    open_positions: list[Position]
    #: 집행 직후 (페이퍼) 자본.
    equity: float

    @property
    def accepted(self) -> bool:
        return self.outcome.accepted


def _to_closed_trade(
    position: Position, *, exit_price: float, exit_time: int, reason: SignalExitReason
) -> ClosedTrade:
    """청산된 execution 포지션을 WAN-33 성과 집계용 `ClosedTrade`로 변환한다.

    손익률(`realized_pct`)은 진입가·청산가로 산출되므로 수량과 무관하게 성과 스키마
    (백분율)와 일치한다. 수수료·펀딩비는 `PaperTradeRecorder`가 요율로 반영한다.
    """
    paper_position = PaperPosition(
        symbol=position.symbol,
        timeframe=position.timeframe,
        direction=position.direction,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        stop_price=position.stop_price,
        take_profit_price=position.take_profit_price,
    )
    return ClosedTrade(
        position=paper_position, exit_time=exit_time, exit_price=exit_price, reason=reason
    )


class PaperExecutor:
    """시그널을 페이퍼 주문으로 집행하고 그 결과를 영속화하는 코디네이터.

    생성 시 저장소의 열린 포지션을 엔진 장부로 복구한다(재시작 안전). 진입은
    사이징·리스크 검사를 엔진에 위임하고, 체결되면 열린 포지션으로 저장한다.
    청산은 엔진으로 정산하고 라운드트립을 `PaperTradeRecorder`(WAN-33)로 위임해
    `paper_trades`에 남긴다 — 성과·패리티 리포트가 읽는 테이블과 동일하다.
    """

    def __init__(
        self,
        *,
        engine: ExecutionEngine,
        store: PaperTradeStore,
        recorder: PaperTradeRecorder,
        sizing: PositionSizingParams,
    ) -> None:
        self._engine = engine
        self._store = store
        self._recorder = recorder
        self._sizing = sizing
        self._restore()

    def _restore(self) -> None:
        restored = self._store.load_open_positions()
        for open_position in restored:
            self._engine.restore_position(open_position.position)
        if restored:
            _logger.info("열린 페이퍼 포지션 %d건 복구", len(restored))

    @property
    def open_positions(self) -> list[Position]:
        """현재 열려 있는 모든 페이퍼 포지션."""
        return self._engine.open_positions

    @property
    def equity(self) -> float:
        return self._engine.equity

    def enter(self, intent: EntryIntent, *, now_ms: int) -> TradeReport:
        """진입 의도를 집행한다. 체결되면 열린 포지션을 영속 저장한다."""
        equity_before = self._engine.equity
        outcome = self._engine.on_entry(intent, now_ms=now_ms)
        risk_amount: float | None = None
        if outcome.accepted and outcome.position is not None:
            risk_amount = equity_before * self._sizing.risk_per_trade
            entry_fee = outcome.fill.fee if outcome.fill is not None else 0.0
            self._store.record_open(outcome.position, risk_amount=risk_amount, entry_fee=entry_fee)
        return TradeReport(
            outcome=outcome,
            risk_amount=risk_amount,
            open_positions=self._engine.open_positions,
            equity=self._engine.equity,
        )

    def exit(
        self,
        symbol: str,
        timeframe: str,
        *,
        exit_price: float,
        exit_time: int,
        reason: SignalExitReason,
        now_ms: int,
    ) -> TradeReport:
        """오픈 포지션을 청산한다. 정산되면 라운드트립을 성과 테이블에 기록한다."""
        outcome = self._engine.on_exit(
            symbol, timeframe, exit_price=exit_price, reason=reason, now_ms=now_ms
        )
        if outcome.accepted and outcome.position is not None and outcome.fill is not None:
            closed = _to_closed_trade(
                outcome.position,
                exit_price=outcome.fill.average_price,
                exit_time=exit_time,
                reason=reason,
            )
            # WAN-33 성과 스키마(paper_trades)에 위임 — 리포트가 읽는 테이블과 동일.
            self._recorder.record(closed)
            self._store.remove_open_position(symbol, timeframe)
        return TradeReport(
            outcome=outcome,
            open_positions=self._engine.open_positions,
            equity=self._engine.equity,
        )
