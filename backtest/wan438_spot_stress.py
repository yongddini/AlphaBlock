"""WAN-438: 스토캐스틱 팔 + 몰림 규칙을 **현물 1분봉**으로 6년 창 밖에 넣는다.

대상은 2018 하락장 · 2020-03 폭락이다.

## 왜 이 모듈이 있나

WAN-436이 페이퍼 좌표로 정한 규칙(몰림 중 BTC 4h ≤ −2.5% · 거래당 2%)의 평가손 MDD 32%는 **못 박은
6년 창(2020-09-15~)의 최악**이다. 그 창에는 2018 하락장과 2020-03 코로나 폭락이 없고,
선물 아카이브는 2020-01부터라 데울 수 없다 — 그래서 **현물**로 넣어 본다
(사용자 제안 「스팟으로 보면 되잖아」).

## 규칙 — WAN-436 그대로 고정(결과를 보고 옮기지 않는다)

롱 · 첫 탭 · 재진입 없음 · 손절폭 ≥ 4% · 직전 확정봉 %K 문턱 · ts4 · 손절 2배 · 초반 건너뛰기 1~10 ·
몰림(11+) 중 BTC 4h ≤ −2.5% · 24h 예산 10 · 거래당 2% · 복리 · 채택 북 · 1분 평가손 MDD.

* **%K 문턱 두 팔**: **K<25**(페이퍼 좌표 · 주 수치) · K<30(대조 = 문턱 민감도 ·
  고르는 자리가 아니다, WAN-161).
* **게이트 없음 팔**(WAN-436 「기준 팔」 = 손절 2배 + 건너뛰기 + 예산)과 병기.
* **시간봉** 1h·2h·4h·6h·8h·12h·1d·1w(3d 제외 — 사용자 결정 · 3h도 WAN-424 격자에만 있고 이
  이슈 목록에 없다).

## 현물을 어떻게 돌리나 — 러너를 고치지 않는다

현물 봉은 `data.spot_klines`가 **별도 루트**(`<root>/data/ohlcv.db`)에 선물 표기로 적재한다. 백테
러너(`wan169.run_cells`)는 상대경로 `data/ohlcv.db`를 읽으므로 **그 루트를 작업 디렉터리로
삼아** 돌면 엔진·러너 코드를 한 줄도 안 바꾸고(→ 선물 payload 캐시도 안 깨진다) 같은 팔이
현물에서 돈다(`working_root`). base 후보 인자는 `wan424.base_cell_kwargs` **한 곳**에서
읽는다. 🚨 현물 payload 캐시는 루트 아래 **별도 디렉터리**다 — 심볼 표기가 선물과
같아 같은 캐시를 쓰면 섞일 수 있다.

## 순서 (이슈 §순서)

1. **현물 대 선물 대조(검산)** — 겹치는 구간 2020-09-15~2021-09-15(2021-05-19 급락일 포함)을 같은
   좌표로. 선물은 WAN-424 base payload(캐시)를 21종목 × 8TF로 좁혀 쓰고, 현물 루트는 (종목, TF)마다
   **선물 DB의 첫 봉 시각부터**만 적재해 데이터 가용성 모양까지 맞춘다(스토캐스틱 워밍업 이력이
   달라지지 않게). 두 쪽 다 같은 날(2020-09-15)에 오더블록 재고 0에서 시작한다.
2. **창 밖 스트레스** — 현물 2017-08~2020-08을 돌리고 **2018-01-01 이후 진입만** 센다(앞 다섯 달은
   워밍업). 구간: 창 밖 전체 · 2018 하락장 · 2020-03 폭락(2020-02~04). 손절 체결
   스트레스(그 1분 저가) 병기.
3. 페이퍼 크기(2%)가 그 폭락에서 몇 %를 잃는지 한 줄.

## 알려진 한계 (착수 전 기록 · 요약에도 찍는다)

* 🚨 **현물은 폭락 순간이 덜 깊다** — 2020-03-12 BTC 선물은 청산 연쇄로 현물보다 약 5% 더 빠졌다.
  현물 결과는 「최소 이만큼」으로 읽는다.
* 현물에는 **펀딩이 없다**(대조의 선물 쪽은 펀딩 포함 — 차이의 일부다) · 2018년은 종목이 적어
  BTC·ETH 위주의 참고값이다 · 종목·기간이 달라 6년 결과와 **셀 비교 금지**.
* 1분 안의 갭·호가 공백은 못 본다(WAN-397/98).

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 엔진·러너 코드 무수정 · 운영 DB 불변 WAN-194) · 이 팔은
**채택 좌표가 아니다** · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import math
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan424_stoch_ob_arm import base_cell_kwargs, build_base_payloads
from backtest.wan436_stoch_crowd_rules import (
    BASE,
    DAY_MS,
    ArmEntry,
    PlacedTrade,
    RuleSet,
    SimResult,
    build_entries,
    place,
    signal_counts,
    simulate,
)
from backtest.wan436_stoch_crowd_rules import DEFAULT_PAYLOAD_DIR as FUTURES_PAYLOAD_DIR
from data import spot_klines
from data.spot_klines import (
    NATIVE_TIMEFRAMES,
    SPOT_SYMBOLS,
    MonthCheck,
    MonthFile,
    check_month,
    list_months,
    load_into_root,
    on_grid,
    read_month,
    select_months,
    to_store_symbol,
)
from data.storage import OhlcvStore, source_timeframe

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = Path(__file__).resolve().parent / "reports"
LOAD_CHECK_PATH = REPORT_DIR / "wan438_spot_load_check.csv"
QUARANTINE_PATH = REPORT_DIR / "wan438_spot_quarantine.csv"
PARITY_PATH = REPORT_DIR / "wan438_spot_vs_futures.csv"
STRESS_PATH = REPORT_DIR / "wan438_spot_stress.csv"
SUMMARY_PATH = REPORT_DIR / "wan438_spot_stress_summary.md"
DEFAULT_ROOTS = REPO_ROOT / "data" / "cache" / "wan438-roots"
DEFAULT_ZIP_CACHE = REPO_ROOT / "data" / "cache" / "wan438-spot"

# ---------------------------------------------------------------------------
# 착수 전에 고정한 숫자 — 결과를 보고 옮기지 않는다(WAN-161 · 이슈 §무엇을)
# ---------------------------------------------------------------------------

TIMEFRAMES: tuple[str, ...] = ("1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w")
K_ARMS: tuple[float, ...] = (25.0, 30.0)
"""%K 문턱 — 25가 페이퍼 좌표(주 수치) · 30은 민감도(고르는 자리가 아니다)."""
PRIMARY_K = 25.0
GATE = -0.025
"""몰림 중 BTC 4h 경계 — WAN-435 페이퍼 좌표(−2.59%의 반올림 · 사용자 결정 2026-09-29)."""
RISK = 0.02
"""거래당 리스크 — 페이퍼 크기(사용자 결정 2026-09-29)."""
GATED = dataclasses.replace(BASE, btc_gate=GATE)
RULE_ARMS: tuple[tuple[str, RuleSet], ...] = (("게이트 −2.5%", GATED), ("게이트 없음", BASE))
STOP_FILLS: tuple[str, ...] = ("stop", "bar_low")
STOP_FILL_LABEL = {"stop": "손절가 체결", "bar_low": "그 1분 저가 체결"}

#: 선물과 겹치는 대조 구간 — 채택 창 시작(두 쪽 다 존 재고 0)부터 1년. 2021-05-19 급락일을 포함한다.
OVERLAP_START = harness.DEFAULT_START
OVERLAP_END = "2021-09-15"
OVERLAP_LOAD_END = "2021-10-15"
"""현물 대조 런의 끝 — 대조 구간 끝 근처에 진입한 거래가 데이터 끝에서 잘리지 않게 한 달 더 둔다."""
OVERLAP_FIRST_MONTH, OVERLAP_LAST_MONTH = "2020-07", "2021-10"

#: 창 밖 스트레스 — 현물이 있는 가장 이른 달부터. 2018-01 이전 진입은 세지 않는다(워밍업).
STRESS_START = "2017-08-01"
STRESS_END = "2020-09-01"
STRESS_FIRST_MONTH, STRESS_LAST_MONTH = "2017-08", "2020-08"
STRESS_SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("창 밖 전체", "2018-01-01", "2020-09-01"),
    ("2018 하락장", "2018-01-01", "2019-01-01"),
    ("2020-03 폭락", "2020-02-01", "2020-05-01"),
)
CRASH_SEGMENT = "2020-03 폭락"
WAN436_SIX_YEAR_MDD = 0.320
"""WAN-436 §7 — 페이퍼 좌표(−2.5% · 2%)의 6년 평가손 MDD(손절가 체결). 한 줄 대조용."""
WAN436_SIX_YEAR_MDD_STRESS = 0.380
"""WAN-436 §8 — 같은 좌표의 손절 체결 스트레스(그 1분 저가) 6년 평가손 MDD."""


def ms(date: str) -> int:
    return parse_date_ms(date)


def store_symbols() -> tuple[str, ...]:
    return tuple(to_store_symbol(s) for s in SPOT_SYMBOLS)


# ---------------------------------------------------------------------------
# 1. 적재 — 받기 · 검증 · 루트
# ---------------------------------------------------------------------------


def wanted_files(first: str, last: str) -> list[MonthFile]:
    return [
        f
        for s in SPOT_SYMBOLS
        for tf in NATIVE_TIMEFRAMES
        for f in select_months(list_months(s, tf), first, last)
    ]


def futures_first_open(
    db_path: Path, symbols: Sequence[str], timeframes: Sequence[str]
) -> dict[tuple[str, str], int]:
    """선물 DB에서 (심볼, **물리** TF)별 첫 봉 시각 — 대조 루트를 같은 가용성 모양으로 자른다."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    out: dict[tuple[str, str], int] = {}
    try:
        for s in symbols:
            for tf in {source_timeframe(t) for t in timeframes} | {"1m"}:
                row = conn.execute(
                    "SELECT MIN(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = ?", (s, tf)
                ).fetchone()
                if row and row[0] is not None:
                    out[(s, tf)] = int(row[0])
    finally:
        conn.close()
    return out


