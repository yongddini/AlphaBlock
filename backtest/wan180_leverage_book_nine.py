"""WAN-180 — 레버리지 북 9종목 재측정: 결합 vs cap-only + 밀림(스킵) 회계·기회비용.

사용자 질문·결정(2026-07-23): 배수별 **수익률/MDD**를 9종목에서 보고, *"심볼이 많아지니까
중복 땜에 못 들어가는게 얼마나 있는지"*(= 밀림 = 스킵)를 재고, **MDD 30% 예산** 아래 어느
구성을 쓸지 고른다. 밀림을 직접 줄이는 **cap-only 레버리지**(팔 B)를 함께 재고, **팔 A에서
밀려난 셋업의 기회비용**(밀림 = 놓친 수익인가, 안전장치인가)을 부호로 판정한다.

## 격자 — WAN-169 기계 재사용, 유니버스·창만 이동

* **팔**: A = 결합(`combined`, WAN-169 방식 — 매 거래 사이징 N배) vs **B = cap-only**
  (`cap_only`, 신설 옵트인 — 북 명목 상한만 N배, 거래 크기는 1배 그대로).
* **배수**: 1 · 2 · 3 · 5 × **스코프**: 15m · 1h · both(18칸 = 9종목 × 2TF).
* **유니버스**: `nine`(9종목 = WAN-176) + **`six`(기존 6종목, 같은 창)** — 6→9 증가분을
  창 이동과 섞지 않고 같은 창 안에서 가른다(WAN-169 옛 표는 3년 창이라 직접 비교 금지).
* **구간**: full · is · **oos_warm(주)** · oos(스트레스) — WAN-166 정본 규약,
  straddle 회계 (b). **창**: 2020-09-15 ~ 2026-07-22(WAN-175/176 확정 6년, 못 박음).
* 렌즈 `baseline` 단독(WAN-128) · 오늘의 채택 엔진 그대로(옛 핀 없음) · leave-one-out.

## 신규 종목 펀딩 대리 (사용자 지시 2026-07-23)

신규 3종목(DOGE·LINK·LTC)은 이 창에서 **펀딩 데이터 커버리지 0%** 라(WAN-176이 확인한
공백) 그대로 두면 펀딩비 0으로 성과가 부풀려진다. 사용자 지시: *"펀딩비 계산은 이전
6종목중에서 가장 높은 애로 계산해주라"* — 기존 6종목 중 **확정 펀딩 평균이 가장 높은**
(롱에게 가장 비싼) 종목의 시계열을 신규 종목 칸에 그대로 싣는다(`apply_funding_proxy`,
보수적 대체). 대체 칸은 `engine_*` 배선 검산에서 빠지고(표준 경로는 원본 펀딩으로 돈다),
검산은 기존 6종목 칸이 담당한다. `--no-funding-proxy`로 끌 수 있다.

## 밀림 기회비용 (팔 A) — 상한(upper bound)임을 명시

밀린 셋업 하나하나를 「그때 넣었다면」으로 격리 평가한다: 스킵 순간의 공유 자본
(`SkippedSetup.equity`)으로 `_to_trade`(실제 비용 모델·같은 배수 사이징)를 태워 net R와
수익 기여를 합산한다. ⚠️ **이 값은 상한이다** — 밀린 걸 다 넣었다면 그것들도 자본·상한을
먹어 서로 또 밀어냈을 텐데 그 상호작용을 무시하고 격리로 계산한다(방향 판단엔 충분하나
크기는 과대다). 밀린 셋업의 가상 체결도 `baseline`(낙관) 위의 값이다.

## 재현

```
uv run python -m backtest.wan180_leverage_book_nine --jobs 6
uv run python -m backtest.wan180_leverage_book_nine --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.leverage_book import (
    BookCell,
    BookOutcome,
    LeverageBookParams,
    LeverageMode,
    _FundingIndex,
    run_leverage_book,
)
from backtest.models import BacktestResult
from backtest.wan167_position_census import MAIN_TIMEFRAMES
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    MIN_TRADES,
    SEGMENTS,
    CellPayload,
    CellRow,
    _isolated_metrics,
    _mean,
    _scope_payloads,
    _segment_cells,
    _short,
    cells_from_csv,
    cells_to_frame,
    run_cells,
    verify_cells,
)
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_END as NEW_END
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_START as NEW_START
from backtest.wan176_nine_symbol_rebaseline import NEW_SYMBOLS, NINE_SYMBOLS, OLD_SYMBOLS
from backtest.zone_limit_backtest import _to_trade, build_result_from_trades
from data.models import FundingRate

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan180_leverage_book_cells.csv"
DEFAULT_GRID_CSV = REPORTS_DIR / "wan180_leverage_book_grid.csv"
DEFAULT_OPP_CSV = REPORTS_DIR / "wan180_opportunity_cost.csv"
DEFAULT_MONTHLY_CSV = REPORTS_DIR / "wan180_monthly_returns.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan180_leverage_book_summary.md"
DEFAULT_META_JSON = REPORTS_DIR / "wan180_run_meta.json"

MULTIPLES: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)
MODES: tuple[LeverageMode, ...] = ("combined", "cap_only")
UNIVERSES: tuple[str, ...] = ("nine", "six")

#: 사용자가 잡은 낙폭 예산 — 판정은 차가움(`oos`) MDD ≤ 30%다(이슈 완료기준).
MDD_BUDGET = 0.30

#: WAN-169 6종목·3년 표에서 차가움 MDD 30% 경계를 낀 추천 4조합(이슈 「반드시 포함」).
#: 9종목·6년에서 이 넷이 예산에 남는지 확인한다 — (스코프, 배수).
RECOMMENDED_COMBOS: tuple[tuple[str, float], ...] = (
    ("both", 2.0),
    ("15m", 3.0),
    ("1h", 3.0),
    ("both", 3.0),
)


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class BookRow(BaseModel):
    """격자 한 셀 — (유니버스 × 스코프 × 팔 × 배수 × 구간 × 제외 종목)의 성과·위험·밀림."""

    model_config = ConfigDict(frozen=True)

    universe: str
    """`nine`(9종목) · `six`(기존 6종목, 같은 창) — 6→9 대조는 이 축 안에서만 성립한다."""
    scope: str
    arm: str
    """`isolated`(격리 현행 — 칸 평균) · `book`(공유 자본 북)."""
    leverage_mode: str = ""
    """북 행의 배수 자리: `combined`(팔 A) · `cap_only`(팔 B). 격리 행은 빈 문자열."""
    multiple: float
    segment: str
    exclude_symbol: str = ""
    num_cells: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    peak_concurrency: int | None = None
    max_concurrent_risk: float | None = None
    max_open_notional_ratio: float | None = None
    liquidation_events: int | None = None
    clamped_entries: int | None = None
    skipped_cell_busy: int | None = None
    skipped_notional: int | None = None
    skipped_sizing: int | None = None

    @field_validator(
        "peak_concurrency",
        "max_concurrent_risk",
        "max_open_notional_ratio",
        "liquidation_events",
        "clamped_entries",
        "skipped_cell_busy",
        "skipped_notional",
        "skipped_sizing",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """CSV 왕복의 빈 칸(`""`/NaN)을 `None`으로 — `RunRow`와 같은 함정 방지(WAN-130)."""
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def return_over_mdd(self) -> float | None:
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


class OppRow(BaseModel):
    """밀림 기회비용 한 셀 — (스코프 × 구간 × 배수 × 스킵 사유)의 가상 손익 vs 실현.

    ⚠️ 가상 열은 전부 **격리 상한**이다(모듈 독스트링) — 밀린 셋업끼리의 상호 밀어냄을
    무시한다. 부호(손해 vs 보호)로만 읽고 크기로 인용하지 말 것.
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    segment: str
    multiple: float
    reason: str
    """`cell_busy`(칸 점유로 밀림) · `notional`(북 명목 상한 소진으로 밀림)."""
    count: int
    """이 사유로 밀린 셋업 수."""
    priced: int
    """가상 사이징이 값을 매긴 수 — 사이징 가드가 거른 셋업은 가상으로도 못 산다."""
    hypo_total_r: float
    """가상 net R 합(비용·펀딩 반영). **부호가 판정이다**: + = 놓친 수익, − = 안전장치."""
    hypo_mean_r: float
    hypo_win_rate: float
    hypo_return_sum: float
    """Σ(가상 손익 ÷ 스킵 순간 자본) — 비복리 수익 기여 상한."""
    actual_trades: int
    actual_total_r: float
    actual_mean_r: float
    actual_win_rate: float
    actual_return_sum: float


