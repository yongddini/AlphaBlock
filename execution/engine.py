"""실행 엔진: 시그널 → (드라이런) 주문 → 포지션 추적 (WAN-9).

전략 시그널(WAN-23)에서 나온 진입/청산 의도를 받아

1. WAN-26 리스크 사이징으로 수량을 역산하고,
2. 리스크 한도(`RiskManager`)로 신규 진입을 검사·차단하고,
3. 브로커(페이퍼/실거래)로 주문을 실행(재시도·부분체결 처리)하고,
4. 심볼·타임프레임별 오픈 포지션을 추적하며 청산 시 실현 손익을 계산한다.

**기본값은 페이퍼(드라이런)** 이다. `build_execution_engine`는 `live_trading`이
꺼져 있으면 항상 `PaperBroker`를 쓰고, 켜져 있어도 실거래 브로커를 자동 생성하지
않고 명시적 주입을 요구한다(WAN-27에서 안전하게 연결).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from execution.broker import Broker, PaperBroker
from execution.leverage import LeverageBookParams, resolve_book_sizing
from execution.models import (
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Position,
    side_for_entry,
    side_for_exit,
)
from execution.risk import (
    CircuitBreakerStatus,
    RealizedPnlSource,
    RiskDecision,
    RiskManager,
    RiskParams,
)
from execution.sizing import PositionSizingParams, SizingRejectReason, size_with_reason
from strategy.models import OrderBlockDirection, SignalExitReason

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
SeriesKey = tuple[str, str]

#: 청산 정산 훅(WAN-392). `(청산된 포지션, 청산 체결가) -> 순실현손익(달러) | None`.
#:
#: 페이퍼 지갑은 비용을 **스스로 계산하지 않는다** — 장부(`paper.store.build_record`)가
#: 백테스트와 같은 산식(`common.costs.CostModel`)으로 낸 순손익을 그대로 받아 정산한다.
#: 그래서 비용을 계산하는 곳이 하나뿐이고, 「장부는 아는데 지갑은 모르는」 갈라짐이
#: 구조적으로 생기지 않는다(WAN-392가 고친 사고가 정확히 그것이다).
#: `None`을 돌려주면 엔진은 옛 정산(그로스 − 체결 수수료)으로 되돌아간다(안전 폴백).
ExitSettlement = Callable[[Position, float], float | None]

#: 진입 거부 **사유 코드**(WAN-221). `reason`(자유 텍스트, 사람용·WAN-194)와 별개로,
#: 집계가 문자열을 파싱하지 않고 사유를 셀 수 있게 안정 코드를 함께 싣는다. 라이브 장부의
#: `SKIP_REASON_*`(`live.order_journal`)와 같은 어휘를 쓴다 — 깔때기 상단(주문 걸기 전
#: 스킵)과 하단(체결 후 거부)의 사유를 나란히 읽기 위해서다(테스트가 두 어휘의 일치를 고정).
REJECT_CODE_CELL_BUSY = "cell_busy"
#: 사이징이 수량 0을 냈다 — 대부분 손절폭 가드(0.3%, WAN-79)에 손절이 너무 가까운 경우다
#: (WAN-194가 "대부분은 손절폭 가드"라 한 그 거부). 손절 참조가 자체가 없어 사이징 불가인
#: 경우도 여기로 묶는다(둘 다 "사이징이 안 됐다").
REJECT_CODE_SIZING = "sizing"
#: 북 명목 상한(cap-only 레버리지)이 소진돼 새 명목을 실을 수 없다(WAN-171/213).
REJECT_CODE_NOTIONAL = "notional"
#: 리스크 한도 차단(자본 소진·일일 손실 서킷브레이커, WAN-38).
REJECT_CODE_RISK = "risk"
#: 브로커가 주문을 체결하지 않았다(페이퍼에서는 사실상 발생하지 않는다).
REJECT_CODE_UNFILLED = "unfilled"


def _sizing_reject_reason(
    reason: SizingRejectReason,
    *,
    entry_price: float,
    stop_price: float,
    params: PositionSizingParams,
) -> str:
    """사이징이 수량 0을 낸 바닥 이유를 사람용 구체 사유 문자열로 옮긴다 (WAN-275).

    예전엔 이 자리가 전부 "사이징 수량 0 — 진입 스킵" 한 뭉치였다(증상만 보임). 이제
    `size_with_reason`이 돌려준 원인을 받아 어느 가드에 걸렸는지(손절 하한 미달·레버리지
    한도 소진·유동성 한도 초과·자본 부족)를 표기한다. 손절 하한 미달이면 **실제 손절폭 %를
    병기**해 사용자가 왜 걸렀는지 화면 숫자로 바로 확인하게 한다(WAN-79 하한 가드).

    표시 문구는 **사용자 확정**이다(WAN-275 코멘트 2026-08-10) — enum(내부)은 그대로 두고
    화면·리포트·알림에 뜨는 라벨만 쉬운 말로 통일했다: "레버리지 한도 소진"(cap_only 5배
    북은 거래당 리스크가 아니라 동시 명목 한도만 키우므로 "증거금 소진"의 청산 뉘앙스보다
    정확) · "유동성 한도 초과"(WAN-279가 `max_notional_adv_fraction`을 "유동성 한도"로
    통일 중 — 같은 단어를 쓴다).

    ⚠️ 라벨만 세분화한다 — 거부 동작·수량·기본값은 불변이다.
    """
    # ⚠️ 표시 문구에 WAN-xxx 이슈 코드를 넣지 않는다(사용자 결정) — 폰으로 알림을 보는
    # 사람에겐 개발자 참조라 뜻이 없다. 근거 이슈는 아래 주석·docstring으로만 남긴다.
    if reason == "stop_too_tight":
        stop_pct = abs(entry_price - stop_price) / entry_price * 100.0
        min_pct = params.min_stop_distance_fraction * 100.0
        return f"손절 {min_pct:.1f}% 하한 미달 (손절폭 {stop_pct:.2f}%) — 진입 스킵"  # WAN-79 하한
    if reason == "notional_exhausted":
        return "레버리지 한도 소진 — 진입 스킵"  # WAN-103 명목 여유 소진
    if reason == "capacity_cap":
        return "유동성 한도 초과 (하루 거래량 대비 주문이 너무 큼) — 진입 스킵"  # WAN-244 ADV
    if reason == "no_equity":
        return "자본 부족 — 진입 스킵"
    if reason == "below_min_qty":
        return "최소 주문 수량 미달 — 진입 스킵"
    # "ok"는 이 함수를 타지 않는다(수량 > 0). 방어적 폴백.
    return "사이징 수량 0 — 진입 스킵"


class EntryIntent(BaseModel):
    """전략이 낸 진입 의도. 시그널(WAN-23)에서 실행 레이어로 넘어오는 입력."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    entry_price: float = Field(gt=0)
    entry_time: int
    stop_price: float | None = None
    """손절 참조가(오더블록 무효화). 사이징의 손절 거리 기준. 없으면 진입 불가."""
    take_profit_price: float | None = None


