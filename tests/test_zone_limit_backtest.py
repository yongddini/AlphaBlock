"""존-지정가 + 실시간 RSI(B안) 백테스트 파이프라인 테스트 (WAN-41).

`entry_mode=zone_limit` + `rsi_mode=realtime`이 오더블록 탐지에 배선되어 end-to-end로
동작하는지, 1분봉 서브스텝 재구성으로 진입/청산이 이뤄지는지, A안과 동일 비용 모델을
쓰는지 검증한다.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.zone_limit_backtest import (
    _Candidate,
    _IncrementalRsiSeeder,
    _line_snapshots,
    _to_trade,
    build_result_from_trades,
    build_zone_limit_candidates,
    run_zone_limit_backtest,
    run_zone_limit_backtest_verbose,
)
from common.costs import Liquidity
from data.models import FundingRate
from strategy.indicators import emas, vwma
from strategy.models import ConfluenceParams, DeviationFilterParams
from strategy.realtime_rsi import RealtimeRsi


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def test_end_to_end_runs_and_is_deterministic() -> None:
    htf, one_min = _synthetic_pair()
    # 이 합성 시드는 숏 셋업만 낸다 — WAN-69 기본값(롱 온리)과 무관하게 엔진 동작을
    # 검증하려면 명시적으로 켠다. deviation_filter(WAN-81 볼린저 기본값)은 이 작은
    # 합성 데이터셋에서 후보를 모두 걸러낼 수 있으므로 파이프라인 배선 검증과는
    # 무관하게 꺼 둔다.
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    result_a = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    result_b = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    # 결정적: 같은 입력 → 같은 결과.
    assert result_a.metrics.num_trades == result_b.metrics.num_trades
    assert [t.realized_pnl for t in result_a.trades] == [t.realized_pnl for t in result_b.trades]
    # 파이프라인이 실제로 진입을 산출한다(존에 닿는 순간 진입 동작).
    assert result_a.metrics.num_trades >= 1


def test_verbose_returns_fill_and_penetration_stats() -> None:
    """진단 통계: 대상 셋업·체결·관통 수를 반환하고 체결률이 정합적이다(WAN-46)."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    result, stats = run_zone_limit_backtest_verbose(htf, one_min, "1h", confluence_params=params)
    assert stats.eligible >= stats.filled >= 0
    assert 0 <= stats.penetrations <= stats.filled
    assert stats.fill_rate is not None and 0.0 <= stats.fill_rate <= 1.0
    # 체결 수는 최종 거래 수 이상이다(단일 포지션 시퀀싱으로 일부가 빠질 수 있으므로).
    assert stats.filled >= result.metrics.num_trades


