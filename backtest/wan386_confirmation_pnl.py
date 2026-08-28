"""WAN-386 §3: 확인 진입 3팔 × 손절폭 가드 3점 × 익절 배수 5점 — 손익 격자.

WAN-383 §1(Phase 1 관문)은 *「확인 규칙이 버는 돈의 대부분을 버리는가」* 하나에 답하고
(아니다 — 15~34%) 세 팔을 전부 살려 보냈다. 🚨 **그것은 「확인 진입이 낫다」를 아무것도
말하지 않는다.** §1이 원리적으로 못 재는 채널이 셋이고 전부 확인 팔에 **불리한** 방향이다:

1. 늦게 사면 **손절폭이 1.39~2.12배**가 되어 고정 1.5R 목표가 멀어진다.
2. 거래 수가 줄어 **공유 자본·슬롯이 재배치**된다(WAN-323이 per-cell과 북 판정이 갈리는
   것을 겪은 그 채널 — 그래서 판단은 북에서, WAN-341).
3. 트리거는 전부 **위쪽**이라 지정가로 걸면 즉시 시장가 체결이다 — **테이커 4bp +
   슬리피지 5bp**를 문다(기준 팔은 메이커 2bp).

이 표가 그 셋을 손익으로 잰다.

## 격자 (사용자 결정 2026-08-28 「3점으로하자」)

| 축 | 값 | 점 |
| -- | -- | -- |
| 진입 시점 | `기준` · `1_봉마감` · `2_교차` · `C_고정오프셋` | 4 |
| 손절폭 가드 | `0.30%`(채택) · `0.40%` · `0.50%` | 3 |
| 익절 배수 | `1.0 · 1.5`(채택) `· 2.0 · 2.5 · 3.0R` | 5 |

= **60조합 × 구간 4개.** 후보 생성은 **한 번**이다 — 팔·배수는 후보를 새로 만들지 않고
진입·청산만 갈아끼우고(`backtest.confirmation_arm`), 가드는 **배치 축**이라 후보를 안
바꾼다(WAN-197). 그래서 이 실행의 무거운 값은 WAN-383 §0·§1과 같은 **한 패스**다.

🚨 **가드 축을 「팔마다 최적 가드 고르기」로 쓰지 않는다** — 그건 앞구간 승자를 찾는
기계다(WAN-366 §0). 용도는 하나: **같은 가드에서 팔끼리 비교하고, 그 순위가 가드를 바꿔도
유지되는지 본다.** 순위가 가드에 따라 뒤집히면 그 자체가 결론이다.

## 가드는 **각 팔의 자기 진입가로** 판정한다 (사용자 사양)

실전 규칙이 그렇고(WAN-305 「백테스트는 페이퍼와 같은 선상」), 그 대가를 열로 낸다 —
확인 팔은 손절폭이 넓어 **기준 팔에서 가드에 잘린 셋업이 부활**한다. 하필 그게 WAN-154가
*「생존율은 최고인데 돈은 잃는다」*고 한 부류다(거래당 −0.414R). `guard_cut`/`guard_kept`
열이 그 교란의 크기이고, 두 팔이 다른 셋업 집합을 매매하는 정도가 거기서 읽힌다.

## 판정 자 · 읽는 법

* **판정 자는 거래당 net R**이다. 총수익 %는 이 좌표에서 복리로 −100%에 포화해 팔을 구분
  하지 못하므로(WAN-169/213) **사이징 기준을 초기 자본에 못 박은**(`compound_sizing=False`)
  판을 낸다 — WAN-346 §2가 만든 그 노브다.
* 🚨 **복리 끈 판의 MDD는 복리 켠 판의 MDD와 비교 불가다**(WAN-346: 베팅은 고정인데 지갑이
  커져 분모만 커진다). 이 표 **안에서** 팔끼리만 비교한다.
* **거래 수를 net R 옆에 병기**한다 — 「덜 매매해서 좋아 보이는 것」과 구분(WAN-378 판정 2).
* **팔끼리는 같은 배수에서** 비교하고 **argmax는 채택 근거가 아니다**(WAN-161: 배수 argmax가
  8칸 중 7칸 IS→OOS 뒤집힘).
* ±0.005R 안은 「0과 구분되지 않는다」(WAN-366/370 규약).

## 검산

* **(a) 기준 팔 @1.5R @0.30% ≡ 인자 없는 채택 북** — 복리를 켠 채 배치해 `book_cli`의 채택
  북과 대조한다. 팔 변환이 엔진의 청산을 다시 만든 것이므로 이 등식이 그 변환의 정본
  검산이다(0이 아니면 팔 표 전체가 다른 눈금 위에 있다).
* **(b) 같은 팔의 다섯 배수는 진입 집합이 비트 일치** — 익절은 청산만 바꾼다(WAN-137/143 훅).
* **(c) 확인 팔은 전부 테이커 · 기준 팔은 전부 메이커** — 「비용을 싸게 잡는 것」이 이 이슈가
  지는 가장 흔한 방식이라(WAN-370) 라벨이 아니라 **후보의 값**으로 센다.

재현::

    uv run python -m backtest.wan386_confirmation_pnl --pilot          # 한 칸 견적
    uv run python -m backtest.wan386_confirmation_pnl --jobs 4         # 48칸 격자
    uv run python -m backtest.wan386_confirmation_pnl --from-csv       # 요약만
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r, run_book_segments
from backtest.confirmation_arm import (
    ARM_BASE,
    ARM_C_OFFSET,
    ARM_CROSS,
    ARM_ORDER,
)
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import Trade
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import MIN_TRADES_PER_SYMBOL
from backtest.wan169_leverage_book import CellPayload, arm_key, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from common.costs import Liquidity

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan386_confirmation_grid.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan386_leave_one_out.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan386_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan386_confirmation_pnl_summary.md"

#: 익절 배수 5점. `1.5`가 채택값이다(WAN-81/90).
MULTIPLES: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5, 3.0)
ADOPTED_MULTIPLE = 1.5

#: 손절폭 가드 3점(분수) — 사용자 결정 2026-08-28. `0.003`이 채택값이다(WAN-79).
GUARD_POINTS: tuple[float, ...] = (ADOPTED_STOP_GUARD, 0.0040, 0.0050)

#: 「0과 구분되지 않는다」 선 — WAN-366/370 규약.
NOISE_R = 0.005

#: leave-one-out 구간 — `full`(6년 낙폭이 사는 곳)과 `oos_warm`(주 수치).
LOO_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

#: 채택 좌표의 신규 3종목(WAN-182) — 묶어 빼 보는 leave-one-out 라벨.
NEW_THREE: tuple[str, ...] = ("DOGE", "LINK", "LTC")


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class GridRow(BaseModel):
    """한 (팔, 가드, 배수, 구간)의 북 집계. 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    arm: str
    guard: float
    multiple: float
    segment: str
    adopted_point: bool
    num_cells: int
    num_symbols: int
    num_trades: int
    """🚨 net R 옆에 **항상** 병기한다 — 「덜 매매해서 좋아 보이는 것」과 구분(WAN-378)."""
    win_rate: float
    mean_net_r: float
    """🚨 **판정 자**. 실현손익 ÷ 그 거래의 리스크 금액(WAN-154 `mean_net_r`와 같은 자)."""
    mean_gross_r: float
    """수수료·펀딩 **전** 기대값. ⚠️ 슬리피지는 체결가에 이미 녹아 있어 여기서 안 빠진다 —
    「어느 배수에서든 0」이면 그것이 결론이라는 이슈의 열이고, 상한은 아니다."""
    total_return_flat: float
    """복리를 끈 총수익률(`compound_sizing=False`, WAN-346 §2). 감 잡는 용."""
    max_drawdown: float
    """⚠️ 복리 끈 판의 낙폭이라 복리 켠 판(채택 북 보고값)과 **비교 불가**다(WAN-346)."""
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    max_effective_concurrent_risk: float
    clamp_rate: float
    """명목 상한에 걸려 축소 진입된 비율 — 채널 ①(확인 팔은 손절이 넓어 이름값에 더 가깝게 벤다)."""
    mean_effective_risk: float
    """거래당 실효 리스크 = 리스크 금액 ÷ 그 순간 공유 자본의 평균."""
    liquidation_events: int
    guard_cut: int
    """이 (팔, 가드)에서 **가드에 잘린** 후보 수 — 완료기준 1-b(비교를 기울이는 교란의 크기)."""
    guard_kept: int
    """가드를 통과한 후보 수. `guard_cut + guard_kept`가 그 팔의 전체 후보다."""
    symbols_below_gate: int
    min_symbol_trades: int


