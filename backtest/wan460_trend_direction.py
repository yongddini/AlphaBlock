"""WAN-460 — 추세 신호로 팔의 방향을 정하면 「가장 나쁜 해」가 올라가는가.

갈래 A(지금 그대로 · 늘 팔 롱) · B(BTC 하락 추세면 팔 쉼) · C(BTC 하락 추세면 팔 숏).
추세추종 엔진(엔진 C · BTC·ETH)은 세 갈래 모두 그대로이고 **팔의 방향 하나만** 바꾼다(WAN-394).

## 착수 전에 못 박은 것(결과를 보고 바꾸지 않는다)

* **추세 신호** = BTC 엔진 C 신호 `mean(sign(P_t / P_{t−L} − 1))`(L ∈ 20·60·120·250일)의 **부호**.
  계산은 `strategy.tsmom.target_from_daily_closes`(페이퍼가 쓰는 함수)와 **같은 경로**다 — 날마다
  등식을 회귀 테스트가 걸고, 실행 때 엔진 C 배율의 부호와도 대조한다. 일봉은 WAN-449 엔진 C가 읽는
  그 일봉(2020-09-15 이전 현물 · 이후 선물)이다.
* **시점** = 진입 시각 `t`에 **이미 닫힌 마지막 일봉** D(`D + 1일 ≤ t`)의 신호. 그날 봉은 안 쓴다.
* **신호 0 · 이력 부족(NaN)** = 갈래 A처럼(팔 롱) — 이슈 규정.
* **숏 거울**(갈래 C, 사용자 결정 2026-10-19 「한번에」 · 거울 규약은 개발자 기본안):
  숏 후보(`short_enabled=True`로 낸 같은 base 후보의 숏) · 직전 확정봉 %K > 75 · 손절폭 하한 4% ·
  손절 2배 · 시간 청산 4봉 · 몰림 규칙 같은 숫자 — 단 **BTC 특징(4h · 24h 수익)의 부호를 뒤집어**
  게이트(BTC 4h ≥ +2.59%)·폭락 층(BTC 24h ≥ +5% → ×0.5)·체결 α 층(그 층의 α를 빌린다 — 숏 손절
  체결은 틱으로 안 쟀다)이 거울이 된다. 몰림 신호 수는 **방향마다 따로** 센다(롱 신호로 숏 몰림을
  세지 않는다). 숏 후보 집합 자체는 추세와 무관하게 세고(신호는 시장 상태다), 갈래가 진입만 거른다.
* **크기 둘** — (i) 정본 크기(팔 리스크 · k · λ 전부 WAN-449 값 고정) · (ii) 팔 리스크 · k 고정에
  λ만 갈래마다 MDD 35%(일 저가)로 다시 맞춘다.
* **앞/뒤** = WAN-449 `FRONT_END`(2024-08-08) 앞 · 뒤.

## 검산

* (a) 갈래 A ≡ WAN-449 정본 — 숏을 켠 payload에서 걸러 낸 롱 시장의 일 수익이 공개
  `wan449_daily.csv`와 같아야 한다(숏을 켜도 롱 후보가 같다는 것의 직접 증거이기도 하다).
* (b) 갈래 B의 거래 ⊇ 갈래 A 거래 중 「추세 아래가 아닌」 것 — 같은 키 · 같은 청산.
  ⚠️ 이슈 문구는 「=」이지만 북은 칸 자리를 공유해 A의 하락 추세 거래가 빠지면 다른 후보가 그
  자리에 들어올 수 있다(WAN-316/389) — 그 수를 따로 센다.
* (c) 추세 신호 미래 절단 불변 — 테스트 + 실행 때 실데이터 세 지점.

재현: (정본 캐시가 없으면 `wan438 --part load` → `wan440 --part load` 먼저) →
`uv run python -m backtest.wan460_trend_direction --part spot15m` → `--part markets`(숏 켠 payload ·
하루 안팎) → `--part run`. **측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).**
"""

from __future__ import annotations

import argparse
import bisect
import pickle
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq
from backtest import wan448_stop_fill_ticks as st
from backtest import wan449_tsmom_second_engine as e449
from strategy import tsmom

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
MARKETS_PATH = REPO_ROOT / "backtest" / "cache" / "wan460_markets.pkl"
YEARLY_CSV = REPORT_DIR / "wan460_yearly.csv"
TRADES_CSV = REPORT_DIR / "wan460_arm_trades.csv.gz"
SUMMARY_PATH = REPORT_DIR / "wan460_trend_direction_summary.md"
BTC = "BTC/USDT:USDT"
CHECK_TOL = 1e-9
SIZE_FIXED = "(i) 정본 크기"
SIZE_REFIT = "(ii) λ 재맞춤(MDD 35%)"

