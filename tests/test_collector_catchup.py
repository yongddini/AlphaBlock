"""접속 직후 꼬리 따라잡기 (WAN-314).

스트림은 접속 이후에 닫힌 봉만 준다 — 기동 백필~접속 사이(신규 심볼 초기 백필이
길면 수십 분)와 재접속 공백에 닫힌 봉은 아무도 다시 요청하지 않아 영구 구멍이 됐다
(2026-08-16 사고: 15m 23:00 KST 봉이 9종목 전부에서 결측). 매 (재)접속마다 꼬리
백필이 실제로 예약·실행되는지를 **동작으로** 고정한다(배선만 만들고 안 걸리면
라벨만 붙는다 — WAN-91/95/112/123 부류의 조용한 실패).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from config.settings import Settings
from data.collector import TailCatchup, run_collector

SYMBOL = "BTC/USDT:USDT"


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        symbols=[SYMBOL],
        timeframes=["1h"],
        db_path=str(tmp_path / "ohlcv.db"),
        repair_state_path=str(tmp_path / "repair_state.json"),
        collector_heartbeat_path=str(tmp_path / "hb.json"),
        funding_enabled=False,
        collector_watchdog_enabled=False,
        **overrides,
    )


class _NullExchange:
    """따라잡기 테스트용 — `backfill_all`이 페이크라 실제로 호출되지 않는다."""

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        return []


async def _wait_until(cond: Any, *, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("조건이 제한 시간 안에 충족되지 않았다")
        await asyncio.sleep(0.01)


def test_tail_catchup_schedules_backfill_in_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake_backfill(*a: Any, **k: Any) -> dict[Any, int]:
        calls.append(a)
        return {}

    monkeypatch.setattr("data.collector.backfill_all", fake_backfill)
    settings = _settings(tmp_path)

    async def main() -> None:
        catchup = TailCatchup(_NullExchange(), object(), settings)  # type: ignore[arg-type]
        catchup.schedule()
        await _wait_until(lambda: calls)

    asyncio.run(main())
    assert len(calls) == 1


def test_tail_catchup_skips_overlapping_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이전 따라잡기가 아직 도는 동안의 재예약은 건너뛴다(빠른 재접속 루프 보호)."""
    release = threading.Event()
    calls: list[int] = []

    def slow_backfill(*a: Any, **k: Any) -> dict[Any, int]:
        calls.append(1)
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr("data.collector.backfill_all", slow_backfill)
    settings = _settings(tmp_path)

    async def main() -> None:
        catchup = TailCatchup(_NullExchange(), object(), settings)  # type: ignore[arg-type]
        catchup.schedule()
        await _wait_until(lambda: calls)  # 첫 실행이 스레드에 들어간 뒤
        catchup.schedule()  # 겹침 — 예약 생략
        release.set()
        await _wait_until(lambda: not catchup._tasks)  # noqa: SLF001 — 완료 대기

    asyncio.run(main())
    assert len(calls) == 1


def test_run_collector_wires_catchup_into_stream_on_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """수집기가 `stream_klines`에 `on_connect`를 실제로 배선하고, 그 훅이 두 번째
    백필(꼬리 따라잡기)을 일으킨다 — 기동 백필 1회 + 접속 직후 1회 = 2회."""
    backfills: list[int] = []

    def fake_backfill(*a: Any, **k: Any) -> dict[Any, int]:
        backfills.append(1)
        return {}

    monkeypatch.setattr("data.collector.backfill_all", fake_backfill)
    monkeypatch.setattr("data.collector.create_exchange", lambda settings: _NullExchange())

    async def no_funding(settings: Settings, exchange: Any) -> None:
        return None

    monkeypatch.setattr("data.collector._backfill_funding", no_funding)

    captured: dict[str, Any] = {}

    async def fake_stream(store: Any, symbols: Any, timeframes: Any, **kwargs: Any) -> None:
        captured["on_connect"] = kwargs.get("on_connect")
        assert kwargs.get("on_connect") is not None
        kwargs["on_connect"]()  # 접속 성립 — 따라잡기 예약
        await _wait_until(lambda: len(backfills) >= 2)
        raise RuntimeError("stop")  # 복구 불가 예외 → run_with_recovery가 올린다

    monkeypatch.setattr("data.collector.stream_klines", fake_stream)
    settings = _settings(tmp_path)

    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(run_collector(settings, run_stream=True, repair_on_start=False))

    assert captured["on_connect"] is not None
    assert len(backfills) == 2  # 기동 백필 + 접속 직후 꼬리 따라잡기
