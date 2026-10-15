"""WAN-451 — 팔을 켜기 전 관문: 라이브 러너 전체 재생 거래 ≡ 정본 백테스트 거래.

그림자 대조(#369)는 탭마다 독립인 그림자끼리 비교해 실제 진입 경로의 차이를 못 봤다(#371). 이 관문은
**돈이 오가는 거래끼리** 맞댄다 — 같은 저장 데이터 · 같은 구간에서

* **라이브**: 실제 러너(`ZoneLimitPaperRunner` · 팔 켬 · 탭마다 독립 주문 · 오래된 존 기억 ·
  몰림 규칙 · 폭락 ×0.5 · 2배 손절 · ts4 시간 청산 · 같은 1분 가격 순서)를 1분봉으로 재생한다.
* **정본**: 백테스트 팔(`run_cells` → `w.build_entries` → `c.place_arm` = 몰림 · 폭락 층 · 채택 북 ·
  가격 순서)을 같은 구간으로 돈다.

몰림 규칙은 **다른 칸의 신호**를 세므로, 칸을 하나씩 재생하면 먼저 도는 칸이 뒤 칸의 신호를 못 본다.
그래서 두 번 돈다 — 1차는 신호 장부만 모으고, 2차는 모은 장부(31종목 × 10TF 전부)를 넣고 판정한다.
신호는 진입과 무관하게 정해지므로(그림자 = 탭마다 독립 주문) 1차 장부가 곧 실시간 러너가 볼 장부다.

맞대는 것:

1. **신호** — 라이브 `arm_signals`(센 것) ≡ 정본 팔 후보(목록 · 손절폭 ≥ 4% · %K < 25) — 체결 시각 ·
   체결가 · 오더블록 손절가.
2. **거래** — 진입 시각 · 진입가 · 2배 손절가 · 크기 배율(몰림 예산 × 폭락) · 청산 시각 · 청산 사유.

가정(밝힌다):

* **종목 목록은 고정**이다(로컬 DB에 전 TF가 있는 종목 전부 · 매주 같은 목록). 매주 목록 계산은
  WAN-451 ③ 검산이 306주 전부 일치를 이미 보였다 — 여기서는 목록 뒤의 판정만 본다.
* 라이브 재생은 종목마다 따로 돈다(병렬). 지갑이 종목마다 따로라 **거래 크기(수량)는 맞대지
  않는다** — 크기 배율만 맞댄다(명목 상한 5배는 정본 최대 1.96배라 안 걸린다).
* 오래된 존 기억은 라이브 2년(첫 공급 기준) · 정본 탐지 시작도 같은 2년 전이다.

재현: `uv run python -m live.arm_gate --jobs 4`. 요약은
`backtest/reports/wan451_arm_replay_gate_summary.md`. 측정 전용 · 엔진·기본값·토대 불변 ·
실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest import wan424_stoch_ob_arm as a424
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan439_crash_size_layer as c
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import run_cells
from common.timefmt import format_kst
from data.storage import OhlcvStore
from data.universe import ARM_TIMEFRAMES, rebalance_at

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
SUMMARY_PATH = REPORT_DIR / "wan451_arm_replay_gate_summary.md"
TRADES_CSV = REPORT_DIR / "wan451_arm_replay_gate_trades.csv"

WINDOW_START = "2026-04-01"
WINDOW_END = "2026-07-10"
#: 데이터 상한 — 대조 구간 끝 + 1w 시간 청산(최대 4주)보다 넉넉히 뒤.
DATA_CAP = "2026-08-12"
DAY = 86_400_000
MINUTE = 60_000
#: 재생 시작(월요일 00:00 UTC) — 몰림 신호(직전 24h)를 세려고 대조 구간보다 앞이다. 라이브는 이 뒤의
#: 탭만 보므로 정본도 이 뒤에 탭된 후보만 쓴다(1d·1w 주문은 수 주 살아 있어 앞 탭이 섞이면 안 된다).
REPLAY_FROM = "2026-03-30"
MEMORY_DAYS = 730
#: 크기 배율 비교 허용 오차(상대) — 곱셈 순서 차이만 넘긴다.
SCALE_TOL = 1e-9
#: 진입가·손절가 비교 허용 오차(상대).
PRICE_TOL = 1e-9

Key = tuple[str, str, int]


def _day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------


def symbols_with_data(db: Path, cap_ms: int) -> list[str]:
    """1분봉이 상한까지 있고 팔 원천 TF가 전부 저장된 종목."""
    need = {tf for tf in ARM_TIMEFRAMES if tf != "2h"} | {"1m"}
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT symbol, timeframe, MAX(open_time) FROM ohlcv GROUP BY symbol, timeframe"
        ).fetchall()
    finally:
        conn.close()
    have: dict[str, dict[str, int]] = {}
    for sym, tf, mx in rows:
        have.setdefault(str(sym), {})[str(tf)] = int(mx)
    out = []
    for sym, tfs in have.items():
        if need <= set(tfs) and tfs["1m"] >= cap_ms - MINUTE:
            out.append(sym)
    return sorted(out)


def make_clipped_db(
    src: Path, dst: Path, symbols: Sequence[str], htf_from: int, m1_from: int, cap: int
) -> None:
    """재생용 복사본 — 상한 뒤 봉이 없다(라이브 재생이 상한 뒤를 못 보게)."""
    OhlcvStore(dst).close()  # 스키마
    conn = sqlite3.connect(dst)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (str(src),))
        marks = ",".join("?" * len(symbols))
        conn.execute(
            f"INSERT INTO ohlcv SELECT * FROM src.ohlcv WHERE symbol IN ({marks})"
            " AND timeframe != '1m' AND open_time >= ? AND open_time < ?",
            (*symbols, htf_from, cap),
        )
        conn.execute(
            f"INSERT INTO ohlcv SELECT * FROM src.ohlcv WHERE symbol IN ({marks})"
            " AND timeframe = '1m' AND open_time >= ? AND open_time < ?",
            (*symbols, m1_from, cap),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 정본
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Canon:
    signals: set[tuple[str, str, int, float, float]]
    """(종목, TF, 체결 시각, 체결가, 오더블록 손절가) — 몰림 규칙이 세는 팔 후보."""
    trades: dict[Key, w.PlacedTrade]
    decisions: dict[Key, str]
    """팔 후보마다 정본 판정(진입 · 몰림 건너뜀 · 칸에 포지션)."""


def canon(
    symbols: Sequence[str],
    replay_from: int,
    cap: int,
    jobs: int,
    *,
    members_at: Callable[[int], frozenset[str]] | None = None,
) -> Canon:
    """정본 팔 — 재생 시작 뒤에 탭된 후보만(라이브가 보는 것과 같다).

    `members_at`(진입 시각 → 그 주 목록)를 주면 정본 `apply_rotation`처럼 목록 밖 후보를 처음부터
    뺀다(몰림 신호 수도 남은 후보로 센다). 안 주면 `symbols` 전부가 매주 목록이다(관문의 고정 목록).
    """
    payloads = run_cells(
        list(symbols),
        tuple(ARM_TIMEFRAMES),
        start=_day(replay_from - MEMORY_DAYS * DAY),
        end=_day(cap),
        jobs=jobs,
        **a424.base_cell_kwargs(),
    )
    store = OhlcvStore(harness.DB_PATH)
    try:
        entries = [
            e
            for e in w.build_entries(payloads, store=store, start_ms=replay_from, end_ms=cap)
            if int(e.cand.trigger_time) >= replay_from
            and (members_at is None or e.symbol in members_at(int(e.cand.entry_time)))
        ]
        counts = w.signal_counts([int(e.cand.entry_time) for e in entries])
        b24, vr = c.btc_features(store, entries, replay_from - 2 * DAY, cap)
    finally:
        store.close()
    mk = c.Market("관문", list(payloads), entries, counts, b24, vr)
    placed = c.place_arm(mk, c.ARM_B, "stop", in_book=True)
    trades = {(t.symbol, t.timeframe, t.entry_time): t for t in placed}
    decisions: dict[Key, str] = {}
    for e, n in zip(entries, counts, strict=True):
        key = (e.symbol, e.timeframe, int(e.cand.entry_time))
        if key in trades:
            decisions[key] = "진입"
        elif w.rule_decision(c.RULES, n, e.btc_4h) <= 0.0:
            decisions[key] = f"몰림 건너뜀(신호 {n}개)"
        else:
            decisions[key] = "칸에 포지션(또는 같은 1분 체결 먼저)"
    signals = {
        (
            e.symbol,
            e.timeframe,
            int(e.cand.entry_time),
            float(e.cand.entry_price),
            float(e.cand.stop_price),
        )
        for e in entries
    }
    return Canon(signals, trades, decisions)


# ---------------------------------------------------------------------------
# 라이브 재생
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snap:
    rebalance_ms: int
    members: frozenset[str]


class FixedUniverse:
    """매주 같은 목록 — 목록 게이트를 대조에서 뺀다(목록 계산은 따로 검산됐다)."""

    def __init__(self, members: Sequence[str]) -> None:
        self._members = frozenset(members)

    def at(self, ms: int) -> _Snap:
        return _Snap(rebalance_at(ms), self._members)


@dataclass(frozen=True)
class LiveTrade:
    symbol: str
    timeframe: str
    entry_time: int
    entry_price: float
    stop_price: float
    scale: float
    exit_time: int | None
    exit_price: float | None
    reason: str | None


@dataclass
class ReplayOut:
    symbol: str
    signals: list[tuple[str, str, int, int, int, float, float, bool, str]] = field(
        default_factory=list
    )
    trades: list[LiveTrade] = field(default_factory=list)
    rejects: dict[Key, str] = field(default_factory=dict)
    seconds: float = 0.0


def replay_symbol(args: tuple[str, str, Sequence[str], int, str | None]) -> ReplayOut:
    """한 종목의 팔 TF 전부를 실제 러너로 재생한다(워커 — 자기 장부 DB를 쓴다)."""
    from config.settings import Settings
    from execution.engine import build_execution_engine
    from live.arm_rules import ArmParams
    from live.arm_signals import ArmSignal, ArmSignalStore
    from live.executor import PaperExecutor
    from live.limit_engine import ZoneLimitLiveEngine
    from live.order_journal import OrderJournal
    from live.zone_limit_runner import (
        ARM_FILL_PENETRATION_BPS,
        ARM_RISK_PER_TRADE,
        ZoneLimitPaperRunner,
        build_paper_recorder,
    )
    from paper.store import PaperTradeStore

    symbol, data_db, members, replay_from, seed = args
    t0 = time.monotonic()
    out = ReplayOut(symbol)
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / "ledger.db"
        settings = Settings(
            db_path=str(ledger),
            confluence={"retap_mode": "once", "fill_penetration_bps": ARM_FILL_PENETRATION_BPS},
            risk_sizing={"risk_per_trade": ARM_RISK_PER_TRADE},
        )
        signals = ArmSignalStore(ledger)
        if seed is not None:
            src = sqlite3.connect(seed)
            for row in src.execute(
                "SELECT symbol, timeframe, zone_start, zone_confirmed, fill_ms, price, ob_stop,"
                " counted, reason_code FROM arm_signals"
            ):
                signals.record(
                    ArmSignal(
                        str(row[0]),
                        str(row[1]),
                        int(row[2]),
                        int(row[3]),
                        int(row[4]),
                        float(row[5]),
                        float(row[6]),
                        bool(row[7]),
                        str(row[8]),
                    )
                )
            src.close()
        paper = PaperTradeStore(ledger)
        journal = OrderJournal(ledger)
        session = journal.start_session(now_ms=0)
        store = OhlcvStore(data_db)
        executor = PaperExecutor(
            engine=build_execution_engine(settings),
            store=paper,
            recorder=build_paper_recorder(paper, settings),
            sizing=settings.risk_sizing,
        )
        scales: dict[Key, tuple[float, float]] = {}
        enter = executor.enter

        def _enter(intent, *, now_ms):  # type: ignore[no-untyped-def]
            report = enter(intent, now_ms=now_ms)
            if report.accepted:
                key = (intent.symbol, intent.timeframe, int(intent.entry_time))
                scales[key] = (float(intent.risk_scale), float(intent.stop_price or 0.0))
            return report

        executor.enter = _enter  # type: ignore[method-assign]
        runner = ZoneLimitPaperRunner(
            store=store,
            engine=ZoneLimitLiveEngine(
                params=settings.confluence, journal=journal, session_id=session
            ),
            journal=journal,
            session_id=session,
            executor=executor,
            params=settings.confluence,
            series=[(symbol, tf) for tf in ARM_TIMEFRAMES],
            lookback_bars=settings.live_signal_lookback_bars,
            poll_interval_seconds=0,
            arm=ArmParams(),
            universe=FixedUniverse(members),  # type: ignore[arg-type]
            signals=signals,
            bootstrap_days=MEMORY_DAYS,
        )
        for tf in ARM_TIMEFRAMES:
            runner._last_substep[(symbol, tf)] = replay_from - MINUTE
            runner.poll_series(symbol, tf)
        conn = sqlite3.connect(ledger)
        out.signals = [
            (
                str(r[0]),
                str(r[1]),
                int(r[2]),
                int(r[3]),
                int(r[4]),
                float(r[5]),
                float(r[6]),
                bool(r[7]),
                str(r[8]),
            )
            for r in conn.execute(
                "SELECT symbol, timeframe, zone_start, zone_confirmed, fill_ms, price, ob_stop,"
                " counted, reason_code FROM arm_signals WHERE symbol = ?",
                (symbol,),
            )
        ]
        for r in conn.execute(
            "SELECT symbol, timeframe, entry_time, entry_price, exit_time, exit_price, reason"
            " FROM paper_trades"
        ):
            key = (str(r[0]), str(r[1]), int(r[2]))
            scale, stop = scales.get(key, (float("nan"), float("nan")))
            out.trades.append(
                LiveTrade(*key, float(r[3]), stop, scale, int(r[4]), float(r[5]), str(r[6]))
            )
        for r in conn.execute(
            "SELECT symbol, timeframe, entry_time, entry_price FROM open_positions"
        ):
            key = (str(r[0]), str(r[1]), int(r[2]))
            scale, stop = scales.get(key, (float("nan"), float("nan")))
            out.trades.append(LiveTrade(*key, float(r[3]), stop, scale, None, None, None))
        for r in conn.execute(
            "SELECT symbol, timeframe, fill_ms, entry_status, COALESCE(entry_reject_code, '')"
            " FROM live_limit_orders WHERE status = 'filled'"
        ):
            out.rejects[(str(r[0]), str(r[1]), int(r[2]))] = (
                "진입" if r[3] == "entered" else f"거부({r[4]})"
            )
        conn.close()
        store.close()
    out.seconds = time.monotonic() - t0
    return out


def replay(
    symbols: Sequence[str],
    data_db: Path,
    replay_from: int,
    seed: Path | None,
    jobs: int,
) -> list[ReplayOut]:
    args = [
        (s, str(data_db), list(symbols), replay_from, str(seed) if seed else None) for s in symbols
    ]
    if jobs <= 1:
        return [replay_symbol(a) for a in args]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(replay_symbol, args))


def write_seed(outs: Sequence[ReplayOut], path: Path) -> None:
    from live.arm_signals import ArmSignal, ArmSignalStore

    store = ArmSignalStore(path)
    for o in outs:
        for r in o.signals:
            store.record(ArmSignal(*r))
    store.close()


# ---------------------------------------------------------------------------
# 대조
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeCheck:
    key: Key
    canon: w.PlacedTrade | None
    live: LiveTrade | None
    problems: tuple[str, ...]


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)


def compare_trades(
    canon_trades: dict[Key, w.PlacedTrade], live: Sequence[LiveTrade], start: int, end: int
) -> list[TradeCheck]:
    lv = {(t.symbol, t.timeframe, t.entry_time): t for t in live if start <= t.entry_time < end}
    cv = {k: t for k, t in canon_trades.items() if start <= k[2] < end}
    out: list[TradeCheck] = []
    for key in sorted(set(lv) | set(cv), key=lambda k: (k[2], k[0], k[1])):
        ct, lt = cv.get(key), lv.get(key)
        problems: list[str] = []
        if ct is None:
            problems.append("라이브만 진입")
        elif lt is None:
            problems.append("정본만 진입")
        else:
            if not _close(ct.entry_price, lt.entry_price, PRICE_TOL):
                problems.append(f"진입가 {ct.entry_price:.8g} ≠ {lt.entry_price:.8g}")
            if not _close(ct.stop_price, lt.stop_price, PRICE_TOL):
                problems.append(f"손절가 {ct.stop_price:.8g} ≠ {lt.stop_price:.8g}")
            if not _close(ct.size, lt.scale, SCALE_TOL):
                problems.append(f"크기 배율 {ct.size:.6g} ≠ {lt.scale:.6g}")
            if lt.exit_time != ct.exit_time:
                problems.append(
                    "청산 시각 "
                    f"{format_kst(ct.exit_time)} ≠ "
                    f"{'—' if lt.exit_time is None else format_kst(lt.exit_time)}"
                )
            live_stop = lt.reason == "stop_loss"
            if lt.exit_time is not None and live_stop != ct.stopped:
                problems.append(f"청산 사유 {'손절' if ct.stopped else '시간'} ≠ {lt.reason}")
        out.append(TradeCheck(key, ct, lt, tuple(problems)))
    return out


def render(
    symbols: Sequence[str],
    sig_canon: set[tuple[str, str, int, float, float]],
    sig_live: set[tuple[str, str, int, float, float]],
    checks: Sequence[TradeCheck],
    canon_decisions: dict[Key, str],
    live_rejects: dict[Key, str],
    elapsed: dict[str, float],
) -> str:
    bad = [c_ for c_ in checks if c_.problems]
    both = [c_ for c_ in checks if c_.canon is not None and c_.live is not None]
    lines = [
        "# WAN-451 — 팔 켜기 전 관문: 라이브 재생 거래 ≡ 정본 백테스트 거래",
        "",
        f"- 대조 구간 {WINDOW_START} ~ {WINDOW_END}(UTC) · 데이터 상한 {DATA_CAP}"
        f" · 종목 {len(symbols)}개 × 팔 TF {len(ARM_TIMEFRAMES)}개"
        f" · 목록 고정(매주 같은 {len(symbols)}종목)",
        f"- 시간: 정본 {elapsed['canon']:.0f}초 · 라이브 1차(신호) {elapsed['pass1']:.0f}초 · "
        f"2차(판정) {elapsed['pass2']:.0f}초",
        "",
        "## 1. 몰림 신호(팔 후보의 가상 체결)",
        "",
        f"- 정본 {len(sig_canon)}건 · 라이브 {len(sig_live)}건"
        f" · 일치 {len(sig_canon & sig_live)}건",
        f"- 정본만 {len(sig_canon - sig_live)}건 · 라이브만 {len(sig_live - sig_canon)}건",
        "",
    ]
    for label, diff in (("정본만", sig_canon - sig_live), ("라이브만", sig_live - sig_canon)):
        for s in sorted(diff, key=lambda x: x[2])[:30]:
            lines.append(
                f"  - {label}: {s[0].split('/')[0]} {s[1]} {format_kst(s[2])}"
                f" 체결가 {s[3]:.8g} 손절 {s[4]:.8g}"
            )
    lines += [
        "",
        "## 2. 거래",
        "",
        f"- 정본 {sum(1 for c_ in checks if c_.canon)}건"
        f" · 라이브 {sum(1 for c_ in checks if c_.live)}건"
        f" · 둘 다 {len(both)}건 · 어긋남 **{len(bad)}건**",
        "",
    ]
    if bad:
        lines += [
            "| 진입(KST) | 칸 | 문제 | 정본 판정 | 라이브 판정 |",
            "| -- | -- | -- | -- | -- |",
        ]
        for c_ in bad:
            k = c_.key
            lines.append(
                f"| {format_kst(k[2])} | {k[0].split('/')[0]} {k[1]} | {' · '.join(c_.problems)} |"
                f" {canon_decisions.get(k, '후보 아님')} | {live_rejects.get(k, '체결 없음')} |"
            )
    else:
        lines.append("✅ 모든 거래가 진입·손절가·크기 배율·청산까지 일치한다.")
    lines += [
        "",
        "## 읽는 법",
        "",
        "- 라이브는 실제 러너 코드를 1분봉으로 재생한 것이다(틱 없음 — 팔은 1분봉만 쓴다).",
        "- 크기는 배율(몰림 예산 × 폭락 ×0.5)만 맞댄다 — 재생 지갑이 종목마다 따로다.",
        "- 목록은 고정이다(목록 계산은 306주 대조로 따로 검산됐다).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=harness.default_jobs())
    ap.add_argument("--symbols", default="", help="콤마 목록(탐색용 · 기본은 데이터가 있는 전부)")
    ap.add_argument("--keep", type=Path, default=None, help="재생 DB를 남길 디렉터리(디버깅)")
    args = ap.parse_args(argv)

    start, end, cap = (parse_date_ms(x) for x in (WINDOW_START, WINDOW_END, DATA_CAP))
    replay_from = parse_date_ms(REPLAY_FROM)
    db = Path(harness.DB_PATH)
    symbols = [s for s in args.symbols.split(",") if s] or symbols_with_data(db, cap)
    if "BTC/USDT:USDT" not in symbols:
        raise SystemExit("BTC 1분봉이 상한까지 없다 — 몰림·폭락 판정을 못 한다")
    work = args.keep or Path(tempfile.mkdtemp(prefix="wan451-gate-"))
    work.mkdir(parents=True, exist_ok=True)
    data_db = work / "data.db"
    elapsed: dict[str, float] = {}
    try:
        t = time.monotonic()
        make_clipped_db(
            db, data_db, symbols, replay_from - (MEMORY_DAYS + 40) * DAY, replay_from - 2 * DAY, cap
        )
        print(f"재생 DB {time.monotonic() - t:.0f}초 · 종목 {len(symbols)}", flush=True)

        t = time.monotonic()
        cn = canon(symbols, replay_from, cap, args.jobs)
        elapsed["canon"] = time.monotonic() - t
        print(f"정본 {elapsed['canon']:.0f}초 · 거래 {len(cn.trades)}", flush=True)

        t = time.monotonic()
        first = replay(symbols, data_db, replay_from, None, args.jobs)
        elapsed["pass1"] = time.monotonic() - t
        seed = work / "seed.db"
        write_seed(first, seed)
        print(f"라이브 1차 {elapsed['pass1']:.0f}초", flush=True)

        t = time.monotonic()
        second = replay(symbols, data_db, replay_from, seed, args.jobs)
        elapsed["pass2"] = time.monotonic() - t
        print(f"라이브 2차 {elapsed['pass2']:.0f}초", flush=True)
    finally:
        if args.keep is None:
            shutil.rmtree(work, ignore_errors=True)

    sig_live = {(r[0], r[1], r[4], r[5], r[6]) for o in second for r in o.signals if r[7]}
    sig_canon = {s for s in cn.signals if replay_from <= s[2] < cap}
    sig_live = {s for s in sig_live if replay_from <= s[2] < cap}
    live_trades = [t_ for o in second for t_ in o.trades]
    rejects = {k: v for o in second for k, v in o.rejects.items()}
    checks = compare_trades(cn.trades, live_trades, start, end)
    text = render(symbols, sig_canon, sig_live, checks, cn.decisions, rejects, elapsed)
    SUMMARY_PATH.write_text(text, encoding="utf-8")
    pd.DataFrame(
        [
            {
                "symbol": c_.key[0],
                "timeframe": c_.key[1],
                "entry_time": c_.key[2],
                "entry_kst": format_kst(c_.key[2]),
                "canon": c_.canon is not None,
                "live": c_.live is not None,
                "problems": " · ".join(c_.problems),
            }
            for c_ in checks
        ]
    ).to_csv(TRADES_CSV, index=False)
    print(text)
    return 0 if not any(c_.problems for c_ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
