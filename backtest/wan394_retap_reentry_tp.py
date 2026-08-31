"""WAN-394 §1 — 재탭 × 재진입 × 익절 배수 세 축 격자.

## 왜 — 아무도 이 조합을 안 쟀다

두 표가 각각 다른 축을 고정했다:

| 표 | 재탭 | 재진입 | 배수 |
| -- | -- | -- | -- |
| WAN-389 | **축** | **축** | 1.5R 고정 |
| WAN-381 | 켬 고정 | 켬 고정 | **축** |

**세 축이 만나는 칸이 격자에 없다.** 실측(둘 다 `oos_warm` · 가드 0.30%)::

    채택(재탭 켬·재진입 켬·1.5R)        −0.1194R
    재탭 끔 · 재진입 끔 (1.5R)          −0.0658R   (+0.0536)
    배수 1.5R → 0.6R (재탭·재진입 켬)   −0.0064R   (+0.1130)

순진하게 더하면 **+0.047R로 양수**가 된다 — 이 저장소에서 처음이다. 🚨 **그런데 순진한
덧셈은 두 번 실측으로 틀렸다**: WAN-368(가드 기여가 자리에 따라 **4.5배**) · WAN-389 자신
(재탭 효과가 재진입 상태에 따라 **2.9배**). **재봐야 알지 더해서는 모른다** — 이 모듈의
산출물은 그 **상호작용 한 줄**(§2 판정)이다.

## 격자

| 축 | 값 | 성격 |
| -- | -- | -- |
| 재탭 | `every_tap`(채택) · `once` | **후보 생성**(2번) |
| 재진입 | ON(채택) · OFF | **배치** |
| 익절 배수 | `0.6 · 0.8 · 1.0 · 1.5R` | 청산 재시뮬 |
| 가드 | 0.30%(채택) 고정 | 축 아님 — WAN-381이 닫았다 |
| 존폭 필터 끔 · 진입가 볼린저 | 채택값 | 축 아님 |

= **2 × 2 × 4 = 16조합** × 구간. 컴퓨트 단위는 조합이 아니라 **재탭 모드 둘**이다.

## 🚨 재진입을 배치 축으로 쓰는 법 (이 모듈의 유일한 새 배선)

WAN-386이 만든 팔 후보(`CellPayload.arm_candidates`)는 base와 재진입을 **이미 합친** 목록이라
그대로 쓰면 재진입을 끌 수가 없다. 그래서 `scoped()`가 그 목록에서 `is_reentry`인 후보를
**빼서** 재진입 끈 팔을 만든다. 그것이 엔진의 `_segment_cells(include_reentry=False)`와 같은
집합이라는 것은 주장이 아니라 **검산 (f)**다(같은 좌표에서 두 경로를 실제로 배치해 대조).

## 🚨 gross 정의를 섞지 말 것

이 표는 **두 자를 다 싣고 이름으로 가른다**(WAN-393 §2가 이름 붙인 함정):

* `gross_r` — 수수료·**슬리피지 전**(WAN-370/388/389의 자). `slippage_r`이 별도 열이다.
* `mean_gross_r_after_slippage` — 수수료 전이되 **슬리피지는 체결가에 녹아 있다**(WAN-381/386의 자).

검산도 자를 따라 갈린다: (a) 계열은 `gross_r`로 WAN-389와, (b)는
`mean_gross_r_after_slippage`로 WAN-381과 대조한다. **두 열을 나란히 빼지 말 것.**

## 판정 열 (완료기준 1~6)

거래당 net R **± 표준오차**(WAN-381 최선이 −0.0023 ± 0.0057이라 부호를 못 정했다) · gross ·
비용 분해 · **거래 수**(WAN-378: 「덜 매매해서 좋아 보이는 것」과 구분) · **계좌 수익률(복리
끈 판) · MDD · 청산 건수**(WAN-381: −0.0023R짜리 칸도 계좌는 −82% · 청산 6,807건 — **거래당
0 ≠ 계좌 본전**) · **같은 분 익절 비중**(WAN-336/348: 목표를 0.6R로 당기면 그 낙관에 더
기댈 수 있다) · 앞구간/뒷구간 · 종목 leave-one-out(지갑 재배치, WAN-316).

## 검산

* **(a)** 1.5R 네 칸 ≡ `wan389_retap_attribution_grid.csv`의 같은 네 팔 — 이 격자가 배수
  축을 더해도 WAN-389 행이 안 움직인다는 증거.
* **(b)** `every_tap` × 재진입 ON의 배수 4점 ≡ `wan381_exit_scales_grid.csv`의 가드 0.30% 행.
* **(c)** 재진입 끈 팔의 **재진입 거래가 전 구간 0건**(라벨이 아니라 동작).
* **(d)** `once` 팔의 **재탭 거래가 전 구간 0건**.
* **(e)** 같은 (재탭, 재진입)의 배수 넷이 **같은 진입 집합**(익절은 청산만 바꾼다).
* **(f)** `is_reentry` 필터 ≡ 엔진의 `include_reentry=False` — 위 🚨 문단의 그 주장.

재현::

    uv run python -m backtest.wan394_retap_reentry_tp --pilot            # 한 칸 견적
    uv run python -m backtest.wan394_retap_reentry_tp --jobs 4           # 48칸 격자
    uv run python -m backtest.wan394_retap_reentry_tp --retaps once --append
    uv run python -m backtest.wan394_retap_reentry_tp --from-csv         # 요약만

⚠️ **측정 전용** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()` 기본값을
하나도 안 바꾼다 · 핀 없음(WAN-305) · 판단은 북에서(WAN-341). ❌ **재진입을 끄자는 제안이
아니다**(WAN-273 사용자 결정 · 끈 팔은 반사실) · ❌ 익절 배수 기본값(1.5R) 전환도 WAN-81/90
소관 · **사용자 결정**. ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값 · 체결 보수화
(`pen_5bp`)는 범위 밖 · **재무장 일정이 배수마다 고정**된 한계가 남는다(WAN-387) — 낮은
배수 행이 그 위의 값이다. 🚨 **「흑자」로 기대하지 말 것**(WAN-370: 비용을 0으로 만들어도
천장이 +0.09R). ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는
*같은 셋업을 몇 번에 나눠 잡고 어디서 챙기나*를 묻지 *진입 규칙이 무작위와 구분되나*를
묻지 않는다.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.confirmation_arm import ARM_BASE
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import BacktestConfig, Trade
from backtest.payload_cache import DEFAULT_CACHE_DIR, PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, arm_key, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS, classify_trades
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan381_exit_scales import ADOPTED_MULTIPLE, MULTIPLES
from backtest.wan381_exit_scales import GRID_CSV_PATH as WAN381_GRID_CSV_PATH
from backtest.wan386_confirmation_pnl import _gross_r
from backtest.wan388_merge_retap_census import ADOPTED_COMBINE_OBS, ADOPTED_RETAP_MODE
from backtest.wan388_merge_x_retap import (
    NOISE_R,
    ChecksumRow,
    GridRow,
    _cfg,
    _row_kwargs,
    _short,
    wallet_defined,
)
from backtest.wan389_retap_attribution import GRID_CSV_PATH as WAN389_GRID_CSV_PATH
from backtest.wan389_retap_attribution import NEW_THREE, RETAP_MODES, entry_in_zone

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan394_retap_reentry_tp_grid.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan394_leave_one_out.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan394_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan394_retap_reentry_tp_summary.md"

#: 재진입 축 — `True`가 채택(WAN-273 band). `False`는 **귀속용 반사실**이지 제안이 아니다.
REENTRY_STATES: tuple[bool, ...] = (True, False)

#: leave-one-out 구간 — `full`(6년 낙폭이 사는 곳)과 `oos_warm`(주 수치).
LOO_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

#: 이 격자가 이 팔의 배수 하나만 만든다 — 확인 진입 팔(WAN-386)은 이 이슈의 축이 아니다.
ARM = ARM_BASE

#: 「net R의 몇 %를 만드는가」를 낼 수 있는 **최소 분모**(R 단위, 양수여야 한다).
#:
#: 🚨 이 좌표는 거래당 기대값이 음수라 순손익 합이 대개 음수다 — 그대로 나누면 「48%」가
#: **부호가 뒤집힌 채** 나오고(파일럿에서 `-384%`) 읽는 사람은 그것을 비중으로 읽는다.
#: WAN-336이 USD 축에서 쓴 것과 같은 규약이고, 표는 대신 **거래 수 몫**을 싣는다.
MIN_NET_R_DENOM = 10.0


# --------------------------------------------------------------------------- #
# 팔
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Point:
    """격자의 한 점 — (재탭, 재진입, 배수)."""

    retap_mode: str
    reentry: bool
    multiple: float

    @property
    def name(self) -> str:
        retap = "매탭" if self.retap_mode == ADOPTED_RETAP_MODE else "첫탭만"
        reentry = "재진입켬" if self.reentry else "재진입끔"
        return f"{retap}·{reentry}·{self.multiple:g}R"

    @property
    def is_adopted(self) -> bool:
        """오늘 채택 북과 같은 점인가 — 검산 (a)의 기준이다."""
        return (
            self.retap_mode == ADOPTED_RETAP_MODE
            and self.reentry
            and self.multiple == ADOPTED_MULTIPLE
        )


def points_for(retap_mode: str) -> list[Point]:
    """이 재탭 모드가 먹이는 점 여덟 — 재진입 2 × 배수 4가 **후보 생성 하나**를 나눠 쓴다."""
    return [
        Point(retap_mode, reentry, multiple) for reentry in REENTRY_STATES for multiple in MULTIPLES
    ]


ADOPTED_POINT = Point(ADOPTED_RETAP_MODE, True, ADOPTED_MULTIPLE)

#: 가장 낮은 배수 — 판정 줄이 「배수를 당긴 몫」으로 쓰는 그 점이다(이슈가 지목한 0.6R).
LOW_MULTIPLE = min(MULTIPLES)

#: leave-one-out을 도는 점 — **판정 줄에 실제로 들어가는 넷**이다.
#:
#: 🚨 16점을 전부 돌지 않는 것은 컴퓨트가 아니라 **읽는 법** 때문이다: 편중 질문은 *「이
#: 표의 결론이 한 종목이 만든 것인가」*이고 그 결론은 §2 판정 줄이 낸다. 나머지 12점은 그
#: 줄에 안 들어가므로 LOO 행이 있어도 읽을 자리가 없다(그래도 필요하면 `--loo-all`).
LOO_POINTS: tuple[Point, ...] = (
    ADOPTED_POINT,
    Point("once", False, ADOPTED_MULTIPLE),
    Point(ADOPTED_RETAP_MODE, True, LOW_MULTIPLE),
    Point("once", False, LOW_MULTIPLE),
)


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class TriaxialRow(GridRow):
    """WAN-388/389 행에 **재진입 · 익절 배수 · 판정 보조 열**을 더한 것.

    열이 겹치는 부분은 이름·정의가 WAN-388/389와 **같다** — 그래야 검산 (a)가 성립하고 두
    표를 한 자로 읽을 수 있다.
    """

    model_config = ConfigDict(frozen=True)

    reentry: bool
    multiple: float
    adopted_point: bool
    net_r_stderr: float
    """거래당 net R의 표준오차. 🚨 **부호를 못 정하는 칸을 그대로 찍기 위한 열**이다 —
    WAN-381 최선이 −0.0023 ± 0.0057이었다(양수가 나와도 같은 검사를 한다)."""
    mean_gross_r_after_slippage: float
    """WAN-381/386의 gross — 수수료 전이되 **슬리피지는 체결가에 녹아 있다**.
    🚨 `gross_r`(슬리피지 전, WAN-370/388/389)과 **다른 자**다. 검산이 자를 따라 갈린다."""
    same_step_tp_trades: int
    """진입한 그 1분 안에 익절한 거래 수(WAN-336). 목표를 당기면 이 낙관에 더 기댄다."""
    same_step_tp_trade_share: float
    """그 거래가 **거래 수**에서 차지하는 몫. 🚨 **이 열이 표의 것**이다 — 분모가 거래 수라
    언제나 정의된다."""
    same_step_tp_net_r_share: float | None
    """그 거래들이 만든 net R 합 ÷ 전체 net R 합. 🚨 **분모가 양수이고 충분히 클 때만
    낸다** — 이 좌표는 거래당 기대값이 음수라 분모가 대개 음수이고, 그러면 「48%」 같은 수가
    **부호가 뒤집힌 채** 나온다(WAN-115가 문서화한 함정 · WAN-336도 같은 이유로 음수 분모에서
    비율을 withhold한다). 파일럿에서 실제로 `-384%`가 찍혀 이 가드를 조였다."""


class LooRow(TriaxialRow):
    """종목 하나(또는 신규 3종목)를 빼고 **지갑을 다시 배치**한 행 (WAN-316 스코프 패턴)."""

    exclude: str


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def _cell_kwargs() -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계).

    `reentry=True`를 **항상** 켠 채로 만든다(WAN-305 기본값) — 재진입 후보를 payload에 실어
    두고 배치에서 고르기 때문이고, 그래야 두 재진입 팔이 **글자 그대로 같은 base 후보**를
    쓴다(검산 (c)·(f)의 전제).
    """
    return {
        **ADOPTED_CELL_KWARGS,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    }


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    retap_mode: str,
    start: str,
    end: str,
    jobs: int,
    cold_segments: bool = True,
    cache: PayloadCache | None = None,
) -> list[CellPayload]:
    """이 재탭 모드의 후보를 만든다 — **점이 아니라 재탭 모드가 컴퓨트 단위다**.

    배수 넷은 `confirmation_multiples`로 **한 순회에** 나오고(WAN-386 §0), 재진입 두 상태는
    배치에서 갈린다. 그래서 8개 점이 후보 생성 **하나**를 나눠 쓴다.
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        engine_check=False,
        combine_obs=ADOPTED_COMBINE_OBS,
        retap_mode=retap_mode,
        confirmation_arms=(ARM,),
        confirmation_multiples=MULTIPLES,
        payload_cache=cache,
        **_cell_kwargs(),  # type: ignore[arg-type]
    )


def scoped(payloads: Sequence[CellPayload], *, multiple: float, reentry: bool) -> list[CellPayload]:
    """그 배수의 팔 후보를 `candidates` 자리에 끼운 payload 사본 (재진입 축 포함).

    🚨 **이 함수가 이 모듈의 유일한 새 배선이다.** 팔 후보는 base와 재진입을 **이미 합친**
    목록이라(WAN-386) 배치는 `include_reentry=False`로 하고, 재진입을 끄려면 그 목록에서
    `is_reentry`인 후보를 **여기서** 빼야 한다. 그것이 엔진의 `include_reentry=False`와 같은
    집합이라는 것은 주장이 아니라 **검산 (f)**다.
    """
    key = arm_key(ARM, multiple)
    out: list[CellPayload] = []
    for payload in payloads:
        cands = payload.arm_candidates.get(key)
        if cands is None:
            raise KeyError(f"{payload.symbol} {payload.timeframe}: 팔 후보가 없습니다({key}).")
        picked = {
            segment: tuple(c for c in cs if reentry or not c.is_reentry)
            for segment, cs in cands.items()
        }
        out.append(replace(payload, candidates=picked, reentry_candidates={}))
    return out


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    include_reentry: bool = False,
    compound: bool = False,
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성을 명시한다(한 표가 한 회계).

    `include_reentry`는 기본이 꺼짐이다 — 팔 후보가 이미 합쳐진 목록이라 켜 두면 재진입이
    **한 번 더** 들어가 이중 계상이 된다(WAN-386 관행). 재진입 축은 `scoped()`가 담당한다.
    검산 (f)만 이 인자를 켜 엔진 경로와 대조한다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=include_reentry,
        min_stop_distance_fraction=ADOPTED_STOP_GUARD,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# 행 만들기
# --------------------------------------------------------------------------- #


def _extra_kwargs(segment: BookSegment) -> dict[str, object]:
    """WAN-388 열에 더하는 판정 보조 열 — 표준오차 · 다른 자의 gross · 같은 분 익절."""
    pairs = segment.trades_with_placements()
    nets = [
        net for trade, placement in pairs if (net := _net_or_none(trade, placement)) is not None
    ]
    grosses = [_gross_r(trade, placement) for trade, placement in pairs]
    same = classify_trades(pairs)
    total_net_r = same["net_r"]
    return {
        "net_r_stderr": (statistics.stdev(nets) / (len(nets) ** 0.5) if len(nets) > 1 else 0.0),
        "mean_gross_r_after_slippage": sum(grosses) / len(grosses) if grosses else 0.0,
        "same_step_tp_trades": int(same["tp_trades"]),
        "same_step_tp_trade_share": (int(same["tp_trades"]) / len(pairs) if pairs else 0.0),
        # 🚨 분모가 **양수이고 충분히 클 때만** 낸다(WAN-115 부호 함정 · WAN-336 관행).
        "same_step_tp_net_r_share": (
            same["tp_net_r"] / total_net_r if total_net_r > MIN_NET_R_DENOM else None
        ),
    }


def _net_or_none(trade: Trade, placement: PlacedSetup) -> float | None:
    """리스크 금액이 0이면 R로 정규화할 수 없다 — `_row_kwargs`와 **같은 규칙**으로 뺀다
    (표준오차가 평균과 다른 표본에서 나오면 그 ± 는 그 평균의 것이 아니다)."""
    return None if placement.risk_amount <= 0 else net_r(trade, placement)


def _point_fields(point: Point) -> dict[str, object]:
    return {
        "arm": point.name,
        "label": point.name,
        "combine_obs": ADOPTED_COMBINE_OBS,
        "retap_mode": point.retap_mode,
        "reentry": point.reentry,
        "multiple": point.multiple,
        "adopted_arm": point.is_adopted,
        "adopted_point": point.is_adopted,
    }


def build_point_rows(
    payloads: Sequence[CellPayload],
    *,
    point: Point,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    segments: Sequence[str],
    cfg: BacktestConfig,
) -> list[TriaxialRow]:
    view = scoped(payloads, multiple=point.multiple, reentry=point.reentry)
    rows: list[TriaxialRow] = []
    for segment in place(view, start_ms=start_ms, end_ms=end_ms, segments=segments):
        rows.append(
            TriaxialRow(
                **_point_fields(point),
                **_row_kwargs(
                    segment,
                    cfg,
                    num_symbols=num_symbols,
                    entry_position=entry_in_zone(view, segment.segment, include_reentry=False),
                ),
                **_extra_kwargs(segment),
            )
        )
    return rows


def build_leave_one_out(
    payloads: Sequence[CellPayload],
    *,
    point: Point,
    start_ms: int,
    end_ms: int,
    cfg: BacktestConfig,
    log: bool = True,
) -> list[LooRow]:
    """종목 하나씩 빼고 **지갑을 다시 배치**한다 — 라벨 필터가 아니다(WAN-316)."""
    view = scoped(payloads, multiple=point.multiple, reentry=point.reentry)
    rows: list[LooRow] = []
    all_symbols = sorted({_short(p.symbol) for p in view})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    for drop_label, dropped in drops:
        drop = {s.upper() for s in dropped}
        kept = [p for p in view if _short(p.symbol) not in drop]
        if not kept:
            continue
        for segment in place(kept, start_ms=start_ms, end_ms=end_ms, segments=LOO_SEGMENTS):
            rows.append(
                LooRow(
                    **_point_fields(point),
                    exclude=drop_label,
                    **_row_kwargs(
                        segment,
                        cfg,
                        num_symbols=len({p.symbol for p in kept}),
                        entry_position=entry_in_zone(kept, segment.segment, include_reentry=False),
                    ),
                    **_extra_kwargs(segment),
                )
            )
    if log:
        print(f"[wan394] {point.name}: leave-one-out {len(drops)}판 완료", flush=True)
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #

#: 검산 (a)가 대조하는 열 — WAN-389 CSV와 이 표의 1.5R 칸이 **비트 일치**해야 한다.
#: 🚨 `gross_r`(슬리피지 **전**)를 쓴다 — WAN-389와 같은 자다.
_WAN389_METRICS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "mean_net_r",
    "gross_r",
    "cost_r",
    "stop_width_p50",
    "retap_trades",
    "reentry_trades",
    "total_return_flat",
    "max_drawdown",
)

#: 검산 (b)가 대조하는 열 — WAN-381 CSV와 이 표의 `every_tap` × 재진입 ON 칸.
#: 🚨 `mean_gross_r`(슬리피지 **후**)를 쓴다 — WAN-381과 같은 자다. 자를 섞으면 0.1186R이
#: 통째로 차로 나타나 「배선 오류」처럼 읽힌다(WAN-393 §2가 이름 붙인 함정).
_WAN381_METRICS: tuple[tuple[str, str], ...] = (
    ("num_trades", "num_trades"),
    ("win_rate", "win_rate"),
    ("mean_net_r", "mean_net_r"),
    ("mean_gross_r_after_slippage", "mean_gross_r"),
    ("total_return_flat", "total_return_flat"),
    ("max_drawdown", "max_drawdown"),
)

#: WAN-389 팔 이름 → 이 격자의 (재탭, 재진입).
_WAN389_ARMS: dict[str, tuple[str, bool]] = {
    "split_every": ("every_tap", True),
    "split_once": ("once", True),
    "split_every_no_reentry": ("every_tap", False),
    "split_once_no_reentry": ("once", False),
}


def _same_coordinates(row: TriaxialRow, other: dict[str, Any]) -> bool:
    """좌표가 다르면 **아예 대조하지 않는다** — 다른 두 표를 빼면 배선 오류처럼 읽힌다."""
    return (int(other["num_cells"]), int(other["num_symbols"])) == (row.num_cells, row.num_symbols)


def check_against_wan389(
    rows: Sequence[TriaxialRow], *, path: Path = WAN389_GRID_CSV_PATH
) -> list[ChecksumRow]:
    """검산 (a) — 1.5R 네 칸 ≡ WAN-389의 같은 네 팔.

    🚨 **「배수 축을 더해도 기존 행이 안 움직였다」의 증거다.** 두 모듈이 후보를 각각 만들어
    (다른 실행 · 다른 날) 같은 숫자를 내야 한다.
    """
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    by_arm: dict[tuple[str, bool, str], dict[str, Any]] = {}
    for rec in frame.to_dict("records"):
        axes = _WAN389_ARMS.get(str(rec["arm"]))
        if axes is not None:
            by_arm[(axes[0], axes[1], str(rec["segment"]))] = rec
    out: list[ChecksumRow] = []
    for row in rows:
        if row.multiple != ADOPTED_MULTIPLE:
            continue
        other = by_arm.get((row.retap_mode, row.reentry, row.segment))
        if other is None or not _same_coordinates(row, other):
            continue
        for metric in _WAN389_METRICS:
            lhs, rhs = float(getattr(row, metric)), float(other[metric])
            out.append(
                ChecksumRow(
                    check="a WAN-389 같은 팔(1.5R)",
                    arm=row.arm,
                    segment=row.segment,
                    metric=metric,
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return out


def check_against_wan381(
    rows: Sequence[TriaxialRow], *, path: Path = WAN381_GRID_CSV_PATH
) -> list[ChecksumRow]:
    """검산 (b) — `every_tap` × 재진입 ON의 배수 4점 ≡ WAN-381 가드 0.30% 행."""
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    by_point: dict[tuple[float, str], dict[str, Any]] = {}
    for rec in frame.to_dict("records"):
        if abs(float(rec["guard"]) - ADOPTED_STOP_GUARD) < 1e-12:
            by_point[(round(float(rec["multiple"]), 2), str(rec["segment"]))] = rec
    out: list[ChecksumRow] = []
    for row in rows:
        if row.retap_mode != ADOPTED_RETAP_MODE or not row.reentry:
            continue
        other = by_point.get((round(row.multiple, 2), row.segment))
        if other is None or not _same_coordinates(row, other):
            continue
        for mine, theirs in _WAN381_METRICS:
            lhs, rhs = float(getattr(row, mine)), float(other[theirs])
            out.append(
                ChecksumRow(
                    check="b WAN-381 가드 0.30%",
                    arm=row.arm,
                    segment=row.segment,
                    metric=f"{mine} ↔ {theirs}",
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return out


def check_reentry_axis(rows: Sequence[TriaxialRow]) -> list[ChecksumRow]:
    """검산 (c) — 재진입 끈 점의 **재진입 거래가 전 구간 0건**(라벨이 아니라 동작)."""
    return [
        ChecksumRow(
            check="c 재진입 축이 실제로 걸렸나",
            arm=row.arm,
            segment=row.segment,
            metric="reentry_trades",
            left=float(row.reentry_trades),
            right=0.0,
            abs_diff=float(row.reentry_trades),
        )
        for row in rows
        if not row.reentry
    ]


def check_retap_axis(rows: Sequence[TriaxialRow]) -> list[ChecksumRow]:
    """검산 (d) — `once` 점의 **재탭 거래가 전 구간 0건**(WAN-388/389 계승)."""
    return [
        ChecksumRow(
            check="d 재탭 축이 실제로 걸렸나",
            arm=row.arm,
            segment=row.segment,
            metric="retap_trades",
            left=float(row.retap_trades),
            right=0.0,
            abs_diff=float(row.retap_trades),
        )
        for row in rows
        if row.retap_mode == "once"
    ]


def check_entry_sets(payloads: Sequence[CellPayload]) -> list[ChecksumRow]:
    """검산 (e) — 같은 (재탭, 재진입)의 배수 넷이 **같은 진입 집합**(익절은 청산만 바꾼다).

    후보 층에서 본다(배치를 네 번 더 돌 필요가 없다). 배수마다 진입이 갈리면 「배수의 값어치」
    가 「다른 셋업을 골랐다」와 섞인다(WAN-137/143 훅의 성질).
    """
    out: list[ChecksumRow] = []
    for reentry in REENTRY_STATES:
        signatures: dict[float, int] = {}
        for multiple in MULTIPLES:
            view = scoped(payloads, multiple=multiple, reentry=reentry)
            entries = tuple(
                (p.symbol, p.timeframe, c.entry_time, c.entry_price)
                for p in view
                for c in p.candidates.get("full", ())
            )
            signatures[multiple] = hash(entries)
        base = signatures[ADOPTED_MULTIPLE]
        for multiple, digest in signatures.items():
            out.append(
                ChecksumRow(
                    check="e 배수는 진입을 안 바꾼다",
                    arm=f"{'재진입켬' if reentry else '재진입끔'}·{multiple:g}R",
                    segment="full",
                    metric="entry_set_hash",
                    left=float(digest == base),
                    right=1.0,
                    abs_diff=0.0 if digest == base else 1.0,
                )
            )
    return out


def check_reentry_filter_matches_engine(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
) -> list[ChecksumRow]:
    """검산 (f) — `is_reentry` 필터 ≡ 엔진의 `include_reentry=False`.

    🚨 **이 모듈의 유일한 새 배선을 재는 검산이다.** 팔 후보(base+재진입 합본)에서
    `is_reentry`를 빼는 것과, 엔진이 낸 base 후보만 배치하는 것이 같은 지갑이어야 한다 —
    어긋나면 「재진입 끔」 팔이 이름만 그런 것이고 §2 판정 줄 전체가 무효다.

    비교는 **채택 배수 하나**로 한다(배수는 진입 집합을 안 바꾼다 — 검산 (e)).
    """
    mine = place(
        scoped(payloads, multiple=ADOPTED_MULTIPLE, reentry=False),
        start_ms=start_ms,
        end_ms=end_ms,
        segments=segments,
    )
    engine = place(
        [replace(p, arm_candidates={}) for p in payloads],
        start_ms=start_ms,
        end_ms=end_ms,
        segments=segments,
        include_reentry=False,
    )
    by_segment = {s.segment: s for s in engine}
    out: list[ChecksumRow] = []
    for segment in mine:
        other = by_segment.get(segment.segment)
        if other is None:
            continue
        for metric in ("num_trades", "win_rate", "total_return", "max_drawdown"):
            lhs = float(getattr(segment.row, metric))
            rhs = float(getattr(other.row, metric))
            out.append(
                ChecksumRow(
                    check="f is_reentry 필터 ≡ 엔진 base-only",
                    arm="재진입끔·1.5R",
                    segment=segment.segment,
                    metric=metric,
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return out


# --------------------------------------------------------------------------- #
# 판정 — 순진한 덧셈과 실측의 차 (완료기준 8)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Verdict:
    """§2 판정 줄 — **이 이슈의 산출물은 마지막 줄(상호작용)이다**."""

    segment: str
    adopted: float | None
    """채택 점(매탭 · 재진입 켬 · 1.5R)의 거래당 net R."""
    delta_axes: float | None
    """재탭·재진입 두 축을 끈 몫(1.5R 고정) — WAN-389가 낸 값."""
    delta_multiple: float | None
    """배수를 0.6R로 당긴 몫(재탭·재진입 켬) — WAN-381이 낸 값."""
    naive: float | None
    """순진한 덧셈. 🚨 이 저장소에서 **두 번 틀린** 계산이다(WAN-368/389)."""
    measured: float | None
    """세 축을 **함께** 흔든 실측(첫탭만 · 재진입 끔 · 0.6R)."""
    measured_stderr: float | None
    trades_adopted: int | None
    trades_measured: int | None

    @property
    def interaction(self) -> float | None:
        """실측 − 순진한 덧셈. 양수면 축들이 **서로를 돕고** 음수면 잡아먹는다."""
        if self.naive is None or self.measured is None:
            return None
        return self.measured - self.naive

    @property
    def label(self) -> str:
        """🚨 규칙은 **착수 전에** 못 박았다(사후에 고르지 않는다).

        판정선은 `NOISE_R`(0.005R, WAN-366/370)이고 다음 중 하나다:

        * **판정 불가** — 필요한 점이 없다.
        * **덧셈이 성립한다** — 상호작용이 노이즈선 안이다(이 저장소에서 처음일 것이다).
        * **덧셈이 과대평가한다** / **과소평가한다** — 상호작용의 부호.

        부호는 **표준오차와 함께** 읽는다 — `sign_is_decided`가 거짓이면 「양수다」라고 말할
        수 없다(WAN-381 최선이 −0.0023 ± 0.0057이라 부호를 못 정했다).
        """
        gap = self.interaction
        if gap is None:
            return "판정 불가"
        if abs(gap) < NOISE_R:
            return "덧셈이 성립한다"
        return "덧셈이 과대평가한다" if gap < 0 else "덧셈이 과소평가한다"

    @property
    def sign_is_decided(self) -> bool:
        """실측의 **부호가 정해졌나** — |값| > 2·표준오차일 때만 참이다."""
        if self.measured is None or self.measured_stderr is None:
            return False
        return abs(self.measured) > 2.0 * self.measured_stderr

    @property
    def crossed_zero(self) -> bool | None:
        """세 축을 함께 흔들어 **거래당 net R이 양수가 됐나**(부호가 정해진 경우만 참)."""
        if self.measured is None:
            return None
        return self.measured > 0.0 and self.sign_is_decided


def _row_of(rows: Sequence[TriaxialRow], point: Point, segment: str) -> TriaxialRow | None:
    return next(
        (
            r
            for r in rows
            if r.retap_mode == point.retap_mode
            and r.reentry == point.reentry
            and abs(r.multiple - point.multiple) < 1e-12
            and r.segment == segment
        ),
        None,
    )


def verdict_for(rows: Sequence[TriaxialRow], segment: str) -> Verdict:
    adopted = _row_of(rows, ADOPTED_POINT, segment)
    axes_only = _row_of(rows, Point("once", False, ADOPTED_MULTIPLE), segment)
    multiple_only = _row_of(rows, Point(ADOPTED_RETAP_MODE, True, LOW_MULTIPLE), segment)
    both = _row_of(rows, Point("once", False, LOW_MULTIPLE), segment)

    def delta(a: TriaxialRow | None) -> float | None:
        return None if a is None or adopted is None else a.mean_net_r - adopted.mean_net_r

    d_axes, d_mult = delta(axes_only), delta(multiple_only)
    naive = (
        None
        if adopted is None or d_axes is None or d_mult is None
        else adopted.mean_net_r + d_axes + d_mult
    )
    return Verdict(
        segment=segment,
        adopted=None if adopted is None else adopted.mean_net_r,
        delta_axes=d_axes,
        delta_multiple=d_mult,
        naive=naive,
        measured=None if both is None else both.mean_net_r,
        measured_stderr=None if both is None else both.net_r_stderr,
        trades_adopted=None if adopted is None else adopted.num_trades,
        trades_measured=None if both is None else both.num_trades,
    )


# --------------------------------------------------------------------------- #
# 입출력
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[GridRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def grid_from_csv(path: Path = GRID_CSV_PATH) -> list[TriaxialRow]:
    if not path.exists():
        return []
    return [TriaxialRow.model_validate(rec) for rec in pd.read_csv(path).to_dict("records")]


def loo_from_csv(path: Path = LOO_CSV_PATH) -> list[LooRow]:
    if not path.exists():
        return []
    return [LooRow.model_validate(rec) for rec in pd.read_csv(path).to_dict("records")]


def checksum_from_csv(path: Path = CHECKSUM_CSV_PATH) -> list[ChecksumRow]:
    if not path.exists():
        return []
    return [ChecksumRow.model_validate(rec) for rec in pd.read_csv(path).to_dict("records")]


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #

#: 부동소수 끝자리 잡음의 상한 — 이 아래는 **불일치가 아니다**(WAN-151/161 관행).
#: CSV를 텍스트로 왕복시키면 완전히 같은 계산도 1e-17 언저리가 남는다. 그것을 「⚠️ 불일치」로
#: 찍으면 **성공이 실패와 같은 모양**이 되고 진짜 배선 오류가 그 소음에 묻힌다.
CHECKSUM_NOISE = 1e-9


def checksum_grade(abs_diff: float) -> str:
    if abs_diff == 0.0:
        return "비트 일치"
    if abs_diff < CHECKSUM_NOISE:
        return "부동소수 끝자리 잡음 — 같은 계산이다"
    return "⚠️ 불일치 — 배선을 확인할 것"


def _fmt(value: float | None, *, digits: int = 4) -> str:
    if value is None:
        return "—"
    mark = " (≈0)" if abs(value) < NOISE_R else ""
    return f"{value:+.{digits}f}R{mark}"


def _segments_present(rows: Sequence[TriaxialRow]) -> list[str]:
    seen = {row.segment for row in rows}
    ordered = [s for s in SEGMENT_ORDER if s in seen]
    return ordered + sorted(seen - set(ordered))


def _points_present(rows: Sequence[TriaxialRow]) -> list[Point]:
    have = {(r.retap_mode, r.reentry, round(r.multiple, 2)) for r in rows}
    return [
        p
        for mode in RETAP_MODES
        for p in points_for(mode)
        if (p.retap_mode, p.reentry, round(p.multiple, 2)) in have
    ]


#: §2 판정 줄이 재는 그 점 — 결론이 이 점 하나에 걸려 있으므로 경고도 이 점에 건다.
MEASURED_POINT = Point("once", False, LOW_MULTIPLE)


def _measured_point_guards(
    rows: Sequence[TriaxialRow], loo: Sequence[LooRow], verdict: Verdict
) -> list[str]:
    """실측 점을 「흑자」로 읽기 전에 반드시 함께 봐야 하는 것 둘.

    🚨 **두 경고 다 결론을 뒤집을 수 있어서 §3에 찍는다** — §1 표에 열로만 있으면 읽는
    사람이 판정 줄만 보고 지나간다(WAN-381이 「−0.0023R짜리 칸도 계좌는 −82%」로 겪은 자리).

    1. **지갑 층** — 거래당 R이 0 언저리여도 계좌는 죽어 있을 수 있다. 이 좌표는 대부분의
       점에서 지갑 지표가 「정의 상실」이라(자본이 0을 뚫는다) 값이 나오는 점은 더더욱
       그 값으로 읽어야 한다.
    2. **종목 leave-one-out** — 실측이 양수인데 종목 하나를 빼면 음수가 되면, 그 양수는
       **한 종목이 만든 것**이다(WAN-111 이래 이 저장소가 반복해 만난 자리).
    """
    out: list[str] = []
    row = _row_of(rows, MEASURED_POINT, PRIMARY_OOS)
    if row is not None:
        if wallet_defined(row):
            out.append(
                f"- 🚨 **지갑 층을 함께 읽는다** — 그 점의 계좌(복리 끔) "
                f"{row.total_return_flat:+.1%} · MDD **{row.max_drawdown:.2%}** · 청산 "
                f"**{row.liquidation_events:,}건**. **거래당 0 ≠ 계좌 본전**이다(WAN-381)."
            )
        else:
            out.append(
                "- 🚨 **그 점의 지갑 층 지표는 「정의 상실」이다** — 자본이 0을 뚫어 비율이 "
                "뜻을 잃는다(WAN-388 §2). **위험의 모양은 이 좌표에서 못 잰다.**"
            )
    sub = [
        r
        for r in loo
        if (r.retap_mode, r.reentry, round(r.multiple, 2))
        == (MEASURED_POINT.retap_mode, MEASURED_POINT.reentry, round(MEASURED_POINT.multiple, 2))
        and r.segment == PRIMARY_OOS
    ]
    if sub and verdict.measured is not None and verdict.measured > 0:
        worst = min(sub, key=lambda r: r.mean_net_r)
        if worst.mean_net_r < 0:
            out.append(
                f"- 🚨 **그 양수는 종목 하나에 걸려 있다** — `{worst.exclude}`를 빼고 지갑을 "
                f"다시 배치하면 {worst.mean_net_r:+.4f}R로 **부호가 넘어간다**(기준 "
                f"{verdict.measured:+.4f}R). WAN-111 이래 이 저장소가 반복해 만난 자리이고, "
                "이것만으로도 채택 근거가 되지 않는다."
            )
        else:
            out.append(
                f"- 종목 leave-one-out {len(sub)}판 전부 **부호 유지**"
                f"(최악 `{worst.exclude}` {worst.mean_net_r:+.4f}R) — 드물게 편중이 아니다."
            )
    return out


def build_summary_markdown(
    rows: Sequence[TriaxialRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    *,
    elapsed: float | None = None,
) -> str:
    out: list[str] = []
    out.append("# WAN-394 §1 — 재탭 × 재진입 × 익절 배수 세 축 격자 (채택 북)")
    out.append("")
    out.append(
        "⚠️ **측정 전용** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()` "
        "기본값 **불변** · 핀 없음(WAN-305) · 판단은 북에서(WAN-341). ❌ **재진입을 끄자는 "
        "제안이 아니고**(WAN-273 사용자 결정) ❌ 익절 배수 기본값 전환도 WAN-81/90 소관 · "
        "**사용자 결정**이다."
    )
    out.append("")
    if not rows:
        out.append("🚨 격자 행이 없다 — 판정하지 않는다(빈 표에서 결론을 지어내지 않는다).")
        out.append("")
        return "\n".join(out)

    present = _points_present(rows)
    segments = _segments_present(rows)
    expected = len(RETAP_MODES) * len(REENTRY_STATES) * len(MULTIPLES)
    if len(present) < expected:
        out.append(
            f"🚨 **격자가 아직 안 찼다**({len(present)}/{expected}점) — 세 축이 만나는 칸이 "
            "없으면 §2 상호작용을 못 낸다. 지금 있는 점: "
            + ", ".join(f"`{p.name}`" for p in present)
        )
        out.append("")

    out.append(f"## 1. 격자 (주 수치 `{PRIMARY_OOS}`)")
    out.append("")
    out.append(
        "| 재탭 | 재진입 | 배수 | 거래 | 거래당 net R | ± SE | gross R(슬립 전) | 비용 R | "
        "승률 | 계좌(복리 끔) | MDD | 청산 | 같은 분 익절 |"
    )
    out.append("| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
    for point in present:
        row = _row_of(rows, point, PRIMARY_OOS)
        if row is None:
            continue
        mdd = f"{row.max_drawdown:.2%}" if wallet_defined(row) else "—"
        equity = f"{row.total_return_flat:+.1%}" if wallet_defined(row) else "—"
        share = f"{row.same_step_tp_trade_share:.0%}"
        star = " ★" if point.is_adopted else ""
        out.append(
            f"| {'켬' if point.retap_mode == ADOPTED_RETAP_MODE else '끔'}{star} | "
            f"{'켬' if point.reentry else '끔'} | {point.multiple:g}R | {row.num_trades:,} | "
            f"{row.mean_net_r:+.4f} | {row.net_r_stderr:.4f} | {row.gross_r:+.4f} | "
            f"{row.cost_r:.4f} | {row.win_rate:.2%} | {equity} | {mdd} | "
            f"{row.liquidation_events:,} | {share} |"
        )
    out.append("")
    out.append("- ★ = 오늘 채택 북(매탭 · 재진입 켬 · 1.5R · 가드 0.30%).")
    out.append(
        "- 🚨 **거래당 0 ≠ 계좌 본전** — WAN-381에서 −0.0023R짜리 칸도 계좌는 −82% · 청산 "
        "6,807건이었다. 계좌·MDD·청산 열을 net R 옆에서 **같이** 읽는다."
    )
    out.append(
        "- 🚨 **거래 수를 net R 옆에서 같이 읽는다** — 재탭·재진입을 끄면 거래가 크게 주는데 "
        "배수를 낮추면 다시 는다. 「덜 매매해서 좋아 보이는 것」과 구분해야 한다(WAN-378)."
    )
    out.append(
        "- **같은 분 익절**은 그 부류가 **거래 수**에서 차지하는 몫이다(WAN-336) — 목표를 "
        "0.6R로 당기면 「1분봉이 봉 안의 순서를 모른다」는 낙관에 **더 기댄다**(WAN-348: 틱이 "
        "지지하는 것은 그중 30%). 🚨 CSV의 net R 몫(`same_step_tp_net_r_share`)은 **분모가 "
        "양수일 때만** 실린다 — 이 좌표는 순손익 합이 대개 음수라 그대로 나누면 비중이 부호가 "
        "뒤집힌 채 나온다(WAN-115 함정)."
    )
    out.append(
        "- 🚨 **gross 자가 둘이다** — 이 표의 `gross R`은 **슬리피지 전**(WAN-370/388/389)이고 "
        "CSV의 `mean_gross_r_after_slippage`는 **슬리피지 후**(WAN-381/386)다. 차가 정확히 "
        "슬리피지 몫이라 **두 열을 나란히 빼지 말 것**(WAN-393 §2)."
    )
    if any(not wallet_defined(r) for r in rows):
        out.append(
            "- 🚨 **`—`인 열은 「정의 상실」이다 — 값이 없는 게 아니라 읽을 수 없다.** 거래당 "
            "기대값이 음수인 좌표에서는 지갑 층 지표가 점을 못 가른다(WAN-388 §2)."
        )
    out.append("")

    out.append("## 2. 판정 줄 — 순진한 덧셈과 실측의 차 (이 이슈의 산출물)")
    out.append("")
    out.append(
        "| 구간 | 채택 | ＋두 축 끔(1.5R) | ＋배수 0.6R | **순진한 덧셈** | **실측(셋 다)** | "
        "± SE | **상호작용** | 판정 |"
    )
    out.append("| -- | --: | --: | --: | --: | --: | --: | --: | -- |")
    for segment in segments:
        v = verdict_for(rows, segment)
        se = "—" if v.measured_stderr is None else f"{v.measured_stderr:.4f}"
        out.append(
            f"| {segment} | {_fmt(v.adopted)} | {_fmt(v.delta_axes)} | {_fmt(v.delta_multiple)} "
            f"| {_fmt(v.naive)} | {_fmt(v.measured)} | {se} | {_fmt(v.interaction)} | {v.label} |"
        )
    out.append("")
    out.append(
        "- **순진한 덧셈** = 채택 + (두 축을 끈 몫) + (배수를 당긴 몫). 🚨 이 저장소에서 "
        "**두 번 틀린** 계산이다(WAN-368 가드 4.5배 · WAN-389 재탭 2.9배)."
    )
    out.append(
        "- **상호작용** = 실측 − 순진한 덧셈. 음수면 축들이 **서로를 잡아먹고** 양수면 돕는다."
    )
    out.append(
        f"- `(≈0)`는 |값| < {NOISE_R}R(WAN-366/370 노이즈선)이라 0과 구분되지 않는다는 표시."
    )
    cold_missing = [s for s in ("is", "oos") if s not in segments]
    if cold_missing:
        out.append(
            "- ⚠️ **차가운 절단(" + " · ".join(f"`{s}`" for s in cold_missing) + ")이 이 판에 "
            "없다**(`--no-cold-segments`, 컴퓨트 절반). 그래서 **「앞구간이 고른 것이 뒷구간에서 "
            "뒤집히는가」(WAN-161 IS→OOS 뒤집힘)를 이 표는 답하지 않는다** — 판정은 `full`과 "
            "주 수치 `oos_warm`의 **부호가 같은지**까지만 읽는다. WAN-389가 같은 이유로 같은 "
            "선택을 했고 그래서 검산 (a)가 그 두 구간에서만 성립한다."
        )
    out.append("")

    out.append("## 3. 결론")
    out.append("")
    v = verdict_for(rows, PRIMARY_OOS)
    if v.label == "판정 불가":
        out.append(f"- 🚨 주 구간(`{PRIMARY_OOS}`)에 필요한 점이 없어 **판정하지 않는다**.")
    else:
        out.append(
            f"- 순진한 덧셈은 {_fmt(v.naive)}를 약속했고 세 축을 **함께** 흔든 실측은 "
            f"{_fmt(v.measured)}다 — 차이가 **{_fmt(v.interaction)}**이고 판정은 "
            f"**「{v.label}」**이다."
        )
        if v.crossed_zero:
            out.append(
                "- 🚨 **거래당 net R이 0을 넘었다** — 이 저장소에서 처음이다. ⚠️ 그래도 "
                "**채택 권고가 아니다**: 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 "
                "체결 보수화(`pen_5bp`)를 안 쟀으며, 낮은 배수일수록 「같은 분 익절」 낙관에 "
                "더 기댄다(WAN-336/348). 위 표의 **계좌·MDD·청산 열을 반드시 함께** 볼 것 — "
                "**거래당 0 ≠ 계좌 본전**이다(WAN-381)."
            )
        elif v.measured is not None and v.measured > 0.0:
            out.append(
                f"- ⚠️ 실측이 양수({_fmt(v.measured)})지만 **부호가 정해지지 않았다** — "
                f"표준오차 {v.measured_stderr:.4f}의 2배 안이다. WAN-381 최선이 "
                "−0.0023 ± 0.0057이라 부호를 못 정한 것과 **같은 자리**다: 양수가 나와도 "
                "같은 검사를 한다."
            )
        else:
            out.append(
                "- **여전히 마이너스다** — 세 축을 다 흔들어도 부호가 안 넘어간다. "
                "🚨 **「흑자」로 기대하지 말 것**(WAN-370: 비용을 0으로 만들어도 천장이 "
                "+0.09R)."
            )
        if v.trades_adopted is not None and v.trades_measured is not None:
            out.append(
                f"- 거래 수 병기(`{PRIMARY_OOS}`): 채택 {v.trades_adopted:,} → "
                f"실측 점 {v.trades_measured:,}."
            )
        out.extend(_measured_point_guards(rows, loo, v))
    out.append("")

    if loo:
        out.append("## 4. 종목 leave-one-out (지갑 재배치)")
        out.append("")
        out.append("| 점 | 구간 | 최악 제외 | 최악 net R | 최선 제외 | 최선 net R | 기준 |")
        out.append("| -- | -- | -- | --: | -- | --: | --: |")
        for point in present:
            key = (point.retap_mode, point.reentry, round(point.multiple, 2))
            for segment in LOO_SEGMENTS:
                sub = [
                    r
                    for r in loo
                    if (r.retap_mode, r.reentry, round(r.multiple, 2)) == key
                    and r.segment == segment
                ]
                if not sub:
                    continue
                worst = min(sub, key=lambda r: r.mean_net_r)
                best = max(sub, key=lambda r: r.mean_net_r)
                ref = _row_of(rows, point, segment)
                base = "—" if ref is None else f"{ref.mean_net_r:+.4f}"
                out.append(
                    f"| {point.name} | {segment} | {worst.exclude} | {worst.mean_net_r:+.4f} | "
                    f"{best.exclude} | {best.mean_net_r:+.4f} | {base} |"
                )
        out.append("")
        out.append(
            "- 🚨 **라벨 필터가 아니라 지갑 재배치다**(WAN-316) — 종목을 빼면 자본 경합이 "
            "달라져 남은 칸의 거래 자체가 바뀐다."
        )
        out.append("")

    out.append("## 5. 검산")
    out.append("")
    if not checks:
        out.append("- (이번 실행에서는 검산을 돌리지 않았다.)")
    else:
        out.append("| 검산 | 점 | 구간 | 지표 | 왼쪽 | 오른쪽 | 절대차 |")
        out.append("| -- | -- | -- | -- | --: | --: | --: |")
        for check in checks:
            out.append(
                f"| {check.check} | {check.arm} | {check.segment} | {check.metric} "
                f"| {check.left:.6g} | {check.right:.6g} | {check.abs_diff:.2e} |"
            )
        worst_diff = max(c.abs_diff for c in checks)
        out.append("")
        out.append(f"- 최대 절대차 **{worst_diff:.2e}** ({checksum_grade(worst_diff)}).")
        out.append(
            "- (a)·(b)는 **다른 모듈·다른 실행**이 같은 숫자를 냈다는 뜻이다 = 「축을 더해도 "
            "기존 행이 안 움직였다」. (c)·(d)는 재진입·재탭 축이 **라벨이 아니라 동작으로** "
            "걸렸다는 증거. (e)는 익절이 진입을 안 바꾼다는 것. 🚨 **(f)가 이 모듈의 유일한 "
            "새 배선을 잰다** — `is_reentry` 필터가 엔진의 `include_reentry=False`와 같은 "
            "지갑을 내는가."
        )
        for tag, note in (
            ("a ", "WAN-389 CSV가 없거나 좌표가 달라 (a)를 **내지 않았다**"),
            ("b ", "WAN-381 CSV가 없거나 좌표가 달라 (b)를 **내지 않았다**"),
        ):
            if not any(c.check.startswith(tag) for c in checks):
                out.append(
                    f"- 🚨 **{note}**(좌표가 다른 두 수를 빼면 배선 오류처럼 읽힌다). "
                    "이 표는 그만큼만 읽는다."
                )
    out.append("")

    out.append("## 6. 경고")
    out.append("")
    out.append(
        "- ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 체결 보수화(`pen_5bp`)는 범위 밖."
    )
    out.append(
        "- ⚠️ **재무장 일정이 배수마다 고정**돼 있다(WAN-387) — 재진입 후보는 채택 배수(1.5R) "
        "시퀀싱에서 나오므로 **낮은 배수 행이 그 위의 값**이다. 알려진 한계이고 이 표가 "
        "재는 축(배수)과 직교하지 않는다."
    )
    out.append(
        "- ⚠️ **총수익 %는 복리 착시라 판정 자가 아니다**(WAN-346) · 이 표의 계좌 수익률은 "
        "**복리를 끈** 판이라 채택 북 보고값과 비교 불가다."
    )
    out.append(
        "- ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *같은 "
        "셋업을 몇 번에 나눠 잡고 어디서 챙기나*를 묻지 *진입 규칙이 무작위와 구분되는가*를 "
        "묻지 않는다."
    )
    out.append("- ⚠️ 채택은 **재-베이스라인 = 사용자 결정**이고 **개발자 임의 착수 금지**다.")
    if elapsed is not None:
        out.append("")
        out.append(f"실측 소요: {elapsed / 3600:.2f}시간")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-394 §1 재탭 × 재진입 × 익절 배수 격자")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument(
        "--retaps",
        default=",".join(RETAP_MODES),
        help=(
            "돌릴 재탭 모드(쉼표). 🚨 **점이 아니라 재탭 모드가 컴퓨트 단위다** — 모드 "
            "하나가 재진입 2 × 배수 4 = 여덟 점을 먹인다."
        ),
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 붙인다")
    parser.add_argument(
        "--loo",
        action="store_true",
        help="leave-one-out을 함께 돌린다(판정 줄의 네 점 — `LOO_POINTS`)",
    )
    parser.add_argument(
        "--loo-all", action="store_true", help="leave-one-out을 **16점 전부** 돌린다"
    )
    parser.add_argument("--no-checksum", action="store_true")
    parser.add_argument(
        "--no-cold-segments",
        action="store_true",
        help="차가운 `is`/`oos` 생성을 건너뛴다(컴퓨트 절반 · 앞구간 판정을 포기한다)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="후보 payload 캐시(§0)를 쓰지 않는다 — 캐시 유무로 산출이 안 갈리는지 확인용",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    parser.add_argument("--pilot", action="store_true", help="1종목 × 4h — 견적용")
    args = parser.parse_args(argv)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_csv:
        SUMMARY_PATH.write_text(
            build_summary_markdown(grid_from_csv(), loo_from_csv(), checksum_from_csv()),
            encoding="utf-8",
        )
        print(f"요약: {SUMMARY_PATH}")
        return 0

    symbols = [s for s in args.symbols.split(",") if s]
    timeframes = [t for t in args.timeframes.split(",") if t]
    if args.pilot:
        symbols, timeframes = symbols[:1], ["4h"]
    unknown = [m for m in args.retaps.split(",") if m and m not in RETAP_MODES]
    if unknown:
        parser.error(f"알 수 없는 재탭 모드: {unknown} (지원: {', '.join(RETAP_MODES)})")
    retaps = [m for m in args.retaps.split(",") if m]

    cold = not args.no_cold_segments
    segments = SEGMENT_ORDER if cold else ("full", PRIMARY_OOS)
    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)
    cache = None if args.no_cache else PayloadCache(args.cache_dir)

    started = time.monotonic()
    cfg = _cfg()
    rows: list[TriaxialRow] = []
    loo: list[LooRow] = []
    checks: list[ChecksumRow] = []
    for retap_mode in retaps:
        mode_started = time.monotonic()
        payloads = build_payloads(
            symbols,
            timeframes,
            retap_mode=retap_mode,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            cold_segments=cold,
            cache=cache,
        )
        print(
            f"[wan394] {retap_mode}: 후보 생성 {(time.monotonic() - mode_started) / 60:.1f}분",
            flush=True,
        )
        if not args.no_checksum:
            checks += check_entry_sets(payloads)
            checks += check_reentry_filter_matches_engine(
                payloads, start_ms=start_ms, end_ms=end_ms, segments=segments
            )
        for point in points_for(retap_mode):
            rows += build_point_rows(
                payloads,
                point=point,
                start_ms=start_ms,
                end_ms=end_ms,
                num_symbols=len(symbols),
                segments=segments,
                cfg=cfg,
            )
            if args.loo and (args.loo_all or point in LOO_POINTS):
                loo += build_leave_one_out(
                    payloads, point=point, start_ms=start_ms, end_ms=end_ms, cfg=cfg
                )
        print(
            f"[wan394] {retap_mode}: 완료 {(time.monotonic() - mode_started) / 60:.1f}분",
            flush=True,
        )

    if args.append:
        keys = {(m, p.reentry, round(p.multiple, 2)) for m in retaps for p in points_for(m)}
        rows = [
            r
            for r in grid_from_csv()
            if (r.retap_mode, r.reentry, round(r.multiple, 2)) not in keys
        ] + rows
        loo = [
            r for r in loo_from_csv() if (r.retap_mode, r.reentry, round(r.multiple, 2)) not in keys
        ] + loo

    if not args.no_checksum:
        checks += check_against_wan389(rows)
        checks += check_against_wan381(rows)
        checks += check_reentry_axis(rows)
        checks += check_retap_axis(rows)

    grid_to_frame(rows).to_csv(GRID_CSV_PATH, index=False)
    if loo:
        grid_to_frame(loo).to_csv(LOO_CSV_PATH, index=False)
    if checks:
        pd.DataFrame([c.model_dump() for c in checks]).to_csv(CHECKSUM_CSV_PATH, index=False)
    SUMMARY_PATH.write_text(
        build_summary_markdown(rows, loo, checks, elapsed=time.monotonic() - started),
        encoding="utf-8",
    )
    print(f"\n격자: {GRID_CSV_PATH}\n요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
