"""WAN-103: 동시 다중 포지션 + 포트폴리오 레버리지 — 레버리지 스윕·단일 포지션 대조.

동시 1포지션 제약(WAN-23)을 풀면 무엇이 회수되고 그 대가가 무엇인지 낸다. 설계 결정과
결론 문장은 [`docs/decisions/wan103.md`](../docs/decisions/wan103.md)에 있고, 이 모듈은
**숫자를 내는 곳**이다.

## 왜 범용 CLI(`backtest.run`)가 아니라 리포트 모듈인가 (WAN-101 규칙)

CLI로는 답이 안 나오는 것을 묻기 때문이다: 동시 겹침(peak concurrency)은 격자 한 셀의
성과 지표가 아니라 **실행 도중의 상태**이고, "역대 최대 겹침 N을 IS에서 재서 OOS에
적용"은 셀 사이에 의존이 있는 2단계 절차다. 골격(데이터 로딩·구간 분할·렌더)은 그대로
`backtest.harness`에서 가져다 쓴다.

## 두 범위

* **series** — (심볼, TF) 시리즈 하나가 자기 자본으로 도는 현행 백테스트 조건. 단일
  포지션 대조군이 채택 엔진 그 자체라, 이 표의 두 열 차이는 **오직 포지션 제약**이다.
* **pooled** — 유니버스 전체(여러 심볼·TF)가 자본 하나를 공유하는 라이브 조건(WAN-83의
  global 범위). peak concurrency와 N은 **유니버스에 전적으로 의존**하므로 두 유니버스를
  따로 낸다: `1h_3sym`(작업 TF 단독)와 `multi_tf`(15m/1h/4h — 15m은 WAN-107 계류 중이라
  대조용).

## 재현

```
uv run python -m backtest.wan103_portfolio_leverage_report
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    DEFAULT_YEARS,
    MarketData,
    Segment,
    detect_order_blocks,
    load_market_data,
    segments_for,
    slice_market,
)
from backtest.models import BacktestConfig, Trade
from backtest.portfolio import C, PortfolioParams, PortfolioStats, ToTrade, sequence_portfolio
from backtest.sweep import default_backtest_config
from backtest.zone_limit_backtest import (
    _Candidate,
    _to_trade,
    apply_portfolio_leverage,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.models import ConfluenceParams

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")

#: 채택 기본값 그대로. 이 리포트는 전략 파라미터를 하나도 바꾸지 않는다 — 묻는 건
#: "지금 기본값에서 포지션 제약을 풀면 무엇이 달라지나"다.
PARAMS = ConfluenceParams()

#: 고정 레버리지 스윕 축(이슈 완료기준). N배(= peak concurrency)는 여기 없고, 실행 중
#: 계측한 값을 대입해 **별도 시나리오**로 돈다.
FIXED_LEVERAGES: tuple[float, ...] = (1.0, 2.0, 3.0)

SCENARIO_SINGLE = "single"
SCENARIO_PEAK = "peak_N"

SCOPE_SERIES = "series"
SCOPE_POOLED = "pooled"

#: pooled 유니버스 정의. peak concurrency는 유니버스에 전적으로 의존하므로 못박아 둔다.
UNIVERSES: dict[str, tuple[str, ...]] = {
    "1h_3sym": ("1h",),
    "multi_tf": ("15m", "1h", "4h"),
}

#: series 범위에서 돌 TF. WAN-97 채택 TF(1h 작업 TF·4h 보류)와 15m(WAN-107 계류) 대조.
SERIES_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")

#: 겹침을 잴 때 쓰는 "사실상 무제한" 레버리지. 자연 수요(명목 상한이 없었다면 몇 개가
#: 겹쳤겠나)를 재려면 상한이 후보를 거르지 않아야 한다 — 실측 최대 명목 배수가 3.4배
#: 수준이라 1000배면 어떤 진입도 상한에 닿지 않는다.
UNCAPPED_LEVERAGE = 1000.0


@dataclass(frozen=True)
class _Tagged:
    """pooled 실행용 후보 래퍼 — 원본 셋업에 시리즈 신원을 붙인다.

    두 가지를 해결한다. (1) `zone_key`는 봉 시각의 집합이라 **심볼이 다르면 같은 값이
    나올 수 있다** — 그대로 pooled에 넣으면 BTC의 존과 ETH의 존이 같은 존으로 취급돼
    `one_per_zone`이 엉뚱하게 막는다. 여기서 시리즈를 섞어 유일하게 만든다. (2) 펀딩비는
    심볼마다 다르므로 후보가 자기 심볼의 요율을 들고 다녀야 한다.

    `backtest.portfolio.CandidateLike`를 구조적으로 만족하므로 시퀀서는 이 래퍼를
    그대로 받는다.
    """

    inner: _Candidate
    symbol: str
    timeframe: str
    rates: tuple[FundingRate, ...]

    @property
    def entry_time(self) -> int:
        return self.inner.entry_time

    @property
    def entry_price(self) -> float:
        return self.inner.entry_price

    @property
    def exit_time(self) -> int:
        return self.inner.exit_time

    @property
    def stop_price(self) -> float:
        return self.inner.stop_price

    @property
    def zone_key(self) -> frozenset[int] | None:
        """시리즈로 네임스페이스한 존 식별자. 원본이 None이면 None(존 제약 미적용)."""
        if self.inner.zone_key is None:
            return None
        tag = hash((self.symbol, self.timeframe))
        return frozenset({v ^ tag for v in self.inner.zone_key})


def _tagged_to_trade(
    cand: _Tagged,
    equity: float,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    open_notional: float,
) -> Trade | None:
    """pooled 후보를 거래로. 펀딩은 **후보가 들고 온 자기 심볼의 요율**을 쓴다.

    시퀀서가 넘기는 `funding_rates`(유니버스 공용)는 여기서 무시된다 — 여러 심볼이 한
    포트폴리오에 섞여 있으므로 공용 요율을 쓰면 BTC 포지션에 ETH 펀딩을 물릴 수 있다.
    """
    return _to_trade(cand.inner, equity, cfg, cand.rates, open_notional)


class ConcurrencyRow(BaseModel):
    """겹침 분포 한 칸 — "동시 k개였던 시간이 전체의 몇 %인가" (사용자 요청 산출 1).

    명목 상한 없이(자연 수요) 잰 값이다. `peak_time`은 최대 겹침이 발생한 시각으로,
    N이 언제 나온 값인지(= 어떤 시장 국면의 산물인지) 추적할 수 있게 남긴다.
    """

    model_config = ConfigDict(frozen=True)

    universe: str
    segment: str
    concurrency: int
    time_share: float
    peak_concurrency: int
    peak_time: int | None


CONCURRENCY_COLUMNS: tuple[str, ...] = tuple(ConcurrencyRow.model_fields)


class PortfolioRow(BaseModel):
    """결과 표 한 행 — 좌표(범위·유니버스·구간·시나리오)와 성과·위험을 같은 줄에."""

    model_config = ConfigDict(frozen=True)

    scope: str
    universe: str
    symbol: str
    timeframe: str
    segment: str
    scenario: str
    leverage: float
    num_trades: int
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float | None
    peak_concurrency: int
    #: 아래 진단은 포트폴리오 시퀀서만 낸다. `single` 행(= 채택 엔진)은 그 시퀀서를 타지
    #: 않으므로 **None = 계측 안 함**이다. 0으로 채우면 "겹친 리스크가 0이었다"는 사실
    #: 주장이 되는데, 그건 재지 않은 값이다(단일 포지션의 실제 동시 리스크는 1%다).
    max_open_notional_ratio: float | None
    max_concurrent_risk_ratio: float | None
    clamped_entries: int | None
    skipped_notional: int | None
    skipped_zone: int | None
    liquidations: int | None


ROW_COLUMNS: tuple[str, ...] = tuple(PortfolioRow.model_fields)


@dataclass(frozen=True)
class _Cell:
    """한 (심볼, TF, 구간)의 후보 풀 — 레버리지 축을 돌 때 **재사용**한다.

    후보 생성(오더블록 탐지 + 1분 서브스텝 시뮬레이션)이 실행 시간의 거의 전부인데,
    레버리지는 후보를 바꾸지 않는다(`build_zone_limit_candidates`는 사이징을 보지 않는다).
    그래서 셀당 한 번만 만들고 시나리오마다 시퀀싱만 다시 한다.
    """

    symbol: str
    timeframe: str
    segment: str
    candidates: list[_Candidate]
    rates: tuple[FundingRate, ...]
    cfg: BacktestConfig


def build_cell(symbol: str, timeframe: str, market: MarketData, segment: Segment) -> _Cell | None:
    window = slice_market(market, segment)
    if window.empty or window.df_1m.empty:
        return None
    cfg = default_backtest_config(timeframe)
    candidates, _stats = build_zone_limit_candidates(
        window.htf_df,
        window.df_1m,
        timeframe,
        params=PARAMS,
        cfg=cfg,
        order_block_result=detect_order_blocks(window),
    )
    return _Cell(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment.name,
        candidates=candidates,
        rates=tuple(window.funding_rates),
        cfg=cfg,
    )


def _metrics_row(
    *,
    scope: str,
    universe: str,
    symbol: str,
    timeframe: str,
    segment: str,
    scenario: str,
    leverage: float,
    trades: list[Trade],
    cfg: BacktestConfig,
    stats: PortfolioStats | None,
) -> PortfolioRow:
    result = build_result_from_trades(trades, cfg, timeframe)
    m = result.metrics
    return PortfolioRow(
        scope=scope,
        universe=universe,
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        scenario=scenario,
        leverage=leverage,
        num_trades=m.num_trades,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        win_rate=m.win_rate,
        sharpe=m.sharpe,
        # `single`은 정의상 동시 1포지션이라 peak=1이 계측이 아니라 사실이다.
        peak_concurrency=stats.peak_concurrency if stats else 1,
        max_open_notional_ratio=stats.max_open_notional_ratio if stats else None,
        max_concurrent_risk_ratio=stats.max_concurrent_risk_ratio if stats else None,
        clamped_entries=stats.clamped_entries if stats else None,
        skipped_notional=stats.skipped_notional if stats else None,
        skipped_zone=stats.skipped_zone if stats else None,
        liquidations=len(stats.liquidations) if stats else None,
    )


def run_scenario(
    candidates: Sequence[C],
    cfg: BacktestConfig,
    to_trade: ToTrade[C],
    *,
    leverage: float,
    rates: Sequence[FundingRate] | None = None,
) -> tuple[list[Trade], PortfolioStats, BacktestConfig]:
    """한 레버리지로 후보를 배치한다. 레버리지는 사이징에도 함께 실어야 한다.

    `PortfolioParams.leverage`만 바꾸고 `cfg.risk_sizing.leverage`를 그대로 두면, 시퀀서는
    새 상한을 쓰는데 사이징은 옛 상한으로 clamp한다 — 두 곳이 갈라지는 그 순간 결과는
    "어느 레버리지로 돈 것도 아닌" 값이 된다. `apply_portfolio_leverage`가 그 둘을 묶는다.
    """
    portfolio = PortfolioParams(leverage=leverage)
    scenario_cfg = apply_portfolio_leverage(cfg, portfolio)
    paired, stats = sequence_portfolio(
        candidates,
        scenario_cfg,
        to_trade,
        portfolio=portfolio,
        funding_rates=rates,
    )
    return [trade for _, trade in paired], stats, scenario_cfg


def measure_concurrency(
    candidates: Sequence[C],
    cfg: BacktestConfig,
    to_trade: ToTrade[C],
    rates: Sequence[FundingRate] | None = None,
) -> PortfolioStats:
    """명목 상한이 없을 때의 자연 겹침 (사용자 요청: "역대 최대 겹침"과 그 분포).

    상한을 걸어 둔 채로 재면 상한이 진입을 스킵시켜 N이 작게 나온다 — 그건 "겹칠 수
    있었던 수"가 아니라 "상한이 허락한 수"다. N과 k별 시간 비중은 **같은 실행**에서
    나와야 서로 모순되지 않는다.
    """
    _trades, stats, _cfg = run_scenario(
        candidates, cfg, to_trade, leverage=UNCAPPED_LEVERAGE, rates=rates
    )
    return stats


def measure_peak(
    candidates: Sequence[C],
    cfg: BacktestConfig,
    to_trade: ToTrade[C],
    rates: Sequence[FundingRate] | None = None,
) -> int:
    """`measure_concurrency`의 최댓값만."""
    return measure_concurrency(candidates, cfg, to_trade, rates).peak_concurrency


def _scenarios(peak: int) -> list[tuple[str, float]]:
    """돌 시나리오 목록 — 고정 1/2/3배 + N배(= peak concurrency).

    N이 고정 축과 겹치면 `peak_N` 행을 따로 내지 않고 그 레버리지 행이 곧 N배다 —
    같은 숫자를 두 줄로 내면 표가 "다른 실행"처럼 읽힌다.
    """
    scenarios = [(f"lev_{lev:g}", lev) for lev in FIXED_LEVERAGES]
    if peak >= 1 and float(peak) not in FIXED_LEVERAGES:
        scenarios.append((SCENARIO_PEAK, float(peak)))
    return scenarios


def series_rows(cell: _Cell) -> list[PortfolioRow]:
    """한 (심볼, TF, 구간)의 단일 포지션 대조군 + 레버리지 스윕."""
    rows: list[PortfolioRow] = []

    # 대조군: 채택 엔진 그 자체(동시 1포지션). 포트폴리오 시퀀서로 흉내 내지 않는다 —
    # 그러면 존 제약·명목 상한이 섞여 "포지션 제약만의 효과"가 아니게 된다.
    single = [
        trade for _, trade in sequence_with_candidates(cell.candidates, cell.cfg, list(cell.rates))
    ]
    rows.append(
        _metrics_row(
            scope=SCOPE_SERIES,
            universe=cell.timeframe,
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            segment=cell.segment,
            scenario=SCENARIO_SINGLE,
            leverage=1.0,
            trades=single,
            cfg=cell.cfg,
            stats=None,
        )
    )

    peak = measure_peak(cell.candidates, cell.cfg, _to_trade, list(cell.rates))
    for name, leverage in _scenarios(peak):
        trades, stats, cfg = run_scenario(
            cell.candidates, cell.cfg, _to_trade, leverage=leverage, rates=list(cell.rates)
        )
        rows.append(
            _metrics_row(
                scope=SCOPE_SERIES,
                universe=cell.timeframe,
                symbol=cell.symbol,
                timeframe=cell.timeframe,
                segment=cell.segment,
                scenario=name,
                leverage=leverage,
                trades=trades,
                cfg=cfg,
                stats=stats,
            )
        )
    return rows


def _pool(cells: Sequence[_Cell]) -> list[_Tagged]:
    """여러 시리즈의 후보를 하나의 포트폴리오 풀로 — 존 식별자를 시리즈로 네임스페이스한다."""
    return [
        _Tagged(inner=cand, symbol=cell.symbol, timeframe=cell.timeframe, rates=cell.rates)
        for cell in cells
        for cand in cell.candidates
    ]


def pooled_rows(
    universe: str, cells: Sequence[_Cell], segment: str, *, peak_override: int | None = None
) -> tuple[list[PortfolioRow], list[ConcurrencyRow], int]:
    """유니버스 전체가 자본 하나를 공유하는 라이브 조건의 행들. (성과행, 겹침분포, N)을 낸다.

    `peak_override`를 주면 그 N으로 `peak_N` 시나리오를 돈다 — IS에서 고른 N을 OOS에
    적용하는 검증(사용자 요청: "IS에서 정한 N을 OOS에 적용했을 때 청산이 나는지")에 쓴다.
    """
    if not cells:
        return [], [], 0
    pool = _pool(cells)
    cfg = cells[0].cfg
    rows: list[PortfolioRow] = []

    # pooled 대조군: 유니버스 전체가 포지션 1개를 공유(WAN-83 global 범위 = 라이브 조건).
    # `max_concurrent=1`이 곧 동시 1포지션 규칙이라 시퀀서 하나로 대조군까지 낼 수 있다.
    portfolio = PortfolioParams(leverage=1.0, max_concurrent=1)
    scenario_cfg = apply_portfolio_leverage(cfg, portfolio)
    paired, stats = sequence_portfolio(pool, scenario_cfg, _tagged_to_trade, portfolio=portfolio)
    rows.append(
        _metrics_row(
            scope=SCOPE_POOLED,
            universe=universe,
            symbol="ALL",
            timeframe="ALL",
            segment=segment,
            scenario=SCENARIO_SINGLE,
            leverage=1.0,
            trades=[t for _, t in paired],
            cfg=scenario_cfg,
            stats=stats,
        )
    )

    natural = measure_concurrency(pool, cfg, _tagged_to_trade)
    measured = natural.peak_concurrency
    peak = peak_override if peak_override is not None else measured
    concurrency = [
        ConcurrencyRow(
            universe=universe,
            segment=segment,
            concurrency=k,
            time_share=natural.time_share(k),
            peak_concurrency=measured,
            peak_time=natural.peak_concurrency_time,
        )
        for k in sorted(natural.concurrency_histogram)
    ]
    for name, leverage in _scenarios(peak):
        trades, sc_stats, sc_cfg = run_scenario(pool, cfg, _tagged_to_trade, leverage=leverage)
        rows.append(
            _metrics_row(
                scope=SCOPE_POOLED,
                universe=universe,
                symbol="ALL",
                timeframe="ALL",
                segment=segment,
                scenario=name,
                leverage=leverage,
                trades=trades,
                cfg=sc_cfg,
                stats=sc_stats,
            )
        )
    return rows, concurrency, measured


def run_report(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    years: float = DEFAULT_YEARS,
    *,
    log: bool = True,
) -> tuple[list[PortfolioRow], list[ConcurrencyRow]]:
    """series + pooled 두 범위의 전체 격자를 돈다.

    셀(후보 풀)은 (심볼, TF, 구간)마다 **한 번만** 만들고 series·pooled·모든 레버리지가
    공유한다 — 후보 생성이 실행 시간의 거의 전부라 시나리오마다 다시 만들면 격자가
    몇십 배로 불어난다.
    """
    segments = [s for s in segments_for(oos=True) if s.name != "full"]
    timeframes = sorted({tf for tfs in UNIVERSES.values() for tf in tfs} | set(SERIES_TIMEFRAMES))

    cells: dict[tuple[str, str, str], _Cell] = {}
    for symbol in symbols:
        for timeframe in timeframes:
            market = load_market_data(symbol, timeframe, years=years, need_1m=True)
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan103] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
                continue
            for segment in segments:
                cell = build_cell(symbol, timeframe, market, segment)
                if cell is not None:
                    cells[(symbol, timeframe, segment.name)] = cell
            if log:
                print(f"[wan103] {symbol} {timeframe}: 후보 생성 완료")

    rows: list[PortfolioRow] = []
    for (symbol, timeframe, segment_name), cell in cells.items():
        if timeframe not in SERIES_TIMEFRAMES:
            continue
        rows.extend(series_rows(cell))
        if log:
            print(f"[wan103] series {symbol} {timeframe} {segment_name}: 완료")

    concurrency: list[ConcurrencyRow] = []
    for universe, tfs in UNIVERSES.items():
        # ⚠️ N은 IS에서 고른다. OOS에서 잰 N을 OOS에 쓰면 그 자체가 look-ahead다 —
        # "미래에 N+1개가 겹치면 청산"이라는 위험이 정의상 사라져 검증이 무의미해진다.
        is_cells = [c for (s, tf, seg), c in cells.items() if tf in tfs and seg == "is"]
        is_rows, is_conc, is_peak = pooled_rows(universe, is_cells, "is")
        rows.extend(is_rows)
        concurrency.extend(is_conc)

        oos_cells = [c for (s, tf, seg), c in cells.items() if tf in tfs and seg == "oos"]
        oos_rows, oos_conc, oos_peak = pooled_rows(
            universe, oos_cells, "oos", peak_override=is_peak
        )
        rows.extend(oos_rows)
        concurrency.extend(oos_conc)
        if log:
            print(
                f"[wan103] pooled {universe}: 완료 (IS peak N={is_peak}, OOS 자연 겹침={oos_peak})"
            )
    return rows, concurrency


def rows_to_frame(rows: Sequence[PortfolioRow]) -> pd.DataFrame:
    records = [row.model_dump() for row in rows]
    return pd.DataFrame(records, columns=list(ROW_COLUMNS))


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-83 리포트와 같은 헬퍼)."""
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


