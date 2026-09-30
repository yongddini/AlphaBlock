"""WAN-444: 스토캐스틱 팔 종목 = 매주 「직전 30일 거래대금 상위 31종목」 — 인과 규칙으로 재현되는가.

## 왜 이 모듈이 있나

WAN-442에서 모멘텀 교체는 뒷구간에서 무작위 교체와 구분되지 않았다. 그런데 같은 표에서
**손으로 고른 31종목 고정**은 뒷구간 수익/MDD +6.78로 무작위 20회 중 19개보다 높았다 —
그 목록이 사후에 고른 것이라는 점 하나가 문제다. 가설: 그 31종목의 공통점은 「거래가 많은
메이저 코인」이다. 그렇다면 **미래를 안 보는 규칙**(직전 거래대금 상위)으로 같은 효과가
재현되어야 한다.

## 무엇을 고정했나 (착수 전 · 코드 상수 — 결과를 보고 옮기지 않는다 · 한 규칙 · 한 번)

* **장비 = WAN-442 그대로** — 시장(`wan440.build_markets` 피클) · 리밸런싱 격자 · 자격 규칙 ·
  평가(`wan442.evaluate`) · 무작위 교체 20회(시드 442 + 판 번호 — **WAN-442와 같은 판**이고
  검산이 그 등식을 건다).
* **규칙**: 시각 t에 **t 이전에 닫힌 일봉으로 직전 `LIQ_DAYS`(30)일 평균 거래대금**
  (거래량 × 종가)이 큰 31종목. 30일은 스윕하지 않는다.
* **판정(둘 다)**: (1) 뒷구간 수익/MDD(미끄러짐 · 앞구간 맞춤 크기)가 무작위 20회 중 그
  이상인 판 ≤ 1(`wan442.verdict`와 같은 자) (2) 앞구간 연환산이 무작위 20회 **중앙값 이상**
  (이 규칙은 고르는 데 앞구간을 안 썼으므로 앞구간도 확인 창이다).
* **대조**: 거래대금 하위 31종목 · 손으로 고른 31종목 고정(참고) · 강제 청산 판(참고).
* **부수 관측**: 거래대금 목록과 손으로 고른 31종목의 주별 겹침 수 평균.

**측정 전용 · 기본값·토대·페이퍼 규칙 불변** · 핀 없음(WAN-305) · WAN-443 이후 정렬 · 실거래
보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import math
import pickle
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest.wan424_stoch_ob_arm import SYMBOLS as SYMBOLS_31

__all__ = ["SYMBOLS_31"]

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
CSV_PATH = REPORT_DIR / "wan444_liquidity_universe.csv"
PICKS_PATH = REPORT_DIR / "wan444_liquidity_picks.csv"
SUMMARY_PATH = REPORT_DIR / "wan444_liquidity_universe_summary.md"

# --- 착수 전 고정 ---------------------------------------------------------------------------
LIQ_DAYS = 30
"""거래대금 평균을 내는 창(일) — 한 값 · 스윕하지 않는다."""
TOP = "거래대금 상위 31종목"
BOTTOM = "거래대금 하위 31종목(대조)"
STAGE_RULE = "규칙"
STAGE_CONTROL = "대조"
STAGE_RANDOM = "무작위"
CHECK_TOL = 1e-12
"""무작위 20회가 WAN-442와 같은 판인지 — 공개 CSV(텍스트 왕복)와 대조하는 허용 오차."""


# ---------------------------------------------------------------------------
# 규칙 — 순수 함수 (t 이전에 닫힌 일봉만)
# ---------------------------------------------------------------------------


def liquidity(d: m.Daily, j: int, days: int = LIQ_DAYS) -> float | None:
    """확정 일봉 j까지 직전 `days`일(j 포함) 평균 거래대금. 없거나 0이면 None."""
    lo = int(np.searchsorted(d.open_time, int(d.open_time[j]) - days * m.DAY_MS, side="right"))
    window = d.quote[lo : j + 1]
    if len(window) == 0:
        return None
    mean = float(window.mean())
    return mean if mean > 0.0 else None


def select_liquidity(
    daily: Mapping[str, m.Daily], t: int, *, top: bool = True, n: int = m.TOP_N
) -> frozenset[str]:
    """시각 t의 목록 — 편입 자격(WAN-442와 같다)이 있는 종목 중 거래대금 순 n개(동점은 이름순)."""
    scores: dict[str, float] = {}
    for sym, d in daily.items():
        j = m.eligible(d, t)
        if j is None:
            continue
        v = liquidity(d, j)
        if v is not None:
            scores[sym] = v
    order = sorted(scores, key=lambda s: (-scores[s] if top else scores[s], s))
    return frozenset(order[:n])


def verdict(rule: m.Pick, randoms: Sequence[m.Pick]) -> tuple[bool, list[str]]:
    """(통과 여부, 근거 줄) — (1) 뒷구간 상위 2 안 (2) 앞구간 무작위 중앙 이상. 둘 다."""
    ok1, beaten = m.verdict(rule, randoms)
    med = float(np.median([r.front_cagr for r in randoms]))
    ok2 = rule.front_cagr >= med
    lines = [
        f"(1) 뒷구간 수익/MDD(앞구간 맞춤 크기 {rule.front_risk:.2%}) {rule.back_ratio:+.3f} — "
        f"무작위 {len(randoms)}회 중 그 이상 {beaten}개(기준 ≤ {m.PASS_MAX_BEATEN}) → "
        + ("✅" if ok1 else "❌"),
        f"(2) 앞구간 연환산 {rule.front_cagr:+.1%} vs 무작위 중앙 {med:+.1%} → "
        + ("✅" if ok2 else "❌"),
    ]
    return ok1 and ok2, lines


def overlap_with_31(rot: m.Rotation) -> float:
    """주별 목록과 손으로 고른 31종목의 겹침 수 평균."""
    hand = set(SYMBOLS_31)
    return float(np.mean([len(set(mem) & hand) for mem in rot.members])) if rot.members else 0.0


def membership_share(rot: m.Rotation) -> list[tuple[str, float]]:
    """종목별로 목록에 든 주의 비율(내림차순)."""
    cnt: Counter[str] = Counter()
    for mem in rot.members:
        cnt.update(mem)
    n = max(len(rot.members), 1)
    return sorted(((s, k / n) for s, k in cnt.items()), key=lambda x: (-x[1], x[0]))


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


@dataclass
class Result:
    picks: list[m.Pick]
    rows: list[c.Row]
    notes: list[str]
    share: list[tuple[str, float]]
    overlap_fut: float
    overlap_spot: float


def checksum_randoms(randoms: Sequence[m.Pick], ref: Path = m.PICKS_PATH) -> str:
    """무작위 20회 = WAN-442 공개 CSV의 같은 판(새 진입만 막기) — 같은 대조군이라는 자격 증명."""
    frame = pd.read_csv(ref)
    frame = frame[(frame["mode"] == m.MODE_DROP) & (frame["stage"] == STAGE_RANDOM)]
    by_cfg = {str(r["config"]): r for r in frame.to_dict("records")}
    worst = 0.0
    for p in randoms:
        rec = by_cfg[p.config]
        for key in ("front_cagr", "back_ratio", "full_cagr", "front_risk"):
            worst = max(worst, abs(float(rec[key]) - float(getattr(p, key))))
        if int(rec["trades"]) != p.trades:
            raise AssertionError(f"무작위 {p.config} 거래 수가 WAN-442와 다르다")
    if worst > CHECK_TOL:
        raise AssertionError(f"무작위 20회가 WAN-442와 다르다(최대 차 {worst:.2e})")
    return f"검산 — 무작위 교체 20회 ≡ WAN-442 공개 CSV(최대 차 {worst:.2e} · 거래 수 정수 일치)"


def run(built: u.Markets) -> Result:
    c.assert_adopted_take_profit_liquidity()
    t0 = time.monotonic()
    st = m.setting(built)
    notes = [*built.notes, st.note, m.checksum_hand31(st.hand_spot, st.hand_fut)]
    top = st.rotations(lambda d, t: select_liquidity(d, t, top=True))
    bottom = st.rotations(lambda d, t: select_liquidity(d, t, top=False))
    picks: list[m.Pick] = []
    rows: list[c.Row] = []
    for mode in m.MODES:
        p, rr = st.evaluate_rotation(mode, TOP, STAGE_RULE, top)
        picks.append(p)
        rows += rr
    p, rr = st.evaluate_rotation(m.MODE_DROP, BOTTOM, STAGE_CONTROL, bottom)
    picks.append(p)
    rows += rr
    p, rr = m.evaluate(
        m.MODE_DROP, m.HAND31, STAGE_CONTROL, st.hand_spot, st.hand_fut, st.boundary_ms
    )
    picks.append(p)
    rows += rr
    randoms: list[m.Pick] = []
    for k, rot in enumerate(st.random_rotations()):
        p, rr = st.evaluate_rotation(m.MODE_DROP, f"무작위 교체 #{k:02d}", STAGE_RANDOM, rot)
        randoms.append(p)
        rows += rr
    picks += randoms
    notes.append(checksum_randoms(randoms))
    ov_f, ov_s = overlap_with_31(top[1]), overlap_with_31(top[0])
    notes.append(
        f"목록 주당 평균 교체 — 선물 {m._turnover(top[1]):.1f}종목 · 현물 {m._turnover(top[0]):.1f}"
    )
    notes.append(f"총 {time.monotonic() - t0:.0f}초")
    return Result(picks, rows, notes, membership_share(top[1]), ov_f, ov_s)


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def _pct(x: float) -> str:
    return "—" if math.isnan(x) else f"{x:+.1%}"


def render(res: Result) -> str:
    picks = res.picks
    rule = next(p for p in picks if p.config == TOP and p.mode == m.MODE_DROP)
    rnd = [p for p in picks if p.stage == STAGE_RANDOM]
    ok, lines = verdict(rule, rnd)
    rs = sorted(r.back_ratio for r in rnd)
    fs = sorted(r.front_cagr for r in rnd)
    out = [
        "# WAN-444 — 스토캐스틱 팔 종목 = 매주 직전 30일 거래대금 상위 31종목",
        "",
        "> 자동 생성(`uv run python -m backtest.wan444_liquidity_universe --part run`). "
        "결정문 `docs/decisions/wan444.md`. 🚨 채택 근거 아님 · 기본값·토대·페이퍼 규칙 불변.",
        "> 판별: **오늘 엔진 · 핀 없음**(WAN-443 이후 정렬) · 장비는 WAN-442 그대로"
        "(같은 무작위 20회).",
        "",
        f"## 판정 (새 진입만 막기): **{'통과' if ok else '불통과'}**",
        "",
        *[f"* {line}" for line in lines],
        f"* 무작위 20회 — 뒷구간 수익/MDD 최소 {rs[0]:+.3f} · 중앙 {float(np.median(rs)):+.3f} · "
        f"최대 {rs[-1]:+.3f} / 앞구간 연환산 최소 {fs[0]:+.1%} · "
        f"중앙 {float(np.median(fs)):+.1%} · 최대 {fs[-1]:+.1%}",
        "",
        "## 규칙 · 대조 한눈에",
        "",
        "| 판 | 처리 | 앞 크기 | 앞 연환산 | 뒤 수익/MDD | 뒤 수익 · MDD | "
        "8.6년 연(미끄러짐 / 손절가) | 3% 고정 연 · MDD | 강제 청산 거래 |",
        "|---|---|--:|--:|--:|---|---|---|--:|",
    ]
    for p in [q for q in picks if q.stage != STAGE_RANDOM]:
        out.append(
            f"| {p.config} | {p.mode} | {p.front_risk:.2%} | {_pct(p.front_cagr)} | "
            f"{p.back_ratio:+.3f} | {p.back_return:+.1%} · {p.back_mdd:.1%} | "
            f"{p.full_risk:.2%} · {_pct(p.full_cagr)} / {_pct(p.full_stop_cagr)} | "
            f"{_pct(p.fixed_cagr)} · {p.fixed_mdd:.1%} | {p.forced} |"
        )
    full = sorted(r.full_cagr for r in rnd)
    out += [
        f"| 무작위 교체 20회 | {m.MODE_DROP} | — | 중앙 {_pct(float(np.median(fs)))} | "
        f"중앙 {float(np.median(rs)):+.3f} | — | 중앙 {_pct(float(np.median(full)))} "
        f"({_pct(full[0])} ~ {_pct(full[-1])}) | — | — |",
        "",
        "## 8.6년 표 (8.6년 맞춤 크기 · 칸: 총수익 · 연 / 평가손 MDD)",
        "",
        "| 판 | 처리 | 체결 | 크기 | " + " | ".join(c.SEGMENTS[:4]) + " |",
        "|" + "---|" * 8,
    ]
    for p in [q for q in picks if q.stage != STAGE_RANDOM]:
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
            out.append(f"| {p.config} | {p.mode} | {w._FILL_LABEL[fill]} | {risk:.2%} | {cells} |")
    out += [
        "",
        "## 목록의 모습(선물 구간)",
        "",
        f"* 손으로 고른 31종목과의 주별 겹침 평균: 선물 **{res.overlap_fut:.1f}종목** · 현물 "
        f"{res.overlap_spot:.1f}종목",
        "* 목록에 든 주의 비율 상위: "
        + " · ".join(f"{s.split('/')[0]} {v:.0%}" for s, v in res.share[:15]),
        "* 가장 드물게 든 종목: "
        + " · ".join(f"{s.split('/')[0]} {v:.0%}" for s, v in res.share[-10:]),
        "",
        "## 실행 기록",
        "",
        *[f"* {n}" for n in res.notes],
        "",
    ]
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
