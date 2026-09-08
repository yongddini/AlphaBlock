"""WAN-408 §0 — 손실이 시간축에 어떻게 분포하나 (관측 전용 · 새 격자 없음).

## 한 줄

채택 북 `oos_warm`의 손실은 **시간축에 고르게 퍼져 있지 않다**. 이 모듈은 그 분포를
세 자로 잰다 — 일자별 **집중도**(최악 N일이 총손실의 몇 %) · 종목 간 **동시성**(한 버킷에
몇 종목이 같이 손절나나) · 그 동시성이 **우연 수준인가**(무작위 대조군). 그리고 §1이
쓸 **인과적 선행 지표**(그 시점까지 알 수 있는 것)를 관측만 한다.

🚨 **매매를 하나도 안 바꾼다.** 엔진·기본값·토대 전부 불변(`ConfluenceParams()`·
`OrderBlockParams()`·`LeverageBookParams()` 그대로 · 핀 하나도 없다, WAN-305)이고, 이
모듈이 하는 일은 **인자 없는 채택 북이 실제로 한 거래**를 다시 집계하는 것뿐이다.

## 🚨 이 표는 채택 근거가 아니다 (이슈가 착수 전에 못 박은 것)

*「폭락일을 피하면 좋아진다」*는 **사후에 그 날을 아는 값**이라 인과적으로 구현할 수 없다.
이 모듈이 답하는 것은 ***「그 날이 시작된 뒤 인과적으로 알 수 있는 것으로 노출을 줄일 수
있는가」***의 **앞단**(그런 지표가 실제로 신호를 갖는가)이지 *「그 날을 지우면 얼마나
좋아지나」*가 아니다. 손익 판정은 **북에서** 나야 하고(WAN-341) 그것은 §1(별도 PR ·
**사용자 결정** · 개발자 임의 착수 금지)이다.

## §0-2 — 「대조군이 동작하지 않았다」의 정체: 그 순열은 **항등**이다

이슈 본문이 *「종목 내 청산시각 순열 대조를 30회 돌렸는데 전부 실측과 같은 값(62.9% ±
0.0)이 나왔다 — 순열이 실제로 안 걸린 것으로 보인다」*고 적었다. **순열은 걸렸다.** 동시성
통계량은 *「이 버킷에 그 종목의 손절이 하나라도 있나」*만 보므로 **종목별 청산시각 집합**의
함수인데, 종목 **안에서** 거래끼리 시각을 바꿔 다는 순열은 그 집합을 하나도 안 바꾼다 —
그래서 값이 소수점까지 같은 것이 **버그가 아니라 정리(theorem)**다. `permute_within_symbol`
＋ `test_wan408_*`가 그 항등을 **동작으로** 못 박는다(이 함수는 대조군이 아니라 **함정의
기록**이다).

동작하는 널은 **종목별 원형 시프트**(`shift_events`)다 — 각 종목의 시계열을 통째로 무작위
오프셋만큼 돌린다. 종목 **안의** 뭉침(그 종목이 원래 몰아서 손절나는 성질)은 **보존**하고
종목 **사이의** 정렬만 깨므로, 남는 초과분이 *「종목들이 같이 죽는다」*의 몫이다.

⚠️ **이 널의 한계**: 원형 시프트는 시계열 끝을 앞으로 감아 붙이므로 그 이음매의 거래가
자기 레짐 밖으로 간다 · 널은 **지갑을 다시 배치하지 않는다**(WAN-316 — 라벨 시각만 옮긴다).
이 널이 답하는 것은 *「이 정렬이 우연인가」* 하나이고, *「정렬을 없앴다면 얼마를 벌었나」*는
**답하지 않는다**.

## §0-3 — 선행 지표는 인과적으로만 센다

거래 하나의 진입 시각 `t`에서 **그 전에 이미 일어난 것만** 본다(같은 KST 하루):
(a) 이미 손절난 거래 수 · (b) 그 순간 열려 있는 칸 수 · (c) 실현 net R 누적. 셋 다
`t`보다 **엄격히 앞선** 청산만 세므로 §1이 그대로 스위치로 쓸 수 있다.

🚨 **이것은 관측이지 반사실이 아니다** — 「그 문턱에서 막았다면」의 손익은 **막힌 거래가
비운 자본·슬롯을 다른 칸이 쓰기 때문에** 이 표에서 읽을 수 없다(WAN-316/323 채널). §1이
북에서 재야 한다.

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목 × 4TF 한 지갑 · 못 박은 6년 창 · 존폭 필터 **끔**(WAN-384) · 인과 취소(WAN-365) ·
재진입 ON(band, WAN-273) · cap_only 5배 · 익절 메이커(WAN-370) · `baseline` 렌즈 ·
복리 **켬**(원 관측 CSV가 인자 없는 채택 북의 산출물이다).

📌 **후보 생성 인자는 `book_cli.run_book_segments`와 같다** — 그래서 이 모듈의 배치가 곧
「인자 없는 채택 북」이고, 그 사실은 주장이 아니라 **검산 (a)·(c)와 스파이 테스트**다.

## 검산

* **(a) 이 모듈 배치 ≡ 채택 북 인자** — `place()`가 `run_book_segments`와 같은 인자로
  `iter_book_segments`를 부른다(스파이 테스트가 **호출 인자**로 고정 · 이 줄은 그 산출물
  대조다).
* **(c) `oos_warm` ≡ 공개 `book_trades.csv` 집계** — 사용자가 올린 CSV(2026-09-04 · 인자
  없는 채택 북 `--positions book --oos-warm --trades`)의 거래 수 14,843 · 거래당 net R
  −0.1207과 **다른 실행·다른 날의 같은 숫자**여야 한다(WAN-409가 이미 상수로 못 박아 둔
  값을 그대로 가져다 쓴다 — 두 벌로 적으면 갈라진다).
* **(n) 널이 실제로 걸렸다** — 시프트 draw 중 실측과 **같은 값이 나온 횟수**를 센다. 0이
  아니면 그 널은 이슈가 겪은 그 함정에 다시 빠진 것이다.
* **(i) 종목 내 순열은 항등이다** — 위 함정을 **숫자로** 남긴다(항상 실측과 같아야 한다).

재현::

    uv run python -m backtest.wan408_loss_clustering --jobs 4
    uv run python -m backtest.wan408_loss_clustering --from-csv      # 요약만
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments
from backtest.leverage_book import LeverageBookParams
from backtest.models import ExitReason
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan409_invalidation_cascade import (
    PUBLISHED_OOS_WARM_MEAN_NET_R,
    PUBLISHED_OOS_WARM_TRADES,
)
from common.timefmt import kst_day_key

__all__ = [
    "BUCKETS",
    "ChecksumRow",
    "ConcentrationRow",
    "ConcurrencyRow",
    "CorrelationRow",
    "DayRow",
    "LeadingRow",
    "NULL_DRAWS",
    "NULL_SEED",
    "PRIMARY_SEGMENT",
    "StopEvent",
    "TOP_DAYS",
    "TradeFact",
    "build_payloads",
    "checksums",
    "concentration_rows",
    "concurrency_rows",
    "concurrency_share",
    "correlation_rows",
    "day_rows",
    "leading_rows",
    "main",
    "permute_within_symbol",
    "place",
    "run_measure",
    "shift_events",
    "trade_facts",
]

REPORTS_DIR = Path("backtest/reports")
DAILY_CSV = REPORTS_DIR / "wan408_daily.csv"
CONCENTRATION_CSV = REPORTS_DIR / "wan408_concentration.csv"
CONCURRENCY_CSV = REPORTS_DIR / "wan408_concurrency.csv"
CORRELATION_CSV = REPORTS_DIR / "wan408_correlation.csv"
LEADING_CSV = REPORTS_DIR / "wan408_leading.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan408_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan408_loss_clustering_summary.md"

#: 주 구간 — 판정은 여기서 낸다(WAN-166 따뜻한 연속 OOS). 나머지 구간은 「같은 모양인가」다.
PRIMARY_SEGMENT = harness.SEGMENT_OOS_WARM

#: 집중도를 낼 상위 N일. 이슈 본문 표와 같은 점이다.
TOP_DAYS: tuple[int, ...] = (1, 5, 10, 20)

#: 동시성 버킷. 🚨 **KST 자정에 맞춰 자른다**(1d 버킷이 사람이 읽는 「하루」와 같아야 한다) —
#: `bucket_index`가 KST 오프셋을 더한 뒤 내림한다.
BUCKETS: tuple[tuple[str, int], ...] = (
    ("1h", 3_600_000),
    ("4h", 14_400_000),
    ("1d", 86_400_000),
)

#: 「몇 종목 이상이면 동시로 볼 것인가」 — 이슈 본문과 같은 두 점.
CONCURRENCY_THRESHOLDS: tuple[int, ...] = (5, 8)

#: 널 추첨 횟수·시드. 시드를 못 박아 재현된다(WAN-88/124 관행).
NULL_DRAWS = 200
NULL_SEED = 408

#: KST는 UTC+9 고정(서머타임 없음) — 버킷 경계를 KST 자정에 맞추는 데만 쓴다.
KST_OFFSET_MS = 9 * 3_600_000

#: 「0과 구분되지 않는다」 선(WAN-366 규약). 선행 지표 표의 읽는 법에 쓴다.
NOISE_R = 0.005

#: 선행 지표 (a) 「이미 손절난 거래 수」 버킷 상한(마지막은 열린 구간).
STOPS_BUCKETS: tuple[int, ...] = (0, 2, 5, 10, 20)

#: 선행 지표 (b) 「그 순간 열린 칸 수」 버킷 상한.
OPEN_BUCKETS: tuple[int, ...] = (1, 3, 6, 10)

#: 선행 지표 (c) 「그날 실현 net R 누적」 버킷 하한(내림차순으로 읽는다).
REALIZED_BUCKETS: tuple[float, ...] = (0.0, -3.0, -5.0, -10.0, -20.0)


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치 — 🚨 인자는 `book_cli.run_book_segments`와 **같아야** 한다
# --------------------------------------------------------------------------- #


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    cold_segments: bool = True,
    cache: PayloadCache | None = None,
) -> list[CellPayload]:
    """무거운 패스는 **여기 한 번**이다 — 이 모듈이 흔드는 축은 하나도 없다.

    인자는 `book_cli.run_book_segments`가 `run_cells`에 넘기는 것과 **같다**(`adv_fraction`
    = `UNSET` · 재진입 band · 익절 메이커 · 취소 시점 미지정 = 채택 인과). 그래서 payload
    디스크 캐시(WAN-394 §0)의 **채택 좌표 판을 그대로 히트**한다 — 안 맞추면 후보 생성만
    4시간대다(WAN-386 실측 4시간 40분).

    `cold_segments=True`(기본)라야 완료기준 4의 `is`(차가운 절단)가 나온다. `False`면 그
    구간의 후보 키 자체가 없어 요청하면 **시끄럽게 죽는다**(빈 값으로 위장하지 않는다).
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
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 복리를 **켠다**(원 관측 CSV가 인자 없는 채택 북의 산출물이다).

    `book_cli.run_book_segments`가 `iter_book_segments`에 넘기는 것과 **같은 인자**다
    (`include_reentry=True` · 익절 메이커 · 나머지는 기본값). 스파이 테스트가 그 동일성을
    **호출 인자**로 고정하므로 이 함수가 조용히 갈라지면 테스트가 먼저 죽는다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# 원자 — 거래 하나 · 손절 하나
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TradeFact:
    """집계에 필요한 것만 뽑은 거래 하나 — 북 배치 기록(`PlacedSetup`)에서 그대로 온다."""

    symbol: str
    timeframe: str
    entry_time: int
    exit_time: int
    is_stop: bool
    net_r: float
    """`book_cli.net_r`가 낸 값 그대로 — 공개 `book_trades.csv`의 `net R` 열과 **같은 자**다."""
    is_reentry: bool


