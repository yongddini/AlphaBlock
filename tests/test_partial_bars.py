"""부분 봉 스캔·분류 테스트 (WAN-327).

가짜 저장소에 1m 봉과 그로부터 리샘플한 15m 봉을 심고, 그 15m 봉을 세 가지로 오염시켜
분류가 갈리는지 본다. 핵심은 **판정자가 가격이 아니라 거래량**이라는 것 — 가격이 맞아도
거래량이 모자라면 손상이다(실측 반례가 있어 그렇게 정했다).
"""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Iterator

import pytest

from data.models import Candle
from data.partial_bars import (
    PARTIAL_VOLUME_RATIO,
    PRICE_NOISE_TICKS,
    BarDiscrepancy,
    classify_bucket,
    infer_price_tick,
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


# --------------------------------------------------------------------------- #
# 가격 축 「손상 vs 노이즈」 (WAN-337 §2)
# --------------------------------------------------------------------------- #


def test_price_noise_within_tick_grid_is_not_damage(store: OhlcvStore) -> None:
    """호가 한 칸짜리 차이는 손상이 아니다 — 상시 빨간불을 만들던 부류(WAN-337 §2).

    합성 봉의 값은 소수 한 자리(`100.0`꼴)라 눈금이 `0.1`이고, 한 칸만 옮기면 노이즈다.
    """
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, close=bucket.close + 0.1)])

    scan = scan_series(store, SYMBOL, TARGET)
    assert scan.damaged == []
    assert [d.kind for d in scan.noise] == ["price_noise"]
    assert scan.noise[0].price_fields == ("close",)
    assert scan.noise[0].max_price_ticks == pytest.approx(1.0)
    assert scan.ok  # 노이즈만 있으면 통과다


def test_price_tick_threshold_is_defined_once(store: OhlcvStore) -> None:
    """문턱은 `PRICE_NOISE_TICKS` 한 곳에서만 정의된다 — 바로 안쪽/바깥쪽이 갈린다."""
    bucket = _stored_bucket(store, 0)
    tick = 0.1  # 합성 봉의 호가 눈금(소수 한 자리)

    inside = (PRICE_NOISE_TICKS - 1) * tick
    store.upsert_candles([dataclasses.replace(bucket, close=bucket.close + inside)])
    assert scan_series(store, SYMBOL, TARGET).damaged == []

    outside = (PRICE_NOISE_TICKS + 1) * tick
    store.upsert_candles([dataclasses.replace(bucket, close=bucket.close + outside)])
    assert [d.kind for d in scan_series(store, SYMBOL, TARGET).damaged] == ["price_only"]


def test_volume_shortfall_beats_price_noise(store: OhlcvStore) -> None:
    """가격이 호가 한 칸만 달라도 **거래량이 모자라면 부분 봉이다** — 판정자는 거래량이다.

    가격 문턱이 부분 봉을 조용히 노이즈로 삼키면 WAN-327이 만든 판정자가 무력화된다.
    """
    bucket = _stored_bucket(store, 0)
    store.upsert_candles(
        [dataclasses.replace(bucket, close=bucket.close + 0.1, volume=bucket.volume * 0.42)]
    )
    assert [d.kind for d in scan_series(store, SYMBOL, TARGET).damaged] == ["partial"]


def test_verify_agrees_with_scan_on_price_noise(store: OhlcvStore) -> None:
    """`verify`와 전 이력 스캔의 자가 갈라지지 않는다 — 분류는 `classify_bucket` 한 곳이다."""
    bucket = _stored_bucket(store, 0)
    store.upsert_candles([dataclasses.replace(bucket, close=bucket.close + 0.1)])

    parity = verify_resample_parity(store, SYMBOL, "1m", TARGET, sample_buckets=10)
    assert parity.damaged == []
    assert [d.kind for d in parity.noise] == ["price_noise"]
    assert parity.ok  # 하드 실패가 아니다


