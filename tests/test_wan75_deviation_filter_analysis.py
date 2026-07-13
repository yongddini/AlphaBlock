"""backtest.wan75_deviation_filter_analysis 단위/스모크 테스트 (WAN-75).

이격 필터 검증 하네스가 실제로 배선되어 동작하는지 합성 데이터로 확인한다. 실데이터
3심볼×3TF 전체 산출(성분 분해·매칭 널·A안 교차검증)은 `backtest/reports/`의 CSV·
`wan75_summary.md`(재현 스크립트 `python -m backtest.wan75_deviation_filter_analysis`)로
별도 확인한다.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan75_deviation_filter_analysis import (
    PIPELINE_VARIANTS,
    REAL_VARIANTS,
    _config,
    _params,
    _shuffled_deviation_filter_components,
    run_a_engine_crosscheck,
    run_symbol_timeframe,
)
from strategy.confluence import ConfluenceStrategy
from strategy.models import DeviationFilterParams

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    # 실제로 거래가 발생하는 것이 검증된 설정(tests/test_zone_limit_backtest.py와 동일).
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


# ------------------------------------------------------------------------ 설정/변형 매핑


def test_config_applies_analysis_min_stop_distance_fraction() -> None:
    """저장소 기본값(0)이 아니라 이 분석 전용 하한(0.005)을 적용한다(코멘트 지적)."""
    cfg = _config("1h")
    assert cfg.risk_sizing is not None
    assert cfg.risk_sizing.min_stop_distance_fraction == pytest.approx(0.005)
    assert cfg.risk_sizing.leverage == pytest.approx(1.0)


def test_params_placebo_label_maps_to_same_filter_as_base_variant() -> None:
    base = _params("sma20_atr2")
    placebo = _params("sma20_atr2_placebo_shuffled_anchor")
    assert placebo.deviation_filter == base.deviation_filter


def test_pipeline_variants_cover_baseline_real_and_placebo() -> None:
    labels = dict(PIPELINE_VARIANTS)
    assert labels["baseline"] is False
    assert labels["sma20_atr2"] is False
    assert labels["sma20_atr2_placebo_shuffled_anchor"] is True
    assert labels["sma20_stdev2_bollinger_placebo_shuffled_anchor"] is True
    # 종가 기준 변형은 "위치" 개념이 없어 플라시보 대상이 아니다.
    assert "close_pct2_placebo_shuffled_anchor" not in labels


# ------------------------------------------------------------------------ 플라시보 셔플


def test_shuffled_components_preserves_width_and_anchor_multiset() -> None:
    df = pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(30)],
            "open": [100.0 + i for i in range(30)],
            "high": [101.0 + i for i in range(30)],
            "low": [99.0 + i for i in range(30)],
            "close": [100.0 + i for i in range(30)],
            "volume": [10.0] * 30,
        }
    )
    filt = DeviationFilterParams(anchor="sma", sma_length=5, width_kind="atr", width_value=2.0)
    real_anchor, real_width = ConfluenceStrategy.deviation_filter_components(df, filt, "close")
    shuffled_anchor, shuffled_width = _shuffled_deviation_filter_components(df, filt, "close")

    assert shuffled_width == pytest.approx(real_width, nan_ok=True)  # 폭 계산은 손대지 않는다.
    real_valid = sorted(v for v in real_anchor if not math.isnan(v))
    shuffled_valid = sorted(v for v in shuffled_anchor if not math.isnan(v))
    assert shuffled_valid == pytest.approx(real_valid)  # 같은 값 집합, 순서만 바뀜.
    assert shuffled_anchor != real_anchor  # 실제로 순서가 바뀌었다(결정적 시드).


# ------------------------------------------------------------------------ 종단 스모크


def test_run_symbol_timeframe_smoke_produces_all_variants() -> None:
    htf, one_min = _synthetic_pair()
    decomposition, matched_null = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", iterations=10
    )
    variants_seen = {row.variant for row in decomposition}
    assert variants_seen == {label for label, _ in PIPELINE_VARIANTS}
    assert all(row.segment in ("IS", "OOS") for row in decomposition)
    # baseline은 매칭 널 대상이 아니다(그 자체가 널 모집단이므로).
    assert all(row.variant != "baseline" for row in matched_null)


def test_run_a_engine_crosscheck_covers_real_variants_only() -> None:
    htf, _ = _synthetic_pair()
    rows = run_a_engine_crosscheck(htf, symbol="BTC/USDT:USDT", timeframe="1h")
    assert {row.variant for row in rows} == set(REAL_VARIANTS)