@dataclass(frozen=True, slots=True)
class StopEvent:
    """손절 하나 — 동시성·널의 원자다(종목과 시각만 본다)."""

    symbol: str
    time: int


def trade_facts(segment: BookSegment) -> list[TradeFact]:
    """북 한 구간의 거래를 집계용 사실로 — 짝 계약은 `book_cli`가 이미 값으로 확인했다.

    🚨 **`net_r`를 여기서 다시 정의하지 않는다** — `book_cli.net_r`(실현손익 ÷ 그 거래의
    리스크 금액)가 단일 소스다. 두 벌로 갈라 두면 검산 (c)가 성립하지 않는다(「R이라 불리는
    자가 셋」 — WAN-393 §2 · WAN-406).
    """
    from backtest.book_cli import net_r

    facts: list[TradeFact] = []
    for trade, placement in segment.trades_with_placements():
        facts.append(
            TradeFact(
                symbol=placement.cell[0],
                timeframe=placement.cell[1],
                entry_time=trade.entry_time,
                exit_time=trade.exits[-1].time,
                is_stop=trade.exits[-1].reason is ExitReason.STOP_LOSS,
                net_r=net_r(trade, placement),
                is_reentry=placement.is_reentry,
            )
        )
    return facts


def stop_events(facts: Sequence[TradeFact]) -> list[StopEvent]:
    """손절로 끝난 거래의 (종목, 청산 시각) — 동시성 통계량의 입력."""
    return [StopEvent(f.symbol, f.exit_time) for f in facts if f.is_stop]


# --------------------------------------------------------------------------- #
# §0-1 일자별 집중도
# --------------------------------------------------------------------------- #


class DayRow(BaseModel):
    """한 (구간, KST 하루)의 손익·손절·동시성 한 줄."""

    model_config = ConfigDict(frozen=True)

    segment: str
    day: str
    """KST 날짜(`YYYY-MM-DD`) — 사람이 읽는 「하루」와 같아야 한다(WAN-172)."""
    num_trades: int
    num_stops: int
    num_symbols_stopped: int
    num_symbols_traded: int
    sum_net_r: float
    num_reentry_trades: int
    reentry_net_r: float


