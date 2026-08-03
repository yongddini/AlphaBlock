"""당일(KST) 거래별 타임라인 — 예약→체결가→청산가→손익 (WAN-234).

WAN-232(`alphablock fills`)가 예약→체결 **여부**를, WAN-233(`alphablock compare`)이 네
단계 **개수**를 봤다면, 이 도구는 그 다음 질문에 답한다: **실제로 들어간 셋업이 언제
예약 → 얼마에 체결 → 어디서 청산 → 손익 얼마**였나. 거래를 한 줄로, 라이브를 주인공으로
두고 백테스트(채택 엔진)를 대조로 병기한다.

## 데이터는 이미 있다 — 흩어져 있을 뿐 (WAN-234는 조인만 한다)

* **예약시각·지정가·체결가·손절/익절 목표·존 시각**: `live_limit_orders`
  (`OrderJournal.orders_placed_between`, WAN-232 당일 코호트 = 예약(`placed_ms`) 창).
* **청산시각·청산가·실현손익**: 페이퍼 라운드트립(`paper_trades`,
  `PaperTradeStore.list_records`). 라이브 주문의 `fill_ms`(체결 시각)와 라운드트립의
  `entry_time`(포지션 오픈 시각)이 같은 사건이라 `(심볼, TF, 방향, fill_ms==entry_time)`로
  조인한다 — 코호트는 **주문 장부(예약 창)** 그대로라 WAN-232/233과 같은 장부·같은 창이다.
* **백테스트 거래별**: 워밍업 연속(그 하루 앞 구간을 태워 존·지표를 데움) + **그날만 평가**
  (WAN-166 `eval_from_ms`) · **per-cell 단일 포지션**(WAN-213 북/사이징 우회) · 미래 봉 없음.
  라이브 셋업과 1:1 규약을 맞춘다(`live.live_vs_backtest`와 같은 배선을 재사용).

## 라벨로 읽는다(버그로 단정 금지 — WAN-233 규약)

라이브 체결가는 실제 틱, 백테스트 체결가는 `baseline`("닿으면 체결", 낙관 상한)이다.
같은 존이라도 값이 갈리는 게 정상이다. **핵심 신호는 「백테만 있는 줄」** — 백테스트는
그 셋업에 진입했는데 라이브 칸이 비었다면, 어느 단계까지 라이브 값이 있다가 끊겼는지가
원인을 가른다(탭 없음/예약 없음/미체결/거부·가드).

## 성격

순수 조회다. 엔진·전략·기본값·토대 불변, `ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from backtest.report import EXIT_REASON_LABELS, format_time_kst
from common.timefmt import KST, KST_LABEL, format_utc, kst_day_bounds_for_date
from live.limit_orders import LimitOrderStatus
from live.order_journal import (
    ENTRY_STATUS_ENTERED,
    ENTRY_STATUS_REJECTED,
    OrderJournal,
    PlacedOrder,
)
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.models import OrderBlockDirection, OrderBlockResult

if TYPE_CHECKING:
    from backtest.harness import MarketData

__all__ = [
    "SOURCE_BACKTEST",
    "SOURCE_LIVE",
    "DayTimeline",
    "TimelineRow",
    "backtest_timeline_by_cell",
    "backtest_timeline_rows",
    "display_columns",
    "live_timeline_rows",
    "render_day_timeline",
    "resolve_day_window",
    "timeline_to_display_frame",
]

#: 행 출처 라벨. 라이브가 주인공, 백테스트는 대조(WAN-234 확정 디자인).
SOURCE_LIVE = "라이브"
SOURCE_BACKTEST = "백테스트"

#: 청산사유 값(`stop_loss`/`take_profit`/…) → 한글. 라이브(`SignalExitReason`)와
#: 백테스트(`ExitReason`)가 같은 문자열 값을 쓰므로 한 매핑으로 둘 다 읽는다 — 화면·
#: 터미널·CSV가 같은 라벨을 보게 하는 공용 규칙(WAN-146/106과 같은 이유).
_EXIT_REASON_LABELS_BY_VALUE = {reason.value: label for reason, label in EXIT_REASON_LABELS.items()}


def _side_label(*, is_long: bool) -> str:
    return "롱" if is_long else "숏"


@dataclass(frozen=True)
class TimelineRow:
    """한 거래의 생애 한 줄 — 예약 → 체결 → (손절/익절 목표) → 청산 → 손익.

    라이브·백테스트 양쪽을 같은 모양으로 담는다(`source`로 구분). 값이 없는 단계는 `None`
    이고 렌더러가 `—`로 읽는다 — 「어느 단계까지 값이 있다가 끊겼는지」가 이 도구의 신호라
    빈 칸을 지어내지 않는다(WAN-95 부류 방지). 시각은 전부 epoch ms(UTC)로 담고 표시
    계층에서만 KST로 옮긴다(WAN-172).
    """

    source: str
    symbol: str
    timeframe: str
    is_long: bool
    status: str
    """상태/사유 라벨(진입·청산·미체결·거부·필터제외 등). 「왜 비었나」를 한 낱말로."""
    reserve_ms: int | None
    limit_price: float | None
    fill_ms: int | None
    fill_price: float | None
    stop_price: float | None
    take_profit_price: float | None
    exit_ms: int | None
    exit_price: float | None
    exit_reason: str | None
    """청산사유 원 코드(`stop_loss`/`take_profit`/…). 렌더러가 한글로 옮긴다."""
    pnl_pct: float | None
    """순손익률(%). 라이브=`net_pct`, 백테스트=`return_pct×100`."""
    pnl_amount: float | None
    """실현 손익(달러). 옛 %-only 라이브 행·미청산은 `None`."""
    zone_start_time: int | None = None
    """존 형성 봉 시각(상위TF `open_time`) — 차트 점프·라이브↔백테 조인 키(WAN-234)."""
    zone_confirmed_time: int | None = None

    @property
    def focus_ms(self) -> int | None:
        """차트 점프의 기준 시각 — 체결(있으면) → 예약 → 존 확정 순으로 고른다."""
        return self.fill_ms or self.reserve_ms or self.zone_confirmed_time


def _live_status(order: PlacedOrder, *, closed: bool) -> str:
    """라이브 주문의 생애 단계를 한 낱말로(WAN-234). WAN-232 라벨 규약을 따른다."""
    if order.status == LimitOrderStatus.CANCELLED_EXPIRED.value:
        return "미체결" if order.first_rested_ms is not None else "밴드기각"
    if order.status != LimitOrderStatus.FILLED.value:
        return {
            LimitOrderStatus.CANCELLED_INVALIDATED.value: "무효화",
            LimitOrderStatus.CANCELLED_CONDITION_FAILED.value: "조건취소",
            LimitOrderStatus.PENDING.value: "대기",
        }.get(order.status, order.status)
    # 여기부터는 체결(FILLED) — 하류 처분으로 가른다.
    if order.entry_status == ENTRY_STATUS_REJECTED:
        return f"거부({order.entry_reject_reason or '사유 미기록'})"
    if order.entry_status != ENTRY_STATUS_ENTERED:
        return "미기록"  # 체결인데 처분이 안 남음(orphan 후보, WAN-194).
    return "청산" if closed else "진입"


def live_timeline_rows(
    journal: OrderJournal,
    store: PaperTradeStore,
    *,
    start_ms: int,
    end_ms: int,
) -> list[TimelineRow]:
    """라이브 거래별 타임라인 — 주문 장부(예약 창) + 페이퍼 라운드트립 조인(WAN-234).

    코호트는 `orders_placed_between`(예약 `placed_ms` 창)이라 WAN-232/233 당일 조회와 **같은
    장부·같은 창**이다(서로 검산 — 완료 기준 5). 체결·진입한 주문은 그 체결 사건
    (`fill_ms`)과 같은 시각에 오픈된 페이퍼 라운드트립(`entry_time`)을 붙여 청산가·손익까지
    잇는다. 라운드트립이 아직 없으면(미청산·재시작 유실) 청산 칸은 비운다.
    """
    orders = journal.orders_placed_between(start_ms=start_ms, end_ms=end_ms)
    # 청산 반쪽 조인용 색인: 이 창에 예약된 심볼·TF의 라운드트립을 (fill_ms 축 =) entry_time으로.
    series = {(o.symbol, o.timeframe) for o in orders}
    roundtrips: dict[tuple[str, str, int], PaperTradeRecord] = {}
    for symbol, timeframe in series:
        for record in store.list_records(symbol, timeframe):
            roundtrips[(symbol, timeframe, record.entry_time)] = record

    rows: list[TimelineRow] = []
    for order in orders:
        is_long = order.direction == OrderBlockDirection.BULLISH.value
        rec: PaperTradeRecord | None = None
        if order.entry_status == ENTRY_STATUS_ENTERED and order.fill_ms is not None:
            rec = roundtrips.get((order.symbol, order.timeframe, order.fill_ms))
        rows.append(
            TimelineRow(
                source=SOURCE_LIVE,
                symbol=order.symbol,
                timeframe=order.timeframe,
                is_long=is_long,
                status=_live_status(order, closed=rec is not None),
                reserve_ms=order.placed_ms,
                limit_price=order.limit_price,
                fill_ms=order.fill_ms,
                fill_price=order.fill_price,
                # 목표가는 주문 장부 값을 우선하고, 없으면 라운드트립(진입 시점 값)으로 보완.
                stop_price=order.stop_price if order.stop_price is not None else _rec_stop(rec),
                take_profit_price=(
                    order.take_profit_price if order.take_profit_price is not None else _rec_tp(rec)
                ),
                exit_ms=None if rec is None else rec.exit_time,
                exit_price=None if rec is None else rec.exit_price,
                exit_reason=None if rec is None else rec.reason.value,
                pnl_pct=None if rec is None else rec.net_pct,
                pnl_amount=None if rec is None else rec.realized_pnl,
                zone_start_time=order.zone_start_time,
                zone_confirmed_time=order.zone_confirmed_time,
            )
        )
    return rows


def _rec_stop(rec: PaperTradeRecord | None) -> float | None:
    return None if rec is None else rec.stop_price


def _rec_tp(rec: PaperTradeRecord | None) -> float | None:
    return None if rec is None else rec.take_profit_price


#: 하루(ms). KST는 서머타임이 없어 하루가 정확히 24h다(`live.live_vs_backtest`와 같은 상수).
_DAY_MS = 86_400_000


@dataclass(frozen=True)
class _BacktestCellTask:
    """한 (심볼, TF) 백테스트 셀의 그날 창 — `ProcessPoolExecutor`로 넘길 수 있게 프로즌."""

    symbol: str
    timeframe: str
    day_start_ms: int
    day_end_ms: int
    warmup_days: int


def cell_timeline_trades(
    market: MarketData,
    ob_result: OrderBlockResult,
    *,
    day_start_ms: int,
) -> list[TimelineRow]:
    """미리 로드·탐지된 한 셀에서 그날 백테스트 거래를 타임라인 행으로 만든다.

    `market`은 `[워밍업 시작, 그날 자정]`으로 잘려 있어야 한다(미래 봉 없음). `eval_from_ms`가
    탭이 그날 자정 이후인 셋업만 평가하고, 자정에서 끝나는 데이터가 그날 이후를 못 보게 한다
    — 그래서 미리 로드된 입력을 받는다(회귀 테스트가 「미래 봉을 잘라도 비트 동일」을 고정,
    `live.live_vs_backtest.cell_funnel`과 같은 규약). per-cell 단일 포지션(`run_once`) · baseline.
    """
    from backtest.harness import BASELINE_FILL, build_config, build_params, run_once
    from backtest.models import PositionSide

    cfg = build_config(market.timeframe)
    params = build_params(fill=BASELINE_FILL)
    out = run_once(
        market,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
        eval_from_ms=day_start_ms,
    )
    rows: list[TimelineRow] = []
    for trade in out.result.trades:
        exit_fill = trade.exits[-1]
        rows.append(
            TimelineRow(
                source=SOURCE_BACKTEST,
                symbol=market.symbol,
                timeframe=market.timeframe,
                is_long=trade.side is PositionSide.LONG,
                status="청산",
                reserve_ms=None,
                limit_price=None,
                fill_ms=trade.entry_time,
                fill_price=trade.entry_price,
                stop_price=None,
                take_profit_price=None,
                exit_ms=trade.exit_time,
                exit_price=exit_fill.price,
                exit_reason=exit_fill.reason.value,
                pnl_pct=trade.return_pct * 100.0,
                pnl_amount=trade.realized_pnl,
            )
        )
    return rows


def _backtest_cell_trades(task: _BacktestCellTask) -> list[TimelineRow]:
    """한 셀의 데이터를 로드·탐지해 그날 백테스트 거래 행을 낸다(워밍업 연속·미래 봉 미로드).

    창은 `[그날 자정 − warmup_days, 그날 자정]`이라 미래 봉을 애초에 로드하지 않는다
    (`live.live_vs_backtest.backtest_cell_funnel`과 같은 로딩). 데이터가 없으면 빈 리스트다
    (대조에서 자연히 빠진다). 순수 평가는 `cell_timeline_trades`가 한다.
    """
    from backtest.harness import detect_order_blocks, load_market_data
    from strategy.models import OrderBlockParams

    warmup_start_ms = task.day_start_ms - task.warmup_days * _DAY_MS
    market = load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=warmup_start_ms,
        end_ms=task.day_end_ms,
        need_1m=True,
        funding=False,
    )
    if market.htf_df.empty or market.df_1m.empty:
        return []
    ob_result = detect_order_blocks(market, OrderBlockParams())
    return cell_timeline_trades(market, ob_result, day_start_ms=task.day_start_ms)


def backtest_timeline_by_cell(
    *,
    day_start_ms: int,
    day_end_ms: int,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    jobs: int = 1,
) -> dict[tuple[str, str], list[TimelineRow]]:
    """백테스트 거래별 타임라인을 **(심볼, TF) 셀 단위로** 낸다(하루 캐시 적재용, WAN-239).

    `backtest_timeline_rows`와 **같은 계산**이되 셀 경계를 유지한다 — 야간 크론이 셀마다
    지문을 붙여 캐시에 담으려면 어느 행이 어느 셀에서 나왔는지 알아야 한다(WAN-239 §1).
    거래가 0건인 셀도 **빈 리스트로 키가 남는다**(= "그 셀은 계산했고 거래가 없었다" — 캐시
    미스와 구분하는 신호). 워밍업·평가·per-cell 단일 규약은 `backtest_timeline_rows`와
    비트 동일하다(회귀 테스트가 고정).
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS

    syms = list(symbols) if symbols is not None else list(DEFAULT_SYMBOLS)
    tfs = list(timeframes) if timeframes is not None else list(DEFAULT_TIMEFRAMES)
    warm = warmup_days if warmup_days is not None else DEFAULT_WARMUP_DAYS

    tasks = [
        _BacktestCellTask(symbol, tf, day_start_ms, day_end_ms, warm)
        for symbol in syms
        for tf in tfs
    ]
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=jobs) as pool:
            per_cell = list(pool.map(_backtest_cell_trades, tasks))
    else:
        per_cell = [_backtest_cell_trades(task) for task in tasks]
    return {
        (task.symbol, task.timeframe): cell_rows
        for task, cell_rows in zip(tasks, per_cell, strict=True)
    }


