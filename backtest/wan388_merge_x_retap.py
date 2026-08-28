"""WAN-388 §2 — 존 병합 × 재탭 차단 2×2 격자 (채택 북).

**묻는 것**: 겹치는 오더블록을 뭉치로 보고 그 뭉치에 한 번만 들어가면 **거래당 net R**이
나아지는가.

격자 — 🚨 **2×2여야 한다**(대각선 둘만 돌리면 효과가 병합에서 왔는지 재탭 차단에서
왔는지 못 가른다 — WAN-131이 볼린저에서 잡은 「선별인가 가격인가」 함정의 이 축 판):

| 팔 | 존 | 재탭 | 재진입 |
| -- | -- | -- | -- |
| `split_every` (분리·매탭) | 원본 | 켬 | 켬 ← **오늘 채택 북**(검산 기준) |
| `merge_every` (병합·매탭) | 병합 | 켬 | 켬 |
| `split_once` (분리·첫탭만) | 원본 | **끔** | 켬 |
| `merge_once` (병합·첫탭만) | 병합 | **끔** | 켬 ← 사용자 원안 |

📌 **재진입(WAN-273 band)은 네 팔 전부에서 채택값 그대로 켠다** — 재진입은 *「익절로 나온
뒤 재무장」*이라 손절 연쇄와 무관하고, 흔들면 축이 둘이 된다. 🚨 **재탭 차단과 재진입은
다른 축이다** — `retap_mode="once"`로도 「익절 후 같은 존 재무장」은 그대로 돈다.

**판정 자**: 거래당 **net R**(+ 거래 수 병기 — 「덜 매매해서 좋아 보이는 것」과 구분).
판정선은 코드 상수다: `merge_once − split_every`의 net R이 `NOISE_R`(0.005R)을 못 넘으면
**채택 권고 없음**이고, 넘더라도 비용 분해에서 **「비용 절감 > gross 감소」**가 확인되지
않으면 메커니즘 미성립으로 적는다(WAN-366/370 노이즈선).

**좌표**: 오늘 채택 그대로 — 12종목 × 4TF · 못 박은 6년 · 존폭 필터 끔(WAN-384) · 인과
취소(WAN-365) · 익절 메이커(WAN-370) · cap_only 5배(WAN-213) · **핀 없음**(WAN-305).
🚨 `combine_obs`는 **탐지** 파라미터이고 `retap_mode`는 시그널 층이라 **네 팔 전부 별도
후보 생성**이다(payload 공유 금지 — WAN-149).

**검산**
* (a) `split_every` ≡ **인자 없는 채택 북** — 같은 payload를 두 배치 경로로(싼 판) ·
  `--checksum-book`이면 좌표만 주고 통째로 다시 만든 독립 판까지.
* (b) 팔 사이 불변: 네 팔의 칸 수·심볼 수가 같다(축이 후보를 바꾸지 실행 좌표를 안 바꾼다).
* (c) 첫탭만 팔의 배치 거래에 **재탭(`tap_index>=1`)이 하나도 없다** — 라벨이 아니라
  **동작**으로 「축이 실제로 걸렸다」를 증명한다(WAN-91/95/112/123/159 부류 방지).

⚠️ **측정 전용** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()` 기본값을
하나도 안 바꾼다. 채택은 재-베이스라인 = **사용자 결정**이고 개발자 임의 착수 금지.
⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값 · 6년 MDD는 폭락 미포함 **바닥선** ·
총수익 %는 복리 착시라 판정 자가 아니다(WAN-346) · **「엣지 없음」(WAN-84/88/111/114/124/
151/201/248/386) 불변**(이 표는 *같은 셋업을 몇 번에 나눠 잡나*를 묻는다 — 다른 질문).

재현::

    uv run python -m backtest.wan388_merge_x_retap --jobs 4
    uv run python -m backtest.wan388_merge_x_retap --arms split_every        # 팔 하나만
    uv run python -m backtest.wan388_merge_x_retap --from-csv                # 요약만
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r, run_book_segments
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import BacktestConfig, Trade
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import MIN_TRADES_PER_SYMBOL
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan370_cost_decomposition import decompose_trade, stop_width_fraction
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan388_merge_retap_census import ADOPTED_COMBINE_OBS, ADOPTED_RETAP_MODE

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan388_merge_x_retap_grid.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan388_leave_one_out.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan388_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan388_merge_x_retap_summary.md"

#: 「0과 구분되지 않는다」 선 — WAN-366/370 규약. **착수 전에 못 박은 판정선**이다.
NOISE_R = 0.005

#: leave-one-out 구간 — `full`(6년 낙폭이 사는 곳)과 `oos_warm`(주 수치).
LOO_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

#: 채택 좌표의 신규 3종목(WAN-182) — 묶어 빼 보는 leave-one-out 라벨.
NEW_THREE: tuple[str, ...] = ("DOGE", "LINK", "LTC")

#: §1 나머지 관문(이슈): 북 층 재탭 거래가 이 선 미만이면 재탭 축은 잴 것이 없다.
BOOK_RETAP_GATE = 0.05


@dataclass(frozen=True)
class Arm:
    """격자의 한 팔. 두 축은 **후보 집합**을 바꾸므로 팔마다 별도 후보 생성이다."""

    name: str
    label: str
    combine_obs: bool
    retap_mode: str

    @property
    def is_adopted(self) -> bool:
        """오늘 채택 북과 같은 팔인가 — 검산(a)의 기준이다."""
        return self.combine_obs == ADOPTED_COMBINE_OBS and self.retap_mode == ADOPTED_RETAP_MODE


ARMS: tuple[Arm, ...] = (
    Arm("split_every", "분리·매탭", combine_obs=False, retap_mode="every_tap"),
    Arm("merge_every", "병합·매탭", combine_obs=True, retap_mode="every_tap"),
    Arm("split_once", "분리·첫탭만", combine_obs=False, retap_mode="once"),
    Arm("merge_once", "병합·첫탭만", combine_obs=True, retap_mode="once"),
)
ARMS_BY_NAME: dict[str, Arm] = {arm.name: arm for arm in ARMS}
ADOPTED_ARM = "split_every"
PROPOSAL_ARM = "merge_once"


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class GridRow(BaseModel):
    """한 (팔, 구간)의 북 집계. 북은 한 지갑이라 심볼 열이 없다(WAN-341)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    label: str
    combine_obs: bool
    retap_mode: str
    segment: str
    adopted_arm: bool
    num_cells: int
    num_symbols: int
    num_trades: int
    """🚨 net R 옆에 **항상** 병기한다 — 「덜 매매해서 좋아 보이는 것」과 구분(WAN-378)."""
    win_rate: float
    mean_net_r: float
    """🚨 **판정 자**. 실현손익 ÷ 그 거래의 리스크 금액(WAN-154 `mean_net_r`와 같은 자)."""
    # 비용 분해(WAN-370 기계) — 전부 R 단위, 비용은 양수
    gross_r: float
    slippage_r: float
    entry_fee_r: float
    take_profit_fee_r: float
    stop_fee_r: float
    other_fee_r: float
    funding_r: float
    cost_r: float
    identity_max_abs: float
    """`gross − 비용합 − net`의 최대 절댓값 — 분해가 닫히는지(0이어야 한다)."""
    # 폭 · 진입가
    stop_width_p50: float
    stop_width_p90: float
    entry_in_zone_p50: float
    """진입가의 **존 근단으로부터의 깊이** 중앙값(0 = 근단 · 1 = 원단 = 무효화 경계).

    🚨 「병합은 클러스터 상단에서 사게 된다 = 진입가 불리」라는 이슈의 우려를 재는 열이다 —
    병합이 이 값을 낮추면(덜 깊게 사면) 그 우려가 실현된 것이다. 후보 층 값이라 배치 여부와
    무관하고, 오프셋 2bp 때문에 음수가 나올 수 있다."""
    # 재탭 귀속(§1-3)
    retap_trades: int
    retap_trade_share: float
    reentry_trades: int
    zone_retap_and_reentry: int
    """같은 존에서 **재탭 거래와 재진입 거래가 둘 다** 난 존의 수 (§1-4 · 버그 주장 아님)."""
    # 위험의 모양
    total_return_flat: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    max_effective_concurrent_risk: float
    liquidation_events: int
    symbols_below_gate: int
    min_symbol_trades: int


