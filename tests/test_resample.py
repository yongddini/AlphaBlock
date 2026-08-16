"""data.resample / OhlcvStore 2h 리샘플 테스트 (WAN-24)."""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd
import pytest

from data.models import Candle
from data.resample import resample_ohlcv
from data.storage import OhlcvStore

_SYMBOL = "BTC/USDT:USDT"
_HOUR = 3_600_000  # 1h in ms
_2H = 7_200_000

# 짝수시(UTC 00:00) 경계 정렬 확인용 기준 시각. 0ms == 1970-01-01 00:00 UTC.
_H00 = 0
_H01 = _HOUR
_H02 = 2 * _HOUR
_H03 = 3 * _HOUR
_H04 = 4 * _HOUR
_H05 = 5 * _HOUR


def _c(
    open_time: int,
    o: float,
    h: float,
    low: float,
    c: float,
    v: float,
    *,
    closed: bool = True,
) -> Candle:
    return Candle(_SYMBOL, "1h", open_time, o, h, low, c, v, closed)


def _df(candles: list[Candle]) -> pd.DataFrame:
    """load() 스키마와 같은 1h DataFrame을 만든다."""
    rows = [
        {
            "symbol": c.symbol,
            "timeframe": c.timeframe,
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "closed": c.closed,
        }
        for c in candles
    ]
    cols = ["symbol", "timeframe", "open_time", "open", "high", "low", "close", "volume", "closed"]
    df = pd.DataFrame(rows, columns=cols)
    df["open_datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


def test_aggregation_matches_ohlc_rules() -> None:
    """O=첫봉 open, H=max, L=min, C=마지막봉 close, V=합."""
    df = _df(
        [
            _c(_H00, 10.0, 15.0, 8.0, 12.0, 100.0),
            _c(_H01, 12.0, 18.0, 11.0, 17.0, 150.0),
        ]
    )
    out = resample_ohlcv(df, "1h", "2h")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open_time"] == _H00  # 짝수시 경계
    assert row["timeframe"] == "2h"
    assert row["open"] == 10.0
    assert row["high"] == 18.0
    assert row["low"] == 8.0
    assert row["close"] == 17.0
    assert row["volume"] == 250.0
    assert bool(row["closed"]) is True
    assert "open_datetime" in out.columns


def test_boundary_aligns_to_even_hours() -> None:
    """01:00에서 시작(홀수시)해도 첫 완성 버킷은 02:00부터다.

    01:00 봉은 [00:00,02:00) 버킷에 속하지만 00:00 봉이 없어 그 버킷은 미완 →
    생성 안 됨. 02:00·03:00 봉이 있어야 [02:00,04:00) 버킷이 만들어진다.
    """
    df = _df(
        [
            _c(_H01, 1.0, 2.0, 1.0, 2.0, 10.0),  # [00:00,02:00) 버킷의 두 번째 봉뿐
            _c(_H02, 2.0, 3.0, 2.0, 3.0, 20.0),
            _c(_H03, 3.0, 4.0, 3.0, 4.0, 30.0),
        ]
    )
    out = resample_ohlcv(df, "1h", "2h")
    assert list(out["open_time"]) == [_H02]


def test_missing_bar_skips_bucket_no_lookahead() -> None:
    """중간 결측 1h 봉이 있으면 해당 2h는 생성하지 않는다(왜곡/누수 방지)."""
    df = _df(
        [
            _c(_H00, 1.0, 2.0, 1.0, 2.0, 10.0),
            _c(_H01, 2.0, 3.0, 2.0, 3.0, 20.0),
            # 02:00 결측 → [02:00,04:00) 버킷 미완
            _c(_H03, 3.0, 4.0, 3.0, 4.0, 30.0),
        ]
    )
    out = resample_ohlcv(df, "1h", "2h")
    assert list(out["open_time"]) == [_H00]


def test_incomplete_final_bucket_excluded() -> None:
    """마지막 2h를 채울 두 번째 1h가 아직 없으면 그 버킷은 제외된다."""
    df = _df(
        [
            _c(_H00, 1.0, 2.0, 1.0, 2.0, 10.0),
            _c(_H01, 2.0, 3.0, 2.0, 3.0, 20.0),
            _c(_H02, 3.0, 4.0, 3.0, 4.0, 30.0),  # 03:00 봉 아직 없음
        ]
    )
    out = resample_ohlcv(df, "1h", "2h")
    assert list(out["open_time"]) == [_H00]


def test_unclosed_member_marks_bucket_unclosed() -> None:
    """구성 1h 봉 중 하나라도 미확정이면 2h도 미확정(closed=False)으로 표시."""
    df = _df(
        [
            _c(_H00, 1.0, 2.0, 1.0, 2.0, 10.0, closed=True),
            _c(_H01, 2.0, 3.0, 2.0, 3.0, 20.0, closed=False),
        ]
    )
    out = resample_ohlcv(df, "1h", "2h")
    assert len(out) == 1
    assert bool(out.iloc[0]["closed"]) is False


def test_empty_input_returns_schema() -> None:
    out = resample_ohlcv(pd.DataFrame(), "1h", "2h")
    assert out.empty
    assert "open_time" in out.columns and "close" in out.columns
    assert out["closed"].dtype == bool


def test_non_multiple_timeframe_raises() -> None:
    with pytest.raises(ValueError):
        resample_ohlcv(_df([]), "4h", "2h")  # target < source
    with pytest.raises(ValueError):
        resample_ohlcv(_df([]), "1h", "1h")  # 같은 TF


def test_store_load_2h_resamples_from_1h(store: OhlcvStore) -> None:
    """store.load('2h')가 저장된 1h에서 리샘플해 다른 TF와 동일 인터페이스로 반환."""
    store.upsert_candles(
        [
            _c(_H00, 10.0, 15.0, 8.0, 12.0, 100.0),
            _c(_H01, 12.0, 18.0, 11.0, 17.0, 150.0),
            _c(_H02, 17.0, 20.0, 16.0, 19.0, 200.0),
            _c(_H03, 19.0, 22.0, 18.0, 21.0, 250.0),
        ]
    )
    out = store.load(_SYMBOL, "2h")
    assert list(out["open_time"]) == [_H00, _H02]
    first = out.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (
        10.0,
        18.0,
        8.0,
        17.0,
        250.0,
    )
    # 다른 TF와 동일한 컬럼 스키마여야 한다.
    expected_cols = {
        "symbol",
        "timeframe",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "closed",
        "open_datetime",
    }
    assert expected_cols.issubset(out.columns)
    assert (out["timeframe"] == "2h").all()


def test_store_load_2h_window_filter(store: OhlcvStore) -> None:
    """start/end 창이 2h open_time 기준으로 [start, end) 적용되며 경계 버킷이 온전."""
    store.upsert_candles(
        [
            _c(_H00 + i * _HOUR, float(i), float(i) + 1, float(i), float(i) + 0.5, 10.0)
            for i in range(6)
        ]
    )
    # 전체 2h 버킷: 00:00, 02:00, 04:00
    full = store.load(_SYMBOL, "2h")
    assert list(full["open_time"]) == [_H00, _H02, _H04]

    windowed = store.load(_SYMBOL, "2h", start_ms=_H02, end_ms=_H04)
    assert list(windowed["open_time"]) == [_H02]  # end 배타적


def test_store_load_2h_empty_returns_schema(store: OhlcvStore) -> None:
    out = store.load(_SYMBOL, "2h")
    assert out.empty
    assert "open_time" in out.columns and "close" in out.columns


# -- factor=2 벡터화 빠른 경로 (WAN-313) --------------------------------------


def _reference_loop_resample(df: pd.DataFrame, source_tf: str, target_tf: str) -> pd.DataFrame:
    """버킷당 파이썬 루프 기준 구현(WAN-313 이전 resample_ohlcv 본문) — 빠른 경로의 자."""
    from data.models import timeframe_to_ms
    from data.resample import _empty_frame

    src_ms = timeframe_to_ms(source_tf)
    tgt_ms = timeframe_to_ms(target_tf)
    factor = tgt_ms // src_ms
    if df.empty:
        return _empty_frame()
    work = df.sort_values("open_time").reset_index(drop=True)
    open_time = work["open_time"].astype("int64")
    bucket = (open_time // tgt_ms) * tgt_ms
    rows: list[dict[str, object]] = []
    for (symbol, bucket_start), group in work.groupby([work["symbol"], bucket], sort=True):
        expected = [int(bucket_start) + i * src_ms for i in range(factor)]
        got = [int(t) for t in group["open_time"]]
        if got != expected:
            continue
        rows.append(
            {
                "symbol": symbol,
                "timeframe": target_tf,
                "open_time": int(bucket_start),
                "open": float(group["open"].iloc[0]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group["close"].iloc[-1]),
                "volume": float(group["volume"].sum()),
                "closed": bool(group["closed"].astype(bool).all()),
            }
        )
    if not rows:
        return _empty_frame()
    cols = ["symbol", "timeframe", "open_time", "open", "high", "low", "close", "volume", "closed"]
    out = pd.DataFrame(rows, columns=cols)
    out = out.sort_values("open_time").reset_index(drop=True)
    out["closed"] = out["closed"].astype(bool)
    out["open_datetime"] = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    return out


def test_factor2_fast_path_matches_loop_reference() -> None:
    """빠른 경로가 루프 기준과 비트 단위로 같다 — 결측·미확정·격자 밖 시각 포함."""
    candles = [
        _c(_H00, 1.0, 2.0, 0.5, 1.5, 10.0),
        _c(_H01, 1.5, 2.5, 1.0, 2.0, 11.0),  # 00~02h 완전·확정
        _c(_H02, 2.0, 3.0, 1.5, 2.5, 12.0),  # 02~04h: 03시 결측 → 생성 안 됨
        _c(_H04, 2.5, 3.5, 2.0, 3.0, 13.0),
        _c(_H05, 3.0, 4.0, 2.5, 3.5, 14.0, closed=False),  # 04~06h 완전·미확정
    ]
    df = _df(candles)
    # 격자 밖 시각(30분 어긋남) — 루프의 `got != expected`가 버리는 행.
    off_grid = _df([_c(6 * _HOUR + 30 * 60_000, 9.0, 9.0, 9.0, 9.0, 9.0)])
    df = pd.concat([df, off_grid], ignore_index=True)

    fast = resample_ohlcv(df, "1h", "2h")
    ref = _reference_loop_resample(df, "1h", "2h")
    pd.testing.assert_frame_equal(fast, ref, check_exact=True)
    assert list(fast["open_time"]) == [_H00, _H04]
    assert list(fast["closed"]) == [True, False]


def test_factor2_fast_path_matches_loop_reference_multi_symbol() -> None:
    """여러 심볼이 섞여도 행 순서·값이 루프 기준과 같다."""
    a = _df([_c(_H00, 1.0, 2.0, 0.5, 1.5, 10.0), _c(_H01, 1.5, 2.5, 1.0, 2.0, 11.0)])
    b = a.copy()
    b["symbol"] = "AAA/USDT:USDT"
    df = pd.concat([a, b], ignore_index=True)
    fast = resample_ohlcv(df, "1h", "2h")
    ref = _reference_loop_resample(df, "1h", "2h")
    pd.testing.assert_frame_equal(fast, ref, check_exact=True)
    assert len(fast) == 2


def test_factor2_fast_path_empty_result_schema() -> None:
    """완전한 버킷이 하나도 없으면 루프 경로와 같은 빈 스키마를 낸다."""
    df = _df([_c(_H01, 1.0, 2.0, 0.5, 1.5, 10.0)])  # 홀수시 하나 — 짝이 없다.
    fast = resample_ohlcv(df, "1h", "2h")
    ref = _reference_loop_resample(df, "1h", "2h")
    pd.testing.assert_frame_equal(fast, ref, check_exact=True)
    assert fast.empty
