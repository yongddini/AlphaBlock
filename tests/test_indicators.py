"""strategy.indicators (RSI · EMA · VWMA) 패리티 테스트.

TradingView 원본 차트 출력을 이 환경에서 직접 대조할 수 없으므로,
`ta.rsi`/`ta.ema`/`ta.vwma`의 **정의**를 진실원으로 삼아 고정 샘플 구간에서
손으로 계산한 기대값과 일치하는지 검증한다(허용 오차 명시). test_order_blocks의
패리티 방침과 동일하다.

손계산 기준 시퀀스(length=3으로 추적 가능하게 축소):

    close  = [10, 11, 10.5, 11.5, 11, 12]
    volume = [ 1,  2,    1,    2,  1,  2]

* EMA(3), alpha=2/(3+1)=0.5, 시드=close[0]:
    [10, 10.5, 10.5, 11.0, 11.0, 11.5]
* RSI(3): 최초 유효값은 인덱스 3.
    idx3=80.0, idx4=61.53846..., idx5=77.27273...
* VWMA(3): 최초 유효값은 인덱스 2.
    idx2=10.625, idx3=11.1, idx4=11.125, idx5=11.6
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from strategy.indicators import DEFAULT_EMA_LENGTHS, ema, emas, rsi, vwma

_CLOSE = [10.0, 11.0, 10.5, 11.5, 11.0, 12.0]
_VOLUME = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0]
_TOL = 1e-6


def _make_df(
    close: list[float] | None = None,
    volume: list[float] | None = None,
) -> pd.DataFrame:
    close = _CLOSE if close is None else close
    volume = _VOLUME if volume is None else volume
    n = len(close)
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(n)],
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "volume": volume,
        }
    )


# --------------------------------------------------------------------------- EMA


def test_ema_matches_hand_computed_recursion() -> None:
    result = ema(_make_df(), length=3)
    expected = [10.0, 10.5, 10.5, 11.0, 11.0, 11.5]
    assert result.name == "ema_3"
    for got, want in zip(result.tolist(), expected, strict=True):
        assert got == pytest.approx(want, abs=_TOL)


def test_ema_matches_independent_alpha_recursion() -> None:
    """길이 14로 독립 재귀 구현과 대조 (ta.ema 정의)."""
    close = [100.0 + math.sin(i) * 5 + i * 0.3 for i in range(120)]
    length = 14
    alpha = 2.0 / (length + 1)
    expected: list[float] = []
    prev = close[0]
    for i, c in enumerate(close):
        prev = c if i == 0 else alpha * c + (1 - alpha) * prev
        expected.append(prev)
    result = ema(_make_df(close=close, volume=[1.0] * len(close)), length=length)
    for got, want in zip(result.tolist(), expected, strict=True):
        assert got == pytest.approx(want, abs=_TOL)


def test_ema_source_parameter() -> None:
    df = _make_df()
    # source=open 은 이 픽스처에서 close와 동일하므로 high로 검증.
    result = ema(df, length=3, source="high")
    expected_first = 11.0  # high[0] = close[0] + 1
    assert result.iloc[0] == pytest.approx(expected_first, abs=_TOL)
    assert result.name == "ema_3"


# --------------------------------------------------------------------------- EMAs


def test_emas_default_lengths_and_columns() -> None:
    close = [100.0 + i for i in range(400)]
    df = _make_df(close=close, volume=[1.0] * len(close))
    frame = emas(df)
    assert list(frame.columns) == [f"ema_{n}" for n in DEFAULT_EMA_LENGTHS]
    # 각 컬럼은 개별 ema() 호출과 일치해야 한다.
    for length in DEFAULT_EMA_LENGTHS:
        single = ema(df, length=length)
        for got, want in zip(frame[f"ema_{length}"].tolist(), single.tolist(), strict=True):
            assert got == pytest.approx(want, abs=_TOL)


def test_emas_custom_lengths() -> None:
    frame = emas(_make_df(), lengths=(2, 4))
    assert list(frame.columns) == ["ema_2", "ema_4"]


# --------------------------------------------------------------------------- RSI


def test_rsi_matches_hand_computed_values() -> None:
    result = rsi(_make_df(), length=3)
    assert result.name == "rsi_3"
    values = result.tolist()
    # 최초 length 개는 미확정(NaN).
    assert all(math.isnan(v) for v in values[:3])
    assert values[3] == pytest.approx(80.0, abs=1e-4)
    assert values[4] == pytest.approx(61.53846, abs=1e-4)
    assert values[5] == pytest.approx(77.27273, abs=1e-4)


def test_rsi_bounds_and_default_length() -> None:
    close = [100.0 + math.sin(i / 3) * 10 for i in range(80)]
    result = rsi(_make_df(close=close, volume=[1.0] * len(close)))  # 기본 length=14
    assert result.name == "rsi_14"
    defined = [v for v in result.tolist() if not math.isnan(v)]
    assert defined  # 최소 하나는 확정
    assert all(0.0 <= v <= 100.0 for v in defined)
    # length=14 → 인덱스 0..13 은 NaN, 14 부터 확정.
    assert math.isnan(result.iloc[13])
    assert not math.isnan(result.iloc[14])


def test_rsi_all_gains_is_100() -> None:
    close = [10.0 + i for i in range(10)]  # 단조 증가 → 하락분 0
    result = rsi(_make_df(close=close, volume=[1.0] * len(close)), length=3)
    defined = [v for v in result.tolist() if not math.isnan(v)]
    assert all(v == pytest.approx(100.0, abs=_TOL) for v in defined)


# --------------------------------------------------------------------------- VWMA


def test_vwma_matches_hand_computed_values() -> None:
    result = vwma(_make_df(), length=3)
    assert result.name == "vwma_3"
    values = result.tolist()
    assert math.isnan(values[0])
    assert math.isnan(values[1])
    assert values[2] == pytest.approx(10.625, abs=_TOL)
    assert values[3] == pytest.approx(11.1, abs=_TOL)
    assert values[4] == pytest.approx(11.125, abs=_TOL)
    assert values[5] == pytest.approx(11.6, abs=_TOL)


def test_vwma_reduces_to_sma_with_constant_volume() -> None:
    close = [10.0, 20.0, 30.0, 40.0, 50.0]
    df = _make_df(close=close, volume=[1.0] * len(close))
    result = vwma(df, length=2)
    # 볼륨이 일정하면 VWMA == SMA.
    expected = pd.Series(close).rolling(2).mean().tolist()
    for got, want in zip(result.tolist(), expected, strict=True):
        if math.isnan(want):
            assert math.isnan(got)
        else:
            assert got == pytest.approx(want, abs=_TOL)


# ------------------------------------------------------------------- 데이터 계약


def test_indicators_respect_closed_filter() -> None:
    df = _make_df()
    # 마지막 봉을 미확정으로 표시하면 지표 계산에서 제외되어야 한다.
    df["closed"] = [True] * (len(df) - 1) + [False]
    result = ema(df, length=3)
    assert len(result) == len(df) - 1


def test_indicators_sort_by_open_time() -> None:
    df = _make_df().iloc[::-1].reset_index(drop=True)  # 시간 역순 입력
    result = ema(df, length=3)
    # 정렬 후 첫 값은 시간상 가장 이른 봉(close=10)이어야 한다.
    assert result.iloc[0] == pytest.approx(10.0, abs=_TOL)


def test_missing_column_raises() -> None:
    df = _make_df().drop(columns=["volume"])
    with pytest.raises(ValueError, match="volume"):
        vwma(df, length=3)


def test_invalid_length_raises() -> None:
    with pytest.raises(ValueError, match="length"):
        ema(_make_df(), length=0)
    with pytest.raises(ValueError, match="length"):
        rsi(_make_df(), length=0)
    with pytest.raises(ValueError, match="length"):
        emas(_make_df(), lengths=(20, 0))


def test_empty_dataframe_returns_empty() -> None:
    df = _make_df(close=[], volume=[])
    assert len(rsi(df)) == 0
    assert len(vwma(df)) == 0
    assert len(emas(df)) == 0
