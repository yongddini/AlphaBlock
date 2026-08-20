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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from backtest.models import PositionSide
from backtest.report import EXIT_REASON_LABELS, format_time_kst
from backtest.substep import ZoneLimitStatus
from common.timefmt import KST, KST_LABEL, format_utc, kst_day_bounds_for_date
from live.limit_orders import LimitOrderStatus
from live.order_journal import (
    ENTRY_STATUS_ENTERED,
    ENTRY_STATUS_REJECTED,
    ORDER_ORIGIN_REENTRY,
    SKIP_REASON_CELL_BUSY,
    SKIP_REASON_RETAP,
    SKIP_REASON_ZONE_WIDTH,
    STATUS_DISCARDED_RESTART,
    STATUS_SKIPPED,
    OrderJournal,
    PlacedOrder,
)
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.models import OrderBlockDirection, OrderBlockResult

if TYPE_CHECKING:
    from backtest.harness import MarketData
    from backtest.models import BacktestConfig
    from backtest.zone_limit_backtest import SetupDiagnostic, _Candidate
    from strategy.models import ConfluenceParams

__all__ = [
    "SOURCE_BACKTEST",
    "SOURCE_LIVE",
    "STATUS_BACKTEST_CLOSED",
    "STATUS_BACKTEST_CONDITION_FAILED",
    "STATUS_BACKTEST_INVALIDATED",
    "STATUS_BACKTEST_SKIP_ZONE_WIDTH",
    "STATUS_BACKTEST_UNFILLED",
    "STATUS_BACKTEST_UNFILLED_EXPIRED",
    "STATUS_BACKTEST_UNPLACED",
    "DayTimeline",
    "TimelineRow",
    "backtest_setup_by_cell",
    "backtest_setup_by_cells",
    "backtest_setup_rows",
    "backtest_timeline_by_cell",
    "backtest_timeline_rows",
    "cell_setup_timeline",
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
    tap_index: int | None = None
    """이 셋업이 존의 몇 번째 탭인지(0-based). `zone_start_time`·`zone_confirmed_time`과 함께
    페이퍼↔백테를 셋업 단위로 1:1 조인하는 키의 마지막 조각이다(WAN-295). 라이브는 주문
    장부(`live_limit_orders.tap_index`), 백테는 셋업 진단(`SetupDiagnostic.tap_index`)에서
    온다 — 둘 다 같은 `entry_candidate_signals`가 매긴 값이라 같은 존의 같은 탭을 가리킨다."""
    trigger_time: int | None = None
    """탭이 발생한 상위TF 봉의 `open_time`(ms). 백테 셋업 행에만 있고(진단 조인용), 라이브
    행은 `None`이다 — 조인은 존 정체성+`tap_index`로 하지 이 값으로 하지 않는다."""
    is_reentry: bool | None = None
    """이 행이 「익절 후 존 내 재진입」인가(WAN-305). 라이브는 장부 `origin` 열에서 오고
    (`None` = 열 도입 전 행 = 판별 불가), 백테는 재진입 후보 여부에서 온다(항상 확정).
    재진입 행은 탭 순번이 안정적이지 않아(라이브 = 재무장 시점 탭 카운트 · 백테 = 0)
    `setup_key` 조인에서 빠지고 존 정체성 구제 조인(`live.setup_compare`)으로 짝지어진다."""

    @property
    def focus_ms(self) -> int | None:
        """차트 점프의 기준 시각 — 체결(있으면) → 예약 → 존 확정 순으로 고른다."""
        return self.fill_ms or self.reserve_ms or self.zone_confirmed_time


#: 라이브 주문 상태 원값(`live_limit_orders.status`) → 한글 라벨(WAN-240). `.get(..., 원본)`
#: 폴백이 영어를 흘리던 자리를 없앤다 — 모든 `LimitOrderStatus` 값 + 장부 원값(`skipped`·
#: `discarded_restart`)을 여기서 덮는다. `cancelled_expired`(미체결/밴드기각)·`filled`(진입/
#: 청산/거부/미기록)·`skipped`(사유 병기)는 하류 정보로 갈리므로 `_live_status`가 따로 처리하고
#: 이 표에 없다. 미매핑이면 `_live_status`가 `?{status}`로 눈에 띄게 찍는다(조용한 누출 금지).
_LIVE_STATUS_LABELS = {
    LimitOrderStatus.CANCELLED_INVALIDATED.value: "무효화",
    LimitOrderStatus.CANCELLED_CONDITION_FAILED.value: "조건취소",
    LimitOrderStatus.PENDING.value: "대기",
    STATUS_DISCARDED_RESTART: "재시작폐기",
}

#: `skipped` 행의 세부 사유(`skip_reason`, WAN-217) → 한글. `fill_report`의 존폭기각/슬롯참/
#: 재탭 어휘와 같은 낱말을 써 두 화면이 갈라지지 않게 한다(완료 기준 3). `거부(사유)`처럼
#: `건너뜀(사유)`로 병기해 "왜 걸렀나"를 한눈에 읽힌다. ⚠️ 문장형 단일 출처는
#: `order_journal.REASON_PHRASES`이고 이건 그 압축 register다 — `슬롯참`은 오픈 포지션·대기
#: 주문 점유를 함께 뜻한다(`cell_busy`, WAN-257).
_SKIP_REASON_LABELS = {
    SKIP_REASON_ZONE_WIDTH: "존폭",
    SKIP_REASON_CELL_BUSY: "슬롯참",
    SKIP_REASON_RETAP: "재탭",
}


def _skip_status(skip_reason: str | None) -> str:
    """`skipped` 주문의 라벨 — 사유를 한글로 병기(미매핑 사유는 `?코드`로 눈에 띄게)."""
    if skip_reason is None:
        return "건너뜀"
    detail = _SKIP_REASON_LABELS.get(skip_reason)
    return f"건너뜀({detail})" if detail is not None else f"건너뜀(?{skip_reason})"


def _live_status(order: PlacedOrder, *, closed: bool) -> str:
    """라이브 주문의 생애 단계를 한 낱말로(WAN-234). 항상 한글 라벨을 낸다(WAN-240)."""
    if order.status == LimitOrderStatus.CANCELLED_EXPIRED.value:
        return "미체결" if order.first_rested_ms is not None else "밴드기각"
    if order.status == LimitOrderStatus.FILLED.value:
        # 체결(FILLED) — 하류 처분으로 가른다.
        if order.entry_status == ENTRY_STATUS_REJECTED:
            return f"거부({order.entry_reject_reason or '사유 미기록'})"
        if order.entry_status != ENTRY_STATUS_ENTERED:
            return "미기록"  # 체결인데 처분이 안 남음(orphan 후보, WAN-194).
        return "청산" if closed else "진입"
    if order.status == STATUS_SKIPPED:
        return _skip_status(order.skip_reason)
    label = _LIVE_STATUS_LABELS.get(order.status)
    # 폴백은 영어를 흘리지 않고 눈에 띄게 찍는다 — 미매핑 상태가 새로 생기면 바로 보인다.
    return label if label is not None else f"?{order.status}"


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
                tap_index=order.tap_index,
                # WAN-305: 장부 origin이 없는 옛 행은 None(판별 불가)으로 남긴다.
                is_reentry=(None if order.origin is None else order.origin == ORDER_ORIGIN_REENTRY),
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


@dataclass(frozen=True)
class _BacktestSymbolTask:
    """한 심볼의 여러 TF를 **한 덩어리로** 돌리는 작업 단위 (WAN-324 §1).

    병렬 단위가 셀에서 심볼로 올라간다 — 그래야 그 심볼의 1분봉을 한 번만 읽어 TF들이
    나눠 쓴다(`harness.load_market_data_by_timeframe`). 셀 단위로 뿌리면 같은 120일치
    1분봉을 TF 수만큼 다시 읽고(48회), 워커마다 자기 사본을 들어 메모리도 중복된다.
    `timeframes`는 **입력 순서를 보존한 중복 없는 튜플**이라 결과를 칸으로 되돌릴 수 있다.
    """

    symbol: str
    timeframes: tuple[str, ...]
    day_start_ms: int
    day_end_ms: int
    warmup_days: int

    def cells(self) -> list[_BacktestCellTask]:
        """이 심볼이 담당하는 셀 작업들 — 평가 함수는 예전과 같은 셀 단위를 본다."""
        return [
            _BacktestCellTask(self.symbol, tf, self.day_start_ms, self.day_end_ms, self.warmup_days)
            for tf in self.timeframes
        ]


def _adopted_day_reentries(
    market: MarketData,
    candidates: Sequence[_Candidate],
    params: ConfluenceParams,
    cfg: BacktestConfig,
    *,
    day_start_ms: int,
) -> list[_Candidate]:
    """워밍업 연속 창의 base 후보에서 채택 재진입(band) 후보를 만들어 그날 것만 남긴다 (WAN-305).

    페이퍼 러너(WAN-274)는 익절 후 같은 존에 band 지정가를 재무장한다 — 대조 백테도 같은
    규칙(WAN-273 채택 = `book_cli.ADOPTED_REENTRY_ENTRY_RULE`)으로 재진입 후보를 만들어야
    페이퍼의 채택 매매가 「판정 갈림」으로 찍히지 않는다(WAN-305 §2). 후보는 북 측정과 **같은
    함수**(`wan169.reentry_candidates_for_window` → WAN-228 `_iter_reentries`)로 만든다(로직
    이중화 금지). 재진입의 `trigger_time`은 재무장 **체결** 시각이다 — 익절이 그날보다
    며칠 전이어도 재무장은 무기한 대기라 그날 체결일 수 있다(2026-08-13 LTC 1h 실사례).
    """
    from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE
    from backtest.wan169_leverage_book import reentry_candidates_for_window

    reentries = reentry_candidates_for_window(
        market,
        candidates,
        params=params,
        cfg=cfg,
        timeframe=market.timeframe,
        entry_rule=ADOPTED_REENTRY_ENTRY_RULE,
    )
    return [c for c in reentries if c.trigger_time >= day_start_ms]


def cell_timeline_trades(
    market: MarketData,
    ob_result: OrderBlockResult,
    *,
    day_start_ms: int,
) -> list[TimelineRow]:
    """미리 로드·탐지된 한 셀에서 그날 백테스트 거래를 타임라인 행으로 만든다.

    `market`은 `[워밍업 시작, 그날 자정]`으로 잘려 있어야 한다(미래 봉 없음). 탭이 그날
    자정 이후인 셋업만 평가하고, 자정에서 끝나는 데이터가 그날 이후를 못 보게 한다
    — 그래서 미리 로드된 입력을 받는다(회귀 테스트가 「미래 봉을 잘라도 비트 동일」을 고정,
    `live.live_vs_backtest.cell_funnel`과 같은 규약). per-cell 단일 포지션 · baseline.

    📌 **채택 재진입(band)을 포함한다(WAN-305)** — base 셋업 탐색·시퀀싱은 `run_once`
    (`build_zone_limit_candidates` + `sequence_with_candidates`)와 같은 코드 경로이고, 그 위에
    페이퍼 러너와 같은 규칙의 재진입 후보(`_adopted_day_reentries`)를 합쳐 한 시퀀서에서
    배치한다. 재진입 행은 `is_reentry=True`로 표기된다.
    """
    from backtest.harness import BASELINE_FILL, build_config, build_params
    from backtest.zone_limit_backtest import (
        build_zone_limit_candidates,
        sequence_with_candidates,
    )

    cfg = build_config(market.timeframe)
    params = build_params(fill=BASELINE_FILL)
    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
    )
    day_candidates = [c for c in candidates if c.trigger_time >= day_start_ms]
    reentries = _adopted_day_reentries(market, candidates, params, cfg, day_start_ms=day_start_ms)
    reentry_ids = {id(c) for c in reentries}
    paired = sequence_with_candidates([*day_candidates, *reentries], cfg, [])
    rows: list[TimelineRow] = []
    for cand, trade in paired:
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
                is_reentry=id(cand) in reentry_ids,
            )
        )
    return rows


