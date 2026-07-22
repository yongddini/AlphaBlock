"""data.aggregate — 1분봉 → native 상위 TF 집계 봉 빌드 테스트 (WAN-175).

핵심 계약을 라벨이 아니라 동작으로 고정한다:

1. **기존 행 비트 단위 불변** — 기존 봉과 다른 값이 리샘플에서 나와도 기존
   행을 덮어쓰지 않는다(컷오프 필터 + INSERT-IGNORE 이중 방어를 각각 검증).
2. **앞쪽 백필** — 기존 첫 봉 이전 구간만 채워지고, 채운 뒤 시리즈가 연속이다.
3. **신규 심볼 전 구간 빌드** — 미완(양끝) 버킷은 만들지 않는다.
4. **dry-run은 쓰지 않는다.**
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from data.aggregate import (
    DEFAULT_TARGET_TIMEFRAMES,
    build_symbol,
    main,
    symbols_with_source,
)
from data.gaps import find_gaps
from data.models import Candle
from data.storage import OhlcvStore
from data.verify import verify_resample_parity

_SYMBOL = "DOGE/USDT:USDT"
_MIN = 60_000
_15M = 900_000


def _minute_candles(start_ms: int, count: int, *, base: float = 100.0) -> list[Candle]:
    """`start_ms`부터 1분 간격 `count`개의 결정적 1m 봉을 만든다."""
    out: list[Candle] = []
    for i in range(count):
        o = base + i
        out.append(
            Candle(
                symbol=_SYMBOL,
                timeframe="1m",
                open_time=start_ms + i * _MIN,
                open=o,
                high=o + 0.5,
                low=o - 0.5,
                close=o + 0.25,
                volume=10.0 + i,
                closed=True,
            )
        )
    return out


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    with OhlcvStore(":memory:") as s:
        yield s


def test_full_build_skips_incomplete_edge_buckets(store: OhlcvStore) -> None:
    """신규 심볼: 버킷 중간에서 시작·끝나는 1m는 상위 봉을 만들지 않는다."""
    # 00:05 시작(첫 15m 버킷 미완) ~ 00:05+40분 = 00:45 직전(마지막 버킷 미완).
    store.upsert_candles(_minute_candles(5 * _MIN, 40))

    results = build_symbol(store, _SYMBOL, ("15m",))

    (r,) = results
    assert r.existing_first_ms is None
    # 온전한 버킷은 00:15, 00:30 둘뿐이다(00:00·00:45 버킷은 미완).
    assert r.built == 2
    assert r.inserted == 2
    assert store.open_times(_SYMBOL, "15m") == [_15M, 2 * _15M]


def test_front_fill_only_adds_before_existing_first(store: OhlcvStore) -> None:
    """기존 봉이 있으면 그 이전 구간만 채우고, 채운 뒤 시리즈가 연속이다."""
    # 1m: 00:00~02:00 (8버킷 = 120분).
    store.upsert_candles(_minute_candles(0, 120))
    # 기존 15m native 봉: 01:00부터(= 뒤쪽 4버킷이 이미 있다). 값은 리샘플과
    # 일부러 다르게 넣는다 — 덮어쓰이면 아래 비트 불변 단언이 잡는다.
    existing = [
        Candle(_SYMBOL, "15m", 4 * _15M + i * _15M, 1.0, 2.0, 0.5, 1.5, 999.0, True)
        for i in range(4)
    ]
    store.upsert_candles(existing)

    results = build_symbol(store, _SYMBOL, ("15m",))

    (r,) = results
    assert r.existing_first_ms == 4 * _15M
    assert r.built == 4  # 00:00·00:15·00:30·00:45 — 01:00 이후는 생성 대상이 아니다
    assert r.inserted == 4
    # 연속: 00:00부터 8버킷, 갭 없음.
    times = store.open_times(_SYMBOL, "15m")
    assert times == [i * _15M for i in range(8)]
    assert find_gaps(times, "15m") == []
    # 기존 행 비트 단위 불변 — 리샘플 값(≠999.0)으로 덮어쓰이지 않았다.
    df = store.load(_SYMBOL, "15m", start_ms=4 * _15M)
    assert (df["volume"] == 999.0).all()


def test_insert_ignore_defends_even_without_cutoff(store: OhlcvStore) -> None:
    """이중 방어: INSERT-IGNORE 자체가 기존 행을 보존한다(컷오프와 독립)."""
    store.upsert_candles([Candle(_SYMBOL, "15m", 0, 1.0, 2.0, 0.5, 1.5, 999.0, True)])
    clashing = [Candle(_SYMBOL, "15m", 0, 7.0, 8.0, 6.0, 7.5, 1.0, True)]

    inserted = store.insert_candles_ignore(clashing)

    assert inserted == 0
    df = store.load(_SYMBOL, "15m")
    assert float(df["volume"].iloc[0]) == 999.0


def test_dry_run_writes_nothing(store: OhlcvStore) -> None:
    store.upsert_candles(_minute_candles(0, 30))

    results = build_symbol(store, _SYMBOL, ("15m",), dry_run=True)

    (r,) = results
    assert r.built == 2
    assert r.inserted == 0
    assert store.count(_SYMBOL, "15m") == 0


def test_built_bars_pass_resample_parity(store: OhlcvStore) -> None:
    """생성 봉은 파리티 검증(1m ↔ 상위 TF)을 통과한다 — 완료 기준의 검증 경로."""
    store.upsert_candles(_minute_candles(0, 240))  # 4시간

    for tf in ("15m", "1h", "4h"):
        build_symbol(store, _SYMBOL, (tf,))
        report = verify_resample_parity(store, _SYMBOL, "1m", tf)
        assert report.compared > 0, tf
        assert report.ok, (tf, report.mismatches)


def test_no_source_returns_empty(store: OhlcvStore) -> None:
    assert build_symbol(store, "LINK/USDT:USDT", DEFAULT_TARGET_TIMEFRAMES) == []


def test_symbols_with_source_lists_only_1m_holders(store: OhlcvStore) -> None:
    store.upsert_candles(_minute_candles(0, 15))
    store.upsert_candles([Candle("LTC/USDT:USDT", "1h", 0, 1.0, 2.0, 0.5, 1.5, 1.0, True)])
    assert symbols_with_source(store) == [_SYMBOL]


def test_cli_dry_run_smoke(tmp_path: object, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI가 dry-run으로 돌고 표를 출력한다(파일 DB 왕복 포함)."""
    import pathlib

    assert isinstance(tmp_path, pathlib.Path)
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as s:
        s.upsert_candles(_minute_candles(0, 30))

    rc = main(["--db", str(db), "--timeframes", "15m", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    with OhlcvStore(db) as s:
        assert s.count(_SYMBOL, "15m") == 0
