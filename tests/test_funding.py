"""data.funding 테스트.

네트워크 대신 가짜 거래소를 주입해 변환·저장·페이징·재시도·비용 계산을
결정론적으로 검증한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import ccxt
import pytest

from data.funding import (
    FundingRateStore,
    backfill_funding_all,
    backfill_funding_symbol,
    cumulative_funding_cost,
    fetch_current_funding,
    funding_cost_for_position,
    refresh_funding,
    run_funding_refresh,
)
from data.models import (
    FundingRate,
    funding_from_ccxt_current,
    funding_from_ccxt_history,
)

SYMBOL = "BTC/USDT:USDT"
EIGHT_H = 8 * 3_600_000
T0 = 1_700_000_000_000


@pytest.fixture
def store() -> Iterator[FundingRateStore]:
    s = FundingRateStore(":memory:")
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# 가짜 거래소
# --------------------------------------------------------------------------- #


def _history(count: int, start: int = T0) -> list[dict[str, Any]]:
    """8h 간격 확정 펀딩 이력 `count`개."""
    return [
        {"symbol": SYMBOL, "timestamp": start + i * EIGHT_H, "fundingRate": 0.0001 * (i + 1)}
        for i in range(count)
    ]


class FakeFundingExchange:
    """현재값 + 이력을 돌려주는 가짜 펀딩 거래소."""

    def __init__(
        self,
        history: list[dict[str, Any]] | None = None,
        current: dict[str, Any] | None = None,
    ) -> None:
        self.history = history or []
        self.current = current or {
            "fundingRate": 0.00025,
            "fundingTimestamp": T0 + 100 * EIGHT_H,
            "markPrice": 50_000.0,
            "nextFundingTimestamp": T0 + 100 * EIGHT_H,
        }
        self.history_calls: list[tuple[int | None, int | None]] = []
        self.current_calls: int = 0

    def fetch_funding_rate(
        self, symbol: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.current_calls += 1
        return dict(self.current)

    def fetch_funding_rate_history(
        self,
        symbol: str = SYMBOL,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.history_calls.append((since, limit))
        rows = [e for e in self.history if since is None or int(e["timestamp"]) >= since]
        return [dict(r) for r in rows[: limit or len(rows)]]


class FlakyFundingExchange(FakeFundingExchange):
    """처음 `fail_times`번의 이력 조회는 네트워크 오류를 던진다."""

    def __init__(self, history: list[dict[str, Any]], fail_times: int) -> None:
        super().__init__(history=history)
        self.fail_times = fail_times
        self.attempts = 0

    def fetch_funding_rate_history(
        self,
        symbol: str = SYMBOL,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise ccxt.NetworkError("일시적 네트워크 오류")
        return super().fetch_funding_rate_history(symbol, since, limit, params)


# --------------------------------------------------------------------------- #
# 변환기
# --------------------------------------------------------------------------- #


def test_from_ccxt_current_is_predicted() -> None:
    fr = funding_from_ccxt_current(
        SYMBOL,
        {
            "fundingRate": 0.0003,
            "fundingTimestamp": T0,
            "markPrice": 51_000.0,
            "nextFundingTimestamp": T0 + EIGHT_H,
        },
    )
    assert fr.is_predicted is True
    assert fr.funding_time == T0
    assert fr.rate == 0.0003
    assert fr.mark_price == 51_000.0
    assert fr.next_funding_time == T0 + EIGHT_H


def test_from_ccxt_current_defaults_next_to_funding_time() -> None:
    fr = funding_from_ccxt_current(SYMBOL, {"fundingRate": 0.0001, "fundingTimestamp": T0})
    assert fr.next_funding_time == T0
    assert fr.mark_price is None


def test_from_ccxt_current_missing_field_raises() -> None:
    with pytest.raises(ValueError):
        funding_from_ccxt_current(SYMBOL, {"fundingRate": 0.0001})


def test_from_ccxt_history_is_confirmed() -> None:
    fr = funding_from_ccxt_history(SYMBOL, {"timestamp": T0, "fundingRate": -0.0002})
    assert fr.is_predicted is False
    assert fr.funding_time == T0
    assert fr.rate == -0.0002


# --------------------------------------------------------------------------- #
# 저장소
# --------------------------------------------------------------------------- #


def test_upsert_idempotent_on_pk(store: FundingRateStore) -> None:
    """같은 (symbol, funding_time) 재삽입은 중복 없이 갱신된다."""
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0001)])
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0009)])
    assert store.count() == 1
    assert store.get_rates(SYMBOL)[0].rate == 0.0009


def test_predicted_then_confirmed_transition(store: FundingRateStore) -> None:
    """예측값이 확정값으로 갱신된다(예측→확정)."""
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0005, is_predicted=True)])
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0004, is_predicted=False)])
    (row,) = store.get_rates(SYMBOL)
    assert row.is_predicted is False
    assert row.rate == 0.0004


def test_confirmed_not_clobbered_by_predicted(store: FundingRateStore) -> None:
    """이미 확정된 행은 나중에 들어온 예측값이 덮어쓰지 않는다."""
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0004, is_predicted=False)])
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.9999, is_predicted=True)])
    (row,) = store.get_rates(SYMBOL)
    assert row.is_predicted is False
    assert row.rate == 0.0004  # 예측값에 덮이지 않음


def test_confirmed_preserves_mark_price_via_coalesce(store: FundingRateStore) -> None:
    """확정 이력이 mark_price=None으로 덮어써도 기존 mark_price는 보존된다."""
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0005, mark_price=50_000.0, is_predicted=True)])
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0004, mark_price=None, is_predicted=False)])
    (row,) = store.get_rates(SYMBOL)
    assert row.mark_price == 50_000.0


def test_last_funding_time_confirmed_only(store: FundingRateStore) -> None:
    store.upsert_rates(
        [
            FundingRate(SYMBOL, T0, 0.0001, is_predicted=False),
            FundingRate(SYMBOL, T0 + EIGHT_H, 0.0002, is_predicted=True),
        ]
    )
    assert store.last_funding_time(SYMBOL, confirmed_only=True) == T0
    assert store.last_funding_time(SYMBOL, confirmed_only=False) == T0 + EIGHT_H
    assert store.last_funding_time("NON/EXISTENT") is None


def test_latest_returns_most_recent(store: FundingRateStore) -> None:
    assert store.latest(SYMBOL) is None
    store.upsert_rates([FundingRate(SYMBOL, T0, 0.0001), FundingRate(SYMBOL, T0 + EIGHT_H, 0.0002)])
    latest = store.latest(SYMBOL)
    assert latest is not None
    assert latest.funding_time == T0 + EIGHT_H


def test_load_orders_filters_and_derives_datetime(store: FundingRateStore) -> None:
    store.upsert_rates(
        [
            FundingRate(SYMBOL, T0 + 2 * EIGHT_H, 0.0003),
            FundingRate(SYMBOL, T0, 0.0001),
            FundingRate(SYMBOL, T0 + EIGHT_H, 0.0002, is_predicted=True),
        ]
    )
    df = store.load(SYMBOL)
    assert list(df["funding_time"]) == [T0, T0 + EIGHT_H, T0 + 2 * EIGHT_H]
    assert "funding_datetime" in df.columns
    assert df["is_predicted"].dtype == bool

    confirmed = store.load(SYMBOL, include_predicted=False)
    assert list(confirmed["funding_time"]) == [T0, T0 + 2 * EIGHT_H]

    windowed = store.load(SYMBOL, start_ms=T0 + EIGHT_H, end_ms=T0 + 2 * EIGHT_H)
    assert list(windowed["funding_time"]) == [T0 + EIGHT_H]  # end 배타적


def test_load_empty_returns_schema(store: FundingRateStore) -> None:
    df = store.load("NON/EXISTENT")
    assert df.empty
    assert "funding_time" in df.columns and "rate" in df.columns


def test_upsert_empty_is_noop(store: FundingRateStore) -> None:
    assert store.upsert_rates([]) == 0


# --------------------------------------------------------------------------- #
# 수집
# --------------------------------------------------------------------------- #


def test_fetch_current_funding(store: FundingRateStore) -> None:
    exchange = FakeFundingExchange()
    fr = fetch_current_funding(exchange, SYMBOL)
    assert fr.is_predicted is True
    assert fr.rate == 0.00025
    assert fr.next_funding_time == T0 + 100 * EIGHT_H


def test_refresh_funding_stores_predicted(store: FundingRateStore) -> None:
    exchange = FakeFundingExchange()
    results = refresh_funding(exchange, store, [SYMBOL])
    assert results[SYMBOL].rate == 0.00025
    latest = store.latest(SYMBOL)
    assert latest is not None and latest.is_predicted is True


def test_backfill_pages_through_all(store: FundingRateStore) -> None:
    exchange = FakeFundingExchange(history=_history(2500))
    end = T0 + 2500 * EIGHT_H
    total = backfill_funding_symbol(
        exchange, store, SYMBOL, T0, until_ms=end, limit=1000, sleeper=lambda _: None
    )
    assert total == 2500
    assert store.count() == 2500
    assert len(exchange.history_calls) == 3  # 1000 + 1000 + 500


def test_backfill_excludes_rows_at_or_after_end(store: FundingRateStore) -> None:
    exchange = FakeFundingExchange(history=_history(10))
    end = T0 + 5 * EIGHT_H  # 5개만 (end 배타적)
    total = backfill_funding_symbol(exchange, store, SYMBOL, T0, until_ms=end, limit=1000)
    assert total == 5
    assert store.last_funding_time(SYMBOL) == T0 + 4 * EIGHT_H


def test_backfill_retries_on_network_error(store: FundingRateStore) -> None:
    exchange = FlakyFundingExchange(_history(3), fail_times=2)
    slept: list[float] = []
    end = T0 + 3 * EIGHT_H
    total = backfill_funding_symbol(
        exchange,
        store,
        SYMBOL,
        T0,
        until_ms=end,
        limit=1000,
        backoff_base=0.01,
        sleeper=slept.append,
    )
    assert total == 3
    assert slept == [0.01, 0.02]  # 지수 백오프


def test_backfill_raises_after_max_retries(store: FundingRateStore) -> None:
    exchange = FlakyFundingExchange(_history(1), fail_times=10)
    with pytest.raises(ccxt.NetworkError):
        backfill_funding_symbol(
            exchange,
            store,
            SYMBOL,
            T0,
            until_ms=T0 + EIGHT_H,
            max_retries=3,
            backoff_base=0.0,
            sleeper=lambda _: None,
        )


def test_backfill_all_resumes_from_last(store: FundingRateStore) -> None:
    """저장된 마지막 확정 펀딩이 있으면 그 다음부터 수집한다(재시작 복구)."""
    seed = FakeFundingExchange(history=_history(5))
    backfill_funding_symbol(seed, store, SYMBOL, T0, until_ms=T0 + 5 * EIGHT_H)
    assert store.count() == 5

    exchange = FakeFundingExchange(history=_history(10))
    now = T0 + 10 * EIGHT_H
    results = backfill_funding_all(
        exchange, store, [SYMBOL], now_ms=lambda: now, sleeper=lambda _: None
    )
    assert results[SYMBOL] == 5  # 신규 5개만
    assert store.count() == 10
    # 첫 호출 since 가 마지막 저장 봉 다음(+1)이어야 한다.
    assert exchange.history_calls[0][0] == (T0 + 4 * EIGHT_H) + 1


# --------------------------------------------------------------------------- #
# 비용 헬퍼
# --------------------------------------------------------------------------- #


def test_cumulative_cost_long_and_short() -> None:
    rates = [
        FundingRate(SYMBOL, T0, 0.001),
        FundingRate(SYMBOL, T0 + EIGHT_H, -0.0005),
    ]
    # 명목가 10,000: 롱 비용 = 10000*(0.001) + 10000*(-0.0005) = 10 - 5 = 5
    assert cumulative_funding_cost(rates, position_notional=10_000) == pytest.approx(5.0)
    # 숏은 부호 반대 = -5
    assert cumulative_funding_cost(
        rates, position_notional=10_000, direction="short"
    ) == pytest.approx(-5.0)


def test_cumulative_cost_window_and_predicted_filter() -> None:
    rates = [
        FundingRate(SYMBOL, T0, 0.001, is_predicted=False),
        FundingRate(SYMBOL, T0 + EIGHT_H, 0.002, is_predicted=False),
        FundingRate(SYMBOL, T0 + 2 * EIGHT_H, 0.003, is_predicted=True),
    ]
    # [T0, T0+2*8h) → 첫 두 개만, 예측 제외(기본) → 10000*(0.001+0.002)=30
    cost = cumulative_funding_cost(
        rates, position_notional=10_000, start_ms=T0, end_ms=T0 + 2 * EIGHT_H
    )
    assert cost == pytest.approx(30.0)
    # 예측 포함 + 구간 확장 → 세 개 모두
    cost_all = cumulative_funding_cost(rates, position_notional=10_000, include_predicted=True)
    assert cost_all == pytest.approx(60.0)


def test_funding_cost_for_position_from_store(store: FundingRateStore) -> None:
    store.upsert_rates(
        [
            FundingRate(SYMBOL, T0, 0.001, is_predicted=False),
            FundingRate(SYMBOL, T0 + EIGHT_H, 0.002, is_predicted=False),
            FundingRate(SYMBOL, T0 + 2 * EIGHT_H, 0.5, is_predicted=True),  # 제외 대상
        ]
    )
    cost = funding_cost_for_position(
        store,
        SYMBOL,
        start_ms=T0,
        end_ms=T0 + 3 * EIGHT_H,
        position_notional=10_000,
    )
    assert cost == pytest.approx(30.0)  # 확정 두 건만


# --------------------------------------------------------------------------- #
# 주기적 최신화 루프
# --------------------------------------------------------------------------- #


def test_run_funding_refresh_backfills_and_refreshes(store: FundingRateStore) -> None:
    import time

    from config.settings import Settings

    # 백필 룩백(기본 30일) 안에 들도록 최근 시각 기준으로 이력을 생성한다.
    recent_start = int(time.time() * 1000) - 5 * EIGHT_H
    exchange = FakeFundingExchange(history=_history(3, start=recent_start))
    settings = Settings(symbols=[SYMBOL], funding_refresh_interval_seconds=0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    asyncio.run(
        run_funding_refresh(
            settings,
            exchange=exchange,
            store=store,
            max_cycles=2,
            sleeper=fake_sleep,
        )
    )
    # 확정 이력 3건 백필 + 예측 현재값 1건(서로 다른 funding_time) 저장.
    assert store.count(SYMBOL) == 4
    assert exchange.current_calls == 2  # max_cycles=2
    assert sleeps == [0.0]  # 사이클 사이 1회 대기


def test_run_funding_refresh_disabled_returns_early(store: FundingRateStore) -> None:
    from config.settings import Settings

    exchange = FakeFundingExchange(history=_history(3))
    settings = Settings(symbols=[SYMBOL], funding_enabled=False)
    asyncio.run(run_funding_refresh(settings, exchange=exchange, store=store))
    assert store.count() == 0
    assert exchange.current_calls == 0
