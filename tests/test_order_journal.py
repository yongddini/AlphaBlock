"""체결률 실측 장부·요약 리포트 테스트 (WAN-45의 1급 산출물).

체결률의 분모·분자 규칙(대기·재시작 폐기는 분모 제외), 스침(관통 < 5bp) 분류,
재시작 폐기 흐름, 가동 세션 기록, 그리고 `live.fill_report` 렌더를 검증한다.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from execution.engine import (
    REJECT_CODE_CELL_BUSY,
    REJECT_CODE_NOTIONAL,
    REJECT_CODE_RISK,
    REJECT_CODE_SIZING,
)
from live.fill_report import render_report
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder
from live.order_journal import (
    LEDGER_REASON_DEVIATION,
    LEDGER_REASON_ENTERED,
    LEDGER_REASON_NO_FILL,
    LEDGER_REASON_UNRECORDED,
    SKIP_REASON_CELL_BUSY,
    SKIP_REASON_RETAP,
    SKIP_REASON_ZONE_WIDTH,
    OrderJournal,
)
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


class _NeverRestsProvider:
    """밴드가 한 번도 유리하지 않은 라이브 지정가 공급자(볼린저 규칙 3 계속 기각).

    `limit_price`가 항상 None이라 주문판에 실린 적이 없어 `first_rested_ms`가 NULL로 남는다
    — deviation(밴드 기각) 만료의 실측 모양이다(WAN-217)."""

    def commit(self, closed_price: float) -> None:
        pass

    def limit_price(self, live_price: float) -> float | None:
        return None

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
        return None


def test_record_skipped_persists_reason_and_stays_out_of_denominator(tmp_path: Path) -> None:
    """주문 걸기 전 걸러진 셋업이 `skip_reason` 행으로 남고, 체결률 분모 밖이다(WAN-217)."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    def skip(reason: str) -> int:
        return journal.record_skipped(
            session_id=session,
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH.value,
            tap_index=0,
            placed_ms=1_000,
            reason=reason,
            zone_start_time=0,
            zone_confirmed_time=1,
        )

    skip("zone_width")
    skip("zone_width")
    skip("cell_busy")
    skip("retap")
    # 실제로 걸린 주문 하나와 섞여도 분리되는지 본다.
    placed = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(placed, _fill())
    journal.record_entry_result(placed, entered=True)

    s = journal.fill_stats()[0]
    assert (s.skipped_zone_width, s.skipped_cell_busy, s.skipped_retap) == (2, 1, 1)
    assert s.skipped == 4
    assert s.placed == 1  # 스킵 4건은 주문이 걸린 적 없어 placed에서 뺀다.
    assert s.filled == 1
    assert s.resolved == 1  # 스킵은 체결률 분모 밖(대기·폐기와 같은 부류가 아니라 더 위다).

    rows = journal._conn.execute(  # noqa: SLF001 — 열이 실제로 찍혔는지 보는 회귀 테스트.
        "SELECT status, skip_reason FROM live_limit_orders WHERE status = 'skipped' ORDER BY id"
    ).fetchall()
    assert rows == [
        ("skipped", "zone_width"),
        ("skipped", "zone_width"),
        ("skipped", "cell_busy"),
        ("skipped", "retap"),
    ]
    journal.close()


def test_unfilled_no_band_separates_deviation_from_pure_no_fill(tmp_path: Path) -> None:
    """밴드가 한 번도 유리하지 않은 만료(deviation)와 걸렸다 안 닿은 만료(no_fill)를
    `first_rested_ms`로 가른다(WAN-217)."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    # live_limit이 규칙 3을 계속 기각 → first_rested_ms NULL인 채 만료(deviation).
    band_rejected = PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        live_limit=_NeverRestsProvider(),
        placed_ms=1_000,
    )
    row = journal.record_placed(
        band_rejected, session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_cancelled(row, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=2)

    # 정적 지정가는 예약 즉시 주문판에 걸린다(first_rested_ms=placed_ms) → 순수 no_fill.
    other = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_cancelled(other, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=3)

    s = journal.fill_stats()[0]
    assert s.cancelled_expired == 2
    assert s.unfilled_no_band == 1  # 밴드 규칙 3 기각(deviation).
    assert s.no_fill == 1  # 걸렸다 안 닿은 순수 미체결.
    journal.close()


def test_reject_code_shares_vocabulary_with_skip_reasons() -> None:
    """체결 후 거부 코드(`REJECT_CODE_*`)가 깔때기 상단 스킵 어휘와 같은 라벨을 쓴다(WAN-221).

    엔진(execution)과 장부(live)가 서로를 import하지 않으므로 두 상수가 우연히 어긋날 수
    있다 — 이 테스트가 `cell_busy` 라벨의 일치를 동작으로 고정한다(funnel이 상단·하단
    슬롯참을 합산하는 근거)."""
    assert REJECT_CODE_CELL_BUSY == SKIP_REASON_CELL_BUSY == "cell_busy"


def test_entry_reject_code_is_persisted(tmp_path: Path) -> None:
    """거부 **코드**가 자유 텍스트와 별도 열에 남는다(WAN-221 — funnel이 파싱 없이 집계)."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    row = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(row, _fill())
    journal.record_entry_result(
        row, entered=False, reason="사이징 수량 0 — 진입 스킵", reason_code=REJECT_CODE_SIZING
    )
    stored = journal._conn.execute(  # noqa: SLF001 — 열이 실제로 찍혔는지 보는 회귀 테스트.
        "SELECT entry_reject_reason, entry_reject_code FROM live_limit_orders WHERE id = ?", (row,)
    ).fetchone()
    assert stored == ("사이징 수량 0 — 진입 스킵", "sizing")

    # 진입 성공이면 코드도 비운다(거부 코드가 성공 행에 남지 않는다).
    ok = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(ok, _fill())
    journal.record_entry_result(ok, entered=True)
    code = journal._conn.execute(  # noqa: SLF001
        "SELECT entry_reject_code FROM live_limit_orders WHERE id = ?", (ok,)
    ).fetchone()[0]
    assert code is None
    journal.close()


