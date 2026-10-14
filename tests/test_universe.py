"""팔의 주간 종목 목록(WAN-451 ③) — 백테스트와 같은 규칙 · 후보 풀 · 저장 · 주기 작업."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backtest import wan440_universe as w440
from backtest import wan442_momentum_rotation as w442
from backtest import wan444_liquidity_universe as w444
from cli.main import cmd_universe
from config.settings import Settings
from data import universe as uni
from data.models import Candle
from data.storage import OhlcvStore
from data.universe_pool import (
    MAX_STALE_FRACTION,
    RotationSnapshot,
    UniverseStore,
    compute_rotation,
    format_rotation_message,
    next_refresh_ms,
    pool_symbols,
    refresh_pool_daily,
    render_rotation,
    run_universe_once,
)

DAY = uni.DAY_MS
MONDAY0 = 4 * DAY  # 1970-01-05 월요일 00:00 UTC
T = MONDAY0 + 2000 * 7 * DAY  # 먼 미래의 어느 월요일


def test_backtest_modules_use_the_single_source() -> None:
    assert w442.Daily is uni.Daily
    assert w442.eligible is uni.eligible
    assert w442.load_daily is uni.load_daily
    assert w442.rebalance_times is uni.rebalance_times
    assert w442.Rotation is uni.Rotation
    assert w444.select_liquidity is uni.select_liquidity
    assert w440.is_usdt_perp is uni.is_usdt_perp
    assert (uni.TOP_N, uni.MIN_AGE_DAYS, uni.FRESH_DAYS, uni.LIQ_DAYS) == (31, 90, 3, 30)


def test_rebalance_at_is_the_monday_grid() -> None:
    grid = uni.rebalance_times(T - 30 * DAY, T + 30 * DAY)
    assert T in grid and all((t - MONDAY0) % (7 * DAY) == 0 for t in grid)
    assert uni.rebalance_at(T) == T
    assert uni.rebalance_at(T + 6 * DAY + DAY - 1) == T
    assert uni.rebalance_at(T - 1) == T - 7 * DAY


def _market(
    mid: str, *, swap: bool = True, linear: bool = True, quote: str = "USDT", **info: str
) -> dict[str, Any]:
    base = {"contractType": "PERPETUAL", "status": "TRADING"}
    base.update(info)
    return {
        "id": mid,
        "swap": swap,
        "linear": linear,
        "quote": quote,
        "settle": quote,
        "info": base,
    }


def test_pool_symbols_keeps_only_trading_usdt_perps() -> None:
    markets = {
        "BTC/USDT:USDT": _market("BTCUSDT"),
        "BTC/USDT": _market("BTCUSDT", swap=False),  # 현물
        "BTC/USD:BTC": _market("BTCUSD_PERP", linear=False, quote="USD"),
        "BTC/USDT:USDT-261225": _market("BTCUSDT_261225", contractType="CURRENT_QUARTER"),
        "OLD/USDT:USDT": _market("OLDUSDT", status="SETTLING"),
        "龙虾/USDT:USDT": _market("龙虾USDT"),
        "BTCDOM/USDT:USDT": _market("BTCDOMUSDT"),  # 지수 — 정본도 DEFIUSDT를 포함했다
        "ETH/USDC:USDC": _market("ETHUSDC", quote="USDC"),
    }
    assert pool_symbols(markets) == ["BTC/USDT:USDT", "BTCDOM/USDT:USDT"]


def _seed(store: OhlcvStore, sym: str, *, first: int, last: int, vol: float) -> None:
    store.upsert_candles(
        Candle(sym, "1d", t, 10.0, 11.0, 9.0, 10.0, vol) for t in range(first, last + 1, DAY)
    )


def _universe_db(tmp_path: Path, n: int = 40) -> tuple[Path, list[str]]:
    db = tmp_path / "u.db"
    syms = [f"S{i:02d}/USDT:USDT" for i in range(n)]
    with OhlcvStore(db) as store:
        for i, s in enumerate(syms):
            _seed(store, s, first=T - 800 * DAY, last=T - DAY, vol=1000.0 + i)
        _seed(store, "YOUNG/USDT:USDT", first=T - 60 * DAY, last=T - DAY, vol=1e9)  # 나이 미달
        _seed(store, "DEAD/USDT:USDT", first=T - 800 * DAY, last=T - 5 * DAY, vol=1e9)  # 신선도
    return db, [*syms, "YOUNG/USDT:USDT", "DEAD/USDT:USDT"]


def test_compute_rotation_matches_full_history_rule(tmp_path: Path) -> None:
    db, pool = _universe_db(tmp_path)
    with OhlcvStore(db) as store:
        snap = compute_rotation(store, pool, T, computed_ms=T + 1)
        full = uni.load_daily([store], pool, 0, T)
    assert snap.members == uni.select_liquidity(full, T)  # 400일 창 = 전 이력
    assert len(snap.members) == 31 and "S39/USDT:USDT" in snap.members
    assert "YOUNG/USDT:USDT" not in dict(snap.ranked)
    assert "DEAD/USDT:USDT" not in dict(snap.ranked)
    assert snap.stale_size == 1  # DEAD만 일요일 봉이 없다
    assert [s for s, _ in snap.ranked[:2]] == ["S39/USDT:USDT", "S38/USDT:USDT"]


def test_ties_break_by_name() -> None:
    d = uni.Daily(np.array([T - 200 * DAY, T - DAY]), np.array([1.0, 1.0]), np.array([5.0, 5.0]))
    assert uni.rank_symbols(uni.liquidity_scores({"B": d, "A": d}, T)) == ["A", "B"]


def _snap(t: int, members: list[str], *, stale: int = 0) -> RotationSnapshot:
    ranked = tuple((s, 100.0 - i) for i, s in enumerate(members))
    return RotationSnapshot(
        rebalance_ms=t, computed_ms=t + 1, pool_size=50, stale_size=stale, ranked=ranked, top_n=2
    )


def test_store_never_overwrites_a_saved_week(tmp_path: Path) -> None:
    with UniverseStore(tmp_path / "u.db") as store:
        assert store.save(_snap(T, ["A", "B", "C"]))
        assert not store.save(_snap(T, ["X", "Y"]))
        assert store.save(_snap(T + 7 * DAY, ["B", "C"]))
        got = store.snapshot(T)
        assert got is not None and got.members == {"A", "B"} and got.scored_size == 3
        later = store.at(T + 9 * DAY)
        assert later is not None and later.rebalance_ms == T + 7 * DAY
        prev = store.previous(T + 7 * DAY)
        assert prev is not None and prev.rebalance_ms == T
        assert store.at(T - 1) is None
        assert store.rebalance_times() == [T, T + 7 * DAY]


class _FakeExchange:
    """`load_markets`·`fetch_ohlcv` 가짜 — 심볼마다 [첫 봉, 마지막 닫힌 봉] 일봉을 준다."""

    def __init__(
        self, bars: Mapping[str, tuple[int, int, float]], fail: frozenset[str] = frozenset()
    ) -> None:
        self.bars = dict(bars)
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    def load_markets(self, reload: bool = False) -> dict[str, dict[str, Any]]:
        return {s: _market(s.split("/")[0] + "USDT") for s in self.bars}

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, object] | None = None,
    ) -> list[list[float]]:
        assert timeframe == "1d" and since is not None and limit is not None
        self.calls.append((symbol, since))
        if symbol in self.fail:
            raise RuntimeError("boom")
        first, last, vol = self.bars[symbol]
        start = max(first, since)
        return [[t, 10.0, 11.0, 9.0, 10.0, vol] for t in range(start, last + 1, DAY)][:limit]


def test_refresh_is_incremental_and_survives_failures(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    now = T + 3 * 3_600_000  # 월요일 03:00 — 일요일 봉까지 닫혔다
    ex = _FakeExchange(
        {
            "A/USDT:USDT": (T - 100 * DAY, T - DAY, 5.0),
            "B/USDT:USDT": (T - 100 * DAY, T - DAY, 5.0),
            "C/USDT:USDT": (T - 100 * DAY, T - DAY, 5.0),
        },
        fail=frozenset({"B/USDT:USDT"}),
    )
    sleeps: list[float] = []
    with OhlcvStore(db) as store:
        _seed(store, "A/USDT:USDT", first=T - 100 * DAY, last=T - 2 * DAY, vol=5.0)
        res = refresh_pool_daily(
            ex,
            store,
            ["A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT"],
            lookback_days=1825,
            request_interval_seconds=0.5,
            sleeper=sleeps.append,
            now_ms=lambda: now,
        )
        assert res.failed == ["B/USDT:USDT"] and res.stored == 1 + 100
        assert ex.calls[0] == ("A/USDT:USDT", T - DAY)  # 저장된 마지막 봉 다음부터
        assert sleeps.count(0.5) == 2  # 종목 사이에만
        ex.calls.clear()
        refresh_pool_daily(
            ex,
            store,
            ["A/USDT:USDT"],
            lookback_days=1825,
            request_interval_seconds=0,
            now_ms=lambda: now,
        )
        assert ex.calls == []  # 새로 닫힌 봉이 없으면 요청하지 않는다


def _run(ex: _FakeExchange, db: Path, now: int, sent: list[str]) -> Any:
    with OhlcvStore(db) as store, UniverseStore(db) as universe:
        return run_universe_once(
            ex,
            store,
            universe,
            lookback_days=400,
            request_interval_seconds=0,
            now_ms=lambda: now,
            notify=sent.append,
        )


def test_run_once_saves_once_and_notifies_changes(tmp_path: Path) -> None:
    db = tmp_path / "w.db"
    bars = {f"S{i:02d}/USDT:USDT": (T - 300 * DAY, T + 6 * DAY, 100.0 + i) for i in range(35)}
    ex = _FakeExchange(bars)
    sent: list[str] = []
    first = _run(ex, db, T + 3_600_000, sent)
    assert first.saved is not None and len(first.saved.members) == 31
    assert "첫 목록" in sent[0]
    assert _run(ex, db, T + 2 * DAY, sent).saved is None and len(sent) == 1  # 같은 주는 한 번
    ex.bars["S00/USDT:USDT"] = (T - 300 * DAY, T + 6 * DAY, 1e9)  # 다음 주 1위로
    nxt = _run(ex, db, T + 7 * DAY + 3_600_000, sent)
    assert nxt.saved is not None and "S00/USDT:USDT" in nxt.saved.members
    assert "들어옴: S00" in sent[1] and "빠짐: S04" in sent[1]
    assert "새 진입만 막습니다" in sent[1]


def test_run_once_defers_when_the_sunday_bar_is_missing(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    n = 20
    bars = {f"S{i:02d}/USDT:USDT": (T - 300 * DAY, T - DAY, 100.0) for i in range(n)}
    stale = int(MAX_STALE_FRACTION * n) + 1
    for i in range(stale):  # 거래소가 일요일 봉을 아직 안 준다
        bars[f"S{i:02d}/USDT:USDT"] = (T - 300 * DAY, T - 2 * DAY, 100.0)
    sent: list[str] = []
    run = _run(_FakeExchange(bars), db, T + 120_000, sent)
    assert run.saved is None and run.deferred is not None and run.deferred.stale_size == stale
    with UniverseStore(db) as universe:
        assert universe.snapshot(T) is None
    assert sent == []


def test_next_refresh_is_after_the_daily_close() -> None:
    assert next_refresh_ms(T + 5, 120) == T + DAY + 120_000
    assert next_refresh_ms(T + DAY - 1, 0) == T + DAY


def test_messages_name_the_changes() -> None:
    prev = _snap(T - 7 * DAY, ["A/USDT:USDT", "B/USDT:USDT"])
    snap = _snap(T, ["A/USDT:USDT", "C/USDT:USDT"], stale=2)
    msg = format_rotation_message(snap, prev)
    assert "들어옴: C" in msg and "빠짐: B" in msg and "직전 일봉이 아직 없는 풀 종목 2개" in msg
    body = render_rotation(snap, prev, want_ms=T, extra=0)
    assert "| 1 | A |" in body and "(새로)" in body and "⚠️ 직전 일봉 없음 2" in body
    assert "아직 없습니다" in render_rotation(None, None, want_ms=T)
    assert "(새로)" not in render_rotation(snap, None, want_ms=T)  # 첫 목록은 「새로」가 아니다


def test_cli_exit_code_follows_this_weeks_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "c.db"
    with UniverseStore(db) as store:
        store.save(_snap(T, ["A/USDT:USDT", "B/USDT:USDT"]))
    ns = argparse.Namespace(db=str(db), at=None, extra=5)
    from datetime import UTC, datetime

    day = datetime.fromtimestamp((T + 2 * DAY) / 1000, tz=UTC).date().isoformat()
    ns.at = day
    assert cmd_universe(ns, Settings()) == 0
    ns.at = datetime.fromtimestamp((T + 8 * DAY) / 1000, tz=UTC).date().isoformat()
    assert cmd_universe(ns, Settings()) == 1  # 다음 주 목록이 아직 없다 → 지난 목록 + 경고
    assert "가장 최근 목록" in capsys.readouterr().out
