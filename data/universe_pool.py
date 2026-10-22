"""팔의 후보 풀 일봉 수집 + 매주 목록 계산·저장 (WAN-451 ③ 단계 · 결정 ①나 ②나).

하는 일은 셋이다.

1. **후보 풀** — 거래소에 **지금 상장된** USDT 무기한 전체(사용자 결정 ② 나). 판정은 백테스트
   정본과 같은 `data.universe.is_usdt_perp`에 「무기한 · 거래 중」을 더한 것이다.
2. **풀 일봉 수집** — 풀 전체의 **1d만** REST로 모은다(결정 ① 나 — 웹소켓·1분봉은 그 주 31종목만,
   다음 단계). 하루 한 번(00:00 UTC 직후)이면 충분하다. 바이낸스 요청 가중치 한도(분당 2400)를
   넘지 않게 종목 사이에 간격을 둔다(첫 적재는 종목당 1~2쪽 · 쪽당 가중치 5).
3. **매주 목록** — 월요일 00:00 UTC에 그 시각까지 **닫힌** 일봉으로 `data.universe` 규칙
   (30일 평균 거래대금 상위 31 · 자격 3일·90일)을 돌려 `universe_rotations`·`universe_ranks`에
   남긴다. **한 번 저장한 주는 덮지 않는다**(나중에 데이터가 바뀌어도 그 주에 실제로 고른 목록이
   기록이다).

⚠️ **나이(90일) 판정은 저장된 첫 일봉 기준이다** — 백테스트도 그렇다(`Daily.open_time[0]`). 신규
종목은 상장일부터 받으므로 같고, 오래된 종목은 룩백(1d 1825일) 시작이 첫 봉이라 어차피 90일을
넘는다. 목록 계산은 `t − 400일`부터만 읽는다(같은 이유로 판정이 안 바뀐다 · 메모리 절약).

이 모듈은 어떤 종목을 1분봉으로 수집할지·러너가 무엇을 감시할지는 **아직 바꾸지 않는다**(다음 PR).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from common.timefmt import format_kst_zoned
from data import universe as uni
from data.backfill import FetchOHLCV, backfill_symbol
from data.sqlite_util import configure_connection
from data.storage import OhlcvStore

logger = logging.getLogger(__name__)

POOL_TIMEFRAME = "1d"
#: 목록 계산이 읽는 과거(일). 나이 90일 · 거래대금 30일 · 신선도 3일보다 넉넉하면 결과가 같다.
ROTATION_READ_DAYS = 400

_ROTATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_rotations (
    rebalance_ms INTEGER PRIMARY KEY,
    computed_ms  INTEGER NOT NULL,
    pool_size    INTEGER NOT NULL,
    scored_size  INTEGER NOT NULL,
    stale_size   INTEGER NOT NULL,
    top_n        INTEGER NOT NULL
)
"""
_RANK_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_ranks (
    rebalance_ms INTEGER NOT NULL,
    rank         INTEGER NOT NULL,
    symbol       TEXT    NOT NULL,
    liquidity    REAL    NOT NULL,
    PRIMARY KEY (rebalance_ms, symbol)
)
"""

_POOL_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_pool_members (
    symbol      TEXT    PRIMARY KEY,
    captured_ms INTEGER NOT NULL
)
"""
"""마지막으로 확인한 후보 풀(지금 거래 중인 USDT 무기한) — **최신 한 벌만** 남는다(WAN-471).

감시(`data.collection_plan.monitor_scope`)가 후보 풀 일봉(1d)을 「DB에 1d가 있는 모든 종목」이
아니라 「지금 풀에 있는 종목」으로 좁히려고 읽는다. 상폐된 종목은 풀 수집이 이미 빼지만
(`status == "TRADING"`) 그 사실이 어디에도 안 남아 감시가 영원히 「수집 지연」으로 울렸다
(2026-10-22 EPIC).
"""