def backtest_timeline_rows(
    *,
    day_start_ms: int,
    day_end_ms: int,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    jobs: int = 1,
) -> list[TimelineRow]:
    """백테스트 거래별 타임라인(그 KST 하루, 워밍업 연속·per-cell 단일·미래 봉 없음).

    `live.live_vs_backtest`와 같은 워밍업·평가 규약을 유지해 라이브 셋업과 1:1로 맞춘다
    (완료 기준 4). `jobs>1`이면 (심볼, TF) 단위로 병렬. 심볼·TF를 안 주면 채택 좌표
    (9종목 × 15m·1h·4h)를 돈다 — 무거우니 탐색 중에는 좁혀 부른다. 셀 경계를 유지한
    `backtest_timeline_by_cell`을 평탄화한 것이라 두 함수가 같은 계산을 공유한다.
    """
    by_cell = backtest_timeline_by_cell(
        day_start_ms=day_start_ms,
        day_end_ms=day_end_ms,
        symbols=symbols,
        timeframes=timeframes,
        warmup_days=warmup_days,
        jobs=jobs,
    )
    rows: list[TimelineRow] = []
    for cell_rows in by_cell.values():
        rows.extend(cell_rows)
    return rows


@dataclass(frozen=True)
class DayTimeline:
    """하루(KST) 거래별 타임라인 — 라이브(주인공)와 백테스트(대조)를 한 자루에."""

    day_key: str
    live: tuple[TimelineRow, ...]
    backtest: tuple[TimelineRow, ...]

    @property
    def rows(self) -> tuple[TimelineRow, ...]:
        """표시 순서: 심볼 → TF → 예약/체결 시각 → 라이브 먼저(주인공), 백테 뒤(대조).

        같은 (심볼, TF)에서 라이브 행 바로 아래 백테 행이 오도록 정렬해, 라이브 칸이 빈
        「백테만 있는 줄」이 눈에 띄게 한다(WAN-234 핵심 신호).
        """
        merged = list(self.live) + list(self.backtest)
        merged.sort(key=_row_sort_key)
        return tuple(merged)


