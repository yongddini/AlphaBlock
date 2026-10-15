"""팔 신호 장부 — 몰림 규칙이 세는 「팔 후보의 가상 체결」 (WAN-451 ③ · 정본 WAN-436/438).

몰림 규칙은 진입 시각 t에서 `[t − 24h, t)` 안에 있는 **다른 팔 후보의 가상 체결** 수를 센다 —
우리가 실제로 들어갔는지와 무관하다. 백테스트에서 그 모집단(`wan436.build_entries` →
`wan442.apply_rotation` 뒤 `signal_counts`)은:

* 오더블록 **첫 탭**마다 독립으로 시뮬레이션한 지정가가 **체결**된 것(롱 · 재진입 아님)
* 체결 순간 **손절폭 ≥ 4%**(2배 전 폭)이고 **직전 확정봉 %K < 25**
* 체결 시각의 **주간 목록 안** 종목

라이브는 엔진의 탭별 독립 주문(`ZoneLimitLiveEngine`)이 체결될 때마다 이 장부에 한
행을 남기고, 위 셋을 통과했는지를 `counted`로 적는다(통과 못 한 것도 사유와 함께 남긴다 — 나중에
「왜 안 셌나」를 볼 수 있게). 시각은 그 1분봉의 `open_time`이다(백테스트 후보의 `entry_time`과 같은
자). 재시작 따라잡기가 같은 체결을 다시 내도 한 행만 남는다(기본키).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from data.sqlite_util import configure_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS arm_signals (
    symbol         TEXT    NOT NULL,
    timeframe      TEXT    NOT NULL,
    zone_start     INTEGER NOT NULL,
    zone_confirmed INTEGER NOT NULL,
    fill_ms        INTEGER NOT NULL,
    price          REAL    NOT NULL,
    ob_stop        REAL    NOT NULL,
    counted        INTEGER NOT NULL,
    reason_code    TEXT    NOT NULL,
    PRIMARY KEY (symbol, timeframe, zone_start, zone_confirmed, fill_ms)
);
CREATE INDEX IF NOT EXISTS arm_signals_fill ON arm_signals (fill_ms);
"""


@dataclass(frozen=True)
class ArmSignal:
    symbol: str
    timeframe: str
    zone_start: int
    zone_confirmed: int
    fill_ms: int
    price: float
    ob_stop: float
    counted: bool
    reason_code: str = ""
    """세지 않은 이유(`arm_rotation`·`arm_stop_floor`·`arm_stoch`…) — 셌으면 빈 문자열."""


class ArmSignalStore:
    """`arm_signals` SQLite 래퍼(시세·장부와 같은 DB 파일 · 단일 작성자 = 러너)."""

    def __init__(self, db_path: str | Path) -> None:
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        configure_connection(self._conn)
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record(self, signal: ArmSignal) -> bool:
        """한 행을 남긴다 — 같은 (칸, 존, 시각)이 이미 있으면 무시하고 False(재생 중복 방지)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO arm_signals (symbol, timeframe, zone_start,"
                " zone_confirmed, fill_ms, price, ob_stop, counted, reason_code)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    signal.symbol,
                    signal.timeframe,
                    signal.zone_start,
                    signal.zone_confirmed,
                    signal.fill_ms,
                    signal.price,
                    signal.ob_stop,
                    int(signal.counted),
                    signal.reason_code,
                ),
            )
        return cur.rowcount > 0

    def count(
        self,
        start_ms: int,
        end_ms: int,
        *,
        exclude: tuple[str, str, int, int] | None = None,
    ) -> int:
        """`[start_ms, end_ms)` 안에서 **센** 신호 수 — `exclude`(칸·존)는 뺀다.

        진입하려는 그 탭의 그림자(쌍둥이)는 「다른 신호」가 아니다. 백테스트에서는 같은 후보라
        같은 ms라서 빠지는데, 라이브는 진짜 주문이 틱으로 먼저 체결되면 시각이 갈릴 수 있어
        존으로 뺀다.
        """
        sql = "SELECT COUNT(*) FROM arm_signals WHERE counted = 1 AND fill_ms >= ? AND fill_ms < ?"
        args: list[object] = [start_ms, end_ms]
        if exclude is not None:
            sql += (
                " AND NOT (symbol = ? AND timeframe = ? AND zone_start = ? AND zone_confirmed = ?)"
            )
            args += list(exclude)
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        return int(row[0])

    def rows(self, start_ms: int, end_ms: int) -> list[ArmSignal]:
        with self._lock:
            got = self._conn.execute(
                "SELECT symbol, timeframe, zone_start, zone_confirmed, fill_ms, price, ob_stop,"
                " counted, reason_code FROM arm_signals WHERE fill_ms >= ? AND fill_ms < ?"
                " ORDER BY fill_ms",
                (start_ms, end_ms),
            ).fetchall()
        return [
            ArmSignal(
                symbol=str(r[0]),
                timeframe=str(r[1]),
                zone_start=int(r[2]),
                zone_confirmed=int(r[3]),
                fill_ms=int(r[4]),
                price=float(r[5]),
                ob_stop=float(r[6]),
                counted=bool(r[7]),
                reason_code=str(r[8]),
            )
            for r in got
        ]
