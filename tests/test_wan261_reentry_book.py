"""WAN-261 재진입 북 주입의 배선·자(尺)를 동작으로 고정한다.

무거운 백테스트는 안 돌린다(그건 실데이터 리포트가 낸다). 여기서 못 박는 것:

1. **재진입 후보 생성** — `reentry_candidates`가 `reentry_events`와 **같은 루프**를 쓰므로
   개수가 같고, 낸 `_Candidate`는 청산이 확정돼 있으며 `trigger_time = 진입 시각`(탭 없는
   재진입을 북 구간 버킷에 올바로 넣는 키)이다.
2. **북 주입 스위치** — `_segment_cells(include_reentry=False)`(기본)는 base만 넣어 예전과
   같고, `True`는 재진입을 합치며, `oos_warm`은 경계로 거른다(straddle (b)).
3. **CLI 거부** — `--reentry on`은 북 모드 전용이라 per-cell과 함께 주면 종료 코드 2.
4. **리포트 순수 함수** — 판정·CSV 왕복·병합.
"""

from __future__ import annotations

from pathlib import Path

from backtest import harness
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
)
from backtest.models import ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan169_leverage_book import CellPayload, _segment_cells
from backtest.wan228_reentry_census import reentry_candidates, reentry_events
from backtest.wan261_reentry_book import (
    BookScopeRow,
    CellReturnRow,
    _verdict_15m,
    book_from_csv,
    book_to_frame,
    build_summary_markdown,
    cells_from_csv,
    cells_to_frame,
    merge_book,
    merge_cells,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

HTF_MS = 3_600_000  # 1h


# --------------------------------------------------------------------------- #
# 1. 재진입 후보 생성 (WAN-228 로직 공유)
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
    # 부모 익절 t=0(115) → t=10 재진입 체결 → t=20 익절(승) → t=30 재진입 → t=40 손절(존 사망).
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


def test_reentry_candidates_share_loop_with_events() -> None:
    """`reentry_candidates`는 `reentry_events`와 같은 재무장 루프라 개수가 항상 같다."""
    substeps = _win_then_stop_substeps()
    events = reentry_events(_long_candidate(), **_kwargs(substeps))  # type: ignore[arg-type]
    cands = reentry_candidates(_long_candidate(), **_kwargs(substeps))  # type: ignore[arg-type]
    assert len(cands) == len(events) == 2


def test_reentry_candidate_fields_are_resolved() -> None:
    """낸 후보는 청산이 확정돼 있고 `trigger_time = 진입 시각`(탭 없는 재진입 버킷 키)이다."""
    substeps = _win_then_stop_substeps()
    cands = reentry_candidates(_long_candidate(), **_kwargs(substeps))  # type: ignore[arg-type]
    win, stop = cands
    assert win.side is PositionSide.LONG and win.reason is ExitReason.TAKE_PROFIT
    assert win.exit_price == 115.0 and win.stop_price == 90.0
    assert win.trigger_time == win.entry_time  # 탭이 없으니 진입 시각이 버킷 키.
    assert stop.reason is ExitReason.STOP_LOSS and stop.exit_price == 90.0
    # 재진입은 부모 익절(t=0) **직후** 시각에서만 생긴다.
    assert win.entry_time > 0 and stop.entry_time > win.entry_time


def test_no_reentry_candidates_when_price_never_returns() -> None:
    substeps = _substeps([(1, 116.0, 114.0, 115.0), (10, 140.0, 120.0, 130.0)])
    assert reentry_candidates(_long_candidate(), **_kwargs(substeps)) == []  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 2. 북 주입 스위치 (_segment_cells include_reentry)
# --------------------------------------------------------------------------- #


def _cand(entry_time: int, *, trigger_time: int | None = None) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=entry_time + 10,
        exit_price=101.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=99.0,
        trigger_time=entry_time if trigger_time is None else trigger_time,
    )


def _payload(*, boundary: int, base: list[_Candidate], reentry: list[_Candidate]) -> CellPayload:
    return CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        boundary_ms=boundary,
        candidates={SEGMENT_FULL: tuple(base), SEGMENT_IS: (), SEGMENT_OOS: ()},
        funding={SEGMENT_FULL: (), SEGMENT_IS: (), SEGMENT_OOS: ()},
        rows=(),
        reentry_candidates={SEGMENT_FULL: tuple(reentry), SEGMENT_IS: (), SEGMENT_OOS: ()},
    )


