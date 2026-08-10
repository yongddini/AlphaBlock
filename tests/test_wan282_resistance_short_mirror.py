"""WAN-282 저항-존 숏 거울의 자(尺)를 동작으로 고정한다 (완료기준 3).

지키는 것:

1. **거울 = 엔진 대칭** — `short_enabled=True`가 베어리시 OB 숏을 낳고, 그 숏의 손절이 존
   **원단**(top·무효화 위)이며 롱의 손절이 존 원단(bottom·무효화 아래)인 정확한 좌우 반전이다.
   숏 게이트는 **롱을 건드리지 않는다**(short off/on에서 롱 후보 비트 동일).
2. **wan169 옵트인 배선** — `short_enabled` 기본값 False(비트 재현)이고, 켜면 `params`에 실린다.
3. **격리 숏 필터** — 롱+숏 셀에서 숏만 방향으로 거른다(oos_warm은 경계 필터).
4. **헤지 창 산수** — 최대 낙폭 창·창 수익률이 곡선에서 올바로 나온다.
5. **판정·프레임 왕복** — 판정이 행에서 계산되고 CSV가 값을 보존한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest import book_cli, harness
from backtest.models import BacktestConfig, EquityPoint, ExitReason, PositionSide
from backtest.run import ADOPTED_BOOK, parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan169_leverage_book import CellPayload, _Task
from backtest.wan282_resistance_short_mirror import (
    ARM_LONG_ONLY,
    ARM_LONG_SHORT,
    BookScopeRow,
    HedgeRow,
    ShortDiagRow,
    _max_drawdown_window,
    _net_r,
    _short_candidates,
    _window_return,
    book_from_csv,
    book_to_frame,
    describe_engine,
    diag_from_csv,
    diag_to_frame,
    hedge_from_csv,
    hedge_to_frame,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[object, object]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


_REFLECT_M = 100_000.0


def _reflect(df: pd.DataFrame) -> pd.DataFrame:
    """가격을 수평선 M에 대칭시킨다(high↔low 스왑) — 베어리시 OB를 불리시 OB로 뒤집는다.

    p' = 2M − p 라 하락 임펄스가 상승 임펄스가 되고, 그 자리의 저항-존 숏이 지지-존 롱이 된다.
    OHLC 유효성(high≥low) 보존을 위해 high'=2M−low, low'=2M−high로 스왑한다.
    """
    out = df.copy()
    out["open"] = 2 * _REFLECT_M - df["open"]
    out["close"] = 2 * _REFLECT_M - df["close"]
    out["high"] = 2 * _REFLECT_M - df["low"]
    out["low"] = 2 * _REFLECT_M - df["high"]
    return out


def _candidates(*, short_enabled: bool, offset_bps: float | None = None) -> list[_Candidate]:
    """볼린저 끔(결정론) · 좁은-존 필터 끔 — 기하만 재는 순수 배선 검증."""
    htf, one_min = _synthetic_pair()
    update: dict[str, object] = dict(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        short_enabled=short_enabled,
        deviation_filter=None,
        max_zone_width_atr=None,
    )
    if offset_bps is not None:
        update["zone_limit_offset_bps"] = offset_bps
    params = ConfluenceParams(**update)
    cands, _stats = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=BacktestConfig()
    )
    return cands


# --------------------------------------------------------------------------- #
# 1. 거울 = 엔진 대칭
# --------------------------------------------------------------------------- #


def test_short_enabled_off_yields_no_shorts() -> None:
    cands = _candidates(short_enabled=False)
    assert all(c.side is PositionSide.LONG for c in cands)


def test_short_enabled_on_adds_bearish_ob_shorts() -> None:
    cands = _candidates(short_enabled=True)
    shorts = [c for c in cands if c.side is PositionSide.SHORT]
    assert shorts, "이 합성 시드는 숏 셋업을 내야 한다(엔진 대칭 검증 불가면 시드 확인)"
    for c in shorts:
        assert c.order_block is not None
        assert c.order_block.direction is OrderBlockDirection.BEARISH


def test_short_gate_does_not_touch_longs() -> None:
    """숏을 켜도 롱 후보는 비트 단위로 같다 — 숏 게이트가 롱을 건드리지 않는다."""
    longs_off = [c for c in _candidates(short_enabled=False) if c.side is PositionSide.LONG]
    longs_on = [c for c in _candidates(short_enabled=True) if c.side is PositionSide.LONG]
    assert len(longs_off) == len(longs_on)
    for a, b in zip(longs_off, longs_on, strict=True):
        assert a.entry_price == b.entry_price
        assert a.stop_price == b.stop_price
        assert a.entry_time == b.entry_time
        assert a.exit_price == b.exit_price


def test_short_stop_is_distal_boundary_above() -> None:
    """거울의 핵심(숏 쪽): 손절 = 존 원단 = 무효화 위(`ob.top`)."""
    shorts = [c for c in _candidates(short_enabled=True) if c.side is PositionSide.SHORT]
    assert shorts
    for c in shorts:
        assert c.order_block is not None
        assert c.stop_price == c.order_block.top


def test_short_is_exact_mirror_of_reflected_long() -> None:
    """저항-존 숏 = 가격 대칭 후의 지지-존 롱과 정확한 거울(오프셋 0에서 진입·손절 동시 일치).

    가격을 수평선 M에 대칭시키면 베어리시 OB 숏이 불리시 OB 롱이 된다. 오프셋을 끄면(0bp,
    가격비례 성분 제거) 진입가·손절가가 `2M − x`로 정확히 뒤집힌다 — 새 규칙 없이 방향만
    반전한 진짜 거울임을 고정한다. 손절은 원단(숏=위·롱=아래) 대칭이다.
    """
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        short_enabled=True,
        deviation_filter=None,
        max_zone_width_atr=None,
        zone_limit_offset_bps=0.0,
    )
    orig, _ = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=BacktestConfig())
    refl, _ = build_zone_limit_candidates(
        _reflect(htf), _reflect(one_min), "1h", params=params, cfg=BacktestConfig()
    )
    shorts = [c for c in orig if c.side is PositionSide.SHORT]
    longs = [c for c in refl if c.side is PositionSide.LONG]
    assert shorts and len(shorts) == len(longs)
    for short_c, long_c in zip(shorts, longs, strict=True):
        assert short_c.order_block is not None and long_c.order_block is not None
        assert short_c.stop_price == short_c.order_block.top  # 숏 원단 = 위
        assert long_c.stop_price == long_c.order_block.bottom  # 롱 원단 = 아래
        assert abs((2 * _REFLECT_M - short_c.entry_price) - long_c.entry_price) < 1e-6
        assert abs((2 * _REFLECT_M - short_c.stop_price) - long_c.stop_price) < 1e-6


def test_short_offset_widens_1r_below_zone() -> None:
    """숏 진입은 근단(존 하단) 이하(체결 쉬운 쪽=아래)이고 손절은 존 위 → 1R > 0."""
    shorts = [c for c in _candidates(short_enabled=True) if c.side is PositionSide.SHORT]
    for c in shorts:
        assert c.order_block is not None
        assert c.entry_price <= c.order_block.bottom  # 근단 하단 + 오프셋(아래)
        assert c.stop_price == c.order_block.top
        assert c.stop_price - c.entry_price > 0  # 1R 양수


# --------------------------------------------------------------------------- #
# 2. wan169 옵트인 배선
# --------------------------------------------------------------------------- #


def test_task_short_enabled_defaults_false() -> None:
    task = _Task(symbol="BTC/USDT", timeframe="1h", start_ms=0, end_ms=1)
    assert task.short_enabled is False


def test_run_cells_signature_has_short_enabled() -> None:
    import inspect

    from backtest.wan169_leverage_book import run_cells

    assert "short_enabled" in inspect.signature(run_cells).parameters


# --------------------------------------------------------------------------- #
# 3. 격리 숏 필터
# --------------------------------------------------------------------------- #


def _ob(direction: OrderBlockDirection) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=105.0,
        bottom=95.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=1.5,
        break_time=None,
    )


def _cand(side: PositionSide, *, trigger_time: int, reason: ExitReason) -> _Candidate:
    ob = _ob(
        OrderBlockDirection.BULLISH if side is PositionSide.LONG else OrderBlockDirection.BEARISH
    )
    return _Candidate(
        side=side,
        entry_time=trigger_time,
        entry_price=95.0 if side is PositionSide.SHORT else 105.0,
        exit_time=trigger_time + 1,
        exit_price=90.0,
        reason=reason,
        stop_price=105.0 if side is PositionSide.SHORT else 95.0,
        order_block=ob,
        trigger_time=trigger_time,
    )


def _payload(base: list[_Candidate], reentry: list[_Candidate], boundary: int) -> CellPayload:
    return CellPayload(
        symbol="BTC/USDT",
        timeframe="1h",
        boundary_ms=boundary,
        candidates={"full": tuple(base), "is": (), "oos": ()},
        funding={"full": (), "is": (), "oos": ()},
        rows=(),
        reentry_candidates={"full": tuple(reentry), "is": (), "oos": ()},
    )


def test_short_candidates_filters_longs_out() -> None:
    base = [
        _cand(PositionSide.LONG, trigger_time=10, reason=ExitReason.TAKE_PROFIT),
        _cand(PositionSide.SHORT, trigger_time=20, reason=ExitReason.STOP_LOSS),
    ]
    reentry = [_cand(PositionSide.SHORT, trigger_time=30, reason=ExitReason.TAKE_PROFIT)]
    cell = _payload(base, reentry, boundary=0)
    shorts = _short_candidates(cell, "full")
    assert len(shorts) == 2
    assert all(c.side is PositionSide.SHORT for c in shorts)


def test_short_candidates_oos_warm_boundary_filter() -> None:
    base = [
        _cand(PositionSide.SHORT, trigger_time=5, reason=ExitReason.STOP_LOSS),
        _cand(PositionSide.SHORT, trigger_time=50, reason=ExitReason.TAKE_PROFIT),
    ]
    cell = _payload(base, [], boundary=25)
    warm = _short_candidates(cell, "oos_warm")
    assert [c.trigger_time for c in warm] == [50]  # 경계 이전(5)은 빠진다


# --------------------------------------------------------------------------- #
# 4. 헤지 창 산수
# --------------------------------------------------------------------------- #


def _curve(points: list[tuple[int, float]]) -> list[EquityPoint]:
    return [EquityPoint(time=t, equity=e) for t, e in points]


def test_max_drawdown_window() -> None:
    curve = _curve([(0, 100.0), (1, 120.0), (2, 90.0), (3, 110.0)])
    mdd, peak_t, trough_t = _max_drawdown_window(curve)
    assert abs(mdd - 0.25) < 1e-12  # 120 → 90 = 25%
    assert peak_t == 1 and trough_t == 2


def test_max_drawdown_window_empty() -> None:
    assert _max_drawdown_window([]) == (0.0, 0, 0)


def test_window_return() -> None:
    curve = _curve([(0, 100.0), (1, 120.0), (2, 90.0), (3, 110.0)])
    # 창 [1, 2]: 시작 120, 끝 90 → −25%
    ret = _window_return(curve, 1, 2)
    assert ret is not None and abs(ret - (-0.25)) < 1e-12
    # 창 시작이 곡선 밖(음수)이면 첫 점 이하가 없어 None
    assert _window_return(curve, -5, -1) is None


def test_net_r_definition() -> None:
    # return_pct 0.03, entry 100, stop 120 → risk_frac 0.2 → net_r 0.15
    assert abs(_net_r(0.03, 100.0, 120.0) - 0.15) < 1e-12
    assert _net_r(0.03, 100.0, 100.0) == 0.0  # 0R 방어


# --------------------------------------------------------------------------- #
# 5. 판정 · 프레임 왕복
# --------------------------------------------------------------------------- #


def _book_row(arm: str, *, mdd: float, rm: float, trades: int = 100) -> BookScopeRow:
    return BookScopeRow(
        scope="all",
        arm=arm,
        segment="oos_warm",
        exclude_symbol="",
        num_cells=9,
        num_symbols=9,
        num_trades=trades,
        win_rate=0.5,
        total_return=1.0,
        max_drawdown=mdd,
        return_over_mdd=rm,
        peak_concurrency=3,
        max_concurrent_risk=0.07,
        liquidation_events=0,
    )


def test_verdict_hedge_when_mdd_down_and_rm_up() -> None:
    rows = [
        _book_row(ARM_LONG_ONLY, mdd=0.20, rm=5.0),
        _book_row(ARM_LONG_SHORT, mdd=0.15, rm=7.0),
    ]
    text = verdict(rows, [])
    assert "하락장 헤지가 된다" in text


def test_verdict_no_hedge_when_worse() -> None:
    rows = [
        _book_row(ARM_LONG_ONLY, mdd=0.15, rm=7.0),
        _book_row(ARM_LONG_SHORT, mdd=0.20, rm=5.0),
    ]
    text = verdict(rows, [])
    assert "헤지가 되지 않는다" in text


def test_verdict_reports_isolated_short_net_r() -> None:
    rows = [_book_row(ARM_LONG_ONLY, mdd=0.2, rm=5.0), _book_row(ARM_LONG_SHORT, mdd=0.15, rm=7.0)]
    diag = [
        ShortDiagRow(
            symbol="BTC/USDT",
            timeframe="1h",
            segment="oos_warm",
            short_trades=10,
            wins=3,
            stops=6,
            unclosed=1,
            net_r_sum=-4.5,
            net_pp_sum=-2.0,
            funding_coverage_gap=False,
        )
    ]
    text = verdict(rows, diag)
    assert "격리 숏 순R -4.5" in text


def test_book_row_roundtrip(tmp_path: Path) -> None:
    rows = [_book_row(ARM_LONG_SHORT, mdd=0.15, rm=7.0)]
    path = tmp_path / "book.csv"
    book_to_frame(rows).to_csv(path, index=False)
    assert book_from_csv(path) == rows


def test_book_row_roundtrip_none_rm(tmp_path: Path) -> None:
    rows = [
        BookScopeRow(
            scope="all",
            arm=ARM_LONG_ONLY,
            segment="oos_warm",
            exclude_symbol="",
            num_cells=1,
            num_symbols=1,
            num_trades=5,
            win_rate=0.0,
            total_return=0.0,
            max_drawdown=0.0,
            return_over_mdd=None,
            peak_concurrency=0,
            max_concurrent_risk=0.0,
            liquidation_events=0,
        )
    ]
    path = tmp_path / "book.csv"
    book_to_frame(rows).to_csv(path, index=False)
    assert book_from_csv(path) == rows


def test_diag_row_roundtrip_and_props(tmp_path: Path) -> None:
    row = ShortDiagRow(
        symbol="BTC/USDT",
        timeframe="4h",
        segment="oos_warm",
        short_trades=25,
        wins=8,
        stops=15,
        unclosed=2,
        net_r_sum=-4.5,
        net_pp_sum=-3.2,
        funding_coverage_gap=False,
    )
    assert row.win_rate is not None and abs(row.win_rate - 8 / 23) < 1e-12
    assert row.mean_net_r is not None and abs(row.mean_net_r - (-4.5 / 25)) < 1e-12
    path = tmp_path / "diag.csv"
    diag_to_frame([row]).to_csv(path, index=False)
    assert diag_from_csv(path) == [row]


def test_hedge_row_roundtrip(tmp_path: Path) -> None:
    rows = [
        HedgeRow(
            scope="all",
            segment="oos_warm",
            long_only_mdd=0.2,
            long_short_mdd=0.15,
            window_start=1000,
            window_end=2000,
            long_only_window_return=-0.2,
            long_short_window_return=-0.05,
        ),
        HedgeRow(
            scope="all",
            segment="oos",
            long_only_mdd=0.3,
            long_short_mdd=0.3,
            window_start=0,
            window_end=0,
            long_only_window_return=None,
            long_short_window_return=None,
        ),
    ]
    path = tmp_path / "hedge.csv"
    hedge_to_frame(rows).to_csv(path, index=False)
    assert hedge_from_csv(path) == rows


def test_describe_engine_reports_adopted_defaults() -> None:
    text = describe_engine()
    assert "short_enabled=False(기본)" in text
    assert "max_zone_width_atr=1.28" in text
    assert "band_bar=intrabar_live" in text
    assert "reentry=band" in text


# --------------------------------------------------------------------------- #
# 6. 실데이터 통합 (CI에선 skip — 데이터 없으면 자동으로 넘어간다)
# --------------------------------------------------------------------------- #

_RD_SYMBOL = "BTCUSDT"
_RD_TF = "4h"
_RD_START = "2024-01-01"
_RD_END = "2025-01-01"


def _require_real_data() -> None:
    market = harness.load_market_data(
        harness.normalize_symbol(_RD_SYMBOL),
        _RD_TF,
        start_ms=parse_date_ms(_RD_START),
        end_ms=parse_date_ms(_RD_END),
        need_1m=False,
        funding=False,
    )
    if market.empty:
        pytest.skip(f"{_RD_SYMBOL} {_RD_TF} 실데이터 없음 — 통합 검증 skip(CI 기본).")


def test_long_only_arm_matches_adopted_book() -> None:
    """검산: 롱-온리 팔 = 채택 북(= 인자 없는 `backtest.run`). total_return 비트 일치.

    `run_arm_cells(short_enabled=False)` + `build_book_scope_rows`의 롱-온리 행이
    `book_cli.build_book_rows`(채택 북)와 같은 셀·같은 배치라 정확히 일치해야 한다.
    """
    from backtest.wan282_resistance_short_mirror import build_book_scope_rows, run_arm_cells

    _require_real_data()
    cells = run_arm_cells(
        (_RD_SYMBOL,), (_RD_TF,), start=_RD_START, end=_RD_END, short_enabled=False, log=False
    )
    rows = build_book_scope_rows(
        cells, cells, start_ms=parse_date_ms(_RD_START), end_ms=parse_date_ms(_RD_END)
    )
    book = book_cli.build_book_rows(
        cells,
        book=ADOPTED_BOOK,
        segments=("oos_warm",),
        start_ms=parse_date_ms(_RD_START),
        end_ms=parse_date_ms(_RD_END),
        include_reentry=True,
    )
    lo = next(
        r
        for r in rows
        if r.arm == ARM_LONG_ONLY and r.exclude_symbol == "" and r.segment == "oos_warm"
    )
    assert abs(lo.total_return - book[0].total_return) < 1e-12
    assert lo.num_trades == book[0].num_trades
    assert abs(lo.max_drawdown - book[0].max_drawdown) < 1e-12


def test_short_toggle_adds_shorts_and_keeps_longs() -> None:
    """`short_enabled=True`가 롱+숏 북에 숏을 더하고 롱은 그대로다(엔진 대칭·wan169 배선)."""
    from backtest.wan282_resistance_short_mirror import run_arm_cells

    _require_real_data()
    long_only = run_arm_cells(
        (_RD_SYMBOL,), (_RD_TF,), start=_RD_START, end=_RD_END, short_enabled=False, log=False
    )
    long_short = run_arm_cells(
        (_RD_SYMBOL,), (_RD_TF,), start=_RD_START, end=_RD_END, short_enabled=True, log=False
    )
    lo_shorts = sum(
        1 for cell in long_only for c in cell.candidates["full"] if c.side is PositionSide.SHORT
    )
    ls_shorts = sum(
        1 for cell in long_short for c in cell.candidates["full"] if c.side is PositionSide.SHORT
    )
    assert lo_shorts == 0  # 롱-온리엔 숏 후보 없음
    assert ls_shorts > 0  # 롱+숏엔 베어리시 OB 숏 추가
    # 롱 후보는 두 팔에서 비트 동일(숏 게이트가 롱을 건드리지 않는다).
    lo_longs = [
        c for cell in long_only for c in cell.candidates["full"] if c.side is PositionSide.LONG
    ]
    ls_longs = [
        c for cell in long_short for c in cell.candidates["full"] if c.side is PositionSide.LONG
    ]
    assert len(lo_longs) == len(ls_longs)
    for a, b in zip(lo_longs, ls_longs, strict=True):
        assert a.entry_price == b.entry_price and a.stop_price == b.stop_price
