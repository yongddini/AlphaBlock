"""WAN-424 §3: %K의 순수 기여를 손절폭 하한에서 떼어낸다 — 「필터가 일을 한 거냐 %K냐」.

§2에서 A 널(오더블록 탭 중 %K로 고름 vs 무작위 고름)이 **앞구간에서 구분되지 않았다**(p=0.14).
그런데 그 A 널은 **손절폭 하한 4%를 이미 깔고** 돌았다 — 하한이 탭의 86%를 쳐낸 뒤라(BTC 4h:
140 → 19탭) %K가 고를 여지가 거의 없었을 수 있다. 사용자 질문(2026-09-22): *「스토캐스틱이
의미 없다는 거냐 — 추가하니 괜찮았다매」*. 이 모듈이 그 자리를 가른다.

## 무엇을 하나 — 하한을 0·2·3·4%로 바꿔가며 2×2를 본다

각 하한 `F`에서 (판정 칸 ts4 · %K<25 · 채택 북 · 복리 끔 · 31종목 × 9TF):

* **팔** = `F` 통과 ∧ `%K<thr` (실제)
* **하한만** = `F` 통과 (%K 안 봄)
* **%K 순수 기여** = 팔 − 하한만 (거래당 net R 차)
* **A 널** = `F` 통과 풀에서 실제와 **같은 개수**를 무작위로(%K 안 봄) · 200반복 · p값

읽는 법:
* 하한을 **뺄수록**(0%로) %K 순수 기여가 **커지고 A 널이 유의**해지면 → %K가 실제로 고르는데
  4% 하한이 그걸 가리고 있었다(= %K 의미 있음).
* 하한을 빼도 %K 기여가 **그대로 약하면** → 값의 출처는 %K가 아니라 **넓은 손절 + 시간 청산**
  구조다(= §6의 「곱하면 값이 있다」는 참이되 그 값은 %K가 아니라 필터·구조의 몫).

🚨 **이건 §2의 판정을 뒤집지 않는다** — §2 B 널(진입 타이밍 전체가 무작위보다 낫다)은 그대로다.
이 표는 그 타이밍 우위의 **출처가 %K냐 필터냐**만 가른다.

## 캐시 · 비용

base 후보는 `wan424_payloads`(캐시)에서 온다. 팔 후보(하한 **0** 풀 = 가장 넓은 풀)는 다시
만들어야 한다(4% 아래 탭이 §2 캐시에 없다) — 단 **시각 널(B)은 안 만들어서** §2보다 싸다.
하한 0 풀을 한 번 만들면 모든 하한·문턱은 그 위의 **필터 + 배치**라 싸다.

```
uv run python -m backtest.wan424_stoch_isolation --jobs 4      # 팔 후보(하한0) + 표
uv run python -m backtest.wan424_stoch_isolation --from-csv    # 표만 다시
```

📌 **익절 유동성 축(`take_profit_liquidity`, WAN-373)** — 북 배치는 전부 `mn.place_facts`를 거치고
그 함수가 `harness.ADOPTED_TAKE_PROFIT_LIQUIDITY`를 명시해 넘긴다(이 팔은 익절선이 없어 시간 청산이
테이커지만, 축을 옛 기본값에 맡기지 않는다는 규약은 같다 — WAN-305).

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 엔진 소스 무변경) · 채택 좌표 아님 · 실거래 보류 유지.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import pickle
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan424_matched_null as mn
from backtest import wan424_stoch_ob_arm as arm
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS
from backtest.run import parse_date_ms
from backtest.substep import build_substeps
from backtest.wan169_leverage_book import IS_SEGMENT, OOS_SEGMENT, CellPayload
from backtest.zone_limit_backtest import _Candidate
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

REPORT_DIR = arm.REPORT_DIR
CSV_PATH = REPORT_DIR / "wan424_stoch_isolation.csv"
SUMMARY_PATH = REPORT_DIR / "wan424_stoch_isolation_summary.md"
WORK_CACHE = Path(__file__).resolve().parent / "cache" / "wan424_iso_work.pkl.gz"

SEED = 424
FLOORS: tuple[float, ...] = (0.0, 0.02, 0.03, 0.04)
HOLDS: tuple[int, ...] = (4, 8)
THRESHOLDS: tuple[float, ...] = (15.0, 20.0, 25.0)
JUDGE = (4, 25.0)
RAW_SEGMENTS = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS)
SEGMENTS = ("full", "is", "oos_warm")
DRAWS = 200


@dataclass(frozen=True)
class IsoCell:
    symbol: str
    timeframe: str
    boundary_ms: int
    pool: dict[int, dict[str, tuple[_Candidate, ...]]]
    """하한 0(가장 넓은) 팔 후보 — 보유 봉 → 구간 → 시간 청산 후보. 순서가 풀 인덱스다."""
    k_value: dict[str, tuple[float | None, ...]]
    """구간 → 풀 후보마다 직전 확정봉 %K(보유 봉 사이 불변 — 진입 시각이 같다)."""


def _cell_work(payload: CellPayload) -> IsoCell:
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
    for segment, window in windows.items():
        base = arm.arm_pool(payload.candidates.get(segment, ()), min_width=0.0)
        substeps = build_substeps(window.df_1m, htf_ms) if base else []
        derived = arm.derive_hold_arms(base, substeps=substeps, holds=HOLDS) if base else {}
        kept = derived.get(HOLDS[0], [])
        for h in HOLDS:
            cands = derived.get(h, [])
            assert [c.entry_time for c in cands] == [c.entry_time for c in kept]
            pool[h][segment] = tuple(cands)
        k_value[segment] = tuple(
            arm.k_before(times, kline, int(c.entry_time), htf_ms) for c in kept
        )
    return IsoCell(payload.symbol, payload.timeframe, payload.boundary_ms, pool, k_value)


def build_cells(payloads: Sequence[CellPayload], *, jobs: int) -> list[IsoCell]:
    if jobs <= 1:
        return [_cell_work(p) for p in payloads]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(_cell_work, payloads))


def _floor_idx(cell: IsoCell, hold: int, segment: str, floor: float) -> list[int]:
    return [i for i, c in enumerate(cell.pool[hold][segment]) if arm.stop_width(c) >= floor]


def _real_idx(cell: IsoCell, hold: int, segment: str, floor: float, thr: float) -> list[int]:
    kv = cell.k_value[segment]
    out: list[int] = []
    for i in _floor_idx(cell, hold, segment, floor):
        v = kv[i]
        if v is not None and v < thr:
            out.append(i)
    return out


@dataclass(frozen=True)
class IsoRow:
    floor: float
    hold: int
    threshold: float
    segment: str
    arm_trades: int
    arm_mean_r: float
    floor_trades: int
    floor_mean_r: float
    k_marginal: float
    null_mean_r: float
    p_value: float
    significant: bool


def _mean(facts: Sequence[mn.Fact]) -> float:
    return mn.mean_r(facts)


def run_grid(base: Sequence[CellPayload], cells: dict[tuple[str, str], IsoCell]) -> list[IsoRow]:
    rows: list[IsoRow] = []
    for floor in FLOORS:
        for hold in HOLDS:
            for thr in THRESHOLDS:
                arm_pay = _select(base, cells, hold=hold, floor=floor, thr=thr, mode="real")
                floor_pay = _select(base, cells, hold=hold, floor=floor, thr=thr, mode="floor")
                arm_facts = mn.place_facts(arm_pay, segments=SEGMENTS)
                floor_facts = mn.place_facts(floor_pay, segments=SEGMENTS)
                null_means: dict[str, list[float]] = {s: [] for s in SEGMENTS}
                for d in range(DRAWS):
                    np_ = _select(base, cells, hold=hold, floor=floor, thr=thr, mode="null", draw=d)
                    nf = mn.place_facts(np_, segments=SEGMENTS)
                    for s in SEGMENTS:
                        null_means[s].append(_mean(nf[s]))
                for s in SEGMENTS:
                    a_mean = _mean(arm_facts[s])
                    f_mean = _mean(floor_facts[s])
                    means = null_means[s]
                    rows.append(
                        IsoRow(
                            floor,
                            hold,
                            thr,
                            s,
                            len(arm_facts[s]),
                            a_mean,
                            len(floor_facts[s]),
                            f_mean,
                            a_mean - f_mean,
                            float(np.nanmean(means)),
                            mn.p_value(a_mean, means),
                            mn.significant(a_mean, means, len(arm_facts[s])),
                        )
                    )
                n_done = len(rows) // len(SEGMENTS)
                total = len(FLOORS) * len(HOLDS) * len(THRESHOLDS)
                print(
                    f"[{n_done}/{total}] 하한 {floor:.0%} · ts{hold} · %K<{thr:.0f} 완료",
                    flush=True,
                )
                # 조합이 끝날 때마다 성적을 바로 찍는다(진행 중에도 숫자가 보이게).
                for r in rows[-len(SEGMENTS) :]:
                    print(
                        f"    {r.segment:>8}: 팔 {r.arm_mean_r:+.4f}({r.arm_trades}) · "
                        f"하한만 {r.floor_mean_r:+.4f} · %K기여 {r.k_marginal:+.4f} · "
                        f"A널 {r.null_mean_r:+.4f} · p {r.p_value:.3f} "
                        f"{'✅' if r.significant else '—'}",
                        flush=True,
                    )
    return rows


def _select(
    base: Sequence[CellPayload],
    cells: dict[tuple[str, str], IsoCell],
    *,
    hold: int,
    floor: float,
    thr: float,
    mode: str,
    draw: int = 0,
) -> list[CellPayload]:
    out: list[CellPayload] = []
    for p in base:
        cell = cells[(p.symbol, p.timeframe)]
        kept: dict[str, tuple[_Candidate, ...]] = {}
        for raw in RAW_SEGMENTS:
            if mode == "real":
                idx = _real_idx(cell, hold, raw, floor, thr)
            elif mode == "floor":
                idx = _floor_idx(cell, hold, raw, floor)
            else:
                real = _real_idx(cell, hold, raw, floor, thr)
                idx = _null_idx_seeded(cell, hold, raw, floor, thr, real, draw)
            kept[raw] = tuple(cell.pool[hold][raw][i] for i in idx)
        out.append(
            dataclasses.replace(p, candidates=kept, reentry_candidates={}, arm_candidates={})
        )
    return out


def _null_idx_seeded(
    cell: IsoCell,
    hold: int,
    segment: str,
    floor: float,
    thr: float,
    real: Sequence[int],
    draw: int,
) -> list[int]:
    cands = cell.pool[hold][segment]
    pool_idx = _floor_idx(cell, hold, segment, floor)
    out: list[int] = []
    for s_name, lo, hi in mn.strata(segment, cell.boundary_ms):
        members = [i for i in pool_idx if mn._in_stratum(cands[i].trigger_time, lo, hi)]
        need = sum(1 for i in real if mn._in_stratum(cands[i].trigger_time, lo, hi))
        rng = mn._rng("iso", draw, cell.symbol, cell.timeframe, segment, s_name, hold, floor, thr)
        out.extend(rng.sample(members, min(need, len(members))))
    return out


def rows_to_frame(rows: Sequence[IsoRow]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(r) for r in rows])


def frame_to_rows(frame: pd.DataFrame) -> list[IsoRow]:
    return [
        IsoRow(**{**rec, "significant": bool(rec["significant"])})
        for rec in frame.to_dict("records")
    ]


def render_summary(rows: Sequence[IsoRow], *, elapsed: float | None = None) -> str:
    hold, thr = JUDGE
    idx = {(r.floor, r.hold, r.threshold, r.segment): r for r in rows}
    lines = [
        "# WAN-424 §3 — %K의 순수 기여를 손절폭 하한에서 떼어낸다",
        "",
        "판정 칸(ts4 · %K<25) · 채택 북 · 복리 끔 · 31종목 × 9TF · 200반복 · 시드 424.",
        "**팔 − 하한만 = %K 순수 기여**. A 널 = 하한 통과 풀에서 무작위 같은 개수.",
        "",
        "## 하한별 (판정 칸 · 각 구간)",
        "",
        "| 하한 | 구간 | 팔 (거래) | 하한만 (거래) | %K 순수 기여 | A 널 무작위 | p | 유의 |",
        "| -- | -- | --: | --: | --: | --: | --: | :-: |",
    ]
    for floor in FLOORS:
        for s in SEGMENTS:
            r = idx.get((floor, hold, thr, s))
            if r is None:
                continue
            lines.append(
                f"| {floor:.0%} | {s} | {r.arm_mean_r:+.4f} ({r.arm_trades}) | "
                f"{r.floor_mean_r:+.4f} ({r.floor_trades}) | **{r.k_marginal:+.4f}** | "
                f"{r.null_mean_r:+.4f} | {r.p_value:.3f} | {'✅' if r.significant else '—'} |"
            )
    lines += [
        "",
        "## 읽는 법",
        "",
        "- 하한을 **뺄수록**(0%) %K 순수 기여가 커지고 A 널이 유의해지면 → %K가 실제로 고르는데 "
        "4% 하한이 가리고 있었다.",
        "- 하한을 빼도 기여가 **그대로 약하면** → 값의 출처는 %K가 아니라 "
        "넓은 손절 + 시간 청산 구조.",
        "",
        "## 전체 격자(보유 × 문턱 · is/oos_warm)",
        "",
        "| 하한 | ts | %K | is 기여 | is p | oos 기여 | oos p |",
        "| -- | -- | -- | --: | --: | --: | --: |",
    ]
    for floor in FLOORS:
        for h in HOLDS:
            for t in THRESHOLDS:
                ri = idx.get((floor, h, t, "is"))
                ro = idx.get((floor, h, t, "oos_warm"))
                if ri is None or ro is None:
                    continue
                lines.append(
                    f"| {floor:.0%} | {h} | <{t:.0f} | {ri.k_marginal:+.4f} | {ri.p_value:.3f} "
                    f"| {ro.k_marginal:+.4f} | {ro.p_value:.3f} |"
                )
    lines += [
        "",
        "⚠️ §2 판정(B 널 = 진입 타이밍 전체가 무작위보다 낫다)은 이 표가 안 건드린다 — "
        "여기는 그 우위의 출처가 %K냐 필터냐만 가른다. 채택 좌표 아님 · `pen_5bp` 위 값.",
    ]
    if elapsed is not None:
        lines.append(f"\n실측 {elapsed:.0f}초.")
    return "\n".join(lines) + "\n"


def _load_or_build(payloads: Sequence[CellPayload], *, jobs: int, cache: Path) -> list[IsoCell]:
    rev = mn._revision()
    if cache.exists():
        with gzip.open(cache, "rb") as fh:
            saved = pickle.load(fh)
        if saved.get("revision") == rev:
            print(f"작업물 캐시 적중: {cache}", flush=True)
            return saved["cells"]  # type: ignore[no-any-return]
    cells = build_cells(payloads, jobs=jobs)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(cache, "wb") as fh:
        pickle.dump({"revision": rev, "cells": cells}, fh)
    return cells


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--payload-dir", type=Path, default=arm.DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--work-cache", type=Path, default=WORK_CACHE)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--timeframes", default=None)
    parser.add_argument("--from-csv", action="store_true")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = frame_to_rows(pd.read_csv(args.csv))
        args.summary.write_text(render_summary(rows), encoding="utf-8")
        print(render_summary(rows))
        return 0

    started = time.monotonic()
    symbols = tuple(args.symbols.split(",")) if args.symbols else arm.SYMBOLS
    timeframes = tuple(args.timeframes.split(",")) if args.timeframes else arm.TIMEFRAMES
    payloads = arm.build_base_payloads(
        jobs=args.jobs, payload_dir=args.payload_dir, symbols=symbols, timeframes=timeframes
    )
    print(f"base 후보 {time.monotonic() - started:.0f}s · 칸 {len(payloads)}", flush=True)
    cells = {
        (c.symbol, c.timeframe): c
        for c in _load_or_build(payloads, jobs=args.jobs, cache=args.work_cache)
    }
    print(f"팔 후보(하한0) {time.monotonic() - started:.0f}s", flush=True)
    rows = run_grid(payloads, cells)
    elapsed = time.monotonic() - started
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(args.csv, index=False)
    text = render_summary(rows, elapsed=elapsed)
    args.summary.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
