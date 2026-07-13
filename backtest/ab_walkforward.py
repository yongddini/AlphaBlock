"""진입 방식(A/B) 워크포워드/아웃오브샘플(OOS) 검증 (WAN-50).

WAN-46(`backtest.ab_experiment`) 본실험에서 B안(존-지정가 진입 + 실시간 봉내 RSI)이
A안(종가/확정봉) 대비 손익비를 개선했지만, 그 우위가 **인샘플에서만** 나타나는
과적합인지 아니면 아웃오브샘플에서도 유지되는지 검증되지 않았다. 이 모듈은
WAN-22(`backtest.walkforward`)의 롤링 워크포워드 프레임을 **진입 방식(A/B) 축에
재사용**해, 각 (IS, OOS) 윈도우에서 A안과 B안을 **동일 조건**(동일 오더블록·동일
비용 모델·동일 셋업 필터)으로 나란히 돌리고, IS 대비 OOS 성능 저하 폭을 A/B 각각
산출한다.

## 왜 새 엔진을 만들지 않는가

윈도우 경계 계산은 `backtest.walkforward.generate_windows`를, A/B 실행은
`backtest.ab_run.build_ab_entries`(→`sweep.evaluate` + `zone_limit_backtest`)를
그대로 쓴다. 이 모듈은 그 둘을 **롤링 윈도우 축으로 배선**할 뿐이다. 진입 방식에는
튜닝할 파라미터가 없으므로(WAN-22의 RSI 스윕과 달리) IS에서 파라미터를 고르지
않는다 — 대신 IS/OOS 각 구간에서 A·B 성과를 그대로 측정해 **구간 간 성능 저하**와
**OOS에서의 A 대비 B 우위**를 정량화한다.

## 데이터 누수 방지

한 윈도우의 어떤 계산에도 그 윈도우의 `oos_end` 이후(미래) 봉·1분봉은 전달되지
않는다. IS 구간은 `frame[is_warmup_start:is_end]`(과거 워밍업 + IS 봉)에서 신호를
만들되 성과 집계는 IS 시간창 안의 거래로 한정하고, OOS 구간은
`frame[oos_warmup_start:oos_end]`에서 신호를 만들되 OOS 시간창 안의 거래로 한정한다
(`*_warmup_start = max(0, *_start - warmup_bars)`로 항상 과거만 포함). 1분봉도 각
집계 시간창으로만 잘라 넘기므로, 워밍업 구간에 걸린 셋업은 1분봉이 없어 평가에서
제외된다. `tests/test_ab_walkforward.py`의 룩어헤드 테스트가 미래 데이터를 바꿔도
과거 윈도우 결과가 불변임을 검증한다.

## 재현성

`build_ab_entries`가 결정적이고 윈도우 경계도 결정적이므로, 같은 `ohlcv.db`
스냅샷에서 다시 돌리면 동일한 CSV가 나온다. 단일 커맨드:
``uv run python -m backtest.ab_walkforward``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.ab_report import ABEntry
from backtest.ab_run import add_cost_args, build_ab_entries, cost_config
from backtest.models import BacktestConfig, Trade
from backtest.sweep import timeframe_to_ms
from backtest.walkforward import generate_windows
from strategy.models import ConfluenceParams, OrderBlockParams

#: 본검증 대상 심볼·상위TF·기간(년). 4h(B의 edge가 가장 큰 구간)를 앞에 둔다.
DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "15m", "1d")
DEFAULT_YEARS: float = 3.0

#: TF별 (IS 봉수, OOS 봉수, 워밍업 봉수). 대략 IS≈1년·OOS≈3개월로 롤링한다.
#: 워밍업은 지표(EMA 최대 365봉)·RSI 시딩·오더블록 컨텍스트 확보용 과거 봉수.
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)


def _default_window_bars(timeframe: str) -> tuple[int, int, int]:
    """TF에서 (is_bars, oos_bars, warmup_bars) 기본값을 유도한다.

    IS는 약 1년, OOS는 약 3개월, 워밍업은 약 2개월치 봉으로 잡는다(TF의 연간 봉수에서
    비례 계산). 최소값을 둬 아주 성긴 TF(1d)에서도 윈도우가 생성되게 한다.
    """
    per_year = _YEAR_MS / timeframe_to_ms(timeframe)
    is_bars = max(60, int(round(per_year)))
    oos_bars = max(20, int(round(per_year / 4)))
    warmup_bars = max(20, int(round(per_year / 6)))
    return is_bars, oos_bars, warmup_bars


# --------------------------------------------------------------------------- #
# 구간(IS/OOS) 성과
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Segment:
    """한 구간(IS 또는 OOS)의 한 변형(A/B) 성과 + B안 진단."""

    total_return: float
    max_drawdown: float
    win_rate: float
    profit_factor: float | None
    sharpe: float | None
    num_trades: int
    avg_trade_return: float | None
    fill_rate: float | None
    num_penetrations: int | None


def _avg_trade_return(trades: list[Trade]) -> float | None:
    """거래별 수익률(`return_pct`) 평균(≈평균 R). 거래가 없으면 None."""
    if not trades:
        return None
    return sum(t.return_pct for t in trades) / len(trades)


def _fill_rate(eligible: int | None, filled: int | None) -> float | None:
    if not eligible or filled is None:
        return None
    return filled / eligible


def _segment_from_entry(entry: ABEntry) -> _Segment:
    m = entry.result.metrics
    return _Segment(
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        win_rate=m.win_rate,
        profit_factor=m.profit_factor,
        sharpe=m.sharpe,
        num_trades=m.num_trades,
        avg_trade_return=_avg_trade_return(entry.result.trades),
        fill_rate=_fill_rate(entry.eligible_setups, entry.num_filled),
        num_penetrations=entry.num_penetrations,
    )


def _empty_segment() -> _Segment:
    return _Segment(
        total_return=0.0,
        max_drawdown=0.0,
        win_rate=0.0,
        profit_factor=None,
        sharpe=None,
        num_trades=0,
        avg_trade_return=None,
        fill_rate=None,
        num_penetrations=None,
    )


def _evaluate_segment(
    frame: pd.DataFrame,
    one_min: pd.DataFrame,
    timeframe: str,
    *,
    symbol: str,
    context_start: int,
    count_start: int,
    seg_end: int,
    confluence_params: ConfluenceParams | None,
    order_block_params: OrderBlockParams | None,
    backtest_config: BacktestConfig | None,
) -> dict[str, _Segment]:
    """한 구간(`[count_start, seg_end)` 봉)에서 A/B를 돌려 변형별 성과를 반환한다.

    오더블록 탐지·지표는 `frame[context_start:seg_end]`(과거 워밍업 포함)에서 하되,
    성과 집계는 `[count_start, seg_end)`의 시간창으로 넘긴 1분봉으로 자연히 한정된다
    (A안은 창 밖 거래 제외, B안은 1분봉 없는 워밍업 셋업 제외). 미래(`seg_end` 이후)는
    어떤 계산에도 들어가지 않는다.
    """
    htf_ms = timeframe_to_ms(timeframe)
    htf_seg = frame.iloc[context_start:seg_end].reset_index(drop=True)
    count_start_time = int(frame["open_time"].iloc[count_start])
    seg_last_time = int(frame["open_time"].iloc[seg_end - 1])
    one_min_seg = one_min[
        (one_min["open_time"] >= count_start_time) & (one_min["open_time"] < seg_last_time + htf_ms)
    ].reset_index(drop=True)

    if htf_seg.empty or one_min_seg.empty:
        return {"A": _empty_segment(), "B": _empty_segment()}

    entries = build_ab_entries(
        htf_seg,
        one_min_seg,
        symbol=symbol,
        timeframe=timeframe,
        confluence_params=confluence_params,
        order_block_params=order_block_params,
        backtest_config=backtest_config,
    )
    by_variant: dict[str, _Segment] = {}
    for entry in entries:
        key = "A" if entry.variant.startswith("A") else "B"
        by_variant[key] = _segment_from_entry(entry)
    by_variant.setdefault("A", _empty_segment())
    by_variant.setdefault("B", _empty_segment())
    return by_variant


# --------------------------------------------------------------------------- #
# 윈도우별 결과 행
# --------------------------------------------------------------------------- #


class ABWalkForwardRow(BaseModel):
    """한 (심볼, TF, 윈도우, 변형)의 IS/OOS 성과 (한 행)."""

    model_config = ConfigDict(frozen=True)

    window_index: int
    symbol: str
    timeframe: str
    variant: str
    is_start_time: int
    is_end_time: int
    oos_start_time: int
    oos_end_time: int
    is_num_bars: int
    oos_num_bars: int
    # --- IS 성과 ---
    is_total_return: float
    is_max_drawdown: float
    is_win_rate: float
    is_profit_factor: float | None
    is_sharpe: float | None
    is_num_trades: int
    is_avg_trade_return: float | None
    is_fill_rate: float | None
    is_num_penetrations: int | None
    # --- OOS 성과 ---
    oos_total_return: float
    oos_max_drawdown: float
    oos_win_rate: float
    oos_profit_factor: float | None
    oos_sharpe: float | None
    oos_num_trades: int
    oos_avg_trade_return: float | None
    oos_fill_rate: float | None
    oos_num_penetrations: int | None

    @property
    def return_gap(self) -> float:
        """IS 대비 OOS 총수익률 격차(IS − OOS). 양수가 클수록 과적합 신호."""
        return self.is_total_return - self.oos_total_return

    @property
    def profit_factor_gap(self) -> float | None:
        """IS 대비 OOS 손익비 격차(IS − OOS). 둘 중 하나라도 None이면 None."""
        if self.is_profit_factor is None or self.oos_profit_factor is None:
            return None
        return self.is_profit_factor - self.oos_profit_factor


_ROW_COLUMNS: tuple[str, ...] = (
    "window_index",
    "symbol",
    "timeframe",
    "variant",
    "is_start_time",
    "is_end_time",
    "oos_start_time",
    "oos_end_time",
    "is_num_bars",
    "oos_num_bars",
    "is_total_return",
    "is_max_drawdown",
    "is_win_rate",
    "is_profit_factor",
    "is_sharpe",
    "is_num_trades",
    "is_avg_trade_return",
    "is_fill_rate",
    "is_num_penetrations",
    "oos_total_return",
    "oos_max_drawdown",
    "oos_win_rate",
    "oos_profit_factor",
    "oos_sharpe",
    "oos_num_trades",
    "oos_avg_trade_return",
    "oos_fill_rate",
    "oos_num_penetrations",
)


class ABWalkForwardReport(BaseModel):
    """`run_ab_walk_forward()`의 반환값. 윈도우별 A/B IS/OOS 성과 행을 담는다."""

    model_config = ConfigDict(frozen=True)

    rows: list[ABWalkForwardRow]

    def to_dataframe(self) -> pd.DataFrame:
        """윈도우별 행을 DataFrame으로 (`_ROW_COLUMNS` 순서 + 격차 컬럼)."""
        records = []
        for row in self.rows:
            record = row.model_dump()
            record["return_gap"] = row.return_gap
            record["profit_factor_gap"] = row.profit_factor_gap
            records.append(record)
        columns = [*_ROW_COLUMNS, "return_gap", "profit_factor_gap"]
        return pd.DataFrame(records, columns=columns)

    def summary_dataframe(self) -> pd.DataFrame:
        """TF×변형별 OOS 성과·IS→OOS 저하 폭 집계표.

        각 (timeframe, variant)에 대해 윈도우 전반의 평균 OOS 총수익률·손익비,
        평균 IS→OOS 격차(수익률·손익비), OOS 거래 합계를 낸다. 손익비 평균은 값이
        있는(None 아닌) 윈도우만 평균한다.
        """
        records = []
        seen: list[tuple[str, str]] = []
        for row in self.rows:
            key = (row.timeframe, row.variant)
            if key not in seen:
                seen.append(key)
        for timeframe, variant in seen:
            group = [r for r in self.rows if r.timeframe == timeframe and r.variant == variant]
            records.append(
                {
                    "timeframe": timeframe,
                    "variant": variant,
                    "num_windows": len(group),
                    "oos_num_trades": sum(r.oos_num_trades for r in group),
                    "mean_oos_total_return": _mean([r.oos_total_return for r in group]),
                    "mean_oos_profit_factor": _mean(
                        [r.oos_profit_factor for r in group if r.oos_profit_factor is not None]
                    ),
                    "mean_oos_sharpe": _mean(
                        [r.oos_sharpe for r in group if r.oos_sharpe is not None]
                    ),
                    "mean_return_gap": _mean([r.return_gap for r in group]),
                    "mean_profit_factor_gap": _mean(
                        [r.profit_factor_gap for r in group if r.profit_factor_gap is not None]
                    ),
                    "mean_oos_fill_rate": _mean(
                        [r.oos_fill_rate for r in group if r.oos_fill_rate is not None]
                    ),
                }
            )
        return pd.DataFrame(records)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    """DataFrame을 CSV로 저장하고 경로를 반환한다(부모 디렉터리 자동 생성)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def run_ab_walk_forward(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    is_bars: int,
    oos_bars: int,
    step_bars: int | None = None,
    warmup_bars: int = 0,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
) -> ABWalkForwardReport:
    """진입 방식(A/B) 롤링 워크포워드를 실행한다.

    각 윈도우에서 IS 구간과 OOS 구간 각각에 대해 A안·B안을 돌려 성과를 측정하고,
    윈도우×변형별 행을 만든다(데이터 누수 방지 방식은 모듈 docstring 참고). `htf_df`는
    상위TF OHLCV(전체 히스토리), `one_min_df`는 B안 봉내 재구성용 1분봉이다. 데이터가
    `is_bars + oos_bars`보다 짧아 윈도우가 하나도 없으면 빈 리포트를 반환한다.
    """
    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    one_min = one_min_df.sort_values("open_time").reset_index(drop=True)

    windows = generate_windows(
        len(frame),
        is_bars=is_bars,
        oos_bars=oos_bars,
        step_bars=step_bars,
        warmup_bars=warmup_bars,
    )

    open_times = frame["open_time"].astype("int64") if len(frame) else None
    rows: list[ABWalkForwardRow] = []
    for w in windows:
        is_warmup_start = max(0, w.is_start - warmup_bars)
        is_segs = _evaluate_segment(
            frame,
            one_min,
            timeframe,
            symbol=symbol,
            context_start=is_warmup_start,
            count_start=w.is_start,
            seg_end=w.is_end,
            confluence_params=confluence_params,
            order_block_params=order_block_params,
            backtest_config=backtest_config,
        )
        oos_segs = _evaluate_segment(
            frame,
            one_min,
            timeframe,
            symbol=symbol,
            context_start=w.warmup_start,
            count_start=w.oos_start,
            seg_end=w.oos_end,
            confluence_params=confluence_params,
            order_block_params=order_block_params,
            backtest_config=backtest_config,
        )

        assert open_times is not None
        for variant in ("A", "B"):
            is_seg = is_segs[variant]
            oos_seg = oos_segs[variant]
            rows.append(
                ABWalkForwardRow(
                    window_index=w.index,
                    symbol=symbol,
                    timeframe=timeframe,
                    variant=variant,
                    is_start_time=int(open_times.iloc[w.is_start]),
                    is_end_time=int(open_times.iloc[w.is_end - 1]),
                    oos_start_time=int(open_times.iloc[w.oos_start]),
                    oos_end_time=int(open_times.iloc[w.oos_end - 1]),
                    is_num_bars=w.is_end - w.is_start,
                    oos_num_bars=w.oos_end - w.oos_start,
                    is_total_return=is_seg.total_return,
                    is_max_drawdown=is_seg.max_drawdown,
                    is_win_rate=is_seg.win_rate,
                    is_profit_factor=is_seg.profit_factor,
                    is_sharpe=is_seg.sharpe,
                    is_num_trades=is_seg.num_trades,
                    is_avg_trade_return=is_seg.avg_trade_return,
                    is_fill_rate=is_seg.fill_rate if variant == "B" else None,
                    is_num_penetrations=is_seg.num_penetrations if variant == "B" else None,
                    oos_total_return=oos_seg.total_return,
                    oos_max_drawdown=oos_seg.max_drawdown,
                    oos_win_rate=oos_seg.win_rate,
                    oos_profit_factor=oos_seg.profit_factor,
                    oos_sharpe=oos_seg.sharpe,
                    oos_num_trades=oos_seg.num_trades,
                    oos_avg_trade_return=oos_seg.avg_trade_return,
                    oos_fill_rate=oos_seg.fill_rate if variant == "B" else None,
                    oos_num_penetrations=oos_seg.num_penetrations if variant == "B" else None,
                )
            )
    return ABWalkForwardReport(rows=rows)


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
DEFAULT_REPORT_PATH = Path("backtest/reports/wan50_ab_walkforward.csv")
DEFAULT_SUMMARY_PATH = Path("backtest/reports/wan50_ab_walkforward_summary.csv")


