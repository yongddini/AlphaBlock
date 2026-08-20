"""cli.main 실행 CLI 테스트 (WAN-31) — 인자 라우팅·상태 포맷·명령 배선."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from cli.main import (
    _doctor_alert_text,
    _notify_doctor_failure,
    build_parser,
    cmd_collect,
    cmd_doctor,
    cmd_live,
    cmd_status,
    cmd_stop_width,
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
    # 파괴적 옵션·경고 옵션은 기본이 꺼져 있다.
    assert args.drop_recovery_artifacts is False
    assert args.skip_quick_check is False
    assert args.orphans_since is None
    assert args.notify_on_failure is False
    assert parser.parse_args(["doctor", "--skip-quick-check"]).skip_quick_check is True
    assert parser.parse_args(["doctor", "--notify-on-failure"]).notify_on_failure is True


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
        salvage_ohlcv=None,
        dry_run=False,
        force=False,
        notify_on_failure=False,
    )
    assert cmd_doctor(args, settings) == 1
    assert "lost_and_found" in capsys.readouterr().out


def test_cmd_doctor_exit_zero_when_only_open_positions_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """WAN-321 §1 완료기준 1 — 서버에서 매시간 울리던 그 상태가 **종료 코드 0**이다.

    실측 상태를 그대로 재현한다: 체결은 처분까지 기록돼 있고(`orphan_fills` 0), 누적 장부는
    성한데 `open_positions`만 0행. 옛 규칙은 여기서 1을 내 `systemctl --failed`를 상시
    빨갛게 만들었고, 그 상시 빨간불이 진짜 이상을 가렸다.
    """
    from live.limit_orders import LimitFill, PendingLimitOrder
    from paper.store import PaperTradeRecord, PaperTradeStore
    from strategy.models import OrderBlockDirection, SignalExitReason
    from strategy.realtime_rsi import RealtimeRsi

    settings = _settings(tmp_path)
    journal = OrderJournal(settings.db_path)
    session = journal.start_session(now_ms=0)
    journal_id = journal.record_placed(
        PendingLimitOrder(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            limit_price=100.0,
            stop_price=90.0,
            rsi_state=RealtimeRsi(length=3),
            placed_ms=1_000,
        ),
        session_id=session,
        zone_start_time=0,
        zone_confirmed_time=0,
    )
    journal.record_filled(
        journal_id,
        LimitFill(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            price=100.0,
            time=61_000,
            rsi=25.0,
            stop_price=90.0,
            take_profit_price=115.0,
            penetration_bps=0.0,
            waited_ms=60_000,
        ),
    )
    journal.record_entry_result(journal_id, entered=True)  # 처분 기록 = 유실이 아니다.
    journal.close()

    store = PaperTradeStore(settings.db_path)
    store.upsert_record(
        PaperTradeRecord(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=0,
            entry_price=100.0,
            exit_time=60_000,
            exit_price=115.0,
            reason=SignalExitReason.TAKE_PROFIT,
            gross_pct=15.0,
            fee_pct=0.04,
            funding_pct=0.0,
            net_pct=14.96,
        )
    )
    store.close()  # 포지션은 닫혔다 = `open_positions` 0행.

    assert cmd_doctor(_doctor_args(), settings) == 0
    out = capsys.readouterr().out
    assert "open_positions" in out  # 리포트에는 계속 찍는다(정보로).


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
        salvage_ohlcv=None,
        dry_run=False,
        force=False,
        notify_on_failure=False,
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

    base = {
        "db": None,
        "skip_quick_check": True,
        "drop_recovery_artifacts": False,
        "salvage_ohlcv": None,
        "dry_run": False,
        "force": False,
        "notify_on_failure": False,
    }
    # 그 날 자정(KST) 이후로 자르면 00:30 체결이 포함된다.
    cmd_doctor(argparse.Namespace(orphans_since="2026-07-26", **base), settings)
    assert "LINK/USDT:USDT" in capsys.readouterr().out
    # 다음 날로 자르면 빠진다.
    cmd_doctor(argparse.Namespace(orphans_since="2026-07-27", **base), settings)
    assert "모든 체결에 진입/거부 처분이 남아 있다" in capsys.readouterr().out


# --- doctor 이상 시 텔레그램 경고 (WAN-185) ----------------------------------


class _CaptureTelegram:
    """send_message 를 잡아 두는 가짜 텔레그램 클라이언트."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []
        self.parse_modes: list[str | None] = []

    def send_message(self, text: str, *, parse_mode: str | None = "Markdown") -> bool:
        self.sent.append(text)
        self.parse_modes.append(parse_mode)
        return self.ok


