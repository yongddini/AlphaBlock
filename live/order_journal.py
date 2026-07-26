"""지정가 주문 체결률 실측 장부 (WAN-45 — 이 이슈의 1급 산출물).

페이퍼 러너가 건 지정가 주문의 전 생애(예약 → 체결/만료/무효화/취소)를 SQLite에
누적한다. 목적은 집행이 아니라 **측정**이다: 이 저장소의 모든 백테스트 판정은
`baseline`("닿으면 체결") 낙관 가정 위에 서 있는데(WAN-96/128), 그 가정을 실제 시장에서
확인할 유일한 통로가 이 장부다(틱·호가 수집 WAN-98은 Canceled).

## 기록하는 것

* **주문 생애**: 심볼·TF별 걸린/체결된/만료·취소된 주문 수 → 체결률.
* **예약→체결 소요**(`wait_ms`)와 **체결 관통 폭**(`penetration_bps`): 관통 0 근처의
  체결("스치듯 닿음")은 실거래에서 큐 우선순위 때문에 가장 안 될 부류라(WAN-96),
  그 비중이 곧 낙관 가정의 비용이다. `pen_5bp` 렌즈(관통 5bp 요구)와 나란히 읽는다.
* **가동 구간**(`live_runner_sessions`): 러너가 실제로 살아 있던 시간. 로컬 맥에서
  돌므로(사용자 결정 2026-07-21) 재부팅·노트북 닫기로 구멍이 나는데, 체결률의 분모가
  "러너가 살아 있던 시간"임을 표에 명시할 수 있어야 한다 — 이게 없으면 "체결률 60%"가
  진짜 60%인지 러너가 40% 시간 죽어 있던 건지 구분이 안 된다.
* **재시작 폐기**(`discarded_restart`): 러너가 죽었다 살아나면 이전 세션의 대기 주문은
  **버리고 새로 건다**(복원하지 않는다 — 복원하려면 죽어 있던 구간의 가격 경로를
  재구성해야 하는데 그 구간 데이터가 빈 것이 문제의 본질이라 지어내지 않는다). 버린
  주문은 별도 상태로 남겨 체결률 통계를 오염시키지 않는다.
* **체결의 하류 처분**(`entry_status`·`entry_reject_reason`, WAN-194): 체결이 실제로
  페이퍼 포지션으로 **열렸는지**(`entered`) 아니면 집행 계층이 **거부했는지**(`rejected`,
  사유 포함). 아래 문단이 이 열이 왜 1급 기록인지 설명한다.

## 체결 ≠ 진입 (WAN-194 — 이 열이 없어서 생긴 사고)

지정가 체결과 페이퍼 진입은 **다른 사건**이다. 체결은 이 장부가 남기지만 그 체결이
포지션이 되는지는 집행 계층(`ExecutionEngine.on_entry`)이 정한다 — 손절 거리가 사이징
가드(`min_stop_distance_fraction`, 0.3% WAN-79)보다 가깝거나, 명목 상한·리스크 한도에
걸리거나, 이미 그 시리즈에 포지션이 있으면 **거부**된다. 백테스트도 같은 자로 거부하므로
(`_to_trade`의 `qty <= 0` → 후보 폐기) 거부 자체는 버그가 아니다.

버그였던 것은 **그 거부가 아무 곳에도 안 남았다는 것**이다. WAN-194 전까지 거부된 체결은
INFO 로그 한 줄이 전부였고 DB에는 `status='filled'`만 남아 포지션·거래가 없었다. 그래서
운영자가 장부를 보면 "체결은 됐는데 포지션이 없다"가 **DB 손상·기록 유실과 구분되지
않았다**(실제로 사용자 질문 → 손상 의심 → WAN-194로 왔다). 두 열이 그 구분을 만든다:

* `entry_status='rejected'` + 사유 → **정상 동작**(가드가 걸렀다). 답이 장부에 있다.
* `entry_status IS NULL` 인데 `status='filled'` → **처분 미기록**. 러너가 체결 기록과
  포지션 쓰기 **사이에서 죽었을** 때 나오는 모양이다(두 쓰기가 원자적이지 않다) — 즉 이
  상태가 곧 "진짜 유실" 신호다. `orphan_fills()`가 그 행만 골라 준다.

⚠️ 옛 DB의 `filled` 행은 열이 없던 시절 기록이라 전부 `NULL`이다 — 그 행들은 "유실"이
아니라 **판별 불가**다(도입 이후만 보려면 `orphan_fills(since_ms=...)`).

수집 DB(`data/ohlcv.db`)를 같이 쓰며 `data.sqlite_util.configure_connection`(WAL +
busy_timeout)으로 동시 프로세스(수집기·대시보드)와의 락 경합을 견딘다.

요약 표는 `python -m live.fill_report`(같은 패키지 `fill_report` 모듈)가 찍는다.
"""

