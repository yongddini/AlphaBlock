"""진입/미진입 사유 장부 조회의 표시 계층 테스트 (WAN-219).

지켜야 하는 성질: (1) 체결률은 **닿았나 vs 안 닿았나**만 분모로 본다(스킵·거부 제외),
(2) 미진입 사유 분포는 진입/체결을 뺀 순수 미진입만 센다, (3) 필터 라벨과 표의 값이 같은
문자열이라 사유를 골라도 빈 표가 안 뜬다, (4) 모르는 코드·방향은 원문을 남긴다, (5) 빈
목록도 골격을 유지한다, 그리고 (6) 목록을 되접으면 `OrderJournal.funnel_counts`와 정확히
같아진다(조회일 뿐 재계산이 아니라는 교차검산).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dashboard.funnel_ledger import (
    ALL,
    cell_options,
    fill_rate_by_cell,
    filter_entries,
    ledger_frame,
    reason_distribution,
    reason_label,
    reason_options,
    to_funnel_counts,
)
from execution.engine import REJECT_CODE_NOTIONAL, REJECT_CODE_SIZING
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder
from live.order_journal import (
    LEDGER_REASON_DEVIATION,
    LEDGER_REASON_ENTERED,
    LEDGER_REASON_NO_FILL,
    LEDGER_REASON_UNRECORDED,
    SKIP_REASON_CELL_BUSY,
    SKIP_REASON_RETAP,
    SKIP_REASON_ZONE_WIDTH,
    LedgerEntry,
    OrderJournal,
)
from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi


def _entry(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    direction: str = "bull",
    event_ms: int = 1_000,
    filled: bool,
    reason: str,
    fill_price: float | None = None,
    limit_price: float | None = None,
    penetration_bps: float | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        event_ms=event_ms,
        filled=filled,
        reason=reason,
        fill_price=fill_price,
        limit_price=limit_price,
        penetration_bps=penetration_bps,
    )


def test_fill_rate_by_cell_uses_touch_denominator() -> None:
    """칸별 체결률 분모는 체결 + no_fill뿐이다 — 스킵·거부는 넣지 않는다."""
    entries = [
        _entry(filled=True, reason=LEDGER_REASON_ENTERED),  # BTC 1h 체결
        _entry(filled=False, reason=LEDGER_REASON_NO_FILL),  # BTC 1h 미체결
        _entry(filled=False, reason=SKIP_REASON_ZONE_WIDTH),  # BTC 1h 스킵(분모 밖)
        _entry(symbol="ETH/USDT:USDT", timeframe="15m", filled=False, reason=LEDGER_REASON_NO_FILL),
        # 스킵만 있는 칸 — 결말 표본이 없어 체결률 "-".
        _entry(symbol="SOL/USDT:USDT", timeframe="4h", filled=False, reason=SKIP_REASON_CELL_BUSY),
    ]
    frame = fill_rate_by_cell(entries)
    by_cell = {(r["심볼"], r["TF"]): r for _, r in frame.iterrows()}

    btc = by_cell[("BTC/USDT:USDT", "1h")]
    assert (btc["체결"], btc["미체결"], btc["체결률"]) == (1, 1, "50.0%")
    eth = by_cell[("ETH/USDT:USDT", "15m")]
    assert (eth["체결"], eth["미체결"], eth["체결률"]) == (0, 1, "0.0%")
    sol = by_cell[("SOL/USDT:USDT", "4h")]
    assert (sol["체결"], sol["미체결"], sol["체결률"]) == (0, 0, "-")


def test_reason_distribution_counts_only_no_entry() -> None:
    """분포는 진입·처분 미기록을 빼고 순수 미진입만 세며, 비율의 분모는 미진입 총계다."""
    entries = [
        _entry(filled=True, reason=LEDGER_REASON_ENTERED),
        _entry(filled=True, reason=LEDGER_REASON_UNRECORDED),
        _entry(filled=False, reason=LEDGER_REASON_NO_FILL),
        _entry(filled=False, reason=LEDGER_REASON_NO_FILL),
        _entry(filled=False, reason=SKIP_REASON_ZONE_WIDTH),
        _entry(filled=True, reason=REJECT_CODE_SIZING),  # 체결됐지만 거부 = 미진입
    ]
    frame = reason_distribution(entries)
    rows = {r["사유"]: r for _, r in frame.iterrows()}

    # 미진입 총계 = no_fill 2 + zone_width 1 + sizing 1 = 4.
    assert (rows["미체결(안 닿음)"]["건수"], rows["미체결(안 닿음)"]["비율"]) == (2, "50.0%")
    assert rows["존폭 기각"]["건수"] == 1 and rows["존폭 기각"]["비율"] == "25.0%"
    assert rows["사이징 가드"]["건수"] == 1
    # 진입·미기록은 분포에 없다.
    assert "진입" not in rows
    # 사유가 0인 줄도 남는다(있어야 할 사유가 0이라는 것도 정보).
    assert rows["명목 상한"]["건수"] == 0


def test_reason_distribution_all_zero_when_only_entries() -> None:
    frame = reason_distribution([_entry(filled=True, reason=LEDGER_REASON_ENTERED)])
    assert int(frame["건수"].sum()) == 0
    assert set(frame["비율"]) == {"-"}  # 분모 0이면 비율 대신 대시.


def test_to_funnel_counts_folds_back_to_the_summary() -> None:
    """목록을 되접으면 filled/사유 카운트가 나온다(요약과 목록이 갈라지지 않는 다리)."""
    entries = [
        _entry(filled=True, reason=LEDGER_REASON_ENTERED),
        _entry(filled=True, reason=SKIP_REASON_CELL_BUSY),  # 체결 후 슬롯참 거부
        _entry(filled=False, reason=SKIP_REASON_CELL_BUSY),  # 주문 걸기 전 슬롯참 스킵
        _entry(filled=False, reason=LEDGER_REASON_NO_FILL),
        _entry(filled=False, reason=LEDGER_REASON_DEVIATION),
        _entry(filled=True, reason=REJECT_CODE_NOTIONAL),
    ]
    funnel = to_funnel_counts(entries)
    assert funnel.filled == 3
    assert funnel.no_fill == 1
    assert funnel.deviation == 1
    assert funnel.cell_busy == 2  # 상단 스킵 + 하단 거부 합산.
    assert funnel.notional == 1
    assert funnel.fill_rate == 3 / 4  # 체결 3 / (체결 3 + no_fill 1).


def test_filters_use_the_labels_the_table_shows() -> None:
    entries = [
        _entry(filled=True, reason=LEDGER_REASON_ENTERED),
        _entry(
            symbol="ETH/USDT:USDT",
            timeframe="15m",
            filled=False,
            reason=SKIP_REASON_ZONE_WIDTH,
        ),
    ]
    assert cell_options(entries) == [ALL, "BTC/USDT:USDT · 1h", "ETH/USDT:USDT · 15m"]
    options = reason_options(entries)
    assert options[0] == ALL
    assert "진입" in options and "존폭 기각" in options

    # 칸으로 좁히기.
    only_eth = filter_entries(entries, cell="ETH/USDT:USDT · 15m")
    assert [e.symbol for e in only_eth] == ["ETH/USDT:USDT"]
    # 사유 라벨로 좁히기(표에 찍히는 라벨과 같은 값).
    only_zone = filter_entries(entries, reason="존폭 기각")
    assert [e.reason for e in only_zone] == [SKIP_REASON_ZONE_WIDTH]
    # 전체는 통과.
    assert len(filter_entries(entries)) == 2


def test_ledger_frame_is_readable_and_keeps_unknowns_verbatim() -> None:
    entries = [
        _entry(
            direction="bull",  # 장부는 OrderBlockDirection 값을 저장한다(강세=롱).
            filled=True,
            reason=LEDGER_REASON_ENTERED,
            fill_price=100.5,
            limit_price=100.0,
            penetration_bps=1.234,
        ),
        _entry(direction="bear", filled=False, reason=SKIP_REASON_RETAP),
        _entry(direction="sideways", filled=False, reason="brand_new_reason"),  # 모르는 값
    ]
    frame = ledger_frame(entries, to_kst=lambda ms: "2026-07-31 09:00")

    assert list(frame["방향"]) == ["롱", "숏", "sideways"]  # 모르는 방향은 원문.
    assert list(frame["체결"]) == ["닿음", "안 닿음", "안 닿음"]
    assert list(frame["사유"]) == ["진입", "재탭", "brand_new_reason"]  # 모르는 사유는 원문.
    assert frame["시각(KST)"].iloc[0] == "2026-07-31 09:00"
    assert frame["체결가"].iloc[0] == "100.5" and frame["관통(bp)"].iloc[0] == "1.23"
    assert frame["체결가"].iloc[1] == "-"  # 미체결 행은 가격이 없다.


def test_ledger_frame_keeps_skeleton_when_empty() -> None:
    frame = ledger_frame([])
    assert frame.empty
    assert "사유" in frame.columns and "시각(KST)" in frame.columns


def test_reason_label_keeps_unknown_verbatim() -> None:
    assert reason_label(LEDGER_REASON_ENTERED) == "진입"
    assert reason_label("mystery") == "mystery"


# -- funnel_counts와의 교차검산 (조회 = 재계산 아님) ----------------------------


def _order() -> PendingLimitOrder:
    return PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=1_000,
    )


def _fill_at(time_ms: int) -> LimitFill:
    return LimitFill(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        price=100.0,
        time=time_ms,
        rsi=25.0,
        stop_price=90.0,
        take_profit_price=115.0,
        penetration_bps=0.0,
        waited_ms=60_000,
    )


class _NeverRestsProvider:
    def commit(self, closed_price: float) -> None:
        pass

    def limit_price(self, live_price: float) -> float | None:
        return None

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
        return None


def test_ledger_entries_fold_back_to_funnel_counts(tmp_path: Path) -> None:
    """목록을 되접은 카운트가 `funnel_counts`와 **필드별로 정확히** 같다(WAN-219 교차검산).

    두 경로(행 나열 vs 창 집계)가 같은 분류·창 귀속을 쓴다는 동작 증거다 — 화면이 조회만
    하고 요약을 다시 계산하지 않는다는 것을 보증한다.
    """
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    def place() -> int:
        return journal.record_placed(
            _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
        )

    def skip(reason: str, placed_ms: int) -> None:
        journal.record_skipped(
            session_id=session,
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH.value,
            tap_index=0,
            placed_ms=placed_ms,
            reason=reason,
            zone_start_time=0,
            zone_confirmed_time=1,
        )

    entered = place()
    journal.record_filled(entered, _fill_at(1400))
    journal.record_entry_result(entered, entered=True)
    for ts, code in ((1500, REJECT_CODE_SIZING), (1600, REJECT_CODE_NOTIONAL)):
        rej = place()
        journal.record_filled(rej, _fill_at(ts))
        journal.record_entry_result(rej, entered=False, reason="거부", reason_code=code)
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_EXPIRED, now_ms=1700)  # no_fill
    band = journal.record_placed(
        PendingLimitOrder(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            stop_price=90.0,
            rsi_state=RealtimeRsi(length=3),
            live_limit=_NeverRestsProvider(),
            placed_ms=1_000,
        ),
        session_id=session,
        zone_start_time=0,
        zone_confirmed_time=1,
    )
    journal.record_cancelled(band, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=1750)  # deviation
    skip("zone_width", 1200)
    skip("cell_busy", 1250)
    # 창 밖 — 양쪽에서 똑같이 빠져야 한다.
    late = place()
    journal.record_filled(late, _fill_at(5000))
    journal.record_entry_result(late, entered=True)

    window = {"start_ms": 1000, "end_ms": 2000}
    folded = to_funnel_counts(journal.ledger_entries(**window))
    # `placed`(헤드라인 「예약 N」, WAN-230)는 깔때기 행이 아니라 `placed_ms` 창 귀속의 헤드라인
    # 카운트라 목록에는 없다 — 되접기 교차검산은 사유·체결 필드에 대한 것이고, 그 한 필드만
    # 카빙한다(나머지는 여전히 필드별로 정확히 일치).
    assert replace(journal.funnel_counts(**window), placed=0) == folded
    journal.close()
