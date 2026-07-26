"""DB 무결성·위생 점검 테스트 (WAN-194 §2·§4·§5).

WAN-194의 판별 논리를 동작으로 고정한다: 복구 산출물 탐지, 빈 장부 판별(다른 테이블이
성한 채 장부만 비면 광범위 유실이 아니다), 처분 미기록 체결, 그리고 파괴적 정리가
**옵트인이며 VACUUM을 하지 않는다**는 것.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data.integrity import (
    RECOVERY_ARTIFACT_TABLES,
    drop_recovery_artifacts,
    inspect,
    render_report,
)
from live.order_journal import OrderJournal


def _journal_db(path: Path) -> None:
    """장부 스키마를 만든다(OrderJournal이 소유한 스키마를 재구현하지 않는다)."""
    journal = OrderJournal(path)
    journal.close()


def _add_recovery_artifact(path: Path, rows: int = 3) -> None:
    """SQLite `.recover`가 남기는 `lost_and_found`를 흉내 낸다(스키마도 그쪽 모양)."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "CREATE TABLE lost_and_found ("
            " rootpgno INTEGER, pgno INTEGER, nfield INTEGER, id INTEGER, c0 ANY)"
        )
        conn.executemany(
            "INSERT INTO lost_and_found VALUES (?, ?, ?, ?, ?)",
            [(1, i, 5, i, "x") for i in range(rows)],
        )
    conn.close()


def test_inspect_missing_file_raises(tmp_path: Path) -> None:
    """없는 경로를 조용히 새 DB로 만들지 않는다 — 경로 오타가 초록불로 보이면 안 된다."""
    with pytest.raises(FileNotFoundError):
        inspect(tmp_path / "nope.db")


def test_inspect_clean_db_is_healthy(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)  # 장부가 비지 않도록 세션 1행.
    journal.close()
    assert session == 1

    # `open_positions`·`paper_trades`는 이 DB에 아예 없다(테이블 부재는 빈 장부가 아니다).
    report = inspect(db)
    assert report.quick_check == ["ok"]
    assert report.quick_check_ok
    assert report.recovery_artifacts == []
    assert report.orphan_fills == []
    assert [t.name for t in report.empty_ledgers] == ["live_limit_orders"]
    assert not report.healthy  # 빈 장부가 있으므로 경고 상태다.


def test_inspect_detects_recovery_artifact(tmp_path: Path) -> None:
    """`lost_and_found`의 존재 자체가 "이 DB는 복구됐다"는 증거로 잡힌다."""
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    _add_recovery_artifact(db, rows=7)

    report = inspect(db, quick_check=False)
    artifacts = report.recovery_artifacts
    assert [(a.name, a.rows) for a in artifacts] == [("lost_and_found", 7)]
    assert not report.healthy
    assert report.quick_check == []  # 건너뛰면 빈 리스트다.
    assert report.quick_check_ok  # 미확인은 실패로 세지 않는다(리포트가 그렇게 적는다).

    text = render_report(report)
    assert "복구 산출물" in text
    assert "7행" in text
    assert "quick_check 건너뜀" in text or "건너뜀" in text


def test_inspect_flags_orphan_fills_and_respects_since(tmp_path: Path) -> None:
    """처분 미기록 체결이 잡히고 `orphan_since_ms`로 도입 이전 기록을 가를 수 있다."""
    db = tmp_path / "ohlcv.db"
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    conn = journal._conn  # noqa: SLF001 — 임의 체결 시각을 심는 픽스처.
    with conn:
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
            " placed_ms, status, fill_ms, fill_price, stop_price)"
            " VALUES (?, 'LINK/USDT:USDT', '15m', 'bullish', 10, 'filled', 1000, 8.35, 8.33)",
            (session,),
        )
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
            " placed_ms, status, fill_ms, fill_price, stop_price, entry_status)"
            " VALUES (?, 'BTC/USDT:USDT', '1h', 'bullish', 20, 'filled', 2000, 100.0, 90.0,"
            " 'entered')",
            (session,),
        )
    journal.close()

    report = inspect(db, quick_check=False)
    assert [o.symbol for o in report.orphan_fills] == ["LINK/USDT:USDT"]
    assert report.orphan_fills[0].fill_price == pytest.approx(8.35)

    # 창을 체결 이후로 자르면 판별 불가 행이 빠진다.
    later = inspect(db, quick_check=False, orphan_since_ms=1_500)
    assert later.orphan_fills == []

    text = render_report(report)
    assert "처분 미기록" in text
    assert "LINK/USDT:USDT" in text


def test_orphan_fills_tolerates_legacy_schema(tmp_path: Path) -> None:
    """`entry_status` 열이 없는 옛 DB에서도 점검이 죽지 않는다(조용히 빈 결과)."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "CREATE TABLE live_limit_orders (id INTEGER PRIMARY KEY, status TEXT, fill_ms INTEGER)"
        )
        conn.execute("INSERT INTO live_limit_orders (status, fill_ms) VALUES ('filled', 1)")
    conn.close()

    report = inspect(db, quick_check=False)
    assert report.orphan_fills == []


def test_drop_recovery_artifacts_is_opt_in_and_leaves_size(tmp_path: Path) -> None:
    """정리는 테이블만 지우고 **파일 크기를 줄이지 않는다**(VACUUM은 사람이 한다).

    자동 VACUUM은 DB를 독점 락하고 같은 크기의 임시 파일을 쓰므로 러너·수집기가 붙은
    서버에서 코드가 스스로 돌 일이 아니다 — 회수 가능 공간만 보고한다.
    """
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    _add_recovery_artifact(db, rows=500)
    before = db.stat().st_size

    dropped = drop_recovery_artifacts(db)
    assert dropped == ["lost_and_found"]
    assert db.stat().st_size == before  # 페이지가 프리리스트로 갔을 뿐이다.

    report = inspect(db, quick_check=False)
    assert report.recovery_artifacts == []
    assert report.space.freelist_count > 0
    assert report.space.reclaimable_bytes > 0

    # 두 번 불러도 안전하다(없으면 아무것도 안 한다).
    assert drop_recovery_artifacts(db) == []


def test_drop_recovery_artifacts_keeps_app_tables(tmp_path: Path) -> None:
    """앱 테이블은 절대 지우지 않는다 — 정리가 데이터 유실이 되면 안 된다."""
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    _add_recovery_artifact(db)
    journal = OrderJournal(db)
    journal.start_session(now_ms=0)
    journal.close()

    drop_recovery_artifacts(db)
    report = inspect(db, quick_check=False)
    names = {t.name for t in report.tables}
    assert "live_limit_orders" in names
    assert "live_runner_sessions" in names
    assert not names & RECOVERY_ARTIFACT_TABLES


def test_space_report_reads_wal_and_disk(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    report = inspect(db, quick_check=False)
    space = report.space
    assert space.page_size > 0
    assert space.page_count > 0
    assert space.journal_mode == "wal"  # configure_connection이 WAL을 건다.
    assert space.disk_total_bytes > 0
    assert 0.0 < space.disk_free_fraction <= 1.0
    assert "공간·저널" in render_report(report)
