"""러너 메모리·폴링 시간 실측 — 스토캐스틱 팔(310 시리즈)이 서버에 들어가는가 (WAN-451 ④ · WAN-453).

사용자 결정(2026-10-14, WAN-451 §3 ④ 가): 팔을 켜기 **전에** 서버(RAM 1GB · 2코어)에서 메모리를
실측하고, 모자라면 그때 업그레이드를 정한다. 이 스크립트가 그 측정기다.

## 무엇을 재나 — 기본 실행은 「켜는 날의 러너」 그대로다(WAN-453)

임시 DB에 합성 봉(랜덤 워크)을 채우고 **실제 러너 코드**(`ZoneLimitPaperRunner` +
`ZoneLimitLiveEngine` + 페이퍼 집행기 + 엔진 상태 영속화)를 그 위에서 돌린다. 배선은
`run_zone_limit_runner`가 팔을 켰을 때와 같다:

* 31종목 × **팔 10TF**(15m·1h·2h·3h·4h·6h·8h·12h·1d·1w — `data.universe.ARM_TIMEFRAMES`)
  = 310 시리즈
* **그 주 종목 목록**(`UniverseStore`)과 **몰림 신호 장부**(`ArmSignalStore`)가 붙는다
* 첫 기동은 **2년치 오래된 존 기억**(`live_zone_memory_days`)으로 탐지한다 — 런북의
  「처음 한 번은 약 10분」이 이 단계다

그리고 **최대 RSS**(프로세스가 잡은 실제 메모리의 최고점) · 기동 폴링 시간 · 정상 폴링 시간을 낸다.

🔁 **WAN-451 ④의 첫 측정(약 213MB)은 이 배선 전의 값이다** — 그때는 신호 장부 · 종목 목록 ·
3h · 2년치 존 기억이 없어 9TF로 재고 10/9배로 환산했다. 그 숫자를 「켠 뒤의 메모리」로 인용하지
말 것. 옛 측정(목록·신호 장부·존 기억 없이)은 `--legacy`로 남겨 뒀다.

## 측정을 지키는 장치

* **시드 봉은 별도 프로세스가 만든다** — 2년치 15m(종목당 7만 봉)를 만드는 동안의 메모리가
  러너의 최대 RSS에 섞이면 러너 몫이 부풀려진다. 부모가 시드 프로세스 → 측정 프로세스 순서로
  띄우고 둘이 같은 임시 DB를 쓴다.
* **러너가 일을 안 했으면 무효라고 찍는다** — 가동 시리즈 수 · 살아 있는 존 수가 0이면 「빨랐다 =
  아무것도 안 했다」이다(WAN-367).

## 알고 받는 한계

* 합성 봉이라 오더블록 수가 실제와 다를 수 있다 — 자릿수를 보는 측정이다.
* 수집기·대시보드는 별도 프로세스라 이 숫자에 안 들어간다. 판정에는 그 몫을 빼고 남은
  여유(`MemAvailable`)와 비교한다. 지금 도는 옛 러너는 전환하면 내려가므로 그 몫만큼 여유가 는다.
* 임시 DB가 수백 MB가 된다(31종목 × 2년치) — 서버 디스크 여유를 먼저 볼 것(`df -h /tmp`).
  `--workdir`로 다른 곳에 만들 수 있다.

## 사용법(서버)

    cd ~/AlphaBlock && uv run python scripts/wan451_runner_memory_probe.py
    # 결과 표가 stdout으로 나온다 — 그대로 WAN-453 이슈에 붙인다.

종목 수·존 기억 일수만 바꿔 보기: `--symbols 31 --bootstrap-days 730`. DB는 임시 디렉터리에
만들고 끝나면 지운다(서버 DB를 건드리지 않는다).
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MINUTE_MS = 60_000
DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
NOW_TFS = ("15m", "1h", "2h", "4h")
"""`--legacy` 비교용 — WAN-451 ④가 잰 「지금 구성」(12종목 × 4TF)."""
ARM_TFS = ("15m", "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d", "1w")
"""팔 10TF — `data.universe.ARM_TIMEFRAMES`와 같아야 한다(테스트가 고정)."""
MAX_HISTORY_BARS = {"1w": 320}
"""TF별 합성 이력 상한(1w는 실제로도 6년치 ≈ 320봉뿐이다)."""
DERIVED_SOURCE = {"2h": "1h"}
"""저장소가 원천에서 접어 내는 TF(`data.storage._DERIVED_TIMEFRAMES`와 같다 — 테스트가 고정)."""


@dataclass(frozen=True)
class ProbeResult:
    symbols: int
    timeframes: list[str]
    series: int
    baseline_rss_mb: float
    peak_rss_mb: float
    prime_seconds: float
    steady_poll_seconds: list[float]
    primed_series: int
    """엔진이 실제로 상태를 만든 시리즈 수 — `series`와 같아야 측정이 성립한다."""
    zones: int
    """살아 있는 존 수 합계 — 0이면 탐지가 안 돈 것이라 메모리가 과소평가된다."""
    bootstrap_days: int = 0
    """첫 기동의 오래된 존 기억 일수(0 = 옛 측정 · 창 1500봉만)."""
    production: bool = False
    """종목 목록 · 몰림 신호 장부까지 켠 「켜는 날의 러너」 배선인가(WAN-453)."""
    signals: int = 0
    """측정 중 신호 장부에 남은 행 수(체결이 없었으면 0 — 무효는 아니다)."""

    @property
    def runner_mb(self) -> float:
        return self.peak_rss_mb - self.baseline_rss_mb


def _rss_mb() -> float:
    """현재 RSS(MB). 리눅스는 /proc, 그 밖은 최대 RSS로 대신한다."""
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return _peak_rss_mb()


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # 리눅스는 KB, macOS는 바이트다.
    return peak / (1024.0 * 1024.0) if sys.platform == "darwin" else peak / 1024.0


def _symbols(n: int) -> list[str]:
    return [f"SYN{i:02d}/USDT:USDT" for i in range(n)]


def history_bars(
    timeframes: tuple[str, ...], *, lookback: int, bootstrap_days: int
) -> dict[str, int]:
    """저장할 TF별 봉 수 — 러너가 첫 기동에 읽는 창(`_window_bars`)을 덮는다.

    창 = max(lookback, 존 기억 일수만큼의 봉). 파생 TF(2h)는 원천(1h)에 그 배수만큼 싣는다.
    """
    from data.models import timeframe_to_ms

    need: dict[str, int] = {}
    for tf in timeframes:
        tf_ms = timeframe_to_ms(tf)
        bars = max(lookback, -(-bootstrap_days * DAY_MS // tf_ms))
        bars = min(bars, MAX_HISTORY_BARS.get(tf, 10**9))
        source = DERIVED_SOURCE.get(tf, tf)
        source_bars = bars * (tf_ms // timeframe_to_ms(source))
        need[source] = max(need.get(source, 0), source_bars)
    return need


def _seed(
    db: Path,
    symbols: list[str],
    timeframes: tuple[str, ...],
    *,
    end_ms: int,
    lookback: int,
    minute_bars: int,
    seed: int,
    bootstrap_days: int = 0,
) -> None:
    from data.models import Candle, timeframe_to_ms
    from data.storage import OhlcvStore

    rng = np.random.default_rng(seed)
    need = history_bars(timeframes, lookback=lookback, bootstrap_days=bootstrap_days)
    with OhlcvStore(db) as store:
        for sym in symbols:
            price = float(rng.uniform(1.0, 50_000.0))
            for tf in sorted(need):
                tf_ms = timeframe_to_ms(tf)
                bars = need[tf]
                last_open = (end_ms // tf_ms) * tf_ms - tf_ms
                closes = price * np.cumprod(1.0 + rng.normal(0.0, 0.01, bars))
                # 봉마다 난수를 차례로 뽑는다 — 이 순서를 바꾸면 같은 시드에서 다른 봉이 나와
                # 그 데이터로 체결을 확인하는 테스트(`test_wan451_runner_poll_reuse`)가 흔들린다.
                candles = []
                for k, c in enumerate(closes):
                    t = last_open - (bars - 1 - k) * tf_ms
                    o = c * (1.0 + float(rng.normal(0, 0.002)))
                    hi = max(o, c) * (1.0 + abs(float(rng.normal(0, 0.006))))
                    lo = min(o, c) * (1.0 - abs(float(rng.normal(0, 0.006))))
                    candles.append(Candle(sym, tf, int(t), o, hi, lo, float(c), 100.0))
                store.upsert_candles(candles)
            m_closes = price * np.cumprod(1.0 + rng.normal(0.0, 0.001, minute_bars))
            start = end_ms - minute_bars * MINUTE_MS
            store.upsert_candles(
                Candle(
                    sym,
                    "1m",
                    start + k * MINUTE_MS,
                    float(c),
                    float(c) * 1.0005,
                    float(c) * 0.9995,
                    float(c),
                    1.0,
                )
                for k, c in enumerate(m_closes)
            )


def _append_minute(db: Path, symbols: list[str], t: int) -> None:
    from data.models import Candle
    from data.storage import OhlcvStore

    with OhlcvStore(db) as store:
        last = store.load(symbols[0], "1m", start_ms=t - MINUTE_MS)
        base = float(last["close"].iloc[-1]) if not last.empty else 100.0
        store.upsert_candles(Candle(s, "1m", t, base, base, base, base, 1.0) for s in symbols)


def _probe_end_ms() -> int:
    """측정 시각 — 주 경계(월요일이 아니라 epoch 주라도 상관없다: 목록은 직접 넣는다)."""
    return (int(time.time() * 1000) // WEEK_MS) * WEEK_MS


def run_probe(
    *,
    symbols: int,
    timeframes: tuple[str, ...],
    polls: int,
    lookback: int,
    minute_bars: int,
    workdir: Path | None = None,
    db: Path | None = None,
    end_ms: int | None = None,
    bootstrap_days: int = 0,
    production: bool = False,
) -> ProbeResult:
    """한 구성을 이 프로세스에서 잰다(비교는 구성마다 새 프로세스로 — `main`).

    `db`를 주면 이미 시드된 DB를 쓴다(시드 메모리가 이 프로세스의 최대 RSS에 안 섞인다).
    `production=True`면 `run_zone_limit_runner`가 팔을 켰을 때와 같은 배선이다: 그 주 종목 목록 ·
    몰림 신호 장부 · 팔 10TF 전부(`timeframes`는 무시된다).
    """
    baseline = _rss_mb()
    from config.settings import Settings
    from data.storage import OhlcvStore
    from data.universe import ARM_TIMEFRAMES, rebalance_at
    from data.universe_pool import RotationSnapshot, UniverseStore
    from execution.engine import build_execution_engine
    from execution.leverage import book_per_trade_sizing
    from live.arm_rules import ArmParams
    from live.arm_signals import ArmSignalStore
    from live.engine_state import EngineStateStore
    from live.executor import PaperExecutor
    from live.limit_engine import ZoneLimitLiveEngine
    from live.order_journal import OrderJournal
    from live.zone_limit_runner import ZoneLimitPaperRunner, build_paper_recorder
    from paper.store import PaperTradeStore

    baseline = max(baseline, _rss_mb())  # import 몫은 기준선에 넣는다(러너 몫이 아니다)
    tfs = tuple(ARM_TIMEFRAMES) if production else timeframes
    tmp = tempfile.TemporaryDirectory(dir=workdir) if db is None else None
    try:
        syms = _symbols(symbols)
        end = end_ms if end_ms is not None else _probe_end_ms()
        if db is None:
            assert tmp is not None
            db = Path(tmp.name) / "probe.db"
            _seed(
                db,
                syms,
                tfs,
                end_ms=end,
                lookback=lookback,
                minute_bars=minute_bars,
                seed=451,
                bootstrap_days=bootstrap_days,
            )
        settings = Settings(db_path=str(db))
        store = OhlcvStore(db)
        journal = OrderJournal(db)
        paper = PaperTradeStore(db)
        book = settings.live_leverage_book
        executor = PaperExecutor(
            engine=build_execution_engine(settings, leverage_book=book),
            store=paper,
            recorder=build_paper_recorder(paper, settings),
            sizing=book_per_trade_sizing(settings.risk_sizing, book),
        )
        session = journal.start_session(now_ms=end)
        engine = ZoneLimitLiveEngine(
            params=settings.confluence, journal=journal, session_id=session
        )
        universe: UniverseStore | None = None
        signals: ArmSignalStore | None = None
        if production:
            universe = UniverseStore(db)
            week = rebalance_at(end)
            universe.save(
                RotationSnapshot(
                    rebalance_ms=week,
                    computed_ms=week,
                    pool_size=len(syms),
                    stale_size=0,
                    ranked=tuple((s, 1.0) for s in syms),
                    top_n=len(syms),
                )
            )
            signals = ArmSignalStore(db)
        clock = [end]
        runner = ZoneLimitPaperRunner(
            store=store,
            engine=engine,
            journal=journal,
            session_id=session,
            executor=executor,
            params=settings.confluence,
            series=[(s, tf) for s in syms for tf in tfs],
            lookback_bars=lookback,
            poll_interval_seconds=0,
            engine_state=EngineStateStore(db),
            arm=ArmParams(),
            universe=universe,
            signals=signals,
            bootstrap_days=bootstrap_days,
            now_ms=lambda: clock[0],
        )
        t0 = time.perf_counter()
        runner.poll_once()
        prime = time.perf_counter() - t0
        steady: list[float] = []
        for k in range(polls):
            t = end + k * MINUTE_MS
            _append_minute(db, syms, t)
            clock[0] = t + MINUTE_MS
            t0 = time.perf_counter()
            runner.poll_once()
            steady.append(time.perf_counter() - t0)
        return ProbeResult(
            symbols=symbols,
            timeframes=list(tfs),
            series=symbols * len(tfs),
            baseline_rss_mb=baseline,
            peak_rss_mb=_peak_rss_mb(),
            prime_seconds=prime,
            steady_poll_seconds=steady,
            # 측정이 실제로 일을 했다는 증거(「빨랐다 = 아무것도 안 했다」를 막는다, WAN-367).
            primed_series=len(engine._series),
            zones=sum(len(st.zones) for st in engine._series.values()),
            bootstrap_days=bootstrap_days,
            production=production,
            signals=len(signals.rows(0, 2**62)) if signals is not None else 0,
        )
    finally:
        if tmp is not None:
            tmp.cleanup()


def _meminfo_mb() -> dict[str, float]:
    path = Path("/proc/meminfo")
    if not path.exists():
        return {}
    out: dict[str, float] = {}
    for line in path.read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            out[key] = int(rest.split()[0]) / 1024.0
    return out


def _run_child(cmd: list[str], what: str) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"{what} 실패: {proc.stderr[-2000:]}")
    return proc.stdout


def _child(
    symbols: int,
    timeframes: tuple[str, ...],
    args: argparse.Namespace,
    *,
    production: bool,
    bootstrap_days: int,
) -> ProbeResult:
    """시드 프로세스 → 측정 프로세스(같은 임시 DB). 시드 메모리는 측정 쪽 최대 RSS에 안 섞인다."""
    common = [
        "--symbols",
        str(symbols),
        "--timeframes",
        ",".join(timeframes),
        "--lookback",
        str(args.lookback),
        "--minute-bars",
        str(args.minute_bars),
        "--bootstrap-days",
        str(bootstrap_days),
        "--end-ms",
        str(_probe_end_ms()),
    ] + (["--production"] if production else [])
    me = [sys.executable, str(Path(__file__).resolve())]
    with tempfile.TemporaryDirectory(dir=args.workdir) as tmp:
        db = str(Path(tmp) / "probe.db")
        _run_child([*me, "--seed-only", "--db", db, *common], f"시드({symbols}종목)")
        out = _run_child(
            [*me, "--json", "--db", db, "--polls", str(args.polls), *common],
            f"측정({symbols}종목)",
        )
    return ProbeResult(**json.loads(out.strip().splitlines()[-1]))


def _hollow(r: ProbeResult) -> str | None:
    if r.primed_series != r.series or r.zones == 0:
        return (
            f"🚨 측정 무효 의심: {r.symbols}종목 구성에서 가동 시리즈 {r.primed_series}/{r.series}"
            f" · 존 {r.zones} — 러너가 일을 다 안 했으면 메모리가 과소평가된다."
        )
    return None


def render(arm: ProbeResult, mem: dict[str, float]) -> str:
    """「켜는 날의 러너」 한 구성의 표(WAN-453 서버 재측정 — 그대로 이슈에 붙인다)."""
    wiring = "종목 목록 · 몰림 신호 장부 · " if arm.production else ""
    lines = [
        "## WAN-453 켜기 전 서버 재측정 — 스토캐스틱 팔 러너",
        "",
        f"배선: {wiring}존 기억 {arm.bootstrap_days}일 · {arm.symbols}종목 × "
        f"{len(arm.timeframes)}TF({', '.join(arm.timeframes)})",
        "",
        "| 시리즈(실제 가동) | 존 | 신호 장부 행 | 러너 몫 메모리(MB) | 최대 RSS(MB)"
        " | 기동 폴링(초) | 정상 폴링 최대(초) |",
        "| --: | --: | --: | --: | --: | --: | --: |",
        f"| {arm.series}({arm.primed_series}) | {arm.zones:,} | {arm.signals:,} |"
        f" **{arm.runner_mb:,.0f}** | {arm.peak_rss_mb:,.0f} | {arm.prime_seconds:,.1f} |"
        f" {max(arm.steady_poll_seconds or [0.0]):,.2f} |",
        "",
    ]
    hollow = _hollow(arm)
    if hollow:
        lines.append(hollow)
    if mem:
        avail = mem.get("MemAvailable", 0.0)
        lines.append(
            f"서버 메모리: 전체 {mem.get('MemTotal', 0):,.0f} MB · 지금 여유(MemAvailable) "
            f"{avail:,.0f} MB · 스왑 {mem.get('SwapFree', 0):,.0f}"
            f"/{mem.get('SwapTotal', 0):,.0f} MB"
        )
        need = arm.peak_rss_mb
        lines.append(
            f"판정: 팔 러너 최대 RSS {need:,.0f} MB vs 여유 {avail:,.0f} MB → "
            + (
                "**들어간다**"
                if need < avail * 0.8
                else "**빠듯하거나 모자란다 — 업그레이드 검토**"
            )
        )
        lines.append(
            "⚠️ 지금 도는 옛 러너가 쓰는 메모리는 여유에서 빠져 있다 — 전환하면 옛 러너가 내려가므로"
            " 그 몫만큼 실제 여유는 더 크다."
        )
    lines.append(
        "⚠️ 합성 봉이라 자릿수만 본다(존·체결 수가 실제와 다를 수 있다). 수집기·대시보드는 별도"
        " 프로세스라 포함되지 않는다."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", type=int, default=None)
    parser.add_argument("--timeframes", default=None)
    parser.add_argument("--polls", type=int, default=3)
    parser.add_argument(
        "--lookback", type=int, default=None, help="기본: 설정 live_signal_lookback_bars"
    )
    parser.add_argument("--minute-bars", type=int, default=2 * 1440)
    parser.add_argument(
        "--bootstrap-days", type=int, default=None, help="기본: 설정 live_zone_memory_days(730)"
    )
    parser.add_argument("--workdir", type=Path, default=None, help="임시 DB를 만들 곳")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="WAN-451 ④ 옛 측정(목록·신호 장부·존 기억 없이, 9TF+환산 대신 그대로)",
    )
    # 내부용(자식 프로세스) — 사람이 직접 줄 일은 없다.
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--seed-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--production", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--db", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--end-ms", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from config.settings import Settings

    settings = Settings()
    if args.lookback is None:
        args.lookback = settings.live_signal_lookback_bars
    bootstrap = (
        args.bootstrap_days if args.bootstrap_days is not None else settings.live_zone_memory_days
    )
    tfs = tuple((args.timeframes or ",".join(ARM_TFS)).split(","))
    symbols = args.symbols or 31

    if args.seed_only:
        assert args.db is not None and args.end_ms is not None
        _seed(
            args.db,
            _symbols(symbols),
            ARM_TFS if args.production else tfs,
            end_ms=args.end_ms,
            lookback=args.lookback,
            minute_bars=args.minute_bars,
            seed=451,
            bootstrap_days=bootstrap,
        )
        return 0
    if args.json:
        result = run_probe(
            symbols=symbols,
            timeframes=tfs,
            polls=args.polls,
            lookback=args.lookback,
            minute_bars=args.minute_bars,
            db=args.db,
            end_ms=args.end_ms,
            bootstrap_days=bootstrap,
            production=args.production,
        )
        print(json.dumps(asdict(result)))
        return 0

    os.environ.setdefault("PYTHONHASHSEED", "0")
    if args.legacy:
        print("측정 중 — 옛 측정(목록·신호 장부·존 기억 없이)...", file=sys.stderr)
        arm = _child(symbols, tfs, args, production=False, bootstrap_days=0)
    else:
        print(
            f"측정 중 — 켜는 날의 러너({symbols}종목 × 10TF · 존 기억 {bootstrap}일)."
            " 시드에 몇 분, 첫 기동에 서버 기준 약 10분 걸린다...",
            file=sys.stderr,
        )
        arm = _child(symbols, ARM_TFS, args, production=True, bootstrap_days=bootstrap)
    print(render(arm, _meminfo_mb()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
