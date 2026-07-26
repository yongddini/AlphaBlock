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
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from data.sqlite_util import configure_connection

#: SQLite `.recover`가 고아 페이지를 쏟아붓는 산출 테이블(앱 스키마가 아니다).
#: 존재 자체가 "이 DB는 복구된 것"이라는 증거다(WAN-194).
RECOVERY_ARTIFACT_TABLES = frozenset({"lost_and_found"})

#: 페이퍼 운영 장부 — 0행이면 매매 기록이 없다는 뜻이라 눈에 띄어야 한다(WAN-194).
LEDGER_TABLES: tuple[str, ...] = (
    "live_limit_orders",
    "live_runner_sessions",
    "open_positions",
    "paper_trades",
)


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

    @property
    def quick_check_ok(self) -> bool:
        """건너뛴 경우도 True다 — "손상 없음"이 아니라 "손상 증거 없음"으로 읽을 것."""
        return self.quick_check in ([], ["ok"])

    @property
    def recovery_artifacts(self) -> list[TableCensus]:
        return [t for t in self.tables if t.is_recovery_artifact]

    @property
    def empty_ledgers(self) -> list[TableCensus]:
        return [t for t in self.tables if t.is_ledger and t.rows == 0]

    @property
    def healthy(self) -> bool:
        """경고할 것이 하나도 없는가(종료 코드의 근거)."""
        return (
            self.quick_check_ok
            and not self.recovery_artifacts
            and not self.orphan_fills
            and not self.empty_ledgers
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
                )
            )
        space = collect_space(conn, path)
        orphans = _orphan_fills(conn, since_ms=orphan_since_ms)
    finally:
        conn.close()

    return IntegrityReport(
        db_path=str(path),
        quick_check=checks,
        tables=tables,
        space=space,
        orphan_fills=orphans,
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


def drop_recovery_artifacts(db_path: str | Path) -> list[str]:
    """복구 산출 테이블(`lost_and_found` 등)을 삭제한다 — 명시적 옵트인 전용(§4).

    ⚠️ **`VACUUM`은 하지 않는다.** 드롭은 페이지를 프리리스트로 돌릴 뿐이라 파일 크기가
    즉시 줄지 않는다. 줄이려면 `VACUUM`이 필요한데 그것은 DB를 독점 락하고 같은 크기의
    임시 파일을 쓰므로, 수집기·러너가 붙은 서버에서는 **러너를 멈춘 뒤 사람이** 돌려야
    한다(회수 가능 크기는 `inspect()`의 `reclaimable_bytes`가 알려준다).

    Returns:
        실제로 삭제한 테이블 이름들(없으면 빈 리스트).
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {path}")
    conn = sqlite3.connect(str(path))
    try:
        configure_connection(conn)
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
            " 정리는 `alphablock doctor --drop-recovery-artifacts`이고, 파일 크기를 실제로"
            " 줄이려면 그 뒤에 **러너를 멈추고** `VACUUM`을 사람이 돌린다."
        )

    lines.append("")
    lines.append("## 테이블 인구조사")
    lines.append("")
    lines.append("| 테이블 | 행 수 | 비고 |")
    lines.append("| -- | --: | -- |")
    for table in report.tables:
        note = ""
        if table.is_recovery_artifact:
            note = "복구 산출물"
        elif table.is_ledger:
            note = "페이퍼 장부" + (" · **0행**" if table.rows == 0 else "")
        lines.append(f"| `{table.name}` | {table.rows:,} | {note} |")
    lines.append("")
    empty = report.empty_ledgers
    if empty:
        names = ", ".join(f"`{t.name}`" for t in empty)
        lines.append(
            f"⚠️ 빈 장부: {names}. **다른 테이블이 성한 채 장부만 비었으면 광범위 유실이"
            " 아니라 배선·거부 쪽**이다(WAN-194의 판별 근거) — 아래 처분 섹션과 함께 읽을 것."
        )
    else:
        lines.append("장부 테이블에 빈 것은 없다.")

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
