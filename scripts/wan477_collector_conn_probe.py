"""WAN-477: 수집기 거래소 연결 셋의 메모리와 GC 전체 수거 시간을 잰다(맥에서 · 서버 금지).

수집기(`data.collector.run_collector`)는 후보 풀 말고도 거래소 연결을 **셋** 만든다 — 기동
백필·펀딩·꼬리 따라잡기용(``exchange``) · 팔 수집(`_build_arm_collection`) · REST 이중 수집
(`_build_rest_poller`). ccxt는 연결마다 첫 ``fetch_ohlcv`` 때 ``load_markets()``를 한 번 부르고
그 결과를 **인스턴스에** 들고 있다. 이 도구는 연결 셋을 수집기처럼 만들고 각각 첫 ``fetch_ohlcv``
까지 부른 뒤 무엇이 남는지 잰다.

변형(``--variant``):

* ``none``   — 연결 없음(임포트만 · 기준).
* ``all``    — 옛 동작: `create_exchange` 셋(현물·USDⓈ-M·COIN-M 시장 전부).
* ``linear`` — 새 동작: `create_collector_exchange` 셋(USDⓈ-M만 · 화폐 목록 안 받음).

재는 것(``--measure``) — 두 자를 **다른 프로세스**에서 잰다(``tracemalloc``이 GC 시간을 부풀린다):

* ``mem`` — RSS 전후(``ps``) · 파이썬 힙에 남은 몫(``tracemalloc``) · 연결별 증가 · 연결끼리
  시장 객체를 공유하는지(``markets`` dict와 그 안의 시장 dict가 같은 객체인지).
* ``gc``  — RSS 전후(추적 없음 = 서버와 같은 조건) · ``gc.collect()`` 전체 수거(세대 2)
  소요 시간(``gc.callbacks``로 잰다) · 그때 추적 중인
  객체 수(``len(gc.get_objects())``) · ``gc.freeze()`` 뒤 같은 수거의 시간과 객체 수.

재현: ``uv run python -m scripts.wan477_collector_conn_probe --all``(네트워크 필요 · 약 1분).
⚠️ 서버에서 돌리지 말 것 — 그 자체가 메모리 출렁임을 만든다(WAN-469 교훈).
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import subprocess
import sys
import time
import tracemalloc
from typing import Any

VARIANTS = ("none", "all", "linear")
MEASURES = ("mem", "gc")
#: 수집기가 만드는 연결 수(기동·펀딩 · 팔 · REST 이중 수집).
CONNECTIONS = 3
#: GC 시간은 같은 상태에서 여러 번 재 중앙값을 낸다.
GC_REPEATS = 5
SYMBOL = "BTC/USDT:USDT"
MB = 1024 * 1024


def _rss_mb() -> float:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, check=True
    )
    return int(out.stdout.strip()) / 1024  # ps는 KB


def _make(variant: str) -> Any:
    from config.settings import get_settings
    from data.exchange import create_collector_exchange, create_exchange

    settings = get_settings()
    return create_exchange(settings) if variant == "all" else create_collector_exchange(settings)


def _touch(exchange: Any) -> None:
    """수집기 연결이 처음 하는 일 — 확정봉 몇 개를 받는다(이때 ccxt가 시장 목록을 한 번 읽는다)."""
    exchange.fetch_ohlcv(SYMBOL, "1m", limit=2)


class _GcTimer:
    """세대 2 수거의 소요 시간을 `gc.callbacks`로 잰다(서버 진단 훅과 같은 방식)."""

    def __init__(self) -> None:
        self.durations: list[float] = []
        self._t0 = 0.0

    def __call__(self, phase: str, info: dict[str, Any]) -> None:
        if info.get("generation") != 2:
            return
        if phase == "start":
            self._t0 = time.perf_counter()
        else:
            self.durations.append(time.perf_counter() - self._t0)


def _time_full_collections(timer: _GcTimer) -> float:
    timer.durations.clear()
    for _ in range(GC_REPEATS):
        gc.collect()
    return statistics.median(timer.durations) * 1000


def _run(variant: str, measure: str) -> dict[str, Any]:
    from data.exchange import create_exchange  # noqa: F401 - 임포트 비용은 「전」에 넣는다

    row: dict[str, Any] = {"variant": variant, "measure": measure}
    conns: list[Any] = []

    if measure == "mem":
        tracemalloc.start()
        rss0 = _rss_mb()
        per: list[float] = []
        for _ in range(CONNECTIONS if variant != "none" else 0):
            before = _rss_mb()
            ex = _make(variant)
            _touch(ex)
            conns.append(ex)
            per.append(round(_rss_mb() - before, 1))
        gc.collect()
        current, _peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        row.update(
            rss_before=round(rss0, 1),
            rss_after=round(_rss_mb(), 1),
            rss_delta=round(_rss_mb() - rss0, 1),
            rss_per_conn=per,
            py_held=round(current / MB, 1),
        )
        if conns:
            a, b = conns[0], conns[1]
            row["markets_per_conn"] = len(a.markets)
            row["markets_dict_shared"] = a.markets is b.markets
            row["market_obj_shared"] = a.markets[SYMBOL] is b.markets[SYMBOL]
        return row

    # measure == "gc" — tracemalloc 없이(추적이 수거 시간·RSS를 부풀린다).
    rss0 = _rss_mb()
    for _ in range(CONNECTIONS if variant != "none" else 0):
        ex = _make(variant)
        _touch(ex)
        conns.append(ex)
    gc.collect()
    row["rss_before"] = round(rss0, 1)
    row["rss_after"] = round(_rss_mb(), 1)
    row["rss_delta"] = round(_rss_mb() - rss0, 1)
    timer = _GcTimer()
    gc.callbacks.append(timer)
    row["objects"] = len(gc.get_objects())
    row["gen2_ms"] = round(_time_full_collections(timer), 1)
    gc.freeze()
    row["frozen"] = gc.get_freeze_count()
    row["objects_after_freeze"] = len(gc.get_objects())
    row["gen2_ms_after_freeze"] = round(_time_full_collections(timer), 1)
    gc.unfreeze()
    gc.callbacks.remove(timer)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", choices=VARIANTS)
    ap.add_argument("--measure", choices=MEASURES)
    ap.add_argument("--all", action="store_true", help="변형 × 자마다 새 프로세스로 전부 잰다")
    args = ap.parse_args(argv)
    if args.variant and args.measure:
        print(json.dumps(_run(args.variant, args.measure), ensure_ascii=False))
        return 0
    if not args.all:
        ap.error("--variant와 --measure, 또는 --all")
    for measure in MEASURES:
        for variant in VARIANTS:
            out = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.wan477_collector_conn_probe",
                    "--variant",
                    variant,
                    "--measure",
                    measure,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if out.returncode != 0:
                print(out.stderr, file=sys.stderr)
                return out.returncode
            print(out.stdout.strip().splitlines()[-1], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
