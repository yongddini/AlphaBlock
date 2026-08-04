"""당일 거래별 타임라인 도구 테스트 (WAN-234).

- 라이브: 주문 장부(예약 창) + 페이퍼 라운드트립 조인이 예약→체결가→청산가→손익을 잇고,
  상태 라벨이 WAN-232 규약을 따르는지. 코호트가 `orders_placed_between`(예약 창)과 같은지.
- 공용 표시 프레임: 화면·터미널·CSV가 같은 함수(`timeline_to_display_frame`)를 써 같은 숫자·
  같은 KST 포맷(`backtest.report.format_time_kst`)을 내는지(완료 기준 3). 렌더 == 프레임.
- 정렬: 같은 셀에서 라이브가 백테 앞(주인공)이고 「백테만 있는 줄」 신호가 잡히는지.
- (실데이터) 백테스트 셀이 워밍업 연속 + 그날만 평가라 미래 봉을 잘라도 그날 거래가 비트
  동일(누수 0).
- CLI `alphablock trades`가 라우팅되는지.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backtest.report import format_time_kst
from common.timefmt import kst_day_bounds_for_date
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder
from live.order_journal import (
    ENTRY_STATUS_ENTERED,
    SKIP_REASON_CELL_BUSY,
    SKIP_REASON_RETAP,
    SKIP_REASON_ZONE_WIDTH,
    STATUS_DISCARDED_RESTART,
    STATUS_SKIPPED,
    OrderJournal,
    PlacedOrder,
)
from live.trade_timeline import (
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    DayTimeline,
    TimelineRow,
    _live_status,
    build_day_timeline,
    display_columns,
    live_timeline_rows,
    render_day_timeline,
    resolve_day_window,
    timeline_to_display_frame,
)
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"


def _place(journal: OrderJournal, session: int, *, placed_ms: int) -> int:
    order = PendingLimitOrder(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=placed_ms,
    )
    return journal.record_placed(
        order, session_id=session, zone_start_time=50, zone_confirmed_time=60
    )


def _fill(time_ms: int, *, price: float = 100.05) -> LimitFill:
    return LimitFill(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        price=price,
        time=time_ms,
        rsi=25.0,
        stop_price=90.0,
        take_profit_price=115.0,
        penetration_bps=0.0,
        waited_ms=60_000,
    )


def _roundtrip(*, entry_time: int, exit_time: int) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        entry_time=entry_time,
        entry_price=100.05,
        exit_time=exit_time,
        exit_price=115.0,
        reason=SignalExitReason.TAKE_PROFIT,
        gross_pct=14.9,
        fee_pct=0.1,
        slippage_pct=0.0,
        funding_pct=0.0,
        net_pct=14.8,
        risk_pct=10.0,
        r_multiple=1.48,
        stop_price=90.0,
        take_profit_price=115.0,
        realized_pnl=148.0,
        equity_after=1148.0,
    )


def test_live_timeline_joins_roundtrip(tmp_path: Path) -> None:
    """체결·진입 주문이 같은 시각(`fill_ms==entry_time`)의 라운드트립과 이어진다."""
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    oid = _place(journal, session, placed_ms=1100)
    journal.record_filled(oid, _fill(1400))
    journal.record_entry_result(oid, entered=True)
    store.upsert_record(_roundtrip(entry_time=1400, exit_time=5000))

    rows = live_timeline_rows(journal, store, start_ms=1000, end_ms=2000)
    assert len(rows) == 1
    row = rows[0]
    assert row.source == SOURCE_LIVE
    assert row.status == "청산"
    assert row.reserve_ms == 1100
    assert row.fill_ms == 1400
    assert row.fill_price == 100.05
    assert row.stop_price == 90.0
    assert row.take_profit_price == 115.0
    assert row.exit_ms == 5000
    assert row.exit_price == 115.0
    assert row.pnl_pct == 14.8
    assert row.pnl_amount == 148.0
    assert row.zone_start_time == 50 and row.zone_confirmed_time == 60
    store.close()
    journal.close()


def test_live_status_labels_split_by_stage(tmp_path: Path) -> None:
    """상태 라벨: 미체결(만료+first_rested) · 거부(체결+rejected) · 진입(체결+entered·미청산)."""
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    # 만료(주문판엔 걸림) → 미체결.
    expired = _place(journal, session, placed_ms=1100)
    journal.record_cancelled(expired, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=1500)
    # 체결인데 진입 거부.
    rejected = _place(journal, session, placed_ms=1200)
    journal.record_filled(rejected, _fill(1450))
    journal.record_entry_result(rejected, entered=False, reason="손절 너무 짧음")
    # 체결+진입인데 아직 청산 라운드트립 없음 → 진입.
    entered = _place(journal, session, placed_ms=1300)
    journal.record_filled(entered, _fill(1480))
    journal.record_entry_result(entered, entered=True)

    rows = {r.reserve_ms: r for r in live_timeline_rows(journal, store, start_ms=1000, end_ms=2000)}
    assert rows[1100].status in ("미체결", "밴드기각")
    assert rows[1200].status.startswith("거부(")
    assert rows[1300].status == "진입"
    assert rows[1300].exit_ms is None  # 미청산이라 청산 칸은 빈다.
    store.close()
    journal.close()


def test_skipped_and_discarded_labels_are_korean(tmp_path: Path) -> None:
    """`skipped`(사유 병기)·`discarded_restart`가 한글 라벨로 뜬다 — 영어 누출 없음(WAN-240)."""
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    # 주문 걸기 전 걸러진 셋업(존폭 필터) → 건너뜀(존폭).
    journal.record_skipped(
        session_id=session,
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH.value,
        tap_index=0,
        placed_ms=1100,
        reason=SKIP_REASON_ZONE_WIDTH,
        zone_start_time=50,
        zone_confirmed_time=60,
    )
    # 이전 세션 대기 주문을 재시작 폐기 → 재시작폐기.
    _place(journal, session, placed_ms=1200)
    journal.discard_stale_pending(now_ms=1500)

    rows = {r.reserve_ms: r for r in live_timeline_rows(journal, store, start_ms=1000, end_ms=2000)}
    assert rows[1100].status == "건너뜀(존폭)"
    assert rows[1200].status == "재시작폐기"
    # 영어 원본이 화면에 그대로 뜨지 않는다.
    assert STATUS_SKIPPED not in rows[1100].status
    assert STATUS_DISCARDED_RESTART not in rows[1200].status
    store.close()
    journal.close()


def _placed(status: str, **kwargs: object) -> PlacedOrder:
    base: dict[str, object] = {
        "symbol": _SYMBOL,
        "timeframe": _TF,
        "direction": OrderBlockDirection.BULLISH.value,
        "placed_ms": 0,
        "status": status,
        "limit_price": None,
        "fill_ms": None,
        "fill_penetration_bps": None,
        "first_rested_ms": None,
        "entry_status": None,
        "entry_reject_reason": None,
        "skip_reason": None,
    }
    base.update(kwargs)
    return PlacedOrder(**base)  # type: ignore[arg-type]


def test_live_status_never_leaks_raw_english() -> None:
    """모든 `LimitOrderStatus` 값 + 장부 원값이 한글 라벨을 갖는다(WAN-240 완료 기준 1)."""
    cases = [
        _placed(LimitOrderStatus.PENDING.value),
        _placed(LimitOrderStatus.CANCELLED_INVALIDATED.value),
        _placed(LimitOrderStatus.CANCELLED_CONDITION_FAILED.value),
        _placed(LimitOrderStatus.CANCELLED_EXPIRED.value, first_rested_ms=1),  # 미체결
        _placed(LimitOrderStatus.CANCELLED_EXPIRED.value),  # 밴드기각
        _placed(LimitOrderStatus.FILLED.value, entry_status=ENTRY_STATUS_ENTERED),  # 진입
        _placed(STATUS_DISCARDED_RESTART),
        _placed(STATUS_SKIPPED, skip_reason=SKIP_REASON_ZONE_WIDTH),
        _placed(STATUS_SKIPPED, skip_reason=SKIP_REASON_CELL_BUSY),
        _placed(STATUS_SKIPPED, skip_reason=SKIP_REASON_RETAP),
        _placed(STATUS_SKIPPED),  # 사유 미기록
    ]
    for order in cases:
        label = _live_status(order, closed=False)
        assert label != order.status, order.status  # 영어 원본 그대로가 아니다.
        assert not label.isascii(), (order.status, label)  # 한글을 포함한다.


def test_live_status_unmapped_falls_back_visibly() -> None:
    """미매핑 상태가 새로 생기면 조용히 영어로 새지 않고 눈에 띄게 찍는다(WAN-240)."""
    assert _live_status(_placed("some_new_status"), closed=False) == "?some_new_status"


def test_cohort_is_placed_window(tmp_path: Path) -> None:
    """코호트는 예약(`placed_ms`) 창 — 창 밖 주문은 빠진다(WAN-232와 같은 창)."""
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    _place(journal, session, placed_ms=1500)  # 창 안.
    _place(journal, session, placed_ms=2500)  # 창 밖.
    rows = live_timeline_rows(journal, store, start_ms=1000, end_ms=2000)
    assert [r.reserve_ms for r in rows] == [1500]
    store.close()
    journal.close()


def _bt_row(*, fill_ms: int, exit_ms: int) -> TimelineRow:
    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=_SYMBOL,
        timeframe=_TF,
        is_long=True,
        status="청산",
        reserve_ms=None,
        limit_price=None,
        fill_ms=fill_ms,
        fill_price=101.0,
        stop_price=None,
        take_profit_price=None,
        exit_ms=exit_ms,
        exit_price=116.0,
        exit_reason="take_profit",
        pnl_pct=15.0,
        pnl_amount=150.0,
    )


def test_display_frame_columns_and_kst_parity() -> None:
    """공용 프레임: 열 골격 고정 + KST 시각이 `format_time_kst`와 같다(완료 기준 3)."""
    fill_ms, exit_ms = 1_600_000_000_000, 1_600_000_360_000
    frame = timeline_to_display_frame([_bt_row(fill_ms=fill_ms, exit_ms=exit_ms)])
    assert tuple(frame.columns) == display_columns()
    # 화면·터미널·CSV가 같은 포맷을 쓰는지 — 같은 함수로 만든 값과 비트 일치.
    assert frame.iloc[0]["체결(KST)"] == format_time_kst(fill_ms)
    assert frame.iloc[0]["청산(KST)"] == format_time_kst(exit_ms)
    assert frame.iloc[0]["손익률%"] == "+15.00%"
    assert frame.iloc[0]["청산사유"] == "익절"


def test_display_frame_empty_keeps_columns() -> None:
    """거래 0건이어도 표 골격이 같다(빈 화면 방지)."""
    frame = timeline_to_display_frame([])
    assert tuple(frame.columns) == display_columns()
    assert len(frame) == 0


def test_display_frame_include_utc_opt_in() -> None:
    """`include_utc=True`면 UTC 열을 병기한다(파일 내보내기)."""
    frame = timeline_to_display_frame([_bt_row(fill_ms=1_600_000_000_000, exit_ms=0 + 1)])
    assert "체결(UTC)" not in frame.columns
    utc_frame = timeline_to_display_frame(
        [_bt_row(fill_ms=1_600_000_000_000, exit_ms=1_600_000_360_000)], include_utc=True
    )
    assert "예약(UTC)" in utc_frame.columns
    assert "체결(UTC)" in utc_frame.columns
    assert "청산(UTC)" in utc_frame.columns


def test_render_matches_display_frame() -> None:
    """터미널 렌더는 공용 프레임과 같은 셀을 찍는다(세 벌로 갈라지지 않음 — 완료 기준 3)."""
    row = _bt_row(fill_ms=1_600_000_000_000, exit_ms=1_600_000_360_000)
    timeline = DayTimeline(day_key="2026-08-02", live=(), backtest=(row,))
    text = render_day_timeline(timeline)
    frame = timeline_to_display_frame(timeline.rows)
    # 프레임의 모든 셀 값이 렌더 표에 그대로 있어야 한다.
    for value in frame.iloc[0].tolist():
        assert str(value) in text


def _live_row(*, reserve_ms: int, symbol: str = _SYMBOL, status: str = "진입") -> TimelineRow:
    return TimelineRow(
        source=SOURCE_LIVE,
        symbol=symbol,
        timeframe=_TF,
        is_long=True,
        status=status,
        reserve_ms=reserve_ms,
        limit_price=100.0,
        fill_ms=reserve_ms + 50,
        fill_price=100.0,
        stop_price=90.0,
        take_profit_price=110.0,
        exit_ms=None,
        exit_price=None,
        exit_reason=None,
        pnl_pct=None,
        pnl_amount=None,
    )


def test_rows_sort_by_time_primary_across_cells() -> None:
    """시각 1차 정렬 — 심볼·출처가 달라도 예약/체결 시각 순으로 늘어선다(WAN-240)."""
    early_bt = TimelineRow(  # 백테(다른 심볼), 가장 이른 체결 1000.
        source=SOURCE_BACKTEST,
        symbol="ETH/USDT:USDT",
        timeframe=_TF,
        is_long=True,
        status="청산",
        reserve_ms=None,
        limit_price=None,
        fill_ms=1000,
        fill_price=101.0,
        stop_price=None,
        take_profit_price=None,
        exit_ms=1500,
        exit_price=116.0,
        exit_reason="take_profit",
        pnl_pct=15.0,
        pnl_amount=150.0,
    )
    mid_live = _live_row(reserve_ms=1200)
    late_live = _live_row(reserve_ms=1400)
    timeline = DayTimeline(day_key="2026-08-02", live=(late_live, mid_live), backtest=(early_bt,))
    whens = [
        (r.reserve_ms if r.reserve_ms is not None else (r.fill_ms or 0)) for r in timeline.rows
    ]
    assert whens == sorted(whens)  # 전체가 시각순.
    # 백테가 라이브보다 이른 시각이면 라이브 앞에 온다(옛 심볼·TF 1차 묶음이 뒤집혔다).
    assert timeline.rows[0].source == SOURCE_BACKTEST


def test_rows_same_time_keep_live_before_backtest() -> None:
    """같은 시각 타이는 라이브(주인공) → 백테(대조) 순으로 인접을 유지한다(WAN-240)."""
    live = _live_row(reserve_ms=1000)
    bt = _bt_row(fill_ms=1000, exit_ms=1200)  # 라이브 예약과 같은 시각.
    timeline = DayTimeline(day_key="2026-08-02", live=(live,), backtest=(bt,))
    assert [r.source for r in timeline.rows] == [SOURCE_LIVE, SOURCE_BACKTEST]


def test_resolve_day_window_parses_and_defaults() -> None:
    start, end, key = resolve_day_window("2026-08-02")
    assert key == "2026-08-02"
    assert (start, end) == kst_day_bounds_for_date(date(2026, 8, 2))
    s2, e2, _ = resolve_day_window("today")
    assert e2 - s2 == 86_400_000


def test_build_day_timeline_live_only(tmp_path: Path) -> None:
    """`include_backtest=False`면 백테스트를 돌리지 않고 라이브만 담는다."""
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    _place(journal, session, placed_ms=1100)
    start_ms, end_ms, key = resolve_day_window("2026-08-02")
    # placed_ms(1100)가 창 밖이라 라이브 0건이지만 예외 없이 빈 타임라인을 낸다.
    timeline = build_day_timeline(
        journal,
        store,
        day_start_ms=start_ms,
        day_end_ms=end_ms,
        day_key=key,
        include_backtest=False,
    )
    assert timeline.backtest == ()
    store.close()
    journal.close()


def test_cli_trades_routes(tmp_path: Path) -> None:
    """`alphablock trades`가 `cmd_trades`로 라우팅되고 기본 인자를 갖는다."""
    from cli.main import build_parser, cmd_trades

    ns = build_parser().parse_args(["trades", "--day", "2026-08-02"])
    assert ns.func is cmd_trades
    assert ns.day == "2026-08-02"
    assert ns.no_backtest is False
    assert ns.jobs == 1


# --------------------------------------------------------------------------- #
# 실데이터: 누수 0 (미래 봉을 잘라도 그날 거래가 비트 동일)
# --------------------------------------------------------------------------- #

_DAY = "2026-07-15"
_DAY_MS = 86_400_000


@pytest.fixture
def _real_day_window() -> tuple[int, int]:
    from backtest.harness import load_market_data

    start_ms, end_ms, _ = resolve_day_window(_DAY)
    market = load_market_data(
        _SYMBOL,
        _TF,
        start_ms=start_ms - 30 * _DAY_MS,
        end_ms=end_ms,
        need_1m=False,
        funding=False,
    )
    if market.empty:
        pytest.skip(f"{_SYMBOL} {_TF} 실데이터가 없어 누수 회귀를 건너뜁니다(CI 기본).")
    return start_ms, end_ms


def test_backtest_cell_trades_ignore_future_bars(_real_day_window: tuple[int, int]) -> None:
    """누수 0: 그날 이후 봉을 물리적으로 잘라도 그날 백테스트 거래가 비트 동일하다."""
    from dataclasses import replace as _replace

    from backtest.harness import detect_order_blocks, load_market_data
    from live.trade_timeline import cell_timeline_trades

    start_ms, end_ms = _real_day_window
    warmup_start = start_ms - 30 * _DAY_MS

    # (A) 처음부터 그날 자정까지만.
    market_a = load_market_data(
        _SYMBOL, _TF, start_ms=warmup_start, end_ms=end_ms, need_1m=True, funding=False
    )
    rows_a = cell_timeline_trades(market_a, detect_order_blocks(market_a), day_start_ms=start_ms)
    # (B) 미래 봉을 포함해 로드한 뒤 그날 자정에서 잘라 재산출.
    long_market = load_market_data(
        _SYMBOL,
        _TF,
        start_ms=warmup_start,
        end_ms=end_ms + 30 * _DAY_MS,
        need_1m=True,
        funding=False,
    )
    htf = long_market.htf_df[long_market.htf_df["open_time"] < end_ms].reset_index(drop=True)
    df_1m = long_market.df_1m[long_market.df_1m["open_time"] < end_ms].reset_index(drop=True)
    truncated = _replace(long_market, htf_df=htf, df_1m=df_1m)
    rows_b = cell_timeline_trades(truncated, detect_order_blocks(truncated), day_start_ms=start_ms)

    def _key(r: TimelineRow) -> tuple[object, ...]:
        return (r.fill_ms, r.exit_ms, r.fill_price, r.exit_price, r.pnl_amount)

    assert [_key(r) for r in rows_a] == [_key(r) for r in rows_b]
