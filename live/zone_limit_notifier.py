"""존-지정가 페이퍼 러너의 매매 이벤트 → 텔레그램 알림 (WAN-189).

채택 페이퍼 러너(`live.zone_limit_runner`, WAN-45)는 지정가 체결·손절·익절을 로그와
`order_journal`에만 남겨, 서버에 올려두고 폰으로 지켜보려 해도 "포지션이 잡혔는지·
손절났는지" 실시간으로 안 왔다. 이 모듈이 그 빈틈을 메운다 — **기존 텔레그램 경로**
(`common.telegram.TelegramClient`)와 **공유 포맷**(`live.message_format`)을 그대로 쓴다
(새 전송 채널·중복 포맷 금지, WAN-45/100 교훈).

## 보내는 것 (사용자와 확정한 필드 스펙, 2026-07-24)

* **진입 체결**(`filled`): 종목·TF·방향 / 진입가 / 손절·익절(±R) / 리스크 %·**명목 금액**
  / **체결 품질 줄**(관통 bp · 대기 시간 · 통과 존폭 ×ATR) / 시각. 체결 품질 줄이 이
  러너의 1순위 목적인 "닿으면 체결" 낙관 가정 실측(WAN-96)을 폰에서 보게 하는 자다.
  ⚠️ **수량(코인 개수)은 넣지 않는다 — 명목 금액만**(종목마다 자릿수가 제각각이라 폰에서
  크기 감이 안 온다; 대조가 필요하면 `order_journal`·`fill_report`에 남는다).
* **청산**(`exit`): 사유(손절/익절) / 진입가→청산가 / 실현 손익 = R 배수 + 금액 + %
  / 보유 시간 / 지갑 잔고·오늘 손익.
* **만료·예약은 실시간으로 안 보낸다**(9종목 × 3TF면 하루 수십 건이라 시끄럽다). 대신
  하루 1회 **일일 요약**(`daily_summary`): `예약 40 · 체결 22 · 만료 18`에 더해
  **체결률 + 미진입 사유 카운트**(WAN-221)를 얹는다 — `체결률 55.0% (체결 22 / 미체결 18)`에
  이어 사유마다 **사람이 읽는 문장 한 줄**(WAN-221 변경요청): `· 지정가에 안 닿아 만료 …… 18`,
  `· 존이 너무 넓어 제외 …… 5` 등. 사유 숫자는
  러너가 넘긴 DB 장부 창 조회(`OrderJournal.funnel_counts`, WAN-217)에서 오며 여기서
  재계산하지 않는다. ⚠️ `no_fill`은 **건별로 보내지 않는다**(체결률 50~80%라 미체결이
  다수 케이스 → 건별이면 알림 피로). 요약에만 실린다.
* **존폭·밴드기각 건별 알림**(`filter_skip`, WAN-221): 기본 꺼짐 옵트인. no_fill보다 드문
  필터 거부를 초반 검증용으로 켤 때만 쓴다(`paper_trade_notify_events`에 `filter_skip` 추가).

시각은 전부 KST(WAN-172), 저장·계산은 UTC epoch ms 그대로다. 텔레그램 미설정 시
드라이런(로그만) — `notifier`의 기존 관행 그대로다. 페이퍼 한정: 실주문·자금 이동은
없다(`ALPHABLOCK_LIVE_TRADING=false` 불변).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta

from common.telegram import TelegramClient
from common.timefmt import KST
from execution.models import Position
from live.executor import TradeReport
from live.limit_orders import LimitFill
from live.message_format import (
    direction_label,
    fmt_duration,
    fmt_pct,
    fmt_price,
    fmt_time,
    fmt_usd,
    short_symbol,
)
from live.order_journal import FunnelCounts
from strategy.models import OrderBlockDirection, SignalExitReason

_logger = logging.getLogger(__name__)

#: 설정으로 켤 수 있는 이벤트 종류. 실시간은 체결·청산만, 만료는 일일 요약에만 실린다.
#: `filter_skip`(WAN-221)은 존폭·밴드기각의 **건별** 텔레그램이라 기본에서 빠져 있다
#: (옵트인 — no_fill보다 드문 필터 거부를 초반 검증용으로 켤 때만 쓴다).
VALID_EVENTS = frozenset({"filled", "exit", "daily_summary", "filter_skip"})

#: 설정 미지정 시 켜는 기본 이벤트(스팸 방지: 실시간은 체결·청산, 만료는 일일 요약).
#: `filter_skip`은 넣지 않는다 — 필터 거부 건별 알림은 명시적 옵트인이다(WAN-221).
DEFAULT_EVENTS: tuple[str, ...] = ("filled", "exit", "daily_summary")

#: `no_fill`은 이 값 집합 어디에도 없다 — 지정가에 안 닿은 만료는 **건별 텔레그램으로
#: 보내지 않는다**(WAN-221 사용자 결정: 체결률 50~80%라 미체결이 다수라서 건별로 쏘면
#: 알림 피로로 진입·거부 알림이 묻힌다). 요약에만 카운트로 실린다.
FILTER_SKIP_EVENT = "filter_skip"

_EXIT_WORDS: dict[SignalExitReason, str] = {
    SignalExitReason.TAKE_PROFIT: "익절 청산",
    SignalExitReason.STOP_LOSS: "손절 청산",
}


# -- 순수 포맷 함수 -----------------------------------------------------------


def _fmt_r(value: float) -> str:
    """R 배수를 부호와 함께(`+1.5R`·`−1.0R`). 유니코드 마이너스로 폰에서 또렷하게."""
    sign = "+" if value >= 0 else "−"
    return f"{sign}{abs(value):.1f}R"


def _r_multiple(entry: float, stop: float | None, price: float, *, is_long: bool) -> float | None:
    """진입가 기준 `price`의 R 배수. 1R = |진입가 − 손절 참조가|. 손절이 없으면 None."""
    if stop is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0.0:
        return None
    move = (price - entry) if is_long else (entry - price)
    return move / risk


def format_fill_entry(fill: LimitFill, *, notional: float, risk_pct: float) -> str:
    """진입 체결 알림을 마크다운 메시지로. 체결 품질 줄(관통·대기·존폭)이 핵심이다."""
    is_long = fill.direction is OrderBlockDirection.BULLISH
    header = (
        f"🟢 *진입 체결* · {short_symbol(fill.symbol)} {fill.timeframe} "
        f"{direction_label(fill.direction)}"
    )
    # 손절 참조가가 곧 1R의 정의(1R = |진입가 − 손절|)라 손절은 항상 −1.0R다.
    exits = [f"손절 {fmt_price(fill.stop_price)} (−1.0R)"]
    if fill.take_profit_price is not None:
        tp_r = _r_multiple(fill.price, fill.stop_price, fill.take_profit_price, is_long=is_long)
        tp_label = f" ({_fmt_r(tp_r)})" if tp_r is not None else ""
        exits.append(f"익절 {fmt_price(fill.take_profit_price)}{tp_label}")

    quality = [f"관통 {fill.penetration_bps:.1f}bp"]
    if fill.waited_ms is not None:
        quality.append(f"대기 {fmt_duration(fill.waited_ms)}")
    if fill.zone_width_atr is not None:
        quality.append(f"존폭 {fill.zone_width_atr:.1f}×ATR ✓")

    return "\n".join(
        [
            header,
            f"진입가 {fmt_price(fill.price)} (존 근단 지정가)",
            " · ".join(exits),
            f"리스크 {risk_pct:.1f}% · 명목 {fmt_usd(notional)}",
            "체결: " + " · ".join(quality),
            fmt_time(fill.time),
        ]
    )


def format_position_exit(
    position: Position,
    *,
    exit_price: float,
    reason: SignalExitReason,
    realized_pnl: float,
    equity: float,
    exit_time: int,
    today_pct: float,
) -> str:
    """청산 알림을 마크다운 메시지로. 실현 손익 = R 배수 + 금액 + %를 한 줄에."""
    is_long = position.direction is OrderBlockDirection.BULLISH
    entry = position.entry_price
    r_mult = _r_multiple(entry, position.stop_price, exit_price, is_long=is_long)
    r_label = f"  ({_fmt_r(r_mult)})" if r_mult is not None else ""
    word = _EXIT_WORDS.get(reason, f"{reason.value} 청산")
    move_pct = ((exit_price - entry) if is_long else (entry - exit_price)) / entry * 100.0
    holding = fmt_duration(exit_time - position.entry_time)
    header = (
        f"🔴 *{word}* · {short_symbol(position.symbol)} {position.timeframe} "
        f"{direction_label(position.direction)}{r_label}"
    )
    return "\n".join(
        [
            header,
            f"{fmt_price(entry)} → {fmt_price(exit_price)} · {fmt_pct(move_pct)} · "
            f"{fmt_usd(realized_pnl, signed=True)}",
            f"보유 {holding}",
            f"잔고 {fmt_usd(equity)} · 오늘 {fmt_pct(today_pct)}",
            fmt_time(exit_time),
        ]
    )


def format_entry_rejected(fill: LimitFill, *, reason: str) -> str:
    """체결됐지만 집행 계층이 진입을 거부했을 때의 알림(WAN-194).

    이 알림이 없던 동안 거부는 INFO 로그 한 줄이 전부였고, 폰으로 보는 운영자에게는
    "체결도 진입도 없었다"와 구분되지 않았다. 사유를 그대로 실어 보내 장부를 열지 않고도
    가드가 걸렀는지 알 수 있게 한다(대부분은 손절폭 가드 0.3% — WAN-79).
    """
    header = (
        f"⚪️ *진입 거부* · {short_symbol(fill.symbol)} {fill.timeframe} "
        f"{direction_label(fill.direction)}"
    )
    return "\n".join(
        [
            header,
            f"지정가는 체결됐으나 포지션을 열지 않았습니다 — {reason}",
            f"체결가 {fmt_price(fill.price)} · 손절 참조 {fmt_price(fill.stop_price)}",
            fmt_time(fill.time),
        ]
    )


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


#: 사유 코드 → 사람이 읽는 문구. 일일 요약과 건별 필터 알림이 **한 출처**를 공유해,
#: 같은 사유가 두 곳에서 다르게 보이지 않게 한다(WAN-221 변경요청 2026-07-31).
_REASON_PHRASES: dict[str, str] = {
    "no_fill": "지정가에 안 닿아 만료",
    "zone_width": "존이 너무 넓어 제외",
    "deviation": "밴드가 불리해 제외",
    "cell_busy": "같은 종목·주기에 포지션 보유 중",
    "notional": "명목 한도 초과",
    "sizing": "손절이 너무 짧아 제외",
    "retap": "이미 진입한 존의 재탭이라 제외",
}


def format_daily_summary(
    day: date,
    *,
    placed: int,
    filled: int,
    expired: int,
    funnel: FunnelCounts | None = None,
) -> str:
    """일일 요약 — 실시간으로 안 보내는 예약·만료에 더해, **체결률 + 미진입 사유 카운트**를
    한눈에 얹는다(WAN-221). 사유 숫자는 러너가 넘긴 `funnel`(DB 장부 조회 · WAN-217)에서
    오며 여기서 **재계산하지 않는다**. `funnel`이 없으면 예약/체결/만료 한 줄만 낸다."""
    lines = [
        f"📊 *일일 요약* · {day.isoformat()} (KST)",
        f"예약 {placed} · 체결 {filled} · 만료 {expired}",
    ]
    if funnel is not None:
        lines.append(
            f"체결률 {_fmt_rate(funnel.fill_rate)} (체결 {funnel.filled} / 미체결 {funnel.no_fill})"
        )
        # 미진입 사유 — 체결률의 분모(no_fill)와 깔때기의 다른 탈락 사유를, 압축어 대신
        # 사람이 읽는 문장으로 사유마다 한 줄씩(WAN-221 변경요청 2026-07-31).
        lines.append("")
        lines.append("*미진입 사유*")
        for count, code in (
            (funnel.no_fill, "no_fill"),
            (funnel.zone_width, "zone_width"),
            (funnel.deviation, "deviation"),
            (funnel.cell_busy, "cell_busy"),
            (funnel.notional, "notional"),
            (funnel.sizing, "sizing"),
        ):
            # 카운트 0인 사유도 그대로 노출한다(예: `명목 한도 초과 …… 0`).
            lines.append(f"· {_REASON_PHRASES[code]} …… {count}")
        # 채택 기본값에서 0인 사유(재탭·기타)는 나올 때만 덧붙여 요약을 어지럽히지 않는다.
        if funnel.retap:
            lines.append(f"· {_REASON_PHRASES['retap']} …… {funnel.retap}")
        if funnel.other:
            lines.append(f"· 기타 …… {funnel.other}")
    return "\n".join(lines)


def format_filter_skip(reason: str, *, symbol: str, timeframe: str, time_ms: int) -> str:
    """존폭·밴드기각의 **건별** 알림(옵트인, WAN-221). 초반 필터 검증용이라 짧게 낸다."""
    word = _REASON_PHRASES.get(reason, f"{reason} 기각")
    return "\n".join(
        [
            f"🟡 *필터 미진입* · {short_symbol(symbol)} {timeframe} — {word}",
            fmt_time(time_ms),
        ]
    )


def _kst_date(now_ms: int) -> date:
    """epoch ms → KST 날짜(일일 경계 판정용)."""
    return datetime.fromtimestamp(now_ms / 1000, tz=KST).date()


# -- 오케스트레이터 ------------------------------------------------------------


class ZoneLimitNotifier:
    """존-지정가 러너의 매매 이벤트를 텔레그램으로 보내는 알림기(페이퍼 한정).

    `telegram`이 None이면 실제 전송 없이 메시지를 로그로만 남긴다(드라이런). `events`로
    켤 이벤트 종류를 고른다(`VALID_EVENTS`의 부분집합) — 알 수 없는 이름은 무시한다.
    실시간 전송은 체결·청산만; 예약·만료는 하루치를 모아 일일 요약 한 줄로 보낸다.
    """

    def __init__(
        self,
        telegram: TelegramClient | None,
        *,
        events: frozenset[str] | None = None,
        funnel_provider: Callable[[int, int], FunnelCounts] | None = None,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._telegram = telegram
        self._events = frozenset(events) if events is not None else frozenset(DEFAULT_EVENTS)
        #: 일일 요약의 체결률·사유 카운트를 내는 창 조회(러너가 `OrderJournal.funnel_counts`로
        #: 배선, WAN-221). None이면 요약은 예약/체결/만료 한 줄만 낸다(재계산 금지 — 없으면 뺀다).
        self._funnel_provider = funnel_provider
        self._now_ms = now_ms
        #: 일일 요약 카운터(오늘 KST). tick()이 날짜 경계에서 비운다.
        self._placed = 0
        self._filled = 0
        self._expired = 0
        #: 현재 집계 중인 KST 날짜와 그 날 시작 시점의 지갑 자본(오늘 손익률 기준).
        self._day: date | None = None
        self._day_open_equity: float | None = None

    # -- 폴링 훅 -------------------------------------------------------------

    def tick(self, equity: float, *, now_ms: int | None = None) -> None:
        """매 폴링마다 호출: KST 날짜 경계에서 전날 일일 요약을 보내고 카운터를 비운다.

        새 날의 첫 tick에 그 시점 자본을 "오늘 손익률"의 기준으로 잡는다 — 청산 알림의
        `오늘 X%`가 자정 무렵 자본 대비 변화를 뜻하게 된다(거래 전에 잡히므로 편향이 적다).
        """
        now = self._now_ms() if now_ms is None else now_ms
        day = _kst_date(now)
        if self._day is None:
            self._day = day
            self._day_open_equity = equity
            return
        if day != self._day:
            self._flush_daily_summary(self._day)
            self._placed = self._filled = self._expired = 0
            self._day = day
            self._day_open_equity = equity

    # -- 이벤트 -------------------------------------------------------------

    def note_placed(self) -> None:
        """지정가 예약을 일일 카운터에만 센다(실시간 전송 안 함)."""
        self._placed += 1

    def note_expired(self) -> None:
        """미체결 만료를 일일 카운터에만 센다(실시간 전송 안 함).

        ⚠️ `no_fill`(지정가에 안 닿은 만료)은 여기서 **건별 전송하지 않는다**(WAN-221) —
        만료는 실시간 사건이 아니라 유효기간 만료로만 확정되고, 다수 케이스라 건별로 쏘면
        시끄럽다. 요약의 체결률 분모로만 실린다."""
        self._expired += 1

    def note_filter_skip(self, reason: str, *, symbol: str, timeframe: str, time_ms: int) -> None:
        """존폭·밴드기각의 **건별** 알림(옵트인 — `filter_skip` 이벤트가 켜졌을 때만, WAN-221).

        기본값에서는 꺼져 있어(요약 집계만) 아무것도 보내지 않는다. `no_fill`은 이 경로로
        **절대** 오지 않는다 — 러너가 no_fill을 이 훅에 넘기지 않는다(만료는 `note_expired`)."""
        if FILTER_SKIP_EVENT not in self._events:
            return
        self._send(format_filter_skip(reason, symbol=symbol, timeframe=timeframe, time_ms=time_ms))

    def handle_fill(self, fill: LimitFill, report: TradeReport) -> None:
        """체결 → 진입 알림. 진입이 거부되면 **거부 알림**을 보낸다(WAN-194).

        옛 동작은 거부 시 조용히 반환하는 것이었다 — 그래서 체결이 거래가 되지 않은
        사건이 폰에서 "아무 일도 없었다"와 같아 보였다. 같은 `filled` 이벤트 스위치를
        쓴다(체결은 났으므로 같은 부류의 사건이고, 스위치를 늘리면 기본값에서 꺼진
        새 채널이 또 조용해진다).
        """
        self._filled += 1
        if "filled" not in self._events:
            return
        position = report.outcome.position
        if not report.accepted or position is None:
            self._send(format_entry_rejected(fill, reason=report.outcome.reason or "사유 미기록"))
            return
        fee = report.outcome.fill.fee if report.outcome.fill is not None else 0.0
        equity_before = report.equity + fee
        risk_pct = (
            report.risk_amount / equity_before * 100.0
            if report.risk_amount is not None and equity_before > 0.0
            else 0.0
        )
        self._send(format_fill_entry(fill, notional=position.notional, risk_pct=risk_pct))

    def handle_exit(
        self, report: TradeReport, *, exit_price: float, reason: SignalExitReason, exit_time: int
    ) -> None:
        """청산 → 청산 알림(실현 손익 R·금액·% + 잔고·오늘 손익)."""
        if "exit" not in self._events:
            return
        position = report.outcome.position
        if not report.accepted or position is None:
            return
        realized = report.outcome.realized_pnl if report.outcome.realized_pnl is not None else 0.0
        self._send(
            format_position_exit(
                position,
                exit_price=exit_price,
                reason=reason,
                realized_pnl=realized,
                equity=report.equity,
                exit_time=exit_time,
                today_pct=self._today_pct(report.equity),
            )
        )

    # -- 내부 ---------------------------------------------------------------

    def _today_pct(self, equity: float) -> float:
        base = self._day_open_equity
        if base is None or base <= 0.0:
            return 0.0
        return (equity - base) / base * 100.0

    def _flush_daily_summary(self, day: date) -> None:
        if "daily_summary" not in self._events:
            return
        if self._placed == 0 and self._filled == 0 and self._expired == 0:
            return  # 아무 일도 없던 날은 보내지 않는다(스팸 방지).
        self._send(
            format_daily_summary(
                day,
                placed=self._placed,
                filled=self._filled,
                expired=self._expired,
                funnel=self._day_funnel(day),
            )
        )

    def _day_funnel(self, day: date) -> FunnelCounts | None:
        """그 KST 하루 `[자정, 다음 자정)`의 깔때기 사유를 DB 장부에서 조회한다(WAN-221).

        KST는 서머타임이 없어 하루가 정확히 24h지만, 경계는 `timedelta`로 잡아 상수(86_400_000)를
        박지 않는다. 조회 실패는 요약 자체를 막지 않도록 삼키고 None을 준다(예약/체결/만료
        한 줄은 계속 나간다)."""
        if self._funnel_provider is None:
            return None
        start = datetime(day.year, day.month, day.day, tzinfo=KST)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int((start + timedelta(days=1)).timestamp() * 1000)
        try:
            return self._funnel_provider(start_ms, end_ms)
        except Exception:  # noqa: BLE001 — 장부 조회 실패가 일일 요약을 막지 않도록.
            _logger.exception("일일 요약 깔때기 조회 실패: %s", day.isoformat())
            return None

    def _send(self, message: str) -> bool:
        if self._telegram is None:
            _logger.info("[드라이런] 텔레그램 미설정 — 매매 알림:\n%s", message)
            return False
        return self._telegram.send_message(message)
