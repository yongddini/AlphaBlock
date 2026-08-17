"""DB 무결성·위생 점검 (WAN-194 §2·§4·§5).

WAN-194는 서버 `data/ohlcv.db`에서 두 가지가 겹쳐 보이는 사고로 시작했다: (1) 체결은
장부에 남았는데 포지션·거래가 없었고, (2) 같은 DB에 SQLite `.recover` 산출 테이블
`lost_and_found`(283만 행)이 얹혀 있었다. 둘은 **다른 문제**였는데(전자는 처분 미기록,
WAN-194 §3이 닫았다) 진단 도구가 없어 구분에 반나절이 걸렸다.

이 모듈은 그 진단을 **한 번의 명령**으로 만든다(`alphablock doctor`). 서버에서 돌리는
것이 전제이고, 읽기 전용이 기본이다 — 파괴적 정리는 명시적 옵트인이다.

## 보는 것

* **`PRAGMA quick_check`** — 페이지 수준 손상. 전수 `integrity_check`는 수 GB에서 수십
  분이라 기본은 quick이다(인덱스 정합성까지 보려면 `integrity_check`를 손으로).
* **테이블 인구조사** — 앱 테이블 행 수. WAN-194에서 결정적이었던 자료다: `backtest_*`가
  성한 채 `paper_trades`·`open_positions`만 0이면 **광범위 유실이 아니라 배선 문제**라는
  뜻이었다(PM 진단 갱신). 이 표가 그 판단을 자동으로 준다.
  ⚠️ **장부는 성격이 둘이고 종료 코드는 한쪽만 본다(WAN-321)** — 누적
  (`CUMULATIVE_LEDGER_TABLES`)이 비면 이상이지만 현재 상태(`STATE_LEDGER_TABLES` =
  `open_positions`)가 비는 것은 **정상**이다(포지션은 닫힌다). 둘을 묶어 두면 싼 판이 도는
  매시간 거짓 경보가 나 진짜 이상이 상시 빨간불에 묻힌다.
* **복구 산출물** — `lost_and_found`처럼 `.recover`가 남긴 테이블(앱 스키마 아님). 있으면
  "이 DB는 한 번 복구됐다"는 증거이고, 행 수만큼 공간을 먹는다.
* **처분 미기록 체결** — `live_limit_orders`의 `filled` 중 `entry_status IS NULL`
  (WAN-194 §3). 이것이 "진짜 유실"의 유일한 신호다.
* **공간·저널 상태** — 페이지/프리리스트(회수 가능 공간)·WAL 크기·디스크 여유. 손상
  벡터 후보(디스크 꽉 참·WAL 비대·쓰기 도중 강제종료)를 숫자로 남긴다(§5).

## 하지 않는 것 (의도적)

* **자동 `VACUUM` 없음.** 3.8GB DB의 VACUUM은 같은 크기의 임시 파일을 만들고 **DB를
  독점 락**한다 — 수집기·러너가 붙어 있는 서버에서 자동으로 돌 일이 아니다. 회수 가능
  공간만 보고하고 명령은 사람이 고른다.
* **자동 복구 없음.** `.recover`를 이 코드가 돌리면 WAN-194가 겪은 "누가 복구했는지
  모르는 DB"를 저장소가 스스로 만들게 된다. 복구는 사람이 의도적으로만 한다.

## WAN-195가 더한 것 — 버리기 전에 안을 본다

WAN-194는 `lost_and_found`를 **행 수만** 셌다. 그 숫자(283만)만 보면 통째로 버리는 판단이
자연스러운데, 실측하니 그 안에 **살아 있는 `ohlcv`에 없는 5m 캔들 145만 행**이 들어 있었다
— `--drop-recovery-artifacts`가 유일본을 말없이 지웠을 것이다(드롭은 되돌릴 수 없다).

* **`census_recovery_artifacts()`** — 산출물 안을 타임프레임별로 분해하고 살아 있는 테이블과
  대조해 「유일본」과 「중복」을 가른다. 리포트가 드롭을 안내하기 **전에** 이 표를 낸다.
* **`could_contain(arity)`** — `.recover`는 가장 넓은 고아 행에 맞춰 `c0..cN`을 만들므로,
  그보다 열이 많은 테이블은 **구조적으로 담길 수 없다**. 17열 `paper_trades`가 그 경우라
  "찾아봤는데 없다"가 아니라 **"있을 수 없다"**로 말한다(훨씬 강한 진술이다).
* **`salvage_ohlcv()`** — 갇힌 캔들을 되돌린다. **기존 행은 절대 덮어쓰지 않는다.**
* **드롭 가드** — 유일본이 남아 있으면 `SalvageableRowsPresent`로 거부한다(`force`로 무시).
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from data.sqlite_util import configure_connection

#: SQLite `.recover`가 고아 페이지를 쏟아붓는 산출 테이블(앱 스키마가 아니다).
#: 존재 자체가 "이 DB는 복구된 것"이라는 증거다(WAN-194).
RECOVERY_ARTIFACT_TABLES = frozenset({"lost_and_found"})

#: `ohlcv` 테이블의 열 수. 고아 행이 캔들인지 가르는 유일한 실마리다(WAN-195) —
#: `.recover`는 행이 **어느 테이블 것인지**를 잃어버리고 필드 개수와 값만 남긴다.
_OHLCV_ARITY = 9

#: 페이퍼 운영 장부 중 **누적**되는 것 — 한 번 쓰면 지워지지 않으므로 0행이면 이상이다
#: (WAN-194가 겪은 사고의 모양). **종료 코드에 반영된다.**
CUMULATIVE_LEDGER_TABLES: tuple[str, ...] = (
    "live_limit_orders",
    "live_runner_sessions",
    "paper_trades",
)

#: 페이퍼 운영 장부 중 **현재 상태**를 담는 것 — 포지션은 닫히므로 **0행이 정상이다**
#: (WAN-321). 리포트에는 계속 찍되 **종료 코드에는 반영하지 않는다.**
#:
#: WAN-194는 두 성격을 한 규칙으로 묶어 「장부가 비면 경고」라고만 했다. 그 규칙이 잡으려던
#: 사고는 「체결은 `filled`인데 포지션·거래가 없다」였는데, `open_positions`가 0인 것은 그
#: 사고의 신호가 아니라 **그냥 지금 열린 포지션이 없다**는 뜻이라 싼 판이 도는 매시간
#: 거짓 경보를 냈다(WAN-321 §1). `systemctl --failed`가 상시 빨간 상태면 진짜 이상과
#: 구분되지 않으므로 — 「정상이 실패로 보임」은 이 저장소가 반복해서 데인 「실패가 성공과
#: 같은 모양」(WAN-194)의 거울상이다 — 성격을 갈랐다.
#:
#: ⚠️ **점검 항목을 줄인 게 아니다.** WAN-194의 사고는 두 겹으로 여전히 잡힌다:
#: (1) 누적 장부(`paper_trades`)가 0이면 여기서 걸리고, (2) 「`filled` + 처분 NULL」은
#: `orphan_fills`가 **더 정확히** 잡는다(WAN-194가 `entry_status` 열을 넣은 이유).
STATE_LEDGER_TABLES: tuple[str, ...] = ("open_positions",)

#: 두 성격을 합친 전체 장부 목록(인구조사 표기용). 분류가 빠진 장부가 생기지 않도록
#: **두 집합에서 파생**한다 — 새 장부를 추가하려면 성격을 반드시 골라야 한다.
LEDGER_TABLES: tuple[str, ...] = tuple(sorted(CUMULATIVE_LEDGER_TABLES + STATE_LEDGER_TABLES))


@dataclass(frozen=True)
class OrphanFill:
    """처분이 기록되지 않은 체결 하나(WAN-194) — "진짜 유실"의 후보.

    `live_limit_orders`에서 `status='filled'`인데 `entry_status IS NULL`인 행이다. 러너가
    체결 기록과 포지션 쓰기 사이에서 죽으면 이 모양이 남는다(두 쓰기는 원자적이지 않다).
    ⚠️ WAN-194 이전 기록도 `NULL`이라 같은 모양으로 보인다 — 그쪽은 유실이 아니라
    **판별 불가**다(창을 자르려면 `since_ms`).

    이 dataclass가 `live`가 아니라 `data`에 있는 이유: 장부(`live.order_journal`)와 점검
    도구가 **같은 행 모양**을 봐야 하는데, 레이어 규칙상 `data`는 `live`를 임포트할 수
    없다(`docs/architecture-layers.md`). 낮은 쪽에 한 벌 두고 위에서 가져다 쓴다.
    """

    journal_id: int
    symbol: str
    timeframe: str
    fill_ms: int | None
    fill_price: float | None
    stop_price: float | None


@dataclass(frozen=True)
class TableCensus:
    """테이블 하나의 행 수."""

    name: str
    rows: int
    is_recovery_artifact: bool = False
    is_ledger: bool = False
    is_state_ledger: bool = False
    """현재 상태 장부인가(WAN-321) — 참이면 0행이 정상이라 종료 코드에 반영하지 않는다."""


@dataclass(frozen=True)
class SalvageableCandles:
    """`lost_and_found`에 갇힌 캔들 행 한 묶음(= 타임프레임 하나, WAN-195).

    `.recover`는 고아 행이 **어느 테이블 것인지**를 잃어버리고 필드 개수와 값만 남긴다.
    그래서 9필드 + `(텍스트, 텍스트, 정수, …)` 모양이면 `ohlcv` 행으로 읽을 수 있다.
    """

    timeframe: str
    rows: int
    symbols: int
    first_open_ms: int | None
    last_open_ms: int | None
    live_rows: int
    """같은 타임프레임이 살아 있는 `ohlcv` 테이블에 몇 행 있는지."""

    @property
    def timeframe_is_lost(self) -> bool:
        """이 TF가 본 테이블에서 통째로 사라졌는가 — 그렇다면 고아 행이 유일한 사본이다."""
        return self.live_rows == 0


@dataclass(frozen=True)
class RecoveryArtifactCensus:
    """복구 산출 테이블 하나의 내용물 분해(WAN-195).

    WAN-194는 `lost_and_found`를 **행 수만** 셌다. 그러면 "283만 행 쓰레기"로 보여
    통째로 버리게 되는데, 실측하면 그 안에 **살아 있는 테이블에 없는 캔들**이 들어
    있었다(5m 145만 행). 버리기 전에 무엇이 들어 있는지 먼저 본다.
    """

    table: str
    total_rows: int
    max_fields: int
    """가장 넓은 고아 행의 필드 수. 이 값보다 열이 많은 앱 테이블은 **여기 있을 수 없다**."""
    candles: list[SalvageableCandles]

    @property
    def candle_rows(self) -> int:
        return sum(group.rows for group in self.candles)

    @property
    def salvageable(self) -> list[SalvageableCandles]:
        """본 테이블에서 사라진 타임프레임 — 버리면 되돌릴 수 없는 묶음."""
        return [group for group in self.candles if group.timeframe_is_lost]

    def could_contain(self, table_arity: int) -> bool:
        """열이 `table_arity`개인 테이블의 행이 이 산출물에 있을 수 있는가.

        `.recover`는 가장 넓은 고아 행에 맞춰 `c0..cN`을 만든다. 그보다 열이 많은
        테이블(예: 17열 `paper_trades`)은 **구조적으로** 들어갈 수 없다 — "복원 시도했으나
        없었다"와 "애초에 있을 수 없다"는 다른 진술이고, 후자가 훨씬 강하다(WAN-195 §4).
        """
        return table_arity <= self.max_fields


@dataclass(frozen=True)
class SalvageResult:
    """`lost_and_found` → `ohlcv` 복원 한 번의 결과(WAN-195)."""

    timeframe: str
    candidates: int
    inserted: int
    dry_run: bool

    @property
    def skipped(self) -> int:
        """이미 본 테이블에 있어서 건너뛴 행(중복 포함)."""
        return self.candidates - self.inserted


@dataclass(frozen=True)
class SpaceReport:
    """공간·저널 상태(§5 손상 벡터 후보)."""

    page_size: int
    page_count: int
    freelist_count: int
    journal_mode: str
    wal_bytes: int
    disk_free_bytes: int
    disk_total_bytes: int

    @property
    def db_bytes(self) -> int:
        return self.page_size * self.page_count

    @property
    def reclaimable_bytes(self) -> int:
        """`VACUUM`으로 회수 가능한 하한(프리리스트 페이지). 드롭한 테이블 공간이 여기 쌓인다."""
        return self.page_size * self.freelist_count

    @property
    def disk_free_fraction(self) -> float:
        return self.disk_free_bytes / self.disk_total_bytes if self.disk_total_bytes else 0.0


@dataclass(frozen=True)
class IntegrityReport:
    """`alphablock doctor` 한 번의 결과."""

    db_path: str
    quick_check: list[str]
    """`PRAGMA quick_check` 출력. 정상이면 `["ok"]`. 건너뛰었으면 빈 리스트."""
    tables: list[TableCensus]
    space: SpaceReport
    orphan_fills: list[OrphanFill]
    artifact_census: list[RecoveryArtifactCensus] = field(default_factory=list)
    """복구 산출물 안에 무엇이 갇혀 있는지(WAN-195). 산출물이 없으면 빈 리스트."""

    @property
    def salvageable_candles(self) -> list[SalvageableCandles]:
        """본 테이블에서 사라진 TF의 캔들 — 산출물을 버리면 같이 사라진다."""
        return [group for report in self.artifact_census for group in report.salvageable]

    @property
    def quick_check_ok(self) -> bool:
        """건너뛴 경우도 True다 — "손상 없음"이 아니라 "손상 증거 없음"으로 읽을 것."""
        return self.quick_check in ([], ["ok"])

    @property
    def recovery_artifacts(self) -> list[TableCensus]:
        return [t for t in self.tables if t.is_recovery_artifact]

    @property
    def empty_cumulative_ledgers(self) -> list[TableCensus]:
        """0행이면 이상인 누적 장부(WAN-321) — **종료 코드의 근거**.

        ⚠️ 옛 `empty_ledgers`는 일부러 없앴다. 그 이름은 상태 장부까지 포함해 「비면 경고」로
        읽혔고, 호출부가 옛 뜻을 조용히 이어받으면 거짓 경보가 그대로 돌아온다 — 이름이
        아니라 **성격을 고르게** 강제한다.
        """
        return [t for t in self.tables if t.is_ledger and not t.is_state_ledger and t.rows == 0]

    @property
    def empty_state_ledgers(self) -> list[TableCensus]:
        """0행이 정상인 상태 장부(WAN-321) — 리포트에 찍는 **정보이지 경고가 아니다**."""
        return [t for t in self.tables if t.is_state_ledger and t.rows == 0]

    @property
    def healthy(self) -> bool:
        """경고할 것이 하나도 없는가(종료 코드의 근거).

        `empty_state_ledgers`는 **일부러 빠져 있다**(WAN-321 §1) — 포지션이 안 열려 있는
        것은 페이퍼 러너의 정상 상태다. WAN-194가 잡으려던 사고는 `empty_cumulative_ledgers`
        와 `orphan_fills`가 두 겹으로 계속 잡는다.
        """
        return (
            self.quick_check_ok
            and not self.recovery_artifacts
            and not self.orphan_fills
            and not self.empty_cumulative_ledgers
        )


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    # 테이블명은 `sqlite_master`에서 온 식별자라 바인딩할 수 없다(SQLite가 식별자
    # 파라미터를 지원하지 않는다). 큰따옴표로 인용해 이상한 이름도 안전하게 넘긴다.
    quoted = '"' + table.replace('"', '""') + '"'
    row = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()
    return int(row[0]) if row is not None else 0


#: 캔들 모양 고아 행을 고르는 조건 — `.recover`는 타입을 보존하므로 `typeof()`로 거른다.
#: 같은 페이지에 섞여 들어온 인덱스 조각·쓰레기 행이 `ohlcv`에 흘러드는 것을 막는다.
_CANDLE_SHAPE = (
    "nfield = ?"
    " AND typeof(c0) = 'text' AND typeof(c1) = 'text' AND typeof(c2) = 'integer'"
    " AND typeof(c3) IN ('integer','real') AND typeof(c4) IN ('integer','real')"
    " AND typeof(c5) IN ('integer','real') AND typeof(c6) IN ('integer','real')"
    " AND typeof(c7) IN ('integer','real')"
)


def _live_rows(conn: sqlite3.Connection, timeframe: str) -> int:
    """살아 있는 `ohlcv` 테이블의 해당 타임프레임 행 수."""
    row = conn.execute("SELECT COUNT(*) FROM ohlcv WHERE timeframe = ?", (timeframe,)).fetchone()
    return int(row[0]) if row is not None else 0


def census_recovery_artifacts(conn: sqlite3.Connection) -> list[RecoveryArtifactCensus]:
    """복구 산출 테이블의 내용물을 분해한다(WAN-195 §4).

    행 수만 세는 대신 **무엇이 갇혀 있는지**를 본다. 캔들 모양 행은 타임프레임별로
    묶고, 그 TF가 살아 있는 `ohlcv`에 있는지까지 대조해 "버려도 되는 중복"과 "여기밖에
    없는 유일본"을 가른다.
    """
    present = [n for n in _table_names(conn) if n in RECOVERY_ARTIFACT_TABLES]
    reports: list[RecoveryArtifactCensus] = []
    live_tables = set(_table_names(conn))
    for name in present:
        quoted = '"' + name.replace('"', '""') + '"'
        total = _count_rows(conn, name)
        # `.recover` 산출물은 언제나 `nfield` + `c0..cN`이지만, 이름만 같고 모양이 다른
        # 테이블에서 점검 전체가 죽으면 안 된다 — doctor는 **망가진 DB에서** 도는 도구다.
        columns = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({quoted})")}
        shaped = "nfield" in columns and {f"c{i}" for i in range(_OHLCV_ARITY)} <= columns

        max_fields = 0
        if "nfield" in columns:
            max_row = conn.execute(f"SELECT MAX(nfield) FROM {quoted}").fetchone()
            max_fields = int(max_row[0]) if max_row is not None and max_row[0] is not None else 0

        candles: list[SalvageableCandles] = []
        if shaped and "ohlcv" in live_tables:
            rows = conn.execute(
                f"SELECT c1, COUNT(*), COUNT(DISTINCT c0), MIN(c2), MAX(c2)"
                f" FROM {quoted} WHERE {_CANDLE_SHAPE} GROUP BY c1 ORDER BY 2 DESC",
                (_OHLCV_ARITY,),
            ).fetchall()
            for tf, count, symbols, first_ms, last_ms in rows:
                candles.append(
                    SalvageableCandles(
                        timeframe=str(tf),
                        rows=int(count),
                        symbols=int(symbols),
                        first_open_ms=None if first_ms is None else int(first_ms),
                        last_open_ms=None if last_ms is None else int(last_ms),
                        live_rows=_live_rows(conn, str(tf)),
                    )
                )
        reports.append(
            RecoveryArtifactCensus(
                table=name, total_rows=total, max_fields=max_fields, candles=candles
            )
        )
    return reports


def salvage_ohlcv(
    db_path: str | Path, *, timeframes: tuple[str, ...] | None = None, dry_run: bool = False
) -> list[SalvageResult]:
    """`lost_and_found`의 캔들 행을 `ohlcv`로 되돌린다 — 명시적 옵트인 전용(WAN-195 §4).

    **기존 행은 절대 덮어쓰지 않는다**(`ON CONFLICT DO NOTHING`). 수집기가 거래소에서
    받은 봉이 복구 산출물로 조용히 바뀌면 그게 더 나쁜 사고라, 충돌은 언제나 살아 있는
    쪽이 이긴다(`data.storage`의 백필 규약과 같다 — WAN-175).

    Args:
        db_path: 대상 DB.
        timeframes: 복원할 타임프레임. `None`이면 **본 테이블에서 사라진 TF만**
            복원한다(그쪽이 유일본이라 안전하고, 중복 대량 삽입을 피한다).
        dry_run: 세기만 하고 쓰지 않는다.

    Returns:
        타임프레임별 결과.
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {path}")

    conn = sqlite3.connect(str(path))
    try:
        configure_connection(conn)
        census = census_recovery_artifacts(conn)
        if not census:
            return []
        # `RECOVERY_ARTIFACT_TABLES`가 한 개(`lost_and_found`)라 산출물도 하나다. 늘어나면
        # 여기서 조용히 첫 번째만 처리하게 되므로, 그때는 루프로 바꿀 것.
        artifact = census[0]
        groups = artifact.candles
        if timeframes is None:
            targets = [g.timeframe for g in artifact.salvageable]
        else:
            known = {g.timeframe for g in groups}
            unknown = sorted(set(timeframes) - known)
            if unknown:
                # 오타를 조용히 0건으로 넘기면 "복원했다"고 믿게 된다.
                raise ValueError(
                    f"복구 산출물에 없는 타임프레임: {', '.join(unknown)}"
                    f" (있는 것: {', '.join(sorted(known)) or '없음'})"
                )
            targets = list(timeframes)

        quoted = '"' + artifact.table.replace('"', '""') + '"'
        results: list[SalvageResult] = []
        for tf in targets:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {quoted} WHERE {_CANDLE_SHAPE} AND c1 = ?",
                (_OHLCV_ARITY, tf),
            ).fetchone()
            candidates = int(row[0]) if row is not None else 0
            inserted = 0
            if not dry_run and candidates:
                before = _live_rows(conn, tf)
                with conn:
                    # `INSERT … SELECT … ON CONFLICT`는 SELECT에 WHERE가 있어야 파서가
                    # `ON`을 JOIN으로 읽지 않는다 — 아래 WHERE가 그 역할을 겸한다.
                    conn.execute(
                        "INSERT INTO ohlcv"
                        " (symbol, timeframe, open_time, open, high, low, close, volume, closed)"
                        " SELECT c0, c1, c2, c3, c4, c5, c6, c7,"
                        "        CASE WHEN typeof(c8) = 'integer' THEN c8 ELSE 1 END"
                        f" FROM {quoted} WHERE {_CANDLE_SHAPE} AND c1 = ?"
                        " ON CONFLICT(symbol, timeframe, open_time) DO NOTHING",
                        (_OHLCV_ARITY, tf),
                    )
                inserted = _live_rows(conn, tf) - before
            results.append(
                SalvageResult(
                    timeframe=tf, candidates=candidates, inserted=inserted, dry_run=dry_run
                )
            )
        return results
    finally:
        conn.close()


