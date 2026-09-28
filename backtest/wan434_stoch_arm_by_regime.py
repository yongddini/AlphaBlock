"""WAN-434: 스토캐스틱 팔이 **「하락장 전략」인가** — 월봉 기준 장세 × 필터로 쪼갠다.

## 왜 이 모듈이 있나

WAN-433이 연도로 쪼개 보니 앞/뒤 구간과 **장세**(상승/하락)가 섞여 있었다 — 뒷구간(약 2024-08~)
에는 2024 하반기 상승장이 들어 있고, 앞구간에는 2022 하락장이 들어 있다. 그래서 이 모듈은 같은
팔의 거래를 **진입 시각의 장세**로 나눈다. 🎯 진짜 값은 **하락장이 앞·뒤 구간에 하나씩** 있다는
것이다 — 2021-11~2022-11(앞)과 2025-10~(뒤)에서 둘 다 같은 방향으로 결정되면, 그것이 이 저장소
처음으로 「앞에서 고르고 뒤에서 확인된」 것이 된다(★).

## 장세 라벨은 월봉으로 못 박은 **사후 구분**이다 (사용자 결정 2026-09-27)

BTC 고점·저점 날짜(UTC)를 코드 상수 `PERIODS`로 고정했다 — 결과를 보고 옮기지 않는다(WAN-161).
경계는 그 날 00:00 UTC이고 반개구간 `[시작, 끝)`이며, 거래는 **진입 시각**으로 라벨을 받는다.
조정(`correction`) 구간이 상승장 안에 겹치면 **조정이 이긴다**(`label_period`). 판정은 `bull`과
`bear`로만 내고 조정은 **따로 보여만** 한다(결과를 보고 한쪽에 붙이지 않게).

🚨 **이 라벨은 매매에 못 쓴다** — 고점·저점은 지나고 나서야 안다. 답하는 질문은 「이 전략이 어떤
장세에서 버는가」다. 200일선 인과 라벨은 이 이슈 범위 밖이다(사용자 결정).

## 축을 새로 만들지 않는다

좌표·팔은 WAN-424/432/433 **그대로**다(31종목 × 9TF · 못 박은 6년 · 롱 · 첫 탭 · 재진입 없음 ·
시간 청산 · `pen_5bp` × 같은 분 익절 금지 · 채택 북 · 복리 끔 · 가드 끔 · **핀 없음**). 🚨 장세는
**다시 돌려서 자르지 않는다** — 한 번의 배치 → 거래별 진입 시각 → 라벨이고, 버킷을 다시 합치면
WAN-432 공개 CSV와 같아야 한다(§검산).

## 판정 상수 — 착수 전에 박았다 (`regime_verdict`)

1. **장세 무관** = `bull`·`bear` 둘 다 net R > 0 · 부호 결정 → 팔 유지(장세 게이트 불필요).
2. **하락장 편향** = `bear` gross R > 0 · 부호 결정 ＋ `bull` gross는 그렇지 않다(미결정 이하).
3. **상승장 편향** = 그 거울.
4. **판정 불가 → 팔을 닫는다** = 1~3이 아니고 두 장세의 net R이 모두 「양수 결정」이 아니다.
5. ★ **진짜** = 하락장 안에서 앞(`bear_1`)·뒤(`bear_2`) net R이 **둘 다 같은 방향으로 결정**.

부호 관문은 이 저장소의 그 자다(`|평균| > 2σ` · WAN-381/412 · WAN-432 `SIGN_SIGMA`).

## §E 몰림 — 거래는 독립된 증거가 아니다

%K가 25 아래로 내려가는 순간은 여러 종목에서 동시에 온다. 그래서 표시 조합의 거래를 같은 날·같은
4시간으로 묶어 세고, **날짜 단위 블록 부트스트랩**(`BLOCK = 1일` · 착수 전 고정)으로 정직한 2σ와
실효 표본 수를 다시 내고, 그 오차로 「페이퍼로 부호가 결정되기까지 몇 개월」을 다시 추정한다.
관측이다 — 판정 상수는 새로 만들지 않는다.

## §F 거래 내역 — `wan434_trades.csv.gz`

표시 조합 둘(ts4 · 하한 4% · %K<25 / %K<15)의 `full` 한 지갑 거래 전부. 누적 자본은
`wan424._equity_path`와 **같은 식·같은 순서(청산 시각)** 이고, 마지막 자본·최대 낙폭이 WAN-430
공개 CSV(`__no15m__` = 이 9TF 좌표)의 `fixed_return`·`fixed_mdd`와 같아야 한다(§검산 둘째).

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 핀 없음 WAN-305) · 이 팔은 **채택 좌표가 아니다**(채택 북
−0.12R과 나란히 놓지 말 것) · 전부 `pen_5bp` × 같은 분 익절 금지 위의 값 · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import datetime as dt
import math
import random
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.book_cli import net_r
from backtest.models import ExitReason
from backtest.wan169_leverage_book import CellPayload
from backtest.wan370_cost_decomposition import decompose_trade

# 🚨 의도적 재노출 — 이 이슈가 더하는 축은 「장세」 하나다(WAN-433 관행). 값을 베껴 쓰면 두 표의
# 자가 조용히 갈라진다(WAN-91/95/112/123/159).
from backtest.wan424_stoch_ob_arm import HOLD_BARS as HOLD_BARS
from backtest.wan424_stoch_ob_arm import SYMBOLS as SYMBOLS
from backtest.wan424_stoch_ob_arm import TIMEFRAMES as TIMEFRAMES
from backtest.wan424_stoch_ob_arm import (
    ArmCell,
    build_arm_cells,
    build_base_payloads,
    filtered_payloads,
    k_before,
    place_segments,
)
from backtest.wan432_stoch_floor_robustness import ADOPTED_FLOOR as ADOPTED_FLOOR
from backtest.wan432_stoch_floor_robustness import CHECKSUM_TOL as CHECKSUM_TOL
from backtest.wan432_stoch_floor_robustness import SIGN_SIGMA as SIGN_SIGMA
from backtest.wan433_stoch_floor_by_year import LOOSE_FLOOR as LOOSE_FLOOR
from backtest.wan433_stoch_floor_by_year import assert_adopted_accounting
from data.models import timeframe_to_ms

REPORT_DIR = Path(__file__).resolve().parent / "reports"
CSV_PATH = REPORT_DIR / "wan434_stoch_arm_by_regime.csv"
CLUSTER_CSV_PATH = REPORT_DIR / "wan434_clustering.csv"
TRADES_PATH = REPORT_DIR / "wan434_trades.csv.gz"
SUMMARY_PATH = REPORT_DIR / "wan434_stoch_arm_by_regime_summary.md"
DEFAULT_PAYLOAD_DIR = Path(__file__).resolve().parent / "cache" / "wan424_payloads"
WAN432_CSV = REPORT_DIR / "wan432_stoch_floor_robustness.csv"
WAN430_CSV = REPORT_DIR / "wan430_stoch_arm_15m.csv"
#: WAN-430 CSV에서 이 좌표(9TF · 15m 없음)에 해당하는 스코프.
WAN430_SCOPE = "__no15m__"

# ---------------------------------------------------------------------------
# 장세 — 월봉으로 못 박은 구간 (사용자 결정 2026-09-27 · 결과를 보고 옮기지 않는다)
# ---------------------------------------------------------------------------

BULL = "bull"
BEAR = "bear"
CORRECTION = "correction"
REGIMES: tuple[str, ...] = (BULL, BEAR, CORRECTION)


def _day_ms(y: int, m: int, d: int) -> int:
    return int(dt.datetime(y, m, d, tzinfo=dt.UTC).timestamp() * 1000)


@dataclass(frozen=True)
class Period:
    """월봉 장세 한 구간 — `[start_ms, end_ms)` · `end_ms=None`은 창 끝까지."""

    key: str
    regime: str
    start_ms: int
    end_ms: int | None
    note: str

    def contains(self, ms: int) -> bool:
        return self.start_ms <= ms and (self.end_ms is None or ms < self.end_ms)


#: 이슈 표 그대로(BTC 1d 고가·저가로 PM이 실측한 고점·저점 날짜).
PERIODS: tuple[Period, ...] = (
    Period("bull_1", BULL, _day_ms(2020, 9, 15), _day_ms(2021, 11, 10), "10k → 69.2k"),
    Period("correction_1", CORRECTION, _day_ms(2021, 4, 14), _day_ms(2021, 7, 20), "65.0k → 28.7k"),
    Period("bear_1", BEAR, _day_ms(2021, 11, 10), _day_ms(2022, 11, 21), "69.2k → 15.4k"),
    Period("bull_2", BULL, _day_ms(2022, 11, 21), _day_ms(2025, 10, 6), "15.4k → 126.2k"),
    Period("correction_2", CORRECTION, _day_ms(2024, 3, 14), _day_ms(2024, 8, 5), "73.9k → 48.9k"),
    Period("correction_3", CORRECTION, _day_ms(2025, 1, 20), _day_ms(2025, 4, 7), "110.0k → 74.5k"),
    Period("bear_2", BEAR, _day_ms(2025, 10, 6), None, "126.2k → 57.8k(창 끝)"),
)
#: ★의 두 하락장 — 앞구간 하락장과 뒷구간 하락장.
BEAR_FRONT = "bear_1"
BEAR_BACK = "bear_2"


def label_period(ms: int) -> Period:
    """진입 시각의 장세 구간. 🚨 조정이 상승장보다 **먼저** 잡힌다(조정은 상승장 안에 겹친다).

    어느 구간에도 안 들면 **죽는다** — 조용히 `bull`이나 `bear`로 채우면 라벨이 지어진다.
    """
    for p in PERIODS:
        if p.regime == CORRECTION and p.contains(ms):
            return p
    for p in PERIODS:
        if p.regime != CORRECTION and p.contains(ms):
            return p
    raise ValueError(f"장세 라벨이 없는 시각입니다: {ms} — PERIODS가 창을 덮지 않습니다.")


# ---------------------------------------------------------------------------
# 거래 기록 — 한 번의 배치에서 필요한 것을 다 뽑는다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeRec:
    """한 거래 — 장세 버킷·몰림·거래 내역 CSV가 **같은 기록**을 읽는다."""

    segment: str
    symbol: str
    timeframe: str
    entry_time: int
    exit_time: int
    entry_price: float
    stop_price: float
    exit_price: float
    exit_reason: str
    stop_width: float
    k_at_entry: float | None
    net_r: float
    gross_r: float
    cost_r: float
    residual_r: float
    win: bool
    period: str
    regime: str
    back_half: bool
    """칸의 따뜻한 OOS 경계(`payload.boundary_ms`) 이후 탭인가 — `oos_warm`과 같은 식."""


def collect_trades(
    payloads: Sequence[CellPayload], cells: Sequence[ArmCell]
) -> dict[str, list[TradeRec]]:
    """배치(`wan424.place_segments` 그대로) → 구간별 거래 기록. 🚨 북 인자는 여기 한 줄도 없다.

    그래서 위임이 채택 익절 회계(`take_profit_liquidity` = 메이커 · WAN-370/373)를 쓰는지를
    `assert_adopted_accounting`이 **값으로** 확인한다.
    """
    by_cell = {(c.symbol, c.timeframe): c for c in cells}
    boundary = {(p.symbol, p.timeframe): p.boundary_ms for p in payloads}
    out: dict[str, list[TradeRec]] = {}
    for seg in place_segments(payloads):
        cfg = seg.outcome.effective_config
        assert_adopted_accounting(cfg)
        recs: list[TradeRec] = []
        for trade, placement in seg.trades_with_placements():
            risk = placement.risk_amount
            if risk <= 0.0:
                continue
            symbol, timeframe = placement.cell
            parts = decompose_trade(trade, cfg)
            entry = float(trade.entry_price)
            cell = by_cell.get((symbol, timeframe))
            k_val = (
                k_before(
                    cell.k_times, cell.k_values, int(trade.entry_time), timeframe_to_ms(timeframe)
                )
                if cell is not None
                else None
            )
            period = label_period(int(trade.entry_time))
            reason = trade.exits[-1].reason
            recs.append(
                TradeRec(
                    segment=seg.segment,
                    symbol=symbol,
                    timeframe=timeframe,
                    entry_time=int(trade.entry_time),
                    exit_time=int(trade.exit_time),
                    entry_price=entry,
                    stop_price=float(placement.stop_price),
                    exit_price=float(trade.exits[-1].price),
                    exit_reason=_reason_label(reason),
                    stop_width=abs(entry - placement.stop_price) / entry if entry > 0 else math.nan,
                    k_at_entry=k_val,
                    net_r=net_r(trade, placement),
                    gross_r=parts.gross / risk,
                    cost_r=parts.total_cost / risk,
                    residual_r=abs(parts.residual) / risk,
                    win=trade.realized_pnl > 0,
                    period=period.key,
                    regime=period.regime,
                    back_half=int(placement.trigger_time) >= boundary[(symbol, timeframe)],
                )
            )
        out[seg.segment] = recs
    return out


def _reason_label(reason: ExitReason) -> str:
    if reason == ExitReason.STOP_LOSS:
        return "손절"
    if reason == ExitReason.END_OF_DATA:
        return "시간청산"
    return str(reason.value)


# ---------------------------------------------------------------------------
# 장세 버킷
# ---------------------------------------------------------------------------

LEVEL_REGIME = "regime"
LEVEL_PERIOD = "period"


@dataclass(frozen=True)
class RegimeRow:
    """(조합 × 구간 × 버킷) 한 칸 — 버킷은 장세(`level=regime`) 또는 구간(`level=period`)."""

    hold: int
    floor: float
    threshold: float | None
    segment: str
    level: str
    bucket: str
    num_trades: int
    win_rate: float
    mean_net_r: float
    se_net_r: float
    sum_net_r: float
    mean_gross_r: float
    se_gross_r: float
    mean_cost_r: float
    median_stop_width: float
    identity_max_abs: float

    @property
    def net_decided(self) -> bool:
        return _decided(self.mean_net_r, self.se_net_r)

    @property
    def gross_decided(self) -> bool:
        return _decided(self.mean_gross_r, self.se_gross_r)


def _stats(values: Sequence[float]) -> tuple[float, float]:
    n = len(values)
    if n == 0:
        return math.nan, math.nan
    mean = sum(values) / n
    if n < 2:
        return mean, math.nan
    return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1) / n)


def _decided(value: float, se: float) -> bool:
    if math.isnan(value) or math.isnan(se):
        return False
    return abs(value) > SIGN_SIGMA * se


def _decided_positive(value: float, se: float) -> bool:
    return value > 0 and _decided(value, se)


def _bucket_row(
    recs: Sequence[TradeRec],
    *,
    hold: int,
    floor: float,
    threshold: float | None,
    segment: str,
    level: str,
    bucket: str,
) -> RegimeRow:
    mean_net, se_net = _stats([r.net_r for r in recs])
    mean_gross, se_gross = _stats([r.gross_r for r in recs])
    widths = [r.stop_width for r in recs if not math.isnan(r.stop_width)]
    n = len(recs)
    return RegimeRow(
        hold=hold,
        floor=floor,
        threshold=threshold,
        segment=segment,
        level=level,
        bucket=bucket,
        num_trades=n,
        win_rate=sum(1 for r in recs if r.win) / n if n else math.nan,
        mean_net_r=mean_net,
        se_net_r=se_net,
        sum_net_r=sum(r.net_r for r in recs),
        mean_gross_r=mean_gross,
        se_gross_r=se_gross,
        mean_cost_r=sum(r.cost_r for r in recs) / n if n else math.nan,
        median_stop_width=statistics.median(widths) if widths else math.nan,
        identity_max_abs=max((r.residual_r for r in recs), default=math.nan),
    )


def bucket_rows(
    trades: dict[str, list[TradeRec]], *, hold: int, floor: float, threshold: float | None
) -> list[RegimeRow]:
    """구간마다 장세 3칸 ＋ 월봉 구간 7칸. 거래가 없는 버킷은 **0행으로 내지 않는다**(WAN-367)."""
    rows: list[RegimeRow] = []
    for segment, recs in trades.items():
        for level in (LEVEL_REGIME, LEVEL_PERIOD):
            groups: dict[str, list[TradeRec]] = {}
            for r in recs:
                groups.setdefault(r.regime if level == LEVEL_REGIME else r.period, []).append(r)
            for bucket in sorted(groups):
                rows.append(
                    _bucket_row(
                        groups[bucket],
                        hold=hold,
                        floor=floor,
                        threshold=threshold,
                        segment=segment,
                        level=level,
                        bucket=bucket,
                    )
                )
    return rows


#: 격자 — 하한은 이슈의 대조 쌍(느슨 1% / 조임 4%), %K는 끔 ＋ WAN-424 문턱 셋, 보유는 전부.
GRID_FLOORS: tuple[float, ...] = (LOOSE_FLOOR, ADOPTED_FLOOR)
GRID_THRESHOLDS: tuple[float | None, ...] = (None, 15.0, 20.0, 25.0)
#: 판정 필터 넷(이슈 격자 「%K 끔/켬 × 느슨 1% / 조임 4%」) — %K 켬은 표시 문턱 25다.
DISPLAY_HOLD = 4
DISPLAY_THRESHOLD = 25.0
VERDICT_FILTERS: tuple[tuple[float, float | None], ...] = (
    (LOOSE_FLOOR, None),
    (LOOSE_FLOOR, DISPLAY_THRESHOLD),
    (ADOPTED_FLOOR, None),
    (ADOPTED_FLOOR, DISPLAY_THRESHOLD),
)
#: §F 거래 내역에 싣는 조합 — (보유, 하한, 문턱).
TRADE_EXPORT_COMBOS: tuple[tuple[int, float, float], ...] = (
    (DISPLAY_HOLD, ADOPTED_FLOOR, 25.0),
    (DISPLAY_HOLD, ADOPTED_FLOOR, 15.0),
)
#: §E 몰림을 재는 조합.
CLUSTER_COMBO: tuple[int, float, float] = (DISPLAY_HOLD, ADOPTED_FLOOR, DISPLAY_THRESHOLD)


# ---------------------------------------------------------------------------
# 판정 — 사람이 표를 보고 정하지 않는다
# ---------------------------------------------------------------------------

AGNOSTIC = "장세 무관"
BEAR_BIAS = "하락장 편향"
BULL_BIAS = "상승장 편향"
CLOSE_ARM = "판정 불가 — 팔을 닫는다"
OTHER = "분류 밖"


def regime_verdict(bull: RegimeRow | None, bear: RegimeRow | None) -> str:
    """착수 전에 박은 상수로 갈래를 낸다(모듈 독스트링 1~4). 순서가 곧 우선순위다."""
    if bull is None or bear is None:
        return CLOSE_ARM
    bull_net = _decided_positive(bull.mean_net_r, bull.se_net_r)
    bear_net = _decided_positive(bear.mean_net_r, bear.se_net_r)
    if bull_net and bear_net:
        return AGNOSTIC
    bull_gross = _decided_positive(bull.mean_gross_r, bull.se_gross_r)
    bear_gross = _decided_positive(bear.mean_gross_r, bear.se_gross_r)
    if bear_gross and not bull_gross:
        return BEAR_BIAS
    if bull_gross and not bear_gross:
        return BULL_BIAS
    if not bull_net and not bear_net:
        return CLOSE_ARM
    return OTHER


def star_verdict(front: RegimeRow | None, back: RegimeRow | None) -> tuple[bool, str]:
    """★ — 하락장 앞(`bear_1`)·뒤(`bear_2`) net R이 **둘 다 같은 방향으로 결정**됐는가."""
    if front is None or back is None:
        return False, "⚠️ 한쪽 하락장에 거래가 없다 — ★ 판정 불가."
    f_dec, b_dec = front.net_decided, back.net_decided
    if f_dec and b_dec and (front.mean_net_r > 0) == (back.mean_net_r > 0):
        if front.mean_net_r > 0:
            return True, "★ 성립 — 두 하락장 모두 **플러스**로 결정됐다."
        return True, (
            "두 하락장 모두 **마이너스**로 결정 — 같은 방향이지만 이 필터는 하락장에서 "
            "**진다**(채택 자격 아님)."
        )
    parts = []
    for name, row, dec in (("앞 하락장", front, f_dec), ("뒤 하락장", back, b_dec)):
        parts.append(f"{name} {row.mean_net_r:+.3f}R {'결정' if dec else '미결정'}")
    return False, "★ 불성립 — " + " · ".join(parts) + "."


def _index(rows: Sequence[RegimeRow]) -> dict[tuple[int, float, float | None, str, str], RegimeRow]:
    return {(r.hold, round(r.floor, 6), r.threshold, r.segment, r.bucket): r for r in rows}


def verdict_table(rows: Sequence[RegimeRow]) -> list[tuple[float, float | None, str, bool, str]]:
    """판정 필터마다 (하한, 문턱, 갈래, ★, ★ 문장) — `full` 한 지갑 · ts4."""
    idx = _index(rows)
    out = []
    for floor, thr in VERDICT_FILTERS:
        key = (DISPLAY_HOLD, round(floor, 6), thr, harness.SEGMENT_FULL)
        bull = idx.get((*key, BULL))
        bear = idx.get((*key, BEAR))
        star, star_line = star_verdict(idx.get((*key, BEAR_FRONT)), idx.get((*key, BEAR_BACK)))
        out.append((floor, thr, regime_verdict(bull, bear), star, star_line))
    return out


# ---------------------------------------------------------------------------
# §E 몰림 — 관측
# ---------------------------------------------------------------------------

DAY_MS = 86_400_000
FOUR_HOURS_MS = 4 * 3_600_000
#: 블록 부트스트랩의 블록 = UTC 1일. **착수 전 고정** — 결과를 보고 바꾸지 않는다.
BLOCK_MS = DAY_MS
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 434
MONTH_MS = 30.4375 * DAY_MS


@dataclass(frozen=True)
class ClusterRow:
    segment: str
    num_trades: int
    days: int
    """거래가 있는 UTC 날짜 수(= 1일 묶음 수)."""
    trades_per_day_mean: float
    trades_per_day_max: int
    share_in_multi_day: float
    """같은 날 다른 거래와 함께 진입한 거래의 비율."""
    blocks_4h: int
    trades_per_4h_max: int
    share_in_multi_4h: float
    concurrent_max: int
    concurrent_p99: float
    mean_net_r: float
    se_iid: float
    se_block: float
    effective_n: float
    day_unit_mean: float
    """묶음(날)을 한 단위로 센 거래당 net R — 날마다 평균을 낸 뒤 그 평균들의 평균."""
    day_unit_se: float
    months: float
    trades_per_month: float
    months_to_decide_iid: float
    months_to_decide_block: float


def _groups(recs: Sequence[TradeRec], width_ms: int) -> dict[int, list[TradeRec]]:
    out: dict[int, list[TradeRec]] = {}
    for r in recs:
        out.setdefault(r.entry_time // width_ms, []).append(r)
    return out


def concurrent_counts(recs: Sequence[TradeRec]) -> list[int]:
    """진입 순간 열려 있는 포지션 수(자기 포함). 청산 == 진입이면 닫힌 것으로 본다(엔진 규약)."""
    exits = sorted(r.exit_time for r in recs)
    entries = sorted(r.entry_time for r in recs)
    out = []
    for t in (r.entry_time for r in recs):
        opened = bisect.bisect_right(entries, t)
        closed = bisect.bisect_right(exits, t)
        out.append(max(opened - closed, 1))
    return out


def block_bootstrap_se(
    recs: Sequence[TradeRec],
    *,
    block_ms: int = BLOCK_MS,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> float:
    """날짜 블록을 복원 추출해 거래당 net R 평균의 표준편차를 낸다(비율 추정량)."""
    blocks = list(_groups(recs, block_ms).values())
    if len(blocks) < 2:
        return math.nan
    sums = [sum(r.net_r for r in b) for b in blocks]
    counts = [len(b) for b in blocks]
    rng = random.Random(seed)
    k = len(blocks)
    means = []
    for _ in range(draws):
        s = 0.0
        n = 0
        for _i in range(k):
            j = rng.randrange(k)
            s += sums[j]
            n += counts[j]
        means.append(s / n)
    return statistics.stdev(means)


def cluster_row(segment: str, recs: Sequence[TradeRec]) -> ClusterRow:
    n = len(recs)
    days = _groups(recs, DAY_MS)
    fours = _groups(recs, FOUR_HOURS_MS)
    conc = sorted(concurrent_counts(recs))
    mean, se_iid = _stats([r.net_r for r in recs])
    se_block = block_bootstrap_se(recs)
    eff = n * (se_iid / se_block) ** 2 if se_block and not math.isnan(se_block) else math.nan
    day_means = [sum(r.net_r for r in g) / len(g) for g in days.values()]
    dmean, dse = _stats(day_means)
    span = (max(r.entry_time for r in recs) - min(r.entry_time for r in recs)) if n else 0
    months = span / MONTH_MS if span else math.nan
    rate = n / months if months and not math.isnan(months) else math.nan
    sd = se_iid * math.sqrt(n) if n > 1 else math.nan
    deff = (se_block / se_iid) ** 2 if se_iid and not math.isnan(se_block) else math.nan
    need_iid = (SIGN_SIGMA * sd / mean) ** 2 if mean > 0 else math.nan
    return ClusterRow(
        segment=segment,
        num_trades=n,
        days=len(days),
        trades_per_day_mean=n / len(days) if days else math.nan,
        trades_per_day_max=max((len(g) for g in days.values()), default=0),
        share_in_multi_day=sum(len(g) for g in days.values() if len(g) > 1) / n if n else math.nan,
        blocks_4h=len(fours),
        trades_per_4h_max=max((len(g) for g in fours.values()), default=0),
        share_in_multi_4h=sum(len(g) for g in fours.values() if len(g) > 1) / n if n else math.nan,
        concurrent_max=conc[-1] if conc else 0,
        concurrent_p99=float(pd.Series(conc).quantile(0.99)) if conc else math.nan,
        mean_net_r=mean,
        se_iid=se_iid,
        se_block=se_block,
        effective_n=eff,
        day_unit_mean=dmean,
        day_unit_se=dse,
        months=months,
        trades_per_month=rate,
        months_to_decide_iid=need_iid / rate if rate else math.nan,
        months_to_decide_block=need_iid * deff / rate if rate else math.nan,
    )


# ---------------------------------------------------------------------------
# §F 거래 내역 + 자본 경로
# ---------------------------------------------------------------------------


def equity_path(recs: Sequence[TradeRec]) -> list[tuple[TradeRec, float, float]]:
    """`wan424._equity_path(compound=False)`와 같은 식·같은 순서(청산 시각 · 안정 정렬).

    🚨 순서를 바꾸면 MDD가 달라진다 — `place`가 `trades_with_placements` 순서로 쌓은 뒤
    청산 시각으로 **안정 정렬**하므로 같은 청산 시각은 원래 순서를 지킨다.
    """
    ordered = sorted(recs, key=lambda r: r.exit_time)
    eq = peak = 1.0
    out = []
    for r in ordered:
        eq += 0.01 * r.net_r
        peak = max(peak, eq)
        out.append((r, eq, 1.0 - eq / peak))
    return out


@dataclass(frozen=True)
class DrawdownSpan:
    peak_time: int
    trough_time: int
    mdd: float
    trades: int
    symbols: int


def max_drawdown_span(path: Sequence[tuple[TradeRec, float, float]]) -> DrawdownSpan | None:
    """MDD가 찍힌 구간 — 고점(직전 최고 자본의 청산 시각) → 바닥(최대 낙폭의 청산 시각)."""
    if not path:
        return None
    trough = max(range(len(path)), key=lambda i: path[i][2])
    if path[trough][2] <= 0:
        return None
    peak_eq = max(eq for _r, eq, _d in path[: trough + 1])
    peak = max(i for i in range(trough + 1) if path[i][1] == peak_eq)
    between = [path[i][0] for i in range(peak + 1, trough + 1)]
    return DrawdownSpan(
        peak_time=path[peak][0].exit_time,
        trough_time=path[trough][0].exit_time,
        mdd=path[trough][2],
        trades=len(between),
        symbols=len({r.symbol for r in between}),
    )


def _combo_label(hold: int, floor: float, thr: float) -> str:
    return f"ts{hold}·하한{floor:.0%}·%K<{thr:.0f}"


def _utc(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).strftime("%Y-%m-%d %H:%M")


def trades_frame(exports: dict[tuple[int, float, float], list[TradeRec]]) -> pd.DataFrame:
    rows = []
    for (hold, floor, thr), recs in exports.items():
        label = _combo_label(hold, floor, thr)
        for r, eq, dd in equity_path(recs):
            rows.append(
                {
                    "조합": label,
                    "종목": r.symbol,
                    "시간봉": r.timeframe,
                    "진입시각(UTC)": _utc(r.entry_time),
                    "청산시각(UTC)": _utc(r.exit_time),
                    "진입시각_ms": r.entry_time,
                    "청산시각_ms": r.exit_time,
                    "진입가": r.entry_price,
                    "손절가": r.stop_price,
                    "청산가": r.exit_price,
                    "청산사유": r.exit_reason,
                    "손절폭(%)": r.stop_width * 100,
                    "진입시%K": r.k_at_entry,
                    "gross_R": r.gross_r,
                    "비용_R": r.cost_r,
                    "net_R": r.net_r,
                    "장세": r.regime,
                    "장세구간": r.period,
                    "앞뒤": "뒤" if r.back_half else "앞",
                    "누적자본(고정1%)": eq,
                    "고점대비낙폭": dd,
                }
            )
    return pd.DataFrame(rows)


def equity_checksum(
    trades: pd.DataFrame, *, reference_csv: Path = WAN430_CSV
) -> tuple[list[str], float]:
    """파일의 마지막 자본·최대 낙폭 ↔ WAN-430 공개 CSV(`__no15m__`)의 `fixed_return`·`fixed_mdd`.

    거래 내역 **파일에 적힌 열**로 잰다 — 그래야 파일 자체가 검산된다(메모리 원값이 아니라).
    """
    if not reference_csv.exists():
        return [f"⚠️ 기준 CSV 없음: {reference_csv}"], math.nan
    ref = pd.read_csv(reference_csv)
    lines = []
    worst = 0.0
    for hold, floor, thr in TRADE_EXPORT_COMBOS:
        label = _combo_label(hold, floor, thr)
        g = trades[trades["조합"] == label] if not trades.empty else trades
        want = ref[
            (ref["scope"] == WAN430_SCOPE)
            & (ref["hold"] == hold)
            & ((ref["floor"] - floor).abs() < 1e-9)
            & (ref["threshold"] == thr)
            & (ref["segment"] == harness.SEGMENT_FULL)
        ]
        if want.empty or g.empty:
            lines.append(f"❌ 기준 행 또는 거래 없음: {label}")
            worst = math.inf
            continue
        w = want.iloc[0]
        got_ret = float(g["누적자본(고정1%)"].iloc[-1]) - 1.0
        got_mdd = float(g["고점대비낙폭"].max())
        diff = max(abs(got_ret - float(w["fixed_return"])), abs(got_mdd - float(w["fixed_mdd"])))
        n_ok = len(g) == int(w["num_trades"])
        worst = max(worst, diff if n_ok else math.inf)
        mark = "✅" if n_ok and diff < CHECKSUM_TOL else "❌"
        lines.append(
            f"{mark} {label} full ↔ WAN-430 `{WAN430_SCOPE}` — 거래 {len(g)}(기준 "
            f"{int(w['num_trades'])}) · 마지막 자본 {got_ret:+.4f} · MDD {got_mdd:.2%} · "
            f"최대 절대차 {diff:.2e}"
        )
    return lines, worst


# ---------------------------------------------------------------------------
# 검산 — 장세 버킷을 다시 합치면 WAN-432 공개 CSV인가 (완료기준 3)
# ---------------------------------------------------------------------------


def checksum_wan432(
    rows: Sequence[RegimeRow], *, reference_csv: Path = WAN432_CSV, adopted: bool = True
) -> tuple[list[str], float]:
    """장세 3칸의 합 ↔ WAN-432 행. 거래 수는 정확히, 평균은 `CHECKSUM_TOL` 안.

    %K 끔 행은 WAN-432에 없어 대조하지 않고 그 사실을 적는다(지어내지 않는다).
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
    pooled: dict[tuple[int, float, float | None, str], dict[str, float]] = {}
    for r in rows:
        if r.level != LEVEL_REGIME:
            continue
        acc = pooled.setdefault(
            (r.hold, round(r.floor, 6), r.threshold, r.segment),
            {"n": 0.0, "net": 0.0, "wins": 0.0, "gross": 0.0, "cost": 0.0},
        )
        acc["n"] += r.num_trades
        acc["net"] += r.sum_net_r
        acc["wins"] += r.win_rate * r.num_trades
        acc["gross"] += r.mean_gross_r * r.num_trades
        acc["cost"] += r.mean_cost_r * r.num_trades
    lines: list[str] = []
    worst = 0.0
    compared = skipped = mismatch = 0
    for (hold, floor, thr, segment), acc in sorted(
        pooled.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0.0, kv[0][3])
    ):
        if thr is None:
            skipped += 1
            continue
        want = index.get((hold, floor, float(thr), segment))
        if want is None:
            lines.append(f"❌ 기준 행 없음: ts{hold} 하한{floor:.1%} K<{thr:.0f} {segment}")
            mismatch += 1
            continue
        compared += 1
        n = acc["n"]
        if int(n) != int(want["num_trades"]):
            mismatch += 1
            lines.append(
                f"❌ 거래 수 불일치: ts{hold} 하한{floor:.1%} K<{thr:.0f} {segment} — "
                f"{int(n)} vs {int(want['num_trades'])}"
            )
            continue
        for got, col in (
            (acc["net"] / n, "mean_net_r"),
            (acc["wins"] / n, "win_rate"),
            (acc["gross"] / n, "gross_r"),
            (acc["cost"] / n, "cost_r"),
        ):
            worst = max(worst, abs(got - float(want[col])))
    if compared == 0:
        lines.append("❌ 대조한 행이 하나도 없다.")
        return lines, math.inf
    mark = "✅" if mismatch == 0 and worst < CHECKSUM_TOL else "❌"
    lines.append(
        f"{mark} 장세 버킷 합 × {compared}행 × 4열(＋거래 수 정수 일치) — 최대 절대차 {worst:.2e}"
        f" · %K 끔 {skipped}행은 WAN-432에 없어 대조 안 함"
    )
    return lines, (math.inf if mismatch else worst)


