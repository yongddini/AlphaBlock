"""수집기 시작 갭 점검이 **최근 창만** 본다 (WAN-187).

서버 실측: 6년 × 9종목 DB에서 시작 점검이 전 구간을 훑느라 웹소켓 접속 직전에
매달렸다. 탐지 자체를 스트리밍으로 바꿔도 45 시리즈 전 구간은 ~40초(콜드)라
시작 경로는 창을 좁힌다 — 그 배선이 실제로 걸리는지를 **동작으로** 고정한다
(설정만 만들고 안 넘기면 라벨만 붙는다. WAN-91/95/112/123 부류의 조용한 실패).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from config.settings import Settings
from data.collector import run_collector
from data.repair import RepairSummary

SYMBOL = "BTC/USDT:USDT"


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        symbols=[SYMBOL],
        timeframes=["1h"],
        db_path=str(tmp_path / "ohlcv.db"),
        repair_state_path=str(tmp_path / "repair_state.json"),
        collector_heartbeat_path=str(tmp_path / "hb.json"),
        funding_enabled=False,
        **overrides,
    )


def _stub_collector(monkeypatch: pytest.MonkeyPatch) -> list[int | None]:
    """수집기 주변(거래소·백필·펀딩·경고)을 걷어내고 복구가 받은 창을 기록한다."""
    seen: list[int | None] = []

    def fake_repair_all(*args: Any, **kwargs: Any) -> RepairSummary:
        seen.append(kwargs.get("lookback_ms"))
        return RepairSummary(ran_at_ms=0, series=[], lookback_ms=kwargs.get("lookback_ms"))

    monkeypatch.setattr("data.collector.create_exchange", lambda settings: object())
    monkeypatch.setattr("data.collector.backfill_all", lambda *a, **k: {})
    monkeypatch.setattr("data.collector.repair_all", fake_repair_all)
    monkeypatch.setattr("data.collector.alert_on_failure", lambda summary, settings: False)

    async def no_funding(settings: Settings, exchange: Any) -> None:
        return None

    monkeypatch.setattr("data.collector._backfill_funding", no_funding)
    return seen


def test_startup_repair_uses_the_configured_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _stub_collector(monkeypatch)
    settings = _settings(tmp_path, repair_on_start_lookback_days=7)

    asyncio.run(run_collector(settings, run_stream=False))

    assert seen == [7 * 86_400_000]


def test_startup_repair_window_zero_means_full_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0은 「창 없음」(예전 동작)이다 — 그 탈출구가 막히면 되돌릴 방법이 없다."""
    seen = _stub_collector(monkeypatch)
    settings = _settings(tmp_path, repair_on_start_lookback_days=0)

    asyncio.run(run_collector(settings, run_stream=False))

    assert seen == [None]


def test_startup_repair_window_is_recorded_in_the_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상태 파일이 창을 남긴다 — 「갭 없음」을 전 구간 무결로 읽지 않도록."""
    _stub_collector(monkeypatch)
    settings = _settings(tmp_path, repair_on_start_lookback_days=3)

    asyncio.run(run_collector(settings, run_stream=False))

    from data.repair import RepairStateStore

    saved = RepairStateStore(settings.repair_state_path).load()
    assert saved is not None
    assert saved.lookback_ms == 3 * 86_400_000
