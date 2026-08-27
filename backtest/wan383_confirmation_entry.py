"""WAN-383 §1: 확인된 뒤에 들어가면 나은가 — Phase 1 관문 (팔을 하나도 안 돌린다).

## 묻는 것

존에 닿자마자 들어가지 말고 「떨어지다 멈췄다」가 확인된 뒤에 들어가면 나은가. 지금 우리
거래의 95.8%가 「하락 가속」(진한 빨강)에서 진입하고 그 뭉치가 거래당 −0.14R로 진다
(WAN-372). 사용자 목적은 *「조금 올랐다가 다시 무효화시켜버리는」* 거래를 배제하는 것이다.

## 왜 Phase 1이 먼저인가 — 관문 하나

> **트리거가 발동했을 때, `기준` 팔은 이미 나갔는가?**

가격이 존에서 튀면 ① 기준 팔은 고정 1.5R 목표에 닿고 ② MACD는 연한 빨강이 된다. **「확인
됐다」와 「이미 익절했다」가 한 움직임의 두 얼굴**일 수 있다. 그렇다면 확인 규칙은 *이긴
거래를 통째로 놓치는* 규칙이고, 손익 격자(Phase 2)를 20팔 돌릴 이유가 없다.

📌 이 표는 **팔을 하나도 안 돌린다** — 지금 있는 채택 북 거래의 **경로만** 다시 훑어
(`zone_limit_backtest.scan_confirmation`) 트리거 시각을 재고, 그것을 그 거래의 실제 청산
시각과 비교한다. 비용은 채택 북 **한 팔** + 관측 훑기다.

## 🚨 건수가 아니라 손익 비중으로 센다

사용자 지적(2026-08-27): *"영양가없는 익절이 많으면 또 달라질 수 있는 여지가 있지않나."*
맞다 — 건수로 자르면 시시한 익절과 큰 익절을 똑같이 센다. WAN-336이 같은 방식을 썼다
(「같은 분 익절」이 건수로는 7.37%인데 순손익의 48%였다).

⚠️ **이 좌표의 총 net R은 음수라 「순손익 비중」이 부호를 뒤집어 읽힌다**(마이너스를 마이너스로
나누면 양수가 나온다). 그래서 **버는 쪽과 잃는 쪽을 갈라서** 낸다: `gain_share`(그 무더기가
기준 팔의 **이익 합**에서 차지하는 비율) · `loss_share`(**손실 합**에서 차지하는 비율).
🚨 **판정 자는 `gain_share`다** — 「버는 돈의 몇 %를 버리는가」가 이슈가 물은 것이다.

## 팔 — 진입 시점만 다르다

| 팔 | 트리거 | 진입가 |
| -- | -- | -- |
| `기준` | 탭하면 즉시 | 존 근단(볼린저 재산정) |
| `1` | 상위TF 봉이 **연한 빨강으로 마감** | 그 봉의 확정 종가 |
| `2` | 진한 빨강을 벗어나는 `P*` 터치 | `max(P*, 그 순간 현재가)` |
| `C` | 진입가 대비 **고정 오프셋** 터치 | 그 수준 |

📌 **`C`의 오프셋은 데이터가 정한다** — 팔 `2`가 실제로 기다린 **평균 거리**(`full` 구간
전수)로 못 박는다. 그래야 `2 − C`가 *「MACD가 실제로 더한 값」*이 된다(WAN-131이 볼린저에서
쓴 그 통제 — 그 표는 기여의 84%가 선별이 아니라 그냥 가격이라고 답했다).

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목 · 4TF(15m·1h·2h·4h) 한 지갑 · 못 박은 6년 창 · **존폭 필터 끔**(WAN-384 채택) ·
cap_only 5배 · 재진입 ON(band) · 유동성 한도 채택값 · 익절 메이커 2bp(WAN-370) · 인과
취소(WAN-365) · `baseline` 렌즈.

## 검산

* **(a) 관측 팔 ≡ 채택 북** — 관측이 순수하다면 이 실행의 북 행이 `book_cli.build_book_rows`
  에 채택 값을 **명시로** 넘긴 행과 같아야 한다. 0이 아니면 관측이 대상을 바꾼 것이다.
* **(b) 귀속 = 전수** — 팔마다 네 부류 건수의 합이 그 구간 거래 수와 같아야 한다.
* **(c) 재진입 거래도 관측을 받았다** — 채택 북은 재진입 ON이라(WAN-273) 한쪽만 배선하면
  표가 거래의 상당 부분을 조용히 놓친다(WAN-345 부류). 관측이 안 붙은 재진입 건수를 센다.

재현::

    uv run python -m backtest.wan383_confirmation_entry --jobs 4
    uv run python -m backtest.wan383_confirmation_entry --pilot        # 파일럿 한 칸 견적
    uv run python -m backtest.wan383_confirmation_entry --from-csv     # 요약만
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import ExitReason, Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.zone_limit_backtest import CONFIRMATION_MAX_OFFSET, ConfirmationProbe
from data.models import timeframe_to_ms

REPORTS_DIR = Path("backtest/reports")
CENSUS_CSV_PATH = REPORTS_DIR / "wan383_confirmation_census.csv"
REACH_CSV_PATH = REPORTS_DIR / "wan383_confirmation_reach.csv"
SUMMARY_PATH = REPORTS_DIR / "wan383_confirmation_entry_summary.md"

#: 판정에 쓸 최소 거래 수(WAN-84 유효 기준) — 미달 조각의 극단값은 판정에서 뺀다.
MIN_TRADES = 20

#: 「0과 구분되지 않는다」 선 — WAN-366/370 규약.
NOISE_R = 0.005

ARM_BAR_CLOSE = "1_봉마감"
ARM_CROSS = "2_교차"
ARM_OFFSET = "C_고정오프셋"
ARM_ORDER: tuple[str, ...] = (ARM_BAR_CLOSE, ARM_CROSS, ARM_OFFSET)

CAT_AFTER_TP = "트리거 전에 이미 익절"
CAT_AFTER_SL = "트리거 전에 이미 손절"
CAT_HOLDING = "아직 들고 있음"
CAT_NO_TRIGGER = "트리거 안 옴"
CATEGORY_ORDER: tuple[str, ...] = (CAT_AFTER_TP, CAT_AFTER_SL, CAT_HOLDING, CAT_NO_TRIGGER)

AXIS_OVERALL = "overall"
AXIS_TIMEFRAME = "timeframe"

Pair = tuple[Trade, PlacedSetup]


# --------------------------------------------------------------------------- #
# 트리거 판독
# --------------------------------------------------------------------------- #


def arm_trigger(
    probe: ConfirmationProbe | None, arm: str, *, offset: float
) -> tuple[int, float] | None:
    """이 팔의 (트리거 시각, **실제로 물릴 진입가**). 트리거가 안 왔으면 `None`.

    🚨 팔 `2`의 진입가는 `P*`가 아니라 `max(P*, 그 순간 현재가)`다 — `P*`가 이미 현재가
    아래면(시그널선 따라잡기) 지정가로 걸어도 즉시 시장가로 체결된다. 그 부류를 `P*`로
    체결시키면 **없는 가격 이점을 지어내는 것**이고, 이 이슈가 지는 가장 흔한 방식이
    「비용을 실제보다 싸게 잡는 것」이다(WAN-370).
    """
    if probe is None:
        return None
    if arm == ARM_BAR_CLOSE:
        if probe.bar_close_time is None or probe.bar_close_price is None:
            return None
        return probe.bar_close_time, probe.bar_close_price
    if arm == ARM_CROSS:
        if probe.cross_time is None or probe.cross_price is None:
            return None
        ref = probe.cross_ref_price if probe.cross_ref_price is not None else probe.entry_price
        return probe.cross_time, max(probe.cross_price, ref)
    if arm == ARM_OFFSET:
        return probe.first_touch(offset)
    raise ValueError(f"알 수 없는 팔: {arm!r}")


def categorize(pair: Pair, arm: str, *, offset: float) -> str:
    """네 부류 중 하나 — 트리거 시각을 **기준 팔의 실제 청산 시각**과 비교한다.

    ⚠️ **같은 1분(`==`)은 「아직 들고 있음」으로 넣는다** — 1분봉은 그 1분 **안의 순서**를
    모르는데(WAN-336), 여기서의 보수적 선택은 **확인 팔에 유리한 쪽**이다. 그래야 그러고도
    판정이 「닫아라」로 나오면 그 판정이 순서 가정에 안 기댄다. 그 건수는 `reach_rows`가
    따로 세어 보인다(`same_minute`).
    """
    trade, placement = pair
    trigger = arm_trigger(placement.confirmation, arm, offset=offset)
    if trigger is None:
        return CAT_NO_TRIGGER
    trigger_time, _price = trigger
    if trigger_time <= trade.exit_time:
        return CAT_HOLDING
    return CAT_AFTER_TP if trade.exits[-1].reason is ExitReason.TAKE_PROFIT else CAT_AFTER_SL


def mean_cross_offset(segments: Sequence[BookSegment], *, segment: str) -> float:
    """팔 `C`의 오프셋 = 팔 `2`가 실제로 기다린 **평균 상대 거리** (`segment` 구간 전수).

    음수(가격이 오히려 내린 자리에서 발동)는 「위에 거는 트리거」로 표현되지 않으므로 0에서
    자른다 — 그 부류의 비중은 `reach_rows`의 `no_rise_share`가 따로 보인다. 사다리가 기록한
    상한을 넘으면 조회가 `None`을 내므로(지어내지 않는다) 여기서 **거부**한다.
    """
    distances: list[float] = []
    for seg in segments:
        if seg.segment != segment:
            continue
        for _trade, placement in seg.trades_with_placements():
            probe = placement.confirmation
            trigger = arm_trigger(probe, ARM_CROSS, offset=0.0)
            if probe is None or trigger is None:
                continue
            distances.append((trigger[1] - probe.entry_price) / probe.entry_price)
    if not distances:
        return 0.0
    mean = max(0.0, statistics.fmean(distances))
    if mean > CONFIRMATION_MAX_OFFSET:
        raise ValueError(
            f"팔 2의 평균 거리 {mean:.4%}가 사다리 상한 "
            f"{CONFIRMATION_MAX_OFFSET:.2%}를 넘습니다 — 팔 C의 첫 터치를 답할 수 없습니다."
        )
    return mean


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CensusRow(BaseModel):
    """한 (구간, 축, 버킷, 팔, 부류)의 건수 + 손익 비중. 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    segment: str
    axis: str
    bucket: str
    arm: str
    category: str
    num_trades: int
    trade_share: float
    """이 버킷 안에서 이 부류가 차지하는 **건수** 비율."""
    net_r_sum: float
    gain_share: float
    """🚨 **판정 자** — 기준 팔의 **이익 합**(양의 net R 합)에서 이 부류가 차지하는 비율."""
    loss_share: float
    """기준 팔의 **손실 합**(음의 net R 합)에서 이 부류가 차지하는 비율."""
    win_rate: float


