"""체결률 실측 장부·요약 리포트 테스트 (WAN-45의 1급 산출물).

체결률의 분모·분자 규칙(대기·재시작 폐기는 분모 제외), 스침(관통 < 5bp) 분류,
재시작 폐기 흐름, 가동 세션 기록, 그리고 `live.fill_report` 렌더를 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from live.fill_report import render_report
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder
from live.order_journal import OrderJournal
from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi


def _order(**kw: object) -> PendingLimitOrder:
    return PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=1_000,
        **kw,  # type: ignore[arg-type]
    )


def _fill(price: float = 100.0, penetration_bps: float = 0.0) -> LimitFill:
    return LimitFill(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        price=price,
        time=61_000,
        rsi=25.0,
        stop_price=90.0,
        take_profit_price=115.0,
        penetration_bps=penetration_bps,
        waited_ms=60_000,
    )


def test_fill_stats_denominator_and_marginal_share(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    def place() -> int:
        return journal.record_placed(
            _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
        )

    # 체결 2건(스침 1 + 관통 1) · 만료 1 · 무효화 1 · 대기 1 · 재시작 폐기 1.
    journal.record_filled(place(), _fill(penetration_bps=0.0))  # 스침(< 5bp)
    journal.record_filled(place(), _fill(penetration_bps=12.0))
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_EXPIRED, now_ms=2)
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_INVALIDATED, now_ms=3)
    place()  # 대기 유지.
    journal.record_discarded(place(), now_ms=4)

    stats = journal.fill_stats()
    assert len(stats) == 1
    s = stats[0]
    assert s.placed == 5  # 폐기 1건 제외.
    assert s.pending == 1
    assert (s.filled, s.cancelled_expired, s.cancelled_invalidated) == (2, 1, 1)
    assert s.discarded_restart == 1
    assert s.resolved == 4  # 대기·폐기는 분모에서 뺀다.
    assert s.fill_rate == 0.5
    assert s.marginal_fills == 1
    assert s.marginal_fill_share == 0.5
    assert s.median_wait_ms == 60_000
    journal.close()


def test_record_cancelled_rejects_non_cancel_status(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    row = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    try:
        journal.record_cancelled(row, LimitOrderStatus.FILLED, now_ms=1)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("체결 상태를 취소로 기록할 수 있어서는 안 된다")
    journal.close()


def test_discard_stale_pending_on_restart(tmp_path: Path) -> None:
    """재시작 정책: 이전 세션의 대기 주문은 복원하지 않고 폐기로 마감한다."""
    journal = OrderJournal(tmp_path / "j.db")
    s1 = journal.start_session(now_ms=0)
    journal.record_placed(_order(), session_id=s1, zone_start_time=0, zone_confirmed_time=1)
    filled = journal.record_placed(
        _order(), session_id=s1, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(filled, _fill())

    # 재시작: 대기 1건만 폐기되고 체결 기록은 그대로다.
    assert journal.discard_stale_pending(now_ms=10_000) == 1
    stats = journal.fill_stats()[0]
    assert stats.discarded_restart == 1
    assert stats.filled == 1
    assert stats.pending == 0
    journal.close()


def test_sessions_track_uptime_spans(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    s1 = journal.start_session(now_ms=1_000)
    journal.heartbeat(s1, now_ms=5_000)
    s2 = journal.start_session(now_ms=100_000)
    journal.heartbeat(s2, now_ms=130_000)

    spans = journal.sessions()
    assert [(sp.started_ms, sp.last_seen_ms) for sp in spans] == [
        (1_000, 5_000),
        (100_000, 130_000),
    ]
    journal.close()


def test_render_report_contains_series_and_uptime(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    journal.heartbeat(session, now_ms=3_600_000)
    row = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(row, _fill(penetration_bps=2.0))

    text = render_report(journal)
    assert "BTC/USDT:USDT" in text
    assert "100.0%" in text  # 체결률 1/1.
    assert "가동" in text
    # 스침 체결(관통 2bp < 5bp)이 표시된다 — `pen_5bp` 렌즈가 부정할 체결.
    assert "| 100.0% |" in text
    journal.close()


def test_render_report_empty_journal(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    text = render_report(journal)
    assert "아직 기록된 주문이 없습니다" in text
    journal.close()
