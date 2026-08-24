"""WAN-271 재진입 북 band 팔 체결 보수화(pen_5bp) 배선·자(尺)를 동작으로 고정한다.

무거운 백테스트는 실데이터 게이트 뒤에서만 돈다(CI 기본 skip). 여기서 못 박는 것:

1. **렌즈 배선(옵트인)** — `run_cells(fill=None)`(기본) ≡ 예전 후보(비트 재현), `pen_5bp`는
   관통 요구로 후보를 **줄인다**(라벨만 붙는 실패 방지 — WAN-95/112/123 부류).
2. **baseline 팔이 WAN-269 와 비트 재현** — 완료기준 3(섞지 않기). 같은 payload·계산기라
   `lens="baseline"` 북 행이 WAN-269 계산기 출력과 값이 같다.
3. **무승부/잔존 순수 함수** — band−freeze 델타, 무승부 판정, 잔존율, 분모 0 가드.
4. **리포트 순수 함수** — CSV 왕복 · 병합 · 렌더 · 판정.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from backtest import harness
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    load_market_data,
    normalize_symbol,
)
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan269_reentry_book_band import _book_scope_rows as wan269_book_rows
from backtest.wan271_reentry_book_band_stress import (
    BandStressBookRow,
    BandStressCellRow,
    _band_freeze_table,
    _gain_residual_table,
    _pair_delta,
    _residual_pct,
    _wash_holds,
    book_from_csv,
    book_to_frame,
    build_summary_markdown,
    cells_from_csv,
    cells_to_frame,
    merge_book,
    merge_cells,
    verdict,
)

_START = "2024-01-01"
_END = "2024-05-01"
_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
_TFS = ["1h"]


def _require_real_data() -> None:
    market = load_market_data(
        normalize_symbol("BTCUSDT"), "1h", start_ms=None, end_ms=None, need_1m=True
    )
    if market.empty:
        pytest.skip("BTCUSDT 1h 실데이터가 없어 WAN-271 스트레스 검산을 건너뜁니다(CI 기본).")


# --------------------------------------------------------------------------- #
# 1. 렌즈 배선 (옵트인)
# --------------------------------------------------------------------------- #


def test_fill_none_reproduces_default_candidates() -> None:
    """`run_cells(fill=None)`(기본) ≡ `run_cells()` — 렌즈 축이 비트 재현(band 재무장 규칙)."""
    _require_real_data()
    kw = dict(start=_START, end=_END, jobs=1, reentry=True, reentry_entry_rule="band")
    default = run_cells(_SYMBOLS, _TFS, **kw)  # type: ignore[arg-type]
    explicit = run_cells(_SYMBOLS, _TFS, fill=None, **kw)  # type: ignore[arg-type]
    assert len(default) == len(explicit)
    for a, b in zip(default, explicit, strict=True):
        for seg in (SEGMENT_FULL, SEGMENT_OOS):
            assert len(a.candidates[seg]) == len(b.candidates[seg])


def test_pen_5bp_flows_into_band_candidates() -> None:
    """관통 5bp 요구가 band 팔 후보 생성에 실제로 흐른다 — 라벨만 붙는 실패 방지."""
    _require_real_data()
    kw = dict(start=_START, end=_END, jobs=1, reentry=True, reentry_entry_rule="band")
    base = run_cells(_SYMBOLS, _TFS, **kw)  # type: ignore[arg-type]
    pen = run_cells(_SYMBOLS, _TFS, fill=harness.fill_preset("pen_5bp"), **kw)  # type: ignore[arg-type]
    base_prices = [tuple(round(c.entry_price, 6) for c in p.candidates[SEGMENT_FULL]) for p in base]
    pen_prices = [tuple(round(c.entry_price, 6) for c in p.candidates[SEGMENT_FULL]) for p in pen]
    # (a) 관통 요구는 base 후보를 늘리지 않는다.
    for b, p in zip(base, pen, strict=True):
        assert len(p.candidates[SEGMENT_FULL]) <= len(b.candidates[SEGMENT_FULL])
    # (b) 후보 집합 또는 진입가가 실제로 달라진다(렌즈가 죽지 않았다).
    assert base_prices != pen_prices


def test_baseline_arm_reproduces_wan269(tmp_path: Path) -> None:
    """`lens="baseline"` 북 행이 WAN-269 계산기 출력과 값 동일 — 완료기준 3(비트 재현).

    WAN-271 은 WAN-269 의 `_book_scope_rows`를 그대로 호출하므로, baseline 렌즈(fill=None)의
    payload 로 WAN-269 계산기를 직접 돌린 행과 lens 만 뺀 나머지가 일치해야 한다.
    """
    _require_real_data()
    from backtest.run import parse_date_ms

    payloads_by_rule: dict[str, Sequence[CellPayload]] = {}
    for rule in ("freeze", "band"):
        payloads = run_cells(
            _SYMBOLS,
            _TFS,
            start=_START,
            end=_END,
            jobs=1,
            reentry=True,
            reentry_entry_rule=rule,
            # WAN-365: 대조의 **양쪽**을 옛 엔진으로 맞춘다 — wan269/wan271 CSV는 소급 취소
            # 시절의 기록이고 `run_report`가 그 핀을 물고 있다. 한쪽만 새 엔진으로 두면
            # 「비트 재현」 검산이 엔진 차이를 재게 된다.
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        )
        payloads, _ = apply_funding_proxy(payloads)
        payloads_by_rule[rule] = payloads
    expected = wan269_book_rows(
        payloads_by_rule, start_ms=parse_date_ms(_START), end_ms=parse_date_ms(_END)
    )
    from backtest.wan271_reentry_book_band_stress import run_report

    _, book_rows = run_report(
        _SYMBOLS, timeframes=_TFS, lenses=("baseline",), start=_START, end=_END, jobs=1, log=False
    )
    assert len(book_rows) == len(expected)
    for got, exp in zip(book_rows, expected, strict=True):
        assert got.lens == "baseline"
        got_d = got.model_dump()
        del got_d["lens"]
        assert got_d == exp.model_dump()


# --------------------------------------------------------------------------- #
# 2. 무승부 / 잔존 순수 함수
# --------------------------------------------------------------------------- #


def _book(
    *,
    arm: str,
    lens: str,
    mdd: float,
    risk: float,
    trades: int,
    ret: float,
    liq: int = 0,
    scope: str = "all",
    segment: str = SEGMENT_OOS_WARM,
) -> BandStressBookRow:
    return BandStressBookRow(
        scope=scope,
        arm=arm,
        segment=segment,
        lens=lens,
        num_cells=27,
        num_symbols=9,
        num_trades=trades,
        win_rate=0.55,
        total_return=ret,
        max_drawdown=mdd,
        return_over_mdd=ret / mdd if mdd else None,
        peak_concurrency=11,
        max_concurrent_risk=risk,
        liquidation_events=liq,
    )


def test_residual_pct_basic_and_zero_guard() -> None:
    assert _residual_pct(0.5, 1.0) == "50%"
    assert _residual_pct(-0.2, -0.4) == "50%"
    assert _residual_pct(0.3, 0.0) == "—"
    assert _residual_pct(0.0, 1e-12) == "—"


def test_wash_holds_thresholds() -> None:
    small = _pair_delta(
        [
            _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.07, trades=100, ret=1.0),
            _book(arm="band", lens="baseline", mdd=0.205, risk=0.072, trades=101, ret=1.1),
        ],
        scope="all",
        segment=SEGMENT_OOS_WARM,
        lens="baseline",
        base_arm="freeze",
        arm="band",
    )
    assert small is not None and _wash_holds(small)  # +0.5%p ≤ 1%p, 청산 불변
    big = _pair_delta(
        [
            _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.07, trades=100, ret=1.0),
            _book(arm="band", lens="baseline", mdd=0.22, risk=0.07, trades=101, ret=1.1),
        ],
        scope="all",
        segment=SEGMENT_OOS_WARM,
        lens="baseline",
        base_arm="freeze",
        arm="band",
    )
    assert big is not None and not _wash_holds(big)  # +2.0%p > 1%p


def test_pair_delta_and_verdict_wash_holds() -> None:
    """baseline·pen_5bp 둘 다 band−freeze 무승부면 판정이 「유지된다」."""
    rows = [
        _book(arm="off", lens="baseline", mdd=0.18, risk=0.06, trades=90, ret=0.9),
        _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.07, trades=100, ret=1.0),
        _book(arm="band", lens="baseline", mdd=0.203, risk=0.071, trades=102, ret=1.1),
        _book(arm="off", lens="pen_5bp", mdd=0.18, risk=0.06, trades=85, ret=0.7),
        _book(arm="freeze", lens="pen_5bp", mdd=0.199, risk=0.069, trades=95, ret=0.8),
        _book(arm="band", lens="pen_5bp", mdd=0.204, risk=0.070, trades=96, ret=0.85),
    ]
    d = _pair_delta(
        rows, scope="all", segment=SEGMENT_OOS_WARM, lens="baseline", base_arm="freeze", arm="band"
    )
    assert d is not None
    assert abs(d.d_mdd - 0.003) < 1e-9
    text = verdict(rows, "all")
    assert "무승부가 pen_5bp에서도 유지된다" in text
    assert "복리 착시" in text


def test_verdict_wash_breaks_under_pen() -> None:
    """baseline 무승부인데 pen_5bp에서 1%p 문턱을 넘으면 「깨진다」."""
    rows = [
        _book(arm="off", lens="baseline", mdd=0.18, risk=0.06, trades=90, ret=0.9),
        _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.07, trades=100, ret=1.0),
        _book(arm="band", lens="baseline", mdd=0.203, risk=0.071, trades=102, ret=1.1),
        _book(arm="off", lens="pen_5bp", mdd=0.18, risk=0.06, trades=85, ret=0.7),
        _book(arm="freeze", lens="pen_5bp", mdd=0.19, risk=0.065, trades=95, ret=0.8),
        _book(arm="band", lens="pen_5bp", mdd=0.22, risk=0.070, trades=96, ret=0.85),
    ]
    assert "무승부가 pen_5bp에서 깨진다" in verdict(rows, "all")


def test_verdict_needs_both_lenses() -> None:
    rows = [
        _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.07, trades=100, ret=1.0),
        _book(arm="band", lens="baseline", mdd=0.20, risk=0.07, trades=100, ret=1.0),
    ]
    assert "판정 불가" in verdict(rows, "all")


def test_band_freeze_table_marks_wash() -> None:
    rows = [
        _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.07, trades=100, ret=1.0),
        _book(arm="band", lens="baseline", mdd=0.205, risk=0.071, trades=102, ret=1.1),
        _book(arm="freeze", lens="pen_5bp", mdd=0.19, risk=0.065, trades=95, ret=0.8),
        _book(arm="band", lens="pen_5bp", mdd=0.23, risk=0.08, trades=96, ret=0.85),
    ]
    table = "\n".join(_band_freeze_table(rows, "all", SEGMENT_OOS_WARM))
    assert "○" in table  # baseline 무승부
    assert "✕" in table  # pen_5bp 깨짐


def test_gain_residual_table_has_both_arms() -> None:
    rows = [
        _book(arm="off", lens="baseline", mdd=0.10, risk=0.05, trades=100, ret=1.0),
        _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.09, trades=140, ret=1.5),
        _book(arm="band", lens="baseline", mdd=0.22, risk=0.10, trades=150, ret=1.6),
        _book(arm="off", lens="pen_5bp", mdd=0.10, risk=0.05, trades=100, ret=0.8),
        _book(arm="freeze", lens="pen_5bp", mdd=0.15, risk=0.07, trades=120, ret=1.0),
        _book(arm="band", lens="pen_5bp", mdd=0.16, risk=0.075, trades=125, ret=1.05),
    ]
    table = "\n".join(_gain_residual_table(rows, "all", SEGMENT_OOS_WARM))
    assert "freeze" in table and "band" in table
    assert "기준" in table  # baseline 행이 기준
    assert "50%" in table  # off→freeze MDD Δ+10%p → pen +5%p = 50%


# --------------------------------------------------------------------------- #
# 3. 리포트 순수 함수 (CSV 왕복 · 병합 · 렌더)
# --------------------------------------------------------------------------- #


def _cell(*, tf: str, arm: str, lens: str, ret: float) -> BandStressCellRow:
    return BandStressCellRow(
        symbol="BTC/USDT:USDT",
        timeframe=tf,
        segment=SEGMENT_OOS_WARM,
        arm=arm,
        lens=lens,
        num_candidates=10,
        num_trades=8,
        win_rate=0.5,
        total_return=ret,
        max_drawdown=0.1,
    )


def test_cell_csv_roundtrip(tmp_path: Path) -> None:
    rows = [
        _cell(tf="1h", arm="freeze", lens="baseline", ret=0.1),
        _cell(tf="1h", arm="band", lens="pen_5bp", ret=0.2),
    ]
    path = tmp_path / "cells.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    back = cells_from_csv(path)
    assert [r.model_dump() for r in back] == [r.model_dump() for r in rows]


def test_book_csv_roundtrip_preserves_none_over_mdd(tmp_path: Path) -> None:
    row = _book(arm="off", lens="baseline", mdd=0.0, risk=0.0, trades=5, ret=0.1)
    path = tmp_path / "book.csv"
    book_to_frame([row]).to_csv(path, index=False)
    back = book_from_csv(path)
    assert back[0].return_over_mdd is None


def test_merge_replaces_same_tf_keeps_all() -> None:
    old_cells = [_cell(tf="1h", arm="off", lens="baseline", ret=0.1)]
    new_cells = [_cell(tf="15m", arm="off", lens="baseline", ret=0.3)]
    assert {r.timeframe for r in merge_cells(old_cells, new_cells)} == {"1h", "15m"}

    old_book = [
        _book(arm="off", lens="baseline", mdd=0.1, risk=0.05, trades=50, ret=0.1, scope="all"),
        _book(arm="off", lens="baseline", mdd=0.1, risk=0.05, trades=50, ret=0.1, scope="1h"),
    ]
    new_book = [
        _book(arm="off", lens="baseline", mdd=0.2, risk=0.06, trades=60, ret=0.2, scope="15m")
    ]
    merged = merge_book(old_book, new_book)
    # 15m append는 부분 TF라 `all`을 재계산 안 하므로 옛 `all`은 남는다.
    assert {r.scope for r in merged} == {"all", "1h", "15m"}


def test_summary_renders_from_synthetic_rows(tmp_path: Path) -> None:
    book_rows = [
        _book(arm="off", lens="baseline", mdd=0.18, risk=0.06, trades=100, ret=1.0),
        _book(arm="freeze", lens="baseline", mdd=0.20, risk=0.07, trades=140, ret=1.5),
        _book(arm="band", lens="baseline", mdd=0.205, risk=0.072, trades=145, ret=1.6),
        _book(arm="off", lens="pen_5bp", mdd=0.18, risk=0.06, trades=90, ret=0.8),
        _book(arm="freeze", lens="pen_5bp", mdd=0.19, risk=0.065, trades=120, ret=1.0),
        _book(arm="band", lens="pen_5bp", mdd=0.23, risk=0.08, trades=122, ret=1.05),
    ]
    book_rows += [r.model_copy(update={"segment": SEGMENT_OOS}) for r in book_rows]
    cell_rows = [
        _cell(tf="1h", arm="freeze", lens="baseline", ret=0.1),
        _cell(tf="1h", arm="band", lens="baseline", ret=0.2),
        _cell(tf="1h", arm="freeze", lens="pen_5bp", ret=0.05),
        _cell(tf="1h", arm="band", lens="pen_5bp", ret=0.06),
    ]
    md = build_summary_markdown(
        cell_rows, book_rows, cells_csv=tmp_path / "c.csv", book_csv=tmp_path / "b.csv"
    )
    assert "band−freeze 무승부" in md
    assert "pen_5bp" in md
    assert "복리 착시" in md
    assert "WAN-98" in md
    assert "재진입 off" in md and "재진입 band" in md
