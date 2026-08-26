"""WAN-378: 「존의 두께」 §1b 격자 — 존폭 문턱 × 손절폭 가드 × 진입가 3팔.

## 한 줄

존폭 필터 **1.28**(WAN-159)과 손절폭 가드 **0.3%**(WAN-76/79)는 둘 다 **소급 취소 버그가
있던 엔진**의 표를 비교해 골라졌다. WAN-365가 그 버그를 고친 뒤 인과 엔진에서 잰 것은 **그
값에서의 켬/끔뿐**(WAN-366/368)이고 **눈금을 흔들어 본 적이 없다.** 이 모듈이 그 눈금을
흔든다 — WAN-376 §0(지도)이 점을 고를 입력을 냈고 §1a(지름길)가 비용을 N배 낮췄으며,
★결정(2026-08-26 사용자)이 아래 격자를 확정했다.

## 격자 (★결정 확정 — 개발자가 점을 더하거나 빼지 않는다)

| 축 | 값 | 점 |
| -- | -- | -- |
| 존폭 문턱 | `끔` · 2.60 · 1.80 · 1.55 · **1.28(채택)** · 1.15 · 1.00 · 0.90 · 0.80 | 9 |
| 손절폭 가드 | `끔(0.0%)` · 0.25% · **0.30%(채택)** · 0.40% | 4 |
| 진입가 | `proximal`(볼린저 끔) · `mid`(존 중앙) · **볼린저(채택)** | 3 |
| 재진입 | ON(채택) ＋ OFF 대조 | 2 |

🚨 문턱 `0.60`은 **제외**다(§0: 표본 게이트 미달이 오직 그 점에서만 나고 대부분 4h) — 그
제외가 곧 「네 TF 전부에서 판정이 가능하다」는 뜻이다.

## 세 축이 엔진의 어디에 걸리는가 — 비용이 서로 다르다

| 축 | 걸리는 곳 | 다시 해야 하는 것 |
| -- | -- | -- |
| 진입가(3팔) | 후보 **생성**(진입가 → 1R → 익절 목표) | 전부 — **3패스**가 이 격자의 바닥값 |
| 존폭 문턱(9점) | 후보 **선별** | 컷 ＋ **재진입 파생**(WAN-376 §1a 지름길) |
| 손절폭 가드(4점) | **사이징**(배치) | 배치만 — 후보는 안 바뀐다(WAN-197) |
| 재진입 ON/OFF | **배치**(같은 payload를 include만 달리) | 배치만 — 후보는 안 바뀐다 |

📌 그래서 **재진입 OFF 대조 열을 108팔 전부에 냈다** — 이슈가 「좁혀도 된다」고 허락한
자리지만, OFF는 이미 만들어 둔 payload를 `include_reentry=False`로 다시 **배치**하는 것뿐이라
후보 생성 비용이 **0**이다. 좁힐 이유가 없어 전부 낸다(이슈 요구: 어느 범위를 냈는지 명시).

🚨 **재진입 파생은 문턱마다 다시 해야 한다** — 재진입 후보는 base 후보의 per-cell
시퀀싱에서 나오므로(WAN-261), 컷이 부모 집합을 바꾸면 슬롯 점유가 바뀌어 청산 시각이
달라지고 재무장 시점도 달라진다. 컷을 재진입 **뒤에** 걸면 「빠진 셋업의 재진입이 살아남는」
잡종이 된다(WAN-376 §1a가 급소로 지목해 실데이터로 못 박은 자리). 그래서
`wan169.run_cell_variants`의 루프 순서는 반드시 **컷 → 파생**이다.

## 판정 규약 (WAN-376에서 승계)

* **판정 자 = 거래당 net R.** 총수익 %·MDD는 이 좌표에서 포화해 단을 못 가른다. **청산 0건은
  안전 신호가 아니다**(WAN-312 §4).
* **±0.005R 안 = 「0과 구분되지 않는다」**(WAN-366 규약).
* **앞구간(`is`)에서 보고 뒷구간(`oos_warm`)에서 확인** — 뒷구간은 고르는 축이 아니다.
* 🚨 **argmax를 답으로 쓰지 않는다** — **곡선의 모양**(고원인가 절벽인가) ＋ **IS→OOS 뒤집힘
  횟수**가 산출물이다(WAN-161: 배수 argmax가 8칸 중 7칸에서 뒤집혔다).
* 🚨 **표본 게이트** — 종목당 20거래 미만이면 그 행에 표시한다(`wan143.MIN_TRADES_PER_SYMBOL`).
* **판단은 북에서**(WAN-341) · **핀 하나도 없이**(WAN-305).

## 좌표

12종목(`harness.DEFAULT_SYMBOLS`) · 못 박은 6년 창 · 4TF 한 지갑 · cap_only 5배 · 오프셋 2bp ·
유동성 한도 채택값 · **익절 청산 유동성 채택값**(`harness.ADOPTED_TAKE_PROFIT_LIQUIDITY` —
후보 생성 · 배치 · LOO 배치 **셋 다에 명시**, WAN-370/373) · `baseline` 렌즈 · 취소 시점은
인자를 안 줘 채택(인과 `bar_close`, WAN-365)을 물려받는다.

🚨 **북은 이어붙일 수 없다**(WAN-316) — TF를 더하려면 통째로 다시 돈다. 실행의 TF 집합이 곧
그 지갑의 정체이므로 행에 `scope`로 적는다.

## 재현

```
uv run python -m backtest.wan378_zone_thickness_grid --part grid --jobs 4
uv run python -m backtest.wan378_zone_thickness_grid --part checksum --jobs 4
uv run python -m backtest.wan378_zone_thickness_grid --part summary   # 요약만
```

⚠️ 이 격자는 무겁다 — 팔 하나가 후보 생성 1패스 ＋ 문턱 9점의 재진입 파생이다. `--arms`로
팔을 나눠 돌리고 `--append`로 이어 붙일 수 있다(**행 단위 병합**이라 지갑을 쪼개는 것이
아니다 — 팔마다 지갑이 따로다).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r, run_book_segments
from backtest.harness import SEGMENT_IS, SEGMENT_OOS_WARM
from backtest.leverage_book import LeverageBookParams
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import MIN_TRADES_PER_SYMBOL
from backtest.wan169_leverage_book import CellPayload, run_cells_multi, zone_width_label
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan376_zone_thickness import (
    ADOPTED_STOP_GUARD,
    ADOPTED_ZONE_WIDTH,
    assert_adopted_base,
)
from strategy.models import ZoneLimitRef

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan378_thickness_grid.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan378_leave_one_out.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan378_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan378_zone_thickness_grid_summary.md"

#: ★결정 2026-08-26 — 존폭 문턱 9점. `None` = 필터 끔. `0.60`은 **일부러 없다**(§0 표본).
WIDTH_POINTS: tuple[float | None, ...] = (
    None,
    2.60,
    1.80,
    1.55,
    ADOPTED_ZONE_WIDTH,
    1.15,
    1.00,
    0.90,
    0.80,
)

#: ★결정 2026-08-26 — 손절폭 가드 4점(분수). `0.0` = 가드 끔. 양 끝을 넣어 곡선 모양을 본다.
GUARD_POINTS: tuple[float, ...] = (0.0, 0.0025, ADOPTED_STOP_GUARD, 0.0040)

ARM_PROXIMAL = "proximal"
ARM_MID = "mid"
ARM_BOLLINGER = "bollinger"
#: ★결정 2026-08-26 — 진입가 3팔. `bollinger`가 채택 규칙이고 앞의 둘은 볼린저를 끈 팔이다.
ARM_ORDER: tuple[str, ...] = (ARM_PROXIMAL, ARM_MID, ARM_BOLLINGER)

#: leave-one-out을 내는 문턱 — 채택값과 그 양옆. 전 격자를 다시 배치하는 것은 비용만 늘고
#: 「특정 종목이 만든 결과인가」는 채택 근방에서 답이 난다(WAN-312 §LOO와 같은 관행).
LOO_WIDTH_LABELS: tuple[str, ...] = ("1.55", "1.28", "1.15")
#: leave-one-out 구간 — `full`(6년 MDD가 사는 곳)과 `oos_warm`(주 수치).
LOO_SEGMENTS: tuple[str, ...] = (harness.SEGMENT_FULL, SEGMENT_OOS_WARM)
#: WAN-307 합류 3종목 — 「신규 3종목 제외」 판을 함께 낸다.
NEW_THREE: tuple[str, ...] = ("ADA", "DOT", "BCH")

#: WAN-366 규약 — 거래당 net R이 이 폭 안이면 「0과 구분되지 않는다」.
NEAR_ZERO_R = 0.005

GRID_KEYS: tuple[str, ...] = ("scope", "arm", "width_label", "guard", "reentry", "segment")
LOO_KEYS: tuple[str, ...] = (*GRID_KEYS, "exclude")
CHECKSUM_KEYS: tuple[str, ...] = ("scope", "segment", "metric")


def arm_engine(arm: str) -> tuple[bool, ZoneLimitRef | None]:
    """진입가 팔 → (볼린저 켬?, 지정가 기준선).

    🚨 `bollinger` 팔의 기준선은 **`None`(= 채택 기본값 물려받기)** 이다 — `"proximal"`을
    명시로 적으면 그 값이 채택 기본값과 갈라졌을 때 라벨만 「채택」이고 숫자는 옛 값이 된다
    (WAN-305 핀 금지). 볼린저가 켜져 있으면 밴드가 진입가를 덮어쓰므로(WAN-95) 이 팔에서
    기준선은 밴드가 존보다 불리해 존 경계로 접힐 때만 뜻이 있다.
    """
    if arm == ARM_PROXIMAL:
        return False, "proximal"
    if arm == ARM_MID:
        return False, "mid"
    if arm == ARM_BOLLINGER:
        return True, None
    raise ValueError(f"모르는 진입가 팔: {arm!r}")


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class GridRow(BaseModel):
    """한 (팔 × 문턱 × 가드 × 재진입 × 구간)의 채택 북 집계."""

    model_config = ConfigDict(frozen=True)

    scope: str
    """이 지갑의 정체 = TF 집합(북은 이어붙일 수 없다, WAN-316)."""
    arm: str
    width_label: str
    width_threshold: float | None
    guard: float
    reentry: bool
    segment: str
    num_cells: int
    num_symbols: int
    """이 지갑이 실제로 본 종목 수 — 요약 머리글이 이 값에서 파생된다.

    🚨 좌표를 본문에 **숫자로 적어 두면** 유니버스가 움직였을 때 라벨만 낡는다(WAN-318 §6이
    대시보드에서 고친 것과 같은 부류 — 「9종목 × 4TF」가 12종목 위에 떠 있었다)."""
    num_trades: int
    win_rate: float
    total_return: float
    mean_net_r: float
    """판정 자 — 실현손익 ÷ 그 거래의 리스크 금액(WAN-154와 같은 자)."""
    max_drawdown: float
    peak_concurrency: int
    liquidation_events: int
    symbols_below_gate: int
    """이 행에서 거래가 20건 미만인 종목 수(`wan143.MIN_TRADES_PER_SYMBOL`)."""
    min_symbol_trades: int
    adopted_point: bool
    """이 점이 오늘의 채택 좌표(볼린저 × 1.28 × 0.3% × 재진입 ON)인가."""


class LooRow(GridRow):
    """종목 하나(또는 합류 3종목)를 빼고 **지갑을 다시 배치**한 행 (완료기준 4)."""

    exclude: str
    """`-ETH`(그 종목 제외) · `-new3`(합류 3종목 제외). 라벨 필터가 아니라 재배치다."""


class ChecksumRow(BaseModel):
    """검산 — 채택 좌표 팔 ≡ 인자 없는 채택 북 (완료기준 6)."""

    model_config = ConfigDict(frozen=True)

    scope: str
    segment: str
    metric: str
    grid_value: float
    book_value: float
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


def payloads_for_arm(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arm: str,
    *,
    start: str,
    end: str,
    jobs: int,
    thresholds: Sequence[float | None] = WIDTH_POINTS,
) -> dict[str, list[CellPayload]]:
    """한 진입가 팔의 「문턱 라벨 → 칸 payload」 — 무거운 패스는 **여기 한 번**이다.

    `run_cells_multi`가 엔진 필터를 끈 채 후보를 만들고 문턱마다 컷 ＋ 재진입 파생을 한다
    (WAN-376 §1a 지름길). 손절폭 가드·재진입 ON/OFF는 **배치 축**이라 이 단계에 없다.
    """
    bollinger, zone_limit_ref = arm_engine(arm)
    kwargs = _cell_kwargs()
    return run_cells_multi(
        symbols,
        timeframes,
        thresholds=thresholds,
        start=start,
        end=end,
        jobs=jobs,
        bollinger=bollinger,
        zone_limit_ref=zone_limit_ref,
        **kwargs,  # type: ignore[arg-type]
    )


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    guard: float,
    reentry: bool,
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성을 명시한다(한 표가 한 회계).

    `min_stop_distance_fraction`(가드)과 `include_reentry`(재진입)가 이 층의 두 축이다 —
    둘 다 후보를 안 바꾸므로 같은 payload를 다시 배치하는 것으로 끝난다(WAN-197 · WAN-273).
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=reentry,
        min_stop_distance_fraction=guard,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def _symbol_trade_counts(segment: BookSegment) -> dict[str, int]:
    """이 구간에서 종목별 거래 수 — 북은 한 지갑이라 배치 기록에서만 칸을 알 수 있다."""
    counts: dict[str, int] = {}
    for _trade, placement in segment.trades_with_placements():
        symbol = placement.cell[0]
        counts[symbol] = counts.get(symbol, 0) + 1
    return counts


def _row_kwargs(segment: BookSegment, *, num_symbols: int) -> dict[str, object]:
    """`BookSegment` → 행 공통 필드(표본 게이트 포함)."""
    row = segment.row
    pairs = segment.trades_with_placements()
    rs = [net_r(t, p) for t, p in pairs]
    counts = _symbol_trade_counts(segment)
    # 거래가 하나도 없는 종목은 `counts`에 안 나타나므로 0으로 세어야 게이트가 정직하다.
    per_symbol = [counts.get(s, 0) for s in {p.cell[0] for _t, p in pairs}] or [0]
    missing = max(0, num_symbols - len(counts))
    return {
        "segment": segment.segment,
        "num_cells": row.num_cells,
        "num_symbols": num_symbols,
        "num_trades": row.num_trades,
        "win_rate": row.win_rate,
        "total_return": row.total_return,
        "mean_net_r": sum(rs) / len(rs) if rs else 0.0,
        "max_drawdown": row.max_drawdown,
        "peak_concurrency": row.peak_concurrency,
        "liquidation_events": row.liquidation_events,
        "symbols_below_gate": sum(1 for n in per_symbol if n < MIN_TRADES_PER_SYMBOL) + missing,
        "min_symbol_trades": 0 if missing else min(per_symbol),
    }


def _is_adopted(arm: str, threshold: float | None, guard: float, reentry: bool) -> bool:
    return (
        arm == ARM_BOLLINGER
        and threshold == ADOPTED_ZONE_WIDTH
        and guard == ADOPTED_STOP_GUARD
        and reentry
    )


def build_grid(
    payloads_by_threshold: dict[str, list[CellPayload]],
    *,
    arm: str,
    scope: str,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    log: bool = True,
) -> list[GridRow]:
    """한 팔의 (문턱 × 가드 × 재진입 × 구간) 행 — 배치만 반복한다."""
    rows: list[GridRow] = []
    for threshold in WIDTH_POINTS:
        label = zone_width_label(threshold)
        payloads = payloads_by_threshold.get(label)
        if not payloads:
            continue
        for guard in GUARD_POINTS:
            for reentry in (True, False):
                for segment in place(
                    payloads,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    segments=list(SEGMENT_ORDER),
                    guard=guard,
                    reentry=reentry,
                ):
                    rows.append(
                        GridRow(
                            scope=scope,
                            arm=arm,
                            width_label=label,
                            width_threshold=threshold,
                            guard=guard,
                            reentry=reentry,
                            adopted_point=_is_adopted(arm, threshold, guard, reentry),
                            **_row_kwargs(segment, num_symbols=num_symbols),
                        )
                    )
        if log:
            print(f"[wan378] {arm} · 문턱 {label}: 배치 {len(GUARD_POINTS) * 2}팔 완료", flush=True)
    return rows


def _short(symbol: str) -> str:
    return symbol.split("/")[0].upper()


def _exclude_payloads(
    payloads: Sequence[CellPayload], excluded: Sequence[str]
) -> list[CellPayload]:
    """짧은 심볼 이름(`ETH`) 집합을 뺀 payload — leave-one-out은 **지갑 재배치**다."""
    drop = {s.upper() for s in excluded}
    return [p for p in payloads if _short(p.symbol) not in drop]


def build_leave_one_out(
    payloads_by_threshold: dict[str, list[CellPayload]],
    *,
    arm: str,
    scope: str,
    start_ms: int,
    end_ms: int,
    log: bool = True,
) -> list[LooRow]:
    """종목 하나씩 빼고 **지갑을 다시 배치**한다 (완료기준 4 · WAN-316 스코프 패턴).

    라벨 필터가 아니다 — 종목을 빼면 그 칸이 쓰던 자본·명목 자리가 비어 **다른 칸의 배치가
    달라진다**. 그래서 남은 칸으로 북을 통째로 다시 돌린다.
    """
    rows: list[LooRow] = []
    for label in LOO_WIDTH_LABELS:
        payloads = payloads_by_threshold.get(label)
        if not payloads:
            continue
        threshold = next((t for t in WIDTH_POINTS if zone_width_label(t) == label), None)
        all_symbols = sorted({_short(p.symbol) for p in payloads})
        drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
        present_new = tuple(s for s in NEW_THREE if s in all_symbols)
        if len(present_new) > 1:
            drops.append(("-new3", present_new))
        for drop_label, dropped in drops:
            kept = _exclude_payloads(payloads, dropped)
            if not kept:
                continue
            num_symbols = len({p.symbol for p in kept})
            for segment in place(
                kept,
                start_ms=start_ms,
                end_ms=end_ms,
                segments=list(LOO_SEGMENTS),
                guard=ADOPTED_STOP_GUARD,
                reentry=True,
            ):
                rows.append(
                    LooRow(
                        scope=scope,
                        arm=arm,
                        width_label=label,
                        width_threshold=threshold,
                        guard=ADOPTED_STOP_GUARD,
                        reentry=True,
                        exclude=drop_label,
                        adopted_point=False,
                        **_row_kwargs(segment, num_symbols=num_symbols),
                    )
                )
        if log:
            print(f"[wan378] {arm} · 문턱 {label}: leave-one-out {len(drops)}판 완료", flush=True)
    return rows


# --------------------------------------------------------------------------- #
# 검산 — 채택 좌표 팔 ≡ 인자 없는 채택 북 (완료기준 6)
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
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    log: bool = True,
) -> list[ChecksumRow]:
    """격자의 채택 좌표 팔이 **인자 없는 채택 북**과 비트 일치하는가.

    격자는 「필터 끔으로 만들고 밖에서 컷」(지름길)이고 채택 북은 **엔진 필터를 켜고** 만든다
    — WAN-376 §1a가 그 등식을 두 지갑에서 못 박았지만 **채택 좌표(12종목 × 4TF)에서는 낸 적이
    없다**(그 표의 두 스코프가 채택 좌표가 아니라 등식이 성립할 수 없었다). 여기서 낸다.

    🚨 두 팔은 **다른 코드 경로**다 — 격자는 `run_cells_multi` ＋ 밖의 컷, 채택 북은
    `book_cli.run_book_segments`(엔진 필터). 같은 숫자가 나와야 「지름길로 잰 격자」가 채택
    북의 눈금 위에 있다는 뜻이다.
    """
    assert_adopted_base()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    scope = "+".join(timeframes)
    if log:
        print("[wan378] 검산 — 격자 채택 좌표 팔(지름길)", flush=True)
    grid = payloads_for_arm(
        symbols,
        timeframes,
        ARM_BOLLINGER,
        start=start,
        end=end,
        jobs=jobs,
        thresholds=(ADOPTED_ZONE_WIDTH,),
    )[zone_width_label(ADOPTED_ZONE_WIDTH)]
    grid_segments = {
        s.segment: s
        for s in place(
            grid,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=list(SEGMENT_ORDER),
            guard=ADOPTED_STOP_GUARD,
            reentry=True,
        )
    }
    if log:
        print("[wan378] 검산 — 인자 없는 채택 북(엔진 필터)", flush=True)
    book_segments = {
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
    rows: list[ChecksumRow] = []
    for segment in SEGMENT_ORDER:
        left, right = grid_segments.get(segment), book_segments.get(segment)
        if left is None or right is None:
            continue
        for metric in _CHECK_METRICS:
            a = float(getattr(left.row, metric))
            b = float(getattr(right.row, metric))
            rows.append(
                ChecksumRow(
                    scope=scope,
                    segment=segment,
                    metric=metric,
                    grid_value=a,
                    book_value=b,
                    abs_diff=abs(a - b),
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def run_grid(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arms: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    log: bool = True,
) -> tuple[list[GridRow], list[LooRow]]:
    """§1b 본체 — 팔마다 한 번 무겁게 만들고 배치를 격자로 돈다."""
    assert_adopted_base()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    scope = "+".join(timeframes)
    num_symbols = len(symbols)
    grid: list[GridRow] = []
    loo: list[LooRow] = []
    for arm in arms:
        if log:
            print(
                f"[wan378] 팔 `{arm}` 후보 생성 — 문턱 {len(WIDTH_POINTS)}점 · {scope}", flush=True
            )
        payloads = payloads_for_arm(symbols, timeframes, arm, start=start, end=end, jobs=jobs)
        grid.extend(
            build_grid(
                payloads,
                arm=arm,
                scope=scope,
                start_ms=start_ms,
                end_ms=end_ms,
                num_symbols=num_symbols,
                log=log,
            )
        )
        loo.extend(
            build_leave_one_out(
                payloads, arm=arm, scope=scope, start_ms=start_ms, end_ms=end_ms, log=log
            )
        )
    return grid, loo


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[GridRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def loo_to_frame(rows: Sequence[LooRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def checksum_to_frame(rows: Sequence[ChecksumRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _r(value: float) -> str:
    return f"{value:+.4f}R"


def _width_order(labels: Sequence[str]) -> list[str]:
    order = [zone_width_label(w) for w in WIDTH_POINTS]
    return [label for label in order if label in set(labels)]


def _select(grid: pd.DataFrame, *, arm: str, segment: str, reentry: bool = True) -> pd.DataFrame:
    return grid[(grid["arm"] == arm) & (grid["segment"] == segment) & (grid["reentry"] == reentry)]


def _cell_value(sub: pd.DataFrame, label: str, guard: float, column: str) -> float | None:
    hit = sub[(sub["width_label"] == label) & (sub["guard"] == guard)]
    return None if hit.empty else float(hit[column].iloc[0])


def curve_shape(values: Sequence[float | None]) -> str:
    """곡선을 한 낱말로 — **argmax가 아니라 모양**이 산출물이다(WAN-161 규약).

    폭(최대−최소)이 「0과 구분되지 않는」 규약 폭(±0.005R)의 두 배 안이면 **고원**, 최고점이
    양 끝 중 하나이고 폭이 크면 **단조**, 그 밖은 **봉우리**다. 절벽(한 점만 크게 떨어짐)은
    폭이 크면서 중앙값에서 크게 벗어난 점이 하나뿐인 경우다.
    """
    seen = [v for v in values if v is not None]
    if len(seen) < 3:
        return "표본 부족"
    span = max(seen) - min(seen)
    if span <= 2 * NEAR_ZERO_R:
        return "고원(평평)"
    best = seen.index(max(seen))
    if best in (0, len(seen) - 1):
        return "단조"
    return "봉우리"


def _render_headline(grid: pd.DataFrame) -> list[str]:
    """주 표 — 팔마다 문턱(행) × 가드(열)의 `oos_warm` 거래당 net R(재진입 ON)."""
    lines = [
        "## §1b — 주 표: 거래당 net R (`oos_warm` · 재진입 ON)",
        "",
        "🚨 **판정 자는 거래당 net R이다** — 총수익 %·MDD는 이 좌표에서 포화해 단을 못 가른다"
        "(WAN-376 규약). **±0.005R 안은 「0과 구분되지 않는다」**(WAN-366).",
        "",
    ]
    if grid.empty:
        return lines + ["_아직 안 돌렸다._", ""]
    for arm in ARM_ORDER:
        sub = _select(grid, arm=arm, segment=SEGMENT_OOS_WARM)
        if sub.empty:
            continue
        labels = _width_order(sub["width_label"].unique().tolist())
        lines += [
            f"### 팔 `{arm}`" + ("  ← 채택 진입가" if arm == ARM_BOLLINGER else ""),
            "",
            "| 존폭 문턱 | "
            + " | ".join(f"가드 {g:.2%}" for g in GUARD_POINTS)
            + " | 거래(0.30%) |",
            "| -- | " + " | ".join(["--"] * (len(GUARD_POINTS) + 1)) + " |",
        ]
        for label in labels:
            cells = []
            for guard in GUARD_POINTS:
                value = _cell_value(sub, label, guard, "mean_net_r")
                if value is None:
                    cells.append("—")
                    continue
                mark = " ✅" if _is_adopted(arm, _threshold_of(label), guard, True) else ""
                cells.append(f"{_r(value)}{mark}")
            trades = _cell_value(sub, label, ADOPTED_STOP_GUARD, "num_trades")
            cells.append("—" if trades is None else f"{int(trades):,}")
            lines.append(f"| `{label}` | " + " | ".join(cells) + " |")
        lines += ["", *_render_shape(sub, arm), ""]
    return lines


def _threshold_of(label: str) -> float | None:
    return next((w for w in WIDTH_POINTS if zone_width_label(w) == label), None)


def _render_shape(sub: pd.DataFrame, arm: str) -> list[str]:
    labels = _width_order(sub["width_label"].unique().tolist())
    out = ["| 가드 | 곡선 모양(문턱 축) | 폭 | 최고 문턱 |", "| -- | -- | -- | -- |"]
    for guard in GUARD_POINTS:
        values = [_cell_value(sub, label, guard, "mean_net_r") for label in labels]
        seen = [(label, v) for label, v in zip(labels, values, strict=True) if v is not None]
        if not seen:
            continue
        span = max(v for _, v in seen) - min(v for _, v in seen)
        best = max(seen, key=lambda kv: kv[1])[0]
        out.append(f"| {guard:.2%} | {curve_shape(values)} | {span:.4f}R | `{best}` |")
    out += [
        "",
        f"⚠️ **`{arm}` 팔의 「최고 문턱」은 답이 아니라 자료다** — argmax를 채택 권고로 쓰지 "
        "않는다(WAN-161: 배수 argmax가 8칸 중 7칸에서 뒤집혔다). 아래 IS→OOS 뒤집힘 표와 함께 "
        "읽을 것.",
    ]
    return out


def flip_table(grid: pd.DataFrame) -> pd.DataFrame:
    """(팔 × 가드)마다 `is` argmax 문턱과 `oos_warm` argmax 문턱 — 뒤집힘 세기."""
    if grid.empty:
        return pd.DataFrame()
    out: list[dict[str, object]] = []
    for arm in ARM_ORDER:
        for guard in GUARD_POINTS:
            picks: dict[str, str | None] = {}
            for segment in (SEGMENT_IS, SEGMENT_OOS_WARM):
                sub = _select(grid, arm=arm, segment=segment)
                labels = _width_order(sub["width_label"].unique().tolist())
                seen = [(label, _cell_value(sub, label, guard, "mean_net_r")) for label in labels]
                alive = [(label, v) for label, v in seen if v is not None]
                picks[segment] = max(alive, key=lambda kv: kv[1])[0] if alive else None
            if picks[SEGMENT_IS] is None or picks[SEGMENT_OOS_WARM] is None:
                continue
            out.append(
                {
                    "arm": arm,
                    "guard": guard,
                    "is_best": picks[SEGMENT_IS],
                    "oos_warm_best": picks[SEGMENT_OOS_WARM],
                    "flipped": picks[SEGMENT_IS] != picks[SEGMENT_OOS_WARM],
                }
            )
    return pd.DataFrame(out)


def _render_flips(grid: pd.DataFrame) -> list[str]:
    lines = [
        "## IS→OOS 뒤집힘 — 「앞구간이 고른 문턱」이 뒷구간에도 최고인가",
        "",
    ]
    table = flip_table(grid)
    if table.empty:
        return lines + ["_아직 안 돌렸다._", ""]
    flips = int(table["flipped"].sum())
    lines += [
        "| 팔 | 가드 | `is` 최고 문턱 | `oos_warm` 최고 문턱 | 뒤집힘 |",
        "| -- | -- | -- | -- | -- |",
    ]
    for _, r in table.iterrows():
        mark = "🚨 예" if bool(r["flipped"]) else "아니오"
        lines.append(
            f"| `{r['arm']}` | {float(r['guard']):.2%} | `{r['is_best']}` | "
            f"`{r['oos_warm_best']}` | {mark} |"
        )
    lines += [
        "",
        f"📌 **{len(table)}칸 중 {flips}칸이 뒤집힌다.** 이것이 이 격자의 산출물이지 "
        "argmax가 아니다 — 앞구간에서 고른 눈금이 뒷구간에서 최고가 아니면 「데이터가 이 값을 "
        "골랐다」는 문장을 쓸 수 없다(WAN-90/161이 배수 축에서, WAN-201이 널 축에서 본 것과 "
        "같은 서명).",
        "",
    ]
    return lines


def _render_guard_axis(grid: pd.DataFrame) -> list[str]:
    """가드 축이 실제로 무는가.

    이슈가 「4점이 다 다른 답을 낸 것처럼 보이면 안 된다」고 못 박은 자리다.
    """
    lines = [
        "## 가드 축이 실제로 무는가 — 「작동하지 않는 구간」을 묶어 읽는다",
        "",
        "§0가 탭 층에서 본 것: `proximal`에서 가드 0.3%가 자르는 탭이 **4h 0.0% · 2h 0.2% · "
        "1h 1.0% · 15m 10.1%**다. 북은 4TF **한 지갑**이라 TF별로 못 가르지만, 가드 점 사이의 "
        "거래 수 변화가 0에 가까우면 그 팔에서 가드는 **축이 아니다**.",
        "",
        "| 팔 | 문턱 | 가드 끔 거래 | 0.40% 거래 | 변화 | net R 폭 |",
        "| -- | -- | -- | -- | -- | -- |",
    ]
    if grid.empty:
        return lines[:-2] + ["_아직 안 돌렸다._", ""]
    for arm in ARM_ORDER:
        sub = _select(grid, arm=arm, segment=SEGMENT_OOS_WARM)
        if sub.empty:
            continue
        for label in _width_order(sub["width_label"].unique().tolist()):
            off = _cell_value(sub, label, 0.0, "num_trades")
            tight = _cell_value(sub, label, GUARD_POINTS[-1], "num_trades")
            values = [_cell_value(sub, label, g, "mean_net_r") for g in GUARD_POINTS]
            seen = [v for v in values if v is not None]
            if off is None or tight is None or not seen:
                continue
            delta = (tight - off) / off if off else 0.0
            lines.append(
                f"| `{arm}` | `{label}` | {int(off):,} | {int(tight):,} | {delta * 100:+.1f}% | "
                f"{max(seen) - min(seen):.4f}R |"
            )
    lines += [
        "",
        "📌 **거래 변화가 몇 %대인 줄에서는 가드 4점을 「네 개의 다른 답」으로 읽지 말 것** — "
        "같은 값을 네 번 쓴 것에 가깝다. `mid` 팔은 진입가를 존 중앙으로 내려 손절폭을 절반으로 "
        "만들므로 같은 가드가 훨씬 많이 문다(**진입가와 가드는 독립이 아니다** — WAN-376 §0).",
        "",
    ]
    return lines


def _render_reentry(grid: pd.DataFrame) -> list[str]:
    lines = [
        "## 재진입 ON/OFF 대조 — 「재진입이 판정을 뒤집는가」",
        "",
        "📌 **108팔 전부에 냈다**(이슈가 좁혀도 된다고 한 자리) — OFF는 이미 만든 payload를 "
        "`include_reentry=False`로 **다시 배치**하는 것뿐이라 후보 생성 비용이 0이다. "
        "⚠️ **최적화 축이 아니라 대조**이고 채택 규칙은 ON이다(WAN-273/305).",
        "",
        "| 팔 | 문턱 | 가드 | ON net R | OFF net R | Δ(ON−OFF) | ON 거래 | OFF 거래 |",
        "| -- | -- | -- | -- | -- | -- | -- | -- |",
    ]
    if grid.empty:
        return lines[:-2] + ["_아직 안 돌렸다._", ""]
    shown = 0
    for arm in ARM_ORDER:
        on = _select(grid, arm=arm, segment=SEGMENT_OOS_WARM, reentry=True)
        off = _select(grid, arm=arm, segment=SEGMENT_OOS_WARM, reentry=False)
        if on.empty or off.empty:
            continue
        for label in _width_order(on["width_label"].unique().tolist()):
            for guard in GUARD_POINTS:
                a = _cell_value(on, label, guard, "mean_net_r")
                b = _cell_value(off, label, guard, "mean_net_r")
                if a is None or b is None:
                    continue
                ta = _cell_value(on, label, guard, "num_trades") or 0.0
                tb = _cell_value(off, label, guard, "num_trades") or 0.0
                lines.append(
                    f"| `{arm}` | `{label}` | {guard:.2%} | {_r(a)} | {_r(b)} | "
                    f"{a - b:+.4f}R | {int(ta):,} | {int(tb):,} |"
                )
                shown += 1
    if not shown:
        return lines[:-2] + ["_아직 안 돌렸다._", ""]
    lines += ["", *_reentry_headline(grid), ""]
    return lines


def _reentry_headline(grid: pd.DataFrame) -> list[str]:
    """「재진입이 판정을 뒤집는가」를 한 줄로 — 108줄 표를 사람이 다 읽지는 않는다."""
    on = grid[(grid["segment"] == SEGMENT_OOS_WARM) & grid["reentry"]]
    off = grid[(grid["segment"] == SEGMENT_OOS_WARM) & ~grid["reentry"]]
    keys = ["arm", "width_label", "guard"]
    merged = on.merge(off, on=keys, suffixes=("_on", "_off"))
    if merged.empty:
        return []
    flipped = merged[
        ((merged["mean_net_r_on"] > 0) & (merged["mean_net_r_off"] <= 0))
        | ((merged["mean_net_r_on"] <= 0) & (merged["mean_net_r_off"] > 0))
    ]
    worst = float((merged["mean_net_r_on"] - merged["mean_net_r_off"]).abs().max())
    return [
        f"📌 **{len(merged)}팔 중 부호가 갈리는 곳은 {len(flipped)}곳**이고 |Δ|의 최댓값은 "
        f"**{worst:.4f}R**이다. ⚠️ 「부호가 갈린다」는 재진입이 판정을 **뒤집었다**는 뜻이지 "
        "재진입이 좋다/나쁘다가 아니다 — 채택 규칙은 ON이고(WAN-273) OFF는 대조다."
    ]


def _render_sample_gate(grid: pd.DataFrame) -> list[str]:
    lines = ["## 표본 게이트 — 종목당 20거래 미만이 나는 곳", ""]
    if grid.empty:
        return lines + ["_아직 안 돌렸다._", ""]
    warm = grid[(grid["segment"] == SEGMENT_OOS_WARM) & grid["reentry"]]
    bad = warm[warm["symbols_below_gate"] > 0]
    if bad.empty:
        return lines + [
            f"📌 **주 구간(`oos_warm` · 재진입 ON)의 {len(warm)}팔 어디에서도 종목당 20거래 "
            "미만이 나지 않는다** — ★결정이 문턱 `0.60`을 뺀 것이 이 뜻이었다(§0).",
            "",
        ]
    lines += [
        "| 팔 | 문턱 | 가드 | 게이트 미달 종목 | 최소 종목 거래 |",
        "| -- | -- | -- | -- | -- |",
    ]
    for _, r in bad.sort_values("min_symbol_trades").head(30).iterrows():
        lines.append(
            f"| `{r['arm']}` | `{r['width_label']}` | {float(r['guard']):.2%} | "
            f"{int(r['symbols_below_gate'])} | {int(r['min_symbol_trades'])} |"
        )
    lines += [
        "",
        "🚨 **이 줄들은 「⚠️ 판정 불가」로 읽는다**(`wan143.MIN_TRADES_PER_SYMBOL` = "
        f"{MIN_TRADES_PER_SYMBOL}). 표에 남기는 것은 지우면 그 사실이 안 보이기 때문이다.",
        "",
    ]
    return lines


def _render_loo(loo: pd.DataFrame) -> list[str]:
    lines = [
        "## 종목 하나씩 빼보기 — 라벨 필터가 아니라 **지갑 재배치** (완료기준 4)",
        "",
        f"낸 범위: 팔 3개 × 문턱 `{'`·`'.join(LOO_WIDTH_LABELS)}` × 가드 "
        f"{ADOPTED_STOP_GUARD:.2%} × 재진입 ON × 구간 `{'`·`'.join(LOO_SEGMENTS)}`. "
        "전 격자를 다시 배치하는 것은 비용만 늘고 「특정 종목이 만든 결과인가」는 채택 근방에서 "
        "답이 난다(WAN-312 관행).",
        "",
    ]
    if loo.empty:
        return lines + ["_아직 안 돌렸다._", ""]
    warm = loo[loo["segment"] == SEGMENT_OOS_WARM]
    for arm in ARM_ORDER:
        sub = warm[warm["arm"] == arm]
        if sub.empty:
            continue
        lines += [
            f"### 팔 `{arm}` (`oos_warm`)",
            "",
            "| 문턱 | 최악 제외 | 최선 제외 | 부호 유지 |",
            "| -- | -- | -- | -- |",
        ]
        for label in LOO_WIDTH_LABELS:
            cell = sub[sub["width_label"] == label]
            if cell.empty:
                continue
            worst = cell.loc[cell["mean_net_r"].idxmin()]
            best = cell.loc[cell["mean_net_r"].idxmax()]
            same = bool((cell["mean_net_r"] > 0).all() or (cell["mean_net_r"] < 0).all())
            lines.append(
                f"| `{label}` | `{worst['exclude']}` {_r(float(worst['mean_net_r']))} | "
                f"`{best['exclude']}` {_r(float(best['mean_net_r']))} | "
                f"{'✅ 예' if same else '🚨 아니오'} |"
            )
        lines += [""]
    lines += [
        "📌 **「부호 유지」가 예면 그 줄은 특정 종목이 만든 결과가 아니다** — 이 저장소의 편중 "
        "계열(WAN-111/119/124/151 = 「플러스는 전부 ETH」)이 여기서 성립하는지 보는 자다.",
        "",
    ]
    return lines


def _render_checksum(checksum: pd.DataFrame) -> list[str]:
    lines = ["## 검산 — 채택 좌표 팔 ≡ 인자 없는 채택 북 (완료기준 6)", ""]
    if checksum.empty:
        return lines + [
            "⚠️ **아직 안 돌렸다** — `--part checksum`. 조용히 건너뛰지 않고 밝힌다"
            "(WAN-194/318/321 「실패가 성공과 같은 모양」).",
            "",
        ]
    worst = float(checksum["abs_diff"].max())
    verdict = "✅ 비트 일치" if worst == 0.0 else f"🚨 불일치(최대 {worst:.3e})"
    lines += [
        f"격자의 채택 좌표 팔(볼린저 × {ADOPTED_ZONE_WIDTH} × {ADOPTED_STOP_GUARD:.1%} × 재진입 "
        f"ON) vs `backtest.run --oos-warm`(인자 없는 채택 북) — **{len(checksum)}개 값 최대 "
        f"절대차 {worst:.2e}** → {verdict}",
        "",
        "| 구간 | 지표 | 격자 | 채택 북 | 차 |",
        "| -- | -- | -- | -- | -- |",
    ]
    for _, r in checksum.iterrows():
        lines.append(
            f"| `{r['segment']}` | `{r['metric']}` | {float(r['grid_value']):.6f} | "
            f"{float(r['book_value']):.6f} | {float(r['abs_diff']):.2e} |"
        )
    lines += [
        "",
        "🚨 **두 팔은 다른 코드 경로다** — 격자는 「필터 끔으로 만들고 밖에서 컷」(WAN-376 §1a "
        "지름길), 채택 북은 **엔진 필터를 켜고** 만든다. §1a가 그 등식을 못 박았지만 **채택 "
        "좌표(12종목 × 4TF)에서는 낸 적이 없었다**(그 표의 두 스코프가 채택 좌표가 아니라 이 "
        "등식이 성립할 수 없었다). 이 줄이 그 자리를 메운다.",
        "",
    ]
    return lines


def _render_verdict(grid: pd.DataFrame) -> list[str]:
    lines = ["## 판정", ""]
    if grid.empty:
        return lines + ["_아직 안 돌렸다._", ""]
    warm = grid[(grid["segment"] == SEGMENT_OOS_WARM) & grid["reentry"]]
    if warm.empty:
        return lines + ["_아직 안 돌렸다._", ""]
    positive = warm[warm["mean_net_r"] > NEAR_ZERO_R]
    adopted = warm[warm["adopted_point"]]
    adopted_r = float(adopted["mean_net_r"].iloc[0]) if not adopted.empty else None
    lines += [
        f"* 주 구간(`oos_warm` · 재진입 ON) **{len(warm)}팔** 중 거래당 net R이 "
        f"**+{NEAR_ZERO_R}R을 넘는 팔은 {len(positive)}개**다"
        + (f" (최고 {_r(float(warm['mean_net_r'].max()))})." if len(warm) else "."),
        (
            f"* 오늘의 채택 좌표(볼린저 × {ADOPTED_ZONE_WIDTH} × {ADOPTED_STOP_GUARD:.1%})는 "
            f"**{_r(adopted_r)}**이다."
            if adopted_r is not None
            else "* 채택 좌표 행이 이 표에 없다(팔을 나눠 돌렸다면 `--append`로 합칠 것)."
        ),
        "",
        "🚨 **이 표는 채택 권고를 내지 않는다** — 9 × 4 격자는 「앞구간 승자를 찾기」 딱 좋은 "
        "크기이고(WAN-366 §0), 산출물은 **곡선의 모양**과 **IS→OOS 뒤집힘 횟수**다. 눈금을 "
        "바꾸는 것은 **재-베이스라인 = 사용자 결정**이고 개발자 임의 착수 금지다.",
        "",
    ]
    return lines


def build_summary(grid: pd.DataFrame, loo: pd.DataFrame, checksum: pd.DataFrame) -> str:
    lines = [
        "# WAN-378 — 「존의 두께」 §1b 격자 (문턱 9점 × 가드 4점 × 진입가 3팔)",
        "",
        "존폭 필터 **1.28**(WAN-159)과 손절폭 가드 **0.3%**(WAN-76/79)는 둘 다 **소급 취소 "
        "버그가 있던 엔진**의 표를 비교해 골라졌다. WAN-365가 그 버그를 고친 뒤 인과 엔진에서 "
        "잰 것은 **그 값에서의 켬/끔뿐**(WAN-366/368)이고 **눈금을 흔들어 본 적이 없다.** "
        "이 표가 그 눈금을 흔든다.",
        "",
        "⚠️ **측정 전용** — `ConfluenceParams()`·`LeverageBookParams()` 기본값을 하나도 안 "
        "바꿨다. 채택은 **재-베이스라인 = 사용자 결정**이고 개발자 임의 착수 금지다.",
        "",
    ]
    if not grid.empty:
        scopes = ", ".join(sorted(grid["scope"].unique()))
        universe = int(grid["num_symbols"].max()) if "num_symbols" in grid.columns else 0
        lines += [
            f"좌표: **{universe}종목 × {scopes}**(한 지갑 — 북은 이어붙일 수 없다, WAN-316) · "
            "못 박은 6년 창 · cap_only 5배 · `baseline` 렌즈 · **핀 하나도 없다**(WAN-305).",
            "",
        ]
    lines += _render_headline(grid)
    lines += _render_flips(grid)
    lines += _render_guard_axis(grid)
    lines += _render_reentry(grid)
    lines += _render_sample_gate(grid)
    lines += _render_loo(loo)
    lines += _render_checksum(checksum)
    lines += _render_verdict(grid)
    lines += [
        "## 경고",
        "",
        "* 전부 `baseline`(닿으면 체결) 렌즈 위 값이고 체결 보수화(`pen_5bp`)는 안 쟀다.",
        "* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 표는 *두 자의 눈금이 "
        "맞나*를 묻지 *진입 규칙이 무작위와 구분되나*를 묻지 않는다.",
        "* 총수익 %는 복리 착시(WAN-169/213)이고 6년 MDD는 2018·2020-03 폭락을 **포함하지 "
        "않는** 창이라 천장이 아니라 **바닥선**이다. **청산 0건은 안전 신호가 아니다**"
        "(WAN-312 §4).",
        "* **판단은 북에서**(WAN-341) · 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-378 §1b 존의 두께 격자")
    parser.add_argument("--part", choices=("grid", "checksum", "summary"), default="grid")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--arms", default=",".join(ARM_ORDER))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    return parser.parse_args(argv)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    if existing.empty:
        return fresh
    if fresh.empty:
        return existing
    merged = pd.concat([existing, fresh], ignore_index=True)
    return merged.drop_duplicates(subset=list(keys), keep="last").reset_index(drop=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    if args.part == "grid" and not args.from_csv:
        grid_rows, loo_rows = run_grid(
            symbols, timeframes, arms, start=args.start, end=args.end, jobs=args.jobs
        )
        grid = _merge(_read(GRID_CSV_PATH), grid_to_frame(grid_rows), GRID_KEYS)
        loo = _merge(_read(LOO_CSV_PATH), loo_to_frame(loo_rows), LOO_KEYS)
        grid.to_csv(GRID_CSV_PATH, index=False)
        loo.to_csv(LOO_CSV_PATH, index=False)
        print(f"[wan378] 격자 적재: {GRID_CSV_PATH} · {LOO_CSV_PATH}", flush=True)
    elif args.part == "checksum" and not args.from_csv:
        rows = run_checksum(symbols, timeframes, start=args.start, end=args.end, jobs=args.jobs)
        checksum = _merge(_read(CHECKSUM_CSV_PATH), checksum_to_frame(rows), CHECKSUM_KEYS)
        checksum.to_csv(CHECKSUM_CSV_PATH, index=False)
        print(f"[wan378] 검산 적재: {CHECKSUM_CSV_PATH}", flush=True)

    SUMMARY_PATH.write_text(
        build_summary(_read(GRID_CSV_PATH), _read(LOO_CSV_PATH), _read(CHECKSUM_CSV_PATH)),
        encoding="utf-8",
    )
    print(f"[wan378] 요약: {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
