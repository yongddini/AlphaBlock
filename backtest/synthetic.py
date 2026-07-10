"""재현 가능한 합성 OHLCV 생성기 (WAN-19).

저장된 실데이터(`data/ohlcv.db`, WAN-6)가 없어도 컨플루언스 전략 백테스트와
파라미터 스윕을 **결정적으로** 실행·검증할 수 있도록, 시드로 고정된 합성 OHLCV를
만든다. 완만한 추세 위에 주기적 스윙(사인)과 임펄스(대량 거래) 봉을 얹어 오더블록·
탭이 형성될 구조를 만든다. 실데이터의 대용이 아니라, 파이프라인 재현·CI 스모크·
데모용이다.

컨플루언스 전략은 (오더블록 탐지 + 3게이트 합의로) 매우 선별적이라 임의 합성
데이터에서 확정 진입이 드물 수 있다. 이 모듈의 목적은 **결정적이고 유효한 OHLCV**를
제공해 파이프라인을 재현 가능하게 돌리는 것이지, 특정 거래 수를 보장하는 것이 아니다.
"""

from __future__ import annotations

import math
import random

import pandas as pd

from backtest.sweep import timeframe_to_ms


def make_synthetic_ohlcv(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    bars: int = 1500,
    start_price: float = 20_000.0,
    seed: int = 7,
    start_time_ms: int = 0,
    drift: float = 0.0003,
    swing_amplitude: float = 0.07,
    swing_period: int = 64,
    noise: float = 0.006,
    impulse_period: int = 12,
) -> pd.DataFrame:
    """시드로 고정된 합성 OHLCV DataFrame을 생성한다.

    가격 경로는 완만한 선형 추세(`drift`)에 진폭 `swing_amplitude`·주기 `swing_period`
    봉의 사인 스윙을 곱하고 가우시안 노이즈(`noise`)를 더해 만든다. `impulse_period`
    봉마다 대량 거래의 임펄스 봉을 섞어 오더블록 형성을 유도한다. 컬럼은 실 저장소와
    동일한 스키마(`open_time`, `open`, `high`, `low`, `close`, `volume`, `closed`)를
    따르며, 같은 인자에는 항상 같은 결과를 반환한다(재현성).

    `symbol`은 스키마 밖 메타로, 반환 프레임에는 포함하지 않는다(백테스트 입력
    스키마 유지).
    """
    if bars <= 0:
        raise ValueError("bars는 1 이상이어야 합니다.")
    if swing_period <= 0:
        raise ValueError("swing_period는 1 이상이어야 합니다.")

    rng = random.Random(seed)
    step = timeframe_to_ms(timeframe)

    times: list[int] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []

    price = start_price
    for i in range(bars):
        trend = start_price * (1.0 + drift * i)
        target = trend * (1.0 + swing_amplitude * math.sin(2.0 * math.pi * i / swing_period))
        close_price = max(1e-6, target * (1.0 + rng.gauss(0.0, noise)))
        open_price = price
        impulse = i % impulse_period == 0
        high = max(open_price, close_price) * (1.0 + abs(rng.gauss(0.0, 0.003)))
        low = min(open_price, close_price) * (1.0 - abs(rng.gauss(0.0, 0.003)))
        volume = 100.0 + abs(rng.gauss(0.0, 25.0)) + (500.0 if impulse else 0.0)

        times.append(start_time_ms + i * step)
        opens.append(open_price)
        highs.append(high)
        lows.append(low)
        closes.append(close_price)
        volumes.append(volume)
        price = close_price

    df = pd.DataFrame(
        {
            "open_time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )
    df["closed"] = True
    return df
