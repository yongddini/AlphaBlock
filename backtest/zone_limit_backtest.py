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
   `run_zone_limit_portfolio_backtest`로 들어오면 이 4단계만 동시 다중 포지션 회계
   (`backtest.portfolio`, WAN-103)로 바뀐다 — 1~3단계(셋업·체결 판정)는 같으므로 두
   실행의 차이는 **오직 포지션 제약**이다. 기본 진입점은 여전히 동시 1포지션이다.

## 1분봉이 없는 구간

1분봉이 커버하지 않는 상위TF 봉의 셋업은 서브스텝이 비어 `NO_TOUCH`로 평가에서
제외된다(이슈 WAN-41의 "1분봉이 없는 구간은 평가에서 제외" 폴백). 따라서 B안 결과는
1분봉이 존재하는 기간으로 자연히 한정된다.
"""

from __future__ import annotations

import bisect
import copy
import logging
import math
import random
from collections.abc import Sequence
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
from backtest.portfolio import PortfolioParams, PortfolioStats, sequence_portfolio
from backtest.substep import ZoneLimitStatus, build_substeps, simulate_zone_limit_trade
from backtest.sweep import bars_per_year, default_backtest_config, timeframe_to_ms
from common.costs import Liquidity
from data.funding import Direction, cumulative_funding_cost, funding_coverage
from data.models import FundingRate
from execution.sizing import position_size
from strategy.confluence import ConfluenceStrategy, entry_candidate_signals
from strategy.indicators import emas, vwma
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    RsiGateMode,
    SignalExitReason,
    deviation_entry_price,
)
from strategy.order_blocks import OrderBlockDetector
from strategy.realtime_band import RealtimeBand
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
    order_block: OrderBlock | None = None
    """이 셋업의 근거 오더블록(WAN-77). 체결·청산 로직에는 쓰이지 않고, 사후 분석
    (예: `ob_volume` 기반 성과 분해)이 거래를 원본 존에 조인할 때만 참조한다."""
    tap_index: int = 0
    """이 셋업의 탭 순번(`OrderBlockSignal.tap_index` 그대로, WAN-83). 진단 전용."""
    zone_key: frozenset[int] | None = None
    """이 셋업이 속한 존의 안정적 식별자(`OrderBlockSignal.zone_key` 그대로, WAN-83).
    포지션 충돌로 스킵된 첫 탭이 같은 존에서 재탭됐는지 사후에 그룹핑할 때 쓴다.
    진단 전용이며 체결·청산 로직에는 쓰이지 않는다."""
    trigger_time: int = 0
    """이 셋업의 탭이 발생한 상위TF 봉 시각(`OrderBlockSignal.trigger_time` 그대로).

    `SetupDiagnostic.trigger_time`과 같은 값이라, 거래를 그 셋업의 진단 레코드에 **정확히**
    조인하는 키다(WAN-104). `entry_time`은 탭 봉 *내부*의 체결 시각이라 이 값과 다르고,
    `(zone_key, tap_index)`는 유일하지 않다 — 병합 존은 새로 편입된 구성 존이 같은
    클러스터 안에서 다시 `tap_index=0`을 받을 수 있기 때문이다(WAN-81 §5). 진단 전용이며
    체결·청산 로직에는 쓰이지 않는다."""


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
    dropped: int = 0
    """`fill_dropout_rate`(WAN-96)로 탈락시킨 체결 건수. 기본 실행에서는 항상 0이다.

    `filled`에는 포함되지 않는다 — `filled`는 보수화를 **적용한 뒤** 실제로 거래가 된
    셋업 수이므로, `fill_rate`가 곧 보수화된 체결률이다.
    """

    @property
    def fill_rate(self) -> float | None:
        """지정가 체결률 = filled / eligible. 대상 셋업이 없으면 None."""
        return self.filled / self.eligible if self.eligible else None


@dataclass(frozen=True)
class SetupDiagnostic:
    """eligible 셋업 하나의 체결 여부 기록 (WAN-96 체결 편향 진단).

    체결률이 28%라면 **나머지 72%는 어떤 셋업이었나**를 물어야 한다. 체결된 것만 성과가
    좋은 방향으로 골라졌다면 수익률이 통째로 착시이기 때문이다. 이 레코드는 체결/미체결
    양쪽에 동일한 사후 분석(가상 진입가 부여 → 기대 손익)을 적용할 수 있도록 셋업의
    원본 조건을 남긴다. 체결·청산 로직에는 전혀 쓰이지 않는 순수 진단용이다.
    """

    trigger_time: int
    """탭이 발생한 상위TF 봉의 `open_time`(ms)."""
    tap_bar_time: int
    """탭 봉의 상위TF 슬롯 시각(ms). 가상 진입은 이 봉이 마감된 뒤부터 평가한다."""
    tap_close: float
    """탭 봉 종가 = 가상 진입가(= A안이 실제로 지불했을 가격)."""
    side: PositionSide
    limit_price: float | None
    """주문이 걸린 지정가. `band_bar="intrabar_live"`(WAN-119)에서는 밴드가 봉내에
    움직여 **미체결 셋업에 단일 주문 가격이 존재하지 않으므로**, 체결됐으면 실제 체결가,
    아니면 `None`이다. 다른 모드에서는 항상 값이 있다(탭 봉에서 상수로 정해진다)."""
    stop_price: float
    filled: bool
    """보수화를 적용한 뒤 최종적으로 체결됐는지."""
    dropped: bool
    """`fill_dropout_rate`로 탈락했는지(탈락했다면 `filled`는 False)."""
    status: ZoneLimitStatus
    tap_index: int = 0
    """이 셋업의 탭 순번(`OrderBlockSignal.tap_index` 그대로, WAN-83). 진단 전용."""
    zone_key: frozenset[int] | None = None
    """이 셋업이 속한 존의 안정적 식별자(`OrderBlockSignal.zone_key` 그대로, WAN-83).
    진단 전용이며 체결·청산 로직에는 쓰이지 않는다."""


def _prepare_htf(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in _HTF_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"상위TF OHLCV DataFrame에 필요한 컬럼이 없습니다: {missing}")
    frame = df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    return frame.sort_values("open_time").reset_index(drop=True)


def _line_snapshots(params: ConfluenceParams, df: pd.DataFrame) -> list[list[float]] | None:
    """전 구간의 익절 목표선(EMA/VWMA) 값 스냅샷을 위치(pos)별로 1회만 계산한다.

    이전에는 시그널마다 `emas`/`vwma`를 다시 계산해 오더블록 셋업 수만큼 같은 EMA/VWMA
    시리즈를 중복 산출했다(WAN-78 성능). `use_line_take_profit=False`면 애초에 필요
    없으므로 None을 반환해 호출부가 빈 리스트를 쓰게 한다.
    """
    if not params.use_line_take_profit:
        return None
    columns: list[list[float]] = []
    ema_lengths = params.sorted_tp_ema_lengths
    if ema_lengths:
        ema_frame = emas(df, lengths=ema_lengths, source=params.source)
        for length in ema_lengths:
            columns.append(ema_frame[f"ema_{length}"].astype(float).tolist())
    if params.tp_vwma_length is not None:
        vwma_series = vwma(df, length=params.tp_vwma_length, source=params.source)
        columns.append(vwma_series.astype(float).tolist())
    if not columns:
        return [[] for _ in range(len(df))]
    return [[col[pos] for col in columns if not math.isnan(col[pos])] for pos in range(len(df))]


def _take_profit_price(is_long: bool, entry_price: float, lines: list[float]) -> float | None:
    """진입가 너머 가장 가까운 선. 없으면 None(익절 목표 없음)."""
    if is_long:
        beyond = [v for v in lines if v > entry_price]
        return min(beyond) if beyond else None
    beyond = [v for v in lines if v < entry_price]
    return max(beyond) if beyond else None


@dataclass
class _IntrabarLiveLimit:
    """`band_bar="intrabar_live"`(WAN-119)의 봉내 지정가 공급자 — `LiveLimitProvider` 구현.

    시뮬레이터는 오더블록·밴드·오프셋 규칙을 모르므로(셋업 하나의 체결·청산만 본다),
    "지금 이 순간 주문판에 걸려 있는 가격"을 이 객체가 낸다. 가격 확정 순서는 정적
    경로(WAN-99)와 **완전히 같다**: 밴드 → `deviation_entry_price`(볼린저가 이김,
    기각 시 주문 없음) → `apply_zone_limit_offset`. 달라지는 건 밴드가 상수가 아니라
    **매 서브스텝 현재가로 다시 계산된다**는 것뿐이다.
    """

    band: RealtimeBand
    order_block: OrderBlock
    is_long: bool
    params: ConfluenceParams
    stop_price: float
    lines: list[float]

    @property
    def _direction_sign(self) -> int:
        return 1 if self.is_long else -1

    def commit(self, closed_price: float) -> None:
        self.band.commit(closed_price)

    def limit_price(self, live_price: float) -> float | None:
        d_sign = self._direction_sign
        band = self.band.value(live_price, d_sign)
        if band is None:
            return None  # WAN-75: 워밍업이라 판정 불가.
        price = deviation_entry_price(d_sign, self.order_block, band)
        if price is None:
            return None  # WAN-75 규칙 3: 밴드가 존 전체보다 불리 — 지금은 주문이 없다.
        return self.params.apply_zone_limit_offset(price, is_long=self.is_long)

    def take_profit_price(self, limit_price: float) -> float | None:
        return _resolve_take_profit(
            self.params, self.is_long, limit_price, self.stop_price, self.lines
        )


def _resolve_take_profit(
    params: ConfluenceParams,
    is_long: bool,
    entry_price: float,
    stop_price: float,
    lines: list[float],
) -> float | None:
    """`take_profit_mode`에 따른 익절 목표가(WAN-73).

    `fixed_r`: 진입가~손절 참조가(위험 1R)의 `take_profit_r`배를 진입가 너머에 고정.
    `line`(기본): 현행대로 `use_line_take_profit`이면 진입가 너머 가장 가까운 선.
    """
    if params.take_profit_mode == "fixed_r":
        risk = entry_price - stop_price if is_long else stop_price - entry_price
        if risk <= 0:
            return None
        signed_risk = risk * params.take_profit_r
        return entry_price + (signed_risk if is_long else -signed_risk)
    if not params.use_line_take_profit:
        return None
    return _take_profit_price(is_long, entry_price, lines)


def run_zone_limit_backtest(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    order_block_result: OrderBlockResult | None = None,
    funding_rates: Sequence[FundingRate] | None = None,
) -> BacktestResult:
    """존-지정가 + 실시간 RSI(B안) 백테스트를 실행한다.

    `htf_df`는 상위TF OHLCV(오더블록 탐지·지표·시딩용, 전체 히스토리), `df_1m`은 봉
    내부 재구성용 1분봉이다. `order_block_result`를 주면 오더블록 탐지를 재실행하지
    않고 재사용한다(A/B가 동일 오더블록으로 비교되도록). 반환값은 A안과 같은
    `BacktestResult`라 `backtest.ab_report`가 그대로 소비한다. 진단 통계(체결률·관통)가
    필요하면 `run_zone_limit_backtest_verbose`를 쓴다.

    `funding_rates`(WAN-95)를 주고 `backtest_config.funding_enabled=True`이면 보유
    구간 펀딩비가 손익에 반영된다 — A안 `evaluate()`와 동일하게 **호출부가 심볼별
    펀딩 데이터를 직접 넘겨야** 한다. 안 넘기면 `funding_missing_policy`에 따라
    커버리지 0%가 결과에 드러난다(조용히 0으로 때우지 않는다).
    """
    result, _ = run_zone_limit_backtest_verbose(
        htf_df,
        df_1m,
        timeframe,
        confluence_params=confluence_params,
        order_block_params=order_block_params,
        backtest_config=backtest_config,
        order_block_result=order_block_result,
        funding_rates=funding_rates,
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
    funding_rates: Sequence[FundingRate] | None = None,
    setup_sink: list[SetupDiagnostic] | None = None,
) -> tuple[BacktestResult, ZoneLimitStats]:
    """`run_zone_limit_backtest`와 동일하되 진단 통계도 함께 반환한다(WAN-46).

    반환 통계 `ZoneLimitStats`는 지정가 체결률(체결/대상)과 관통(같은 스텝 진입+손절)
    건수를 담아 낙관 편향 감사에 쓴다. `setup_sink`(WAN-96)를 주면 eligible 셋업별
    체결 여부 레코드를 채워 체결 편향 진단에 쓸 수 있다.
    """
    params = confluence_params or ConfluenceParams()
    if params.entry_mode != "zone_limit":
        raise ValueError(
            f'run_zone_limit_backtest()는 지정가 진입(B안) 전용인데 entry_mode="'
            f'{params.entry_mode}"가 들어왔습니다. 종가 진입은 backtest.sweep.evaluate()를 '
            "쓰세요(WAN-95)."
        )
    cfg = backtest_config or default_backtest_config(timeframe)
    rates = list(funding_rates) if funding_rates else []
    if cfg.funding_enabled and not rates and cfg.funding_missing_policy == "error":
        raise ValueError(
            "funding_enabled=True인데 펀딩비 데이터가 없습니다. "
            "funding_missing_policy='zero'로 두거나 펀딩비를 전달하세요."
        )
    if cfg.risk_sizing is None:
        logger.warning(
            "risk_sizing=None — B안(존-지정가) 백테스트가 전액 진입 모드"
            "(position_fraction=%.0f%%)로 실행됩니다. 손절 거리와 무관하게 매 거래가 "
            "동일 비율의 자본을 쓰므로 성과가 리스크 정규화되지 않습니다(WAN-65).",
            cfg.position_fraction * 100.0,
        )
    candidates, stats = build_zone_limit_candidates(
        htf_df,
        df_1m,
        timeframe,
        params=params,
        cfg=cfg,
        order_block_params=order_block_params,
        order_block_result=order_block_result,
        setup_sink=setup_sink,
    )
    trades = _sequence_and_cost(candidates, cfg, rates)
    coverage = _check_funding_coverage(htf_df, rates, cfg)
    result = build_result_from_trades(trades, cfg, timeframe, funding_coverage_value=coverage)
    return result, stats


def apply_portfolio_leverage(cfg: BacktestConfig, portfolio: PortfolioParams) -> BacktestConfig:
    """`portfolio.leverage`를 사이징 파라미터에 싣는다 (WAN-103).

    레버리지 축이 `PortfolioParams` 한 곳에만 있어야 "어느 레버리지로 돈 결과인가"가
    한 곳에서 읽힌다 — `cfg.risk_sizing.leverage`와 `portfolio.leverage`가 각각 값을
    들면 둘이 조용히 갈라지고, 그때 실제로 쓰인 값이 무엇인지 CSV만 봐서는 알 수 없다.
    """
    if cfg.risk_sizing is None:
        return cfg
    sizing = cfg.risk_sizing.model_copy(update={"leverage": portfolio.leverage})
    return cfg.model_copy(update={"risk_sizing": sizing})


def run_zone_limit_portfolio_backtest(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    portfolio: PortfolioParams,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    order_block_result: OrderBlockResult | None = None,
    funding_rates: Sequence[FundingRate] | None = None,
) -> tuple[BacktestResult, ZoneLimitStats, PortfolioStats]:
    """동시 다중 포지션(WAN-103)으로 B안 백테스트를 실행한다.

    셋업 탐색·체결 시뮬레이션은 `run_zone_limit_backtest_verbose`와 **완전히 동일**하고
    (같은 `build_zone_limit_candidates`를 탄다), 다른 것은 그 후보를 배치하는 회계뿐이다:
    동시 1포지션 시퀀서 대신 `backtest.portfolio.sequence_portfolio`가 여러 포지션을
    동시에 굴린다. 그래서 이 함수와 단일 포지션 실행의 차이는 **오직 포지션 제약**이며,
    대조표의 두 열이 같은 셋업 풀에서 나온다.

    `PortfolioParams(leverage=1.0, max_concurrent=1)`은 동시 1포지션 규칙과 **같은 거래를
    낸다**(`tests/test_portfolio.py`가 채택 엔진과의 일치를 고정한다) — 슬롯이 하나뿐이면
    존 제약은 어차피 걸릴 일이 없고, 명목 상한도 플랫 상태의 per-trade clamp와 같아지기
    때문이다. 그래서 pooled 대조군은 이 경로로 낼 수 있다. 다만 series 대조군은 여전히
    `sequence_with_candidates`(채택 엔진 그 자체)로 내는 게 안전하다 — 시퀀서에 버그가
    생기면 대조군과 실험군이 **함께** 틀어져 차이가 0으로 보일 수 있다.
    """
    params = confluence_params or ConfluenceParams()
    if params.entry_mode != "zone_limit":
        raise ValueError(
            f'run_zone_limit_portfolio_backtest()는 지정가 진입(B안) 전용인데 entry_mode="'
            f'{params.entry_mode}"가 들어왔습니다(WAN-95).'
        )
    cfg = apply_portfolio_leverage(backtest_config or default_backtest_config(timeframe), portfolio)
    rates = list(funding_rates) if funding_rates else []
    candidates, stats = build_zone_limit_candidates(
        htf_df,
        df_1m,
        timeframe,
        params=params,
        cfg=cfg,
        order_block_params=order_block_params,
        order_block_result=order_block_result,
    )
    paired, portfolio_stats = sequence_portfolio(
        candidates,
        cfg,
        _to_trade,
        portfolio=portfolio,
        funding_rates=rates,
    )
    coverage = _check_funding_coverage(htf_df, rates, cfg)
    result = build_result_from_trades(
        [trade for _, trade in paired], cfg, timeframe, funding_coverage_value=coverage
    )
    return result, stats, portfolio_stats


def _check_funding_coverage(
    htf_df: pd.DataFrame, rates: list[FundingRate], cfg: BacktestConfig
) -> float | None:
    """백테스트 구간의 펀딩 커버리지(WAN-63/WAN-95). 펀딩 미사용이면 None.

    A안 엔진(`BacktestEngine._check_funding_coverage`)과 같은 규칙으로, 커버리지가
    100% 미만이면 경고하고 정책이 `"error"`면 중단한다 — "net"이라 이름 붙은 성과가
    실은 펀딩을 빠뜨린 값이라는 조용한 실패를 드러내기 위해서다.
    """
    if not cfg.funding_enabled:
        return None
    frame = _prepare_htf(htf_df)
    if len(frame) == 0:
        return 1.0
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    coverage = funding_coverage(
        rates, times[0], times[-1], include_predicted=cfg.funding_include_predicted
    )
    if coverage < 1.0:
        logger.warning(
            "펀딩 데이터 커버리지 %.1f%% (<100%%) — 결측 구간 펀딩비를 0으로 처리합니다. "
            "비용이 과소 계상되어 성과가 부풀려질 수 있습니다. 구간=[%d, %d)",
            coverage * 100.0,
            times[0],
            times[-1],
        )
        if cfg.funding_missing_policy == "error":
            raise ValueError(
                "funding_missing_policy='error'인데 펀딩 데이터 커버리지가 "
                f"{coverage:.1%}로 100% 미만입니다. 백테스트 구간 전체의 펀딩 이력을 "
                "백필하거나 정책을 'zero'로 두세요."
            )
    return coverage


def build_zone_limit_candidates(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    order_block_params: OrderBlockParams | None = None,
    order_block_result: OrderBlockResult | None = None,
    rsi_oversold: float | None = None,
    rsi_overbought: float | None = None,
    rsi_gate_mode: RsiGateMode | None = None,
    setup_sink: list[SetupDiagnostic] | None = None,
) -> tuple[list[_Candidate], ZoneLimitStats]:
    """B안 셋업 순회 → 1분 서브스텝 시뮬레이션까지(비용 반영 전 원가 후보 목록).

    `run_zone_limit_backtest_verbose`의 핵심 루프를 재사용 가능하게 뺀 것이다.
    `rsi_oversold`/`rsi_overbought`/`rsi_gate_mode`를 지정하면 `params`의 실시간 RSI
    게이트 값을 덮어쓴다 — 오더블록 존·손절·익절·비용·1분 서브스텝은 동일하게 두고
    RSI 진입 조건만 무력화(예: `rsi_gate_mode="none"`은 RSI가 유효하기만 하면 항상
    통과)한 "무작위 대조군" 풀을 만들 때 쓴다(WAN-70).

    `setup_sink`(WAN-96)를 주면 eligible 셋업마다 `SetupDiagnostic`을 append한다 —
    체결/미체결을 같은 기준으로 사후 비교하는 편향 진단용이며, 체결 로직에는 영향이 없다.
    """
    frame = _prepare_htf(htf_df)
    if len(frame) == 0:
        return [], ZoneLimitStats()

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

    # WAN-119: `intrabar_live`는 밴드가 봉내에 움직이므로 봉 단위 시리즈로 표현할 수 없다
    # — 정적 밴드 대신 서브스텝마다 값을 내는 `RealtimeBand`를 셋업별로 만들어 쓴다.
    deviation = params.deviation_filter
    live_band_mode = deviation is not None and deviation.band_bar == "intrabar_live"
    if live_band_mode:
        if params.min_rr is not None:
            raise ValueError(
                "band_bar='intrabar_live'는 min_rr 게이트를 지원하지 않습니다 — "
                "손익비는 진입가에 달렸는데 진입가가 봉내에 정해지므로 탭 봉 시점에 "
                "판정할 수 없습니다(채택 기본값은 min_rr=None)."
            )
        if params.source != "close":
            raise ValueError(
                f"band_bar='intrabar_live'는 source='close'만 지원합니다: {params.source!r} — "
                "서브스텝이 공급하는 현재가는 체결 가격이라 hl2 같은 합성 소스의 "
                "실시간 대응값이 없습니다."
            )

    filter_components: tuple[list[float], list[float]] | None = None
    if deviation is not None and not live_band_mode:
        filter_components = ConfluenceStrategy.deviation_filter_components(
            htf_df, deviation, params.source
        )
    line_snapshots = _line_snapshots(params, htf_df)

    effective_oversold = params.rsi_oversold if rsi_oversold is None else rsi_oversold
    effective_overbought = params.rsi_overbought if rsi_overbought is None else rsi_overbought
    effective_gate_mode = params.rsi_gate_mode if rsi_gate_mode is None else rsi_gate_mode
    rsi_seeder = _IncrementalRsiSeeder(closes, params.rsi_length)
    # 체결률 하향 민감도(WAN-96). rate=0이면 난수를 아예 뽑지 않아 기본 실행은 WAN-95와
    # 비트 단위로 동일하다.
    dropout_rng = random.Random(params.fill_dropout_seed) if params.fill_dropout_rate > 0 else None

    candidates: list[_Candidate] = []
    eligible = 0
    filled = 0
    penetrations = 0
    dropped = 0
    for signal in entry_candidate_signals(ob_result, params, times, closes, time_to_pos):
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

        limit_price: float | None = params.zone_limit_price(ob)
        if filter_components is not None:
            assert deviation is not None
            anchor_vals, width_vals = filter_components
            d_sign = 1 if is_long else -1
            # WAN-115: `band_bar="prev_closed"`면 직전 확정봉의 밴드를 읽는다 — 탭 봉
            # 자신의 SMA20은 그 봉 종가를 포함하는데 체결은 봉 **내부**라 룩어헤드다.
            band = ConfluenceStrategy.deviation_band_at(
                pos, d_sign, anchor_vals, width_vals, deviation.band_bar
            )
            if band is None:
                continue  # WAN-75: 워밍업 중이라 판정 불가(WAN-115: 구간 첫 봉 포함).
            new_price = deviation_entry_price(d_sign, ob, band)
            if new_price is None:
                continue  # WAN-75: 밴드가 존 전체보다 불리한 쪽 — 진입하지 않음(규칙 3).
            limit_price = new_price
        lines = line_snapshots[pos] if line_snapshots is not None else []
        if live_band_mode:
            # WAN-119: 지정가·1R·익절 목표가 전부 봉내 체결 순간에 정해진다 — 탭 봉
            # 시점에는 값이 없다. 지어내지 않고 `None`으로 두고 아래에서 공급자를 만든다.
            limit_price = None
        else:
            # WAN-99: 체결 오프셋은 **볼린저 재산정 뒤에** 얹는다 — WAN-95의 "볼린저가
            # 이긴다" 규칙을 깨지 않기 위해서다. 위 `continue`(밴드가 "진입 없음" 판정)를
            # 지나온 셋업에만 적용되므로 오프셋이 진입 자체를 되살리는 일도 없다. 여기서
            # 확정된 가격이 아래 min_rr 게이트·손절 거리(1R)·익절 목표의 기준이 된다.
            assert limit_price is not None  # live 모드에서만 None이고 여기는 그 반대 갈래다.
            limit_price = params.apply_zone_limit_offset(limit_price, is_long=is_long)
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

        stop_price = ob.bottom if is_long else ob.top
        tp_price = (
            None
            if limit_price is None
            else _resolve_take_profit(params, is_long, limit_price, stop_price, lines)
        )

        cut = bisect.bisect_left(times, setup_substeps[0].htf_bar_time)
        rsi_state = rsi_seeder.seed(cut)
        live_limit: _IntrabarLiveLimit | None = None
        if live_band_mode:
            assert deviation is not None
            # 탭 봉 **직전까지의** 확정봉으로 시딩한다 — 탭 봉 종가를 넣는 순간 그것이
            # 곧 WAN-115가 잡아낸 룩어헤드다. 20번째 표본 자리는 현재가 몫으로 비운다.
            live_limit = _IntrabarLiveLimit(
                band=RealtimeBand.seed_from_closed(closes[:cut], deviation),
                order_block=ob,
                is_long=is_long,
                params=params,
                stop_price=stop_price,
                lines=lines,
            )
        # WAN-100 갭A: 첫 탭 면제는 `tap_index`를 아는 여기서만 판정할 수 있다 —
        # 시뮬레이터는 셋업 하나만 보므로 몇 번째 탭인지 모른다. A안
        # (`ConfluenceStrategy._evaluate_entry`)과 같은 조건이며, 병합 존이면
        # `signal.tap_index`가 이미 병합 존 기준으로 매겨져 있다(WAN-81 §5).
        first_tap_free = effective_gate_mode == "first_tap_free" and signal.tap_index == 0
        outcome = simulate_zone_limit_trade(
            direction=ob.direction,
            limit_price=limit_price,
            live_limit=live_limit,
            stop_price=stop_price,
            substeps=setup_substeps,
            rsi_state=rsi_state,
            rsi_oversold=effective_oversold,
            rsi_overbought=effective_overbought,
            take_profit_price=tp_price,
            limit_valid_bars=params.limit_valid_bars,
            invalidation_time=ob.break_time if params.use_order_block_stop else None,
            cancel_on_condition_fail=params.cancel_limit_on_condition_fail,
            stop_before_tp=params.stop_before_take_profit,
            rsi_gate_mode=effective_gate_mode,
            rsi_neutral_band=params.rsi_neutral_band,
            penetration_bps=params.fill_penetration_bps,
            first_tap_free=first_tap_free,
        )

        if not outcome.order_rested:
            # WAN-119: live 밴드가 이 셋업에 **끝까지 주문을 걸지 못했다**(워밍업이거나
            # 밴드가 줄곧 존보다 불리 = WAN-75 규칙 3). 정적 모드는 같은 셋업을 탭 봉에서
            # `continue`로 걸러내 분모(eligible)에 넣지 않으므로, 여기서도 세지 않아야
            # 체결률이 모드 간 같은 것을 잰다.
            continue

        eligible += 1
        is_filled = (
            outcome.filled and outcome.entry_time is not None and outcome.entry_price is not None
        )
        # 큐 근사(WAN-96): 낙관적 모델이 "체결"이라 본 셋업을 일정 비율로 탈락시킨다.
        # 탈락한 셋업은 거래가 되지 않으므로 단일 포지션 슬롯이 비어 다른 셋업이 그
        # 자리를 채울 수 있다 — 실거래에서 미체결이 다음 기회를 여는 것과 같다.
        is_dropped = (
            is_filled
            and dropout_rng is not None
            and dropout_rng.random() < params.fill_dropout_rate
        )
        if is_dropped:
            is_filled = False
            dropped += 1
        if setup_sink is not None:
            setup_sink.append(
                SetupDiagnostic(
                    trigger_time=signal.trigger_time,
                    tap_bar_time=tap_htf,
                    tap_close=closes[pos],
                    side=side,
                    # WAN-119 live 모드엔 상수 주문 가격이 없다 — 체결됐으면 그때 걸려
                    # 있던 실제 가격, 아니면 `None`(지어내지 않는다).
                    limit_price=limit_price if limit_price is not None else outcome.entry_price,
                    stop_price=stop_price,
                    filled=is_filled,
                    dropped=is_dropped,
                    status=outcome.status,
                    tap_index=signal.tap_index,
                    zone_key=signal.zone_key,
                )
            )
        if not is_filled or outcome.entry_time is None or outcome.entry_price is None:
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
                order_block=ob,
                tap_index=signal.tap_index,
                zone_key=signal.zone_key,
                trigger_time=signal.trigger_time,
            )
        )

    stats = ZoneLimitStats(
        eligible=eligible, filled=filled, penetrations=penetrations, dropped=dropped
    )
    return candidates, stats


def _seed_rsi(
    params: ConfluenceParams, times: list[int], closes: list[float], first_htf: int
) -> RealtimeRsi:
    """탭 봉(first_htf) **직전까지**의 확정봉 종가로 시딩한 실시간 RSI 상태."""
    cut = bisect.bisect_left(times, first_htf)
    return RealtimeRsi.seed_from_closed(closes[:cut], length=params.rsi_length)


class _IncrementalRsiSeeder:
    """시그널마다 `RealtimeRsi`를 처음부터 재시딩하던 것을 증분 커밋으로 줄인다(WAN-78).

    프로파일링 결과 `_seed_rsi`(매 시그널마다 `closes[:cut]` 전체를 0부터 재커밋)가
    셋업 많은 구간에서 전체 실행 시간의 절반 이상을 먹었다 — 신호 수만큼 O(n) 재계산이
    반복되는 O(신호수×n) 병목. 시그널은 보통 시간 오름차순(`cut` 오름차순)이지만
    보장되지는 않는다(오더블록 확정 순서 ≠ 첫 탭 순서일 수 있음). `cut`이 지금까지
    커밋한 지점 이상이면 그 접두사 상태에서 증분 커밋만 하고(정확히 `_seed_rsi`와
    동일한 최종 상태), 그 미만이면(드묾) 정확성을 위해 `_seed_rsi`로 처음부터 다시
    시딩한다 — 어느 경우든 반환값은 항상 `_seed_rsi(params, times, closes, ...)`와
    동일하다(`RealtimeRsi.commit`이 순서에 대해 결합법칙적이므로).

    `seed()`는 내부 진행 상태의 **사본**을 반환한다 — 호출자(`simulate_zone_limit_trade`)가
    반환값을 변형해도 다음 시그널의 시딩에 영향이 없어야 하기 때문이다.
    """

    def __init__(self, closes: list[float], length: int) -> None:
        self._closes = closes
        self._length = length
        self._state = RealtimeRsi(length=length)
        self._committed = 0

    def seed(self, cut: int) -> RealtimeRsi:
        if cut < self._committed:
            return RealtimeRsi.seed_from_closed(self._closes[:cut], length=self._length)
        for close in self._closes[self._committed : cut]:
            self._state.commit(close)
        self._committed = cut
        return copy.copy(self._state)


def sequence_with_candidates(
    candidates: list[_Candidate],
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None = None,
) -> list[tuple[_Candidate, Trade]]:
    """시퀀싱 + 비용 반영 결과를 **원본 셋업과 짝지어** 반환한다.

    본체는 `_sequence_and_cost`와 같고, 거래를 낸 셋업이 무엇이었는지를 함께 돌려준다 —
    사후 분석(예: WAN-104의 오프셋 증분 거래 분해)이 `Trade`를 원본 셋업·오더블록에
    조인해야 하는데, `Trade`에는 그 링크가 없기 때문이다. 진입 시각으로 역매칭하면
    같은 시각에 두 셋업이 있을 때 조용히 어긋나므로 시퀀서가 직접 짝을 알려준다.

    분해용 함수를 따로 두지 않고 시퀀싱을 **여기 한 곳에만** 두는 이유: 리포트가 같은
    규칙을 복제하면 그 복제본이 본체와 갈라진다(WAN-77의 사본은 실제로 `funding_rates`를
    빠뜨린 채 남아 있다 — 펀딩 배선이 WAN-91에서 뒤늦게 들어왔기 때문이다).
    """
    ordered = sorted(candidates, key=lambda c: (c.entry_time, c.exit_time))
    cash = cfg.initial_capital
    busy_until = -1
    paired: list[tuple[_Candidate, Trade]] = []
    for cand in ordered:
        if cand.entry_time < busy_until:
            continue
        trade = _to_trade(cand, cash, cfg, funding_rates)
        if trade is None:
            continue
        cash += trade.realized_pnl
        busy_until = cand.exit_time
        paired.append((cand, trade))
    return paired


def _sequence_and_cost(
    candidates: list[_Candidate],
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None = None,
) -> list[Trade]:
    """동시 1포지션 제약으로 셋업을 시간순 배치하고 비용 모델로 `Trade`를 만든다.

    진입 시각 오름차순으로 훑으며, 직전 포지션의 청산 시각 이후에 진입하는 셋업만
    채택한다(겹치면 스킵 — WAN-23의 단일 포지션 규칙). 사이징 자본은 A안 엔진과
    동일하게 진행 중 현금을 쓴다. `funding_rates`를 주면 보유 구간 펀딩비를 A안
    엔진과 같은 산식으로 손익에서 차감한다(WAN-95).
    """
    return [trade for _, trade in sequence_with_candidates(candidates, cfg, funding_rates)]


def _to_trade(
    cand: _Candidate,
    equity: float,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None = None,
    open_notional: float = 0.0,
) -> Trade | None:
    """공용 비용 모델로 셋업을 `Trade`로 변환한다(WAN-37).

    B안은 **지정가(메이커) 진입**이므로 진입에는 슬리피지가 붙지 않고 메이커 수수료가
    적용된다. 청산은 손절·익절 도달 시 시장가 성격이라 테이커(수수료+슬리피지)로 본다.
    이 비대칭이 A안(시장가=테이커 진입)과의 공정한 비교의 핵심이다.

    보유 구간 `[진입시각, 청산시각)`의 펀딩비는 A안 엔진(`BacktestEngine._funding_cost`)
    과 동일하게 진입 명목가 기준으로 산출해 실현손익에서 뺀다(WAN-95). B안은 부분청산이
    없어 구간 분할이 필요 없다.

    `open_notional`(WAN-103)은 이미 열린 포지션들의 명목 합이다. 명목 상한이 포트폴리오
    전체에 걸리므로 사이징이 그 여유분만 새 포지션에 배정한다 — 동시 1포지션 경로는
    항상 0을 넘기므로 예전과 동일하다.
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
            open_notional=open_notional,
        )
        if qty <= 0.0:
            return None
    else:
        # 고정 비율 사이징에는 레버리지가 없다(`position_size`를 타지 않으므로 clamp도 없다).
        # 포트폴리오 실행에서 명목 상한을 거는 건 시퀀서 쪽 `_notional_cap`이며, 여기서
        # 또 다른 규칙으로 clamp하면 두 상한이 갈라진다 — 그래서 이 경로는 `open_notional`을
        # 보지 않는다(WAN-65가 경고하는 대로 이 모드 자체가 리스크 정규화되지 않은 경로다).
        qty = (equity * cfg.position_fraction) / entry_fill
    entry_notional = entry_fill * qty
    entry_fee = costs.fee(entry_notional, Liquidity.MAKER)

    exit_fill = costs.exit_fill(cand.exit_price, is_long=is_long, liquidity=Liquidity.TAKER)
    exit_fee = costs.fee(exit_fill * qty, Liquidity.TAKER)
    gross = side.sign * (exit_fill - entry_fill) * qty
    funding_cost = _funding_cost_for(cand, entry_notional, cfg, funding_rates)
    realized = gross - entry_fee - exit_fee - funding_cost
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
        funding_cost=funding_cost,
        realized_pnl=realized,
        return_pct=realized / entry_notional if entry_notional else 0.0,
    )


