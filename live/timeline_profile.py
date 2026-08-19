"""타임라인 하루치 적재 단계별 프로파일 (WAN-324 §0).

## 왜 프로파일이 먼저인가

WAN-322 서버 실측이 하루치(48셀) 적재를 **6분 23초**로 쟀는데 `user`는 2분 13초였다 —
약 4분이 **I/O 대기**다(WAN-318 §1이 doctor에서 본 것과 같은 성질). 원인을 **추측으로
고치지 않기 위해** 이 모듈이 한 단계씩 시간을 잰다: 상위TF SQL 읽기 · 1분봉 SQL 읽기 ·
오더블록 탐지 · 서브스텝 평가 · 펀딩 조회.

📌 **CLAUDE.md의 「1분봉 로딩이 1조합 실행의 ~36%」를 그대로 인용하면 안 된다** — 그건
3년 격자 기준이고 여기는 120일 창이라 비중이 다르다(다른 모듈 값 인용 사고). 그래서 이
도구가 **이 경로에서 직접** 잰다.

## 두 모양을 같은 자로 잰다

`--shape per-cell`은 WAN-324 이전 모양(셀마다 `load_market_data`)이고 `--shape shared`는
지금 모양(종목당 1분봉 1회 읽기, `harness.load_market_data_by_timeframe`)이다. 둘을 같은
실행에서 재면 「1분봉 읽기가 실제로 몇 초를 먹고 공유가 그중 얼마를 돌려주는가」가 표
하나로 나온다.

⚠️ **측정 경합을 함께 적을 것** — 이 실측은 러너·수집기·doctor와 디스크를 다툰다(이슈
「범위 밖·경고」). 리포트가 실행 시각·머신·`--jobs`를 함께 찍는 이유다.

## 성격

**순수 측정이다** — 엔진·전략·기본값·토대 불변(`ConfluenceParams()`·`LeverageBookParams()`),
DB에 아무것도 쓰지 않는다, `ALPHABLOCK_LIVE_TRADING=false` 유지. 계측은 `OhlcvStore.load`를
프로파일 구간에서만 감싸고 끝나면 되돌린다 — 프로덕션 경로에는 어떤 오버헤드도 남지 않는다.
"""

from __future__ import annotations

import platform
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from common.timefmt import KST_LABEL, format_kst

if TYPE_CHECKING:
    from backtest.harness import MarketData

__all__ = [
    "SHAPE_PER_CELL",
    "SHAPE_SHARED",
    "CellProfile",
    "DayProfile",
    "profile_day",
    "render_profile",
]

#: 적재 모양 — 예전(셀마다 1분봉 읽기) 대 지금(종목당 1회 읽고 TF가 나눠 씀).
SHAPE_PER_CELL = "per-cell"
SHAPE_SHARED = "shared"

_DAY_MS = 86_400_000


@dataclass(frozen=True)
class CellProfile:
    """한 (심볼, TF) 셀의 단계별 소요 시간(초)과 규모."""

    symbol: str
    timeframe: str
    htf_bars: int
    bars_1m: int
    rows: int
    detect_s: float
    eval_s: float


@dataclass
class SymbolProfile:
    """한 심볼의 로드 시간 — SQL은 셀이 아니라 **심볼 단위**로 잰다(공유 모양의 단위)."""

    symbol: str
    htf_sql_s: float = 0.0
    sql_1m_s: float = 0.0
    reads_1m: int = 0
    cells: list[CellProfile] = field(default_factory=list)


@dataclass(frozen=True)
class DayProfile:
    """하루치 적재 프로파일 한 장."""

    day_key: str
    shape: str
    warmup_days: int
    jobs: int
    machine: str
    measured_at_ms: int
    wall_s: float
    symbols: tuple[SymbolProfile, ...]

    @property
    def htf_sql_s(self) -> float:
        return sum(s.htf_sql_s for s in self.symbols)

    @property
    def sql_1m_s(self) -> float:
        return sum(s.sql_1m_s for s in self.symbols)

    @property
    def reads_1m(self) -> int:
        return sum(s.reads_1m for s in self.symbols)

    @property
    def detect_s(self) -> float:
        return sum(c.detect_s for s in self.symbols for c in s.cells)

    @property
    def eval_s(self) -> float:
        return sum(c.eval_s for s in self.symbols for c in s.cells)

    @property
    def cell_count(self) -> int:
        return sum(len(s.cells) for s in self.symbols)

    @property
    def accounted_s(self) -> float:
        return self.htf_sql_s + self.sql_1m_s + self.detect_s + self.eval_s


