"""워크포워드/OOS 검증 단위·스모크 테스트 (WAN-22).

`backtest.walkforward`(윈도우 경계 계산·롤링 IS 스윕→OOS 평가·리포트)를 검증한다.
핵심은 (1) 윈도우 경계가 겹치지 않고 미래를 침범하지 않는지, (2) 실행 결과가
재현 가능한지, (3) 어떤 윈도우의 결과도 그 윈도우의 `oos_end` 이후(미래) 데이터가
바뀌어도 변하지 않는지(룩어헤드 없음)다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.walkforward import (
    WalkForwardRow,
    generate_windows,
    run_walk_forward,
    write_walk_forward_csv,
)

# --------------------------------------------------------------------------- 윈도우 경계


def test_generate_windows_basic_boundaries() -> None:
    windows = generate_windows(300, is_bars=100, oos_bars=50)
    # 겹치지 않는 롤링(step 기본값=oos_bars): 0..150, 50..200, 100..250, 150..300
    assert [w.is_start for w in windows] == [0, 50, 100, 150]
    for w in windows:
        assert w.is_end - w.is_start == 100
        assert w.oos_end - w.oos_start == 50
        # OOS는 IS 바로 다음부터, 겹치지 않음
        assert w.oos_start == w.is_end
        assert w.oos_end <= 300


def test_generate_windows_custom_step() -> None:
    windows = generate_windows(400, is_bars=100, oos_bars=50, step_bars=100)
    assert [w.is_start for w in windows] == [0, 100, 200]


def test_generate_windows_warmup_bounds_never_precede_is_start() -> None:
    # warmup_bars가 is_bars보다 큰 경우에도 warmup_start는 is_start 아래로 내려가지 않는다.
    windows = generate_windows(300, is_bars=50, oos_bars=50, warmup_bars=1000)
    for w in windows:
        assert w.warmup_start == w.is_start
        assert w.is_start <= w.warmup_start <= w.oos_start


def test_generate_windows_warmup_within_is_tail() -> None:
    windows = generate_windows(300, is_bars=100, oos_bars=50, warmup_bars=30)
    for w in windows:
        assert w.warmup_start == w.oos_start - 30
        assert w.warmup_start >= w.is_start


def test_generate_windows_insufficient_data_returns_empty() -> None:
    assert generate_windows(120, is_bars=100, oos_bars=50) == []


def test_generate_windows_rejects_nonpositive_sizes() -> None:
    with pytest.raises(ValueError, match="is_bars"):
        generate_windows(300, is_bars=0, oos_bars=50)
    with pytest.raises(ValueError, match="is_bars"):
        generate_windows(300, is_bars=100, oos_bars=0)
    with pytest.raises(ValueError, match="step_bars"):
        generate_windows(300, is_bars=100, oos_bars=50, step_bars=0)


# --------------------------------------------------------------------------- 실행 (엔드투엔드)


def test_run_walk_forward_produces_expected_window_count() -> None:
    df = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=7)
    report = run_walk_forward(df, symbol="BTC/USDT:USDT", timeframe="1h", is_bars=150, oos_bars=80)
    # generate_windows(400, is=150, oos=80, step=80): is_start in [0, 80, 160] (230/310/390<=400)
    assert len(report.rows) == 3
    for row in report.rows:
        assert row.symbol == "BTC/USDT:USDT"
        assert row.timeframe == "1h"
        assert row.is_num_bars == 150
        assert row.oos_num_bars == 80


def test_run_walk_forward_windows_are_reproducible_metadata() -> None:
    df = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=7)
    report = run_walk_forward(df, symbol="X", timeframe="1h", is_bars=150, oos_bars=80)
    row = report.rows[0]
    assert row.is_start_time == int(df["open_time"].iloc[0])
    assert row.is_end_time == int(df["open_time"].iloc[149])
    assert row.oos_start_time == int(df["open_time"].iloc[150])
    assert row.oos_end_time == int(df["open_time"].iloc[229])
    assert row.seed == 0


def test_run_walk_forward_is_deterministic() -> None:
    df = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=9)
    a = run_walk_forward(df, symbol="X", timeframe="1h", is_bars=150, oos_bars=80)
    b = run_walk_forward(df, symbol="X", timeframe="1h", is_bars=150, oos_bars=80)
    assert [r.model_dump() for r in a.rows] == [r.model_dump() for r in b.rows]


def test_run_walk_forward_empty_when_data_too_short() -> None:
    df = make_synthetic_ohlcv(bars=100, timeframe="1h", seed=1)
    report = run_walk_forward(df, symbol="X", timeframe="1h", is_bars=150, oos_bars=80)
    assert report.rows == []
    assert report.mean_return_gap() is None
    assert report.mean_sharpe_gap() is None


def test_walk_forward_row_gap_properties() -> None:
    base = {
        "window_index": 0,
        "symbol": "X",
        "timeframe": "1h",
        "is_start_time": 0,
        "is_end_time": 1,
        "oos_start_time": 2,
        "oos_end_time": 3,
        "is_num_bars": 100,
        "oos_num_bars": 50,
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "is_total_return": 0.2,
        "is_max_drawdown": 0.05,
        "is_win_rate": 0.6,
        "is_profit_factor": 1.5,
        "is_sharpe": 1.2,
        "is_num_trades": 10,
        "oos_total_return": 0.05,
        "oos_max_drawdown": 0.1,
        "oos_win_rate": 0.4,
        "oos_profit_factor": 0.9,
        "oos_sharpe": None,
        "oos_num_trades": 4,
        "seed": 0,
    }
    row = WalkForwardRow(**base)
    assert row.return_gap == pytest.approx(0.15)
    assert row.sharpe_gap is None  # oos_sharpe가 None이면 격차도 None


# --------------------------------------------------------------------------- 리포트 출력


def test_walk_forward_report_dataframe_and_table_columns() -> None:
    df = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=3)
    report = run_walk_forward(df, symbol="X", timeframe="1h", is_bars=150, oos_bars=80)
    frame = report.to_dataframe()
    assert len(frame) == len(report.rows)
    for col in ("oos_total_return", "oos_sharpe", "return_gap", "sharpe_gap", "rsi_overbought"):
        assert col in frame.columns
    table = report.to_table()
    assert "Walk-Forward" in table
    assert "mean return_gap" in table


def test_write_walk_forward_csv_roundtrip(tmp_path: Path) -> None:
    df = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=5)
    report = run_walk_forward(df, symbol="X", timeframe="1h", is_bars=150, oos_bars=80)
    path = write_walk_forward_csv(report, tmp_path / "walkforward.csv")
    assert path.exists()
    loaded = pd.read_csv(path)
    assert len(loaded) == len(report.rows)
    assert "oos_total_return" in loaded.columns


# --------------------------------------------------------------------- 룩어헤드(미래 누수) 없음


def _extend_with_independent_tail(
    base: pd.DataFrame, *, keep_bars: int, tail_bars: int, timeframe: str, tail_seed: int
) -> pd.DataFrame:
    """`base`의 앞 `keep_bars`개는 그대로 두고, 서로 다른 시드로 만든 독립적인
    타 구간을 이어붙인다. 시간축은 끊기지 않게 이어 붙인다(open_time 연속)."""
    head = base.iloc[:keep_bars].reset_index(drop=True)
    tail = make_synthetic_ohlcv(bars=tail_bars, timeframe=timeframe, seed=tail_seed)
    step = timeframe_to_ms(timeframe)
    offset = int(head["open_time"].iloc[-1]) + step - int(tail["open_time"].iloc[0])
    tail = tail.assign(open_time=tail["open_time"] + offset)
    return pd.concat([head, tail], ignore_index=True)


def test_run_walk_forward_first_window_unaffected_by_future_data() -> None:
    """윈도우0의 oos_end 이후 데이터를 완전히 다른 값으로 바꿔도 윈도우0의
    IS 파라미터 선택·OOS 성과는 완전히 동일해야 한다(미래 데이터 누수 없음)."""
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
        tail_seed=999,  # base와 무관한 시드 → 미래 구간이 완전히 다른 값
    )
    assert len(df_long) == 400
    # 두 데이터프레임의 첫 window0_oos_end개 봉은 동일해야 테스트가 유효하다.
    pd.testing.assert_frame_equal(
        df_short.reset_index(drop=True), df_long.iloc[:window0_oos_end].reset_index(drop=True)
    )

    report_short = run_walk_forward(
        df_short,
        symbol="X",
        timeframe=timeframe,
        is_bars=is_bars,
        oos_bars=oos_bars,
        warmup_bars=warmup_bars,
    )
    report_long = run_walk_forward(
        df_long,
        symbol="X",
        timeframe=timeframe,
        is_bars=is_bars,
        oos_bars=oos_bars,
        warmup_bars=warmup_bars,
    )

    # df_short는 윈도우가 정확히 1개만 들어맞고, df_long은 미래 데이터가 있어 더 많다.
    assert len(report_short.rows) == 1
    assert len(report_long.rows) > 1

    # 윈도우0 결과는 미래 데이터 유무와 무관하게 완전히 동일해야 한다.
    assert report_short.rows[0].model_dump() == report_long.rows[0].model_dump()


def test_run_walk_forward_warmup_uses_only_past_data() -> None:
    """warmup_bars>0이어도 warmup 구간은 IS 꼬리(과거)에서만 가져오므로, OOS
    구간 자체를 바꾸지 않는 한(즉 미래를 바꾸는 것만으로는) 윈도우0 결과가 그대로다.
    `warmup_bars=0`과 `warmup_bars>0`을 비교해 워밍업이 미래 데이터가 아님을 보인다."""
    timeframe = "1h"
    is_bars, oos_bars = 150, 80
    df = make_synthetic_ohlcv(bars=230, timeframe=timeframe, seed=21)

    report_no_warmup = run_walk_forward(
        df, symbol="X", timeframe=timeframe, is_bars=is_bars, oos_bars=oos_bars, warmup_bars=0
    )
    report_with_warmup = run_walk_forward(
        df, symbol="X", timeframe=timeframe, is_bars=is_bars, oos_bars=oos_bars, warmup_bars=60
    )

    # 두 실행 모두 윈도우 경계(시간)는 동일해야 한다 — warmup은 신호 생성 컨텍스트일 뿐,
    # OOS 구간 자체(oos_start/oos_end)를 바꾸지 않는다.
    assert report_no_warmup.rows[0].oos_start_time == report_with_warmup.rows[0].oos_start_time
    assert report_no_warmup.rows[0].oos_end_time == report_with_warmup.rows[0].oos_end_time
    assert report_no_warmup.rows[0].is_start_time == report_with_warmup.rows[0].is_start_time
    assert report_no_warmup.rows[0].is_end_time == report_with_warmup.rows[0].is_end_time


# --------------------------------------------------------------------- 펀딩비 스레딩 (WAN-29)


def test_run_walk_forward_forwards_funding_to_is_sweep_and_oos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_walk_forward가 funding_rates를 IS 스윕과 OOS 평가에 동일하게 전달한다.

    워크포워드는 오더블록을 내부에서 재탐지하므로 합성 데이터로 OOS 거래를 강제하기
    어렵다. 대신 원래 갭(펀딩비를 스윕·OOS 손익에 흘려버림)을 직접 막도록, IS 스윕
    (`run_sweep`)과 OOS 백테스트(`BacktestEngine.run`)가 넘겨준 그대로의 funding_rates를
    받는지 스파이로 검증한다. 펀딩비가 손익에 반영되는 경로는 test_sweep의
    evaluate 테스트가 확인한다.
    """
    import backtest.walkforward as wf_mod
    from backtest.engine import BacktestEngine
    from backtest.sweep import run_sweep as real_run_sweep
    from data.models import FundingRate

    rates = [FundingRate(symbol="X", funding_time=t, rate=0.001) for t in (0, 1, 2)]

    sweep_seen: list[object] = []

    def spy_run_sweep(df: pd.DataFrame, **kwargs: object) -> object:
        sweep_seen.append(kwargs.get("funding_rates"))
        return real_run_sweep(df, **kwargs)  # type: ignore[arg-type]

    engine_seen: list[object] = []
    real_run = BacktestEngine.run

    def spy_run(self: object, df: pd.DataFrame, signals: object, funding: object = None) -> object:
        engine_seen.append(funding)
        return real_run(self, df, signals, funding)  # type: ignore[arg-type]

    monkeypatch.setattr(wf_mod, "run_sweep", spy_run_sweep)
    monkeypatch.setattr(BacktestEngine, "run", spy_run)

    df = make_synthetic_ohlcv(bars=400, timeframe="1h", seed=7)
    report = run_walk_forward(
        df,
        symbol="X",
        timeframe="1h",
        is_bars=150,
        oos_bars=80,
        funding_rates=rates,
    )

    # 윈도우가 생성되어 IS 스윕과 OOS 평가가 실제로 호출됐다.
    assert report.rows
    assert sweep_seen and all(fr is rates for fr in sweep_seen)
    assert engine_seen and all(fr is rates for fr in engine_seen)