def _pct(frame: pd.DataFrame) -> pd.DataFrame:
    """표에 싣기 좋게 비율 열을 % 소수 2자리로 정리한다.

    계측하지 않은 칸(`single` 행의 포트폴리오 진단)은 `—`로 적는다 — 0으로 적으면
    "쟀더니 0이었다"로 읽힌다.
    """
    out = frame.copy()
    for col in ("total_return", "max_drawdown", "win_rate"):
        out[col] = (out[col] * 100).round(2)
    for col in ("max_open_notional_ratio", "max_concurrent_risk_ratio"):
        out[col] = out[col].round(3)
    if "sharpe" in out.columns:
        out["sharpe"] = out["sharpe"].round(2)
    return out.astype(object).where(out.notna(), "—")


_SERIES_VIEW = (
    "symbol",
    "timeframe",
    "segment",
    "scenario",
    "num_trades",
    "total_return",
    "max_drawdown",
    "win_rate",
    "peak_concurrency",
    "max_concurrent_risk_ratio",
    "liquidations",
)

_POOLED_VIEW = (
    "universe",
    "segment",
    "scenario",
    "leverage",
    "num_trades",
    "total_return",
    "max_drawdown",
    "win_rate",
    "sharpe",
    "peak_concurrency",
    "max_open_notional_ratio",
    "max_concurrent_risk_ratio",
    "liquidations",
)


