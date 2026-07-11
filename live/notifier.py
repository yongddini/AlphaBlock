"""신호 이벤트 → 텔레그램 메시지 포맷·전송 + 페이퍼 집행/장부 연동 (WAN-25).

컨플루언스 결과에서 **확정 진입**과 **계획 청산**을 시리즈(심볼·TF)별 이벤트로
모으고(`collect_events`), 각 이벤트를 사람이 읽기 좋은 마크다운 메시지로 만들어
텔레그램으로 보낸다(`Notifier`).

`executor`가 주어지면(WAN-34) 시그널을 execution 엔진으로 집행(사이징→리스크→페이퍼
주문→포지션 추적)하고 결과를 DB에 영속화하며, 알림에 체결가·수량·리스크·현재
포지션을 담는다. `executor`가 없으면 경량 `PaperBook`으로 포지션만 추적하고(기존
동작), `trade_sink`가 있으면 청산 거래를 성과 저장소(WAN-33)에 누적한다.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from execution.engine import EntryIntent
from execution.models import Position
from live.executor import PaperExecutor, TradeReport
from live.paper import ClosedTrade, PaperBook, PaperPosition
from live.telegram import TelegramClient
from strategy.confluence import ConfluenceResult, ConfluenceSignal, SignalKind
from strategy.models import OrderBlock, OrderBlockDirection, SignalExitReason

_logger = logging.getLogger(__name__)


class SignalEvent(BaseModel):
    """한 시리즈(심볼·TF)에서 발생한 진입/청산 신호 이벤트."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    signal: ConfluenceSignal

    @property
    def time(self) -> int:
        """신호 봉의 `open_time`(ms)."""
        return self.signal.time

    @property
    def is_entry(self) -> bool:
        return self.signal.kind is SignalKind.ENTRY

    @property
    def signal_id(self) -> str:
        """중복 판정을 위한 안정적 식별자.

        같은 봉·같은 종류의 신호는 전략을 재평가해도 동일한 id를 갖는다.
        """
        sig = self.signal
        reason = sig.exit_reason.value if sig.exit_reason is not None else "-"
        return (
            f"{self.symbol}|{self.timeframe}|{sig.kind.value}|"
            f"{sig.direction.value}|{sig.time}|{reason}"
        )


def collect_events(result: ConfluenceResult, symbol: str, timeframe: str) -> list[SignalEvent]:
    """컨플루언스 결과에서 확정 진입·계획 청산을 시간순 이벤트 목록으로 모은다.

    같은 시각이면 진입을 청산보다 앞에 둔다.
    """
    events = [
        SignalEvent(symbol=symbol, timeframe=timeframe, signal=sig)
        for sig in result.confirmed_entries
    ]
    events += [SignalEvent(symbol=symbol, timeframe=timeframe, signal=sig) for sig in result.exits]
    events.sort(key=lambda e: (e.time, 0 if e.is_entry else 1))
    return events


# -- 포맷 헬퍼 ----------------------------------------------------------------


def _fmt_price(value: float) -> str:
    """가격을 천 단위 구분 + 불필요한 소수점 0 제거로 읽기 좋게 포맷."""
    text = f"{value:,.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_qty(value: float) -> str:
    """수량을 불필요한 소수점 0 제거로 포맷."""
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_money(value: float) -> str:
    """견적 통화 금액(USDT 등)을 천 단위 구분·소수 2자리로 포맷."""
    return f"{value:,.2f}"


