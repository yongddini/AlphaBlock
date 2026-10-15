"""팔 페이퍼 장부 ↔ 정본 대조(WAN-451 ③) — 장부 읽기 · 목록 · 판정 · 끝에서 끝(실데이터)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from backtest import wan436_stoch_crowd_rules as w
from data.storage import OhlcvStore
from data.universe import rebalance_at
from data.universe_pool import RotationSnapshot, UniverseStore
from live import arm_gate as g
from live import arm_ledger_parity as lp
from live.order_journal import OrderJournal
from paper.store import PaperTradeStore

DAY = 86_400_000
H = 3_600_000
MON = rebalance_at(1_790_000_000_000)


def _snap(t: int, members: list[str]) -> RotationSnapshot:
    return RotationSnapshot(
        rebalance_ms=t,
        computed_ms=t,
        pool_size=len(members),
        stale_size=0,
        ranked=tuple((s, 1.0) for s in members),
        top_n=31,
    )


def _ledger_db(tmp_path: Path) -> Path:
    db = tmp_path / "l.db"
    OhlcvStore(db).close()
    PaperTradeStore(db).close()
    journal = OrderJournal(db)
    conn = journal._conn
    with conn:
        conn.execute(
            "INSERT INTO live_runner_sessions (started_ms, last_seen_ms) VALUES (?, ?)",
            (MON, MON + DAY),
        )
        conn.execute(
            "INSERT INTO live_runner_sessions (started_ms, last_seen_ms) VALUES (?, ?)",
            (MON + 4 * DAY, MON + 5 * DAY),
        )
        conn.execute(
            "INSERT INTO ohlcv (symbol, timeframe, open_time, open, high, low, close, volume,"
            " closed) VALUES ('BTC/USDT:USDT', '1m', ?, 1, 1, 1, 1, 1, 1)",
            (MON + 6 * DAY,),
        )
    journal.close()
    from live.arm_signals import ArmSignal, ArmSignalStore

    sig = ArmSignalStore(db)
    sig.record(ArmSignal("A", "1h", 1, 2, MON + H, 10.0, 9.0, True))
    sig.record(ArmSignal("A", "1h", 3, 4, MON + 2 * H, 10.0, 9.0, False, "arm_stoch"))
    sig.close()
    with UniverseStore(db) as u:
        u.save(_snap(MON, ["A", "B"]))
    return db


def test_read_ledger_takes_counted_signals_and_long_gaps(tmp_path: Path) -> None:
    led = lp.read_ledger(_ledger_db(tmp_path), since=None, until=None)
    assert led.since == MON and led.until == MON + 6 * DAY
    assert led.signals == {("A", "1h", MON + H, 10.0, 9.0)}  # 센 것만
    assert led.gaps == [(MON + DAY, MON + 4 * DAY)]  # 72시간 > 48시간 따라잡기 한도
    assert lp.weekly_members(_ledger_db(tmp_path / "x"), MON, MON + 6 * DAY) == {
        MON: frozenset({"A", "B"})
    }


def _canon_trade(exit_ms: int, *, stopped: bool = True) -> w.PlacedTrade:
    e = np.array([], dtype=np.int64)
    return w.PlacedTrade("A", "1h", 0, exit_ms, 0.0, stopped, False, 1.0, 10.0, 8.0, e, e, e)


def test_open_trade_is_not_an_exit_mismatch_unless_canon_closed_earlier() -> None:
    live = g.LiveTrade("A", "1h", 0, 10.0, 8.0, 1.0, None, None, None)
    until = 10 * H
    truncated = g.compare_trades({("A", "1h", 0): _canon_trade(until - 60_000)}, [live], 0, until)
    assert [lp.open_trade_ok(c, until).problems for c in truncated] == [()]
    closed = g.compare_trades({("A", "1h", 0): _canon_trade(5 * H)}, [live], 0, until)
    assert lp.open_trade_ok(closed[0], until).problems  # 정본은 기간 안에서 청산 — 진짜 어긋남


def test_render_names_the_cause() -> None:
    led = lp.Ledger(0, 10 * DAY, set(), [], {("A", "1h", 5 * DAY): "거부(arm_crowd_skip)"}, [])
    led_gap = lp.Ledger(0, 10 * DAY, set(), [], {}, [(4 * DAY, 6 * DAY)])
    check = g.TradeCheck(("A", "1h", 5 * DAY), None, None, ("정본만 진입",))
    for ledger, cause in ((led, "데이터 또는 로직"), (led_gap, "러너 공백")):
        rep = lp.Report(ledger, {0: frozenset({"A"})}, checks=[check])
        text = lp.render(rep)
        assert cause in text and "어긋남 있음 ❌" in text and not rep.ok


def test_db_workdir_points_relative_path_and_restores(tmp_path: Path) -> None:
    db = tmp_path / "real.db"
    db.write_bytes(b"x")
    before = Path.cwd()
    with lp.db_workdir(db):
        assert Path("data/ohlcv.db").resolve() == db.resolve()
    assert Path.cwd() == before


# ------------------------------------------------------------------ 끝에서 끝(실데이터)

_REAL_DB = Path("data/ohlcv.db")


def test_real_runner_ledger_matches_the_canon(tmp_path: Path) -> None:
    """실제 러너가 장부를 쓰게 한 뒤(UNI·BTC 2주) 이 도구로 정본과 맞대면 일치한다."""
    from config.settings import Settings
    from execution.engine import build_execution_engine
    from live.arm_rules import ArmParams
    from live.arm_signals import ArmSignalStore
    from live.executor import PaperExecutor
    from live.limit_engine import ZoneLimitLiveEngine
    from live.zone_limit_runner import (
        ARM_FILL_PENETRATION_BPS,
        ARM_RISK_PER_TRADE,
        ZoneLimitPaperRunner,
        build_paper_recorder,
    )

    syms = ["UNI/USDT:USDT", "BTC/USDT:USDT"]
    start = 1_737_936_000_000  # 2025-01-27(월)
    cap = start + 21 * DAY
    if not _REAL_DB.exists():
        pytest.skip("실데이터(data/ohlcv.db) 없음")
    try:
        have = (
            sqlite3.connect(_REAL_DB)
            .execute(
                "SELECT MAX(open_time) FROM ohlcv"
                " WHERE symbol = 'UNI/USDT:USDT' AND timeframe = '1m'"
            )
            .fetchone()[0]
        )
    except sqlite3.DatabaseError:
        pytest.skip("실데이터 DB를 못 읽음")
    if not have or have < cap:
        pytest.skip("UNI 1분봉 없음")
    db = tmp_path / "server.db"
    g.make_clipped_db(_REAL_DB.resolve(), db, syms, start - 800 * DAY, start - 2 * DAY, cap)
    with UniverseStore(db) as u:
        for t in range(rebalance_at(start), cap, 7 * DAY):
            u.save(_snap(t, syms))
    from data.universe import ARM_TIMEFRAMES
    from live.arm_signals import ArmSignal

    series = [(s, tf) for s in syms for tf in ARM_TIMEFRAMES]

    def replay(ledger: Path) -> None:
        settings = Settings(
            db_path=str(ledger),
            confluence={"retap_mode": "once", "fill_penetration_bps": ARM_FILL_PENETRATION_BPS},
            risk_sizing={"risk_per_trade": ARM_RISK_PER_TRADE},
        )
        journal = OrderJournal(ledger)
        session = journal.start_session(now_ms=start)
        paper = PaperTradeStore(ledger)
        store = OhlcvStore(db)
        universe = UniverseStore(db)
        runner = ZoneLimitPaperRunner(
            store=store,
            engine=ZoneLimitLiveEngine(
                params=settings.confluence,
                journal=journal,
                session_id=session,
            ),
            journal=journal,
            session_id=session,
            executor=PaperExecutor(
                engine=build_execution_engine(settings),
                store=paper,
                recorder=build_paper_recorder(paper, settings),
                sizing=settings.risk_sizing,
            ),
            params=settings.confluence,
            series=series,
            lookback_bars=settings.live_signal_lookback_bars,
            poll_interval_seconds=0,
            arm=ArmParams(),
            universe=universe,
            signals=ArmSignalStore(ledger),
            bootstrap_days=730,
        )
        for key in series:
            runner._last_substep[key] = start - 60_000
            runner.poll_series(*key)
        journal.heartbeat(session, now_ms=cap)
        for closable in (store, universe, journal, paper):
            closable.close()

    # 몰림 규칙은 다른 칸의 신호를 센다 — 1차로 신호 장부를 다 채우고, 2차가 그 장부로 판정한다.
    first = tmp_path / "pass1.db"
    replay(first)
    seed = ArmSignalStore(db)
    for row in sqlite3.connect(first).execute(
        "SELECT symbol, timeframe, zone_start, zone_confirmed, fill_ms, price, ob_stop, counted,"
        " reason_code FROM arm_signals"
    ):
        seed.record(ArmSignal(*row))
    seed.close()
    replay(db)
    report = lp.run(db, since=start, until=cap - 7 * DAY, jobs=min(4, os.cpu_count() or 1))
    print(lp.render(report))
    assert report.checks, lp.render(report)
    assert report.ok, lp.render(report)


def test_orders_tapped_before_the_window_are_left_out(tmp_path: Path) -> None:
    """러너 첫 기동은 형성 중인 상위TF 봉부터 소비해 기간 시작 전 탭에도 주문을 건다 — 그 체결은
    정본(기간 뒤 탭만)에 없으니 양쪽에서 뺀다(안 빼면 「장부만」 어긋남으로 둔갑한다)."""
    db = _ledger_db(tmp_path)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction, placed_ms,"
            " status, fill_ms) VALUES (1, 'A', '1h', 'bull', ?, 'filled', ?)",
            (MON - H, MON + H),
        )
    conn.close()
    led = lp.read_ledger(db, since=None, until=None)
    assert led.signals == set() and led.pre_session == 1
    rep = lp.Report(led, {MON: frozenset({"A"})})
    assert "기간 시작 전에 탭된 주문의 체결 1건" in lp.render(rep)
