"""WAN-463 — 추세추종(엔진 C)의 숏 쪽만 키우면 평균과 가장 나쁜 해가 함께 오르는가.

## 착수 전에 못 박은 것(이슈 본문 그대로 · 결과를 보고 바꾸지 않는다)

* 규칙: 엔진 C 자산별 목표 배율 w가 음수(숏)인 날만 w × s(정본 클립 뒤 · 다시 클립 안 함 ·
  비용과 펀딩은 바뀐 w로) — `wan449.tsmom_returns(short_mult=)`.
* 격자 s ∈ {1.0, 1.25, 1.5, 2.0} · 바스켓 둘(BTC+ETH = 지금 페이퍼 · BTC만 = WAN-462 ①) ·
  k는 바스켓마다 s = 1 판의 앞구간 변동성 일치값으로 고정 · λ는 MDD 35% 재맞춤
  (정본 규칙 = 전체 창 · 엄격 = 앞구간만).
* 선택(앞구간만): 앞구간 해(2018~2023)의 가장 나쁜 해가 가장 큰 s, 동률이면 앞구간 연환산.
* 판정(바스켓마다 · 넷 다 · 고른 s ≠ 1 · 같은 바스켓 s = 1 대비): (1) 전체 가장 나쁜 해 ≥
  (2) 해마다 중앙 ≥ (3) 뒷구간 연환산 ≥ 그리고 뒷구간 수익/MDD ≥ (4) 2018 제외 · 2022 제외
  각각에서 가장 나쁜 해 · 중앙 ≥.
* 숏 스퀴즈 스트레스(판정과 별개): 하락 추세 구간 안 합성 최대 낙폭 · 하락 추세 중 엔진 C 최대
  숏 노출 · 전 이력 최대 7일 BTC 반등 × 그 노출 · 엔진 C 명목 ÷ 자본 최대.

재현: `uv run python -m backtest.wan463_short_side_mult`(시장 `backtest/cache/wan442_markets.pkl` ·
펀딩 WAN-447 캐시 — WAN-460이 다시 만든 정본 캐시). **측정 전용 · 기본값·토대·페이퍼 불변 ·
실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).** 엔진 C 규칙 변경은 재-베이스라인 = 사용자 결정.
"""

from __future__ import annotations

import argparse
import pickle
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan447_funding_carry as fc
from backtest import wan449_tsmom_second_engine as e449
from backtest import wan460_trend_direction as t460

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
SUMMARY_PATH = REPORT_DIR / "wan463_short_side_mult_summary.md"
CSV_PATH = REPORT_DIR / "wan463_short_side_mult.csv"
CHECK_TOL = 1e-9
SHORT_MULTS: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0)
BTC = "BTC/USDT:USDT"
ETH = "ETH/USDT:USDT"
BASKETS: dict[str, tuple[str, ...]] = {"BTC+ETH": (BTC, ETH), "BTC만": (BTC,)}
FRONT_YEARS: tuple[int, ...] = (2018, 2019, 2020, 2021, 2022, 2023)
BEAR_YEARS: tuple[int, ...] = (2018, 2022)
RALLY_DAYS = 7


@dataclass(frozen=True)
class Row:
    basket: str
    s: float
    size: str
    k: float
    lam: float
    worst: float
    worst_year: int
    median: float
    cagr: float
    mdd: float
    front_cagr: float
    back_cagr: float
    back_mdd: float
    front_worst: float
    yearly: dict[int, float]
    down_mdd: float
    max_short_exposure: float
    max_notional: float
    rally_loss: float

    @property
    def back_ratio(self) -> float:
        return self.back_cagr / self.back_mdd

    def without(self, year: int) -> tuple[float, float]:
        """그 해를 뺀 2018~2025의 (가장 나쁜 해, 중앙)."""
        v = [self.yearly[y] for y in e449.YEARS if y != year]
        return float(min(v)), float(np.median(v))


def down_span_mdd(eq: pd.Series, low: pd.Series, down: pd.Series) -> float:
    """BTC 하락 추세 구간마다 그 구간 시작 직전 평가액을 고점으로 다시 잡고 잰 최대 낙폭."""
    worst = 0.0
    peak = None
    prev_close = 1.0
    for d in eq.index:
        if bool(down[d]):
            if peak is None:
                peak = prev_close
            peak = max(peak, float(eq[d]))
            worst = max(worst, 1.0 - float(low[d]) / peak)
        else:
            peak = None
        prev_close = float(eq[d])
    return worst


def max_btc_rally(days: int = RALLY_DAYS) -> float:
    """전 이력(현물→선물 이음 일봉)에서 가장 큰 `days`일 BTC 상승률."""
    bars = e449.daily_bars(BTC, u.ms(e449.FUT_FROM))
    price = (1.0 + bars["ret"].fillna(0.0)).cumprod()
    return float((price / price.shift(days) - 1.0).max())


