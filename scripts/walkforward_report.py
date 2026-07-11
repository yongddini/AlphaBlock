"""컨플루언스 전략 워크포워드/아웃오브샘플(OOS) 검증 CLI (WAN-22).

WAN-19 파라미터 스윕이 인샘플(IS)에 과적합되지 않았는지, 롤링 워크포워드로
확인한다. 각 윈도우에서 IS 구간으로 RSI 임계값을 스윕해 최적값을 고르고, 그
값을 고정해 **보지 않은** OOS 구간에서 성과를 측정한다. IS·OOS 성과 격차가
크면 과적합 신호다. 결과(윈도우별 IS/OOS 비교표 CSV)는 재현을 위해 심볼·
타임프레임·구간·시드·선택 파라미터와 함께 `--out-dir`에 저장한다.

데이터는 `ALPHABLOCK_DB_PATH`(기본 `data/ohlcv.db`, WAN-6 수집분)에서 읽는다.
저장된 데이터가 없거나 `--synthetic`이면 시드로 고정된 합성 OHLCV로 대체해
**항상 재현 가능하게** 실행한다(데모/CI 스모크).

사용법::

    # 저장된 데이터로 BTC 1h 워크포워드 (IS 720봉≈30일, OOS 168봉≈7일)
    uv run python scripts/walkforward_report.py --symbols BTC/USDT:USDT \\
        --timeframes 1h --is-bars 720 --oos-bars 168

    # 데이터 없이 합성 데이터로 재현 가능한 데모
    uv run python scripts/walkforward_report.py --synthetic --timeframes 1h
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest.models import BacktestConfig
from backtest.sweep import ParamGrid, bars_per_year
from backtest.synthetic import make_synthetic_ohlcv
from backtest.walkforward import WalkForwardReport, run_walk_forward, write_walk_forward_csv
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
        default="1h",
        help="쉼표로 구분한 타임프레임 목록 (기본: 1h)",
    )
    parser.add_argument("--db-path", default=None, help="OHLCV SQLite 경로 (기본: 설정값)")
    parser.add_argument("--start", default=None, help="시작일 (ISO, 예: 2024-01-01). 없으면 전체")
    parser.add_argument("--end", default=None, help="종료일 (ISO, 배타적). 미지정 시 전체")
    parser.add_argument(
        "--out-dir", type=Path, default=Path("out/walkforward"), help="결과 저장 디렉터리"
    )
    parser.add_argument(
        "--is-bars", type=int, default=720, help="인샘플(IS) 윈도우 길이(봉 수, 기본 720)"
    )
    parser.add_argument(
        "--oos-bars", type=int, default=168, help="아웃오브샘플(OOS) 윈도우 길이(봉 수, 기본 168)"
    )
    parser.add_argument(
        "--step-bars",
        type=int,
        default=None,
        help="윈도우 전진 간격(봉 수). 미지정 시 --oos-bars와 동일(겹치지 않는 롤링)",
    )
    parser.add_argument(
        "--warmup-bars",
        type=int,
        default=400,
        help="OOS 지표 워밍업용 과거(IS 꼬리) 봉 수(기본 400, 최장 EMA 365봉 커버)",
    )
    parser.add_argument(
        "--sort-by",
        default="sharpe",
        help="IS 스윕 정렬 기준 (sharpe|total_return|win_rate|profit_factor|num_trades)",
    )
    parser.add_argument(
        "--synthetic", action="store_true", help="저장 데이터 대신 합성 OHLCV를 강제 사용"
    )
    parser.add_argument("--synthetic-bars", type=int, default=3000, help="합성 봉 수")
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

    (DataFrame, 데이터 출처 라벨)을 반환한다.
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
    is_bars: int,
    oos_bars: int,
    step_bars: int | None,
    warmup_bars: int,
    sort_by: str,
    seed: int,
) -> WalkForwardReport:
    """한 (심볼, 타임프레임)에 대해 워크포워드를 실행하고 비교표를 저장한다."""
    base_confluence = ConfluenceParams()
    base_backtest = BacktestConfig(annualization_factor=bars_per_year(timeframe), seed=seed)

    report = run_walk_forward(
        df,
        symbol=symbol,
        timeframe=timeframe,
        is_bars=is_bars,
        oos_bars=oos_bars,
        step_bars=step_bars,
        warmup_bars=warmup_bars,
        grid=ParamGrid(),
        base_confluence=base_confluence,
        base_backtest=base_backtest,
        sort_by=sort_by,
    )

    slug = f"{symbol.replace('/', '_').replace(':', '_')}_{timeframe}"
    out_path = write_walk_forward_csv(report, out_dir / slug / "walkforward.csv")

    print(
        f"\n########## {symbol} {timeframe} (source={source}, bars={len(df)}, "
        f"windows={len(report.rows)}) ##########"
    )
    if report.rows:
        print(report.to_table())
    else:
        print(f"윈도우 없음: 데이터가 is_bars({is_bars}) + oos_bars({oos_bars})보다 짧습니다.")
    print(f"\n워크포워드 비교표 저장: {out_path}")

    return report


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    db_path = args.db_path or settings.db_path
    start_ms = _to_ms(args.start)
    end_ms = _to_ms(args.end)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

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
            _report_combo(
                symbol,
                timeframe,
                df,
                source,
                out_dir=args.out_dir,
                is_bars=args.is_bars,
                oos_bars=args.oos_bars,
                step_bars=args.step_bars,
                warmup_bars=args.warmup_bars,
                sort_by=args.sort_by,
                seed=args.seed,
            )


if __name__ == "__main__":
    main()
