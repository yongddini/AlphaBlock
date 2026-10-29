"""수집기 GC 진단(WAN-477) — 세대 2(전체) 수거 소요 시간을 로그에 남기고, 기동 뒤 객체를 얼린다.

둘 다 **기본 꺼짐**이고 수집 결과를 바꾸지 않는다(측정·운영 손잡이).

* `install_gc_pause_logging(min_ms)` — `gc.callbacks`에 훅을 걸어 세대 2 수거가 `min_ms` 이상
  걸리면 WARNING 한 줄을 남긴다. 가설: 스왑에 밀려난 객체를 전체 수거가 한 바퀴 돌며 다시 읽고,
  그동안 GIL을 쥐어 이벤트 루프가 멈춘다(서버 09:03 「43초 동시 공백」). 이 로그가 그 시각에
  찍히면 가설이 서고, 안 찍히면 다른 원인이다.
* `freeze_startup_objects()` — 기동 백필이 끝난 뒤 `gc.freeze()`로 그때까지의 객체를 영구 세대로
  옮긴다. 그 뒤 전체 수거는 얼린 객체를 **건드리지 않는다**(맥 측정: 수거 시간 → 0.0ms ·
  `scripts/wan477_collector_conn_probe.py`). 얼린 객체 중 순환 쓰레기는 영영 수거되지 않으므로
  손잡이로만 둔다.
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class GcPauseLogger:
    """세대 2 수거가 문턱 이상 걸리면 로그를 남기는 `gc.callbacks` 훅."""

    def __init__(self, min_ms: float, *, clock: Any = time.perf_counter) -> None:
        self.min_ms = min_ms
        self._clock = clock
        self._t0: float | None = None
        #: 마지막 세대 2 수거 소요 시간(ms) — 테스트·진단용.
        self.last_ms: float | None = None

    def __call__(self, phase: str, info: dict[str, Any]) -> None:
        if info.get("generation") != 2:
            return
        if phase == "start":
            self._t0 = self._clock()
            return
        if self._t0 is None:  # 훅을 수거 도중에 걸었다
            return
        elapsed_ms = (self._clock() - self._t0) * 1000
        self._t0 = None
        self.last_ms = elapsed_ms
        if elapsed_ms >= self.min_ms:
            logger.warning(
                "GC 세대 2 수거 %.1fms (수거 %s · 회수 불가 %s) — 이 동안 이벤트 루프가 멈춘다",
                elapsed_ms,
                info.get("collected"),
                info.get("uncollectable"),
            )


def install_gc_pause_logging(min_ms: float | None) -> GcPauseLogger | None:
    """`min_ms`가 None이면 아무것도 안 한다(기본). 걸면 훅을 돌려준다.

    떼려면 `gc.callbacks.remove(hook)`.
    """
    if min_ms is None:
        return None
    hook = GcPauseLogger(min_ms)
    gc.callbacks.append(hook)
    logger.info("GC 세대 2 수거 시간 로그 켬 — %.0fms 이상이면 남긴다", min_ms)
    return hook


def freeze_startup_objects() -> int:
    """지금까지의 객체를 얼린다(`gc.collect()` 뒤 `gc.freeze()`). 얼린 객체 수를 돌려준다."""
    gc.collect()
    gc.freeze()
    frozen = gc.get_freeze_count()
    logger.info("GC: 기동 뒤 객체 %d개를 얼렸다(전체 수거가 건드리지 않는다)", frozen)
    return frozen