def test_verbose_matches_plain_result() -> None:
    """verbose와 기본 함수가 같은 결과를 낸다(래핑 일관성)."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    plain = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    verbose, _ = run_zone_limit_backtest_verbose(htf, one_min, "1h", confluence_params=params)
    assert plain.metrics.num_trades == verbose.metrics.num_trades
    assert [t.realized_pnl for t in plain.trades] == [t.realized_pnl for t in verbose.trades]


def test_trades_reference_1m_substep_times() -> None:
    """진입/청산 시각이 1분봉 서브스텝(1m 해상도)에서 나온다 — 봉 내부 재구성 증거."""
    htf, one_min = _synthetic_pair()
    minute_times = set(int(t) for t in one_min["open_time"].astype("int64"))
    # 이 합성 시드는 숏 셋업만 낸다 — WAN-69 기본값(롱 온리)과 무관하게 엔진 동작을
    # 검증하려면 명시적으로 켠다. deviation_filter는 WAN-81 볼린저 기본값이 이 작은
    # 합성 데이터셋의 후보를 모두 걸러낼 수 있어 배선 검증을 위해 꺼 둔다.
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    result = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    assert result.trades
    for trade in result.trades:
        assert trade.entry_time in minute_times
        assert trade.exit_time in minute_times


def test_empty_1m_yields_no_trades() -> None:
    """1분봉이 상위TF 창을 커버하지 않으면 셋업이 평가에서 제외된다(폴백)."""
    htf = make_synthetic_ohlcv(timeframe="1h", bars=400, seed=7)
    # 상위TF 범위 밖(미래)의 1분봉 → 어떤 셋업도 커버하지 않음.
    far = int(htf["open_time"].iloc[-1]) + 10 * timeframe_to_ms("1h")
    one_min = make_synthetic_ohlcv(timeframe="1m", bars=200, seed=3, start_time_ms=far)
    result = run_zone_limit_backtest(htf, one_min, "1h")
    assert result.metrics.num_trades == 0


def test_default_gates_off_preserve_zone_limit_behavior() -> None:
    """WAN-68 게이트 3종 모두 기본값(꺼짐/WAN-69 롱 온리)이면 B안 결과가 명시적 off와 동일하다."""
    htf, one_min = _synthetic_pair()
    default_params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    explicit_off = default_params.model_copy(
        update={"min_rr": None, "long_max_deviation": None, "short_enabled": False}
    )
    a = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=default_params)
    b = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=explicit_off)
    assert [t.realized_pnl for t in a.trades] == [t.realized_pnl for t in b.trades]


def test_short_enabled_false_yields_only_long_trades() -> None:
    htf, one_min = _synthetic_pair()
    baseline_params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    baseline = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=baseline_params)
    assert any(t.side is PositionSide.SHORT for t in baseline.trades)  # 전제: 숏 거래가 있다

    params = baseline_params.model_copy(update={"short_enabled": False})
    result = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    assert all(t.side is PositionSide.LONG for t in result.trades)


def test_min_rr_gate_does_not_increase_trade_count() -> None:
    htf, one_min = _synthetic_pair()
    base = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    baseline = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=base)
    gated = run_zone_limit_backtest(
        htf, one_min, "1h", confluence_params=base.model_copy(update={"min_rr": 50.0})
    )
    # 매우 높은 R:R 요구는 거래를 줄이거나 그대로 두지, 늘리지 않는다.
    assert gated.metrics.num_trades <= baseline.metrics.num_trades


def test_long_deviation_gate_does_not_increase_long_trade_count() -> None:
    htf, one_min = _synthetic_pair()
    base = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", long_deviation_gate_ema_length=20
    )
    baseline = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=base)
    strict = run_zone_limit_backtest(
        htf, one_min, "1h", confluence_params=base.model_copy(update={"long_max_deviation": -0.9})
    )
    long_baseline = sum(1 for t in baseline.trades if t.side is PositionSide.LONG)
    long_strict = sum(1 for t in strict.trades if t.side is PositionSide.LONG)
    assert long_strict <= long_baseline


def test_cost_model_applied_slippage_and_fees() -> None:
    """진입/청산 체결가에 슬리피지가 불리하게, 수수료가 차감돼 반영된다."""
    htf, one_min = _synthetic_pair()
    # 이 합성 시드는 숏 셋업만 낸다 — WAN-69 기본값(롱 온리)과 무관하게 엔진 동작을
    # 검증하려면 명시적으로 켠다. deviation_filter는 WAN-81 볼린저 기본값이 이 작은
    # 합성 데이터셋의 후보를 모두 걸러낼 수 있어 배선 검증을 위해 꺼 둔다.
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    zero = run_zone_limit_backtest(
        htf,
        one_min,
        "1h",
        confluence_params=params,
        backtest_config=BacktestConfig(fee_rate=0.0, slippage=0.0),
    )
    costed = run_zone_limit_backtest(
        htf,
        one_min,
        "1h",
        confluence_params=params,
        backtest_config=BacktestConfig(fee_rate=0.001, slippage=0.001),
    )
    assert zero.trades and costed.trades
    # 비용이 붙으면 동일 셋업의 순손익이 더 낮다.
    assert costed.trades[0].realized_pnl < zero.trades[0].realized_pnl
    assert costed.trades[0].entry_fee > 0.0


def test_build_result_from_trades_single_position_sequencing() -> None:
    """겹치는 거래는 단일 포지션 제약으로 배치되고 자본곡선이 순차 반영된다."""
    cfg = BacktestConfig()
    trades = [
        Trade(
            side=PositionSide.LONG,
            entry_time=1_000,
            entry_price=100.0,
            quantity=1.0,
            entry_fee=0.0,
            exits=[],
            realized_pnl=50.0,
            return_pct=0.5,
        ),
    ]
    # exits가 비면 exit_time 접근이 실패하므로 최소 하나의 fill을 넣는다.
    from backtest.models import ExitReason, TradeFill

    trade = trades[0].model_copy(
        update={
            "exits": [
                TradeFill(
                    time=2_000, price=150.0, quantity=1.0, fee=0.0, reason=ExitReason.TAKE_PROFIT
                )
            ]
        }
    )
    result = build_result_from_trades([trade], cfg, "1h")
    assert result.metrics.num_trades == 1
    assert result.equity_curve[-1].equity == cfg.initial_capital + 50.0


# ------------------------------------------------------------------ WAN-73 신규 파라미터


def test_retap_every_tap_yields_at_least_as_many_eligible_setups() -> None:
    """`retap_mode="every_tap"`는 존 생존 중 모든 탭을 후보로 내 대상 셋업이 늘거나 같다."""
    htf, one_min = _synthetic_pair()
    cfg_kwargs = dict(htf_df=htf, df_1m=one_min, timeframe="1h", cfg=BacktestConfig())
    once_params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    every_tap_params = once_params.model_copy(update={"retap_mode": "every_tap"})
    _, once_stats = build_zone_limit_candidates(params=once_params, **cfg_kwargs)
    _, every_tap_stats = build_zone_limit_candidates(params=every_tap_params, **cfg_kwargs)
    assert every_tap_stats.eligible >= once_stats.eligible


def test_limit_valid_bars_none_runs_end_to_end() -> None:
    """`limit_valid_bars=None`(무기한 대기)도 정상 동작하며 결정적이다."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", limit_valid_bars=None)
    result_a = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    result_b = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    assert [t.realized_pnl for t in result_a.trades] == [t.realized_pnl for t in result_b.trades]


