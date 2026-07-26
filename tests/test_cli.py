"""cli.main 실행 CLI 테스트 (WAN-31) — 인자 라우팅·상태 포맷·명령 배선."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from cli.main import (
    build_parser,
    cmd_collect,
    cmd_doctor,
    cmd_live,
    cmd_status,
    format_status,
)
from common.heartbeat import HeartbeatStore
from config.settings import Settings
from dashboard.health_data import build_health_view
from data.models import Candle
from data.repair import RepairStateStore, RepairSummary
from data.storage import OhlcvStore
from live.order_journal import OrderJournal

_HOUR = 3_600_000
_NOW = 1_000 * _HOUR


def _settings(tmp_path: Path) -> Settings:
    """테스트용 Settings(임시 경로). .env/환경변수와 무관하게 명시 값만 쓴다."""
    return Settings(
        db_path=str(tmp_path / "ohlcv.db"),
        live_runtime_state_path=str(tmp_path / "runtime.json"),
        collector_heartbeat_path=str(tmp_path / "hb.json"),
    )


def _seed_db(db_path: str) -> None:
    with OhlcvStore(db_path) as store:
        store.upsert_candles(
            Candle("BTC/USDT:USDT", "1h", _NOW - i * _HOUR, 100.0, 105.0, 95.0, 100.0, 1.0)
            for i in range(3)
        )


# --- 인자 파싱/라우팅 --------------------------------------------------------


def test_parser_routes_subcommands() -> None:
    parser = build_parser()
    assert parser.parse_args(["collect"]).func is cmd_collect
    assert parser.parse_args(["collect", "--once"]).once is True
    assert parser.parse_args(["live", "--dry-run"]).func is cmd_live
    assert parser.parse_args(["live", "--once", "--test-message"]).test_message is True
    assert parser.parse_args(["status"]).func is cmd_status


def test_parser_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


# --- 상태 요약 포맷 ----------------------------------------------------------


def test_format_status_reports_running_processes(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ohlcv.db")
    _seed_db(db_path)
    hb_path = tmp_path / "hb.json"
    HeartbeatStore(hb_path, label="collector", now_ms=lambda: _NOW - 30_000).beat()

    view = build_health_view(
        db_path,
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=str(hb_path),
        collector_heartbeat_interval_seconds=60,
        now_ms=_NOW,
    )
    text = format_status(view)

    assert "AlphaBlock 운영 상태" in text
    assert "수집기:" in text
    assert "러너:" in text
    # 수집기 하트비트가 신선 → 미실행 안내가 아니어야 한다.
    assert "[OK]" in text
    # 러너는 미실행 흔적 없음 → 안내 문구.
    assert "alphablock live" in text


def test_format_status_reports_idle_when_nothing_ran(tmp_path: Path) -> None:
    view = build_health_view(
        str(tmp_path / "empty.db"),
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=str(tmp_path / "no_hb.json"),
        now_ms=_NOW,
    )
    text = format_status(view)
    assert "alphablock collect" in text
    assert "저장된 OHLCV 없음" in text


def test_format_status_shows_the_window_a_windowed_repair_looked_at(tmp_path: Path) -> None:
    """창을 좁힌 점검의 「갭 없음」이 「전 구간 무결」로 읽히지 않게 창을 함께 찍는다.

    수집기 시작 점검은 최근 창만 본다(WAN-187) — 그 사실이 화면에서 사라지면
    WAN-156/157과 같은 종류의 침묵이 된다.
    """
    state_path = tmp_path / "repair_state.json"
    RepairStateStore(state_path).save(
        RepairSummary(ran_at_ms=_NOW, series=[], lookback_ms=7 * 86_400_000)
    )
    view = build_health_view(
        str(tmp_path / "empty.db"),
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=str(tmp_path / "no_hb.json"),
        repair_state_path=str(state_path),
        now_ms=_NOW,
    )
    assert "최근 7일 창" in format_status(view)

    # 전 구간 점검(창 없음)에는 그 표기가 붙지 않는다.
    RepairStateStore(state_path).save(RepairSummary(ran_at_ms=_NOW, series=[]))
    view = build_health_view(
        str(tmp_path / "empty.db"),
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=str(tmp_path / "no_hb.json"),
        repair_state_path=str(state_path),
        now_ms=_NOW,
    )
    assert "창" not in format_status(view)


# --- 명령 배선(외부 호출은 스텁) --------------------------------------------


def test_cmd_status_prints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _settings(tmp_path)
    _seed_db(settings.db_path)
    rc = cmd_status(argparse.Namespace(bar_count=False), settings)
    assert rc == 0
    out = capsys.readouterr().out
    assert "AlphaBlock 운영 상태" in out


def test_cmd_status_omits_bar_count_by_default_and_shows_it_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """봉 수는 `--bar-count`로 켤 때만 찍힌다(WAN-186).

    기본 실행이 시리즈마다 `COUNT(*)`를 돌면 6년 DB에서 status가 멈춘다 —
    라벨이 아니라 **출력**으로 고정한다.
    """
    settings = _settings(tmp_path)
    _seed_db(settings.db_path)

    assert cmd_status(argparse.Namespace(bar_count=False), settings) == 0
    assert "봉)" not in capsys.readouterr().out

    assert cmd_status(argparse.Namespace(bar_count=True), settings) == 0
    assert "봉)" in capsys.readouterr().out


def test_cmd_collect_invokes_run_collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls: dict[str, Any] = {}

    async def fake_run_collector(
        s: Settings, *, run_stream: bool, repair_on_start: bool | None
    ) -> None:
        calls["settings"] = s
        calls["run_stream"] = run_stream
        calls["repair_on_start"] = repair_on_start

    monkeypatch.setattr("data.collector.run_collector", fake_run_collector)
    rc = cmd_collect(argparse.Namespace(once=True, repair_on_start=None), settings)
    assert rc == 0
    assert calls["run_stream"] is False  # --once → 스트림 없음
    assert calls["settings"] is settings
    assert calls["repair_on_start"] is None  # 미지정 → 설정값 위임


def test_cmd_live_invokes_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    calls: dict[str, Any] = {}

    def fake_run(s: Settings, *, once: bool, dry_run: bool, test_message: bool) -> None:
        calls.update(once=once, dry_run=dry_run, test_message=test_message)

    monkeypatch.setattr("live.runner.run_signal_runner", fake_run)
    rc = cmd_live(argparse.Namespace(once=True, dry_run=True, test_message=False), settings)
    assert rc == 0
    assert calls == {"once": True, "dry_run": True, "test_message": False}


# --- doctor (WAN-194) --------------------------------------------------------


def test_parser_routes_doctor() -> None:
    parser = build_parser()
    args = parser.parse_args(["doctor"])
    assert args.func is cmd_doctor
    # 파괴적 옵션은 기본이 꺼져 있다.
    assert args.drop_recovery_artifacts is False
    assert args.skip_quick_check is False
    assert args.orphans_since is None
    assert parser.parse_args(["doctor", "--skip-quick-check"]).skip_quick_check is True


def test_cmd_doctor_exit_code_signals_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """이상이 있으면 종료 코드 1 — cron·감시에서 그대로 쓸 수 있어야 한다."""
    import sqlite3

    settings = _settings(tmp_path)
    journal = OrderJournal(settings.db_path)
    journal.start_session(now_ms=0)
    journal.close()
    conn = sqlite3.connect(settings.db_path)
    with conn:  # 복구 산출물 = 이상 신호.
        conn.execute("CREATE TABLE lost_and_found (rootpgno INTEGER, pgno INTEGER)")
    conn.close()

    args = argparse.Namespace(
        db=None,
        skip_quick_check=True,
        orphans_since=None,
        drop_recovery_artifacts=False,
    )
    assert cmd_doctor(args, settings) == 1
    assert "lost_and_found" in capsys.readouterr().out


def test_cmd_doctor_drop_is_explicit_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--drop-recovery-artifacts`가 실제로 지우고, VACUUM은 사람 몫임을 알린다."""
    import sqlite3

    settings = _settings(tmp_path)
    journal = OrderJournal(settings.db_path)
    journal.start_session(now_ms=0)
    journal.close()
    conn = sqlite3.connect(settings.db_path)
    with conn:
        conn.execute("CREATE TABLE lost_and_found (rootpgno INTEGER)")
    conn.close()

    args = argparse.Namespace(
        db=None,
        skip_quick_check=True,
        orphans_since=None,
        drop_recovery_artifacts=True,
    )
    cmd_doctor(args, settings)
    out = capsys.readouterr().out
    assert "복구 산출 테이블 삭제" in out
    assert "VACUUM" in out
    # 삭제 후 같은 실행의 리포트에는 산출물이 없다.
    assert "이 DB에 `.recover` 흔적이 없다" in out


