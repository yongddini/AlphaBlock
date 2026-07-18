"""WAN-90 Phase 1: MFE 분포 · E[러너] · 익절 R 스윕 — "1.5R이 맞는가".

[WAN-81](../docs/decisions/)이 익절을 "선 도달"에서 **고정 1.5R**로 바꿨지만, 그 1.5라는
값이 다른 값과 비교된 기록이 저장소에 없었다. 이 모듈이 두 가지를 잰다:

1. **거래가 실제로 어디까지 가는가**(MFE 분포). 채택 엔진은 승자를 1.5R에서 자르므로
   MFE가 그 목표에서 **검열**된다 — "익절 없이 어디까지 갔는가"를 보려면 익절을 끈
   실행이 필요하다. 그래서 이 모듈의 분포는 **익절을 끈(no-TP) 변형**을 **동시 1포지션
   시퀀싱 이전, 셋업(후보) 단위**로 잰다(시퀀싱은 포트폴리오 문제이지 이탈 성질이
   아니므로). 채택 진입가·손절·볼린저·오프셋·게이트는 전부 채택 기본값 그대로다.
2. **E[러너]** — 1.5R 도달 후 손절을 본절(진입가)로 옮긴 잔여 물량의 기대 R. 사용자
   제안(부분익절 + 본절스탑 + R재산정)의 손익 부호는 **오직 `E[러너]`가 1.5R보다 큰지**에
   달렸다(부분익절 비율과 무관 — 이슈 §2). MFE의 최댓값만으로는 경로 순서를 알 수 없어
   E[러너]를 못 내므로, 셋업별로 서브스텝 경로를 되짚어 **본절-러너**를 시뮬레이션한다.

그리고 확인용으로 `take_profit_r ∈ {1.0, 1.5, 2.0, 2.5, 3.0}` 스윕을 **비용 포함 순액**
으로 낸다(범용 CLI `backtest.run --tp-r`와 같은 `run_grid`). 과최적화 방지 원칙(이슈):
**MFE 분포로 사전 가설을 세운 뒤** 스윕은 IS→OOS 고원 확인용으로만 읽는다.

## 축

* **심볼 3개**: BTC·ETH·SOL(이슈 §2 명세).
* **TF 2개**: 15m·1h(공동 작업 TF, WAN-107).
* **구간**: 전 구간·IS(앞 2/3)·OOS(뒤 1/3).
* **렌즈**: 공식 `baseline`(WAN-104) — 주 수치. 민감도(`pen_5bp`)는 `--fill`로 별도.
* **창**: WAN-111/114/115/119/120과 **같은 못 박은 창**(2023-07-14~2026-07-15) — `--years`가
  미끄러지는 문제(CLAUDE.md)를 피한다.

## 이 표는 채택 엔진을 흔들지 않는다

`ConfluenceParams()`·`BacktestConfig` 기본값을 바꾸지 않는다. 분포는 익절만 끈 **측정
변형**이고, 스윕은 `take_profit_r`를 **축으로 명시**해 돈다. 채택 여부는 사용자 결정이다
(실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지).

## 재현

```
uv run python -m backtest.wan90_mfe_distribution              # 격자 재실행(15m은 길다)
uv run python -m backtest.wan90_mfe_distribution --tf 1h      # 1h만
uv run python -m backtest.wan90_mfe_distribution --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.harness import (
    BASELINE_FILL,
    DB_PATH,
    RunRow,
    build_config,
    detect_order_blocks,
    load_market_data,
    normalize_symbol,
    segments_for,
    slice_market,
)
from backtest.models import PositionSide
from backtest.run import Grid, RunOptions, parse_date_ms, run_grid
from backtest.substep import SubStep, build_substeps
from backtest.sweep import timeframe_to_ms
from backtest.zone_limit_backtest import build_zone_limit_candidates
from strategy.models import ConfluenceParams

# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

ALL_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
ALL_TFS: tuple[str, ...] = ("15m", "1h")

#: 채택 기본값의 익절 R(= 본절-러너 무장 지점, 이슈 §2가 1.5R로 고정).
ARM_R: float = 1.5

#: 확인용 익절 R 스윕(이슈 §3).
R_SWEEP: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5, 3.0)

#: MFE 히스토그램 R 버킷(이슈 §2 명세).
MFE_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (-math.inf, 0.5, "<0.5"),
    (0.5, 1.0, "0.5–1.0"),
    (1.0, 1.5, "1.0–1.5"),
    (1.5, 2.0, "1.5–2"),
    (2.0, 3.0, "2–3"),
    (3.0, 4.0, "3–4"),
    (4.0, 5.0, "4–5"),
    (5.0, math.inf, "5+"),
)

#: 1.5R 도달 거래가 이후 넘어간 R 문턱(이슈 §2).
REACH_THRESHOLDS: tuple[float, ...] = (2.0, 3.0, 4.0, 5.0)

#: 못 박은 창(WAN-111 이래 공통). CLI `--start`/`--end` 없이도 재현되도록 기본으로 박는다.
WINDOW_START = "2023-07-14"
WINDOW_END = "2026-07-15"

_REPORTS = Path("backtest/reports")
TRADES_CSV = _REPORTS / "wan90_mfe_trades.csv"
SWEEP_CSV = _REPORTS / "wan90_tpr_sweep.csv"
SUMMARY_MD = _REPORTS / "wan90_mfe_distribution_summary.md"


# --------------------------------------------------------------------------- #
# 이탈(excursion) + 본절-러너 시뮬레이션
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Excursion:
    """셋업 하나의 이탈 요약(비용 미반영, 순수 R 관측값)."""

    mfe_r: float
    """최대유리이탈(R). 1R = 진입가 → 무효화 경계."""
    mae_r: float
    """최대불리이탈(R)."""
    reached_arm: bool
    """보유 중 유리 이탈이 `arm_r`(1.5R)에 닿았는가."""
    runner_r: float | None
    """본절-러너의 실현 R. `reached_arm`일 때만 값이 있다.

    1.5R에 닿는 순간 손절을 본절(진입가)로 옮긴 잔여 물량이 결국 실현하는 R이다:
    이후 가격이 진입가로 되돌아오면 0R(본절 청산), 되돌아오지 않고 데이터 끝까지 가면
    마지막 종가의 R. 상방은 절단하지 않는다(오른쪽 꼬리가 이 값의 핵심이다).

    ⚠️ **그로스다** — 왕복 수수료·슬리피지를 빼지 않았다. 실제 순액은 1R당 약 0.1%p씩
    낮다(1.5R 전량 익절도 같은 비용을 내므로 **둘의 비교**에는 영향이 작다).
    """
    runner_open_at_end: bool
    """러너가 본절 청산 없이 **구간 끝까지 열려** 있었는가(그래서 `runner_r`이 마지막
    종가의 R). 참인 러너의 R은 **창 경계에 걸린 미실현 값**이라, IS/OOS 구간에서는 경계가
    인위적이다(변형 C PM 코멘트: "데이터끝 홀드는 실전 규칙이 아니다"). E[러너] 평균이
    이 소수 값에 끌려가므로 중앙값·상위기여도와 함께 읽는다."""


def simulate_excursion(
    steps: Sequence[SubStep],
    *,
    entry_price: float,
    stop_price: float,
    is_long: bool,
    arm_r: float = ARM_R,
) -> Excursion | None:
    """진입~청산 서브스텝 경로로 MFE/MAE와 본절-러너를 낸다. 1R을 못 재면 None.

    `steps`는 체결 스텝부터 청산 스텝까지(둘 다 포함)의 서브스텝이어야 한다 — 청산 이후는
    보지 않는다(look-ahead 금지). MFE/MAE 정의는 `backtest.substep`과 동일해 같은 경로에서
    같은 값을 낸다(`simulate_zone_limit_trade`의 결과와 셋업 단위로 일치한다 — 테스트가 고정).

    본절-러너: 유리 이탈이 처음 `arm_r`에 닿으면 손절을 진입가로 올린다. 그 뒤 어느 봉이든
    가격이 진입가에 닿으면(롱=저가≤진입가) 0R로 청산하고, 끝까지 안 닿으면 마지막 종가의
    R로 청산한다. 봉 내부 순서는 알 수 없으므로, 무장 봉에서 본절 터치가 동시에 성립하면
    **본절 청산(0R)으로 보수적으로** 처리한다(유리 이동 뒤 불리 이동을 가정).
    """
    if not steps:
        return None
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return None

    hold_high = -math.inf
    hold_low = math.inf
    armed = False
    runner_r: float | None = None
    for step in steps:
        hold_high = max(hold_high, step.high)
        hold_low = min(hold_low, step.low)
        fav_r = (step.high - entry_price) / risk if is_long else (entry_price - step.low) / risk
        if not armed and fav_r >= arm_r:
            armed = True
        if armed and runner_r is None:
            breakeven_hit = step.low <= entry_price if is_long else step.high >= entry_price
            if breakeven_hit:
                runner_r = 0.0
    if is_long:
        mfe_r = (hold_high - entry_price) / risk
        mae_r = (hold_low - entry_price) / risk
    else:
        mfe_r = (entry_price - hold_low) / risk
        mae_r = (entry_price - hold_high) / risk
    open_at_end = False
    if armed and runner_r is None:
        close = steps[-1].close
        runner_r = (close - entry_price) / risk if is_long else (entry_price - close) / risk
        open_at_end = True
    return Excursion(
        mfe_r=mfe_r,
        mae_r=mae_r,
        reached_arm=armed,
        runner_r=runner_r,
        runner_open_at_end=open_at_end,
    )


def _uncensored_params(base: ConfluenceParams) -> ConfluenceParams:
    """익절만 끈 채택 기본값 — 거래를 무효화 경계/데이터 끝까지 들고 가 MFE를 검열하지 않는다.

    `take_profit_mode="line"` + `use_line_take_profit=False`(기본)면 익절 목표가 `None`이라
    익절 청산이 없다(`backtest.zone_limit_backtest._resolve_take_profit`). 진입가·손절·
    볼린저·오프셋·RSI 게이트는 채택 기본값 그대로다.
    """
    return base.model_copy(update={"take_profit_mode": "line"})


# --------------------------------------------------------------------------- #
# 이탈 격자 실행
# --------------------------------------------------------------------------- #


def _trade_rows_for_cell(
    symbol: str, timeframe: str, *, start_ms: int, end_ms: int, oos: bool, db_path: str
) -> list[dict[str, object]]:
    """(심볼, TF)의 익절 없는 후보를 구간별로 시뮬레이션해 이탈 행을 낸다."""
    market = load_market_data(
        symbol,
        timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        need_1m=True,
        funding=False,  # 이탈은 비용 미반영이라 펀딩이 필요 없다.
        db_path=db_path,
    )
    if market.empty or market.df_1m.empty:
        return []
    cfg = build_config(timeframe, funding_enabled=False)
    params = _uncensored_params(ConfluenceParams())
    htf_ms = timeframe_to_ms(timeframe)
    rows: list[dict[str, object]] = []
    for segment in segments_for(oos=oos):
        window = slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        ob_result = detect_order_blocks(window)
        substeps = build_substeps(window.df_1m, htf_ms)
        substep_times = [s.time for s in substeps]
        candidates, _ = build_zone_limit_candidates(
            window.htf_df,
            window.df_1m,
            timeframe,
            params=params,
            cfg=cfg,
            order_block_result=ob_result,
        )
        for cand in candidates:
            lo = bisect.bisect_left(substep_times, cand.entry_time)
            hi = bisect.bisect_right(substep_times, cand.exit_time)
            is_long = cand.side is PositionSide.LONG
            exc = simulate_excursion(
                substeps[lo:hi],
                entry_price=cand.entry_price,
                stop_price=cand.stop_price,
                is_long=is_long,
            )
            if exc is None:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "segment": segment.name,
                    "entry_time": cand.entry_time,
                    "exit_reason": cand.reason.value,
                    "mfe_r": exc.mfe_r,
                    "mae_r": exc.mae_r,
                    "reached_arm": exc.reached_arm,
                    "runner_r": exc.runner_r,
                    "runner_open_at_end": exc.runner_open_at_end,
                }
            )
    return rows


def run_excursion_grid(
    symbols: Sequence[str], timeframes: Sequence[str], *, start_ms: int, end_ms: int, db_path: str
) -> pd.DataFrame:
    """모든 (심볼, TF)의 이탈 행을 모아 DataFrame으로."""
    rows: list[dict[str, object]] = []
    for timeframe in timeframes:
        for symbol in symbols:
            cell = _trade_rows_for_cell(
                symbol, timeframe, start_ms=start_ms, end_ms=end_ms, oos=True, db_path=db_path
            )
            print(f"[wan90] {symbol} {timeframe}: 이탈 후보 {len(cell)}건")
            rows.extend(cell)
    return pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "timeframe",
            "segment",
            "entry_time",
            "exit_reason",
            "mfe_r",
            "mae_r",
            "reached_arm",
            "runner_r",
            "runner_open_at_end",
        ],
    )


# --------------------------------------------------------------------------- #
# 익절 R 스윕 (범용 CLI와 같은 run_grid)
# --------------------------------------------------------------------------- #


def run_sweep(
    symbols: Sequence[str], timeframes: Sequence[str], *, start_ms: int, end_ms: int, jobs: int
) -> pd.DataFrame:
    """`take_profit_r` 스윕을 비용 포함 순액으로. 채택 엔진(검열됨)·baseline 렌즈."""
    grid = Grid(
        symbols=tuple(symbols),
        timeframes=tuple(timeframes),
        entry_modes=("zone_limit",),
        take_profit_rs=R_SWEEP,
        offsets_bps=(ConfluenceParams().zone_limit_offset_bps,),
        fills=(BASELINE_FILL,),
    )
    options = RunOptions(start_ms=start_ms, end_ms=end_ms, oos=True)
    rows = run_grid(grid, options, log=True, jobs=jobs)
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(RunRow.model_fields))


# --------------------------------------------------------------------------- #
# 집계 · 렌더
# --------------------------------------------------------------------------- #


def _bucket_label(mfe_r: float) -> str:
    for lo, hi, label in MFE_BUCKETS:
        if lo <= mfe_r < hi:
            return label
    return MFE_BUCKETS[-1][2]


def mfe_histogram(trades: pd.DataFrame) -> pd.DataFrame:
    """구간별 MFE 버킷 비율(%). 전 거래 기준."""
    frames: list[dict[str, object]] = []
    for (tf, seg), grp in trades.groupby(["timeframe", "segment"], sort=False):
        n = len(grp)
        row: dict[str, object] = {"timeframe": tf, "segment": seg, "trades": n}
        counts = grp["mfe_r"].map(_bucket_label).value_counts()
        for _, _, label in MFE_BUCKETS:
            row[label] = round(100.0 * counts.get(label, 0) / n, 1) if n else 0.0
        frames.append(row)
    return pd.DataFrame(frames)


def runner_table(trades: pd.DataFrame) -> pd.DataFrame:
    """1.5R 도달 거래의 이후 도달 비율·본절 복귀 비율·E[러너](심볼·TF·구간별).

    `hit1.5R%`는 **전 거래** 중 1.5R을 찍은 비율, `→NR%`는 **그 1.5R 도달 거래 중** MFE가
    N R 이상 간 조건부 비율이다(분모가 다르다). `breakeven%`는 본절로 되돌아온 비율,
    `openEnd%`는 러너가 구간 끝까지 열려 있던(경계에 걸린 미실현) 비율이다. E[러너]는
    이슈 §2 부호 판정에 쓰는 **평균**이지만 창 경계·소수 대박에 끌리므로 **중앙값**과
    **상위3 기여도**(양(+) 러너 R 합 중 상위 3건이 차지하는 %)를 반드시 병기한다.
    """
    frames: list[dict[str, object]] = []
    for (tf, seg, sym), grp in trades.groupby(["timeframe", "segment", "symbol"], sort=False):
        n = len(grp)
        armed = grp[grp["reached_arm"]]
        n_arm = len(armed)
        runners = armed["runner_r"].astype(float)
        pos = runners[runners > 0.0].sort_values(ascending=False)
        top3_share = (
            round(100.0 * pos.head(3).sum() / pos.sum(), 1)
            if float(pos.sum()) > 0
            else float("nan")
        )
        row: dict[str, object] = {
            "timeframe": tf,
            "segment": seg,
            "symbol": sym,
            "trades": n,
            "hit1.5R%": round(100.0 * n_arm / n, 1) if n else 0.0,
        }
        for thr in REACH_THRESHOLDS:
            hit = int((armed["mfe_r"] >= thr).sum())
            row[f"→{thr:g}R%"] = round(100.0 * hit / n_arm, 1) if n_arm else 0.0
        be = int((armed["runner_r"] == 0.0).sum())
        row["breakeven%"] = round(100.0 * be / n_arm, 1) if n_arm else 0.0
        row["openEnd%"] = (
            round(100.0 * int(armed["runner_open_at_end"].sum()) / n_arm, 1) if n_arm else 0.0
        )
        row["Erun_mean"] = round(float(runners.mean()), 3) if n_arm else float("nan")
        row["Erun_med"] = round(float(runners.median()), 3) if n_arm else float("nan")
        row["top3%"] = top3_share
        frames.append(row)
    return pd.DataFrame(frames)


def _fmt_df(df: pd.DataFrame) -> str:
    """의존성 없이 DataFrame을 마크다운 파이프 표로 렌더한다(`tabulate` 미설치)."""
    if df.empty:
        return "_(빈 표)_"

    def cell(value: object) -> str:
        if isinstance(value, float):
            if math.isnan(value):
                return "—"
            return f"{value:g}"
        return str(value)

    headers = [str(c) for c in df.columns]
    body = [[cell(v) for v in row] for row in df.itertuples(index=False, name=None)]
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    rows = ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join([head, sep, *rows])


def _sweep_pivot(sweep: pd.DataFrame, timeframe: str, segment: str) -> pd.DataFrame:
    """한 (TF, 구간)의 심볼×R total_return(%) 피벗 + 심볼평균."""
    sub = sweep[(sweep["timeframe"] == timeframe) & (sweep["segment"] == segment)]
    if sub.empty:
        return pd.DataFrame()
    piv = sub.pivot_table(
        index="symbol", columns="take_profit_r", values="total_return", aggfunc="first"
    )
    piv = (piv * 100).round(2)
    piv.loc["심볼평균"] = piv.mean().round(2)
    piv = piv.reset_index()
    piv.columns = ["symbol"] + [f"{c:g}R%" for c in piv.columns[1:]]
    return piv


def _symbol_mean_runner(runner: pd.DataFrame, timeframe: str, segment: str, column: str) -> float:
    """구간의 심볼별 E[러너] 값(`Erun_mean` 또는 `Erun_med`)을 심볼평균한다."""
    sub = runner[(runner["timeframe"] == timeframe) & (runner["segment"] == segment)]
    vals = sub[column].dropna()
    return float(vals.mean()) if len(vals) else float("nan")


def _best_r_by_symbol_mean(sweep: pd.DataFrame, timeframe: str, segment: str) -> float | None:
    piv = _sweep_pivot(sweep, timeframe, segment)
    if piv.empty:
        return None
    avg = piv[piv["symbol"] == "심볼평균"]
    if avg.empty:
        return None
    cols = [c for c in piv.columns if c.endswith("R%")]
    best = max(cols, key=lambda c: float(avg[c].iloc[0]))
    return float(best[:-2])


def _judgment(runner: pd.DataFrame, sweep: pd.DataFrame, timeframe: str) -> str:
    """(a)/(b)/(c) 판정 — E[러너] vs 1.5R을 **강건 지표**로 읽고 IS→OOS 스윕 안정성과 대조.

    §2 부호식은 평균 E[러너]를 쓰지만, 평균은 창 경계 미실현·소수 대박에 끌린다. 그래서
    **강건 신호는 OOS 중앙값 E[러너]**로 잡고(대부분 본절 복귀면 0R), 평균은 병기한다.
    스윕은 **IS 최적 R이 OOS에서 유지되는지**가 과최적화 판별의 핵심(이슈 §4).
    """
    er_mean = _symbol_mean_runner(runner, timeframe, "oos", "Erun_mean")
    er_med = _symbol_mean_runner(runner, timeframe, "oos", "Erun_med")
    best_is = _best_r_by_symbol_mean(sweep, timeframe, "is")
    best_oos = _best_r_by_symbol_mean(sweep, timeframe, "oos")
    if math.isnan(er_med) or best_is is None or best_oos is None:
        return f"- **{timeframe}**: 판정 불가(표본 부족)."
    runner_beats = er_med > ARM_R  # 강건(중앙값) 기준
    sweep_stable_far = best_is > ARM_R and best_oos > ARM_R
    if runner_beats and sweep_stable_far:
        verdict = "(a) 목표를 멀리 — 강건 E[러너]>1.5R이고 스윕도 IS·OOS 모두 더 먼 R 지지"
    elif not runner_beats and best_is <= ARM_R:
        verdict = "(b) 현행 1.5R 유지 — 강건 E[러너]<1.5R이고 IS 스윕도 1.5 이하"
    elif not runner_beats and best_is > ARM_R and best_is != best_oos:
        verdict = (
            "(b·과최적화 경계) 강건 E[러너]<1.5R이고, IS가 고른 먼 R이 OOS에서 무너진다 "
            "→ 먼 목표는 IS 과최적화. 현행 1.5R 유지"
        )
    else:
        verdict = "(c) 엇갈림 — E[러너]와 스윕이 다른 방향(신호가 약하다는 증거)"
    return (
        f"- **{timeframe}**: {verdict}. "
        f"강건 E[러너](OOS 중앙값 심볼평균)={er_med:.3f}R · 평균={er_mean:.3f}R, "
        f"IS 최적 R={best_is:g}, OOS 최적 R={best_oos:g} "
        f"({'유지' if best_is == best_oos else '이동 — 과최적화 신호'})."
    )


def render_summary(trades: pd.DataFrame, sweep: pd.DataFrame) -> str:
    hist = mfe_histogram(trades)
    runner = runner_table(trades)
    lines: list[str] = []
    lines.append("# WAN-90 Phase 1 — MFE 분포 · E[러너] · 익절 R 스윕\n")
    lines.append(
        "> 익절만 끈(no-TP) 측정 변형에서 **셋업(후보) 단위**로 잰 이탈 분포다. 채택 엔진은 "
        "승자를 1.5R에서 자르므로 MFE가 검열되는데, 이 표는 그 검열 없이 '거래가 실제로 "
        "어디까지 가는가'를 본다. 진입가·손절·볼린저·오프셋·게이트는 채택 기본값 그대로이고, "
        "**기본값은 바꾸지 않았다**(채택은 사용자 결정, 실거래 보류 유지).\n"
    )
    lines.append(
        "> ⚠️ MFE/MAE·E[러너]는 **그로스 R**(비용 미반영)이다. 스윕(§3)만 비용 포함 순액이다. "
        "E[러너]의 본절(0R)·1.5R 전량 익절은 같은 왕복 비용을 내므로 **둘의 비교**에는 영향이 "
        "작다.\n"
    )

    lines.append("\n## 1. MFE 히스토그램 (전 거래, R 버킷 %)\n")
    lines.append(_fmt_df(hist))

    lines.append("\n\n## 2. 1.5R 도달 이후 · E[러너] (심볼·TF·구간별)\n")
    lines.append(
        "`hit1.5R%`는 전 거래 중 1.5R을 찍은 비율, `→NR%`는 그 1.5R 도달 거래 중 MFE가 N R "
        "이상 간 조건부 비율(분모가 다르다), `breakeven%`는 본절로 되돌아온 비율, `openEnd%`는 "
        "러너가 구간 끝까지 열려 있던(경계에 걸린 미실현) 비율이다. E[러너]는 §2 부호식에 쓰는 "
        "**평균**(`Erun_mean`)과 강건 **중앙값**(`Erun_med`)·**상위3 기여도**(`top3%`)를 함께 "
        "본다 — 평균은 창 경계 미실현·소수 대박에 끌리므로 단독 인용 금지.\n"
    )
    lines.append(_fmt_df(runner))

    lines.append("\n\n## 3. 익절 R 스윕 — total_return %(비용 포함 순액, baseline 렌즈)\n")
    for tf in sorted(sweep["timeframe"].unique(), key=lambda t: timeframe_to_ms(str(t))):
        for seg in ("is", "oos"):
            piv = _sweep_pivot(sweep, tf, seg)
            if piv.empty:
                continue
            lines.append(f"\n**{tf} · {seg.upper()}**\n")
            lines.append(_fmt_df(piv))
            lines.append("")

    lines.append("\n## 4. 판정 (a/b/c) — MFE·E[러너] vs 스윕\n")
    for tf in sorted(sweep["timeframe"].unique(), key=lambda t: timeframe_to_ms(str(t))):
        lines.append(_judgment(runner, sweep, str(tf)))
    lines.append(
        "\n판정 기준(이슈 §4): (a) `E[러너]>1.5R`이고 스윕도 더 먼 R 지지 → 목표를 멀리. "
        "(b) `E[러너]<1.5R`이고 스윕도 1.5 부근 → 현행 유지. (c) 엇갈리면 그 사실을 보고 "
        "(신호가 약하다는 증거). **과최적화 방지**: OOS 순위로 확인하고 R−수익 곡선이 고원인지 "
        "뾰족한지 본다.\n"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 진입점
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="WAN-90 MFE 분포·E[러너]·익절 R 스윕")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument("--symbol", default=None, help="심볼 콤마 목록(기본 BTC·ETH·SOL)")
    parser.add_argument("--tf", default=None, help="TF 콤마 목록(기본 15m·1h)")
    parser.add_argument("--jobs", type=int, default=1, help="스윕 병렬 워커 수(기본 1)")
    parser.add_argument("--db-path", default=DB_PATH)
    args = parser.parse_args(argv)

    symbols = (
        tuple(normalize_symbol(s) for s in args.symbol.split(",")) if args.symbol else ALL_SYMBOLS
    )
    timeframes = tuple(args.tf.split(",")) if args.tf else ALL_TFS

    if args.from_csv:
        trades = pd.read_csv(TRADES_CSV)
        sweep = pd.read_csv(SWEEP_CSV)
    else:
        start_ms = parse_date_ms(WINDOW_START)
        end_ms = parse_date_ms(WINDOW_END)
        trades = run_excursion_grid(
            symbols, timeframes, start_ms=start_ms, end_ms=end_ms, db_path=args.db_path
        )
        sweep = run_sweep(symbols, timeframes, start_ms=start_ms, end_ms=end_ms, jobs=args.jobs)
        _REPORTS.mkdir(parents=True, exist_ok=True)
        trades.to_csv(TRADES_CSV, index=False)
        sweep.to_csv(SWEEP_CSV, index=False)

    summary = render_summary(trades, sweep)
    SUMMARY_MD.write_text(summary, encoding="utf-8")
    print(f"\n[wan90] 요약 저장 → {SUMMARY_MD}")


if __name__ == "__main__":
    main()
