"""WAN-445: 스토캐스틱 팔 크기 = 손절폭 비례(명목 고정) — 무작위 20회와 31종목에서 좋아지는가.

## 왜 이 모듈이 있나

WAN-444(거래대금 상위 31종목)는 근소하게 불통과였다. 종목 선택과 **독립인 축**인 크기 규칙이
남은 후보다. WAN-424 §8 탐색에서 「손절폭에 비례한 크기」(거래당 리스크 ∝ 손절폭 = 거래당 명목
고정)는 26개 설정 중 유일하게 앞구간·뒷구간 모두에서 수익/MDD를 올렸다 — 다만 스크래치 · 옛
좌표였다. 이 모듈이 B + 15m · 8.6년 · 채택 북 경로에서 한 번 잰다.

## 무엇을 고정했나 (착수 전 · 코드 상수 — 결과를 보고 옮기지 않는다 · 한 규칙 · 한 번)

* **장비 = WAN-442/444 그대로** — 시장 피클 · 평가(`wan442.evaluate`) · 같은 무작위 교체 20회.
* **규칙**: 거래 크기 배율 = 기존 층(B 폭락 ×0.5) × `d / D`. `d` = 진입가 대비 **원래** 손절
  거리(손절 2배 규칙은 상수배라 무관), `D` = **앞구간 진입 전부의 `d` 평균**(한 상수 · 모든 판에
  공통 · 뒷구간 정보를 안 쓴다). 층은 채택 북 배치에도 들어간다(B와 같은 `in_book=True`). 크기는
  기존처럼 미끄러짐 MDD 35%에 맞춘다(스케일은 맞춤이 흡수한다).
* **판정(둘 다)**: (1) 무작위 20회 중 앞구간 연환산이 좋아진 판 ≥ `PASS_MIN_IMPROVED` **이고**
  뒷구간 수익/MDD(앞구간 맞춤 크기)가 좋아진 판 ≥ `PASS_MIN_IMPROVED`(부호 검정 한쪽 p ≈ 0.02씩)
  (2) 손으로 고른 31종목에서 앞·뒤 둘 다 좋아짐.
* **비교 판**: 무작위 교체 20회 · 손으로 고른 31종목 고정 · (참고) 거래대금 상위 31종목. 전부
  「새 진입만 막기」 처리.

**측정 전용 · 기본값·토대·페이퍼 규칙 불변** · 핀 없음(WAN-305) · WAN-443 이후 정렬 · 실거래
보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import math
import pickle
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
CSV_PATH = REPORT_DIR / "wan445_width_sizing.csv"
PICKS_PATH = REPORT_DIR / "wan445_width_picks.csv"
SUMMARY_PATH = REPORT_DIR / "wan445_width_sizing_summary.md"

# --- 착수 전 고정 ---------------------------------------------------------------------------
PASS_MIN_IMPROVED = 15
"""무작위 20회 중 앞·뒤 각각 이 수 이상 좋아져야 한다(부호 검정 한쪽 p ≈ 0.021)."""
FLAT = "균일"
WIDTH = "손절폭 비례"
SIZINGS: tuple[str, ...] = (FLAT, WIDTH)
STAGE_RANDOM = "무작위"
STAGE_HAND = "31종목"
STAGE_LIQ = "거래대금(참고)"
CHECK_TOL = 1e-12
"""균일 판이 WAN-442 공개 CSV와 같은 값인지 — 텍스트 왕복 허용 오차."""


# ---------------------------------------------------------------------------
# 규칙 — 순수 함수
# ---------------------------------------------------------------------------


def stop_width(entry: w.ArmEntry) -> float:
    """진입가 대비 원래 손절 거리(손절 배수 적용 전)."""
    e = float(entry.cand.entry_price)
    d = (e - float(entry.cand.stop_price)) / e
    if not d > 0.0:
        raise ValueError(f"손절 거리가 양수가 아니다: {entry.key} → {d}")
    return d


def reference_width(spot: c.Market, fut: c.Market, boundary_ms: int) -> float:
    """앞구간 진입 전부(현물 창 밖 전부 + 선물 대표 경계 이전)의 손절 거리 평균 — 한 상수."""
    ds = [stop_width(e) for e in spot.entries]
    ds += [stop_width(e) for e in fut.entries if int(e.cand.entry_time) < boundary_ms]
    if not ds:
        raise ValueError("앞구간 진입이 없다")
    return float(np.mean(ds))


@dataclass
class WidthMarket(c.Market):
    """층 배율에 `d / D`를 곱하는 시장 — 나머지(후보 · 신호 수 · BTC 특징)는 원래 시장 그대로."""

    ref_width: float = 1.0

    def layer(self, arm: str) -> list[float]:
        base = super().layer(arm)
        return [b * stop_width(e) / self.ref_width for b, e in zip(base, self.entries, strict=True)]


def with_width(mk: c.Market, ref: float) -> WidthMarket:
    return WidthMarket(
        mk.name, mk.payloads, mk.entries, mk.counts, mk.btc_24h, mk.vol_ratio, ref_width=ref
    )


def verdict(
    pairs: Sequence[tuple[m.Pick, m.Pick]], hand: tuple[m.Pick, m.Pick]
) -> tuple[bool, list[str]]:
    """(통과 여부, 근거 줄) — `pairs`는 무작위 판마다 (균일, 비례)."""
    if len(pairs) != m.RANDOM_DRAWS:
        raise ValueError(f"무작위 판이 {m.RANDOM_DRAWS}개여야 한다: {len(pairs)}")
    front = sum(b.front_cagr > a.front_cagr for a, b in pairs)
    back = sum(b.back_ratio > a.back_ratio for a, b in pairs)
    ok1 = front >= PASS_MIN_IMPROVED and back >= PASS_MIN_IMPROVED
    hf, hw = hand
    ok2 = hw.front_cagr > hf.front_cagr and hw.back_ratio > hf.back_ratio
    lines = [
        f"(1) 무작위 {len(pairs)}회 — 앞구간 연환산 좋아진 판 {front}개 · "
        f"뒷구간 수익/MDD 좋아진 판 {back}개(각 기준 ≥ {PASS_MIN_IMPROVED}) → "
        + ("✅" if ok1 else "❌"),
        f"(2) 손으로 고른 31종목 — 앞 {hf.front_cagr:+.1%} → {hw.front_cagr:+.1%} · 뒤 "
        f"{hf.back_ratio:+.3f} → {hw.back_ratio:+.3f} → " + ("✅" if ok2 else "❌"),
    ]
    return ok1 and ok2, lines


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


@dataclass
class Result:
    picks: list[m.Pick]
    rows: list[c.Row]
    notes: list[str]
    ref_width: float


def checksum_flat(flat: Sequence[m.Pick], ref: Path = m.PICKS_PATH) -> str:
    """균일 판(무작위 20회 · 31종목) = WAN-442 공개 CSV의 같은 판 — 같은 장비라는 자격 증명."""
    frame = pd.read_csv(ref)
    frame = frame[frame["mode"] == m.MODE_DROP]
    by_cfg = {str(r["config"]): r for r in frame.to_dict("records")}
    worst = 0.0
    for p in flat:
        rec = by_cfg[p.config.removesuffix(f" · {FLAT}")]
        for key in ("front_cagr", "back_ratio", "full_cagr", "front_risk"):
            worst = max(worst, abs(float(rec[key]) - float(getattr(p, key))))
        if int(rec["trades"]) != p.trades:
            raise AssertionError(f"{p.config} 거래 수가 WAN-442와 다르다")
    if worst > CHECK_TOL:
        raise AssertionError(f"균일 판이 WAN-442와 다르다(최대 차 {worst:.2e})")
    return (
        f"검산 — 균일 판 {len(flat)}개(무작위 20회 + 31종목) ≡ WAN-442 공개 CSV"
        f"(최대 차 {worst:.2e} · 거래 수 정수 일치)"
    )


def _multiplier_note(spot: c.Market, fut: c.Market, ref: float) -> str:
    ds = np.array([stop_width(e) for e in [*spot.entries, *fut.entries]]) / ref
    q = np.quantile(ds, [0.1, 0.5, 0.9])
    return (
        f"손절폭 기준 D = {ref:.4%}(앞구간 진입 평균) · 풀 전체 배율 d/D 10%/50%/90% = "
        f"{q[0]:.2f} / {q[1]:.2f} / {q[2]:.2f} · 최대 {ds.max():.2f}"
    )


def _breach_note(label: str, fut: c.Market, risk: float) -> str:
    """8.6년 맞춤 크기에서 선물 복리 경로의 명목 한도(자본 × 5) 초과 진입 수 — 정보용."""
    placed = c.place_arm(fut, c.ARM_B, c.FIT_FILL, in_book=True)
    path = w.mtm_path(placed, risk=risk)
    return f"{label}: 8.6년 맞춤 크기 {risk:.2%}에서 복리 명목 한도 초과 진입 {path.breaches}건"


def run(built: u.Markets) -> Result:
    c.assert_adopted_take_profit_liquidity()
    t0 = time.monotonic()
    st = m.setting(built)
    ref = reference_width(st.spot_pool, st.fut_pool, st.boundary_ms)
    notes = [*built.notes, st.note, _multiplier_note(st.spot_pool, st.fut_pool, ref)]
    picks: list[m.Pick] = []
    rows: list[c.Row] = []

    def both(stage: str, name: str, s_mk: c.Market, f_mk: c.Market) -> tuple[m.Pick, m.Pick]:
        out: list[m.Pick] = []
        for sizing in SIZINGS:
            s2, f2 = (
                (s_mk, f_mk) if sizing == FLAT else (with_width(s_mk, ref), with_width(f_mk, ref))
            )
            p, rr = m.evaluate(m.MODE_DROP, f"{name} · {sizing}", stage, s2, f2, st.boundary_ms)
            out.append(p)
            rows.extend(rr)
        picks.extend(out)
        return out[0], out[1]

    hand = both(STAGE_HAND, m.HAND31, st.hand_spot, st.hand_fut)
    notes.append(_breach_note(f"31종목 {FLAT}", st.hand_fut, hand[0].full_risk))
    notes.append(_breach_note(f"31종목 {WIDTH}", with_width(st.hand_fut, ref), hand[1].full_risk))
    top = st.rotations(lambda d, t: liq.select_liquidity(d, t, top=True))
    s_mk, _ = m.apply_rotation(st.spot_pool, top[0], force_close=False)
    f_mk, _ = m.apply_rotation(st.fut_pool, top[1], force_close=False)
    both(STAGE_LIQ, liq.TOP, s_mk, f_mk)
    pairs: list[tuple[m.Pick, m.Pick]] = []
    for k, rot in enumerate(st.random_rotations()):
        s_mk, _ = m.apply_rotation(st.spot_pool, rot[0], force_close=False)
        f_mk, _ = m.apply_rotation(st.fut_pool, rot[1], force_close=False)
        pairs.append(both(STAGE_RANDOM, f"무작위 교체 #{k:02d}", s_mk, f_mk))
    notes.append(checksum_flat([hand[0], *(a for a, _ in pairs)]))
    hit = [p for p in picks if min(p.front_risk, p.full_risk) <= c.FIT_LO * 1.001]
    hit += [p for p in picks if max(p.front_risk, p.full_risk) >= c.FIT_HI * 0.999]
    notes.append(
        "크기 맞춤이 탐색 범위 끝에 닿은 판: "
        + (", ".join(p.config for p in hit) if hit else "없음")
    )
    notes.append(f"총 {time.monotonic() - t0:.0f}초")
    return Result(picks, rows, notes, ref)


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    return "—" if math.isnan(x) else f"{x:+.1%}"


def _pairs(picks: Sequence[m.Pick], stage: str) -> list[tuple[m.Pick, m.Pick]]:
    flat = {p.config.removesuffix(f" · {FLAT}"): p for p in picks if p.stage == stage}
    out: list[tuple[m.Pick, m.Pick]] = []
    for p in picks:
        if p.stage == stage and p.config.endswith(f" · {WIDTH}"):
            out.append((flat[p.config.removesuffix(f" · {WIDTH}")], p))
    return out


def render(res: Result) -> str:
    pairs = _pairs(res.picks, STAGE_RANDOM)
    hand = _pairs(res.picks, STAGE_HAND)[0]
    liq_pair = _pairs(res.picks, STAGE_LIQ)[0]
    ok, lines = verdict(pairs, hand)

    def med(xs: Sequence[float]) -> float:
        return float(np.median(xs))

    out = [
        "# WAN-445 — 스토캐스틱 팔 크기 = 손절폭 비례(명목 고정)",
        "",
        "> 자동 생성(`uv run python -m backtest.wan445_width_sizing --part run`). 결정문 "
        "`docs/decisions/wan445.md`. 🚨 채택 근거 아님 · 기본값·토대·페이퍼 규칙 불변.",
        "> 판별: **오늘 엔진 · 핀 없음**(WAN-443 이후 정렬) · 장비는 WAN-442/444 그대로"
        "(같은 무작위 20회 · 새 진입만 막기).",
        "",
        f"## 판정: **{'통과' if ok else '불통과'}**",
        "",
        *[f"* {line}" for line in lines],
        "",
        "## 판별 한눈에 (균일 → 손절폭 비례)",
        "",
        "| 판 | 앞 크기 | 앞 연환산 | 뒤 수익/MDD | 뒤 수익 · MDD | 8.6년 크기 · 연 | "
        "3% 고정 연 · MDD |",
        "|---|---|---|---|---|---|---|",
    ]

    def arrow(a: str, b: str) -> str:
        return f"{a} → {b}"

    for name, (a, b) in (
        (m.HAND31, hand),
        (liq.TOP + "(참고)", liq_pair),
        *((a.config.removesuffix(f" · {FLAT}"), (a, b)) for a, b in pairs),
    ):
        out.append(
            f"| {name} | {arrow(f'{a.front_risk:.2%}', f'{b.front_risk:.2%}')} | "
            f"{arrow(_pct(a.front_cagr), _pct(b.front_cagr))} | "
            f"{arrow(f'{a.back_ratio:+.2f}', f'{b.back_ratio:+.2f}')} | "
            f"{a.back_return:+.0%} · {a.back_mdd:.1%} → {b.back_return:+.0%} · {b.back_mdd:.1%} | "
            f"{a.full_risk:.2%} · {_pct(a.full_cagr)} → {b.full_risk:.2%} · {_pct(b.full_cagr)} | "
            f"{_pct(a.fixed_cagr)} · {a.fixed_mdd:.1%} → {_pct(b.fixed_cagr)} · {b.fixed_mdd:.1%} |"
        )
    fa = [a.front_cagr for a, _ in pairs]
    fb = [b.front_cagr for _, b in pairs]
    ba = [a.back_ratio for a, _ in pairs]
    bb = [b.back_ratio for _, b in pairs]
    ua = [a.full_cagr for a, _ in pairs]
    ub = [b.full_cagr for _, b in pairs]
    out += [
        f"| **무작위 20회 중앙** | — | {_pct(med(fa))} → {_pct(med(fb))} | "
        f"{med(ba):+.2f} → {med(bb):+.2f} | — | {_pct(med(ua))} → {_pct(med(ub))} | — |",
        "",
        "## 8.6년 표 (8.6년 맞춤 크기 · 칸: 총수익 · 연 / 평가손 MDD)",
        "",
        "| 판 | 체결 | 크기 | " + " | ".join(c.SEGMENTS[:4]) + " |",
        "|" + "---|" * 7,
    ]
    for p in (*hand, *liq_pair):
        for fill in c.FILLS:
            seg = {
                r.segment: r
                for r in res.rows
                if (r.arm, r.size_basis, r.fill) == (f"{p.mode} · {p.config}", m.SIZE_FULL, fill)
            }
            if not seg:
                continue
            risk = next(iter(seg.values())).risk
            cells = " | ".join(c._cell(seg[s]) for s in c.SEGMENTS[:4])
            out.append(f"| {p.config} | {w._FILL_LABEL[fill]} | {risk:.2%} | {cells} |")
    out += ["", "## 실행 기록", "", *[f"* {n}" for n in res.notes], ""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--part", choices=("run",), required=True)
    ap.add_argument("--markets", type=Path, default=m.MARKETS_PATH)
    args = ap.parse_args(argv)
    with args.markets.open("rb") as fh:
        built = pickle.load(fh)  # noqa: S301 — wan442 `--part markets`가 만든 로컬 캐시
    res = run(built)
    m.picks_frame(res.picks).to_csv(PICKS_PATH, index=False)
    c.rows_frame(res.rows).to_csv(CSV_PATH, index=False)
    SUMMARY_PATH.write_text(render(res), "utf-8")
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
