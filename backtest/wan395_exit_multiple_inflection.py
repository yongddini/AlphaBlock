"""WAN-395: 익절 배수의 꺾임을 찾는다 — 0.4·0.5R로 격자를 늘린다.

WAN-381이 가드 5점 × 배수 4점을 돌아 **가드 축은 닫고**(gross 진폭 0.0117R · 다섯 점 전부
음수) **배수 축을 열어 뒀다**(net R 진폭 ＋0.1130R · gross가 0.6·0.8R에서 0을 넘는다).
그런데 그 격자가 **0.6R에서 끝났다** — 결정문이 스스로 적었듯 *「0.6R이 최선인 것은 「거기가
최적」이 아니라 「격자가 거기서 끝났다」」*이고, WAN-394도 같은 자리에서 멈췄다(실측 최선이
0.6R = 끝값).

이 표가 답하는 것은 셋 중 하나이고 **다음 행동이 다 다르다**:

===============================  ==========================  =========================
결과                             뜻                          다음 행동
===============================  ==========================  =========================
0.4~0.5R에서 **더 좋아진다**     꺾임이 아직 더 아래          더 당기는 축을 계속 판다
0.6R **근처에 꺾임**             최적을 찾았다                재-베이스라인 **후보**
0.4~0.5R에서 **나빠진다**        0.6R이 진짜 정점             **배수 축도 닫힌다**
===============================  ==========================  =========================

## 🚨 산수가 「곧 소진된다」고 말한다

손익분기 승률 = `(1 + 비용R) / (1 + 목표R)`. 비용을 0으로 두면 0.4R은 **71.4%**, 0.5R은
**66.7%**가 필요한데 WAN-381 실측 0.6R 승률이 **67.75%**다. 여유가 곧 소진될 가능성이 크고
**그래서 이 표가 「닫힌다」로 끝날 확률이 높다 — 그것도 충분히 값진 답이다.**

⚠️ **이슈 본문의 손익분기 표는 「비용 0」 판이다**(1/(1+R)). 이 모듈은 **두 자를 함께**
낸다 — 비용 0 판과 실측 `비용R`을 넣은 판. 실제로 넘어야 하는 선은 후자다.

## 왜 모듈을 따로 두나 (이슈는 "새로 만들지 말라"고 했다)

축·기계는 **하나도 새로 만들지 않았다** — `wan381_exit_scales`(→ `wan386_confirmation_pnl`)의
`build_payloads`·`arm_payloads`·`guard_census`·`_row_kwargs`·`GridRow`를 그대로 쓴다. 파일이
갈린 이유는 둘뿐이고 둘 다 **완료기준이 강제한다**:

1. **검산 (d)의 상대가 `wan381_exit_scales_grid.csv`다.** 그 CSV를 덮어쓰면 비트 일치를
   대조할 상대가 사라진다(옛 표는 그때의 기록으로 보존한다 — 이 저장소의 관행).
2. **격자 모양이 다르다** — 이슈가 가드를 채택값 하나로 **고정**하라고 했다(WAN-381 §3이
   가드 축을 닫았다). 그래서 이 표는 2차원이 아니라 **배수 하나의 공선**이다.

## 격자

============  ================================================  ====================
축            값                                                비고
============  ================================================  ====================
익절 배수     `0.4` · `0.5`                                     ❌ **신규**
              `0.6` · `0.8` · `1.0` · `1.5`(채택)               ✅ **검산 (d)**
손절폭 가드   `0.30%`(채택) 하나                                축 아님(WAN-381 §3)
체결 렌즈     `baseline`(§1) · `pen_5bp`(§2, 두 점만)           옵트인 `--lens`
============  ================================================  ====================

좌표는 채택 그대로: 12종목 × 4TF **한 지갑** · 못 박은 6년 · 존폭 필터 끔(WAN-384) · 인과
취소(WAN-365) · 재진입 ON(band, WAN-273) · cap_only 5배(WAN-213) · **핀 하나도 없다**
(WAN-305) · 판단은 북에서(WAN-341).

## 읽는 법 · 금지

* **판정 자는 거래당 net R**이고 **거래 수를 항상 옆에 둔다**(WAN-378).
* **±0.005R 안은 「0과 구분되지 않는다」**(WAN-366/370 규약)이고, `sign_is_decided`(|평균| >
  2×표준오차)가 거짓이면 **「0을 넘었다」를 찍지 않는다**(WAN-394 §1이 코드로 만든 관문).
* **argmax는 채택 근거가 아니다**(WAN-161: 배수 argmax가 8칸 중 7칸 IS→OOS 뒤집힘).
* 🚨 **끝점(0.4R)이 최선이면 「최적」이 아니라 「이 격자도 안 꺾였다」로 쓴다.**
* 지갑 층 열은 이 좌표에서 **뜻을 잃는다**(WAN-386 `wallet_defined`) — 읽을 수 있는 것은
  거래당 net R · gross · 승률 · 거래 수 · 최대 동시 칸뿐이다.

## 검산

* **(a-1)** 기준 팔 후보 ≡ 엔진 base+재진입 (칸·구간별, 진입·청산까지)
* **(a-2)** 채택 점(1.5R × 0.30%) 지갑 ≡ **인자 없는 채택 북**(복리 켬)
* **(b)** 여섯 배수의 **진입 집합이 비트 일치** — 익절은 청산만 바꾼다(WAN-137/143)
* **(c)** 진입 유동성이 전부 메이커(라벨이 아니라 **후보의 값**으로 — WAN-370)
* **(d)** 겹치는 배수 4점 ≡ `wan381_exit_scales_grid.csv`의 가드 0.30% 행 **비트 일치**

재현::

    uv run python -m backtest.wan395_exit_multiple_inflection --pilot          # 한 칸 견적
    uv run python -m backtest.wan395_exit_multiple_inflection --jobs 4         # §1 격자
    uv run python -m backtest.wan395_exit_multiple_inflection --lens pen_5bp \\
        --jobs 4 --append                                                      # §2 두 점
    uv run python -m backtest.wan395_exit_multiple_inflection --from-csv       # 요약만
"""

from __future__ import annotations

import argparse
import math
import statistics
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.confirmation_arm import ARM_BASE, ARM_C_OFFSET
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import BacktestConfig, Trade
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import MIN_TRADES_PER_SYMBOL
from backtest.wan169_leverage_book import CellPayload, arm_key, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS, classify_trades
from backtest.wan370_cost_decomposition import decompose_trade
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan381_exit_scales import GRID_CSV_PATH as WAN381_GRID_PATH
from backtest.wan386_confirmation_pnl import (
    NEW_THREE,
    ChecksumRow,
    GridRow,
    _compare_segments,
    _pct,
    _r,
    _row_kwargs,
    _short,
    arm_payloads,
    guard_census,
    wallet_defined,
)
from common.costs import Liquidity

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan395_exit_multiple_grid.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan395_leave_one_out.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan395_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan395_exit_multiple_summary.md"

#: 익절 배수 6점. `1.5`가 채택값이다(WAN-81/90) — 개발자가 점을 더하거나 빼지 않는다.
#: ⚠️ 위쪽(2.0·2.5·3.0R)은 WAN-386이 이미 냈고 **단조로 나빠진다**. 이 표는 **아래쪽**만 본다.
MULTIPLES: tuple[float, ...] = (0.4, 0.5, 0.6, 0.8, 1.0, 1.5)

#: 이 이슈가 **새로 여는** 점 — 아무도 안 잰 자리다.
NEW_MULTIPLES: tuple[float, ...] = (0.4, 0.5)

