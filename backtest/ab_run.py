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
from backtest.harness import coverage_window, windowed_result
from backtest.models import BacktestConfig, BacktestResult, Trade
from backtest.sweep import default_backtest_config, evaluate
from backtest.zone_limit_backtest import run_zone_limit_backtest_verbose
from strategy.models import ConfluenceParams, OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

#: 기본 리포트 출력 경로(재현 증적으로 커밋).
DEFAULT_REPORT_PATH = Path("backtest/reports/wan41_ab_report.csv")


def add_cost_args(parser: argparse.ArgumentParser) -> None:
    """A/B 러너 CLI에 공용 체결 비용 플래그를 추가한다 (WAN-58).

    기본값은 WAN-37 현실 비용 모델(테이커 0.04% / 메이커 0.02% / 슬리피지 5bps).
    비용 차감 전(`gross`) 재산출을 원하면 세 값을 모두 0으로 넘긴다
    (``--fee-rate 0 --maker-fee-rate 0 --slippage 0``). 그래야 병합(WAN-56) 효과와
    비용(WAN-37) 효과를 같은 하네스에서 분리 산출할 수 있다.
    """
    parser.add_argument(
        "--fee-rate", type=float, default=0.0004, help="테이커(시장가) 수수료율 (기본 0.0004)"
    )
    parser.add_argument(
        "--maker-fee-rate",
        type=float,
        default=0.0002,
        help="메이커(지정가) 수수료율. B안 진입 체결에 적용 (기본 0.0002)",
    )
    parser.add_argument(
        "--slippage", type=float, default=0.0005, help="테이커 슬리피지 분수 (기본 0.0005)"
    )


def cost_config(args: argparse.Namespace) -> BacktestConfig:
    """`add_cost_args`로 파싱한 값으로 `BacktestConfig`를 만든다.

    수수료·슬리피지만 비용 모델 위에 덮어쓰면 되므로 나머지는 공용 팩토리
    (`default_backtest_config`, WAN-65)의 기본값을 쓴다 — 그래야 A/B 실험도 다른
    진입점과 동일하게 `settings.effective_risk_sizing`이 적용된다.
    """
    base = default_backtest_config()
    return base.model_copy(
        update={
            "fee_rate": args.fee_rate,
            "maker_fee_rate": args.maker_fee_rate,
            "slippage": args.slippage,
        }
    )


def _window(df_1m: pd.DataFrame) -> tuple[int, int]:
    """1분봉이 커버하는 시간창 `[start, end]`(ms). 공용 골격(WAN-101) 위임."""
    return coverage_window(df_1m)


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
    cfg = backtest_config or default_backtest_config(timeframe)
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
    update: dict[str, object] = {"entry_mode": entry_mode, "rsi_mode": rsi_mode}
    if entry_mode == "close":
        # A안은 존폭 필터(B안 전용, WAN-158)를 읽지 않는다. 채택 기본값 1.28을 들고 있으면
        # `sweep.evaluate`가 거부하므로 끈다 — `CLOSE_ENTRY_DEFAULTS`가 offset과 함께 끄는 것과
        # 같은 처리(WAN-159).
        update["max_zone_width_atr"] = None
    return params.model_copy(update=update)


def _windowed_result(
    trades: list[Trade],
    cfg: BacktestConfig,
    timeframe: str,
    start: int,
    end: int,
    *,
    funding_coverage: float | None = None,
) -> BacktestResult:
    """A안 엔진 거래를 창으로 한정해 B안과 동일한 방식으로 재집계한 결과.

    공용 골격(`backtest.harness.windowed_result`, WAN-101) 위임 — 같은 창 규칙을 CLI와
    리포트가 공유해야 두 결과를 나란히 놓고 비교할 수 있다.
    """
    return windowed_result(trades, cfg, timeframe, start, end, funding_coverage=funding_coverage)


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
