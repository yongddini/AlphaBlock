"""WAN-449 — 두 번째 엔진: 시계열 모멘텀을 스토캐스틱 팔에 붙여 「해마다 20%」를 만드는가.

스토캐스틱 팔은 폭락 반등을 사는 구조라 조용한 해·하락 해에 쉰다(해마다 중앙 +14.8% ·
20% 이상 3/9해). 그 해에 버는 성질이 반대인 엔진 C(시계열 모멘텀 · 교과서 설정 · 튜닝 없음)를
같은 자본에 겹쳐 잰다.

* 엔진 C: BTC·ETH 같은 위험 · 신호 = 평균(sign(L일 수익)), L ∈ {20, 60, 120, 250} ·
  크기 = 신호 × 연 20% / 직전 60일 실현 변동성(|배율| ≤ 2) · 다음 날 수익에 적용 ·
  비용 = 포지션 변화량 × 9bp · 2020-09-15부터 선물 실제 펀딩(롱이 낸다 · 숏이 받는다),
  그 전은 현물 가격(펀딩 0).
* 합성: 팔(WAN-448 측정 α · 캐리 없음) 일 수익 + k × 엔진 C 일 수익. k = 앞구간
  (2018-01 ~ 2024-08-08)에서 두 일 변동성이 같게. 그 뒤 λ로 함께 키워 MDD 35%(일 저가 기준 ·
  엔진 C도 일봉 고·저)에 맞춘다.
* 판정(착수 전 고정): 2018~2025 여덟 해 중 해마다 중앙값 ≥ 20% 그리고 마이너스인 해 ≤ 1.

재현: `uv run python -m backtest.wan449_tsmom_second_engine`(시장 WAN-442 피클 · 펀딩 WAN-447
캐시 · 체결 α WAN-448 CSV). **측정 전용 · 기본값·토대·페이퍼 불변 · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).**
"""

from __future__ import annotations

import argparse
import math
import pickle
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan438_spot_stress as m438
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq
from backtest import wan447_funding_carry as fc
from backtest import wan448_stop_fill_ticks as st
from data import spot_klines as sk
from data.storage import OhlcvStore

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
DAILY_CSV = REPORT_DIR / "wan449_daily.csv"
SUMMARY_PATH = REPORT_DIR / "wan449_tsmom_summary.md"

ASSETS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT")
LOOKBACKS: tuple[int, ...] = (20, 60, 120, 250)
VOL_TARGET = 0.20
VOL_WINDOW = 60
MAX_LEVERAGE = 2.0
COST = 0.0009
DAYS_PER_YEAR = 365.0
FUT_FROM = "2020-09-15"
FRONT_END = "2024-08-08"
"""앞구간 끝 = WAN-442 대표 경계(합성 비율 k를 여기서만 정한다)."""
YEARS: tuple[int, ...] = tuple(range(2018, 2026))
TARGET_MEDIAN = 0.20
MAX_NEGATIVE_YEARS = 1
DAY_MS = 86_400_000
CHECK_TOL = 1e-9
CARRY_FRACTION = 0.5
VARIANT_MAIN = "정본(엔진 C 일 고·저 반영)"
VARIANT_CLOSE = "엔진 C 종가만(초안)"
VARIANT_CARRY = "정본 + 캐리 50%"


def tsmom_weights(price: pd.Series, returns: pd.Series) -> pd.Series:
    """그날 종가에 정한 포지션(다음 날 수익에 적용) — 신호 × 변동성 목표 · |배율| ≤ 2."""
    sig = sum(np.sign(price / price.shift(lb) - 1.0) for lb in LOOKBACKS) / len(LOOKBACKS)
    vol = returns.rolling(VOL_WINDOW).std() * math.sqrt(DAYS_PER_YEAR)
    wgt = (sig * VOL_TARGET / vol).clip(-MAX_LEVERAGE, MAX_LEVERAGE)
    return wgt.fillna(0.0)


@dataclass(frozen=True)
class EngineDaily:
    """엔진 C 한 자산(또는 바스켓)의 일 수익과 그날 가장 나쁜 순간의 수익."""

    ret: pd.Series
    low: pd.Series
    """보유 포지션이 그날 고가·저가 중 불리한 쪽에 닿았을 때의 수익(비용·펀딩 포함)."""