# 백테 셋업 행 상태 라벨 (라이브 `_live_status`와 대칭 어휘, WAN-295). 라이브의 「진입/청산」·
# 「거부·사이징0」·「건너뜀·존폭」·「미체결」과 셋업 단위로 짝지을 수 있게 같은 낱말을 쓴다.
STATUS_BACKTEST_CLOSED = "청산"
STATUS_BACKTEST_UNPLACED = "미진입(슬롯·사이징)"
"""지정가는 체결됐으나 동시 1포지션 시퀀서/사이징(qty≤0)에 밀려 **포지션이 안 잡힌** 셋업.
라이브의 「슬롯참」·「거부(사이징0)」에 대응하되, per-cell 단일 baseline 경로는 둘을 코드에서
가르지 못하므로(사이징 qty는 진행 자본에 종속) 한 라벨로 병기한다."""
STATUS_BACKTEST_UNFILLED = "미체결"
"""걸렸으나 지정가에 **닿지 않은** 셋업(`no_touch`). 라이브 「미체결」과 글자까지 같다.

⚠️ WAN-339 이전에는 이 한 낱말이 닿지 않음/만료/무효화/조건취소 **넷을 뭉갰다** — 백테는
`SetupDiagnostic.status`로 사유를 이미 들고 있는데 `filled` 참/거짓만 읽었기 때문이다. 그래서
같은 셋업이 라이브 「무효화」 ↔ 백테 「미체결」로 갈려 보여 「매칭」 집계를 못 믿게 됐다(사용자
관찰 2026-08-19). 지금은 `_backtest_unfilled_status`가 사유별로 가른다."""
STATUS_BACKTEST_UNFILLED_EXPIRED = "미체결(만료)"
"""`limit_valid_bars`(24봉) 경과로 취소된 셋업. **머리 낱말은 라이브와 같고**(`미체결`) 괄호가
백테만 아는 세부를 살린다 — 라이브 `_live_status`는 만료와 「걸렸다 안 닿음」을 둘 다 「미체결」로
뭉개므로(`first_rested_ms` 유무로만 밴드기각을 가른다) 여기서만 갈린다(WAN-339 결정 1)."""
STATUS_BACKTEST_INVALIDATED = "무효화"
"""오더블록 무효화로 취소된 셋업. 라이브 「무효화」와 글자까지 같다."""
STATUS_BACKTEST_CONDITION_FAILED = "조건취소"
"""지정가에 닿았으나 실시간 조건 미충족으로 취소된 셋업. 라이브 「조건취소」와 글자까지 같다."""
STATUS_BACKTEST_SKIP_ZONE_WIDTH = "건너뜀(존폭)"
"""존폭 필터(1.28)로 아예 주문을 걸지 않은 셋업. 라이브 「건너뜀(존폭)」 대칭."""

