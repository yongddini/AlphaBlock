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
import json
import subprocess
from pathlib import Path

import pandas as pd

from backtest.models import BacktestConfig
from backtest.report import (
    format_long_short,
    format_summary,
    funding_coverage_banner,
    sizing_mode_banner,
    write_equity_csv,
    write_trades_csv,
)
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
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams, OrderBlockParams


def _git_commit_hash() -> str | None:
    """현재 커밋 해시(재현 증적용). git 정보를 못 읽으면 None."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        )
        return out.stdout.strip()
    except Exception:
        return None


def _write_run_config_json(
    path: Path,
    *,
    confluence: ConfluenceParams,
    order_block_params: OrderBlockParams,
    backtest_config: BacktestConfig,
) -> Path:
    """실행 설정 전체(재현 증적)를 JSON으로 남긴다(WAN-65).

    CSV 컬럼으로 매 행 반복하기 부담스러운 부가 필드(`retap_rule`·`swing_length`·
    `zone_count` 등)까지 포함해, 리포트 디렉터리 하나만 봐도 어떤 설정·어떤 코드
    버전으로 나온 숫자인지 완결적으로 알 수 있게 한다. CSV에는 핵심 4개
    (`entry_mode`/`sizing_mode`/`combine_obs`/`funding_coverage`)만 남기고 나머지는
    여기로 위임한다.
    """
    payload = {
        "commit": _git_commit_hash(),
        "entry_mode": confluence.entry_mode,
        "rsi_mode": confluence.rsi_mode,
        "combine_obs": order_block_params.combine_obs,
        "retap_rule": "first_tap",
        "swing_length": order_block_params.swing_length,
        "zone_count": order_block_params.zone_count,
        "sizing_mode": backtest_config.sizing_mode,
        "risk_per_trade": backtest_config.risk_per_trade,
        "fee_rate": backtest_config.fee_rate,
        "maker_fee_rate": backtest_config.maker_fee_rate,
        "slippage": backtest_config.slippage,
        "funding_enabled": backtest_config.funding_enabled,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


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
    parser.add_argument(
        "--no-funding",
        action="store_true",
        help="펀딩비를 손익에 반영하지 않는다(기본: 반영). 합성 데이터는 항상 미반영.",
    )
    parser.add_argument(
        "--strict-costs",
        action="store_true",
        help="펀딩 데이터 커버리지가 100%% 미만이면 실행을 중단한다(조용한 0원 처리 금지).",
    )
    parser.add_argument(
        "--funding-include-predicted",
        action="store_true",
        help="예측(미정산) 펀딩비까지 반영한다(기본: 확정값만).",
    )
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


def _funding_config(
    timeframe: str,
    *,
    seed: int,
    funding_enabled: bool,
    strict_costs: bool,
    include_predicted: bool,
) -> BacktestConfig:
    """리포트용 백테스트 설정. 기본으로 펀딩비를 손익에 반영한다(WAN-63).

    `strict_costs=True`이면 펀딩 데이터 커버리지가 100% 미만일 때 엔진이 실행을 중단해
    "조용한 0원 처리"를 원천 차단한다.
    """
    base = default_backtest_config(timeframe, seed=seed)
    return base.model_copy(
        update={
            "funding_enabled": funding_enabled,
            "funding_include_predicted": include_predicted,
            "funding_missing_policy": "error" if strict_costs else "zero",
        }
    )


def _load_funding(
    symbol: str,
    *,
    db_path: str,
    start_ms: int | None,
    end_ms: int | None,
) -> list[FundingRate]:
    """저장소에서 심볼의 펀딩 이력을 로드한다(예측 포함). 없으면 빈 리스트.

    DB 파일이 없거나 테이블이 비어 있으면 빈 리스트를 돌려주며, 그 경우 엔진이
    커버리지 0을 감지해 경고/중단하도록 한다(WAN-63 조용한 실패 방지).
    """
    if not Path(db_path).exists():
        return []
    with FundingRateStore(db_path) as store:
        return store.get_rates(symbol, start_ms=start_ms, end_ms=end_ms, include_predicted=True)


def _report_combo(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    source: str,
    *,
    out_dir: Path,
    sort_by: str,
    seed: int,
    funding_rates: list[FundingRate],
    funding_enabled: bool,
    strict_costs: bool,
    include_predicted: bool,
) -> SweepReport:
    """한 (심볼, 타임프레임)에 대해 스윕 + 최적 조합 상세 리포트를 만들고 저장한다.

    저장된 펀딩 이력(`funding_rates`)을 손익에 반영한다. 합성 데이터에는 실제 펀딩이
    없으므로 호출부에서 `funding_enabled=False`로 넘긴다.
    """
    grid = ParamGrid()
    base_confluence = ConfluenceParams()
    base_backtest = _funding_config(
        timeframe,
        seed=seed,
        funding_enabled=funding_enabled,
        strict_costs=strict_costs,
        include_predicted=include_predicted,
    )

    report = run_sweep(
        df,
        symbol=symbol,
        timeframe=timeframe,
        grid=grid,
        base_confluence=base_confluence,
        base_backtest=base_backtest,
        funding_rates=funding_rates if funding_enabled else None,
        sort_by=sort_by,
    )

    slug = f"{symbol.replace('/', '_').replace(':', '_')}_{timeframe}"
    combo_dir = out_dir / slug
    sweep_path = write_sweep_csv(report, combo_dir / "sweep.csv")

    n_rates = len(funding_rates) if funding_enabled else 0
    print(f"\n########## {symbol} {timeframe} (source={source}, bars={len(df)}) ##########")
    print(f"펀딩비 반영: {funding_enabled} (저장 펀딩 {n_rates}행, strict_costs={strict_costs})")
    print(report.to_table())
    print(f"\n스윕 비교표 저장: {sweep_path}")

    best = report.best()
    if best is not None:
        confluence = apply_sweep_point(base_confluence, _point_from_row(best))
        result = evaluate(
            df,
            confluence_params=confluence,
            backtest_config=base_backtest,
            funding_rates=funding_rates if funding_enabled else None,
        )
        trades_path = write_trades_csv(result, combo_dir / "best_trades.csv", confluence=confluence)
        equity_path = write_equity_csv(result, combo_dir / "best_equity.csv")
        run_config_path = _write_run_config_json(
            combo_dir / "run_config.json",
            confluence=confluence,
            order_block_params=OrderBlockParams(),
            backtest_config=base_backtest,
        )
        print(f"\n--- 추천(best) 조합 상세: {sort_by} 최상위 ---")
        print(f"rsi_oversold={best.rsi_oversold:.0f} rsi_overbought={best.rsi_overbought:.0f}")
        print(format_summary(result, confluence=confluence))
        banner = sizing_mode_banner(result)
        if banner:
            print(banner)
        banner = funding_coverage_banner(result)
        if banner:
            print(banner)
        print()
        print(format_long_short(result))
        print(f"거래 CSV: {trades_path}")
        print(f"자본곡선 CSV: {equity_path}")
        print(f"실행 설정 JSON: {run_config_path}")

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
        # 펀딩 이력은 심볼당 한 번만 조회해 모든 타임프레임에 공유한다.
        funding_rates = _load_funding(symbol, db_path=db_path, start_ms=start_ms, end_ms=end_ms)
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
            # 합성 데이터에는 실제 펀딩이 없으므로 펀딩 반영을 끈다.
            funding_enabled = not args.no_funding and source == "db"
            report = _report_combo(
                symbol,
                timeframe,
                df,
                source,
                out_dir=args.out_dir,
                sort_by=args.sort_by,
                seed=args.seed,
                funding_rates=funding_rates,
                funding_enabled=funding_enabled,
                strict_costs=args.strict_costs,
                include_predicted=args.funding_include_predicted,
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
