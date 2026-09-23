"""WAN-428 §1 — 북이 체결한 거래는 그 시점 **몇 위 존**이었나 (인구조사).

## 한 줄

사용자 결정(2026-09-23 *「일단 A로 해보자」*)으로 이 이슈는 **§1 인구조사만** 잰다 — 채택
북이 실제로 체결한 거래가 그 순간 화면에 그려지는 존 목록의 **몇 번째**였는지 분포를 낸다.
🚨 **§2(5팔 손익 격자)는 이 PR에 없다** — `zone_limit`을 시그널 경로에 걸지 않았고 팔도
만들지 않았다. 관측뿐이다.

묻는 이유는 화면과 엔진이 **다른 개수의 존을 본다**는 사실이다(이슈 §0 · 코드 확인):
`signals`/`retap_signals`(매매 경로)는 아카이브 **전체**를 소비하는데
`rendered_order_blocks`(화면)는 방향별 `zone_limit`개(원본 기본값 `Low` = **3개**)만 그린다.
그 설계는 WAN-47이 **의도**한 것(생존자 편향 제거)이되 **손익으로 검증된 적이 없다.**

## 순위 정의 — `select_active`의 규칙 **그대로**, 새로 정의하지 않는다

    alive = [ob for ob in archive if ob.direction is direction and ob.alive_at(t)]
    alive.sort(key=lambda ob: ob.confirmed_time, reverse=True)
    rank = alive.index(target) + 1        # 1-based

**방향별 · 그 시점 생존(`alive_at`) · 최신 확정순**이고 이 세 줄이 곧
`strategy.models.select_active`의 몸통이다(그쪽은 정렬·컷 **뒤에** 클리핑·병합을 하므로
**순서에 영향이 없다**). `combine=False`(채택, WAN-149)라 병합도 안 돈다.

📌 **원본 pine도 같은 기준이다**(이슈 코멘트 확인) — `bullishOrderBlocksList.unshift(new)`
(맨 앞 삽입) + `for j = 0 to bullishOrderBlocks-1`(앞에서부터). **거래량 순이 아니다**
(`obVolume`은 박스 라벨일 뿐 정렬에 안 쓰인다).

🚨 **빠른 랭커를 쓰되 그것이 `select_active`와 같음을 테스트가 동작으로 건다**
(`rank_at` ↔ `rank_at_via_select_active`). 이 저장소가 WAN-403에서 `pool_k`로 데인 자리라
**「같은 자로 쟀다」를 주장이 아니라 등식으로** 남긴다.

### ⚠️ 착수 전에 고른 것 — 순위를 매기는 **시각**은 `trigger_time`이다

주문은 탭에서 걸린다(개수 캡이 걸릴 자리가 `signals`이고 그것이 탭에서 난다). 재진입 후보는
탭이 없어 `trigger_time`이 **재무장 체결 시각**이다(WAN-228 배선 그대로) — 그것도 「그 주문이
존재한 순간」이라 `alive_at`이 그대로 정의된다. 두 부류를 **라벨로 갈라** 낸다.

### ⚠️ 민감도 한 열 — 「확정 봉이 닫힌 뒤에야 그린다」

`alive_at`은 `confirmed_time <= t`라 **확정 봉 안에서도** 살아 있다고 본다(렌더 경로가 그렇게
동작한다 — 화면과 맞추는 것이 이 이슈의 질문이다). 트레이딩뷰는 그 봉이 닫힌 뒤 그리므로
경쟁 존 하나가 순위를 1 밀어 올릴 수 있다. 그 크기를 `rank_bar_close`(= `confirmed_time +
TF <= t`) 열로 **함께** 낸다 — 판정이 그 선택에 기대는지 보이게 한다(WAN-402/417이 인과 시각을
따로 확인한 것과 같은 자리).

## 조인 — `zone_key`가 아카이브 인덱스다

`_generate_signals`가 `zone_key = frozenset({archive_idx})`를 싣고(`combine_obs=False`라 항상
원소 하나) 재진입도 부모의 값을 물려받는다(WAN-409 §0). 그래서 **같은 창·같은
`OrderBlockParams()`로 다시 탐지한 아카이브**의 그 인덱스가 곧 그 존이다.

🚨 **그 매핑이 맞다는 것을 검산 (c)가 값으로 건다** — base 거래(`is_reentry=False`)의
`trigger_time`이 그 존의 `tapped_times`에 **실제로 있어야** 한다. 인덱스가 어긋나면 그 술어가
곧바로 깨진다(개수만 세면 안 보인다 — WAN-161).

⚠️ **아카이브는 `full` 창에서 한 번만 탐지한다** — `run_cell`이 구간마다 다시 탐지하므로
차가운 `is`/`oos`는 **인덱스가 다른 아카이브**를 쓴다. 그래서 이 표는 `full`과 `oos_warm`만
낸다(`oos_warm`은 `full`을 경계로 거른 것이라 같은 아카이브다). 차가운 절단은 **안 쟀다**
(WAN-389/394와 같은 선택 · 요약이 그 사실을 찍는다).

## 재현

    uv run python -m backtest.wan428_zone_rank_census --pilot          # BTC 4h 한 칸
    uv run python -m backtest.wan428_zone_rank_census --jobs 4         # 전체(후보+대장+라벨링)
    uv run python -m backtest.wan428_zone_rank_census --from-csv       # 요약만

측정·관측 전용 · 엔진·기본값·토대 불변(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로) · 핀 없음(WAN-305) · 판단은 북에서(WAN-341) ·
`ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments
from backtest.leverage_book import LeverageBookParams
from backtest.models import ExitReason
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import CellPayload
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan408_loss_clustering import build_payloads, place
from backtest.wan409_invalidation_cascade import (
    PUBLISHED_OOS_WARM_MEAN_NET_R,
    PUBLISHED_OOS_WARM_TRADES,
)
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockParams, select_active

__all__ = [
    "CENSUS_CSV",
    "CHECKSUM_CSV",
    "PRIMARY_SEGMENT",
    "RANK_BUCKETS",
    "SEGMENTS",
    "SUMMARY_MD",
    "CellArchive",
    "ChecksumRow",
    "RankRow",
    "TradeRank",
    "ARMS",
    "ARM_ADOPTED",
    "ARM_NO_REENTRY",
    "build_archives",
    "place_arm",
    # 🚨 빌려 온 배선을 **일부러 다시 내보낸다** — 이 모듈이 후보 생성·배치를 자기 손으로
    # 짜지 않고 `wan408`의 그 함수를 쓴다는 사실이 공개 표면에 드러나야 하고, 스파이 테스트가
    # **이 이름들로** 호출 인자를 확인한다(자기 사본을 만들면 두 경로가 갈라진다).
    "build_payloads",
    "place",
    "bucket_label",
    "census_rows",
    "checksum_rows",
    "rank_at",
    "rank_at_via_select_active",
    "rank_trades",
    "render_summary",
    "run_measure",
]

#: 낼 구간 — 차가운 절단(`is`/`oos`)은 **아카이브 인덱스가 다른 판**이라 뺀다(모듈 독스트링).
SEGMENTS: tuple[str, ...] = ("full", "oos_warm")

#: 주 판정 구간(WAN-166 정본).
PRIMARY_SEGMENT = "oos_warm"

#: 순위 버킷 — `(라벨, 하한, 상한|None)`. 🚨 **착수 전에 못 박는다**(결과를 보고 경계를 옮기면
#: WAN-161). `1`·`2`·`3`을 낱개로 두는 것은 원본 기본값 `Low` = 3개가 그 선이기 때문이고,
#: `11+`를 따로 두는 것은 WAN-405가 탐지 층에서 **0.0%**로 쟀던 구간이라 대조가 된다.
RANK_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3", 3, 3),
    ("4-5", 4, 5),
    ("6-10", 6, 10),
    ("11+", 11, None),
)

#: 원본 지표 기본값 `Zone Count = "Low"`가 그리는 개수 — 「4위 이하」의 경계다.
RENDER_LIMIT_LOW = 3

#: 🚨 원본 pine의 **데이터 리스트 상한**(`const int maxOrderBlocks = 30`, `.pine:13`) —
#: `Zone Count`와 **다른 축이고 우리 엔진에는 없다**. 원본은 새 존을 `unshift`한 뒤
#: `size() > maxOrderBlocks`면 **가장 오래된 것을 `pop()`한다**(`.pine:290`/`:331`), 즉 방향별로
#: 31위 이상은 **기억조차 하지 않는다**. WAN-47이 「아카이브 전체 보존」으로 의도적으로 뗀 그
#: 캡이고, 이 표는 **그 선을 넘는 거래가 있는지**를 한 줄로 낸다(이 이슈는 캡을 걸지 않는다).
PINE_MAX_ORDER_BLOCKS = 30

CENSUS_CSV = Path("backtest/reports/wan428_zone_rank_census.csv")
CHECKSUM_CSV = Path("backtest/reports/wan428_zone_rank_checksum.csv")
SUMMARY_MD = Path("backtest/reports/wan428_zone_rank_summary.md")

#: 거래 단위 원자료 — 커밋하지 않는다(`backtest/cache/`는 gitignore). `--from-csv`가 이것을
#: 읽어 **판정 줄까지** 다시 낸다(집계 CSV만으로는 `rank_bar_close` 민감도를 복원할 수 없다).
TRADES_CSV = Path("backtest/cache/wan428/trades.csv.gz")

#: 익절 청산 유동성(WAN-370) — 이 모듈은 후보 생성·배치를 `wan408.build_payloads`/`place`에서
#: **그대로 빌려 쓰므로** 그쪽이 명시한 `harness.ADOPTED_TAKE_PROFIT_LIQUIDITY`(익절 메이커
#: 2bp)를 물려받는다. net R이 어느 비용 회계 위의 값인지는 표를 읽는 데 필수라 **여기서 이름을
#: 밝히고 요약에도 찍는다**.
#:
#: 🚨 **값을 다시 정하지 않는다** — 두 벌로 갈라지면 「라벨은 익절 메이커인데 실제는 테이커」가
#: 된다(WAN-370/373이 못 박은 자리). 그래서 이것은 **인용이지 정의가 아니고**, 빌려 쓴 두 함수가
#: 실제로 그 값을 넘기는지는 **호출부를 가로채는 스파이 테스트**가 확인한다.
INHERITED_TAKE_PROFIT_LIQUIDITY = harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


# --------------------------------------------------------------------------- #
# 순위 — `select_active`의 규칙 그대로
# --------------------------------------------------------------------------- #


def _identity(ob: OrderBlock) -> tuple[OrderBlockDirection, float, float, int, int]:
    """클리핑을 견디는 존 정체성 — `_clip_to_time`은 생애 필드만 손대고 기하는 안 건드린다."""
    return (ob.direction, ob.top, ob.bottom, ob.start_time, ob.confirmed_time)


def rank_at_via_select_active(
    archive: Sequence[OrderBlock], index: int, time_ms: int
) -> int | None:
    """**정의 그대로의** 순위 — `strategy.models.select_active`를 **실제로 부른다**(등식의 기준).

    같은 두 줄을 여기 다시 타이핑하지 않는 것이 요점이다 — 옮겨 적으면 렌더 경로가 바뀔 때
    조용히 갈린다(WAN-403이 `pool_k`에서 데인 자리). `limit=None`이면 방향별 **전체**가 순서
    그대로 나오고(`combine=False` = 채택), 방향 블록 안의 위치가 곧 순위다.

    🚨 `select_active`는 그 시점 상태로 **클리핑한 사본**을 돌려주므로 객체 동일성으로는 못
    찾는다 — 클리핑이 안 건드리는 기하(`_identity`)로 짝짓는다. 대상이 그 시점에 살아 있지
    않으면 `None`(지어내지 않는다 — WAN-367).

    ⚠️ **민감도 축(`confirm_delay_ms`)은 여기 없다** — `select_active`에 그런 노브가 없고,
    있는 척 넣으면 「같은 자로 쟀다」가 거짓이 된다. 그 변형은 `rank_at`에만 있다.
    """
    target = _identity(archive[index])
    position = 0
    for ob in select_active(list(archive), time_ms, limit=None, combine=False):
        if ob.direction is not target[0]:
            continue
        position += 1
        if _identity(ob) == target:
            return position
    return None


@dataclass(frozen=True, slots=True)
class _Ranked:
    """방향별로 **최신 확정순**으로 미리 정렬해 둔 아카이브 뷰 — 순위 질의의 입력."""

    by_direction: dict[OrderBlockDirection, tuple[tuple[int, OrderBlock], ...]]

    @classmethod
    def build(cls, archive: Sequence[OrderBlock]) -> _Ranked:
        out: dict[OrderBlockDirection, tuple[tuple[int, OrderBlock], ...]] = {}
        for direction in (OrderBlockDirection.BULLISH, OrderBlockDirection.BEARISH):
            items = [(i, ob) for i, ob in enumerate(archive) if ob.direction is direction]
            # 🚨 `list.sort`는 안정적이다 — `select_active`가 같은 키로 같은 순서를 만들고,
            # 동률(`confirmed_time`이 같은 두 존)에서 아카이브 순서가 그대로 남는 성질까지
            # 같다. 키를 바꾸면 그 동률에서 조용히 갈린다.
            items.sort(key=lambda pair: pair[1].confirmed_time, reverse=True)
            out[direction] = tuple(items)
        return cls(by_direction=out)


def rank_at(
    ranked: _Ranked,
    archive: Sequence[OrderBlock],
    index: int,
    time_ms: int,
    *,
    confirm_delay_ms: int = 0,
) -> int | None:
    """미리 정렬한 뷰로 같은 순위를 낸다(빠르다). `rank_at_via_select_active`와 **같은 값**."""
    target = archive[index]
    position = 0
    for idx, ob in ranked.by_direction[target.direction]:
        if ob.alive_at(time_ms) and ob.confirmed_time + confirm_delay_ms <= time_ms:
            position += 1
            if idx == index:
                return position
        elif idx == index:
            return None
    return None


# --------------------------------------------------------------------------- #
# 존 대장 — 탐지 층(1분봉을 안 읽는다)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CellArchive:
    """한 칸의 아카이브 — 엔진이 소비하는 **그 아카이브**를 같은 창·같은 파라미터로 다시 낸다."""

    symbol: str
    timeframe: str
    archive: tuple[OrderBlock, ...]


@dataclass(frozen=True)
class _ArchiveTask:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def _archive_for_cell(task: _ArchiveTask) -> CellArchive:
    """`harness.detect_order_blocks`를 그대로 부른다 — 사본을 손으로 만들지 않는다(WAN-77).

    🚨 창은 **`full`**(자르지 않은 전체)이다. `run_cell`이 `full` 구간에서 이 탐지를 그대로
    하므로 `zone_key`의 인덱스가 이 리스트의 인덱스와 같다(검산 (c)가 값으로 확인한다).
    """
    market = harness.load_market_data(
        harness.normalize_symbol(task.symbol),
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=False,
        funding=False,
    )
    if market.empty:
        return CellArchive(harness.normalize_symbol(task.symbol), task.timeframe, ())
    result = harness.detect_order_blocks(market, OrderBlockParams())
    print(
        f"[wan428] 존 대장 {task.symbol} {task.timeframe}: 존 {len(result.order_blocks):,}",
        flush=True,
    )
    return CellArchive(
        harness.normalize_symbol(task.symbol), task.timeframe, tuple(result.order_blocks)
    )


def build_archives(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
) -> dict[tuple[str, str], CellArchive]:
    """전 칸의 아카이브. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    tasks = [
        _ArchiveTask(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1:
        results = [_archive_for_cell(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            results = list(executor.map(_archive_for_cell, tasks))
    return {(item.symbol, item.timeframe): item for item in results}


# --------------------------------------------------------------------------- #
# 팔 — 채택(재진입 ON) + 반사실(재진입 끔)
# --------------------------------------------------------------------------- #

#: 채택 팔 = 인자 없는 채택 북(재진입 ON · WAN-273). 검산 (a)가 이 팔에만 걸린다.
ARM_ADOPTED = "adopted"

#: 🚨 **반사실 팔**(사용자 결정 2026-09-23 「일단은 (가)만」) — 재진입을 **끄고 지갑을 다시
#: 배치한다**. ❌ 「재진입을 끄자」는 제안이 **아니다**: `reentry=True`는 WAN-273 사용자 결정이고
#: 그 전환은 이 이슈 범위 밖의 재-베이스라인이다(페이퍼 러너도 같은 규칙으로 재진입한다,
#: WAN-274 · WAN-305).
#:
#: 이 팔이 있어야만 답할 수 있는 것: 「4위 이하 37%」 중 **재진입이 만든 몫**. 🚨 `scope=base`
#: 행으로는 못 답한다 — 그건 **라벨 필터**라 재진입이 쓰던 자본·슬롯을 다른 칸이 가져가는
#: **재배치가 빠져 있다**(WAN-316 · WAN-389가 같은 자리에서 못 박은 구분).
ARM_NO_REENTRY = "no_reentry"

ARMS: tuple[str, ...] = (ARM_ADOPTED, ARM_NO_REENTRY)


def place_arm(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    include_reentry: bool,
) -> list[BookSegment]:
    """한 팔의 북 배치 — `include_reentry` **하나만** 축이고 나머지는 채택 인자 그대로다.

    🚨 `include_reentry=True`면 `wan408.place`와 **같은 호출**이어야 한다(그쪽이 채택 북 인자의
    단일 소스다). 두 벌이 조용히 갈라지면 검산 (a)가 도는 팔과 반사실 팔의 **비교 자체가
    무효**가 되므로, 스파이 테스트가 두 경로의 **호출 인자 전체**를 대조해 고정한다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=include_reentry,
        take_profit_liquidity=INHERITED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# 거래 하나 · 라벨
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TradeRank:
    """북이 체결한 거래 하나 + 그 시점 존 순위."""

    arm: str
    """`adopted`(채택 북) 또는 `no_reentry`(반사실) — 팔마다 **다른 지갑**이다."""
    segment: str
    symbol: str
    timeframe: str
    trigger_time: int
    entry_time: int
    net_r: float
    """`book_cli.net_r`가 낸 값 그대로 — 공개 `book_trades.csv`의 `net R` 열과 **같은 자**다."""
    is_stop: bool
    is_reentry: bool
    tap_index: int
    archive_index: int | None
    """`zone_key`가 가리키는 아카이브 인덱스(원소 하나). 존을 못 찾았으면 `None`."""
    rank: int | None
    """렌더 경로와 같은 자(`confirm_delay_ms=0`)의 순위. 1-based. 못 잰 칸은 `None`."""
    rank_bar_close: int | None
    """민감도: 「확정 봉이 닫힌 뒤에야 그린다」(`confirm_delay_ms=TF`)의 순위."""
    alive_zones: int
    """그 시점 같은 방향으로 살아 있던 존 수 — 순위의 분모.

    「3위 중 3위」와 「200위 중 3위」는 다른 이야기다."""


def _zone_index(zone_key: frozenset[int] | None) -> int | None:
    """`zone_key`에서 아카이브 인덱스 하나를 꺼낸다 — 원소가 하나가 아니면 **거부**한다.

    🚨 채택은 `combine_obs=False`(WAN-149)라 항상 원소 하나다. 병합 경로를 조용히 통과시키면
    「한 클러스터의 몇 위」라는 정의되지 않은 순위를 매기게 된다(라벨만 붙는 실행 — WAN-91/95/
    112/123/159 부류).
    """
    if zone_key is None:
        return None
    if len(zone_key) != 1:
        raise ValueError(
            f"zone_key 원소가 {len(zone_key)}개입니다 — 이 인구조사는 분리 존(`combine_obs=False`) "
            "전용입니다(병합 클러스터의 순위는 정의되지 않습니다)."
        )
    return next(iter(zone_key))


def rank_trades(
    segment: BookSegment,
    archives: dict[tuple[str, str], CellArchive],
    *,
    arm: str = ARM_ADOPTED,
    ranked_cache: dict[tuple[str, str], _Ranked] | None = None,
) -> list[TradeRank]:
    """북 한 구간의 거래마다 그 시점 존 순위를 붙인다.

    🚨 `net_r`를 여기서 다시 정의하지 않는다 — `book_cli.net_r`가 단일 소스다(「R이라 불리는
    자가 셋」 · WAN-393 §2 · WAN-406).
    """
    from backtest.book_cli import net_r

    cache = ranked_cache if ranked_cache is not None else {}
    out: list[TradeRank] = []
    for trade, placement in segment.trades_with_placements():
        symbol, timeframe = placement.cell
        cell = (symbol, timeframe)
        item = archives.get(cell)
        index = _zone_index(placement.zone_key)
        rank = rank_bar_close = None
        alive = 0
        if item is not None and index is not None and 0 <= index < len(item.archive):
            if cell not in cache:
                cache[cell] = _Ranked.build(item.archive)
            ranked = cache[cell]
            tf_ms = timeframe_to_ms(timeframe)
            rank = rank_at(ranked, item.archive, index, placement.trigger_time)
            rank_bar_close = rank_at(
                ranked, item.archive, index, placement.trigger_time, confirm_delay_ms=tf_ms
            )
            target = item.archive[index]
            alive = sum(
                1
                for _, ob in ranked.by_direction[target.direction]
                if ob.alive_at(placement.trigger_time)
            )
        out.append(
            TradeRank(
                arm=arm,
                segment=segment.segment,
                symbol=symbol,
                timeframe=timeframe,
                trigger_time=placement.trigger_time,
                entry_time=trade.entry_time,
                net_r=net_r(trade, placement),
                is_stop=trade.exits[-1].reason is ExitReason.STOP_LOSS,
                is_reentry=placement.is_reentry,
                tap_index=placement.tap_index,
                archive_index=index,
                rank=rank,
                rank_bar_close=rank_bar_close,
                alive_zones=alive,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def bucket_label(rank: int | None) -> str:
    """순위를 버킷 라벨로 — 못 잰 칸은 「?」다(0으로 위장하지 않는다 · WAN-194/367)."""
    if rank is None:
        return "?"
    for label, low, high in RANK_BUCKETS:
        if rank >= low and (high is None or rank <= high):
            return label
    raise AssertionError(
        f"버킷에 안 맞는 순위: {rank}"
    )  # pragma: no cover - 버킷이 전 구간을 덮는다


class RankRow(BaseModel):
    """한 (구간, 스코프, 순위 버킷)의 집계."""

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    scope: str
    """`all`(전체) 또는 TF 이름 · `reentry`/`base`(부류) — 스코프는 **라벨 필터**다."""
    rank_bucket: str
    num_trades: int
    trade_share: float
    """그 스코프 거래 수 대비 몫."""
    mean_net_r: float
    net_r_sum: float
    net_r_share: float
    """`net_r_sum / 그 스코프 net R 합` — 🚨 분모가 **음수**면 「총손실에 얼마나 기여했나」로
    읽는다(WAN-409가 분모를 밝히라고 못 박은 자리). 분모가 0에 붙으면 `nan`."""
    abs_net_r_share: float
    """`|net_r_sum| / Σ|버킷별 net_r_sum|` — 부호와 무관한 **크기** 몫(분모 부호 함정 회피)."""
    stop_rate: float
    mean_alive_zones: float
    """그 버킷 거래들의 「그 시점 살아 있던 같은 방향 존 수」 평균 — 순위의 분모."""


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


def _bucket_order(label: str) -> int:
    for position, (name, _low, _high) in enumerate(RANK_BUCKETS):
        if name == label:
            return position
    return len(RANK_BUCKETS)  # 「?」는 맨 뒤


def _rows_for_scope(
    trades: Sequence[TradeRank], *, arm: str, segment: str, scope: str
) -> list[RankRow]:
    if not trades:
        return []
    total_trades = len(trades)
    total_net = sum(t.net_r for t in trades)
    grouped: dict[str, list[TradeRank]] = {}
    for trade in trades:
        grouped.setdefault(bucket_label(trade.rank), []).append(trade)
    sums = {label: sum(t.net_r for t in items) for label, items in grouped.items()}
    abs_total = sum(abs(v) for v in sums.values())
    rows: list[RankRow] = []
    for label in sorted(grouped, key=_bucket_order):
        items = grouped[label]
        bucket_sum = sums[label]
        rows.append(
            RankRow(
                arm=arm,
                segment=segment,
                scope=scope,
                rank_bucket=label,
                num_trades=len(items),
                trade_share=len(items) / total_trades,
                mean_net_r=_mean([t.net_r for t in items]),
                net_r_sum=bucket_sum,
                net_r_share=(bucket_sum / total_net if total_net else float("nan")),
                abs_net_r_share=(bucket_sum and abs(bucket_sum) / abs_total) if abs_total else 0.0,
                stop_rate=sum(1 for t in items if t.is_stop) / len(items),
                mean_alive_zones=_mean([float(t.alive_zones) for t in items]),
            )
        )
    return rows


def census_rows(trades_by_arm: dict[str, dict[str, list[TradeRank]]]) -> list[RankRow]:
    """팔 × 구간 × (전체 · TF별 · base/재진입별) 스코프의 순위 분포.

    🚨 **팔은 서로 다른 지갑이다**(재진입을 끄면 자본·슬롯이 재배치된다 — WAN-316). 팔을 가로질러
    행을 더하거나 빼지 말 것 — 그래서 `arm`이 **행의 열**이지 필터가 아니다.
    """
    rows: list[RankRow] = []
    for arm in ARMS:
        by_segment = trades_by_arm.get(arm, {})
        for segment in SEGMENTS:
            trades = by_segment.get(segment, [])
            if not trades:
                continue
            rows.extend(_rows_for_scope(trades, arm=arm, segment=segment, scope="all"))
            for timeframe in sorted({t.timeframe for t in trades}, key=timeframe_to_ms):
                subset = [t for t in trades if t.timeframe == timeframe]
                rows.extend(_rows_for_scope(subset, arm=arm, segment=segment, scope=timeframe))
            for label, predicate in (
                ("base", lambda t: not t.is_reentry),
                ("reentry", lambda t: t.is_reentry),
            ):
                subset = [t for t in trades if predicate(t)]
                if subset:
                    rows.extend(_rows_for_scope(subset, arm=arm, segment=segment, scope=label))
    return rows


#: `net R 몫`을 **낼 수 있는** 조건 — 버킷들이 서로 상쇄해 분모(`Σnet R`)가 작아지면 그 비율은
#: 뜻을 잃는다(BTC 4h 파일럿에서 실제로 `+502%`·`−322%`가 나왔다). WAN-115가 문서화하고
#: WAN-330 `_residual_ratio`·WAN-395가 코드로 막은 그 함정의 이 축 판 — **자를 넘지 못하면
#: 비율을 내지 않고 「—」로 찍는다**(숫자는 CSV에 그대로 남는다).
SHARE_DENOMINATOR_GUARD = 0.25


def share_is_meaningful(total: float, abs_total: float) -> bool:
    """`net_r_share`의 분모가 「버킷 크기 합」 대비 충분히 큰가(위 `SHARE_DENOMINATOR_GUARD`)."""
    if abs_total <= 0 or math.isnan(total) or math.isnan(abs_total):
        return False
    return abs(total) >= SHARE_DENOMINATOR_GUARD * abs_total


def below_render_limit(
    trades: Sequence[TradeRank], *, limit: int = RENDER_LIMIT_LOW
) -> dict[str, float]:
    """★판정 한 줄의 재료 — 「`limit`위 이하(= 화면에 안 그려지는) 거래」의 몫.

    `net_r_share`의 분모는 **그 구간 net R 합**이다(음수일 수 있다 — 위 `RankRow` 주석).
    `share_ok`(1/0)가 그 비율을 **인용해도 되는지**를 함께 낸다(`share_is_meaningful`).
    """
    ranked = [t for t in trades if t.rank is not None]
    beyond = [t for t in ranked if t.rank is not None and t.rank > limit]
    total_net = sum(t.net_r for t in ranked)
    beyond_net = sum(t.net_r for t in beyond)
    within_net = total_net - beyond_net
    return {
        "share_ok": float(share_is_meaningful(total_net, abs(beyond_net) + abs(within_net))),
        "num_ranked": float(len(ranked)),
        "num_beyond": float(len(beyond)),
        "trade_share": (len(beyond) / len(ranked)) if ranked else float("nan"),
        "net_r_sum": beyond_net,
        "net_r_total": total_net,
        "net_r_share": (beyond_net / total_net) if total_net else float("nan"),
        "mean_net_r": _mean([t.net_r for t in beyond]),
    }


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


class ChecksumRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: str
    segment: str
    metric: str
    left: float
    right: float
    abs_diff: float


def on_adopted_coordinates(
    symbols: Sequence[str], timeframes: Sequence[str], start: str, end: str
) -> bool:
    """검산 (a)가 성립하는 좌표인가 — 좁혀 돈 판을 공개 CSV와 대조하면 배선 오류처럼 보인다."""
    return (
        tuple(symbols) == tuple(harness.DEFAULT_SYMBOLS)
        and tuple(timeframes) == tuple(harness.DEFAULT_TIMEFRAMES)
        and start == harness.DEFAULT_START
        and end == harness.DEFAULT_END
    )


def checksum_rows(
    trades_by_arm: dict[str, dict[str, list[TradeRank]]],
    archives: dict[tuple[str, str], CellArchive],
    *,
    adopted_coordinates: bool,
) -> list[ChecksumRow]:
    """(a) 공개 채택 북 집계 · (b) 존을 못 찾은 거래 0 · (c) base 탭 시각 ∈ `tapped_times`.

    🚨 (a)는 **채택 팔에만** 건다 — 반사실 팔은 정의상 다른 지갑이라 그 등식이 성립하지 않고,
    거기에 걸면 「검산 실패」가 정상 동작이 된다(WAN-367: 실패가 성공과 같은 모양의 거울상).
    (d)는 그 반사실이 **실제로 동작했는지**를 본다 — 재진입 거래가 0건이어야 한다.
    """
    checks: list[ChecksumRow] = []
    trades_by_segment = trades_by_arm.get(ARM_ADOPTED, {})
    primary = trades_by_segment.get(PRIMARY_SEGMENT, [])
    if adopted_coordinates and primary:
        mean_net = _mean([t.net_r for t in primary])
        checks.append(
            ChecksumRow(
                check="(a) oos_warm ≡ 공개 book_trades.csv 집계",
                segment=PRIMARY_SEGMENT,
                metric="num_trades",
                left=float(len(primary)),
                right=float(PUBLISHED_OOS_WARM_TRADES),
                abs_diff=abs(len(primary) - PUBLISHED_OOS_WARM_TRADES),
            )
        )
        checks.append(
            ChecksumRow(
                check="(a) oos_warm ≡ 공개 book_trades.csv 집계",
                segment=PRIMARY_SEGMENT,
                metric="mean_net_r",
                left=mean_net,
                right=PUBLISHED_OOS_WARM_MEAN_NET_R,
                abs_diff=abs(mean_net - PUBLISHED_OOS_WARM_MEAN_NET_R),
            )
        )
    else:
        checks.append(
            ChecksumRow(
                check="(a) oos_warm ≡ 공개 book_trades.csv 집계",
                segment="—",
                metric="skipped_not_adopted_coordinates",
                left=0.0,
                right=0.0,
                abs_diff=0.0,
            )
        )

    for arm in ARMS:
        for segment in SEGMENTS:
            trades = trades_by_arm.get(arm, {}).get(segment, [])
            if not trades:
                continue
            checks.extend(_integrity_checks(trades, archives, arm=arm, segment=segment))
    return checks


def _integrity_checks(
    trades: Sequence[TradeRank],
    archives: dict[tuple[str, str], CellArchive],
    *,
    arm: str,
    segment: str,
) -> list[ChecksumRow]:
    checks: list[ChecksumRow] = []
    label = f"{arm}/{segment}"
    if trades:
        unresolved = sum(1 for t in trades if t.rank is None)
        checks.append(
            ChecksumRow(
                check="(b) 순위를 못 잰 거래 0건",
                segment=label,
                metric="num_unranked",
                left=float(unresolved),
                right=0.0,
                abs_diff=float(unresolved),
            )
        )
        # (c) 인덱스 매핑의 직접 증거 — base 거래의 탭 시각이 그 존의 `tapped_times`에 있다.
        mismatched = 0
        for trade in trades:
            if trade.is_reentry or trade.archive_index is None:
                continue
            item = archives.get((trade.symbol, trade.timeframe))
            if item is None or not (0 <= trade.archive_index < len(item.archive)):
                mismatched += 1
                continue
            if trade.trigger_time not in item.archive[trade.archive_index].tapped_times:
                mismatched += 1
        checks.append(
            ChecksumRow(
                check="(c) base 거래의 탭 시각 ∈ 그 존의 tapped_times",
                segment=label,
                metric="num_mismatched",
                left=float(mismatched),
                right=0.0,
                abs_diff=float(mismatched),
            )
        )
        if arm == ARM_NO_REENTRY:
            # (d) 반사실이 **라벨이 아니라 동작**이었는지 — 재진입 거래가 하나도 없어야 한다.
            leaked = sum(1 for t in trades if t.is_reentry)
            checks.append(
                ChecksumRow(
                    check="(d) 반사실 팔에 재진입 거래 0건",
                    segment=label,
                    metric="num_reentry_trades",
                    left=float(leaked),
                    right=0.0,
                    abs_diff=float(leaked),
                )
            )
    return checks


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def run_measure(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    cache: PayloadCache | None = None,
    log: bool = True,
) -> tuple[list[RankRow], list[ChecksumRow], dict[str, dict[str, list[TradeRank]]]]:
    """후보 → **팔마다 배치** → 존 대장 → 라벨링. 흔드는 축은 **재진입 하나**뿐이다(관측 전용).

    📌 **재진입은 배치 축이라 후보를 다시 안 만든다**(WAN-389/394 §0) — 무거운 패스는 위에서
    한 번이고 두 팔은 그 payload를 나눠 쓴다. 실측으로 팔 하나 추가 비용이 **초 단위**다.
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    t0 = time.monotonic()
    payloads: list[CellPayload] = build_payloads(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=False,
        cache=cache,
    )
    t_cand = time.monotonic()
    if log:
        print(f"[wan428] 후보 {len(payloads)}칸: {t_cand - t0:.0f}s", flush=True)

    books: dict[str, list[BookSegment]] = {}
    for arm in ARMS:
        books[arm] = place_arm(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=SEGMENTS,
            include_reentry=(arm == ARM_ADOPTED),
        )
    t_book = time.monotonic()
    if log:
        print(
            f"[wan428] 배치 {len(ARMS)}팔 × {len(SEGMENTS)}구간: {t_book - t_cand:.0f}s", flush=True
        )

    archives = build_archives(symbols, timeframes, start=start, end=end, jobs=jobs)
    t_arch = time.monotonic()
    if log:
        print(f"[wan428] 존 대장 {len(archives)}칸: {t_arch - t_book:.0f}s", flush=True)

    # 🚨 순위 뷰(`_Ranked`)는 **아카이브만의 함수**라 팔 사이에서 공유해도 된다(팔은 어떤
    # 거래를 하느냐만 바꾸지 존 대장을 안 바꾼다) — 그래서 캐시를 팔 밖에 둔다.
    ranked_cache: dict[tuple[str, str], _Ranked] = {}
    trades_by_arm = {
        arm: {
            seg.segment: rank_trades(seg, archives, arm=arm, ranked_cache=ranked_cache)
            for seg in books[arm]
        }
        for arm in ARMS
    }
    if log:
        print(f"[wan428] 라벨링: {time.monotonic() - t_arch:.0f}s", flush=True)

    rows = census_rows(trades_by_arm)
    checks = checksum_rows(
        trades_by_arm,
        archives,
        adopted_coordinates=on_adopted_coordinates(symbols, timeframes, start, end),
    )
    return rows, checks, trades_by_arm


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# 렌더링
# --------------------------------------------------------------------------- #


def _fmt(value: float, digits: int = 4) -> str:
    """`nan`을 「—」로 — 안 잰 칸을 0으로 위장하지 않는다(WAN-194 관행)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:.{digits}f}"


def _pct(value: float, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value * 100:.{digits}f}%"


def _scope_share(scope_rows: pd.DataFrame, bucket_sum: float) -> str:
    """한 스코프 안에서 `bucket_sum`의 net R 몫 — 분모가 상쇄되면 **비율을 내지 않는다**.

    🚨 CSV의 `net_r_share` 열을 그냥 찍지 않는 이유가 이것이다(BTC 4h 파일럿에서 `+502%`가
    나왔다 — 숫자는 맞는데 읽으면 틀린 부류, WAN-115/330/395).
    """
    sums = [float(v) for v in scope_rows.net_r_sum]
    total, abs_total = sum(sums), sum(abs(v) for v in sums)
    if not share_is_meaningful(total, abs_total):
        return "—"
    return _pct(bucket_sum / total)


def verdict_lines(
    census: pd.DataFrame, trades_by_arm: dict[str, dict[str, list[TradeRank]]] | None
) -> list[str]:
    """★판정 — 「4위 이하가 손익의 몇 %인가」. 코드가 찍는다(사람이 표를 보고 정하지 않는다)."""
    lines: list[str] = []
    primary = (trades_by_arm or {}).get(ARM_ADOPTED, {}).get(PRIMARY_SEGMENT)
    if primary:
        stats = below_render_limit(primary)
        share = _pct(stats["net_r_share"]) if stats["share_ok"] else "— (분모가 상쇄돼 뜻 없음)"
        lines.append(
            f"- ★ **`{PRIMARY_SEGMENT}` 4위 이하**(원본 기본값 `Low` = {RENDER_LIMIT_LOW}개 밖): "
            f"거래 **{int(stats['num_beyond']):,}건 / {int(stats['num_ranked']):,}건 "
            f"= {_pct(stats['trade_share'])}** · net R 합 **{stats['net_r_sum']:+,.1f}R** "
            f"(구간 합 {stats['net_r_total']:+,.1f}R의 **{share}**) · "
            f"거래당 {_fmt(stats['mean_net_r'])}"
        )
        beyond_ten = below_render_limit(primary, limit=10)
        share_ten = _pct(beyond_ten["net_r_share"]) if beyond_ten["share_ok"] else "— (분모 상쇄)"
        lines.append(
            f"- 「11위 이상」(WAN-405가 탐지 층에서 **0.0%**로 쟀던 구간): "
            f"거래 **{int(beyond_ten['num_beyond']):,}건 = {_pct(beyond_ten['trade_share'])}** · "
            f"net R 합 {beyond_ten['net_r_sum']:+,.1f}R ({share_ten})"
        )
        beyond_pine = below_render_limit(primary, limit=PINE_MAX_ORDER_BLOCKS)
        lines.append(
            f"- 🚨 **원본 pine의 데이터 리스트 상한({PINE_MAX_ORDER_BLOCKS}개, `.pine:13` "
            f"`maxOrderBlocks`) 밖**(= 원본은 그 존을 **기억조차 안 한다**): 거래 "
            f"**{int(beyond_pine['num_beyond']):,}건 = {_pct(beyond_pine['trade_share'])}** · "
            f"net R 합 {beyond_pine['net_r_sum']:+,.1f}R"
        )
        shifted = sum(1 for t in primary if t.rank is not None and t.rank_bar_close != t.rank)
        lines.append(
            f"- 민감도(「확정 봉이 닫힌 뒤에야 그린다」): 순위가 움직이는 거래 "
            f"**{shifted:,}건 = {_pct(shifted / len(primary))}** "
            "— 판정이 그 선택에 기대는지 보이는 자다."
        )
    else:
        lines.append("- ★ 판정: `oos_warm` 거래가 없습니다 — 좌표를 확인하십시오.")
    if not census.empty:
        row = census[
            (census.arm == ARM_ADOPTED)
            & (census.segment == PRIMARY_SEGMENT)
            & (census.scope == "all")
        ]
        top = row[row.rank_bucket == "1"]
        if not top.empty:
            lines.append(
                f"- 1위 존 거래가 **{_pct(float(top.iloc[0].trade_share))}**("
                f"거래당 {_fmt(float(top.iloc[0].mean_net_r))}) · "
                f"그 시점 살아 있던 같은 방향 존 수 평균 "
                f"**{_fmt(float(top.iloc[0].mean_alive_zones), 1)}개**"
            )
    return lines


def render_summary(
    census: pd.DataFrame,
    checks: pd.DataFrame,
    *,
    trades_by_arm: dict[str, dict[str, list[TradeRank]]] | None = None,
    cost_note: str | None = None,
) -> str:
    lines: list[str] = [
        "# WAN-428 §1 — 북이 체결한 거래는 그 시점 몇 위 존이었나 (인구조사)",
        "",
        "재현: `uv run python -m backtest.wan428_zone_rank_census --jobs 4` (요약만 `--from-csv`)",
        "",
        "🚨 **§2(5팔 손익 격자)는 이 표에 없다** — 사용자 결정(2026-09-23 「일단 A로 해보자」)으로"
        " §1만 쟀고, `zone_limit`을 **시그널 경로에 걸지 않았다**(관측 전용).",
        "",
        f"좌표: 12종목 × 4TF × 못 박은 6년 · `{PRIMARY_SEGMENT}` 주 수치 · 핀 없음(WAN-305) ·"
        " 판단은 북에서(WAN-341) · 전부 `baseline`(낙관) 렌즈 위 값 ·"
        f" 익절 청산 유동성 **{INHERITED_TAKE_PROFIT_LIQUIDITY.value}**"
        "(`harness.ADOPTED_TAKE_PROFIT_LIQUIDITY`, WAN-370).",
        "",
        "순위 = `select_active`의 규칙 **그대로**(방향별 · 그 시점 생존 · **최신 확정순**) —"
        " 원본 pine의 `unshift` + `for j = 0 to bullishOrderBlocks-1`과 같은 기준이고"
        " **거래량 순이 아니다**.",
        "",
        "## 판정",
        "",
    ]
    lines.extend(verdict_lines(census, trades_by_arm))
    lines.extend(
        [
            "",
            "## 순위 분포 — `oos_warm` 전체",
            "",
            "| 순위 | 거래 | 거래 몫 | 거래당 net R | net R 합 | net R 몫 | 손절률 | 생존 존 수 |",
            "| -- | --: | --: | --: | --: | --: | --: | --: |",
        ]
    )
    primary_rows = (
        census[
            (census.arm == ARM_ADOPTED)
            & (census.segment == PRIMARY_SEGMENT)
            & (census.scope == "all")
        ]
        if not census.empty
        else census
    )
    for _, row in primary_rows.iterrows():
        lines.append(
            f"| {row.rank_bucket} | {int(row.num_trades):,} | {_pct(float(row.trade_share))} | "
            f"{_fmt(float(row.mean_net_r))} | {float(row.net_r_sum):+,.1f}R | "
            f"{_scope_share(primary_rows, float(row.net_r_sum))} | "
            f"{_pct(float(row.stop_rate), 1)} | {_fmt(float(row.mean_alive_zones), 1)} |"
        )
    lines.extend(
        [
            "",
            "⚠️ **`net R 몫`의 분모는 그 스코프 net R 합이다** — 채택 좌표에서 그 합은"
            " **음수**이므로 이 열은 「그 순위가 **총손실에** 얼마나 기여했나」로 읽는다(WAN-409가"
            " 분모를 밝히라고"
            " 못 박은 자리). 🚨 버킷들이 서로 상쇄해 분모가 작아지면(`|합| <"
            f" {SHARE_DENOMINATOR_GUARD:.0%} × Σ|버킷|`) **비율을 내지 않고 「—」로 찍는다** —"
            " 그 자리에서는 숫자가 맞는데 읽으면 틀린다(WAN-115/330/395 부호 함정). 부호와 무관한"
            " 크기 몫은 CSV의 `abs_net_r_share` 열.",
            "",
            "## TF별 (`oos_warm`)",
            "",
            "| TF | 순위 | 거래 | 거래 몫 | 거래당 net R | net R 합 | net R 몫 |",
            "| -- | -- | --: | --: | --: | --: | --: |",
        ]
    )
    if not census.empty:
        tf_rows = census[
            (census.arm == ARM_ADOPTED)
            & (census.segment == PRIMARY_SEGMENT)
            & (~census.scope.isin(["all", "base", "reentry"]))
        ]
        for _, row in tf_rows.iterrows():
            scope_rows = tf_rows[tf_rows.scope == row.scope]
            lines.append(
                f"| {row.scope} | {row.rank_bucket} | {int(row.num_trades):,} | "
                f"{_pct(float(row.trade_share))} | {_fmt(float(row.mean_net_r))} | "
                f"{float(row.net_r_sum):+,.1f}R | "
                f"{_scope_share(scope_rows, float(row.net_r_sum))} |"
            )
    lines.extend(
        [
            "",
            "## 반사실 — 재진입을 끄면 순위가 어떻게 움직이나",
            "",
            "🚨 **「재진입을 끄자」는 제안이 아니다** — `reentry=True`는 WAN-273 **사용자"
            " 결정**이고 페이퍼 러너도 같은 규칙으로 재진입한다(WAN-274 · WAN-305). 이 팔은"
            " **귀속용**이다: 「4위 이하」 중 **재진입이 만든 몫**을 가른다"
            "(사용자 결정 2026-09-23 「일단은 (가)만」).",
            "",
            "🚨 **`scope=base` 행으로는 이 질문에 답할 수 없다** — 그건 **라벨 필터**라 재진입이"
            " 쓰던 자본·슬롯을 다른 칸이 가져가는 **재배치가 빠져 있다**(WAN-316 · WAN-389).",
            "",
            "| 팔 | 거래 | 거래당 net R | 1위 | 4위 이하 | 11위+ |",
            "| -- | --: | --: | --: | --: | --: |",
        ]
    )
    for arm in ARMS:
        trades = (trades_by_arm or {}).get(arm, {}).get(PRIMARY_SEGMENT)
        if not trades:
            lines.append(f"| `{arm}` | — | — | — | — | — |")
            continue
        stats = below_render_limit(trades)
        ten = below_render_limit(trades, limit=10)
        first = sum(1 for t in trades if t.rank == 1)
        lines.append(
            f"| `{arm}` | {len(trades):,} | {_fmt(_mean([t.net_r for t in trades]))} | "
            f"{_pct(first / len(trades))} | {_pct(stats['trade_share'])} "
            f"({stats['net_r_sum']:+,.1f}R) | {_pct(ten['trade_share'])} |"
        )
    lines.extend(
        [
            "",
            "⚠️ **두 팔은 서로 다른 지갑이다** — 거래 수 차이를 「재진입 거래 수」로 읽지"
            " 말 것(재진입을 끄면 남은 후보가 그 슬롯을 가져가 base 거래도 늘어난다,"
            " WAN-389 §슬롯 경합).",
            "",
            "## WAN-405 §부수답과의 관계",
            "",
            "WAN-405는 **탐지 층 탭 분포**에서 *「탭의 약 80%가 1위 존 · 99%가 ≤3위 · 11위＋"
            " 0.0%」*를 냈다(1h·2h·4h · 생짜 · 두 탐지기 공통). 이 표는 **북 거래**이고"
            " **15m 포함 4TF · 가드 0.3% · 재진입 ON**이다 — 같은 방향인지는 위 판정 줄의"
            " 「11위 이상」과 1위 몫이 답한다.",
            "",
            "## 🚨 원본 pine 대조 — 이슈 코멘트의 선행 조사 답(한 줄 요구분)",
            "",
            "이슈 코멘트가 *「원본 pine `:268`의 `else if i < bullishOrderBlocks …`가 **탭"
            " 판정에도** 개수를 쓴다 · 즉 원본에서 `Zone Count`는 표시 설정이 아니라 **신호"
            " 파라미터**다」*를 §2의 선행 조사로 남겼다. **원본 파일을 읽어 보니 그 전제는"
            " 성립하지 않는다**(WAN-400 §A와 같은 부류):",
            "",
            "1. 그 가드가 세우는 값은 `bullishBreaked`(`.pine:254`·`:269`)와"
            " `bearishBreaked`(`:295`·`:310`)인데 **파일 어디에서도 읽지 않는다**"
            " (`grep -in breaked`가 네 줄 = 초기화 2 + 대입 2뿐). 즉 그 `i <"
            " bullishOrderBlocks` 가드는 **원본에서 아무것도 하지 않는 죽은 코드**이고,"
            " 게다가 `breaker`인 존에만 · **탭이 아니라 스윙 고점**(`top.y`)이 그 안에 드는지를"
            " 본다. **`Zone Count`는 원본에서도 표시 설정이다.**",
            "2. 📌 **대신 진짜 캡이 따로 있다** — `const int maxOrderBlocks = 30`(`.pine:13`)이"
            " `unshift` 뒤 `size() > maxOrderBlocks`면 **가장 오래된 존을 `pop()`한다**"
            f"(`:290`/`:331`). 원본은 방향별 **{PINE_MAX_ORDER_BLOCKS}개까지만 기억한다** —"
            " WAN-47이 「아카이브 전체 보존」으로 의도적으로 뗀 그 캡이고, **§2가 원본과 맞추려면"
            " 흔들 축은 `Zone Count`가 아니라 이쪽**일 수 있다. 위 판정 줄이 그 선을 넘는 거래가"
            " 있는지를 낸다.",
            "",
            "⚠️ **이 절은 파일 대조이지 측정이 아니다** — 캡을 걸어 본 적이 없고, 둘 중 어느"
            " 축도 이 PR에서 시그널 경로에 배선하지 않았다.",
            "",
            "## 검산",
            "",
            "| 검산 | 구간 | 지표 | 이 표 | 상대 | 절대차 |",
            "| -- | -- | -- | --: | --: | --: |",
        ]
    )
    for _, row in checks.iterrows():
        lines.append(
            f"| {row.check} | {row.segment} | {row.metric} | {_fmt(float(row.left), 6)} | "
            f"{_fmt(float(row.right), 6)} | {float(row.abs_diff):.2e} |"
        )
    lines.extend(
        [
            "",
            "## 알려진 한계",
            "",
            "- ⚠️ **차가운 절단(`is`/`oos`)은 안 쟀다** — `run_cell`이 구간마다 다시 탐지하므로"
            " 그 판은 **아카이브 인덱스가 다르고**, `zone_key`로 존을 되찾을 수 없다."
            " `full`·`oos_warm`은 같은 아카이브라 성립한다(WAN-389/394와 같은 선택).",
            "- ⚠️ **재진입 거래의 `trigger_time`은 탭이 아니라 재무장 체결 시각**이다"
            "(WAN-228 배선) — 「그 주문이 존재한 순간」이라 순위는 정의되지만 base와 같은 뜻의"
            " 시각이 아니다. CSV의 `scope=base|reentry` 행으로 갈라 두었다.",
            "- 🚨 **「그래서 존을 줄이면 낫다」로 읽지 말 것** — 이 표는 **관측**이고 개수 캡을"
            " 걸어 본 적이 없다. 거래를 줄이면 좋아 보이는 착시가 이 저장소에서 반복됐다"
            "(WAN-378: 조일수록 손실이 주는 것처럼 보였는데 거래를 74% 줄인 것이었다).",
            "- ⚠️ 전부 `baseline`(낙관) 렌즈 위 값이고 **체결 보수화(`pen_5bp`) 미측정** ·"
            " **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변**(이 축은 *몇 개의 존을"
            " 볼까*를 묻는다 — **다른 질문**).",
            "- **측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·"
            "`LeverageBookParams()` 그대로) · 전환은 **재-베이스라인 = 사용자 결정** ·"
            " 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).",
        ]
    )
    if cost_note:
        lines.extend(["", f"📌 실측 비용: {cost_note}"])
    return "\n".join(lines) + "\n"


def _load_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.read_csv(CENSUS_CSV), pd.read_csv(CHECKSUM_CSV)


def trades_frame(trades_by_arm: dict[str, dict[str, list[TradeRank]]]) -> pd.DataFrame:
    """거래 단위 원자료 — `--from-csv`가 판정 줄을 복원하는 데 필요하다."""
    return pd.DataFrame.from_records(
        [
            asdict(t)
            for arm in ARMS
            for segment in SEGMENTS
            for t in trades_by_arm.get(arm, {}).get(segment, [])
        ]
    )


def trades_from_frame(frame: pd.DataFrame) -> dict[str, dict[str, list[TradeRank]]]:
    """CSV 왕복 — 🚨 빈 칸(`NaN`)을 `None`으로 되돌린다.

    pandas가 `rank`의 빈 칸을 `NaN`으로 되살리면 「순위를 못 잰 거래」가 유효한 float으로
    둔갑해 판정에 섞인다(WAN-395 부수 수리 ②가 고친 그 함정).
    """

    def _opt(value: object) -> int | None:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return int(str(value).split(".")[0])

    out: dict[str, dict[str, list[TradeRank]]] = {}
    for rec in frame.to_dict("records"):
        out.setdefault(str(rec["arm"]), {}).setdefault(str(rec["segment"]), []).append(
            TradeRank(
                arm=str(rec["arm"]),
                segment=str(rec["segment"]),
                symbol=str(rec["symbol"]),
                timeframe=str(rec["timeframe"]),
                trigger_time=int(rec["trigger_time"]),
                entry_time=int(rec["entry_time"]),
                net_r=float(rec["net_r"]),
                is_stop=bool(rec["is_stop"]),
                is_reentry=bool(rec["is_reentry"]),
                tap_index=int(rec["tap_index"]),
                archive_index=_opt(rec["archive_index"]),
                rank=_opt(rec["rank"]),
                rank_bar_close=_opt(rec["rank_bar_close"]),
                alive_zones=int(rec["alive_zones"]),
            )
        )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-428 §1 존 순위 인구조사")
    parser.add_argument("--symbol", default=None, help="콤마 구분(기본 = 채택 12종목)")
    parser.add_argument("--tf", default=None, help="콤마 구분(기본 = 채택 4TF)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=None, help="성능 노브(WAN-121)")
    parser.add_argument("--pilot", action="store_true", help="BTC 4h 한 칸만(배선 확인)")
    parser.add_argument("--no-cache", action="store_true", help="payload 캐시를 쓰지 않는다")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 다시 낸다")
    args = parser.parse_args(argv)

    if args.from_csv:
        census, checks = _load_frames()
        # 거래 단위 원자료가 있으면 **판정 줄까지** 복원한다 — 없으면 그 사실이 요약에 드러난다
        # (지어내지 않는다 · WAN-194/367).
        trades = trades_from_frame(pd.read_csv(TRADES_CSV)) if TRADES_CSV.exists() else None
        if trades is None:
            print(f"[wan428] ⚠️ {TRADES_CSV}가 없어 판정 줄을 복원하지 못합니다.", flush=True)
        SUMMARY_MD.write_text(
            render_summary(census, checks, trades_by_arm=trades), encoding="utf-8"
        )
        print(f"[wan428] 요약 재생성: {SUMMARY_MD}")
        return 0

    symbols = (
        ["BTCUSDT"]
        if args.pilot
        else (
            [s.strip() for s in args.symbol.split(",")]
            if args.symbol
            else list(harness.DEFAULT_SYMBOLS)
        )
    )
    timeframes = (
        ["4h"]
        if args.pilot
        else (
            [t.strip() for t in args.tf.split(",")] if args.tf else list(harness.DEFAULT_TIMEFRAMES)
        )
    )
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    print(
        f"[wan428] 병렬 설정: 워커 {jobs}개 · 칸 {len(symbols) * len(timeframes)}개 "
        f"({len(symbols)}종목 × {len(timeframes)}TF)",
        flush=True,
    )

    t0 = time.monotonic()
    rows, checks, trades = run_measure(
        symbols,
        timeframes,
        start=args.start,
        end=args.end,
        jobs=jobs,
        cache=None if args.no_cache else PayloadCache(),
    )
    elapsed = time.monotonic() - t0

    census_frame = pd.DataFrame([r.model_dump() for r in rows])
    checks_frame = pd.DataFrame([c.model_dump() for c in checks])
    if args.pilot:
        print(census_frame.to_string(index=False))
        print(checks_frame.to_string(index=False))
        print(f"[wan428] 파일럿 {elapsed:.0f}s — CSV는 쓰지 않았다(좁혀 돈 판)")
        return 0

    _write(census_frame, CENSUS_CSV)
    _write(checks_frame, CHECKSUM_CSV)
    TRADES_CSV.parent.mkdir(parents=True, exist_ok=True)
    trades_frame(trades).to_csv(TRADES_CSV, index=False, compression="gzip")
    note = (
        f"{elapsed:.0f}s = {elapsed / 3600:.2f}시간"
        f"({len(symbols) * len(timeframes)}칸 · `--jobs {jobs}` · M1)"
    )
    SUMMARY_MD.write_text(
        render_summary(census_frame, checks_frame, trades_by_arm=trades, cost_note=note),
        encoding="utf-8",
    )
    print(f"[wan428] 적재: {CENSUS_CSV} · {CHECKSUM_CSV} · {SUMMARY_MD} ({note})")
    failed = [c for c in checks if c.abs_diff > 1e-9 and "skipped" not in c.metric]
    if failed:
        for c in failed:
            print(f"[wan428] 🚨 검산 실패: {c.check} / {c.metric} 차 {c.abs_diff:.2e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