def _Cat(name: str) -> Any:
    return type("Cat", (), {"name": name})()


def test_doctor_alert_text_lists_tripped_categories() -> None:
    """경고 문구는 healthy 를 무너뜨린 카테고리만 짧게 나열한다."""

    report = type(
        "R",
        (),
        {
            "db_path": "/srv/ohlcv.db",
            "quick_check_ok": False,
            "recovery_artifacts": [_Cat("lost_and_found")],
            "orphan_fills": [object(), object()],
            "empty_cumulative_ledgers": [_Cat("paper_trades")],
        },
    )()
    text = _doctor_alert_text(report, "orderblock")

    assert "orderblock" in text
    assert "/srv/ohlcv.db" in text
    assert "quick_check 손상" in text
    assert "복구 산출물 1개" in text
    assert "처분 미기록 체결 2건" in text
    assert "빈 누적 장부(paper_trades)" in text


def test_doctor_alert_text_carries_no_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    """경고문에 레거시 Markdown 서식이 없다 — 있으면 400으로 거부된다(WAN-321 §2).

    서버 실측에서 파서를 깨뜨린 것이 `open_positions`의 **밑줄**이었다. 테이블 이름은
    경고문에 실릴 수밖에 없으므로 서식 자체를 쓰지 않는다(라벨이 아니라 동작으로 고정).
    """
    report = type(
        "R",
        (),
        {
            "db_path": "/srv/data/ohlcv.db",
            "quick_check_ok": True,
            "recovery_artifacts": [],
            "orphan_fills": [object()],
            "empty_cumulative_ledgers": [_Cat("paper_trades")],
        },
    )()
    text = _doctor_alert_text(report, "orderblock")

    assert "`" not in text
    assert "*" not in text
    # 밑줄이 든 이름은 그대로 실린다 — 서식을 안 쓰므로 문제가 되지 않는다.
    assert "paper_trades" in text


def test_notify_doctor_failure_sends_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """doctor 경고는 평문으로 보낸다(WAN-321 §2) — 파싱 자체가 없어야 안 깨진다."""
    client = _CaptureTelegram()
    monkeypatch.setattr("common.telegram.build_telegram_client", lambda _s: client)

    report = type(
        "R",
        (),
        {
            "db_path": "/srv/ohlcv.db",
            "quick_check_ok": True,
            "recovery_artifacts": [],
            "orphan_fills": [object()],
            "empty_cumulative_ledgers": [],
        },
    )()
    _notify_doctor_failure(report, Settings())

    assert client.parse_modes == [None]


def test_notify_doctor_failure_escalates_send_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """전송이 실패하면 ERROR로 올리고 경고 본문을 남긴다(WAN-321 §2).

    「경보를 못 보냈다」가 WARNING 한 줄로 묻히면 두 다리(systemctl·텔레그램)가 모두
    죽은 것을 아무도 모른다.
    """
    client = _CaptureTelegram(ok=False)
    monkeypatch.setattr("common.telegram.build_telegram_client", lambda _s: client)

    report = type(
        "R",
        (),
        {
            "db_path": "/srv/ohlcv.db",
            "quick_check_ok": False,
            "recovery_artifacts": [],
            "orphan_fills": [],
            "empty_cumulative_ledgers": [],
        },
    )()
    with caplog.at_level("ERROR"):
        _notify_doctor_failure(report, Settings())

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors, "전송 실패가 ERROR로 남아야 한다"
    # 본문이 함께 남아야 로그만 보고도 무슨 이상이었는지 알 수 있다.
    assert "quick_check 손상" in errors[0].getMessage()


