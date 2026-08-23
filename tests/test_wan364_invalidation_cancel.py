"""WAN-364: 「존이 깨진 봉」 소급 취소의 인과 팔 — 라벨이 아니라 **동작**으로 고정한다.

고정하는 것 다섯:

1. **시뮬레이터 층** — 같은 셋업이 `bar_open`에서는 취소되고 `bar_close`에서는 체결 후
   손절로 끝난다(취소 시점이 실제로 움직인다).
2. **시그널 층** — 인과 팔은 **무효화 봉에서 난 탭**도 후보로 받는다. 한쪽만 바꾸면
   「탭은 여전히 안 보는데 취소만 늦춘」 잡종이 되는데, 그 잡종은 결과가 기준선과 같아
   조용히 통과한다.
3. **되살아난 거래는 무효화 봉 *안*에만 있다** — 봉이 닫힌 뒤 체결이 하나라도 있으면
   인과 팔이 「취소 끔」으로 새어 나간 것이다(검산 (c)).
4. **재진입 경로도 같은 규칙을 받는다** — WAN-345가 래더 축에서 겪은 실패(시그니처만 넓히고
   배선을 빠뜨림)를 이 축에서 되풀이하지 않는다. 인자 전달 여부가 아니라 **재진입 후보가
   실제로 생기는지**로 건다.
5. **기본값은 비트 재현** — 인자를 안 주면 예전과 같은 후보가 나온다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import (
    SubStep,
    ZoneLimitStatus,
    simulate_zone_limit_trade,
)
from backtest.wan169_leverage_book import run_cells
from backtest.wan228_reentry_census import reentry_candidates
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan364_invalidation_cancel import (
    ADOPTED_ARM,
    ARM_ORDER,
    ARMS_BY_NAME,
    CAUSAL_ARM,
    CancelRow,
    build_summary,
    census_cell,
    revived_rulers,
    rows_to_frame,
    run_arm,
)
from backtest.zone_limit_backtest import (
    ADOPTED_INVALIDATION_CANCEL,
    _Candidate,
    build_zone_limit_candidates,
    invalidation_cutoff,
)
from execution.sizing import PositionSizingParams
from strategy.models import OrderBlock, OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

_HTF_MS = 3_600_000  # 1h
_BREAK_BAR = _HTF_MS  # 두 번째 봉에서 존이 깨진다.


# --------------------------------------------------------------------------- #
# 0. 순수 함수
# --------------------------------------------------------------------------- #


def test_cutoff_moves_only_in_causal_mode() -> None:
    # WAN-365: 기본값이 인과(`bar_close`)로 옮겨졌다 — 인자를 안 주면 봉 마감이 나온다.
    assert invalidation_cutoff(_BREAK_BAR, htf_ms=_HTF_MS) == _BREAK_BAR + _HTF_MS
    assert invalidation_cutoff(_BREAK_BAR, htf_ms=_HTF_MS, mode="bar_open") == _BREAK_BAR
    assert invalidation_cutoff(_BREAK_BAR, htf_ms=_HTF_MS, mode="bar_close") == _BREAK_BAR + _HTF_MS
    assert invalidation_cutoff(None, htf_ms=_HTF_MS, mode="bar_close") is None
    assert ADOPTED_INVALIDATION_CANCEL == "bar_close", "채택 기본값이 움직이면 재-베이스라인이다"
    assert harness.LEGACY_INVALIDATION_CANCEL == "bar_open", "옛 동작 핀이 움직이면 옛 CSV가 깨진다"


# --------------------------------------------------------------------------- #
# 1. 시뮬레이터 층 — 취소가 실제로 미뤄진다
# --------------------------------------------------------------------------- #


def _break_bar_substeps() -> list[SubStep]:
    """무효화 봉 안에서 「지정가 통과 → 존 아랫변 돌파」가 순서대로 일어나는 1분 경로.

    존 = [99, 100] · 지정가 100 · 손절 99. 무효화 봉(두 번째 봉)의 3분에 100을 찍고
    7분에 98.5까지 내려간다 — 실거래라면 산 뒤 손절이고, 소급 취소면 없던 일이 된다.
    """
    steps: list[SubStep] = []
    for minute in range(60):  # 첫 봉 — 존 위에서만 논다.
        t = minute * 60_000
        steps.append(SubStep(time=t, high=105.0, low=101.0, close=102.0, htf_bar_time=0))
    path = {3: (100.5, 100.0), 7: (99.5, 98.5)}
    for minute in range(60):  # 무효화 봉.
        t = _BREAK_BAR + minute * 60_000
        high, low = path.get(minute, (101.0, 100.6))
        steps.append(SubStep(time=t, high=high, low=low, close=low, htf_bar_time=_BREAK_BAR))
    return steps


def _simulate(invalidation_time: int | None) -> Any:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=99.0,
        substeps=_break_bar_substeps(),
        start=0,
        rsi_state=RealtimeRsi.seed_from_closed([], length=14),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=101.5,
        limit_valid_bars=24,
        invalidation_time=invalidation_time,
        rsi_gate_mode="unconditional",
    )


def test_bar_open_cancels_what_bar_close_lets_stop_out() -> None:
    """같은 경로가 팔에 따라 「없던 거래」와 「−1R 손절」로 갈린다."""
    adopted = _simulate(invalidation_cutoff(_BREAK_BAR, htf_ms=_HTF_MS, mode="bar_open"))
    assert adopted.status is ZoneLimitStatus.CANCELLED_INVALIDATED
    assert not adopted.filled

    causal = _simulate(invalidation_cutoff(_BREAK_BAR, htf_ms=_HTF_MS, mode="bar_close"))
    assert causal.filled
    assert causal.entry_time == _BREAK_BAR + 3 * 60_000
    assert causal.exit_reason is not None
    assert causal.exit_price == pytest.approx(99.0), "존 아랫변에서 손절로 끝나야 한다"


def test_bar_close_still_cancels_after_the_bar_has_closed() -> None:
    """인과 팔은 「취소 끔」이 아니다 — 봉이 닫힌 뒤의 탭은 여전히 취소된다."""
    steps = [
        SubStep(
            time=_BREAK_BAR + _HTF_MS + m * 60_000,
            high=101.0,
            low=99.5,
            close=100.0,
            htf_bar_time=_BREAK_BAR + _HTF_MS,
        )
        for m in range(60)
    ]
    outcome = simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=99.0,
        substeps=steps,
        start=0,
        rsi_state=RealtimeRsi.seed_from_closed([], length=14),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=101.5,
        invalidation_time=invalidation_cutoff(_BREAK_BAR, htf_ms=_HTF_MS, mode="bar_close"),
        rsi_gate_mode="unconditional",
    )
    assert outcome.status is ZoneLimitStatus.CANCELLED_INVALIDATED


# --------------------------------------------------------------------------- #
# 2. 재진입 경로 — 시그니처만 넓히는 실패(WAN-345)를 막는다
# --------------------------------------------------------------------------- #


def _reentry_setup() -> tuple[_Candidate, list[SubStep], list[int]]:
    """익절로 닫힌 존 하나 + 무효화 봉 안에서 다시 지정가에 닿는 경로."""
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=99.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        break_time=_BREAK_BAR,
    )
    cand = _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=60_000,
        exit_price=101.5,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.0,
        order_block=ob,
        trigger_time=0,
    )
    return cand, _break_bar_substeps(), [0, _BREAK_BAR, _BREAK_BAR + _HTF_MS]


def _reentries(**kwargs: Any) -> list[_Candidate]:
    cand, steps, htf_times = _reentry_setup()
    params = harness.build_params().model_copy(update={"deviation_filter": None})
    return reentry_candidates(
        cand,
        parent_exit_time=60_000,
        substeps=steps,
        substep_times=[s.time for s in steps],
        htf_times=htf_times,
        htf_closes=[102.0, 100.0, 100.0],
        params=params,
        cfg=BacktestConfig(
            initial_capital=10_000.0,
            risk_sizing=PositionSizingParams(
                sizing_mode="risk_pct",
                risk_per_trade=0.01,
                leverage=1.0,
                min_stop_distance_fraction=0.0,
            ),
        ),
        funding_rates=None,
        entry_rule="freeze",
        **kwargs,
    )


def test_reentry_path_actually_receives_the_cancel_mode() -> None:
    """재진입도 base와 **같은 취소 시점**을 받는다 — 한쪽만 걸면 잡종 엔진이다."""
    legacy = _reentries(invalidation_cancel="bar_open")
    causal = _reentries(invalidation_cancel="bar_close", htf_ms=_HTF_MS)
    assert legacy == [], "소급 취소 팔은 무효화 봉의 재무장 체결을 만들지 않는다"
    assert causal, "인과 팔이 재진입 후보를 하나도 못 냈다 — 배선이 빠졌다(WAN-345 부류)"
    assert all(c.entry_after_invalidation for c in causal)
    assert all(_BREAK_BAR <= c.entry_time < _BREAK_BAR + _HTF_MS for c in causal)


def test_reentry_causal_arm_refuses_to_fold_silently_without_the_bar_length() -> None:
    """봉 길이를 모르면 조용히 소급 취소로 접히지 않고 시끄럽게 죽는다."""
    with pytest.raises(ValueError, match="htf_ms"):
        _reentries(invalidation_cancel="bar_close")
    # WAN-365: 인자를 아예 안 줘도 채택 기본값이 인과라 같은 자리에서 죽는다.
    with pytest.raises(ValueError, match="htf_ms"):
        _reentries()


# --------------------------------------------------------------------------- #
# 3. 관측 필드
# --------------------------------------------------------------------------- #


def test_entry_after_invalidation_defaults_to_false() -> None:
    """순수 관측 필드라 기본이 거짓이고, 그래서 옛 CSV가 비트 재현된다."""
    assert (
        _Candidate(
            side=PositionSide.LONG,
            entry_time=0,
            entry_price=1.0,
            exit_time=1,
            exit_price=1.0,
            reason=ExitReason.TAKE_PROFIT,
            stop_price=0.9,
        ).entry_after_invalidation
        is False
    )


def test_revived_rulers_split_stops_from_take_profits() -> None:
    """되살아난 거래가 「전부 −1R」이라는 예상은 표가 검정할 대상이지 전제가 아니다."""
    from backtest.leverage_book import PlacedSetup
    from backtest.models import Trade, TradeFill

    def _pair(pnl: float, reason: ExitReason, revived: bool) -> tuple[Trade, PlacedSetup]:
        trade = Trade(
            side=PositionSide.LONG,
            entry_time=0,
            entry_price=100.0,
            quantity=1.0,
            entry_fee=0.0,
            exits=[TradeFill(time=1, price=101.0, quantity=1.0, fee=0.0, reason=reason)],
            realized_pnl=pnl,
            return_pct=pnl / 10_000.0,
        )
        placement = PlacedSetup(
            cell=("BTCUSDT", "1h"),
            equity=10_000.0,
            risk_amount=100.0,
            realized_pnl=pnl,
            entry_after_invalidation=revived,
        )
        return trade, placement

    rulers = revived_rulers(
        [
            _pair(-100.0, ExitReason.STOP_LOSS, True),
            _pair(150.0, ExitReason.TAKE_PROFIT, True),
            _pair(150.0, ExitReason.TAKE_PROFIT, False),
        ]
    )
    assert rulers["revived_trades"] == 2
    assert rulers["revived_stop_losses"] == 1
    assert rulers["revived_take_profits"] == 1
    assert rulers["revived_win_rate"] == pytest.approx(0.5)
    assert rulers["revived_net_r"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 4. 배선 — 팔 이름이 실제 인자로 이어지는가
# --------------------------------------------------------------------------- #


def test_arms_carry_the_cancel_mode_and_drop_the_engine_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """라벨만 붙고 기본 엔진이 도는 것이 이 저장소의 상습 사고다(WAN-91/95/112/123/159)."""
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan364_invalidation_cancel.run_cells", _fake)
    monkeypatch.setattr("backtest.wan336_same_step_tp.apply_funding_proxy", lambda p: (list(p), ""))
    expected = {ADOPTED_ARM: ("bar_open", True), CAUSAL_ARM: ("bar_close", False)}
    for name, (mode, check) in expected.items():
        captured.clear()
        run_arm(
            ["BTCUSDT"],
            ["1h"],
            ARMS_BY_NAME[name],
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            jobs=1,
            log=False,
        )
        assert captured, name
        kwargs = captured[0]
        assert kwargs["invalidation_cancel"] == mode, name
        assert kwargs["engine_check"] is check, name
        for key in ADOPTED_CELL_KWARGS:
            assert kwargs[key] == ADOPTED_CELL_KWARGS[key], f"{key}가 채택 좌표와 다르다"


def test_arm_names_are_stable() -> None:
    assert ARM_ORDER == (ADOPTED_ARM, CAUSAL_ARM)


# --------------------------------------------------------------------------- #
# 5. 요약
# --------------------------------------------------------------------------- #


def _row(arm: str, *, trades: int, mean_net_r: float, revived: int) -> CancelRow:
    return CancelRow(
        arm=arm,
        arm_label=ARMS_BY_NAME[arm].label,
        invalidation_cancel=ARMS_BY_NAME[arm].cancel,
        segment="oos_warm",
        num_cells=48,
        num_trades=trades,
        win_rate=0.55,
        total_return=1.5,
        mean_net_r=mean_net_r,
        net_r=mean_net_r * trades,
        profit_factor=1.4,
        max_drawdown=0.2,
        return_over_mdd=7.5,
        ruin=False,
        peak_concurrency=14,
        max_concurrent_risk=0.11,
        max_effective_concurrent_risk=0.17,
        liquidation_events=0,
        revived_trades=revived,
        revived_share=revived / trades,
        revived_win_rate=0.25,
        revived_mean_net_r=-0.5,
        revived_net_r=-0.5 * revived,
        revived_stop_losses=int(revived * 0.75),
        revived_take_profits=revived - int(revived * 0.75),
        revived_other_exits=0,
        reentry_trades=100,
    )


def test_summary_states_the_direction_and_size_of_the_lookahead() -> None:
    frame = rows_to_frame(
        [
            _row(ADOPTED_ARM, trades=6_336, mean_net_r=0.1985, revived=0),
            _row(CAUSAL_ARM, trades=7_500, mean_net_r=0.1200, revived=1_164),
        ]
    )
    text = build_summary(frame, pd.DataFrame(), pd.DataFrame())
    assert "부풀렸다" in text
    assert "되살아난 거래 1164건" in text or "되살아난 거래 1,164건" in text.replace(",", ",")
    assert "재-베이스라인 = 사용자 결정" in text
    assert "엣지 없음" in text


def test_summary_says_it_cannot_judge_with_one_arm() -> None:
    frame = rows_to_frame([_row(ADOPTED_ARM, trades=10, mean_net_r=0.1, revived=0)])
    assert "판정 불가" in build_summary(frame, pd.DataFrame(), pd.DataFrame())


# --------------------------------------------------------------------------- #
# 6. 실데이터 — 두 층이 실제로 함께 움직인다
# --------------------------------------------------------------------------- #

_REAL_SYMBOL, _REAL_TF = "BTC/USDT:USDT", "4h"
_REAL_START, _REAL_END = "2024-01-01", "2026-07-22"


def _real_market() -> Any:
    from backtest.run import parse_date_ms

    return harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        need_1m=True,
    )


def test_causal_arm_revives_trades_on_real_data() -> None:
    """합성 경로가 아니라 **채택 엔진**에서 두 층이 함께 움직이는지 — 그리고 채택 팔에는
    되살아난 거래가 정의상 0건인지(검산 (b)·(c))."""
    market = _real_market()
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")

    from data.models import timeframe_to_ms

    ob_result = harness.detect_order_blocks(market)
    # WAN-365: 파라미터 기본값이 인과라, 이 테스트의 「옛 팔」은 명시 핀으로 만든다.
    params = harness.pin_invalidation_cancel(harness.build_params())
    cfg = harness.build_config(_REAL_TF)
    htf_ms = timeframe_to_ms(_REAL_TF)

    def _build(**kwargs: Any) -> list[_Candidate]:
        cands, _stats = build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            _REAL_TF,
            params=params,
            cfg=cfg,
            order_block_result=ob_result,
            **kwargs,
        )
        return cands

    legacy = _build()
    assert legacy, "실데이터가 있는데 후보가 비었다"
    assert _build(invalidation_cancel="bar_open") == legacy, "명시 핀이 비트 재현이 아니다"
    assert all(not c.entry_after_invalidation for c in legacy)

    causal = _build(invalidation_cancel="bar_close")
    # WAN-365: 채택 기본값 파라미터는 인자 없이도 인과 팔과 같은 후보를 낸다.
    adopted_default, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _REAL_TF,
        params=harness.build_params(),
        cfg=cfg,
        order_block_result=ob_result,
    )
    assert adopted_default == causal, "인자 없는 채택 파라미터가 인과로 돌지 않는다"
    revived = [c for c in causal if c.entry_after_invalidation]
    assert len(causal) > len(legacy), "인과 팔이 실데이터에서 아무것도 되살리지 못했다"
    assert revived, "되살아난 거래 라벨이 하나도 안 붙었다"
    # 검산 (c) — 되살아난 체결은 **무효화 봉 안**에만 있다(취소가 꺼진 게 아니다).
    for cand in revived:
        assert cand.order_block is not None and cand.order_block.break_time is not None
        assert cand.order_block.break_time <= cand.entry_time < cand.order_block.break_time + htf_ms


def test_census_counts_break_bar_taps_on_real_data() -> None:
    """§2 인구조사가 실제 탐지 결과에서 무효화 봉 탭을 센다."""
    from backtest.run import parse_date_ms

    probe = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        need_1m=False,
        funding=False,
    )
    if probe.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")
    row = census_cell(
        _REAL_SYMBOL, _REAL_TF, start_ms=parse_date_ms(_REAL_START), end_ms=parse_date_ms(_REAL_END)
    )
    assert row is not None
    assert row.break_bar_taps > 0
    assert 0.0 < row.break_bar_share < 1.0


def test_run_cells_threads_the_mode_to_both_candidate_layers() -> None:
    """`run_cells` 한 인자가 base 후보와 재진입 후보 **양쪽**에 닿는지 — 실데이터."""
    market = _real_market()
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")

    shared: dict[str, Any] = {
        "start": _REAL_START,
        "end": _REAL_END,
        "jobs": 1,
        "cold_segments": False,
        "engine_check": False,
        **ADOPTED_CELL_KWARGS,
    }
    legacy = run_cells([_REAL_SYMBOL], [_REAL_TF], invalidation_cancel="bar_open", **shared)
    causal = run_cells([_REAL_SYMBOL], [_REAL_TF], **shared)  # WAN-365: 미지정 = 인과

    def _revived(payloads: list[Any], key: str) -> int:
        return sum(
            1 for p in payloads for c in getattr(p, key)["full"] if c.entry_after_invalidation
        )

    assert _revived(legacy, "candidates") == 0
    assert _revived(legacy, "reentry_candidates") == 0
    assert _revived(causal, "candidates") > 0, "base 후보 층에 인과 팔이 안 걸렸다"
