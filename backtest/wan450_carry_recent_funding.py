"""WAN-450 — 팔 + 추세추종 + 캐리 합성을 「최근 12개월 펀딩 고정」으로 다시 잰다.

WAN-449 참고 판 「정본 + 캐리 50%」(해마다 중앙 +39.5%)는 **과거 실제 펀딩**을 넣었다. 최근 12개월
평균 펀딩은 8.6년 평균의 4분의 1 수준이라, 그 판이 과거 펀딩에 기댄 값인지 따로 잰다.

* 토대 = WAN-449 정본 그대로(팔 · 엔진 C · k 앞구간 변동성 일치 · λ 합성 MDD 35% 맞춤).
* 캐리 = WAN-447 규칙 그대로(BTC·ETH 반반 · 정산된 펀딩만 · 왕복 0.22% 시작 시 1회).
* 판 = 캐리 비율 f ∈ {0, 0.5, 1.0} × 펀딩 {실제, 최근 12개월 고정}. 엔진 C 자신의 펀딩은 실제값.
* 판정(착수 전 고정) = f 0.5 · 최근 12개월 고정에서 2018~2025 해마다 중앙 ≥ 20% 그리고 마이너스 ≤ 1.
* §증거금 = 날마다 총 명목 ÷ 자본(팔 열린 명목 + 엔진 C + 캐리 양다리)과, 총 명목 상한을
  3배·5배로 가정했을 때 막히는 팔 진입. 🚨 상한은 실제 증거금 규칙이 아니라 민감도다.

재현: `uv run python -m backtest.wan450_carry_recent_funding`(시장 WAN-442 피클 · 펀딩 WAN-447
캐시 · 체결 α WAN-448 CSV · 약 2분). **측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 · 실거래
보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**
"""

from __future__ import annotations

import argparse
import math
import pickle
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan447_funding_carry as fc
from backtest import wan449_tsmom_second_engine as e

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
PANELS_CSV = REPORT_DIR / "wan450_panels.csv"
MARGIN_DAILY_CSV = REPORT_DIR / "wan450_margin_daily.csv"
MARGIN_CAPS_CSV = REPORT_DIR / "wan450_margin_caps.csv"
SUMMARY_PATH = REPORT_DIR / "wan450_carry_recent_funding_summary.md"

FRACTIONS: tuple[float, ...] = (0.0, 0.5, 1.0)
VERDICT_FRACTION = 0.5
SERIES_ACTUAL = fc.SERIES_ACTUAL
SERIES_TRAILING = fc.SERIES_TRAILING
SERIES_ZERO = "펀딩 0 고정"
SERIES: tuple[str, ...] = (SERIES_ACTUAL, SERIES_TRAILING)
CAPS: tuple[float, ...] = (3.0, 5.0)
"""총 명목 ÷ 자본 가정 상한 — 실제 증거금 규칙이 아니라 민감도 확인용(이슈가 고정)."""
TOP_DAY_SHARE = 0.01
TOP_DAYS_LISTED = 10
CARRY_LEGS = 2.0
"""캐리 명목 = 현물 롱 f + 무기한 숏 f."""
CHECK_TOL = 1e-9
SETTLEMENTS_PER_YEAR = 3 * 365.25
COLLATERAL_NOTE = (
    "바이낸스 공개 응답(`/bapi/margin/v1/public/margin/portfolio/collateral-rate` · 2026-10-14 "
    "조회)은 BTC·ETH 0.95 · USDT 0.9999다(운용 전 계좌 화면에서 다시 확인할 것)."
)
"""담보 비율 기록 — 측정에는 안 쓴다(리포트 「빠진 위험」에 적는 값)."""


def carry_daily(fund: fc.Funding, idx: pd.DatetimeIndex, fraction: float) -> pd.Series | None:
    """자본의 `fraction`이 캐리일 때 일 배율 − 1 · 진입 비용은 첫날 한 번 — 0이면 캐리 없음(`None`).

    `e.carry_daily`(캐리 50% 고정)를 비율·펀딩 시나리오로 일반화한 것이다. 같은 입력이면 같은 값을
    내는지는 검산이 건다.
    """
    if fraction == 0.0:
        return None
    ends = e._to_ms(idx + pd.Timedelta(days=1))
    ci = pd.Series(fc.carry_index(fund, np.asarray(ends, dtype=np.int64), fraction), index=idx)
    out = ci / ci.shift(1).fillna(1.0) - 1.0
    out.iloc[0] = (1.0 + out.iloc[0]) * (1.0 - fraction * fc.CARRY_COST) - 1.0
    # 하드 제로 경보(WAN-367) — 펀딩이 있는데 캐리가 안 움직이면 시각 단위가 어긋난 것이다.
    if bool(np.any(fund.rates != 0.0)) and float(np.abs(out.iloc[1:]).max()) == 0.0:
        raise ValueError("캐리 일 배율이 전부 0 — 펀딩 시각과 날짜 격자가 안 맞는다")
    return out


