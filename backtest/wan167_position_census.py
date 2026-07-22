"""WAN-167 — 타임프레임·종목 가로질러 동시에 열리는 포지션 census.

사용자 정의(2026-07-22): *"타임프레임이 다른 곳에서 다중 진입이 허용된다고 한 거야. 같은
프레임에서는 익절하기 전엔 한 번만 들어가야지."* 즉 진입 단위 = **(종목, 타임프레임) 칸**이고,
칸 안에서는 청산 전 1포지션(채택 기본값 그대로), 대신 BTC 15m과 BTC 1h는 **별개 칸**이라
동시에 열릴 수 있다. 이 모듈은 그 정의대로 각 칸을 **오늘의 채택 엔진 그대로** 독립 실행해
진입·청산 시각을 뽑고, 하나의 공통 시간축 위에서 **동시에 열려 있는 칸 수**를 센다.

## 왜 먼저 세나 (레버리지 북 WAN-169의 사전 조사)

겹침이 거의 안 나면 심볼 간 공유 자본·레버리지 북(WAN-169)을 지어도 실익이 작다. WAN-108이
옛 엔진(3심볼 · 병합 존 · 게이트 on · tap 밴드 · 0bp)에서 **역대 최대 동시 겹침 수준은
시간의 0.13%(1h)·0.004%(multi_tf)에만 성립**한다고 관찰했는데, 그때는 그랬고 오늘 엔진에선
잰 적이 없다. 이 census가 크기를 먼저 보여줘야 WAN-169가 풀 문제의 규모가 잡힌다.

## 엔진이 아니라 자(尺)다

손익·사이징·자본 공유는 **하나도 계산하지 않는다** — 각 칸은 기존 단일 포지션 백테스트
그대로이고(`harness.run_once`, 채택 기본값), 이 모듈이 새로 하는 일은 그 거래들의
`[entry_time, exit_time)` 구간을 겹쳐 세는 것뿐이다. 같은 칸 안의 겹침은 정의상 없어야
하며(동시 1포지션), 있으면 **ValueError로 거부**한다(엔진 전제가 깨졌다는 뜻이므로 조용히
세면 안 된다).

## 스코프 (한 구간 집합을 여러 자로 잰다)

* `cells/main` — **본 census**: 6심볼 × 15m·1h = 12칸의 동시 열림 수.
* `cells/all_tf` — 대조: 4h·1d까지 24칸(4h·1d는 표본이 얇아 대조로만, WAN-143 게이트와
  같은 이유).
* `cells_tf/<tf>` — 한 TF 안 6심볼(종목 간 겹침의 TF 단면 · WAN-108의 1h 축과 같은 모양).
* `symbols/main` — 동시에 포지션을 든 **종목 수**(칸 수가 아니라 · 종목 간 겹침).
* `symbols_multi_tf/main` — 같은 종목의 15m·1h **두 칸을 동시에** 든 종목 수(종목 내
  TF 간 겹침 — 사용자 정의의 "BTC 15m + BTC 1h").
* `intra_symbol/<sym>` — 한 종목의 15m·1h 두 칸만 놓고 센 동시 열림 수.
* `wan108/1h_3sym` · `wan108/multitf_3sym` — WAN-108 관찰(0.13%/0.004%)의 오늘 엔진
  재현축(BTC·ETH·SOL, 각각 1h 단독 · 4TF 전부). ⚠️ 그쪽은 옛 엔진 + 다중 포지션
  시퀀서였으므로 숫자가 같아야 할 이유는 없다 — 자릿수만 비교한다.

렌즈 `baseline` 단독(WAN-128) · 못 박은 창(2023-07-14~2026-07-15, WAN-111) · 기본값·토대
불변(측정 전용, `ALPHABLOCK_LIVE_TRADING=false` 유지).

## 재현

```
uv run python -m backtest.wan167_position_census --jobs 6
uv run python -m backtest.wan167_position_census --from-csv   # 요약·census만 재생성
```
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.run import parse_date_ms
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_INTERVALS_CSV = REPORTS_DIR / "wan167_position_intervals.csv"
DEFAULT_CENSUS_CSV = REPORTS_DIR / "wan167_position_census.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan167_position_census_summary.md"

#: 못 박은 창 — WAN-111/114/145/164와 동일(`--years N`은 미끄러진다).
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 6심볼(WAN-111).
ALL_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "TRXUSDT",
)

#: 본 census의 TF = 두 작업 TF(WAN-107).
MAIN_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 대조 TF — 표본이 얇아(WAN-143: 4h 19.7거래·1d 2.0거래) 판정 축이 아니라 대조로만 센다.
CONTROL_TIMEFRAMES: tuple[str, ...] = ("4h", "1d")

ALL_TIMEFRAMES: tuple[str, ...] = MAIN_TIMEFRAMES + CONTROL_TIMEFRAMES

#: WAN-108 재현축의 3심볼(그쪽 격자가 이 셋이었다).
WAN108_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

#: 🚨 오늘 엔진 = 채택 기본값 그대로(분리 존 + 필터 1.28 + 봉내 라이브 + 게이트 없음 +
#: 오프셋 2bp). **옛 핀(`LEGACY_*`)을 하나도 물려받지 않는다** — 이슈 완료기준.
ADOPTED_OB_PARAMS = OrderBlockParams()

#: 공식 렌즈는 `baseline` 단독(WAN-128) — `harness.build_params()` 기본값이 이미 그것이다.
OFFICIAL_LENS = harness.BASELINE_FILL.name

#: 판정 문턱 — 본 census(`cells/main`)에서 **2칸 이상 겹침**이 전체 시간에서 차지하는 비율.
#: 5% 이상이면 "겹침이 일상"이라 공유 자본 회계(WAN-169)가 풀 문제가 실재하고, 0.5% 미만이면
#: 옛 관찰(WAN-108의 0.13%)과 같은 자릿수라 북을 지어도 실익이 작다. 그 사이는 경계 —
#: 판정 문장이 세 갈래를 다르게 찍는다(문턱을 문장에 박지 않고 코드가 계산한다).
SIGNIFICANT_OVERLAP_SHARE = 0.05
NEGLIGIBLE_OVERLAP_SHARE = 0.005


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class IntervalRow(BaseModel):
    """한 거래의 보유 구간 — census의 원자료.

    `[entry_time, exit_time)` **반개구간**이다: 청산 봉의 시각에 새 진입이 같은 시각으로
    이어져도(같은 칸의 연속 거래) 겹침으로 세지 않는다. 시각은 지정가(B안) 엔진의 1분봉
    서브스텝 해상도다. `window_*`는 census의 공통 분모(못 박은 창)를 행마다 싣는다 —
    CSV만으로 재계산이 닫히게(`--from-csv`).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    side: str
    entry_time: int
    exit_time: int
    window_start: int
    window_end: int


