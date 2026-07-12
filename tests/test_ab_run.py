"""A vs B 비교 실행 오케스트레이터 테스트 (WAN-41).

`backtest.ab_run`이 A안(종가/확정봉)과 B안(존-지정가/실시간 RSI)을 같은 창·같은
비용 모델로 돌려 재현 가능한 비교 CSV를 만드는지 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.ab_run import build_ab_csv, build_ab_entries, main
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv


def _pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=600, seed=7)
    htf_ms = timeframe_to_ms("1h")
    span = 120
    start = int(htf["open_time"].iloc[-span])
    one_min = make_synthetic_ohlcv(
        timeframe="1m",
        bars=span * (htf_ms // 60_000),
        seed=11,
        start_time_ms=start,
        swing_period=180,
    )
    return htf, one_min


def test_build_ab_entries_has_both_variants() -> None:
    htf, one_min = _pair()
    entries = build_ab_entries(htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h")
    variants = {e.variant for e in entries}
    assert variants == {"A_close_closedbar", "B_zonelimit_realtime"}
    for e in entries:
        assert e.symbol == "BTC/USDT:USDT"
        assert e.timeframe == "1h"


def test_build_ab_csv_is_reproducible() -> None:
    htf, one_min = _pair()
    csv_a = build_ab_csv(htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h")
    csv_b = build_ab_csv(htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h")
    assert csv_a == csv_b
    lines = csv_a.strip().splitlines()
    header = lines[0].split(",")
    assert header[:3] == ["symbol", "timeframe", "variant"]
    # 심볼·TF별 2행 + 변형별 합산 2행.
    assert any("A_close_closedbar" in line for line in lines)
    assert any("B_zonelimit_realtime" in line for line in lines)
    assert any(line.startswith("ALL,ALL,") for line in lines)


def test_a_window_matches_1m_span() -> None:
    """A안 성과 집계가 1분봉 커버 창 안 진입으로 한정된다."""
    htf, one_min = _pair()
    entries = build_ab_entries(htf, one_min, symbol="S", timeframe="1h")
    start = int(one_min["open_time"].min())
    end = int(one_min["open_time"].max())
    a = next(e for e in entries if e.variant.startswith("A"))
    for trade in a.result.trades:
        assert start <= trade.entry_time <= end


def test_main_writes_synthetic_report(tmp_path: Path) -> None:
    out = tmp_path / "report.csv"
    rc = main(["--synthetic", "--timeframe", "1h", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "A_close_closedbar" in text
    assert "B_zonelimit_realtime" in text
