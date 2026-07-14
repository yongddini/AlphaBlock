"""backtest.wan81_engine_replacement_report 단위 테스트 (WAN-81).

3심볼×4TF×3년 실데이터 재산출은 `backtest/reports/wan81_*.csv`·`wan81_summary.md`
(재현: `python -m backtest.wan81_engine_replacement_report`)로 별도 확인한다. 여기서는
결정적 합성 데이터·수기 구성 입력으로 핵심 로직(구/신 엔진 실행, §5 되살아난 진입
수 계산, 갭D 기각 수 계산)만 검증한다.
"""

from __future__ import annotations

from backtest.models import BacktestConfig, PositionSide, Trade, TradeFill
from backtest.models import ExitReason as BtExitReason
from backtest.sweep import default_backtest_config
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan81_engine_replacement_report import (
    NEW_ENGINE_PARAMS,
    OLD_ENGINE_PARAMS,
    _side_counts,
    count_gap_d_rejections,
    count_revived_entries,
    run_engine,
)
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockSignal
from strategy.order_blocks import OrderBlockDetector


def test_engine_presets_match_expected_defaults() -> None:
    """구 엔진 프리셋은 WAN-81 이전 기본값. 신 엔진 프리셋은 `ConfluenceParams()`
    기본값을 따르되, `short_enabled`만은 WAN-87(WAN-86 결정 1)로 기본값이 `False`가
    된 뒤에도 "숏 활성화 신 엔진" 정의를 보존하기 위해 `True`로 명시 고정한다."""
    assert OLD_ENGINE_PARAMS.retap_mode == "once"
    assert OLD_ENGINE_PARAMS.rsi_gate_mode == "extreme"
    assert OLD_ENGINE_PARAMS.take_profit_mode == "line"
    assert OLD_ENGINE_PARAMS.deviation_filter is None
    assert OLD_ENGINE_PARAMS.short_enabled is False

    assert NEW_ENGINE_PARAMS.retap_mode == "every_tap"
    assert NEW_ENGINE_PARAMS.rsi_gate_mode == "first_tap_free"
    assert NEW_ENGINE_PARAMS.take_profit_mode == "fixed_r"
    assert NEW_ENGINE_PARAMS.deviation_filter is not None
    assert NEW_ENGINE_PARAMS.short_enabled is True


def test_side_counts_splits_long_and_short() -> None:
    fill = TradeFill(
        time=2_000, price=100.0, quantity=1.0, fee=0.0, reason=BtExitReason.END_OF_DATA
    )
    trades = [
        Trade(
            side=PositionSide.LONG,
            entry_time=1_000,
            entry_price=100.0,
            quantity=1.0,
            entry_fee=0.0,
            exits=[fill],
            realized_pnl=0.0,
            return_pct=0.0,
        ),
        Trade(
            side=PositionSide.SHORT,
            entry_time=1_000,
            entry_price=100.0,
            quantity=1.0,
            entry_fee=0.0,
            exits=[fill],
            realized_pnl=0.0,
            return_pct=0.0,
        ),
        Trade(
            side=PositionSide.LONG,
            entry_time=1_000,
            entry_price=100.0,
            quantity=1.0,
            entry_fee=0.0,
            exits=[fill],
            realized_pnl=0.0,
            return_pct=0.0,
        ),
    ]
    assert _side_counts(trades) == (2, 1)


def _ob(direction: OrderBlockDirection, *, top: float, bottom: float) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def test_count_gap_d_rejections_flags_narrow_stops() -> None:
    """손절 거리가 진입가의 0.3% 미만인 시그널만 갭D로 기각 집계된다."""
    narrow_long = _ob(OrderBlockDirection.BULLISH, top=100.0, bottom=99.95)  # 거리=0.05/100=0.05%
    wide_long = _ob(OrderBlockDirection.BULLISH, top=100.0, bottom=90.0)  # 거리=10%
    narrow_short = _ob(OrderBlockDirection.BEARISH, top=100.05, bottom=90.0)  # 거리=0.05%
    signals = [
        OrderBlockSignal(
            direction=OrderBlockDirection.BULLISH,
            trigger_time=0,
            price=100.0,
            order_block=narrow_long,
        ),
        OrderBlockSignal(
            direction=OrderBlockDirection.BULLISH,
            trigger_time=1,
            price=100.0,
            order_block=wide_long,
        ),
        OrderBlockSignal(
            direction=OrderBlockDirection.BEARISH,
            trigger_time=2,
            price=100.0,
            order_block=narrow_short,
        ),
    ]
    assert count_gap_d_rejections(signals) == 2


def test_count_revived_entries_at_least_matches_fixed_signal_count() -> None:
    """§5 수정 전(구 버그) 시그널 수는 수정 후(신 로직) 시그널 수보다 많을 수 없다.

    구 버그는 병합 단위 전체로 `entered`를 검사해 진입 기회를 놓치기만 하므로
    (허위 양성은 만들지 않는다), 항상 `buggy <= fixed`다.
    """
    df = make_synthetic_ohlcv(timeframe="1h", bars=2000, seed=13)
    result = OrderBlockDetector().run(df)
    buggy = count_revived_entries(df, result)
    assert buggy <= len(result.signals)


def test_run_engine_old_vs_new_end_to_end_on_synthetic_data() -> None:
    """구/신 엔진 둘 다 합성 데이터에서 실행되고 지표를 낸다(배선 스모크 테스트)."""
    df = make_synthetic_ohlcv(timeframe="1h", bars=3000, seed=5)
    ob_result = OrderBlockDetector().run(df)
    cfg: BacktestConfig = default_backtest_config("1h")

    old_row = run_engine(
        df, OLD_ENGINE_PARAMS, cfg, ob_result, symbol="TEST/USDT", timeframe="1h", engine="old"
    )
    new_row = run_engine(
        df, NEW_ENGINE_PARAMS, cfg, ob_result, symbol="TEST/USDT", timeframe="1h", engine="new"
    )
    assert old_row.num_trades == old_row.long_trades + old_row.short_trades
    assert new_row.num_trades == new_row.long_trades + new_row.short_trades
    # 구 엔진은 롱 온리(short_enabled=False) 기본값이므로 숏 거래가 없다.
    assert old_row.short_trades == 0