def test_funnel_counts_windows_and_buckets_reasons(tmp_path: Path) -> None:
    """일일 요약의 원자료: 사유마다 자기 시각으로 창에 넣고, 코드로 명목/사이징/슬롯참을
    가른다(WAN-221). 체결률 = 체결 ÷ (체결 + no_fill)."""
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

    # -- 창 [1000, 2000) 안 --
    entered = place()  # 체결 + 진입 성공(체결률 분자).
    journal.record_filled(entered, _fill_at(1400))
    journal.record_entry_result(entered, entered=True)
    for ts, code in (
        (1500, REJECT_CODE_SIZING),
        (1600, REJECT_CODE_NOTIONAL),
        (1700, REJECT_CODE_CELL_BUSY),
    ):
        rej = place()  # 체결됐으나 집행 계층이 거부 — 체결로 세되 사유로도 센다.
        journal.record_filled(rej, _fill_at(ts))
        journal.record_entry_result(rej, entered=False, reason="거부", reason_code=code)
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_EXPIRED, now_ms=1800)  # no_fill
    band = journal.record_placed(  # deviation(밴드 규칙 3): first_rested_ms NULL.
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
    journal.record_cancelled(band, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=1850)
    skip("zone_width", 1200)
    skip("cell_busy", 1250)
    skip("retap", 1300)

    # -- 창 밖(제외돼야 한다) --
    late = place()
    journal.record_filled(late, _fill_at(5000))
    journal.record_entry_result(late, entered=True)
    skip("zone_width", 5000)
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_EXPIRED, now_ms=5000)  # no_fill 밖

    f = journal.funnel_counts(start_ms=1000, end_ms=2000)
    assert f.filled == 4  # 진입 성공 1 + 거부 3(모두 창 안). 창 밖 체결은 제외.
    assert f.no_fill == 1
    assert f.deviation == 1
    assert f.zone_width == 1  # 창 밖 존폭 스킵 제외.
    assert f.cell_busy == 2  # 상단 스킵 1 + 하단 거부 1.
    assert f.retap == 1
    assert f.notional == 1
    assert f.sizing == 1
    assert f.other == 0
    assert f.fill_rate == 4 / 5  # 체결 4 / (체결 4 + no_fill 1).
    journal.close()


def test_funnel_counts_empty_ledger_is_safe(tmp_path: Path) -> None:
    """빈 장부에서도 깨지지 않고 fill_rate는 None이다(WAN-221 완료 기준)."""
    journal = OrderJournal(tmp_path / "j.db")
    journal.start_session(now_ms=0)
    f = journal.funnel_counts(start_ms=0, end_ms=1_000)
    assert (f.filled, f.no_fill, f.zone_width, f.cell_busy, f.notional, f.sizing) == (
        0,
        0,
        0,
        0,
        0,
        0,
    )
    assert f.fill_rate is None
    journal.close()