def tsmom_returns(
    returns: pd.Series,
    funding: pd.Series,
    low_r: pd.Series | None = None,
    high_r: pd.Series | None = None,
) -> EngineDaily:
    """한 자산의 엔진 C 일 수익 — 어제 정한 배율 × 오늘 수익 − 비용 − 펀딩.

    `low_r`·`high_r`(그날 저가·고가 ÷ 전날 종가 − 1)을 주면 롱은 저가, 숏은 고가에서 잰 그날 최악
    수익을 함께 낸다(MDD를 일 저가 기준으로 재기 위해). 안 주면 최악 = 종가 수익(초안 판).
    """
    r = returns.fillna(0.0)
    price = (1.0 + r).cumprod()
    wgt = tsmom_weights(price, returns)
    held = wgt.shift(1).fillna(0.0)
    cost = (wgt.diff().abs().fillna(wgt.abs()) * COST).shift(1).fillna(0.0)
    ret = held * r - cost - held * funding
    if low_r is None or high_r is None:
        return EngineDaily(ret, ret.copy())
    adverse = np.where(
        held > 0, low_r.reindex(r.index).fillna(r), high_r.reindex(r.index).fillna(r)
    )
    low = pd.Series(held.to_numpy() * adverse, index=r.index) - cost - held * funding
    return EngineDaily(ret, np.minimum(low, ret))


def daily_returns(spot: m.Daily | None, fut: m.Daily, fut_from_ms: int) -> pd.Series:
    """`fut_from_ms` 이전은 현물 수익(있으면), 이후는 선물 수익 — 이음매 점프 없이 수익끼리 "
    "잇는다."""

    def rets(d: m.Daily) -> pd.Series:
        s = pd.Series(d.close, index=pd.to_datetime(d.open_time, unit="ms"))
        return s.pct_change()

    rf = rets(fut)
    if spot is None:
        return rf
    rs = rets(spot)
    cut = pd.to_datetime(fut_from_ms, unit="ms")
    early = rs[rs.index < cut]
    late = rf[rf.index >= cut]
    gap = rf[(rf.index < cut) & ~rf.index.isin(early.index)]
    return pd.concat([early, gap, late]).sort_index().dropna()


def daily_funding(
    rows: Sequence[tuple[int, float]], index: pd.DatetimeIndex, from_ms: int
) -> pd.Series:
    """그날(UTC) 정산된 펀딩률 합 — `from_ms` 이전은 0(현물 구간)."""
    if not rows:
        return pd.Series(0.0, index=index)
    f = pd.Series([r for _t, r in rows], index=pd.to_datetime([t for t, _r in rows], unit="ms"))
    day = f.groupby(f.index.floor("D")).sum().reindex(index, fill_value=0.0)
    day[day.index < pd.to_datetime(from_ms, unit="ms")] = 0.0
    return day


