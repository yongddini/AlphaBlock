"""러너 읽기 재사용(WAN-451 ④) — 빨라져도 결과는 비트 동일 · 저장이 바뀌면 다시 읽는다.

서버 실측(팔 310 시리즈)에서 매분 폴링이 50초였고 그 59%가 같은 상위TF 창 재로드였다.
재사용을 켜고 끈 두 러너를 같은 데이터에 돌려 **이벤트 · 주문 장부 · 엔진 상태 스냅샷**이
글자 그대로 같은지 본다(빨라졌는데 숫자가 다르면 버그다 — WAN-203 선례).
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from config.settings import Settings
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from execution.leverage import book_per_trade_sizing
from live.arm_rules import ArmParams
from live.engine_state import EngineStateStore
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.zone_limit_runner import ZoneLimitPaperRunner, build_paper_recorder
from paper.store import PaperTradeStore
from scripts import wan451_runner_memory_probe as probe

M = 60_000
TFS = ("15m", "1h", "2h", "4h", "1w")
WEEK = 7 * 86_400_000


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str], int]:
    db = tmp_path_factory.mktemp("seed") / "seed.db"
    syms = probe._symbols(4)
    end_ms = (int(time.time() * 1000) // WEEK) * WEEK
    # 시드 458: 이 크기에서 실제로 주문이 걸리고 체결된다(「아무 일 없어서 같다」 방지).
    probe._seed(db, syms, TFS, end_ms=end_ms, lookback=400, minute_bars=2880, seed=458)
    return db, syms, end_ms


def _runner(
    db: Path, syms: list[str], end_ms: int, clock: list[int], *, reuse: bool
) -> ZoneLimitPaperRunner:
    settings = Settings(db_path=str(db))
    paper = PaperTradeStore(db)
    book = settings.live_leverage_book
    executor = PaperExecutor(
        engine=build_execution_engine(settings, leverage_book=book),
        store=paper,
        recorder=build_paper_recorder(paper, settings),
        sizing=book_per_trade_sizing(settings.risk_sizing, book),
    )
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=end_ms)
    engine = ZoneLimitLiveEngine(params=settings.confluence, journal=journal, session_id=session)
    return ZoneLimitPaperRunner(
        store=OhlcvStore(db),
        engine=engine,
        journal=journal,
        session_id=session,
        executor=executor,
        params=settings.confluence,
        series=[(s, tf) for s in syms for tf in TFS],
        lookback_bars=400,
        poll_interval_seconds=0,
        engine_state=EngineStateStore(db),
        arm=ArmParams(),
        now_ms=lambda: clock[0],
        reuse_loads=reuse,
    )


def _drive(
    seed_db: Path, tmp: Path, syms: list[str], end_ms: int, *, reuse: bool
) -> tuple[list[tuple[str, str, str]], list[tuple[object, ...]], list[tuple[object, ...]]]:
    db = tmp / ("reuse.db" if reuse else "fresh.db")
    shutil.copy(seed_db, db)
    clock = [end_ms]
    runner = _runner(db, syms, end_ms, clock, reuse=reuse)
    events = list(runner.poll_once())
    for k in range(6):
        t = end_ms + k * M
        probe._append_minute(db, syms, t)
        if k == 3:  # 중간에 새 상위TF 봉(15m)이 들어온다 — 재사용 경로가 다시 읽어야 한다.
            with OhlcvStore(db) as store:
                last = store.load(syms[0], "15m").iloc[-1]
                nxt = int(last["open_time"]) + 15 * M
                c = float(last["close"])
                store.upsert_candles([Candle(syms[0], "15m", nxt, c, c * 1.01, c * 0.99, c, 1.0)])
        clock[0] = t + M
        events += runner.poll_once()
    conn = sqlite3.connect(db)
    journal = conn.execute(
        "SELECT symbol, timeframe, status, placed_ms, fill_ms, last_limit_price"
        " FROM live_limit_orders ORDER BY id"
    ).fetchall()
    state = conn.execute(
        "SELECT symbol, timeframe, state_json FROM live_engine_state ORDER BY symbol, timeframe"
    ).fetchall()
    conn.close()
    return [(e.kind, e.symbol, e.timeframe) for e in events], journal, state


def test_reuse_is_bit_identical_to_fresh_loads(
    seeded: tuple[Path, list[str], int], tmp_path: Path
) -> None:
    db, syms, end_ms = seeded
    fresh = _drive(db, tmp_path, syms, end_ms, reuse=False)
    reused = _drive(db, tmp_path, syms, end_ms, reuse=True)
    assert fresh[0] == reused[0]
    assert fresh[1] == reused[1]
    assert fresh[2] == reused[2]
    # 이 비교가 「아무 일도 없어서 같다」가 아니게 — 실제로 주문이 걸리고 상태가 남았다.
    assert any(kind == "placed" for kind, _, _ in fresh[0])
    assert any(kind == "filled" for kind, _, _ in fresh[0])
    assert len(fresh[2]) == len(syms) * len(TFS)


def test_new_stored_rows_invalidate_the_window_cache(
    seeded: tuple[Path, list[str], int], tmp_path: Path
) -> None:
    db = tmp_path / "c.db"
    shutil.copy(seeded[0], db)
    syms, end_ms = seeded[1], seeded[2]
    clock = [end_ms]
    runner = _runner(db, syms, end_ms, clock, reuse=True)
    runner.poll_once()
    key = (syms[0], "1h")
    first = runner._htf_tail_cache[key]
    runner.poll_once()
    assert runner._htf_tail_cache[key] is first  # 저장이 그대로면 다시 안 읽는다
    with OhlcvStore(db) as store:
        last = store.load(syms[0], "1h").iloc[-1]
        c = float(last["close"])
        store.upsert_candles(
            [Candle(syms[0], "1h", int(last["open_time"]) + 3_600_000, c, c, c, c, 1.0)]
        )
    runner.poll_once()
    assert runner._htf_tail_cache[key] is not first  # 새 봉 → 다시 읽었다
    assert runner._htf_tail_cache[key][0] != first[0]


def test_window_fingerprint_tracks_rows_and_derived_source(tmp_path: Path) -> None:
    db = tmp_path / "f.db"
    h = 3_600_000
    with OhlcvStore(db) as store:
        store.upsert_candles([Candle("X", "1h", k * h, 1, 1, 1, 1, 1.0) for k in range(10)])
        a = store.window_fingerprint("X", "1h", start_ms=4 * h)
        assert a == (9 * h, 6, 6)
        # 2h는 원본 1h를 버킷 경계로 내린 시작부터 본다(5h → 4h).
        assert store.window_fingerprint("X", "2h", start_ms=5 * h) == (9 * h, 6, 6)
        store.upsert_candles([Candle("X", "1h", 10 * h, 1, 1, 1, 1, 1.0, closed=False)])
        assert store.window_fingerprint("X", "1h", start_ms=4 * h) == (10 * h, 7, 6)
        assert store.window_fingerprint("Y", "1h", start_ms=None) == (None, 0, 0)