def test_cmd_doctor_orphans_since_is_kst(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--orphans-since`는 KST 자정 기준이다(운영 출력 시간대 규약, WAN-172)."""
    settings = _settings(tmp_path)
    journal = OrderJournal(settings.db_path)
    session = journal.start_session(now_ms=0)
    # 2026-07-26 00:30 KST = 2026-07-25 15:30 UTC.
    fill_ms = 1784993400000
    conn = journal._conn  # noqa: SLF001
    with conn:
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
            " placed_ms, status, fill_ms) VALUES (?, 'LINK/USDT:USDT', '15m', 'bullish',"
            " 1, 'filled', ?)",
            (session, fill_ms),
        )
    journal.close()

    base = {"db": None, "skip_quick_check": True, "drop_recovery_artifacts": False}
    # 그 날 자정(KST) 이후로 자르면 00:30 체결이 포함된다.
    cmd_doctor(argparse.Namespace(orphans_since="2026-07-26", **base), settings)
    assert "LINK/USDT:USDT" in capsys.readouterr().out
    # 다음 날로 자르면 빠진다.
    cmd_doctor(argparse.Namespace(orphans_since="2026-07-27", **base), settings)
    assert "모든 체결에 진입/거부 처분이 남아 있다" in capsys.readouterr().out