class LooRow(GridRow):
    """종목 하나(또는 신규 3종목)를 빼고 **지갑을 다시 배치**한 행 (WAN-316 스코프 패턴)."""

    exclude: str


class ChecksumRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: str
    arm: str
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
    arm: Arm,
    start: str,
    end: str,
    jobs: int,
    cold_segments: bool = True,
) -> list[CellPayload]:
    """이 팔의 후보를 만든다.

    🚨 **팔마다 별도 실행이다** — `combine_obs`는 탐지 파라미터라 값이 다르면 오더블록을
    다시 탐지해야 하고(WAN-149), `retap_mode`는 소비하는 시그널 목록 자체를 바꾼다.
    payload를 팔끼리 돌려 쓰면 라벨만 다른 같은 숫자가 나온다.
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        engine_check=False,
        combine_obs=arm.combine_obs,
        retap_mode=arm.retap_mode,
        observe_zone_width_atr=False,
        **_cell_kwargs(),  # type: ignore[arg-type]
    )


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    compound: bool = False,
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성을 명시한다(한 표가 한 회계).

    `include_reentry=True`가 채택 규칙이다(WAN-273/305) — 네 팔 전부에서 켠다.
    `compound=False`(기본)가 이 격자의 판이다(WAN-346 §2: 복리 총수익은 판정 자가 아니다).
    검산만 복리를 켜 **인자 없는 채택 북**과 대조한다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        min_stop_distance_fraction=ADOPTED_STOP_GUARD,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# 행 만들기
# --------------------------------------------------------------------------- #


def _p(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    return float(statistics.quantiles(ordered, n=100, method="inclusive")[int(q * 100) - 1])


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def entry_in_zone(payloads: Sequence[CellPayload], segment: str) -> float:
    """진입가의 **존 근단으로부터의 깊이** 중앙값 (0 = 근단 · 1 = 원단 = 무효화 경계).

    롱(강세 OB)은 존 **상단**이 근단(먼저 닿는다)이고 하단이 원단(무효화 경계)이며, 숏은
    그 반대다(`ConfluenceParams.zone_limit_price` 정의 그대로). 그래서 롱은
    `(top − 진입가) ÷ 높이`, 숏은 `(진입가 − bottom) ÷ 높이`다.

    🚨 **깊을수록(1에 가까울수록) 진입가는 유리하지만 1R이 작아진다** — 병합은 클러스터를
    두껍게 만들어 같은 절대 가격이 **덜 깊은** 자리가 되게 하고, 그래서 이슈가 「병합은
    클러스터 상단에서 사게 된다 = 진입가 불리」를 우려한 것이다. 다만 진입가는 볼린저가
    재산정하므로(WAN-95/132) 실제로 그렇게 되는지는 **재 봐야 아는 값**이고 이 열이 그걸
    잰다. ⚠️ 음수가 나올 수 있다 — 오프셋 2bp(WAN-112)가 근단보다 체결 쉬운 쪽에 걸기
    때문이다. 후보 층 값이라 배치 여부와 무관하다.
    """
    positions: list[float] = []
    for payload in payloads:
        # 🚨 `oos_warm`은 payload에 **없는 키**다 — 배치가 `full` 후보를 평가 경계로 걸러
        # 만든다(`_segment_cells`). 그대로 `get("oos_warm")`을 하면 조용히 빈 목록이 돌아와
        # 이 열이 전부 0이 된다(주 수치 구간에서 하필).
        source = harness.SEGMENT_FULL if segment == PRIMARY_OOS else segment
        cands = list(payload.candidates.get(source, ()))
        cands += list(payload.reentry_candidates.get(source, ()))
        if segment == PRIMARY_OOS:
            cands = [c for c in cands if c.trigger_time >= payload.boundary_ms]
        for cand in cands:
            ob = cand.order_block
            if ob is None:
                continue
            height = ob.top - ob.bottom
            if height <= 0:
                continue
            if cand.side.sign > 0:
                positions.append((ob.top - cand.entry_price) / height)
            else:
                positions.append((cand.entry_price - ob.bottom) / height)
    return statistics.median(positions) if positions else 0.0


def _zone_overlap(pairs: Sequence[tuple[Trade, PlacedSetup]]) -> int:
    """같은 존에서 **재탭 거래와 재진입 거래가 둘 다** 난 존의 수 (§1-4).

    현행에서 두 경로가 같은 존을 다시 잡을 수 있는지 확인하는 관측이다 — **버그 주장이
    아니다**. `zone_key`가 없는 후보(옛 픽스처·비병합 경로의 일부)는 셀 수 없어 뺀다.
    """
    retapped: set[tuple[str, str, frozenset[int]]] = set()
    rearmed: set[tuple[str, str, frozenset[int]]] = set()
    for _trade, placement in pairs:
        if placement.zone_key is None:
            continue
        key = (placement.cell[0], placement.cell[1], placement.zone_key)
        if placement.is_reentry:
            rearmed.add(key)
        elif placement.tap_index >= 1:
            retapped.add(key)
    return len(retapped & rearmed)


def _row_kwargs(
    segment: BookSegment,
    cfg: BacktestConfig,
    *,
    num_symbols: int,
    entry_position: float,
) -> dict[str, object]:
    row = segment.row
    pairs = segment.trades_with_placements()
    stats = segment.outcome.stats

    counts: dict[str, int] = {}
    for _trade, placement in pairs:
        counts[placement.cell[0]] = counts.get(placement.cell[0], 0) + 1
    per_symbol = list(counts.values()) or [0]
    missing = max(0, num_symbols - len(counts))

    nets: list[float] = []
    gross: list[float] = []
    slip: list[float] = []
    entry_fee: list[float] = []
    tp_fee: list[float] = []
    stop_fee: list[float] = []
    other_fee: list[float] = []
    funding: list[float] = []
    cost: list[float] = []
    identity = 0.0
    widths: list[float] = []
    retaps = reentries = 0
    for trade, placement in pairs:
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
        if placement.is_reentry:
            reentries += 1
        elif placement.tap_index >= 1:
            retaps += 1

    return {
        "segment": segment.segment,
        "num_cells": row.num_cells,
        "num_symbols": num_symbols,
        "num_trades": row.num_trades,
        "win_rate": row.win_rate,
        "mean_net_r": _mean(nets),
        "gross_r": _mean(gross),
        "slippage_r": _mean(slip),
        "entry_fee_r": _mean(entry_fee),
        "take_profit_fee_r": _mean(tp_fee),
        "stop_fee_r": _mean(stop_fee),
        "other_fee_r": _mean(other_fee),
        "funding_r": _mean(funding),
        "cost_r": _mean(cost),
        "identity_max_abs": identity,
        "stop_width_p50": _p(widths, 0.50),
        "stop_width_p90": _p(widths, 0.90),
        "entry_in_zone_p50": entry_position,
        "retap_trades": retaps,
        "retap_trade_share": retaps / len(pairs) if pairs else 0.0,
        "reentry_trades": reentries,
        "zone_retap_and_reentry": _zone_overlap(pairs),
        "total_return_flat": row.total_return,
        "max_drawdown": row.max_drawdown,
        "return_over_mdd": (row.total_return / row.max_drawdown if row.max_drawdown else None),
        "peak_concurrency": row.peak_concurrency,
        "max_concurrent_risk": stats.max_concurrent_risk_ratio,
        "max_effective_concurrent_risk": stats.max_effective_concurrent_risk_ratio,
        "liquidation_events": row.liquidation_events,
        "symbols_below_gate": sum(1 for n in per_symbol if n < MIN_TRADES_PER_SYMBOL) + missing,
        "min_symbol_trades": 0 if missing else min(per_symbol),
    }


def _cfg() -> BacktestConfig:
    """비용 분해가 쓰는 설정 — 🚨 배치와 **같은** 익절 청산 유동성이라야 항등식이 닫힌다."""
    return harness.build_config(
        harness.DEFAULT_TIMEFRAMES[0],
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def _arm_fields(arm: Arm) -> dict[str, object]:
    return {
        "arm": arm.name,
        "label": arm.label,
        "combine_obs": arm.combine_obs,
        "retap_mode": arm.retap_mode,
        "adopted_arm": arm.is_adopted,
    }


def build_arm_rows(
    payloads: Sequence[CellPayload],
    *,
    arm: Arm,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    segments: Sequence[str] = SEGMENT_ORDER,
) -> list[GridRow]:
    cfg = _cfg()
    rows: list[GridRow] = []
    for segment in place(payloads, start_ms=start_ms, end_ms=end_ms, segments=list(segments)):
        rows.append(
            GridRow(
                **_arm_fields(arm),
                **_row_kwargs(
                    segment,
                    cfg,
                    num_symbols=num_symbols,
                    entry_position=entry_in_zone(payloads, segment.segment),
                ),
            )
        )
    return rows


def _short(symbol: str) -> str:
    return symbol.split("/")[0].upper()


def build_leave_one_out(
    payloads: Sequence[CellPayload],
    *,
    arm: Arm,
    start_ms: int,
    end_ms: int,
    log: bool = True,
) -> list[LooRow]:
    """종목 하나씩 빼고 **지갑을 다시 배치**한다 — 라벨 필터가 아니다(WAN-316)."""
    cfg = _cfg()
    rows: list[LooRow] = []
    all_symbols = sorted({_short(p.symbol) for p in payloads})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    for drop_label, dropped in drops:
        drop = {s.upper() for s in dropped}
        kept = [p for p in payloads if _short(p.symbol) not in drop]
        if not kept:
            continue
        for segment in place(kept, start_ms=start_ms, end_ms=end_ms, segments=list(LOO_SEGMENTS)):
            rows.append(
                LooRow(
                    **_arm_fields(arm),
                    exclude=drop_label,
                    **_row_kwargs(
                        segment,
                        cfg,
                        num_symbols=len({p.symbol for p in kept}),
                        entry_position=entry_in_zone(kept, segment.segment),
                    ),
                )
            )
    if log:
        print(f"[wan388] {arm.name}: leave-one-out {len(drops)}판 완료", flush=True)
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


def _compare_segments(
    left: Sequence[BookSegment], right: Sequence[BookSegment], *, check: str, arm: str
) -> list[ChecksumRow]:
    right_by_segment = {seg.segment: seg for seg in right}
    rows: list[ChecksumRow] = []
    for seg in left:
        other = right_by_segment.get(seg.segment)
        if other is None:
            continue
        for metric in _CHECK_METRICS:
            lhs = float(getattr(seg.row, metric))
            rhs = float(getattr(other.row, metric))
            rows.append(
                ChecksumRow(
                    check=check,
                    arm=arm,
                    segment=seg.segment,
                    metric=metric,
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return rows


def check_adopted_identity(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
) -> list[ChecksumRow]:
    """검산 (a) 싼 판 — 같은 payload를 「이 모듈의 배치」와 「채택 북 배치」로 각각 돌린다.

    복리를 켜는 것이 핵심이다 — 인자 없는 채택 북이 복리로 돌기 때문이다(WAN-346).
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
        min_stop_distance_fraction=None,
        compound_sizing=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )
    return _compare_segments(mine, theirs, check="a-1 채택 북(같은 payload)", arm=ADOPTED_ARM)