class ExecutionOutcome(BaseModel):
    """진입/청산 시도 결과."""

    model_config = ConfigDict(frozen=True)

    accepted: bool
    reason: str = ""
    """거부·스킵 사유(자유 텍스트, 사람용). 성공이면 빈 문자열."""
    reason_code: str = ""
    """거부 사유 **코드**(`REJECT_CODE_*`, WAN-221). 집계가 자유 텍스트를 파싱하지 않고
    사유를 세도록 안정 코드를 함께 싣는다. 성공이면 빈 문자열."""
    position: Position | None = None
    """진입 성공 시 새로 연(또는 청산된) 포지션."""
    fill: Fill | None = None
    realized_pnl: float | None = None
    """청산 시 실현 손익(수수료 반영). 진입이면 None."""

    @classmethod
    def rejected(cls, reason: str, code: str = "") -> ExecutionOutcome:
        return cls(accepted=False, reason=reason, reason_code=code)


class ExecutionEngine:
    """시그널을 주문으로 옮기고 포지션을 추적하는 실행 엔진.

    페이퍼/실거래 공통. 브로커만 교체하면 되며, 리스크·사이징·포지션 추적 로직은
    동일하다. 자본(`equity`)은 페이퍼 손익 정산과 리스크·사이징 기준으로 쓰인다.
    """

    def __init__(
        self,
        *,
        broker: Broker,
        risk_manager: RiskManager,
        sizing_params: PositionSizingParams,
        equity: float,
        live_trading: bool = False,
        max_retries: int = 2,
        leverage_book: LeverageBookParams | None = None,
    ) -> None:
        self._broker = broker
        self._risk = risk_manager
        self._sizing = sizing_params
        self._equity = equity
        self._live_trading = live_trading
        self._max_retries = max(0, max_retries)
        # 레버리지 북(WAN-171). None이면 예전과 비트 단위로 동일한 사이징(open_notional 미전달).
        # 켜면 칸=(종목,TF)들이 한 지갑(공유 자본)을 나눠 쓰고, 진입마다 백테스트와 **같은**
        # `resolve_book_sizing`으로 배수·북 상한을 건다. `_sizing`은 원본(1배) 기준이라
        # resolve가 배수를 싣는다 — 둘을 곱하지 않도록 여기서 스케일하지 않는다.
        self._leverage_book = leverage_book
        self._book: dict[SeriesKey, Position] = {}

    # -- 상태 조회 ----------------------------------------------------------

    def bind_realized_pnl_source(self, source: RealizedPnlSource | None) -> None:
        """서킷브레이커를 DB 재계산으로 전환한다(러너 배선, WAN-38).

        엔진 구성 시점엔 저장소가 없으므로, 저장소가 준비된 뒤 호출부(`PaperExecutor`)가
        `paper_trades` 조회 콜백을 여기서 물린다. `None`이면 인메모리로 되돌린다.
        """
        self._risk.bind_realized_pnl_source(source)

    def circuit_breaker_status(self, now_ms: int) -> CircuitBreakerStatus:
        """현재 자본 기준 서킷브레이커 상태 스냅샷(대시보드·알림, WAN-38)."""
        return self._risk.status(now_ms, self._equity)

    @property
    def equity(self) -> float:
        """현재(페이퍼) 자본."""
        return self._equity

    @property
    def live_trading(self) -> bool:
        return self._live_trading

    @property
    def open_positions(self) -> list[Position]:
        return list(self._book.values())

    @property
    def open_notional(self) -> float:
        """오픈 포지션들의 명목가치 합."""
        return sum(pos.notional for pos in self._book.values())

    def position(self, symbol: str, timeframe: str) -> Position | None:
        return self._book.get((symbol, timeframe))

    def restore_position(self, position: Position) -> None:
        """영속 저장소에서 읽은 열린 포지션을 장부에 복구한다(재시작 안전).

        주문을 내지 않고 추적 장부에만 넣는다. 러너 재시작 시 DB의 열린 포지션을
        엔진에 다시 실어 청산 평가가 이어지게 한다. 같은 시리즈가 이미 있으면
        덮어쓴다(단일 작성자 전제).
        """
        self._book[(position.symbol, position.timeframe)] = position

    def restore_equity(self, equity: float) -> None:
        """영속 장부에서 복원한 누적 자본을 시드한다(재시작 안전, WAN-238).

        `restore_position`과 대칭이다. 엔진은 기본이 초기 자본으로 시드되는데, 그
        상태로 재시작하면 사이징(`risk_amount = equity × risk_per_trade`)이 손실·이익을
        모르는 초기 자본 기준으로 계산돼 리스크 관리가 설계와 어긋난다. 호출부
        (`PaperExecutor`)가 원장에서 계산한 누적 자본을 여기로 실어 이어지게 한다.

        미실현 손익 이중계산 없음 — 복원되는 오픈 포지션(`restore_position`)의 손익은
        엔진 자본에 아직 반영돼 있지 않고 청산 시(`on_exit`) 비로소 더해지므로, 청산된
        거래만 더해 만든 누적 자본과 겹치지 않는다.
        """
        self._equity = equity

    # -- 진입 ---------------------------------------------------------------

    def _book_risk_decision(self, now_ms: int) -> RiskDecision:
        """북 모드의 진입 한도 검사 — 자본 유무 + 일일 손실 서킷브레이커만.

        명목·동시 포지션 상한은 북 회계(칸당 1포지션 + `resolve_book_sizing` 명목 clamp)가
        대체하므로 여기서 다시 걸지 않는다(WAN-171). 서킷브레이커는 `on_exit`의
        `register_realized_pnl`이 계속 먹여 살린다(WAN-38)."""
        if self._equity <= 0.0:
            return RiskDecision.block("자본이 없어 진입 차단")
        if self._risk.circuit_breaker_tripped(now_ms, self._equity):
            return RiskDecision.block(
                f"일일 손실 서킷브레이커 발동(누적 {self._risk.daily_realized_pnl:.2f}) — 진입 차단"
            )
        return RiskDecision.allow()

    def on_entry(self, intent: EntryIntent, *, now_ms: int) -> ExecutionOutcome:
        """진입 의도를 사이징·리스크 검사 후 주문·포지션 오픈으로 처리한다."""
        key = (intent.symbol, intent.timeframe)
        if key in self._book:
            return ExecutionOutcome.rejected(
                "이미 오픈 포지션이 있어 진입 스킵", REJECT_CODE_CELL_BUSY
            )
        if intent.stop_price is None:
            return ExecutionOutcome.rejected(
                "손절 참조가가 없어 사이징 불가 — 진입 스킵", REJECT_CODE_SIZING
            )

        if self._leverage_book is None:
            size_params = self._sizing
            size_open = 0.0
        else:
            # 레버리지 북(WAN-171): 배수·북 명목 상한·cap-only 합성 여유를 백테스트
            # (`run_leverage_book`)와 **같은** `resolve_book_sizing`으로 정하고, 공유 지갑의
            # 열린 명목(`open_notional`)을 넘겨 칸을 가로지르는 명목 상한을 건다. 칸당
            # 1포지션은 위 `key in self._book` 거부가 이미 강제한다(= 북의 cell_busy).
            book_sizing = resolve_book_sizing(
                self._sizing,
                self._leverage_book,
                equity=self._equity,
                open_notional=self.open_notional,
            )
            if book_sizing.cap_exhausted:
                return ExecutionOutcome.rejected(
                    "북 명목 상한 소진 — 진입 스킵", REJECT_CODE_NOTIONAL
                )
            size_params = book_sizing.params
            size_open = book_sizing.synthetic_open

        # 수량과 함께 **왜 그 수량인지**를 받는다(WAN-275). 0이면 어느 가드에 걸렸는지를
        # 구체 사유로 실어 보낸다 — 예전의 "사이징 수량 0" 한 뭉치를 세분화. 수량 계산·
        # 거부 동작은 불변이라 `size_open=0.0`(북 없음)은 예전 per-trade clamp와 비트 동일.
        qty, sizing_reason = size_with_reason(
            equity=self._equity,
            entry_price=intent.entry_price,
            stop_price=intent.stop_price,
            params=size_params,
            open_notional=size_open,
        )
        if qty <= 0.0:
            return ExecutionOutcome.rejected(
                _sizing_reject_reason(
                    sizing_reason,
                    entry_price=intent.entry_price,
                    stop_price=intent.stop_price,
                    params=size_params,
                ),
                REJECT_CODE_SIZING,
            )

        new_notional = intent.entry_price * qty
        if self._leverage_book is None:
            decision = self._risk.can_enter(
                equity=self._equity,
                new_notional=new_notional,
                open_notional=self.open_notional,
                open_positions=len(self._book),
                now_ms=now_ms,
            )
        else:
            # 북 모드(WAN-171): 명목가치·동시 포지션 수 상한은 **북 자체**가 대체한다 —
            # 칸당 1포지션(위 `key in self._book`)이 동시 포지션을, `resolve_book_sizing`이
            # 이미 clamp한 공유 명목 상한이 명목을 관장한다(백테스트 `run_leverage_book`도
            # 이 둘만 건다). `RiskParams` 기본값(동시 1포지션 · 명목 1×)은 칸을 모르는
            # 러너 전역 상한이라 북을 조용히 1포지션·1배로 되돌린다 — 그래서 북 모드에서는
            # **일일 손실 서킷브레이커만** 남긴다(WAN-38 — 레버리지를 얹으면 더 필요하다).
            decision = self._book_risk_decision(now_ms)
        if not decision.allowed:
            _logger.info("진입 차단(%s %s): %s", intent.symbol, intent.timeframe, decision.reason)
            return ExecutionOutcome.rejected(decision.reason, REJECT_CODE_RISK)

        order = Order(
            symbol=intent.symbol,
            side=side_for_entry(intent.direction),
            type=OrderType.MARKET,
            quantity=qty,
        )
        fill = self._place_with_retry(order, mark_price=intent.entry_price)
        if not fill.is_filled:
            return ExecutionOutcome.rejected("주문이 체결되지 않음(거부)", REJECT_CODE_UNFILLED)
        if fill.status is OrderStatus.PARTIALLY_FILLED:
            _logger.warning(
                "부분체결로 진입(%s %s): 요청 %s, 체결 %s",
                intent.symbol,
                intent.timeframe,
                qty,
                fill.filled_quantity,
            )

        position = Position(
            symbol=intent.symbol,
            timeframe=intent.timeframe,
            direction=intent.direction,
            quantity=fill.filled_quantity,
            entry_price=fill.average_price,
            entry_time=intent.entry_time,
            stop_price=intent.stop_price,
            take_profit_price=intent.take_profit_price,
        )
        self._book[key] = position
        # 진입 수수료를 페이퍼 자본에서 차감(수수료 0이면 영향 없음).
        self._equity -= fill.fee
        return ExecutionOutcome(accepted=True, position=position, fill=fill)

    # -- 청산 ---------------------------------------------------------------

    def on_exit(
        self,
        symbol: str,
        timeframe: str,
        *,
        exit_price: float,
        reason: SignalExitReason,
        now_ms: int,
        settle: ExitSettlement | None = None,
    ) -> ExecutionOutcome:
        """오픈 포지션을 청산하고 실현 손익을 정산한다.

        `settle`(WAN-392)을 주면 **그것이 비용의 단일 소스**다 — 엔진은 체결가만 넘기고,
        수수료·슬리피지·펀딩을 반영한 순실현손익을 받아 그대로 지갑에 반영한다. 안 주면
        옛 정산(그로스 − 브로커 체결 수수료)이라 예전과 비트 단위로 같다.

        ⚠️ `settle`을 쓰는 경로에서는 브로커가 수수료를 물면 **안 된다**(진입 수수료는
        `on_entry`에서 이미 자본에서 빠지는데 정산값에도 왕복 수수료가 들어 있어 이중
        계상이 된다). 그래서 페이퍼 경로의 `PaperBroker`는 비용 모델 없이 만든다 —
        `build_execution_engine` 참고.
        """
        key = (symbol, timeframe)
        position = self._book.get(key)
        if position is None:
            return ExecutionOutcome.rejected("청산할 오픈 포지션이 없음")

        order = Order(
            symbol=symbol,
            side=side_for_exit(position.direction),
            type=OrderType.MARKET,
            quantity=position.quantity,
            reduce_only=True,
        )
        fill = self._place_with_retry(order, mark_price=exit_price)
        if not fill.is_filled:
            return ExecutionOutcome.rejected("청산 주문이 체결되지 않음(거부)")

        # 비용의 단일 소스: `settle`이 있으면 장부가 낸 순손익을 그대로 쓴다(WAN-392).
        # 없으면(또는 장부 산정이 실패해 None이면) 옛 정산으로 되돌아간다.
        realized = position.realized_pnl(fill.average_price) - fill.fee
        if settle is not None:
            settled = settle(position, fill.average_price)
            if settled is None:
                _logger.warning(
                    "청산 비용 산정 실패(%s %s) — 그로스 기준 폴백 정산", symbol, timeframe
                )
            else:
                realized = settled
        self._equity += realized
        self._risk.register_realized_pnl(realized, now_ms=now_ms, equity=self._equity)
        del self._book[key]
        _logger.info(
            "청산(%s %s) 사유=%s 실현손익=%.2f",
            symbol,
            timeframe,
            reason.value,
            realized,
        )
        return ExecutionOutcome(accepted=True, position=position, fill=fill, realized_pnl=realized)

    # -- 내부 ---------------------------------------------------------------

    def _place_with_retry(self, order: Order, *, mark_price: float | None) -> Fill:
        """일시적 예외에 대해 주문을 재시도한다. 모두 실패하면 REJECTED 체결을 만든다."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._broker.place_order(order, mark_price=mark_price)
            except Exception as exc:  # noqa: BLE001 — 브로커 오류를 재시도로 흡수.
                last_exc = exc
                _logger.warning(
                    "주문 실행 실패(시도 %d/%d): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                )
        _logger.error("재시도 소진 — 주문 거부 처리: %s (%s)", order.symbol, last_exc)
        return Fill(
            order=order,
            status=OrderStatus.REJECTED,
            filled_quantity=0.0,
            average_price=0.0,
        )


class ExecutionSettings(Protocol):
    """`build_execution_engine`가 요구하는 설정의 구조적 인터페이스.

    `config.Settings`가 이 속성들을 모두 제공하므로 그대로 넘길 수 있다. Protocol로
    두어 실행 레이어가 전체 설정 클래스에 강결합되지 않게 한다.
    """

    @property
    def live_trading(self) -> bool: ...
    @property
    def paper_equity(self) -> float: ...
    @property
    def risk_limits(self) -> RiskParams: ...
    @property
    def risk_sizing(self) -> PositionSizingParams: ...


def build_execution_engine(
    settings: ExecutionSettings,
    *,
    broker: Broker | None = None,
    equity: float | None = None,
    now_ms: Callable[[], int] | None = None,
    leverage_book: LeverageBookParams | None = None,
) -> ExecutionEngine:
    """설정에서 실행 엔진을 만든다.

    안전: `live_trading`이 꺼져 있으면 항상 `PaperBroker`(네트워크 없음)를 쓴다.
    켜져 있는데 브로커를 주지 않으면 자동으로 실거래 브로커를 만들지 않고
    `RuntimeError`를 던진다(실계좌 연결은 WAN-27에서 명시적으로 주입).

    `leverage_book`(WAN-171)을 주면 엔진이 칸=(종목,TF)을 가로지르는 공유 자본 북으로
    사이징한다(옵트인). None이면 예전과 비트 단위로 같다. 사이징 파라미터는 **원본**을
    넘긴다 — 배수는 `resolve_book_sizing`이 진입마다 싣는다(이중 스케일 방지).
    """
    del now_ms  # 예약 인자(러너 연결용). 현재는 미사용.
    if broker is None:
        if settings.live_trading:
            raise RuntimeError(
                "live_trading=True: 실거래 브로커를 명시적으로 주입해야 합니다(WAN-27). "
                "자동으로 실거래 경로를 켜지 않습니다."
            )
        # 비용 모델을 **일부러 넘기지 않는다**(WAN-392): 페이퍼 지갑의 비용 권위는
        # 장부(`paper.store.build_record`)이고, 브로커가 수수료를 또 물면 이중 계상이다.
        broker = PaperBroker()

    resolved_equity = settings.paper_equity if equity is None else equity
    return ExecutionEngine(
        broker=broker,
        risk_manager=RiskManager(settings.risk_limits),
        sizing_params=settings.risk_sizing,
        equity=resolved_equity,
        live_trading=settings.live_trading,
        leverage_book=leverage_book,
    )
