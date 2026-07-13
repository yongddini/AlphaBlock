"""페이퍼 vs 백테스트 패리티 리포트 (WAN-33).

"페이퍼로 실제 낸 거래"와 "같은 기간·시리즈를 백테스트로 재실행한 거래"를 나란히
집계해, 거래 수·승률·평균 R·총수익률의 차이를 수치로 드러낸다. 차이가 임계값을
넘는 시리즈는 `flagged`로 표시해 조사를 유도한다.

## 왜 차이가 나는가

페이퍼 러너(WAN-25)와 백테스트(WAN-8)는 **같은 전략**(WAN-23 컨플루언스)을 쓰지만,
러너는 실시간으로 도착하는(때로는 지연·결측된) 데이터를 폴링하고 프라이밍 이후의
신호만 처리한다. 백테스트는 완결된 과거 데이터를 한 번에 평가한다. 이 리포트는 그
실행 격차를 정량화한다. 손익 비용(수수료·펀딩비, WAN-20)은 양쪽에 동일하게 적용해,
남는 차이가 **거래 선택·체결 타이밍**에서 비롯되도록 한다.

## 구성

- `backtest_trade_stats()` / `build_parity_row()` — 순수 비교 코어(주입식, 테스트 용이).
- `build_parity_report()` — 저장소(OHLCV·펀딩·페이퍼 거래)에서 읽어 시리즈별로
  백테스트를 재실행하고 리포트를 조립하는 오케스트레이터.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, BacktestResult
from common.costs import Liquidity
from config.settings import Settings, get_settings
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from paper.performance import PerfMetrics, TradeStat, compute_metrics, record_to_stat
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.confluence import ConfluenceStrategy
from strategy.models import (
    ConfluenceParams,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockSignal,
)


class ParityThresholds(BaseModel):
    """시리즈를 `flagged`로 볼 차이 임계값. 하나라도 넘으면 표시한다."""

    model_config = ConfigDict(frozen=True)

    max_trade_count_diff: int = 2
    """|페이퍼 거래 수 − 백테스트 거래 수| 허용치."""
    max_win_rate_diff: float = 0.15
    """|승률 차| 허용치(분수). 예: 0.15 = 15%p."""
    max_avg_r_diff: float = 0.5
    """|평균 R 차| 허용치. 한쪽이라도 평균 R이 없으면 이 축은 판정에서 제외."""


class ParityRow(BaseModel):
    """한 시리즈(심볼·TF)의 페이퍼 vs 백테스트 비교 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    paper: PerfMetrics
    backtest: PerfMetrics
    trade_count_diff: int
    """페이퍼 거래 수 − 백테스트 거래 수."""
    win_rate_diff: float
    """페이퍼 승률 − 백테스트 승률(분수)."""
    avg_r_diff: float | None
    """페이퍼 평균 R − 백테스트 평균 R. 한쪽이라도 없으면 None."""
    total_return_diff: float
    """페이퍼 총수익률 − 백테스트 총수익률(%)."""
    flagged: bool
    """임계값을 넘는 불일치가 있어 조사가 필요한지 여부."""


class ParityReport(BaseModel):
    """`build_parity_report()`의 반환값. 시리즈별 비교 행과 임계값을 담는다."""

    model_config = ConfigDict(frozen=True)

    rows: list[ParityRow]
    thresholds: ParityThresholds

    @property
    def flagged_rows(self) -> list[ParityRow]:
        """불일치가 큰 시리즈만."""
        return [r for r in self.rows if r.flagged]

    def to_dataframe(self) -> pd.DataFrame:
        """비교 행을 DataFrame으로(핵심 지표 + 차이)."""
        records = [
            {
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "paper_trades": r.paper.num_trades,
                "backtest_trades": r.backtest.num_trades,
                "trade_count_diff": r.trade_count_diff,
                "paper_win_rate": r.paper.win_rate,
                "backtest_win_rate": r.backtest.win_rate,
                "win_rate_diff": r.win_rate_diff,
                "paper_avg_r": r.paper.avg_r,
                "backtest_avg_r": r.backtest.avg_r,
                "avg_r_diff": r.avg_r_diff,
                "paper_total_return_pct": r.paper.total_return_pct,
                "backtest_total_return_pct": r.backtest.total_return_pct,
                "total_return_diff": r.total_return_diff,
                "flagged": r.flagged,
            }
            for r in self.rows
        ]
        return pd.DataFrame(records, columns=_ROW_COLUMNS)

    def to_table(self) -> str:
        """콘솔용 비교표 문자열."""
        header = (
            f"{'symbol':<16} {'tf':>4} {'p_trd':>5} {'b_trd':>5} {'Δtrd':>5} "
            f"{'p_win%':>7} {'b_win%':>7} {'p_R':>6} {'b_R':>6} {'ΔR':>6} {'flag':>4}"
        )
        lines = ["=== Paper vs Backtest Parity ===", header, "-" * len(header)]
        for r in self.rows:
            p_r = "N/A" if r.paper.avg_r is None else f"{r.paper.avg_r:.2f}"
            b_r = "N/A" if r.backtest.avg_r is None else f"{r.backtest.avg_r:.2f}"
            d_r = "N/A" if r.avg_r_diff is None else f"{r.avg_r_diff:+.2f}"
            lines.append(
                f"{r.symbol:<16} {r.timeframe:>4} "
                f"{r.paper.num_trades:>5} {r.backtest.num_trades:>5} "
                f"{r.trade_count_diff:>+5} "
                f"{r.paper.win_rate * 100:>7.1f} {r.backtest.win_rate * 100:>7.1f} "
                f"{p_r:>6} {b_r:>6} {d_r:>6} {'⚠' if r.flagged else '':>4}"
            )
        return "\n".join(lines)


