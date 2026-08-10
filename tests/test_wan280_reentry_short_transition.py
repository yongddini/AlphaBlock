"""WAN-280 「N회 지지 후 숏 전환」의 자(尺)를 동작으로 고정한다 (완료기준 3).

백테스트 전체는 안 돌린다 — 숏 스펙 기하·재무장 사슬·N 도달 정의(switch)·프레임 왕복을
합성 값으로 검증한다. 고정하는 함정들:

1. **숏 기하** — 진입=윗경계(`ob.top`)·1R=존 너비·손절=진입+1R·익절=진입−1.5R.
2. **숏 재무장 사슬** — 위이탈 손절이면 다시 무장, 붕괴 익절·미체결이면 존 종료.
3. **진짜 재-탭 게이트** — 손절 후 가격이 상단으로 되돌아오지 않으면 재무장 안 함(즉시
   재체결 인공물 방지).
4. **N 도달 정의(switch)** — N=0은 부모 익절 직후, N≥1은 N번째 롱 재진입 익절 직후. N 도달
   전 손절·미체결이면 전환 안 함(= 롱-온리).
5. **팔 합성·CSV 왕복** — 전환 후보 = depth≤N 롱 + 숏 사슬 · 파생 속성이 값을 보존한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan280_reentry_short_transition import (
    BookScopeRow,
    ShortDiagRow,
    _net_r,
    _short_chain,
    _switch_time,
    _ZoneArms,
    book_from_csv,
    book_to_frame,
    diag_from_csv,
    diag_to_frame,
    short_zone_geometry,
    zone_arms,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

HTF_MS = 3_600_000  # 1h


def _substeps(bars: list[tuple[int, float, float, float]]) -> list[SubStep]:
    out: list[SubStep] = []
    for minute, high, low, close in bars:
        t = minute * 60_000
        out.append(
            SubStep(time=t, high=high, low=low, close=close, htf_bar_time=(t // HTF_MS) * HTF_MS)
        )
    return out


def _ob(*, break_time: int | None) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=105.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=1.5,
        break_time=break_time,
    )


def _long_candidate(*, break_time: int | None) -> _Candidate:
    """롱 셋업: 지정가 100 · 손절 90(1R=10) · 익절 115(1.5R). 존 top=105 bottom=90."""
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=0,
        exit_price=115.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
        order_block=_ob(break_time=break_time),
    )


def _params() -> ConfluenceParams:
    return ConfluenceParams()  # 채택 게이트(unconditional)라 RSI 값은 안 본다.


def _cfg() -> BacktestConfig:
    return harness.build_config("1h")


def _short_args(substeps: list[SubStep]) -> dict[str, Any]:
    return dict(
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=_params(),
        cfg=_cfg(),
        funding_rates=None,
    )


# --------------------------------------------------------------------------- #
# 1. 숏 기하
# --------------------------------------------------------------------------- #


def test_short_geometry_matches_spec() -> None:
    entry, stop, tp = short_zone_geometry(_ob(break_time=None), _params())
    assert entry == 105.0  # 윗경계
    assert stop == 120.0  # 진입 +1R(존 너비 15)
    assert tp == 82.5  # 진입 −1.5R = 105 − 1.5*15


# --------------------------------------------------------------------------- #
# 2·3. 숏 재무장 사슬 + 재-탭 게이트
# --------------------------------------------------------------------------- #


def test_short_chain_stop_then_collapse() -> None:
    """위이탈 손절(존 버팀) → 재-탭 → 붕괴 익절. 정확히 2건, 붕괴에서 종료."""
    bars = [
        (10, 106.0, 104.0, 105.5),  # 재-탭(low<=105) + 숏 체결 105
        (20, 121.0, 110.0, 118.0),  # 위이탈 손절 120 (존이 위로 튕김)
        (30, 106.0, 100.0, 103.0),  # 가격 상단 복귀 → 재무장·재체결 105
        (40, 90.0, 80.0, 83.0),  # 붕괴 → 익절 82.5
        (50, 84.0, 80.0, 82.0),  # 종료 후 봉(무시)
    ]
    substeps = _substeps(bars)
    cands, events = _short_chain(
        _long_candidate(break_time=None), switch_time=0, **_short_args(substeps)
    )
    assert len(events) == 2
    assert events[0].is_stop and not events[0].is_win
    assert events[1].is_win and not events[1].is_stop
    assert events[0].gross_r == -1.0
    assert events[1].gross_r == 1.5
    assert [e.depth for e in events] == [1, 2]
    assert [c.side for c in cands] == [PositionSide.SHORT, PositionSide.SHORT]
    assert cands[0].entry_price == 105.0 and cands[0].stop_price == 120.0


def test_short_chain_retap_gate_no_instant_refill() -> None:
    """손절 후 가격이 상단으로 안 돌아오면 재무장하지 않는다(즉시 재체결 인공물 방지)."""
    bars = [
        (10, 106.0, 104.0, 105.5),  # 재-탭 + 체결 105
        (20, 121.0, 110.0, 118.0),  # 위이탈 손절 120
        (30, 130.0, 121.0, 125.0),  # 계속 위(low=121>105) → 재무장 없음
        (40, 140.0, 131.0, 135.0),  # 계속 위 → 재무장 없음
    ]
    substeps = _substeps(bars)
    _cands, events = _short_chain(
        _long_candidate(break_time=None), switch_time=0, **_short_args(substeps)
    )
    assert len(events) == 1
    assert events[0].is_stop


def test_short_chain_no_return_no_fill() -> None:
    """가격이 상단으로 한 번도 안 오면 숏 0건."""
    bars = [(10, 100.0, 95.0, 98.0), (20, 101.0, 96.0, 99.0)]  # high < 105 → 미체결
    substeps = _substeps(bars)
    cands, events = _short_chain(
        _long_candidate(break_time=None), switch_time=0, **_short_args(substeps)
    )
    assert cands == [] and events == []


def test_short_chain_invalidation_cuts_rearm() -> None:
    """`break_time` 이후엔 재무장하지 않는다."""
    bars = [
        (10, 106.0, 104.0, 105.5),  # 체결 105
        (20, 121.0, 110.0, 118.0),  # 위이탈 손절 120
        (30, 106.0, 100.0, 103.0),  # 상단 복귀지만 break_time 이후 → 무장 안 함
    ]
    substeps = _substeps(bars)
    cands, events = _short_chain(
        _long_candidate(break_time=25 * 60_000),
        switch_time=0,
        **_short_args(substeps),
    )
    assert len(events) == 1  # 첫 숏만, 손절 후 재무장은 무효화로 차단


# --------------------------------------------------------------------------- #
# 4. N 도달 정의 (switch)
# --------------------------------------------------------------------------- #


def _re(exit_time: int) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=115.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
        order_block=_ob(break_time=None),
    )


def test_switch_time_n0_is_parent_exit() -> None:
    assert _switch_time(_long_candidate(break_time=None), 777, 0, []) == 777


def test_switch_time_reaches_n_on_win() -> None:
    chain = [(_re(100), True, 1), (_re(200), True, 2), (_re(300), True, 3)]
    assert _switch_time(_long_candidate(break_time=None), 0, 1, chain) == 100
    assert _switch_time(_long_candidate(break_time=None), 0, 2, chain) == 200
    assert _switch_time(_long_candidate(break_time=None), 0, 3, chain) == 300


def test_switch_time_none_when_nth_not_win() -> None:
    chain = [(_re(100), True, 1), (_re(200), False, 2)]  # 2회째가 손절 → 전환 안 함
    assert _switch_time(_long_candidate(break_time=None), 0, 2, chain) is None


def test_switch_time_none_when_chain_too_short() -> None:
    chain = [(_re(100), True, 1)]  # 사슬이 N=3보다 짧다 → 도달 못 함
    assert _switch_time(_long_candidate(break_time=None), 0, 3, chain) is None


# --------------------------------------------------------------------------- #
# 5. zone_arms 팔 합성 (freeze 롱 재진입으로 결정론 고정)
# --------------------------------------------------------------------------- #


def _zone_arms_freeze(support_ns: Sequence[int], break_time: int | None) -> _ZoneArms:
    """freeze 롱 재무장(정적 지정가 100)으로 존 팔을 낸다 — band 시딩 없이 결정론적."""
    # 롱 재진입: 100 터치 체결 → 115 익절(승) 또는 90 손절(패). 존 top=105.
    bars = [
        (10, 101.0, 99.0, 100.5),  # 롱 재진입1 체결 100
        (20, 116.0, 108.0, 115.0),  # 익절 115 (win)
        (30, 101.0, 99.5, 100.2),  # 롱 재진입2 체결 100
        (40, 116.0, 108.0, 115.0),  # 익절 115 (win)
        # 전환 후 숏(윗경계 105): 상단 복귀 → 체결 → 붕괴 익절
        (50, 106.0, 100.0, 103.0),  # 재-탭 + 숏 체결 105
        (60, 90.0, 80.0, 83.0),  # 붕괴 익절 82.5
    ]
    substeps = _substeps(bars)
    return zone_arms(
        _long_candidate(break_time=break_time),
        parent_exit_time=0,
        support_ns=support_ns,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=_params(),
        cfg=_cfg(),
        funding_rates=None,
        entry_rule="freeze",
    )


def test_zone_arms_long_only_is_full_chain() -> None:
    arms = _zone_arms_freeze((0, 1, 2), break_time=None)
    # 롱 재진입 3건(익절·익절·손절) — 손절이 나면 사슬 종료. 전부 롱.
    assert len(arms.long_only) == 3
    assert all(c.side is PositionSide.LONG for c in arms.long_only)


def test_zone_arms_n1_switches_after_one_long() -> None:
    arms = _zone_arms_freeze((1,), break_time=None)
    longs = [c for c in arms.transition[1] if c.side is PositionSide.LONG]
    shorts = [c for c in arms.transition[1] if c.side is PositionSide.SHORT]
    assert len(longs) == 1  # depth≤1 롱만
    assert len(shorts) >= 1  # 전환 후 숏 사슬
    assert len(arms.short_events[1]) == len(shorts)


def test_zone_arms_n0_no_long_reentry() -> None:
    arms = _zone_arms_freeze((0,), break_time=None)
    longs = [c for c in arms.transition[0] if c.side is PositionSide.LONG]
    assert longs == []  # 부모 익절 직후 바로 숏 전환
    assert any(c.side is PositionSide.SHORT for c in arms.transition[0])


def test_zone_arms_unreached_n_equals_long_only() -> None:
    """N이 지지 횟수보다 크면 전환 팔 = 롱-온리(숏 없음)."""
    arms = _zone_arms_freeze((5,), break_time=None)
    assert arms.transition[5] == arms.long_only
    assert arms.short_events[5] == []


# --------------------------------------------------------------------------- #
# 6. 순 R · CSV 왕복
# --------------------------------------------------------------------------- #


def test_net_r_definition() -> None:
    # return_pct = 0.03, entry 100, stop 120 → risk_frac 0.2 → net_r 0.15
    assert abs(_net_r(0.03, 100.0, 120.0) - 0.15) < 1e-12
    assert _net_r(0.03, 100.0, 100.0) == 0.0  # 0R 방어


def test_book_row_roundtrip(tmp_path: Path) -> None:
    rows = [
        BookScopeRow(
            scope="all",
            arm="N1",
            segment="oos_warm",
            exclude_symbol="",
            num_cells=9,
            num_symbols=9,
            num_trades=42,
            win_rate=0.5,
            total_return=1.23,
            max_drawdown=0.15,
            return_over_mdd=8.2,
            peak_concurrency=3,
            max_concurrent_risk=0.07,
            liquidation_events=0,
        )
    ]
    path = tmp_path / "book.csv"
    book_to_frame(rows).to_csv(path, index=False)
    back = book_from_csv(path)
    assert back == rows


def test_diag_row_roundtrip_and_props(tmp_path: Path) -> None:
    row = ShortDiagRow(
        symbol="BTC/USDT",
        timeframe="4h",
        support_n=1,
        segment="oos_warm",
        zones_switched=10,
        short_fills=25,
        wins=8,
        stops=15,
        unclosed=2,
        collapse_r_sum=12.0,
        stop_r_sum=-15.0,
        net_r_sum=-4.5,
        net_pp_sum=-3.2,
        funding_coverage_gap=False,
    )
    assert row.win_rate is not None and abs(row.win_rate - 8 / 23) < 1e-12
    assert row.shorts_per_zone == 2.5
    assert row.mean_net_r is not None and abs(row.mean_net_r - (-4.5 / 25)) < 1e-12
    path = tmp_path / "diag.csv"
    diag_to_frame([row]).to_csv(path, index=False)
    assert diag_from_csv(path) == [row]
