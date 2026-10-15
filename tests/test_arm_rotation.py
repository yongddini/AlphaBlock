"""팔의 종목 목록 게이트 · 감시 시리즈 교체(WAN-451 ③) — 목록 밖은 새 진입만 막는다."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config.settings import Settings
from data.storage import OhlcvStore
from data.universe import ARM_TIMEFRAMES, WEEK_MS
from data.universe_pool import RotationSnapshot, UniverseStore
from execution.engine import EntryIntent, build_execution_engine
from live import arm_rules as ar
from live.engine_state import EngineStateStore
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.zone_limit_runner import ZoneLimitPaperRunner, build_paper_recorder
from paper.store import PaperTradeStore
from strategy.models import OrderBlockDirection
from tests.test_arm_rules import TF, _fill_event, _seed

DAY = 86_400_000
#: `_fill_event`의 체결 시각(15m 봉 60 + 2분)이 속한 주의 월요일 00:00 UTC.
FILL_MS = 60 * TF + 2 * 60_000
MONDAY = FILL_MS - ((FILL_MS - 4 * DAY) % WEEK_MS)


def _snap(t: int, members: list[str]) -> RotationSnapshot:
    return RotationSnapshot(
        rebalance_ms=t,
        computed_ms=t,
        pool_size=len(members),
        stale_size=0,
        ranked=tuple((s, 1.0) for s in members),
    )


def test_rotation_decision_blocks_outsiders_and_unknown_weeks() -> None:
    m = frozenset({"A"})
    assert ar.rotation_decision(MONDAY, m, "A", FILL_MS) is None
    out = ar.rotation_decision(MONDAY, m, "B", FILL_MS)
    assert out is not None and out.reason_code == ar.REJECT_CODE_ARM_ROTATION
    stale = ar.rotation_decision(MONDAY - WEEK_MS, m, "A", FILL_MS)  # 지난주 목록으로 대신 안 한다
    assert stale is not None and stale.reason_code == ar.REJECT_CODE_ARM_ROTATION_UNKNOWN
    none = ar.rotation_decision(None, frozenset(), "A", FILL_MS)
    assert none is not None and none.reason_code == ar.REJECT_CODE_ARM_ROTATION_UNKNOWN


def _runner(db: Path, now: list[int]) -> tuple[ZoneLimitPaperRunner, OrderJournal, int]:
    settings = Settings(db_path=str(db))
    paper = PaperTradeStore(db)
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper,
        recorder=build_paper_recorder(paper, settings),
        sizing=settings.risk_sizing,
    )
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    runner = ZoneLimitPaperRunner(
        store=OhlcvStore(db),
        engine=ZoneLimitLiveEngine(params=settings.confluence, journal=journal, session_id=session),
        journal=journal,
        session_id=session,
        executor=executor,
        params=settings.confluence,
        series=[],
        lookback_bars=100,
        poll_interval_seconds=0,
        engine_state=EngineStateStore(db),
        arm=ar.ArmParams(),
        universe=UniverseStore(db),
        now_ms=lambda: now[0],
    )
    return runner, journal, session


def test_series_follow_the_weekly_list_but_keep_held_cells(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    now = [MONDAY + DAY]
    with UniverseStore(db) as u:
        u.save(_snap(MONDAY, ["A", "B"]))
    runner, _, _ = _runner(db, now)
    added, removed = runner.sync_series(now[0])
    assert len(added) == 2 * len(ARM_TIMEFRAMES) and removed == []
    # B 4h에 포지션을 연다 → 다음 주 목록에서 빠져도 그 칸만 남는다
    report = runner._executor.enter(
        EntryIntent(
            symbol="B",
            timeframe="4h",
            direction=OrderBlockDirection.BULLISH,
            entry_price=100.0,
            entry_time=now[0],
            stop_price=90.0,
        ),
        now_ms=now[0],
    )
    assert report.accepted
    with UniverseStore(db) as u:
        u.save(_snap(MONDAY + WEEK_MS, ["A", "C"]))
    now[0] = MONDAY + WEEK_MS + 60_000
    added, removed = runner.sync_series(now[0])
    assert {s for s, _ in added} == {"C"}
    assert ("B", "4h") not in removed and ("B", "1h") in removed
    series = set(runner._series)
    assert ("B", "4h") in series and ("B", "1h") not in series
    assert {s for s, _ in series} == {"A", "B", "C"}
    assert runner.sync_series(now[0]) == ([], [])  # 바뀐 게 없으면 아무것도 안 한다


def test_dropped_series_lose_their_engine_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "e.db"
    now = [MONDAY + DAY]
    with UniverseStore(db) as u:
        u.save(_snap(MONDAY, ["A"]))
        u.save(_snap(MONDAY + WEEK_MS, ["Z"]))
    runner, _, _ = _runner(db, now)
    runner.sync_series(now[0])
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO live_engine_state (symbol, timeframe, saved_ms, state_json) "
        "VALUES ('A', '1h', 0, '{}')"
    )
    conn.commit()
    now[0] = MONDAY + WEEK_MS + 1
    runner.sync_series(now[0])
    assert conn.execute("SELECT COUNT(*) FROM live_engine_state WHERE symbol='A'").fetchone() == (
        0,
    )


def _fill_rejection(db: Path) -> tuple[str, str]:
    row = (
        sqlite3.connect(db)
        .execute("SELECT entry_status, entry_reject_code FROM live_limit_orders")
        .fetchone()
    )
    return (str(row[0]), str(row[1]))


def test_fill_outside_this_weeks_list_is_rejected(tmp_path: Path) -> None:
    db = tmp_path / "g.db"
    _seed(db, falling=True)  # %K 낮음 — 목록 게이트가 아니면 진입했을 체결
    with UniverseStore(db) as u:
        u.save(_snap(MONDAY, ["OTHER/USDT:USDT"]))
    now = [FILL_MS]
    runner, journal, session = _runner(db, now)
    runner._handle_events([_fill_event(journal, session, price=140.0, ob_stop=133.0)])
    assert not runner._executor.open_positions
    assert _fill_rejection(db) == ("rejected", ar.REJECT_CODE_ARM_ROTATION)


def test_fill_before_this_weeks_list_is_saved_is_rejected(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    _seed(db, falling=True)
    with UniverseStore(db) as u:
        u.save(_snap(MONDAY - WEEK_MS, ["SYN/USDT:USDT"]))  # 지난주 목록뿐
    runner, journal, session = _runner(db, [FILL_MS])
    runner._handle_events([_fill_event(journal, session, price=140.0, ob_stop=133.0)])
    assert not runner._executor.open_positions
    assert _fill_rejection(db) == ("rejected", ar.REJECT_CODE_ARM_ROTATION_UNKNOWN)


def test_member_fill_goes_on_to_the_arm_rules(tmp_path: Path) -> None:
    db = tmp_path / "i.db"
    _seed(db, falling=True)
    with UniverseStore(db) as u:
        u.save(_snap(MONDAY, ["SYN/USDT:USDT"]))
    runner, journal, session = _runner(db, [FILL_MS])
    runner._handle_events([_fill_event(journal, session, price=140.0, ob_stop=133.0)])
    (pos,) = runner._executor.open_positions
    assert pos.stop_price == 126.0 and pos.take_profit_price is None
