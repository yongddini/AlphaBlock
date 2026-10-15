"""러너 메모리·폴링 시간 실측 — 스토캐스틱 팔(약 310 시리즈)이 서버에 들어가는가 (WAN-451 ④).

사용자 결정(2026-10-14, WAN-451 §3 ④ 가): 팔을 배선하기 **전에** 서버(RAM 1GB · 2코어)에서
메모리를 실측하고, 모자라면 그때 업그레이드를 정한다. 이 스크립트가 그 측정기다.

## 무엇을 재나

임시 DB에 합성 봉(랜덤 워크)을 채우고 **실제 러너 코드**(`ZoneLimitPaperRunner` +
`ZoneLimitLiveEngine` + 페이퍼 집행기 + 엔진 상태 영속화)를 그 위에서 돌린다. 시리즈(종목 × TF)
수만 바꿔 두 구성을 각각 **별도 프로세스**로 잰다(한 프로세스에서 이어 재면 앞 구성의 메모리가
남아 섞인다):

* 지금: 12종목 × 15m·1h·2h·4h = 48 시리즈
* 팔:   31종목 × 15m·1h·2h·4h·6h·8h·12h·1d·1w = 279 시리즈 → **10 TF(약 310)로 환산**

각 구성에서:

1. **기동(프라이밍) 폴링** — 시리즈마다 확정 상위TF 창(최대 1500봉)을 처음 먹이고 오더블록을
   탐지한다. 재시작 직후 한 번 치르는 비용이다.
2. **정상 폴링** — 1분봉이 한 개씩 늘 때마다 한 바퀴. 서버가 매분 치르는 비용이다.

그리고 **최대 RSS**(프로세스가 잡은 실제 메모리의 최고점)와 폴링 시간을 낸다.

## 알고 받는 한계

* **하한이다** — 팔에만 있는 부품(몰림 규칙의 신호 장부 · %K · 주간 교체)은 아직 없어서
  안 잰다. 그 몫은 시리즈 수에 비례해 더 붙는다.
* 3h는 아직 TF 표에 없어(WAN-451 §2-1) 9 TF로 재고 10/9배로 환산한다.
* 합성 봉이라 오더블록 수가 실제와 다를 수 있다 — 자릿수를 보는 측정이다.
* 수집기·대시보드는 별도 프로세스라 이 숫자에 안 들어간다. 판정에는 그 몫을 빼고 남은
  여유(`MemAvailable`)와 비교한다.

## 사용법(서버)

    cd ~/AlphaBlock && uv run python scripts/wan451_runner_memory_probe.py
    # 결과 표가 stdout으로 나온다 — 그대로 이슈에 붙인다.

한 구성만: `--symbols 31 --timeframes 15m,1h,2h,4h,6h,8h,12h,1d,1w --json`.
DB는 임시 디렉터리에 만들고 끝나면 지운다(서버 DB를 건드리지 않는다).
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
NOW_TFS = ("15m", "1h", "2h", "4h")
ARM_TFS = ("15m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w")
ARM_TF_COUNT = 10
"""정본 팔 TF 수(3h 포함) — 9 TF 측정값을 이 수로 환산한다."""
MAX_HISTORY_BARS = {"1w": 320, "1d": 1500}
"""TF별 합성 이력 상한(1w는 실제로도 6년치 ≈ 320봉뿐이다)."""


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


def _seed(
    db: Path,
    symbols: list[str],
    timeframes: tuple[str, ...],
    *,
    end_ms: int,
    lookback: int,
    minute_bars: int,
    seed: int,
) -> None:
    from data.models import Candle, timeframe_to_ms
    from data.storage import OhlcvStore

    rng = np.random.default_rng(seed)
    stored = {"1h" if tf == "2h" else tf for tf in timeframes}  # 2h는 1h에서 파생된다
    with OhlcvStore(db) as store:
        for sym in symbols:
            price = float(rng.uniform(1.0, 50_000.0))
            for tf in sorted(stored):
                tf_ms = timeframe_to_ms(tf)
                bars = min(lookback * (2 if tf == "1h" else 1), MAX_HISTORY_BARS.get(tf, 10**9))
                last_open = (end_ms // tf_ms) * tf_ms - tf_ms
                closes = price * np.cumprod(1.0 + rng.normal(0.0, 0.01, bars))
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


def run_probe(
    *,
    symbols: int,
    timeframes: tuple[str, ...],
    polls: int,
    lookback: int,
    minute_bars: int,
    workdir: Path | None = None,
) -> ProbeResult:
    """한 구성을 이 프로세스에서 잰다(비교는 구성마다 새 프로세스로 — `main`)."""
    baseline = _rss_mb()
    from config.settings import Settings
    from data.storage import OhlcvStore
    from execution.engine import build_execution_engine
    from execution.leverage import book_per_trade_sizing
    from live.arm_rules import ArmParams
    from live.engine_state import EngineStateStore
    from live.executor import PaperExecutor
    from live.limit_engine import ZoneLimitLiveEngine
    from live.order_journal import OrderJournal
    from live.zone_limit_runner import ZoneLimitPaperRunner, build_paper_recorder
    from paper.store import PaperTradeStore

    baseline = max(baseline, _rss_mb())  # import 몫은 기준선에 넣는다(러너 몫이 아니다)
    tmp = tempfile.TemporaryDirectory(dir=workdir)
    try:
        db = Path(tmp.name) / "probe.db"
        syms = _symbols(symbols)
        end_ms = (int(time.time() * 1000) // (7 * 86_400_000)) * (7 * 86_400_000)
        _seed(
            db,
            syms,
            timeframes,
            end_ms=end_ms,
            lookback=lookback,
            minute_bars=minute_bars,
            seed=451,
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
        session = journal.start_session(now_ms=end_ms)
        engine = ZoneLimitLiveEngine(
            params=settings.confluence, journal=journal, session_id=session
        )
        clock = [end_ms]
        runner = ZoneLimitPaperRunner(
            store=store,
            engine=engine,
            journal=journal,
            session_id=session,
            executor=executor,
            params=settings.confluence,
            series=[(s, tf) for s in syms for tf in timeframes],
            lookback_bars=lookback,
            poll_interval_seconds=0,
            engine_state=EngineStateStore(db),
            arm=ArmParams(),
            now_ms=lambda: clock[0],
        )
        t0 = time.perf_counter()
        runner.poll_once()
        prime = time.perf_counter() - t0
        steady: list[float] = []
        for k in range(polls):
            t = end_ms + k * MINUTE_MS
            _append_minute(db, syms, t)
            clock[0] = t + MINUTE_MS
            t0 = time.perf_counter()
            runner.poll_once()
            steady.append(time.perf_counter() - t0)
        return ProbeResult(
            symbols=symbols,
            timeframes=list(timeframes),
            series=symbols * len(timeframes),
            baseline_rss_mb=baseline,
            peak_rss_mb=_peak_rss_mb(),
            prime_seconds=prime,
            steady_poll_seconds=steady,
            # 측정이 실제로 일을 했다는 증거(「빨랐다 = 아무것도 안 했다」를 막는다, WAN-367).
            primed_series=len(engine._series),
            zones=sum(len(st.zones) for st in engine._series.values()),
        )
    finally:
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


def _child(symbols: int, timeframes: tuple[str, ...], args: argparse.Namespace) -> ProbeResult:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--json",
        "--symbols",
        str(symbols),
        "--timeframes",
        ",".join(timeframes),
        "--polls",
        str(args.polls),
        "--lookback",
        str(args.lookback),
        "--minute-bars",
        str(args.minute_bars),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"측정 실패({symbols}종목): {proc.stderr[-2000:]}")
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    return ProbeResult(**data)


def render(now: ProbeResult, arm: ProbeResult, mem: dict[str, float]) -> str:
    scale = ARM_TF_COUNT / len(arm.timeframes)
    arm_mb = arm.runner_mb * scale
    arm_steady = max(arm.steady_poll_seconds) * scale if arm.steady_poll_seconds else 0.0
    lines = [
        "## WAN-451 ④ 러너 메모리 실측",
        "",
        "| 구성 | 시리즈(실제 가동) | 존 | 러너 몫 메모리(MB) | 최대 RSS(MB)"
        " | 기동 폴링(초) | 정상 폴링 최대(초) |",
        "| -- | --: | --: | --: | --: | --: | --: |",
    ]
    for name, r in (
        ("지금(12종목 × 4TF)", now),
        (f"팔 측정({arm.symbols}종목 × {len(arm.timeframes)}TF)", arm),
    ):
        lines.append(
            f"| {name} | {r.series}({r.primed_series}) | {r.zones:,} | {r.runner_mb:,.0f}"
            f" | {r.peak_rss_mb:,.0f} | "
            f"{r.prime_seconds:,.1f} | {max(r.steady_poll_seconds or [0.0]):,.2f} |"
        )
    lines.append(
        f"| **팔 환산({arm.symbols}종목 × {ARM_TF_COUNT}TF)** | {round(arm.series * scale)} | — | "
        f"**{arm_mb:,.0f}** | {arm.baseline_rss_mb + arm_mb:,.0f} | "
        f"{arm.prime_seconds * scale:,.1f} | {arm_steady:,.2f} |"
    )
    lines.append("")
    for r in (now, arm):
        if r.primed_series != r.series or r.zones == 0:
            lines.append(
                f"🚨 측정 무효 의심: {r.symbols}종목 구성에서 가동 시리즈"
                f" {r.primed_series}/{r.series}"
                f" · 존 {r.zones} — 러너가 일을 다 안 했으면 메모리가 과소평가된다."
            )
    if mem:
        avail = mem.get("MemAvailable", 0.0)
        lines.append(
            f"서버 메모리: 전체 {mem.get('MemTotal', 0):,.0f} MB · 지금 여유(MemAvailable) "
            f"{avail:,.0f} MB · 스왑 {mem.get('SwapFree', 0):,.0f}"
            f"/{mem.get('SwapTotal', 0):,.0f} MB"
        )
        need = arm.baseline_rss_mb + arm_mb
        lines.append(
            f"판정(하한 기준): 팔 러너 예상 {need:,.0f} MB vs 여유 {avail:,.0f} MB → "
            + (
                "**들어간다**"
                if need < avail * 0.8
                else "**빠듯하거나 모자란다 — 업그레이드 검토**"
            )
        )
        lines.append(
            "⚠️ 지금 러너가 이미 쓰는 메모리는 여유에서 빠져 있다 — 전환하면 옛 러너가 내려가므로"
            " 그 몫만큼 실제 여유는 더 크다."
        )
    lines.append(
        "⚠️ 하한이다 — 몰림 신호 장부·%K·주간 교체는 아직 없어 안 쟀다. 합성 봉이라 자릿수만 본다."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", type=int, default=None)
    parser.add_argument("--timeframes", default=None)
    parser.add_argument("--polls", type=int, default=3)
    parser.add_argument("--lookback", type=int, default=1500)
    parser.add_argument("--minute-bars", type=int, default=2 * 1440)
    parser.add_argument("--json", action="store_true", help="한 구성만 재고 JSON 한 줄로 낸다")
    args = parser.parse_args(argv)

    if args.json:
        tfs = tuple((args.timeframes or ",".join(ARM_TFS)).split(","))
        result = run_probe(
            symbols=args.symbols or 31,
            timeframes=tfs,
            polls=args.polls,
            lookback=args.lookback,
            minute_bars=args.minute_bars,
        )
        print(json.dumps(asdict(result)))
        return 0

    os.environ.setdefault("PYTHONHASHSEED", "0")
    print("측정 중 — 지금 구성(12종목 × 4TF)...", file=sys.stderr)
    now = _child(12, NOW_TFS, args)
    print("측정 중 — 팔 구성(31종목 × 9TF) · 몇 분 걸린다...", file=sys.stderr)
    arm = _child(args.symbols or 31, ARM_TFS, args)
    print(render(now, arm, _meminfo_mb()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
