"""WAN-439 §2: 「B + 15m」 — 폭락 국면 크기 층(B)에 15m을 더한 판을 두 자로 잰다.

사용자 요청 2026-09-29.

WAN-438 탐색(`tf15_*.py`, 채택 근거 아님)이 「+15m」을 사전 고정 시험으로 통과시켰지만 그 값은
**복리 층에서만** 크기를 바꾼 탐색 시뮬레이터 위의 수였다. 이 모듈은
`wan439_crash_size_layer`의 정식 경로(채택 북 배치 · 인과 폭락 판정 · 검산)를 **그대로** 쓰고
TF만 넓힌다.

## 무엇을 고정했나 (착수 전 · 코드 상수)

* 팔: A(층 없음) · B(폭락 ×0.5, 북 반영) × 변형 둘 — `9TF`(선물 9TF · 현물 8TF) ·
  `+15m`(각각 15m 추가).
  `9TF`는 `+15m`에서 15m 칸·후보를 **빼고** 신호 수를 다시 센 것이다(탐색 `tf15_build.py`와 같다).
* 자 둘(사용자 결정): ① **미끄러짐 포함 · 8.6년 평가손 MDD 35%**(정본) ② **손절가 체결 · MDD 30%**.
  각 자로 크기를 맞추고, 그 크기에서 두 체결 방식을 모두 적는다.
* 판정(자마다 · 팔마다): (1) `+15m`의 8.6년 연환산(그 자의 체결 · 자기 크기) > `9TF`
  **그리고** (2) `9TF` 맞춤 크기에서 창 밖 · 6년 앞 · 6년 뒤 수익/MDD가 `9TF`보다 나쁘지 않다
  (§1과 같은 형태 — 비교 기준은 지금 쓰는 쪽의 크기).

## 검산

* `9TF` × A · 손절가 · 거래당 2% = WAN-436/438 공개 CSV(0.00e+00) — 15m을 더해 만든 후보에서 15m을
  빼면 원래 팔과 같아야 한다.
* `9TF` × B의 미끄러짐 MDD 35% 맞춤 크기 = §1 CSV의 B 크기(같은 입력 · 같은 계산이면 같은 수).

**측정 전용 · 기본값·토대 불변** · 페이퍼 규칙(WAN-435) 변경은 **사용자 결정** · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan438_spot_stress as m438
from backtest import wan439_crash_size_layer as c

CSV_PATH = c.REPORT_DIR / "wan439_b15m.csv"
SUMMARY_PATH = c.REPORT_DIR / "wan439_b15m_summary.md"

TF15 = "15m"
V9 = "9TF"
V15 = "+15m"
VARIANTS: tuple[str, ...] = (V9, V15)
ARMS: tuple[str, ...] = (c.ARM_A, c.ARM_B)
#: (라벨, 맞춤 체결, MDD 목표) — ①이 정본, ②는 사용자가 요청한 비교 자.
RULERS: tuple[tuple[str, str, float], ...] = (
    ("① 미끄러짐 · MDD 35%", "bar_low", 0.35),
    ("② 손절가 · MDD 30%", "stop", 0.30),
)
RISK_CHECK_TOL = 1e-12


def label(variant: str, arm: str) -> str:
    return f"{arm.split()[0]} {variant}"


def own(ruler: str) -> str:
    return f"{ruler} · 자기 맞춤"


def base_size(ruler: str, arm: str) -> str:
    return f"{ruler} · {label(V9, arm)} 맞춤 크기"


def verdict(rows: Sequence[c.Row], ruler: str, fill: str, arm: str) -> tuple[bool, list[str]]:
    def get(variant: str, basis: str, seg: str) -> c.Row:
        hits = [
            r
            for r in rows
            if (r.arm, r.size_basis, r.segment, r.fill) == (label(variant, arm), basis, seg, fill)
        ]
        if len(hits) != 1:
            raise KeyError((variant, arm, basis, seg, fill, len(hits)))
        return hits[0]

    b, p = get(V9, own(ruler), c.SEG_CHAIN), get(V15, own(ruler), c.SEG_CHAIN)
    cond1 = p.cagr > b.cagr
    lines = [
        f"(1) 8.6년 연환산(자기 크기): +15m {p.cagr:+.2%} ({p.risk:.2%}) vs 9TF {b.cagr:+.2%} "
        f"({b.risk:.2%}) → " + ("✅" if cond1 else "❌")
    ]
    ok = cond1
    for seg in c.VERDICT_SEGMENTS:
        rb = get(V9, base_size(ruler, arm), seg)
        rp = get(V15, base_size(ruler, arm), seg)
        good = rp.ratio >= rb.ratio
        ok = ok and good
        lines.append(
            f"(2) 9TF 크기 {rb.risk:.2%} · {seg}: 수익/MDD +15m {rp.ratio:+.3f} "
            f"({rp.total_return:+.1%}/{rp.mdd_low:.1%}) vs 9TF {rb.ratio:+.3f} "
            f"({rb.total_return:+.1%}/{rb.mdd_low:.1%}) → " + ("✅" if good else "❌")
        )
    return ok, lines


def run(jobs: int, payload_dir: Path, root: Path) -> tuple[list[c.Row], list[str]]:
    t0 = time.monotonic()
    notes: list[str] = []
    fut = c.futures_market(jobs, payload_dir, (*c.FUT_TIMEFRAMES, TF15))
    notes.append(f"선물(+15m) 후보 {len(fut.entries)} ({time.monotonic() - t0:.0f}초)")
    spot = c.spot_market(jobs, root, (*m438.TIMEFRAMES, TF15))
    notes.append(f"현물(+15m) 후보 {len(spot.entries)} ({time.monotonic() - t0:.0f}초)")
    bounds = sorted(p.boundary_ms for p in fut.payloads)
    if bounds[-1] - bounds[0] > 7 * 86_400_000:
        raise AssertionError(
            f"선물 칸의 앞/뒤 경계가 일주일 넘게 벌어졌다: {bounds[0]}~{bounds[-1]}."
        )
    boundary_ms = bounds[len(bounds) // 2]
    markets = {V15: (spot, fut), V9: (spot.without(TF15), fut.without(TF15))}
    for v, (sp_mk, fu_mk) in markets.items():
        n15 = sum(e.timeframe == TF15 for e in (*sp_mk.entries, *fu_mk.entries))
        notes.append(
            f"{v}: 현물 후보 {len(sp_mk.entries)} · 선물 후보 {len(fu_mk.entries)} · 15m {n15}"
        )

    placed: dict[tuple[str, str, str], tuple[list[w.PlacedTrade], list[w.PlacedTrade]]] = {}
    for v, (sp_mk, fu_mk) in markets.items():
        for arm in ARMS:
            for fill in c.FILLS:
                placed[(v, arm, fill)] = (
                    c.place_arm(sp_mk, arm, fill, in_book=True),
                    c.place_arm(fu_mk, arm, fill, in_book=True),
                )
    notes += c.checksum(*placed[(V9, c.ARM_A, "stop")])

    rows: list[c.Row] = []
    for ruler, fit_fill, target in RULERS:
        fitted: dict[tuple[str, str], float] = {}
        for v in VARIANTS:
            for arm in ARMS:
                s_fit, f_fit = placed[(v, arm, fit_fill)]

                def mdd_at(
                    r: float, s: list[w.PlacedTrade] = s_fit, f: list[w.PlacedTrade] = f_fit
                ) -> float:
                    return c.chain(s, f, r)[1]

                fitted[(v, arm)] = c.fit_risk(mdd_at, target=target)
        for v in VARIANTS:
            for arm in ARMS:
                sm, fm = markets[v]
                layered = {
                    (e.symbol, e.timeframe, int(e.cand.entry_time))
                    for mk in (sm, fm)
                    for e, mult in zip(mk.entries, mk.layer(arm), strict=True)
                    if mult != 1.0
                }
                bases = {own(ruler): fitted[(v, arm)], base_size(ruler, arm): fitted[(V9, arm)]}
                for basis, risk in bases.items():
                    for fill in c.FILLS:
                        s, f = placed[(v, arm, fill)]
                        rows += c.segment_rows(
                            label(v, arm), True, fill, basis, risk, s, f, boundary_ms, layered
                        )
    notes += cross_check(rows)
    notes.append(f"총 {time.monotonic() - t0:.0f}초")
    return rows, notes


def cross_check(rows: Sequence[c.Row]) -> list[str]:
    """9TF × B의 ① 맞춤 크기 = §1 CSV의 B 자기 맞춤 크기(같은 입력 · 같은 계산)."""
    if not c.CSV_PATH.exists():
        return ["§1 CSV 없음 — 크기 대조 건너뜀"]
    main = pd.read_csv(c.CSV_PATH)
    ref = main[
        (main.arm == c.ARM_B)
        & (main.in_book)
        & (main.size_basis == c.SIZE_OWN)
        & (main.segment == c.SEG_CHAIN)
        & (main.fill == c.FIT_FILL)
    ]
    got = [
        r
        for r in rows
        if (r.arm, r.size_basis, r.segment, r.fill)
        == (label(V9, c.ARM_B), own(RULERS[0][0]), c.SEG_CHAIN, c.FIT_FILL)
    ]
    if len(ref) != 1 or len(got) != 1:
        raise AssertionError("§1 대조 행을 못 찾았다")
    diff = abs(float(ref.risk.iloc[0]) - got[0].risk)
    if diff > RISK_CHECK_TOL or abs(float(ref.total_return.iloc[0]) - got[0].total_return) > 1e-9:
        raise AssertionError(f"9TF × B가 §1과 다르다: 크기 차 {diff:.2e}")
    return [
        f"9TF × B ≡ §1 B(크기 {got[0].risk:.4%} · 수익 {got[0].total_return:+.2%})",
        f"  └ §1과 크기 차 {diff:.2e}",
    ]


def render(rows: Sequence[c.Row], notes: Sequence[str], elapsed: str) -> str:
    out = [
        "# WAN-439 §2 — B + 15m (두 자)",
        "",
        "> 자동 생성(`uv run python -m backtest.wan439_b15m`). 결정문 `docs/decisions/wan439.md`.",
        "> 🚨 채택 근거 아님 · 기본값·토대·페이퍼 규칙 불변.",
        "",
        "## 핵심 표 — 각 자로 크기 맞춤 (칸: 총수익 · 연환산 / 평가손 MDD)",
        "",
    ]
    for ruler, fit_fill, _ in RULERS:
        out += [f"### {ruler}", ""]
        out += ["| 팔 | 체결 | 크기 | " + " | ".join(c.SEGMENTS) + " |"]
        out += ["|" + "---|" * (3 + len(c.SEGMENTS))]
        for v in VARIANTS:
            for arm in ARMS:
                for fill in (fit_fill, *[f for f in c.FILLS if f != fit_fill]):
                    seg = {
                        r.segment: r
                        for r in rows
                        if (r.arm, r.size_basis, r.fill) == (label(v, arm), own(ruler), fill)
                    }
                    if not seg:
                        continue
                    risk = next(iter(seg.values())).risk
                    cells = " | ".join(c._cell(seg[s]) for s in c.SEGMENTS)
                    tag = "(맞춤 체결)" if fill == fit_fill else "(같은 크기 병기)"
                    out.append(
                        f"| {label(v, arm)} | {w._FILL_LABEL[fill]} {tag} | {risk:.2%} | {cells} |"
                    )
        out.append("")
        for arm in ARMS:
            ok, lines = verdict(rows, ruler, fit_fill, arm)
            out.append(f"**{arm.split()[0]}: +15m vs 9TF — {'통과' if ok else '불통과'}**")
            out.append("")
            out += [f"* {line}" for line in lines]
            out.append("")
    out += ["## 실행 기록", ""] + [f"* {n}" for n in notes] + [f"* {elapsed}", ""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--payload-dir", type=Path, default=w.DEFAULT_PAYLOAD_DIR)
    ap.add_argument("--root", type=Path, default=m438.DEFAULT_ROOTS / "stress")
    ap.add_argument("--from-csv", action="store_true")
    args = ap.parse_args(argv)
    if args.from_csv:
        rows = c.rows_from_frame(pd.read_csv(CSV_PATH))
        old = SUMMARY_PATH.read_text(encoding="utf-8") if SUMMARY_PATH.exists() else ""
        notes = [
            ln[2:] for ln in old.split("## 실행 기록", 1)[-1].splitlines() if ln.startswith("* ")
        ]
        SUMMARY_PATH.write_text(render(rows, notes[:-1], notes[-1] if notes else ""), "utf-8")
        return 0
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    t0 = time.monotonic()
    rows, notes = run(jobs, args.payload_dir, args.root)
    c.rows_frame(rows).to_csv(CSV_PATH, index=False)
    SUMMARY_PATH.write_text(
        render(rows, notes, f"실측 {time.monotonic() - t0:.0f}초 · `--jobs {jobs}`"), "utf-8"
    )
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
