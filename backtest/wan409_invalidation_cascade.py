"""WAN-409 §1·§2 — 무효화 봉 안의 「같은 존 연속 체결·연속 손절」 인구조사 (2026-09-08).

사용자가 올린 `book_trades.csv`(채택 북 `oos_warm` · 14,843거래)에서 **직전 거래가 손절이고
손절가가 같은** 거래 391건(2.64%)이 손절률 97.4% · 거래당 net R −1.1288 · 순손실의 24.6%를
만든다는 관측이 나왔다. 그 관측의 「같은 존」은 **`손절가` 일치라는 대리변수**였고(WAN-362 §3),
이 모듈이 그것을 **진짜 존 식별자**(`PlacedSetup.zone_key` = 탐지 아카이브 인덱스 집합,
WAN-83)로 갈아 끼워 다시 센다.

🚨 **판정 자는 「몇 건인가」가 아니라 「어느 코드 경로인가」다**(WAN-367: 97.4%·중앙값 0분 같은
칼 같은 경계는 결과가 아니라 **제약의 지문**이다 — 설명을 붙이기 전에 코드에서 경로를 찾는다).
그래서 이 표는 세 가지를 함께 낸다:

1. **존 식별자 교체**(§1) — 진짜 `zone_key`로 센 무더기와 대리변수(`손절가`)로 센 무더기의
   **교차표**. 둘이 갈리는 만큼 원 관측의 391에 「우연히 같은 가격대인 다른 존」이 섞여 있었다.
2. **코드 경로 귀속**(§2) — 그 무더기의 몇 %가 `entry_after_invalidation`(= 체결 시각이 존
   `break_time` 이후 = **무효화 봉 안에서 체결**, WAN-364 관측 필드)인가. 이 열이 곧 가설의
   직접 증거다.
3. **반사실 팔**(§2) — `retap_mode="once"`와 `invalidation_cancel="bar_open"`에서 그 무더기가
   **실제로 사라지는지**를 동작으로 확인한다(라벨이 아니라 숫자로).

## 좌표 · 팔

채택 좌표 그대로다 — 12종목 × 4TF **한 지갑** · 못 박은 6년 · 존폭 필터 끔(WAN-384) · 인과 취소
(WAN-365) · 재탭 `every_tap` · 재진입 band(WAN-273) · cap_only 5배 · 익절 메이커(WAN-370) ·
**핀 하나도 없다**(WAN-305). 복리는 **켠다** — 원 관측 CSV가 인자 없는 채택 북
(`backtest.run --positions book --oos-warm --trades`)의 산출물이라 그것과 같은 지갑이라야
검산 (a)가 성립한다.

| 팔 | 뜻 |
| -- | -- |
| `base` | 채택 북 그대로(= 인자 없는 `backtest.run`) |
| `retap_once` | `retap_mode="once"` — 존당 첫 탭만 후보 |
| `cancel_bar_open` | `invalidation_cancel="bar_open"` — WAN-365 이전의 소급 취소 |

🚨 **팔마다 후보를 다시 만든다**(둘 다 후보 집합을 바꾸는 축이다) — payload를 공유하면 라벨만
다른 같은 숫자가 나온다(WAN-388이 `combine_obs`에서 겪은 자리).

## 🚨 이 표가 답하지 않는 것

* **「필터를 켜라」가 아니다.** 무더기를 지우면 공유 자본·슬롯이 재배치돼 다른 숫자가 나오고
  (WAN-316), 애초에 `retap_mode`·`invalidation_cancel` 전환은 **재-베이스라인 = 사용자 결정**
  이다(WAN-404 · WAN-365 소관). 이 모듈은 **기본값을 하나도 안 건드린다.**
* **「라이브도 이러는가」가 아니다** — 그것이 이 이슈의 진짜 질문이고 **서버 몫**이다
  (`live/cascade_census.py` · `alphablock cascade` · `scripts/wan409-server-cascade-census.sh`).
  로컬은 러너 장부가 비어 판정할 수 없다(WAN-195/314/353과 같은 제약).
* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386)** 은 불변이다 — 이 표는 *같은 셋업을 몇
  번에 나눠 잡나*를 묻는다(**다른 질문**).

## 재현

    uv run python -m backtest.wan409_invalidation_cascade --arms base --jobs 4
    uv run python -m backtest.wan409_invalidation_cascade --arms retap_once --append --jobs 4
    uv run python -m backtest.wan409_invalidation_cascade --arms cancel_bar_open --append --jobs 4
    uv run python -m backtest.wan409_invalidation_cascade --from-csv          # 요약만
    uv run python -m backtest.wan409_invalidation_cascade --pilot --arms base # 한 칸 견적
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import LeverageBookParams
from backtest.models import ExitReason
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan386_confirmation_pnl import ChecksumRow, _compare_segments

REPORTS_DIR = Path("backtest/reports")
CENSUS_CSV_PATH = REPORTS_DIR / "wan409_cascade_census.csv"
AGREEMENT_CSV_PATH = REPORTS_DIR / "wan409_zone_id_agreement.csv"
CHAIN_CSV_PATH = REPORTS_DIR / "wan409_chain_length.csv"
TF_CSV_PATH = REPORTS_DIR / "wan409_by_timeframe.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan409_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan409_invalidation_cascade_summary.md"

ARM_BASE = "base"
ARM_RETAP_ONCE = "retap_once"
ARM_CANCEL_BAR_OPEN = "cancel_bar_open"
ARM_ORDER: tuple[str, ...] = (ARM_BASE, ARM_RETAP_ONCE, ARM_CANCEL_BAR_OPEN)

#: 구간 순서 — `is`는 **차가운 절단**이라 `--cold-segments`를 켠 실행에만 나온다(아래 참고).
SEGMENT_FULL = "full"
SEGMENT_IS = "is"
SEGMENT_OOS_WARM = "oos_warm"
SEGMENT_OOS = "oos"
SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)
PRIMARY_SEGMENT = SEGMENT_OOS_WARM

#: 존 식별자 두 가지 — 진짜(`zone_key`)와 원 관측의 대리변수(`손절가`).
ZONE_ID_REAL = "real"
ZONE_ID_PROXY = "proxy"

#: 부류 넷 — 원 관측 표와 **같은 칸**이다(칸 안 첫 거래는 직전이 없어 분류에서 빠진다).
GROUP_CASCADE = "prev_stop_same_zone"
GROUP_STOP_OTHER = "prev_stop_other_zone"
GROUP_TP_SAME = "prev_tp_same_zone"
GROUP_TP_OTHER = "prev_tp_other_zone"
GROUP_ORDER: tuple[str, ...] = (GROUP_CASCADE, GROUP_STOP_OTHER, GROUP_TP_SAME, GROUP_TP_OTHER)

MINUTE_MS = 60_000

#: 검산 (c)의 상대 — 사용자가 올린 `book_trades.csv`(2026-09-04 · 인자 없는 채택 북
#: `--positions book --oos-warm --trades`)의 집계다. 🚨 **다른 실행·다른 날의 숫자**라, 이
#: 모듈의 기준 팔이 여기에 맞으면 「채택 북을 돌고 있다」가 래퍼 인자 대조보다 강하게 선다
#: (WAN-330/394 관행). 좌표가 다르면 대조하지 않는다.
PUBLISHED_OOS_WARM_TRADES = 14_843
PUBLISHED_OOS_WARM_MEAN_NET_R = -0.12073851686076308


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class _ArmRow(BaseModel):
    """모든 표의 공통 머리 — 팔 라벨. `--append`가 **팔 단위로** 갈아 끼울 수 있는 근거다."""

    model_config = ConfigDict(frozen=True)

    arm: str


class CascadeRow(_ArmRow):
    """한 (팔, 구간, 존 식별자, 부류)의 인구조사 한 줄."""

    segment: str
    zone_id: str
    group: str
    num_trades: int
    num_classified: int
    """그 (팔, 구간)에서 분류된 거래 수 — 분모(칸 안 첫 거래는 빠진다)."""
    share_of_classified: float
    stop_rate: float
    mean_net_r: float
    sum_net_r: float
    share_of_net_total: float | None
    """이 부류의 net R 합 ÷ 그 구간 **전체 거래**의 net R 합.

    🚨 두 경우에 `None`이다(WAN-115 부호·크기 함정): 분모가 **양수**일 때(버는 구간에서 「손실
    중 몇 %」는 뜻이 없다)와 이 부류의 합이 **분모보다 클 때**(「비중」이라는 낱말이 100%를
    넘으면 읽는 사람이 반드시 틀린다). 지어내지 않고 표에 `—`로 찍는다."""
    same_minute_share: float
    """직전 청산과 **같은 1분**에 진입한 비율(간격 0분)."""
    median_gap_minutes: float
    invalidation_bar_share: float
    """`entry_after_invalidation` 비율 — 체결이 존 무효화 봉 안이었는가(WAN-364 관측)."""
    reentry_share: float


class AgreementRow(_ArmRow):
    """진짜 존 식별자 대 대리변수 — 「무더기」 술어의 교차표 (완료기준 1)."""

    segment: str
    num_classified: int
    both: int
    """둘 다 무더기라고 본 거래."""
    real_only: int
    proxy_only: int
    """🚨 대리변수만 무더기라 본 거래 = 「우연히 손절가가 같은 다른 존」."""
    neither: int


class ChainRow(_ArmRow):
    """같은 존에서 연달아 손절난 사슬의 길이 분포 (완료기준 2 — 「몇 개까지 체결되나」)."""

    segment: str
    zone_id: str
    chain_length: int
    """한 사슬에 든 거래 수(2 이상 — 1은 사슬이 아니다)."""
    num_chains: int
    num_trades: int


class TimeframeRow(_ArmRow):
    """TF별 무더기 — 원 관측(15m 269 · 1h 73 · 2h 38 · 4h 11)과 대조할 열."""

    segment: str
    zone_id: str
    timeframe: str
    num_cascade: int
    num_classified: int
    share_of_classified: float


class ArmChecksumRow(_ArmRow):
    """검산 한 줄 — `wan386.ChecksumRow`에 **팔 라벨만** 더한 것이다.

    팔마다 검산이 따로 나오는데 그 모델에는 `arm`이 없어 `--append`가 옛 팔의 줄을 갈아 끼울
    수 없다(같은 팔의 줄이 겹겹이 쌓인다). 값은 그대로 옮긴다.
    """

    check: str
    segment: str
    metric: str
    left: float
    right: float
    abs_diff: float


def _with_arm(rows: Sequence[ChecksumRow], arm: str) -> list[ArmChecksumRow]:
    return [ArmChecksumRow(arm=arm, **row.model_dump()) for row in rows]


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def _cell_kwargs(arm: str) -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계).

    팔이 얹는 것은 **축 하나**뿐이다. `base`는 아무것도 안 얹으므로 `run_cells`가 채택
    기본값을 그대로 읽고(`retap_mode=None` = `every_tap` · `invalidation_cancel=None` =
    `bar_close`), 그래서 검산 (a)가 「인자 없는 채택 북」과 성립한다.
    """
    kwargs: dict[str, object] = {
        **ADOPTED_CELL_KWARGS,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    }
    if arm == ARM_RETAP_ONCE:
        kwargs["retap_mode"] = "once"
    elif arm == ARM_CANCEL_BAR_OPEN:
        kwargs["invalidation_cancel"] = "bar_open"
    elif arm != ARM_BASE:
        raise ValueError(f"모르는 팔: {arm!r} (가능: {', '.join(ARM_ORDER)})")
    return kwargs


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    arm: str = ARM_BASE,
    cold_segments: bool = False,
    cache: PayloadCache | None = None,
) -> list[CellPayload]:
    """한 팔의 후보 — 무거운 패스는 여기 한 번이다.

    `cold_segments=False`(기본)면 차가운 `is`/`oos`를 건너뛰어 셀 비용의 큰 몫을 아낀다
    (WAN-301 노브). 그 대신 완료기준 5의 `is`는 **`--cold-segments`를 켠 실행에서만** 나온다 —
    끄고 돈 실행의 요약이 그 사실을 스스로 찍는다(없는 구간을 빈 값으로 위장하지 않는다).
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        engine_check=False,
        payload_cache=cache,
        **_cell_kwargs(arm),  # type: ignore[arg-type]
    )


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 복리를 **켠다**(원 관측 CSV가 인자 없는 채택 북의 산출물이다).

    `include_reentry=True`가 채택 규칙이고(WAN-273/305), 익절 청산 유동성을 여기에도 명시해야
    한 표가 한 회계다(WAN-370).
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        min_stop_distance_fraction=ADOPTED_STOP_GUARD,
        compound_sizing=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# 순수 분류 — 표본 없이도 테스트가 이 부분을 통째로 건다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TradeFact:
    """분류에 필요한 것만 뽑은 거래 하나 — 북 배치 기록(`PlacedSetup`)에서 그대로 온다."""

    cell: tuple[str, str]
    entry_time: int
    exit_time: int
    is_stop: bool
    net_r: float
    """`book_cli.net_r`가 낸 값 그대로 — 원 관측 CSV의 `net R` 열과 **같은 자**다
    (리스크 0이면 그쪽도 0으로 본다). 두 벌로 갈라 두면 검산 (c)가 성립하지 않는다."""
    zone_key: frozenset[int] | None
    stop_price: float
    entry_after_invalidation: bool
    is_reentry: bool


@dataclass(frozen=True)
class ClassifiedTrade:
    """`TradeFact` ＋ 두 존 식별자로 각각 매긴 부류와 직전 청산과의 간격."""

    fact: TradeFact
    group_real: str
    group_proxy: str
    gap_minutes: float


def trade_facts(segment: BookSegment) -> list[TradeFact]:
    """북 한 구간의 거래를 분류용 사실로 — 짝 계약은 `book_cli`가 이미 값으로 확인했다."""
    facts: list[TradeFact] = []
    for trade, placement in segment.trades_with_placements():
        facts.append(
            TradeFact(
                cell=placement.cell,
                entry_time=trade.entry_time,
                exit_time=trade.exits[-1].time,
                is_stop=trade.exits[-1].reason is ExitReason.STOP_LOSS,
                net_r=net_r(trade, placement),
                zone_key=placement.zone_key,
                stop_price=placement.stop_price,
                entry_after_invalidation=placement.entry_after_invalidation,
                is_reentry=placement.is_reentry,
            )
        )
    return facts


def _same_zone_real(a: TradeFact, b: TradeFact) -> bool:
    """진짜 존 식별자 — 🚨 한쪽이라도 `None`이면 **같다고 하지 않는다**.

    `zone_key`는 그 칸 탐지 아카이브의 인덱스 집합이라 칸 안에서 안정적이다(WAN-83). 없으면
    「모른다」이지 「다르다」도 「같다」도 아니므로, 무더기로 세지 않는 **보수적인 쪽**으로 간다
    (지어내지 않는다 — WAN-194). 값이 실제로 붙어 있는지는 검산 (b)가 센다.
    """
    return a.zone_key is not None and b.zone_key is not None and a.zone_key == b.zone_key


def _same_zone_proxy(a: TradeFact, b: TradeFact) -> bool:
    """원 관측의 대리변수 — 손절 참조가가 **정확히** 같은가(WAN-362 §3)."""
    return a.stop_price == b.stop_price


def _group(prev_is_stop: bool, same_zone: bool) -> str:
    if prev_is_stop:
        return GROUP_CASCADE if same_zone else GROUP_STOP_OTHER
    return GROUP_TP_SAME if same_zone else GROUP_TP_OTHER


def classify(facts: Iterable[TradeFact]) -> list[ClassifiedTrade]:
    """칸 안에서 **직전 거래**와 견줘 부류를 매긴다 — 칸의 첫 거래는 분류에서 빠진다.

    북의 칸은 한 번에 한 포지션이라 같은 칸의 거래는 겹치지 않는다. 정렬 키는 진입 시각이고
    (동시각은 청산 시각으로 안정 정렬) 그래서 「직전 거래」가 유일하게 정해진다.

    ⚠️ 직전이 **기간종료**면 「직전 손절 아님」 쪽으로 간다(원 관측 표의 「직전 익절」 칸과 같은
    처리 — 그 부류는 6년 채택 북에서 5건이다).
    """
    by_cell: dict[tuple[str, str], list[TradeFact]] = {}
    for fact in facts:
        by_cell.setdefault(fact.cell, []).append(fact)
    out: list[ClassifiedTrade] = []
    for cell_facts in by_cell.values():
        ordered = sorted(cell_facts, key=lambda f: (f.entry_time, f.exit_time))
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            out.append(
                ClassifiedTrade(
                    fact=cur,
                    group_real=_group(prev.is_stop, _same_zone_real(prev, cur)),
                    group_proxy=_group(prev.is_stop, _same_zone_proxy(prev, cur)),
                    gap_minutes=(cur.entry_time - prev.exit_time) / MINUTE_MS,
                )
            )
    return out


def _group_of(item: ClassifiedTrade, zone_id: str) -> str:
    return item.group_real if zone_id == ZONE_ID_REAL else item.group_proxy


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _share_of_net_total(sum_net: float, net_total: float) -> float | None:
    """「순손익 대비 몇 %」 — 읽으면 틀리는 자리에서는 **비율을 내지 않는다**(WAN-115 관행)."""
    if net_total >= 0.0 or abs(sum_net) > abs(net_total):
        return None
    return sum_net / net_total * 100.0


def census_rows(
    classified: Sequence[ClassifiedTrade],
    *,
    arm: str,
    segment: str,
    net_total: float,
) -> list[CascadeRow]:
    """(팔, 구간) 하나의 인구조사 — 존 식별자 둘 × 부류 넷."""
    rows: list[CascadeRow] = []
    total = len(classified)
    for zone_id in (ZONE_ID_REAL, ZONE_ID_PROXY):
        for group in GROUP_ORDER:
            members = [c for c in classified if _group_of(c, zone_id) == group]
            nets = [c.fact.net_r for c in members]
            gaps = [c.gap_minutes for c in members]
            sum_net = sum(nets)
            rows.append(
                CascadeRow(
                    arm=arm,
                    segment=segment,
                    zone_id=zone_id,
                    group=group,
                    num_trades=len(members),
                    num_classified=total,
                    share_of_classified=(len(members) / total * 100.0) if total else 0.0,
                    stop_rate=_mean([100.0 if c.fact.is_stop else 0.0 for c in members]),
                    mean_net_r=_mean(nets),
                    sum_net_r=sum_net,
                    # 🚨 분모가 양수면 비율을 내지 않는다(WAN-115 부호 함정).
                    share_of_net_total=_share_of_net_total(sum_net, net_total),
                    same_minute_share=_mean([100.0 if g <= 0.0 else 0.0 for g in gaps]),
                    median_gap_minutes=statistics.median(gaps) if gaps else 0.0,
                    invalidation_bar_share=_mean(
                        [100.0 if c.fact.entry_after_invalidation else 0.0 for c in members]
                    ),
                    reentry_share=_mean([100.0 if c.fact.is_reentry else 0.0 for c in members]),
                )
            )
    return rows


def agreement_row(classified: Sequence[ClassifiedTrade], *, arm: str, segment: str) -> AgreementRow:
    """존 식별자 둘의 교차표 — 대리변수가 몇 건을 잘못 주웠나(완료기준 1)."""
    both = real_only = proxy_only = neither = 0
    for item in classified:
        real = item.group_real == GROUP_CASCADE
        proxy = item.group_proxy == GROUP_CASCADE
        if real and proxy:
            both += 1
        elif real:
            real_only += 1
        elif proxy:
            proxy_only += 1
        else:
            neither += 1
    return AgreementRow(
        arm=arm,
        segment=segment,
        num_classified=len(classified),
        both=both,
        real_only=real_only,
        proxy_only=proxy_only,
        neither=neither,
    )


def chain_rows(facts: Sequence[TradeFact], *, arm: str, segment: str) -> list[ChainRow]:
    """같은 존에서 **연달아 손절난** 사슬의 길이 분포.

    사슬 = 칸 안에서 「같은 존 · 직전도 손절」이 이어지는 최대 구간이고 길이는 그 안의 거래
    수다(2 이상). 완료기준 2의 「무효화 봉 안에서 몇 개가 체결될 수 있나」가 이 열이다.
    """
    by_cell: dict[tuple[str, str], list[TradeFact]] = {}
    for fact in facts:
        by_cell.setdefault(fact.cell, []).append(fact)
    counts: dict[tuple[str, int], int] = {}
    for zone_id, same in ((ZONE_ID_REAL, _same_zone_real), (ZONE_ID_PROXY, _same_zone_proxy)):
        for cell_facts in by_cell.values():
            ordered = sorted(cell_facts, key=lambda f: (f.entry_time, f.exit_time))
            run = 1
            for prev, cur in zip(ordered, ordered[1:], strict=False):
                if prev.is_stop and same(prev, cur):
                    run += 1
                    continue
                if run >= 2:
                    counts[(zone_id, run)] = counts.get((zone_id, run), 0) + 1
                run = 1
            if run >= 2:
                counts[(zone_id, run)] = counts.get((zone_id, run), 0) + 1
    return [
        ChainRow(
            arm=arm,
            segment=segment,
            zone_id=zone_id,
            chain_length=length,
            num_chains=num,
            num_trades=length * num,
        )
        for (zone_id, length), num in sorted(counts.items())
    ]


def timeframe_rows(
    classified: Sequence[ClassifiedTrade], *, arm: str, segment: str
) -> list[TimeframeRow]:
    rows: list[TimeframeRow] = []
    timeframes = sorted({c.fact.cell[1] for c in classified}, key=harness.DEFAULT_TIMEFRAMES.index)
    for zone_id in (ZONE_ID_REAL, ZONE_ID_PROXY):
        for timeframe in timeframes:
            members = [c for c in classified if c.fact.cell[1] == timeframe]
            cascade = [c for c in members if _group_of(c, zone_id) == GROUP_CASCADE]
            rows.append(
                TimeframeRow(
                    arm=arm,
                    segment=segment,
                    zone_id=zone_id,
                    timeframe=timeframe,
                    num_cascade=len(cascade),
                    num_classified=len(members),
                    share_of_classified=(len(cascade) / len(members) * 100.0 if members else 0.0),
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 격자 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ArmTables:
    census: list[CascadeRow]
    agreement: list[AgreementRow]
    chains: list[ChainRow]
    timeframes: list[TimeframeRow]


def build_arm(
    payloads: Sequence[CellPayload],
    *,
    arm: str,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
) -> ArmTables:
    census: list[CascadeRow] = []
    agreement: list[AgreementRow] = []
    chains: list[ChainRow] = []
    tf_rows: list[TimeframeRow] = []
    for book in place(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments):
        facts = trade_facts(book)
        classified = classify(facts)
        net_total = sum(f.net_r for f in facts)
        census.extend(census_rows(classified, arm=arm, segment=book.segment, net_total=net_total))
        agreement.append(agreement_row(classified, arm=arm, segment=book.segment))
        chains.extend(chain_rows(facts, arm=arm, segment=book.segment))
        tf_rows.extend(timeframe_rows(classified, arm=arm, segment=book.segment))
    return ArmTables(census=census, agreement=agreement, chains=chains, timeframes=tf_rows)


def run_checksum(
    payloads: Sequence[CellPayload],
    *,
    arm: str,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    cross_check: bool,
) -> list[ChecksumRow]:
    """검산 — (a) 기준 팔 ≡ 인자 없는 채택 북 · (b) 존 식별자가 실제로 붙어 있다.

    🚨 (a)는 **채택 좌표를 도는 기준 팔에서만** 뜻이 있다(WAN-381/397 관행) — 좁혀 돈 판을
    대조하면 좌표 차이가 배선 오류처럼 보인다. 성립하지 않는 실행에서는 대조하지 않고 그
    사실을 표에 찍는다(조용히 건너뛰지 않는다).
    """
    checks: list[ChecksumRow] = []
    left = {
        s.segment: s for s in place(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments)
    }
    if arm == ARM_BASE and cross_check:
        proxied, _note = apply_funding_proxy(payloads)
        right = {
            s.segment: s
            for s in iter_book_segments(
                proxied,
                book=LeverageBookParams(),
                segments=list(segments),
                start_ms=start_ms,
                end_ms=end_ms,
                include_reentry=True,
                min_stop_distance_fraction=ADOPTED_STOP_GUARD,
                compound_sizing=True,
                take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
            )
        }
        checks.extend(_compare_segments(left, right, check="(a) 기준 팔 ≡ 채택 북 인자"))

        # (c) 🚨 **다른 실행·다른 날의 같은 숫자** — 사용자가 올린 `book_trades.csv`(2026-09-04
        # 산출, 인자 없는 채택 북 `--oos-warm --trades`)의 집계와 대조한다. (a)는 이 모듈의
        # 래퍼가 채택 인자를 쓰는지만 보므로 그 자체로는 순환에 가깝다 — 이 줄이 그 밖에서
        # 온 값과 맞대 준다.
        primary = left.get(PRIMARY_SEGMENT)
        if primary is not None:
            facts = trade_facts(primary)
            checks.append(
                ChecksumRow(
                    check="(c) 기준 팔 ≡ 공개 book_trades.csv 집계",
                    segment=PRIMARY_SEGMENT,
                    metric="num_trades",
                    left=float(len(facts)),
                    right=float(PUBLISHED_OOS_WARM_TRADES),
                    abs_diff=abs(len(facts) - PUBLISHED_OOS_WARM_TRADES),
                )
            )
            mean_net = _mean([f.net_r for f in facts])
            checks.append(
                ChecksumRow(
                    check="(c) 기준 팔 ≡ 공개 book_trades.csv 집계",
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
                check="(a) 기준 팔 ≡ 채택 북 인자",
                segment="—",
                metric=(
                    "skipped_not_adopted_coordinates" if arm == ARM_BASE else "skipped_not_base_arm"
                ),
                left=0.0,
                right=0.0,
                abs_diff=0.0,
            )
        )

    # (b) 존 식별자가 라벨이 아니라 값으로 붙어 있는가 — `zone_key`가 `None`인 배치 거래 수.
    missing = 0
    total = 0
    for book in left.values():
        for fact in trade_facts(book):
            total += 1
            if fact.zone_key is None:
                missing += 1
    checks.append(
        ChecksumRow(
            check="(b) 존 식별자 누락 0건",
            segment="all",
            metric="trades_without_zone_key",
            left=float(missing),
            right=0.0,
            abs_diff=float(missing),
        )
    )
    return checks


# --------------------------------------------------------------------------- #
# 표 · 요약
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


RowT = TypeVar("RowT", bound=_ArmRow)


def _read(path: Path, model: type[RowT]) -> list[RowT]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [model(**record) for record in frame.to_dict(orient="records")]


def _merge(existing: Sequence[RowT], fresh: Sequence[RowT], *, arms: set[str]) -> list[RowT]:
    """같은 팔의 옛 행을 새 행으로 갈아 끼운다 — `--append`가 팔 단위로 안전하도록."""
    kept = [r for r in existing if r.arm not in arms]
    return [*kept, *fresh]


def _pick(
    rows: Sequence[CascadeRow], *, arm: str, segment: str, zone_id: str, group: str
) -> CascadeRow | None:
    for row in rows:
        if (row.arm, row.segment, row.zone_id, row.group) == (arm, segment, zone_id, group):
            return row
    return None


def build_summary_markdown(
    census: Sequence[CascadeRow],
    agreement: Sequence[AgreementRow],
    chains: Sequence[ChainRow],
    timeframes: Sequence[TimeframeRow],
    checks: Sequence[ArmChecksumRow],
    *,
    elapsed: float | None = None,
    num_cells: int | None = None,
) -> str:
    arms = [a for a in ARM_ORDER if any(r.arm == a for r in census)]
    segments = [s for s in SEGMENT_ORDER if any(r.segment == s for r in census)]
    lines: list[str] = [
        "# WAN-409 — 무효화 봉 안의 「같은 존 연속 체결」 인구조사",
        "",
        "채택 좌표(12종목 × 4TF 한 지갑 · 못 박은 6년 · 존폭 필터 끔 · 인과 취소 · 재진입 band ·"
        " cap_only 5배 · 복리 켬) · **핀 없음**. 재현: 모듈 독스트링.",
        "",
        f"팔: {', '.join(arms) if arms else '—'}"
        f" · 구간: {', '.join(segments) if segments else '—'}",
        "",
        "🚨 **판정 자는 「몇 건인가」가 아니라 「어느 코드 경로인가」다**(WAN-367)."
        " 이 표는 기본값을"
        " 하나도 안 바꾸고, `retap_mode`·`invalidation_cancel` 전환은 **재-베이스라인 = 사용자"
        " 결정**이다(WAN-404 · WAN-365 소관).",
        "",
    ]
    lines += ["| 팔 | 가진 구간 |", "| -- | -- |"]
    for arm in arms:
        owned = [s for s in SEGMENT_ORDER if any(r.arm == arm and r.segment == s for r in census)]
        lines.append(f"| {arm} | {', '.join(owned) if owned else '—'} |")
    lines.append("")
    if any(not any(r.arm == arm and r.segment == SEGMENT_IS for r in census) for arm in arms):
        lines += [
            "⚠️ **차가운 `is`/`oos` 구간이 없는 팔이 있다** — `--cold-segments` 없이 돈 팔은 차가운"
            " 절단 후보를 만들지 않는다(WAN-301 노브 · 셀 비용이 두 배다). 완료기준 5의 `is`는 그"
            " 플래그를 켠 팔만 낸다 — 없는 구간을 빈 값으로 위장하지 않는다.",
            "",
        ]

    lines += ["## §1 무더기 인구조사 (진짜 존 식별자)", ""]
    for segment in segments:
        lines += [
            f"### {segment}",
            "",
            "| 팔 | 부류 | 건수 | 비중 | 손절률 | 거래당 net R | net R 합 |"
            " 순손익 대비 | 같은 1분 | 무효화 봉 체결 |",
            "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: |",
        ]
        for arm in arms:
            for group in GROUP_ORDER:
                row = _pick(census, arm=arm, segment=segment, zone_id=ZONE_ID_REAL, group=group)
                if row is None:
                    continue
                share = "—" if row.share_of_net_total is None else f"{row.share_of_net_total:.1f}%"
                mark = "**" if group == GROUP_CASCADE else ""
                lines.append(
                    f"| {arm} | {mark}{group}{mark} | {row.num_trades:,} |"
                    f" {row.share_of_classified:.2f}% | {row.stop_rate:.1f}% |"
                    f" {row.mean_net_r:+.4f} | {row.sum_net_r:+,.1f}R | {share} |"
                    f" {row.same_minute_share:.1f}% | {row.invalidation_bar_share:.1f}% |"
                )
        lines.append("")

    lines += [
        "## §1 존 식별자 교체 — 진짜 대 대리변수(`손절가`)",
        "",
        "| 팔 | 구간 | 분류 거래 | 둘 다 | 진짜만 | **대리변수만** | 둘 다 아님 |",
        "| -- | -- | --: | --: | --: | --: | --: |",
    ]
    for agree_row in agreement:
        lines.append(
            f"| {agree_row.arm} | {agree_row.segment} | {agree_row.num_classified:,} |"
            f" {agree_row.both:,} | {agree_row.real_only:,} | **{agree_row.proxy_only:,}** |"
            f" {agree_row.neither:,} |"
        )
    lines += [
        "",
        "🚨 「대리변수만」 칸이 곧 **우연히 손절가가 같은 다른 존**이다 — 원 관측의 391에 그만큼이"
        " 섞여 있었다.",
        "",
    ]

    lines += [
        "## §2 사슬 길이 — 같은 존에서 연달아 몇 개가 체결되나 (진짜 식별자)",
        "",
        "| 팔 | 구간 | 사슬 길이 | 사슬 수 | 거래 수 |",
        "| -- | -- | --: | --: | --: |",
    ]
    for chain_row in chains:
        if chain_row.zone_id != ZONE_ID_REAL:
            continue
        lines.append(
            f"| {chain_row.arm} | {chain_row.segment} | {chain_row.chain_length} |"
            f" {chain_row.num_chains:,} | {chain_row.num_trades:,} |"
        )
    lines.append("")

    lines += [
        "## §1 TF 분포 (진짜 식별자 · 무더기)",
        "",
        "| 팔 | 구간 | TF | 무더기 | 분류 거래 | 비중 |",
        "| -- | -- | -- | --: | --: | --: |",
    ]
    for tf_row in timeframes:
        if tf_row.zone_id != ZONE_ID_REAL:
            continue
        lines.append(
            f"| {tf_row.arm} | {tf_row.segment} | {tf_row.timeframe} | {tf_row.num_cascade:,} |"
            f" {tf_row.num_classified:,} | {tf_row.share_of_classified:.2f}% |"
        )
    lines.append("")

    if checks:
        lines += [
            "## 검산",
            "",
            "| 검산 | 구간 | 지표 | 좌 | 우 | 차 |",
            "| -- | -- | -- | --: | --: | --: |",
        ]
        for check_row in checks:
            lines.append(
                f"| {check_row.check} | {check_row.segment} | {check_row.metric} |"
                f" {check_row.left:,.6f} | {check_row.right:,.6f} | {check_row.abs_diff:.2e} |"
            )
        lines.append("")

    lines += ["## 판정", ""]
    lines += _verdict_lines(census, agreement)
    lines += [
        "",
        "## 범위 밖 · 불변",
        "",
        "* **기본값·토대 불변** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()`"
        " 그대로 · 존폭 필터(WAN-159/384)·손절폭 가드(WAN-76/79)·익절 배수(WAN-81/90) **안"
        " 건드렸다** · 팔은 전부 옵트인이고 안 주면 비트 재현.",
        "* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *같은 셋업을 몇"
        " 번에 나눠 잡나*를 묻는다(**다른 질문**).",
        "* 전부 `baseline`(낙관) 렌즈 위 값이고 **체결 보수화(`pen_5bp`) 미측정** · 총수익 %는 이"
        " 좌표에서 복리 착시(WAN-169/213).",
        "* **라이브 파리티는 서버 몫**(`alphablock cascade` ·"
        " `scripts/wan409-server-cascade-census.sh`) — 로컬은 러너 장부가 비어 판정할 수 없다.",
        "* 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).",
        "",
    ]
    if elapsed is not None:
        cells = f" · {num_cells}칸" if num_cells is not None else ""
        lines.append(
            f"⏱️ 실측 {elapsed:,.0f}초{cells} — ⚠️ 다른 모듈의 셀 비용과 섞지 말 것(WAN-316)."
        )
        lines.append("")
    return "\n".join(lines)


def _verdict_lines(census: Sequence[CascadeRow], agreement: Sequence[AgreementRow]) -> list[str]:
    """판정 문장은 **코드가 낸다** — 사람이 표를 보고 정하지 않는다(WAN-388 관행)."""
    base = _pick(
        census, arm=ARM_BASE, segment=PRIMARY_SEGMENT, zone_id=ZONE_ID_REAL, group=GROUP_CASCADE
    )
    if base is None:
        return ["기준 팔의 주 구간 행이 없어 판정하지 않는다(지어내지 않는다)."]
    lines = [
        f"📌 **§1 — 진짜 존 식별자로 세면 주 구간(`{PRIMARY_SEGMENT}`) 무더기는 "
        f"{base.num_trades:,}건({base.share_of_classified:.2f}%)이고 손절률 {base.stop_rate:.1f}% ·"
        f" 거래당 net R {base.mean_net_r:+.4f}다.**",
    ]
    proxy = _pick(
        census, arm=ARM_BASE, segment=PRIMARY_SEGMENT, zone_id=ZONE_ID_PROXY, group=GROUP_CASCADE
    )
    agree = next((a for a in agreement if a.arm == ARM_BASE and a.segment == PRIMARY_SEGMENT), None)
    if proxy is not None and agree is not None:
        lines.append(
            f"📌 대리변수(`손절가`)로 세면 {proxy.num_trades:,}건이다 — 차이는 「대리변수만」"
            f" {agree.proxy_only:,}건 · 「진짜만」 {agree.real_only:,}건이다."
        )
    lines.append(
        f"📌 **§2 코드 경로 — 그 무더기의 {base.invalidation_bar_share:.1f}%가 체결 시각이 존"
        f" `break_time` 이후다**(= 무효화 봉 안에서 체결 · WAN-364 관측 필드). 인과 취소"
        "(WAN-365)가 그 봉의 대기 지정가를 살려 두는 **설계대로의 결과**인지, 그 위에 재탭이"
        " 후보를 여러 개 만든 몫인지는 아래 반사실 팔이 가른다."
    )
    for arm, label in (
        (ARM_RETAP_ONCE, '`retap_mode="once"`'),
        (ARM_CANCEL_BAR_OPEN, '`invalidation_cancel="bar_open"`'),
    ):
        row = _pick(
            census, arm=arm, segment=PRIMARY_SEGMENT, zone_id=ZONE_ID_REAL, group=GROUP_CASCADE
        )
        if row is None:
            lines.append(f"⚠️ 반사실 팔 {label}은 **안 쟀다** — 지어내지 않는다.")
            continue
        lines.append(
            f"📌 반사실 {label}: 무더기 {row.num_trades:,}건"
            f"({row.share_of_classified:.2f}%) · 거래당 net R {row.mean_net_r:+.4f}."
        )
    lines += [
        "",
        "🚨 **이 표는 「필터를 켜라」로 읽지 않는다** — 무더기를 지우면 공유 자본·슬롯이 재배치돼"
        " 다른 숫자가 나오고(WAN-316), 팔 사이의 차이에는 「그 거래를 안 해서」와 「다른 거래를"
        " 대신 해서」가 섞여 있다. **판정(가/나/다)은 라이브 파리티(§3)가 낸다.**",
    ]
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-409 무효화 봉 같은 존 연속 체결 인구조사")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--arms", default=ARM_BASE, help=f"쉼표 구분 ({', '.join(ARM_ORDER)})")
    parser.add_argument("--cold-segments", action="store_true", help="차가운 is/oos도 만든다")
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 붙인다")
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    parser.add_argument("--pilot", action="store_true", help="한 칸 견적(첫 종목 4h)")
    parser.add_argument("--no-checksum", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="payload 디스크 캐시를 안 쓴다")
    args = parser.parse_args(argv)

    if args.from_csv:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(
            build_summary_markdown(
                _read(CENSUS_CSV_PATH, CascadeRow),
                _read(AGREEMENT_CSV_PATH, AgreementRow),
                _read(CHAIN_CSV_PATH, ChainRow),
                _read(TF_CSV_PATH, TimeframeRow),
                _read(CHECKSUM_CSV_PATH, ArmChecksumRow),
            ),
            encoding="utf-8",
        )
        print(f"요약 갱신: {SUMMARY_PATH}")
        return 0

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARM_ORDER]
    if unknown:
        parser.error(f"모르는 팔: {', '.join(unknown)} (가능: {', '.join(ARM_ORDER)})")
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    if args.pilot:
        symbols, timeframes = symbols[:1], ["4h"]
        print(f"[wan409] 파일럿 — {symbols[0]} 4h (⚠️ 이 값을 격자 견적으로 인용 금지)")

    segments = [SEGMENT_FULL, SEGMENT_OOS_WARM]
    if args.cold_segments:
        segments = list(SEGMENT_ORDER)
    cross_check = set(symbols) == set(harness.DEFAULT_SYMBOLS) and set(timeframes) == set(
        harness.DEFAULT_TIMEFRAMES
    )
    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)

    census = list(_read(CENSUS_CSV_PATH, CascadeRow)) if args.append else []
    agreement = list(_read(AGREEMENT_CSV_PATH, AgreementRow)) if args.append else []
    chains = list(_read(CHAIN_CSV_PATH, ChainRow)) if args.append else []
    tf_rows = list(_read(TF_CSV_PATH, TimeframeRow)) if args.append else []
    checks = list(_read(CHECKSUM_CSV_PATH, ArmChecksumRow)) if args.append else []

    started = time.monotonic()
    num_cells = 0
    fresh_checks: list[ArmChecksumRow] = []
    fresh = ArmTables(census=[], agreement=[], chains=[], timeframes=[])
    for arm in arms:
        arm_started = time.monotonic()
        payloads = build_payloads(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            arm=arm,
            cold_segments=args.cold_segments,
            cache=None if args.no_cache else PayloadCache(),
        )
        num_cells = len(payloads)
        print(
            f"[wan409] {arm} 후보 생성 {time.monotonic() - arm_started:,.0f}초 ({num_cells}칸)",
            flush=True,
        )
        tables = build_arm(payloads, arm=arm, start_ms=start_ms, end_ms=end_ms, segments=segments)
        fresh = ArmTables(
            census=[*fresh.census, *tables.census],
            agreement=[*fresh.agreement, *tables.agreement],
            chains=[*fresh.chains, *tables.chains],
            timeframes=[*fresh.timeframes, *tables.timeframes],
        )
        if not args.no_checksum:
            fresh_checks.extend(
                _with_arm(
                    run_checksum(
                        payloads,
                        arm=arm,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        segments=segments,
                        cross_check=cross_check,
                    ),
                    arm,
                )
            )
        print(f"[wan409] {arm} 완료 {time.monotonic() - arm_started:,.0f}초", flush=True)

    touched = set(arms)
    census = _merge(census, fresh.census, arms=touched)
    agreement = _merge(agreement, fresh.agreement, arms=touched)
    chains = _merge(chains, fresh.chains, arms=touched)
    tf_rows = _merge(tf_rows, fresh.timeframes, arms=touched)
    if not args.no_checksum:
        checks = _merge(checks, fresh_checks, arms=touched)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rows_to_frame(census).to_csv(CENSUS_CSV_PATH, index=False)
    rows_to_frame(agreement).to_csv(AGREEMENT_CSV_PATH, index=False)
    rows_to_frame(chains).to_csv(CHAIN_CSV_PATH, index=False)
    rows_to_frame(tf_rows).to_csv(TF_CSV_PATH, index=False)
    if checks:
        rows_to_frame(checks).to_csv(CHECKSUM_CSV_PATH, index=False)

    elapsed = time.monotonic() - started
    SUMMARY_PATH.write_text(
        build_summary_markdown(
            census, agreement, chains, tf_rows, checks, elapsed=elapsed, num_cells=num_cells
        ),
        encoding="utf-8",
    )
    print(f"[wan409] 완료 {elapsed:,.0f}초 → {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