#: 미체결로 끝난 백테 셋업의 종료 사유(`ZoneLimitStatus`) → 화면 라벨 (WAN-339). **머리 낱말은
#: 언제나 라이브(`_live_status`)와 같고, 백테가 더 아는 것만 괄호로 덧붙인다** — `_skip_status`
#: 의 `건너뜀(사유)`·`거부(사유)`와 같은 저장소 관행이다. 세 코드(`cancelled_*`)는
#: `LimitOrderStatus`와 **문자열까지 같아** 두 시스템이 같은 사건을 같은 낱말로 낸다(회귀
#: 테스트가 그 대칭을 동작으로 건다).
#: ⚠️ `FILLED_*`는 일부러 안 담는다 — 이 표는 `diag.filled`가 거짓인 셋업에만 쓰이고, 그런데도
#: 상태가 `filled_*`이면 체결 탈락 렌즈(`fill_dropout_rate`)가 켜진 것인데 이 화면은
#: `BASELINE_FILL`(탈락률 0) 고정이라 일어날 수 없다. 일어난다면 조용히 흡수하지 말고 `?코드`
#: 로 드러나야 한다.
_BACKTEST_UNFILLED_STATUS_LABELS = {
    ZoneLimitStatus.NO_TOUCH.value: STATUS_BACKTEST_UNFILLED,
    ZoneLimitStatus.CANCELLED_EXPIRED.value: STATUS_BACKTEST_UNFILLED_EXPIRED,
    ZoneLimitStatus.CANCELLED_INVALIDATED.value: STATUS_BACKTEST_INVALIDATED,
    ZoneLimitStatus.CANCELLED_CONDITION_FAILED.value: STATUS_BACKTEST_CONDITION_FAILED,
}


