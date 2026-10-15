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
  몫), 존별 전이/탭 표식, 탐지 창 밖으로 밀려나 엔진이 기억하는 존(WAN-451), 걸려 있던
  주문들의 생애 스칼라(예약 시각·탭 번호·경과 봉 수·유효 기간), 마지막으로 소비한 1분봉
  시각(따라잡기 재생의 시작점).

📌 **옛 스냅샷도 읽힌다** — 칸당 대기 주문 하나(`pending`)·재진입 재무장 존(`occupied_zone`)·
예약 시도 표식(`armed_forming_bar`)은 옛 오더블록 페이퍼(WAN-451에서 없어짐)의 필드다. 모델이
모르는 키는 pydantic 기본값(`extra="ignore"`)대로 무시되므로 옛 JSON이 검증에서 떨어지지
않는다 — 그 주문들은 복원되지 않고 기존 재시작 폐기 경로로 남는다.

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
from strategy.models import OrderBlock

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
    """감시 중인 존 하나의 전이/탭 표식 — 탭 판정의 「직전까지 안/바깥」 맥락.

    탐지로 다시 세워지는 존 자체(`OrderBlock`)는 담지 않는다 — 복원 시 탐지를 다시 돌려 같은
    `ZoneRef`로 맞춰 끼운다(탐지 결과가 정본, 표식은 세션 상태).
    """

    zone: ZoneRef
    was_inside: bool
    skip_recorded_forming_bar: int | None = None
    #: 이번 형성 중 봉에서 이미 탭을 처리한 봉 시각(엔진 `_ZoneWatch.tap_bar`). 이름은 그
    #: 주문이 「그림자 주문」이던 시절의 것이다 — 저장 형식을 바꾸지 않으려고 그대로 둔다.
    shadow_bar: int | None = None
    #: 탐지 창 밖으로 밀려나 엔진이 직접 따라가는 존(WAN-451) — 다시 탐지할 수 없으므로 존 자체를
    #: 담는다. 탐지로 다시 세워지는 존은 None이다(옛 스냅샷도 None — 호환).
    carried_ob: OrderBlock | None = None
    bar_inside: bool = False
    checked_through: int | None = None


