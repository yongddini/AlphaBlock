"""WAN-448 — 스토캐스틱 팔 손절 체결가 틱 실측.

묻는 것: 폭락 때 몰려 나는 이 팔의 손절이 실제로 얼마에 체결됐나 — 「손절가 그대로」(연 ~28%)와
「그 1분 저가」(연 ~20%) 사이 어디인가(WAN-446 §3 · WAN-447 §2).

* 모집단: 거래대금 상위 31 · 팔 B(폭락 층 · 채택 북) · 선물 구간 · 손절로 끝난 거래.
* 층: 폭락(진입 직전 BTC 24h ≤ −5% = 팔 B의 폭락 판정과 같은 값) / 평시.
  층별 무작위 표본(시드 고정).
* 체결: 그 1분 체결내역에서 손절가에 처음 닿은 체결부터 우리 수량만큼 먹은 단가(`wan397.walk_tape`).
  α = (손절가 − 체결가) / (손절가 − 그 1분 저가), 0 = 손절가 체결 · 1 = 최악.
* 적용: 거래의 net R은 청산가에 대해 **정확히 선형**(손익·청산 수수료 모두 청산가에 비례)이고
  두 극단(`stop`·`bar_low`)은 **같은 거래 집합**을 낸다(청산 시각이 같다 · 검산으로 확인) —
  그래서 층별 평균 α로 두 극단의 net R을 가중합한다.
* 🚨 **하한이다** — 인쇄된 체결을 우리가 전부 먹는다고 본다(호가 깊이·큐는 못 잰다, WAN-98).

재현: `uv run python -m backtest.wan448_stop_fill_ticks --part run`(시장은 WAN-442 피클 · 펀딩은
WAN-447 캐시 · 체결내역은 `data/cache/wan348-aggtrades`).
**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변 ·
실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).**
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import pickle
import random
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest import wan397_stop_slippage as s397
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest import wan440_universe as u
from backtest import wan442_momentum_rotation as m
from backtest import wan444_liquidity_universe as liq
from backtest import wan447_funding_carry as fc
from data import agg_trade_archive

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
TICKS_CSV = REPORT_DIR / "wan448_stop_fill_ticks.csv"
GRID_CSV = REPORT_DIR / "wan448_stop_fill_grid.csv"
NOTES_TXT = REPORT_DIR / "wan448_notes.txt"
SUMMARY_PATH = REPORT_DIR / "wan448_stop_fill_summary.md"

CRASH_SAMPLE = 100
NORMAL_SAMPLE = 60
SEED = 448
EQUITY_REF = 100_000.0
"""기준 계좌(USDT) — 우리 주문 크기를 정하는 값. 크기 의존은 ×10 열로 병기한다."""
RISK_REF = 0.0273
"""거래당 리스크 — WAN-447 거래대금 상위 31 · 캐리 50% 맞춤 크기."""
VERDICT_FRACTION = 0.5
TARGET_CAGR = 0.25
FRACTIONS: tuple[float, ...] = (0.0, 0.5, 1.0)
CHECK_TOL = 1e-12

CRASH = "폭락"
NORMAL = "평시"


def alpha_of(stop: float, fill: float, low: float) -> float:
    """체결이 손절가 → 그 1분 저가 사이 어디였나(0 = 손절가 · 1 = 저가). 저가가 손절가면 0."""
    span = stop - low
    if span <= 0.0:
        return 0.0
    return min(1.0, max(0.0, (stop - fill) / span))


def blend(r_stop: float, r_low: float, alpha: float) -> float:
    """net R의 청산가 선형성 — α 체결의 net R = 두 극단의 가중합."""
    return (1.0 - alpha) * r_stop + alpha * r_low


@dataclass(frozen=True)
class StopRow:
    """손절 거래 하나 — 표본 추출과 틱 판정의 입력."""

    symbol: str
    timeframe: str
    entry_time: int
    exit_ms: int
    stratum: str
    stop_price: float
    bar_low: float
    quantity: float


def stop_rows(mk: c.Market, trades: Sequence[w.PlacedTrade]) -> list[StopRow]:
    """손절로 끝난 선물 거래 → 청산 1분봉 · 층 · 우리 수량."""
    index = {e.key: i for i, e in enumerate(mk.entries)}
    layer = mk.layer(c.ARM_B)
    out: list[StopRow] = []
    for t in trades:
        if not t.stopped:
            continue
        i = index[(t.symbol, t.timeframe, t.entry_time, t.entry_price)]
        entry = mk.entries[i]
        cand, off = w.arm_exit(entry, c.RULES.stop_multiple, stop_fill="bar_low")
        if int(cand.exit_time) != t.exit_time:
            raise AssertionError(f"{t.symbol} {t.timeframe} 청산 시각이 배치와 다르다")
        stop = float(cand.stop_price)
        low = float(entry.lows[off])
        b24 = mk.btc_24h[i]
        dist = t.entry_price - stop
        out.append(
            StopRow(
                symbol=t.symbol,
                timeframe=t.timeframe,
                entry_time=t.entry_time,
                exit_ms=t.exit_time,
                stratum=CRASH if b24 <= c.CRASH_THRESHOLD else NORMAL,
                stop_price=stop,
                bar_low=low,
                quantity=EQUITY_REF * RISK_REF * layer[i] / dist,
            )
        )
    return out


def sample_rows(rows: Sequence[StopRow], *, seed: int = SEED) -> list[StopRow]:
    """층별 무작위 표본(시드 고정 · 층 안 순서는 입력 순서 기준)."""
    rng = random.Random(seed)
    out: list[StopRow] = []
    for stratum, want in ((CRASH, CRASH_SAMPLE), (NORMAL, NORMAL_SAMPLE)):
        pool = [r for r in rows if r.stratum == stratum]
        rng.shuffle(pool)
        out += pool[:want]
    return out


@dataclass(frozen=True)
class TickResult:
    row: StopRow
    ok: bool
    fill: float
    first: float
    fill_x10: float
    tick_low: float
    minute_volume: float
    note: str

    @property
    def alpha(self) -> float:
        return alpha_of(self.row.stop_price, self.fill, self.row.bar_low) if self.ok else math.nan

    @property
    def slippage_bp(self) -> float:
        s = self.row.stop_price
        return (s - self.fill) / s * 10_000.0 if self.ok else math.nan


def measure(
    sample: Sequence[StopRow], *, log: bool = True
) -> tuple[list[TickResult], dict[str, float]]:
    """표본마다 청산 1분의 체결내역을 펼쳐 체결 단가를 낸다(종목·일 단위로 파일 한 번)."""
    by_day: dict[tuple[str, str], list[StopRow]] = {}
    for r in sample:
        by_day.setdefault((r.symbol, agg_trade_archive.day_of(r.exit_ms)), []).append(r)
    stats = {"files": 0.0, "bytes": 0.0, "seconds": 0.0, "failed": 0.0}
    out: list[TickResult] = []
    for (symbol, day), rows in sorted(by_day.items()):
        fetch = agg_trade_archive.fetch_day(symbol, day)
        stats["files"] += 1
        stats["bytes"] += fetch.size_bytes
        stats["seconds"] += fetch.seconds
        if not fetch.ok or fetch.path is None:
            stats["failed"] += 1
            out += [
                TickResult(r, False, math.nan, math.nan, math.nan, math.nan, 0.0, fetch.note)
                for r in rows
            ]
            continue
        minutes = agg_trade_archive.minutes_ticks(fetch.path, [r.exit_ms for r in rows])
        for r in rows:
            ticks = minutes[r.exit_ms]
            fill, got, first = s397.walk_tape(
                ticks, stop_price=r.stop_price, quantity=r.quantity, is_long=True
            )
            fill10, _g, _f = s397.walk_tape(
                ticks, stop_price=r.stop_price, quantity=10 * r.quantity, is_long=True
            )
            low = min((t.price for t in ticks), default=math.nan)
            ok = not math.isnan(fill)
            note = "" if ok else "손절가에 닿는 체결 없음"
            if ok and got + 1e-12 < r.quantity:
                note = "그 분의 체결량이 주문보다 작음(채운 데까지)"
            out.append(TickResult(r, ok, fill, first, fill10, low, sum(t.qty for t in ticks), note))
        if log:
            print(f"[wan448] {symbol} {day}: {len(rows)}건", flush=True)
    return out, stats


def stratum_alpha(results: Sequence[TickResult]) -> dict[str, float]:
    """층별 평균 α(판정 가능한 표본만)."""
    out: dict[str, float] = {}
    for stratum in (CRASH, NORMAL):
        vals = [r.alpha for r in results if r.ok and r.row.stratum == stratum]
        if not vals:
            raise ValueError(f"{stratum} 층에 판정된 표본이 없다")
        out[stratum] = sum(vals) / len(vals)
    return out


def blended_trades(
    stop_trades: Sequence[w.PlacedTrade],
    low_trades: Sequence[w.PlacedTrade],
    alpha_by_trade: Sequence[float],
) -> list[w.PlacedTrade]:
    """두 극단 배치를 거래 단위로 짝지어 α 가중합 net R을 싣는다(짝이 어긋나면 죽는다)."""
    if not (len(stop_trades) == len(low_trades) == len(alpha_by_trade)):
        raise AssertionError("두 극단 배치의 거래 수가 다르다")
    out: list[w.PlacedTrade] = []
    for a, b, al in zip(stop_trades, low_trades, alpha_by_trade, strict=True):
        if (a.symbol, a.timeframe, a.entry_time, a.exit_time) != (
            b.symbol,
            b.timeframe,
            b.entry_time,
            b.exit_time,
        ):
            raise AssertionError(f"짝이 어긋났다: {a.symbol} {a.timeframe} {a.entry_time}")
        out.append(
            dataclasses.replace(a, net_r=blend(a.net_r, b.net_r, al) if a.stopped else a.net_r)
        )
    return out


def alphas_for(
    trades: Sequence[w.PlacedTrade], mk: c.Market, by_stratum: dict[str, float]
) -> list[float]:
    index = {e.key: i for i, e in enumerate(mk.entries)}
    out = []
    for t in trades:
        b24 = mk.btc_24h[index[(t.symbol, t.timeframe, t.entry_time, t.entry_price)]]
        out.append(by_stratum[CRASH if b24 <= c.CRASH_THRESHOLD else NORMAL])
    return out


@dataclass(frozen=True)
class GridRow:
    fill: str
    series: str
    fraction: float
    risk: float
    cagr: float
    mdd: float


def run(built: u.Markets, *, log: bool = True) -> tuple[pd.DataFrame, list[GridRow], list[str]]:
    t0 = time.monotonic()
    st = m.setting(built)
    top = st.rotations(lambda d, t: liq.select_liquidity(d, t, top=True))
    smk, _ = m.apply_rotation(st.spot_pool, top[0], force_close=False)
    fmk, _ = m.apply_rotation(st.fut_pool, top[1], force_close=False)
    placed = {
        fill: (
            c.place_arm(smk, c.ARM_B, fill, in_book=True),
            c.place_arm(fmk, c.ARM_B, fill, in_book=True),
        )
        for fill in c.FILLS
    }
    rows = stop_rows(fmk, placed["stop"][1])
    sample = sample_rows(rows)
    if log:
        n_crash = sum(r.stratum == CRASH for r in rows)
        print(
            f"[wan448] 선물 손절 {len(rows)}건(폭락 {n_crash}) · 표본 {len(sample)}건", flush=True
        )
    results, stats = measure(sample, log=log)
    by_stratum = stratum_alpha(results)

    fund, _fetched = fc.fetch_funding()
    trail = fc.flat(fund, fc.trailing_mean(fund, int(fund.times[-1])))
    fills: dict[str, tuple[list[w.PlacedTrade], list[w.PlacedTrade]]] = {
        "손절가(α=0)": placed["stop"],
        "최악(α=1)": placed["bar_low"],
    }
    for label, al in (("측정 α", by_stratum), ("α=0 검산", {CRASH: 0.0, NORMAL: 0.0})):
        fills[label] = tuple(  # type: ignore[assignment]
            blended_trades(
                placed["stop"][k], placed["bar_low"][k], alphas_for(placed["stop"][k], mk, al)
            )
            for k, mk in ((0, smk), (1, fmk))
        )
    yrs = c._years(u.ms(c.WINDOW_START), u.ms(harness.DEFAULT_END))

    def fit(
        sp: Sequence[w.PlacedTrade], fu: Sequence[w.PlacedTrade], fs: fc.Funding, fr: float
    ) -> float:
        return c.fit_risk(lambda x: fc.chain_carry(sp, fu, x, fs, fr)[1], target=c.MDD_TARGET)

    grid: list[GridRow] = []
    for fill, (sp, fu) in fills.items():
        for series, fs in ((fc.SERIES_ACTUAL, fund), (fc.SERIES_TRAILING, trail)):
            for fr in FRACTIONS:
                r = fit(sp, fu, fs, fr)
                tot, mdd = fc.chain_carry(sp, fu, r, fs, fr)
                grid.append(GridRow(fill, series, fr, r, c.cagr(tot, yrs), mdd))
    check = max(
        abs(a.cagr - b.cagr)
        for a in grid
        if a.fill == "α=0 검산"
        for b in grid
        if b.fill == "손절가(α=0)" and (b.series, b.fraction) == (a.series, a.fraction)
    )
    if check > CHECK_TOL:
        raise AssertionError(f"α=0 가중합이 손절가 체결과 다르다({check:.2e})")
    frame = pd.DataFrame(
        [
            {
                **dataclasses.asdict(r.row),
                "ok": r.ok,
                "fill": r.fill,
                "first": r.first,
                "fill_x10": r.fill_x10,
                "tick_low": r.tick_low,
                "minute_volume": r.minute_volume,
                "alpha": r.alpha,
                "slippage_bp": r.slippage_bp,
                "note": r.note,
            }
            for r in results
        ]
    )
    ok = frame[frame["ok"]]
    low_match = int((abs(ok["tick_low"] - ok["bar_low"]) <= 1e-9 * ok["bar_low"]).sum())
    notes = [
        f"선물 손절 {len(rows)}건(폭락 {sum(r.stratum == CRASH for r in rows)} · 평시 "
        f"{sum(r.stratum == NORMAL for r in rows)}) · 표본 {len(sample)}건 · 판정 {len(ok)}건",
        f"체결내역 파일 {int(stats['files'])}개 · {stats['bytes'] / 1e6:.0f}MB · "
        f"받기 실패 {int(stats['failed'])}",
        f"검산 — 틱 저가 ≡ 저장 1분봉 저가 {low_match}/{len(ok)} · "
        f"α=0 가중합 ≡ 손절가 체결(최대 차 {check:.2e})",
        f"층별 평균 α: 폭락 {by_stratum[CRASH]:.3f} · 평시 {by_stratum[NORMAL]:.3f}",
        f"측정 {time.monotonic() - t0:.0f}초",
    ]
    return frame, grid, notes


def verdict(grid: Sequence[GridRow]) -> tuple[bool, float]:
    hit = [
        g
        for g in grid
        if g.fill == "측정 α" and g.series == fc.SERIES_TRAILING and g.fraction == VERDICT_FRACTION
    ]
    if len(hit) != 1:
        raise ValueError(f"판정 행이 하나가 아니다: {len(hit)}")
    return hit[0].cagr >= TARGET_CAGR, hit[0].cagr


def render(frame: pd.DataFrame, grid: Sequence[GridRow], notes: Sequence[str]) -> str:
    ok_flag, val = verdict(grid)
    out = [
        "# WAN-448 — 스토캐스틱 팔 손절 체결가 틱 실측",
        "",
        "> 거래대금 상위 31 · 팔 B · 선물 손절 표본. "
        "α = (손절가 − 체결가)/(손절가 − 그 1분 저가). 🚨 하한"
        "(인쇄된 체결을 다 먹는다고 본다 · 호가 깊이·큐 미측정).",
        "",
        f"## 판정 — **{'25% 달성' if ok_flag else '미달'}** "
        f"(측정 α · 캐리 50% · 최근 12개월 펀딩 고정 · MDD 35% 맞춤 8.6년 연 {val:+.1%})",
        "",
        "## 체결 실측(판정된 표본)",
        "",
        "| 층 | 표본 | α 평균 | α 중앙 | 슬리피지 bp 중앙 / p90 | ×10 수량 슬리피지 bp 중앙 |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    ok = frame[frame["ok"]]
    for stratum in (CRASH, NORMAL):
        s = ok[ok["stratum"] == stratum]
        x10 = (s["stop_price"] - s["fill_x10"]) / s["stop_price"] * 10_000.0
        out.append(
            f"| {stratum} | {len(s)} | {s['alpha'].mean():.3f} | {s['alpha'].median():.3f} | "
            f"{s['slippage_bp'].median():.1f} / {s['slippage_bp'].quantile(0.9):.1f} | "
            f"{x10.median():.1f} |"
        )
    out += [
        "",
        "## 8.6년 연환산(거래대금 상위 31 · MDD 35% 맞춤)",
        "",
        "| 체결 | 펀딩 | 캐리 0% | 캐리 50% | 캐리 100% |",
        "|---|---|--:|--:|--:|",
    ]
    for fill in ("손절가(α=0)", "측정 α", "최악(α=1)"):
        for series in (fc.SERIES_ACTUAL, fc.SERIES_TRAILING):
            cells = [
                f"{g.cagr:+.1%} ({g.risk:.2%})"
                for fr in FRACTIONS
                for g in grid
                if (g.fill, g.series, g.fraction) == (fill, series, fr)
            ]
            out.append(f"| {fill} | {series} | " + " | ".join(cells) + " |")
    out += ["", "## 기록", ""] + [f"* {n}" for n in notes]
    out += [
        "",
        "**측정 전용 · 기본값·토대·페이퍼 불변 · DB 불변(체결내역은 `data/cache/`) · "
        "실거래 보류 유지"
        "(`ALPHABLOCK_LIVE_TRADING=false`).**",
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
        frame, grid, notes = run(built)
        frame.to_csv(TICKS_CSV, index=False)
        pd.DataFrame([dataclasses.asdict(g) for g in grid]).to_csv(GRID_CSV, index=False)
        NOTES_TXT.write_text("\n".join(notes) + "\n")
    frame = pd.read_csv(TICKS_CSV)
    grid = [GridRow(**r) for r in pd.read_csv(GRID_CSV).to_dict("records")]
    notes = NOTES_TXT.read_text().splitlines()
    SUMMARY_PATH.write_text(render(frame, grid, notes))
    print(SUMMARY_PATH.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
