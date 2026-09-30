"""WAN-442: 스토캐스틱 팔 종목 갈아끼우기 — 모멘텀 정의 × 기간 사전 고정 · 무작위 교체 대조.

## 왜 이 모듈이 있나

WAN-440에서 **어떤 31종목이냐에 따라 연 5~30%로 흔들린다**는 것이 나왔고, 그 뒤 탐색(12가지 설정 ·
채택 근거 아님 · `docs/decisions/wan440_scratch/`)에서 「90일 수익률 상위 31종목으로 갈아끼우기」가
하위 대조를 이겼다. 이 모듈은 그 방향을 **착수 전에 숫자를 못 박고** 한 번 잰다 — 정의 5가지 ·
기간 6판을 **앞구간에서만** 고르고, 고른 한 판을 **뒷구간에서 한 번** 「매주 무작위 31종목 교체」
20회와 견준다(WAN-161).

## 무엇을 고정했나 (착수 전 · 코드 상수 — 결과를 보고 옮기지 않는다)

* **토대 그대로**: B + 15m(WAN-439 §8) — 페이퍼 좌표(`wan438.GATED`) + 폭락 국면 ×0.5 + 15m ·
  채택 북 배치 · 몰림 규칙 숫자(신호 1~10 건너뜀 · 예산 10)는 그대로 두고 **목록 안에서** 센다.
  시장(팔 후보 · BTC 특징)은 `wan440_universe.build_markets` 한 경로로 만든다(두 벌 금지).
* **풀**: 규칙 53종목(WAN-440 · 상폐 포함). 현물 창 밖은 그중 현물 데이터가 있는 종목.
* **리밸런싱**: 월요일 00:00 UTC부터 `REBALANCE_DAYS`(7)일마다. 시각 t의 점수는 **t 이전에 닫힌
  일봉**(`open_time + 1일 ≤ t`)만 본다. 편입 자격: 직전 확정 일봉이 `FRESH_DAYS`(3)일 안(상폐 ·
  거래 정지 종목이 옛 점수로 자리를 차지하지 않게) · 첫 일봉부터 `MIN_AGE_DAYS`(90)일 이상 · 그
  정의에 필요한 과거가 있을 것. 상위 `TOP_N`(31) — 자격 종목이 그보다 적으면 전부.
* **정의 5가지**(`DEFINITIONS`, 1단계는 기간 90일 · 간격 7일 고정): 단순 수익률 · 최근 7일 제외
  수익률 · 변동성 대비(수익률 ÷ 기간 일간 수익률 표준편차) · 추세의 질(로그 가격 회귀 기울기 × R²) ·
  거래량 가중(수익률 × 직전 30일 평균 거래대금 ÷ 직전 180일 평균 · 거래대금 = 일봉 거래량 × 종가).
* **기간 6판**(`STAGE2_LOOKBACKS` + 순위 평균 `RANK_AVG_LOOKBACKS`) — 1단계 1등 정의로.
* **앞/뒤**: 거래는 칸별 경계(BTC 칸 = 2024-08-08~09 UTC, WAN-166 · 트리거 시각 기준)로 나뉜다.
  **앞구간 = 현물 창 밖 + 선물 경계 이전**을 이어 붙인 경로.
* **1등 고르기**: 앞구간 경로를 미끄러짐(손절 = 그 1분 저가) 평가손 MDD 35%에 맞춘 크기에서 앞구간
  연환산이 가장 높은 판. 동점(차 ≤ `TIE_TOL`)이면 정의 · 기간 목록의 앞쪽.
* **판정**(`verdict`): 고른 한 판의 **뒷구간 수익/MDD**(미끄러짐 · **앞구간 맞춤 크기** — 크기도
  앞구간 정보만으로 정한다)가 무작위 교체 20회(각자 자기 앞구간 맞춤 크기) 중 **그 값 이상인 판이
  `PASS_MAX_BEATEN`(1)개 이하** = 21판 중 상위 2개 안이면 통과.
* **목록에서 빠질 때**: 기본 「새 진입만 막기」 · 대조 「강제 청산」(빠지는 리밸런싱 시각 직전 1분
  종가 · 시간 청산과 같은 테이커 회계 · 북 배치를 그대로 탄다) — 두 판 각각 1단계 → 2단계 → 판정을
  따로 낸다.
* **대조군 셋**(같은 실행): 무작위 교체 20회(시드 `RANDOM_SEED + 판 번호` · 자격 = 단순 90일 정의와
  같은 자격 풀) · 고른 판의 하위 31종목 · 손으로 고른 31종목 고정(참고 · WAN-440 재산출을 대신한다).
* **자**: 8.6년 이어 붙인 경로(미끄러짐 MDD 35% 맞춤 · 같은 크기 손절가 · 거래당 3% 고정) — 표용.

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 핀 없음 WAN-305 · WAN-443 이후 정렬) · 페이퍼 반영(WAN-441)은
**사용자 결정** · 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import math
import pickle
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan438_spot_stress as m438
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest.wan424_stoch_ob_arm import SYMBOLS as SYMBOLS_31
from data import spot_klines as sk
from data.storage import OhlcvStore

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
CSV_PATH = REPORT_DIR / "wan442_momentum_rotation.csv"
PICKS_PATH = REPORT_DIR / "wan442_momentum_picks.csv"
SUMMARY_PATH = REPORT_DIR / "wan442_momentum_rotation_summary.md"
MARKETS_PATH = REPO_ROOT / "backtest" / "cache" / "wan442_markets.pkl"

DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS

# --- 착수 전 고정 ---------------------------------------------------------------------------
TOP_N = 31
REBALANCE_DAYS = 7
"""리밸런싱 간격(일) — 월요일 00:00 UTC 격자."""
MIN_AGE_DAYS = 90
FRESH_DAYS = 3
"""직전 확정 일봉이 이 일수 안에 있어야 편입 자격(상폐 · 정지 종목 배제)."""
STAGE1_LOOKBACK = 90
SKIP_DAYS = 7
VOL_RECENT_DAYS = 30
VOL_BASE_DAYS = 180

DEF_SIMPLE = "단순 수익률"
DEF_SKIP = "최근 7일 제외"
DEF_VOLADJ = "변동성 대비"
DEF_TREND = "추세의 질"
DEF_VOLUME = "거래량 가중"
DEFINITIONS: tuple[str, ...] = (DEF_SIMPLE, DEF_SKIP, DEF_VOLADJ, DEF_TREND, DEF_VOLUME)
STAGE2_LOOKBACKS: tuple[int, ...] = (30, 60, 90, 120, 180)
RANK_AVG_LOOKBACKS: tuple[int, ...] = (30, 90, 180)

RANDOM_DRAWS = 20
RANDOM_SEED = 442
PASS_MAX_BEATEN = 1
"""무작위 교체 20회 중 뒷구간 수익/MDD가 고른 판 **이상**인 판이 이 수 이하면 통과
(21판 중 상위 2)."""
TIE_TOL = 1e-12
FIXED_RISK = 0.03

MODE_DROP = "새 진입만 막기"
MODE_FORCE = "강제 청산"
MODES: tuple[str, ...] = (MODE_DROP, MODE_FORCE)

HAND31 = "손으로 고른 31종목(고정)"
SIZE_FULL = "① 미끄러짐 · MDD 35% · 8.6년 맞춤"
SIZE_FIXED = "고정 3%"


# ---------------------------------------------------------------------------
# 일봉 · 점수 — 순수 함수 (t 이전에 닫힌 일봉만)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Daily:
    open_time: np.ndarray
    close: np.ndarray
    quote: np.ndarray
    """거래대금 = 거래량 × 종가."""


def last_closed(d: Daily, t: int) -> int:
    """시각 t에 이미 닫힌 마지막 일봉의 인덱스(`open_time + 1일 ≤ t`) · 없으면 −1."""
    return int(np.searchsorted(d.open_time, t - DAY_MS, side="right")) - 1


def _at_or_before(d: Daily, ms: int) -> int:
    return int(np.searchsorted(d.open_time, ms, side="right")) - 1


def eligible(d: Daily, t: int) -> int | None:
    """편입 자격이 있으면 직전 확정 일봉 인덱스, 없으면 None."""
    j = last_closed(d, t)
    if j < 0:
        return None
    if int(d.open_time[j]) < t - FRESH_DAYS * DAY_MS:
        return None
    if int(d.open_time[j]) - int(d.open_time[0]) < MIN_AGE_DAYS * DAY_MS:
        return None
    return j


def score(defn: str, d: Daily, j: int, look: int) -> float | None:
    """정의 `defn` · 기간 `look`일의 점수(클수록 강함). 계산할 과거가 없으면 None."""
    end = int(d.open_time[j])
    i = _at_or_before(d, end - look * DAY_MS)
    if i < 0 or i >= j:
        return None
    cl = d.close
    if defn == DEF_SIMPLE:
        return float(cl[j] / cl[i] - 1.0)
    if defn == DEF_SKIP:
        k = _at_or_before(d, end - SKIP_DAYS * DAY_MS)
        if k <= i:
            return None
        return float(cl[k] / cl[i] - 1.0)
    if defn == DEF_VOLADJ:
        rets = np.diff(cl[i : j + 1]) / cl[i:j]
        if len(rets) < 2:
            return None
        sd = float(np.std(rets, ddof=1))
        return float(cl[j] / cl[i] - 1.0) / sd if sd > 0.0 else None
    if defn == DEF_TREND:
        x = (d.open_time[i : j + 1] - d.open_time[i]) / DAY_MS
        y = np.log(cl[i : j + 1])
        if len(x) < 3:
            return None
        xm, ym = float(x.mean()), float(y.mean())
        sxx = float(((x - xm) ** 2).sum())
        syy = float(((y - ym) ** 2).sum())
        if sxx <= 0.0:
            return None
        slope = float(((x - xm) * (y - ym)).sum()) / sxx
        r2 = 1.0 if syy <= 0.0 else (slope * slope * sxx) / syy
        return slope * r2
    if defn == DEF_VOLUME:
        recent = d.quote[_at_or_before(d, end - VOL_RECENT_DAYS * DAY_MS) + 1 : j + 1]
        base = d.quote[_at_or_before(d, end - VOL_BASE_DAYS * DAY_MS) + 1 : j + 1]
        if len(recent) == 0 or len(base) == 0 or float(base.mean()) <= 0.0:
            return None
        return float(cl[j] / cl[i] - 1.0) * float(recent.mean()) / float(base.mean())
    raise ValueError(f"모르는 정의: {defn!r}")


def rebalance_times(start_ms: int, end_ms: int, days: int = REBALANCE_DAYS) -> list[int]:
    """`start_ms` 이하의 월요일 00:00 UTC부터 `days`일 간격(1970-01-01은 목요일)."""
    t = start_ms - ((start_ms - 4 * DAY_MS) % WEEK_MS)
    out: list[int] = []
    while t < end_ms:
        out.append(t)
        t += days * DAY_MS
    return out


def select(
    daily: Mapping[str, Daily],
    t: int,
    defn: str,
    lookbacks: Sequence[int],
    *,
    top: bool = True,
    n: int = TOP_N,
) -> frozenset[str]:
    """시각 t의 목록 — 기간마다 순위(동점은 심볼 이름순)를 매겨 평균 순위가 앞선 n종목.

    기간이 하나면 그 점수 순이다. 여러 기간이면 **모든 기간에서 점수가 있는 종목**만 순위를 받는다.
    """
    scores: dict[str, list[float]] = {}
    for sym, d in daily.items():
        j = eligible(d, t)
        if j is None:
            continue
        vals = [score(defn, d, j, look) for look in lookbacks]
        if any(v is None for v in vals):
            continue
        scores[sym] = [float(v) for v in vals if v is not None]
    if not scores:
        return frozenset()
    rank: dict[str, float] = dict.fromkeys(scores, 0.0)
    for k in range(len(lookbacks)):
        order = sorted(scores, key=lambda s: (-scores[s][k] if top else scores[s][k], s))
        for pos, s in enumerate(order):
            rank[s] += pos
    chosen = sorted(scores, key=lambda s: (rank[s], s))[:n]
    return frozenset(chosen)


def random_members(
    daily: Mapping[str, Daily], times: Sequence[int], seed: int, n: int = TOP_N
) -> list[frozenset[str]]:
    """매 리밸런싱 자격 풀(단순 90일 정의와 같은 자격)에서 n종목을 무작위로(시드 고정)."""
    rng = random.Random(seed)
    out: list[frozenset[str]] = []
    for t in times:
        pool = sorted(
            s
            for s, d in daily.items()
            if (j := eligible(d, t)) is not None
            and score(DEF_SIMPLE, d, j, STAGE1_LOOKBACK) is not None
        )
        out.append(frozenset(rng.sample(pool, min(n, len(pool)))))
    return out


# ---------------------------------------------------------------------------
# 목록을 시장에 적용 — 진입 시각의 목록 · 강제 청산은 보유 구간을 자른다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rotation:
    times: tuple[int, ...]
    members: tuple[frozenset[str], ...]

    def index_at(self, t: int) -> int:
        return bisect.bisect_right(self.times, t) - 1


def apply_rotation(mk: c.Market, rot: Rotation, *, force_close: bool) -> tuple[c.Market, int]:
    """진입 시각의 목록에 있는 후보만 남긴다 → (시장, 강제 청산으로 자른 후보 수).

    몰림 신호 수는 **남은 후보로 다시** 센다. `force_close`면 보유 중에 목록에서 빠지는 첫 리밸런싱
    시각 r 이전 분까지만 보유 경로를 남긴다 — 청산은 `arm_exit`이 그 마지막 1분 종가로 낸다(시간
    청산과 같은 사유 · 같은 테이커 회계). 손절이 r 이전이면 결과가 같다.
    """
    keep: list[int] = []
    entries: list[w.ArmEntry] = []
    cut = 0
    for k, e in enumerate(mk.entries):
        t = int(e.cand.entry_time)
        i = rot.index_at(t)
        if i < 0 or e.symbol not in rot.members[i]:
            continue
        if force_close:
            last = int(e.times[-1])
            m = i + 1
            while m < len(rot.times) and rot.times[m] <= last:
                if e.symbol not in rot.members[m]:
                    n = int(np.searchsorted(e.times, rot.times[m]))
                    if n < 1:
                        raise AssertionError("강제 청산 시각이 진입 분보다 앞선다")
                    e = dataclasses.replace(
                        e, times=e.times[:n], lows=e.lows[:n], closes=e.closes[:n]
                    )
                    cut += 1
                    break
                m += 1
        keep.append(k)
        entries.append(e)
    return (
        c.Market(
            mk.name,
            mk.payloads,
            entries,
            w.signal_counts([int(e.cand.entry_time) for e in entries]),
            [mk.btc_24h[k] for k in keep],
            [mk.vol_ratio[k] for k in keep],
        ),
        cut,
    )


# ---------------------------------------------------------------------------
# 평가 — 한 판(설정 × 목록에서 빠질 때 처리)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pick:
    mode: str
    config: str
    stage: str
    trades: int
    forced: int
    front_risk: float
    front_cagr: float
    back_ratio: float
    back_return: float
    back_mdd: float
    back_cagr: float
    back_stop_ratio: float
    full_risk: float
    full_cagr: float
    full_stop_cagr: float
    fixed_cagr: float
    fixed_mdd: float


def _fit(spot: Sequence[w.PlacedTrade], fut: Sequence[w.PlacedTrade]) -> float:
    def mdd_at(r: float) -> float:
        return c.chain(spot, fut, r)[1]

    return c.fit_risk(mdd_at, target=c.MDD_TARGET)


def evaluate(
    mode: str,
    config: str,
    stage: str,
    spot_mk: c.Market,
    fut_mk: c.Market,
    boundary_ms: int,
    forced: int = 0,
) -> tuple[Pick, list[c.Row]]:
    placed = {
        fill: (
            c.place_arm(spot_mk, c.ARM_B, fill, in_book=True),
            c.place_arm(fut_mk, c.ARM_B, fill, in_book=True),
        )
        for fill in c.FILLS
    }
    s, f = placed["bar_low"]
    s_stop, f_stop = placed["stop"]
    front = [t for t in f if not t.back]
    back = [t for t in f if t.back]
    back_stop = [t for t in f_stop if t.back]
    w0, f1 = u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END)
    r_front = _fit(s, front)
    front_total, _ = c.chain(s, front, r_front)
    sim_back = w.simulate(back, risk=r_front)
    sim_back_stop = w.simulate(back_stop, risk=r_front)
    r_full = _fit(s, f)
    full_total, _ = c.chain(s, f, r_full)
    stop_total, _ = c.chain(s_stop, f_stop, r_full)
    fixed_total, fixed_mdd = c.chain(s, f, FIXED_RISK)
    years = c._years(w0, f1)
    arm = f"{mode} · {config}"
    rows: list[c.Row] = []
    for basis, risk in ((SIZE_FULL, r_full), (SIZE_FIXED, FIXED_RISK)):
        for fill in c.FILLS:
            ps, pf = placed[fill]
            rows += c.segment_rows(arm, True, fill, basis, risk, ps, pf, boundary_ms, set())
    pick = Pick(
        mode=mode,
        config=config,
        stage=stage,
        trades=len(s) + len(f),
        forced=forced,
        front_risk=r_front,
        front_cagr=c.cagr(front_total, c._years(w0, boundary_ms)),
        back_ratio=_ratio(sim_back),
        back_return=sim_back.total_return,
        back_mdd=sim_back.mdd_low,
        back_cagr=c.cagr(sim_back.total_return, c._years(boundary_ms, f1)),
        back_stop_ratio=_ratio(sim_back_stop),
        full_risk=r_full,
        full_cagr=c.cagr(full_total, years),
        full_stop_cagr=c.cagr(stop_total, years),
        fixed_cagr=c.cagr(fixed_total, years),
        fixed_mdd=fixed_mdd,
    )
    return pick, rows


def _ratio(sim: w.SimResult) -> float:
    return sim.total_return / sim.mdd_low if sim.mdd_low > 0 else math.nan


def best(picks: Sequence[Pick]) -> Pick:
    """앞구간 연환산 최대 · 동점(차 ≤ TIE_TOL)이면 목록 앞쪽."""
    top = picks[0]
    for p in picks[1:]:
        if p.front_cagr > top.front_cagr + TIE_TOL:
            top = p
    return top


def verdict(chosen: Pick, randoms: Sequence[Pick]) -> tuple[bool, int]:
    """(통과 여부, 뒷구간 수익/MDD가 고른 판 이상인 무작위 판 수)."""
    if len(randoms) != RANDOM_DRAWS:
        raise ValueError(f"무작위 판이 {RANDOM_DRAWS}개여야 한다: {len(randoms)}")
    beaten = sum(r.back_ratio >= chosen.back_ratio for r in randoms)
    return beaten <= PASS_MAX_BEATEN, beaten


def config_label(defn: str, lookbacks: Sequence[int], *, top: bool = True) -> str:
    look = "·".join(str(x) for x in lookbacks) + "일" + (" 순위평균" if len(lookbacks) > 1 else "")
    return f"{defn} {look}" + ("" if top else " 하위(대조)")


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def load_daily(
    stores: Sequence[OhlcvStore], symbols: Sequence[str], start_ms: int, end_ms: int
) -> dict[str, Daily]:
    out: dict[str, Daily] = {}
    for s in symbols:
        for st in stores:
            f = st.load(s, "1d", start_ms=start_ms, end_ms=end_ms)
            if "closed" in f.columns:
                f = f[f["closed"].astype(bool)]
            if len(f):
                f = f.sort_values("open_time")
                close = f["close"].to_numpy(float)
                out[s] = Daily(
                    f["open_time"].to_numpy(np.int64),
                    close,
                    f["volume"].to_numpy(float) * close,
                )
                break
    return out


@dataclass
class Run:
    picks: list[Pick]
    rows: list[c.Row]
    notes: list[str]


def run(built: u.Markets) -> Run:
    c.assert_adopted_take_profit_liquidity()
    t0 = time.monotonic()
    spot, fut, boundary_ms = built.spot, built.fut, built.boundary_ms
    notes = list(built.notes)
    pool = set(built.rule_store)
    spot_syms = {p.symbol for p in spot.payloads}
    fut_d = load_daily(
        [OhlcvStore(harness.DB_PATH), OhlcvStore(sk.root_db_path(u.FUT_ROOT))],
        sorted(pool),
        u.ms("2019-06-01"),
        u.ms(harness.DEFAULT_END),
    )
    stress = m438.DEFAULT_ROOTS / "stress"
    spot_d = load_daily(
        [OhlcvStore(sk.root_db_path(stress)), OhlcvStore(sk.root_db_path(u.SPOT_ROOT))],
        sorted(pool & spot_syms),
        u.ms("2017-01-01"),
        u.ms(m438.STRESS_END),
    )
    fut_times = rebalance_times(u.ms(harness.DEFAULT_START), u.ms(harness.DEFAULT_END))
    spot_times = rebalance_times(u.ms(c.WINDOW_START), u.ms(m438.STRESS_END))
    notes.append(
        f"일봉 — 선물 {len(fut_d)}종목 · 현물 {len(spot_d)}종목 · 리밸런싱 선물 "
        f"{len(fut_times)}회 · 현물 {len(spot_times)}회 · 앞/뒤 경계(대표) {m438._utc(boundary_ms)}"
    )
    # 풀 밖 종목(UNI · AVAX)은 모멘텀 · 무작위 목록에 오를 수 없다 — 시장도 풀로 먼저 좁힌다.
    spot_pool = u.restrict(spot, pool & spot_syms)
    fut_pool = u.restrict(fut, pool)

    def rotations(
        member_fn: Callable[[Mapping[str, Daily], int], frozenset[str]],
    ) -> tuple[Rotation, Rotation]:
        return (
            Rotation(tuple(spot_times), tuple(member_fn(spot_d, t) for t in spot_times)),
            Rotation(tuple(fut_times), tuple(member_fn(fut_d, t) for t in fut_times)),
        )

    cache: dict[str, tuple[Rotation, Rotation]] = {}

    def momentum(defn: str, looks: Sequence[int], top: bool = True) -> tuple[Rotation, Rotation]:
        key = config_label(defn, looks, top=top)
        if key not in cache:
            cache[key] = rotations(lambda d, t: select(d, t, defn, looks, top=top))
        return cache[key]

    picks: list[Pick] = []
    rows: list[c.Row] = []

    def eval_rot(mode: str, config: str, stage: str, rot: tuple[Rotation, Rotation]) -> Pick:
        force = mode == MODE_FORCE
        s_mk, cut_s = apply_rotation(spot_pool, rot[0], force_close=force)
        f_mk, cut_f = apply_rotation(fut_pool, rot[1], force_close=force)
        pick, rr = evaluate(mode, config, stage, s_mk, f_mk, boundary_ms, cut_s + cut_f)
        picks.append(pick)
        rows.extend(rr)
        return pick

    # 손으로 고른 31종목 고정 — 목록이 안 바뀌어 두 처리가 같다. 검산(옛 정렬 ≡ WAN-440)도 여기서.
    hand_s = u.restrict(spot, set(SYMBOLS_31) & spot_syms)
    hand_f = u.restrict(fut, set(SYMBOLS_31))
    notes.append(checksum_hand31(hand_s, hand_f))
    for mode in MODES:
        pick, rr = evaluate(mode, HAND31, "대조", hand_s, hand_f, boundary_ms)
        picks.append(pick)
        rows.extend(rr)

    random_rots = [
        (
            Rotation(tuple(spot_times), tuple(random_members(spot_d, spot_times, RANDOM_SEED + k))),
            Rotation(tuple(fut_times), tuple(random_members(fut_d, fut_times, RANDOM_SEED + k))),
        )
        for k in range(RANDOM_DRAWS)
    ]
    for mode in MODES:
        stage1 = [
            eval_rot(
                mode, config_label(d, (STAGE1_LOOKBACK,)), "1단계", momentum(d, (STAGE1_LOOKBACK,))
            )
            for d in DEFINITIONS
        ]
        win1 = best(stage1)
        defn = next(d for d in DEFINITIONS if win1.config == config_label(d, (STAGE1_LOOKBACK,)))
        stage2: list[Pick] = []
        for looks in (*((x,) for x in STAGE2_LOOKBACKS), RANK_AVG_LOOKBACKS):
            label = config_label(defn, looks)
            same = next((p for p in stage1 if p.config == label), None)
            if same is not None:
                stage2.append(dataclasses.replace(same, stage="2단계"))
                picks.append(stage2[-1])
                continue
            stage2.append(eval_rot(mode, label, "2단계", momentum(defn, looks)))
        chosen = best(stage2)
        looks_chosen = next(
            lk
            for lk in (*((x,) for x in STAGE2_LOOKBACKS), RANK_AVG_LOOKBACKS)
            if config_label(defn, lk) == chosen.config
        )
        eval_rot(
            mode,
            config_label(defn, looks_chosen, top=False),
            "대조",
            momentum(defn, looks_chosen, top=False),
        )
        for k, rot in enumerate(random_rots):
            eval_rot(mode, f"무작위 교체 #{k:02d}", "무작위", rot)
        notes.append(f"{mode}: 끝 ({time.monotonic() - t0:.0f}초)")
    for key, (rs, rf) in cache.items():
        notes.append(
            f"목록 {key}: 선물 주당 평균 교체 {_turnover(rf):.1f}종목 · 현물 {_turnover(rs):.1f}"
        )
    notes.append(f"무작위 교체: 선물 주당 평균 교체 {_turnover(random_rots[0][1]):.1f}종목(#00)")
    notes.append(f"총 {time.monotonic() - t0:.0f}초")
    return Run(picks, rows, notes)


def _turnover(rot: Rotation) -> float:
    changes = [len(a - b) for a, b in zip(rot.members[1:], rot.members[:-1], strict=True)]
    return float(np.mean(changes)) if changes else 0.0


def checksum_hand31(spot: c.Market, fut: c.Market) -> str:
    """손으로 고른 31종목 · B+15m · ① 맞춤 크기(**WAN-443 이전 정렬**) ≡ WAN-440 공개 CSV.

    배선(시장 · 배치)이 WAN-440과 같은지 보는 검산이라 옛 정렬로 대조하고 지금 정렬 값을 옆에
    적는다.
    """
    s = c.place_arm(spot, c.ARM_B, "bar_low", in_book=True)
    f = c.place_arm(fut, c.ARM_B, "bar_low", in_book=True)

    def legacy(r: float) -> float:
        return c.chain(s, f, r, legacy_zero_duration_order=True)[1]

    got = c.fit_risk(legacy, target=c.MDD_TARGET)
    frame = pd.read_csv(u.CSV_PATH)
    hit = frame[
        (frame.arm == u.label(u.U31, "+15m"))
        & (frame.size_basis == f"{u.PRIMARY_RULER} · 자기 맞춤")
        & (frame.segment == c.SEG_CHAIN)
        & (frame.fill == "bar_low")
    ]
    if len(hit) != 1:
        raise AssertionError(f"WAN-440 CSV에서 대조 행을 못 찾았다: {len(hit)}")
    ref = float(hit.risk.iloc[0])
    diff = abs(ref - got)
    if diff > 1e-12:
        raise AssertionError(f"손으로 고른 31종목이 WAN-440과 다르다: {got} vs {ref}")
    return (
        f"검산 — 손으로 고른 31종목 ① 크기 {got:.4%} ≡ WAN-440 공개 CSV (차 {diff:.2e} · "
        f"WAN-443 이전 정렬) · 지금 정렬 크기 {_fit(s, f):.4%}"
    )


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def picks_frame(picks: Sequence[Pick]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(p) for p in picks])


def picks_from_frame(frame: pd.DataFrame) -> list[Pick]:
    fields = {f.name: f.type for f in dataclasses.fields(Pick)}
    out: list[Pick] = []
    for rec in frame.to_dict("records"):
        kw = {
            k: (str(v) if fields[k] == "str" else int(v) if fields[k] == "int" else float(v))
            for k, v in rec.items()
            if k in fields
        }
        out.append(Pick(**kw))  # type: ignore[arg-type]
    return out


def _pct(x: float) -> str:
    return "—" if math.isnan(x) else f"{x:+.1%}"


def render(picks: Sequence[Pick], rows: Sequence[c.Row], notes: Sequence[str]) -> str:
    out = [
        "# WAN-442 — 스토캐스틱 팔 모멘텀 종목 갈아끼우기 (정의 × 기간 · 무작위 교체 대조)",
        "",
        "> 자동 생성(`uv run python -m backtest.wan442_momentum_rotation --part run`). "
        "결정문 `docs/decisions/wan442.md`. 🚨 채택 근거 아님 · 기본값·토대·페이퍼 규칙 불변.",
        "> 판별: **오늘 엔진 · 핀 없음**(WAN-443 이후 정렬 · `ConfluenceParams()`·"
        "`LeverageBookParams()` 그대로). 탐색 숫자(`wan440_scratch/`, 옛 정렬)와 한 표에 "
        "놓지 말 것.",
        "",
    ]
    for mode in MODES:
        mp = [p for p in picks if p.mode == mode]
        s1 = [p for p in mp if p.stage == "1단계"]
        s2 = [p for p in mp if p.stage == "2단계"]
        rnd = [p for p in mp if p.stage == "무작위"]
        if not s1 or not s2 or len(rnd) != RANDOM_DRAWS:
            continue
        chosen = best(s2)
        ok, beaten = verdict(chosen, rnd)
        out += [f"## {mode} — 판정: **{'통과' if ok else '불통과'}**", ""]
        out.append(
            f"* 1단계 1등 **{best(s1).config}** → 2단계 1등 **{chosen.config}**(앞구간 연환산 "
            f"{_pct(chosen.front_cagr)} · 앞구간 맞춤 크기 {chosen.front_risk:.2%})"
        )
        rs = sorted(r.back_ratio for r in rnd)
        out.append(
            f"* 뒷구간 수익/MDD(미끄러짐 · 앞구간 맞춤 크기): 고른 판 **{chosen.back_ratio:+.3f}** "
            f"({chosen.back_return:+.1%}/{chosen.back_mdd:.1%}) · 무작위 20회 중 그 이상 "
            f"**{beaten}개**(기준 ≤ {PASS_MAX_BEATEN}) · 무작위 분포 최소 {rs[0]:+.3f} · 중앙 "
            f"{float(np.median(rs)):+.3f} · 최대 {rs[-1]:+.3f}"
        )
        out.append("")
        for title, group in (("1단계 — 정의 5가지(90일)", s1), ("2단계 — 기간", s2)):
            out += [
                f"### {title}",
                "",
                "| 판 | 앞 크기 | 앞 연환산 | 뒤 수익/MDD | 뒤 수익 · MDD | 뒤 손절가 수익/MDD |",
                "|---|--:|--:|--:|---|--:|",
            ]
            for p in group:
                mark = " ★" if p.config == best(group).config else ""
                out.append(
                    f"| {p.config}{mark} | {p.front_risk:.2%} | {_pct(p.front_cagr)} | "
                    f"{p.back_ratio:+.3f} | {p.back_return:+.1%} · {p.back_mdd:.1%} | "
                    f"{p.back_stop_ratio:+.3f} |"
                )
            out.append("")
        out += [
            "### 8.6년 표 (8.6년 맞춤 크기 · 칸: 총수익 · 연 / 평가손 MDD)",
            "",
            "| 판 | 체결 | 크기 | " + " | ".join(c.SEGMENTS[:4]) + " |",
            "|" + "---|" * 7,
        ]
        listed = [*dict.fromkeys(p.config for p in mp if p.stage != "무작위")]
        for cfg in listed:
            for fill in c.FILLS:
                seg = {
                    r.segment: r
                    for r in rows
                    if (r.arm, r.size_basis, r.fill) == (f"{mode} · {cfg}", SIZE_FULL, fill)
                }
                if not seg:
                    continue
                risk = next(iter(seg.values())).risk
                cells = " | ".join(c._cell(seg[s]) for s in c.SEGMENTS[:4])
                out.append(f"| {cfg} | {w._FILL_LABEL[fill]} | {risk:.2%} | {cells} |")
        out.append("")
        full = sorted(r.full_cagr for r in rnd)
        fixed = sorted(r.fixed_cagr for r in rnd)
        out += [
            "### 무작위 교체 20회 분포(8.6년)",
            "",
            f"* 미끄러짐 MDD 35% 맞춤 연환산: 최소 {full[0]:+.1%} · 중앙 "
            f"{float(np.median(full)):+.1%} · 최대 {full[-1]:+.1%}",
            f"* 거래당 3% 고정 연환산: 최소 {fixed[0]:+.1%} · 중앙 {float(np.median(fixed)):+.1%} "
            f"· 최대 {fixed[-1]:+.1%}",
            "",
            "| 판 | 8.6년 크기 · 연(미끄러짐 / 손절가) | 3% 고정 연 · MDD |",
            "|---|---|---|",
        ]
        for p in [q for q in mp if q.stage != "무작위"]:
            if p.stage == "2단계" and any(q.config == p.config and q.stage == "1단계" for q in mp):
                continue
            out.append(
                f"| {p.config} | {p.full_risk:.2%} · {_pct(p.full_cagr)} / "
                f"{_pct(p.full_stop_cagr)} | {_pct(p.fixed_cagr)} · {p.fixed_mdd:.1%} |"
            )
        out.append("")
    out += ["## 실행 기록", ""] + [f"* {n}" for n in notes] + [""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--part", choices=("markets", "run", "summary"), required=True)
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--markets", type=Path, default=MARKETS_PATH)
    args = ap.parse_args(argv)
    if args.part == "markets":
        jobs = args.jobs if args.jobs is not None else harness.default_jobs()
        built = u.build_markets(jobs)
        args.markets.parent.mkdir(parents=True, exist_ok=True)
        with args.markets.open("wb") as fh:
            pickle.dump(built, fh)
        print("\n".join(built.notes))
        return 0
    if args.part == "summary":
        picks = picks_from_frame(pd.read_csv(PICKS_PATH))
        rows = c.rows_from_frame(pd.read_csv(CSV_PATH))
        old = SUMMARY_PATH.read_text(encoding="utf-8") if SUMMARY_PATH.exists() else ""
        notes = [
            line[2:]
            for line in old.split("## 실행 기록", 1)[-1].splitlines()
            if line.startswith("* ")
        ]
        SUMMARY_PATH.write_text(render(picks, rows, notes), "utf-8")
        return 0
    with args.markets.open("rb") as fh:
        built = pickle.load(fh)  # noqa: S301 — 이 모듈이 `--part markets`로 만든 로컬 캐시
    result = run(built)
    picks_frame(result.picks).to_csv(PICKS_PATH, index=False)
    c.rows_frame(result.rows).to_csv(CSV_PATH, index=False)
    SUMMARY_PATH.write_text(render(result.picks, result.rows, result.notes), "utf-8")
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