class CensusRow(BaseModel):
    """한 스코프의 동시 열림 수준 하나 — `duration_ms`는 그 수준이 지속된 총 시간.

    한 스코프의 행들을 다 더하면 `duration_ms` 합 = 창 길이, `share` 합 = 1.0이다
    (수준 0 = 아무것도 안 열림 — 도 행으로 낸다. 분포가 완결돼야 "겹침 없음"과
    "안 셌음"이 구분된다).
    """

    model_config = ConfigDict(frozen=True)

    scope_kind: str
    scope_key: str
    level: int
    duration_ms: int
    share: float
    window_start: int
    window_end: int


# --------------------------------------------------------------------------- #
# 스위프라인 (순수 함수 — 테스트가 여기를 고정한다)
# --------------------------------------------------------------------------- #


def validate_single_position(intervals: Sequence[IntervalRow]) -> None:
    """같은 (종목, TF) 칸 안 겹침을 거부한다 — 사용자 정의의 전제(칸 안 동시 1포지션).

    채택 엔진(단일 포지션 경로)은 이를 보장하므로, 걸리면 census가 아니라 **엔진**이
    깨진 것이다. 조용히 세면 "칸 안 스택"이 "칸 간 겹침"으로 둔갑한다. 청산 시각 ==
    다음 진입 시각(같은 1분봉에서 청산·재진입)은 반개구간이라 겹침이 아니다.
    """
    by_cell: dict[tuple[str, str], list[IntervalRow]] = defaultdict(list)
    for row in intervals:
        if row.exit_time < row.entry_time:
            raise ValueError(
                f"{row.symbol} {row.timeframe}: 청산이 진입보다 이르다 "
                f"(entry={row.entry_time}, exit={row.exit_time})"
            )
        by_cell[(row.symbol, row.timeframe)].append(row)
    for (symbol, timeframe), rows in by_cell.items():
        ordered = sorted(rows, key=lambda r: (r.entry_time, r.exit_time))
        for prev, nxt in zip(ordered, ordered[1:], strict=False):
            if nxt.entry_time < prev.exit_time:
                raise ValueError(
                    f"{symbol} {timeframe}: 같은 칸 안에서 포지션이 겹친다 "
                    f"(exit={prev.exit_time} > next entry={nxt.entry_time}) — "
                    "동시 1포지션 전제가 깨졌다(엔진 확인 필요)."
                )