def test_take_profit_mode_fixed_r_runs_end_to_end() -> None:
    """`take_profit_mode="fixed_r"`도 B안 파이프라인에서 정상 동작한다."""
    htf, one_min = _synthetic_pair()
    # 이 합성 시드는 숏 셋업만 낸다 — WAN-69 기본값(롱 온리)과 무관하게 엔진 동작을
    # 검증하려면 명시적으로 켠다. deviation_filter는 WAN-81 볼린저 기본값이 이 작은
    # 합성 데이터셋의 후보를 모두 걸러낼 수 있어 배선 검증을 위해 꺼 둔다.
    params = ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        take_profit_mode="fixed_r",
        take_profit_r=2.0,
        short_enabled=True,
        deviation_filter=None,
    )
    result = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    assert result.metrics.num_trades >= 1


def test_deviation_filter_off_yields_at_least_as_many_trades_as_default() -> None:
    """`deviation_filter=None`(필터 끔)은 WAN-81 기본 볼린저 필터보다 거래를 줄이지 않는다.

    이격 필터는 순수 거부 필터(규칙 3에서만 진입을 기각)이므로, 꺼두면 후보가
    늘거나 같아야 한다. (WAN-81 이전엔 기본이 `None`이라 이 둘이 항상 동일했지만,
    이제 기본이 볼린저라 더 이상 항상 같지 않다.)
    """
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True)
    baseline = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=params)
    explicit_off = run_zone_limit_backtest(
        htf, one_min, "1h", confluence_params=params.model_copy(update={"deviation_filter": None})
    )
    assert len(explicit_off.trades) >= len(baseline.trades)


def test_deviation_filter_extremely_wide_band_rejects_all_setups() -> None:
    """밴드 폭이 매우 크면(규칙 3 상시 발동) 모든 셋업이 기각되어 후보가 0이 된다."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        short_enabled=True,
        deviation_filter=DeviationFilterParams(anchor="close", width_kind="pct", width_value=5.0),
    )
    candidates, stats = build_zone_limit_candidates(
        htf_df=htf, df_1m=one_min, timeframe="1h", params=params, cfg=BacktestConfig()
    )
    assert candidates == []
    assert stats.eligible == 0


def test_deviation_filter_does_not_increase_eligible_setups() -> None:
    htf, one_min = _synthetic_pair()
    base = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True)
    baseline, baseline_stats = build_zone_limit_candidates(
        htf_df=htf, df_1m=one_min, timeframe="1h", params=base, cfg=BacktestConfig()
    )
    filtered_params = base.model_copy(
        update={
            "deviation_filter": DeviationFilterParams(
                anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0
            )
        }
    )
    filtered, filtered_stats = build_zone_limit_candidates(
        htf_df=htf, df_1m=one_min, timeframe="1h", params=filtered_params, cfg=BacktestConfig()
    )
    assert filtered_stats.eligible <= baseline_stats.eligible


def test_rsi_gate_mode_override_disables_gate_for_pool_construction() -> None:
    """`build_zone_limit_candidates`의 `rsi_gate_mode` 오버라이드로 게이트를 무력화한 풀을
    만들 수 있다(WAN-70 매칭 널 재사용 대비)."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    gated, _ = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=BacktestConfig())
    ungated, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=BacktestConfig(), rsi_gate_mode="none"
    )
    assert len(ungated) >= len(gated)


