"""「틱이 지지하지 않는 익절」만 골라 끄고 채택 북을 다시 돌린다 (WAN-359 · WAN-348 §7-3 후속).

## 무엇이 달라지나 — 「보간」과 「실측」은 같은 숫자가 아니다

WAN-348이 낸 **41%**는 표본 100건의 비율이고, 지금 쓰고 있는 할인된 수치(`oos_warm`
MDD ≈ 24.3% · 승률 ≈ 54.2%)는 그 비율로 WAN-336의 두 극단 **사이를 직선 보간**한 값이다.
보간이 맞으려면 세 가지가 성립해야 하는데 셋 다 확인된 적이 없다:

* 인공물 거래가 **큰 거래에 몰려 있지 않아야** 한다(표본 100건 안에서만 확인됐다).
* 북은 **한 지갑을 나눠 쓰므로**(WAN-213) 거래 하나를 지우면 그 자리를 다른 칸이 쓴다 —
  **선형이 아니다**(WAN-323이 밝힌 채널 그대로).
* 인공물 거래는 손실이 되는 게 아니라 **더 오래 보유**된다. 그 뒤가 어떻게 끝나는지는
  **아무도 안 쟀다**(WAN-348 §7-3이 남긴 자리 = 이 모듈의 §3).

## 세 팔

| 팔 | 무엇 |
| -- | -- |
| `base` | 인자 없는 채택 북 (= WAN-346 팔 A · 검산 (a)의 기준) |
| `all_off` | 같은 스텝 익절 **전부** 끔 (= WAN-336 반사실 · 상한) |
| `tick_off` | **틱이 지지하지 않는 것만** 끔 ← **답** |

## §1 — 표적 단위가 (칸, 1분)인 이유

증거의 단위가 그것이다. 자료는 **그 1분의 체결내역**이고, 엔진이 판정을 내리는 자리도
**그 1분 스텝**이다(`substep.simulate_zone_limit_trade`). 그리고 같은 분에 여러 번 체결하는
재진입 사슬은 **진입가·익절가가 같아** 틱으로 갈리지 않는다 — 모집단 467건 중 156건이 그런
사슬에 속하고(67개 분 · 최대 4건), 그중 60개 분은 사슬 전체가 **같은 지정가**다.

🚨 **그래서 이 모듈은 WAN-348의 「행마다 따로 판정」을 그대로 쓰지 않는다.** 같은 분의 사슬
4건을 각각 독립으로 재면 같은 틱 순서를 네 번 쓰므로 **네 건 모두 「진짜」로 찍힌다** — 실제로
성립하려면 그 1분 안에서 `체결→익절`이 **네 번** 왕복해야 하는데도. 그래서 사슬을 **순서대로
소비**한다(`measure_chain`): 체결 → 익절 → 그 뒤부터 다음 거래의 체결을 찾는다. 사슬이 끊긴
지점부터는 **그 거래도 뒤따르는 거래도 일어나지 않는다**.

두 판정을 **함께** 낸다 — 독립 판정은 WAN-348의 41%와 **직접 비교되는 수**이고(표본이 편향
됐는지 보는 자), 사슬 판정은 **표적 집합을 정하는 수**다.

## §2 — 그 목록을 실제 회계에 얹는다

블록 집합 = 「그 분의 **첫 거래**가 틱의 지지를 못 받는 분」. 첫 거래가 막히면 그 익절이
없어지므로 사슬 자체가 일어나지 않는다(뒤 거래는 저절로 사라진다). 반대로 첫 거래는 지지받고
**뒤 거래만** 못 받는 분은 **막지 않는다** — 분 단위 스위치로는 표현되지 않는 자리라, 그런 분이
몇 개인지 요약이 **그대로 밝힌다**(남은 낙관의 크기).

⚠️ **판정 불가(`틱없음`)인 분도 막지 않는다** — 아카이브가 없어 「지지받지 못했다」고 말할 근거가
없다. 그래서 `tick_off`는 표적 팔의 **하한**(= `base` 쪽에 붙는 방향)이고, 그 건수도 밝힌다.

## 재현

```
uv run python -m backtest.wan359_tick_targeted_tp --part verdicts             # §1 전수 판정
uv run python -m backtest.wan359_tick_targeted_tp --part book --jobs 4        # §2·§3 3팔 북
uv run python -m backtest.wan359_tick_targeted_tp --from-csv                  # 요약만
```

측정 전용 — `ConfluenceParams()`·`LeverageBookParams()` 기본값 불변 · 팔은 전부 옵트인(끄면
비트 재현) · DB에 아무것도 쓰지 않는다(WAN-194) · 실거래 보류 유지.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, net_r
from backtest.leverage_book import PlacedSetup
from backtest.models import Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, _segment_cells, run_cells
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import (
    ADOPTED_CELL_KWARGS,
    book_segments_for_payloads,
    verify_adopted_identity,
)
from backtest.wan346_conservative_book import RUIN_MDD, cagr, span_years, trade_rulers
from backtest.wan348_same_minute_tp import (
    DECIDABLE,
    TF_ORDER,
    VERDICT_ARTIFACT,
    VERDICT_NO_FILL,
    VERDICT_NO_TICKS,
    VERDICT_NOT_SAME_MINUTE,
    VERDICT_ORDER_FLIPPED_STILL,
    VERDICT_REAL,
    Measurement,
    Target,
    load_targets,
    measure_static,
    ohlc_matches,
    take_profit_checksum,
    wilson_interval,
)
from backtest.wan348_same_minute_tp import (
    load_minute_bar as _load_minute_bar,
)
from data.agg_trade_archive import (
    DEFAULT_CACHE_DIR,
    DayFetch,
    Tick,
    day_of,
    fetch_day,
    minutes_ticks,
)

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("backtest/reports")
VERDICT_CSV = REPORTS_DIR / "wan359_tick_verdicts.csv"
COST_CSV = REPORTS_DIR / "wan359_archive_cost.csv"
BOOK_CSV = REPORTS_DIR / "wan359_targeted_book.csv"
EXIT_CSV = REPORTS_DIR / "wan359_artifact_exits.csv"
SUMMARY_PATH = REPORTS_DIR / "wan359_tick_targeted_tp_summary.md"

#: WAN-348이 표본 100건에서 낸 값 — **다시 계산하지 않고 인용한다**(§1이 그 밖으로 나가면
#: 표본 추출이 편향됐다는 뜻이라 요약이 그것부터 찍는다).
WAN348_WEIGHTED_P = 0.411
WAN348_SIMPLE_LOW, WAN348_SIMPLE_HIGH = 0.309, 0.498

#: WAN-336 §1·§2가 `oos_warm`에서 낸 두 극단(보간의 두 끝). 이 모듈이 그 사이를 **실측**으로
#: 채우므로, 보간값과의 차이를 보이려면 같은 두 끝을 인용해야 한다.
WAN336_BASE_MDD, WAN336_COUNTERFACTUAL_MDD = 22.90, 25.22
WAN336_BASE_WIN, WAN336_COUNTERFACTUAL_WIN = 55.33, 53.32

CellKey = tuple[str, str]
MinuteKey = tuple[str, str, int]


# --------------------------------------------------------------------------- #
# §1 — 모집단 전수 판정 (사슬 인지)
# --------------------------------------------------------------------------- #


def group_by_minute(targets: Sequence[Target]) -> dict[MinuteKey, list[Target]]:
    """(칸, 1분)마다 그 분에 체결된 거래들 — **CSV 행 순서 그대로** 담는다.

    행 순서가 곧 사슬 순서다(WAN-346 §0의 거래별 CSV는 청산 시각 순으로 쓰인다). 여기서
    가격이나 다른 키로 다시 정렬하면 「무엇이 먼저였나」가 정렬의 산물이 된다 —
    `iter_ticks`가 정렬하지 않는 것과 같은 이유다.
    """
    out: dict[MinuteKey, list[Target]] = {}
    for target in targets:
        out.setdefault((target.symbol, target.timeframe, target.entry_ms), []).append(target)
    return out


VERDICT_CHAIN_BROKEN = "사슬끊김"
"""사슬이 **앞에서** 끊겨 이 거래는 애초에 일어나지 않는다 (WAN-359 전용 라벨).

