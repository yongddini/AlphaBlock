"""data.backfill 테스트.

네트워크 대신 가짜 거래소를 주입해 페이징·재시도·재시작 복구를 결정론적으로
검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import ccxt
import pytest

from data.backfill import backfill_all, backfill_symbol
from data.storage import OhlcvStore

TF_MS = 60_000  # 1m
T0 = 1_700_000_000_000


def _make_candles(count: int, start: int = T0) -> list[list[float]]:
    """1m 간격 OHLCV 행 `count`개."""
    return [[float(start + i * TF_MS), 1.0, 2.0, 0.5, 1.0 + i, 10.0] for i in range(count)]


class FakeExchange:
    """`since`부터 `limit`개까지 OHLCV를 돌려주는 가짜 거래소."""

    def __init__(self, candles: list[list[float]]) -> None:
        self.candles = candles
        self.calls: list[tuple[int | None, int | None]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.calls.append((since, limit))
        rows = [c for c in self.candles if since is None or int(c[0]) >= since]
        return [list(r) for r in rows[: limit or len(rows)]]


class FlakyExchange(FakeExchange):
    """처음 `fail_times`번은 네트워크 오류를 던지고 이후 정상 응답."""

    def __init__(self, candles: list[list[float]], fail_times: int) -> None:
        super().__init__(candles)
        self.fail_times = fail_times
        self.attempts = 0

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise ccxt.NetworkError("일시적 네트워크 오류")
        return super().fetch_ohlcv(symbol, timeframe, since, limit, params)


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def test_backfill_pages_through_all(store: OhlcvStore) -> None:
    """limit보다 많은 봉을 여러 페이지에 걸쳐 모두 저장한다."""
    exchange = FakeExchange(_make_candles(2500))
    end = T0 + 2500 * TF_MS
    total = backfill_symbol(exchange, store, "BTC/USDT:USDT", "1m", T0, until_ms=end, limit=1000)
    assert total == 2500
    assert store.count() == 2500
    assert len(exchange.calls) == 3  # 1000 + 1000 + 500


def test_backfill_excludes_rows_at_or_after_end(store: OhlcvStore) -> None:
    exchange = FakeExchange(_make_candles(10))
    end = T0 + 5 * TF_MS  # 5개만 (end 배타적)
    total = backfill_symbol(exchange, store, "BTC/USDT:USDT", "1m", T0, until_ms=end, limit=1000)
    assert total == 5
    assert store.last_open_time("BTC/USDT:USDT", "1m") == T0 + 4 * TF_MS


def test_backfill_retries_on_network_error(store: OhlcvStore) -> None:
    """네트워크 오류는 지수 백오프로 재시도되고 결국 성공한다."""
    exchange = FlakyExchange(_make_candles(3), fail_times=2)
    slept: list[float] = []
    end = T0 + 3 * TF_MS
    total = backfill_symbol(
        exchange,
        store,
        "BTC/USDT:USDT",
        "1m",
        T0,
        until_ms=end,
        limit=1000,
        backoff_base=0.01,
        sleeper=slept.append,
    )
    assert total == 3
    assert len(slept) == 2  # 두 번 재시도 대기
    assert slept == [0.01, 0.02]  # 지수 백오프


def test_backfill_raises_after_max_retries(store: OhlcvStore) -> None:
    exchange = FlakyExchange(_make_candles(1), fail_times=10)
    with pytest.raises(ccxt.NetworkError):
        backfill_symbol(
            exchange,
            store,
            "BTC/USDT:USDT",
            "1m",
            T0,
            until_ms=T0 + TF_MS,
            max_retries=3,
            backoff_base=0.0,
            sleeper=lambda _: None,
        )


def test_backfill_all_resumes_from_last(store: OhlcvStore) -> None:
    """저장된 마지막 봉이 있으면 그 다음부터 수집한다(재시작 복구)."""
    # 최근 5개 봉을 미리 저장.
    seed = FakeExchange(_make_candles(5))
    end = T0 + 5 * TF_MS
    backfill_symbol(seed, store, "BTC/USDT:USDT", "1m", T0, until_ms=end)
    assert store.count() == 5

    # 이후 5개가 더 생겼다고 가정.
    exchange = FakeExchange(_make_candles(10))
    now = T0 + 10 * TF_MS
    results = backfill_all(
        exchange,
        store,
        ["BTC/USDT:USDT"],
        ["1m"],
        now_ms=lambda: now,
        sleeper=lambda _: None,
    )
    assert results[("BTC/USDT:USDT", "1m")] == 5  # 신규 5개만
    assert store.count() == 10
    # 첫 호출 since 가 마지막 저장 봉 다음이어야 한다.
    assert exchange.calls[0][0] == T0 + 5 * TF_MS
