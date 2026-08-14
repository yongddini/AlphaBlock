"""날짜 지정 하루치 백테 대조 화면의 순수 계층 테스트 (WAN-290).

`dashboard/trade_timeline_view.py`의 요약 집계와 `live.trade_timeline`의 「채택 12종목×4TF(WAN-307)
전부」 커버리지를 화면 없이 고정한다:

- `backtest_day_summary`: 백테 행만 세고(라이브 병기 행은 무시) 손익률 `None`은 승/패 어디에도
  안 센다(빈 칸 지어내지 않음 — 완료 기준 3).
- 「전부」 대상은 라이브 활동과 무관하게 채택 12종목×4TF = 48셀을 **전부** 돈다(완료 기준 1).
  무거운 셀 계산은 스텁으로 갈아 커버리지(어느 셀을 도는가)만 잰다.
"""

from __future__ import annotations

import pytest

from dashboard.trade_timeline_view import BacktestDaySummary, backtest_day_summary
from live.trade_timeline import SOURCE_BACKTEST, SOURCE_LIVE, TimelineRow

_TF = "1h"


def _bt_row(*, symbol: str, timeframe: str = _TF, pnl_pct: float | None) -> TimelineRow:
    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=symbol,
        timeframe=timeframe,
        is_long=True,
        status="청산",
        reserve_ms=None,
        limit_price=None,
        fill_ms=1_700_000_000_000,
        fill_price=100.0,
        stop_price=None,
        take_profit_price=None,
        exit_ms=1_700_003_600_000,
        exit_price=101.0,
        exit_reason="take_profit",
        pnl_pct=pnl_pct,
        pnl_amount=None if pnl_pct is None else pnl_pct,
    )


def _live_row(*, symbol: str, pnl_pct: float | None) -> TimelineRow:
    return TimelineRow(
        source=SOURCE_LIVE,
        symbol=symbol,
        timeframe=_TF,
        is_long=True,
        status="청산" if pnl_pct is not None else "진입",
        reserve_ms=1_700_000_000_000,
        limit_price=100.0,
        fill_ms=1_700_000_060_000,
        fill_price=100.0,
        stop_price=90.0,
        take_profit_price=110.0,
        exit_ms=None,
        exit_price=None,
        exit_reason=None,
        pnl_pct=pnl_pct,
        pnl_amount=None,
    )


def test_backtest_day_summary_counts_trades_cells_wins_losses() -> None:
    """백테 행만 세고 셀·승·패를 가른다."""
    rows = [
        _bt_row(symbol="BTC/USDT:USDT", pnl_pct=1.2),  # 승
        _bt_row(symbol="BTC/USDT:USDT", timeframe="4h", pnl_pct=-0.5),  # 패 (다른 셀)
        _bt_row(symbol="ETH/USDT:USDT", pnl_pct=2.0),  # 승
    ]
    summary = backtest_day_summary(rows)
    assert summary == BacktestDaySummary(trades=3, cells_with_trades=3, wins=2, losses=1)


def test_backtest_day_summary_ignores_live_and_none_pnl() -> None:
    """라이브 병기 행은 백테 요약에서 빠지고, 손익률 None은 승/패 어디에도 안 센다."""
    rows = [
        _bt_row(symbol="BTC/USDT:USDT", pnl_pct=1.0),  # 승
        _bt_row(symbol="SOL/USDT:USDT", pnl_pct=None),  # 손익 없음 → 승/패 아님
        _live_row(symbol="BTC/USDT:USDT", pnl_pct=5.0),  # 라이브 → 무시
    ]
    summary = backtest_day_summary(rows)
    # 백테 2건(BTC 승·SOL None), 2셀, 승 1·패 0. 라이브 5.0%는 세지 않는다.
    assert summary == BacktestDaySummary(trades=2, cells_with_trades=2, wins=1, losses=0)


def test_backtest_day_summary_empty() -> None:
    assert backtest_day_summary([]) == BacktestDaySummary(
        trades=0, cells_with_trades=0, wins=0, losses=0
    )


def test_full_universe_runs_all_12x4_cells_regardless_of_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「채택 12종목×4TF(WAN-307) 전부」는 라이브와 무관하게 48셀을 전부 돈다(완료 기준 1).

    화면 버튼이 `backtest_timeline_by_cell`(심볼·TF 미지정 = 채택 좌표)을 호출하는데, 그
    좌표가 실제로 채택 12종목 × 4TF임을 스텁으로 고정한다 — 무거운 셀 계산은 갈아 끼우고
    「어느 셀을 도는가」만 잰다.
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES
    from live.trade_timeline import _BacktestCellTask, backtest_timeline_by_cell

    seen: list[tuple[str, str]] = []

    def _fake_cell(task: _BacktestCellTask) -> list[TimelineRow]:
        seen.append((task.symbol, task.timeframe))
        return []

    monkeypatch.setattr("live.trade_timeline._backtest_cell_trades", _fake_cell)

    by_cell = backtest_timeline_by_cell(day_start_ms=0, day_end_ms=86_400_000)

    expected = {(s, tf) for s in DEFAULT_SYMBOLS for tf in DEFAULT_TIMEFRAMES}
    assert set(seen) == expected  # 라이브 파생이 아니라 채택 좌표 전부.
    assert set(by_cell.keys()) == expected
    assert len(expected) == 12 * 4  # 12종목(WAN-307) × 4TF = 48셀.
