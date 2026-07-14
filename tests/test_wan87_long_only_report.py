"""backtest.wan87_long_only_report 단위 테스트 (WAN-87).

3심볼×4TF×3년 실데이터 재산출은 `backtest/reports/wan87_long_only_*.csv`·
`wan87_long_only_summary.md`(재현: `python -m backtest.wan87_long_only_report`)로 별도
확인한다. 여기서는 결정적 합성 데이터로 프리셋 정의·엔진 실행 배선만 검증한다.
"""

from __future__ import annotations

from backtest.models import BacktestConfig
from backtest.sweep import default_backtest_config
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan81_engine_replacement_report import run_engine
from backtest.wan87_long_only_report import (
    ENGINE_PRESETS,
    SHORT_DISABLED_PARAMS,
    SHORT_ENABLED_PARAMS,
    _render_summary,
    _rows_to_frame,
)
from strategy.models import ConfluenceParams
from strategy.order_blocks import OrderBlockDetector


def test_short_disabled_params_matches_current_default() -> None:
    """`SHORT_DISABLED_PARAMS`(현재 WAN-87 기본값)는 `ConfluenceParams()` 기본값과
    완전히 같아야 한다 — 롱 온리 전환이 다른 필드에 영향을 주지 않았음을 보장한다."""
    defaults = ConfluenceParams()
    assert defaults == SHORT_DISABLED_PARAMS
    assert SHORT_DISABLED_PARAMS.short_enabled is False


def test_short_enabled_params_pins_true_independent_of_default() -> None:
    """`SHORT_ENABLED_PARAMS`는 WAN-81/WAN-84 검증 당시 기본값(숏 활성화)을 보존하기
    위해 `short_enabled=True`를 명시 고정한다 — 그 외 필드는 여전히 현재 기본값과 같다."""
    defaults = ConfluenceParams()
    assert SHORT_ENABLED_PARAMS.short_enabled is True
    assert SHORT_ENABLED_PARAMS.retap_mode == defaults.retap_mode
    assert SHORT_ENABLED_PARAMS.take_profit_mode == defaults.take_profit_mode


def test_engine_presets_keys() -> None:
    assert set(ENGINE_PRESETS) == {"long_only", "short_enabled"}
    assert ENGINE_PRESETS["long_only"] is SHORT_DISABLED_PARAMS
    assert ENGINE_PRESETS["short_enabled"] is SHORT_ENABLED_PARAMS


def test_run_engine_long_only_vs_short_enabled_on_synthetic_data() -> None:
    """두 프리셋 모두 합성 데이터에서 실행되고 지표를 낸다(배선 스모크 테스트)."""
    df = make_synthetic_ohlcv(timeframe="1h", bars=3000, seed=7)
    ob_result = OrderBlockDetector().run(df)
    cfg: BacktestConfig = default_backtest_config("1h")

    long_only_row = run_engine(
        df,
        SHORT_DISABLED_PARAMS,
        cfg,
        ob_result,
        symbol="TEST/USDT",
        timeframe="1h",
        engine="long_only",
    )
    short_enabled_row = run_engine(
        df,
        SHORT_ENABLED_PARAMS,
        cfg,
        ob_result,
        symbol="TEST/USDT",
        timeframe="1h",
        engine="short_enabled",
    )
    # 롱 온리 프리셋은 숏 거래가 없어야 한다.
    assert long_only_row.short_trades == 0
    assert long_only_row.num_trades == long_only_row.long_trades

    rows = [long_only_row, short_enabled_row]
    frame = _rows_to_frame(rows)
    summary = _render_summary(frame)
    assert "long_only" in summary
    assert "short_enabled" in summary
    assert "롱 온리가 우월한 셀" in summary
