"""A안(종가/확정봉) vs B안(존-지정가/실시간 RSI) 실행 오케스트레이터 (WAN-41).

`backtest.ab_report`(리포트 포맷)와 `backtest.zone_limit_backtest`(B안 파이프라인)를
묶어, **같은 심볼·TF·기간·비용 모델**에서 A안과 B안을 나란히 돌려 비교 CSV를
산출한다. 두 변형이 동일 오더블록·동일 비용(수수료·슬리피지·사이징)을 공유하므로
비교가 공정하다.

## 공정 비교 창(window)

B안은 1분봉이 존재하는 기간으로만 셋업을 평가한다(1분봉 미커버 구간 제외,
`zone_limit_backtest` 참고). 같은 기간에서 비교하기 위해 A안 거래도 **1분봉이
커버하는 시간창**(`[min(1m open_time), max(1m open_time)]`) 안에 진입한 것만
집계한다. 상위TF 히스토리 전체는 오더블록 탐지·지표 워밍업에 그대로 쓰되, 성과
집계만 창으로 한정한다.

## 재현성

`build_ab_entries`는 결정적 함수다. `main`은 로컬 저장소(`data/ohlcv.db`)에 1분봉이
있으면 실데이터로, 없으면 합성 데이터(`backtest.synthetic`)로 CSV를 만들어
`backtest/reports/wan41_ab_report.csv`에 쓴다(둘 다 재현 가능).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest.ab_report import ABEntry, build_ab_report
from backtest.metrics import build_metrics
from backtest.models import BacktestConfig, BacktestResult, Trade
from backtest.sweep import bars_per_year, evaluate
from backtest.zone_limit_backtest import (
    build_result_from_trades,
    run_zone_limit_backtest_verbose,
)
from strategy.models import ConfluenceParams, OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

#: 기본 리포트 출력 경로(재현 증적으로 커밋).
DEFAULT_REPORT_PATH = Path("backtest/reports/wan41_ab_report.csv")


def _window(df_1m: pd.DataFrame) -> tuple[int, int]:
    """1분봉이 커버하는 시간창 `[start, end]`(ms)."""
    times = df_1m["open_time"].astype("int64")
    return int(times.min()), int(times.max())


def _trades_in_window(trades: list[Trade], start: int, end: int) -> list[Trade]:
    """진입 시각이 창 `[start, end]` 안인 거래만."""
    return [t for t in trades if start <= t.entry_time <= end]


def build_ab_entries(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
) -> list[ABEntry]:
    """A안·B안을 같은 창에서 실행해 `ABEntry` 두 개(변형 A/B)를 만든다.

    오더블록 탐지는 한 번만 하고 두 변형이 공유한다(진입 방식과 무관하므로 동일
    오더블록으로 비교). A안은 `sweep.evaluate`(→`BacktestEngine`), B안은
    `run_zone_limit_backtest`로 돌린 뒤, 성과 집계를 1분봉 커버 창으로 한정한다.
    """
    cfg = backtest_config or BacktestConfig()
    ob_result: OrderBlockResult = OrderBlockDetector(order_block_params).run(htf_df)
    start, end = _window(df_1m)

    # A안: 종가 진입 + 확정봉 RSI(현행). 창 밖 거래는 집계에서 제외.
    a_params = _with_entry_mode(confluence_params, entry_mode="close", rsi_mode="closed_bar")
    a_full = evaluate(
        htf_df,
        confluence_params=a_params,
        order_block_params=order_block_params,
        backtest_config=cfg,
        order_block_result=ob_result,
    )
    a_result = _windowed_result(a_full.trades, cfg, timeframe, start, end)

    # B안: 존-지정가 진입 + 실시간 RSI(1분봉 서브스텝). 자연히 창 안으로 한정됨.
    b_params = _with_entry_mode(confluence_params, entry_mode="zone_limit", rsi_mode="realtime")
    b_result, b_stats = run_zone_limit_backtest_verbose(
        htf_df,
        df_1m,
        timeframe,
        confluence_params=b_params,
        order_block_params=order_block_params,
        backtest_config=cfg,
        order_block_result=ob_result,
    )

    return [
        ABEntry(symbol=symbol, timeframe=timeframe, variant="A_close_closedbar", result=a_result),
        ABEntry(
            symbol=symbol,
            timeframe=timeframe,
            variant="B_zonelimit_realtime",
            result=b_result,
            eligible_setups=b_stats.eligible,
            num_filled=b_stats.filled,
            num_penetrations=b_stats.penetrations,
        ),
    ]


def _with_entry_mode(
    base: ConfluenceParams | None, *, entry_mode: str, rsi_mode: str
) -> ConfluenceParams:
    params = base or ConfluenceParams()
    return params.model_copy(update={"entry_mode": entry_mode, "rsi_mode": rsi_mode})


def _windowed_result(
    trades: list[Trade],
    cfg: BacktestConfig,
    timeframe: str,
    start: int,
    end: int,
) -> BacktestResult:
    """A안 엔진 거래를 창으로 한정해 B안과 동일한 방식으로 재집계한 결과."""
    in_window = _trades_in_window(trades, start, end)
    if not in_window:
        metrics = build_metrics(
            initial_capital=cfg.initial_capital,
            equities=[cfg.initial_capital],
            trades=[],
            annualization_factor=bars_per_year(timeframe),
        )
        return BacktestResult(config=cfg, trades=[], equity_curve=[], metrics=metrics)
    return build_result_from_trades(in_window, cfg, timeframe)


def build_ab_csv(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
) -> str:
    """단일 심볼·TF에 대한 A/B 비교 CSV 문자열을 만든다."""
    entries = build_ab_entries(
        htf_df,
        df_1m,
        symbol=symbol,
        timeframe=timeframe,
        confluence_params=confluence_params,
        order_block_params=order_block_params,
        backtest_config=backtest_config,
    )
    return build_ab_report(entries)


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터 또는 합성 데이터
# --------------------------------------------------------------------------- #


def _load_local(symbol: str, timeframe: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """로컬 `data/ohlcv.db`에서 상위TF·1분봉을 읽는다. 없으면 None."""
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return None
    db_path = Path("data/ohlcv.db")
    if not db_path.exists():
        return None
    with OhlcvStore(db_path) as store:
        htf = store.load(symbol, timeframe)
        one_min = store.load(symbol, "1m")
    if htf.empty or one_min.empty:
        return None
    return htf, one_min


def _synthetic_pair(timeframe: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """실데이터가 없을 때 쓰는 결정적 합성 상위TF·1분봉 쌍."""
    from backtest.sweep import timeframe_to_ms
    from backtest.synthetic import make_synthetic_ohlcv

    htf = make_synthetic_ohlcv(timeframe=timeframe, bars=600, seed=7, start_time_ms=0)
    # 상위TF 마지막 구간을 커버하는 1분봉(봉 내부 경로 근사).
    span_bars = 120
    htf_ms = timeframe_to_ms(timeframe)
    start = int(htf["open_time"].iloc[-span_bars])
    minutes = span_bars * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def main(argv: list[str] | None = None) -> int:
    """A/B 리포트 CSV를 생성해 파일로 쓴다(재현 증적)."""
    parser = argparse.ArgumentParser(description="WAN-41 A vs B 비교 리포트 생성")
    parser.add_argument("--symbol", default="BTC/USDT:USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--synthetic", action="store_true", help="로컬 데이터 대신 합성 데이터를 강제 사용"
    )
    args = parser.parse_args(argv)

    pair = None if args.synthetic else _load_local(args.symbol, args.timeframe)
    if pair is None:
        htf_df, df_1m = _synthetic_pair(args.timeframe)
        source = "synthetic"
    else:
        htf_df, df_1m = pair
        source = "local ohlcv.db"

    csv_text = build_ab_csv(htf_df, df_1m, symbol=args.symbol, timeframe=args.timeframe)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(csv_text, encoding="utf-8")
    print(f"[ab_run] source={source} rows={len(df_1m)}(1m) → {args.out}")
    print(csv_text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
