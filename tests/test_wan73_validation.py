"""WAN-73 채택 검증 하네스 테스트.

`decide_adoption`의 판정 로직(IS·OOS 양쪽 유의 + 체결률 반영 기대값 양수라야 채택)과
체결률 반영 R 기대값 계산이 옳은지 검증한다. 실데이터 없이 결과 모델을 합성해 판정을
검증하고, `compute_fill_rate_expectancy`는 소형 합성 시계열로 스모크 테스트한다.
"""

from __future__ import annotations

from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan70_random_control_b import RandomControlBResult
from backtest.wan73_validation import (
    WAN73_NEW_PARAMS,
    FillRateExpectancy,
    compute_fill_rate_expectancy,
    decide_adoption,
)


def _cell(
    symbol: str,
    timeframe: str,
    segment: str,
    *,
    real: float,
    n: int,
    rand_mean: float,
    p: float,
) -> RandomControlBResult:
    return RandomControlBResult(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        gate="wan73_new_rules",
        real_total_return=real,
        real_num_trades=n,
        real_long=n,
        real_short=0,
        pool_size=n * 2,
        random_mean_return=rand_mean,
        random_ci_low=rand_mean - 0.1,
        random_ci_high=rand_mean + 0.1,
        random_p_value=p,
        iterations=200,
        bucket_fallback_count=0,
    )


def _fr(symbol: str, timeframe: str, adjusted: float | None) -> FillRateExpectancy:
    return FillRateExpectancy(
        symbol=symbol,
        timeframe=timeframe,
        eligible=100,
        filled=40,
        fill_rate=0.4,
        mean_r_per_filled_trade=(adjusted / 0.4 if adjusted is not None else None),
        opportunity_adjusted_expectancy=adjusted,
    )


def test_decide_adoption_rejects_when_no_cell_significant_in_both_segments() -> None:
    """개별 셀은 유의해도 IS·OOS 양쪽이 아니면 기각한다(워크포워드 강건성 요구)."""
    results = [
        _cell("ETH/USDT:USDT", "1h", "IS", real=0.3, n=200, rand_mean=0.05, p=0.01),  # IS만 유의
        _cell("ETH/USDT:USDT", "1h", "OOS", real=0.1, n=100, rand_mean=0.08, p=0.30),  # OOS 비유의
        _cell("SOL/USDT:USDT", "4h", "IS", real=0.05, n=50, rand_mean=0.06, p=0.51),
        _cell("SOL/USDT:USDT", "4h", "OOS", real=0.09, n=40, rand_mean=0.04, p=0.02),  # OOS만 유의
    ]
    fill_rates = [_fr("ETH/USDT:USDT", "1h", 0.1), _fr("SOL/USDT:USDT", "4h", 0.1)]
    decision, reasons = decide_adoption(results, fill_rates)
    assert decision == "REJECT"
    assert reasons


def test_decide_adoption_partial_when_both_segments_significant_and_expectancy_positive() -> None:
    """IS·OOS 양쪽 유의 + 체결률 반영 기대값 양수면 부분 채택 후보다."""
    results = [
        _cell("ETH/USDT:USDT", "1h", "IS", real=0.3, n=200, rand_mean=0.05, p=0.01),
        _cell("ETH/USDT:USDT", "1h", "OOS", real=0.25, n=120, rand_mean=0.05, p=0.02),
    ]
    fill_rates = [_fr("ETH/USDT:USDT", "1h", 0.12)]
    decision, reasons = decide_adoption(results, fill_rates)
    assert decision == "ADOPT_PARTIAL"
    assert any("ETH/USDT:USDT/1h" in r for r in reasons)


def test_decide_adoption_rejects_when_robust_but_fill_rate_expectancy_not_positive() -> None:
    """IS·OOS 양쪽 유의해도 체결률 반영 기대값이 양수가 아니면 기각한다."""
    results = [
        _cell("ETH/USDT:USDT", "1h", "IS", real=0.3, n=200, rand_mean=0.05, p=0.01),
        _cell("ETH/USDT:USDT", "1h", "OOS", real=0.25, n=120, rand_mean=0.05, p=0.02),
    ]
    fill_rates = [_fr("ETH/USDT:USDT", "1h", -0.05)]
    decision, _ = decide_adoption(results, fill_rates)
    assert decision == "REJECT"


def test_decide_adoption_ignores_small_sample_significant_cells() -> None:
    """표본 20건 미만 셀은 p가 낮아도 유의로 세지 않는다(소표본 방어)."""
    results = [
        _cell("BTC/USDT:USDT", "1d", "IS", real=0.3, n=4, rand_mean=0.0, p=0.0),
        _cell("BTC/USDT:USDT", "1d", "OOS", real=0.2, n=3, rand_mean=0.0, p=0.0),
    ]
    fill_rates = [_fr("BTC/USDT:USDT", "1d", 0.2)]
    decision, _ = decide_adoption(results, fill_rates)
    assert decision == "REJECT"


def test_compute_fill_rate_expectancy_smoke() -> None:
    """소형 합성 시계열에서 체결률·R 기대값이 정합적 범위로 산출된다."""
    htf = make_synthetic_ohlcv(timeframe="1h", bars=600, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-120])
    minutes = 120 * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    result = compute_fill_rate_expectancy(
        htf, one_min, "1h", symbol="BTC/USDT:USDT", params=WAN73_NEW_PARAMS
    )
    assert result.eligible >= result.filled >= 0
    if result.fill_rate is not None:
        assert 0.0 <= result.fill_rate <= 1.0
    if result.mean_r_per_filled_trade is not None and result.fill_rate is not None:
        assert result.opportunity_adjusted_expectancy is not None
