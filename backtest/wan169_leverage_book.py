"""WAN-169 — 타임프레임·종목 가로지르는 레버리지 북의 손익·위험 측정.

사용자 정의(2026-07-22): 진입 단위 = **(종목, TF) 칸**, 칸 안에서는 청산 전 1포지션,
여러 칸이 **한 지갑(공유 자본)** 을 나눠 쓰며, 레버리지 N배 = **매 거래 사이징 N배**
(리스크 1% → N%, 원문: *"한번의 진입이 원래 1%였다면 3배일때는 3% 이런식으로"*).
엔진은 `backtest.leverage_book`(§1·§2)이고, 이 모듈은 그 위의 측정 격자(§3)다.

## 격자

* **팔**: 격리(현행 — 각 칸 독립 자본, 채택 단일 포지션 엔진 그대로) vs **공유 자본 북**.
* **사이징**: `risk_pct`(현행, 리스크 1%×N) vs `fixed_notional`(시드 분할 — 명목 =
  자본 × N/칸수, WAN-108 2안의 오늘 엔진 판. ⚠️ 옛 WAN-108의 "2안이 진다"는 옛 엔진
  값이라 결론으로 재인용 금지 — 이 표가 처음 잰다).
* **배수**: 1 · 2 · 3 · 5 (1배 = 채택 사이징 그대로에 자본 공유만 얹은 기준점).
* **스코프**: 15m(6칸) · 1h(6칸) · both(12칸 — 사용자 정의의 실제 북).
* **구간**: full · is · **oos_warm(주 수치)** · oos(스트레스) — WAN-166 정본 규약.
  straddle 회계 = **(b) 배치 안 함**(사용자 결정, `docs/decisions/wan169.md`).
* leave-one-out(종목 편중) · 20건 게이트 · 렌즈 `baseline` 단독 · 못 박은 창.

## 판정 열 (사용자 지시 2026-07-22)

원수익 단독이 아니라 **위험조정**으로 판정한다: `total_return` · MDD · **수익/MDD(주)** ·
**통합 최대 동시 리스크**(전 포지션 동시 손절 시 공유 자본 대비 % — WAN-108이 1안 12%
vs 2안 55.7%로 가른 지표의 오늘 엔진 초측정) · **청산 트리거 건수**(최악 가정, WAN-103
결정 4 — 이 모델에선 필수 열).

## 재현

```
uv run python -m backtest.wan169_leverage_book --jobs 6
uv run python -m backtest.wan169_leverage_book --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.harness import (
    IS_FRACTION,
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    WARM_OOS_SEGMENT,
    Segment,
)
from backtest.leverage_book import BookCell, LeverageBookParams, run_leverage_book
from backtest.models import BacktestConfig
from backtest.run import parse_date_ms
from backtest.wan167_position_census import ALL_SYMBOLS, MAIN_TIMEFRAMES
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from data.models import FundingRate

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan169_leverage_book_cells.csv"
DEFAULT_GRID_CSV = REPORTS_DIR / "wan169_leverage_book_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan169_leverage_book_summary.md"

#: 못 박은 창 — WAN-111/114/145/164/167과 동일(`--years N`은 미끄러진다).
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 스윕 배수(사용자 확정 2026-07-22). 1배가 기준점이다.
MULTIPLES: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)

SIZING_MODES: tuple[str, ...] = ("risk_pct", "fixed_notional")

SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 셀은 판정에서 뺀다 — WAN-143 게이트와 같은 이유.
MIN_TRADES = 20

IS_SEGMENT = Segment(name=SEGMENT_IS, window=0, start_fraction=0.0, end_fraction=IS_FRACTION)
OOS_SEGMENT = Segment(name=SEGMENT_OOS, window=0, start_fraction=IS_FRACTION, end_fraction=1.0)

#: 북 자본곡선 지표의 연율화 앵커. 북은 거래 단위 곡선이라 Sharpe를 판정에 쓰지 않으며
#: (판정 열은 수익/MDD), 이 값은 `build_result_from_trades` 인자를 채우는 용도다.
BOOK_ANNUALIZATION_TF = "1h"


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CellRow(BaseModel):
    """칸 하나 × 구간 하나의 격리(현행) 성과 — 북 대조의 원자료이자 검산 대상.

    `engine_*` 열(full 구간만)은 같은 입력을 표준 경로(`harness.run_once`)로 다시 돌린
    값이다 — 이 모듈의 후보 생성·시퀀싱 배선이 채택 엔진과 같은 숫자를 내는지의 검산
    (WAN-164 패턴). 나머지 구간은 그 검산된 배선을 재사용하므로 따로 재지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    num_candidates: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    engine_total_return: float | None = None
    engine_num_trades: int | None = None

    @field_validator("engine_total_return", "engine_num_trades", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """CSV 왕복의 빈 칸(`""`/NaN)을 `None`으로 — `RunRow`와 같은 함정 방지(WAN-130)."""
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value


class BookRow(BaseModel):
    """격자 한 셀 — (스코프 × 팔 × 사이징 × 배수 × 구간 × 제외 종목)의 성과·위험."""

    model_config = ConfigDict(frozen=True)

    scope: str
    """칸 집합: `15m`(6칸) · `1h`(6칸) · `both`(12칸 = 사용자 정의의 실제 북)."""
    arm: str
    """`isolated`(격리 현행 — 칸 평균) · `book`(공유 자본 북)."""
    sizing_mode: str
    multiple: float
    segment: str
    exclude_symbol: str = ""
    """leave-one-out 축 — 빈 문자열이면 전 종목."""
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

    @field_validator(
        "peak_concurrency",
        "max_concurrent_risk",
        "max_open_notional_ratio",
        "liquidation_events",
        "clamped_entries",
        "skipped_cell_busy",
        "skipped_notional",
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
        """수익/MDD(주 판정 지표). MDD가 0이면 정의하지 않는다."""
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


# --------------------------------------------------------------------------- #
# 칸 실행 (무거운 fan-out 단위 — 워커가 자기 데이터를 자기가 로드한다)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class CellPayload:
    """칸 하나의 산출물 — 구간별 후보(북의 입력) + 격리 성과 행 + 따뜻한 경계."""

    symbol: str
    timeframe: str
    boundary_ms: int
    """따뜻한 평가 경계(WAN-166 `eval_boundary_ms` — 이 칸의 전체 창 앵커 기준).

    칸마다 마지막 봉 시각이 달라 경계가 칸 사이 1봉 미만으로 어긋날 수 있다 — 북은
    칸별 경계로 후보를 거른다(각 칸의 따뜻한 평가 창 = 그 칸의 차가운 OOS 창, WAN-166
    보장을 칸 단위로 보존)."""
    candidates: dict[str, tuple[_Candidate, ...]]
    """구간(`full`/`is`/`oos`) → 후보. `oos_warm`은 `full`을 경계로 걸러 만든다."""
    funding: dict[str, tuple[FundingRate, ...]]
    rows: tuple[CellRow, ...]


def _isolated_metrics(
    candidates: Sequence[_Candidate],
    cfg: BacktestConfig,
    timeframe: str,
    rates: Sequence[FundingRate],
) -> tuple[int, float, float, float]:
    """격리(단일 포지션) 성과 — (거래수, 승률, 수익률, MDD)."""
    trades = [t for _, t in sequence_with_candidates(list(candidates), cfg, rates)]
    result = build_result_from_trades(trades, cfg, timeframe)
    m = result.metrics
    return m.num_trades, m.win_rate, m.total_return, m.max_drawdown


def run_cell(task: _Task, *, log: bool = True) -> CellPayload:
    """한 칸의 구간별 후보·격리 성과·검산을 낸다 — 채택 기본값 그대로(옛 핀 없음).

    후보 생성이 이 리포트의 유일한 무거운 연산이다. `full` 후보는 따뜻한 구간이
    재사용하고(경계 필터만), 차가운 `is`/`oos`는 잘린 창에서 탐지부터 다시 한다
    (존 재고 0에서 시작 — `harness.slice_market` 규약 그대로).
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms, need_1m=True
    )
    if market.empty or market.df_1m.empty:
        raise ValueError(f"{task.symbol} {task.timeframe}: 데이터가 없습니다(창 확인).")
    params = harness.build_params()  # 인자 없음 = 채택 기본값(옛 핀 물려받기 금지 — 완료기준).
    cfg = harness.build_config(task.timeframe)

    candidates: dict[str, tuple[_Candidate, ...]] = {}
    funding: dict[str, tuple[FundingRate, ...]] = {}
    rows: list[CellRow] = []

    boundary = harness.eval_boundary_ms(market, WARM_OOS_SEGMENT)
    assert boundary is not None  # WARM_OOS_SEGMENT는 평가 경계를 항상 가진다.

    for segment_name, segment in (
        (SEGMENT_FULL, None),
        (SEGMENT_IS, IS_SEGMENT),
        (SEGMENT_OOS, OOS_SEGMENT),
    ):
        window = market if segment is None else harness.slice_market(market, segment)
        ob_result = harness.detect_order_blocks(window)
        cands, _stats = build_zone_limit_candidates(
            window.htf_df,
            window.df_1m,
            task.timeframe,
            params=params,
            cfg=cfg,
            order_block_result=ob_result,
        )
        candidates[segment_name] = tuple(cands)
        funding[segment_name] = tuple(window.funding_rates)

        num_trades, win_rate, total_return, mdd = _isolated_metrics(
            cands, cfg, task.timeframe, window.funding_rates
        )
        engine_return: float | None = None
        engine_trades: int | None = None
        if segment is None:
            # 검산(WAN-164 패턴): 같은 창을 표준 경로로 다시 돌려 배선 실수를 비트로 잡는다.
            outcome = harness.run_once(window, params=params, cfg=cfg, order_block_result=ob_result)
            engine_return = outcome.result.metrics.total_return
            engine_trades = outcome.result.metrics.num_trades
        rows.append(
            CellRow(
                symbol=task.symbol,
                timeframe=task.timeframe,
                segment=segment_name,
                num_candidates=len(cands),
                num_trades=num_trades,
                win_rate=win_rate,
                total_return=total_return,
                max_drawdown=mdd,
                engine_total_return=engine_return,
                engine_num_trades=engine_trades,
            )
        )

    # 따뜻한 구간(oos_warm): 전 창 후보를 경계로 걸러(straddle (b) — 워밍업 셋업은 배치조차
    # 안 함) 신선한 초기자본으로 격리 시퀀싱 — `run_zone_limit_backtest_verbose(eval_from_ms=)`
    # 와 같은 규약이다.
    warm_cands = tuple(c for c in candidates[SEGMENT_FULL] if c.trigger_time >= boundary)
    num_trades, win_rate, total_return, mdd = _isolated_metrics(
        warm_cands, cfg, task.timeframe, funding[SEGMENT_FULL]
    )
    rows.append(
        CellRow(
            symbol=task.symbol,
            timeframe=task.timeframe,
            segment=SEGMENT_OOS_WARM,
            num_candidates=len(warm_cands),
            num_trades=num_trades,
            win_rate=win_rate,
            total_return=total_return,
            max_drawdown=mdd,
        )
    )
    if log:
        full_row = rows[0]
        print(
            f"[wan169] {task.symbol} {task.timeframe}: full 후보 {full_row.num_candidates} · "
            f"거래 {full_row.num_trades} · 수익 {full_row.total_return * 100:.2f}%",
            flush=True,
        )
    return CellPayload(
        symbol=task.symbol,
        timeframe=task.timeframe,
        boundary_ms=boundary,
        candidates=candidates,
        funding=funding,
        rows=tuple(rows),
    )


def _run_task_logged(task: _Task) -> CellPayload:
    return run_cell(task, log=True)


def run_cells(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int = 1,
) -> list[CellPayload]:
    """전 칸을 돈다. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [run_cell(task) for task in tasks]
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        return list(executor.map(_run_task_logged, tasks))


# --------------------------------------------------------------------------- #
# 북 격자 (가벼운 시퀀싱 — 후보 재사용)
# --------------------------------------------------------------------------- #


def _segment_cells(
    payloads: Sequence[CellPayload], segment: str, exclude_symbol: str
) -> list[BookCell]:
    """이 구간의 북 입력 칸들. `oos_warm`은 full 후보를 칸별 경계로 거른다(straddle (b))."""
    cells: list[BookCell] = []
    for payload in payloads:
        if exclude_symbol and _short(payload.symbol) == exclude_symbol:
            continue
        if segment == SEGMENT_OOS_WARM:
            cands: Sequence[_Candidate] = [
                c for c in payload.candidates[SEGMENT_FULL] if c.trigger_time >= payload.boundary_ms
            ]
            rates = payload.funding[SEGMENT_FULL]
        else:
            cands = list(payload.candidates[segment])
            rates = payload.funding[segment]
        cells.append(
            BookCell(
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                candidates=cands,
                funding_rates=rates,
            )
        )
    return cells


def _book_config(base_cfg: BacktestConfig, sizing_mode: str, scope_cells: int) -> BacktestConfig:
    """팔의 사이징 모드를 실은 설정. 배수는 북(`apply_book_leverage`)이 얹는다.

    `fixed_notional`의 분수는 **시드 분할** = `1/스코프 칸수`다(사용자의 「1/N 시드」를
    칸 축으로 옮긴 것) — 12칸이 전부 열리면 총 명목이 자본 × N이 되어 `risk_pct` 팔과
    같은 천장을 쓴다. leave-one-out 행도 이 분수를 **바꾸지 않는다**(같은 전략에서
    종목의 거래만 빼는 것이지 사이징을 다시 고르는 게 아니다).
    """
    if sizing_mode == "risk_pct":
        return base_cfg
    assert base_cfg.risk_sizing is not None
    sizing = base_cfg.risk_sizing.model_copy(
        update={"sizing_mode": "fixed_notional", "notional_fraction": 1.0 / scope_cells}
    )
    return base_cfg.model_copy(update={"risk_sizing": sizing})


def _scope_payloads(payloads: Sequence[CellPayload], scope: str) -> list[CellPayload]:
    if scope == "both":
        return list(payloads)
    return [p for p in payloads if p.timeframe == scope]


def build_book_rows(payloads: Sequence[CellPayload]) -> list[BookRow]:
    """격자 전체의 북 행 + 격리(현행) 대조 행."""
    scopes = [*MAIN_TIMEFRAMES, "both"]
    symbols = sorted({_short(p.symbol) for p in payloads})
    cell_rows_by_key = {
        (row.symbol, row.timeframe, row.segment): row for p in payloads for row in p.rows
    }
    rows: list[BookRow] = []
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)

    for scope in scopes:
        scoped = _scope_payloads(payloads, scope)
        if not scoped:
            continue  # 이 TF의 칸이 없다(부분 실행) — 빈 스코프는 행을 내지 않는다.
        if scope == "both" and len({p.timeframe for p in scoped}) < 2:
            continue  # TF가 하나뿐이면 both는 그 TF 스코프의 복제라 내지 않는다.
        scope_cells = len(scoped)
        for segment in SEGMENTS:
            for exclude in ["", *symbols]:
                kept = [p for p in scoped if not exclude or _short(p.symbol) != exclude]
                # 격리(현행) 대조 행 — 칸 평균(이 저장소의 심볼평균 관행을 칸 축으로).
                iso = [cell_rows_by_key[(p.symbol, p.timeframe, segment)] for p in kept]
                rows.append(
                    BookRow(
                        scope=scope,
                        arm="isolated",
                        sizing_mode="risk_pct",
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
                for sizing_mode in SIZING_MODES:
                    cfg = _book_config(base_cfg, sizing_mode, scope_cells)
                    cells = _segment_cells(scoped, segment, exclude)
                    for multiple in MULTIPLES:
                        outcome = run_leverage_book(
                            cells, cfg, LeverageBookParams(leverage_multiple=multiple)
                        )
                        result = build_result_from_trades(
                            outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
                        )
                        m = result.metrics
                        stats = outcome.stats
                        rows.append(
                            BookRow(
                                scope=scope,
                                arm="book",
                                sizing_mode=sizing_mode,
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
                            )
                        )
    return rows


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def verify_cells(rows: Sequence[CellRow]) -> tuple[str, float]:
    """full 구간 격리 행 ↔ 표준 경로(`harness.run_once`) 대조 — (문장, 최대 절대차).

    코드가 일치·잡음·불일치를 다르게 찍는다(WAN-151/161 패턴 — 조용한 통과 금지).
    """
    diffs: list[float] = []
    for row in rows:
        if row.segment != SEGMENT_FULL or row.engine_total_return is None:
            continue
        diffs.append(abs(row.total_return - row.engine_total_return))
        if row.engine_num_trades is not None and row.engine_num_trades != row.num_trades:
            return (
                f"🚨 **불일치** — {row.symbol} {row.timeframe} full 거래 수가 표준 경로와 "
                f"다릅니다({row.num_trades} vs {row.engine_num_trades}). 배선 오류다.",
                float("inf"),
            )
    if not diffs:
        return ("🚨 **검산 불가** — full 구간 엔진 대조 값이 없습니다.", float("inf"))
    worst = max(diffs)
    if worst == 0.0:
        return (
            f"✅ **일치** — full 격리 {len(diffs)}칸의 `total_return`이 표준 경로"
            "(`harness.run_once`)와 **비트 단위로 같다**(최대 절대차 0.00e+00).",
            worst,
        )
    if worst < 1e-12:
        return (
            f"✅ 일치(부동소수 끝자리) — 최대 절대차 {worst:.2e} (< 1e-12), "
            f"{len(diffs)}칸 전부 표준 경로와 같은 수다.",
            worst,
        )
    return (
        f"🚨 **불일치** — full 격리 성과가 표준 경로와 최대 {worst:.2e} 차이. 배선 오류다.",
        worst,
    )


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def pick(
    rows: Sequence[BookRow],
    *,
    scope: str,
    arm: str,
    segment: str,
    sizing_mode: str = "risk_pct",
    multiple: float | None = None,
    exclude: str = "",
) -> list[BookRow]:
    out = [
        r
        for r in rows
        if r.scope == scope
        and r.arm == arm
        and r.segment == segment
        and r.sizing_mode == sizing_mode
        and r.exclude_symbol == exclude
        and (multiple is None or r.multiple == multiple)
    ]
    return sorted(out, key=lambda r: r.multiple)


def _one(
    rows: Sequence[BookRow],
    *,
    scope: str,
    arm: str,
    segment: str,
    sizing_mode: str = "risk_pct",
    multiple: float | None = None,
    exclude: str = "",
) -> BookRow:
    found = pick(
        rows,
        scope=scope,
        arm=arm,
        segment=segment,
        sizing_mode=sizing_mode,
        multiple=multiple,
        exclude=exclude,
    )
    if len(found) != 1:
        key = (scope, arm, segment, sizing_mode, multiple, exclude)
        raise ValueError(f"행이 정확히 1개여야 합니다: {key} → {len(found)}개")
    return found[0]


def verdict(rows: Sequence[BookRow]) -> str:
    """완료기준의 판정 문장 — 배수 N이 위험조정으로 「할 만한가」(사용자 지시 서식).

    자: 주 수치(`oos_warm`)와 스트레스(`oos`)에서, 각 스코프의 북(`risk_pct`)이 배수
    N>1로 **수익/MDD를 1배보다 올리는가**(청산 트리거 0 조건). 숫자는 전부 행에서
    계산한다 — 문장에 박으면 재실행 뒤 리포트가 거짓말을 한다(WAN-164 패턴).
    """
    present = {r.scope for r in rows}
    sub: list[tuple[str, str, bool, bool]] = []  # (scope, segment, improved, raw_up)
    for scope in [s for s in [*MAIN_TIMEFRAMES, "both"] if s in present]:
        for segment in (SEGMENT_OOS_WARM, SEGMENT_OOS):
            base = _one(rows, scope=scope, arm="book", segment=segment, multiple=1.0)
            others = [
                r
                for r in pick(rows, scope=scope, arm="book", segment=segment)
                if r.multiple > 1.0 and r.sample_ok
            ]
            if not others or not base.sample_ok:
                continue
            base_rr = base.return_over_mdd or 0.0
            improved = any(
                (r.return_over_mdd or 0.0) > base_rr and (r.liquidation_events or 0) == 0
                for r in others
            )
            raw_up = max(r.total_return for r in others) > base.total_return
            sub.append((scope, segment, improved, raw_up))
    if not sub:
        return "**판정 불가** — 표본 게이트(20건)를 넘는 셀이 없다."

    coord_scope = "both" if "both" in present else next(iter(sorted(present)))
    both_warm = _one(rows, scope=coord_scope, arm="book", segment=SEGMENT_OOS_WARM, multiple=1.0)
    best = max(
        (
            r
            for r in pick(rows, scope=coord_scope, arm="book", segment=SEGMENT_OOS_WARM)
            if r.multiple > 1.0
        ),
        key=lambda r: r.return_over_mdd or float("-inf"),
    )
    coords = (
        f"{coord_scope}·oos_warm 기준 배수 {best.multiple:g}에서 수익 "
        f"{(best.total_return - both_warm.total_return) * 100:+.2f}%p"
        f"({both_warm.total_return * 100:.2f}% → {best.total_return * 100:.2f}%), "
        f"최대 동시 리스크 {(best.max_concurrent_risk or 0.0) * 100:.2f}%, 수익/MDD "
        f"{both_warm.return_over_mdd or 0.0:.2f} → {best.return_over_mdd or 0.0:.2f}, "
        f"청산 트리거 {best.liquidation_events}건"
    )
    if all(improved for _, _, improved, _ in sub):
        return (
            f"**(a) 위험조정 개선 — 배수가 수익/MDD를 올린다.** {coords}. 스코프·구간 "
            "전부에서 어떤 배수 N>1이 1배의 수익/MDD를 청산 트리거 없이 이겼다."
        )
    if all(not improved for _, _, improved, _ in sub):
        raw = all(raw_up for _, _, _, raw_up in sub)
        tail = "원수익은 배수대로 커지지만" if raw else "원수익 우위조차 구간에 갈리고"
        return (
            f"**(b) 원수익만 개선 — 위험조정 우위는 없다.** {coords}. {tail} 수익/MDD는 "
            "어느 스코프·구간에서도 1배를 넘지 못했다 — 배수는 위험의 모양만 키운다."
        )
    split = " · ".join(
        f"{scope}/{segment}={'개선' if improved else '아님'}" for scope, segment, improved, _ in sub
    )
    return (
        f"**(c) 스코프·구간에 갈린다.** {coords}. 세부: {split}. 하나의 배수로 전부를 "
        "좋게 할 수 없다 — 채택은 이 갈림을 알고 내리는 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[CellRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CellRow.model_fields))


def grid_to_frame(rows: Sequence[BookRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookRow.model_fields))


def cells_from_csv(path: Path) -> list[CellRow]:
    # `keep_default_na=False`: `exclude_symbol=""` 같은 빈 문자열 축이 NaN으로 둔갑하지
    # 않게 한다. 선택 숫자 열의 빈 칸은 검증기가 `""` → None으로 되돌린다.
    frame = pd.read_csv(path, keep_default_na=False)
    return [CellRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def grid_from_csv(path: Path) -> list[BookRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [BookRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _book_table(rows: Sequence[BookRow], scope: str, segment: str, sizing_mode: str) -> list[str]:
    lines = [
        "| 팔 | 배수 | 수익률 | MDD | 수익/MDD | 최대동시리스크 | 최대명목/자본 "
        "| 청산 | 거래 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    iso = _one(rows, scope=scope, arm="isolated", segment=segment)
    lines.append(
        f"| 격리(현행) | — | {_pct(iso.total_return)} | {_pct(iso.max_drawdown)} | "
        f"{_rr(iso.return_over_mdd)} | — | — | — | {iso.num_trades} | — |"
    )
    for row in pick(rows, scope=scope, arm="book", segment=segment, sizing_mode=sizing_mode):
        gate = "" if row.sample_ok else " ⚠️"
        lines.append(
            f"| 북 | {row.multiple:g} | {_pct(row.total_return)} | {_pct(row.max_drawdown)} | "
            f"{_rr(row.return_over_mdd)} | {_pct(row.max_concurrent_risk or 0.0)} | "
            f"{(row.max_open_notional_ratio or 0.0):.2f} | {row.liquidation_events} | "
            f"{row.num_trades}{gate} | {row.peak_concurrency} |"
        )
    return lines


def _loo_lines(rows: Sequence[BookRow], scope: str, segment: str) -> list[str]:
    symbols = sorted({r.exclude_symbol for r in rows if r.exclude_symbol})
    lines = [
        "| 배수 | 전체 | " + " | ".join(f"−{s}" for s in symbols) + " |",
        "| --: | --: | " + " | ".join("--:" for _ in symbols) + " |",
    ]
    for multiple in MULTIPLES:
        base = _one(rows, scope=scope, arm="book", segment=segment, multiple=multiple)
        cells = [
            _one(
                rows,
                scope=scope,
                arm="book",
                segment=segment,
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


def _compounding_caveat(rows: Sequence[BookRow], scope: str) -> str:
    """수익/MDD가 배수에 대해 기계적으로 커지는 성질의 경고 — 숫자는 행에서 계산한다.

    거래당 기대값이 양(+)이면 총수익은 배수 N에 대해 지수적으로 커지는데 MDD는 100%로
    유계다 — 그래서 이 자(총수익/MDD)는 복리 구간이 길수록 N과 함께 **산수적으로** 오르는
    경향이 있다. (a)를 「높은 배수가 안전하다」로 읽으면 안 되는 이유를 판정 옆에 붙인다.
    """
    base = _one(rows, scope=scope, arm="book", segment=SEGMENT_OOS_WARM, multiple=MULTIPLES[0])
    top = _one(rows, scope=scope, arm="book", segment=SEGMENT_OOS_WARM, multiple=MULTIPLES[-1])
    top_cold = _one(rows, scope=scope, arm="book", segment=SEGMENT_OOS, multiple=MULTIPLES[-1])
    return (
        "🚨 **(a)를 「높은 배수가 안전하다」로 읽지 말 것 — 수익/MDD의 N-단조 상승은 상당"
        "부분 복리 산수다.** 거래당 기대값이 양(+)인 백테스트를 수백 번 복리로 돌리면 "
        "총수익은 N에 대해 지수적으로 커지고 MDD는 100%로 유계라, 총수익/MDD는 배수를 "
        "올릴수록 기계적으로 커지는 경향이 있다(그 자를 주 판정으로 정한 것은 사용자 지시고, "
        "이 경고는 그 자의 성질 기록이다). 결정에 실질적인 질문은 낙폭 그 자체다 — "
        f"{scope}·oos_warm에서 MDD가 1배 {_pct(base.max_drawdown)} → "
        f"{MULTIPLES[-1]:g}배 {_pct(top.max_drawdown)}"
        f"(차가운 oos는 {_pct(top_cold.max_drawdown)})로, **그 낙폭을 견딜 수 있는가**가 "
        "배수 선택의 실제 내용이다."
    )


def build_summary_markdown(
    cell_rows: Sequence[CellRow],
    book_rows: Sequence[BookRow],
    *,
    cells_csv: Path,
    grid_csv: Path,
) -> str:
    verify_line, _ = verify_cells(cell_rows)
    present = {r.scope for r in book_rows}
    main_scope = "both" if "both" in present else next(iter(sorted(present)))
    lines = [
        "# WAN-169 — 타임프레임·종목 가로지르는 레버리지 북: 손익·위험 측정",
        "",
        "**성격** 측정 전용(옵트인 엔진 `backtest.leverage_book` 위의 격자). 진입 단위 = "
        "**(종목, TF) 칸**, 칸 안 1포지션 · 칸 간 동시 허용 · 한 지갑 공유(사용자 정의 "
        "2026-07-22). **레버리지 N배 = 매 거래 사이징 N배**(리스크 1% → N% — cap-only가 "
        "아니다, 사용자 확정). 렌즈 `baseline` 단독(WAN-128) · 못 박은 창"
        f"({DEFAULT_START}~{DEFAULT_END}) · 채택 기본값 그대로(옛 핀 없음) · **기본값·토대·"
        "사이징 기본값 불변**(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "**구간** `oos_warm`(따뜻한 연속 OOS, **주 수치**) + `oos`(차가운 절단, 과최적화 "
        "스트레스) 병기 — WAN-166 정본 규약. **straddle 회계 = (b) 배치 안 함**(사용자 결정): "
        "워밍업에 탭이 나 평가 경계를 넘어 사는 포지션은 평가 초입의 칸·자본·레버리지 자리를 "
        "점유하지 않는다(`docs/decisions/wan169.md`).",
        "",
        f"재현: `uv run python -m backtest.wan169_leverage_book --jobs 6` (요약만: `--from-csv`). "
        f"원자료: `{cells_csv}`(칸별 격리 성과·검산) · `{grid_csv}`(북 격자).",
        "",
        "## 0. 검산 — 이 모듈의 배선이 채택 엔진과 같은 수를 내는가",
        "",
        verify_line,
        "",
        "추가로 칸 하나짜리 북 ≡ 채택 단일 포지션 시퀀서의 비트 일치는 "
        "`tests/test_leverage_book.py`가 동작으로 고정한다.",
        "",
        f"## 1. 본 판정 — {main_scope}"
        + ("(15m+1h 12칸 = 사용자 정의의 실제 북)" if main_scope == "both" else "(부분 실행)")
        + " × `risk_pct`(현행 사이징 × N)",
        "",
        "### oos_warm (주 수치)",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS_WARM, "risk_pct"),
        "",
        "### oos (차가운 스트레스)",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS, "risk_pct"),
        "",
        "### full · is (맥락)",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_FULL, "risk_pct"),
        "",
        *_book_table(book_rows, main_scope, SEGMENT_IS, "risk_pct"),
        "",
        "⚠️ **격리(현행) 행과 북 행은 자가 다르다** — 격리는 칸마다 독립 자본을 준 수익률의 "
        "**칸 평균**(이 저장소의 심볼평균 관행)이고, 북은 한 지갑의 단일 자본곡선이다. 격리 "
        "12칸의 자본 합은 북의 12배이므로 두 행을 「같은 돈의 두 성적」으로 읽지 말 것 — "
        "격리 행은 「칸들이 각자였다면」의 기준선일 뿐이다. 북 1배가 격리 평균보다 훨씬 큰 "
        "주된 이유도 배수가 아니라 **거래 빈도**다: 한 지갑이 전 칸의 셋업을 순차로 다 받아 "
        "복리 횟수가 칸 하나의 몇 배가 된다.",
        "",
        "🚨 **북의 수익률은 수백~수천 거래의 복리 값이다 — 달성 가능 성과로 인용 금지.** "
        "full·is에서 조 단위 %까지 커지는 것은 복리 산수이지 새 정보가 아니며, 거래당 "
        "기대값의 작은 낙관(체결 가정·비용)이 그 횟수만큼 지수적으로 증폭된 값이다. 이 표에서 "
        "결정에 실질적인 열은 수익률의 절대 크기가 아니라 **MDD · 최대 동시 리스크 · 청산 "
        "트리거**다.",
        "",
    ]
    for tf in [t for t in MAIN_TIMEFRAMES if t in present and t != main_scope]:
        lines += [
            f"## 2. TF 단면 — {tf}(6칸) × `risk_pct`",
            "",
            "### oos_warm (주)",
            "",
            *_book_table(book_rows, tf, SEGMENT_OOS_WARM, "risk_pct"),
            "",
            "### oos (스트레스)",
            "",
            *_book_table(book_rows, tf, SEGMENT_OOS, "risk_pct"),
            "",
        ]
    lines += [
        "## 3. 사이징 축 — `fixed_notional`(시드 분할: 명목 = 자본 × N/칸수)",
        "",
        "WAN-108 2안의 오늘 엔진 판이다. ⚠️ 옛 WAN-108의 「2안이 진다」는 **옛 엔진** 값이라 "
        "결론으로 재인용 금지 — 아래가 첫 측정이다.",
        "",
        f"### {main_scope} · oos_warm",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS_WARM, "fixed_notional"),
        "",
        f"### {main_scope} · oos",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS, "fixed_notional"),
        "",
        "## 4. leave-one-out — 종목 편중 (`risk_pct` · 북)",
        "",
        f"### {main_scope} · oos_warm",
        "",
        *_loo_lines(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        f"### {main_scope} · oos",
        "",
        *_loo_lines(book_rows, main_scope, SEGMENT_OOS),
        "",
        "## 판정 — 리스크가 배수만큼 오른다는 가정 아래, 그럼에도 할 만한가",
        "",
        verdict(book_rows),
        "",
        f"판정 자: 각 스코프(15m·1h·both) × 구간(oos_warm·oos)에서 북(`risk_pct`)의 어떤 배수 "
        "N>1이 1배의 수익/MDD를 **청산 트리거 0으로** 이기는가. 전부 그렇다 → (a) · 전부 "
        f"아니다 → (b) · 갈린다 → (c). 표본 게이트 {MIN_TRADES}건(WAN-84) 미달 셀은 판정에서 "
        "뺀다.",
        "",
        "⚠️ **청산 트리거는 최악 가정 검사다**(WAN-103 결정 4 — 열린 포지션 전부 동시 손절 시 "
        "유지증거금 미달) — 0건이 「그 배수는 안전하다」가 아니라 「이 보수적 상한 검사로는 "
        "마진콜 사거리 밖」이라는 뜻이다. 순차 손실의 복리 낙폭은 MDD 열이 담당한다.",
        "",
        _compounding_caveat(book_rows, main_scope),
        "",
        "⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151)은 이 표로 뒤집히지 않는다** — 레버리지는 "
        "위험의 모양만 바꾸지 알파를 만들지 않는다(WAN-90 계열). 배수·사이징을 기본값으로 "
        "올리는 것은 재-베이스라인 = 사용자 결정이다.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-169 레버리지 북 측정")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(MAIN_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 칸 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-grid", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_grid = Path(args.out_grid)
    out_md = Path(args.out_md)

    if args.from_csv:
        cell_rows = cells_from_csv(out_cells)
        book_rows = grid_from_csv(out_grid)
        print(f"[wan169] CSV 로드 — 칸 {len(cell_rows)}행 · 격자 {len(book_rows)}행 (재실행 없음)")
    else:
        payloads = run_cells(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        cell_rows = [row for p in payloads for row in p.rows]
        book_rows = build_book_rows(payloads)
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(cell_rows).to_csv(out_cells, index=False)
        grid_to_frame(book_rows).to_csv(out_grid, index=False)
        print(f"[wan169] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan169] 격자 {len(book_rows)}행 → {out_grid}")

    verify_line, worst = verify_cells(cell_rows)
    print(f"[wan169] 검산: {verify_line}")
    if not math.isfinite(worst) or worst >= 1e-12:
        print("[wan169] 🚨 검산 실패 — 요약을 내기 전에 배선을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(cell_rows, book_rows, cells_csv=out_cells, grid_csv=out_grid),
        encoding="utf-8",
    )
    print(f"[wan169] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
