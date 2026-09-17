"""WAN-419 — 「존에 닿은 뒤 양봉을 확인하고 진입」 1봉 대 2봉.

§0 탭↔체결 간격 · §1 선별력과 밀림 배수.

## 한 줄

사용자 사양(2026-09-17): *「봉에 닿으면 N+1, N+2 모두 양봉이 뜨는지를 확인해. 그게 아니면 그
존은 진입하지 않는걸로해. 근데 N+1 양봉 확인 후 진입하는것도 보고싶어」*. 탭 봉을 `N`이라 하고
팔이 둘이다:

* `1봉확인` — `N+1`이 양봉이면 `N+2` 시가에 시장가.
* `2봉확인` — `N+1`·`N+2`가 **둘 다** 양봉이면 `N+3` 시가에 시장가.

양봉 = **종가 ≥ 시가**(사용자 결정 「동가도 양봉으로 쳐」 — 동가로 판정이 갈린 건수를 따로 센다).
기회는 한 번뿐이다(첫 탭만 · 창을 넘으면 그 존은 끝).

## 🚨 이 모듈은 팔을 만들지 않는다 — 라벨링과 집계뿐이다

모집단은 **WAN-375 셋업 표 그대로**(`wan402.load_population` — `band` · 첫 탭만 · 가드 통과 ·
데이터 끝 제외 · 채택 회계 net R). 새로 만들지 않는다. 읽는 것은 **상위TF 봉**뿐이다(1분봉 없음).
실제로 늦게 사는 팔은 2단계이고 **북에서** 잰다(WAN-341).

## 갈래 — 팔마다 (`K` = 그 팔이 보는 마지막 봉 · 진입 시각 = `N+K+1` 시가)

* **(D)** 기준 팔 체결이 진입 시각 이후 — 「확인을 기다린 대가」와 「지정가를 안 기다린 이득」이
  섞이는 부류(§0). 정본 판정에서 **빼고**, 넣은 판을 병기한다.
* **(C)** `K` 마감 전에 이미 청산(익절 끈 판의 손절) — 구조적으로 못 기다린다. 🚨 안 떼면
  「빨리 손절난 거래가 손절났다」는 동어반복이 된다(WAN-383 §1).
* **(A)/(B)** `K`까지 살아 있고 조건 충족/미충족 — **선별력의 정직한 자는 (A) 대 (B)다.**

## 판정선 — 착수 전에 코드 상수로 못 박음 (결과를 보고 옮기지 않는다 · WAN-161)

1. **밀림 게이트** — (A)의 1R 배수 중앙값 ≥ `DRIFT_GATE`(1.55, WAN-386 `1_봉마감` 실측)이면 닫는다.
2. **선별 게이트** — (A)−(B)의 net R 차가 2σ를 판정 점 8개(TF 4 × 팔 2)로 Bonferroni 보정한
   z를 넘고 노이즈선(±0.005R) 밖이며 **양수**일 것.
3. **폭락일 통제** — WAN-408 최악 10일(진입일 또는 청산일)을 뺀 판 **그리고** WAN-410 「그날
   실현 net R」 버킷 안 표본 가중 합에서 부호가 유지될 것.

재현::

    uv run python -m backtest.wan419_bullish_confirmation            # 라벨링 + 집계
    uv run python -m backtest.wan419_bullish_confirmation --from-csv # 요약만(캐시에서)

측정 전용 · 엔진·기본값·토대 불변 · 핀 없음(WAN-305) · 전부 `baseline`(낙관) 렌즈 위 값.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from backtest import harness
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan375_conditional_rr import worst_days
from backtest.wan402_concurrent_breaks import load_population, population_census
from backtest.wan408_loss_clustering import (
    REALIZED_BUCKETS,
    TradeFact,
    _bucket_label_realized,
    _bucket_order_realized,
)
from backtest.wan410_daily_loss_circuit_breaker import realized_r_before
from common.timefmt import kst_day_key

REPORTS_DIR = Path("backtest/reports")
GAP_CSV = REPORTS_DIR / "wan419_tap_fill_gap.csv"
ARM_CSV = REPORTS_DIR / "wan419_arm_branches.csv"
SECOND_BAR_CSV = REPORTS_DIR / "wan419_second_bar_tradeoff.csv"
TAP_BAR_CSV = REPORTS_DIR / "wan419_tap_bar_reference.csv"
DOJI_CSV = REPORTS_DIR / "wan419_doji_flips.csv"
STRATA_CSV = REPORTS_DIR / "wan419_realized_strata.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan419_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan419_bullish_confirmation_summary.md"
#: 셋업 단위 라벨 원자료 — 커밋하지 않는다(`backtest/cache/`는 gitignore).
LABELS_CSV = Path("backtest/cache/wan419/labels.csv.gz")

SEGMENT_IS = harness.SEGMENT_IS
SEGMENT_OOS_WARM = harness.SEGMENT_OOS_WARM
SEGMENTS: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS_WARM)
PRIMARY_SEGMENT = SEGMENT_OOS_WARM

# --------------------------------------------------------------------------- #
# 착수 전에 못 박은 상수 — 결과를 보고 옮기지 않는다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    name: str
    confirm_bars: int
    """`K` — 조건을 보는 마지막 봉(탭 봉 `N` 기준 오프셋). 진입은 `N+K+1` 시가."""


ARM_ONE = Arm("1봉확인", 1)
ARM_TWO = Arm("2봉확인", 2)
ARMS: tuple[Arm, ...] = (ARM_ONE, ARM_TWO)
TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES

DRIFT_GATE = 1.55
"""(A)의 1R 배수 중앙값이 이 이상이면 그 팔은 닫는다 — WAN-386 `1_봉마감`이 1.55배에서 졌다."""
NOISE_R = 0.005
SIGMA_MULTIPLE = 2.0
TESTS = len(TIMEFRAMES) * len(ARMS)
"""판정 점 = TF 4 × 팔 2. 🚨 참고 열(탭 봉 양봉)은 판정 점이 아니므로 넣지 않는다."""
MIN_GROUP_N = 100
"""(A)·(B) 어느 쪽이든 이보다 적으면 그 칸은 판정 불가(부호를 내지 않는다)."""
WORST_DAYS = 10
EXPECTED_SETUPS: dict[str, int] = {SEGMENT_IS: 22_725, SEGMENT_OOS_WARM: 10_272}
"""이슈 본문이 밝힌 WAN-375 모집단 크기 — 검산 (a)가 값으로 대조한다."""

BRANCH_A = "A"
BRANCH_B = "B"
BRANCH_C = "C"
BRANCH_D = "D"
BRANCHES: tuple[str, ...] = (BRANCH_A, BRANCH_B, BRANCH_C, BRANCH_D)

VARIANT_EXCL_D = "D 제외(정본)"
VARIANT_INCL_D = "D 포함(병기)"
VARIANTS: tuple[str, ...] = (VARIANT_EXCL_D, VARIANT_INCL_D)

VERDICT_PASS = "2단계로 넘김(세 관문 통과)"
VERDICT_DRIFT = "닫음 — 밀림 게이트"
VERDICT_NO_SELECTION = "닫음 — 선별력 부호 미정"
VERDICT_REVERSE = "닫음 — 역방향(조건 충족 쪽이 더 나쁘다)"
VERDICT_CRASH_PROXY = "닫음 — 폭락일 대리변수(통제하면 부호가 안 남는다)"
VERDICT_UNDECIDED = "판정 불가(표본)"

GAP_BUCKETS: tuple[str, ...] = ("0봉", "1봉", "2봉", "3봉+")
BAR_WINDOW_PAD_MS = 3 * 24 * 3_600_000
"""창 끝 뒤 여유 — 창 마지막 날 탭의 `N+3` 봉까지 읽기 위해서다(없으면 「데이터 끝」으로 센다)."""


def decision_z() -> float:
    """2σ(양측 α ≈ 4.55%)를 판정 점 개수(`TESTS`)로 Bonferroni 보정한 z."""
    alpha = 2.0 * (1.0 - NormalDist().cdf(SIGMA_MULTIPLE))
    return NormalDist().inv_cdf(1.0 - alpha / (2.0 * TESTS))


def is_bullish(open_: np.ndarray, close: np.ndarray, *, allow_doji: bool = True) -> np.ndarray:
    """양봉 판정 — 사용자 결정은 `종가 ≥ 시가`(동가 포함). `allow_doji=False`는 동가 계수 전용."""
    return (close >= open_) if allow_doji else (close > open_)


# --------------------------------------------------------------------------- #
# 봉 읽기 — 칸마다 상위TF 봉(시각 · 시가 · 종가)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _BarTask:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def load_bars(task: _BarTask) -> tuple[str, str, pd.DataFrame]:
    market = harness.load_market_data(
        harness.normalize_symbol(task.symbol),
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=False,
        funding=False,
    )
    frame = market.htf_df[["open_time", "open", "close"]].copy()
    return harness.normalize_symbol(task.symbol), task.timeframe, frame


def load_all_bars(
    pairs: Sequence[tuple[str, str]], *, jobs: int = 1
) -> dict[tuple[str, str], pd.DataFrame]:
    """칸마다 봉. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    start_ms = parse_date_ms(harness.DEFAULT_START)
    end_ms = parse_date_ms(harness.DEFAULT_END) + BAR_WINDOW_PAD_MS
    tasks = [_BarTask(s, tf, start_ms, end_ms) for s, tf in pairs]
    if jobs <= 1:
        results = [load_bars(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            results = list(executor.map(load_bars, tasks))
    return {(s, tf): frame for s, tf, frame in results}


# --------------------------------------------------------------------------- #
# 라벨링 — 🚨 셋업 원열은 손대지 않는다(검산 (b))
# --------------------------------------------------------------------------- #


def _lookup(bars: pd.DataFrame, times: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """시각 → (찾음, 시가, 종가). 🚨 위치가 아니라 **시각이 정확히 같은 봉**만 찾는다 — 구멍이
    있으면 옆 봉을 주워 오지 않고 「없음」이 된다."""
    ot = bars["open_time"].to_numpy(dtype=np.int64)
    idx = np.searchsorted(ot, times)
    safe = np.clip(idx, 0, max(len(ot) - 1, 0))
    found = (idx < len(ot)) & (ot[safe] == times) if len(ot) else np.zeros(len(times), bool)
    opens = np.where(found, bars["open"].to_numpy(dtype=float)[safe], np.nan)
    closes = np.where(found, bars["close"].to_numpy(dtype=float)[safe], np.nan)
    return found, opens, closes


def label_group(group: pd.DataFrame, bars: pd.DataFrame, tf_ms: int) -> pd.DataFrame:
    """한 칸의 셋업에 라벨을 붙인다(새 열만 더한다)."""
    out = pd.DataFrame(index=group.index)
    trig = group["trigger_time"].to_numpy(dtype=np.int64)
    entry = group["entry_time"].to_numpy(dtype=np.int64)
    exit_no_tp = group["exit_time"].to_numpy(dtype=np.int64)
    tp_exit = group["tp_on_exit_time"].to_numpy(dtype=np.int64)
    is_tp = (group["tp_on_reason"] == "take_profit").to_numpy()
    entry_price = group["entry_price"].to_numpy(dtype=float)
    stop = group["stop_price"].to_numpy(dtype=float)

    gap = (entry - trig) // tf_ms
    out["gap_bars"] = gap
    found = {}
    opens = {}
    closes = {}
    for k in range(0, 4):
        f, o, c = _lookup(bars, trig + k * tf_ms)
        found[k], opens[k], closes[k] = f, o, c
        out[f"bar{k}_found"] = f
        out[f"bar{k}_open"] = o
        out[f"bar{k}_close"] = c
    out["tap_bar_bullish"] = found[0] & is_bullish(opens[0], closes[0])
    out["tap_bar_doji"] = found[0] & (closes[0] == opens[0])

    base_risk = entry_price - stop
    for arm in ARMS:
        k = arm.confirm_bars
        tag = f"k{k}"
        entry_open_time = trig + (k + 1) * tf_ms
        data_ok = np.ones(len(group), bool)
        cond = np.ones(len(group), bool)
        strict = np.ones(len(group), bool)
        for j in range(1, k + 2):
            data_ok &= found[j]
        for j in range(1, k + 1):
            cond &= is_bullish(opens[j], closes[j])
            strict &= is_bullish(opens[j], closes[j], allow_doji=False)
        cond &= data_ok
        strict &= data_ok
        is_d = entry >= entry_open_time
        is_c = (~is_d) & ((exit_no_tp < entry_open_time) | ~data_ok)
        branch = np.where(
            is_d, BRANCH_D, np.where(is_c, BRANCH_C, np.where(cond, BRANCH_A, BRANCH_B))
        )
        conf_price = opens[k + 1]
        mult = (conf_price - stop) / base_risk
        out[f"{tag}_entry_time"] = entry_open_time
        out[f"{tag}_label_last_close"] = trig + (k + 1) * tf_ms
        out[f"{tag}_data_ok"] = data_ok
        out[f"{tag}_cond"] = cond
        out[f"{tag}_cond_strict"] = strict
        out[f"{tag}_branch"] = branch
        out[f"{tag}_c_data_end"] = is_c & ~data_ok
        out[f"{tag}_conf_price"] = conf_price
        out[f"{tag}_mult"] = mult
        out[f"{tag}_tp_before"] = is_tp & (tp_exit < entry_open_time)
    return out


def label_population(
    population: pd.DataFrame, bars_by: dict[tuple[str, str], pd.DataFrame]
) -> pd.DataFrame:
    """전 칸 라벨. 원열 + 새 열(원열은 그대로)."""
    parts = []
    for (symbol, tf), group in population.groupby(["symbol", "timeframe"], sort=False):
        bars = bars_by.get((str(symbol), str(tf)))
        if bars is None:
            bars = pd.DataFrame({"open_time": [], "open": [], "close": []})
        parts.append(label_group(group, bars, timeframe_to_ms(str(tf))))
    labels = pd.concat(parts).loc[population.index]
    return pd.concat([population, labels], axis=1)


def attach_controls(labeled: pd.DataFrame, *, bad_days: Sequence[str]) -> pd.DataFrame:
    """WAN-410 축 · KST 진입일/청산일 · 폭락일 표식(WAN-417 `attach_controls`와 같은 자)."""
    out = labeled.copy()
    facts = [
        TradeFact(
            symbol=str(rec["symbol"]),
            timeframe=str(rec["timeframe"]),
            entry_time=int(rec["entry_time"]),
            exit_time=int(rec["tp_on_exit_time"]),
            is_stop=bool(rec["is_stop"]),
            net_r=float(rec["net_r"]),
            is_reentry=False,
        )
        for rec in out.to_dict("records")
    ]
    out["realized_today_before"] = realized_r_before(facts, convention="settled")
    out["realized_bucket"] = out["realized_today_before"].map(
        lambda v: _bucket_label_realized(float(v), REALIZED_BUCKETS)
    )
    out["realized_order"] = out["realized_today_before"].map(
        lambda v: _bucket_order_realized(float(v), REALIZED_BUCKETS)
    )
    out["entry_day"] = out["entry_time"].map(lambda ms: kst_day_key(int(ms)))
    out["exit_day"] = out["tp_on_exit_time"].map(lambda ms: kst_day_key(int(ms)))
    bad = set(bad_days)
    out["is_crash_day"] = out["entry_day"].isin(bad) | out["exit_day"].isin(bad)
    return out


# --------------------------------------------------------------------------- #
# 비용 — 2단계 예고(확인 진입은 테이커 + 슬리피지 · 기준은 메이커)
# --------------------------------------------------------------------------- #


def entry_cost_delta_r(frame: pd.DataFrame, k: int) -> pd.Series:
    """(확인 진입 테이커+슬리피지 비용 ÷ 확인 진입 1R) − (기준 메이커 비용 ÷ 기준 1R).

    요율은 채택 설정에서 읽는다(리터럴을 다시 적지 않는다). 손절폭은 **이 표에서** 계산한다."""
    cfg = harness.build_config(str(frame["timeframe"].iloc[0])) if len(frame) else None
    if cfg is None:
        return pd.Series(dtype=float)
    taker = float(cfg.fee_rate) + float(cfg.slippage)
    maker = float(cfg.fee_rate if cfg.maker_fee_rate is None else cfg.maker_fee_rate)
    conf = frame[f"k{k}_conf_price"]
    stop = frame["stop_price"]
    base = frame["entry_price"]
    return taker * conf / (conf - stop) - maker * base / (base - stop)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _mean(values: pd.Series) -> float | None:
    return float(values.mean()) if len(values) else None


def _se(values: pd.Series) -> float | None:
    return float(values.std(ddof=1) / math.sqrt(len(values))) if len(values) >= 2 else None


def _delta(a: pd.Series, b: pd.Series) -> tuple[float | None, float | None]:
    if len(a) < 2 or len(b) < 2:
        return None, None
    delta = float(a.mean() - b.mean())
    sigma = math.sqrt(float(_se(a) or 0.0) ** 2 + float(_se(b) or 0.0) ** 2)
    return delta, sigma


@dataclass(frozen=True)
class GapRow:
    segment: str
    timeframe: str
    n: int
    gap_0: int
    gap_1: int
    gap_2: int
    gap_3plus: int
    share_0: float
    d_one: int
    """`1봉확인`의 (D) — 체결이 `N+2` 시가 이후."""
    d_two: int
    """`2봉확인`의 (D) — 체결이 `N+3` 시가 이후."""


def gap_rows(labeled: pd.DataFrame) -> list[GapRow]:
    rows = []
    for segment in SEGMENTS:
        for tf in TIMEFRAMES:
            sub = labeled[(labeled["segment"] == segment) & (labeled["timeframe"] == tf)]
            if sub.empty:
                continue
            g = sub["gap_bars"]
            rows.append(
                GapRow(
                    segment=segment,
                    timeframe=tf,
                    n=len(sub),
                    gap_0=int((g == 0).sum()),
                    gap_1=int((g == 1).sum()),
                    gap_2=int((g == 2).sum()),
                    gap_3plus=int((g >= 3).sum()),
                    share_0=float((g == 0).mean()),
                    d_one=int((sub["k1_branch"] == BRANCH_D).sum()),
                    d_two=int((sub["k2_branch"] == BRANCH_D).sum()),
                )
            )
    return rows


@dataclass(frozen=True)
class ArmRow:
    segment: str
    timeframe: str
    arm: str
    variant: str
    n_total: int
    n_a: int
    n_b: int
    n_c: int
    n_c_data_end: int
    n_d: int
    net_a: float | None
    net_b: float | None
    net_c: float | None
    net_d: float | None
    stop_rate_a: float | None
    stop_rate_b: float | None
    tp_before_share_a: float | None
    """(A) 중 기준 팔이 확인 진입 시각 **전에 이미 익절**한 비율 — 그 몫은 확인 팔이 못 먹는다."""
    tp_before_share_b: float | None
    delta: float | None
    sigma: float | None
    z: float | None
    mult_median_a: float | None
    mult_p25_a: float | None
    mult_p75_a: float | None
    mult_lt1_share_a: float | None
    cost_delta_r_median_a: float | None
    delta_no_crash: float | None
    sigma_no_crash: float | None
    n_a_no_crash: int
    n_b_no_crash: int
    delta_pooled: float | None
    sigma_pooled: float | None
    delta_ex_tp_before: float | None
    """참고(판정 아님) — 확인 진입 전에 기준 팔이 이미 익절한 셋업을 (A)·(B) 양쪽에서 뺀 Δ.
    그 몫은 확인 팔이 먹을 수 없으므로 선별력의 **더 보수적인** 읽기다."""
    sigma_ex_tp_before: float | None
    verdict: str


def _arm_subset(sub: pd.DataFrame, arm: Arm, variant: str) -> dict[str, pd.DataFrame]:
    """갈래별 행. `D 포함` 판은 (D)를 조건으로 (A)/(B)에 넣는다(체결 전이라 (C) 판정이 없다)."""
    tag = f"k{arm.confirm_bars}"
    br = sub[f"{tag}_branch"]
    if variant == VARIANT_EXCL_D:
        return {b: sub[br == b] for b in BRANCHES}
    d = sub[br == BRANCH_D]
    d_ok = d[d[f"{tag}_data_ok"].astype(bool)]
    return {
        BRANCH_A: pd.concat([sub[br == BRANCH_A], d_ok[d_ok[f"{tag}_cond"].astype(bool)]]),
        BRANCH_B: pd.concat([sub[br == BRANCH_B], d_ok[~d_ok[f"{tag}_cond"].astype(bool)]]),
        BRANCH_C: pd.concat([sub[br == BRANCH_C], d[~d[f"{tag}_data_ok"].astype(bool)]]),
        BRANCH_D: sub.iloc[0:0],
    }


def _pooled(
    a: pd.DataFrame, b: pd.DataFrame
) -> tuple[float | None, float | None, list[dict[str, object]]]:
    """WAN-410 「그날 실현 net R」 버킷 안 (A)−(B) 차의 표본 가중 합(WAN-402/417 관행)."""
    strata: list[dict[str, object]] = []
    usable: list[tuple[float, float, float]] = []
    orders = sorted(set(a["realized_order"]) | set(b["realized_order"]))
    for order in orders:
        sa = a[a["realized_order"] == order]
        sb = b[b["realized_order"] == order]
        label = str(pd.concat([sa, sb])["realized_bucket"].iloc[0])
        delta, sigma = _delta(sa["net_r"], sb["net_r"])
        strata.append(
            {
                "bucket": label,
                "order": int(order),
                "n_a": len(sa),
                "n_b": len(sb),
                "delta": delta,
                "sigma": sigma,
            }
        )
        if delta is not None and sigma is not None and min(len(sa), len(sb)) >= MIN_GROUP_N:
            usable.append((float(min(len(sa), len(sb))), delta, sigma))
    if not usable:
        return None, None, strata
    total = sum(w for w, _, _ in usable)
    pooled = sum(w * d for w, d, _ in usable) / total
    sigma = math.sqrt(sum((w / total) ** 2 * s**2 for w, _, s in usable))
    return pooled, sigma, strata


def verdict_for(
    *,
    n_a: int,
    n_b: int,
    mult_median_a: float | None,
    delta: float | None,
    sigma: float | None,
    delta_no_crash: float | None,
    delta_pooled: float | None,
) -> str:
    """판정 — 착수 전에 못 박은 세 관문을 **코드가** 적용한다(사람이 표를 보고 정하지 않는다)."""
    if n_a < MIN_GROUP_N or n_b < MIN_GROUP_N or delta is None or sigma is None:
        return VERDICT_UNDECIDED
    if mult_median_a is not None and mult_median_a >= DRIFT_GATE:
        return VERDICT_DRIFT
    decided = abs(delta) > decision_z() * sigma and abs(delta) > NOISE_R
    if not decided:
        return VERDICT_NO_SELECTION
    if delta < 0:
        return VERDICT_REVERSE
    if delta_no_crash is None or delta_no_crash <= 0 or delta_pooled is None or delta_pooled <= 0:
        return VERDICT_CRASH_PROXY
    return VERDICT_PASS


def arm_rows(labeled: pd.DataFrame) -> tuple[list[ArmRow], list[dict[str, object]]]:
    rows: list[ArmRow] = []
    strata_records: list[dict[str, object]] = []
    for segment in SEGMENTS:
        for tf in TIMEFRAMES:
            sub = labeled[(labeled["segment"] == segment) & (labeled["timeframe"] == tf)]
            if sub.empty:
                continue
            for arm in ARMS:
                tag = f"k{arm.confirm_bars}"
                for variant in VARIANTS:
                    parts = _arm_subset(sub, arm, variant)
                    a, b, c, d = (parts[x] for x in BRANCHES)
                    delta, sigma = _delta(a["net_r"], b["net_r"])
                    a_nc = a[~a["is_crash_day"]]
                    b_nc = b[~b["is_crash_day"]]
                    delta_nc, sigma_nc = _delta(a_nc["net_r"], b_nc["net_r"])
                    pooled, sigma_p, strata = _pooled(a, b)
                    a_x = a[~a[f"{tag}_tp_before"].astype(bool)]
                    b_x = b[~b[f"{tag}_tp_before"].astype(bool)]
                    delta_x, sigma_x = _delta(a_x["net_r"], b_x["net_r"])
                    for rec in strata:
                        strata_records.append(
                            {
                                "segment": segment,
                                "timeframe": tf,
                                "arm": arm.name,
                                "variant": variant,
                                **rec,
                            }
                        )
                    mult = a[f"{tag}_mult"].dropna()
                    cost = (
                        entry_cost_delta_r(a, arm.confirm_bars).dropna()
                        if len(a)
                        else pd.Series(dtype=float)
                    )
                    median = float(mult.median()) if len(mult) else None
                    rows.append(
                        ArmRow(
                            segment=segment,
                            timeframe=tf,
                            arm=arm.name,
                            variant=variant,
                            n_total=len(sub),
                            n_a=len(a),
                            n_b=len(b),
                            n_c=len(c),
                            n_c_data_end=int(c[f"{tag}_c_data_end"].astype(bool).sum())
                            if len(c)
                            else 0,
                            n_d=len(d),
                            net_a=_mean(a["net_r"]),
                            net_b=_mean(b["net_r"]),
                            net_c=_mean(c["net_r"]),
                            net_d=_mean(d["net_r"]),
                            stop_rate_a=_mean(a["is_stop"].astype(float)),
                            stop_rate_b=_mean(b["is_stop"].astype(float)),
                            tp_before_share_a=_mean(a[f"{tag}_tp_before"].astype(float)),
                            tp_before_share_b=_mean(b[f"{tag}_tp_before"].astype(float)),
                            delta=delta,
                            sigma=sigma,
                            z=(abs(delta) / sigma if delta is not None and sigma else None),
                            mult_median_a=median,
                            mult_p25_a=float(mult.quantile(0.25)) if len(mult) else None,
                            mult_p75_a=float(mult.quantile(0.75)) if len(mult) else None,
                            mult_lt1_share_a=float((mult < 1.0).mean()) if len(mult) else None,
                            cost_delta_r_median_a=float(cost.median()) if len(cost) else None,
                            delta_no_crash=delta_nc,
                            sigma_no_crash=sigma_nc,
                            n_a_no_crash=len(a_nc),
                            n_b_no_crash=len(b_nc),
                            delta_pooled=pooled,
                            sigma_pooled=sigma_p,
                            delta_ex_tp_before=delta_x,
                            sigma_ex_tp_before=sigma_x,
                            verdict=verdict_for(
                                n_a=len(a),
                                n_b=len(b),
                                mult_median_a=median,
                                delta=delta,
                                sigma=sigma,
                                delta_no_crash=delta_nc,
                                delta_pooled=pooled,
                            ),
                        )
                    )
    return rows, strata_records


@dataclass(frozen=True)
class SecondBarRow:
    """③ 둘째 봉의 교환비 — `N+2`까지 살아 있고 `N+1`이 양봉인 셋업을 `N+2` 색으로 쪼갠다."""

    segment: str
    timeframe: str
    n_second_bull: int
    n_second_bear: int
    n_dropped_c: int
    """`N+1` 양봉 · `N+1`까지 살아 있었는데 `N+2` 안에서 손절 — 둘째 봉 음봉으로 세면
    동어반복이라 뺀다."""
    net_second_bull: float | None
    net_second_bear: float | None
    selection_gain: float | None
    selection_sigma: float | None
    mult_median_one: float | None
    mult_median_two: float | None
    geometry_cost: float | None
    """두 팔 (A)의 1R 배수 중앙값 차(`2봉확인` − `1봉확인`)."""


def second_bar_rows(labeled: pd.DataFrame) -> list[SecondBarRow]:
    rows = []
    for segment in SEGMENTS:
        for tf in TIMEFRAMES:
            sub = labeled[(labeled["segment"] == segment) & (labeled["timeframe"] == tf)]
            if sub.empty:
                continue
            bar1_bull = sub["bar1_found"].astype(bool) & is_bullish(
                sub["bar1_open"].to_numpy(), sub["bar1_close"].to_numpy()
            )
            alive_two = sub["k2_branch"].isin([BRANCH_A, BRANCH_B])
            pool = sub[alive_two & bar1_bull]
            bull = pool[pool["k2_cond"].astype(bool)]
            bear = pool[~pool["k2_cond"].astype(bool)]
            dropped = sub[(sub["k1_branch"] == BRANCH_A) & (sub["k2_branch"] == BRANCH_C)]
            gain, sigma = _delta(bull["net_r"], bear["net_r"])
            a1 = sub[sub["k1_branch"] == BRANCH_A]["k1_mult"].dropna()
            a2 = sub[sub["k2_branch"] == BRANCH_A]["k2_mult"].dropna()
            m1 = float(a1.median()) if len(a1) else None
            m2 = float(a2.median()) if len(a2) else None
            rows.append(
                SecondBarRow(
                    segment=segment,
                    timeframe=tf,
                    n_second_bull=len(bull),
                    n_second_bear=len(bear),
                    n_dropped_c=len(dropped),
                    net_second_bull=_mean(bull["net_r"]),
                    net_second_bear=_mean(bear["net_r"]),
                    selection_gain=gain,
                    selection_sigma=sigma,
                    mult_median_one=m1,
                    mult_median_two=m2,
                    geometry_cost=(m2 - m1 if m1 is not None and m2 is not None else None),
                )
            )
    return rows


@dataclass(frozen=True)
class TapBarRow:
    """참고 열(판정 점 아님) — 탭 봉 `N` 자체의 양봉 여부."""

    segment: str
    timeframe: str
    n: int
    bullish_share: float
    doji_share: float
    net_bullish: float | None
    net_bearish: float | None


def tap_bar_rows(labeled: pd.DataFrame) -> list[TapBarRow]:
    rows = []
    for segment in SEGMENTS:
        for tf in TIMEFRAMES:
            sub = labeled[(labeled["segment"] == segment) & (labeled["timeframe"] == tf)]
            if sub.empty:
                continue
            bull = sub["tap_bar_bullish"].astype(bool)
            rows.append(
                TapBarRow(
                    segment=segment,
                    timeframe=tf,
                    n=len(sub),
                    bullish_share=float(bull.mean()),
                    doji_share=float(sub["tap_bar_doji"].astype(bool).mean()),
                    net_bullish=_mean(sub[bull]["net_r"]),
                    net_bearish=_mean(sub[~bull]["net_r"]),
                )
            )
    return rows


@dataclass(frozen=True)
class DojiRow:
    """검산 (f) — 동가(종가 == 시가)로 조건 판정이 갈린 셋업(느슨 `≥` 충족 · 엄격 `>` 미충족)."""

    segment: str
    timeframe: str
    arm: str
    n_cond: int
    n_flip_all: int
    n_flip_ab: int
    """(A)/(B)로 분류된 셋업 중 동가 때문에 (A)가 된 것 — 판정에 실제로 닿는 몫."""
    share_of_a: float | None


def doji_rows(labeled: pd.DataFrame) -> list[DojiRow]:
    rows = []
    for segment in SEGMENTS:
        for tf in TIMEFRAMES:
            sub = labeled[(labeled["segment"] == segment) & (labeled["timeframe"] == tf)]
            if sub.empty:
                continue
            for arm in ARMS:
                tag = f"k{arm.confirm_bars}"
                cond = sub[f"{tag}_cond"].astype(bool)
                flip = cond & ~sub[f"{tag}_cond_strict"].astype(bool)
                in_a = sub[f"{tag}_branch"] == BRANCH_A
                n_a = int(in_a.sum())
                rows.append(
                    DojiRow(
                        segment=segment,
                        timeframe=tf,
                        arm=arm.name,
                        n_cond=int(cond.sum()),
                        n_flip_all=int(flip.sum()),
                        n_flip_ab=int((flip & in_a).sum()),
                        share_of_a=(float((flip & in_a).sum()) / n_a if n_a else None),
                    )
                )
    return rows


@dataclass(frozen=True)
class ChecksumRow:
    name: str
    detail: str
    expected: float
    actual: float
    diff: float


_SETUP_COLUMNS = (
    "symbol",
    "timeframe",
    "segment",
    "tap_index",
    "trigger_time",
    "entry_time",
    "entry_price",
    "stop_price",
    "exit_time",
    "tp_on_reason",
    "tp_on_exit_time",
    "stop_width",
    "net_r",
)


def checksum_rows(
    population: pd.DataFrame, labeled: pd.DataFrame, *, census: dict[str, tuple[int, int]] | None
) -> list[ChecksumRow]:
    rows: list[ChecksumRow] = []
    for segment in SEGMENTS:
        n = int((labeled["segment"] == segment).sum())
        expected = EXPECTED_SETUPS[segment]
        rows.append(
            ChecksumRow(
                "(a) 모집단 건수", f"{segment} · 이슈 본문 값", expected, n, float(n - expected)
            )
        )
        if census is not None:
            total, end = census[segment]
            rows.append(
                ChecksumRow(
                    "(a) 모집단 건수",
                    f"{segment} · WAN-375 표(첫 탭 · 가드) − 데이터 끝",
                    float(total - end),
                    float(n),
                    float(n - (total - end)),
                )
            )
    # (b) 원열 불변 — 값으로
    mismatches = 0
    for col in _SETUP_COLUMNS:
        left = population[col].reset_index(drop=True)
        right = labeled[col].reset_index(drop=True)
        mismatches += int((~((left == right) | (left.isna() & right.isna()))).sum())
    rows.append(
        ChecksumRow(
            "(b) 셋업 원열 불변",
            f"{len(_SETUP_COLUMNS)}열 × 전 행",
            0.0,
            float(mismatches),
            float(mismatches),
        )
    )
    for arm in ARMS:
        tag = f"k{arm.confirm_bars}"
        # (c) 인과 — 라벨에 쓴 마지막 봉의 마감 ≤ 진입 시각
        viol = int((labeled[f"{tag}_label_last_close"] > labeled[f"{tag}_entry_time"]).sum())
        tf_ms = labeled["timeframe"].map(timeframe_to_ms)
        used_last_open = labeled["trigger_time"] + arm.confirm_bars * tf_ms
        viol += int((used_last_open + tf_ms > labeled[f"{tag}_entry_time"]).sum())
        rows.append(
            ChecksumRow(
                "(c) 인과", f"{arm.name} · 라벨 봉 마감 > 진입 시각", 0.0, float(viol), float(viol)
            )
        )
        # (d) 전체성
        for segment in SEGMENTS:
            sub = labeled[labeled["segment"] == segment]
            counts = sub[f"{tag}_branch"].value_counts()
            total = int(sum(int(counts.get(b, 0)) for b in BRANCHES))
            rows.append(
                ChecksumRow(
                    "(d) 전체성",
                    f"{arm.name} · {segment} · A+B+C+D",
                    float(len(sub)),
                    float(total),
                    float(total - len(sub)),
                )
            )
    # (e) 구조 — 2봉확인 충족 ⊂ 1봉확인 충족 (집합으로)
    key_cols = ["symbol", "timeframe", "trigger_time", "entry_time", "zone"]
    one = set(map(tuple, labeled[labeled["k1_cond"].astype(bool)][key_cols].to_numpy().tolist()))
    two = set(map(tuple, labeled[labeled["k2_cond"].astype(bool)][key_cols].to_numpy().tolist()))
    outside = len(two - one)
    rows.append(
        ChecksumRow(
            "(e) 구조", "2봉확인 충족 − 1봉확인 충족(집합)", 0.0, float(outside), float(outside)
        )
    )
    proper = 1.0 if len(one - two) > 0 else 0.0
    rows.append(
        ChecksumRow(
            "(e) 구조", "진부분집합(1봉 충족에만 있는 셋업 존재)", 1.0, proper, proper - 1.0
        )
    )
    keys_unique = len(labeled[key_cols].drop_duplicates()) == len(labeled)
    rows.append(
        ChecksumRow("(e) 구조", "집합 키 유일성", 1.0, float(keys_unique), float(keys_unique) - 1.0)
    )
    return rows


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _f(value: object, digits: int = 4) -> str:
    v = _num(value)
    if v is None:
        return "—"
    return f"{v:+.{digits}f}" if digits >= 3 else f"{v:.{digits}f}"


def _pct(value: object) -> str:
    v = _num(value)
    return "—" if v is None else f"{100 * v:.1f}%"


def _x(value: object) -> str:
    v = _num(value)
    return "—" if v is None else f"{v:.3f}×"


def _n(value: object) -> str:
    return f"{int(value):,}"  # type: ignore[call-overload]


def _table(headers: Sequence[str], cells: Sequence[Sequence[str]]) -> list[str]:
    """마크다운 표 — 첫 열들(문자)은 왼쪽, 나머지는 오른쪽 정렬."""
    align = ["--" if not h.startswith("#") else "--:" for h in headers]
    names = [h.lstrip("#") for h in headers]
    out = ["| " + " | ".join(names) + " |", "| " + " | ".join(align) + " |"]
    out.extend("| " + " | ".join(row) + " |" for row in cells)
    return out


def render_summary(
    *,
    gaps: pd.DataFrame,
    arms: pd.DataFrame,
    second: pd.DataFrame,
    tap: pd.DataFrame,
    doji: pd.DataFrame,
    checks: pd.DataFrame,
    bad_days: Sequence[str],
    cost_note: str,
) -> str:
    z = decision_z()
    lines = [
        "# WAN-419 — 「존에 닿은 뒤 양봉 확인하고 진입」 1봉 대 2봉",
        "",
        "> 자동 생성(`python -m backtest.wan419_bullish_confirmation`). "
        "**측정 전용 · 셋업 층 · 팔을 만들지 않는다.**",
        "> 모집단 = WAN-375 셋업 표(`band` · 첫 탭만 · 가드 통과 · 데이터 끝 제외) · "
        "상위TF 봉만 읽는다 · 핀 없음.",
        "> ⚠️ 채택 좌표가 아니다(시퀀싱·공유 자본·재진입 없음) — 채택 북 성적과 나란히 놓지 "
        "말 것. 판단은 북에서(WAN-341).",
        "",
        f"판정선(코드 상수): 밀림 게이트 (A) 1R 배수 중앙값 ≥ **{DRIFT_GATE}** · "
        f"선별 게이트 |Δ| > **{z:.3f}σ**(2σ를 판정 점 {TESTS}개로 Bonferroni)이면서 "
        f"> {NOISE_R}R · 양수 · 폭락일 통제(최악 {len(bad_days)}일 제외 + WAN-410 버킷 안 "
        f"가중 합) 부호 유지 · 표본 게이트 (A)·(B) 각 ≥ {MIN_GROUP_N}. "
        "관문은 이 순서로 적용한다(앞 관문에서 닫히면 뒤는 안 본다).",
        "",
        "## 판정 (주 구간 `oos_warm` · D 제외 정본)",
        "",
    ]
    primary = arms[(arms["segment"] == PRIMARY_SEGMENT) & (arms["variant"] == VARIANT_EXCL_D)]
    lines += _table(
        [
            "TF",
            "팔",
            "#(A)",
            "#(B)",
            "#(C)",
            "#(D)",
            "#net (A)",
            "#net (B)",
            "#Δ(A−B)",
            "#z",
            "#1R 배수 중앙(A)",
            "#Δ 폭락일 제외",
            "#Δ 버킷 안",
            "#Δ 확인 전 익절 제외(참고)",
            "판정",
        ],
        [
            [
                str(r["timeframe"]),
                str(r["arm"]),
                _n(r["n_a"]),
                _n(r["n_b"]),
                _n(r["n_c"]),
                _n(r["n_d"]),
                _f(r["net_a"]),
                _f(r["net_b"]),
                _f(r["delta"]),
                _f(r["z"], 2),
                _x(r["mult_median_a"]),
                _f(r["delta_no_crash"]),
                _f(r["delta_pooled"]),
                _f(r["delta_ex_tp_before"]),
                str(r["verdict"]),
            ]
            for r in primary.to_dict("records")
        ],
    )
    passed = primary[primary["verdict"] == VERDICT_PASS]
    names = ", ".join(f"{r['timeframe']}·{r['arm']}" for r in passed.to_dict("records"))
    lines += [
        "",
        f"**2단계로 넘기는 칸: {len(passed)} / {len(primary)}**" + (f" — {names}" if names else ""),
        "",
    ]
    lines += ["## §0 탭↔체결 간격 (기준 팔 체결이 탭 봉 몇 개 뒤인가)", ""]
    lines += _table(
        [
            "구간",
            "TF",
            "#셋업",
            "#0봉",
            "#1봉",
            "#2봉",
            "#3봉+",
            "#0봉 비율",
            "#(D) 1봉확인",
            "#(D) 2봉확인",
        ],
        [
            [
                str(r["segment"]),
                str(r["timeframe"]),
                _n(r["n"]),
                _n(r["gap_0"]),
                _n(r["gap_1"]),
                _n(r["gap_2"]),
                _n(r["gap_3plus"]),
                _pct(r["share_0"]),
                f"{_n(r['d_one'])} ({_pct(r['d_one'] / r['n'])})",
                f"{_n(r['d_two'])} ({_pct(r['d_two'] / r['n'])})",
            ]
            for r in gaps.to_dict("records")
        ],
    )
    lines += [
        "",
        "(D) = 기준 팔 체결이 그 팔의 진입 시각(`N+K+1` 시가) 이후 — 「확인을 기다린 대가」와 "
        "「지정가를 안 기다린 이득」이 섞이는 부류라 정본 판정에서 빼고 아래에 넣은 판을 병기한다.",
        "",
        "## §1 갈래 전체 (두 구간 × 두 판)",
        "",
    ]
    lines += _table(
        [
            "구간",
            "TF",
            "팔",
            "판",
            "#(A)",
            "#(B)",
            "#(C)",
            "#(C) 데이터 끝",
            "#(D)",
            "#net (A)",
            "#net (B)",
            "#net (C)",
            "#손절률 (A)",
            "#손절률 (B)",
            "#확인 전 이미 익절 (A)",
            "#Δ",
            "#σ",
            "#1R 배수 (A) p25·중앙·p75",
            "#배수<1 (A)",
            "#진입 비용차 R 중앙(A)",
            "판정",
        ],
        [
            [
                str(r["segment"]),
                str(r["timeframe"]),
                str(r["arm"]),
                str(r["variant"]),
                _n(r["n_a"]),
                _n(r["n_b"]),
                _n(r["n_c"]),
                _n(r["n_c_data_end"]),
                _n(r["n_d"]),
                _f(r["net_a"]),
                _f(r["net_b"]),
                _f(r["net_c"]),
                _pct(r["stop_rate_a"]),
                _pct(r["stop_rate_b"]),
                _pct(r["tp_before_share_a"]),
                _f(r["delta"]),
                _f(r["sigma"]),
                f"{_x(r['mult_p25_a'])} · {_x(r['mult_median_a'])} · {_x(r['mult_p75_a'])}",
                _pct(r["mult_lt1_share_a"]),
                _f(r["cost_delta_r_median_a"]),
                str(r["verdict"]),
            ]
            for r in arms.to_dict("records")
        ],
    )
    lines += [
        "",
        "진입 비용차 R = 확인 진입(테이커+슬리피지) ÷ 확인 1R − 기준 진입(메이커) ÷ 기준 1R. "
        "§1은 진입가를 안 바꾸므로 net R에 **안 걸려 있다** — 2단계가 물 몫의 예고다.",
        "",
        "## ③ 둘째 봉의 교환비 (헤드라인)",
        "",
        "`N+2`까지 살아 있고 `N+1`이 양봉인 셋업을 `N+2` 색으로 쪼갠다. 선별 이득 = 두 부분집합의 "
        "net R 차 · 기하 비용 = 두 팔 (A)의 1R 배수 중앙값 차.",
        "",
    ]
    lines += _table(
        [
            "구간",
            "TF",
            "#N+2 양봉",
            "#N+2 음봉",
            "#뺀 (C)",
            "#net (양)",
            "#net (음)",
            "#선별 이득",
            "#σ",
            "#1R 배수 중앙 1봉",
            "#2봉",
            "#기하 비용",
        ],
        [
            [
                str(r["segment"]),
                str(r["timeframe"]),
                _n(r["n_second_bull"]),
                _n(r["n_second_bear"]),
                _n(r["n_dropped_c"]),
                _f(r["net_second_bull"]),
                _f(r["net_second_bear"]),
                _f(r["selection_gain"]),
                _f(r["selection_sigma"]),
                _x(r["mult_median_one"]),
                _x(r["mult_median_two"]),
                _f(r["geometry_cost"], 3),
            ]
            for r in second.to_dict("records")
        ],
    )
    lines += [
        "",
        "뺀 (C) = `1봉확인`에선 (A)였는데 `N+2` 안에서 손절난 셋업 — 「둘째 봉 음봉」으로 세면 "
        "동어반복이라 뺐다.",
        "",
        "## 참고 열 — 탭 봉 `N` 자체의 색 (판정 점 아님 · 보정 분모에 안 넣음)",
        "",
    ]
    lines += _table(
        [
            "구간",
            "TF",
            "#셋업",
            "#양봉 비율",
            "#동가 비율",
            "#net (탭 봉 양봉)",
            "#net (탭 봉 음봉)",
        ],
        [
            [
                str(r["segment"]),
                str(r["timeframe"]),
                _n(r["n"]),
                _pct(r["bullish_share"]),
                _pct(r["doji_share"]),
                _f(r["net_bullish"]),
                _f(r["net_bearish"]),
            ]
            for r in tap.to_dict("records")
        ],
    )
    lines += ["", "## 검산 (f) — 동가(종가 == 시가)로 판정이 갈린 셋업", ""]
    lines += _table(
        ["구간", "TF", "팔", "#조건 충족", "#동가로 충족(전체)", "#그중 (A)", "#(A) 대비"],
        [
            [
                str(r["segment"]),
                str(r["timeframe"]),
                str(r["arm"]),
                _n(r["n_cond"]),
                _n(r["n_flip_all"]),
                _n(r["n_flip_ab"]),
                _pct(r["share_of_a"]),
            ]
            for r in doji.to_dict("records")
        ],
    )
    lines += ["", "## 검산 (a)~(e)", ""]
    lines += _table(
        ["항목", "내용", "#기대", "#실제", "#차"],
        [
            [
                str(r["name"]),
                str(r["detail"]),
                f"{r['expected']:g}",
                f"{r['actual']:g}",
                f"{r['diff']:.2e}",
            ]
            for r in checks.to_dict("records")
        ],
    )
    lines += [
        "",
        "## 읽는 법 · 한계",
        "",
        "* **셋업 층 관측이다** — (A)/(B)의 net R은 **기준 팔(볼린저 지정가)**의 결과이지 확인 "
        "팔의 손익이 아니다. 진입가를 안 바꾸고 조건의 고르는 힘만 본다. 실제로 늦게 사는 팔은 "
        "2단계이고 북에서 잰다(WAN-341).",
        "* **모집단은 체결 셋업뿐이다** — 진짜 확인 진입 팔이라면 볼린저에 막힌 첫 탭도 진입했을 "
        "것이다. 「확인 진입 팔 전체」로 일반화하지 말 것.",
        "* 🚨 **선별력이 커 보여도 확정이 아니다** — WAN-383 §1이 같은 방식으로 「확인이 건너뛰는 "
        "거래가 지는 거래」를 냈는데 WAN-386이 실제로 늦게 사서 재자 손익으로 안 갚아졌다.",
        "* 「확인 전 이미 익절」 = (A) 중 기준 팔이 확인 진입 시각 전에 1.5R을 이미 챙긴 비율 — "
        "그 몫은 확인 팔이 못 먹는다.",
        "* 전부 `baseline`(낙관) 렌즈 위 값 · `pen_5bp` 미측정 · WAN-408/410과 더하지 말 것 · "
        "「엣지 없음」 불변(*어느 순간에 들어갈까*를 묻는다 — 다른 질문).",
        "* 재탭 차단(WAN-404)과의 충돌·「존별 첫 체결」 스코프 참고는 이 표에서 안 쟀다.",
        "",
        f"비용: {cost_note}",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _records(items: Sequence[object]) -> pd.DataFrame:
    return pd.DataFrame.from_records([asdict(i) for i in items])  # type: ignore[call-overload]


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    parser.add_argument(
        "--from-csv", action="store_true", help="라벨 캐시에서 집계·요약만 다시 낸다"
    )
    parser.add_argument("--jobs", default=None, help="봉 읽기 병렬(성능 노브 · 결과 불변)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.monotonic()
    population = load_population()
    bad_days = worst_days(count=WORST_DAYS)
    if args.from_csv:
        if not LABELS_CSV.exists():
            print(f"[wan419] 라벨 캐시가 없습니다: {LABELS_CSV}", file=sys.stderr)
            return 2
        labeled = pd.read_csv(LABELS_CSV)
        cost_note = "요약만 재생성(`--from-csv`). "
    else:
        jobs = harness.default_jobs() if args.jobs is None else int(args.jobs)
        pairs = sorted(
            {(str(s), str(tf)) for s, tf in population[["symbol", "timeframe"]].to_numpy().tolist()}
        )
        load_started = time.monotonic()
        bars_by = load_all_bars(pairs, jobs=jobs)
        label_started = time.monotonic()
        labeled = attach_controls(label_population(population, bars_by), bad_days=bad_days)
        LABELS_CSV.parent.mkdir(parents=True, exist_ok=True)
        labeled.to_csv(LABELS_CSV, index=False)
        cost_note = (
            f"봉 읽기 {label_started - load_started:.0f}s({len(pairs)}칸) · "
            f"라벨링 {time.monotonic() - label_started:.0f}s({len(labeled):,} 셋업). "
        )
    census = {seg: population_census(segment=seg) for seg in SEGMENTS}
    gaps = _records(gap_rows(labeled))
    arm_list, strata = arm_rows(labeled)
    arms = _records(arm_list)
    second = _records(second_bar_rows(labeled))
    tap = _records(tap_bar_rows(labeled))
    doji = _records(doji_rows(labeled))
    checks_list = checksum_rows(population, labeled, census=census)
    checks = _records(checks_list)
    cost_note += f"전체 {time.monotonic() - started:.0f}s."
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for frame, path in (
        (gaps, GAP_CSV),
        (arms, ARM_CSV),
        (second, SECOND_BAR_CSV),
        (tap, TAP_BAR_CSV),
        (doji, DOJI_CSV),
        (pd.DataFrame.from_records(strata), STRATA_CSV),
        (checks, CHECKSUM_CSV),
    ):
        frame.to_csv(path, index=False, float_format="%.10g")
    summary = render_summary(
        gaps=gaps,
        arms=arms,
        second=second,
        tap=tap,
        doji=doji,
        checks=checks,
        bad_days=bad_days,
        cost_note=cost_note,
    )
    SUMMARY_PATH.write_text(summary)
    print(summary)
    bad = [c for c in checks_list if abs(c.diff) > 1e-9]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