def run_experiment(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    backtest_config: BacktestConfig | None = None,
) -> ABWalkForwardReport:
    """로컬 `data/ohlcv.db`의 실데이터로 심볼 × TF A/B 워크포워드를 돌린다.

    각 (심볼, TF)는 1분봉이 커버하는 최근 `years`년 구간으로 한정해 상위TF·1분봉을
    읽고 워크포워드를 실행한다. 저장소나 DB가 없으면 빈 리포트를 반환한다.
    """
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return ABWalkForwardReport(rows=[])
    if not db_path.exists():
        return ABWalkForwardReport(rows=[])

    rows: list[ABWalkForwardRow] = []
    with OhlcvStore(db_path) as store:
        for symbol in symbols:
            one_min_full = store.load(symbol, "1m")
            if one_min_full.empty:
                continue
            m_max = int(one_min_full["open_time"].max())
            req_start = m_max - int(years * _YEAR_MS)
            for timeframe in timeframes:
                htf_df = store.load(symbol, timeframe)
                if htf_df.empty:
                    continue
                start = max(req_start, int(htf_df["open_time"].min()))
                htf_win = htf_df[htf_df["open_time"] >= start].reset_index(drop=True)
                one_min_win = one_min_full[one_min_full["open_time"] >= start].reset_index(
                    drop=True
                )
                is_bars, oos_bars, warmup_bars = _default_window_bars(timeframe)
                report = run_ab_walk_forward(
                    htf_win,
                    one_min_win,
                    symbol=symbol,
                    timeframe=timeframe,
                    is_bars=is_bars,
                    oos_bars=oos_bars,
                    warmup_bars=warmup_bars,
                    backtest_config=backtest_config,
                )
                rows.extend(report.rows)
                print(
                    f"[ab_walkforward] {symbol} {timeframe}: "
                    f"windows={len(report.rows) // 2} is_bars={is_bars} oos_bars={oos_bars}"
                )
    return ABWalkForwardReport(rows=rows)


def main(argv: list[str] | None = None) -> int:
    """A/B 워크포워드 본검증을 실행해 윈도우별 CSV와 요약 CSV를 파일로 쓴다."""
    parser = argparse.ArgumentParser(description="WAN-50 A vs B 워크포워드/OOS 검증")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY_PATH)
    add_cost_args(parser)
    args = parser.parse_args(argv)

    report = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        backtest_config=cost_config(args),
    )
    detail = report.to_dataframe()
    summary = report.summary_dataframe()
    write_csv(detail, args.out)
    write_csv(summary, args.summary_out)
    print(f"[ab_walkforward] rows={len(report.rows)} → {args.out}")
    print(f"[ab_walkforward] summary → {args.summary_out}")
    if not summary.empty:
        print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
