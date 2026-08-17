"""차트-우선 메인 화면(WAN-245)의 순수 로직 — 좌표·존 선택·표 프레임·지갑 곡선.

`dashboard.app`이 Streamlit 위젯을 그리기 전에 필요한 계산을 전부 여기 모은다. 화면
코드에서 분리해 두면 **테스트가 위젯 없이 동작을 고정**할 수 있다(이 저장소가 반복해
겪은 "라벨은 붙었는데 실제로는 안 도는" 부류의 실패를 막는 장치 — WAN-91/95/112/123).

## 이 모듈이 지키는 두 가지 계약

1. **메인 차트는 최근 봉만 본다.** 6년 전량이 아니라 `CHART_BARS`개(기본 1,200)만
   로드·탐지한다. 분석 탭 cold load ~10초(WAN-202)의 원인이던 "전 구간 재계산 + 통째
   전송"이 이 화면에는 **구조적으로 없다** — 그래서 심볼·TF를 바꿔도 가볍다.
2. **선택지는 채택 좌표에서 온다** — 심볼은 수집 유니버스 전체, TF는 작업 TF 4개
   (15m·1h·2h·4h). 두 가지를 **하면 안 된다**:
   * **저장된 시리즈(`OhlcvStore.list_series`)에서 만들기** — 2h는 물리 저장이 아니라
     1h에서 파생되므로(`data.storage._DERIVED_TIMEFRAMES`) 그 목록에는 **영영 안 뜬다**
     (WAN-24 · 이 이슈의 PM 실측).
   * **러너 감시 목록(`live_signal_*`)에서 만들기** — 그건 배포마다 좁힐 수 있는 운영
     설정이라(로컬 `.env`는 BTC·1h 단독) 화면이 조용히 1×1짜리가 된다. 러너가 채택
     좌표 밖을 보고 있으면 **뒤에 덧붙여** 잃지 않게만 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from common.timefmt import format_kst
from config.settings import Settings
from data.models import timeframe_to_ms
from execution.models import Position
from paper.report import exit_reason_label
from paper.store import PaperTradeRecord
from strategy.models import OrderBlock, OrderBlockDirection, SignalExitReason

#: 채택 좌표의 작업 TF(WAN-182 4h 승격 · WAN-252 2h 승격). 설정이 비었을 때의 기본값이자
#: 표시 순서의 정본이다 — 짧은 TF가 왼쪽(트레이딩뷰 토글 감각).
WORKING_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "2h", "4h")

#: TF 토글에 쓸 한글 라벨. OHLC 범례 1줄에도 같은 문자열이 들어간다.
TIMEFRAME_LABELS: dict[str, str] = {
    "15m": "15분",
    "1h": "1시간",
    "2h": "2시간",
    "4h": "4시간",
}

#: 오버레이할 최근 오더블록 개수(사용자 결정 2026-08-11 — 숏 대비 3→4).
#: 🔁 메인 차트의 선택 기준은 WAN-289에서 **활성 존 `ACTIVE_ZONE_LIMIT`개**로 바뀌었다
#: (`display_zones`) — 이 상수는 활성 존이 하나도 없을 때의 폴백 개수로만 남는다.
RECENT_ZONE_LIMIT = 4

#: 메인 차트에 그릴 **활성(무효화 안 된) 존** 개수(사용자 결정 2026-08-12 — "6으로
#: 넓게 가자"). 옛 규칙(최근 4개, 활성·죽은 존 섞어서)을 대체한다: 활성 존 6개를
#: 고르고, 첫 화면 창을 그중 가장 오래된 존의 생성 봉까지 넓히며, 그 구간의 죽은
#: 존은 회색으로 함께 그린다.
ACTIVE_ZONE_LIMIT = 6

#: 첫 화면 창 왼쪽 여유(봉 수) — 가장 오래된 활성 존이 화면 왼쪽 변에 딱 붙지 않게.
ZONE_VIEW_MARGIN_BARS = 12

#: 메인 차트가 로드하는 최근 봉 수. 최근 4개 존을 담을 만큼 넉넉하되 6년 전량과는
#: 자릿수가 다르다(15m 1,200봉 ≈ 12.5일 · 4h 1,200봉 ≈ 200일).
CHART_BARS = 1_200

#: 차트 오른쪽 여백 = **처음 보이는 창의 비율**(TradingView `rightOffset` 감각 — 사용자
#: 요청 2026-08-11). 최신 봉·활성 존·현재가가 가격축에 딱 붙지 않게 띄운다.
#: ⚠️ 봉 수로 주면 안 된다 — 창의 봉 수가 TF마다 10배 넘게 달라(15m 672봉 · 4h 42봉)
#: 고정 봉 수는 한쪽에서 안 보이고 다른 쪽에서 화면의 1/3을 먹는다(실측).
RIGHT_PAD_RATIO = 0.06


def chart_symbols(settings: Settings) -> list[str]:
    """심볼 선택지 = **수집 유니버스**(채택 종목 전부) + 러너가 감시하는 것 중 빠진 것.

    ⚠️ 러너 감시 목록(`live_signal_symbols`)을 **기준으로 삼지 않는다** — 그건 배포마다
    좁힐 수 있는 운영 설정이라(로컬 `.env`는 BTC 단독) 그걸 기준으로 만들면 화면이
    조용히 1종목짜리가 된다. 이 화면은 **모아 둔 데이터를 보는 뷰어**라 기준은 수집
    유니버스이고, 러너가 그 밖의 심볼을 보고 있으면 잃지 않도록 뒤에 붙인다.
    """
    universe = [s for s in settings.symbols if s]
    extra = [s for s in settings.live_signal_symbols if s and s not in universe]
    return universe + extra


def chart_timeframes(settings: Settings) -> list[str]:
    """TF 선택지 = **작업 TF 4개**(15m·1h·2h·4h) + 러너가 감시하는 것 중 빠진 것.

    `chart_symbols`와 같은 이유로 러너 설정을 기준으로 삼지 않는다 — 채택 좌표의 작업
    TF(WAN-182·WAN-252)가 기준이고, 운영 설정이 그 밖의 TF를 보고 있으면 뒤에 붙인다.
    ⚠️ **저장된 시리즈에서 만들면 2h가 영영 안 뜬다**(파생 TF, WAN-24).
    """
    known = list(WORKING_TIMEFRAMES)
    extra = [tf for tf in settings.live_signal_timeframes if tf and tf not in known]
    return known + extra


def timeframe_label(timeframe: str) -> str:
    return TIMEFRAME_LABELS.get(timeframe, timeframe)


def symbol_label(symbol: str) -> str:
    """OHLC 범례 1줄에 쓸 심볼 표기 — ccxt `"BTC/USDT:USDT"` → `"BTC/USDT PERPETUAL SWAP"`."""
    base, _, settle = symbol.partition(":")
    return f"{base} PERPETUAL SWAP" if settle else base


def legend_title(symbol: str, timeframe: str) -> str:
    """범례 1줄: 심볼 · 계약 유형 · TF (사용자 확정 2026-08-11)."""
    return f"{symbol_label(symbol)} · {timeframe_label(timeframe)}"


def chart_start_ms(last_open_ms: int, timeframe: str, *, bars: int = CHART_BARS) -> int:
    """메인 차트가 읽을 구간의 시작(ms) — 마지막 봉에서 `bars`개 뒤로.

    음수로 내려가지 않게 0에서 자른다(합성 데이터의 짧은 시리즈 방어).
    """
    span = timeframe_to_ms(timeframe) * max(bars - 1, 0)
    return max(0, last_open_ms - span)


def recent_zones(
    order_blocks: Sequence[OrderBlock], *, limit: int = RECENT_ZONE_LIMIT
) -> list[OrderBlock]:
    """가장 최근 오더블록 `limit`개만 남긴다(방향 불문 — 숏 존 포함).

    최신 판정은 **확정 시각**(`confirmed_time`) 기준이고, 같으면 생성 시각으로 가른다.
    반환은 시간 오름차순이라 차트가 그리는 순서가 화면 왼쪽→오른쪽과 같다.

    ⚠️ 여기서 자르는 것이 이 화면의 성능 계약이다(WAN-202 흡수) — 존 수백 개를 실어
    보내면 페이로드가 다시 무거워진다.
    """
    if limit <= 0:
        return []
    ranked = sorted(order_blocks, key=lambda ob: (ob.confirmed_time, ob.start_time), reverse=True)
    picked = ranked[:limit]
    return sorted(picked, key=lambda ob: (ob.confirmed_time, ob.start_time))


def _zone_is_alive(ob: OrderBlock) -> bool:
    """무효화(`break_time`)도 소멸(`swept_time`)도 안 된, 지금 살아있는 존인가."""
    return ob.break_time is None and ob.swept_time is None


def _zone_end_ms(ob: OrderBlock) -> int:
    """죽은 존의 수명 종료 시각(무효화 또는 소멸). 살아있는 존에는 부르지 않는다."""
    if ob.break_time is not None:
        return ob.break_time
    return ob.swept_time if ob.swept_time is not None else ob.start_time


def display_zones(
    order_blocks: Sequence[OrderBlock], *, limit: int = ACTIVE_ZONE_LIMIT
) -> list[OrderBlock]:
    """메인 차트에 그릴 존 = **활성 존 최신 `limit`개** + 그 구간의 죽은 존(회색).

    사용자 결정(2026-08-12, WAN-289): 선택 기준을 "최근 N개(활성·죽은 섞어서)"에서
    "활성 존 `limit`개"로 바꾸고, 창을 그중 가장 오래된 활성 존까지 넓힌다. 그 구간에
    수명이 걸치는 죽은 존은 회색으로 **함께** 그린다(죽은 존 폐기 해석은 취소됐다).

    활성 존이 하나도 없으면 옛 규칙(최근 `RECENT_ZONE_LIMIT`개)으로 폴백한다 —
    빈 차트는 "탐지가 안 도나"로 읽히기 때문이다.

    반환은 시간 오름차순(차트가 왼쪽→오른쪽으로 그리는 순서)이다. 페이로드 상한은
    탐지 창(`CHART_BARS`봉)이 이미 잡는다 — 6년 전량이 아니라 최근 창의 존만 온다.
    """
    active = [ob for ob in order_blocks if _zone_is_alive(ob)]
    if not active:
        return recent_zones(order_blocks, limit=RECENT_ZONE_LIMIT)
    ranked = sorted(active, key=lambda ob: (ob.confirmed_time, ob.start_time), reverse=True)
    picked = ranked[:limit]
    window_start = min(ob.start_time for ob in picked)
    dead_in_window = [
        ob for ob in order_blocks if not _zone_is_alive(ob) and _zone_end_ms(ob) >= window_start
    ]
    return sorted(picked + dead_in_window, key=lambda ob: (ob.confirmed_time, ob.start_time))


def zone_view_start_ms(zones: Sequence[OrderBlock], timeframe: str) -> int | None:
    """첫 화면 창의 왼쪽 경계(ms) = 가장 오래된 **활성** 존 생성 봉 − 여유.

    `display_zones`가 고른 존 목록을 받아, 활성 존이 있으면 그 중 가장 오래된 생성
    시각에서 `ZONE_VIEW_MARGIN_BARS`봉만큼 물러난 시각을 돌려준다(차트는 이 값으로
    처음 보이는 창을 **넓히기만** 한다). 활성 존이 없으면 None(기본 창 유지).
    """
    active_starts = [ob.start_time for ob in zones if _zone_is_alive(ob)]
    if not active_starts:
        return None
    return min(active_starts) - timeframe_to_ms(timeframe) * ZONE_VIEW_MARGIN_BARS


def _direction_text(direction: OrderBlockDirection) -> str:
    return "롱" if direction is OrderBlockDirection.BULLISH else "숏"


def short_symbol(symbol: str) -> str:
    """표 안에서 쓰는 짧은 심볼 — `"BTC/USDT:USDT"` → `"BTC"`(목업 표기)."""
    return symbol.partition("/")[0] or symbol


@dataclass(frozen=True)
class OpenPositionRow:
    """오픈 포지션 한 건의 화면 값(현재가·미실현 손익 포함).

    ⚠️ **러너 상태파일이 아니라 `open_positions` 테이블에서 만든다** — 상태파일 스냅샷
    (`PositionSnapshot`)에는 **수량이 없어** 달러 미실현 손익을 낼 수 없다. 이슈 본문이
    이 표의 소스를 `open_positions` 테이블로 못 박았고, 목업이 `+58.1 (+1.08%)`처럼
    달러와 %를 함께 보여준다.
    """

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    entry_price: float
    stop_price: float | None
    take_profit_price: float | None
    quantity: float
    current_price: float | None
    unrealized_usd: float | None
    unrealized_pct: float | None


def build_open_position_row(position: Position, current_price: float | None) -> OpenPositionRow:
    """열린 포지션 + 최신 종가 → 화면 행. 현재가가 없으면 손익 칸을 비운다."""
    sign = 1.0 if position.direction is OrderBlockDirection.BULLISH else -1.0
    usd: float | None = None
    pct: float | None = None
    if current_price is not None and position.entry_price:
        move = sign * (current_price - position.entry_price)
        usd = move * position.quantity
        pct = move / position.entry_price * 100.0
    return OpenPositionRow(
        symbol=position.symbol,
        timeframe=position.timeframe,
        direction=position.direction,
        entry_price=position.entry_price,
        stop_price=position.stop_price,
        take_profit_price=position.take_profit_price,
        quantity=position.quantity,
        current_price=current_price,
        unrealized_usd=usd,
        unrealized_pct=pct,
    )


def price_text(value: float | None) -> str:
    """가격 표기 — BTC의 `64,690`과 XRP의 `0.6120`을 한 규칙으로 낸다. None이면 `—`."""
    if value is None:
        return "—"
    if abs(value) >= 100:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.2f}"
    return f"{value:,.4f}"


def unrealized_text(row: OpenPositionRow) -> str:
    """`+58.1 (+1.08%)` — 달러와 %를 함께(목업). 현재가가 없으면 `—`."""
    if row.unrealized_pct is None:
        return "—"
    if row.unrealized_usd is None:
        return f"{row.unrealized_pct:+.2f}%"
    return f"{row.unrealized_usd:+,.1f} ({row.unrealized_pct:+.2f}%)"


#: 오픈 포지션 표의 열(목업 확정) — 「손절/익절」이 아니라 **가격**임을 이름이 밝힌다.
OPEN_POSITION_COLUMNS = ["심볼 · TF", "방향", "진입가", "손절가", "익절가", "미실현손익"]


def open_positions_frame(rows: Sequence[OpenPositionRow]) -> pd.DataFrame:
    """차트 아래 「현재 오픈 포지션」 표."""
    if not rows:
        return pd.DataFrame(columns=OPEN_POSITION_COLUMNS)
    return pd.DataFrame(
        {
            "심볼 · TF": f"{short_symbol(r.symbol)} · {r.timeframe}",
            "방향": _direction_text(r.direction),
            "진입가": price_text(r.entry_price),
            "손절가": price_text(r.stop_price),
            "익절가": price_text(r.take_profit_price),
            "미실현손익": unrealized_text(r),
        }
        for r in rows
    )


def total_unrealized_usd(rows: Sequence[OpenPositionRow]) -> float | None:
    """오픈 포지션 미실현 손익의 **달러 합**. 하나도 못 구하면 None.

    %와 달리 달러는 **더해도 뜻이 있다**(칸마다 사이징이 달라도 같은 지갑의 돈이다) —
    잔고 탭의 「미실현손익」 카드가 이 값을 쓴다.
    """
    values = [r.unrealized_usd for r in rows if r.unrealized_usd is not None]
    return sum(values) if values else None


#: 청산 사유 필터의 세 갈래(목업 칩) — 「전체」가 기본이다.
REASON_FILTER_ALL = "전체"
REASON_FILTER_OPTIONS: tuple[str, ...] = (REASON_FILTER_ALL, "익절만", "손절만")


def filter_records_by_choice(
    records: Sequence[PaperTradeRecord], choice: str
) -> list[PaperTradeRecord]:
    """청산 사유 칩(전체/익절만/손절만)으로 거래를 좁힌다.

    라벨은 `paper.report.exit_reason_label`이 만드는 것과 **같은 문자열**을 비교한다 —
    두 벌로 갈라지면 필터가 표에 없는 값을 골라 결과가 늘 비어 보인다.
    모르는 선택은 「전체」로 접는다(빈 화면은 고장으로 읽힌다).
    """
    if choice == "익절만":
        wanted = exit_reason_label(SignalExitReason.TAKE_PROFIT)
    elif choice == "손절만":
        wanted = exit_reason_label(SignalExitReason.STOP_LOSS)
    else:
        return list(records)
    return [r for r in records if exit_reason_label(r.reason) == wanted]


#: 잔고 탭 거래 리스트의 열(목업 확정) — 전체 원장(`records_to_display_frame`, 20열)은
#: 아래 「전체 원장」에 그대로 남고, 여기는 **읽는 표**라 8열로 줄인다.
WALLET_TRADE_COLUMNS = [
    "청산시각(KST)",
    "심볼 · TF",
    "방향",
    "진입가",
    "청산가",
    "사유",
    "손익",
    "수익률%",
]


def wallet_trade_frame(records: Sequence[PaperTradeRecord]) -> pd.DataFrame:
    """잔고 탭의 청산 거래 리스트(최근순).

    시각은 KST 표시(WAN-172) · 손익은 달러(옛 %-only 행은 `—`) · 수익률은 순손익률.
    """
    if not records:
        return pd.DataFrame(columns=WALLET_TRADE_COLUMNS)
    ordered = sorted(records, key=lambda r: r.exit_time, reverse=True)
    return pd.DataFrame(
        {
            "청산시각(KST)": format_kst(r.exit_time),
            "심볼 · TF": f"{short_symbol(r.symbol)} · {r.timeframe}",
            "방향": _direction_text(r.direction),
            "진입가": price_text(r.entry_price),
            "청산가": price_text(r.exit_price),
            "사유": exit_reason_label(r.reason),
            "손익": "—" if r.realized_pnl is None else f"{r.realized_pnl:+,.1f}",
            "수익률%": f"{r.net_pct:+.2f}",
        }
        for r in ordered
    )


@dataclass(frozen=True)
class EquityPoint:
    """지갑 에쿼티 곡선의 한 점(청산 1건 직후)."""

    time_ms: int
    equity: float


@dataclass(frozen=True)
class DrawdownWindow:
    """에쿼티 곡선에서 가장 깊게 깨진 구간(고점 → 저점)."""

    peak_time_ms: int
    peak_equity: float
    trough_time_ms: int
    trough_equity: float
    drawdown_pct: float
    """고점 대비 낙폭(%) — 양수로 보고한다(화면에서 "MDD −N%"로 찍는다)."""


def wallet_equity_points(
    records: Sequence[PaperTradeRecord], *, initial_equity: float | None
) -> list[EquityPoint]:
    """공유 지갑(WAN-213) 에쿼티 곡선 = 초기 자본 + 실현손익 누적.

    `_wallet_balance`(WAN-237)와 **같은 재구성**이다 — 마지막 거래의 `equity_after`
    스냅샷은 여러 칸이 동시에 청산되면 마지막 칸만 반영해 지갑을 합산하지 못한다.
    달러 실현손익이 없는 옛 %-only 행(WAN-207 이전)이 하나라도 섞이면 **빈 곡선**을
    반환한다 — 억지 %-역산으로 실제 잔고와 어긋나는 곡선을 그리지 않는다.
    """
    if not records or initial_equity is None:
        return []
    if any(r.realized_pnl is None for r in records):
        return []
    ordered = sorted(records, key=lambda r: r.exit_time)
    points = [EquityPoint(time_ms=ordered[0].exit_time, equity=initial_equity)]
    equity = initial_equity
    for record in ordered:
        equity += record.realized_pnl or 0.0
        points.append(EquityPoint(time_ms=record.exit_time, equity=equity))
    return points


def max_drawdown_window(points: Sequence[EquityPoint]) -> DrawdownWindow | None:
    """가장 깊은 낙폭 구간(고점→저점). 낙폭이 없으면 None.

    화면이 "어디서 얼마나 깨졌는지"를 빨간 구간으로 보여주려면 크기(%)만으로는
    부족하고 **구간의 양 끝**이 필요하다(사용자 요청 2026-08-11).
    """
    peak_time: int | None = None
    peak_equity = float("-inf")
    best: DrawdownWindow | None = None
    for point in points:
        if point.equity > peak_equity:
            peak_equity = point.equity
            peak_time = point.time_ms
            continue
        if peak_equity <= 0 or peak_time is None:
            continue
        drop = (peak_equity - point.equity) / peak_equity * 100.0
        if drop > 0 and (best is None or drop > best.drawdown_pct):
            best = DrawdownWindow(
                peak_time_ms=peak_time,
                peak_equity=peak_equity,
                trough_time_ms=point.time_ms,
                trough_equity=point.equity,
                drawdown_pct=drop,
            )
    return best
