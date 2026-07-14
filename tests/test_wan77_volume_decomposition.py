"""backtest.wan77_volume_decomposition 단위/스모크 테스트 (WAN-77).

3심볼×3TF 전체 실데이터 산출(분위별 성과·단조성·손절폭 민감도·조건부 매칭 널)은
`backtest/reports/wan77_*.csv`·`wan77_summary.md`(재현: `python -m
backtest.wan77_volume_decomposition`)로 별도 확인한다. 여기서는 결정적 합성 데이터·
수기 구성 입력으로 핵심 로직(축 계산·분위 집계·단조성 판정·오더블록 조인·손절폭
재시퀀싱)만 검증한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade, TradeFill
from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan77_volume_decomposition import (
    A_ENGINE_PARAMS,
    B_ENGINE_PARAMS,
    SENSITIVITY_FLOORS,
    VolumeQuantileRow,
    _annotate_percentile,
    _imbalance,
    _JoinedTrade,
    _quantile_rows,
    _relative_volume,
    collect_cell_joined,
    floor_sensitivity_rows,
    monotonicity_verdict,
    stage2_qualifying_axes,
)
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.models import OrderBlock, OrderBlockDirection
from strategy.order_blocks import OrderBlockDetector

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _make_ob(
    *,
    ob_volume: float = 100.0,
    ob_low_volume: float = 40.0,
    ob_high_volume: float = 60.0,
    confirmed_time: int = 1000,
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=110.0,
        bottom=100.0,
        start_time=confirmed_time - 3000,
        confirmed_time=confirmed_time,
        ob_volume=ob_volume,
        ob_low_volume=ob_low_volume,
        ob_high_volume=ob_high_volume,
    )


def _make_trade(*, return_pct: float, realized_pnl: float) -> Trade:
    return Trade(
        side=PositionSide.LONG,
        entry_time=1000,
        entry_price=100.0,
        quantity=1.0,
        entry_fee=0.0,
        exits=[
            TradeFill(time=2000, price=101.0, quantity=1.0, fee=0.0, reason=ExitReason.TAKE_PROFIT)
        ],
        realized_pnl=realized_pnl,
        return_pct=return_pct,
    )


# ------------------------------------------------------------------------ 축 계산


def test_relative_volume_basic_ratio() -> None:
    times = list(range(30))
    volumes = [10.0] * 30
    # 존 형성 3봉(t-2..t)=t 위치 25, 이전 20봉(3..23) 평균 10 → 기대 3봉 합=30.
    rel = _relative_volume(times, volumes, confirmed_time=25, ob_volume=60.0, window=20)
    assert rel == pytest.approx(2.0)  # 60 / (10*3)


def test_relative_volume_none_when_insufficient_warmup() -> None:
    times = list(range(10))
    volumes = [10.0] * 10
    assert _relative_volume(times, volumes, confirmed_time=5, ob_volume=60.0, window=20) is None


def test_relative_volume_none_when_baseline_zero() -> None:
    times = list(range(30))
    volumes = [0.0] * 30
    assert _relative_volume(times, volumes, confirmed_time=25, ob_volume=60.0, window=20) is None


def test_imbalance_symmetric_is_one() -> None:
    ob = _make_ob(ob_low_volume=50.0, ob_high_volume=50.0)
    assert _imbalance(ob) == pytest.approx(1.0)


def test_imbalance_skewed_is_near_zero() -> None:
    ob = _make_ob(ob_low_volume=1.0, ob_high_volume=99.0)
    assert _imbalance(ob) == pytest.approx(1.0 / 99.0)


def test_imbalance_none_when_both_zero() -> None:
    ob = _make_ob(ob_low_volume=0.0, ob_high_volume=0.0)
    assert _imbalance(ob) is None


def test_annotate_percentile_ranks_ascending_volume() -> None:
    joined = [
        _JoinedTrade(
            trade=_make_trade(return_pct=0.0, realized_pnl=0.0),
            order_block=_make_ob(ob_volume=v),
            r_multiple=None,
            relative_volume=None,
            imbalance=None,
            volume_percentile=None,
        )
        for v in (30.0, 10.0, 20.0)
    ]
    annotated = _annotate_percentile(joined)
    by_volume = {jt.order_block.ob_volume: jt.volume_percentile for jt in annotated}
    assert by_volume[10.0] == pytest.approx(0.0)
    assert by_volume[20.0] == pytest.approx(0.5)
    assert by_volume[30.0] == pytest.approx(1.0)


# ------------------------------------------------------------------------ 단조성 판정


def _row(rank: int, avg_r: float | None) -> VolumeQuantileRow:
    return VolumeQuantileRow(
        symbol="TEST/USDT:USDT",
        timeframe="1h",
        engine="B",
        axis="relative_volume",
        quantile=f"Q{rank}",
        quantile_rank=rank,
        axis_min=0.0,
        axis_max=1.0,
        n=25,
        win_rate=0.5,
        avg_r=avg_r,
        profit_factor=1.2,
        total_return_sum=0.1,
    )


def test_monotonicity_increasing() -> None:
    rows = [_row(1, -0.2), _row(2, 0.0), _row(3, 0.1), _row(4, 0.3)]
    assert monotonicity_verdict(rows) == "단조 증가"


def test_monotonicity_decreasing() -> None:
    rows = [_row(1, 0.3), _row(2, 0.1), _row(3, 0.0), _row(4, -0.2)]
    assert monotonicity_verdict(rows) == "단조 감소"


def test_monotonicity_non_monotonic() -> None:
    rows = [_row(1, 0.1), _row(2, -0.2), _row(3, 0.3), _row(4, 0.0)]
    assert monotonicity_verdict(rows) == "비단조(들쭉날쭉)"


def test_monotonicity_insufficient_data() -> None:
    rows = [_row(1, None), _row(2, 0.1)]
    assert monotonicity_verdict(rows) == "판정 불가(표본 부족)"


def test_monotonicity_flat() -> None:
    rows = [_row(1, 0.1), _row(2, 0.1), _row(3, 0.1)]
    assert monotonicity_verdict(rows) == "평탄"


# ------------------------------------------------------------------------ 분위 집계


def test_quantile_rows_reports_n_and_metrics_per_bucket() -> None:
    # relative_volume 0..7 오름차순 8개 거래 → 4분위(2개씩), 뒤로 갈수록 손익이 커짐.
    joined = []
    for i in range(8):
        pnl = float(i) - 3.5  # 상위 분위일수록 평균 손익이 커지도록 설계.
        joined.append(
            _JoinedTrade(
                trade=_make_trade(return_pct=pnl / 100.0, realized_pnl=pnl),
                order_block=_make_ob(ob_volume=100.0),
                r_multiple=pnl / 10.0,
                relative_volume=float(i),
                imbalance=None,
                volume_percentile=None,
            )
        )
    rows = _quantile_rows(
        joined, symbol="TEST/USDT:USDT", timeframe="1h", engine="B", axis_name="relative_volume"
    )
    assert len(rows) == 4
    assert sum(r.n for r in rows) == 8
    assert monotonicity_verdict(rows) == "단조 증가"


def test_quantile_rows_empty_when_insufficient_valid_values() -> None:
    joined = [
        _JoinedTrade(
            trade=_make_trade(return_pct=0.0, realized_pnl=0.0),
            order_block=_make_ob(),
            r_multiple=None,
            relative_volume=None,
            imbalance=None,
            volume_percentile=None,
        )
        for _ in range(3)
    ]
    assert (
        _quantile_rows(joined, symbol="T", timeframe="1h", engine="B", axis_name="relative_volume")
        == []
    )


# ------------------------------------------------------------------------ 2단계 발동 규칙


def test_stage2_qualifying_axes_requires_increasing_and_min_trades() -> None:
    increasing_rows = [_row(1, -0.1), _row(2, 0.0), _row(3, 0.1), _row(4, 0.2)]
    flat_rows = [_row(1, 0.1), _row(2, 0.1), _row(3, 0.1), _row(4, 0.1)]
    small_n_rows = [VolumeQuantileRow(**{**r.model_dump(), "n": 5}) for r in increasing_rows]
    assert stage2_qualifying_axes({"relative_volume": increasing_rows}) == ["relative_volume"]
    assert stage2_qualifying_axes({"imbalance": flat_rows}) == []
    assert stage2_qualifying_axes({"relative_volume": small_n_rows}) == []


# ------------------------------------------------------------------ 오더블록 조인 배선(합성)


def test_candidate_carries_order_block_reference() -> None:
    """WAN-77이 `_Candidate`에 추가한 `order_block` 필드가 실제로 채워진다.

    `B_ENGINE_PARAMS`는 기본 롱온리(WAN-69)인데, 이 합성 시드(`tests/test_zone_
    limit_backtest.py`의 `test_end_to_end_runs_and_is_deterministic` 코멘트 참고)는
    숏 셋업만 낸다 — 거래가 0건이면(다른 wan7x 테스트와 동일 관례) 스킵한다.
    """
    htf, one_min = _synthetic_pair()
    cfg = default_backtest_config("1h")
    ob_result = OrderBlockDetector().run(htf)
    candidates, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=B_ENGINE_PARAMS, cfg=cfg, order_block_result=ob_result
    )
    if not candidates:
        pytest.skip("합성 데이터에서 거래가 발생하지 않음(롱온리 기본값 vs 숏 전용 시드)")
    assert all(isinstance(c, _Candidate) and c.order_block is not None for c in candidates)


def test_collect_cell_joined_attaches_order_block_and_axes() -> None:
    htf, one_min = _synthetic_pair()
    joined_by_engine = collect_cell_joined(htf, one_min, timeframe="1h")
    assert set(joined_by_engine) == {"A", "B"}
    b_joined = joined_by_engine["B"]
    if not b_joined:
        pytest.skip("합성 데이터에서 거래가 발생하지 않음(롱온리 기본값 vs 숏 전용 시드)")
    for jt in b_joined:
        assert jt.order_block is not None
        assert 0.0 <= (jt.volume_percentile or 0.0) <= 1.0


def test_collect_cell_joined_a_engine_uses_default_params() -> None:
    assert A_ENGINE_PARAMS.entry_mode == "close"
    assert A_ENGINE_PARAMS.rsi_mode == "closed_bar"
    assert B_ENGINE_PARAMS.entry_mode == "zone_limit"
    assert B_ENGINE_PARAMS.rsi_mode == "realtime"


# ------------------------------------------------------------------------ 손절폭 민감도


def test_floor_sensitivity_rows_covers_all_floors() -> None:
    htf, one_min = _synthetic_pair()
    rows = floor_sensitivity_rows(htf, one_min, symbol="TEST/USDT:USDT", timeframe="1h")
    assert {r.min_stop_distance_fraction for r in rows} == set(SENSITIVITY_FLOORS)
    for r in rows:
        assert r.num_trades >= 0


def test_floor_sensitivity_higher_floor_never_increases_trade_count() -> None:
    """손절폭 하한이 높을수록(더 엄격) 사이징이 스킵되는 거래가 늘거나 같아야 한다."""
    htf, one_min = _synthetic_pair()
    rows = floor_sensitivity_rows(
        htf, one_min, symbol="TEST/USDT:USDT", timeframe="1h", floors=(0.0, 0.01)
    )
    by_floor = {r.min_stop_distance_fraction: r.num_trades for r in rows}
    assert by_floor[0.01] <= by_floor[0.0]


# ------------------------------------------------------------------ BacktestConfig 배선(문서화용)


def test_default_backtest_config_has_risk_sizing_for_r_multiple() -> None:
    cfg = default_backtest_config("1h")
    assert isinstance(cfg, BacktestConfig)
    assert cfg.risk_sizing is not None, "R 배수 계산은 risk_sizing이 있어야 정의된다"