class PersistedPendingOrder(BaseModel):
    """대기 지정가 주문의 생애 스칼라 — 지표 상태는 재시딩으로 복원되므로 담지 않는다.

    `limit_valid_bars=None`은 **무기한**이다 — 필드 부재가 아니라 명시적 null로 저장돼
    24봉 기본값과 갈린다(WAN-159 「none ≠ 미지정」 규약과 같은 부류).
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
    #: 걸려 있던 주문 — 탭마다 독립 주문(WAN-451). 이름은 그 주문이 「그림자 주문」이던 시절의
    #: 것이다 — 저장 형식을 바꾸지 않으려고 그대로 둔다. 옛 스냅샷엔 없다(빈 목록).
    shadows: list[PersistedPendingOrder] = Field(default_factory=list)


class EngineStateStore:
    """시리즈별 엔진 스냅샷을 SQLite에 저장/복원한다(단일 작성자 = 러너).

    매 서브스텝 소비 직후 저장한다 — 저장 주기가 성기면 크래시 후 따라잡기 재생이 이미
    집행한 구간을 다시 집행해 장부·페이퍼 거래가 중복된다(저장은 행 하나 교체라 싸다).
    손상·구버전 스냅샷은 조용히 버리고 None을 반환한다(복원 불가 = 기존 재시작 폐기 경로).

    📌 **내용이 안 바뀐 저장은 건너뛴다(WAN-451 ③)** — 「행 하나 교체라 싸다」는 한 번의
    값이고 러너는 그걸 시리즈 ~310개 × (서브스텝 + 폴링 끝)마다 커밋했다(분당 600회+, 대부분
    방금 쓴 것과 같은 내용). 그 쓰기가 WAL 쓰기량의 대부분이었고 2026-10-16 WAL 2.46GB 사고의
    주원인이다. 그래서 시리즈마다 **마지막으로 쓴 스냅샷을 `saved_ms`를 뺀 직렬화로** 기억하고,
    같으면 DB를 건드리지 않는다. `saved_ms`는 저장 때마다 바뀌는 벽시계라 비교에 넣으면
    아무것도 안 건너뛴다.

    * **크래시 안전성은 그대로다** — 건너뛴 저장은 DB에 이미 같은 내용이 있을 때뿐이라 크래시
      시점에 DB가 담은 복원 상태는 매번 썼을 때와 비트 단위로 같다(`saved_ms` 하나만 다르다 —
      그 값은 복원에 쓰이지 않는다). 커서(`last_substep_time`)가 나아가면 내용이 바뀌므로 서브스텝
      저장은 여전히 매 분 쓴다.
    * ⚠️ **그래서 `saved_ms`의 뜻이 「마지막 저장 호출 시각」에서 「내용이 마지막으로 바뀐 시각」
      으로 바뀐다.** 지금 이 열을 읽는 코드는 없다.
    * 기억은 **쓰기가 커밋된 뒤에만** 갱신한다(실패한 쓰기를 「썼다」로 기억하면 다음 같은 내용
      저장이 건너뛰어져 DB가 영영 옛 상태로 남는다). `delete()`는 기억도 지운다(삭제 뒤 같은
      내용을 다시 저장하면 반드시 써야 한다). `load()`는 기억을 채우지 않는다 — 복원 뒤 첫 저장은
      항상 쓴다(안전한 쪽).
    * ⚠️ 전제: 이 테이블의 작성자는 이 인스턴스 하나다(단일 작성자 = 러너). 러너가 도는 중에
      밖에서 행을 지우면(예: 러너를 안 멈추고 `paper-reset.sh`) 기억과 DB가 어긋난다 — 그 스크립트는
      러너 정지를 확인한다.
    """

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        configure_connection(self._conn)
        self._lock = threading.Lock()
        #: (심볼, TF) → 마지막으로 커밋한 스냅샷의 `saved_ms` 제외 직렬화(내용 비교 키).
        self._last_written: dict[tuple[str, str], str] = {}
        #: 실제로 DB에 쓴 횟수 · 내용이 같아 건너뛴 횟수(관측용 — 테스트·진단이 읽는다).
        self.writes = 0
        self.skipped = 0
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def save(self, symbol: str, timeframe: str, snapshot: SeriesStateSnapshot) -> bool:
        """스냅샷을 저장한다 → 실제로 DB에 썼으면 True, 내용이 같아 건너뛰었으면 False."""
        key = (symbol, timeframe)
        content = snapshot.model_dump_json(exclude={"saved_ms"})
        with self._lock:
            if self._last_written.get(key) == content:
                self.skipped += 1
                return False
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO live_engine_state (symbol, timeframe, saved_ms,"
                    " state_json) VALUES (?, ?, ?, ?)",
                    (symbol, timeframe, snapshot.saved_ms, snapshot.model_dump_json()),
                )
            # 커밋이 끝난 뒤에만 기억한다 — 위에서 예외가 나면 여기까지 오지 않는다.
            self._last_written[key] = content
            self.writes += 1
            return True

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

    def series_keys(self) -> list[tuple[str, str]]:
        """스냅샷이 남아 있는 (심볼, TF) — 재시작 때 감시 목록 밖 시리즈도 복원하려고 쓴다."""
        rows = self._conn.execute(
            "SELECT symbol, timeframe FROM live_engine_state ORDER BY symbol, timeframe"
        ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def delete(self, symbol: str, timeframe: str) -> None:
        with self._lock:
            # 기억을 먼저 지운다 — 삭제가 실패해도 다음 저장은 반드시 쓴다(안전한 쪽).
            self._last_written.pop((symbol, timeframe), None)
            with self._conn:
                self._conn.execute(
                    "DELETE FROM live_engine_state WHERE symbol = ? AND timeframe = ?",
                    (symbol, timeframe),
                )