@dataclass(frozen=True)
class Panel:
    fraction: float
    series: str
    variant: e.Variant
    cagr: float

    @property
    def label(self) -> str:
        return f"캐리 {self.fraction:.0%} · {self.series}"


@dataclass(frozen=True)
class Inputs:
    """합성에 들어가는 일 단위 재료(WAN-449 정본과 같은 값)."""

    ra: pd.Series
    la: pd.Series
    rc: pd.Series
    lc: pd.Series
    front: np.ndarray
    years: float

    @property
    def idx(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.ra.index)


def make_panel(inp: Inputs, fraction: float, series: str, carry: pd.Series | None) -> Panel:
    v = e.fit_variant(
        f"캐리 {fraction:.0%} · {series}", inp.ra, inp.la, inp.rc, inp.lc, inp.front, carry
    )
    return Panel(fraction, series, v, c.cagr(float(v.eq.iloc[-1]) - 1.0, inp.years))


def front_lambda_panel(inp: Inputs, fraction: float, series: str, carry: pd.Series | None) -> Panel:
    """참고 — λ를 **앞구간만으로** 맞춘 판(전체 창 MDD는 35%가 아닐 수 있다)."""
    f = inp.front
    k = float(inp.ra[f].std() / inp.rc[f].std())
    lam = e.fit_lambda(
        inp.ra[f],
        inp.la[f],
        inp.rc[f],
        inp.lc[f],
        k,
        c.MDD_TARGET,
        None if carry is None else carry[f],
    )
    eq, low_eq = e.combine(inp.ra, inp.la, inp.rc, inp.lc, k, lam, carry)
    peak = np.maximum.accumulate(np.maximum(eq.to_numpy(), 1.0))
    dd = 1.0 - low_eq.to_numpy() / peak
    day = str(eq.index[int(np.argmax(dd))].date())
    v = e.Variant(
        f"캐리 {fraction:.0%} · {series} · λ 앞구간", k, lam, float(dd.max()), day, e.yearly(eq), eq
    )
    return Panel(fraction, series, v, c.cagr(float(eq.iloc[-1]) - 1.0, inp.years))


def carry_share(with_carry: Panel, base: Panel) -> tuple[pd.Series, float]:
    """캐리가 더하는 몫 — (해마다 수익 차, 8.6년 연환산 차)."""
    return with_carry.variant.yearly - base.variant.yearly, with_carry.cagr - base.cagr


# ───────────────────────── §증거금 — 총 명목 ÷ 자본 ─────────────────────────


@dataclass(frozen=True)
class Sweep:
    """팔 거래를 `w.mtm_path`와 같은 순서로 훑은 결과(명목은 전부 그 순간 확정 자본 대비)."""

    notional: np.ndarray
    """거래별 진입 명목 ÷ 진입 순간 자본(막힌 거래도 「들어갔다면」의 값)."""
    kept: np.ndarray
    event_time: np.ndarray
    event_ratio: np.ndarray
    """사건(진입·청산) 직후의 열린 명목 ÷ 자본."""
    final_equity: float
    breaches: int


def sweep(
    trades: Sequence[w.PlacedTrade],
    risk: float,
    room: Callable[[int], float] | None = None,
) -> Sweep:
    """`w.mtm_path`의 사건 순서 그대로 열린 명목을 따라간다.

    `room(ms)`를 주면 그 순간 팔이 쓸 수 있는 명목 ÷ 자본 한도이고, 넘기는 진입은 **통째로
    건너뛴다**(줄여서 들어가는 것보다 보수적). 안 주면 아무것도 막지 않는다 — 그때 마지막 자본과
    상한 초과 횟수가 `w.mtm_path`와 같아야 한다(검산).
    """
    events: list[tuple[int, int, int]] = []
    for i, t in enumerate(trades):
        events.append((t.exit_time, 2 if t.exit_time <= t.entry_time else 0, i))
        events.append((t.entry_time, 1, i))
    events.sort()
    n = len(trades)
    eq = 1.0
    weight = [0.0] * n
    held = [0.0] * n
    notional = np.zeros(n)
    kept = np.ones(n, dtype=bool)
    open_notional = 0.0
    breaches = 0
    ev_t = np.zeros(len(events), dtype=np.int64)
    ev_r = np.zeros(len(events))
    for j, (tm, kind, i) in enumerate(events):
        t = trades[i]
        if kind != 1:
            eq += risk * weight[i] * t.net_r
            open_notional -= held[i]
        else:
            wgt = eq * t.size
            size = risk * wgt * t.entry_price / (t.entry_price - t.stop_price)
            notional[i] = size / eq
            if room is not None and open_notional + size > room(tm) * eq:
                kept[i] = False
            else:
                weight[i] = wgt
                held[i] = size
                open_notional += size
            if open_notional > w.CAP_MULTIPLE * eq:
                breaches += 1
        ev_t[j] = tm
        ev_r[j] = max(open_notional, 0.0) / eq
    return Sweep(notional, kept, ev_t, ev_r, eq, breaches)