_ROW_COLUMNS = [
    "symbol",
    "timeframe",
    "paper_trades",
    "backtest_trades",
    "trade_count_diff",
    "paper_win_rate",
    "backtest_win_rate",
    "win_rate_diff",
    "paper_avg_r",
    "backtest_avg_r",
    "avg_r_diff",
    "paper_total_return_pct",
    "backtest_total_return_pct",
    "total_return_diff",
    "flagged",
]


def _stop_reference(signal: OrderBlockSignal) -> float | None:
    """진입 근거 오더블록의 손절 참조가(롱=존 하단, 숏=존 상단). 없으면 None."""
    ob = signal.order_block
    if ob is None:
        return None
    return ob.bottom if signal.direction is OrderBlockDirection.BULLISH else ob.top


def backtest_trade_stats(
    result: BacktestResult, signals: Sequence[OrderBlockSignal]
) -> list[TradeStat]:
    """백테스트 거래를 페이퍼와 같은 `TradeStat`(순손익률·R·청산시각)으로 변환한다.

    R 배수는 진입가와 진입 근거 오더블록의 손절 참조가로 리스크를 역산해 구한다
    (페이퍼의 `risk_pct` 정의와 동일: `net_pct / risk_pct`).
    """
    stop_by_time: dict[int, float] = {}
    for sig in signals:
        stop = _stop_reference(sig)
        if stop is not None:
            stop_by_time[sig.trigger_time] = stop

    stats: list[TradeStat] = []
    for trade in result.trades:
        net_pct = trade.return_pct * 100.0
        r_multiple: float | None = None
        stop = stop_by_time.get(trade.entry_time)
        if stop is not None and trade.entry_price > 0.0:
            risk_frac = abs(trade.entry_price - stop) / trade.entry_price
            if risk_frac > 0.0:
                r_multiple = trade.return_pct / risk_frac
        stats.append(TradeStat(net_pct=net_pct, r_multiple=r_multiple, exit_time=trade.exit_time))
    return stats


def build_parity_row(
    *,
    symbol: str,
    timeframe: str,
    paper_records: Sequence[PaperTradeRecord],
    backtest_stats: Sequence[TradeStat],
    thresholds: ParityThresholds,
) -> ParityRow:
    """페이퍼 거래와 백테스트 통계를 같은 지표로 집계하고 차이를 판정한다."""
    paper = compute_metrics(record_to_stat(r) for r in paper_records)
    backtest = compute_metrics(backtest_stats)

    trade_count_diff = paper.num_trades - backtest.num_trades
    win_rate_diff = paper.win_rate - backtest.win_rate
    total_return_diff = paper.total_return_pct - backtest.total_return_pct
    avg_r_diff = (
        None if paper.avg_r is None or backtest.avg_r is None else paper.avg_r - backtest.avg_r
    )

    flagged = (
        abs(trade_count_diff) > thresholds.max_trade_count_diff
        or abs(win_rate_diff) > thresholds.max_win_rate_diff
        or (avg_r_diff is not None and abs(avg_r_diff) > thresholds.max_avg_r_diff)
    )

    return ParityRow(
        symbol=symbol,
        timeframe=timeframe,
        paper=paper,
        backtest=backtest,
        trade_count_diff=trade_count_diff,
        win_rate_diff=win_rate_diff,
        avg_r_diff=avg_r_diff,
        total_return_diff=total_return_diff,
        flagged=flagged,
    )


# 타입 별칭: OHLCV/펀딩 로더(주입식 — 테스트에서 저장소 없이 대체 가능).
OhlcvLoader = Callable[[str, str, int | None, int | None], pd.DataFrame]
FundingLoader = Callable[[str, int | None, int | None], list[FundingRate]]


def _default_ohlcv_loader(db_path: str) -> OhlcvLoader:
    def load(symbol: str, timeframe: str, start_ms: int | None, end_ms: int | None) -> pd.DataFrame:
        with OhlcvStore(db_path) as store:
            return store.load(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)

    return load


