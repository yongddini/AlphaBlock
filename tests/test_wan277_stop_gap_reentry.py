"""WAN-277 손절 갭-체결 스트레스 (재진입 ON) 모듈 테스트.

WAN-276 기계를 재사용하므로 α 사후 변환·행 모델·렌더는 `test_wan276_stop_gap_fill`이 고정한다.
여기서는 **재진입 ON 배선**만 합성 후보로 고정한다:

1. 재진입 후보가 `include_reentry=True`로 실제 북에 들어간다(OFF와 갈린다).
2. α=0 재진입 ON 북이 원본과 **비트 재현**된다(사후 변환 항등).
3. 재진입 손절 후보가 `exit_extreme`를 실어 base와 **같은 α 슬리피지**를 받는다.
"""

from __future__ import annotations

import dataclasses

import pytest

from backtest import harness
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload, _segment_cells
from backtest.wan276_stop_gap_fill import (
    apply_stop_slippage,
    build_grid,
    slip_candidate,
    verify_alpha0_identity,
)
from backtest.wan277_stop_gap_reentry import REENTRY_ENTRY_RULE, _reentry_delta_table
from backtest.zone_limit_backtest import _Candidate

_HOUR = 3_600_000


def _stop_candidate(entry_time: int, *, extreme: float = 80.0) -> _Candidate:
    """손절로 청산되는 롱 후보 — 진입 100 · 손절 90 · 봉 저가(extreme) 80."""
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=entry_time + _HOUR,
        exit_price=90.0,
        reason=ExitReason.STOP_LOSS,
        stop_price=90.0,
        exit_extreme=extreme,
        trigger_time=entry_time,
    )


def _payload_with_reentry(symbol: str) -> CellPayload:
    """base 손절 2건 + (익절 후) 재진입 손절 1건 — 재진입도 `exit_extreme`를 싣는다."""
    base = (_stop_candidate(0), _stop_candidate(20 * _HOUR))
    reentry = (_stop_candidate(40 * _HOUR, extreme=70.0),)
    return CellPayload(
        symbol=symbol,
        timeframe="1h",
        boundary_ms=0,
        candidates={harness.SEGMENT_FULL: base, harness.SEGMENT_OOS: base},
        funding={harness.SEGMENT_FULL: (), harness.SEGMENT_OOS: ()},
        rows=(),
        reentry_candidates={harness.SEGMENT_FULL: reentry, harness.SEGMENT_OOS: reentry},
    )


def test_adopted_reentry_rule_is_band() -> None:
    """WAN-273 채택 재진입 규칙 = band(book_cli 채택 상수에서 물려받는다)."""
    assert REENTRY_ENTRY_RULE == "band"


def test_include_reentry_adds_candidates_to_book() -> None:
    """`include_reentry=True`면 재진입 후보가 base와 합쳐져 북 셀에 들어간다."""
    payload = _payload_with_reentry("BTCUSDT")
    off = _segment_cells([payload], harness.SEGMENT_FULL, "")
    on = _segment_cells([payload], harness.SEGMENT_FULL, "", include_reentry=True)
    assert len(off[0].candidates) == 2  # base만
    assert len(on[0].candidates) == 3  # base + 재진입


def test_reentry_on_grid_differs_from_off() -> None:
    """재진입 ON 격자가 OFF보다 거래가 많다(재진입이 실제 배치된다)."""
    payloads = [_payload_with_reentry("BTCUSDT"), _payload_with_reentry("ETHUSDT")]
    off = build_grid(payloads, payloads, ["1h"], include_reentry=False)
    on = build_grid(payloads, payloads, ["1h"], include_reentry=True)
    off_full = {r.scenario: r for r in off if r.segment == harness.SEGMENT_FULL}
    on_full = {r.scenario: r for r in on if r.segment == harness.SEGMENT_FULL}
    assert on_full["alpha_0.00"].num_trades > off_full["alpha_0.00"].num_trades


def test_reentry_on_alpha_zero_is_identity() -> None:
    """재진입 ON 북에서도 α=0 사후 변환이 원본과 비트 일치."""
    payloads = [_payload_with_reentry("BTCUSDT"), _payload_with_reentry("ETHUSDT")]
    diff = verify_alpha0_identity(payloads, ["1h"], include_reentry=True)
    assert diff == pytest.approx(0.0, abs=1e-12)


def test_reentry_stop_gets_slippage() -> None:
    """α 사후 변환이 base뿐 아니라 재진입 손절 후보의 청산가도 바꾼다(WAN-277 핵심)."""
    payload = _payload_with_reentry("BTCUSDT")
    slipped = apply_stop_slippage([payload], 1.0)[0]
    # base 손절: 90 → 봉 저가 80.
    assert all(
        c.exit_price == pytest.approx(80.0) for c in slipped.candidates[harness.SEGMENT_FULL]
    )
    # 재진입 손절: 90 → 봉 저가 70(그 후보의 극값).
    re = slipped.reentry_candidates[harness.SEGMENT_FULL][0]
    assert re.exit_price == pytest.approx(70.0)


def test_reentry_alpha_deepens_book_drawdown() -> None:
    """재진입 ON 북에서 α가 커지면(더 나쁜 손절 체결) MDD가 깊어진다."""
    payloads = [_payload_with_reentry("BTCUSDT"), _payload_with_reentry("ETHUSDT")]
    rows = build_grid(payloads, payloads, ["1h"], include_reentry=True)
    full = {r.scenario: r for r in rows if r.segment == harness.SEGMENT_FULL}
    assert full["alpha_1.00"].max_drawdown >= full["alpha_0.00"].max_drawdown
    assert full["alpha_1.00"].total_return <= full["alpha_0.00"].total_return


def test_apply_stop_slippage_reentry_zero_untouched() -> None:
    """α=0이면 재진입 후보도 항등(손익 비트 불변)."""
    payload = _payload_with_reentry("BTCUSDT")
    out = apply_stop_slippage([payload], 0.0)[0]
    assert out.reentry_candidates[harness.SEGMENT_FULL][0].exit_price == 90.0


def test_slip_candidate_reentry_non_stop_untouched() -> None:
    """재진입 익절 후보(극값 없음)는 α와 무관하게 그대로."""
    tp = dataclasses.replace(
        _stop_candidate(0), reason=ExitReason.TAKE_PROFIT, exit_price=110.0, exit_extreme=None
    )
    assert slip_candidate(tp, 1.0) is tp


def test_reentry_delta_table_matches_overlapping_scopes_only() -> None:
    """OFF 대조 표는 겹치는 (scope, segment, scenario)만 낸다 — 없으면 헤더만."""
    payloads = [_payload_with_reentry("BTCUSDT")]
    on = build_grid(payloads, payloads, ["1h"], include_reentry=True)
    # OFF에 대응 행이 없으면(빈 목록) 데이터 행이 없다.
    assert _reentry_delta_table(on, []) == _reentry_delta_table(on, [])
    lines = _reentry_delta_table(on, [])
    assert len(lines) == 2  # 헤더 2줄만