@contextmanager
def _timed_store_loads() -> Iterator[dict[str, list[float]]]:
    """프로파일 구간에서만 `OhlcvStore.load`를 TF별로 계측한다(끝나면 원복).

    ⚠️ 파생 TF(`2h`)는 내부에서 원본(`1h`)을 `_load_native`로 읽으므로 요청한 TF 이름
    하나로만 잡힌다 — 「1h를 두 번 읽었다」로 이중 계상되지 않는다.
    """
    from data.storage import OhlcvStore

    totals: dict[str, list[float]] = {}
    original = OhlcvStore.load

    def timed(
        self: OhlcvStore,
        symbol: str,
        timeframe: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> object:
        t0 = time.perf_counter()
        try:
            return original(self, symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
        finally:
            totals.setdefault(timeframe, []).append(time.perf_counter() - t0)

    OhlcvStore.load = timed  # type: ignore[method-assign]
    try:
        yield totals
    finally:
        OhlcvStore.load = original  # type: ignore[method-assign]


def _load_markets(
    symbol: str,
    timeframes: Sequence[str],
    *,
    shape: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, MarketData]:
    """모양에 따라 그 심볼의 TF별 `MarketData`를 만든다 — 산출물은 두 모양이 같다."""
    from backtest.harness import load_market_data, load_market_data_by_timeframe

    if shape == SHAPE_SHARED:
        return dict(
            load_market_data_by_timeframe(
                symbol, timeframes, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=False
            )
        )
    if shape != SHAPE_PER_CELL:
        raise ValueError(f"알 수 없는 적재 모양: {shape!r} ({SHAPE_PER_CELL} 또는 {SHAPE_SHARED})")
    return {
        tf: load_market_data(
            symbol, tf, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=False
        )
        for tf in timeframes
    }


def profile_day(
    *,
    day_start_ms: int,
    day_end_ms: int,
    day_key: str,
    symbols: Sequence[str] | None = None,
    timeframes: Sequence[str] | None = None,
    warmup_days: int | None = None,
    shape: str = SHAPE_SHARED,
) -> DayProfile:
    """하루치 적재를 단계별로 재서 한 장으로 낸다(직렬 — 단계 시간을 섞지 않으려고).

    `jobs`를 받지 않는 것은 의도다: 병렬로 재면 워커들이 디스크·CPU를 다퉈 **어느 단계가
    몇 초인지**가 흐려진다. 이 도구가 답하는 질문은 「전체가 몇 분인가」가 아니라 「시간이
    어디서 나가는가」다(전체 시간은 `alphablock trades --persist-cache`를 `time`으로 잰다).
    """
    from backtest.harness import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES, detect_order_blocks
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS
    from live.trade_timeline import cell_setup_timeline
    from strategy.models import OrderBlockParams

    syms = list(symbols) if symbols is not None else list(DEFAULT_SYMBOLS)
    tfs = list(timeframes) if timeframes is not None else list(DEFAULT_TIMEFRAMES)
    warm = warmup_days if warmup_days is not None else DEFAULT_WARMUP_DAYS
    warmup_start_ms = day_start_ms - warm * _DAY_MS

    measured_at_ms = int(time.time() * 1000)
    wall0 = time.perf_counter()
    profiles: list[SymbolProfile] = []
    for symbol in syms:
        prof = SymbolProfile(symbol)
        with _timed_store_loads() as sql:
            markets = _load_markets(
                symbol, tfs, shape=shape, start_ms=warmup_start_ms, end_ms=day_end_ms
            )
        durations_1m = sql.pop("1m", [])
        prof.sql_1m_s = sum(durations_1m)
        prof.reads_1m = len(durations_1m)
        prof.htf_sql_s = sum(d for spans in sql.values() for d in spans)

        for tf in tfs:
            market = markets[tf]
            if market.htf_df.empty or market.df_1m.empty:
                prof.cells.append(CellProfile(symbol, tf, 0, 0, 0, 0.0, 0.0))
                continue
            t0 = time.perf_counter()
            ob_result = detect_order_blocks(market, OrderBlockParams())
            detect_s = time.perf_counter() - t0
            t0 = time.perf_counter()
            rows = cell_setup_timeline(
                market, ob_result, day_start_ms=day_start_ms, day_end_ms=day_end_ms
            )
            eval_s = time.perf_counter() - t0
            prof.cells.append(
                CellProfile(
                    symbol,
                    tf,
                    len(market.htf_df),
                    len(market.df_1m),
                    len(rows),
                    detect_s,
                    eval_s,
                )
            )
        profiles.append(prof)

    return DayProfile(
        day_key=day_key,
        shape=shape,
        warmup_days=warm,
        jobs=1,
        machine=f"{platform.system()} {platform.machine()} · CPU {_cpu_count()}",
        measured_at_ms=measured_at_ms,
        wall_s=time.perf_counter() - wall0,
        symbols=tuple(profiles),
    )


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 0


def _pct(part: float, whole: float) -> str:
    return f"{part / whole * 100:5.1f}%" if whole > 0 else "    —"


def render_profile(profile: DayProfile) -> str:
    """사람이 읽는 단계별 표. 시각은 KST(WAN-172)."""
    total = profile.accounted_s
    lines = [
        f"# 타임라인 하루치 적재 프로파일 — {profile.day_key} (WAN-324 §0)",
        "",
        f"- 측정 시각: {format_kst(profile.measured_at_ms)} {KST_LABEL}",
        f"- 머신: {profile.machine}",
        f"- 적재 모양: `{profile.shape}` · 워밍업 {profile.warmup_days}일 · 직렬(jobs=1)",
        f"- 셀: {profile.cell_count}개 ({len(profile.symbols)}종목)",
        f"- 벽시계: {profile.wall_s:.1f}s · 단계 합계: {total:.1f}s",
        "",
        "⚠️ 이 실측은 러너·수집기·doctor와 디스크를 다툰다 — 경합 상태를 함께 적을 것.",
        "",
        "## 단계별",
        "",
        "| 단계 | 초 | 비중 |",
        "| --- | ---: | ---: |",
        f"| 1분봉 SQL 읽기 ({profile.reads_1m}회) | {profile.sql_1m_s:8.2f} "
        f"| {_pct(profile.sql_1m_s, total)} |",
        f"| 상위TF SQL 읽기 | {profile.htf_sql_s:8.2f} | {_pct(profile.htf_sql_s, total)} |",
        f"| 오더블록 탐지 | {profile.detect_s:8.2f} | {_pct(profile.detect_s, total)} |",
        f"| 서브스텝 평가 | {profile.eval_s:8.2f} | {_pct(profile.eval_s, total)} |",
        "| 펀딩 조회 | 0.00 | (이 경로는 `funding=False`라 아예 조회하지 않는다) |",
        "",
        "## 심볼별",
        "",
        "| 심볼 | 1분봉 읽기 | 초 | 상위TF 초 | 탐지 초 | 평가 초 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sym in profile.symbols:
        detect_s = sum(c.detect_s for c in sym.cells)
        eval_s = sum(c.eval_s for c in sym.cells)
        lines.append(
            f"| {sym.symbol} | {sym.reads_1m}회 | {sym.sql_1m_s:6.2f} "
            f"| {sym.htf_sql_s:6.2f} | {detect_s:6.2f} | {eval_s:6.2f} |"
        )
    lines += [
        "",
        "## 셀별",
        "",
        "| 심볼 | TF | 상위TF봉 | 1분봉 | 행 | 탐지 초 | 평가 초 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sym in profile.symbols:
        for cell in sym.cells:
            lines.append(
                f"| {cell.symbol} | {cell.timeframe} | {cell.htf_bars} | {cell.bars_1m} "
                f"| {cell.rows} | {cell.detect_s:6.2f} | {cell.eval_s:6.2f} |"
            )
    return "\n".join(lines) + "\n"