def collect_space(conn: sqlite3.Connection, db_path: Path) -> SpaceReport:
    """페이지·프리리스트·WAL·디스크 여유를 읽는다."""

    def pragma_int(name: str) -> int:
        row = conn.execute(f"PRAGMA {name}").fetchone()
        return int(row[0]) if row is not None else 0

    mode_row = conn.execute("PRAGMA journal_mode").fetchone()
    wal = db_path.with_name(db_path.name + "-wal")
    usage = shutil.disk_usage(db_path.parent if db_path.parent != Path("") else Path("."))
    return SpaceReport(
        page_size=pragma_int("page_size"),
        page_count=pragma_int("page_count"),
        freelist_count=pragma_int("freelist_count"),
        journal_mode=str(mode_row[0]) if mode_row is not None else "?",
        wal_bytes=wal.stat().st_size if wal.exists() else 0,
        disk_free_bytes=usage.free,
        disk_total_bytes=usage.total,
    )


def inspect(
    db_path: str | Path, *, quick_check: bool = True, orphan_since_ms: int | None = None
) -> IntegrityReport:
    """DB를 읽기 전용 관점으로 점검한다(쓰기 없음).

    Args:
        db_path: 점검할 SQLite 파일.
        quick_check: `PRAGMA quick_check` 실행 여부. 수 GB DB에서는 수십 초~분이 걸리니
            빠른 인구조사만 원하면 False.
        orphan_since_ms: 이 시각 이후 체결만 처분 미기록으로 본다(WAN-194 열 도입 이전
            기록은 전부 NULL이라 유실과 구분되지 않는다).

    Raises:
        FileNotFoundError: DB 파일이 없을 때 — 빈 DB를 새로 만들어 "정상"이라 보고하면
            경로 오타가 초록불로 보인다.
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {path}")

    conn = sqlite3.connect(str(path))
    try:
        configure_connection(conn)
        checks: list[str] = []
        if quick_check:
            checks = [str(r[0]) for r in conn.execute("PRAGMA quick_check").fetchall()]

        tables: list[TableCensus] = []
        for name in _table_names(conn):
            tables.append(
                TableCensus(
                    name=name,
                    rows=_count_rows(conn, name),
                    is_recovery_artifact=name in RECOVERY_ARTIFACT_TABLES,
                    is_ledger=name in LEDGER_TABLES,
                    is_state_ledger=name in STATE_LEDGER_TABLES,
                )
            )
        space = collect_space(conn, path)
        orphans = _orphan_fills(conn, since_ms=orphan_since_ms)
        census = census_recovery_artifacts(conn)
    finally:
        conn.close()

    return IntegrityReport(
        db_path=str(path),
        quick_check=checks,
        tables=tables,
        space=space,
        orphan_fills=orphans,
        artifact_census=census,
    )


def _orphan_fills(conn: sqlite3.Connection, *, since_ms: int | None) -> list[OrphanFill]:
    """처분 미기록 체결. 장부 테이블·열이 없는 DB(옛 스냅샷)에서는 빈 리스트다."""
    columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(live_limit_orders)")}
    if not columns or "entry_status" not in columns:
        # 테이블이 없거나(빈 집합) 열 도입 전 스키마다 — 판별할 자료가 없으므로 조용히
        # 빈 결과다(없는 열로 SELECT하면 OperationalError로 점검 전체가 죽는다).
        return []
    sql = (
        "SELECT id, symbol, timeframe, fill_ms, fill_price, stop_price FROM live_limit_orders"
        " WHERE status = 'filled' AND entry_status IS NULL"
    )
    args: list[object] = []
    if since_ms is not None:
        sql += " AND fill_ms >= ?"
        args.append(since_ms)
    rows = conn.execute(sql + " ORDER BY fill_ms", args).fetchall()
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


class SalvageableRowsPresent(RuntimeError):
    """복원할 수 있는 행이 남아 있는데 드롭하려 할 때(WAN-195).

    `lost_and_found`를 "283만 행 쓰레기"로 읽고 버리면 **살아 있는 테이블에 없는
    캔들까지 같이 사라진다** — 실제로 5m 145만 행이 그 상태였다. 드롭은 되돌릴 수
    없으므로(그 DB에만 있는 유일본이다) 기본을 거부로 두고 `force`를 요구한다.
    """


def drop_recovery_artifacts(db_path: str | Path, *, force: bool = False) -> list[str]:
    """복구 산출 테이블(`lost_and_found` 등)을 삭제한다 — 명시적 옵트인 전용(§4).

    ⚠️ **`VACUUM`은 하지 않는다.** 드롭은 페이지를 프리리스트로 돌릴 뿐이라 파일 크기가
    즉시 줄지 않는다. 줄이려면 `VACUUM`이 필요한데 그것은 DB를 독점 락하고 같은 크기의
    임시 파일을 쓰므로, 수집기·러너가 붙은 서버에서는 **러너를 멈춘 뒤 사람이** 돌려야
    한다(회수 가능 크기는 `inspect()`의 `reclaimable_bytes`가 알려준다).

    Args:
        db_path: 대상 DB.
        force: 복원 가능한 행이 남아 있어도 삭제한다(기본은 거부 — WAN-195).

    Raises:
        SalvageableRowsPresent: 본 테이블에서 사라진 타임프레임의 캔들이 아직 산출물에
            남아 있는데 `force`가 아닐 때. 먼저 `salvage_ohlcv()`를 돌릴 것.

    Returns:
        실제로 삭제한 테이블 이름들(없으면 빈 리스트).
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {path}")
    conn = sqlite3.connect(str(path))
    try:
        configure_connection(conn)
        if not force:
            for report in census_recovery_artifacts(conn):
                lost = report.salvageable
                if lost:
                    detail = ", ".join(f"{g.timeframe} {g.rows:,}행" for g in lost)
                    raise SalvageableRowsPresent(
                        f"`{report.table}`에 본 테이블에 없는 캔들이 남아 있습니다: {detail}."
                        " 먼저 `alphablock doctor --salvage-ohlcv`로 복원하거나,"
                        " 버릴 것이 확실하면 `--force`를 주십시오."
                    )
        present = [n for n in _table_names(conn) if n in RECOVERY_ARTIFACT_TABLES]
        with conn:
            for name in present:
                quoted = '"' + name.replace('"', '""') + '"'
                conn.execute(f"DROP TABLE {quoted}")
        return present
    finally:
        conn.close()


