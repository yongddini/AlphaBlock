"""strategy.confluence (오더블록 + 지표 컨플루언스) 단위 테스트 (WAN-18).

전략 계층의 **결합·판정 로직**을 검증한다. 지표 수치 자체는 test_indicators가
검증하므로, 여기서는 오더블록 탭 시그널을 주입(`order_block_result`)해 게이트
합산·임계 판정·on/off·강도·지표 이탈 청산·백테스트 연동을 확인한다.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest import run_backtest
from strategy.confluence import (
    ConfluenceResult,
    ConfluenceStrategy,
    ExitTrigger,
    SignalKind,
    _ema_trend,
    generate_confluence_signals,
)
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
)

_BULL = OrderBlockDirection.BULLISH
_BEAR = OrderBlockDirection.BEARISH


def _dummy_order_block(direction: OrderBlockDirection) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=1.0,
        bottom=0.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _signal(direction: OrderBlockDirection, time: int, price: float) -> OrderBlockSignal:
    return OrderBlockSignal(
        direction=direction,
        trigger_time=time,
        price=price,
        order_block=_dummy_order_block(direction),
        status="active",
    )


def _arch_df(volume: float = 10.0) -> pd.DataFrame:
    """상승(0..119) 후 하락(120..199)하는 아치형 종가 시계열.

    상승 구간은 EMA 정배열(상승 추세)·종가>VWMA를, 하락 구간은 역배열·종가<VWMA를
    만든다. volume을 상수로 두면 VWMA는 종가의 단순 롤링 평균이 된다.
    """
    rising = [100.0 + i * 0.5 for i in range(120)]  # 100.0 -> 159.5
    falling = [rising[-1] - (i + 1) * 0.5 for i in range(80)]  # 159.0 -> 120.0
    closes = rising + falling
    n = len(closes)
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(n)],
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [volume] * n,
        }
    )


# --------------------------------------------------------------------------- _ema_trend


def test_ema_trend_bullish_when_descending_by_length() -> None:
    # 짧은 EMA가 위(값 내림차순) => 정배열 => 상승(+1)
    assert _ema_trend([10.0, 9.0, 8.0, 7.0], required_pairs=3) == 1


def test_ema_trend_bearish_when_ascending_by_length() -> None:
    assert _ema_trend([7.0, 8.0, 9.0, 10.0], required_pairs=3) == -1


def test_ema_trend_neutral_when_mixed_or_below_required() -> None:
    assert _ema_trend([10.0, 8.0, 9.0, 7.0], required_pairs=3) == 0


def test_ema_trend_neutral_on_nan() -> None:
    assert _ema_trend([10.0, math.nan, 8.0, 7.0], required_pairs=1) == 0


# --------------------------------------------------------------------------- 게이트 합산/판정


def _run_single(
    df: pd.DataFrame,
    signal: OrderBlockSignal,
    params: ConfluenceParams,
) -> tuple[ConfluenceStrategy, ConfluenceResult]:
    strategy = ConfluenceStrategy(params=params)
    ob_result = OrderBlockResult(order_blocks=[], signals=[signal])
    return strategy, strategy.run(df, ob_result)


def test_single_gate_ema_confirms_bull_in_uptrend() -> None:
    df = _arch_df()
    params = ConfluenceParams(use_ema_trend=True, use_rsi_gate=False, use_vwma_trend=False)
    # 상승 구간 봉(pos=100)에서 롱 시그널
    time = 100 * 60_000
    _, result = _run_single(df, _signal(_BULL, time, 150.0), params)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.kind is SignalKind.ENTRY
    assert entry.score == 1
    assert entry.threshold == 1
    assert entry.confirmed is True
    assert entry.strength == pytest.approx(1.0)
    assert entry.indicators.ema_trend == 1


def test_single_gate_ema_rejects_bear_in_uptrend() -> None:
    df = _arch_df()
    params = ConfluenceParams(use_ema_trend=True, use_rsi_gate=False, use_vwma_trend=False)
    time = 100 * 60_000
    _, result = _run_single(df, _signal(_BEAR, time, 150.0), params)

    entry = result.entries[0]
    assert entry.score == -1
    assert entry.confirmed is False
    assert entry.strength == pytest.approx(0.0)
    assert result.confirmed_entries == []


def test_vwma_gate_confirms_bull_above_vwma() -> None:
    df = _arch_df()
    params = ConfluenceParams(use_ema_trend=False, use_rsi_gate=False, use_vwma_trend=True)
    time = 100 * 60_000  # 상승 구간: 종가 > 롤링 VWMA
    _, result = _run_single(df, _signal(_BULL, time, 150.0), params)
    entry = result.entries[0]
    assert entry.indicators.vwma is not None
    assert entry.indicators.close >= entry.indicators.vwma
    assert entry.score == 1
    assert entry.confirmed is True


def test_all_gates_composed_score_matches_spec() -> None:
    """세 게이트가 개별 규칙대로 합산되는지 지표 실측값으로 대조."""
    from strategy.indicators import emas, rsi, vwma

    df = _arch_df()
    params = ConfluenceParams()  # 전 게이트 활성, 기본 임계
    pos = 100
    time = pos * 60_000

    rsi_v = float(rsi(df, length=params.rsi_length)[pos])
    vwma_v = float(vwma(df, length=params.vwma_length)[pos])
    ema_frame = emas(df, lengths=sorted(params.ema_lengths))
    ema_values = [float(ema_frame[f"ema_{length}"][pos]) for length in sorted(params.ema_lengths)]
    close = float(df["close"][pos])

    # 스펙대로 기대 표 계산 (롱)
    trend = _ema_trend(ema_values, params.required_aligned_pairs)
    expected = 0
    expected += 1 if trend == 1 else (-1 if trend == -1 else 0)
    expected += -1 if rsi_v >= params.rsi_overbought else 1  # RSI 워밍업 아님(pos=100)
    expected += 1 if close >= vwma_v else -1

    _, result = _run_single(df, _signal(_BULL, time, close), params)
    entry = result.entries[0]
    assert entry.score == expected
    assert entry.threshold == 3
    assert entry.confirmed is (expected >= 3)
    assert entry.strength == pytest.approx(min(1.0, max(0.0, expected / 3)))


def test_min_confluence_score_relaxes_confirmation() -> None:
    df = _arch_df()
    time = 100 * 60_000
    # 상승 구간 롱: ema(+1), vwma(+1), rsi(과매수면 -1) => 점수 최소 +1
    strict = ConfluenceParams()  # 임계 3
    relaxed = ConfluenceParams(min_confluence_score=1)

    _, strict_res = _run_single(df, _signal(_BULL, time, 150.0), strict)
    _, relaxed_res = _run_single(df, _signal(_BULL, time, 150.0), relaxed)

    assert relaxed_res.entries[0].threshold == 1
    assert relaxed_res.entries[0].confirmed is True
    # 완화 임계가 엄격 임계보다 확정을 더 쉽게 한다(같은 점수 기준).
    assert relaxed_res.entries[0].score == strict_res.entries[0].score


def test_no_gates_enabled_confirms_all_taps() -> None:
    df = _arch_df()
    params = ConfluenceParams(use_ema_trend=False, use_rsi_gate=False, use_vwma_trend=False)
    time = 100 * 60_000
    _, result = _run_single(df, _signal(_BULL, time, 150.0), params)
    entry = result.entries[0]
    assert entry.score == 0
    assert entry.threshold == 0
    assert entry.confirmed is True
    assert entry.strength == pytest.approx(1.0)


def test_breaker_taps_are_ignored() -> None:
    df = _arch_df()
    cancelled = OrderBlockSignal(
        direction=_BULL,
        trigger_time=100 * 60_000,
        price=150.0,
        order_block=_dummy_order_block(_BULL),
        status="cancelled",
    )
    strategy = ConfluenceStrategy()
    result = strategy.run(df, OrderBlockResult(order_blocks=[], signals=[cancelled]))
    assert result.entries == []
    assert result.exits == []


# --------------------------------------------------------------------------- 청산(지표 이탈)


def test_trend_flip_exit_emitted_after_confirmed_entry() -> None:
    df = _arch_df()
    # 200봉 안에서 추세 반전이 관찰되도록 짧은 EMA 세트 사용(기본 365 EMA는 반응이 느림).
    params = ConfluenceParams(
        use_ema_trend=True,
        use_rsi_gate=False,
        use_vwma_trend=False,
        ema_lengths=(3, 5, 8),
        exit_on_trend_flip=True,
        exit_on_vwma_cross=False,
    )
    entry_time = 100 * 60_000
    _, result = _run_single(df, _signal(_BULL, entry_time, 150.0), params)

    assert result.confirmed_entries  # 진입 확정됨
    assert len(result.exits) == 1
    exit_sig = result.exits[0]
    assert exit_sig.kind is SignalKind.EXIT
    assert exit_sig.exit_trigger is ExitTrigger.TREND_FLIP
    assert exit_sig.direction is _BULL
    assert exit_sig.time > entry_time  # 하락 구간에서 추세 반전 후 청산


def test_vwma_cross_exit_emitted() -> None:
    df = _arch_df()
    params = ConfluenceParams(
        use_ema_trend=False,
        use_rsi_gate=False,
        use_vwma_trend=True,
        exit_on_trend_flip=False,
        exit_on_vwma_cross=True,
    )
    entry_time = 100 * 60_000
    _, result = _run_single(df, _signal(_BULL, entry_time, 150.0), params)
    assert result.confirmed_entries
    assert len(result.exits) == 1
    assert result.exits[0].exit_trigger is ExitTrigger.VWMA_CROSS
    assert result.exits[0].time > entry_time


def test_no_exit_when_exit_rules_disabled() -> None:
    df = _arch_df()
    params = ConfluenceParams(
        use_ema_trend=True,
        use_rsi_gate=False,
        use_vwma_trend=False,
        exit_on_trend_flip=False,
        exit_on_vwma_cross=False,
    )
    _, result = _run_single(df, _signal(_BULL, 100 * 60_000, 150.0), params)
    assert result.confirmed_entries
    assert result.exits == []


# --------------------------------------------------------------------------- WAN-8 백테스트 연동


def test_order_block_signals_bridge_only_confirmed() -> None:
    df = _arch_df()
    params = ConfluenceParams(use_ema_trend=True, use_rsi_gate=False, use_vwma_trend=False)
    signals = [
        _signal(_BULL, 100 * 60_000, 150.0),  # 상승 구간 롱 -> 확정
        _signal(_BEAR, 100 * 60_000, 150.0),  # 같은 봉 숏 -> 기각
    ]
    strategy = ConfluenceStrategy(params=params)
    result = strategy.run(df, OrderBlockResult(order_blocks=[], signals=signals))

    bridged = result.order_block_signals
    assert len(bridged) == 1
    assert bridged[0].direction is _BULL
    assert bridged[0].status == "active"
    assert all(isinstance(s, OrderBlockSignal) for s in bridged)


def test_confluence_signals_feed_backtest_engine() -> None:
    df = _arch_df()
    params = ConfluenceParams(use_ema_trend=True, use_rsi_gate=False, use_vwma_trend=False)
    signals = [_signal(_BULL, 50 * 60_000, df["close"][50])]
    result = generate_confluence_signals(
        df, params, order_block_result=OrderBlockResult(order_blocks=[], signals=signals)
    )
    assert result.confirmed_entries

    bt = run_backtest(df, result.order_block_signals)
    # 확정 진입이 있으므로 거래가 최소 1건 실행된다.
    assert bt.metrics.num_trades >= 1


def test_run_end_to_end_detects_and_filters() -> None:
    """실제 오더블록 탐지까지 포함한 엔드투엔드 스모크 테스트."""
    df = _arch_df()
    result = generate_confluence_signals(df)
    # 예외 없이 실행되고, 확정 진입은 항상 order_block을 가진다.
    for entry in result.confirmed_entries:
        assert entry.order_block is not None
    assert isinstance(result.params, ConfluenceParams)


def test_empty_dataframe_returns_empty_result() -> None:
    empty = pd.DataFrame({c: [] for c in ("open_time", "open", "high", "low", "close", "volume")})
    result = generate_confluence_signals(empty)
    assert result.entries == []
    assert result.exits == []
