"""오더블록 탐지·시그널 출력 모델과 파라미터.

Fluxchart "Volumized Order Blocks" (`strategy/reference/`) 로직에 대응하는
불변 값 객체들이다. 파라미터는 원본 인디케이터 입력값과 1:1로 대응한다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_ZONE_COUNT_LIMITS: dict[str, int] = {"high": 10, "medium": 5, "low": 3, "one": 1}


class OrderBlockDirection(StrEnum):
    """오더블록 방향. 원본의 `obType` ("Bull"/"Bear")에 대응."""

    BULLISH = "bull"
    BEARISH = "bear"


class OrderBlockParams(BaseModel):
    """오더블록 탐지 파라미터. 원본 인디케이터 설정값과 대응한다."""

    model_config = ConfigDict(frozen=True)

    swing_length: int = Field(default=10, ge=3)
    zone_invalidation: Literal["wick", "close"] = "wick"
    zone_count: Literal["high", "medium", "low", "one"] = "low"
    combine_obs: bool = True
    max_atr_mult: float = Field(default=3.5, gt=0)
    atr_length: int = Field(default=10, ge=1)
    max_order_blocks: int = Field(default=30, ge=1)
    max_distance_to_last_bar: int = Field(default=1750, ge=1)

    @property
    def zone_limit(self) -> int:
        """`zone_count` 문자열을 방향별 렌더/채택 개수로 변환."""
        return _ZONE_COUNT_LIMITS[self.zone_count]


class OrderBlock(BaseModel):
    """탐지된 오더블록 하나. 원본 `orderBlockInfo`에 대응."""

    model_config = ConfigDict(frozen=True)

    direction: OrderBlockDirection
    top: float
    bottom: float
    start_time: int
    """존이 시작되는(박스 왼쪽 변) 봉의 `open_time`(ms). 원본 `startTime`."""
    confirmed_time: int
    """오더블록이 실제로 확정(탐지)된 봉의 `open_time`(ms)."""
    ob_volume: float
    ob_low_volume: float
    ob_high_volume: float
    breaker: bool = False
    break_time: int | None = None
    combined: bool = False
    """`combine_obs`로 다른 존과 병합되어 생성된 존인지 여부."""


class OrderBlockSignal(BaseModel):
    """오더블록 기반 진입 후보 시그널 (AlphaBlock 확장, 원본에는 없음)."""

    model_config = ConfigDict(frozen=True)

    direction: OrderBlockDirection
    trigger_time: int
    """가격이 오더블록 존에 재진입(tap)한 봉의 `open_time`(ms)."""
    price: float
    order_block: OrderBlock
    status: Literal["active", "cancelled"] = "active"


class OrderBlockResult(BaseModel):
    """`OrderBlockDetector.run()`의 반환값."""

    model_config = ConfigDict(frozen=True)

    order_blocks: list[OrderBlock]
    signals: list[OrderBlockSignal]