def test_notify_doctor_failure_sends_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CaptureTelegram()
    monkeypatch.setattr("common.telegram.build_telegram_client", lambda _s: client)

    report = type(
        "R",
        (),
        {
            "db_path": "/srv/ohlcv.db",
            "quick_check_ok": True,
            "recovery_artifacts": [_Cat("lost_and_found")],
            "orphan_fills": [],
            "empty_cumulative_ledgers": [],
        },
    )()
    _notify_doctor_failure(report, Settings())

    assert len(client.sent) == 1
    assert "복구 산출물 1개" in client.sent[0]


def test_notify_doctor_failure_logs_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """텔레그램 미설정이면 던지지 않고 로그로만 남긴다(경고 유실 방지)."""
    monkeypatch.setattr("common.telegram.build_telegram_client", lambda _s: None)

    report = type(
        "R",
        (),
        {
            "db_path": "/srv/ohlcv.db",
            "quick_check_ok": False,
            "recovery_artifacts": [],
            "orphan_fills": [],
            "empty_cumulative_ledgers": [],
        },
    )()
    with caplog.at_level("WARNING"):
        _notify_doctor_failure(report, Settings())  # 예외 없이 통과해야 한다.
    assert any("미전송" in r.message for r in caplog.records)


def _unhealthy_db(settings: Settings) -> None:
    import sqlite3

    journal = OrderJournal(settings.db_path)
    journal.start_session(now_ms=0)
    journal.close()
    conn = sqlite3.connect(settings.db_path)
    with conn:  # 복구 산출물 = 이상 신호.
        conn.execute("CREATE TABLE lost_and_found (rootpgno INTEGER)")
    conn.close()


