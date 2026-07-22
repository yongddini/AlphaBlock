"""라이브/페이퍼 대기 지정가 주문 상태 머신 (WAN-41 · WAN-45 개정).

백테스트(`backtest.substep.simulate_zone_limit_trade`)가 1분봉 서브스텝으로
시뮬레이션하는 "존-지정가 대기 → 닿으면 체결/취소"를, 라이브·페이퍼에서 **웹소켓
실시간 가격**으로 동일하게 굴리는 상태 머신이다.

## WAN-45 개정 — 오늘 엔진(WAN-123/132)에 맞춘 부품 수정

WAN-41 시절 이 부품에는 2026-07-12 시절 규칙이 박혀 있었다: 닿으면 **롱은
`RSI <= oversold`일 때만** 체결(모든 탭에 극단 게이트), 지정가는 `limit_price` 하나로
고정. 오늘 엔진은 정반대다:

* **RSI 게이트가 없다**(`rsi_gate_mode="unconditional"`, WAN-123) — 닿으면 워밍업
  (RSI None)이어도 즉시 체결한다. 이대로 옛 부품을 배선하면 라이브만 혼자 RSI 조건을
  걸어 주문을 안 채우고, 체결률 실측(이 이슈의 1순위 목적)이 "시장 때문인지 우리 코드
  때문인지" 구분되지 않게 오염된다. 게이트는 `rsi_gate_mode` **옵트인**으로만 돈다
  (첫 탭 면제는 `first_tap_free` — `tap_index`를 아는 호출부의 책임, WAN-100 계약).
* **지정가는 가만히 있지 않는다**(`band_bar="intrabar_live"`, WAN-132) — 볼린저 밴드의
  20번째 표본이 현재가라 봉 안에서 값이 계속 움직인다. 그래서 상수 `limit_price` 대신
  `live_limit`(`backtest.substep.LiveLimitProvider`, 구현은
  `backtest.zone_limit_backtest.IntrabarLiveLimit`)을 받아 **매 틱 지정가를 재산정**한다.
  백테스트와 같은 객체를 쓰므로 밴드 → `deviation_entry_price` → 오프셋 사슬이 두
  경로에서 갈라질 수 없다(로직 이중화 금지 — WAN-100의 교훈).

## 상태 전이

    PENDING ──(닿음 + 게이트 통과[기본: 게이트 없음])──▶ FILLED
        │
        ├─(닿음 + 옵트인 게이트 미충족 + cancel_on_condition_fail)─▶ CANCELLED_CONDITION_FAILED
        ├─(오더블록 무효화)─────────────────────────────────────▶ CANCELLED_INVALIDATED
        └─(limit_valid_bars 경과)──────────────────────────────▶ CANCELLED_EXPIRED

`on_price`는 매 틱(웹소켓 가격) 호출하고, 상위TF 봉이 마감되면 `on_bar_close`로
실시간 RSI·밴드 상태를 굴리며 경과 봉 수를 센다. 무효화는 `cancel_invalidated`로 즉시
반영한다. 한 (symbol, timeframe) 시리즈에는 대기 주문 하나만 둔다(단일 포지션 규칙).

## 체결률 실측 필드 (WAN-45 1급 산출물)

`placed_ms`(예약 시각)·`first_rested_ms`(처음 주문판에 걸린 시각 — live 밴드는 워밍업·
규칙 3 기각 구간엔 주문이 없다)·`LimitFill.penetration_bps`(체결 틱이 지정가를 얼마나
관통했나 — 0 근처면 "스치듯 닿은 체결" = 실거래에서 큐 우선순위 때문에 가장 안 될
가능성이 높은 체결, WAN-96)를 기록해 백테스트 `baseline` 가정과 나란히 놓는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from backtest.substep import LiveLimitProvider
from strategy.models import OrderBlockDirection, RsiGateMode, rsi_gate_passes
from strategy.realtime_rsi import RealtimeRsi

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
SeriesKey = tuple[str, str]


class LimitOrderStatus(StrEnum):
    """대기 지정가 주문의 상태."""

    PENDING = "pending"
    FILLED = "filled"
    CANCELLED_EXPIRED = "cancelled_expired"
    CANCELLED_INVALIDATED = "cancelled_invalidated"
    CANCELLED_CONDITION_FAILED = "cancelled_condition_failed"

    @property
    def is_terminal(self) -> bool:
        return self is not LimitOrderStatus.PENDING


@dataclass(frozen=True)
class LimitFill:
    """지정가 체결 결과."""

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    price: float
    """체결가(체결 순간 주문판에 걸려 있던 지정가)."""
    time: int
    """체결 시각(ms)."""
    rsi: float | None
    """체결 순간의 실시간 RSI. 워밍업(None)이어도 기본 게이트(`unconditional`)는
    체결하므로 값이 없을 수 있다 — 지어내지 않는다."""
    stop_price: float
    """이 거래에 실제로 적용된 손절 참조가. live 밴드는 체결 순간
    `LiveLimitProvider.resolve_exits`가 낸다(WAN-143과 같은 계약)."""
    take_profit_price: float | None
    """익절 목표가(고정 R). live 밴드는 진입가가 체결 순간에야 정해지므로 1R도, 그
    배수 목표도 그때 산출된다. None이면 목표 없음(무효화까지 홀딩)."""
    penetration_bps: float
    """체결 틱이 지정가를 관통한 폭(bp, 항상 >= 0). 0 근처 = "스치듯 닿은 체결"로,
    실거래에서 큐 우선순위 때문에 가장 체결이 안 될 부류다(WAN-96). 백테스트
    `baseline`("닿으면 체결") 가정의 낙관 비용을 실측하는 열쇠 값."""
    waited_ms: int | None
    """예약(`placed_ms`)→체결까지 걸린 시간(ms). 예약 시각을 모르면 None."""


@dataclass
class PendingLimitOrder:
    """한 오더블록에 예약한 대기 지정가 주문.

    지정가는 `limit_price`(상수) **또는** `live_limit`(봉내 재산정, WAN-132 채택 경로) 중
    정확히 하나로 준다 — `backtest.substep.simulate_zone_limit_trade`와 같은 계약이며,
    둘 다 주면 어느 쪽이 실행됐는지 결과만 보고는 알 수 없어 거부한다(WAN-95의 교훈).

    `rsi_state`는 이 주문이 걸린 시점 **직전까지**의 확정봉으로 시딩돼 있어야 한다
    (`RealtimeRsi.seed_from_closed`). 백테스트 서브스텝 시뮬레이터와 동일한 상태
    머신을 그대로 쓴다. RSI 게이트는 **기본값 `unconditional`(게이트 없음, WAN-123)** —
    옛 극단 게이트는 `rsi_gate_mode` 옵트인으로만 돌고, 첫 탭 면제(`first_tap_free`)는
    `tap_index`를 아는 호출부가 판정해 넘긴다(WAN-100 계약: 이 부품은 몇 번째 탭인지
    스스로 알 수 없다).
    """

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    stop_price: float
    """손절 참조가 기본값(진입 근거 오더블록의 무효화 경계). `live_limit`이 있으면
    체결 순간 `resolve_exits`가 최종값을 낸다(오버라이드 없으면 이 값 그대로)."""
    rsi_state: RealtimeRsi
    limit_price: float | None = None
    take_profit_price: float | None = None
    """상수 지정가 경로의 익절 목표(예약 시점 확정). `live_limit`이면 체결 순간
    산출되므로 함께 줄 수 없다."""
    live_limit: LiveLimitProvider | None = None
    rsi_gate_mode: RsiGateMode = "unconditional"
    first_tap_free: bool = False
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    limit_valid_bars: int | None = 24
    cancel_on_condition_fail: bool = False
    placed_ms: int | None = None
    tap_index: int = 0
    journal_id: int | None = None
    """체결률 실측 장부(`live.order_journal`)의 행 id. 기록을 안 쓰면 None."""
    status: LimitOrderStatus = LimitOrderStatus.PENDING
    bars_elapsed: int = field(default=0)
    first_rested_ms: int | None = None
    """처음으로 주문이 실제 주문판에 걸린 시각(ms). 상수 지정가는 예약 즉시,
    live 밴드는 밴드가 처음 값을 낸 틱이다(워밍업·WAN-75 규칙 3 기각 구간엔 주문이 없다)."""
    last_limit_price: float | None = None
    """마지막으로 주문판에 걸려 있던 지정가(상태 스냅샷·기록용)."""

    def __post_init__(self) -> None:
        if (self.limit_price is None) == (self.live_limit is None):
            raise ValueError("limit_price와 live_limit 중 정확히 하나를 줘야 합니다.")
        if self.live_limit is not None and self.take_profit_price is not None:
            raise ValueError(
                "live_limit을 쓰면 익절 목표는 체결 순간에 산출되므로 "
                "take_profit_price를 함께 줄 수 없습니다."
            )
        if self.limit_price is not None:
            self.last_limit_price = self.limit_price
            self.first_rested_ms = self.placed_ms

    @property
    def is_long(self) -> bool:
        return self.direction is OrderBlockDirection.BULLISH

    def on_bar_close(self, closed_price: float) -> None:
        """상위TF 봉 마감: 실시간 RSI·밴드 상태를 굴리고 경과 봉 수를 늘린다.

        경과 봉 수가 `limit_valid_bars`에 도달하면 만료로 취소한다
        (`limit_valid_bars=None`이면 무기한 — 존 무효화까지 대기, WAN-73).
        """
        if self.status.is_terminal:
            return
        self.rsi_state.commit(closed_price)
        if self.live_limit is not None:
            self.live_limit.commit(closed_price)
        self.bars_elapsed += 1
        if self.limit_valid_bars is not None and self.bars_elapsed >= self.limit_valid_bars:
            self.status = LimitOrderStatus.CANCELLED_EXPIRED

    def on_price(self, low: float, high: float, close: float, *, now_ms: int) -> LimitFill | None:
        """실시간 가격 틱을 반영한다. 체결되면 `LimitFill`, 아니면 None.

        틱은 `(low, high, close)`로 받는다 — 백테스트 서브스텝과 같은 모양이다:
        지정가 재산정·실시간 RSI는 **현재가(close)** 로, 터치 판정은 롱 `low <= 지정가` ·
        숏 `high >= 지정가`로 한다(웹소켓 kline은 형성 중 봉의 누적 고저를 실어 주므로
        갱신 사이의 꼬리도 놓치지 않는다). 진짜 단일 가격 틱이면 셋 다 같은 값을 넣는다.

        기본 게이트(`unconditional`)는 닿으면 즉시 체결한다(워밍업 NaN이어도 — WAN-123).
        옵트인 게이트 미충족이면 주문을 유지하거나(기본) 취소한다.
        """
        if self.status.is_terminal:
            return None

        if self.live_limit is None:
            current_limit = self.limit_price
            assert current_limit is not None  # __post_init__이 보장.
        else:
            # WAN-132: 밴드가 현재가를 표본으로 쓰므로 지정가가 봉내에 움직인다.
            # None이면 지금 주문판에 주문이 없다 — 다음 틱에 생길 수 있으니 유지.
            maybe_limit = self.live_limit.limit_price(close)
            if maybe_limit is None:
                return None
            current_limit = maybe_limit
            if self.first_rested_ms is None:
                self.first_rested_ms = now_ms
            self.last_limit_price = current_limit

        touched = low <= current_limit if self.is_long else high >= current_limit
        if not touched:
            return None

        live_rsi = self.rsi_state.value(close)
        # WAN-123: `unconditional`은 게이트가 아예 없다(워밍업 NaN이어도 체결).
        # WAN-100: 첫 탭 면제는 RSI 값 자체를 보지 않는다. `none`은 이 둘과 다르다 —
        # 게이트 판정만 통과시킬 뿐 워밍업 요구(`live_rsi is not None`)는 남는다.
        condition = (
            self.first_tap_free
            or self.rsi_gate_mode == "unconditional"
            or (
                live_rsi is not None
                and rsi_gate_passes(
                    live_rsi,
                    is_long=self.is_long,
                    mode=self.rsi_gate_mode,
                    rsi_oversold=self.rsi_oversold,
                    rsi_overbought=self.rsi_overbought,
                )
            )
        )
        if not condition:
            if self.cancel_on_condition_fail:
                self.status = LimitOrderStatus.CANCELLED_CONDITION_FAILED
            return None

        stop_price = self.stop_price
        tp_price = self.take_profit_price
        if self.live_limit is not None:
            # 1R = 진입가→무효화 경계라 체결가가 정해진 지금에야 손절·익절이 나온다
            # (WAN-143 `resolve_exits`와 같은 계약 — 백테스트와 같은 구현체를 쓴다).
            exits = self.live_limit.resolve_exits(current_limit)
            if exits is None:
                self.status = LimitOrderStatus.CANCELLED_CONDITION_FAILED
                return None
            stop_price, tp_price = exits

        self.status = LimitOrderStatus.FILLED
        # 관통 폭: 체결 틱이 지정가를 얼마나 지나쳤나(bp, >= 0). 0 근처면 "스치듯 닿은
        # 체결"이다 — `baseline` 낙관 가정의 비용을 재는 1급 실측값(모듈 독스트링).
        raw_penetration = (
            (current_limit - low) / current_limit
            if self.is_long
            else (high - current_limit) / current_limit
        )
        penetration_bps = max(raw_penetration, 0.0) * 10_000.0
        return LimitFill(
            symbol=self.symbol,
            timeframe=self.timeframe,
            direction=self.direction,
            price=current_limit,
            time=now_ms,
            rsi=live_rsi,
            stop_price=stop_price,
            take_profit_price=tp_price,
            penetration_bps=penetration_bps,
            waited_ms=None if self.placed_ms is None else now_ms - self.placed_ms,
        )

    def cancel_invalidated(self) -> None:
        """오더블록 무효화로 대기 주문을 즉시 취소한다."""
        if not self.status.is_terminal:
            self.status = LimitOrderStatus.CANCELLED_INVALIDATED


class LimitOrderBook:
    """심볼·타임프레임별 대기 지정가 주문 장부.

    한 시리즈에는 대기 주문 하나만 둔다(단일 포지션 규칙). 체결·취소된 주문은
    장부에서 제거되며, 체결은 `LimitFill`로 반환해 실행/페이퍼 엔진이 포지션 오픈에
    쓰도록 한다.
    """

    def __init__(self) -> None:
        self._pending: dict[SeriesKey, PendingLimitOrder] = {}

    def pending(self, symbol: str, timeframe: str) -> PendingLimitOrder | None:
        """해당 시리즈의 대기 주문. 없으면 None."""
        return self._pending.get((symbol, timeframe))

    @property
    def open_orders(self) -> list[PendingLimitOrder]:
        return list(self._pending.values())

    def place(self, order: PendingLimitOrder) -> PendingLimitOrder | None:
        """대기 지정가 주문을 예약한다. 이미 대기 주문이 있으면 무시하고 None."""
        key = (order.symbol, order.timeframe)
        if key in self._pending:
            _logger.warning("이미 대기 지정가 주문 존재 — 예약 무시: %s %s", *key)
            return None
        self._pending[key] = order
        return order

    def on_price(
        self,
        symbol: str,
        timeframe: str,
        low: float,
        high: float,
        close: float,
        *,
        now_ms: int,
    ) -> LimitFill | None:
        """시리즈의 대기 주문에 실시간 가격 틱을 반영한다. 체결되면 장부에서 제거."""
        key = (symbol, timeframe)
        order = self._pending.get(key)
        if order is None:
            return None
        fill = order.on_price(low, high, close, now_ms=now_ms)
        if order.status.is_terminal:
            del self._pending[key]
        return fill

    def on_bar_close(
        self, symbol: str, timeframe: str, closed_price: float
    ) -> LimitOrderStatus | None:
        """상위TF 봉 마감을 반영한다. 만료 취소되면 장부에서 제거하고 상태를 반환."""
        key = (symbol, timeframe)
        order = self._pending.get(key)
        if order is None:
            return None
        order.on_bar_close(closed_price)
        if order.status.is_terminal:
            del self._pending[key]
            return order.status
        return None

    def cancel_invalidated(self, symbol: str, timeframe: str) -> PendingLimitOrder | None:
        """오더블록 무효화로 대기 주문을 취소한다. 취소한 주문을 반환(없으면 None)."""
        key = (symbol, timeframe)
        order = self._pending.pop(key, None)
        if order is None:
            return None
        order.cancel_invalidated()
        return order