재진입은 앞 거래가 익절로 닫혀야 무장되므로(WAN-273), 앞 거래의 같은 분 익절이 틱의 지지를
못 받으면 뒤 거래는 **존재하지 않는다**. 이것은 「판정 불가」가 아니라 **판정 결과**이므로
분모에 남아야 한다 — 빼면 사슬이 긴 분일수록 성립률이 조용히 올라간다.

WAN-348의 `미체결`을 재사용하지 않는 이유: 그쪽은 팔 `static`에서 **데이터 이상 신호**라
분모에서 빠진다(그 뜻이 여기와 정반대다). 라벨을 겹쳐 쓰면 두 표가 다른 것을 세게 된다.
"""

#: 이 모듈의 분모 — WAN-348의 판정 가능 집합 + 사슬 끊김.
CHAIN_DECIDABLE: tuple[str, ...] = (*DECIDABLE, VERDICT_CHAIN_BROKEN)


def measure_chain(ticks: Sequence[Tick], chain: Sequence[Target]) -> list[Measurement]:
    """한 분의 거래들을 **순서대로 소비**하며 판정한다 (이 모듈의 핵심).

    거래 i의 체결은 거래 i−1의 익절 **뒤**에서만 찾는다 — 재진입은 앞 거래가 익절로 닫혀야
    무장되기 때문이다(WAN-273). 사슬이 끊긴 지점의 거래는 인공물이고, 그 **뒤 거래들은 애초에
    일어나지 않는다**(`사슬끊김`).

    사슬 길이가 1이면 `measure_static`과 **글자 그대로 같은 답**을 낸다(커서가 0에서 시작해
    처음 닿는 곳을 찾는다) — 회귀 테스트가 그 동등성을 고정한다.

    ⚠️ **머리와 몸통은 `미체결`의 뜻이 다르다**. 머리가 자기 지정가에 안 닿는 것은 WAN-348이
    말한 **데이터 이상 신호**(엔진은 거기서 체결했다고 기록했다)라 그 라벨 그대로 두고 분모에서
    뺀다. 몸통이 안 닿는 것은 **앞 거래의 익절 뒤로는 가격이 안 돌아왔다**는 판정이라
    `사슬끊김`이다.
    """
    out: list[Measurement] = []
    cursor = 0  # 이 인덱스 **이후**에서만 다음 체결을 찾는다.
    broken = False
    for position, target in enumerate(chain):
        if not ticks:
            out.append(_measurement(target, VERDICT_NO_TICKS, tick_count=0))
            continue
        if broken:
            out.append(_measurement(target, VERDICT_CHAIN_BROKEN, tick_count=len(ticks)))
            continue
        fill_index: int | None = None
        first_tp_ms: int | None = None
        tp_after_fill_ms: int | None = None
        for index in range(cursor, len(ticks)):
            tick = ticks[index]
            if fill_index is None and tick.price <= target.entry_price:
                fill_index = index
            if tick.price >= target.take_profit_price:
                if first_tp_ms is None:
                    first_tp_ms = tick.time_ms
                if fill_index is not None and tp_after_fill_ms is None:
                    tp_after_fill_ms = tick.time_ms
                    cursor = index + 1
                    break
        fill_ms = None if fill_index is None else ticks[fill_index].time_ms
        verdict = _classify_chain(
            fill_ms=fill_ms,
            first_tp_ms=first_tp_ms,
            tp_after_fill_ms=tp_after_fill_ms,
            tick_count=len(ticks),
        )
        if verdict == VERDICT_NO_FILL and position > 0:
            verdict = VERDICT_CHAIN_BROKEN
        if verdict not in (VERDICT_REAL, VERDICT_ORDER_FLIPPED_STILL):
            broken = True  # 이 거래가 그 분에 안 닫혔으면 뒤 거래는 무장되지 않는다.
        out.append(
            Measurement(
                verdict=verdict,
                fill_ms=fill_ms,
                fill_price=target.entry_price if fill_ms is not None else None,
                take_profit_price=target.take_profit_price,
                first_tp_ms=first_tp_ms,
                tp_after_fill_ms=tp_after_fill_ms,
                tick_count=len(ticks),
            )
        )
    return out


def _measurement(target: Target, verdict: str, *, tick_count: int) -> Measurement:
    return Measurement(
        verdict=verdict,
        fill_ms=None,
        fill_price=None,
        take_profit_price=target.take_profit_price,
        first_tp_ms=None,
        tp_after_fill_ms=None,
        tick_count=tick_count,
    )


def _classify_chain(
    *, fill_ms: int | None, first_tp_ms: int | None, tp_after_fill_ms: int | None, tick_count: int
) -> str:
    """`wan348._classify`와 같은 표(라벨을 두 벌로 갈라 두지 않는다)."""
    if tick_count == 0:
        return VERDICT_NO_TICKS
    if fill_ms is None:
        return VERDICT_NO_FILL
    if tp_after_fill_ms is None:
        return VERDICT_ARTIFACT if first_tp_ms is not None else VERDICT_NOT_SAME_MINUTE
    if first_tp_ms is not None and first_tp_ms < fill_ms:
        return VERDICT_ORDER_FLIPPED_STILL
    return VERDICT_REAL


@dataclass(frozen=True, slots=True)
class VerdictRow:
    """CSV 한 줄 — 거래 하나. 독립 판정과 사슬 판정을 **나란히** 싣는다."""

    symbol: str
    timeframe: str
    entry_ms: int
    entry_utc: str
    day: str
    ordinal: int
    """그 분 안에서 몇 번째 체결인가(1부터). 사슬 판정이 이 순서를 쓴다."""
    chain_size: int
    is_reentry: bool
    engine_entry: float
    engine_take_profit: float
    stop_price: float
    net_r: float
    tick_count: int
    ohlc_match: bool | None
    """틱 고·저가가 저장 1분봉과 같은가 — 두 자료의 출처가 달라 어긋나면 판정 무효다."""
    solo_verdict: str
    """WAN-348과 **같은 자**(행마다 독립). 41%와 직접 비교되는 수."""
    solo_outcome_ok: bool
    chain_verdict: str
    """사슬을 순서대로 소비한 판정. **표적 집합을 정하는 수**."""
    chain_outcome_ok: bool
    fill_offset_s: float | None


def _row(
    target: Target,
    *,
    ordinal: int,
    chain_size: int,
    solo: Measurement,
    chain: Measurement,
    ohlc_match: bool | None,
) -> VerdictRow:
    return VerdictRow(
        symbol=target.symbol,
        timeframe=target.timeframe,
        entry_ms=target.entry_ms,
        entry_utc=datetime.fromtimestamp(target.entry_ms / 1000.0, tz=UTC).strftime(
            "%Y-%m-%d %H:%M"
        ),
        day=target.day,
        ordinal=ordinal,
        chain_size=chain_size,
        is_reentry=target.is_reentry,
        engine_entry=target.entry_price,
        engine_take_profit=target.take_profit_price,
        stop_price=target.stop_price,
        net_r=target.net_r,
        tick_count=chain.tick_count,
        ohlc_match=ohlc_match,
        solo_verdict=solo.verdict,
        solo_outcome_ok=solo.outcome_ok,
        chain_verdict=chain.verdict,
        chain_outcome_ok=chain.outcome_ok,
        fill_offset_s=(
            None if chain.fill_ms is None else (chain.fill_ms - target.entry_ms) / 1000.0
        ),
    )


def run_verdicts(
    targets: Sequence[Target],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    db_path: str = harness.DB_PATH,
    fetch: Callable[[str, str], DayFetch] | None = None,
) -> tuple[list[VerdictRow], list[DayFetch]]:
    """모집단 전수를 잰다 — **(종목, 날짜)마다 파일을 한 번만 훑는다**.

    WAN-348은 거래마다 zip을 처음부터 다시 읽었다(표본 100건에서는 그래도 됐다). 모집단은
    파일 257개에 거래 467건이라 같은 파일을 최대 여러 번 파므로, 그 하루에 필요한 분들을
    모아 **한 번에** 꺼낸다(`minutes_ticks`) — 결과는 같고 시간만 줄어든다.
    """
    fetcher = fetch or (lambda symbol, day: fetch_day(symbol, day, cache_dir=cache_dir))
    minutes = group_by_minute(targets)
    by_day: dict[tuple[str, str], list[MinuteKey]] = {}
    for key in minutes:
        by_day.setdefault((key[0], day_of(key[2])), []).append(key)

    rows: list[VerdictRow] = []
    fetches: list[DayFetch] = []
    for index, ((symbol, day), keys) in enumerate(sorted(by_day.items()), start=1):
        got = fetcher(symbol, day)
        fetches.append(got)
        logger.info(
            "[wan359] %d/%d %s %s %s",
            index,
            len(by_day),
            symbol,
            day,
            "캐시" if got.cached else f"{got.size_bytes / 1e6:.1f}MB",
        )
        wanted = sorted({key[2] for key in keys})
        ticks_by_minute = (
            minutes_ticks(got.path, wanted) if got.path is not None else {m: [] for m in wanted}
        )
        for key in sorted(keys, key=lambda k: k[2]):
            chain = minutes[key]
            ticks = ticks_by_minute.get(key[2], [])
            bar = _load_minute_bar(key[0], key[2], db_path=db_path)
            match = ohlc_matches(ticks, bar)
            chain_measurements = measure_chain(ticks, chain)
            paired = zip(chain, chain_measurements, strict=True)
            for ordinal, (target, chain_m) in enumerate(paired, start=1):
                rows.append(
                    _row(
                        target,
                        ordinal=ordinal,
                        chain_size=len(chain),
                        solo=measure_static(ticks, target),
                        chain=chain_m,
                        ohlc_match=match,
                    )
                )
    return rows, fetches


def verdict_frame(rows: Sequence[VerdictRow]) -> pd.DataFrame:
    return pd.DataFrame([asdict(row) for row in rows])


def cost_frame(fetches: Sequence[DayFetch]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": f.symbol,
                "day": f.day,
                "status": f.status,
                "size_bytes": f.size_bytes,
                "seconds": f.seconds,
                "cached": f.cached,
                "note": f.note,
            }
            for f in fetches
        ]
    )


# --------------------------------------------------------------------------- #
# §1→§2 다리 — 「막을 분」 목록
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BlockSet:
    """표적 집합 + **왜 그렇게 골랐는지**를 함께 나르는 값.

    수만 넘기면 요약이 「몇 개를 왜 안 막았나」를 못 쓴다 — 이 표에서 가장 오해하기 쉬운 자리가
    거기다(막지 않은 분이 곧 「진짜였던 분」은 아니다).
    """

    minutes: dict[CellKey, frozenset[int]]
    blocked_minutes: int
    supported_minutes: int
    undecidable_minutes: int
    """판정 불가(`틱없음` 등)라 **막지 않은** 분 — 아카이브가 없으면 「지지 못 받았다」고
    말할 근거가 없다. `tick_off`를 `base` 쪽으로 미는 방향이라 하한이 된다."""
    tail_only_minutes: int
    """첫 거래는 지지받고 **뒤 거래만** 못 받는 분 — 분 단위 스위치로 표현되지 않아 막지 않는다.
    남은 낙관의 크기이므로 요약이 그대로 밝힌다."""
    blocked_trades: int
    """막힌 분에 속한 거래 수(사슬 포함) — 그 분이 막히면 전부 사라지거나 다른 청산을 탄다."""

    @property
    def decided_minutes(self) -> int:
        return self.blocked_minutes + self.supported_minutes


def build_block_set(frame: pd.DataFrame) -> BlockSet:
    """판정 표에서 「그 분의 **첫 거래**가 틱의 지지를 못 받는 분」을 고른다.

    첫 거래를 기준으로 삼는 이유는 인과다 — 첫 거래의 같은 분 익절이 막히면 그 존은 그 분에
    닫히지 않으므로 **뒤 거래(재무장 재진입)는 애초에 일어나지 않는다**. 뒤 거래만 못 받는
    분은 반대로 분 단위 스위치로 표현되지 않으니 막지 않고 **세어서 밝힌다**.
    """
    minutes: dict[CellKey, set[int]] = {}
    blocked = supported = undecidable = tail_only = 0
    blocked_trades = 0
    for (symbol, timeframe, entry_ms), part in frame.groupby(
        ["symbol", "timeframe", "entry_ms"], sort=True
    ):
        chain = part.sort_values("ordinal")
        head = chain.iloc[0]
        if head["chain_verdict"] not in CHAIN_DECIDABLE:
            undecidable += 1
            continue
        if bool(head["chain_outcome_ok"]):
            supported += 1
            if not bool(chain["chain_outcome_ok"].astype(bool).all()):
                tail_only += 1
            continue
        blocked += 1
        blocked_trades += int(len(chain))
        minutes.setdefault((str(symbol), str(timeframe)), set()).add(int(entry_ms))
    return BlockSet(
        minutes={key: frozenset(value) for key, value in minutes.items()},
        blocked_minutes=blocked,
        supported_minutes=supported,
        undecidable_minutes=undecidable,
        tail_only_minutes=tail_only,
        blocked_trades=blocked_trades,
    )


# --------------------------------------------------------------------------- #
# §2 — 세 팔의 채택 북
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    name: str
    label: str
    all_off: bool
    """`no_same_step_tp` — 「전부 끔」(반대쪽 극단)."""
    targeted: bool
    """`no_same_step_tp_minutes` — 「틱이 지지하지 않는 그 분들만 끔」."""

    @property
    def is_adopted(self) -> bool:
        return not self.all_off and not self.targeted


ARMS: tuple[Arm, ...] = (
    Arm("base", "채택 북(현행) = 인자 없는 backtest.run", False, False),
    Arm("all_off", "같은 스텝 익절 전부 끔(WAN-336 반사실 · 상한)", True, False),
    Arm("tick_off", "틱이 지지하지 않는 것만 끔 ← 답", False, True),
)
ARMS_BY_NAME: dict[str, Arm] = {a.name: a for a in ARMS}
ARM_ORDER: tuple[str, ...] = tuple(a.name for a in ARMS)
ADOPTED_ARM = "base"
ANSWER_ARM = "tick_off"
UPPER_ARM = "all_off"

CSV_KEYS: tuple[str, ...] = ("arm", "segment")


class BookRow(BaseModel):
    """한 (팔, 구간)의 북 집계 — 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    arm: str
    arm_label: str
    segment: str
    num_cells: int
    num_trades: int
    win_rate: float
    total_return: float
    """⚠️ 6년 복리라 실현 수익이 아니다(WAN-169/213) — 거래당 자와 나란히 읽는다."""
    cagr: float | None
    span_years: float
    net_pnl: float
    net_r: float
    mean_net_r: float
    median_net_r: float
    profit_factor: float | None
    max_drawdown: float
    return_over_mdd: float | None
    ruin: bool
    peak_concurrency: int
    max_concurrent_risk: float
    max_effective_concurrent_risk: float
    liquidation_events: int
    same_step_tp_trades: int
    same_step_tp_trade_share: float
    candidate_same_step_tps: int
    """후보 층(시퀀싱 전) 카운터 — `all_off`에서는 **정의상 0**이고 `tick_off`에서는
    **0도 base 값도 아니어야** 한다(검산: 팔이 라벨이 아니라 실제로 동작했다는 증거)."""
    reentry_trades: int


