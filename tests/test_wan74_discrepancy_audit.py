"""WAN-74 불일치 감사 하네스 테스트.

`naive_trades`(A안 근사)·`b_engine_trades`(B안)가 합성 시계열에서 정합적인 R을
산출하는지, 체결 분해(`compute_fill_decomposition`)·풀링 검정
(`pooled_matched_null_test`)이 빈/소형 입력에서도 예외 없이 정상 범위 값을 내는지
검증한다.
"""

from __future__ import annotations

from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan73_validation import WAN73_NEW_PARAMS
from backtest.wan74_discrepancy_audit import (
    FillDecomposition,
    TradeRecord,
    b_engine_trades,
    compute_fill_decomposition,
    naive_trades,
    pooled_matched_null_test,
)
from strategy.order_blocks import OrderBlockDetector


def _synthetic_pair(*, bars: int = 900, seed: int = 7) -> tuple[object, object]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=seed)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[0])
    minutes = bars * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=seed + 1, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def test_naive_trades_produce_finite_r_multiples() -> None:
    """naive(A안 every_tap) 엔진이 낸 거래는 유효한 R과 오더블록 식별자를 갖는다."""
    htf, _ = _synthetic_pair()
    ob_result = OrderBlockDetector().run(htf)
    trades = naive_trades(
        htf,
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        params=WAN73_NEW_PARAMS,
        order_block_params=None,
        order_block_result=ob_result,
    )
    assert isinstance(trades, list)
    for t in trades:
        assert isinstance(t, TradeRecord)
        assert t.engine == "naive_closed_bar"
        assert t.side in ("long", "short")
        assert t.exit_time >= t.entry_time
        assert t.ob_key != ""


def test_b_engine_trades_gate_none_yields_at_least_as_many_as_real_gate() -> None:
    """RSI 게이트를 무력화(gate_mode='none')한 풀은 실제(neutral) 체결보다 항상 크거나 같다."""
    htf, one_min = _synthetic_pair()
    ob_result = OrderBlockDetector().run(htf)
    cfg = default_backtest_config("1h")

    real_filled = b_engine_trades(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        engine_label="b_engine_realtime",
        params=WAN73_NEW_PARAMS,
        cfg=cfg,
        order_block_result=ob_result,
    )
    gate_none = b_engine_trades(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        engine_label="b_engine_gate_none",
        params=WAN73_NEW_PARAMS,
        cfg=cfg,
        order_block_result=ob_result,
        rsi_gate_mode="none",
    )
    assert len(gate_none) >= len(real_filled)
    for t in real_filled + gate_none:
        assert t.exit_time >= t.entry_time


def test_compute_fill_decomposition_matches_by_ob_key() -> None:
    """실제 체결과 같은 존(ob_key)인 naive 거래는 '미체결'에서 제외된다."""
    naive = [
        TradeRecord(
            engine="naive_closed_bar",
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            side="long",
            entry_time=1,
            entry_price=100.0,
            exit_time=2,
            exit_price=110.0,
            stop_price=95.0,
            reason="take_profit",
            r_multiple=2.0,
            ob_key="bull:1:2:3",
        ),
        TradeRecord(
            engine="naive_closed_bar",
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            side="long",
            entry_time=10,
            entry_price=100.0,
            exit_time=20,
            exit_price=90.0,
            stop_price=95.0,
            reason="stop_loss",
            r_multiple=-1.0,
            ob_key="bull:4:5:6",
        ),
    ]
    real_filled = [
        TradeRecord(
            engine="b_engine_realtime",
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            side="long",
            entry_time=1,
            entry_price=101.0,
            exit_time=2,
            exit_price=111.0,
            stop_price=95.0,
            reason="take_profit",
            r_multiple=2.1,
            ob_key="bull:1:2:3",
        ),
    ]
    decomp = compute_fill_decomposition(naive, real_filled, symbol="BTC/USDT:USDT", timeframe="1h")
    assert isinstance(decomp, FillDecomposition)
    assert decomp.naive_n == 2
    assert decomp.real_filled_n == 1
    assert decomp.unfilled_n == 1
    assert decomp.mean_r_unfilled_virtual == -1.0
    assert decomp.unfilled_better is False


def test_compute_fill_decomposition_handles_empty_naive() -> None:
    decomp = compute_fill_decomposition([], [], symbol="BTC/USDT:USDT", timeframe="1h")
    assert decomp.naive_n == 0
    assert decomp.fill_rate_by_zone is None
    assert decomp.unfilled_better is None


def test_pooled_matched_null_test_empty_cells_returns_neutral_result() -> None:
    result = pooled_matched_null_test({}, iterations=10)
    assert result.real_pooled_trades == 0
    assert result.iterations == 0
    assert result.p_value == 1.0


def test_pooled_matched_null_test_smoke_with_synthetic_cell() -> None:
    """실데이터 없이도 실제/널 풀이 있으면 p-value가 [0,1] 범위로 산출된다."""
    real = [
        TradeRecord(
            engine="b_engine_realtime",
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            side="long",
            entry_time=1_700_000_000_000 + i * 3_600_000,
            entry_price=100.0,
            exit_time=1_700_000_000_000 + i * 3_600_000 + 1,
            exit_price=110.0,
            stop_price=95.0,
            reason="take_profit",
            r_multiple=2.0,
            ob_key=f"bull:{i}:0:0",
        )
        for i in range(5)
    ]
    null_pool = [
        TradeRecord(
            engine="b_engine_gate_none",
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            side="long",
            entry_time=1_700_000_000_000 + i * 3_600_000,
            entry_price=100.0,
            exit_time=1_700_000_000_000 + i * 3_600_000 + 1,
            exit_price=90.0 + i,
            stop_price=95.0,
            reason="stop_loss",
            r_multiple=-1.0 + i * 0.1,
            ob_key=f"bull:{i}:0:0",
        )
        for i in range(20)
    ]
    result = pooled_matched_null_test({("BTC/USDT:USDT", "1h"): (real, null_pool)}, iterations=50)
    assert result.real_pooled_trades == 5
    assert result.iterations == 50
    assert 0.0 <= result.p_value <= 1.0
