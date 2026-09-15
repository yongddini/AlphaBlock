"""WAN-417 §G-0 — 존이 깨질 때와 반등할 때 미결제약정(OI)은 어떻게 움직였나 (사건 정렬 관측).

## 한 줄

사용자 지시(2026-09-15 *「일단 존이 깨질 때와 반등할 때의 OI 변화를 보고싶은건데」*)로 이 이슈는
**§G-0 한 축만** 잰다. 진입 축이 세 번 죽은 뒤(WAN-403 위치 · WAN-375 폭 · WAN-402 동시 붕괴)
남은 유일한 차별점은 **우리 시스템 밖의 정보**(거래소가 발표하는 미결제약정)라는 것 하나다.

## 🚨 정렬이 둘인 이유 — 착수 지시의 판정 줄에 룩어헤드가 있었다

착수 지시는 *「청산 시각을 0으로 맞추고 0 대비 변화율 · 두 경로가 0 왼쪽에서 갈리면 선행」*
이었다. 그대로 돌리자 네 판이 전부 「선행」(z 최대 73)이 나왔고, 부검하니 **구조가 만든 값**이었다:

1. **0점이 결과 뒤다** — 손절 청산 순간 가격은 막 1R 내려왔고 익절 순간은 1.5R 올라왔다.
   그 순간 값으로 나눈 변화율은 결과를 분모에 싣는다(USDT 명목 = 코인 수량 × 가격이라 특히 크다).
2. **청산의 「0 왼쪽」은 보유 구간이다** — `oos_warm` 거래의 **65.7%가 2시간 안에** 청산된다
   (15m 손절 보유 중앙값 23분). −2h~−5m 점은 대부분 **진입 뒤**라 진입 순간에 알 수 없다.

그래서 **두 정렬을 함께 낸다**(착수 전에 못 박은 창·점·관문·통제는 그대로 · 0점만 옮김):

* `exit`(청산 정렬) = 사용자가 보고 싶었던 **사건 그림 그대로** — **관측용**이고 판정하지 않는다.
  같은 관문을 돌려 「통과했을 것」도 표에 싣는다(함정을 지우지 않고 보이게 둔다).
* `entry`(진입 정렬) = **판정용** — 0점 = 체결 시각 이하 마지막 스냅샷. 판정 점(−24h~−5m)이
  전부 진입 전이고 분모도 진입 순간 값이라 **그 순간에 아는 정보만** 쓴다.

## 정의 — 착수 전에 못 박음 (이슈 코멘트 2026-09-15 「실행 지시서」 · 코드 상수)

* **모집단** = WAN-375 셋업 표(`band` · 첫 탭만 · 가드 통과 · 데이터 끝 제외) —
  `wan402.load_population`을 **그대로** 부른다(모집단을 두 벌 만들지 않는다).
* **두 집단** = 같은 표에서 **손절**로 끝난 거래(깨짐) vs **익절**로 끝난 거래(반등).
* **창** = 0점 기준 `−24h ~ +4h` · **5분 원해상도**(집계하지 않는다).
* **정규화** = 0점 대비 변화율 `OI(0+k) / OI(0) − 1`. 두 단위(코인 수량 · USDT 명목) **나란히**.
* **범위** = 자기 종목 OI와 **BTC(시장) OI**를 따로.
* **0 오른쪽은 관측용** — `SIGNAL_MAX_OFFSET_MS`(= −5분)가 경계. 판정 점은 **착수 전에 고른
  7개**(`LEAD_OFFSETS_MS`)이고 결과를 보고 늘리지 않는다(WAN-161).
* **유의** = 2σ 관문(WAN-412 자)을 점 7 × 단위 2 × 범위 2 = **28**로 Bonferroni 보정.
* **통제 둘** — (1) 폭락일 제외(WAN-408 최악 10일 · 진입일 또는 청산일), (2) WAN-410 「그날 실현
  net R 누적」 버킷 안 층화. 앞구간 같은 부호까지 **관문 넷**을 다 넘어야 「선행」이다.
* **보간 금지** — **두 정렬 중 하나라도** 창에 스냅샷이 빠진 셋업은 **제외**(두 판이 같은 셋업을
  보게)하고 그 수를 인구조사에 싣는다. OI가 0으로 적힌 게시 결함 행도 구멍이 된다.

## 데이터 규약 (0단계 실측 · `docs/decisions/wan417.md` §1)

간격 **300초**(격자 위 최소 차) · 🚨 `create_time`은 **2024-03-03까지 값을 잰 시각 · 2024-03-04부터
5분 구간 시작 라벨(값은 +5분)** — 파서가 잰 시각으로 되돌리고 `--part audit`이 1분봉과 대조해 매번
검산한다 · 옛 파일은 행이 **두 번씩**
적혀 「577행」이 났다 · 두 단위 열 다 있음 · 첫 날짜 **BTC 2020-09-01 · 나머지 11종목 2021-12-01**
· OI=0 게시 결함 2,703행(12종목이 같은 달에) · 날 넘김 행 5 · 격자 밖 17.

## 재현

    uv run python -m backtest.wan417_oi_event_study --part census   # 0단계(목록·받기·규약)
    uv run python -m backtest.wan417_oi_event_study --part paths    # 라벨링(경로 행렬)
    uv run python -m backtest.wan417_oi_event_study --part summary  # 요약만(캐시에서)
    uv run python -m backtest.wan417_oi_event_study                 # 전부

측정 전용 · 엔진·기본값·토대 불변 · **필터를 만들지 않는다**(WAN-341/323) · 핀 없음(WAN-305).
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from backtest import harness
from backtest.wan375_conditional_rr import CostRates, _ev_at, worst_days
from backtest.wan402_concurrent_breaks import (
    EXPECTED_OOS_WARM_SETUPS,
    assign_bins,
    bin_edges,
    load_population,
    population_census,
)
from backtest.wan408_loss_clustering import (
    REALIZED_BUCKETS,
    TradeFact,
    _bucket_label_realized,
    _bucket_order_realized,
)
from backtest.wan410_daily_loss_circuit_breaker import realized_r_before
from common.timefmt import kst_day_key
from data.agg_trade_archive import archive_symbol
from data.oi_metrics_archive import (
    DEFAULT_CACHE_DIR,
    OI_COLUMNS,
    SNAPSHOT_INTERVAL_MS,
    MetricsSeries,
    day_range,
    fetch_days,
    list_available_days,
    load_series,
)

REPORTS_DIR = Path("backtest/reports")
CENSUS_CSV = REPORTS_DIR / "wan417_oi_census.csv"
EXCLUSION_CSV = REPORTS_DIR / "wan417_oi_exclusions.csv"
PATHS_CSV = REPORTS_DIR / "wan417_oi_paths.csv"
VERDICT_CSV = REPORTS_DIR / "wan417_oi_verdict.csv"
LEAD_CSV = REPORTS_DIR / "wan417_oi_lead_points.csv"
BINS_CSV = REPORTS_DIR / "wan417_oi_bins.csv"
LOO_CSV = REPORTS_DIR / "wan417_oi_loo.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan417_oi_checksum.csv"
HOLD_CSV = REPORTS_DIR / "wan417_oi_holding.csv"
AUDIT_CSV = REPORTS_DIR / "wan417_oi_timestamp_audit.csv"
DECOMP_CSV = REPORTS_DIR / "wan417_oi_price_decomposition.csv"
FIGURE_SVG = REPORTS_DIR / "wan417_oi_paths.svg"
SUMMARY_PATH = REPORTS_DIR / "wan417_oi_event_study_summary.md"
#: 원자료(라벨 · 경로 행렬) — 커밋하지 않는다(`backtest/cache/`는 gitignore).
LABELS_CSV = Path("backtest/cache/wan417/labels.csv.gz")
PATHS_NPZ = Path("backtest/cache/wan417/paths.npz")

SEGMENT_IS = harness.SEGMENT_IS
SEGMENT_OOS_WARM = harness.SEGMENT_OOS_WARM
SEGMENTS: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS_WARM)
PRIMARY_SEGMENT = SEGMENT_OOS_WARM

# --------------------------------------------------------------------------- #
# 착수 전에 못 박은 상수 — 결과를 보고 옮기지 않는다
# --------------------------------------------------------------------------- #

WINDOW_BEFORE_MS = 24 * 3_600_000
WINDOW_AFTER_MS = 4 * 3_600_000
INTERVAL_MS = SNAPSHOT_INTERVAL_MS
OFFSETS_MS: tuple[int, ...] = tuple(
    range(-WINDOW_BEFORE_MS, WINDOW_AFTER_MS + INTERVAL_MS, INTERVAL_MS)
)
"""창의 점 337개(−24h … +4h · 5분)."""
SIGNAL_MAX_OFFSET_MS = -INTERVAL_MS
"""🚨 「0 왼쪽」의 경계 — 이 값 **이하**의 오프셋만 신호 후보다. 0과 오른쪽은 관측용."""
LEAD_OFFSETS_MS: tuple[int, ...] = (
    -24 * 3_600_000,
    -12 * 3_600_000,
    -6 * 3_600_000,
    -2 * 3_600_000,
    -1 * 3_600_000,
    -30 * 60_000,
    -5 * 60_000,
)
"""판정에 쓰는 점 — 착수 전에 고정. 전부 `SIGNAL_MAX_OFFSET_MS` 이하(테스트가 잠근다)."""
UNITS: tuple[tuple[str, str], ...] = (("coin", OI_COLUMNS[0]), ("usdt", OI_COLUMNS[1]))
"""(라벨, 파일 열) — 코인 수량 · USDT 명목. 둘 다 낸다."""
SCOPE_SELF = "self"
SCOPE_BTC = "btc"
SCOPES: tuple[str, ...] = (SCOPE_SELF, SCOPE_BTC)
MARKET_SYMBOL = "BTCUSDT"

ALIGN_ENTRY = "entry"
"""판정용 정렬 — 0점 = 체결 시각(`entry_time`). 판정 점·분모가 전부 진입 순간에 아는 값이다."""
ALIGN_EXIT = "exit"
"""관측용 정렬 — 0점 = 청산 시각(`tp_on_exit_time`). 분모가 결과 뒤라 **판정하지 않는다**."""
ALIGNS: tuple[str, ...] = (ALIGN_ENTRY, ALIGN_EXIT)
VERDICT_ALIGN = ALIGN_ENTRY
ANCHOR_COLUMN: dict[str, str] = {ALIGN_ENTRY: "entry_time", ALIGN_EXIT: "tp_on_exit_time"}

SIGMA_MULTIPLE = 2.0
"""WAN-412 `_sign_is_decided` 자 — 두 집단 표준오차 합성의 2σ."""
TESTS = len(LEAD_OFFSETS_MS) * len(UNITS) * len(SCOPES)
"""다중검정 — 점 7 × 단위 2 × 범위 2 = 28(Bonferroni · 판정 정렬 하나에 대해)."""
MIN_GROUP_N = 100
"""두 집단 중 한쪽이 이보다 적으면 그 점은 부호를 내지 않는다(WAN-375/402 규약)."""
QUANTILES = 5
WORST_DAYS = 10

CONTROL_NONE = "없음"
CONTROL_NO_CRASH = "폭락일 제외"
CONTROL_REALIZED = "WAN-410 버킷 안"
CONTROL_POOLED = "WAN-410 버킷 안(표본 가중 합)"

VERDICT_LEAD = "선행(진입 전에 갈린다)"
VERDICT_PROXY = "대리변수(통제하면 사라진다)"
VERDICT_NO = "안 갈린다"
VERDICT_UNDECIDED = "판정 불가"
VERDICT_INVALID = "무효(0점 = 청산 · 결과가 섞임 · 관측용)"

#: 창 하루 여유 — 청산이 창 첫날·마지막날이면 앞뒤 하루 파일이 필요하다.
ARCHIVE_DAY_LO = "2020-09-14"
ARCHIVE_DAY_HI = "2026-07-23"


def decision_z() -> float:
    """2σ(양측 α ≈ 4.55%)를 검정 개수(`TESTS`)로 Bonferroni 보정한 z."""
    alpha = 2.0 * (1.0 - NormalDist().cdf(SIGMA_MULTIPLE))
    return NormalDist().inv_cdf(1.0 - alpha / (2.0 * TESTS))


def offset_index(offset_ms: int) -> int:
    """오프셋(ms) → 경로 행렬 열 번호."""
    return (offset_ms + WINDOW_BEFORE_MS) // INTERVAL_MS


def offset_label(offset_ms: int) -> str:
    sign = "−" if offset_ms < 0 else "+"
    minutes = abs(offset_ms) // 60_000
    if minutes % 60 == 0:
        return f"{sign}{minutes // 60}h"
    return f"{sign}{minutes}m"


# --------------------------------------------------------------------------- #
# 0단계 — 목록 · 받기 · 규약 인구조사
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CensusRow:
    """종목 하나의 0단계 표 — 「값으로」 적는다(이슈 코멘트 완료기준 1)."""

    symbol: str
    first_listed_day: str
    last_listed_day: str
    wanted_days: int
    """창(`ARCHIVE_DAY_LO~HI`) 안의 날짜 수."""
    listed_in_window: int
    missing_in_window: int
    """창 안인데 아카이브에 없는 날(목록 기준) — 대부분 상장 전."""
    missing_before_first: int
    """그중 첫 날짜 이전(= 상장/집계 전)."""
    files_read: int
    snapshots: int
    min_gap_ms: int
    """이어 붙인 시계열의 격자 위 **최소** 스냅샷 차 — 간격의 실측값(첫 두 행이 아니다)."""
    gaps_regular: int
    """이웃 스냅샷 차가 정확히 간격인 수."""
    gaps_longer: int
    """간격보다 긴 차(= 그 사이 스냅샷이 빠졌다 · 날 사이 구멍 포함)."""
    raw_rows: int
    duplicate_rows: int
    conflicting_rows: int
    files_with_duplicates: int
    offgrid_rows: int
    """5분 격자에서 벗어나 **뺀** 스냅샷(끌어오지 않는다)."""
    spill_rows: int
    """다음 날 첫 1분 시각이 적혀 **뺀** 행(다음 날 파일의 몫)."""
    zero_rows: int
    """OI가 0 이하로 적혀 **뺀** 행(거래소 게시 결함 · 구멍이 된다)."""
    files_shifted: int
    """라벨 → 값을 잰 시각을 +5분 보정한 파일 수(2024-03-04부터)."""
    files_short: int
    """스냅샷이 288개 미만인 파일(하루 안의 구멍)."""
    missing_snapshots_in_files: int
    """읽은 파일들 안에서 빠진 스냅샷 수 합(288 − 실제)."""


def wanted_days() -> list[str]:
    return day_range(ARCHIVE_DAY_LO, ARCHIVE_DAY_HI)


def ensure_archive(
    symbols: Sequence[str], *, cache_dir: Path = DEFAULT_CACHE_DIR, jobs: int = 8
) -> dict[str, list[str]]:
    """종목마다 목록을 받고 창 안의 존재하는 날짜를 전부 확보한다. 반환 = 종목 → 목록 전체."""
    listed: dict[str, list[str]] = {}
    wanted = set(wanted_days())
    for symbol in symbols:
        bare = archive_symbol(symbol)
        days = list_available_days(bare)
        listed[bare] = days
        targets = [d for d in days if d in wanted]
        started = time.monotonic()
        results = fetch_days(bare, targets, cache_dir=cache_dir, jobs=jobs)
        failed = [r for r in results if not r.ok]
        downloaded = [r for r in results if r.ok and not r.cached]
        print(
            f"[wan417] {bare}: 목록 {len(days)}일({days[0]}..{days[-1]}) · 창 안 {len(targets)} · "
            f"받음 {len(downloaded)} · 캐시 {len(results) - len(downloaded) - len(failed)} · "
            f"실패 {len(failed)} · {time.monotonic() - started:.0f}s",
            flush=True,
        )
        if failed:
            raise RuntimeError(f"{bare}: 받지 못한 날 {len(failed)}개 — 첫 건 {failed[0]}")
    return listed


def census_rows(
    symbols: Sequence[str],
    listed: dict[str, list[str]],
    series_by: dict[str, MetricsSeries],
) -> list[CensusRow]:
    wanted = wanted_days()
    rows: list[CensusRow] = []
    for symbol in symbols:
        bare = archive_symbol(symbol)
        days = listed[bare]
        in_window = [d for d in days if wanted[0] <= d <= wanted[-1]]
        missing = [d for d in wanted if d not in set(days)]
        before_first = [d for d in missing if d < days[0]] if days else missing
        series = series_by[bare]
        min_gap = int(np.diff(series.times_ms).min()) if len(series.times_ms) > 1 else 0
        rows.append(
            CensusRow(
                symbol=bare,
                first_listed_day=days[0] if days else "",
                last_listed_day=days[-1] if days else "",
                wanted_days=len(wanted),
                listed_in_window=len(in_window),
                missing_in_window=len(missing),
                missing_before_first=len(before_first),
                files_read=len(series.days_read),
                snapshots=series.census.get("snapshots", 0),
                min_gap_ms=min_gap,
                gaps_regular=series.census.get("gaps_regular", 0),
                gaps_longer=series.census.get("gaps_longer", 0),
                raw_rows=series.census.get("raw_rows", 0),
                duplicate_rows=series.census.get("duplicate_rows", 0),
                conflicting_rows=series.census.get("conflicting_rows", 0),
                files_with_duplicates=series.census.get("files_with_duplicates", 0),
                offgrid_rows=series.census.get("offgrid_rows", 0),
                spill_rows=series.census.get("spill_rows", 0),
                zero_rows=series.census.get("zero_rows", 0),
                files_shifted=series.census.get("files_shifted", 0),
                files_short=series.census.get("files_short", 0),
                missing_snapshots_in_files=series.census.get("missing_snapshots_in_files", 0),
            )
        )
    return rows


def load_all_series(
    symbols: Sequence[str], *, cache_dir: Path = DEFAULT_CACHE_DIR
) -> dict[str, MetricsSeries]:
    """캐시에 있는 창 안의 날을 전부 읽는다(없는 날은 그냥 없다 — 보간 없음)."""
    days = wanted_days()
    out: dict[str, MetricsSeries] = {}
    for symbol in symbols:
        bare = archive_symbol(symbol)
        started = time.monotonic()
        out[bare] = load_series(bare, days, cache_dir=cache_dir)
        print(
            f"[wan417] {bare}: 스냅샷 {out[bare].census.get('snapshots', 0):,} · "
            f"{time.monotonic() - started:.0f}s",
            flush=True,
        )
    return out


# --------------------------------------------------------------------------- #
# 시각 검산 — 파일의 함의 가격이 「값을 잰 시각」의 1분봉 가격과 맞는가
# --------------------------------------------------------------------------- #

AUDIT_DAYS: tuple[str, ...] = ("2022-06-01", "2023-06-01", "2024-03-01", "2024-03-06", "2025-06-01")
"""전환일(2024-03-04) 양옆과 앞뒤 해에서 고른 날 — 착수 후 고정(결과를 보고 고르지 않았다)."""
AUDIT_LAGS_MIN: tuple[int, ...] = tuple(range(-10, 11))


@dataclass(frozen=True)
class AuditRow:
    symbol: str
    day: str
    snapshots: int
    best_lag_min: int
    err_bp_at_0: float
    err_bp_at_best: float
    err_bp_at_plus5: float


def timestamp_audit_rows(
    symbols: Sequence[str], series_by: dict[str, MetricsSeries]
) -> list[AuditRow]:
    """보정 뒤 시계열의 함의 가격(명목 ÷ 수량)을 1분봉 **시가**(= 그 분의 시작 순간 가격)와 시차별로
    대조한다. 보정이 맞으면 모든 (종목, 날)에서 **시차 0분**이 가장 잘 맞아야 한다(검산 (j))."""
    rows: list[AuditRow] = []
    for symbol in symbols:
        bare = archive_symbol(symbol)
        series = series_by[bare]
        for day in AUDIT_DAYS:
            lo = parse_day_ms(day)
            hi = lo + 86_400_000
            sel = (series.times_ms >= lo) & (series.times_ms < hi)
            if not sel.any():
                continue
            market = harness.load_market_data(
                harness.normalize_symbol(bare),
                "1h",
                start_ms=lo - 86_400_000,
                end_ms=hi + 86_400_000,
                need_1m=True,
                funding=False,
            )
            opens = pd.Series(
                market.df_1m["open"].to_numpy(dtype=np.float64),
                index=market.df_1m["open_time"].to_numpy(dtype=np.int64),
            )
            times = series.times_ms[sel]
            implied = series.oi_usdt[sel] / series.oi_coin[sel]
            errors: dict[int, float] = {}
            for lag in AUDIT_LAGS_MIN:
                ref = opens.reindex(times + lag * 60_000).to_numpy()
                ok = ~np.isnan(ref)
                if ok.sum() < 100:
                    continue
                errors[lag] = float(np.median(np.abs(implied[ok] / ref[ok] - 1.0)) * 1e4)
            if 0 not in errors:
                continue
            best = min(errors, key=lambda k: errors[k])
            rows.append(
                AuditRow(
                    symbol=bare,
                    day=day,
                    snapshots=int(sel.sum()),
                    best_lag_min=best,
                    err_bp_at_0=errors[0],
                    err_bp_at_best=errors[best],
                    err_bp_at_plus5=errors.get(5, float("nan")),
                )
            )
    return rows


def parse_day_ms(day: str) -> int:
    return int(pd.Timestamp(day, tz="UTC").value // 1_000_000)


# --------------------------------------------------------------------------- #
# 라벨링 — 셋업마다 경로 8장(정렬 2 × 범위 2 × 단위 2) · 제외 사유
# --------------------------------------------------------------------------- #

EXCLUDE_NO_ANCHOR = "no_anchor"
"""0점 직전 5분 안에 자기 종목 스냅샷이 없다(대개 상장/집계 전)."""
EXCLUDE_HOLE_SELF = "hole_self"
EXCLUDE_HOLE_BTC = "hole_btc"
EXCLUDE_REASONS: tuple[str, ...] = (EXCLUDE_NO_ANCHOR, EXCLUDE_HOLE_SELF, EXCLUDE_HOLE_BTC)

PanelKey = tuple[str, str, str]
"""(정렬, 범위, 단위)."""


@dataclass(frozen=True)
class PathMatrices:
    """경로 행렬 — `paths[(align, scope, unit)]`는 (n, 337) float32. 행은 `labels`와 같은 순서."""

    labels: pd.DataFrame
    paths: dict[PanelKey, np.ndarray]


def panel_key(align: str, scope: str, unit: str) -> str:
    return f"{align}__{scope}__{unit}"


def _change_ratio(grid_values: np.ndarray, anchor_col: int) -> np.ndarray:
    anchor = grid_values[:, anchor_col : anchor_col + 1]
    out: np.ndarray = grid_values / anchor - 1.0
    return out


def _align_panels(
    series: MetricsSeries | None,
    market: MetricsSeries,
    times: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[tuple[str, str], np.ndarray]]:
    """한 정렬의 (사유 배열, 기준점 시각, 원값 격자[(범위, 단위)]).

    * 기준점 = 0점 시각 **이하 마지막 스냅샷**(그 순간에 알 수 있는 값). 5분보다 오래됐으면
      0점 자체가 없다 → `no_anchor`.
    * 창의 337점 중 하나라도 없으면 `hole_self` / `hole_btc` — 보간하지 않는다.
    * BTC 범위는 **자기 종목 기준점의 같은 격자 시각**을 BTC 시계열에서 읽는다.
    """
    offsets = np.array(OFFSETS_MS, dtype=np.int64)
    reasons = np.full(len(times), "", dtype=object)
    if series is None or len(series.times_ms) == 0:
        reasons[:] = EXCLUDE_NO_ANCHOR
        anchors = np.full(len(times), -1, dtype=np.int64)
    else:
        idx = np.searchsorted(series.times_ms, times, side="right") - 1
        valid = idx >= 0
        anchors = np.where(valid, series.times_ms[np.maximum(idx, 0)], -1)
        lag = times - anchors
        reasons[(~valid) | (lag >= INTERVAL_MS) | (lag < 0)] = EXCLUDE_NO_ANCHOR
    grid = anchors[:, None] + offsets[None, :]
    values: dict[tuple[str, str], np.ndarray] = {}
    for unit, column in UNITS:
        if series is None or len(series.times_ms) == 0:
            values[(SCOPE_SELF, unit)] = np.full(grid.shape, np.nan)
        else:
            flat = series.values_at(grid.ravel(), column)
            values[(SCOPE_SELF, unit)] = flat.reshape(grid.shape)
        values[(SCOPE_BTC, unit)] = market.values_at(grid.ravel(), column).reshape(grid.shape)
    hole_self = np.zeros(len(times), dtype=bool)
    hole_btc = np.zeros(len(times), dtype=bool)
    for unit, _ in UNITS:
        hole_self |= np.isnan(values[(SCOPE_SELF, unit)]).any(axis=1)
        hole_btc |= np.isnan(values[(SCOPE_BTC, unit)]).any(axis=1)
    reasons[(reasons == "") & hole_self] = EXCLUDE_HOLE_SELF
    reasons[(reasons == "") & hole_btc] = EXCLUDE_HOLE_BTC
    return reasons, anchors, values


def build_paths(
    population: pd.DataFrame,
    series_by: dict[str, MetricsSeries],
    *,
    market_symbol: str = MARKET_SYMBOL,
) -> tuple[PathMatrices, pd.DataFrame]:
    """셋업마다 두 정렬의 변화율 경로를 만든다. 반환 = (포함된 셋업, 제외 인구조사).

    🚨 **두 정렬 모두** 창이 온전한 셋업만 남긴다 — 정렬마다 다른 셋업을 보면 두 판을 나란히
    읽을 수 없다. 제외 인구조사는 정렬마다 사유를 따로 센다(한 셋업이 두 표에 다 나올 수 있다).
    """
    market = series_by[archive_symbol(market_symbol)]
    anchor_col = offset_index(0)
    keep_frames: list[pd.DataFrame] = []
    panels: dict[PanelKey, list[np.ndarray]] = {
        (align, scope, unit): [] for align in ALIGNS for scope in SCOPES for unit, _ in UNITS
    }
    exclusions: list[dict[str, object]] = []
    for symbol, group in population.groupby("symbol", sort=True):
        bare = archive_symbol(str(symbol))
        series = series_by.get(bare)
        per_align: dict[str, tuple[np.ndarray, np.ndarray, dict[tuple[str, str], np.ndarray]]] = {}
        keep = np.ones(len(group), dtype=bool)
        for align in ALIGNS:
            times = group[ANCHOR_COLUMN[align]].to_numpy(dtype=np.int64)
            reasons, anchors, values = _align_panels(series, market, times)
            per_align[align] = (reasons, anchors, values)
            keep &= reasons == ""
            for segment in SEGMENTS:
                seg = (group["segment"] == segment).to_numpy()
                for reason in EXCLUDE_REASONS:
                    exclusions.append(
                        {
                            "align": align,
                            "symbol": bare,
                            "segment": segment,
                            "reason": reason,
                            "n": int((seg & (reasons == reason)).sum()),
                            "n_total": int(seg.sum()),
                        }
                    )
        kept = group[keep].copy()
        for align in ALIGNS:
            _, anchors, values = per_align[align]
            times = group[ANCHOR_COLUMN[align]].to_numpy(dtype=np.int64)
            kept[f"anchor_time_{align}"] = anchors[keep]
            kept[f"anchor_lag_{align}_ms"] = times[keep] - anchors[keep]
            for (scope, unit), matrix in values.items():
                ratio = _change_ratio(matrix[keep], anchor_col).astype(np.float32)
                if ratio.size and not np.isfinite(ratio).all():
                    # 포함된 셋업에 NaN/inf가 남으면 제외 규칙이 샌 것 — 평균에 섞지 않는다.
                    raise AssertionError(f"{align}/{scope}/{unit}: 유한하지 않은 경로 값")
                panels[(align, scope, unit)].append(ratio)
        kept["hold_ms"] = kept["tp_on_exit_time"] - kept["entry_time"]
        keep_frames.append(kept)
    labels = pd.concat(keep_frames, ignore_index=True) if keep_frames else population.iloc[0:0]
    width = len(OFFSETS_MS)
    paths = {
        key: (np.concatenate(parts) if parts else np.zeros((0, width), np.float32))
        for key, parts in panels.items()
    }
    return PathMatrices(labels, paths), pd.DataFrame.from_records(exclusions)


def attach_controls(labels: pd.DataFrame, *, bad_days: Sequence[str]) -> pd.DataFrame:
    """WAN-410 축(`realized_r_before` · `settled`) · KST 진입일/청산일 · 폭락일 표식을 붙인다.

    🚨 관측만 붙인다 — 셋업의 진입·청산·손익 열은 손대지 않는다(검산 (c)가 값으로 확인)."""
    out = labels.copy()
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
# 집계 — 두 집단 경로(평균 · 중앙 · 분위 띠) · 시각별 차 · 표준오차
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PathRow:
    align: str
    segment: str
    control: str
    stratum: str
    scope: str
    unit: str
    offset_ms: int
    n_stop: int
    n_tp: int
    mean_stop: float
    mean_tp: float
    median_stop: float
    median_tp: float
    q25_stop: float
    q75_stop: float
    q25_tp: float
    q75_tp: float
    delta: float
    """깨짐 − 반등(평균 변화율 차)."""
    sigma: float
    z: float


def _stat(values: np.ndarray, func: str) -> np.ndarray:
    if values.shape[0] == 0:
        return np.full(values.shape[1], np.nan)
    out: np.ndarray
    if func == "mean":
        out = values.mean(axis=0)
    elif func == "median":
        out = np.median(values, axis=0)
    elif func == "q25":
        out = np.quantile(values, 0.25, axis=0)
    elif func == "q75":
        out = np.quantile(values, 0.75, axis=0)
    else:
        raise ValueError(func)
    return out


def _stderr_cols(values: np.ndarray) -> np.ndarray:
    n = values.shape[0]
    if n <= 1:
        return np.full(values.shape[1], np.nan)
    out: np.ndarray = values.std(axis=0, ddof=1) / math.sqrt(n)
    return out


def path_rows(
    matrix: np.ndarray,
    is_stop: np.ndarray,
    *,
    align: str,
    segment: str,
    control: str,
    stratum: str,
    scope: str,
    unit: str,
) -> list[PathRow]:
    """한 판(집단 마스크로 나눈 경로 행렬)의 337점 통계."""
    stop = matrix[is_stop].astype(np.float64)
    tp = matrix[~is_stop].astype(np.float64)
    mean_s, mean_t = _stat(stop, "mean"), _stat(tp, "mean")
    med_s, med_t = _stat(stop, "median"), _stat(tp, "median")
    q25_s, q75_s = _stat(stop, "q25"), _stat(stop, "q75")
    q25_t, q75_t = _stat(tp, "q25"), _stat(tp, "q75")
    delta = mean_s - mean_t
    sigma = np.sqrt(_stderr_cols(stop) ** 2 + _stderr_cols(tp) ** 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sigma > 0, np.abs(delta) / sigma, np.inf)
    rows: list[PathRow] = []
    for col, offset in enumerate(OFFSETS_MS):
        rows.append(
            PathRow(
                align=align,
                segment=segment,
                control=control,
                stratum=stratum,
                scope=scope,
                unit=unit,
                offset_ms=int(offset),
                n_stop=int(stop.shape[0]),
                n_tp=int(tp.shape[0]),
                mean_stop=float(mean_s[col]),
                mean_tp=float(mean_t[col]),
                median_stop=float(med_s[col]),
                median_tp=float(med_t[col]),
                q25_stop=float(q25_s[col]),
                q75_stop=float(q75_s[col]),
                q25_tp=float(q25_t[col]),
                q75_tp=float(q75_t[col]),
                delta=float(delta[col]),
                sigma=float(sigma[col]),
                z=float(z[col]),
            )
        )
    return rows


def all_path_rows(matrices: PathMatrices) -> list[PathRow]:
    """정렬 2 × 구간 2 × 통제(없음 · 폭락일 제외 · WAN-410 버킷마다 · 가중 합) × 범위 × 단위."""
    labels = matrices.labels
    is_stop_all = labels["is_stop"].to_numpy(dtype=bool)
    crash = labels["is_crash_day"].to_numpy(dtype=bool)
    buckets = sorted(
        {
            (str(b), int(o))
            for b, o in zip(labels["realized_bucket"], labels["realized_order"], strict=True)
        },
        key=lambda kv: kv[1],
    )
    rows: list[PathRow] = []
    for align in ALIGNS:
        for segment in SEGMENTS:
            seg = (labels["segment"] == segment).to_numpy()
            masks = [
                (CONTROL_NONE, "—", seg),
                (CONTROL_NO_CRASH, f"최악 {WORST_DAYS}일 제외", seg & ~crash),
            ]
            strata = [
                (CONTROL_REALIZED, bucket, seg & (labels["realized_bucket"] == bucket).to_numpy())
                for bucket, _order in buckets
            ]
            for scope in SCOPES:
                for unit, _ in UNITS:
                    matrix = matrices.paths[(align, scope, unit)]
                    common = {"align": align, "segment": segment, "scope": scope, "unit": unit}
                    for control, stratum, mask in masks:
                        rows.extend(
                            path_rows(
                                matrix[mask],
                                is_stop_all[mask],
                                control=control,
                                stratum=stratum,
                                **common,
                            )
                        )
                    stratum_rows: list[list[PathRow]] = []
                    for control, stratum, mask in strata:
                        part = path_rows(
                            matrix[mask],
                            is_stop_all[mask],
                            control=control,
                            stratum=stratum,
                            **common,
                        )
                        rows.extend(part)
                        stratum_rows.append(part)
                    rows.extend(_pooled_rows(stratum_rows, **common))
    return rows


def _pooled_rows(
    strata: Sequence[Sequence[PathRow]], *, align: str, segment: str, scope: str, unit: str
) -> list[PathRow]:
    """WAN-410 버킷 안 차를 **표본 가중**(min(n_stop, n_tp))으로 합친다(wan402 가중 합 자)."""
    usable = [s for s in strata if s and min(s[0].n_stop, s[0].n_tp) >= MIN_GROUP_N]
    if not usable:
        return []
    weights = np.array([float(min(s[0].n_stop, s[0].n_tp)) for s in usable])
    weights /= weights.sum()
    nan = float("nan")
    out: list[PathRow] = []
    for col in range(len(OFFSETS_MS)):
        delta = float(sum(w * s[col].delta for w, s in zip(weights, usable, strict=True)))
        var = float(sum((w**2) * s[col].sigma ** 2 for w, s in zip(weights, usable, strict=True)))
        sigma = math.sqrt(var)
        out.append(
            PathRow(
                align=align,
                segment=segment,
                control=CONTROL_POOLED,
                stratum=f"{len(usable)}버킷",
                scope=scope,
                unit=unit,
                offset_ms=int(OFFSETS_MS[col]),
                n_stop=sum(s[0].n_stop for s in usable),
                n_tp=sum(s[0].n_tp for s in usable),
                mean_stop=nan,
                mean_tp=nan,
                median_stop=nan,
                median_tp=nan,
                q25_stop=nan,
                q75_stop=nan,
                q25_tp=nan,
                q75_tp=nan,
                delta=delta,
                sigma=sigma,
                z=(abs(delta) / sigma if sigma > 0 else math.inf),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 판정 — 코드가 낸다
# --------------------------------------------------------------------------- #

POINT_LEAD = "선행"
POINT_PROXY = "대리변수"
POINT_NONE = "무"
POINT_UNDECIDED = "판정 불가"


@dataclass(frozen=True)
class LeadPoint:
    """판정 점 하나(정렬 · 범위 · 단위 · 오프셋)의 관문 결과."""

    align: str
    scope: str
    unit: str
    offset_ms: int
    n_stop: int
    n_tp: int
    oos_delta: float | None
    oos_z: float | None
    oos_decided: bool
    is_delta: float | None
    is_same_sign: bool
    no_crash_delta: float | None
    no_crash_z: float | None
    no_crash_kept: bool
    pooled_delta: float | None
    pooled_z: float | None
    pooled_kept: bool
    status: str


def _index_rows(rows: Sequence[PathRow]) -> dict[tuple[str, str, str, str, str, int], PathRow]:
    return {(r.align, r.segment, r.control, r.scope, r.unit, r.offset_ms): r for r in rows}


def _same_sign(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and a != 0.0 and b != 0.0 and (a < 0) == (b < 0)


def lead_points(rows: Sequence[PathRow], *, align: str = VERDICT_ALIGN) -> list[LeadPoint]:
    """착수 전에 고른 점마다 관문 넷 — (1) 뒷구간 Bonferroni 2σ · (2) 앞구간 같은 부호 ·
    (3) 폭락일 제외 판에서 같은 부호이고 여전히 결정 · (4) WAN-410 버킷 안 가중 합에서 같은 부호이고
    여전히 결정. 🚨 0 오른쪽 점은 신호 후보가 아니다."""
    z_needed = decision_z()
    index = _index_rows(rows)
    out: list[LeadPoint] = []
    for scope in SCOPES:
        for unit, _ in UNITS:
            for offset in LEAD_OFFSETS_MS:
                if offset > SIGNAL_MAX_OFFSET_MS:
                    raise AssertionError("0 오른쪽 점은 신호 후보가 아니다")
                key = (scope, unit, offset)
                base = index.get((align, PRIMARY_SEGMENT, CONTROL_NONE, *key))
                is_row = index.get((align, SEGMENT_IS, CONTROL_NONE, *key))
                crash = index.get((align, PRIMARY_SEGMENT, CONTROL_NO_CRASH, *key))
                pooled = index.get((align, PRIMARY_SEGMENT, CONTROL_POOLED, *key))
                if base is None or min(base.n_stop, base.n_tp) < MIN_GROUP_N:
                    out.append(
                        LeadPoint(
                            align=align,
                            scope=scope,
                            unit=unit,
                            offset_ms=offset,
                            n_stop=0 if base is None else base.n_stop,
                            n_tp=0 if base is None else base.n_tp,
                            oos_delta=None,
                            oos_z=None,
                            oos_decided=False,
                            is_delta=None,
                            is_same_sign=False,
                            no_crash_delta=None,
                            no_crash_z=None,
                            no_crash_kept=False,
                            pooled_delta=None,
                            pooled_z=None,
                            pooled_kept=False,
                            status=POINT_UNDECIDED,
                        )
                    )
                    continue
                decided = base.z > z_needed
                is_ok = _same_sign(base.delta, None if is_row is None else is_row.delta)
                crash_ok = (
                    crash is not None
                    and min(crash.n_stop, crash.n_tp) >= MIN_GROUP_N
                    and _same_sign(base.delta, crash.delta)
                    and crash.z > z_needed
                )
                pooled_ok = (
                    pooled is not None
                    and _same_sign(base.delta, pooled.delta)
                    and pooled.z > z_needed
                )
                if not decided:
                    status = POINT_NONE
                elif is_ok and crash_ok and pooled_ok:
                    status = POINT_LEAD
                else:
                    status = POINT_PROXY
                out.append(
                    LeadPoint(
                        align=align,
                        scope=scope,
                        unit=unit,
                        offset_ms=offset,
                        n_stop=base.n_stop,
                        n_tp=base.n_tp,
                        oos_delta=base.delta,
                        oos_z=base.z,
                        oos_decided=decided,
                        is_delta=None if is_row is None else is_row.delta,
                        is_same_sign=is_ok,
                        no_crash_delta=None if crash is None else crash.delta,
                        no_crash_z=None if crash is None else crash.z,
                        no_crash_kept=crash_ok,
                        pooled_delta=None if pooled is None else pooled.delta,
                        pooled_z=None if pooled is None else pooled.z,
                        pooled_kept=pooled_ok,
                        status=status,
                    )
                )
    return out


@dataclass(frozen=True)
class PanelVerdict:
    align: str
    scope: str
    unit: str
    points: int
    lead_points: int
    proxy_points: int
    none_points: int
    undecided_points: int
    verdict: str
    reason: str


def panel_verdicts(points: Sequence[LeadPoint]) -> list[PanelVerdict]:
    """정렬 × 범위 × 단위마다 한 줄. 판정 정렬에서만 선행/대리변수/무를 내고, 청산 정렬은 같은
    관문의 결과를 세기만 한 채 **무효**로 찍는다(0점이 결과 뒤라 판정 자격이 없다)."""
    out: list[PanelVerdict] = []
    for align in ALIGNS:
        for scope in SCOPES:
            for unit, _ in UNITS:
                mine = [
                    p for p in points if p.align == align and p.scope == scope and p.unit == unit
                ]
                if not mine:
                    continue
                lead = [p for p in mine if p.status == POINT_LEAD]
                proxy = [p for p in mine if p.status == POINT_PROXY]
                none = [p for p in mine if p.status == POINT_NONE]
                undecided = [p for p in mine if p.status == POINT_UNDECIDED]
                if align != VERDICT_ALIGN:
                    verdict = VERDICT_INVALID
                    reason = (
                        f"같은 관문이면 선행 {len(lead)}점 — 분모가 청산 순간 값이고 "
                        "0 왼쪽이 보유 구간이라 인과가 아니다"
                    )
                elif lead:
                    verdict = VERDICT_LEAD
                    reason = "선행 점 " + ", ".join(offset_label(p.offset_ms) for p in lead)
                elif proxy:
                    verdict = VERDICT_PROXY
                    reason = "통제 전에만 갈림 " + ", ".join(
                        offset_label(p.offset_ms) for p in proxy
                    )
                elif none:
                    verdict = VERDICT_NO
                    reason = (
                        f"판정 점 {len(none)}개 전부 z ≤ {decision_z():.2f}(Bonferroni {TESTS})"
                    )
                else:
                    verdict = VERDICT_UNDECIDED
                    reason = "표본 미달"
                out.append(
                    PanelVerdict(
                        align=align,
                        scope=scope,
                        unit=unit,
                        points=len(mine),
                        lead_points=len(lead),
                        proxy_points=len(proxy),
                        none_points=len(none),
                        undecided_points=len(undecided),
                        verdict=verdict,
                        reason=reason,
                    )
                )
    return out


# --------------------------------------------------------------------------- #
# 분위 표 — 손절률(a) · 거래당 net R(b) 병기 (판정 정렬만 · 진입 전 변화율을 앞구간 5분위로)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FeatureBinRow:
    scope: str
    unit: str
    offset_ms: int
    segment: str
    bin: int
    bins: int
    lo: float
    hi: float
    n: int
    stops: int
    stop_rate: float
    mean_net_r: float
    net_r_stderr: float


def _stderr(values: Sequence[float]) -> float:
    return statistics.stdev(values) / (len(values) ** 0.5) if len(values) > 1 else 0.0


def feature_bin_rows(matrices: PathMatrices) -> list[FeatureBinRow]:
    """진입 정렬 판정 점의 변화율(= 진입 순간에 아는 값)을 앞구간 5분위로 잘라 두 구간에 적용.

    🚨 청산 정렬로는 만들지 않는다 — 결과가 섞인 값으로 자르면 net R과 **정의상** 엮인다."""
    labels = matrices.labels
    is_mask = (labels["segment"] == SEGMENT_IS).to_numpy()
    rows: list[FeatureBinRow] = []
    for scope in SCOPES:
        for unit, _ in UNITS:
            matrix = matrices.paths[(VERDICT_ALIGN, scope, unit)]
            for offset in LEAD_OFFSETS_MS:
                feature = pd.Series(matrix[:, offset_index(offset)].astype(np.float64))
                edges = bin_edges(feature[is_mask], QUANTILES)
                bins = assign_bins(feature, edges)
                total_bins = max(len(edges) - 1, 0)
                for segment in SEGMENTS:
                    seg = (labels["segment"] == segment).to_numpy()
                    for b in range(total_bins):
                        sel = seg & (bins == b).fillna(False).to_numpy(dtype=bool)
                        if not sel.any():
                            continue
                        nets = [float(v) for v in labels.loc[sel, "net_r"].tolist()]
                        stops = int(labels.loc[sel, "is_stop"].sum())
                        rows.append(
                            FeatureBinRow(
                                scope=scope,
                                unit=unit,
                                offset_ms=int(offset),
                                segment=segment,
                                bin=b,
                                bins=total_bins,
                                lo=float(edges[b]),
                                hi=float(edges[b + 1]),
                                n=int(sel.sum()),
                                stops=stops,
                                stop_rate=stops / int(sel.sum()),
                                mean_net_r=float(statistics.fmean(nets)),
                                net_r_stderr=_stderr(nets),
                            )
                        )
    return rows


# --------------------------------------------------------------------------- #
# leave-one-out — 판정 정렬 · 종목 하나씩 빼고 판정 점의 뒷구간 차를 다시 낸다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LooRow:
    scope: str
    unit: str
    offset_ms: int
    dropped: str
    n_stop: int
    n_tp: int
    delta: float | None
    z: float | None
    full_delta: float | None
    sign_kept: bool | None


def loo_rows(matrices: PathMatrices, points: Sequence[LeadPoint]) -> list[LooRow]:
    labels = matrices.labels
    warm = (labels["segment"] == PRIMARY_SEGMENT).to_numpy()
    is_stop = labels["is_stop"].to_numpy(dtype=bool)
    symbols = labels["symbol"].map(archive_symbol).to_numpy()
    full_by = {(p.scope, p.unit, p.offset_ms): p for p in points if p.align == VERDICT_ALIGN}
    out: list[LooRow] = []
    for dropped in sorted(set(symbols)):
        mask = warm & (symbols != dropped)
        for scope in SCOPES:
            for unit, _ in UNITS:
                matrix = matrices.paths[(VERDICT_ALIGN, scope, unit)]
                for offset in LEAD_OFFSETS_MS:
                    col = offset_index(offset)
                    stop = matrix[mask & is_stop, col].astype(np.float64)
                    tp = matrix[mask & ~is_stop, col].astype(np.float64)
                    full = full_by[(scope, unit, offset)]
                    if min(len(stop), len(tp)) < MIN_GROUP_N:
                        out.append(
                            LooRow(
                                scope, unit, offset, str(dropped), len(stop), len(tp),
                                None, None, full.oos_delta, None,
                            )
                        )  # fmt: skip
                        continue
                    delta = float(stop.mean() - tp.mean())
                    sigma = math.sqrt(stop.var(ddof=1) / len(stop) + tp.var(ddof=1) / len(tp))
                    z = abs(delta) / sigma if sigma > 0 else math.inf
                    kept = None if full.oos_delta is None else _same_sign(delta, full.oos_delta)
                    out.append(
                        LooRow(
                            scope, unit, offset, str(dropped), len(stop), len(tp),
                            delta, z, full.oos_delta, kept,
                        )
                    )  # fmt: skip
    return out


# --------------------------------------------------------------------------- #
# 단위 분해 — USDT 명목 = 코인 수량 × 가격 · 그 차가 OI인가 가격인가
# --------------------------------------------------------------------------- #

MEASURE_COIN = "coin"
MEASURE_PRICE = "implied_price"
MEASURE_USDT = "usdt"


@dataclass(frozen=True)
class DecompRow:
    """판정 정렬의 판정 점에서 두 집단 차를 **코인 수량 · 함의 가격 · USDT 명목** 셋으로 나란히.

    함의 가격 변화율 = `(1 + usdt) / (1 + coin) − 1` — 명목을 수량으로 나눈 값이라 거래소가 명목을
    계산한 가격(마크가)의 변화다. 🚨 **판정 관문이 아니다**(결과를 보고 관문을 늘리지 않는다,
    WAN-161) — 판정 줄이 USDT 단위에서 무엇을 잡았는지 **읽는 법**을 주는 표다.
    """

    scope: str
    offset_ms: int
    control: str
    measure: str
    n_stop: int
    n_tp: int
    delta: float
    z: float


def implied_price_change(coin: np.ndarray, usdt: np.ndarray) -> np.ndarray:
    out: np.ndarray = (1.0 + usdt.astype(np.float64)) / (1.0 + coin.astype(np.float64)) - 1.0
    return out


def decomposition_rows(matrices: PathMatrices) -> list[DecompRow]:
    labels = matrices.labels
    warm = (labels["segment"] == PRIMARY_SEGMENT).to_numpy()
    crash = labels["is_crash_day"].to_numpy(dtype=bool)
    is_stop = labels["is_stop"].to_numpy(dtype=bool)
    rows: list[DecompRow] = []
    for scope in SCOPES:
        coin = matrices.paths[(VERDICT_ALIGN, scope, "coin")].astype(np.float64)
        usdt = matrices.paths[(VERDICT_ALIGN, scope, "usdt")].astype(np.float64)
        measures = {
            MEASURE_COIN: coin,
            MEASURE_PRICE: implied_price_change(coin, usdt),
            MEASURE_USDT: usdt,
        }
        for offset in LEAD_OFFSETS_MS:
            col = offset_index(offset)
            for control, mask in ((CONTROL_NONE, warm), (CONTROL_NO_CRASH, warm & ~crash)):
                for measure, matrix in measures.items():
                    stop = matrix[mask & is_stop, col]
                    tp = matrix[mask & ~is_stop, col]
                    if min(len(stop), len(tp)) < 2:
                        continue
                    delta = float(stop.mean() - tp.mean())
                    sigma = math.sqrt(stop.var(ddof=1) / len(stop) + tp.var(ddof=1) / len(tp))
                    rows.append(
                        DecompRow(
                            scope=scope,
                            offset_ms=int(offset),
                            control=control,
                            measure=measure,
                            n_stop=len(stop),
                            n_tp=len(tp),
                            delta=delta,
                            z=(abs(delta) / sigma if sigma > 0 else math.inf),
                        )
                    )
    return rows


# --------------------------------------------------------------------------- #
# 보유 시간 — 청산 정렬이 왜 판정 자격이 없는지의 실측
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HoldRow:
    segment: str
    timeframe: str
    group: str
    n: int
    p25_min: float
    median_min: float
    p75_min: float
    share_under_2h: float
    share_under_24h: float


def holding_rows(labels: pd.DataFrame) -> list[HoldRow]:
    rows: list[HoldRow] = []
    for segment in SEGMENTS:
        seg = labels[labels["segment"] == segment]
        for timeframe in ["ALL", *sorted(seg["timeframe"].unique(), key=_tf_order)]:
            sub = seg if timeframe == "ALL" else seg[seg["timeframe"] == timeframe]
            for group, part in (
                ("all", sub),
                ("stop", sub[sub["is_stop"]]),
                ("tp", sub[~sub["is_stop"]]),
            ):
                if part.empty:
                    continue
                minutes = part["hold_ms"].astype(np.float64) / 60_000
                rows.append(
                    HoldRow(
                        segment=segment,
                        timeframe=str(timeframe),
                        group=group,
                        n=len(part),
                        p25_min=float(minutes.quantile(0.25)),
                        median_min=float(minutes.median()),
                        p75_min=float(minutes.quantile(0.75)),
                        share_under_2h=float((minutes < 120).mean()),
                        share_under_24h=float((minutes < 1440).mean()),
                    )
                )
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


_SETUP_COLUMNS = (
    "symbol",
    "timeframe",
    "segment",
    "trigger_time",
    "entry_time",
    "entry_price",
    "stop_price",
    "tp_on_exit_time",
    "tp_on_reason",
    "stop_width",
    "net_r",
    "is_stop",
)


def checksum_rows(
    population: pd.DataFrame,
    matrices: PathMatrices,
    census: Sequence[CensusRow],
) -> list[ChecksumRow]:
    """검산.

    (a) 모집단 ≡ WAN-375 표(`wan402`와 같은 자) · (b) net R 평균 ≡ WAN-375 `_ev_at` ·
    (c) 라벨링이 셋업 열을 안 바꿨다(값으로) · (d) 0점 변화율이 정확히 0 ·
    (e) 기준점 지연 < 간격(두 정렬) · (f) 파일 안 값 충돌 0 · (g) BTC 셋업의 자기 범위 ≡ BTC 범위 ·
    (h) 변화율 ≤ −100% 점 0(OI=0 게시 결함이 안 샜다) ·
    (i) 판정 정렬의 마지막 판정 점 데이터 시각 ≤ 체결 시각(인과 · 구조적으로 확인).
    """
    rows: list[ChecksumRow] = []
    warm = population[population["segment"] == SEGMENT_OOS_WARM]
    raw_warm, raw_end = population_census(segment=SEGMENT_OOS_WARM)
    rows.append(
        ChecksumRow(
            "(a-1) WAN-375 첫 탭·가드 통과 oos_warm(데이터 끝 포함) ≡ 이슈가 인용한 10,274",
            float(raw_warm),
            float(EXPECTED_OOS_WARM_SETUPS),
            float(raw_warm - EXPECTED_OOS_WARM_SETUPS),
        )
    )
    rows.append(
        ChecksumRow(
            "(a-2) 모집단 ≡ 그 집합 − 데이터 끝(익절 켠 판이 미결)",
            float(len(warm)),
            float(raw_warm - raw_end),
            float(len(warm) - (raw_warm - raw_end)),
        )
    )
    target_r = harness.build_params().take_profit_r
    for tf in sorted(warm["timeframe"].unique(), key=_tf_order):
        sub = warm[warm["timeframe"] == tf]
        ref = _ev_at(sub, target_r, CostRates.from_config(harness.build_config(str(tf))))
        mine = float(sub["net_r"].mean())
        ref_v = float(ref if ref is not None else float("nan"))
        rows.append(
            ChecksumRow(f"(b) net R 평균 ≡ WAN-375 _ev_at · {tf}", mine, ref_v, mine - ref_v)
        )
    labels = matrices.labels
    raw_columns = [c for c in _SETUP_COLUMNS if c != "net_r"]
    merged = labels[list(_SETUP_COLUMNS)].merge(
        population[list(_SETUP_COLUMNS)].drop_duplicates(subset=raw_columns),
        on=raw_columns,
        how="left",
        suffixes=("", "_population"),
        indicator=True,
    )
    unmatched = int((merged["_merge"] != "both").sum())
    rows.append(
        ChecksumRow("(c-1) 라벨링 후 셋업 원열 불일치 행", float(unmatched), 0.0, float(unmatched))
    )
    # net R은 계산 열이라 CSV 텍스트 왕복 끝자리(≈2e-16)만 허용한다(WAN-395와 같은 자).
    net_diff = float((merged["net_r"] - merged["net_r_population"]).abs().max())
    rows.append(
        ChecksumRow(
            "(c-2) net R 최대 절대차(CSV 왕복 끝자리 1e-12까지 허용)",
            net_diff,
            0.0,
            0.0 if net_diff <= 1e-12 else net_diff,
        )
    )
    anchor_col = offset_index(0)
    nonzero = sum(int((m[:, anchor_col] != 0.0).sum()) for m in matrices.paths.values())
    rows.append(
        ChecksumRow("(d) 0점 변화율 ≠ 0인 (셋업 × 판) 수", float(nonzero), 0.0, float(nonzero))
    )
    for align in ALIGNS:
        column = f"anchor_lag_{align}_ms"
        max_lag = float(labels[column].max()) if len(labels) else 0.0
        min_lag = float(labels[column].min()) if len(labels) else 0.0
        violation = max(0.0, max_lag - (INTERVAL_MS - 1)) + max(0.0, -min_lag)
        rows.append(
            ChecksumRow(
                f"(e) 기준점 지연 최댓값(ms) · {align} — 간격 미만 · 음수 없음",
                max_lag,
                float(INTERVAL_MS),
                violation,
            )
        )
    conflicts = float(sum(c.conflicting_rows for c in census))
    rows.append(
        ChecksumRow("(f) 같은 시각인데 값이 다른 중복 행(전 종목)", conflicts, 0.0, conflicts)
    )
    btc = (labels["symbol"].map(archive_symbol) == archive_symbol(MARKET_SYMBOL)).to_numpy()
    mismatch = 0
    for align in ALIGNS:
        for unit, _ in UNITS:
            a = matrices.paths[(align, SCOPE_SELF, unit)][btc]
            b = matrices.paths[(align, SCOPE_BTC, unit)][btc]
            mismatch += int((a != b).sum())
    rows.append(
        ChecksumRow(
            "(g) BTC 셋업 자기 범위 ≠ BTC 범위인 (셋업 × 점) 수",
            float(mismatch),
            0.0,
            float(mismatch),
        )
    )
    minus_one = sum(int((m <= -1.0).sum()) for m in matrices.paths.values())
    rows.append(
        ChecksumRow(
            "(h) 변화율 ≤ −100%인 (셋업 × 판 × 점) 수", float(minus_one), 0.0, float(minus_one)
        )
    )
    last_lead = max(LEAD_OFFSETS_MS)
    data_time = labels[f"anchor_time_{VERDICT_ALIGN}"] + last_lead
    late = int((data_time > labels["entry_time"]).sum())
    rows.append(
        ChecksumRow(
            "(i) 판정 점 데이터 시각 > 체결 시각인 셋업 수(인과 위반)",
            float(late),
            0.0,
            float(late),
        )
    )
    # (j) 시각 검산 — (i)는 **파일 라벨이 맞다는 전제** 위의 값이라, 라벨 자체를 1분봉으로 확인한다.
    audit = pd.read_csv(AUDIT_CSV) if AUDIT_CSV.exists() else pd.DataFrame()
    audited = len(audit)
    off = int((audit["best_lag_min"] != 0).sum()) if audited else 0
    rows.append(
        ChecksumRow(
            f"(j) 시각 검산 — 보정 뒤 최적 시차 ≠ 0분인 (종목, 날) · 대조 {audited}칸",
            float(off),
            0.0,
            float(off) if audited else float("nan"),
        )
    )
    return rows


def _tf_order(tf: str) -> int:
    return {"15m": 0, "1h": 1, "2h": 2, "4h": 3}.get(tf, 9)


# --------------------------------------------------------------------------- #
# 그림 — SVG(텍스트라 커밋 가능)
# --------------------------------------------------------------------------- #


def render_figure(paths: pd.DataFrame, out: Path = FIGURE_SVG) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["svg.hashsalt"] = "wan417"
    panels = [(align, scope) for align in ALIGNS for scope in SCOPES]
    fig, axes = plt.subplots(len(panels), len(UNITS), figsize=(12, 13), sharex=True)
    hours = np.array(OFFSETS_MS) / 3_600_000
    for i, (align, scope) in enumerate(panels):
        for j, (unit, _) in enumerate(UNITS):
            ax = axes[i][j]
            for control, style in ((CONTROL_NONE, "-"), (CONTROL_NO_CRASH, "--")):
                sub = paths[
                    (paths["align"] == align)
                    & (paths["segment"] == PRIMARY_SEGMENT)
                    & (paths["control"] == control)
                    & (paths["scope"] == scope)
                    & (paths["unit"] == unit)
                ].sort_values("offset_ms")
                if sub.empty:
                    continue
                tag = "" if control == CONTROL_NONE else " (crash days excluded)"
                ax.plot(hours, sub["mean_stop"] * 100, style, color="#c0392b", label=f"stop{tag}")
                ax.plot(hours, sub["mean_tp"] * 100, style, color="#2471a3", label=f"tp{tag}")
                if control == CONTROL_NONE:
                    ax.fill_between(
                        hours, sub["q25_stop"] * 100, sub["q75_stop"] * 100,
                        color="#c0392b", alpha=0.12, linewidth=0,
                    )  # fmt: skip
                    ax.fill_between(
                        hours, sub["q25_tp"] * 100, sub["q75_tp"] * 100,
                        color="#2471a3", alpha=0.12, linewidth=0,
                    )  # fmt: skip
            ax.axvline(0, color="black", linewidth=0.8)
            ax.axhline(0, color="gray", linewidth=0.5)
            note = "VERDICT" if align == VERDICT_ALIGN else "descriptive only (outcome-anchored)"
            ax.set_title(f"0 = {align} · {scope} OI · {unit} · oos_warm · {note}", fontsize=9)
            ax.set_ylabel("OI change vs 0 (%)")
            if i == len(panels) - 1:
                ax.set_xlabel("hours from 0")
            ax.legend(fontsize=7)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, format="svg", metadata={"Date": None})
    plt.close(fig)


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _f(value: float | None, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:+.{digits}f}"


def _pct(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value * 100:+.{digits}f}%"


def _mark(flag: object) -> str:
    if flag is None or (isinstance(flag, float) and math.isnan(flag)):
        return "—"
    return "✅" if bool(flag) else "❌"


def _points_table(points: pd.DataFrame, align: str) -> list[str]:
    lines = [
        "| 범위 | 단위 | 점 | n(깨짐/반등) | Δ | z | 앞구간 Δ | 같은 부호 | "
        "폭락일 제외 Δ (z) | 유지 | WAN-410 버킷 안 Δ (z) | 유지 | 상태 |",
        "| -- | -- | -- | -- | --: | --: | --: | -- | --: | -- | --: | -- | -- |",
    ]
    for rec in points[points["align"] == align].to_dict("records"):
        lines.append(
            f"| {rec['scope']} | {rec['unit']} | {offset_label(int(rec['offset_ms']))} | "
            f"{rec['n_stop']:,}/{rec['n_tp']:,} | {_pct(rec['oos_delta'])} | "
            f"{_f(rec['oos_z'], 2)} | {_pct(rec['is_delta'])} | {_mark(rec['is_same_sign'])} | "
            f"{_pct(rec['no_crash_delta'])} ({_f(rec['no_crash_z'], 2)}) | "
            f"{_mark(rec['no_crash_kept'])} | "
            f"{_pct(rec['pooled_delta'])} ({_f(rec['pooled_z'], 2)}) | "
            f"{_mark(rec['pooled_kept'])} | **{rec['status']}** |"
        )
    return lines


def _path_tables(paths: pd.DataFrame, align: str) -> list[str]:
    lines: list[str] = []
    show = [*LEAD_OFFSETS_MS, 0, 30 * 60_000, 60 * 60_000, 4 * 3_600_000]
    base_all = paths[(paths["align"] == align) & (paths["segment"] == PRIMARY_SEGMENT)]
    for scope in SCOPES:
        for unit, _ in UNITS:
            lines.append(f"#### 0 = {align} · {scope} · {unit}")
            lines.append("")
            lines.append(
                "| 점 | 깨짐 평균 | 반등 평균 | Δ | z | 깨짐 중앙 | 반등 중앙 | 폭락일 제외 Δ | z |"
            )
            lines.append("| -- | --: | --: | --: | --: | --: | --: | --: | --: |")
            sub = base_all[(base_all["scope"] == scope) & (base_all["unit"] == unit)]
            for offset in show:
                base = sub[(sub["control"] == CONTROL_NONE) & (sub["offset_ms"] == offset)]
                crash = sub[(sub["control"] == CONTROL_NO_CRASH) & (sub["offset_ms"] == offset)]
                if base.empty:
                    continue
                b = base.iloc[0]
                c = crash.iloc[0] if not crash.empty else None
                tag = "" if offset <= SIGNAL_MAX_OFFSET_MS else " ⚠️관측용"
                lines.append(
                    f"| {offset_label(int(offset))}{tag} | {_pct(b['mean_stop'])} | "
                    f"{_pct(b['mean_tp'])} | {_pct(b['delta'])} | {_f(b['z'], 2)} | "
                    f"{_pct(b['median_stop'])} | {_pct(b['median_tp'])} | "
                    f"{_pct(c['delta']) if c is not None else '—'} | "
                    f"{_f(c['z'], 2) if c is not None else '—'} |"
                )
            lines.append("")
    return lines


def render_summary(
    *,
    census: pd.DataFrame,
    exclusions: pd.DataFrame,
    holding: pd.DataFrame,
    decomposition: pd.DataFrame,
    paths: pd.DataFrame,
    points: pd.DataFrame,
    verdicts: pd.DataFrame,
    bins: pd.DataFrame,
    loo: pd.DataFrame,
    checksum: pd.DataFrame,
    population_n: dict[str, int],
    included_n: dict[str, int],
    cost_note: str,
) -> str:
    lines: list[str] = []
    lines.append("# WAN-417 §G-0 — 존이 깨질 때와 반등할 때의 미결제약정(OI) · 사건 정렬 관측")
    lines.append("")
    lines.append(
        "🚨 **이 표는 채택 좌표가 아니다** — `band` 팔 · **첫 탭만** · 재진입 없음 · "
        "손절폭 가드 통과 · 셋업 층(WAN-402 §0와 같은 모집단). "
        "여기 수치를 채택 북 성적과 나란히 놓지 말 것. **필터를 만들지 않았다**(WAN-341/323)."
    )
    lines.append("")
    lines.append(
        f"모집단 `is` {population_n.get(SEGMENT_IS, 0):,} · "
        f"`oos_warm` {population_n.get(SEGMENT_OOS_WARM, 0):,} "
        f"→ 두 정렬 모두 OI 창이 온전한 셋업 `is` {included_n.get(SEGMENT_IS, 0):,} · "
        f"`oos_warm` {included_n.get(SEGMENT_OOS_WARM, 0):,}. 창 −24h~+4h · 5분 · "
        f"0점 대비 변화율 · 판정 점 {', '.join(offset_label(o) for o in LEAD_OFFSETS_MS)} · "
        f"2σ를 Bonferroni {TESTS}로 보정한 z = {decision_z():.2f}. {cost_note}"
    )
    lines.append("")
    lines.append("## 🚨 정렬이 둘이다 — 판정은 `entry`(진입 정렬)만")
    lines.append("")
    lines.append(
        "착수 지시(0 = 청산)대로 판정하면 네 판이 전부 「선행」으로 찍힌다. 그런데 **분모가 청산 "
        "순간 값**(손절이면 가격이 막 1R 내려온 순간)이고 **청산의 0 왼쪽은 보유 구간**이다"
        "(아래 보유 시간 표). 그래서 청산 정렬은 **사건 그림으로만** 싣고, 판정은 0 = 체결 시각인 "
        "진입 정렬에서 **같은 창·점·관문·통제**로 낸다."
    )
    lines.append("")
    lines.append("## 판정 — 코드가 낸다 (`lead_points` · `panel_verdicts`)")
    lines.append("")
    lines.append("| 정렬 | 범위 | 단위 | 판정 점 | 선행 | 대리변수 | 무 | 불가 | 판정 | 이유 |")
    lines.append("| -- | -- | -- | --: | --: | --: | --: | --: | -- | -- |")
    for rec in verdicts.to_dict("records"):
        lines.append(
            f"| {rec['align']} | {rec['scope']} | {rec['unit']} | {rec['points']} | "
            f"{rec['lead_points']} | {rec['proxy_points']} | {rec['none_points']} | "
            f"{rec['undecided_points']} | **{rec['verdict']}** | {rec['reason']} |"
        )
    lines.append("")
    lines.append("### 판정 점마다 — 0 = 진입(판정) · Δ = 깨짐 − 반등(평균 변화율 · `oos_warm`)")
    lines.append("")
    lines.extend(_points_table(points, VERDICT_ALIGN))
    lines.append("")
    lines.append(
        "### 단위 분해 — USDT 명목의 차는 미결제약정인가 가격인가 (`oos_warm` · 진입 정렬)"
    )
    lines.append("")
    lines.append(
        "USDT 명목 = 코인 수량 × 가격이라 명목 단위의 차는 둘로 쪼개진다. 함의 가격 = "
        "`(1+usdt)/(1+coin) − 1`. ⚠️ **판정 관문이 아니다** — 판정 줄이 명목 단위에서 무엇을 "
        "잡았는지 읽는 표다(관문은 착수 전에 못 박은 그대로다)."
    )
    lines.append("")
    lines.append("| 범위 | 점 | 통제 | 코인 수량 Δ (z) | 함의 가격 Δ (z) | USDT 명목 Δ (z) |")
    lines.append("| -- | -- | -- | --: | --: | --: |")
    if not decomposition.empty:
        keyed = {
            (r["scope"], int(r["offset_ms"]), r["control"], r["measure"]): r
            for r in decomposition.to_dict("records")
        }
        for scope in SCOPES:
            for offset in LEAD_OFFSETS_MS:
                for control in (CONTROL_NONE, CONTROL_NO_CRASH):
                    cells = []
                    for measure in (MEASURE_COIN, MEASURE_PRICE, MEASURE_USDT):
                        rec = keyed.get((scope, offset, control, measure))
                        cells.append(
                            "—" if rec is None else f"{_pct(rec['delta'])} ({rec['z']:.2f})"
                        )
                    lines.append(
                        f"| {scope} | {offset_label(offset)} | {control} | "
                        + " | ".join(cells)
                        + " |"
                    )
    lines.append("")
    lines.append("### 같은 관문을 청산 정렬에 돌리면 (⚠️ 무효 — 함정을 보이게 두는 표)")
    lines.append("")
    lines.extend(_points_table(points, ALIGN_EXIT))
    lines.append("")
    lines.append("## 보유 시간 — 청산 정렬의 0 왼쪽은 대부분 진입 뒤다")
    lines.append("")
    lines.append("| 구간 | TF | 집단 | n | p25(분) | 중앙(분) | p75(분) | 2시간 안 | 24시간 안 |")
    lines.append("| -- | -- | -- | --: | --: | --: | --: | --: | --: |")
    for rec in holding.to_dict("records"):
        lines.append(
            f"| {rec['segment']} | {rec['timeframe']} | {rec['group']} | {rec['n']:,} | "
            f"{rec['p25_min']:.0f} | {rec['median_min']:.0f} | {rec['p75_min']:.0f} | "
            f"{rec['share_under_2h'] * 100:.1f}% | {rec['share_under_24h'] * 100:.1f}% |"
        )
    lines.append("")
    lines.append("## 분위 표 — 진입 정렬 판정 점 · 손절률(a) · 거래당 net R(b) (`oos_warm`)")
    lines.append("")
    lines.append(
        "진입 전 변화율(진입 순간에 아는 값)을 앞구간 5분위로 잘라 뒷구간에 적용한 표의 "
        "**최고−최저 분위**만 싣는다(전체는 CSV). ⚠️ per-cell 셋업 층 · **필터가 아니다**."
    )
    lines.append("")
    lines.append(
        "| 범위 | 단위 | 점 | 분위 | n(최저/최고) | 손절률(최저→최고) | "
        "net R(최저→최고) | Δ net R | z |"
    )
    lines.append("| -- | -- | -- | --: | -- | -- | -- | --: | --: |")
    for scope in SCOPES:
        for unit, _ in UNITS:
            for offset in LEAD_OFFSETS_MS:
                sub = bins[
                    (bins["scope"] == scope)
                    & (bins["unit"] == unit)
                    & (bins["offset_ms"] == offset)
                    & (bins["segment"] == PRIMARY_SEGMENT)
                ].sort_values("bin")
                if sub.empty:
                    continue
                lo, hi = sub.iloc[0], sub.iloc[-1]
                delta = float(hi["mean_net_r"] - lo["mean_net_r"])
                sigma = math.sqrt(float(hi["net_r_stderr"]) ** 2 + float(lo["net_r_stderr"]) ** 2)
                z = abs(delta) / sigma if sigma > 0 else math.inf
                lines.append(
                    f"| {scope} | {unit} | {offset_label(int(offset))} | {int(hi['bins'])} | "
                    f"{int(lo['n']):,}/{int(hi['n']):,} | {float(lo['stop_rate']) * 100:.1f}%→"
                    f"{float(hi['stop_rate']) * 100:.1f}% | {_f(float(lo['mean_net_r']))}→"
                    f"{_f(float(hi['mean_net_r']))} | {_f(delta)} | {z:.2f} |"
                )
    lines.append("")
    lines.append("## leave-one-out — 진입 정렬 · 종목 하나씩 (`oos_warm` · 부호 유지 / 판정 가능)")
    lines.append("")
    lines.append("| 범위 | 단위 | 점 | 부호 유지 | 판정 가능 | Δ 최소 | Δ 최대 |")
    lines.append("| -- | -- | -- | --: | --: | --: | --: |")
    if not loo.empty:
        for (scope, unit, offset), sub in loo.groupby(["scope", "unit", "offset_ms"], sort=False):
            usable = sub[sub["delta"].notna()]
            kept = int(usable["sign_kept"].fillna(False).astype(bool).sum())
            lo_v = float(usable["delta"].min()) if len(usable) else None
            hi_v = float(usable["delta"].max()) if len(usable) else None
            lines.append(
                f"| {scope} | {unit} | {offset_label(int(offset))} | {kept} | {len(usable)} | "
                f"{_pct(lo_v)} | {_pct(hi_v)} |"
            )
    lines.append("")
    lines.append("## 두 집단 경로 (`oos_warm` · 평균 변화율 · 통제 없음 / 폭락일 제외)")
    lines.append("")
    lines.append(f"그림: `{FIGURE_SVG.name}`(평균 + 사분위 띠). 아래는 판정 점과 0 오른쪽 몇 점만.")
    lines.append("")
    lines.append("### 0 = 진입(판정)")
    lines.append("")
    lines.extend(_path_tables(paths, VERDICT_ALIGN))
    lines.append("### 0 = 청산(관측용 · 사용자가 보고 싶었던 사건 그림)")
    lines.append("")
    lines.extend(_path_tables(paths, ALIGN_EXIT))
    lines.append("## 0단계 — 데이터 규약(값으로)")
    lines.append("")
    lines.append(
        "| 종목 | 첫 날짜 | 창 안 있음/필요 | 창 안 없음(첫 날짜 전) | 스냅샷 | 최소 간격(ms) | "
        "정규 간격 | 긴 간격 | 중복행 | 값충돌 | 격자 밖 | 날 넘김 | OI=0 | 짧은 파일 | "
        "빠진 스냅샷 |"
    )
    lines.append(
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |"
    )
    for rec in census.to_dict("records"):
        lines.append(
            f"| {rec['symbol']} | {rec['first_listed_day']} | "
            f"{rec['listed_in_window']}/{rec['wanted_days']} | "
            f"{rec['missing_in_window']}({rec['missing_before_first']}) | {rec['snapshots']:,} | "
            f"{rec['min_gap_ms']} | {rec['gaps_regular']:,} | {rec['gaps_longer']:,} | "
            f"{rec['duplicate_rows']:,} | {rec['conflicting_rows']} | {rec['offgrid_rows']} | "
            f"{rec['spill_rows']} | {rec['zero_rows']:,} | {rec['files_short']} | "
            f"{rec['missing_snapshots_in_files']:,} |"
        )
    lines.append("")
    lines.append("### 🚨 시각 검산 — `create_time`의 뜻이 2024-03-04에 바뀐다")
    lines.append("")
    lines.append(
        "파일의 함의 가격(명목 ÷ 수량)을 저장 1분봉 시가와 시차별로 맞췄다. "
        "라벨 그대로면 2024-03-03까지는 0분, 2024-03-04부터는 **+5분**에서만 맞는다"
        "(값을 구간 끝에 쟀다). 파서가 그날부터 +5분을 더해 **값을 잰 시각**으로 되돌렸고, "
        "아래는 **보정 뒤** 표다 — 전부 0분이어야 한다(검산 (j))."
    )
    lines.append("")
    lines.append("| 종목 | 날 | 스냅샷 | 가장 잘 맞는 시차(분) | 오차 bp @0 | @+5분 |")
    lines.append("| -- | -- | --: | --: | --: | --: |")
    if AUDIT_CSV.exists():
        for rec in pd.read_csv(AUDIT_CSV).to_dict("records"):
            lines.append(
                f"| {rec['symbol']} | {rec['day']} | {int(rec['snapshots'])} | "
                f"{int(rec['best_lag_min']):+d} | {rec['err_bp_at_0']:.2f} | "
                f"{rec['err_bp_at_plus5']:.2f} |"
            )
    lines.append("")
    lines.append("### 제외 인구조사 (정렬마다 · 보간 없음)")
    lines.append("")
    if not exclusions.empty:
        pivot = exclusions.pivot_table(
            index=["align", "segment"], columns="reason", values="n", aggfunc="sum", fill_value=0
        )
        totals = exclusions.groupby(["align", "segment", "symbol"])["n_total"].first()
        lines.append("| 정렬 | 구간 | 셋업 | no_anchor | hole_self | hole_btc |")
        lines.append("| -- | -- | --: | --: | --: | --: |")
        for (align, segment), rec in pivot.iterrows():
            total = int(totals.loc[(align, segment)].sum())
            lines.append(
                f"| {align} | {segment} | {total:,} | {int(rec.get(EXCLUDE_NO_ANCHOR, 0)):,} | "
                f"{int(rec.get(EXCLUDE_HOLE_SELF, 0)):,} | {int(rec.get(EXCLUDE_HOLE_BTC, 0)):,} |"
            )
        lines.append("")
    lines.append("## 검산")
    lines.append("")
    lines.append("| 검산 | 값 | 기준 | 차이 |")
    lines.append("| -- | --: | --: | --: |")
    for rec in checksum.to_dict("records"):
        lines.append(
            f"| {rec['name']} | {rec['value']:.6f} | {rec['reference']:.6f} | {rec['diff']:.2e} |"
        )
    lines.append("")
    lines.append(
        "측정 전용 · 엔진·기본값·토대 불변(`ConfluenceParams()`·`OrderBlockParams()`·"
        "`LeverageBookParams()`) · 핀 없음(WAN-305) · DB 불변(받은 zip은 `data/cache/`) · "
        "전부 `baseline` 위 값 · **필터를 만들지 않았다** · "
        "**WAN-408/410과 더하지 말 것**(WAN-409) · 「엣지 없음」 계열 불변 · 실거래 보류 유지."
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--part",
        choices=("census", "paths", "summary", "all"),
        default="all",
        help="census=목록·받기·규약 · paths=라벨링 · summary=요약만(캐시에서) · all=전부",
    )
    parser.add_argument("--jobs", type=int, default=8, help="받기 스레드(성능 노브)")
    parser.add_argument("--from-csv", action="store_true", help="`--part summary`와 같다")
    parser.add_argument("--no-loo", action="store_true", help="leave-one-out 생략")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    return parser.parse_args(argv)


def _save_matrices(matrices: PathMatrices) -> None:
    LABELS_CSV.parent.mkdir(parents=True, exist_ok=True)
    matrices.labels.to_csv(LABELS_CSV, index=False)
    arrays: dict[str, np.ndarray] = {
        panel_key(*key): matrix for key, matrix in matrices.paths.items()
    }
    np.savez_compressed(PATHS_NPZ, **arrays)  # type: ignore[arg-type]


def _load_matrices() -> PathMatrices:
    if not LABELS_CSV.exists() or not PATHS_NPZ.exists():
        raise FileNotFoundError(
            f"원자료가 없습니다: {LABELS_CSV} / {PATHS_NPZ} — `--part paths` 먼저"
        )
    labels = pd.read_csv(LABELS_CSV)
    with np.load(PATHS_NPZ) as data:
        paths = {
            (align, scope, unit): data[panel_key(align, scope, unit)]
            for align in ALIGNS
            for scope in SCOPES
            for unit, _ in UNITS
        }
    return PathMatrices(labels, paths)


def _run_census(
    symbols: Sequence[str], *, cache_dir: Path, jobs: int
) -> tuple[list[CensusRow], dict[str, MetricsSeries]]:
    listed = ensure_archive(symbols, cache_dir=cache_dir, jobs=jobs)
    series_by = load_all_series(symbols, cache_dir=cache_dir)
    census = census_rows(symbols, listed, series_by)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame.from_records([asdict(r) for r in census]).to_csv(CENSUS_CSV, index=False)
    audit = timestamp_audit_rows(symbols, series_by)
    _records(audit).to_csv(AUDIT_CSV, index=False)
    return census, series_by


def _records(items: Sequence[Any]) -> pd.DataFrame:
    return pd.DataFrame.from_records([asdict(item) for item in items])


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    part = "summary" if args.from_csv else args.part
    started = time.monotonic()
    symbols = [archive_symbol(s) for s in harness.DEFAULT_SYMBOLS]
    population = load_population()
    population_n = {seg: int((population["segment"] == seg).sum()) for seg in SEGMENTS}
    cost_note = ""
    if part in ("census", "paths", "all"):
        census_started = time.monotonic()
        census, series_by = _run_census(symbols, cache_dir=args.cache_dir, jobs=args.jobs)
        cost_note += f"0단계(목록·받기·읽기) {time.monotonic() - census_started:.0f}s. "
        if part == "census":
            print(_records(census).to_string())
            return 0
        label_started = time.monotonic()
        matrices, exclusions = build_paths(population, series_by)
        matrices = PathMatrices(
            attach_controls(matrices.labels, bad_days=worst_days(count=WORST_DAYS)),
            matrices.paths,
        )
        _save_matrices(matrices)
        exclusions.to_csv(EXCLUSION_CSV, index=False)
        cost_note += (
            f"라벨링 {time.monotonic() - label_started:.0f}s({len(matrices.labels):,} 셋업). "
        )
        if part == "paths":
            print(exclusions.groupby(["align", "segment", "reason"])["n"].sum())
            return 0
    else:
        if not CENSUS_CSV.exists():
            print(f"[wan417] 0단계 표가 없습니다: {CENSUS_CSV}", file=sys.stderr)
            return 2
        matrices = _load_matrices()
        exclusions = pd.read_csv(EXCLUSION_CSV) if EXCLUSION_CSV.exists() else pd.DataFrame()
        cost_note = "요약만 재생성(`--part summary`). "
    census_frame = pd.read_csv(CENSUS_CSV)
    census_list = [
        CensusRow(**{k: (v.item() if isinstance(v, np.generic) else v) for k, v in rec.items()})
        for rec in census_frame.to_dict("records")
    ]
    agg_started = time.monotonic()
    rows = all_path_rows(matrices)
    points = [*lead_points(rows, align=ALIGN_ENTRY), *lead_points(rows, align=ALIGN_EXIT)]
    verdicts = panel_verdicts(points)
    bins = feature_bin_rows(matrices)
    loo = [] if args.no_loo else loo_rows(matrices, points)
    holding = holding_rows(matrices.labels)
    decomposition = decomposition_rows(matrices)
    checks = checksum_rows(population, matrices, census_list)
    elapsed = time.monotonic() - agg_started
    cost_note += f"집계 {elapsed:.0f}s · 전체 {time.monotonic() - started:.0f}s."
    frames = {
        "paths": _records(rows),
        "points": _records(points),
        "verdicts": _records(verdicts),
        "bins": _records(bins),
        "loo": _records(loo),
        "holding": _records(holding),
        "decomposition": _records(decomposition),
        "checksum": _records(checks),
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for key, path in (
        ("paths", PATHS_CSV),
        ("points", LEAD_CSV),
        ("verdicts", VERDICT_CSV),
        ("bins", BINS_CSV),
        ("loo", LOO_CSV),
        ("holding", HOLD_CSV),
        ("decomposition", DECOMP_CSV),
        ("checksum", CHECKSUM_CSV),
    ):
        if key == "loo" and args.no_loo and LOO_CSV.exists():
            frames["loo"] = pd.read_csv(LOO_CSV)
            continue
        frame = frames[key]
        if key == "paths":
            # 버킷별 층은 가중 합 행으로 요약돼 있다 — 공개 CSV에는 싣지 않는다(원자료는 캐시).
            frame = frame[frame["control"] != CONTROL_REALIZED]
        frame.to_csv(path, index=False, float_format="%.10g")
    render_figure(frames["paths"])
    included_n = {seg: int((matrices.labels["segment"] == seg).sum()) for seg in SEGMENTS}
    summary = render_summary(
        census=census_frame,
        exclusions=exclusions,
        holding=frames["holding"],
        decomposition=frames["decomposition"],
        paths=frames["paths"],
        points=frames["points"],
        verdicts=frames["verdicts"],
        bins=frames["bins"],
        loo=frames["loo"],
        checksum=frames["checksum"],
        population_n=population_n,
        included_n=included_n,
        cost_note=cost_note,
    )
    SUMMARY_PATH.write_text(summary)
    print(summary)
    bad = [c for c in checks if not (abs(c.diff) <= 1e-9)]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
