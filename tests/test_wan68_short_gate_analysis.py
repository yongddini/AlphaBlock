"""backtest.wan68_short_gate_analysis 단위/스모크 테스트 (WAN-68).

레짐 정의 함수·신호 필터링은 결정적 유닛 테스트로, 변형 비교·무작위 대조군은
합성 데이터로 배선이 실제로 동작하는지(스모크) 확인한다. 실데이터 3심볼×4TF
전체 산출은 `backtest/reports/`의 CSV(재현 스크립트 `python -m
backtest.wan68_short_gate_analysis`)로 별도 확인한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan68_short_gate_analysis import (
    _build_regime_lookup,
    _split_bars,
    build_regime_definitions,
    filter_signals_by_regime,
    run_random_entry_control,
    run_variant_comparison,
)
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockResult, OrderBlockSignal

_BULL = OrderBlockDirection.BULLISH
_BEAR = OrderBlockDirection.BEARISH


def _synthetic_pair(bars: int = 1200, span: int = 400) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="4h", bars=bars, seed=13)
    htf_ms = timeframe_to_ms("4h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=17, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _regime_df(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    step = 3_600_000
    return pd.DataFrame(
        {
            "open_time": [i * step for i in range(n)],
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [10.0] * n,
        }
    )


def _order_block(direction: OrderBlockDirection) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=1_000.0,
        bottom=0.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=False,
        break_time=None,
    )


# --------------------------------------------------------------------------- _split_bars


def test_split_bars_two_thirds() -> None:
    assert _split_bars(300) == 200
    assert _split_bars(9) == 6


def test_split_bars_never_empties_either_side() -> None:
    is_end = _split_bars(2)
    assert 1 <= is_end <= 1  # n=2 -> is_end 최소 1, 최대 n-1=1


# --------------------------------------------------------------------------- 레짐 판정


def test_regime_lookup_level_definition_flags_below_ema() -> None:
    # 장기 하락 후 평탄 -> 마지막 확정봉에서 종가<EMA(짧은 길이라 워밍업 빠르게 끝남).
    closes = [200.0 - i * 3.0 for i in range(60)]
    df = _regime_df(closes)
    lookup = _build_regime_lookup(df, ema_length=5, use_slope=False, slope_lookback=0)
    last_time = int(df["open_time"].iloc[-1])
    assert lookup(last_time) is True  # 단조 하락 -> 종가가 EMA 아래


def test_regime_lookup_level_definition_flags_above_ema_as_false() -> None:
    closes = [100.0 + i * 3.0 for i in range(60)]  # 단조 상승 -> 종가가 EMA 위
    df = _regime_df(closes)
    lookup = _build_regime_lookup(df, ema_length=5, use_slope=False, slope_lookback=0)
    last_time = int(df["open_time"].iloc[-1])
    assert lookup(last_time) is False


def test_regime_lookup_slope_definition_flags_declining_ema() -> None:
    closes = [200.0 - i * 3.0 for i in range(60)]  # 단조 하락 -> EMA 자체도 하락
    df = _regime_df(closes)
    lookup = _build_regime_lookup(df, ema_length=5, use_slope=True, slope_lookback=10)
    last_time = int(df["open_time"].iloc[-1])
    assert lookup(last_time) is True


def test_regime_lookup_before_any_data_is_false() -> None:
    closes = [200.0 - i * 3.0 for i in range(60)]
    df = _regime_df(closes)
    lookup = _build_regime_lookup(df, ema_length=5, use_slope=False, slope_lookback=0)
    assert lookup(-1) is False  # 판정 불가 -> 보수적으로 숏 배제(=하락국면 아님)


def test_build_regime_definitions_excludes_cd_without_btc_daily() -> None:
    closes = [200.0 - i * 3.0 for i in range(300)]
    df = _regime_df(closes)
    defs = build_regime_definitions(df, None)
    assert set(defs) == {"A_local_close_below_ema240", "B_local_ema240_slope_down"}


def test_build_regime_definitions_includes_all_four_with_btc_daily() -> None:
    closes = [200.0 - i * 3.0 for i in range(300)]
    df = _regime_df(closes)
    defs = build_regime_definitions(df, df)
    assert set(defs) == {
        "A_local_close_below_ema240",
        "B_local_ema240_slope_down",
        "C_btc_daily_close_below_ema200",
        "D_btc_daily_ema200_slope_down",
    }


# --------------------------------------------------------------------------- 신호 필터링


def test_filter_signals_by_regime_keeps_longs_and_gates_shorts() -> None:
    signals = [
        OrderBlockSignal(
            direction=_BULL, trigger_time=100, price=1.0, order_block=_order_block(_BULL)
        ),
        OrderBlockSignal(
            direction=_BEAR, trigger_time=200, price=1.0, order_block=_order_block(_BEAR)
        ),
        OrderBlockSignal(
            direction=_BEAR, trigger_time=300, price=1.0, order_block=_order_block(_BEAR)
        ),
    ]
    ob_result = OrderBlockResult(order_blocks=[], signals=signals)
    # 200만 허용하는 레짐.
    lookup = lambda t: t == 200  # noqa: E731
    filtered = filter_signals_by_regime(ob_result, lookup)
    kept_times = {s.trigger_time for s in filtered.signals}
    assert kept_times == {100, 200}


# --------------------------------------------------------------------------- 변형 비교 (스모크)


def test_run_variant_comparison_produces_expected_variants() -> None:
    htf, one_min = _synthetic_pair()
    rows = run_variant_comparison(
        htf, one_min, btc_daily_df=None, symbol="BTC/USDT:USDT", timeframe="4h"
    )
    variants = {r.variant for r in rows}
    assert "baseline_B안" in variants
    assert "나_숏제거" in variants
    assert "다_RR게이트" in variants
    assert any(v.startswith("가_레짐게이트_") for v in variants)
    # btc_daily_df가 없으면 C/D 정의는 생성되지 않는다.
    assert not any("btc_daily" in v for v in variants)

    baseline = next(r for r in rows if r.variant == "baseline_B안")
    assert baseline.oos_superior_to_baseline is None
    for row in rows:
        if row.variant != "baseline_B안":
            assert row.oos_superior_to_baseline in (True, False)


def test_run_variant_comparison_empty_for_too_short_series() -> None:
    htf = make_synthetic_ohlcv(timeframe="4h", bars=10, seed=1)
    one_min = make_synthetic_ohlcv(timeframe="1m", bars=10, seed=2)
    rows = run_variant_comparison(
        htf, one_min, btc_daily_df=None, symbol="BTC/USDT:USDT", timeframe="4h"
    )
    assert rows == []


# --------------------------------------------------------------------------- 무작위 대조군 (스모크)


def test_run_random_entry_control_smoke() -> None:
    htf = make_synthetic_ohlcv(timeframe="4h", bars=1200, seed=13)
    result = run_random_entry_control(
        htf, symbol="BTC/USDT:USDT", timeframe="4h", iterations=15, seed=1
    )
    assert result.iterations == 15
    if result.random_p_value is not None:
        assert 0.0 <= result.random_p_value <= 1.0
    assert result.real_num_trades >= 0


def test_run_random_entry_control_deterministic_with_fixed_seed() -> None:
    htf = make_synthetic_ohlcv(timeframe="4h", bars=1200, seed=13)
    a = run_random_entry_control(htf, symbol="BTC/USDT:USDT", timeframe="4h", iterations=10, seed=5)
    b = run_random_entry_control(htf, symbol="BTC/USDT:USDT", timeframe="4h", iterations=10, seed=5)
    assert a.random_mean_return == pytest.approx(b.random_mean_return)
    assert a.random_p_value == b.random_p_value
