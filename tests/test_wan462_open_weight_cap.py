"""WAN-462 — 북의 동시 위험 합계 한도(`open_weight_cap`)를 동작으로 고정한다.

1. **안 주면 비트 동일** — 한도 없는 판과 거래가 같다.
2. **인과** — 진입 순간 이미 열린 포지션의 무게만 센다(같은 시각에 닫힌 포지션은 안 센다).
3. **북 안에서 판정** — 건너뛴 후보는 칸을 차지하지 않아, 같은 칸의 뒤 후보가 들어올 수 있다.
4. 한쪽만 주거나 0 이하면 거부한다.
"""

from __future__ import annotations

import pytest

from backtest.leverage_book import LEGACY_BOOK_PARAMS, BookCell, run_leverage_book
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate
from execution.sizing import PositionSizingParams


def _cand(entry: int, exit_: int) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry,
        entry_price=100.0,
        exit_time=exit_,
        exit_price=101.5,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.0,
        trigger_time=entry,
    )


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=10_000.0,
        risk_sizing=PositionSizingParams(
            risk_per_trade=0.01, leverage=50.0, min_stop_distance_fraction=0.0
        ),
    )


def _cells() -> list[BookCell]:
    return [
        BookCell("A", "1h", [_cand(1_000, 5_000), _cand(6_000, 7_000)]),
        BookCell("B", "1h", [_cand(2_000, 6_000)]),
        BookCell("C", "1h", [_cand(3_000, 6_500), _cand(5_000, 8_000)]),
    ]


def _keys(out: object) -> list[tuple[int, int]]:
    return [(t.entry_time, t.exit_time) for t in out.trades]  # type: ignore[attr-defined]


def test_no_cap_is_bit_identical() -> None:
    base = run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS)
    off = run_leverage_book(
        _cells(), _cfg(), LEGACY_BOOK_PARAMS, open_weight_cap=100.0, cap_weight=lambda c, x: 1.0
    )
    assert _keys(base) == _keys(off)
    assert [t.realized_pnl for t in base.trades] == [t.realized_pnl for t in off.trades]


def test_cap_skips_only_when_open_weight_would_exceed() -> None:
    """무게 1씩 · 한도 2: t=3000(A·B 열림)은 막히고, t=5000(A가 그 시각 닫힘 → B만)은 들어간다."""
    out = run_leverage_book(
        _cells(), _cfg(), LEGACY_BOOK_PARAMS, open_weight_cap=2.0, cap_weight=lambda c, x: 1.0
    )
    assert (3_000, 6_500) not in _keys(out)
    assert (5_000, 8_000) in _keys(out)  # 같은 시각 청산은 이미 닫힌 것(반개구간)
    skipped = [r for r in out.stats.skip_records if r.reason == "open_risk_cap"]
    assert [(r.candidate.entry_time) for r in skipped] == [3_000]


def test_skipped_candidate_frees_the_cell() -> None:
    """C의 첫 후보가 한도로 빠지면 C 칸은 비어 있다 — 그래서 C의 다음 후보(5000)가 들어온다.

    (복리 층에서 거래를 지우는 것과 다르다: 북 안에서 판정해야 이 채널이 보인다 · WAN-341.)
    """
    base = run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS)
    assert (5_000, 8_000) not in _keys(base)  # 한도 없으면 C 칸이 차 있어 못 들어간다
    out = run_leverage_book(
        _cells(), _cfg(), LEGACY_BOOK_PARAMS, open_weight_cap=2.0, cap_weight=lambda c, x: 1.0
    )
    assert (5_000, 8_000) in _keys(out)


def test_cap_arguments_validated() -> None:
    with pytest.raises(ValueError, match="함께"):
        run_leverage_book(_cells(), _cfg(), LEGACY_BOOK_PARAMS, open_weight_cap=2.0)
    with pytest.raises(ValueError, match="양수"):
        run_leverage_book(
            _cells(), _cfg(), LEGACY_BOOK_PARAMS, open_weight_cap=0.0, cap_weight=lambda c, x: 1.0
        )
