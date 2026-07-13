"""A vs B 비교 실행 오케스트레이터 테스트 (WAN-41).

`backtest.ab_run`이 A안(종가/확정봉)과 B안(존-지정가/실시간 RSI)을 같은 창·같은
비용 모델로 돌려 재현 가능한 비교 CSV를 만드는지 검증한다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest.ab_run import add_cost_args, build_ab_csv, build_ab_entries, cost_config, main
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


def test_cost_args_defaults_and_gross() -> None:
    """WAN-58 비용 플래그: 기본값은 WAN-37 현실 모델, 0으로 넘기면 gross(무비용)."""
    parser = argparse.ArgumentParser()
    add_cost_args(parser)

    default_cfg = cost_config(parser.parse_args([]))
    assert default_cfg.fee_rate == 0.0004
    assert default_cfg.maker_fee_rate == 0.0002
    assert default_cfg.slippage == 0.0005
    # 메이커(B) 진입은 테이커보다 싸다 — 비대칭이 비용 모델에 실린다.
    assert default_cfg.cost_model.maker_fee_rate < default_cfg.cost_model.taker_fee_rate

    gross_cfg = cost_config(
        parser.parse_args(["--fee-rate", "0", "--maker-fee-rate", "0", "--slippage", "0"])
    )
    assert gross_cfg.fee_rate == 0.0
    assert gross_cfg.maker_fee_rate == 0.0
    assert gross_cfg.slippage == 0.0
    assert gross_cfg.cost_model.slippage_fraction == 0.0


def test_gross_beats_net_total_return() -> None:
    """같은 창에서 gross(무비용) 성과는 net(비용 후)보다 나쁘지 않다."""
    htf, one_min = _pair()
    gross = build_ab_entries(
        htf,
        one_min,
        symbol="S",
        timeframe="1h",
        backtest_config=cost_config(
            argparse.Namespace(fee_rate=0.0, maker_fee_rate=0.0, slippage=0.0)
        ),
    )
    net = build_ab_entries(
        htf,
        one_min,
        symbol="S",
        timeframe="1h",
        backtest_config=cost_config(
            argparse.Namespace(fee_rate=0.0004, maker_fee_rate=0.0002, slippage=0.0005)
        ),
    )
    for variant in ("A", "B"):
        g = next(e for e in gross if e.variant.startswith(variant))
        n = next(e for e in net if e.variant.startswith(variant))
        # 거래 집합은 동일(비용은 손익만 바꾼다) → gross 총수익이 net 이상.
        assert g.result.metrics.total_return >= n.result.metrics.total_return - 1e-9