def concurrency_to_frame(rows: Sequence[ConcurrencyRow]) -> pd.DataFrame:
    records = [row.model_dump() for row in rows]
    return pd.DataFrame(records, columns=list(CONCURRENCY_COLUMNS))


def write_summary(
    rows: Sequence[PortfolioRow], path: Path, concurrency: Sequence[ConcurrencyRow] = ()
) -> None:
    frame = rows_to_frame(rows)
    series = _pct(frame[frame["scope"] == SCOPE_SERIES]).reset_index(drop=True)
    pooled = _pct(frame[frame["scope"] == SCOPE_POOLED]).reset_index(drop=True)

    lines = [
        "# WAN-103: 동시 다중 포지션 + 포트폴리오 레버리지 — 레버리지 스윕·단일 포지션 대조",
        "",
        "재현: `uv run python -m backtest.wan103_portfolio_leverage_report`",
        "",
        "채택 기본값(`ConfluenceParams()` = 롱 온리·지정가·`first_tap_free`·고정 1.5R) 위에서 "
        "**포지션 제약만** 바꿔 돌린 결과다. 전략 파라미터는 하나도 건드리지 않았다. "
        "설계 결정 5개와 결론은 [`docs/decisions/wan103.md`](../../docs/decisions/wan103.md).",
        "",
        "> ⚠️ **`total_return`은 공식 렌즈 `baseline`(닿으면 체결) 기준**이라 상한으로 읽어야 "
        "한다(WAN-104). 체결 가정 민감도는 이 리포트의 축이 아니다 — 이 표가 묻는 건 "
        "*같은 체결 가정에서* 포지션 제약을 풀면 무엇이 달라지는가다.",
        "",
        "## series 범위 — (심볼, TF)가 자기 자본으로 도는 현행 백테스트 조건",
        "",
        "`single`이 채택 엔진 그 자체(동시 1포지션)이고, `lev_*`가 같은 셋업 풀을 동시 "
        "다중 포지션으로 배치한 것이다. 두 행의 차이는 **오직 포지션 제약**이다.",
        "",
        _md_table(series[list(_SERIES_VIEW)]),
        "",
        "## pooled 범위 — 유니버스 전체가 자본 하나를 공유(라이브 조건)",
        "",
        "`single`은 유니버스 전체가 포지션 1개를 나눠 쓰는 WAN-83 global 조건이다. "
        "`peak_N`의 N은 **IS에서 잰 자연 겹침 최댓값**이며, OOS 행은 그 N을 그대로 "
        "적용한 검증이다(OOS에서 잰 N을 OOS에 쓰면 look-ahead다).",
        "",
        _md_table(pooled[list(_POOLED_VIEW)]),
        "",
    ]

    if len(concurrency):
        conc = concurrency_to_frame(concurrency)
        conc["time_share"] = (conc["time_share"] * 100).round(2)
        lines += [
            "## 겹침 분포 — 동시 k개를 들고 있던 시간 비중 (명목 상한 없이 잰 자연 수요)",
            "",
            "N(= `peak_concurrency`)이 **얼마나 드문 사건인지**를 보여준다. N은 최댓값이라 "
            "단 한 순간에만 성립할 수 있고, 그 한 순간에 맞춰 레버리지를 정하면 나머지 "
            "시간 전체가 과도한 상한 아래에서 도는 셈이다.",
            "",
            _md_table(
                conc[["universe", "segment", "concurrency", "time_share", "peak_concurrency"]]
            ),
            "",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument(
        "--out-csv", type=str, default="backtest/reports/wan103_portfolio_leverage.csv"
    )
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan103_portfolio_leverage_summary.md"
    )
    parser.add_argument(
        "--out-concurrency",
        type=str,
        default="backtest/reports/wan103_concurrency_distribution.csv",
    )
    args = parser.parse_args()

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    rows, concurrency = run_report(symbols=symbols, years=args.years)
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(out_csv, index=False)
    concurrency_to_frame(concurrency).to_csv(Path(args.out_concurrency), index=False)
    write_summary(rows, Path(args.out_md), concurrency)
    print(f"[wan103] 저장: {out_csv}, {args.out_concurrency}, {args.out_md}")


if __name__ == "__main__":
    main()
