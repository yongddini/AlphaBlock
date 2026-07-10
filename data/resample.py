"""OHLCV 리샘플 유틸: 하위 TF 봉을 상위 TF로 무손실 집계한다 (WAN-24).

바이낸스에서 2h를 새로 백필하지 않고, 이미 저장된 1h 봉 두 개를 합쳐 2h를
만든다. 상위 TF의 한 봉은 하위 TF 봉 ``factor``개(1h→2h면 2개)가 **빠짐없이**
모여야만 생성된다.

경계 정의(중요 — 룩어헤드/왜곡 방지):

- 버킷 경계는 에폭(1970-01-01 00:00 UTC) 기준 ``target_ms`` 격자에 정렬된다.
  2h의 경우 UTC 짝수시(00, 02, 04, …) 시작이다. `open_time`이 이미 UTC 밀리초
  격자에 정렬돼 있으므로 ``open_time // target_ms * target_ms``로 버킷을 구한다.
- 한 상위 봉 ``B``는 하위 봉 ``B, B+src_ms, …, B+(factor-1)*src_ms``가 **모두**
  있어야 만들어진다. 하나라도 결측이면(중간 갭 또는 아직 다 모이지 않은 마지막
  구간) 그 상위 봉은 생성하지 않는다 → 미래 데이터가 새지 않는다.
- 집계: ``open`` = 첫 봉 open, ``high`` = max, ``low`` = min,
  ``close`` = 마지막 봉 close, ``volume`` = 합.
- ``closed``: 구성 하위 봉이 **모두** closed일 때만 True. 마지막 하위 봉이 아직
  확정 전(closed=False)이면 상위 봉도 미확정으로 표시된다.

입출력 DataFrame은 `data.storage.OhlcvStore.load()`와 동일한 스키마
(`symbol, timeframe, open_time, open, high, low, close, volume, closed`
+ 파생 `open_datetime`)를 따른다.
"""

from __future__ import annotations

import pandas as pd

from data.models import timeframe_to_ms

# 리샘플 결과 컬럼 순서(파생 open_datetime 포함) — storage.load()와 일치시킨다.
_OUTPUT_COLUMNS = [
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "closed",
]


def _empty_frame() -> pd.DataFrame:
    """리샘플 스키마의 빈 DataFrame(타입 포함)을 만든다."""
    df = pd.DataFrame(columns=_OUTPUT_COLUMNS)
    df["closed"] = df["closed"].astype(bool)
    df["open_datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def resample_ohlcv(
    df: pd.DataFrame,
    source_timeframe: str,
    target_timeframe: str,
) -> pd.DataFrame:
    """하위 TF OHLCV를 상위 TF로 집계한다.

    ``target``은 ``source``의 정수배 TF여야 한다(예: 1h→2h, 1h→4h). 상위 봉은
    구성 하위 봉이 **모두** 존재할 때만 생성되며, 결측/미완 구간은 조용히
    제외된다(모듈 도크스트링의 경계 정의 참조).

    Args:
        df: `OhlcvStore.load()` 스키마의 하위 TF DataFrame. 단일 심볼을 가정하되,
            여러 심볼이 섞여 있어도 심볼별로 독립 집계한다.
        source_timeframe: 입력 봉의 TF(예: ``"1h"``).
        target_timeframe: 출력 봉의 TF(예: ``"2h"``).

    Returns:
        상위 TF DataFrame(`open_time` 오름차순). 결과가 없으면 동일 스키마의 빈
        DataFrame.

    Raises:
        ValueError: ``target``이 ``source``의 정수배가 아니거나 더 짧은 경우.
    """
    src_ms = timeframe_to_ms(source_timeframe)
    tgt_ms = timeframe_to_ms(target_timeframe)
    if tgt_ms <= src_ms or tgt_ms % src_ms != 0:
        raise ValueError(
            f"target({target_timeframe})은 source({source_timeframe})의 정수배 TF여야 "
            f"합니다: {tgt_ms} % {src_ms} = {tgt_ms % src_ms}"
        )
    factor = tgt_ms // src_ms

    if df.empty:
        return _empty_frame()

    work = df.sort_values("open_time").reset_index(drop=True)
    open_time = work["open_time"].astype("int64")
    bucket = (open_time // tgt_ms) * tgt_ms

    rows: list[dict[str, object]] = []
    for (symbol, bucket_start), group in work.groupby([work["symbol"], bucket], sort=True):
        # 이 상위 봉을 구성해야 할 하위 봉들의 open_time(빠짐없이 이 순서여야 한다).
        expected = [int(bucket_start) + i * src_ms for i in range(factor)]
        got = [int(t) for t in group["open_time"]]
        if got != expected:
            # 결측·중복·미완(아직 factor개가 안 모임) → 상위 봉 생성 안 함.
            continue
        rows.append(
            {
                "symbol": symbol,
                "timeframe": target_timeframe,
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

    out = pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)
    out = out.sort_values("open_time").reset_index(drop=True)
    out["closed"] = out["closed"].astype(bool)
    out["open_datetime"] = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    return out