def _backtest_unfilled_status(status: str) -> str:
    """미체결로 끝난 백테 셋업의 라벨 — 종료 사유별로 가른다(미매핑은 `?코드`로 눈에 띄게).

    라이브 `_live_status`의 폴백과 같은 규약이다: 새 상태가 생겼을 때 조용히 「미체결」로
    흡수되면 WAN-339가 고친 그 혼동이 그대로 재발한다.
    """
    label = _BACKTEST_UNFILLED_STATUS_LABELS.get(status)
    return label if label is not None else f"?{status}"


#: 백테 셋업 행 내부 중복 제거 키(존 정체성 + 탭 순번 + 탭 시각). 페이퍼↔백테 **교차** 조인
#: 키는 탭 시각을 뺀 축(`live.setup_compare`)이지만, 한 셀 안에서 청산·미체결 행이 겹치지
#: 않게 셀 내부에서는 탭 시각까지 포함해 유일성을 보장한다.
_BacktestSetupKey = tuple[bool, int | None, int | None, int, int]


def cell_setup_timeline(
    market: MarketData,
    ob_result: OrderBlockResult,
    *,
    day_start_ms: int,
    day_end_ms: int,
) -> list[TimelineRow]:
    """미리 로드·탐지된 한 셀의 그날 백테 **셋업 전부**를 타임라인 행으로 만든다 (WAN-295).

    `cell_timeline_trades`가 실제 청산 거래만 낸다면, 이 함수는 라이브 장부와 **대칭**으로
    그날 탭한 셋업 하나하나를 한 줄로 낸다 — 청산 / 미진입(슬롯·사이징) / 미체결(사유별:
    닿지 않음·만료·무효화·조건취소, WAN-339) / 건너뜀(존폭). 각 행에 존 정체성
    (`zone_start_time`·`zone_confirmed_time`·`tap_index`)과 탭 시각을
    실어 페이퍼↔백테 셋업 단위 1:1 조인(`live.setup_compare`)이 가능하게 한다 — 체결 시각(틱
    vs 1분봉으로 갈린다)이 아니라 존 정체성으로 짝짓는 것이 이 도구의 핵심이다(WAN-234 노트가
    지적한 존 단위 조인 불안정성을 피한다).

    셋업 탐색·체결·시퀀싱은 `run_once`(= `build_zone_limit_candidates` + `sequence_with_candidates`)
    와 **같은 코드 경로**라 「청산」 행은 `cell_timeline_trades`와 비트 단위로 같다(회귀 테스트
    고정). 그날 탭 필터도 `eval_from_ms=day_start_ms`와 같은 하한이다(누수 0).

    📌 **채택 재진입(band)을 포함한다(WAN-305)** — 페이퍼 러너와 같은 규칙의 재무장 후보를
    base와 한 시퀀서에서 배치하고, 그 행은 `is_reentry=True`로 표기된다(존 정체성 구제 조인의
    입력, `live.setup_compare`).
    """
    from backtest.harness import BASELINE_FILL, build_config, build_params
    from backtest.zone_limit_backtest import (
        build_zone_limit_candidates,
        sequence_with_candidates,
    )

    cfg = build_config(market.timeframe)
    params = build_params(fill=BASELINE_FILL)
    sink: list[SetupDiagnostic] = []
    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
        setup_sink=sink,
    )
    # 그날 탭만 — `run_once(eval_from_ms=day_start_ms)`과 같은 하한 필터(누수 0).
    day_candidates = [c for c in candidates if c.trigger_time >= day_start_ms]
    day_sink = [d for d in sink if d.trigger_time >= day_start_ms]
    # 채택 재진입(band, WAN-305) — 페이퍼 러너(WAN-274)와 같은 규칙의 재무장 후보를 base와
    # 한 시퀀서에서 배치한다(재무장 체결이 그날인 것만).
    reentries = _adopted_day_reentries(market, candidates, params, cfg, day_start_ms=day_start_ms)
    reentry_ids = {id(c) for c in reentries}
    # 단일 포지션 시퀀싱. 셀 로더가 펀딩을 안 싣으므로(`need_1m`·`funding=False`) rates는 빈
    # 리스트다 — run_once 단일 경로와 같은 배선이라 거래가 비트 동일하다.
    paired = sequence_with_candidates([*day_candidates, *reentries], cfg, [])

    rows: list[TimelineRow] = []
    traded_keys: set[_BacktestSetupKey] = set()
    for cand, trade in paired:
        ob = cand.order_block
        zs = ob.start_time if ob is not None else None
        zc = ob.confirmed_time if ob is not None else None
        is_long = cand.side is PositionSide.LONG
        is_reentry = id(cand) in reentry_ids
        if not is_reentry:
            traded_keys.add((is_long, zs, zc, cand.tap_index, cand.trigger_time))
        exit_fill = trade.exits[-1]
        rows.append(
            TimelineRow(
                source=SOURCE_BACKTEST,
                symbol=market.symbol,
                timeframe=market.timeframe,
                is_long=is_long,
                status=STATUS_BACKTEST_CLOSED,
                reserve_ms=None,
                limit_price=None,
                fill_ms=trade.entry_time,
                fill_price=trade.entry_price,
                stop_price=cand.stop_price,
                take_profit_price=None,
                exit_ms=trade.exit_time,
                exit_price=exit_fill.price,
                exit_reason=exit_fill.reason.value,
                pnl_pct=trade.return_pct * 100.0,
                pnl_amount=trade.realized_pnl,
                zone_start_time=zs,
                zone_confirmed_time=zc,
                tap_index=cand.tap_index,
                trigger_time=cand.trigger_time,
                is_reentry=is_reentry,
            )
        )
    # 남은 rested 셋업: 체결됐으나 슬롯참/사이징에 밀린 것(미진입) · 지정가 미체결.
    for diag in day_sink:
        is_long = diag.side is PositionSide.LONG
        key: _BacktestSetupKey = (
            is_long,
            diag.zone_start_time,
            diag.zone_confirmed_time,
            diag.tap_index,
            diag.trigger_time,
        )
        if key in traded_keys:
            continue  # 이미 「청산」 행으로 냈다.
        rows.append(_setup_diagnostic_row(diag, symbol=market.symbol, timeframe=market.timeframe))
    # 존폭 필터로 아예 주문을 안 건 셋업(라이브 「건너뜀·존폭」과 대칭).
    rows.extend(
        _zone_width_skipped_rows(
            market,
            ob_result,
            params,
            cfg,
            day_start_ms=day_start_ms,
            day_end_ms=day_end_ms,
        )
    )
    return rows