def daily_max(times: np.ndarray, ratios: np.ndarray, idx: pd.DatetimeIndex) -> pd.Series:
    """사건열에서 그날 최대 열린 명목 ÷ 자본 — 전날에서 넘어온 포지션도 그날 값이다."""
    if len(times) == 0:
        return pd.Series(0.0, index=idx)
    s = pd.Series(ratios, index=pd.to_datetime(times, unit="ms"))
    day = pd.DatetimeIndex(s.index).floor("D")
    grouped = s.groupby(day)
    carried = grouped.last().reindex(idx).ffill().shift(1).fillna(0.0)
    peak = grouped.max().reindex(idx).fillna(0.0)
    return pd.Series(np.maximum(peak.to_numpy(), carried.to_numpy()), index=idx)


def engine_gross(idx: pd.DatetimeIndex) -> pd.Series:
    """엔진 C 바스켓의 그날 보유 명목 ÷ 자본(k·λ 곱하기 전) = 자산별 |전날 정한 배율|의 평균."""
    fut_from = u.ms(e.FUT_FROM)
    cols = []
    for sym in e.ASSETS:
        bars = e.daily_bars(sym, fut_from)
        bars = bars[bars.index >= pd.Timestamp("2017-01-01")]
        price = (1.0 + bars["ret"].fillna(0.0)).cumprod()
        cols.append(e.tsmom_weights(price, bars["ret"]).shift(1).fillna(0.0).abs())
    return pd.concat(cols, axis=1).mean(axis=1).reindex(idx).fillna(0.0)


def carry_gross(fund: fc.Funding, idx: pd.DatetimeIndex, fraction: float) -> pd.Series:
    """캐리 양다리 명목 ÷ 자본 — 선물이 생긴 날부터 2f(그 전은 캐리 0)."""
    start = pd.Timestamp(int(fund.times[0]), unit="ms").floor("D")
    return pd.Series(np.where(idx >= start, CARRY_LEGS * fraction, 0.0), index=idx)


@dataclass(frozen=True)
class Margin:
    fraction: float
    lam: float
    k: float
    arm: pd.Series
    """팔 그날 최대 열린 명목 ÷ 자본(λ 반영)."""
    engine: pd.Series
    carry: pd.Series

    @property
    def total(self) -> pd.Series:
        return self.arm + self.engine + self.carry


@dataclass(frozen=True)
class TopDays:
    fraction: float
    days: int
    entries: int
    net_r: float
    all_entries: int
    all_net_r: float


@dataclass(frozen=True)
class CapRow:
    cap: float
    fraction: float
    entries: int
    blocked: int
    blocked_net_r: float
    blocked_pnl: float
    """막힌 거래의 λ × 크기 × net R 합(자본 대비 단순 합 · 복리 아님)."""
    cagr: float
    mdd: float
    median: float
    negative: int
    yearly: pd.Series


def _other_lookup(other: pd.Series) -> Callable[[int], float]:
    base = int(e._to_ms(pd.DatetimeIndex(other.index))[0])
    vals = other.to_numpy()

    def at(ms: int) -> float:
        i = (ms - base) // e.DAY_MS
        return float(vals[i]) if 0 <= i < len(vals) else 0.0

    return at


