"""backtest.wan176_nine_symbol_rebaseline 테스트 (WAN-176).

이 파일이 지키는 것은 다섯이다:

1. **좌표가 이슈의 결정 그대로다** — 9종목 = 기존 6 + 신규 3, 창은 SOL 상장 하한의
   균일 창, 옛 창 검산 좌표는 판정 계열과 동일.
2. **기계를 남에게서 가져온다** — 널은 wan151(팔·풀·시드·반복수), 존폭은 wan142
   (팔·시드·문턱 규칙), 격자는 표준 CLI 파서 경로. 여기서 다시 정의하면 옛 창 검산이
   배선 검산이 되지 못한다.
3. **§3은 이중 필터를 끈다** — 채택 기본값 1.28이 엔진에서 먼저 걸러 버리면 매칭
   대조군이 오염된다(WAN-159 §파급의 함정).
4. **판정·게이트가 행에서 계산된다** — 표본 게이트(WAN-109)는 주의문이 아니라 코드다.
5. **검산이 일치·잡음·불일치를 다르게 찍는다**(WAN-151/161 패턴).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest import harness
from backtest import run as runcli
from backtest import wan142_zone_width_filter_verdict as wan142
from backtest import wan151_split_zone_null as wan151
from backtest.wan176_nine_symbol_rebaseline import (
    DEFAULT_END,
    DEFAULT_START,
    GRID_TIMEFRAMES,
    MIN_TRADES_FOR_VERDICT,
    NEW_SYMBOLS,
    NINE_SYMBOLS,
    OLD_END,
    OLD_START,
    OLD_SYMBOLS,
    VerifyRow,
    _classify,
    compare_frames,
    describe_engine,
    grid_argv,
    leave_one_out_lines,
    null_tasks,
    positions_verdict,
    sample_gate_verdict,
    tf_ranking,
    verify_rows_from_csv,
    zone_width_verdict,
    zw_params,
)
from strategy.models import ConfluenceParams

# ------------------------------------------------------- 1. 좌표


def test_universe_is_old_six_plus_new_three_in_order() -> None:
    assert NINE_SYMBOLS == OLD_SYMBOLS + NEW_SYMBOLS
    assert len(NINE_SYMBOLS) == 9
    assert NEW_SYMBOLS == ("DOGEUSDT", "LINKUSDT", "LTCUSDT")


def test_window_is_uniform_and_sol_bounded() -> None:
    """공통 시작점 = SOL 상장 하한(균일 창 원칙, WAN-111). 끝은 마지막 완결 하루."""
    assert DEFAULT_START == "2020-09-15"
    assert DEFAULT_END == "2026-07-22"


def test_old_window_matches_verdict_family() -> None:
    """옛 창 검산 좌표는 WAN-111 이래 판정 계열의 못 박은 창과 같아야 한다."""
    assert (OLD_START, OLD_END) == (wan151.DEFAULT_START, wan151.DEFAULT_END)


def test_grid_covers_work_tfs_and_sample_retrial_tfs() -> None:
    assert GRID_TIMEFRAMES == ("15m", "1h", "4h", "1d")


def test_engine_is_adopted_default_with_baseline_lens() -> None:
    text = describe_engine()
    assert "combine_obs=False" in text
    assert "band_bar=intrabar_live" in text
    assert "lens=baseline" in text


# ------------------------------------------------------- 2. 기계를 남에게서


def test_null_tasks_reuse_wan151_machinery() -> None:
    tasks = null_tasks(symbols=("BTCUSDT",), timeframes=("1h",))
    assert len(tasks) == 1
    task = tasks[0]
    assert isinstance(task, wan151._Task)
    assert task.iterations == wan151.BOOTSTRAP_ITERATIONS == 200
    assert task.arm_names == (wan151.LONG_ARM,)
    assert task.start_ms == runcli.parse_date_ms(DEFAULT_START)
    assert task.end_ms == runcli.parse_date_ms(DEFAULT_END)


def test_null_seed_and_ruler_match_verdict_family() -> None:
    assert wan151.BOOTSTRAP_SEED == 124
    assert MIN_TRADES_FOR_VERDICT == wan151.MIN_TRADES_FOR_VERDICT == 20


def test_grid_argv_is_cli_compatible() -> None:
    """만든 인자 목록이 실제 CLI 파서를 통과해 같은 격자가 된다."""
    argv = grid_argv(
        symbols=("BTCUSDT",),
        timeframes=("1h",),
        start=DEFAULT_START,
        end=DEFAULT_END,
        warm=True,
    )
    args = runcli.build_parser().parse_args(argv)
    grid = runcli.grid_from_args(args)
    options = runcli.options_from_args(args)
    assert grid.symbols == ("BTC/USDT:USDT",)
    assert grid.timeframes == ("1h",)
    assert options.warm_oos is True
    assert options.oos is False
    # 축을 열지 않았으므로 채택 기본값 그대로(미지정 센티넬 · 단일 포지션).
    assert grid.max_zone_widths_atr == (harness.UNSET,)
    assert grid.portfolio_leverages == (None,)


def test_grid_argv_positions_axis() -> None:
    argv = grid_argv(
        symbols=("BTCUSDT",),
        timeframes=("1h",),
        start=DEFAULT_START,
        end=DEFAULT_END,
        warm=False,
        positions="single,3",
    )
    args = runcli.build_parser().parse_args(argv)
    grid = runcli.grid_from_args(args)
    options = runcli.options_from_args(args)
    assert options.oos is True and options.warm_oos is False
    assert grid.portfolio_leverages == (None, 3.0)


# ------------------------------------------------------- 3. 이중 필터 방지


def test_zw_params_turn_the_adopted_filter_off() -> None:
    """§3 엔진은 존폭 필터를 명시적으로 끈다 — 켜져 있으면 매칭 대조군이 오염된다."""
    params = zw_params()
    assert params.max_zone_width_atr is None
    assert ConfluenceParams().max_zone_width_atr == 1.28  # 채택 기본값은 그대로다
    assert params.deviation_filter is not None  # 볼린저는 끄지 않는다(§2와 다른 축)
    assert wan142.LENS_PRIMARY == "baseline"


# ------------------------------------------------------- 4. 판정·게이트


def _grid_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    base: dict[str, object] = {
        "position_mode": "single",
        "max_drawdown": 0.1,
        "win_rate": 0.5,
        "fill_rate": 0.8,
    }
    return pd.DataFrame([{**base, **row} for row in rows])


def test_sample_gate_blocks_thin_cells() -> None:
    frame = _grid_frame(
        [
            {
                "symbol": "A",
                "timeframe": "1d",
                "segment": "oos",
                "num_trades": 5,
                "total_return": 0.1,
            },
            {
                "symbol": "B",
                "timeframe": "1d",
                "segment": "oos",
                "num_trades": 7,
                "total_return": 0.1,
            },
        ]
    )
    assert "판정 불가" in sample_gate_verdict(frame, timeframe="1d", segment="oos")


def test_sample_gate_passes_when_resolved() -> None:
    frame = _grid_frame(
        [
            {
                "symbol": "A",
                "timeframe": "4h",
                "segment": "oos",
                "num_trades": 30,
                "total_return": 0.1,
            },
            {
                "symbol": "B",
                "timeframe": "4h",
                "segment": "oos",
                "num_trades": 25,
                "total_return": 0.1,
            },
        ]
    )
    assert "표본 미달 해소" in sample_gate_verdict(frame, timeframe="4h", segment="oos")


def test_tf_ranking_sorts_by_mean_return() -> None:
    frame = _grid_frame(
        [
            {
                "symbol": "A",
                "timeframe": "1h",
                "segment": "oos_warm",
                "num_trades": 30,
                "total_return": 0.30,
            },
            {
                "symbol": "A",
                "timeframe": "15m",
                "segment": "oos_warm",
                "num_trades": 30,
                "total_return": 0.10,
            },
        ]
    )
    ranking = tf_ranking(frame, segment="oos_warm")
    assert [tf for tf, _ in ranking] == ["1h", "15m"]


def test_leave_one_out_reports_new_symbol_shift() -> None:
    rows = [
        {
            "symbol": harness.normalize_symbol(symbol),
            "timeframe": "1h",
            "segment": "oos_warm",
            "num_trades": 30,
            "total_return": 0.10 if symbol in OLD_SYMBOLS else -0.20,
        }
        for symbol in NINE_SYMBOLS
    ]
    lines = leave_one_out_lines(_grid_frame(rows), timeframe="1h", segment="oos_warm")
    text = "\n".join(lines)
    for symbol in NINE_SYMBOLS:
        assert f"−{symbol.replace('USDT', '')}" in text
    assert "기존 6종목만 평균" in text
    assert "%p 이동" in text


def test_positions_verdict_names_the_winner_per_tf() -> None:
    frame = pd.DataFrame(
        [
            {
                "position_mode": "single",
                "timeframe": "15m",
                "segment": "oos",
                "total_return": 0.10,
                "max_drawdown": 0.10,
            },
            {
                "position_mode": "multi",
                "timeframe": "15m",
                "segment": "oos",
                "total_return": 0.20,
                "max_drawdown": 0.15,
            },
            {
                "position_mode": "single",
                "timeframe": "1h",
                "segment": "oos",
                "total_return": 0.10,
                "max_drawdown": 0.10,
            },
            {
                "position_mode": "multi",
                "timeframe": "1h",
                "segment": "oos",
                "total_return": 0.05,
                "max_drawdown": 0.12,
            },
        ]
    )
    text = positions_verdict(frame)
    assert "15m 다중 승" in text
    assert "1h 단일 승" in text


def _test_row(*, p: float | None, filt: float, matched: float) -> wan142.MatchedTestRow:
    return wan142.MatchedTestRow(
        lens="baseline",
        timeframe="1h",
        segment="oos",
        n_symbols=9,
        n_seeds=20,
        filter_return=filt,
        matched_return_mean=matched,
        p_return=p,
        filter_mdd=0.05,
        matched_mdd_mean=0.10,
        p_mdd=p,
        default_mdd=0.12,
        sample_share=0.3,
        filter_trades=100.0,
        matched_trades=105.0,
        trade_gap_pct=5.0,
    )


def test_zone_width_verdict_all_significant() -> None:
    tests = [_test_row(p=0.048, filt=0.2, matched=0.05)]
    assert "전 셀 유의" in zone_width_verdict(tests)


def test_zone_width_verdict_collapse() -> None:
    tests = [_test_row(p=0.5, filt=0.05, matched=0.2)]
    assert "무너진다" in zone_width_verdict(tests)


def test_zone_width_verdict_requires_direction_not_just_p() -> None:
    """p가 낮아도 필터가 매칭보다 못 벌면 유의로 세지 않는다."""
    tests = [_test_row(p=0.048, filt=0.01, matched=0.2)]
    assert "유의 0개" in zone_width_verdict(tests)


# ------------------------------------------------------- 5. 검산 분류


def test_classify_distinguishes_exact_noise_mismatch() -> None:
    assert _classify(10, 0.0, mismatch_note="x")[0] == "일치"
    assert _classify(10, 1e-15, mismatch_note="x")[0] == "잡음"
    assert _classify(10, 1e-3, mismatch_note="x")[0] == "불일치"
    assert _classify(0, None, mismatch_note="x")[0] == "불일치"


def test_compare_frames_max_abs_diff_and_missing_keys() -> None:
    ours = pd.DataFrame(
        [
            {"k": "a", "v": 1.0},
            {"k": "b", "v": 2.0},
        ]
    )
    ref = pd.DataFrame(
        [
            {"k": "a", "v": 1.0},
            {"k": "b", "v": 2.5},
            {"k": "c", "v": 9.0},
        ]
    )
    compared, diff = compare_frames(ours, ref, keys=("k",), numeric_columns=("v",))
    assert compared == 2  # "c"는 비교 대상에 못 든다 — 호출부가 기대 행 수와 대조한다
    assert diff == 0.5


def test_compare_frames_one_sided_nan_is_mismatch() -> None:
    ours = pd.DataFrame([{"k": "a", "v": float("nan")}])
    ref = pd.DataFrame([{"k": "a", "v": 1.0}])
    compared, diff = compare_frames(ours, ref, keys=("k",), numeric_columns=("v",))
    assert compared == 1
    assert diff == float("inf")


def test_compare_frames_both_nan_is_equal() -> None:
    ours = pd.DataFrame([{"k": "a", "v": float("nan")}])
    ref = pd.DataFrame([{"k": "a", "v": float("nan")}])
    compared, diff = compare_frames(ours, ref, keys=("k",), numeric_columns=("v",))
    assert compared == 1
    assert diff == 0.0


def test_verify_rows_round_trip(tmp_path: Path) -> None:
    rows = [
        VerifyRow(
            check="grid-1h",
            reference="x.csv",
            rows_compared=24,
            max_abs_diff=0.0,
            status="일치",
            note="차이 0",
        ),
        VerifyRow(
            check="null-long",
            reference="y.csv",
            rows_compared=24,
            max_abs_diff=None,
            status="일치",
            note="",
        ),
    ]
    path = tmp_path / "verify.csv"
    pd.DataFrame([r.model_dump() for r in rows]).to_csv(path, index=False)
    assert verify_rows_from_csv(path) == rows