def _default_funding_loader(db_path: str, *, include_predicted: bool) -> FundingLoader:
    def load(symbol: str, start_ms: int | None, end_ms: int | None) -> list[FundingRate]:
        with FundingRateStore(db_path) as store:
            return store.get_rates(
                symbol, start_ms=start_ms, end_ms=end_ms, include_predicted=include_predicted
            )

    return load


def run_series_backtest(
    df: pd.DataFrame,
    *,
    confluence_params: ConfluenceParams | None,
    order_block_params: OrderBlockParams | None,
    backtest_config: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
) -> tuple[BacktestResult, list[OrderBlockSignal]]:
    """컨플루언스 전략을 재실행해 (백테스트 결과, 확정 진입 시그널)을 반환한다.

    시그널을 함께 돌려주는 이유는 R 배수 산출(손절 참조가)이 시그널의 오더블록을
    필요로 하기 때문이다.
    """
    strategy = ConfluenceStrategy(confluence_params, order_block_params)
    confluence = strategy.run(df)
    signals = confluence.order_block_signals
    result = BacktestEngine(backtest_config).run(df, signals, funding_rates)
    return result, signals


def build_parity_report(
    db_path: str,
    *,
    settings: Settings | None = None,
    series: Sequence[tuple[str, str]] | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    thresholds: ParityThresholds | None = None,
    ohlcv_loader: OhlcvLoader | None = None,
    funding_loader: FundingLoader | None = None,
) -> ParityReport:
    """저장소에서 읽어 시리즈별 페이퍼 vs 백테스트 패리티 리포트를 만든다.

    `series`가 None이면 페이퍼 거래가 저장된 시리즈를 대상으로 한다. 각 시리즈에 대해
    저장된 페이퍼 거래(진입시각이 `[start_ms, end_ms)`)와, 같은 심볼·TF·기간의 OHLCV를
    백테스트로 재실행한 결과를 비교한다. 손익 비용(수수료·슬리피지·펀딩비)은 페이퍼
    기록과 **같은 공용 비용 모델**(`settings.costs`, WAN-37)·`settings.funding_enabled`를
    써서, 남는 차이가 비용이 아니라 거래 선택·체결 타이밍에서만 비롯되게 한다.

    `ohlcv_loader`/`funding_loader`를 주입하면 저장소 없이도(테스트) 동작한다.
    """
    settings = settings or get_settings()
    thresholds = thresholds or ParityThresholds()
    confluence_params = confluence_params or settings.confluence
    ohlcv_loader = ohlcv_loader or _default_ohlcv_loader(db_path)

    funding_enabled = settings.funding_enabled
    if funding_loader is None and funding_enabled:
        funding_loader = _default_funding_loader(db_path, include_predicted=False)

    with PaperTradeStore(db_path) as paper_store:
        target_series = list(series) if series is not None else paper_store.list_series()
        records_by_series = {
            (symbol, timeframe): paper_store.list_records(
                symbol, timeframe, start_ms=start_ms, end_ms=end_ms
            )
            for symbol, timeframe in target_series
        }

    # 페이퍼 기록과 같은 공용 비용 모델을 백테스트에도 싣는다(WAN-37). 페이퍼 러너의
    # 진입/청산은 시장가라 A안(taker) 진입으로 재실행한다 — 그래야 슬리피지·수수료가
    # 페이퍼 기록과 동일 산식으로 적용돼 비용 차이가 상쇄된다.
    costs = settings.costs
    backtest_config = BacktestConfig(
        fee_rate=costs.taker_fee_rate,
        maker_fee_rate=costs.maker_fee_rate,
        slippage=costs.slippage_fraction,
        entry_liquidity=Liquidity.TAKER,
        funding_enabled=funding_enabled,
        funding_missing_policy="zero",
    )

    rows: list[ParityRow] = []
    for symbol, timeframe in target_series:
        paper_records = records_by_series[(symbol, timeframe)]
        df = ohlcv_loader(symbol, timeframe, start_ms, end_ms)
        funding_rates: list[FundingRate] | None = None
        if funding_enabled and funding_loader is not None:
            funding_rates = funding_loader(symbol, start_ms, end_ms)

        if df.empty:
            backtest_stats: list[TradeStat] = []
        else:
            result, signals = run_series_backtest(
                df,
                confluence_params=confluence_params,
                order_block_params=order_block_params,
                backtest_config=backtest_config,
                funding_rates=funding_rates,
            )
            backtest_stats = backtest_trade_stats(result, signals)

        rows.append(
            build_parity_row(
                symbol=symbol,
                timeframe=timeframe,
                paper_records=paper_records,
                backtest_stats=backtest_stats,
                thresholds=thresholds,
            )
        )

    return ParityReport(rows=rows, thresholds=thresholds)
