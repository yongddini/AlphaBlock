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
from typing import TYPE_CHECKING

from data.backfill import (
    DEFAULT_LIMIT,
    FetchOHLCV,
    backfill_symbol,
    page_progress,
    resolve_last_open_time,
)
from data.derived_bars import build_all_derived, source_closes_bucket
from data.derived_bars import build_derived as _build_derived
from data.models import Candle, timeframe_to_ms
from data.storage import OhlcvStore
from data.universe import ARM_STREAM_TIMEFRAMES, BUILT_TIMEFRAMES
from data.universe_pool import (
    POOL_TIMEFRAME,
    UniverseStore,
    pool_members_latest,
    rotation_members_at,
)

if TYPE_CHECKING:
    from config.settings import Settings

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


@dataclass(frozen=True)
class MonitorScope:
    """신선도 감시가 보는 시리즈 = **지금 수집 계획에 있는 것**(WAN-458).

    감시(`alphablock watch`)·`status`·Health 화면은 원래 DB에 저장된 **모든** 시리즈를 봤다.
    팔 목록이 매주 바뀌면서 빠진 종목은 수집기가 일부러 안 모으는데, 그 봉이 「멈춤」으로 판정돼
    거짓 경보가 매시간 쌓였다(2026-10-19 첫 교체 직후). 이 범위는 수집기와 **같은 함수**
    (`build_plan`)로 계산한다 — 감시와 수집이 다른 목록을 보면 같은 병이 다른 모양으로 돌아온다.
    """

    pairs: frozenset[Pair]
    any_symbol_timeframes: frozenset[str] = frozenset()
    """종목과 무관하게 모으는 TF — **후보 풀 목록을 못 읽었을 때만** 쓰는 옛 동작(WAN-471).

    풀(지금 상장된 USDT 무기한 전체)은 원래 DB에 목록으로 남지 않아 풀 일봉(`1d`)을 「저장된 것
    전부」로 감시했다 — 그러자 상폐된 종목(EPIC, 2026-10-22)의 1d가 다시는 안 오는데도 영원히
    「수집 지연」으로 울렸다. 지금은 수집기가 매일 풀을 `universe_pool_members`에 남기고, 그것을
    읽으면 풀 일봉이 종목 단위 `pairs`로 들어가 이 집합은 비어 있다. 못 읽으면(첫 갱신 전·표
    없음) 감시가 꺼지지 않게 옛 동작으로 돌아간다(WAN-458 규칙).
    """
    pool_symbols: frozenset[str] | None = None
    """마지막으로 확인한 후보 풀(WAN-471) — 못 읽었거나 팔이 꺼져 있으면 None."""

    def left_pool(self, symbol: str) -> bool:
        """이 종목이 마지막 후보 풀에 **없는가** — 상장 폐지·정산으로 풀에서 빠진 종목(WAN-471).

        풀을 모르면(None) False다 — 모르는 것을 「상폐」로 지어내지 않는다.
        """
        return self.pool_symbols is not None and symbol not in self.pool_symbols

    def covers(self, symbol: str, timeframe: str) -> bool:
        return (symbol, timeframe) in self.pairs or timeframe in self.any_symbol_timeframes

    def collects_timeframe(self, timeframe: str) -> bool:
        """이 TF를 **어느 종목에서든** 모으는가 (WAN-466).

        False면 그 TF는 설정상 아무도 안 모으는 TF(예전에 받아 둔 5m 등 · WAN-157 「미추적」)이고,
        True인데 `covers`가 False면 다른 종목에서는 모으지만 이 종목은 지금 계획 밖이다(팔
        목록에서 빠진 종목 등). 두 경우는 사람이 할 일이 달라 사유를 가른다.
        """
        return timeframe in self.any_symbol_timeframes or any(
            tf == timeframe for _, tf in self.pairs
        )


