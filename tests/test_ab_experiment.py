"""A vs B 본실험 러너 테스트 (WAN-46).

로컬 저장소의 실데이터로 다심볼 × 다TF A/B를 돌려 비교 CSV·커버리지 CSV를 재현
가능하게 내는지 검증한다. 실 DB 없이 임시 SQLite에 합성 봉을 심어 end-to-end로 돈다.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd

from backtest.ab_experiment import (
    build_coverage_csv,
    main,
    run_experiment,
)
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from data.models import Candle
from data.storage import OhlcvStore


def _seed_db(db_path: Path, symbols: tuple[str, ...]) -> None:
    """각 심볼에 상위TF(1h) 히스토리 + 이를 커버하는 1분봉을 심는다."""
    with OhlcvStore(db_path) as store:
        for i, symbol in enumerate(symbols):
            htf = make_synthetic_ohlcv(timeframe="1h", bars=400, seed=7 + i)
            htf_ms = timeframe_to_ms("1h")
            span = 120
            start = int(htf["open_time"].iloc[-span])
            one_min = make_synthetic_ohlcv(
                timeframe="1m",
                bars=span * (htf_ms // 60_000),
                seed=11 + i,
                start_time_ms=start,
                swing_period=180,
            )
            store.upsert_candles(_candles(symbol, "1h", htf))
            store.upsert_candles(_candles(symbol, "1m", one_min))


def _candles(symbol: str, timeframe: str, df: pd.DataFrame) -> list[Candle]:
    return [
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=int(row.open_time),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in df.itertuples(index=False)
    ]


def _parse(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_run_experiment_produces_entries_and_coverage(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    symbols = ("BTC/USDT:USDT", "ETH/USDT:USDT")
    _seed_db(db, symbols)
    entries, coverages = run_experiment(db_path=db, symbols=symbols, timeframes=("1h",), years=5.0)
    # 각 심볼당 A/B 두 엔트리.
    variants = {(e.symbol, e.variant) for e in entries}
    for symbol in symbols:
        assert (symbol, "A_close_closedbar") in variants
        assert (symbol, "B_zonelimit_realtime") in variants
    # 커버리지 행이 심볼×TF마다 하나씩.
    assert len(coverages) == len(symbols)
    for cov in coverages:
        assert cov.note == "ok"
        assert cov.coverage_ratio is not None and cov.coverage_ratio > 0


def test_b_entries_carry_fill_and_penetration_stats(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    symbols = ("BTC/USDT:USDT",)
    _seed_db(db, symbols)
    entries, _ = run_experiment(db_path=db, symbols=symbols, timeframes=("1h",), years=5.0)
    b = next(e for e in entries if e.variant.startswith("B"))
    assert b.eligible_setups is not None and b.eligible_setups >= 0
    assert b.num_filled is not None and b.num_filled >= 0
    assert b.num_penetrations is not None and b.num_penetrations >= 0
    # 체결 수는 대상 셋업 수를 넘지 않는다.
    assert b.num_filled <= b.eligible_setups


def test_coverage_csv_records_missing_policy() -> None:
    from backtest.ab_experiment import Coverage

    cov = Coverage(
        symbol="SOL/USDT:USDT",
        timeframe="4h",
        window_start_ms=0,
        window_end_ms=60_000,
        htf_bars=1,
        one_min_bars=1,
        expected_one_min=2,
        note="ok",
    )
    rows = _parse(build_coverage_csv([cov]))
    assert rows[0]["missing_policy"] == "exclude_uncovered_setups"
    assert rows[0]["coverage_ratio"] == "0.5"


def test_main_writes_both_reports(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    _seed_db(db, ("BTC/USDT:USDT",))
    out = tmp_path / "report.csv"
    cov_out = tmp_path / "coverage.csv"
    rc = main(
        [
            "--db",
            str(db),
            "--symbols",
            "BTC/USDT:USDT",
            "--timeframes",
            "1h",
            "--years",
            "5",
            "--out",
            str(out),
            "--coverage-out",
            str(cov_out),
        ]
    )
    assert rc == 0
    report = out.read_text(encoding="utf-8")
    assert "A_close_closedbar" in report
    assert "B_zonelimit_realtime" in report
    assert "num_penetrations" in report.splitlines()[0]
    assert "ALL,ALL," in report
    cov_text = cov_out.read_text(encoding="utf-8")
    assert "exclude_uncovered_setups" in cov_text


def test_missing_db_yields_empty(tmp_path: Path) -> None:
    entries, coverages = run_experiment(db_path=tmp_path / "nope.db")
    assert entries == []
    assert coverages == []
