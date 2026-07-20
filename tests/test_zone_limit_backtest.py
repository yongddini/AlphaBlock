"""존-지정가 + 실시간 RSI(B안) 백테스트 파이프라인 테스트 (WAN-41).

`entry_mode=zone_limit` + `rsi_mode=realtime`이 오더블록 탐지에 배선되어 end-to-end로
동작하는지, 1분봉 서브스텝 재구성으로 진입/청산이 이뤄지는지, A안과 동일 비용 모델을
쓰는지 검증한다.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest.harness import LEGACY_BAND_BAR, pin_band_bar
from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    StopLossContext,
    TakeProfitContext,
    _Candidate,
    _IncrementalRsiSeeder,
    _IntrabarLiveLimit,
    _line_snapshots,
    _sequence_and_cost,
    _to_trade,
    build_result_from_trades,
    build_zone_limit_candidates,
    run_zone_limit_backtest,
    run_zone_limit_backtest_verbose,
    sequence_with_candidates,
)
from common.costs import Liquidity
from data.models import FundingRate
from strategy.confluence import ConfluenceStrategy, entry_candidate_signals
from strategy.indicators import emas, vwma
from strategy.models import (
    ConfluenceParams,
    DeviationFilterParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
)
from strategy.order_blocks import OrderBlockDetector
from strategy.realtime_band import RealtimeBand
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


# ---------------------------------------------------- 체결 가정 보수화 (WAN-96)


def _conservatism_params(**overrides: object) -> ConfluenceParams:
    """보수화 테스트 기준 파라미터.

    이 합성 시드는 숏 셋업만 내므로 숏을 켜고, 볼린저 필터는 작은 데이터셋에서 후보를
    모두 걸러낼 수 있어 꺼 둔다 — 검증 대상은 **체결 가정**뿐이다.
    """
    base = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    return base.model_copy(update=overrides)


def _fills(**overrides: object) -> tuple[int, list[float]]:
    """(체결 수, 거래 손익 목록)."""
    htf, one_min = _synthetic_pair()
    result, stats = run_zone_limit_backtest_verbose(
        htf, one_min, "1h", confluence_params=_conservatism_params(**overrides)
    )
    return stats.filled, [t.realized_pnl for t in result.trades]


def test_conservatism_defaults_reproduce_baseline_exactly() -> None:
    """기본값(관통 0 · 탈락 0)은 WAN-95 동작 그대로다 — 시드가 달라도 동일하다.

    완료기준의 '기본값은 바꾸지 않는다'를 지키는 테스트다: 탈락률 0이면 난수를 아예
    뽑지 않으므로 `fill_dropout_seed`는 결과에 영향이 없어야 한다.
    """
    baseline = _fills()
    assert baseline == _fills(fill_penetration_bps=0.0, fill_dropout_rate=0.0)
    assert baseline == _fills(fill_dropout_seed=12345)


def test_penetration_requirement_never_increases_fills() -> None:
    """관통 요구를 세게 걸수록 체결은 줄기만 한다(늘어날 수 없다)."""
    baseline, _ = _fills()
    assert baseline > 0  # 전제: 기본 가정에서는 체결이 있다
    loose, _ = _fills(fill_penetration_bps=5.0)
    strict, _ = _fills(fill_penetration_bps=200.0)
    assert strict <= loose <= baseline


def test_fill_dropout_removes_all_fills_at_rate_one() -> None:
    """탈락률 100%면 체결이 0이 되고, 그만큼 `dropped`에 잡힌다."""
    htf, one_min = _synthetic_pair()
    result, stats = run_zone_limit_backtest_verbose(
        htf, one_min, "1h", confluence_params=_conservatism_params(fill_dropout_rate=1.0)
    )
    assert stats.filled == 0
    assert result.metrics.num_trades == 0
    # 낙관 모델이 체결이라 본 건수가 전부 탈락으로 옮겨갔다.
    assert stats.dropped == _fills()[0]
    assert stats.eligible > 0  # 셋업 자체는 여전히 평가됐다


def test_fill_dropout_is_deterministic_per_seed() -> None:
    """같은 시드 → 같은 탈락 집합(재현 가능), 부분 탈락은 기본과 100% 사이에 놓인다."""
    half_a = _fills(fill_dropout_rate=0.5, fill_dropout_seed=7)
    half_b = _fills(fill_dropout_rate=0.5, fill_dropout_seed=7)
    assert half_a == half_b
    assert 0 <= half_a[0] <= _fills()[0]


def test_fill_dropout_seed_changes_which_setups_drop() -> None:
    """시드가 다르면 탈락 집합이 달라진다 — 결과 분포를 볼 수 있어야 하기 때문이다."""
    seeds = {_fills(fill_dropout_rate=0.5, fill_dropout_seed=s)[0] for s in range(8)}
    assert len(seeds) > 1


def test_setup_sink_records_every_eligible_setup() -> None:
    """진단 sink는 eligible 셋업을 빠짐없이 기록하고 통계와 정합적이다."""
    htf, one_min = _synthetic_pair()
    sink: list[SetupDiagnostic] = []
    _, stats = run_zone_limit_backtest_verbose(
        htf,
        one_min,
        "1h",
        confluence_params=_conservatism_params(fill_dropout_rate=0.5, fill_dropout_seed=3),
        setup_sink=sink,
    )
    assert len(sink) == stats.eligible
    assert sum(1 for s in sink if s.filled) == stats.filled
    assert sum(1 for s in sink if s.dropped) == stats.dropped
    # 탈락한 셋업은 결코 체결로 잡히지 않는다(둘은 배타적이다).
    assert not any(s.filled and s.dropped for s in sink)
    # 미체결 셋업도 사후 비교가 가능하도록 원본 조건을 남긴다(체결 편향 진단의 전제).
    unfilled = [s for s in sink if not s.filled]
    assert unfilled and all(s.tap_close > 0 and s.stop_price > 0 for s in unfilled)


def test_setup_sink_does_not_change_results() -> None:
    """sink는 순수 진단이다 — 넘기든 말든 거래 결과가 같아야 한다."""
    htf, one_min = _synthetic_pair()
    params = _conservatism_params()
    with_sink: list[SetupDiagnostic] = []
    a, _ = run_zone_limit_backtest_verbose(
        htf, one_min, "1h", confluence_params=params, setup_sink=with_sink
    )
    b, _ = run_zone_limit_backtest_verbose(htf, one_min, "1h", confluence_params=params)
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
    # ⚠️ `min_rr`(WAN-68 옵트인 게이트)는 봉내 라이브 밴드와 함께 쓸 수 없다 — 진입가가
    # 봉내에 정해지므로 탭 봉 시점에 손익비를 판정할 수 없다. WAN-132로 그 밴드가 기본값이
    # 됐으므로, 이 게이트를 재는 테스트는 옛 밴드(`tap`)를 명시 고정한다.
    htf, one_min = _synthetic_pair()
    base = pin_band_bar(ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime"))
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


def test_sequence_with_candidates_matches_sequence_and_cost() -> None:
    """짝 반환판(WAN-104)이 시퀀싱의 유일한 구현이므로 기존 결과와 완전히 같아야 한다.

    `_sequence_and_cost`가 이 함수에 위임하도록 바꿨다 — 거래 목록이 조금이라도 달라지면
    WAN-95/99 리포트 수치가 통째로 흔들린다.
    """
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams()
    cfg = BacktestConfig()
    candidates, _ = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=cfg)

    paired = sequence_with_candidates(candidates, cfg)
    trades = _sequence_and_cost(candidates, cfg)

    assert [t.model_dump() for _, t in paired] == [t.model_dump() for t in trades]
    # 짝지어진 셋업은 그 거래의 진입 시각을 그대로 들고 있어야 조인이 성립한다.
    assert all(cand.entry_time == trade.entry_time for cand, trade in paired)


def test_candidate_trigger_time_uniquely_joins_to_setup_diagnostic() -> None:
    """`trigger_time`이 후보 ↔ 진단의 유일 키다 (WAN-104 분해의 전제).

    `(zone_key, tap_index)`로는 이을 수 없다 — 병합 존은 새로 편입된 구성 존이 같은
    클러스터 안에서 다시 `tap_index=0`을 받을 수 있어(WAN-81 §5) 그 조합이 유일하지
    않고, dict 키로 쓰면 두 셋업이 조용히 하나로 합쳐진다.
    """
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams()
    cfg = BacktestConfig()
    sink: list[SetupDiagnostic] = []
    candidates, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=cfg, setup_sink=sink
    )

    trigger_times = [s.trigger_time for s in sink]
    assert len(set(trigger_times)) == len(trigger_times), "진단의 trigger_time이 유일해야 한다"
    # 체결된 후보는 전부 자기 진단 레코드로 이어져야 한다(하나도 떨어지면 손익이 샌다).
    assert {c.trigger_time for c in candidates} <= set(trigger_times)


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


def test_retap_mode_selects_first_tap_vs_retap_signal_view() -> None:
    """WAN-138: `retap_mode`가 **동작으로** 첫 탭 뷰(`once`)와 전체 탭 뷰(`every_tap`)를
    가른다 — 라벨이 아니라 실제 소비하는 시그널 집합이 갈린다.

    채택 기본값(`combine_obs=True`)에서 탐지한 `OrderBlockResult`를 `entry_candidate_signals`
    (A안·B안 공유 진입점)에 먹여, `once`는 병합 존당 **첫 탭만**(`signals`, 전부
    `tap_index==0`)을, `every_tap`은 재탭 포함 전체(`retap_signals`, 상위집합)를 쓰는지
    고정한다. WAN-91/95/100/112/123 부류의 "라벨만 바뀌고 동작은 그대로"인 조용한 실패를
    막는다.
    """
    htf = make_synthetic_ohlcv(timeframe="1h", bars=3000, seed=7)
    ob_result = OrderBlockDetector(OrderBlockParams(combine_obs=True)).run(htf)
    base = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")

    once = entry_candidate_signals(
        ob_result, base.model_copy(update={"retap_mode": "once"}), [], [], {}
    )
    every_tap = entry_candidate_signals(
        ob_result, base.model_copy(update={"retap_mode": "every_tap"}), [], [], {}
    )

    # `once`는 첫 탭 뷰 그 자체 — 모든 시그널이 tap_index==0(재탭 없음).
    assert once is ob_result.signals
    assert once  # 이 시드는 실제로 진입 후보를 낸다.
    assert all(s.tap_index == 0 for s in once)
    # `every_tap`은 재탭 포함 뷰(상위집합)이고, 이 시드엔 실제 재탭(tap_index>=1)이 있다.
    assert every_tap is ob_result.retap_signals
    assert len(every_tap) >= len(once)
    assert any(s.tap_index >= 1 for s in every_tap)


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


def _bollinger(band_bar: str) -> ConfluenceParams:
    return ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        short_enabled=True,
        deviation_filter=DeviationFilterParams(
            anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0, band_bar=band_bar
        ),
    )


def test_band_bar_prev_closed_is_wired_into_zone_limit_path() -> None:
    """WAN-115: 채택 경로(B안)가 `band_bar`를 **실제로 읽는다**.

    B안이 이 배선을 빠뜨리면 교정 모드를 켠 채 옛 밴드로 돌면서 "룩어헤드를 뺐다"고
    믿게 된다 — WAN-100이 첫 탭 면제에서 겪은 그 사고다. 진입가가 실제로 움직였는지로
    배선을 증명한다(밴드가 한 봉 미뤄지면 규칙 2 진입가가 달라진다).
    """
    htf, one_min = _synthetic_pair()
    tap, _ = build_zone_limit_candidates(
        htf_df=htf, df_1m=one_min, timeframe="1h", params=_bollinger("tap"), cfg=BacktestConfig()
    )
    prev, _ = build_zone_limit_candidates(
        htf_df=htf,
        df_1m=one_min,
        timeframe="1h",
        params=_bollinger("prev_closed"),
        cfg=BacktestConfig(),
    )
    tap_prices = [(c.entry_time, c.entry_price) for c in tap]
    prev_prices = [(c.entry_time, c.entry_price) for c in prev]
    assert tap_prices != prev_prices


def test_band_bar_default_runs_the_intrabar_live_band_not_tap() -> None:
    """WAN-132: 채택 기본값이 **봉내 라이브 밴드**로 돈다 — 라벨이 아니라 동작으로 고정.

    라벨만 보는 테스트(`band_bar == "intrabar_live"`)는 WAN-123의 `"none"` vs
    `"unconditional"` 부류의 조용한 실패를 못 잡는다: 필드는 새 값인데 실행 경로가 옛
    밴드를 타면 "바꿨다고 믿으면서 `tap`을 도는" 상태가 된다. 그래서 **진입가가 명시적
    `tap`과 다르고 명시적 `intrabar_live`와 같다**는 것으로 증명한다.
    """
    htf, one_min = _synthetic_pair()
    default = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True)
    assert default.deviation_filter is not None
    assert default.deviation_filter.band_bar == "intrabar_live"

    def setups(params: ConfluenceParams) -> list[SetupDiagnostic]:
        sink: list[SetupDiagnostic] = []
        run_zone_limit_backtest_verbose(
            htf, one_min, "1h", confluence_params=params, setup_sink=sink
        )
        return sink

    base = setups(default)
    live = setups(_bollinger("intrabar_live"))
    tap = setups(_bollinger("tap"))
    assert base  # 전제: 판정할 셋업이 있다

    # 봉내 모드는 주문 가격을 서브스텝마다 다시 내므로 탭 봉 시점의 상수가 **없다**
    # (`limit_price is None`). 정적 모드(`tap`)는 반대로 항상 값이 있다 — 이 차이는
    # 라벨이 아니라 실행 경로가 만든다.
    assert all(s.limit_price is None for s in base)
    assert [s.limit_price for s in base] == [s.limit_price for s in live]
    assert any(s.limit_price is not None for s in tap)


def test_legacy_band_bar_pin_restores_the_pre_wan132_engine() -> None:
    """WAN-132: `harness.pin_band_bar`가 옛 밴드(`tap`)를 **동작으로** 되돌린다.

    옛 수치를 결론에 박아 둔 리포트(wan84/88/96/99/104/110/111/114/117/126/133/134/137 …)가
    전부 이 헬퍼 하나에 기대므로, 헬퍼가 조용히 아무것도 안 하면 그 리포트들이 한꺼번에
    새 밴드로 돈다 — 그 실패를 여기서 막는다.
    """
    htf, one_min = _synthetic_pair()
    pinned = pin_band_bar(
        ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True)
    )
    assert pinned.deviation_filter is not None
    assert pinned.deviation_filter.band_bar == LEGACY_BAND_BAR == "tap"

    got = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=pinned)
    tap = run_zone_limit_backtest(htf, one_min, "1h", confluence_params=_bollinger("tap"))
    assert [t.entry_price for t in got.trades] == [t.entry_price for t in tap.trades]


def test_pin_band_bar_leaves_a_disabled_deviation_filter_alone() -> None:
    """볼린저를 끈 파라미터(`deviation_filter=None`)에는 고정할 밴드가 없다 — 그대로 통과."""
    off = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", deviation_filter=None)
    assert pin_band_bar(off) is off


def test_intrabar_live_limit_applies_band_then_offset_in_the_adopted_order() -> None:
    """WAN-119: 봉내 공급자도 WAN-99의 가격 확정 순서를 그대로 따른다.

    밴드 → `deviation_entry_price`(볼린저가 이김) → 오프셋. 순서가 바뀌면 오프셋이
    볼린저가 기각한 셋업을 되살리거나(WAN-99가 막은 것) 존 근단 위에 얹혀 버린다.
    """
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    params = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", zone_limit_offset_bps=2.0
    )
    # 밴드가 존 안(90~100)에 오도록 만든 결정적 창: 종가가 모두 95면 σ=0 → band=95.
    filt = DeviationFilterParams(anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0)
    provider = _IntrabarLiveLimit(
        band=RealtimeBand.seed_from_closed([95.0] * 19, filt),
        order_block=ob,
        is_long=True,
        params=params,
        stop_price=90.0,
        lines=[],
    )
    # 현재가도 95면 20표본이 모두 95라 σ=0 → 밴드 = SMA = 95(존 안 → 규칙 2 진입가).
    # 그 위에 롱 오프셋 2bp가 체결 쉬운 쪽(위)으로 얹힌다.
    assert provider.limit_price(95.0) == pytest.approx(95.0 * (1.0 + 2.0 / 10_000.0), rel=1e-12)


def test_intrabar_live_limit_is_absent_when_band_rejects_the_zone() -> None:
    """밴드가 존 전체보다 불리하면 그 순간엔 주문이 없다(WAN-75 규칙 3).

    오프셋이 이 기각을 되살리면 안 된다 — 정적 경로와 같은 계약이다(WAN-99).
    """
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    filt = DeviationFilterParams(anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0)
    provider = _IntrabarLiveLimit(
        band=RealtimeBand.seed_from_closed([50.0] * 19, filt),
        order_block=ob,
        is_long=True,
        params=ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime"),
        stop_price=90.0,
        lines=[],
    )
    # 밴드 = 50 → 존 하단(90)보다 아래 = 존 전체가 밴드에 못 미친다 → 진입 없음.
    assert provider.limit_price(50.0) is None


def test_intrabar_live_rejects_min_rr_gate_instead_of_ignoring_it() -> None:
    """WAN-119: 진입가가 봉내에 정해지므로 탭 봉의 손익비 게이트를 판정할 수 없다.

    조용히 건너뛰면 게이트를 켠 채 안 걸린 결과가 나온다(채택 기본값은 `min_rr=None`이라
    이 조합은 실제로 쓰이지 않는다 — 그래도 지어내지 않고 거부한다).
    """
    htf, one_min = _synthetic_pair()
    params = _bollinger("intrabar_live").model_copy(update={"min_rr": 1.5})
    with pytest.raises(ValueError, match="min_rr"):
        build_zone_limit_candidates(
            htf_df=htf, df_1m=one_min, timeframe="1h", params=params, cfg=BacktestConfig()
        )


def test_intrabar_live_diagnostic_has_no_limit_price_when_unfilled() -> None:
    """WAN-119: 미체결 셋업엔 단일 주문 가격이 없다 — 지어내지 않고 `None`이다.

    밴드가 봉내에 움직이므로 "그 셋업의 지정가"라는 상수가 존재하지 않는다. 정적 모드와
    달리 진단 레코드가 그 사실을 그대로 남겨야 사후 분석이 착각하지 않는다.
    """
    htf, one_min = _synthetic_pair()
    sink: list[SetupDiagnostic] = []
    build_zone_limit_candidates(
        htf_df=htf,
        df_1m=one_min,
        timeframe="1h",
        params=_bollinger("intrabar_live"),
        cfg=BacktestConfig(),
        setup_sink=sink,
    )
    assert sink, "전제: 진단 레코드가 쌓인다"
    for s in sink:
        if s.filled:
            assert s.limit_price is not None
        else:
            assert s.limit_price is None


def _live_provider(*, causal: bool, pending_price: float | None = None) -> _IntrabarLiveLimit:
    """봉내 공급자 하나 — 두 모드가 `causal`/`pending_price`만 다르게 갈린다."""
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    filt = DeviationFilterParams(anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0)
    return _IntrabarLiveLimit(
        band=RealtimeBand.seed_from_closed([95.0] * 19, filt),
        order_block=ob,
        is_long=True,
        params=ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime"),
        stop_price=90.0,
        lines=[],
        pending_price=pending_price,
        causal=causal,
    )


def _tp_provider(
    *,
    take_profit_override: object = None,
    stop_loss_override: object = None,
) -> _IntrabarLiveLimit:
    """고정 1.5R 익절이 켜진 봉내 공급자 — WAN-143 훅 배선을 재는 최소 셋업."""
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )
    filt = DeviationFilterParams(anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0)
    return _IntrabarLiveLimit(
        band=RealtimeBand.seed_from_closed([95.0] * 19, filt),
        order_block=ob,
        is_long=True,
        params=ConfluenceParams(
            entry_mode="zone_limit",
            rsi_mode="realtime",
            take_profit_mode="fixed_r",
            take_profit_r=1.5,
        ),
        stop_price=90.0,
        lines=[],
        trigger_time=1_700_000_000_000,
        take_profit_override=take_profit_override,  # type: ignore[arg-type]
        stop_loss_override=stop_loss_override,  # type: ignore[arg-type]
    )


def test_live_resolve_exits_without_overrides_is_the_adopted_rule() -> None:  # WAN-143 §0
    """훅을 안 주면 손절은 존 경계, 익절은 고정 1.5R 그대로 — 기존 수치 재현의 계약."""
    stop, tp = _tp_provider().resolve_exits(96.0) or (None, None)
    assert stop == 90.0
    assert tp == pytest.approx(96.0 + 1.5 * (96.0 - 90.0))


def test_live_take_profit_override_is_called_with_the_fill_price() -> None:  # WAN-143 §0
    """익절 훅이 **체결 순간의 진입가**로 불린다(탭 봉 가격이면 옛 배선이다)."""
    seen: list[TakeProfitContext] = []

    def override(ctx: TakeProfitContext) -> float | None:
        seen.append(ctx)
        return ctx.entry_price + 2.0

    resolved = _tp_provider(take_profit_override=override).resolve_exits(96.0)
    assert resolved == (90.0, 98.0)
    assert [(c.entry_price, c.stop_price, c.trigger_time) for c in seen] == [
        (96.0, 90.0, 1_700_000_000_000)
    ]


def test_live_stop_override_feeds_the_take_profit_context() -> None:  # WAN-143 §0
    """손절 훅이 먼저 돌고, 익절은 **그 손절**로 계산된다(순서 의존을 한 메서드에 가둔다)."""

    def stop_override(ctx: StopLossContext) -> float | None:
        assert ctx.default_stop == 90.0
        return ctx.entry_price - 2.0  # 1R을 6.0 → 2.0으로 좁힌다.

    resolved = _tp_provider(stop_loss_override=stop_override).resolve_exits(96.0)
    assert resolved is not None
    stop, tp = resolved
    assert stop == 94.0
    assert tp == pytest.approx(96.0 + 1.5 * 2.0)


def test_live_stop_override_none_means_no_entry() -> None:  # WAN-143 §0
    """유효 장벽을 못 만들면 `None` — 시뮬레이터가 이걸 보고 체결시키지 않는다."""

    def stop_override(ctx: StopLossContext) -> float | None:
        return None

    assert _tp_provider(stop_loss_override=stop_override).resolve_exits(96.0) is None


def test_intrabar_causal_band_lags_live_by_exactly_one_substep() -> None:
    """WAN-120: 인과 모드는 밴드 표본으로 **직전** 서브스텝 종가를 쓴다.

    계약을 가장 좁게 고정하는 방법: 같은 가격 시퀀스를 두 공급자에 흘리면 인과 모드의
    t번째 출력이 라이브 모드의 **t−1번째** 출력과 같아야 한다. 지연이 0이면(= 잔여 1분
    룩어헤드가 그대로 남으면) 두 열이 겹쳐 이 테스트가 깨지고, 2분이면 어긋난다 — 즉
    이 이슈가 재는 대상 자체가 배선됐는지를 고정한다.
    """
    prices = [95.0, 96.0, 94.0, 93.5, 97.0]
    live = _live_provider(causal=False)
    # 셋업 직전 1분봉 종가로 시딩 = 첫 분이 시작될 때 이미 알던 가격.
    causal = _live_provider(causal=True, pending_price=prices[0])

    live_out = [live.limit_price(p) for p in prices]
    causal_out = [causal.limit_price(p) for p in prices[1:]]

    assert causal_out == live_out[:-1]


def test_intrabar_causal_has_no_order_until_a_price_is_known() -> None:
    """지연선이 비어 있으면 주문이 없다 — 값을 지어내지 않는다.

    시딩(`pending_price`)은 호출부가 셋업 직전 1분봉으로 해 준다. 그것조차 없는 구간
    (데이터 첫 분)에서는 밴드 표본이 될 **과거 가격이 실제로 없으므로**, 현재가로 대신
    채우면 그게 곧 이 모드가 없애려는 그 룩어헤드다.
    """
    causal = _live_provider(causal=True)
    assert causal.limit_price(95.0) is None
    # 첫 호출이 지연선을 채웠으므로 두 번째 호출부터 값이 나온다.
    assert causal.limit_price(95.0) == pytest.approx(95.0 * (1.0 + 2.0 / 10_000.0), rel=1e-12)


def test_intrabar_causal_is_rejected_by_the_bar_level_band() -> None:
    """A안(봉 단위)은 이 모드를 표현할 수 없으므로 `tap`으로 접지 않고 거부한다.

    `intrabar_live`는 A안 진입 시점의 현재가가 곧 탭 봉 종가라 `tap`과 만나지만, 인과
    모드가 쓰는 값은 **직전 1분봉 종가**라 봉 단위 시리즈에 없다. 조용히 접으면 "인과
    라벨을 달고 룩어헤드 값을 돌리는" WAN-95 부류의 사고가 된다.
    """
    with pytest.raises(ValueError, match="intrabar_causal"):
        ConfluenceStrategy.deviation_band_at(5, 1, [100.0] * 10, [2.0] * 10, "intrabar_causal")


def test_intrabar_causal_rejects_min_rr_gate_like_live_does() -> None:
    """봉내 모드의 제약(WAN-119)은 인과 변형에도 그대로 걸린다 — 진입가가 봉내에 정해진다."""
    htf, one_min = _synthetic_pair()
    params = _bollinger("intrabar_causal").model_copy(update={"min_rr": 1.5})
    with pytest.raises(ValueError, match="min_rr"):
        build_zone_limit_candidates(
            htf_df=htf, df_1m=one_min, timeframe="1h", params=params, cfg=BacktestConfig()
        )


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


# ---------------------------------------------------- 지정가 오프셋 (WAN-99)


def _offset_setups(**overrides: object) -> list[SetupDiagnostic]:
    """볼린저 필터를 **켠 채**(WAN-81 기본값) eligible 셋업의 지정가 기록을 받는다.

    체결 여부와 무관하게 모든 eligible 셋업의 `limit_price`가 남으므로, 오프셋이 어느
    가격 위에 얹혔는지를 체결 운에 기대지 않고 직접 볼 수 있다.

    ⚠️ 밴드는 **정적 모드(`tap`)로 고정**한다 — WAN-132로 기본값이 봉내 라이브가 된 뒤로는
    주문 가격이 서브스텝마다 바뀌어 `limit_price`가 `None`으로 기록되므로(상수가 아니다)
    "어느 가격 위에 얹혔나"를 이 진단으로 볼 수 없다. 봉내 모드의 순서 검증은
    `test_intrabar_live_limit_applies_band_then_offset_in_the_adopted_order`가 따로 한다.
    """
    htf, one_min = _synthetic_pair()
    params = pin_band_bar(
        ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True)
    ).model_copy(update=overrides)
    sink: list[SetupDiagnostic] = []
    run_zone_limit_backtest_verbose(htf, one_min, "1h", confluence_params=params, setup_sink=sink)
    return sink


def test_zone_limit_offset_default_is_two_bps_and_moves_fills() -> None:
    """채택 기본값은 **2bp**이고, 그 2bp는 실제로 체결을 움직인다(WAN-112).

    기본값이 0bp이던 시절 이 테스트는 "기본 == 명시적 0.0"을 고정했다. 이제는 그 등식이
    **깨져 있어야** 한다 — 같으면 오프셋이 어딘가에서 증발했다는 뜻이고, 그건 결정을
    반영했다고 믿으면서 옛 엔진을 돌리는 조용한 실패다(WAN-112 이슈 본문의 `0.0002`를
    그대로 넣었을 때 나는 증상이 정확히 이것이다).
    """
    assert ConfluenceParams().zone_limit_offset_bps == 2.0
    assert _fills() != _fills(zone_limit_offset_bps=0.0)


def test_explicit_zero_offset_still_reproduces_the_pre_wan112_engine() -> None:
    """명시적 0bp = WAN-112 이전 엔진. 0bp로 고정한 과거 리포트가 재현되는 근거다."""
    zero = ConfluenceParams(zone_limit_offset_bps=0.0)
    for price in (0.5, 100.0, 31_337.75):
        # 항등이어야 한다 — 곱셈 오차조차 끼면 과거 리포트 재현이 비트 단위로 깨진다.
        assert zero.apply_zone_limit_offset(price, is_long=True) is price
        assert zero.apply_zone_limit_offset(price, is_long=False) is price


def test_zone_limit_offset_applies_on_top_of_deviation_filter_price() -> None:
    """오프셋은 **볼린저가 재산정한 진입가** 위에 얹힌다(WAN-95 "볼린저가 이긴다" 유지).

    이 합성 셋업들은 볼린저가 실제로 존 근단을 덮어쓰는(규칙 2) 케이스라, 오프셋이
    존 근단 위에 얹혔다면 비율이 어긋난다 — 적용 순서가 뒤집히면 실패한다.
    """
    offset_bps = 20.0
    # 기준선은 **명시적 0bp**여야 한다 — 채택 기본값(2bp)을 기준으로 잡으면 아래 기대식이
    # 2bp만큼 이미 밀린 가격에 20bp를 또 얹는 꼴이 된다(WAN-112).
    base = _offset_setups(zone_limit_offset_bps=0.0)
    shifted = _offset_setups(zone_limit_offset_bps=offset_bps)
    unfiltered = {
        s.trigger_time: s.limit_price
        for s in _offset_setups(zone_limit_offset_bps=0.0, deviation_filter=None)
    }
    assert base  # 전제: 판정할 셋업이 있다

    for before, after in zip(base, shifted, strict=True):
        assert before.trigger_time == after.trigger_time
        # 전제: 이 셋업은 볼린저가 존 근단을 실제로 덮어쓴 케이스다. 그렇지 않으면
        # "볼린저 가격 위에 얹혔다"와 "존 근단 위에 얹혔다"를 구분할 수 없다.
        assert before.limit_price != unfiltered[before.trigger_time]
        # 정적 밴드 모드라 주문 가격은 탭 봉에서 상수로 정해진다(None은 live 모드 전용).
        assert before.limit_price is not None
        sign = 1.0 if before.side is PositionSide.LONG else -1.0
        expected = before.limit_price * (1.0 + sign * offset_bps / 10_000.0)
        assert after.limit_price == pytest.approx(expected, rel=1e-12)


def test_zone_limit_offset_does_not_revive_setups_rejected_by_deviation_filter() -> None:
    """볼린저가 '진입 없음'으로 기각한 셋업을 오프셋이 되살리지 않는다(WAN-75 규칙 3)."""
    base = _offset_setups()
    shifted = _offset_setups(zone_limit_offset_bps=50.0)
    unfiltered = _offset_setups(deviation_filter=None)
    # 전제: 볼린저가 실제로 일부 셋업을 기각하고 있다(아니면 이 테스트는 공허하다).
    assert len(base) < len(unfiltered)
    assert [s.trigger_time for s in shifted] == [s.trigger_time for s in base]


def test_zone_limit_offset_sign_convention_is_symmetric() -> None:
    """양수 = 체결이 쉬워지는 방향(롱은 위·숏은 아래), 음수는 그 반대. 0은 항등."""
    params = ConfluenceParams(zone_limit_offset_bps=10.0)
    assert params.apply_zone_limit_offset(100.0, is_long=True) == pytest.approx(100.1)
    assert params.apply_zone_limit_offset(100.0, is_long=False) == pytest.approx(99.9)

    negative = ConfluenceParams(zone_limit_offset_bps=-10.0)
    assert negative.apply_zone_limit_offset(100.0, is_long=True) == pytest.approx(99.9)
    assert negative.apply_zone_limit_offset(100.0, is_long=False) == pytest.approx(100.1)


def _staged_long_setup(offset_bps: float) -> list[_Candidate]:
    """존 근단 100 / 무효화 90인 롱 오더블록 하나를 심고 후보를 만든다.

    합성 시드는 익절까지 가는 셋업을 내주지 않아(전부 손절) 오프셋이 익절 목표를
    옮기는지 볼 수 없다. 그래서 오더블록과 1분봉 경로를 직접 심어 **가격을 아는
    상태에서** 1R·익절 목표를 검산한다: 존에 닿은 뒤 손절선(90)을 건드리지 않고
    익절까지 상승하는 경로다.
    """
    htf_ms = timeframe_to_ms("1h")
    bars = 40
    htf = pd.DataFrame(
        {
            "open_time": [i * htf_ms for i in range(bars)],
            "open": [105.0 for _ in range(bars)],
            # RSI가 워밍업을 벗어나도록 종가를 흔든다(서브스텝은 RSI가 NaN이면 체결하지 않는다).
            "close": [105.0 + (1.0 if i % 2 else -1.0) for i in range(bars)],
            "high": [107.0 for _ in range(bars)],
            "low": [103.0 for _ in range(bars)],
            "volume": [1_000.0 for _ in range(bars)],
        }
    )
    tap_time = int(htf["open_time"].iloc[-1])
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=htf_ms,
        ob_volume=1_000.0,
        ob_low_volume=400.0,
        ob_high_volume=600.0,
    )
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=tap_time,
        price=100.0,
        order_block=ob,
    )
    # 탭 봉 안에서 99.5까지 내려가 지정가(100 또는 오프셋 적용가)를 채운 뒤, 손절선 90은
    # 건드리지 않고 120까지 상승한다.
    minute = 60_000
    lows = [99.5] + [100.0 + i * 0.35 for i in range(60)]
    one_min = pd.DataFrame(
        {
            "open_time": [tap_time + i * minute for i in range(len(lows))],
            "open": [105.0] + [lo for lo in lows[:-1]],
            "high": [105.0] + [lo + 1.0 for lo in lows[1:]],
            "low": lows,
            "close": [100.0] + [lo + 0.5 for lo in lows[1:]],
            "volume": [10.0 for _ in lows],
        }
    )
    params = ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        deviation_filter=None,
        take_profit_mode="fixed_r",
        take_profit_r=1.5,
        rsi_gate_mode="none",  # 검증 대상은 진입가·1R·익절이지 RSI 게이트가 아니다.
        zone_limit_offset_bps=offset_bps,
    )
    candidates, _ = build_zone_limit_candidates(
        htf,
        one_min,
        "1h",
        params=params,
        cfg=BacktestConfig(),
        order_block_result=OrderBlockResult(
            order_blocks=[ob], signals=[signal], retap_signals=[signal]
        ),
    )
    return candidates


def test_zone_limit_offset_widens_risk_and_pushes_take_profit_target() -> None:
    """오프셋이 반영된 진입가로 1R과 고정 R 익절 목표가 **재계산**된다(WAN-99 핵심 대가).

    존 근단 100 · 무효화 90 · 1:1.5R에서 오프셋 100bp(1%)를 걸면 진입가는 101로
    올라가고 1R은 10 → 11로 늘어, 익절 목표는 115 → 117.5로 **멀어진다**. 익절이
    오프셋 이전 진입가로 계산되면(= 115에서 익절) 이 테스트가 잡는다 — 그 버그는
    오프셋의 대가를 지우고 결과를 통째로 무의미하게 만든다.
    """
    (base,) = _staged_long_setup(0.0)
    assert base.reason is ExitReason.TAKE_PROFIT
    assert base.entry_price == pytest.approx(100.0)
    assert base.stop_price == pytest.approx(90.0)
    assert base.exit_price == pytest.approx(115.0)  # 100 + 1.5 × (100 − 90)

    (shifted,) = _staged_long_setup(100.0)
    assert shifted.reason is ExitReason.TAKE_PROFIT
    assert shifted.entry_price == pytest.approx(101.0)  # 100 × (1 + 100bp)
    assert shifted.stop_price == pytest.approx(90.0)  # 손절 참조가는 존 원단 그대로다
    assert shifted.exit_price == pytest.approx(117.5)  # 101 + 1.5 × (101 − 90)

    # 대가의 본체: 같은 존인데 1R이 커졌다 → 익절이 멀어졌다.
    base_risk = base.entry_price - base.stop_price
    shifted_risk = shifted.entry_price - shifted.stop_price
    assert shifted_risk > base_risk
    for cand in (base, shifted):
        risk = cand.entry_price - cand.stop_price
        assert cand.exit_price - cand.entry_price == pytest.approx(1.5 * risk)


def test_zone_limit_offset_makes_fills_easier_for_longs() -> None:
    """오프셋을 키우면 체결이 쉬워진다(줄지 않는다) — 오프셋의 존재 이유.

    가격이 오는 방향으로 마중 나가므로, 같은 경로에서 체결이 늘거나 같아야 한다.
    """
    counts = [
        sum(1 for s in _offset_setups(zone_limit_offset_bps=bps) if s.filled)
        for bps in (0.0, 20.0, 100.0)
    ]
    assert counts == sorted(counts)


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
