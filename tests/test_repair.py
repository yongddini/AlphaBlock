"""data.repair 테스트 — 갭 자동 복구 백필 (WAN-35).

가짜 거래소를 주입해 네트워크 없이 결정론적으로 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import ccxt
import pytest

from data.gaps import find_gaps
from data.models import Candle
from data.repair import (
    RepairStateStore,
    RepairSummary,
    SeriesRepair,
    repair_all,
    repair_series,
)
from data.storage import OhlcvStore

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
TF_MS = 3_600_000
T0 = 1_700_000_000_000


def _rows(indices: list[int]) -> list[list[float]]:
    """주어진 인덱스들의 1h OHLCV 행."""
    return [[float(T0 + i * TF_MS), 1.0, 2.0, 0.5, 1.0 + i, 10.0] for i in indices]


def _candles(indices: list[int]) -> list[Candle]:
    return [
        Candle(
            symbol=SYMBOL,
            timeframe=TF,
            open_time=T0 + i * TF_MS,
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.0 + i,
            volume=10.0,
        )
        for i in indices
    ]


class FakeExchange:
    """전체 봉 세트를 알고 있고, `since`부터 `limit`개를 돌려주는 가짜 거래소."""

    def __init__(self, all_indices: list[int]) -> None:
        self._all = _rows(all_indices)
        self.calls: list[tuple[int | None, int | None]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.calls.append((since, limit))
        rows = [c for c in self._all if since is None or int(c[0]) >= since]
        return [list(r) for r in rows[: limit or len(rows)]]


class BrokenExchange:
    """항상 네트워크 오류를 던지는 거래소(복구 실패 경로 검증용)."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.calls += 1
        raise ccxt.NetworkError("permanent")


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def _timestamps(store: OhlcvStore) -> list[int]:
    return [int(t) for t in store.load(SYMBOL, TF)["open_time"].tolist()]


def test_repair_fills_gap_to_zero(store: OhlcvStore) -> None:
    """봉을 인위적으로 지운 저장소에서 갭을 탐지·복구하면 갭이 0이 된다."""
    # 0..9 중 3,4,5,7을 지운 상태로 시드.
    store.upsert_candles(_candles([0, 1, 2, 6, 8, 9]))
    assert find_gaps(_timestamps(store), TF)  # 갭 있음(사전 조건)

    exchange = FakeExchange(list(range(10)))  # 거래소에는 전체가 존재
    result = repair_series(exchange, store, SYMBOL, TF, sleeper=lambda _: None)

    assert result.bars_filled == 4  # 3,4,5,7
    assert result.bars_remaining == 0
    assert result.error is None
    assert find_gaps(_timestamps(store), TF) == []  # 복구 후 갭 0
    assert store.count(SYMBOL, TF) == 10


def test_repair_no_gaps_makes_no_api_calls(store: OhlcvStore) -> None:
    """갭이 없으면 거래소를 전혀 호출하지 않는다."""
    store.upsert_candles(_candles(list(range(10))))
    exchange = FakeExchange(list(range(10)))
    result = repair_series(exchange, store, SYMBOL, TF, sleeper=lambda _: None)

    assert exchange.calls == []  # API 미호출
    assert result.gaps_found == 0
    assert result.bars_filled == 0


def test_repair_empty_series_no_api_calls(store: OhlcvStore) -> None:
    """저장된 봉이 없으면(신규 시리즈) 갭도 없고 호출도 없다."""
    exchange = FakeExchange(list(range(10)))
    result = repair_series(exchange, store, SYMBOL, TF, sleeper=lambda _: None)
    assert exchange.calls == []
    assert result.bars_missing == 0


def test_repair_remaining_when_exchange_also_missing(store: OhlcvStore) -> None:
    """거래소에도 없는 구간은 복구 후에도 잔여로 남는다(무한루프·오탐 없음)."""
    store.upsert_candles(_candles([0, 1, 5, 6]))  # 2,3,4 누락
    exchange = FakeExchange([0, 1, 5, 6])  # 거래소도 2,3,4 없음
    result = repair_series(exchange, store, SYMBOL, TF, sleeper=lambda _: None)

    assert result.gaps_found == 1
    assert result.bars_missing == 3
    assert result.bars_filled == 0
    assert result.bars_remaining == 3
    assert result.error is None


def test_repair_isolates_errors(store: OhlcvStore) -> None:
    """복구 중 예외는 시리즈에 격리되어 error에 담기고 예외를 전파하지 않는다."""
    store.upsert_candles(_candles([0, 3]))  # 1,2 누락
    exchange = BrokenExchange()
    result = repair_series(
        exchange, store, SYMBOL, TF, max_retries=1, backoff_base=0.0, sleeper=lambda _: None
    )
    assert result.error is not None
    assert "NetworkError" in result.error
    assert result.bars_remaining == 2  # 못 채움


def test_repair_all_summarizes_series(store: OhlcvStore) -> None:
    store.upsert_candles(_candles([0, 1, 3, 4]))  # 2 누락
    exchange = FakeExchange(list(range(5)))
    summary = repair_all(exchange, store, sleeper=lambda _: None, now_ms=lambda: T0)

    assert isinstance(summary, RepairSummary)
    assert summary.ran_at_ms == T0
    assert summary.total_filled == 1
    assert summary.total_remaining == 0
    assert len(summary.repaired_series) == 1


def test_repair_all_uses_list_series_by_default(store: OhlcvStore) -> None:
    """series 미지정 시 저장된 시리즈만 대상으로 한다."""
    store.upsert_candles(_candles([0, 2]))  # 1 누락
    exchange = FakeExchange(list(range(3)))
    summary = repair_all(exchange, store, sleeper=lambda _: None)
    assert {(s.symbol, s.timeframe) for s in summary.series} == {(SYMBOL, TF)}


def test_repair_state_store_roundtrip(tmp_path: Path) -> None:
    store = RepairStateStore(tmp_path / "repair_state.json")
    assert store.load() is None  # 파일 없음

    summary = RepairSummary(
        ran_at_ms=T0,
        series=[
            SeriesRepair(
                symbol=SYMBOL,
                timeframe=TF,
                gaps_found=1,
                bars_missing=2,
                bars_filled=2,
                bars_remaining=0,
            )
        ],
    )
    store.save(summary)
    loaded = store.load()
    assert loaded is not None
    assert loaded.ran_at_ms == T0
    assert loaded.total_filled == 2
    assert loaded.has_error is False
