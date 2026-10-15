"""엔진 C(시계열 모멘텀) 목표 배율 — 백테스트와 페이퍼가 같이 쓰는 **단일 소스** (WAN-451).

WAN-449 정본(`backtest/wan449_tsmom_second_engine.py`)의 「그날 종가에 정한 포지션」 계산을 여기로
옮겼다. 그 모듈은 이제 이 함수를 import한다 — 페이퍼 러너가 같은 식을 다시 적으면 두 성적표가
조용히 갈라진다(WAN-305 「백테는 페이퍼와 같은 선상」).

정본(바꾸지 않는다 — 페이퍼 결과를 보고 규칙을 고치지 않는다, WAN-451):

* 대상: BTC·ETH 무기한, 각 50% 바스켓 · 롱·숏 모두.
* 신호: `mean(sign(P_t / P_{t−L} − 1))`, L ∈ {20, 60, 120, 250}일 → {−1, −0.5, 0, 0.5, 1}.
* 배율: `w = clip(신호 × 0.20 / σ60, −2, 2)` · σ60 = 최근 60개 일 수익의 표본 표준편차 × √365.
* 시점: 일봉 D가 닫히는 D+1 00:00 UTC에 w_D를 정하고 D+1 하루를 보유한다(문턱 없이 매일 전량
  리밸런싱). 이력이 모자라면 NaN → 비중 0.
* 크기: 자산별 명목 = 총자본 × λ × k × 0.5 × w (k = 1.569 · λ = 0.906, WAN-449 표 — 소수 셋째
  자리 반올림값을 이슈가 정본으로 못 박았다). 🔁 2026-10-15 팔의 같은 1분 규약을 가격 순서로 바꾼
  재산출 값이다(WAN-451 · 옛 값 k 1.590 · λ 0.885).

이 모듈은 **오더블록 백테 수치를 하나도 안 움직인다** — 그래서 `backtest.trade_store`의 엔진
목록이 아니라 비-엔진 목록에 있다(넣으면 이 파일을 손볼 때마다 payload·타임라인 캐시가 통째로
무효화된다).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

ASSETS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT")
LOOKBACKS: tuple[int, ...] = (20, 60, 120, 250)
VOL_TARGET = 0.20
VOL_WINDOW = 60
MAX_LEVERAGE = 2.0
DAYS_PER_YEAR = 365.0
COST = 0.0009
"""리밸런싱 비용 |Δw| × 9bp(WAN-449 정본 — 테이커 4bp + 슬리피지 5bp와 같은 값)."""
K = 1.569
"""합성 비율 — 앞구간 `std(팔 일 수익) / std(엔진 C 바스켓 일 수익)`(WAN-449)."""
LAMBDA = 0.906
"""전체 배율 — 합성 MDD 35%(일 저가) 맞춤(WAN-449)."""
ASSET_SHARE = 1.0 / len(ASSETS)
"""바스켓 = 두 자산 일 수익의 평균 → 자산당 0.5."""
MIN_HISTORY = max(LOOKBACKS) + 1
"""가장 긴 룩백(250일)의 비교 대상까지 확정 일봉이 있어야 신호가 정의된다(그 전은 비중 0)."""
DAY_MS = 86_400_000


def tsmom_weights(price: pd.Series, returns: pd.Series) -> pd.Series:
    """그날 종가에 정한 포지션(다음 날 수익에 적용) — 신호 × 변동성 목표 · |배율| ≤ 2."""
    sig = sum(np.sign(price / price.shift(lb) - 1.0) for lb in LOOKBACKS) / len(LOOKBACKS)
    vol = returns.rolling(VOL_WINDOW).std() * math.sqrt(DAYS_PER_YEAR)
    wgt = (sig * VOL_TARGET / vol).clip(-MAX_LEVERAGE, MAX_LEVERAGE)
    return wgt.fillna(0.0)


def notional_fraction(weight: float) -> float:
    """자산 하나의 목표 명목 ÷ 총자본 = λ × k × 0.5 × w."""
    return LAMBDA * K * ASSET_SHARE * weight


@dataclass(frozen=True)
class TsmomTarget:
    """한 자산의 그날 목표 — 일봉 `as_of_ms`(D)가 닫힌 뒤 D+1 00:00 UTC에 정한 값."""

    asset: str
    as_of_ms: int
    """마지막 확정 일봉 D의 `open_time`(UTC epoch ms). 이 값으로 정한 포지션은 D+1 하루를 든다."""
    signal: float
    """{−1, −0.5, 0, 0.5, 1}. 이력이 모자라면 NaN."""
    annual_vol: float
    """σ60 × √365. 이력이 모자라면 NaN."""
    weight: float
    """`clip(신호 × 0.20 / σ60, −2, 2)` — 이력이 모자라면 0."""

    @property
    def notional_fraction(self) -> float:
        """목표 명목 ÷ 총자본(부호 = 방향: 양수 롱 · 음수 숏)."""
        return notional_fraction(self.weight)

    @property
    def effective_from_ms(self) -> int:
        """이 목표가 걸리는 시각 = D+1 00:00 UTC."""
        return self.as_of_ms + DAY_MS


def target_from_daily_closes(asset: str, open_time_ms: pd.Series, close: pd.Series) -> TsmomTarget:
    """확정 일봉(`open_time` 오름차순 · 연속)에서 마지막 날의 목표를 낸다.

    백테스트(`wan449.tsmom_returns`)와 같은 경로로 계산한다: 수익 → `(1 + 수익)` 누적곱을 가격으로
    삼아 `tsmom_weights`. 그래서 같은 일봉을 주면 그 함수의 마지막 값과 비트 단위로 같다.

    🚨 **확정봉만** 줄 것 — 형성 중인 오늘 봉이 섞이면 아직 끝나지 않은 날의 종가로 포지션을
    정하는 룩어헤드다. 날짜가 비면(수집 공백) 「N일 전」이 다른 날을 가리키므로 **거부한다** —
    지어내지 않는다.
    """
    times = pd.Series(open_time_ms, dtype="int64").reset_index(drop=True)
    closes = pd.Series(close, dtype="float64").reset_index(drop=True)
    if len(times) == 0:
        raise ValueError(f"{asset}: 확정 일봉이 없다")
    if len(times) != len(closes):
        raise ValueError(
            f"{asset}: open_time {len(times)}개 · close {len(closes)}개 — 길이가 다르다"
        )
    steps = times.diff().dropna()
    if len(steps) and not (steps == DAY_MS).all():
        bad = times[1:][(steps != DAY_MS).to_numpy()].iloc[0]
        raise ValueError(f"{asset}: 일봉이 연속이 아니다(첫 어긋남 open_time={int(bad)})")
    if closes.isna().any() or (closes <= 0).any():
        raise ValueError(f"{asset}: 종가에 결측·비양수가 있다")

    returns = closes.pct_change()
    price = (1.0 + returns.fillna(0.0)).cumprod()
    weights = tsmom_weights(price, returns)
    sig = sum(np.sign(price / price.shift(lb) - 1.0) for lb in LOOKBACKS) / len(LOOKBACKS)
    vol = returns.rolling(VOL_WINDOW).std() * math.sqrt(DAYS_PER_YEAR)
    return TsmomTarget(
        asset=asset,
        as_of_ms=int(times.iloc[-1]),
        signal=float(sig.iloc[-1]),
        annual_vol=float(vol.iloc[-1]),
        weight=float(weights.iloc[-1]),
    )