def _load_1d(stores: Sequence[OhlcvStore], sym: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    for store in stores:
        f = store.load(sym, "1d", start_ms=start_ms, end_ms=end_ms)
        if "closed" in f.columns:
            f = f[f["closed"].astype(bool)]
        if len(f):
            f = f.sort_values("open_time")
            return pd.DataFrame(
                {
                    "high": f["high"].to_numpy(float),
                    "low": f["low"].to_numpy(float),
                    "close": f["close"].to_numpy(float),
                },
                index=pd.to_datetime(f["open_time"].to_numpy(np.int64), unit="ms"),
            )
    return pd.DataFrame(columns=["high", "low", "close"])


def daily_bars(sym: str, fut_from_ms: int) -> pd.DataFrame:
    """`m.setting`과 같은 저장소 순서로 일봉 고·저·종을 읽어 (ret, low_r, high_r)를 낸다.

    현물은 `fut_from_ms` 이전, 선물은 이후 — 각 시장 안에서 전날 종가 대비로 재므로 이음매
    점프가 없다.
    """
    spot_st = [
        OhlcvStore(sk.root_db_path(m438.DEFAULT_ROOTS / "stress")),
        OhlcvStore(sk.root_db_path(u.SPOT_ROOT)),
    ]
    fut_st = [OhlcvStore(harness.DB_PATH), OhlcvStore(sk.root_db_path(u.FUT_ROOT))]

    def rel(b: pd.DataFrame) -> pd.DataFrame:
        prev = b["close"].shift(1)
        return pd.DataFrame(
            {
                "ret": b["close"] / prev - 1.0,
                "low_r": b["low"] / prev - 1.0,
                "high_r": b["high"] / prev - 1.0,
            }
        )

    fut = rel(_load_1d(fut_st, sym, u.ms("2019-06-01"), u.ms(harness.DEFAULT_END)))
    spot = rel(_load_1d(spot_st, sym, u.ms("2017-01-01"), u.ms(m438.STRESS_END)))
    cut = pd.to_datetime(fut_from_ms, unit="ms")
    early = spot[spot.index < cut]
    late = fut[fut.index >= cut]
    gap = fut[(fut.index < cut) & ~fut.index.isin(early.index)]
    return pd.concat([early, gap, late]).sort_index().dropna()


@dataclass(frozen=True)
class ArmDaily:
    ret: pd.Series
    low: pd.Series
    """그날 가장 낮은 평가액 ÷ 전날 종가 − 1."""
    risk: float


def arm_daily(built: u.Markets) -> ArmDaily:
    """WAN-448 측정 α · 거래대금 상위 31 · 캐리 없음 · MDD 35% 맞춤 크기의 일 수익."""
    s = m.setting(built)
    top = s.rotations(lambda d, t: liq.select_liquidity(d, t, top=True))
    smk, _ = m.apply_rotation(s.spot_pool, top[0], force_close=False)
    fmk, _ = m.apply_rotation(s.fut_pool, top[1], force_close=False)
    pl = {
        f: (c.place_arm(smk, c.ARM_B, f, in_book=True), c.place_arm(fmk, c.ARM_B, f, in_book=True))
        for f in c.FILLS
    }
    tk = pd.read_csv(st.TICKS_CSV)
    tk = tk[tk["ok"]]
    al = {k: float(tk[tk["stratum"] == k]["alpha"].mean()) for k in (st.CRASH, st.NORMAL)}
    sp, fu = (
        st.blended_trades(pl["stop"][k], pl["bar_low"][k], st.alphas_for(pl["stop"][k], mk, al))
        for k, mk in ((0, smk), (1, fmk))
    )
    risk = c.fit_risk(lambda x: c.chain(sp, fu, x)[1], target=c.MDD_TARGET)
    a = w.mtm_path(sp, risk=risk)
    b = w.mtm_path(fu, risk=risk)
    scale = float(a.mtm_close[-1])
    t = pd.to_datetime(np.concatenate([fc._grid(a), fc._grid(b)]), unit="ms")
    close = pd.Series(np.concatenate([a.mtm_close, b.mtm_close * scale]), index=t)
    low = pd.Series(np.concatenate([a.mtm_low, b.mtm_low * scale]), index=t)
    day_close = close.resample("D").last()
    day_low = low.resample("D").min()
    start = pd.date_range(c.WINDOW_START, day_close.index[0], freq="D", inclusive="left")
    day_close = pd.concat([pd.Series(1.0, index=start), day_close]).ffill()
    day_low = pd.concat([pd.Series(1.0, index=start), day_low]).fillna(day_close)
    prev = day_close.shift(1).fillna(1.0)
    return ArmDaily(day_close / prev - 1.0, day_low / prev - 1.0, risk)


def combine(
    ra: pd.Series,
    la: pd.Series,
    rc: pd.Series,
    lc: pd.Series,
    k: float,
    lam: float,
    carry: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series]:
    """합성 자본 곡선(종가 · 일 저가) — 같은 자본에 두 엔진을 겹친다.

    일 저가는 두 엔진의 그날 최악이 **동시에** 왔다고 본다(보수적). `carry`는 자본 전체에 곱해지는
    일 배율(캐리 지수의 하루 변화 − 1)이고 레버리지 λ와 무관하다(WAN-447 정의).
    """
    r = lam * (ra + k * rc)
    lo = lam * (la + k * lc)
    if carry is not None:
        r = (1.0 + r) * (1.0 + carry) - 1.0
        lo = (1.0 + lo) * (1.0 + carry) - 1.0
    eq = (1.0 + r).cumprod()
    low_eq = eq.shift(1).fillna(1.0) * (1.0 + lo)
    return eq, low_eq


def mdd_of(eq: pd.Series, low_eq: pd.Series) -> float:
    peak = np.maximum.accumulate(np.maximum(eq.to_numpy(), 1.0))
    return float((1.0 - low_eq.to_numpy() / peak).max())


def fit_lambda(
    ra: pd.Series,
    la: pd.Series,
    rc: pd.Series,
    lc: pd.Series,
    k: float,
    target: float,
    carry: pd.Series | None = None,
) -> float:
    lo, hi = 0.01, 20.0
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if mdd_of(*combine(ra, la, rc, lc, k, mid, carry)) <= target:
            lo = mid
        else:
            hi = mid
    return lo


def yearly(eq: pd.Series) -> pd.Series:
    ye = eq.resample("YE").last()
    prev = ye.shift(1).fillna(1.0)
    out = ye / prev - 1.0
    out.index = out.index.year
    return out


def verdict(yr: pd.Series) -> tuple[bool, float, int]:
    full = yr.reindex(list(YEARS))
    if full.isna().any():
        raise ValueError("판정 연도에 빈 해가 있다")
    med = float(full.median())
    neg = int((full < 0).sum())
    return med >= TARGET_MEDIAN and neg <= MAX_NEGATIVE_YEARS, med, neg


def _to_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    """해상도(ns/us/ms/s)와 무관하게 epoch ms — `asi8 // 1e6`은 해상도가 ns가 아니면 조용히 "
    "틀린다."""
    return np.asarray(idx.as_unit("ms").asi8, dtype=np.int64)


def carry_daily(
    rows: Mapping[str, Sequence[tuple[int, float]]], idx: pd.DatetimeIndex
) -> pd.Series:
    """WAN-447 캐리 50%(BTC·ETH 반반 · 정산된 펀딩만)의 일 배율 − 1 · 진입 비용은 첫날에 한 번."""
    fund = fc.basket({s: rows[s] for s in ASSETS if rows.get(s)})
    ends = _to_ms(idx + pd.Timedelta(days=1))
    ci = pd.Series(
        fc.carry_index(fund, np.asarray(ends, dtype=np.int64), CARRY_FRACTION), index=idx
    )
    out = ci / ci.shift(1).fillna(1.0) - 1.0
    out.iloc[0] = (1.0 + out.iloc[0]) * (1.0 - CARRY_FRACTION * fc.CARRY_COST) - 1.0
    # 하드 제로 경보(WAN-367) — 캐리가 한 번도 안 움직였으면 시각 단위가 어긋난 것이다.
    if float(np.abs(out.iloc[1:]).max()) == 0.0:
        raise ValueError("캐리 일 배율이 전부 0 — 펀딩 시각과 날짜 격자가 안 맞는다")
    return out


@dataclass(frozen=True)
class Variant:
    name: str
    k: float
    lam: float
    mdd: float
    mdd_day: str
    yearly: pd.Series
    eq: pd.Series


def fit_variant(
    name: str,
    ra: pd.Series,
    la: pd.Series,
    rc: pd.Series,
    lc: pd.Series,
    front: np.ndarray,
    carry: pd.Series | None = None,
) -> Variant:
    """k = 앞구간 일 변동성 일치(팔 ÷ 엔진 C) · λ = 전체 MDD 35%(일 저가) 맞춤 — 착수 전 고정 "
    "규칙."""
    k = float(ra[front].std() / rc[front].std())
    lam = fit_lambda(ra, la, rc, lc, k, c.MDD_TARGET, carry)
    eq, low_eq = combine(ra, la, rc, lc, k, lam, carry)
    peak = np.maximum.accumulate(np.maximum(eq.to_numpy(), 1.0))
    dd = 1.0 - low_eq.to_numpy() / peak
    day = str(eq.index[int(np.argmax(dd))].date())
    return Variant(name, k, lam, float(dd.max()), day, yearly(eq), eq)


def run(built: u.Markets) -> tuple[pd.DataFrame, list[Variant], list[str], dict[str, float]]:
    t0 = time.monotonic()
    arm = arm_daily(built)
    s = m.setting(built)
    with fc.FUNDING_CACHE.open("rb") as fh:
        rows, _fetched = pickle.load(fh)
    fut_from = u.ms(FUT_FROM)
    notes: list[str] = []
    engines: dict[str, EngineDaily] = {}
    worst_ret_gap = 0.0
    for sym in ASSETS:
        bars = daily_bars(sym, fut_from)
        bars = bars[bars.index >= pd.Timestamp("2017-01-01")]
        # 검산 — 직접 읽은 일봉 수익 ≡ `m.setting`이 쓰는 일봉 수익(같은 저장소 · 같은 이음매).
        ref = daily_returns(s.spot_d.get(sym), s.fut_d[sym], fut_from)
        ref = ref[ref.index >= pd.Timestamp("2017-01-01")]
        common = bars.index.intersection(ref.index)
        if len(common) < 0.99 * len(ref):
            raise ValueError(f"{sym}: 일봉 고·저 로드가 기준 수익과 날짜가 안 맞는다")
        worst_ret_gap = max(worst_ret_gap, float((bars["ret"][common] - ref[common]).abs().max()))
        f = daily_funding(rows.get(sym, []), bars.index, fut_from)
        engines[sym] = tsmom_returns(bars["ret"], f, bars["low_r"], bars["high_r"])
        engines[sym + "#close"] = tsmom_returns(bars["ret"], f)
    if worst_ret_gap > CHECK_TOL:
        raise ValueError(f"일봉 수익 검산 실패: 최대 차 {worst_ret_gap:.2e}")
    notes.append(
        "검산 — 고·저를 함께 읽은 일봉 수익 ≡ `m.setting` 일봉 수익(BTC·ETH 최대 차 "
        f"{worst_ret_gap:.1e})"
    )

    idx = arm.ret.index[arm.ret.index <= pd.Timestamp(harness.DEFAULT_END)]
    ra, la = arm.ret.reindex(idx).fillna(0.0), arm.low.reindex(idx).fillna(0.0)
    front = np.asarray(idx < pd.Timestamp(FRONT_END))

    def basket(keys: Sequence[str]) -> tuple[pd.Series, pd.Series]:
        r = pd.concat([engines[k].ret for k in keys], axis=1).mean(axis=1)
        lo = pd.concat([engines[k].low for k in keys], axis=1).mean(axis=1)
        return r.reindex(idx).fillna(0.0), lo.reindex(idx).fillna(0.0)

    rc, lc = basket(list(ASSETS))
    rc0, lc0 = basket([a + "#close" for a in ASSETS])
    carry = carry_daily(rows, idx)
    variants = [
        fit_variant(VARIANT_MAIN, ra, la, rc, lc, front),
        fit_variant(VARIANT_CLOSE, ra, la, rc0, lc0, front),
        fit_variant(VARIANT_CARRY, ra, la, rc, lc, front, carry),
        fit_variant("엔진 C = BTC만", ra, la, *basket([ASSETS[0]]), front),
        fit_variant("엔진 C = ETH만", ra, la, *basket([ASSETS[1]]), front),
    ]
    arm_eq, arm_low = combine(ra, la, rc, lc, 0.0, 1.0)
    arm_mdd = mdd_of(arm_eq, arm_low)
    notes.append(
        f"검산 — 팔 단독을 일 격자로 다시 잰 MDD {arm_mdd:.1%}(1분 평가손 맞춤 35% · 일 격자가 "
        "더 굵다)"
    )
    c_eq = (1.0 + rc).cumprod()
    c_low = c_eq.shift(1).fillna(1.0) * (1.0 + lc)
    main = variants[0]
    yrs = c._years(u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END))
    nums = {
        "arm_risk": arm.risk,
        "corr": float(ra.corr(rc)),
        "corr_front": float(ra[front].corr(rc[front])),
        "corr_back": float(ra[~front].corr(rc[~front])),
        "combo_cagr": c.cagr(float(main.eq.iloc[-1]) - 1.0, yrs),
        "c_cagr": c.cagr(float(c_eq.iloc[-1]) - 1.0, yrs),
        "c_mdd": mdd_of(c_eq, c_low),
        "arm_cagr": c.cagr(float(arm_eq.iloc[-1]) - 1.0, yrs),
        "arm_mdd": arm_mdd,
    }
    frame = pd.DataFrame(
        {
            "arm_ret": ra,
            "arm_low": la,
            "c_ret": rc,
            "c_low": lc,
            "carry": carry,
            "combo_eq": main.eq,
        }
    )
    yr = pd.DataFrame({"팔 단독": yearly(arm_eq), "엔진 C 단독": yearly(c_eq), "합성": main.yearly})
    frame.attrs["yearly"] = yr
    notes.append(f"측정 {time.monotonic() - t0:.0f}초")
    return frame, variants, notes, nums


