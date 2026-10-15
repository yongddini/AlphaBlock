"""팔은 탭마다 독립 주문이다(WAN-451) — 정본 북처럼 칸에 포지션이 없으면 먼저 체결된 것을 잡는다.

옛 러너는 칸마다 대기 주문이 하나뿐이라(슬롯), 존 A의 주문이 걸려 있는 동안 탭된 존 B에는 주문을
안 걸었다. B가 먼저 닿으면 정본(`wan436.place` → 채택 북)은 B로 들어가는데 라이브는 놓쳤다. 이제
엔진은 탭마다 주문을 하나씩 건다(옛 슬롯 경로는 WAN-451 옛 규칙 정리로 없어졌다) — 장부에 생애를
남기고 체결은 `filled`로 내며, 러너가 그 체결로 진입을 판정한다.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from live.engine_state import PersistedPendingOrder, SeriesStateSnapshot
from live.limit_engine import EngineEvent, ZoneLimitLiveEngine
from live.limit_orders import LimitOrderStatus
from live.order_journal import OrderJournal
from strategy.models import OrderBlock
from tests.test_arm_crowding import _crowd_runner, _zone
from tests.test_limit_engine import (
    _DESC_CLOSES,
    _FORMING,
    _H,
    _M,
    _SYMBOL,
    _TF,
    _htf_df,
    _install_stub_detector,
    _params,
)

_TWO = [_zone(0), _zone(-_H, top=99.6)]


def _engine(
    monkeypatch: pytest.MonkeyPatch, zones: list[OrderBlock]
) -> tuple[ZoneLimitLiveEngine, OrderJournal]:
    _install_stub_detector(monkeypatch, zones)
    journal = OrderJournal(":memory:")
    session = journal.start_session(now_ms=0)
    engine = ZoneLimitLiveEngine(params=_params(), journal=journal, session_id=session)
    engine.on_htf_bars(_SYMBOL, _TF, _htf_df(closes=_DESC_CLOSES))
    return engine, journal


def _tap(engine: ZoneLimitLiveEngine) -> list[EngineEvent]:
    return engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=99.4, high=101.5, close=100.0)


def _ids(engine: ZoneLimitLiveEngine) -> list[int]:
    ids = [o.journal_id for _z, o in engine._series[(_SYMBOL, _TF)].orders]
    assert all(i is not None for i in ids)
    return [int(i) for i in ids if i is not None]


# ------------------------------------------------------------------ 엔진


def test_every_tap_gets_its_own_journaled_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """겹친 두 탭 — 옛 슬롯은 주문 하나(나머지는 `cell_busy`)였고, 이제 둘 다 장부에 걸린다."""
    engine, journal = _engine(monkeypatch, _TWO)
    events = _tap(engine)
    assert [e.kind for e in events] == ["placed", "placed"]
    ids = _ids(engine)
    assert len(set(ids)) == 2
    assert {journal.order_status(i) for i in ids} == {LimitOrderStatus.PENDING.value}
    assert {e.zone for e in events} == {z for z, _o in engine._series[(_SYMBOL, _TF)].orders}
    assert len(engine.open_orders()) == 2

    events = engine.on_substep(
        _SYMBOL, _TF, time_ms=_FORMING + _M, low=95.0, high=100.0, close=96.0
    )
    assert [e.kind for e in events] == ["filled", "filled"]
    assert {e.order.journal_id for e in events} == set(ids)
    assert {journal.order_status(i) for i in ids} == {LimitOrderStatus.FILLED.value}
    assert engine.open_orders() == []


def test_expiry_is_journaled_at_the_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, journal = _engine(monkeypatch, [_zone(0)])
    _tap(engine)
    (jid,) = _ids(engine)
    kinds: list[str] = []
    for k in range(1, 30):  # 닿지 않는 봉들 — 24봉 만료
        kinds += [
            e.kind
            for e in engine.on_substep(
                _SYMBOL, _TF, time_ms=_FORMING + k * _H, low=100.2, high=101.0, close=100.5
            )
        ]
        if engine.open_orders() == []:
            break
    assert kinds == ["cancelled_expired"]
    assert journal.order_status(jid) == LimitOrderStatus.CANCELLED_EXPIRED.value


def test_invalidation_and_gap_are_journaled(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, journal = _engine(monkeypatch, [_zone(0)])
    _tap(engine)
    (jid,) = _ids(engine)
    # 존이 무효화된다 — 탐지 결과(아카이브)에 깨진 존으로 나온다. ⚠️ 그냥 사라지면(빈 결과)
    # 창 밖으로 밀린 존으로 보고 엔진이 기억해 이어 든다(오래된 존 기억, WAN-451).
    _install_stub_detector(monkeypatch, [_zone(0).model_copy(update={"break_time": _FORMING})])
    events = engine.on_htf_bars(_SYMBOL, _TF, _htf_df(closes=_DESC_CLOSES))
    assert [e.kind for e in events] == ["cancelled_invalidated"]
    assert journal.order_status(jid) == LimitOrderStatus.CANCELLED_INVALIDATED.value

    engine, journal = _engine(monkeypatch, [_zone(0)])
    _tap(engine)
    (jid,) = _ids(engine)
    events = engine.on_substep(  # 상위TF 봉 하나를 통째로 건너뛴 1분봉 — 측정 무효로 폐기
        _SYMBOL, _TF, time_ms=_FORMING + 2 * _H, low=100.2, high=101.0, close=100.5
    )
    assert "discarded" in [e.kind for e in events]
    assert journal.order_status(jid) == "discarded_restart"


def test_wall_clock_backstop_expires_every_order(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, journal = _engine(monkeypatch, [_zone(0)])
    _tap(engine)
    (jid,) = _ids(engine)
    assert engine.expire_overdue(now_ms=_FORMING + _H) == []  # 기한 전
    events = engine.expire_overdue(now_ms=_FORMING + 100 * _H)
    assert [e.kind for e in events] == ["cancelled_expired"]
    assert journal.order_status(jid) == LimitOrderStatus.CANCELLED_EXPIRED.value
    assert engine.open_orders() == []


def test_series_with_open_orders_is_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = _engine(monkeypatch, [_zone(0)])
    _tap(engine)
    assert engine.drop_series(_SYMBOL, _TF) is False
    assert engine.journal_ids(_SYMBOL, _TF) == set(_ids(engine))


def test_snapshot_keeps_the_journal_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, _ = _engine(monkeypatch, _TWO)
    _tap(engine)
    ids = set(_ids(engine))
    snap = engine.snapshot_series(_SYMBOL, _TF, last_htf_time=_FORMING - _H, saved_ms=0)
    assert snap is not None and {s.journal_id for s in snap.shadows} == ids
    snap = SeriesStateSnapshot.model_validate_json(snap.model_dump_json())
    fresh, _ = _engine(monkeypatch, _TWO)
    fresh.restore_series(_SYMBOL, _TF, snap)
    assert fresh.journal_ids(_SYMBOL, _TF) == ids


# ------------------------------------------------------------------ 러너


def _zone_fill(event: EngineEvent, zone: tuple[str, int, int], *, after_ms: int = 0) -> EngineEvent:
    fill = event.fill
    assert fill is not None
    return dataclasses.replace(
        event,
        time_ms=event.time_ms + after_ms,
        fill=dataclasses.replace(fill, time=fill.time + after_ms),
        zone=zone,
    )


def test_runner_enters_from_the_first_fill_and_skips_while_holding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 존의 체결 — 먼저 체결된 것이 진입하고, 포지션이 있는 동안의 체결은 `cell_busy`.

    몰림 규칙은 끈다 — 정본도 몰림 판정이 칸 점유보다 먼저라, 켜 두면 두 번째 체결은 첫 체결의
    신호 1개 때문에 몰림 규칙(1~10개 건너뛰기)에서 먼저 빠진다(그것도 정본과 같은 동작이다).
    """
    from tests.test_arm_rules import _fill_event

    runner, journal, session, db = _crowd_runner(tmp_path, ["SYN/USDT:USDT"])
    monkeypatch.setattr(runner, "_crowd_gate", lambda _event, decision: decision)
    first = _zone_fill(_fill_event(journal, session, price=140.0, ob_stop=133.0), ("bullish", 7, 8))
    second = _zone_fill(
        _fill_event(journal, session, price=140.0, ob_stop=133.0), ("bullish", 9, 9), after_ms=_M
    )
    runner._handle_events([first])
    runner._handle_events([second])
    rows = (
        sqlite3.connect(db)
        .execute(
            "SELECT entry_status, COALESCE(entry_reject_code, '') FROM live_limit_orders"
            " ORDER BY id"
        )
        .fetchall()
    )
    assert rows == [("entered", ""), ("rejected", "cell_busy")]
    assert len(runner._executor.open_positions) == 1
    assert runner._signals is not None and len(runner._signals.rows(0, 10**13)) == 2