def _fmt_time(open_time_ms: int) -> str:
    return datetime.fromtimestamp(open_time_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _direction_label(direction: OrderBlockDirection) -> str:
    return "롱" if direction is OrderBlockDirection.BULLISH else "숏"


def _line_label(key: str) -> str:
    """스냅샷 키(`ema_240`·`vwma_100`)를 표시용 라벨(`EMA240`·`VWMA100`)로."""
    prefix, _, length = key.partition("_")
    return f"{prefix.upper()}{length}" if length else key.upper()


def _nearest_take_profit_line(
    direction: OrderBlockDirection, entry_price: float, lines: dict[str, float]
) -> tuple[str, float] | None:
    """진입가 너머 가장 가까운 익절 목표선(라벨, 값). 없으면 None."""
    if direction is OrderBlockDirection.BULLISH:
        beyond = {k: v for k, v in lines.items() if v > entry_price}
        if not beyond:
            return None
        key = min(beyond, key=lambda k: beyond[k])
    else:
        beyond = {k: v for k, v in lines.items() if v < entry_price}
        if not beyond:
            return None
        key = max(beyond, key=lambda k: beyond[k])
    return _line_label(key), beyond[key]


def _stop_loss_price(direction: OrderBlockDirection, order_block: OrderBlock) -> float:
    """오더블록 무효화(손절) 참조가: 롱은 존 하단, 숏은 존 상단."""
    return order_block.bottom if direction is OrderBlockDirection.BULLISH else order_block.top


def format_entry(event: SignalEvent) -> str:
    """진입 신호를 마크다운 메시지로 포맷한다."""
    sig = event.signal
    direction = sig.direction
    lines = [
        f"🟢 *진입 신호* — `{event.symbol}` `{event.timeframe}`",
        f"방향: *{_direction_label(direction)}*",
        f"가격: `{_fmt_price(sig.price)}`",
    ]
    if sig.rsi is not None:
        gate = "과매도" if direction is OrderBlockDirection.BULLISH else "과매수"
        lines.append(f"RSI: `{sig.rsi:.1f}` ({gate})")
    if sig.order_block is not None:
        ob = sig.order_block
        lines.append(f"오더블록 존: `{_fmt_price(ob.bottom)} ~ {_fmt_price(ob.top)}`")
        lines.append(f"손절가: `{_fmt_price(_stop_loss_price(direction, ob))}` (오더블록 무효화)")
    tp = _nearest_take_profit_line(direction, sig.price, sig.indicators.lines)
    if tp is not None:
        label, value = tp
        lines.append(f"익절 목표선: `{label} {_fmt_price(value)}`")
    else:
        lines.append("익절 목표선: `없음` (손절/청산 규칙만 적용)")
    lines.append(f"시각: `{_fmt_time(sig.time)}`")
    return "\n".join(lines)


def format_exit(event: SignalEvent, trade: ClosedTrade | None) -> str:
    """청산 신호를 마크다운 메시지로 포맷한다. `trade`가 있으면 실현 손익을 덧붙인다."""
    sig = event.signal
    is_take_profit = sig.exit_reason is SignalExitReason.TAKE_PROFIT
    header_emoji = "🎯" if is_take_profit else "🛑"
    reason_label = "익절 (EMA/VWMA 선 도달)" if is_take_profit else "손절 (오더블록 무효화)"
    lines = [
        f"{header_emoji} *청산 신호* — `{event.symbol}` `{event.timeframe}`",
        f"방향: *{_direction_label(sig.direction)}* 청산",
        f"청산가: `{_fmt_price(sig.price)}`",
        f"사유: {reason_label}",
    ]
    if trade is not None:
        pnl = trade.realized_pct
        emoji = "📈" if pnl >= 0 else "📉"
        lines.append(f"진입가: `{_fmt_price(trade.position.entry_price)}`")
        lines.append(f"손익: {emoji} `{pnl:+.2f}%`")
    lines.append(f"시각: `{_fmt_time(sig.time)}`")
    return "\n".join(lines)


def _entry_intent(event: SignalEvent) -> EntryIntent:
    """진입 신호 이벤트를 execution 엔진 진입 의도로 변환한다.

    손절가는 오더블록 무효화(존 경계), 익절가는 진입가 너머 가장 가까운 목표선을
    쓴다. 손절 참조가가 없으면(오더블록 없음) 사이징이 불가하므로 엔진이 진입을
    스킵한다.
    """
    sig = event.signal
    stop_price = (
        _stop_loss_price(sig.direction, sig.order_block) if sig.order_block is not None else None
    )
    tp = _nearest_take_profit_line(sig.direction, sig.price, sig.indicators.lines)
    return EntryIntent(
        symbol=event.symbol,
        timeframe=event.timeframe,
        direction=sig.direction,
        entry_price=sig.price,
        entry_time=sig.time,
        stop_price=stop_price,
        take_profit_price=tp[1] if tp is not None else None,
    )


def _position_summary(positions: list[Position]) -> str:
    """현재 오픈 포지션들을 한 줄 요약으로."""
    if not positions:
        return "없음"
    parts = [
        f"{p.symbol} {p.timeframe} {_direction_label(p.direction)} `{_fmt_qty(p.quantity)}`"
        for p in positions
    ]
    return " · ".join(parts)


def format_entry_exec(event: SignalEvent, report: TradeReport) -> str:
    """진입 신호 + 페이퍼 집행 결과(체결가·수량·리스크·포지션 요약)를 포맷한다."""
    lines = [format_entry(event)]
    outcome = report.outcome
    if report.accepted and outcome.position is not None and outcome.fill is not None:
        pos = outcome.position
        lines.append("— 페이퍼 집행 —")
        lines.append(f"체결가: `{_fmt_price(outcome.fill.average_price)}`")
        lines.append(f"수량: `{_fmt_qty(pos.quantity)}` (명목 `{_fmt_money(pos.notional)}`)")
        if report.risk_amount is not None:
            lines.append(f"리스크 금액: `{_fmt_money(report.risk_amount)}`")
        lines.append(f"현재 포지션: {_position_summary(report.open_positions)}")
    else:
        lines.append(f"— 페이퍼 집행: 진입 스킵 ({outcome.reason}) —")
    return "\n".join(lines)


def format_exit_exec(event: SignalEvent, report: TradeReport | None) -> str:
    """청산 신호 + 페이퍼 집행 결과(실현손익·잔여 포지션)를 포맷한다."""
    lines = [format_exit(event, None)]
    if report is None:
        return "\n".join(lines)
    outcome = report.outcome
    if report.accepted and outcome.position is not None and outcome.fill is not None:
        realized = outcome.realized_pnl if outcome.realized_pnl is not None else 0.0
        emoji = "📈" if realized >= 0 else "📉"
        lines.append("— 페이퍼 집행 —")
        lines.append(f"체결가: `{_fmt_price(outcome.fill.average_price)}`")
        lines.append(f"수량: `{_fmt_qty(outcome.position.quantity)}`")
        lines.append(f"실현손익: {emoji} `{_fmt_money(realized)}`")
        lines.append(f"자본: `{_fmt_money(report.equity)}`")
        lines.append(f"현재 포지션: {_position_summary(report.open_positions)}")
    else:
        lines.append(f"— 페이퍼 집행: 청산 없음 ({outcome.reason}) —")
    return "\n".join(lines)


def _to_paper_position(position: Position) -> PaperPosition:
    """execution 포지션을 러너 상태 스냅샷용 페이퍼 포지션으로 변환한다."""
    return PaperPosition(
        symbol=position.symbol,
        timeframe=position.timeframe,
        direction=position.direction,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        stop_price=position.stop_price,
        take_profit_price=position.take_profit_price,
    )


class TradeSink(Protocol):
    """청산된 페이퍼 거래를 받아 어딘가에 영속화하는 싱크(WAN-33).

    구현은 `paper.store.PaperTradeRecorder`가 담당한다. 결합을 피하기 위해 여기서는
    최소 시그니처만 선언한다.
    """

    def record(self, trade: ClosedTrade) -> object: ...


class Notifier:
    """신호 이벤트를 페이퍼 집행/장부에 반영하고 텔레그램으로 전송한다.

    `executor`가 주어지면(WAN-34) 시그널을 execution 엔진으로 집행하고 결과를 DB에
    영속화하며, 알림에 체결가·수량·리스크·현재 포지션을 담는다. `executor`가 없으면
    경량 `PaperBook`으로 포지션만 추적하는 기존 경로를 쓰고(테스트·드라이런),
    `trade_sink`가 있으면 청산 거래를 성과 저장소(`paper_trades`, WAN-33)에 누적한다.

    `telegram`이 None이면 실제 전송 없이 메시지를 로그로만 남긴다(드라이런). 이
    경우에도 집행·장부는 정상 갱신되어 포지션 추적을 검증할 수 있다.
    """

    def __init__(
        self,
        telegram: TelegramClient | None,
        book: PaperBook | None = None,
        *,
        trade_sink: TradeSink | None = None,
        executor: PaperExecutor | None = None,
    ) -> None:
        self._telegram = telegram
        self.book = book if book is not None else PaperBook()
        self._trade_sink = trade_sink
        self._executor = executor

    @property
    def open_positions(self) -> list[PaperPosition]:
        """현재 오픈 중인 페이퍼 포지션(러너 상태 기록용)."""
        if self._executor is not None:
            return [_to_paper_position(p) for p in self._executor.open_positions]
        return self.book.open_positions

    def handle(self, event: SignalEvent, *, now_ms: int | None = None) -> bool:
        """이벤트를 처리한다. 전송 성공(또는 드라이런에서 처리)하면 True."""
        if self._executor is not None:
            resolved_now = int(time.time() * 1000) if now_ms is None else now_ms
            return self._handle_with_executor(event, now_ms=resolved_now)
        return self._handle_with_book(event)

    def _handle_with_executor(self, event: SignalEvent, *, now_ms: int) -> bool:
        """execution 엔진으로 집행하고 결과를 담아 알린다(WAN-34)."""
        assert self._executor is not None  # handle()에서 보장.
        sig = event.signal
        if event.is_entry:
            report = self._executor.enter(_entry_intent(event), now_ms=now_ms)
            message = format_entry_exec(event, report)
        else:
            report = None
            if sig.exit_reason is not None:
                report = self._executor.exit(
                    event.symbol,
                    event.timeframe,
                    exit_price=sig.price,
                    exit_time=sig.time,
                    reason=sig.exit_reason,
                    now_ms=now_ms,
                )
            message = format_exit_exec(event, report)
        return self._send(message)

    def _handle_with_book(self, event: SignalEvent) -> bool:
        """경량 페이퍼 장부만 갱신하는 기존 경로(집행 엔진 없음, WAN-25/33)."""
        sig = event.signal
        if event.is_entry:
            stop_price = (
                _stop_loss_price(sig.direction, sig.order_block)
                if sig.order_block is not None
                else None
            )
            tp = _nearest_take_profit_line(sig.direction, sig.price, sig.indicators.lines)
            self.book.open(
                PaperPosition(
                    symbol=event.symbol,
                    timeframe=event.timeframe,
                    direction=sig.direction,
                    entry_time=sig.time,
                    entry_price=sig.price,
                    stop_price=stop_price,
                    take_profit_price=tp[1] if tp is not None else None,
                )
            )
            message = format_entry(event)
        else:
            trade: ClosedTrade | None = None
            if sig.exit_reason is not None:
                trade = self.book.close(
                    event.symbol,
                    event.timeframe,
                    exit_time=sig.time,
                    exit_price=sig.price,
                    reason=sig.exit_reason,
                )
                if trade is not None and self._trade_sink is not None:
                    self._trade_sink.record(trade)
            message = format_exit(event, trade)
        return self._send(message)

    def _send(self, message: str) -> bool:
        if self._telegram is None:
            _logger.info("[드라이런] 텔레그램 미설정 — 메시지:\n%s", message)
            return False
        return self._telegram.send_message(message)
