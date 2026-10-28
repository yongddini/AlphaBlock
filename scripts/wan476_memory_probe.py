"""WAN-476: 후보 풀 갱신의 부분별 최대 메모리를 잰다(맥에서 · 서버에서 돌리지 말 것).

부분마다 **새 프로세스**에서 한 번씩 부른다 — 한 프로세스에서 이어 부르면 앞 부분이 잡은
메모리가 뒤 부분의 「전」으로 섞인다. 재는 것:

* ``rss_before`` · ``rss_after`` — 부분 전후의 RSS(MB, ``psutil`` 없이 ``ps``로).
* ``peak_rss`` — 프로세스 최대 RSS(MB, ``resource.getrusage``).
* ``py_peak`` — 그 부분이 파이썬 힙에 동시에 잡은 최대(MB, ``tracemalloc``).
* ``held`` — 부분이 끝난 뒤에도 살아 있는 객체가 잡은 파이썬 힙(MB, 참조를 들고 있는 상태).

부분(``--part``):

* ``markets_all``      — 옛 동작: ``create_exchange``의 ``load_markets(True)``(현물·USDⓈ-M·COIN-M).
* ``markets_pool``     — 새 동작: ``create_pool_exchange``(USDⓈ-M만 · 화폐 목록 안 받음).
* ``refresh``          — ``refresh_pool_daily``(임시 DB · 풀 전체 · 종목당 최근 2봉).
* ``rotation``         — ``compute_rotation``(저장소 DB 읽기 · 이번 주 월요일 — 로컬 DB에 풀 일봉이
  없으면 빈 읽기다).
* ``rotation_full``    — ``compute_rotation``(임시 DB에 풀 전 종목 × 400일 합성 일봉을 채운 상한).
* ``arm_compute``      — ``ArmCollection.compute``(저장소 DB 읽기).

재현: ``uv run python -m scripts.wan476_memory_probe --all``(네트워크 필요 · 약 5분).
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
import tracemalloc
from typing import Any

PARTS = ("markets_all", "markets_pool", "refresh", "rotation", "rotation_full", "arm_compute")
MB = 1024 * 1024


def _rss_mb() -> float:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True
    )
    return int(out.stdout.strip()) / 1024  # ps는 KB


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / MB if sys.platform == "darwin" else peak / 1024  # 맥은 바이트 · 리눅스는 KB


def _run_part(part: str) -> dict[str, Any]:
    # 임포트 비용은 「전」에 넣는다 — 부분이 끌어올리는 몫만 보려고.
    from config.settings import get_settings
    from data import universe as uni
    from data.exchange import create_exchange, create_pool_exchange
    from data.storage import OhlcvStore
    from data.universe_pool import (
        ROTATION_READ_DAYS,
        UniverseStore,
        compute_rotation,
        pool_symbols,
        refresh_pool_daily,
    )

    settings = get_settings()
    held: Any = None
    extra: dict[str, Any] = {}

    if part in ("refresh", "rotation", "rotation_full", "arm_compute"):
        # 풀 목록은 「전」에서 만들어 둔다(그 부분의 몫이 아니다).
        ex = create_pool_exchange(settings)
        pool = pool_symbols(ex.load_markets(True))
        extra["pool"] = len(pool)

    tmp: tempfile.TemporaryDirectory[str] | None = None
    if part == "rotation_full":
        from data.models import Candle

        tmp = tempfile.TemporaryDirectory()
        full_store = OhlcvStore(os.path.join(tmp.name, "full.db"))
        t = uni.rebalance_at(int(time.time() * 1000))
        days = ROTATION_READ_DAYS
        for i, sym in enumerate(pool):
            full_store.upsert_candles(
                [
                    Candle(
                        symbol=sym,
                        timeframe="1d",
                        open_time=t - (days - k) * uni.DAY_MS,
                        open=1.0 + i,
                        high=1.1 + i,
                        low=0.9 + i,
                        close=1.0 + i,
                        volume=1000.0 + k,
                        closed=True,
                    )
                    for k in range(days)
                ]
            )

    tracemalloc.start()
    rss_before = _rss_mb()
    peak_before = _peak_rss_mb()
    t0 = time.monotonic()

    if part == "markets_all":
        ex = create_exchange(settings)
        held = ex.load_markets(True)
        extra["markets"] = len(held)
        extra["pool"] = len(pool_symbols(held))
        held = ex  # 수집기는 인스턴스를 계속 들고 있다 — 그 몫까지 잰다
    elif part == "markets_pool":
        ex = create_pool_exchange(settings)
        held = ex.load_markets(True)
        extra["markets"] = len(held)
        extra["pool"] = len(pool_symbols(held))
        held = ex
    elif part == "refresh":
        with tempfile.TemporaryDirectory() as d:
            store = OhlcvStore(os.path.join(d, "probe.db"))
            res = refresh_pool_daily(
                ex, store, pool, lookback_days=2, request_interval_seconds=0.05
            )
            extra["stored"] = res.stored
            extra["failed"] = len(res.failed)
            store.close()
    elif part == "rotation":
        store = OhlcvStore(settings.db_path)
        t = uni.rebalance_at(int(time.time() * 1000))
        snap = compute_rotation(store, pool, t, computed_ms=t)
        extra["scored"] = snap.scored_size
        extra["stale"] = snap.stale_size
    elif part == "rotation_full":
        snap = compute_rotation(full_store, pool, t, computed_ms=t)
        extra["scored"] = snap.scored_size
        extra["stale"] = snap.stale_size
        full_store.close()
    elif part == "arm_compute":
        from data.collection_plan import ArmCollection

        store = OhlcvStore(settings.db_path)
        universe = UniverseStore(settings.db_path)
        arm = ArmCollection(
            exchange=ex,
            store=store,
            universe=universe,
            db_path=settings.db_path,
            base_symbols=list(settings.symbols),
            base_timeframes=list(settings.timeframes),
            lookback_days=settings.lookback_days_for,
            pause_seconds=0.0,
        )
        plan = arm.compute()
        extra["pairs"] = len(plan.pairs())
    else:  # pragma: no cover
        raise SystemExit(f"알 수 없는 부분: {part}")

    elapsed = time.monotonic() - t0
    current, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if tmp is not None:
        tmp.cleanup()
    return {
        "part": part,
        "seconds": round(elapsed, 1),
        "rss_before": round(rss_before, 1),
        "rss_after": round(_rss_mb(), 1),
        "peak_rss": round(_peak_rss_mb(), 1),
        "peak_rss_delta": round(_peak_rss_mb() - max(peak_before, rss_before), 1),
        "py_peak": round(py_peak / MB, 1),
        "held": round(current / MB, 1) if held is not None else 0.0,
        **extra,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--part", choices=PARTS)
    ap.add_argument("--all", action="store_true", help="부분마다 새 프로세스로 전부 잰다")
    args = ap.parse_args(argv)
    if args.part:
        print(json.dumps(_run_part(args.part), ensure_ascii=False))
        return 0
    if not args.all:
        ap.error("--part 또는 --all")
    rows = []
    for part in PARTS:
        out = subprocess.run(
            [sys.executable, "-m", "scripts.wan476_memory_probe", "--part", part],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            print(out.stderr, file=sys.stderr)
            return out.returncode
        rows.append(json.loads(out.stdout.strip().splitlines()[-1]))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
