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
from execution.risk import CircuitBreakerStatus
from live import message_format, zone_limit_notifier
from live.executor import TradeReport
from live.limit_orders import LimitFill
from live.order_journal import FunnelCounts
from live.zone_limit_notifier import (
    ZoneLimitNotifier,
    format_circuit_breaker_cleared,
    format_circuit_breaker_tripped,
    format_daily_summary,
    format_fill_entry,
    format_filter_skip,
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
    # funnel 없이는 체결률·사유 줄이 붙지 않는다(재계산 금지 — 없으면 뺀다).
    assert "체결률" not in msg


def test_daily_summary_appends_fill_rate_and_reasons() -> None:
    """funnel이 붙으면 체결률과 6개 사유가 **문장형 한 줄씩** 실린다(WAN-221 변경요청)."""
    from datetime import date

    funnel = FunnelCounts(
        filled=22, no_fill=18, deviation=3, zone_width=5, cell_busy=2, notional=1, sizing=4
    )
    msg = format_daily_summary(date(2026, 7, 24), placed=40, filled=22, expired=21, funnel=funnel)
    assert "예약 40 · 체결 22 · 만료 21" in msg
    assert "체결률 55.0% (체결 22 / 미체결 18)" in msg  # 22 / (22+18).
    assert "미진입 사유*" in msg  # 사유 블록 헤더.
    # 압축어 대신 사람이 읽는 문장으로 사유마다 한 줄씩.
    assert "· 지정가에 안 닿아 만료 …… 18" in msg
    assert "· 존이 너무 넓어 제외 …… 5" in msg
    assert "· 밴드가 불리해 제외 …… 3" in msg
    assert "· 같은 종목·주기에 포지션 보유 중 …… 2" in msg
    assert "· 명목 한도 초과 …… 1" in msg
    assert "· 손절이 너무 짧아 제외 …… 4" in msg


def test_daily_summary_fill_rate_none_when_no_resolved() -> None:
    """체결·미체결이 모두 0이면 체결률은 '-'로 낸다(빈 장부에서도 안 깨진다, WAN-221)."""
    from datetime import date

    msg = format_daily_summary(
        date(2026, 7, 24), placed=0, filled=0, expired=0, funnel=FunnelCounts()
    )
    assert "체결률 - (체결 0 / 미체결 0)" in msg
    # 카운트 0인 사유도 그대로 노출한다(WAN-221 변경요청 — 현재 동작 유지).
    assert "· 명목 한도 초과 …… 0" in msg


def test_filter_skip_message_is_short() -> None:
    """건별 필터 알림도 요약과 **같은 문구 출처**(`_REASON_PHRASES`)를 쓴다(WAN-221 변경요청)."""
    msg = format_filter_skip("zone_width", symbol=_SYMBOL, timeframe=_TF, time_ms=14 * _H)
    assert "필터 미진입" in msg and "존이 너무 넓어 제외" in msg


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


class _FunnelProvider:
    """창을 기록하고 고정 카운트를 돌려주는 가짜 공급자(WAN-221 일일 요약 배선 테스트용)."""

    def __init__(self, counts: FunnelCounts) -> None:
        self.counts = counts
        self.window: tuple[int, int] | None = None

    def __call__(self, start_ms: int, end_ms: int) -> FunnelCounts:
        self.window = (start_ms, end_ms)
        return self.counts


def test_daily_summary_uses_funnel_provider_on_rollover() -> None:
    """날짜 경계에서 DB 창 조회로 체결률·사유가 요약에 실린다(재계산 아님, WAN-221)."""
    rec = _Recorder()
    prov = _FunnelProvider(FunnelCounts(filled=3, no_fill=1, zone_width=2))
    notif = ZoneLimitNotifier(_client(rec), funnel_provider=prov)
    notif.tick(10_000.0, now_ms=0)
    notif.note_placed()
    notif.note_expired()
    notif.tick(10_000.0, now_ms=24 * _H + _H)  # 다음 날.
    assert len(rec.sent) == 1
    assert "체결률 75.0% (체결 3 / 미체결 1)" in rec.sent[0]  # 3 / (3+1).
    assert "· 존이 너무 넓어 제외 …… 2" in rec.sent[0]
    # 조회 창은 정확히 KST 하루 span이다(서머타임 없음).
    assert prov.window is not None and prov.window[1] - prov.window[0] == 24 * _H


def test_daily_summary_survives_funnel_provider_error() -> None:
    """장부 조회가 터져도 요약(예약/체결/만료)은 나간다(빈 장부 안전 · WAN-221)."""

    def _boom(start_ms: int, end_ms: int) -> FunnelCounts:
        raise RuntimeError("장부 조회 실패")

    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), funnel_provider=_boom)
    notif.tick(10_000.0, now_ms=0)
    notif.note_placed()
    notif.tick(10_000.0, now_ms=24 * _H + _H)
    assert len(rec.sent) == 1
    assert "예약 1 · 체결 0 · 만료 0" in rec.sent[0]
    assert "체결률" not in rec.sent[0]  # 조회 실패면 사유 줄은 뺀다(요약 자체는 보존).