@pytest.mark.parametrize(
    ("price", "ob_stop", "low", "want"),
    [
        # 체결가 125 ≤ 앞 포지션 2배 손절 126 — 가격이 손절가를 먼저 지나 더 내려가 체결됐다.
        (125.0, 118.0, 124.0, [("entered", ""), ("entered", "")]),
        # 체결가 127 > 126 — 내려오는 가격이 지정가에 먼저 닿았다. 그 순간 포지션은 열려 있다.
        (127.0, 120.0, 124.0, [("entered", ""), ("rejected", "cell_busy")]),
    ],
)
def test_same_minute_follows_the_price_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    price: float,
    ob_stop: float,
    low: float,
    want: list[tuple[str, str]],
) -> None:
    """같은 1분에 앞 포지션 손절과 다음 체결 — 정본 `w436.fill_before_exit`와 같은 가격 순서."""
    from tests.test_arm_rules import _fill_event

    runner, journal, session, db = _crowd_runner(tmp_path, ["SYN/USDT:USDT"])
    monkeypatch.setattr(runner, "_crowd_gate", lambda _event, decision: decision)
    first = _zone_fill(_fill_event(journal, session, price=140.0, ob_stop=133.0), ("bullish", 7, 8))
    runner._handle_events([first])
    (pos,) = runner._executor.open_positions
    assert pos.stop_price == pytest.approx(126.0)
    second = _zone_fill(
        _fill_event(journal, session, price=price, ob_stop=ob_stop), ("bullish", 9, 9), after_ms=_M
    )
    assert second.fill is not None
    runner._minute_bar[("SYN/USDT:USDT", "15m")] = (second.fill.time, low, 141.0)
    runner._handle_events([second])
    rows = (
        sqlite3.connect(db)
        .execute(
            "SELECT entry_status, COALESCE(entry_reject_code, '') FROM live_limit_orders"
            " ORDER BY id"
        )
        .fetchall()
    )
    assert rows == want
    (now,) = runner._executor.open_positions
    assert now.entry_price == (price if want[1][0] == "entered" else 140.0)


