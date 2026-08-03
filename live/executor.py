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
from execution.risk import CircuitBreakerStatus
from execution.sizing import PositionSizingParams
from live.paper import ClosedTrade, PaperPosition
from paper.store import PaperTradeRecorder, PaperTradeStore, TradeDollars
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
        # 일일 손실 서킷브레이커를 DB(paper_trades) 재계산으로 전환한다(WAN-38). 러너가
        # 재시작돼도 "오늘(KST) 청산 손익 합"을 원장에서 다시 읽어 차단 상태가 유지된다 —
        # 인메모리 누적만 쓰면 재시작 시 0으로 리셋돼 브레이크가 풀린다.
        self._engine.bind_realized_pnl_source(store.realized_pnl_between)
        self._restore()

    def _restore(self) -> None:
        # 누적 자본을 먼저 복원한다 — 오픈 포지션 복구는 자본을 건드리지 않으므로 순서는
        # 무관하지만, 자본이 시드된 뒤에 열린 포지션이 얹히는 편이 읽기 쉽다(WAN-238).
        self._restore_equity()
        self._restore_positions()

    def _restore_positions(self) -> None:
        restored = self._store.load_open_positions()
        for open_position in restored:
            self._engine.restore_position(open_position.position)
        if restored:
            _logger.info("열린 페이퍼 포지션 %d건 복구", len(restored))

    def _restore_equity(self) -> None:
        """재시작 시 누적 북 자본을 원장에서 복원한다(WAN-238, `restore_position`과 대칭).

        엔진은 기본이 초기 자본(`settings.paper_equity`)으로 시드되는데, 그러면 재시작
        후 사이징(`risk_amount = equity × risk_per_trade`)이 **손실을 모르는 초기 자본**
        기준이 된다(손실이 나도 다음 베팅이 안 줄고, 이익이 나도 안 는다). 북(WAN-213)은
        공유 지갑이므로 복원 단위는 칸별이 아니라 **전 칸 실현손익 합**이다:
        `초기자본 + Σrealized_pnl`(= WAN-237 표시 잔고와 같은 공식).

        복원 불가(옛 %-only 장부, `realized_pnl` NULL)면 초기 자본을 그대로 둔다(안전 폴백).
        """
        # 아직 시드 직후라 엔진 자본 = settings.paper_equity(초기 자본).
        initial = self._engine.equity
        total = self._store.total_realized_pnl()
        if total is None:
            _logger.warning(
                "옛 %%-only 장부(realized_pnl NULL) 감지 — 누적 자본 복원 불가, "
                "초기 자본(%.2f)으로 시드",
                initial,
            )
            return
        if total == 0.0:
            return  # 거래 없음(또는 정확히 상쇄) — 초기 자본 그대로.
        restored = initial + total
        self._engine.restore_equity(restored)
        _logger.info(
            "누적 북 자본 복원(WAN-238): 초기 %.2f + 실현손익 Σ%.2f = %.2f",
            initial,
            total,
            restored,
        )

    def circuit_breaker_status(self, now_ms: int) -> CircuitBreakerStatus:
        """현재 서킷브레이커 상태 스냅샷(러너 알림·대시보드, WAN-38)."""
        return self._engine.circuit_breaker_status(now_ms)

    def get_circuit_breaker_notice(self) -> tuple[str | None, bool]:
        """마지막으로 알린 서킷브레이커 상태 `(KST일, 발동여부)`(중복 알림 방지, WAN-38)."""
        return self._store.get_circuit_breaker_notice()

    def set_circuit_breaker_notice(self, day: str, *, tripped: bool) -> None:
        """서킷브레이커 알림 상태를 원장에 기록한다(재시작 내구, WAN-38)."""
        self._store.set_circuit_breaker_notice(day, tripped=tripped)

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
        # 진입 시 감수한 리스크 금액은 open_positions에만 있다 — 삭제 전에 회수해
        # 청산 기록에 실어 성과 곡선을 지갑 기준으로 재구성한다(WAN-207).
        open_pos = self._store.get_open_position(symbol, timeframe)
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
            # 달러 금액: 명목·수량은 청산된 포지션에서, 실현손익은 엔진이 정산한 값,
            # 리스크 금액은 진입 때 저장분, equity_after는 정산 직후 자본(WAN-207).
            position = outcome.position
            dollars = TradeDollars(
                quantity=position.quantity,
                notional=position.notional,
                risk_amount=None if open_pos is None else open_pos.risk_amount,
                realized_pnl=outcome.realized_pnl,
                equity_after=self._engine.equity,
            )
            # WAN-33 성과 스키마(paper_trades)에 위임 — 리포트가 읽는 테이블과 동일.
            self._recorder.record(closed, dollars=dollars)
            self._store.remove_open_position(symbol, timeframe)
        return TradeReport(
            outcome=outcome,
            open_positions=self._engine.open_positions,
            equity=self._engine.equity,
        )