def test_infer_price_tick_reads_the_quote_grid() -> None:
    """호가 눈금은 값의 소수 자릿수에서 읽는다 — 가장 고운 값이 눈금을 정한다."""
    assert infer_price_tick([70276.6, 70331.0, 70075.5]) == pytest.approx(0.1)
    assert infer_price_tick([3584.12, 3587.01]) == pytest.approx(0.01)
    assert infer_price_tick([0.12064, 0.12069]) == pytest.approx(1e-5)
    # 한 값만 고와도 그 눈금을 따른다(거친 값이 눈금을 되돌리지 않는다).
    assert infer_price_tick([100.0, 100.25]) == pytest.approx(0.01)
    # 정수뿐이면 `repr(float)`가 `'100.0'`이라 눈금이 `0.1`로 잡힌다 — 실제 눈금보다 **고운**
    # 쪽이라 같은 차이가 더 많은 틱으로 세어진다(= 손상으로 찍히는 쪽). 안전한 방향이다.
    assert infer_price_tick([100, 101]) == pytest.approx(0.1)


# --- 실측 값 회귀 가드 — 문턱이 진짜 손상을 지우지 않는다 (WAN-337 §2 완료기준 3·5) --- #

#: 2024-03-27 12:30 BTC 15m 실측(로컬 DB) — `open`이 **475틱** 어긋난 진짜 손상.
_REAL_DAMAGE_2024_03_27 = (
    {"open": 70276.6, "high": 70331.0, "low": 70075.5, "close": 70087.9, "volume": 1749.455},
    {"open": 70324.1, "high": 70331.0, "low": 70075.5, "close": 70087.9, "volume": 1749.455},
)
#: 2026-07-18 02:30 SOL 15m 실측 — `close`가 **1틱**(그런데 **1.33bp**) 어긋난 호가 잔돈.
_REAL_NOISE_SOL = (
    {"open": 75.28, "high": 75.42, "low": 75.26, "close": 75.27, "volume": 80922.51},
    {"open": 75.28, "high": 75.42, "low": 75.26, "close": 75.26, "volume": 80919.99},
)
#: 2026-07-16 17:15 BTC 15m 실측 — `close`가 **15틱**(그런데 **0.23bp**) 어긋난 손상.
_REAL_DAMAGE_BTC_CLOSE = (
    {"open": 64134.8, "high": 64209.6, "low": 64059.1, "close": 64193.3, "volume": 2057.699},
    {"open": 64134.8, "high": 64209.6, "low": 64059.1, "close": 64191.8, "volume": 2046.065},
)


def _classify_real(pair: tuple[dict[str, float], dict[str, float]]) -> BarDiscrepancy:
    resampled, stored = pair
    found = classify_bucket(
        SYMBOL, TARGET, 0, types.SimpleNamespace(**resampled), types.SimpleNamespace(**stored)
    )
    assert found is not None
    return found


def test_price_threshold_does_not_erase_the_2024_03_27_damage() -> None:
    """🚨 함정 값 회귀 — 실측 손상이 문턱을 넘어 **손상으로 남는다**(WAN-330 가드와 같은 방식).

    문턱을 올려 경보를 줄이고 싶어지는 자리라, 지어낸 값이 아니라 **그날 그 봉의 실제 값**으로
    건다. 이 테스트가 깨지면 문턱이 진짜 손상을 삼킨 것이다.
    """
    found = _classify_real(_REAL_DAMAGE_2024_03_27)
    assert found.kind == "price_only"
    assert found.damaged
    assert found.price_fields == ("open",)
    assert found.max_price_ticks == pytest.approx(475.0)
    assert found.max_price_bp == pytest.approx(6.759, abs=1e-3)


def test_tick_ruler_orders_differently_than_bp() -> None:
    """🚨 자의 근거 — **어떤 bp 문턱도 이 두 실측 봉을 옳게 가를 수 없다.**

    SOL 2026-07-18은 **1.33bp인데 1틱**(호가 잔돈)이고 BTC 2026-07-16은 **0.23bp인데 15틱**
    (손상)이다. 즉 bp로는 노이즈가 손상보다 **5.7배 크다** — 절대 bp를 자로 삼으면 둘 중
    하나는 반드시 반대로 찍힌다. 그래서 판정자가 틱 배수다.
    """
    noise = _classify_real(_REAL_NOISE_SOL)
    damage = _classify_real(_REAL_DAMAGE_BTC_CLOSE)

    assert noise.max_price_ticks == pytest.approx(1.0)
    assert damage.max_price_ticks == pytest.approx(15.0)
    # bp 축은 순서가 **뒤집혀 있다** — 이것이 절대 bp 문턱을 못 쓰는 이유다.
    assert noise.max_price_bp > damage.max_price_bp

    assert noise.kind == "price_noise" and not noise.damaged
    assert damage.kind == "price_only" and damage.damaged