def check_independent_book(
    payloads: Sequence[CellPayload],
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    segments: Sequence[str],
    log: bool = True,
) -> list[ChecksumRow]:
    """검산 (a) 비싼 판 — 좌표만 주고 **통째로 다시** 만든 독립 판과 대조한다."""
    mine = place(
        payloads,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        segments=list(segments),
        compound=True,
    )
    theirs = run_book_segments(
        symbols,
        timeframes,
        start=start,
        end=end,
        book=LeverageBookParams(),
        segments=list(segments),
        jobs=jobs,
        log=log,
    )
    return _compare_segments(mine, theirs, check="a-2 채택 북(독립 생성)", arm=ADOPTED_ARM)


def check_retap_axis(rows: Sequence[GridRow]) -> list[ChecksumRow]:
    """검산 (c) — 첫탭만 팔의 배치 거래에 재탭이 하나도 없다.

    🚨 **라벨이 아니라 동작으로** 축이 걸렸음을 증명한다. `retap_mode="once"`인데 재탭
    거래가 남아 있으면 그 팔은 이름만 「첫탭만」이고 조용히 채택 팔로 돈 것이다.
    """
    out: list[ChecksumRow] = []
    for row in rows:
        if row.retap_mode != "once":
            continue
        out.append(
            ChecksumRow(
                check="c 재탭 축이 실제로 걸렸나",
                arm=row.arm,
                segment=row.segment,
                metric="retap_trades",
                left=float(row.retap_trades),
                right=0.0,
                abs_diff=float(row.retap_trades),
            )
        )
    return out


