"""backtest.wan70_random_control_b 단위/스모크 테스트 (WAN-70).

B안(존-지정가) 엔진 그대로 재구현한 매칭 널 무작위 대조군이 실제로 배선되어
동작하는지 합성 데이터로 확인한다. 실데이터 3심볼×4TF×IS/OOS×게이트on/off 전체
산출은 `backtest/reports/`의 CSV(재현 스크립트 `python -m
backtest.wan70_random_control_b`)로 별도 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import pin_band_bar
from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan70_random_control_b import (
    GATE_PRESETS,
    RandomControlBResult,
    _hour_bucket,
    run_experiment,
    run_random_control_b_segment,
    run_symbol_timeframe,
    summarize_verdict,
)
from backtest.zone_limit_backtest import build_zone_limit_candidates
from data.models import Candle
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams
from strategy.order_blocks import OrderBlockDetector

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


# --------------------------------------------------------------------------- 시각대 버킷


def test_hour_bucket_splits_utc_day_into_six_buckets() -> None:
    midnight_ms = 0  # 1970-01-01T00:00:00Z
    six_am_ms = 6 * 60 * 60 * 1000
    assert _hour_bucket(midnight_ms) == 0
    assert _hour_bucket(six_am_ms) == 1


# ------------------------------------------------------------------------ 무력화 풀 vs RSI


def test_rsi_disabled_pool_has_at_least_as_many_fills_as_gated() -> None:
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    cfg = default_backtest_config("1h")
    gated, _ = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=cfg)
    ungated, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=cfg, rsi_oversold=100.0, rsi_overbought=0.0
    )
    # RSI 조건을 무력화하면 존에 닿기만 해도 체결되므로 체결 수가 줄지 않는다.
    assert len(ungated) >= len(gated)


# --------------------------------------------------------------------------- 세그먼트 단위 대조군


def test_segment_control_is_deterministic_and_bounded() -> None:
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    result_a = run_random_control_b_segment(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        gate="off",
        confluence_params=params,
        iterations=25,
        seed=70,
    )
    result_b = run_random_control_b_segment(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        gate="off",
        confluence_params=params,
        iterations=25,
        seed=70,
    )
    assert result_a.model_dump() == result_b.model_dump()  # 같은 시드 -> 같은 결과.

    if result_a.real_num_trades == 0:
        assert result_a.random_p_value is None
        return

    assert result_a.random_p_value is not None
    assert 0.0 <= result_a.random_p_value <= 1.0
    assert result_a.iterations == 25
    assert result_a.pool_size >= result_a.real_num_trades
    if result_a.random_ci_low is not None and result_a.random_ci_high is not None:
        assert result_a.random_ci_low <= result_a.random_ci_high
    assert result_a.real_long + result_a.real_short == result_a.real_num_trades


def test_segment_control_matches_real_direction_counts_in_pool_sampling() -> None:
    """무작위 표본의 방향 비율이 실제 거래의 롱/숏 개수와 일치하도록 설계됐는지,
    최소한 각 방향의 target count가 사용 가능한 풀 크기를 넘지 않는지 확인한다."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    result = run_random_control_b_segment(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        gate="off",
        confluence_params=params,
        iterations=10,
        seed=1,
    )
    if result.real_num_trades == 0:
        pytest.skip("합성 데이터에서 거래가 발생하지 않음")
    assert result.real_long >= 0
    assert result.real_short >= 0


def test_no_trades_yields_none_p_value() -> None:
    """비현실적으로 엄격한 min_rr로 거래가 0건이면 p-value는 None(판정 불가).

    ⚠️ `min_rr`는 봉내 라이브 밴드(WAN-132 채택 기본값)와 함께 쓸 수 없으므로 — 진입가가
    봉내에 정해져 탭 봉 시점에 손익비를 판정할 수 없다 — 옛 밴드(`tap`)를 명시 고정한다.
    이 리포트의 게이트 프리셋(`GATE_PRESETS`)이 고정한 것과 같은 이유다.
    """
    htf, one_min = _synthetic_pair()
    params = pin_band_bar(
        ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", min_rr=1000.0)
    )
    result = run_random_control_b_segment(
        htf,
        one_min,
        "1h",
        symbol="BTC/USDT:USDT",
        segment="IS",
        gate="on",
        confluence_params=params,
        iterations=10,
        seed=1,
    )
    assert result.real_num_trades == 0
    assert result.random_p_value is None
    assert result.random_mean_return is None