def run_margin(
    sp: Sequence[w.PlacedTrade],
    fu: Sequence[w.PlacedTrade],
    risk: float,
    inp: Inputs,
    fund: fc.Funding,
    panels: dict[tuple[float, str], Panel],
    carries: dict[tuple[float, str], pd.Series | None],
) -> tuple[list[Margin], list[TopDays], list[CapRow], list[str]]:
    idx = inp.idx
    notes: list[str] = []
    free = [sweep(sp, risk), sweep(fu, risk)]
    # 검산 — 막지 않은 훑기 ≡ `w.mtm_path`(마지막 확정 자본 · 상한 초과 횟수).
    worst = 0.0
    for sw, trades in zip(free, (sp, fu), strict=True):
        path = w.mtm_path(trades, risk=risk)
        if path.realized is None or sw.breaches != path.breaches:
            raise AssertionError("명목 훑기가 `w.mtm_path`와 다르다(상한 초과 횟수)")
        worst = max(worst, abs(sw.final_equity - float(path.realized[-1])))
    if worst > CHECK_TOL:
        raise AssertionError(f"명목 훑기 자본이 `w.mtm_path`와 다르다(최대 차 {worst:.2e})")
    notes.append(
        f"검산 — 명목 훑기 ≡ `w.mtm_path`(마지막 확정 자본 최대 차 {worst:.1e} · 5배 초과 "
        f"{free[0].breaches + free[1].breaches}회 일치)"
    )
    arm_raw = daily_max(
        np.concatenate([s.event_time for s in free]),
        np.concatenate([s.event_ratio for s in free]),
        idx,
    )
    gross = engine_gross(idx)
    trades_all = [*sp, *fu]
    entry_day = pd.to_datetime([t.entry_time for t in trades_all], unit="ms").floor("D")
    net_r = np.array([t.net_r for t in trades_all])
    size = np.array([t.size for t in trades_all])
    in_window = np.asarray(entry_day.isin(idx))

    margins: list[Margin] = []
    tops: list[TopDays] = []
    caps: list[CapRow] = []
    for fr in FRACTIONS:
        # 명목은 판정 시나리오(최근 12개월 고정)의 λ로 잰다 — 캐리 0%는 두 시나리오가 같다.
        key = (fr, SERIES_TRAILING)
        v = panels[key].variant
        mg = Margin(
            fr, v.lam, v.k, v.lam * arm_raw, v.k * v.lam * gross, carry_gross(fund, idx, fr)
        )
        margins.append(mg)
        total = mg.total
        n_top = math.ceil(TOP_DAY_SHARE * len(idx))
        top_days = pd.DatetimeIndex(total.nlargest(n_top).index)
        hit = np.asarray(entry_day.isin(top_days))
        tops.append(
            TopDays(
                fr,
                n_top,
                int(hit.sum()),
                float(net_r[hit].sum()),
                int(in_window.sum()),
                float(net_r[in_window].sum()),
            )
        )
        other = _other_lookup(mg.engine + mg.carry)
        for cap in CAPS:

            def room(
                ms: int,
                cap: float = cap,
                lam: float = v.lam,
                other: Callable[[int], float] = other,
            ) -> float:
                return (cap - other(ms)) / lam

            gated = [sweep(sp, risk, room), sweep(fu, risk, room)]
            kept = np.concatenate([g.kept for g in gated])
            arm = e.arm_daily_from(
                [t for t, ok in zip(sp, gated[0].kept, strict=True) if ok],
                [t for t, ok in zip(fu, gated[1].kept, strict=True) if ok],
                risk,
            )
            ra = arm.ret.reindex(idx).fillna(0.0)
            la = arm.low.reindex(idx).fillna(0.0)
            eq, low_eq = e.combine(ra, la, inp.rc, inp.lc, v.k, v.lam, carries[key])
            yr = e.yearly(eq)
            _ok, med, neg = e.verdict(yr)
            caps.append(
                CapRow(
                    cap,
                    fr,
                    len(kept),
                    int((~kept).sum()),
                    float(net_r[~kept].sum()),
                    float((v.lam * risk * size[~kept] * net_r[~kept]).sum()),
                    c.cagr(float(eq.iloc[-1]) - 1.0, inp.years),
                    e.mdd_of(eq, low_eq),
                    med,
                    neg,
                    yr,
                )
            )
    # 검산 — 상한 없이 같은 길로 돌린 팔 일 수익 ≡ 정본 팔 일 수익(상한 배선이 값을 안 바꾼다).
    same = e.arm_daily_from(sp, fu, risk).ret.reindex(idx).fillna(0.0)
    gap = float((same - inp.ra).abs().max())
    if gap > CHECK_TOL:
        raise AssertionError(f"상한 없는 팔 일 수익이 정본과 다르다(최대 차 {gap:.2e})")
    notes.append(f"검산 — 상한 없이 다시 만든 팔 일 수익 ≡ 정본 팔 일 수익(최대 차 {gap:.1e})")
    return margins, tops, caps, notes


# ───────────────────────────── 실행 · 렌더 ─────────────────────────────


@dataclass(frozen=True)
class Outcome:
    panels: dict[tuple[float, str], Panel]
    zero: list[Panel]
    front: list[Panel]
    margins: list[Margin]
    tops: list[TopDays]
    caps: list[CapRow]
    notes: list[str]
    trail_rate: float
    mean_rate: float