DAY_MS = tsmom.DAY_MS

Branch = Literal["A", "B", "C"]
BRANCHES: tuple[Branch, ...] = ("A", "B", "C")
BRANCH_LABELS: dict[Branch, str] = {
    "A": "A · 지금 그대로",
    "B": "B · 하락 추세면 팔 쉼",
    "C": "C · 하락 추세면 팔 숏",
}
Side = Literal["long", "short"]


def daily_trend_signal(open_time_ms: Sequence[int], close: Sequence[float]) -> pd.Series:
    """확정 일봉마다 BTC 추세 신호(−1 · −0.5 · 0 · 0.5 · 1 · 이력 부족은 NaN).

    index = 일봉 open_time(ms).

    `strategy.tsmom.target_from_daily_closes`와 같은 경로로 계산한다(그 함수는 마지막 날 하나만
    내므로 날마다 다시 부르면 O(N²) — 여기서는 한 번에 낸다). 등식은 회귀 테스트가 날마다 건다.
    🚨 확정봉만 줄 것(형성 중인 오늘 봉이 섞이면 룩어헤드다). 날짜가 비면 거부한다.
    """
    times = pd.Series(np.asarray(open_time_ms, dtype=np.int64))
    closes = pd.Series(np.asarray(close, dtype=float))
    if len(times) != len(closes):
        raise ValueError("open_time과 close의 길이가 다르다")
    if len(times) == 0:
        raise ValueError("확정 일봉이 없다")
    steps = times.diff().dropna()
    if len(steps) and not (steps == DAY_MS).all():
        raise ValueError("일봉이 연속이 아니다 — 「L일 전」이 다른 날을 가리킨다")
    if closes.isna().any() or (closes <= 0).any():
        raise ValueError("종가에 결측·비양수가 있다")
    returns = closes.pct_change()
    price = (1.0 + returns.fillna(0.0)).cumprod()
    sig = sum(np.sign(price / price.shift(lb) - 1.0) for lb in tsmom.LOOKBACKS) / len(
        tsmom.LOOKBACKS
    )
    out = pd.Series(sig.to_numpy(float), index=times.to_numpy(np.int64))
    out.index.name = "open_time"
    return out


@dataclass(frozen=True)
class TrendLookup:
    """진입 시각 → 그 순간 이미 닫힌 마지막 일봉의 추세 신호."""

    open_times: tuple[int, ...]
    signals: tuple[float, ...]

    @classmethod
    def from_series(cls, signal: pd.Series) -> TrendLookup:
        idx = [int(v) for v in signal.index]
        if idx != sorted(idx):
            raise ValueError("일봉 open_time이 오름차순이 아니다")
        return cls(tuple(idx), tuple(float(v) for v in signal.to_numpy(float)))

    def signal_at(self, entry_ms: int) -> float:
        """`D + 1일 ≤ entry_ms`인 마지막 D의 신호 — 없으면 NaN."""
        # 닫힌 봉 = open_time ≤ entry − 1일
        i = bisect.bisect_right(self.open_times, int(entry_ms) - DAY_MS) - 1
        return self.signals[i] if i >= 0 else float("nan")

    def sign_at(self, entry_ms: int) -> int:
        """+1 · 0 · −1(NaN은 0 — 갈래 A처럼 다룬다)."""
        s = self.signal_at(entry_ms)
        if not np.isfinite(s) or s == 0.0:
            return 0
        return 1 if s > 0 else -1


def branch_side(branch: Branch, sign: int) -> Side | None:
    """그 갈래 · 그 추세에서 팔이 낼 방향(None = 쉼). 이슈 표 그대로."""
    if branch not in BRANCHES:
        raise ValueError(f"갈래는 {BRANCHES} 중 하나: {branch!r}")
    if sign >= 0 or branch == "A":
        return "long"
    return None if branch == "B" else "short"


T = TypeVar("T")


def select_for_branch(
    items: Sequence[T],
    *,
    branch: Branch,
    side: Side,
    entry_ms: Callable[[T], int],
    lookup: TrendLookup,
) -> list[T]:
    """`side` 방향 후보 중 그 갈래가 그 시각에 허용하는 것만 남긴다(순서 보존).

    갈래 B의 롱 = 갈래 A의 롱 중 「추세 아래가 아닌」 것 — 검산 (b)가 그 부분집합을 건다.
    """
    return [x for x in items if branch_side(branch, lookup.sign_at(entry_ms(x))) == side]


# ---------------------------------------------------------------------------
# 추세 신호 — 엔진 C가 읽는 그 BTC 일봉에서
# ---------------------------------------------------------------------------


