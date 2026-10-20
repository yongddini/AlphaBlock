"""WAN-462 — 팔 + 추세추종 합성의 손잡이 둘.

① 엔진 C = BTC만(앞/뒤 · 엄격 판) ② 동시 위험 합계 한도로 같은 MDD 35%에서 크기를 키울 수 있나.

## 착수 전에 못 박은 것(이슈 본문 그대로 · 결과를 보고 바꾸지 않는다)

* 좌표: WAN-449 정본(팔 = 측정 체결 α · 거래대금 상위 31 · 몰림 규칙 · 폭락 ×0.5 · 8.6년).
  기준선 = 정본. 앞/뒤 = WAN-449 `FRONT_END`(2024-08-08).
* ① 엔진 C 바스켓만 BTC 단독. 크기 두 판 — (정본 규칙) k 앞구간 · λ 전체 창 MDD 35% /
  (엄격) k·λ **둘 다 앞구간만**. 판정: 엄격 판에서도 뒷구간 연환산 · 뒷구간 수익/MDD가 정본
  이상이면 「앞뒤 모두 유지」, 아니면 「앞구간에서만」.
* ② 팔 진입 순간 열린 팔 포지션의 크기 배율 합(몰림 예산 × 폭락 층 — 거래당 리스크 단위) + 그
  진입이 한도 U를 넘으면 **북 안에서** 건너뛴다(`run_leverage_book(open_weight_cap=)`).
  U 격자 = 정본 배치의 동시 위험 합계(노출) 분포의 p50 · p75 · p90 · p95 · p99 · 최대.
  크기: 팔 리스크 · k 고정, λ만 MDD 35% 재맞춤. 판정은 넷 다 —
  (1) 가장 나쁜 해 · 해마다 중앙 ≥ 기준선 (2) 앞 · 뒤 연환산 모두 ≥ 기준선
  (3) 이웃한 두 격자점 이상에서 성립 (4) U는 앞구간 연환산만으로 고른다.

재현: `uv run python -m backtest.wan462_combo_levers`(시장 = `backtest/cache/wan442_markets.pkl`
또는 WAN-460 `wan460_markets.pkl` · 펀딩 WAN-447 캐시). **측정 전용 · 채택 권고 없음 ·
기본값·토대·페이퍼 불변 · 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**
"""

from __future__ import annotations

import argparse
import pickle
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq
from backtest import wan447_funding_carry as fc
from backtest import wan448_stop_fill_ticks as st
from backtest import wan449_tsmom_second_engine as e449
from backtest import wan460_trend_direction as t460

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
SUMMARY_PATH = REPORT_DIR / "wan462_combo_levers_summary.md"
CSV_PATH = REPORT_DIR / "wan462_combo_levers.csv"
CHECK_TOL = 1e-9
QUANTILES: tuple[tuple[str, float], ...] = (
    ("p50", 0.50),
    ("p75", 0.75),
    ("p90", 0.90),
    ("p95", 0.95),
    ("p99", 0.99),
    ("최대", 1.00),
)
BTC = "BTC/USDT:USDT"
LABEL_BASE = "정본(바스켓 BTC·ETH · 한도 없음)"


@dataclass(frozen=True)
class Row:
    part: str
    label: str
    cap: float | None
    k: float
    lam: float
    worst: float
    worst_year: int
    median: float
    negative_years: int
    cagr: float
    mdd: float
    front_cagr: float
    front_mdd: float
    back_cagr: float
    back_mdd: float
    yearly: tuple[float, ...]
    trades: int
    max_open_weight: float


def summarize(
    part: str,
    label: str,
    cap: float | None,
    k: float,
    lam: float,
    eq: pd.Series,
    low: pd.Series,
    trades: int,
    max_w: float,
) -> Row:
    r = t460.metrics("A", label, lam, eq, low)
    yr = tuple(float(r.yearly.get(y, float("nan"))) for y in e449.YEARS)
    return Row(
        part,
        label,
        cap,
        k,
        lam,
        r.worst,
        r.worst_year,
        r.median,
        r.negative_years,
        r.cagr,
        r.mdd,
        r.front_cagr,
        r.front_mdd,
        r.back_cagr,
        r.back_mdd,
        yr,
        trades,
        max_w,
    )


def open_weights_at_entry(trades: Sequence[w.PlacedTrade]) -> np.ndarray:
    """진입 순간마다 (이미 열린 거래의 크기 배율 합 + 그 진입) — 북의 한도 판정과 같은 자.

    북 규약: 같은 시각 `exit_time == now`인 포지션은 이미 닫혔다(반개구간 `[entry, exit)`).
    """
    ts = sorted(trades, key=lambda x: (x.entry_time, x.exit_time))
    out = np.empty(len(ts))
    for i, a in enumerate(ts):
        out[i] = sum(b.size for b in ts[:i] if b.exit_time > a.entry_time) + a.size
    return out