def test_restore_drops_orders_the_journal_already_closed(tmp_path: Path) -> None:
    """크래시 직전 체결을 이미 집행했으면 스냅샷의 그 주문을 다시 걸지 않는다(두 번 집행 금지)."""
    from tests.test_arm_rules import _fill_event

    runner, journal, session, _db = _crowd_runner(tmp_path, ["SYN/USDT:USDT"])
    filled = _fill_event(journal, session, price=140.0, ob_stop=133.0).order.journal_id
    pending = _fill_event(journal, session, price=140.0, ob_stop=133.0).order.journal_id
    assert filled is not None and pending is not None
    journal._conn.execute(
        "UPDATE live_limit_orders SET status = 'pending', fill_ms = NULL WHERE id = ?", (pending,)
    )
    journal._conn.commit()

    def _saved(jid: int | None, start: int) -> PersistedPendingOrder:
        return PersistedPendingOrder(
            journal_id=jid,
            zone=("bullish", start, start),
            tap_index=0,
            placed_ms=0,
            limit_valid_bars=24,
            bars_elapsed=0,
            first_rested_ms=None,
            last_limit_price=None,
        )

    snap = SeriesStateSnapshot(
        saved_ms=0,
        last_substep_time=0,
        last_htf_time=0,
        forming_bar=None,
        forming_low=None,
        forming_high=None,
        running_close=None,
        shadows=[_saved(filled, 1), _saved(pending, 2), _saved(None, 3)],
    )
    kept = runner._reconcile_with_journal("SYN/USDT:USDT", "15m", snap).shadows
    assert [s.journal_id for s in kept] == [pending, None]


# ------------------------------------------------------------------ 실데이터


_REAL_DB = Path("data/ohlcv.db")
_DAY = 86_400_000


@dataclass
class _Snap:
    rebalance_ms: int
    members: frozenset[str]


class _Always:
    """그 종목이 매주 목록 안 — 목록 게이트를 대조에서 뺀다."""

    def __init__(self, symbol: str) -> None:
        self._symbol = symbol

    def at(self, ms: int) -> _Snap:
        from data.universe import rebalance_at

        return _Snap(rebalance_at(ms), frozenset({self._symbol}))


def _real_available(symbol: str, end: int) -> bool:
    if not _REAL_DB.exists():
        return False
    try:
        conn = sqlite3.connect(_REAL_DB)
        try:
            row = conn.execute(
                "SELECT MAX(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = '1m'", (symbol,)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row and row[0] and row[0] >= end)


