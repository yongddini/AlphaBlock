"""시세 데이터 도메인 모델과 공용 헬퍼.

`Candle`은 저장·스트림 전반에서 사용하는 불변 OHLCV 봉 표현이다. 타임프레임
문자열과 밀리초 간 변환 등 순수 함수 헬퍼도 여기 모은다(네트워크 의존 없음 →
단위 테스트 용이).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

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