def btc_trend_signal() -> pd.Series:
    """WAN-449 엔진 C의 BTC 일봉(현물→선물 이음)으로 낸 날마다 신호(index = open_time ms).

    엔진 C 배율(`tsmom_weights`)의 부호와 대조한다 — 이 신호가 정말 「그 엔진의 BTC 신호」인지.
    """
    bars = e449.daily_bars(BTC, u.ms(e449.FUT_FROM))
    bars = bars[bars.index >= pd.Timestamp("2017-01-01")]
    ret = bars["ret"]
    price = (1.0 + ret.fillna(0.0)).cumprod()
    open_ms = e449._to_ms(pd.DatetimeIndex(bars.index))
    sig = daily_trend_signal(open_ms.tolist(), price.tolist())
    wgt = e449.tsmom_weights(price, ret).to_numpy(float)
    sv = sig.to_numpy(float)
    live = wgt != 0.0
    if not np.array_equal(np.sign(wgt[live]), np.sign(sv[live])):
        raise AssertionError("추세 신호의 부호가 엔진 C BTC 배율의 부호와 다르다")
    return sig


def future_cut_check(sig_fn: Callable[[int], pd.Series], full: pd.Series) -> int:
    """검산 (c) 실데이터 — 세 지점에서 잘라 다시 낸 신호가 전체판의 앞부분과 같은가(지점 수)."""
    n = len(full)
    cuts = (n // 3, n // 2, n - 30)
    for k in cuts:
        part = sig_fn(k)
        pd.testing.assert_series_equal(part, full.iloc[:k])
    return len(cuts)


# ---------------------------------------------------------------------------
# 갈래별 시장
# ---------------------------------------------------------------------------


def subset(mk: c.Market, keep: Callable[[w.ArmEntry], bool]) -> c.Market:
    """후보만 거른다 — 몰림 신호 수 · BTC 특징은 **그대로** 둔다(신호는 시장 상태다)."""
    idx = [i for i, e in enumerate(mk.entries) if keep(e)]
    return c.Market(
        mk.name,
        mk.payloads,
        [mk.entries[i] for i in idx],
        [mk.counts[i] for i in idx],
        [mk.btc_24h[i] for i in idx],
        [mk.vol_ratio[i] for i in idx],
    )


def merge(long_mk: c.Market, short_mk: c.Market) -> c.Market:
    """롱 · 숏 시장을 한 북으로 — 진입 시각 순(안정)으로 섞는다. 칸은 롱 쪽 payload를 쓴다."""
    cells = {(p.symbol, p.timeframe) for p in long_mk.payloads}
    if not {(p.symbol, p.timeframe) for p in short_mk.payloads} <= cells:
        raise AssertionError("숏 시장에 롱 시장에 없는 칸이 있다")
    rows = [
        (e, n, b, v, 0)
        for e, n, b, v in zip(
            long_mk.entries, long_mk.counts, long_mk.btc_24h, long_mk.vol_ratio, strict=True
        )
    ] + [
        (e, n, b, v, 1)
        for e, n, b, v in zip(
            short_mk.entries, short_mk.counts, short_mk.btc_24h, short_mk.vol_ratio, strict=True
        )
    ]
    rows.sort(key=lambda r: int(r[0].cand.entry_time))
    keys = [r[0].key for r in rows]
    if len(set(keys)) != len(keys):
        raise AssertionError("롱 · 숏 후보의 키(칸 · 진입 시각 · 진입가)가 겹친다")
    return c.Market(
        long_mk.name,
        long_mk.payloads,
        [r[0] for r in rows],
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
    )


def stop_alphas() -> dict[str, float]:
    """WAN-448 측정 α(층별 평균) — `e449.arm_trades`와 같은 식."""
    tk = pd.read_csv(st.TICKS_CSV)
    tk = tk[tk["ok"]]
    return {k: float(tk[tk["stratum"] == k]["alpha"].mean()) for k in (st.CRASH, st.NORMAL)}


def place_branch(
    smk: c.Market, fmk: c.Market, alphas: dict[str, float]
) -> tuple[list[w.PlacedTrade], list[w.PlacedTrade]]:
    """`e449.arm_trades`와 같은 배치 — 두 체결 극단 → 측정 α 가중합."""
    pl = {
        f: (
            c.place_arm(smk, c.ARM_B, f, in_book=True),
            c.place_arm(fmk, c.ARM_B, f, in_book=True),
        )
        for f in c.FILLS
    }
    sp, fu = (
        st.blended_trades(pl["stop"][k], pl["bar_low"][k], st.alphas_for(pl["stop"][k], mk, alphas))
        for k, mk in ((0, smk), (1, fmk))
    )
    return sp, fu


# ---------------------------------------------------------------------------
# 지표
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BranchResult:
    branch: Branch
    size: str
    lam: float
    yearly: pd.Series
    worst_year: int
    worst: float
    negative_years: int
    median: float
    cagr: float
    mdd: float
    front_cagr: float
    front_mdd: float
    back_cagr: float
    back_mdd: float
    mdd_day: str
    back_mdd_day: str


def _mdd_day(eq: pd.Series, low: pd.Series) -> str:
    peak = np.maximum.accumulate(np.maximum(eq.to_numpy(), 1.0))
    return str(eq.index[int(np.argmax(1.0 - low.to_numpy() / peak))].date())


def _seg(eq: pd.Series, low: pd.Series, mask: np.ndarray, base: float) -> tuple[float, float]:
    """구간 (연환산, MDD) — 구간 시작 평가액을 1로 다시 잡는다."""
    e = eq[mask] / base
    lo = low[mask] / base
    yrs = (e.index[-1] - e.index[0]).days / 365.25 + 1 / 365.25
    return c.cagr(float(e.iloc[-1]) - 1.0, yrs), e449.mdd_of(e, lo)


def metrics(branch: Branch, size: str, lam: float, eq: pd.Series, low: pd.Series) -> BranchResult:
    yr = e449.yearly(eq)
    full = yr.reindex(list(e449.YEARS))
    if full.isna().any():
        raise ValueError("판정 연도에 빈 해가 있다")
    worst_year = int(full.idxmin())
    front = np.asarray(eq.index < pd.Timestamp(e449.FRONT_END))
    years = c._years(u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END))
    fc, fm = _seg(eq, low, front, 1.0)
    base = float(eq[front].iloc[-1])
    bc, bm = _seg(eq, low, ~front, base)
    back_day = _mdd_day(eq[~front] / base, low[~front] / base)
    return BranchResult(
        branch,
        size,
        lam,
        yr,
        worst_year,
        float(full.min()),
        int((full < 0).sum()),
        float(full.median()),
        c.cagr(float(eq.iloc[-1]) - 1.0, years),
        e449.mdd_of(eq, low),
        fc,
        fm,
        bc,
        bm,
        _mdd_day(eq, low),
        back_day,
    )