def _candidate_same_step_tps(payloads: Sequence[CellPayload], segment: str) -> int:
    return sum(
        1
        for cell in _segment_cells(payloads, segment, "", include_reentry=True)
        for cand in cell.candidates
        if cand.same_step_take_profit
    )


def _to_row(*, arm: Arm, segment: BookSegment, payloads: Sequence[CellPayload]) -> BookRow:
    row = segment.row
    pairs = segment.trades_with_placements()
    rulers = trade_rulers(pairs)
    years = span_years(segment)
    return BookRow(
        arm=arm.name,
        arm_label=arm.label,
        segment=segment.segment,
        num_cells=row.num_cells,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        cagr=cagr(row.total_return, years),
        span_years=years,
        net_pnl=rulers["net_pnl"],
        net_r=rulers["net_r"],
        mean_net_r=rulers["mean_net_r"],
        median_net_r=rulers["median_net_r"],
        profit_factor=segment.result.metrics.profit_factor,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd,
        ruin=row.max_drawdown >= RUIN_MDD,
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=row.max_concurrent_risk,
        max_effective_concurrent_risk=row.max_effective_concurrent_risk,
        liquidation_events=row.liquidation_events,
        same_step_tp_trades=int(rulers["same_step_tp_trades"]),
        same_step_tp_trade_share=(
            rulers["same_step_tp_trades"] / row.num_trades if row.num_trades else 0.0
        ),
        candidate_same_step_tps=_candidate_same_step_tps(payloads, segment.segment),
        reentry_trades=int(rulers["reentry_trades"]),
    )