def concurrency_durations(
    intervals: Sequence[IntervalRow],
    *,
    window_start: int,
    window_end: int,
    unit_of: Callable[[IntervalRow], Hashable],
    min_count: int = 1,
) -> dict[int, int]:
    """수준별 지속 시간(ms). 수준 = 시각마다 「켜진 단위 수」.

    단위(`unit_of`)는 칸((종목,TF)) 또는 종목이고, 한 단위는 자기 구간이 `min_count`개
    이상 열려 있을 때 켜진다 — `min_count=1`이면 "포지션이 있는 단위 수",
    단위=종목·`min_count=2`면 "자기 TF 칸을 2개 이상 동시에 든 종목 수"(종목 내 TF 간
    겹침)가 된다. 구간은 반개라 경계가 맞닿은 두 구간은 동시에 세지 않는다
    (같은 시각에서는 닫힘(−1)을 열림(+1)보다 먼저 처리한다).
    """
    if window_end <= window_start:
        raise ValueError(f"창이 비었습니다: [{window_start}, {window_end})")
    events: list[tuple[int, int, Hashable]] = []
    for row in intervals:
        lo = max(row.entry_time, window_start)
        hi = min(row.exit_time, window_end)
        if hi > lo:
            events.append((lo, +1, unit_of(row)))
            events.append((hi, -1, unit_of(row)))
    events.sort(key=lambda e: (e[0], e[1]))  # 같은 시각이면 -1(닫힘)이 먼저 = 반개구간

    durations: dict[int, int] = defaultdict(int)
    counts: dict[Hashable, int] = defaultdict(int)
    active = 0  # counts[u] >= min_count 인 단위 수
    prev = window_start
    for time, delta, unit in events:
        if time > prev:
            durations[active] += time - prev
            prev = time
        before = counts[unit]
        counts[unit] = before + delta
        if delta > 0 and before + 1 == min_count:
            active += 1
        elif delta < 0 and before == min_count:
            active -= 1
    if window_end > prev:
        durations[active] += window_end - prev
    return dict(durations)


def _cell_unit(row: IntervalRow) -> Hashable:
    return (row.symbol, row.timeframe)


def _symbol_unit(row: IntervalRow) -> Hashable:
    return row.symbol


@dataclass(frozen=True)
class Scope:
    """census 스코프 하나 — 구간 필터 + 단위 + 문턱."""

    kind: str
    key: str
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    unit_of: Callable[[IntervalRow], Hashable] = _cell_unit
    min_count: int = 1

    def select(self, intervals: Sequence[IntervalRow]) -> list[IntervalRow]:
        return [r for r in intervals if r.symbol in self.symbols and r.timeframe in self.timeframes]