def _row_sort_key(row: TimelineRow) -> tuple[str, str, int, int]:
    when = row.reserve_ms if row.reserve_ms is not None else (row.fill_ms or 0)
    source_rank = 0 if row.source == SOURCE_LIVE else 1
    return (row.symbol, row.timeframe, when, source_rank)


# -- 공용 표시 계층 (화면·터미널·CSV가 같은 거래를 같은 숫자로 — 완료 기준 3) -----------
#
# 백테스트 거래표(`backtest.report.trades_to_display_frame`, WAN-146/106)와 같은 규칙이다:
# 시각은 KST(표시 전용, 저장·계산은 UTC), 사유는 한글, 열 골격은 거래 0건이어도 같다.
# 한 함수(여기)만 쓰게 해 세 곳(대시보드·`alphablock trades`·CSV)이 갈라지지 않게 한다.

COL_SOURCE = "출처"
COL_SYMBOL = "심볼"
COL_TF = "TF"
COL_SIDE = "방향"
COL_STATUS = "상태"
COL_RESERVE_KST = "예약(KST)"
COL_LIMIT = "지정가"
COL_FILL_KST = "체결(KST)"
COL_FILL_PRICE = "체결가"
COL_STOP = "손절목표"
COL_TP = "익절목표"
COL_EXIT_KST = "청산(KST)"
COL_EXIT_PRICE = "청산가"
COL_EXIT_REASON = "청산사유"
COL_PNL_PCT = "손익률%"
COL_PNL = "손익"
COL_RESERVE_UTC = "예약(UTC)"
COL_FILL_UTC = "체결(UTC)"
COL_EXIT_UTC = "청산(UTC)"


