"""WAN-402 §0 — 「같이 깨지는 중인가」: 진입하는 그 순간 다른 종목 존들도 함께 무효화되고 있나.

## 한 줄

사용자 결정(2026-09-15)으로 이 이슈는 §0 **축 하나**부터 잰다 — `concurrent_breaks`. 존 기하
축은 두 번 죽었고(WAN-403 위치 · WAN-375 폭), 탭 순간 시장 축은 상관 ≈0, 상위 TF 축은 WAN-126이
닫았다. 독립 신호가 남아 있는 축은 이것 하나다(WAN-408: 1h 버킷 5종목+ 동시 손절이 널 대비
2,876배). 묻는 것은 *「이 존이 약해서」와 「시장 전체가 무너져서」를 가를 수 있는가*이고,
판정 자는 **손절률(a)과 거래당 net R(b)을 병기하되 결론은 (b)** 다(WAN-154: 「안 뚫리는 존」과
「돈 버는 존」이 갈린 실측이 있다).

## 정의 — 착수 전에 못 박음 (이슈 코멘트 2026-09-15 그대로)

* **세는 사건** = **다른 종목 강세 존의 무효화**(주 · `OrderBlock.break_time`) ＋ **다른 칸의
  손절**(보조 · 모집단 자체에서). 존 무효화는 우리가 거래를 안 해도 일어나 항상 정의되고 표본이
  크다. 롱 온리라 **강세 존**만 센다(약세 존이 깨지는 것은 가격이 오르는 사건이다).
* **자기 제외** = 같은 종목은 **전 TF** 제외(안 빼면 「내 존이 깨지는 중」= 「내가 손절날 것」
  동어반복).
* **창** = 진입 직전 **1h · 4h · 24h** 셋 고정(WAN-408이 쓴 버킷 폭과 같은 자 — 단 여기서는 KST
  정렬 버킷이 아니라 진입 시각을 오른쪽 끝으로 하는 **뒤로 향한 창**이다). 🚨 자유 파라미터라
  세 점에서 못 박고 **다중검정에서 3개로 센다** — 결과를 보고 창을 늘리면 WAN-161.
* **TF** = 모든 TF 합산(「시장이 무너지는가」를 보는 것이라 TF를 안 가린다).
* **정규화** = 건수와 **그 시각 살아 있던 존 대비 비율** 둘 다(존 재고가 시간에 따라 변해 건수만
  으로는 뜻이 갈린다). 분모는 **창 시작 시점에 살아 있던 다른 종목 강세 존 수**(= 그 창에서 깨질
  수 있던 존).
* 🚨 **인과성** = 진입 시각 `t` 이전에 **닫힌 봉**에서 확정된 무효화만. 무효화는 봉이 닫혀야
  발효한다(WAN-365 `bar_close`) — 존의 `break_time`은 깨진 봉의 `open_time`이므로 **알려지는
  시각 = `break_time + TF`**이고, 창은 그 시각으로 `(t − W, t]`를 본다. 안 지키면 WAN-364 부류
  룩어헤드다. 존의 확정도 같다(`confirmed_time + TF`부터 「살아 있다」).
* **진입 시각 `t`** = 체결 시각(`entry_time`). 주문은 탭에서 걸리지만 「들어가는 순간」에 아는
  정보가 이 축의 뜻이다.

## 모집단 — 새로 만들지 않는다 (WAN-375가 이미 만들었다)

`backtest/cache/wan375/setups.csv.gz`의 **`band` 팔 · 첫 탭만(`tap_index == 0` =
`retap_mode="once"`와 같은 셋업, WAN-375 검산 (d)) · 손절폭 가드 통과(≥ 0.3%) · 익절 켠 판
복원(`tp_on_reason`, 검산 (b)로 채택 엔진 후보와 같음이 증명됨) · 데이터 끝 제외**. 재진입은
셋업 층에 **정의상 없다**. 🚨 **이 표는 채택 좌표가 아니다** — 재탭·재진입을 뺐으므로 페이퍼가
실제로 하는 매매와 다르다(WAN-305). 여기 수치를 채택 북 성적과 나란히 놓지 말 것.

거래당 net R은 WAN-375의 `cost_r`(채택 회계 — 진입 메이커 · 익절 메이커 · 손절 테이커＋슬리피지,
WAN-370/396)로 셋업마다 계산한다(펀딩 제외 · 셋업 층).

## 판정 — 착수 전에 코드 상수로 못 박음 (`verdict_for_window`)

`concurrent_breaks` **비율**을 앞구간(`is`) 5분위로 자르고 그 경계를 뒷구간(`oos_warm`)에
그대로 적용한다(문턱을 뒷구간에서 고르지 않는다). 셋 다일 때만 「간다」:

1. 앞구간에서 분위별 거래당 net R이 **단조**(방향은 가설대로 — 많이 깨질수록 나쁘다).
2. 뒷구간에서 최고·최저 분위 차의 **부호가 같다**.
3. 뒷구간 최고·최저 분위 net R 차가 **2σ 밖**(WAN-412 `_sign_is_decided` 자)이되, 창이 셋이라
   그 2σ 기준을 **Bonferroni로 3개로 센다**(`decision_z()`).

분위가 3개 미만으로 뭉치거나(1h 창은 0이 많다) 최고·최저 분위 표본이 100 미만이면 **판정
불가**로 찍는다 — 지어내지 않는다(WAN-367).

## 🚨 가장 중요한 통제 — 이미 있는 축의 다른 이름인가

WAN-410이 「그날 실현 net R 누적」(`settled` 규약 · KST 하루)으로 같은 종류의 것을 이미 쟀다.
`concurrent_breaks`가 그것의 **대리변수**일 뿐이면 새로 얻는 게 없다. 그래서 (1) 두 축의 교차표,
(2) WAN-410 축을 **통제한 뒤에도** 남는가(같은 누적손실 버킷 안에서 다시 쪼개기), (3) 폭락일
(WAN-408 최악 10일)을 빼고도 남는가를 함께 낸다. 실현 누계는 **WAN-410의 그 함수**
(`realized_r_before`)를 이 모집단 위에서 부른다(자를 두 벌로 적지 않는다).

## 재현

    uv run python -m backtest.wan402_concurrent_breaks --pilot            # BTC 4h 한 칸 + 비용
    uv run python -m backtest.wan402_concurrent_breaks --jobs 4           # 전체(존 대장 + 라벨링)
    uv run python -m backtest.wan402_concurrent_breaks --from-csv         # 요약만

측정 전용 · 엔진·기본값·토대 불변 · 필터를 만들지 않는다(§0은 「신호가 있는가」까지 —
WAN-341 · WAN-323).
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist

import pandas as pd

from backtest import harness
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan375_conditional_rr import (
    ARM_BAND,
    SETUPS_CSV,
    CostRates,
    cost_r,
    worst_days,
)
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan408_loss_clustering import (
    REALIZED_BUCKETS,
    TradeFact,
    _bucket_label_realized,
    _bucket_order_realized,
)
from backtest.wan410_daily_loss_circuit_breaker import realized_r_before
from common.timefmt import kst_day_key
from strategy.models import OrderBlockDirection, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
BINS_CSV = REPORTS_DIR / "wan402_concurrent_breaks_bins.csv"
VERDICT_CSV = REPORTS_DIR / "wan402_concurrent_breaks_verdict.csv"
CROSSTAB_CSV = REPORTS_DIR / "wan402_concurrent_breaks_crosstab.csv"
CONTROL_CSV = REPORTS_DIR / "wan402_concurrent_breaks_control.csv"
LOO_CSV = REPORTS_DIR / "wan402_concurrent_breaks_loo.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan402_concurrent_breaks_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan402_concurrent_breaks_summary.md"
#: 원자료(존 대장 · 라벨 붙은 셋업) — 커밋하지 않는다(`backtest/cache/`는 gitignore).
ZONES_CSV = Path("backtest/cache/wan402/zones.csv.gz")
LABELED_CSV = Path("backtest/cache/wan402/labeled.csv.gz")

SEGMENT_IS = harness.SEGMENT_IS
SEGMENT_OOS_WARM = harness.SEGMENT_OOS_WARM
SEGMENTS: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS_WARM)
PRIMARY_SEGMENT = SEGMENT_OOS_WARM

# --------------------------------------------------------------------------- #
# 착수 전에 못 박은 상수 — 결과를 보고 옮기지 않는다
# --------------------------------------------------------------------------- #

#: 뒤로 향한 창 셋(이름, ms). WAN-408 버킷 폭과 같은 자 · 자유 파라미터라 세 점 고정.
WINDOWS: tuple[tuple[str, int], ...] = (
    ("1h", 3_600_000),
    ("4h", 14_400_000),
    ("24h", 86_400_000),
)
WINDOW_MS: dict[str, int] = dict(WINDOWS)
MEASURE_RATIO = "ratio"
MEASURE_COUNT = "count"
MEASURES: tuple[str, ...] = (MEASURE_RATIO, MEASURE_COUNT)
#: 판정 자는 **비율**이다(존 재고가 시간에 따라 변한다 — 건수는 대조 표로만).
VERDICT_MEASURE = MEASURE_RATIO
QUANTILES = 5
MIN_BINS = 3
"""분위 경계가 뭉쳐 이보다 적게 남으면 판정하지 않는다(0이 많은 1h 창이 그럴 수 있다)."""
MIN_BIN_N = 100
"""최고·최저 분위 중 한쪽 표본이 이보다 적으면 그 창은 부호를 내지 않는다(WAN-375 규약)."""
SIGMA_MULTIPLE = 2.0
"""WAN-412 `_sign_is_decided` 자 — 두 분위 표준오차 합성의 2σ."""
TESTS = len(WINDOWS)
"""다중검정 — 창 셋이 곧 검정 셋이다(Bonferroni)."""
HYPOTHESIS_SIGN = -1
"""가설 방향: 다른 종목 존이 많이 깨지는 중일수록 거래당 net R이 **낮다**."""
VERDICT_GO = "간다"
VERDICT_NO = "안 간다"
VERDICT_UNDECIDED = "판정 불가"

WORST_DAYS = 10


def decision_z() -> float:
    """2σ(양측 α ≈ 4.55%)를 창 개수(`TESTS`)로 Bonferroni 보정한 z — 상수를 두 곳에 적지 않는다."""
    alpha = 2.0 * (1.0 - NormalDist().cdf(SIGMA_MULTIPLE))
    return NormalDist().inv_cdf(1.0 - alpha / (2.0 * TESTS))


# --------------------------------------------------------------------------- #
# 존 대장 — 탐지 층(1분봉을 안 읽는다)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ZoneEvent:
    """강세 존 하나의 「알려진」 생애 — 확정과 무효화를 **봉 마감 시각**으로 적는다."""

    symbol: str
    timeframe: str
    confirmed_known: int
    """`confirmed_time + TF` — 이 시각부터 「살아 있다」고 알 수 있다."""
    break_known: int | None
    """`break_time + TF` — 이 시각부터 「깨졌다」고 알 수 있다(WAN-365 `bar_close`). 없으면 생존."""


@dataclass(frozen=True)
class _ZoneTask:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def zone_events_for_cell(task: _ZoneTask) -> list[ZoneEvent]:
    """한 칸의 강세 존 전부 — 엔진이 소비하는 **그 아카이브**(`harness.detect_order_blocks`,
    같은 창 · 같은 `OrderBlockParams()`)에서 읽는다. 사본을 손으로 만들지 않는다(WAN-77)."""
    market = harness.load_market_data(
        harness.normalize_symbol(task.symbol),
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=False,
        funding=False,
    )
    if market.empty:
        return []
    tf_ms = timeframe_to_ms(task.timeframe)
    result = harness.detect_order_blocks(market, OrderBlockParams())
    events: list[ZoneEvent] = []
    for ob in result.order_blocks:
        if ob.direction is not OrderBlockDirection.BULLISH:
            continue
        events.append(
            ZoneEvent(
                symbol=harness.normalize_symbol(task.symbol),
                timeframe=task.timeframe,
                confirmed_known=ob.confirmed_time + tf_ms,
                break_known=None if ob.break_time is None else ob.break_time + tf_ms,
            )
        )
    print(f"[wan402] 존 대장 {task.symbol} {task.timeframe}: 강세 존 {len(events)}", flush=True)
    return events


def build_zone_ledger(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
) -> list[ZoneEvent]:
    """전 칸의 존 대장. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    tasks = [
        _ZoneTask(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1:
        batches = [zone_events_for_cell(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            batches = list(executor.map(zone_events_for_cell, tasks))
    return [event for batch in batches for event in batch]


def zones_frame(events: Sequence[ZoneEvent]) -> pd.DataFrame:
    return pd.DataFrame.from_records([asdict(e) for e in events])


def zones_from_frame(frame: pd.DataFrame) -> list[ZoneEvent]:
    """CSV 왕복 — `break_known`의 빈 칸(`NaN`)을 `None`으로 되돌린다(WAN-395 부수 수리의 함정)."""
    out: list[ZoneEvent] = []
    for rec in frame.to_dict("records"):
        raw = rec["break_known"]
        broken = None if raw is None or (isinstance(raw, float) and math.isnan(raw)) else int(raw)
        out.append(
            ZoneEvent(
                symbol=str(rec["symbol"]),
                timeframe=str(rec["timeframe"]),
                confirmed_known=int(rec["confirmed_known"]),
                break_known=broken,
            )
        )
    return out


class BreakIndex:
    """종목별 정렬된 「확정 알려짐」·「무효화 알려짐」 시각 — 창 계수와 생존 수를 `bisect`로 낸다.

    같은 종목의 존은 **전 TF**를 한 묶음으로 들고, 자기 제외는 종목 단위다.
    """

    def __init__(self, events: Sequence[ZoneEvent]) -> None:
        confirmed: dict[str, list[int]] = defaultdict(list)
        broken: dict[str, list[int]] = defaultdict(list)
        for event in events:
            confirmed[event.symbol].append(event.confirmed_known)
            if event.break_known is not None:
                if event.break_known < event.confirmed_known:
                    raise AssertionError(
                        f"{event.symbol} {event.timeframe}: 확정보다 먼저 깨진 존이 있습니다."
                    )
                broken[event.symbol].append(event.break_known)
        self._confirmed = {s: sorted(v) for s, v in confirmed.items()}
        self._broken = {s: sorted(v) for s, v in broken.items()}
        self.symbols: tuple[str, ...] = tuple(sorted(confirmed))

    def breaks_in(self, *, exclude: str, lo: int, hi: int) -> int:
        """`(lo, hi]`에 무효화가 **알려진** 다른 종목 강세 존 수."""
        total = 0
        for symbol, times in self._broken.items():
            if symbol == exclude:
                continue
            total += bisect_right(times, hi) - bisect_right(times, lo)
        return total

    def alive_at(self, *, exclude: str, at: int) -> int:
        """`at` 시점에 살아 있다고 **알 수 있는** 다른 종목 강세 존 수(확정 ≤ at · 무효화 > at)."""
        total = 0
        for symbol, times in self._confirmed.items():
            if symbol == exclude:
                continue
            total += bisect_right(times, at) - bisect_right(self._broken.get(symbol, []), at)
        return total


# --------------------------------------------------------------------------- #
# 모집단 — WAN-375 셋업 표에서 읽는다
# --------------------------------------------------------------------------- #


def load_population(path: Path = SETUPS_CSV) -> pd.DataFrame:
    """`band` · 첫 탭만 · 가드 통과 · 데이터 끝 제외 — 그리고 셋업마다 채택 회계 net R.

    없으면 **시끄럽게 죽는다** — 모집단을 여기서 새로 만들지 않는다(이슈 코멘트 2026-09-15).
    """
    if not path.exists():
        raise FileNotFoundError(
            f"WAN-375 셋업 표가 없습니다: {path} — "
            "`python -m backtest.wan375_conditional_rr`로 먼저 만드십시오"
            "(모집단을 두 벌로 만들지 않는다)."
        )
    frame = pd.read_csv(path)
    frame = frame[(frame["arm"] == ARM_BAND) & (frame["tap_index"] == 0)]
    frame = frame[frame["stop_width"] >= ADOPTED_STOP_GUARD]
    frame = frame[frame["tp_on_reason"] != ExitReason.END_OF_DATA.value].copy()
    if not frame["is_long"].astype(bool).all():
        raise AssertionError("롱 온리 모집단에 숏이 섞여 있습니다.")
    frame["symbol"] = frame["symbol"].map(harness.normalize_symbol)
    frame["net_r"] = net_r_column(frame)
    frame["is_stop"] = frame["tp_on_reason"] == ExitReason.STOP_LOSS.value
    frame["tp_on_exit_time"] = frame["tp_on_exit_time"].astype("int64")
    frame["entry_time"] = frame["entry_time"].astype("int64")
    return frame.sort_values(["symbol", "timeframe", "entry_time"]).reset_index(drop=True)


def population_census(path: Path = SETUPS_CSV, *, segment: str) -> tuple[int, int]:
    """WAN-375 표에서 `band` · 첫 탭 · 가드 통과 셋업 수와 그중 **데이터 끝**(익절 켠 판이 미결) 수.

    `load_population`이 빼는 것이 정확히 그 둘째뿐임을 검산 (a-2)가 값으로 확인한다.
    """
    frame = pd.read_csv(path)
    frame = frame[
        (frame["arm"] == ARM_BAND)
        & (frame["tap_index"] == 0)
        & (frame["stop_width"] >= ADOPTED_STOP_GUARD)
        & (frame["segment"] == segment)
    ]
    end = int((frame["tp_on_reason"] == ExitReason.END_OF_DATA.value).sum())
    return len(frame), end


def net_r_column(frame: pd.DataFrame) -> pd.Series:
    """셋업마다 거래당 net R — WAN-375 `cost_r`(채택 회계) 그대로. 익절 배수는 파라미터에서."""
    target_r = harness.build_params().take_profit_r
    rates_by_tf = {
        str(tf): CostRates.from_config(harness.build_config(str(tf)))
        for tf in frame["timeframe"].unique()
    }
    values: list[float] = []
    for width, reason, tf in zip(
        frame["stop_width"].to_numpy(),
        frame["tp_on_reason"].to_numpy(),
        frame["timeframe"].to_numpy(),
        strict=True,
    ):
        c_win, c_loss = cost_r(float(width), target_r, rates_by_tf[str(tf)])
        if reason == ExitReason.TAKE_PROFIT.value:
            values.append(target_r - c_win)
        elif reason == ExitReason.STOP_LOSS.value:
            values.append(-1.0 - c_loss)
        else:
            raise AssertionError(f"모르는 청산 사유: {reason!r}")
    return pd.Series(values, index=frame.index, dtype=float)


# --------------------------------------------------------------------------- #
# 라벨링 — 셋업마다 창 셋 × (건수 · 비율 · 생존 수 · 다른 칸 손절) ＋ WAN-410 축
# --------------------------------------------------------------------------- #


def _other_stops_index(frame: pd.DataFrame) -> dict[str, list[int]]:
    """보조 사건 — 모집단의 손절 청산 시각(종목별 정렬). 익절 켠 판의 청산 시각이다."""
    stops: dict[str, list[int]] = defaultdict(list)
    sub = frame[frame["is_stop"]]
    for symbol, t in zip(sub["symbol"].to_numpy(), sub["tp_on_exit_time"].to_numpy(), strict=True):
        stops[str(symbol)].append(int(t))
    return {s: sorted(v) for s, v in stops.items()}


def label_setups(frame: pd.DataFrame, index: BreakIndex) -> pd.DataFrame:
    """셋업마다 `cb_count_{w}` · `cb_alive_{w}` · `cb_ratio_{w}` · `other_stops_{w}` ＋
    `realized_today_before`(WAN-410 `settled` 자 · 이 모집단 위)."""
    out = frame.copy()
    stops = _other_stops_index(out)
    symbols = out["symbol"].to_numpy()
    entries = out["entry_time"].to_numpy()
    for name, width in WINDOWS:
        counts: list[int] = []
        alive: list[int] = []
        other: list[int] = []
        for symbol, t in zip(symbols, entries, strict=True):
            lo = int(t) - width
            counts.append(index.breaks_in(exclude=str(symbol), lo=lo, hi=int(t)))
            alive.append(index.alive_at(exclude=str(symbol), at=lo))
            other.append(
                sum(
                    bisect_right(times, int(t)) - bisect_right(times, lo)
                    for s, times in stops.items()
                    if s != symbol
                )
            )
        out[f"cb_count_{name}"] = counts
        out[f"cb_alive_{name}"] = alive
        out[f"cb_ratio_{name}"] = [
            (c / a) if a > 0 else float("nan") for c, a in zip(counts, alive, strict=True)
        ]
        out[f"other_stops_{name}"] = other
    facts = [
        TradeFact(
            symbol=str(rec["symbol"]),
            timeframe=str(rec["timeframe"]),
            entry_time=int(rec["entry_time"]),
            exit_time=int(rec["tp_on_exit_time"]),
            is_stop=bool(rec["is_stop"]),
            net_r=float(rec["net_r"]),
            is_reentry=False,
        )
        for rec in out.to_dict("records")
    ]
    out["realized_today_before"] = realized_r_before(facts, convention="settled")
    out["realized_bucket"] = out["realized_today_before"].map(
        lambda v: _bucket_label_realized(float(v), REALIZED_BUCKETS)
    )
    out["realized_order"] = out["realized_today_before"].map(
        lambda v: _bucket_order_realized(float(v), REALIZED_BUCKETS)
    )
    out["entry_day"] = out["entry_time"].map(lambda ms: kst_day_key(int(ms)))
    return out


def feature_column(window: str, measure: str) -> str:
    if measure == MEASURE_RATIO:
        return f"cb_ratio_{window}"
    if measure == MEASURE_COUNT:
        return f"cb_count_{window}"
    raise ValueError(f"모르는 정규화: {measure!r}")


# --------------------------------------------------------------------------- #
# 분위 — 앞구간에서 자르고 뒷구간에 그대로 적용한다
# --------------------------------------------------------------------------- #


def bin_edges(values: pd.Series, quantiles: int = QUANTILES) -> list[float]:
    """앞구간 값의 분위 경계(중복 경계는 접는다 — 0이 많으면 분위가 3개 이하로 남을 수 있다)."""
    clean = values.dropna()
    if clean.empty:
        return []
    edges = clean.quantile([i / quantiles for i in range(quantiles + 1)]).tolist()
    unique: list[float] = []
    for edge in edges:
        if not unique or edge > unique[-1]:
            unique.append(float(edge))
    return unique


def assign_bins(values: pd.Series, edges: Sequence[float]) -> pd.Series:
    """값 → 분위 번호(0 = 가장 낮음). 경계 밖(뒷구간이 앞구간 범위를 넘을 때)은 끝 분위로."""
    if len(edges) < 2:
        return pd.Series([pd.NA] * len(values), index=values.index, dtype="Int64")
    inner = list(edges[1:-1])
    out: list[int | None] = []
    for value in values.tolist():
        if value is None or (isinstance(value, float) and math.isnan(value)):
            out.append(None)
            continue
        out.append(int(bisect_right(inner, float(value))))
    return pd.Series(out, index=values.index, dtype="Int64")


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BinRow:
    window: str
    measure: str
    segment: str
    bin: int
    bins: int
    lo: float
    hi: float
    n: int
    stops: int
    stop_rate: float
    mean_net_r: float
    net_r_stderr: float
    mean_feature: float


def _stderr(values: Sequence[float]) -> float:
    return statistics.stdev(values) / (len(values) ** 0.5) if len(values) > 1 else 0.0


def bin_rows(
    frame: pd.DataFrame, *, window: str, measure: str, edges: Sequence[float]
) -> list[BinRow]:
    column = feature_column(window, measure)
    bins = assign_bins(frame[column], edges)
    total_bins = max(len(edges) - 1, 0)
    rows: list[BinRow] = []
    for segment in SEGMENTS:
        seg = frame["segment"] == segment
        for b in range(total_bins):
            sub = frame[seg & (bins == b).fillna(False).astype(bool)]
            if sub.empty:
                continue
            nets = [float(v) for v in sub["net_r"].tolist()]
            rows.append(
                BinRow(
                    window=window,
                    measure=measure,
                    segment=segment,
                    bin=b,
                    bins=total_bins,
                    lo=float(edges[b]),
                    hi=float(edges[b + 1]),
                    n=len(sub),
                    stops=int(sub["is_stop"].sum()),
                    stop_rate=float(sub["is_stop"].mean()),
                    mean_net_r=float(statistics.fmean(nets)),
                    net_r_stderr=_stderr(nets),
                    mean_feature=float(sub[column].mean()),
                )
            )
    return rows


def all_bin_rows(labeled: pd.DataFrame) -> tuple[list[BinRow], dict[tuple[str, str], list[float]]]:
    """창 × 정규화마다 앞구간 분위 경계를 잡고 두 구간을 집계한다. 경계도 함께 돌려준다."""
    rows: list[BinRow] = []
    edges_by: dict[tuple[str, str], list[float]] = {}
    is_frame = labeled[labeled["segment"] == SEGMENT_IS]
    for window, _ in WINDOWS:
        for measure in MEASURES:
            edges = bin_edges(is_frame[feature_column(window, measure)])
            edges_by[(window, measure)] = edges
            rows.extend(bin_rows(labeled, window=window, measure=measure, edges=edges))
    return rows, edges_by


# --------------------------------------------------------------------------- #
# 판정 — 코드가 낸다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WindowVerdict:
    window: str
    measure: str
    bins: int
    is_monotone: bool
    is_direction_matches: bool
    is_delta: float | None
    oos_delta: float | None
    oos_sigma: float | None
    oos_z: float | None
    oos_same_direction: bool
    oos_decided: bool
    verdict: str
    reason: str


def _is_monotone(values: Sequence[float], sign: int) -> bool:
    """`sign=−1`이면 비증가, `+1`이면 비감소(첫 분위 → 마지막 분위)."""
    if len(values) < 2:
        return False
    steps = [b - a for a, b in zip(values[:-1], values[1:], strict=True)]
    return all(s <= 0 for s in steps) if sign < 0 else all(s >= 0 for s in steps)


def verdict_for_window(rows: Sequence[BinRow], *, window: str, measure: str) -> WindowVerdict:
    """착수 전에 못 박은 규칙 셋(단조 · 같은 방향 · Bonferroni 2σ)을 **코드가** 적용한다."""
    mine = [r for r in rows if r.window == window and r.measure == measure]
    bins = mine[0].bins if mine else 0
    by_seg: dict[str, dict[int, BinRow]] = defaultdict(dict)
    for r in mine:
        by_seg[r.segment][r.bin] = r

    def undecided(reason: str) -> WindowVerdict:
        return WindowVerdict(
            window=window,
            measure=measure,
            bins=bins,
            is_monotone=False,
            is_direction_matches=False,
            is_delta=None,
            oos_delta=None,
            oos_sigma=None,
            oos_z=None,
            oos_same_direction=False,
            oos_decided=False,
            verdict=VERDICT_UNDECIDED,
            reason=reason,
        )

    if bins < MIN_BINS:
        return undecided(f"분위가 {bins}개로 뭉쳐 {MIN_BINS}개 미만")
    is_bins, oos_bins = by_seg.get(SEGMENT_IS, {}), by_seg.get(PRIMARY_SEGMENT, {})
    if len(is_bins) < bins or len(oos_bins) < bins:
        return undecided("빈 분위가 있음")
    lo_i, hi_i = is_bins[0], is_bins[bins - 1]
    lo_o, hi_o = oos_bins[0], oos_bins[bins - 1]
    if min(lo_i.n, hi_i.n, lo_o.n, hi_o.n) < MIN_BIN_N:
        return undecided(f"최고·최저 분위 표본 {MIN_BIN_N} 미만")
    is_curve = [is_bins[b].mean_net_r for b in range(bins)]
    is_delta = hi_i.mean_net_r - lo_i.mean_net_r
    monotone = _is_monotone(is_curve, HYPOTHESIS_SIGN)
    direction_ok = (is_delta < 0) if HYPOTHESIS_SIGN < 0 else (is_delta > 0)
    oos_delta = hi_o.mean_net_r - lo_o.mean_net_r
    sigma = (hi_o.net_r_stderr**2 + lo_o.net_r_stderr**2) ** 0.5
    z = abs(oos_delta) / sigma if sigma > 0 else math.inf
    same_direction = (oos_delta < 0) == (is_delta < 0) and oos_delta != 0.0 and is_delta != 0.0
    decided = z > decision_z()
    reasons: list[str] = []
    if not monotone:
        reasons.append("앞구간 비단조")
    if not direction_ok:
        reasons.append("앞구간 방향이 가설과 반대")
    if not same_direction:
        reasons.append("뒷구간 방향이 다름")
    if not decided:
        reasons.append(f"뒷구간 차 z={z:.2f} ≤ {decision_z():.2f}(Bonferroni {TESTS})")
    verdict = VERDICT_GO if not reasons else VERDICT_NO
    return WindowVerdict(
        window=window,
        measure=measure,
        bins=bins,
        is_monotone=monotone,
        is_direction_matches=direction_ok,
        is_delta=is_delta,
        oos_delta=oos_delta,
        oos_sigma=sigma,
        oos_z=z,
        oos_same_direction=same_direction,
        oos_decided=decided,
        verdict=verdict,
        reason=" · ".join(reasons) if reasons else "셋 다 통과",
    )


def verdict_rows(rows: Sequence[BinRow]) -> list[WindowVerdict]:
    return [
        verdict_for_window(rows, window=window, measure=measure)
        for window, _ in WINDOWS
        for measure in MEASURES
    ]


# --------------------------------------------------------------------------- #
# 통제 — WAN-410 축과의 교차 · 층화 · 폭락일 제외
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CrossRow:
    window: str
    segment: str
    realized_bucket: str
    realized_order: int
    bin: int
    n: int
    share_of_realized_bucket: float
    mean_net_r: float
    stop_rate: float


def crosstab_rows(
    labeled: pd.DataFrame, edges_by: dict[tuple[str, str], list[float]], *, segment: str
) -> list[CrossRow]:
    """WAN-410 축(그날 실현 누계 버킷) × `concurrent_breaks` 분위 — 얼마나 겹치나."""
    sub = labeled[labeled["segment"] == segment]
    rows: list[CrossRow] = []
    for window, _ in WINDOWS:
        bins = assign_bins(
            sub[feature_column(window, VERDICT_MEASURE)], edges_by[(window, VERDICT_MEASURE)]
        )
        for (bucket, order), group in sub.assign(_bin=bins).groupby(
            ["realized_bucket", "realized_order"], sort=False
        ):
            total = len(group)
            for b, cell in group.groupby("_bin"):
                if cell.empty:
                    continue
                rows.append(
                    CrossRow(
                        window=window,
                        segment=segment,
                        realized_bucket=str(bucket),
                        realized_order=int(order),
                        bin=int(b),
                        n=len(cell),
                        share_of_realized_bucket=len(cell) / total,
                        mean_net_r=float(cell["net_r"].mean()),
                        stop_rate=float(cell["is_stop"].mean()),
                    )
                )
    return rows


@dataclass(frozen=True)
class ControlRow:
    """통제 한 줄 — 「WAN-410 버킷 안에서」 또는 「폭락일 뺀 판에서」 최고−최저 분위 차."""

    window: str
    segment: str
    control: str
    stratum: str
    n_lo: int
    n_hi: int
    delta: float | None
    sigma: float | None
    z: float | None
    stop_rate_delta: float | None


def _delta_row(
    sub: pd.DataFrame,
    *,
    window: str,
    segment: str,
    control: str,
    stratum: str,
    edges: Sequence[float],
) -> ControlRow:
    bins = assign_bins(sub[feature_column(window, VERDICT_MEASURE)], edges)
    top = max(len(edges) - 2, 0)
    lo = sub[(bins == 0).fillna(False).astype(bool)]
    hi = sub[(bins == top).fillna(False).astype(bool)]
    if len(edges) - 1 < MIN_BINS or lo.empty or hi.empty:
        return ControlRow(
            window, segment, control, stratum, len(lo), len(hi), None, None, None, None
        )
    lo_nets = [float(v) for v in lo["net_r"].tolist()]
    hi_nets = [float(v) for v in hi["net_r"].tolist()]
    delta = statistics.fmean(hi_nets) - statistics.fmean(lo_nets)
    sigma = (_stderr(hi_nets) ** 2 + _stderr(lo_nets) ** 2) ** 0.5
    return ControlRow(
        window=window,
        segment=segment,
        control=control,
        stratum=stratum,
        n_lo=len(lo),
        n_hi=len(hi),
        delta=delta,
        sigma=sigma,
        z=(abs(delta) / sigma if sigma > 0 else math.inf),
        stop_rate_delta=float(hi["is_stop"].mean() - lo["is_stop"].mean()),
    )


CONTROL_NONE = "없음"
CONTROL_REALIZED = "WAN-410 버킷 안"
CONTROL_NO_CRASH = "폭락일 제외"
CONTROL_POOLED = "WAN-410 버킷 안(표본 가중 합)"


def control_rows(
    labeled: pd.DataFrame,
    edges_by: dict[tuple[str, str], list[float]],
    *,
    bad_days: Sequence[str],
    segment: str = PRIMARY_SEGMENT,
) -> list[ControlRow]:
    """뒷구간에서 (1) 통제 없음 · (2) WAN-410 버킷마다 · (2′) 그 버킷들의 표본 가중 합 ·
    (3) WAN-408 최악 10일을 뺀 판의 최고−최저 분위 차."""
    sub = labeled[labeled["segment"] == segment]
    rows: list[ControlRow] = []
    for window, _ in WINDOWS:
        edges = edges_by[(window, VERDICT_MEASURE)]
        rows.append(
            _delta_row(
                sub, window=window, segment=segment, control=CONTROL_NONE, stratum="—", edges=edges
            )
        )
        strata: list[ControlRow] = []
        for (bucket, _order), group in sorted(
            sub.groupby(["realized_bucket", "realized_order"], sort=False),
            key=lambda kv: kv[0][1],
        ):
            strata.append(
                _delta_row(
                    group,
                    window=window,
                    segment=segment,
                    control=CONTROL_REALIZED,
                    stratum=str(bucket),
                    edges=edges,
                )
            )
        rows.extend(strata)
        usable = [r for r in strata if r.delta is not None and min(r.n_lo, r.n_hi) >= MIN_BIN_N]
        if usable:
            weights = [float(min(r.n_lo, r.n_hi)) for r in usable]
            pooled = sum(w * float(r.delta or 0.0) for w, r in zip(weights, usable, strict=True))
            pooled /= sum(weights)
            var = sum(
                (w / sum(weights)) ** 2 * float(r.sigma or 0.0) ** 2
                for w, r in zip(weights, usable, strict=True)
            )
            sigma = var**0.5
            rows.append(
                ControlRow(
                    window=window,
                    segment=segment,
                    control=CONTROL_POOLED,
                    stratum=f"{len(usable)}버킷",
                    n_lo=sum(r.n_lo for r in usable),
                    n_hi=sum(r.n_hi for r in usable),
                    delta=pooled,
                    sigma=sigma,
                    z=(abs(pooled) / sigma if sigma > 0 else math.inf),
                    stop_rate_delta=None,
                )
            )
        if bad_days:
            rows.append(
                _delta_row(
                    sub[~sub["entry_day"].isin(set(bad_days))],
                    window=window,
                    segment=segment,
                    control=CONTROL_NO_CRASH,
                    stratum=f"최악 {len(bad_days)}일 제외",
                    edges=edges,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# leave-one-out — 종목 하나를 통째로 빼고(거래도 존 사건도) 다시 라벨링한다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LooRow:
    window: str
    dropped: str
    n: int
    bins: int
    oos_delta: float | None
    oos_z: float | None
    full_delta: float | None
    sign_kept: bool | None


def loo_rows(
    population: pd.DataFrame, events: Sequence[ZoneEvent], full_verdicts: Sequence[WindowVerdict]
) -> list[LooRow]:
    full_by = {v.window: v for v in full_verdicts if v.measure == VERDICT_MEASURE}
    rows: list[LooRow] = []
    for dropped in sorted(population["symbol"].unique()):
        sub_events = [e for e in events if e.symbol != dropped]
        sub_pop = population[population["symbol"] != dropped]
        labeled = label_setups(sub_pop, BreakIndex(sub_events))
        bins_rows, _ = all_bin_rows(labeled)
        for window, _ in WINDOWS:
            v = verdict_for_window(bins_rows, window=window, measure=VERDICT_MEASURE)
            full = full_by[window]
            kept = (
                None
                if v.oos_delta is None or full.oos_delta is None
                else (v.oos_delta < 0) == (full.oos_delta < 0)
            )
            rows.append(
                LooRow(
                    window=window,
                    dropped=dropped,
                    n=len(labeled),
                    bins=v.bins,
                    oos_delta=v.oos_delta,
                    oos_z=v.oos_z,
                    full_delta=full.oos_delta,
                    sign_kept=kept,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChecksumRow:
    name: str
    value: float
    reference: float
    diff: float


EXPECTED_OOS_WARM_SETUPS = 10_274
"""이슈 코멘트(2026-09-15)가 인용한 모집단 크기 — 「WAN-375가 만든 그 집합」인지 확인한다."""

_CACHE_KEYS = ("trigger_time", "tap_index", "entry_time", "entry_price", "stop_price", "exit_time")


def cache_reproduction_mismatches(
    population: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
) -> tuple[int, int]:
    """(d) 적재된 WAN-375 셋업 표가 **지금 코드가 내는 것**인가 — 한 칸을 다시 만들어 대조한다.

    캐시는 gitignore라 「그때의 산출물」이지 「이 리비전의 산출물」이 아닐 수 있다. 첫 탭·`band`·
    가드 통과 셋업의 (탭 시각 · 탭 순번 · 진입 시각 · 진입가 · 손절가 · 무-익절 청산 시각)이
    전부 같아야 한다. 반환 = (대조한 셋업 수, 불일치 수) — 불일치가 0이 아니면 캐시를 다시 만든다.
    """
    from backtest.wan375_conditional_rr import _Task as Wan375Task
    from backtest.wan375_conditional_rr import run_cell

    result = run_cell(
        Wan375Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            checksum=False,
        )
    )
    # 모집단과 같은 규칙으로 고른다 — `band` · 첫 탭 · 가드 통과 · 익절 켠 판이 데이터 끝이 아님.
    fresh = sorted(
        (r.trigger_time, r.tap_index, r.entry_time, r.entry_price, r.stop_price, r.exit_time)
        for r in result.rows
        if r.arm == ARM_BAND
        and r.tap_index == 0
        and r.stop_width >= ADOPTED_STOP_GUARD
        and not (r.exit_reason == ExitReason.END_OF_DATA.value and r.target_reach_time is None)
    )
    cached_frame = population[
        (population["symbol"] == harness.normalize_symbol(symbol))
        & (population["timeframe"] == timeframe)
    ]
    cached = sorted(
        (int(a), int(b), int(c), float(d), float(e), int(f))
        for a, b, c, d, e, f in cached_frame[list(_CACHE_KEYS)].itertuples(index=False)
    )
    # 정수(시각·순번)는 정확히, 가격은 CSV 왕복 끝자리(rel 1e-9)만 허용한다 — 11/133이 그
    # 끝자리였다(파일럿 실측). 그 밖의 차이는 전부 불일치다.
    mismatches = sum(
        1
        for a, b in zip(fresh, cached, strict=False)
        if not (
            a[0] == b[0]
            and a[1] == b[1]
            and a[2] == b[2]
            and a[5] == b[5]
            and math.isclose(a[3], b[3], rel_tol=1e-9, abs_tol=0.0)
            and math.isclose(a[4], b[4], rel_tol=1e-9, abs_tol=0.0)
        )
    )
    mismatches += abs(len(fresh) - len(cached))
    return len(cached), mismatches


def checksum_rows(population: pd.DataFrame, labeled: pd.DataFrame) -> list[ChecksumRow]:
    """(a) 모집단 ≡ WAN-375 첫 탭·가드 통과 `oos_warm` 10,274 · (b) net R 평균 ≡ WAN-375 `_ev_at`
    (다른 코드 경로) · (c) 라벨이 인과적이다(창 밖 사건 0 — 구성상 참이나 값으로 확인)."""
    from backtest.wan375_conditional_rr import _ev_at

    warm = population[population["segment"] == SEGMENT_OOS_WARM]
    raw_warm, raw_end = population_census(segment=SEGMENT_OOS_WARM)
    rows = [
        ChecksumRow(
            "(a-1) WAN-375 첫 탭·가드 통과 oos_warm(데이터 끝 포함) ≡ 이슈가 인용한 10,274",
            float(raw_warm),
            float(EXPECTED_OOS_WARM_SETUPS),
            float(raw_warm - EXPECTED_OOS_WARM_SETUPS),
        ),
        ChecksumRow(
            "(a-2) 모집단 ≡ 그 집합 − 데이터 끝(익절 켠 판이 미결)",
            float(len(warm)),
            float(raw_warm - raw_end),
            float(len(warm) - (raw_warm - raw_end)),
        ),
    ]
    target_r = harness.build_params().take_profit_r
    for tf in sorted(warm["timeframe"].unique(), key=_tf_order):
        sub = warm[warm["timeframe"] == tf]
        ref = _ev_at(sub, target_r, CostRates.from_config(harness.build_config(str(tf))))
        mine = float(sub["net_r"].mean())
        rows.append(
            ChecksumRow(
                f"(b) net R 평균 ≡ WAN-375 _ev_at · {tf}",
                mine,
                float(ref if ref is not None else float("nan")),
                float(mine - (ref if ref is not None else float("nan"))),
            )
        )
    # (c) 창 폭이 길수록 건수가 단조 증가해야 한다(창이 포개져 있다) — 뒤집히면 배선 오류.
    violations = int(
        (
            (labeled["cb_count_1h"] > labeled["cb_count_4h"])
            | (labeled["cb_count_4h"] > labeled["cb_count_24h"])
        ).sum()
    )
    rows.append(
        ChecksumRow(
            "(c) 포개진 창의 건수 단조(위반 셋업 수)", float(violations), 0.0, float(violations)
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _tf_order(tf: str) -> int:
    order = {"15m": 0, "1h": 1, "2h": 2, "4h": 3}
    return order.get(tf, 9)


def _f(value: float | None, digits: int = 4) -> str:
    return (
        "—"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{value:+.{digits}f}"
    )


def _p(value: float | None) -> str:
    return (
        "—"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{value * 100:.1f}%"
    )


def render_summary(
    *,
    bins: pd.DataFrame,
    verdicts: pd.DataFrame,
    crosstab: pd.DataFrame,
    control: pd.DataFrame,
    loo: pd.DataFrame,
    checksum: pd.DataFrame,
    edges_by: dict[tuple[str, str], list[float]],
    population_n: dict[str, int],
    cost_note: str,
) -> str:
    lines: list[str] = []
    lines.append("# WAN-402 §0 — 「같이 깨지는 중인가」(`concurrent_breaks`) · 셋업 층 관측")
    lines.append("")
    lines.append(
        '🚨 **이 표는 채택 좌표가 아니다** — `band` 팔 · **첫 탭만**(= `retap_mode="once"`) · '
        "재진입 없음 · 손절폭 가드 통과 · 셋업 층(시퀀싱·북·공유 자본 없음). 페이퍼가 실제로 "
        "하는 매매와 다르다(WAN-305). 여기 수치를 채택 북 성적과 나란히 놓지 말 것. 판정 자는 "
        "**거래당 net R**(손절률은 대조)."
    )
    lines.append("")
    lines.append(
        f"모집단: `is` {population_n.get(SEGMENT_IS, 0):,} · "
        f"`oos_warm` {population_n.get(SEGMENT_OOS_WARM, 0):,} 셋업. "
        f"창 {', '.join(w for w, _ in WINDOWS)} · 판정 자 `{VERDICT_MEASURE}` · 분위 {QUANTILES} "
        f"(앞구간 경계 고정) · 가설 방향 {'−' if HYPOTHESIS_SIGN < 0 else '+'} · "
        f"2σ를 Bonferroni {TESTS}로 보정한 z = {decision_z():.2f}. {cost_note}"
    )
    lines.append("")
    lines.append("## 판정 — 코드가 낸다 (`verdict_for_window`)")
    lines.append("")
    lines.append(
        "| 창 | 자 | 분위 | 앞구간 단조 | 앞구간 방향 | 앞구간 Δ | 뒷구간 Δ | σ | z | 판정 | 이유 |"
    )
    lines.append("| -- | -- | --: | -- | -- | --: | --: | --: | --: | -- | -- |")
    for rec in verdicts.to_dict("records"):
        lines.append(
            f"| {rec['window']} | {rec['measure']} | {rec['bins']} | "
            f"{'✅' if rec['is_monotone'] else '—'} | "
            f"{'✅' if rec['is_direction_matches'] else '—'} | {_f(rec['is_delta'])} | "
            f"{_f(rec['oos_delta'])} | {_f(rec['oos_sigma'])} | {_f(rec['oos_z'], 2)} | "
            f"**{rec['verdict']}** | {rec['reason']} |"
        )
    lines.append("")
    lines.append("Δ = 최고 분위(많이 깨지는 중) − 최저 분위의 거래당 net R.")
    lines.append("")
    lines.append("## 분위별 — 손절률(a) · 거래당 net R(b) 병기")
    lines.append("")
    for window, _ in WINDOWS:
        for measure in MEASURES:
            sub = bins[(bins["window"] == window) & (bins["measure"] == measure)]
            if sub.empty:
                continue
            edges = edges_by.get((window, measure), [])
            lines.append(
                f"### 창 {window} · `{measure}` · 앞구간 경계 {[round(e, 4) for e in edges]}"
            )
            lines.append("")
            lines.append("| 구간 | 분위 | 범위 | n | 손절률 | 거래당 net R | ±σ | 평균값 |")
            lines.append("| -- | --: | -- | --: | --: | --: | --: | --: |")
            for rec in sub.sort_values(["segment", "bin"]).to_dict("records"):
                lines.append(
                    f"| {rec['segment']} | {rec['bin']} | {rec['lo']:.4f}~{rec['hi']:.4f} | "
                    f"{rec['n']:,} | {_p(rec['stop_rate'])} | {_f(rec['mean_net_r'])} | "
                    f"{rec['net_r_stderr']:.4f} | {rec['mean_feature']:.4f} |"
                )
            lines.append("")
    lines.append("## 통제 — WAN-410 「그날 실현 net R 누적」의 다른 이름인가")
    lines.append("")
    lines.append("### 교차표 (`oos_warm` · 행 = WAN-410 버킷 · 열 = 분위의 비중)")
    lines.append("")
    for window, _ in WINDOWS:
        sub = crosstab[crosstab["window"] == window]
        if sub.empty:
            continue
        top = int(sub["bin"].max())
        lines.append(f"창 {window}:")
        lines.append("")
        header = " | ".join(f"Q{b}" for b in range(top + 1))
        lines.append(f"| 실현 누계 버킷 | n | {header} |")
        lines.append("| -- | --: |" + " --: |" * (top + 1))
        for (bucket, _order), group in sorted(
            sub.groupby(["realized_bucket", "realized_order"], sort=False), key=lambda kv: kv[0][1]
        ):
            shares = {
                int(r["bin"]): float(r["share_of_realized_bucket"])
                for r in group.to_dict("records")
            }
            total = int(group["n"].sum())
            cells = " | ".join(_p(shares.get(b, 0.0)) for b in range(top + 1))
            lines.append(f"| {bucket} | {total:,} | {cells} |")
        lines.append("")
    lines.append("### 통제한 뒤 남는 Δ (`oos_warm` · 최고−최저 분위 · 거래당 net R)")
    lines.append("")
    lines.append("| 창 | 통제 | 층 | n(최저) | n(최고) | Δ | σ | z | 손절률 Δ |")
    lines.append("| -- | -- | -- | --: | --: | --: | --: | --: | --: |")
    for rec in control.to_dict("records"):
        lines.append(
            f"| {rec['window']} | {rec['control']} | {rec['stratum']} | {rec['n_lo']:,} | "
            f"{rec['n_hi']:,} | {_f(rec['delta'])} | {_f(rec['sigma'])} | {_f(rec['z'], 2)} | "
            f"{_f(rec['stop_rate_delta'])} |"
        )
    lines.append("")
    lines.append("## leave-one-out (종목 하나씩 — 거래와 존 사건을 함께 뺀다 · `oos_warm` Δ)")
    lines.append("")
    lines.append("| 창 | 뺀 종목 | n | 분위 | Δ | z | 전체 Δ | 부호 유지 |")
    lines.append("| -- | -- | --: | --: | --: | --: | --: | -- |")
    for rec in loo.to_dict("records"):
        kept = rec["sign_kept"]
        mark = (
            "—"
            if kept is None or (isinstance(kept, float) and math.isnan(kept))
            else ("✅" if kept else "❌")
        )
        lines.append(
            f"| {rec['window']} | {rec['dropped']} | {rec['n']:,} | {rec['bins']} | "
            f"{_f(rec['oos_delta'])} | {_f(rec['oos_z'], 2)} | {_f(rec['full_delta'])} | {mark} |"
        )
    lines.append("")
    lines.append("## 검산")
    lines.append("")
    lines.append("| 검산 | 값 | 기준 | 차이 |")
    lines.append("| -- | --: | --: | --: |")
    for rec in checksum.to_dict("records"):
        lines.append(
            f"| {rec['name']} | {rec['value']:.6f} | {rec['reference']:.6f} | {rec['diff']:.2e} |"
        )
    lines.append("")
    lines.append(
        "측정 전용 · 엔진·기본값·토대 불변(`ConfluenceParams()`·`OrderBlockParams()`·"
        "`LeverageBookParams()`) · 핀 없음(WAN-305) · 전부 `baseline` 위 값 · "
        "**필터를 만들지 않았다**(§0은 「신호가 있는가」까지 — 필터로 가면 북에서 다시 잰다, "
        "WAN-341/323) · **WAN-408과 더하지 말 것**(WAN-409) · 「엣지 없음」 계열 불변 · "
        "실거래 보류 유지."
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=None, help="존 대장 병렬(성능 노브, WAN-121)")
    parser.add_argument("--from-csv", action="store_true", help="라벨 표를 다시 읽어 요약만")
    parser.add_argument("--pilot", action="store_true", help="BTC 4h 한 칸만 라벨링해 비용을 잰다")
    parser.add_argument("--no-loo", action="store_true", help="leave-one-out 생략")
    return parser.parse_args(argv)


def _frames(
    population: pd.DataFrame, labeled: pd.DataFrame, events: Sequence[ZoneEvent], *, loo: bool
) -> dict[str, object]:
    bins_rows, edges_by = all_bin_rows(labeled)
    verdicts = verdict_rows(bins_rows)
    bad_days = worst_days(count=WORST_DAYS)
    crosstab = crosstab_rows(labeled, edges_by, segment=PRIMARY_SEGMENT)
    control = control_rows(labeled, edges_by, bad_days=bad_days)
    loo_list = loo_rows(population, events, verdicts) if loo else []
    checks = checksum_rows(population, labeled)
    return {
        "bins": pd.DataFrame.from_records([asdict(r) for r in bins_rows]),
        "verdicts": pd.DataFrame.from_records([asdict(r) for r in verdicts]),
        "crosstab": pd.DataFrame.from_records([asdict(r) for r in crosstab]),
        "control": pd.DataFrame.from_records([asdict(r) for r in control]),
        "loo": pd.DataFrame.from_records([asdict(r) for r in loo_list]),
        "checksum": pd.DataFrame.from_records([asdict(r) for r in checks]),
        "edges_by": edges_by,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    started = time.monotonic()
    population = load_population()
    if args.from_csv:
        if not LABELED_CSV.exists() or not ZONES_CSV.exists():
            print(f"[wan402] 원자료가 없습니다: {LABELED_CSV} / {ZONES_CSV}", file=sys.stderr)
            return 2
        labeled = pd.read_csv(LABELED_CSV)
        events = zones_from_frame(pd.read_csv(ZONES_CSV))
        cost_note = "요약만 재생성(`--from-csv`)."
    else:
        ledger_started = time.monotonic()
        if ZONES_CSV.exists() and not args.pilot:
            events = zones_from_frame(pd.read_csv(ZONES_CSV))
            print(f"[wan402] 존 대장 재사용: {len(events):,} 강세 존", flush=True)
        else:
            events = build_zone_ledger(
                harness.DEFAULT_SYMBOLS, harness.DEFAULT_TIMEFRAMES, jobs=jobs
            )
            ZONES_CSV.parent.mkdir(parents=True, exist_ok=True)
            zones_frame(events).to_csv(ZONES_CSV, index=False)
        ledger_seconds = time.monotonic() - ledger_started
        index = BreakIndex(events)
        if args.pilot:
            population = population[
                (population["symbol"] == harness.normalize_symbol("BTCUSDT"))
                & (population["timeframe"] == "4h")
            ].reset_index(drop=True)
        label_started = time.monotonic()
        labeled = label_setups(population, index)
        label_seconds = time.monotonic() - label_started
        cost_note = (
            f"실측 비용: 존 대장 {ledger_seconds:.0f}s(48칸 · `--jobs {jobs}`) · 라벨링 "
            f"{label_seconds:.0f}s({len(labeled):,} 셋업)."
        )
        if args.pilot:
            print(f"[wan402] 파일럿 BTC 4h: {len(labeled)} 셋업 · {cost_note}", flush=True)
            cols = [c for c in labeled.columns if c.startswith(("cb_", "other_", "realized_"))]
            print(labeled[cols].describe().T)
            recheck_started = time.monotonic()
            compared, mismatches = cache_reproduction_mismatches(
                population, symbol="BTCUSDT", timeframe="4h"
            )
            print(
                f"[wan402] (d) WAN-375 캐시 재현 BTC 4h: 대조 {compared} · 불일치 {mismatches} · "
                f"{time.monotonic() - recheck_started:.0f}s",
                flush=True,
            )
            return 0 if mismatches == 0 else 1
        LABELED_CSV.parent.mkdir(parents=True, exist_ok=True)
        labeled.to_csv(LABELED_CSV, index=False)
    frames = _frames(population, labeled, events, loo=not args.no_loo)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for key, path in (
        ("bins", BINS_CSV),
        ("verdicts", VERDICT_CSV),
        ("crosstab", CROSSTAB_CSV),
        ("control", CONTROL_CSV),
        ("loo", LOO_CSV),
        ("checksum", CHECKSUM_CSV),
    ):
        frame = frames[key]
        assert isinstance(frame, pd.DataFrame)
        if key == "loo" and frame.empty and args.no_loo and LOO_CSV.exists():
            continue
        frame.to_csv(path, index=False)
    edges_by = frames["edges_by"]
    assert isinstance(edges_by, dict)
    population_n = {seg: int((population["segment"] == seg).sum()) for seg in SEGMENTS}
    summary = render_summary(
        bins=_df(frames["bins"]),
        verdicts=_df(frames["verdicts"]),
        crosstab=_df(frames["crosstab"]),
        control=_df(frames["control"]),
        loo=_df(frames["loo"]) if not (args.no_loo and LOO_CSV.exists()) else pd.read_csv(LOO_CSV),
        checksum=_df(frames["checksum"]),
        edges_by=edges_by,
        population_n=population_n,
        cost_note=cost_note + f" 전체 {time.monotonic() - started:.0f}s.",
    )
    SUMMARY_PATH.write_text(summary)
    print(summary)
    return 0


def _df(value: object) -> pd.DataFrame:
    assert isinstance(value, pd.DataFrame)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
