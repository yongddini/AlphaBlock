"""cli.main 실행 CLI 테스트 (WAN-31) — 인자 라우팅·상태 포맷·명령 배선."""

from __future__ import annotations

import argparse
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