def run(built: u.Markets) -> Outcome:
    sp, fu, risk = e.arm_trades(built, same_minute=w.SAME_MINUTE_EXIT_FIRST)
    arm = e.arm_daily_from(sp, fu, risk)
    frame, ref, _notes449, _nums = e.run(built, arm)
    idx = pd.DatetimeIndex(frame.index)
    inp = Inputs(
        frame["arm_ret"],
        frame["arm_low"],
        frame["c_ret"],
        frame["c_low"],
        np.asarray(idx < pd.Timestamp(e.FRONT_END)),
        c._years(u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END)),
    )
    fund, fetched = fc.fetch_funding()
    trail_rate = fc.trailing_mean(fund, int(fund.times[-1]))
    scenario = {
        SERIES_ACTUAL: fund,
        SERIES_TRAILING: fc.flat(fund, trail_rate),
        SERIES_ZERO: fc.flat(fund, 0.0),
    }
    notes = [
        f"펀딩 — BTC·ETH 반반 · 정산 {len(fund.times)}회 · {fc.m438_utc(int(fund.times[0]))} ~ "
        f"{fc.m438_utc(int(fund.times[-1]))} · 받은 시각 {fetched}",
        f"최근 12개월 정산당 평균 {trail_rate:.6%} → 연환산 "
        f"{trail_rate * SETTLEMENTS_PER_YEAR:+.2%}"
        f"(전체 기간 평균 {float(fund.rates.mean()) * SETTLEMENTS_PER_YEAR:+.2%})",
    ]
    carries: dict[tuple[float, str], pd.Series | None] = {
        (fr, name): carry_daily(scenario[name], idx, fr) for fr in FRACTIONS for name in scenario
    }
    panels = {
        (fr, name): make_panel(inp, fr, name, carries[fr, name])
        for fr in FRACTIONS
        for name in SERIES
    }

    # 검산 ① — 캐리 0% · 실제 ≡ WAN-449 정본(같은 실행의 k·λ·해마다 + 공개 일별 CSV의 자본 곡선).
    base = panels[0.0, SERIES_ACTUAL].variant
    main = ref[0]
    if (base.k, base.lam) != (main.k, main.lam) or not base.yearly.equals(main.yearly):
        raise AssertionError("캐리 0% · 실제 판이 WAN-449 정본과 다르다")
    pub = pd.read_csv(e.DAILY_CSV, index_col=0, parse_dates=True)["combo_eq"]
    pub_gap = float((e.yearly(pub) - base.yearly).abs().max())
    if pub_gap > CHECK_TOL:
        raise AssertionError(
            f"캐리 0% · 실제 판이 WAN-449 공개 CSV와 다르다(최대 차 {pub_gap:.2e})"
        )
    notes.append(
        f"검산 — 캐리 0% · 실제 ≡ WAN-449 정본(k {base.k:.3f} · λ {base.lam:.3f} · 해마다 정확히 "
        f"일치 · 공개 `wan449_daily.csv` 해마다 최대 차 {pub_gap:.1e})"
    )
    # 검산 ② — 캐리 50% · 실제 ≡ WAN-449 「정본 + 캐리 50%」(일반화한 캐리 함수가 같은 값을 낸다).
    half, ref_half = panels[0.5, SERIES_ACTUAL].variant, ref[2]
    half_gap = float((half.yearly - ref_half.yearly).abs().max())
    if ref_half.name != e.VARIANT_CARRY or half_gap > CHECK_TOL or half.lam != ref_half.lam:
        raise AssertionError(
            f"캐리 50% · 실제 판이 WAN-449 캐리 판과 다르다(최대 차 {half_gap:.2e})"
        )
    notes.append(
        f"검산 — 캐리 50% · 실제 ≡ WAN-449 「{e.VARIANT_CARRY}」(λ {half.lam:.3f} · 해마다 최대 차 "
        f"{half_gap:.1e})"
    )
    if not panels[0.0, SERIES_TRAILING].variant.yearly.equals(base.yearly):
        raise AssertionError("캐리 0%인데 펀딩 시나리오에 따라 값이 달라졌다")

    zero = [make_panel(inp, fr, SERIES_ZERO, carries[fr, SERIES_ZERO]) for fr in FRACTIONS[1:]]
    front = [
        front_lambda_panel(inp, fr, SERIES_TRAILING, carries[fr, SERIES_TRAILING])
        for fr in FRACTIONS
    ]
    margins, tops, caps, mnotes = run_margin(sp, fu, risk, inp, fund, panels, carries)
    notes += mnotes
    notes.append(
        f"팔 거래 {len(sp) + len(fu)}건(현물 구간 {len(sp)} · 선물 구간 {len(fu)}) · 거래당 리스크 "
        f"{risk:.2%}"
    )
    peak_w = max(float(mg.engine.max()) / (mg.k * mg.lam) for mg in margins)
    notes.append(
        f"엔진 C 바스켓 배율(자산별 |배율| 평균)의 실측 최대 {peak_w:.2f} — 상한 "
        f"{e.MAX_LEVERAGE:.0f}에 닿은 적이 없다(코인 변동성이 연 20% 목표보다 커서 배율이 작다)"
    )
    return Outcome(
        panels, zero, front, margins, tops, caps, notes, trail_rate, float(fund.rates.mean())
    )


def _pct(x: float) -> str:
    return "—" if x != x else f"{x:+.1%}"


def _year_label(y: int) -> str:
    return f"{y}(부분)" if y > max(e.YEARS) else str(y)


def _years_in(p: Panel) -> list[int]:
    return [int(y) for y in p.variant.yearly.index]


def panel_frame(out: Outcome) -> pd.DataFrame:
    recs = []
    groups = (
        ("판", list(out.panels.values())),
        ("참고·펀딩 0", out.zero),
        ("참고·λ 앞구간", out.front),
    )
    for group, ps in groups:
        for p in ps:
            ok, med, neg = e.verdict(p.variant.yearly)
            rec: dict[str, object] = {
                "group": group,
                "fraction": p.fraction,
                "series": p.series,
                "k": p.variant.k,
                "lambda": p.variant.lam,
                "mdd": p.variant.mdd,
                "mdd_day": p.variant.mdd_day,
                "cagr": p.cagr,
                "median_2018_2025": med,
                "negative_years": neg,
                "verdict_ok": ok,
            }
            rec.update({f"y{y}": float(p.variant.yearly[y]) for y in _years_in(p)})
            recs.append(rec)
    return pd.DataFrame(recs)


