"""WAN-276 손절 갭-체결 민감도 모듈 테스트.

엔진 동작(α 슬리피지·지정가 미체결)은 `tests/test_substep.py`가 고정한다. 여기서는 α 사후
변환의 항등·단조성과 채택 북 스트레스 격자 배선(합성 후보)을 고정한다.
"""

from __future__ import annotations

import dataclasses

import pytest

from backtest import harness
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload
from backtest.wan276_stop_gap_fill import (
    ALPHAS,
    NONFILL_LABEL,
    SEGMENTS,
    _resolve_scopes,
    apply_stop_slippage,
    build_grid,
    slip_candidate,
)
from backtest.zone_limit_backtest import _Candidate

_HOUR = 3_600_000


def _stop_candidate(entry_time: int, *, extreme: float = 80.0) -> _Candidate:
    """손절로 청산되는 롱 후보 하나 — 진입 100 · 손절 90 · 봉 저가(extreme) 80."""
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=entry_time + _HOUR,
        exit_price=90.0,  # α=0 = 손절가
        reason=ExitReason.STOP_LOSS,
        stop_price=90.0,
        exit_extreme=extreme,
        trigger_time=entry_time,
    )


def test_slip_candidate_long_math() -> None:
    cand = _stop_candidate(0)
    assert slip_candidate(cand, 0.0).exit_price == 90.0  # 항등
    assert slip_candidate(cand, 0.5).exit_price == pytest.approx(85.0)  # 90 - 0.5*(90-80)
    assert slip_candidate(cand, 1.0).exit_price == pytest.approx(80.0)  # 봉 저가


def test_slip_candidate_short_math() -> None:
    cand = dataclasses.replace(
        _stop_candidate(0),
        side=PositionSide.SHORT,
        entry_price=100.0,
        exit_price=110.0,
        stop_price=110.0,
        exit_extreme=120.0,  # 봉 고가
    )
    assert slip_candidate(cand, 1.0).exit_price == pytest.approx(120.0)
    assert slip_candidate(cand, 0.25).exit_price == pytest.approx(112.5)  # 110 + 0.25*(120-110)


def test_slip_candidate_leaves_non_stop_untouched() -> None:
    """익절·데이터종료 후보(또는 극값 없는 후보)는 α와 무관하게 그대로."""
    tp = dataclasses.replace(_stop_candidate(0), reason=ExitReason.TAKE_PROFIT, exit_price=110.0)
    assert slip_candidate(tp, 1.0) is tp
    no_extreme = dataclasses.replace(_stop_candidate(0), exit_extreme=None)
    assert slip_candidate(no_extreme, 1.0) is no_extreme


def _payload(symbol: str) -> CellPayload:
    cands = (_stop_candidate(0), _stop_candidate(10 * _HOUR))
    return CellPayload(
        symbol=symbol,
        timeframe="1h",
        boundary_ms=0,  # 모든 후보가 따뜻한 경계 이후 → oos_warm이 전부 포함
        candidates={harness.SEGMENT_FULL: cands, harness.SEGMENT_OOS: cands},
        funding={harness.SEGMENT_FULL: (), harness.SEGMENT_OOS: ()},
        rows=(),
    )


def test_apply_stop_slippage_zero_is_identity() -> None:
    payloads = [_payload("BTCUSDT")]
    assert apply_stop_slippage(payloads, 0.0) is not payloads  # 새 리스트지만
    out = apply_stop_slippage(payloads, 0.0)
    assert out[0].candidates[harness.SEGMENT_FULL][0].exit_price == 90.0  # 값은 항등


def test_apply_stop_slippage_only_touches_stop_exit() -> None:
    payloads = [_payload("BTCUSDT")]
    out = apply_stop_slippage(payloads, 1.0)
    for cand in out[0].candidates[harness.SEGMENT_FULL]:
        assert cand.exit_price == pytest.approx(80.0)  # 손절가 → 봉 저가


def test_build_grid_scenarios_and_monotone_mdd() -> None:
    """격자가 α 스윕 + 미체결 시나리오를 전 구간에 내고, α가 커지면 손실(MDD)이 깊어진다."""
    payloads = [_payload("BTCUSDT"), _payload("ETHUSDT")]
    rows = build_grid(payloads, payloads, ["1h"])
    scenarios = {r.scenario for r in rows}
    assert scenarios == {f"alpha_{a:.2f}" for a in ALPHAS} | {NONFILL_LABEL}
    assert {r.segment for r in rows} == set(SEGMENTS)
    # 전 손절 후보라 α가 커질수록 총수익이 더 낮아진다(더 나쁜 체결).
    full = {r.scenario: r for r in rows if r.segment == harness.SEGMENT_FULL}
    assert full["alpha_1.00"].total_return <= full["alpha_0.00"].total_return
    assert full["alpha_1.00"].max_drawdown >= full["alpha_0.00"].max_drawdown
    for r in rows:
        assert r.liquidation_events >= 0
        assert r.num_symbols == 2


def test_resolve_scopes_adds_both_for_multi_tf() -> None:
    assert _resolve_scopes(["1h"], []) == ["1h"]
    assert _resolve_scopes(["1h", "4h"], []) == ["1h", "4h", "both"]
    # append: 이전 실행이 15m을 냈고 이번이 1h면 두 TF가 모여 both가 성립.
    assert _resolve_scopes(["1h"], ["15m"]) == ["1h", "both"]