class ReachRow(BaseModel):
    """한 (구간, 축, 버킷, 팔)의 도달률·거리·손절폭 배수 — 완료기준 1의 나머지 열."""

    model_config = ConfigDict(frozen=True)

    segment: str
    axis: str
    bucket: str
    arm: str
    num_trades: int
    triggered: int
    reach_rate: float
    same_minute: int
    """트리거와 기준 팔 청산이 **같은 1분**인 건수 — 순서를 모르는 부류(위 `categorize` 참고)."""
    median_rise_pct: float | None
    """트리거 가격이 기준 팔 진입가보다 몇 % 위인가(중앙값)."""
    median_stop_multiple: float | None
    """그래서 손절폭이 **몇 배**가 되는가(중앙값) — `(트리거−손절선) / (진입가−손절선)`."""
    no_rise_share: float | None
    """팔 `2` 전용 — 가격이 오르지 않았는데 발동한 비율(시그널선 따라잡기 = EMA 산수)."""
    missed_trades: int
    """트리거가 안 온 셋업 수."""
    missed_win_rate: float | None
    missed_mean_net_r: float | None
    """🚨 그 셋업들이 기준 팔에서 **이겼는가** — 이겼으면 확인이 좋은 거래를 버리는 것이다
    (RSI 게이트가 정확히 그랬다 — WAN-114/123)."""
    window_closed_share: float
    """「안 왔다」가 뜻을 갖는 비율(무효화까지 봤거나 세 팔이 전부 결판난 셋업).

    낮으면 그 구간의 「안 옴」에 **창 오른쪽 절단**이 섞여 있다는 뜻이다."""


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _census(
    pairs: Sequence[Pair], *, segment: str, axis: str, bucket: str, offset: float
) -> list[CensusRow]:
    total = len(pairs)
    net_rs = {id(t): net_r(t, p) for t, p in pairs}
    gain_total = sum(v for v in net_rs.values() if v > 0)
    loss_total = -sum(v for v in net_rs.values() if v < 0)
    rows: list[CensusRow] = []
    for arm in ARM_ORDER:
        groups: dict[str, list[Pair]] = {c: [] for c in CATEGORY_ORDER}
        for pair in pairs:
            groups[categorize(pair, arm, offset=offset)].append(pair)
        for category in CATEGORY_ORDER:
            group = groups[category]
            num = len(group)
            values = [net_rs[id(t)] for t, _p in group]
            gain = sum(v for v in values if v > 0)
            loss = -sum(v for v in values if v < 0)
            rows.append(
                CensusRow(
                    segment=segment,
                    axis=axis,
                    bucket=bucket,
                    arm=arm,
                    category=category,
                    num_trades=num,
                    trade_share=(num / total) if total else 0.0,
                    net_r_sum=sum(values),
                    gain_share=(gain / gain_total) if gain_total > 0 else 0.0,
                    loss_share=(loss / loss_total) if loss_total > 0 else 0.0,
                    win_rate=(sum(1 for t, _p in group if t.is_win) / num) if num else 0.0,
                )
            )
    return rows


