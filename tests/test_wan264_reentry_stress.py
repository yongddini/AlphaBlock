"""WAN-264 렌즈 × 비용 스트레스 배선·자(尺)를 동작으로 고정한다.

무거운 백테스트는 실데이터 게이트 뒤에서만 돈다(CI 기본 skip). 여기서 못 박는 것:

1. **렌즈 배선(옵트인)** — `run_cells(fill=None)`(기본) ≡ 예전 후보(비트 재현), `pen_5bp`는
   관통 요구로 후보를 **줄인다**(라벨만 붙는 실패 방지).
2. **비용 배선(옵트인)** — `build_book_rows(fee_rate=None,...)`(기본) ≡ 채택 비용(비트 재현),
   테이커 5bp는 청산 비용을 올려 북 수익을 **바꾼다**(후보·거래 수는 불변 — 비용은 후보 무관).
3. **잔존율 순수 함수** — 기준(baseline·base) 대비 델타 비율, 분모 0 가드, 반올림.
4. **리포트 순수 함수** — CSV 왕복 · 병합 · 렌더 · 판정.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest import book_cli, harness
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    load_market_data,
    normalize_symbol,
)
from backtest.run import ADOPTED_BOOK
from backtest.wan169_leverage_book import run_cells
from backtest.wan264_reentry_book_stress import (
    BookStressRow,
    CellStressRow,
    _delta,
    _residual_pct,
    _residual_table,
    book_from_csv,
    book_to_frame,
    build_summary_markdown,
    cells_from_csv,
    cells_to_frame,
    cost_preset,
    merge_book,
    merge_cells,
    verdict,
)

_START = "2024-01-01"
_END = "2024-04-01"
_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
_TFS = ["1h"]


def _require_real_data() -> None:
    market = load_market_data(
        normalize_symbol("BTCUSDT"), "1h", start_ms=None, end_ms=None, need_1m=True
    )
    if market.empty:
        pytest.skip("BTCUSDT 1h 실데이터가 없어 WAN-264 스트레스 검산을 건너뜁니다(CI 기본).")


# --------------------------------------------------------------------------- #
# 1. 렌즈 배선 (옵트인)
# --------------------------------------------------------------------------- #


def test_fill_none_reproduces_default_candidates() -> None:
    """`run_cells(fill=None)`(기본) ≡ `run_cells()` — 렌즈 축이 비트 재현(WAN-264 완료기준)."""
    _require_real_data()
    kw = dict(start=_START, end=_END, jobs=1, reentry=True)
    default = run_cells(_SYMBOLS, _TFS, **kw)  # type: ignore[arg-type]
    explicit = run_cells(_SYMBOLS, _TFS, fill=None, **kw)  # type: ignore[arg-type]
    assert len(default) == len(explicit)
    for a, b in zip(default, explicit, strict=True):
        for seg in (SEGMENT_FULL, SEGMENT_OOS):
            assert len(a.candidates[seg]) == len(b.candidates[seg])


def test_pen_5bp_flows_into_candidates() -> None:
    """관통 5bp 요구가 후보 생성에 실제로 흐른다 — 라벨만 붙는 실패 방지(WAN-95/112/123 부류).

    관통은 스치듯 닿은 체결을 **빼거나**(후보 수↓) 남은 체결을 **더 깊은 진입가로 미룬다**
    (진입가 이동). 작은 창에선 후자가 흔하므로 둘 중 하나가 반드시 성립함을 못 박는다:
    (a) 후보 수는 결코 늘지 않고, (b) 엔진 출력(진입가 열)이 실제로 달라진다.
    """
    _require_real_data()
    kw = dict(start=_START, end=_END, jobs=1, reentry=True)
    base = run_cells(_SYMBOLS, _TFS, **kw)  # type: ignore[arg-type]
    pen = run_cells(_SYMBOLS, _TFS, fill=harness.fill_preset("pen_5bp"), **kw)  # type: ignore[arg-type]
    base_prices = [tuple(round(c.entry_price, 6) for c in p.candidates[SEGMENT_FULL]) for p in base]
    pen_prices = [tuple(round(c.entry_price, 6) for c in p.candidates[SEGMENT_FULL]) for p in pen]
    # (a) 관통 요구는 후보를 늘리지 않는다(스치듯 닿은 체결이 빠진다).
    for b, p in zip(base, pen, strict=True):
        assert len(p.candidates[SEGMENT_FULL]) <= len(b.candidates[SEGMENT_FULL])
    # (b) 후보 집합 또는 진입가가 실제로 달라진다(렌즈가 죽지 않았다).
    assert base_prices != pen_prices


# --------------------------------------------------------------------------- #
# 2. 비용 배선 (옵트인)
# --------------------------------------------------------------------------- #


def test_cost_none_reproduces_default_book() -> None:
    """`build_book_rows(fee_rate=None,...)`(기본) ≡ 채택 비용 — 비용 축이 비트 재현."""
    _require_real_data()
    payloads = run_cells(_SYMBOLS, _TFS, start=_START, end=_END, jobs=1, reentry=True)
    kw = dict(
        book=ADOPTED_BOOK, segments=[SEGMENT_FULL], start_ms=0, end_ms=0, include_reentry=True
    )
    default = book_cli.build_book_rows(payloads, **kw)  # type: ignore[arg-type]
    explicit = book_cli.build_book_rows(
        payloads,
        fee_rate=None,
        maker_fee_rate=None,
        slippage=None,
        **kw,  # type: ignore[arg-type]
    )
    for a, b in zip(default, explicit, strict=True):
        assert a.model_dump() == b.model_dump()


def test_taker5_changes_pnl_not_trade_count() -> None:
    """테이커 5bp는 청산 비용을 올려 북 수익을 바꾸지만 거래 수는 불변(비용은 후보 무관)."""
    _require_real_data()
    payloads = run_cells(_SYMBOLS, _TFS, start=_START, end=_END, jobs=1, reentry=True)
    kw = dict(
        book=ADOPTED_BOOK, segments=[SEGMENT_FULL], start_ms=0, end_ms=0, include_reentry=True
    )
    base = book_cli.build_book_rows(payloads, **kw)[0]  # type: ignore[arg-type]
    taker5 = book_cli.build_book_rows(payloads, fee_rate=0.0005, **kw)[0]  # type: ignore[arg-type]
    assert taker5.num_trades == base.num_trades  # 후보 집합 불변 → 거래 수 불변
    assert taker5.total_return != base.total_return  # 청산 비용↑ → 수익↓


# --------------------------------------------------------------------------- #
# 3. 잔존율 순수 함수
# --------------------------------------------------------------------------- #


def test_residual_pct_basic_and_zero_guard() -> None:
    assert _residual_pct(0.5, 1.0) == "50%"
    assert _residual_pct(-0.2, -0.4) == "50%"
    assert _residual_pct(0.3, 0.0) == "—"  # 분모 0 → 무의미
    assert _residual_pct(0.0, 1e-12) == "—"  # eps 아래도 무의미


def _book_row(
    *,
    arm: str,
    lens: str,
    cost: str,
    mdd: float,
    risk: float,
    trades: int,
    ret: float,
    liq: int = 0,
) -> BookStressRow:
    return BookStressRow(
        scope="all",
        arm=arm,
        segment=SEGMENT_OOS_WARM,
        lens=lens,
        cost=cost,
        num_cells=6,
        num_symbols=6,
        num_trades=trades,
        win_rate=0.5,
        total_return=ret,
        max_drawdown=mdd,
        return_over_mdd=None,
        peak_concurrency=2,
        max_concurrent_risk=risk,
        liquidation_events=liq,
    )


def test_delta_and_residual_table() -> None:
    """기준(baseline·base) 델타가 100%이고, pen_5bp가 절반 남으면 50%로 찍힌다."""
    rows = [
        # 기준: off MDD 10% → on 20% (Δ+10%p), 리스크 5%→9%(Δ+4%p), 거래 100→140
        _book_row(
            arm="off", lens="baseline", cost="base", mdd=0.10, risk=0.05, trades=100, ret=1.0
        ),
        _book_row(arm="on", lens="baseline", cost="base", mdd=0.20, risk=0.09, trades=140, ret=1.5),
        # pen_5bp: MDD Δ+5%p(기준의 절반) · 리스크 Δ+2%p(절반) · 거래 100→120
        _book_row(arm="off", lens="pen_5bp", cost="base", mdd=0.10, risk=0.05, trades=100, ret=0.8),
        _book_row(arm="on", lens="pen_5bp", cost="base", mdd=0.15, risk=0.07, trades=120, ret=1.0),
    ]
    ref = _delta(rows, scope="all", segment=SEGMENT_OOS_WARM, lens="baseline", cost="base")
    assert ref is not None
    assert abs(ref.d_mdd - 0.10) < 1e-12
    assert ref.on_trades - ref.off_trades == 40
    pen = _delta(rows, scope="all", segment=SEGMENT_OOS_WARM, lens="pen_5bp", cost="base")
    assert pen is not None
    assert _residual_pct(pen.d_mdd, ref.d_mdd) == "50%"
    assert _residual_pct(pen.d_risk, ref.d_risk) == "50%"
    table = "\n".join(_residual_table(rows, "all", SEGMENT_OOS_WARM))
    assert "기준" in table  # 기준 행이 라벨을 단다
    assert "50%" in table  # pen_5bp 잔존율
    v = verdict(rows, "all")
    assert "pen_5bp" in v
    assert "복리 착시" in v


# --------------------------------------------------------------------------- #
# 4. 리포트 순수 함수 (CSV 왕복 · 병합 · 렌더)
# --------------------------------------------------------------------------- #


def _cell_row(*, tf: str, arm: str, lens: str, cost: str, ret: float) -> CellStressRow:
    return CellStressRow(
        symbol="BTCUSDT",
        timeframe=tf,
        segment=SEGMENT_OOS_WARM,
        arm=arm,
        lens=lens,
        cost=cost,
        num_candidates=10,
        num_trades=8,
        win_rate=0.5,
        total_return=ret,
        max_drawdown=0.1,
    )


def test_cell_csv_roundtrip(tmp_path: Path) -> None:
    rows = [
        _cell_row(tf="1h", arm="off", lens="baseline", cost="base", ret=0.1),
        _cell_row(tf="1h", arm="on", lens="pen_5bp", cost="taker5", ret=0.2),
    ]
    path = tmp_path / "cells.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    back = cells_from_csv(path)
    assert [r.model_dump() for r in back] == [r.model_dump() for r in rows]


def test_book_csv_roundtrip_preserves_none_over_mdd(tmp_path: Path) -> None:
    row = _book_row(arm="off", lens="baseline", cost="base", mdd=0.0, risk=0.0, trades=5, ret=0.1)
    path = tmp_path / "book.csv"
    book_to_frame([row]).to_csv(path, index=False)
    back = book_from_csv(path)
    assert back[0].return_over_mdd is None  # MDD 0 → NaN이 아니라 None으로 되살아난다


def test_merge_replaces_same_tf_keeps_all_rule() -> None:
    old_cells = [_cell_row(tf="1h", arm="off", lens="baseline", cost="base", ret=0.1)]
    new_cells = [_cell_row(tf="15m", arm="off", lens="baseline", cost="base", ret=0.3)]
    merged = merge_cells(old_cells, new_cells)
    assert {r.timeframe for r in merged} == {"1h", "15m"}

    old_book = [
        _book_row(arm="off", lens="baseline", cost="base", mdd=0.1, risk=0.05, trades=50, ret=0.1),
    ]
    old_book[0] = old_book[0].model_copy(update={"scope": "all"})
    new_book = [
        _book_row(arm="off", lens="baseline", cost="base", mdd=0.2, risk=0.06, trades=60, ret=0.2)
    ]
    new_book[0] = new_book[0].model_copy(update={"scope": "15m"})
    merged_book = merge_book(old_book, new_book)
    # 15m append는 부분 TF라 `all`을 재계산 안 하므로 옛 `all`은 남는다(has_new_all=False).
    assert {r.scope for r in merged_book} == {"all", "15m"}


def test_summary_renders_from_synthetic_rows() -> None:
    """렌더가 렌즈 × 비용 격자에서 KeyError 없이 도는지(주 스코프 = all)."""
    book_rows = [
        _book_row(
            arm="off", lens="baseline", cost="base", mdd=0.10, risk=0.05, trades=100, ret=1.0
        ),
        _book_row(arm="on", lens="baseline", cost="base", mdd=0.20, risk=0.09, trades=140, ret=1.5),
        _book_row(arm="off", lens="pen_5bp", cost="base", mdd=0.10, risk=0.05, trades=100, ret=0.8),
        _book_row(arm="on", lens="pen_5bp", cost="base", mdd=0.15, risk=0.07, trades=120, ret=1.0),
    ]
    # oos 구간 행도 있어야 렌더가 완결(같은 값 재사용).
    book_rows += [r.model_copy(update={"segment": SEGMENT_OOS}) for r in book_rows]
    cell_rows = [
        _cell_row(tf="1h", arm="off", lens="baseline", cost="base", ret=0.1),
        _cell_row(tf="1h", arm="on", lens="baseline", cost="base", ret=0.2),
        _cell_row(tf="1h", arm="off", lens="pen_5bp", cost="base", ret=0.05),
        _cell_row(tf="1h", arm="on", lens="pen_5bp", cost="base", ret=0.08),
    ]
    md = build_summary_markdown(
        cell_rows, book_rows, cells_csv=Path("c.csv"), book_csv=Path("b.csv")
    )
    assert "잔존 표" in md
    assert "pen_5bp" in md
    assert "복리 착시" in md
    assert "WAN-98" in md


def test_cost_preset_unknown_rejected() -> None:
    with pytest.raises(ValueError, match="알 수 없는 비용 프리셋"):
        cost_preset("nope")
