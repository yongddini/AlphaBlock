"""수집 계획 — 종목마다 어떤 TF를 받을지 (WAN-451 ③ 단계 · 결정 ① 나).

수집기는 원래 「설정 심볼 × 설정 TF」 고정 격자 하나만 받았다. 스토캐스틱 팔이 붙으면서
종목마다 받는 TF가 달라진다.

* **기본** — 설정 심볼(`ALPHABLOCK_SYMBOLS`) × 설정 TF. 예전 그대로다(BTC 1분봉은 폭락 크기 규칙,
  BTC·ETH 1d는 엔진 C가 읽는다).
* **팔** — 그 주 목록 31종목(`universe_rotations`) **+ 목록에서 빠졌지만 포지션을 들고 있는 종목**
  × 팔 TF(1m + 원천 8개). 2h는 저장소가 1h에서 파생하고(WAN-24), 3h는 수집기가 1h에서 접어
  저장한다(`data.derived_bars` — 1분봉에서 만든 백테스트 3h와 값이 같다, 31종목 54.9만 봉 실측).

팔 쪽은 `ALPHABLOCK_UNIVERSE_ENABLED`가 켜져 있을 때만 붙는다. 목록이 바뀌면(월요일) 수집기가
새 종목의 과거를 채운 뒤 웹소켓을 다시 붙는다(`data.collector`).

보유 종목은 `open_positions` 테이블을 **직접 읽는다** — `data`는 `paper`를 import할 수 없다
(레이어 규칙). 테이블이 없으면(러너가 한 번도 안 떴으면) 빈 집합이다.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from data.backfill import FetchOHLCV, backfill_symbol
from data.derived_bars import build_all_derived, source_closes_bucket
from data.derived_bars import build_derived as _build_derived
from data.models import Candle, timeframe_to_ms
from data.storage import OhlcvStore
from data.universe import ARM_STREAM_TIMEFRAMES
from data.universe_pool import UniverseStore

logger = logging.getLogger(__name__)

Pair = tuple[str, str]


@dataclass(frozen=True)
class CollectionPlan:
    """종목 → 받을 TF(원천 kline만 — 파생·생성 TF는 안 들어간다)."""

    timeframes: Mapping[str, tuple[str, ...]]
    arm_symbols: frozenset[str] = frozenset()
    """팔 쪽으로 들어온 종목(3h를 만들 대상)."""

    @property
    def symbols(self) -> list[str]:
        return sorted(self.timeframes)

    def pairs(self) -> list[Pair]:
        return [(s, tf) for s in self.symbols for tf in self.timeframes[s]]

    def new_pairs(self, before: CollectionPlan) -> list[Pair]:
        old = set(before.pairs())
        return [p for p in self.pairs() if p not in old]


def build_plan(
    base_symbols: Sequence[str],
    base_timeframes: Sequence[str],
    arm_symbols: Iterable[str] = (),
) -> CollectionPlan:
    """기본 격자 + 팔 종목(팔 TF와 합집합). TF 순서는 짧은 것부터 · 중복 없음."""
    want: dict[str, set[str]] = {s: set(base_timeframes) for s in base_symbols}
    arm = frozenset(arm_symbols)
    for s in arm:
        want.setdefault(s, set()).update(ARM_STREAM_TIMEFRAMES)
    return CollectionPlan(
        timeframes={s: tuple(sorted(tfs, key=timeframe_to_ms)) for s, tfs in want.items()},
        arm_symbols=arm,
    )


def held_symbols(db_path: str | Path) -> set[str]:
    """`open_positions`에 포지션이 있는 종목(읽기 전용 · 테이블이 없으면 빈 집합)."""
    path = str(db_path)
    if path != ":memory:" and not Path(path).exists():
        return set()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True) if path != ":memory:" else None
    if conn is None:
        return set()
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='open_positions'"
        ).fetchone()
        if row is None:
            return set()
        return {str(r[0]) for r in conn.execute("SELECT DISTINCT symbol FROM open_positions")}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def backfill_pairs(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    pairs: Sequence[Pair],
    *,
    lookback_days: Callable[[str], int],
    pause_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> int:
    """(심볼, TF)마다 저장된 마지막 봉 다음부터(없으면 룩백부터) 받는다 — 요청 사이에 간격을 둔다.

    팔 종목은 처음 들어오면 TF 9개 × 몇 쪽이라 한꺼번에 쏘면 바이낸스 가중치 한도(분당 2400)를
    넘는다. 기본 격자(`backfill_all`)는 예전 그대로 두고, 팔 쪽만 이 경로로 천천히 받는다.
    한 쌍이 실패해도 나머지는 계속 받는다(다음 따라잡기에서 다시 시도된다).
    """
    total = 0
    for i, (symbol, timeframe) in enumerate(pairs):
        tf_ms = timeframe_to_ms(timeframe)
        last = store.last_open_time(symbol, timeframe)
        since = (
            last + tf_ms if last is not None else now_ms() - lookback_days(timeframe) * 86_400_000
        )
        if since + tf_ms > now_ms():
            continue  # 새로 닫힌 봉이 없다
        try:
            total += backfill_symbol(
                exchange, store, symbol, timeframe, since, sleeper=sleeper, now_ms=now_ms
            )
        except Exception:  # noqa: BLE001 — 한 쌍 실패가 나머지를 막으면 안 된다
            logger.exception("팔 종목 백필 실패: %s %s(다음 따라잡기에서 다시)", symbol, timeframe)
        if pause_seconds > 0 and i + 1 < len(pairs):
            sleeper(pause_seconds)
    return total


class ArmCollection:
    """팔 쪽 수집 계획을 들고, 바뀌면 새 쌍을 채운다(WAN-451 ③ · 수집기 전용).

    `compute()`는 그 순간의 계획을 다시 계산한다(목록 DB · 보유 포지션). 계획 계산은 SQL 두 번이라
    싸다 — 수집기가 1분마다 불러 바뀌었는지 본다.
    """

    def __init__(
        self,
        *,
        exchange: FetchOHLCV,
        store: OhlcvStore,
        universe: UniverseStore,
        db_path: str | Path,
        base_symbols: Sequence[str],
        base_timeframes: Sequence[str],
        lookback_days: Callable[[str], int],
        pause_seconds: float,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._exchange = exchange
        self._store = store
        self._universe = universe
        self._db_path = db_path
        self._base_symbols = list(base_symbols)
        self._base_timeframes = list(base_timeframes)
        self._lookback_days = lookback_days
        self._pause = pause_seconds
        self._now_ms = now_ms
        self.plan = build_plan(self._base_symbols, self._base_timeframes)
        self._base_pairs = set(self.plan.pairs())

    def compute(self) -> CollectionPlan:
        snap = self._universe.at(self._now_ms())
        members = snap.members if snap is not None else frozenset()
        arm = set(members) | held_symbols(self._db_path)
        return build_plan(self._base_symbols, self._base_timeframes, arm)

    def arm_pairs(self, plan: CollectionPlan | None = None) -> list[Pair]:
        """기본 격자 밖의 쌍(팔 쪽이 따로 채워야 하는 것)."""
        return [p for p in (plan or self.plan).pairs() if p not in self._base_pairs]

    def fill(self, pairs: Sequence[Pair], arm_symbols: Iterable[str]) -> int:
        """주어진 쌍을 천천히 채우고 팔 종목의 생성 TF(3h)를 이어 만든다(동기 · 스레드에서 돈다)."""
        n = backfill_pairs(
            self._exchange,
            self._store,
            pairs,
            lookback_days=self._lookback_days,
            pause_seconds=self._pause,
            now_ms=self._now_ms,
        )
        n += build_all_derived(self._store, sorted(set(arm_symbols)))
        return n

    def catch_up(self) -> int:
        """지금 계획의 팔 쪽 전부를 따라잡는다(접속 직후 · 기동 직후)."""
        return self.fill(self.arm_pairs(), self.plan.arm_symbols)

    def adopt(self, plan: CollectionPlan) -> tuple[list[str], list[str]]:
        """새 계획으로 바꾼다 — (들어온 종목, 빠진 종목). 새 쌍 채우기는 호출부가 먼저 한다."""
        added = sorted(plan.arm_symbols - self.plan.arm_symbols)
        removed = sorted(self.plan.arm_symbols - plan.arm_symbols)
        self.plan = plan
        return added, removed

    def on_candle(self, candle: Candle) -> None:
        """스트림 훅 — 팔 종목의 1h가 닫혀 3h 버킷이 완성되면 3h를 만든다."""
        if candle.symbol not in self.plan.arm_symbols:
            return
        for target in source_closes_bucket(candle.timeframe, candle.open_time):
            try:
                _build_derived(self._store, candle.symbol, target)
            except Exception:  # noqa: BLE001 — 스트림을 죽이면 안 된다(따라잡기가 다시 만든다)
                logger.exception("%s %s 생성 실패", candle.symbol, target)