@dataclass
class Result:
    rows: list[Row]
    picks: dict[str, Row]
    baselines: dict[str, Row]
    verdicts: dict[str, list[str]]
    notes: list[str] = field(default_factory=list)


def _verdict(pick: Row, base: Row) -> tuple[bool, list[str]]:
    lines: list[str] = []
    c1 = pick.worst >= base.worst
    c2 = pick.median >= base.median
    c3 = pick.back_cagr >= base.back_cagr and pick.back_ratio >= base.back_ratio
    c4_parts = []
    for y in BEAR_YEARS:
        pw, pm = pick.without(y)
        bw, bm = base.without(y)
        c4_parts.append((y, pw >= bw and pm >= bm, pw, pm, bw, bm))
    c4 = all(p[1] for p in c4_parts)
    lines.append(
        f"(1) 가장 나쁜 해 {pick.worst:+.1%} vs {base.worst:+.1%} → {'✅' if c1 else '❌'}"
    )
    lines.append(
        f"(2) 해마다 중앙 {pick.median:+.1%} vs {base.median:+.1%} → {'✅' if c2 else '❌'}"
    )
    lines.append(
        f"(3) 뒷구간 연환산 {pick.back_cagr:+.1%} vs {base.back_cagr:+.1%} · 수익/MDD "
        f"{pick.back_ratio:.2f} vs {base.back_ratio:.2f} → {'✅' if c3 else '❌'}"
    )
    for y, ok, pw, pm, bw, bm in c4_parts:
        lines.append(
            f"(4) {y} 빼고: 가장 나쁜 해 {pw:+.1%} vs {bw:+.1%} · 중앙 {pm:+.1%} vs {bm:+.1%} → "
            f"{'✅' if ok else '❌'}"
        )
    passed = pick.s != 1.0 and c1 and c2 and c3 and c4
    return passed, lines


