"""WAN-409 §3 라이브 인구조사의 자를 **동작으로** 고정한다.

이 §이 지는 방식은 셋이고 전부 조용하다:

1. **조인이 안 붙는데 「무더기가 없다」로 읽힌다** — 주문(`live_limit_orders`)과 라운드트립
   (`paper_trades`)을 붙이는 키가 틀리면 결과를 아는 거래가 0건이 되고, 그게 「라이브는 안
   한다」라는 정반대 결론으로 나간다(WAN-333이 백테 쪽에서 겪은 그 실패).
2. **표본이 없는데 판정한다** — 페이퍼 장부는 2026-09-01에 리셋됐고 기대 건수가 한 자릿수라,
   문턱 미만이면 판정하지 않아야 한다(WAN-194: 안 났다와 못 봤다는 다르다).
3. **안 잰 칸을 0으로 적는다** — 라이브에는 `entry_after_invalidation`에 대응하는 값이 없다.
   0%로 찍으면 「라이브는 무효화 봉에서 체결하지 않는다」가 근거 없이 만들어진다.
"""

from __future__ import annotations

from live.cascade_census import (
    SAMPLE_GATE,
    build_report,
    live_trade_facts,
    render,
)
from live.order_journal import PlacedOrder
from paper.store import PaperTradeRecord
from strategy.models import OrderBlockDirection, SignalExitReason

MINUTE = 60_000
SYMBOL = "BTC/USDT:USDT"
TF = "15m"


def _order(
    *,
    fill_ms: int,
    zone: tuple[int, int] | None = (1_000, 2_000),
    entered: bool = True,
    origin: str = "tap",
    stop_price: float | None = 100.0,
) -> PlacedOrder:
    return PlacedOrder(
        symbol=SYMBOL,
        timeframe=TF,
        direction="long",
        placed_ms=fill_ms - MINUTE,
        status="filled",
        limit_price=101.0,
        fill_ms=fill_ms,
        fill_penetration_bps=None,
        first_rested_ms=fill_ms - MINUTE,
        entry_status="entered" if entered else "rejected",
        entry_reject_reason=None,
        skip_reason=None,
        fill_price=101.0,
        stop_price=stop_price,
        take_profit_price=104.0,
        zone_start_time=None if zone is None else zone[0],
        zone_confirmed_time=None if zone is None else zone[1],
        tap_index=0,
        origin=origin,
    )


def _record(
    *, entry_ms: int, exit_ms: int, stop: bool = True, r_multiple: float | None = -1.2
) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol=SYMBOL,
        timeframe=TF,
        direction=OrderBlockDirection.BULLISH,
        entry_time=entry_ms,
        entry_price=101.0,
        exit_time=exit_ms,
        exit_price=100.0,
        reason=SignalExitReason.STOP_LOSS if stop else SignalExitReason.TAKE_PROFIT,
        gross_pct=-1.0,
        fee_pct=0.04,
        funding_pct=0.0,
        net_pct=-1.04,
        risk_pct=1.0,
        r_multiple=r_multiple,
    )


# --------------------------------------------------------------------------- #
# 1. 조인 — 붙는 것만 세고 못 붙은 것은 지어내지 않는다
# --------------------------------------------------------------------------- #


def test_join_matches_fill_time_to_roundtrip_entry_time() -> None:
    orders = [_order(fill_ms=10 * MINUTE)]
    records = [_record(entry_ms=10 * MINUTE, exit_ms=11 * MINUTE)]
    facts = live_trade_facts(orders, records)
    assert len(facts) == 1
    assert facts[0].cell == (SYMBOL, TF)
    assert facts[0].net_r == -1.2


def test_order_without_a_roundtrip_is_dropped_not_invented() -> None:
    """결과를 모르는 체결은 표에서 빠진다 — 손절/익절을 지어내지 않는다(WAN-194)."""
    assert live_trade_facts([_order(fill_ms=10 * MINUTE)], []) == []


def test_rejected_entry_is_not_a_trade() -> None:
    """체결됐지만 집행 관문에 거부된 주문은 포지션이 아니다(WAN-194의 그 구분)."""
    orders = [_order(fill_ms=10 * MINUTE, entered=False)]
    records = [_record(entry_ms=10 * MINUTE, exit_ms=11 * MINUTE)]
    assert live_trade_facts(orders, records) == []