@dataclass(frozen=True)
class LoadCheckRow:
    root: str
    symbol: str
    timeframe: str
    months: int
    first_month: str
    bars_in_files: int
    expected: int
    gaps: int
    longest_gap_bars: int
    duplicates: int
    off_grid: int
    """거래소 원본의 격자 밖 행 — **버렸다**(적재 안 함 · 구멍으로 센다)."""
    outside_month: int
    stored: int
    """루트에 실제로 들어간 행 수(창 자르기 뒤)."""


def build_root(
    name: str,
    root: Path,
    files: Sequence[MonthFile],
    *,
    zip_cache: Path,
    start_ms: dict[tuple[str, str], int] | None,
    end_ms: int,
) -> list[LoadCheckRow]:
    """받은 zip을 읽어 검사하고 루트에 적재한다 → (종목, TF)별 검사 행."""
    checks: dict[tuple[str, str], list[MonthCheck]] = {}

    def frames() -> Iterator[tuple[str, str, pd.DataFrame]]:
        for f in files:
            frame = read_month(spot_klines.cache_path(f, zip_cache))
            checks.setdefault((f.symbol, f.timeframe), []).append(
                check_month(frame, symbol=f.symbol, timeframe=f.timeframe, month=f.month)
            )
            # 격자 밖 행은 버린다(구멍으로 센다 — `spot_klines.on_grid`). 그 수가 `off_grid`다.
            yield to_store_symbol(f.symbol), f.timeframe, frame[on_grid(frame, f.timeframe)]

    stored = load_into_root(root, frames(), start_ms=start_ms, end_ms=end_ms)
    rows = []
    for (symbol, tf), cs in sorted(checks.items()):
        rows.append(
            LoadCheckRow(
                root=name,
                symbol=symbol,
                timeframe=tf,
                months=len(cs),
                first_month=min(c.month for c in cs),
                bars_in_files=sum(c.bars for c in cs),
                expected=sum(max(c.expected, 0) for c in cs),
                gaps=sum(c.gaps for c in cs),
                longest_gap_bars=max(c.longest_gap_bars for c in cs),
                duplicates=sum(c.duplicates for c in cs),
                off_grid=sum(c.off_grid for c in cs),
                outside_month=sum(c.outside_month for c in cs),
                stored=stored.get((to_store_symbol(symbol), tf), 0),
            )
        )
    return rows


