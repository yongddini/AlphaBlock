"""WAN-410 — 하루 손실 서킷브레이커: 그날 실현손실이 문턱을 넘으면 신규 진입을 멈춘다.

## 한 줄

WAN-408 §0이 *「손실의 59.3%가 최악 10일에 있고 그 날은 10~12개 종목이 같이 죽는다」*를
냈다. 이 모듈은 그 꼬리를 **인과적으로** 자를 수 있는지 잰다 — 그날 **이미 청산된** 손익이
문턱을 넘으면 그 **뒤에** 진입하는 거래를 막고, 그 팔을 **북에서 처음부터 다시 배치**해
성적을 낸다.

🚨 **이 모듈은 채택 권고를 내지 않는다 — 재기만 한다**(사용자 결정 2026-09-09 *「일단은
재기만 하자」*). 문턱 선정 기준을 착수 전에 못 박지 않았고, 그래서 요약에 *「―R이 최적」*·
*「이 문턱을 권고한다」*를 **쓰지 않는다**. argmax는 표에 찍되 **채택 근거가 아니다**
(WAN-161 규약). 「조일수록 좋다」로 나오는 것은 예상된 결과이지 발견이 아니다 — 극한은
「매매를 하지 마라」다.

## 이 축이 지금까지의 시도와 다른 점

WAN-366/368/378/381/386/395는 전부 *「어떤 셋업이 좋은가」*(선별)를 물었고 전부 「채택
권고 없음」이었다. 이 축은 **엣지를 주장하지 않는다** — 꼬리를 자르는 **리스크 관리**다.
그래서 판정 자도 하나가 아니다(§3겹).

## §1-0 — 정의를 먼저 못 박는다 (선행 관문)

WAN-408 §0의 선행 지표 둘이 PM 독립 재현과 안 맞았다. §1의 스위치가 그 정의 위에 서므로
**먼저 가른다** — 그리고 가르는 방법은 *「누가 맞나」*를 논하는 것이 아니라 **정의를 둘 다
구현해 숫자를 나란히 내는 것**이다.

* **(b) 그 순간 열린 칸 수** — 두 규약을 함께 낸다.
  * `wan408`: `#(진입 < t) − #(청산 ≤ t)` (공개 CSV가 쓴 식 그대로).
  * `interval`: `#{진입 < t < 청산}` (「그 순간 실제로 열려 있는 포지션」).
  🚨 **둘의 차는 정확히 「`진입 == 청산 == t`인 거래 수」다**(§1-0 대수 항등 ·
  `open_cells_gap` 열이 그 항등을 매 실행 확인한다). 그런 거래는 실재한다 — 「같은 분
  익절」(WAN-336: 채택 북 `oos_warm` 거래의 7.37% · 순손익의 48%)이 그 부류이고, 그
  거래는 **자기 자신을 열린 칸에서 빼고** 있었다.
* **(c) 그날 실현 net R 누적** — 경계 둘을 함께 낸다.
  * `strict`: `청산 < t`(WAN-408 §0-3이 쓴 `bisect_left`).
  * `settled`: `청산 ≤ t`(**북이 실제로 아는 것** — `settle_due(t)`가 그 경계다).
  🚨 스위치는 `settled`를 읽는다 — 관측이 아니라 **집행**이라 「북이 그 순간 아는 것」이
  정본이어야 한다. 그 선택의 크기가 이 표에 숫자로 남는다.

🚨 **§1-0은 복리를 켜고 잰다 — §1-1은 끄고 잰다. 일부러 다르다.** §1-0이 대조하는 WAN-408
공개 표(그리고 사용자가 올린 `book_trades.csv`)가 **인자 없는 채택 북**의 산출물이라 같은
발판 위에 서야 하고(검산 (a′)가 거래 수 14,843·거래당 net R을 대조한다), §1-1의 판정 자는
거래당 net R이라 복리 총수익이 포화하는 것을 피해야 한다(WAN-346 §2 · WAN-388).

⚠️ **이 표는 관측이지 반사실이 아니다**(WAN-408 §0-3과 같은 경고) — 「그 문턱에서 막았다면」의
손익은 막힌 거래가 비운 자본·슬롯을 다른 칸이 쓰기 때문에 여기서 읽을 수 없다. 그것이 §1-1이다.

## §1-1 — 서킷브레이커 팔 (북에서 · 세 겹으로 잰다)

**1겹 — 라벨 필터가 아니라 북에서 다시 돌린다.** 문턱마다 지갑을 처음부터 다시 배치한다
(WAN-316). 「매번 다른 거래」는 문제가 아니라 **측정 대상**이다 — 막았을 때 그 자리에 뭐가
들어오는지까지 포함한 것이 진짜 답이다.

**2겹 — 매칭 대조군**(§`--part null`). 각 문턱이 실제로 만든 `(KST 날짜, 첫 발동 시각)`
쌍을 **다른 날로 무작위 재배정**해, *같은 날짜 수 · 같은 하루 중 시각 분포로 쉬되 손실과
무관하게* 쉬는 팔을 만든다(20시드 · 단측 순위 p · WAN-142와 같은 자). 실제 팔이 대조군을
못 이기면 개선은 **「덜 매매한 몫」**이고 이 축은 죽는다.

**3겹 — 자를 하나만 쓰지 않는다.** 거래당 net R(남은 거래의 질) · net R 합(계좌가 실제로
얼마 잃었나) · MDD와 **지갑 정의 여부**(이 축의 존재 이유) · 발동일 수와 차단 비중(실용성) ·
gross/비용 분해(선별인가 비용인가). 📌 **셋이 서로 다른 문턱을 가리키면 그것도 결과다** —
억지로 하나로 합치지 않는다(WAN-330 선례).

🚨 **지갑 정의 여부가 사실상 첫 관문이다** — 오늘 채택 북은 WAN-365 이후 기대값이 음수라
복리를 꺼도 자본이 0을 뚫어 지갑 층 열이 「정의 상실」로 찍힌다(WAN-386/388 `wallet_defined`).
**어느 문턱이 지갑을 되살리는가**가 그 자체로 정보고, **하나도 못 살리면 그것도 답이다.**

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목 × 4TF **한 지갑** · 못 박은 6년 창 · 존폭 필터 끔(WAN-384) · 인과 취소(WAN-365) ·
재진입 ON(band, WAN-273) · cap_only 5배 · 익절 메이커(WAN-370) · `baseline` 렌즈 ·
복리 **끔**(판정 자가 거래당 net R이다 — WAN-346 §2).

## 🚨 캐시가 전 칸 미스가 된다 — 그게 맞는 동작이다

이 PR이 `backtest/leverage_book.py`·`backtest/book_cli.py`를 고쳤고 둘 다
`trade_store.ENGINE_SOURCE_FILES`에 있어 **엔진 소스 지문이 바뀐다**(WAN-253). 후보는 한
글자도 안 달라지지만 캐시는 그것을 모르므로 미스가 맞다 — 엔진이 바뀐 줄 모르고 옛 후보를
내주는 것이 이 저장소 최악의 사고이기 때문이다(WAN-364). 📌 **그 「후보는 안 달라진다」를
주장이 아니라 검산으로** 낸다(`--part checksum`의 (d)).

## 검산

* **(a) 「멈춤 없음」 팔 ≡ 인자 없는 채택 북** — 같은 payload를 이 모듈의 배치와 채택 북
  배치로 각각 돌려 `0.00e+00`(복리 **켜고** 대조한다 — 채택 북이 복리로 돈다).
* **(b) 팔이 실제로 걸렸다** — 문턱을 넘은 뒤 진입한 거래가 **0건**인지 **동작으로** 센다
  (라벨이 아니라). 차단 건수 0인 팔은 「발동 안 함」으로 따로 찍는다.
* **(c) 이미 열린 포지션은 안 건드린다** — 서킷브레이커 팔의 거래 중 **첫 발동보다 먼저
  진입한** 거래의 청산 시각·사유가 기준 팔과 같은지 본다.
* **(d) 후보 층 불변** — 이 PR의 엔진 편집이 **배치 축**이라는 것을 후보 수로 확인한다.

재현::

    uv run python -m backtest.wan410_daily_loss_circuit_breaker --part defs --jobs 2
    uv run python -m backtest.wan410_daily_loss_circuit_breaker --part grid --jobs 2
    uv run python -m backtest.wan410_daily_loss_circuit_breaker --part null --jobs 2
    uv run python -m backtest.wan410_daily_loss_circuit_breaker --part loo --jobs 2
    uv run python -m backtest.wan410_daily_loss_circuit_breaker --part checksum --jobs 2
    uv run python -m backtest.wan410_daily_loss_circuit_breaker --from-csv      # 요약만
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, get_args

import pandas as pd
from pydantic import BaseModel, ConfigDict, model_validator

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import (
    CIRCUIT_BREAKER_SCOPES,
    CircuitBreakerScope,
    LeverageBookParams,
)
from backtest.models import BacktestConfig
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan370_cost_decomposition import decompose_trade, stop_width_fraction
from backtest.wan408_loss_clustering import (
    OPEN_BUCKETS,
    REALIZED_BUCKETS,
    TradeFact,
    _bucket_label_int,
    _bucket_label_realized,
    _bucket_order_int,
    _bucket_order_realized,
    trade_facts,
)
from backtest.wan409_invalidation_cascade import (
    PUBLISHED_OOS_WARM_MEAN_NET_R,
    PUBLISHED_OOS_WARM_TRADES,
)
from common.timefmt import kst_day_key

__all__ = [
    "GRID_CSV",
    "NULL_DRAWS",
    "NULL_SEED",
    "STOP_LIMITS",
    "THRESHOLDS",
    "Arm",
    "ChecksumRow",
    "DefinitionRow",
    "GridRow",
    "LooRow",
    "NullRow",
    "arms",
    "build_payloads",
    "build_summary_markdown",
    "definition_rows",
    "grid_rows",
    "null_rows",
    "open_cells_before",
    "place",
    "realized_r_before",
    "trip_schedule",
    "wallet_defined",
]

REPORTS_DIR = Path("backtest/reports")
DEFS_CSV = REPORTS_DIR / "wan410_definitions.csv"
GRID_CSV = REPORTS_DIR / "wan410_grid.csv"
NULL_CSV = REPORTS_DIR / "wan410_null.csv"
LOO_CSV = REPORTS_DIR / "wan410_loo.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan410_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan410_circuit_breaker_summary.md"

#: 주 구간 — 판정은 여기서 낸다(WAN-166 따뜻한 연속 OOS).
PRIMARY_SEGMENT = harness.SEGMENT_OOS_WARM

#: 하루 실현손실 문턱(R). 🚨 **−1R·−2R까지 연다** — PM 사전 측정의 최선(−3R)이 격자 끝이었고
#: WAN-381이 배운 자리가 정확히 그것이다(「거기가 최적」이 아니라 「격자가 거기서 끝났다」).
THRESHOLDS: tuple[float, ...] = (-1.0, -2.0, -3.0, -5.0, -8.0, -10.0, -15.0, -20.0, -30.0)

#: 대조군 축 B — 「그날 손절 N건이면 멈춘다」. 🚨 **주 축이 아니다**(이슈 §주 축 = A).
#: B는 이긴 거래를 안 세므로 **버는 날을 끈다**(발동 548일 중 40.1%가 플러스로 끝난 날).
STOP_LIMITS: tuple[int, ...] = (1, 2, 3, 5, 8)

#: 매칭 대조군 추첨 수·시드 — WAN-142와 **같은 자**(20시드 · 단측 순위 p → 최소 p = 0.048).
NULL_DRAWS = 20
NULL_SEED = 410

#: 「0과 구분되지 않는다」 선(WAN-366/381 규약).
NOISE_R = 0.005

#: 종목당 유효 거래 하한(WAN-84 이래 같은 자).
MIN_TRADES_PER_SYMBOL = 20

#: §1-0 버킷 하한 — 이보다 얇은 버킷은 「가장 나쁜 버킷」 후보에서 뺀다(같은 20건 자).
MIN_BUCKET_TRADES = 20

#: 검산 (a)가 대조하는 열.
_CHECK_METRICS = ("num_trades", "win_rate", "total_return", "max_drawdown", "peak_concurrency")


# --------------------------------------------------------------------------- #
# 팔
# --------------------------------------------------------------------------- #


class _BreakerKwargs(TypedDict, total=False):
    """`place`가 받는 서킷브레이커 인자 — 빈 dict면 예전과 비트 단위로 같다."""

    daily_loss_limit_r: float | None
    daily_stop_limit: int | None
    circuit_breaker_scope: CircuitBreakerScope


@dataclass(frozen=True)
class Arm:
    """격자 한 팔 — 「무엇을 · 언제 · 무엇에 대해」 멈추나."""

    name: str
    label: str
    kind: str
    """`"base"`(멈춤 없음) · `"loss"`(축 A) · `"stops"`(축 B 대조군)."""
    threshold: float | None = None
    stop_limit: int | None = None
    scope: CircuitBreakerScope = "both"

    @property
    def is_base(self) -> bool:
        return self.kind == "base"

    def kwargs(self) -> _BreakerKwargs:
        """배치에 넘길 서킷브레이커 인자 — 기준 팔은 **아무것도 안 넘긴다**(비트 재현)."""
        if self.is_base:
            return {}
        return {
            "daily_loss_limit_r": self.threshold,
            "daily_stop_limit": self.stop_limit,
            "circuit_breaker_scope": self.scope,
        }

    def in_scope(self, is_reentry: bool) -> bool:
        """이 팔이 그 후보를 막는 범위인가 — 엔진의 판정과 **같은 술어**여야 한다."""
        if self.scope == "both":
            return True
        return is_reentry if self.scope == "reentry" else not is_reentry


BASE_ARM = Arm(name="base", label="멈춤 없음(채택 북)", kind="base")

_SCOPE_LABEL: dict[CircuitBreakerScope, str] = {
    "entry": "신규 진입만",
    "reentry": "재진입 재무장만",
    "both": "둘 다",
}


def arms(
    *,
    thresholds: Sequence[float] = THRESHOLDS,
    scopes: Sequence[CircuitBreakerScope] = ("entry", "reentry", "both"),
    stop_limits: Sequence[int] = STOP_LIMITS,
) -> list[Arm]:
    """기준 팔 + 축 A(문턱 × 범위) + 축 B(대조군).

    🚨 **범위 셋을 반드시 가른다** — 신규 진입과 재진입 재무장을 함께 흔들면 「어느 쪽이
    얼마를 움직였나」를 못 가른다(WAN-394: 세 축을 함께 흔들면 이득의 92%가 상호작용으로
    사라진다). 재진입 축이 여기 있는 이유는 WAN-408이 낸 사실이다 — 재진입의 손실이
    사실상 전부 폭락일에 있다(재진입 비중이 최악 1일 35.3% vs 평균 10.7%).
    """
    out = [BASE_ARM]
    for scope in scopes:
        for threshold in thresholds:
            out.append(
                Arm(
                    name=f"loss{threshold:+.0f}R_{scope}",
                    label=f"하루 {threshold:+.0f}R · {_SCOPE_LABEL[scope]}",
                    kind="loss",
                    threshold=threshold,
                    scope=scope,
                )
            )
    for limit in stop_limits:
        out.append(
            Arm(
                name=f"stops{limit}_both",
                label=f"손절 {limit}건 · 둘 다 (대조군 B)",
                kind="stops",
                stop_limit=limit,
                scope="both",
            )
        )
    return out


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
    """무거운 패스는 **여기 한 번**이다 — 이 모듈이 흔드는 축은 **후보를 안 바꾼다**.

    서킷브레이커는 `run_leverage_book`의 **배치** 축이라 `_Task`에 아예 없다 — 가드·재진입
    배치·복리와 같은 부류다(WAN-394 §0). 그래서 팔 33개가 이 payload **하나**를 나눠 쓴다.

    인자는 `book_cli.run_book_segments`가 `run_cells`에 넘기는 것과 같다(핀 없음, WAN-305).
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
    compound: bool = False,
    daily_loss_limit_r: float | None = None,
    daily_stop_limit: int | None = None,
    circuit_breaker_scope: CircuitBreakerScope = "both",
    blocked_from_by_day: Mapping[str, int] | None = None,
) -> list[BookSegment]:
    """채택 북 배치 + (옵트인) 서킷브레이커.

    `compound=False`(기본)가 이 격자의 판이다 — 판정 자가 거래당 net R이고 복리 총수익은
    이 좌표에서 포화한다(WAN-346 §2 · WAN-388). 검산만 복리를 켜 **인자 없는 채택 북**과
    대조한다. `breaker`가 비면 예전과 **비트 단위로 같다**.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        daily_loss_limit_r=daily_loss_limit_r,
        daily_stop_limit=daily_stop_limit,
        circuit_breaker_scope=circuit_breaker_scope,
        blocked_from_by_day=blocked_from_by_day,
    )


def on_adopted_coordinates(symbols: Sequence[str], timeframes: Sequence[str]) -> bool:
    """이 실행이 **채택 좌표**를 도는가 — 아니면 공개값 대조 검산은 성립하지 않는다.

    🚨 좁혀 돈 판을 공개 채택 북과 대조하면 **좌표 차이가 배선 오류처럼 보인다**(WAN-381이
    파일럿에서 `5.63e+04`를 보고 못 박은 자리). 성립 안 하는 검산은 **실패로 찍지 않고
    「건너뜀」으로** 찍는다 — 「안 걸었다」와 「걸었는데 틀렸다」는 다른 말이다.
    """
    return list(symbols) == list(harness.DEFAULT_SYMBOLS) and list(timeframes) == list(
        harness.DEFAULT_TIMEFRAMES
    )


# --------------------------------------------------------------------------- #
# §1-0 정의를 못 박는다 — 두 규약을 **나란히** 낸다
# --------------------------------------------------------------------------- #

#: (b) 「그 순간 열린 칸 수」 규약.
OPEN_CONVENTIONS: tuple[str, ...] = ("wan408", "interval")

#: (c) 「그날 실현 net R 누적」 규약.
REALIZED_CONVENTIONS: tuple[str, ...] = ("strict", "settled")


def _zero_duration_times(facts: Sequence[TradeFact]) -> list[int]:
    """`진입 == 청산`인 거래의 시각(정렬) — 두 규약의 차를 **정확히** 만드는 집합이다.

    실재하는 부류다: 「같은 분 익절」(WAN-336)은 진입한 그 1분 스텝에 청산돼 두 시각이
    같아진다(채택 북 `oos_warm` 거래의 7.37% · 순손익의 48%).
    """
    return sorted(f.entry_time for f in facts if f.exit_time == f.entry_time)


def open_cells_before(facts: Sequence[TradeFact], *, convention: str = "interval") -> list[int]:
    """각 거래의 진입 순간에 **열려 있던 칸 수** — 규약을 명시로 받는다.

    * `"wan408"` — `max(0, #(진입 < t) − #(청산 ≤ t))`. 공개 `wan408_leading.csv`가 쓴 식.
    * `"interval"` — `#{진입 < t < 청산}`. 「그 순간 실제로 열려 있는 포지션」이고, 칸당
      1포지션이라 **곧 열린 칸 수**다.

    🚨 **두 값의 차는 정확히 `#{진입 == 청산 == t}`다**(대수 항등):
    `#(청산 ≤ t) − #(진입 < t ∧ 청산 ≤ t) = #{진입 ≥ t ∧ 청산 ≤ t}`이고 `청산 ≥ 진입`이라
    그 집합은 `진입 == 청산 == t`뿐이다. 즉 `wan408` 식은 **길이 0인 거래를 자기 자신의
    열린 칸에서 빼고** 있었다 — 그리고 그 부류가 하필 「같은 분 익절」이다.
    """
    if convention not in OPEN_CONVENTIONS:
        raise ValueError(f"모르는 규약입니다: {convention!r} (가능: {OPEN_CONVENTIONS})")
    entries = sorted(f.entry_time for f in facts)
    exits = sorted(f.exit_time for f in facts)
    zero = _zero_duration_times(facts)
    out: list[int] = []
    for fact in facts:
        t = fact.entry_time
        raw = bisect_left(entries, t) - bisect_right(exits, t)
        if convention == "interval":
            raw += bisect_right(zero, t) - bisect_left(zero, t)
        out.append(max(0, raw))
    return out


def realized_r_before(facts: Sequence[TradeFact], *, convention: str = "settled") -> list[float]:
    """각 거래의 진입 순간에 **그날 이미 실현된 net R 누계** — 경계를 명시로 받는다.

    * `"strict"` — `청산 < t`(WAN-408 §0-3의 `bisect_left`).
    * `"settled"` — `청산 ≤ t`(**북이 실제로 아는 것**: `settle_due(t)`의 경계).

    하루는 **KST 날짜**(WAN-172)이고, 누계에 담기는 것은 **그 거래의 진입일과 같은 KST 날**에
    청산된 거래뿐이다(자정에 리셋 = 「하루 한도」의 뜻).
    """
    if convention not in REALIZED_CONVENTIONS:
        raise ValueError(f"모르는 규약입니다: {convention!r} (가능: {REALIZED_CONVENTIONS})")
    by_day: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for fact in facts:
        by_day[kst_day_key(fact.exit_time)].append((fact.exit_time, fact.net_r))
    prefix: dict[str, tuple[list[int], list[float]]] = {}
    for day, rows in by_day.items():
        rows.sort()
        times = [r[0] for r in rows]
        cumulative = [0.0]
        for _, value in rows:
            cumulative.append(cumulative[-1] + value)
        prefix[day] = (times, cumulative)

    out: list[float] = []
    for fact in facts:
        times, cumulative = prefix.get(kst_day_key(fact.entry_time), ([], [0.0]))
        cut = (
            bisect_left(times, fact.entry_time)
            if convention == "strict"
            else bisect_right(times, fact.entry_time)
        )
        out.append(cumulative[cut])
    return out


class _CsvRow(BaseModel):
    """CSV 왕복에서 **`None`이 `NaN`으로 되살아나는 것**을 모델에서 한 번 되돌린다.

    🚨 pandas는 빈 칸을 `NaN`으로 읽는다 — `float | None` 필드는 그것을 **유효한 float으로
    받아** 표에 `nan`이 찍히고(WAN-395 §부수 수리가 겪은 그 함정) `int | None` 필드는 아예
    죽는다. 되돌리는 자리는 **모델 하나**여야 한다: 읽는 경로마다 고치면 한 곳을 빠뜨린다.

    ⚠️ **`None`을 허용하는 필드만** 되돌린다 — `share_of_total_net_r`처럼 **`NaN`이 뜻을
    갖는**(총합이 음수가 아니라 비율이 정의되지 않는다) 필드는 그대로 둔다.
    """

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _restore_none(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name, field in cls.model_fields.items():
            value = out.get(name)
            if isinstance(value, float) and value != value and _allows_none(field.annotation):
                out[name] = None
        return out


def _allows_none(annotation: object) -> bool:
    """그 필드가 `None`을 받는가 — `X | None`의 유니온 인자에 `NoneType`이 있으면 참."""
    return type(None) in get_args(annotation)


class DefinitionRow(_CsvRow):
    """§1-0 한 줄 — (구간, 지표, 규약, 버킷)의 사후 성적.

    🚨 **관측이지 반사실이 아니다** — 「그 문턱에서 막았다면」의 손익은 막힌 거래가 비운
    자본·슬롯을 다른 칸이 쓰기 때문에 이 표에서 읽을 수 없다(§1-1이 그걸 잰다).
    """

    model_config = ConfigDict(frozen=True)

    segment: str
    indicator: str
    convention: str
    bucket: str
    order: int
    num_trades: int
    share_of_trades: float
    win_rate: float
    mean_net_r: float
    sum_net_r: float
    share_of_total_net_r: float
    """이 버킷의 net R 합 ÷ 구간 전체 net R 합. 🚨 **총합이 음수일 때만** 뜻이 있다."""
    total_trades: int
    """🚨 버킷 합이 이 값과 같아야 한다 — 이슈가 지적한 「1,597건이 샜다」를 막는 열이다."""
    zero_duration_trades: int
    """`진입 == 청산`인 거래 수 — 두 (b) 규약의 차를 만드는 그 집합의 크기."""


def definition_rows(facts: Sequence[TradeFact], *, segment: str) -> list[DefinitionRow]:
    """(b)·(c)를 **네 규약 전부**로 낸다 — 「누가 맞나」를 논하지 않고 숫자를 나란히 놓는다."""
    if not facts:
        return []
    total_net = sum(f.net_r for f in facts)
    total = len(facts)
    zero = len(_zero_duration_times(facts))

    labelled: list[tuple[str, str, list[tuple[str, int]]]] = []
    for convention in OPEN_CONVENTIONS:
        values = open_cells_before(facts, convention=convention)
        labelled.append(
            (
                "open_cells_before",
                convention,
                [
                    (_bucket_label_int(v, OPEN_BUCKETS), _bucket_order_int(v, OPEN_BUCKETS))
                    for v in values
                ],
            )
        )
    for convention in REALIZED_CONVENTIONS:
        realized = realized_r_before(facts, convention=convention)
        labelled.append(
            (
                "realized_net_r_today_before",
                convention,
                [
                    (
                        _bucket_label_realized(v, REALIZED_BUCKETS),
                        _bucket_order_realized(v, REALIZED_BUCKETS),
                    )
                    for v in realized
                ],
            )
        )

    rows: list[DefinitionRow] = []
    for indicator, convention, labels in labelled:
        groups: dict[tuple[str, int], list[TradeFact]] = defaultdict(list)
        for fact, (label, order) in zip(facts, labels, strict=True):
            groups[(label, order)].append(fact)
        for (bucket, order), group in sorted(groups.items(), key=lambda kv: kv[0][1]):
            bucket_net = sum(f.net_r for f in group)
            rows.append(
                DefinitionRow(
                    segment=segment,
                    indicator=indicator,
                    convention=convention,
                    bucket=bucket,
                    order=order,
                    num_trades=len(group),
                    share_of_trades=len(group) / total,
                    win_rate=sum(1 for f in group if f.net_r > 0) / len(group),
                    mean_net_r=bucket_net / len(group),
                    sum_net_r=bucket_net,
                    share_of_total_net_r=(
                        bucket_net / total_net if total_net < 0 else float("nan")
                    ),
                    total_trades=total,
                    zero_duration_trades=zero,
                )
            )
    return rows


def worst_bucket(
    rows: Sequence[DefinitionRow],
    *,
    indicator: str,
    convention: str,
    min_trades: int = MIN_BUCKET_TRADES,
) -> str | None:
    """그 (지표, 규약)에서 **거래당 net R이 가장 나쁜** 버킷 — §1-0이 가르는 그 부호.

    🚨 **얇은 버킷은 후보에서 뺀다**(기본 `min_trades`) — 안 그러면 **거래 한 건**이
    「가장 나쁜 버킷」을 정한다(4h만 좁혀 돌린 연기 시험에서 실제로 n=1 버킷이 argmin으로
    올라왔다). 이 저장소가 표본 미달 셀을 판정에서 빼 온 그 자와 같은 20건이다(WAN-84 이래).
    전부 얇으면 **판정하지 않는다**(`None` — 지어내지 않는다).
    """
    scoped = [
        r
        for r in rows
        if r.indicator == indicator and r.convention == convention and r.num_trades >= min_trades
    ]
    return min(scoped, key=lambda r: r.mean_net_r).bucket if scoped else None


# --------------------------------------------------------------------------- #
# §1-1 격자 — 북에서 다시 배치한다
# --------------------------------------------------------------------------- #


class GridRow(_CsvRow):
    """한 (팔, 구간)의 북 집계. 북은 한 지갑이라 심볼 열이 없다(WAN-341)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    label: str
    kind: str
    threshold: float | None
    stop_limit: int | None
    scope: str
    segment: str
    num_cells: int
    num_symbols: int
    # ── 자 1: 남은 거래의 질 ────────────────────────────────────────────────
    num_trades: int
    """🚨 net R 옆에 **항상** 병기한다 — 「덜 매매해서 좋아 보이는 것」과 구분(WAN-378)."""
    win_rate: float
    mean_net_r: float
    """판정 자 하나 — 실현손익 ÷ 그 거래의 리스크 금액(`book_cli.net_r`와 같은 자)."""
    # ── 자 2: 계좌가 실제로 얼마 잃었나 ─────────────────────────────────────
    sum_net_r: float
    """🚨 거래를 줄이면 **당연히** 줄어드는 자다 — 거래당 net R과 **함께만** 읽는다."""
    # ── 자 3: 낙폭(이 축의 존재 이유) ───────────────────────────────────────
    total_return_flat: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    liquidation_events: int
    wallet_defined: bool
    """지갑 층 열(총수익·MDD·수익/MDD·동시 리스크·청산)이 뜻을 갖는가(WAN-386/388 술어).

    🚨 거짓이면 그 열을 **비율로 읽지 않는다** — 자본이 0을 뚫으면 「자본 대비 비율」이
    정의를 잃는다. **어느 문턱이 지갑을 되살리는가**가 이 축의 첫 관문이다."""
    # ── 자 4: 실용성 ────────────────────────────────────────────────────────
    trip_days: int
    """서킷브레이커가 **발동한** KST 하루 수."""
    trading_days: int
    """기준 팔이 **진입을 한** KST 하루 수 — `trip_days`의 분모."""
    trip_day_share: float
    blocked_candidates: int
    """막힌 후보 수(`skipped_circuit_breaker`)."""
    blocked_share_of_base: float
    """막힌 후보 ÷ 기준 팔 거래 수 — 「얼마나 쉬나」의 크기."""
    # ── 자 5: 선별인가 비용인가 (WAN-370 분해) ──────────────────────────────
    gross_r: float
    cost_r: float
    slippage_r: float
    entry_fee_r: float
    take_profit_fee_r: float
    stop_fee_r: float
    other_fee_r: float
    funding_r: float
    identity_max_abs: float
    """`gross − 비용합 − net`의 최대 절댓값 — 분해가 닫히는지(0이어야 한다)."""
    stop_width_p50: float
    reentry_trades: int
    symbols_below_gate: int
    min_symbol_trades: int