def build_scopes(symbols: Sequence[str]) -> tuple[Scope, ...]:
    """이 리포트가 세는 스코프 전부(모듈 독스트링의 목록 그대로)."""
    syms = tuple(symbols)
    wan108_syms = tuple(
        s for s in syms if s in {harness.normalize_symbol(x) for x in WAN108_SYMBOLS}
    )
    scopes: list[Scope] = [
        Scope("cells", "main", syms, MAIN_TIMEFRAMES),
        Scope("cells", "all_tf", syms, ALL_TIMEFRAMES),
        *(Scope("cells_tf", tf, syms, (tf,)) for tf in ALL_TIMEFRAMES),
        Scope("symbols", "main", syms, MAIN_TIMEFRAMES, unit_of=_symbol_unit),
        Scope(
            "symbols_multi_tf",
            "main",
            syms,
            MAIN_TIMEFRAMES,
            unit_of=_symbol_unit,
            min_count=2,
        ),
        *(Scope("intra_symbol", _short(sym), (sym,), MAIN_TIMEFRAMES) for sym in syms),
    ]
    if wan108_syms:
        scopes.append(Scope("wan108", "1h_3sym", wan108_syms, ("1h",)))
        scopes.append(Scope("wan108", "multitf_3sym", wan108_syms, ALL_TIMEFRAMES))
    return tuple(scopes)