#: 검산 (d)가 덮는 겹침 — WAN-381이 가드 0.30%에서 이미 낸 배수들.
CHECK_MULTIPLES: tuple[float, ...] = (0.6, 0.8, 1.0, 1.5)

ADOPTED_MULTIPLE = 1.5

#: 가장 낮은 배수 — 「이 격자도 안 꺾였다」를 판정할 때 끝점으로 쓴다.
LOW_MULTIPLE = min(MULTIPLES)

#: 「0과 구분되지 않는다」 선 — WAN-366/370 규약.
NOISE_R = 0.005

#: `same_step_tp_net_r_share`를 낼 수 있는 최소 분모 — 분모가 음수·0 언저리면 비율이 부호를
#: 뒤집은 채 나온다(WAN-115 함정 · WAN-336/394와 같은 가드).
MIN_NET_R_DENOM = 10.0

#: 공식 렌즈(§1)와 체결 보수화(§2). 🚨 §2는 **후보를 다시 만들어야** 한다 — 관통 요구가
#: 어느 지정가가 체결되는지를 바꾸므로 배치만으로는 낼 수 없다.
BASELINE_LENS = "baseline"
STRESS_LENS = "pen_5bp"

#: leave-one-out 구간 — `full`과 `oos_warm`(주 수치).
LOO_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

_CROSS_METRICS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "mean_net_r",
    "mean_gross_r",
    "total_return_flat",
    "max_drawdown",
    "guard_cut",
)


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class MultipleRow(GridRow):
    """WAN-381/386 행에 **판정 보조 열**을 더한 것.

    겹치는 열은 이름·정의가 WAN-381과 **같다** — 그래야 검산 (d)가 성립하고 두 표를 한 자로
    읽을 수 있다.
    """

    model_config = ConfigDict(frozen=True)

    lens: str
    """체결 렌즈. `baseline`이 §1이고 `pen_5bp`가 §2다 — **행마다 싣는다**(한 CSV에 두 렌즈가
    섞이므로 라벨이 없으면 잔존율을 잘못 짝지을 수 있다)."""
    net_r_stderr: float
    """거래당 net R의 표준오차. 🚨 **부호를 못 정하는 칸을 그대로 찍기 위한 열**이다 —
    WAN-381 최선이 −0.0023 ± 0.0057이었고 WAN-394 최선이 ＋0.0039 ± 0.0079였다."""
    gross_r: float
    """수수료·펀딩·**슬리피지 전** R(WAN-370 분해). 🚨 `mean_gross_r`(슬리피지 **후**,
    WAN-381/386)와 **다른 자**다 — 검산이 자를 따라 갈리므로 둘 다 싣는다.
    ⚠️ 이 열은 **WAN-396 보정 이후**의 값이라 WAN-388/389/394의 공개 `gross_r`과 직접 비교
    금지다(그쪽은 진입 슬리피지 5bp를 허수로 계상한 판이고 `wan396_*`가 보정표를 낸다)."""
    cost_r: float
    """거래당 총비용 R — 손익분기 승률의 분자에 들어가는 그 값이다."""
    breakeven_win_rate: float
    """`(1 + 비용R) / (1 + 목표R)` — **실제로 넘어야 하는 선**."""
    breakeven_win_rate_zero_cost: float
    """`1 / (1 + 목표R)` — 이슈 본문 표가 쓴 자(비용 0). 두 자를 함께 둬야 「비용이 그 선을
    얼마나 밀어 올리는가」가 보인다."""
    win_rate_margin: float
    """`승률 − 손익분기 승률(비용 반영)`. 음수면 그 배수에서는 구조적으로 못 번다."""
    same_step_tp_trades: int
    """진입한 그 1분 안에 익절한 거래 수(WAN-336). 목표를 당기면 이 낙관에 더 기댄다."""
    same_step_tp_trade_share: float
    """그 거래가 **거래 수**에서 차지하는 몫. 🚨 **이 열이 표의 것**이다 — 분모가 거래 수라
    언제나 정의된다(WAN-394 실측: 1.5R 7% → 0.6R 26%)."""
    same_step_tp_net_r_share: float | None
    """그 거래들이 만든 net R 합 ÷ 전체 net R 합. **분모가 양수이고 충분히 클 때만** 낸다."""

    @field_validator("same_step_tp_net_r_share", mode="before")
    @classmethod
    def _nan_is_withheld(cls, value: object) -> object:
        """🚨 CSV 왕복이 `None`을 **NaN으로 되살린다** — 그걸 그대로 두면 「내지 않는다」는
        가드가 조용히 뚫린다(pandas가 빈 칸을 NaN으로 읽고 pydantic이 그것을 유효한 float으로
        받는다). 실제로 `--from-csv` 요약이 `nan%`를 찍었다.

        「라벨과 동작이 어긋남」(WAN-91/95/112/123/159/194)의 **직렬화 축 변종**이라, 표시하는
        쪽마다 막지 않고 **모델에서 한 번** 되돌린다.
        """
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