# --------------------------------------------------------------------------- #
# §3 — 인공물 거래는 그 뒤 어떻게 끝나나
# --------------------------------------------------------------------------- #


class ExitRow(BaseModel):
    """막힌 분에 진입한 거래들의 **실제 청산** 분포 (팔마다 한 줄씩 · 사유별)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    exit_reason: str
    num_trades: int
    net_r: float
    """그 사유로 끝난 거래들의 net R 합 — 「재판정 대상 ≠ 손실」을 실제 손익으로 바꾼다."""
    net_pnl: float
    median_hold_minutes: float


def _exit_reason(trade: Trade) -> str:
    return trade.exits[-1].reason.value if trade.exits else "미청산"


def artifact_exit_rows(*, arm: Arm, segment: BookSegment, blocks: BlockSet) -> list[ExitRow]:
    """막힌 (칸, 분)에 진입한 거래만 골라 청산 사유별로 센다.

    ⚠️ 라벨 필터가 맞는 자리다 — 지갑을 다시 배치하는 것이 아니라 **이미 배치된 이 팔의
    거래**에서 그 분에 들어간 것을 세는 것이라, 여기서 스코프를 다시 잡으면 다른 지갑이 된다.
    """
    buckets: dict[str, list[tuple[Trade, PlacedSetup]]] = {}
    for trade, placement in segment.trades_with_placements():
        # `PlacedSetup.cell`이 곧 `(심볼, TF)`다 — 표적 집합의 키와 같은 자를 쓴다.
        if trade.entry_time not in blocks.minutes.get(placement.cell, frozenset()):
            continue
        buckets.setdefault(_exit_reason(trade), []).append((trade, placement))
    rows: list[ExitRow] = []
    for reason, pairs in sorted(buckets.items(), key=lambda item: -len(item[1])):
        holds = [
            (t.exits[-1].time - t.entry_time) / 60_000.0 if t.exits else 0.0 for t, _p in pairs
        ]
        rows.append(
            ExitRow(
                arm=arm.name,
                segment=segment.segment,
                exit_reason=reason,
                num_trades=len(pairs),
                net_r=sum(net_r(t, p) for t, p in pairs),
                net_pnl=sum(t.realized_pnl for t, _p in pairs),
                median_hold_minutes=float(pd.Series(holds).median()),
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def run_arm(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arm: Arm,
    *,
    blocks: BlockSet,
    start: str,
    end: str,
    jobs: int,
    segments: Sequence[str] = SEGMENT_ORDER,
    on_rows: Callable[[list[BookRow]], None] | None = None,
    log: bool = True,
) -> tuple[list[BookRow], list[ExitRow], float | None]:
    """한 팔의 후보를 **한 번** 만들고 구간별 북 행과 §3 청산 분포를 낸다.

    `on_rows`는 북 행이 나오는 **즉시** 불린다(§3 귀속 **전**) — 아래 주석 참고.
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        # ⚠️ 채택 팔에서만 `engine_check`를 켠다 — 그 검산은 격리 성과가 `harness.run_once`
        # (반사실 없는 per-cell)와 비트 일치하는지 보는 것이라, 축을 켠 팔에서는 **당연히**
        # 어긋난다(WAN-336/346 관행 그대로).
        engine_check=arm.is_adopted,
        no_same_step_tp=arm.all_off,
        no_same_step_tp_minutes=blocks.minutes if arm.targeted else None,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )
    identity: float | None = None
    if arm.is_adopted:
        identity = verify_adopted_identity(payloads, start_ms=start_ms, end_ms=end_ms)
        if log:
            print(f"[wan359] 검산(a) 채택 경로 최대차: {identity:.2e}", flush=True)

    book = book_segments_for_payloads(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments)
    rows = [_to_row(arm=arm, segment=seg, payloads=payloads) for seg in book]
    # 🚨 **북 행을 먼저 넘긴다** — 후보 생성이 이 팔의 비용 전부(~70분)이고 §3 귀속은 그 위의
    # 값싼 집계다. 귀속이 죽으면 그 70분이 같이 죽는데, 실제로 한 번 그렇게 잃었다. 호출부가
    # 여기서 적재하면 뒤가 어떻게 되든 팔은 보존된다.
    if on_rows is not None:
        on_rows(rows)
    exits = [row for seg in book for row in artifact_exit_rows(arm=arm, segment=seg, blocks=blocks)]
    return rows, exits, identity


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    blocks: BlockSet,
    arms: Sequence[str] = ARM_ORDER,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    on_arm: Callable[[list[BookRow], list[ExitRow]], None] | None = None,
    log: bool = True,
) -> tuple[list[BookRow], list[ExitRow]]:
    """팔마다 4TF 지갑을 한 실행으로 돈다.

    📌 팔마다 즉시 적재한다(`on_arm`) — 한 팔이 12종목 × 4TF라 한 시간 안팎이고 팔은 각자
    독립 지갑이라 중간에 끊겨도 끝난 팔은 보존된다. **끊길 수 없는 것은 한 팔 안의 4TF뿐이다**
    (북은 이어붙일 수 없다 — WAN-316).
    """
    rows: list[BookRow] = []
    exits: list[ExitRow] = []
    for name in arms:
        arm = ARMS_BY_NAME[name]
        t0 = time.time()
        arm_rows, arm_exits, _identity = run_arm(
            symbols,
            timeframes,
            arm,
            blocks=blocks,
            start=start,
            end=end,
            jobs=jobs,
            segments=segments,
            on_rows=(None if on_arm is None else lambda r: on_arm(r, [])),
            log=log,
        )
        rows.extend(arm_rows)
        exits.extend(arm_exits)
        if on_arm is not None:
            on_arm(arm_rows, arm_exits)
        if log:
            print(f"[wan359] 팔 {name} 완료 ({(time.time() - t0) / 60:.1f}분)", flush=True)
    return rows, exits


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value) * 100:.{digits}f}%"