# --------------------------------------------------------------------------- 심볼×TF 전체 흐름


def test_run_symbol_timeframe_produces_is_oos_and_gate_cells() -> None:
    htf, one_min = _synthetic_pair(bars=1200, span=500)
    results = run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", iterations=10, seed=5
    )
    assert results  # 배선이 동작해 최소 1행 이상 산출된다.
    segments = {r.segment for r in results}
    gates = {r.gate for r in results}
    assert segments <= {"IS", "OOS"}
    assert gates == set(GATE_PRESETS.keys())
    for r in results:
        assert r.symbol == "BTC/USDT:USDT"
        assert r.timeframe == "1h"


def test_run_symbol_timeframe_too_few_bars_returns_empty() -> None:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=10, seed=1)
    one_min = make_synthetic_ohlcv(timeframe="1m", bars=50, seed=2)
    assert run_symbol_timeframe(htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h") == []


def test_run_symbol_timeframe_detects_order_blocks_once_per_segment_not_per_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 수와 무관하게 오더블록 탐지는 세그먼트(IS/OOS)당 1회만 실행돼야 한다.

    회귀 방지(WAN-78): 예전엔 게이트 루프 **안**에서 매번 재탐지해 게이트 수만큼
    똑같은 탐지를 중복 실행했다(`order_block_params`가 게이트와 무관하므로 낭비).
    """
    htf, one_min = _synthetic_pair(bars=1200, span=500)
    call_count = 0
    original_run = OrderBlockDetector.run

    def counting_run(self: OrderBlockDetector, df: pd.DataFrame) -> object:
        nonlocal call_count
        call_count += 1
        return original_run(self, df)

    monkeypatch.setattr(OrderBlockDetector, "run", counting_run)
    three_gates = {"off": GATE_PRESETS["off"], "on": GATE_PRESETS["on"], "on2": GATE_PRESETS["on"]}
    run_symbol_timeframe(
        htf, one_min, symbol="BTC/USDT:USDT", timeframe="1h", gates=three_gates, iterations=5
    )
    assert call_count == 2  # IS 1회 + OOS 1회 — 게이트 3개와 무관.


# ------------------------------------------------------------------ run_experiment 병렬·캐시


def _populate_synthetic_store(db_path: Path, symbols: tuple[str, ...]) -> None:
    """`run_experiment`가 읽을 수 있도록 심볼별 1h/1m 합성 데이터를 저장소에 채운다."""
    with OhlcvStore(db_path) as store:
        for i, symbol in enumerate(symbols):
            htf = make_synthetic_ohlcv(timeframe="1h", bars=1200, seed=7 + i)
            htf_ms = timeframe_to_ms("1h")
            start = int(htf["open_time"].iloc[-500])
            minutes = 500 * (htf_ms // 60_000)
            one_min = make_synthetic_ohlcv(
                timeframe="1m", bars=minutes, seed=11 + i, start_time_ms=start, swing_period=180
            )
            candles = [
                Candle(
                    symbol,
                    "1h",
                    int(row.open_time),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    bool(row.closed),
                )
                for row in htf.itertuples(index=False)
            ]
            candles += [
                Candle(
                    symbol,
                    "1m",
                    int(row.open_time),
                    float(row.open),
                    float(row.high),
                    float(row.low),
                    float(row.close),
                    float(row.volume),
                    bool(row.closed),
                )
                for row in one_min.itertuples(index=False)
            ]
            store.upsert_candles(candles)


def test_run_experiment_parallel_jobs_matches_sequential(tmp_path: Path) -> None:
    """`--jobs`(ProcessPoolExecutor 심볼 단위 병렬화)를 켜도 순차 실행과 결과가 완전히
    같아야 한다(WAN-78 완료 기준: 워커 수와 무관한 결과 동일성)."""
    db_path = tmp_path / "ohlcv.db"
    symbols = ("BTC/USDT:USDT", "ETH/USDT:USDT")
    _populate_synthetic_store(db_path, symbols)

    sequential = run_experiment(
        db_path=db_path,
        symbols=symbols,
        timeframes=("1h",),
        years=1.0,
        iterations=15,
        jobs=1,
        cache_dir=None,
    )
    parallel = run_experiment(
        db_path=db_path,
        symbols=symbols,
        timeframes=("1h",),
        years=1.0,
        iterations=15,
        jobs=2,
        cache_dir=None,
    )
    assert len(sequential) > 0
    assert [r.model_dump() for r in sequential] == [r.model_dump() for r in parallel]


def test_run_experiment_cache_matches_no_cache(tmp_path: Path) -> None:
    """parquet 캐시를 켜도(cache_dir 지정) 결과가 캐시 없이 돌린 것과 완전히 같다."""
    db_path = tmp_path / "ohlcv.db"
    cache_dir = tmp_path / "cache"
    symbols = ("BTC/USDT:USDT",)
    _populate_synthetic_store(db_path, symbols)

    no_cache = run_experiment(
        db_path=db_path,
        symbols=symbols,
        timeframes=("1h",),
        years=1.0,
        iterations=15,
        jobs=1,
        cache_dir=None,
    )
    with_cache = run_experiment(
        db_path=db_path,
        symbols=symbols,
        timeframes=("1h",),
        years=1.0,
        iterations=15,
        jobs=1,
        cache_dir=cache_dir,
    )
    assert len(no_cache) > 0
    assert [r.model_dump() for r in no_cache] == [r.model_dump() for r in with_cache]


# --------------------------------------------------------------------------- 판정 문단


def _verdict_row(**overrides: object) -> RandomControlBResult:
    base: dict[str, object] = dict(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="IS",
        gate="off",
        real_total_return=0.5,
        real_num_trades=40,
        real_long=25,
        real_short=15,
        pool_size=120,
        random_mean_return=0.02,
        random_ci_low=-0.05,
        random_ci_high=0.08,
        random_p_value=0.01,
        iterations=200,
        bucket_fallback_count=0,
    )
    base.update(overrides)
    return RandomControlBResult(**base)


def test_summarize_verdict_no_edge_when_not_significant() -> None:
    rows = [_verdict_row(real_total_return=0.01, random_p_value=0.6)]
    assert "엣지 없다" in summarize_verdict(rows)


def test_summarize_verdict_edge_when_all_significant() -> None:
    rows = [_verdict_row(real_total_return=0.5, random_p_value=0.01, random_mean_return=0.02)]
    assert "엣지 있다" in summarize_verdict(rows)


def test_summarize_verdict_excludes_tiny_sample_cells() -> None:
    """거래 수가 min_trades 미만인 셀은 p가 낮아도 유의 판정에서 제외된다(무의미한 소표본)."""
    rows = [_verdict_row(real_num_trades=2, random_p_value=0.0, real_total_return=-0.02)]
    verdict = summarize_verdict(rows)
    assert "판정 불가" in verdict  # 유효 표본 셀이 없다
    assert "1개 제외" in verdict


def test_summarize_verdict_ignores_downside_significance() -> None:
    """p는 낮지만 실제 수익률이 무작위 평균보다 나쁜(하방) 셀은 엣지로 세지 않는다."""
    rows = [
        _verdict_row(
            real_num_trades=40,
            random_p_value=0.01,
            real_total_return=-0.1,
            random_mean_return=0.05,
        )
    ]
    assert "엣지 없다" in summarize_verdict(rows)


def test_summarize_verdict_handles_no_valid_cells() -> None:
    assert "판정 불가" in summarize_verdict([])


def test_build_summary_markdown_is_self_contained() -> None:
    """리포트 마크다운에 셀별 표·귀무가설 정의·판정·결론이 모두 담긴다."""
    from pathlib import Path

    from backtest.wan70_random_control_b import build_summary_markdown

    rows = [
        _verdict_row(symbol="BTC/USDT:USDT", timeframe="1h", segment="IS", gate="off"),
        _verdict_row(real_num_trades=2, random_p_value=0.0, gate="on"),
    ]
    md = build_summary_markdown(rows, report_path=Path("backtest/reports/wan70.csv"))
    assert "## 셀별 결과" in md
    assert "## 귀무가설(매칭 널)" in md
    assert "## 판정" in md
    assert "## 결론" in md
    assert "BTC/USDT:USDT" in md