def display_columns(*, include_utc: bool = False) -> tuple[str, ...]:
    """타임라인 표의 열 순서. 거래가 0건이어도 표 골격이 같아야 한다(빈 화면 방지)."""
    columns = [
        COL_SOURCE,
        COL_SYMBOL,
        COL_TF,
        COL_SIDE,
        COL_STATUS,
        COL_RESERVE_KST,
        COL_LIMIT,
        COL_FILL_KST,
        COL_FILL_PRICE,
        COL_STOP,
        COL_TP,
        COL_EXIT_KST,
        COL_EXIT_PRICE,
        COL_EXIT_REASON,
        COL_PNL_PCT,
        COL_PNL,
    ]
    if include_utc:
        columns.insert(columns.index(COL_RESERVE_KST) + 1, COL_RESERVE_UTC)
        columns.insert(columns.index(COL_FILL_KST) + 1, COL_FILL_UTC)
        columns.insert(columns.index(COL_EXIT_KST) + 1, COL_EXIT_UTC)
    return tuple(columns)


def _time_kst(ms: int | None) -> str:
    return "—" if ms is None else format_time_kst(ms)


def _time_utc(ms: int | None) -> str:
    return "—" if ms is None else format_utc(ms)


def _price(value: float | None) -> str:
    return "—" if value is None else f"{value:.8g}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _money(value: float | None) -> str:
    return "—" if value is None else f"{value:+,.2f}"