# --------------------------------------------------------------------------- #
# 성능 호이스팅 회귀 테스트 (WAN-78)
# --------------------------------------------------------------------------- #


def test_incremental_rsi_seeder_matches_full_reseed_for_increasing_cuts() -> None:
    closes = [100.0 + i * 0.3 - (i % 7) for i in range(500)]
    seeder = _IncrementalRsiSeeder(closes, length=14)
    for cut in (0, 1, 14, 15, 100, 100, 250, 499):
        expected = RealtimeRsi.seed_from_closed(closes[:cut], length=14)
        assert seeder.seed(cut) == expected


def test_incremental_rsi_seeder_matches_full_reseed_for_out_of_order_cuts() -> None:
    """오더블록 확정 순서와 첫 탭 순서가 어긋나 cut이 감소하는 드문 경우도 정확해야
    한다(`entry_candidate_signals`가 전역적으로 시각 오름차순임을 보장하지 않음)."""
    closes = [100.0 + i * 0.3 - (i % 7) for i in range(500)]
    seeder = _IncrementalRsiSeeder(closes, length=14)
    for cut in (200, 50, 300, 10, 400, 5):
        expected = RealtimeRsi.seed_from_closed(closes[:cut], length=14)
        assert seeder.seed(cut) == expected


def test_incremental_rsi_seeder_returns_independent_copy() -> None:
    """`seed()` 반환값을 호출자가 변형해도(시뮬레이션이 상태를 진행시킴) 다음 시딩에
    영향이 없어야 한다 — 내부 상태를 그대로 공유하면 이후 시그널의 RSI가 오염된다."""
    closes = [100.0 + i for i in range(50)]
    seeder = _IncrementalRsiSeeder(closes, length=14)
    state = seeder.seed(20)
    state.commit(9999.0)
    again = seeder.seed(20)
    assert again == RealtimeRsi.seed_from_closed(closes[:20], length=14)


def test_line_snapshots_matches_manual_ema_vwma_per_position() -> None:
    """사전계산된 스냅샷이 시그널마다 재계산하던 이전 로직과 값이 같아야 한다."""
    htf, _ = _synthetic_pair(bars=400)
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", use_line_take_profit=True
    )
    snapshots = _line_snapshots(params, htf)
    assert snapshots is not None
    assert params.tp_vwma_length is not None
    ema_frame = emas(htf, lengths=params.sorted_tp_ema_lengths, source=params.source)
    vwma_series = vwma(htf, length=params.tp_vwma_length, source=params.source)
    for pos in (0, 50, 100, 200, 399):
        expected: list[float] = []
        for length in params.sorted_tp_ema_lengths:
            v = float(ema_frame[f"ema_{length}"].iloc[pos])
            if not math.isnan(v):
                expected.append(v)
        vv = float(vwma_series.iloc[pos])
        if not math.isnan(vv):
            expected.append(vv)
        assert snapshots[pos] == expected


def test_line_snapshots_none_when_line_take_profit_disabled() -> None:
    htf, _ = _synthetic_pair()
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", use_line_take_profit=False
    )
    assert _line_snapshots(params, htf) is None


# ------------------------------------------------------- WAN-95 지정가 채택 배선


def test_maker_fee_default_is_two_bp_and_below_taker() -> None:
    """WAN-95 드리프트 가드: 지정가(메이커) 진입에 테이커 요율이 붙지 않는다.

    기본값이 지정가 진입으로 바뀐 이상 `maker_fee_rate=None`(→ 테이커 4bp 폴백)은
    비용을 2bp 과대 계상한다. 공용 `CostModel`의 메이커 기본값과도 같은 값이어야
    한다 — 두 곳이 다른 상수를 들면 페이퍼-백테스트 패리티가 무의미해진다(WAN-37).
    """
    cfg = BacktestConfig()
    assert cfg.maker_fee_rate == 0.0002
    costs = cfg.cost_model
    assert costs.fee_rate(Liquidity.MAKER) == 0.0002
    assert costs.fee_rate(Liquidity.TAKER) == cfg.fee_rate == 0.0004
    assert costs.fee_rate(Liquidity.MAKER) < costs.fee_rate(Liquidity.TAKER)
    # 메이커(지정가) 체결에는 슬리피지가 붙지 않는다.
    assert costs.slippage_for(Liquidity.MAKER) == 0.0
    assert costs.slippage_for(Liquidity.TAKER) > 0.0


