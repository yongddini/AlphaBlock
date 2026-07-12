"""프로세스 하트비트 영속화 (WAN-31).

상시 구동되는 데몬(수집기·러너)이 "마지막으로 살아 움직인 시각"을 작은 JSON 파일에
주기적으로 남겨, 운영 상태(Health) 대시보드가 프로세스 생존 여부를 판정할 수 있게
한다. 러너는 이미 `RuntimeStateStore`(포지션·신호 포함)로 더 풍부한 상태를 남기지만,
**수집기**는 남길 상태가 하트비트뿐이므로 이 가벼운 스토어를 사용한다.

저장은 임시 파일에 쓴 뒤 원자적으로 바꿔치기해(atomic replace) 중간 크래시로 파일이
손상되지 않게 한다.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

_logger = logging.getLogger(__name__)


class Heartbeat(BaseModel):
    """하트비트 스냅샷(파일로 직렬화되는 최상위 모델)."""

    #: 마지막으로 하트비트를 기록한 벽시계 시각(ms). 한 번도 없으면 None.
    updated_at: int | None = None
    #: 어떤 프로세스가 남긴 하트비트인지 식별용 라벨(예: "collector").
    label: str = ""


class HeartbeatStore:
    """프로세스 하트비트를 JSON 파일로 영속화한다(단일 작성자).

    파일이 없거나 손상됐으면 빈 상태로 읽는다. `beat()`는 현재 시각으로 하트비트를
    갱신·저장한다. 잦은 호출에도 부담이 없도록 `min_interval_ms`(기본 0)로 최소 기록
    간격을 둘 수 있다 — 마지막 기록 이후 그만큼 지나지 않았으면 파일 쓰기를 건너뛴다.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        label: str = "",
        min_interval_ms: int = 0,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._path = Path(path)
        self._label = label
        self._min_interval_ms = max(0, min_interval_ms)
        self._now_ms = now_ms
        self._last_written: int | None = None

    def beat(self) -> bool:
        """현재 시각으로 하트비트를 기록한다.

        `min_interval_ms` 스로틀에 걸려 기록을 건너뛰면 False, 실제로 파일에 쓰면 True.
        """
        now = self._now_ms()
        if self._last_written is not None and now - self._last_written < self._min_interval_ms:
            return False
        self._write(Heartbeat(updated_at=now, label=self._label))
        self._last_written = now
        return True

    def load(self) -> Heartbeat:
        """파일에서 현재 하트비트를 읽는다(없거나 손상 시 빈 하트비트)."""
        if not self._path.exists():
            return Heartbeat(label=self._label)
        try:
            return Heartbeat.model_validate_json(self._path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            _logger.warning("하트비트 파일을 읽지 못해 빈 상태로 처리: %s", exc)
            return Heartbeat(label=self._label)

    def _write(self, beat: Heartbeat) -> None:
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(beat.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