def checksum_passes(worst: float) -> bool:
    """보고 마크와 종료 코드가 같은 자를 쓰게 하는 한 곳(WAN-432 규약)."""
    return math.isnan(worst) or worst < CHECKSUM_TOL


def on_adopted_coordinates(*, symbols: Sequence[str], timeframes: Sequence[str]) -> bool:
    return tuple(symbols) == SYMBOLS and tuple(timeframes) == TIMEFRAMES


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    rows: list[RegimeRow]
    clusters: list[ClusterRow]
    exports: dict[tuple[int, float, float], list[TradeRec]]


def run_grid(
    payloads: Sequence[CellPayload],
    cells: Sequence[ArmCell],
    *,
    holds: Sequence[int] = HOLD_BARS,
    progress: bool = False,
) -> RunResult:
    rows: list[RegimeRow] = []
    clusters: list[ClusterRow] = []
    exports: dict[tuple[int, float, float], list[TradeRec]] = {}
    combos = [(h, f, t) for f in GRID_FLOORS for t in GRID_THRESHOLDS for h in holds]
    for done, (hold, floor, thr) in enumerate(combos, start=1):
        trades = collect_trades(
            filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=thr), cells
        )
        rows.extend(bucket_rows(trades, hold=hold, floor=floor, threshold=thr))
        if thr is not None and (hold, floor, thr) in TRADE_EXPORT_COMBOS:
            exports[(hold, floor, thr)] = trades[harness.SEGMENT_FULL]
        if (hold, floor, thr) == CLUSTER_COMBO:
            clusters = [cluster_row(seg, recs) for seg, recs in trades.items()]
        if progress:
            k = "끔" if thr is None else f"<{thr:.0f}"
            print(f"  배치 {done}/{len(combos)} (하한{floor:.1%} K{k} ts{hold})", flush=True)
    return RunResult(rows, clusters, exports)