from __future__ import annotations

import sqlite3
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path

from data.integrity import OrphanFill
from data.sqlite_util import configure_connection
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder

#: 처분 미기록 체결의 행 모양은 `data.integrity`가 소유한다(레이어 규칙: `data`는 `live`를
#: 임포트할 수 없어 점검 도구와 장부가 공유하려면 낮은 쪽에 둬야 한다). 여기서 재수출해
#: 장부 사용자가 `data.integrity`를 직접 알 필요는 없게 한다.
__all__ = [
    "ENTRY_STATUS_ENTERED",
    "ENTRY_STATUS_REJECTED",
    "MARGINAL_FILL_BPS",
    "STATUS_DISCARDED_RESTART",
    "OrderJournal",
    "OrphanFill",
    "SeriesFillStats",
    "SessionSpan",
]

#: "스치듯 닿은 체결" 판정 문턱(bp). `pen_5bp` 민감도 렌즈(WAN-96)와 같은 5bp를 써서
#: 백테스트 표와 같은 자로 읽는다 — 이 값 미만 관통의 체결은 실거래에서 큐 우선순위
#: 때문에 가장 안 될 부류다.
MARGINAL_FILL_BPS = 5.0

#: 이전 세션이 남긴 대기 주문의 폐기 상태(재시작 정책 — 모듈 독스트링).
STATUS_DISCARDED_RESTART = "discarded_restart"

#: 체결이 페이퍼 포지션으로 열렸다(`entry_status`, WAN-194).
ENTRY_STATUS_ENTERED = "entered"

#: 체결이 집행 계층에서 거부됐다 — 사유는 `entry_reject_reason`(WAN-194).
ENTRY_STATUS_REJECTED = "rejected"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_limit_orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           INTEGER NOT NULL,
    symbol               TEXT    NOT NULL,
    timeframe            TEXT    NOT NULL,
    direction            TEXT    NOT NULL,
    zone_start_time      INTEGER,
    zone_confirmed_time  INTEGER,
    tap_index            INTEGER NOT NULL DEFAULT 0,
    placed_ms            INTEGER NOT NULL,
    status               TEXT    NOT NULL,
    terminal_ms          INTEGER,
    first_rested_ms      INTEGER,
    last_limit_price     REAL,
    fill_ms              INTEGER,
    fill_price           REAL,
    fill_rsi             REAL,
    fill_penetration_bps REAL,
    stop_price           REAL,
    take_profit_price    REAL,
    wait_ms              INTEGER,
    entry_status         TEXT,
    entry_reject_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_limit_orders_series
    ON live_limit_orders (symbol, timeframe);
