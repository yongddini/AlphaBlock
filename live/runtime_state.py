"""실시간 러너의 운영 상태 영속화 (WAN-30).

WAN-25 시그널 러너가 매 폴링마다 **하트비트·현재 페이퍼 포지션·최근 신호 이력**을
JSON 파일로 남겨, 운영 상태(Health) 대시보드가 "지금 러너가 살아있는지 / 무슨
포지션을 들고 있는지 / 최근에 무슨 신호가 났는지"를 읽을 수 있게 한다.

워터마크 상태(`WatermarkStore`)와 별개 파일이며, 저장은 임시 파일에 쓴 뒤 원자적으로
바꿔치기해(atomic replace) 중간 크래시로 파일이 손상되지 않게 한다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from live.notifier import SignalEvent
from live.paper import PaperPosition
from strategy.confluence import SignalKind
from strategy.models import OrderBlockDirection, SignalExitReason

_logger = logging.getLogger(__name__)

#: 기본으로 보관하는 최근 신호 이력 건수.
DEFAULT_MAX_EVENTS = 50


class PositionSnapshot(BaseModel):
    """현재 오픈 중인 가상(페이퍼) 포지션 한 건의 스냅샷."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    entry_time: int
    entry_price: float
    stop_price: float | None = None
    take_profit_price: float | None = None

    @classmethod
    def from_position(cls, position: PaperPosition) -> PositionSnapshot:
        return cls(
            symbol=position.symbol,
            timeframe=position.timeframe,
            direction=position.direction,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            stop_price=position.stop_price,
            take_profit_price=position.take_profit_price,
        )


class EventRecord(BaseModel):
    """최근 신호/알림 이력 한 건."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    kind: SignalKind
    direction: OrderBlockDirection
    time: int
    price: float
    exit_reason: SignalExitReason | None = None

    @classmethod
    def from_event(cls, event: SignalEvent) -> EventRecord:
        sig = event.signal
        return cls(
            symbol=event.symbol,
            timeframe=event.timeframe,
            kind=sig.kind,
            direction=sig.direction,
            time=sig.time,
            price=sig.price,
            exit_reason=sig.exit_reason,
        )


class RunnerRuntimeState(BaseModel):
    """러너 운영 상태 스냅샷(파일로 직렬화되는 최상위 모델)."""

    #: 마지막으로 상태를 기록한 벽시계 시각(ms). 하트비트/마지막 폴링 시각.
    updated_at: int | None = None
    #: 마지막으로 새 신호 알림이 나온 벽시계 시각(ms). 한 번도 없으면 None.
    last_notification_at: int | None = None
    #: 현재 오픈 중인 페이퍼 포지션.
    open_positions: list[PositionSnapshot] = Field(default_factory=list)
    #: 최근 신호 이력(오래된→최신 순). `DEFAULT_MAX_EVENTS`로 상한.
    recent_events: list[EventRecord] = Field(default_factory=list)


class RuntimeStateStore:
    """러너 운영 상태를 JSON 파일로 영속화한다(단일 작성자 = 러너).

    파일이 없거나 손상됐으면 빈 상태로 시작한다. `record`는 최근 이력을 누적하고
    `max_events`로 상한을 두며, 저장은 원자적 교체로 안전하게 수행한다.
    """

    def __init__(self, path: str | Path, *, max_events: int = DEFAULT_MAX_EVENTS) -> None:
        self._path = Path(path)
        self._max_events = max_events
        self._state = self._read()

    def _read(self) -> RunnerRuntimeState:
        if not self._path.exists():
            return RunnerRuntimeState()
        try:
            raw = self._path.read_text(encoding="utf-8")
            return RunnerRuntimeState.model_validate_json(raw)
        except (OSError, ValidationError, ValueError) as exc:
            _logger.warning("러너 상태 파일을 읽지 못해 빈 상태로 시작: %s", exc)
            return RunnerRuntimeState()

    def load(self) -> RunnerRuntimeState:
        """현재 상태 스냅샷을 반환한다(항상 파일에서 새로 읽어 최신값 보장)."""
        self._state = self._read()
        return self._state

    def record(
        self,
        *,
        now_ms: int,
        open_positions: list[PaperPosition],
        new_events: list[SignalEvent],
    ) -> RunnerRuntimeState:
        """이번 폴링 결과를 반영해 상태를 갱신·저장하고 반환한다.

        - `updated_at`(하트비트)을 `now_ms`로 올린다.
        - `new_events`가 있으면 이력에 누적(상한 초과분은 오래된 것부터 버림)하고
          `last_notification_at`을 갱신한다.
        - 현재 오픈 포지션을 스냅샷으로 통째로 교체한다.
        """
        events = list(self._state.recent_events)
        events.extend(EventRecord.from_event(e) for e in new_events)
        if len(events) > self._max_events:
            events = events[-self._max_events :]

        self._state = RunnerRuntimeState(
            updated_at=now_ms,
            last_notification_at=(now_ms if new_events else self._state.last_notification_at),
            open_positions=[PositionSnapshot.from_position(p) for p in open_positions],
            recent_events=events,
        )
        self._flush()
        return self._state

    def _flush(self) -> None:
        if self._path.parent != Path(""):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = self._state.model_dump_json(indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)