def _live_entries(symbol: str, tf: str, start: int, end: int) -> list[int]:
    from config.settings import Settings
    from data.storage import OhlcvStore
    from execution.engine import build_execution_engine
    from live.arm_rules import ArmParams
    from live.executor import PaperExecutor
    from live.zone_limit_runner import ZoneLimitPaperRunner, build_paper_recorder
    from paper.store import PaperTradeStore

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "j.db"
        settings = Settings(
            db_path=str(db),
            confluence={"retap_mode": "once", "fill_penetration_bps": 5.0},
        )
        paper = PaperTradeStore(db)
        journal = OrderJournal(db)
        session = journal.start_session(now_ms=0)
        real = OhlcvStore(_REAL_DB)
        runner = ZoneLimitPaperRunner(
            store=real,
            engine=ZoneLimitLiveEngine(
                params=settings.confluence, journal=journal, session_id=session
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
            series=[(symbol, tf)],
            lookback_bars=settings.live_signal_lookback_bars,
            poll_interval_seconds=0,
            arm=ArmParams(),
            universe=_Always(symbol),  # type: ignore[arg-type]
            bootstrap_days=730,
        )
        runner._last_substep[(symbol, tf)] = start - 60_000
        runner.poll_series(symbol, tf)
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT entry_time FROM paper_trades").fetchall()
        rows += conn.execute("SELECT entry_time FROM open_positions").fetchall()
        conn.close()
        real.close()
    return sorted(int(r[0]) for r in rows if start <= int(r[0]) < end)


def _canon_entries(symbol: str, tf: str, start: int, end: int) -> tuple[list[int], list[int]]:
    """정본 팔 — 탭마다 독립 체결 · 팔 게이트 · 칸당 포지션 1개 → (옛 규약, 가격 순서) 진입 시각.

    옛 규약은 같은 1분에 청산을 먼저 정산한다. 가격 순서(채택)는 정본 `w436.fill_before_exit`가
    정한다 — 앞 포지션 손절가보다 높은 체결은 체결이 먼저라 못 들어간다.
    """
    from backtest import wan436_stoch_crowd_rules as w436
    from backtest import wan439_crash_size_layer as w439
    from backtest.models import ExitReason
    from backtest.wan169_leverage_book import run_cells
    from backtest.wan424_stoch_ob_arm import base_cell_kwargs
    from data.storage import OhlcvStore

    def day(ms: int) -> str:
        return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")

    (payload,) = run_cells(
        [symbol],
        (tf,),
        start=day(start - 730 * _DAY),
        end=day((end // _DAY) * _DAY),
        jobs=1,
        cold_segments=False,
        **base_cell_kwargs(),
    )
    with OhlcvStore(_REAL_DB) as store:
        entries = w436.build_entries(
            [payload], store=store, start_ms=start - 2 * _DAY, end_ms=end + 3 * _DAY
        )
    entries = sorted(
        (e for e in entries if start <= e.cand.entry_time < end),
        key=lambda e: (int(e.cand.entry_time), float(e.cand.entry_price)),
    )
    exits = [w436.arm_exit(e, w439.RULES.stop_multiple)[0] for e in entries]

    def seq(price_order: bool) -> list[int]:
        out: list[int] = []
        prev = None
        for cand in exits:
            t = int(cand.entry_time)
            if prev is not None and t < int(prev.exit_time):
                continue
            if (
                price_order
                and prev is not None
                and t == int(prev.exit_time)
                and (
                    prev.reason != ExitReason.STOP_LOSS
                    or float(cand.entry_price) > float(prev.stop_price)
                )
            ):
                continue  # 체결이 앞 포지션 청산보다 먼저 — 칸에 포지션이 있어 못 들어간다.
            out.append(t)
            prev = cand
        return out

    return seq(False), seq(True)


def test_real_data_live_entries_equal_the_canonical_arm() -> None:
    """UNI 1d 2025-01-27 ~ 02-10 — 같은 1분에 「앞 포지션 손절」과 「다음 존 체결」이 겹친다.

    02-03 09:44(KST) 첫 진입 → 11:07 급락 1분에 그 포지션이 2배 손절(7.110)에 닿고, 같은 1분에
    가격이 아래 존에 들어와 7.136에 체결된다. 체결가가 손절가보다 높으니 내려오는 가격이 지정가에
    먼저 닿았다 — 그 순간 포지션은 아직 열려 있어 못 들어간다(가격 순서 · 2026-10-15 채택). 옛
    규약(청산 먼저)이면 두 번째도 들어갔다. 몰림 규칙은 뺐다(칸 하나로는 신호 수가 정본과 다르다).
    """
    symbol, tf = "UNI/USDT:USDT", "1d"
    end = 1_739_145_600_000  # 2025-02-10
    start = end - 14 * _DAY
    if not _real_available(symbol, end):
        pytest.skip("실데이터(data/ohlcv.db) 없음")
    exit_first, price_order = _canon_entries(symbol, tf, start, end)
    assert len(exit_first) == 2 and price_order == exit_first[:1]
    assert _live_entries(symbol, tf, start, end) == price_order