def place_capped(
    smk: c.Market, fmk: c.Market, alphas: dict[str, float], cap: float | None
) -> tuple[list[w.PlacedTrade], list[w.PlacedTrade]]:
    """`t460.place_branch`와 같은 배치(두 체결 극단 → 측정 α) + 동시 위험 합계 한도."""
    pl = {
        f: (
            c.place_arm(smk, c.ARM_B, f, in_book=True, open_risk_cap=cap),
            c.place_arm(fmk, c.ARM_B, f, in_book=True, open_risk_cap=cap),
        )
        for f in c.FILLS
    }
    sp, fu = (
        st.blended_trades(pl["stop"][k], pl["bar_low"][k], st.alphas_for(pl["stop"][k], mk, alphas))
        for k, mk in ((0, smk), (1, fmk))
    )
    return sp, fu


def fit_front_lambda(
    ra: pd.Series, la: pd.Series, rc: pd.Series, lc: pd.Series, k: float, front: np.ndarray
) -> float:
    return e449.fit_lambda(ra[front], la[front], rc[front], lc[front], k, c.MDD_TARGET)


@dataclass
class Result:
    rows: list[Row]
    notes: list[str]
    verdict_1: list[str]
    verdict_2: list[str]


def run(built: u.Markets) -> Result:
    t0 = time.monotonic()
    notes: list[str] = []
    sp_a, fu_a, risk = e449.arm_trades(built)
    arm_a = e449.arm_daily_from(sp_a, fu_a, risk)
    frame, variants, _n, _nums = e449.run(built, arm=arm_a)
    gap = t460.check_a(frame)
    if gap > CHECK_TOL:
        raise AssertionError(f"정본 검산 실패 — 공개 wan449_daily.csv와 최대 차 {gap:.2e}")
    notes.append(f"검산 — 정본 ≡ 공개 `wan449_daily.csv`(일 곡선 최대 차 {gap:.1e})")
    main = variants[0]
    k0, lam0 = main.k, main.lam
    idx = frame.index
    front = np.asarray(idx < pd.Timestamp(e449.FRONT_END))
    ra, la = frame["arm_ret"], frame["arm_low"]
    rc, lc = frame["c_ret"], frame["c_low"]
    rows: list[Row] = []
    n_trades_a = len(sp_a) + len(fu_a)
    w_a = open_weights_at_entry(sp_a)
    w_b = open_weights_at_entry(fu_a)
    max_w_a = float(max(w_a.max(), w_b.max()))
    base_eq, base_low = e449.combine(ra, la, rc, lc, k0, lam0)
    base = summarize("기준", LABEL_BASE, None, k0, lam0, base_eq, base_low, n_trades_a, max_w_a)
    rows.append(base)

    # ── ① 엔진 C = BTC만 ────────────────────────────────────────────
    s = m.setting(built)
    with fc.FUNDING_CACHE.open("rb") as fh:
        frows, _f = pickle.load(fh)
    engines, worst_gap = e449.engine_daily(s, frows)
    if worst_gap > CHECK_TOL:
        raise AssertionError(f"일봉 수익 검산 실패 {worst_gap:.2e}")
    rcb = engines[BTC].ret.reindex(idx).fillna(0.0)
    lcb = engines[BTC].low.reindex(idx).fillna(0.0)
    kb = float(ra[front].std() / rcb[front].std())
    lamb = e449.fit_lambda(ra, la, rcb, lcb, kb, c.MDD_TARGET)
    ref = next(v for v in variants if v.name == "엔진 C = BTC만")
    yb_eq, yb_low = e449.combine(ra, la, rcb, lcb, kb, lamb)
    ydiff = float((e449.yearly(yb_eq) - ref.yearly).abs().max())
    if abs(kb - ref.k) > CHECK_TOL or abs(lamb - ref.lam) > CHECK_TOL or ydiff > CHECK_TOL:
        raise AssertionError("「BTC만」 판이 WAN-449 참고 판(k · λ · 해마다)을 재현하지 못한다")
    notes.append(
        f"검산 — 「BTC만」 판 ≡ WAN-449 참고 판(k {kb:.3f} · λ {lamb:.3f} · 해마다 최대 차 "
        f"{ydiff:.1e})"
    )
    rows.append(
        summarize(
            "①", "엔진 C = BTC만 · 정본 규칙", None, kb, lamb, yb_eq, yb_low, n_trades_a, max_w_a
        )
    )
    lam_strict = fit_front_lambda(ra, la, rcb, lcb, kb, front)
    s_eq, s_low = e449.combine(ra, la, rcb, lcb, kb, lam_strict)
    rows.append(
        summarize(
            "①",
            "엔진 C = BTC만 · 엄격(λ 앞구간)",
            None,
            kb,
            lam_strict,
            s_eq,
            s_low,
            n_trades_a,
            max_w_a,
        )
    )
    lam_strict0 = fit_front_lambda(ra, la, rc, lc, k0, front)
    s0_eq, s0_low = e449.combine(ra, la, rc, lc, k0, lam_strict0)
    rows.append(
        summarize(
            "①",
            "정본 바스켓 · 엄격(λ 앞구간) — 대조",
            None,
            k0,
            lam_strict0,
            s0_eq,
            s0_low,
            n_trades_a,
            max_w_a,
        )
    )
    strict_b, strict_0 = rows[-2], rows[-1]
    keep = (
        strict_b.back_cagr >= strict_0.back_cagr
        and strict_b.back_cagr / strict_b.back_mdd >= strict_0.back_cagr / strict_0.back_mdd
    )
    verdict_1 = [
        f"엄격 판 뒷구간 — BTC만 {strict_b.back_cagr:+.1%} / MDD {strict_b.back_mdd:.1%} "
        f"(수익/MDD {strict_b.back_cagr / strict_b.back_mdd:.2f}) vs 정본 바스켓 "
        f"{strict_0.back_cagr:+.1%} / MDD {strict_0.back_mdd:.1%} "
        f"({strict_0.back_cagr / strict_0.back_mdd:.2f})",
        (
            "→ **앞뒤 모두 유지**"
            if keep
            else (
                "→ **판정 규칙상 미통과** — 뒷구간에서 더 벌었지만 그만큼 더 깊이 빠져 위험조정"
                "(수익/MDD)이 정본에 못 미친다"
                if strict_b.back_cagr >= strict_0.back_cagr
                else "→ **앞구간에서만**(뒷구간 연환산이 정본 바스켓에 못 미친다)"
            )
        ),
    ]
    if abs(strict_b.lam - rows[1].lam) < CHECK_TOL:
        verdict_1.append(
            "엄격 판(λ 앞구간)이 정본 규칙 판과 같다 — 최대 낙폭(2021-05-19)이 앞구간이라 λ가 "
            "원래 앞구간만으로 정해지고 있었다(뒷구간은 λ에 안 쓰였다)"
        )

    # ── ② 동시 위험 합계 한도 ────────────────────────────────────────
    expo = np.concatenate([w_a, w_b])
    grid = [(name, float(np.quantile(expo, q))) for name, q in QUANTILES]
    notes.append(
        "② 격자(정본 배치의 진입 순간 동시 위험 합계 분포 · 거래당 리스크 단위) — "
        + " · ".join(f"{n} {v:.2f}" for n, v in grid)
    )
    top = s.rotations(lambda d, tm: liq.select_liquidity(d, tm, top=True))
    smk, _ = m.apply_rotation(s.spot_pool, top[0], force_close=False)
    fmk, _ = m.apply_rotation(s.fut_pool, top[1], force_close=False)
    alphas = t460.stop_alphas()
    cap_rows: list[Row] = []
    for name, cap in grid:
        sp, fu = place_capped(smk, fmk, alphas, cap)
        mw = float(max(open_weights_at_entry(sp).max(), open_weights_at_entry(fu).max()))
        if mw > cap * (1.0 + 1e-9):
            raise AssertionError(f"한도 {cap}인데 진입 순간 위험 합계 {mw}가 넘는다")
        arm = e449.arm_daily_from(sp, fu, risk)
        rac = arm.ret.reindex(idx).fillna(0.0)
        lac = arm.low.reindex(idx).fillna(0.0)
        lam = e449.fit_lambda(rac, lac, rc, lc, k0, c.MDD_TARGET)
        eq, low = e449.combine(rac, lac, rc, lc, k0, lam)
        cap_rows.append(
            summarize("②", f"한도 U = {name}", cap, k0, lam, eq, low, len(sp) + len(fu), mw)
        )
    rows += cap_rows
    notes.append("검산 — 한도를 켠 판마다 진입 순간 위험 합계 ≤ U(거래 목록으로 · 넘으면 죽는다)")
    # 한도 = 최대 노출이면 아무 진입도 안 막아야 한다 — 그 판은 기준선과 같아야 한다(배선 검산).
    at_max = cap_rows[-1]
    same = (
        at_max.trades == base.trades
        and abs(at_max.cagr - base.cagr) < CHECK_TOL
        and abs(at_max.lam - base.lam) < CHECK_TOL
    )
    if not same:
        raise AssertionError("한도 = 최대 노출인데 기준선과 다르다 — 한도 배선이 진입을 바꾼다")
    notes.append(
        "검산 — 한도 = 최대 노출 판 ≡ 기준선(거래 수 · 연환산 · λ) — 한도가 안 걸리면 무동작"
    )

    def ok(r: Row) -> bool:
        return (
            r.worst >= base.worst
            and r.median >= base.median
            and r.front_cagr >= base.front_cagr
            and r.back_cagr >= base.back_cagr
        )

    pick = max(range(len(cap_rows)), key=lambda i: cap_rows[i].front_cagr)
    chosen = cap_rows[pick]
    neighbors = [cap_rows[j] for j in (pick - 1, pick + 1) if 0 <= j < len(cap_rows)]
    plateau = ok(chosen) and any(ok(nb) for nb in neighbors)
    verdict_2 = [
        f"앞구간 연환산으로 고른 U = {chosen.label.split('= ')[1]}({chosen.cap:.2f}) — "
        f"가장 나쁜 해 {chosen.worst:+.1%} · 중앙 {chosen.median:+.1%} · 앞 "
        f"{chosen.front_cagr:+.1%} · 뒤 {chosen.back_cagr:+.1%} · λ {chosen.lam:.3f} "
        f"(기준선 {base.worst:+.1%} · {base.median:+.1%} · {base.front_cagr:+.1%} · "
        f"{base.back_cagr:+.1%} · λ {base.lam:.3f})",
        f"조건 (1)·(2) 그 점: {'충족' if ok(chosen) else '미충족'} · (3) 이웃 격자점: "
        + ", ".join(
            f"{nb.label.split('= ')[1]} {'충족' if ok(nb) else '미충족'}" for nb in neighbors
        ),
        "→ **같은 MDD에서 크기를 키운다(통과)**" if plateau else "→ **불통과**",
    ]
    notes.append(f"측정 {time.monotonic() - t0:.0f}초")
    return Result(rows, notes, verdict_1, verdict_2)


