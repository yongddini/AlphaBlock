"""backtest.wan71_edge_decomposition 단위/스모크 테스트 (WAN-71).

3심볼×4TF 전체 실데이터 산출(무작위 대조군·비용 민감도·바이앤홀드·가설 판정)은
`backtest/reports/wan71_*.csv`·`wan71_summary.md`(재현: `python -m
backtest.wan71_edge_decomposition`)로 별도 확인한다. 여기서는 결정적 합성 데이터·수기
구성 증거로 핵심 로직(바이앤홀드 계산·비용 재시퀀싱·구간 절단·가설 판정 임계값)만
검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan70_random_control_b import RandomControlBResult
from backtest.wan71_edge_decomposition import (
    COST_MULTIPLIERS,
    BuyHoldRow,
    CostSensitivityRow,
    build_conclusion_paragraph,
    build_hypothesis_table,
    build_summary_markdown,
    buy_hold_return,
    compute_hypothesis_evidence,
    run_experiment,
    run_symbol_timeframe,
)
from common.costs import Liquidity
from data.models import Candle
from data.storage import OhlcvStore

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


def _synthetic_pair(bars: int = 1200, span: int = 500) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


# --------------------------------------------------------------------------- 바이앤홀드


def test_buy_hold_return_matches_hand_calculation() -> None:
    cfg = default_backtest_config("1h")
    closes = pd.Series([100.0, 110.0])
    result = buy_hold_return(closes, cfg)
    assert result is not None

    costs = cfg.cost_model
    entry_fill = costs.entry_fill(100.0, is_long=True, liquidity=Liquidity.TAKER)
    exit_fill = costs.exit_fill(110.0, is_long=True, liquidity=Liquidity.TAKER)
    qty = cfg.initial_capital / entry_fill
    entry_fee = costs.fee(entry_fill * qty, Liquidity.TAKER)
    exit_fee = costs.fee(exit_fill * qty, Liquidity.TAKER)
    expected = ((exit_fill - entry_fill) * qty - entry_fee - exit_fee) / cfg.initial_capital
    assert result == pytest.approx(expected)


def test_buy_hold_return_positive_when_price_rises_net_of_costs() -> None:
    cfg = default_backtest_config("1h")
    result = buy_hold_return(pd.Series([100.0, 200.0]), cfg)
    assert result is not None
    assert result > 0


def test_buy_hold_return_none_when_fewer_than_two_bars() -> None:
    cfg = default_backtest_config("1h")
    assert buy_hold_return(pd.Series([100.0]), cfg) is None
    assert buy_hold_return(pd.Series(dtype=float), cfg) is None


# --------------------------------------------------------------------------- 심볼×TF 전체 흐름


def test_run_symbol_timeframe_produces_random_cost_and_buy_hold_rows() -> None:
    htf, one_min = _synthetic_pair()
    random_results, cost_rows, buy_hold_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", iterations=10, seed=5
    )
    assert random_results  # 배선이 동작해 최소 1행 이상 산출된다.
    gates = {r.gate for r in random_results}
    assert gates == {"current", "with_short"}
    segments = {r.segment for r in random_results}
    assert segments <= {"IS", "OOS"}

    assert buy_hold_rows  # 세그먼트당 1행.
    bh_segments = {r.segment for r in buy_hold_rows}
    assert bh_segments <= {"IS", "OOS"}
    for r in buy_hold_rows:
        assert r.symbol == "BTC/USDT:USDT"
        assert r.timeframe == "1h"

    if cost_rows:
        multipliers = {r.cost_multiplier for r in cost_rows}
        assert multipliers == set(COST_MULTIPLIERS)


def test_run_symbol_timeframe_too_few_bars_returns_empty() -> None:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=10, seed=1)
    one_min = make_synthetic_ohlcv(timeframe="1m", bars=50, seed=2)
    random_results, cost_rows, buy_hold_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h"
    )
    assert random_results == []
    assert cost_rows == []
    assert buy_hold_rows == []


def test_cost_sensitivity_return_is_monotonic_non_increasing_in_multiplier() -> None:
    """수수료·슬리피지를 올리면 총수익률은 (같은 거래 집합 기준) 늘어날 수 없다."""
    htf, one_min = _synthetic_pair()
    _random_results, cost_rows, _buy_hold_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", iterations=5, seed=5
    )
    by_segment: dict[str, list[CostSensitivityRow]] = {}
    for row in cost_rows:
        by_segment.setdefault(row.segment, []).append(row)
    for rows in by_segment.values():
        if not rows or rows[0].num_trades == 0:
            continue
        ordered = sorted(rows, key=lambda r: r.cost_multiplier)
        returns = [r.total_return for r in ordered]
        assert returns == sorted(returns, reverse=True)


# --------------------------------------------------------------------------- run_experiment 병렬


def _populate_synthetic_store(db_path: Path, symbols: tuple[str, ...]) -> None:
    with OhlcvStore(db_path) as store:
        for i, symbol in enumerate(symbols):
            htf = make_synthetic_ohlcv(timeframe="1h", bars=1200, seed=7 + i)
            htf_ms = timeframe_to_ms("1h")
            start = int(htf["open_time"].iloc[-500])
            minutes = 500 * (htf_ms // 60_000)
            one_min = make_synthetic_ohlcv(
                timeframe="1m", bars=minutes, seed=11 + i, start_time_ms=start, swing_period=180
            )
            candles = [
                Candle(
                    symbol,
                    "1h",
                    int(row.open_time),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    bool(row.closed),
                )
                for row in htf.itertuples(index=False)
            ]
            candles += [
                Candle(
                    symbol,
                    "1m",
                    int(row.open_time),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    bool(row.closed),
                )
                for row in one_min.itertuples(index=False)
            ]
            store.upsert_candles(candles)


def test_run_experiment_parallel_jobs_matches_sequential(tmp_path: Path) -> None:
    db_path = tmp_path / "ohlcv.db"
    symbols = ("BTC/USDT:USDT", "ETH/USDT:USDT")
    _populate_synthetic_store(db_path, symbols)

    sequential = run_experiment(
        db_path=db_path,
        symbols=symbols,
        timeframes=("1h",),
        years=1.0,
        iterations=15,
        jobs=1,
        cache_dir=None,
    )
    parallel = run_experiment(
        db_path=db_path,
        symbols=symbols,
        timeframes=("1h",),
        years=1.0,
        iterations=15,
        jobs=2,
        cache_dir=None,
    )
    assert len(sequential.random_results) > 0
    assert [r.model_dump() for r in sequential.random_results] == [
        r.model_dump() for r in parallel.random_results
    ]
    assert [r.model_dump() for r in sequential.cost_rows] == [
        r.model_dump() for r in parallel.cost_rows
    ]
    assert [r.model_dump() for r in sequential.buy_hold_rows] == [
        r.model_dump() for r in parallel.buy_hold_rows
    ]


# --------------------------------------------------------------------------- 가설 판정


def _bh_row(buy_hold_return_: float) -> BuyHoldRow:
    return BuyHoldRow(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="OOS",
        buy_hold_return=buy_hold_return_,
        num_bars=100,
    )


def _random_row(**overrides: object) -> RandomControlBResult:
    base: dict[str, object] = dict(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="OOS",
        gate="current",
        real_total_return=0.5,
        real_num_trades=40,
        real_long=25,
        real_short=15,
        pool_size=120,
        random_mean_return=0.4,
        random_ci_low=-0.05,
        random_ci_high=0.6,
        random_p_value=0.6,
        iterations=200,
        bucket_fallback_count=0,
    )
    base.update(overrides)
    return RandomControlBResult(**base)


def test_compute_hypothesis_evidence_aggregates_only_eligible_oos_current_cells() -> None:
    rows = [
        _random_row(symbol="BTC/USDT:USDT", timeframe="1h", real_num_trades=40),
        _random_row(symbol="ETH/USDT:USDT", timeframe="1h", real_num_trades=5),  # 소표본 제외
        _random_row(symbol="BTC/USDT:USDT", timeframe="1h", segment="IS"),  # IS 제외
        _random_row(gate="with_short", real_total_return=0.1),
    ]
    buy_hold_rows = [
        BuyHoldRow(
            symbol="BTC/USDT:USDT", timeframe="1h", segment="OOS", buy_hold_return=0.2, num_bars=100
        ),
        BuyHoldRow(
            symbol="ETH/USDT:USDT", timeframe="1h", segment="OOS", buy_hold_return=0.9, num_bars=100
        ),
    ]
    cost_rows = [
        CostSensitivityRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment="OOS",
            cost_multiplier=mult,
            total_return=0.5 - 0.1 * mult,
            num_trades=40,
        )
        for mult in COST_MULTIPLIERS
    ]

    evidence = compute_hypothesis_evidence(rows, cost_rows, buy_hold_rows)
    assert evidence.num_eligible_cells == 1
    assert evidence.real_mean == pytest.approx(0.5)
    assert evidence.random_mean == pytest.approx(0.4)
    assert evidence.buy_hold_mean == pytest.approx(0.2)  # ETH 셀은 소표본이라 제외됨
    assert evidence.with_short_mean == pytest.approx(0.1)


def test_hypothesis_1_supported_when_random_mean_close_to_real() -> None:
    rows = [_random_row(real_total_return=0.5, random_mean_return=0.45)]
    buy_hold_rows = [_bh_row(0.1)]
    cost_rows = [
        CostSensitivityRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment="OOS",
            cost_multiplier=mult,
            total_return=0.4,
            num_trades=40,
        )
        for mult in COST_MULTIPLIERS
    ]
    evidence = compute_hypothesis_evidence(rows, cost_rows, buy_hold_rows)
    table = build_hypothesis_table(evidence)
    assert "지지" in table
    conclusion = build_conclusion_paragraph(evidence)
    assert "가설 1" in conclusion


def test_hypothesis_2_dominant_when_buy_hold_beats_strategy() -> None:
    rows = [_random_row(real_total_return=0.2, random_mean_return=0.15)]
    buy_hold_rows = [_bh_row(0.8)]
    cost_rows = [
        CostSensitivityRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment="OOS",
            cost_multiplier=mult,
            total_return=0.15,
            num_trades=40,
        )
        for mult in COST_MULTIPLIERS
    ]
    evidence = compute_hypothesis_evidence(rows, cost_rows, buy_hold_rows)
    conclusion = build_conclusion_paragraph(evidence)
    assert "가설 2" in conclusion


def test_hypothesis_3_supported_when_cost_flips_sign() -> None:
    rows = [_random_row(real_total_return=0.1, random_mean_return=0.02)]
    buy_hold_rows = [_bh_row(-0.1)]
    cost_rows = [
        CostSensitivityRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment="OOS",
            cost_multiplier=1.0,
            total_return=0.1,
            num_trades=40,
        ),
        CostSensitivityRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment="OOS",
            cost_multiplier=1.5,
            total_return=-0.02,
            num_trades=40,
        ),
        CostSensitivityRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment="OOS",
            cost_multiplier=2.0,
            total_return=-0.08,
            num_trades=40,
        ),
    ]
    evidence = compute_hypothesis_evidence(rows, cost_rows, buy_hold_rows)
    conclusion = build_conclusion_paragraph(evidence)
    assert "가설 3" in conclusion


def test_compute_hypothesis_evidence_handles_no_eligible_cells() -> None:
    evidence = compute_hypothesis_evidence([], [], [])
    assert evidence.num_eligible_cells == 0
    assert evidence.real_mean is None
    conclusion = build_conclusion_paragraph(evidence)
    assert "판정 불가" in conclusion


# --------------------------------------------------------------------------- 리포트 산출


def test_build_summary_markdown_is_self_contained() -> None:
    random_results = [_random_row(), _random_row(gate="with_short", real_total_return=0.1)]
    buy_hold_rows = [_bh_row(0.2)]
    cost_rows = [
        CostSensitivityRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment="OOS",
            cost_multiplier=mult,
            total_return=0.4,
            num_trades=40,
        )
        for mult in COST_MULTIPLIERS
    ]
    md = build_summary_markdown(
        random_results,
        cost_rows,
        buy_hold_rows,
        random_report_path=Path("backtest/reports/wan71_random_entry_current_exit.csv"),
        cost_report_path=Path("backtest/reports/wan71_cost_sensitivity.csv"),
        buy_hold_report_path=Path("backtest/reports/wan71_buy_hold.csv"),
    )
    assert "## 무작위 진입 + 현행 청산" in md
    assert "## 숏 포함 변형" in md
    assert "## 비용 민감도" in md
    assert "## 바이앤홀드 벤치마크" in md
    assert "## 가설별 판정" in md
    assert "## 결론" in md
    assert "BTC/USDT:USDT" in md
    assert "0.003" in md  # min_stop_distance_fraction 각주