#: `paper-reset.sh`가 지우지 않는 테이블이다 — 목록은 장부가 아니라 시장 기록이다.
UNIVERSE_TABLES: tuple[str, ...] = ("universe_rotations", "universe_ranks", "universe_pool_members")


# ---------------------------------------------------------------------------
# 후보 풀 — 지금 상장된 USDT 무기한
# ---------------------------------------------------------------------------


def pool_symbols(markets: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """ccxt `load_markets()` 결과에서 후보 풀(ccxt 심볼, 예 `BTC/USDT:USDT`)을 고른다.

    선형 USDT 무기한 · 거래 중(`status == "TRADING"`) · 거래소 이름이 정본 판정
    (`is_usdt_perp`)을 통과하는 것. 정산 중(`SETTLING`, 상폐 절차)·상장 대기는 뺀다.
    """
    out: list[str] = []
    for sym, m in markets.items():
        info = m.get("info") or {}
        if not (m.get("swap") and m.get("linear")):
            continue
        if m.get("quote") != uni.QUOTE or m.get("settle") != uni.QUOTE:
            continue
        if info.get("contractType") != "PERPETUAL" or info.get("status") != "TRADING":
            continue
        if not uni.is_usdt_perp(str(m.get("id", ""))):
            continue
        out.append(sym)
    return sorted(out)


# ---------------------------------------------------------------------------
# 목록 저장
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RotationSnapshot:
    """한 주의 목록 — 자격 있는 종목 전체 순위와 그중 상위 `top_n`."""

    rebalance_ms: int
    computed_ms: int
    pool_size: int
    stale_size: int
    """풀 중 그 주 직전 일봉(월요일 00:00에 닫힌 봉)이 아직 없는 종목 수 — 0이 정상이다."""
    ranked: tuple[tuple[str, float], ...]
    """(심볼, 30일 평균 거래대금) — 1위부터."""
    top_n: int = uni.TOP_N

    @property
    def members(self) -> frozenset[str]:
        return frozenset(s for s, _ in self.ranked[: self.top_n])

    @property
    def scored_size(self) -> int:
        return len(self.ranked)


class UniverseStore:
    """`universe_rotations`·`universe_ranks`의 SQLite 래퍼(시세와 같은 DB 파일)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        configure_connection(self._conn)
        self._conn.execute(_ROTATION_SCHEMA)
        self._conn.execute(_RANK_SCHEMA)
        self._conn.execute(_POOL_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> UniverseStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, snap: RotationSnapshot) -> bool:
        """저장한다 — 그 주가 이미 있으면 **덮지 않고** False(그 주에 고른 목록이 기록이다)."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT INTO universe_rotations "
                    "(rebalance_ms, computed_ms, pool_size, scored_size, stale_size, top_n) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        snap.rebalance_ms,
                        snap.computed_ms,
                        snap.pool_size,
                        snap.scored_size,
                        snap.stale_size,
                        snap.top_n,
                    ),
                )
                self._conn.executemany(
                    "INSERT INTO universe_ranks (rebalance_ms, rank, symbol, liquidity) "
                    "VALUES (?, ?, ?, ?)",
                    [(snap.rebalance_ms, i + 1, s, v) for i, (s, v) in enumerate(snap.ranked)],
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def save_pool(self, symbols: Iterable[str], *, captured_ms: int) -> bool:
        """지금의 후보 풀을 **통째로 갈아끼운다**(WAN-471) — 빈 풀은 저장하지 않고 False.

        빈 풀은 거래소 응답이 비었다는 뜻이지 「모든 종목이 상폐됐다」가 아니다 — 저장하면 감시가
        풀 일봉 전부를 판정에서 빼 버린다(감시가 꺼지는 쪽으로 실패하면 안 된다, WAN-458). 그때는
        마지막으로 확인한 풀을 그대로 둔다.
        """
        members = sorted(set(symbols))
        if not members:
            return False
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM universe_pool_members")
            self._conn.executemany(
                "INSERT INTO universe_pool_members (symbol, captured_ms) VALUES (?, ?)",
                [(s, captured_ms) for s in members],
            )
        return True

    def pool(self) -> frozenset[str] | None:
        """마지막으로 저장한 후보 풀 — 한 번도 저장하지 않았으면 None."""
        with self._lock:
            rows = self._conn.execute("SELECT symbol FROM universe_pool_members").fetchall()
        return frozenset(str(r[0]) for r in rows) if rows else None

    def rebalance_times(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT rebalance_ms FROM universe_rotations ORDER BY rebalance_ms"
            ).fetchall()
        return [int(r[0]) for r in rows]

    def snapshot(self, rebalance_ms: int) -> RotationSnapshot | None:
        with self._lock:
            head = self._conn.execute(
                "SELECT computed_ms, pool_size, stale_size, top_n FROM universe_rotations "
                "WHERE rebalance_ms = ?",
                (rebalance_ms,),
            ).fetchone()
            if head is None:
                return None
            ranks = self._conn.execute(
                "SELECT symbol, liquidity FROM universe_ranks WHERE rebalance_ms = ? ORDER BY rank",
                (rebalance_ms,),
            ).fetchall()
        return RotationSnapshot(
            rebalance_ms=rebalance_ms,
            computed_ms=int(head[0]),
            pool_size=int(head[1]),
            stale_size=int(head[2]),
            ranked=tuple((str(s), float(v)) for s, v in ranks),
            top_n=int(head[3]),
        )

    def at(self, t_ms: int) -> RotationSnapshot | None:
        """시각 t에 유효한 목록(t 이하 마지막 리밸런싱) — 없으면 None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(rebalance_ms) FROM universe_rotations WHERE rebalance_ms <= ?",
                (t_ms,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return self.snapshot(int(row[0]))

    def previous(self, rebalance_ms: int) -> RotationSnapshot | None:
        return self.at(rebalance_ms - 1)


def rotation_members_at(db_path: str | Path, t_ms: int) -> frozenset[str] | None:
    """시각 t에 유효한 그 주 목록(상위 `top_n`)을 **읽기 전용**으로 읽는다(WAN-458).

    수집기 밖(감시·`status`·대시보드)이 「지금 무엇을 모으는가」를 알려고 쓴다. `UniverseStore`는
    생성자에서 표를 만들므로(쓰기) 감시 쪽에는 맞지 않는다. DB·표·그 시각 목록이 없거나 읽기가
    실패하면 None — 호출부가 「계획을 못 읽었다」로 받아 옛 동작으로 돌아간다(감시가 꺼지는
    쪽으로 실패하면 안 된다).
    """
    path = str(db_path)
    if path == ":memory:" or not Path(path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('universe_rotations', 'universe_ranks')"
            )
        }
        if tables != {"universe_rotations", "universe_ranks"}:
            return None
        head = conn.execute(
            "SELECT rebalance_ms, top_n FROM universe_rotations WHERE rebalance_ms = "
            "(SELECT MAX(rebalance_ms) FROM universe_rotations WHERE rebalance_ms <= ?)",
            (t_ms,),
        ).fetchone()
        if head is None:
            return None
        rows = conn.execute(
            "SELECT symbol FROM universe_ranks WHERE rebalance_ms = ? ORDER BY rank LIMIT ?",
            (int(head[0]), int(head[1])),
        ).fetchall()
        return frozenset(str(r[0]) for r in rows)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def pool_members_latest(db_path: str | Path) -> frozenset[str] | None:
    """마지막으로 저장한 후보 풀을 **읽기 전용**으로 읽는다(WAN-471).

    `rotation_members_at`과 같은 규약이다 — DB·표가 없거나 비었거나 읽기가 실패하면 None이고,
    호출부는 「풀을 못 읽었다」로 받아 옛 동작(저장된 1d 전부 감시)으로 돌아간다.
    """
    path = str(db_path)
    if path == ":memory:" or not Path(path).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='universe_pool_members'"
        ).fetchone()
        if row is None:
            return None
        rows = conn.execute("SELECT symbol FROM universe_pool_members").fetchall()
        return frozenset(str(r[0]) for r in rows) if rows else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 계산
# ---------------------------------------------------------------------------


def compute_rotation(
    store: OhlcvStore, pool: Sequence[str], rebalance_ms: int, *, computed_ms: int
) -> RotationSnapshot:
    """`rebalance_ms`에 닫힌 일봉까지로 그 주의 목록을 계산한다(정본 규칙 그대로)."""
    daily = uni.load_daily(
        [store], pool, rebalance_ms - ROTATION_READ_DAYS * uni.DAY_MS, rebalance_ms
    )
    scores = uni.liquidity_scores(daily, rebalance_ms)
    ranked = tuple((s, scores[s]) for s in uni.rank_symbols(scores))
    want = rebalance_ms - uni.DAY_MS  # 월요일 00:00에 닫힌 일봉(일요일)
    stale = sum(1 for s in pool if (d := daily.get(s)) is None or int(d.open_time[-1]) < want)
    return RotationSnapshot(
        rebalance_ms=rebalance_ms,
        computed_ms=computed_ms,
        pool_size=len(pool),
        stale_size=stale,
        ranked=ranked,
    )


@dataclass
class PoolRefresh:
    """풀 일봉 한 번 갱신의 결과."""

    symbols: int = 0
    stored: int = 0
    failed: list[str] = field(default_factory=list)


def refresh_pool_daily(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    pool: Sequence[str],
    *,
    lookback_days: int,
    request_interval_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> PoolRefresh:
    """풀 전체의 1d를 저장된 마지막 봉 다음부터(없으면 룩백부터) 받는다. 실패는 건너뛴다."""
    res = PoolRefresh(symbols=len(pool))
    day = uni.DAY_MS
    for i, sym in enumerate(pool):
        last = store.last_open_time(sym, POOL_TIMEFRAME)
        since = last + day if last is not None else now_ms() - lookback_days * day
        if since + day > now_ms():
            continue  # 새로 닫힌 봉이 없다 — 요청하지 않는다
        try:
            res.stored += backfill_symbol(
                exchange, store, sym, POOL_TIMEFRAME, since, sleeper=sleeper, now_ms=now_ms
            )
        except Exception:  # noqa: BLE001 — 한 종목 실패가 풀 전체를 멈추면 안 된다
            logger.exception("후보 풀 일봉 수집 실패: %s(다음 갱신에서 다시 시도)", sym)
            res.failed.append(sym)
        if i + 1 < len(pool) and request_interval_seconds > 0:
            sleeper(request_interval_seconds)
    return res


def describe_change(
    snap: RotationSnapshot, prev: RotationSnapshot | None
) -> tuple[list[str], list[str]]:
    """(새로 들어온 종목, 빠진 종목) — 이전 주가 없으면 전부 새로 들어온 것이다."""
    now = snap.members
    before = prev.members if prev is not None else frozenset()
    return sorted(now - before), sorted(before - now)


def short_symbol(sym: str) -> str:
    return sym.split("/", 1)[0]


def format_rotation_message(snap: RotationSnapshot, prev: RotationSnapshot | None) -> str:
    """주간 목록 알림(평문 — 심볼 이름에 서식 문자가 끼어도 깨지지 않게, WAN-321)."""
    added, removed = describe_change(snap, prev)
    lines = [
        f"🗂 팔 종목 목록 — {format_kst_zoned(snap.rebalance_ms)} 주",
        f"후보 풀 {snap.pool_size} · 자격 {snap.scored_size} · 상위 {snap.top_n}",
    ]
    if prev is None:
        lines.append("첫 목록: " + " ".join(short_symbol(s) for s in sorted(snap.members)))
    else:
        lines.append("들어옴: " + (" ".join(short_symbol(s) for s in added) or "없음"))
        lines.append("빠짐: " + (" ".join(short_symbol(s) for s in removed) or "없음"))
        if removed:
            lines.append("빠진 종목의 보유 포지션은 유지하고 새 진입만 막습니다.")
    if snap.stale_size:
        lines.append(f"⚠️ 직전 일봉이 아직 없는 풀 종목 {snap.stale_size}개(수집 실패·지연)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 수집기 안 주기 작업
# ---------------------------------------------------------------------------


class MarketSource(FetchOHLCV, Protocol):
    def load_markets(self, reload: bool = ...) -> Mapping[str, Mapping[str, Any]]: ...


#: 풀 중 직전 일봉이 없는 종목이 이 비율을 넘으면 목록을 저장하지 않고 다시 시도한다 — 수집이
#: 통째로 실패한 날 엉뚱한 목록이 그 주의 기록으로 굳지 않게. 늦게 계산해도 그 시각까지 닫힌
#: 일봉만 읽으므로 결과는 같다(지연은 무해하다).
MAX_STALE_FRACTION = 0.10
#: 저장을 미뤘을 때 다시 시도할 간격(초).
RETRY_SECONDS = 1800.0


@dataclass(frozen=True)
class UniverseRun:
    """`run_universe_once` 한 번의 결과."""

    refresh: PoolRefresh
    saved: RotationSnapshot | None = None
    """이번에 새로 저장한 목록."""
    deferred: RotationSnapshot | None = None
    """계산은 했지만 직전 일봉 누락이 많아 저장을 미룬 목록(곧 다시 시도한다)."""


def run_universe_once(
    exchange: MarketSource,
    store: OhlcvStore,
    universe: UniverseStore,
    *,
    lookback_days: int,
    request_interval_seconds: float,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    sleeper: Callable[[float], None] = time.sleep,
    notify: Callable[[str], None] | None = None,
) -> UniverseRun:
    """풀 갱신 → 이번 주 목록이 없으면 계산·저장(동기 · 수집기 스레드에서 돈다)."""
    markets = exchange.load_markets(True)
    pool = pool_symbols(markets)
    # 감시가 「지금 풀에 있는 종목」의 1d만 보도록 그때의 풀을 남긴다(WAN-471 · 상폐 종목 제외).
    if not universe.save_pool(pool, captured_ms=now_ms()):
        logger.warning("후보 풀이 비었습니다 — 마지막으로 저장한 풀 목록을 그대로 둡니다")
    refresh = refresh_pool_daily(
        exchange,
        store,
        pool,
        lookback_days=lookback_days,
        request_interval_seconds=request_interval_seconds,
        sleeper=sleeper,
        now_ms=now_ms,
    )
    logger.info(
        "후보 풀 일봉 갱신: 풀 %d · %d봉 저장 · 실패 %d",
        refresh.symbols,
        refresh.stored,
        len(refresh.failed),
    )
    now = now_ms()
    t = uni.rebalance_at(now)
    if universe.snapshot(t) is not None:
        return UniverseRun(refresh)
    snap = compute_rotation(store, pool, t, computed_ms=now)
    if snap.stale_size > MAX_STALE_FRACTION * max(snap.pool_size, 1):
        logger.error(
            "팔 종목 목록 저장 보류: 풀 %d 중 %d종목이 %s에 닫힌 일봉이 없다 — 다시 시도합니다",
            snap.pool_size,
            snap.stale_size,
            format_kst_zoned(t),
        )
        return UniverseRun(refresh, deferred=snap)
    if not universe.save(snap):
        return UniverseRun(refresh)
    prev = universe.previous(t)
    logger.info(
        "팔 종목 목록 저장: %s 주 · 자격 %d · 직전 일봉 없음 %d",
        format_kst_zoned(t),
        snap.scored_size,
        snap.stale_size,
    )
    if notify is not None:
        try:
            notify(format_rotation_message(snap, prev))
        except Exception:  # noqa: BLE001 — 알림 실패가 수집기를 죽이면 안 된다
            logger.exception("팔 종목 목록 알림 실패(무시)")
    return UniverseRun(refresh, saved=snap)


def next_refresh_ms(now: int, delay_seconds: float) -> int:
    """다음 갱신 시각 = 다음 00:00 UTC + 지연(일봉이 닫힌 직후)."""
    day = uni.DAY_MS
    return (now // day + 1) * day + int(delay_seconds * 1000)


async def run_universe_loop(
    exchange: MarketSource,
    store: OhlcvStore,
    universe: UniverseStore,
    *,
    lookback_days: int,
    request_interval_seconds: float,
    delay_seconds: float,
    notify: Callable[[str], None] | None = None,
) -> None:
    """기동 직후 한 번, 그 뒤 매일 00:00 UTC + 지연마다 `run_universe_once`(무한 루프).

    실패하거나 목록 저장을 미뤘으면 다음 날이 아니라 `RETRY_SECONDS` 뒤에 다시 한다.
    """
    while True:
        retry = False
        try:
            run = await asyncio.to_thread(
                run_universe_once,
                exchange,
                store,
                universe,
                lookback_days=lookback_days,
                request_interval_seconds=request_interval_seconds,
                notify=notify,
            )
            retry = run.deferred is not None
        except Exception:  # noqa: BLE001 — 곧 다시 시도한다(스트림은 계속)
            logger.exception("후보 풀 갱신·목록 계산 실패 — 다시 시도합니다")
            retry = True
        now = int(time.time() * 1000)
        wait = (next_refresh_ms(now, delay_seconds) - now) / 1000
        if retry:
            wait = min(wait, RETRY_SECONDS)
        await asyncio.sleep(max(1.0, wait))


def rotation_rows(snap: RotationSnapshot) -> Iterable[tuple[int, str, float, bool]]:
    """(순위, 심볼, 거래대금, 목록 안) — 표시용."""
    members = snap.members
    for i, (s, v) in enumerate(snap.ranked):
        yield i + 1, s, v, s in members


def render_rotation(
    snap: RotationSnapshot | None,
    prev: RotationSnapshot | None,
    *,
    want_ms: int,
    extra: int = 5,
) -> str:
    """`alphablock universe` 본문 — 그 주 목록 · 지난주 대비 변화 · 순위표(목록 밖 몇 개 포함)."""
    head = f"# 팔 종목 목록 — {format_kst_zoned(want_ms)} 주(월요일 00:00 UTC)"
    if snap is None:
        return (
            head + "\n\n이 주의 목록이 아직 없습니다"
            "(수집기 `ALPHABLOCK_UNIVERSE_ENABLED` · 풀 일봉을 확인하세요)."
        )
    lines = [head, ""]
    if snap.rebalance_ms != want_ms:
        lines.append(
            f"⚠️ 이 주 목록이 아직 없어 가장 최근 목록({format_kst_zoned(snap.rebalance_ms)} 주)을"
            " 보여 줍니다."
        )
    lines.append(
        f"계산 {format_kst_zoned(snap.computed_ms)} · 후보 풀 {snap.pool_size} · "
        f"자격 {snap.scored_size} · 상위 {snap.top_n}"
        + (f" · ⚠️ 직전 일봉 없음 {snap.stale_size}" if snap.stale_size else "")
    )
    added, removed = describe_change(snap, prev)
    if prev is not None:
        lines.append("들어옴: " + (" ".join(short_symbol(s) for s in added) or "없음"))
        lines.append("빠짐: " + (" ".join(short_symbol(s) for s in removed) or "없음"))
    lines += ["", "| 순위 | 종목 | 30일 평균 거래대금(USD) | 목록 |", "| --: | -- | --: | -- |"]
    shown = snap.top_n + extra
    for rank, sym, liq, member in rotation_rows(snap):
        if rank > shown:
            break
        mark = ("✅" if member else "") + (" (새로)" if prev is not None and sym in added else "")
        lines.append(f"| {rank} | {short_symbol(sym)} | {liq:,.0f} | {mark} |")
    return "\n".join(lines)
