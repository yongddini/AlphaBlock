"""컨플루언스 전략 백테스트 성과 리포트 & 파라미터 스윕 CLI (WAN-19).

WAN-23 재설계 컨플루언스 전략(오더블록+RSI 진입 / EMA·VWMA 선 익절 / 오더블록
무효화 손절)을 WAN-8 백테스트 엔진에 태워 심볼·타임프레임별 성과 리포트를 만들고,
진입 RSI 임계값을 소규모로 스윕해 비교표를 낸다. 각 타임프레임은 독립 단위로
개별 평가하며(지표 파라미터는 전 TF 공통 고정), 결과(요약 텍스트·거래/자본곡선·
스윕 CSV)는 재현을 위해 파라미터·시드·기간과 함께 `--out-dir`에 저장한다.

데이터는 `ALPHABLOCK_DB_PATH`(기본 `data/ohlcv.db`, WAN-6 수집분)에서 읽는다.
저장된 데이터가 없거나 `--synthetic`이면 시드로 고정된 합성 OHLCV로 대체해
**항상 재현 가능하게** 실행한다(데모/CI 스모크).

사용법::

    # 저장된 데이터로 BTC 전 타임프레임(15m·1h·2h·4h·1d) 리포트 + 스윕
    uv run python scripts/backtest_report.py --symbols BTC/USDT:USDT

    # 데이터 없이 합성 데이터로 재현 가능한 데모
    uv run python scripts/backtest_report.py --synthetic --timeframes 1h,4h
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest.report import format_summary, write_equity_csv, write_trades_csv
from backtest.sweep import (
    MultiSweepReport,
    ParamGrid,
    SweepPoint,
    SweepReport,
    SweepRunRow,
    apply_sweep_point,
    default_backtest_config,
    evaluate,
    run_sweep,
    write_sweep_csv,
)
from backtest.synthetic import make_synthetic_ohlcv
from config.settings import get_settings
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default="BTC/USDT:USDT",
        help="쉼표로 구분한 심볼 목록 (예: BTC/USDT:USDT,ETH/USDT:USDT)",
    )
    parser.add_argument(
        "--timeframes",
        default="15m,1h,2h,4h,1d",
        help="쉼표로 구분한 타임프레임 목록 (기본: 15m,1h,2h,4h,1d — WAN-23 대상 TF)",
    )
    parser.add_argument("--db-path", default=None, help="OHLCV SQLite 경로 (기본: 설정값)")
    parser.add_argument("--start", default=None, help="시작일 (ISO, 예: 2024-01-01). 없으면 전체")
    parser.add_argument("--end", default=None, help="종료일 (ISO, 배타적). 미지정 시 전체")
    parser.add_argument(
        "--out-dir", type=Path, default=Path("out/backtest"), help="결과 저장 디렉터리"
    )
    parser.add_argument(
        "--sort-by",
        default="sharpe",
        help="스윕 정렬 기준 (sharpe|total_return|win_rate|profit_factor|num_trades)",
    )
    parser.add_argument(
        "--synthetic", action="store_true", help="저장 데이터 대신 합성 OHLCV를 강제 사용"
    )
    parser.add_argument("--synthetic-bars", type=int, default=1500, help="합성 봉 수")
    parser.add_argument("--seed", type=int, default=0, help="백테스트/합성 데이터 시드(재현용)")
    return parser.parse_args(argv)


def _to_ms(value: str | None) -> int | None:
    """ISO 날짜/시각 문자열을 UTC epoch 밀리초로 변환한다."""
    if value is None:
        return None
    ts = pd.Timestamp(value, tz="UTC")
    return int(ts.timestamp() * 1000)


def _load_df(
    symbol: str,
    timeframe: str,
    *,
    db_path: str,
    start_ms: int | None,
    end_ms: int | None,
    use_synthetic: bool,
    synthetic_bars: int,
    seed: int,
) -> tuple[pd.DataFrame, str]:
    """(심볼, 타임프레임) OHLCV를 로드한다. 없거나 강제 시 합성으로 대체.

    (DataFrame, 데이터 출처 라벨) 을 반환한다.
    """
    if not use_synthetic:
        with OhlcvStore(db_path) as store:
            df = store.load(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
        if not df.empty:
            return df, "db"

    df = make_synthetic_ohlcv(symbol=symbol, timeframe=timeframe, bars=synthetic_bars, seed=seed)
    return df, "synthetic"


def _report_combo(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    source: str,
    *,
    out_dir: Path,
    sort_by: str,
    seed: int,
) -> SweepReport:
    """한 (심볼, 타임프레임)에 대해 스윕 + 최적 조합 상세 리포트를 만들고 저장한다."""
    grid = ParamGrid()
    base_confluence = ConfluenceParams()
    base_backtest = default_backtest_config(timeframe, seed=seed)

    report = run_sweep(
        df,
        symbol=symbol,
        timeframe=timeframe,
        grid=grid,
        base_confluence=base_confluence,
        base_backtest=base_backtest,
        sort_by=sort_by,
    )

    slug = f"{symbol.replace('/', '_').replace(':', '_')}_{timeframe}"
    combo_dir = out_dir / slug
    sweep_path = write_sweep_csv(report, combo_dir / "sweep.csv")

    print(f"\n########## {symbol} {timeframe} (source={source}, bars={len(df)}) ##########")
    print(report.to_table())
    print(f"\n스윕 비교표 저장: {sweep_path}")

    best = report.best()
    if best is not None:
        confluence = apply_sweep_point(base_confluence, _point_from_row(best))
        result = evaluate(df, confluence_params=confluence, backtest_config=base_backtest)
        trades_path = write_trades_csv(result, combo_dir / "best_trades.csv")
        equity_path = write_equity_csv(result, combo_dir / "best_equity.csv")
        print(f"\n--- 추천(best) 조합 상세: {sort_by} 최상위 ---")
        print(f"rsi_oversold={best.rsi_oversold:.0f} rsi_overbought={best.rsi_overbought:.0f}")
        print(format_summary(result))
        print(f"거래 CSV: {trades_path}")
        print(f"자본곡선 CSV: {equity_path}")

    return report


def _point_from_row(row: SweepRunRow) -> SweepPoint:
    """`SweepRunRow`의 파라미터로 `SweepPoint`를 재구성한다."""
    return SweepPoint(rsi_overbought=row.rsi_overbought)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    db_path = args.db_path or settings.db_path
    start_ms = _to_ms(args.start)
    end_ms = _to_ms(args.end)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    reports: list[SweepReport] = []
    for symbol in symbols:
        for timeframe in timeframes:
            df, source = _load_df(
                symbol,
                timeframe,
                db_path=db_path,
                start_ms=start_ms,
                end_ms=end_ms,
                use_synthetic=args.synthetic,
                synthetic_bars=args.synthetic_bars,
                seed=args.seed,
            )
            report = _report_combo(
                symbol,
                timeframe,
                df,
                source,
                out_dir=args.out_dir,
                sort_by=args.sort_by,
                seed=args.seed,
            )
            reports.append(report)

    multi = MultiSweepReport(reports=reports)
    combined = multi.combined_dataframe()
    combined_path = args.out_dir / "sweep_combined.csv"
    combined_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(combined_path, index=False)
    print(f"\n전체 조합 스윕 CSV: {combined_path} ({len(combined)} 행)")


if __name__ == "__main__":
    main()
