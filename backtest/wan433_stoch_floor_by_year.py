"""WAN-433: 스토캐스틱 팔의 「넓은 손절이 이긴다」가 **최근 2년만의 일인가** — 연도별로 쪼갠다.

## 왜 이 모듈이 있나

WAN-432가 하한 0.5% → 4%로 좋아지는 몫을 **비용 절감**(손절폭이 넓어지면 같은 수수료의 R
비중이 그냥 준다 — WAN-370)과 **시장 몫**(Δgross)으로 갈랐고, 답은 갈렸다:

| 구간 | Δnet | 비용 절감 | 시장 몫 |
| -- | --: | --: | --: |
| 앞구간 `is`(약 4년) | +0.029R | +0.039R | **−0.009R** |
| 뒷구간 `oos_warm`(약 2년) | +0.197R | +0.046R | **+0.151R** |

즉 **「넓은 손절 자리가 더 좋은 거래」라는 몫은 뒷구간에만 있다.** 그런데 뒷구간은 약 2년짜리
한 덩어리라 *그 2년이 특별했나*를 물을 수 없다. 이 모듈은 같은 팔의 **`full` 판 거래를 진입
시각의 달력 연도로 쪼개** 「넓은 손절이 이기는 해」가 앞 4년 안에도 있었는지 센다.

## 축을 새로 만들지 않는다

좌표·팔·격자는 WAN-432 **그대로**다(31종목 × 9TF · 못 박은 6년 · 롱 · 첫 탭 · 재진입 없음 ·
시간 청산 · `pen_5bp` × 같은 분 익절 금지 · 채택 북 · 복리 끔 · 가드 끔 · **핀 없음**). 하한
6점 × %K 3점 × 보유 5점도 같다. **새로 생긴 것은 「진입 연도」라는 집계 축 하나**이고, 그것은
이미 돌린 배치의 거래를 다시 세는 일이라 엔진에 닿지 않는다.

🚨 **연도는 다시 돌려서 자르지 않는다.** 구간마다 다시 탐지하면 아카이브 인덱스가 밀린다
(WAN-428 부록이 `is`에서 실제로 겪은 버그). 한 번의 배치 → 거래별 진입 시각 → 연도 버킷이고,
그 버킷을 **다시 합치면 WAN-432 공개 CSV와 같은 수**여야 한다(§검산 = 완료기준 3).

## 판정은 코드가 낸다 — 상수를 먼저 박았다

이슈가 착수 전에 정한 선을 상수로 둔다(결과를 보고 옮기지 않는다 — WAN-161):

* 한 해가 **「시장 몫이 있는 해」**인가 = 그 해의 15조합(문턱 3 × 보유 5) 중 **Δgross > 0이고
  부호가 결정된**(`|Δ| > 2σ`) 조합이 **과반**인가(`year_is_market`).
* **앞구간 연도**(`IS_YEARS` = 2020~2023) 중 그런 해가 **`MULTI_YEAR_GATE`(= 2)개 이상**이면
  「여러 해」, 아니면 「최근 2년에만」.

⚠️ **버킷당 표본이 작다** — 하한 4%는 `full` 861~2,710거래라 연 100~500건대다. 2σ가 차이를
덮으면 그 해는 **「미결정」**이고, 판정문은 **미결정 개수를 함께 찍는다**(미결정을 「없다」로
읽지 않게 — 완료기준 4).

## 두 표본이 포개져 있다 (알고 쓰는 근사)

Δ의 2σ는 `sqrt(se_4% ² + se_1% ²)`(WAN-381/412 관문과 같은 자)인데, **하한 4% 거래는 1%
거래의 부분집합**이라 독립 가정이 성립하지 않는다. 그래서 이 σ는 **방향 판정에만** 쓰고 크기로
인용하지 않는다. 같은 이유로 판정을 **한 조합의 부호가 아니라 15조합의 과반**으로 낸다.

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 핀 없음 WAN-305) · 이 팔은 **채택 좌표가 아니다**(채택 북
−0.12R과 나란히 놓지 말 것) · 전부 `pen_5bp` × 같은 분 익절 금지 위의 값 · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.book_cli import net_r
from backtest.models import BacktestConfig
from backtest.run import parse_date_ms

# 🚨 아래는 **의도적 재노출**이다 — 이 이슈가 더하는 축은 「진입 연도」 하나이고 나머지(하한
# 점·문턱·보유 봉·TF·종목·구간·부호 자)는 WAN-424 §1 / WAN-432의 값을 그대로 물려받는다.
# `as` 형태가 「여기서 다시 정의하지 않았다」를 타입 수준에서 못 박는다 — 값을 베껴 쓰면 두
# 표의 자가 조용히 갈라진다(WAN-91/95/112/123/159).
from backtest.wan169_leverage_book import CellPayload
from backtest.wan370_cost_decomposition import decompose_trade
from backtest.wan424_stoch_ob_arm import HOLD_BARS as HOLD_BARS
from backtest.wan424_stoch_ob_arm import SEGMENTS as SEGMENTS
from backtest.wan424_stoch_ob_arm import SYMBOLS as SYMBOLS
from backtest.wan424_stoch_ob_arm import THRESHOLDS as THRESHOLDS
from backtest.wan424_stoch_ob_arm import TIMEFRAMES as TIMEFRAMES
from backtest.wan424_stoch_ob_arm import WAN423_REFERENCE as WAN423_REFERENCE
from backtest.wan424_stoch_ob_arm import (
    ArmCell,
    build_arm_cells,
    build_base_payloads,
    filtered_payloads,
    place_segments,
)
from backtest.wan432_stoch_floor_robustness import ADOPTED_FLOOR as ADOPTED_FLOOR
from backtest.wan432_stoch_floor_robustness import ARM_FLOOR as ARM_FLOOR
from backtest.wan432_stoch_floor_robustness import CHECKSUM_TOL as CHECKSUM_TOL
from backtest.wan432_stoch_floor_robustness import FLOORS as FLOORS
from backtest.wan432_stoch_floor_robustness import NOISE_R as NOISE_R
from backtest.wan432_stoch_floor_robustness import SIGN_SIGMA as SIGN_SIGMA
from data.storage import OhlcvStore

REPORT_DIR = Path(__file__).resolve().parent / "reports"
CSV_PATH = REPORT_DIR / "wan433_stoch_floor_by_year.csv"
SUMMARY_PATH = REPORT_DIR / "wan433_stoch_floor_by_year_summary.md"
DEFAULT_PAYLOAD_DIR = Path(__file__).resolve().parent / "cache" / "wan424_payloads"
WAN432_CSV = REPORT_DIR / "wan432_stoch_floor_robustness.csv"

#: 이슈가 고른 대조 쌍 — 채택 하한(4%) 대 **느슨한 하한**(1%).
#:
#: ⚠️ 바닥(0.5%)이 아니라 1%다. 이슈 제목·사양이 그렇게 정했고, 0.5%는 「거의 아무 거래나」에
#: 가까워 손절폭 축이 아니라 **표본 구성**이 통째로 달라진다. 0.5% 행도 CSV에는 그대로 있다.
LOOSE_FLOOR = 0.01

#: 앞구간 연도 — 이 좌표에서 **모든 칸이 `is`**인 해들(§구간 경계 인구조사가 실측으로 건다).
#:
#: 🚨 2024는 **걸친다**(`IS_FRACTION` = 2/3 경계가 2024년 안에 있다) — 그래서 판정에서 뺀다.
#: 이슈가 「앞구간 연도(2020~2023)」로 못 박은 자리다.
IS_YEARS: tuple[int, ...] = (2020, 2021, 2022, 2023)

#: 「여러 해에 걸쳐 보인다」의 선 — 앞구간 연도 중 몇 개여야 하는가. **착수 전에 정했다**.
MULTI_YEAR_GATE = 2

#: 보조 열(해석용 · 판정 아님) — 그해 BTC 연간 수익률을 읽는 곳.
BTC_SYMBOL = "BTC/USDT:USDT"
BTC_TIMEFRAME = "1d"


# ---------------------------------------------------------------------------
# 연도 — 데이터 축이라 UTC다 (사람이 읽는 시각만 KST · WAN-172)
# ---------------------------------------------------------------------------


def year_of(ms: int) -> int:
    """epoch ms의 **UTC 달력 연도**.

    🚨 KST가 아니다 — 연도는 표시가 아니라 **버킷 키**(데이터 축)이고, 시간대를 섞으면 같은
    거래가 두 표에서 다른 해로 간다(WAN-172: 저장·계산은 UTC, 표시만 KST).
    """
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.UTC).year


# ---------------------------------------------------------------------------
# 한 조합의 배치 → 연도 버킷
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YearRow:
    """(구간 × 진입 연도 × 하한 × 문턱 × 보유) 한 칸."""

    segment: str
    year: int
    floor: float
    threshold: float | None
    hold: int
    num_trades: int
    win_rate: float
    mean_net_r: float
    se_net_r: float
    sum_net_r: float
    """거래당 net R의 **합** — 버킷을 다시 합치는 검산이 평균이 아니라 이 값을 쓴다."""
    mean_gross_r: float
    """수수료·슬리피지·펀딩 전 가격 손익(거래당 R) — 「시장에서 얻은 것」(WAN-370 자)."""
    se_gross_r: float
    mean_cost_r: float
    """거래당 총비용 R(≥0). `mean_gross_r − mean_cost_r ≈ mean_net_r`이 닫혀야 한다."""
    identity_max_abs: float
    """검산 — 거래별 `gross − 비용 − net`의 최대 절댓값(R). 0이 아니면 분해가 틀렸다."""
    median_stop_width: float
    """그 해 거래의 손절폭 중앙값(보조 열 · 해석용)."""

    @property
    def sign_is_decided(self) -> bool:
        """`|평균| > 2σ`인가 — 이 저장소의 부호 관문(WAN-381/412). 새 자가 아니다."""
        if self.num_trades < 2 or math.isnan(self.se_net_r):
            return False
        return abs(self.mean_net_r) > SIGN_SIGMA * self.se_net_r


def _stats(values: Sequence[float]) -> tuple[float, float]:
    """(평균, 표준오차). 표본이 2건 미만이면 표준오차는 `nan`."""
    n = len(values)
    if n == 0:
        return math.nan, math.nan
    mean = sum(values) / n
    if n < 2:
        return mean, math.nan
    se = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n)
    return mean, se


def assert_adopted_accounting(cfg: BacktestConfig) -> None:
    """🚨 배치를 `wan424.place_segments`에 위임하므로 **이 모듈에는 북 인자가 한 줄도 없다** —
    그래서 위임이 **채택 회계**를 쓰는지 라벨이 아니라 **값으로** 확인한다.

    익절(부분 익절 포함)은 **메이커 2bp · 슬리피지 0**이 채택 값이다(`take_profit_liquidity` ·
    WAN-370). 위임한 함수가 그 인자를 잃으면 거래당 비용 R이 조용히 커져 §4의 「비용 몫」 열과
    Δ가 통째로 틀리는데, 표는 멀쩡해 보인다 — 이 저장소가 반복해 잡아 온 「라벨과 동작이
    어긋남」(WAN-91/95/112/123/159/373)의 회계 축이다. 어긋나면 **시끄럽게 죽는다**.
    """
    if cfg.take_profit_liquidity is not harness.ADOPTED_TAKE_PROFIT_LIQUIDITY:
        raise AssertionError(
            "위임한 배치가 채택 익절 회계를 쓰지 않습니다 — "
            f"{cfg.take_profit_liquidity!r} != {harness.ADOPTED_TAKE_PROFIT_LIQUIDITY!r}. "
            "`wan424.place_segments`가 `take_profit_liquidity`를 잃었는지 확인하세요(WAN-370/373)."
        )


@dataclass(frozen=True)
class _TradeRec:
    """한 거래의 자(scale) — 버킷 안에서만 쓰는 값 묶음."""

    net_r: float
    gross_r: float
    cost_r: float
    residual_r: float
    win: bool
    stop_width: float


def bucket_by_year(
    payloads: Sequence[CellPayload], *, hold: int, floor: float, threshold: float | None
) -> list[YearRow]:
    """한 조합을 채택 북에 배치해 **구간 × 진입 연도**로 쪼갠 행을 낸다.

    🚨 배치 자체는 `wan424.place_segments` **그대로**다 — 북 인자를 여기서 다시 쓰면 「같은
    팔로 쟀다」가 거짓이 된다(WAN-95/112/123). 이 함수가 더하는 것은 **거래를 진입 연도로
    나누는 것** 하나이고, 나눈 뒤 다시 합치면 WAN-432 행이 나와야 한다(§검산).
    """
    rows: list[YearRow] = []
    for seg in place_segments(payloads):
        cfg = seg.outcome.effective_config
        assert_adopted_accounting(cfg)
        buckets: dict[int, list[_TradeRec]] = {}
        for trade, placement in seg.trades_with_placements():
            risk = placement.risk_amount
            if risk <= 0.0:
                continue
            parts = decompose_trade(trade, cfg)
            entry = float(trade.entry_price)
            buckets.setdefault(year_of(int(trade.entry_time)), []).append(
                _TradeRec(
                    net_r=net_r(trade, placement),
                    gross_r=parts.gross / risk,
                    cost_r=parts.total_cost / risk,
                    residual_r=abs(parts.residual) / risk,
                    win=trade.realized_pnl > 0,
                    stop_width=(
                        abs(entry - placement.stop_price) / entry if entry > 0.0 else math.nan
                    ),
                )
            )
        for year in sorted(buckets):
            recs = buckets[year]
            mean_net, se_net = _stats([r.net_r for r in recs])
            mean_gross, se_gross = _stats([r.gross_r for r in recs])
            widths = [r.stop_width for r in recs if not math.isnan(r.stop_width)]
            rows.append(
                YearRow(
                    segment=seg.segment,
                    year=year,
                    floor=floor,
                    threshold=threshold,
                    hold=hold,
                    num_trades=len(recs),
                    win_rate=sum(1 for r in recs if r.win) / len(recs),
                    mean_net_r=mean_net,
                    se_net_r=se_net,
                    sum_net_r=sum(r.net_r for r in recs),
                    mean_gross_r=mean_gross,
                    se_gross_r=se_gross,
                    mean_cost_r=sum(r.cost_r for r in recs) / len(recs),
                    identity_max_abs=max(r.residual_r for r in recs),
                    median_stop_width=float(pd.Series(widths).median()) if widths else math.nan,
                )
            )
    return rows


def run_grid(
    payloads: Sequence[CellPayload],
    cells: Sequence[ArmCell],
    *,
    floors: Sequence[float] = FLOORS,
    thresholds: Sequence[float] = THRESHOLDS,
    holds: Sequence[int] = HOLD_BARS,
    progress: bool = False,
) -> list[YearRow]:
    rows: list[YearRow] = []
    total = len(floors) * len(thresholds) * len(holds)
    done = 0
    for floor in floors:
        for thr in thresholds:
            for hold in holds:
                rows.extend(
                    bucket_by_year(
                        filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=thr),
                        hold=hold,
                        floor=floor,
                        threshold=thr,
                    )
                )
                done += 1
                if progress:
                    print(
                        f"  배치 {done}/{total} (하한{floor:.1%} K<{thr:.0f} ts{hold})", flush=True
                    )
    return rows


# ---------------------------------------------------------------------------
# 연도별 Δ(채택 하한 − 느슨한 하한) — 시장 몫과 비용 몫을 가른다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YearDelta:
    """한 (연도 × 문턱 × 보유)에서 하한을 느슨한 값 → 채택값으로 올린 몫."""

    year: int
    threshold: float | None
    hold: int
    trades_high: int
    trades_low: int
    delta_net: float
    se_net: float
    delta_gross: float
    """**시장 몫** — gross R(수수료·슬리피지·펀딩 전)의 변화."""
    se_gross: float
    delta_cost: float
    """**비용 몫** — 거래당 비용 R의 **감소**(손절폭이 넓어져 R 비중이 준다 · 기계적이다)."""

    @property
    def net_is_decided(self) -> bool:
        return _decided(self.delta_net, self.se_net)

    @property
    def gross_is_decided(self) -> bool:
        """🚨 두 표본이 포개져 있어 이 σ는 근사다 — **방향 판정에만** 쓴다(모듈 독스트링)."""
        return _decided(self.delta_gross, self.se_gross)


def _decided(value: float, se: float) -> bool:
    if math.isnan(value) or math.isnan(se):
        return False
    return abs(value) > SIGN_SIGMA * se


def year_deltas(
    rows: Sequence[YearRow],
    *,
    segment: str = harness.SEGMENT_FULL,
    high: float = ADOPTED_FLOOR,
    low: float = LOOSE_FLOOR,
) -> list[YearDelta]:
    """연도 × 조합마다 (하한 `high` − 하한 `low`)의 net·gross·비용 Δ.

    한쪽 하한에 그 해 거래가 없으면 그 칸은 내지 않는다(0으로 채우면 「없는 거래」가 Δ를
    만든다 — WAN-367 「하드 제로는 경보다」).
    """
    index = {(r.year, r.floor, r.threshold, r.hold): r for r in rows if r.segment == segment}
    out: list[YearDelta] = []
    years = sorted({y for (y, f, _t, _h) in index if f == high})
    for year in years:
        for thr in sorted({r.threshold for r in rows if r.threshold is not None}):
            for hold in sorted({r.hold for r in rows}):
                hi = index.get((year, high, thr, hold))
                lo = index.get((year, low, thr, hold))
                if hi is None or lo is None or hi.num_trades == 0 or lo.num_trades == 0:
                    continue
                out.append(
                    YearDelta(
                        year=year,
                        threshold=thr,
                        hold=hold,
                        trades_high=hi.num_trades,
                        trades_low=lo.num_trades,
                        delta_net=hi.mean_net_r - lo.mean_net_r,
                        se_net=_compose_se(hi.se_net_r, lo.se_net_r),
                        delta_gross=hi.mean_gross_r - lo.mean_gross_r,
                        se_gross=_compose_se(hi.se_gross_r, lo.se_gross_r),
                        delta_cost=lo.mean_cost_r - hi.mean_cost_r,
                    )
                )
    return out


def _compose_se(se_a: float, se_b: float) -> float:
    if math.isnan(se_a) or math.isnan(se_b):
        return math.nan
    return math.sqrt(se_a**2 + se_b**2)


# ---------------------------------------------------------------------------
# 판정 — 사람이 표를 보고 정하지 않는다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YearCensus:
    """한 해의 조합 인구조사 — 시장 몫이 있는 해인가."""

    year: int
    combos: int
    gross_up: int
    """Δgross > 0이고 **부호가 결정된** 조합 수."""
    gross_down: int
    undecided: int
    median_delta_gross: float
    median_delta_net: float
    median_delta_cost: float
    trades_high: int
    """그 해 채택 하한 조합의 거래 수 중앙값(표본 크기를 감추지 않는다)."""

    @property
    def is_market_year(self) -> bool:
        """**과반**이 「Δgross > 0 · 부호 결정」인가 — 착수 전에 정한 선이다(WAN-161)."""
        return self.combos > 0 and self.gross_up * 2 > self.combos

    @property
    def is_undecided_year(self) -> bool:
        """어느 쪽도 과반이 아닌 해 — 「없다」가 아니라 **미결정**이다(완료기준 4)."""
        return self.combos > 0 and not self.is_market_year and self.gross_down * 2 <= self.combos


def census(deltas: Sequence[YearDelta]) -> list[YearCensus]:
    out: list[YearCensus] = []
    for year in sorted({d.year for d in deltas}):
        rows = [d for d in deltas if d.year == year]
        up = sum(1 for d in rows if d.gross_is_decided and d.delta_gross > 0)
        down = sum(1 for d in rows if d.gross_is_decided and d.delta_gross < 0)
        out.append(
            YearCensus(
                year=year,
                combos=len(rows),
                gross_up=up,
                gross_down=down,
                undecided=len(rows) - up - down,
                median_delta_gross=float(pd.Series([d.delta_gross for d in rows]).median()),
                median_delta_net=float(pd.Series([d.delta_net for d in rows]).median()),
                median_delta_cost=float(pd.Series([d.delta_cost for d in rows]).median()),
                trades_high=int(pd.Series([d.trades_high for d in rows]).median()),
            )
        )
    return out


def verdict(census_rows: Sequence[YearCensus]) -> str:
    """헤드라인 — 「넓은 손절이 이기는 해」가 여러 해인가 최근 2년만인가. **코드가 낸다**.

    🚨 미결정을 「없다」로 접지 않는다(완료기준 4) — 버킷당 표본이 연 100~500건대라 2σ가 차이를
    덮는 해가 나오는 것이 정상이고, 그 개수를 문장에 함께 싣는다.
    """
    if not census_rows:
        return "⚠️ 판정 불가 — 연도 버킷이 없다."
    by_year = {c.year: c for c in census_rows}
    present = [y for y in IS_YEARS if y in by_year]
    if not present:
        return "⚠️ 판정 불가 — 앞구간 연도(2020~2023)의 버킷이 하나도 없다."
    market = [y for y in present if by_year[y].is_market_year]
    undecided = [y for y in present if by_year[y].is_undecided_year]
    down = [
        y for y in present if not by_year[y].is_market_year and not by_year[y].is_undecided_year
    ]

    def _years(label: str, ys: Sequence[int]) -> str:
        if not ys:
            return f"{label} 0개"
        joined = ", ".join(str(y) for y in ys)
        return f"{label} {len(ys)}개({joined})"

    tail = (
        f" 앞구간 {len(present)}개 해 중 "
        + _years("시장 몫 있는 해", market)
        + " · "
        + _years("미결정", undecided)
        + " · "
        + _years("시장 몫이 **반대**로 결정된 해", down)
        + "."
    )
    if len(market) >= MULTI_YEAR_GATE:
        return (
            f"📌 **여러 해에 걸쳐 보인다** — 착수 전에 박은 선(앞구간 연도 중 "
            f"{MULTI_YEAR_GATE}개 이상)을 넘는다.{tail}"
        )
    return (
        f"🚨 **최근 2년에만 있다** — 앞구간 연도 중 시장 몫이 있는 해가 {len(market)}개로 "
        f"착수 전에 박은 선({MULTI_YEAR_GATE}개)을 못 넘는다.{tail}"
    )


def power_reading(census_rows: Sequence[YearCensus]) -> str:
    """🚨 **관문이 어느 해도 통과시키지 않으면 그 사실을 먼저 적는다.**

    판정문만 읽으면 「최근 2년에만 있다」가 *앞구간이 아니라고 말했다*로 읽힌다. 그런데 같은
    관문이 **뒷구간 연도까지** 통과시키지 못하면 그 문장의 뜻은 *어느 해도 결정되지 않았다*이고,
    그것은 앞구간을 부정한 것이 아니라 **연도 크기 버킷에서 이 자가 판정을 못 낸다**는 뜻이다.
    완료기준 4가 「미결정을 없다로 읽지 말라」고 못 박은 자리를 문장으로 만든다.
    """
    if not census_rows:
        return "⚠️ 연도 버킷이 없어 관문의 분해능을 말할 수 없다."
    passed = [c.year for c in census_rows if c.is_market_year]
    total = len(census_rows)
    if passed:
        joined = ", ".join(str(y) for y in passed)
        return (
            f"📌 관문을 통과한 해는 {len(passed)}/{total}개다({joined}) — 앞구간·뒷구간을 다 "
            "센 값이고, 그래서 위 판정문의 개수는 **그 안에서** 읽는다."
        )
    best = max(census_rows, key=lambda c: c.gross_up)
    need = best.combos // 2 + 1
    return (
        f"🚨 **이 관문은 어느 해도 통과시키지 않는다 — 뒷구간 연도까지 포함해 0/{total}개다.** "
        f"가장 센 해가 {best.year}({best.gross_up}/{best.combos}조합)이고 과반선은 {need}이다. "
        "즉 위 판정문의 「최근 2년에만 있다」는 **앞구간이 아니라고 말한 것이 아니라 어느 해도 "
        "결정되지 않았다**는 뜻이다(완료기준 4). 연도로 쪼개면 버킷이 연 100~500거래로 내려가 "
        "2σ가 차이를 덮으므로, 이 표가 낼 수 있는 결론은 **「쪼갠 해 단위로는 못 가른다」**까지다."
    )


@dataclass(frozen=True)
class HalfRow:
    """연도 묶음(앞구간 연도 · 걸치는 해 · 뒷구간 연도)의 조합 평균 Δ."""

    label: str
    years: tuple[int, ...]
    combos: int
    delta_net: float
    delta_gross: float
    trades_high: int
    trades_low: int


#: 연도를 묶는 세 칸 — `IS_YEARS`에서 파생한다(리터럴을 두 번 적지 않는다).
def half_groups(years: Sequence[int]) -> list[tuple[str, tuple[int, ...]]]:
    present = sorted(set(years))
    straddle = tuple(y for y in present if y == max(IS_YEARS) + 1)
    return [
        ("앞구간 연도", tuple(y for y in present if y in IS_YEARS)),
        ("걸치는 해", straddle),
        ("뒷구간 연도", tuple(y for y in present if y > max(IS_YEARS) + 1)),
    ]


def half_split(
    rows: Sequence[YearRow],
    *,
    segment: str = harness.SEGMENT_FULL,
    high: float = ADOPTED_FLOOR,
    low: float = LOOSE_FLOOR,
) -> list[HalfRow]:
    """연도를 앞/걸침/뒤로 묶어 **조합마다 Δ를 내고 평균**한다(WAN-432 `cost_vs_market`와 같은 식).

    🚨 이 묶음은 WAN-432의 `is`/`oos_warm` **행과 같은 물건이 아니다** — 그쪽은 구간마다 **다른
    지갑**을 배치한 것이고 이것은 `full` 한 지갑의 거래를 나눈 것이다. 그래서 값이 같기를 요구할
    수 없고, **방향이 같은지**만 읽는다(요약이 그 대조를 함께 찍는다).
    """
    index = {(r.year, r.floor, r.threshold, r.hold): r for r in rows if r.segment == segment}
    thresholds = sorted({r.threshold for r in rows if r.threshold is not None})
    holds = sorted({r.hold for r in rows})
    out: list[HalfRow] = []
    for label, years in half_groups([r.year for r in rows if r.segment == segment]):
        if not years:
            continue
        dn = dg = 0.0
        k = 0
        n_hi = n_lo = 0
        for thr in thresholds:
            for hold in holds:
                hi = [index.get((y, high, thr, hold)) for y in years]
                lo = [index.get((y, low, thr, hold)) for y in years]
                hi_rows = [r for r in hi if r is not None and r.num_trades]
                lo_rows = [r for r in lo if r is not None and r.num_trades]
                if not hi_rows or not lo_rows:
                    continue
                hi_n = sum(r.num_trades for r in hi_rows)
                lo_n = sum(r.num_trades for r in lo_rows)
                dn += (
                    sum(r.sum_net_r for r in hi_rows) / hi_n
                    - sum(r.sum_net_r for r in lo_rows) / lo_n
                )
                dg += (
                    sum(r.mean_gross_r * r.num_trades for r in hi_rows) / hi_n
                    - sum(r.mean_gross_r * r.num_trades for r in lo_rows) / lo_n
                )
                k += 1
                n_hi += hi_n
                n_lo += lo_n
        if k:
            out.append(
                HalfRow(
                    label=label,
                    years=years,
                    combos=k,
                    delta_net=dn / k,
                    delta_gross=dg / k,
                    trades_high=n_hi // k,
                    trades_low=n_lo // k,
                )
            )
    return out


def cancellation_reading(census_rows: Sequence[YearCensus], halves: Sequence[HalfRow]) -> str:
    """앞구간의 ≈0이 **한 해의 큰 음수가 나머지를 지운 것**인지 한 줄로 낸다.

    WAN-432가 앞구간 Δgross를 −0.009R로 냈을 때 그 수는 4년을 한 덩어리로 본 값이라 *4년 내내
    아무 일도 없었다*로 읽힌다. 연도로 쪼개면 그 ≈0의 안쪽이 보인다 — 그 사실을 표에서 파생한다.
    """
    by_year = {c.year: c for c in census_rows}
    present = [y for y in IS_YEARS if y in by_year]
    if not present:
        return ""
    pos = [y for y in present if by_year[y].median_delta_gross > 0]
    neg = [y for y in present if by_year[y].median_delta_gross < 0]
    front = next((h for h in halves if h.label == "앞구간 연도"), None)
    if not neg or len(pos) <= len(neg):
        return (
            f"📌 앞구간 연도의 Δgross 중앙값은 양수 {len(pos)}개 · 음수 {len(neg)}개다 — "
            "한쪽으로 몰려 있지 않다."
        )
    worst = min(neg, key=lambda y: by_year[y].median_delta_gross)
    joined = ", ".join(str(y) for y in pos)
    tail = f"(묶어서 재면 {front.delta_gross:+.4f}R)" if front else ""
    return (
        f"🚨 **앞구간의 ≈0은 「4년 내내 아무 일도 없었다」가 아니라 상쇄다** — 앞구간 연도 "
        f"{len(present)}개 중 **{len(pos)}개({joined})는 Δgross 중앙값이 양수**이고, "
        f"**{worst}년 하나가 {by_year[worst].median_delta_gross:+.3f}R로 크게 음수**다{tail}. "
        "⚠️ 다만 그 양수들은 **전부 미결정**이라 「앞구간에도 있었다」로 읽을 수 없다 — 읽을 수 "
        "있는 것은 *한 덩어리 평균이 그 안의 모양을 지운다*까지다."
    )


def character_reading(census_rows: Sequence[YearCensus], btc: dict[int, float]) -> str:
    """보조 열로 「넓은 손절이 이기는 해」의 **성격**을 본다 — 🚨 해석용이고 판정이 아니다.

    그해 BTC가 올랐나 내렸나로 Δgross 중앙값의 부호를 갈라 본다. 이슈가 보조 열을 넣은 이유가
    *「특정 시장 성격의 해」인지 읽는 데만 쓴다*이므로 그 한 줄을 표에서 파생한다.
    """
    rows = [(c, btc[c.year]) for c in census_rows if c.year in btc]
    if len(rows) < 3:
        return ""
    up = [c for c, b in rows if b > 0]
    down = [c for c, b in rows if b <= 0]
    if not up or not down:
        return ""
    up_pos = [c.year for c in up if c.median_delta_gross > 0]
    down_pos = [c.year for c in down if c.median_delta_gross > 0]
    return (
        f"📌 **보조 열로 본 모양(판정 아님)**: BTC가 **내린 해 {len(down)}개 중 "
        f"{len(down_pos)}개**에서 Δgross 중앙값이 양수이고, **오른 해 {len(up)}개 중 "
        f"{len(up_pos)}개**다. ⚠️ 해 표본이 {len(rows)}개이고 어느 해도 관문을 통과하지 않았으므로 "
        "「내린 해에 넓은 손절이 이긴다」는 **가설이지 판정이 아니다** — 다만 뒷구간(2025~2026)이 "
        "하필 **둘 다 내린 해**라, WAN-432가 본 「시장 몫은 뒷구간에만」이 *최근*이 아니라 "
        "*장세*의 다른 이름일 수 있다는 것까지는 읽힌다."
    )


# ---------------------------------------------------------------------------
# 표시 조합 — 결과를 보고 고르지 않는다
# ---------------------------------------------------------------------------


def display_combo() -> tuple[int, float]:
    """연도 표를 그릴 (보유, 문턱) — **WAN-423 §7 기준값에서 파생**한다.

    🚨 내 표에서 최선 칸을 고르면 그게 곧 과최적화 보고다(WAN-161). 그래서 조합을 **이미 있던
    기준값**(`wan424.WAN423_REFERENCE`)에서 뽑고, 그 안에서 채택 하한 행의 **`full` 거래 수가
    가장 많은** 쪽을 쓴다 — 성적이 아니라 **표본 크기**로 고른다(얇은 칸으로 연도를 쪼개면
    버킷이 두 자릿수로 내려간다).
    """
    best: tuple[int, float] | None = None
    best_n = -1
    for (hold, floor, thr), segs in WAN423_REFERENCE.items():
        if round(floor, 6) != round(ADOPTED_FLOOR, 6):
            continue
        n = segs.get(harness.SEGMENT_FULL, (math.nan, 0))[1]
        if n > best_n:
            best, best_n = (hold, thr), n
    if best is None:  # pragma: no cover - 기준값이 채택 하한을 담고 있다.
        raise ValueError("WAN423_REFERENCE에 채택 하한 행이 없습니다.")
    return best


# ---------------------------------------------------------------------------
# 보조 열 — 그해 BTC 연간 수익률 (해석용 · 판정 아님)
# ---------------------------------------------------------------------------


def _db_exists() -> bool:
    """저장 DB가 있는가 — 없으면 보조 열을 **지어내지 않고 뺀다**(`—`)."""
    return Path(harness.DB_PATH).exists()


def btc_annual_returns(
    *, store: OhlcvStore | None = None, symbol: str = BTC_SYMBOL, timeframe: str = BTC_TIMEFRAME
) -> dict[int, float]:
    """창 안에서 그해 첫 봉 시가 → 마지막 봉 종가의 수익률(2020·2026은 **부분 연도**).

    ⚠️ 보조 열이다 — 판정에 쓰지 않는다. 「넓은 손절이 이기는 해 = 특정 시장 성격의 해」인지
    읽는 데만 쓴다. 데이터가 없으면 그 해를 **지어내지 않고 빼고**(`—`) 표에 그대로 밝힌다.
    """
    store = store or OhlcvStore(harness.DB_PATH)
    frame = store.load(
        symbol,
        timeframe,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
    )
    if frame.empty:
        return {}
    out: dict[int, float] = {}
    frame = frame.sort_values("open_time")
    for year, part in frame.groupby(frame["open_time"].map(lambda ms: year_of(int(ms)))):
        first_open = float(part.iloc[0]["open"])
        last_close = float(part.iloc[-1]["close"])
        if first_open > 0:
            out[int(year)] = last_close / first_open - 1.0
    return out


# ---------------------------------------------------------------------------
# 구간 경계 인구조사 — 「2020~2023은 전부 앞구간」을 실측으로 건다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundaryRow:
    """한 해가 `is`·`oos_warm` 중 어디에 나타나는가(칸마다 경계가 다르므로 실측한다)."""

    year: int
    is_trades: int
    oos_warm_trades: int

    @property
    def label(self) -> str:
        if self.is_trades and self.oos_warm_trades:
            return "걸침"
        if self.is_trades:
            return "앞구간"
        if self.oos_warm_trades:
            return "뒷구간"
        return "—"


def boundary_census(rows: Sequence[YearRow]) -> list[BoundaryRow]:
    """🚨 `IS_FRACTION`(2/3) 경계는 **칸마다 다르다**(종목별 상장 시점이 달라 창 길이가 다르다).

    그래서 「2020~2023은 앞구간」을 주장하지 않고 **거래가 실제로 어느 구간에 나타나는지** 센다.
    걸치는 해가 `IS_YEARS`에 들어 있으면 판정이 흔들리므로 요약이 그 사실을 찍는다.
    """
    out: list[BoundaryRow] = []
    for year in sorted({r.year for r in rows}):
        out.append(
            BoundaryRow(
                year=year,
                is_trades=sum(
                    r.num_trades for r in rows if r.year == year and r.segment == harness.SEGMENT_IS
                ),
                oos_warm_trades=sum(
                    r.num_trades
                    for r in rows
                    if r.year == year and r.segment == harness.SEGMENT_OOS_WARM
                ),
            )
        )
    return out


def boundary_reading(bounds: Sequence[BoundaryRow]) -> str:
    """앞구간 연도 판정이 걸침 때문에 흔들리는지 한 줄로 낸다."""
    by_year = {b.year: b for b in bounds}
    straddling = [y for y in IS_YEARS if y in by_year and by_year[y].label == "걸침"]
    if straddling:
        return (
            f"🚨 **앞구간 연도로 센 해 중 {', '.join(str(y) for y in straddling)}가 구간을 "
            "걸친다** — 그 해의 판정은 앞구간만의 것이 아니다."
        )
    return (
        "📌 앞구간 연도(2020~2023)는 **전부 `is`에만** 나타난다 — 칸마다 다른 2/3 경계가 "
        "그 해들보다 뒤에 있다는 실측이고, 그래서 그 해를 「앞구간」으로 세도 된다."
    )


# ---------------------------------------------------------------------------
# 검산 — 연도 버킷을 다시 합치면 WAN-432 공개 CSV인가 (완료기준 3)
# ---------------------------------------------------------------------------

#: 다시 합쳐 대조하는 열 — WAN-432 `FloorRow`에 있는 것만 본다.
#: (중앙값은 버킷에서 복원되지 않아 뺀다 — 지어내지 않는다.)
CHECK_FLOAT_COLUMNS: tuple[str, ...] = ("mean_net_r", "win_rate", "gross_r", "cost_r")


def on_adopted_coordinates(
    *,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    holds: Sequence[int],
    floors: Sequence[float],
) -> bool:
    """이 실행이 WAN-432 좌표를 그대로 도는가 — 검산이 성립하는 유일한 조건.

    🚨 좁혀 돈 파일럿을 공개 CSV와 대조하면 **좌표 차이가 배선 오류처럼 보인다**(WAN-381이
    실제로 겪었다). 그때는 대조하지 않고 「건너뜀」으로 찍는다.
    """
    return (
        tuple(symbols) == SYMBOLS
        and tuple(timeframes) == TIMEFRAMES
        and tuple(holds) == HOLD_BARS
        and tuple(floors) == FLOORS
    )


_PoolKey = tuple[int, float, float | None, str]


def pool_years(rows: Sequence[YearRow]) -> dict[_PoolKey, dict[str, float]]:
    """연도 버킷을 (보유 × 하한 × 문턱 × 구간)으로 다시 합친다 — 거래 가중이다."""
    out: dict[_PoolKey, dict[str, float]] = {}
    for r in rows:
        key = (r.hold, r.floor, r.threshold, r.segment)
        acc = out.setdefault(
            key, {"num_trades": 0.0, "net": 0.0, "wins": 0.0, "gross": 0.0, "cost": 0.0}
        )
        acc["num_trades"] += r.num_trades
        acc["net"] += r.sum_net_r
        acc["wins"] += r.win_rate * r.num_trades
        acc["gross"] += r.mean_gross_r * r.num_trades
        acc["cost"] += r.mean_cost_r * r.num_trades
    return out


def checksum_wan432(
    rows: Sequence[YearRow], *, reference_csv: Path = WAN432_CSV, adopted: bool = True
) -> tuple[list[str], float]:
    """연도 버킷의 합 ↔ WAN-432 공개 CSV. (보고 줄, 최대 절대차)를 낸다.

    🚨 이 등식이 이 이슈의 자격 증명이다 — 버킷이 거래를 **잃지도 겹치지도** 않았다면 다시
    합친 값이 그 표와 같아야 한다. 거래 수는 **정확히**, 평균은 합산 순서가 달라진 만큼
    (`CHECKSUM_TOL` 안에서) 같아야 한다.

    ⚠️ 정확한 0을 요구하지 않는다 — 연도로 나눠 더하면 부동소수 **합산 순서**가 달라진다.
    그것이 이 검산의 한계이고, 거래 수·승수의 정수 일치가 그 빈틈을 메운다.
    """
    if not adopted:
        return ["⏭️ 건너뜀 — 좁혀 돈 좌표라 공개 CSV와의 대조가 성립하지 않는다(WAN-381)."], math.nan
    if not reference_csv.exists():
        return [f"⚠️ 기준 CSV 없음: {reference_csv}"], math.nan
    ref = pd.read_csv(reference_csv)
    index = {
        (int(r["hold"]), round(float(r["floor"]), 6), float(r["threshold"]), str(r["segment"])): r
        for _, r in ref.iterrows()
    }
    lines: list[str] = []
    worst = 0.0
    compared = 0
    trade_mismatch = 0
    for (hold, floor, thr, segment), acc in sorted(
        pool_years(rows).items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0.0, kv[0][3])
    ):
        if thr is None:
            continue
        want = index.get((hold, round(floor, 6), float(thr), segment))
        if want is None:
            lines.append(f"❌ 기준 행 없음: ts{hold} 하한{floor:.1%} K<{thr:.0f} {segment}")
            continue
        compared += 1
        n = acc["num_trades"]
        if int(n) != int(want["num_trades"]):
            trade_mismatch += 1
            lines.append(
                f"❌ 거래 수 불일치: ts{hold} 하한{floor:.1%} K<{thr:.0f} {segment} — "
                f"버킷 합 {int(n)} vs 기준 {int(want['num_trades'])}"
            )
            continue
        got = {
            "mean_net_r": acc["net"] / n if n else math.nan,
            "win_rate": acc["wins"] / n if n else math.nan,
            "gross_r": acc["gross"] / n if n else math.nan,
            "cost_r": acc["cost"] / n if n else math.nan,
        }
        for col in CHECK_FLOAT_COLUMNS:
            exp = float(want[col])
            if math.isnan(got[col]) and math.isnan(exp):
                continue
            worst = max(worst, abs(got[col] - exp))
    if compared == 0:
        lines.append("❌ 대조한 행이 하나도 없다 — 좌표가 다르면 이 검산은 성립하지 않는다.")
    else:
        mark = "✅" if worst == 0.0 else ("≈" if worst < CHECKSUM_TOL else "❌")
        lines.append(
            f"{mark} 연도 버킷 합 × {compared}행 × {len(CHECK_FLOAT_COLUMNS)}열 "
            f"(＋거래 수 정수 일치) — 최대 절대차 {worst:.2e}"
        )
        if trade_mismatch:
            lines.append(
                f"❌ 거래 수가 어긋난 행 {trade_mismatch}개 — 버킷이 거래를 잃거나 겹쳤다."
            )
    return lines, (math.inf if trade_mismatch else worst)


def checksum_passes(worst: float) -> bool:
    """검산이 합격인가 — **보고 마크와 종료 코드가 같은 자를 쓰게 하는 한 곳**(WAN-432 규약).

    `math.nan`(대조 건너뜀)은 합격이다 — 좁혀 돈 파일럿은 애초에 이 등식을 물을 좌표가 아니다.
    """
    return math.isnan(worst) or worst < CHECKSUM_TOL


# ---------------------------------------------------------------------------
# 표
# ---------------------------------------------------------------------------


def rows_to_frame(rows: Sequence[YearRow]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(r) for r in rows])


def frame_to_rows(frame: pd.DataFrame) -> list[YearRow]:
    fields = {f.name for f in dataclasses.fields(YearRow)}
    out: list[YearRow] = []
    for rec in frame.to_dict("records"):
        rec = {k: v for k, v in rec.items() if k in fields}
        thr = rec["threshold"]
        rec["threshold"] = (
            None if thr is None or (isinstance(thr, float) and math.isnan(thr)) else float(thr)
        )
        rec["hold"] = int(rec["hold"])
        rec["year"] = int(rec["year"])
        rec["num_trades"] = int(rec["num_trades"])
        out.append(YearRow(**rec))
    return out


def _fmt_r(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:+.3f}"


def _fmt_pct(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:.2%}"


def _pm(se: float) -> str:
    """`± 2σ` 칸 — 표본 1건이면 `±—`다(`nan`을 숫자처럼 찍지 않는다)."""
    return "±—" if math.isnan(se) else f"±{2 * se:.3f}"


def _mark(value: float, se: float) -> str:
    if not _decided(value, se):
        return ""
    return "＋" if value > 0 else "−"


def render_summary(
    rows: Sequence[YearRow],
    *,
    elapsed: float | None = None,
    adopted: bool = True,
    btc: dict[int, float] | None = None,
) -> str:
    hold, thr = display_combo()
    deltas = year_deltas(rows)
    census_rows = census(deltas)
    halves = half_split(rows)
    bounds = boundary_census(rows)
    check_lines, _worst = checksum_wan432(rows, adopted=adopted)
    btc = btc if btc is not None else {}
    index = {(r.year, r.floor, r.threshold, r.hold): r for r in rows if r.segment == "full"}
    years = sorted({r.year for r in rows if r.segment == "full"})
    lines = [
        "# WAN-433 — 스토캐스틱 팔의 「넓은 손절이 이긴다」가 최근 2년만의 일인가 (연도별)",
        "",
        "롱 · 31종목 × 9TF(1h~1w) · 못 박은 6년 · 첫 탭 · 재진입 없음 · `pen_5bp` × 같은 분 "
        "익절 금지 · 채택 북 · 복리 끔 · 가드 끔(손절폭 하한이 대신) · **핀 없음**.",
        f"연도는 `full` 판 거래의 **진입 시각(UTC) 달력 연도**다(다시 돌려 자르지 않았다). "
        f"대조 쌍 = 하한 **{ADOPTED_FLOOR:.0%}** 대 **{LOOSE_FLOOR:.0%}**.",
        "",
        f"## 판정 — {verdict(census_rows)}",
        "",
        "🚨 **미결정을 「없다」로 읽지 말 것** — 버킷당 표본이 연 100~500건대라 2σ가 차이를 덮는 "
        "해가 정상이다(완료기준 4). 🚨 **어느 갈래도 채택 근거가 아니다**(WAN-161).",
        "",
        power_reading(census_rows),
        "",
        boundary_reading(bounds),
        "",
        f"## 연도 × 하한 (표시 조합 ts{hold} · %K<{thr:.0f} · 완료기준 1)",
        "",
        "📌 표시 조합은 **WAN-423 §7 기준값에서 파생**한다(`display_combo` — 내 표의 최선 칸을 "
        "고르지 않는다). 판정은 이 조합이 아니라 **15조합 인구조사**가 낸다.",
        "",
        "| 연도 | 하한 | 거래 | 승률 | 거래당 net R ± 2σ | gross R | 비용 R | 손절폭 중앙 "
        "| BTC 그해 |",
        "| --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for year in years:
        for floor in (LOOSE_FLOOR, ADOPTED_FLOOR):
            row = index.get((year, floor, thr, hold))
            if row is None:
                lines.append(
                    f"| {year} | {floor:.0%} | — | — | — | — | — | — | "
                    f"{_fmt_pct(btc[year]) if year in btc else '—'} |"
                )
                continue
            lines.append(
                f"| {year} | {floor:.0%} | {row.num_trades} | {row.win_rate:.1%} "
                f"| {row.mean_net_r:+.3f}{_mark(row.mean_net_r, row.se_net_r)} "
                f"{_pm(row.se_net_r)} "
                f"| {_fmt_r(row.mean_gross_r)} | {row.mean_cost_r:.3f} "
                f"| {_fmt_pct(row.median_stop_width)} "
                f"| {_fmt_pct(btc[year]) if year in btc else '—'} |"
            )
    lines += [
        "",
        "⚠️ BTC 그해 수익률·손절폭 중앙값은 **보조 열이다**(판정 아님) — 2020은 09-15~, "
        "2026은 ~07-22의 **부분 연도**다.",
        "",
        f"## 연도별 Δ(하한 {ADOPTED_FLOOR:.0%} − {LOOSE_FLOOR:.0%}) — 시장 몫과 비용 몫 "
        "(완료기준 2)",
        "",
        f"표시 조합(ts{hold} · %K<{thr:.0f})의 Δ와, 그 옆에 **15조합 인구조사**를 같이 둔다.",
        "",
        "| 연도 | Δnet ± 2σ | Δgross(시장) ± 2σ | Δ비용(감소) | 조합 중 Δgross＋ | 조합 중 "
        "Δgross− | 미결정 | Δgross 중앙 | 채택 하한 거래(중앙) |",
        "| --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    by_year_census = {c.year: c for c in census_rows}
    shown = {(d.year): d for d in deltas if d.hold == hold and d.threshold == thr}
    for year in years:
        c = by_year_census.get(year)
        d = shown.get(year)
        if c is None:
            continue
        net = f"{d.delta_net:+.3f}{_mark(d.delta_net, d.se_net)} {_pm(d.se_net)}" if d else "—"
        gross = (
            f"{d.delta_gross:+.3f}{_mark(d.delta_gross, d.se_gross)} {_pm(d.se_gross)}"
            if d
            else "—"
        )
        cost = f"{d.delta_cost:+.3f}" if d else "—"
        flag = "📌" if c.is_market_year else ("·" if c.is_undecided_year else "🚨")
        lines.append(
            f"| {year} {flag} | {net} | {gross} | {cost} | {c.gross_up}/{c.combos} "
            f"| {c.gross_down}/{c.combos} | {c.undecided}/{c.combos} "
            f"| {c.median_delta_gross:+.3f} | {c.trades_high} |"
        )
    lines += [
        "",
        "📌 = 그 해 조합의 **과반**이 「Δgross > 0 · 부호 결정」(시장 몫 있는 해) · · = 미결정 · "
        "🚨 = 시장 몫이 **반대**로 결정된 해.",
        "",
        "🚨 **Δ의 2σ는 근사다** — 하한 4% 거래는 1% 거래의 **부분집합**이라 두 표본이 독립이 "
        "아니다. 방향 판정에만 쓰고 크기로 인용하지 않는다.",
        "",
        "🚨 **비용 몫은 기계적이다** — 하한을 올리면 손절폭이 넓어져 같은 수수료의 R 비중이 그냥 "
        "준다(WAN-370). 그래서 읽을 것은 **시장 몫(Δgross)이 어느 해에 있는가**다.",
        "",
        "## 연도를 다시 묶으면 — 앞구간 연도 · 걸치는 해 · 뒷구간 연도",
        "",
        "| 묶음 | 해 | 조합 | Δ거래당 net R | Δgross(시장) | 채택 하한 거래 | 느슨한 하한 거래 |",
        "| -- | -- | --: | --: | --: | --: | --: |",
    ]
    for h in halves:
        joined = ", ".join(str(y) for y in h.years)
        lines.append(
            f"| {h.label} | {joined} | {h.combos} | {h.delta_net:+.4f} | {h.delta_gross:+.4f} "
            f"| {h.trades_high} | {h.trades_low} |"
        )
    lines += [
        "",
        "🚨 **이 묶음은 WAN-432의 `is`/`oos_warm` 행과 같은 물건이 아니다** — 그쪽은 구간마다 "
        "**다른 지갑**을 배치한 값이고 이것은 `full` 한 지갑의 거래를 나눈 것이다. 값이 같기를 "
        "요구할 수 없고 **방향만** 읽는다.",
        "",
        cancellation_reading(census_rows, halves),
        "",
        character_reading(census_rows, btc),
        "",
        "## 구간 경계 인구조사 — 어느 해가 앞구간인가",
        "",
        "| 연도 | `is` 거래 | `oos_warm` 거래 | 어디 |",
        "| --: | --: | --: | -- |",
    ]
    for b in bounds:
        lines.append(f"| {b.year} | {b.is_trades} | {b.oos_warm_trades} | {b.label} |")
    lines += [
        "",
        "⚠️ 2/3 경계는 **칸마다 다르다**(종목별 상장 시점이 달라 창 길이가 다르다) — 그래서 "
        "주장하지 않고 **실측**한다. 표시 조합이 아니라 전 조합 합이다.",
        "",
        "## 검산 — 연도 버킷의 합 ≡ WAN-432 공개 CSV (완료기준 3)",
        "",
        *[f"- {line}" for line in check_lines],
        "",
        "비용 분해 항등식(`gross − 비용 − net`)의 최대 절댓값: "
        f"{max((r.identity_max_abs for r in rows), default=math.nan):.2e} R.",
        "",
        "⚠️ **표에서 최선 해를 고르지 말 것**(WAN-161) — 읽을 것은 **모양**이다. 전부 "
        "`pen_5bp`(체결 보수화) 위의 값이고 **채택 좌표가 아니다**(채택 북 −0.12R과 나란히 놓지 "
        "말 것) · **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변**(이 표는 *그 팔의 "
        "하한 우위가 어느 해에 있었나*를 묻는다 — **다른 질문**) · ⚠️ 창이 2020-09부터라 "
        "**2020-03급 폭락이 없다**.",
    ]
    if elapsed is not None:
        lines.append(f"\n실측 {elapsed:.0f}초({elapsed / 60:.0f}분).")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--from-csv", action="store_true", help="CSV에서 요약만 다시 만든다.")
    parser.add_argument(
        "--elapsed",
        type=float,
        default=None,
        help=(
            "`--from-csv`가 실측 비용 줄을 되살릴 때 쓰는 초(완료기준 5). "
            "CSV에는 시간이 없으므로, 안 주면 그 줄을 **지어내지 않고 뺀다**."
        ),
    )
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--symbols", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--timeframes", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--holds", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--floors", default=None, help="파일럿용 좁히기(콤마 · 비율).")
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = frame_to_rows(pd.read_csv(args.csv))
        summary = render_summary(
            rows, elapsed=args.elapsed, btc=btc_annual_returns() if _db_exists() else {}
        )
        args.summary.write_text(summary, encoding="utf-8")
        print(summary)
        return 0

    started = time.monotonic()
    symbols = tuple(args.symbols.split(",")) if args.symbols else SYMBOLS
    timeframes = tuple(args.timeframes.split(",")) if args.timeframes else TIMEFRAMES
    holds = tuple(int(h) for h in args.holds.split(",")) if args.holds else HOLD_BARS
    floors = tuple(float(f) for f in args.floors.split(",")) if args.floors else FLOORS
    payloads = build_base_payloads(
        jobs=args.jobs, payload_dir=args.payload_dir, symbols=symbols, timeframes=timeframes
    )
    print(f"base 후보 {time.monotonic() - started:.0f}s · 칸 {len(payloads)}", flush=True)
    cells = build_arm_cells(payloads, jobs=args.jobs, min_width=ARM_FLOOR)
    print(f"팔 후보(하한 {ARM_FLOOR:.1%}) {time.monotonic() - started:.0f}s", flush=True)
    rows = run_grid(payloads, cells, floors=floors, holds=holds, progress=True)
    elapsed = time.monotonic() - started
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(args.csv, index=False)
    adopted = on_adopted_coordinates(
        symbols=symbols, timeframes=timeframes, holds=holds, floors=floors
    )
    summary = render_summary(rows, elapsed=elapsed, adopted=adopted, btc=btc_annual_returns())
    args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    _lines, worst = checksum_wan432(rows, adopted=adopted)
    return 0 if checksum_passes(worst) else 1


if __name__ == "__main__":
    raise SystemExit(main())
