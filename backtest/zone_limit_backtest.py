"""존-지정가 + 실시간 RSI(B안) 백테스트 파이프라인 (WAN-41).

`backtest.substep`(1분봉 서브스텝 시뮬레이터)과 `strategy.realtime_rsi`(봉내 실시간
RSI)를 오더블록 탐지(`strategy.order_blocks`)에 **배선**해, `entry_mode=zone_limit`
+ `rsi_mode=realtime` 진입이 실제로 동작하는 end-to-end 백테스트를 제공한다. A안
(종가/확정봉, `backtest.sweep.evaluate` → `BacktestEngine`)과 **같은 공용 비용 모델**
(`common.costs.CostModel`)·리스크 사이징을 쓴다. 다만 B안은 **지정가(메이커) 진입**
이라 진입에 슬리피지가 붙지 않고 메이커 수수료가 적용되는 반면, A안은 시장가(테이커)
진입이라 테이커 수수료+슬리피지가 붙는다 — 이 비대칭이 A vs B 비교(`backtest.ab_run`)
를 실제 체결 비용에 맞게 공정하게 만든다(WAN-37).

## 파이프라인

1. 상위TF OHLCV로 오더블록을 탐지한다(A안과 동일 탐지기·동일 오더블록 재사용 가능).
2. 활성(비-breaker) 오더블록 각각에 대해 존 근단(`ConfluenceParams.zone_limit_price`)에
   지정가를 예약한다. 손절 참조가는 존 원단(무효화 경계, 롱=존 하단·숏=존 상단).
   익절 목표는 탭 봉에서 진입가 너머 **가장 가까운 EMA/VWMA 선**(스냅샷)으로 둔다
   (`use_line_take_profit`).
3. 그 오더블록의 탭 봉부터 **1분봉 서브스텝**(`build_substeps`)으로 봉 내부를
   재구성하고, 직전까지 확정봉 종가로 시딩한 `RealtimeRsi`를 실어
   `simulate_zone_limit_trade`로 지정가 대기 → (실시간 RSI 조건 충족 시) 체결 →
   청산(같은 스텝 관통 손절 포함)을 1분 해상도로 시뮬레이션한다.
4. 체결·청산된 셋업을 A안과 동일한 비용 모델로 `Trade`로 변환하고, **동시 1포지션**
   제약(WAN-23) 아래 시간순으로 배치해 `BacktestResult`를 만든다.

## 1분봉이 없는 구간

1분봉이 커버하지 않는 상위TF 봉의 셋업은 서브스텝이 비어 `NO_TOUCH`로 평가에서
제외된다(이슈 WAN-41의 "1분봉이 없는 구간은 평가에서 제외" 폴백). 따라서 B안 결과는
1분봉이 존재하는 기간으로 자연히 한정된다.
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass

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
from backtest.substep import ZoneLimitStatus, build_substeps, simulate_zone_limit_trade
from backtest.sweep import bars_per_year, default_backtest_config, timeframe_to_ms
from common.costs import Liquidity
from execution.sizing import position_size
from strategy.confluence import ConfluenceStrategy
from strategy.indicators import emas, vwma
from strategy.models import (
    ConfluenceParams,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    SignalExitReason,
)
from strategy.order_blocks import OrderBlockDetector
from strategy.realtime_rsi import RealtimeRsi

logger = logging.getLogger(__name__)

_HTF_COLUMNS = ("open_time", "open", "high", "low", "close", "volume")

#: 서브스텝 청산 사유 → 백테스트 청산 사유.
_EXIT_REASON: dict[SignalExitReason, ExitReason] = {
    SignalExitReason.STOP_LOSS: ExitReason.STOP_LOSS,
    SignalExitReason.TAKE_PROFIT: ExitReason.TAKE_PROFIT,
}


@dataclass(frozen=True)
class _Candidate:
    """체결·청산이 확정된 한 셋업(비용 미반영 원가 정보)."""

    side: PositionSide
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    reason: ExitReason
    stop_price: float
    """리스크 사이징의 손절 참조가(존 원단, 무효화 경계)."""
    penetration: bool = False
    """진입과 손절이 **같은 1분 스텝**에서 일어났는지(관통). 낙관 편향 감사용(WAN-46).

    참이면 가격이 존 근단을 지나 손절선까지 관통한 봉에서 체결됐다는 뜻으로, "좋은
    진입가만 챙기고 손실은 피한" 결과가 아님을 드러낸다.
    """


@dataclass(frozen=True)
class ZoneLimitStats:
    """B안 파이프라인 실행 진단 통계 (WAN-46 낙관 편향 감사·지정가 체결률).

    `eligible`는 탭 봉이 1분봉으로 커버돼 실제 시뮬레이션에 들어간 활성 오더블록
    셋업 수, `filled`는 그중 지정가가 체결된 수다. `penetrations`는 체결된 셋업 중
    같은 스텝에서 손절까지 간(관통) 수로, 단일 포지션 시퀀싱으로 최종 거래에서
    빠진 것도 포함한 원(raw) 감사 수치다.
    """

    eligible: int = 0
    filled: int = 0
    penetrations: int = 0

    @property
    def fill_rate(self) -> float | None:
        """지정가 체결률 = filled / eligible. 대상 셋업이 없으면 None."""
        return self.filled / self.eligible if self.eligible else None


def _prepare_htf(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in _HTF_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"상위TF OHLCV DataFrame에 필요한 컬럼이 없습니다: {missing}")
    frame = df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    return frame.sort_values("open_time").reset_index(drop=True)


def _line_snapshot(params: ConfluenceParams, df: pd.DataFrame, pos: int) -> list[float]:
    """탭 봉(pos)에서의 익절 목표선(EMA/VWMA) 값들(NaN 제외)."""
    if not params.use_line_take_profit:
        return []
    values: list[float] = []
    ema_lengths = params.sorted_tp_ema_lengths
    if ema_lengths:
        ema_frame = emas(df, lengths=ema_lengths, source=params.source)
        for length in ema_lengths:
            v = float(ema_frame[f"ema_{length}"].iloc[pos])
            if not math.isnan(v):
                values.append(v)
    if params.tp_vwma_length is not None:
        v = float(vwma(df, length=params.tp_vwma_length, source=params.source).iloc[pos])
        if not math.isnan(v):
            values.append(v)
    return values


def _take_profit_price(is_long: bool, entry_price: float, lines: list[float]) -> float | None:
    """진입가 너머 가장 가까운 선. 없으면 None(익절 목표 없음)."""
    if is_long:
        beyond = [v for v in lines if v > entry_price]
        return min(beyond) if beyond else None
    beyond = [v for v in lines if v < entry_price]
    return max(beyond) if beyond else None


def run_zone_limit_backtest(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    order_block_result: OrderBlockResult | None = None,
) -> BacktestResult:
    """존-지정가 + 실시간 RSI(B안) 백테스트를 실행한다.

    `htf_df`는 상위TF OHLCV(오더블록 탐지·지표·시딩용, 전체 히스토리), `df_1m`은 봉
    내부 재구성용 1분봉이다. `order_block_result`를 주면 오더블록 탐지를 재실행하지
    않고 재사용한다(A/B가 동일 오더블록으로 비교되도록). 반환값은 A안과 같은
    `BacktestResult`라 `backtest.ab_report`가 그대로 소비한다. 진단 통계(체결률·관통)가
    필요하면 `run_zone_limit_backtest_verbose`를 쓴다.
    """
    result, _ = run_zone_limit_backtest_verbose(
        htf_df,
        df_1m,
        timeframe,
        confluence_params=confluence_params,
        order_block_params=order_block_params,
        backtest_config=backtest_config,
        order_block_result=order_block_result,
    )
    return result


def run_zone_limit_backtest_verbose(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    order_block_result: OrderBlockResult | None = None,
) -> tuple[BacktestResult, ZoneLimitStats]:
    """`run_zone_limit_backtest`와 동일하되 진단 통계도 함께 반환한다(WAN-46).

    반환 통계 `ZoneLimitStats`는 지정가 체결률(체결/대상)과 관통(같은 스텝 진입+손절)
    건수를 담아 낙관 편향 감사에 쓴다.
    """
    params = confluence_params or ConfluenceParams()
    cfg = backtest_config or default_backtest_config(timeframe)
    if cfg.risk_sizing is None:
        logger.warning(
            "risk_sizing=None — B안(존-지정가) 백테스트가 전액 진입 모드"
            "(position_fraction=%.0f%%)로 실행됩니다. 손절 거리와 무관하게 매 거래가 "
            "동일 비율의 자본을 쓰므로 성과가 리스크 정규화되지 않습니다(WAN-65).",
            cfg.position_fraction * 100.0,
        )
    frame = _prepare_htf(htf_df)
    if len(frame) == 0:
        return _empty_result(cfg), ZoneLimitStats()

    htf_ms = timeframe_to_ms(timeframe)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    time_to_pos = {t: i for i, t in enumerate(times)}

    ob_result = order_block_result or OrderBlockDetector(order_block_params).run(htf_df)
    substeps = build_substeps(df_1m, htf_ms)
    substep_times = [s.time for s in substeps]
    deviation_ema: pd.Series | None = None
    if params.long_max_deviation is not None:
        dev_length = params.long_deviation_gate_ema_length
        dev_frame = emas(htf_df, lengths=(dev_length,), source=params.source)
        deviation_ema = dev_frame[f"ema_{dev_length}"]

    candidates: list[_Candidate] = []
    eligible = 0
    filled = 0
    penetrations = 0
    for signal in ob_result.signals:
        if signal.status != "active":
            continue
        pos = time_to_pos.get(signal.trigger_time)
        if pos is None:
            continue
        ob = signal.order_block
        is_long = ob.direction is OrderBlockDirection.BULLISH
        side = PositionSide.LONG if is_long else PositionSide.SHORT
        if (is_long and not cfg.allow_long) or (not is_long and not cfg.allow_short):
            continue
        if not is_long and not params.short_enabled:
            continue  # WAN-68: 숏 완전 제거 게이트.

        limit_price = params.zone_limit_price(ob)
        lines = _line_snapshot(params, htf_df, pos)
        lines_by_key = {str(i): v for i, v in enumerate(lines)}
        if params.min_rr is not None and not ConfluenceStrategy._passes_min_rr(
            1 if is_long else -1, limit_price, ob, lines_by_key, params.min_rr
        ):
            continue  # WAN-68: 최소 손익비 게이트.
        if is_long and params.long_max_deviation is not None:
            assert deviation_ema is not None
            if not ConfluenceStrategy._passes_deviation_gate(
                pos, closes, deviation_ema.tolist(), params.long_max_deviation
            ):
                continue  # WAN-68: 롱 이격도 게이트.

        # 이 셋업의 서브스텝: 탭 봉부터 데이터 끝까지. 단, **탭 봉이 1분봉으로
        # 커버돼야** 한다 — 탭 봉의 상위TF 슬롯에 1분봉이 없으면(미커버·갭) 이
        # 셋업은 평가에서 제외한다(이슈 WAN-41의 "1분봉이 없는 구간 제외" 폴백).
        start = bisect.bisect_left(substep_times, signal.trigger_time)
        setup_substeps = substeps[start:]
        if not setup_substeps:
            continue
        tap_htf = (signal.trigger_time // htf_ms) * htf_ms
        if setup_substeps[0].htf_bar_time != tap_htf:
            continue  # 탭 봉에 1분봉 커버 없음 → 평가 제외.

        eligible += 1
        stop_price = ob.bottom if is_long else ob.top
        tp_price = _take_profit_price(is_long, limit_price, lines)

        rsi_state = _seed_rsi(params, times, closes, setup_substeps[0].htf_bar_time)
        outcome = simulate_zone_limit_trade(
            direction=ob.direction,
            limit_price=limit_price,
            stop_price=stop_price,
            substeps=setup_substeps,
            rsi_state=rsi_state,
            rsi_oversold=params.rsi_oversold,
            rsi_overbought=params.rsi_overbought,
            take_profit_price=tp_price if params.use_line_take_profit else None,
            limit_valid_bars=params.limit_valid_bars,
            invalidation_time=ob.break_time if params.use_order_block_stop else None,
            cancel_on_condition_fail=params.cancel_limit_on_condition_fail,
            stop_before_tp=params.stop_before_take_profit,
        )
        if not outcome.filled or outcome.entry_time is None or outcome.entry_price is None:
            continue

        filled += 1
        penetration = False
        if outcome.status is ZoneLimitStatus.FILLED_EXITED:
            assert outcome.exit_time is not None and outcome.exit_price is not None
            exit_time, exit_price = outcome.exit_time, outcome.exit_price
            reason = (
                _EXIT_REASON[outcome.exit_reason] if outcome.exit_reason else ExitReason.STOP_LOSS
            )
            # 관통: 같은 1분 스텝에서 진입 + 손절(낙관 편향 감사, WAN-46).
            if reason is ExitReason.STOP_LOSS and exit_time == outcome.entry_time:
                penetration = True
                penetrations += 1
        else:
            # 데이터 종료까지 보유 → 마지막 1분봉 종가로 강제 청산.
            exit_time, exit_price = setup_substeps[-1].time, setup_substeps[-1].close
            reason = ExitReason.END_OF_DATA
        candidates.append(
            _Candidate(
                side=side,
                entry_time=outcome.entry_time,
                entry_price=outcome.entry_price,
                exit_time=exit_time,
                exit_price=exit_price,
                reason=reason,
                stop_price=stop_price,
                penetration=penetration,
            )
        )

    trades = _sequence_and_cost(candidates, cfg)
    stats = ZoneLimitStats(eligible=eligible, filled=filled, penetrations=penetrations)
    return build_result_from_trades(trades, cfg, timeframe), stats


def _seed_rsi(
    params: ConfluenceParams, times: list[int], closes: list[float], first_htf: int
) -> RealtimeRsi:
    """탭 봉(first_htf) **직전까지**의 확정봉 종가로 시딩한 실시간 RSI 상태."""
    cut = bisect.bisect_left(times, first_htf)
    return RealtimeRsi.seed_from_closed(closes[:cut], length=params.rsi_length)


def _sequence_and_cost(candidates: list[_Candidate], cfg: BacktestConfig) -> list[Trade]:
    """동시 1포지션 제약으로 셋업을 시간순 배치하고 비용 모델로 `Trade`를 만든다.

    진입 시각 오름차순으로 훑으며, 직전 포지션의 청산 시각 이후에 진입하는 셋업만
    채택한다(겹치면 스킵 — WAN-23의 단일 포지션 규칙). 사이징 자본은 A안 엔진과
    동일하게 진행 중 현금을 쓴다.
    """
    ordered = sorted(candidates, key=lambda c: (c.entry_time, c.exit_time))
    cash = cfg.initial_capital
    busy_until = -1
    trades: list[Trade] = []
    for cand in ordered:
        if cand.entry_time < busy_until:
            continue
        trade = _to_trade(cand, cash, cfg)
        if trade is None:
            continue
        cash += trade.realized_pnl
        busy_until = cand.exit_time
        trades.append(trade)
    return trades


def _to_trade(cand: _Candidate, equity: float, cfg: BacktestConfig) -> Trade | None:
    """공용 비용 모델로 셋업을 `Trade`로 변환한다(WAN-37).

    B안은 **지정가(메이커) 진입**이므로 진입에는 슬리피지가 붙지 않고 메이커 수수료가
    적용된다. 청산은 손절·익절 도달 시 시장가 성격이라 테이커(수수료+슬리피지)로 본다.
    이 비대칭이 A안(시장가=테이커 진입)과의 공정한 비교의 핵심이다.
    """
    side = cand.side
    is_long = side is PositionSide.LONG
    costs = cfg.cost_model
    entry_fill = costs.entry_fill(cand.entry_price, is_long=is_long, liquidity=Liquidity.MAKER)
    if cfg.risk_sizing is not None:
        qty = position_size(
            equity=equity,
            entry_price=entry_fill,
            stop_price=cand.stop_price,
            params=cfg.risk_sizing,
        )
        if qty <= 0.0:
            return None
    else:
        qty = (equity * cfg.position_fraction) / entry_fill
    entry_notional = entry_fill * qty
    entry_fee = costs.fee(entry_notional, Liquidity.MAKER)

    exit_fill = costs.exit_fill(cand.exit_price, is_long=is_long, liquidity=Liquidity.TAKER)
    exit_fee = costs.fee(exit_fill * qty, Liquidity.TAKER)
    gross = side.sign * (exit_fill - entry_fill) * qty
    realized = gross - entry_fee - exit_fee
    return Trade(
        side=side,
        entry_time=cand.entry_time,
        entry_price=entry_fill,
        quantity=qty,
        entry_fee=entry_fee,
        exits=[
            TradeFill(
                time=cand.exit_time,
                price=exit_fill,
                quantity=qty,
                fee=exit_fee,
                reason=cand.reason,
            )
        ],
        realized_pnl=realized,
        return_pct=realized / entry_notional if entry_notional else 0.0,
    )


def build_result_from_trades(
    trades: list[Trade], cfg: BacktestConfig, timeframe: str
) -> BacktestResult:
    """시간순 `Trade` 리스트로 자본곡선·지표를 만들어 `BacktestResult`를 낸다.

    자본곡선은 각 거래의 청산 시각에 실현손익을 순차 반영한 점들로 구성한다(진입
    시작점 포함). MDD·샤프는 이 거래 단위 곡선에서 산출한다.
    """
    equity = cfg.initial_capital
    curve: list[EquityPoint] = []
    ordered = sorted(trades, key=lambda t: t.exit_time)
    if ordered:
        curve.append(EquityPoint(time=ordered[0].entry_time, equity=equity))
    for trade in ordered:
        equity += trade.realized_pnl
        curve.append(EquityPoint(time=trade.exit_time, equity=equity))

    annualization = (
        bars_per_year(timeframe) if cfg.annualization_factor is None else cfg.annualization_factor
    )
    metrics = build_metrics(
        initial_capital=cfg.initial_capital,
        equities=[p.equity for p in curve] or [cfg.initial_capital],
        trades=ordered,
        annualization_factor=annualization,
    )
    return BacktestResult(config=cfg, trades=ordered, equity_curve=curve, metrics=metrics)


def _empty_result(cfg: BacktestConfig) -> BacktestResult:
    metrics = build_metrics(
        initial_capital=cfg.initial_capital,
        equities=[cfg.initial_capital],
        trades=[],
    )
    return BacktestResult(config=cfg, trades=[], equity_curve=[], metrics=metrics)
