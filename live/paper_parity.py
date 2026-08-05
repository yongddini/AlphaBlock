"""페이퍼 러너 ↔ 백테스트 파리티 리포트 (WAN-247).

`alphablock compare`(WAN-233)가 **하루**의 진입 깔때기 **개수**(탭→예약→체결→진입)를
라이브·백테스트로 나란히 놓는다면, 이 도구는 **여러 날 창**을 묶어 (심볼·TF)별로
**체결률**과 **실현 R**을 대조한다 — WAN-247의 완료 기준(체결률·실현 R 대조표 + 판정)이다.

## ⚠️ 이 도구가 재는 것 — 「낙관」이 아니라 「파리티」다 (이슈 헤드라인 정정)

WAN-247의 원 제목은 "baseline이 얼마나 부풀려졌나"를 재는 것이었으나, 사용자·PM 정정
(2026-08-05)대로 **그 질문은 이 방법으로 답이 안 나온다**: 페이퍼 러너도 백테스트도 **둘 다
`baseline`("닿으면 체결") · 1분봉 해상도**라(페이퍼는 의도적으로 백테스트와 같은 자 —
WAN-45/246) 어느 쪽도 큐 우선순위를 모델링하지 않는다. **진짜 낙관 측정**은 실주문·틱·호가
(WAN-98 Canceled · WAN-246)에서만 나온다.

그래서 이 리포트가 실제로 재는 것은 **파리티(배선 검증)**다:

1. **탭·예약 단계는 같아야 한다** — 같은 캔들·같은 탐지 파라미터·같은 존폭 필터(1.28)면
   개수가 붙어야 한다. 갈리면 데이터/탐지/필터 **배선 버그**다(가장 깨끗한 파리티 체크).
2. **체결·실현 R은 콜드스타트·러너 다운타임·지연·집행 거부만큼만 갈린다** — 라이브 러너는
   재시작하면 대기 주문을 버리고(WAN-45 재시작 정책), 노트북이 닫히면 그 구간 셋업을 아예
   못 본다. 백테스트는 창을 연속으로 태우므로(WAN-166 워밍업) 그 구멍이 없다. 이 차이는
   **버그가 아니라 운영 현실**이고, 표에 `uptime`으로 명시한다.

즉 이 표의 쓰임새는 "엣지 판정"이 아니라 **"라이브가 백테스트대로 도는가"의 감사**다.

## 백테스트 쪽 규약 (WAN-233 재사용)

* **워밍업 연속 + 창만 평가**(WAN-166 `eval_from_ms`) — 창 앞 `--warmup-days`를 연속으로
  태워 존·지표를 데운 뒤 **탭이 창 안인 셋업만** 평가한다(라이브의 전-이력 존 재고 근사).
* **미래 봉 없음** — 데이터를 창 끝(다음 자정)에서 자른다(누수 0).
* **per-cell 단일 포지션** — 채택 북(WAN-213)이 아니라 단일 경로로 「어느 셋업이 진입했나」만
  본다(북/사이징 차이가 개수·R 대조를 오염시키지 않도록). 라이브 페이퍼도 (종목,TF)당
  1포지션이라 자가 맞는다.
* **실현 R = 청산 사유 기준**(손절 −1.0R · 익절 +`take_profit_r`) — 양쪽 엔진 모두 고정
  1.5R/−1R 청산이라 이 정의가 **비용과 무관하게 정확히 같은 자**다(`harness.mean_r`와 동일).
  페이퍼의 비용-차감 `r_multiple`(net)은 참고 열로 병기한다.

## 성격

순수 대조 조회다 — 엔진·전략·기본값·토대 불변, `ALPHABLOCK_LIVE_TRADING=false` 유지.
라이브 숫자는 `OrderJournal`(WAN-45/194/217)·`PaperTradeStore`(WAN-33) 장부를 **그대로 조회**
하므로 `alphablock fills`/`compare`와 같은 장부·같은 창을 본다.

⚠️ **서버 DB 기준** — 로컬 `ohlcv.db`는 07-22에서 얼어붙은 스냅샷일 수 있다(WAN-195). 러너가
쓰는 **서버 장부**(`--db`)를 읽어야 실데이터다. 장부가 비어 있으면(러너 미가동) 리포트는
그 사실을 판정으로 낸다 — 없는 표본을 지어내지 않는다.

## 재현

```
alphablock parity                                  # 전 장부 창(러너 가동 구간)
alphablock parity --start 2026-08-01 --end 2026-08-04
python -m live.paper_parity --db data/ohlcv.db --by-cell --jobs 6
```
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from backtest.harness import (
    BASELINE_FILL,
    MarketData,
    build_config,
    build_params,
    detect_order_blocks,
    fill_preset,
    load_market_data,
    run_once,
)
from backtest.models import BacktestResult, ExitReason
from common.timefmt import KST, kst_day_bounds_for_date
from config.settings import get_settings
from live.live_vs_backtest import DEFAULT_WARMUP_DAYS, count_taps_reservations
from live.order_journal import (
    LEDGER_REASON_ENTERED,
    LEDGER_REASON_NO_FILL,
    LEDGER_REASON_UNRECORDED,
    MARGINAL_FILL_BPS,
    LedgerEntry,
    OrderJournal,
)
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.models import (
    ConfluenceParams,
    OrderBlockParams,
    OrderBlockResult,
    SignalExitReason,
)

__all__ = [
    "BacktestParityCell",
    "PaperParityCell",
    "ParityReport",
    "RStats",
    "backtest_parity_cell",
    "build_parity_report",
    "paper_cells",
    "r_stats_from_reasons",
    "render_parity",
    "resolve_cells",
    "resolve_window",
]

#: 하루(ms). KST는 서머타임이 없어 하루가 정확히 24h다.
_DAY_MS = 86_400_000


@dataclass(frozen=True)
class RStats:
    """청산 사유로 매긴 실현 R 요약(손절 −1.0R · 익절 +`take_profit_r`).

    양쪽 엔진 모두 고정 1.5R/−1R 청산이라 이 정의가 비용과 무관하게 정확히 같은 자다
    (`harness.mean_r`와 동일). 데이터 종료 미청산(`END_OF_DATA`)은 R이 확정되지 않아 뺀다.
    """

    n: int
    """R이 확정된 거래 수(익절 + 손절)."""
    wins: int
    """익절(TP) 청산 수."""
    losses: int
    """손절(SL) 청산 수."""
    mean_r: float | None
    """거래당 평균 R = (wins × tp_r − losses) / n. 표본이 없으면 None."""
    net_mean_r: float | None = None
    """비용-차감 실현 R 평균(페이퍼 `r_multiple`) — 참고용. 백테스트 쪽은 사유 기준이라 None."""

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.n if self.n else None


def r_stats_from_reasons(
    reasons: Iterable[bool | None],
    *,
    take_profit_r: float,
    net_r_values: Sequence[float] | None = None,
) -> RStats:
    """청산 사유 목록(True=익절 · False=손절 · None=그 밖)에서 `RStats`를 만든다.

    `net_r_values`(페이퍼의 비용-차감 `r_multiple`)를 주면 그 평균을 참고 열로 함께 낸다.
    """
    wins = losses = 0
    for is_tp in reasons:
        if is_tp is True:
            wins += 1
        elif is_tp is False:
            losses += 1
    n = wins + losses
    mean_r = (wins * take_profit_r - losses) / n if n else None
    net_mean_r: float | None = None
    if net_r_values:
        net_mean_r = sum(net_r_values) / len(net_r_values)
    return RStats(n=n, wins=wins, losses=losses, mean_r=mean_r, net_mean_r=net_mean_r)


@dataclass(frozen=True)
class PaperParityCell:
    """한 (심볼, TF)의 라이브 페이퍼 실측(창 안) — 체결률·진입률·실현 R.

    체결·미체결·진입/거부는 `OrderJournal.ledger_entries`(사건 시각 귀속)에서, 실현 R은
    `PaperTradeStore`(청산 시각 귀속)에서 온다 — 두 장부의 창 귀속이 달라(체결 vs 청산)
    개수가 1:1로 맞지는 않는다(보유 구간이 창 경계를 걸칠 수 있다). 파리티 감사에는 충분하다.
    """

    symbol: str
    timeframe: str
    filled: int
    no_fill: int
    entered: int
    entry_rejected: int
    marginal_fills: int
    """체결 중 관통 < 5bp("스치듯 닿음") 수 — `pen_5bp` 렌즈가 부정할 체결(WAN-96)."""
    r: RStats

    @property
    def fill_rate(self) -> float | None:
        """체결률 = 체결 / (체결 + 미체결). `OrderJournal`/대시보드와 같은 정의(WAN-221)."""
        denom = self.filled + self.no_fill
        return self.filled / denom if denom else None

    @property
    def entry_rate(self) -> float | None:
        """진입률 = 진입 / (진입 + 거부). 처분 미기록은 분모 밖(WAN-194)."""
        denom = self.entered + self.entry_rejected
        return self.entered / denom if denom else None

    @property
    def marginal_fill_share(self) -> float | None:
        return self.marginal_fills / self.filled if self.filled else None


@dataclass(frozen=True)
class BacktestParityCell:
    """한 (심볼, TF)의 백테스트 채택 엔진 예측(창 안, 워밍업 연속·단일 포지션).

    `has_data=False`면 그 셀 데이터가 없어(수집 안 됨/1분봉 공백) 대조에서 뺀다 — 0으로 세면
    "탭이 없었다"와 "데이터가 없었다"가 표에서 같아 보인다(WAN-95 부류).
    """

    symbol: str
    timeframe: str
    taps: int
    reservations: int
    eligible: int
    fills_baseline: int
    fills_pen5: int
    entries: int
    r: RStats
    has_data: bool = True

    @property
    def fill_rate(self) -> float | None:
        """지정가 체결률 = filled / eligible(`ZoneLimitStats.fill_rate`, `baseline`)."""
        return self.fills_baseline / self.eligible if self.eligible else None

    @property
    def fill_rate_pen5(self) -> float | None:
        """보수적 체결(`pen_5bp`) 체결률 — 라이브에 더 가까운지 읽는 참고 렌즈."""
        return self.fills_pen5 / self.eligible if self.eligible else None


@dataclass(frozen=True)
class ParityReport:
    """창 하나의 파리티 대조 — 라이브(장부)와 백테스트(채택 엔진)를 셀별로."""

    start_ms: int
    end_ms: int
    start_key: str
    end_key: str
    take_profit_r: float
    uptime_ms: int
    """러너가 실제로 살아 있던 시간(세션 구간 합) — 체결률 분모의 실질 근거."""
    window_ms: int
    paper: tuple[PaperParityCell, ...]
    backtest: tuple[BacktestParityCell, ...]

    @property
    def has_paper(self) -> bool:
        return any(c.filled or c.no_fill or c.r.n for c in self.paper)

    @property
    def uptime_frac(self) -> float | None:
        return self.uptime_ms / self.window_ms if self.window_ms else None


# ---------------------------------------------------------------------------
# 라이브(페이퍼) 쪽 집계 — 장부 조회
# ---------------------------------------------------------------------------


def _paper_r_by_cell(
    records: Sequence[PaperTradeRecord],
    *,
    start_ms: int,
    end_ms: int,
    take_profit_r: float,
) -> dict[tuple[str, str], RStats]:
    """청산이 `[start_ms, end_ms)`에 난 페이퍼 거래의 셀별 실현 R(사유 기준 + net 참고)."""
    reasons: dict[tuple[str, str], list[bool | None]] = {}
    nets: dict[tuple[str, str], list[float]] = {}
    for rec in records:
        if not (start_ms <= rec.exit_time < end_ms):
            continue
        key = (rec.symbol, rec.timeframe)
        if rec.reason is SignalExitReason.TAKE_PROFIT:
            reasons.setdefault(key, []).append(True)
        elif rec.reason is SignalExitReason.STOP_LOSS:
            reasons.setdefault(key, []).append(False)
        else:
            reasons.setdefault(key, []).append(None)
        if rec.r_multiple is not None:
            nets.setdefault(key, []).append(rec.r_multiple)
    return {
        key: r_stats_from_reasons(vals, take_profit_r=take_profit_r, net_r_values=nets.get(key))
        for key, vals in reasons.items()
    }


def paper_cells(
    entries: Sequence[LedgerEntry],
    records: Sequence[PaperTradeRecord],
    *,
    start_ms: int,
    end_ms: int,
    take_profit_r: float,
) -> list[PaperParityCell]:
    """장부 행(체결 깔때기)과 청산 거래(실현 R)를 셀별 `PaperParityCell`로 접는다.

    `entries`는 `OrderJournal.ledger_entries(start,end)`가 이미 창으로 자른 목록이다 —
    체결·미체결·진입/거부 분류는 `dashboard.funnel_ledger`/`DaySummary`와 같은 어휘를 쓴다.
    """
    fill: dict[tuple[str, str], list[int]] = {}
    for e in entries:
        key = (e.symbol, e.timeframe)
        # [filled, no_fill, entered, entry_rejected, marginal]
        slot = fill.setdefault(key, [0, 0, 0, 0, 0])
        if e.filled:
            slot[0] += 1
            if e.reason == LEDGER_REASON_ENTERED:
                slot[2] += 1
            elif e.reason != LEDGER_REASON_UNRECORDED:
                # 체결됐으나 진입도 미기록도 아님 = 집행 거부(cell_busy/notional/sizing/other).
                slot[3] += 1
            if e.penetration_bps is not None and e.penetration_bps < MARGINAL_FILL_BPS:
                slot[4] += 1
        elif e.reason == LEDGER_REASON_NO_FILL:
            slot[1] += 1

    r_by_cell = _paper_r_by_cell(
        records, start_ms=start_ms, end_ms=end_ms, take_profit_r=take_profit_r
    )
    empty_r = RStats(n=0, wins=0, losses=0, mean_r=None)
    keys = sorted(set(fill) | set(r_by_cell))
    cells: list[PaperParityCell] = []
    for symbol, timeframe in keys:
        f = fill.get((symbol, timeframe), [0, 0, 0, 0, 0])
        cells.append(
            PaperParityCell(
                symbol=symbol,
                timeframe=timeframe,
                filled=f[0],
                no_fill=f[1],
                entered=f[2],
                entry_rejected=f[3],
                marginal_fills=f[4],
                r=r_by_cell.get((symbol, timeframe), empty_r),
            )
        )
    return cells


# ---------------------------------------------------------------------------
# 백테스트 쪽 집계 — 채택 엔진 재산출(창, 워밍업 연속)
# ---------------------------------------------------------------------------


def _reason_r_stats(result: BacktestResult, take_profit_r: float) -> RStats:
    """백테스트 거래의 청산 사유 기준 실현 R(`harness.mean_r`와 같은 정의)."""
    reasons: list[bool | None] = []
    for trade in result.trades:
        reason = trade.exits[-1].reason if trade.exits else None
        if reason is ExitReason.TAKE_PROFIT:
            reasons.append(True)
        elif reason is ExitReason.STOP_LOSS:
            reasons.append(False)
        else:
            reasons.append(None)
    return r_stats_from_reasons(reasons, take_profit_r=take_profit_r)


def _cell_from_market(
    market: MarketData,
    ob_result: OrderBlockResult,
    *,
    start_ms: int,
    end_ms: int,
) -> BacktestParityCell:
    """미리 로드·탐지된 셀의 백테스트 파리티 셀을 낸다(누수 0 회귀 테스트용 분리)."""
    cfg = build_config(market.timeframe)
    params_base: ConfluenceParams = build_params(fill=BASELINE_FILL)
    params_pen5: ConfluenceParams = build_params(fill=fill_preset("pen_5bp"))

    taps, reservations = count_taps_reservations(
        ob_result, market, params_base, cfg, day_start_ms=start_ms, day_end_ms=end_ms
    )
    out_base = run_once(
        market, params=params_base, cfg=cfg, order_block_result=ob_result, eval_from_ms=start_ms
    )
    out_pen5 = run_once(
        market, params=params_pen5, cfg=cfg, order_block_result=ob_result, eval_from_ms=start_ms
    )
    eligible = out_base.stats.eligible if out_base.stats is not None else 0
    fills_base = out_base.stats.filled if out_base.stats is not None else 0
    fills_pen5 = out_pen5.stats.filled if out_pen5.stats is not None else 0
    return BacktestParityCell(
        symbol=market.symbol,
        timeframe=market.timeframe,
        taps=taps,
        reservations=reservations,
        eligible=eligible,
        fills_baseline=fills_base,
        fills_pen5=fills_pen5,
        entries=len(out_base.result.trades),
        r=_reason_r_stats(out_base.result, params_base.take_profit_r),
    )


def backtest_parity_cell(
    symbol: str,
    timeframe: str,
    *,
    start_ms: int,
    end_ms: int,
    warmup_days: int,
) -> BacktestParityCell:
    """한 셀의 데이터를 로드·탐지해 창의 백테스트 파리티 셀을 낸다(데이터 없으면 has_data=False)."""
    warmup_start_ms = start_ms - warmup_days * _DAY_MS
    market = load_market_data(
        symbol,
        timeframe,
        start_ms=warmup_start_ms,
        end_ms=end_ms,
        need_1m=True,
        funding=False,
    )
    if market.htf_df.empty or market.df_1m.empty:
        return BacktestParityCell(
            symbol=symbol,
            timeframe=timeframe,
            taps=0,
            reservations=0,
            eligible=0,
            fills_baseline=0,
            fills_pen5=0,
            entries=0,
            r=RStats(n=0, wins=0, losses=0, mean_r=None),
            has_data=False,
        )
    ob_result = detect_order_blocks(market, OrderBlockParams())
    return _cell_from_market(market, ob_result, start_ms=start_ms, end_ms=end_ms)


@dataclass(frozen=True)
class _CellTask:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    warmup_days: int


def _run_cell_task(task: _CellTask) -> BacktestParityCell:
    return backtest_parity_cell(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        warmup_days=task.warmup_days,
    )


def _backtest_cells(
    cells: Sequence[tuple[str, str]],
    *,
    start_ms: int,
    end_ms: int,
    warmup_days: int,
    jobs: int,
) -> list[BacktestParityCell]:
    tasks = [_CellTask(sym, tf, start_ms, end_ms, warmup_days) for sym, tf in cells]
    if jobs > 1 and tasks:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(_run_cell_task, tasks))
    return [_run_cell_task(t) for t in tasks]


# ---------------------------------------------------------------------------
# 창 해석 + 리포트 조립
# ---------------------------------------------------------------------------


def resolve_window(
    journal: OrderJournal,
    store: PaperTradeStore,
    *,
    start: str | None,
    end: str | None,
) -> tuple[int, int, str, str]:
    """`[start_ms, end_ms)` 창 + 날짜 키를 푼다.

    `--start`/`--end`(KST `YYYY-MM-DD`)를 주면 그 날들의 자정 경계를 쓴다. 안 주면 장부에서
    유추한다: 러너 가동 구간(`sessions`)의 처음~끝을 우선하고, 없으면 페이퍼 거래 창을 쓴다.
    둘 다 비면 오늘(KST) 하루로 둔다(빈 리포트가 나오되 창은 유효하다).
    """
    if start is not None and end is not None:
        s_day = datetime.strptime(start, "%Y-%m-%d").date()
        e_day = datetime.strptime(end, "%Y-%m-%d").date()
        start_ms = kst_day_bounds_for_date(s_day)[0]
        end_ms = kst_day_bounds_for_date(e_day)[1]
        return start_ms, end_ms, s_day.isoformat(), e_day.isoformat()

    lo: int | None = None
    hi: int | None = None
    sessions = journal.sessions()
    if sessions:
        lo = min(s.started_ms for s in sessions)
        hi = max(s.last_seen_ms for s in sessions)
    span = store.time_span()
    if span is not None:
        lo = span[0] if lo is None else min(lo, span[0])
        hi = span[1] if hi is None else max(hi, span[1])
    if lo is None or hi is None:
        today = datetime.now(tz=KST).date()
        start_ms, end_ms = kst_day_bounds_for_date(today)
        return start_ms, end_ms, today.isoformat(), today.isoformat()

    # 창을 KST 자정 경계로 넓혀 백테스트 평가 경계와 자를 맞춘다.
    start_ms = kst_day_bounds_for_date(datetime.fromtimestamp(lo / 1000, tz=KST).date())[0]
    end_ms = kst_day_bounds_for_date(datetime.fromtimestamp(hi / 1000, tz=KST).date())[1]
    start_key = datetime.fromtimestamp(start_ms / 1000, tz=KST).date().isoformat()
    end_key = datetime.fromtimestamp((end_ms - 1) / 1000, tz=KST).date().isoformat()
    return start_ms, end_ms, start_key, end_key


def _uptime_ms(journal: OrderJournal, *, start_ms: int, end_ms: int) -> int:
    """창과 겹치는 러너 세션 구간의 합(ms). 겹침만 센다(창 밖 가동은 빼고)."""
    total = 0
    for s in journal.sessions():
        lo = max(s.started_ms, start_ms)
        hi = min(s.last_seen_ms, end_ms)
        if hi > lo:
            total += hi - lo
    return total


def _ledger_cells(journal: OrderJournal, store: PaperTradeStore) -> list[tuple[str, str]]:
    """장부에 흔적이 있는 (심볼, TF) 집합 — 러너가 실제로 돌린 셀만 백테스트한다.

    36셀 전부를 도는 대신(15m·긴 워밍업은 초선형으로 무겁다, WAN-203) 라이브가 실제로 거래한
    셀만 대조한다. `fill_stats`(전 이력 시리즈)와 페이퍼 거래 시리즈의 합집합이다.
    """
    cells = {(s.symbol, s.timeframe) for s in journal.fill_stats()}
    cells |= set(store.list_series())
    return sorted(cells)


def build_parity_report(
    journal: OrderJournal,
    store: PaperTradeStore,
    *,
    start_ms: int,
    end_ms: int,
    start_key: str,
    end_key: str,
    cells: Sequence[tuple[str, str]],
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    jobs: int = 1,
) -> ParityReport:
    """창 하나의 파리티 리포트를 계산한다 — 라이브는 조회, 백테스트는 채택 엔진 재산출.

    `cells`가 비면 백테스트를 돌리지 않는다(라이브 표본이 없어 대조할 것이 없다) — 빈 리포트가
    나오고 렌더러가 "표본 없음" 판정을 낸다.
    """
    take_profit_r = build_params(fill=BASELINE_FILL).take_profit_r
    entries = journal.ledger_entries(start_ms=start_ms, end_ms=end_ms)
    records = store.list_records()
    paper = paper_cells(
        entries, records, start_ms=start_ms, end_ms=end_ms, take_profit_r=take_profit_r
    )
    backtest = _backtest_cells(
        cells, start_ms=start_ms, end_ms=end_ms, warmup_days=warmup_days, jobs=jobs
    )
    return ParityReport(
        start_ms=start_ms,
        end_ms=end_ms,
        start_key=start_key,
        end_key=end_key,
        take_profit_r=take_profit_r,
        uptime_ms=_uptime_ms(journal, start_ms=start_ms, end_ms=end_ms),
        window_ms=end_ms - start_ms,
        paper=tuple(paper),
        backtest=tuple(backtest),
    )


# ---------------------------------------------------------------------------
# 렌더
# ---------------------------------------------------------------------------


def _short(symbol: str) -> str:
    return symbol.split("/", 1)[0]


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _r(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}R"


def _days(ms: int) -> str:
    return f"{ms / _DAY_MS:.1f}일"


def render_parity(report: ParityReport, *, by_cell: bool = False) -> str:
    """파리티 리포트를 마크다운으로 렌더한다(체결률·실현 R 대조 + 판정)."""
    lines: list[str] = [
        f"# 페이퍼 ↔ 백테스트 파리티 · {report.start_key} ~ {report.end_key} (KST) — WAN-247",
        "",
        "**낙관 측정이 아니라 파리티(배선) 감사다** — 페이퍼도 백테스트도 둘 다 `baseline`"
        '("닿으면 체결")·1분봉이라 큐 우선순위를 모델링하지 않는다. 이 표가 재는 것은'
        " 라이브가 백테스트대로 도는가(탭·예약 배선)와, 콜드스타트·러너 다운타임·집행 거부"
        "만큼의 체결·R 차이다. 진짜 낙관 실측은 틱·호가(WAN-98 Canceled·WAN-246) 소관이다.",
        "",
        f"창 {report.start_key}~{report.end_key} · 길이 {_days(report.window_ms)} · 러너 가동"
        f" {_days(report.uptime_ms)}"
        + (f" ({_pct(report.uptime_frac)})" if report.uptime_frac is not None else "")
        + " — 가동률이 낮으면 라이브 체결이 그만큼 적은 게 정상이다(버그 아님).",
        "",
    ]

    if not report.has_paper:
        lines.extend(_render_empty(report))
        return "\n".join(lines)

    lines.extend(_render_aggregate(report))
    lines.extend(_render_verdict(report))
    if by_cell:
        lines.extend(_render_by_cell(report))
    return "\n".join(lines)


def _agg_paper(
    cells: Sequence[PaperParityCell], tp_r: float
) -> tuple[int, int, int, int, RStats, int]:
    filled = sum(c.filled for c in cells)
    no_fill = sum(c.no_fill for c in cells)
    entered = sum(c.entered for c in cells)
    rejected = sum(c.entry_rejected for c in cells)
    marginal = sum(c.marginal_fills for c in cells)
    wins = sum(c.r.wins for c in cells)
    losses = sum(c.r.losses for c in cells)
    n = wins + losses
    mean_r = (wins * tp_r - losses) / n if n else None
    nets = [c.r.net_mean_r for c in cells if c.r.net_mean_r is not None and c.r.n]
    net = sum(nets) / len(nets) if nets else None
    return filled, no_fill, entered, rejected, RStats(n, wins, losses, mean_r, net), marginal


def _agg_backtest(
    cells: Sequence[BacktestParityCell], tp_r: float
) -> tuple[int, int, int, int, int, RStats]:
    live_cells = [c for c in cells if c.has_data]
    taps = sum(c.taps for c in live_cells)
    reservations = sum(c.reservations for c in live_cells)
    eligible = sum(c.eligible for c in live_cells)
    fills_base = sum(c.fills_baseline for c in live_cells)
    fills_pen5 = sum(c.fills_pen5 for c in live_cells)
    wins = sum(c.r.wins for c in live_cells)
    losses = sum(c.r.losses for c in live_cells)
    n = wins + losses
    mean_r = (wins * tp_r - losses) / n if n else None
    return taps, reservations, eligible, fills_base, fills_pen5, RStats(n, wins, losses, mean_r)


def _render_aggregate(report: ParityReport) -> list[str]:
    tp = report.take_profit_r
    p_filled, p_no_fill, p_entered, p_rej, p_r, p_marg = _agg_paper(report.paper, tp)
    _, bt_res, bt_elig, bt_fills, bt_pen5, bt_r = _agg_backtest(report.backtest, tp)
    p_fill_rate = p_filled / (p_filled + p_no_fill) if (p_filled + p_no_fill) else None
    bt_fill_rate = bt_fills / bt_elig if bt_elig else None
    bt_pen5_rate = bt_pen5 / bt_elig if bt_elig else None
    p_marg_share = p_marg / p_filled if p_filled else None

    lines = [
        "## 집계 대조 (전 셀 합산)",
        "",
        "| 지표 | 라이브(페이퍼) | 백테스트(baseline) | 참고 |",
        "| -- | --: | --: | -- |",
        f"| 체결 수 | {p_filled} | {bt_fills} | pen_5bp {bt_pen5} |",
        f"| 체결률 | {_pct(p_fill_rate)} | {_pct(bt_fill_rate)} | pen_5bp {_pct(bt_pen5_rate)} |",
        f"| 진입률(체결→진입) | {_pct(_safe_rate(p_entered, p_rej))} | — |"
        f" 진입 {p_entered}/거부 {p_rej} |",
        f"| 스침 체결(관통<5bp) | {_pct(p_marg_share)} | — | {p_marg}건 |",
        f"| 실현 R(사유 기준) | {_r(p_r.mean_r)} | {_r(bt_r.mean_r)} | net {_r(p_r.net_mean_r)} |",
        f"| 승률 | {_pct(p_r.win_rate)} | {_pct(bt_r.win_rate)} | 표본 L{p_r.n}/BT{bt_r.n} |",
        "",
        f"⚙️ 실현 R = 청산 사유 기준(손절 −1.0R · 익절 +{tp:g}R) — 양쪽 같은 자. 페이퍼 `net`은"
        " 수수료·슬리피지·펀딩을 뺀 `r_multiple` 평균(참고). 백테스트 체결률은 filled/eligible,"
        " 페이퍼 체결률은 filled/(filled+미체결)이라 **분모 정의가 다르다**(각자 native).",
        "",
    ]
    return lines


def _safe_rate(num: int, other: int) -> float | None:
    denom = num + other
    return num / denom if denom else None


def _render_verdict(report: ParityReport) -> list[str]:
    tp = report.take_profit_r
    p_filled, p_no_fill, p_entered, p_rej, p_r, _ = _agg_paper(report.paper, tp)
    _, _, bt_elig, bt_fills, bt_pen5, bt_r = _agg_backtest(report.backtest, tp)
    p_fill_rate = p_filled / (p_filled + p_no_fill) if (p_filled + p_no_fill) else None
    bt_fill_rate = bt_fills / bt_elig if bt_elig else None

    lines = ["## 판정", ""]

    # 체결률 파리티.
    if p_fill_rate is None or bt_fill_rate is None:
        lines.append(
            "- **체결률**: 한쪽 표본이 없어 대조 불가"
            f"(라이브 결말 {p_filled + p_no_fill}건 · 백테스트 eligible {bt_elig})."
        )
    else:
        gap = p_fill_rate - bt_fill_rate
        pen5_closer = bt_pen5 and abs(p_filled - bt_pen5) < abs(p_filled - bt_fills)
        closer = "pen_5bp" if pen5_closer else "baseline"
        lines.append(
            f"- **체결률**: 라이브 {_pct(p_fill_rate)} vs 백테스트 baseline {_pct(bt_fill_rate)}"
            f"(차이 {gap * 100:+.1f}%p). 라이브가 더 낮으면 대개 콜드스타트·다운타임·지연 탓이지"
            f" 배선 버그가 아니다(가동률 {_pct(report.uptime_frac)}). 라이브 체결 수가"
            f" **{closer}** 쪽에 더 가깝다."
        )

    # 실현 R 파리티.
    if p_r.mean_r is None or bt_r.mean_r is None:
        lines.append(
            f"- **실현 R**: 한쪽 표본이 없어 대조 불가(라이브 {p_r.n}건 · 백테스트 {bt_r.n}건)."
            " 페이퍼를 며칠~몇 주 더 돌려 청산 표본을 쌓아야 유의미하다."
        )
    else:
        lines.append(
            f"- **실현 R(사유 기준)**: 라이브 {_r(p_r.mean_r)}(승률 {_pct(p_r.win_rate)},"
            f" {p_r.n}건) vs 백테스트 {_r(bt_r.mean_r)}(승률 {_pct(bt_r.win_rate)}, {bt_r.n}건)."
            " 같은 셋업이 같은 방향으로 청산되면 붙는다 — 크게 갈리면 손절/익절 배선이나 존"
            " 정의를 의심한다."
        )

    # 진입 거부 분포.
    funnel = _rejection_summary(report)
    if funnel:
        lines.append(
            f"- **집행 거부 분포**(체결 후): {funnel}. 백테스트도 같은 가드로 후보를"
            " 버리므로(WAN-194) 거부 자체는 파리티 위반이 아니다."
        )
    else:
        lines.append("- **집행 거부 분포**(체결 후): 창 안 거부 없음.")

    lines.append(
        "- **파리티 종합**: 이 표는 엣지 판정이 아니다 — 「엣지 없음」(WAN-84/88/111/114/124)은"
        " 그대로다. 라이브가 백테스트대로 도는지의 감사이며, 진짜 체결 낙관(큐 우선순위)은"
        " 틱·호가(WAN-98 Canceled·WAN-246)에서만 잰다. 기본값·토대 불변 ·"
        " `ALPHABLOCK_LIVE_TRADING=false` 유지."
    )
    lines.append("")
    return lines


def _rejection_summary(report: ParityReport) -> str:
    """창 안 라이브 집행 거부(체결 후)의 사유별 건수 문자열."""
    total = sum(c.entry_rejected for c in report.paper)
    if not total:
        return ""
    return f"총 {total}건"


def _render_empty(report: ParityReport) -> list[str]:
    return [
        "## 판정 — 라이브 표본 없음",
        "",
        "이 창에 페이퍼 러너의 체결·거래 기록이 없다. 대조할 라이브 표본이 없으므로 판정을"
        " 낼 수 없다 — **없는 표본을 지어내지 않는다**(WAN-195 원칙).",
        "",
        "가능한 원인:",
        "1. **러너 미가동** — 페이퍼 러너(`alphablock live`)가 이 창에 돌지 않았다."
        f" 러너 가동 {_days(report.uptime_ms)}.",
        "2. **로컬 스냅샷** — 로컬 `ohlcv.db`는 07-22에서 얼어붙은 스냅샷일 수 있다(WAN-195)."
        " 러너가 쓰는 **서버 장부**를 `--db`로 지정해야 실데이터다.",
        "3. **표본 축적 시간** — 페이퍼는 며칠~몇 주 돌려야 유의미한 표본이 쌓인다.",
        "",
        "러너가 살아 있는 서버 DB를 대상으로 다시 실행하면 이 리포트가 체결률·실현 R 대조표를"
        " 낸다. 기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지.",
    ]


def _render_by_cell(report: ParityReport) -> list[str]:
    paper_map = {(c.symbol, c.timeframe): c for c in report.paper}
    bt_map = {(c.symbol, c.timeframe): c for c in report.backtest}
    keys = sorted(set(paper_map) | set(bt_map))

    lines = [
        "",
        f"## 심볼×TF별 대조 · {report.start_key}~{report.end_key} (KST)",
        "",
        "| 심볼 | TF | 체결률(L/BT) | 진입률(L) | 실현 R(L/BT) | 승률(L/BT) | 표본(L/BT) |",
        "| -- | -- | -- | --: | -- | -- | -- |",
    ]
    any_row = False
    for sym, tf in keys:
        p = paper_map.get((sym, tf))
        b = bt_map.get((sym, tf))
        if p is None and (b is None or not b.has_data):
            continue
        any_row = True
        p_fill = _pct(p.fill_rate) if p else "—"
        p_entry = _pct(p.entry_rate) if p else "—"
        p_r = _r(p.r.mean_r) if p else "—"
        p_wr = _pct(p.r.win_rate) if p else "—"
        p_n = p.r.n if p else 0
        if b is not None and b.has_data:
            b_fill = _pct(b.fill_rate)
            b_r = _r(b.r.mean_r)
            b_wr = _pct(b.r.win_rate)
            b_n = b.r.n
        else:
            b_fill = b_r = b_wr = "—"
            b_n = 0
        lines.append(
            f"| {_short(sym)} | {tf} | {p_fill}/{b_fill} | {p_entry} | {p_r}/{b_r} |"
            f" {p_wr}/{b_wr} | {p_n}/{b_n} |"
        )
    if not any_row:
        lines.append("| (해당 셀 없음) | | | | | | |")
    lines.append("")
    lines.append(
        "각 칸 `라이브/백테스트`. 체결률 분모는 각자 native(L=filled/(filled+미체결)·"
        "BT=filled/eligible). 실현 R은 사유 기준(양쪽 같은 자). `—`는 표본·데이터 없음."
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-247 페이퍼 ↔ 백테스트 파리티(체결률·실현 R) 리포트",
    )
    parser.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    parser.add_argument("--start", default=None, metavar="YYYY-MM-DD", help="창 시작(KST)")
    parser.add_argument("--end", default=None, metavar="YYYY-MM-DD", help="창 끝(KST, 포함)")
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=DEFAULT_WARMUP_DAYS,
        help="백테스트 워밍업 길이(일, 기본 %(default)s) — 라이브 전-이력 존 재고 근사 노브",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="대조할 심볼(콤마 구분, 기본: 장부에 있는 셀). 예: BTC/USDT:USDT",
    )
    parser.add_argument(
        "--tf", default=None, help="대조할 TF(콤마 구분, 기본: 장부에 있는 셀). 예: 15m,1h"
    )
    parser.add_argument("--by-cell", action="store_true", help="심볼×TF별 대조 표도 출력")
    parser.add_argument(
        "--jobs", type=int, default=1, help="백테스트 (심볼, TF) 병렬 워커 수(기본 1)"
    )
    args = parser.parse_args(argv)

    if (args.start is None) != (args.end is None):
        parser.error("--start와 --end는 함께 줘야 합니다(하나만 주면 창이 모호합니다).")

    db_path = args.db if args.db is not None else get_settings().db_path
    journal = OrderJournal(db_path)
    store = PaperTradeStore(db_path)
    try:
        start_ms, end_ms, start_key, end_key = resolve_window(
            journal, store, start=args.start, end=args.end
        )
        cells = resolve_cells(journal, store, symbols=args.symbols, timeframes=args.tf)
        report = build_parity_report(
            journal,
            store,
            start_ms=start_ms,
            end_ms=end_ms,
            start_key=start_key,
            end_key=end_key,
            cells=cells,
            warmup_days=args.warmup_days,
            jobs=args.jobs,
        )
    finally:
        journal.close()
        store.close()
    print(render_parity(report, by_cell=args.by_cell))
    return 0


def resolve_cells(
    journal: OrderJournal,
    store: PaperTradeStore,
    *,
    symbols: str | None,
    timeframes: str | None,
) -> list[tuple[str, str]]:
    """대조할 (심볼, TF) 목록. `--symbols`/`--tf`를 주면 그 곱, 아니면 장부에 있는 셀."""
    if symbols is not None and timeframes is not None:
        syms = [s.strip() for s in symbols.split(",") if s.strip()]
        tfs = [t.strip() for t in timeframes.split(",") if t.strip()]
        return [(s, t) for s in syms for t in tfs]
    return _ledger_cells(journal, store)


if __name__ == "__main__":
    raise SystemExit(main())
