"""기술지표(RSI · EMA · VWMA) 계산.

사용자가 매매에 쓰는 지표를 오더블록(`strategy/order_blocks.py`)과 동일한
데이터 계약으로 재사용 가능한 **순수 함수**로 구현한다. 수치는 TradingView
Pine `ta.rsi` / `ta.ema` / `ta.vwma`의 정의와 **패리티**를 맞춘다.

## 데이터 계약

입력 DataFrame은 `data.storage.OhlcvStore.load()` / 오더블록 모듈과 같은 스키마
(`open_time`(ms), `open`, `high`, `low`, `close`, `volume`, 선택적 `closed`)를
따른다. `closed` 컬럼이 있으면 확정봉(`closed=True`)만 사용하고, `open_time`이
있으면 시간 오름차순으로 정렬한 뒤 계산한다(오더블록 모듈 `_prepare`와 동일 규칙).
반환 시리즈/프레임의 인덱스는 이렇게 정렬·필터된 프레임의 위치 인덱스(0..n-1)다.

## 파라미터 (기본값 = 사용자 실제 트레이딩뷰 설정)

* RSI: length **14**, source=`close`. Wilder RMA 스무딩(`ta.rma`)과 동일.
* EMA: length **20 / 60 / 120 / 240 / 365** (`emas` 헬퍼로 한 번에), source=`close`.
* VWMA: length **100**, source=`close`. 세션 VWAP이 아니라 롤링 VWMA.
  `strategy/reference/tradingview_vwma.pine` 참조.

length·source는 모두 인자로 조절 가능하며, 기본값만 위 사용자 설정을 따른다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import pandas as pd

#: EMA 다중선 기본 세트(사용자 트레이딩뷰 설정).
DEFAULT_EMA_LENGTHS: tuple[int, ...] = (20, 60, 120, 240, 365)


def _require_positive_length(length: int) -> None:
    if length < 1:
        raise ValueError(f"length는 1 이상이어야 합니다: {length}")


def _prepare(df: pd.DataFrame, required: Sequence[str]) -> pd.DataFrame:
    """오더블록 모듈과 동일한 입력 준비: 컬럼 검증 → 확정봉 필터 → 시간 정렬."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV DataFrame에 필요한 컬럼이 없습니다: {missing}")
    frame = df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    if "open_time" in frame.columns:
        frame = frame.sort_values("open_time")
    return frame.reset_index(drop=True)


def _wilder_rma(values: Sequence[float], length: int) -> list[float]:
    """Wilder's RMA (`ta.rma`).

    선행 `NaN`(예: `diff()`로 생기는 첫 값)은 건너뛰고, 최초로 모인 유효값
    `length`개의 단순평균(SMA)을 시드로 삼아 그 위치에 기록한 뒤 재귀
    스무딩한다. 미확정 구간은 `NaN`으로 채운다. TradingView `ta.rma`가
    `na(rma[1]) ? sma(src, length) : (src + (length-1)*rma[1]) / length`로
    동작하는 것과 동일하다.
    """
    out: list[float] = [math.nan] * len(values)
    prev: float | None = None
    seed_sum = 0.0
    seed_count = 0
    for i, value in enumerate(values):
        if math.isnan(value):
            continue
        if prev is None:
            seed_sum += value
            seed_count += 1
            if seed_count == length:
                prev = seed_sum / length
                out[i] = prev
        else:
            prev = (prev * (length - 1) + value) / length
            out[i] = prev
    return out


def rsi(df: pd.DataFrame, length: int = 14, source: str = "close") -> pd.Series:
    """RSI (`ta.rsi`). 기본 length=14, source=close.

    상승분/하락분을 Wilder RMA로 스무딩해 계산한다. 최초 유효값은 인덱스
    `length`에서 나타나고 그 이전은 `NaN`이다.
    """
    _require_positive_length(length)
    frame = _prepare(df, (source,))
    src = frame[source].astype(float)

    change = src.diff()
    gain = change.clip(lower=0.0)
    loss = (-change).clip(lower=0.0)

    avg_gain = pd.Series(_wilder_rma(gain.tolist(), length), index=frame.index, dtype="float64")
    avg_loss = pd.Series(_wilder_rma(loss.tolist(), length), index=frame.index, dtype="float64")

    rs = avg_gain / avg_loss
    result = 100.0 - 100.0 / (1.0 + rs)
    result.name = f"rsi_{length}"
    return result


def ema(df: pd.DataFrame, length: int, source: str = "close") -> pd.Series:
    """EMA (`ta.ema`).

    `alpha = 2 / (length + 1)`의 재귀 지수이동평균으로, 첫 봉의 source 값을
    시드로 삼는다(pandas `ewm(span=length, adjust=False)`와 동일하며 이는
    Pine `ta.ema`의 시드 규칙과 일치한다).
    """
    _require_positive_length(length)
    frame = _prepare(df, (source,))
    src = frame[source].astype(float)
    result = src.ewm(span=length, adjust=False).mean()
    result.name = f"ema_{length}"
    return result


def emas(
    df: pd.DataFrame,
    lengths: Iterable[int] = DEFAULT_EMA_LENGTHS,
    source: str = "close",
) -> pd.DataFrame:
    """여러 length의 EMA를 한 번에 계산해 DataFrame으로 반환.

    컬럼명은 `ema_{length}`. 기본 세트는 사용자 트레이딩뷰 설정
    `(20, 60, 120, 240, 365)`. 입력을 한 번만 준비하므로 개별 `ema()` 반복
    호출보다 효율적이며 인덱스가 서로 정렬된다.
    """
    length_list = list(lengths)
    for length in length_list:
        _require_positive_length(length)
    frame = _prepare(df, (source,))
    src = frame[source].astype(float)
    columns = {f"ema_{length}": src.ewm(span=length, adjust=False).mean() for length in length_list}
    return pd.DataFrame(columns, index=frame.index)


def vwma(df: pd.DataFrame, length: int = 100, source: str = "close") -> pd.Series:
    """VWMA (`ta.vwma`). 기본 length=100, source=close.

    롤링 거래가중평균 `sum(src*volume, length) / sum(volume, length)`. 세션
    VWAP이 아니라 최근 `length`봉 롤링 이동평균이다. 최초 유효값은 인덱스
    `length-1`에서 나타나고 그 이전은 `NaN`이다.
    """
    _require_positive_length(length)
    frame = _prepare(df, (source, "volume"))
    src = frame[source].astype(float)
    volume = frame["volume"].astype(float)

    weighted = (src * volume).rolling(window=length).sum()
    total = volume.rolling(window=length).sum()
    result = weighted / total
    result.name = f"vwma_{length}"
    return result