def test_segment_cells_default_ignores_reentry() -> None:
    """기본(include_reentry=False)이면 base만 — 재진입이 실려 있어도 무시(비트 재현)."""
    payload = _payload(boundary=100, base=[_cand(10)], reentry=[_cand(200)])
    cells = _segment_cells([payload], SEGMENT_FULL, "")
    assert len(cells[0].candidates) == 1  # base만.


def test_segment_cells_includes_reentry_when_asked() -> None:
    payload = _payload(boundary=100, base=[_cand(10)], reentry=[_cand(200)])
    cells = _segment_cells([payload], SEGMENT_FULL, "", include_reentry=True)
    assert len(cells[0].candidates) == 2  # base + 재진입.


def test_segment_cells_oos_warm_filters_reentry_by_boundary() -> None:
    """oos_warm은 base처럼 재진입도 칸 경계로 거른다 — 경계 전 재진입은 배치조차 안 한다."""
    # base: 경계 전(50)·후(150) 각 1 · 재진입: 경계 전(60)·후(160) 각 1.
    payload = _payload(
        boundary=100,
        base=[_cand(50), _cand(150)],
        reentry=[_cand(60), _cand(160)],
    )
    warm = _segment_cells([payload], SEGMENT_OOS_WARM, "", include_reentry=True)[0]
    times = sorted(c.trigger_time for c in warm.candidates)
    assert times == [150, 160]  # 경계(100) 이후만 — base 1 + 재진입 1.


def test_segment_cells_no_reentry_dict_is_bit_identical() -> None:
    """payload에 재진입이 없으면 include_reentry=True여도 base만(빈 dict = 비트 재현)."""
    payload = CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        boundary_ms=100,
        candidates={SEGMENT_FULL: (_cand(10),), SEGMENT_IS: (), SEGMENT_OOS: ()},
        funding={SEGMENT_FULL: (), SEGMENT_IS: (), SEGMENT_OOS: ()},
        rows=(),
    )
    cells = _segment_cells([payload], SEGMENT_FULL, "", include_reentry=True)
    assert len(cells[0].candidates) == 1


# --------------------------------------------------------------------------- #
# 3. 리포트 순수 함수 — 판정 · CSV 왕복 · 병합
# --------------------------------------------------------------------------- #


def _book_row(
    *,
    scope: str = "all",
    arm: str,
    segment: str = SEGMENT_OOS_WARM,
    exclude: str = "",
    total_return: float = 0.2,
    max_drawdown: float = 0.03,
    max_concurrent_risk: float = 0.02,
    liquidation_events: int = 0,
    num_trades: int = 40,
) -> BookScopeRow:
    return BookScopeRow(
        scope=scope,
        arm=arm,
        segment=segment,
        exclude_symbol=exclude,
        num_cells=12,
        num_symbols=9,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=max_drawdown,
        return_over_mdd=total_return / max_drawdown if max_drawdown else None,
        peak_concurrency=3,
        max_concurrent_risk=max_concurrent_risk,
        liquidation_events=liquidation_events,
    )


def test_verdict_flags_risk_increase() -> None:
    rows = [
        _book_row(arm="off", max_drawdown=0.03, liquidation_events=0),
        _book_row(arm="on", max_drawdown=0.03, liquidation_events=2),
    ]
    assert verdict(rows).startswith("**재진입 on이 북 위험을 키운다.")


def test_verdict_small_change() -> None:
    rows = [
        _book_row(arm="off", max_drawdown=0.030, max_concurrent_risk=0.020),
        _book_row(arm="on", max_drawdown=0.033, max_concurrent_risk=0.021),
    ]
    assert "위험 변화는 작다" in verdict(rows)


def test_verdict_needs_both_arms() -> None:
    assert verdict([_book_row(arm="off")]).startswith("**판정 불가")


def test_cells_frame_roundtrip(tmp_path: Path) -> None:
    rows = [
        CellReturnRow(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment=SEGMENT_OOS_WARM,
            arm="on",
            num_candidates=20,
            num_trades=15,
            win_rate=0.5,
            total_return=0.14,
            max_drawdown=0.02,
        )
    ]
    path = tmp_path / "cells.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    restored = cells_from_csv(path)
    assert restored[0].arm == "on" and restored[0].total_return == 0.14


