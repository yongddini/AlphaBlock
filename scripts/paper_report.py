"""페이퍼 트레이딩 성과 & 백테스트 대비 패리티 리포트 CLI (WAN-33).

WAN-25 페이퍼 러너가 `paper_trades` 테이블에 누적한 가상 거래를 읽어 성과 지표
(총 PnL(R 배수·%)·승률·손익비·MDD·거래 수)를 전체 및 심볼·TF별로 집계하고, 같은
기간·시리즈를 WAN-8 백테스트로 재실행한 결과와 비교(거래 수·승률·평균 R 차이)한다.
차이가 큰 시리즈는 ⚠로 표시한다.

데이터는 `ALPHABLOCK_DB_PATH`(기본 `data/ohlcv.db`)의 `paper_trades`(성과)·`ohlcv`
(패리티 백테스트)·`funding_rate`(펀딩비)에서 읽는다. 결과 표는 `--out-dir`에 CSV로
저장한다(거래 원장·성과 요약·패리티).

사용법::

    # 저장된 페이퍼 거래로 성과 + 패리티 리포트
    uv run python scripts/paper_report.py

    # 특정 기간·심볼만, 패리티 없이 성과만
    uv run python scripts/paper_report.py --symbols BTC/USDT:USDT --start 2024-01-01 --no-parity
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from config.settings import get_settings
from paper.parity import ParityThresholds, build_parity_report
from paper.performance import build_performance
from paper.report import (
    format_performance,
    performance_to_dataframe,
    records_to_dataframe,
)
from paper.store import PaperTradeStore


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="SQLite 경로 (기본: 설정값)")
    parser.add_argument("--symbols", default=None, help="쉼표로 구분한 심볼 필터 (기본: 전체)")
    parser.add_argument("--timeframes", default=None, help="쉼표로 구분한 TF 필터 (기본: 전체)")
    parser.add_argument("--start", default=None, help="시작일 (ISO, 진입시각 기준). 없으면 전체")
    parser.add_argument("--end", default=None, help="종료일 (ISO, 배타적). 없으면 전체")
    parser.add_argument(
        "--out-dir", type=Path, default=Path("out/paper"), help="결과 CSV 저장 디렉터리"
    )
    parser.add_argument("--no-parity", action="store_true", help="패리티(백테스트 비교) 생략")
    return parser.parse_args(argv)


def _to_ms(value: str | None) -> int | None:
    if value is None:
        return None
    return int(pd.Timestamp(value, tz="UTC").timestamp() * 1000)


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    db_path = args.db_path or settings.db_path
    start_ms = _to_ms(args.start)
    end_ms = _to_ms(args.end)
    symbols = _split(args.symbols)
    timeframes = _split(args.timeframes)

    with PaperTradeStore(db_path) as store:
        all_series = store.list_series()
        target_series = [
            (s, tf)
            for s, tf in all_series
            if (symbols is None or s in symbols) and (timeframes is None or tf in timeframes)
        ]
        records = [
            r
            for s, tf in target_series
            for r in store.list_records(s, tf, start_ms=start_ms, end_ms=end_ms)
        ]

    if not records:
        print(f"페이퍼 거래가 없습니다 ({db_path}). 먼저 러너(WAN-25)를 실행하세요.")
        return

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    performance = build_performance(records)
    print(format_performance(performance))

    trades_path = out_dir / "paper_trades.csv"
    records_to_dataframe(records).to_csv(trades_path, index=False)
    perf_path = out_dir / "performance.csv"
    performance_to_dataframe(performance).to_csv(perf_path, index=False)
    print(f"\n거래 원장 CSV: {trades_path}")
    print(f"성과 요약 CSV: {perf_path}")

    if args.no_parity:
        return

    report = build_parity_report(
        db_path,
        settings=settings,
        series=target_series,
        start_ms=start_ms,
        end_ms=end_ms,
        thresholds=ParityThresholds(),
    )
    print("\n" + report.to_table())
    parity_path = out_dir / "parity.csv"
    report.to_dataframe().to_csv(parity_path, index=False)
    print(f"\n패리티 CSV: {parity_path}")
    if report.flagged_rows:
        flagged = ", ".join(f"{r.symbol} {r.timeframe}" for r in report.flagged_rows)
        print(f"⚠ 불일치가 큰 시리즈: {flagged}")


if __name__ == "__main__":
    main()
