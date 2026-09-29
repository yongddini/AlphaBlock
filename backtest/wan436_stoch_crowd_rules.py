"""WAN-436: 스토캐스틱 팔 × 몰림 규칙 — 숫자를 고정하고 채택 북 위에서 **평가손 포함 MDD**로 잰다.

## 왜 이 모듈이 있나

WAN-434가 이 팔(롱 · 오더블록 첫 탭 · 손절폭 하한 4% · 직전 확정봉 %K<25 · ts4 시간 청산 · 31종목 ×
1h~1w 9TF)의 거래가 **급락일에 몰리고** MDD가 **급락일 하루**에 난다는 것을 보였다. 그 PR 작업 중의
탐색(`docs/decisions/wan434_scratch/`)이 규칙 넷을 찾았고, 이 모듈은 그것을 **착수 전에 숫자를
고정**해 한 번 잰다(결과를 보고 옮기지 않는다 — WAN-161).

## 규칙 넷 (전부 옵트인 — `RuleSet()`은 WAN-434 팔 그대로다)

* **손절 배수** `stop_multiple` — 손절선 = 진입가 − k × (진입가 − 오더블록 손절가). 리스크 1R은
  유지하므로
  포지션은 1/k가 된다. 청산 규칙(ts4 시간 청산 · 손절 우선 · 손절가 체결)은 그대로.
* **초반 건너뛰기** `skip_early` — 진입 순간 **직전 24시간 신호 수**가 1~10이면 건너뛴다(급락 초반).
* **몰림 중 BTC 게이트** `btc_gate` — 신호 수가 11 이상인 진입은 BTC 직전 4h 수익률 ≤ 문턱일 때만.
* **24h 리스크 예산** `budget` — 크기 × min(1, budget / (신호 수 + 1)).

📌 **신호 수는 체결 여부와 무관하게 센다** — 팔의 후보(조건을 충족한 셋업) 전부의 체결 시각이 신호
시각이다. 우리가 들어간 거래만 세면 초반을 건너뛰는 순간 카운트가 멈춘다. 창은 `[t − 24h, t)`이고
**같은 ms의 다른 신호는 세지 않는다**(동시 도착은 서로의 존재를 모른다 — 인과).

## 평가 방식 — 탐색이 한 번 틀린 자리를 코드로 못 박았다

* **배치**는 `wan424.place_segments`(채택 북 · 한 지갑 · 칸당 1포지션 · 5배 명목 상한 · 복리 끔)
  그대로다.
* **복리 자본 경로**는 이 모듈이 낸다(`simulate`): 각 거래가 **진입 순간의 확정 자본** × 거래당
  리스크 ×
  예산 배율로 사이징되고, 청산 때 `자본 += 리스크 금액 × net R`이다. 🚨 거래를 청산 순서로 한 줄로
  곱하면(탐색의 첫 근사) 동시에 열린 수십 개 포지션이 서로의 자본 변화를 반영하는 것처럼 계산돼 크게
  부풀려진다(1배 +1,886% 대 이 방식 +590%).
* **평가손 포함 MDD** — 1분마다 보유 포지션을 1분 **저가**(보수)와 **종가**로 평가한 계좌 가치의
  MDD.
  비는 1분은 직전 값으로 채운다(거래가 없던 분).
* ⚠️ 북의 명목 상한은 **복리 끔** 배치에서 걸린다 — 복리 사이징에서 그 상한(자본 × 5)을 넘는 진입이
  있었는지는 `simulate`가 **센다**(`cap_breaches`). 유동성(ADV) 한도는 WAN-424 좌표대로 꺼져 있다.

## 판정 상수 (착수 전 고정 · `verdict`)

**통과** = 뒷구간 평가손 MDD(저가) ≤ 30% **그리고** 같은 크기의 기준 팔(손절 2배 + 건너뛰기 + 예산,
게이트 없음)보다 뒷구간 복리 수익이 높다. 차이가 **날짜 블록 부트스트랩 2σ 안**이면 판정문이 그
사실을
함께 적는다(PM 코멘트 2026-09-29 — 뒷구간 실효 표본이 거래 수보다 훨씬 작다, WAN-434 §E).

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()`
그대로 ·
엔진 코드 무수정 · 핀 없음 WAN-305) · 이 팔은 **채택 좌표가 아니다** · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import datetime as dt
import math
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest.book_cli import net_r
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload
from backtest.wan424_stoch_ob_arm import SYMBOLS as SYMBOLS
from backtest.wan424_stoch_ob_arm import TIMEFRAMES as TIMEFRAMES
from backtest.wan424_stoch_ob_arm import (
    arm_pool,
    build_base_payloads,
    k_before,
    place_segments,
    stoch_series,
)
from backtest.wan432_stoch_floor_robustness import ADOPTED_FLOOR as ADOPTED_FLOOR
from backtest.wan432_stoch_floor_robustness import SIGN_SIGMA as SIGN_SIGMA
from backtest.zone_limit_backtest import _Candidate
from common.costs import Liquidity
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

REPORT_DIR = Path(__file__).resolve().parent / "reports"
CSV_PATH = REPORT_DIR / "wan436_stoch_crowd_rules.csv"
MONTHLY_PATH = REPORT_DIR / "wan436_monthly.csv"
MATCHED_PATH = REPORT_DIR / "wan436_mdd_matched.csv"
STRESS_PATH = REPORT_DIR / "wan436_stop_fill_stress.csv"
GRID_PATH = REPORT_DIR / "wan436_gate_x_risk_grid.csv"
SUMMARY_PATH = REPORT_DIR / "wan436_stoch_crowd_rules_summary.md"
DEFAULT_PAYLOAD_DIR = Path(__file__).resolve().parent / "cache" / "wan424_payloads"
WAN434_TRADES = REPORT_DIR / "wan434_trades.csv.gz"
WAN434_COMBO = "ts4·하한4%·%K<25"

# ---------------------------------------------------------------------------
# 착수 전에 고정한 숫자 — 결과를 보고 옮기지 않는다(WAN-161 · 이슈 §사양)
# ---------------------------------------------------------------------------

HOLD = 4
"""시간 청산 봉 수(ts4) — WAN-423/424 표시 조합."""
K_THRESHOLD = 25.0
"""직전 확정봉 %K 문턱 — WAN-424 표시 조합."""
FLOOR = ADOPTED_FLOOR
"""손절폭 하한 4% — WAN-432 채택 하한."""

STOP_MULTIPLE = 2.0
SKIP_LOW, SKIP_HIGH = 1, 10
"""직전 24h 신호 수가 이 닫힌 구간이면 건너뛴다(급락 초반)."""
CROWD_MIN = SKIP_HIGH + 1
"""「몰림 중」의 하한 — 건너뛰기 구간 바로 위다(두 숫자가 갈라지지 않게 파생)."""
BUDGET = 10.0
BTC_GATE = -0.0259
"""앞구간 몰림 진입 BTC 4h 수익률 3분위 경계(탐색이 결과를 보기 전 기계적으로 산출한 값)."""
RISK = 0.015
"""거래당 리스크 — 사용자 결정(2026-09-29 · MDD 30% 대비 여유)."""
MDD_LIMIT = 0.30
CAP_MULTIPLE = 5.0
"""채택 북의 명목 상한 배수(`LeverageBookParams()` = cap_only 5배) — 복리 사이징에서 넘는지 센다."""

SIGNAL_WINDOW_MS = 24 * 3_600_000
BTC_LOOKBACK_MIN = 240
BTC_SYMBOL = "BTC/USDT:USDT"
MINUTE_MS = 60_000
DAY_MS = 86_400_000

#: 이웃 확인(판정 아님) — 이슈 §사양.
NEIGHBOR_GATES: tuple[float, ...] = (-0.020, -0.030)
NEIGHBOR_RISKS: tuple[float, ...] = (0.0125, 0.0175)

#: **관측(판정 아님)** — 몰림 중 BTC 게이트 경계별로 크기를 「6년 평가손 MDD = 30%」에 맞춘
# 비교(사용자
#: 요청 2026-09-29 · 탐색 표를 정식 코드로). `None` = 게이트 없음(기준 팔).
MATCHED_GATES: tuple[float | None, ...] = (
    None,
    -0.010,
    -0.015,
    -0.020,
    -0.025,
    BTC_GATE,
    -0.030,
    -0.035,
    -0.040,
    -0.050,
)
MATCHED_RISK_RANGE: tuple[float, float] = (0.001, 0.08)
MATCHED_ITERATIONS = 14
# 손절 체결 스트레스(사용자 요청 2026-09-29) — 판정과 무관한 관측. 페이퍼 병행 좌표(−2.5% · 2%)와
# 그 주변 크기에서 「손절이 그 1분 저가에 체결됐다면」을 잰다.
STOP_FILLS: tuple[str, ...] = ("stop", "bar_low")
STRESS_GATE = -0.025
STRESS_RISKS: tuple[float, ...] = (0.015, 0.0175, 0.020, 0.0222)
# 맞출 6년 평가손 MDD — 30%는 사용자 한도, 35%는 「한도를 올리면?」 질문(2026-09-29)에 답하는 관측.
MATCHED_TARGETS: tuple[float, ...] = (MDD_LIMIT, 0.35)

#: **관측(판정 아님)** — 경계 × 거래당 리스크 격자(사용자 요청 2026-09-29). 크기 목록은 탐색 기준
#: 평가손 MDD 약 15~45%를 덮도록 골랐고, 한도 30% 근처(1.25~2.0%)를 0.25% 간격으로 촘촘히 둔다.
GRID_RISKS: tuple[float, ...] = (0.0075, 0.010, 0.0125, 0.015, 0.0175, 0.020, 0.025, 0.030)

BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 436
CHECKSUM_TOL = 1e-9

SEGMENT_FULL = "full"
SEGMENT_FRONT = "앞구간"
SEGMENT_BACK = "뒷구간"


@dataclass(frozen=True)
class RuleSet:
    """규칙 넷. 기본값은 **전부 꺼짐** = WAN-434 팔 그대로(검산 대상)."""

    stop_multiple: float = 1.0
    skip_early: bool = False
    budget: float | None = None
    btc_gate: float | None = None

    @property
    def label(self) -> str:
        parts = [f"손절 {self.stop_multiple:g}배"]
        if self.skip_early:
            parts.append("초반 건너뛰기")
        if self.budget is not None:
            parts.append(f"24h 예산 {self.budget:g}")
        if self.btc_gate is not None:
            parts.append(f"몰림 중 BTC 4h ≤ {self.btc_gate:+.2%}")
        return " · ".join(parts)


OFF = RuleSet()
BASE = RuleSet(stop_multiple=STOP_MULTIPLE, skip_early=True, budget=BUDGET)
PROPOSED = dataclasses.replace(BASE, btc_gate=BTC_GATE)


# ---------------------------------------------------------------------------
# 규칙 — 순수 함수
# ---------------------------------------------------------------------------


def signal_counts(times: Sequence[int], *, window_ms: int = SIGNAL_WINDOW_MS) -> list[int]:
    """각 신호 시각 t에서 `[t − window, t)` 안의 다른 신호 수(같은 ms는 세지 않는다)."""
    ordered = sorted(times)
    return [
        bisect.bisect_left(ordered, t) - bisect.bisect_left(ordered, t - window_ms) for t in times
    ]


def rule_decision(rules: RuleSet, count: int, btc_4h: float | None) -> float:
    """이 진입의 크기 배율. **0이면 건너뛴다**. 규칙이 다 꺼져 있으면 1이다."""
    if rules.skip_early and SKIP_LOW <= count <= SKIP_HIGH:
        return 0.0
    gated = rules.btc_gate is not None and count >= CROWD_MIN
    if gated and (btc_4h is None or math.isnan(btc_4h) or btc_4h > (rules.btc_gate or 0.0)):
        return 0.0
    if rules.budget is not None:
        return min(1.0, rules.budget / (count + 1))
    return 1.0


# ---------------------------------------------------------------------------
# 팔 후보 — 1분봉으로 청산만 다시 푼다(진입·손절 참조가는 base 후보 그대로)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmEntry:
    """팔 후보 하나 — 손절 배수와 무관한 부분(진입 · 신호 특징 · 보유 구간 1분봉)."""

    symbol: str
    timeframe: str
    cand: _Candidate
    btc_4h: float
    times: np.ndarray
    """체결 분부터 ts4 시간 청산 분까지(포함)의 1분봉 `open_time`."""
    lows: np.ndarray
    closes: np.ndarray

    @property
    def key(self) -> tuple[str, str, int, float]:
        return (
            self.symbol,
            self.timeframe,
            int(self.cand.entry_time),
            float(self.cand.entry_price),
        )


def arm_exit(
    entry: ArmEntry, stop_multiple: float, *, stop_fill: str = "stop"
) -> tuple[_Candidate, int]:
    """손절 배수를 적용해 청산을 푼다 → (후보, 청산 1분봉 오프셋). 손절 우선.

    `stop_fill` — `"stop"`(기본 · 손절가 체결) · `"bar_low"`(스트레스: 손절이 난 그 1분의 **저가**에
    체결 = 1분봉이 본 최악, WAN-276 α=1과 같은 자). 1분 안의 더 깊은 갭은 여전히 못 본다.

    `wan424.time_exit`과 같은 식이다(진입 봉 포함 HOLD개 · 그 뒤 새 봉이 시작되기 직전 1분 종가). 그
    등식은 주장이 아니라 검산이다 — k=1이 WAN-434 공개 거래 내역과 비트로 같아야 한다.
    """
    c = entry.cand
    e = float(c.entry_price)
    # k=1은 원래 손절가를 **그대로** 쓴다 — `e − (e − sp)`는 부동소수 끝자리가 달라 사이징이
    # 흔들린다.
    stop = (
        float(c.stop_price)
        if stop_multiple == 1.0
        else e - stop_multiple * (e - float(c.stop_price))
    )
    if stop_fill not in STOP_FILLS:
        raise ValueError(f"stop_fill은 {STOP_FILLS} 중 하나여야 한다: {stop_fill!r}")
    hit = np.flatnonzero(entry.lows <= stop)
    if len(hit):
        off = int(hit[0])
        px = stop if stop_fill == "stop" else min(stop, float(entry.lows[off]))
        reason = ExitReason.STOP_LOSS
    else:
        off = len(entry.closes) - 1
        px, reason = float(entry.closes[-1]), ExitReason.END_OF_DATA
    return (
        dataclasses.replace(
            c,
            stop_price=stop,
            exit_time=int(entry.times[off]),
            exit_price=px,
            reason=reason,
            take_profit_price=None,
            entry_liquidity=Liquidity.MAKER,
            mfe_r=None,
            mae_r=None,
        ),
        off,
    )


def hold_end(times_from_entry: np.ndarray, timeframe_ms: int, hold: int = HOLD) -> int:
    """체결 분(인덱스 0)부터 상위TF 봉 `hold`개를 채운 마지막 1분봉의 **다음** 인덱스."""
    buckets = times_from_entry // timeframe_ms
    new_bar = np.flatnonzero(np.diff(buckets) != 0)
    return int(new_bar[hold - 1]) + 1 if len(new_bar) >= hold else len(times_from_entry)


def build_entries(
    payloads: Sequence[CellPayload],
    *,
    store: OhlcvStore | None = None,
    progress: bool = False,
    start_ms: int | None = None,
    end_ms: int | None = None,
    k_threshold: float = K_THRESHOLD,
) -> list[ArmEntry]:
    """팔 후보(롱 · 첫 탭 · 재진입 아님 · 손절폭 ≥ 4% · 직전 확정봉 %K < 25)와 그 보유 구간
    1분봉.

    `start_ms`·`end_ms`·`k_threshold`(옵트인, WAN-438)는 1분봉 창과 %K 문턱이다 — 안 주면 채택 창 ·
    %K<25 그대로라 예전과 비트 동일하다(현물 루트 · 문턱 민감도 팔이 쓴다).
    """
    store = store or OhlcvStore(harness.DB_PATH)
    start = parse_date_ms(harness.DEFAULT_START) if start_ms is None else start_ms
    end = parse_date_ms(harness.DEFAULT_END) if end_ms is None else end_ms
    btc = _minute_frame(store, BTC_SYMBOL, start - DAY_MS, end)
    btc_t = btc["open_time"].to_numpy(np.int64)
    btc_c = btc["close"].to_numpy(float)
    by_symbol: dict[str, list[CellPayload]] = {}
    for p in payloads:
        by_symbol.setdefault(p.symbol, []).append(p)
    out: list[ArmEntry] = []
    for symbol, cells in by_symbol.items():
        frame = _minute_frame(store, symbol, start, end)
        ot = frame["open_time"].to_numpy(np.int64)
        lo = frame["low"].to_numpy(float)
        cl = frame["close"].to_numpy(float)
        for p in cells:
            tf_ms = timeframe_to_ms(p.timeframe)
            k_times, k_values = stoch_series(store, p.symbol, p.timeframe)
            for c in arm_pool(p.candidates.get(harness.SEGMENT_FULL, ()), min_width=FLOOR):
                kv = k_before(k_times, k_values, int(c.entry_time), tf_ms)
                if kv is None or kv >= k_threshold:
                    continue
                j = int(np.searchsorted(ot, c.entry_time))
                if j >= len(ot) or ot[j] != c.entry_time:
                    continue
                je = j + hold_end(ot[j:], tf_ms)
                out.append(
                    ArmEntry(
                        symbol=p.symbol,
                        timeframe=p.timeframe,
                        cand=c,
                        btc_4h=btc_return(btc_t, btc_c, int(c.entry_time)),
                        times=ot[j:je].copy(),
                        lows=lo[j:je].copy(),
                        closes=cl[j:je].copy(),
                    )
                )
        if progress:
            print(f"  팔 후보 {symbol}", flush=True)
    return out


def _minute_frame(store: OhlcvStore, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    frame = store.load(symbol, "1m", start_ms=start_ms, end_ms=end_ms)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    return frame.sort_values("open_time").reset_index(drop=True)


def btc_return(times: np.ndarray, closes: np.ndarray, entry_ms: int) -> float:
    """진입 **직전 확정 1분봉** 종가 대비 그보다 240분 앞 종가의 수익률(비는 분은 직전 값)."""
    j = int(np.searchsorted(times, entry_ms)) - 1
    i = int(np.searchsorted(times, int(times[j]) - BTC_LOOKBACK_MIN * MINUTE_MS, side="right")) - 1
    if j < 0 or i < 0:
        return math.nan
    return float(closes[j] / closes[i] - 1.0)


# ---------------------------------------------------------------------------
# 배치 — 규칙으로 거른 후보를 채택 북에 넣는다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlacedTrade:
    symbol: str
    timeframe: str
    entry_time: int
    exit_time: int
    net_r: float
    stopped: bool
    back: bool
    size: float
    """예산 배율(규칙이 꺼져 있으면 1)."""
    entry_price: float
    stop_price: float
    path_times: np.ndarray
    path_lows: np.ndarray
    path_closes: np.ndarray
    """체결 분부터 청산 **직전** 분까지 — 평가손 경로."""


def place(
    payloads: Sequence[CellPayload],
    entries: Sequence[ArmEntry],
    rules: RuleSet,
    *,
    counts: Sequence[int] | None = None,
    stop_fill: str = "stop",
) -> list[PlacedTrade]:
    """규칙 적용 → 칸마다 후보 → `wan424.place_segments`(채택 북) → `full` 거래.

    북 인자(익절 회계 `take_profit_liquidity` 포함)는 `place_segments` 한 곳에만 있다 — 여기서 다시
    쓰면 「같은 팔로 쟀다」가 거짓이 된다(WAN-95/112/123). 검산이 그 등식을 값으로 건다.
    """
    counts = (
        list(counts)
        if counts is not None
        else signal_counts([int(e.cand.entry_time) for e in entries])
    )
    kept: dict[tuple[str, str], list[_Candidate]] = {}
    meta: dict[tuple[str, str, int, float], tuple[ArmEntry, float, int]] = {}
    for entry, count in zip(entries, counts, strict=True):
        size = rule_decision(rules, count, entry.btc_4h)
        if size <= 0.0:
            continue
        cand, off = arm_exit(entry, rules.stop_multiple, stop_fill=stop_fill)
        kept.setdefault((entry.symbol, entry.timeframe), []).append(cand)
        meta[entry.key] = (entry, size, off)
    pl = [
        dataclasses.replace(
            p,
            candidates={
                harness.SEGMENT_FULL: tuple(kept.get((p.symbol, p.timeframe), ())),
                harness.SEGMENT_IS: (),
                harness.SEGMENT_OOS: (),
            },
            reentry_candidates={},
            arm_candidates={},
        )
        for p in payloads
    ]
    boundary = {(p.symbol, p.timeframe): p.boundary_ms for p in payloads}
    seg = next(s for s in place_segments(pl) if s.segment == harness.SEGMENT_FULL)
    out: list[PlacedTrade] = []
    for trade, placed in seg.trades_with_placements():
        symbol, timeframe = placed.cell
        entry, size, off = meta[
            (symbol, timeframe, int(trade.entry_time), float(trade.entry_price))
        ]
        out.append(
            PlacedTrade(
                symbol=symbol,
                timeframe=timeframe,
                entry_time=int(trade.entry_time),
                exit_time=int(trade.exit_time),
                net_r=net_r(trade, placed),
                stopped=trade.exits[-1].reason == ExitReason.STOP_LOSS,
                back=int(placed.trigger_time) >= boundary[placed.cell],
                size=size,
                entry_price=float(trade.entry_price),
                stop_price=float(placed.stop_price),
                path_times=entry.times[:off],
                path_lows=entry.lows[:off],
                path_closes=entry.closes[:off],
            )
        )
    return out


# ---------------------------------------------------------------------------
# 복리 자본 경로 + 1분 평가손
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimResult:
    trades: int
    total_return: float
    cagr: float
    mdd_low: float
    mdd_close: float
    worst_day: float
    """전날 마지막 평가액 대비 그날 최저 평가액(저가 기준) — UTC 일자."""
    mdd_peak_ms: int
    mdd_trough_ms: int
    cap_breaches: int
    """복리 사이징에서 열린 명목 합이 자본 × 5를 넘은 진입 수(북 배치는 복리 끔이라 못 본 자리)."""
    day_returns: dict[int, float]


def simulate(trades: Sequence[PlacedTrade], *, risk: float) -> SimResult:
    """진입 순간의 확정 자본 기준 복리 · 1분마다 저가·종가로 평가한 계좌 가치."""
    if not trades:
        return SimResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, {})
    start = min(t.entry_time for t in trades) // MINUTE_MS * MINUTE_MS
    end = max(t.exit_time for t in trades)
    n = (end - start) // MINUTE_MS + 2
    events: list[tuple[int, int, int]] = []
    for i, t in enumerate(trades):
        events.append((t.exit_time, 0, i))  # 같은 시각은 청산 먼저(엔진 규약)
        events.append((t.entry_time, 1, i))
    events.sort()
    eq = 1.0
    weight = [0.0] * len(trades)
    notional = [0.0] * len(trades)
    open_notional = 0.0
    breaches = 0
    for _tm, kind, i in events:
        t = trades[i]
        if kind == 0:
            eq += risk * weight[i] * t.net_r
            open_notional -= notional[i]
            continue
        weight[i] = eq * t.size
        notional[i] = risk * weight[i] * t.entry_price / (t.entry_price - t.stop_price)
        open_notional += notional[i]
        if open_notional > CAP_MULTIPLE * eq:
            breaches += 1
    realized = np.zeros(n)
    u_low = np.zeros(n)
    u_close = np.zeros(n)
    for i, t in enumerate(trades):
        realized[(t.exit_time - start) // MINUTE_MS] += risk * weight[i] * t.net_r
        if len(t.path_times):
            a = (int(t.path_times[0]) - start) // MINUTE_MS
            b = (t.exit_time - start) // MINUTE_MS
            if b > a:
                grid = np.arange(a, b)
                idx = np.searchsorted((t.path_times - start) // MINUTE_MS, grid, side="right") - 1
                scale = risk * weight[i] / (t.entry_price - t.stop_price)
                u_low[a:b] += scale * (t.path_lows[idx] - t.entry_price)
                u_close[a:b] += scale * (t.path_closes[idx] - t.entry_price)
    base = 1.0 + np.cumsum(realized)
    mtm_low = base + u_low
    mtm_close = base + u_close
    peak = np.maximum.accumulate(np.maximum(mtm_low, 1.0))
    dd = 1.0 - mtm_low / peak
    trough = int(np.argmax(dd))
    peak_i = int(np.argmax(mtm_low[: trough + 1]))
    peak_c = np.maximum.accumulate(np.maximum(mtm_close, 1.0))
    days = (np.arange(n) * MINUTE_MS + start) // DAY_MS
    frame = pd.DataFrame({"d": days, "v": mtm_low, "c": mtm_close})
    last = frame.groupby("d").c.last()
    prev = last.shift(1).fillna(1.0)
    worst = frame.groupby("d").v.min() / prev - 1.0
    day_ret = (last / prev - 1.0).to_dict()
    years = (end - start) / DAY_MS / 365.25
    final = float(mtm_close[-1])
    return SimResult(
        trades=len(trades),
        total_return=final - 1.0,
        # 연환산은 석 달 미만 구간에서는 뜻이 없다(지수가 폭주한다) — 지어내지 않고 nan.
        cagr=(max(final, 1e-12) ** (1.0 / years) - 1.0) if years >= 0.25 else math.nan,
        mdd_low=float(dd.max()),
        mdd_close=float((1.0 - mtm_close / peak_c).max()),
        worst_day=float(worst.min()),
        mdd_peak_ms=int(peak_i * MINUTE_MS + start),
        mdd_trough_ms=int(trough * MINUTE_MS + start),
        cap_breaches=breaches,
        day_returns={int(k): float(v) for k, v in day_ret.items()},
    )


def segment_trades(trades: Sequence[PlacedTrade], segment: str) -> list[PlacedTrade]:
    if segment == SEGMENT_FULL:
        return list(trades)
    back = segment == SEGMENT_BACK
    return [t for t in trades if t.back == back]


# ---------------------------------------------------------------------------
# 판정 — 착수 전 고정
# ---------------------------------------------------------------------------


def day_block_diff_se(
    proposed: Sequence[PlacedTrade],
    base: Sequence[PlacedTrade],
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """(크기 가중 거래당 net R 차, 날짜 블록 부트스트랩 표준편차) — 두 팔을 **같은 날짜
    표본**으로 뽑는다."""

    def by_day(ts: Sequence[PlacedTrade]) -> dict[int, tuple[float, float]]:
        acc: dict[int, list[float]] = {}
        for t in ts:
            a = acc.setdefault(t.entry_time // DAY_MS, [0.0, 0.0])
            a[0] += t.size * t.net_r
            a[1] += t.size
        return {d: (v[0], v[1]) for d, v in acc.items()}

    p, b = by_day(proposed), by_day(base)
    days = sorted(set(p) | set(b))

    def stat(sample: Sequence[int]) -> float:
        ps = sum(p.get(d, (0.0, 0.0))[0] for d in sample)
        pw = sum(p.get(d, (0.0, 0.0))[1] for d in sample)
        bs = sum(b.get(d, (0.0, 0.0))[0] for d in sample)
        bw = sum(b.get(d, (0.0, 0.0))[1] for d in sample)
        if pw <= 0 or bw <= 0:
            return math.nan
        return ps / pw - bs / bw

    point = stat(days)
    if len(days) < 2:
        return point, math.nan
    rng = random.Random(seed)
    vals = [stat([days[rng.randrange(len(days))] for _ in days]) for _ in range(draws)]
    vals = [v for v in vals if not math.isnan(v)]
    return point, float(np.std(vals, ddof=1)) if len(vals) > 1 else math.nan


def verdict(proposed_back: SimResult, base_back: SimResult, diff: float, diff_se: float) -> str:
    """판정 상수(이슈 §판정 상수 1)를 코드가 적용한다. 차이가 2σ 안이면 그 사실을 함께 적는다."""
    passes_mdd = proposed_back.mdd_low <= MDD_LIMIT
    beats = proposed_back.total_return > base_back.total_return
    head = (
        f"뒷구간 평가손 MDD {proposed_back.mdd_low:.1%}({'≤' if passes_mdd else '>'} "
        f"{MDD_LIMIT:.0%}) · "
        f"뒷구간 복리 {proposed_back.total_return:+.0%} 대 기준 팔 {base_back.total_return:+.0%}"
    )
    within = math.isnan(diff_se) or abs(diff) <= SIGN_SIGMA * diff_se
    band = SIGN_SIGMA * diff_se
    diff_text = f"거래당 크기 가중 net R 차 {diff:+.3f}R은 날짜 블록 2σ(±{band:.3f})"
    noise = (
        f" 🚨 단 {diff_text} **안**이라 오차와 구분되지 않는다."
        if within
        else f" {diff_text} 밖이다."
    )
    if passes_mdd and beats:
        return f"✅ **통과** — {head}.{noise}"
    return f"❌ **불통과** — {head}.{noise}"


# ---------------------------------------------------------------------------
# 검산 — 규칙을 전부 끈 판 ≡ WAN-434 공개 거래 내역
# ---------------------------------------------------------------------------


def checksum_wan434(
    trades: Sequence[PlacedTrade], *, reference: Path = WAN434_TRADES
) -> tuple[str, bool]:
    if not reference.exists():
        return f"⚠️ 기준 파일 없음: {reference}", False
    ref = pd.read_csv(reference)
    ref = ref[ref["조합"] == WAN434_COMBO]
    got_keys = sorted((t.symbol, t.timeframe, t.entry_time) for t in trades)
    want_keys = sorted(
        (str(s), str(tf), int(e))
        for s, tf, e in zip(ref["종목"], ref["시간봉"], ref["진입시각_ms"], strict=True)
    )
    got_sum = sum(t.net_r for t in trades)
    want_sum = float(ref["net_R"].sum())
    ok = got_keys == want_keys and abs(got_sum - want_sum) < CHECKSUM_TOL
    mark = "✅" if ok else "❌"
    return (
        f"{mark} 규칙 전부 끔(손절 1배) ≡ WAN-434 공개 거래 내역 — 거래 {len(trades)} 대 "
        f"{len(ref)} · "
        f"(종목·TF·진입 시각) 집합 {'일치' if got_keys == want_keys else '불일치'} · net R 합 "
        f"{got_sum:.6f} 대 {want_sum:.6f}(차 {abs(got_sum - want_sum):.1e})",
        ok,
    )


# ---------------------------------------------------------------------------
# 표
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    arm: str
    rules: str
    risk: float
    segment: str
    trades: int
    total_return: float
    cagr: float
    mdd_low: float
    mdd_close: float
    worst_day: float
    mdd_peak: str
    mdd_trough: str
    cap_breaches: int
    mean_net_r: float
    stop_rate: float


def _utc(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).strftime("%Y-%m-%d %H:%M")


def make_rows(arm: str, rules: RuleSet, risk: float, trades: Sequence[PlacedTrade]) -> list[Row]:
    rows = []
    for seg in (SEGMENT_FULL, SEGMENT_FRONT, SEGMENT_BACK):
        ts = segment_trades(trades, seg)
        sim = simulate(ts, risk=risk)
        w = sum(t.size for t in ts)
        rows.append(
            Row(
                arm=arm,
                rules=rules.label,
                risk=risk,
                segment=seg,
                trades=sim.trades,
                total_return=sim.total_return,
                cagr=sim.cagr,
                mdd_low=sim.mdd_low,
                mdd_close=sim.mdd_close,
                worst_day=sim.worst_day,
                mdd_peak=_utc(sim.mdd_peak_ms) if ts else "",
                mdd_trough=_utc(sim.mdd_trough_ms) if ts else "",
                cap_breaches=sim.cap_breaches,
                mean_net_r=sum(t.size * t.net_r for t in ts) / w if w else math.nan,
                stop_rate=sum(t.stopped for t in ts) / len(ts) if ts else math.nan,
            )
        )
    return rows


def monthly(arm: str, trades: Sequence[PlacedTrade]) -> pd.DataFrame:
    months = pd.period_range(harness.DEFAULT_START[:7], harness.DEFAULT_END[:7], freq="M")
    s = pd.Series([pd.Timestamp(t.entry_time, unit="ms").to_period("M") for t in trades])
    counts = s.value_counts().reindex(months, fill_value=0)
    return pd.DataFrame({"arm": arm, "month": months.astype(str), "trades": counts.to_numpy()})


@dataclass(frozen=True)
class MatchedRow:
    """경계 하나 — 6년 평가손 MDD(저가)를 `target`에 맞춘 크기와 그때의 구간별 결과."""

    gate: float | None
    risk: float
    trades: int
    full_return: float
    full_cagr: float
    full_mdd: float
    worst_day: float
    front_return: float
    front_mdd: float
    back_return: float
    back_mdd: float
    target: float = MDD_LIMIT


def risk_for_mdd(
    trades: Sequence[PlacedTrade],
    *,
    target: float = MDD_LIMIT,
    lo: float = MATCHED_RISK_RANGE[0],
    hi: float = MATCHED_RISK_RANGE[1],
    iterations: int = MATCHED_ITERATIONS,
) -> float:
    """6년(`full`) 평가손 MDD(저가)가 `target` 이하인 가장 큰 거래당 리스크(로그 이분 탐색)."""
    best = lo
    for _ in range(iterations):
        mid = math.sqrt(lo * hi)
        if simulate(trades, risk=mid).mdd_low <= target:
            best = lo = mid
        else:
            hi = mid
    return best


def matched_row(
    gate: float | None, trades: Sequence[PlacedTrade], target: float = MDD_LIMIT
) -> MatchedRow:
    risk = risk_for_mdd(trades, target=target)
    full = simulate(trades, risk=risk)
    front = simulate(segment_trades(trades, SEGMENT_FRONT), risk=risk)
    back = simulate(segment_trades(trades, SEGMENT_BACK), risk=risk)
    return MatchedRow(
        gate=gate,
        risk=risk,
        trades=full.trades,
        full_return=full.total_return,
        full_cagr=full.cagr,
        full_mdd=full.mdd_low,
        worst_day=full.worst_day,
        front_return=front.total_return,
        front_mdd=front.mdd_low,
        back_return=back.total_return,
        back_mdd=back.mdd_low,
        target=target,
    )


def render_matched(rows: Sequence[MatchedRow]) -> list[str]:
    lines: list[str] = []
    for target in sorted({r.target for r in rows}):
        lines += [
            f"## 경계별 — 6년 평가손 MDD {target:.0%}에 크기를 맞춘 비교 (관측 · 판정 아님)",
            "",
            "사용자 목표(평가손 MDD 한도 안에서 복리 최대)를 직접 묻는 자다. 🚨 **판정은 위 "
            "상수(1.5% 고정)가 낸다** — 이 표는 결과를 본 뒤 요청된 자라 판정 근거가 아니고, "
            "이 자를 판정으로 쓰려면 상수로 **새로 박은** 측정이 필요하다(WAN-161).",
            "",
            "| 몰림 중 BTC 4h 경계 | 거래당 | 거래 | 6년 복리 | 연환산 | 하루 최악 "
            "| 앞구간 복리 · MDD | 뒷구간 복리 · MDD |",
            "| -- | --: | --: | --: | --: | --: | --: | --: |",
        ]
        for r in (r for r in rows if r.target == target):
            label = "게이트 없음(기준 팔)" if r.gate is None else f"≤ {r.gate:+.2%}"
            lines.append(
                f"| {label} | {r.risk:.2%} | {r.trades} | {r.full_return:+.0%} "
                f"| {r.full_cagr:+.1%} | {r.worst_day:+.1%} "
                f"| {r.front_return:+.0%} · {r.front_mdd:.1%} "
                f"| {r.back_return:+.0%} · {r.back_mdd:.1%} |"
            )
        lines += [
            "",
            "⚠️ 크기는 6년 전체 MDD로 맞췄다 — 그 MDD는 전부 앞구간 급락일(2021-05-19)에서 "
            f"나므로 뒷구간 MDD는 {target:.0%}보다 작다. 앞·뒤는 각자 자본 1에서 다시 쌓은 값이다.",
            "",
        ]
    return lines


@dataclass(frozen=True)
class GridCell:
    """경계 × 거래당 리스크 한 칸."""

    gate: float | None
    risk: float
    trades: int
    full_return: float
    full_cagr: float
    full_mdd: float
    worst_day: float
    front_return: float
    front_mdd: float
    back_return: float
    back_mdd: float


def grid_cell(gate: float | None, risk: float, trades: Sequence[PlacedTrade]) -> GridCell:
    full = simulate(trades, risk=risk)
    front = simulate(segment_trades(trades, SEGMENT_FRONT), risk=risk)
    back = simulate(segment_trades(trades, SEGMENT_BACK), risk=risk)
    return GridCell(
        gate=gate,
        risk=risk,
        trades=full.trades,
        full_return=full.total_return,
        full_cagr=full.cagr,
        full_mdd=full.mdd_low,
        worst_day=full.worst_day,
        front_return=front.total_return,
        front_mdd=front.mdd_low,
        back_return=back.total_return,
        back_mdd=back.mdd_low,
    )


def _gate_label(gate: float | None) -> str:
    return "게이트 없음" if gate is None else f"≤ {gate:+.2%}"


def render_grid(cells: Sequence[GridCell]) -> list[str]:
    """두 행렬 — 6년(복리 수익 · 평가손 MDD)과 뒷구간(같은 크기로 자본 1에서 다시 쌓은 값)."""
    if not cells:
        return []
    gates = list(dict.fromkeys(c.gate for c in cells))
    risks = sorted({c.risk for c in cells})
    idx = {(c.gate, c.risk): c for c in cells}
    head = "| BTC 4h 경계 ＼ 거래당 | " + " | ".join(f"{r:.2%}" for r in risks) + " |"
    rule = "| -- | " + " | ".join("--:" for _ in risks) + " |"

    def table(pick: str) -> list[str]:
        out = [head, rule]
        for g in gates:
            vals = []
            for r in risks:
                c = idx[(g, r)]
                ret, mdd = (
                    (c.full_return, c.full_mdd) if pick == "full" else (c.back_return, c.back_mdd)
                )
                mark = "" if mdd <= MDD_LIMIT else " ⚠️"
                vals.append(f"{ret:+.0%} · {mdd:.0%}{mark}")
            out.append(f"| {_gate_label(g)} | " + " | ".join(vals) + " |")
        return out

    return [
        "## 경계 × 거래당 리스크 격자 (관측 · 판정 아님)",
        "",
        "칸 = 「복리 수익 · 평가손 MDD(저가)」 · ⚠️ = MDD 30% 초과. "
        "🚨 **판정은 위 상수(1.5% 고정)가 "
        "낸다** — 이 격자는 결과를 본 뒤 요청된 자라 판정 근거가 아니다(WAN-161).",
        "",
        "### 6년 전체",
        "",
        *table("full"),
        "",
        "### 뒷구간 (같은 크기 · 자본 1에서 다시 쌓음)",
        "",
        *table("back"),
        "",
        "⚠️ 6년 MDD는 거의 전부 앞구간 급락일(2021-05-19)에서 나므로 뒷구간 MDD가 더 작다. "
        "거래 수·앞구간"
        "·연환산·하루 최악은 `wan436_gate_x_risk_grid.csv`에 있다.",
        "",
    ]


@dataclass(frozen=True)
class StressRow:
    """손절 체결 스트레스 한 줄 — 팔 × 체결 가정 × 크기(`risk`가 nan이면 MDD 맞춤 줄)."""

    arm: str
    stop_fill: str
    label: str
    risk: float
    trades: int
    stopped: int
    stopped_mean_r: float
    full_return: float
    full_mdd: float
    worst_day: float
    back_return: float
    back_mdd: float


def stress_row(
    arm: str, stop_fill: str, label: str, risk: float, trades: Sequence[PlacedTrade]
) -> StressRow:
    full = simulate(trades, risk=risk)
    back = simulate(segment_trades(trades, SEGMENT_BACK), risk=risk)
    stops = [t.net_r for t in trades if t.stopped]
    return StressRow(
        arm=arm,
        stop_fill=stop_fill,
        label=label,
        risk=risk,
        trades=full.trades,
        stopped=len(stops),
        stopped_mean_r=float(np.mean(stops)) if stops else math.nan,
        full_return=full.total_return,
        full_mdd=full.mdd_low,
        worst_day=full.worst_day,
        back_return=back.total_return,
        back_mdd=back.mdd_low,
    )


def stress_rows(arms: Sequence[tuple[str, str, Sequence[PlacedTrade]]]) -> list[StressRow]:
    """(팔 이름, 체결 가정, 거래) 묶음마다 고정 크기 `STRESS_RISKS` + MDD 30%/35% 맞춤."""
    out: list[StressRow] = []
    for arm, fill, trades in arms:
        for risk in STRESS_RISKS:
            out.append(stress_row(arm, fill, f"거래당 {risk:.2%}", risk, trades))
        for target in MATCHED_TARGETS:
            risk = risk_for_mdd(trades, target=target)
            out.append(stress_row(arm, fill, f"MDD {target:.0%} 맞춤", risk, trades))
    return out


_FILL_LABEL = {"stop": "손절가 체결", "bar_low": "그 1분 저가 체결"}


def render_stress(rows: Sequence[StressRow]) -> list[str]:
    lines = [
        "## 손절 체결 스트레스 — 손절이 그 1분의 저가에 체결됐다면 (관측 · 판정 아님)",
        "",
        "기본은 손절가에 정확히 체결된다고 본다. 스트레스는 손절이 난 "
        "**그 1분의 저가**에 체결됐다고 본다 — 1분봉이 본 최악(WAN-276 α=1과 같은 자). "
        "🚨 1분 안의 더 깊은 갭·호가 공백은 여전히 못 본다(WAN-397/98). "
        "진입·손절선·배치는 두 가정이 같고 **손절 거래의 청산가만** 다르다.",
        "",
        "| 팔 | 체결 가정 | 크기 | 거래당 | 손절(건 · 평균 R) | 6년 복리 · MDD | 하루 최악 "
        "| 뒷구간 복리 · MDD |",
        "| -- | -- | -- | --: | --: | --: | --: | --: |",
    ]
    for r in rows:
        lines.append(
            f"| {r.arm} | {_FILL_LABEL[r.stop_fill]} | {r.label} | {r.risk:.2%} "
            f"| {r.stopped} · {r.stopped_mean_r:+.2f} | {r.full_return:+.0%} · {r.full_mdd:.1%} "
            f"| {r.worst_day:+.1%} | {r.back_return:+.0%} · {r.back_mdd:.1%} |"
        )
    lines.append("")
    return lines


def render(
    rows: Sequence[Row],
    checks: Sequence[str],
    verdict_line: str,
    month_frame: pd.DataFrame,
    elapsed: float | None,
    matched: Sequence[MatchedRow] = (),
    grid: Sequence[GridCell] = (),
    stress: Sequence[StressRow] = (),
    fill_lens: str = "pen_5bp",
) -> str:
    def fmt(r: Row) -> str:
        return (
            f"| {r.arm} | {r.segment} | {r.risk:.2%} | {r.trades} | {r.total_return:+.0%} | "
            f"{r.cagr:+.1%} "
            f"| {r.mdd_low:.1%} | {r.mdd_close:.1%} | {r.worst_day:+.1%} | {r.mean_net_r:+.3f} "
            f"| {r.stop_rate:.0%} | {r.cap_breaches} |"
        )

    head = (
        "| 팔 | 구간 | 거래당 | 거래 | 복리 수익 | 연환산 | 평가손 MDD(저가) | (종가) | 하루 최악 "
        "| 거래당 net R(크기 가중) | 손절률 | 5배 초과 진입 |\n"
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |"
    )
    main = [r for r in rows if r.arm in ("기준 팔", "제안 팔") and abs(r.risk - RISK) < 1e-12]
    neigh = [r for r in rows if r.arm.startswith("이웃")]
    off = [r for r in rows if r.arm == "규칙 끔"]
    mstat = month_frame.groupby("arm").trades.agg(["mean", "median", lambda s: int((s == 0).sum())])
    mstat.columns = ["월평균", "월 중앙값", "0건인 달"]
    lines = [
        "# WAN-436 — 스토캐스틱 팔 × 몰림 규칙 (숫자 고정 · 채택 북 · 평가손 MDD)",
        "",
        "롱 · 31종목 × 9TF(1h~1w) · 못 박은 6년 · 첫 탭 · 재진입 없음 · 손절폭 ≥ 4% · 직전 "
        "확정봉 %K<25 · "
        f"ts4 시간 청산 · `{fill_lens}` × 같은 분 익절 금지 · 채택 북 배치 · **진입 시점 자본 기준 "
        "복리** · "
        "**1분 평가손 포함 MDD** · 핀 없음.",
        "",
        f"* 기준 팔 = {BASE.label}",
        f"* 제안 팔 = {PROPOSED.label}",
        f"* 거래당 리스크 {RISK:.1%} · 신호 수 = 팔 후보 전체의 체결 시각(체결 여부 무관) · 창 "
        "`[t−24h, t)`",
        "",
        f"## 판정 — {verdict_line}",
        "",
        "상수(착수 전 고정): 뒷구간 평가손 MDD(저가) ≤ 30% **그리고** 뒷구간 복리 수익이 기준 "
        "팔보다 높다. "
        "차이가 날짜 블록 2σ 안이면 판정문이 그 사실을 적는다. 🚨 통과해도 채택이 아니다 — "
        "다음은 페이퍼 "
        "병행이고 채택은 사용자 결정이다.",
        "",
        "## 기준 팔 대 제안 팔 (완료기준 2)",
        "",
        head,
        *[fmt(r) for r in main],
        "",
        "MDD 구간(저가 기준 · UTC): "
        + " · ".join(
            f"{r.arm} {r.segment} {r.mdd_peak} → {r.mdd_trough}"
            for r in main
            if r.segment == SEGMENT_FULL
        ),
        "",
        "⚠️ 앞·뒤 구간은 `full` 한 지갑의 거래를 칸별 따뜻한 OOS 경계(탭 시각)로 나눠 **각자 "
        "자본 1에서** "
        "다시 쌓은 값이다(구간별 별도 배치가 아니다).",
        "",
        "## 월별 거래 수",
        "",
        "| 팔 | 월평균 | 월 중앙값 | 0건인 달 |",
        "| -- | --: | --: | --: |",
        *[
            f"| {arm} | {v['월평균']:.1f} | {v['월 중앙값']:.0f} | {int(v['0건인 달'])} |"
            for arm, v in mstat.iterrows()
        ],
        "",
        *(render_matched(matched) if matched else []),
        *render_grid(grid),
        *(render_stress(stress) if stress else []),
        "## 이웃 확인 (판정 아님)",
        "",
        head,
        *[fmt(r) for r in neigh],
        "",
        "## 검산 (완료기준 4)",
        "",
        *[f"- {c}" for c in checks],
        "",
        "규칙 끔 팔(참고 · 손절 1배 · 1.5%):",
        "",
        head,
        *[fmt(r) for r in off],
        "",
        "## 한계",
        "",
        "* 급락 순간 손절이 손절가보다 아래에서 체결되는 효과는 1분봉으로 못 잰다(WAN-397/98) — "
        "손절가 체결 가정.",
        "* 규칙의 **모양**은 WAN-434 탐색이 뒷구간까지 보고 골랐다(경계값만 앞구간) — 뒷구간 "
        "통과는 필요조건이다.",
        "* 명목 상한은 복리 끔 배치에서 걸렸다 — 복리 사이징에서 5배를 넘은 진입 수를 표에 싣는다.",
        f"* 전부 `{fill_lens}` 렌즈 · 채택 좌표가 아니다(채택 북 −0.12R과 나란히 놓지 말 것) · "
        "「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변(다른 질문).",
    ]
    if elapsed is not None:
        lines.append(f"\n실측 {elapsed:.0f}초({elapsed / 60:.0f}분).")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def output_paths(fill_lens: str) -> dict[str, Path]:
    """판정 좌표(`pen_5bp`)는 원래 이름 · 다른 렌즈는 이름에 렌즈를 붙여 **덮지 않는다**."""
    base = {
        "csv": CSV_PATH,
        "monthly": MONTHLY_PATH,
        "matched": MATCHED_PATH,
        "grid": GRID_PATH,
        "stress": STRESS_PATH,
        "summary": SUMMARY_PATH,
    }
    if fill_lens == "pen_5bp":
        return base
    return {k: p.with_name(f"{p.stem}_{fill_lens}{p.suffix}") for k, p in base.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument(
        "--fill",
        default="pen_5bp",
        help="체결 렌즈(기본 pen_5bp = 판정 좌표). baseline 등은 관측이고 "
        "산출물 이름에 렌즈가 붙는다"
        " · WAN-434 검산은 pen_5bp에서만 성립하므로 건너뛴다",
    )
    args = parser.parse_args(argv)
    started = time.monotonic()
    payloads = build_base_payloads(
        jobs=args.jobs, payload_dir=args.payload_dir, fill_lens=args.fill
    )
    print(f"base 후보 {time.monotonic() - started:.0f}s · 칸 {len(payloads)}", flush=True)
    entries = build_entries(payloads, progress=True)
    counts = signal_counts([int(e.cand.entry_time) for e in entries])
    print(f"팔 후보 {len(entries)}건 {time.monotonic() - started:.0f}s", flush=True)

    off_trades = place(payloads, entries, OFF, counts=counts)
    if args.fill == "pen_5bp":
        check_line, ok = checksum_wan434(off_trades)
    else:
        check_line, ok = (
            f"⏭️ WAN-434 검산 건너뜀 — 체결 렌즈 `{args.fill}`(공개 거래 내역은 `pen_5bp`)",
            True,
        )
    print(check_line, flush=True)

    arms: list[tuple[str, RuleSet, float]] = [
        ("규칙 끔", OFF, RISK),
        ("기준 팔", BASE, RISK),
        ("제안 팔", PROPOSED, RISK),
    ]
    arms += [
        (f"이웃 · BTC {g:+.1%}", dataclasses.replace(PROPOSED, btc_gate=g), RISK)
        for g in NEIGHBOR_GATES
    ]
    arms += [(f"이웃 · 거래당 {r:.2%}", PROPOSED, r) for r in NEIGHBOR_RISKS]
    placed: dict[RuleSet, list[PlacedTrade]] = {OFF: off_trades}
    rows: list[Row] = []
    months = []
    for name, rules, risk in arms:
        trades = placed.get(rules)
        if trades is None:
            trades = placed[rules] = place(payloads, entries, rules, counts=counts)
        rows.extend(make_rows(name, rules, risk, trades))
        if name in ("기준 팔", "제안 팔"):
            months.append(monthly(name, trades))
        print(f"  {name} 완료 {time.monotonic() - started:.0f}s", flush=True)
    p_back = simulate(segment_trades(placed[PROPOSED], SEGMENT_BACK), risk=RISK)
    b_back = simulate(segment_trades(placed[BASE], SEGMENT_BACK), risk=RISK)
    diff, se = day_block_diff_se(
        segment_trades(placed[PROPOSED], SEGMENT_BACK), segment_trades(placed[BASE], SEGMENT_BACK)
    )
    line = verdict(p_back, b_back, diff, se)
    matched: list[MatchedRow] = []
    for gate in MATCHED_GATES:
        rules = BASE if gate is None else dataclasses.replace(PROPOSED, btc_gate=gate)
        trades = placed.get(rules)
        if trades is None:
            trades = placed[rules] = place(payloads, entries, rules, counts=counts)
    for target in MATCHED_TARGETS:
        for gate in MATCHED_GATES:
            rules = BASE if gate is None else dataclasses.replace(PROPOSED, btc_gate=gate)
            matched.append(matched_row(gate, placed[rules], target=target))
        print(f"  MDD {target:.0%} 맞춤 완료 {time.monotonic() - started:.0f}s", flush=True)
    grid = [
        grid_cell(
            gate,
            risk,
            placed[BASE if gate is None else dataclasses.replace(PROPOSED, btc_gate=gate)],
        )
        for gate in MATCHED_GATES
        for risk in GRID_RISKS
    ]
    print(f"  격자 {len(grid)}칸 완료 {time.monotonic() - started:.0f}s", flush=True)
    gate_rules = dataclasses.replace(PROPOSED, btc_gate=STRESS_GATE)
    stress_arms = [
        ("기준 팔", "stop", placed[BASE]),
        ("기준 팔", "bar_low", place(payloads, entries, BASE, counts=counts, stop_fill="bar_low")),
        (f"≤ {STRESS_GATE:+.1%}", "stop", placed[gate_rules]),
        (
            f"≤ {STRESS_GATE:+.1%}",
            "bar_low",
            place(payloads, entries, gate_rules, counts=counts, stop_fill="bar_low"),
        ),
    ]
    stress = stress_rows(stress_arms)
    print(f"  스트레스 완료 {time.monotonic() - started:.0f}s", flush=True)
    elapsed = time.monotonic() - started
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    paths = output_paths(args.fill)
    pd.DataFrame([dataclasses.asdict(r) for r in rows]).to_csv(paths["csv"], index=False)
    month_frame = pd.concat(months, ignore_index=True)
    month_frame.to_csv(paths["monthly"], index=False)
    pd.DataFrame([dataclasses.asdict(r) for r in matched]).to_csv(paths["matched"], index=False)
    pd.DataFrame([dataclasses.asdict(c) for c in grid]).to_csv(paths["grid"], index=False)
    pd.DataFrame([dataclasses.asdict(r) for r in stress]).to_csv(paths["stress"], index=False)
    summary = render(
        rows, [check_line], line, month_frame, elapsed, matched, grid, stress, args.fill
    )
    paths["summary"].write_text(summary, encoding="utf-8")
    print(summary)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
