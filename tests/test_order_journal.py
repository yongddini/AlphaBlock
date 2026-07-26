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


def _fill_at(time_ms: int) -> LimitFill:
    """체결 시각만 다른 체결(처분 미기록 창 자르기 테스트용, WAN-194)."""
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


# -- 체결의 하류 처분 (WAN-194) ------------------------------------------------


def test_entry_result_separates_rejected_from_unrecorded(tmp_path: Path) -> None:
    """진입/거부/미기록이 **세 갈래로 구분**된다 — 이 구분이 WAN-194의 사고 원인이었다.

    거부는 정상 동작(가드가 걸렀다)이고 미기록은 유실 신호다. 둘이 같은 모양이면
    "체결됐는데 포지션이 없다"가 DB 손상과 구분되지 않는다.
    """
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    def place() -> int:
        return journal.record_placed(
            _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
        )

    entered = place()
    journal.record_filled(entered, _fill())
    journal.record_entry_result(entered, entered=True)

    rejected = place()
    journal.record_filled(rejected, _fill())
    journal.record_entry_result(rejected, entered=False, reason="사이징 수량 0 — 진입 스킵")

    unrecorded = place()  # 체결만 남고 처분 기록 없음(= 두 쓰기 사이에서 죽은 모양).
    journal.record_filled(unrecorded, _fill())

    stats = journal.fill_stats()[0]
    assert stats.filled == 3
    assert (stats.entered, stats.entry_rejected, stats.entry_unrecorded) == (1, 1, 1)
    # 전환율의 분모는 처분이 정해진 것만이다(미기록은 결과를 모른다).
    assert stats.entry_rate == 0.5
    # 체결률은 진입 거부에 영향받지 않는다 — 두 값은 곱해 읽는 별개의 자다.
    assert stats.fill_rate == 1.0

    orphans = journal.orphan_fills()
    assert [o.journal_id for o in orphans] == [unrecorded]
    journal.close()


def test_entry_rejection_reason_is_persisted(tmp_path: Path) -> None:
    """거부 사유가 DB에 남는다 — 로그를 안 봐도 장부만으로 왜인지 알 수 있어야 한다."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    row = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(row, _fill())
    journal.record_entry_result(row, entered=False, reason="사이징 수량 0 — 진입 스킵")

    stored = journal._conn.execute(  # noqa: SLF001 — 열이 실제로 찍혔는지 보는 회귀 테스트.
        "SELECT entry_status, entry_reject_reason FROM live_limit_orders WHERE id = ?", (row,)
    ).fetchone()
    assert stored == ("rejected", "사이징 수량 0 — 진입 스킵")

    # 사유를 안 주더라도 빈 문자열로 남기지 않는다(왜 거부됐는지 모르는 행 방지).
    other = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_entry_result(other, entered=False)
    reason = journal._conn.execute(  # noqa: SLF001
        "SELECT entry_reject_reason FROM live_limit_orders WHERE id = ?", (other,)
    ).fetchone()[0]
    assert reason == "사유 미기록"
    journal.close()


def test_orphan_fills_since_filters_pre_migration_rows(tmp_path: Path) -> None:
    """`since_ms`는 열 도입 전 기록(전부 NULL)을 유실과 가른다."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    old = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(old, _fill())  # fill.time = 61_000
    new = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(new, _fill_at(500_000))

    assert [o.journal_id for o in journal.orphan_fills()] == [old, new]
    assert [o.journal_id for o in journal.orphan_fills(since_ms=100_000)] == [new]
    journal.close()


def test_journal_migrates_legacy_schema_without_entry_columns(tmp_path: Path) -> None:
    """옛 DB(서버가 그 상태다)에 열을 덧붙인다 — 없으면 러너가 첫 체결에서 죽는다.

    `CREATE TABLE IF NOT EXISTS`는 이미 있는 테이블의 열을 늘려 주지 않으므로, 이
    마이그레이션이 없으면 새 코드가 옛 DB에서 `OperationalError`를 낸다.
    """
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    with conn:  # WAN-45 시절 스키마(entry_* 열 없음) + 기존 체결 1행.
        conn.execute(
            "CREATE TABLE live_limit_orders ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,"
            " symbol TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL,"
            " zone_start_time INTEGER, zone_confirmed_time INTEGER,"
            " tap_index INTEGER NOT NULL DEFAULT 0, placed_ms INTEGER NOT NULL,"
            " status TEXT NOT NULL, terminal_ms INTEGER, first_rested_ms INTEGER,"
            " last_limit_price REAL, fill_ms INTEGER, fill_price REAL, fill_rsi REAL,"
            " fill_penetration_bps REAL, stop_price REAL, take_profit_price REAL,"
            " wait_ms INTEGER)"
        )
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
            " placed_ms, status, fill_ms, fill_price)"
            " VALUES (1, 'LINK/USDT:USDT', '15m', 'bullish', 10, 'filled', 20, 8.3586714)"
        )
    conn.close()

    journal = OrderJournal(path)  # 마이그레이션이 여기서 돈다.
    # 옛 행은 처분 미기록으로 잡히고(판별 불가), 새 기록은 정상 동작한다.
    assert [o.symbol for o in journal.orphan_fills()] == ["LINK/USDT:USDT"]
    row = journal.record_placed(_order(), session_id=1, zone_start_time=0, zone_confirmed_time=1)
    journal.record_filled(row, _fill())
    journal.record_entry_result(row, entered=True)
    assert journal.fill_stats()[0].entered == 1
    journal.close()

    # 두 번 열어도 ALTER를 다시 걸지 않는다(멱등).
    again = OrderJournal(path)
    assert len(again.orphan_fills()) == 1
    again.close()


def test_render_report_flags_orphan_fills(tmp_path: Path) -> None:
    """리포트가 처분 미기록 체결을 별도 섹션으로 드러낸다(조용한 통과 금지)."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    row = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(row, _fill())

    text = render_report(journal)
    assert "처분 미기록" in text
    assert "**1건**" in text

    journal.record_entry_result(row, entered=True)
    clean = render_report(journal)
    assert "없음 — 모든 체결에 진입/거부 처분이 남아 있습니다." in clean
    journal.close()