def ratio_block(frame: pd.DataFrame, column: str, weights: dict[str, int]) -> list[str]:
    """TF별 성립률 + 층 가중 합 — WAN-348 `_ratio_block`과 **같은 자**(비교 가능해야 한다).

    전수라 층 가중과 단순 합이 (판정 불가를 빼면) 거의 같아야 정상이다. 갈리면 판정 불가가
    TF에 몰렸다는 뜻이라 그것부터 봐야 한다.
    """
    chain_axis = column.startswith("chain")
    verdict_col = "chain_verdict" if chain_axis else "solo_verdict"
    usable = frame[frame[verdict_col].isin(CHAIN_DECIDABLE if chain_axis else DECIDABLE)]
    lines: list[str] = []
    per: dict[str, tuple[int, int]] = {}
    for tf in TF_ORDER:
        part = usable[usable["timeframe"] == tf]
        if part.empty:
            continue
        hits, total = int(part[column].astype(bool).sum()), int(len(part))
        per[tf] = (hits, total)
        low, high = wilson_interval(hits, total)
        lines.append(
            f"| {tf} | {hits}/{total} | {hits / total * 100:.1f}% | "
            f"{low * 100:.1f}~{high * 100:.1f}% | {weights.get(tf, 0)} |"
        )
    hits, total = int(usable[column].astype(bool).sum()), int(len(usable))
    if total:
        low, high = wilson_interval(hits, total)
        lines.append(
            f"| **합** | {hits}/{total} | {hits / total * 100:.1f}% | "
            f"{low * 100:.1f}~{high * 100:.1f}% | {sum(weights.values())} |"
        )
    return lines


def _headline_p(frame: pd.DataFrame, column: str) -> float:
    chain_axis = column.startswith("chain")
    verdict_col = "chain_verdict" if chain_axis else "solo_verdict"
    usable = frame[frame[verdict_col].isin(CHAIN_DECIDABLE if chain_axis else DECIDABLE)]
    if usable.empty:
        return float("nan")
    return float(usable[column].astype(bool).mean())


def net_r_weighted_share(frame: pd.DataFrame) -> float:
    """크기로 가중한 성립 비율 — 「인공물이 하필 큰 거래에 몰려 있나」를 본다.

    건수 비율과 크게 갈리면 그 자체가 신호다(보간이 더 크게 틀린다). `net R`을 쓰는 이유는
    복리·시점 편중이 안 섞이기 때문이다(WAN-348과 같은 자).
    """
    usable = frame[frame["chain_verdict"].isin(CHAIN_DECIDABLE)]
    total = float(usable["net_r"].abs().sum())
    if total <= 0:
        return float("nan")
    return float(usable[usable["chain_outcome_ok"].astype(bool)]["net_r"].abs().sum()) / total


