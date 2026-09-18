"""scripts/paper-reset.sh 회귀 테스트 (WAN-218 · WAN-399).

이 도구의 핵심 안전 계약은 **"시세 데이터는 절대 건드리지 않는다"** 이다 — DB 파일 하나에
시세(`ohlcv`·`funding_rate`)와 거래기록이 함께 들어 있으므로, 초기화가 거래기록 4개
테이블만 비우고 시세·백테스트 테이블은 그대로 두는지를 **라벨이 아니라 동작으로** 잠근다.

테스트는 실제 bash 스크립트를 서브프로세스로 돌린다(계약이 스크립트 안에 있으므로) —
파이썬 표준 `sqlite3` 모듈로 임시 DB를 만들고, 스크립트가 요구하는 `sqlite3` CLI와 `bash`가
있을 때만 실행한다(없으면 스킵).

WAN-399(사용자 결정 2026-09-07·09-18): 기본 실행은 **장부 CSV 내보내기 → DELETE** 까지만
한다. DB 전체 백업·VACUUM 은 옵트인(`--backup`·`--vacuum`)이고, 내보내기가 실패하면 삭제하지
않는다. 이 계약들도 전부 **동작으로**(파일이 생겼나 · 명령 목록에 있나 · 행이 남았나) 건다.
"""

from __future__ import annotations

import csv
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "paper-reset.sh"

# 스크립트가 비우는 거래기록 4개 테이블.
PAPER_TABLES = ("paper_trades", "open_positions", "live_limit_orders", "live_runner_sessions")
# --include-backtest 로만 함께 비우는 백테스트 장부 5개 테이블.
BACKTEST_TABLES = (
    "backtest_runs",
    "backtest_trades",
    "backtest_trade_exits",
    "backtest_setups",
    "backtest_equity",
)
# 절대 건드리면 안 되는 시세 테이블.
PROTECTED_TABLES = ("ohlcv", "funding_rate")