def _reach(
    pairs: Sequence[Pair], *, segment: str, axis: str, bucket: str, offset: float
) -> list[ReachRow]:
    rows: list[ReachRow] = []
    total = len(pairs)
    for arm in ARM_ORDER:
        rises: list[float] = []
        multiples: list[float] = []
        triggered = same_minute = no_rise = 0
        missed: list[Pair] = []
        closed = 0
        for trade, placement in pairs:
            probe = placement.confirmation
            if probe is not None and probe.window_closed:
                closed += 1
            trigger = arm_trigger(probe, arm, offset=offset)
            if trigger is None or probe is None:
                missed.append((trade, placement))
                continue
            triggered += 1
            trigger_time, price = trigger
            if trigger_time == trade.exit_time:
                same_minute += 1
            rises.append((price - probe.entry_price) / probe.entry_price)
            risk = probe.entry_price - placement.stop_price
            if risk > 0:
                multiples.append((price - placement.stop_price) / risk)
            if arm == ARM_CROSS and probe.cross_price is not None:
                ref = (
                    probe.cross_ref_price
                    if probe.cross_ref_price is not None
                    else probe.entry_price
                )
                if probe.cross_price <= ref:
                    no_rise += 1
        missed_values = [net_r(t, p) for t, p in missed]
        rows.append(
            ReachRow(
                segment=segment,
                axis=axis,
                bucket=bucket,
                arm=arm,
                num_trades=total,
                triggered=triggered,
                reach_rate=(triggered / total) if total else 0.0,
                same_minute=same_minute,
                median_rise_pct=statistics.median(rises) if rises else None,
                median_stop_multiple=statistics.median(multiples) if multiples else None,
                no_rise_share=((no_rise / triggered) if (arm == ARM_CROSS and triggered) else None),
                missed_trades=len(missed),
                missed_win_rate=(
                    sum(1 for t, _p in missed if t.is_win) / len(missed) if missed else None
                ),
                missed_mean_net_r=(statistics.fmean(missed_values) if missed_values else None),
                window_closed_share=(closed / total) if total else 0.0,
            )
        )
    return rows


