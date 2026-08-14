"""dashboard.live_board — 차트-우선 메인 화면(WAN-245)의 순수 로직 테스트.

화면(Streamlit) 없이 **동작으로** 고정한다: 2h가 선택지에서 사라지지 않는지, 존이
정말 최근 N개로 잘리는지, 열 이름이 사용자 확정본인지, 지갑 곡선·MDD 구간이 실제
숫자와 맞는지.
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from dashboard.live_board import (
    ACTIVE_ZONE_LIMIT,
    CHART_BARS,
    REASON_FILTER_ALL,
    RECENT_ZONE_LIMIT,
    WALLET_TRADE_COLUMNS,
    ZONE_VIEW_MARGIN_BARS,
    EquityPoint,
    OpenPositionRow,
    build_open_position_row,
    chart_start_ms,
    chart_symbols,
    chart_timeframes,
    display_zones,
    filter_records_by_choice,
    legend_title,
    max_drawdown_window,
    open_positions_frame,
    recent_zones,
    symbol_label,
    total_unrealized_usd,
    wallet_equity_points,
    wallet_trade_frame,
    zone_view_start_ms,
)
from execution.models import Position
from paper.store import PaperTradeRecord
from strategy.models import OrderBlock, OrderBlockDirection, SignalExitReason

_HOUR = 3_600_000


def _ob(
    *,
    confirmed: int,
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    break_time: int | None = None,
    swept_time: int | None = None,
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
        break_time=break_time,
        swept_time=swept_time,
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
    assert len(chart_symbols(narrowed)) == 12  # WAN-307: 채택 유니버스 12종목.


def test_timeframe_options_append_timeframes_the_runner_watches_outside_the_grid() -> None:
    """채택 좌표 밖을 러너가 보고 있으면 **잃지 않고 뒤에 붙인다**."""
    settings = Settings(live_signal_timeframes=["1h", "1d"])

    assert chart_timeframes(settings) == ["15m", "1h", "2h", "4h", "1d"]


def test_symbol_options_are_the_collection_universe() -> None:
    symbols = chart_symbols(Settings())

    assert len(symbols) == 12  # WAN-307: 채택 유니버스 12종목.
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


def test_display_zones_picks_active_six_plus_dead_in_their_window() -> None:
    """WAN-289 사용자 결정(2026-08-12): 선택 기준 = **활성 존 6개**.

    활성 7개 + 죽은 존 2개를 주면 — 활성은 최신 6개만 남고, 그 6개의 구간과 겹치는
    죽은 존은 회색으로 **함께** 그려지며(폐기 아님), 구간보다 먼저 끝난 죽은 존은
    빠진다. 라벨이 아니라 동작으로 고정한다.
    """
    active = [_ob(confirmed=(i + 10) * _HOUR) for i in range(7)]  # start 9h..15h
    dead_inside = _ob(confirmed=2 * _HOUR, break_time=12 * _HOUR)  # 창(>=10h)과 겹침
    dead_before = _ob(confirmed=2 * _HOUR, break_time=3 * _HOUR)  # 창 이전에 끝남

    picked = display_zones([dead_inside, dead_before, *active])

    alive = [ob for ob in picked if ob.break_time is None and ob.swept_time is None]
    assert len(alive) == ACTIVE_ZONE_LIMIT == 6
    # 활성 7개(confirmed 10h~16h) 중 최신 6개(11h~16h)만 — 가장 오래된 10h는 잘린다.
    assert min(ob.confirmed_time for ob in alive) == 11 * _HOUR
    assert dead_inside in picked
    assert dead_before not in picked
    # 시간 오름차순(차트가 왼쪽→오른쪽으로 그리는 순서).
    assert [ob.confirmed_time for ob in picked] == sorted(ob.confirmed_time for ob in picked)


def test_display_zones_falls_back_to_recent_when_nothing_is_alive() -> None:
    """활성 존이 하나도 없으면 옛 규칙(최근 4개)으로 폴백 — 빈 차트는 고장으로 읽힌다."""
    dead = [_ob(confirmed=i * _HOUR, break_time=(i + 1) * _HOUR) for i in range(1, 7)]

    picked = display_zones(dead)

    assert len(picked) == RECENT_ZONE_LIMIT
    assert [ob.confirmed_time // _HOUR for ob in picked] == [3, 4, 5, 6]


def test_zone_view_start_extends_to_the_oldest_active_zone_with_margin() -> None:
    """첫 화면 창 = 가장 오래된 **활성** 존 생성 봉 − 여유(`ZONE_VIEW_MARGIN_BARS`)."""
    zones = display_zones(
        [
            _ob(confirmed=10 * _HOUR),
            _ob(confirmed=20 * _HOUR),
            _ob(confirmed=2 * _HOUR, break_time=15 * _HOUR),  # 죽은 존은 경계 계산에서 제외
        ]
    )

    start = zone_view_start_ms(zones, "1h")

    assert start == 9 * _HOUR - ZONE_VIEW_MARGIN_BARS * _HOUR  # start_time = confirmed − 1h


def test_zone_view_start_is_none_without_active_zones() -> None:
    dead = [_ob(confirmed=2 * _HOUR, break_time=3 * _HOUR)]

    assert zone_view_start_ms(display_zones(dead), "1h") is None


def _row(
    *,
    stop: float | None = 99.0,
    take_profit: float | None = 104.0,
    price: float | None = 101.5,
    quantity: float = 2.0,
) -> OpenPositionRow:
    return build_open_position_row(
        Position(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            quantity=quantity,
            entry_price=100.0,
            entry_time=_HOUR,
            stop_price=stop,
            take_profit_price=take_profit,
        ),
        price,
    )


def test_open_positions_columns_say_price_not_action() -> None:
    """열 이름은 「손절가」·「익절가」다(사용자 요청 2026-08-11) — 값이 가격임을 밝힌다."""
    frame = open_positions_frame([_row()])

    assert list(frame.columns) == ["심볼 · TF", "방향", "진입가", "손절가", "익절가", "미실현손익"]
    assert frame.iloc[0]["심볼 · TF"] == "BTC · 1h"  # 목업 표기(짧은 심볼)
    assert frame.iloc[0]["손절가"] == "99.00"
    # 빈 목록도 같은 열을 낸다(화면이 열 없는 표로 무너지지 않게).
    assert list(open_positions_frame([]).columns) == list(frame.columns)


def test_open_position_unrealized_shows_dollars_and_percent() -> None:
    """목업의 `+58.1 (+1.08%)` — 달러가 나오려면 **수량**이 있어야 한다.

    러너 상태파일 스냅샷에는 수량이 없어서 이 표는 `open_positions` 테이블에서 만든다.
    """
    frame = open_positions_frame([_row(quantity=2.0, price=101.5)])

    # 롱 2코인 × (101.5 − 100.0) = +3.0달러 · +1.50%
    assert frame.iloc[0]["미실현손익"] == "+3.0 (+1.50%)"


def test_open_position_short_direction_flips_the_sign() -> None:
    short = build_open_position_row(
        Position(
            symbol="XRP/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BEARISH,
            quantity=100.0,
            entry_price=0.640,
            entry_time=_HOUR,
            stop_price=0.660,
            take_profit_price=0.600,
        ),
        0.620,
    )

    assert short.unrealized_usd == pytest.approx(2.0)
    assert short.unrealized_pct == pytest.approx(3.125)
    assert open_positions_frame([short]).iloc[0]["방향"] == "숏"
    # 저가 종목은 소수 4자리로 읽힌다(BTC의 64,690과 한 규칙).
    assert open_positions_frame([short]).iloc[0]["진입가"] == "0.6400"


def test_open_positions_render_missing_prices_as_dash() -> None:
    frame = open_positions_frame([_row(stop=None, take_profit=None, price=None)])

    assert frame.iloc[0]["손절가"] == "—"
    assert frame.iloc[0]["익절가"] == "—"
    assert frame.iloc[0]["미실현손익"] == "—"


def test_total_unrealized_usd_sums_the_wallet_not_percentages() -> None:
    """달러는 **더해도 뜻이 있다**(같은 지갑의 돈) — 잔고 탭 카드가 이 값을 쓴다."""
    assert total_unrealized_usd([]) is None
    assert total_unrealized_usd([_row(price=None)]) is None
    assert total_unrealized_usd([_row(price=101.5), _row(price=99.0)]) == pytest.approx(1.0)


def test_legend_title_matches_tradingview_style() -> None:
    assert symbol_label("BTC/USDT:USDT") == "BTC/USDT PERPETUAL SWAP"
    assert legend_title("BTC/USDT:USDT", "1h") == "BTC/USDT PERPETUAL SWAP · 1시간"


def _record(
    *,
    exit_time: int,
    realized_pnl: float | None,
    reason: SignalExitReason = SignalExitReason.TAKE_PROFIT,
) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_time=exit_time - _HOUR,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=101.0,
        reason=reason,
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


def test_exit_reason_chips_narrow_and_all_shows_everything() -> None:
    """칩은 전체/익절만/손절만 세 갈래(목업) — 「전체」가 기본이자 모르는 값의 폴백이다."""
    win = _record(exit_time=_HOUR, realized_pnl=10.0)
    loss = _record(exit_time=2 * _HOUR, realized_pnl=-5.0, reason=SignalExitReason.STOP_LOSS)

    assert filter_records_by_choice([win, loss], "손절만") == [loss]
    assert filter_records_by_choice([win, loss], "익절만") == [win]
    assert filter_records_by_choice([win, loss], REASON_FILTER_ALL) == [win, loss]
    # 모르는 선택은 「전체」로 접는다(빈 화면은 고장으로 읽힌다).
    assert filter_records_by_choice([win, loss], "???") == [win, loss]


def test_wallet_trade_frame_is_the_compact_mockup_table_newest_first() -> None:
    """잔고 탭 리스트는 목업의 **8열**이고 최근순이다(전체 20열 원장은 따로 남는다)."""
    from paper.report import records_to_display_frame

    old = _record(exit_time=_HOUR, realized_pnl=10.0)
    new = _record(exit_time=3 * _HOUR, realized_pnl=-5.0, reason=SignalExitReason.STOP_LOSS)

    frame = wallet_trade_frame([old, new])

    assert list(frame.columns) == WALLET_TRADE_COLUMNS
    assert len(frame.columns) == 8
    assert frame.iloc[0]["사유"] == "손절"  # 최근순
    assert frame.iloc[1]["사유"] == "익절"
    assert frame.iloc[0]["손익"] == "-5.0"
    # 필터·표가 같은 라벨을 쓴다 — 두 벌이면 결과가 늘 비어 보인다.
    assert set(frame["사유"]) <= set(records_to_display_frame([old, new])["청산사유"])
    assert list(wallet_trade_frame([]).columns) == WALLET_TRADE_COLUMNS