def _setup_diagnostic_row(diag: SetupDiagnostic, *, symbol: str, timeframe: str) -> TimelineRow:
    """진입까지 못 간 백테 셋업 하나를 라이브 대칭 행으로 (WAN-295 · 라벨은 WAN-339).

    두 갈래다: **체결은 됐는데 포지션이 안 잡힌** 셋업(동시 1포지션 시퀀서·사이징 qty≤0)은
    「미진입(슬롯·사이징)」이고, **지정가가 끝내 체결되지 않은** 셋업은 `diag.status`가 담고
    있는 **종료 사유별로** 갈린다(닿지 않음/만료/무효화/조건취소). 사유를 안 읽고 한 낱말로
    뭉개면 같은 셋업이 라이브 「무효화」 ↔ 백테 「미체결」로 갈려 보여 「매칭」 집계를 못 믿게
    된다(WAN-339가 고친 고장).
    """
    status = STATUS_BACKTEST_UNPLACED if diag.filled else _backtest_unfilled_status(diag.status)
    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=symbol,
        timeframe=timeframe,
        is_long=diag.side is PositionSide.LONG,
        status=status,
        reserve_ms=diag.tap_bar_time,
        limit_price=diag.limit_price,
        fill_ms=None,
        fill_price=None,
        stop_price=diag.stop_price,
        take_profit_price=None,
        exit_ms=None,
        exit_price=None,
        exit_reason=None,
        pnl_pct=None,
        pnl_amount=None,
        zone_start_time=diag.zone_start_time,
        zone_confirmed_time=diag.zone_confirmed_time,
        tap_index=diag.tap_index,
        trigger_time=diag.trigger_time,
        is_reentry=False,
    )


