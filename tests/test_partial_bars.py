"""부분 봉 스캔·분류 테스트 (WAN-327).

가짜 저장소에 1m 봉과 그로부터 리샘플한 15m 봉을 심고, 그 15m 봉을 세 가지로 오염시켜
분류가 갈리는지 본다. 핵심은 **판정자가 가격이 아니라 거래량**이라는 것 — 가격이 맞아도
거래량이 모자라면 손상이다(실측 반례가 있어 그렇게 정했다).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest

from data.models import Candle
from data.partial_bars import (
    PARTIAL_VOLUME_RATIO,
    classify_bucket,
    repair_frame,
    scan_series,
    scan_symbol,
)
from data.resample import resample_ohlcv
from data.storage import OhlcvStore
from data.verify import verify_resample_parity

TF_MS = 60_000
SYMBOL = "BTC/USDT:USDT"
TARGET = "15m"


def _one_minute(count: int, start: int = 0) -> list[Candle]:
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


def _seed_native(store: OhlcvStore, target_tf: str = TARGET) -> None:
    df = store.load(SYMBOL, "1m")
    resampled = resample_ohlcv(df, "1m", target_tf)
    store.upsert_candles(
        [
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
    )


def _stored_bucket(store: OhlcvStore, open_time: int) -> Candle:
    df = store.load(SYMBOL, TARGET)
    row = df[df["open_time"] == open_time].iloc[0]
    return Candle(
        symbol=SYMBOL,
        timeframe=TARGET,
        open_time=open_time,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
    )


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    s.upsert_candles(_one_minute(30))
    _seed_native(s)
    try:
        yield s
    finally:
        s.close()


def test_clean_series_has_no_discrepancy(store: OhlcvStore) -> None:
    scan = scan_series(store, SYMBOL, TARGET)
    assert scan.compared == 2
    assert scan.discrepancies == []
    assert scan.ok
    assert scan.damaged_span is None


def test_partial_bar_with_correct_prices_is_damaged(store: OhlcvStore) -> None:
    """🚨 회귀 방지의 핵심 — 가격이 **맞아도** 거래량이 모자라면 손상이다.

    그 버킷의 고가·저가가 잘리기 전에 이미 찍혀 있으면 부분 봉이어도 가격이 맞는다
    (실측: 2026-07-21 BNB 4h는 거래량 41.9%인데 high 오차 0.0bp). 「가격이 틀렸는가」로
    가르던 옛 기준은 이런 봉을 통째로 놓쳤다.
    """
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, volume=bucket.volume * 0.42)])

    scan = scan_series(store, SYMBOL, TARGET)
    assert len(scan.damaged) == 1
    found = scan.damaged[0]
    assert found.kind == "partial"
    assert found.price_fields == ()  # 가격은 멀쩡하다
    assert found.max_price_bp == 0.0
    assert found.volume_ratio == pytest.approx(0.42)
    assert not scan.ok


def test_price_mismatch_without_volume_shortfall_is_damaged(store: OhlcvStore) -> None:
    """거래량은 모자라지 않는데 가격이 다르면 원인 미상이라도 손상으로 센다."""
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, high=bucket.high + 50.0)])

    scan = scan_series(store, SYMBOL, TARGET)
    assert [d.kind for d in scan.damaged] == ["price_only"]
    assert scan.damaged[0].price_fields == ("high",)
    assert scan.damaged[0].max_price_bp > 0.0


def test_volume_noise_is_not_damage(store: OhlcvStore) -> None:
    """가격이 같고 저장 거래량이 **더 크면** 무해 — 판정(하드 실패)에 넣지 않는다."""
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, volume=bucket.volume * 1.002)])

    scan = scan_series(store, SYMBOL, TARGET)
    assert scan.damaged == []
    assert [d.kind for d in scan.noise] == ["volume_noise"]
    assert scan.ok  # 노이즈만 있으면 통과다


def test_volume_shortfall_just_inside_threshold_is_noise(store: OhlcvStore) -> None:
    """문턱은 `PARTIAL_VOLUME_RATIO` 한 곳에서만 정의된다 — 바로 안쪽은 노이즈."""
    bucket = _stored_bucket(store, 0)
    inside = bucket.volume * (PARTIAL_VOLUME_RATIO + 0.005)
    store.upsert_candles([dataclasses.replace(bucket, volume=inside)])
    assert scan_series(store, SYMBOL, TARGET).damaged == []

    outside = bucket.volume * (PARTIAL_VOLUME_RATIO - 0.005)
    store.upsert_candles([dataclasses.replace(bucket, volume=outside)])
    assert [d.kind for d in scan_series(store, SYMBOL, TARGET).damaged] == ["partial"]


def test_verify_agrees_with_scan_on_the_same_judge(store: OhlcvStore) -> None:
    """`verify`와 스캔이 같은 자를 쓴다 — 「스캔은 손상인데 verify는 통과」가 없어야 한다."""
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, volume=bucket.volume * 0.42)])
    report = verify_resample_parity(store, SYMBOL, "1m", TARGET)
    assert not report.ok
    assert [d.kind for d in report.damaged] == ["partial"]
    assert report.noise == []


def test_verify_volume_noise_no_longer_hard_fails(store: OhlcvStore) -> None:
    """§3 — 거래량 노이즈는 보고는 하되 하드 실패가 아니다(상시 빨간불 방지)."""
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, volume=bucket.volume * 1.002)])
    report = verify_resample_parity(store, SYMBOL, "1m", TARGET)
    assert report.ok  # 판정은 통과
    assert len(report.noise) == 1  # 그래도 보고는 된다
    assert report.mismatches  # 필드 단위 상세는 그대로 남는다


def test_scan_is_chunk_size_invariant(store: OhlcvStore) -> None:
    """`chunk_days`는 메모리 노브이지 결과 축이 아니다."""
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, volume=bucket.volume * 0.5)])
    wide = scan_series(store, SYMBOL, TARGET, chunk_days=365)
    narrow = scan_series(store, SYMBOL, TARGET, chunk_days=1)
    assert wide.compared == narrow.compared
    assert [(d.open_time, d.kind) for d in wide.discrepancies] == [
        (d.open_time, d.kind) for d in narrow.discrepancies
    ]


def test_scan_symbol_shares_one_source_load(store: OhlcvStore) -> None:
    """여러 TF를 한 번에 스캔해도 시리즈별 결과는 단건 스캔과 같다."""
    store.upsert_candles(_one_minute(120))
    _seed_native(store, "15m")
    _seed_native(store, "1h")
    both = {sc.timeframe: sc for sc in scan_symbol(store, SYMBOL, ["15m", "1h"])}
    assert both["15m"].compared == scan_series(store, SYMBOL, "15m").compared
    assert both["1h"].compared == scan_series(store, SYMBOL, "1h").compared


def test_repair_frame_touches_damaged_only(store: OhlcvStore) -> None:
    """반사실은 **손상 봉만** 갈아끼운다 — 노이즈 봉은 저장 값을 지킨다."""
    damaged = _stored_bucket(store, 0)
    noisy = _stored_bucket(store, 15 * TF_MS)
    store.upsert_candles(
        [
            dataclasses.replace(damaged, volume=damaged.volume * 0.3, high=damaged.high - 7),
            dataclasses.replace(noisy, volume=noisy.volume * 1.002),
        ]
    )
    stored = store.load(SYMBOL, TARGET)
    resampled = resample_ohlcv(store.load(SYMBOL, "1m"), "1m", TARGET)

    fixed, replaced = repair_frame(stored, resampled)
    assert replaced == 1
    fixed_first = fixed[fixed["open_time"] == 0].iloc[0]
    ref_first = resampled[resampled["open_time"] == 0].iloc[0]
    assert float(fixed_first["high"]) == pytest.approx(float(ref_first["high"]))
    assert float(fixed_first["volume"]) == pytest.approx(float(ref_first["volume"]))
    # 노이즈 봉은 저장 값 그대로다(1분봉 쪽이 모자란 것이라 덮으면 오히려 나빠진다).
    fixed_second = fixed[fixed["open_time"] == 15 * TF_MS].iloc[0]
    assert float(fixed_second["volume"]) == pytest.approx(noisy.volume * 1.002)


def test_repair_frame_is_identity_when_clean(store: OhlcvStore) -> None:
    """손상이 없으면 반사실이 항등이다 — 「교정 0봉」 칸의 두 팔이 비트 단위로 같다."""
    stored = store.load(SYMBOL, TARGET)
    resampled = resample_ohlcv(store.load(SYMBOL, "1m"), "1m", TARGET)
    fixed, replaced = repair_frame(stored, resampled)
    assert replaced == 0
    cols = ["open", "high", "low", "close", "volume"]
    assert fixed[cols].equals(stored.reset_index(drop=True)[cols])


def test_classify_bucket_is_pure_and_symmetric_on_equal_rows(store: OhlcvStore) -> None:
    """같은 행끼리는 불일치가 없다(자기 자신과의 비교가 None)."""
    df = store.load(SYMBOL, TARGET)
    row = next(df.itertuples(index=False))
    assert classify_bucket(SYMBOL, TARGET, int(row.open_time), row, row) is None


# --------------------------------------------------------------------------- #
# 하네스·북 옵트인 배선 (WAN-327 §2 — 비파괴 반사실)
# --------------------------------------------------------------------------- #


def _seed_db(path: str) -> None:
    """1m 30봉 + 그로부터 만든 15m 2봉을 실제 파일 DB에 심고, 첫 15m 봉을 부분 봉으로 만든다."""
    store = OhlcvStore(path)
    try:
        store.upsert_candles(_one_minute(30))
        _seed_native(store, TARGET)
        stored = store.load(SYMBOL, TARGET)
        row = stored.iloc[0]
        store.upsert_candles(
            [
                Candle(
                    symbol=SYMBOL,
                    timeframe=TARGET,
                    open_time=int(row["open_time"]),
                    open=float(row["open"]),
                    high=float(row["high"]) - 4.0,
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]) * 0.4,
                )
            ]
        )
    finally:
        store.close()


def test_load_market_data_repair_is_opt_in(tmp_path: object) -> None:
    """끄면(기본) 저장 봉 그대로, 켜면 손상 봉만 1분봉 합으로 갈린다 — DB는 불변이다."""
    from pathlib import Path

    from backtest import harness

    db = str(Path(str(tmp_path)) / "t.db")
    _seed_db(db)

    stored = harness.load_market_data(
        SYMBOL, TARGET, years=10.0, need_1m=True, funding=False, db_path=db, cache_dir=str(tmp_path)
    )
    fixed = harness.load_market_data(
        SYMBOL,
        TARGET,
        years=10.0,
        need_1m=True,
        funding=False,
        db_path=db,
        cache_dir=str(tmp_path),
        repair_htf_from_1m=True,
    )
    cols = ["open", "high", "low", "close", "volume"]
    assert not stored.htf_df[cols].equals(fixed.htf_df[cols])  # 실제로 갈렸다
    assert float(fixed.htf_df.iloc[0]["high"]) > float(stored.htf_df.iloc[0]["high"])
    # 두 번째 봉(멀쩡한 봉)은 손대지 않는다.
    assert float(fixed.htf_df.iloc[1]["high"]) == float(stored.htf_df.iloc[1]["high"])
    # 1분봉·창은 그대로다(차이가 상위TF 봉 하나로 격리된다).
    assert len(fixed.df_1m) == len(stored.df_1m)
    assert list(fixed.htf_df["open_time"]) == list(stored.htf_df["open_time"])

    # DB는 안 바뀐다 — 반사실은 메모리 전용이다(WAN-194 원칙).
    again = harness.load_market_data(
        SYMBOL, TARGET, years=10.0, need_1m=True, funding=False, db_path=db, cache_dir=str(tmp_path)
    )
    assert again.htf_df[cols].equals(stored.htf_df[cols])


def test_load_market_data_repair_requires_1m(tmp_path: object) -> None:
    """1분봉 없이 켜면 조용히 넘어가지 않고 거부한다(라벨만 붙는 실패 방지)."""
    from pathlib import Path

    from backtest import harness

    db = str(Path(str(tmp_path)) / "t.db")
    _seed_db(db)
    with pytest.raises(ValueError, match="1분봉"):
        harness.load_market_data(
            SYMBOL,
            TARGET,
            years=10.0,
            need_1m=False,
            funding=False,
            db_path=db,
            cache_dir=str(tmp_path),
            repair_htf_from_1m=True,
        )


def test_book_task_repair_flag_defaults_off_and_passes_through() -> None:
    """북 칸의 옵트인 필드가 기본 꺼짐이고 `run_cells`가 그대로 넘긴다."""
    from backtest.wan169_leverage_book import _Task

    assert _Task(symbol=SYMBOL, timeframe="4h", start_ms=0, end_ms=1).repair_partial_bars is False
    assert (
        _Task(
            symbol=SYMBOL, timeframe="4h", start_ms=0, end_ms=1, repair_partial_bars=True
        ).repair_partial_bars
        is True
    )


def test_bit_identical_ratio_is_a_provenance_fingerprint(store: OhlcvStore) -> None:
    """§1-3 — 「불일치 0」과 「검사 미성립」을 가르는 지문.

    이 픽스처의 15m 봉은 1분봉 리샘플을 그대로 심은 것이라(= `data.aggregate` 유래와 같은
    모양) 비트 일치율이 1.0이다. 저장 값을 아주 조금만 흔들면(허용오차 안이라 불일치로는
    안 잡히는 크기) 비율이 떨어진다 — 독립 수집분의 서명이다.
    """
    assert scan_series(store, SYMBOL, TARGET).bit_identical_ratio == 1.0

    bucket = _stored_bucket(store, 0)
    nudged = bucket.volume * (1 + 1e-9)  # `VOLUME_REL_TOL`(1e-6) 안 — 불일치가 아니다
    store.upsert_candles([dataclasses.replace(bucket, volume=nudged)])
    scan = scan_series(store, SYMBOL, TARGET)
    assert scan.discrepancies == []  # 여전히 「불일치 0」인데
    assert scan.bit_identical_ratio == 0.5  # 지문은 갈린다