CREATE TABLE IF NOT EXISTS live_runner_sessions (
    session_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ms  INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class SeriesFillStats:
    """한 (symbol, timeframe) 시리즈의 체결률 요약."""

    symbol: str
    timeframe: str
    placed: int
    """유효 표본(재시작 폐기 제외, 아직 대기 중 포함)."""
    pending: int
    filled: int
    cancelled_expired: int
    cancelled_invalidated: int
    cancelled_condition_failed: int
    discarded_restart: int
    median_wait_ms: float | None
    """체결 건의 예약→체결 소요 중앙값(ms)."""
    marginal_fills: int
    """관통 < `MARGINAL_FILL_BPS`(5bp)인 체결 수 — `pen_5bp` 렌즈가 부정할 체결."""
    entered: int = 0
    """체결 중 페이퍼 포지션이 실제로 열린 수(WAN-194)."""
    entry_rejected: int = 0
    """체결 중 집행 계층이 거부한 수(사이징 가드·리스크 한도 등, WAN-194)."""
    entry_unrecorded: int = 0
    """체결 중 처분이 안 남은 수 — 열 도입 전 기록이거나 쓰기 사이의 유실(WAN-194)."""

    @property
    def entry_rate(self) -> float | None:
        """체결 → 진입 전환율 = `entered` / (`entered` + `entry_rejected`).

        ⚠️ **체결률과 곱해 읽는 값이다.** 체결률의 분자(체결)는 이 전환율만큼만 거래가
        된다 — 백테스트도 같은 가드로 후보를 버리므로 파리티가 깨진 건 아니지만, "체결률
        81%"를 거래 성립률로 오독하지 않으려면 두 값을 함께 봐야 한다. 처분 미기록
        (`entry_unrecorded`)은 결과를 모르므로 분모에서 뺀다.
        """
        decided = self.entered + self.entry_rejected
        return self.entered / decided if decided else None

    @property
    def resolved(self) -> int:
        """결말이 난 표본 수(체결 + 취소, 대기·폐기 제외) — 체결률의 분모."""
        return (
            self.filled
            + self.cancelled_expired
            + self.cancelled_invalidated
            + self.cancelled_condition_failed
        )

    @property
    def fill_rate(self) -> float | None:
        """체결률 = filled / resolved. 결말 표본이 없으면 None.

        아직 대기 중인 주문은 분모에 넣지 않는다 — 결과가 정해지지 않은 표본을 미체결로
        세면 체결률이 러너를 켠 직후마다 아래로 왜곡된다.
        """
        return self.filled / self.resolved if self.resolved else None

    @property
    def marginal_fill_share(self) -> float | None:
        """체결 중 "스치듯 닿은"(관통 < 5bp) 비중. 체결이 없으면 None."""
        return self.marginal_fills / self.filled if self.filled else None


@dataclass(frozen=True)
class SessionSpan:
    """러너 가동 구간 하나(시작 ~ 마지막 하트비트)."""

    session_id: int
    started_ms: int
    last_seen_ms: int


class OrderJournal:
    """지정가 주문 생애·러너 가동 구간을 SQLite에 기록하는 장부(단일 작성자 = 러너)."""

    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        if path.parent != Path(""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        configure_connection(self._conn)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """옛 DB에 나중에 생긴 열을 덧붙인다(호출부가 이미 락·트랜잭션 안이다).

        `CREATE TABLE IF NOT EXISTS`는 **이미 있는 테이블의 열을 늘려 주지 않는다** —
        서버 DB는 WAN-45 시절 스키마로 만들어졌으므로 마이그레이션 없이 새 열을 쓰면
        `OperationalError`로 러너가 죽는다. 존재 여부를 보고 `ALTER TABLE`을 건다
        (SQLite는 `ADD COLUMN`이 O(1)이고 기존 행은 NULL로 채운다).
        """
        existing = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(live_limit_orders)")
        }
        for column in ("entry_status", "entry_reject_reason"):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE live_limit_orders ADD COLUMN {column} TEXT")

    def close(self) -> None:
        self._conn.close()

    # -- 러너 가동(uptime) ---------------------------------------------------

    def start_session(self, *, now_ms: int) -> int:
        """새 가동 세션을 연다. 세션 id를 반환한다."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO live_runner_sessions (started_ms, last_seen_ms) VALUES (?, ?)",
                (now_ms, now_ms),
            )
        session_id = cur.lastrowid
        assert session_id is not None
        return session_id

    def heartbeat(self, session_id: int, *, now_ms: int) -> None:
        """세션 생존 시각을 갱신한다(가동 구간의 오른쪽 끝)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_runner_sessions SET last_seen_ms = ? WHERE session_id = ?",
                (now_ms, session_id),
            )

    def sessions(self) -> list[SessionSpan]:
        """모든 가동 구간(시작 순). 구간 사이의 틈이 곧 중단(다운타임)이다."""
        rows = self._conn.execute(
            "SELECT session_id, started_ms, last_seen_ms FROM live_runner_sessions "
            "ORDER BY started_ms"
        ).fetchall()
        return [SessionSpan(int(r[0]), int(r[1]), int(r[2])) for r in rows]

    # -- 주문 생애 -----------------------------------------------------------

    def record_placed(
        self,
        order: PendingLimitOrder,
        *,
        session_id: int,
        zone_start_time: int | None,
        zone_confirmed_time: int | None,
    ) -> int:
        """주문 예약을 기록하고 장부 행 id를 반환한다(주문의 `journal_id`로 쓴다)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
                " zone_start_time, zone_confirmed_time, tap_index, placed_ms, status,"
                " first_rested_ms, last_limit_price)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    order.symbol,
                    order.timeframe,
                    order.direction.value,
                    zone_start_time,
                    zone_confirmed_time,
                    order.tap_index,
                    order.placed_ms,
                    LimitOrderStatus.PENDING.value,
                    order.first_rested_ms,
                    order.last_limit_price,
                ),
            )
        row_id = cur.lastrowid
        assert row_id is not None
        return row_id

    def record_progress(self, journal_id: int, order: PendingLimitOrder) -> None:
        """대기 중 주문의 진행 상태(첫 걸림 시각·마지막 지정가)를 갱신한다."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET first_rested_ms = ?, last_limit_price = ?"
                " WHERE id = ?",
                (order.first_rested_ms, order.last_limit_price, journal_id),
            )

    def record_filled(self, journal_id: int, fill: LimitFill) -> None:
        """체결을 기록한다 — 체결가·RSI·관통 폭·대기 시간이 실측의 본체다."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ?, fill_ms = ?,"
                " fill_price = ?, fill_rsi = ?, fill_penetration_bps = ?, stop_price = ?,"
                " take_profit_price = ?, wait_ms = ?, last_limit_price = ? WHERE id = ?",
                (
                    LimitOrderStatus.FILLED.value,
                    fill.time,
                    fill.time,
                    fill.price,
                    fill.rsi,
                    fill.penetration_bps,
                    fill.stop_price,
                    fill.take_profit_price,
                    fill.waited_ms,
                    fill.price,
                    journal_id,
                ),
            )

    def record_entry_result(self, journal_id: int, *, entered: bool, reason: str = "") -> None:
        """체결의 **하류 처분**을 기록한다 — 포지션이 열렸는지, 거부면 사유(WAN-194).

        체결(`record_filled`) 직후에 부른다. 이 호출이 없으면 그 행은 `entry_status
        IS NULL`로 남아 `orphan_fills()`에 잡힌다 — 그게 "체결과 포지션 쓰기 사이에서
        죽었다"의 유일한 신호이므로, **거부일 때도 반드시 기록한다**(조용한 성공/실패
        구분 불가가 이 이슈의 사고 원인이었다).
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET entry_status = ?, entry_reject_reason = ?"
                " WHERE id = ?",
                (
                    ENTRY_STATUS_ENTERED if entered else ENTRY_STATUS_REJECTED,
                    None if entered else (reason or "사유 미기록"),
                    journal_id,
                ),
            )

    def orphan_fills(self, *, since_ms: int | None = None) -> list[OrphanFill]:
        """처분이 기록되지 않은 체결(= `filled` + `entry_status IS NULL`)을 모은다.

        `since_ms`를 주면 그 이후 체결만 본다 — 열 도입 전 기록은 전부 NULL이라 같은
        모양으로 잡히므로, "진짜 유실"을 보려면 도입 시점 이후로 잘라야 한다.
        """
        sql = (
            "SELECT id, symbol, timeframe, fill_ms, fill_price, stop_price"
            " FROM live_limit_orders WHERE status = ? AND entry_status IS NULL"
        )
        args: list[object] = [LimitOrderStatus.FILLED.value]
        if since_ms is not None:
            sql += " AND fill_ms >= ?"
            args.append(since_ms)
        rows = self._conn.execute(sql + " ORDER BY fill_ms", args).fetchall()
        return [
            OrphanFill(
                journal_id=int(r[0]),
                symbol=str(r[1]),
                timeframe=str(r[2]),
                fill_ms=None if r[3] is None else int(r[3]),
                fill_price=None if r[4] is None else float(r[4]),
                stop_price=None if r[5] is None else float(r[5]),
            )
            for r in rows
        ]

    def record_cancelled(self, journal_id: int, status: LimitOrderStatus, *, now_ms: int) -> None:
        """취소(만료·무효화·조건 미충족)를 기록한다."""
        if not status.is_terminal or status is LimitOrderStatus.FILLED:
            raise ValueError(f"취소 상태가 아닙니다: {status}")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ? WHERE id = ?",
                (status.value, now_ms, journal_id),
            )

    def record_discarded(self, journal_id: int, *, now_ms: int) -> None:
        """개별 주문을 측정 무효로 폐기한다(1분봉 공백 등 — 재시작 폐기와 같은 상태).

        일반 취소와 달리 체결률 분모에서 빠진다 — 러너/데이터가 죽어 있던 구간의 결과를
        지어내지 않기 위해서다.
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ? WHERE id = ?",
                (STATUS_DISCARDED_RESTART, now_ms, journal_id),
            )

    def discard_stale_pending(self, *, now_ms: int) -> int:
        """이전 세션이 남긴 대기 주문을 재시작 폐기로 마감한다. 폐기 건수를 반환.

        러너 재시작 시 대기 주문은 복원하지 않고 버린다(모듈 독스트링의 재시작 정책).
        일반 취소와 다른 상태(`discarded_restart`)로 남겨 체결률 분모에서 빠진다.
        """
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE live_limit_orders SET status = ?, terminal_ms = ? WHERE status = ?",
                (STATUS_DISCARDED_RESTART, now_ms, LimitOrderStatus.PENDING.value),
            )
        return cur.rowcount

    # -- 요약 ----------------------------------------------------------------

    def fill_stats(self) -> list[SeriesFillStats]:
        """심볼·TF별 체결률 요약(백테스트 `baseline` 가정과 나란히 놓는 표의 원자료)."""
        rows = self._conn.execute(
            "SELECT symbol, timeframe, status, wait_ms, fill_penetration_bps, entry_status"
            " FROM live_limit_orders ORDER BY symbol, timeframe"
        ).fetchall()
        by_series: dict[
            tuple[str, str], list[tuple[str, int | None, float | None, str | None]]
        ] = {}
        for symbol, timeframe, status, wait_ms, penetration, entry_status in rows:
            by_series.setdefault((str(symbol), str(timeframe)), []).append(
                (str(status), wait_ms, penetration, entry_status)
            )

        stats: list[SeriesFillStats] = []
        for (symbol, timeframe), entries in sorted(by_series.items()):
            counts: dict[str, int] = {}
            waits: list[int] = []
            marginal = 0
            entered = rejected = unrecorded = 0
            for status, wait_ms, penetration, entry_status in entries:
                counts[status] = counts.get(status, 0) + 1
                if status == LimitOrderStatus.FILLED.value:
                    if wait_ms is not None:
                        waits.append(int(wait_ms))
                    if penetration is not None and penetration < MARGINAL_FILL_BPS:
                        marginal += 1
                    if entry_status == ENTRY_STATUS_ENTERED:
                        entered += 1
                    elif entry_status == ENTRY_STATUS_REJECTED:
                        rejected += 1
                    else:
                        unrecorded += 1
            discarded = counts.get(STATUS_DISCARDED_RESTART, 0)
            stats.append(
                SeriesFillStats(
                    symbol=symbol,
                    timeframe=timeframe,
                    placed=len(entries) - discarded,
                    pending=counts.get(LimitOrderStatus.PENDING.value, 0),
                    filled=counts.get(LimitOrderStatus.FILLED.value, 0),
                    cancelled_expired=counts.get(LimitOrderStatus.CANCELLED_EXPIRED.value, 0),
                    cancelled_invalidated=counts.get(
                        LimitOrderStatus.CANCELLED_INVALIDATED.value, 0
                    ),
                    cancelled_condition_failed=counts.get(
                        LimitOrderStatus.CANCELLED_CONDITION_FAILED.value, 0
                    ),
                    discarded_restart=discarded,
                    median_wait_ms=statistics.median(waits) if waits else None,
                    marginal_fills=marginal,
                    entered=entered,
                    entry_rejected=rejected,
                    entry_unrecorded=unrecorded,
                )
            )
        return stats