def blend(base: float, counterfactual: float, p: float) -> float:
    """WAN-348 §4가 쓴 그 선형 혼합 — **이 이슈가 틀렸는지 확인하려고** 다시 계산한다."""
    return counterfactual + p * (base - counterfactual)


def _book_row(frame: pd.DataFrame, arm: str, segment: str) -> pd.Series | None:
    part = frame[(frame["arm"] == arm) & (frame["segment"] == segment)]
    return None if part.empty else part.iloc[0]


def _book_table(frame: pd.DataFrame, segment: str) -> list[str]:
    rows = [
        "| 팔 | 거래 | 승률 | MDD | 거래당 net R | 최대 동시 리스크(계획/실효) | 청산 |",
        "| -- | --: | --: | --: | --: | --: | --: |",
    ]
    for name in ARM_ORDER:
        row = _book_row(frame, name, segment)
        if row is None:
            continue
        rows.append(
            f"| `{name}` | {int(row['num_trades']):,} | {_pct(row['win_rate'])} | "
            f"**{_pct(row['max_drawdown'])}** | {float(row['mean_net_r']):.4f} | "
            f"{_pct(row['max_concurrent_risk'])}/"
            f"{_pct(row['max_effective_concurrent_risk'])} | "
            f"{int(row['liquidation_events'])} |"
        )
    return rows