def run_census(
    intervals: Sequence[IntervalRow],
    *,
    window_start: int,
    window_end: int,
    symbols: Sequence[str],
) -> list[CensusRow]:
    """구간 집합을 모든 스코프로 재고 행으로 낸다. 스코프마다 share 합 = 1.0."""
    validate_single_position(intervals)
    span = window_end - window_start
    rows: list[CensusRow] = []
    for scope in build_scopes(symbols):
        durations = concurrency_durations(
            scope.select(intervals),
            window_start=window_start,
            window_end=window_end,
            unit_of=scope.unit_of,
            min_count=scope.min_count,
        )
        for level in sorted(durations):
            duration = durations[level]
            rows.append(
                CensusRow(
                    scope_kind=scope.kind,
                    scope_key=scope.key,
                    level=level,
                    duration_ms=duration,
                    share=duration / span,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 실행 (칸별 백테스트 → 구간 추출)
# --------------------------------------------------------------------------- #


def describe_engine() -> str:
    """이 census가 돌린 엔진의 지문 — 산출물만 봐도 어떤 엔진인지 드러나게(WAN-164 패턴)."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={ADOPTED_OB_PARAMS.combine_obs}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, short_enabled={p.short_enabled}"
    )


@dataclass(frozen=True)
class _Task:
    """fan-out 한 단위 = (심볼, TF) 칸 — 워커가 자기 데이터를 자기가 로드한다."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def run_cell(task: _Task, *, log: bool = True) -> list[IntervalRow]:
    """한 칸을 채택 기본값 그대로 독립 실행해 보유 구간을 뽑는다."""
    market = harness.load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=True,
    )
    if market.empty or market.df_1m.empty:
        return []
    ob_result = harness.detect_order_blocks(market, ADOPTED_OB_PARAMS)
    params = harness.build_params()  # 인자 없음 = 채택 기본값 그대로(옛 핀 없음)
    cfg = harness.build_config(task.timeframe)
    outcome = harness.run_once(market, params=params, cfg=cfg, order_block_result=ob_result)
    rows = [
        IntervalRow(
            symbol=task.symbol,
            timeframe=task.timeframe,
            side=str(trade.side.value),
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            window_start=task.start_ms,
            window_end=task.end_ms,
        )
        for trade in outcome.result.trades
    ]
    if log:
        open_ms = sum(
            min(r.exit_time, task.end_ms) - max(r.entry_time, task.start_ms) for r in rows
        )
        share = open_ms / (task.end_ms - task.start_ms)
        print(
            f"[wan167] {task.symbol} {task.timeframe}: trades={len(rows)} "
            f"open_share={share * 100:.2f}%",
            flush=True,
        )
    return rows


def _run_task_logged(task: _Task) -> list[IntervalRow]:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = ALL_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[IntervalRow]:
    """6심볼 × 4TF 칸을 돌아 구간을 모은다.

    `jobs`는 **성능 노브이지 결과 축이 아니다**(WAN-121) — (심볼, TF) 단위로만 갈라
    제출 순서대로 모으므로 직렬과 행·순서가 같다.
    """
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        results = [run_cell(task, log=log) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            results = list(executor.map(_run_task_logged, tasks))
    rows: list[IntervalRow] = []
    for res in results:
        rows.extend(res)
    return rows


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def intervals_to_frame(rows: Sequence[IntervalRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(IntervalRow.model_fields))


def census_to_frame(rows: Sequence[CensusRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CensusRow.model_fields))


def intervals_from_csv(path: Path) -> list[IntervalRow]:
    frame = pd.read_csv(path)
    return [IntervalRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def census_window(intervals: Sequence[IntervalRow]) -> tuple[int, int]:
    """구간 행들이 싣고 온 공통 창. 행마다 다르면 거부한다 — 분모가 갈리면 share가 거짓말."""
    windows = {(r.window_start, r.window_end) for r in intervals}
    if len(windows) != 1:
        raise ValueError(f"구간 행들의 창이 서로 다릅니다: {sorted(windows)}")
    return next(iter(windows))


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def scope_rows(rows: Sequence[CensusRow], kind: str, key: str) -> list[CensusRow]:
    return sorted(
        (r for r in rows if r.scope_kind == kind and r.scope_key == key), key=lambda r: r.level
    )


def share_at_least(rows: Sequence[CensusRow], kind: str, key: str, level: int) -> float:
    """그 스코프에서 수준이 `level` 이상인 시간 비율."""
    return sum(r.share for r in scope_rows(rows, kind, key) if r.level >= level)


def max_level(rows: Sequence[CensusRow], kind: str, key: str) -> int:
    scoped = [r for r in scope_rows(rows, kind, key) if r.duration_ms > 0]
    return max((r.level for r in scoped), default=0)


def mean_level(rows: Sequence[CensusRow], kind: str, key: str) -> float:
    """시간가중 평균 동시 열림 수(= 분포의 기대값)."""
    scoped = scope_rows(rows, kind, key)
    return sum(r.level * r.share for r in scoped)


def verdict(rows: Sequence[CensusRow]) -> str:
    """완료기준의 판정 문장 — 겹침이 레버리지 북(WAN-169)을 지을 만큼 유의미한가.

    자는 본 census(`cells/main`)의 **2칸 이상 겹침 시간 비율**이다(1칸 열림은 겹침이
    아니라 그냥 포지션이다). 숫자는 전부 행에서 계산한다 — 문장에 숫자를 박아 두면
    재실행 뒤 리포트가 거짓말을 한다(WAN-164 패턴).
    """
    ge2 = share_at_least(rows, "cells", "main", 2)
    peak = max_level(rows, "cells", "main")
    mean = mean_level(rows, "cells", "main")
    coords = (
        f"2칸 이상 겹침 = 전체 시간의 **{ge2 * 100:.2f}%** · 최대 동시 **{peak}칸** · "
        f"시간가중 평균 **{mean:.2f}칸**"
    )
    if ge2 >= SIGNIFICANT_OVERLAP_SHARE:
        return (
            f"**(a) 유의미하다 — 레버리지 북을 지을 가치가 있다.** {coords}. 겹침이 "
            f"전체 시간의 {SIGNIFICANT_OVERLAP_SHARE * 100:.0f}% 이상으로 일상이라, 칸들이 "
            "한 자본을 공유하는 회계(WAN-169) 없이는 실효 레버리지를 알 수 없다."
        )
    if ge2 < NEGLIGIBLE_OVERLAP_SHARE:
        return (
            f"**(b) 미미하다 — 북을 지어도 실익이 작다.** {coords}. 겹침이 전체 시간의 "
            f"{NEGLIGIBLE_OVERLAP_SHARE * 100:.1f}% 미만으로 WAN-108 관찰(0.13%)과 같은 "
            "자릿수다."
        )
    return (
        f"**(c) 경계다 — 크기를 알고 결정할 것.** {coords}. 겹침이 무시할 수준은 아니나 "
        f"일상({SIGNIFICANT_OVERLAP_SHARE * 100:.0f}% 이상)도 아니다. WAN-169 착수 여부는 "
        "이 크기를 알고 내리는 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _fmt_share(value: float) -> str:
    """아주 작은 비율이 0.00%로 뭉개지지 않게 — WAN-108의 0.004% 같은 값을 보는 표다."""
    if value == 0.0:
        return "0%"
    if value < 0.0001:
        return f"{value * 100:.4f}%"
    return f"{value * 100:.2f}%"


def _cell_stats_table(intervals: Sequence[IntervalRow], window: tuple[int, int]) -> list[str]:
    start, end = window
    span = end - start
    lines = [
        "| 심볼 | TF | 거래 수 | 보유 시간 비율 |",
        "| -- | -- | --: | --: |",
    ]
    by_cell: dict[tuple[str, str], list[IntervalRow]] = defaultdict(list)
    for row in intervals:
        by_cell[(row.symbol, row.timeframe)].append(row)
    for symbol in sorted({s for s, _ in by_cell}):
        for timeframe in ALL_TIMEFRAMES:
            rows = by_cell.get((symbol, timeframe))
            if not rows:
                continue
            open_ms = sum(min(r.exit_time, end) - max(r.entry_time, start) for r in rows)
            lines.append(
                f"| {_short(symbol)} | {timeframe} | {len(rows)} | {_pct(open_ms / span)} |"
            )
    return lines


def _distribution_table(rows: Sequence[CensusRow], kind: str, key: str) -> list[str]:
    scoped = scope_rows(rows, kind, key)
    lines = [
        "| 동시 열림 | 시간 비율 | 누적(이상) |",
        "| --: | --: | --: |",
    ]
    for row in scoped:
        cum = share_at_least(rows, kind, key, row.level)
        lines.append(f"| {row.level} | {_fmt_share(row.share)} | {_fmt_share(cum)} |")
    return lines


def build_summary_markdown(
    intervals: Sequence[IntervalRow],
    census: Sequence[CensusRow],
    *,
    intervals_csv: Path,
    census_csv: Path,
) -> str:
    window = census_window(intervals)
    lines = [
        "# WAN-167 — 타임프레임·종목 가로질러 동시에 열리는 포지션 census",
        "",
        "**성격** 측정 전용(레버리지 북 WAN-169의 사전 조사). 손익·사이징·자본 공유는 계산하지 "
        "않는다 — 각 (종목, TF) 칸을 채택 기본값 그대로 **독립** 실행해 보유 구간만 겹쳐 센다. "
        f"렌즈 `{OFFICIAL_LENS}` 단독(WAN-128) · 창 못 박음({DEFAULT_START}~{DEFAULT_END}) · "
        "기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "**진입 단위 = (종목, 타임프레임) 칸**(사용자 정의 2026-07-22): 칸 안에서는 청산 전 "
        "1포지션(채택 엔진이 이미 그렇다 — 어기면 이 모듈이 ValueError로 거부), BTC 15m과 "
        "BTC 1h는 별개 칸이라 동시에 열릴 수 있다. 이 표는 그 「동시에」가 실제로 얼마나 "
        "자주·많이 나는지를 센 것이다.",
        "",
        "## 이 census가 돌린 엔진",
        "",
        f"**지금 채택된 기본값 그대로** — `{describe_engine()}` + 펀딩비 반영. 옛 핀"
        "(`LEGACY_*`)은 하나도 물려받지 않았다(이슈 완료기준).",
        "",
        "⚠️ **구간은 반개(`[entry, exit)`) · 1분봉 서브스텝 해상도**다. 같은 칸의 연속 거래가 "
        "같은 1분봉에서 청산·재진입해도 겹침으로 세지 않는다.",
        "",
        f"재현: `uv run python -m backtest.wan167_position_census --jobs 6` (요약만: "
        f"`--from-csv`). 원자료: `{intervals_csv}`(보유 구간) · `{census_csv}`(수준별 분포).",
        "",
        "## 1. 칸별 기초 — 거래 수와 보유 시간 비율",
        "",
        "겹침의 원료다: 각 칸이 시간의 몇 %를 포지션으로 보내는지. (4h·1d는 대조 — 표본이 "
        "얇다, WAN-143.)",
        "",
        *_cell_stats_table(intervals, window),
        "",
        "## 2. 본 census — 6심볼 × 15m·1h = 12칸의 동시 열림 분포",
        "",
        *_distribution_table(census, "cells", "main"),
        "",
        f"최대 동시 **{max_level(census, 'cells', 'main')}칸** · 시간가중 평균 "
        f"**{mean_level(census, 'cells', 'main'):.2f}칸** · 1칸 이상 "
        f"**{_fmt_share(share_at_least(census, 'cells', 'main', 1))}** · 2칸 이상 "
        f"**{_fmt_share(share_at_least(census, 'cells', 'main', 2))}**.",
        "",
        "### 대조 — 4h·1d까지 24칸",
        "",
        *_distribution_table(census, "cells", "all_tf"),
        "",
        "## 3. 겹침의 결 — 종목 간 vs 종목 내 TF 간",
        "",
        "### 종목 간 (동시에 포지션을 든 **종목 수** · 15m·1h)",
        "",
        *_distribution_table(census, "symbols", "main"),
        "",
        "### 종목 내 TF 간 (자기 15m·1h **두 칸을 동시에** 든 종목 수)",
        "",
        *_distribution_table(census, "symbols_multi_tf", "main"),
        "",
        "### 종목별 — 자기 두 칸(15m·1h)의 동시 열림 ≥2 시간 비율",
        "",
        *_intra_symbol_lines(census),
        "",
        "## 4. TF 단면 — 한 TF 안 6심볼의 동시 열림",
        "",
        *_tf_section_lines(census),
        "",
        "## 5. WAN-108 관찰(0.13% / 0.004%)의 오늘 엔진 재현축",
        "",
        "⚠️ **숫자가 같아야 할 이유는 없다** — WAN-108은 옛 엔진(3심볼 · 병합 존 · 게이트 on · "
        "`tap` 밴드 · 0bp · 다중 포지션 시퀀서)에서 **역대 최대 겹침 수준의 시간 비율**을 봤다. "
        "여기서는 같은 3심볼 축을 오늘 엔진(독립 칸 겹침)으로 다시 잰다 — 자릿수만 비교한다.",
        "",
        *_wan108_lines(census),
        "",
        "## 판정 — 겹침이 레버리지 북(WAN-169)을 지을 만큼 유의미한가",
        "",
        verdict(census),
        "",
        f"판정 자: 본 census 2칸 이상 겹침 시간 비율 ≥ {SIGNIFICANT_OVERLAP_SHARE * 100:.0f}% "
        f"→ (a) · < {NEGLIGIBLE_OVERLAP_SHARE * 100:.1f}% → (b) · 사이 → (c). 문턱은 코드 상수"
        "(`SIGNIFICANT_OVERLAP_SHARE`·`NEGLIGIBLE_OVERLAP_SHARE`)다.",
        "",
        "⚠️ **이 표는 채택 근거가 아니라 크기 조사다** — 겹침이 유의미해도 「다중 칸을 실제로 "
        "매매하라」가 아니고(그 손익·회계는 WAN-169 소관), 「엣지 없음」(WAN-84/88/111/114/124/"
        "151)도 그대로다. 각 칸은 독립 자본으로 돌린 것이라 겹침 시점의 실효 레버리지·자본 "
        "잠식은 이 표에 없다 — 그게 없어서 북(WAN-169)을 짓는 것이다.",
        "",
        "⚠️ **기본값·토대 불변**(측정 전용 · `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


def _intra_symbol_lines(census: Sequence[CensusRow]) -> list[str]:
    lines = [
        "| 심볼 | ≥2(두 칸 동시) 시간 비율 | 최대 |",
        "| -- | --: | --: |",
    ]
    keys = sorted({r.scope_key for r in census if r.scope_kind == "intra_symbol"})
    for key in keys:
        ge2 = share_at_least(census, "intra_symbol", key, 2)
        peak = max_level(census, "intra_symbol", key)
        lines.append(f"| {key} | {_fmt_share(ge2)} | {peak} |")
    return lines


def _tf_section_lines(census: Sequence[CensusRow]) -> list[str]:
    lines = [
        "| TF | ≥1 시간 비율 | ≥2 시간 비율 | 최대 | 시간가중 평균 |",
        "| -- | --: | --: | --: | --: |",
    ]
    for tf in ALL_TIMEFRAMES:
        if not scope_rows(census, "cells_tf", tf):
            continue
        lines.append(
            f"| {tf} | {_fmt_share(share_at_least(census, 'cells_tf', tf, 1))} | "
            f"{_fmt_share(share_at_least(census, 'cells_tf', tf, 2))} | "
            f"{max_level(census, 'cells_tf', tf)} | {mean_level(census, 'cells_tf', tf):.2f} |"
        )
    return lines


def _wan108_lines(census: Sequence[CensusRow]) -> list[str]:
    lines: list[str] = []
    for key, old_note in (
        ("1h_3sym", "WAN-108의 1h 축(옛 값 0.13%)"),
        ("multitf_3sym", "WAN-108의 multi_tf 축(옛 값 0.004%)"),
    ):
        if not scope_rows(census, "wan108", key):
            continue
        peak = max_level(census, "wan108", key)
        peak_share = sum(r.share for r in scope_rows(census, "wan108", key) if r.level == peak)
        ge2 = share_at_least(census, "wan108", key, 2)
        lines.append(
            f"- **{key}** ({old_note}): 최대 동시 **{peak}칸**이 시간의 "
            f"**{_fmt_share(peak_share)}** · 2칸 이상 **{_fmt_share(ge2)}**."
        )
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-167 동시 포지션 census")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(ALL_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-intervals", type=Path, default=DEFAULT_INTERVALS_CSV)
    parser.add_argument("--out-census", type=Path, default=DEFAULT_CENSUS_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 구간 CSV에서 census·요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_intervals = Path(args.out_intervals)
    out_census = Path(args.out_census)
    out_md = Path(args.out_md)

    if args.from_csv:
        intervals = intervals_from_csv(out_intervals)
        print(f"[wan167] CSV에서 구간 {len(intervals)}행 로드 — 백테스트 재실행 없음")
    else:
        intervals = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        out_intervals.parent.mkdir(parents=True, exist_ok=True)
        intervals_to_frame(intervals).to_csv(out_intervals, index=False)
        print(f"[wan167] 구간 {len(intervals)}행 → {out_intervals}")

    window_start, window_end = census_window(intervals)
    symbols = sorted({r.symbol for r in intervals})
    census = run_census(
        intervals, window_start=window_start, window_end=window_end, symbols=symbols
    )
    out_census.parent.mkdir(parents=True, exist_ok=True)
    census_to_frame(census).to_csv(out_census, index=False)
    print(f"[wan167] census {len(census)}행 → {out_census}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            intervals, census, intervals_csv=out_intervals, census_csv=out_census
        ),
        encoding="utf-8",
    )
    print(f"[wan167] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
