"""WAN-424 §2: 스토캐스틱 × 오더블록 팔이 **무작위와 구분되는가** — 매칭 대조군 두 갈래.

§1(`backtest.wan424_stoch_ob_arm`)이 WAN-423 §7 표를 90값 전부 재현했다. 이 모듈이 그
팔(롱 · 31종목 × 9TF · 첫 탭 · 재진입 없음 · `pen_5bp` × 같은 분 익절 금지 · 채택 북 ·
복리 끔)에 대조군을 건다. **좌표는 결정문 권고 그대로이고 줄이지 않는다**(사용자 코멘트:
줄이면 검정 대상인 숫자 자체가 달라진다).

## 대조군 두 갈래 — 같은 개수 · 같은 기하 · 같은 회계

* **(A) %K 선별 널** — *「%K가 오더블록 탭 중 무작위 고르기를 이기나」*. 같은 칸 · 같은
  구간 층(아래)의 **하한 통과 팔 후보**(%K를 안 본 풀)에서 실제와 **같은 개수**를 무작위
  추출한다. 진입·청산은 그 후보 자기 것이라 기하·회계가 같다.
* **(B) 시각 널** — *「OB+%K 진입이 같은 칸의 무작위 시각을 이기나」*(이슈 본문의 「존은 같고
  진입 시각만 무작위」). 실제 거래 하나마다 **같은 칸 · 같은 구간 층 · 같은 손절폭 비율 · 같은
  보유 봉 수**로, 진입 시각만 그 층의 1분봉 중 무작위로 옮긴다(진입가 = 그 1분 종가 ·
  손절 = 진입가 × (1 − 폭) · 같은 `time_exit` 규칙). WAN-423 §4 `nts` 팔과 같은 설계다.

🚨 **구간 층** — `full`은 따뜻한 경계(`boundary_ms`) 앞/뒤를 **따로** 뽑는다. 안 그러면
`oos_warm`(= `full`의 경계 뒤)의 개수가 실제와 달라져 「같은 개수」가 그 구간에서 깨진다.
차가운 `is`·`oos`는 각자 자기 창에서 뽑는다. (B)의 무작위 진입은 `trigger_time`도 그 시각으로
옮겨 층이 유지된다.

## 자 — 옛 계열에서 물려받는다 (새로 쓰지 않는다)

`backtest.wan248_zone_position_null`에서 **import**: 표본 게이트 `MIN_TRADES_FOR_VERDICT`(20) ·
`ALPHA`(0.05) · 반복 `BOOTSTRAP_ITERATIONS`(200). p값은 WAN-70 식 그대로 —
*무작위 반복 중 실제 이상을 낸 비율*(단측). 유의 = `p ≤ α` **이면서** 실제 > 무작위 평균.
시드는 이 모듈의 `NULL_SEED`(424)이고 표본마다 (널·반복·칸·구간)에서 결정적으로 파생한다.
📌 **판단은 북에서**(WAN-341) — 주 판정은 **한 지갑의 거래당 net R**이고, 칸 단위 유의
셀 수(유효 셀 ≥20거래)는 옛 계열과 같은 자의 **보조 열**이다.

## 판정 줄 (착수 전에 못 박음)

판정 칸 = `ts4 · 하한 4% · %K<25`(사용자 코멘트). 두 널 각각:
**앞구간(`is`)에서 유의 → 뒷구간(`oos_warm`)에서 확인**. 뒷구간은 고르는 축이 아니다.
나머지 다섯 칸(ts4/ts8 × %K 15·20·25)은 **모양**으로만 읽는다(WAN-161 — 최선 칸 고르기 금지).

통제(판정 칸 · `oos_warm`): (1) 종목 하나씩 빼고 **지갑 재배치**(31판) · (2) WAN-408 최악
10일(KST 진입일) 제외 · (3) WAN-410 「그날 실현 net R 누계」 버킷 안 차이의 가중 합(누계는 각
지갑 자기 거래로 — `realized_r_before(convention="settled")`).

## 재현

```
uv run python -m backtest.wan424_matched_null --jobs 4          # 팔 + 널 B 생성(무겁다) + 배치
uv run python -m backtest.wan424_matched_null --from-csv        # 요약만 다시
```

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 엔진 소스 무변경) · 채택 좌표 아님 · 실거래 보류 유지.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import math
import pickle
import random
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan424_stoch_ob_arm as arm
from backtest.book_cli import iter_book_segments, net_r
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS
from backtest.leverage_book import LeverageBookParams
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.substep import SubStep, build_substeps
from backtest.wan169_leverage_book import IS_SEGMENT, OOS_SEGMENT, CellPayload

# 자는 옛 계열에서 물려받는다 — 재수출(`X as X`)로 테스트가 「같은 객체」임을 건다.
from backtest.wan248_zone_position_null import ALPHA as ALPHA
from backtest.wan248_zone_position_null import BOOTSTRAP_ITERATIONS as BOOTSTRAP_ITERATIONS
from backtest.wan248_zone_position_null import MIN_TRADES_FOR_VERDICT as MIN_TRADES_FOR_VERDICT
from backtest.wan375_conditional_rr import worst_days
from backtest.wan408_loss_clustering import (
    REALIZED_BUCKETS,
    TradeFact,
    _bucket_order_realized,
)
from backtest.wan410_daily_loss_circuit_breaker import realized_r_before
from backtest.zone_limit_backtest import _Candidate
from common.costs import Liquidity
from common.timefmt import kst_day_key
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

REPORT_DIR = arm.REPORT_DIR
NULL_CSV = REPORT_DIR / "wan424_matched_null.csv"
LOO_CSV = REPORT_DIR / "wan424_matched_null_loo.csv"
CONTROL_CSV = REPORT_DIR / "wan424_matched_null_controls.csv"
CELL_CSV = REPORT_DIR / "wan424_matched_null_cells.csv"
SUMMARY_PATH = REPORT_DIR / "wan424_matched_null_summary.md"
WORK_CACHE = Path(__file__).resolve().parent / "cache" / "wan424_null_work.pkl.gz"

NULL_SEED = 424
FLOOR = 0.04
HOLDS: tuple[int, ...] = (4, 8)
THRESHOLDS: tuple[float, ...] = (15.0, 20.0, 25.0)
JUDGE = (4, 25.0)
"""판정 칸(보유 봉, %K 문턱) — 하한은 `FLOOR`. 사용자 코멘트가 못 박은 좌표다."""
NULLS: tuple[str, ...] = ("A_선별", "B_시각")
SEGMENTS: tuple[str, ...] = ("full", "is", "oos_warm")
RAW_SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS)
LOO_DRAWS = 100
"""LOO는 판정 칸 · `oos_warm`만, 널 반복을 100으로 줄인다(31판 × 두 널 — 비용 기록)."""


# ---------------------------------------------------------------------------
# 순수 함수 — 무작위 시각 청산(넘파이)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MinuteArrays:
    """한 창의 1분봉을 시간 청산용 배열로 — `build_substeps`와 같은 버킷(`floor(t/tf)`)."""

    time: np.ndarray
    low: np.ndarray
    close: np.ndarray
    bar_end: np.ndarray
    """각 1분이 속한 상위TF 버킷 번호 → 그 버킷 마지막 1분의 인덱스(버킷 번호로 조회)."""
    bar_id: np.ndarray

    @classmethod
    def from_substeps(cls, substeps: Sequence[SubStep]) -> MinuteArrays:
        times = np.fromiter((s.time for s in substeps), dtype=np.int64)
        lows = np.fromiter((s.low for s in substeps), dtype=np.float64)
        closes = np.fromiter((s.close for s in substeps), dtype=np.float64)
        htf = np.fromiter((s.htf_bar_time for s in substeps), dtype=np.int64)
        if len(htf):
            change = np.flatnonzero(np.diff(htf)) + 1
            bar_id = np.zeros(len(htf), dtype=np.int64)
            bar_id[change] = 1
            bar_id = np.cumsum(bar_id)
            bar_end = np.append(change - 1, len(htf) - 1)
        else:
            bar_id = np.zeros(0, dtype=np.int64)
            bar_end = np.zeros(0, dtype=np.int64)
        return cls(times, lows, closes, bar_end, bar_id)


def random_time_exit(
    arrays: MinuteArrays, *, index: int, width: float, bars: int
) -> tuple[int, float, float, int, float, bool]:
    """`index`의 1분 종가에 롱 진입 · 손절 = 진입 × (1 − 폭) · 버킷 `bars`개 뒤 종가 청산.

    `arm.time_exit`와 같은 규칙(진입 스텝 포함 손절 우선 · 창 끝이면 거기서 청산)을 배열로 푼다.
    반환 = (진입 시각, 진입가, 손절가, 청산 시각, 청산가, 손절 여부).
    """
    entry = float(arrays.close[index])
    stop = entry * (1.0 - width)
    last_bar = min(int(arrays.bar_id[index]) + bars - 1, len(arrays.bar_end) - 1)
    end = int(arrays.bar_end[last_bar])
    window = arrays.low[index : end + 1]
    hits = np.flatnonzero(window <= stop)
    t0 = int(arrays.time[index])
    if hits.size:
        j = index + int(hits[0])
        return t0, entry, stop, int(arrays.time[j]), stop, True
    return t0, entry, stop, int(arrays.time[end]), float(arrays.close[end]), False


def _rng(*parts: object) -> random.Random:
    return random.Random(":".join(str(p) for p in (NULL_SEED, *parts)))


def strata(segment: str, boundary_ms: int) -> tuple[tuple[str, int | None, int | None], ...]:
    """구간 → (층 이름, 하한, 상한) — `full`만 따뜻한 경계로 둘로 가른다."""
    if segment == SEGMENT_FULL:
        return (("pre", None, boundary_ms), ("post", boundary_ms, None))
    return (("all", None, None),)


def _in_stratum(t: int, lo: int | None, hi: int | None) -> bool:
    return (lo is None or t >= lo) and (hi is None or t < hi)


# ---------------------------------------------------------------------------
# 워커 — 칸 하나: 팔 후보(§1과 같은 식) + 널 B 반복
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NullCell:
    symbol: str
    timeframe: str
    boundary_ms: int
    pool: dict[int, dict[str, tuple[_Candidate, ...]]]
    """보유 봉 → 구간 → 하한 `FLOOR` 통과 팔 후보(%K 안 봄). 순서가 곧 풀 인덱스다."""
    k_value: dict[str, tuple[float | None, ...]]
    """구간 → 풀 후보마다 직전 확정봉 %K(보유 봉과 무관 — 진입 시각이 같다)."""
    null_b: dict[int, dict[str, np.ndarray]]
    """보유 봉 → 구간 → (반복, 풀 크기, 6) 배열: 진입t·진입가·손절·청산t·청산가·손절여부."""


def _cell_work(job: tuple[CellPayload, int]) -> NullCell:
    payload, draws = job
    start_ms = parse_date_ms(harness.DEFAULT_START)
    end_ms = parse_date_ms(harness.DEFAULT_END)
    market = harness.load_market_data(
        payload.symbol, payload.timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True
    )
    htf_ms = timeframe_to_ms(payload.timeframe)
    windows = {
        SEGMENT_FULL: market,
        SEGMENT_IS: harness.slice_market(market, IS_SEGMENT),
        SEGMENT_OOS: harness.slice_market(market, OOS_SEGMENT),
    }
    times, kline = arm.stoch_series(OhlcvStore(harness.DB_PATH), payload.symbol, payload.timeframe)
    pool: dict[int, dict[str, tuple[_Candidate, ...]]] = {h: {} for h in HOLDS}
    k_value: dict[str, tuple[float | None, ...]] = {}
    null_b: dict[int, dict[str, np.ndarray]] = {h: {} for h in HOLDS}
    for segment, window in windows.items():
        base = arm.arm_pool(payload.candidates.get(segment, ()), min_width=FLOOR)
        substeps = build_substeps(window.df_1m, htf_ms) if base else []
        derived = arm.derive_hold_arms(base, substeps=substeps, holds=HOLDS) if base else {}
        # derive_hold_arms는 진입 스텝이 없는 후보를 뺀다 — 모든 보유 봉에서 같은 후보가 빠지므로
        # 풀 인덱스가 보유 봉 사이에서 정렬된다(아래 assert가 확인한다).
        kept = derived.get(HOLDS[0], [])
        for h in HOLDS:
            cands = derived.get(h, [])
            assert [c.entry_time for c in cands] == [c.entry_time for c in kept]
            pool[h][segment] = tuple(cands)
        k_value[segment] = tuple(
            arm.k_before(times, kline, int(c.entry_time), htf_ms) for c in kept
        )
        arrays = MinuteArrays.from_substeps(substeps) if kept else None
        for h in HOLDS:
            out = np.zeros((draws, len(kept), 6), dtype=np.float64)
            if arrays is not None and len(arrays.time):
                for s_name, lo, hi in strata(segment, payload.boundary_ms):
                    members = [i for i, c in enumerate(kept) if _in_stratum(c.trigger_time, lo, hi)]
                    if not members:
                        continue
                    lo_i = 0 if lo is None else int(np.searchsorted(arrays.time, lo))
                    hi_i = len(arrays.time) if hi is None else int(np.searchsorted(arrays.time, hi))
                    if hi_i <= lo_i:
                        lo_i, hi_i = 0, len(arrays.time)
                    for d in range(draws):
                        rng = _rng("B", d, payload.symbol, payload.timeframe, segment, s_name)
                        for i in members:
                            index = rng.randrange(lo_i, hi_i)
                            out[d, i] = random_time_exit(
                                arrays, index=index, width=arm.stop_width(kept[i]), bars=h
                            )
            null_b[h][segment] = out
    return NullCell(payload.symbol, payload.timeframe, payload.boundary_ms, pool, k_value, null_b)


# ---------------------------------------------------------------------------
# 표본 → payload → 북 배치
# ---------------------------------------------------------------------------


def real_indices(cell: NullCell, segment: str, threshold: float) -> list[int]:
    return [
        i for i, kv in enumerate(cell.k_value.get(segment, ())) if kv is not None and kv < threshold
    ]


def _null_a_indices(
    cell: NullCell, segment: str, real: Sequence[int], draw: int, hold: int, threshold: float
) -> list[int]:
    cands = cell.pool[hold].get(segment, ())
    out: list[int] = []
    for s_name, lo, hi in strata(segment, cell.boundary_ms):
        members = [i for i, c in enumerate(cands) if _in_stratum(c.trigger_time, lo, hi)]
        need = sum(1 for i in real if _in_stratum(cands[i].trigger_time, lo, hi))
        rng = _rng("A", draw, cell.symbol, cell.timeframe, segment, s_name, hold, threshold)
        out.extend(rng.sample(members, need))
    return out


def _null_b_candidate(base: _Candidate, row: np.ndarray) -> _Candidate:
    t_in, entry, stop, t_out, exit_price, is_stop = row
    return dataclasses.replace(
        base,
        entry_time=int(t_in),
        trigger_time=int(t_in),
        entry_price=float(entry),
        stop_price=float(stop),
        exit_time=int(t_out),
        exit_price=float(exit_price),
        reason=ExitReason.STOP_LOSS if is_stop else ExitReason.END_OF_DATA,
        take_profit_price=None,
        entry_liquidity=Liquidity.MAKER,
        mfe_r=None,
        mae_r=None,
    )


def build_payloads(
    payloads: Sequence[CellPayload],
    cells: dict[tuple[str, str], NullCell],
    *,
    hold: int,
    threshold: float,
    null: str | None,
    draw: int = 0,
    drop_symbol: str | None = None,
) -> list[CellPayload]:
    """실제(`null=None`) 또는 널 표본 한 판의 payload — 재진입·팔 후보는 비운다."""
    out: list[CellPayload] = []
    for p in payloads:
        if drop_symbol is not None and p.symbol == drop_symbol:
            continue
        cell = cells[(p.symbol, p.timeframe)]
        kept: dict[str, tuple[_Candidate, ...]] = {}
        for segment in RAW_SEGMENTS:
            cands = cell.pool[hold].get(segment, ())
            real = real_indices(cell, segment, threshold)
            if null is None:
                kept[segment] = tuple(cands[i] for i in real)
            elif null == NULLS[0]:
                picks = _null_a_indices(cell, segment, real, draw, hold, threshold)
                kept[segment] = tuple(cands[i] for i in picks)
            else:
                rows = cell.null_b[hold][segment]
                kept[segment] = tuple(_null_b_candidate(cands[i], rows[draw, i]) for i in real)
        out.append(
            dataclasses.replace(p, candidates=kept, reentry_candidates={}, arm_candidates={})
        )
    return out


@dataclass(frozen=True)
class Fact:
    symbol: str
    timeframe: str
    entry_time: int
    exit_time: int
    is_stop: bool
    net_r: float


def place_facts(
    payloads: Sequence[CellPayload], segments: Sequence[str] = SEGMENTS
) -> dict[str, list[Fact]]:
    """§1과 같은 회계(채택 북 · 복리 끔 · 가드 끔 · 익절 메이커)로 배치해 거래 사실을 낸다."""
    out: dict[str, list[Fact]] = {}
    for seg in iter_book_segments(
        payloads,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
        include_reentry=False,
        compound_sizing=False,
        min_stop_distance_fraction=0.0,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    ):
        out[seg.segment] = [
            Fact(
                p.cell[0],
                p.cell[1],
                int(t.entry_time),
                int(t.exit_time),
                t.exits[-1].reason is ExitReason.STOP_LOSS,
                net_r(t, p),
            )
            for t, p in seg.trades_with_placements()
        ]
    return out


def mean_r(facts: Sequence[Fact]) -> float:
    return sum(f.net_r for f in facts) / len(facts) if facts else math.nan


def p_value(real: float, nulls: Sequence[float]) -> float:
    """WAN-70 식 — 무작위 반복 중 실제 이상을 낸 비율(단측)."""
    vals = [v for v in nulls if not math.isnan(v)]
    return sum(1 for v in vals if v >= real) / len(vals) if vals else math.nan


def significant(real: float, nulls: Sequence[float], n_real: int) -> bool:
    vals = [v for v in nulls if not math.isnan(v)]
    if n_real < MIN_TRADES_FOR_VERDICT or not vals:
        return False
    return p_value(real, vals) <= ALPHA and real > sum(vals) / len(vals)


# ---------------------------------------------------------------------------
# 통제 — 폭락일 · 그날 실현 누계
# ---------------------------------------------------------------------------


def excluding_days(facts: Sequence[Fact], days: set[str]) -> list[Fact]:
    return [f for f in facts if kst_day_key(f.entry_time) not in days]


def realized_orders(facts: Sequence[Fact]) -> list[int]:
    """거래마다 그날(KST) 이미 실현된 net R 누계의 WAN-410 버킷 순번(자기 지갑 기준)."""
    trade_facts = [
        TradeFact(f.symbol, f.timeframe, f.entry_time, f.exit_time, f.is_stop, f.net_r, False)
        for f in facts
    ]
    before = realized_r_before(trade_facts, convention="settled")
    return [_bucket_order_realized(v, REALIZED_BUCKETS) for v in before]


def bucket_means(facts: Sequence[Fact]) -> dict[int, tuple[float, int]]:
    groups: dict[int, list[float]] = defaultdict(list)
    for f, order in zip(facts, realized_orders(facts), strict=True):
        groups[order].append(f.net_r)
    return {k: (sum(v) / len(v), len(v)) for k, v in groups.items()}


def pooled_bucket_gap(real: Sequence[Fact], null: Sequence[Fact]) -> float:
    """버킷마다 (실제 − 널) 평균 차를 **실제 거래 수로 가중**한 합 — 공통 버킷만."""
    rb, nb = bucket_means(real), bucket_means(null)
    common = [k for k in rb if k in nb]
    total = sum(rb[k][1] for k in common)
    if total == 0:
        return math.nan
    return sum(rb[k][1] * (rb[k][0] - nb[k][0]) for k in common) / total


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------


def build_cells(
    payloads: Sequence[CellPayload], *, jobs: int, draws: int
) -> dict[tuple[str, str], NullCell]:
    jobs_list = [(p, draws) for p in payloads]
    if jobs <= 1:
        cells = [_cell_work(j) for j in jobs_list]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            cells = list(pool.map(_cell_work, jobs_list))
    return {(c.symbol, c.timeframe): c for c in cells}


@dataclass(frozen=True)
class NullRow:
    hold: int
    threshold: float
    null: str
    segment: str
    real_trades: int
    real_mean_r: float
    null_mean_r: float
    null_ci_low: float
    null_ci_high: float
    p_value: float
    significant: bool
    null_trades_mean: float


def _ci(vals: Sequence[float]) -> tuple[float, float]:
    s = sorted(v for v in vals if not math.isnan(v))
    if not s:
        return math.nan, math.nan
    return s[int(0.025 * (len(s) - 1))], s[int(0.975 * (len(s) - 1))]


def run_null_grid(
    payloads: Sequence[CellPayload],
    cells: dict[tuple[str, str], NullCell],
    *,
    draws: int,
) -> tuple[
    list[NullRow],
    dict[tuple[int, float, str], list[dict[str, list[Fact]]]],
    dict[tuple[int, float], dict[str, list[Fact]]],
]:
    rows: list[NullRow] = []
    null_facts: dict[tuple[int, float, str], list[dict[str, list[Fact]]]] = {}
    real_facts: dict[tuple[int, float], dict[str, list[Fact]]] = {}
    for hold in HOLDS:
        for thr in THRESHOLDS:
            real = place_facts(build_payloads(payloads, cells, hold=hold, threshold=thr, null=None))
            real_facts[(hold, thr)] = real
            for null in NULLS:
                started = time.monotonic()
                samples = [
                    place_facts(
                        build_payloads(payloads, cells, hold=hold, threshold=thr, null=null, draw=d)
                    )
                    for d in range(draws)
                ]
                null_facts[(hold, thr, null)] = samples
                for seg in SEGMENTS:
                    means = [mean_r(s[seg]) for s in samples]
                    lo, hi = _ci(means)
                    r_mean = mean_r(real[seg])
                    rows.append(
                        NullRow(
                            hold,
                            thr,
                            null,
                            seg,
                            len(real[seg]),
                            r_mean,
                            float(np.nanmean(means)),
                            lo,
                            hi,
                            p_value(r_mean, means),
                            significant(r_mean, means, len(real[seg])),
                            float(np.mean([len(s[seg]) for s in samples])),
                        )
                    )
                print(
                    f"ts{hold} K<{thr:.0f} {null}: {draws}표본 {time.monotonic() - started:.0f}s",
                    flush=True,
                )
    return rows, null_facts, real_facts


def cell_rows(
    real: dict[str, list[Fact]], samples: Sequence[dict[str, list[Fact]]], null: str
) -> list[dict[str, object]]:
    """칸 단위 보조 열 — 옛 계열과 같은 자(유효 ≥20거래 · p≤α & 실제>평균)."""
    out: list[dict[str, object]] = []
    for seg in ("is", "oos_warm"):
        by_cell: dict[tuple[str, str], list[float]] = defaultdict(list)
        for f in real[seg]:
            by_cell[(f.symbol, f.timeframe)].append(f.net_r)
        for key, rs in by_cell.items():
            if len(rs) < MIN_TRADES_FOR_VERDICT:
                continue
            r_mean = sum(rs) / len(rs)
            nulls = []
            for s in samples:
                vals = [f.net_r for f in s[seg] if (f.symbol, f.timeframe) == key]
                nulls.append(sum(vals) / len(vals) if vals else math.nan)
            out.append(
                {
                    "null": null,
                    "segment": seg,
                    "symbol": key[0],
                    "timeframe": key[1],
                    "real_trades": len(rs),
                    "real_mean_r": r_mean,
                    "null_mean_r": float(np.nanmean(nulls)),
                    "p_value": p_value(r_mean, nulls),
                    "significant": significant(r_mean, nulls, len(rs)),
                }
            )
    return out


def run_loo(
    payloads: Sequence[CellPayload],
    cells: dict[tuple[str, str], NullCell],
    *,
    draws: int,
) -> list[dict[str, object]]:
    """판정 칸 · `oos_warm` — 종목 하나씩 빼고 **지갑을 다시 배치**(WAN-316)."""
    hold, thr = JUDGE
    rows: list[dict[str, object]] = []
    present = {p.symbol for p in payloads}
    for sym in [s for s in arm.SYMBOLS if s in present]:
        real = place_facts(
            build_payloads(payloads, cells, hold=hold, threshold=thr, null=None, drop_symbol=sym),
            segments=("oos_warm",),
        )["oos_warm"]
        r_mean = mean_r(real)
        rec: dict[str, object] = {"drop": sym, "real_trades": len(real), "real_mean_r": r_mean}
        for null in NULLS:
            means = [
                mean_r(
                    place_facts(
                        build_payloads(
                            payloads,
                            cells,
                            hold=hold,
                            threshold=thr,
                            null=null,
                            draw=d,
                            drop_symbol=sym,
                        ),
                        segments=("oos_warm",),
                    )["oos_warm"]
                )
                for d in range(draws)
            ]
            rec[f"{null}_mean_r"] = float(np.nanmean(means))
            rec[f"{null}_p"] = p_value(r_mean, means)
            rec[f"{null}_sig"] = significant(r_mean, means, len(real))
        rows.append(rec)
        print(f"LOO −{sym}: {r_mean:+.4f}", flush=True)
    return rows


def run_controls(
    real: dict[str, list[Fact]], samples_by_null: Mapping[str, Sequence[dict[str, list[Fact]]]]
) -> list[dict[str, object]]:
    """판정 칸 · `oos_warm` — 최악 10일 제외 · WAN-410 버킷 안 차이."""
    bad = set(worst_days())
    seg = "oos_warm"
    rows: list[dict[str, object]] = []
    real_ex = excluding_days(real[seg], bad)
    for null, samples in samples_by_null.items():
        null_ex = [mean_r(excluding_days(s[seg], bad)) for s in samples]
        r_ex = mean_r(real_ex)
        rows.append(
            {
                "null": null,
                "control": f"최악 {len(bad)}일 제외",
                "real_trades": len(real_ex),
                "real_mean_r": r_ex,
                "null_mean_r": float(np.nanmean(null_ex)),
                "p_value": p_value(r_ex, null_ex),
                "significant": significant(r_ex, null_ex, len(real_ex)),
            }
        )
        gaps = [pooled_bucket_gap(real[seg], s[seg]) for s in samples]
        rows.append(
            {
                "null": null,
                "control": "WAN-410 버킷 안(실제 가중 차)",
                "real_trades": len(real[seg]),
                "real_mean_r": math.nan,
                "null_mean_r": float(np.nanmean(gaps)),
                "p_value": sum(1 for g in gaps if not math.isnan(g) and g <= 0)
                / max(1, sum(1 for g in gaps if not math.isnan(g))),
                "significant": False,
            }
        )
        # 버킷 안 차이의 판정 — 널 반복마다 낸 차의 분포가 0보다 확실히 크면(하위 α가 양수) 선다.
        valid = sorted(g for g in gaps if not math.isnan(g))
        rows[-1]["significant"] = bool(valid) and valid[int(ALPHA * (len(valid) - 1))] > 0
    by_bucket = bucket_means(real[seg])
    for order, (m, n) in sorted(by_bucket.items()):
        rows.append(
            {
                "null": "실제",
                "control": f"버킷 {order}",
                "real_trades": n,
                "real_mean_r": m,
                "null_mean_r": math.nan,
                "p_value": math.nan,
                "significant": False,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def verdict(rows: Sequence[NullRow], null: str) -> str:
    hold, thr = JUDGE
    idx = {(r.segment): r for r in rows if (r.hold, r.threshold, r.null) == (hold, thr, null)}
    is_row, oos_row = idx.get("is"), idx.get("oos_warm")
    if is_row is None or oos_row is None:
        return "판정 불가(행 없음)"
    if not is_row.significant:
        return f"(나) 앞구간에서 무작위와 구분되지 않는다(p={is_row.p_value:.3f})"
    if oos_row.significant:
        return f"(가) 앞구간 p={is_row.p_value:.3f} → 뒷구간 p={oos_row.p_value:.3f}로 **넘어간다**"
    return (
        f"(다) 앞구간은 유의(p={is_row.p_value:.3f})한데 뒷구간에서 **안 넘어간다**"
        f"(p={oos_row.p_value:.3f})"
    )


def reproduction_check(rows: Sequence[NullRow]) -> list[str]:
    """실제 팔이 §1이 재현한 WAN-423 기준값과 같은가 — 거래 수 정확히 · net R 소수 셋째 자리."""
    out: list[str] = []
    for (hold, floor, thr), segs in arm.WAN423_REFERENCE.items():
        if floor != FLOOR or hold not in HOLDS or thr not in THRESHOLDS:
            continue
        for seg, (ref_r, ref_n) in segs.items():
            got = next(
                (r for r in rows if (r.hold, r.threshold, r.segment) == (hold, thr, seg)), None
            )
            if got is None:
                continue
            ok = got.real_trades == ref_n and round(got.real_mean_r, 3) == ref_r
            out.append(
                f"{'✅' if ok else '❌'} ts{hold} %K<{thr:.0f} {seg}: {got.real_mean_r:+.4f} "
                f"({got.real_trades}) · 기준 {ref_r:+.3f} ({ref_n})"
            )
    return out


def render_summary(
    rows: Sequence[NullRow],
    loo: pd.DataFrame | None,
    controls: pd.DataFrame | None,
    cell_frame: pd.DataFrame | None,
    *,
    draws: int,
    elapsed: float | None = None,
) -> str:
    hold, thr = JUDGE
    lines = [
        "# WAN-424 §2 — 스토캐스틱 × 오더블록 팔의 매칭 대조군",
        "",
        "롱 · 31종목 × 9TF · 첫 탭 · 재진입 없음 · `pen_5bp` × 같은 분 익절 금지 · "
        "채택 북 · 복리 끔 · "
        f"하한 {FLOOR:.0%} · 반복 {draws} · 시드 {NULL_SEED} · "
        f"유의 = p≤{ALPHA} & 실제>무작위평균 · "
        f"표본 게이트 {MIN_TRADES_FOR_VERDICT}거래.",
        "",
        f"## 판정 (판정 칸 ts{hold} · 하한 {FLOOR:.0%} · %K<{thr:.0f} · 앞구간 → 뒷구간)",
        "",
    ]
    for null in NULLS:
        lines.append(f"- **{null}**: {verdict(rows, null)}")
    lines += ["", "재현 검산(실제 팔 ≡ §1 · WAN-423 §7 기준값):", ""]
    lines += [f"- {line}" for line in reproduction_check(rows)]
    lines += [
        "",
        "## 표 (거래당 net R · 실제 vs 무작위)",
        "",
        "| 칸 | 널 | 구간 | 실제 (거래) | 무작위 평균 [95%] | p | 유의 |",
        "| -- | -- | -- | --: | --: | --: | :-: |",
    ]
    for r in rows:
        lines.append(
            f"| ts{r.hold} · %K<{r.threshold:.0f} | {r.null} | {r.segment} | "
            f"{r.real_mean_r:+.4f} ({r.real_trades}) | {r.null_mean_r:+.4f} "
            f"[{r.null_ci_low:+.4f}, {r.null_ci_high:+.4f}] | {r.p_value:.3f} | "
            f"{'✅' if r.significant else '—'} |"
        )
    if cell_frame is not None and not cell_frame.empty:
        lines += ["", "## 칸 단위 보조 열(판정 칸 · 유효 ≥20거래)", ""]
        for (null, seg), g in cell_frame.groupby(["null", "segment"]):
            n_sig = int(g["significant"].sum())
            lines.append(f"- {null} · {seg}: 유효 {len(g)}칸 중 유의 {n_sig}")
    if loo is not None and not loo.empty:
        lines += ["", "## 종목 하나씩 빼기 (판정 칸 · oos_warm · 지갑 재배치)", ""]
        lines.append(
            f"- 실제 net R: 최저 {loo['real_mean_r'].min():+.4f} · "
            f"최고 {loo['real_mean_r'].max():+.4f}"
            f" · 음수 {int((loo['real_mean_r'] <= 0).sum())}/{len(loo)}"
        )
        for null in NULLS:
            col = f"{null}_sig"
            if col in loo:
                lines.append(
                    f"- {null}: 유의 유지 {int(loo[col].sum())}/{len(loo)} · "
                    f"p 최대 {loo[f'{null}_p'].max():.3f}"
                )
    if controls is not None and not controls.empty:
        lines += ["", "## 폭락일 통제 (판정 칸 · oos_warm)", ""]
        lines += [
            "| 널 | 통제 | 실제 (거래) | 무작위 / 차 | p | 유의 |",
            "| -- | -- | --: | --: | --: | :-: |",
        ]
        for rec in controls.to_dict("records"):
            lines.append(
                f"| {rec['null']} | {rec['control']} | {float(rec['real_mean_r']):+.4f} "
                f"({int(rec['real_trades'])}) | {float(rec['null_mean_r']):+.4f} | "
                f"{float(rec['p_value']):.3f} | {'✅' if bool(rec['significant']) else '—'} |"
            )
    lines += [
        "",
        "⚠️ 최선 칸을 고르지 말 것(WAN-161) · 채택 좌표 아님 · 복리 MDD 30~95%(§1) · "
        "`pen_5bp` 위 값 · 기본값 전환은 사용자 결정.",
    ]
    if elapsed is not None:
        lines.append(f"\n실측 {elapsed:.0f}초.")
    return "\n".join(lines) + "\n"


def _load_or_build_cells(
    payloads: Sequence[CellPayload], *, jobs: int, draws: int, cache: Path
) -> dict[tuple[str, str], NullCell]:
    if cache.exists():
        with gzip.open(cache, "rb") as fh:
            saved = pickle.load(fh)
        if saved.get("draws") == draws and saved.get("revision") == _revision():
            print(f"널 작업물 캐시 적중: {cache}", flush=True)
            return saved["cells"]  # type: ignore[no-any-return]
    cells = build_cells(payloads, jobs=jobs, draws=draws)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache, "wb") as fh:
        pickle.dump({"draws": draws, "revision": _revision(), "cells": cells}, fh)
    return cells


def _revision() -> str:
    """널 작업물 캐시의 지문 — 이 모듈·§1 모듈 소스 + 후보 캐시 리비전."""
    import hashlib

    from backtest.payload_cache import payload_source_revision

    digest = hashlib.sha256(payload_source_revision().encode())
    for path in (Path(__file__), Path(arm.__file__)):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--draws", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--loo-draws", type=int, default=LOO_DRAWS)
    parser.add_argument("--payload-dir", type=Path, default=arm.DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--work-cache", type=Path, default=WORK_CACHE)
    parser.add_argument("--symbols", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--timeframes", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--skip-loo", action="store_true")
    parser.add_argument("--from-csv", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args(argv)
    out = args.out_dir
    paths = {
        "null": out / NULL_CSV.name,
        "loo": out / LOO_CSV.name,
        "controls": out / CONTROL_CSV.name,
        "cells": out / CELL_CSV.name,
        "summary": out / SUMMARY_PATH.name,
    }

    def _read(p: Path) -> pd.DataFrame | None:
        return pd.read_csv(p) if p.exists() else None

    if args.from_csv:
        frame = pd.read_csv(paths["null"])
        rows = [NullRow(**rec) for rec in frame.to_dict("records")]
        text = render_summary(
            rows,
            _read(paths["loo"]),
            _read(paths["controls"]),
            _read(paths["cells"]),
            draws=args.draws,
        )
        paths["summary"].write_text(text, encoding="utf-8")
        print(text)
        return 0

    started = time.monotonic()
    symbols = tuple(args.symbols.split(",")) if args.symbols else arm.SYMBOLS
    timeframes = tuple(args.timeframes.split(",")) if args.timeframes else arm.TIMEFRAMES
    payloads = arm.build_base_payloads(
        jobs=args.jobs, payload_dir=args.payload_dir, symbols=symbols, timeframes=timeframes
    )
    print(f"base 후보 {time.monotonic() - started:.0f}s · 칸 {len(payloads)}", flush=True)
    cells = _load_or_build_cells(payloads, jobs=args.jobs, draws=args.draws, cache=args.work_cache)
    print(f"팔 + 널 B {time.monotonic() - started:.0f}s", flush=True)

    rows, null_facts, real_facts = run_null_grid(payloads, cells, draws=args.draws)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dataclasses.asdict(r) for r in rows]).to_csv(paths["null"], index=False)

    judge_real = real_facts[JUDGE]
    judge_samples = {null: null_facts[(*JUDGE, null)] for null in NULLS}
    cell_frame = pd.DataFrame(
        [rec for null in NULLS for rec in cell_rows(judge_real, judge_samples[null], null)]
    )
    cell_frame.to_csv(paths["cells"], index=False)
    controls = pd.DataFrame(run_controls(judge_real, judge_samples))
    controls.to_csv(paths["controls"], index=False)
    print(f"표 · 통제 {time.monotonic() - started:.0f}s", flush=True)

    loo = None
    if not args.skip_loo:
        loo = pd.DataFrame(run_loo(payloads, cells, draws=args.loo_draws))
        loo.to_csv(paths["loo"], index=False)
    elapsed = time.monotonic() - started
    text = render_summary(rows, loo, controls, cell_frame, draws=args.draws, elapsed=elapsed)
    paths["summary"].write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
