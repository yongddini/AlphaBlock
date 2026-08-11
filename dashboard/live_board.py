"""차트-우선 메인 화면(WAN-245)의 순수 로직 — 좌표·존 선택·표 프레임·지갑 곡선.

`dashboard.app`이 Streamlit 위젯을 그리기 전에 필요한 계산을 전부 여기 모은다. 화면
코드에서 분리해 두면 **테스트가 위젯 없이 동작을 고정**할 수 있다(이 저장소가 반복해
겪은 "라벨은 붙었는데 실제로는 안 도는" 부류의 실패를 막는 장치 — WAN-91/95/112/123).

## 이 모듈이 지키는 두 가지 계약

1. **메인 차트는 최근 봉만 본다.** 6년 전량이 아니라 `CHART_BARS`개(기본 1,200)만
   로드·탐지한다. 분석 탭 cold load ~10초(WAN-202)의 원인이던 "전 구간 재계산 + 통째
   전송"이 이 화면에는 **구조적으로 없다** — 그래서 심볼·TF를 바꿔도 가볍다.
2. **선택지는 채택 좌표에서 온다** — 심볼은 수집 유니버스 9종목, TF는 작업 TF 4개
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

from config.settings import Settings
from dashboard.health_data import OpenPositionView
from data.models import timeframe_to_ms
from paper.report import exit_reason_label
from paper.store import PaperTradeRecord
from strategy.models import OrderBlock, OrderBlockDirection

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
RECENT_ZONE_LIMIT = 4

#: 메인 차트가 로드하는 최근 봉 수. 최근 4개 존을 담을 만큼 넉넉하되 6년 전량과는
#: 자릿수가 다르다(15m 1,200봉 ≈ 12.5일 · 4h 1,200봉 ≈ 200일).
CHART_BARS = 1_200

#: 차트 오른쪽 여백 = **처음 보이는 창의 비율**(TradingView `rightOffset` 감각 — 사용자
#: 요청 2026-08-11). 최신 봉·활성 존·현재가가 가격축에 딱 붙지 않게 띄운다.
#: ⚠️ 봉 수로 주면 안 된다 — 창의 봉 수가 TF마다 10배 넘게 달라(15m 672봉 · 4h 42봉)
#: 고정 봉 수는 한쪽에서 안 보이고 다른 쪽에서 화면의 1/3을 먹는다(실측).
RIGHT_PAD_RATIO = 0.06


def chart_symbols(settings: Settings) -> list[str]:
    """심볼 선택지 = **수집 유니버스**(채택 9종목) + 러너가 감시하는 것 중 빠진 것.

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


def _direction_text(direction: OrderBlockDirection) -> str:
    return "롱" if direction is OrderBlockDirection.BULLISH else "숏"


def open_positions_frame(views: Sequence[OpenPositionView]) -> pd.DataFrame:
    """차트 아래 「현재 오픈 포지션」 표.

    열 구성은 사용자 확정(2026-08-11): 심볼·TF · 방향 · 진입가 · **손절가** · **익절가** ·
    미실현손익. 「손절/익절」이 아니라 「손절가/익절가」인 것은 값이 가격임을 이름이
    밝히기 위해서다.
    """
    if not views:
        return pd.DataFrame(columns=["심볼·TF", "방향", "진입가", "손절가", "익절가", "미실현손익"])
    return pd.DataFrame(
        {
            "심볼·TF": f"{v.snapshot.symbol} · {v.snapshot.timeframe}",
            "방향": _direction_text(v.snapshot.direction),
            "진입가": v.snapshot.entry_price,
            "손절가": "—" if v.snapshot.stop_price is None else v.snapshot.stop_price,
            "익절가": "—" if v.snapshot.take_profit_price is None else v.snapshot.take_profit_price,
            "미실현손익": "—" if v.unrealized_pct is None else f"{v.unrealized_pct:+.2f}%",
        }
        for v in views
    )


def total_unrealized_pct(views: Sequence[OpenPositionView]) -> float | None:
    """오픈 포지션 미실현 손익률의 합(%). 하나도 못 구하면 None.

    ⚠️ 명목 가중이 아니라 **단순 합**이다 — 칸마다 사이징이 달라 이 값이 지갑 대비
    수익률은 아니다. 「지금 열려 있는 자리가 대략 어느 쪽인지」를 보는 눈금이다.
    """
    values = [v.unrealized_pct for v in views if v.unrealized_pct is not None]
    return sum(values) if values else None


def filter_reason_options(records: Sequence[PaperTradeRecord]) -> list[str]:
    """거래 원장에 실제로 있는 청산 사유 라벨(화면 표기 그대로).

    고정 목록이 아니라 **데이터에서** 만든다 — 없는 사유를 고를 수 있게 두면 필터가
    항상 빈 표를 낼 수 있고, 새 사유가 생겼는데 목록에 없어 조용히 숨는 일도 막는다.
    """
    seen = {exit_reason_label(r.reason) for r in records}
    return sorted(seen)


def filter_records_by_reason(
    records: Sequence[PaperTradeRecord], reasons: Sequence[str]
) -> list[PaperTradeRecord]:
    """청산 사유 라벨로 거래를 좁힌다. 빈 선택은 **전부 보여준다**.

    빈 선택을 "아무것도 안 보여줌"으로 두면 사용자가 필터를 다 지웠을 때 화면이 비어
    고장처럼 보인다 — 이 화면에서 필터는 좁히는 도구이지 끄는 스위치가 아니다.
    """
    wanted = set(reasons)
    if not wanted:
        return list(records)
    return [r for r in records if exit_reason_label(r.reason) in wanted]


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