def run(built: u.Markets) -> Result:
    t0 = time.monotonic()
    notes: list[str] = []
    sp, fu, risk = e449.arm_trades(built)
    arm_a = e449.arm_daily_from(sp, fu, risk)
    frame, variants, _n, _nums = e449.run(built, arm=arm_a)
    gap = t460.check_a(frame)
    if gap > CHECK_TOL:
        raise AssertionError(f"정본 검산 실패 — 공개 wan449_daily.csv와 최대 차 {gap:.2e}")
    idx = frame.index
    front = np.asarray(idx < pd.Timestamp(e449.FRONT_END))
    ra, la = frame["arm_ret"], frame["arm_low"]
    st = m.setting(built)
    with fc.FUNDING_CACHE.open("rb") as fh:
        frows, _f = pickle.load(fh)
    lookup = t460.TrendLookup.from_series(t460.btc_trend_signal())
    down = pd.Series([lookup.sign_at(int(d.value // 1_000_000)) < 0 for d in idx], index=idx)
    rally = max_btc_rally()
    notes.append(f"전 이력 최대 {RALLY_DAYS}일 BTC 반등 {rally:+.1%}(스트레스 입력)")

    engines_by_s: dict[float, dict[str, e449.EngineDaily]] = {}
    for sm in SHORT_MULTS:
        eng, gap_r = e449.engine_daily(st, frows, short_mult=sm)
        if gap_r > CHECK_TOL:
            raise AssertionError(f"일봉 수익 검산 실패 {gap_r:.2e}")
        engines_by_s[sm] = eng

    # 검산 — 숏 배율은 롱 보유일(전날·그날 모두 롱이라 비용도 같은 날) 수익을 바꾸지 않는다.
    for sym in (BTC, ETH):
        base_e, alt_e = engines_by_s[1.0][sym], engines_by_s[2.0][sym]
        assert base_e.held is not None and alt_e.held is not None
        h = base_e.held
        steady = (h > 0) & (h.shift(1).fillna(0.0) >= 0) & (h.shift(-1).fillna(0.0) >= 0)
        diff = float((base_e.ret[steady] - alt_e.ret[steady]).abs().max())
        if diff > 0.0:
            raise AssertionError(f"{sym}: 숏 배율이 롱 보유일 수익을 바꿨다({diff:.2e})")
    notes.append("검산 — 숏 배율이 롱 보유일 엔진 C 일 수익을 안 바꾼다(차 0.0 · BTC·ETH)")

    rows: list[Row] = []
    for bname, assets in BASKETS.items():
        k = 0.0
        for sm in SHORT_MULTS:
            eng = engines_by_s[sm]
            rc = pd.concat([eng[a].ret for a in assets], axis=1).mean(axis=1)
            lc = pd.concat([eng[a].low for a in assets], axis=1).mean(axis=1)
            rc, lc = rc.reindex(idx).fillna(0.0), lc.reindex(idx).fillna(0.0)
            held = pd.concat([eng[a].held for a in assets], axis=1).reindex(idx).fillna(0.0)
            if sm == 1.0:
                k = float(ra[front].std() / rc[front].std())
            for size, lam in (
                ("정본 규칙(λ 전체)", e449.fit_lambda(ra, la, rc, lc, k, c.MDD_TARGET)),
                (
                    "엄격(λ 앞구간)",
                    e449.fit_lambda(ra[front], la[front], rc[front], lc[front], k, c.MDD_TARGET),
                ),
            ):
                eq, low = e449.combine(ra, la, rc, lc, k, lam)
                r = t460.metrics("A", size, lam, eq, low)
                yearly = {int(y): float(v) for y, v in r.yearly.items()}
                share = lam * k / len(assets)
                short_exp = float((held.clip(upper=0.0).abs().sum(axis=1) * share)[down].max())
                notional = float((held.abs().sum(axis=1) * share).max())
                rows.append(
                    Row(
                        basket=bname,
                        s=sm,
                        size=size,
                        k=k,
                        lam=lam,
                        worst=r.worst,
                        worst_year=r.worst_year,
                        median=r.median,
                        cagr=r.cagr,
                        mdd=r.mdd,
                        front_cagr=r.front_cagr,
                        back_cagr=r.back_cagr,
                        back_mdd=r.back_mdd,
                        front_worst=min(yearly[y] for y in FRONT_YEARS),
                        yearly=yearly,
                        down_mdd=down_span_mdd(eq, low, down),
                        max_short_exposure=short_exp,
                        max_notional=notional,
                        rally_loss=short_exp * rally,
                    )
                )

    def main_rows(b: str) -> list[Row]:
        return [r for r in rows if r.basket == b and r.size == "정본 규칙(λ 전체)"]

    # 검산 — s = 1이 정본 · WAN-462 ①을 재현한다.
    be1 = main_rows("BTC+ETH")[0]
    pub = e449.yearly(pd.read_csv(e449.DAILY_CSV, index_col=0, parse_dates=True)["combo_eq"])
    d1 = max(abs(be1.yearly[y] - float(pub[y])) for y in e449.YEARS)
    ref_b = next(v for v in variants if v.name == "엔진 C = BTC만")
    b1 = main_rows("BTC만")[0]
    d2 = max(abs(b1.yearly[y] - float(ref_b.yearly[y])) for y in e449.YEARS)
    if d1 > CHECK_TOL or d2 > CHECK_TOL or abs(b1.lam - ref_b.lam) > CHECK_TOL:
        raise AssertionError(f"s = 1 검산 실패(정본 {d1:.2e} · BTC만 {d2:.2e})")
    notes.append(
        f"검산 — s = 1: BTC+ETH ≡ WAN-449 정본(해마다 최대 차 {d1:.1e}) · BTC만 ≡ WAN-462 ① "
        f"(λ {b1.lam:.3f} · 해마다 최대 차 {d2:.1e})"
    )

    picks: dict[str, Row] = {}
    baselines: dict[str, Row] = {}
    verdicts: dict[str, list[str]] = {}
    for bname in BASKETS:
        mr = main_rows(bname)
        base = mr[0]
        pick = max(mr, key=lambda r: (r.front_worst, r.front_cagr))
        passed, lines = _verdict(pick, base)
        picks[bname], baselines[bname] = pick, base
        verdicts[bname] = [
            f"앞구간(2018~2023 가장 나쁜 해)으로 고른 s = **{pick.s:g}**",
            *lines,
            "→ **통과**"
            if passed
            else ("→ **불통과**" if pick.s != 1.0 else "→ **s = 1이 골라짐**"),
        ]
    notes.append(f"측정 {time.monotonic() - t0:.0f}초")
    return Result(rows, picks, baselines, verdicts, notes)


def _p(x: float) -> str:
    return f"{x:+.1%}"


def render(res: Result) -> str:
    years = list(e449.YEARS)
    out = [
        "# WAN-463 — 추세추종의 숏 쪽만 키우면 평균과 가장 나쁜 해가 함께 오르는가",
        "",
        f"> 엔진: 오늘 채택 구성(WAN-449 정본 · 핀 없음) · 창: 현물 {c.WINDOW_START} ~ 선물 "
        f"{harness.DEFAULT_END}(8.6년) · 앞/뒤 경계 {e449.FRONT_END} · MDD 35%(일 저가) 맞춤.",
        "",
    ]
    for bname in BASKETS:
        out += [f"## 판정 — {bname}", "", *[f"* {v}" for v in res.verdicts[bname]], ""]
    out += [
        "## 표 — 정본 규칙(λ 전체 창)",
        "",
        "| 바스켓 | s | k | λ | 가장 나쁜 해 | 중앙 | 연환산 | MDD | 앞 연환산 | 앞 해 최악 "
        "| 뒤 연환산 / MDD | 뒤 수익/MDD |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in res.rows:
        if r.size != "정본 규칙(λ 전체)":
            continue
        out.append(
            f"| {r.basket} | {r.s:g} | {r.k:.3f} | {r.lam:.3f} | **{_p(r.worst)}**({r.worst_year}) "
            f"| {_p(r.median)} | {_p(r.cagr)} | {r.mdd:.1%} | {_p(r.front_cagr)} | "
            f"{_p(r.front_worst)} | {_p(r.back_cagr)} / {r.back_mdd:.1%} | {r.back_ratio:.2f} |"
        )
    out += [
        "",
        "## 해마다(정본 규칙)",
        "",
        "| 바스켓 | s | " + " | ".join(str(y) for y in years) + " | 2026(부분) |",
        "|---|--:|" + "--:|" * (len(years) + 1),
    ]
    for r in res.rows:
        if r.size != "정본 규칙(λ 전체)":
            continue
        cells = " | ".join(_p(r.yearly[y]) for y in years)
        tail = _p(r.yearly[2026]) if 2026 in r.yearly else "—"
        out.append(f"| {r.basket} | {r.s:g} | {cells} | {tail} |")
    out += [
        "",
        "## 엄격 판(λ 앞구간만)",
        "",
        "| 바스켓 | s | λ | 가장 나쁜 해 | 중앙 | 연환산 | MDD(전체) | 뒤 연환산 / MDD |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in res.rows:
        if r.size == "엄격(λ 앞구간)":
            out.append(
                f"| {r.basket} | {r.s:g} | {r.lam:.3f} | {_p(r.worst)}({r.worst_year}) | "
                f"{_p(r.median)} | {_p(r.cagr)} | {r.mdd:.1%} | {_p(r.back_cagr)} / "
                f"{r.back_mdd:.1%} |"
            )
    out += [
        "",
        "## 하락장 하나 빼고(정본 규칙)",
        "",
        "| 바스켓 | s | 2018 빼고 최악 / 중앙 | 2022 빼고 최악 / 중앙 |",
        "|---|--:|--:|--:|",
    ]
    for r in res.rows:
        if r.size != "정본 규칙(λ 전체)":
            continue
        a, b = r.without(2018), r.without(2022)
        out.append(f"| {r.basket} | {r.s:g} | {_p(a[0])} / {_p(a[1])} | {_p(b[0])} / {_p(b[1])} |")
    out += [
        "",
        "## 숏 스퀴즈 스트레스(판정과 별개 · 정본 규칙)",
        "",
        "| 바스켓 | s | 하락 추세 구간 안 최대 낙폭 | 하락 추세 중 엔진 C 최대 숏 노출 "
        f"| 최대 {RALLY_DAYS}일 반등 × 그 노출 | 엔진 C 명목 ÷ 자본 최대 |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for r in res.rows:
        if r.size != "정본 규칙(λ 전체)":
            continue
        out.append(
            f"| {r.basket} | {r.s:g} | {r.down_mdd:.1%} | {r.max_short_exposure:.2f}배 | "
            f"−{r.rally_loss:.1%} | {r.max_notional:.2f}배 |"
        )
    out += [
        "",
        "## 기록 · 검산",
        "",
        *[f"* {n}" for n in res.notes],
        "* 숏 노출 · 명목은 엔진 C만이다(팔 포지션은 따로) · 7일 반등 손실은 그 노출이 반등 내내 "
        "그대로라고 본 단순 곱이다(일 재조정 · 손절 없음 · 펀딩 무시).",
        "* 팔 크기 변경은 일 수익의 선형 배율 근사(WAN-449와 같다) · 팔은 측정 체결(WAN-448) "
        "위 값.",
        "",
        "**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 · 실거래 보류 "
        "유지(`ALPHABLOCK_LIVE_TRADING=false`).**",
        "",
    ]
    return "\n".join(out)


def rows_frame(rows: Sequence[Row]) -> pd.DataFrame:
    recs = []
    for r in rows:
        d = {k: v for k, v in r.__dict__.items() if k != "yearly"}
        d.update({f"y{y}": v for y, v in r.yearly.items()})
        recs.append(d)
    return pd.DataFrame(recs)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--markets", type=Path, default=m.MARKETS_PATH)
    args = ap.parse_args(argv)
    with args.markets.open("rb") as fh:
        built = pickle.load(fh)  # noqa: S301 — 로컬 캐시(WAN-442/460 `--part markets`)
    res = run(built)
    rows_frame(res.rows).to_csv(CSV_PATH, index=False)
    SUMMARY_PATH.write_text(render(res), "utf-8")
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
