"""WAN-309 — 채택 좌표 ↔ 환경변수 드리프트 점검 테스트.

핵심 계약을 동작으로 고정한다:
- 코드 기본값(= 채택 좌표)이면 **아무것도 안 찍는다**(드리프트 0 · 경고 줄 0).
- 좌표가 좁혀져 있으면 누락 값이 명시적으로 찍힌다(낡은 `.env` 사고의 서명).
- 드리프트 점검은 채택 좌표를 복붙하지 않고 pydantic 기본값에서 읽는다 — 다음
  재-베이스라인이 기본값을 옮기면 자동으로 따라간다.
- `.env.example` 키 대조는 키 **이름**만 다루고, 파일이 없으면 대조 불가(None)다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config.drift import (
    adopted_default,
    check_coordinate_drift,
    env_example_only_keys,
    parse_env_keys,
    render_drift_lines,
)
from config.settings import Settings


def _clean_settings(**overrides: Any) -> Settings:
    """로컬 `.env`·셸 환경변수와 무관하게 코드 기본값 + 명시 인자만 쓰는 Settings."""
    return Settings(_env_file=None, **overrides)


def _delenv_coordinates(monkeypatch: Any) -> None:
    for key in (
        "ALPHABLOCK_SYMBOLS",
        "ALPHABLOCK_TIMEFRAMES",
        "ALPHABLOCK_LIVE_SIGNAL_SYMBOLS",
        "ALPHABLOCK_LIVE_SIGNAL_TIMEFRAMES",
    ):
        monkeypatch.delenv(key, raising=False)


# -- 드리프트 판정 --------------------------------------------------------------


def test_code_defaults_produce_no_drift_and_no_output(monkeypatch: Any) -> None:
    """채택 좌표 그대로면 드리프트 0 · 경고 줄 0 — 「같으면 아무것도 안 뜬다」(완료 기준 2)."""
    _delenv_coordinates(monkeypatch)
    drifts = check_coordinate_drift(_clean_settings())
    assert drifts == []
    assert render_drift_lines(drifts) == []


def test_narrowed_symbols_report_missing_values(monkeypatch: Any) -> None:
    """유니버스가 좁혀져 있으면(낡은 `.env`의 서명) 누락 심볼이 명시적으로 찍힌다."""
    _delenv_coordinates(monkeypatch)
    adopted = list(adopted_default("symbols"))
    narrowed = adopted[:9]  # WAN-307 이전 9종목 상태 재현
    drifts = check_coordinate_drift(_clean_settings(symbols=narrowed))
    assert [d.field for d in drifts] == ["symbols"]
    assert set(drifts[0].missing) == set(adopted[9:])
    assert drifts[0].extra == ()
    lines = render_drift_lines(drifts)
    assert any("ALPHABLOCK_SYMBOLS" in line for line in lines)
    # 누락 값이 줄 안에 실제로 보인다(개수만 찍고 마는 요약 금지).
    for symbol in adopted[9:]:
        assert any(symbol in line for line in lines)


def test_live_signal_narrowing_is_reported(monkeypatch: Any) -> None:
    """감시 대상 BTC/1h 단독(WAN-191 이전 상태)이 두 키 모두에서 드리프트로 잡힌다."""
    _delenv_coordinates(monkeypatch)
    drifts = check_coordinate_drift(
        _clean_settings(
            live_signal_symbols=["BTC/USDT:USDT"],
            live_signal_timeframes=["1h"],
        )
    )
    assert {d.env_key for d in drifts} == {
        "ALPHABLOCK_LIVE_SIGNAL_SYMBOLS",
        "ALPHABLOCK_LIVE_SIGNAL_TIMEFRAMES",
    }
    tf_drift = next(d for d in drifts if d.field == "live_signal_timeframes")
    assert set(tf_drift.missing) == {"15m", "2h", "4h"}


def test_reordered_values_are_not_drift(monkeypatch: Any) -> None:
    """순서만 다르고 집합이 같으면 드리프트가 아니다(좌표 필드는 순서 무의미)."""
    _delenv_coordinates(monkeypatch)
    reordered = list(reversed(adopted_default("symbols")))
    assert check_coordinate_drift(_clean_settings(symbols=list(reordered))) == []


def test_extra_values_are_reported_as_extra(monkeypatch: Any) -> None:
    """채택 좌표에 없는 값이 더해져 있으면 「추가」로 찍힌다(누락과 구분)."""
    _delenv_coordinates(monkeypatch)
    widened = [*adopted_default("timeframes"), "3d"]
    drifts = check_coordinate_drift(_clean_settings(timeframes=widened))
    assert [d.field for d in drifts] == ["timeframes"]
    assert drifts[0].extra == ("3d",)
    assert drifts[0].missing == ()
    assert any("추가: 3d" in line for line in render_drift_lines(drifts))


def test_adopted_default_reads_pydantic_field_not_a_copy() -> None:
    """채택 좌표는 pydantic 기본값에서 읽는다 — 기본값이 옮겨지면 점검이 자동으로 따라간다."""
    settings = Settings(_env_file=None)
    assert adopted_default("symbols") == tuple(settings.symbols)
    assert adopted_default("live_signal_timeframes") == tuple(settings.live_signal_timeframes)


# -- .env.example 키 대조 (§2) ---------------------------------------------------


def test_parse_env_keys_skips_comments_and_reads_names_only(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# 주석 키는 무시\n"
        "# ALPHABLOCK_COMMENTED=1\n"
        "ALPHABLOCK_A=1\n"
        "export ALPHABLOCK_B = 2\n"
        "잘못된 줄\n",
        encoding="utf-8",
    )
    assert parse_env_keys(env) == {"ALPHABLOCK_A", "ALPHABLOCK_B"}


def test_env_example_only_keys_lists_the_gap(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    env = tmp_path / ".env"
    example.write_text("ALPHABLOCK_A=\nALPHABLOCK_B=\nALPHABLOCK_C=\n", encoding="utf-8")
    env.write_text("ALPHABLOCK_B=1\n", encoding="utf-8")
    assert env_example_only_keys(example, env) == ["ALPHABLOCK_A", "ALPHABLOCK_C"]


def test_env_example_only_keys_none_when_a_side_is_missing(tmp_path: Path) -> None:
    """`.env` 없이 코드 기본값으로 도는 기계는 대조 불가(None) — 빈 리스트와 구분한다."""
    example = tmp_path / ".env.example"
    example.write_text("ALPHABLOCK_A=\n", encoding="utf-8")
    assert env_example_only_keys(example, tmp_path / ".env") is None
    assert env_example_only_keys(tmp_path / "없는.example", tmp_path / ".env") is None


# -- CLI 배선 (status·doctor) ----------------------------------------------------


def _healthy_disk(monkeypatch: Any) -> None:
    """디스크 여유 점검을 격리한다 — 실기계의 디스크 상태가 exit 코드 단언을 흔들지 않게."""
    import collections
    import shutil

    usage = collections.namedtuple("usage", ["total", "used", "free"])
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(100, 20, 80))


def _seed_healthy_journal(db_path: str) -> None:
    """doctor가 건강 판정을 내는 최소 장부: 세션 1 + 처분이 남은 체결 1행(빈 장부 회피)."""
    from live.order_journal import OrderJournal

    journal = OrderJournal(db_path)
    session = journal.start_session(now_ms=0)
    conn = journal._conn  # noqa: SLF001 — 최소 픽스처.
    with conn:
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
            " placed_ms, status, fill_ms, fill_price, stop_price, entry_status)"
            " VALUES (?, 'BTC/USDT:USDT', '1h', 'bullish', 20, 'filled', 2000, 100.0, 90.0,"
            " 'entered')",
            (session,),
        )
    journal.close()


def _cli_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """CLI 테스트용: 코드 기본값 + 임시 경로(로컬 `.env`·저장소 상태 파일과 격리)."""
    return _clean_settings(
        db_path=str(tmp_path / "ohlcv.db"),
        live_runtime_state_path=str(tmp_path / "runtime.json"),
        collector_heartbeat_path=str(tmp_path / "hb.json"),
        repair_state_path=str(tmp_path / "repair.json"),
        **overrides,
    )


def test_cmd_status_is_silent_without_drift_and_warns_with_it(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    """status는 채택 좌표 일치 시 드리프트를 한 줄도 안 찍고, 어긋나면 명시적으로 찍는다."""
    import argparse

    from cli.main import cmd_status

    _delenv_coordinates(monkeypatch)
    args = argparse.Namespace(bar_count=False)

    assert cmd_status(args, _cli_settings(tmp_path)) == 0
    assert "채택 좌표" not in capsys.readouterr().out

    drifted = _cli_settings(tmp_path, live_signal_timeframes=["1h"])
    assert cmd_status(args, drifted) == 0
    out = capsys.readouterr().out
    assert "ALPHABLOCK_LIVE_SIGNAL_TIMEFRAMES" in out
    assert "누락: 15m, 2h, 4h" in out


def test_cmd_doctor_prints_drift_but_exit_code_stays_db_only(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    """doctor는 드리프트를 찍되 종료 코드는 DB 무결성 전용이다 — 낡은 `.env`가
    exit 1을 내면 cron 감시가 「DB 손상」과 구분하지 못한다."""
    import argparse

    from cli.main import cmd_doctor

    _delenv_coordinates(monkeypatch)
    _healthy_disk(monkeypatch)
    monkeypatch.chdir(tmp_path)  # 저장소 `.env.example` 대조를 격리(§2 줄은 별도 테스트).
    settings = _cli_settings(tmp_path, symbols=["BTC/USDT:USDT"])
    _seed_healthy_journal(settings.db_path)

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
    assert cmd_doctor(args, settings) == 0  # 드리프트가 있어도 DB가 건강하면 0.
    out = capsys.readouterr().out
    assert "환경 설정 드리프트" in out
    assert "ALPHABLOCK_SYMBOLS" in out


def test_cmd_doctor_lists_example_only_keys(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    """`.env.example`에만 있는 키 목록이 doctor에 보인다(완료 기준 3) — 키 이름만."""
    import argparse

    from cli.main import cmd_doctor

    _delenv_coordinates(monkeypatch)
    _healthy_disk(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text(
        "ALPHABLOCK_NEW_KNOB=\nALPHABLOCK_DB_PATH=\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text("ALPHABLOCK_DB_PATH=x.db\n", encoding="utf-8")
    settings = _cli_settings(tmp_path)
    _seed_healthy_journal(settings.db_path)

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
    assert cmd_doctor(args, settings) == 0
    out = capsys.readouterr().out
    assert "`.env.example`에만 있는 키 1개" in out
    assert "ALPHABLOCK_NEW_KNOB" in out


def test_repo_env_example_matches_adopted_symbols() -> None:
    """저장소 `.env.example`의 심볼 예시가 채택 유니버스와 일치한다 — 예시 자체가
    드리프트 소스가 되는 것(WAN-307 이전 9종목이 남는 것)을 막는다."""
    text = Path(".env.example").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("ALPHABLOCK_SYMBOLS="))
    import json

    example_symbols = json.loads(line.split("=", 1)[1])
    assert set(example_symbols) == set(adopted_default("symbols"))