class LooRow(GridRow):
    """종목 하나(또는 신규 3종목)를 빼고 **지갑을 다시 배치**한 행 (WAN-316 스코프 패턴)."""

    exclude: str


class ChecksumRow(BaseModel):
    """검산 — 기준 팔 @1.5R @0.30% ≡ 인자 없는 채택 북."""

    model_config = ConfigDict(frozen=True)

    check: str
    segment: str
    metric: str
    left: float
    right: float
    abs_diff: float


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def _cell_kwargs() -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계)."""
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
    arms: Sequence[str] = ARM_ORDER,
    multiples: Sequence[float] = MULTIPLES,
    cold_segments: bool = True,
) -> list[CellPayload]:
    """무거운 패스는 **여기 한 번**이다 — 팔 × 배수 후보가 payload에 함께 실려 나온다."""
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        engine_check=False,
        confirmation_arms=arms,
        confirmation_multiples=multiples,
        confirmation_offset=ARM_C_OFFSET,
        **_cell_kwargs(),  # type: ignore[arg-type]
    )


def arm_payloads(
    payloads: Sequence[CellPayload], *, arm: str, multiple: float
) -> list[CellPayload]:
    """그 팔·배수의 후보를 `candidates` 자리에 끼운 payload 사본.

    🚨 팔 후보는 base와 재진입을 **이미 합친** 목록이라 배치는 `include_reentry=False`로
    한다 — 켜 두면 기준 팔의 재진입이 한 번 더 들어가 이중 계상이 된다.
    """
    key = arm_key(arm, multiple)
    out: list[CellPayload] = []
    for payload in payloads:
        cands = payload.arm_candidates.get(key)
        if cands is None:
            raise KeyError(f"{payload.symbol} {payload.timeframe}: 팔 후보가 없습니다({key}).")
        out.append(replace(payload, candidates=dict(cands), reentry_candidates={}))
    return out


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    guard: float,
    compound: bool = False,
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성을 명시한다(한 표가 한 회계).

    `compound=False`(기본)가 이 격자의 판이다 — 이 좌표의 복리 총수익은 −100%에 포화해
    팔을 구분하지 못한다(WAN-346 §2). 검산만 복리를 켜 채택 북과 대조한다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=False,
        min_stop_distance_fraction=guard,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def guard_census(payloads: Sequence[CellPayload], *, arm: str, guard: float) -> tuple[int, int]:
    """(가드에 잘린 후보, 통과한 후보) — 완료기준 1-b.

    사이징(`execution.sizing.position_size`)과 **같은 식**이다: 손절 거리가
    `guard × 체결 진입가`보다 좁으면 그 셋업은 매매되지 않는다. 체결가는 슬리피지를 물린
    값이라(확인 팔은 테이커) 팔마다 다르고, 그 차이가 이 열의 존재 이유다.

    배수와 무관하므로(익절은 청산만 바꾼다) 채택 배수 하나로 센다.
    """
    cfg = harness.build_config(harness.DEFAULT_TIMEFRAMES[0])
    costs = cfg.cost_model
    cut = kept = 0
    for payload in payloads:
        for cand in payload.arm_candidates[arm_key(arm, ADOPTED_MULTIPLE)].get("full", ()):
            is_long = cand.side.sign > 0
            fill = costs.entry_fill(
                cand.entry_price, is_long=is_long, liquidity=cand.entry_liquidity
            )
            distance = abs(fill - cand.stop_price)
            if distance <= 0.0 or distance < guard * fill:
                cut += 1
            else:
                kept += 1
    return cut, kept


# --------------------------------------------------------------------------- #
# 행 만들기
# --------------------------------------------------------------------------- #


def _gross_r(trade: Trade, placement: PlacedSetup) -> float:
    """수수료·펀딩 전 R. ⚠️ 슬리피지는 체결가에 녹아 있어 여기서 안 빠진다."""
    if placement.risk_amount <= 0:
        return 0.0
    fees = trade.entry_fee + sum(f.fee for f in trade.exits) + trade.funding_cost
    return (trade.realized_pnl + fees) / placement.risk_amount


def _symbol_trade_counts(segment: BookSegment) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _trade, placement in segment.trades_with_placements():
        counts[placement.cell[0]] = counts.get(placement.cell[0], 0) + 1
    return counts


def _row_kwargs(segment: BookSegment, *, num_symbols: int) -> dict[str, object]:
    row = segment.row
    pairs = segment.trades_with_placements()
    stats = segment.outcome.stats
    counts = _symbol_trade_counts(segment)
    per_symbol = [counts.get(s, 0) for s in {p.cell[0] for _t, p in pairs}] or [0]
    missing = max(0, num_symbols - len(counts))
    nets = [net_r(t, p) for t, p in pairs]
    grosses = [_gross_r(t, p) for t, p in pairs]
    risks = [p.risk_amount / p.equity for _t, p in pairs if p.equity > 0]
    return {
        "segment": segment.segment,
        "num_cells": row.num_cells,
        "num_symbols": num_symbols,
        "num_trades": row.num_trades,
        "win_rate": row.win_rate,
        "mean_net_r": sum(nets) / len(nets) if nets else 0.0,
        "mean_gross_r": sum(grosses) / len(grosses) if grosses else 0.0,
        "total_return_flat": row.total_return,
        "max_drawdown": row.max_drawdown,
        "return_over_mdd": (row.total_return / row.max_drawdown if row.max_drawdown else None),
        "peak_concurrency": row.peak_concurrency,
        "max_concurrent_risk": stats.max_concurrent_risk_ratio,
        "max_effective_concurrent_risk": stats.max_effective_concurrent_risk_ratio,
        "clamp_rate": (stats.clamped_entries / stats.placed if stats.placed else 0.0),
        "mean_effective_risk": sum(risks) / len(risks) if risks else 0.0,
        "liquidation_events": row.liquidation_events,
        "symbols_below_gate": sum(1 for n in per_symbol if n < MIN_TRADES_PER_SYMBOL) + missing,
        "min_symbol_trades": 0 if missing else min(per_symbol),
    }


def build_grid(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    log: bool = True,
) -> list[GridRow]:
    """팔 × 가드 × 배수 × 구간 — 배치만 반복한다(후보는 이미 있다)."""
    rows: list[GridRow] = []
    for arm in ARM_ORDER:
        census = {g: guard_census(payloads, arm=arm, guard=g) for g in GUARD_POINTS}
        for multiple in MULTIPLES:
            scoped = arm_payloads(payloads, arm=arm, multiple=multiple)
            for guard in GUARD_POINTS:
                cut, kept = census[guard]
                for segment in place(
                    scoped,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    segments=list(SEGMENT_ORDER),
                    guard=guard,
                ):
                    rows.append(
                        GridRow(
                            arm=arm,
                            guard=guard,
                            multiple=multiple,
                            adopted_point=(
                                arm == ARM_BASE
                                and guard == ADOPTED_STOP_GUARD
                                and multiple == ADOPTED_MULTIPLE
                            ),
                            guard_cut=cut,
                            guard_kept=kept,
                            **_row_kwargs(segment, num_symbols=num_symbols),
                        )
                    )
        if log:
            print(
                f"[wan386] {arm}: 배치 {len(MULTIPLES) * len(GUARD_POINTS)}조합 완료",
                flush=True,
            )
    return rows


def _short(symbol: str) -> str:
    return symbol.split("/")[0].upper()


def build_leave_one_out(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    log: bool = True,
) -> list[LooRow]:
    """판정 근방(채택 배수 · 채택 가드)에서 종목 하나씩 빼고 **지갑을 다시 배치**한다."""
    rows: list[LooRow] = []
    all_symbols = sorted({_short(p.symbol) for p in payloads})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    for arm in ARM_ORDER:
        scoped = arm_payloads(payloads, arm=arm, multiple=ADOPTED_MULTIPLE)
        for drop_label, dropped in drops:
            drop = {s.upper() for s in dropped}
            kept_payloads = [p for p in scoped if _short(p.symbol) not in drop]
            if not kept_payloads:
                continue
            cut, kept = guard_census(
                [p for p in payloads if _short(p.symbol) not in drop],
                arm=arm,
                guard=ADOPTED_STOP_GUARD,
            )
            for segment in place(
                kept_payloads,
                start_ms=start_ms,
                end_ms=end_ms,
                segments=list(LOO_SEGMENTS),
                guard=ADOPTED_STOP_GUARD,
            ):
                rows.append(
                    LooRow(
                        arm=arm,
                        guard=ADOPTED_STOP_GUARD,
                        multiple=ADOPTED_MULTIPLE,
                        adopted_point=False,
                        exclude=drop_label,
                        guard_cut=cut,
                        guard_kept=kept,
                        **_row_kwargs(segment, num_symbols=len({p.symbol for p in kept_payloads})),
                    )
                )
        if log:
            print(f"[wan386] {arm}: leave-one-out {len(drops)}판 완료", flush=True)
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #

_CHECK_METRICS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "total_return",
    "max_drawdown",
    "peak_concurrency",
    "liquidation_events",
)


def run_checksum(
    payloads: Sequence[CellPayload],
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    independent_book: bool = False,
    log: bool = True,
) -> list[ChecksumRow]:
    """세 검산. (a)는 **셋업 집합 동일 + 지갑 동일** 두 겹으로 낸다.

    📌 **(a)의 비싼 판은 옵트인이다** — 채택 북을 `book_cli.run_book_segments`로 **처음부터
    다시 생성**하면 이 실행의 무거운 값이 그대로 한 번 더 든다(후보 생성이 이 모듈 비용의
    사실상 전부다 — §파일럿). 그래서 기본은 **같은 payload를 두 방식으로 배치**해 대조하고
    (팔 변환이 지갑을 안 바꿨는가), 완전히 독립한 경로와의 대조는 `independent_book=True`로
    연다. ⚠️ 켜지 않았으면 요약이 그 사실을 밝힌다 — 「검산했다」는 라벨만 남기지 않는다.
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    rows: list[ChecksumRow] = []

    # (a-1) 기준 팔의 후보 집합 ≡ 엔진이 낸 base + 재진입 (칸마다 · 진입·청산까지).
    mismatched = 0
    for payload in payloads:
        for segment in payload.candidates:
            engine = [
                *payload.candidates[segment],
                *payload.reentry_candidates.get(segment, ()),
            ]
            derived = payload.arm_candidates[arm_key(ARM_BASE, ADOPTED_MULTIPLE)].get(segment, ())
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
    rows.append(
        ChecksumRow(
            check="(a-1) 기준 팔 후보 ≡ 엔진 base+재진입 (칸·구간별)",
            segment="all",
            metric="mismatched_cells",
            left=float(mismatched),
            right=0.0,
            abs_diff=float(mismatched),
        )
    )

    # (a-2) 그 후보로 배치한 지갑 ≡ 원래 payload로 배치한 채택 북(복리 켬).
    if log:
        print("[wan386] 검산 (a-2) — 기준 팔 지갑 ≡ 채택 북 지갑(복리 켬)", flush=True)
    left_segments = {
        s.segment: s
        for s in place(
            arm_payloads(payloads, arm=ARM_BASE, multiple=ADOPTED_MULTIPLE),
            start_ms=start_ms,
            end_ms=end_ms,
            segments=list(SEGMENT_ORDER),
            guard=ADOPTED_STOP_GUARD,
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
    rows.extend(
        _compare_segments(left_segments, right_segments, check="(a-2) 기준 팔 지갑 ≡ 채택 북 지갑")
    )

    # (b) 같은 팔의 배수들은 진입 집합이 비트 일치 — 익절은 청산만 바꾼다(WAN-137/143).
    for arm in ARM_ORDER:
        sets = {
            tuple(
                (c.entry_time, c.entry_price)
                for p in payloads
                for c in p.arm_candidates[arm_key(arm, m)].get("full", ())
            )
            for m in MULTIPLES
        }
        rows.append(
            ChecksumRow(
                check=f"(b) 배수 불변 진입 집합 · {arm}",
                segment="full",
                metric="distinct_entry_sets",
                left=float(len(sets)),
                right=1.0,
                abs_diff=abs(len(sets) - 1.0),
            )
        )

    # (c) 확인 팔은 전부 테이커 · 기준 팔은 전부 메이커(라벨이 아니라 값으로).
    for arm in ARM_ORDER:
        want = Liquidity.MAKER if arm == ARM_BASE else Liquidity.TAKER
        cands = [
            c
            for p in payloads
            for c in p.arm_candidates[arm_key(arm, ADOPTED_MULTIPLE)].get("full", ())
        ]
        wrong = sum(1 for c in cands if c.entry_liquidity is not want)
        rows.append(
            ChecksumRow(
                check=f"(c) 진입 유동성 · {arm} = {want.value}",
                segment="full",
                metric="wrong_liquidity",
                left=float(wrong),
                right=0.0,
                abs_diff=float(wrong),
            )
        )

    if independent_book:
        # 완전히 독립한 경로 — 후보를 처음부터 다시 만든다(비싸다: 이 모듈 비용 한 판 더).
        if log:
            print("[wan386] 검산 (a-3) — 인자 없는 채택 북(독립 경로 · 후보 재생성)", flush=True)
        independent = {
            s.segment: s
            for s in run_book_segments(
                symbols,
                timeframes,
                start=start,
                end=end,
                book=LeverageBookParams(),
                segments=list(SEGMENT_ORDER),
                jobs=jobs,
                log=log,
            )
        }
        rows.extend(
            _compare_segments(
                left_segments, independent, check="(a-3) 기준 팔 ≡ 인자 없는 채택 북(독립 경로)"
            )
        )
    return rows


def _compare_segments(
    left: dict[str, BookSegment], right: dict[str, BookSegment], *, check: str
) -> list[ChecksumRow]:
    rows: list[ChecksumRow] = []
    for segment in SEGMENT_ORDER:
        a_seg, b_seg = left.get(segment), right.get(segment)
        if a_seg is None or b_seg is None:
            continue
        for metric in _CHECK_METRICS:
            a = float(getattr(a_seg.row, metric))
            b = float(getattr(b_seg.row, metric))
            rows.append(
                ChecksumRow(
                    check=check,
                    segment=segment,
                    metric=metric,
                    left=a,
                    right=b,
                    abs_diff=abs(a - b),
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 표 · 요약
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[GridRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def grid_from_csv(path: Path) -> list[GridRow]:
    frame = pd.read_csv(path)
    return [GridRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def loo_from_csv(path: Path) -> list[LooRow]:
    frame = pd.read_csv(path)
    return [LooRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def checksum_from_csv(path: Path) -> list[ChecksumRow]:
    frame = pd.read_csv(path)
    return [ChecksumRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def wallet_defined(row: GridRow) -> bool:
    """이 행의 **지갑 층** 열(총수익·MDD·수익/MDD·동시 리스크·실효 리스크)이 뜻을 갖는가.

    🚨 복리를 꺼도 이 좌표에서는 지갑이 **0을 뚫는다** — 사이징은 초기 자본에 못 박혀 있지만
    (`compound_sizing=False`, WAN-346 §2) 잔고 자체는 손익을 따라가므로, 6년 음의 기대값을
    5배 북으로 돌리면 자본이 음수가 되고 그 뒤의 「자본 대비 비율」은 전부 뜻을 잃는다
    (분모가 0을 지나며 부호가 뒤집힌다). **비율을 안 내고 「정의 상실」이라 찍는다** —
    WAN-115가 증분 부호 함정에서 세운 관행(뜻을 잃은 비율은 계산하지 않는다)의 이 축 판이다.

    ⚠️ **거래당 net R은 이 함정에 안 걸린다** — 분모(`risk_amount`)가 초기 자본으로 사이징된
    값이라 잔고와 무관하다. 그래서 이 표의 판정 자가 처음부터 그것이다.
    """
    return row.total_return_flat > -1.0 and row.max_drawdown < 1.0


def _pick(
    rows: Sequence[GridRow], *, arm: str, guard: float, multiple: float, segment: str
) -> GridRow | None:
    for row in rows:
        if (
            row.arm == arm
            and row.guard == guard
            and row.multiple == multiple
            and row.segment == segment
        ):
            return row
    return None


def _rank_at(rows: Sequence[GridRow], *, guard: float, segment: str, multiple: float) -> list[str]:
    """그 (가드, 배수, 구간)에서 팔을 거래당 net R 내림차순으로."""
    found = [
        (row.mean_net_r, row.arm)
        for row in rows
        if row.guard == guard and row.segment == segment and row.multiple == multiple
    ]
    return [arm for _r, arm in sorted(found, reverse=True)]


def rank_stability(rows: Sequence[GridRow], *, segment: str, multiple: float) -> str:
    """완료기준 1-c: 가드를 바꿔도 팔 순위가 유지되는가 — 한 문장."""
    orders = {
        guard: _rank_at(rows, guard=guard, segment=segment, multiple=multiple)
        for guard in GUARD_POINTS
    }
    distinct = {tuple(v) for v in orders.values() if v}
    if not distinct:
        return "판정 불가 — 행이 없다."
    if len(distinct) == 1:
        order = " > ".join(next(iter(distinct)))
        return f"**가드를 바꿔도 팔 순위가 유지된다** ({segment} · {multiple:g}R): {order}."
    lines = " / ".join(f"{g:.2%}: {' > '.join(o)}" for g, o in orders.items())
    return (
        f"🚨 **가드에 따라 팔 순위가 뒤집힌다** ({segment} · {multiple:g}R) — {lines}. "
        "그 자체가 결론이다: 「확인 진입이 낫다/아니다」를 가드 하나로 단정할 수 없다."
    )


def is_to_oos_flips(rows: Sequence[GridRow], *, guard: float) -> dict[str, tuple[str, str]]:
    """팔마다 (IS 최적 배수, OOS 최적 배수) — 뒤집힘 세기(WAN-161 관행)."""
    out: dict[str, tuple[str, str]] = {}
    for arm in ARM_ORDER:
        best: dict[str, str] = {}
        for segment in ("is", PRIMARY_OOS):
            found = [
                (row.mean_net_r, row.multiple)
                for row in rows
                if row.arm == arm and row.guard == guard and row.segment == segment
            ]
            best[segment] = f"{max(found)[1]:g}R" if found else "—"
        out[arm] = (best["is"], best[PRIMARY_OOS])
    return out


def _r(value: float) -> str:
    return f"{value:+.4f}R"


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _grid_table(rows: Sequence[GridRow], *, guard: float, segment: str) -> list[str]:
    head = ["| 팔 | " + " | ".join(f"{m:g}R" for m in MULTIPLES) + " |"]
    head.append("| -- | " + " | ".join(["--:"] * len(MULTIPLES)) + " |")
    for arm in ARM_ORDER:
        cells: list[str] = []
        for multiple in MULTIPLES:
            row = _pick(rows, arm=arm, guard=guard, multiple=multiple, segment=segment)
            cells.append(f"{_r(row.mean_net_r)} ({row.num_trades:,})" if row else "—")
        head.append(f"| `{arm}` | " + " | ".join(cells) + " |")
    return head


def _wallet_note(rows: Sequence[GridRow], ruined: Sequence[str], *, segment: str) -> str:
    """지갑 층 열을 어떻게 읽어야 하는지 — 뜻을 잃었으면 그 사실을 **먼저** 말한다."""
    if not ruined:
        return (
            "🚨 **복리 끈 판의 MDD는 복리 켠 판(채택 북 보고값)과 비교 불가**다(WAN-346: 베팅은 "
            "고정인데 지갑이 커져 분모만 커진다) — 이 표 **안에서** 팔끼리만 비교한다."
        )
    worst = min(
        (
            r
            for r in rows
            if r.segment == segment
            and r.guard == ADOPTED_STOP_GUARD
            and r.multiple == ADOPTED_MULTIPLE
        ),
        key=lambda r: r.total_return_flat,
        default=None,
    )
    lost = f"{worst.total_return_flat * 100:,.0f}%" if worst is not None else "—"
    return (
        f"🚨 **지갑 층 열이 이 좌표에서 뜻을 잃었다 — {len(ruined)}팔 전부**(`"
        + "` · `".join(ruined)
        + "`). 복리를 껐는데도(사이징은 초기 자본 고정, WAN-346 §2) **잔고가 0을 뚫는다** — "
        f"최악 팔이 {lost}다. 자본이 음수를 지나면 「자본 대비 비율」(MDD·수익/MDD·동시 "
        "리스크·실효 리스크)은 분모가 부호를 바꾸며 무의미해지므로 **비율을 내지 않고 "
        "「정의 상실」로 찍는다**(WAN-115가 증분 부호 함정에서 세운 관행의 이 축 판). "
        "청산 건수도 같은 이유로 못 읽는다.\n\n"
        "📌 **그래서 판정 자가 처음부터 거래당 net R이다** — 그 분모(`risk_amount`)는 초기 "
        "자본으로 사이징된 값이라 잔고와 무관하고, 승률·거래 수·최대 동시 칸·상한 발동률도 "
        "잔고를 안 본다. 위 표에서 읽을 수 있는 것은 그 넷뿐이다.\n\n"
        "⚠️ **이것은 팔의 성질이 아니라 좌표의 성질이다** — 존폭 필터를 끈 오늘 엔진(WAN-384)의 "
        "채택 북은 6년 `full`에서 **모든 팔이** 초기 자본을 넘겨 잃는다(WAN-378의 「108팔 전부 "
        "음수」·WAN-370의 「비용 0에서도 천장 ＋0.09R」과 같은 자리). 확인 진입이 만든 결과가 "
        "아니다."
    )


def build_summary_markdown(
    rows: Sequence[GridRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    *,
    elapsed: float | None = None,
    num_cells: int | None = None,
) -> str:
    seg = PRIMARY_OOS
    out: list[str] = [
        "# WAN-386 §3 — 확인 진입 3팔 × 가드 3점 × 익절 배수 5점 (손익 격자)",
        "",
        "**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`LeverageBookParams()` 그대로 · "
        "확인 팔은 전부 옵트인 · 실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        f"주 수치는 **{seg}**이고 **판정 자는 거래당 net R**이다(괄호는 거래 수 — 「덜 매매해서 "
        "좋아 보이는 것」과 구분, WAN-378). 총수익률은 복리를 끈 판이다(WAN-346 §2).",
        "",
        "## 1. 거래당 net R — 채택 가드(0.30%)",
        "",
        *_grid_table(rows, guard=ADOPTED_STOP_GUARD, segment=seg),
        "",
        "## 2. 판정 줄 — 확인을 기다린 값어치",
        "",
        "| 줄 | " + " | ".join(f"{m:g}R" for m in MULTIPLES) + " |",
        "| -- | " + " | ".join(["--:"] * len(MULTIPLES)) + " |",
    ]
    for label, left, right in (
        ("`1 − 기준`", "1_봉마감", ARM_BASE),
        ("`2 − 기준`", ARM_CROSS, ARM_BASE),
        ("🚨 `2 − C` (MACD가 실제로 더한 값)", ARM_CROSS, "C_고정오프셋"),
    ):
        cells: list[str] = []
        for multiple in MULTIPLES:
            a = _pick(rows, arm=left, guard=ADOPTED_STOP_GUARD, multiple=multiple, segment=seg)
            b = _pick(rows, arm=right, guard=ADOPTED_STOP_GUARD, multiple=multiple, segment=seg)
            if a is None or b is None:
                cells.append("—")
                continue
            delta = a.mean_net_r - b.mean_net_r
            mark = " (≈0)" if abs(delta) < NOISE_R else ""
            cells.append(f"{_r(delta)}{mark}")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out += [
        "",
        f"±{NOISE_R}R 안은 「0과 구분되지 않는다」(WAN-366/370 규약).",
        "",
        "### 수수료·펀딩 전(gross) 병기 — 채택 배수",
        "",
        "| 팔 | net R | gross R | 거래 수 |",
        "| -- | --: | --: | --: |",
    ]
    for arm in ARM_ORDER:
        row = _pick(rows, arm=arm, guard=ADOPTED_STOP_GUARD, multiple=ADOPTED_MULTIPLE, segment=seg)
        if row is None:
            continue
        out.append(
            f"| `{arm}` | {_r(row.mean_net_r)} | {_r(row.mean_gross_r)} | {row.num_trades:,} |"
        )
    out += [
        "",
        "⚠️ gross는 **수수료·펀딩 전**이고 슬리피지는 체결가에 이미 녹아 있어 빠지지 않는다.",
        "",
        "## 3. 가드 축 — 순위가 흔들리는가 (완료기준 1-c)",
        "",
        rank_stability(rows, segment=seg, multiple=ADOPTED_MULTIPLE),
        "",
        "🚨 **팔마다 최적 가드를 고르지 않는다** — 그건 앞구간 승자를 찾는 기계다(WAN-366 §0).",
        "",
        "| 팔 | " + " | ".join(f"가드 {g:.2%}" for g in GUARD_POINTS) + " | 가드별 잘린 셋업 |",
        "| -- | " + " | ".join(["--:"] * (len(GUARD_POINTS) + 1)) + " |",
    ]
    for arm in ARM_ORDER:
        cells = []
        cuts = []
        for guard in GUARD_POINTS:
            row = _pick(rows, arm=arm, guard=guard, multiple=ADOPTED_MULTIPLE, segment=seg)
            cells.append(f"{_r(row.mean_net_r)} ({row.num_trades:,})" if row else "—")
            cuts.append(f"{row.guard_cut:,}" if row else "—")
        out.append(f"| `{arm}` | " + " | ".join(cells) + " | " + " / ".join(cuts) + " |")
    out += [
        "",
        "⚠️ **두 팔은 다른 셋업 집합을 매매한다** — 확인 팔은 손절폭이 넓어 기준 팔에서 가드에 "
        "잘린 셋업이 부활한다. 하필 그게 WAN-154가 *「생존율은 최고인데 돈은 잃는다」*고 한 "
        "부류다. 위 「잘린 셋업」 수의 팔 사이 차이가 그 교란의 크기다.",
        "",
        "## 4. 위험의 모양 · 채널 ①②(채택 가드 · 채택 배수)",
        "",
        "| 팔 | 승률 | 복리 끈 수익 | MDD | 수익/MDD | 최대 동시 칸 | 최대 동시 리스크 | "
        "상한 발동률 | 실효 리스크 | 청산 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    ruined: list[str] = []
    for arm in ARM_ORDER:
        row = _pick(rows, arm=arm, guard=ADOPTED_STOP_GUARD, multiple=ADOPTED_MULTIPLE, segment=seg)
        if row is None:
            continue
        if not wallet_defined(row):
            ruined.append(arm)
            lost = "🚨 정의 상실"
            out.append(
                f"| `{arm}` | {_pct(row.win_rate)} | {lost} | {lost} | {lost} | "
                f"{row.peak_concurrency} | {lost} | {_pct(row.clamp_rate)} | {lost} | {lost} |"
            )
            continue
        rom = f"{row.return_over_mdd:.2f}x" if row.return_over_mdd is not None else "—"
        out.append(
            f"| `{arm}` | {_pct(row.win_rate)} | {_pct(row.total_return_flat)} | "
            f"{_pct(row.max_drawdown)} | {rom} | {row.peak_concurrency} | "
            f"{_pct(row.max_concurrent_risk)} | {_pct(row.clamp_rate)} | "
            f"{_pct(row.mean_effective_risk)} | {row.liquidation_events} |"
        )
    flips = is_to_oos_flips(rows, guard=ADOPTED_STOP_GUARD)
    flipped = sum(1 for is_best, oos_best in flips.values() if is_best != oos_best)
    out += [
        "",
        _wallet_note(rows, ruined, segment=seg),
        "",
        "## 5. IS→OOS 뒤집힘 (완료기준 4 · WAN-161 관행)",
        "",
        "| 팔 | IS 최적 배수 | OOS 최적 배수 | 뒤집힘 |",
        "| -- | -- | -- | -- |",
    ]
    for arm, (is_best, oos_best) in flips.items():
        out.append(
            f"| `{arm}` | {is_best} | {oos_best} | {'🚨 예' if is_best != oos_best else '아니오'} |"
        )
    out += [
        "",
        f"**{flipped}/{len(flips)}팔이 뒤집힌다.** ⚠️ **argmax는 채택 근거가 아니다** — 이 줄은 "
        "「배수를 앞구간에서 고르면 안 된다」를 세는 데만 쓴다(WAN-161: 8칸 중 7칸 뒤집힘).",
        "",
        "## 6. 종목 하나씩 빼보기 (완료기준 5 · 지갑 재배치)",
        "",
        "| 팔 | 기준 | 최악(빼면 가장 나빠짐) | 최선 | 부호 유지 |",
        "| -- | --: | -- | -- | -- |",
    ]
    for arm in ARM_ORDER:
        base_row = _pick(
            rows, arm=arm, guard=ADOPTED_STOP_GUARD, multiple=ADOPTED_MULTIPLE, segment=seg
        )
        subset = [r for r in loo if r.arm == arm and r.segment == seg]
        if base_row is None or not subset:
            continue
        worst = min(subset, key=lambda r: r.mean_net_r)
        best = max(subset, key=lambda r: r.mean_net_r)
        same = all((r.mean_net_r >= 0) == (base_row.mean_net_r >= 0) for r in subset)
        out.append(
            f"| `{arm}` | {_r(base_row.mean_net_r)} | {worst.exclude} {_r(worst.mean_net_r)} | "
            f"{best.exclude} {_r(best.mean_net_r)} | {'예' if same else '🚨 아니오'} |"
        )
    out += [
        "",
        "## 7. 검산",
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
        verdict + ".",
        "",
        "## 8. 경고 (전부 유효)",
        "",
        "* ⚠️ **가드 기본값 전환 제안이 아니다** — `min_stop_distance_fraction=0.003`은 불변이고 "
        "변경은 **재-베이스라인 = 사용자 결정**(WAN-76/79 소관).",
        "* 🚨 **모멘텀 확인은 이미 한 번 실패했다**(WAN-114/123: RSI 게이트의 쳐냄이 순손해).",
        "* 🚨 **「싸게 사는 것」을 포기하는 방향이다**(WAN-131: 볼린저 기여의 84%가 「가격」).",
        "* 🚨 **「흑자」로 기대하지 말 것**(WAN-370: 비용을 0으로 만들어도 천장이 ＋0.09R · "
        "WAN-378 격자는 108팔 전부 음수).",
        "* ⚠️ **재무장 일정(재진입)은 기준 팔의 것을 쓴다** — 셋업을 팔 사이에서 같게 두는 설계의 "
        "대가다(`backtest.confirmation_arm` 독스트링의 알려진 한계).",
        "* ⚠️ 판단은 북에서(WAN-341) · 핀 없이(WAN-305) · 전부 `baseline`(닿으면 체결) 낙관 렌즈 "
        "위 값이고 체결 보수화(`pen_5bp`)는 범위 밖 · 6년 MDD는 폭락 미포함 **바닥선**.",
        "* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 표는 *어느 시점에 "
        "들어가나*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 않는다. **다른 질문이다.**",
    ]
    if elapsed is not None:
        cell_note = f"{num_cells}칸" if num_cells is not None else "칸 수 미상"
        out += [
            "",
            f"실측 비용: **{elapsed:,.0f}초**({cell_note} · 후보 생성 1회 + 배치 반복). "
            "⚠️ 다른 모듈의 칸 비용을 옮기지 말 것(WAN-203 → WAN-312 · WAN-383 선례).",
        ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-386 §3 확인 진입 손익 격자")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    parser.add_argument("--pilot", action="store_true", help="한 칸 견적(BTC 4h)")
    parser.add_argument("--no-checksum", action="store_true", help="검산을 건너뛴다")
    parser.add_argument(
        "--checksum-book",
        action="store_true",
        help="검산 (a-3): 채택 북을 독립 경로로 **다시 생성**해 대조(이 모듈 비용 한 판 더)",
    )
    args = parser.parse_args(argv)

    if args.from_csv:
        SUMMARY_PATH.write_text(
            build_summary_markdown(
                grid_from_csv(GRID_CSV_PATH),
                loo_from_csv(LOO_CSV_PATH) if LOO_CSV_PATH.exists() else [],
                checksum_from_csv(CHECKSUM_CSV_PATH) if CHECKSUM_CSV_PATH.exists() else [],
            ),
            encoding="utf-8",
        )
        print(f"요약 갱신: {SUMMARY_PATH}")
        return 0

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    if args.pilot:
        symbols, timeframes = symbols[:1], ["4h"]
        print(f"[wan386] 파일럿 — {symbols[0]} 4h (⚠️ 이 값을 격자 견적으로 인용 금지)")

    started = time.monotonic()
    payloads = build_payloads(symbols, timeframes, start=args.start, end=args.end, jobs=args.jobs)
    built = time.monotonic() - started
    print(f"[wan386] 후보 생성 {built:,.0f}초 ({len(payloads)}칸)", flush=True)

    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)
    num_symbols = len({p.symbol for p in payloads})
    rows = build_grid(payloads, start_ms=start_ms, end_ms=end_ms, num_symbols=num_symbols)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    grid_to_frame(rows).to_csv(GRID_CSV_PATH, index=False)

    loo = build_leave_one_out(payloads, start_ms=start_ms, end_ms=end_ms)
    grid_to_frame(loo).to_csv(LOO_CSV_PATH, index=False)

    checks: list[ChecksumRow] = []
    if not args.no_checksum:
        checks = run_checksum(
            payloads,
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            independent_book=args.checksum_book,
        )
        pd.DataFrame([c.model_dump() for c in checks]).to_csv(CHECKSUM_CSV_PATH, index=False)

    elapsed = time.monotonic() - started
    SUMMARY_PATH.write_text(
        build_summary_markdown(rows, loo, checks, elapsed=elapsed, num_cells=len(payloads)),
        encoding="utf-8",
    )
    print(f"[wan386] 완료 {elapsed:,.0f}초 → {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
