"""data.repair 테스트 — 갭 자동 복구 백필 (WAN-35).

가짜 거래소를 주입해 네트워크 없이 결정론적으로 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import ccxt
import pytest

from data.gaps import Gap, find_gaps
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


def test_repair_never_loads_the_whole_series(store: OhlcvStore) -> None:
    """갭 탐지가 `store.load`(pandas 전체 로드)를 **타지 않는다** (WAN-187).

    라벨이 아니라 동작으로 고정한다 — 옛 경로는 시리즈를 9열 DataFrame으로 통째로
    올려(6년 1m 기준 9.9초 · RSS 2.3GB) 수집기 시작 갭 복구가 웹소켓 접속 직전에
    매달렸다. 여기서 `load`를 폭탄으로 바꿔 두면, 누가 편의상 그 경로로 되돌릴 때
    테스트가 먼저 터진다.
    """
    store.upsert_candles(_candles([0, 1, 2, 6, 8, 9]))  # 3,4,5,7 누락

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("갭 탐지가 시리즈를 통째로 로드했다")

    monkeypatched = store.load
    store.load = boom  # type: ignore[method-assign]
    try:
        result = repair_series(
            FakeExchange(list(range(10))), store, SYMBOL, TF, sleeper=lambda _: None
        )
    finally:
        store.load = monkeypatched  # type: ignore[method-assign]

    # 옛 경로와 같은 답이어야 의미가 있다(복구 전 탐지 · 복구 후 잔여 재계산 둘 다).
    assert result.gaps_found == 2  # 3-5, 7
    assert result.bars_filled == 4
    assert result.bars_remaining == 0


def test_repair_lookback_window_skips_older_gaps(store: OhlcvStore) -> None:
    """최근 창 한정 점검은 창 밖 갭을 건드리지 않는다 (WAN-187).

    6년 DB에서 전 구간 스캔이 수집기 시작을 늦추므로 시작 경로만 창을 좁혔다. 창
    밖은 `alphablock backfill --repair`(전 구간) 소관이다.
    """
    # 옛 갭(2-4)은 창 밖에서 닫히고, 최근 갭(21-23)만 창 안이다.
    store.upsert_candles(_candles([0, 1, *range(5, 21), 24]))
    exchange = FakeExchange(list(range(25)))
    now = T0 + 24 * TF_MS

    result = repair_series(
        exchange,
        store,
        SYMBOL,
        TF,
        sleeper=lambda _: None,
        now_ms=lambda: now,
        lookback_ms=10 * TF_MS,  # 인덱스 14 이후만
    )

    assert result.gaps_found == 1  # 21-23만
    assert result.bars_filled == 3
    assert result.bars_remaining == 0
    # 창 밖 옛 갭(2-4)은 그대로 남는다 — 전 구간 점검(=`--repair`)은 여전히 그걸 본다.
    assert find_gaps(_timestamps(store), TF) == [
        Gap(start_ms=T0 + 2 * TF_MS, end_ms=T0 + 4 * TF_MS, missing=3)
    ]


def test_repair_lookback_sees_gap_straddling_the_window_edge(store: OhlcvStore) -> None:
    """창 경계를 가로지르는 갭도 보인다 — 앵커 봉 하나를 함께 훑기 때문이다."""
    store.upsert_candles(_candles([0, 1, 2, 20]))  # 갭 3-19가 창 경계를 가로지른다
    exchange = FakeExchange(list(range(21)))
    now = T0 + 20 * TF_MS

    result = repair_series(
        exchange,
        store,
        SYMBOL,
        TF,
        sleeper=lambda _: None,
        now_ms=lambda: now,
        lookback_ms=5 * TF_MS,  # 창은 인덱스 15부터 — 갭의 앞봉(2)은 창 밖이다
    )

    assert result.gaps_found == 1
    assert result.bars_filled == 17
    assert store.count(SYMBOL, TF) == 21


def test_repair_all_records_the_window_it_looked_at(store: OhlcvStore) -> None:
    """요약이 「어디까지 봤는지」를 남긴다 — 갭 0을 전 구간 무결로 읽지 않도록."""
    store.upsert_candles(_candles([0, 1, 2]))
    summary = repair_all(
        FakeExchange([0, 1, 2]), store, sleeper=lambda _: None, lookback_ms=3 * TF_MS
    )
    assert summary.lookback_ms == 3 * TF_MS
    # 창을 안 주면(전 구간) None이라 화면에도 창 표기가 안 붙는다.
    assert repair_all(FakeExchange([0, 1, 2]), store, sleeper=lambda _: None).lookback_ms is None