def _exit_reason_label(value: str | None) -> str:
    return "—" if value is None else _EXIT_REASON_LABELS_BY_VALUE.get(value, value)


def timeline_to_display_frame(
    rows: Sequence[TimelineRow], *, include_utc: bool = False
) -> pd.DataFrame:
    """타임라인 행들을 **사람이 읽는** 표(pandas)로 (WAN-234 공용 표시 계층).

    화면(대시보드)·터미널(`alphablock trades`)·CSV가 **이 함수 하나**를 써 같은 거래를 같은
    숫자로 본다. 시각은 KST(`include_utc=True`면 UTC 병기 — 파일 내보내기), 청산사유는 한글,
    가격은 유효숫자 8, 손익률·손익은 부호 포함이다. 값이 없는 단계는 `—`다.
    """
    records: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {
            COL_SOURCE: row.source,
            COL_SYMBOL: row.symbol,
            COL_TF: row.timeframe,
            COL_SIDE: _side_label(is_long=row.is_long),
            COL_STATUS: row.status,
            COL_RESERVE_KST: _time_kst(row.reserve_ms),
            COL_LIMIT: _price(row.limit_price),
            COL_FILL_KST: _time_kst(row.fill_ms),
            COL_FILL_PRICE: _price(row.fill_price),
            COL_STOP: _price(row.stop_price),
            COL_TP: _price(row.take_profit_price),
            COL_EXIT_KST: _time_kst(row.exit_ms),
            COL_EXIT_PRICE: _price(row.exit_price),
            COL_EXIT_REASON: _exit_reason_label(row.exit_reason),
            COL_PNL_PCT: _pct(row.pnl_pct),
            COL_PNL: _money(row.pnl_amount),
        }
        if include_utc:
            record[COL_RESERVE_UTC] = _time_utc(row.reserve_ms)
            record[COL_FILL_UTC] = _time_utc(row.fill_ms)
            record[COL_EXIT_UTC] = _time_utc(row.exit_ms)
        records.append(record)
    return pd.DataFrame(records, columns=list(display_columns(include_utc=include_utc)))


