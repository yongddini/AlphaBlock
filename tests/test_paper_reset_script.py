"""scripts/paper-reset.sh 회귀 테스트 (WAN-218).

이 도구의 핵심 안전 계약은 **"시세 데이터는 절대 건드리지 않는다"** 이다 — DB 파일 하나에
시세(`ohlcv`·`funding_rate`)와 거래기록이 함께 들어 있으므로, 초기화가 거래기록 4개
테이블만 비우고 시세·백테스트 테이블은 그대로 두는지를 **라벨이 아니라 동작으로** 잠근다.

테스트는 실제 bash 스크립트를 서브프로세스로 돌린다(계약이 스크립트 안에 있으므로) —
파이썬 표준 `sqlite3` 모듈로 임시 DB를 만들고, 스크립트가 요구하는 `sqlite3` CLI와 `bash`가
있을 때만 실행한다(없으면 스킵).
"""

from __future__ import annotations

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


def _run(db: Path, *args: str) -> subprocess.CompletedProcess[str]:
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

    proc = _run(db, "--yes", "--no-restart", "--no-backup")
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

    proc = _run(db, "--yes", "--no-restart", "--no-backup", "--include-backtest")
    assert proc.returncode == 0, proc.stderr

    for table in PAPER_TABLES + BACKTEST_TABLES:
        assert _count(db, table) == 0, f"{table} 가 비워지지 않았다"
    for table in PROTECTED_TABLES:
        assert _count(db, table) == before[table], f"{table}(시세) 가 건드려졌다"


def test_backup_is_revertible(tmp_path: Path) -> None:
    """백업(기본 켜짐)이 삭제 전 원본을 그대로 담는다 — WAN-195 되돌리기 교훈."""
    db = tmp_path / "ohlcv.db"
    before = _make_db(db, with_backtest=True)

    proc = _run(db, "--yes", "--no-restart")
    assert proc.returncode == 0, proc.stderr

    backups = list(tmp_path.glob("ohlcv.db.bak-*"))
    assert len(backups) == 1, f"백업 파일이 정확히 하나여야 한다: {backups}"
    backup = backups[0]
    # 백업은 삭제 이전 상태 — 거래기록 행이 그대로 살아 있어야 되돌릴 수 있다.
    for table, expected in before.items():
        assert _count(backup, table) == expected, f"백업의 {table} 가 원본과 다르다"
    # 원본은 실제로 비워졌다(백업이 dry-run 이 아님을 확인).
    assert _count(db, "paper_trades") == 0


def test_missing_db_fails(tmp_path: Path) -> None:
    """없는 DB 경로를 주면 조용히 통과하지 않고 실패한다(0이 아닌 종료 코드)."""
    proc = _run(tmp_path / "does-not-exist.db", "--yes", "--no-restart", "--no-backup")
    assert proc.returncode != 0