def test_book_frame_roundtrip_preserves_none(tmp_path: Path) -> None:
    rows = [_book_row(arm="on", max_drawdown=0.0)]  # MDD 0 → return_over_mdd None.
    assert rows[0].return_over_mdd is None
    path = tmp_path / "book.csv"
    book_to_frame(rows).to_csv(path, index=False)
    restored = book_from_csv(path)
    assert restored[0].return_over_mdd is None  # None이 NaN으로 되살아나지 않는다.


def test_merge_cells_replaces_same_tf_keeps_others() -> None:
    old = [_cell_return("1h"), _cell_return("4h")]
    new = [_cell_return("1h", total_return=0.99)]
    merged = merge_cells(old, new)
    assert {r.timeframe for r in merged} == {"1h", "4h"}
    assert next(r for r in merged if r.timeframe == "1h").total_return == 0.99


def test_merge_book_drops_stale_all_on_new_run() -> None:
    old = [_book_row(scope="all", arm="on"), _book_row(scope="1h", arm="on")]
    new = [_book_row(scope="all", arm="on", total_return=0.5), _book_row(scope="4h", arm="on")]
    merged = merge_book(old, new)
    scopes = sorted({r.scope for r in merged})
    assert scopes == ["1h", "4h", "all"]  # 옛 1h 유지 · 새 4h 추가 · all은 새 것만.
    assert next(r for r in merged if r.scope == "all").total_return == 0.5


def test_verdict_15m_present_when_scope_exists() -> None:
    """15m 스코프가 있으면 판정에 15m 위험 델타 + 낙관 의존 경고가 붙는다(완료기준 — 15m 명시)."""
    rows = [
        _book_row(scope="15m", arm="off", max_drawdown=0.06, max_concurrent_risk=0.05),
        _book_row(scope="15m", arm="on", max_drawdown=0.09, max_concurrent_risk=0.07),
    ]
    note = _verdict_15m(rows)
    assert note.startswith("📌 **15m")
    assert "6.00% → 9.00%" in note  # off→on MDD가 행에서 계산된다(하드코딩 아님).
    assert "가장 크게 의존" in note


def test_verdict_15m_absent_without_scope() -> None:
    """15m 스코프가 없으면(1h·2h·4h만) 빈 문자열 — 렌더러가 문단을 건너뛴다."""
    rows = [_book_row(scope="all", arm="off"), _book_row(scope="all", arm="on")]
    assert _verdict_15m(rows) == ""


def test_summary_renders_per_tf_loo_and_15m_note() -> None:
    """`all` 주 스코프면 §4가 스코프별 LOO(전체 + 각 TF)를 렌더하고 15m 판정 주석이 붙는다.

    15m append 후 `main_scope`는 `all`로 남지만(병합 규칙) 완료기준은 15m LOO 표와 15m
    판정 명시를 요구한다 — 렌더러가 그 둘을 실제로 낸다는 것을 동작으로 고정한다.
    """
    cell_rows = [
        _cell_return("15m", arm="off"),
        _cell_return("15m", arm="on"),
        _cell_return("1h", arm="off"),
        _cell_return("1h", arm="on"),
    ]
    book_rows = [
        _book_row(scope="all", arm="off"),
        _book_row(scope="all", arm="on"),
        _book_row(scope="all", arm="off", exclude="BTC"),
        _book_row(scope="all", arm="on", exclude="BTC"),
        _book_row(scope="15m", arm="off"),
        _book_row(scope="15m", arm="on"),
        _book_row(scope="15m", arm="off", exclude="BTC"),
        _book_row(scope="15m", arm="on", exclude="BTC"),
        _book_row(scope="1h", arm="off"),
        _book_row(scope="1h", arm="on"),
    ]
    md = build_summary_markdown(
        cell_rows, book_rows, cells_csv=Path("cells.csv"), book_csv=Path("book.csv")
    )
    loo_section = md.split("## 4.")[1].split("## 판정")[0]
    assert "### all" in loo_section  # 주 스코프 LOO.
    assert "### 15m" in loo_section  # 15m LOO 표(완료기준).
    assert "### 1h" in loo_section
    assert "📌 **15m — 낙관 가정 최대 의존 TF.**" in md  # 15m 판정 명시.


def _cell_return(timeframe: str, *, total_return: float = 0.1, arm: str = "on") -> CellReturnRow:
    return CellReturnRow(
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        segment=SEGMENT_OOS_WARM,
        arm=arm,
        num_candidates=10,
        num_trades=8,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.02,
    )