def _zone_width_skipped_rows(
    market: MarketData,
    ob_result: OrderBlockResult,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    *,
    day_start_ms: int,
    day_end_ms: int,
) -> list[TimelineRow]:
    """존폭 필터로 주문을 안 건 그날 셋업들을 「건너뜀(존폭)」 행으로 (WAN-295).

    엔진과 **같은 탭 생성기·방향 게이트·직전봉 ATR·존폭 판정**을 쓴다
    (`live.live_vs_backtest.count_taps_reservations`와 같은 규칙) — 필터가 켜진 채택 기본값
    (1.28)에서 탭했으나 통과하지 못한 셋업만 낸다. 필터가 꺼져 있으면 빈 리스트다.
    """
    from backtest.zone_limit_backtest import _prepare_htf
    from strategy.confluence import entry_candidate_signals
    from strategy.indicators import atr
    from strategy.models import OrderBlockDirection

    if params.max_zone_width_atr is None:
        return []
    frame = _prepare_htf(market.htf_df)
    if len(frame) == 0:
        return []
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    time_to_pos = {t: i for i, t in enumerate(times)}
    atr14 = [float(v) for v in atr(frame, length=params.zone_width_atr_length).tolist()]

    rows: list[TimelineRow] = []
    for signal in entry_candidate_signals(ob_result, params, times, closes, time_to_pos):
        if signal.status != "active":
            continue
        if not (day_start_ms <= signal.trigger_time < day_end_ms):
            continue
        pos = time_to_pos.get(signal.trigger_time)
        if pos is None:
            continue
        ob = signal.order_block
        is_long = ob.direction is OrderBlockDirection.BULLISH
        if (is_long and not cfg.allow_long) or (not is_long and not cfg.allow_short):
            continue
        if not is_long and not params.short_enabled:
            continue
        width_atr = atr14[pos - 1] if pos >= 1 else None
        if params.zone_width_filter_passes(ob, width_atr):
            continue  # 통과한 셋업은 sink/거래 경로가 다룬다(존폭으로 안 걸렀다).
        rows.append(
            TimelineRow(
                source=SOURCE_BACKTEST,
                symbol=market.symbol,
                timeframe=market.timeframe,
                is_long=is_long,
                status=STATUS_BACKTEST_SKIP_ZONE_WIDTH,
                reserve_ms=signal.trigger_time,
                limit_price=None,
                fill_ms=None,
                fill_price=None,
                stop_price=None,
                take_profit_price=None,
                exit_ms=None,
                exit_price=None,
                exit_reason=None,
                pnl_pct=None,
                pnl_amount=None,
                zone_start_time=ob.start_time,
                zone_confirmed_time=ob.confirmed_time,
                tap_index=signal.tap_index,
                trigger_time=signal.trigger_time,
                is_reentry=False,
            )
        )
    return rows


def _cell_trades_from_loaded(
    task: _BacktestCellTask, loaded: tuple[MarketData, OrderBlockResult] | None
) -> list[TimelineRow]:
    """한 셀에서 그날 백테스트 **거래(청산)** 행. 순수 평가는 `cell_timeline_trades`가 한다."""
    if loaded is None:
        return []
    market, ob_result = loaded
    return cell_timeline_trades(market, ob_result, day_start_ms=task.day_start_ms)


def _cell_setups_from_loaded(
    task: _BacktestCellTask, loaded: tuple[MarketData, OrderBlockResult] | None
) -> list[TimelineRow]:
    """한 셀에서 그날 백테스트 **셋업 전부**(청산·미진입·미체결·건너뜀) 행을 낸다 (WAN-295).

    순수 평가는 `cell_setup_timeline`이 한다. 라이브 장부와 셋업 단위로 대칭인 행을 낸다.
    """
    if loaded is None:
        return []
    market, ob_result = loaded
    return cell_setup_timeline(
        market, ob_result, day_start_ms=task.day_start_ms, day_end_ms=task.day_end_ms
    )


