"""data.verify 테스트 (WAN-44).

가짜 저장소에 1m 봉과 그로부터 리샘플한 상위 TF 봉을 심어, 갭·중복·정렬·상위TF
정합성 검증이 정확히 동작하는지 확인한다(네트워크·부수효과 없음).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from data.models import Candle
from data.resample import resample_ohlcv
from data.storage import OhlcvStore
from data.verify import verify_all, verify_resample_parity, verify_series

TF_MS = 60_000  # 1m
SYMBOL = "BTC/USDT:USDT"


def _one_minute(count: int, start: int = 0) -> list[Candle]:
    """OHLCV가 봉마다 달라지는 1m 봉 `count`개."""
    out: list[Candle] = []
    for i in range(count):
        base = 100.0 + i
        out.append(
            Candle(
                symbol=SYMBOL,
                timeframe="1m",
                open_time=start + i * TF_MS,
                open=base,
                high=base + 5,
                low=base - 3,
                close=base + 1,
                volume=10.0 + i,
            )
        )
    return out


def _seed_native_higher_tf(store: OhlcvStore, target_tf: str) -> None:
    """저장된 1m을 리샘플해 그 결과를 `target_tf` 네이티브 봉으로 심는다."""
    df = store.load(SYMBOL, "1m")
    resampled = resample_ohlcv(df, "1m", target_tf)
    candles = [
        Candle(
            symbol=SYMBOL,
            timeframe=target_tf,
            open_time=int(row.open_time),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in resampled.itertuples(index=False)
    ]
    store.upsert_candles(candles)


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def test_verify_series_clean(store: OhlcvStore) -> None:
    store.upsert_candles(_one_minute(60))
    report = verify_series(store, SYMBOL, "1m")
    assert report.bar_count == 60
    assert not report.has_gaps
    assert report.duplicates == 0
    assert report.monotonic
    assert report.integrity_ok


def test_verify_series_detects_gap(store: OhlcvStore) -> None:
    candles = _one_minute(30) + _one_minute(10, start=40 * TF_MS)
    store.upsert_candles(candles)
    report = verify_series(store, SYMBOL, "1m")
    assert report.has_gaps
    # 30번째(끝 open=29분) 다음부터 40분 전까지 10봉이 빈다.
    assert report.missing == 10
    assert report.integrity_ok  # 갭은 하드 실패가 아님(중복·역순 아님)


def test_verify_series_empty(store: OhlcvStore) -> None:
    report = verify_series(store, SYMBOL, "1m")
    assert report.bar_count == 0
    assert report.first_ms is None
    assert not report.has_gaps
    assert report.integrity_ok


def test_resample_parity_matches(store: OhlcvStore) -> None:
    """1m 두 개의 15m 버킷을 리샘플한 결과가 저장된 15m과 일치한다."""
    store.upsert_candles(_one_minute(30))  # 두 개의 15m 버킷
    _seed_native_higher_tf(store, "15m")
    report = verify_resample_parity(store, SYMBOL, "1m", "15m")
    assert report.compared == 2
    assert report.ok


def test_resample_parity_flags_mismatch(store: OhlcvStore) -> None:
    """저장된 상위 TF 봉이 리샘플과 어긋나면 불일치로 보고한다."""
    store.upsert_candles(_one_minute(30))
    _seed_native_higher_tf(store, "15m")
    # 첫 15m 봉의 high 를 인위적으로 오염.
    store.upsert_candles(
        [
            Candle(
                symbol=SYMBOL,
                timeframe="15m",
                open_time=0,
                open=100.0,
                high=999.0,  # 리샘플 값과 다름
                low=97.0,
                close=101.0,
                volume=10.0,
            )
        ]
    )
    report = verify_resample_parity(store, SYMBOL, "1m", "15m")
    assert not report.ok
    assert any(m.field == "high" and m.open_time == 0 for m in report.mismatches)


def test_verify_all_aggregates(store: OhlcvStore) -> None:
    store.upsert_candles(_one_minute(30))
    _seed_native_higher_tf(store, "15m")
    # 이 픽스처의 봉은 에폭 0에서 시작하므로 벽시계 기준으로는 **실제로 정지 상태**다
    # (WAN-156 신선도 판정). 무결성만 보려는 테스트라 기준 시각을 창 끝에 고정한다.
    report = verify_all(
        store,
        [SYMBOL],
        ["1m", "15m"],
        parity_targets=("15m",),
        now_ms=30 * TF_MS,
    )
    assert report.ok
    assert not report.has_stale
    assert report.strict_ok  # 갭 없음
    assert len(report.series) == 2
    assert len(report.parity) == 1
    assert report.parity[0].target_timeframe == "15m"


def test_open_times_sorted(store: OhlcvStore) -> None:
    """open_times 는 삽입 순서와 무관하게 오름차순으로 반환된다."""
    store.upsert_candles(_one_minute(5, start=10 * TF_MS))
    store.upsert_candles(_one_minute(5))
    times = store.open_times(SYMBOL, "1m")
    assert times == sorted(times)
    assert len(times) == 10
