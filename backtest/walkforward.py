"""컨플루언스 전략 워크포워드/아웃오브샘플(OOS) 검증 (WAN-22).

WAN-19가 인샘플(IS) 파라미터 스윕으로 고른 기본값이 특정 구간에 과적합되지
않았는지, 롤링 워크포워드로 확인한다. 각 윈도우는 IS 구간에서
`backtest.sweep.run_sweep`으로 최적 파라미터를 고르고, 그 파라미터를 **보지 않은**
OOS 구간에 그대로 적용해 성과를 측정한다. IS 성과와 OOS 성과의 격차
(`WalkForwardRow.return_gap`/`sharpe_gap`)가 크면 과적합 신호다.

## 구성

- `generate_windows()` — 데이터 길이에서 롤링 (IS, OOS) 윈도우 경계를 계산한다.
- `run_walk_forward()` — 각 윈도우에서 IS 스윕 → OOS 평가를 실행해
  `WalkForwardReport`를 만든다.
- `WalkForwardReport` — 윈도우별 IS/OOS 성과 행(`WalkForwardRow`)을 담고,
  DataFrame·CSV·비교표·평균 격차를 제공한다.

## 데이터 누수 방지

각 윈도우는 세 구간으로 나뉜다: `IS`(파라미터 선택) → `warmup`(지표 워밍업 전용,
IS 꼬리에서 빌려옴) → `OOS`(성과 측정). 파라미터는 **IS 구간만** 보고 고르며
(`run_sweep`에는 `frame.iloc[is_start:is_end]`만 전달), OOS 평가는 그 파라미터를
고정해 IS가 전혀 보지 못한 구간에 적용한다. `warmup`은 지표(EMA 최대 365봉 등)
워밍업을 위해 OOS 시작 이전의 과거 봉을 컨플루언스 신호 생성에만 포함시키는
것으로, 미래 데이터가 아니다(`warmup_start <= oos_start`로 항상 과거만 포함).
백테스트 엔진에는 `OOS` 구간의 봉만 전달되므로, 그 이전 시각에 계산된 신호는
엔진의 시간축에 존재하지 않아 체결되지 않는다. 어떤 윈도우의 계산에도 그 윈도우의
`oos_end` 이후(미래) 데이터는 전달되지 않는다 — `tests/test_walkforward.py`의
룩어헤드 테스트가 미래 구간을 바꿔도 과거 윈도우 결과가 불변임을 검증한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.sweep import (
    CLOSE_ENTRY_DEFAULTS,
    ParamGrid,
    SweepPoint,
    apply_sweep_point,
    default_backtest_config,
    run_sweep,
)
from data.models import FundingRate
from strategy.confluence import ConfluenceStrategy
from strategy.models import ConfluenceParams, OrderBlockParams

# --------------------------------------------------------------------------- #
# 윈도우 경계
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WalkForwardWindow:
    """롤링 워크포워드 한 윈도우의 위치 인덱스 경계(반열림 구간 `[start, end)`)."""

    index: int
    is_start: int
    is_end: int
    warmup_start: int
    oos_start: int
    oos_end: int


def generate_windows(
    n_bars: int,
    *,
    is_bars: int,
    oos_bars: int,
    step_bars: int | None = None,
    warmup_bars: int = 0,
) -> list[WalkForwardWindow]:
    """데이터 길이 `n_bars`에서 롤링 (IS, OOS) 윈도우 경계를 계산한다.

    각 윈도우는 `[is_start, is_end)`가 인샘플, `[oos_start, oos_end)`가
    아웃오브샘플이며 `oos_start == is_end`로 맞닿아 겹치지 않는다. `step_bars`
    (기본 `oos_bars`)만큼 앞으로 밀며 반복하고, 남은 구간이 `is_bars + oos_bars`
    보다 작아지면 멈춘다(부분 윈도우는 만들지 않는다).

    `warmup_bars`는 OOS 지표 워밍업을 위해 IS 꼬리에서 빌려오는 과거 봉 수다.
    `warmup_start = max(is_start, oos_start - warmup_bars)`로 계산해 항상
    `is_start <= warmup_start <= oos_start`를 만족한다(미래 데이터를 포함하지 않음).
    """
    if is_bars <= 0 or oos_bars <= 0:
        raise ValueError("is_bars, oos_bars는 1 이상이어야 합니다.")
    step = step_bars if step_bars is not None else oos_bars
    if step <= 0:
        raise ValueError("step_bars는 1 이상이어야 합니다.")

    windows: list[WalkForwardWindow] = []
    is_start = 0
    idx = 0
    while True:
        is_end = is_start + is_bars
        oos_end = is_end + oos_bars
        if oos_end > n_bars:
            break
        warmup_start = max(is_start, is_end - max(0, warmup_bars))
        windows.append(
            WalkForwardWindow(
                index=idx,
                is_start=is_start,
                is_end=is_end,
                warmup_start=warmup_start,
                oos_start=is_end,
                oos_end=oos_end,
            )
        )
        is_start += step
        idx += 1
    return windows


# --------------------------------------------------------------------------- #
# 윈도우별 결과
# --------------------------------------------------------------------------- #


class WalkForwardRow(BaseModel):
    """워크포워드 한 윈도우의 IS 선택 파라미터 + IS/OOS 성과 (한 행)."""

    model_config = ConfigDict(frozen=True)

    window_index: int
    symbol: str
    timeframe: str
    is_start_time: int
    is_end_time: int
    oos_start_time: int
    oos_end_time: int
    is_num_bars: int
    oos_num_bars: int
    # --- IS에서 선택된 파라미터 ---
    rsi_oversold: float
    rsi_overbought: float
    # --- IS 성과 (파라미터 선택 근거) ---
    is_total_return: float
    is_max_drawdown: float
    is_win_rate: float
    is_profit_factor: float | None
    is_sharpe: float | None
    is_num_trades: int
    # --- OOS 성과 (검증 대상) ---
    oos_total_return: float
    oos_max_drawdown: float
    oos_win_rate: float
    oos_profit_factor: float | None
    oos_sharpe: float | None
    oos_num_trades: int
    seed: int

    @property
    def return_gap(self) -> float:
        """IS 대비 OOS 총수익률 격차(IS − OOS). 양수가 클수록 과적합 신호."""
        return self.is_total_return - self.oos_total_return

    @property
    def sharpe_gap(self) -> float | None:
        """IS 대비 OOS 샤프 격차(IS − OOS). 둘 중 하나라도 None이면 None."""
        if self.is_sharpe is None or self.oos_sharpe is None:
            return None
        return self.is_sharpe - self.oos_sharpe


_ROW_COLUMNS = [
    "window_index",
    "symbol",
    "timeframe",
    "is_start_time",
    "is_end_time",
    "oos_start_time",
    "oos_end_time",
    "is_num_bars",
    "oos_num_bars",
    "rsi_oversold",
    "rsi_overbought",
    "is_total_return",
    "is_max_drawdown",
    "is_win_rate",
    "is_profit_factor",
    "is_sharpe",
    "is_num_trades",
    "oos_total_return",
    "oos_max_drawdown",
    "oos_win_rate",
    "oos_profit_factor",
    "oos_sharpe",
    "oos_num_trades",
    "seed",
]


class WalkForwardReport(BaseModel):
    """`run_walk_forward()`의 반환값. 윈도우별 IS/OOS 성과 행을 담는다."""

    model_config = ConfigDict(frozen=True)

    rows: list[WalkForwardRow]

    def to_dataframe(self) -> pd.DataFrame:
        """윈도우별 행을 DataFrame으로 (`_ROW_COLUMNS` 순서, IS/OOS 격차 컬럼 포함)."""
        records = []
        for row in self.rows:
            record = row.model_dump()
            record["return_gap"] = row.return_gap
            record["sharpe_gap"] = row.sharpe_gap
            records.append(record)
        return pd.DataFrame(records, columns=[*_ROW_COLUMNS, "return_gap", "sharpe_gap"])

    def mean_return_gap(self) -> float | None:
        """전 윈도우 평균 수익률 격차(IS − OOS). 윈도우가 없으면 None."""
        if not self.rows:
            return None
        return sum(r.return_gap for r in self.rows) / len(self.rows)

    def mean_sharpe_gap(self) -> float | None:
        """전 윈도우 평균 샤프 격차(IS − OOS). 격차를 계산할 수 있는 윈도우가
        없으면 None(둘 중 하나라도 None인 윈도우는 평균에서 제외)."""
        gaps = [r.sharpe_gap for r in self.rows if r.sharpe_gap is not None]
        return sum(gaps) / len(gaps) if gaps else None

    def to_table(self) -> str:
        """윈도우별 IS/OOS 비교표 문자열(콘솔용)."""
        header = (
            f"{'win':>3} {'rsi_ob':>6} "
            f"{'is_ret%':>8} {'oos_ret%':>9} {'is_shrp':>8} {'oos_shrp':>9} "
            f"{'is_n':>5} {'oos_n':>6}"
        )
        lines = ["=== Walk-Forward IS vs OOS ===", header, "-" * len(header)]
        for row in self.rows:
            is_shrp = "N/A" if row.is_sharpe is None else f"{row.is_sharpe:.2f}"
            oos_shrp = "N/A" if row.oos_sharpe is None else f"{row.oos_sharpe:.2f}"
            lines.append(
                f"{row.window_index:>3} {row.rsi_overbought:>6.0f} "
                f"{row.is_total_return * 100:>8.2f} {row.oos_total_return * 100:>9.2f} "
                f"{is_shrp:>8} {oos_shrp:>9} "
                f"{row.is_num_trades:>5} {row.oos_num_trades:>6}"
            )
        mean_ret_gap = self.mean_return_gap()
        mean_shrp_gap = self.mean_sharpe_gap()
        lines.append("-" * len(header))
        ret_gap_str = "N/A" if mean_ret_gap is None else f"{mean_ret_gap * 100:.2f}%"
        shrp_gap_str = "N/A" if mean_shrp_gap is None else f"{mean_shrp_gap:.2f}"
        lines.append(
            f"mean return_gap(IS-OOS)={ret_gap_str} mean sharpe_gap(IS-OOS)={shrp_gap_str}"
        )
        return "\n".join(lines)


def write_walk_forward_csv(report: WalkForwardReport, path: str | Path) -> Path:
    """워크포워드 윈도우별 비교표를 CSV로 저장하고 경로를 반환한다."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    report.to_dataframe().to_csv(out, index=False)
    return out


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _slice(frame: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    return frame.iloc[start:end].reset_index(drop=True)


def run_walk_forward(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    is_bars: int,
    oos_bars: int,
    step_bars: int | None = None,
    warmup_bars: int = 0,
    grid: ParamGrid | None = None,
    base_confluence: ConfluenceParams | None = None,
    base_backtest: BacktestConfig | None = None,
    order_block_params: OrderBlockParams | None = None,
    funding_rates: Sequence[FundingRate] | None = None,
    sort_by: str = "sharpe",
) -> WalkForwardReport:
    """롤링 워크포워드를 실행한다: 윈도우마다 IS 스윕으로 파라미터를 고르고
    (`run_sweep`), 그 파라미터를 고정해 OOS 구간에서 검증한다.

    OOS 신호 생성에는 `warmup_bars`만큼의 과거(IS 꼬리) 컨텍스트를 포함해 지표
    워밍업을 확보하지만, 백테스트 엔진에는 OOS 구간의 봉만 전달되므로 워밍업 구간의
    시각에 걸린 신호는 체결되지 않는다(데이터 누수 방지 방식은 모듈 docstring 참고).

    `funding_rates`(WAN-20)는 IS 스윕과 OOS 평가에 **동일하게** 전달되어, `base_
    backtest.funding_enabled=True`이면 파라미터 선택(IS)과 검증(OOS)이 같은 손익
    모델(수수료·슬리피지·펀딩비)로 이뤄진다. 엔진은 각 거래의 보유 구간에 정산된
    펀딩만 반영하므로, 전체 요율 목록을 넘겨도 IS/OOS 각 구간의 거래에만 적용된다.

    데이터가 `is_bars + oos_bars`보다 짧아 윈도우가 하나도 만들어지지 않으면 빈
    리포트(`rows=[]`)를 반환한다.
    """
    frame = df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)

    windows = generate_windows(
        len(frame),
        is_bars=is_bars,
        oos_bars=oos_bars,
        step_bars=step_bars,
        warmup_bars=warmup_bars,
    )

    grid = grid or ParamGrid()
    base_conf = base_confluence or CLOSE_ENTRY_DEFAULTS
    base_bt = base_backtest or default_backtest_config(timeframe)

    open_times = frame["open_time"].astype("int64") if len(frame) else None

    rows: list[WalkForwardRow] = []
    for w in windows:
        is_df = _slice(frame, w.is_start, w.is_end)
        sweep_report = run_sweep(
            is_df,
            symbol=symbol,
            timeframe=timeframe,
            grid=grid,
            base_confluence=base_conf,
            base_backtest=base_bt,
            order_block_params=order_block_params,
            funding_rates=funding_rates,
            sort_by=sort_by,
        )
        best = sweep_report.best()
        if best is None:
            continue

        selected_confluence = apply_sweep_point(
            base_conf, SweepPoint(rsi_overbought=best.rsi_overbought)
        )

        warmup_and_oos_df = _slice(frame, w.warmup_start, w.oos_end)
        oos_only_df = _slice(frame, w.oos_start, w.oos_end)

        strategy = ConfluenceStrategy(selected_confluence, order_block_params)
        confluence = strategy.run(warmup_and_oos_df)
        oos_result = BacktestEngine(base_bt).run(
            oos_only_df, confluence.order_block_signals, funding_rates
        )
        m = oos_result.metrics

        assert open_times is not None
        rows.append(
            WalkForwardRow(
                window_index=w.index,
                symbol=symbol,
                timeframe=timeframe,
                is_start_time=int(open_times.iloc[w.is_start]),
                is_end_time=int(open_times.iloc[w.is_end - 1]),
                oos_start_time=int(open_times.iloc[w.oos_start]),
                oos_end_time=int(open_times.iloc[w.oos_end - 1]),
                is_num_bars=w.is_end - w.is_start,
                oos_num_bars=w.oos_end - w.oos_start,
                rsi_oversold=best.rsi_oversold,
                rsi_overbought=best.rsi_overbought,
                is_total_return=best.total_return,
                is_max_drawdown=best.max_drawdown,
                is_win_rate=best.win_rate,
                is_profit_factor=best.profit_factor,
                is_sharpe=best.sharpe,
                is_num_trades=best.num_trades,
                oos_total_return=m.total_return,
                oos_max_drawdown=m.max_drawdown,
                oos_win_rate=m.win_rate,
                oos_profit_factor=m.profit_factor,
                oos_sharpe=m.sharpe,
                oos_num_trades=m.num_trades,
                seed=base_bt.seed,
            )
        )

    return WalkForwardReport(rows=rows)
