"""WAN-452 — 채택 후보(팔 + 추세추종)의 낙관 네 곳을 잰다.

묻는 것(사용자 「너무 좋은데?」 2026-10-14): WAN-449/450 채택 후보(스토캐스틱 팔 · 거래대금 상위
31 · WAN-448 측정 손절 체결 + 엔진 C(BTC·ETH 시계열 모멘텀) · 캐리 없음 · 합성 MDD 35%)가 아직 안
잰 낙관에 얼마나 기대고 있나.

* **§1 진입 체결(틱)** — 팔은 폭락 직후 알트를 **봉내 라이브 밴드 지정가**로 산다. 기록된 진입가
  `E`는 1분봉 종가로 밴드를 다시 낸 `L(종가)`다. 실제로는 밴드가 체결마다 움직이므로, 그 1분의
  체결을 시간순으로 훑어 `p ≤ L(p)`가 처음 성립하는 체결(WAN-348 팔 `band`의 고정점)에서 산다고
  보고 그 값을 `F`라 한다.
* **§2 팔 크기를 앞구간만으로** — 팔 거래당 리스크(2.85%)를 2018-01 ~ 2024-08-08 MDD 35%로만
  맞추고, k·λ도 앞구간만으로 맞춘 뒤 뒷구간을 본다.
* **§3 손절 규칙 구멍 · 격리 마진** — 손절 배수 2가 만드는 넓은 손절폭(≥ 50% · ≥ 100% = 손절가
  음수)을 세고 막는 방법 셋을 같은 자로 잰다. 격리 레버리지 2·3·5·10배에서 보유 중 청산선 도달을
  센다.
* **§4 15m 칸의 몫** — 15m을 뺀 9TF 판(신호 수는 남은 후보로 다시 센다).

## 착수 전에 못 박은 것(결과를 보고 옮기지 않는다)

* 토대 = WAN-449 정본(캐리 없음). 규칙·종목·문턱은 그대로다.
* §1 표본: 선물 구간 팔 거래를 진입 순간 BTC 24h(팔 B 폭락 판정과 같은 값)로 **폭락 / 평시** 층화,
  층마다 무작위 100건 · 시드 452(WAN-448: 135건 · 97파일 · 1.3GB · 386초를 보고 정했다).
* §1 판정 가능 = 그 분 체결이 있고 · 틱 저가 ≡ 저장 1분봉 저가 · **재구성 성공**(되살린 오더블록과
  밴드로 `L(그 1분 종가)`가 기록 진입가를 재현). 셋 중 하나라도 실패면 판정하지 않는다.
* §1 적용 진입가 차 `g`(진입가 대비 · 양수 = 더 비싸게 샀다):
  - 틱 고정점이 있으면 `g = (F − E)/E`. 그 분 안에서 `F` 이하로 인쇄된 체결량(고정점 체결 이후)이
    우리 수량보다 적으면 모자란 몫 `r`은 그 분 종가에 추격 매수한다고 보고 `r · (max(종가, F) −
    F)/E`를 더한다.
  - 틱 고정점이 없으면(엔진은 체결이라 했는데 틱으로는 안 닿음) 그 분 종가에 추격 매수 +
    테이커−메이커 수수료 차 2bp: `g = (max(종가, E) − E)/E + 0.0002`.
  - 층별 **평균** `g`를 그 층의 모든 팔 거래(현물 구간 포함 — 가정)에 얹는다. 손절가·수량은
    그대로라 net R 변화는 정확히 `−g · E / (E − 손절가)`다.
* 판정(참고 · 이슈 고정): §1 반영 후에도 2018~2025 해마다 중앙 ≥ 20% · 마이너스 ≤ 1이면 「해결
  유지」.
* §4 판정(참고 · 이슈 고정): 15m을 빼면 ① 8.6년 연환산이 낮아지고 ② 뒷구간 수익/MDD도 나빠지면
  「15m 유지 근거 있음」. 비교는 **둘 다 MDD 35%로 다시 맞춘 합성**(제외 판 팔 크기 재맞춤 · k·λ
  재맞춤)끼리다.

재현: `uv run python -m backtest.wan452_adopted_optimism --part ticks`(틱 받기 · 표본 CSV) →
`--part run`(전 절 · 약 2분) · 요약만 `--part summary`. 시장은 WAN-442 피클 · 펀딩 WAN-447 캐시 ·
손절 α WAN-448 CSV · 체결내역 `data/cache/wan348-aggtrades` · 유지증거금 표 `data/cache/wan452/`.

**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 · 실거래 보류
유지(`ALPHABLOCK_LIVE_TRADING=false`).**
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import pickle
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan348_same_minute_tp as s348
from backtest import wan424_stoch_ob_arm as a424  # noqa: F401 — 3h 주기 등록(register_3h)
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq
from backtest import wan448_stop_fill_ticks as st
from backtest import wan449_tsmom_second_engine as e449
from data import agg_trade_archive
from data import spot_klines as sk
from data.models import timeframe_to_ms
from execution.leverage import DEFAULT_MAINTENANCE_MARGIN_RATE

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
TICKS_CSV = REPORT_DIR / "wan452_entry_ticks.csv"
PANELS_CSV = REPORT_DIR / "wan452_panels.csv"
WIDTH_CSV = REPORT_DIR / "wan452_stop_width.csv"
LIQ_CSV = REPORT_DIR / "wan452_isolated_liquidation.csv"
NOTES_TXT = REPORT_DIR / "wan452_notes.txt"
SUMMARY_PATH = REPORT_DIR / "wan452_adopted_optimism_summary.md"
BRACKETS_JSON = REPO_ROOT / "data" / "cache" / "wan452" / "brackets.json"
BRACKETS_URL = "https://www.binance.com/bapi/futures/v1/friendly/future/common/brackets"

CRASH_SAMPLE = 100
NORMAL_SAMPLE = 100
SEED = 452
CHASE_FEE = 0.0002
"""추격 매수의 테이커(4bp) − 메이커(2bp) 수수료 차."""
WIDTH_LINES: tuple[float, ...] = (0.5, 1.0)
CLAMP_WIDTH = 0.5
LEVERAGES: tuple[int, ...] = (2, 3, 5, 10)
TF15 = "15m"
CHECK_TOL = 1e-9

CRASH = st.CRASH
NORMAL = st.NORMAL


# ---------------------------------------------------------------------------
# 채택 후보 — WAN-449 `arm_trades`와 같은 경로(시장까지 들고 있다)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Adopted:
    smk: c.Market
    fmk: c.Market
    stop_alpha: dict[str, float]
    sp: list[w.PlacedTrade]
    fu: list[w.PlacedTrade]
    risk: float


def stop_alphas() -> dict[str, float]:
    tk = pd.read_csv(st.TICKS_CSV)
    tk = tk[tk["ok"]]
    return {k: float(tk[tk["stratum"] == k]["alpha"].mean()) for k in (CRASH, NORMAL)}


def arm_from(
    smk: c.Market, fmk: c.Market, alpha: dict[str, float]
) -> tuple[list[w.PlacedTrade], list[w.PlacedTrade]]:
    """두 시장 → WAN-448 측정 α를 얹은 (현물 구간, 선물 구간) 팔 거래."""
    pl = {
        f: (c.place_arm(smk, c.ARM_B, f, in_book=True), c.place_arm(fmk, c.ARM_B, f, in_book=True))
        for f in c.FILLS
    }
    sp, fu = (
        st.blended_trades(pl["stop"][k], pl["bar_low"][k], st.alphas_for(pl["stop"][k], mk, alpha))
        for k, mk in ((0, smk), (1, fmk))
    )
    return sp, fu


def fit_arm(sp: Sequence[w.PlacedTrade], fu: Sequence[w.PlacedTrade]) -> float:
    return c.fit_risk(lambda x: c.chain(sp, fu, x)[1], target=c.MDD_TARGET)


def adopted(built: u.Markets) -> Adopted:
    s = m.setting(built)
    top = s.rotations(lambda d, t: liq.select_liquidity(d, t, top=True))
    smk, _ = m.apply_rotation(s.spot_pool, top[0], force_close=False)
    fmk, _ = m.apply_rotation(s.fut_pool, top[1], force_close=False)
    alpha = stop_alphas()
    sp, fu = arm_from(smk, fmk, alpha)
    return Adopted(smk, fmk, alpha, sp, fu, fit_arm(sp, fu))


def stratum_of(mk: c.Market) -> dict[tuple[str, str, int, float], str]:
    return {
        e.key: (CRASH if b <= c.CRASH_THRESHOLD else NORMAL)
        for e, b in zip(mk.entries, mk.btc_24h, strict=True)
    }


def trade_key(t: w.PlacedTrade) -> tuple[str, str, int, float]:
    return (t.symbol, t.timeframe, t.entry_time, t.entry_price)


# ---------------------------------------------------------------------------
# 합성 패널 — 같은 자(일 저가 MDD · 8.6년 · 앞/뒤)로 모든 판을 잰다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Engine:
    rc: pd.Series
    lc: pd.Series
    k: float
    lam: float


@dataclass(frozen=True)
class Panel:
    section: str
    name: str
    arm_risk: float
    k: float
    lam: float
    cagr: float
    mdd: float
    mdd_day: str
    front_return: float
    front_mdd: float
    back_return: float
    back_mdd: float
    back_cagr: float
    median: float
    negative: int
    yearly: dict[int, float]

    @property
    def back_ratio(self) -> float:
        return self.back_return / self.back_mdd if self.back_mdd > 0 else math.nan


def arm_series(
    sp: Sequence[w.PlacedTrade], fu: Sequence[w.PlacedTrade], risk: float
) -> tuple[pd.Series, pd.Series]:
    arm = e449.arm_daily_from(sp, fu, risk)
    idx = arm.ret.index[arm.ret.index <= pd.Timestamp(harness.DEFAULT_END)]
    return arm.ret.reindex(idx).fillna(0.0), arm.low.reindex(idx).fillna(0.0)


def front_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(idx < pd.Timestamp(e449.FRONT_END))


def fit_k(ra: pd.Series, rc: pd.Series) -> float:
    front = front_mask(ra.index)
    return float(ra[front].std() / rc[front].std())


def segment_stats(eq: pd.Series, low: pd.Series, mask: np.ndarray) -> tuple[float, float, float]:
    """(수익, MDD, 연환산) — 구간 시작 평가액을 1로 두고 그 구간 안에서만 고점을 센다."""
    pos = np.flatnonzero(mask)
    first = int(pos[0])
    base = float(eq.iloc[first - 1]) if first > 0 else 1.0
    e = eq.to_numpy()[mask] / base
    lo = low.to_numpy()[mask] / base
    peak = np.maximum.accumulate(np.maximum(e, 1.0))
    years = len(pos) / 365.25
    total = float(e[-1]) - 1.0
    return total, float((1.0 - lo / peak).max()), c.cagr(total, years)


def make_panel(
    section: str,
    name: str,
    ra: pd.Series,
    la: pd.Series,
    eng: Engine,
    *,
    k: float,
    lam: float,
    arm_risk: float,
) -> Panel:
    eq, low = e449.combine(
        ra, la, eng.rc.reindex(ra.index).fillna(0.0), eng.lc.reindex(ra.index).fillna(0.0), k, lam
    )
    peak = np.maximum.accumulate(np.maximum(eq.to_numpy(), 1.0))
    dd = 1.0 - low.to_numpy() / peak
    front = front_mask(eq.index)
    f_ret, f_mdd, _ = segment_stats(eq, low, front)
    b_ret, b_mdd, b_cagr = segment_stats(eq, low, ~front)
    yr = e449.yearly(eq)
    full = yr.reindex(list(e449.YEARS))
    years = c._years(u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END))
    return Panel(
        section=section,
        name=name,
        arm_risk=arm_risk,
        k=k,
        lam=lam,
        cagr=c.cagr(float(eq.iloc[-1]) - 1.0, years),
        mdd=float(dd.max()),
        mdd_day=str(eq.index[int(np.argmax(dd))].date()),
        front_return=f_ret,
        front_mdd=f_mdd,
        back_return=b_ret,
        back_mdd=b_mdd,
        back_cagr=b_cagr,
        median=float(full.median()),
        negative=int((full < 0).sum()),
        yearly={int(y): float(v) for y, v in yr.items()},
    )


def combo_fit(
    ra: pd.Series, la: pd.Series, eng: Engine, *, front_only: bool = False
) -> tuple[float, float]:
    """(k, λ) — k는 앞구간 변동성 일치 · λ는 MDD 35%(전체 또는 앞구간만)."""
    rc = eng.rc.reindex(ra.index).fillna(0.0)
    lc = eng.lc.reindex(ra.index).fillna(0.0)
    k = fit_k(ra, rc)
    if front_only:
        f = front_mask(ra.index)
        return k, e449.fit_lambda(ra[f], la[f], rc[f], lc[f], k, c.MDD_TARGET)
    return k, e449.fit_lambda(ra, la, rc, lc, k, c.MDD_TARGET)


def arm_alone_panel(
    section: str, name: str, ra: pd.Series, la: pd.Series, risk: float, eng: Engine
) -> Panel:
    return make_panel(section, name, ra, la, eng, k=0.0, lam=1.0, arm_risk=risk)


def panel_set(
    section: str,
    tag: str,
    sp: Sequence[w.PlacedTrade],
    fu: Sequence[w.PlacedTrade],
    eng: Engine,
    *,
    canon_risk: float,
    refit: bool,
) -> list[Panel]:
    """한 거래 목록의 패널들 — 팔 단독 · 합성(정본 k·λ 고정) · (재맞춤이면) 합성 재맞춤."""
    risk = fit_arm(sp, fu) if refit else canon_risk
    size = "크기 재맞춤" if refit else "정본 크기"
    ra, la = arm_series(sp, fu, risk)
    out = [
        arm_alone_panel(section, f"{tag} · 팔 단독 · {size}", ra, la, risk, eng),
        make_panel(
            section,
            f"{tag} · 합성 · {size} · k·λ 정본",
            ra,
            la,
            eng,
            k=eng.k,
            lam=eng.lam,
            arm_risk=risk,
        ),
    ]
    k, lam = combo_fit(ra, la, eng)
    out.append(
        make_panel(
            section, f"{tag} · 합성 · {size} · k·λ 재맞춤", ra, la, eng, k=k, lam=lam, arm_risk=risk
        )
    )
    return out


def engine_c(built: u.Markets, ad: Adopted) -> tuple[Engine, pd.DataFrame, list[str]]:
    """엔진 C 일 수익 + 정본 k·λ — WAN-449 `run`을 그대로 부른다(두 벌로 갈라지지 않게)."""
    frame, variants, _notes, _nums = e449.run(built, e449.arm_daily_from(ad.sp, ad.fu, ad.risk))
    main = variants[0]
    eng = Engine(frame["c_ret"], frame["c_low"], main.k, main.lam)
    notes: list[str] = []
    pub = pd.read_csv(e449.DAILY_CSV, index_col=0, parse_dates=True)
    gap = float((pub["combo_eq"] - frame["combo_eq"].reindex(pub.index)).abs().max())
    notes.append(f"검산 — 정본 재구성 ≡ 공개 `wan449_daily.csv` 합성 곡선(최대 차 {gap:.1e})")
    if gap > CHECK_TOL:
        raise AssertionError(f"WAN-449 정본 재현 실패: {gap:.2e}")
    return eng, frame, notes


# ---------------------------------------------------------------------------
# §1 진입 체결 — 틱
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryRow:
    symbol: str
    timeframe: str
    entry_time: int
    entry_price: float
    ob_stop: float
    arm_stop: float
    stratum: str
    quantity: float


def entry_rows(ad: Adopted) -> list[EntryRow]:
    strata = stratum_of(ad.fmk)
    index = {e.key: e for e in ad.fmk.entries}
    out: list[EntryRow] = []
    for t in ad.fu:
        e = index[trade_key(t)]
        out.append(
            EntryRow(
                symbol=t.symbol,
                timeframe=t.timeframe,
                entry_time=t.entry_time,
                entry_price=t.entry_price,
                ob_stop=float(e.cand.stop_price),
                arm_stop=t.stop_price,
                stratum=strata[trade_key(t)],
                quantity=st.EQUITY_REF * ad.risk * t.size / (t.entry_price - t.stop_price),
            )
        )
    return out


def sample_entries(rows: Sequence[EntryRow], *, seed: int = SEED) -> list[EntryRow]:
    rng = random.Random(seed)
    out: list[EntryRow] = []
    for stratum, want in ((CRASH, CRASH_SAMPLE), (NORMAL, NORMAL_SAMPLE)):
        pool = sorted(
            (r for r in rows if r.stratum == stratum),
            key=lambda r: (r.symbol, r.timeframe, r.entry_time, r.entry_price),
        )
        rng.shuffle(pool)
        out += pool[:want]
    return out


def fixed_point(
    ticks: Sequence[agg_trade_archive.Tick], limit_at: Callable[[float], float | None]
) -> tuple[int | None, float | None]:
    """`p ≤ L(p)`가 처음 성립하는 체결의 (인덱스, 지정가)."""
    for i, tick in enumerate(ticks):
        lim = limit_at(tick.price)
        if lim is not None and tick.price <= lim:
            return i, lim
    return None, None


def entry_gap(
    ticks: Sequence[agg_trade_archive.Tick],
    limit_at: Callable[[float], float | None],
    *,
    entry_price: float,
    quantity: float,
    minute_close: float,
) -> dict[str, float]:
    """§0 규칙대로의 진입가 차 `g`와 그 구성."""
    i, fill = fixed_point(ticks, limit_at)
    if i is None or fill is None:
        g = (max(minute_close, entry_price) - entry_price) / entry_price + CHASE_FEE
        return {"tick_fill": math.nan, "fill_frac": 0.0, "gap": g, "touched": 0.0}
    vol = sum(t.qty for t in ticks[i:] if t.price <= fill)
    frac = min(1.0, vol / quantity) if quantity > 0 else 1.0
    g = (fill - entry_price) / entry_price
    g += (1.0 - frac) * (max(minute_close, fill) - fill) / entry_price
    return {"tick_fill": fill, "fill_frac": frac, "gap": g, "touched": 1.0}


def _stores() -> list[str]:
    return [harness.DB_PATH, str(sk.root_db_path(u.FUT_ROOT))]


def load_cell_any(symbol: str, timeframe: str) -> s348.CellBars:
    for db in _stores():
        cell = s348.load_cell(symbol, timeframe, db_path=db)
        if cell.times:
            return cell
    return s348.CellBars([], [], [])


def minute_bar_any(symbol: str, minute: int) -> tuple[float, float, float] | None:
    for db in _stores():
        bar = s348.load_minute_bar(symbol, minute, db_path=db)
        if bar is not None:
            return bar
    return None


def measure_entries(
    sample: Sequence[EntryRow], *, log: bool = True
) -> tuple[pd.DataFrame, dict[str, float]]:
    s348.TF_MS.setdefault("3h", timeframe_to_ms("3h"))
    cells: dict[tuple[str, str], s348.CellBars] = {}
    by_day: dict[tuple[str, str], list[EntryRow]] = {}
    for r in sample:
        by_day.setdefault((r.symbol, agg_trade_archive.day_of(r.entry_time)), []).append(r)
    stats = {"files": 0.0, "bytes": 0.0, "seconds": 0.0, "failed": 0.0}
    out: list[dict[str, object]] = []
    for (symbol, day), rows in sorted(by_day.items()):
        fetch = agg_trade_archive.fetch_day(symbol, day)
        stats["files"] += 1
        stats["bytes"] += fetch.size_bytes
        stats["seconds"] += fetch.seconds
        minutes = (
            agg_trade_archive.minutes_ticks(fetch.path, [r.entry_time for r in rows])
            if fetch.ok and fetch.path is not None
            else {}
        )
        if not fetch.ok:
            stats["failed"] += 1
        for r in rows:
            rec: dict[str, object] = {**dataclasses.asdict(r), "ok": False, "note": ""}
            ticks = minutes.get(r.entry_time, [])
            bar = minute_bar_any(r.symbol, r.entry_time)
            if not ticks or bar is None:
                rec["note"] = "틱 없음" if bar is not None else "1분봉 없음"
                out.append(rec)
                continue
            high, low, close = bar
            tick_low = min(t.price for t in ticks)
            rec.update(
                tick_low=tick_low,
                bar_low=low,
                minute_close=close,
                below=float(any(t.price < r.entry_price for t in ticks)),
            )
            if abs(tick_low - low) > 1e-9 * low:
                rec["note"] = "틱 저가 ≠ 저장 1분봉 저가"
                out.append(rec)
                continue
            key = (r.symbol, r.timeframe)
            if key not in cells:
                cells[key] = load_cell_any(*key)
            target = s348.Target(
                r.symbol,
                r.timeframe,
                r.entry_time,
                r.entry_price,
                r.ob_stop,
                math.nan,
                False,
                math.nan,
                math.nan,
            )
            recon = s348.reconstruct(target, cells[key], close)
            if recon is None:
                rec["note"] = "재구성 실패"
                out.append(rec)
                continue
            rec.update(
                entry_gap(
                    ticks,
                    recon.limit_at,
                    entry_price=r.entry_price,
                    quantity=r.quantity,
                    minute_close=close,
                )
            )
            rec["ok"] = True
            out.append(rec)
        if log:
            print(f"[wan452] {symbol} {day}: {len(rows)}건", flush=True)
    frame = pd.DataFrame(out)
    if "gap" in frame:
        frame["gap_bp"] = frame["gap"] * 10_000.0
    return frame, stats


def stratum_gaps(frame: pd.DataFrame) -> dict[str, float]:
    ok = frame[frame["ok"].astype(bool)]
    out: dict[str, float] = {}
    for s in (CRASH, NORMAL):
        v = ok[ok["stratum"] == s]["gap"]
        if v.empty:
            raise ValueError(f"{s} 층에 판정된 진입이 없다")
        out[s] = float(v.mean())
    return out


def apply_entry_gap(
    trades: Sequence[w.PlacedTrade], mk: c.Market, gaps: dict[str, float]
) -> list[w.PlacedTrade]:
    strata = stratum_of(mk)
    out = []
    for t in trades:
        g = gaps[strata[trade_key(t)]]
        out.append(
            dataclasses.replace(
                t, net_r=t.net_r - g * t.entry_price / (t.entry_price - t.stop_price)
            )
        )
    return out


# ---------------------------------------------------------------------------
# §3 손절폭 · 격리 마진
# ---------------------------------------------------------------------------


def width(t: w.PlacedTrade) -> float:
    return (t.entry_price - t.stop_price) / t.entry_price


def entry_width(e: w.ArmEntry) -> float:
    ep = float(e.cand.entry_price)
    return c.RULES.stop_multiple * (ep - float(e.cand.stop_price)) / ep


def skip_wide(mk: c.Market, line: float) -> c.Market:
    keep = [i for i, e in enumerate(mk.entries) if entry_width(e) < line]
    return _subset(mk, keep)


def _subset(
    mk: c.Market, keep: Sequence[int], entries: Sequence[w.ArmEntry] | None = None
) -> c.Market:
    ents = list(entries) if entries is not None else [mk.entries[i] for i in keep]
    return c.Market(
        mk.name,
        mk.payloads,
        ents,
        w.signal_counts([int(e.cand.entry_time) for e in ents]),
        [mk.btc_24h[i] for i in keep],
        [mk.vol_ratio[i] for i in keep],
    )


def clamp_wide(mk: c.Market, cap: float) -> c.Market:
    """손절폭을 `cap`에서 자른다 — 오더블록 손절가를 올려 배수 적용 뒤 손절가가 E·(1−cap)이 되게."""
    ents = []
    for e in mk.entries:
        if entry_width(e) > cap:
            ep = float(e.cand.entry_price)
            s_ob = ep - cap * ep / c.RULES.stop_multiple
            e = dataclasses.replace(e, cand=dataclasses.replace(e.cand, stop_price=s_ob))
        ents.append(e)
    # 신호 수는 후보 집합이 같으므로 그대로다.
    return c.Market(
        mk.name, mk.payloads, ents, list(mk.counts), list(mk.btc_24h), list(mk.vol_ratio)
    )


def load_brackets(path: Path = BRACKETS_JSON) -> dict[str, list[dict[str, float]]]:
    if not path.exists():
        import urllib.request

        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(BRACKETS_URL, timeout=30) as resp:  # noqa: S310 — 고정 공개 URL
            path.write_bytes(resp.read())
    data = json.loads(path.read_text())["data"]["brackets"]
    return {b["symbol"]: b["riskBrackets"] for b in data}


def bracket_for(brackets: Sequence[dict[str, float]], notional: float) -> tuple[float, float, int]:
    """(유지증거금률, 누적 차감액, 최대 레버리지) — 명목이 속한 구간."""
    for b in brackets:
        if b["bracketNotionalFloor"] <= notional < b["bracketNotionalCap"]:
            return (
                float(b["bracketMaintenanceMarginRate"]),
                float(b["cumFastMaintenanceAmount"]),
                int(b["maxOpenPosLeverage"]),
            )
    b = brackets[-1]
    return (
        float(b["bracketMaintenanceMarginRate"]),
        float(b["cumFastMaintenanceAmount"]),
        int(b["maxOpenPosLeverage"]),
    )


def isolated_liq_price(entry: float, qty: float, leverage: float, mmr: float, cum: float) -> float:
    """격리 롱 청산가 — (E(1 − 1/L) − cum/Q) / (1 − MMR)(바이낸스 공식 · 수수료 무시)."""
    return (entry * (1.0 - 1.0 / leverage) - cum / qty) / (1.0 - mmr)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def run_ticks(built: u.Markets) -> list[str]:
    t0 = time.monotonic()
    ad = adopted(built)
    rows = entry_rows(ad)
    sample = sample_entries(rows)
    frame, stats = measure_entries(sample)
    frame.to_csv(TICKS_CSV, index=False)
    ok = frame[frame["ok"].astype(bool)]
    notes = [
        f"선물 팔 거래 {len(rows)}건(폭락 {sum(r.stratum == CRASH for r in rows)} · 평시 "
        f"{sum(r.stratum == NORMAL for r in rows)}) · 표본 {len(sample)}건 · 판정 {len(ok)}건",
        f"체결내역 파일 {int(stats['files'])}개 · {stats['bytes'] / 1e6:.0f}MB · "
        f"받기 실패 {int(stats['failed'])} · 받는 시간 {stats['seconds']:.0f}초",
        f"틱 측정 {time.monotonic() - t0:.0f}초",
    ]
    return notes


def run_all(built: u.Markets) -> tuple[list[Panel], pd.DataFrame, pd.DataFrame, list[str]]:
    t0 = time.monotonic()
    ad = adopted(built)
    eng, _frame, notes = engine_c(built, ad)
    notes.append(
        f"정본 — 팔 거래당 리스크 {ad.risk:.4%} · k {eng.k:.3f} · λ {eng.lam:.3f} · "
        f"팔 거래 {len(ad.sp) + len(ad.fu)}건(현물 {len(ad.sp)} · 선물 {len(ad.fu)})"
    )
    panels: list[Panel] = []
    ra, la = arm_series(ad.sp, ad.fu, ad.risk)
    canon = [
        arm_alone_panel("정본", "정본 · 팔 단독", ra, la, ad.risk, eng),
        make_panel("정본", "정본 · 합성", ra, la, eng, k=eng.k, lam=eng.lam, arm_risk=ad.risk),
    ]
    panels += canon

    # §1 — 진입 체결 차를 얹은 판
    ticks = pd.read_csv(TICKS_CSV)
    gaps = stratum_gaps(ticks)
    sp1 = apply_entry_gap(ad.sp, ad.smk, gaps)
    fu1 = apply_entry_gap(ad.fu, ad.fmk, gaps)
    zero = apply_entry_gap(ad.fu, ad.fmk, {CRASH: 0.0, NORMAL: 0.0})
    z_gap = max(abs(a.net_r - b.net_r) for a, b in zip(zero, ad.fu, strict=True))
    notes.append(f"검산 — 진입가 차 0을 얹은 판 ≡ 정본 net R(최대 차 {z_gap:.1e})")
    notes.append(
        f"§1 층별 평균 진입가 차: 폭락 {gaps[CRASH] * 1e4:+.2f}bp · "
        f"평시 {gaps[NORMAL] * 1e4:+.2f}bp"
    )
    for refit in (False, True):
        panels += panel_set("§1", "진입 틱 반영", sp1, fu1, eng, canon_risk=ad.risk, refit=refit)

    # §2 — 팔 크기 · k·λ를 앞구간만으로
    fu_front = [t for t in ad.fu if not t.back]
    risk_f = c.fit_risk(lambda x: c.chain(ad.sp, fu_front, x)[1], target=c.MDD_TARGET)
    ra2, la2 = arm_series(ad.sp, ad.fu, risk_f)
    k2, lam2 = combo_fit(ra2, la2, eng, front_only=True)
    panels += [
        arm_alone_panel("§2", "앞구간 크기 · 팔 단독", ra2, la2, risk_f, eng),
        make_panel(
            "§2", "앞구간 크기 · 합성(k·λ 앞구간)", ra2, la2, eng, k=k2, lam=lam2, arm_risk=risk_f
        ),
    ]
    back_start = min(t.entry_time for t in ad.fu if t.back)
    notes.append(
        f"§2 — 앞구간만 맞춘 팔 크기 {risk_f:.4%}(정본 {ad.risk:.4%}) · k {k2:.3f} · "
        f"λ {lam2:.3f} · 뒷구간 첫 진입 {pd.to_datetime(back_start, unit='ms').date()}"
    )

    # §3 — 손절폭 분포 · 막는 방법 · 격리 마진
    allt = [*ad.sp, *ad.fu]
    widths = pd.DataFrame(
        {
            "symbol": [t.symbol for t in allt],
            "timeframe": [t.timeframe for t in allt],
            "entry_time": [t.entry_time for t in allt],
            "width": [width(t) for t in allt],
            "net_r": [t.net_r for t in allt],
            "stopped": [t.stopped for t in allt],
            "back": [t.back for t in allt],
            "spot": [True] * len(ad.sp) + [False] * len(ad.fu),
        }
    )
    guards: list[tuple[str, Callable[[c.Market], c.Market]]] = [
        ("손절폭 ≥ 100% 건너뛰기", lambda mk: skip_wide(mk, 1.0)),
        ("손절폭 ≥ 50% 건너뛰기", lambda mk: skip_wide(mk, 0.5)),
        (f"손절폭 {CLAMP_WIDTH:.0%} 상한(손절가 끌어올림)", lambda mk: clamp_wide(mk, CLAMP_WIDTH)),
    ]
    for name, mk_fn in guards:
        spx, fux = arm_from(mk_fn(ad.smk), mk_fn(ad.fmk), ad.stop_alpha)
        for refit in (False, True):
            panels += panel_set("§3", name, spx, fux, eng, canon_risk=ad.risk, refit=refit)
    liq_rows = isolated_table(ad)

    # §4 — 15m 제외
    spx, fux = arm_from(ad.smk.without(TF15), ad.fmk.without(TF15), ad.stop_alpha)
    notes.append(
        f"§4 — 15m 제외 판 팔 거래 {len(spx) + len(fux)}건(정본 "
        f"{len(ad.sp) + len(ad.fu)}건) · 15m 후보 현물 "
        f"{sum(e.timeframe == TF15 for e in ad.smk.entries)} · 선물 "
        f"{sum(e.timeframe == TF15 for e in ad.fmk.entries)}"
    )
    for refit in (False, True):
        panels += panel_set("§4", "15m 제외", spx, fux, eng, canon_risk=ad.risk, refit=refit)
    t15 = [t for t in allt if t.timeframe == TF15]
    for seg, sel in (("앞", [t for t in t15 if not t.back]), ("뒤", [t for t in t15 if t.back])):
        nr = [t.net_r for t in sel]
        notes.append(
            f"§4 — 정본 15m 거래 {seg}구간 {len(sel)}건 · net R 평균 "
            f"{(sum(nr) / len(nr)) if nr else math.nan:+.3f} · 합 {sum(nr):+.1f}R"
        )
    notes.append(f"측정 {time.monotonic() - t0:.0f}초")
    return panels, widths, liq_rows, notes


def isolated_table(ad: Adopted) -> pd.DataFrame:
    brackets = load_brackets()
    index = {e.key: e for e in (*ad.smk.entries, *ad.fmk.entries)}
    rows: list[dict[str, object]] = []
    for t in (*ad.sp, *ad.fu):
        e = index[trade_key(t)]
        lows = e.lows[e.times <= t.exit_time]
        low = float(lows.min()) if len(lows) else t.entry_price
        qty = st.EQUITY_REF * ad.risk * t.size / (t.entry_price - t.stop_price)
        notional = qty * t.entry_price
        table = brackets.get(agg_trade_archive.archive_symbol(t.symbol))
        for lev in LEVERAGES:
            if table is None:
                mmr, cum, max_lev = harness_mmr(), 0.0, 125
            else:
                mmr, cum, max_lev = bracket_for(table, notional)
            lp = isolated_liq_price(t.entry_price, qty, lev, mmr, cum)
            hit = lp > t.stop_price and low <= lp
            liq_r = -(t.entry_price - lp) / (t.entry_price - t.stop_price)
            rows.append(
                {
                    "symbol": t.symbol,
                    "timeframe": t.timeframe,
                    "entry_time": t.entry_time,
                    "leverage": lev,
                    "notional": notional,
                    "mmr": mmr,
                    "max_leverage": max_lev,
                    "table": table is not None,
                    "liq_price": lp,
                    "stop_price": t.stop_price,
                    "hold_low": low,
                    "liquidated": hit,
                    "net_r": t.net_r,
                    "liq_net_r": liq_r if hit else t.net_r,
                    "width": width(t),
                }
            )
    return pd.DataFrame(rows)


def harness_mmr() -> float:
    return float(DEFAULT_MAINTENANCE_MARGIN_RATE)


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.1%}"


def panel_table(panels: Sequence[Panel]) -> list[str]:
    years = list(e449.YEARS)
    out = [
        "| 판 | 팔 리스크 | k | λ | 8.6년 연환산 | MDD(최저점) | 앞 수익/MDD | 뒤 수익 | 뒤 MDD | "
        "뒤 연환산 | 뒤 수익/MDD | 2018~25 중앙 | 마이너스 해 | "
        + " | ".join(str(y) for y in years)
        + " |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|" + "--:|" * len(years),
    ]
    for p in panels:
        f_ratio = p.front_return / p.front_mdd if p.front_mdd > 0 else math.nan
        out.append(
            f"| {p.name} | {p.arm_risk:.2%} | {p.k:.3f} | {p.lam:.3f} | {_pct(p.cagr)} | "
            f"{p.mdd:.1%}({p.mdd_day}) | {f_ratio:.2f} | {_pct(p.back_return)} | "
            f"{p.back_mdd:.1%} | "
            f"{_pct(p.back_cagr)} | {p.back_ratio:.2f} | {_pct(p.median)} | {p.negative} | "
            + " | ".join(_pct(p.yearly.get(y, math.nan)) for y in years)
            + " |"
        )
    return out


def resolved(p: Panel) -> bool:
    return p.median >= e449.TARGET_MEDIAN and p.negative <= e449.MAX_NEGATIVE_YEARS


def verdict_15m(panels: Sequence[Panel]) -> tuple[str, Panel, Panel]:
    inc = next(p for p in panels if p.name == "정본 · 합성")
    exc = next(p for p in panels if p.name == "15m 제외 · 합성 · 크기 재맞춤 · k·λ 재맞춤")
    lower = exc.cagr < inc.cagr
    worse = exc.back_ratio < inc.back_ratio
    text = (
        "15m 유지 근거 있음"
        if (lower and worse)
        else ("15m 몫 확인 안 됨" if not (lower or worse) else "갈린다(한쪽만 나빠진다)")
    )
    return text, inc, exc


def render(
    panels: Sequence[Panel],
    ticks: pd.DataFrame,
    widths: pd.DataFrame,
    liq_rows: pd.DataFrame,
    notes: Sequence[str],
) -> str:
    by = {p.name: p for p in panels}
    out = [
        "# WAN-452 — 채택 후보(팔 + 추세추종)의 낙관 네 곳",
        "",
        "> 토대 = WAN-449 정본(팔 = 거래대금 상위 31 · WAN-448 측정 손절 체결 · 캐리 없음 + 엔진 C "
        "BTC·ETH). 모든 판은 같은 자(일 저가 MDD · 8.6년 = 2018-01 ~ 2026-07-22 · 앞/뒤 경계 "
        "2024-08-08)로 잰다. 🚨 측정 전용.",
        "",
    ]
    # §1
    ok = ticks[ticks["ok"].astype(bool)]
    out += [
        "## §1 진입 체결 — 틱 위 밴드 재산정 고정점",
        "",
        "| 층 | 표본 | 판정 | 재구성 성공률 | 틱 고정점 있음 | 진입가 아래 체결 있음 | "
        "우리 수량 다 채움 | "
        "진입가 차 bp 평균 / 중앙 / p90 |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for s in (CRASH, NORMAL):
        a = ticks[ticks["stratum"] == s]
        o = ok[ok["stratum"] == s]
        recon_base = a[a["note"].fillna("").isin(["", "재구성 실패"])]
        recon = len(o) / len(recon_base) if len(recon_base) else math.nan
        out.append(
            f"| {s} | {len(a)} | {len(o)} | {recon:.0%} | {o['touched'].mean():.0%} | "
            f"{o['below'].mean():.0%} | {(o['fill_frac'] >= 1.0).mean():.0%} | "
            f"{o['gap_bp'].mean():+.2f} / {o['gap_bp'].median():+.2f} / "
            f"{o['gap_bp'].quantile(0.9):+.2f} |"
        )
    fail = ticks[~ticks["ok"].astype(bool)]["note"].value_counts()
    if len(fail):
        out += ["", "판정 못 한 표본: " + " · ".join(f"{k} {v}건" for k, v in fail.items())]
    out += [
        "",
        *panel_table(
            [by["정본 · 팔 단독"], by["정본 · 합성"], *[p for p in panels if p.section == "§1"]]
        ),
    ]
    s1 = by["진입 틱 반영 · 합성 · 크기 재맞춤 · k·λ 재맞춤"]
    s1_fixed = by["진입 틱 반영 · 합성 · 정본 크기 · k·λ 정본"]
    out += [
        "",
        f"**§1 판정(참고)**: 반영 판(크기 재맞춤) 해마다 중앙 {s1.median:+.1%} · 마이너스 "
        f"{s1.negative}해 → **{'해결 유지' if resolved(s1) else '해결 깨짐'}** "
        f"(크기 그대로 판: 중앙 {s1_fixed.median:+.1%} · 마이너스 {s1_fixed.negative}해 · "
        f"MDD {s1_fixed.mdd:.1%})",
        "",
    ]
    # §2
    p2 = by["앞구간 크기 · 합성(k·λ 앞구간)"]
    canon = by["정본 · 합성"]
    out += [
        "## §2 팔 크기 · k·λ를 앞구간만으로",
        "",
        *panel_table([by["정본 · 팔 단독"], canon, *[p for p in panels if p.section == "§2"]]),
        "",
        f"**§2**: 앞구간 맞춤 판 뒷구간 MDD {p2.back_mdd:.1%}(정본 {canon.back_mdd:.1%}) · "
        f"전체 MDD {p2.mdd:.1%} — 「크기를 전체 창에서 맞춘 낙관」 = 뒷구간 MDD가 35%를 "
        f"{max(0.0, p2.back_mdd - c.MDD_TARGET):.1%}p 넘는 몫.",
        "",
    ]
    # §3
    w_all = widths["width"]
    out += [
        "## §3 손절 규칙 구멍 · 격리 마진",
        "",
        "| 손절폭(손절 배수 2 적용 뒤) | 거래 | 비율 | 손절로 끝남 | net R 합 |",
        "|---|--:|--:|--:|--:|",
    ]
    for line in WIDTH_LINES:
        sel = widths[w_all >= line]
        out.append(
            f"| ≥ {line:.0%}{' (손절가 ≤ 0)' if line >= 1.0 else ''} | {len(sel)} | "
            f"{len(sel) / len(widths):.2%} | {int(sel['stopped'].sum())} | "
            f"{sel['net_r'].sum():+.1f}R |"
        )
    out += [
        "",
        f"* 분포: 중앙 {w_all.median():.1%} · p90 {w_all.quantile(0.9):.1%} · p99 "
        f"{w_all.quantile(0.99):.1%} · 최대 {w_all.max():.1%}(전체 {len(widths)}건)",
    ]
    wide = widths[w_all >= 1.0]
    for _, r in wide.iterrows():
        out.append(
            f"* 손절가 ≤ 0: {r['symbol']} {r['timeframe']} "
            f"{pd.to_datetime(r['entry_time'], unit='ms').date()} · 손절폭 {r['width']:.0%} "
            f"· net R {r['net_r']:+.2f}"
        )
    out += [
        "",
        "### 막는 방법 선택지(방법 선택은 사용자 결정)",
        "",
        *panel_table([by["정본 · 팔 단독"], canon, *[p for p in panels if p.section == "§3"]]),
        "",
    ]
    out += [
        "### 격리 마진 — 보유 중 청산선 도달",
        "",
        "| 격리 레버리지 | 청산 거래 | 비율 | 그 거래 net R(원래) | 청산 시 net R | 차이 | "
        "구간 최대 레버리지 초과 거래 |",
        "|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for lev in LEVERAGES:
        x = liq_rows[liq_rows["leverage"] == lev]
        h = x[x["liquidated"]]
        out.append(
            f"| {lev}배 | {len(h)} | {len(h) / len(x):.2%} | {h['net_r'].sum():+.1f}R | "
            f"{h['liq_net_r'].sum():+.1f}R | {(h['liq_net_r'] - h['net_r']).sum():+.1f}R | "
            f"{int((x['max_leverage'] < lev).sum())} |"
        )
    out += [
        "",
        f"* 유지증거금률 = 바이낸스 공개 구간표(`{BRACKETS_URL}` · 조회 시점 값 — 과거 표가 "
        f"아니다) · 표에 없는 종목 {int((~liq_rows['table']).sum() // len(LEVERAGES))}건은 "
        f"{harness_mmr():.1%} · 명목은 기준 계좌 {st.EQUITY_REF:,.0f} USDT × 정본 크기.",
        "* 청산 판정: 청산가 > 손절가이고 보유 중(청산 분 포함) 1분 저가 ≤ 청산가. 청산가 ≤ "
        "손절가면 손절이 먼저 나간다고 본다. 📌 **채택 전제는 교차·포트폴리오 마진**이다 — 이 표는 "
        "격리로 돌렸을 때의 위험이고, 청산 시 net R은 진입가→청산가 거리만 센다(청산 수수료 무시).",
        "",
    ]
    # §4
    text, inc, exc = verdict_15m(panels)
    out += [
        "## §4 15m 칸의 몫",
        "",
        *panel_table([by["정본 · 팔 단독"], canon, *[p for p in panels if p.section == "§4"]]),
        "",
        f"**§4 판정(참고)**: **{text}** — 15m 제외(MDD 35% 재맞춤 합성) 8.6년 연환산 "
        f"{exc.cagr:+.1%} vs 정본 {inc.cagr:+.1%} · 뒷구간 수익/MDD {exc.back_ratio:.2f} vs "
        f"{inc.back_ratio:.2f}. 15m을 빼는 결정은 사용자 몫이다.",
        "",
    ]
    out += [
        "## 기록",
        "",
        *[f"* {n}" for n in notes],
        "",
        "**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변(체결내역·구간표는 `data/cache/`) · "
        "실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**",
        "",
    ]
    return "\n".join(out)


def panels_frame(panels: Sequence[Panel]) -> pd.DataFrame:
    rows = []
    for p in panels:
        d = dataclasses.asdict(p)
        yr = d.pop("yearly")
        d.update({f"y{y}": v for y, v in yr.items()})
        rows.append(d)
    return pd.DataFrame(rows)


def panels_from_frame(frame: pd.DataFrame) -> list[Panel]:
    out = []
    for r in frame.to_dict("records"):
        yr = {int(k[1:]): float(v) for k, v in r.items() if k.startswith("y2") and not pd.isna(v)}
        base = {k: v for k, v in r.items() if not k.startswith("y2")}
        base["mdd_day"] = str(base["mdd_day"])
        out.append(Panel(**base, yearly=yr))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--part", choices=("ticks", "run", "summary"), default="run")
    args = ap.parse_args(argv)
    if args.part in ("ticks", "run"):
        with m.MARKETS_PATH.open("rb") as fh:
            built = pickle.load(fh)
        if args.part == "ticks":
            notes = run_ticks(built)
            NOTES_TXT.write_text("\n".join(notes) + "\n")
            print("\n".join(notes))
            return 0
        panels, widths, liq_rows, notes = run_all(built)
        panels_frame(panels).to_csv(PANELS_CSV, index=False)
        widths.to_csv(WIDTH_CSV, index=False)
        liq_rows.to_csv(LIQ_CSV, index=False)
        tick_notes = (
            [n for n in NOTES_TXT.read_text().splitlines() if n] if NOTES_TXT.exists() else []
        )
        tick_notes = [n for n in tick_notes if not n.startswith("§") and "검산" not in n]
        NOTES_TXT.write_text("\n".join([*tick_notes[:3], *notes]) + "\n")
    panels = panels_from_frame(pd.read_csv(PANELS_CSV))
    text = render(
        panels,
        pd.read_csv(TICKS_CSV),
        pd.read_csv(WIDTH_CSV),
        pd.read_csv(LIQ_CSV),
        NOTES_TXT.read_text().splitlines(),
    )
    SUMMARY_PATH.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