def monitor_scope(
    db_path: str | Path,
    *,
    base_symbols: Sequence[str],
    base_timeframes: Sequence[str],
    universe_enabled: bool,
    now_ms: int,
) -> MonitorScope | None:
    """지금 수집기가 모으는 시리즈 범위 — 계획을 못 읽으면 None(호출부는 옛 동작으로 돌아간다).

    * 팔이 꺼져 있으면(`universe_enabled=False`) 기본 격자뿐이다 — 수집기도 그것만 받는다.
    * 팔이 켜져 있으면 기본 격자 + (그 주 목록 ∪ 보유 종목) × (팔 TF + 수집기가 접어 저장하는 3h)
      + 풀 일봉. 그 주 목록을 못 읽으면(표 없음 · 아직 첫 목록 전) **None** — 감시가 꺼지는
      쪽으로 실패하면 안 된다.
    * 풀 일봉은 **마지막으로 저장한 후보 풀**의 종목만이다(WAN-471 — 상폐 종목은 풀에서 빠진다).
      풀 목록을 못 읽으면 1d만 옛 동작(저장된 1d 전부)으로 돌아간다.
    """
    if not universe_enabled:
        return MonitorScope(pairs=frozenset(build_plan(base_symbols, base_timeframes).pairs()))
    members = rotation_members_at(db_path, now_ms)
    if members is None:
        return None
    arm = set(members) | held_symbols(db_path)
    plan = build_plan(base_symbols, base_timeframes, arm)
    built = {(s, tf) for s in plan.arm_symbols for tf in BUILT_TIMEFRAMES}
    pool = pool_members_latest(db_path)
    if pool is None:
        return MonitorScope(
            pairs=frozenset(plan.pairs()) | frozenset(built),
            any_symbol_timeframes=frozenset({POOL_TIMEFRAME}),
        )
    pool_daily = {(s, POOL_TIMEFRAME) for s in pool}
    return MonitorScope(
        pairs=frozenset(plan.pairs()) | frozenset(built) | frozenset(pool_daily),
        pool_symbols=pool,
    )


def monitor_scope_for(settings: Settings, *, now_ms: int | None = None) -> MonitorScope | None:
    """설정에서 **지금 수집 계획** 범위를 읽는다(WAN-458 · WAN-466).

    감시·`status`·Health 화면(WAN-458)과 **수집기 시작 갭 복구·`alphablock backfill --repair`
    (WAN-466)**가 모두 이 함수를 쓴다 — 어느 하나라도 다른 목록을 보면 목록에서 빠진 종목이 그
    경로에서 「🚨 수집 정지」로 다시 울린다(WAN-458이 감시 세 곳만 좁혀 수집기 시작 복구가 남았던
    것이 WAN-466 사고다). 계획을 못 읽으면 None(옛 동작) — 읽다가 예외가 나도 감시를 끄지 않고
    옛 동작으로 돌아간다.

    (WAN-466 전에는 `dashboard.health_data`에 있었다 — `data`가 `dashboard`를 import할 수 없어
    여기로 옮기고 그쪽은 재노출한다.)
    """
    try:
        return monitor_scope(
            settings.db_path,
            base_symbols=settings.symbols,
            base_timeframes=settings.timeframes,
            universe_enabled=settings.universe_enabled,
            now_ms=now_ms if now_ms is not None else int(time.time() * 1000),
        )
    except Exception:  # noqa: BLE001 — 범위 계산 실패가 감시를 멈추면 안 된다(옛 동작으로)
        logger.warning(
            "수집 계획 범위를 못 읽었습니다 — 옛 동작(저장된 시리즈 전부)으로", exc_info=True
        )
        return None