def test_entry_is_maker_and_exit_is_taker() -> None:
    """진입=메이커(슬리피지 0), 청산=테이커(수수료+슬리피지)로 구분 적용된다."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    cfg = BacktestConfig(fee_rate=0.0004, maker_fee_rate=0.0002, slippage=0.0005)
    result = run_zone_limit_backtest(
        htf, one_min, "1h", confluence_params=params, backtest_config=cfg
    )
    assert result.trades, "이 시드는 체결 거래를 내야 한다"
    for trade in result.trades:
        entry_notional = trade.entry_price * trade.quantity
        # 진입 수수료는 메이커 요율(2bp)로 계산된다.
        assert trade.entry_fee == pytest.approx(entry_notional * 0.0002, rel=1e-9)
        for fill in trade.exits:
            # 청산 수수료는 테이커 요율(4bp).
            assert fill.fee == pytest.approx(fill.price * fill.quantity * 0.0004, rel=1e-9)


def test_zone_limit_rejects_close_entry_params() -> None:
    """B안 진입점에 종가 진입 파라미터가 들어오면 거부한다(WAN-95)."""
    htf, one_min = _synthetic_pair()
    close_params = ConfluenceParams(entry_mode="close", rsi_mode="closed_bar")
    with pytest.raises(ValueError, match="지정가 진입"):
        run_zone_limit_backtest(htf, one_min, "1h", confluence_params=close_params)


def test_funding_cost_is_deducted_over_hold_window() -> None:
    """WAN-95: 보유 구간에 정산된 펀딩이 B안 거래 손익에서 실제로 차감된다.

    A안 엔진(`BacktestEngine._funding_cost`)과 같은 산식 — 진입 명목가 × rate × 방향
    부호를 `[진입시각, 청산시각)` 구간의 정산에 대해 합산한다. 롱은 rate>0이면 지불.
    """
    hour = 60 * 60_000
    cand = _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=9 * hour,
        exit_price=100.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
    )
    cfg = BacktestConfig(
        funding_enabled=True, risk_sizing=None, position_fraction=1.0, initial_capital=10_000.0
    )
    # 보유 구간 안에 8h 정산 1건(4h 시점), 밖에 1건(20h 시점).
    rates = [
        FundingRate(symbol="X", funding_time=4 * hour, rate=0.001),
        FundingRate(symbol="X", funding_time=20 * hour, rate=0.001),
    ]
    without = _to_trade(cand, cfg.initial_capital, cfg, None)
    with_rates = _to_trade(cand, cfg.initial_capital, cfg, rates)
    assert without is not None and with_rates is not None
    assert without.funding_cost == 0.0
    # 구간 안 정산 1건만 반영: 진입 명목가 × 0.001. (롱 + rate>0 → 지불(양수 비용))
    entry_notional = with_rates.entry_price * with_rates.quantity
    assert with_rates.funding_cost == pytest.approx(entry_notional * 0.001, rel=1e-9)
    # 펀딩만큼 실현손익이 줄어든다.
    assert with_rates.realized_pnl == pytest.approx(
        without.realized_pnl - with_rates.funding_cost, rel=1e-9
    )


def test_funding_enabled_without_rates_shows_zero_coverage() -> None:
    """`funding_enabled=True`인데 rates를 안 넘기면 커버리지 0%로 드러난다(WAN-63/95).

    비용을 조용히 0으로 채우고 "반영했다"고 하지 않는 것이 핵심이다.
    """
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    result = run_zone_limit_backtest(
        htf,
        one_min,
        "1h",
        confluence_params=params,
        backtest_config=BacktestConfig(funding_enabled=True),
    )
    assert all(t.funding_cost == 0.0 for t in result.trades)
    assert result.metrics.funding_coverage == pytest.approx(0.0)


def test_funding_disabled_ignores_rates() -> None:
    """`funding_enabled=False`면 펀딩 데이터를 넘겨도 반영하지 않는다(커버리지 None)."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    cfg = BacktestConfig(funding_enabled=False)
    rates = [FundingRate(symbol="X", funding_time=int(htf["open_time"].iloc[0]), rate=0.01)]
    result = run_zone_limit_backtest(
        htf, one_min, "1h", confluence_params=params, backtest_config=cfg, funding_rates=rates
    )
    assert all(t.funding_cost == 0.0 for t in result.trades)
    assert result.metrics.funding_coverage is None