def test_funnel_counts_uncoded_rejection_falls_to_other(tmp_path: Path) -> None:
    """코드가 없는 거부(리스크 한도·WAN-221 이전 행)는 `other`로 떨어진다."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    row = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(row, _fill_at(1500))
    journal.record_entry_result(
        row, entered=False, reason="일일 손실 서킷브레이커", reason_code=REJECT_CODE_RISK
    )
    f = journal.funnel_counts(start_ms=1000, end_ms=2000)
    assert f.filled == 1
    assert (f.sizing, f.notional, f.cell_busy) == (0, 0, 0)
    assert f.other == 1
    journal.close()


def _band_rejected_order() -> PendingLimitOrder:
    """밴드가 한 번도 유리하지 않아 first_rested_ms NULL로 만료되는 주문(deviation)."""
    return PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        live_limit=_NeverRestsProvider(),
        placed_ms=1_000,
    )


def test_ledger_entries_windows_classifies_and_excludes_non_funnel(tmp_path: Path) -> None:
    """진입 깔때기 행을 창·사유로 나열하되 깔때기 밖 상태는 뺀다(WAN-219).

    `funnel_counts`와 같은 모집단·창 귀속·분류를 쓴다(집계 일치의 교차검산은 표시 계층
    테스트가 맡는다). 여기서는 사유 분류·창 자르기·깔때기 밖 제외·정렬을 직접 고정한다.
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

    # -- 창 [1000, 2000) 안: 깔때기 행 8개 --
    entered = place()
    journal.record_filled(entered, _fill_at(1400))
    journal.record_entry_result(entered, entered=True)
    rej = place()
    journal.record_filled(rej, _fill_at(1500))
    journal.record_entry_result(rej, entered=False, reason="사이징", reason_code=REJECT_CODE_SIZING)
    orphan = place()  # 체결만 남고 처분 미기록.
    journal.record_filled(orphan, _fill_at(1550))
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_EXPIRED, now_ms=1600)  # no_fill
    band = journal.record_placed(
        _band_rejected_order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_cancelled(band, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=1650)  # deviation
    skip("zone_width", 1200)
    skip("cell_busy", 1250)
    skip("retap", 1300)

    # -- 창 안이지만 깔때기 밖 상태: 나열되지 않아야 한다 --
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_INVALIDATED, now_ms=1700)
    journal.record_cancelled(place(), LimitOrderStatus.CANCELLED_CONDITION_FAILED, now_ms=1750)
    place()  # 대기 유지.
    journal.record_discarded(place(), now_ms=1800)

    # -- 창 밖 체결: 제외 --
    late = place()
    journal.record_filled(late, _fill_at(5000))
    journal.record_entry_result(late, entered=True)

    entries = journal.ledger_entries(start_ms=1000, end_ms=2000)

    # 사건 시각순 정렬.
    assert [e.event_ms for e in entries] == sorted(e.event_ms for e in entries)
    # 사유 분류가 funnel_counts와 같은 어휘로 찍힌다.
    assert Counter(e.reason for e in entries) == Counter(
        {
            LEDGER_REASON_ENTERED: 1,
            REJECT_CODE_SIZING: 1,
            LEDGER_REASON_UNRECORDED: 1,
            LEDGER_REASON_NO_FILL: 1,
            LEDGER_REASON_DEVIATION: 1,
            SKIP_REASON_ZONE_WIDTH: 1,
            SKIP_REASON_CELL_BUSY: 1,
            SKIP_REASON_RETAP: 1,
        }
    )
    # 무효화·조건취소·대기·재시작 폐기는 깔때기 밖이라 없다.
    assert all(
        e.reason
        not in (
            "cancelled_invalidated",
            "cancelled_condition_failed",
            "pending",
            "discarded_restart",
        )
        for e in entries
    )
    # 체결 플래그는 지정가가 닿은 3건(진입·거부·미기록)만 True.
    assert sum(1 for e in entries if e.filled) == 3
    assert sum(1 for e in entries if e.entered) == 1  # entered 프로퍼티.
    # 창 밖 체결(5000)은 나열되지 않는다.
    assert all(e.event_ms < 2000 for e in entries)
    # 체결 행은 체결가·관통을 싣고, 스킵 행은 안 싣는다.
    entered_entry = next(e for e in entries if e.entered)
    assert entered_entry.fill_price == 100.0
    skip_entry = next(e for e in entries if e.reason == SKIP_REASON_ZONE_WIDTH)
    assert skip_entry.fill_price is None and skip_entry.penetration_bps is None
    journal.close()


def test_ledger_entries_empty_ledger_is_safe(tmp_path: Path) -> None:
    """빈 장부에서도 예외 없이 빈 목록을 돌려준다(WAN-219 빈 장부 렌더의 근거)."""
    journal = OrderJournal(tmp_path / "j.db")
    assert journal.ledger_entries(start_ms=0, end_ms=10_000) == []
    journal.close()


def test_render_report_shows_skip_funnel(tmp_path: Path) -> None:
    """리포트가 주문 걸기 전 미진입 사유를 별도 섹션으로 드러낸다(WAN-217)."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    journal.record_skipped(
        session_id=session,
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH.value,
        tap_index=0,
        placed_ms=1_000,
        reason="zone_width",
        zone_start_time=0,
        zone_confirmed_time=1,
    )
    text = render_report(journal)
    assert "주문 걸기 전 미진입 사유" in text
    assert "존폭기각" in text
    journal.close()