def _funding_cost_for(
    cand: _Candidate,
    entry_notional: float,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
) -> float:
    """셋업 보유 구간의 누적 펀딩비용(양수=지불, 음수=수취). 미사용/무데이터면 0."""
    if not cfg.funding_enabled or not funding_rates:
        return 0.0
    direction: Direction = "long" if cand.side is PositionSide.LONG else "short"
    return cumulative_funding_cost(
        funding_rates,
        position_notional=entry_notional,
        direction=direction,
        start_ms=cand.entry_time,
        end_ms=cand.exit_time,
        include_predicted=cfg.funding_include_predicted,
    )


def build_result_from_trades(
    trades: list[Trade],
    cfg: BacktestConfig,
    timeframe: str,
    *,
    funding_coverage_value: float | None = None,
) -> BacktestResult:
    """시간순 `Trade` 리스트로 자본곡선·지표를 만들어 `BacktestResult`를 낸다.

    자본곡선은 각 거래의 청산 시각에 실현손익을 순차 반영한 점들로 구성한다(진입
    시작점 포함). MDD·샤프는 이 거래 단위 곡선에서 산출한다.

    `funding_coverage_value`는 결과에 그대로 실어 "펀딩을 얼마나 반영했는지"가
    리포트에 드러나게 한다(WAN-63/WAN-95).
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
        funding_coverage=funding_coverage_value,
    )
    return BacktestResult(config=cfg, trades=ordered, equity_curve=curve, metrics=metrics)


def _empty_result(cfg: BacktestConfig) -> BacktestResult:
    metrics = build_metrics(
        initial_capital=cfg.initial_capital,
        equities=[cfg.initial_capital],
        trades=[],
    )
    return BacktestResult(config=cfg, trades=[], equity_curve=[], metrics=metrics)