# CLI 의존성이 없으면 스크립트 자체가 못 도므로 스킵(실 스크립트를 돌리는 게 이 테스트의 요점).
_MISSING = [tool for tool in ("bash", "sqlite3") if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(bool(_MISSING), reason=f"필요한 CLI 없음: {', '.join(_MISSING)}")


def _make_db(path: Path, *, with_backtest: bool = True) -> dict[str, int]:
    """모든 테이블을 만들고 각기 다른 행 수를 넣는다. 넣은 행 수를 돌려준다."""
    counts: dict[str, int] = {}
    tables = list(PAPER_TABLES) + list(PROTECTED_TABLES)
    if with_backtest:
        tables += list(BACKTEST_TABLES)
    conn = sqlite3.connect(path)
    try:
        for i, table in enumerate(tables, start=1):
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
            conn.executemany(f"INSERT INTO {table} (id) VALUES (?)", [(j,) for j in range(i)])
            counts[table] = i
        conn.commit()
    finally:
        conn.close()
    return counts


def _count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _run(
    db: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # `--services ""` 로 systemd 단계를 격리한다. 이 테스트가 잠그는 것은 "시세를
    # 건드리지 않는다"는 DB 계약이지 서비스 오케스트레이션이 아니다 — 그리고 CI 러너에는
    # systemctl 은 있으나 유닛(alphablock-live 등)이 없어, 서비스 정지 단계가 유닛
    # 미로드(종료 코드 5)로 스크립트를 삭제 이전에 중단시킨다(로컬 macOS 에는 systemctl
    # 자체가 없어 그 단계를 건너뛰므로 환경마다 결과가 달라진다). 빈 SERVICES 는
    # `-n "$SERVICES"` 가드로 서비스 분기를 통째로 건너뛰게 한다.
    return subprocess.run(
        [str(SCRIPT), "--db", str(db), "--services", "", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
        env=env,
    )


def test_script_is_executable() -> None:
    """스크립트가 커밋돼 있고 실행 권한이 있다(완료 기준)."""
    import os

    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK), "paper-reset.sh 에 실행 권한이 없다"


def test_default_wipes_only_paper_tables(tmp_path: Path) -> None:
    """기본 실행: 거래기록 4개만 0, 시세·백테스트는 그대로 — 핵심 안전 계약."""
    db = tmp_path / "ohlcv.db"
    before = _make_db(db, with_backtest=True)

    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stderr

    for table in PAPER_TABLES:
        assert _count(db, table) == 0, f"{table} 가 비워지지 않았다"
    for table in PROTECTED_TABLES + BACKTEST_TABLES:
        assert _count(db, table) == before[table], f"{table} 가 건드려졌다(보존돼야 함)"


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    """--dry-run 은 아무것도 바꾸지 않고 지울 대상만 출력한다(완료 기준)."""
    db = tmp_path / "ohlcv.db"
    before = _make_db(db, with_backtest=True)

    proc = _run(db, "--dry-run", "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stderr

    for table, expected in before.items():
        assert _count(db, table) == expected, f"{table} 가 dry-run 에서 바뀌었다"
    # 지울 대상(행 수)이 출력에 보인다.
    assert "paper_trades" in proc.stdout


def test_include_backtest_also_wipes_backtest(tmp_path: Path) -> None:
    """--include-backtest: 거래기록 4개 + 백테스트 5개 = 0, 시세는 그대로."""
    db = tmp_path / "ohlcv.db"
    before = _make_db(db, with_backtest=True)

    proc = _run(db, "--yes", "--no-restart", "--include-backtest")
    assert proc.returncode == 0, proc.stderr

    for table in PAPER_TABLES + BACKTEST_TABLES:
        assert _count(db, table) == 0, f"{table} 가 비워지지 않았다"
    for table in PROTECTED_TABLES:
        assert _count(db, table) == before[table], f"{table}(시세) 가 건드려졌다"


def test_backup_is_revertible(tmp_path: Path) -> None:
    """--backup(옵트인)이 삭제 전 원본을 그대로 담는다 — WAN-195 되돌리기 교훈."""
    db = tmp_path / "ohlcv.db"
    before = _make_db(db, with_backtest=True)

    proc = _run(db, "--yes", "--no-restart", "--backup")
    assert proc.returncode == 0, proc.stderr

    backups = list(tmp_path.glob("ohlcv.db.bak-*"))
    assert len(backups) == 1, f"백업 파일이 정확히 하나여야 한다: {backups}"
    backup = backups[0]
    # 백업은 삭제 이전 상태 — 거래기록 행이 그대로 살아 있어야 되돌릴 수 있다.
    for table, expected in before.items():
        assert _count(backup, table) == expected, f"백업의 {table} 가 원본과 다르다"
    # 원본은 실제로 비워졌다(백업이 dry-run 이 아님을 확인).
    assert _count(db, "paper_trades") == 0
    # 되돌리는 법은 백업을 떴을 때만 안내한다.
    assert "되돌리려면" in proc.stdout


# --- WAN-399: 기본은 DELETE만 — 백업·VACUUM 옵트인 ---------------------------------


def test_default_run_has_no_backup_and_no_vacuum(tmp_path: Path) -> None:
    """기본 실행에 백업도 VACUUM도 안 돈다 — 라벨이 아니라 명령 목록·산출물로.

    돌연변이 확인: 기본값을 옛 동작(`BACKUP=1` · 무조건 `VACUUM`)으로 되돌리면 이 테스트가
    깨진다(dry-run 명령 목록에 둘이 나타나고 · 실제 실행에 `.bak-*` 파일이 생긴다).
    """
    db = tmp_path / "ohlcv.db"
    _make_db(db)

    dry = _run(db, "--dry-run", "--yes", "--no-restart")
    assert dry.returncode == 0, dry.stderr
    commands = [line for line in dry.stdout.splitlines() if "[dry-run]" in line]
    assert any("DELETE FROM paper_trades" in c for c in commands), commands
    assert not any("VACUUM" in c for c in commands), f"기본 실행에 VACUUM 이 있다: {commands}"
    assert not any("db-backup.sh" in c for c in commands), f"기본 실행에 백업이 있다: {commands}"
    # 로그 문구도 동작을 따라간다(건너뛰고 「+ VACUUM」이라 찍히면 라벨과 동작이 어긋난다).
    assert "삭제 + VACUUM" not in dry.stdout

    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stderr
    assert not list(tmp_path.glob("ohlcv.db.bak-*")), "기본 실행이 백업을 떴다"
    assert "되돌리려면" not in proc.stdout


def test_default_run_exit_code_is_zero(tmp_path: Path) -> None:
    """🚨 기본 실행(백업 꺼짐)이 종료 코드 0 — 마지막 `if`의 단축평가 함정 고정.

    스크립트 마지막 문장이 `[[ "$BACKUP" -eq 1 ]] && log …` 로 바뀌면 백업이 꺼진 기본 실행이
    `set -e` 아래에서 **매번** 종료 코드 1로 끝난다(WAN-399 이전엔 `--no-backup` 때만).
    """
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "초기화 완료" in proc.stdout


def test_opt_in_backup_and_vacuum_actually_run(tmp_path: Path) -> None:
    """--backup · --vacuum 을 주면 각각 명령 목록에 실제로 오른다(백업은 db-backup.sh 경로)."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)

    dry = _run(db, "--dry-run", "--yes", "--no-restart", "--backup", "--vacuum")
    assert dry.returncode == 0, dry.stderr
    commands = [line for line in dry.stdout.splitlines() if "[dry-run]" in line]
    assert any("VACUUM" in c for c in commands), commands
    assert any("db-backup.sh" in c for c in commands), commands
    assert "삭제 + VACUUM" in dry.stdout


def test_vacuum_reclaims_space_when_asked(tmp_path: Path) -> None:
    """--vacuum 이 라벨이 아니라 실제로 파일을 다시 쓴다 — 큰 장부를 지우면 파일이 준다."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.executemany("INSERT INTO paper_trades (id) VALUES (?)", [(i,) for i in range(20_000)])
        conn.commit()
    finally:
        conn.close()
    size_before = db.stat().st_size

    proc = _run(db, "--yes", "--no-restart", "--vacuum")
    assert proc.returncode == 0, proc.stderr
    assert db.stat().st_size < size_before, "--vacuum 을 줬는데 파일이 줄지 않았다"


def test_default_run_does_not_shrink_file(tmp_path: Path) -> None:
    """기본 실행은 VACUUM 을 안 하므로 큰 장부를 지워도 파일 크기가 그대로다(동작 확인)."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.executemany("INSERT INTO paper_trades (id) VALUES (?)", [(i,) for i in range(20_000)])
        conn.commit()
    finally:
        conn.close()
    size_before = db.stat().st_size

    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stderr
    assert db.stat().st_size == size_before


def test_legacy_no_backup_flag_is_accepted_as_noop(tmp_path: Path) -> None:
    """`--no-backup`(손에 익은 옛 플래그)은 거부하지 않고 무동작 + 안내 한 줄."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    proc = _run(db, "--yes", "--no-restart", "--no-backup")
    assert proc.returncode == 0, proc.stderr
    assert "이제 기본입니다" in proc.stdout
    assert not list(tmp_path.glob("ohlcv.db.bak-*"))


def test_include_backtest_without_vacuum_hints_once(tmp_path: Path) -> None:
    """--include-backtest 에 --vacuum 이 없으면 한 줄 안내만(막지도 · 켜지도 않는다)."""
    db = tmp_path / "ohlcv.db"
    _make_db(db, with_backtest=True)

    proc = _run(db, "--dry-run", "--yes", "--no-restart", "--include-backtest")
    assert proc.returncode == 0, proc.stderr
    assert "--vacuum 을 함께" in proc.stdout
    assert not any("VACUUM" in c for c in proc.stdout.splitlines() if "[dry-run]" in c)

    with_vac = _run(db, "--dry-run", "--yes", "--no-restart", "--include-backtest", "--vacuum")
    assert "--vacuum 을 함께" not in with_vac.stdout
    # 정상 경로(백테스트 장부 안 지움)에는 잔소리가 없다.
    plain = _run(db, "--dry-run", "--yes", "--no-restart")
    assert "--vacuum 을 함께" not in plain.stdout


# --- WAN-399 §7: 지우기 전에 장부를 CSV로 떨군다 ------------------------------------


def _export_dirs(parent: Path) -> list[Path]:
    return sorted(p for p in parent.iterdir() if p.is_dir()) if parent.exists() else []


def test_default_run_exports_paper_tables_to_csv(tmp_path: Path) -> None:
    """기본 실행에서 CSV가 실제로 떨어지고 행 수가 지운 행 수와 같다(따옴표 안 줄바꿈 포함)."""
    db = tmp_path / "ohlcv.db"
    before = _make_db(db, with_backtest=True)
    conn = sqlite3.connect(db)
    try:
        # CSV가 까다로워하는 값 — 쉼표 · 줄바꿈 · 따옴표. 줄 수가 아니라 레코드 수로 잰다.
        conn.execute("ALTER TABLE live_limit_orders ADD COLUMN note TEXT")
        conn.executemany(
            "INSERT INTO live_limit_orders (id, note) VALUES (?, ?)",
            [(100, "a,b"), (101, "line1\nline2"), (102, 'q"x')],
        )
        conn.commit()
    finally:
        conn.close()
    before["live_limit_orders"] += 3

    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stderr

    dirs = _export_dirs(tmp_path / "paper-reset-exports")
    assert len(dirs) == 1, dirs
    exported = {p.stem for p in dirs[0].glob("*.csv")}
    # 페이퍼 장부 4개만 — 시세·백테스트 장부는 내보내지 않는다(수 GB CSV 사고 방지).
    assert exported == set(PAPER_TABLES), exported
    for table in PAPER_TABLES:
        with (dirs[0] / f"{table}.csv").open(newline="") as fh:
            rows = list(csv.reader(fh))
        assert rows[0][0] == "id", f"{table}.csv 에 헤더가 없다"
        assert len(rows) - 1 == before[table], f"{table}: CSV 행 수가 지운 행 수와 다르다"
        assert _count(db, table) == 0
    with (dirs[0] / "live_limit_orders.csv").open(newline="") as fh:
        notes = [r[1] for r in csv.reader(fh)][1:]
    assert "line1\nline2" in notes and 'q"x' in notes and "a,b" in notes


def test_empty_table_still_exports_header(tmp_path: Path) -> None:
    """0행 테이블도 열 이름이 담긴 CSV를 남긴다(`-header`는 0행이면 헤더조차 안 쓴다)."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM open_positions")
        conn.commit()
    finally:
        conn.close()
    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stderr
    (d,) = _export_dirs(tmp_path / "paper-reset-exports")
    assert (d / "open_positions.csv").read_text().strip() == "id"


def test_export_failure_aborts_delete(tmp_path: Path) -> None:
    """🚨 내보내기가 실패하면 삭제하지 않는다 — 기록 없이 장부만 사라지는 결과를 막는다."""
    db = tmp_path / "ohlcv.db"
    before = _make_db(db, with_backtest=True)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # 디렉터리를 만들 수 없는 자리

    proc = _run(db, "--yes", "--no-restart", "--export-dir", str(blocker / "sub"))
    assert proc.returncode != 0
    for table, expected in before.items():
        assert _count(db, table) == expected, f"{table}: 내보내기 실패인데 지워졌다"


def test_export_failure_on_one_table_aborts_delete(tmp_path: Path) -> None:
    """한 테이블이라도 못 내보내면(없는 테이블) 나머지도 지우지 않는다."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE live_runner_sessions")
        conn.commit()
    finally:
        conn.close()
    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode != 0
    assert "삭제를 중단" in proc.stderr
    assert _count(db, "paper_trades") > 0


def test_export_never_overwrites(tmp_path: Path) -> None:
    """같은 시각의 내보내기 디렉터리가 이미 있으면 덮어쓰지 않고 삭제도 멈춘다.

    `date` 를 고정 시각을 내는 가짜로 바꿔(PATH 앞에 끼움) 두 번째 실행이 첫 실행과 **같은**
    디렉터리를 요구하게 만든다 — 리셋은 반복되므로 옛 기록을 덮으면 안 된다.
    """
    import os

    shim = tmp_path / "bin"
    shim.mkdir()
    fake_date = shim / "date"
    fake_date.write_text("#!/bin/sh\necho 20260101-000000\n")
    fake_date.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}

    db = tmp_path / "ohlcv.db"
    _make_db(db)
    parent = tmp_path / "exp"
    first = _run(db, "--yes", "--no-restart", "--export-dir", str(parent), env=env)
    assert first.returncode == 0, first.stderr
    (d,) = _export_dirs(parent)
    assert d.name == "20260101-000000"
    assert re.fullmatch(r"\d{8}-\d{6}", d.name)
    snapshot = {p.name: p.read_bytes() for p in d.iterdir()}

    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO paper_trades (id) VALUES (999)")
        conn.commit()
    finally:
        conn.close()

    second = _run(db, "--yes", "--no-restart", "--export-dir", str(parent), env=env)
    assert second.returncode != 0
    assert _count(db, "paper_trades") == 1, "덮어쓰기를 거부했는데 장부가 지워졌다"
    assert {p.name: p.read_bytes() for p in d.iterdir()} == snapshot, "옛 기록이 덮였다"


def test_dry_run_shows_export_plan_without_writing(tmp_path: Path) -> None:
    """--dry-run 이 어디에 무엇을 떨굴지 보이되 아무것도 만들지 않는다."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    parent = tmp_path / "exp"
    proc = _run(db, "--dry-run", "--yes", "--no-restart", "--export-dir", str(parent))
    assert proc.returncode == 0, proc.stderr
    for table in PAPER_TABLES:
        assert f"{table}.csv" in proc.stdout
    assert str(parent) in proc.stdout
    assert not parent.exists(), "dry-run 이 내보내기 디렉터리를 만들었다"


def test_help_prints_header_only() -> None:
    """--help 가 머리말만 출력한다 — 코드가 새지 않고(옛 `2,40p`) 옵션이 다 보인다."""
    proc = subprocess.run(
        [str(SCRIPT), "--help"], capture_output=True, text=True, cwd=REPO_ROOT, timeout=30
    )
    assert proc.returncode == 0
    assert "set -euo" not in proc.stdout
    assert "PAPER_TABLES" not in proc.stdout
    for flag in ("--backup", "--vacuum", "--export-dir", "--no-backup", "--dry-run"):
        assert flag in proc.stdout, flag


def test_missing_db_fails(tmp_path: Path) -> None:
    """없는 DB 경로를 주면 조용히 통과하지 않고 실패한다(0이 아닌 종료 코드)."""
    proc = _run(tmp_path / "does-not-exist.db", "--yes", "--no-restart", "--no-backup")
    assert proc.returncode != 0
