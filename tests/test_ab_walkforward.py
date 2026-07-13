"""진입 방식(A/B) 워크포워드/OOS 검증 테스트 (WAN-50).

`backtest.ab_walkforward`가 (1) 윈도우×변형(A/B)별 IS/OOS 행을 만들고, (2) 재현
가능하며, (3) 어떤 윈도우의 결과도 그 윈도우의 `oos_end` 이후(미래) 상위TF·1분봉이
바뀌어도 변하지 않는지(룩어헤드 없음)를 검증한다. WAN-22의 룩어헤드 테스트를 A/B
축(상위TF + 1분봉 두 입력)으로 확장한 것이다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.ab_walkforward import (
    ABWalkForwardRow,
    run_ab_walk_forward,
)
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv


def _aligned_1m(htf: pd.DataFrame, timeframe: str, *, seed: int) -> pd.DataFrame:
    """`htf` 전체 구간을 커버하는 결정적 1분봉을 만든다(각 상위TF 봉을 1분봉으로 채움).

    1분봉 값은 인덱스의 결정적 함수라 상위TF 가격과 무관하다 — 룩어헤드 테스트는 값의
    일치가 아니라 **미래 데이터가 과거 윈도우 결과를 바꾸지 않음**을 보므로 충분하다.
    """
    htf_ms = timeframe_to_ms(timeframe)
    minutes_per_bar = htf_ms // 60_000
    n_minutes = len(htf) * minutes_per_bar
    start = int(htf["open_time"].iloc[0])
    return make_synthetic_ohlcv(
        timeframe="1m", bars=n_minutes, start_time_ms=start, seed=seed, swing_period=180
    )


def _extend_with_independent_tail(
    base: pd.DataFrame, *, keep_bars: int, tail_bars: int, timeframe: str, tail_seed: int
) -> pd.DataFrame:
    """`base`의 앞 `keep_bars`개는 그대로 두고, 다른 시드의 독립 구간을 이어붙인다
    (시간축 연속). WAN-22 워크포워드 룩어헤드 테스트와 동일한 구성."""
    head = base.iloc[:keep_bars].reset_index(drop=True)
    tail = make_synthetic_ohlcv(bars=tail_bars, timeframe=timeframe, seed=tail_seed)
    step = timeframe_to_ms(timeframe)
    offset = int(head["open_time"].iloc[-1]) + step - int(tail["open_time"].iloc[0])
    tail = tail.assign(open_time=tail["open_time"] + offset)
    return pd.concat([head, tail], ignore_index=True)


# --------------------------------------------------------------------------- 구조·재현성


def test_run_ab_walk_forward_produces_two_variants_per_window() -> None:
    htf = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=7)
    one_min = _aligned_1m(htf, "1h", seed=11)
    report = run_ab_walk_forward(
        htf, one_min, symbol="X", timeframe="1h", is_bars=150, oos_bars=80, warmup_bars=40
    )
    # generate_windows(400, is=150, oos=80, step=80): is_start in [0, 80, 160] → 3 윈도우.
    assert len(report.rows) == 3 * 2  # 윈도우당 A/B 두 행
    variants = {r.variant for r in report.rows}
    assert variants == {"A", "B"}
    for row in report.rows:
        assert row.symbol == "X"
        assert row.timeframe == "1h"
        assert row.is_num_bars == 150
        assert row.oos_num_bars == 80
        # A안에는 지정가 체결률·관통 개념이 없어 항상 None.
        if row.variant == "A":
            assert row.oos_fill_rate is None
            assert row.oos_num_penetrations is None


def test_run_ab_walk_forward_is_deterministic() -> None:
    htf = make_synthetic_ohlcv(bars=360, timeframe="1h", seed=9)
    one_min = _aligned_1m(htf, "1h", seed=13)
    a = run_ab_walk_forward(
        htf, one_min, symbol="X", timeframe="1h", is_bars=150, oos_bars=80, warmup_bars=40
    )
    b = run_ab_walk_forward(
        htf, one_min, symbol="X", timeframe="1h", is_bars=150, oos_bars=80, warmup_bars=40
    )
    assert [r.model_dump() for r in a.rows] == [r.model_dump() for r in b.rows]


def test_run_ab_walk_forward_empty_when_data_too_short() -> None:
    htf = make_synthetic_ohlcv(bars=100, timeframe="1h", seed=1)
    one_min = _aligned_1m(htf, "1h", seed=5)
    report = run_ab_walk_forward(htf, one_min, symbol="X", timeframe="1h", is_bars=150, oos_bars=80)
    assert report.rows == []
    assert report.to_dataframe().empty
    assert report.summary_dataframe().empty


def test_summary_dataframe_has_variant_rows_and_gap_columns() -> None:
    htf = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=3)
    one_min = _aligned_1m(htf, "1h", seed=17)
    report = run_ab_walk_forward(
        htf, one_min, symbol="X", timeframe="1h", is_bars=150, oos_bars=80, warmup_bars=40
    )
    summary = report.summary_dataframe()
    assert set(summary["variant"]) == {"A", "B"}
    for col in ("mean_oos_total_return", "mean_return_gap", "mean_profit_factor_gap"):
        assert col in summary.columns
    frame = report.to_dataframe()
    for col in ("return_gap", "profit_factor_gap", "variant", "oos_profit_factor"):
        assert col in frame.columns


def test_ab_walk_forward_row_gap_properties() -> None:
    base = {
        "window_index": 0,
        "symbol": "X",
        "timeframe": "1h",
        "variant": "B",
        "is_start_time": 0,
        "is_end_time": 1,
        "oos_start_time": 2,
        "oos_end_time": 3,
        "is_num_bars": 100,
        "oos_num_bars": 50,
        "is_total_return": 0.2,
        "is_max_drawdown": 0.05,
        "is_win_rate": 0.6,
        "is_profit_factor": 1.5,
        "is_sharpe": 1.2,
        "is_num_trades": 10,
        "is_avg_trade_return": 0.01,
        "is_fill_rate": 0.4,
        "is_num_penetrations": 1,
        "oos_total_return": 0.05,
        "oos_max_drawdown": 0.1,
        "oos_win_rate": 0.4,
        "oos_profit_factor": 0.9,
        "oos_sharpe": None,
        "oos_num_trades": 4,
        "oos_avg_trade_return": -0.002,
        "oos_fill_rate": 0.33,
        "oos_num_penetrations": 0,
    }
    row = ABWalkForwardRow(**base)
    assert row.return_gap == pytest.approx(0.15)
    assert row.profit_factor_gap == pytest.approx(0.6)
    row_no_pf = row.model_copy(update={"oos_profit_factor": None})
    assert row_no_pf.profit_factor_gap is None


# --------------------------------------------------------------------- 룩어헤드(미래 누수) 없음


def test_window0_unaffected_by_future_htf_and_1m() -> None:
    """윈도우0의 oos_end 이후 상위TF·1분봉을 완전히 다른 값으로 바꿔도, 윈도우0의
    A/B IS·OOS 성과가 완전히 동일해야 한다(미래 데이터 누수 없음)."""
    timeframe = "1h"
    is_bars, oos_bars, warmup_bars = 150, 80, 50
    base = make_synthetic_ohlcv(bars=400, timeframe=timeframe, seed=11)
    window0_oos_end = is_bars + oos_bars  # 230

    df_short = base.iloc[:window0_oos_end].reset_index(drop=True)
    df_long = _extend_with_independent_tail(
        base,
        keep_bars=window0_oos_end,
        tail_bars=170,
        timeframe=timeframe,
        tail_seed=999,  # base와 무관한 미래 값
    )
    assert len(df_long) == 400
    pd.testing.assert_frame_equal(
        df_short.reset_index(drop=True), df_long.iloc[:window0_oos_end].reset_index(drop=True)
    )

    # 1분봉: 같은 시드·시작으로 만들면 짧은/긴 프레임의 앞부분이 동일하고(과거),
    # 긴 프레임의 뒤(미래)만 더 길다.
    one_min_short = _aligned_1m(df_short, timeframe, seed=23)
    one_min_long = _aligned_1m(df_long, timeframe, seed=23)
    overlap = len(one_min_short)
    pd.testing.assert_frame_equal(
        one_min_short.reset_index(drop=True),
        one_min_long.iloc[:overlap].reset_index(drop=True),
    )

    report_short = run_ab_walk_forward(
        df_short,
        one_min_short,
        symbol="X",
        timeframe=timeframe,
        is_bars=is_bars,
        oos_bars=oos_bars,
        warmup_bars=warmup_bars,
    )
    report_long = run_ab_walk_forward(
        df_long,
        one_min_long,
        symbol="X",
        timeframe=timeframe,
        is_bars=is_bars,
        oos_bars=oos_bars,
        warmup_bars=warmup_bars,
    )

    assert len(report_short.rows) == 2  # 윈도우 1개 × A/B
    assert len(report_long.rows) > 2  # 미래 데이터로 윈도우가 더 생긴다

    short_window0 = {r.variant: r.model_dump() for r in report_short.rows}
    long_window0 = {r.variant: r.model_dump() for r in report_long.rows if r.window_index == 0}
    assert short_window0["A"] == long_window0["A"]
    assert short_window0["B"] == long_window0["B"]
