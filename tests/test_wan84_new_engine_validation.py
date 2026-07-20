"""backtest.wan84_new_engine_validation 단위/스모크 테스트 (WAN-84).

3심볼×4TF 전체 실데이터 산출(매칭 널·비용 민감도·롱숏 분해·바이앤홀드·워크포워드)은
`backtest/reports/wan84_*.csv`·`wan84_summary.md`(재현: `python -m
backtest.wan84_new_engine_validation`)로 별도 확인한다. 여기서는 결정적 합성 데이터로
핵심 로직(신 엔진 게이트 정의·롱숏 분해·판정 임계값·병렬 재현성)만 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from backtest.harness import LEGACY_BAND_BAR
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan70_random_control_b import RandomControlBResult
from backtest.wan71_edge_decomposition import COST_MULTIPLIERS, BuyHoldRow, CostSensitivityRow
from backtest.wan84_new_engine_validation import (
    GATES,
    NEW_ENGINE_PARAMS,
    SideBreakdownRow,
    build_summary_markdown,
    build_verdict,
    run_experiment,
    run_symbol_timeframe,
    summarize_short_drag,
)
from data.models import Candle
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams

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


# --------------------------------------------------------------------------- 신 엔진 게이트 정의


def test_new_engine_params_match_confluence_params_defaults_except_wiring() -> None:
    """`NEW_ENGINE_PARAMS`는 B안 배선(entry_mode/rsi_mode)과 **기본값이 이 정의에서
    멀어진 세 필드**(`short_enabled`·`rsi_gate_mode`·`band_bar`)만 명시 고정하고, 나머지는
    `ConfluenceParams()`를 그대로 물려받아야 한다(모듈 docstring 핵심 주장).

    고정 이유는 같다 — 기본값이 움직여도 이 리포트가 검증하는 "숏 활성화 신 엔진" 정의를
    보존한다: `short_enabled`는 WAN-87(WAN-86 결정 1)이 `False`로 되돌렸고,
    `rsi_gate_mode`는 WAN-123(WAN-116 결정 B)이 `unconditional`로, `band_bar`는
    WAN-132(사용자 결정)가 `intrabar_live`로 옮겼다."""
    defaults = ConfluenceParams()
    assert NEW_ENGINE_PARAMS.entry_mode == "zone_limit"
    assert NEW_ENGINE_PARAMS.rsi_mode == "realtime"
    assert NEW_ENGINE_PARAMS.retap_mode == defaults.retap_mode == "every_tap"
    assert NEW_ENGINE_PARAMS.rsi_gate_mode == "first_tap_free" != defaults.rsi_gate_mode
    assert NEW_ENGINE_PARAMS.take_profit_mode == defaults.take_profit_mode == "fixed_r"
    assert NEW_ENGINE_PARAMS.take_profit_r == defaults.take_profit_r == 1.5
    assert defaults.short_enabled is False
    assert NEW_ENGINE_PARAMS.short_enabled is True
    # 밴드 표본만 빼면 볼린저 정의(SMA20 ± 2σ)는 기본값 그대로다(WAN-132).
    assert defaults.deviation_filter is not None
    assert NEW_ENGINE_PARAMS.deviation_filter == defaults.deviation_filter.model_copy(
        update={"band_bar": LEGACY_BAND_BAR}
    )
    assert NEW_ENGINE_PARAMS.deviation_filter is not None
    assert (
        NEW_ENGINE_PARAMS.deviation_filter.band_bar
        == LEGACY_BAND_BAR
        != defaults.deviation_filter.band_bar
    )


def test_gates_has_single_new_engine_gate() -> None:
    assert set(GATES) == {"new_engine"}
    assert GATES["new_engine"] is NEW_ENGINE_PARAMS


# --------------------------------------------------------------------------- 심볼×TF 전체 흐름


def test_run_symbol_timeframe_produces_all_four_outputs() -> None:
    htf, one_min = _synthetic_pair()
    random_results, cost_rows, buy_hold_rows, side_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", iterations=10, seed=5
    )
    assert random_results
    assert {r.gate for r in random_results} == {"new_engine"}
    assert {r.segment for r in random_results} <= {"IS", "OOS"}

    assert buy_hold_rows
    for r in buy_hold_rows:
        assert r.symbol == "BTC/USDT:USDT"
        assert r.timeframe == "1h"

    if cost_rows:
        assert {r.cost_multiplier for r in cost_rows} == set(COST_MULTIPLIERS)

    if side_rows:
        assert {r.side for r in side_rows} <= {"long", "short"}
        for side_row in side_rows:
            assert side_row.symbol == "BTC/USDT:USDT"
            assert side_row.timeframe == "1h"


def test_run_symbol_timeframe_too_few_bars_returns_empty() -> None:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=10, seed=1)
    one_min = make_synthetic_ohlcv(timeframe="1m", bars=50, seed=2)
    random_results, cost_rows, buy_hold_rows, side_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h"
    )
    assert random_results == []
    assert cost_rows == []
    assert buy_hold_rows == []
    assert side_rows == []


def test_side_breakdown_total_trades_le_combined_trades() -> None:
    """롱+숏 거래수 합은 비용 1.0x 셀의 전체 거래수(cost_rows) 이하여야 한다(분해가
    거래를 만들어내거나 잃지 않는지 검증)."""
    htf, one_min = _synthetic_pair()
    _random_results, cost_rows, _buy_hold_rows, side_rows = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", iterations=5, seed=5
    )
    combined_by_segment: dict[str, int] = {}
    for cost_row in cost_rows:
        if cost_row.cost_multiplier == 1.0:
            combined_by_segment[cost_row.segment] = cost_row.num_trades
    side_by_segment: dict[str, int] = {}
    for side_row in side_rows:
        side_by_segment[side_row.segment] = (
            side_by_segment.get(side_row.segment, 0) + side_row.num_trades
        )
    for segment, total in side_by_segment.items():
        assert total == combined_by_segment.get(segment, 0)


def test_cost_sensitivity_return_is_monotonic_non_increasing_in_multiplier() -> None:
    htf, one_min = _synthetic_pair()
    _random_results, cost_rows, _buy_hold_rows, _side_rows = run_symbol_timeframe(
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
    assert [r.model_dump() for r in sequential.side_rows] == [
        r.model_dump() for r in parallel.side_rows
    ]


# --------------------------------------------------------------------------- 판정


def _random_row(**overrides: object) -> RandomControlBResult:
    base: dict[str, object] = dict(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="OOS",
        gate="new_engine",
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


def test_build_verdict_no_edge_when_no_significant_cells() -> None:
    rows = [_random_row(random_p_value=0.6, real_total_return=0.5, random_mean_return=0.6)]
    verdict = build_verdict(rows)
    assert "엣지 없다" in verdict


def test_build_verdict_edge_when_all_significant() -> None:
    rows = [_random_row(random_p_value=0.01, real_total_return=0.5, random_mean_return=0.1)]
    verdict = build_verdict(rows)
    assert "엣지 있다" in verdict


def test_build_verdict_excludes_small_sample_cells() -> None:
    rows = [_random_row(real_num_trades=5, random_p_value=0.01, random_mean_return=0.1)]
    verdict = build_verdict(rows)
    assert "판정 불가" in verdict


def test_build_verdict_handles_no_valid_cells() -> None:
    assert "판정 불가" in build_verdict([])


def _side_row(
    side: Literal["long", "short"], total_return: float, num_trades: int = 20
) -> SideBreakdownRow:
    return SideBreakdownRow(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="OOS",
        side=side,
        total_return=total_return,
        num_trades=num_trades,
        win_rate=0.4,
    )


def test_summarize_short_drag_flags_when_short_drags_long_down() -> None:
    rows = [_side_row("long", 0.3), _side_row("short", -0.2)]
    summary = summarize_short_drag(rows)
    assert "숏이 롱 수익을 갉아먹는다" in summary


def test_summarize_short_drag_when_short_positive() -> None:
    rows = [_side_row("long", 0.3), _side_row("short", 0.1)]
    summary = summarize_short_drag(rows)
    assert "갉아먹지 않는다" in summary


def test_summarize_short_drag_insufficient_samples() -> None:
    rows = [_side_row("long", 0.3, num_trades=2), _side_row("short", -0.2, num_trades=2)]
    summary = summarize_short_drag(rows)
    assert "판정 불가" in summary


# --------------------------------------------------------------------------- 리포트 산출


def test_build_summary_markdown_is_self_contained() -> None:
    random_results = [_random_row()]
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
    buy_hold_rows = [
        BuyHoldRow(
            symbol="BTC/USDT:USDT", timeframe="1h", segment="OOS", buy_hold_return=0.2, num_bars=100
        )
    ]
    side_rows = [_side_row("long", 0.3), _side_row("short", -0.1)]

    md = build_summary_markdown(
        random_results,
        cost_rows,
        buy_hold_rows,
        side_rows,
        random_report_path=Path("backtest/reports/wan84_random_entry_new_engine.csv"),
        cost_report_path=Path("backtest/reports/wan84_cost_sensitivity.csv"),
        buy_hold_report_path=Path("backtest/reports/wan84_buy_hold.csv"),
        side_report_path=Path("backtest/reports/wan84_side_breakdown.csv"),
        walkforward_summary=None,
    )
    assert "## 무작위 진입 매칭 널" in md
    assert "## 비용 민감도" in md
    assert "## 롱/숏 분해" in md
    assert "## 바이앤홀드 벤치마크" in md
    assert "## OOS/워크포워드" in md
    assert "## 매칭 널 판정" in md
    assert "## 결론" in md
    assert "BTC/USDT:USDT" in md
    assert "건너뜀" in md  # walkforward_summary=None 경로


def test_build_summary_markdown_includes_walkforward_table() -> None:
    wf_summary = pd.DataFrame(
        [
            {
                "timeframe": "1h",
                "variant": "B",
                "num_windows": 3,
                "oos_num_trades": 12,
                "mean_oos_total_return": 0.05,
                "mean_oos_profit_factor": 1.2,
                "mean_oos_sharpe": 0.3,
                "mean_return_gap": 0.02,
                "mean_profit_factor_gap": 0.1,
                "mean_oos_fill_rate": 0.8,
            },
            {
                "timeframe": "1h",
                "variant": "A",
                "num_windows": 3,
                "oos_num_trades": 10,
                "mean_oos_total_return": 0.01,
                "mean_oos_profit_factor": 1.0,
                "mean_oos_sharpe": 0.1,
                "mean_return_gap": 0.03,
                "mean_profit_factor_gap": 0.05,
                "mean_oos_fill_rate": None,
            },
        ]
    )
    md = build_summary_markdown(
        [_random_row()],
        [],
        [],
        [],
        random_report_path=Path("r.csv"),
        cost_report_path=Path("c.csv"),
        buy_hold_report_path=Path("b.csv"),
        side_report_path=Path("s.csv"),
        walkforward_summary=wf_summary,
    )
    wf_section = md.split("## OOS/워크포워드")[1].split("## 매칭 널 판정")[0]
    assert "0.0500" in wf_section  # B 변형 mean_oos_total_return만 표에 실린다.
    assert "0.0100" not in wf_section  # A 변형은 제외(변형 B만 요약).
    assert "| 1h |" in wf_section
