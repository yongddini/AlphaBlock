"""WAN-169 리포트 모듈 테스트 — CSV 왕복 · 검산 세 갈래 · 격자 조립 · 판정."""

from __future__ import annotations

import math
from pathlib import Path

from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import (
    MIN_TRADES,
    MULTIPLES,
    BookRow,
    CellPayload,
    CellRow,
    build_book_rows,
    cells_from_csv,
    cells_to_frame,
    grid_from_csv,
    grid_to_frame,
    verdict,
    verify_cells,
)
from backtest.zone_limit_backtest import _Candidate


def _cand(entry_time: int, exit_time: int, *, trigger_time: int | None = None) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=101.5,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.0,
        trigger_time=entry_time if trigger_time is None else trigger_time,
    )


def _cell_row(
    symbol: str,
    timeframe: str,
    segment: str,
    *,
    total_return: float = 0.1,
    engine_total_return: float | None = None,
    engine_num_trades: int | None = None,
    num_trades: int = 30,
) -> CellRow:
    return CellRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        num_candidates=num_trades,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.05,
        engine_total_return=engine_total_return,
        engine_num_trades=engine_num_trades,
    )


def _book_row(
    *,
    scope: str = "both",
    arm: str = "book",
    segment: str = "oos_warm",
    multiple: float = 1.0,
    total_return: float = 0.1,
    max_drawdown: float = 0.05,
    liquidation_events: int | None = 0,
    num_trades: int = MIN_TRADES,
    exclude_symbol: str = "",
) -> BookRow:
    return BookRow(
        scope=scope,
        arm=arm,
        sizing_mode="risk_pct",
        multiple=multiple,
        segment=segment,
        exclude_symbol=exclude_symbol,
        num_cells=12,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=max_drawdown,
        peak_concurrency=3 if arm == "book" else None,
        max_concurrent_risk=0.05 if arm == "book" else None,
        max_open_notional_ratio=1.0 if arm == "book" else None,
        liquidation_events=liquidation_events if arm == "book" else None,
        clamped_entries=0 if arm == "book" else None,
        skipped_cell_busy=0 if arm == "book" else None,
        skipped_notional=0 if arm == "book" else None,
    )


# --------------------------------------------------------------------------- #
# CSV 왕복 — None 열이 NaN·빈 문자열로 둔갑하지 않는다
# --------------------------------------------------------------------------- #


def test_cell_rows_roundtrip_through_csv(tmp_path: Path) -> None:
    rows = [
        _cell_row("BTC/USDT:USDT", "1h", "full", engine_total_return=0.1, engine_num_trades=30),
        _cell_row("BTC/USDT:USDT", "1h", "oos_warm"),  # 엔진 검산 열이 None인 행.
    ]
    path = tmp_path / "cells.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    assert cells_from_csv(path) == rows


def test_book_rows_roundtrip_through_csv(tmp_path: Path) -> None:
    rows = [
        _book_row(),
        _book_row(arm="isolated"),  # 북 전용 열이 전부 None인 행.
        _book_row(exclude_symbol="ETH"),
    ]
    path = tmp_path / "grid.csv"
    grid_to_frame(rows).to_csv(path, index=False)
    assert grid_from_csv(path) == rows


# --------------------------------------------------------------------------- #
# 검산 세 갈래 (일치 · 잡음 · 불일치 — 조용한 통과 금지)
# --------------------------------------------------------------------------- #


def test_verify_cells_bitwise_match() -> None:
    rows = [_cell_row("BTC/USDT:USDT", "1h", "full", engine_total_return=0.1, engine_num_trades=30)]
    line, worst = verify_cells(rows)
    assert "일치" in line and worst == 0.0


def test_verify_cells_flags_return_mismatch() -> None:
    rows = [
        _cell_row(
            "BTC/USDT:USDT",
            "1h",
            "full",
            total_return=0.1,
            engine_total_return=0.2,
            engine_num_trades=30,
        )
    ]
    line, worst = verify_cells(rows)
    assert "불일치" in line and worst > 1e-12


def test_verify_cells_flags_trade_count_mismatch() -> None:
    rows = [
        _cell_row(
            "BTC/USDT:USDT",
            "1h",
            "full",
            engine_total_return=0.1,
            engine_num_trades=29,
            num_trades=30,
        )
    ]
    line, worst = verify_cells(rows)
    assert "불일치" in line and math.isinf(worst)


# --------------------------------------------------------------------------- #
# 격자 조립 — 따뜻한 구간의 straddle (b)가 북 입력에도 적용된다
# --------------------------------------------------------------------------- #