def _p(x: float) -> str:
    return f"{x:+.1%}"


def render(res: Result) -> str:
    years = list(e449.YEARS)
    out = [
        "# WAN-462 — 팔 + 추세추종 합성의 손잡이 둘",
        "",
        f"> 엔진: 오늘 채택 구성(WAN-449 정본 · 핀 없음) · 창: 현물 {c.WINDOW_START} ~ 선물 "
        f"{harness.DEFAULT_END}(8.6년) · 앞/뒤 경계 {e449.FRONT_END}. "
        "🚨 **채택 권고를 내지 않는다.**",
        "",
        "## ① 엔진 C = BTC만 — 판정",
        "",
        *[f"* {v}" for v in res.verdict_1],
        "",
        "## ② 동시 위험 합계 한도 — 판정",
        "",
        *[f"* {v}" for v in res.verdict_2],
        "",
        "## 표",
        "",
        "| 축 | 판 | k | λ | 가장 나쁜 해 | 중앙 | 마이너스 해 | 연환산 | MDD | 앞 연환산 / MDD "
        "| 뒤 연환산 / MDD | 팔 거래 | 최대 동시 위험 |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for r in res.rows:
        out.append(
            f"| {r.part} | {r.label} | {r.k:.3f} | {r.lam:.3f} | **{_p(r.worst)}**({r.worst_year}) "
            f"| {_p(r.median)} | {r.negative_years} | {_p(r.cagr)} | {r.mdd:.1%} | "
            f"{_p(r.front_cagr)} / {r.front_mdd:.1%} | {_p(r.back_cagr)} / {r.back_mdd:.1%} | "
            f"{r.trades} | {r.max_open_weight:.2f} |"
        )
    out += [
        "",
        "## 해마다(합성)",
        "",
        "| 축 | 판 | " + " | ".join(str(y) for y in years) + " |",
        "|---|---|" + "--:|" * len(years),
    ]
    for r in res.rows:
        out.append(f"| {r.part} | {r.label} | " + " | ".join(_p(v) for v in r.yearly) + " |")
    out += [
        "",
        "## 기록 · 검산",
        "",
        *[f"* {n}" for n in res.notes],
        "* ①의 「BTC만」은 WAN-449 표를 보고 고른 판이다 — 엄격 판(λ를 앞구간만으로)의 뒷구간이 "
        "그 선택 편향을 거른 확인용이다(WAN-161).",
        "* ②의 위험 단위는 팔의 크기 배율(몰림 예산 × 폭락 층)이고 엔진 C는 세지 않는다 · λ는 전체 "
        "창으로 맞춘다(이슈 규정) · 한도는 북 안에서 판정해 빈 칸·자본에 뒤 후보가 들어올 수 있다.",
        "* 팔 크기 변경은 일 수익의 선형 배율 근사(WAN-449와 같다) · 전부 측정 체결(WAN-448) "
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
        d.update({f"y{y}": v for y, v in zip(e449.YEARS, r.yearly, strict=True)})
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