def rows_to_frame(rows: Sequence[RegimeRow]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(r) for r in rows])


def frame_to_rows(frame: pd.DataFrame) -> list[RegimeRow]:
    fields = {f.name for f in dataclasses.fields(RegimeRow)}
    out = []
    for rec in frame.to_dict("records"):
        rec = {k: v for k, v in rec.items() if k in fields}
        thr = rec["threshold"]
        rec["threshold"] = (
            None if thr is None or (isinstance(thr, float) and math.isnan(thr)) else float(thr)
        )
        rec["hold"] = int(rec["hold"])
        rec["num_trades"] = int(rec["num_trades"])
        out.append(RegimeRow(**rec))
    return out


def clusters_to_frame(rows: Sequence[ClusterRow]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(r) for r in rows])


def frame_to_clusters(frame: pd.DataFrame) -> list[ClusterRow]:
    out = []
    for rec in frame.to_dict("records"):
        for key in ("num_trades", "days", "trades_per_day_max", "blocks_4h"):
            rec[key] = int(rec[key])
        rec["trades_per_4h_max"] = int(rec["trades_per_4h_max"])
        rec["concurrent_max"] = int(rec["concurrent_max"])
        out.append(ClusterRow(**rec))
    return out


def exports_from_frame(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {str(k): g for k, g in frame.groupby("조합", sort=False)}


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def _pm(se: float) -> str:
    return "±—" if math.isnan(se) else f"±{2 * se:.3f}"


def _mark(value: float, se: float) -> str:
    if not _decided(value, se):
        return ""
    return "＋" if value > 0 else "−"


def _pct(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:.2%}"


def _filter_label(floor: float, thr: float | None) -> str:
    k = "%K 끔" if thr is None else f"%K<{thr:.0f}"
    return f"하한 {floor:.0%} · {k}"


def _row_line(label: str, r: RegimeRow | None) -> str:
    if r is None:
        return f"| {label} | 0 | — | — | — | — | — |"
    return (
        f"| {label} | {r.num_trades} | {r.win_rate:.1%} "
        f"| {r.mean_net_r:+.3f}{_mark(r.mean_net_r, r.se_net_r)} {_pm(r.se_net_r)} "
        f"| {r.mean_gross_r:+.3f}{_mark(r.mean_gross_r, r.se_gross_r)} {_pm(r.se_gross_r)} "
        f"| {r.mean_cost_r:.3f} | {_pct(r.median_stop_width)} |"
    )


def render_summary(
    rows: Sequence[RegimeRow],
    clusters: Sequence[ClusterRow],
    *,
    trades: pd.DataFrame | None = None,
    equity_lines: Sequence[str] = (),
    elapsed: float | None = None,
    adopted: bool = True,
) -> str:
    idx = _index(rows)
    verdicts = verdict_table(rows)
    headline = next(v for v in verdicts if v[0] == ADOPTED_FLOOR and v[1] == DISPLAY_THRESHOLD)
    check_lines, _ = checksum_wan432(rows, adopted=adopted)
    identity = max(
        (r.identity_max_abs for r in rows if not math.isnan(r.identity_max_abs)),
        default=math.nan,
    )
    lines = [
        "# WAN-434 — 스토캐스틱 팔이 「하락장 전략」인가 (월봉 장세 × 필터)",
        "",
        "롱 · 31종목 × 9TF(1h~1w) · 못 박은 6년 · 첫 탭 · 재진입 없음 · 시간 청산 · `pen_5bp` × "
        "같은 분 익절 금지 · 채택 북 · 복리 끔 · 가드 끔(손절폭 하한이 대신) · **핀 없음**. 장세는 "
        "거래의 **진입 시각(UTC)** 으로 붙인 **사후 라벨**이다(다시 돌려 자르지 않았다).",
        "",
        f"## 판정 — 표시 조합 ts{DISPLAY_HOLD} · 하한 {ADOPTED_FLOOR:.0%} · "
        f"%K<{DISPLAY_THRESHOLD:.0f}: **{headline[2]}** · {headline[4]}",
        "",
        "🚨 **이 라벨은 매매에 못 쓴다** — 고점·저점은 지나고 나서야 안다. 답하는 질문은 「이 "
        "전략이 어떤 장세에서 버는가」다. 🚨 어느 갈래도 채택 근거가 아니다(WAN-161).",
        "",
        "| 필터(ts4 · `full`) | 갈래 | ★ 두 하락장 |",
        "| -- | -- | -- |",
    ]
    for floor, thr, verdict, _star, star_line in verdicts:
        lines.append(f"| {_filter_label(floor, thr)} | {verdict} | {star_line} |")
    lines += [
        "",
        "상수(착수 전 고정): 장세 무관 = `bull`·`bear` 둘 다 net R 양수·결정 · 하락장 편향 = "
        "`bear` gross 양수·결정 ＋ `bull` gross는 아님 · 상승장 편향 = 거울 · 판정 불가 = 둘 다 "
        "net이 양수·결정이 아님 · ★ = 두 하락장 net R이 같은 방향으로 결정. 결정 = `|평균| > 2σ`.",
        "",
        "## 월봉 장세 구간 (코드 상수 `PERIODS`)",
        "",
        "| 구간 | 장세 | 기간(UTC) | BTC |",
        "| -- | -- | -- | -- |",
    ]
    for p in PERIODS:
        end = "창 끝" if p.end_ms is None else _utc(p.end_ms)[:10]
        lines.append(f"| {p.key} | {p.regime} | {_utc(p.start_ms)[:10]} ~ {end} | {p.note} |")
    lines += [
        "",
        "조정은 상승장 안에 겹치고 **조정이 이긴다** · 경계는 그날 00:00 UTC · 반개구간.",
        "",
        "## 장세 × 필터 × 구간 (ts4 · 완료기준 1)",
        "",
    ]
    for floor, thr in VERDICT_FILTERS:
        lines += [
            f"### {_filter_label(floor, thr)}",
            "",
            "| 구간 · 장세 | 거래 | 승률 | 거래당 net R ± 2σ | gross R ± 2σ | 비용 R "
            "| 손절폭 중앙 |",
            "| -- | --: | --: | --: | --: | --: | --: |",
        ]
        for seg in (harness.SEGMENT_FULL, harness.SEGMENT_IS, harness.SEGMENT_OOS_WARM):
            for regime in REGIMES:
                lines.append(
                    _row_line(
                        f"{seg} · {regime}",
                        idx.get((DISPLAY_HOLD, round(floor, 6), thr, seg, regime)),
                    )
                )
        lines.append("")
    lines += [
        "⚠️ `is`·`oos_warm`은 **다른 지갑**이다(칸마다 다른 2/3 경계) — 월봉 구간과 일치하지 "
        "않으므로 두 하락장 대조는 아래 `full` 구간 표로 읽는다.",
        "",
        "## ★ 하락장 안에서 앞 · 뒤 (`full` 한 지갑 · 월봉 구간별)",
        "",
    ]
    for floor, thr in VERDICT_FILTERS:
        lines += [
            f"### {_filter_label(floor, thr)}",
            "",
            "| 월봉 구간 | 거래 | 승률 | 거래당 net R ± 2σ | gross R ± 2σ | 비용 R | 손절폭 중앙 |",
            "| -- | --: | --: | --: | --: | --: | --: |",
        ]
        for p in PERIODS:
            lines.append(
                _row_line(
                    p.key,
                    idx.get((DISPLAY_HOLD, round(floor, 6), thr, harness.SEGMENT_FULL, p.key)),
                )
            )
        lines.append("")
    lines += [
        "⚠️ 하락장 표본은 사실상 **둘**(2021-11~2022-11 · 2025-10~)이고 창이 2020-09부터라 "
        "**2020-03급 폭락이 없다** — 결론에 그 한계가 그대로 남는다.",
        "",
    ]
    lines += _cluster_section(clusters)
    lines += _trades_section(trades, equity_lines)
    lines += [
        "## 검산 — 장세 버킷의 합 ≡ WAN-432 공개 CSV (완료기준 3)",
        "",
        *[f"- {line}" for line in check_lines],
        "",
        f"비용 분해 항등식(`gross − 비용 − net`)의 최대 절댓값: {identity:.2e} R.",
        "",
        "⚠️ **표에서 최선 칸을 고르지 말 것**(WAN-161) · 전부 `pen_5bp` 위 값이고 **채택 좌표가 "
        "아니다**(채택 북 −0.12R과 나란히 놓지 말 것) · **「엣지 없음」(WAN-84/88/111/114/124/151/"
        "201/248/386) 불변**(이 표는 *그 팔이 어느 장세에서 버는가*를 묻는다 — 다른 질문) · "
        "장세 게이트를 채택 규칙으로 올리는 것은 **재-베이스라인 = 사용자 결정**.",
    ]
    if elapsed is not None:
        lines.append(f"\n실측 {elapsed:.0f}초({elapsed / 60:.0f}분).")
    return "\n".join(lines) + "\n"


def _cluster_section(clusters: Sequence[ClusterRow]) -> list[str]:
    hold, floor, thr = CLUSTER_COMBO
    lines = [
        f"## §E 몰림 — ts{hold} · 하한 {floor:.0%} · %K<{thr:.0f} (관측)",
        "",
        "| 구간 | 거래 | 거래 있는 날 | 날당 평균/최대 | 같은 날 묶인 비율 | 4시간 묶음 · 최대 "
        "| 같은 4시간 비율 | 동시 보유 최대 · 상위 1% |",
        "| -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for c in clusters:
        lines.append(
            f"| {c.segment} | {c.num_trades} | {c.days} | {c.trades_per_day_mean:.2f} / "
            f"{c.trades_per_day_max} | {c.share_in_multi_day:.1%} | {c.blocks_4h} · "
            f"{c.trades_per_4h_max} | {c.share_in_multi_4h:.1%} | {c.concurrent_max} · "
            f"{c.concurrent_p99:.0f} |"
        )
    lines += [
        "",
        f"### 정직한 오차 — 날짜 블록 부트스트랩(블록 1일 · {BOOTSTRAP_DRAWS}회 · 시드 "
        f"{BOOTSTRAP_SEED})",
        "",
        "| 구간 | 거래당 net R | 독립 가정 2σ | 블록 2σ | 실효 표본 | 날 단위 평균 ± 2σ |",
        "| -- | --: | --: | --: | --: | --: |",
    ]
    for c in clusters:
        lines.append(
            f"| {c.segment} | {c.mean_net_r:+.3f} | ±{2 * c.se_iid:.3f} | ±{2 * c.se_block:.3f} "
            f"| {c.effective_n:.0f} / {c.num_trades} | {c.day_unit_mean:+.3f} "
            f"{_pm(c.day_unit_se)} |"
        )
    oos = next((c for c in clusters if c.segment == harness.SEGMENT_OOS_WARM), None)
    if oos is not None and oos.mean_net_r > 0:
        lines += [
            "",
            f"📌 **페이퍼 확인 기간 재추정** — 뒷구간 성적(거래당 {oos.mean_net_r:+.3f}R · 월 "
            f"{oos.trades_per_month:.1f}거래)이 그대로 나온다고 할 때 부호가 2σ로 결정되기까지: "
            f"독립 가정 **{oos.months_to_decide_iid:.1f}개월** → 몰림 반영 "
            f"**{oos.months_to_decide_block:.1f}개월**. ⚠️ 뒷구간 평균이 그대로 나온다는 가정 "
            "위의 값이다(그 자체가 낙관).",
        ]
    elif oos is not None:
        lines += [
            "",
            f"📌 **페이퍼 확인 기간** — 뒷구간 거래당 net R이 {oos.mean_net_r:+.3f}R(0 이하)이라 "
            "「양의 부호가 결정되기까지」를 추정할 수 없다(지어내지 않는다).",
        ]
    lines += [
        "",
        "⚠️ 날 단위 평균은 **묶음을 한 단위로** 센 값이다(완료기준 §E-4) — 부호가 거래 단위와 "
        "같은지만 읽는다.",
        "",
    ]
    return lines


def _trades_section(trades: pd.DataFrame | None, equity_lines: Sequence[str]) -> list[str]:
    lines = [
        f"## §F 거래 내역 — `{TRADES_PATH.name}`",
        "",
        "표시 조합 둘의 `full` 한 지갑 거래 전부 · 누적 자본은 거래당 1% 고정 리스크(복리 끔)를 "
        "청산 시각 순으로 쌓은 값이다(`wan424._equity_path`와 같은 식).",
        "",
        *[f"- {line}" for line in equity_lines],
        "",
    ]
    if trades is None or trades.empty:
        return lines
    for label, g in exports_from_frame(trades).items():
        recs = g.sort_values("청산시각_ms", kind="stable")
        dd = recs["고점대비낙폭"].to_numpy()
        eq = recs["누적자본(고정1%)"].to_numpy()
        if len(dd) == 0 or dd.max() <= 0:
            continue
        trough = int(dd.argmax())
        peak_eq = eq[: trough + 1].max()
        peak = max(i for i in range(trough + 1) if eq[i] == peak_eq)
        between = recs.iloc[peak + 1 : trough + 1]
        lines.append(
            f"- 📌 **{label} MDD {dd[trough]:.1%}** — 고점 {recs.iloc[peak]['청산시각(UTC)']} → "
            f"바닥 {recs.iloc[trough]['청산시각(UTC)']} (UTC) · 그 사이 거래 {len(between)}건 · "
            f"종목 {between['종목'].nunique()}개 · 바닥까지 장세 구간 "
            f"{', '.join(sorted(between['장세구간'].unique()))}."
        )
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--from-csv", action="store_true", help="CSV에서 요약만 다시 만든다.")
    parser.add_argument("--elapsed", type=float, default=None)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--cluster-csv", type=Path, default=CLUSTER_CSV_PATH)
    parser.add_argument("--trades", type=Path, default=TRADES_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--symbols", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--timeframes", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--holds", default=None, help="파일럿용 좁히기(콤마).")
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = frame_to_rows(pd.read_csv(args.csv))
        clusters = frame_to_clusters(pd.read_csv(args.cluster_csv))
        trades = pd.read_csv(args.trades) if args.trades.exists() else None
        equity_lines = equity_checksum(trades)[0] if trades is not None else []
        summary = render_summary(
            rows, clusters, trades=trades, equity_lines=equity_lines, elapsed=args.elapsed
        )
        args.summary.write_text(summary, encoding="utf-8")
        print(summary)
        return 0

    started = time.monotonic()
    symbols = tuple(args.symbols.split(",")) if args.symbols else SYMBOLS
    timeframes = tuple(args.timeframes.split(",")) if args.timeframes else TIMEFRAMES
    holds = tuple(int(h) for h in args.holds.split(",")) if args.holds else HOLD_BARS
    payloads = build_base_payloads(
        jobs=args.jobs, payload_dir=args.payload_dir, symbols=symbols, timeframes=timeframes
    )
    print(f"base 후보 {time.monotonic() - started:.0f}s · 칸 {len(payloads)}", flush=True)
    cells = build_arm_cells(payloads, jobs=args.jobs, min_width=min(GRID_FLOORS), progress=True)
    print(f"팔 후보 {time.monotonic() - started:.0f}s", flush=True)
    result = run_grid(payloads, cells, holds=holds, progress=True)
    elapsed = time.monotonic() - started
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(result.rows).to_csv(args.csv, index=False)
    clusters_to_frame(result.clusters).to_csv(args.cluster_csv, index=False)
    trades = trades_frame(result.exports)
    trades.to_csv(args.trades, index=False)
    adopted = on_adopted_coordinates(symbols=symbols, timeframes=timeframes) and tuple(
        holds
    ) == tuple(HOLD_BARS)
    equity_lines, eq_worst = (
        equity_checksum(trades) if adopted else (["⏭️ 건너뜀 — 좁혀 돈 좌표."], math.nan)
    )
    summary = render_summary(
        result.rows,
        result.clusters,
        trades=trades,
        equity_lines=equity_lines,
        elapsed=elapsed,
        adopted=adopted,
    )
    args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    _lines, worst = checksum_wan432(result.rows, adopted=adopted)
    return 0 if checksum_passes(worst) and checksum_passes(eq_worst) else 1


if __name__ == "__main__":
    raise SystemExit(main())