def verify_root_continuity(root: Path, symbols: Sequence[str]) -> list[str]:
    """적재 뒤 검산 — 저장된 1분봉이 격자 위에 있고(off-grid 0) 중복이 없는지 DB에서 다시 잰다."""
    problems = []
    conn = sqlite3.connect(spot_klines.root_db_path(root))
    try:
        for s in symbols:
            n, off = conn.execute(
                "SELECT COUNT(*), SUM(open_time % 60000 != 0) FROM ohlcv "
                "WHERE symbol = ? AND timeframe = '1m'",
                (s,),
            ).fetchone()
            if n and off:
                problems.append(f"{s} 1m: 격자 밖 {off}행")
    finally:
        conn.close()
    return problems


# ---------------------------------------------------------------------------
# 2. 러너 — 현물 루트를 작업 디렉터리로 삼는다
# ---------------------------------------------------------------------------


@contextmanager
def working_root(root: Path) -> Iterator[None]:
    """러너가 상대경로 `data/ohlcv.db`를 읽는 동안 작업 디렉터리를 현물 루트로 바꾼다.

    spawn 워커는 부모의 작업 디렉터리와 `sys.path`를 물려받는다 — 저장소 루트를 절대경로로
    `sys.path`에 넣어 두어야 워커가 패키지를 찾는다. 🚨 루트 DB가 운영 DB와 같은 파일이면
    `spot_klines.guard_root`가 먼저 거부한다.
    """
    spot_klines.guard_root(root, repo_db=REPO_ROOT / harness.DB_PATH)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    previous = os.getcwd()
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(previous)