def _fmt_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:,.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024.0
    return f"{size:,.1f}TB"


def _render_artifact_contents(report: IntegrityReport) -> list[str]:
    """복구 산출물 **안에 무엇이 있는지**를 렌더한다(WAN-195 §4).

    WAN-194는 행 수만 보여 줬고, 그 숫자만 보면 통째로 버리는 판단이 자연스러웠다.
    실제로는 유일본 캔들이 들어 있었으므로 드롭 명령을 안내하기 **전에** 내용물을 낸다.
    """
    from common.timefmt import format_kst

    lines: list[str] = []
    for census in report.artifact_census:
        lines.append("")
        lines.append(f"**`{census.table}` 내용물** (가장 넓은 고아 행 = {census.max_fields}필드)")
        lines.append("")
        if not census.candles:
            lines.append("* 캔들 모양 행 없음.")
        else:
            lines.append("| TF | 고아 행 | 심볼 | 기간 | 본 테이블 | 판정 |")
            lines.append("| -- | --: | --: | -- | --: | -- |")
            for group in census.candles:
                span = "-"
                if group.first_open_ms is not None and group.last_open_ms is not None:
                    span = (
                        f"{format_kst(group.first_open_ms)[:10]}"
                        f" … {format_kst(group.last_open_ms)[:10]}"
                    )
                verdict = "🚨 **유일본**" if group.timeframe_is_lost else "본 테이블에 있음"
                lines.append(
                    f"| `{group.timeframe}` | {group.rows:,} | {group.symbols} | {span} |"
                    f" {group.live_rows:,} | {verdict} |"
                )
        # 열 수가 모자라면 그 테이블 행은 **구조적으로** 들어갈 수 없다 — "복원 시도했으나
        # 못 찾았다"보다 훨씬 강한 진술이라 명시한다.
        too_wide = [
            (name, arity)
            for name, arity in (("paper_trades", 17), ("open_positions", 10))
            if not census.could_contain(arity)
        ]
        if too_wide:
            names = ", ".join(f"`{n}`({a}열)" for n, a in too_wide)
            lines.append("")
            lines.append(
                f"📌 {names}의 행은 여기 **있을 수 없다** — 산출물의 최대 필드 수가"
                f" {census.max_fields}라 그보다 넓은 행은 애초에 담기지 않는다."
                " 즉 매매 장부는 이 산출물에서 복원할 수 없다(없는 게 아니라 담길 수 없다)."
            )

    salvage = report.salvageable_candles
    lines.append("")
    if salvage:
        total = sum(g.rows for g in salvage)
        tfs = ", ".join(f"`{g.timeframe}`" for g in salvage)
        lines.append(
            f"🚨 **버리기 전에 복원할 것** — {tfs}의 {total:,}행은 본 테이블에 **0행**이라"
            " 이 산출물이 유일한 사본이다. `alphablock doctor --salvage-ohlcv`로 되돌린 뒤"
            " 드롭한다(드롭은 이 상태에서 기본 거부된다)."
        )
    else:
        lines.append(
            "복원할 유일본은 없다 — 정리는 `alphablock doctor --drop-recovery-artifacts`이고,"
            " 파일 크기를 실제로 줄이려면 그 뒤에 **러너를 멈추고** `VACUUM`을 사람이 돌린다."
        )
    return lines