class LooRow(MultipleRow):
    """종목 하나(또는 신규 3종목)를 빼고 **지갑을 다시 배치**한 행 (WAN-316 스코프 패턴)."""

    exclude: str


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def _cell_kwargs() -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계).

    🚨 **WAN-386의 `_cell_kwargs`와 글자 그대로 같아야 한다** — 검산 (d)가 그 전제 위에
    선다(후보가 다르면 겹치는 배수가 비트 일치할 리 없다). 5시간짜리 실행을 기다리지 않고
    그 동일성을 잡으려고 회귀 테스트가 **두 호출의 인자를 실제로 캡처해 대조**한다
    (WAN-330 스파이 테스트 패턴).
    """
    return {
        **ADOPTED_CELL_KWARGS,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    }


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    multiples: Sequence[float] = MULTIPLES,
    lens: str = BASELINE_LENS,
    cold_segments: bool = True,
    cache: PayloadCache | None = None,
) -> list[CellPayload]:
    """무거운 패스는 **여기 한 번**이다 — 배수 여섯의 후보가 payload에 함께 실려 나온다.

    🚨 **여섯 점을 한 번에 요청한다.** 나눠 돌리면 payload 캐시가 배수 **합집합**으로 저장
    하는 성질 때문에 매번 미스가 나고(`docs/ops/wan394-payload-cache.md`), 캐시에 남는 것도
    쪼개진 판이라 다음 이슈가 히트하지 못한다.

    `lens`가 `baseline`이면 `fill`을 **넘기지 않는다** — `None`이 곧 공식 렌즈라 WAN-381
    후보와 비트 단위로 같아진다(검산 (d)의 전제).
    """
    fill = None if lens == BASELINE_LENS else harness.fill_preset(lens)
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        engine_check=False,
        fill=fill,
        confirmation_arms=(ARM_BASE,),
        confirmation_multiples=multiples,
        confirmation_offset=ARM_C_OFFSET,
        payload_cache=cache,
        **_cell_kwargs(),  # type: ignore[arg-type]
    )


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    guard: float = ADOPTED_STOP_GUARD,
    compound: bool = False,
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성을 명시한다(한 표가 한 회계).

    `include_reentry=False`가 맞다 — 팔 후보는 base와 재진입을 **이미 합친** 목록이라
    (WAN-386) 켜 두면 재진입이 한 번 더 들어가 이중 계상이 된다.

    `compound=False`(기본)가 이 격자의 판이다 — 이 좌표의 복리 총수익은 −100%에 포화해 점을
    구분하지 못한다(WAN-346 §2). 검산 (a-2)만 복리를 켜 채택 북과 대조한다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=False,
        min_stop_distance_fraction=guard,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def _cfg() -> BacktestConfig:
    """비용 분해가 쓰는 설정 — 🚨 배치와 **같은** 익절 청산 유동성이라야 항등식이 닫힌다."""
    return harness.build_config(
        harness.DEFAULT_TIMEFRAMES[0],
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# 행 만들기
# --------------------------------------------------------------------------- #


def _net_or_none(trade: Trade, placement: PlacedSetup) -> float | None:
    """리스크 금액이 0이면 R로 정규화할 수 없다 — `_row_kwargs`와 **같은 규칙**으로 뺀다
    (표준오차가 평균과 다른 표본에서 나오면 그 ± 는 그 평균의 것이 아니다)."""
    return None if placement.risk_amount <= 0 else net_r(trade, placement)


def _row_zero_cost_breakeven(multiple: float) -> float:
    """`1 / (1 + 목표R)` — **비용 0** 판의 손익분기 승률(이슈 본문 표가 쓴 자).

    실제로 넘어야 하는 선은 `breakeven_win_rate`(비용 반영)이고 이 자는 **대조군**이다.
    자를 한 곳에 두는 이유는 표와 테스트가 같은 식을 보게 하기 위해서다.
    """
    return 1.0 / (1.0 + multiple)


def _extra_kwargs(
    segment: BookSegment, cfg: BacktestConfig, *, multiple: float, lens: str
) -> dict[str, object]:
    """판정 보조 열 — 표준오차 · 슬리피지 전 gross · 손익분기 여유 · 같은 분 익절."""
    pairs = segment.trades_with_placements()
    nets = [
        net for trade, placement in pairs if (net := _net_or_none(trade, placement)) is not None
    ]
    grosses: list[float] = []
    costs: list[float] = []
    for trade, placement in pairs:
        risk = placement.risk_amount
        if risk <= 0:
            continue
        parts = decompose_trade(trade, cfg)
        grosses.append(parts.gross / risk)
        costs.append(parts.total_cost / risk)
    same = classify_trades(pairs)
    total_net_r = same["net_r"]
    cost_r = sum(costs) / len(costs) if costs else 0.0
    win_rate = segment.row.win_rate
    breakeven = (1.0 + cost_r) / (1.0 + multiple)
    return {
        "lens": lens,
        "net_r_stderr": (statistics.stdev(nets) / (len(nets) ** 0.5) if len(nets) > 1 else 0.0),
        "gross_r": sum(grosses) / len(grosses) if grosses else 0.0,
        "cost_r": cost_r,
        "breakeven_win_rate": breakeven,
        "breakeven_win_rate_zero_cost": _row_zero_cost_breakeven(multiple),
        "win_rate_margin": win_rate - breakeven,
        "same_step_tp_trades": int(same["tp_trades"]),
        "same_step_tp_trade_share": (int(same["tp_trades"]) / len(pairs) if pairs else 0.0),
        "same_step_tp_net_r_share": (
            same["tp_net_r"] / total_net_r if total_net_r > MIN_NET_R_DENOM else None
        ),
    }


def build_grid(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    multiples: Sequence[float] = MULTIPLES,
    lens: str = BASELINE_LENS,
    segments: Sequence[str] = SEGMENT_ORDER,
    log: bool = True,
) -> list[MultipleRow]:
    """배수 × 구간 — 배치만 반복한다(후보는 이미 있다).

    가드는 **축이 아니라 고정값**이다(WAN-381 §3이 그 축을 닫았다) — 그래서 이 표는 2차원이
    아니라 배수 하나의 **공선**이고, 그 모양이 곧 판정이다.
    """
    cfg = _cfg()
    cut, kept = guard_census(payloads, arm=ARM_BASE, guard=ADOPTED_STOP_GUARD)
    rows: list[MultipleRow] = []
    for multiple in multiples:
        scoped = arm_payloads(payloads, arm=ARM_BASE, multiple=multiple)
        for segment in place(scoped, start_ms=start_ms, end_ms=end_ms, segments=segments):
            rows.append(
                MultipleRow(
                    arm=ARM_BASE,
                    guard=ADOPTED_STOP_GUARD,
                    multiple=multiple,
                    adopted_point=(multiple == ADOPTED_MULTIPLE and lens == BASELINE_LENS),
                    guard_cut=cut,
                    guard_kept=kept,
                    **_row_kwargs(segment, num_symbols=num_symbols),
                    **_extra_kwargs(segment, cfg, multiple=multiple, lens=lens),
                )
            )
        if log:
            print(f"[wan395] {lens} · 배수 {multiple:g}R 배치 완료", flush=True)
    return rows


def build_leave_one_out(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    multiples: Sequence[float],
    lens: str = BASELINE_LENS,
    log: bool = True,
) -> list[LooRow]:
    """지목한 배수마다 종목 하나씩 빼고 **지갑을 다시 배치**한다(WAN-316 스코프 패턴).

    🚨 라벨 필터가 아니다 — 종목을 빼면 그 자본·슬롯을 남은 칸이 쓰므로 **다른 지갑**이 된다.
    WAN-381의 최선(−0.0023R)은 BNB 하나를 빼면 부호가 바뀌었고 WAN-394의 실측 양수
    (＋0.0039R)는 ETH 하나를 빼면 −0.0064R로 넘어갔다.
    """
    cfg = _cfg()
    rows: list[LooRow] = []
    all_symbols = sorted({_short(p.symbol) for p in payloads})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    for multiple in multiples:
        scoped = arm_payloads(payloads, arm=ARM_BASE, multiple=multiple)
        for drop_label, dropped in drops:
            drop = {s.upper() for s in dropped}
            kept_payloads = [p for p in scoped if _short(p.symbol) not in drop]
            if not kept_payloads:
                continue
            cut, kept = guard_census(
                [p for p in payloads if _short(p.symbol) not in drop],
                arm=ARM_BASE,
                guard=ADOPTED_STOP_GUARD,
            )
            for segment in place(
                kept_payloads, start_ms=start_ms, end_ms=end_ms, segments=list(LOO_SEGMENTS)
            ):
                rows.append(
                    LooRow(
                        arm=ARM_BASE,
                        guard=ADOPTED_STOP_GUARD,
                        multiple=multiple,
                        adopted_point=False,
                        exclude=drop_label,
                        guard_cut=cut,
                        guard_kept=kept,
                        **_row_kwargs(segment, num_symbols=len({p.symbol for p in kept_payloads})),
                        **_extra_kwargs(segment, cfg, multiple=multiple, lens=lens),
                    )
                )
        if log:
            print(f"[wan395] leave-one-out {multiple:g}R: {len(drops)}판 완료", flush=True)
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def on_adopted_coordinates(symbols: Sequence[str], timeframes: Sequence[str]) -> bool:
    """이 실행이 **채택 좌표 전부**를 도는가 — 검산 (a-2)·(d)가 성립할 조건.

    🚨 좁혀 돌린 판을 WAN-381 격자와 대조하면 「다른 좌표의 두 표」를 비교하게 되어 차가
    커지고, 그 차가 **배선 오류처럼 보인다**(WAN-386 파일럿에서 실제로 그랬다). 좌표가 다르면
    대조하지 않고 **그 사실을 표에 찍는다** — 조용히 건너뛰지 않는다.
    """
    return set(symbols) == set(harness.DEFAULT_SYMBOLS) and set(timeframes) == set(
        harness.DEFAULT_TIMEFRAMES
    )


def cross_check_wan381(
    rows: Sequence[MultipleRow], *, path: Path = WAN381_GRID_PATH
) -> list[ChecksumRow]:
    """검산 (d) — 겹치는 배수 4점이 `wan381_exit_scales_grid.csv`의 가드 0.30% 행과 비트 일치.

    🚨 **이 검산이 없으면 두 표를 이어 읽을 수 없다.** 이 이슈가 새로 여는 것은 0.4·0.5R
    둘뿐이고, 겹치는 칸이 어긋나면 그 **새로 연 점**이 다른 눈금 위에 서게 된다.
    """
    if not path.exists():
        return [
            ChecksumRow(
                check="(d) WAN-381 격자 대조 — 파일 없음",
                segment="all",
                metric="missing_csv",
                left=1.0,
                right=0.0,
                abs_diff=1.0,
            )
        ]
    frame = pd.read_csv(path)
    ours = {(r.multiple, r.segment): r for r in rows if r.lens == BASELINE_LENS}
    out: list[ChecksumRow] = []
    for rec in frame.to_dict(orient="records"):
        if str(rec["arm"]) != ARM_BASE or float(rec["guard"]) != ADOPTED_STOP_GUARD:
            continue
        multiple = float(rec["multiple"])
        if multiple not in CHECK_MULTIPLES:
            continue
        mine = ours.get((multiple, str(rec["segment"])))
        if mine is None:
            continue
        for metric in _CROSS_METRICS:
            left = float(getattr(mine, metric))
            right = float(rec[metric])
            out.append(
                ChecksumRow(
                    check=f"(d) WAN-381 대조 · {multiple:g}R",
                    segment=str(rec["segment"]),
                    metric=metric,
                    left=left,
                    right=right,
                    abs_diff=abs(left - right),
                )
            )
    if not out:
        out.append(
            ChecksumRow(
                check="(d) WAN-381 격자 대조 — 겹치는 행이 없음",
                segment="all",
                metric="matched_rows",
                left=0.0,
                right=1.0,
                abs_diff=1.0,
            )
        )
    return out


def run_checksum(
    payloads: Sequence[CellPayload],
    rows: Sequence[MultipleRow],
    *,
    start_ms: int,
    end_ms: int,
    multiples: Sequence[float] = MULTIPLES,
    cross_check: bool = True,
    log: bool = True,
) -> list[ChecksumRow]:
    """네 검산. (a)는 **셋업 집합 동일 + 지갑 동일** 두 겹으로 낸다."""
    checks: list[ChecksumRow] = []

    # (a-1) 기준 팔의 후보 집합 ≡ 엔진이 낸 base + 재진입 (칸마다 · 진입·청산까지).
    mismatched = 0
    for payload in payloads:
        for segment_name in payload.candidates:
            engine = [
                *payload.candidates[segment_name],
                *payload.reentry_candidates.get(segment_name, ()),
            ]
            derived = payload.arm_candidates[arm_key(ARM_BASE, ADOPTED_MULTIPLE)].get(
                segment_name, ()
            )
            left = sorted(
                (c.entry_time, c.entry_price, c.exit_time, c.exit_price, c.reason.value)
                for c in engine
            )
            right = sorted(
                (c.entry_time, c.entry_price, c.exit_time, c.exit_price, c.reason.value)
                for c in derived
            )
            if left != right:
                mismatched += 1
    checks.append(
        ChecksumRow(
            check="(a-1) 기준 팔 후보 ≡ 엔진 base+재진입 (칸·구간별)",
            segment="all",
            metric="mismatched_cells",
            left=float(mismatched),
            right=0.0,
            abs_diff=float(mismatched),
        )
    )

    # (a-2) 채택 점의 지갑 ≡ 인자 없는 채택 북(복리 켬).
    if log:
        print("[wan395] 검산 (a-2) — 채택 점 지갑 ≡ 채택 북 지갑(복리 켬)", flush=True)
    left_segments = {
        s.segment: s
        for s in place(
            arm_payloads(payloads, arm=ARM_BASE, multiple=ADOPTED_MULTIPLE),
            start_ms=start_ms,
            end_ms=end_ms,
            segments=list(SEGMENT_ORDER),
            compound=True,
        )
    }
    proxied, _note = apply_funding_proxy(payloads)
    right_segments = {
        s.segment: s
        for s in iter_book_segments(
            proxied,
            book=LeverageBookParams(),
            segments=list(SEGMENT_ORDER),
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
            min_stop_distance_fraction=ADOPTED_STOP_GUARD,
            compound_sizing=True,
            take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        )
    }
    checks.extend(
        _compare_segments(left_segments, right_segments, check="(a-2) 채택 점 지갑 ≡ 채택 북 지갑")
    )

    # (b) 여섯 배수의 진입 집합이 비트 일치 — 익절은 청산만 바꾼다(WAN-137/143 훅).
    entry_sets = {
        tuple(
            (c.entry_time, c.entry_price)
            for p in payloads
            for c in p.arm_candidates[arm_key(ARM_BASE, m)].get("full", ())
        )
        for m in multiples
    }
    checks.append(
        ChecksumRow(
            check=f"(b) 배수 불변 진입 집합 ({len(multiples)}점)",
            segment="full",
            metric="distinct_entry_sets",
            left=float(len(entry_sets)),
            right=1.0,
            abs_diff=abs(len(entry_sets) - 1.0),
        )
    )

    # (c) 진입 유동성이 전부 메이커 — 라벨이 아니라 후보의 값으로(WAN-370).
    wrong = sum(
        1
        for p in payloads
        for c in p.arm_candidates[arm_key(ARM_BASE, ADOPTED_MULTIPLE)].get("full", ())
        if c.entry_liquidity is not Liquidity.MAKER
    )
    checks.append(
        ChecksumRow(
            check=f"(c) 진입 유동성 · {ARM_BASE} = {Liquidity.MAKER.value}",
            segment="full",
            metric="wrong_liquidity",
            left=float(wrong),
            right=0.0,
            abs_diff=float(wrong),
        )
    )

    # (d) 겹치는 배수 4점 ≡ WAN-381 격자 — **채택 좌표를 돌 때만** 성립한다.
    if cross_check:
        checks.extend(cross_check_wan381(rows))
    else:
        checks.append(
            ChecksumRow(
                check="(d) WAN-381 격자 대조 — 좌표가 달라 **건너뜀**(좁혀 돈 실행)",
                segment="all",
                metric="skipped",
                left=1.0,
                right=1.0,
                abs_diff=0.0,
            )
        )
    return checks


# --------------------------------------------------------------------------- #
# 판정 — 공선의 모양
# --------------------------------------------------------------------------- #


def pick(
    rows: Sequence[MultipleRow], *, multiple: float, segment: str, lens: str = BASELINE_LENS
) -> MultipleRow | None:
    for row in rows:
        if row.multiple == multiple and row.segment == segment and row.lens == lens:
            return row
    return None


def curve(
    rows: Sequence[MultipleRow], *, segment: str, lens: str = BASELINE_LENS
) -> list[tuple[float, MultipleRow]]:
    """배수 공선 — 낮은 배수부터."""
    out: list[tuple[float, MultipleRow]] = []
    for multiple in MULTIPLES:
        row = pick(rows, multiple=multiple, segment=segment, lens=lens)
        if row is not None:
            out.append((multiple, row))
    return out


def sign_is_decided(row: MultipleRow) -> bool:
    """부호를 말해도 되는가 — |평균| > 2×표준오차 (WAN-394 §1이 코드로 만든 관문).

    🚨 거짓이면 **「0을 넘었다」를 찍지 않는다.** WAN-381 최선이 −0.0023 ± 0.0057이었고
    WAN-394 최선이 ＋0.0039 ± 0.0079였다 — 둘 다 부호가 정해지지 않은 값이다.
    """
    return abs(row.mean_net_r) > 2.0 * row.net_r_stderr


def inflection_verdict(rows: Sequence[MultipleRow], *, segment: str) -> str:
    """완료기준 2 — 이슈가 미리 적어 둔 **세 갈래 중 어느 것인가**. 한 문장으로."""
    points = curve(rows, segment=segment)
    if len(points) < len(MULTIPLES):
        return "판정 불가 — 배수 점이 모자란다(여섯 점이 다 있어야 갈래가 정해진다)."
    values = {m: row.mean_net_r for m, row in points}
    anchor = values[0.6]
    deltas = {m: values[m] - anchor for m in NEW_MULTIPLES}
    best_new = max(deltas, key=lambda m: deltas[m])
    gain = deltas[best_new]
    listing = " → ".join(f"{m:g}R {_r(values[m])}" for m in MULTIPLES)
    if gain > NOISE_R:
        # 🚨 「끝점이 최선」과 「아직 오르는 중」은 다르다 — **마지막 한 걸음**이 그것을 가른다.
        last_step = values[MULTIPLES[0]] - values[MULTIPLES[1]]
        flat = abs(last_step) < NOISE_R
        tail = (
            f"🚨 **다만 마지막 한 걸음({MULTIPLES[1]:g}R → {MULTIPLES[0]:g}R)이 {_r(last_step)}로 "
            f"잡음선 안이라 공선은 이미 평평해졌다** — 「더 내려가면 더 좋아진다」가 아니라 "
            "**「꺾임이 이 근방이고 여기서 멈췄다」**로 읽는다. "
            if flat
            else f"마지막 한 걸음({MULTIPLES[1]:g}R → {MULTIPLES[0]:g}R)도 {_r(last_step)}로 "
            "잡음선 밖이라 아직 오르는 중이다. "
        )
        return (
            f"**갈래 ①: 0.4~0.5R에서 더 좋아진다 — 꺾임이 아직 더 아래다.** {listing}. "
            f"{best_new:g}R이 0.6R보다 {_r(gain)} 낫다(잡음선 {NOISE_R}R 밖). "
            + tail
            + "🚨 **끝점을 「최적값」으로 인용하지 말 것** — 이 격자도 거기서 끝났다. "
            "⚠️ 목표를 당길수록 **손익분기 승률이 가파르게 올라가고**(§2 표) 「같은 분 "
            "익절」 낙관에 더 깊이 기댄다(§4) — 더 파려면 그 둘을 함께 본다."
        )
    if gain < -NOISE_R:
        return (
            f"**갈래 ③: 0.4~0.5R에서 나빠진다 — 0.6R이 진짜 정점이고 배수 축도 닫힌다.** "
            f"{listing}. 두 새 점이 0.6R보다 낮다(가장 나은 쪽도 {_r(gain)}). "
            "가드 축이 WAN-381 §3으로 닫힌 것과 **같은 형식**이다 — 산수가 예고한 대로 "
            "손익분기 승률이 여유를 먼저 소진했다(§2 표)."
        )
    return (
        f"**갈래 ②: 공선이 0.6R 근처에서 평평해진다 — 꺾임이 이 근방이다.** {listing}. "
        f"가장 나은 새 점이 0.6R과 {_r(gain)} 차이로 **잡음선({NOISE_R}R) 안**이라 "
        "「0.4·0.5R이 더 낫다」고 말할 수 없다. ⚠️ **argmax를 채택 권고로 쓰지 않는다**"
        "(WAN-161) — 이 줄은 공선의 **모양**이다."
    )


def best_row(
    rows: Sequence[MultipleRow], *, segment: str, lens: str = BASELINE_LENS
) -> MultipleRow | None:
    subset = [r for r in rows if r.segment == segment and r.lens == lens]
    return max(subset, key=lambda r: r.mean_net_r) if subset else None


def best_multiple(
    rows: Sequence[MultipleRow], *, segment: str, lens: str = BASELINE_LENS
) -> float | None:
    row = best_row(rows, segment=segment, lens=lens)
    return None if row is None else row.multiple


def sign_line(rows: Sequence[MultipleRow], *, segment: str) -> str:
    """완료기준 2 — 최선 점의 부호를 말해도 되는가."""
    row = best_row(rows, segment=segment)
    if row is None:
        return "판정 불가 — 행이 없다."
    body = (
        f"주 구간 최선은 **{row.multiple:g}R {_r(row.mean_net_r)} ± {row.net_r_stderr:.4f}**"
        f"({row.num_trades:,}거래)"
    )
    if not sign_is_decided(row):
        return (
            f"{body} — 🚨 **표준오차의 2배 안이라 부호가 정해지지 않았다.** 「0을 넘었다」도 "
            "「못 넘었다」도 이 표로는 말할 수 없다(WAN-394 §1 관문)."
        )
    word = "양수" if row.mean_net_r > 0 else "음수"
    return f"{body} — 부호는 **{word}로 정해진다**(|평균| > 2×표준오차)."


def flip_rows(rows: Sequence[MultipleRow]) -> tuple[str, str, bool]:
    """완료기준 3 — 앞구간(`is`)에서 고른 배수가 뒷구간(`oos_warm`)에서도 최선인가."""
    is_best = best_multiple(rows, segment="is")
    oos_best = best_multiple(rows, segment=PRIMARY_OOS)
    return (
        f"{is_best:g}R" if is_best is not None else "—",
        f"{oos_best:g}R" if oos_best is not None else "—",
        is_best != oos_best,
    )


def judgment_multiples(rows: Sequence[MultipleRow]) -> list[float]:
    """leave-one-out·§2를 거는 배수 — **채택 점**과 **주 구간 최선 점**."""
    points = [ADOPTED_MULTIPLE]
    best = best_multiple(rows, segment=PRIMARY_OOS)
    if best is not None and best not in points:
        points.append(best)
    return points


def residual_line(rows: Sequence[MultipleRow], *, segment: str) -> str:
    """§2 한 줄 — 최선 점의 `pen_5bp` 잔존율.

    🚨 **기준이 0 언저리거나 부호가 갈리면 비율을 내지 않는다**(WAN-115가 문서화한 함정 —
    잔존율 172%가 「유지」로 읽히던 그 자리). 그때는 **부호와 크기만** 적는다.
    """
    stress = [r for r in rows if r.lens == STRESS_LENS and r.segment == segment]
    if not stress:
        return (
            "§2는 아직 안 돌았다 — `--lens pen_5bp --append`가 채운다(체결 보수화는 후보를 "
            "**다시 만들어야** 하므로 배치만으로는 낼 수 없다)."
        )
    parts: list[str] = []
    for row in sorted(stress, key=lambda r: r.multiple):
        base = pick(rows, multiple=row.multiple, segment=segment)
        if base is None:
            continue
        label = f"{row.multiple:g}R"
        if abs(base.mean_net_r) < NOISE_R or (base.mean_net_r > 0) != (row.mean_net_r > 0):
            parts.append(
                f"**{label}**: {_r(base.mean_net_r)} → {_r(row.mean_net_r)} "
                "(🚨 잔존율을 내지 않는다 — 기준이 0 언저리이거나 부호가 갈린다)"
            )
            continue
        ratio = row.mean_net_r / base.mean_net_r
        drop = row.num_trades / base.num_trades - 1
        parts.append(
            f"**{label}**: {_r(base.mean_net_r)} → {_r(row.mean_net_r)} (잔존 {ratio:.0%} · "
            f"거래 {base.num_trades:,} → {row.num_trades:,} = {drop:+.1%})"
        )
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# 표 · 요약
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def grid_from_csv(path: Path = GRID_CSV_PATH) -> list[MultipleRow]:
    frame = pd.read_csv(path)
    return [MultipleRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def loo_from_csv(path: Path = LOO_CSV_PATH) -> list[LooRow]:
    frame = pd.read_csv(path)
    return [LooRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def checksum_from_csv(path: Path = CHECKSUM_CSV_PATH) -> list[ChecksumRow]:
    frame = pd.read_csv(path)
    return [ChecksumRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def _main_table(rows: Sequence[MultipleRow], *, segment: str) -> list[str]:
    """완료기준 1 — 배수 × 그 구간의 판정 열 전부를 한 표에."""
    out = [
        "| 배수 | 거래당 net R ± 표준오차 | gross(슬립 후) | gross(슬립 전) | 승률 | "
        "손익분기(비용반영) | 여유 | 거래 수 | 같은 분 익절 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for multiple, row in curve(rows, segment=segment):
        mark = " ✅" if row.multiple == ADOPTED_MULTIPLE else ""
        new = " ❌" if multiple in NEW_MULTIPLES else ""
        out.append(
            f"| **{multiple:g}R**{mark}{new} | {_r(row.mean_net_r)} ± {row.net_r_stderr:.4f} | "
            f"{_r(row.mean_gross_r)} | {_r(row.gross_r)} | {_pct(row.win_rate)} | "
            f"{_pct(row.breakeven_win_rate)} | {row.win_rate_margin:+.2%} | "
            f"{row.num_trades:,} | {row.same_step_tp_trade_share:.0%} |"
        )
    return out


def _segment_table(rows: Sequence[MultipleRow]) -> list[str]:
    """구간 4개 × 배수 6점의 거래당 net R — 완료기준 1의 구간 축."""
    out = ["| 구간 | " + " | ".join(f"{m:g}R" for m in MULTIPLES) + " |"]
    out.append("| -- | " + " | ".join(["--:"] * len(MULTIPLES)) + " |")
    for segment in SEGMENT_ORDER:
        cells: list[str] = []
        for multiple in MULTIPLES:
            row = pick(rows, multiple=multiple, segment=segment)
            cells.append(f"{_r(row.mean_net_r)} ({row.num_trades:,})" if row else "—")
        label = f"**{segment}**" if segment == PRIMARY_OOS else segment
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return out


def identity_line(rows: Sequence[MultipleRow], *, segment: str) -> str:
    """`gross_r(슬리피지 전) − cost_r == mean_net_r`이 닫히는가 — **공짜 검산**.

    두 열이 서로 다른 경로에서 온다(`gross_r`·`cost_r`는 WAN-370 비용 분해, `mean_net_r`은
    북이 실제로 실현한 손익 ÷ 리스크 금액). 그래서 이 항등식이 닫힌다는 것은 **분해가 그
    거래를 제대로 읽었다**는 독립 증거이고, 격자를 다시 안 돌려도 낼 수 있다.

    🚨 `mean_gross_r`(슬리피지 **후**)로는 안 닫힌다 — **다른 자**다(WAN-393 §2가 못 박은
    「R이라 불리는 자가 셋」의 이 축 판).
    """
    points = curve(rows, segment=segment)
    if not points:
        return "판정 불가 — 행이 없다."
    worst = max(abs(row.gross_r - row.cost_r - row.mean_net_r) for _m, row in points)
    verdict = "**닫힌다**" if worst < 1e-9 else "🚨 **안 닫힌다 — 확인 필요**"
    return (
        f"항등식 `gross_r(슬리피지 전) − cost_r = 거래당 net R`이 여섯 점 전부에서 {verdict}"
        f"(최대 차 {worst:.2e}). 두 열이 **다른 경로**에서 오므로(분해 vs 북이 실현한 손익) "
        "이건 공짜로 얻은 독립 검산이다. ⚠️ `gross(슬립 후)` 열로는 안 닫힌다 — **다른 자**다."
    )


def same_step_share_cell(row: MultipleRow) -> str:
    """§4의 「net R 몫」 칸 — 🚨 **100%를 넘으면 퍼센트로 적지 않는다**.

    이 좌표는 거래당 기대값이 0 언저리라 분모(구간 전체 net R 합)가 작다. 그러면 「같은 분
    익절」이 만든 R이 **분모보다 커져** `1,977%` 같은 수가 나오는데, 그것은 계산이 틀린 게
    아니라 *「그 거래들이 순손익 전부를 만들고 나머지 거래는 합쳐서 손실」*이라는 뜻이다.
    퍼센트로 적으면 「40%쯤이겠거니」로 읽히므로 **배수와 문장으로 바꿔 적는다**
    (WAN-115가 잔존율 172%를 「유지」로 읽던 자리에서 세운 관행의 이 축 판).

    ⚠️ 음수도 같다 — 분모가 음수면 부호가 뒤집힌 채 나온다.
    """
    share = row.same_step_tp_net_r_share
    if share is None or math.isnan(share):
        return "—(분모가 뜻을 잃음)"
    if share < 0.0:
        return f"🚨 {share:.0%}(분모가 음수 — 읽지 말 것)"
    if share > 1.0:
        return f"🚨 ×{share:.1f} — 순손익 전부보다 크다"
    return f"{share:.0%}"


def gate_line(rows: Sequence[MultipleRow], *, segment: str) -> str:
    """표본 게이트가 어느 점에서 깨지는가. 깨지면 그것 자체가 답의 일부다."""
    subset = [r for r in rows if r.segment == segment and r.lens == BASELINE_LENS]
    broken = [row for row in subset if row.symbols_below_gate > 0]
    if not broken:
        worst = min(subset, key=lambda r: r.min_symbol_trades, default=None)
        tail = (
            f" 가장 얇은 점이 종목당 {worst.min_symbol_trades}거래({worst.multiple:g}R)다."
            if worst is not None
            else ""
        )
        return (
            f"**표본은 이 공선 어디에서도 안 깨진다**(종목당 {MIN_TRADES_PER_SYMBOL}건 게이트, "
            f"{segment}).{tail} 📌 **그것도 답의 일부다** — 목표를 당기면 거래가 오히려 는다."
        )
    listing = ", ".join(f"{r.multiple:g}R({r.symbols_below_gate}종목)" for r in broken)
    return f"🚨 **표본이 깨지는 점이 있다**({segment}) — {listing}. **억지로 살리지 않는다.**"


def build_summary_markdown(
    rows: Sequence[MultipleRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    *,
    elapsed: float | None = None,
    num_cells: int | None = None,
) -> str:
    seg = PRIMARY_OOS
    adopted = pick(rows, multiple=ADOPTED_MULTIPLE, segment=seg)
    best = best_row(rows, segment=seg)
    out: list[str] = [
        "# WAN-395 — 익절 배수의 꺾임을 찾는다 (0.4·0.5R로 격자를 늘린다)",
        "",
        "**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`LeverageBookParams()` 그대로 · "
        "`take_profit_r=1.5`·`min_stop_distance_fraction=0.003` 안 건드렸다 · 핀 없음(WAN-305) · "
        "실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        f"팔은 **하나**(오늘 페이퍼가 돌리는 그 규칙)이고 흔든 것은 **익절 배수 하나**다 — "
        "가드는 채택값 0.30%로 **고정**했다(WAN-381 §3이 그 축을 닫았다). 주 수치는 "
        f"**{seg}**이고 판정 자는 **거래당 net R**이다(WAN-341: 판단은 북에서).",
        "",
        "## 1. 판정 한 문장",
        "",
        inflection_verdict(rows, segment=seg),
        "",
        sign_line(rows, segment=seg),
        "",
        "## 2. 배수 공선 — 주 구간",
        "",
        *_main_table(rows, segment=seg),
        "",
        "❌ = 이 이슈가 **새로 연 점** · ✅ = 채택 배수. "
        "「손익분기(비용반영)」은 `(1 + 비용R) / (1 + 목표R)`이고 「여유」는 `승률 − 그 선`이다.",
        "",
        identity_line(rows, segment=seg),
        "",
    ]
    zero_cost = " · ".join(f"{m:g}R {_row_zero_cost_breakeven(m):.1%}" for m in MULTIPLES)
    out += [
        f"⚠️ **이슈 본문의 손익분기 표는 「비용 0」 판이다**({zero_cost}) — 실제로 넘어야 하는 "
        "선은 위 표의 「비용반영」 열이고, 비용R이 그 선을 그만큼 밀어 올린다.",
        "",
        "## 3. 구간 4개 (앞구간에서 보고 뒷구간에서 확인)",
        "",
        *_segment_table(rows),
        "",
    ]
    is_best, oos_best, flipped = flip_rows(rows)
    out += [
        f"IS 최적 **{is_best}** → {seg} 최적 **{oos_best}** — "
        + (
            "🚨 **뒤집힌다.** 앞구간에서 고른 배수가 뒷구간에서 최선이 아니다"
            if flipped
            else "**안 뒤집힌다**(같은 값)"
        )
        + "(WAN-161: 배수 argmax가 8칸 중 7칸 뒤집힌 선례). 🚨 **어느 쪽이든 argmax는 채택 "
        "근거가 아니다** — 뒷구간은 고르는 축이 아니다.",
        "",
        "## 4. 「같은 분 익절」 몫 — 목표를 당기면 낙관에 더 기댄다",
        "",
        "| 배수 | 같은 분 익절 거래 | 거래 수 몫 | net R 몫 |",
        "| -- | --: | --: | --: |",
    ]
    for multiple, row in curve(rows, segment=seg):
        out.append(
            f"| {multiple:g}R | {row.same_step_tp_trades:,} | "
            f"{row.same_step_tp_trade_share:.0%} | {same_step_share_cell(row)} |"
        )
    out += [
        "",
        "🚨 **진입한 그 1분 안의 익절은 「저가 먼저·고가 나중」을 가정한 값이고, 틱이 지지하는 "
        "것은 그중 약 30%뿐이다**(WAN-336/348/359). 목표를 당길수록 이 몫이 커지므로 **이 열은 "
        "위 표의 net R과 반드시 함께 읽는다.** ⚠️ net R 몫은 **분모가 음수·0 언저리면 내지 "
        "않고**, **100%를 넘으면 퍼센트가 아니라 배수로** 적는다 — 그건 *「그 거래들이 순손익 "
        "전부를 만들고 나머지는 합쳐서 손실」*이라는 뜻이지 「몇 %쯤」이 아니다(WAN-115 함정).",
        "",
        "## 5. 체결 보수화(`pen_5bp`) — §2",
        "",
        residual_line(rows, segment=seg),
        "",
        "🚨 **전 격자에 렌즈를 얹지 않았다** — 판정 점 둘에서만 낸다. `pen_5bp`는 **민감도이지 "
        "실측이 아니다**(큐 우선순위는 틱·호가 WAN-98, Canceled 소관).",
        "",
        "## 6. 거래 수와 표본 게이트",
        "",
        gate_line(rows, segment=seg),
        "",
        "## 7. 위험의 모양 — 판정 점",
        "",
        "| 배수 | 승률 | 복리 끈 수익 | MDD | 최대 동시 칸 | 상한 발동률 | 청산 |",
        "| -- | --: | --: | --: | --: | --: | --: |",
    ]
    ruined = 0
    for multiple in judgment_multiples(rows):
        point = pick(rows, multiple=multiple, segment=seg)
        if point is None:
            continue
        if not wallet_defined(point):
            ruined += 1
            lost = "🚨 정의 상실"
            out.append(
                f"| {multiple:g}R | {_pct(point.win_rate)} | {lost} | {lost} | "
                f"{point.peak_concurrency} | {_pct(point.clamp_rate)} | {lost} |"
            )
            continue
        out.append(
            f"| {multiple:g}R | {_pct(point.win_rate)} | {_pct(point.total_return_flat)} | "
            f"{_pct(point.max_drawdown)} | {point.peak_concurrency} | "
            f"{_pct(point.clamp_rate)} | {point.liquidation_events} |"
        )
    if ruined:
        out += [
            "",
            "🚨 **지갑 층 열이 이 좌표에서 뜻을 잃는다** — 복리를 껐는데도(사이징은 초기 자본 "
            "고정, WAN-346 §2) 잔고가 0을 뚫으므로 「자본 대비 비율」은 분모가 부호를 바꾸며 "
            "무의미해진다. **비율을 내지 않고 「정의 상실」로 찍는다**(WAN-115 관행 · WAN-386과 "
            "같은 술어). **거래당 net R은 이 함정에 안 걸린다** — 분모가 초기 자본으로 사이징된 "
            "값이라 잔고와 무관하다. 🚨 **청산 0건을 안전 신호로 읽지 말 것**(WAN-312 §4 · "
            "WAN-367: 하드 제로는 경보다).",
        ]
    out += [
        "",
        "## 8. 종목 하나씩 빼보기 (지갑 재배치)",
        "",
        "| 배수 | 기준 | 최악(빼면 가장 나빠짐) | 최선 | 부호 유지 |",
        "| -- | --: | -- | -- | -- |",
    ]
    for multiple in judgment_multiples(rows):
        base_row = pick(rows, multiple=multiple, segment=seg)
        subset = [
            r
            for r in loo
            if r.multiple == multiple and r.segment == seg and r.lens == BASELINE_LENS
        ]
        if base_row is None or not subset:
            continue
        worst = min(subset, key=lambda r: r.mean_net_r)
        top = max(subset, key=lambda r: r.mean_net_r)
        same = all((r.mean_net_r >= 0) == (base_row.mean_net_r >= 0) for r in subset)
        out.append(
            f"| {multiple:g}R | {_r(base_row.mean_net_r)} | "
            f"{worst.exclude} {_r(worst.mean_net_r)} | {top.exclude} {_r(top.mean_net_r)} | "
            f"{'예' if same else '🚨 아니오'} |"
        )
    out += [
        "",
        "**지갑을 다시 배치**한다(라벨 필터가 아니다 — WAN-316 스코프 패턴): 종목을 빼면 그 "
        "자본·슬롯을 남은 칸이 쓴다. 🚨 WAN-381의 최선은 **BNB 하나를 빼면 부호가 바뀌었고** "
        "WAN-394의 실측 양수는 **ETH 하나를 빼면 넘어갔다** — 이 열이 그래서 필수다.",
        "",
        "## 9. 검산",
        "",
        "| 검산 | 구간 | 지표 | 좌 | 우 | 차 |",
        "| -- | -- | -- | --: | --: | --: |",
    ]
    worst_diff = 0.0
    for check in checks:
        worst_diff = max(worst_diff, check.abs_diff)
        out.append(
            f"| {check.check} | {check.segment} | {check.metric} | {check.left:.6g} | "
            f"{check.right:.6g} | {check.abs_diff:.2e} |"
        )
    verdict = (
        "**전부 비트 일치**"
        if worst_diff == 0.0
        else f"🚨 **최대 차 {worst_diff:.2e} — 확인 필요**"
    )
    out += [
        "",
        (verdict + ".") if checks else "검산을 안 돌렸다(`--no-checksum`).",
        "",
        "📌 **(d)가 이 표를 WAN-381과 이어 붙인다** — 겹치는 배수 4점(0.6·0.8·1.0·1.5R × 가드 "
        "0.30%)이 비트 일치해야 이 이슈가 **새로 연 점**(0.4·0.5R)을 그 표와 한 줄에 놓을 수 "
        "있다. **점을 늘려도 기존 행이 안 움직인다**는 증거이기도 하다.",
        "",
        "## 10. 경고 (전부 유효)",
        "",
        "* ❌ **익절 배수 기본값 전환 제안이 아니다** — `take_profit_r=1.5`는 WAN-81/90 소관이고 "
        "변경은 **재-베이스라인 = 사용자 결정**이다. 개발자 임의 착수 금지.",
        "* ❌ **가드 축을 다시 흔들지 않았다** — WAN-381 §3이 닫았다(gross 진폭 0.0117R · 다섯 "
        "점 전부 음수). 이 표에서 가드는 채택값 하나로 **고정**이다.",
        "* 🚨 **「흑자」로 기대하지 말 것** — WAN-370은 비용을 0으로 만들어도 시장에서 얻은 것의 "
        "천장이 작다고 냈고, 🔁 **WAN-396이 그 값을 ＋0.09R → ＋0.007R(존폭 필터 끈 좌표는 "
        "＋0.002R)로 정정**했다. 이 축이 노리는 것은 그 구멍의 **일부**다.",
        "* ⚠️ **거래를 줄여서/늘려서 달라 보이는 것**과 구분할 것 — §2·§6의 거래 수가 그 자다.",
        "* ⚠️ **재무장 일정(재진입)은 채택 배수(1.5R)의 것을 쓴다** — 재진입 후보는 base 후보의 "
        "per-cell 시퀀싱에서 나오므로(WAN-261) 배수마다 다시 파생하면 재무장 **시점**까지 "
        "배수를 따라 움직여 축이 둘이 된다. 🚨 **낮은 배수 행의 약 10.7%(WAN-381 실측 1,591건)가 "
        "그 위의 값이고 방향은 모른다** — 그 잔여는 WAN-387 소관이다.",
        "* ⚠️ 판단은 북에서(WAN-341) · 핀 없이(WAN-305) · §1은 전부 `baseline`(닿으면 체결) 낙관 "
        "렌즈 위 값 · 총수익 %는 복리 착시이자 이 좌표에서 **포화**(WAN-169/213) · 6년 MDD는 "
        "폭락 미포함 **바닥선**.",
        "* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *어디서 "
        "이익을 챙기나*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 않는다. **다른 "
        "질문이다.**",
        "* ⚠️ `gross_r`(슬리피지 **전**) 열은 **WAN-396 보정 이후**의 값이다 — WAN-388/389/394의 "
        "공개 `gross_r`과 직접 비교 금지(그쪽은 보정 전 판이다).",
    ]
    if elapsed is not None:
        cell_note = f"{num_cells}칸" if num_cells is not None else "칸 수 미상"
        out += [
            "",
            f"실측 비용: **{elapsed:,.0f}초**({cell_note} · 후보 생성 1회 + 배치 반복). "
            "⚠️ **다른 모듈의 칸 비용을 옮기지 말 것**(WAN-203 → WAN-312 · WAN-383 · WAN-386 "
            "선례 — 이 저장소가 그 실수로 다섯 번 데였다).",
        ]
    if adopted is not None and best is not None and adopted.multiple != best.multiple:
        out += [
            "",
            f"📌 채택 좌표({ADOPTED_MULTIPLE:g}R)는 {_r(adopted.mean_net_r)}이고 격자 최선은 "
            f"{best.multiple:g}R {_r(best.mean_net_r)} — "
            f"차 {_r(best.mean_net_r - adopted.mean_net_r)}.",
        ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _merge(existing: Sequence[MultipleRow], fresh: Sequence[MultipleRow]) -> list[MultipleRow]:
    """같은 (렌즈, 배수, 구간)은 새 행이 이긴다 — `--append`가 덮어쓰기가 아니라 갱신이 되게."""
    keyed = {(r.lens, r.multiple, r.segment): r for r in existing}
    keyed.update({(r.lens, r.multiple, r.segment): r for r in fresh})
    return [keyed[k] for k in sorted(keyed, key=lambda k: (k[0], k[1], SEGMENT_ORDER.index(k[2])))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-395 익절 배수 꺾임 (0.4·0.5R 확장)")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument(
        "--lens",
        default=BASELINE_LENS,
        choices=[BASELINE_LENS, STRESS_LENS],
        help="체결 렌즈 — `pen_5bp`(§2)는 후보를 다시 만든다(판정 점 둘만 낸다)",
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 붙인다(§2용)")
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    parser.add_argument("--pilot", action="store_true", help="한 칸 견적(첫 종목 4h)")
    parser.add_argument("--no-checksum", action="store_true", help="검산을 건너뛴다")
    parser.add_argument("--no-cache", action="store_true", help="payload 디스크 캐시를 안 쓴다")
    args = parser.parse_args(argv)

    if args.from_csv:
        SUMMARY_PATH.write_text(
            build_summary_markdown(
                grid_from_csv(),
                loo_from_csv() if LOO_CSV_PATH.exists() else [],
                checksum_from_csv() if CHECKSUM_CSV_PATH.exists() else [],
            ),
            encoding="utf-8",
        )
        print(f"요약 갱신: {SUMMARY_PATH}")
        return 0

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    if args.pilot:
        symbols, timeframes = symbols[:1], ["4h"]
        print(f"[wan395] 파일럿 — {symbols[0]} 4h (⚠️ 이 값을 격자 견적으로 인용 금지)")

    stress = args.lens == STRESS_LENS
    existing = grid_from_csv() if args.append and GRID_CSV_PATH.exists() else []
    # 🚨 §2는 **판정 점 둘**만 낸다(이슈: 전 격자에 렌즈 축을 얹지 않는다).
    multiples = tuple(judgment_multiples(existing)) if stress and existing else MULTIPLES
    if stress and not existing:
        print("[wan395] ⚠️ §1 격자가 없어 §2가 판정 점을 모른다 — 여섯 점을 전부 돈다.")

    started = time.monotonic()
    payloads = build_payloads(
        symbols,
        timeframes,
        start=args.start,
        end=args.end,
        jobs=args.jobs,
        multiples=multiples,
        lens=args.lens,
        cache=None if args.no_cache else PayloadCache(),
    )
    built = time.monotonic() - started
    print(f"[wan395] 후보 생성 {built:,.0f}초 ({len(payloads)}칸 · {args.lens})", flush=True)

    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)
    num_symbols = len({p.symbol for p in payloads})
    fresh = build_grid(
        payloads,
        start_ms=start_ms,
        end_ms=end_ms,
        num_symbols=num_symbols,
        multiples=multiples,
        lens=args.lens,
    )
    rows = _merge(existing, fresh)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(GRID_CSV_PATH, index=False)

    loo: list[LooRow] = []
    if not stress:
        loo = build_leave_one_out(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            multiples=judgment_multiples(rows),
            lens=args.lens,
        )
        rows_to_frame(loo).to_csv(LOO_CSV_PATH, index=False)
    elif LOO_CSV_PATH.exists():
        loo = loo_from_csv()

    checks: list[ChecksumRow] = []
    if not args.no_checksum and not stress:
        checks = run_checksum(
            payloads,
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
            multiples=multiples,
            cross_check=on_adopted_coordinates(symbols, timeframes),
        )
        rows_to_frame(checks).to_csv(CHECKSUM_CSV_PATH, index=False)
    elif CHECKSUM_CSV_PATH.exists():
        checks = checksum_from_csv()

    elapsed = time.monotonic() - started
    SUMMARY_PATH.write_text(
        build_summary_markdown(rows, loo, checks, elapsed=elapsed, num_cells=len(payloads)),
        encoding="utf-8",
    )
    print(f"[wan395] 완료 {elapsed:,.0f}초 → {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
