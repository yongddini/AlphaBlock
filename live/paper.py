"""페이퍼(무주문) 포지션 추적 (WAN-25).

실제 주문 없이 가상 포지션만 추적한다. 심볼·타임프레임별로 **동시 1포지션**
(WAN-23 전략 규칙과 동일)을 유지하며, 확정 진입에서 열고 계획 청산(익절/손절)
에서 닫는다. 손익은 방향을 반영한 백분율로 계산한다.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

from strategy.models import OrderBlockDirection, SignalExitReason

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
SeriesKey = tuple[str, str]


class PaperPosition(BaseModel):
    """가상 오픈 포지션 하나."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: OrderBlockDirection
    entry_time: int
    entry_price: float
    stop_price: float | None = None
    """진입 시점 손절 참조가(오더블록 무효화). 없으면 None."""
    take_profit_price: float | None = None
    """진입 시점 가장 가까운 익절 목표선 가격. 없으면 None."""

    @property
    def is_long(self) -> bool:
        return self.direction is OrderBlockDirection.BULLISH

    def pnl_pct(self, price: float) -> float:
        """진입가 대비 방향을 반영한 손익률(%). 롱은 상승이 +, 숏은 하락이 +."""
        sign = 1.0 if self.is_long else -1.0
        return sign * (price - self.entry_price) / self.entry_price * 100.0


class ClosedTrade(BaseModel):
    """청산된 가상 거래(진입→익절/손절)."""

    model_config = ConfigDict(frozen=True)

    position: PaperPosition
    exit_time: int
    exit_price: float
    reason: SignalExitReason

    @property
    def realized_pct(self) -> float:
        """실현 손익률(%)."""
        return self.position.pnl_pct(self.exit_price)


class PaperBook:
    """심볼·타임프레임별 가상 포지션 장부.

    한 시리즈에는 최대 하나의 오픈 포지션만 둔다(피라미딩 없음). 이미 열려 있는
    시리즈에 진입이 또 오면 무시하고 경고를 남긴다(전략상 발생하지 않아야 함).
    """

    def __init__(self) -> None:
        self._open: dict[SeriesKey, PaperPosition] = {}
        self.closed: list[ClosedTrade] = []

    def position(self, symbol: str, timeframe: str) -> PaperPosition | None:
        """해당 시리즈의 오픈 포지션. 없으면 None."""
        return self._open.get((symbol, timeframe))

    @property
    def open_positions(self) -> list[PaperPosition]:
        """현재 오픈 중인 모든 가상 포지션."""
        return list(self._open.values())

    def open(self, position: PaperPosition) -> PaperPosition | None:
        """가상 포지션을 연다. 성공하면 그 포지션, 이미 열려 있으면 None."""
        key = (position.symbol, position.timeframe)
        if key in self._open:
            _logger.warning(
                "이미 오픈 포지션이 있어 진입 무시: %s %s", position.symbol, position.timeframe
            )
            return None
        self._open[key] = position
        return position

    def close(
        self,
        symbol: str,
        timeframe: str,
        *,
        exit_time: int,
        exit_price: float,
        reason: SignalExitReason,
    ) -> ClosedTrade | None:
        """오픈 포지션을 닫고 `ClosedTrade`를 반환한다. 오픈 포지션이 없으면 None."""
        key = (symbol, timeframe)
        position = self._open.pop(key, None)
        if position is None:
            _logger.warning("청산할 오픈 포지션이 없음: %s %s", symbol, timeframe)
            return None
        trade = ClosedTrade(
            position=position, exit_time=exit_time, exit_price=exit_price, reason=reason
        )
        self.closed.append(trade)
        return trade