def spot_payloads(
    root: Path,
    *,
    start: str,
    end: str,
    jobs: int,
    timeframes: Sequence[str] = TIMEFRAMES,
) -> list[CellPayload]:
    """현물 루트에서 base 후보 — 인자는 `wan424.base_cell_kwargs` 그대로(차가운 절단만 끈다).

    `timeframes`(옵트인, WAN-439)는 TF 목록만 바꾼다 — 안 주면 8TF 그대로라 캐시 키·결과가 같다.

    차가운 절단(`is`/`oos`)은 이 이슈가 안 읽는 구간이라 끈다(`full` 후보는 불변 — WAN-301).
    펀딩 대리는 걸지 않는다 — 현물에는 펀딩이 없다.
    """
    kwargs = base_cell_kwargs()
    # 익절 회계가 채택 값(메이커, WAN-370)이어야 선물 쪽(WAN-424 캐시)과 같은 팔이다.
    if kwargs["take_profit_liquidity"] is not harness.ADOPTED_TAKE_PROFIT_LIQUIDITY:
        raise AssertionError("base_cell_kwargs의 take_profit_liquidity가 채택 값이 아닙니다")
    with working_root(root):
        payloads = run_cells(
            store_symbols(),
            tuple(timeframes),
            start=start,
            end=end,
            jobs=jobs,
            cold_segments=False,
            payload_cache=PayloadCache(root / "payloads"),
            **kwargs,
        )
    # 차가운 절단을 끈 payload에는 `is`/`oos` 펀딩 키가 없다 — 공용 배치기(`place_segments`)가 세
    # 구간을 다 도므로 빈 값을 채운다. 이 모듈이 읽는 것은 `full`뿐이라 수치는 안 움직인다.
    blank = {harness.SEGMENT_IS: (), harness.SEGMENT_OOS: ()}
    return [dataclasses.replace(p, funding={**blank, **p.funding}) for p in payloads]


def entries_for(
    payloads: Sequence[CellPayload],
    store: OhlcvStore,
    *,
    start_ms: int,
    end_ms: int,
    k_threshold: float,
    entry_from_ms: int,
    entry_to_ms: int,
) -> list[ArmEntry]:
    """팔 후보 → 진입 시각 창으로 자른다(신호 수도 이 창 안에서만 센다)."""
    entries = build_entries(
        payloads, store=store, start_ms=start_ms, end_ms=end_ms, k_threshold=k_threshold
    )
    return [e for e in entries if entry_from_ms <= int(e.cand.entry_time) < entry_to_ms]


def place_all(
    payloads: Sequence[CellPayload], entries: Sequence[ArmEntry], stop_fill: str
) -> dict[str, list[PlacedTrade]]:
    counts = signal_counts([int(e.cand.entry_time) for e in entries])
    return {
        name: place(payloads, entries, rules, counts=counts, stop_fill=stop_fill)
        for name, rules in RULE_ARMS
    }


# ---------------------------------------------------------------------------
# 표
# ---------------------------------------------------------------------------


def _utc(epoch_ms: int) -> str:
    return dt.datetime.fromtimestamp(epoch_ms / 1000, tz=dt.UTC).strftime("%Y-%m-%d %H:%M")


def worst_day_date(sim: SimResult) -> str:
    """종가 기준 가장 나쁜 하루(UTC) — `SimResult.worst_day`(저가 기준)의 날짜 짝."""
    if not sim.day_returns:
        return ""
    day = min(sim.day_returns, key=lambda d: sim.day_returns[d])
    return _utc(day * DAY_MS)[:10]


@dataclass(frozen=True)
class StatRow:
    part: str
    """`대조` 또는 `스트레스`."""
    market: str
    k_threshold: float
    arm: str
    stop_fill: str
    segment: str
    trades: int
    symbols: int
    total_return: float
    cagr: float
    mdd_low: float
    worst_day: float
    worst_day_date: str
    mdd_peak: str
    mdd_trough: str
    mean_net_r: float
    stop_rate: float
    cap_breaches: int


def stat_row(
    part: str,
    market: str,
    k: float,
    arm: str,
    stop_fill: str,
    segment: str,
    trades: Sequence[PlacedTrade],
) -> StatRow:
    sim = simulate(trades, risk=RISK)
    w = sum(t.size for t in trades)
    return StatRow(
        part=part,
        market=market,
        k_threshold=k,
        arm=arm,
        stop_fill=stop_fill,
        segment=segment,
        trades=sim.trades,
        symbols=len({t.symbol for t in trades}),
        total_return=sim.total_return,
        cagr=sim.cagr,
        mdd_low=sim.mdd_low,
        worst_day=sim.worst_day,
        worst_day_date=worst_day_date(sim),
        mdd_peak=_utc(sim.mdd_peak_ms) if trades else "",
        mdd_trough=_utc(sim.mdd_trough_ms) if trades else "",
        mean_net_r=sum(t.size * t.net_r for t in trades) / w if w else math.nan,
        stop_rate=sum(t.stopped for t in trades) / len(trades) if trades else math.nan,
        cap_breaches=sim.cap_breaches,
    )


