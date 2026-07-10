"""시세 데이터 도메인 모델과 공용 헬퍼.

`Candle`은 저장·스트림 전반에서 사용하는 불변 OHLCV 봉 표현이다. 타임프레임
문자열과 밀리초 간 변환 등 순수 함수 헬퍼도 여기 모은다(네트워크 의존 없음 →
단위 테스트 용이).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# 타임프레임 문자열 → 밀리초. 수집 대상(1m~1d) 전체를 포함한다.
_TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


def timeframe_to_ms(timeframe: str) -> int:
    """타임프레임 문자열을 밀리초로 변환한다.

    지원하지 않는 값이면 `ValueError`.
    """
    try:
        return _TIMEFRAME_MS[timeframe]
    except KeyError as exc:  # pragma: no cover - 방어적 분기
        raise ValueError(f"지원하지 않는 타임프레임: {timeframe!r}") from exc


@dataclass(frozen=True, slots=True)
class Candle:
    """단일 OHLCV 봉. `open_time`은 UTC 밀리초 정수."""

    symbol: str
    timeframe: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True

    def as_row(self) -> tuple[str, str, int, float, float, float, float, float, int]:
        """SQLite 저장용 튜플. `closed`는 0/1 정수로 직렬화한다."""
        return (
            self.symbol,
            self.timeframe,
            self.open_time,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            int(self.closed),
        )


def candle_from_ccxt(
    symbol: str,
    timeframe: str,
    row: Sequence[float],
    *,
    closed: bool = True,
) -> Candle:
    """ccxt `fetch_ohlcv` 한 행(`[ts, o, h, l, c, v]`)을 `Candle`로 변환한다.

    백필로 받은 과거 봉은 이미 확정된 봉이므로 기본 `closed=True`.
    """
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        closed=closed,
    )


@dataclass(frozen=True, slots=True)
class FundingRate:
    """단일 펀딩비 관측.

    `funding_time`은 이 펀딩비가 정산되는(또는 될) UTC 밀리초 시각이다.
    `is_predicted=True`는 아직 정산 전인 예측값(프리미엄 인덱스),
    `False`는 이력에서 확인된 확정값이다.
    """

    symbol: str
    funding_time: int
    rate: float
    mark_price: float | None = None
    next_funding_time: int | None = None
    is_predicted: bool = False

    def as_row(self) -> tuple[str, int, float, float | None, int | None, int]:
        """SQLite 저장용 튜플. `is_predicted`는 0/1 정수로 직렬화한다."""
        return (
            self.symbol,
            self.funding_time,
            self.rate,
            self.mark_price,
            self.next_funding_time,
            int(self.is_predicted),
        )


def funding_from_ccxt_current(symbol: str, data: Mapping[str, Any]) -> FundingRate:
    """ccxt `fetch_funding_rate`(프리미엄 인덱스) 응답을 `FundingRate`로 변환한다.

    현재 펀딩비는 다음 정산 시각에 적용될 **예측값**이므로 `is_predicted=True`.
    `fundingTimestamp`(다음 정산 시각)와 `fundingRate`는 필수다.
    """
    funding_ts = data.get("fundingTimestamp")
    rate = data.get("fundingRate")
    if funding_ts is None or rate is None:
        raise ValueError(f"펀딩비 응답에 fundingTimestamp/fundingRate 없음: {symbol}")
    mark = data.get("markPrice")
    next_ts = data.get("nextFundingTimestamp")
    return FundingRate(
        symbol=symbol,
        funding_time=int(funding_ts),
        rate=float(rate),
        mark_price=float(mark) if mark is not None else None,
        next_funding_time=int(next_ts) if next_ts is not None else int(funding_ts),
        is_predicted=True,
    )


def funding_from_ccxt_history(symbol: str, entry: Mapping[str, Any]) -> FundingRate:
    """ccxt `fetch_funding_rate_history` 한 항목을 `FundingRate`로 변환한다.

    이력은 이미 정산된 **확정값**이므로 `is_predicted=False`.
    `timestamp`(정산 시각)와 `fundingRate`는 필수다.
    """
    ts = entry.get("timestamp")
    rate = entry.get("fundingRate")
    if ts is None or rate is None:
        raise ValueError(f"펀딩 이력 항목에 timestamp/fundingRate 없음: {symbol}")
    return FundingRate(
        symbol=symbol,
        funding_time=int(ts),
        rate=float(rate),
        is_predicted=False,
    )