def _symbol_rows(
    task: _BacktestSymbolTask,
    evaluate: Callable[
        [_BacktestCellTask, tuple[MarketData, OrderBlockResult] | None], list[TimelineRow]
    ],
) -> list[list[TimelineRow]]:
    """한 심볼의 TF별 행을 낸다 — **1분봉을 한 번만 읽어** TF들이 나눠 쓴다 (WAN-324 §1).

    창은 `[그날 자정 − warmup_days, 그날 자정]`이라 미래 봉을 애초에 로드하지 않는다
    (`live.live_vs_backtest.backtest_cell_funnel`과 같은 로딩). 데이터가 없는 TF는 빈 행이다.
    거래·셋업 두 경로가 이 로더를 공유해 같은 스냅샷·같은 탐지를 본다.

    📌 **TF를 하나씩 소비하고 넘긴다** — `iter_market_data_by_timeframe`이 지연 산출이라
    살아 있는 1분봉 사본은 «공유 원본 + 지금 TF» 둘뿐이다. 넷을 한꺼번에 들면 워커
    메모리가 오히려 늘어 이슈가 기대한 방향(`BrokenProcessPool` 완화)과 반대가 된다.

    🚨 TF마다 `load_market_data`를 부르던 예전과 **비트 단위로 같은 입력**을 만든다 —
    빨라졌는데 숫자가 달라지면 그건 버그다(WAN-324 완료 기준 2·4). 실데이터 회귀 테스트가
    두 로더의 동치를 고정한다.
    """
    from backtest.harness import detect_order_blocks, iter_market_data_by_timeframe
    from strategy.models import OrderBlockParams

    warmup_start_ms = task.day_start_ms - task.warmup_days * _DAY_MS
    cells = {cell.timeframe: cell for cell in task.cells()}
    rows_by_tf: dict[str, list[TimelineRow]] = {}
    for timeframe, market in iter_market_data_by_timeframe(
        task.symbol,
        task.timeframes,
        start_ms=warmup_start_ms,
        end_ms=task.day_end_ms,
        need_1m=True,
        funding=False,
    ):
        if market.htf_df.empty or market.df_1m.empty:
            rows_by_tf[timeframe] = []
            continue
        loaded = (market, detect_order_blocks(market, OrderBlockParams()))
        rows_by_tf[timeframe] = evaluate(cells[timeframe], loaded)
    return [rows_by_tf[tf] for tf in task.timeframes]


def _backtest_symbol_trades(task: _BacktestSymbolTask) -> list[list[TimelineRow]]:
    """한 심볼의 TF별 거래 행 — `task.timeframes` 순서대로."""
    return _symbol_rows(task, _cell_trades_from_loaded)


def _backtest_symbol_setups(task: _BacktestSymbolTask) -> list[list[TimelineRow]]:
    """한 심볼의 TF별 셋업 행 — `task.timeframes` 순서대로."""
    return _symbol_rows(task, _cell_setups_from_loaded)


def _symbol_tasks(
    cells: Sequence[tuple[str, str]],
    *,
    day_start_ms: int,
    day_end_ms: int,
    warmup_days: int,
) -> list[_BacktestSymbolTask]:
    """칸 목록을 **심볼 단위 작업**으로 묶는다 — 심볼·TF 모두 첫 등장 순서를 보존한다.

    순서를 보존해야 예전(칸 단위)과 **같은 칸을 같은 순서로** 계산한다(WAN-335 회귀
    테스트가 그 순서를 고정한다). 같은 칸이 두 번 들어와도 한 번만 굽는다.
    """
    grouped: dict[str, list[str]] = {}
    for symbol, timeframe in cells:
        tfs = grouped.setdefault(symbol, [])
        if timeframe not in tfs:
            tfs.append(timeframe)
    return [
        _BacktestSymbolTask(symbol, tuple(tfs), day_start_ms, day_end_ms, warmup_days)
        for symbol, tfs in grouped.items()
    ]


def _run_symbol_tasks(
    tasks: Sequence[_BacktestSymbolTask],
    worker: Callable[[_BacktestSymbolTask], list[list[TimelineRow]]],
    *,
    jobs: int,
) -> dict[tuple[str, str], list[TimelineRow]]:
    """심볼 작업들을 (선택적으로 병렬로) 돌려 칸 단위 결과로 되돌린다."""
    if not tasks:
        return {}
    if jobs > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=jobs) as pool:
            per_symbol = list(pool.map(worker, tasks))
    else:
        per_symbol = [worker(task) for task in tasks]
    return {
        (task.symbol, timeframe): cell_rows
        for task, rows_by_tf in zip(tasks, per_symbol, strict=True)
        for timeframe, cell_rows in zip(task.timeframes, rows_by_tf, strict=True)
    }


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

    cells = [(symbol, tf) for symbol in syms for tf in tfs]
    tasks = _symbol_tasks(cells, day_start_ms=day_start_ms, day_end_ms=day_end_ms, warmup_days=warm)
    by_cell = _run_symbol_tasks(tasks, _backtest_symbol_trades, jobs=jobs)
    return {cell: by_cell[cell] for cell in cells}


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
    (채택 유니버스 × 작업 TF, WAN-307 이후 12종목 × 4TF)를 돈다 — 무거우니 탐색 중에는
    좁혀 부른다. 셀 경계를 유지한
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