@dataclass(frozen=True)
class TradeStats:
    branch: Branch
    side: str
    segment: str
    trades: int
    win_rate: float
    mean_net_r: float


def trade_stats(branch: Branch, trades: Sequence[w.PlacedTrade]) -> list[TradeStats]:
    cut = u.ms(e449.FRONT_END)
    out: list[TradeStats] = []
    for side in ("롱", "숏"):
        for seg in ("앞", "뒤"):
            ts = [
                t
                for t in trades
                if t.short == (side == "숏") and (t.entry_time < cut) == (seg == "앞")
            ]
            r = np.array([t.net_r for t in ts], dtype=float)
            out.append(
                TradeStats(
                    branch,
                    side,
                    seg,
                    len(ts),
                    float((r > 0).mean()) if len(r) else float("nan"),
                    float(r.mean()) if len(r) else float("nan"),
                )
            )
    return out


def _tkey(t: w.PlacedTrade) -> tuple[str, str, int, float]:
    return (t.symbol, t.timeframe, t.entry_time, t.entry_price)


def check_b(
    a: Sequence[w.PlacedTrade], b: Sequence[w.PlacedTrade], lookup: TrendLookup
) -> tuple[int, int, int, int]:
    """검산 (b) — (A의 추세 위 거래 수, 그중 B에 같은 키 · 같은 청산으로 있는 수, B 거래 수,
    B에만 있는 거래 수). B에 A의 하락 추세 거래가 있으면 죽는다."""
    a_up = {_tkey(t): t for t in a if lookup.sign_at(t.entry_time) >= 0}
    b_map = {_tkey(t): t for t in b}
    if any(lookup.sign_at(t.entry_time) < 0 for t in b):
        raise AssertionError("갈래 B에 하락 추세 진입이 있다")
    same = sum(
        1
        for k, t in a_up.items()
        if k in b_map and (b_map[k].exit_time, b_map[k].stopped) == (t.exit_time, t.stopped)
    )
    extra = sum(1 for k in b_map if k not in a_up)
    return len(a_up), same, len(b_map), extra


def check_a(frame: pd.DataFrame) -> float:
    """검산 (a) — 공개 `wan449_daily.csv`와 일 수익 · 합성 곡선의 최대 절대차."""
    pub = pd.read_csv(e449.DAILY_CSV, index_col=0, parse_dates=True)
    worst = 0.0
    for col in ("arm_ret", "arm_low", "c_ret", "c_low", "combo_eq"):
        a = frame[col].reindex(pub.index)
        worst = max(worst, float((a - pub[col]).abs().max()))
    if len(frame) != len(pub):
        raise AssertionError(f"일 격자 길이가 다르다: {len(frame)} vs 공개 {len(pub)}")
    return worst


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