def check_arm_invariants(rows: Sequence[GridRow]) -> list[ChecksumRow]:
    """검산 (b) — 팔이 후보를 바꾸지 **실행 좌표**를 안 바꾼다(칸 수·심볼 수 불변)."""
    out: list[ChecksumRow] = []
    base = {(r.segment): r for r in rows if r.arm == ADOPTED_ARM}
    for row in rows:
        ref = base.get(row.segment)
        if ref is None or row.arm == ADOPTED_ARM:
            continue
        for metric in ("num_cells", "num_symbols"):
            lhs = float(getattr(row, metric))
            rhs = float(getattr(ref, metric))
            out.append(
                ChecksumRow(
                    check="b 팔 사이 좌표 불변",
                    arm=row.arm,
                    segment=row.segment,
                    metric=metric,
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return out


# --------------------------------------------------------------------------- #
# 판정 · 요약
# --------------------------------------------------------------------------- #


def _by_arm(rows: Sequence[GridRow], segment: str) -> dict[str, GridRow]:
    return {row.arm: row for row in rows if row.segment == segment}


@dataclass(frozen=True)
class Verdict:
    """§2 판정 줄 — 병합의 몫 · 재탭 차단의 몫 · 상호작용(2×2 잔차)."""

    segment: str
    merge_effect_every: float | None
    merge_effect_once: float | None
    retap_effect_split: float | None
    retap_effect_merge: float | None
    interaction: float | None
    headline: float | None
    """`merge_once − split_every` — 사용자 원안 대 오늘 채택 북."""
    cost_saving: float | None
    """`split_every.cost_r − merge_once.cost_r` (양수 = 비용이 줄었다)."""
    gross_drop: float | None
    """`split_every.gross_r − merge_once.gross_r` (양수 = gross가 깎였다)."""

    @property
    def passes_noise(self) -> bool:
        return self.headline is not None and self.headline > NOISE_R

    @property
    def mechanism_holds(self) -> bool:
        """🚨 net R이 좋아졌으면 **「비용 절감 > gross 감소」**여야 한다. 아니면 우연이다."""
        if self.cost_saving is None or self.gross_drop is None:
            return False
        return self.cost_saving > self.gross_drop


def verdict_for(rows: Sequence[GridRow], segment: str) -> Verdict:
    arms = _by_arm(rows, segment)

    def net(name: str) -> float | None:
        row = arms.get(name)
        return None if row is None else row.mean_net_r

    def diff(a: str, b: str) -> float | None:
        lhs, rhs = net(a), net(b)
        return None if lhs is None or rhs is None else lhs - rhs

    merge_every = diff("merge_every", "split_every")
    merge_once = diff("merge_once", "split_once")
    interaction = None if merge_every is None or merge_once is None else merge_once - merge_every
    base, prop = arms.get(ADOPTED_ARM), arms.get(PROPOSAL_ARM)
    return Verdict(
        segment=segment,
        merge_effect_every=merge_every,
        merge_effect_once=merge_once,
        retap_effect_split=diff("split_once", "split_every"),
        retap_effect_merge=diff("merge_once", "merge_every"),
        interaction=interaction,
        headline=diff(PROPOSAL_ARM, ADOPTED_ARM),
        cost_saving=None if base is None or prop is None else base.cost_r - prop.cost_r,
        gross_drop=None if base is None or prop is None else base.gross_r - prop.gross_r,
    )


def _fmt(value: float | None, *, digits: int = 4, suffix: str = "R") -> str:
    if value is None:
        return "—"
    mark = " (≈0)" if abs(value) < NOISE_R else ""
    return f"{value:+.{digits}f}{suffix}{mark}"


def grid_to_frame(rows: Sequence[GridRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def grid_from_csv(path: Path = GRID_CSV_PATH) -> list[GridRow]:
    frame = pd.read_csv(path)
    return [GridRow.model_validate(rec) for rec in frame.to_dict("records")]


def loo_from_csv(path: Path = LOO_CSV_PATH) -> list[LooRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [LooRow.model_validate(rec) for rec in frame.to_dict("records")]


def checksum_from_csv(path: Path = CHECKSUM_CSV_PATH) -> list[ChecksumRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [ChecksumRow.model_validate(rec) for rec in frame.to_dict("records")]


def build_summary_markdown(
    rows: Sequence[GridRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    *,
    elapsed: float | None = None,
) -> str:
    out: list[str] = []
    out.append("# WAN-388 §2 — 존 병합 × 재탭 차단 2×2 (채택 북)")
    out.append("")
    out.append(
        "⚠️ **측정 전용** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()` "
        '기본값 **불변**(WAN-149 `combine_obs=False` · WAN-123 `retap_mode="every_tap"`). '
        "채택은 재-베이스라인 = **사용자 결정**이고 개발자 임의 착수 금지."
    )
    out.append("")
    if not rows:
        out.append("🚨 격자 행이 없다 — 판정하지 않는다(빈 표에서 결론을 지어내지 않는다).")
        out.append("")
        return "\n".join(out)

    present = [arm for arm in ARMS if any(r.arm == arm.name for r in rows)]
    if len(present) < len(ARMS):
        out.append(
            "🚨 **2×2가 아직 안 찼다** — 지금 있는 팔: "
            + ", ".join(f"`{a.name}`" for a in present)
            + ". 대각선만으로는 효과가 병합에서 왔는지 재탭 차단에서 왔는지 **가를 수 없다**"
            "(WAN-131 함정). 아래 판정 줄은 그만큼만 읽는다."
        )
        out.append("")

    out.append(f"## 1. 격자 (주 수치 `{PRIMARY_OOS}`)")
    out.append("")
    out.append(
        "| 팔 | 구간 | 거래 | 거래당 net R | gross R | 비용 R | 승률 | 손절폭 p50 | "
        "존 내 깊이 | 재탭 거래 | MDD | 청산 |"
    )
    out.append("| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
    for arm in present:
        for segment in SEGMENT_ORDER:
            row = next((r for r in rows if r.arm == arm.name and r.segment == segment), None)
            if row is None:
                continue
            out.append(
                f"| {arm.label} | {segment} | {row.num_trades:,} | {row.mean_net_r:+.4f} | "
                f"{row.gross_r:+.4f} | {row.cost_r:.4f} | {row.win_rate:.2%} | "
                f"{row.stop_width_p50:.3%} | {row.entry_in_zone_p50:.2f} | "
                f"{row.retap_trades:,} ({row.retap_trade_share:.1%}) | "
                f"{row.max_drawdown:.2%} | {row.liquidation_events} |"
            )
    out.append("")

    out.append("## 2. 판정 줄 — 병합의 몫 · 재탭 차단의 몫 · 상호작용")
    out.append("")
    out.append(
        "| 구간 | 병합(매탭) | 병합(첫탭만) | 첫탭만(분리) | 첫탭만(병합) | 상호작용 | "
        "**헤드라인** |"
    )
    out.append("| -- | --: | --: | --: | --: | --: | --: |")
    for segment in SEGMENT_ORDER:
        if not any(r.segment == segment for r in rows):
            continue
        v = verdict_for(rows, segment)
        out.append(
            f"| {segment} | {_fmt(v.merge_effect_every)} | {_fmt(v.merge_effect_once)} | "
            f"{_fmt(v.retap_effect_split)} | {_fmt(v.retap_effect_merge)} | "
            f"{_fmt(v.interaction)} | {_fmt(v.headline)} |"
        )
    out.append("")
    out.append(
        "- **헤드라인** = `병합·첫탭만 − 분리·매탭`(사용자 원안 대 오늘 채택 북). "
        "**상호작용** = 2×2 잔차 — 0에서 멀면 두 축이 서로 기댄다는 뜻이라 한 축만 떼어 "
        "채택하면 안 된다."
    )
    out.append(
        f"- `(≈0)`는 |값| < {NOISE_R}R(WAN-366/370 노이즈선)이라 0과 구분되지 않는다는 표시."
    )
    out.append("")

    out.append("## 3. 결론")
    out.append("")
    v = verdict_for(rows, PRIMARY_OOS)
    if v.headline is None:
        out.append("- 🚨 주 구간 두 팔이 다 없어 **판정하지 않는다**.")
    elif not v.passes_noise:
        out.append(
            f"- **채택 권고 없음** — 헤드라인 {_fmt(v.headline)}이 착수 전에 못 박은 판정선"
            f"(+{NOISE_R}R)을 못 넘는다."
        )
    elif not v.mechanism_holds:
        out.append(
            f"- **채택 권고 없음(메커니즘 미성립)** — 헤드라인 {_fmt(v.headline)}은 선을 넘지만 "
            f"비용 절감({_fmt(v.cost_saving)})이 gross 감소({_fmt(v.gross_drop)})를 못 넘는다. "
            "🚨 이 축이 노린 메커니즘은 **「넓은 손절 = 싼 거래」**(WAN-370)이므로 그 확인 없이는 "
            "우연으로 적는다."
        )
    else:
        out.append(
            f"- 헤드라인 {_fmt(v.headline)}이 판정선을 넘고 **메커니즘도 성립한다**"
            f"(비용 절감 {_fmt(v.cost_saving)} > gross 감소 {_fmt(v.gross_drop)}). "
            "⚠️ **그래도 채택은 사용자 결정이다** — 재-베이스라인이고 개발자 임의 착수 금지."
        )
    out.append("")

    out.append("## 4. 재탭·재진입 귀속 (§1 3·4항)")
    out.append("")
    out.append("| 팔 | 구간 | 거래 | 재탭 거래 | 재탭 몫 | 재진입 거래 | 재탭×재진입 겹친 존 |")
    out.append("| -- | -- | --: | --: | --: | --: | --: |")
    for arm in present:
        row = next((r for r in rows if r.arm == arm.name and r.segment == PRIMARY_OOS), None)
        if row is None:
            continue
        out.append(
            f"| {arm.label} | {PRIMARY_OOS} | {row.num_trades:,} | {row.retap_trades:,} | "
            f"{row.retap_trade_share:.2%} | {row.reentry_trades:,} | "
            f"{row.zone_retap_and_reentry:,} |"
        )
    adopted = next((r for r in rows if r.arm == ADOPTED_ARM and r.segment == PRIMARY_OOS), None)
    if adopted is not None:
        share = adopted.retap_trade_share
        verdict = "넘는다" if share >= BOOK_RETAP_GATE else "**미달**"
        out.append("")
        out.append(
            f"- 🚨 **북 층 재탭 관문**: 채택 팔의 재탭 거래 몫이 {share:.2%}로 "
            f"{BOOK_RETAP_GATE:.0%}를 {verdict}. 「탭이 N% 준다」와 「북 거래가 N% 준다」는 "
            "**다른 수다** — 상당수 탭이 이미 칸 점유로 버려진다."
        )
        out.append(
            f"- **재탭 × 재진입이 같은 존에서 겹친 수**: {adopted.zone_retap_and_reentry:,} "
            "(관측일 뿐 **버그 주장이 아니다** — 두 경로는 설계상 다른 축이다)."
        )
    out.append("")

    if loo:
        out.append("## 5. 종목 leave-one-out (지갑 재배치)")
        out.append("")
        out.append("| 팔 | 구간 | 최악 제외 | 최악 net R | 최선 제외 | 최선 net R | 기준 |")
        out.append("| -- | -- | -- | --: | -- | --: | --: |")
        for arm in present:
            for segment in LOO_SEGMENTS:
                sub = [r for r in loo if r.arm == arm.name and r.segment == segment]
                if not sub:
                    continue
                worst = min(sub, key=lambda r: r.mean_net_r)
                best = max(sub, key=lambda r: r.mean_net_r)
                ref = next((r for r in rows if r.arm == arm.name and r.segment == segment), None)
                base = "—" if ref is None else f"{ref.mean_net_r:+.4f}"
                out.append(
                    f"| {arm.label} | {segment} | {worst.exclude} | {worst.mean_net_r:+.4f} | "
                    f"{best.exclude} | {best.mean_net_r:+.4f} | {base} |"
                )
        out.append("")
        out.append(
            "- 🚨 **라벨 필터가 아니라 지갑 재배치다**(WAN-316) — 종목을 빼면 자본 경합이 "
            "달라져 남은 칸의 거래 자체가 바뀐다."
        )
        out.append("")

    out.append("## 6. 검산")
    out.append("")
    if not checks:
        out.append("- (이번 실행에서는 검산을 돌리지 않았다.)")
    else:
        out.append("| 검산 | 팔 | 구간 | 지표 | 왼쪽 | 오른쪽 | 절대차 |")
        out.append("| -- | -- | -- | -- | --: | --: | --: |")
        for check in checks:
            out.append(
                f"| {check.check} | {check.arm} | {check.segment} | {check.metric} "
                f"| {check.left:.6g} | {check.right:.6g} | {check.abs_diff:.2e} |"
            )
        worst_diff = max(c.abs_diff for c in checks)
        out.append("")
        out.append(
            f"- 최대 절대차 **{worst_diff:.2e}** "
            f"({'비트 일치' if worst_diff == 0 else '⚠️ 불일치 — 배선을 확인할 것'})."
        )
    out.append("")

    out.append("## 7. 경고")
    out.append("")
    out.append(
        "- ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 체결 보수화(`pen_5bp`)는 범위 밖이다."
    )
    out.append(
        "- ⚠️ 6년 MDD는 폭락 미포함 **바닥선**이고, 총수익 %는 복리 착시라 판정 자가 "
        "아니다(WAN-346). 이 표의 총수익은 **복리를 끈** 판이라 채택 북 보고값과 비교 불가다."
    )
    out.append(
        "- ⚠️ **WAN-149 §3의 옛 판정과 셀을 비교하지 말 것** — 그 표는 옛 엔진(소급 취소 · "
        "존폭 필터 켬 · 익절 테이커)이고 `total_return`으로 쟀으며, 스스로 「두 팔은 아예 다른 "
        "거래를 한다」고 적었다."
    )
    out.append(
        "- ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *같은 "
        "셋업을 몇 번에 나눠 잡나*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 않는다."
    )
    out.append(
        "- ⚠️ **병합 팔의 화면에는 알려진 차트 버그가 남는다** — 병합 시그널은 합쳐진 존을 "
        "싣는데 차트 목록은 원본 단위 아카이브라 `zone_key`가 안 맞아 근거 박스가 안 보인다"
        "(WAN-149가 문서화한 한계 · **측정에는 무영향**)."
    )
    if elapsed is not None:
        out.append("")
        out.append(f"실측 소요: {elapsed / 3600:.2f}시간")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-388 §2 존 병합 × 재탭 차단 2×2 격자")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument(
        "--arms",
        default=",".join(arm.name for arm in ARMS),
        help=(
            "돌릴 팔(쉼표). 🚨 실행 순서는 split_every → merge_every → split_once → "
            "merge_once — 검산이 먼저 떨어져 배선을 확인한 뒤 나머지 시간을 쓴다."
        ),
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 CSV에 이어 붙인다(팔을 나눠 돌릴 때). 같은 팔은 새 행이 이긴다.",
    )
    parser.add_argument("--no-loo", action="store_true", help="leave-one-out을 건너뛴다")
    parser.add_argument("--no-checksum", action="store_true")
    parser.add_argument(
        "--checksum-book",
        action="store_true",
        help="비싼 검산 — 좌표만 주고 채택 북을 통째로 다시 만들어 대조한다",
    )
    parser.add_argument(
        "--no-cold-segments",
        action="store_true",
        help="차가운 `is`/`oos` 생성을 건너뛴다(컴퓨트 절반 · 주 수치 oos_warm은 그대로)",
    )
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
    unknown = [a for a in args.arms.split(",") if a and a not in ARMS_BY_NAME]
    if unknown:
        parser.error(f"알 수 없는 팔: {unknown} (지원: {', '.join(ARMS_BY_NAME)})")
    arms = [ARMS_BY_NAME[a] for a in args.arms.split(",") if a]

    cold = not args.no_cold_segments
    segments = SEGMENT_ORDER if cold else ("full", PRIMARY_OOS)
    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)

    started = time.monotonic()
    rows: list[GridRow] = []
    loo: list[LooRow] = []
    checks: list[ChecksumRow] = []
    for arm in arms:
        arm_started = time.monotonic()
        payloads = build_payloads(
            symbols,
            timeframes,
            arm=arm,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            cold_segments=cold,
        )
        print(
            f"[wan388] {arm.name}: 후보 생성 {(time.monotonic() - arm_started) / 60:.1f}분",
            flush=True,
        )
        arm_rows = build_arm_rows(
            payloads,
            arm=arm,
            start_ms=start_ms,
            end_ms=end_ms,
            num_symbols=len(symbols),
            segments=segments,
        )
        rows += arm_rows
        if not args.no_loo:
            loo += build_leave_one_out(payloads, arm=arm, start_ms=start_ms, end_ms=end_ms)
        if arm.is_adopted and not args.no_checksum:
            checks += check_adopted_identity(
                payloads, start_ms=start_ms, end_ms=end_ms, segments=segments
            )
            if args.checksum_book:
                checks += check_independent_book(
                    payloads,
                    symbols,
                    timeframes,
                    start=args.start,
                    end=args.end,
                    jobs=args.jobs,
                    segments=segments,
                )
        print(
            f"[wan388] {arm.name}: 완료 {(time.monotonic() - arm_started) / 60:.1f}분",
            flush=True,
        )

    if args.append:
        kept = [r for r in grid_from_csv() if r.arm not in {a.name for a in arms}]
        rows = kept + rows
        kept_loo = [r for r in loo_from_csv() if r.arm not in {a.name for a in arms}]
        loo = kept_loo + loo
        # (b)·(c)는 **전체 행에서 다시** 계산하므로 옛 판을 이어붙이면 중복이 쌓인다 —
        # 팔에 묶인 (a)만 남긴다.
        checks = [c for c in checksum_from_csv() if c.check.startswith("a-")] + checks

    checks += check_arm_invariants(rows)
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
