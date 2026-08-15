"""WAN-269 재진입 북 band 팔의 배선·자(尺)를 동작으로 고정한다.

무거운 백테스트는 실데이터 검산 두 개에서만 돌리고(없으면 skip), 나머지는 순수 함수다.
여기서 못 박는 것:

1. **entry_rule 배선** — `reentry_candidates(entry_rule="freeze")`(기본)는 인자 없는 호출과
   **비트 동일**(wan261/262 CSV 재현)하고, `"band"`는 재무장 지정가를 재산정해 결과가
   달라진다(라벨만 붙는 실패 방지 — WAN-95/112 부류).
2. **실데이터 검산(옵트인)** — `run_cells(reentry_entry_rule="freeze")` ≡ `run_cells()` 재진입
   후보, band는 재진입 후보를 실제로 바꾼다. base 후보는 팔 사이에서 비트 동일.
3. **리포트 순수 함수** — 세 팔 판정·CSV 왕복·병합·표.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest import harness
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
)
from backtest.models import ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan169_leverage_book import run_cells
from backtest.wan228_reentry_census import reentry_candidates
from backtest.wan261_reentry_book import BookScopeRow, CellReturnRow
from backtest.wan269_reentry_book_band import (
    ARM_SPEC,
    ARMS,
    _verdict_15m,
    book_from_csv,
    book_to_frame,
    build_summary_markdown,
    cells_from_csv,
    cells_to_frame,
    merge_book,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

HTF_MS = 3_600_000  # 1h


# --------------------------------------------------------------------------- #
# 1. entry_rule 배선 (합성 — 무거운 데이터 없음)
# --------------------------------------------------------------------------- #


def _substeps(bars: list[tuple[int, float, float, float]]) -> list[SubStep]:
    out: list[SubStep] = []
    for minute, high, low, close in bars:
        t = minute * 60_000
        out.append(
            SubStep(time=t, high=high, low=low, close=close, htf_bar_time=(t // HTF_MS) * HTF_MS)
        )
    return out


def _long_candidate() -> _Candidate:
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=105.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=1.5,
        break_time=None,
    )
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=0,
        exit_price=115.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
        order_block=ob,
    )


def _win_then_stop_substeps() -> list[SubStep]:
    return _substeps(
        [
            (1, 116.0, 114.0, 115.0),
            (10, 101.0, 99.0, 100.5),
            (20, 116.0, 108.0, 115.0),
            (30, 101.0, 99.5, 100.2),
            (40, 95.0, 89.0, 90.5),
            (50, 101.0, 99.0, 100.0),
        ]
    )


def _kwargs(substeps: list[SubStep]) -> dict[str, object]:
    return dict(
        parent_exit_time=0,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=ConfluenceParams(),
        cfg=harness.build_config("1h"),
        funding_rates=None,
    )


def test_reentry_candidates_entry_rule_default_is_freeze() -> None:
    """`entry_rule` 기본값은 freeze — 인자를 안 주면 예전과 비트 동일(완료기준 2 배선 몫)."""
    substeps = _win_then_stop_substeps()
    default = reentry_candidates(_long_candidate(), **_kwargs(substeps))  # type: ignore[arg-type]
    freeze = reentry_candidates(_long_candidate(), entry_rule="freeze", **_kwargs(substeps))  # type: ignore[arg-type]
    assert len(default) == len(freeze) == 2
    for a, b in zip(default, freeze, strict=True):
        assert a.entry_price == b.entry_price
        assert a.entry_time == b.entry_time
        assert a.exit_price == b.exit_price
        assert a.reason is b.reason


def test_band_arm_needs_a_filter() -> None:
    """볼린저 필터가 없으면 band 팔은 재산정할 밴드가 없어 재진입을 내지 않는다(가드)."""
    substeps = _win_then_stop_substeps()
    params = ConfluenceParams(deviation_filter=None)
    kwargs = _kwargs(substeps)
    kwargs["params"] = params
    band = reentry_candidates(_long_candidate(), entry_rule="band", **kwargs)  # type: ignore[arg-type]
    assert band == []


# --------------------------------------------------------------------------- #
# 2. 실데이터 검산 (옵트인 — data/ohlcv.db 없으면 skip)
# --------------------------------------------------------------------------- #

_START = "2024-01-01"
_END = "2024-05-01"
_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
_TFS = ["1h"]


def _require_real_data() -> None:
    market = harness.load_market_data(
        harness.normalize_symbol("BTCUSDT"), "1h", start_ms=None, end_ms=None, need_1m=True
    )
    if market.empty:
        pytest.skip("BTCUSDT 1h 실데이터가 없어 WAN-269 배선 검산을 건너뜁니다(CI 기본).")


def _reentry_prices(payload: object) -> list[float]:
    cands = getattr(payload, "reentry_candidates", {})
    return [
        round(c.entry_price, 8)
        for seg in (SEGMENT_FULL, "is", SEGMENT_OOS)
        for c in cands.get(seg, ())
    ]


def test_run_cells_entry_rule_default_reproduces_band() -> None:
    """`run_cells()`(기본) ≡ `run_cells(reentry_entry_rule='band')` — 기본이 채택 규칙(WAN-305).

    🔁 WAN-269 시절 기본은 `freeze`였으나 WAN-305가 채택 규칙(band, WAN-273)을 기본으로
    승격했다 — freeze는 옵트인 존치(wan261/262 CSV 재현은 명시 핀).
    """
    _require_real_data()
    kw = dict(start=_START, end=_END, jobs=1, reentry=True)
    default = run_cells(_SYMBOLS, _TFS, **kw)  # type: ignore[arg-type]
    band = run_cells(_SYMBOLS, _TFS, reentry_entry_rule="band", **kw)  # type: ignore[arg-type]
    assert len(default) == len(band)
    for a, b in zip(default, band, strict=True):
        # base 후보는 규칙과 무관 — 팔 사이에서 비트 동일.
        for seg in (SEGMENT_FULL, SEGMENT_OOS):
            assert len(a.candidates[seg]) == len(b.candidates[seg])
        # 재진입 후보(band)도 기본 호출과 비트 동일.
        assert _reentry_prices(a) == _reentry_prices(b)


def test_band_changes_reentry_but_not_base() -> None:
    """band는 재진입 후보를 실제로 바꾸고 base 후보는 안 바꾼다(배선이 죽지 않았다)."""
    _require_real_data()
    kw = dict(start=_START, end=_END, jobs=1, reentry=True)
    freeze = run_cells(_SYMBOLS, _TFS, reentry_entry_rule="freeze", **kw)  # type: ignore[arg-type]
    band = run_cells(_SYMBOLS, _TFS, reentry_entry_rule="band", **kw)  # type: ignore[arg-type]
    base_freeze = [[len(p.candidates[s]) for s in (SEGMENT_FULL, SEGMENT_OOS)] for p in freeze]
    base_band = [[len(p.candidates[s]) for s in (SEGMENT_FULL, SEGMENT_OOS)] for p in band]
    assert base_freeze == base_band  # base 불변.
    # 재진입 가격은 최소 한 칸에서 달라진다(밴드 재산정).
    assert [_reentry_prices(p) for p in freeze] != [_reentry_prices(p) for p in band]


# --------------------------------------------------------------------------- #
# 3. 리포트 순수 함수 (세 팔)
# --------------------------------------------------------------------------- #


def _book(
    *,
    scope: str,
    arm: str,
    segment: str = SEGMENT_OOS_WARM,
    exclude: str = "",
    total_return: float = 1.0,
    max_drawdown: float = 0.20,
    max_concurrent_risk: float = 0.07,
    liquidation_events: int = 0,
    num_trades: int = 100,
) -> BookScopeRow:
    return BookScopeRow(
        scope=scope,
        arm=arm,
        segment=segment,
        exclude_symbol=exclude,
        num_cells=27,
        num_symbols=9,
        num_trades=num_trades,
        win_rate=0.55,
        total_return=total_return,
        max_drawdown=max_drawdown,
        return_over_mdd=total_return / max_drawdown if max_drawdown else None,
        peak_concurrency=11,
        max_concurrent_risk=max_concurrent_risk,
        liquidation_events=liquidation_events,
    )


def _three_arm_book(scope: str, *, band_mdd: float) -> list[BookScopeRow]:
    return [
        _book(scope=scope, arm="off", max_drawdown=0.18),
        _book(scope=scope, arm="freeze", max_drawdown=0.20),
        _book(scope=scope, arm="band", max_drawdown=band_mdd),
    ]


def test_arms_and_spec_consistent() -> None:
    assert ARMS == ("off", "freeze", "band")
    assert ARM_SPEC["off"] == ("freeze", False)
    assert ARM_SPEC["band"] == ("band", True)


def test_verdict_flags_band_raising_risk() -> None:
    rows = _three_arm_book("all", band_mdd=0.30)  # band MDD 30% vs freeze 20% = +10%p.
    text = verdict(rows)
    assert "band가 freeze보다 북 위험을 키운다" in text


def test_verdict_flags_small_difference() -> None:
    rows = _three_arm_book("all", band_mdd=0.205)  # +0.5%p ≤ 1%p.
    assert "위험 차이는 작다" in verdict(rows)


def test_verdict_needs_all_three_arms() -> None:
    rows = [_book(scope="all", arm="off"), _book(scope="all", arm="freeze")]
    assert "판정 불가" in verdict(rows)


def test_verdict_15m_present_only_with_15m_scope() -> None:
    assert _verdict_15m(_three_arm_book("all", band_mdd=0.20)) == ""
    note = _verdict_15m(_three_arm_book("15m", band_mdd=0.25))
    assert "15m" in note and "낙관 가정 최대 의존" in note


def test_book_frame_roundtrip(tmp_path: Path) -> None:
    rows = _three_arm_book("all", band_mdd=0.22)
    path = tmp_path / "book.csv"
    book_to_frame(rows).to_csv(path, index=False)
    back = book_from_csv(path)
    assert [r.arm for r in back] == ["off", "freeze", "band"]
    assert back[2].max_drawdown == 0.22


def test_cell_frame_roundtrip(tmp_path: Path) -> None:
    rows = [
        CellReturnRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment=SEGMENT_OOS_WARM,
            arm=arm,
            num_candidates=10,
            num_trades=8,
            win_rate=0.5,
            total_return=0.1,
            max_drawdown=0.05,
        )
        for arm in ARMS
    ]
    path = tmp_path / "cells.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    assert [r.arm for r in cells_from_csv(path)] == ["off", "freeze", "band"]


def test_merge_book_new_tf_wins_and_keeps_all_only_from_new() -> None:
    old = [_book(scope="1h", arm="band"), _book(scope="all", arm="band")]
    new = [_book(scope="15m", arm="band")]  # 새 TF, `all` 없음 → 옛 all 보존.
    merged = merge_book(old, new)
    scopes = sorted({r.scope for r in merged})
    assert scopes == ["15m", "1h", "all"]


def test_summary_renders_three_arms(tmp_path: Path) -> None:
    book_rows = _three_arm_book("all", band_mdd=0.25) + [_book(scope="1h", arm=a) for a in ARMS]
    cell_rows = [
        CellReturnRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment=SEGMENT_OOS_WARM,
            arm=arm,
            num_candidates=10,
            num_trades=8,
            win_rate=0.5,
            total_return=0.1,
            max_drawdown=0.05,
        )
        for arm in ARMS
    ]
    md = build_summary_markdown(
        cell_rows, book_rows, cells_csv=tmp_path / "c.csv", book_csv=tmp_path / "b.csv"
    )
    assert "재진입 off" in md and "재진입 freeze" in md and "재진입 band" in md
    assert "band를 북에 얹으면" in md