def resolve_day_window(day_arg: str) -> tuple[int, int, str]:
    """`--day` 인자를 `[start_ms, end_ms)` KST 하루 창 + 날짜 키로 푼다(WAN-232 규약).

    `"today"`(인자 없이)는 지금(KST)이 속한 날, 그 밖은 `YYYY-MM-DD`. 창 경계는
    `common.timefmt.kst_day_bounds_for_date`가 잡아 `alphablock fills`/`compare` 당일 조회와
    같은 자정 경계를 공유한다(서로 검산).
    """
    if day_arg == "today":
        day = datetime.now(tz=KST).date()
    else:
        day = datetime.strptime(day_arg, "%Y-%m-%d").date()
    start_ms, end_ms = kst_day_bounds_for_date(day)
    return start_ms, end_ms, day.isoformat()


def _fmt_cell(value: object) -> str:
    return "" if value is None else str(value)


def render_day_timeline(
    timeline: DayTimeline,
    *,
    engine_label: str | None = None,
    status_note: str | None = None,
) -> str:
    """당일 거래별 타임라인을 마크다운 표로 렌더한다(터미널 `alphablock trades`).

    `timeline_to_display_frame`이 낸 **같은 표**를 마크다운으로 옮긴다 — 화면·CSV와 숫자가
    갈라지지 않는다(완료 기준 3). 라이브가 주인공, 백테스트가 대조이고, 라이브 칸이 빈
    「백테만 있는 줄」이 이 도구의 핵심 신호다.

    `engine_label`은 백테 대조가 **어느 엔진으로 계산됐는지**를 밝히는 배지다(WAN-239 완료
    기준 4-c — (Ⅰ) 설명형 이름 + (Ⅱ) git 해시). `status_note`는 캐시 상태(미스 안내 등)를
    한 줄로 얹는다.
    """
    rows = timeline.rows
    lines: list[str] = [f"# 당일 거래별 타임라인 · {timeline.day_key} ({KST_LABEL}) — WAN-234", ""]
    if engine_label is not None:
        lines.append(f"백테 대조 엔진: **{engine_label}**")
    if status_note is not None:
        lines.append(status_note)
    if engine_label is not None or status_note is not None:
        lines.append("")
    live_closed = sum(1 for r in timeline.live if r.exit_ms is not None)
    live_entered = sum(1 for r in timeline.live if r.status in ("진입", "청산"))
    lines.append(
        f"**라이브 {len(timeline.live)}건**(진입 {live_entered} · 청산 {live_closed}) ·"
        f" **백테스트 {len(timeline.backtest)}건**(진입·청산)"
    )
    lines.append(
        "라이브가 주인공, 백테스트는 대조다. 🚨 **라이브 칸이 없고 백테 값만 있는 줄**이"
        " 핵심 신호 — 백테는 그 셋업에 진입했는데 라이브는 어느 단계에서 끊겼나(상태 열)."
        " 체결가·청산가가 갈리는 건 정상이다(라이브=실제 틱 · 백테=`baseline` 낙관 상한)."
    )
    lines.append("")

    if not rows:
        lines.append("이 날 라이브 예약·백테스트 진입이 모두 없습니다.")
        return "\n".join(lines)

    frame = timeline_to_display_frame(rows)
    columns = list(frame.columns)
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join("--" for _ in columns) + " |")
    for record in frame.to_dict("records"):
        lines.append("| " + " | ".join(_fmt_cell(record[c]) for c in columns) + " |")
    return "\n".join(lines)


def build_day_timeline(
    journal: OrderJournal,
    store: PaperTradeStore,
    *,
    day_start_ms: int,
    day_end_ms: int,
    day_key: str,
    include_backtest: bool = True,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    jobs: int = 1,
) -> DayTimeline:
    """하루 거래별 타임라인을 계산한다 — 라이브는 조회, 백테스트는 채택 엔진으로 재산출.

    `include_backtest=False`면 백테스트를 돌리지 않는다(무겁다 — 27셀 × 워밍업). 라이브만
    빠르게 보고 싶을 때 쓴다.
    """
    live = live_timeline_rows(journal, store, start_ms=day_start_ms, end_ms=day_end_ms)
    backtest: list[TimelineRow] = []
    if include_backtest:
        backtest = backtest_timeline_rows(
            day_start_ms=day_start_ms,
            day_end_ms=day_end_ms,
            symbols=symbols,
            timeframes=timeframes,
            warmup_days=warmup_days,
            jobs=jobs,
        )
    return DayTimeline(day_key=day_key, live=tuple(live), backtest=tuple(backtest))