def render_report(report: IntegrityReport) -> str:
    """점검 결과를 사람이 읽는 마크다운으로 렌더한다."""
    from common.timefmt import KST_LABEL, format_kst

    lines: list[str] = [f"# DB 점검 (WAN-194) — `{report.db_path}`", ""]

    lines.append("## 무결성")
    lines.append("")
    if not report.quick_check:
        lines.append(
            "`quick_check` 건너뜀(`--skip-quick-check`) — **손상 없음이 아니라 미확인**이다."
        )
    elif report.quick_check_ok:
        lines.append("`PRAGMA quick_check` = **ok**(페이지 수준 손상 없음).")
        lines.append("")
        lines.append(
            "⚠️ quick_check는 인덱스 정합성을 다 보지 않는다 — 의심이 남으면"
            " `PRAGMA integrity_check`를 손으로 돌릴 것(수 GB에서 수십 분)."
        )
    else:
        lines.append("🚨 **손상 발견** — `PRAGMA quick_check` 출력:")
        lines.append("")
        for entry in report.quick_check[:20]:
            lines.append(f"* `{entry}`")
        if len(report.quick_check) > 20:
            lines.append(f"* … 외 {len(report.quick_check) - 20}줄")

    lines.append("")
    lines.append("## 복구 산출물 (§4)")
    lines.append("")
    artifacts = report.recovery_artifacts
    if not artifacts:
        lines.append("없음 — 이 DB에 `.recover` 흔적이 없다.")
    else:
        for table in artifacts:
            lines.append(
                f"* 🚨 `{table.name}` **{table.rows:,}행** — SQLite `.recover` 산출물이다."
            )
        lines.append("")
        lines.append(
            "존재 자체가 **이 DB가 한 번 복구됐다**는 증거다(앱 코드에는 복구 경로가 없다)."
        )
        lines.extend(_render_artifact_contents(report))

    lines.append("")
    lines.append("## 테이블 인구조사")
    lines.append("")
    lines.append("| 테이블 | 행 수 | 비고 |")
    lines.append("| -- | --: | -- |")
    for table in report.tables:
        note = ""
        if table.is_recovery_artifact:
            note = "복구 산출물"
        elif table.is_state_ledger:
            # 0행이 정상이라 여기서는 굵게 세우지 않는다(WAN-321) — 눈이 그리로 가면
            # 사람이 다시 "빈 장부"로 읽는다.
            empty_note = " · 0행 = 열린 포지션 없음" if table.rows == 0 else ""
            note = "페이퍼 장부(현재 상태)" + empty_note
        elif table.is_ledger:
            note = "페이퍼 장부(누적)" + (" · **0행**" if table.rows == 0 else "")
        lines.append(f"| `{table.name}` | {table.rows:,} | {note} |")
    lines.append("")
    empty = report.empty_cumulative_ledgers
    if empty:
        names = ", ".join(f"`{t.name}`" for t in empty)
        lines.append(
            f"⚠️ 빈 누적 장부: {names}. **다른 테이블이 성한 채 장부만 비었으면 광범위 유실이"
            " 아니라 배선·거부 쪽**이다(WAN-194의 판별 근거) — 아래 처분 섹션과 함께 읽을 것."
        )
    else:
        lines.append("누적 장부에 빈 것은 없다.")
    for table in report.empty_state_ledgers:
        # 정보 한 줄로만 남긴다 — 종료 코드에는 반영하지 않는다(WAN-321 §1).
        lines.append(
            f"ℹ️ `{table.name}` 0행 — **지금 열린 포지션이 없다는 뜻이고 정상이다**"
            "(포지션은 닫힌다). 진짜 유실은 아래 처분 섹션이 잡는다."
        )

    lines.append("")
    lines.append("## 처분 미기록 체결 (§3 잔여 유실)")
    lines.append("")
    if not report.orphan_fills:
        lines.append("없음 — 모든 체결에 진입/거부 처분이 남아 있다.")
    else:
        lines.append(
            f"🚨 **{len(report.orphan_fills)}건**. 체결은 남았는데 포지션이 됐는지"
            " 거부됐는지 기록이 없다 — 러너가 두 쓰기 사이에서 죽은 모양이다."
        )
        lines.append("")
        lines.append(f"| 장부 id | 심볼 | TF | 체결({KST_LABEL}) |")
        lines.append("| --: | -- | -- | -- |")
        for orphan in report.orphan_fills[:20]:
            when = "-" if orphan.fill_ms is None else format_kst(orphan.fill_ms)
            lines.append(f"| {orphan.journal_id} | {orphan.symbol} | {orphan.timeframe} | {when} |")
        if len(report.orphan_fills) > 20:
            lines.append(f"| … | 외 {len(report.orphan_fills) - 20}건 | | |")
        lines.append("")
        lines.append(
            "⚠️ WAN-194 이전 체결은 `entry_status` 열이 없어 전부 여기 잡힌다 — 도입"
            " 이후만 유실로 읽으려면 `--orphans-since`(KST 날짜)를 줄 것."
        )

    lines.append("")
    lines.append("## 공간·저널 (§5 손상 벡터)")
    lines.append("")
    space = report.space
    lines.append(f"* DB {_fmt_bytes(space.db_bytes)} ({space.page_count:,} 페이지)")
    lines.append(
        f"* 회수 가능(프리리스트) {_fmt_bytes(space.reclaimable_bytes)} — `VACUUM` 시 줄어드는 하한"
    )
    lines.append(f"* 저널 모드 `{space.journal_mode}` · WAL {_fmt_bytes(space.wal_bytes)}")
    lines.append(
        f"* 디스크 여유 {_fmt_bytes(space.disk_free_bytes)} /"
        f" {_fmt_bytes(space.disk_total_bytes)} ({space.disk_free_fraction * 100:.1f}%)"
    )
    lines.append("")
    if space.disk_free_fraction < 0.10:
        lines.append(
            "🚨 디스크 여유 10% 미만 — SQLite 쓰기 실패·손상의 대표 벡터다(§5). 먼저 비울 것."
        )
    else:
        lines.append(
            "디스크 여유는 넉넉하다. 남은 손상 벡터 후보는 **쓰기 도중 강제종료**다"
            "(WAN-186/187 대응 때의 강제종료 이력) — 러너·수집기를 SIGKILL로 끊지 말 것."
        )
    return "\n".join(lines)
