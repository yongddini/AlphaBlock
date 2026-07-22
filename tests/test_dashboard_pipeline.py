"""dashboard.pipeline 테스트 (WAN-59).

`run_pipeline`이 **CLI 리포트와 동일한 코드 경로**(컨플루언스 전략 →
`backtest.sweep.evaluate`)로 백테스트를 실행하는지, 그리고 그 결과가 청산 규칙으로
손실이 유계(bounded)한지 확인한다. 두 경로가 갈라지면(대시보드가 원시 오더블록 탭
신호를 그대로 백테스트에 넣던 WAN-59 버그) 회귀로 잡는다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, ExitReason
from backtest.sweep import default_backtest_config, evaluate
from backtest.synthetic import make_synthetic_ohlcv
from dashboard.pipeline import run_pipeline
from strategy.confluence import ConfluenceStrategy
from strategy.models import ConfluenceParams, OrderBlockParams
from strategy.order_blocks import OrderBlockDetector

_STEP = 3_600_000

# 컨플루언스 진입은 RSI 워밍업(14봉) 이후에만 확정되므로, 의미 있는 거래를 내려면
# 충분히 긴 재현 가능한 합성 시계열이 필요하다.
_SYNTH_TF = "1h"
_SYNTH_BARS = 1500


def _make_df(bars: Sequence[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * _STEP for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
    )


# strategy/order_blocks 테스트와 동일한 강세 오더블록 시나리오(재사용 가능한 최소 픽스처).
_BARS = [
    (100, 102, 90, 95, 10),
    (95, 100, 93, 98, 10),
    (98, 101, 94, 99, 10),
    (99, 103, 95, 101, 10),
    (101, 110, 100, 108, 10),
    (108, 109, 104, 106, 15),
    (106, 107, 103, 105, 20),
    (105, 106, 102, 104, 25),
    (104, 105, 100, 102, 10),
    (102, 104, 99, 101, 10),
    (101, 103, 98, 100, 10),
    (100, 105, 99, 112, 30),
    (112, 113, 95, 96, 10),
]


def _synth_df() -> pd.DataFrame:
    return make_synthetic_ohlcv(timeframe=_SYNTH_TF, bars=_SYNTH_BARS, seed=7)


def test_run_pipeline_shares_cli_evaluate_path() -> None:
    """대시보드 백테스트가 CLI(`evaluate`)와 같은 함수·설정으로 동일 결과를 낸다.

    두 경로가 갈라지면(WAN-59) 이 동치 검증이 실패한다.
    """
    df = _synth_df()
    ob_params = OrderBlockParams()
    # 대시보드/CLI(evaluate)는 A안(종가 진입) 경로 — WAN-95 기본값은 zone_limit이라
    # 명시적으로 A안을 선언해야 한다.
    conf_params = ConfluenceParams(
        entry_mode="close", rsi_mode="closed_bar", max_zone_width_atr=None
    )
    bt_config = default_backtest_config(_SYNTH_TF)

    result = run_pipeline(df, ob_params, conf_params, bt_config)

    expected = evaluate(
        df,
        confluence_params=conf_params,
        order_block_params=ob_params,
        backtest_config=bt_config,
    )
    assert result.backtest == expected


def test_run_pipeline_signals_are_confirmed_confluence_entries() -> None:
    """`signals`가 백테스트가 소비한 확정 진입(계획 청산 포함)과 정확히 일치한다."""
    df = _synth_df()

    result = run_pipeline(df)

    detection = OrderBlockDetector().run(df)
    confluence = ConfluenceStrategy().run(df, detection)
    assert result.signals == confluence.order_block_signals
    assert result.signals, "합성 데이터에서 확정 진입이 하나도 없어 회귀를 검증할 수 없음"
    # 컨플루언스 확정 진입은 전부 계획 청산(익절 선 도달 또는 오더블록 무효화)을 싣는다.
    assert all(s.planned_exit is not None for s in result.signals)


def test_run_pipeline_exit_rules_bound_losses() -> None:
    """WAN-59 완료 기준: 청산 규칙이 동작해 손실·MDD·거래 수가 정상 범위다."""
    df = _synth_df()

    metrics = run_pipeline(df).backtest.metrics

    # Trades가 1로 고정되지 않고 실측 진입 건수를 반영한다.
    assert metrics.num_trades > 1
    # 수익률이 −100% 미만으로 내려가지 않는다(숏 무한 손실 노출 방지).
    assert metrics.total_return > -1.0
    # MDD가 100%를 초과하지 않는다.
    assert metrics.max_drawdown <= 1.0


def test_run_pipeline_closes_every_position() -> None:
    """모든 거래가 전략 청산(익절·손절)으로 닫힌다 — '첫 신호 진입 후 방치'가 아니다.

    WAN-59 버그는 청산 규칙이 없어 포지션이 영원히 열려 두 번째 거래가 발생하지 않았다
    (Trades=1). 계획 청산이 배선되면 각 거래는 익절 또는 손절로 종료된다.
    """
    df = _synth_df()

    trades = run_pipeline(df).backtest.trades

    assert len(trades) > 1
    strategy_exits = {ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS}
    for trade in trades:
        assert trade.exits[-1].reason in strategy_exits


def test_confluence_path_fixes_raw_signal_pathology() -> None:
    """원시 탭 신호(옛 버그 경로) 대비, 컨플루언스 경로가 병목을 해소했음을 고정한다.

    같은 합성 데이터에서 원시 신호는 청산 규칙이 없어 Trades=1에 갇힌다. 컨플루언스
    경로는 여러 거래를 낸다 — 이 대비가 회귀 방지선이다.
    """
    df = _synth_df()
    bt_config = default_backtest_config(_SYNTH_TF)

    detection = OrderBlockDetector().run(df)
    raw = BacktestEngine(bt_config).run(df, detection.signals)
    active_raw = [s for s in detection.signals if s.status == "active"]

    fixed = run_pipeline(df, backtest_config=bt_config).backtest

    # 옛 경로: 활성 신호가 여럿이어도 청산이 없어 한 거래에 갇힌다.
    assert len(active_raw) > 1
    assert raw.metrics.num_trades == 1
    assert all(s.planned_exit is None for s in detection.signals)
    # 새 경로: 계획 청산으로 여러 거래가 실현된다.
    assert fixed.metrics.num_trades > 1


def test_run_pipeline_forwards_custom_configs() -> None:
    """사용자 지정 컨플루언스·백테스트 설정이 그대로 전달된다."""
    df = _make_df(_BARS)
    conf_params = ConfluenceParams(
        rsi_overbought=60.0,
        rsi_oversold=40.0,
        entry_mode="close",
        rsi_mode="closed_bar",
        max_zone_width_atr=None,
    )
    config = BacktestConfig(initial_capital=5_000.0)

    result = run_pipeline(df, confluence_params=conf_params, backtest_config=config)

    assert result.backtest.config == config
    assert result.backtest.metrics.initial_capital == 5_000.0
    expected = evaluate(df, confluence_params=conf_params, backtest_config=config)
    assert result.backtest == expected
