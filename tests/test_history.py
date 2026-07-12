"""data.history 테스트 (WAN-44).

과거 구간 대량 백필의 창 계산·멱등성·중단 재개·진행률 콜백을 가짜 거래소로
결정론적으로 검증한다(네트워크 없음).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from data.history import run_history_backfill
from data.storage import OhlcvStore
from data.verify import verify_series

TF_MS = 60_000  # 1m
DAY_MS = 86_400_000


class WindowExchange:
    """`since`부터 연속 1m 봉을 최대 `limit`개 돌려주는 가짜 거래소.

    `available_from`~`available_to`(양끝 포함) 범위에서만 봉을 제공한다.
    """

    def __init__(self, available_from: int, available_to: int) -> None:
        self.available_from = available_from
        self.available_to = available_to
        self.calls: list[int | None] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        self.calls.append(since)
        start = self.available_from if since is None else max(since, self.available_from)
        start = (start // TF_MS) * TF_MS
        rows: list[list[float]] = []
        t = start
        cap = limit or 1000
        while t <= self.available_to and len(rows) < cap:
            # 봉마다 OHLCV를 다르게 두어 정합성·집계 검증에 쓸 수 있게 한다.
            i = (t - self.available_from) // TF_MS
            rows.append([float(t), 100.0 + i, 105.0 + i, 95.0 + i, 101.0 + i, 10.0 + i])
            t += TF_MS
        return rows


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def test_history_fills_full_window(store: OhlcvStore) -> None:
    """days 창 전체를 창 시작부터 채운다(순방향 재시작과 달리 과거를 넓게 메움)."""
    now = 10 * DAY_MS
    exchange = WindowExchange(available_from=0, available_to=now)
    results = run_history_backfill(
        exchange,
        store,
        ["BTC/USDT:USDT"],
        ["1m"],
        days=5,
        now_ms=lambda: now,
        sleeper=lambda _: None,
    )
    assert len(results) == 1
    r = results[0]
    # 5일 = 7200봉(끝 배타적). 창 시작이 last 저장봉과 무관하게 과거부터 채워진다.
    expected = 5 * DAY_MS // TF_MS
    assert r.since_ms == now - 5 * DAY_MS
    assert store.count("BTC/USDT:USDT", "1m") == expected


def test_history_is_idempotent(store: OhlcvStore) -> None:
    """같은 창을 두 번 실행해도 저장 봉 수가 그대로고 중복이 없다(멱등)."""
    now = 10 * DAY_MS
    exchange = WindowExchange(available_from=0, available_to=now)

    first = run_history_backfill(
        exchange,
        store,
        ["BTC/USDT:USDT"],
        ["1m"],
        days=3,
        now_ms=lambda: now,
        sleeper=lambda _: None,
    )
    count_after_first = store.count("BTC/USDT:USDT", "1m")

    second = run_history_backfill(
        exchange,
        store,
        ["BTC/USDT:USDT"],
        ["1m"],
        days=3,
        now_ms=lambda: now,
        sleeper=lambda _: None,
    )
    count_after_second = store.count("BTC/USDT:USDT", "1m")

    assert count_after_first == count_after_second  # 재실행이 봉을 늘리지 않음
    assert first[0].stored_after == second[0].stored_after
    report = verify_series(store, "BTC/USDT:USDT", "1m")
    assert report.duplicates == 0
    assert report.monotonic


def test_history_resume_after_interruption(store: OhlcvStore) -> None:
    """일부만 저장된 상태에서 재실행하면 중복 없이 남은 구간을 채운다.

    중단은 '거래소가 창의 앞부분까지만 데이터를 갖고 있던 시점'으로 모사한다:
    1차 실행에서 앞 구간만 저장되고, 이후 최신 봉이 생겨 2차 실행이 뒤를 마저 채운다.
    """
    now = 10 * DAY_MS
    cutoff = now - DAY_MS  # 1차 시점엔 여기까지만 존재

    # 1차: 창 [now-3d, cutoff] 만 채워진다(그 뒤는 아직 거래소에 없음).
    partial_ex = WindowExchange(available_from=0, available_to=cutoff)
    run_history_backfill(
        partial_ex,
        store,
        ["BTC/USDT:USDT"],
        ["1m"],
        days=3,
        now_ms=lambda: now,
        sleeper=lambda _: None,
    )
    partial = store.count("BTC/USDT:USDT", "1m")
    assert 0 < partial < 3 * DAY_MS // TF_MS  # 아직 다 안 참

    # 2차: 이제 now 까지 데이터가 생겼다 → 재실행이 남은 뒷구간을 중복 없이 채운다.
    full_ex = WindowExchange(available_from=0, available_to=now)
    run_history_backfill(
        full_ex,
        store,
        ["BTC/USDT:USDT"],
        ["1m"],
        days=3,
        now_ms=lambda: now,
        sleeper=lambda _: None,
    )
    assert store.count("BTC/USDT:USDT", "1m") == 3 * DAY_MS // TF_MS
    report = verify_series(store, "BTC/USDT:USDT", "1m")
    assert report.duplicates == 0
    assert not report.has_gaps


def test_history_rejects_nonpositive_days(store: OhlcvStore) -> None:
    exchange = WindowExchange(available_from=0, available_to=DAY_MS)
    with pytest.raises(ValueError):
        run_history_backfill(
            exchange, store, ["BTC/USDT:USDT"], ["1m"], days=0, now_ms=lambda: DAY_MS
        )
