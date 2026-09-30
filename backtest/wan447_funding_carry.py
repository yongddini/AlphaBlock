"""WAN-447 — 스토캐스틱 팔 × 펀딩 캐리 합성.

묻는 것: 놀고 있는 자본에 캐리를 얹으면 MDD 35%에서 연 25%가 되는가.

팔(B + 15m · 몰림 규칙 · 채택 북 배치)은 연 ~180거래 · 봉 4개 보유라 자본 대부분이
놀고 있다. 그 자본의 f 비율을 **펀딩 캐리**(현물 롱 + 같은 양 무기한 숏 · BTC·ETH 반반 ·
규칙 없음 = 음수 펀딩 때도 보유)에 둔다고 보고 합성 곡선 = 팔 1분 평가 경로 × 캐리
지수로 잰다. 크기는 합성 곡선 MDD 35%에 다시 맞춘다.

* 합성이 곱으로 떨어지는 이유: 팔은 진입 순간 **계좌 전체** 자본에 비례해 크기를 받고
  (`w.mtm_path`) 캐리도 계좌 전체에 비례해 불어나므로 두 수익이 같은 분모를 키운다.
* 펀딩은 거래소 공개 API(`fetch_funding_rate_history`)에서 받아 `backtest/cache/`에만
  둔다(DB 불변 · WAN-194). 2019-09 이전(선물 없음)은 캐리 0.
* 캐리 비용 = 현물·선물 테이커 진입+청산 한 번(`CARRY_COST`)을 계좌 시작에 한 번 뺀다.
* 🚨 **빠진 위험**: 베이시스 순간 변동 · 숏 다리 증거금 청산 · 거래소 위험 · 현물을
  선물 증거금으로 쓰는 통합 증거금 계좌 전제. 펀딩 곡선만의 낙폭이다.

재현: `uv run python -m backtest.wan447_funding_carry --part run`(시장은 WAN-442 피클).
**측정 전용 · 기본값·토대·페이퍼 불변 · 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**
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

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
CSV_PATH = REPORT_DIR / "wan447_funding_carry.csv"
SUMMARY_PATH = REPORT_DIR / "wan447_funding_carry_summary.md"
FUNDING_CACHE = REPO_ROOT / "backtest" / "cache" / "wan447_funding.pkl"

CARRY_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT")
FUNDING_SINCE_MS = 1_567_296_000_000  # 2019-09-01 — 바이낸스 USDT 무기한 개시 전
CARRY_COST = 0.0022
"""캐리 왕복 비용(현물·선물 테이커 진입+청산) — 계좌 시작에 한 번."""
FRACTIONS: tuple[float, ...] = (0.0, 0.5, 1.0)
VERDICT_FRACTION = 0.5
TARGET_CAGR = 0.25
TRAILING_DAYS = 365
MINUTE_MS = 60_000
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
CHECK_TOL = 1e-12

SERIES_ACTUAL = "실제 펀딩"
SERIES_TRAILING = "최근 12개월 고정"
STAGE_LIQ = "거래대금 상위 31"
STAGE_HAND = "손으로 고른 31"
STAGE_RANDOM = "무작위"
HAND_CONFIG = "손으로 고른 31종목(고정)"
"""WAN-442 공개 CSV의 config 라벨(검산 대조 키)."""


def random_config(k: int) -> str:
    return f"무작위 교체 #{k:02d}"


@dataclass(frozen=True)
class Funding:
    """정산 시각(ms, 오름차순) · 그 시각 바스켓 펀딩률(숏이 받는 쪽이 +)."""

    times: np.ndarray
    rates: np.ndarray

    def __post_init__(self) -> None:
        if len(self.times) != len(self.rates) or len(self.times) == 0:
            raise ValueError("펀딩 시각·값 길이가 다르거나 비었다")
        if np.any(np.diff(self.times) <= 0):
            raise ValueError("펀딩 시각이 오름차순이 아니다")


def basket(rows: dict[str, Sequence[tuple[int, float]]]) -> Funding:
    """종목별 (시각, 펀딩률)을 정시 버킷으로 맞춰 그 시각에 있는 종목만 평균한다.

    ETH는 BTC보다 늦게 시작한다.
    """
    buckets: dict[int, list[float]] = {}
    for series in rows.values():
        for t, r in series:
            buckets.setdefault(int(t) // HOUR_MS * HOUR_MS, []).append(float(r))
    ts = np.array(sorted(buckets), dtype=np.int64)
    return Funding(ts, np.array([float(np.mean(buckets[int(t)])) for t in ts]))


def trailing_mean(f: Funding, end_ms: int, days: int = TRAILING_DAYS) -> float:
    """`end_ms` 이전 `days`일의 정산당 평균 펀딩률."""
    sel = (f.times > end_ms - days * DAY_MS) & (f.times <= end_ms)
    if not sel.any():
        raise ValueError("최근 창에 펀딩이 없다")
    return float(f.rates[sel].mean())


def flat(f: Funding, rate: float) -> Funding:
    """같은 정산 시각에 펀딩률만 `rate`로 고정한 시나리오."""
    return Funding(f.times.copy(), np.full(len(f.times), rate))


def carry_index(f: Funding, times: np.ndarray, fraction: float) -> np.ndarray:
    """자본의 `fraction`이 캐리일 때 시각별 누적 배율(비용 없음) — 그 시각까지 **정산된** 펀딩만."""
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"캐리 비율은 0~1: {fraction}")
    lg = np.concatenate([[0.0], np.cumsum(np.log1p(fraction * f.rates))])
    return np.asarray(np.exp(lg[np.searchsorted(f.times, times, side="right")]))


def _grid(path: w.MtmPath) -> np.ndarray:
    return path.start + np.arange(len(path.mtm_low), dtype=np.int64) * MINUTE_MS


def chain_carry(
    spot: Sequence[w.PlacedTrade],
    fut: Sequence[w.PlacedTrade],
    risk: float,
    f: Funding,
    fraction: float,
) -> tuple[float, float]:
    """`c.chain`에 캐리 지수를 곱한 (총수익, 평가손 MDD) — fraction=0이면 `c.chain`과 같다."""
    a = w.mtm_path(spot, risk=risk)
    b = w.mtm_path(fut, risk=risk)
    cost = 1.0 - fraction * CARRY_COST
    scale = float(a.mtm_close[-1])
    ca, cb = carry_index(f, _grid(a), fraction), carry_index(f, _grid(b), fraction)
    low = np.concatenate([a.mtm_low * ca, b.mtm_low * scale * cb]) * cost
    total = float(b.mtm_close[-1]) * scale * float(cb[-1]) * cost - 1.0
    return total, c._mdd(low)


def single_carry(
    trades: Sequence[w.PlacedTrade], risk: float, f: Funding, fraction: float
) -> tuple[float, float]:
    """한 구간만 새 계좌로(뒷구간) — 캐리 지수는 그 구간 시작에서 1로 다시 잡는다."""
    p = w.mtm_path(trades, risk=risk)
    ci = carry_index(f, _grid(p), fraction)
    ci = ci / carry_index(f, np.array([p.start]), fraction)[0]
    cost = 1.0 - fraction * CARRY_COST
    return float(p.mtm_close[-1]) * float(ci[-1]) * cost - 1.0, c._mdd(p.mtm_low * ci * cost)


@dataclass(frozen=True)
class Result:
    stage: str
    config: str
    series: str
    fraction: float
    trades: int
    front_risk: float
    front_cagr: float
    back_cagr: float
    back_mdd: float
    full_risk: float
    full_cagr: float
    full_stop_risk: float
    full_stop_cagr: float


def evaluate(
    stage: str,
    config: str,
    placed: dict[str, tuple[list[w.PlacedTrade], list[w.PlacedTrade]]],
    boundary_ms: int,
    series: str,
    f: Funding,
    fraction: float,
) -> Result:
    """`m.evaluate`와 같은 앞/뒤/전체 정의 · 크기는 합성 곡선 MDD 35%에 맞춘다."""
    s, fz = placed["bar_low"]
    s_stop, f_stop = placed["stop"]
    front = [t for t in fz if not t.back]
    back = [t for t in fz if t.back]
    w0, f1 = u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END)

    def fit(sp: Sequence[w.PlacedTrade], fu: Sequence[w.PlacedTrade]) -> float:
        return c.fit_risk(lambda r: chain_carry(sp, fu, r, f, fraction)[1], target=c.MDD_TARGET)

    r_front = fit(s, front)
    front_total, _ = chain_carry(s, front, r_front, f, fraction)
    back_total, back_mdd = single_carry(back, r_front, f, fraction)
    r_full = fit(s, fz)
    full_total, _ = chain_carry(s, fz, r_full, f, fraction)
    r_stop = fit(s_stop, f_stop)
    stop_total, _ = chain_carry(s_stop, f_stop, r_stop, f, fraction)
    return Result(
        stage=stage,
        config=config,
        series=series,
        fraction=fraction,
        trades=len(s) + len(fz),
        front_risk=r_front,
        front_cagr=c.cagr(front_total, c._years(w0, boundary_ms)),
        back_cagr=c.cagr(back_total, c._years(boundary_ms, f1)),
        back_mdd=back_mdd,
        full_risk=r_full,
        full_cagr=c.cagr(full_total, c._years(w0, f1)),
        full_stop_risk=r_stop,
        full_stop_cagr=c.cagr(stop_total, c._years(w0, f1)),
    )


def verdict(rows: Sequence[Result]) -> tuple[str, list[tuple[str, bool, str]]]:
    """미리 정한 셋(거래대금 상위 31 · 최악 체결 · f=0.5)."""

    def pick(series: str, fraction: float) -> Result:
        hit = [
            r
            for r in rows
            if r.stage == STAGE_LIQ and r.series == series and r.fraction == fraction
        ]
        if len(hit) != 1:
            raise ValueError(f"판정 행이 하나가 아니다: {series} f={fraction} → {len(hit)}")
        return hit[0]

    act, trail = pick(SERIES_ACTUAL, VERDICT_FRACTION), pick(SERIES_TRAILING, VERDICT_FRACTION)
    base = pick(SERIES_ACTUAL, 0.0)
    checks = [
        (
            "① 8.6년 실제 펀딩 ≥ 25%",
            act.full_cagr >= TARGET_CAGR,
            f"{act.full_cagr:+.1%}(캐리 0% {base.full_cagr:+.1%})",
        ),
        (
            "② 최근 12개월 펀딩 고정 ≥ 25%",
            trail.full_cagr >= TARGET_CAGR,
            f"{trail.full_cagr:+.1%}",
        ),
        (
            "③ 뒷구간 연환산 > 캐리 0%",
            act.back_cagr > base.back_cagr,
            f"{base.back_cagr:+.1%} → {act.back_cagr:+.1%}",
        ),
    ]
    ok = [x[1] for x in checks]
    if all(ok):
        label = "캐리 합성으로 25% 달성"
    elif ok[0] and not ok[1]:
        label = "과거 펀딩에만 기대 달성 — 현재 펀딩 수준으론 미달"
    else:
        label = "미달"
    return label, checks


def fetch_funding(cache: Path = FUNDING_CACHE, *, refresh: bool = False) -> tuple[Funding, str]:
    """공개 API에서 BTC·ETH 펀딩을 받아 캐시(DB 불변). 캐시가 있으면 그대로 쓴다(재현성)."""
    if cache.exists() and not refresh:
        with cache.open("rb") as fh:
            rows, fetched = pickle.load(fh)
    else:
        import ccxt

        ex = ccxt.binanceusdm()
        rows = {}
        for sym in CARRY_SYMBOLS:
            out: list[tuple[int, float]] = []
            since = FUNDING_SINCE_MS
            while True:
                got = ex.fetch_funding_rate_history(sym, since=since, limit=1000)
                if not got:
                    break
                out += [(int(x["timestamp"]), float(x["fundingRate"])) for x in got]
                nxt = int(got[-1]["timestamp"]) + 1
                if nxt <= since or len(got) < 1000:
                    break
                since = nxt
            rows[sym] = out
        fetched = pd.Timestamp.now(tz="UTC").isoformat()
        cache.parent.mkdir(parents=True, exist_ok=True)
        with cache.open("wb") as fh:
            pickle.dump((rows, fetched), fh)
    return basket(rows), str(fetched)


def checksum(rows: Sequence[Result], ref: Path = m.PICKS_PATH) -> str:
    """캐리 0% · 실제 펀딩 판(무작위 20회 + 31종목) ≡ WAN-442 공개 CSV — 같은 장비라는 증명."""
    frame = pd.read_csv(ref)
    frame = frame[frame["mode"] == m.MODE_DROP]
    by_cfg = {str(r["config"]): r for r in frame.to_dict("records")}
    zero = [
        r for r in rows if r.fraction == 0.0 and r.series == SERIES_ACTUAL and r.stage != STAGE_LIQ
    ]
    worst = 0.0
    for r in zero:
        rec = by_cfg[r.config]
        for key in ("front_risk", "front_cagr", "back_cagr", "full_risk", "full_cagr"):
            worst = max(worst, abs(float(rec[key]) - float(getattr(r, key))))
        if int(rec["trades"]) != r.trades:
            raise AssertionError(f"{r.config} 거래 수가 WAN-442와 다르다")
    if worst > CHECK_TOL:
        raise AssertionError(f"캐리 0% 판이 WAN-442와 다르다(최대 차 {worst:.2e})")
    return (
        f"검산 — 캐리 0% 판 {len(zero)}개(무작위 20회 + 31종목) ≡ WAN-442 공개 CSV"
        f"(최대 차 {worst:.2e} · 거래 수 정수 일치)"
    )


def yearly_carry(f: Funding) -> dict[int, float]:
    years = pd.to_datetime(f.times, unit="ms").year
    return {int(y): float(f.rates[years == y].sum()) for y in sorted(set(years))}


def run(built: u.Markets) -> tuple[list[Result], list[str]]:
    t0 = time.monotonic()
    fund, fetched = fetch_funding()
    end_ms = int(fund.times[-1])
    trail_rate = trailing_mean(fund, end_ms)
    trail = flat(fund, trail_rate)
    st = m.setting(built)
    per_year = 3 * 365.25
    notes = [
        f"펀딩 — BTC·ETH 반반 · 정산 {len(fund.times)}회 · {m438_utc(int(fund.times[0]))} ~ "
        f"{m438_utc(end_ms)} · 받은 시각 {fetched}",
        f"최근 12개월 정산당 평균 {trail_rate:.6%} → 연환산 {trail_rate * per_year:+.2%}"
        f"(8시간 정산 기준 · 전체 기간 평균 {fund.rates.mean() * per_year:+.2%})",
        "연도별 합: " + " · ".join(f"{y} {v:+.1%}" for y, v in yearly_carry(fund).items()),
    ]
    top = st.rotations(lambda d, t: liq.select_liquidity(d, t, top=True))
    lists: list[tuple[str, str, tuple[m.Rotation, m.Rotation] | None]] = [
        (STAGE_LIQ, STAGE_LIQ, top),
        (STAGE_HAND, HAND_CONFIG, None),
    ]
    lists += [(STAGE_RANDOM, random_config(k), rot) for k, rot in enumerate(st.random_rotations())]
    rows: list[Result] = []
    for stage, config, rot in lists:
        if rot is None:
            s_mk, f_mk = st.hand_spot, st.hand_fut
        else:
            s_mk, _ = m.apply_rotation(st.spot_pool, rot[0], force_close=False)
            f_mk, _ = m.apply_rotation(st.fut_pool, rot[1], force_close=False)
        placed = {
            fill: (
                c.place_arm(s_mk, c.ARM_B, fill, in_book=True),
                c.place_arm(f_mk, c.ARM_B, fill, in_book=True),
            )
            for fill in c.FILLS
        }
        series = [(SERIES_ACTUAL, fund)]
        if stage != STAGE_RANDOM:
            series.append((SERIES_TRAILING, trail))
        for name, fs in series:
            for fr in FRACTIONS:
                if name == SERIES_TRAILING and fr == 0.0:
                    continue
                rows.append(evaluate(stage, config, placed, st.boundary_ms, name, fs, fr))
        print(f"{config} 완료 {time.monotonic() - t0:.0f}s", flush=True)
    # 시나리오 0% 행은 실제 0%와 같다(캐리 없음) — 판정 조회를 위해 복제해 둔다.
    rows += [
        Result(**{**r.__dict__, "series": SERIES_TRAILING})
        for r in rows
        if r.series == SERIES_ACTUAL and r.fraction == 0.0 and r.stage != STAGE_RANDOM
    ]
    notes.append(checksum(rows))
    notes.append(f"측정 {time.monotonic() - t0:.0f}초")
    return rows, notes


def m438_utc(ms: int) -> str:
    return str(pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%d"))


def _pct(x: float) -> str:
    return "—" if math.isnan(x) else f"{x:+.1%}"


def render(rows: Sequence[Result], notes: Sequence[str]) -> str:
    label, checks = verdict(rows)
    out = [
        "# WAN-447 — 스토캐스틱 팔 × 펀딩 캐리 합성",
        "",
        "> 합성 = 팔 1분 평가 경로 × 캐리 지수(자본의 f · BTC·ETH 반반 현물 롱 + "
        "무기한 숏). 크기는 합성 곡선 MDD 35% 맞춤. 앞/뒤 = WAN-442 정의. "
        "🚨 펀딩 곡선만의 낙폭이다(베이시스·숏 증거금·거래소 위험 제외).",
        "",
        f"## 판정 — **{label}**",
        "",
        "| 조건(거래대금 상위 31 · 최악 체결 · 캐리 50%) | 값 | 결과 |",
        "|---|---|---|",
    ]
    out += [f"| {n} | {v} | {'✅' if ok else '❌'} |" for n, ok, v in checks]
    out += ["", "## 표 — 8.6년 연환산(MDD 35% 맞춤)", ""]
    out += [
        "| 목록 | 펀딩 | 캐리 | 앞구간 연 | 뒷구간 연 / MDD(앞 크기) "
        "| 8.6년 최악 체결 (크기) | 8.6년 손절가 체결 |",
        "|---|---|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        if r.stage == STAGE_RANDOM:
            continue
        out.append(
            f"| {r.stage} | {r.series} | {r.fraction:.0%} | {_pct(r.front_cagr)} | "
            f"{_pct(r.back_cagr)} / {r.back_mdd:.1%} | {_pct(r.full_cagr)} ({r.full_risk:.2%}) | "
            f"{_pct(r.full_stop_cagr)} |"
        )
    out += ["", "### 무작위 교체 20회(실제 펀딩) — 중앙값 · 25% 이상 판 수", ""]
    out += ["| 캐리 | 앞구간 연 | 뒷구간 연 | 8.6년 최악 | 8.6년 손절가 |", "|--:|--:|--:|--:|--:|"]
    for fr in FRACTIONS:
        rs = [r for r in rows if r.stage == STAGE_RANDOM and r.fraction == fr]

        def med(key: str, rs: list[Result] = rs) -> str:
            vals = [getattr(r, key) for r in rs]
            return (
                f"{float(np.median(vals)):+.1%} ({sum(v >= TARGET_CAGR for v in vals)}/{len(vals)})"
            )

        out.append(
            f"| {fr:.0%} | {med('front_cagr')} | {med('back_cagr')} | {med('full_cagr')} | "
            f"{med('full_stop_cagr')} |"
        )
    out += ["", "## 기록", ""] + [f"* {n}" for n in notes]
    out += [
        "",
        "**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변(펀딩은 공개 API → `backtest/cache/`) · "
        "실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**",
        "",
    ]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--part", choices=("run", "summary"), default="run")
    args = ap.parse_args(argv)
    if args.part == "run":
        with m.MARKETS_PATH.open("rb") as fh:
            built = pickle.load(fh)
        rows, notes = run(built)
        pd.DataFrame([r.__dict__ for r in rows]).to_csv(CSV_PATH, index=False)
        (REPORT_DIR / "wan447_notes.txt").write_text("\n".join(notes) + "\n")
    frame = pd.read_csv(CSV_PATH)
    rows = [Result(**rec) for rec in frame.to_dict("records")]
    notes = (REPORT_DIR / "wan447_notes.txt").read_text().splitlines()
    SUMMARY_PATH.write_text(render(rows, notes))
    print(SUMMARY_PATH.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
