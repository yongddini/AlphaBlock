"""WAN-428 부록 — 스토캐스틱 × 오더블록 팔의 **존 순위 인구조사** (사용자 요청 2026-09-23).

## 왜 이 모듈이 있나

§1(`wan428_zone_rank_census`)은 **채택 북**에서 *「북이 체결한 거래가 그 순간 화면 존 목록의
몇 위였나」*를 쟀고, 4위 이하가 거래의 21.94% · 총손실의 37.07%였다. 사용자가 물었다 —
*「스토캐스틱 + 손절폭 넣은 걸로 한번 봐주면 안 되니? 첫탭 재진입 없는 버전」*.

그 팔이 이 저장소에서 **뒷구간 양수를 낸 유일한 팔**이므로(WAN-423 §6·§7 → WAN-424 §1이
복원: ts4 · 손절폭 하한 4% · %K<25 → `oos_warm` **＋0.165R · 679거래**), 순위 이야기가 거기서도
서는지가 §2 설계를 가른다.

🚨 **§1의 착수 지시(「§1만」 = 채택 좌표) 범위 밖이다** — 사용자 요청으로 얹는 **부록**이고,
그래서 별도 모듈·별도 CSV다. **§1 표와 셀 비교 금지**(아래 좌표 표).

## 팔은 새로 정의하지 않는다 — `wan424_stoch_ob_arm`을 그대로 부른다

후보 풀·시간 청산·손절폭 하한·%K 정의·배치 인자가 전부 그쪽 함수다(`build_base_payloads` ·
`build_arm_cells` · `filtered_payloads`). 🚨 **자기 사본을 만들면 「같은 팔을 쟀다」가 거짓이
된다** — 그래서 이 모듈이 더하는 것은 **순위 라벨 하나**뿐이고, 배치 인자가 그쪽 `place`와
같은지는 **호출부를 가로채는 스파이 테스트**가 고정한다.

순위 자도 §1의 것을 **import**한다(`rank_at` · `_Ranked` · `bucket_label` · `RANK_BUCKETS`) —
`select_active`를 실제로 부르는 그 등식이 여기서도 그대로 성립해야 한다.

## 좌표 — §1과 **여섯 축**이 다르다 (셀 비교 금지)

| 축 | §1(채택 북) | 이 표 |
| -- | -- | -- |
| 유니버스 | 12종목 × 4TF(15m·1h·2h·4h) | **31종목 × 9TF**(1h~1w) |
| 재탭 | `every_tap` | **첫 탭만** |
| 재진입 | ON(band) | **없음** |
| 익절 | 고정 1.5R | **익절선 제거** + `tsN` 시간 청산 |
| 손절폭 | 가드 0.3% | **하한 3~4%**(가드 끔) |
| 렌즈 | `baseline` | **`pen_5bp`** × 같은 분 익절 금지 |
| 스토캐스틱 | 없음 | %K(20,10) < 문턱 |

## 검산

* **(a′) 팔 재현** — 이 모듈의 배치가 WAN-424 §1이 적재한 `wan424_stoch_ob_arm.csv`의 같은
  칸(거래 수 · 거래당 net R)과 맞아야 한다. 🚨 **§1의 검산 (a)(공개 `book_trades.csv` 등식)는
  여기 성립하지 않는다** — 채택 좌표가 아니다. 거기 걸면 「검산 실패」가 정상 동작이 된다
  (WAN-367).
* **(b)** 순위를 못 잰 거래 0건 · **(c)** base 거래의 탭 시각 ∈ 그 존의 `tapped_times`.
  🚨 이 팔은 **첫 탭만**이라 (c)가 전 거래에 걸린다(재진입이 없어 면제 대상이 없다).

## 재현

    uv run python -m backtest.wan428_zone_rank_stoch_arm --pilot          # 2종목 × 2TF
    uv run python -m backtest.wan428_zone_rank_stoch_arm --jobs 4         # 전체
    uv run python -m backtest.wan428_zone_rank_stoch_arm --from-csv       # 요약만

📌 **후보 캐시를 공유한다** — `--payload-dir`가 WAN-424 §1이 쓰는 그 디렉터리를 가리키면
279칸이 전부 히트한다(같은 `_Task` 모양 · 같은 엔진 리비전). 리비전이 다르면 **미스가 되고
그게 맞는 동작이다**(WAN-253/364).

측정·관측 전용 · 엔진·기본값·토대 불변 · 핀 없음(WAN-305) · 판단은 북에서(WAN-341) ·
`zone_limit`을 시그널 경로에 걸지 않았다 · `ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import LeverageBookParams
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import CellPayload
from backtest.wan424_stoch_ob_arm import (
    DEFAULT_PAYLOAD_DIR,
    SYMBOLS,
    TIMEFRAMES,
    WAN423_REFERENCE,
    ArmCell,
    _equity_path,
    build_arm_cells,
    build_base_payloads,
    filtered_payloads,
)
from backtest.wan428_zone_rank_census import (
    PINE_MAX_ORDER_BLOCKS,
    RENDER_LIMIT_LOW,
    CellArchive,
    TradeRank,
    _Ranked,
    _zone_index,
    below_render_limit,
    bucket_label,
    build_archives,
)
from backtest.zone_limit_backtest import _Candidate

__all__ = [
    "CENSUS_CSV",
    "CHECKSUM_CSV",
    "CAPS",
    "COMBOS",
    "CapRow",
    "cap_rows",
    "PRIMARY_SEGMENT",
    "RANK_SEGMENTS",
    "SEGMENTS",
    "SUMMARY_MD",
    "ChecksumRow",
    "StochRankRow",
    "census_rows",
    "checksum_rows",
    "place_arm_segments",
    "rank_arm_trades",
    "render_summary",
    "run_measure",
]

#: **배치**하는 구간 — `wan424_stoch_ob_arm.SEGMENTS`와 같다. 기준값 재현 검산 (a′)이 셋 다
#: 덮는다(거래 수·거래당 net R은 순위가 필요 없다).
SEGMENTS: tuple[str, ...] = ("full", "is", "oos_warm")

#: 🚨 **순위를 매기는 구간은 둘뿐이다.**
#:
#: 착수 때 *「이 팔의 `is`는 차가운 절단이 아니라 같은 아카이브를 쓴다」*고 적었는데 **틀렸다** —
#: `wan424.build_base_payloads`가 `cold_segments`를 넘기지 않고 `run_cells`의 **기본값이
#: `True`**라, `is`는 구간을 먼저 자르고 **다시 탐지한** 판이다. 그러면 `zone_key`의 아카이브
#: 인덱스가 `full` 창의 것과 달라 순위를 매길 수 없다(§1이 `is`를 뺀 그 이유 그대로).
#:
#: 📌 **파일럿이 그것을 잡았다** — 검산 (b)·(c)가 `is` 구간에서 **전 거래 불일치**로 울렸고
#: `full`·`oos_warm`은 0이었다. 조용히 통과했으면 「엉뚱한 존의 순위」가 표에 실렸을 것이다
#: (개수만 세면 안 보이는 부류 — WAN-161).
RANK_SEGMENTS: tuple[str, ...] = ("full", "oos_warm")

PRIMARY_SEGMENT = "oos_warm"

#: 재는 조합 — WAN-424가 **기준값으로 못 박아 둔 두 칸**만 본다(`WAN423_REFERENCE`).
#: 🚨 격자를 새로 뒤지지 않는다: 순위 분포를 보려는 것이고, 조합을 늘리면 그 자체가 탐색이 된다
#: (WAN-161). `(보유 봉, 손절폭 하한, %K 문턱)`.
COMBOS: tuple[tuple[int, float, float], ...] = tuple(sorted(WAN423_REFERENCE))

#: 🚨 **§2 — `Zone Count`를 시그널 경로에 실제로 건다**(사용자 요청 2026-09-23: *「MDD가 좀
#: 높길래 그거 zone count가 어떤지에 따라서 MDD를 낮출 수 있는지가 보고 싶다」*).
#:
#: 원본 지표의 네 값(`One`/`Low`/`Medium`/`High`) + 무제한(= 오늘 동작). 각 값에서 **그 시점
#: 순위가 캡 이하인 후보만** 남기고 북을 **다시 배치**한다 — 라벨 필터가 아니라 배치를 다시
#: 하므로 자본·슬롯 재배치가 반영된다(WAN-316/389).
#:
#: 🚨 **판정 자는 MDD다**(사용자 지정). 거래당 net R·거래 수를 함께 내되, **거래를 줄여 MDD가
#: 내려가는 것은 당연하므로**(WAN-378) 두 열을 반드시 같이 읽는다.
CAPS: tuple[int | None, ...] = (None, 10, 5, 3, 1)

#: 원본 `Zone Count` 이름 — 표에 그 이름으로 찍어야 사용자가 화면 설정과 바로 맞춰 본다.
CAP_NAMES: dict[int | None, str] = {
    None: "무제한(오늘)",
    10: "High(10)",
    5: "Medium(5)",
    3: "Low(3) = 원본 기본",
    1: "One(1)",
}

CAP_CSV = Path("backtest/reports/wan428_zone_cap_stoch_arm.csv")
CENSUS_CSV = Path("backtest/reports/wan428_zone_rank_stoch_arm.csv")
CHECKSUM_CSV = Path("backtest/reports/wan428_zone_rank_stoch_arm_checksum.csv")
SUMMARY_MD = Path("backtest/reports/wan428_zone_rank_stoch_arm_summary.md")
TRADES_CSV = Path("backtest/cache/wan428/stoch_arm_trades.csv.gz")


def combo_label(combo: tuple[int, float, float]) -> str:
    hold, floor, threshold = combo
    return f"ts{hold}·하한{floor:.0%}·%K<{threshold:.0f}"


# --------------------------------------------------------------------------- #
# 배치 — `wan424.place`와 **같은 인자**여야 한다
# --------------------------------------------------------------------------- #


def place_arm_segments(payloads: Sequence[CellPayload]) -> list[BookSegment]:
    """한 조합의 북 배치 — 인자는 `wan424_stoch_ob_arm.place`가 쓰는 것과 **같다**.

    그쪽은 `ArmRow`(집계)만 돌려주므로 거래 단위 귀속에 쓸 `BookSegment`를 얻을 수 없다
    (§1이 `book_cli.build_book_rows` 대신 `iter_book_segments`를 쓴 것과 같은 이유, WAN-336).
    🚨 인자가 조용히 갈라지면 **다른 팔을 쟀다**가 되므로 스파이 테스트가 두 경로의 호출 인자
    전체를 대조한다.
    """
    return iter_book_segments(
        payloads,
        book=LeverageBookParams(),
        segments=SEGMENTS,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
        include_reentry=False,
        compound_sizing=False,
        min_stop_distance_fraction=0.0,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def rank_arm_trades(
    segment: BookSegment,
    archives: dict[tuple[str, str], CellArchive],
    *,
    combo: str,
    ranked_cache: dict[tuple[str, str], _Ranked],
) -> list[TradeRank]:
    """§1과 **같은 라벨러** — 순위 자를 다시 쓰지 않는다(`rank_at`을 그대로 부른다)."""
    from backtest.wan428_zone_rank_census import rank_at

    out: list[TradeRank] = []
    for trade, placement in segment.trades_with_placements():
        symbol, timeframe = placement.cell
        cell = (symbol, timeframe)
        item = archives.get(cell)
        index = _zone_index(placement.zone_key)
        rank = rank_bar_close = None
        alive = 0
        if item is not None and index is not None and 0 <= index < len(item.archive):
            if cell not in ranked_cache:
                ranked_cache[cell] = _Ranked.build(item.archive)
            ranked = ranked_cache[cell]
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
                arm=combo,
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
# §2 — `Zone Count`를 시그널 경로에 걸고 MDD를 본다 (사용자 요청 2026-09-23)
# --------------------------------------------------------------------------- #


class CapRow(BaseModel):
    """한 (조합, 캡, 구간)의 북 집계 — **판정 열은 MDD**다."""

    model_config = ConfigDict(frozen=True)

    combo: str
    cap: int
    """0이면 무제한(캡 없음) — CSV에 `None`을 쓰지 않는다(왕복에서 NaN이 된다, WAN-395)."""
    cap_name: str
    segment: str
    num_trades: int
    mean_net_r: float
    se_net_r: float
    win_rate: float
    compound_return: float
    compound_mdd: float
    compound_ruined: bool
    fixed_return: float
    fixed_mdd: float
    """🚨 **복리를 끈 MDD** — 복리 MDD는 잔고가 커질수록 분모가 커져 팔 사이 비교가 흐려진다
    (WAN-346 §2가 못 박은 자리). 두 열을 함께 낸다."""


def cap_candidates(
    payloads: Sequence[CellPayload],
    archives: dict[tuple[str, str], CellArchive],
    *,
    cap: int | None,
    ranked_cache: dict[tuple[str, str], _Ranked],
) -> list[CellPayload]:
    """후보를 **그 시점 존 순위 ≤ cap**으로 거른다 — 이것이 「시그널 경로에 개수를 거는 것」이다.

    🚨 순위는 **탭 시각(`trigger_time`)**에 매긴다(주문이 걸리는 순간). `cap=None`이면 아무것도
    안 걸러 **원본 payload를 그대로 돌려준다**(비트 동일).

    ⚠️ **아카이브 전체를 잘라 두지 않는다**(이슈 경고) — 순위는 후보마다 그 시점에 다시
    매겨진다. 한 번 잘라 두면 「그때는 3위였는데 지금은 5위」인 존을 놓친다.
    """
    if cap is None:
        return list(payloads)
    from backtest.wan428_zone_rank_census import rank_at

    out: list[CellPayload] = []
    for payload in payloads:
        cell = (payload.symbol, payload.timeframe)
        item = archives.get(cell)
        if item is None or not item.archive:
            out.append(dataclasses.replace(payload, candidates={k: () for k in payload.candidates}))
            continue
        if cell not in ranked_cache:
            ranked_cache[cell] = _Ranked.build(item.archive)
        ranked = ranked_cache[cell]
        kept: dict[str, tuple[_Candidate, ...]] = {}
        for segment, cands in payload.candidates.items():
            rows = []
            for cand in cands:
                index = _zone_index(cand.zone_key)
                if index is None or not (0 <= index < len(item.archive)):
                    continue
                rank = rank_at(ranked, item.archive, index, cand.trigger_time)
                if rank is not None and rank <= cap:
                    rows.append(cand)
            kept[segment] = tuple(rows)
        out.append(dataclasses.replace(payload, candidates=kept))
    return out


def cap_rows(
    payloads: Sequence[CellPayload],
    cells: Sequence[ArmCell],
    archives: dict[tuple[str, str], CellArchive],
    *,
    ranked_cache: dict[tuple[str, str], _Ranked],
    log: bool = True,
) -> list[CapRow]:
    """조합 × 캡 × 구간의 북 집계. MDD·수익 식은 `wan424._equity_path`를 **그대로** 쓴다."""
    rows: list[CapRow] = []
    for combo in COMBOS:
        hold, floor, threshold = combo
        label = combo_label(combo)
        armed = filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=threshold)
        for cap in CAPS:
            capped = cap_candidates(armed, archives, cap=cap, ranked_cache=ranked_cache)
            for seg in place_arm_segments(capped):
                pairs = sorted(
                    ((t.exit_time, net_r(t, pl)) for t, pl in seg.trades_with_placements()),
                    key=lambda x: x[0],
                )
                rs = [r for _, r in pairs]
                n = len(rs)
                mean = _mean(rs)
                se = (
                    math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1) / n)
                    if n > 1
                    else float("nan")
                )
                f_ret, f_mdd, _ = _equity_path(rs, compound=False)
                c_ret, c_mdd, ruined = _equity_path(rs, compound=True)
                rows.append(
                    CapRow(
                        combo=label,
                        cap=cap or 0,
                        cap_name=CAP_NAMES[cap],
                        segment=seg.segment,
                        num_trades=n,
                        mean_net_r=mean,
                        se_net_r=se,
                        win_rate=(sum(1 for r in rs if r > 0) / n) if n else float("nan"),
                        compound_return=c_ret,
                        compound_mdd=c_mdd,
                        compound_ruined=ruined,
                        fixed_return=f_ret,
                        fixed_mdd=f_mdd,
                    )
                )
            if log:
                got = [r for r in rows if r.combo == label and r.cap == (cap or 0)]
                primary = next((r for r in got if r.segment == PRIMARY_SEGMENT), None)
                if primary is not None:
                    print(
                        f"[wan428-stoch] {label} · {CAP_NAMES[cap]}: "
                        f"{primary.num_trades:,}거래 · net R {primary.mean_net_r:+.4f} · "
                        f"복리MDD {primary.compound_mdd:.1%} · 고정MDD {primary.fixed_mdd:.1%}",
                        flush=True,
                    )
    return rows


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


class StochRankRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    combo: str
    segment: str
    rank_bucket: str
    num_trades: int
    trade_share: float
    mean_net_r: float
    net_r_sum: float
    stop_rate: float
    mean_alive_zones: float
    mean_stop_width: float
    """그 버킷 거래들의 손절폭(= `|진입가 − 손절가| / 진입가`) 평균 — **왜 이 축을 넣었나**:
    손절폭 하한이 존 높이를 고르는 필터라 「고순위 존이 넓은가」가 이 표의 핵심 질문이다."""


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


def _bucket_order(label: str) -> int:
    from backtest.wan428_zone_rank_census import RANK_BUCKETS

    for position, (name, _low, _high) in enumerate(RANK_BUCKETS):
        if name == label:
            return position
    return len(RANK_BUCKETS)


def census_rows(
    trades_by_combo: dict[str, dict[str, list[TradeRank]]],
    widths: dict[tuple[str, int, int], float],
) -> list[StochRankRow]:
    """조합 × 구간 × 순위 버킷. `widths`는 (조합, 진입시각, 아카이브 인덱스) → 손절폭."""
    rows: list[StochRankRow] = []
    for combo, by_segment in trades_by_combo.items():
        for segment in RANK_SEGMENTS:
            trades = by_segment.get(segment, [])
            if not trades:
                continue
            grouped: dict[str, list[TradeRank]] = {}
            for t in trades:
                grouped.setdefault(bucket_label(t.rank), []).append(t)
            for label in sorted(grouped, key=_bucket_order):
                items = grouped[label]
                rows.append(
                    StochRankRow(
                        combo=combo,
                        segment=segment,
                        rank_bucket=label,
                        num_trades=len(items),
                        trade_share=len(items) / len(trades),
                        mean_net_r=_mean([t.net_r for t in items]),
                        net_r_sum=sum(t.net_r for t in items),
                        stop_rate=sum(1 for t in items if t.is_stop) / len(items),
                        mean_alive_zones=_mean([float(t.alive_zones) for t in items]),
                        mean_stop_width=_mean(
                            [
                                w
                                for t in items
                                if (
                                    w := widths.get(
                                        (
                                            combo,
                                            t.entry_time,
                                            t.archive_index if t.archive_index is not None else -1,
                                        )
                                    )
                                )
                                is not None
                            ]
                        ),
                    )
                )
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


class ChecksumRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: str
    combo: str
    segment: str
    metric: str
    left: float
    right: float
    abs_diff: float


def checksum_rows(
    trades_by_combo: dict[str, dict[str, list[TradeRank]]],
    archives: dict[tuple[str, str], CellArchive],
    *,
    reference: dict[str, dict[str, list[float]]],
    full_coordinates: bool,
) -> list[ChecksumRow]:
    """(a′) WAN-424 기준값 재현 · (b) 순위 미측정 0 · (c) 탭 시각 ∈ `tapped_times`.

    🚨 (a′)는 **배치 층 net R 목록**(`reference`)에서 낸다 — 순위가 필요 없으므로 `is`까지
    덮는다. §1의 검산 (a)(공개 `book_trades.csv` 등식)는 **여기 성립하지 않는다**(채택 좌표가
    아니다) — 거기 걸면 「검산 실패」가 정상 동작이 된다(WAN-367).
    """
    checks: list[ChecksumRow] = []
    for combo in COMBOS:
        label = combo_label(combo)
        for segment, (ref_net, ref_n) in WAN423_REFERENCE[combo].items():
            values = reference.get(label, {}).get(segment)
            if not full_coordinates or not values:
                checks.append(
                    ChecksumRow(
                        check="(a′) WAN-424 §1 기준값 재현",
                        combo=label,
                        segment=segment,
                        metric="skipped_not_full_coordinates",
                        left=0.0,
                        right=0.0,
                        abs_diff=0.0,
                    )
                )
                continue
            checks.append(
                ChecksumRow(
                    check="(a′) WAN-424 §1 기준값 재현",
                    combo=label,
                    segment=segment,
                    metric="num_trades",
                    left=float(len(values)),
                    right=float(ref_n),
                    abs_diff=abs(len(values) - ref_n),
                )
            )
            mean = _mean(values)
            checks.append(
                ChecksumRow(
                    check="(a′) WAN-424 §1 기준값 재현",
                    combo=label,
                    segment=segment,
                    # 🚨 기준값은 WAN-423 **로그가 소수 셋째 자리로** 찍은 값이라 그 정밀도까지만
                    # 대조한다(WAN-424 `reference_check`와 같은 규약 — 자를 두 벌로 적지 않는다).
                    metric="mean_net_r(소수 3자리)",
                    left=round(mean, 3),
                    right=ref_net,
                    abs_diff=abs(round(mean, 3) - ref_net),
                )
            )
    for combo_name, by_segment in trades_by_combo.items():
        for segment, trades in by_segment.items():
            unranked = sum(1 for t in trades if t.rank is None)
            checks.append(
                ChecksumRow(
                    check="(b) 순위를 못 잰 거래 0건",
                    combo=combo_name,
                    segment=segment,
                    metric="num_unranked",
                    left=float(unranked),
                    right=0.0,
                    abs_diff=float(unranked),
                )
            )
            mismatched = 0
            for t in trades:
                if t.archive_index is None:
                    mismatched += 1
                    continue
                item = archives.get((t.symbol, t.timeframe))
                if (
                    item is None
                    or not (0 <= t.archive_index < len(item.archive))
                    or t.trigger_time not in item.archive[t.archive_index].tapped_times
                ):
                    mismatched += 1
            checks.append(
                ChecksumRow(
                    check="(c) 탭 시각 ∈ 그 존의 tapped_times",
                    combo=combo_name,
                    segment=segment,
                    metric="num_mismatched",
                    left=float(mismatched),
                    right=0.0,
                    abs_diff=float(mismatched),
                )
            )
    return checks


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _stop_widths(
    payloads: Sequence[CellPayload], *, combo: str
) -> dict[tuple[str, int, int], float]:
    """(조합, 진입시각, 아카이브 인덱스) → 손절폭 — 후보에서 그대로 읽는다.

    🚨 **`full` 후보만 본다**: `oos_warm`은 payload에 **키가 없고**(`full`을 경계로 걸러
    만든다) `is`는 아카이브 인덱스가 다른 판이라 섞으면 같은 키에 엉뚱한 값이 덮인다.
    파일럿에서 `oos_warm` 손절폭이 통째로 `NaN`으로 나와 드러난 자리다.
    """
    out: dict[tuple[str, int, int], float] = {}
    for payload in payloads:
        for cand in payload.candidates.get("full", ()):
            index = _zone_index(cand.zone_key)
            if index is None or cand.entry_price <= 0:
                continue
            out[(combo, cand.entry_time, index)] = (
                abs(cand.entry_price - cand.stop_price) / cand.entry_price
            )
    return out


def run_measure(
    *,
    symbols: Sequence[str] = SYMBOLS,
    timeframes: Sequence[str] = TIMEFRAMES,
    jobs: int = 1,
    payload_dir: Path = DEFAULT_PAYLOAD_DIR,
    log: bool = True,
) -> tuple[
    list[StochRankRow],
    list[ChecksumRow],
    dict[str, dict[str, list[TradeRank]]],
    list[CapRow],
]:
    t0 = time.monotonic()
    payloads = build_base_payloads(
        jobs=jobs, payload_dir=payload_dir, symbols=symbols, timeframes=timeframes
    )
    if log:
        print(
            f"[wan428-stoch] base 후보 {len(payloads)}칸: {time.monotonic() - t0:.0f}s", flush=True
        )
    cells: list[ArmCell] = build_arm_cells(payloads, jobs=jobs)
    t_arms = time.monotonic()
    if log:
        print(f"[wan428-stoch] 팔 후보: {t_arms - t0:.0f}s", flush=True)

    archives = build_archives(
        symbols, timeframes, start=harness.DEFAULT_START, end=harness.DEFAULT_END, jobs=jobs
    )
    t_arch = time.monotonic()
    if log:
        print(f"[wan428-stoch] 존 대장 {len(archives)}칸: {t_arch - t_arms:.0f}s", flush=True)

    ranked_cache: dict[tuple[str, str], _Ranked] = {}
    trades_by_combo: dict[str, dict[str, list[TradeRank]]] = {}
    reference: dict[str, dict[str, list[float]]] = {}
    widths: dict[tuple[str, int, int], float] = {}
    for combo in COMBOS:
        hold, floor, threshold = combo
        label = combo_label(combo)
        armed = filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=threshold)
        widths.update(_stop_widths(armed, combo=label))
        placed = place_arm_segments(armed)
        # 🚨 순위는 `RANK_SEGMENTS`만 — `is`는 구간을 자른 뒤 **다시 탐지한** 판이라 `zone_key`의
        # 아카이브 인덱스가 다르다(그 상수 주석 · 파일럿이 검산 (b)·(c)로 잡았다).
        trades_by_combo[label] = {
            seg.segment: rank_arm_trades(seg, archives, combo=label, ranked_cache=ranked_cache)
            for seg in placed
            if seg.segment in RANK_SEGMENTS
        }
        # 기준값 재현(a′)은 `is`까지 덮는다 — 순위가 필요 없는 집계라서다.
        reference[label] = {
            seg.segment: [net_r(t, pl) for t, pl in seg.trades_with_placements()] for seg in placed
        }
        if log:
            n = sum(len(v) for v in trades_by_combo[label].values())
            print(f"[wan428-stoch] {label}: 순위 라벨 {n:,}거래", flush=True)

    # §2 — `Zone Count`를 시그널 경로에 걸고 **MDD**를 본다(사용자 요청 2026-09-23).
    caps = cap_rows(payloads, cells, archives, ranked_cache=ranked_cache, log=log)

    rows = census_rows(trades_by_combo, widths)
    full = tuple(symbols) == SYMBOLS and tuple(timeframes) == TIMEFRAMES
    checks = checksum_rows(trades_by_combo, archives, reference=reference, full_coordinates=full)
    return rows, checks, trades_by_combo, caps


def _ratio(ret: float, mdd: float) -> str:
    """수익 ÷ MDD — 🚨 **위험당 수익**이다. 캡을 조이면 MDD는 거의 언제나 내려가므로(거래가 준다)
    MDD 단독으로는 「잘했다」와 「덜 했다」가 구분되지 않는다(WAN-378).

    MDD가 0에 붙으면(거래가 너무 적어 낙폭이 안 난 칸) 비율이 폭발하므로 **내지 않는다** —
    숫자는 맞는데 읽으면 틀리는 자리다(WAN-115/330/395 부호 함정의 이 축 판).
    """
    import math

    if mdd is None or (isinstance(mdd, float) and math.isnan(mdd)) or mdd < 0.005:
        return "—"
    if ret is None or (isinstance(ret, float) and math.isnan(ret)):
        return "—"
    return f"{ret / mdd:,.2f}"


def _fmt(value: float, digits: int = 4) -> str:
    import math

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:.{digits}f}"


def _pct(value: float, digits: int = 2) -> str:
    import math

    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value * 100:.{digits}f}%"


def render_summary(
    census: pd.DataFrame,
    checks: pd.DataFrame,
    *,
    caps: pd.DataFrame | None = None,
    trades_by_combo: dict[str, dict[str, list[TradeRank]]] | None = None,
    cost_note: str | None = None,
) -> str:
    lines: list[str] = [
        "# WAN-428 부록 — 스토캐스틱 × 오더블록 팔의 존 순위 인구조사",
        "",
        "재현: `uv run python -m backtest.wan428_zone_rank_stoch_arm --jobs 4`"
        " (요약만 `--from-csv`)",
        "",
        "🚨 **§1(채택 북) 표와 셀 비교 금지** — 유니버스·재탭·재진입·익절·손절폭·렌즈 **여섯 축**이"
        " 다르다(모듈 독스트링의 좌표 표). 사용자 요청(2026-09-23)으로 얹은 **부록**이고 §1의"
        " 착수 지시 범위 밖이다.",
        "",
        "🚨 **`zone_limit`을 시그널 경로에 걸지 않았다** — 관측이다. 「그래서 존을 줄이면 낫다」로"
        " 읽지 말 것(WAN-378).",
        "",
        "## ★ 판정 — `Zone Count`를 걸면 MDD가 내려가나 (사용자 질문 2026-09-23)",
        "",
        "🚨 **판정 자는 MDD다.** 캡마다 **후보를 순위로 거르고 북을 다시 배치**했다(라벨 필터가"
        " 아니라 재배치 — 자본·슬롯이 옮겨 간다, WAN-316/389).",
        "",
        "🚨 **거래 수를 반드시 같이 읽을 것** — 거래를 줄이면 MDD가 내려가는 것은 당연하다"
        "(WAN-378). MDD가 **거래 수보다 더 빨리** 내려가야 그 캡이 값을 하는 것이다.",
        "",
        "| 조합 | `Zone Count` | 거래 | 거래당 net R | 승률 | 고정 수익 | **고정 MDD** |"
        " **고정 수익/MDD** | 복리 수익 | 복리 MDD | 복리 수익/MDD |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    if caps is not None and not caps.empty:
        primary = caps[caps.segment == PRIMARY_SEGMENT]
        for _, row in primary.iterrows():
            ruin = " 🚨파산" if bool(row.compound_ruined) else ""
            lines.append(
                f"| `{row.combo}` | {row.cap_name} | {int(row.num_trades):,} | "
                f"{_fmt(float(row.mean_net_r))} ± {_fmt(float(row.se_net_r))} | "
                f"{_pct(float(row.win_rate), 1)} | {_pct(float(row.fixed_return), 0)} | "
                f"**{_pct(float(row.fixed_mdd), 1)}** | "
                f"**{_ratio(float(row.fixed_return), float(row.fixed_mdd))}** | "
                f"{_pct(float(row.compound_return), 0)}{ruin} | "
                f"{_pct(float(row.compound_mdd), 1)} | "
                f"{_ratio(float(row.compound_return), float(row.compound_mdd))} |"
            )
    else:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "🚨 **MDD만 보지 말 것 — `수익/MDD`가 판정 열이다.** 캡을 조이면 거래가 줄어 MDD는"
            " 거의 언제나 내려간다(WAN-378). **같은 위험당 수익이 늘어야** 그 캡이 값을 한 것이고,"
            " `수익/MDD`가 그것을 한 칸에 담는다.",
            "",
            "⚠️ **고정(복리 끔)을 주 열로 읽는다** — 복리 판은 잔고가 커질수록 MDD의 분모가 커져"
            " 팔 사이 비교가 흐려진다(WAN-346 §2 · WAN-169/213 복리 착시). 두 판이 **같은 방향**일"
            " 때만 결론을 낸다.",
            "",
            "### 다른 구간",
            "",
            "| 조합 | `Zone Count` | 구간 | 거래 | 거래당 net R | 고정 수익 | 고정 MDD |"
            " 고정 수익/MDD |",
            "| -- | -- | -- | --: | --: | --: | --: | --: |",
        ]
    )
    if caps is not None and not caps.empty:
        for _, row in caps[caps.segment != PRIMARY_SEGMENT].iterrows():
            lines.append(
                f"| `{row.combo}` | {row.cap_name} | {row.segment} | {int(row.num_trades):,} | "
                f"{_fmt(float(row.mean_net_r))} | {_pct(float(row.fixed_return), 0)} | "
                f"{_pct(float(row.fixed_mdd), 1)} | "
                f"{_ratio(float(row.fixed_return), float(row.fixed_mdd))} |"
            )
    lines.extend(
        [
            "",
            "## 참고 — 순위 분포(이 팔에서 존은 몇 위였나)",
            "",
            "| 조합 | 구간 | 거래 | 거래당 net R | 1위 | 4위 이하 | 11위+ | 30위+ |",
            "| -- | -- | --: | --: | --: | --: | --: | --: |",
        ]
    )
    for combo in COMBOS:
        label = combo_label(combo)
        for segment in RANK_SEGMENTS:
            trades = (trades_by_combo or {}).get(label, {}).get(segment)
            if not trades:
                lines.append(f"| `{label}` | {segment} | — | — | — | — | — | — |")
                continue
            s3 = below_render_limit(trades, limit=RENDER_LIMIT_LOW)
            s10 = below_render_limit(trades, limit=10)
            s30 = below_render_limit(trades, limit=PINE_MAX_ORDER_BLOCKS)
            first = sum(1 for t in trades if t.rank == 1)
            lines.append(
                f"| `{label}` | {segment} | {len(trades):,} | "
                f"{_fmt(_mean([t.net_r for t in trades]))} | {_pct(first / len(trades))} | "
                f"{_pct(s3['trade_share'])} ({s3['net_r_sum']:+,.1f}R) | "
                f"{_pct(s10['trade_share'])} | {_pct(s30['trade_share'])} |"
            )
    lines.extend(
        [
            "",
            "## 순위 분포 — `oos_warm`",
            "",
            "| 조합 | 순위 | 거래 | 몫 | 거래당 net R | net R 합 | 손절률 | 생존 존 | 손절폭 |",
            "| -- | -- | --: | --: | --: | --: | --: | --: | --: |",
        ]
    )
    if not census.empty:
        primary = census[census.segment == PRIMARY_SEGMENT]
        for _, row in primary.iterrows():
            lines.append(
                f"| `{row.combo}` | {row.rank_bucket} | {int(row.num_trades):,} | "
                f"{_pct(float(row.trade_share))} | {_fmt(float(row.mean_net_r))} | "
                f"{float(row.net_r_sum):+,.1f}R | {_pct(float(row.stop_rate), 1)} | "
                f"{_fmt(float(row.mean_alive_zones), 1)} | "
                f"{_pct(float(row.mean_stop_width), 2)} |"
            )
    lines.extend(
        [
            "",
            "📌 **`손절폭` 열이 이 표의 핵심 축이다** — 손절폭 하한이 **존 높이**를 고르는"
            " 필터이므로, 「고순위(오래된) 존이 넓은가」가 순위 분포를 그 필터가 어느 쪽으로"
            " 미는지를 정한다. 순위와 함께 **커지면** 하한 필터가 고순위 거래를 **남기고**,"
            " 평평하면 두 축이 무관하다.",
            "",
            "## 검산",
            "",
            "| 검산 | 조합 | 구간 | 지표 | 이 표 | 상대 | 절대차 |",
            "| -- | -- | -- | -- | --: | --: | --: |",
        ]
    )
    for _, row in checks.iterrows():
        lines.append(
            f"| {row.check} | {row.combo} | {row.segment} | {row.metric} | "
            f"{_fmt(float(row.left), 6)} | {_fmt(float(row.right), 6)} | "
            f"{float(row.abs_diff):.2e} |"
        )
    lines.extend(
        [
            "",
            "## 알려진 한계",
            "",
            "- 🚨🚨 **15m이 없다 — 이 표의 가장 큰 구멍이다**(사용자 지적 2026-09-23). 이 팔의"
            " 좌표는 1h·2h·3h·4h·6h·8h·12h·1d·1w 아홉 개뿐이고, WAN-423도 WAN-424도 **15m을 한"
            " 번도 재지 않았다**. 그런데 **채택 북 거래의 66%가 15m**이다(WAN-312). 그래서 이"
            " 표의 어떤 줄도 15m에 대해 말하지 않는다. 게다가 §1의 나이 측정이 **15m은 같은"
            " 순위에서 존 나이가 3~8배 짧다**고 냈으므로(11위+ 15m 162일 vs 1h 398일) 긴 TF의"
            " 결론을 15m으로 옮길 근거가 없다. 15m 측정은 **WAN-424 후속**이고 이 이슈가 아니다"
            "(31종목 × 15m × 6년 ≈ 8시간+ · 캐시 없음).",
            "- 🚨 **`net R` 개선은 통계적으로 서지 않았다** — 표준오차(±0.05)가 캡이 만든 차"
            "(+0.018)보다 크다. **MDD 하락이 이 표의 발견이고 `net R`은 덤**이다.",
            "- ⚠️ **5점 중 최선 고르기다**(WAN-161) — 다만 `Low(3)`은 **원본 지표의 기본값**이라"
            " 결과를 보고 고른 값이 아니고, 두 조합 모두에서 이긴다.",
            "- 🚨 **이 팔은 채택이 아니다** — WAN-423/424가 **채택 권고 없음**으로 닫았다(매칭 널"
            " 미측정 · 한 좌표 · 복리 MDD 37~95% · 거래당·복리·낙폭이 서로 다른 칸을 가리킴)."
            " 전환은 **재-베이스라인 = 사용자 결정**이다.",
            "- ⚠️ **조합을 두 칸만 본다** — WAN-424가 기준값으로 못 박은 칸이다. 격자를 새로"
            " 뒤지면 그 자체가 탐색이 된다(WAN-161).",
            "- ⚠️ 순위 자는 §1과 **같다**(`select_active`를 실제로 부르는 그 등식) — 다만 이 팔은"
            " **1w까지** 보므로 존 재고 규모가 §1과 크게 다르다(`생존 존` 열).",
            "- **측정·관측 전용 · 기본값·토대 불변** · 핀 없음(WAN-305) · 판단은 북에서(WAN-341) ·"
            " **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변**(이 축은 *몇 개의 존을"
            " 볼까*를 묻는다 — **다른 질문**) · 실거래 보류 유지"
            "(`ALPHABLOCK_LIVE_TRADING=false`).",
        ]
    )
    if cost_note:
        lines.extend(["", f"📌 실측 비용: {cost_note}"])
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    from backtest.wan428_zone_rank_census import trades_frame, trades_from_frame

    parser = argparse.ArgumentParser(description="WAN-428 부록 — 스토캐스틱 팔 존 순위")
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--pilot", action="store_true", help="2종목 × 2TF(배선 확인)")
    parser.add_argument("--from-csv", action="store_true")
    args = parser.parse_args(argv)

    if args.from_csv:
        census, checks = pd.read_csv(CENSUS_CSV), pd.read_csv(CHECKSUM_CSV)
        caps = pd.read_csv(CAP_CSV) if CAP_CSV.exists() else None
        trades = trades_from_frame(pd.read_csv(TRADES_CSV)) if TRADES_CSV.exists() else None
        SUMMARY_MD.write_text(
            render_summary(census, checks, caps=caps, trades_by_combo=trades), encoding="utf-8"
        )
        print(f"[wan428-stoch] 요약 재생성: {SUMMARY_MD}")
        return 0

    symbols = SYMBOLS[:2] if args.pilot else SYMBOLS
    timeframes = ("4h", "1d") if args.pilot else TIMEFRAMES
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    print(
        f"[wan428-stoch] 병렬 {jobs} · 칸 {len(symbols) * len(timeframes)}"
        f" · 캐시 {args.payload_dir}",
        flush=True,
    )
    t0 = time.monotonic()
    rows, checks, trades, caps = run_measure(
        symbols=symbols, timeframes=timeframes, jobs=jobs, payload_dir=args.payload_dir
    )
    elapsed = time.monotonic() - t0
    census_frame = pd.DataFrame([r.model_dump() for r in rows])
    checks_frame = pd.DataFrame([c.model_dump() for c in checks])
    cap_frame = pd.DataFrame([r.model_dump() for r in caps])
    if args.pilot:
        print(cap_frame.to_string(index=False))
        print(checks_frame.to_string(index=False))
        print(f"[wan428-stoch] 파일럿 {elapsed:.0f}s — CSV는 쓰지 않았다(좁혀 돈 판)")
        return 0

    for frame, path in (
        (census_frame, CENSUS_CSV),
        (checks_frame, CHECKSUM_CSV),
        (cap_frame, CAP_CSV),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
    TRADES_CSV.parent.mkdir(parents=True, exist_ok=True)
    trades_frame(trades).to_csv(TRADES_CSV, index=False, compression="gzip")
    note = f"{elapsed:.0f}s = {elapsed / 3600:.2f}시간({len(symbols) * len(timeframes)}칸 · M1)"
    SUMMARY_MD.write_text(
        render_summary(
            census_frame, checks_frame, caps=cap_frame, trades_by_combo=trades, cost_note=note
        ),
        encoding="utf-8",
    )
    print(f"[wan428-stoch] 적재: {CENSUS_CSV} · {SUMMARY_MD} ({note})")
    failed = [c for c in checks if c.abs_diff > 1e-9 and "skipped" not in c.metric]
    for c in failed:
        print(
            f"[wan428-stoch] 🚨 검산 실패: {c.check} / {c.metric} 차 {c.abs_diff:.2e}", flush=True
        )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
