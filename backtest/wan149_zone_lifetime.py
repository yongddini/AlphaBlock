"""WAN-149 §4: 존 단위 생애 추적 — 「몇 번째 **진입** 뒤에 오더블록이 깨지나」.

사용자 원 질문(2026-07-19): *"같은 오더블록에 여러 번 진입하면 깨질 확률이 더 높아지잖아"*.

WAN-138이 이 질문을 **손익 축**에서 검정해 판정 (c)를 냈고, 진단으로 *"재탭 거래가 유독
더 잘 깨지는 게 아니라 같은 품질 거래가 더 적어질 뿐"*을 확인했다. **그런데 그 진단에는
생존 편향이 남아 있다** — 5번째 진입에 도달한 존은 앞의 4번을 이미 버틴 존이라, 높은
회차 표본은 **살아남은 존만** 모인다. 거래 단위 자료로는 못 걷어내고 **존 단위 생애
추적 + 생존 분석**이 필요하다. 그래서 라벨 단위를 거래가 아니라 **존**으로 바꾼다.

## 왜 이 이슈에 붙어 있나 — 「존 하나」의 정의가 여기서 정해진다

구 WAN-141이 *"병합 존 취급을 명시할 것(클러스터 단위인지 구성 존 단위인지에 따라 정의가
달라진다)"*고 남긴 숙제를, WAN-149 §1의 분리 전환이 **원본 존 단위 하나로 확정**한다.
병합 시절에 재면 곧바로 폐기되는 측정이라 같은 이슈에 합쳤다.

## 축은 「탭」이 아니라 「진입」이다 (2026-07-21 사용자 지적)

이 엔진에서 탭 ≠ 진입이고 **두 단계에서** 갈라진다:

* **① 탭** `tap_count` — 존 근단(proximal) 터치.
* **② 체결 가능** `fillable_count` — 지정가가 실제로 닿아 체결된 셋업. 진입가가 존 근단이
  아니라 **볼린저 재산정가**(존 안쪽)라, 근단만 찍고 되돌면 미체결이다.
* **③ 실제 진입** `entry_count` — 백테스트가 실제로 연 포지션. 엔진이 **플랫일 때만**
  진입하므로, 이미 포지션이 있으면 그 탭은 통째로 건너뛴다.

**판정은 ③ 진입 축으로 낸다.** 탭 축 곡선은 보조로 병기하되 결론을 그걸로 쓰지 않는다.

📌 **세 카운트를 다 남기는 이유 — 간극 자체가 정보다.**

1. **탭↔진입 간극 = 볼린저가 걸러낸 양.** 볼린저는 이 엔진의 **유일한 플러스
   부품**이므로(WAN-114/145), 그것이 존마다 얼마나 걸러내는지가 그대로 진단이다.
2. 🚨 **`entry_count == 0`인 채로 뚫린 존을 반드시 센다.** 탭은 했는데 한 번도 체결
   못 하고 무너진 존이다. 손익표에는 아예 안 잡히지만(손실이 안 났으니), *"존이 얼마나
   잘 뚫리나"*를 볼 때 이걸 빼면 **「우리가 들어간 존만」 보게 되어 또 다른 생존 편향**이
   된다 — 이 모듈이 걷어내려는 편향과 같은 부류다.
3. **②↔③ 간극 = 포지션이 차 있어서 놓친 자리.** 동시 1포지션 제약의 실제 비용이고
   WAN-108/130의 다중 포지션 논쟁에 새 자료가 된다(⚠️ 여기서 그 판정을 내지는 않는다).

## 방법론 — 단순 비율표는 미충족이다

* ⚠️ **우측 검열(right-censoring)을 처리한다.** 창 끝에 살아 있는 존을 "안 죽었다"로
  세면 수명이 부풀려진다. 회차별 단순 비율은 **이 측정이 고치려는 바로 그 편향을
  재생산한다**.
* **핵심 지표 = 위험률(hazard)** `h(n) = d(n) / R(n)`:
  - `R(n)` = **n번 진입에 도달한** 존 수(= 진입 수 ≥ n),
  - `d(n)` = 그중 **n번 진입에서 멈추고 무효화된** 존 수.
  즉 *"n번 버틴 존이 (n+1)번째 진입 대신 죽을 조건부 확률"* — 사용자 질문 그대로다.
  회차에 따라 **오르면 가설 성립(a)**, 평평·비단조면 **기각(b)**.
* **Kaplan-Meier 생존곡선** `S(n) = Π_{j≤n} (1 − h(j))`.
* **추세 검정** = 이산시간 생존 모형의 로지스틱 회귀 `logit h(n) = a + b·n`
  (존-회차 person-period 자료). `b > 0`이면 위험률 상승. 신뢰구간은 **존 단위 클러스터
  부트스트랩**으로 낸다 — 한 존이 여러 회차 행을 내므로 행 단위로 재표집하면 표본을
  실제보다 크게 본다.
* 축: 6심볼 × 15m·1h·4h·1d(§3과 같은 창) · **leave-one-out 심볼 편중 병기**.

⚠️ **새 라벨링 파이프라인이라 범용 CLI로는 답이 안 나온다** — CLAUDE.md §실험 도구가 말한
"CLI로 답이 안 나오는 것"이라 새 모듈이 정당하다. 골격은 `backtest.harness`에서 가져온다.

⚠️ **어느 판정이 나오든 「엣지 없음」(WAN-84/88/111/114/124/145)을 뒤집는 것으로 인용
금지** — 이 표는 존이 얼마나 오래 버티는가를 잴 뿐 수익의 근거를 재지 않는다.

## 재현

```
uv run python -m backtest.wan149_zone_lifetime --tf 1h,4h,1d
uv run python -m backtest.wan149_zone_lifetime --tf 15m --append
uv run python -m backtest.wan149_zone_lifetime --from-csv    # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

ALL_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)

TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
WORKING_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 위험률 표에 찍는 최대 회차. 그 위는 표본이 한 자리라 곡선이 읽히지 않는다(꼬리는
#: 검정에는 그대로 들어간다 — 표시만 자른다).
MAX_LEVEL = 6

#: 클러스터 부트스트랩 반복. WAN-70/84/88 널과 같은 자릿수.
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_SEED = 149

#: 판정을 내기 위한 최소 존 수. 이보다 적으면 곡선이 우연이라 「판정 불가」로 접는다.
MIN_ZONES = 100

#: 🚨 **추세 검정이 세는 최소 회차 = 1.** 이 상수가 이 모듈에서 가장 중요한 판단이다.
#:
#: `h(0)`의 위험집합은 **탐지된 모든 존**이고 그 대부분은 우리가 **탭조차 못 했거나 지정가가
#: 안 닿은 존**이다(1h 실측: 무효화된 존의 **72%가 진입 0회**). `h(1)` 이상의 위험집합은
#: **실제로 진입한 존**이다 — 즉 `h(0)`과 `h(1)`은 같은 모집단이 아니다. 두 칸을 한 회귀에
#: 넣으면 그 **모집단 교체**가 기울기로 둔갑한다.
#:
#: 실측이 정확히 그렇다(1h): 전곡선 기울기는 **+0.138(p=0.001)** 로 유의하지만, `h(0)`을
#: 빼면 **+0.009**(회차 1~4만 보면 **−0.004**)로 사라진다. 즉 그 유의성은 `h(0)→h(1)` 한 칸이
#: 통째로 만든 것이고, 그 칸은 *"진입을 한 번 더 하면 더 잘 깨지나"*가 아니라 *"우리가 들어갈
#: 수 있었던 존과 못 들어간 존이 다르다"*를 재고 있다.
#:
#: 사용자 질문은 **진입한 존**에 대한 것이므로(*"같은 오더블록에 여러 번 진입하면"*) 판정은
#: `min_level=1`로 낸다. 전곡선 값은 §2 표와 요약에 **함께 싣되 판정에 쓰지 않는다** —
#: 「틀린 이유로 맞는 답」(WAN-91/95/100/112/124 부류)을 막는 자리다.
MIN_LEVEL_ENTERED = 1


# --------------------------------------------------------------------------- #
# 존 1행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ZoneLife:
    """탐지된 존 하나의 생애. **거래가 아니라 존이 1행**이다."""

    symbol: str
    timeframe: str
    zone_id: int
    """탐지 아카이브 인덱스 — `OrderBlockSignal.zone_key`의 유일 원소(분리 엔진)."""
    direction: str
    confirmed_time: int
    top: float
    bottom: float
    width_atr: float | None
    """존폭 / 확정 시점 ATR. 진입 시점에 알 수 있는 특징만 싣는다(사후 정보 금지)."""
    ob_volume: float
    tap_count: int
    fillable_count: int
    entry_count: int
    broken: bool
    """무효화(breaker)로 뚫렸는가. False면 **창 끝까지 생존 = 우측 검열**이다."""
    break_time: int | None
    death_after_entries: int | None
    """몇 번째 진입 뒤에 죽었는가 — 사용자가 요청한 그 수치. 검열이면 None."""
    death_after_taps: int | None
    """탭 축 같은 값(보조·대조용)."""


ZONE_COLUMNS: tuple[str, ...] = tuple(ZoneLife.__dataclass_fields__)


# --------------------------------------------------------------------------- #
# 라벨링
# --------------------------------------------------------------------------- #


def _zone_id(zone_key: frozenset[int] | None) -> int | None:
    """분리 엔진의 `zone_key`는 원소 하나짜리 집합이다.

    병합 엔진에서 돌리면 원소가 여럿이라 **존 식별이 정의되지 않으므로 `None`을
    돌려준다** — 조용히 첫 원소를 집으면 클러스터 하나가 여러 존으로 쪼개져 위험률이
    말없이 틀린다. 이 모듈이 §1의 분리 전환 위에 서 있다는 사실을 코드가 드러낸다.
    """
    if zone_key is None or len(zone_key) != 1:
        return None
    return next(iter(zone_key))


def _width_atr(frame: pd.DataFrame, ob: OrderBlock) -> float | None:
    """존폭 / 확정 봉 ATR. 프레임에 `atr` 열이 없으면 None."""
    if "atr" not in frame.columns:
        return None
    row = frame[frame["open_time"] == ob.confirmed_time]
    if row.empty:
        return None
    atr = float(row["atr"].iloc[0])
    if not math.isfinite(atr) or atr <= 0:
        return None
    return (ob.top - ob.bottom) / atr


def label_cell(
    market: harness.MarketData, *, params: ConfluenceParams | None = None
) -> list[ZoneLife]:
    """한 (심볼, TF)의 모든 탐지 존을 1행씩 낸다.

    ⚠️ **채택 엔진을 그대로 태운다** — 셋업 탐색·체결·시퀀싱을 다시 짜지 않고
    `build_zone_limit_candidates` + `sequence_with_candidates`(= 시퀀서 본체)를 부른다.
    복제하면 그 사본이 본체와 갈라진다(WAN-77의 사본이 실제로 펀딩 배선을 빠뜨렸다).
    """
    if market.empty or market.df_1m.empty:
        return []
    conf = params or harness.build_params(entry_mode="zone_limit")
    cfg = harness.build_config(market.timeframe)
    ob_result = harness.detect_order_blocks(market)

    sink: list[SetupDiagnostic] = []
    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=conf,
        cfg=cfg,
        order_block_result=ob_result,
        setup_sink=sink,
    )
    paired = sequence_with_candidates(candidates, cfg, market.funding_rates)

    # ① 탭 — 탐지기의 재탭 뷰(`retap_signals`)가 곧 엔진이 소비하는 전체 탭 집합이다.
    taps: dict[int, list[int]] = {}
    for signal in ob_result.retap_signals:
        zid = _zone_id(signal.zone_key)
        if zid is not None:
            taps.setdefault(zid, []).append(signal.trigger_time)
    # ② 체결 가능 · ③ 실제 진입.
    fills: dict[int, list[int]] = {}
    for diag in sink:
        zid = _zone_id(diag.zone_key)
        if zid is not None and diag.filled:
            fills.setdefault(zid, []).append(diag.trigger_time)
    entries: dict[int, list[int]] = {}
    for candidate, _trade in paired:
        zid = _zone_id(candidate.zone_key)
        if zid is not None:
            entries.setdefault(zid, []).append(candidate.entry_time)

    frame = harness_atr_frame(market)
    lives: list[ZoneLife] = []
    for index, ob in enumerate(ob_result.order_blocks):
        break_time = ob.break_time
        broken = break_time is not None
        lives.append(
            ZoneLife(
                symbol=market.symbol,
                timeframe=market.timeframe,
                zone_id=index,
                direction=("bull" if ob.direction is OrderBlockDirection.BULLISH else "bear"),
                confirmed_time=ob.confirmed_time,
                top=ob.top,
                bottom=ob.bottom,
                width_atr=_width_atr(frame, ob),
                ob_volume=ob.ob_volume,
                tap_count=_count_before(taps.get(index, ()), break_time),
                fillable_count=_count_before(fills.get(index, ()), break_time),
                entry_count=_count_before(entries.get(index, ()), break_time),
                broken=broken,
                break_time=break_time,
                death_after_entries=(
                    _count_before(entries.get(index, ()), break_time) if broken else None
                ),
                death_after_taps=(
                    _count_before(taps.get(index, ()), break_time) if broken else None
                ),
            )
        )
    return lives


def _count_before(times: Sequence[int], break_time: int | None) -> int:
    """무효화 **전에** 일어난 사건만 센다.

    존이 뚫린 뒤의 탭은 그 존의 생애가 아니다(breaker 존을 되쓸린 가격 움직임이다).
    이 경계가 흐려지면 사망 회차가 뒤로 밀려 위험률이 낮게 나온다.
    """
    if break_time is None:
        return len(times)
    return sum(1 for t in times if t < break_time)


def harness_atr_frame(market: harness.MarketData) -> pd.DataFrame:
    """존폭 정규화용 ATR을 붙인 상위TF 프레임(없으면 원본 그대로)."""
    from strategy.indicators import atr as atr_series

    frame = market.htf_df.sort_values("open_time").reset_index(drop=True)
    try:
        return frame.assign(atr=atr_series(frame))
    except Exception:  # noqa: BLE001 — ATR은 부가 특징이라 없다고 라벨링을 멈추지 않는다.
        return frame


# --------------------------------------------------------------------------- #
# 생존 분석
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HazardRow:
    """위험률 한 회차."""

    level: int
    at_risk: int
    deaths: int
    hazard: float
    survival: float


def hazard_table(lives: Sequence[ZoneLife], *, axis: str = "entry") -> list[HazardRow]:
    """회차별 위험률 + Kaplan-Meier 생존곡선.

    `axis="entry"`가 정본이고 `"tap"`은 보조다. 검열(창 끝까지 생존)은 `deaths`에
    안 들어가되 `at_risk`에는 들어간다 — 그것이 검열 처리의 전부이자 핵심이다.
    """
    counts = [_axis_count(life, axis) for life in lives]
    deaths_at = [c for c, life in zip(counts, lives, strict=True) if life.broken]
    if not counts:
        return []
    rows: list[HazardRow] = []
    survival = 1.0
    for level in range(0, max(counts) + 1):
        at_risk = sum(1 for c in counts if c >= level)
        if at_risk == 0:
            break
        deaths = sum(1 for c in deaths_at if c == level)
        hazard = deaths / at_risk
        survival *= 1.0 - hazard
        rows.append(
            HazardRow(level=level, at_risk=at_risk, deaths=deaths, hazard=hazard, survival=survival)
        )
    return rows


def _axis_count(life: ZoneLife, axis: str) -> int:
    if axis == "entry":
        return life.entry_count
    if axis == "tap":
        return life.tap_count
    raise ValueError(f"알 수 없는 축: {axis!r} (entry|tap)")


def _person_periods(
    lives: Sequence[ZoneLife], axis: str
) -> list[tuple[int, list[tuple[int, int]]]]:
    """존별 person-period 행 — `(회차, 사망여부)` 목록.

    존 하나가 `T+1`개 행(회차 0..T)을 내고, 사망한 존만 마지막 행이 1이다. 이 구조가
    이산시간 생존 모형의 우도이며, **클러스터(존) 단위로 재표집**해야 하는 이유이기도
    하다(행 단위 재표집은 표본을 실제보다 크게 본다).
    """
    periods: list[tuple[int, list[tuple[int, int]]]] = []
    for zone_index, life in enumerate(lives):
        total = _axis_count(life, axis)
        rows = [(level, 0) for level in range(total)]
        rows.append((total, 1 if life.broken else 0))
        periods.append((zone_index, rows))
    return periods


def _fit_logit(rows: Sequence[tuple[int, int]]) -> float | None:
    """`logit h(n) = a + b·n`의 기울기 `b`. IRLS 몇 번이면 수렴한다(2모수).

    분리(모든 행이 0이거나 1)면 기울기가 무한이라 `None`을 돌려준다 — 그 경우를 큰
    유한값으로 적으면 부트스트랩 평균이 조용히 부풀려진다.
    """
    if not rows:
        return None
    events = sum(y for _, y in rows)
    if events == 0 or events == len(rows):
        return None
    a, b = 0.0, 0.0
    for _ in range(50):
        g00 = g01 = g11 = 0.0
        s0 = s1 = 0.0
        for level, y in rows:
            eta = a + b * level
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, eta))))
            w = max(p * (1.0 - p), 1e-9)
            resid = y - p
            s0 += resid
            s1 += resid * level
            g00 += w
            g01 += w * level
            g11 += w * level * level
        det = g00 * g11 - g01 * g01
        if abs(det) < 1e-12:
            return None
        da = (g11 * s0 - g01 * s1) / det
        db = (-g01 * s0 + g00 * s1) / det
        a, b = a + da, b + db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return b


@dataclass(frozen=True)
class TrendResult:
    """추세 검정 결과 — 기울기와 클러스터 부트스트랩 구간."""

    slope: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    n_zones: int
    n_broken: int

    @property
    def increasing(self) -> bool:
        """95% 구간이 통째로 0보다 큰가(= 위험률이 회차에 따라 오른다)."""
        return self.slope is not None and self.ci_low is not None and self.ci_low > 0.0


def trend_test(
    lives: Sequence[ZoneLife],
    *,
    axis: str = "entry",
    min_level: int = MIN_LEVEL_ENTERED,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> TrendResult:
    """이산시간 생존 모형의 기울기 + 존 단위 클러스터 부트스트랩 구간.

    `min_level`이 이 함수의 핵심 인자다 — 위 상수 문단을 읽을 것. 기본값은 **1**이라
    「한 번이라도 진입한 존」에서만 재고, `0`을 주면 진입 없이 죽은 존까지 포함한 전곡선
    기울기가 나온다(그 값은 판정에 쓰지 않는다).
    """
    periods = [
        (zone, [(level, y) for level, y in rows if level >= min_level])
        for zone, rows in _person_periods(lives, axis)
    ]
    periods = [(zone, rows) for zone, rows in periods if rows]
    n_broken = sum(1 for life in lives if life.broken)
    flat = [row for _, rows in periods for row in rows]
    slope = _fit_logit(flat)
    if slope is None or len(periods) < 2:
        return TrendResult(slope, None, None, None, len(lives), n_broken)

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(iterations):
        sample = [
            row for _ in range(len(periods)) for row in periods[rng.randrange(len(periods))][1]
        ]
        value = _fit_logit(sample)
        if value is not None:
            draws.append(value)
    # `iterations=0`(점추정만 원할 때, leave-one-out이 그렇게 쓴다)이면 `draws`가 비어
    # 있다 — `0 < 0`은 거짓이라 이 가드를 `<`만으로 두면 빈 리스트를 인덱싱한다.
    if not draws or len(draws) < iterations // 2:
        return TrendResult(slope, None, None, None, len(lives), n_broken)
    draws.sort()
    low = draws[int(0.025 * len(draws))]
    high = draws[min(len(draws) - 1, int(0.975 * len(draws)))]
    # 양측 p — 부트스트랩 분포가 0을 넘는 비율(부호 검정). 0이면 1/iterations로 바닥을
    # 깐다(0을 적으면 "정확히 0"으로 읽혀 표본이 주는 해상도를 넘어선 주장이 된다).
    beyond = sum(1 for d in draws if (d <= 0.0) if slope > 0) + sum(
        1 for d in draws if (d >= 0.0) if slope <= 0
    )
    p_value = max(2.0 * beyond / len(draws), 1.0 / len(draws))
    return TrendResult(slope, low, high, min(p_value, 1.0), len(lives), n_broken)


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def verdict(lives: Sequence[ZoneLife], timeframe: str) -> str:
    """진입 축 위험률이 회차에 따라 오르는가 — 사용자 가설의 검정.

    ⚠️ 표본(존 수)이 모자라면 판정하지 않는다. 4h·1d가 그 자리이고, §3의 거래 수
    게이트와 같은 역할을 존 수로 한다.
    """
    subset = [life for life in lives if life.timeframe == timeframe]
    if not subset:
        return "판정 불가(데이터 없음)."
    result = trend_test(subset)
    full = trend_test(subset, min_level=0)
    table = hazard_table(subset)
    shown = ", ".join(f"h({r.level})={r.hazard * 100:.1f}%" for r in table[: MAX_LEVEL + 1])
    head = (
        f"존 {result.n_zones}개(무효화 {result.n_broken}개) · 진입 축 위험률 {shown}"
        if table
        else f"존 {result.n_zones}개"
    )
    if timeframe not in WORKING_TIMEFRAMES or result.n_zones < MIN_ZONES:
        return (
            f"⚠️ **판정 불가(대조군)** — {timeframe}는 작업 TF가 아니거나 존 표본이 "
            f"{MIN_ZONES}개 미만이다(WAN-107/130의 4h 「보류」와 같은 자리). {head}. "
            "아래 숫자는 방향을 보는 참고값이지 채택 근거가 아니다."
        )
    if result.slope is None:
        return f"판정 불가(추세 모형이 수렴하지 않음) — {head}."
    band = (
        f"[{result.ci_low:+.3f}, {result.ci_high:+.3f}]"
        if result.ci_low is not None and result.ci_high is not None
        else "구간 없음"
    )
    if result.increasing:
        tag = (
            "**(a) 위험률이 진입 회차에 따라 오른다 — 사용자 가설 성립.** 재탭 정책"
            "(`retap_mode`) 재검의 근거가 된다(⚠️ 단 그 판정은 WAN-138 소관이고 이 표가 "
            "직접 내리지 않는다)"
        )
    else:
        tag = (
            "**(b) 위험률이 진입 회차에 따라 오르지 않는다 — 가설 기각.** WAN-138의 진단"
            "(*재탭 거래가 유독 더 잘 깨지는 게 아니라 같은 품질 거래가 더 적어질 뿐*)이 "
            "**생존 편향을 걷어낸 뒤에도 유지**된다"
        )
    # 🚨 전곡선 값을 **판정 옆에 나란히** 찍는다 — 이 둘이 갈릴 때가 정확히 이 모듈이
    # 경계하는 자리이고(모집단 교체가 기울기로 둔갑), 따로 두면 인용할 때 하나만 떼어 간다.
    contrast = ""
    if full.slope is not None and (full.increasing != result.increasing):
        contrast = (
            f" 🚨 **전곡선(회차 0 포함) 기울기는 b={full.slope:+.3f}로 부호·유의성이 다르다 — "
            "그 차이는 신호가 아니라 **모집단 교체**다**: `h(0)`의 위험집합은 탐지된 모든 존이고 "
            "그 대부분은 우리가 **진입조차 못 한 존**이라(§4 간극 표) `h(1)` 이상과 같은 "
            "모집단이 아니다. **전곡선 값을 사용자 가설의 근거로 인용 금지.**"
        )
    return (
        f"{tag} — 이산시간 생존 모형 기울기 b={result.slope:+.3f} "
        f"(**진입한 존만**, 회차 ≥{MIN_LEVEL_ENTERED} · 존 단위 클러스터 부트스트랩 95% "
        f"{band}, p={result.p_value:.3f}). {head}.{contrast}"
    )


# --------------------------------------------------------------------------- #
# 실행 · 렌더
# --------------------------------------------------------------------------- #


def lives_to_frame(lives: Sequence[ZoneLife]) -> pd.DataFrame:
    return pd.DataFrame([vars(life) for life in lives], columns=list(ZONE_COLUMNS))


def lives_from_csv(path: Path) -> list[ZoneLife]:
    frame = pd.read_csv(path)
    # ⚠️ `frame.where(notna, None)`만으로는 **float 열의 NaN이 그대로 남는다**(pandas가
    # float dtype에 None을 못 넣어 NaN으로 되돌린다). `astype(object)`를 먼저 씌워야
    # 검열된 존의 빈 칸(`break_time`·`death_after_*`)이 진짜 `None`으로 온다.
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [_life_from_record(record) for record in records]


def _life_from_record(record: dict[str, object]) -> ZoneLife:
    """CSV 한 행 → `ZoneLife`. 열마다 타입을 되돌린다(`**dict`는 타입이 안 잡힌다)."""

    def opt_int(key: str) -> int | None:
        value = record.get(key)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return int(float(str(value)))

    def req_int(key: str) -> int:
        value = opt_int(key)
        if value is None:
            raise ValueError(f"{key}는 비어 있을 수 없습니다: {record}")
        return value

    def opt_float(key: str) -> float | None:
        value = record.get(key)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return float(str(value))

    def req_float(key: str) -> float:
        value = opt_float(key)
        if value is None:
            raise ValueError(f"{key}는 비어 있을 수 없습니다: {record}")
        return value

    return ZoneLife(
        symbol=str(record["symbol"]),
        timeframe=str(record["timeframe"]),
        zone_id=req_int("zone_id"),
        direction=str(record["direction"]),
        confirmed_time=req_int("confirmed_time"),
        top=req_float("top"),
        bottom=req_float("bottom"),
        width_atr=opt_float("width_atr"),
        ob_volume=req_float("ob_volume"),
        tap_count=req_int("tap_count"),
        fillable_count=req_int("fillable_count"),
        entry_count=req_int("entry_count"),
        broken=str(record["broken"]).strip().lower() in {"true", "1"},
        break_time=opt_int("break_time"),
        death_after_entries=opt_int("death_after_entries"),
        death_after_taps=opt_int("death_after_taps"),
    )


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    timeframes: Sequence[str] = TIMEFRAMES,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    log: bool = True,
) -> list[ZoneLife]:
    from backtest.run import parse_date_ms

    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    lives: list[ZoneLife] = []
    for symbol in symbols:
        normalized = harness.normalize_symbol(symbol)
        for timeframe in timeframes:
            market = harness.load_market_data(
                normalized, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True
            )
            cell = label_cell(market)
            lives.extend(cell)
            if log:
                broken = sum(1 for life in cell if life.broken)
                print(f"[wan149] {normalized} {timeframe}: 존 {len(cell)}개(무효화 {broken})")
    return lives


def _md_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_(데이터 없음)_"
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    divider = "| " + " | ".join("--" for _ in frame.columns) + " |"
    body = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in record) + " |"
        for record in frame.itertuples(index=False, name=None)
    ]
    return "\n".join([header, divider, *body])


def hazard_frame(lives: Sequence[ZoneLife], timeframe: str, axis: str) -> pd.DataFrame:
    subset = [life for life in lives if life.timeframe == timeframe]
    rows = hazard_table(subset, axis=axis)[: MAX_LEVEL + 1]
    return pd.DataFrame(
        [
            {
                "level": r.level,
                "at_risk": r.at_risk,
                "deaths": r.deaths,
                "hazard%": round(r.hazard * 100, 2),
                "survival%": round(r.survival * 100, 2),
            }
            for r in rows
        ]
    )


def gap_frame(lives: Sequence[ZoneLife]) -> pd.DataFrame:
    """탭 → 체결 가능 → 진입 간극, 그리고 **한 번도 못 들어간 채 뚫린 존**."""
    frame = lives_to_frame(lives)
    if frame.empty:
        return frame
    grouped = frame.groupby("timeframe", as_index=False).agg(
        zones=("zone_id", "count"),
        broken=("broken", "sum"),
        taps=("tap_count", "sum"),
        fillable=("fillable_count", "sum"),
        entries=("entry_count", "sum"),
    )
    never = (
        frame.assign(never_entered_broken=(frame["entry_count"] == 0) & frame["broken"])
        .groupby("timeframe", as_index=False)["never_entered_broken"]
        .sum()
    )
    out = grouped.merge(never, on="timeframe", how="left")
    out["fill_per_tap%"] = (out["fillable"] / out["taps"] * 100).round(2)
    out["entry_per_fill%"] = (out["entries"] / out["fillable"] * 100).round(2)
    out["never_entered_broken%"] = (out["never_entered_broken"] / out["broken"] * 100).round(2)
    return out


def leave_one_out(lives: Sequence[ZoneLife], timeframe: str) -> pd.DataFrame:
    """심볼 하나를 빼도 기울기의 부호가 남는가."""
    subset = [life for life in lives if life.timeframe == timeframe]
    base = trend_test(subset, iterations=0)
    symbols = sorted({life.symbol for life in subset})
    # 판정과 같은 축(진입한 존만)으로 뺀다 — 다른 축으로 재면 표와 판정이 갈라진다.
    records = [
        {
            "dropped": symbol,
            "slope": None
            if (s := trend_test([x for x in subset if x.symbol != symbol], iterations=0).slope)
            is None
            else round(s, 4),
            "base_slope": None if base.slope is None else round(base.slope, 4),
        }
        for symbol in symbols
    ]
    return pd.DataFrame(records)


def write_summary(lives: Sequence[ZoneLife], path: Path) -> None:
    timeframes = [tf for tf in TIMEFRAMES if any(life.timeframe == tf for life in lives)]
    lines = [
        "# WAN-149 §4 존 단위 생애 추적 — 몇 번째 **진입** 뒤에 오더블록이 깨지나",
        "",
        "재현: `uv run python -m backtest.wan149_zone_lifetime`"
        " (요약만 재생성은 `--from-csv`) · 원본 CSV: `wan149_zone_lifetime.csv` (**존 1행**)",
        "",
        "6심볼 · 못 박은 창(2023-07-14~2026-07-15) · **원본 존 단위**(WAN-149 §1 분리 전환이 "
        "「존 하나」의 정의를 확정한다) · 채택 기본값 엔진 그대로.",
        "",
        "⚠️ **우측 검열을 처리한 값이다** — 창 끝까지 살아 있는 존은 `deaths`가 아니라 "
        "`at_risk`에만 들어간다. 회차별 단순 비율표는 이 측정이 고치려는 바로 그 편향을 "
        "재생산하므로 쓰지 않는다.",
        "",
        "## 1. 판정 — 진입 축 위험률의 추세",
        "",
    ]
    for timeframe in timeframes:
        role = "작업 TF" if timeframe in WORKING_TIMEFRAMES else "대조군"
        lines += [f"* **{timeframe}** ({role}): {verdict(lives, timeframe)}", ""]
    lines += [
        "⚠️ **어느 판정이든 「엣지 없음」(WAN-84/88/111/114/124/145)을 뒤집는 것으로 인용 "
        "금지** — 이 표는 존이 얼마나 오래 버티는가를 잴 뿐 수익의 근거를 재지 않는다.",
        "",
        "## 2. 위험률 · Kaplan-Meier 생존곡선 (진입 축 = 정본)",
        "",
        "`h(n)` = **n번 진입에 도달한** 존이 (n+1)번째 진입 대신 무효화될 조건부 확률. "
        "`survival%` = `Π(1−h)`. **오르면 사용자 가설 성립, 평평·비단조면 기각.**",
        "",
        "🚨 **`h(0)`은 다른 모집단이다 — 추세 판정에서 제외한다.** 그 위험집합은 탐지된 "
        "**모든** 존이고 대부분은 우리가 탭조차 못 했거나 지정가가 안 닿은 존이다(§4 간극 "
        "표의 `never_entered_broken%`). `h(1)` 이상은 **실제로 진입한 존**이라, 두 칸을 한 "
        "회귀에 넣으면 그 **모집단 교체가 기울기로 둔갑**한다. 숫자는 그대로 싣되 판정은 "
        "회차 ≥1에서 낸다.",
        "",
    ]
    for timeframe in timeframes:
        lines += [
            f"### {timeframe}",
            "",
            _md_table(hazard_frame(lives, timeframe, "entry")),
            "",
        ]
    lines += [
        "## 3. 탭 축 (보조 — 결론을 이걸로 쓰지 않는다)",
        "",
        "탭 축으로 재면 회차가 부풀려지고 곡선의 x축이 사용자가 겪는 경험과 어긋난다"
        "(2026-07-21 사용자 지적). 진입 축과 모양이 다른지만 본다.",
        "",
    ]
    for timeframe in timeframes:
        lines += [
            f"### {timeframe}",
            "",
            _md_table(hazard_frame(lives, timeframe, "tap")),
            "",
        ]
    lines += [
        "## 4. 세 카운트의 간극 — 간극 자체가 정보다",
        "",
        "`fill_per_tap%` = 탭↔진입 간극의 첫 단(**볼린저가 걸러낸 양** — 이 엔진의 유일한 "
        "플러스 부품이 존마다 얼마나 걸러내는지, WAN-114/145). "
        "`entry_per_fill%` = **포지션이 차 있어서 놓친 자리**(동시 1포지션 제약의 실제 비용 — "
        "⚠️ WAN-108/130의 다중 포지션 판정을 여기서 내지는 않는다, 기록만). "
        "🚨 `never_entered_broken` = **한 번도 체결 못 하고 무너진 존**. 손익표에는 아예 안 "
        "잡히지만 이걸 빼면 「우리가 들어간 존만」 보게 되어 또 다른 생존 편향이 된다.",
        "",
        _md_table(gap_frame(lives)),
        "",
        "## 5. leave-one-out — 심볼 하나가 기울기를 다 만드는가",
        "",
        "`slope` = 그 심볼을 뺀 5심볼의 추세 기울기(부트스트랩 없이 점추정만). 부호가 "
        "심볼 하나에 뒤집히면 판정을 신뢰할 수 없다.",
        "",
    ]
    for timeframe in timeframes:
        if timeframe not in WORKING_TIMEFRAMES:
            continue
        lines += [f"### {timeframe}", "", _md_table(leave_one_out(lives, timeframe)), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--out-csv", type=str, default="backtest/reports/wan149_zone_lifetime.csv")
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan149_zone_lifetime_summary.md"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 --out-csv에 이어 붙인다(비싼 TF를 따로 돌릴 때)",
    )
    parser.add_argument(
        "--from-csv", action="store_true", help="라벨링을 다시 돌리지 않고 요약만 재생성한다"
    )
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        lives = lives_from_csv(out_csv)
        print(f"[wan149] {out_csv}에서 존 {len(lives)}행 로드 — 라벨링 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        lives = list(run_report(symbols, timeframes, start=args.start, end=args.end))
        if args.append and out_csv.exists():
            lives = lives_from_csv(out_csv) + lives
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        lives_to_frame(lives).to_csv(out_csv, index=False)
    write_summary(lives, Path(args.out_md))
    print(f"[wan149] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