def backtest_setup_by_cell(
    *,
    day_start_ms: int,
    day_end_ms: int,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    jobs: int = 1,
) -> dict[tuple[str, str], list[TimelineRow]]:
    """백테스트 **셋업 전부**(청산·미진입·미체결·건너뜀) 타임라인을 셀 단위로 낸다 (WAN-295).

    `backtest_timeline_by_cell`과 같은 로딩·워밍업·평가 규약이되 청산 거래만이 아니라
    라이브 장부와 대칭인 셋업 행을 낸다(`cell_setup_timeline`). 「청산」 행만 추리면
    `backtest_timeline_by_cell`과 비트 동일하다(회귀 테스트 고정).

    좌표(심볼 × TF)의 **데카르트 곱**을 돌린다 — 임의의 칸 목록만 돌리려면
    `backtest_setup_by_cells`를 직접 쓴다(캐시 미스 칸만 메우는 경로, WAN-335).
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES

    syms = list(symbols) if symbols is not None else list(DEFAULT_SYMBOLS)
    tfs = list(timeframes) if timeframes is not None else list(DEFAULT_TIMEFRAMES)
    cells = [(symbol, tf) for symbol in syms for tf in tfs]
    return backtest_setup_by_cells(
        cells,
        day_start_ms=day_start_ms,
        day_end_ms=day_end_ms,
        warmup_days=warmup_days,
        jobs=jobs,
    )


def backtest_setup_by_cells(
    cells: Sequence[tuple[str, str]],
    *,
    day_start_ms: int,
    day_end_ms: int,
    warmup_days: int | None = None,
    jobs: int = 1,
) -> dict[tuple[str, str], list[TimelineRow]]:
    """`backtest_setup_by_cell`과 **같은 계산**이되 돌릴 (심볼, TF) 칸을 낱개로 받는다 (WAN-335).

    캐시가 이미 가진 칸은 다시 굽지 않고 **미스인 칸만** 메우기 위해 있다 — 미스는 좌표의
    데카르트 곱이 아니라 임의의 부분집합이라(칸마다 지문이 따로다) 심볼·TF 목록으로는
    표현되지 않는다. 같은 칸 목록을 주면 `backtest_setup_by_cell`과 산출물이 같다(회귀
    테스트가 고정) — 캐시를 읽어 빨라지는 것이 **숫자를 바꾸면 버그**다(WAN-335 완료 기준 5).

    칸이 없으면 빈 dict를 낸다(빈 풀을 여는 비용도 치르지 않는다).
    """
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS

    warm = warmup_days if warmup_days is not None else DEFAULT_WARMUP_DAYS
    tasks = _symbol_tasks(cells, day_start_ms=day_start_ms, day_end_ms=day_end_ms, warmup_days=warm)
    by_cell = _run_symbol_tasks(tasks, _backtest_symbol_setups, jobs=jobs)
    return {cell: by_cell[cell] for cell in cells}


def backtest_setup_rows(
    *,
    day_start_ms: int,
    day_end_ms: int,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    jobs: int = 1,
) -> list[TimelineRow]:
    """백테스트 셋업 전부 타임라인(그 KST 하루). `backtest_setup_by_cell`을 평탄화한다 (WAN-295)."""
    by_cell = backtest_setup_by_cell(
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
        """표시 순서: 예약/체결 시각 1차 → 심볼 → TF → 라이브 먼저(주인공), 백테 뒤(대조).

        사용자가 "시간대로 보여줬으면"이라 한 요청대로 **시각을 1차 정렬 키**로 올린다
        (WAN-240 — 옛 WAN-234의 심볼·TF 1차 묶음을 뒤집는다). 「백테만 있는 줄(라이브가 못
        잡은 거래)」 신호는 인접이 아니라 **`출처` 열**(라이브/백테스트)로 읽는다 — 시각순에서
        라이브·백테 짝이 흩어져도 백테 행은 `출처=백테스트`로 그대로 드러난다. 같은 시각의
        타이(같은 셋업의 라이브·백테)는 심볼·TF·출처 순 타이브레이크로 인접을 유지한다.
        """
        merged = list(self.live) + list(self.backtest)
        merged.sort(key=_row_sort_key)
        return tuple(merged)


def _row_sort_key(row: TimelineRow) -> tuple[int, str, str, int]:
    when = row.reserve_ms if row.reserve_ms is not None else (row.fill_ms or 0)
    source_rank = 0 if row.source == SOURCE_LIVE else 1
    return (when, row.symbol, row.timeframe, source_rank)


# -- 공용 표시 계층 (화면·터미널·CSV가 같은 거래를 같은 숫자로 — 완료 기준 3) -----------
#
# 백테스트 거래표(`backtest.report.trades_to_display_frame`, WAN-146/106)와 같은 규칙이다:
# 시각은 KST(표시 전용, 저장·계산은 UTC), 사유는 한글, 열 골격은 거래 0건이어도 같다.
# 한 함수(여기)만 쓰게 해 세 곳(대시보드·`alphablock trades`·CSV)이 갈라지지 않게 한다.

COL_SOURCE = "출처"
COL_SYMBOL = "심볼"
COL_TF = "TF"
COL_SIDE = "방향"
COL_KIND = "구분"
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
        COL_KIND,
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


def _kind_label(is_reentry: bool | None) -> str:
    """구분 열(WAN-305): 재진입 재무장 / 탭 / 판별 불가(옛 장부 행 — origin NULL)."""
    if is_reentry is None:
        return "—"
    return "재진입" if is_reentry else "탭"


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
            COL_KIND: _kind_label(row.is_reentry),
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