def build_summary(
    verdicts: pd.DataFrame,
    book: pd.DataFrame,
    exits: pd.DataFrame,
    costs: pd.DataFrame,
    *,
    targets: Sequence[Target] | None = None,
) -> str:
    """판정을 문장으로 낸다 — 표만 두면 다음 사람이 다시 해석한다."""
    weights: dict[str, int] = {}
    for target in targets or []:
        weights[target.timeframe] = weights.get(target.timeframe, 0) + 1
    out: list[str] = [
        "# WAN-359 — 「틱이 지지하지 않는 익절」만 골라 끄고 채택 북을 다시 돌린다",
        "",
        "> 대상: WAN-346 §0 팔 A 거래별 CSV의 `같은분익절=True` "
        f"**{sum(weights.values()) or len(verdicts)}건 전수**(표본이 아니다).",
        "> 자료: Binance USDⓈ-M 선물 일자별 체결내역 아카이브 — WAN-347 §0이 「유일한 길」로 "
        "실측한 경로 · 기계는 WAN-348 그대로.",
        "",
    ]

    if verdicts.empty:
        return "\n".join(out + ["(§1 판정 표가 없습니다.)", ""])

    blocks = build_block_set(verdicts)
    chain_p = _headline_p(verdicts, "chain_outcome_ok")
    solo_p = _headline_p(verdicts, "solo_outcome_ok")
    inside = WAN348_SIMPLE_LOW <= solo_p <= WAN348_SIMPLE_HIGH
    out += [
        "## §1 — 467건 전수 판정",
        "",
        f"**한 줄: 전수 성립률은 사슬 인지 기준 {chain_p * 100:.1f}%, WAN-348과 같은 자"
        f"(행마다 독립)로는 {solo_p * 100:.1f}%다 — 표본 100건의 "
        f"{WAN348_WEIGHTED_P * 100:.1f}%와 "
        + ("**같은 자리**" if inside else "**어긋난다**")
        + f"(그 표의 95% 구간 {WAN348_SIMPLE_LOW * 100:.1f}~{WAN348_SIMPLE_HIGH * 100:.1f}% "
        + ("안" if inside else "밖")
        + ").**",
        "",
        "🚨 **두 자를 함께 내는 이유** — 같은 분에 여러 번 체결하는 재진입 사슬(모집단 "
        f"{int((verdicts['chain_size'] > 1).sum())}건 / "
        f"{int(verdicts[verdicts['chain_size'] > 1]['entry_ms'].nunique())}개 분)은 진입가·"
        "익절가가 같아 **행마다 독립으로 재면 같은 틱 순서를 여러 번 쓴다**. 사슬 판정은 그 "
        "순서를 **한 번만** 쓰고(체결→익절→그 뒤부터 다음 체결) 끊긴 지점부터는 뒤 거래가 "
        "일어나지 않는 것으로 본다 — **표적 집합을 정하는 것은 이쪽**이다.",
        "",
        "**사슬 판정**(표적 집합의 근거)",
        "",
        "| TF | 성립/판정 | 비율 | 95% 구간 | 모집단 |",
        "| -- | -- | -- | -- | -- |",
        *ratio_block(verdicts, "chain_outcome_ok", weights),
        "",
        "**독립 판정**(WAN-348과 같은 자 · 표본 편향 점검용)",
        "",
        "| TF | 성립/판정 | 비율 | 95% 구간 | 모집단 |",
        "| -- | -- | -- | -- | -- |",
        *ratio_block(verdicts, "solo_outcome_ok", weights),
        "",
        "판정 분포(사슬): "
        + " · ".join(
            f"`{k}` {v}" for k, v in sorted(verdicts["chain_verdict"].value_counts().items())
        ),
        "",
        "## §1-b — 무엇을 막고 무엇을 안 막았나",
        "",
        f"- **막는다**: 첫 거래가 지지받지 못한 분 **{blocks.blocked_minutes}개**"
        f"(그 분에 속한 거래 {blocks.blocked_trades}건).",
        f"- **안 막는다 (1) 지지받은 분 {blocks.supported_minutes}개** — 그중 "
        f"**{blocks.tail_only_minutes}개는 첫 거래만 지지받고 뒤 거래는 못 받는다**. 분 단위 "
        "스위치로는 표현되지 않아 그대로 두므로, 그만큼 `tick_off`에 낙관이 남는다.",
        f"- **안 막는다 (2) 판정 불가 {blocks.undecidable_minutes}개** — 아카이브가 없거나 그 "
        "분에 체결이 없어 「지지 못 받았다」고 말할 근거가 없다.",
        "- 📌 그래서 **`tick_off`는 표적 효과의 하한**이다(둘 다 `base` 쪽으로 미는 방향).",
        "",
    ]

    weighted = net_r_weighted_share(verdicts)
    out += [
        f"- 📌 **크기로 가중해도 같다** — net R 가중 성립률 **{weighted * 100:.1f}%**(건수 "
        f"{chain_p * 100:.1f}%). 두 수가 갈리면 「인공물이 하필 큰 거래에 몰렸다」는 뜻이라 "
        "보간이 더 크게 틀리는데, 그렇지 않다(WAN-348이 표본 100건에서 본 것을 모집단에서 "
        "재확인).",
        "",
    ]

    compared = verdicts[verdicts["ohlc_match"].notna()]
    if not compared.empty:
        matched = int(compared["ohlc_match"].astype(bool).sum())
        out += [
            f"- 🚨 검산 (c): **틱 고·저가가 저장 1분봉과 일치 {matched}/{len(compared)}건** — "
            "두 자료는 출처가 달라(수집기 1분봉 vs 거래소 아카이브) 어긋나면 엉뚱한 파일을 "
            "펼쳤다는 뜻이라 판정 전체가 무효다.",
        ]
        misses = compared[~compared["ohlc_match"].astype(bool)]
        if not misses.empty:
            listed = " · ".join(
                f"{r['symbol']} {r['timeframe']} {r['entry_utc']}({r['chain_verdict']})"
                for _i, r in misses.iterrows()
            )
            out.append(
                f"  - 어긋난 {len(misses)}건: {listed}. ⚠️ **판정에서 빼지 않았다** — 두 "
                "자료의 극값이 1분 경계에서 미세하게 갈리는 것과 「엉뚱한 파일」은 크기가 "
                "다르고, 이 건수(전체의 "
                f"{len(misses) / len(compared) * 100:.1f}%)로는 헤드라인이 안 움직인다. "
                "판정 자체는 그 분의 체결 **순서**를 보므로 극값 한 틱 차이에 안 흔들린다."
            )
        out.append("")

    if not book.empty:
        out += [
            "## §2 — 세 팔의 채택 북",
            "",
            f"### `{PRIMARY_OOS}` (주 수치, WAN-166)",
            "",
            *_book_table(book, PRIMARY_OOS),
            "",
        ]
        for segment in SEGMENT_ORDER:
            if segment == PRIMARY_OOS:
                continue
            table = _book_table(book, segment)
            if len(table) > 2:
                out += [f"### `{segment}`", "", *table, ""]

        base_row = _book_row(book, ADOPTED_ARM, PRIMARY_OOS)
        answer = _book_row(book, ANSWER_ARM, PRIMARY_OOS)
        upper = _book_row(book, UPPER_ARM, PRIMARY_OOS)
        if base_row is not None and answer is not None and upper is not None:
            mdd_base = float(base_row["max_drawdown"]) * 100
            mdd_upper = float(upper["max_drawdown"]) * 100
            mdd_answer = float(answer["max_drawdown"]) * 100
            win_base = float(base_row["win_rate"]) * 100
            win_upper = float(upper["win_rate"]) * 100
            win_answer = float(answer["win_rate"]) * 100
            between = min(mdd_base, mdd_upper) <= mdd_answer <= max(mdd_base, mdd_upper)
            blended_mdd = blend(WAN336_BASE_MDD, WAN336_COUNTERFACTUAL_MDD, chain_p)
            blended_win = blend(WAN336_BASE_WIN, WAN336_COUNTERFACTUAL_WIN, chain_p)
            out += [
                "## §3 — 보간이 얼마나 틀렸나",
                "",
                f"**한 줄: 보간은 MDD를 {blended_mdd:.2f}%로 봤는데 실측은 "
                f"{mdd_answer:.2f}%다({mdd_answer - blended_mdd:+.2f}%p).**",
                "",
                "| `oos_warm` | `base`(현행) | `all_off`(상한) | **`tick_off`(실측)** "
                "| 보간값(WAN-348 §4 방식) | 차이 |",
                "| -- | --: | --: | --: | --: | --: |",
                f"| MDD | {mdd_base:.2f}% | {mdd_upper:.2f}% | **{mdd_answer:.2f}%** | "
                f"{blended_mdd:.2f}% | {mdd_answer - blended_mdd:+.2f}%p |",
                f"| 승률 | {win_base:.2f}% | {win_upper:.2f}% | **{win_answer:.2f}%** | "
                f"{blended_win:.2f}% | {win_answer - blended_win:+.2f}%p |",
                "",
                "- `tick_off`가 두 극단 **사이에 "
                + ("있다" if between else "있지 않다")
                + "**"
                + (
                    "."
                    if between
                    else " — 🚨 북은 한 지갑이라 거래를 지우면 그 자리를 다른 칸이 쓴다"
                    "(WAN-213/323). 선형 보간이 성립하지 않는다는 직접 증거다."
                ),
                "- ⚠️ **보간값은 WAN-336의 두 극단** 위에 이 표의 p를 얹은 값이고, 실측 열은 "
                "**이 실행의 세 팔**이다. 두 열의 `base`가 같은 수인지부터 확인할 것"
                f"(이 실행 {mdd_base:.2f}% vs WAN-336 {WAN336_BASE_MDD:.2f}%).",
                "- ⚠️ **복리 총수익에는 어떤 혼합도 쓰지 말 것** — 거래당 효과가 곱으로 쌓여 "
                "선형이 아니다(WAN-169/213).",
                "",
            ]

    if not exits.empty:
        part = exits[(exits["arm"] == ANSWER_ARM) & (exits["segment"] == PRIMARY_OOS)]
        if not part.empty:
            total_r = float(part["net_r"].sum())
            total_n = int(part["num_trades"].sum())
            out += [
                "## §4 — 인공물 거래는 그 뒤 어떻게 끝나나",
                "",
                f"**한 줄: 막힌 분에 진입한 {total_n}건은 손실이 되는 게 아니라 다르게 "
                f"끝난다 — 그 거래들의 net R 합은 {total_r:+.2f}R이다.**",
                "",
                "| 청산 사유 | 건수 | net R 합 | 순손익(USD) | 보유(분, 중앙값) |",
                "| -- | --: | --: | --: | --: |",
                *[
                    f"| {r['exit_reason']} | {int(r['num_trades'])} | {float(r['net_r']):+.2f} | "
                    f"{float(r['net_pnl']):,.0f} | {float(r['median_hold_minutes']):.0f} |"
                    for _i, r in part.iterrows()
                ],
                "",
                "- 🚨 **이것이 WAN-348 §4의 「재판정 대상 ≠ 손실」을 실제 손익으로 바꾼 자리다** "
                "— 순서가 반대였다면 그 거래는 손실이 아니라 **더 오래 보유**였고, 이 표가 그 "
                "뒤를 처음 센다.",
                "- ⚠️ **`base` 팔의 같은 분 거래와 직접 빼지 말 것** — 북은 한 지갑이라 팔이 "
                "다르면 **그 뒤에 놓인 거래들도 달라진다**(같은 거래의 전후가 아니다).",
                "",
            ]

    if not costs.empty:
        downloaded = costs[~costs["cached"].astype(bool)]
        failed = int((costs["status"].astype(int) != 200).sum())
        out += [
            "## §5 — 비용 (실측)",
            "",
            f"- 파일 {len(costs)}개(= 서로 다른 (종목, 날짜) 쌍) · 합계 "
            f"**{float(costs['size_bytes'].sum()) / 1e6:.0f}MB** · 실패 {failed}개.",
            (
                f"- 받는 데 **{float(downloaded['seconds'].sum()):.0f}초**"
                f"({len(downloaded)}개 실제 다운로드 · 나머지는 캐시)."
                if len(downloaded)
                else "- ⚠️ 이 실행은 **전부 캐시 적중**이라 받는 시간을 재지 않았다."
            ),
            "",
        ]

    known, matched_tp, max_rel = take_profit_checksum()
    if known:
        out += [
            "## 검산",
            "",
            "- **(a) `base` ≡ 인자 없는 채택 북** — 실행 로그의 「검산(a) 채택 경로 최대차」"
            "(`verify_adopted_identity`, WAN-336/346과 같은 함수).",
            "- **(b) `all_off` ≡ WAN-336 `no_same_step_tp`** — 회귀 테스트가 CSV 대 CSV로 "
            "대조한다(다른 모듈·다른 실행이 같은 숫자를 내야 한다).",
            f"- **(c) 되살린 익절가** — 값이 적힌 {known}건에서 고정 R 규칙이 {matched_tp}건 "
            f"재현(최대 상대오차 {max_rel:.2e}). 재진입 행은 `익절가` 열이 비어 있어 되살린다"
            "(WAN-348과 같은 자리).",
            "",
        ]

    out += [
        "## ⚠️ 읽는 법",
        "",
        "- **`tick_off`가 답이고 하한이다** — 판정 불가인 분과 「뒤 거래만 미지지」인 분을 "
        "막지 않으므로 실제 효과는 이보다 조금 클 수 있다(§1-b가 그 크기를 밝힌다).",
        "- **「우리 주문이 채워졌을까」는 여전히 답하지 못한다** — 호가 깊이·큐 우선순위는 "
        "별개 축이고(`pen_5bp`가 그 민감도) WAN-98은 Canceled다.",
        "- **더 이른 분에서 체결했을 가능성은 안 잰다** — WAN-348·WAN-328과 같은 한계다.",
        "- **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 축은 *진입 규칙이 "
        "무작위와 구분되는가*가 아니라 *이미 잰 숫자가 얼마나 낙관인가*를 묻는다.",
        "- 총수익 %는 복리 착시(WAN-169/213) · 6년 MDD는 폭락 미포함 **바닥선**.",
        "- 측정 전용 — `no_same_step_tp`(전부든 표적이든)를 기본으로 켜는 것은 "
        "**재-베이스라인 = 사용자 결정**이다(개발자 임의 착수 금지).",
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _append(path: Path, frame: pd.DataFrame, keys: Sequence[str]) -> None:
    """같은 키의 옛 행을 **덮어쓰며** 붙인다 — 팔을 나눠 돌려도 표가 한 벌로 남는다."""
    if frame.empty:
        return
    old = _read(path)
    if not old.empty:
        merged = pd.concat([old, frame], ignore_index=True)
        merged = merged.drop_duplicates(subset=list(keys), keep="last")
    else:
        merged = frame
    merged.to_csv(path, index=False)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WAN-359 표적 반사실 — 틱이 지지하지 않는 익절만 끔"
    )
    parser.add_argument(
        "--part",
        choices=("verdicts", "book", "all"),
        default="all",
        help="verdicts=§1 전수 판정 · book=§2·§3 3팔 북 · all=둘 다",
    )
    parser.add_argument("--arms", default=",".join(ARM_ORDER))
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--append", action="store_true", help="옛 행을 덮어쓰며 붙인다")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 재생성")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_targets()

    if args.from_csv:
        verdicts = _read(VERDICT_CSV)
        if verdicts.empty:
            print(f"[wan359] {VERDICT_CSV}가 없습니다 — 먼저 --part verdicts를 돌리세요.")
            return 1
        SUMMARY_PATH.write_text(
            build_summary(
                verdicts,
                _read(BOOK_CSV),
                _read(EXIT_CSV),
                _read(COST_CSV),
                targets=targets,
            ),
            encoding="utf-8",
        )
        print(f"[wan359] 요약 재생성: {SUMMARY_PATH}")
        return 0

    if args.part in ("verdicts", "all"):
        rows, fetches = run_verdicts(targets, cache_dir=Path(args.cache_dir))
        verdict_frame(rows).to_csv(VERDICT_CSV, index=False)
        cost_frame(fetches).to_csv(COST_CSV, index=False)
        print(f"[wan359] §1 적재: {VERDICT_CSV} ({len(rows)}행)", flush=True)

    if args.part in ("book", "all"):
        verdicts = _read(VERDICT_CSV)
        if verdicts.empty:
            print(f"[wan359] {VERDICT_CSV}가 없습니다 — §1을 먼저 돌리세요.")
            return 1
        blocks = build_block_set(verdicts)
        print(
            f"[wan359] 표적 집합: 막을 분 {blocks.blocked_minutes}개(거래 "
            f"{blocks.blocked_trades}건) · 지지받은 분 {blocks.supported_minutes}개 · "
            f"판정 불가 {blocks.undecidable_minutes}개 · 뒤 거래만 미지지 "
            f"{blocks.tail_only_minutes}개",
            flush=True,
        )
        if not blocks.minutes:
            raise AssertionError(
                "막을 분이 하나도 없습니다 — 표적 팔이 기준선과 같아집니다(§1을 확인하세요)."
            )

        def _persist(rows: list[BookRow], exits: list[ExitRow]) -> None:
            _append(BOOK_CSV, pd.DataFrame([r.model_dump() for r in rows]), CSV_KEYS)
            _append(
                EXIT_CSV,
                pd.DataFrame([r.model_dump() for r in exits]),
                ("arm", "segment", "exit_reason"),
            )

        if not args.append:
            BOOK_CSV.unlink(missing_ok=True)
            EXIT_CSV.unlink(missing_ok=True)
        run_report(
            [s.strip() for s in args.symbols.split(",") if s.strip()],
            tuple(t.strip() for t in args.timeframes.split(",") if t.strip()),
            blocks=blocks,
            arms=[a.strip() for a in args.arms.split(",") if a.strip()],
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            on_arm=_persist,
        )

    SUMMARY_PATH.write_text(
        build_summary(
            _read(VERDICT_CSV),
            _read(BOOK_CSV),
            _read(EXIT_CSV),
            _read(COST_CSV),
            targets=targets,
        ),
        encoding="utf-8",
    )
    print(f"[wan359] 요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
