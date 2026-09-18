"""WAN-421 — 존이 뚫리기 전에 가격이 무엇을 했나.

유동성 스윕 · 접근 변동성 · 상위TF 추세 · 지표선 겹침을 한 격자로.

## 한 줄

사용자 제안(2026-09-18, 페이퍼 09-10 「7개 포지션이 8분 안에 같이 죽음」)의 가설 넷을 **탭 이전에
확정된 봉만으로** 셋업에 라벨로 붙이고, 그 라벨이 거래당 net R을 가르는지 잰다. 축은 넷이고
순서는 이슈대로 A → D → B → C다.

* **A 유동성 스윕(A1)** — 탭 전에, 존 **바로 위**에 쌓여 있던 주요 전저점이 **이미 깨졌나**.
* **D 지표선 겹침** — 탭 직전 확정봉에서 EMA 20·60·120·240·365 + VWMA 100 중 **하나라도 존 안에**
  있나(사용자 정의 「하나라도 겹치면 진입」). 「어느 선이」를 가르지 않는다(판정 점 24 → 4,
  WAN-161). 개수(0~6)는 인구조사로만 낸다.
* **B 접근 변동성** — 탭 직전 `N`봉의 범위·몸통·거래량이 그 앞 기준 구간 대비 얼마나 큰가를
  **하나의 연속 축**으로(세 비율의 기하평균). ②(장대봉 직진)와 ③(압착)은 이 축의 **양 끝**이다.
* **C 상위TF 추세** — 탭 전에 마감된 **일봉** 종가의 일봉 EMA 50 대비 괴리.

## 🚨 가설 방향 — 착수 전에 못 박음 (결과를 보고 옮기지 않는다 · WAN-161)

모든 대조는 **가설이 「양수」를 예측하도록** 방향을 맞췄다(`좋은 쪽 − 나쁜 쪽`):

| 축 | 좋은 쪽(가설) | 나쁜 쪽(가설) | 대조 |
| -- | -- | -- | -- |
| A | 스윕됨 | 스윕 안 됨 | `swept − unswept` |
| D | 선이 하나라도 존 안(1개 이상) | 0개 | `≥1 − 0` (사용자 정의 「하나라도 겹치면 진입」) |
| B | 가운데 세 분위 | 양 끝 두 분위 | `mid − ends` (🚨 **U자를 예상한다** = net R은 뒤집힌 U) |
| C | 추세 괴리 최상위 분위 | 최하위 분위(하락 추세) | `top − bottom` |

B는 WAN-117이 `approach_mom`을 **부호 고정 상관**으로 쟀기 때문에 되살린다 — 상관은 U자를
0으로 본다. 그래서 선형 대조(`top − bottom`)는 **참고 열**로만 내고 판정 점은 U 대조 하나다.

## 판정 — 코드가 낸다 (`verdict_for`)

판정 점 = 축 4 × TF 4 = **16**(Bonferroni 분모). 전부 통과해야 「간다」:

1. **표본** — 대조 양쪽이 앞·뒷구간 모두 `MIN_GROUP_N`(100) 이상. 아니면 판정 불가.
2. **뒷구간(`oos_warm`)** 차가 2σ를 16개로 Bonferroni 보정한 z 밖이고 노이즈선(±0.005R) 밖.
3. 그 차가 **양수**(가설 방향). 음수면 「역방향」.
4. **앞구간(`is`)** 차도 양수(분위 경계를 고른 구간과 방향이 같다).
5. 🚨 **폭락일 통제** — WAN-408 최악 10일(진입일 또는 청산일)을 뺀 판 **그리고** WAN-410 「그날
   실현 net R」 버킷 안 표본 가중 합에서 양수 유지. 진입 축이 일곱 번 다 여기서 죽었다.
6. **D만** — EMA 20을 뺀 5선 판(5선 중 1개 이상 대 0개)에서도 양수 유지. 안 되면 「볼린저(중심선
   SMA 20)의 다른 이름」.

판정 자는 거래당 net R(b)이고 손절률(a)은 병기만 한다(WAN-154).

## 모집단 — 새로 만들지 않는다

WAN-375 셋업 표(`wan402.load_population` — `band` · 첫 탭만 · 가드 통과 · 데이터 끝 제외 · 채택
회계 net R). 존 경계는 **엔진이 쓴 그 아카이브**를 같은 창·같은 `OrderBlockParams()`로 다시 탐지해
`zone` 인덱스로 찾는다(검산 (f): 아카이브 존 바닥 ≡ 셋업 손절가 · 0건 불일치). 읽는 것은
**상위TF 봉**뿐이다(1분봉 없음).

## 재현

    uv run python -m backtest.wan421_pre_break_context --pilot       # BTC 4h 한 칸
    uv run python -m backtest.wan421_pre_break_context --jobs 4      # 전체
    uv run python -m backtest.wan421_pre_break_context --from-csv    # 요약만(캐시에서)
    uv run python -m backtest.wan421_pre_break_context --cache-check # WAN-375 캐시 재현(BTC 4h)

측정 전용 · 엔진·기본값·토대 불변 · **필터를 만들지 않는다**(라벨링과 집계뿐 — 거르는 팔은 북에서,
WAN-341) · 핀 없음(WAN-305) · 전부 `baseline`(낙관) 렌즈 위 값.
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
from backtest.wan402_concurrent_breaks import (
    assign_bins,
    bin_edges,
    cache_reproduction_mismatches,
    load_population,
)
from backtest.wan419_bullish_confirmation import _delta, _pooled, attach_controls
from strategy import indicators
from strategy.models import OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
BUCKETS_CSV = REPORTS_DIR / "wan421_buckets.csv"
VERDICT_CSV = REPORTS_DIR / "wan421_verdict.csv"
LOO_CSV = REPORTS_DIR / "wan421_loo.csv"
CORR_CSV = REPORTS_DIR / "wan421_axis_correlation.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan421_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan421_pre_break_context_summary.md"
#: 셋업 단위 라벨 원자료 — 커밋하지 않는다(`backtest/cache/`는 gitignore).
LABELED_CSV = Path("backtest/cache/wan421/labeled.csv.gz")

SEGMENT_IS = harness.SEGMENT_IS
SEGMENT_OOS_WARM = harness.SEGMENT_OOS_WARM
SEGMENTS: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS_WARM)
PRIMARY_SEGMENT = SEGMENT_OOS_WARM
TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES

# --------------------------------------------------------------------------- #
# 착수 전에 못 박은 상수 — 결과를 보고 옮기지 않는다 (WAN-161)
# --------------------------------------------------------------------------- #

AXIS_A = "A_스윕"
AXIS_D = "D_지표선겹침"
AXIS_B = "B_접근변동성"
AXIS_C = "C_일봉추세"
AXES: tuple[str, ...] = (AXIS_A, AXIS_D, AXIS_B, AXIS_C)
"""이슈가 정한 순서(A → D → B → C)."""

PIVOT_LENGTH: int = OrderBlockParams().swing_length
"""A 「주요」 전저점 = 좌우 `L`봉보다 낮은 피벗. `L`은 **탐지기가 존을 만드는 그 스윙 길이**
(`OrderBlockParams().swing_length` = 10)를 쓴다 — 새 숫자를 고르지 않는다."""
SWEEP_LOOKBACK_BARS = 50
"""A 「근처」(시간) — 피벗 중심이 탭 봉보다 이만큼 이내에 있어야 한다."""
SWEEP_NEAR_ATR = 2.0
"""A 「근처」(가격) — 피벗 저가가 존 윗변 위로 `ATR14 × 이 값` 이내(존 **위**에 쌓인 유동성)."""
EQUAL_LOWS_BP = 10.0
"""A 「Equal Lows」 허용오차(bp) — **참고 열 전용**(판정 점 아님): 적격 피벗 둘이 이 안이면 풀."""
ATR_LENGTH = 14

APPROACH_BARS = 5
"""B `N` — 탭 직전 이만큼의 봉이 「접근」이다."""
BASELINE_BARS = 50
"""B 기준 구간 — 접근 바로 앞 이만큼의 봉(접근과 겹치지 않는다)."""

LINE_EMA_LENGTHS: tuple[int, ...] = (20, 60, 120, 240, 365)
"""D — 사용자 트레이딩뷰 설정 그대로(`ConfluenceParams().display_ema_lengths`와 같다 · 검산)."""
LINE_VWMA_LENGTH = 100
"""D — `ConfluenceParams().tp_vwma_length`와 같다(검산)."""
BOLLINGER_TWIN_EMA = 20
"""D의 「볼린저 중복」 — 볼린저 중심선(SMA 20)의 쌍둥이. 이 선을 뺀 판을 병기·관문으로 쓴다."""

DAILY_TF = "1d"
DAILY_EMA_LENGTH = 50
"""C — 일봉 EMA 50 대비 괴리. (WAN-117 `trend_dev`는 자기 TF EMA 200이었다 — 여기는 **상위**TF.)"""

FEATURE_PAD_MS = 120 * 86_400_000
"""자기 TF 지표(EMA 365 · VWMA 100 · 기준 구간)의 워밍업 여유 — 창 시작 전 봉을 더 읽는다."""
DAILY_PAD_MS = 300 * 86_400_000
"""일봉 EMA 50 워밍업 여유."""

QUANTILES = 5
MIN_GROUP_N = 100
NOISE_R = 0.005
SIGMA_MULTIPLE = 2.0
TESTS = len(AXES) * len(TIMEFRAMES)
"""판정 점 = 축 4 × TF 4 = 16(Bonferroni)."""
WORST_DAYS = 10
EXPECTED_SETUPS: dict[str, int] = {SEGMENT_IS: 22_725, SEGMENT_OOS_WARM: 10_272}

CLASS_SWEPT = "swept"
CLASS_UNSWEPT = "unswept"
CLASS_NONE = "no_pivot"
CLASS_MISSING = "missing"
"""라벨을 못 붙인 셋업(탭 봉이 봉 목록에 없거나 이력 부족) — 분류의 전체성(검산 (d))."""

VERDICT_PASS = "간다(다섯 관문 통과)"
VERDICT_UNDECIDED = "판정 불가(표본)"
VERDICT_NO_SELECTION = "닫음 — 부호 미정(2σ·Bonferroni 또는 노이즈선 안)"
VERDICT_REVERSE = "닫음 — 역방향(가설과 반대 쪽이 낫다)"
VERDICT_IS_MISMATCH = "닫음 — 앞구간 방향이 다름"
VERDICT_CRASH_PROXY = "닫음 — 폭락일 대리변수(통제하면 부호가 안 남는다)"
VERDICT_BOLLINGER_ALIAS = "닫음 — 볼린저의 다른 이름(EMA 20을 빼면 부호가 안 남는다)"


def decision_z() -> float:
    """2σ(양측 α ≈ 4.55%)를 판정 점 개수(`TESTS`)로 Bonferroni 보정한 z."""
    alpha = 2.0 * (1.0 - NormalDist().cdf(SIGMA_MULTIPLE))
    return NormalDist().inv_cdf(1.0 - alpha / (2.0 * TESTS))


# --------------------------------------------------------------------------- #
# 봉·존 읽기 — 칸마다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _CellTask:
    symbol: str
    timeframe: str
    setups: pd.DataFrame
    """이 칸의 셋업(인덱스 = 모집단 인덱스). 라벨만 계산해 돌려준다."""


def pivot_lows(lows: np.ndarray, length: int) -> np.ndarray:
    """중심 봉 `c`가 피벗 저가인가 — `strategy.lux_order_blocks._pivot_high`의 거울상.

    좌 `length`봉은 **모두 더 높고**(동률이면 피벗 아님), 우 `length`봉도 **모두 더 높다**.
    확정 시각은 `c + length` 봉의 마감이다(그 전에는 알 수 없다).
    """
    n = len(lows)
    out = np.zeros(n, dtype=bool)
    series = pd.Series(lows)
    left_min = series.shift(1).rolling(length, min_periods=length).min().to_numpy()
    right_min = series[::-1].shift(1).rolling(length, min_periods=length).min().to_numpy()[::-1]
    with np.errstate(invalid="ignore"):
        out = (lows < left_min) & (lows < right_min)
    return np.asarray(out & ~np.isnan(left_min) & ~np.isnan(right_min), dtype=bool)


def _load_bars(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    market = harness.load_market_data(
        symbol, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=False, funding=False
    )
    return market.htf_df[["open_time", "open", "high", "low", "close", "volume"]].reset_index(
        drop=True
    )


def zone_bounds(symbol: str, timeframe: str) -> list[tuple[float, float]]:
    """엔진이 쓴 **그 아카이브**의 (윗변, 바닥) — 같은 창 · 같은 `OrderBlockParams()`(WAN-77)."""
    market = harness.load_market_data(
        symbol,
        timeframe,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
        need_1m=False,
        funding=False,
    )
    result = harness.detect_order_blocks(market, OrderBlockParams())
    return [(float(ob.top), float(ob.bottom)) for ob in result.order_blocks]


def label_cell(task: _CellTask) -> pd.DataFrame:
    """한 칸의 셋업에 네 축 라벨을 붙인다(새 열만 — 원열은 손대지 않는다)."""
    started = time.monotonic()
    symbol, tf = task.symbol, task.timeframe
    tf_ms = timeframe_to_ms(tf)
    start_ms = parse_date_ms(harness.DEFAULT_START)
    end_ms = parse_date_ms(harness.DEFAULT_END)
    bars = _load_bars(symbol, tf, start_ms - FEATURE_PAD_MS, end_ms)
    daily = _load_bars(symbol, DAILY_TF, start_ms - DAILY_PAD_MS, end_ms)
    zones = zone_bounds(symbol, tf)
    out = label_frame(task.setups, bars=bars, daily=daily, zones=zones, tf_ms=tf_ms)
    print(
        f"[wan421] {symbol} {tf}: 셋업 {len(task.setups)} · {time.monotonic() - started:.1f}s",
        flush=True,
    )
    return out


def label_frame(
    setups: pd.DataFrame,
    *,
    bars: pd.DataFrame,
    daily: pd.DataFrame,
    zones: Sequence[tuple[float, float]],
    tf_ms: int,
) -> pd.DataFrame:
    """순수 함수 — 봉·일봉·존 경계를 받아 셋업마다 라벨을 낸다(테스트가 합성 입력으로 부른다)."""
    ot = bars["open_time"].to_numpy(dtype=np.int64)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    open_ = bars["open"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    volume = bars["volume"].to_numpy(dtype=float)
    atr = indicators.atr(bars, ATR_LENGTH).to_numpy(dtype=float) if len(bars) else np.array([])
    lines: dict[str, np.ndarray] = {}
    if len(bars):
        for length in LINE_EMA_LENGTHS:
            lines[f"ema{length}"] = indicators.ema(bars, length).to_numpy(dtype=float)
        lines[f"vwma{LINE_VWMA_LENGTH}"] = indicators.vwma(bars, LINE_VWMA_LENGTH).to_numpy(
            dtype=float
        )
    is_pivot = pivot_lows(low, PIVOT_LENGTH) if len(bars) else np.array([], dtype=bool)
    rng = high - low
    body = np.abs(close - open_)

    d_ot = daily["open_time"].to_numpy(dtype=np.int64)
    d_close = daily["close"].to_numpy(dtype=float)
    d_ema = (
        indicators.ema(daily, DAILY_EMA_LENGTH).to_numpy(dtype=float)
        if len(daily)
        else np.array([])
    )
    day_ms = timeframe_to_ms(DAILY_TF)

    records: list[dict[str, object]] = []
    for idx, trig, zone, stop in zip(
        setups.index,
        setups["trigger_time"].to_numpy(dtype=np.int64),
        setups["zone"].to_numpy(),
        setups["stop_price"].to_numpy(dtype=float),
        strict=True,
    ):
        rec: dict[str, object] = {"_idx": idx}
        top, bottom = zones[int(zone)]
        rec["zone_top"] = top
        rec["zone_bottom"] = bottom
        rec["zone_matches_stop"] = bottom == float(stop)
        pos = int(np.searchsorted(ot, trig))
        found = pos < len(ot) and int(ot[pos]) == int(trig)
        prev = pos - 1
        usable = found and prev >= 0
        last_close = int(ot[prev]) + tf_ms if usable else None
        # ---- A 스윕 -------------------------------------------------------- #
        a_class = CLASS_MISSING
        eq_pool = False
        eligible = 0
        a_last = None
        if usable and not math.isnan(atr[prev]):
            a_last = last_close
            ceiling = top + SWEEP_NEAR_ATR * float(atr[prev])
            lo_c = max(0, pos - SWEEP_LOOKBACK_BARS)
            hi_c = prev - PIVOT_LENGTH  # 확정 봉(c+L)이 탭 봉 직전 이하
            swept = False
            pivots: list[float] = []
            for c in range(lo_c, hi_c + 1):
                if not is_pivot[c]:
                    continue
                p_low = float(low[c])
                if not (top < p_low <= ceiling):
                    continue
                pivots.append(p_low)
                after = low[c + PIVOT_LENGTH + 1 : pos]
                if len(after) and float(after.min()) < p_low:
                    swept = True
            eligible = len(pivots)
            if eligible == 0:
                a_class = CLASS_NONE
            elif swept:
                a_class = CLASS_SWEPT
            else:
                a_class = CLASS_UNSWEPT
            srt = sorted(pivots)
            eq_pool = any(
                (b - a) / a * 10_000 <= EQUAL_LOWS_BP for a, b in zip(srt, srt[1:], strict=False)
            )
        rec["a_class"] = a_class
        rec["a_eligible_pivots"] = eligible
        rec["a_equal_lows"] = eq_pool
        rec["a_last_close"] = a_last
        # ---- D 지표선 겹침 ------------------------------------------------ #
        d_count: float = math.nan
        d_count_ex: float = math.nan
        d_last = None
        if usable:
            vals = {name: float(arr[prev]) for name, arr in lines.items()}
            if not any(math.isnan(v) for v in vals.values()):
                inside = {name: bottom <= v <= top for name, v in vals.items()}
                d_count = float(sum(inside.values()))
                d_count_ex = float(
                    sum(v for name, v in inside.items() if name != f"ema{BOLLINGER_TWIN_EMA}")
                )
                d_last = last_close
                for name, flag in inside.items():
                    rec[f"d_in_{name}"] = flag
        rec["d_count"] = d_count
        rec["d_count_ex20"] = d_count_ex
        rec["d_last_close"] = d_last
        # ---- B 접근 변동성 ------------------------------------------------ #
        b_energy = math.nan
        b_rr = b_br = b_vr = math.nan
        b_last = None
        a0 = pos - APPROACH_BARS
        base0 = a0 - BASELINE_BARS
        if usable and base0 >= 0:
            appr = slice(a0, pos)
            base = slice(base0, a0)
            ratios = []
            for arr in (rng, body, volume):
                denom = float(arr[base].mean())
                ratios.append(float(arr[appr].mean()) / denom if denom > 0 else math.nan)
            b_rr, b_br, b_vr = ratios
            if all(r > 0 and not math.isnan(r) for r in ratios):
                b_energy = float(np.exp(np.mean(np.log(ratios))))
                b_last = last_close
        rec["b_energy"] = b_energy
        rec["b_range_ratio"] = b_rr
        rec["b_body_ratio"] = b_br
        rec["b_volume_ratio"] = b_vr
        rec["b_last_close"] = b_last
        # ---- C 일봉 추세 -------------------------------------------------- #
        c_dev = math.nan
        c_last = None
        # 마감(open + 1d)이 탭 시각 이하인 마지막 일봉.
        dpos = int(np.searchsorted(d_ot, int(trig) - day_ms, side="right")) - 1
        if dpos >= 0 and not math.isnan(d_ema[dpos]) and d_ema[dpos] > 0:
            c_dev = float(d_close[dpos] / d_ema[dpos] - 1.0)
            c_last = int(d_ot[dpos]) + day_ms
        rec["c_trend_dev"] = c_dev
        rec["c_last_close"] = c_last
        records.append(rec)
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    return frame.set_index("_idx").rename_axis(None)


def label_population(population: pd.DataFrame, *, jobs: int = 1) -> pd.DataFrame:
    """전 칸 라벨 → 원열 + 새 열. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    tasks = [
        _CellTask(str(symbol), str(tf), group)
        for (symbol, tf), group in population.groupby(["symbol", "timeframe"], sort=True)
    ]
    if jobs <= 1:
        parts = [label_cell(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            parts = list(executor.map(label_cell, tasks))
    labels = pd.concat(parts).loc[population.index]
    return pd.concat([population, labels], axis=1)


# --------------------------------------------------------------------------- #
# 대조 — 축마다 (좋은 쪽, 나쁜 쪽) 마스크 · 방향은 가설이 양수를 예측하도록
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AxisSpec:
    """한 판정 점의 버킷 규칙 — 앞구간(`is`)에서 정하고 뒷구간에 그대로 적용한다."""

    axis: str
    timeframe: str
    edges: tuple[float, ...]
    """B·C: 분위 경계. D: 버킷 하한 목록(겹침 개수). A: 비어 있음."""


D_FLOORS: tuple[float, ...] = (0.0, 1.0)
"""D 버킷 = **0개 대 1개 이상** — 사용자 정의(2026-09-18 「하나라도 겹치면 진입」). 개수로 등급을
나누지 않는다(선이 몇 개냐가 아니라 **겹치느냐**가 진입 조건이다)."""


def spec_for(labeled: pd.DataFrame, axis: str, timeframe: str) -> AxisSpec:
    is_frame = labeled[(labeled["segment"] == SEGMENT_IS) & (labeled["timeframe"] == timeframe)]
    if axis == AXIS_A:
        return AxisSpec(axis, timeframe, ())
    if axis == AXIS_D:
        return AxisSpec(axis, timeframe, D_FLOORS)
    column = "b_energy" if axis == AXIS_B else "c_trend_dev"
    return AxisSpec(axis, timeframe, tuple(bin_edges(is_frame[column], QUANTILES)))


def bucket_series(frame: pd.DataFrame, spec: AxisSpec, *, column: str | None = None) -> pd.Series:
    """셋업 → 버킷 번호(0 = 가장 낮음). A는 클래스 문자열을 그대로."""
    if spec.axis == AXIS_A:
        return frame["a_class"]
    if spec.axis == AXIS_D:
        values = frame[column or "d_count"]
        floors = list(spec.edges)
        out: list[int | None] = []
        for v in values.tolist():
            if v is None or (isinstance(v, float) and math.isnan(v)):
                out.append(None)
                continue
            out.append(int(sum(1 for f in floors if float(v) >= f) - 1))
        return pd.Series(out, index=frame.index, dtype="Int64")
    col = "b_energy" if spec.axis == AXIS_B else "c_trend_dev"
    return assign_bins(frame[col], spec.edges)


def contrast_masks(
    frame: pd.DataFrame, spec: AxisSpec, *, column: str | None = None
) -> tuple[pd.Series, pd.Series]:
    """(좋은 쪽, 나쁜 쪽) — 가설이 `좋은 − 나쁜 > 0`을 예측한다."""
    buckets = bucket_series(frame, spec, column=column)
    if spec.axis == AXIS_A:
        return buckets == CLASS_SWEPT, buckets == CLASS_UNSWEPT
    n_bins = len(spec.edges) - 1 if spec.axis != AXIS_D else len(spec.edges)
    b = buckets.astype("Int64")
    if spec.axis == AXIS_B:
        ends = (b == 0) | (b == n_bins - 1)
        good = b.between(1, n_bins - 2)
        return good.fillna(False).astype(bool), ends.fillna(False).astype(bool)
    return (b == n_bins - 1).fillna(False).astype(bool), (b == 0).fillna(False).astype(bool)


def linear_masks(frame: pd.DataFrame, spec: AxisSpec) -> tuple[pd.Series, pd.Series] | None:
    """B 참고 열 — 선형 `top − bottom`(판정 점 아님)."""
    if spec.axis != AXIS_B:
        return None
    b = bucket_series(frame, spec).astype("Int64")
    n_bins = len(spec.edges) - 1
    return (b == n_bins - 1).fillna(False).astype(bool), (b == 0).fillna(False).astype(bool)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BucketRow:
    axis: str
    timeframe: str
    segment: str
    bucket: str
    lo: float | None
    hi: float | None
    n: int
    share: float
    stop_rate: float
    mean_net_r: float
    net_r_se: float
    mean_stop_width: float


def _bucket_label(spec: AxisSpec, key: object) -> tuple[str, float | None, float | None]:
    if spec.axis == AXIS_A:
        return str(key), None, None
    k = int(key)  # type: ignore[call-overload]
    if spec.axis == AXIS_D:
        lo = spec.edges[k]
        hi = spec.edges[k + 1] - 1 if k + 1 < len(spec.edges) else None
        name = f"{int(lo)}" if hi is not None and hi == lo else f"{int(lo)}+"
        if hi is not None and hi > lo:
            name = f"{int(lo)}~{int(hi)}"
        return name, float(lo), (float(hi) if hi is not None else None)
    return f"Q{k + 1}", float(spec.edges[k]), float(spec.edges[k + 1])


def bucket_rows(labeled: pd.DataFrame, specs: Sequence[AxisSpec]) -> list[BucketRow]:
    rows: list[BucketRow] = []
    for spec in specs:
        for segment in SEGMENTS:
            sub = labeled[
                (labeled["segment"] == segment) & (labeled["timeframe"] == spec.timeframe)
            ]
            buckets = bucket_series(sub, spec)
            total = len(sub)
            keys: Sequence[object]
            if spec.axis == AXIS_A:
                keys = [CLASS_SWEPT, CLASS_UNSWEPT, CLASS_NONE, CLASS_MISSING]
            else:
                keys = sorted({int(k) for k in buckets.dropna().tolist()})
            for key in keys:
                cell = sub[(buckets == key).fillna(False).astype(bool)]
                if cell.empty:
                    continue
                name, lo, hi = _bucket_label(spec, key)
                nets = cell["net_r"]
                rows.append(
                    BucketRow(
                        axis=spec.axis,
                        timeframe=spec.timeframe,
                        segment=segment,
                        bucket=name,
                        lo=lo,
                        hi=hi,
                        n=len(cell),
                        share=len(cell) / total if total else float("nan"),
                        stop_rate=float(cell["is_stop"].mean()),
                        mean_net_r=float(nets.mean()),
                        net_r_se=float(nets.std(ddof=1) / math.sqrt(len(nets)))
                        if len(nets) > 1
                        else 0.0,
                        mean_stop_width=float(cell["stop_width"].mean()),
                    )
                )
    return rows


@dataclass(frozen=True)
class VerdictRow:
    axis: str
    timeframe: str
    n_good_is: int
    n_bad_is: int
    n_good_oos: int
    n_bad_oos: int
    delta_is: float | None
    delta_oos: float | None
    sigma_oos: float | None
    z_oos: float | None
    stop_rate_delta_oos: float | None
    delta_no_crash: float | None
    delta_pooled: float | None
    delta_ex20: float | None
    delta_linear_oos: float | None
    verdict: str


def verdict_for(
    *,
    n_good_is: int,
    n_bad_is: int,
    n_good_oos: int,
    n_bad_oos: int,
    delta_is: float | None,
    delta_oos: float | None,
    sigma_oos: float | None,
    delta_no_crash: float | None,
    delta_pooled: float | None,
    delta_ex20: float | None,
    is_line_axis: bool,
) -> str:
    """착수 전에 못 박은 다섯(D는 여섯) 관문을 **코드가** 적용한다."""
    if (
        min(n_good_is, n_bad_is, n_good_oos, n_bad_oos) < MIN_GROUP_N
        or delta_oos is None
        or sigma_oos is None
        or delta_is is None
    ):
        return VERDICT_UNDECIDED
    decided = abs(delta_oos) > decision_z() * sigma_oos and abs(delta_oos) > NOISE_R
    if not decided:
        return VERDICT_NO_SELECTION
    if delta_oos < 0:
        return VERDICT_REVERSE
    if delta_is <= 0:
        return VERDICT_IS_MISMATCH
    if delta_no_crash is None or delta_no_crash <= 0 or delta_pooled is None or delta_pooled <= 0:
        return VERDICT_CRASH_PROXY
    if is_line_axis and (delta_ex20 is None or delta_ex20 <= 0):
        return VERDICT_BOLLINGER_ALIAS
    return VERDICT_PASS


def _seg(labeled: pd.DataFrame, segment: str, timeframe: str) -> pd.DataFrame:
    return labeled[(labeled["segment"] == segment) & (labeled["timeframe"] == timeframe)]


def verdict_row(labeled: pd.DataFrame, spec: AxisSpec) -> VerdictRow:
    is_sub = _seg(labeled, SEGMENT_IS, spec.timeframe)
    oos = _seg(labeled, PRIMARY_SEGMENT, spec.timeframe)
    g_is, b_is = contrast_masks(is_sub, spec)
    g_o, b_o = contrast_masks(oos, spec)
    d_is, _ = _delta(is_sub[g_is]["net_r"], is_sub[b_is]["net_r"])
    d_o, s_o = _delta(oos[g_o]["net_r"], oos[b_o]["net_r"])
    stop_delta = (
        float(oos[g_o]["is_stop"].mean() - oos[b_o]["is_stop"].mean())
        if g_o.any() and b_o.any()
        else None
    )
    calm = oos[~oos["is_crash_day"].astype(bool)]
    g_c, b_c = contrast_masks(calm, spec)
    d_calm, _ = _delta(calm[g_c]["net_r"], calm[b_c]["net_r"])
    d_pool, _, _ = _pooled(oos[g_o], oos[b_o])
    d_ex20: float | None = None
    if spec.axis == AXIS_D:
        g_x, b_x = contrast_masks(oos, spec, column="d_count_ex20")
        d_ex20, _ = _delta(oos[g_x]["net_r"], oos[b_x]["net_r"])
    lin = linear_masks(oos, spec)
    d_lin = _delta(oos[lin[0]]["net_r"], oos[lin[1]]["net_r"])[0] if lin else None
    verdict = verdict_for(
        n_good_is=int(g_is.sum()),
        n_bad_is=int(b_is.sum()),
        n_good_oos=int(g_o.sum()),
        n_bad_oos=int(b_o.sum()),
        delta_is=d_is,
        delta_oos=d_o,
        sigma_oos=s_o,
        delta_no_crash=d_calm,
        delta_pooled=d_pool,
        delta_ex20=d_ex20,
        is_line_axis=spec.axis == AXIS_D,
    )
    return VerdictRow(
        axis=spec.axis,
        timeframe=spec.timeframe,
        n_good_is=int(g_is.sum()),
        n_bad_is=int(b_is.sum()),
        n_good_oos=int(g_o.sum()),
        n_bad_oos=int(b_o.sum()),
        delta_is=d_is,
        delta_oos=d_o,
        sigma_oos=s_o,
        z_oos=(abs(d_o) / s_o) if d_o is not None and s_o else None,
        stop_rate_delta_oos=stop_delta,
        delta_no_crash=d_calm,
        delta_pooled=d_pool,
        delta_ex20=d_ex20,
        delta_linear_oos=d_lin,
        verdict=verdict,
    )


@dataclass(frozen=True)
class LooRow:
    axis: str
    timeframe: str
    dropped: str
    delta_oos: float | None
    sign_kept: bool | None


def loo_rows(labeled: pd.DataFrame, specs: Sequence[AxisSpec]) -> list[LooRow]:
    """종목 하나씩 빼고 뒷구간 차의 부호가 남나(버킷 경계는 전체 앞구간 그대로 — 재집계)."""
    rows: list[LooRow] = []
    for spec in specs:
        oos = _seg(labeled, PRIMARY_SEGMENT, spec.timeframe)
        g, b = contrast_masks(oos, spec)
        full, _ = _delta(oos[g]["net_r"], oos[b]["net_r"])
        for dropped in sorted(oos["symbol"].unique()):
            sub = oos[oos["symbol"] != dropped]
            gs, bs = contrast_masks(sub, spec)
            d, _ = _delta(sub[gs]["net_r"], sub[bs]["net_r"])
            kept = None if d is None or full is None else (d > 0) == (full > 0)
            rows.append(LooRow(spec.axis, spec.timeframe, dropped, d, kept))
    return rows


@dataclass(frozen=True)
class CorrRow:
    """축 ↔ 폭락일 대리변수(WAN-410 「그날 실현 net R」) · 축끼리 — 순위상관(뒷구간)."""

    timeframe: str
    left: str
    right: str
    n: int
    spearman: float | None


_CONT = {
    "B 접근에너지": "b_energy",
    "C 일봉추세": "c_trend_dev",
    "D 겹침수": "d_count",
    "A 스윕(1/0)": "a_swept_flag",
    "그날 실현 net R(WAN-410)": "realized_today_before",
}


def corr_rows(labeled: pd.DataFrame) -> list[CorrRow]:
    frame = labeled.copy()
    frame["a_swept_flag"] = np.where(
        frame["a_class"] == CLASS_SWEPT,
        1.0,
        np.where(frame["a_class"] == CLASS_UNSWEPT, 0.0, np.nan),
    )
    rows: list[CorrRow] = []
    names = list(_CONT)
    for tf in TIMEFRAMES:
        sub = _seg(frame, PRIMARY_SEGMENT, tf)
        for i, left in enumerate(names):
            for right in names[i + 1 :]:
                pair = sub[[_CONT[left], _CONT[right]]].dropna()
                # 스피어먼 = 순위의 피어슨(scipy 없이 — 의존성을 늘리지 않는다).
                ranks = pair.rank()
                rho = float(ranks[_CONT[left]].corr(ranks[_CONT[right]])) if len(pair) > 2 else None
                rows.append(CorrRow(tf, left, right, len(pair), rho))
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChecksumRow:
    name: str
    value: float
    reference: float
    diff: float


ORIGINAL_COLUMNS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "segment",
    "zone",
    "trigger_time",
    "entry_time",
    "entry_price",
    "stop_price",
    "tp_on_reason",
    "tp_on_exit_time",
    "stop_width",
    "net_r",
)


def checksum_rows(population: pd.DataFrame, labeled: pd.DataFrame) -> list[ChecksumRow]:
    rows: list[ChecksumRow] = []
    for segment, expected in EXPECTED_SETUPS.items():
        n = int((population["segment"] == segment).sum())
        rows.append(
            ChecksumRow(
                f"(a) 모집단 {segment} ≡ WAN-375 표", float(n), float(expected), n - expected
            )
        )
    # (b) 원열이 값으로 같다.
    changed = 0
    for col in ORIGINAL_COLUMNS:
        left = population[col].reset_index(drop=True)
        right = labeled[col].reset_index(drop=True)
        changed += int((~((left == right) | (left.isna() & right.isna()))).sum())
    rows.append(
        ChecksumRow("(b) 라벨링이 바꾼 원열 값(셀 수)", float(changed), 0.0, float(changed))
    )
    # (c) 인과 — 각 축이 읽은 마지막 봉 마감 ≤ 탭 시각.
    for axis, col in (
        (AXIS_A, "a_last_close"),
        (AXIS_D, "d_last_close"),
        (AXIS_B, "b_last_close"),
        (AXIS_C, "c_last_close"),
    ):
        used = labeled[col].dropna().astype("int64")
        trig = labeled.loc[used.index, "trigger_time"].astype("int64")
        viol = int((used > trig).sum())
        rows.append(ChecksumRow(f"(c) 인과 위반 · {axis}", float(viol), 0.0, float(viol)))
    # (d) 분류의 전체성 — 축마다 라벨 있음 + 라벨 없음 = 모집단.
    n = len(labeled)
    a_total = int(
        labeled["a_class"].isin([CLASS_SWEPT, CLASS_UNSWEPT, CLASS_NONE, CLASS_MISSING]).sum()
    )
    rows.append(ChecksumRow("(d) 전체성 · A 네 클래스 합", float(a_total), float(n), a_total - n))
    for axis, col in ((AXIS_D, "d_count"), (AXIS_B, "b_energy"), (AXIS_C, "c_trend_dev")):
        have = int(labeled[col].notna().sum())
        missing = int(labeled[col].isna().sum())
        rows.append(
            ChecksumRow(
                f"(d) 전체성 · {axis} 라벨 {have} + 없음 {missing}",
                float(have + missing),
                float(n),
                float(have + missing - n),
            )
        )
    # (f) 존 경계 = 엔진이 쓴 아카이브(바닥 ≡ 손절가).
    mism = int((~labeled["zone_matches_stop"].astype(bool)).sum())
    rows.append(ChecksumRow("(f) 아카이브 존 바닥 ≢ 셋업 손절가", float(mism), 0.0, float(mism)))
    # (g) D의 선 길이가 채택 파라미터와 같다(두 벌로 적지 않았다는 확인).
    params = harness.build_params()
    same = int(
        tuple(params.display_ema_lengths) == LINE_EMA_LENGTHS
        and params.tp_vwma_length == LINE_VWMA_LENGTH
    )
    rows.append(
        ChecksumRow("(g) D 선 = display_ema_lengths + tp_vwma_length", float(same), 1.0, same - 1.0)
    )
    return rows


def evenness_rows(labeled: pd.DataFrame, specs: Sequence[AxisSpec]) -> list[ChecksumRow]:
    """(e) B·C 분위가 앞구간 표본을 고르게 나누나 — 분위 몫의 최소·최대(이상 0.20)."""
    rows: list[ChecksumRow] = []
    for spec in specs:
        if spec.axis not in (AXIS_B, AXIS_C):
            continue
        sub = _seg(labeled, SEGMENT_IS, spec.timeframe)
        b = bucket_series(sub, spec).dropna()
        shares = b.value_counts(normalize=True)
        worst = float((shares - 1.0 / QUANTILES).abs().max()) if len(shares) else float("nan")
        rows.append(
            ChecksumRow(
                f"(e) 균등 분할 · {spec.axis} {spec.timeframe} (분위 {len(shares)}개, 최대 편차)",
                worst,
                0.0,
                worst,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _f(value: object, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{float(value):+.{digits}f}"  # type: ignore[arg-type]


def _pct(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{float(value) * 100:.1f}%"  # type: ignore[arg-type]


def _table(headers: Sequence[str], cells: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in cells)
    return lines


def render_summary(
    *,
    buckets: pd.DataFrame,
    verdicts: pd.DataFrame,
    loo: pd.DataFrame,
    corr: pd.DataFrame,
    checksum: pd.DataFrame,
    bad_days: Sequence[str],
    elapsed: float | None,
    census: pd.DataFrame | None = None,
) -> str:
    lines: list[str] = [
        "# WAN-421 — 존이 뚫리기 전에 가격이 무엇을 했나",
        "",
        "재현: `uv run python -m backtest.wan421_pre_break_context --jobs 4`"
        " (요약만 `--from-csv`). 모집단 = WAN-375 셋업 표(`band` · 첫 탭 · 가드 통과 ·"
        " 데이터 끝 제외) · 상위TF 봉만 · 핀 없음 · `baseline` 렌즈.",
        "",
        f"판정 점 {TESTS}개(축 4 × TF 4) · Bonferroni z = {decision_z():.3f} · 노이즈선 ±{NOISE_R}R"
        f" · 표본 게이트 {MIN_GROUP_N} · 폭락일 = WAN-408 최악 {len(bad_days)}일"
        f"({', '.join(bad_days) if bad_days else '없음'}).",
        "",
        "## 판정 (코드가 낸다 · 대조는 `가설이 좋다고 한 쪽 − 나쁘다고 한 쪽`, 가설은 양수를 예측)",
        "",
    ]
    passed = verdicts[verdicts["verdict"] == VERDICT_PASS]
    if passed.empty:
        lines.append(
            "📌 **16점 중 「간다」 0개** — 네 가설 어느 것도 착수 전에 못 박은 관문을 전부"
            " 통과하지 못했다. 사유 분포:"
        )
    else:
        pts = ", ".join(f"{r.axis}·{r.timeframe}" for r in passed.itertuples())
        lines.append(f"📌 **16점 중 「간다」 {len(passed)}개**: {pts}. 사유 분포:")
    lines.append("")
    for reason, count in verdicts["verdict"].value_counts().items():
        lines.append(f"* {reason}: {count}")
    lines.append("")
    cells = [
        [
            str(r.axis),
            str(r.timeframe),
            f"{r.n_good_oos}/{r.n_bad_oos}",
            _f(r.delta_is),
            _f(r.delta_oos),
            "—" if pd.isna(r.z_oos) else f"{float(r.z_oos):.2f}",
            _pct(r.stop_rate_delta_oos),
            _f(r.delta_no_crash),
            _f(r.delta_pooled),
            _f(r.delta_ex20),
            _f(r.delta_linear_oos),
            str(r.verdict),
        ]
        for r in verdicts.itertuples()
    ]
    lines.extend(
        _table(
            [
                "축",
                "TF",
                "n 좋은/나쁜(oos)",
                "Δ is",
                "Δ oos_warm",
                "z",
                "손절률 Δ(oos)",
                "Δ 폭락일 제외",
                "Δ WAN-410 층화",
                "Δ EMA20 제외(D)",
                "Δ 선형 top−bottom(B 참고)",
                "판정",
            ],
            cells,
        )
    )
    lines += [
        "",
        "⚠️ **손절률 Δ는 병기이고 결론은 net R이다**(WAN-154). 음수 손절률 Δ = 가설이 좋다고 한 쪽이"
        " 덜 손절난다.",
        "",
        "## 버킷 표 (뒷구간 `oos_warm`)",
        "",
    ]
    warm = buckets[buckets["segment"] == PRIMARY_SEGMENT]
    for axis in AXES:
        lines.append(f"### {axis}")
        lines.append("")
        sub = warm[warm["axis"] == axis]
        cells = [
            [
                str(r.timeframe),
                str(r.bucket),
                str(r.n),
                _pct(r.share),
                _pct(r.stop_rate),
                _f(r.mean_net_r),
                f"±{float(r.net_r_se):.4f}",
                _pct(r.mean_stop_width),
            ]
            for r in sub.itertuples()
        ]
        lines.extend(
            _table(["TF", "버킷", "n", "몫", "손절률", "net R", "SE", "손절폭 평균"], cells)
        )
        lines.append("")
    if census is not None and not census.empty:
        lines += [
            "## D 인구조사 — 존 안에 든 선의 개수(판정은 0개 대 1개 이상)",
            "",
        ]
        cells = []
        for (tf, segment), g in census.groupby(["timeframe", "segment"], sort=False):
            dist = " · ".join(f"{int(r.count)}:{int(r.n)}" for r in g.itertuples() if r.n)
            cells.append([str(tf), str(segment), dist, str(g["floors"].iloc[0])])
        lines.extend(_table(["TF", "구간", "개수:셋업 수", "판정 버킷 하한"], cells))
        lines += [""]
    lines += ["## 폭락일 대리변수 점검 — 순위상관(뒷구간)", ""]
    proxy = corr[corr["right"] == "그날 실현 net R(WAN-410)"]
    cells = [
        [str(r.timeframe), str(r.left), str(r.n), _f(r.spearman, 3)] for r in proxy.itertuples()
    ]
    lines.extend(_table(["TF", "축", "n", "ρ(축, 그날 실현 net R)"], cells))
    lines += [
        "",
        "📌 WAN-402의 「동시 붕괴」는 이 상관이 **−0.80**이었다(= 같은 것의 다른 이름)."
        " 크기를 그것과 대조해 읽는다.",
        "",
        "## leave-one-out (종목 하나씩 빼고 뒷구간 Δ 부호)",
        "",
    ]
    grouped = loo.groupby(["axis", "timeframe"], sort=False)
    cells = []
    for (axis, tf), g in grouped:
        kept = int(g["sign_kept"].fillna(False).astype(bool).sum())
        cells.append([str(axis), str(tf), f"{kept}/{len(g)}"])
    lines.extend(_table(["축", "TF", "부호 유지"], cells))
    lines += ["", "## 검산", ""]
    cells = [
        [str(r.name), f"{float(r.value):g}", f"{float(r.reference):g}", f"{float(r.diff):.2e}"]
        for r in checksum.itertuples()
    ]
    lines.extend(_table(["항목", "값", "기준", "차"], cells))
    lines += [
        "",
        "## 읽는 법 · 경고",
        "",
        "* 🚨 **셋업 층이다 — 채택 좌표가 아니다.** 시퀀싱·공유 자본·재진입이 없다."
        " 채택 북 성적과 나란히 놓지 말 것. 거르는 팔은 **북에서** 재야 한다(WAN-341).",
        "* **필터를 만들지 않았다** — 라벨링과 집계뿐이다."
        " A2(쓸고 되돌아옴)는 안 쟀다(WAN-419 함정).",
        "* 전부 `baseline`(낙관) 렌즈 위 값 · `pen_5bp` 미측정 · WAN-408/410과 **더하지 말 것**.",
        "* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는"
        " *뚫리기 전에 가격이 무엇을 했나*를 묻는다(다른 질문).",
    ]
    if elapsed is not None:
        lines += ["", f"실측 라벨링 시간: {elapsed:.0f}초."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _records(items: Sequence[object]) -> pd.DataFrame:
    return pd.DataFrame.from_records([asdict(i) for i in items])  # type: ignore[call-overload]


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--pilot", action="store_true", help="BTC 4h 한 칸만")
    parser.add_argument("--from-csv", action="store_true", help="캐시된 라벨로 요약만")
    parser.add_argument(
        "--cache-check",
        action="store_true",
        help="WAN-375 셋업 캐시가 지금 코드의 산출물인지 BTC 4h 한 칸으로 대조(1분봉을 읽는다)",
    )
    return parser.parse_args(argv)


def d_census_frame(labeled: pd.DataFrame, specs: Sequence[AxisSpec]) -> pd.DataFrame:
    """D 인구조사 — 구간·TF마다 겹침 개수 분포(판정 버킷은 0개 대 1개 이상으로 고정)."""
    floors = {s.timeframe: s.edges for s in specs if s.axis == AXIS_D}
    records: list[dict[str, object]] = []
    for tf, edges in floors.items():
        for segment in SEGMENTS:
            counts = _seg(labeled, segment, tf)["d_count"].dropna().astype(int)
            for level in range(0, len(LINE_EMA_LENGTHS) + 2):
                records.append(
                    {
                        "timeframe": tf,
                        "segment": segment,
                        "count": level,
                        "n": int((counts == level).sum()),
                        "floors": "/".join(str(int(f)) for f in edges),
                    }
                )
    return pd.DataFrame.from_records(records)


def analyse(
    labeled: pd.DataFrame, *, bad_days: Sequence[str]
) -> tuple[list[AxisSpec], dict[str, pd.DataFrame]]:
    timeframes = [tf for tf in TIMEFRAMES if (labeled["timeframe"] == tf).any()]
    specs = [spec_for(labeled, axis, tf) for axis in AXES for tf in timeframes]
    frames = {
        "buckets": _records(bucket_rows(labeled, specs)),
        "verdicts": _records([verdict_row(labeled, s) for s in specs]),
        "loo": _records(loo_rows(labeled, specs)),
        "corr": _records(corr_rows(labeled)),
        "census": d_census_frame(labeled, specs),
    }
    return specs, frames


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    population = load_population()
    if args.cache_check:
        compared, mism = cache_reproduction_mismatches(population, symbol="BTCUSDT", timeframe="4h")
        print(f"[wan421] WAN-375 캐시 재현 대조 BTC 4h: {compared}건 중 불일치 {mism}")
        return 0 if mism == 0 else 1
    bad_days = worst_days(count=WORST_DAYS)
    elapsed: float | None = None
    if args.from_csv:
        labeled = pd.read_csv(LABELED_CSV, index_col=0)
    else:
        target = population
        if args.pilot:
            target = population[
                (population["symbol"] == "BTC/USDT:USDT") & (population["timeframe"] == "4h")
            ]
        jobs = args.jobs if args.jobs is not None else harness.default_jobs()
        print(f"병렬 설정: 워커 {jobs}개", file=sys.stderr)
        started = time.monotonic()
        labeled = label_population(target, jobs=jobs)
        labeled = attach_controls(labeled, bad_days=bad_days)
        elapsed = time.monotonic() - started
        if not args.pilot:
            LABELED_CSV.parent.mkdir(parents=True, exist_ok=True)
            labeled.to_csv(LABELED_CSV)
    specs, frames = analyse(labeled, bad_days=bad_days)
    base = population.loc[labeled.index] if args.pilot else population
    checksum = _records(checksum_rows(base, labeled) + evenness_rows(labeled, specs))
    if args.pilot:
        print(frames["verdicts"].to_string())
        print(checksum.to_string())
        print(labeled["a_class"].value_counts())
        print(labeled["d_count"].value_counts())
        return 0
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    frames["buckets"].to_csv(BUCKETS_CSV, index=False)
    frames["verdicts"].to_csv(VERDICT_CSV, index=False)
    frames["loo"].to_csv(LOO_CSV, index=False)
    frames["corr"].to_csv(CORR_CSV, index=False)
    checksum.to_csv(CHECKSUM_CSV, index=False)
    summary = render_summary(
        buckets=frames["buckets"],
        verdicts=frames["verdicts"],
        loo=frames["loo"],
        corr=frames["corr"],
        checksum=checksum,
        bad_days=bad_days,
        elapsed=elapsed,
        census=frames["census"],
    )
    SUMMARY_PATH.write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