def test_no_fill_expiry_never_sends_per_event_even_with_filter_skip_on() -> None:
    """`no_fill`(안 닿은 만료)은 filter_skip을 켜도 **건별로 안 나간다**(WAN-221 완료 기준).

    만료는 `note_expired`로만 세고 어떤 전송 경로도 타지 않는다 — 러너도 no_fill을
    `note_filter_skip`에 넘기지 않으므로, 두 계층에서 no_fill 건별 알림이 원천 차단된다."""
    rec = _Recorder()
    notif = ZoneLimitNotifier(
        _client(rec), events=frozenset({"filter_skip"}), now_ms=lambda: 14 * _H
    )
    notif.note_expired()
    notif.note_expired()
    assert rec.sent == []


def test_filter_skip_sends_only_when_opted_in() -> None:
    """존폭·밴드기각 건별 알림은 `filter_skip` 옵트인일 때만 나간다(기본 꺼짐, WAN-221)."""
    rec_off = _Recorder()
    off = ZoneLimitNotifier(_client(rec_off), now_ms=lambda: 14 * _H)  # 기본 이벤트(옵트인 아님).
    off.note_filter_skip("zone_width", symbol=_SYMBOL, timeframe=_TF, time_ms=14 * _H)
    assert rec_off.sent == []

    rec_on = _Recorder()
    on = ZoneLimitNotifier(
        _client(rec_on),
        events=frozenset({"filled", "exit", "daily_summary", "filter_skip"}),
        now_ms=lambda: 14 * _H,
    )
    on.note_filter_skip("deviation", symbol=_SYMBOL, timeframe=_TF, time_ms=14 * _H)
    assert len(rec_on.sent) == 1 and "필터 미진입" in rec_on.sent[0]


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
    """지정가 러너 알림이 저수준 포맷 함수를 `live.message_format` 단일 소스에서 쓴다(WAN-189).

    별칭이 아니라 사본이 되는 순간 두 경로가 갈라질 수 있으므로 `is`로 고정한다.
    `vars(...)`로 모듈 네임스페이스를 직접 보는 것은 재수출한 이름의 런타임 동일성만
    보기 때문이다(정적 재수출 규칙과 무관). (옛 A안 `live.notifier` 포맷 별칭은
    WAN-208에서 제거됐다.)
    """
    assert vars(zone_limit_notifier)["fmt_price"] is message_format.fmt_price
    assert vars(zone_limit_notifier)["fmt_time"] is message_format.fmt_time


# -- 일일 손실 서킷브레이커 알림 (WAN-38) ------------------------------------


def _cb_status(*, tripped: bool) -> CircuitBreakerStatus:
    return CircuitBreakerStatus(
        enabled=True,
        tripped=tripped,
        daily_realized_pnl=-600.0,
        loss_limit=500.0,
        baseline_equity=10_000.0,
    )


def test_circuit_breaker_tripped_message_content() -> None:
    msg = format_circuit_breaker_tripped(_cb_status(tripped=True), now_ms=14 * _H)
    assert "서킷브레이커 발동" in msg
    assert "신규 진입을 차단" in msg


def test_circuit_breaker_cleared_message_content() -> None:
    msg = format_circuit_breaker_cleared(_cb_status(tripped=False), now_ms=14 * _H)
    assert "서킷브레이커 해제" in msg
    assert "재개" in msg


def test_circuit_breaker_handlers_send_regardless_of_event_toggle() -> None:
    # 안전 알림이라 이벤트 스위치(여기선 아무것도 안 켬)와 무관하게 나간다.
    rec = _Recorder()
    notif = ZoneLimitNotifier(_client(rec), events=frozenset())
    notif.handle_circuit_breaker_tripped(_cb_status(tripped=True), now_ms=14 * _H)
    notif.handle_circuit_breaker_cleared(_cb_status(tripped=False), now_ms=15 * _H)
    assert len(rec.sent) == 2
    assert "발동" in rec.sent[0]
    assert "해제" in rec.sent[1]