def in_window(trades: Sequence[PlacedTrade], lo: str, hi: str) -> list[PlacedTrade]:
    a, b = ms(lo), ms(hi)
    return [t for t in trades if a <= t.entry_time < b]


@dataclass(frozen=True)
class OverlapRow:
    """대조 구간의 거래 집합 겹침 — (종목, TF, 진입 시각)이 **정확히** 같은 거래."""

    k_threshold: float
    arm: str
    spot_trades: int
    futures_trades: int
    common: int
    common_share_of_futures: float
    mean_abs_net_r_diff: float
    """공통 거래의 |현물 net R − 선물 net R| 평균(같은 거래가 얼마나 다르게 끝났나)."""


def overlap_row(
    k: float, arm: str, spot: Sequence[PlacedTrade], fut: Sequence[PlacedTrade]
) -> OverlapRow:
    s_map = {(t.symbol, t.timeframe, t.entry_time): t for t in spot}
    f_map = {(t.symbol, t.timeframe, t.entry_time): t for t in fut}
    common = sorted(set(s_map) & set(f_map))
    diffs = [abs(s_map[key].net_r - f_map[key].net_r) for key in common]
    return OverlapRow(
        k_threshold=k,
        arm=arm,
        spot_trades=len(spot),
        futures_trades=len(fut),
        common=len(common),
        common_share_of_futures=len(common) / len(fut) if fut else math.nan,
        mean_abs_net_r_diff=sum(diffs) / len(diffs) if diffs else math.nan,
    )


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def _pct(x: float, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def render_summary(
    stats: pd.DataFrame,
    overlaps: pd.DataFrame,
    load: pd.DataFrame,
    quarantine: pd.DataFrame,
    *,
    size_line: str,
    elapsed: float | None,
) -> str:
    def pick(**kw: object) -> pd.DataFrame:
        m = pd.Series(True, index=stats.index)
        for k, v in kw.items():
            m &= stats[k] == v
        return stats[m]

    lines = [
        "# WAN-438 — 스토캐스틱 팔 + 몰림 규칙을 현물 1분봉으로 6년 창 밖에 넣는다",
        "",
        "규칙은 WAN-436 페이퍼 좌표 **그대로 고정**(롱 · 첫 탭 · 재진입 없음 · 손절폭 ≥ 4% ·",
        "직전 확정봉 %K 문턱 · ts4 · 손절 2배 · 초반 건너뛰기 1~10 · 몰림 중 BTC 4h ≤ −2.5% ·",
        "24h 예산 10 · 거래당 2% · 복리 · 채택 북 · 1분 평가손 MDD). %K<25가 주 수치이고",
        "%K<30은 민감도다(고르는 자리가 아니다).",
        "",
        f"데이터: {size_line}",
        "",
    ]
    # ---- 3. 한 줄
    crash = pick(part="스트레스", k_threshold=PRIMARY_K, arm="게이트 −2.5%", segment=CRASH_SEGMENT)
    whole = pick(part="스트레스", k_threshold=PRIMARY_K, arm="게이트 −2.5%", segment="창 밖 전체")
    if len(crash) and len(whole):
        c_stop = crash[crash.stop_fill == "stop"].iloc[0]
        c_low = crash[crash.stop_fill == "bar_low"].iloc[0]
        w_stop = whole[whole.stop_fill == "stop"].iloc[0]
        w_low = whole[whole.stop_fill == "bar_low"].iloc[0]
        lines += [
            "## 한 줄 (이슈 §순서 3)",
            "",
            f"* **페이퍼 크기(거래당 2%)는 2020-03 폭락 구간(2020-02~04)에서 평가손 MDD "
            f"{_pct(c_stop.mdd_low)}**(손절가 체결) · **{_pct(c_low.mdd_low)}**"
            "(그 1분 저가 체결)를 "
            f"냈다 — 하루 최악 {_pct(c_stop.worst_day, True)} / {_pct(c_low.worst_day, True)}.",
            f"* 창 밖 전체(2018-01~2020-08)의 평가손 MDD는 **{_pct(w_stop.mdd_low)}** / "
            f"{_pct(w_low.mdd_low)}(바닥 {w_stop.mdd_trough} UTC) — WAN-436 6년 창의 "
            f"{WAN436_SIX_YEAR_MDD:.0%} / {WAN436_SIX_YEAR_MDD_STRESS:.0%}와 나란히 놓되 "
            "**셀 비교는 아니다**"
            "(종목·기간·시장이 다르다).",
            "* 🚨 **현물은 폭락 순간이 덜 깊다**(2020-03-12 BTC 선물은 청산 연쇄로 약 5% 더 "
            "빠졌다) — 이 수치는 「최소 이만큼」이다.",
            "",
        ]
    # ---- 1. 대조
    lines += [
        "## §1 현물 대 선물 대조 (검산 · 2020-09-15~2021-09-15 · 21종목 × 8TF)",
        "",
        "| %K | 팔 | 시장 | 거래 | 복리 | 평가손 MDD | 하루 최악 | 거래당 net R | 손절률 |",
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: |",
    ]
    par = stats[(stats.part == "대조") & (stats.stop_fill == "stop")]
    for (k, arm), g in par.groupby(["k_threshold", "arm"], sort=False):
        for _, r in g.iterrows():
            lines.append(
                f"| <{k:g} | {arm} | {r.market} | {r.trades:,} | {_pct(r.total_return, True)} | "
                f"{_pct(r.mdd_low)} | {_pct(r.worst_day, True)} | {r.mean_net_r:+.3f} | "
                f"{_pct(r.stop_rate)} |"
            )
    lines += [
        "",
        "| %K | 팔 | 현물 거래 | 선물 거래 | 정확히 같은 거래 | 선물 대비 "
        "| 공통 거래 |Δnet R| 평균 |",
        "| -- | -- | --: | --: | --: | --: | --: |",
    ]
    for _, r in overlaps.iterrows():
        lines.append(
            f"| <{r.k_threshold:g} | {r.arm} | {r.spot_trades:,} | {r.futures_trades:,} | "
            f"{r.common:,} | {_pct(r.common_share_of_futures)} | {r.mean_abs_net_r_diff:.3f} |"
        )
    lines.append("")
    # ---- 2. 스트레스
    lines += [
        "## §2 창 밖 스트레스 (현물 · 2018-01-01 이후 진입 · 구간마다 자본 1에서 복리)",
        "",
        "| %K | 팔 | 체결 | 구간 | 거래 | 종목 | 복리 | 연환산 | 평가손 MDD | 바닥(UTC) "
        "| 하루 최악 | 거래당 net R |",
        "| -- | -- | -- | -- | --: | --: | --: | --: | --: | -- | --: | --: |",
    ]
    for _, r in stats[stats.part == "스트레스"].iterrows():
        lines.append(
            f"| <{r.k_threshold:g} | {r.arm} | {STOP_FILL_LABEL[r.stop_fill]} | {r.segment} | "
            f"{r.trades:,} | {r.symbols} | {_pct(r.total_return, True)} | {_pct(r.cagr, True)} | "
            f"{_pct(r.mdd_low)} | {r.mdd_trough} | {_pct(r.worst_day, True)} "
            f"({r.worst_day_date}) | {r.mean_net_r:+.3f} |"
        )
    lines.append("")
    # ---- 적재 검사
    lines += [
        "## 적재 검사 (완료 기준 1 — 봉 수 · 시각 연속성)",
        "",
        "| 루트 | TF | 종목 | 파일 봉 | 기대 봉 | 구멍(칸) | 최장 구멍(칸) | 중복 | 격자 밖 "
        "| 달 밖 | 적재 |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for (root, tf), g in load.groupby(["root", "timeframe"], sort=False):
        lines.append(
            f"| {root} | {tf} | {len(g)} | {g.bars_in_files.sum():,} | "
            f"{g.expected.sum():,} | {g.gaps.sum():,} | {g.longest_gap_bars.max():,} | "
            f"{g.duplicates.sum():,} | {g.off_grid.sum():,} | {g.outside_month.sum():,} | "
            f"{g.stored.sum():,} |"
        )
    lines += ["", "**불가능한 틱 격리**(바이낸스 현물 PERCENT_PRICE 배율 상 5 · 하 0.2 밖):", ""]
    if quarantine.empty:
        lines.append("* 없음")
    for _, q in quarantine.iterrows():
        lines.append(
            f"* {q.root} · {q.symbol} {q.timeframe} {_utc(int(q.open_time))} UTC — {q.action}"
            f" (저가 {q.old_low:g} → {q.new_low:g})"
        )
    lines += [
        "",
        "* 🚨 **격자 밖 행은 거래소 원본의 결함이고 버렸다** — 2017-12-04 06:00~(+20.8초 · 워밍업",
        "  구간) · 2018-02-09~10 점검 직후(+14.8~16.8초 · 약 20시간). 분으로 내려 맞추면 시각을",
        "  지어내는 것이라 구멍으로 센다(상위TF는 거래소 원본이라 영향 없음).",
        "* 1w는 기대 봉을 세지 않는다(월 경계에 걸친 주봉이 두 파일에 나뉜다) — 격자 검사는",
        "  「월요일 00:00 UTC」로 한다. 구멍은 거래소 점검 등 **실제로 없는 분**이고",
        "  지어내지 않는다(보간 없음).",
        "",
        "## 한계",
        "",
        "* 🚨 현물은 폭락 순간이 덜 깊다 — 결과는 「최소 이만큼」이다.",
        "* 현물에는 펀딩이 없다(대조의 선물 쪽은 펀딩 포함) · 2018년은 종목이 적다",
        "  (요약 표 「종목」 열) ·",
        "  6년 결과와 셀 비교 금지 · 1분 안의 갭·호가 공백은 못 본다(WAN-397/98).",
        "* **측정 전용 · 기본값·토대 불변** · 이 팔은 채택 좌표가 아니다 · 실거래 보류 유지.",
    ]
    if elapsed is not None:
        lines += ["", f"실측 {elapsed / 60:.0f}분."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


def part_load(args: argparse.Namespace) -> None:
    started = time.monotonic()
    stress_files = wanted_files(STRESS_FIRST_MONTH, STRESS_LAST_MONTH)
    overlap_files = wanted_files(OVERLAP_FIRST_MONTH, OVERLAP_LAST_MONTH)
    every = {(f.symbol, f.timeframe, f.month): f for f in stress_files + overlap_files}
    total = sum(f.size_bytes for f in every.values())
    print(f"받을 파일 {len(every):,}개 · {total / 1e6:,.1f}MB(목록 실측)", flush=True)
    fetched = spot_klines.fetch_months(list(every.values()), cache_dir=args.zip_cache, jobs=8)
    bad = [r for r in fetched if not r.ok]
    if bad:
        raise SystemExit(f"받기 실패 {len(bad)}건: {[(r.file, r.note) for r in bad[:5]]}")
    got = sum(r.file.size_bytes for r in fetched if not r.cached)
    print(f"받음 {got / 1e6:,.1f}MB · 캐시 적중 {sum(r.cached for r in fetched):,}", flush=True)
    firsts = futures_first_open(REPO_ROOT / harness.DB_PATH, store_symbols(), TIMEFRAMES)
    # 🚨 루트는 파생물이라 통째로 다시 만든다 — 루트 아래 payload 캐시의 키에는 **데이터 내용이
    # 없어서**, DB만 바꾸고 캐시를 두면 낡은 후보가 「히트」로 나온다(WAN-106/253 부류).
    for name in ("stress", "overlap"):
        shutil.rmtree(args.roots / name, ignore_errors=True)
    rows = build_root(
        "스트레스",
        args.roots / "stress",
        stress_files,
        zip_cache=args.zip_cache,
        start_ms=None,
        end_ms=ms(STRESS_END),
    )
    rows += build_root(
        "대조",
        args.roots / "overlap",
        overlap_files,
        zip_cache=args.zip_cache,
        start_ms=firsts,
        end_ms=ms(OVERLAP_LOAD_END) + 31 * DAY_MS,
    )
    quarantined = []
    for label, name in (("스트레스", "stress"), ("대조", "overlap")):
        for q in spot_klines.quarantine_impossible_ticks(args.roots / name):
            quarantined.append({"root": label, **dataclasses.asdict(q)})
            print(f"  격리 {label} {q.symbol} {q.timeframe} {_utc(q.open_time)} {q.action}")
    pd.DataFrame(
        quarantined,
        columns=["root", *(f.name for f in dataclasses.fields(spot_klines.Quarantine))],
    ).to_csv(QUARANTINE_PATH, index=False)
    if any(q["action"] == "검토 필요" for q in quarantined):
        raise SystemExit("1분봉 근거 없는 불가능한 상위TF 봉이 있다 — 손대지 않고 멈춘다")
    for name in ("stress", "overlap"):
        problems = verify_root_continuity(args.roots / name, store_symbols())
        if problems:
            raise SystemExit(f"{name} 루트 검산 실패: {problems}")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dataclasses.asdict(r) for r in rows]).to_csv(LOAD_CHECK_PATH, index=False)
    print(f"적재 {time.monotonic() - started:.0f}s → {LOAD_CHECK_PATH}", flush=True)


def part_run(args: argparse.Namespace) -> None:
    started = time.monotonic()
    stats: list[StatRow] = []
    overlaps: list[OverlapRow] = []

    # ---- §1 대조: 선물(WAN-424 캐시 · 21종목 × 8TF) 대 현물(대조 루트)
    fut_payloads = build_base_payloads(
        jobs=args.jobs,
        payload_dir=args.futures_payload_dir,
        symbols=store_symbols(),
        timeframes=TIMEFRAMES,
    )
    print(f"선물 base 후보 {time.monotonic() - started:.0f}s", flush=True)
    spot_ov = spot_payloads(
        args.roots / "overlap", start=OVERLAP_START, end=OVERLAP_LOAD_END, jobs=args.jobs
    )
    print(f"현물(대조) base 후보 {time.monotonic() - started:.0f}s", flush=True)
    fut_store = OhlcvStore(REPO_ROOT / harness.DB_PATH)
    ov_store = OhlcvStore(spot_klines.root_db_path(args.roots / "overlap"))
    a, b = ms(OVERLAP_START), ms(OVERLAP_END)
    for k in K_ARMS:
        fut_e = entries_for(
            fut_payloads,
            fut_store,
            start_ms=a,
            end_ms=ms(OVERLAP_LOAD_END),
            k_threshold=k,
            entry_from_ms=a,
            entry_to_ms=b,
        )
        spot_e = entries_for(
            spot_ov,
            ov_store,
            start_ms=a,
            end_ms=ms(OVERLAP_LOAD_END),
            k_threshold=k,
            entry_from_ms=a,
            entry_to_ms=b,
        )
        fut_t = place_all(fut_payloads, fut_e, "stop")
        spot_t = place_all(spot_ov, spot_e, "stop")
        for arm, _rules in RULE_ARMS:
            stats.append(stat_row("대조", "선물", k, arm, "stop", "대조 구간", fut_t[arm]))
            stats.append(stat_row("대조", "현물", k, arm, "stop", "대조 구간", spot_t[arm]))
            overlaps.append(overlap_row(k, arm, spot_t[arm], fut_t[arm]))
        print(f"  대조 %K<{k:g} {time.monotonic() - started:.0f}s", flush=True)

    # ---- §2 창 밖 스트레스(현물 스트레스 루트)
    spot_st = spot_payloads(
        args.roots / "stress", start=STRESS_START, end=STRESS_END, jobs=args.jobs
    )
    print(f"현물(스트레스) base 후보 {time.monotonic() - started:.0f}s", flush=True)
    st_store = OhlcvStore(spot_klines.root_db_path(args.roots / "stress"))
    for k in K_ARMS:
        entries = entries_for(
            spot_st,
            st_store,
            start_ms=ms(STRESS_START),
            end_ms=ms(STRESS_END),
            k_threshold=k,
            entry_from_ms=ms(STRESS_SEGMENTS[0][1]),
            entry_to_ms=ms(STRESS_END),
        )
        for fill in STOP_FILLS:
            placed = place_all(spot_st, entries, fill)
            for arm, _rules in RULE_ARMS:
                for seg, lo, hi in STRESS_SEGMENTS:
                    stats.append(
                        stat_row(
                            "스트레스", "현물", k, arm, fill, seg, in_window(placed[arm], lo, hi)
                        )
                    )
        print(f"  스트레스 %K<{k:g} {time.monotonic() - started:.0f}s", flush=True)

    st_frame = pd.DataFrame([dataclasses.asdict(r) for r in stats])
    ov_frame = pd.DataFrame([dataclasses.asdict(r) for r in overlaps])
    st_frame[st_frame.part == "대조"].to_csv(PARITY_PATH, index=False)
    ov_frame.to_csv(PARITY_PATH.with_name("wan438_spot_vs_futures_overlap.csv"), index=False)
    st_frame[st_frame.part == "스트레스"].to_csv(STRESS_PATH, index=False)
    write_summary(elapsed=time.monotonic() - started, size_line=args.size_line)


def load_stats() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stats = pd.concat([pd.read_csv(PARITY_PATH), pd.read_csv(STRESS_PATH)], ignore_index=True)
    stats["mdd_trough"] = stats["mdd_trough"].fillna("")
    stats["worst_day_date"] = stats["worst_day_date"].fillna("")
    overlaps = pd.read_csv(PARITY_PATH.with_name("wan438_spot_vs_futures_overlap.csv"))
    return stats, overlaps, pd.read_csv(LOAD_CHECK_PATH), pd.read_csv(QUARANTINE_PATH)


def write_summary(*, elapsed: float | None, size_line: str) -> None:
    stats, overlaps, load, quarantine = load_stats()
    SUMMARY_PATH.write_text(
        render_summary(stats, overlaps, load, quarantine, size_line=size_line, elapsed=elapsed)
    )
    print(f"→ {SUMMARY_PATH}", flush=True)


SIZE_LINE = (
    "`data.binance.vision` 현물 월별 zip 21종목 × 8TF(1m + 거래소 원본 "
    "1h·4h·6h·8h·12h·1d·1w · 2h는 1h 파생) · 2017-08~2021-10 · 6,345파일 · 1,345.0MB"
    "(S3 목록 실측 · 1분봉 1,294.0MB) · 받기 231초 · 체크섬 전부 일치 · "
    "`data/cache/` 캐시 파일(운영 DB 불변)"
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--part", choices=("load", "run", "summary"), default="run")
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--roots", type=Path, default=DEFAULT_ROOTS)
    parser.add_argument("--zip-cache", type=Path, default=DEFAULT_ZIP_CACHE)
    parser.add_argument("--futures-payload-dir", type=Path, default=FUTURES_PAYLOAD_DIR)
    parser.add_argument("--size-line", default=SIZE_LINE)
    args = parser.parse_args(argv)
    if args.part == "load":
        part_load(args)
    elif args.part == "run":
        part_run(args)
    else:
        write_summary(elapsed=None, size_line=args.size_line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