class LooRow(GridRow):
    """종목 하나를 빼고 **지갑을 다시 배치**한 행 (WAN-316 스코프 패턴)."""

    exclude: str


class NullRow(_CsvRow):
    """§2겹 매칭 대조군 한 줄 — 「같은 만큼 쉬되 손실과 무관하게」 쉬는 팔."""

    model_config = ConfigDict(frozen=True)

    segment: str
    threshold: float
    scope: str
    seed: int
    """`-1`이면 **실제 팔**(대조군이 아니라 기준점)."""
    is_actual: bool
    num_trades: int
    mean_net_r: float
    sum_net_r: float
    win_rate: float
    max_drawdown: float
    blocked_candidates: int
    trip_days: int


class ChecksumRow(_CsvRow):
    model_config = ConfigDict(frozen=True)

    check: str
    arm: str
    segment: str
    metric: str
    left: float
    right: float
    abs_diff: float


def wallet_defined(total_return_flat: float, max_drawdown: float) -> bool:
    """이 행의 **지갑 층** 열이 뜻을 갖는가 (WAN-386/388과 **같은 술어**).

    복리를 끈 판에서 자본이 0을 뚫으면(총수익 ≤ −100%) 「자본 대비 비율」은 분모가 부호를
    바꿔 무의미해진다 — 그때는 **비율을 내지 않고 「정의 상실」로 찍는다**(WAN-115 관행).
    ⚠️ 거래당 net R·gross·비용·승률은 이 함정에 안 걸린다(분모가 리스크 금액이라 잔고와
    무관하다) — 그래서 판정 자가 처음부터 그것이다.
    """
    return total_return_flat > -1.0 and max_drawdown < 1.0


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _p50(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _cfg() -> BacktestConfig:
    """비용 분해가 쓰는 설정 — 🚨 배치와 **같은** 익절 청산 유동성이라야 항등식이 닫힌다."""
    return harness.build_config(
        harness.DEFAULT_TIMEFRAMES[0],
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def trading_day_count(segment: BookSegment) -> int:
    """이 구간에서 **진입이 있었던** KST 하루 수 — 발동일 비중의 분모."""
    return len({kst_day_key(t.entry_time) for t in segment.outcome.trades})


def trip_schedule(segment: BookSegment) -> dict[str, int]:
    """발동한 KST 하루 → **처음 막은 시각**(ms). §2겹 대조군의 입력이다.

    ⚠️ **「문턱을 넘은 시각」이 아니라 「처음 막은 시각」이다** — 문턱을 넘은 뒤 후보가 한참
    없으면 그 사이는 안 세어진다. 대조군이 맞춰야 하는 것이 *「하루 중 언제부터 매매가
    끊겼나」*라 이쪽이 맞는 자다(발동해도 막을 것이 없으면 그 하루는 아무 일도 안 일어난다).
    """
    return dict(segment.outcome.stats.circuit_breaker_first_trip)


def grid_row(
    arm: Arm,
    segment: BookSegment,
    *,
    base: BookSegment,
    num_symbols: int,
    cfg: BacktestConfig,
) -> GridRow:
    """한 (팔, 구간)의 행 — 자 다섯을 **한 줄에** 싣는다(하나만 보면 틀린다)."""
    row = segment.row
    pairs = segment.trades_with_placements()
    stats = segment.outcome.stats

    counts: dict[str, int] = {}
    nets: list[float] = []
    gross: list[float] = []
    slip: list[float] = []
    entry_fee: list[float] = []
    tp_fee: list[float] = []
    stop_fee: list[float] = []
    other_fee: list[float] = []
    funding: list[float] = []
    cost: list[float] = []
    widths: list[float] = []
    identity = 0.0
    reentries = 0
    for trade, placement in pairs:
        counts[placement.cell[0]] = counts.get(placement.cell[0], 0) + 1
        if placement.is_reentry:
            reentries += 1
        risk = placement.risk_amount
        if risk <= 0:
            continue
        parts = decompose_trade(trade, cfg)
        nets.append(net_r(trade, placement))
        gross.append(parts.gross / risk)
        slip.append(parts.slippage / risk)
        entry_fee.append(parts.entry_fee / risk)
        tp_fee.append(parts.take_profit_fee / risk)
        stop_fee.append(parts.stop_fee / risk)
        other_fee.append(parts.other_fee / risk)
        funding.append(parts.funding / risk)
        cost.append(parts.total_cost / risk)
        identity = max(identity, abs(parts.residual))
        widths.append(stop_width_fraction(trade, placement))

    per_symbol = list(counts.values()) or [0]
    missing = max(0, num_symbols - len(counts))
    days = trading_day_count(base)
    trips = len(stats.circuit_breaker_days)
    base_trades = base.row.num_trades
    return GridRow(
        arm=arm.name,
        label=arm.label,
        kind=arm.kind,
        threshold=arm.threshold,
        stop_limit=arm.stop_limit,
        scope=arm.scope,
        segment=segment.segment,
        num_cells=row.num_cells,
        num_symbols=num_symbols,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        mean_net_r=_mean(nets),
        sum_net_r=sum(nets),
        total_return_flat=row.total_return,
        max_drawdown=row.max_drawdown,
        return_over_mdd=(row.total_return / row.max_drawdown if row.max_drawdown else None),
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=stats.max_concurrent_risk_ratio,
        liquidation_events=row.liquidation_events,
        wallet_defined=wallet_defined(row.total_return, row.max_drawdown),
        trip_days=trips,
        trading_days=days,
        trip_day_share=trips / days if days else 0.0,
        blocked_candidates=stats.skipped_circuit_breaker,
        blocked_share_of_base=(stats.skipped_circuit_breaker / base_trades if base_trades else 0.0),
        gross_r=_mean(gross),
        cost_r=_mean(cost),
        slippage_r=_mean(slip),
        entry_fee_r=_mean(entry_fee),
        take_profit_fee_r=_mean(tp_fee),
        stop_fee_r=_mean(stop_fee),
        other_fee_r=_mean(other_fee),
        funding_r=_mean(funding),
        identity_max_abs=identity,
        stop_width_p50=_p50(widths),
        reentry_trades=reentries,
        symbols_below_gate=sum(1 for n in per_symbol if n < MIN_TRADES_PER_SYMBOL) + missing,
        min_symbol_trades=0 if missing else min(per_symbol),
    )


def grid_rows(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    grid: Sequence[Arm],
    log: bool = True,
) -> tuple[list[GridRow], dict[tuple[str, str], BookSegment]]:
    """격자 전부 — 팔마다 **지갑을 처음부터 다시 배치**한다(라벨 필터가 아니다).

    반환의 둘째는 `(팔, 구간) → BookSegment`다 — §2겹 대조군이 실제 팔의 발동 스케줄을
    읽고, 검산 (c)가 기준 팔과 거래를 대조하는 데 쓴다.
    """
    cfg = _cfg()
    num_symbols = len({p.symbol for p in payloads})
    placed: dict[tuple[str, str], BookSegment] = {}

    base_segments = place(
        payloads, start_ms=start_ms, end_ms=end_ms, segments=list(segments), **BASE_ARM.kwargs()
    )
    base_by_segment = {seg.segment: seg for seg in base_segments}
    for seg in base_segments:
        placed[(BASE_ARM.name, seg.segment)] = seg

    rows: list[GridRow] = []
    for arm in grid:
        segs = (
            base_segments
            if arm.is_base
            else place(
                payloads,
                start_ms=start_ms,
                end_ms=end_ms,
                segments=list(segments),
                **arm.kwargs(),
            )
        )
        for seg in segs:
            placed[(arm.name, seg.segment)] = seg
            rows.append(
                grid_row(
                    arm,
                    seg,
                    base=base_by_segment[seg.segment],
                    num_symbols=num_symbols,
                    cfg=cfg,
                )
            )
        if log:
            main = next((s for s in segs if s.segment == PRIMARY_SEGMENT), segs[0])
            stats = main.outcome.stats
            print(
                f"[wan410] {arm.name:<22} {main.segment}: 거래 {main.row.num_trades:>6} · "
                f"차단 {stats.skipped_circuit_breaker:>5} · 발동일 "
                f"{len(stats.circuit_breaker_days):>4}",
                flush=True,
            )
    return rows, placed


# --------------------------------------------------------------------------- #
# §2겹 매칭 대조군 — 「덜 매매한 몫」과 「손실 뒤를 피한 몫」을 가른다
# --------------------------------------------------------------------------- #

#: KST는 UTC+9 고정(서머타임 없음).
KST_OFFSET_MS = 9 * 3_600_000
_DAY_MS = 86_400_000


def day_start_ms(day: str) -> int:
    """KST 날짜 키(`YYYY-MM-DD`) → 그날 **KST 자정**의 epoch ms."""
    return int(pd.Timestamp(day, tz="Asia/Seoul").timestamp() * 1000)


def shuffled_schedule(
    schedule: Mapping[str, int], trading_days: Sequence[str], rng: random.Random
) -> dict[str, int]:
    """실제 발동 `(날짜, 하루 중 오프셋)`을 **다른 날로 재배정**한다.

    * **날짜 수가 같다** — 같은 만큼 쉰다.
    * **하루 중 시각 분포가 같다** — 「하루 중 언제부터 쉬나」를 보존한다(오프셋 다발을 통째로
      가져가되 날짜와의 짝만 무작위로 다시 맺는다).
    * **손실을 안 읽는다** — 그래서 이 팔이 이기면 개선은 *「덜 매매한 몫」*이다.

    🚨 **널이 항등으로 퇴화하지 않는지 호출부가 확인해야 한다**(WAN-408 §0-2가 「종목 안
    순열은 통계량을 안 바꾼다」를 몰라 헛돌린 선례) — 재배정된 날짜 집합이 원래와 같으면
    이 널은 아무것도 안 한 것이고, `null_rows`가 그 겹침을 세어 행에 남긴다.
    """
    offsets = [first - day_start_ms(day) for day, first in sorted(schedule.items())]
    if not offsets:
        return {}
    days = rng.sample(list(trading_days), min(len(offsets), len(trading_days)))
    rng.shuffle(offsets)
    return {day: day_start_ms(day) + off for day, off in zip(sorted(days), offsets, strict=False)}


def null_rows(
    payloads: Sequence[CellPayload],
    placed: Mapping[tuple[str, str], BookSegment],
    *,
    start_ms: int,
    end_ms: int,
    segment: str = PRIMARY_SEGMENT,
    thresholds: Sequence[float] = THRESHOLDS,
    scope: CircuitBreakerScope = "both",
    draws: int = NULL_DRAWS,
    seed: int = NULL_SEED,
    log: bool = True,
) -> list[NullRow]:
    """문턱마다 실제 팔 1행 + 대조군 `draws`행. 단측 순위 p는 `null_pvalue`가 낸다."""
    base = placed[(BASE_ARM.name, segment)]
    days = sorted({kst_day_key(t.entry_time) for t in base.outcome.trades})
    out: list[NullRow] = []
    for threshold in thresholds:
        arm_name = f"loss{threshold:+.0f}R_{scope}"
        actual = placed.get((arm_name, segment))
        if actual is None:
            continue
        out.append(_null_row(actual, segment=segment, threshold=threshold, scope=scope, seed=-1))
        schedule = trip_schedule(actual)
        if not schedule:
            if log:
                print(f"[wan410-null] {arm_name}: 발동 0일 — 대조군을 만들 것이 없다", flush=True)
            continue
        rng = random.Random(f"{seed}:{threshold}:{scope}")
        for draw in range(draws):
            shuffled = shuffled_schedule(schedule, days, rng)
            seg = place(
                payloads,
                start_ms=start_ms,
                end_ms=end_ms,
                segments=[segment],
                blocked_from_by_day=shuffled,
                circuit_breaker_scope=scope,
            )[0]
            out.append(_null_row(seg, segment=segment, threshold=threshold, scope=scope, seed=draw))
        if log:
            print(f"[wan410-null] {arm_name}: 대조군 {draws}판 완료", flush=True)
    return out


def _null_row(
    segment_result: BookSegment, *, segment: str, threshold: float, scope: str, seed: int
) -> NullRow:
    pairs = segment_result.trades_with_placements()
    nets = [net_r(t, p) for t, p in pairs if p.risk_amount > 0]
    stats = segment_result.outcome.stats
    return NullRow(
        segment=segment,
        threshold=threshold,
        scope=scope,
        seed=seed,
        is_actual=seed < 0,
        num_trades=segment_result.row.num_trades,
        mean_net_r=_mean(nets),
        sum_net_r=sum(nets),
        win_rate=segment_result.row.win_rate,
        max_drawdown=segment_result.row.max_drawdown,
        blocked_candidates=stats.skipped_circuit_breaker,
        trip_days=len(stats.circuit_breaker_days),
    )


def null_pvalue(rows: Sequence[NullRow], *, threshold: float, scope: str) -> tuple[float, int]:
    """단측 순위 p와 대조군 수 — `(#{대조군 ≥ 실제} + 1) / (판 수 + 1)` (WAN-142와 같은 자).

    🚨 **작을수록 「손실 뒤라는 조건이 정보를 갖는다」**이고, 크면 개선은 「덜 매매한 몫」이다.
    실제 팔이 없거나 대조군이 0판이면 `(nan, 0)`을 낸다(지어내지 않는다).
    """
    scoped = [r for r in rows if r.threshold == threshold and r.scope == scope]
    actual = next((r for r in scoped if r.is_actual), None)
    draws = [r for r in scoped if not r.is_actual]
    if actual is None or not draws:
        return float("nan"), len(draws)
    beats = sum(1 for r in draws if r.mean_net_r >= actual.mean_net_r)
    return (beats + 1) / (len(draws) + 1), len(draws)


# --------------------------------------------------------------------------- #
# leave-one-out — 종목을 빼고 **지갑을 다시 배치**한다
# --------------------------------------------------------------------------- #


def loo_rows(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segment: str,
    grid: Sequence[Arm],
    log: bool = True,
) -> list[LooRow]:
    """종목 하나씩 빼고 **처음부터 다시 배치**한다 — 라벨 필터가 아니다(WAN-316).

    📌 배치는 사실상 공짜다(WAN-394 §1 실측: 배치·LOO가 청구서의 2%) — WAN-389가
    「짐작이 틀렸고 표준 관문이 빠졌다」고 스스로 밝힌 자리라 처음부터 넣는다.
    """
    cfg = _cfg()
    symbols = sorted({p.symbol for p in payloads})
    out: list[LooRow] = []
    for excluded in symbols:
        kept = [p for p in payloads if p.symbol != excluded]
        base_seg = place(
            kept, start_ms=start_ms, end_ms=end_ms, segments=[segment], **BASE_ARM.kwargs()
        )[0]
        for arm in grid:
            seg = (
                base_seg
                if arm.is_base
                else place(
                    kept,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    segments=[segment],
                    **arm.kwargs(),
                )[0]
            )
            row = grid_row(
                arm, seg, base=base_seg, num_symbols=len(symbols) - 1, cfg=cfg
            ).model_dump()
            out.append(LooRow(**row, exclude=excluded))
        if log:
            print(f"[wan410-loo] −{excluded} 완료", flush=True)
    return out


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def check_adopted_identity(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
) -> list[ChecksumRow]:
    """검산 (a) — 「멈춤 없음」 팔 ≡ **인자 없는 채택 북**.

    🚨 복리를 **켜고** 대조한다 — 인자 없는 채택 북이 복리로 돌기 때문이다(WAN-346).
    이 등식이 서야 이 표의 기준선이 「채택 북이 실제로 한 매매」다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    mine = place(payloads, start_ms=start_ms, end_ms=end_ms, segments=list(segments), compound=True)
    theirs = iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        compound_sizing=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )
    right = {seg.segment: seg for seg in theirs}
    out: list[ChecksumRow] = []
    for seg in mine:
        other = right.get(seg.segment)
        if other is None:
            continue
        for metric in _CHECK_METRICS:
            lhs = float(getattr(seg.row, metric))
            rhs = float(getattr(other.row, metric))
            out.append(
                ChecksumRow(
                    check="a 멈춤 없음 ≡ 채택 북",
                    arm=BASE_ARM.name,
                    segment=seg.segment,
                    metric=metric,
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return out


def check_breaker_bound(
    placed: Mapping[tuple[str, str], BookSegment], grid: Sequence[Arm]
) -> list[ChecksumRow]:
    """검산 (b) — 팔이 **라벨이 아니라 동작으로** 걸렸다.

    발동 시각보다 **엄격히 뒤에** 진입한 거래가 하나라도 남아 있으면 그 팔은 이름만
    서킷브레이커이고 조용히 기준 팔로 돈 것이다(WAN-388 검산 (c)와 같은 부류).

    🚨 **왜 「같은 ms」는 빼는가 — 손실을 만든 그 거래 자신은 자기 손실로 막힐 수 없다.**
    북은 후보를 시각순으로 훑으며 후보마다 `settle_due(t)`로 그때까지의 청산을 반영한다.
    그런데 **진입과 청산이 같은 1분인 거래**(「같은 분 익절/손절」 — WAN-336)는 그 자신이
    배치된 **뒤에야** 정산되므로, 그 거래가 평가되는 순간 그 손익은 아직 없다. 그것을 막으려면
    **같은 ms 안에서 나중에 일어날 일을 미리 알아야** 한다 — 룩어헤드다(WAN-364가 6년치 표를
    얼린 그 부류). ⚠️ **같은 ms라도 그 정산 뒤에 평가되는 후보는 막힌다**(회귀 테스트가
    양쪽으로 고정) — 남는 것은 그 경계에 걸린 **한 칸**이다.

    그래서 이 검산은 **엄격히 뒤(`>`)만** 위반으로 세고, 같은 ms 진입은 **따로 관측만** 한다
    (`same_instant_entries`). 실측(4h 연기 시험)에서 그 수는 팔당 0~2건이다. ⚠️ 이것을
    「작으니 괜찮다」가 아니라 **「1분 해상도가 만드는 경계이고 크기는 이만큼」**으로 읽는다.
    """
    by_name = {arm.name: arm for arm in grid}
    out: list[ChecksumRow] = []
    for (arm_name, segment), seg in sorted(placed.items()):
        arm = by_name.get(arm_name)
        if arm is None or arm.is_base:
            continue
        first = seg.outcome.stats.circuit_breaker_first_trip
        leaked = same_instant = 0
        for trade, placement in seg.trades_with_placements():
            trip = first.get(kst_day_key(trade.entry_time))
            if trip is None or trade.entry_time < trip or not arm.in_scope(placement.is_reentry):
                continue
            if trade.entry_time > trip:
                leaked += 1
            else:
                same_instant += 1
        out.append(
            ChecksumRow(
                check="b 발동보다 뒤의 진입이 실제로 0건",
                arm=arm_name,
                segment=segment,
                metric="leaked_entries",
                left=float(leaked),
                right=0.0,
                abs_diff=float(leaked),
            )
        )
        out.append(
            ChecksumRow(
                check="b′ 같은 ms 진입(관측 · 1분 해상도의 경계)",
                arm=arm_name,
                segment=segment,
                metric="same_instant_entries",
                left=float(same_instant),
                right=float(same_instant),
                abs_diff=0.0,
            )
        )
    return out


def check_open_positions_untouched(
    placed: Mapping[tuple[str, str], BookSegment], grid: Sequence[Arm], *, segment: str
) -> list[ChecksumRow]:
    """검산 (c) — **이미 열린 포지션은 안 건드린다**.

    첫 발동보다 **먼저 진입한** 거래는 서킷브레이커와 무관하게 원래대로 끝나야 한다. 두 팔의
    거래를 `(칸, 진입 시각, 진입가)`로 짝지어, 짝이 지어진 거래의 **청산 시각**이 다르면
    이 팔은 청산 규칙까지 건드린 것이다.

    ⚠️ 짝이 **안 지어지는** 거래는 정상이다 — 진입을 막으면 자본·슬롯이 비어 다른 칸이 쓰고
    사이징 자본도 달라진다(그것이 §1겹의 측정 대상이다). 이 검산이 보는 것은 **짝이 지어진
    거래의 청산이 같은가** 하나다.
    """
    base = placed.get((BASE_ARM.name, segment))
    if base is None:
        return []
    base_exits = {
        (p.cell, t.entry_time, t.entry_price): t.exits[-1].time
        for t, p in base.trades_with_placements()
    }
    out: list[ChecksumRow] = []
    for arm in grid:
        seg = placed.get((arm.name, segment))
        if seg is None or arm.is_base:
            continue
        first = seg.outcome.stats.circuit_breaker_first_trip
        mismatched = matched = 0
        for trade, placement in seg.trades_with_placements():
            trip = first.get(kst_day_key(trade.entry_time))
            if trip is not None and trade.entry_time >= trip:
                continue
            key = (placement.cell, trade.entry_time, trade.entry_price)
            other = base_exits.get(key)
            if other is None:
                continue
            matched += 1
            mismatched += 1 if other != trade.exits[-1].time else 0
        # 🚨 짝 수는 **열**로 싣는다 — 검산 **이름**에 넣으면 팔마다 다른 이름이 되어
        # 요약이 33줄로 불어나고 「같은 검산인가」가 안 읽힌다.
        out.append(
            ChecksumRow(
                check="c 먼저 열린 포지션 청산 불변",
                arm=arm.name,
                segment=segment,
                metric=f"exit_time_mismatch(짝 {matched})",
                left=float(mismatched),
                right=0.0,
                abs_diff=float(mismatched),
            )
        )
    return out


def check_candidate_layer(
    payloads: Sequence[CellPayload], placed: Mapping[tuple[str, str], BookSegment]
) -> list[ChecksumRow]:
    """검산 (d) — 이 PR의 엔진 편집은 **배치 축**이다(후보를 안 바꾼다).

    팔이 33개인데 후보는 **하나**를 나눠 쓴다 — 서킷브레이커가 `_Task`에 아예 없기 때문이다
    (WAN-394 §0 「payload를 바꾸는 것은 전부 `_Task`에 있다」의 역). 이 줄은 그 사실을 후보
    수로 남긴다(팔마다 다시 생성했다면 이 값이 팔 사이에서 갈릴 수 있다).
    """
    total = sum(len(p.candidates.get(harness.SEGMENT_FULL, ())) for p in payloads)
    reentry = sum(len(p.reentry_candidates.get(harness.SEGMENT_FULL, ())) for p in payloads)
    arms_seen = len({name for name, _ in placed})
    return [
        ChecksumRow(
            check="d 후보 층은 팔에 무관(배치 축)",
            arm=f"{arms_seen}팔",
            segment=harness.SEGMENT_FULL,
            metric="base+reentry_candidates",
            left=float(total + reentry),
            right=float(total + reentry),
            abs_diff=0.0,
        )
    ]


# --------------------------------------------------------------------------- #
# 요약 렌더
# --------------------------------------------------------------------------- #


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None or value != value:  # noqa: PLR0124 - NaN
        return "—"
    return f"{value:+.{digits}f}"


def _pct(value: float | None, digits: int = 1) -> str:
    if value is None or value != value:  # noqa: PLR0124 - NaN
        return "—"
    return f"{value * 100:.{digits}f}%"


def _wallet_cell(row: GridRow, value: float | None, *, percent: bool = True) -> str:
    """지갑 층 열 — 🚨 정의를 잃었으면 **비율을 내지 않는다**(WAN-386/388 관행)."""
    if not row.wallet_defined:
        return "정의 상실"
    return _pct(value) if percent else _fmt(value, 2)


def _segment_rows(rows: Sequence[GridRow], segment: str) -> list[GridRow]:
    return [r for r in rows if r.segment == segment]


def _base_row(rows: Sequence[GridRow], segment: str) -> GridRow | None:
    return next((r for r in _segment_rows(rows, segment) if r.kind == "base"), None)


def _sign_note(value: float) -> str:
    """부호를 **말로** 찍는다 — 「양수가 나왔다」로 읽히지 않게 잡음선을 함께 본다."""
    if value < -NOISE_R:
        return "**여전히 음수**다"
    if value > NOISE_R:
        return "양수이되 이 표는 채택을 권고하지 않는다"
    return "**0과 구분되지 않는다**"


def argmax_arm(rows: Sequence[GridRow], segment: str, *, kind: str = "loss") -> GridRow | None:
    """그 구간에서 거래당 net R이 가장 높은 팔. 🚨 **채택 근거가 아니다**(WAN-161)."""
    scoped = [r for r in _segment_rows(rows, segment) if r.kind == kind]
    return max(scoped, key=lambda r: r.mean_net_r) if scoped else None


def is_to_oos_flips(rows: Sequence[GridRow], *, scope: str) -> tuple[str | None, str | None]:
    """앞구간(`is`)과 뒷구간(`oos_warm`)이 **같은 문턱을 고르는가** — 뒤집힘을 센다."""

    def best(segment: str) -> str | None:
        scoped = [r for r in _segment_rows(rows, segment) if r.kind == "loss" and r.scope == scope]
        return max(scoped, key=lambda r: r.mean_net_r).arm if scoped else None

    return best(harness.SEGMENT_IS), best(PRIMARY_SEGMENT)


def build_summary_markdown(
    defs: Sequence[DefinitionRow],
    rows: Sequence[GridRow],
    nulls: Sequence[NullRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    *,
    elapsed: float | None = None,
) -> str:
    out: list[str] = []
    out.append("# WAN-410 — 하루 손실 서킷브레이커 (채택 북)")
    out.append("")
    out.append(
        "🚨 **이 표는 채택 권고를 내지 않는다 — 재기만 한다**(사용자 결정 2026-09-09 "
        "「일단은 재기만 하자」). 문턱 선정 기준을 착수 전에 못 박지 않았으므로 "
        "**「―R이 최적」·「이 문턱을 권고한다」를 쓰지 않는다**. argmax는 표에 찍되 "
        "**채택 근거가 아니다**(WAN-161). 「조일수록 좋다」는 예상된 결과이지 발견이 "
        "아니다 — 극한은 「매매를 하지 마라」다."
    )
    out.append("")
    out.append(
        "⚠️ **측정 전용** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()` "
        "기본값 **불변**이고 서킷브레이커는 전부 옵트인이다(안 켜면 비트 재현). 채택은 "
        "**재-베이스라인 = 사용자 결정** · 개발자 임의 착수 금지 · 실거래 보류 유지"
        "(`ALPHABLOCK_LIVE_TRADING=false`)."
    )
    out.append("")
    out.append(
        "**좌표**: 12종목 × 4TF **한 지갑** · 못 박은 6년 창 · 존폭 필터 끔(WAN-384) · "
        "인과 취소(WAN-365) · 재진입 ON(band, WAN-273) · cap_only 5배 · 익절 메이커"
        "(WAN-370) · `baseline` 렌즈 · 복리 **끔** · **핀 하나도 없다**(WAN-305)."
    )
    if elapsed is not None:
        out.append("")
        out.append(f"**실측 비용**: {elapsed / 3600:.2f}시간. ⚠️ 다른 모듈로 옮기지 말 것(WAN-316).")
    out.append("")
    out.extend(_defs_section(defs))
    out.extend(_grid_section(rows))
    out.extend(_null_section(nulls))
    out.extend(_loo_section(loo))
    out.extend(_checks_section(checks))
    out.append("")
    out.append("---")
    out.append("")
    out.append(
        "⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *언제 "
        "매매를 멈추나*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 않는다(**다른 질문**). "
        "전부 `baseline`(낙관) 렌즈 위 값이고 **체결 보수화(`pen_5bp`)는 안 쟀다**. "
        "총수익 %는 이 좌표에서 포화하고(WAN-169/213) 6년 MDD는 폭락 미포함 **바닥선**이다."
    )
    return "\n".join(out) + "\n"


def _defs_section(defs: Sequence[DefinitionRow]) -> list[str]:
    out = ["## §1-0 — 정의를 못 박는다 (선행 관문)", ""]
    if not defs:
        out.append("🚨 §1-0 행이 없다 — 판정하지 않는다(빈 표에서 결론을 지어내지 않는다).")
        out.append("")
        return out
    primary = [d for d in defs if d.segment == PRIMARY_SEGMENT]
    zero = primary[0].zero_duration_trades if primary else 0
    total = primary[0].total_trades if primary else 0
    out.append(
        f"주 구간(`{PRIMARY_SEGMENT}`) 거래 **{total:,}건** 중 `진입 == 청산`인 거래가 "
        f"**{zero:,}건**({zero / total:.2%} — 「같은 분 익절」 부류, WAN-336)이다. "
        "🚨 **그 수가 곧 (b) 두 규약의 차다**(§1-0 대수 항등)."
    )
    out.append("")
    for indicator, title in (
        ("open_cells_before", "(b) 그 순간 열린 칸 수"),
        ("realized_net_r_today_before", "(c) 그날 실현 net R 누적"),
    ):
        out.append(f"### {title}")
        out.append("")
        out.append("| 규약 | 버킷 | 거래 | 비중 | 승률 | 거래당 net R | net R 합 |")
        out.append("| -- | -- | --: | --: | --: | --: | --: |")
        for row in primary:
            if row.indicator != indicator:
                continue
            thin = " ⚠️표본" if row.num_trades < MIN_BUCKET_TRADES else ""
            out.append(
                f"| `{row.convention}` | {row.bucket}{thin} | {row.num_trades:,} | "
                f"{_pct(row.share_of_trades)} | {_pct(row.win_rate)} | "
                f"{_fmt(row.mean_net_r)} | {_fmt(row.sum_net_r, 1)} |"
            )
        out.append("")
        conventions = OPEN_CONVENTIONS if indicator == "open_cells_before" else REALIZED_CONVENTIONS
        worst = {c: worst_bucket(primary, indicator=indicator, convention=c) for c in conventions}
        if any(b is None for b in worst.values()):
            verdict = "🚨 **판정 불가**(유효 버킷이 없다 — 표본 미달)"
        elif len(set(worst.values())) == 1:
            verdict = "**두 규약이 같은 버킷을 가장 나쁘다고 본다**"
        else:
            verdict = "🚨 **규약에 갈린다**"
        out.append(
            f"가장 나쁜 버킷(**{MIN_BUCKET_TRADES}건 미만 버킷은 후보에서 뺀다**): "
            + " · ".join(f"`{c}` → {b or '—'}" for c, b in worst.items())
        )
        out.append("")
        out.append(f"{verdict}.")
        out.append("")
    out.append(
        "📌 **스위치는 `settled`를 읽는다** — 관측이 아니라 **집행**이라 「북이 그 순간 아는 "
        "것」(`settle_due(t)`의 경계)이 정본이어야 한다. `strict`는 WAN-408 §0-3의 관측 규약이고 "
        "둘 다 인과적이되 **같은 수가 아니다**."
    )
    out.append("")
    return out


def _grid_section(rows: Sequence[GridRow]) -> list[str]:
    out = ["## §1-1 — 격자 (자 다섯을 한 줄에)", ""]
    if not rows:
        out.append("🚨 격자 행이 없다 — 판정하지 않는다.")
        out.append("")
        return out
    base = _base_row(rows, PRIMARY_SEGMENT)
    if base is not None:
        out.append(
            f"기준 팔(`{PRIMARY_SEGMENT}`): 거래 **{base.num_trades:,}** · 거래당 net R "
            f"**{_fmt(base.mean_net_r)}** · net R 합 **{_fmt(base.sum_net_r, 1)}** · "
            f"매매한 날 **{base.trading_days:,}일** · 지갑 층 "
            f"{'정의됨' if base.wallet_defined else '**정의 상실**'}."
        )
        out.append("")
    out.append(f"### 주 구간 `{PRIMARY_SEGMENT}`")
    out.append("")
    out.append(
        "| 팔 | 거래 | 승률 | 거래당 net R | net R 합 | MDD | 발동일 | 차단 | gross | 비용 |"
    )
    out.append("| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
    for row in _segment_rows(rows, PRIMARY_SEGMENT):
        trips = (
            f"{row.trip_days:,}/{row.trading_days:,} ({_pct(row.trip_day_share)})"
            if row.kind != "base"
            else "—"
        )
        blocked = (
            f"{row.blocked_candidates:,} ({_pct(row.blocked_share_of_base)})"
            if row.kind != "base"
            else "—"
        )
        out.append(
            f"| {row.label} | {row.num_trades:,} | {_pct(row.win_rate)} | "
            f"{_fmt(row.mean_net_r)} | {_fmt(row.sum_net_r, 1)} | "
            f"{_wallet_cell(row, row.max_drawdown)} | {trips} | {blocked} | "
            f"{_fmt(row.gross_r)} | {_fmt(row.cost_r)} |"
        )
    out.append("")
    best = argmax_arm(rows, PRIMARY_SEGMENT)
    if best is not None and base is not None:
        delta = best.mean_net_r - base.mean_net_r
        out.append(
            f"argmax(A축): **{best.label}** — 거래당 net R {_fmt(best.mean_net_r)} "
            f"(기준 대비 {_fmt(delta)} · 거래 {best.num_trades:,} = 기준의 "
            f"{best.num_trades / base.num_trades:.1%})."
        )
        out.append("")
        out.append(
            "🚨 **argmax는 채택 근거가 아니다**(WAN-161) — 이 표는 재기만 한다. "
            f"그리고 이 값도 {_sign_note(best.mean_net_r)}(노이즈선 ±{NOISE_R:g}R)."
        )
        out.append("")
    for scope in CIRCUIT_BREAKER_SCOPES:
        is_best, oos_best = is_to_oos_flips(rows, scope=scope)
        if is_best is None or oos_best is None:
            continue
        mark = "같다" if is_best == oos_best else "🚨 **뒤집힌다**"
        out.append(f"* IS→OOS(`{_SCOPE_LABEL[scope]}`): `{is_best}` → `{oos_best}` — {mark}.")
    out.append("")
    out.append("### 구간 넷 병기 (범위 `둘 다` · 거래당 net R)")
    out.append("")
    header = "| 팔 | " + " | ".join(f"`{s}`" for s in SEGMENT_ORDER) + " |"
    out.append(header)
    out.append("| -- | " + " | ".join("--:" for _ in SEGMENT_ORDER) + " |")
    for arm_name in (
        [BASE_ARM.name]
        + [f"loss{t:+.0f}R_both" for t in THRESHOLDS]
        + [f"stops{n}_both" for n in STOP_LIMITS]
    ):
        by_segment = {r.segment: r for r in rows if r.arm == arm_name}
        if not by_segment:
            continue
        label = next(iter(by_segment.values())).label
        cells = " | ".join(
            _fmt(by_segment[s].mean_net_r) if s in by_segment else "—" for s in SEGMENT_ORDER
        )
        out.append(f"| {label} | {cells} |")
    out.append("")
    return out


def _null_section(nulls: Sequence[NullRow]) -> list[str]:
    out = ["## §2겹 — 매칭 대조군 (「덜 매매한 몫」인가)", ""]
    if not nulls:
        out.append(
            "🚨 이 실행은 대조군을 안 돌렸다 — **판정하지 않는다**. 대조군 없이는 개선이 "
            "「손실 뒤를 피한 몫」인지 「덜 매매한 몫」인지 **가를 수 없다**."
        )
        out.append("")
        return out
    scope = nulls[0].scope
    out.append(
        f"각 문턱이 실제로 만든 `(KST 날짜, 첫 발동 시각)`을 **다른 날로 재배정**해 "
        f"*같은 날짜 수 · 같은 하루 중 시각 분포로 쉬되 손실과 무관하게* 쉬는 팔을 "
        f"{NULL_DRAWS}판 만든다(범위 `{scope}` · 단측 순위 p · 하한 "
        f"{1 / (NULL_DRAWS + 1):.3f})."
    )
    out.append("")
    out.append("| 문턱 | 실제 net R | 대조군 평균 | 대조군 최고 | p | 판정 |")
    out.append("| -- | --: | --: | --: | --: | -- |")
    for threshold in THRESHOLDS:
        scoped = [n for n in nulls if n.threshold == threshold]
        actual = next((n for n in scoped if n.is_actual), None)
        draws = [n for n in scoped if not n.is_actual]
        if actual is None:
            continue
        p, count = null_pvalue(nulls, threshold=threshold, scope=scope)
        if not draws:
            verdict = "발동 0일(대조군 없음)"
        elif p <= 0.05:
            verdict = "**대조군을 이긴다**"
        else:
            verdict = "🚨 못 이긴다 = 「덜 매매한 몫」"
        out.append(
            f"| {threshold:+.0f}R | {_fmt(actual.mean_net_r)} | "
            f"{_fmt(_mean([d.mean_net_r for d in draws]))} | "
            f"{_fmt(max((d.mean_net_r for d in draws), default=float('nan')))} | "
            f"{'—' if count == 0 else f'{p:.3f}'} | {verdict} |"
        )
    out.append("")
    out.append(
        "🚨 **널이 항등으로 퇴화하지 않았는지** — 대조군 행의 `blocked_candidates`·`trip_days`가 "
        "실제 팔과 **다른 값**이어야 한다(같으면 재배정이 아무것도 안 한 것이다 · WAN-408 §0-2 "
        "선례). 원본은 `wan410_null.csv`."
    )
    out.append("")
    return out


def _loo_section(loo: Sequence[LooRow]) -> list[str]:
    out = ["## leave-one-out — 종목을 빼고 **지갑을 다시 배치**", ""]
    if not loo:
        out.append("⚠️ 이 실행은 leave-one-out을 안 돌렸다(`--part loo`).")
        out.append("")
        return out
    arm_names = sorted({r.arm for r in loo if r.kind != "base"})
    out.append("| 제외 | 기준 | " + " | ".join(arm_names) + " |")
    out.append("| -- | --: | " + " | ".join("--:" for _ in arm_names) + " |")
    for excluded in sorted({r.exclude for r in loo}):
        scoped = {r.arm: r for r in loo if r.exclude == excluded}
        base = next((r for r in scoped.values() if r.kind == "base"), None)
        cells = " | ".join(_fmt(scoped[a].mean_net_r) if a in scoped else "—" for a in arm_names)
        out.append(f"| −{excluded} | {_fmt(base.mean_net_r) if base else '—'} | {cells} |")
    out.append("")
    return out


def _checks_section(checks: Sequence[ChecksumRow]) -> list[str]:
    out = ["## 검산", ""]
    if not checks:
        out.append("⚠️ 이 실행은 검산을 안 돌렸다(`--part checksum`).")
        out.append("")
        return out
    out.append("| 검산 | 팔 | 구간 | 지표 | 차이 |")
    out.append("| -- | -- | -- | -- | --: |")
    worst: dict[str, float] = {}
    for row in checks:
        worst[row.check] = max(worst.get(row.check, 0.0), row.abs_diff)
    for check, value in sorted(worst.items()):
        out.append(f"| {check} | (전체) | (전체) | 최대 절대차 | {value:.2e} |")
    out.append("")
    failed = [c for c, v in worst.items() if v > 0]
    if failed:
        out.append("🚨 **차이가 0이 아닌 검산이 있다**: " + " · ".join(failed))
    else:
        out.append("✅ 검산 전부 `0.00e+00`.")
    out.append("")
    return out


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _use_smoke_paths() -> None:
    """좁혀 돈 판의 산출물을 `*_smoke.csv`로 돌린다 — 공개 표를 **덮지 않는다**."""
    global DEFS_CSV, GRID_CSV, NULL_CSV, LOO_CSV, CHECKSUM_CSV, SUMMARY_PATH
    DEFS_CSV = REPORTS_DIR / "wan410_definitions_smoke.csv"
    GRID_CSV = REPORTS_DIR / "wan410_grid_smoke.csv"
    NULL_CSV = REPORTS_DIR / "wan410_null_smoke.csv"
    LOO_CSV = REPORTS_DIR / "wan410_loo_smoke.csv"
    CHECKSUM_CSV = REPORTS_DIR / "wan410_checksum_smoke.csv"
    SUMMARY_PATH = REPORTS_DIR / "wan410_circuit_breaker_summary_smoke.md"


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _append(rows: Sequence[BaseModel], path: Path, key: Sequence[str]) -> pd.DataFrame:
    """이미 있는 CSV에 **덮지 않고** 이어 붙인다 — `--part`를 나눠 돌려도 표가 안 깨진다.

    같은 키가 다시 오면 **새 행이 이긴다**(다시 돌린 것을 채택). 🚨 한 파일을 읽고 다시
    쓰므로 **같은 파트를 동시에 두 번 돌리지 말 것**(잃어버린 갱신, WAN-403 선례).

    ⚠️ **키가 바뀌면(예: 검산 이름을 고치면) 옛 행이 고아로 남는다** — 덮이지 않고 옆에
    쌓인다. 그럴 때는 해당 CSV를 **지우고 다시 돌린다**(자동 삭제는 안 한다 — WAN-194/297
    원칙: 무엇을 지웠는지 모르는 표를 저장소가 스스로 만들지 않는다).
    """
    fresh = pd.DataFrame([row.model_dump() for row in rows])
    if path.exists() and not fresh.empty:
        old = pd.read_csv(path)
        merged = pd.concat([old, fresh], ignore_index=True)
        merged = merged.drop_duplicates(subset=list(key), keep="last")
    else:
        merged = fresh
    _write(merged, path)
    return merged


def _read(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [model.model_validate(rec) for rec in frame.to_dict("records")]


def _load_all() -> tuple[
    list[DefinitionRow], list[GridRow], list[NullRow], list[LooRow], list[ChecksumRow]
]:
    return (
        [r for r in _read(DEFS_CSV, DefinitionRow) if isinstance(r, DefinitionRow)],
        [r for r in _read(GRID_CSV, GridRow) if isinstance(r, GridRow)],
        [r for r in _read(NULL_CSV, NullRow) if isinstance(r, NullRow)],
        [r for r in _read(LOO_CSV, LooRow) if isinstance(r, LooRow)],
        [r for r in _read(CHECKSUM_CSV, ChecksumRow) if isinstance(r, ChecksumRow)],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--part",
        choices=("defs", "grid", "null", "loo", "checksum", "summary"),
        default="grid",
        help="무엇을 돌릴지. 전부 CSV에 이어 붙이므로 나눠 돌려도 된다.",
    )
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 다시 낸다")
    parser.add_argument("--jobs", type=int, default=None, help="후보 생성 병렬(성능 노브)")
    parser.add_argument(
        "--loo-arms",
        default="loss-3R_both,loss-10R_both",
        help="leave-one-out을 돌릴 팔(콤마) — 배치는 싸지만 전부 돌 필요는 없다",
    )
    parser.add_argument(
        "--null-thresholds",
        default="",
        help="대조군을 돌릴 문턱(콤마) — 비우면 전부",
    )
    parser.add_argument("--no-cache", action="store_true", help="후보 payload 디스크 캐시를 끈다")
    parser.add_argument(
        "--symbols",
        default="",
        help="좁혀 돌 종목(콤마) — 🚨 **탐색·연기 시험용**이다. 비우면 채택 좌표 전부.",
    )
    parser.add_argument("--timeframes", default="", help="좁혀 돌 TF(콤마). 비우면 채택 좌표 전부.")
    args = parser.parse_args(argv)

    if args.from_csv or args.part == "summary":
        defs, grid, nulls, loo, checks = _load_all()
        SUMMARY_PATH.write_text(
            build_summary_markdown(defs, grid, nulls, loo, checks), encoding="utf-8"
        )
        print(f"요약을 다시 냈습니다: {SUMMARY_PATH}")
        return 0

    started = time.time()
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or list(
        harness.DEFAULT_SYMBOLS
    )
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()] or list(
        harness.DEFAULT_TIMEFRAMES
    )
    narrowed = symbols != list(harness.DEFAULT_SYMBOLS) or timeframes != list(
        harness.DEFAULT_TIMEFRAMES
    )
    adopted = on_adopted_coordinates(symbols, timeframes)
    if narrowed:
        # 🚨 좁혀 돈 판은 **채택 좌표가 아니다** — 공개 CSV를 덮으면 그 표가 조용히 거짓이
        # 된다(WAN-335가 「좁히기가 한쪽에만 걸려 정상 좁히기를 고장으로 읽은」 자리).
        _use_smoke_paths()
        print(
            "[wan410] 🚨 좁혀 돕니다 — 채택 좌표가 아니라 **연기 시험**이고 산출물은 "
            f"`*_smoke.csv`입니다({len(symbols)}종목 × {len(timeframes)}TF).",
            flush=True,
        )
    start, end = harness.DEFAULT_START, harness.DEFAULT_END
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    print(
        f"[wan410] 병렬 설정: 워커 {jobs}개 · 좌표 {len(symbols)}종목 × {len(timeframes)}TF",
        flush=True,
    )

    payloads = build_payloads(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cache=None if args.no_cache else PayloadCache(),
    )
    grid_arms = arms()

    if args.part == "defs":
        # 🚨 §1-0은 **복리를 켜고** 잰다 — WAN-408의 공개 표(그리고 사용자가 올린
        # `book_trades.csv`)가 **인자 없는 채택 북**의 산출물이라, 그 관측을 대조하려면
        # 같은 발판 위에 서야 한다. §1-1 격자는 반대로 복리를 끈다(판정 자가 거래당 net R).
        segs = place(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=list(SEGMENT_ORDER),
            compound=True,
        )
        rows: list[DefinitionRow] = []
        checks_defs: list[ChecksumRow] = []
        for seg in segs:
            facts = trade_facts(seg)
            rows.extend(definition_rows(facts, segment=seg.segment))
            if seg.segment != PRIMARY_SEGMENT or not adopted:
                continue
            mean = _mean([f.net_r for f in facts])
            checks_defs.append(
                ChecksumRow(
                    check="a′ §1-0 발판 ≡ 공개 채택 북",
                    arm=BASE_ARM.name,
                    segment=seg.segment,
                    metric="num_trades",
                    left=float(len(facts)),
                    right=float(PUBLISHED_OOS_WARM_TRADES),
                    abs_diff=abs(len(facts) - PUBLISHED_OOS_WARM_TRADES),
                )
            )
            checks_defs.append(
                ChecksumRow(
                    check="a′ §1-0 발판 ≡ 공개 채택 북",
                    arm=BASE_ARM.name,
                    segment=seg.segment,
                    metric="mean_net_r",
                    left=mean,
                    right=PUBLISHED_OOS_WARM_MEAN_NET_R,
                    abs_diff=abs(mean - PUBLISHED_OOS_WARM_MEAN_NET_R),
                )
            )
        _append(rows, DEFS_CSV, ("segment", "indicator", "convention", "bucket"))
        if checks_defs:
            _append(checks_defs, CHECKSUM_CSV, ("check", "arm", "segment", "metric"))
        else:
            print("[wan410] 검산 (a′)는 **건너뜁니다** — 채택 좌표가 아닙니다(좁혀 돈 판).")
        print(f"§1-0 {len(rows)}행 → {DEFS_CSV}")
    elif args.part == "grid":
        rows_grid, _placed = grid_rows(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=list(SEGMENT_ORDER),
            grid=grid_arms,
        )
        _append(rows_grid, GRID_CSV, ("arm", "segment"))
        print(f"§1-1 {len(rows_grid)}행 → {GRID_CSV}")
    elif args.part == "null":
        wanted = (
            [float(v) for v in args.null_thresholds.split(",") if v.strip()]
            if args.null_thresholds
            else list(THRESHOLDS)
        )
        needed = [
            a for a in grid_arms if a.is_base or (a.scope == "both" and a.threshold in wanted)
        ]
        _rows, placed = grid_rows(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=[PRIMARY_SEGMENT],
            grid=needed,
            log=False,
        )
        rows_null = null_rows(
            payloads,
            placed,
            start_ms=start_ms,
            end_ms=end_ms,
            thresholds=wanted,
        )
        _append(rows_null, NULL_CSV, ("segment", "threshold", "scope", "seed"))
        print(f"§2겹 {len(rows_null)}행 → {NULL_CSV}")
    elif args.part == "loo":
        wanted_names = {n.strip() for n in args.loo_arms.split(",") if n.strip()}
        needed = [a for a in grid_arms if a.is_base or a.name in wanted_names]
        rows_loo = loo_rows(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            segment=PRIMARY_SEGMENT,
            grid=needed,
        )
        _append(rows_loo, LOO_CSV, ("exclude", "arm", "segment"))
        print(f"LOO {len(rows_loo)}행 → {LOO_CSV}")
    else:  # checksum
        _rows, placed = grid_rows(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=[PRIMARY_SEGMENT],
            grid=grid_arms,
            log=False,
        )
        checks = (
            (
                check_adopted_identity(
                    payloads, start_ms=start_ms, end_ms=end_ms, segments=list(SEGMENT_ORDER)
                )
                if adopted
                else []
            )
            + check_breaker_bound(placed, grid_arms)
            + check_open_positions_untouched(placed, grid_arms, segment=PRIMARY_SEGMENT)
            + check_candidate_layer(payloads, placed)
        )
        _append(checks, CHECKSUM_CSV, ("check", "arm", "segment", "metric"))
        print(f"검산 {len(checks)}행 → {CHECKSUM_CSV}")

    defs, grid_loaded, nulls, loo, checks_loaded = _load_all()
    SUMMARY_PATH.write_text(
        build_summary_markdown(
            defs, grid_loaded, nulls, loo, checks_loaded, elapsed=time.time() - started
        ),
        encoding="utf-8",
    )
    print(f"요약: {SUMMARY_PATH} ({time.time() - started:.0f}초)")
    return 0


if __name__ == "__main__":  # pragma: no cover - 실행 진입점
    raise SystemExit(main())