def render(
    yr: pd.DataFrame, variants: Sequence[Variant], nums: dict[str, float], notes: Sequence[str]
) -> str:
    main = variants[0]
    ok, med, neg = verdict(yr["합성"])
    out = [
        "# WAN-449 — 두 번째 엔진(시계열 모멘텀) 합성 · 해마다",
        "",
        "> 팔 = WAN-448 측정 체결 · 거래대금 상위 31 · 캐리 없음. 엔진 C = BTC·ETH 시계열 모멘텀"
        "(교과서 설정 · 튜닝 없음). 합성은 같은 자본에 겹치고 MDD 35%(일 저가 — 두 엔진의 그날 "
        "최악이 "
        "동시에 온다고 본다)에 맞춘다.",
        "",
        f"## 판정 — **{'해결' if ok else '미해결'}** "
        f"(2018~2025 해마다 중앙 {med:+.1%} · 마이너스 {neg}해 · 기준 중앙 ≥ 20% · 마이너스 ≤ 1)",
        "",
        "| 연도 | 팔 단독(MDD 35% 크기) | 엔진 C 단독(연 20% 변동성) | **합성(MDD 35%)** |",
        "|---|--:|--:|--:|",
    ]
    for y, row in yr.iterrows():
        tag = "(부분)" if int(str(y)) > max(YEARS) else ""
        out.append(
            f"| {y}{tag} | {row['팔 단독']:+.1%} | {row['엔진 C 단독']:+.1%} | "
            f"**{row['합성']:+.1%}** |"
        )
    out += [
        "",
        f"* 8.6년 연환산: 팔 단독 {nums['arm_cagr']:+.1%}(일 격자 MDD {nums['arm_mdd']:.1%}) · "
        f"엔진 C 단독 {nums['c_cagr']:+.1%}(MDD {nums['c_mdd']:.1%}) · 합성 "
        f"{nums['combo_cagr']:+.1%}"
        f"(MDD {main.mdd:.1%} · 최저점 {main.mdd_day})",
        f"* 두 엔진 일 수익 상관 {nums['corr']:+.2f}(앞구간 {nums['corr_front']:+.2f} · 뒷구간 "
        f"{nums['corr_back']:+.2f}) · 합성 비율 k = {main.k:.3f}(앞구간 변동성 일치) · 전체 배율 "
        f"λ = {main.lam:.3f} · 팔 거래당 리스크 {nums['arm_risk']:.2%} × λ",
        "",
        "## 참고 판 — 같은 규칙(k 앞구간 · λ MDD 35%)으로 한 축씩",
        "",
        "| 판 | k | λ | MDD(최저점) | 2018~2025 중앙 | 마이너스 해 | 판정 | "
        + " | ".join(str(y) for y in YEARS)
        + " |",
        "|---|--:|--:|--:|--:|--:|---|" + "--:|" * len(YEARS),
    ]
    for v in variants:
        v_ok, v_med, v_neg = verdict(v.yearly)
        cells = " | ".join(f"{v.yearly.get(y, float('nan')):+.1%}" for y in YEARS)
        out.append(
            f"| {v.name} | {v.k:.3f} | {v.lam:.3f} | {v.mdd:.1%}({v.mdd_day}) | {v_med:+.1%} | "
            f"{v_neg} | {'해결' if v_ok else '미해결'} | {cells} |"
        )
    out += [
        "",
        "## 기록 · 한계",
        "",
        *[f"* {n}" for n in notes],
        "* 2020-09-15 이전 엔진 C는 현물 가격 · 펀딩 0 — 이후 선물 + 실제 펀딩(롱이 냄 · 숏이 "
        "받음).",
        "* 엔진 C의 일 중 낙폭은 일봉 고·저로 잰다(1분 경로 아님) — 「종가만」 판은 초안 "
        "근사이고 참고로만 싣는다.",
        "* 팔의 크기 변경을 일 수익의 선형 배율로 근사했다(복리 사이징의 경로 차이 무시 · λ ≈ "
        "0.9라 작다).",
        "* λ는 전체 창(뒷구간 포함)에서 맞춘다 — 이슈 규칙 그대로이고, 앞구간만으로 정한 것은 "
        "k 하나다.",
        "* 판정은 해마다 수익이지 거래당 실력이 아니다 · 팔은 `baseline` 위가 아니라 WAN-448 "
        "측정 체결 위다.",
        "",
        "**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 · 실거래 보류 "
        "유지(`ALPHABLOCK_LIVE_TRADING=false`).**",
        "",
    ]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    with m.MARKETS_PATH.open("rb") as fh:
        built = pickle.load(fh)
    frame, variants, notes, nums = run(built)
    yr = frame.attrs["yearly"]
    frame.to_csv(DAILY_CSV)
    SUMMARY_PATH.write_text(render(yr, variants, nums, notes))
    print(SUMMARY_PATH.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
