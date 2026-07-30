"""live.runner 테스트 — 시리즈 조립 + 진입점 위임.

옛 A안(종가 시그널) 러너(`SignalRunner`·`WatermarkStore`·`Notifier`)는 WAN-208에서
제거됐다. 진입점(`run_signal_runner`)은 남아 존-지정가 페이퍼 러너로 위임하고,
그 위임은 `test_zone_limit_runner.test_default_settings_dispatch_to_zone_limit_runner`가
동작으로 고정한다. 이 파일은 진입점에 남은 표면(시리즈 조립·테스트 메시지·위임)을 본다.
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from live import runner as runner_mod
from live.runner import build_series


def test_build_series_is_cartesian_product() -> None:
    settings = Settings(
        live_signal_symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
        live_signal_timeframes=["15m", "1h"],
    )
    assert build_series(settings) == [
        ("BTC/USDT:USDT", "15m"),
        ("BTC/USDT:USDT", "1h"),
        ("ETH/USDT:USDT", "15m"),
        ("ETH/USDT:USDT", "1h"),
    ]


def test_empty_series_when_no_symbols() -> None:
    settings = Settings(live_signal_symbols=[], live_signal_timeframes=["1h"])
    assert build_series(settings) == []


def test_run_delegates_to_zone_limit_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값(`entry_mode="zone_limit"`)에서 실제 폴링은 존-지정가 러너로 위임된다."""
    import live.zone_limit_runner as zlr

    calls: list[bool] = []
    monkeypatch.setattr(zlr, "run_zone_limit_runner", lambda settings, once: calls.append(once))
    settings = Settings(live_signal_symbols=[], live_signal_timeframes=[])
    runner_mod.run_signal_runner(settings, once=True)
    assert calls == [True]


def test_test_message_short_circuits_without_delegating(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--test-message`는 위임하지 않고 텔레그램 확인 메시지만 보낸다(드라이런=미설정 처리)."""
    import live.zone_limit_runner as zlr

    delegated: list[bool] = []
    monkeypatch.setattr(zlr, "run_zone_limit_runner", lambda settings, once: delegated.append(once))
    settings = Settings(live_signal_symbols=[], live_signal_timeframes=[])
    # dry_run=True → 텔레그램 미설정으로 취급, 전송 시도 없이 종료. 위임도 없다.
    runner_mod.run_signal_runner(settings, test_message=True, dry_run=True)
    assert delegated == []