class MonthlyRow(BaseModel):
    """북 자본곡선의 월별 수익률 한 칸 (사용자 질문 2026-07-23 「월별 수익률도 알 수 있나」).

    월말 자본 ÷ 직전 월말 자본 − 1 (UTC 달력월 · 거래 청산 시각 기준 복리). 청산이 없던
    달은 행이 없다(자본 불변 = 0%). ⚠️ 복리 곡선의 월 분해라 높은 배수의 월 수익률은
    절대값 인용 금지 — **플러스 월 비율·최악 월(월간 낙폭)** 이 결정에 실질적인 열이다.
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    leverage_mode: str
    multiple: float
    segment: str
    month: str
    """UTC 달력월, `YYYY-MM`."""
    monthly_return: float
    equity_end: float
    num_exits: int


# --------------------------------------------------------------------------- #
# 펀딩 대리 (사용자 지시 2026-07-23)
# --------------------------------------------------------------------------- #


def _mean_confirmed_rate(rates: Sequence[FundingRate]) -> float:
    """확정(예측 제외) 펀딩의 평균 — 대리 종목 선정 자. 비어 있으면 −∞(선정 불가)."""
    vals = [r.rate for r in rates if not r.is_predicted]
    return sum(vals) / len(vals) if vals else float("-inf")


def _reprice_with_funding(
    payload: CellPayload, funding: dict[str, tuple[FundingRate, ...]]
) -> CellPayload:
    """칸의 펀딩 시계열을 갈아끼우고 격리 성과 행을 다시 계산한다.

    후보(진입·체결 집합)는 펀딩과 무관하므로 그대로다 — 움직이는 것은 손익 변환뿐이다.
    ⚠️ 재계산 행은 `engine_*` 검산 값을 비운다: 표준 경로(`harness.run_once`)는 그 칸의
    (비어 있는) 원본 펀딩으로 돌았으므로 대리 펀딩 성과와 비교하면 배선 검산이 아니라
    펀딩 차이를 재는 셈이 된다. 검산은 대리가 필요 없는 기존 6종목 칸이 담당한다.
    """
    cfg = harness.build_config(payload.timeframe)
    rows: list[CellRow] = []
    for segment in (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS):
        cands = payload.candidates[segment]
        num_trades, win_rate, total_return, mdd = _isolated_metrics(
            cands, cfg, payload.timeframe, funding[segment]
        )
        rows.append(
            CellRow(
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                segment=segment,
                num_candidates=len(cands),
                num_trades=num_trades,
                win_rate=win_rate,
                total_return=total_return,
                max_drawdown=mdd,
            )
        )
    warm = tuple(
        c for c in payload.candidates[SEGMENT_FULL] if c.trigger_time >= payload.boundary_ms
    )
    num_trades, win_rate, total_return, mdd = _isolated_metrics(
        warm, cfg, payload.timeframe, funding[SEGMENT_FULL]
    )
    rows.append(
        CellRow(
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            segment=SEGMENT_OOS_WARM,
            num_candidates=len(warm),
            num_trades=num_trades,
            win_rate=win_rate,
            total_return=total_return,
            max_drawdown=mdd,
        )
    )
    return dataclasses.replace(payload, funding=dict(funding), rows=tuple(rows))


def apply_funding_proxy(payloads: Sequence[CellPayload]) -> tuple[list[CellPayload], str]:
    """신규 3종목의 펀딩을 기존 6종목 중 **최고 펀딩** 종목의 시계열로 대체한다.

    사용자 지시(2026-07-23): 신규 종목(DOGE·LINK·LTC)은 이 창에서 펀딩 데이터 커버리지가
    0%라 펀딩비가 0으로 계상된다 — *"펀딩비 계산은 이전 6종목중에서 가장 높은 애로
    계산해주라"*. 대리 종목 = 기존 6종목 중 full 구간 확정 펀딩 **평균이 가장 높은**(=
    롱에게 가장 비싼) 종목이고, 그 시계열(같은 TF 칸의 것)을 신규 종목 칸에 그대로 싣는다.
    보수적 대체다 — 실제 신규 종목 펀딩이 이보다 쌌다면 성과는 여기 값보다 좋았을 것이다.
    기존 6종목의 자기 펀딩(커버리지 ~86%)은 손대지 않는다.
    """
    old_short = {_short(s) for s in OLD_SYMBOLS}
    new_short = {_short(s) for s in NEW_SYMBOLS}
    olds = [p for p in payloads if _short(p.symbol) in old_short]
    if not olds or not any(_short(p.symbol) in new_short for p in payloads):
        return list(payloads), ""  # 대체할 신규 칸이 없거나 대리 후보가 없다(부분 실행).

    rate_by_symbol: dict[str, float] = {}
    for p in olds:
        short = _short(p.symbol)
        rate = _mean_confirmed_rate(p.funding[SEGMENT_FULL])
        rate_by_symbol[short] = max(rate_by_symbol.get(short, float("-inf")), rate)
    proxy_short = max(rate_by_symbol, key=lambda s: rate_by_symbol[s])
    if not math.isfinite(rate_by_symbol[proxy_short]):
        return list(payloads), ""  # 기존 종목에도 확정 펀딩이 없다 — 대체 불가.

    proxy_by_tf = {p.timeframe: p for p in olds if _short(p.symbol) == proxy_short}
    out: list[CellPayload] = []
    replaced: list[str] = []
    for p in payloads:
        if _short(p.symbol) not in new_short or p.funding[SEGMENT_FULL]:
            # 신규 종목이라도 자기 펀딩 데이터가 있으면 실데이터를 대리로 덮지 않는다 —
            # 대리는 「데이터가 없어 0으로 계상되는」 문제의 교정이지 데이터 교체가 아니다.
            out.append(p)
            continue
        proxy = proxy_by_tf.get(p.timeframe) or next(iter(proxy_by_tf.values()))
        out.append(_reprice_with_funding(p, proxy.funding))
        replaced.append(f"{_short(p.symbol)} {p.timeframe}")
    if not replaced:
        return out, ""  # 신규 칸 전부가 자기 데이터를 갖고 있다 — 대체할 것이 없다.
    note = (
        f"신규 종목 펀딩 대리(사용자 지시 2026-07-23): {', '.join(replaced)} 칸의 펀딩을 "
        f"**{proxy_short}**(기존 6종목 중 확정 펀딩 평균 최고 "
        f"{rate_by_symbol[proxy_short] * 100:.4f}%/정산)의 시계열로 대체했다 — 신규 종목은 "
        "이 창에서 펀딩 데이터가 없어(커버리지 0%) 그대로 두면 펀딩비 0으로 성과가 "
        "부풀려진다. 보수적(롱에게 가장 비싼) 대체이며, 대체 칸은 `engine_*` 배선 검산에서 "
        "빠진다(표준 경로는 원본 펀딩으로 돌기 때문 — 검산은 기존 6종목 칸이 담당)."
    )
    return out, note


# --------------------------------------------------------------------------- #
# 격자 빌드
# --------------------------------------------------------------------------- #


def _opportunity_rows(
    outcome: BookOutcome,
    cells: Sequence[BookCell],
    scope: str,
    segment: str,
    multiple: float,
) -> list[OppRow]:
    """팔 A 한 실행의 밀림 기회비용 — 스킵 사유별 가상 손익 vs 실현 손익."""
    rates_index = {cell.key: _FundingIndex(cell.funding_rates) for cell in cells}
    eff_cfg = outcome.effective_config

    placed = outcome.stats.placed_records
    actual_rs = [p.realized_pnl / p.risk_amount for p in placed if p.risk_amount > 0.0]
    actual_return_sum = sum(p.realized_pnl / p.equity for p in placed if p.equity > 0.0)
    actual_wins = sum(1 for p in placed if p.realized_pnl > 0.0)
    actual_win_rate = actual_wins / len(placed) if placed else 0.0

    rows: list[OppRow] = []
    for reason in ("cell_busy", "notional"):
        records = [r for r in outcome.stats.skip_records if r.reason == reason]
        hypo_rs: list[float] = []
        hypo_return_sum = 0.0
        hypo_wins = 0
        for record in records:
            cand = record.candidate
            rates = rates_index[record.cell].window(cand.entry_time, cand.exit_time)
            trade = _to_trade(cand, record.equity, eff_cfg, rates, 0.0)
            if trade is None:
                continue  # 사이징 가드 거부 — 가상으로도 살 수 없던 셋업.
            risk = abs(trade.entry_price - cand.stop_price) * trade.quantity
            if risk <= 0.0:
                continue
            hypo_rs.append(trade.realized_pnl / risk)
            if record.equity > 0.0:
                hypo_return_sum += trade.realized_pnl / record.equity
            if trade.realized_pnl > 0.0:
                hypo_wins += 1
        rows.append(
            OppRow(
                scope=scope,
                segment=segment,
                multiple=multiple,
                reason=reason,
                count=len(records),
                priced=len(hypo_rs),
                hypo_total_r=sum(hypo_rs),
                hypo_mean_r=_mean(hypo_rs),
                hypo_win_rate=hypo_wins / len(hypo_rs) if hypo_rs else 0.0,
                hypo_return_sum=hypo_return_sum,
                actual_trades=len(placed),
                actual_total_r=sum(actual_rs),
                actual_mean_r=_mean(actual_rs),
                actual_win_rate=actual_win_rate,
                actual_return_sum=actual_return_sum,
            )
        )
    return rows


def _monthly_rows(
    result: BacktestResult,
    scope: str,
    leverage_mode: str,
    multiple: float,
    segment: str,
) -> list[MonthlyRow]:
    """북 자본곡선을 UTC 달력월로 접은 월별 수익률 — 월말 자본 ÷ 직전 월말 자본 − 1.

    자본곡선의 첫 점(첫 진입 시각의 초기자본)은 청산이 아니므로 월 카운트에 넣지 않는다.
    청산이 없던 달은 자본이 안 움직여 행이 없다(0%로 읽는다).
    """
    curve = result.equity_curve
    if len(curve) < 2:
        return []
    month_end: dict[str, float] = {}
    month_exits: dict[str, int] = {}
    for point in curve[1:]:  # 첫 점은 초기자본 앵커 — 청산이 아니다.
        stamp = datetime.fromtimestamp(point.time / 1000, tz=UTC)
        month = f"{stamp.year:04d}-{stamp.month:02d}"
        month_end[month] = point.equity
        month_exits[month] = month_exits.get(month, 0) + 1
    rows: list[MonthlyRow] = []
    prev = result.config.initial_capital
    for month in sorted(month_end):
        equity = month_end[month]
        rows.append(
            MonthlyRow(
                scope=scope,
                leverage_mode=leverage_mode,
                multiple=multiple,
                segment=segment,
                month=month,
                monthly_return=equity / prev - 1.0 if prev > 0 else 0.0,
                equity_end=equity,
                num_exits=month_exits[month],
            )
        )
        prev = equity
    return rows


def build_rows(
    payloads: Sequence[CellPayload],
) -> tuple[list[BookRow], list[OppRow], list[MonthlyRow]]:
    """격자 전체 — 두 유니버스 × 두 팔의 북 행 + 격리 대조 행 + 팔 A 기회비용 + 월별 수익률."""
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    old_six = {_short(s) for s in OLD_SYMBOLS}
    cell_rows_by_key = {
        (row.symbol, row.timeframe, row.segment): row for p in payloads for row in p.rows
    }
    book_rows: list[BookRow] = []
    opp_rows: list[OppRow] = []
    monthly_rows: list[MonthlyRow] = []

    for universe in UNIVERSES:
        uni_payloads = (
            list(payloads)
            if universe == "nine"
            else [p for p in payloads if _short(p.symbol) in old_six]
        )
        if universe == "six" and len(uni_payloads) == len(payloads):
            continue  # 부분 실행(6종목만 돌린 경우) — six가 nine의 복제가 되면 내지 않는다.
        symbols = sorted({_short(p.symbol) for p in uni_payloads})
        excludes = ["", *symbols] if universe == "nine" else [""]
        for scope in [*MAIN_TIMEFRAMES, "both"]:
            scoped = _scope_payloads(uni_payloads, scope)
            if not scoped:
                continue
            if scope == "both" and len({p.timeframe for p in scoped}) < 2:
                continue
            for segment in SEGMENTS:
                for exclude in excludes:
                    kept = [p for p in scoped if not exclude or _short(p.symbol) != exclude]
                    iso = [cell_rows_by_key[(p.symbol, p.timeframe, segment)] for p in kept]
                    book_rows.append(
                        BookRow(
                            universe=universe,
                            scope=scope,
                            arm="isolated",
                            multiple=1.0,
                            segment=segment,
                            exclude_symbol=exclude,
                            num_cells=len(iso),
                            num_trades=sum(r.num_trades for r in iso),
                            win_rate=_mean([r.win_rate for r in iso]),
                            total_return=_mean([r.total_return for r in iso]),
                            max_drawdown=_mean([r.max_drawdown for r in iso]),
                        )
                    )
                    cells = _segment_cells(scoped, segment, exclude)
                    for mode in MODES:
                        for multiple in MULTIPLES:
                            outcome = run_leverage_book(
                                cells,
                                base_cfg,
                                LeverageBookParams(leverage_multiple=multiple, leverage_mode=mode),
                            )
                            result = build_result_from_trades(
                                outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
                            )
                            m = result.metrics
                            stats = outcome.stats
                            book_rows.append(
                                BookRow(
                                    universe=universe,
                                    scope=scope,
                                    arm="book",
                                    leverage_mode=mode,
                                    multiple=multiple,
                                    segment=segment,
                                    exclude_symbol=exclude,
                                    num_cells=len(cells),
                                    num_trades=m.num_trades,
                                    win_rate=m.win_rate,
                                    total_return=m.total_return,
                                    max_drawdown=m.max_drawdown,
                                    peak_concurrency=stats.peak_concurrency,
                                    max_concurrent_risk=stats.max_concurrent_risk_ratio,
                                    max_open_notional_ratio=stats.max_open_notional_ratio,
                                    liquidation_events=len(stats.liquidations),
                                    clamped_entries=stats.clamped_entries,
                                    skipped_cell_busy=stats.skipped_cell_busy,
                                    skipped_notional=stats.skipped_notional,
                                    skipped_sizing=stats.skipped_sizing,
                                )
                            )
                            if universe == "nine" and not exclude:
                                monthly_rows.extend(
                                    _monthly_rows(result, scope, mode, multiple, segment)
                                )
                                if mode == "combined":
                                    opp_rows.extend(
                                        _opportunity_rows(outcome, cells, scope, segment, multiple)
                                    )
    return book_rows, opp_rows, monthly_rows


# --------------------------------------------------------------------------- #
# 선택 · 집계
# --------------------------------------------------------------------------- #


def pick(
    rows: Sequence[BookRow],
    *,
    universe: str = "nine",
    scope: str,
    arm: str,
    segment: str,
    leverage_mode: str = "",
    multiple: float | None = None,
    exclude: str = "",
) -> list[BookRow]:
    out = [
        r
        for r in rows
        if r.universe == universe
        and r.scope == scope
        and r.arm == arm
        and r.segment == segment
        and r.leverage_mode == leverage_mode
        and r.exclude_symbol == exclude
        and (multiple is None or r.multiple == multiple)
    ]
    return sorted(out, key=lambda r: r.multiple)


def _one(
    rows: Sequence[BookRow],
    *,
    universe: str = "nine",
    scope: str,
    arm: str,
    segment: str,
    leverage_mode: str = "",
    multiple: float | None = None,
    exclude: str = "",
) -> BookRow:
    found = pick(
        rows,
        universe=universe,
        scope=scope,
        arm=arm,
        segment=segment,
        leverage_mode=leverage_mode,
        multiple=multiple,
        exclude=exclude,
    )
    if len(found) != 1:
        key = (universe, scope, arm, segment, leverage_mode, multiple, exclude)
        raise ValueError(f"행이 정확히 1개여야 합니다: {key} → {len(found)}개")
    return found[0]


def budget_rows(rows: Sequence[BookRow], mode: str) -> list[BookRow]:
    """차가움(`oos`) MDD ≤ 예산 · 청산 0 · 표본 게이트 통과 구성 — 수익 내림차순."""
    under = [
        r
        for r in rows
        if r.universe == "nine"
        and r.arm == "book"
        and r.leverage_mode == mode
        and r.segment == SEGMENT_OOS
        and not r.exclude_symbol
        and r.sample_ok
        and r.max_drawdown <= MDD_BUDGET
        and (r.liquidation_events or 0) == 0
    ]
    return sorted(under, key=lambda r: r.total_return, reverse=True)


def verdict(rows: Sequence[BookRow], opp_rows: Sequence[OppRow]) -> list[str]:
    """판정 3문장 — 예산 승자 · cap-only 교환 · 밀림의 부호. 숫자는 전부 행에서 계산한다."""
    lines: list[str] = []

    # 1) MDD 30% 예산의 승자 — 팔별 최상 구성.
    best_by_mode: dict[str, BookRow | None] = {}
    for mode in MODES:
        under = budget_rows(rows, mode)
        best_by_mode[mode] = under[0] if under else None
    a, b = best_by_mode["combined"], best_by_mode["cap_only"]
    if a is None and b is None:
        lines.append("**예산 판정 불가** — 차가움 MDD ≤ 30%를 지키는 구성이 어느 팔에도 없다.")
    else:

        def _fmt(row: BookRow) -> str:
            warm = _one(
                rows,
                scope=row.scope,
                arm="book",
                segment=SEGMENT_OOS_WARM,
                leverage_mode=row.leverage_mode,
                multiple=row.multiple,
            )
            return (
                f"{row.scope} {row.multiple:g}배(차가움 {row.total_return * 100:+.2f}% / "
                f"MDD {row.max_drawdown * 100:.2f}%, 따뜻 {warm.total_return * 100:+.2f}% / "
                f"{warm.max_drawdown * 100:.2f}%)"
            )

        parts = []
        if a is not None:
            parts.append(f"팔 A(결합) 최상 = {_fmt(a)}")
        if b is not None:
            parts.append(f"팔 B(cap-only) 최상 = {_fmt(b)}")
        winner = ""
        if a is not None and b is not None:
            better = "A(결합)" if a.total_return >= b.total_return else "B(cap-only)"
            winner = f" 예산 안 수익 최대는 **팔 {better}** 쪽이다."
        lines.append(f"**MDD 30% 예산(차가움) 판정**: {'; '.join(parts)}.{winner}")

    # 2) cap-only 교환 — both·oos_warm에서 스킵 감소 vs 위험 증가.
    reduced = 0
    risk_up = 0
    compared = 0
    for multiple in MULTIPLES:
        if multiple <= 1.0:
            continue
        try:
            combined = _one(
                rows,
                scope="both",
                arm="book",
                segment=SEGMENT_OOS_WARM,
                leverage_mode="combined",
                multiple=multiple,
            )
            cap_only = _one(
                rows,
                scope="both",
                arm="book",
                segment=SEGMENT_OOS_WARM,
                leverage_mode="cap_only",
                multiple=multiple,
            )
        except ValueError:
            continue
        compared += 1
        if (cap_only.skipped_notional or 0) < (combined.skipped_notional or 0):
            reduced += 1
        if (cap_only.max_concurrent_risk or 0.0) > (combined.max_concurrent_risk or 0.0):
            risk_up += 1
    if compared:
        lines.append(
            f"**cap-only 교환(both·oos_warm, 배수 N>1 {compared}점)**: 명목상한 스킵 감소 "
            f"{reduced}/{compared}점 · 최대 동시 리스크 증가 {risk_up}/{compared}점 — "
            "cap-only는 밀림을 사서 동시 노출로 지불하는 팔이고, 그 교환비는 위 표의 "
            "수익/MDD로 판정한다."
        )

    # 3) 밀림의 부호 — both·oos_warm 명목상한 스킵의 가상 손익.
    signs = [
        r
        for r in opp_rows
        if r.scope == "both" and r.segment == SEGMENT_OOS_WARM and r.reason == "notional"
    ]
    if signs:
        total = sum(r.hypo_total_r for r in signs)
        by_mult = " · ".join(f"{r.multiple:g}배 {r.hypo_total_r:+.1f}R" for r in signs)
        word = "놓친 수익(밀림 = 손해)" if total > 0 else "안전장치(밀림 = 보호)"
        lines.append(
            f"**밀림 기회비용(both·oos_warm, 명목상한 스킵)**: 배수별 가상 net R 합 {by_mult} "
            f"— 부호 판정은 **{word}** 쪽이다. ⚠️ 격리 상한 값이라 방향만 읽을 것."
        )
    return lines


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[BookRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookRow.model_fields))


def grid_from_csv(path: Path) -> list[BookRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [BookRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def opp_to_frame(rows: Sequence[OppRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(OppRow.model_fields))


def opp_from_csv(path: Path) -> list[OppRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [OppRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def monthly_to_frame(rows: Sequence[MonthlyRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(MonthlyRow.model_fields))


def monthly_from_csv(path: Path) -> list[MonthlyRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [MonthlyRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _mode_label(mode: str) -> str:
    return "A 결합" if mode == "combined" else "B cap-only"


def _perf_table(rows: Sequence[BookRow], scope: str, segment: str) -> list[str]:
    lines = [
        "| 팔 | 배수 | 수익률 | MDD | 수익/MDD | 최대동시리스크 | 최대명목/자본 | 청산 "
        "| 거래 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    iso = _one(rows, scope=scope, arm="isolated", segment=segment)
    lines.append(
        f"| 격리(현행) | — | {_pct(iso.total_return)} | {_pct(iso.max_drawdown)} | "
        f"{_rr(iso.return_over_mdd)} | — | — | — | {iso.num_trades} | — |"
    )
    for mode in MODES:
        for row in pick(rows, scope=scope, arm="book", segment=segment, leverage_mode=mode):
            gate = "" if row.sample_ok else " ⚠️"
            lines.append(
                f"| {_mode_label(mode)} | {row.multiple:g} | {_pct(row.total_return)} | "
                f"{_pct(row.max_drawdown)} | {_rr(row.return_over_mdd)} | "
                f"{_pct(row.max_concurrent_risk or 0.0)} | "
                f"{(row.max_open_notional_ratio or 0.0):.2f} | {row.liquidation_events} | "
                f"{row.num_trades}{gate} | {row.peak_concurrency} |"
            )
    return lines


def _skip_table(rows: Sequence[BookRow], scope: str, segment: str) -> list[str]:
    """밀림 회계 — 9종목 두 팔 병기 + 같은 창 6종목(결합)과의 증가분."""
    lines = [
        "| 팔 | 배수 | 스킵(칸점유) | 스킵(명목상한) | 스킵(사이징) | 축소진입 | 최대칸 "
        "| 6종목 스킵(칸/명목) |",
        "| -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for mode in MODES:
        for row in pick(rows, scope=scope, arm="book", segment=segment, leverage_mode=mode):
            try:
                six = _one(
                    rows,
                    universe="six",
                    scope=scope,
                    arm="book",
                    segment=segment,
                    leverage_mode=mode,
                    multiple=row.multiple,
                )
                six_txt = f"{six.skipped_cell_busy} / {six.skipped_notional}"
            except ValueError:
                six_txt = "—"
            lines.append(
                f"| {_mode_label(mode)} | {row.multiple:g} | {row.skipped_cell_busy} | "
                f"{row.skipped_notional} | {row.skipped_sizing} | {row.clamped_entries} | "
                f"{row.peak_concurrency} | {six_txt} |"
            )
    return lines


def _opp_table(opp_rows: Sequence[OppRow], scope: str, segment: str) -> list[str]:
    lines = [
        "| 배수 | 사유 | 밀린 수 | 값매김 | 가상 합 R | 가상 평균 R | 가상 승률 "
        "| 가상 수익 기여 | 실현 거래 | 실현 합 R | 실현 승률 |",
        "| --: | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for row in sorted(
        (r for r in opp_rows if r.scope == scope and r.segment == segment),
        key=lambda r: (r.multiple, r.reason),
    ):
        lines.append(
            f"| {row.multiple:g} | {row.reason} | {row.count} | {row.priced} | "
            f"{row.hypo_total_r:+.1f} | {row.hypo_mean_r:+.3f} | {_pct(row.hypo_win_rate)} | "
            f"{_pct(row.hypo_return_sum)} | {row.actual_trades} | {row.actual_total_r:+.1f} | "
            f"{_pct(row.actual_win_rate)} |"
        )
    return lines


def _budget_section(rows: Sequence[BookRow]) -> list[str]:
    lines: list[str] = []
    for mode in MODES:
        under = budget_rows(rows, mode)
        lines += [
            f"### 팔 {_mode_label(mode).split(' ', 1)[1]} — 차가움 MDD ≤ 30% 구성(수익 내림차순)",
            "",
            "| 스코프 | 배수 | 차가움 수익 | 차가움 MDD | 따뜻 수익 | 따뜻 MDD | 수익/MDD(차) |",
            "| -- | --: | --: | --: | --: | --: | --: |",
        ]
        for row in under:
            warm = _one(
                rows,
                scope=row.scope,
                arm="book",
                segment=SEGMENT_OOS_WARM,
                leverage_mode=mode,
                multiple=row.multiple,
            )
            lines.append(
                f"| {row.scope} | {row.multiple:g} | {_pct(row.total_return)} | "
                f"{_pct(row.max_drawdown)} | {_pct(warm.total_return)} | "
                f"{_pct(warm.max_drawdown)} | {_rr(row.return_over_mdd)} |"
            )
        if not under:
            lines.append("| (없음) | — | — | — | — | — | — |")
        lines.append("")
    lines += [
        "### 추천 4조합(WAN-169 6종목·3년 표) — 9종목·6년에서의 재확인",
        "",
        "| 구성 | 따뜻 수익 | 따뜻 MDD | 차가움 수익 | 차가움 MDD | 30% 통과(차가움) |",
        "| -- | --: | --: | --: | --: | -- |",
    ]
    for scope, multiple in RECOMMENDED_COMBOS:
        try:
            warm = _one(
                rows,
                scope=scope,
                arm="book",
                segment=SEGMENT_OOS_WARM,
                leverage_mode="combined",
                multiple=multiple,
            )
            cold = _one(
                rows,
                scope=scope,
                arm="book",
                segment=SEGMENT_OOS,
                leverage_mode="combined",
                multiple=multiple,
            )
        except ValueError:
            lines.append(f"| {scope} {multiple:g}배 | — | — | — | — | (부분 실행 — 행 없음) |")
            continue
        ok = "✅" if cold.max_drawdown <= MDD_BUDGET else "❌"
        lines.append(
            f"| {scope} {multiple:g}배 | {_pct(warm.total_return)} | {_pct(warm.max_drawdown)} | "
            f"{_pct(cold.total_return)} | {_pct(cold.max_drawdown)} | {ok} |"
        )
    lines += [
        "",
        "⚠️ WAN-169 표의 절대 수치와 직접 비교 금지 — 그쪽은 6종목·3년 창(2023-07-14~"
        "2026-07-15)이고 이 표는 9종목·6년 창이다. 6→9의 몫만 가르려면 같은 창의 "
        "`universe=six` 행(§3 표 오른쪽 열)과 비교할 것.",
    ]
    return lines


def _loo_lines(rows: Sequence[BookRow], scope: str, segment: str, mode: str) -> list[str]:
    symbols = sorted({r.exclude_symbol for r in rows if r.exclude_symbol})
    lines = [
        "| 배수 | 전체 | " + " | ".join(f"−{s}" for s in symbols) + " |",
        "| --: | --: | " + " | ".join("--:" for _ in symbols) + " |",
    ]
    for multiple in MULTIPLES:
        base = _one(
            rows, scope=scope, arm="book", segment=segment, leverage_mode=mode, multiple=multiple
        )
        cells = [
            _one(
                rows,
                scope=scope,
                arm="book",
                segment=segment,
                leverage_mode=mode,
                multiple=multiple,
                exclude=s,
            ).total_return
            for s in symbols
        ]
        lines.append(
            f"| {multiple:g} | {_pct(base.total_return)} | "
            + " | ".join(_pct(v) for v in cells)
            + " |"
        )
    return lines


def _monthly_stats_table(monthly_rows: Sequence[MonthlyRow], scope: str, segment: str) -> list[str]:
    """모드 × 배수별 월별 수익률 통계 — 결정에 실질적인 열(플러스 월 비율 · 최악 월)."""
    lines = [
        "| 팔 | 배수 | 개월 | 플러스 월 | 평균 월수익 | 중앙값 | 최악 월 | 최고 월 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for mode in MODES:
        for multiple in MULTIPLES:
            rows = [
                r
                for r in monthly_rows
                if r.scope == scope
                and r.segment == segment
                and r.leverage_mode == mode
                and r.multiple == multiple
            ]
            if not rows:
                continue
            returns = sorted(r.monthly_return for r in rows)
            n = len(returns)
            plus = sum(1 for v in returns if v > 0)
            median = returns[n // 2] if n % 2 else (returns[n // 2 - 1] + returns[n // 2]) / 2.0
            worst = min(rows, key=lambda r: r.monthly_return)
            best = max(rows, key=lambda r: r.monthly_return)
            lines.append(
                f"| {_mode_label(mode)} | {multiple:g} | {n} | {plus}/{n} | "
                f"{_pct(sum(returns) / n)} | {_pct(median)} | "
                f"{_pct(worst.monthly_return)} ({worst.month}) | "
                f"{_pct(best.monthly_return)} ({best.month}) |"
            )
    return lines


def _monthly_detail_table(
    monthly_rows: Sequence[MonthlyRow],
    scope: str,
    segment: str,
    configs: Sequence[tuple[str, float]],
) -> list[str]:
    """헤드라인 구성의 월별 수익률 나열 — 달력월 행, 구성 열."""
    per_config: dict[tuple[str, float], dict[str, MonthlyRow]] = {}
    months: set[str] = set()
    for mode, multiple in configs:
        rows = {
            r.month: r
            for r in monthly_rows
            if r.scope == scope
            and r.segment == segment
            and r.leverage_mode == mode
            and r.multiple == multiple
        }
        per_config[(mode, multiple)] = rows
        months.update(rows)
    if not months:
        return ["(부분 실행 — 월별 행 없음)"]
    header = " | ".join(f"{_mode_label(m)} {mult:g}배" for m, mult in configs)
    lines = [
        f"| 월 | {header} |",
        "| -- | " + " | ".join("--:" for _ in configs) + " |",
    ]
    for month in sorted(months):
        cells = []
        for key in configs:
            row = per_config[key].get(month)
            cells.append(_pct(row.monthly_return) if row else "0.00%")
        lines.append(f"| {month} | " + " | ".join(cells) + " |")
    return lines


def build_summary_markdown(
    cell_rows: Sequence[CellRow],
    book_rows: Sequence[BookRow],
    opp_rows: Sequence[OppRow],
    monthly_rows: Sequence[MonthlyRow] = (),
    *,
    cells_csv: Path,
    grid_csv: Path,
    opp_csv: Path,
    funding_note: str = "",
) -> str:
    verify_line, _ = verify_cells(cell_rows)
    present = {r.scope for r in book_rows if r.universe == "nine"}
    main_scope = "both" if "both" in present else next(iter(sorted(present)))
    lines = [
        "# WAN-180 — 레버리지 북 9종목 재측정: 결합 vs cap-only + 밀림 회계·기회비용",
        "",
        "**성격** 측정 + 옵트인 엔진(cap-only) 전용 — **기본값·토대·사이징 기본값 불변**"
        "(`ALPHABLOCK_LIVE_TRADING=false` 유지). 진입 단위 = (종목, TF) 칸 · 칸당 1포지션 · "
        "한 지갑 공유(WAN-169 사용자 정의). **팔 A = 결합**(매 거래 사이징 N배, WAN-169 방식) "
        "· **팔 B = cap-only**(북 명목 상한만 N배 — 같은 크기 포지션을 더 많이 동시에, "
        "WAN-180 신설 옵트인). 렌즈 `baseline` 단독(WAN-128) · 오늘의 채택 엔진 그대로"
        f"(옛 핀 없음) · 못 박은 창 **{NEW_START} ~ {NEW_END}**(WAN-175/176 확정 6년) · "
        "9종목(기존 6 + DOGE·LINK·LTC).",
        "",
        "**구간** `oos_warm`(따뜻한 연속 OOS, 주 수치) + `oos`(차가운 절단, 스트레스) 병기 — "
        "WAN-166 정본 규약 · straddle 회계 (b).",
        "",
        *([f"📌 {funding_note}", ""] if funding_note else []),
        f"재현: `uv run python -m backtest.wan180_leverage_book_nine --jobs 6` "
        f"(요약만: `--from-csv`). 원자료: `{cells_csv}`(칸 격리·검산) · `{grid_csv}`(격자) · "
        f"`{opp_csv}`(기회비용).",
        "",
        "## 0. 검산 — 이 배선이 채택 엔진과 같은 수를 내는가",
        "",
        verify_line,
        "",
        "cap-only 엔진의 동작(상한만 N배 · 거래 크기 1배 불변 · 스킵 감소 · 동시 열림 증가)과 "
        "결합 모드의 기존 CSV 비트 재현은 `tests/test_leverage_book.py`가 고정한다.",
        "",
        f"## 1. 팔 A·B 배수별 수익/MDD — {main_scope}"
        + ("(9종목 × 2TF = 18칸)" if main_scope == "both" else "(부분 실행)"),
        "",
        "### oos_warm (주 수치)",
        "",
        *_perf_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "### oos (차가운 스트레스)",
        "",
        *_perf_table(book_rows, main_scope, SEGMENT_OOS),
        "",
    ]
    for tf in [t for t in MAIN_TIMEFRAMES if t in present and t != main_scope]:
        lines += [
            f"## 2. TF 단면 — {tf}(9칸)",
            "",
            "### oos_warm (주)",
            "",
            *_perf_table(book_rows, tf, SEGMENT_OOS_WARM),
            "",
            "### oos (스트레스)",
            "",
            *_perf_table(book_rows, tf, SEGMENT_OOS),
            "",
        ]
    lines += [
        "🚨 **북의 수익률은 수백~수천 거래의 복리 값이다 — 달성 가능 성과로 인용 금지**"
        "(WAN-169 경고 그대로). 결정에 실질적인 열은 절대 수익이 아니라 **MDD · 최대 동시 "
        "리스크 · 청산 트리거**다. 격리(현행) 행은 칸 평균이라 북 행과 자가 다르다.",
        "",
        f"## 3. 밀림(스킵) 회계 — {main_scope}",
        "",
        "### oos_warm",
        "",
        *_skip_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "### oos",
        "",
        *_skip_table(book_rows, main_scope, SEGMENT_OOS),
        "",
        "오른쪽 열이 **같은 창 6종목**의 스킵(칸점유/명목상한)이다 — 6→9 증가분은 이 열과의 "
        "차이로 읽는다(다른 창의 WAN-169 표와 비교 금지). 팔 A에서 스킵이 배수에 거의 "
        "불변인 것은 결합 모델의 성질(상대 여유 불변)이고, 팔 B의 감소분이 cap-only가 사는 "
        "것이다.",
        "",
        f"## 4. 밀림 기회비용 (팔 A) — {main_scope}",
        "",
        "### oos_warm (주)",
        "",
        *_opp_table(opp_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "### oos (스트레스)",
        "",
        *_opp_table(opp_rows, main_scope, SEGMENT_OOS),
        "",
        "⚠️ **가상 열은 격리 상한(upper bound)이다** — 밀린 셋업을 다 넣었다면 그것들끼리 또 "
        "밀어냈을 상호작용을 무시했고, 가상 체결도 `baseline`(낙관) 위의 값이다. **부호로만** "
        "「놓친 수익 vs 안전장치」를 판정하고 크기로 인용하지 말 것.",
        "",
        "## 5. MDD 30% 예산 표 (차가움 기준)",
        "",
        *_budget_section(book_rows),
        "",
        "## 6. leave-one-out — 종목 편중 (9종목)",
        "",
        f"### 팔 A 결합 · {main_scope} · oos_warm",
        "",
        *_loo_lines(book_rows, main_scope, SEGMENT_OOS_WARM, "combined"),
        "",
        f"### 팔 B cap-only · {main_scope} · oos_warm",
        "",
        *_loo_lines(book_rows, main_scope, SEGMENT_OOS_WARM, "cap_only"),
        "",
        f"## 7. 월별 수익률 — {main_scope} (UTC 달력월 · 청산 시각 복리)",
        "",
        "### 통계 — oos_warm (주)",
        "",
        *_monthly_stats_table(monthly_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "### 통계 — oos (스트레스)",
        "",
        *_monthly_stats_table(monthly_rows, main_scope, SEGMENT_OOS),
        "",
        "### 월별 나열 — 예산 헤드라인 구성 · oos_warm",
        "",
        *_monthly_detail_table(
            monthly_rows,
            main_scope,
            SEGMENT_OOS_WARM,
            [("combined", 1.0), ("combined", 3.0), ("cap_only", 5.0)],
        ),
        "",
        "⚠️ 청산이 없던 달은 행이 없어 0%로 접힌다(월별 나열에선 0.00%로 표기). 높은 배수의 "
        "월 수익률 절대값은 복리 값이라 인용 금지 — 결정에 실질적인 열은 **플러스 월 비율과 "
        "최악 월**(월간 낙폭)이다. 원자료(전 스코프 × 팔 × 배수 × 구간)는 "
        "`wan180_monthly_returns.csv`.",
        "",
        "## 판정",
        "",
        *verdict(book_rows, opp_rows),
        "",
        "⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/176)은 이 표로 뒤집히지 않는다** — "
        "레버리지·cap-only는 위험의 모양만 바꾸지 알파를 만들지 않는다(WAN-90 계열). 수익/"
        "MDD의 배수-단조 상승은 상당 부분 복리 산수다(WAN-169 — 「N배가 안전」으로 읽지 말 "
        "것). 배수·북·cap-only를 기본값으로 올리는 것은 재-베이스라인 = **사용자 결정**이다.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-180 레버리지 북 9종목 재측정")
    parser.add_argument("--symbols", type=str, default=",".join(NINE_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(MAIN_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=NEW_START)
    parser.add_argument("--end", type=str, default=NEW_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 칸 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-grid", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--out-opp", type=Path, default=DEFAULT_OPP_CSV)
    parser.add_argument("--out-monthly", type=Path, default=DEFAULT_MONTHLY_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--out-meta", type=Path, default=DEFAULT_META_JSON)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    parser.add_argument(
        "--no-funding-proxy",
        action="store_true",
        help="신규 종목 펀딩 대리(사용자 지시 2026-07-23)를 끈다 — 펀딩 공백을 0으로 둔다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_grid = Path(args.out_grid)
    out_opp = Path(args.out_opp)
    out_monthly = Path(args.out_monthly)
    out_md = Path(args.out_md)
    out_meta = Path(args.out_meta)

    if args.from_csv:
        cell_rows = cells_from_csv(out_cells)
        book_rows = grid_from_csv(out_grid)
        opp_rows = opp_from_csv(out_opp)
        monthly_rows = monthly_from_csv(out_monthly) if out_monthly.exists() else []
        funding_note = ""
        if out_meta.exists():
            funding_note = str(
                json.loads(out_meta.read_text(encoding="utf-8")).get("funding_note", "")
            )
        print(
            f"[wan180] CSV 로드 — 칸 {len(cell_rows)}행 · 격자 {len(book_rows)}행 · "
            f"기회비용 {len(opp_rows)}행 · 월별 {len(monthly_rows)}행 (재실행 없음)"
        )
    else:
        payloads = run_cells(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        funding_note = ""
        if not args.no_funding_proxy:
            payloads, funding_note = apply_funding_proxy(payloads)
            if funding_note:
                print(f"[wan180] {funding_note}")
        cell_rows = [row for p in payloads for row in p.rows]
        book_rows, opp_rows, monthly_rows = build_rows(payloads)
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(cell_rows).to_csv(out_cells, index=False)
        grid_to_frame(book_rows).to_csv(out_grid, index=False)
        opp_to_frame(opp_rows).to_csv(out_opp, index=False)
        monthly_to_frame(monthly_rows).to_csv(out_monthly, index=False)
        out_meta.write_text(
            json.dumps({"funding_note": funding_note}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[wan180] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan180] 격자 {len(book_rows)}행 → {out_grid}")
        print(f"[wan180] 기회비용 {len(opp_rows)}행 → {out_opp}")
        print(f"[wan180] 월별 {len(monthly_rows)}행 → {out_monthly}")

    verify_line, worst = verify_cells(cell_rows)
    print(f"[wan180] 검산: {verify_line}")
    if not math.isfinite(worst) or worst >= 1e-12:
        print("[wan180] 🚨 검산 실패 — 요약을 내기 전에 배선을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            cell_rows,
            book_rows,
            opp_rows,
            monthly_rows,
            cells_csv=out_cells,
            grid_csv=out_grid,
            opp_csv=out_opp,
            funding_note=funding_note,
        ),
        encoding="utf-8",
    )
    print(f"[wan180] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
