"""오더블록 탐지·시그널 출력 모델과 파라미터.

Fluxchart "Volumized Order Blocks" (`strategy/reference/`) 로직에 대응하는
불변 값 객체들이다. 파라미터는 원본 인디케이터 입력값과 1:1로 대응한다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ZONE_COUNT_LIMITS: dict[str, int] = {"high": 10, "medium": 5, "low": 3, "one": 1}

#: 컨플루언스 EMA 배열의 기본 세트(사용자 트레이딩뷰 설정, `indicators.DEFAULT_EMA_LENGTHS`와 동일).
DEFAULT_CONFLUENCE_EMA_LENGTHS: tuple[int, ...] = (20, 60, 120, 240, 365)


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


class SignalExitReason(StrEnum):
    """전략이 계획한 청산의 사유 (WAN-23)."""

    TAKE_PROFIT = "take_profit"
    """진입가 너머 가장 가까운 EMA/VWMA 선에 도달(전량 익절)."""
    STOP_LOSS = "stop_loss"
    """진입 근거 오더블록이 breaker로 무효화(손절)."""


class PlannedExit(BaseModel):
    """전략이 진입 시점에 계획한 명시적 청산 이벤트 (WAN-23).

    익절(선 도달)·손절(오더블록 무효화)이 모두 봉마다 달라지는 **동적** 규칙이라,
    전략이 진입가를 기준으로 청산 봉·참조가·사유를 미리 산출해 시그널에 실어
    보내면 백테스트(`backtest.run_backtest`)가 이를 그대로 소비한다.
    """

    model_config = ConfigDict(frozen=True)

    time: int
    """청산이 발생하는 봉의 `open_time`(ms)."""
    price: float
    """청산 참조가. 익절=도달한 선 가격, 손절=무효화 봉 종가."""
    reason: SignalExitReason


class OrderBlockSignal(BaseModel):
    """오더블록 기반 진입 후보 시그널 (AlphaBlock 확장, 원본에는 없음)."""

    model_config = ConfigDict(frozen=True)

    direction: OrderBlockDirection
    trigger_time: int
    """가격이 오더블록 존에 재진입(tap)한 봉의 `open_time`(ms)."""
    price: float
    order_block: OrderBlock
    status: Literal["active", "cancelled"] = "active"
    planned_exit: PlannedExit | None = None
    """컨플루언스 전략이 계획한 청산(WAN-23). 없으면 백테스트의 고정 %TP/SL 경로를 따른다."""


class OrderBlockResult(BaseModel):
    """`OrderBlockDetector.run()`의 반환값."""

    model_config = ConfigDict(frozen=True)

    order_blocks: list[OrderBlock]
    signals: list[OrderBlockSignal]


class ConfluenceParams(BaseModel):
    """오더블록 + RSI 진입 / EMA·VWMA 선 익절 / 오더블록 무효화 손절 규칙 (WAN-23).

    사용자의 실제 매매 방식에 맞춘 규칙이다. **EMA·VWMA는 진입 판정에 쓰지 않고**
    오직 익절 목표선으로만 쓴다. 기본값은 사용자 트레이딩뷰 설정과 일치한다
    (`indicators.py` 기본값 참고). 필드는 `config.Settings`에서
    `ALPHABLOCK_CONFLUENCE__*` 환경변수로 덮어쓸 수 있다.

    ## 진입 (오더블록 탭 + RSI, 필수 조건)

    기준 신호는 활성(비-breaker) 오더블록 탭(tap). 탭 봉의 RSI가

    * **롱**(강세 오더블록): RSI ≤ `rsi_oversold`(과매도)면 진입.
    * **숏**(약세 오더블록): RSI ≥ `rsi_overbought`(과매수)면 진입.

    RSI가 워밍업 중(NaN)이면 진입하지 않는다. EMA·VWMA는 진입에 영향이 없다.

    ## 익절 (EMA/VWMA 중 진입가 너머 가장 가까운 선 도달)

    대상 선은 `tp_ema_lengths` EMA들과 `tp_vwma_length` VWMA. 진입 이후 매 봉,
    진입가 **너머**(롱은 위·숏은 아래)에 있는 대상 선들 중 **가장 가까운 선**에
    고가(롱)/저가(숏)가 도달하면 그 선 가격에서 **전량 익절**한다. 선은 봉마다
    움직이므로 매 봉 재평가한다. 진입가 너머에 대상 선이 하나도 없으면 익절 목표가
    없어 손절/청산에만 의존한다.

    ## 손절 (오더블록 무효화)

    진입 근거였던 오더블록이 breaker로 무효화되면(존 반대편 돌파 — 무효화 기준
    Wick/Close는 `OrderBlockParams.zone_invalidation`을 재사용) 그 봉에서 손절한다.

    ## 우선순위

    같은 봉에서 손절·익절이 동시에 충족되면 `stop_before_take_profit`(기본 True)이면
    **손절을 우선**한다(보수적). 한 오더블록당 진입은 첫 탭 1회로 제한된다.
    """

    model_config = ConfigDict(frozen=True)

    # --- 진입: RSI (필수 조건) ---
    rsi_length: int = Field(default=14, ge=1)
    rsi_overbought: float = Field(default=70.0, gt=0, lt=100)
    rsi_oversold: float = Field(default=30.0, gt=0, lt=100)

    # --- 익절: EMA/VWMA 목표선 ---
    use_line_take_profit: bool = True
    """익절(선 도달) 규칙 on/off. False면 손절/강제청산에만 의존."""
    tp_ema_lengths: tuple[int, ...] = DEFAULT_CONFLUENCE_EMA_LENGTHS
    """익절 목표로 쓸 EMA 길이들. 비우면 EMA 목표선 없음."""
    tp_vwma_length: int | None = Field(default=100, ge=1)
    """익절 목표로 쓸 VWMA 길이. None이면 VWMA 목표선 없음."""

    # --- 손절: 오더블록 무효화 ---
    use_order_block_stop: bool = True
    """손절(오더블록 무효화) 규칙 on/off."""

    # --- 우선순위 ---
    stop_before_take_profit: bool = True
    """동일 봉에서 손절·익절 동시 충족 시 손절 우선(보수적)."""

    source: str = "close"

    @model_validator(mode="after")
    def _validate(self) -> ConfluenceParams:
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError("rsi_oversold는 rsi_overbought보다 작아야 합니다.")
        if any(length < 1 for length in self.tp_ema_lengths):
            raise ValueError("tp_ema_lengths의 모든 길이는 1 이상이어야 합니다.")
        if len(set(self.tp_ema_lengths)) != len(self.tp_ema_lengths):
            raise ValueError("tp_ema_lengths에 중복된 길이가 있습니다.")
        if self.use_line_take_profit and not self.tp_ema_lengths and self.tp_vwma_length is None:
            raise ValueError(
                "use_line_take_profit=True면 tp_ema_lengths 또는 tp_vwma_length 중 "
                "최소 하나의 익절 목표선이 필요합니다."
            )
        return self

    @property
    def tp_vwma_key(self) -> str | None:
        """익절 VWMA 선의 스냅샷 키(`vwma_<길이>`). 미사용이면 None."""
        return None if self.tp_vwma_length is None else f"vwma_{self.tp_vwma_length}"

    @property
    def sorted_tp_ema_lengths(self) -> list[int]:
        """익절 EMA 길이들을 오름차순 정렬한 리스트."""
        return sorted(self.tp_ema_lengths)