def rows_for_segment(
    segment: BookSegment, *, offset: float
) -> tuple[list[CensusRow], list[ReachRow]]:
    """한 구간의 두 축(전체·TF) 행."""
    pairs = segment.trades_with_placements()
    census = _census(
        pairs, segment=segment.segment, axis=AXIS_OVERALL, bucket="전체", offset=offset
    )
    reach = _reach(pairs, segment=segment.segment, axis=AXIS_OVERALL, bucket="전체", offset=offset)
    by_tf: dict[str, list[Pair]] = {}
    for trade, placement in pairs:
        by_tf.setdefault(placement.cell[1], []).append((trade, placement))
    for timeframe, group in sorted(by_tf.items(), key=lambda kv: timeframe_to_ms(kv[0])):
        census += _census(
            group, segment=segment.segment, axis=AXIS_TIMEFRAME, bucket=timeframe, offset=offset
        )
        reach += _reach(
            group, segment=segment.segment, axis=AXIS_TIMEFRAME, bucket=timeframe, offset=offset
        )
    return census, reach


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
) -> list[CellPayload]:
    """채택 북의 칸 후보 — **관측만 켠 채택 좌표**(WAN-305, 핀 없음).

    `observe_confirmation=True`가 base 후보와 재진입 후보 **양쪽에** 걸린다(`run_cells` →
    `_Task`). 순수 관측이라 후보 집합·손익은 안 켠 실행과 비트 단위로 같다(검산 (a)).
    존폭 필터는 **핀하지 않는다** — 채택 기본값이 곧 「끔」이다(WAN-384).
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        observe_confirmation=True,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )


def place_book(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str] = SEGMENT_ORDER,
) -> list[BookSegment]:
    """채택 북 배치 — `book_cli.run_book`과 **같은 함수·같은 인자**다."""
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def verify_adopted(
    payloads: Sequence[CellPayload],
    book: Sequence[BookSegment],
    *,
    start_ms: int,
    end_ms: int,
) -> float:
    """검산 (a) — 이 실행의 북 ≡ `build_book_rows`에 채택 값을 **명시로** 넘긴 행."""
    from backtest.book_cli import build_book_rows

    proxied, _note = apply_funding_proxy(payloads)
    reference = {
        r.segment: r
        for r in build_book_rows(
            proxied,
            book=LeverageBookParams(),
            segments=[s.segment for s in book],
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
            take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        )
    }
    worst = 0.0
    for seg in book:
        other = reference[seg.segment]
        worst = max(
            worst,
            abs(seg.row.total_return - other.total_return),
            abs(seg.row.max_drawdown - other.max_drawdown),
            float(abs(seg.row.num_trades - other.num_trades)),
        )
    return worst


def coverage_gap(book: Sequence[BookSegment], frame: pd.DataFrame) -> float:
    """검산 (b) — 팔마다 네 부류 건수의 합이 그 구간 거래 수와 같은가(최대 절대차)."""
    worst = 0.0
    for seg in book:
        part = frame[(frame["segment"] == seg.segment) & (frame["axis"] == AXIS_OVERALL)]
        for arm in ARM_ORDER:
            counted = int(part[part["arm"] == arm]["num_trades"].sum())
            worst = max(worst, float(abs(counted - len(seg.outcome.trades))))
    return worst


def unobserved_reentries(book: Sequence[BookSegment]) -> int:
    """검산 (c) — 재진입 거래 중 관측이 안 붙은 건수 (WAN-345 부류의 동작 가드).

    🚨 인자를 넘기는 줄을 보는 게 아니라 **재진입 거래에 실제로 관측이 붙었는지**를 센다.
    """
    return sum(
        1
        for seg in book
        for _t, p in seg.trades_with_placements()
        if p.is_reentry and p.confirmation is None
    )


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    log: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[BookSegment], dict[str, float]]:
    """4갈래 인구조사 + 도달률 표 + **배치된 북** + 검산값.

    🚨 북을 함께 돌려주는 이유는 호출부가 그것 때문에 격자를 **한 번 더 돌지 않게** 하기
    위해서다 — 이 좌표에서 후보 생성이 비용의 전부라(WAN-372 실측: 48칸 8,156초 중 8,148초)
    「요약을 쓰려고 다시 만들기」는 실행 시간을 두 배로 만든다.
    """
    started = time.monotonic()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = build_payloads(symbols, timeframes, start=start, end=end, jobs=jobs)
    if log:
        print(f"[wan383] 후보 생성 {time.monotonic() - started:.0f}s", flush=True)

    book = place_book(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments)
    offset = mean_cross_offset(book, segment=harness.SEGMENT_FULL)
    if log:
        print(f"[wan383] 팔 C 오프셋 = 팔 2 평균 거리 {offset:.4%}", flush=True)

    census: list[CensusRow] = []
    reach: list[ReachRow] = []
    for seg in book:
        c_rows, r_rows = rows_for_segment(seg, offset=offset)
        census += c_rows
        reach += r_rows
    census_frame = pd.DataFrame([r.model_dump() for r in census])
    reach_frame = pd.DataFrame([r.model_dump() for r in reach])

    checks = {
        "adopted_identity": verify_adopted(payloads, book, start_ms=start_ms, end_ms=end_ms),
        "coverage_gap": coverage_gap(book, census_frame),
        "unobserved_reentries": float(unobserved_reentries(book)),
        "arm_c_offset": offset,
    }
    if log:
        print(f"[wan383] 총 {time.monotonic() - started:.0f}s · 검산 {checks}", flush=True)
    return census_frame, reach_frame, book, checks


# --------------------------------------------------------------------------- #
# 판정 — §2의 갈림을 **착수 전에** 못 박은 그대로 코드로 옮긴다
# --------------------------------------------------------------------------- #

#: 「손익 비중이 압도적이다」의 선 — 기준 팔이 **버는 돈의 절반 이상**을 그 규칙이 버리면
#: 그 팔은 여기서 끝난다(§2 첫째 갈림). 🚨 이 선은 판정 **전에** 정해 놓은 값이고, 표를
#: 보고 옮기지 않는다.
OVERWHELMING_GAIN_SHARE = 0.50

#: 「도달률이 매우 낮다」의 선 — 이보다 낮으면 표본이 무너져 Phase 2가 얇은 조각의 극단값을
#: 재게 된다(§2 셋째 갈림).
MIN_REACH_RATE = 0.20


def _overall(frame: pd.DataFrame, segment: str) -> pd.DataFrame:
    return frame[(frame["segment"] == segment) & (frame["axis"] == AXIS_OVERALL)]


def baseline_mean_net_r(book: Sequence[BookSegment], segment: str) -> float | None:
    """그 구간 기준 팔의 거래당 net R — 「놓친 셋업이 오히려 이겼나」의 비교 대상."""
    for seg in book:
        if seg.segment != segment:
            continue
        pairs = seg.trades_with_placements()
        if not pairs:
            return None
        return statistics.fmean(net_r(t, p) for t, p in pairs)
    return None


def arm_verdict(
    census: pd.DataFrame,
    reach: pd.DataFrame,
    *,
    arm: str,
    segment: str,
    base_mean_net_r: float | None,
) -> tuple[bool, str]:
    """(살아남았나, 판정 한 문장) — §2의 갈림 그대로."""
    c = _overall(census, segment)
    c = c[(c["arm"] == arm) & (c["category"] == CAT_AFTER_TP)]
    r = _overall(reach, segment)
    r = r[r["arm"] == arm]
    if c.empty or r.empty:
        return False, f"**{arm}** — 판정 불가(행 없음)."

    gain_share = float(c["gain_share"].iloc[0])
    after_tp_trades = int(c["num_trades"].iloc[0])
    reach_rate = float(r["reach_rate"].iloc[0])
    triggered = int(r["triggered"].iloc[0])
    missed = int(r["missed_trades"].iloc[0])
    missed_mean = r["missed_mean_net_r"].iloc[0]
    stop_multiple = r["median_stop_multiple"].iloc[0]

    head = (
        f"**{arm}** — 도달률 {reach_rate:.1%}({triggered}건) · "
        f"「이미 익절한 뒤 트리거」 {after_tp_trades}건이 기준 팔 **이익의 "
        f"{gain_share:.1%}**"
    )
    if stop_multiple is not None and not pd.isna(stop_multiple):
        head += f" · 손절폭 {float(stop_multiple):.2f}배"
    head += ". "

    if gain_share >= OVERWHELMING_GAIN_SHARE:
        return False, head + (
            f"→ **탈락**: 버는 돈의 {gain_share:.0%}를 버리는 규칙이다"
            f"(§2 첫째 갈림, 선 {OVERWHELMING_GAIN_SHARE:.0%})."
        )
    if reach_rate < MIN_REACH_RATE:
        return False, head + (
            f"→ **탈락**: 도달률이 선({MIN_REACH_RATE:.0%})보다 낮아 표본이 무너진다(§2 셋째 갈림)."
        )
    if (
        missed
        and missed_mean is not None
        and not pd.isna(missed_mean)
        and base_mean_net_r is not None
        and float(missed_mean) > base_mean_net_r + NOISE_R
    ):
        return False, head + (
            f"→ **탈락**: 트리거가 안 온 {missed}건이 기준 팔에서 오히려 이겼다"
            f"(거래당 {float(missed_mean):+.4f}R vs 전체 {base_mean_net_r:+.4f}R) — 확인이 "
            "좋은 거래를 버린다(§2 넷째 갈림 · RSI 게이트가 정확히 그랬다, WAN-114/123)."
        )
    return True, head + "→ **생존**: §1로는 못 닫는다 — §3(Phase 2 손익)으로 넘긴다."


def verdict(
    census: pd.DataFrame,
    reach: pd.DataFrame,
    book: Sequence[BookSegment],
    *,
    segment: str = PRIMARY_OOS,
) -> tuple[list[str], list[str]]:
    """(팔별 판정 문장, 살아남은 팔) — 살아남은 팔이 없으면 Phase 2를 안 돌리고 닫는다."""
    base = baseline_mean_net_r(book, segment)
    sentences: list[str] = []
    survivors: list[str] = []
    for arm in ARM_ORDER:
        alive, sentence = arm_verdict(census, reach, arm=arm, segment=segment, base_mean_net_r=base)
        sentences.append(sentence)
        if alive:
            survivors.append(arm)
    return sentences, survivors


# --------------------------------------------------------------------------- #
# 요약 렌더
# --------------------------------------------------------------------------- #


def _census_table(frame: pd.DataFrame, segment: str, arm: str) -> str:
    part = _overall(frame, segment)
    part = part[part["arm"] == arm]
    lines = [
        "| 무더기 | 건수 | 건수 비중 | net R 합 | **이익 비중** | 손실 비중 | 승률 |",
        "| -- | --: | --: | --: | --: | --: | --: |",
    ]
    for category in CATEGORY_ORDER:
        row = part[part["category"] == category]
        if row.empty:
            continue
        r = row.iloc[0]
        mark = "**" if category == CAT_AFTER_TP else ""
        lines.append(
            f"| {category} | {int(r['num_trades'])} | {float(r['trade_share']):.1%} | "
            f"{float(r['net_r_sum']):+.1f}R | {mark}{float(r['gain_share']):.1%}{mark} | "
            f"{float(r['loss_share']):.1%} | {float(r['win_rate']):.1%} |"
        )
    return "\n".join(lines)


def _reach_table(frame: pd.DataFrame, segment: str, *, axis: str = AXIS_OVERALL) -> str:
    part = frame[(frame["segment"] == segment) & (frame["axis"] == axis)]
    lines = [
        "| 축 | 팔 | 거래 | 트리거 | 도달률 | 같은 1분 | 상승폭(중앙) | 손절폭 배수 | "
        "상승없이 발동 | 놓친 셋업 | 놓친 승률 | 놓친 net R | 창 닫힘 |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]

    def fmt(value: float | None, spec: str) -> str:
        if value is None or pd.isna(value):
            return "—"
        return format(float(value), spec)

    for _i, r in part.iterrows():
        lines.append(
            f"| {r['bucket']} | {r['arm']} | {int(r['num_trades'])} | {int(r['triggered'])} | "
            f"{float(r['reach_rate']):.1%} | {int(r['same_minute'])} | "
            f"{fmt(r['median_rise_pct'], '+.3%')} | {fmt(r['median_stop_multiple'], '.2f')}배 | "
            f"{fmt(r['no_rise_share'], '.1%')} | {int(r['missed_trades'])} | "
            f"{fmt(r['missed_win_rate'], '.1%')} | {fmt(r['missed_mean_net_r'], '+.4f')}R | "
            f"{float(r['window_closed_share']):.1%} |"
        )
    return "\n".join(lines)


def render_summary(
    census: pd.DataFrame,
    reach: pd.DataFrame,
    book: Sequence[BookSegment],
    checks: dict[str, float],
) -> str:
    sentences, survivors = verdict(census, reach, book)
    base = baseline_mean_net_r(book, PRIMARY_OOS)
    offset = checks.get("arm_c_offset", 0.0)
    out = [
        "# WAN-383 §1 — 확인된 뒤에 들어가면 나은가 (Phase 1 관문)",
        "",
        "> **팔을 하나도 안 돌린 표다.** 지금 있는 채택 북 거래의 경로만 다시 훑어 확인 진입",
        "> 세 팔의 **트리거 시각**을 재고, 그것을 그 거래의 **실제 청산 시각**과 비교한다.",
        "",
        f"좌표: 12종목 × 4TF 한 지갑 · 못 박은 6년 창 · 존폭 필터 **끔**(WAN-384) · "
        f"cap_only 5배 · 재진입 ON(band) · 익절 메이커 2bp · `baseline` 렌즈 · **핀 없음**. "
        f"주 구간 `{PRIMARY_OOS}`.",
        "",
        f"📌 **팔 `C`의 오프셋 = {offset:.4%}** — 팔 `2`가 실제로 기다린 평균 거리(`full` 전수)로 "
        "못 박았다. 그래야 `2 − C`가 「MACD가 실제로 더한 값」이 된다(WAN-131 통제).",
        "",
        "## 판정",
        "",
    ]
    out += [f"- {s}" for s in sentences]
    out += [""]
    if survivors:
        out += [
            f"➡️ **살아남은 팔: {', '.join(survivors)}** — §3(Phase 2 손익 격자)로 넘긴다.",
            "",
        ]
    else:
        out += [
            "➡️ **살아남은 팔이 없다 — Phase 2를 안 돌리고 닫는다.** §2가 착수 전에 못 박은",
            "갈림 그대로다(*「처방을 미리 정해 놓고 재지 마십시오」*). **그것도 결론이다.**",
            "",
        ]
    if base is not None:
        out += [f"기준 팔 거래당 net R(`{PRIMARY_OOS}`) = **{base:+.4f}R**.", ""]

    for segment in (PRIMARY_OOS, harness.SEGMENT_FULL):
        out += [f"## 4갈래 인구조사 — `{segment}`", ""]
        for arm in ARM_ORDER:
            out += [f"### 팔 `{arm}`", "", _census_table(census, segment, arm), ""]
        out += [f"## 도달률·거리 — `{segment}` (전체)", "", _reach_table(reach, segment), ""]
        out += [
            f"### TF별 — `{segment}`",
            "",
            _reach_table(reach, segment, axis=AXIS_TIMEFRAME),
            "",
        ]

    out += [
        "## 읽는 법 — 함정 셋",
        "",
        "1. 🚨 **판정 자는 「이익 비중」이지 건수가 아니다.** 시시한 익절을 많이 놓치는 것과 "
        "큰 익절을 놓치는 것은 다르다(WAN-336: 「같은 분 익절」이 건수 7.37% ↔ 순손익 48%).",
        "2. ⚠️ **총 net R이 음수라 「순손익 비중」은 부호가 뒤집혀 읽힌다** — 그래서 버는 쪽"
        "(`이익 비중`)과 잃는 쪽(`손실 비중`)을 갈라서 냈다.",
        "3. ⚠️ **「같은 1분」은 확인 팔에 유리한 쪽으로 넣었다**(「아직 들고 있음」). 1분봉은 그 "
        "1분 안의 순서를 모르므로(WAN-336), 그러고도 판정이 「닫아라」면 그 판정은 순서 "
        "가정에 안 기댄다. 그 건수는 도달률 표의 `같은 1분` 열이다.",
        "",
        "## 검산",
        "",
        "| 검산 | 값 | 뜻 |",
        "| -- | --: | -- |",
        f"| (a) 관측 팔 ≡ 채택 북 | {checks['adopted_identity']:.2e} | 0이 아니면 관측이 "
        "대상을 바꾼 것이다 |",
        f"| (b) 귀속 = 전수 | {checks['coverage_gap']:.0f} | 팔마다 네 부류 합 = 구간 거래 수 |",
        f"| (c) 관측 없는 재진입 | {checks['unobserved_reentries']:.0f} | 0이 아니면 재진입 "
        "거래가 표에서 빠진 것이다(WAN-345 부류) |",
        "",
        "## 범위 밖 · 경고",
        "",
        "- 전부 `baseline`(닿으면 체결) 렌즈 위의 값이고 체결 보수화(`pen_5bp`)는 범위 밖이다.",
        "- **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 표는 *어느 시점에 "
        "들어가나*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 않는다.",
        "- 기본값·토대 불변(`ConfluenceParams()`·`LeverageBookParams()`) · 실거래 보류 유지"
        "(`ALPHABLOCK_LIVE_TRADING=false`).",
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _write(
    census: pd.DataFrame,
    reach: pd.DataFrame,
    book: Sequence[BookSegment],
    checks: dict[str, float],
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    census.to_csv(CENSUS_CSV_PATH, index=False)
    reach.to_csv(REACH_CSV_PATH, index=False)
    SUMMARY_PATH.write_text(render_summary(census, reach, book, checks), encoding="utf-8")
    print(f"[wan383] {CENSUS_CSV_PATH} · {REACH_CSV_PATH} · {SUMMARY_PATH}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-383 §1 확인 진입 Phase 1 관문")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="파일럿 한 칸(BTC 15m)만 돌려 견적을 낸다 — 48칸 외삽 전에 반드시 먼저.",
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 안 돌리고 저장된 CSV로 요약만 재생성한다(검산은 CSV에서 못 낸다).",
    )
    args = parser.parse_args(argv)

    if args.from_csv:
        census = pd.read_csv(CENSUS_CSV_PATH)
        reach = pd.read_csv(REACH_CSV_PATH)
        # 북이 없으면 기준 net R을 못 내므로 판정 문장만 CSV로 다시 낸다.
        print(_census_table(census, PRIMARY_OOS, ARM_CROSS))
        print()
        print(_reach_table(reach, PRIMARY_OOS))
        return 0

    symbols = [s for s in args.symbols.split(",") if s]
    timeframes = [t for t in args.timeframes.split(",") if t]
    if args.pilot:
        symbols, timeframes = [harness.DEFAULT_SYMBOLS[0]], ["15m"]
        print(f"[wan383] 파일럿: {symbols[0]} × 15m 한 칸 (가장 비싼 TF)", flush=True)

    census, reach, book, checks = run_report(
        symbols,
        timeframes,
        start=args.start,
        end=args.end,
        jobs=args.jobs,
    )
    if args.pilot:
        print(census.to_string())
        print(reach.to_string())
        return 0

    _write(census, reach, book, checks)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
