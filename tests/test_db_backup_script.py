"""scripts/db-backup.sh 회귀 테스트 (WAN-318 §4).

이 도구의 핵심 계약은 **"검증에 실패한 산출물은 정상 백업의 이름을 갖지 못한다"** 이다 —
2026-08-17 서버 사고에서 `sqlite3 .backup` 이 doctor 와 경합하다 끊겼는데 **잘린 1.5GB
파일이 4.0GB 백업과 똑같은 이름으로 남았다**. 나중에 그걸 복구본으로 쓰면 DB의 3분의 2가
조용히 사라진다(WAN-194 「실패가 성공과 같은 모양」 계열).

테스트는 실제 bash 스크립트를 서브프로세스로 돌린다(계약이 스크립트 안에 있으므로) —
파이썬 표준 `sqlite3` 모듈로 임시 DB를 만들고, `sqlite3` CLI 와 `bash` 가 있을 때만 돈다.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "db-backup.sh"

_MISSING = [tool for tool in ("bash", "sqlite3") if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(bool(_MISSING), reason=f"필요한 CLI 없음: {', '.join(_MISSING)}")


def _make_db(path: Path, rows: int = 5_000) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE ohlcv (v TEXT)")
        conn.executemany("INSERT INTO ohlcv VALUES (?)", [("x" * 200,) for _ in range(rows)])
        conn.commit()
    finally:
        conn.close()


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        # 유닛 확인은 systemd 가 있는 서버에서만 뜻이 있고, 여기서는 백업 자체를 잰다.
        check=False,
    )


def test_backup_is_created_and_matches_source(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    out = tmp_path / "backup.db"
    _make_db(db)

    proc = _run("--db", str(db), "--out", str(out), "--allow-running")

    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    # 백업본이 원본과 같은 행 수를 갖는다(라벨이 아니라 내용으로).
    with sqlite3.connect(out) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0] == 5_000


def test_no_intermediate_name_survives_success(tmp_path: Path) -> None:
    """성공하면 임시·실패 산출물이 남지 않는다 — 디렉터리에 백업 하나만 있어야 한다."""
    db = tmp_path / "ohlcv.db"
    out = tmp_path / "backup.db"
    _make_db(db)

    assert _run("--db", str(db), "--out", str(out), "--allow-running").returncode == 0

    leftovers = sorted(p.name for p in tmp_path.iterdir())
    assert "backup.db.inprogress" not in leftovers
    assert "backup.db.FAILED" not in leftovers


def test_truncated_file_fails_verification(tmp_path: Path) -> None:
    """사고 재현 — 잘린 백업은 `--verify-only` 가 **실패로** 판정한다(종료 코드 1).

    이 테스트가 이 스크립트의 존재 이유다: 크기만 봐서는 "그냥 작은 DB" 와 구분되지 않고,
    파일 이름만 봐서는 정상 백업과 글자 그대로 같다.
    """
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    truncated = tmp_path / "truncated.db"
    raw = db.read_bytes()
    truncated.write_bytes(raw[: len(raw) // 2])

    proc = _run("--verify-only", str(truncated))

    assert proc.returncode == 1
    assert "검증 실패" in proc.stderr
    # 사람이 그 파일로 복구하지 않게 명시적으로 말린다.
    assert "복구본으로 쓰지 마세요" in proc.stdout


def test_verify_only_accepts_a_good_backup(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    out = tmp_path / "backup.db"
    _make_db(db)
    assert _run("--db", str(db), "--out", str(out), "--allow-running").returncode == 0

    proc = _run("--verify-only", str(out))

    assert proc.returncode == 0
    assert "정상 백업입니다" in proc.stdout


def test_failed_backup_never_takes_the_final_name(tmp_path: Path) -> None:
    """`.backup` 이 실패하면 최종 이름은 생기지 않는다(빈·잘린 파일이 백업으로 오인되지 않게)."""
    db = tmp_path / "ohlcv.db"
    _make_db(db)
    out = tmp_path / "no-such-dir" / "backup.db"

    proc = _run("--db", str(db), "--out", str(out), "--allow-running")

    assert proc.returncode == 1
    assert not out.exists()
    assert "최종 이름" in proc.stderr


def test_leftover_journal_is_treated_as_interruption(tmp_path: Path) -> None:
    """중단 흔적(`-journal`)이 옆에 남아 있으면 그 백업은 신뢰하지 않는다(사고 당시 모양)."""
    db = tmp_path / "ohlcv.db"
    out = tmp_path / "backup.db"
    _make_db(db)
    assert _run("--db", str(db), "--out", str(out), "--allow-running").returncode == 0
    (tmp_path / "backup.db-journal").write_bytes(b"\x00" * 512)

    proc = _run("--verify-only", str(out))

    assert proc.returncode == 1
    assert "저널이 남아" in proc.stderr


def test_interrupted_backup_is_quarantined_as_FAILED(tmp_path: Path) -> None:
    """🚨 이 이슈의 사고 그대로 — `.backup` 이 종료 코드 0 을 내면서 **잘린 파일**을 남기면.

    서버에서 실제로 그랬다: 산출물은 있는데 내용이 3분의 1이었고, 이름은 정상 백업과 글자
    그대로 같았다. 그래서 스크립트는 산출물을 먼저 임시 이름으로 받고, 검증에 실패하면
    `.FAILED` 로 격리한 뒤 **최종 이름을 만들지 않는다**.

    실제 `.backup` 은 끊기게 만들 수 없으므로 `PATH` 앞에 그 상황을 흉내 내는 `sqlite3`
    껍데기를 둔다 — 검증 PRAGMA 는 진짜 sqlite3 로 넘긴다.
    """
    db = tmp_path / "ohlcv.db"
    out = tmp_path / "backup.db"
    _make_db(db)

    real_sqlite3 = shutil.which("sqlite3")
    assert real_sqlite3 is not None
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "sqlite3"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${2:-}" == .backup* ]]; then\n'
        "  target=$(printf '%s' \"$2\" | sed \"s/^\\.backup '//; s/'$//\")\n"
        '  head -c 4096 "$1" > "$target"\n'  # 첫 페이지만 = 헤더는 전체 크기를 말한다
        "  exit 0\n"
        "fi\n"
        f'exec {real_sqlite3} "$@"\n'
    )
    shim.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{shim_dir}:{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--db", str(db), "--out", str(out), "--allow-running"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 1
    assert not out.exists(), "잘린 산출물이 정상 백업의 이름을 가져갔다"
    assert (tmp_path / "backup.db.FAILED").is_file(), "실패 산출물이 격리되지 않았다"
    assert "검증 실패" in proc.stderr


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    proc = _run("--db", str(tmp_path / "does-not-exist.db"), "--allow-running")
    assert proc.returncode == 1
    assert "DB 파일이 없습니다" in proc.stderr


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    out = tmp_path / "backup.db"
    _make_db(db)

    proc = _run("--db", str(db), "--out", str(out), "--allow-running", "--dry-run")

    assert proc.returncode == 0
    assert not out.exists()
    assert not (tmp_path / "backup.db.inprogress").exists()


def test_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & 0o111, "db-backup.sh 에 실행 권한이 없다"