def _payload(symbol: str, timeframe: str, boundary: int, *, shift: int = 0) -> CellPayload:
    """칸 하나의 합성 payload. `shift`로 칸끼리 시간을 어긋나게 둔다 — 1배 명목 상한이
    동시 2칸을 막는 것(모델의 실제 성질)이 warm 필터 검증을 가리지 않게 하기 위해서다."""
    full = (
        _cand(1_000 + shift, 2_000 + shift),  # 워밍업(경계 전 탭) — oos_warm 북 제외 대상.
        _cand(boundary + 1_000 + shift, boundary + 2_000 + shift),
    )
    rows = tuple(_cell_row(symbol, timeframe, seg) for seg in ("full", "is", "oos_warm", "oos"))
    return CellPayload(
        symbol=symbol,
        timeframe=timeframe,
        boundary_ms=boundary,
        candidates={"full": full, "is": full[:1], "oos": full[1:]},
        funding={"full": (), "is": (), "oos": ()},
        rows=rows,
    )


def test_build_book_rows_filters_warm_candidates_by_cell_boundary() -> None:
    payloads = [
        _payload("BTC/USDT:USDT", "15m", 5_000),
        _payload("BTC/USDT:USDT", "1h", 5_000, shift=1_500),
    ]
    rows = build_book_rows(payloads)

    warm = [
        r
        for r in rows
        if r.scope == "both"
        and r.arm == "book"
        and r.segment == "oos_warm"
        and r.sizing_mode == "risk_pct"
        and r.multiple == 1.0
        and not r.exclude_symbol
    ]
    assert len(warm) == 1
    # 칸마다 워밍업 후보 1개가 걸러져 경계 이후 후보만 배치됐다(칸당 1개 × 2칸).
    assert warm[0].num_trades == 2

    full = [
        r
        for r in rows
        if r.scope == "both"
        and r.arm == "book"
        and r.segment == "full"
        and r.sizing_mode == "risk_pct"
        and r.multiple == 1.0
        and not r.exclude_symbol
    ]
    assert full[0].num_trades == 4  # full은 워밍업 후보까지 전부.

    excluded = [
        r for r in rows if r.scope == "both" and r.arm == "book" and r.exclude_symbol == "BTC"
    ]
    assert excluded and all(r.num_cells == 0 for r in excluded)  # 유일 종목 제외 → 빈 북.

    multiples = {
        r.multiple for r in rows if r.arm == "book" and r.scope == "both" and not r.exclude_symbol
    }
    assert multiples == set(MULTIPLES)


# --------------------------------------------------------------------------- #
# 판정 세 갈래
# --------------------------------------------------------------------------- #


def _judgment_rows(*, improve: bool | dict[str, bool]) -> list[BookRow]:
    """스코프·구간 전 셀을 채운 판정용 행 집합. `improve`가 dict면 스코프별로 가른다."""
    rows: list[BookRow] = []
    for scope in ("15m", "1h", "both"):
        gain = improve if isinstance(improve, bool) else improve[scope]
        for segment in ("oos_warm", "oos"):
            rows.append(_book_row(scope=scope, arm="isolated", segment=segment))
            for multiple in MULTIPLES:
                # 개선 팔: 배수에서 MDD가 수익보다 덜 늘어 수익/MDD 상승. 반대 팔: 더 늘어
                # 하락(둘 다 배수 1에서 스케일 1 — 기준점은 공유한다).
                mdd_scale = multiple**0.5 if gain else multiple**1.2
                rows.append(
                    _book_row(
                        scope=scope,
                        segment=segment,
                        multiple=multiple,
                        total_return=0.1 * multiple,
                        max_drawdown=0.05 * mdd_scale,
                    )
                )
    return rows


def test_verdict_a_when_risk_adjusted_improves_everywhere() -> None:
    assert verdict(_judgment_rows(improve=True)).startswith("**(a)")


def test_verdict_b_when_only_raw_return_improves() -> None:
    assert verdict(_judgment_rows(improve=False)).startswith("**(b)")


def test_verdict_c_when_scopes_disagree() -> None:
    text = verdict(_judgment_rows(improve={"15m": True, "1h": False, "both": False}))
    assert text.startswith("**(c)")


def test_verdict_ignores_cells_below_trade_gate() -> None:
    """표본 게이트 미달 셀은 판정에 못 들어간다 — 얇은 셀의 극단값이 판정을 흔들면 안 된다."""
    rows = _judgment_rows(improve=False)
    thin = [
        _book_row(
            scope="15m",
            segment="oos_warm",
            multiple=9.0,
            total_return=9.9,
            max_drawdown=0.01,
            num_trades=MIN_TRADES - 1,  # 게이트 미달 — 이 셀이 (a)를 만들면 안 된다.
        )
    ]
    assert verdict(rows + thin).startswith("**(b)")
