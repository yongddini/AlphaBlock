"""WAN-436: 몰림 규칙 · 복리 평가손 시뮬레이션 · 판정 상수를 동작으로 고정한다.

지키는 것:

1. **규칙은 전부 옵트인이다** — `RuleSet()`은 크기 배율 1 · 건너뛰기 없음 · 손절가 그대로
   (검산 전제).
2. **숫자는 착수 전 고정이다** — 이슈 §사양의 값이 그대로 박혀 있다(WAN-161).
3. **신호 수는 인과적이다** — `[t − 24h, t)`만 세고 같은 ms·미래는 세지 않는다.
4. **복리는 진입 순간의 자본으로 사이징한다** — 동시에 열린 두 거래는 같은 자본을 본다(탐색의 첫
   근사가 부풀린 자리).
5. **판정은 코드가 낸다** — 오차 안이면 그 사실을 문장에 싣는다.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pandas as pd
import pytest

from backtest import wan424_stoch_ob_arm as w424
from backtest import wan436_stoch_crowd_rules as m
from backtest.models import ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate

H = 3_600_000
MIN = 60_000


# --- 1·2. 상수와 옵트인 -----------------------------------------------------------------


def test_constants_are_the_issue_spec() -> None:
    assert m.STOP_MULTIPLE == 2.0
    assert (m.SKIP_LOW, m.SKIP_HIGH) == (1, 10)
    assert m.CROWD_MIN == m.SKIP_HIGH + 1 == 11
    assert m.BUDGET == 10.0
    assert m.BTC_GATE == -0.0259
    assert m.RISK == 0.015
    assert m.MDD_LIMIT == 0.30
    assert m.HOLD == 4 and m.K_THRESHOLD == 25.0 and m.FLOOR == 0.04
    assert m.NEIGHBOR_GATES == (-0.020, -0.030)
    assert m.NEIGHBOR_RISKS == (0.0125, 0.0175)
    assert m.SYMBOLS is w424.SYMBOLS and m.TIMEFRAMES is w424.TIMEFRAMES


def test_arms_are_one_axis_apart() -> None:
    """제안 팔은 기준 팔에 게이트 **하나만** 더한 것이다(두 축이 같이 움직이면 비교가 안 된다)."""
    assert m.RuleSet() == m.OFF
    assert dataclasses.replace(m.PROPOSED, btc_gate=None) == m.BASE
    assert m.BASE.stop_multiple == 2.0 and m.BASE.skip_early and m.BASE.budget == 10.0


def test_rules_off_change_nothing() -> None:
    for count in (0, 1, 5, 10, 11, 50, 500):
        for btc in (-0.2, 0.0, 0.1, math.nan):
            assert m.rule_decision(m.OFF, count, btc) == 1.0


# --- 3. 규칙 ----------------------------------------------------------------------------


def test_skip_early_window() -> None:
    r = m.RuleSet(skip_early=True)
    assert m.rule_decision(r, 0, None) == 1.0
    assert all(m.rule_decision(r, c, None) == 0.0 for c in range(1, 11))
    assert m.rule_decision(r, 11, None) == 1.0


def test_btc_gate_only_bites_when_crowded() -> None:
    r = m.RuleSet(btc_gate=-0.0259)
    assert m.rule_decision(r, 5, 0.05) == 1.0  # 몰림 아님 → 게이트 무관
    assert m.rule_decision(r, 11, -0.03) == 1.0
    assert m.rule_decision(r, 11, -0.0259) == 1.0  # 경계 포함
    assert m.rule_decision(r, 11, -0.02) == 0.0
    assert m.rule_decision(r, 30, math.nan) == 0.0  # 모르면 안 들어간다


def test_budget_formula() -> None:
    r = m.RuleSet(budget=10.0)
    assert m.rule_decision(r, 0, None) == 1.0
    assert m.rule_decision(r, 9, None) == 1.0
    assert m.rule_decision(r, 19, None) == pytest.approx(0.5)
    assert m.rule_decision(r, 99, None) == pytest.approx(0.1)


def test_signal_counts_are_causal() -> None:
    t = [0, 10 * H, 20 * H, 20 * H, 30 * H, 44 * H]
    # 30H: [6H, 30H) 안 = 10H, 20H, 20H → 3 · 같은 ms 20H 두 개는 서로를 세지 않는다.
    assert m.signal_counts(t) == [0, 1, 2, 2, 3, 3]


# --- 청산 --------------------------------------------------------------------------------


def _cand(entry: float = 100.0, stop: float = 95.0, t0: int = 0) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        trigger_time=t0,
        entry_time=t0,
        entry_price=entry,
        stop_price=stop,
        exit_time=t0,
        exit_price=entry,
        reason=ExitReason.END_OF_DATA,
    )


def _entry(lows: list[float], closes: list[float], stop: float = 95.0) -> m.ArmEntry:
    times = np.arange(len(lows), dtype=np.int64) * MIN
    return m.ArmEntry("X", "1h", _cand(stop=stop), 0.0, times, np.array(lows), np.array(closes))


def test_arm_exit_k1_keeps_the_original_stop_exactly() -> None:
    """k=1은 `e − (e − sp)`를 쓰지 않는다 — 끝자리가 흔들리면 사이징이 흔들려 검산이 깨진다."""
    e = _entry([99.0] * 5, [99.5] * 5, stop=95.123456789)
    cand, _ = m.arm_exit(e, 1.0)
    assert cand.stop_price == 95.123456789


def test_arm_exit_stop_first_then_time_exit() -> None:
    e = _entry([99, 96, 94, 98, 99], [99, 97, 95, 99, 101])
    c1, off1 = m.arm_exit(e, 1.0)
    assert (c1.reason, off1, c1.exit_price) == (ExitReason.STOP_LOSS, 2, 95.0)
    c2, off2 = m.arm_exit(e, 2.0)  # 손절 90 → 안 닿음 → 마지막 1분 종가
    assert (c2.reason, off2, c2.exit_price, c2.stop_price) == (ExitReason.END_OF_DATA, 4, 101, 90.0)


def test_hold_end_counts_htf_bars_from_entry() -> None:
    times = np.arange(0, 6 * H, 30 * MIN, dtype=np.int64) + 30 * MIN  # 진입 봉 중간부터
    # 봉: [0,1H)에 1개 · 이후 봉마다 2개. 4봉 = 인덱스 0..6 → 끝 7.
    assert m.hold_end(times, H, 4) == 7


# --- 4. 복리 + 평가손 ----------------------------------------------------------------------


def _trade(
    e: int, x: int, r: float, lows: list[float] | None = None, size: float = 1.0, back: bool = False
) -> m.PlacedTrade:
    lows = lows if lows is not None else [100.0] * ((x - e) // MIN)
    times = np.arange(e, e + len(lows) * MIN, MIN, dtype=np.int64)
    return m.PlacedTrade(
        "X", "1h", e, x, r, r < 0, back, size, 100.0, 90.0, times, np.array(lows), np.array(lows)
    )


def test_simulate_sizes_concurrent_trades_off_the_same_equity() -> None:
    """두 거래가 겹치면 둘 다 진입 순간 자본 1로 사이징된다.

    청산 순서로 곱하면(탐색의 첫 근사) 두 번째가 자본 1.1로 사이징된다.
    """
    trades = [_trade(0, 10 * MIN, 10.0), _trade(5 * MIN, 20 * MIN, 10.0)]
    res = m.simulate(trades, risk=0.01)
    assert res.total_return == pytest.approx(0.2)
    sequential = (1 + 0.01 * 10) ** 2 - 1  # 탐색의 첫 근사
    assert res.total_return < sequential


def test_simulate_marks_open_positions_at_the_minute_low() -> None:
    """보유 중 저가가 손절선(90)까지 가면 평가손이 −1R(자본 1% × 1)이다."""
    lows = [100.0, 95.0, 90.0, 100.0]
    res = m.simulate([_trade(0, 4 * MIN, 0.5, lows)], risk=0.01)
    assert res.mdd_low == pytest.approx(0.01)
    assert res.total_return == pytest.approx(0.005)


def test_simulate_budget_scales_risk() -> None:
    full = m.simulate([_trade(0, 5 * MIN, 1.0)], risk=0.01)
    half = m.simulate([_trade(0, 5 * MIN, 1.0, size=0.5)], risk=0.01)
    assert half.total_return == pytest.approx(full.total_return / 2)


def test_simulate_counts_notional_cap_breaches() -> None:
    """손절폭 10% · 리스크 1% → 명목 10% · 60개 동시면 6배 > 5배 → 5배를 넘긴 진입부터 센다."""
    trades = [_trade(i * MIN, 100 * MIN, 0.0) for i in range(60)]
    assert m.simulate(trades, risk=0.01).cap_breaches == 10


def test_segment_split() -> None:
    ts = [_trade(0, MIN, 1.0, back=False), _trade(MIN, 2 * MIN, 1.0, back=True)]
    assert [t.back for t in m.segment_trades(ts, m.SEGMENT_BACK)] == [True]
    assert [t.back for t in m.segment_trades(ts, m.SEGMENT_FRONT)] == [False]
    assert len(m.segment_trades(ts, m.SEGMENT_FULL)) == 2


# --- 5. 판정 ----------------------------------------------------------------------------


def _sim(ret: float, mdd: float) -> m.SimResult:
    return m.SimResult(10, ret, 0.0, mdd, mdd, -mdd, 0, 0, 0, {})


def test_verdict_needs_both_mdd_and_beating_base() -> None:
    assert m.verdict(_sim(1.0, 0.2), _sim(0.5, 0.3), 0.3, 0.01).startswith("✅")
    assert m.verdict(_sim(0.4, 0.2), _sim(0.5, 0.3), 0.3, 0.01).startswith("❌")
    assert m.verdict(_sim(1.0, 0.31), _sim(0.5, 0.3), 0.3, 0.01).startswith("❌")


def test_verdict_says_when_the_difference_is_noise() -> None:
    assert "오차와 구분되지 않는다" in m.verdict(_sim(1.0, 0.2), _sim(0.5, 0.3), 0.01, 0.05)
    assert "밖이다" in m.verdict(_sim(1.0, 0.2), _sim(0.5, 0.3), 0.5, 0.05)


def test_day_block_diff_is_zero_for_identical_arms_and_seeded() -> None:
    ts = [
        _trade(d * 86_400_000, d * 86_400_000 + MIN, r) for d, r in enumerate([1, -1, 0.5, 2, -0.3])
    ]
    point, se = m.day_block_diff_se(ts, ts)
    assert point == pytest.approx(0.0) and se == pytest.approx(0.0)
    other = [dataclasses.replace(t, net_r=t.net_r + 0.1) for t in ts]
    a = m.day_block_diff_se(other, ts)
    assert a == m.day_block_diff_se(other, ts)
    assert a[0] == pytest.approx(0.1)


def test_render_smoke() -> None:
    rows = m.make_rows("제안 팔", m.PROPOSED, m.RISK, [_trade(0, 5 * MIN, 1.0)])
    months = pd.DataFrame({"arm": ["제안 팔"], "month": ["2020-09"], "trades": [1]})
    text = m.render(rows, ["✅ 검산"], "✅ **통과** — 테스트", months, None)
    assert "## 판정" in text and "기준 팔 대 제안 팔" in text


def test_risk_for_mdd_finds_the_largest_size_within_target() -> None:
    """손절선까지 빠졌다 회복하는 거래 하나.

    평가손 MDD = 리스크(1R 하락)이므로 답은 목표 그 자체다.
    """
    lows = [100.0, 90.0, 100.0, 100.0]
    trades = [_trade(0, 4 * MIN, 0.0, lows)]
    risk = m.risk_for_mdd(trades, target=0.05, lo=0.001, hi=0.2, iterations=30)
    assert risk == pytest.approx(0.05, rel=1e-3)
    assert m.simulate(trades, risk=risk).mdd_low <= 0.05


def test_matched_gates_include_the_base_and_the_proposed_point() -> None:
    assert m.MATCHED_GATES[0] is None
    assert m.BTC_GATE in m.MATCHED_GATES
    assert all(g is None or g < 0 for g in m.MATCHED_GATES)


def test_render_matched_says_it_is_not_the_verdict() -> None:
    row = m.MatchedRow(None, 0.015, 10, 1.0, 0.2, 0.3, -0.2, 0.5, 0.3, 0.4, 0.2)
    text = "\n".join(m.render_matched([row]))
    assert "판정 아님" in text and "게이트 없음(기준 팔)" in text


def test_render_matched_splits_tables_by_target() -> None:
    rows = [
        m.MatchedRow(None, r, 10, 1.0, 0.2, t, -0.2, 0.5, t, 0.4, 0.2, target=t)
        for r, t in ((0.015, 0.30), (0.02, 0.35))
    ]
    text = "\n".join(m.render_matched(rows))
    assert "MDD 30%에 크기를" in text and "MDD 35%에 크기를" in text
    assert text.index("MDD 30%에") < text.index("1.50%") < text.index("MDD 35%에")
    assert m.MATCHED_TARGETS[0] == m.MDD_LIMIT and 0.35 in m.MATCHED_TARGETS


def test_render_grid_marks_cells_over_the_limit() -> None:
    cells = [
        m.GridCell(None, r, 10, 1.0, 0.2, mdd, -0.1, 0.5, mdd, 0.4, mdd / 2)
        for r, mdd in ((0.01, 0.2), (0.02, 0.35))
    ]
    text = "\n".join(m.render_grid(cells))
    assert "+100% · 20%" in text and "+100% · 35% ⚠️" in text
    assert "판정 근거가 아니다" in text
    assert tuple(sorted(m.GRID_RISKS)) == m.GRID_RISKS and 0.015 in m.GRID_RISKS