def margin_daily_frame(out: Outcome) -> pd.DataFrame:
    cols: dict[str, pd.Series] = {}
    for mg in out.margins:
        tag = f"f{mg.fraction:.1f}"
        cols[f"arm_{tag}"] = mg.arm
        cols[f"engine_{tag}"] = mg.engine
        cols[f"carry_{tag}"] = mg.carry
        cols[f"total_{tag}"] = mg.total
    return pd.DataFrame(cols)


def caps_frame(out: Outcome) -> pd.DataFrame:
    recs = []
    for r in out.caps:
        rec: dict[str, object] = {
            "cap": r.cap,
            "fraction": r.fraction,
            "entries": r.entries,
            "blocked": r.blocked,
            "blocked_net_r": r.blocked_net_r,
            "blocked_pnl": r.blocked_pnl,
            "cagr": r.cagr,
            "mdd": r.mdd,
            "median_2018_2025": r.median,
            "negative_years": r.negative,
        }
        rec.update({f"y{int(y)}": float(v) for y, v in r.yearly.items()})
        recs.append(rec)
    return pd.DataFrame(recs)


def verdict_line(out: Outcome) -> tuple[bool, float, int]:
    return e.verdict(out.panels[VERDICT_FRACTION, SERIES_TRAILING].variant.yearly)


def cap_notes(out: Outcome) -> list[str]:
    """막힌 진입이 0건인 칸이 「안 일어났다」인지 「일어날 수 없다」인지 값으로 밝힌다(WAN-367)."""
    notes = []
    peak = {mg.fraction: float(mg.total.max()) for mg in out.margins}
    for r in out.caps:
        if r.blocked == 0:
            notes.append(
                f"* 상한 {r.cap:.0f}배 · 캐리 {r.fraction:.0%}: 막힌 진입 0건 — 총 명목 최대가 "
                f"{peak[r.fraction]:.2f}배라 상한에 닿은 날이 없다(여유 "
                f"{r.cap - peak[r.fraction]:.2f}배)."
            )
        else:
            notes.append(
                f"* 상한 {r.cap:.0f}배 · 캐리 {r.fraction:.0%}: {r.blocked}건이 막힌다(전체의 "
                f"{r.blocked / r.entries:.1%}) — 그 거래들의 net R 합 {r.blocked_net_r:+.1f}R."
            )
    return notes


def front_lambda_note(out: Outcome) -> str:
    """λ 앞구간 판이 정본과 같은지 값으로 밝힌다(같으면 그 이유 = 최저점이 앞구간 안)."""
    gap = max(
        abs(p.variant.lam - out.panels[p.fraction, SERIES_TRAILING].variant.lam) for p in out.front
    )
    days = sorted({out.panels[p.fraction, SERIES_TRAILING].variant.mdd_day for p in out.front})
    if gap <= CHECK_TOL and all(d < e.FRONT_END for d in days):
        return (
            f"* 📌 세 판 모두 λ가 전체 창 맞춤과 **같다**(최대 차 {gap:.1e}) — MDD 35%를 만드는 "
            f"최저점({' · '.join(days)})이 앞구간 안에 있어서다. 이 축에서 뒷구간 낙관은 없다."
        )
    return f"* 📌 λ가 전체 창 맞춤과 다르다(최대 차 {gap:.3f}) — 위 표의 전체 창 MDD를 볼 것."


