"""수집기가 실제로 펀딩 경로를 타는지 + 실패를 시끄럽게 내는지 테스트 (WAN-63).

근본 원인은 `run_collector`가 펀딩 수집을 **아예 호출하지 않아** `funding_rate`가
0행이었다는 것이다. 이 테스트는 수집기의 펀딩 백필 단계가 저장소를 실제로 채우고,
비어 있거나 예외가 나면 **조용히 넘어가지 않고 로깅**하는지 고정한다.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from config.settings import Settings
from data.collector import _backfill_funding
from data.funding import FundingRateStore

SYMBOL = "BTC/USDT:USDT"
EIGHT_H = 8 * 3_600_000
START_ISO = "2023-07-01"
START_MS = int(pd.Timestamp(START_ISO, tz="UTC").timestamp() * 1000)


class _FakeExchange:
    """설정한 이력만 돌려주는 최소 펀딩 거래소."""

    def __init__(self, history: list[dict[str, Any]]) -> None:
        self.history = history

    def fetch_funding_rate(
        self, symbol: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last = self.history[-1]["timestamp"] if self.history else START_MS
        return {
            "fundingRate": 0.0,
            "fundingTimestamp": last,
            "markPrice": 1.0,
            "nextFundingTimestamp": last,
        }

    def fetch_funding_rate_history(
        self,
        symbol: str = SYMBOL,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows = [e for e in self.history if since is None or int(e["timestamp"]) >= since]
        return [dict(r) for r in rows[: limit or len(rows)]]


class _RaisingExchange(_FakeExchange):
    def fetch_funding_rate_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("boom")


def _history(count: int) -> list[dict[str, Any]]:
    return [
        {"symbol": SYMBOL, "timestamp": START_MS + i * EIGHT_H, "fundingRate": 0.0001}
        for i in range(count)
    ]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        symbols=[SYMBOL],
        db_path=str(tmp_path / "t.db"),
        funding_enabled=True,
        funding_backfill_start=START_ISO,
    )


def test_collector_backfill_populates_funding_store(tmp_path: Path) -> None:
    """수집기의 펀딩 백필이 저장소를 실제로 채운다(예전엔 0행이었음)."""
    settings = _settings(tmp_path)
    exchange = _FakeExchange(_history(5))
    asyncio.run(_backfill_funding(settings, exchange))
    with FundingRateStore(settings.db_path) as store:
        assert store.count(SYMBOL) == 5


def test_collector_backfill_loud_when_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """거래소가 펀딩을 하나도 안 주면 0행임을 error 로 크게 남긴다(조용한 실패 금지)."""
    settings = _settings(tmp_path)
    exchange = _FakeExchange([])  # 이력 없음
    with caplog.at_level(logging.ERROR):
        asyncio.run(_backfill_funding(settings, exchange))
    assert any("0행" in r.message for r in caplog.records)


def test_collector_backfill_loud_on_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """백필 중 예외가 나도 수집기를 죽이지 않되, 예외를 크게 로깅한다."""
    settings = _settings(tmp_path)
    exchange = _RaisingExchange([])
    with caplog.at_level(logging.ERROR):
        asyncio.run(_backfill_funding(settings, exchange))  # 전파되지 않아야 함
    assert any("펀딩 백필 실패" in r.message for r in caplog.records)


def test_collector_backfill_warns_when_disabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """funding_enabled=False면 펀딩이 0 처리됨을 경고로 알린다."""
    settings = Settings(symbols=[SYMBOL], db_path=str(tmp_path / "t.db"), funding_enabled=False)
    exchange = _FakeExchange(_history(3))
    with caplog.at_level(logging.WARNING):
        asyncio.run(_backfill_funding(settings, exchange))
    assert any("비활성화" in r.message for r in caplog.records)