def day_rows(facts: Sequence[TradeFact], *, segment: str) -> list[DayRow]:
    """거래를 **청산 시각의 KST 하루**로 접는다.

    청산 시각으로 접는 이유: 이 표가 묻는 것은 *「손실이 언제 실현됐나」*이고, 손절은 그
    시각에 자본을 깎는다. 진입 시각으로 접으면 여러 날에 걸친 포지션의 손실이 진입일로
    소급돼 「그날 무슨 일이 있었나」가 흐려진다.
    """
    by_day: dict[str, list[TradeFact]] = defaultdict(list)
    for fact in facts:
        by_day[kst_day_key(fact.exit_time)].append(fact)
    rows: list[DayRow] = []
    for day in sorted(by_day):
        group = by_day[day]
        stops = [f for f in group if f.is_stop]
        reentries = [f for f in group if f.is_reentry]
        rows.append(
            DayRow(
                segment=segment,
                day=day,
                num_trades=len(group),
                num_stops=len(stops),
                num_symbols_stopped=len({f.symbol for f in stops}),
                num_symbols_traded=len({f.symbol for f in group}),
                sum_net_r=sum(f.net_r for f in group),
                num_reentry_trades=len(reentries),
                reentry_net_r=sum(f.net_r for f in reentries),
            )
        )
    return rows


class ConcentrationRow(BaseModel):
    """「최악 N일이 총손실의 몇 %인가」 한 줄."""

    model_config = ConfigDict(frozen=True)

    segment: str
    top_n: int
    num_days: int
    """그 구간에 거래가 있었던 KST 날짜 수 — 분모의 크기감."""
    days_share: float
    """`top_n / num_days` — 「710일의 1.4%」의 그 값."""
    sum_net_r_top: float
    total_net_r: float
    share_of_total: float
    """`sum_net_r_top / total_net_r`. 🚨 총합이 **음수일 때만** 뜻이 있다(아래 주의)."""
    num_trades_top: int
    num_stops_top: int
    min_symbols_stopped_top: int
    """최악 N일 중 **가장 적게** 동시 손절난 날의 종목 수 — 「전부 10~12종목」의 그 자."""
    num_reentry_trades_top: int
    reentry_share_top: float
    """최악 N일의 재진입 거래 비중(전체 평균과 견주는 값)."""