def render(out: Outcome) -> str:
    ok, med, neg = verdict_line(out)
    judge = out.panels[VERDICT_FRACTION, SERIES_TRAILING]
    base = out.panels[0.0, SERIES_TRAILING]
    years = _years_in(judge)
    per_year = SETTLEMENTS_PER_YEAR
    lines = [
        "# WAN-450 — 팔 + 추세추종 + 캐리 합성 · 「최근 12개월 펀딩 고정」",
        "",
        "> 토대 = WAN-449 정본(팔 + 엔진 C · k 앞구간 변동성 일치 · λ 합성 MDD 35% 맞춤). 캐리 = "
        "WAN-447 규칙(BTC·ETH 반반 현물 롱 + 무기한 숏 · 자본의 f). 펀딩 시나리오는 캐리 다리에만 "
        "걸고 엔진 C 자신의 펀딩은 실제값이다. 🚨 캐리 곡선은 펀딩만의 손익이다(베이시스·숏 증거금·"
        "거래소 위험 제외).",
        "",
        f"## 판정 — **{'해결 유지' if ok else '해결 깨짐'}** (캐리 50% · 최근 12개월 고정: "
        f"2018~2025 해마다 중앙 {med:+.1%} · 마이너스 {neg}해 · 기준 중앙 ≥ 20% · 마이너스 ≤ 1)",
        "",
        f"* 최근 12개월 평균 펀딩 연 {out.trail_rate * per_year:+.2%} · 전체 기간 평균 연 "
        f"{out.mean_rate * per_year:+.2%}.",
        f"* 캐리 없는 정본: 중앙 {e.verdict(base.variant.yearly)[1]:+.1%} · 8.6년 연환산 "
        f"{base.cagr:+.1%} → 캐리 50%(최근 펀딩): 중앙 {med:+.1%} · 연환산 {judge.cagr:+.1%}.",
        "",
        "## §1 여섯 판 — 해마다(각 판 MDD 35% 맞춤)",
        "",
        "| 판 | λ | 8.6년 연환산 | MDD(최저점) | 2018~2025 중앙 | 마이너스 해 | "
        + " | ".join(_year_label(y) for y in years)
        + " |",
        "|---|--:|--:|--:|--:|--:|" + "--:|" * len(years),
    ]

    def row(p: Panel) -> str:
        _ok, p_med, p_neg = e.verdict(p.variant.yearly)
        cells = " | ".join(_pct(float(p.variant.yearly.get(y, float("nan")))) for y in years)
        return (
            f"| {p.label} | {p.variant.lam:.3f} | {p.cagr:+.1%} | {p.variant.mdd:.1%}"
            f"({p.variant.mdd_day}) | {p_med:+.1%} | {p_neg} | {cells} |"
        )

    lines += [row(out.panels[fr, name]) for name in SERIES for fr in FRACTIONS]
    lines += [
        "",
        "## §2 캐리가 더하는 몫 — 같은 펀딩 시나리오의 캐리 0% 판 대비(%p)",
        "",
        "| 캐리 | 펀딩 | 8.6년 연환산 차 | " + " | ".join(_year_label(y) for y in years) + " |",
        "|--:|---|--:|" + "--:|" * len(years),
    ]
    for fr in FRACTIONS[1:]:
        for name in SERIES:
            diff, d_cagr = carry_share(out.panels[fr, name], out.panels[0.0, name])
            cells = " | ".join(f"{float(diff.get(y, float('nan'))) * 100:+.1f}" for y in years)
            lines.append(f"| {fr:.0%} | {name} | {d_cagr * 100:+.1f} | {cells} |")
    lines += [
        "",
        "* 몫에는 캐리 손익과 함께 **λ가 달라진 몫**이 들어 있다(판마다 MDD 35%에 다시 맞춘다).",
        "",
        "## §3 참고 판(판정 아님)",
        "",
        "| 판 | λ | 8.6년 연환산 | 전체 창 MDD(최저점) | 2018~2025 중앙 | 마이너스 해 | "
        + " | ".join(_year_label(y) for y in years)
        + " |",
        "|---|--:|--:|--:|--:|--:|" + "--:|" * len(years),
    ]

    def ref_row(p: Panel, name: str) -> str:
        return row(Panel(p.fraction, name, p.variant, p.cagr))

    lines += [ref_row(p, SERIES_ZERO) for p in out.zero]
    lines += [ref_row(p, f"{SERIES_TRAILING} · λ 앞구간만") for p in out.front]
    lines += [
        "",
        "* 「펀딩 0 고정」은 캐리가 진입 비용만 내고 아무것도 못 받는 하한이다(음수 펀딩이 "
        "이어지는 경우는 이보다 나쁘다).",
        f"* 「λ 앞구간만」은 λ를 {c.WINDOW_START} ~ {e.FRONT_END}에서만 맞춘 판이다 — 전체 창 "
        "MDD가 35%를 넘으면 그만큼이 「λ를 전체 창에서 맞춘」 정본의 뒷구간 낙관이다.",
        front_lambda_note(out),
        "",
        "## §4 증거금 — 날마다 총 명목 ÷ 자본",
        "",
        "> 총 명목 = 팔 열린 명목(그날 최대 · λ 반영) + 엔진 C 보유 명목(k·λ 반영) + 캐리 양다리"
        "(현물 롱 f + 무기한 숏 f). 🚨 명목 배수일 뿐 실제 증거금이 아니다 — 유지증거금률·담보 "
        "할인율·종목별 한도는 모델링하지 않았다.",
        "",
        "| 캐리 | λ | 중앙 | p95 | p99 | 최대 | 팔만 최대 | 엔진 C만 최대 | 캐리 |",
        "|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for mg in out.margins:
        t = mg.total
        lines.append(
            f"| {mg.fraction:.0%} | {mg.lam:.3f} | {t.median():.2f}배 | {t.quantile(0.95):.2f}배 | "
            f"{t.quantile(0.99):.2f}배 | {t.max():.2f}배 | {mg.arm.max():.2f}배 | "
            f"{mg.engine.max():.2f}배 | {mg.carry.max():.2f}배 |"
        )
    for mg in out.margins:
        lines += [
            "",
            f"### 총 명목이 가장 큰 날 {TOP_DAYS_LISTED}개 — 캐리 {mg.fraction:.0%}",
            "",
            "| 날짜(UTC) | 총 명목 | 팔 | 엔진 C | 캐리 |",
            "|---|--:|--:|--:|--:|",
        ]
        for day, val in mg.total.nlargest(TOP_DAYS_LISTED).items():
            lines.append(
                f"| {pd.Timestamp(str(day)).date()} | {val:.2f}배 | {mg.arm[day]:.2f}배 | "
                f"{mg.engine[day]:.2f}배 | {mg.carry[day]:.2f}배 |"
            )
    lines += [
        "",
        f"### 총 명목 상위 {TOP_DAY_SHARE:.0%} 날에 들어간 팔 진입",
        "",
        "| 캐리 | 상위 날 수 | 그날 팔 진입 | 그 거래 net R 합 | 전체 팔 진입 | 전체 net R 합 |",
        "|--:|--:|--:|--:|--:|--:|",
    ]
    lines += [
        f"| {t.fraction:.0%} | {t.days} | {t.entries} | {t.net_r:+.1f}R | {t.all_entries} | "
        f"{t.all_net_r:+.1f}R |"
        for t in out.tops
    ]
    lines += [
        "",
        "### 가정 상한 민감도 — 총 명목이 상한을 넘기는 팔 진입을 통째로 건너뛴다",
        "",
        "> 🚨 3배·5배는 실제 증거금 규칙이 아니라 민감도 확인용이다. 엔진 C와 캐리는 늘 들고 "
        "있다고 보고 팔 진입만 막는다(줄여서 들어가는 것보다 보수적). k·λ·캐리는 상한 없는 판 "
        "값 그대로이고 다시 맞추지 않는다(캐리 펀딩 = 최근 12개월 고정).",
        "",
        "| 상한 | 캐리 | 막힌 진입 / 전체 | 막힌 거래 net R 합 | 막힌 몫(자본 대비 단순 합) | "
        "8.6년 연환산 | MDD | 2018~2025 중앙 | 마이너스 해 | "
        + " | ".join(_year_label(y) for y in years)
        + " |",
        "|--:|--:|--:|--:|--:|--:|--:|--:|--:|" + "--:|" * len(years),
    ]
    for r in out.caps:
        cells = " | ".join(_pct(float(r.yearly.get(y, float("nan")))) for y in years)
        lines.append(
            f"| {r.cap:.0f}배 | {r.fraction:.0%} | {r.blocked} / {r.entries} | "
            f"{r.blocked_net_r:+.1f}R | {r.blocked_pnl:+.1%} | {r.cagr:+.1%} | {r.mdd:.1%} | "
            f"{r.median:+.1%} | {r.negative} | {cells} |"
        )
    lines += ["", *cap_notes(out)]
    lines += [
        "",
        "## 빠진 위험(WAN-447 §3 그대로 + 담보 할인)",
        "",
        "* **베이시스 순간 변동** — 현물과 무기한의 가격 차가 벌어지는 순간의 평가손은 안 쟀다.",
        "* **숏 다리 증거금 청산** — 급등 때 무기한 숏의 증거금이 모자라 청산되는 경로는 안 쟀다.",
        "* **거래소 위험** — 자산이 한 거래소에 묶인다.",
        "* **통합 증거금(포트폴리오 마진) 전제** — 현물을 선물 증거금으로 쓴다고 본다. 사용자 "
        "계좌는 가입 가능(2026-10-14 확인).",
        "* **담보 할인율 미반영** — 포트폴리오 마진은 담보 코인을 시가 100%가 아니라 할인된 "
        f"비율로 쳐 준다. {COLLATERAL_NOTE} 이 측정은 그 할인을 넣지 않았으므로 실제 여유 "
        "증거금은 이 표보다 작다.",
        "",
        "## 기록 · 한계",
        "",
        *[f"* {n}" for n in out.notes],
        "* 팔 명목은 **진입 순간 확정 자본** 대비다(열린 포지션 평가손익으로 자본이 움직이는 몫과 "
        "보유 중 가격 변화로 명목이 움직이는 몫은 무시).",
        "* 엔진 C 명목은 그날 보유한 배율(전날 종가에 정한 값)이고 일 중 가격 변화는 무시한다.",
        "* 팔 크기 변경을 일 수익의 선형 배율로 근사한 것 · 엔진 C 일 중 낙폭을 일봉 고·저로 잰 것 "
        "· λ를 전체 창에서 맞춘 것은 WAN-449와 같다.",
        "* 판정은 해마다 수익이지 거래당 실력이 아니다.",
        "",
        "**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 · 캐리 운용 여부는 사용자 결정 · 실거래 "
        "보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    with m.MARKETS_PATH.open("rb") as fh:
        built = pickle.load(fh)
    out = run(built)
    panel_frame(out).to_csv(PANELS_CSV, index=False)
    margin_daily_frame(out).to_csv(MARGIN_DAILY_CSV)
    caps_frame(out).to_csv(MARGIN_CAPS_CSV, index=False)
    SUMMARY_PATH.write_text(render(out))
    print(SUMMARY_PATH.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
