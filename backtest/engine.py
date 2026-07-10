"""바(bar) 기반 백테스트 루프.

`strategy.order_blocks`가 생성한 `OrderBlockSignal`(status="active")을 진입
트리거로, 과거 OHLCV를 순회하며 거래를 시뮬레이션한다. 한 번에 하나의
포지션만 보유하며(피라미딩 없음), 손절·익절·부분 익절과 수수료·슬리피지를
반영한다.

## 체결·손익 모델

- 진입: 시그널 봉의 `open_time`에서 `signal.price`에 슬리피지를 불리하게 적용해
  체결. 수량 = (현재 equity × `position_fraction`) / 진입 체결가.
- 청산: 봉의 고가/저가로 손절·익절 도달을 판정하고, 도달가에 슬리피지를 불리하게
  적용해 체결. 한 봉에서 손절과 익절이 모두 걸리면 보수적으로 손절을 우선한다.
- 손익은 방향 부호 × (청산가 − 진입가) × 수량으로 계산하며, 롱/숏에 동일하게
  적용된다. 모든 손익은 진입·청산 수수료를 차감한 순값이다.
- 진입 봉에서는 청산을 판정하지 않는다(청산 판정은 진입 후 다음 봉부터).

## 계획 청산(planned exit, WAN-23)

시그널에 `planned_exit`(전략이 진입가 기준으로 미리 계산한 익절 선 도달·손절
오더블록 무효화)이 실려 있으면, 그 포지션은 계획된 봉·참조가·사유대로 전량
청산된다. 계획 청산이 있는 포지션은 고정 %TP/SL·부분익절 규칙을 타지 않으므로
두 경로가 충돌하지 않는다(계획 청산이 없는 포지션만 고정 %규칙을 따른다).

## 펀딩비(funding rate)

`config.funding_enabled=True`이고 `run()`에 심볼의 펀딩비(`FundingRate`)가 전달되면,
포지션 보유 구간 `[진입시각, 청산시각)`에 정산된 각 펀딩마다
`명목가치 × 요율 × 방향`을 손익에 가감한다(WAN-16이 수집한 데이터, WAN-20). 명목가는
진입 체결가 × 잔여 수량을 사용하므로 부분 익절로 수량이 줄면 이후 정산의 명목가도
비례해 줄어든다. 펀딩비용은 청산 시점에 현금·손익에 반영되며, 각 정산은 개별적으로
계산해 합산한다. 펀딩비 데이터는 `data.FundingRateStore.get_rates(symbol, start_ms, end_ms)`로
조회해 전달한다.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from backtest.metrics import build_metrics
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)
from data.funding import Direction, cumulative_funding_cost
from data.models import FundingRate
from strategy.models import OrderBlockDirection, OrderBlockSignal, PlannedExit, SignalExitReason

_REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close")
_QTY_EPS = 1e-12

#: 전략(WAN-23)이 계획한 청산 사유 → 백테스트 청산 사유 매핑.
_PLANNED_EXIT_REASON: dict[SignalExitReason, ExitReason] = {
    SignalExitReason.TAKE_PROFIT: ExitReason.TAKE_PROFIT,
    SignalExitReason.STOP_LOSS: ExitReason.STOP_LOSS,
}


@dataclass(eq=False)
class _OpenPosition:
    """진행 중인 포지션 상태."""

    side: PositionSide
    entry_time: int
    entry_price: float
    initial_quantity: float
    remaining_quantity: float
    entry_fee: float
    stop_price: float | None
    take_profit_price: float | None
    partial_take_profit_price: float | None
    planned_exit: PlannedExit | None = None
    partial_taken: bool = False
    exits: list[TradeFill] = field(default_factory=list)

    def unrealized(self, price: float) -> float:
        return self.side.sign * (price - self.entry_price) * self.remaining_quantity


class BacktestEngine:
    """오더블록 시그널 기반 백테스트 실행기.

    사용법::

        engine = BacktestEngine(BacktestConfig(take_profit_pct=0.05, stop_loss_pct=0.02))
        result = engine.run(ohlcv_df, signals)
    """

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()
        # 현재 엔진은 결정적이지만, 시드를 기록·고정해 향후 확률적 슬리피지 등
        # 확장 시에도 재현이 유지되도록 한다.
        random.seed(self.config.seed)
        self._funding_rates: list[FundingRate] = []

    def run(
        self,
        df: pd.DataFrame,
        signals: list[OrderBlockSignal],
        funding_rates: Sequence[FundingRate] | None = None,
    ) -> BacktestResult:
        frame = self._prepare(df)
        cfg = self.config
        self._funding_rates = list(funding_rates) if funding_rates else []
        missing_funding = cfg.funding_enabled and not self._funding_rates
        if missing_funding and cfg.funding_missing_policy == "error":
            raise ValueError(
                "funding_enabled=True인데 펀딩비 데이터가 없습니다. "
                "funding_missing_policy='zero'로 두거나 펀딩비를 전달하세요."
            )

        times = frame["open_time"].astype("int64").tolist()
        highs = frame["high"].astype(float).tolist()
        lows = frame["low"].astype(float).tolist()
        closes = frame["close"].astype(float).tolist()
        n = len(frame)

        signals_by_time: dict[int, list[OrderBlockSignal]] = defaultdict(list)
        for sig in signals:
            if sig.status == "active":
                signals_by_time[sig.trigger_time].append(sig)

        cash = cfg.initial_capital
        position: _OpenPosition | None = None
        trades: list[Trade] = []
        equity_curve: list[EquityPoint] = []

        for i in range(n):
            t = times[i]

            # 1) 청산 판정 (진입 봉은 다음 루프부터 대상이 됨)
            if position is not None:
                cash, closed = self._process_exits(position, cash, t, highs[i], lows[i])
                if closed is not None:
                    trades.append(closed)
                    position = None

            # 2) 진입 (플랫일 때만, 활성 시그널 1건)
            if position is None:
                for sig in signals_by_time.get(t, ()):
                    side = self._resolve_side(sig)
                    if side is None:
                        continue
                    position, cash = self._open(sig, side, cash, t)
                    break

            # 3) 봉 종가 기준 평가금 기록
            equity = cash + (position.unrealized(closes[i]) if position is not None else 0.0)
            equity_curve.append(EquityPoint(time=t, equity=equity))

        # 잔여 포지션은 마지막 종가로 강제 청산
        if position is not None and n > 0:
            cash, closed = self._force_close(position, cash, times[-1], closes[-1])
            trades.append(closed)
            equity_curve[-1] = EquityPoint(time=times[-1], equity=cash)

        equities = [p.equity for p in equity_curve]
        metrics = build_metrics(
            initial_capital=cfg.initial_capital,
            equities=equities,
            trades=trades,
            annualization_factor=cfg.annualization_factor,
        )
        return BacktestResult(config=cfg, trades=trades, equity_curve=equity_curve, metrics=metrics)

    def _resolve_side(self, sig: OrderBlockSignal) -> PositionSide | None:
        if sig.direction is OrderBlockDirection.BULLISH:
            return PositionSide.LONG if self.config.allow_long else None
        return PositionSide.SHORT if self.config.allow_short else None

    def _entry_fill(self, price: float, side: PositionSide) -> float:
        slip = self.config.slippage
        return price * (1.0 + slip) if side is PositionSide.LONG else price * (1.0 - slip)

    def _exit_fill(self, price: float, side: PositionSide) -> float:
        slip = self.config.slippage
        return price * (1.0 - slip) if side is PositionSide.LONG else price * (1.0 + slip)

    def _open(
        self, sig: OrderBlockSignal, side: PositionSide, cash: float, t: int
    ) -> tuple[_OpenPosition, float]:
        cfg = self.config
        equity = cash  # 플랫 상태이므로 미실현손익 없음
        entry_price = self._entry_fill(sig.price, side)
        notional = equity * cfg.position_fraction
        quantity = notional / entry_price
        entry_fee = notional * cfg.fee_rate
        cash -= entry_fee

        sign = side.sign
        stop_price = (
            entry_price * (1.0 - sign * cfg.stop_loss_pct)
            if cfg.stop_loss_pct is not None
            else None
        )
        take_profit_price = (
            entry_price * (1.0 + sign * cfg.take_profit_pct)
            if cfg.take_profit_pct is not None
            else None
        )
        partial_price = (
            entry_price * (1.0 + sign * cfg.partial_take_profit_pct)
            if cfg.partial_take_profit_pct is not None
            else None
        )

        position = _OpenPosition(
            side=side,
            entry_time=t,
            entry_price=entry_price,
            initial_quantity=quantity,
            remaining_quantity=quantity,
            entry_fee=entry_fee,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            partial_take_profit_price=partial_price,
            planned_exit=sig.planned_exit,
        )
        return position, cash

    def _close_qty(
        self,
        pos: _OpenPosition,
        quantity: float,
        level_price: float,
        cash: float,
        t: int,
        reason: ExitReason,
    ) -> float:
        fill = self._exit_fill(level_price, pos.side)
        notional = fill * quantity
        fee = notional * self.config.fee_rate
        realized = pos.side.sign * (fill - pos.entry_price) * quantity
        cash += realized - fee
        pos.exits.append(TradeFill(time=t, price=fill, quantity=quantity, fee=fee, reason=reason))
        pos.remaining_quantity -= quantity
        return cash

    def _process_exits(
        self, pos: _OpenPosition, cash: float, t: int, high: float, low: float
    ) -> tuple[float, Trade | None]:
        """한 봉에 대해 손절·부분익절·익절을 판정하고 체결한다.

        완전 청산되면 (cash, Trade)를, 아니면 (cash, None)을 반환한다.
        """
        # 전략(WAN-23)이 계획한 청산이 있으면 그 봉에서 계획대로 전량 청산한다
        # (익절=선 도달, 손절=오더블록 무효화). 고정 %TP/SL 경로와 배타적으로 동작해
        # 충돌하지 않는다 — 계획 청산이 없는 포지션만 아래 고정 %규칙을 탄다.
        planned = pos.planned_exit
        if planned is not None:
            if t >= planned.time:
                cash = self._close_qty(
                    pos,
                    pos.remaining_quantity,
                    planned.price,
                    cash,
                    t,
                    _PLANNED_EXIT_REASON[planned.reason],
                )
                return self._finalize_and_settle(pos, cash)
            return cash, None

        is_long = pos.side is PositionSide.LONG

        # 보수적 가정: 손절과 익절이 같은 봉에 걸리면 손절을 우선한다.
        if pos.stop_price is not None:
            stop_hit = low <= pos.stop_price if is_long else high >= pos.stop_price
            if stop_hit:
                cash = self._close_qty(
                    pos, pos.remaining_quantity, pos.stop_price, cash, t, ExitReason.STOP_LOSS
                )
                return self._finalize_and_settle(pos, cash)

        # 부분 익절 (전체 익절보다 앞선 목표)
        pp = pos.partial_take_profit_price
        if pp is not None and not pos.partial_taken:
            partial_hit = high >= pp if is_long else low <= pp
            if partial_hit:
                qty = pos.initial_quantity * self.config.partial_exit_fraction
                qty = min(qty, pos.remaining_quantity)
                cash = self._close_qty(pos, qty, pp, cash, t, ExitReason.PARTIAL_TAKE_PROFIT)
                pos.partial_taken = True
                if pos.remaining_quantity <= _QTY_EPS:
                    return self._finalize_and_settle(pos, cash)

        # 전체 익절
        tp = pos.take_profit_price
        if tp is not None:
            tp_hit = high >= tp if is_long else low <= tp
            if tp_hit:
                cash = self._close_qty(
                    pos, pos.remaining_quantity, tp, cash, t, ExitReason.TAKE_PROFIT
                )
                return self._finalize_and_settle(pos, cash)

        return cash, None

    def _force_close(
        self, pos: _OpenPosition, cash: float, t: int, close_price: float
    ) -> tuple[float, Trade]:
        cash = self._close_qty(
            pos, pos.remaining_quantity, close_price, cash, t, ExitReason.END_OF_DATA
        )
        return self._finalize_and_settle(pos, cash)

    def _funding_cost(self, pos: _OpenPosition) -> float:
        """보유 구간 `[진입시각, 최종청산시각)`의 누적 펀딩비용을 계산한다.

        수량이 바뀌는 (부분)청산 시점으로 구간을 분할해, 각 구간의 잔여 명목가
        (진입가 × 잔여수량)에 그 구간에 정산된 펀딩을 적용한다. 양수=지불, 음수=수취.
        """
        if not self.config.funding_enabled or not self._funding_rates:
            return 0.0
        direction: Direction = "long" if pos.side is PositionSide.LONG else "short"
        include_predicted = self.config.funding_include_predicted
        total = 0.0
        seg_start = pos.entry_time
        remaining = pos.initial_quantity
        for fill in sorted(pos.exits, key=lambda f: f.time):
            if fill.time > seg_start and remaining > _QTY_EPS:
                total += cumulative_funding_cost(
                    self._funding_rates,
                    position_notional=pos.entry_price * remaining,
                    direction=direction,
                    start_ms=seg_start,
                    end_ms=fill.time,
                    include_predicted=include_predicted,
                )
            remaining -= fill.quantity
            seg_start = fill.time
        return total

    def _finalize_and_settle(self, pos: _OpenPosition, cash: float) -> tuple[float, Trade]:
        """포지션을 마감하며 펀딩비용을 정산하고 (현금, Trade)를 반환한다."""
        funding_cost = self._funding_cost(pos)
        cash -= funding_cost
        return cash, self._finalize(pos, funding_cost)

    @staticmethod
    def _finalize(pos: _OpenPosition, funding_cost: float) -> Trade:
        exit_fees = sum(f.fee for f in pos.exits)
        gross = sum(pos.side.sign * (f.price - pos.entry_price) * f.quantity for f in pos.exits)
        realized_pnl = gross - exit_fees - pos.entry_fee - funding_cost
        entry_notional = pos.entry_price * pos.initial_quantity
        return_pct = realized_pnl / entry_notional if entry_notional else 0.0
        return Trade(
            side=pos.side,
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            quantity=pos.initial_quantity,
            entry_fee=pos.entry_fee,
            exits=list(pos.exits),
            funding_cost=funding_cost,
            realized_pnl=realized_pnl,
            return_pct=return_pct,
        )

    @staticmethod
    def _prepare(df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"OHLCV DataFrame에 필요한 컬럼이 없습니다: {missing}")
        frame = df
        if "closed" in df.columns:
            frame = frame[frame["closed"].astype(bool)]
        return frame.sort_values("open_time").reset_index(drop=True)


def run_backtest(
    df: pd.DataFrame,
    signals: list[OrderBlockSignal],
    config: BacktestConfig | None = None,
    funding_rates: Sequence[FundingRate] | None = None,
) -> BacktestResult:
    """`BacktestEngine(config).run(df, signals, funding_rates)`의 편의 함수."""
    return BacktestEngine(config).run(df, signals, funding_rates)
