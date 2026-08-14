"""존-지정가 러너 엔진 상태의 재시작 영속화 (WAN-306).

러너는 재시작할 때마다 존의 과거 기억을 잃고 「직전 확정봉 한 장」 요약으로 복귀했다 —
대기 주문은 전부 폐기(`discarded_restart`), 전이(안/바깥) 맥락·형성 중 봉의 누적 고저·
탭 시도 기록·재진입 재무장 상태는 소멸. 백테스트는 끊김 없이 전체 경로를 기억하므로
재시작이 잦은 날은 같은 존의 판단이 라이브↔백테에서 갈렸다(2026-08-13 실사례: 재시작
직후 즉시 체결 — WAN-305 픽스처). 사용자 결정: 페이퍼는 재시작 여부와 무관하게 **끊긴
적 없는 것처럼** 동작해야 한다.

이 모듈은 그 「기억」을 담는 그릇이다 — (symbol, timeframe) 시리즈마다 엔진이 재시작을
넘어 복원해야 하는 상태를 스냅샷 모델로 정의하고, SQLite(`live_engine_state` 테이블,
장부와 같은 DB)에 저장/복원한다. 스냅샷의 원칙:

* **재파생 가능한 것은 담지 않는다** — 존 대장·지표 시딩(`seed_closes`)·ATR은 복원 시
  저장소의 확정봉 창을 `on_htf_bars`에 다시 먹여 재구성한다(같은 입력 = 같은 상태).
  대기 주문의 RSI·밴드 상태도 마찬가지다: 확정봉 종가에서 재시딩하면 예약 시점 시딩 +
  경계 커밋의 결과와 같다(경계 커밋의 표본이 곧 확정봉 종가다).
* **재파생 불가능한 것만 담는다** — 형성 중 봉의 누적 고저(재시작 전에 소비한 서브스텝의
  몫), 존별 전이/시도 표식, 대기 주문의 생애 스칼라(예약 시각·탭 번호·경과 봉 수·유효
  기간 — 특히 재진입 재무장의 무기한 `None`), 오픈 포지션의 존(`occupied_zone`, 익절 후
  재진입이 읽는다), 마지막으로 소비한 1분봉 시각(따라잡기 재생의 시작점).

운영 상태 파일(`live.runtime_state`, WAN-30)과는 목적이 다르다 — 그쪽은 사람/대시보드가
읽는 표시용 요약이고, 이쪽은 엔진이 자기 상태를 도로 삼키는 복원용 원본이다. 두 벌을
합치면 표시 요구가 복원 계약을 흔든다(분리 유지).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from data.sqlite_util import configure_connection

_logger = logging.getLogger(__name__)

#: 존의 안정적 식별자 — `live.limit_engine.ZoneId`와 같은 (방향, start_time,
#: confirmed_time)이다(JSON 왕복을 위해 튜플 대신 리스트로 저장해도 pydantic이 복원한다).
ZoneRef = tuple[str, int, int]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_engine_state (
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    saved_ms   INTEGER NOT NULL,
    state_json TEXT    NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);
"""


class PersistedZoneWatch(BaseModel):
    """감시 중인 존 하나의 전이/시도 표식 — 탭 판정의 「직전까지 안/바깥」 맥락.

    존 자체(`OrderBlock`)는 담지 않는다 — 복원 시 탐지를 다시 돌려 같은 `ZoneRef`로
    맞춰 끼운다(탐지 결과가 정본, 표식은 세션 상태).
    """

    zone: ZoneRef
    was_inside: bool
    armed_forming_bar: int | None = None
    skip_recorded_forming_bar: int | None = None


class PersistedPendingOrder(BaseModel):
    """대기 지정가 주문의 생애 스칼라 — 지표 상태는 재시딩으로 복원되므로 담지 않는다.

    `limit_valid_bars=None`은 **무기한**(재진입 재무장, WAN-274)이다 — 필드 부재가 아니라
    명시적 null로 저장돼 24봉 기본값과 갈린다(WAN-159 「none ≠ 미지정」 규약과 같은 부류).
    """

    journal_id: int | None
    zone: ZoneRef
    tap_index: int
    placed_ms: int
    limit_valid_bars: int | None
    bars_elapsed: int
    first_rested_ms: int | None
    last_limit_price: float | None


class SeriesStateSnapshot(BaseModel):
    """한 (symbol, timeframe) 시리즈의 복원용 스냅샷(엔진 + 러너 커서)."""

    saved_ms: int
    #: 마지막으로 소비한 확정 1분봉의 open_time — 따라잡기 재생은 이 다음 봉부터다.
    last_substep_time: int
    #: 마지막으로 `on_htf_bars`에 먹인 확정 상위TF 봉의 open_time — 복원 시 이 시각까지의
    #: 창을 다시 먹여 존 대장·시딩을 스냅샷 시점으로 되돌린다.
    last_htf_time: int
    forming_bar: int | None
    forming_low: float | None
    forming_high: float | None
    running_close: float | None
    zones: list[PersistedZoneWatch] = Field(default_factory=list)
    pending: PersistedPendingOrder | None = None
    #: 오픈 포지션이 어느 존의 것인지 — 익절 후 재진입(WAN-274)이 재무장할 존.
    occupied_zone: ZoneRef | None = None


class EngineStateStore:
    """시리즈별 엔진 스냅샷을 SQLite에 저장/복원한다(단일 작성자 = 러너).

    매 서브스텝 소비 직후 저장한다 — 저장 주기가 성기면 크래시 후 따라잡기 재생이 이미
    집행한 구간을 다시 집행해 장부·페이퍼 거래가 중복된다(저장은 행 하나 교체라 싸다).
    손상·구버전 스냅샷은 조용히 버리고 None을 반환한다(복원 불가 = 기존 재시작 폐기 경로).
    """

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        configure_connection(self._conn)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def save(self, symbol: str, timeframe: str, snapshot: SeriesStateSnapshot) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO live_engine_state (symbol, timeframe, saved_ms,"
                " state_json) VALUES (?, ?, ?, ?)",
                (symbol, timeframe, snapshot.saved_ms, snapshot.model_dump_json()),
            )

    def load(self, symbol: str, timeframe: str) -> SeriesStateSnapshot | None:
        row = self._conn.execute(
            "SELECT state_json FROM live_engine_state WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        ).fetchone()
        if row is None:
            return None
        try:
            return SeriesStateSnapshot.model_validate_json(str(row[0]))
        except ValidationError as exc:
            _logger.warning(
                "%s %s: 엔진 상태 스냅샷을 읽지 못해 무시합니다: %s", symbol, timeframe, exc
            )
            return None

    def delete(self, symbol: str, timeframe: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM live_engine_state WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            )
