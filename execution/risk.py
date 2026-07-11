"""리스크 관리·서킷브레이커 (WAN-9).

자금이 오가는 실행 레이어의 안전장치. 포지션 **사이징**은 WAN-26
(`execution.sizing.position_size`)이 담당하고, 여기서는 사이징과 별개로 **진입을
차단**하는 상한을 검사한다:

* 최대 명목가치(레버리지·정책 상한)
* 동시 오픈 포지션 수 상한
* 일일 손실 서킷브레이커 — 하루 누적 실현 손실이 한도를 넘으면 그날 신규 진입 차단

`RiskManager`는 실현 손익을 등록받아 UTC 일자별 누적 손익을 추적한다. 날짜가
바뀌면 카운터가 리셋된다. 순수 인메모리 상태이며 실주문·`live_trading`을 직접
건드리지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class RiskParams(BaseModel):
    """진입 차단(리스크 한도) 파라미터. 사이징(WAN-26)과 별개다."""

    model_config = ConfigDict(frozen=True)

    max_leverage: float = Field(default=1.0, gt=0)
    """총 명목가치 상한 = `equity × max_leverage`. 신규 진입 후 총 명목이 이를
    넘으면 차단."""
    max_notional_fraction: float | None = Field(default=None, gt=0)
    """추가 명목 상한 = `equity × 이 값`. 설정 시 `max_leverage`와 함께 더 작은 쪽이
    실제 상한. None이면 레버리지 상한만 쓴다."""
    max_concurrent_positions: int = Field(default=1, ge=1)
    """동시에 열 수 있는 오픈 포지션 최대 수."""
    daily_loss_limit_fraction: float | None = Field(default=0.05, gt=0)
    """일일 손실 서킷브레이커 한도 = `기준자본 × 이 값`. 하루 누적 실현 손실이 이
    금액 이상이면 그날 신규 진입을 차단. None이면 서킷브레이커 비활성."""

    def notional_cap(self, equity: float) -> float:
        """자본 대비 총 명목가치 상한. 레버리지·정책 중 더 작은 쪽."""
        cap = equity * self.max_leverage
        if self.max_notional_fraction is not None:
            cap = min(cap, equity * self.max_notional_fraction)
        return cap


class RiskDecision(BaseModel):
    """진입 허용 여부와 차단 사유."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str = ""
    """차단 시 사람이 읽을 사유. 허용이면 빈 문자열."""

    @classmethod
    def allow(cls) -> RiskDecision:
        return cls(allowed=True)

    @classmethod
    def block(cls, reason: str) -> RiskDecision:
        return cls(allowed=False, reason=reason)


def _utc_day(now_ms: int) -> str:
    """epoch(ms)를 UTC 일자 문자열(YYYY-MM-DD)로."""
    return datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


class RiskManager:
    """진입 한도 검사 + 일일 손실 서킷브레이커.

    상태:
    * `_day`: 현재 추적 중인 UTC 일자.
    * `_daily_realized`: 그날 누적 실현 손익(견적 통화, 손실은 음수).
    * `_day_baseline_equity`: 그날 서킷브레이커 한도 계산의 기준 자본.
    """

    def __init__(self, params: RiskParams | None = None) -> None:
        self._params = params if params is not None else RiskParams()
        self._day: str | None = None
        self._daily_realized: float = 0.0
        self._day_baseline_equity: float = 0.0

    @property
    def params(self) -> RiskParams:
        return self._params

    @property
    def daily_realized_pnl(self) -> float:
        """현재 UTC 일자의 누적 실현 손익."""
        return self._daily_realized

    def _roll_day(self, now_ms: int, equity: float) -> None:
        """UTC 일자가 바뀌었으면 일일 카운터를 리셋한다."""
        day = _utc_day(now_ms)
        if self._day != day:
            self._day = day
            self._daily_realized = 0.0
            self._day_baseline_equity = max(equity, 0.0)

    def circuit_breaker_tripped(self, now_ms: int, equity: float) -> bool:
        """오늘 일일 손실 한도를 이미 초과했는지."""
        self._roll_day(now_ms, equity)
        limit = self._params.daily_loss_limit_fraction
        if limit is None:
            return False
        loss_cap = self._day_baseline_equity * limit
        # 누적 손실(음수)의 크기가 한도 이상이면 발동.
        return -self._daily_realized >= loss_cap

    def register_realized_pnl(self, pnl: float, *, now_ms: int, equity: float) -> None:
        """청산 실현 손익을 그날 누적치에 반영한다(서킷브레이커용)."""
        self._roll_day(now_ms, equity)
        self._daily_realized += pnl

    def can_enter(
        self,
        *,
        equity: float,
        new_notional: float,
        open_notional: float,
        open_positions: int,
        now_ms: int,
    ) -> RiskDecision:
        """신규 진입 허용 여부를 판정한다(사이징과 별개의 한도 검사).

        Args:
            equity: 현재 계좌 자본.
            new_notional: 신규 진입의 명목가치(진입가 × 수량).
            open_notional: 이미 열려 있는 포지션들의 명목가치 합.
            open_positions: 현재 오픈 포지션 수.
            now_ms: 현재 시각(epoch ms). 일일 카운터 롤오버에 사용.
        """
        self._roll_day(now_ms, equity)

        if equity <= 0.0:
            return RiskDecision.block("자본이 없어 진입 차단")

        if self.circuit_breaker_tripped(now_ms, equity):
            return RiskDecision.block(
                f"일일 손실 서킷브레이커 발동(누적 {self._daily_realized:.2f}) — 신규 진입 차단"
            )

        if open_positions >= self._params.max_concurrent_positions:
            return RiskDecision.block(
                f"동시 오픈 포지션 한도 초과({open_positions}/"
                f"{self._params.max_concurrent_positions})"
            )

        cap = self._params.notional_cap(equity)
        projected = open_notional + new_notional
        if projected > cap:
            return RiskDecision.block(f"명목가치 한도 초과(예상 {projected:.2f} > 상한 {cap:.2f})")

        return RiskDecision.allow()
