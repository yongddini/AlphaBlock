"""존-지정가 러너 매매 알림 테스트 (WAN-189).

필드 스펙(진입 체결 품질 줄·명목만 수량 없음·청산 R+금액+%)·드라이런·이벤트 토글·
일일 요약·포맷 공유(이중화 없음)를 동작으로 고정한다.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from common.telegram import TelegramClient, TelegramResponse
from execution.engine import ExecutionOutcome
from execution.models import Fill, Order, OrderSide, OrderStatus, OrderType, Position
from live import message_format, notifier, zone_limit_notifier
from live.executor import TradeReport
from live.limit_orders import LimitFill
from live.zone_limit_notifier import (
    ZoneLimitNotifier,
    format_daily_summary,
    format_fill_entry,
    format_position_exit,
)
from strategy.models import OrderBlockDirection, SignalExitReason

_H = 3_600_000
_M = 60_000
_SYMBOL = "BTC/USDT:USDT"
_TF = "15m"


def _fill(**overrides: Any) -> LimitFill:
    base: dict[str, Any] = dict(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        price=62_340.0,
        time=14 * _H,
        rsi=None,
        stop_price=61_720.0,
        take_profit_price=63_270.0,
        penetration_bps=1.2,
        waited_ms=3 * _M,
        zone_width_atr=0.9,
    )
    base.update(overrides)
    return LimitFill(**base)


def _entry_report(*, accepted: bool = True) -> TradeReport:
    position = Position(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        quantity=0.08,
        entry_price=62_340.0,
        entry_time=14 * _H,
        stop_price=61_720.0,
        take_profit_price=63_270.0,
    )
    order = Order(symbol=_SYMBOL, side=OrderSide.BUY, type=OrderType.MARKET, quantity=0.08)
    fill = Fill(
        order=order,
        status=OrderStatus.FILLED,
        filled_quantity=0.08,
        average_price=62_340.0,
        fee=1.0,
    )
    if not accepted:
        return TradeReport(
            outcome=ExecutionOutcome.rejected("이미 오픈 포지션"),
            open_positions=[],
            equity=10_000.0,
        )
    return TradeReport(
        outcome=ExecutionOutcome(accepted=True, position=position, fill=fill),
        risk_amount=100.0,
        open_positions=[position],
        equity=9_999.0,
    )


def _exit_report(*, realized: float = 74.0, equity: float = 10_074.0) -> TradeReport:
    position = Position(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        quantity=0.08,
        entry_price=62_340.0,
        entry_time=14 * _H,
        stop_price=61_720.0,
        take_profit_price=63_270.0,
    )
    order = Order(
        symbol=_SYMBOL, side=OrderSide.SELL, type=OrderType.MARKET, quantity=0.08, reduce_only=True
    )
    fill = Fill(
        order=order, status=OrderStatus.FILLED, filled_quantity=0.08, average_price=63_270.0
    )
    return TradeReport(
        outcome=ExecutionOutcome(
            accepted=True, position=position, fill=fill, realized_pnl=realized
        ),
        open_positions=[],
        equity=equity,
    )


# -- 포맷 스펙 ---------------------------------------------------------------


def test_fill_entry_message_matches_field_spec() -> None:
    msg = format_fill_entry(_fill(), notional=4_987.0, risk_pct=1.0)
    assert "🟢 *진입 체결* · BTC 15m 롱" in msg
    assert "진입가 62,340 (존 근단 지정가)" in msg
    assert "손절 61,720 (−1.0R)" in msg
    assert "익절 63,270 (+1.5R)" in msg  # 1.5R = (63270-62340)/(62340-61720)
    assert "리스크 1.0% · 명목 $4,987" in msg
    assert "체결: 관통 1.2bp · 대기 3분 · 존폭 0.9×ATR ✓" in msg
    assert "KST" in msg


def test_fill_entry_has_no_quantity() -> None:
    """수량(코인 개수)은 넣지 않는다 — 명목 금액만(스펙 ⚠️)."""
    msg = format_fill_entry(_fill(), notional=4_987.0, risk_pct=1.0)
    assert "0.08" not in msg
    assert "수량" not in msg


def test_fill_entry_omits_zone_width_when_filter_off() -> None:
    msg = format_fill_entry(_fill(zone_width_atr=None), notional=4_987.0, risk_pct=1.0)
    assert "×ATR" not in msg
    assert "관통 1.2bp · 대기 3분" in msg


def test_exit_message_has_r_multiple_amount_and_pct() -> None:
    msg = format_position_exit(
        _exit_report().outcome.position,  # type: ignore[arg-type]
        exit_price=63_270.0,
        reason=SignalExitReason.TAKE_PROFIT,
        realized_pnl=74.0,
        equity=10_074.0,
        exit_time=14 * _H + 47 * _M,
        today_pct=2.1,
    )
    assert "🔴 *익절 청산* · BTC 15m 롱  (+1.5R)" in msg
    assert "62,340 → 63,270 · +1.49% · +$74" in msg
    assert "보유 47분" in msg
    assert "잔고 $10,074 · 오늘 +2.10%" in msg


def test_exit_stop_loss_shows_negative() -> None:
    position = _exit_report().outcome.position
    assert position is not None
    msg = format_position_exit(
        position,
        exit_price=61_720.0,
        reason=SignalExitReason.STOP_LOSS,
        realized_pnl=-100.0,
        equity=9_900.0,
        exit_time=14 * _H + 10 * _M,
        today_pct=-1.0,
    )
    assert "🔴 *손절 청산*" in msg
    assert "(−1.0R)" in msg
    assert "−$100" in msg


def test_daily_summary_line() -> None:
    from datetime import date

    msg = format_daily_summary(date(2026, 7, 24), placed=40, filled=22, expired=18)
    assert "예약 40 · 체결 22 · 만료 18" in msg


# -- 전송·드라이런·토글 ------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, url: str, payload: dict[str, Any]) -> TelegramResponse:
        self.sent.append(str(payload["text"]))
        return TelegramResponse(ok=True, status_code=200)


def _client(recorder: _Recorder) -> TelegramClient:
    return TelegramClient("token", "chat", transport=recorder)


def test_fill_sends_when_configured() -> None:
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), now_ms=lambda: 14 * _H)
    notif.handle_fill(_fill(), _entry_report())
    assert len(rec.sent) == 1 and "진입 체결" in rec.sent[0]


def test_dry_run_logs_when_telegram_missing(caplog: pytest.LogCaptureFixture) -> None:
    notif = ZoneLimitNotifier(None, now_ms=lambda: 14 * _H)
    with caplog.at_level(logging.INFO, logger="live.zone_limit_notifier"):
        notif.handle_fill(_fill(), _entry_report())
    assert any("드라이런" in r.message for r in caplog.records)


def test_rejected_entry_sends_rejection_notice_and_counts() -> None:
    """거부도 알림으로 나간다 (WAN-194 — 옛 동작은 조용한 반환이었고 그게 버그였다).

    체결은 났는데 포지션이 열리지 않은 사건이 폰에서 "아무 일도 없었다"와 같아 보이면,
    운영자는 장부와 성과가 어긋난 걸 알 수 없다(WAN-194가 손상 의심으로 시작한 이유).
    사유를 실어 보내 장부를 열지 않고도 가드가 걸렀는지 알 수 있게 한다.
    """
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), now_ms=lambda: 14 * _H)
    notif.handle_fill(_fill(), _entry_report(accepted=False))
    assert len(rec.sent) == 1
    assert "진입 거부" in rec.sent[0]
    assert "이미 오픈 포지션" in rec.sent[0]  # 거부 사유가 그대로 실린다.
    assert "진입 체결" not in rec.sent[0]  # 진입 알림으로 오인되지 않는다.

    # 체결 자체는 일어났으니 일일 카운터에도 잡힌다(거부와 별개의 자다).
    notif.tick(10_000.0, now_ms=14 * _H)
    notif.tick(10_000.0, now_ms=14 * _H + 24 * _H)
    assert any("체결 1" in s for s in rec.sent)


def test_filled_event_toggle_off_suppresses_send() -> None:
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), events=frozenset({"exit"}), now_ms=lambda: 14 * _H)
    notif.handle_fill(_fill(), _entry_report())
    assert rec.sent == []


def test_filled_toggle_also_governs_rejection_notice() -> None:
    """거부 알림은 `filled` 스위치를 함께 쓴다 (WAN-194).

    스위치를 새로 늘리면 기본값에서 꺼진 채널이 또 하나 생겨 같은 침묵이 재발한다 —
    체결은 났으므로 같은 부류의 사건으로 묶는다.
    """
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), events=frozenset({"exit"}), now_ms=lambda: 14 * _H)
    notif.handle_fill(_fill(), _entry_report(accepted=False))
    assert rec.sent == []


def test_exit_uses_day_open_equity_for_today_pct() -> None:
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), now_ms=lambda: 14 * _H)
    notif.tick(10_000.0, now_ms=14 * _H)  # 오늘 시작 자본 = 10,000.
    notif.handle_exit(
        _exit_report(equity=10_200.0),
        exit_price=63_270.0,
        reason=SignalExitReason.TAKE_PROFIT,
        exit_time=14 * _H + 47 * _M,
    )
    assert len(rec.sent) == 1
    assert "오늘 +2.00%" in rec.sent[0]  # (10200-10000)/10000.


# -- 일일 요약 경계 ----------------------------------------------------------


def test_daily_summary_emitted_on_day_rollover() -> None:
    rec = _Recorder()
    day0 = 0  # 1970-01-01 KST.
    notif = ZoneLimitNotifier(_client(rec))
    notif.tick(10_000.0, now_ms=day0)
    notif.note_placed()
    notif.note_placed()
    notif.note_expired()
    notif.tick(10_000.0, now_ms=day0 + 24 * _H + _H)  # 다음 날.
    assert len(rec.sent) == 1
    assert "예약 2 · 체결 0 · 만료 1" in rec.sent[0]


def test_no_daily_summary_when_no_events() -> None:
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec))
    notif.tick(10_000.0, now_ms=0)
    notif.tick(10_000.0, now_ms=24 * _H + _H)
    assert rec.sent == []


def test_daily_summary_toggle_off() -> None:
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), events=frozenset({"filled"}))
    notif.tick(10_000.0, now_ms=0)
    notif.note_placed()
    notif.tick(10_000.0, now_ms=24 * _H + _H)
    assert rec.sent == []


# -- 포맷 공유(이중화 없음) --------------------------------------------------


def test_format_helpers_are_shared_single_source() -> None:
    """지정가 러너 알림과 옛 시그널 러너가 같은 포맷 함수를 공유한다(WAN-189).

    별칭이 아니라 사본이 되는 순간 두 경로가 갈라질 수 있으므로 `is`로 고정한다.
    `vars(...)`로 모듈 네임스페이스를 직접 보는 것은 재수출한 이름의 런타임 동일성만
    보기 때문이다(정적 재수출 규칙과 무관).
    """
    assert vars(zone_limit_notifier)["fmt_price"] is message_format.fmt_price
    assert vars(zone_limit_notifier)["fmt_time"] is message_format.fmt_time
    # 옛 notifier의 사설 별칭도 같은 구현을 가리킨다(중복 정의 아님).
    assert notifier._fmt_price is message_format.fmt_price
    assert notifier._fmt_time is message_format.fmt_time