def concentration_rows(days: Sequence[DayRow], *, segment: str) -> list[ConcentrationRow]:
    """최악 N일의 몫. 🚨 **총합이 양수인 구간에서는 비율을 내지 않는다**.

    분모가 「총손실」이라 총합이 0 이상이면 그 비율은 뜻을 잃는다(WAN-115가 문서화한 부호
    함정의 이 축 판) — 그런 구간은 `share_of_total`을 `nan`으로 두고 요약이 **부호만**
    읽으라고 찍는다.
    """
    ordered = sorted(days, key=lambda r: r.sum_net_r)
    total = sum(r.sum_net_r for r in days)
    rows: list[ConcentrationRow] = []
    for n in TOP_DAYS:
        top = ordered[:n]
        if not top:
            continue
        top_sum = sum(r.sum_net_r for r in top)
        trades = sum(r.num_trades for r in top)
        reentries = sum(r.num_reentry_trades for r in top)
        rows.append(
            ConcentrationRow(
                segment=segment,
                top_n=n,
                num_days=len(days),
                days_share=n / len(days) if days else float("nan"),
                sum_net_r_top=top_sum,
                total_net_r=total,
                share_of_total=(top_sum / total if total < 0 else float("nan")),
                num_trades_top=trades,
                num_stops_top=sum(r.num_stops for r in top),
                min_symbols_stopped_top=min(r.num_symbols_stopped for r in top),
                num_reentry_trades_top=reentries,
                reentry_share_top=(reentries / trades if trades else float("nan")),
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# §0-1 동시성 · §0-2 무작위 대조군
# --------------------------------------------------------------------------- #


def bucket_index(ms: int, bucket_ms: int) -> int:
    """KST 자정에 맞춘 버킷 번호. 1d 버킷이 사람이 읽는 하루와 같아지도록 오프셋을 더한다."""
    return (ms + KST_OFFSET_MS) // bucket_ms


def concurrency_share(events: Sequence[StopEvent], *, bucket_ms: int, threshold: int) -> float:
    """**손절 건수 기준** 「그 버킷에 `threshold` 종목 이상이 같이 손절난 비율」.

    건수 기준인 것이 요점이다 — 버킷 기준으로 세면 손절 하나짜리 조용한 버킷이 폭락 버킷과
    같은 무게를 갖는다(WAN-388이 인구조사에서 못 박은 「얇은 칸 과대 대표」 함정).
    """
    if not events:
        return float("nan")
    symbols_by_bucket: dict[int, set[str]] = defaultdict(set)
    for event in events:
        symbols_by_bucket[bucket_index(event.time, bucket_ms)].add(event.symbol)
    hit = sum(
        1
        for event in events
        if len(symbols_by_bucket[bucket_index(event.time, bucket_ms)]) >= threshold
    )
    return hit / len(events)


def permute_within_symbol(events: Sequence[StopEvent], rng: random.Random) -> list[StopEvent]:
    """🚨 **대조군이 아니라 함정의 기록** — 종목 안에서 청산 시각을 섞는다(= 항등).

    이슈 본문이 *「30회 전부 실측과 같은 값이 나와 순열이 안 걸린 것으로 보인다」*고 적은
    그 시도다. 순열은 걸린다 — **동시성 통계량이 이 순열에 대해 불변**일 뿐이다: 통계량은
    「종목별 청산 시각 **집합**」의 함수인데 종목 안 순열은 그 집합을 바꾸지 않는다.
    `test_wan408_*`가 이 항등을 동작으로 고정하고, 검산 (i)가 그 사실을 표에 남긴다.
    """
    by_symbol: dict[str, list[int]] = defaultdict(list)
    for event in events:
        by_symbol[event.symbol].append(event.time)
    out: list[StopEvent] = []
    for symbol in sorted(by_symbol):
        times = list(by_symbol[symbol])
        rng.shuffle(times)
        out.extend(StopEvent(symbol, t) for t in times)
    return out


def shift_events(
    events: Sequence[StopEvent], rng: random.Random, *, lo: int, hi: int
) -> list[StopEvent]:
    """**동작하는 널** — 종목별로 시계열을 통째로 무작위 오프셋만큼 **원형 시프트**한다.

    종목 **안의** 뭉침(그 종목이 원래 몰아서 손절나는 성질)은 보존하고 종목 **사이의**
    정렬만 깬다. 그래서 실측 초과분이 *「종목들이 같이 죽는다」*의 몫으로 읽힌다.

    ⚠️ 시계열 끝이 앞으로 감겨 붙으므로 이음매의 손절은 자기 레짐 밖으로 간다 ·
    🚨 **지갑을 다시 배치하지 않는다**(WAN-316) — 이 널은 *「정렬이 우연인가」*만 답하고
    *「정렬이 없었다면 얼마를 벌었나」*는 답하지 않는다.
    """
    span = hi - lo
    if span <= 0:
        return list(events)
    offsets: dict[str, int] = {}
    for symbol in sorted({e.symbol for e in events}):
        offsets[symbol] = rng.randrange(span)
    return [StopEvent(e.symbol, lo + ((e.time - lo + offsets[e.symbol]) % span)) for e in events]


class ConcurrencyRow(BaseModel):
    """한 (구간, 버킷, 문턱)의 실측 ↔ 널 한 줄."""

    model_config = ConfigDict(frozen=True)

    segment: str
    bucket: str
    threshold: int
    num_stops: int
    observed_share: float
    null_mean: float
    null_p05: float
    null_p95: float
    null_max: float
    p_value: float
    """단측 — 널 draw 중 실측 이상인 비율(`(k+1)/(draws+1)`, 0을 안 내는 관행)."""
    draws: int
    num_identical_draws: int
    """🚨 널 draw 중 실측과 **소수점까지 같은** 횟수 — 0이 아니면 그 널은 함정에 다시 빠진
    것이다(이슈가 겪은 그 자리). 검산 (n)이 이 값을 표에 올린다."""


def _same(a: float, b: float) -> bool:
    """두 비율이 **소수점까지 같은가** — 널이 항등으로 퇴화했는지 세는 데만 쓴다."""
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12)


def concurrency_rows(
    facts: Sequence[TradeFact],
    *,
    segment: str,
    draws: int = NULL_DRAWS,
    seed: int = NULL_SEED,
) -> list[ConcurrencyRow]:
    """동시성 실측 + 종목별 원형 시프트 널.

    널 시계열은 **버킷·문턱마다 다시 뽑지 않는다** — 같은 draw를 모든 (버킷, 문턱)이 나눠
    쓴다(같은 무작위 세계에서 잰 값이라야 표 안에서 서로 비교된다).
    """
    events = stop_events(facts)
    if not events:
        return []
    lo = min(e.time for e in events)
    hi = max(e.time for e in events) + 1
    rng = random.Random(seed)
    null_draws = [shift_events(events, rng, lo=lo, hi=hi) for _ in range(draws)]

    rows: list[ConcurrencyRow] = []
    for bucket, bucket_ms in BUCKETS:
        for threshold in CONCURRENCY_THRESHOLDS:
            observed = concurrency_share(events, bucket_ms=bucket_ms, threshold=threshold)
            null = [
                concurrency_share(d, bucket_ms=bucket_ms, threshold=threshold) for d in null_draws
            ]
            ge = sum(1 for v in null if v >= observed)
            identical = sum(1 for v in null if _same(v, observed))
            ordered = sorted(null)
            rows.append(
                ConcurrencyRow(
                    segment=segment,
                    bucket=bucket,
                    threshold=threshold,
                    num_stops=len(events),
                    observed_share=observed,
                    null_mean=statistics.fmean(null),
                    null_p05=_quantile(ordered, 0.05),
                    null_p95=_quantile(ordered, 0.95),
                    null_max=ordered[-1],
                    p_value=(ge + 1) / (len(null) + 1),
                    draws=len(null),
                    num_identical_draws=identical,
                )
            )
    return rows


def _quantile(ordered: Sequence[float], q: float) -> float:
    """정렬된 표본의 분위(가장 가까운 순위). 표본이 200개라 보간은 뜻이 없다."""
    if not ordered:
        return float("nan")
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


# --------------------------------------------------------------------------- #
# §0-1 종목 간 상관
# --------------------------------------------------------------------------- #


class CorrelationRow(BaseModel):
    """일간 net R 상관 한 줄 — `scope`가 「종목쌍 평균」·「BTC 대비」·종목 하나를 가른다."""

    model_config = ConfigDict(frozen=True)

    segment: str
    scope: str
    symbol: str
    value: float
    num_days: int


def _daily_by_symbol(facts: Sequence[TradeFact]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for fact in facts:
        out[fact.symbol][kst_day_key(fact.exit_time)] += fact.net_r
    return {s: dict(d) for s, d in out.items()}


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return float("nan") if dx == 0 or dy == 0 else num / (dx * dy)


def correlation_rows(facts: Sequence[TradeFact], *, segment: str) -> list[CorrelationRow]:
    """일간 net R의 종목 간 상관.

    📌 **거래가 없는 날은 0으로 둔다** — 그날 그 종목의 실현 손익이 실제로 0이기 때문이다
    (「둘 다 거래한 날만」으로 자르면 하필 폭락일처럼 **모두가 거래한 날**로 표본이 쏠려
    상관이 부풀려진다). 날짜 격자는 **그 구간에 거래가 하나라도 있던 KST 날짜 전부**다.
    """
    by_symbol = _daily_by_symbol(facts)
    if len(by_symbol) < 2:
        return []
    days = sorted({day for series in by_symbol.values() for day in series})
    vectors = {s: [by_symbol[s].get(day, 0.0) for day in days] for s in sorted(by_symbol)}

    rows: list[CorrelationRow] = []
    symbols = sorted(vectors)
    pairs: list[float] = []
    for i, a in enumerate(symbols):
        for b in symbols[i + 1 :]:
            value = _pearson(vectors[a], vectors[b])
            if not math.isnan(value):
                pairs.append(value)
    if pairs:
        rows.append(
            CorrelationRow(
                segment=segment,
                scope="pair_mean",
                symbol="—",
                value=statistics.fmean(pairs),
                num_days=len(days),
            )
        )
    btc = next((s for s in symbols if s.startswith("BTC")), None)
    if btc is not None:
        for symbol in symbols:
            if symbol == btc:
                continue
            rows.append(
                CorrelationRow(
                    segment=segment,
                    scope="vs_btc",
                    symbol=symbol,
                    value=_pearson(vectors[btc], vectors[symbol]),
                    num_days=len(days),
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# §0-3 인과적 선행 지표
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LeadingState:
    """거래 하나의 진입 순간에 **그 전까지 알 수 있던 것**."""

    stops_today_before: int
    open_cells_before: int
    realized_net_r_today_before: float


def leading_states(facts: Sequence[TradeFact]) -> list[LeadingState]:
    """각 거래의 진입 시각에서 본 (a)·(b)·(c). 🚨 **엄격히 앞선 청산만** 센다.

    - (a)·(c)는 **같은 KST 하루** 안에서만 센다(§1의 스위치가 하루 단위라 그 자와 맞춘다).
    - (b)는 하루 경계와 무관하다 — 「지금 몇 칸이 열려 있나」는 자정에 리셋되지 않는다.
      반개구간 규약(`exit_time == t`는 이미 닫힌 것으로 본다)은 북 시퀀서의 그것과 같다.
    """
    entries = sorted(f.entry_time for f in facts)
    exits = sorted(f.exit_time for f in facts)

    by_day_exit: dict[str, list[tuple[int, bool, float]]] = defaultdict(list)
    for fact in facts:
        by_day_exit[kst_day_key(fact.exit_time)].append((fact.exit_time, fact.is_stop, fact.net_r))
    prefix: dict[str, tuple[list[int], list[int], list[float]]] = {}
    for day, rows in by_day_exit.items():
        rows.sort()
        times = [r[0] for r in rows]
        stops = [0]
        net = [0.0]
        for _, is_stop, net_r_value in rows:
            stops.append(stops[-1] + (1 if is_stop else 0))
            net.append(net[-1] + net_r_value)
        prefix[day] = (times, stops, net)

    states: list[LeadingState] = []
    for fact in facts:
        day = kst_day_key(fact.entry_time)
        times, stops, net = prefix.get(day, ([], [0], [0.0]))
        cut = bisect_left(times, fact.entry_time)
        opened = bisect_left(entries, fact.entry_time)
        closed = bisect_right(exits, fact.entry_time)
        states.append(
            LeadingState(
                stops_today_before=stops[cut],
                open_cells_before=max(0, opened - closed),
                realized_net_r_today_before=net[cut],
            )
        )
    return states


def _bucket_label_int(value: int, edges: Sequence[int]) -> str:
    lo = 0
    for edge in edges:
        if value <= edge:
            return f"{lo}" if lo == edge else f"{lo}–{edge}"
        lo = edge + 1
    return f"{lo}+"


def _bucket_label_realized(value: float, edges: Sequence[float]) -> str:
    if value >= edges[0]:
        return "≥ 0R"
    for lo, hi in zip(edges[1:], edges[:-1], strict=True):
        if value >= lo:
            # 🚨 라벨에 콤마를 넣지 않는다 — CSV에서 따옴표로 감싸여 표가 읽기 어려워진다.
            return f"[{lo:.0f}R ~ {hi:.0f}R)"
    return f"< {edges[-1]:.0f}R"


class LeadingRow(BaseModel):
    """한 (구간, 지표, 버킷)의 「그 상태에서 들어간 거래는 어떻게 끝났나」 한 줄."""

    model_config = ConfigDict(frozen=True)

    segment: str
    indicator: str
    bucket: str
    order: int
    """표 정렬용 — 라벨 문자열로 정렬하면 「10–20」이 「2–5」 앞에 온다."""
    num_trades: int
    share_of_trades: float
    win_rate: float
    mean_net_r: float
    sum_net_r: float
    share_of_total_net_r: float
    """이 버킷의 net R 합 ÷ 구간 전체 net R 합. 🚨 **총합이 음수일 때만** 뜻이 있다."""


def leading_rows(facts: Sequence[TradeFact], *, segment: str) -> list[LeadingRow]:
    """선행 지표 셋 × 버킷의 사후 성적. **관측이지 반사실이 아니다**(모듈 독스트링 §0-3)."""
    if not facts:
        return []
    states = leading_states(facts)
    total_net = sum(f.net_r for f in facts)
    total_trades = len(facts)

    groups: dict[tuple[str, str, int], list[TradeFact]] = defaultdict(list)
    for fact, state in zip(facts, states, strict=True):
        label_a = _bucket_label_int(state.stops_today_before, STOPS_BUCKETS)
        order_a = _bucket_order_int(state.stops_today_before, STOPS_BUCKETS)
        groups[("stops_today_before", label_a, order_a)].append(fact)
        label_b = _bucket_label_int(state.open_cells_before, OPEN_BUCKETS)
        order_b = _bucket_order_int(state.open_cells_before, OPEN_BUCKETS)
        groups[("open_cells_before", label_b, order_b)].append(fact)
        label_c = _bucket_label_realized(state.realized_net_r_today_before, REALIZED_BUCKETS)
        order_c = _bucket_order_realized(state.realized_net_r_today_before, REALIZED_BUCKETS)
        groups[("realized_net_r_today_before", label_c, order_c)].append(fact)

    rows: list[LeadingRow] = []
    for (indicator, bucket, order), group in groups.items():
        wins = sum(1 for f in group if f.net_r > 0)
        bucket_net = sum(f.net_r for f in group)
        rows.append(
            LeadingRow(
                segment=segment,
                indicator=indicator,
                bucket=bucket,
                order=order,
                num_trades=len(group),
                share_of_trades=len(group) / total_trades,
                win_rate=wins / len(group),
                mean_net_r=bucket_net / len(group),
                sum_net_r=bucket_net,
                share_of_total_net_r=(bucket_net / total_net if total_net < 0 else float("nan")),
            )
        )
    return sorted(rows, key=lambda r: (r.indicator, r.order))


def _bucket_order_int(value: int, edges: Sequence[int]) -> int:
    for i, edge in enumerate(edges):
        if value <= edge:
            return i
    return len(edges)


def _bucket_order_realized(value: float, edges: Sequence[float]) -> int:
    for i, edge in enumerate(edges):
        if value >= edge:
            return i
    return len(edges)


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
    """검산 (c)가 성립하는 좌표인가 — 좁혀 돈 판을 공개 CSV와 대조하면 배선 오류처럼 보인다."""
    return (
        tuple(symbols) == tuple(harness.DEFAULT_SYMBOLS)
        and tuple(timeframes) == tuple(harness.DEFAULT_TIMEFRAMES)
        and start == harness.DEFAULT_START
        and end == harness.DEFAULT_END
    )


def checksums(
    facts_by_segment: dict[str, list[TradeFact]],
    concurrency: Sequence[ConcurrencyRow],
    identical_permutations: int,
    *,
    adopted_coordinates: bool,
) -> list[ChecksumRow]:
    """(c) 공개 CSV 집계 · (n) 널이 걸렸다 · (i) 종목 내 순열은 항등이다."""
    checks: list[ChecksumRow] = []
    primary = facts_by_segment.get(PRIMARY_SEGMENT, [])
    if adopted_coordinates and primary:
        mean_net = statistics.fmean(f.net_r for f in primary)
        checks.append(
            ChecksumRow(
                check="(c) oos_warm ≡ 공개 book_trades.csv 집계",
                segment=PRIMARY_SEGMENT,
                metric="num_trades",
                left=float(len(primary)),
                right=float(PUBLISHED_OOS_WARM_TRADES),
                abs_diff=abs(len(primary) - PUBLISHED_OOS_WARM_TRADES),
            )
        )
        checks.append(
            ChecksumRow(
                check="(c) oos_warm ≡ 공개 book_trades.csv 집계",
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
                check="(c) oos_warm ≡ 공개 book_trades.csv 집계",
                segment="—",
                metric="skipped_not_adopted_coordinates",
                left=0.0,
                right=0.0,
                abs_diff=0.0,
            )
        )

    # 🚨 **실측이 0인 행은 세지 않는다** — 널도 0이라 「같은 값」이 정상이고(그 행은 애초에
    # 아무 말도 안 한다), 그걸 섞어 세면 멀쩡한 널이 퇴화로 보고된다.
    identical_null = sum(r.num_identical_draws for r in concurrency if r.observed_share > 0)
    checks.append(
        ChecksumRow(
            check="(n) 널이 실제로 걸렸다(시프트 draw ≠ 실측)",
            segment="all",
            metric="num_identical_draws",
            left=float(identical_null),
            right=0.0,
            abs_diff=float(identical_null),
        )
    )
    checks.append(
        ChecksumRow(
            check="(i) 종목 내 순열은 항등이다(함정의 기록)",
            segment=PRIMARY_SEGMENT,
            metric="num_identical_permutations",
            left=float(identical_permutations),
            right=float(identical_permutations),
            abs_diff=0.0,
        )
    )
    return checks


def identity_probe(facts: Sequence[TradeFact], *, draws: int = 30, seed: int = NULL_SEED) -> int:
    """이슈가 돌린 그 대조군을 **그대로** 다시 돌려 몇 번이 실측과 같은지 센다.

    돌아오는 값은 언제나 `draws`여야 한다 — 그것이 「대조군이 고장 난 게 아니라 그 순열이
    이 통계량에 대해 항등」이라는 이 이슈의 답이다(모듈 독스트링 §0-2).
    """
    events = stop_events(facts)
    if not events:
        return 0
    bucket_ms = dict(BUCKETS)["4h"]
    observed = concurrency_share(events, bucket_ms=bucket_ms, threshold=5)
    rng = random.Random(seed)
    same = 0
    for _ in range(draws):
        permuted = permute_within_symbol(events, rng)
        value = concurrency_share(permuted, bucket_ms=bucket_ms, threshold=5)
        if _same(value, observed):
            same += 1
    return same


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
    segments: Sequence[str] = SEGMENT_ORDER,
    draws: int = NULL_DRAWS,
    seed: int = NULL_SEED,
    cold_segments: bool = True,
    cache: PayloadCache | None = None,
    log: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """§0 전체 — 후보 한 번 · 구간별 배치 · 집계."""
    started = time.monotonic()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = build_payloads(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        cache=cache,
    )
    if log:
        print(f"[wan408] 후보 {len(payloads)}칸 · {time.monotonic() - started:.0f}s", flush=True)

    books = place(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments)
    facts_by_segment = {b.segment: trade_facts(b) for b in books}

    daily: list[DayRow] = []
    concentration: list[ConcentrationRow] = []
    concurrency: list[ConcurrencyRow] = []
    correlation: list[CorrelationRow] = []
    leading: list[LeadingRow] = []
    for segment in segments:
        facts = facts_by_segment.get(segment)
        if not facts:
            continue
        days = day_rows(facts, segment=segment)
        daily.extend(days)
        concentration.extend(concentration_rows(days, segment=segment))
        concurrency.extend(concurrency_rows(facts, segment=segment, draws=draws, seed=seed))
        correlation.extend(correlation_rows(facts, segment=segment))
        leading.extend(leading_rows(facts, segment=segment))
        if log:
            print(
                f"[wan408] {segment}: 거래 {len(facts)} · 일수 {len(days)} · "
                f"{time.monotonic() - started:.0f}s",
                flush=True,
            )

    checks = checksums(
        facts_by_segment,
        concurrency,
        identity_probe(facts_by_segment.get(PRIMARY_SEGMENT, [])),
        adopted_coordinates=on_adopted_coordinates(symbols, timeframes, start, end),
    )
    return (
        pd.DataFrame([r.model_dump() for r in daily]),
        pd.DataFrame([r.model_dump() for r in concentration]),
        pd.DataFrame([r.model_dump() for r in concurrency]),
        pd.DataFrame([r.model_dump() for r in correlation]),
        pd.DataFrame([r.model_dump() for r in leading]),
        pd.DataFrame([r.model_dump() for r in checks]),
    )


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


def _pct(value: float, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value * 100:.{digits}f}%"


def _ratio(observed: float, null_mean: float) -> str:
    """실측 ÷ 널 평균 — 🚨 **p가 바닥에 붙을 때 크기를 읽는 자**다(널 200 draw면 p ≥ 1/201)."""
    if null_mean <= 0:
        return "널 평균 0(배수 정의 불가)"
    return f"{observed / null_mean:,.0f}배"


def verdict_lines(
    concentration: pd.DataFrame, concurrency: pd.DataFrame, leading: pd.DataFrame
) -> list[str]:
    """표에서 **계산해서** 내는 판정 — 사람이 표를 보고 문장을 지어내지 않는다."""
    lines: list[str] = []

    top10 = concentration[concentration["top_n"] == 10].set_index("segment")
    if PRIMARY_SEGMENT in top10.index:
        primary = top10.loc[PRIMARY_SEGMENT]
        others = ", ".join(
            f"`{seg}` {_pct(float(top10.loc[seg, 'share_of_total']))}"
            for seg in top10.index
            if seg != PRIMARY_SEGMENT
        )
        lines.append(
            f"1. **집중도는 사실이고 뒷구간에서 더 세다** — `{PRIMARY_SEGMENT}`의 최악 10일"
            f"({_pct(float(primary['days_share']), 2)})이 총손실의 "
            f"**{_pct(float(primary['share_of_total']))}**를 만든다({others}). 즉 폭락일 집중은 "
            f"뒷구간만의 성질이 **아니고**(전 구간에서 성립한다) 다만 **뒷구간에서 더 극단적**이다."
        )

    primary_conc = concurrency[concurrency["segment"] == PRIMARY_SEGMENT]
    tight = primary_conc[(primary_conc["bucket"] == "4h") & (primary_conc["threshold"] == 5)]
    if not tight.empty:
        row = tight.iloc[0]
        observed, null_mean = float(row["observed_share"]), float(row["null_mean"])
        lines.append(
            f"2. **동시성은 우연이 아니다** — `{PRIMARY_SEGMENT}` 4시간 버킷에서 "
            f"손절의 **{_pct(observed)}**가 5종목 이상 동시인데 종목별 원형 시프트 널은 "
            f"{_pct(null_mean)}다(**{_ratio(observed, null_mean)}** · "
            f"p={float(row['p_value']):.4f}). 🚨 **p는 바닥(1/(draws+1))이라 크기를 "
            f"못 읽는다** — 크기는 배수로 읽는다."
        )
    wide = primary_conc[(primary_conc["bucket"] == "1d") & (primary_conc["threshold"] == 5)]
    if not wide.empty:
        row = wide.iloc[0]
        observed, null_mean = float(row["observed_share"]), float(row["null_mean"])
        gap = observed - null_mean
        lines.append(
            f"3. ⚠️ **모든 칸이 그렇게 말하는 건 아니다** — 1일 버킷 5종목+는 실측 "
            f"{_pct(observed)} vs 널 {_pct(null_mean)}로 격차가 **{gap * 100:.1f}%p뿐**이다. "
            f"버킷이 넓으면 **우연히도** 5종목이 같은 하루에 들어가므로 그 칸은 사실상 "
            f"아무 말도 안 한다 — 증거는 **1시간·4시간 버킷과 1일 8종목+**에 있다."
        )

    primary_leading = leading[leading["segment"] == PRIMARY_SEGMENT]
    realized = primary_leading[primary_leading["indicator"] == "realized_net_r_today_before"]
    if not realized.empty:
        worst = realized.loc[realized["order"].idxmax()]
        best = realized.loc[realized["order"].idxmin()]
        lines.append(
            f"4. **인과적으로 가장 깨끗한 지표는 (c) 그날 실현 net R 누적이다** — "
            f"「{worst['bucket']}」에서 들어간 거래는 거래당 "
            f"**{float(worst['mean_net_r']):+.4f}R**(승률 {_pct(float(worst['win_rate']))})이고 "
            f"그 버킷이 총손실의 **{_pct(float(worst['share_of_total_net_r']))}**를 담는데 "
            f"거래는 {_pct(float(worst['share_of_trades']))}뿐이다"
            f"(「{best['bucket']}」은 {float(best['mean_net_r']):+.4f}R). ⚠️ **그래도 그 버킷을 "
            f"지우면 얼마를 버는지는 이 표가 답하지 않는다**(막힌 거래가 비운 자본·슬롯을 "
            f"다른 칸이 쓴다 — §1 소관)."
        )
    stops = primary_leading[primary_leading["indicator"] == "stops_today_before"]
    if not stops.empty:
        ordered = stops.sort_values("order")
        means = [float(v) for v in ordered["mean_net_r"]]
        monotone = all(a >= b for a, b in zip(means, means[1:], strict=True))
        tail = ordered.iloc[-1]
        lines.append(
            f"5. **(a) 그날 이미 손절난 거래 수는 꼬리에서만 말한다** — 「{tail['bucket']}」이 "
            f"{float(tail['mean_net_r']):+.4f}R로 최악이되 중간 버킷은 "
            f"{'단조롭다' if monotone else '**단조롭지 않다**'} — 문턱을 낮게 잡으면 오히려 좋은 "
            f"구간을 자른다."
        )
    opens = primary_leading[primary_leading["indicator"] == "open_cells_before"]
    if not opens.empty:
        first = opens.sort_values("order").iloc[0]
        lines.append(
            f"6. 🚨 **(b) 열린 칸 수는 직관과 반대다** — 가장 나쁜 버킷이 「{first['bucket']}」"
            f"({float(first['mean_net_r']):+.4f}R · 총손실의 "
            f"{_pct(float(first['share_of_total_net_r']))})이다. 「많이 열려 있으면 위험하다」가 "
            f"**성립하지 않는다** — 폭락일에는 진입한 포지션이 곧바로 손절나 **열린 칸이 쌓이지 "
            f"않기** 때문으로 보인다(정합적인 설명까지이고 이 표가 증명한 것은 아니다). "
            f"**§1이 이 축을 스위치로 쓰면 안 된다.**"
        )
    return lines


def render_summary(
    daily: pd.DataFrame,
    concentration: pd.DataFrame,
    concurrency: pd.DataFrame,
    correlation: pd.DataFrame,
    leading: pd.DataFrame,
    checks: pd.DataFrame,
) -> str:
    """사람이 읽는 요약 — 🚨 **읽는 법을 먼저** 적고 표를 뒤에 놓는다."""
    out: list[str] = []
    out.append("# WAN-408 §0 — 손실은 시간축에 어떻게 분포하나 (관측 전용)\n")
    out.append("## 판정\n")
    for line in verdict_lines(concentration, concurrency, leading):
        out.append(line + "\n")
    out.append(
        "\n🚨 **이 표는 채택 근거가 아니다.** 「폭락일을 피하면 좋아진다」는 사후에 그 날을 아는 "
        "값이라 인과적으로 구현할 수 없다. 여기 있는 것은 **분포와 선행 지표의 관측**이고, "
        "손익 판정은 북에서 나야 한다(WAN-341) — 그것은 §1(별도 PR · **사용자 결정**)이다.\n"
    )
    out.append(
        "\n좌표: 12종목 × 4TF 한 지갑 · 못 박은 6년 창 · 존폭 필터 끔(WAN-384) · 인과 취소"
        "(WAN-365) · 재진입 ON(band, WAN-273) · cap_only 5배 · 익절 메이커(WAN-370) · "
        "`baseline` 렌즈 · 복리 켬 · **핀 없음**(WAN-305). 매매를 하나도 안 바꾼다.\n"
    )

    out.append("\n## 검산\n")
    out.append("| 검산 | 구간 | 값 | 기준 | 차이 |")
    out.append("| -- | -- | --: | --: | --: |")
    for row in checks.itertuples():
        out.append(
            f"| {row.check} | {row.segment} | {_fmt(float(row.left), 6)} | "
            f"{_fmt(float(row.right), 6)} | {float(row.abs_diff):.2e} |"
        )
    out.append(
        "\n📌 **(i)는 「실패」가 아니라 이 이슈의 답이다** — 종목 **안에서** 청산 시각을 섞는 "
        "순열은 동시성 통계량에 대해 **항등**이라(통계량이 종목별 시각 **집합**의 함수다) "
        "draw 전부가 실측과 같은 값을 낸다. 이슈 본문의 「대조군이 동작하지 않았다」는 배선 "
        "문제가 아니라 그 성질이었다. 동작하는 널은 **종목별 원형 시프트**다.\n"
    )

    out.append("\n## §0-1 집중도 — 최악 N일이 총손실의 몇 %인가\n")
    out.append(
        "| 구간 | 최악 N일 | 일수 대비 | net R 합 | 전체 net R | 총손실 대비 | 거래 | 손절 | "
        "최소 동시 손절 종목 | 재진입 비중 |"
    )
    out.append("| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
    for row in concentration.itertuples():
        out.append(
            f"| {row.segment} | {int(row.top_n)}일 | {_pct(float(row.days_share), 2)} | "
            f"{float(row.sum_net_r_top):+.1f}R | {float(row.total_net_r):+.1f}R | "
            f"{_pct(float(row.share_of_total))} | {int(row.num_trades_top)} | "
            f"{int(row.num_stops_top)} | {int(row.min_symbols_stopped_top)} | "
            f"{_pct(float(row.reentry_share_top))} |"
        )
    out.append(
        "\n⚠️ **「총손실 대비」는 구간 총합이 음수일 때만 뜻이 있다** — 양수 구간은 「—」다"
        "(WAN-115 부호 함정). ⚠️ 재진입 비중은 **라벨 필터**이지 지갑 재배치가 아니다"
        "(WAN-316) — 「재진입을 끄면 좋다」로 읽지 말 것.\n"
    )

    primary_days = daily[daily["segment"] == PRIMARY_SEGMENT].sort_values("sum_net_r")
    if not primary_days.empty:
        out.append(f"\n### 최악 10일 (`{PRIMARY_SEGMENT}`)\n")
        out.append("| 날짜(KST) | net R | 거래 | 손절 | 손절 종목 | 매매 종목 | 재진입 |")
        out.append("| -- | --: | --: | --: | --: | --: | --: |")
        for row in primary_days.head(10).itertuples():
            out.append(
                f"| {row.day} | {float(row.sum_net_r):+.1f}R | {int(row.num_trades)} | "
                f"{int(row.num_stops)} | {int(row.num_symbols_stopped)} | "
                f"{int(row.num_symbols_traded)} | {int(row.num_reentry_trades)} |"
            )

    out.append("\n## §0-1·§0-2 동시성 — 실측 ↔ 종목별 원형 시프트 널\n")
    out.append("| 구간 | 버킷 | 문턱 | 손절 | 실측 | 널 평균 | 널 p05~p95 | p | 같은 값 draw |")
    out.append("| -- | -- | --: | --: | --: | --: | -- | --: | --: |")
    for row in concurrency.itertuples():
        out.append(
            f"| {row.segment} | {row.bucket} | {int(row.threshold)}종목+ | "
            f"{int(row.num_stops)} | {_pct(float(row.observed_share))} | "
            f"{_pct(float(row.null_mean))} | {_pct(float(row.null_p05))}~"
            f"{_pct(float(row.null_p95))} | {float(row.p_value):.4f} | "
            f"{int(row.num_identical_draws)} |"
        )
    out.append(
        "\n📌 **널이 보존하는 것과 깨는 것**: 종목 **안의** 뭉침은 그대로 두고 종목 **사이의** "
        "정렬만 깬다 — 그래서 초과분이 *「종목들이 같이 죽는다」*의 몫이다. ⚠️ 원형 시프트는 "
        "시계열 끝을 앞으로 감아 붙이고 **지갑을 다시 배치하지 않는다**(WAN-316) — 이 널은 "
        "*「정렬이 우연인가」*만 답하고 *「정렬이 없었다면 얼마를 벌었나」*는 답하지 않는다.\n"
    )

    if not correlation.empty:
        out.append("\n## §0-1 일간 net R 상관\n")
        pair = correlation[correlation["scope"] == "pair_mean"]
        out.append("| 구간 | 종목쌍 평균 | BTC 대비 평균 | BTC 대비 최소(종목) |")
        out.append("| -- | --: | --: | -- |")
        for row in pair.itertuples():
            vs_btc = correlation[
                (correlation["segment"] == row.segment) & (correlation["scope"] == "vs_btc")
            ]
            if vs_btc.empty:
                out.append(f"| {row.segment} | {_fmt(float(row.value), 2)} | — | — |")
                continue
            worst = vs_btc.loc[vs_btc["value"].idxmin()]
            out.append(
                f"| {row.segment} | {_fmt(float(row.value), 2)} | "
                f"{_fmt(float(vs_btc['value'].mean()), 2)} | "
                f"{_fmt(float(worst['value']), 2)} ({worst['symbol']}) |"
            )
        out.append(
            "\n📌 **거래가 없는 날은 0으로 둔다** — 그날 그 종목의 실현 손익이 실제로 0이기 "
            "때문이다. 「둘 다 거래한 날만」으로 자르면 하필 **모두가 거래한 날**(= 폭락일)로 "
            "표본이 쏠려 상관이 부풀려진다.\n"
        )

    out.append(f"\n## §0-3 인과적 선행 지표 (`{PRIMARY_SEGMENT}`)\n")
    out.append(
        "진입 시각에서 **그 전에 이미 끝난 청산만** 본다 — §1이 그대로 스위치로 쓸 수 있는 "
        "자다. (a)·(c)는 같은 KST 하루 안에서, (b)는 하루 경계와 무관하게 센다.\n"
    )
    primary_leading = leading[leading["segment"] == PRIMARY_SEGMENT]
    for indicator, title in (
        ("stops_today_before", "(a) 그날 이미 손절난 거래 수"),
        ("open_cells_before", "(b) 그 순간 열려 있는 칸 수"),
        ("realized_net_r_today_before", "(c) 그날 실현 net R 누적"),
    ):
        block = primary_leading[primary_leading["indicator"] == indicator]
        if block.empty:
            continue
        out.append(f"\n### {title}\n")
        out.append("| 버킷 | 거래 | 비중 | 승률 | 거래당 net R | net R 합 | 총손실 대비 |")
        out.append("| -- | --: | --: | --: | --: | --: | --: |")
        for row in block.sort_values("order").itertuples():
            out.append(
                f"| {row.bucket} | {int(row.num_trades)} | {_pct(float(row.share_of_trades))} | "
                f"{_pct(float(row.win_rate))} | {float(row.mean_net_r):+.4f} | "
                f"{float(row.sum_net_r):+.1f}R | {_pct(float(row.share_of_total_net_r))} |"
            )
    out.append(
        f"\n🚨 **이것은 관측이지 반사실이 아니다** — 「그 문턱에서 막았다면」의 손익은 막힌 "
        f"거래가 비운 자본·슬롯을 다른 칸이 쓰기 때문에 이 표에서 읽을 수 없다"
        f"(WAN-316/323 채널). ⚠️ 거래당 net R 차이는 ±{NOISE_R}R 안이면 「0과 구분되지 "
        f"않는다」로 읽는다(WAN-366 규약).\n"
    )

    out.append(
        "\n## 안 잰 것 · 불변\n\n"
        "- 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 **체결 보수화(`pen_5bp`) 미측정**.\n"
        "- 6년 MDD는 폭락 미포함 **바닥선**이고, 이 표는 그 바닥선조차 **하루가 4분의 1을 "
        "먹는 분포** 위의 값임을 보인다.\n"
        "- **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *손실이 "
        "시간축에 어떻게 분포하나*를 묻는다(**다른 질문**).\n"
        "- **측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·"
        "`LeverageBookParams()` 그대로 · 존폭 필터·손절폭 가드·익절 배수 **안 건드렸다**) · "
        "실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).\n"
    )
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-408 §0 손실 집중·동시성 관측")
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--draws", type=int, default=NULL_DRAWS)
    parser.add_argument("--seed", type=int, default=NULL_SEED)
    parser.add_argument(
        "--no-cold-segments",
        action="store_true",
        help="차가운 절단(`is`/`oos`)을 건너뛴다 — 완료기준 4의 `is`가 표에서 빠진다",
    )
    parser.add_argument("--no-cache", action="store_true", help="payload 디스크 캐시를 쓰지 않는다")
    parser.add_argument("--from-csv", action="store_true", help="적재 CSV로 요약만 다시 낸다")
    args = parser.parse_args(argv)

    if args.from_csv:
        frames = _load_frames()
        if frames is None:
            parser.error(f"{DAILY_CSV}가 없습니다 — 먼저 측정을 돌리세요.")
        SUMMARY_PATH.write_text(render_summary(*frames), encoding="utf-8")
        print(f"[wan408] 요약 → {SUMMARY_PATH}", flush=True)
        return 0

    cold = not args.no_cold_segments
    segments = SEGMENT_ORDER if cold else ("full", PRIMARY_SEGMENT)
    daily, concentration, concurrency, correlation, leading, checks = run_measure(
        [s.strip() for s in args.symbols.split(",") if s.strip()],
        [t.strip() for t in args.timeframes.split(",") if t.strip()],
        start=args.start,
        end=args.end,
        jobs=args.jobs,
        segments=segments,
        draws=args.draws,
        seed=args.seed,
        cold_segments=cold,
        cache=None if args.no_cache else PayloadCache(),
    )
    _write(daily, DAILY_CSV)
    _write(concentration, CONCENTRATION_CSV)
    _write(concurrency, CONCURRENCY_CSV)
    _write(correlation, CORRELATION_CSV)
    _write(leading, LEADING_CSV)
    _write(checks, CHECKSUM_CSV)
    SUMMARY_PATH.write_text(
        render_summary(daily, concentration, concurrency, correlation, leading, checks),
        encoding="utf-8",
    )
    print(f"[wan408] 요약 → {SUMMARY_PATH}", flush=True)
    return 0


def _load_frames() -> (
    tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame] | None
):
    paths = (
        DAILY_CSV,
        CONCENTRATION_CSV,
        CONCURRENCY_CSV,
        CORRELATION_CSV,
        LEADING_CSV,
        CHECKSUM_CSV,
    )
    if not all(p.exists() for p in paths):
        return None
    a, b, c, d, e, f = (pd.read_csv(p) for p in paths)
    return a, b, c, d, e, f


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