def backfill_pairs(
    exchange: FetchOHLCV,
    store: OhlcvStore,
    pairs: Sequence[Pair],
    *,
    lookback_days: Callable[[str], int],
    pause_seconds: float,
    page_pause_seconds: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    progress: Callable[[], None] | None = None,
    last_open_times: Mapping[Pair, int | None] | None = None,
) -> int:
    """(심볼, TF)마다 저장된 마지막 봉 다음부터(없으면 룩백부터) 받는다 — 요청 사이에 간격을 둔다.

    `last_open_times`(WAN-456)는 `backfill_all`과 같다 — 접속 순간의 스냅샷이 있으면 그 값에서
    시작한다(쌍 사이 간격 때문에 이 경로가 특히 오래 걸려 스트림이 앞질러 가기 쉽다).

    `progress`는 쌍 하나를 시작할 때마다, 그리고 **쪽을 하나 저장할 때마다**(WAN-468) 부른다 —
    수집기 워치독의 진행 표시(WAN-451 · 아래 `ArmCollection.catch_up` 참고). 안 주면 예전과 같다.

    팔 종목은 처음 들어오면 TF 9개 × 몇 쪽이라 한꺼번에 쏘면 바이낸스 가중치 한도(분당 2400)를
    넘는다. 기본 격자(`backfill_all`)는 예전 그대로 두고, 팔 쪽만 이 경로로 천천히 받는다.
    한 쌍이 실패해도 나머지는 계속 받는다(다음 따라잡기에서 다시 시도된다).
    """
    total = 0
    on_page = page_progress(progress)
    for i, (symbol, timeframe) in enumerate(pairs):
        if progress is not None:
            progress()
        tf_ms = timeframe_to_ms(timeframe)
        last = resolve_last_open_time(store, symbol, timeframe, last_open_times)
        since = (
            last + tf_ms if last is not None else now_ms() - lookback_days(timeframe) * 86_400_000
        )
        if since + tf_ms > now_ms():
            continue  # 새로 닫힌 봉이 없다
        try:
            total += backfill_symbol(
                exchange,
                store,
                symbol,
                timeframe,
                since,
                sleeper=sleeper,
                now_ms=now_ms,
                page_pause_seconds=page_pause_seconds,
                progress=on_page,
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
        history_days: int = 0,
        page_pause_seconds: float = 0.2,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._exchange = exchange
        #: 팔 종목 상위TF 이력 깊이(WAN-451 오래된 존 기억 — 러너가 처음에 이만큼으로 탐지한다).
        #: 1분봉은 해당 없다(탐지에 안 쓴다).
        self._history_days = history_days
        self._page_pause = page_pause_seconds
        #: 이력 확장 한 쪽의 봉 수(거래소 쪽 크기 · 테스트가 줄인다).
        self._page_limit = DEFAULT_LIMIT
        #: 이력 확장을 이미 시도한 쌍 — 상장이 그보다 늦은 종목을 매번 다시 요청하지 않게.
        self._extended: set[Pair] = set()
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

    def _arm_lookback(self, timeframe: str) -> int:
        base = self._lookback_days(timeframe)
        return base if timeframe == "1m" else max(base, self._history_days)

    def fill(
        self,
        pairs: Sequence[Pair],
        arm_symbols: Iterable[str],
        *,
        progress: Callable[[], None] | None = None,
        last_open_times: Mapping[Pair, int | None] | None = None,
    ) -> int:
        """주어진 쌍을 천천히 채우고 팔 종목의 생성 TF(3h)를 이어 만든다(동기 · 스레드에서 돈다).

        저장분이 없는 쌍은 처음부터 이력 깊이(`history_days`)만큼 받는다 — 새로 들어온 종목도
        러너가 처음 탐지할 때 몇 년 된 존까지 알 수 있게.
        """
        n = backfill_pairs(
            self._exchange,
            self._store,
            pairs,
            lookback_days=self._arm_lookback,
            pause_seconds=self._pause,
            page_pause_seconds=self._page_pause,
            now_ms=self._now_ms,
            progress=progress,
            last_open_times=last_open_times,
        )
        n += build_all_derived(self._store, sorted(set(arm_symbols)), progress=progress)
        return n

    def extend_history(self, plan: CollectionPlan | None = None) -> int:
        """팔 쌍의 상위TF 이력을 `history_days`까지 뒤로 늘린다(동기 · 백그라운드).

        `plan`이 주어지면 그 계획의 팔 쌍을 본다 — 운영 중 교체(WAN-468)는 새 계획을
        **채택하기 전에** 이 일을 마쳐야 하므로(그동안 옛 스트림을 감시하며) 아직 `self.plan`이
        아닌 계획을 넘긴다.

        저장된 첫 봉이 목표보다 늦으면 그 앞을 받는다. 상장이 더 늦은 종목은 거래소가 더 줄 게
        없으므로 쌍마다 한 번만 시도한다. 늘린 뒤 3h를 앞 구간까지 다시 접는다.

        🚨 **뒤에서 앞으로 한 쪽씩 받는다(WAN-468 재시작 고리 방지).** 예전에는 목표 시점부터
        앞으로 쪽을 넘겼는데, 중간에 프로세스가 죽으면 저장된 첫 봉이 이미 목표에 닿아 있어 다음
        기동이 「다 늘렸다」로 건너뛰고 **받다 만 구간과 옛 첫 봉 사이의 구멍이 영구히
        남았다**(기동 갭 복구는 최근 7일만 본다). 저장된 첫 봉 바로 앞 한 쪽씩 받으면 어디서
        죽어도 저장분이 이어져 있고, 다음 기동은 그 첫 봉에서 이어 간다.
        """
        if self._history_days <= 0:
            return 0
        plan = plan or self.plan
        now = self._now_ms()
        want = now - self._history_days * 86_400_000
        total = 0
        for symbol, timeframe in self.arm_pairs(plan):
            if timeframe == "1m" or (symbol, timeframe) in self._extended:
                continue
            first = self._store.first_open_time(symbol, timeframe)
            self._extended.add((symbol, timeframe))
            tf_ms = timeframe_to_ms(timeframe)
            if first is None or first <= want + tf_ms:
                continue
            try:
                total += self._extend_backwards(symbol, timeframe, first, want)
            except Exception:  # noqa: BLE001 — 한 쌍 실패가 나머지를 막으면 안 된다
                logger.exception("팔 종목 이력 확장 실패: %s %s", symbol, timeframe)
                self._extended.discard((symbol, timeframe))
            if self._pause > 0:
                time.sleep(self._pause)
        total += build_all_derived(self._store, sorted(plan.arm_symbols))
        if total:
            logger.info("팔 종목 이력 확장: %d봉(목표 %d일)", total, self._history_days)
        return total

    def _extend_backwards(self, symbol: str, timeframe: str, first: int, want: int) -> int:
        """`first` 앞을 한 쪽씩 뒤로 받아 `want`까지 늘린다(각 쪽이 저장분과 이어진다)."""
        tf_ms = timeframe_to_ms(timeframe)
        page_ms = self._page_limit * tf_ms
        total = 0
        while first > want + tf_ms:
            start = max(want, first - page_ms)
            total += backfill_symbol(
                self._exchange,
                self._store,
                symbol,
                timeframe,
                start,
                until_ms=first,
                limit=self._page_limit,
                now_ms=self._now_ms,
            )
            earlier = self._store.first_open_time(symbol, timeframe)
            if earlier is None or earlier >= first:
                break  # 그 앞에는 거래소도 줄 게 없다(상장 이전)
            first = earlier
            if self._page_pause > 0 and first > want + tf_ms:
                time.sleep(self._page_pause)
        return total

    def catch_up(
        self,
        progress: Callable[[], None] | None = None,
        last_open_times: Mapping[Pair, int | None] | None = None,
    ) -> int:
        """지금 계획의 팔 쪽 전부를 따라잡는다(접속 직후 · 기동 직후).

        🚨 기동 때는 **웹소켓 접속 전**에 돌아 그동안 메시지가 없다 — 279쌍 × 0.5초 간격이라
        서버에서 5분이 넘는다. `progress`(수집기 워치독의 진행 표시)를 쌍마다 부르지 않으면
        워치독이 300초에 「멎었다」고 프로세스를 죽이고, 재시작하면 같은 자리에서 또 죽는다
        (2026-10-15 서버 실측: 21:31부터 5~6분 간격 재시작 루프 · 스트림이 한 번도 안 붙음).
        """
        return self.fill(
            self.arm_pairs(),
            self.plan.arm_symbols,
            progress=progress,
            last_open_times=last_open_times,
        )

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