def _doctor_args(**overrides: Any) -> argparse.Namespace:
    base = {
        "db": None,
        "skip_quick_check": True,
        "orphans_since": None,
        "drop_recovery_artifacts": False,
        "salvage_ohlcv": None,
        "dry_run": False,
        "force": False,
        "notify_on_failure": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_doctor_notifies_only_with_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--notify-on-failure 없이는 이상이어도 경고를 보내지 않는다."""
    import importlib

    # `cli` 패키지가 `main` 함수를 재노출해 `cli.main` 이름이 모듈이 아니라 그 함수로
    # 섀도잉된다 — 실제 모듈 객체는 importlib 로 가져온다.
    cli_main = importlib.import_module("cli.main")

    settings = _settings(tmp_path)
    _unhealthy_db(settings)
    calls: list[str] = []
    monkeypatch.setattr(cli_main, "_notify_doctor_failure", lambda r, s: calls.append(r.db_path))

    assert cmd_doctor(_doctor_args(notify_on_failure=False), settings) == 1
    assert calls == []  # 플래그 없으면 조용하다.

    assert cmd_doctor(_doctor_args(notify_on_failure=True), settings) == 1
    assert len(calls) == 1  # 플래그 + 이상 → 한 번 보낸다.


# --------------------------------------------------------------------------- #
# 부분 봉 스캔 출력 (WAN-327)
# --------------------------------------------------------------------------- #


def test_format_partial_bar_scan_splits_damage_from_noise() -> None:
    """손상과 거래량 노이즈를 갈라 찍는다 — 한 수로 뭉치면 진짜 부분 봉이 묻힌다."""
    from cli.main import format_partial_bar_scan
    from data.partial_bars import BarDiscrepancy, SeriesScan

    damaged = BarDiscrepancy(
        symbol="BTC/USDT:USDT",
        timeframe="4h",
        open_time=1_784_000_000_000,
        kind="partial",
        resampled_volume=100.0,
        stored_volume=45.0,
        price_fields=("high", "close"),
        max_price_bp=29.8,
    )
    noise = BarDiscrepancy(
        symbol="BTC/USDT:USDT",
        timeframe="4h",
        open_time=1_784_100_000_000,
        kind="volume_noise",
        resampled_volume=100.0,
        stored_volume=100.2,
        price_fields=(),
        max_price_bp=0.0,
    )
    text = format_partial_bar_scan(
        [
            SeriesScan(
                symbol="BTC/USDT:USDT",
                timeframe="4h",
                source_timeframe="1m",
                compared=13_139,
                discrepancies=[damaged, noise],
            )
        ]
    )
    assert "손상 1봉" in text
    assert "노이즈 1봉" in text
    assert "partial" in text
    assert "45.0" in text  # 거래량 비율이 보인다(판정자)


def test_format_partial_bar_scan_clean_says_so() -> None:
    from cli.main import format_partial_bar_scan
    from data.partial_bars import SeriesScan

    text = format_partial_bar_scan(
        [SeriesScan(symbol="BTC/USDT:USDT", timeframe="4h", source_timeframe="1m", compared=10)]
    )
    assert "손상 봉 없음" in text
    assert "OK" in text


# --- WAN-333/335: stop-width — 셋업 행 · 좌표 좁히기 · 캐시 먼저 ---------------


def _stop_width_args(tmp_path: Path, **kw: Any) -> argparse.Namespace:
    base: dict[str, Any] = dict(
        db=str(tmp_path / "journal.db"),
        day="2026-08-17",
        days=1,
        with_backtest=True,
        warmup_days=None,
        jobs=1,
        symbol=None,
        tf=None,
        recompute=False,
        allow_stale=False,
        unpaired=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _stub_cells(
    monkeypatch: pytest.MonkeyPatch, rows_for: dict[tuple[str, str], list[Any]] | None = None
) -> list[list[tuple[str, str]]]:
    """`backtest_setup_by_cells`를 가로채 **어느 칸을 실제로 구웠는지** 기록한다.

    라벨이 아니라 **어느 칸이 계산됐는지**로 고정한다 — 「캐시를 읽었다」는 주장은 곧
    「그 칸을 안 구웠다」이고, 그건 로그 문구가 아니라 호출로만 증명된다.
    """
    import live.trade_timeline as tt

    calls: list[list[tuple[str, str]]] = []

    def fake(cells: Any, **kwargs: Any) -> dict[tuple[str, str], list[Any]]:
        listed = [(s, t) for s, t in cells]
        calls.append(listed)
        return {cell: list((rows_for or {}).get(cell, [])) for cell in listed}

    monkeypatch.setattr(tt, "backtest_setup_by_cells", fake)

    def boom(**kwargs: Any) -> list[Any]:  # 거래 행 경로는 조인 키가 없다(WAN-333).
        raise AssertionError("백테 **거래** 행을 먹였다 — 조인 키가 없어 짝이 영원히 0건이다")

    monkeypatch.setattr(tt, "backtest_timeline_rows", boom)
    return calls


def _seed_timeline_cache(
    db_path: str,
    day_key: str,
    cells: Sequence[tuple[str, str]],
    *,
    revision: str | None = None,
    created_at: int = 1_755_000_000_000,
) -> None:
    """야간 크론이 담아 둔 것처럼 캐시에 셀을 넣는다(거래 0건 셀 = "계산했고 없었다")."""
    from backtest.trade_store import engine_source_revision
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS
    from live.timeline_cache import TimelineCacheStore, cell_fingerprint

    rev = revision if revision is not None else engine_source_revision()
    cache = TimelineCacheStore(db_path)
    try:
        for symbol, timeframe in cells:
            fingerprint = cell_fingerprint(
                symbol, timeframe, day_key, warmup_days=DEFAULT_WARMUP_DAYS, revision=rev
            )
            cache.save_cell(fingerprint, [], created_at=created_at)
    finally:
        cache.close()


def test_cmd_stop_width_feeds_setup_rows_not_trade_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """🚨 백테 대조에 **셋업 행**을 먹인다 — 거래 행에는 조인 키가 없어 짝이 영원히 0건이다.

    2026-08-18에 사용자가 본 「조인 0건」의 원인이 정확히 이 배선이었다(WAN-333 §2). 라벨이
    아니라 **어느 함수가 불리는지**로 고정한다(WAN-335 이후 그 함수는 칸 낱개 경로다).
    """
    calls = _stub_cells(monkeypatch)
    rc = cmd_stop_width(_stop_width_args(tmp_path), _settings(tmp_path))
    assert rc == 0
    assert calls, "백테 대조가 셋업 행을 쓰지 않았다"
    out = capsys.readouterr().out
    assert "조인 인구조사" in out


def test_cmd_stop_width_narrowing_is_opt_in_and_labelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--symbol`·`--tf`는 옵트인이다 — 안 주면 채택 좌표 전부, 주면 그 좌표만 밝혀 돈다."""
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES

    calls = _stub_cells(monkeypatch)
    settings = _settings(tmp_path)

    assert cmd_stop_width(_stop_width_args(tmp_path), settings) == 0
    # 인자를 안 주면 예전과 같이 채택 좌표 전부를 돈다.
    assert len(calls[-1]) == len(DEFAULT_SYMBOLS) * len(DEFAULT_TIMEFRAMES)
    assert "좌표" not in capsys.readouterr().out

    args = _stop_width_args(tmp_path, symbol="BTC/USDT:USDT,ETH/USDT:USDT", tf="1h")
    assert cmd_stop_width(args, settings) == 0
    assert calls[-1] == [("BTC/USDT:USDT", "1h"), ("ETH/USDT:USDT", "1h")]
    out = capsys.readouterr().out
    assert "좌표 BTC/USDT:USDT,ETH/USDT:USDT × 1h" in out


def test_cmd_stop_width_reads_the_cache_instead_of_recomputing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """캐시가 있는 칸은 **다시 굽지 않는다**(WAN-335 §1 — 느림의 진짜 원인).

    야간 크론이 담는 것(`persist_day` → `backtest_setup_by_cell`)이 정확히 이 도구가 쓰는
    셋업 행인데, 디스크에 있는 걸 무시하고 매번 처음부터 구워 4칸으로 좁혀도 안 끝났다.
    """
    db = str(tmp_path / "journal.db")
    _seed_timeline_cache(db, "2026-08-17", [("BTC/USDT:USDT", "15m")])
    calls = _stub_cells(monkeypatch)

    args = _stop_width_args(tmp_path, db=db, symbol="BTC/USDT:USDT", tf="15m")
    assert cmd_stop_width(args, _settings(tmp_path)) == 0
    assert calls == [], "캐시에 있는 칸을 다시 계산했다"
    assert "캐시 적중 1/1칸" in capsys.readouterr().out


def test_cmd_stop_width_unpaired_block_is_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """짝 없는 셋업 귀속(WAN-337 §1)은 **옵트인**이고 기본 표를 안 바꾼다.

    제외 자체는 옳은 설계다(다른 존의 손절폭을 빼면 무의미한 Δ가 나온다 — WAN-333). 그래서
    분해는 **덧붙이는 블록**이지 기본 출력의 변경이 아니다.
    """
    _stub_cells(monkeypatch)
    settings = _settings(tmp_path)

    assert cmd_stop_width(_stop_width_args(tmp_path), settings) == 0
    assert "짝 없는 셋업 귀속" not in capsys.readouterr().out

    assert cmd_stop_width(_stop_width_args(tmp_path, unpaired=True), settings) == 0
    out = capsys.readouterr().out
    assert "짝 없는 셋업 귀속" in out
    assert "조인 인구조사" in out  # 기본 블록은 그대로다


def test_cmd_stop_width_unpaired_requires_the_backtest_join(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--unpaired`만 주면 조용히 빈 블록을 내지 않고 **그 사실을 밝힌다**."""
    args = _stop_width_args(tmp_path, with_backtest=False, unpaired=True)
    assert cmd_stop_width(args, _settings(tmp_path)) == 0
    assert "--with-backtest가 있어야" in capsys.readouterr().out


def test_cmd_stop_width_computes_only_misses_and_says_how_many_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """미스인 칸만 계산하고, **몇 칸을 계산하는지 먼저 찍는다**(완료 기준 3·4).

    진행 상황이 안 보여 「멈춘 건지 도는 건지」를 알 수 없었던 것이 실사용 중단의 절반이다.
    """
    db = str(tmp_path / "journal.db")
    _seed_timeline_cache(db, "2026-08-17", [("BTC/USDT:USDT", "15m")])
    calls = _stub_cells(monkeypatch)

    args = _stop_width_args(tmp_path, db=db, symbol="BTC/USDT:USDT", tf="15m,1h")
    assert cmd_stop_width(args, _settings(tmp_path)) == 0
    assert calls == [[("BTC/USDT:USDT", "1h")]], "미스가 아닌 칸까지 구웠다"
    out = capsys.readouterr().out
    assert "캐시 적중 1/2칸" in out
    assert "미스 1칸을 지금 계산합니다" in out.replace("**", "")


def test_cmd_stop_width_refuses_stale_engine_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """🚨 옛 엔진 판은 **기본 거부**다 — 파리티 측정에 엔진이 바뀐 몫을 섞지 않는다.

    `trades`의 `--no-stale`과 **반대**로 엄격이 기본이고 완화가 `--allow-stale`이다
    (WAN-325가 3열 대조를 끄는 것과 같은 이유). 옵트인했을 때는 옛 판을 읽되 **계산으로
    메우지 않는다** — 그러면 한 표에 두 엔진이 섞인다.
    """
    db = str(tmp_path / "journal.db")
    _seed_timeline_cache(db, "2026-08-17", [("BTC/USDT:USDT", "15m")], revision="oldrev0")
    calls = _stub_cells(monkeypatch)
    settings = _settings(tmp_path)

    # 기본: 옛 판을 안 읽고 미스로 보고 계산한다.
    args = _stop_width_args(tmp_path, db=db, symbol="BTC/USDT:USDT", tf="15m")
    assert cmd_stop_width(args, settings) == 0
    assert calls == [[("BTC/USDT:USDT", "15m")]]
    assert "옛 엔진 결과입니다" not in capsys.readouterr().out.replace("**", "")

    # 옵트인: 옛 판을 읽고 그 사실을 밝히며, 굽지 않는다.
    calls.clear()
    args = _stop_width_args(tmp_path, db=db, symbol="BTC/USDT:USDT", tf="15m", allow_stale=True)
    assert cmd_stop_width(args, settings) == 0
    assert calls == [], "옛 판을 읽고도 미스를 계산해 두 엔진을 섞었다"
    out = capsys.readouterr().out.replace("**", "")
    assert "옛 엔진 결과입니다" in out
    assert "집행 차이가 아니라" in out


def test_cmd_stop_width_narrowing_applies_to_live_rows_and_census_shows_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """좁히기가 **양쪽에** 걸리고, 인구조사가 분모를 밝힌다(WAN-335 §2).

    옛 배선은 `--symbol`/`--tf`를 백테 쪽에만 걸어 「라이브 60건 · 백테 3건 → 짝 없음 라이브
    57」을 찍었다 — 48칸과 1칸을 비교한 값인데 하필 그 줄이 「배선 오류를 지목하려고」 넣은
    것이라 **정상 좁히기가 고장처럼 읽혀** 실제 오독을 만들었다.

    §3(라이브 분포)은 **일부러** 창 전체를 본다 — 좁힌 TF를 주고도 그날 체결이 어느 TF에
    있었는지가 보이는 성질이 실사용에서 유용했다. 그 비대칭을 화면이 밝힌다.
    """
    db = str(tmp_path / "journal.db")
    _seed_two_cell_journal(db)
    _stub_cells(monkeypatch)

    args = _stop_width_args(tmp_path, db=db, symbol="BTC/USDT:USDT", tf="15m")
    assert cmd_stop_width(args, _settings(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "라이브 셋업 1건(창 전체 2건 중 이 좌표)" in out
    # 좁힌 좌표 밖(ETH 1h)의 라이브 행이 orphan으로 섞이지 않는다 — 옛 배선의 「짝 없음
    # 라이브 대량」이 정확히 그것이었다. 인구조사도 orphan 표본도 BTC 15m 하나뿐이다.
    assert "짝지어짐 0 · 짝 없음 라이브 1 · 백테 0" in out
    assert "ETH/USDT:USDT" not in out
    assert "§3은 좌표 좁히기와" in out.replace("**", "")
    assert "라이브 체결 2건" in out  # §3은 창 전체(두 칸 다)를 본다


def test_cmd_stop_width_cache_and_recompute_paths_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ **숫자가 바뀌면 실패다**(완료 기준 5) — 캐시 경로와 재계산 경로의 표가 같아야 한다.

    성능 수정이 결과를 흔들면 그건 버그다(WAN-203 선례). 같은 셋업 행을 한 번은 캐시에서,
    한 번은 계산으로 얻어 **렌더된 리포트 본문**이 글자 단위로 같은지 본다.
    """
    from backtest.trade_store import engine_source_revision
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS
    from live.timeline_cache import TimelineCacheStore, cell_fingerprint

    db = str(tmp_path / "journal.db")
    _seed_two_cell_journal(db)
    cell = ("BTC/USDT:USDT", "15m")
    rows = [_bt_setup_row()]
    settings = _settings(tmp_path)

    # (1) 재계산 경로 — 캐시를 읽지 않고 계산한다.
    _stub_cells(monkeypatch, {cell: rows})
    args = _stop_width_args(tmp_path, db=db, symbol=cell[0], tf=cell[1], recompute=True)
    assert cmd_stop_width(args, settings) == 0
    recomputed = capsys.readouterr().out
    assert "재계산" in recomputed

    # (2) 캐시 경로 — 같은 행을 캐시에서 읽는다(굽지 않는다).
    cache = TimelineCacheStore(db)
    try:
        cache.save_cell(
            cell_fingerprint(
                cell[0],
                cell[1],
                "2026-08-17",
                warmup_days=DEFAULT_WARMUP_DAYS,
                revision=engine_source_revision(),
            ),
            rows,
        )
    finally:
        cache.close()
    calls = _stub_cells(monkeypatch)
    args = _stop_width_args(tmp_path, db=db, symbol=cell[0], tf=cell[1])
    assert cmd_stop_width(args, settings) == 0
    cached = capsys.readouterr().out
    assert calls == []

    body = "손절폭 해부"
    assert cached[cached.index(body) :] == recomputed[recomputed.index(body) :]


def test_cmd_stop_width_says_non_default_warmup_cannot_use_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """워밍업이 캐시 적재값과 다르면 **전 칸이 당연히 미스**임을 화면이 밝힌다(WAN-335 §1-3).

    조용히 느려지면 「왜 안 끝나지」가 반복된다 — 지문에 워밍업이 들어간다는 사실이 화면에
    보여야 사용자가 그 노브를 뺄 수 있다.
    """
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS

    db = str(tmp_path / "journal.db")
    _seed_timeline_cache(db, "2026-08-17", [("BTC/USDT:USDT", "15m")])
    calls = _stub_cells(monkeypatch)

    args = _stop_width_args(
        tmp_path, db=db, symbol="BTC/USDT:USDT", tf="15m", warmup_days=DEFAULT_WARMUP_DAYS + 1
    )
    assert cmd_stop_width(args, _settings(tmp_path)) == 0
    assert calls == [[("BTC/USDT:USDT", "15m")]]
    assert "캐시를 쓸 수 없습니다" in capsys.readouterr().out.replace("**", "")


def test_stop_width_parser_defaults_are_strict() -> None:
    """`--recompute`·`--allow-stale`은 둘 다 옵트인이다(기본 = 캐시 먼저 · 옛 판 거부)."""
    parser = build_parser()
    args = parser.parse_args(["stop-width", "--with-backtest"])
    assert args.recompute is False
    assert args.allow_stale is False
    assert parser.parse_args(["stop-width", "--allow-stale"]).allow_stale is True
    assert parser.parse_args(["stop-width", "--recompute"]).recompute is True


def _bt_setup_row() -> Any:
    """백테 셋업 행 하나 — 라이브 BTC 15m 주문과 **같은 존**이라 조인이 성립한다."""
    from live.trade_timeline import SOURCE_BACKTEST, TimelineRow

    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        is_long=True,
        status="청산",
        reserve_ms=_STOP_WIDTH_DAY_MS + 1_000,
        limit_price=100.5,
        fill_ms=_STOP_WIDTH_DAY_MS + 2_000,
        fill_price=100.5,
        stop_price=99.9,
        take_profit_price=None,
        exit_ms=None,
        exit_price=None,
        exit_reason=None,
        pnl_pct=None,
        pnl_amount=None,
        zone_start_time=10,
        zone_confirmed_time=20,
        tap_index=0,
    )


#: 2026-08-17 KST 하루의 시작(UTC epoch ms) — 장부 행이 그 창에 들어가야 조회에 잡힌다.
_STOP_WIDTH_DAY_MS = 1_786_892_400_000


def _seed_two_cell_journal(db_path: str) -> None:
    """서로 다른 두 칸(BTC 15m · ETH 1h)에 체결된 주문을 하나씩 넣는다.

    좁히기가 라이브 쪽에도 걸리는지 보려면 **좁힌 좌표 밖의 라이브 행이 있어야** 한다.
    """
    from live.limit_orders import LimitFill, PendingLimitOrder
    from strategy.models import OrderBlockDirection
    from strategy.realtime_rsi import RealtimeRsi

    journal = OrderJournal(db_path)
    try:
        session_id = journal.start_session(now_ms=_STOP_WIDTH_DAY_MS)
        for offset, (symbol, timeframe) in enumerate(
            [("BTC/USDT:USDT", "15m"), ("ETH/USDT:USDT", "1h")]
        ):
            order = PendingLimitOrder(
                symbol=symbol,
                timeframe=timeframe,
                direction=OrderBlockDirection.BULLISH,
                stop_price=99.9,
                rsi_state=RealtimeRsi(length=14),
                limit_price=100.0,
                placed_ms=_STOP_WIDTH_DAY_MS + 1_000 + offset,
                tap_index=0,
            )
            journal_id = journal.record_placed(
                order, session_id=session_id, zone_start_time=10, zone_confirmed_time=20
            )
            journal.record_filled(
                journal_id,
                LimitFill(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=order.direction,
                    price=100.0,
                    time=_STOP_WIDTH_DAY_MS + 2_000 + offset,
                    rsi=None,
                    stop_price=99.9,  # 손절폭 0.10% → 가드 0.3% 미달
                    take_profit_price=None,
                    penetration_bps=0.0,
                    waited_ms=1_000,
                ),
            )
            journal.record_entry_result(
                journal_id,
                entered=False,
                reason="거부(손절 0.3% 하한 미달 — 진입 스킵)",
                reason_code="sizing",
            )
    finally:
        journal.close()