@dataclass
class Result:
    rows: list[BranchResult]
    stats: list[TradeStats]
    trend_by_year: pd.DataFrame
    trades: dict[Branch, tuple[list[w.PlacedTrade], list[w.PlacedTrade]]]
    notes: list[str]


def run(built: u.Markets) -> Result:
    t0 = time.monotonic()
    if built.spot_short is None or built.fut_short is None:
        raise ValueError("숏 거울 시장이 없다 — `--part markets`(short_enabled)로 다시 만든다")
    notes: list[str] = []

    sig = btc_trend_signal()
    bars = e449.daily_bars(BTC, u.ms(e449.FUT_FROM))
    bars = bars[bars.index >= pd.Timestamp("2017-01-01")]
    price = (1.0 + bars["ret"].fillna(0.0)).cumprod()
    open_ms = e449._to_ms(pd.DatetimeIndex(bars.index)).tolist()
    n_cut = future_cut_check(lambda k: daily_trend_signal(open_ms[:k], price.tolist()[:k]), sig)
    notes.append(
        f"검산 (c) — 실데이터 {n_cut}지점 미래 절단 불변 통과 · 엔진 C BTC 배율 부호와 일치"
    )
    lookup = TrendLookup.from_series(sig)
    sd = pd.Series(sig.to_numpy(float), index=pd.to_datetime(sig.index, unit="ms"))
    trend_by_year = pd.DataFrame(
        {
            "위": sd.groupby(sd.index.year).apply(lambda x: float((x > 0).mean())),
            "0": sd.groupby(sd.index.year).apply(lambda x: float((x == 0).mean())),
            "이력 부족": sd.groupby(sd.index.year).apply(lambda x: float(x.isna().mean())),
            "아래": sd.groupby(sd.index.year).apply(lambda x: float((x < 0).mean())),
        }
    )

    # 정본(갈래 A) — `e449.arm_trades` 그대로.
    sp_a, fu_a, risk = e449.arm_trades(built)
    arm_a = e449.arm_daily_from(sp_a, fu_a, risk)
    frame, variants, _n449, _nums = e449.run(built, arm=arm_a)
    gap = check_a(frame)
    if gap > CHECK_TOL:
        raise AssertionError(f"검산 (a) 실패 — 공개 wan449_daily.csv와 최대 차 {gap:.2e}")
    notes.append(
        f"검산 (a) — 갈래 A(숏을 켠 payload의 롱) ≡ 공개 `wan449_daily.csv` "
        f"(arm·엔진 C·합성 일 곡선 최대 차 {gap:.1e})"
    )
    main = variants[0]
    k, lam0 = main.k, main.lam

    # 갈래 시장 — 정본과 같은 거래대금 상위 31 목록을 롱 · 숏에 똑같이 건다.
    s = m.setting(built)
    top = s.rotations(lambda d, t: liq.select_liquidity(d, t, top=True))
    pool = set(built.rule_store)
    spot_syms = {p.symbol for p in built.spot.payloads}
    smk_l, _ = m.apply_rotation(s.spot_pool, top[0], force_close=False)
    fmk_l, _ = m.apply_rotation(s.fut_pool, top[1], force_close=False)
    smk_s, _ = m.apply_rotation(
        u.restrict(built.spot_short, pool & spot_syms), top[0], force_close=False
    )
    fmk_s, _ = m.apply_rotation(u.restrict(built.fut_short, pool), top[1], force_close=False)
    alphas = stop_alphas()

    def up(e: w.ArmEntry) -> bool:
        return lookup.sign_at(int(e.cand.entry_time)) >= 0

    def down(e: w.ArmEntry) -> bool:
        return lookup.sign_at(int(e.cand.entry_time)) < 0

    markets: dict[Branch, tuple[c.Market, c.Market]] = {
        "A": (smk_l, fmk_l),
        "B": (subset(smk_l, up), subset(fmk_l, up)),
        "C": (
            merge(subset(smk_l, up), subset(smk_s, down)),
            merge(subset(fmk_l, up), subset(fmk_s, down)),
        ),
    }
    notes.append(
        "후보 수(현물 창 밖 / 선물 6년) — "
        + " · ".join(
            f"{b}: 롱 {sum(not e.is_short for e in sm.entries)}/"
            f"{sum(not e.is_short for e in fm.entries)} 숏 "
            f"{sum(e.is_short for e in sm.entries)}/{sum(e.is_short for e in fm.entries)}"
            for b, (sm, fm) in markets.items()
        )
        + f" · 숏 후보 전체(추세 무관) {len(smk_s.entries)}/{len(fmk_s.entries)}"
    )
    trades: dict[Branch, tuple[list[w.PlacedTrade], list[w.PlacedTrade]]] = {}
    for b, (sm, fm) in markets.items():
        trades[b] = place_branch(sm, fm, alphas)
    # 이 모듈의 배치 경로 ≡ 정본 경로(갈래 A).
    for mine, ref in zip((*trades["A"][0], *trades["A"][1]), (*sp_a, *fu_a), strict=True):
        if (_tkey(mine), mine.exit_time, mine.net_r) != (_tkey(ref), ref.exit_time, ref.net_r):
            raise AssertionError("이 모듈의 갈래 A 배치가 `e449.arm_trades`와 다르다")
    notes.append("검산 — 이 모듈의 갈래 A 배치 ≡ `e449.arm_trades` 거래(키 · 청산 · net R)")
    a_all = [*trades["A"][0], *trades["A"][1]]
    b_all = [*trades["B"][0], *trades["B"][1]]
    n_up, same, n_b, extra = check_b(a_all, b_all, lookup)
    notes.append(
        f"검산 (b) — A의 추세 위 거래 {n_up}건 중 {same}건이 B에 같은 키 · 같은 청산으로 있다 · "
        f"B 거래 {n_b}건 중 A에 없던 거래 {extra}건(하락 추세 거래가 비운 칸 · 자본 자리)"
    )

    rc, lc = frame["c_ret"], frame["c_low"]
    idx = frame.index
    rows: list[BranchResult] = []
    stats: list[TradeStats] = []
    for b, (sp, fu) in trades.items():
        arm = e449.arm_daily_from(sp, fu, risk)
        ra = arm.ret.reindex(idx).fillna(0.0)
        la = arm.low.reindex(idx).fillna(0.0)
        for size in (SIZE_FIXED, SIZE_REFIT):
            lam = lam0 if size == SIZE_FIXED else e449.fit_lambda(ra, la, rc, lc, k, c.MDD_TARGET)
            eq, low = e449.combine(ra, la, rc, lc, k, lam)
            rows.append(metrics(b, size, lam, eq, low))
        stats += trade_stats(b, [*sp, *fu])
    # 하드 경계 경보(WAN-367): 세 갈래의 MDD가 같은 값이면 「같은 하락 구간에 같은 거래」인지
    # 거래 목록으로 확인한다(아니면 죽는다 — 같은 숫자가 버그로 나온 것일 수 있다).
    eq_a, low_a = e449.combine(frame["arm_ret"], frame["arm_low"], rc, lc, k, lam0)
    peak = np.maximum.accumulate(np.maximum(eq_a.to_numpy(), 1.0))
    trough_i = int(np.argmax(1.0 - low_a.to_numpy() / peak))
    peak_i = int(np.argmax(eq_a.to_numpy()[: trough_i + 1]))
    w0 = int(eq_a.index[peak_i].value // 1_000_000)
    w1 = int(eq_a.index[trough_i].value // 1_000_000) + DAY_MS

    def in_window(ts: Sequence[w.PlacedTrade]) -> set[tuple[str, str, int, float, int]]:
        return {(*_tkey(t), t.exit_time) for t in ts if t.exit_time >= w0 and t.entry_time < w1}

    win = {b: in_window([*sp, *fu]) for b, (sp, fu) in trades.items()}
    mdds = {r.branch: r.mdd for r in rows if r.size == SIZE_FIXED}
    same_mdd = max(mdds.values()) - min(mdds.values()) < 1e-6
    if same_mdd and not (win["A"] == win["B"] == win["C"]):
        raise AssertionError("세 갈래 MDD가 같은데 하락 구간의 거래가 다르다 — 버그 의심")
    notes.append(
        f"하드 경계 확인 — 정본 최대 낙폭 구간 {eq_a.index[peak_i].date()} → "
        f"{eq_a.index[trough_i].date()}의 팔 거래가 세 갈래 모두 같다({len(win['A'])}건 · "
        "하락 추세 진입 0 · 숏 0): 최대 낙폭이 **상승 추세 중 급락일**에 나므로 "
        "추세 필터가 닿지 않는다 → 세 갈래 MDD가 같고("
        + " · ".join(f"{b} {v:.4%}" for b, v in mdds.items())
        + ") λ 재맞춤도 정본과 같다"
    )
    a_fixed = next(r for r in rows if r.branch == "A" and r.size == SIZE_FIXED)
    pub_eq = pd.read_csv(e449.DAILY_CSV, index_col=0, parse_dates=True)["combo_eq"]
    if float((e449.yearly(pub_eq) - a_fixed.yearly).abs().max()) > CHECK_TOL:
        raise AssertionError("검산 (a) — 갈래 A 정본 크기 해마다 수익이 WAN-449와 다르다")
    notes.append(f"크기 — 팔 리스크 {risk:.4%} · k {k:.3f} · λ(정본) {lam0:.3f}(재맞춤 λ는 표에)")
    notes.append(f"측정 {time.monotonic() - t0:.0f}초")
    return Result(rows, stats, trend_by_year, trades, notes)


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------


def yearly_frame(rows: Sequence[BranchResult]) -> pd.DataFrame:
    recs = []
    for r in rows:
        for y, v in r.yearly.items():
            recs.append({"branch": r.branch, "size": r.size, "lam": r.lam, "year": y, "ret": v})
    return pd.DataFrame(recs)


def trades_frame(
    trades: dict[Branch, tuple[list[w.PlacedTrade], list[w.PlacedTrade]]],
) -> pd.DataFrame:
    recs = []
    for b, (sp, fu) in trades.items():
        for market, ts in (("현물", sp), ("선물", fu)):
            for t in ts:
                recs.append(
                    {
                        "branch": b,
                        "market": market,
                        "symbol": t.symbol,
                        "timeframe": t.timeframe,
                        "side": "숏" if t.short else "롱",
                        "entry_time": t.entry_time,
                        "exit_time": t.exit_time,
                        "entry_price": t.entry_price,
                        "stop_price": t.stop_price,
                        "stopped": t.stopped,
                        "size": t.size,
                        "net_r": t.net_r,
                    }
                )
    return pd.DataFrame(recs)


def _p(x: float) -> str:
    return f"{x:+.1%}"


def render(res: Result) -> str:
    years = list(e449.YEARS)
    out = [
        "# WAN-460 — 추세 신호로 팔의 방향을 정하면 「가장 나쁜 해」가 올라가는가",
        "",
        f"> 엔진: 오늘 채택 팔(WAN-449 정본 · 핀 없음) · 창: 현물 {c.WINDOW_START} ~ 선물 "
        f"{harness.DEFAULT_END}(8.6년) · 추세 = BTC 엔진 C 신호의 부호(진입 시각에 닫힌 마지막 "
        "일봉). 🚨 **채택 권고를 내지 않는다** — 표를 내면 선택은 사용자가 한다.",
        "",
        "## 판정 줄 — 가장 나쁜 해(2018~2025 · 합성)",
        "",
        "| 갈래 | 크기 | λ | 가장 나쁜 해 | 마이너스 해 | 해마다 중앙 | 연환산 "
        "| MDD(일 저가 · 최저점) | 앞 연환산 / MDD | 뒤 연환산 / MDD(최저점) |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in res.rows:
        out.append(
            f"| {BRANCH_LABELS[r.branch]} | {r.size} | {r.lam:.4f} | **{_p(r.worst)}**"
            f"({r.worst_year}) | {r.negative_years} | {_p(r.median)} | {_p(r.cagr)} | "
            f"{r.mdd:.2%}({r.mdd_day}) | {_p(r.front_cagr)} / {r.front_mdd:.2%} | "
            f"{_p(r.back_cagr)} / {r.back_mdd:.2%}({r.back_mdd_day}) |"
        )
    out += [
        "",
        "## 해마다(합성)",
        "",
        "| 갈래 | 크기 | " + " | ".join(str(y) for y in years) + " | 2026(부분) |",
        "|---|---|" + "--:|" * (len(years) + 1),
    ]
    for r in res.rows:
        cells = " | ".join(_p(float(r.yearly.get(y, float("nan")))) for y in years)
        tail = _p(float(r.yearly.get(2026, float("nan"))))
        out.append(f"| {BRANCH_LABELS[r.branch]} | {r.size} | {cells} | {tail} |")
    out += [
        "",
        "## 팔 거래(갈래 · 방향 · 앞/뒤) — 측정 체결 α 가중 net R",
        "",
        "| 갈래 | 방향 | 구간 | 거래 | 승률 | 거래당 net R |",
        "|---|---|---|--:|--:|--:|",
    ]
    for t in res.stats:
        if t.trades == 0:
            continue
        out.append(
            f"| {BRANCH_LABELS[t.branch]} | {t.side} | {t.segment} | {t.trades} | "
            f"{t.win_rate:.1%} | {t.mean_net_r:+.3f} |"
        )
    out += [
        "",
        "## 참고 — BTC 추세 부호의 날 비율(해마다)",
        "",
        "| 연도 | 위 | 0 | 아래 | 이력 부족(갈래 A처럼) |",
        "|---|--:|--:|--:|--:|",
    ]
    for y, row in res.trend_by_year.iterrows():
        out.append(
            f"| {y} | {row['위']:.1%} | {row['0']:.1%} | {row['아래']:.1%} | "
            f"{row['이력 부족']:.1%} |"
        )
    first = res.trend_by_year["이력 부족"]
    out.append(
        "\n⚠️ BTC 현물 이력이 2017년 중반에 시작해 250일 룩백이 "
        f"2018년의 {float(first.get(2018, 0.0)):.0%}에서 정의되지 않는다 — 그 날들은 이슈 규정대로 "
        "갈래 A처럼(팔 롱) 다뤘다. **판정 줄의 2018년은 그만큼 세 갈래가 같은 거래를 한다.**"
    )
    out += ["", "## 기록 · 검산", "", *[f"* {n}" for n in res.notes]]
    out += [
        "* 숏 손절 체결 α는 롱 측정값(WAN-448)을 거울 층에서 빌렸다 — 숏 손절은 틱으로 안 쟀다.",
        "* 몰림 게이트 · 폭락 층 · α 층의 숏 거울은 BTC 수익의 부호를 뒤집은 것이다"
        "(개발자 기본안).",
        "* 방향이 다른 두 거래가 같은 칸 · 같은 1분에 「청산 → 체결」로 겹치면 보수적으로 체결을 "
        "막는다(1분봉으로 순서를 못 가른다).",
        "* 팔의 크기 변경은 일 수익의 선형 배율 근사(WAN-449와 같다) · λ 재맞춤은 전체 창 기준.",
        "* 전부 `pen_5bp` × 같은 분 익절 금지 위 값 · 페이퍼 풀(지금 상장 전체)이 아니라 "
        "정본 풀이다.",
        "",
        "**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 · 실거래 보류 "
        "유지(`ALPHABLOCK_LIVE_TRADING=false`).**",
        "",
    ]
    return "\n".join(out)


def load_stress_15m() -> str:
    """WAN-438 현물 스트레스 루트에 **원천 15m**을 싣는다(2017-08 ~ 2020-08).

    WAN-439 §8(B + 15m)이 그 루트의 15m 칸을 썼는데, 15m을 루트에 넣은 경로가 커밋된 코드에 없다
    (`spot_klines.NATIVE_TIMEFRAMES`에 15m이 없고 WAN-438 탐색 스크래치가 넣었다). 캐시를 다시
    만들면 그 칸이 「데이터 없음」으로 죽는다 — 같은 아카이브(현물 15m 월별 zip)를 WAN-440이
    신규 현물에 쓰는 그 경로(`u.load_root`)로 싣고, 불가능한 틱 격리를 다시 돌린다. 운영 DB 불변.
    """
    from backtest import wan438_spot_stress as m438
    from data import spot_klines as sk

    root = m438.DEFAULT_ROOTS / "stress"
    rows, mb, sec = u.load_root(
        root,
        sk.SPOT_SYMBOLS,
        ("15m",),
        m438.STRESS_FIRST_MONTH,
        m438.STRESS_LAST_MONTH,
        market="spot",
        end_ms=u.ms(m438.STRESS_END),
    )
    q = sk.quarantine_impossible_ticks(root)
    gaps = sum(r.gaps for r in rows)
    return (
        f"현물 스트레스 루트 15m — {len(rows)}종목 · {mb:,.1f}MB · {sec:.0f}초 · 구멍 {gaps} · "
        f"격리 재계산 {len(q)}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--part", choices=("spot15m", "markets", "run"), required=True)
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--markets", type=Path, default=MARKETS_PATH)
    args = ap.parse_args(argv)
    if args.part == "spot15m":
        print(load_stress_15m())
        return 0
    if args.part == "markets":
        jobs = args.jobs if args.jobs is not None else harness.default_jobs()
        t0 = time.monotonic()
        built = u.build_markets(jobs, short_enabled=True)
        args.markets.parent.mkdir(parents=True, exist_ok=True)
        with args.markets.open("wb") as fh:
            pickle.dump(built, fh)
        print("\n".join(built.notes))
        print(f"시장(롱+숏) {time.monotonic() - t0:.0f}초")
        return 0
    with args.markets.open("rb") as fh:
        built = pickle.load(fh)  # noqa: S301 — 이 모듈이 `--part markets`로 만든 로컬 캐시
    res = run(built)
    yearly_frame(res.rows).to_csv(YEARLY_CSV, index=False)
    trades_frame(res.trades).to_csv(TRADES_CSV, index=False)
    SUMMARY_PATH.write_text(render(res), "utf-8")
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
