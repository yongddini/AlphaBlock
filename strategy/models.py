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


class ConfluenceParams(BaseModel):
    """오더블록 + 지표 컨플루언스(진입·청산) 규칙 파라미터 (WAN-18).

    오더블록 탭(tap) 시그널을 기준 신호로 두고, 세 개의 지표 게이트로 확정한다.
    각 게이트는 개별 on/off 가능하고, 임계값·EMA 배열 길이·필요 컨플루언스 점수를
    조정할 수 있다. 기본값은 사용자 트레이딩뷰 설정과 일치한다
    (`indicators.py` 기본값 참고). 필드는 `config.Settings`에서
    `ALPHABLOCK_CONFLUENCE__*` 환경변수로 덮어쓸 수 있다.

    ## 게이트 (진입)

    * **EMA 추세 배열**(`use_ema_trend`): `ema_lengths`를 짧은→긴 순으로 볼 때
      값이 내림차순(짧은 EMA가 위)이면 정배열=상승 추세, 오름차순이면 역배열=하락
      추세로 본다. 시그널 방향이 추세와 같으면 +1, 반대면 −1, 중립이면 0표를 준다.
    * **RSI 게이트**(`use_rsi_gate`): 롱은 RSI가 `rsi_overbought` 이상이면(과매수)
      −1, 아니면 +1. 숏은 RSI가 `rsi_oversold` 이하이면(과매도) −1, 아니면 +1.
      과열 구간 진입을 걸러내는 게이트다.
    * **VWMA 추세**(`use_vwma_trend`): 롱은 종가가 VWMA 이상이면 +1 아니면 −1,
      숏은 종가가 VWMA 이하이면 +1 아니면 −1.

    활성 게이트 표의 합이 `min_confluence_score`(미지정 시 활성 게이트 수 = 전원
    확정) 이상이면 진입을 **확정**한다. 지표가 아직 워밍업 중(NaN)인 게이트는 0표.

    ## 청산 (지표 이탈)

    진입 후 첫 지표 이탈 봉에서 청산 신호를 낸다. `exit_on_trend_flip`이면 EMA
    추세가 반대로 뒤집힐 때, `exit_on_vwma_cross`이면 종가가 VWMA를 반대로 돌파할
    때. (손절·익절은 백테스트/실행 계층의 `BacktestConfig`가 담당한다.)
    """

    model_config = ConfigDict(frozen=True)

    # --- 게이트 on/off ---
    use_ema_trend: bool = True
    use_rsi_gate: bool = True
    use_vwma_trend: bool = True

    # --- EMA 추세 배열 ---
    ema_lengths: tuple[int, ...] = DEFAULT_CONFLUENCE_EMA_LENGTHS
    ema_min_aligned_pairs: int | None = Field(default=None, ge=1)
    """정/역배열로 인정할 최소 인접쌍 수. None이면 완전 배열(len(ema_lengths)-1)."""

    # --- RSI 게이트 ---
    rsi_length: int = Field(default=14, ge=1)
    rsi_overbought: float = Field(default=70.0, gt=0, lt=100)
    rsi_oversold: float = Field(default=30.0, gt=0, lt=100)

    # --- VWMA 추세 ---
    vwma_length: int = Field(default=100, ge=1)

    # --- 컨플루언스 판정 ---
    source: str = "close"
    min_confluence_score: int | None = Field(default=None, ge=1)
    """진입 확정에 필요한 최소 게이트 표 합. None이면 활성 게이트 전원 확정."""

    # --- 청산(지표 이탈) ---
    exit_on_trend_flip: bool = True
    exit_on_vwma_cross: bool = True

    @model_validator(mode="after")
    def _validate(self) -> ConfluenceParams:
        if len(self.ema_lengths) < 2:
            raise ValueError("ema_lengths에는 최소 2개의 길이가 필요합니다.")
        if any(length < 1 for length in self.ema_lengths):
            raise ValueError("ema_lengths의 모든 길이는 1 이상이어야 합니다.")
        max_pairs = len(self.ema_lengths) - 1
        if self.ema_min_aligned_pairs is not None and self.ema_min_aligned_pairs > max_pairs:
            raise ValueError(f"ema_min_aligned_pairs는 {max_pairs} 이하여야 합니다(인접쌍 수).")
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError("rsi_oversold는 rsi_overbought보다 작아야 합니다.")
        enabled = self.enabled_gate_count
        if (
            self.min_confluence_score is not None
            and enabled
            and self.min_confluence_score > enabled
        ):
            raise ValueError(f"min_confluence_score는 활성 게이트 수({enabled}) 이하여야 합니다.")
        return self

    @property
    def enabled_gate_count(self) -> int:
        """활성화된 진입 게이트 수."""
        return int(self.use_ema_trend) + int(self.use_rsi_gate) + int(self.use_vwma_trend)

    @property
    def required_aligned_pairs(self) -> int:
        """정/역배열로 인정할 인접쌍 수(미지정 시 완전 배열)."""
        return (
            self.ema_min_aligned_pairs
            if self.ema_min_aligned_pairs is not None
            else len(self.ema_lengths) - 1
        )

    @property
    def entry_threshold(self) -> int:
        """진입 확정 임계 점수. 미지정 시 활성 게이트 전원 확정."""
        return (
            self.min_confluence_score
            if self.min_confluence_score is not None
            else self.enabled_gate_count
        )
