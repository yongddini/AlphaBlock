"""dashboard.live_board — 차트-우선 메인 화면(WAN-245)의 순수 로직 테스트.

화면(Streamlit) 없이 **동작으로** 고정한다: 2h가 선택지에서 사라지지 않는지, 존이
정말 최근 N개로 잘리는지, 열 이름이 사용자 확정본인지, 지갑 곡선·MDD 구간이 실제
숫자와 맞는지.
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from dashboard.health_data import OpenPositionView
from dashboard.live_board import (
    CHART_BARS,
    RECENT_ZONE_LIMIT,
    EquityPoint,
    chart_start_ms,
    chart_symbols,
    chart_timeframes,
    legend_title,
    max_drawdown_window,
    open_positions_frame,
    recent_zones,
    symbol_label,
    total_unrealized_pct,
    wallet_equity_points,
)
from live.runtime_state import PositionSnapshot
from paper.store import PaperTradeRecord
from strategy.models import OrderBlock, OrderBlockDirection, SignalExitReason

_HOUR = 3_600_000


def _ob(
    *, confirmed: int, direction: OrderBlockDirection = OrderBlockDirection.BULLISH
) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=101.0,
        bottom=99.0,
        start_time=confirmed - _HOUR,
        confirmed_time=confirmed,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def test_timeframe_options_include_derived_2h() -> None:
    """2h가 선택지에 있어야 한다 — 저장된 시리즈에서 만들면 **영영 안 뜬다**.

    `OhlcvStore.list_series()`는 물리 저장 행만 보는데 2h는 1h에서 파생되므로
    (WAN-24) 그 목록에 없다. 이 화면의 TF 목록이 **작업 TF 상수**에서 오는 이유다.
    """
    options = chart_timeframes(Settings())

    assert options[:4] == ["15m", "1h", "2h", "4h"]


def test_timeframe_options_survive_a_narrowed_runner_config() -> None:
    """운영 설정이 러너를 1h 단독으로 좁혀도 **화면 선택지는 작업 TF 4개 그대로**다.

    로컬 `.env`가 실제로 그렇다(`ALPHABLOCK_LIVE_SIGNAL_TIMEFRAMES=["1h"]`) — 러너
    감시 목록을 기준으로 만들면 차트 탭이 조용히 1×1짜리가 된다(완료 기준 1 위반).
    """
    narrowed = Settings(live_signal_timeframes=["1h"], live_signal_symbols=["BTC/USDT:USDT"])

    assert chart_timeframes(narrowed) == ["15m", "1h", "2h", "4h"]
    assert len(chart_symbols(narrowed)) == 9


def test_timeframe_options_append_timeframes_the_runner_watches_outside_the_grid() -> None:
    """채택 좌표 밖을 러너가 보고 있으면 **잃지 않고 뒤에 붙인다**."""
    settings = Settings(live_signal_timeframes=["1h", "1d"])

    assert chart_timeframes(settings) == ["15m", "1h", "2h", "4h", "1d"]


def test_symbol_options_are_the_collection_universe() -> None:
    symbols = chart_symbols(Settings())

    assert len(symbols) == 9
    assert symbols[0].startswith("BTC/")


def test_chart_start_ms_reads_only_the_recent_window() -> None:
    """메인 차트는 6년 전량이 아니라 최근 `CHART_BARS`봉만 읽는다(WAN-202 흡수)."""
    last = 10_000 * _HOUR

    start = chart_start_ms(last, "1h")

    assert (last - start) // _HOUR == CHART_BARS - 1
    # 짧은 시리즈에서 음수로 내려가지 않는다.
    assert chart_start_ms(5 * _HOUR, "1h") == 0


def test_recent_zones_keeps_only_the_latest_and_includes_shorts() -> None:
    """최근 4개만 · 방향 불문(숏 존 포함) · 시간 오름차순."""
    blocks = [
        _ob(confirmed=1 * _HOUR),
        _ob(confirmed=2 * _HOUR, direction=OrderBlockDirection.BEARISH),
        _ob(confirmed=3 * _HOUR),
        _ob(confirmed=4 * _HOUR, direction=OrderBlockDirection.BEARISH),
        _ob(confirmed=5 * _HOUR),
        _ob(confirmed=6 * _HOUR),
    ]

    picked = recent_zones(blocks)

    assert len(picked) == RECENT_ZONE_LIMIT
    assert [ob.confirmed_time // _HOUR for ob in picked] == [3, 4, 5, 6]
    assert OrderBlockDirection.BEARISH in {ob.direction for ob in picked}


def test_recent_zones_handles_fewer_blocks_than_limit() -> None:
    assert len(recent_zones([_ob(confirmed=_HOUR)])) == 1
    assert recent_zones([], limit=4) == []
    assert recent_zones([_ob(confirmed=_HOUR)], limit=0) == []


def _view(
    *,
    stop: float | None = 99.0,
    take_profit: float | None = 104.0,
    unrealized: float | None = 1.5,
) -> OpenPositionView:
    return OpenPositionView(
        snapshot=PositionSnapshot(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=_HOUR,
            entry_price=100.0,
            stop_price=stop,
            take_profit_price=take_profit,
        ),
        current_price=101.5,
        unrealized_pct=unrealized,
    )


def test_open_positions_columns_say_price_not_action() -> None:
    """열 이름은 「손절가」·「익절가」다(사용자 요청 2026-08-11) — 값이 가격임을 밝힌다."""
    frame = open_positions_frame([_view()])

    assert list(frame.columns) == ["심볼·TF", "방향", "진입가", "손절가", "익절가", "미실현손익"]
    assert frame.iloc[0]["손절가"] == 99.0
    assert frame.iloc[0]["미실현손익"] == "+1.50%"
    # 빈 목록도 같은 열을 낸다(화면이 열 없는 표로 무너지지 않게).
    assert list(open_positions_frame([]).columns) == list(frame.columns)


def test_open_positions_render_missing_prices_as_dash() -> None:
    frame = open_positions_frame([_view(stop=None, take_profit=None, unrealized=None)])

    assert frame.iloc[0]["손절가"] == "—"
    assert frame.iloc[0]["익절가"] == "—"
    assert frame.iloc[0]["미실현손익"] == "—"


def test_total_unrealized_is_none_without_prices() -> None:
    assert total_unrealized_pct([]) is None
    assert total_unrealized_pct([_view(unrealized=None)]) is None
    assert total_unrealized_pct([_view(unrealized=1.0), _view(unrealized=-0.5)]) == pytest.approx(
        0.5
    )


def test_legend_title_matches_tradingview_style() -> None:
    assert symbol_label("BTC/USDT:USDT") == "BTC/USDT PERPETUAL SWAP"
    assert legend_title("BTC/USDT:USDT", "1h") == "BTC/USDT PERPETUAL SWAP · 1시간"


def _record(*, exit_time: int, realized_pnl: float | None) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_time=exit_time - _HOUR,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=101.0,
        reason=SignalExitReason.TAKE_PROFIT,
        gross_pct=1.0,
        fee_pct=0.0,
        funding_pct=0.0,
        net_pct=1.0,
        realized_pnl=realized_pnl,
    )


def test_wallet_equity_curve_is_the_shared_wallet_not_a_last_snapshot() -> None:
    """WAN-237과 **같은 재구성**: 초기 자본 + 실현손익 누적(칸 합산)."""
    records = [
        _record(exit_time=2 * _HOUR, realized_pnl=100.0),
        _record(exit_time=1 * _HOUR, realized_pnl=-50.0),
        _record(exit_time=3 * _HOUR, realized_pnl=25.0),
    ]

    points = wallet_equity_points(records, initial_equity=1_000.0)

    assert [p.equity for p in points] == [1_000.0, 950.0, 1_050.0, 1_075.0]
    assert [p.time_ms for p in points] == [_HOUR, _HOUR, 2 * _HOUR, 3 * _HOUR]


def test_wallet_equity_curve_refuses_percent_only_rows() -> None:
    """달러 손익이 없는 옛 행(WAN-207 이전)이 섞이면 억지 역산 대신 **안 그린다**."""
    records = [
        _record(exit_time=_HOUR, realized_pnl=10.0),
        _record(exit_time=2 * _HOUR, realized_pnl=None),
    ]

    assert wallet_equity_points(records, initial_equity=1_000.0) == []
    assert wallet_equity_points([], initial_equity=1_000.0) == []
    assert wallet_equity_points(records, initial_equity=None) == []


def test_max_drawdown_window_reports_where_not_just_how_much() -> None:
    """ "MDD −N%"만이 아니라 **고점→저점 구간**을 낸다(빨간 구간을 그리려면 필요)."""
    points = [
        EquityPoint(time_ms=1, equity=1_000.0),
        EquityPoint(time_ms=2, equity=1_200.0),
        EquityPoint(time_ms=3, equity=900.0),
        EquityPoint(time_ms=4, equity=1_500.0),
        EquityPoint(time_ms=5, equity=1_400.0),
    ]

    window = max_drawdown_window(points)

    assert window is not None
    assert window.peak_time_ms == 2
    assert window.trough_time_ms == 3
    assert window.drawdown_pct == pytest.approx(25.0)


def test_max_drawdown_window_is_none_when_the_curve_only_rises() -> None:
    rising = [EquityPoint(time_ms=i, equity=100.0 * i) for i in range(1, 5)]

    assert max_drawdown_window(rising) is None
    assert max_drawdown_window([]) is None
