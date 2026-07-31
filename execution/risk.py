"""리스크 관리·서킷브레이커 (WAN-9).

자금이 오가는 실행 레이어의 안전장치. 포지션 **사이징**은 WAN-26
(`execution.sizing.position_size`)이 담당하고, 여기서는 사이징과 별개로 **진입을
차단**하는 상한을 검사한다:

* 최대 명목가치(레버리지·정책 상한)
* 동시 오픈 포지션 수 상한
* 일일 손실 서킷브레이커 — 하루 누적 실현 손실이 한도를 넘으면 그날 신규 진입 차단

`RiskManager`는 실현 손익을 등록받아 **KST 일자**별 누적 손익을 추적한다(WAN-38 ·
WAN-172 KST 통일). 날짜가 바뀌면 카운터가 리셋된다. 실주문·`live_trading`을 직접
건드리지 않는다.

## 재시작 내구성 — DB 재계산 소스(WAN-38)

인메모리 누적(`_daily_realized`)만으로는 러너가 재시작되면 "오늘의 손실"이 0으로
리셋돼 서킷브레이커가 무력화된다(systemd 자동재시작 환경). 그래서 `realized_pnl_source`
(= `paper_trades`에서 "오늘(KST) 청산된 거래의 실현손익 합"을 조회하는 콜백)를 주입하면
서킷브레이커 판정을 **DB에서 재계산**한다 — 프로세스 메모리가 아니라 원장이 진실의
원천이라 재시작 후에도 차단 상태가 유지된다. 소스를 안 주면(백테스트·단위 테스트) 예전과
같은 순수 인메모리 동작이다(`execution`은 `paper` 레이어를 모르므로 소스는 호출부가 주입).
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from common.timefmt import kst_day_bounds, kst_day_key

#: `(start_ms, end_ms)`(배타적 상한) 창에서 청산된 거래의 실현손익 합을 돌려주는 조회.
RealizedPnlSource = Callable[[int, int], float]


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


class CircuitBreakerStatus(BaseModel):
    """일일 손실 서킷브레이커의 현재 상태(대시보드·알림 표시용)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool
    """서킷브레이커가 켜져 있는지(`daily_loss_limit_fraction`이 설정됐는지)."""
    tripped: bool
    """오늘(KST) 손실 한도를 이미 초과해 신규 진입이 차단되는지."""
    daily_realized_pnl: float
    """오늘(KST) 누적 실현 손익(손실은 음수)."""
    loss_limit: float
    """오늘 차단이 걸리는 손실 금액 = `기준자본 × fraction`. 비활성이면 0."""
    baseline_equity: float
    """한도 계산의 기준 자본(오늘 시작 시점 추정)."""


class RiskManager:
    """진입 한도 검사 + 일일 손실 서킷브레이커.

    상태:
    * `_day`: 현재 추적 중인 KST 일자.
    * `_daily_realized`: 그날 누적 실현 손익(견적 통화, 손실은 음수).
    * `_day_baseline_equity`: 그날 서킷브레이커 한도 계산의 기준 자본.

    `realized_pnl_source`를 주면 위 두 값을 매 판정마다 **DB에서 재계산**한다(재시작
    내구성, 모듈 독스트링 참고). 안 주면 순수 인메모리 누적이다.
    """

    def __init__(
        self,
        params: RiskParams | None = None,
        *,
        realized_pnl_source: RealizedPnlSource | None = None,
    ) -> None:
        self._params = params if params is not None else RiskParams()
        self._realized_source = realized_pnl_source
        self._day: str | None = None
        self._daily_realized: float = 0.0
        self._day_baseline_equity: float = 0.0

    @property
    def params(self) -> RiskParams:
        return self._params

    def bind_realized_pnl_source(self, source: RealizedPnlSource | None) -> None:
        """서킷브레이커 판정을 DB 재계산으로 전환한다(러너 배선용, WAN-38).

        엔진이 `RiskManager`를 먼저 만든 뒤(사이징·한도) 저장소가 준비되면 호출부가
        `paper_trades` 조회 콜백을 여기서 물린다. `None`이면 인메모리로 되돌린다.
        """
        self._realized_source = source

    @property
    def daily_realized_pnl(self) -> float:
        """현재 KST 일자의 누적 실현 손익.

        DB 소스가 물려 있으면 마지막 판정에서 재계산된 값을 돌려준다(판정 훅이
        `_daily_realized`를 DB 값으로 덮어쓴다). 소스가 없으면 인메모리 누적치.
        """
        return self._daily_realized

    def _roll_day(self, now_ms: int, equity: float) -> None:
        """KST 일자가 바뀌었으면 일일 카운터를 리셋한다."""
        day = kst_day_key(now_ms)
        if self._day != day:
            self._day = day
            self._daily_realized = 0.0
            self._day_baseline_equity = max(equity, 0.0)

    def _sync_from_source(self, now_ms: int, equity: float) -> None:
        """DB 소스가 물려 있으면 오늘(KST) 실현손익·기준자본을 원장에서 재계산한다.

        기준자본은 원장에 따로 없으므로 `현재자본 − 오늘실현손익`으로 복원한다 —
        현재 자본이 이미 오늘 손익을 반영하고 있으니 그 차가 곧 오늘 시작 자본이다.
        재시작해도 원장만 있으면 같은 값이 나온다.
        """
        if self._realized_source is None:
            return
        start_ms, end_ms = kst_day_bounds(now_ms)
        realized = self._realized_source(start_ms, end_ms)
        self._daily_realized = realized
        self._day_baseline_equity = max(equity - realized, 0.0)

    def circuit_breaker_tripped(self, now_ms: int, equity: float) -> bool:
        """오늘 일일 손실 한도를 이미 초과했는지."""
        self._roll_day(now_ms, equity)
        self._sync_from_source(now_ms, equity)
        limit = self._params.daily_loss_limit_fraction
        if limit is None:
            return False
        loss_cap = self._day_baseline_equity * limit
        # 누적 손실(음수)의 크기가 한도 이상이면 발동.
        return -self._daily_realized >= loss_cap

    def status(self, now_ms: int, equity: float) -> CircuitBreakerStatus:
        """현재 서킷브레이커 상태 스냅샷(대시보드·알림 표시용).

        `circuit_breaker_tripped`과 **같은 경로**로 판정하므로 표시와 실제 차단이
        어긋나지 않는다.
        """
        tripped = self.circuit_breaker_tripped(now_ms, equity)
        limit = self._params.daily_loss_limit_fraction
        loss_limit = self._day_baseline_equity * limit if limit is not None else 0.0
        return CircuitBreakerStatus(
            enabled=limit is not None,
            tripped=tripped,
            daily_realized_pnl=self._daily_realized,
            loss_limit=loss_limit,
            baseline_equity=self._day_baseline_equity,
        )

    def register_realized_pnl(self, pnl: float, *, now_ms: int, equity: float) -> None:
        """청산 실현 손익을 그날 누적치에 반영한다(서킷브레이커용).

        DB 소스가 물려 있어도 프로세스 내 즉시 반영을 위해 인메모리 누적을 유지한다 —
        다음 판정에서 `_sync_from_source`가 DB 값으로 덮어쓰므로 이중 계산은 없다.
        """
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