def test_zone_key_encodes_both_times_and_is_none_when_unknown() -> None:
    """존 식별자 = `{형성, 확정}` — 확정이 형성보다 늦으므로 그 2-집합에서 쌍이 복원된다."""
    facts = live_trade_facts(
        [_order(fill_ms=10 * MINUTE, zone=(1_000, 2_000))],
        [_record(entry_ms=10 * MINUTE, exit_ms=11 * MINUTE)],
    )
    assert facts[0].zone_key == frozenset({1_000, 2_000})

    unknown = live_trade_facts(
        [_order(fill_ms=10 * MINUTE, zone=None)],
        [_record(entry_ms=10 * MINUTE, exit_ms=11 * MINUTE)],
    )
    assert unknown[0].zone_key is None


def test_reentry_origin_is_labelled() -> None:
    facts = live_trade_facts(
        [_order(fill_ms=10 * MINUTE, origin="reentry")],
        [_record(entry_ms=10 * MINUTE, exit_ms=11 * MINUTE)],
    )
    assert facts[0].is_reentry


# --------------------------------------------------------------------------- #
# 2. 표본 문턱 — 못 넘으면 판정하지 않는다
# --------------------------------------------------------------------------- #


def _cascade_pair(index: int, *, zone: tuple[int, int] = (1_000, 2_000)) -> tuple[list, list]:
    """같은 존에서 연달아 손절나는 두 거래 — 두 번째가 무더기 1건이 된다."""
    base = (100 + index * 10) * MINUTE
    orders = [
        _order(fill_ms=base, zone=zone),
        _order(fill_ms=base + MINUTE, zone=zone),
    ]
    records = [
        _record(entry_ms=base, exit_ms=base + MINUTE),
        _record(entry_ms=base + MINUTE, exit_ms=base + 2 * MINUTE),
    ]
    return orders, records


def _many_cascades(count: int) -> tuple[list, list]:
    orders: list = []
    records: list = []
    for index in range(count):
        # 사슬이 이어지면 무더기가 2가 아니라 3, 4…로 커지므로 **존을 갈라** 1건씩 만든다.
        o, r = _cascade_pair(index, zone=(1_000 + index, 2_000 + index))
        orders += o
        records += r
    return orders, records


def test_small_sample_refuses_to_judge() -> None:
    orders, records = _many_cascades(2)
    report = build_report(orders, records)
    assert report.num_cascade == 2
    assert not report.has_sample
    text = render(orders, records, label="테스트")
    assert "판정하지 않는다 — 표본 부족" in text
    assert "(가)" not in text and "(나)" not in text


def test_sample_at_the_gate_does_judge() -> None:
    """문턱을 넘으면 판정 문장이 실제로 나온다 — 문턱이 장식이 아님을 건다."""
    orders, records = _many_cascades(SAMPLE_GATE)
    report = build_report(orders, records)
    assert report.num_cascade == SAMPLE_GATE
    assert report.has_sample
    text = render(orders, records, label="테스트")
    assert "판정하지 않는다" not in text
    assert ("(가)" in text) or ("(나)" in text)


def test_empty_ledger_says_server_only() -> None:
    text = render([], [], label="테스트")
    assert "서버 장부에서만" in text


# --------------------------------------------------------------------------- #
# 3. 안 잰 것을 0으로 적지 않는다
# --------------------------------------------------------------------------- #


def test_render_does_not_print_an_invalidation_bar_column() -> None:
    """라이브에는 대응 필드가 없다 — 0%로 찍으면 정반대 결론이 만들어진다."""
    orders, records = _many_cascades(SAMPLE_GATE)
    text = render(orders, records, label="테스트")
    assert "무효화 봉 체결" not in text
    assert "「무효화 봉 안 체결」 칸이 없다" in text


def test_missing_r_multiple_is_counted_not_zero_filled() -> None:
    """`r_multiple`이 비면 R 평균에서 **빼고 그 사실을 적는다** — 0으로 채우면 본전으로 섞인다."""
    orders, records = _many_cascades(SAMPLE_GATE)
    records[1] = _record(
        entry_ms=records[1].entry_time, exit_ms=records[1].exit_time, r_multiple=None
    )
    report = build_report(orders, records)
    assert report.num_missing_r == 1
    assert report.cascade_mean_r == -1.2  # 남은 거래의 평균 — 0이 섞이지 않았다.
    assert "`r_multiple`이 비어" in render(orders, records, label="테스트")
