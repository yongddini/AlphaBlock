"""WAN-381: 현행 엔진의 「출구 눈금」 둘을 한 격자로 — 손절폭 가드 5점 × 익절 배수 4점.

팔은 **하나**다: 오늘 페이퍼가 돌리는 그 규칙(볼린저 지정가 · 존폭 필터 끔(WAN-384) ·
인과 취소(WAN-365) · 재진입 band(WAN-273) · cap_only 5배(WAN-213)). 흔드는 것은 **출구
눈금 둘**뿐이다.

## 왜 둘을 **함께** 흔드나 (이 이슈가 합쳐진 이유)

손익분기 승률이 두 눈금을 하나로 묶는다::

    손익분기 승률 = (1 + 비용R) / (1 + 목표R)

가드를 올리면 좁은 손절이 잘려 **비용 R이 내려가고**, 그러면 **손익분기선이 내려가 최적
목표도 따라 움직인다**. 따로 돌리면 서로를 무효화한다 — *「가드를 올리면 최적 배수가
움직이는가」*는 곱해야만 나오는 답이고, 그것이 이 표의 §4다.

## 격자 (사용자 결정 2026-08-31)

| 축 | 값 | 점 | 비고 |
| -- | -- | -- | -- |
| 손절폭 가드 | `0.30%`(채택) · `0.40%` · `0.50%` | 3 | ✅ **검산** — WAN-386과 비트 일치 |
| | `0.60%` · `0.80%` | 2 | ❌ 신규 |
| 익절 배수 | `1.0R` · `1.5R`(채택) | 2 | ✅ **검산**(위 가드 3점에 한해) |
| | `0.6R` · `0.8R` | 2 | ❌ 신규 |

= **20조합 × 구간 4개**, 그중 **6조합이 검산**이다.

⚠️ **배수를 위쪽(2.0·2.5·3.0R)으로 다시 돌리지 않는다** — WAN-386이 이미 냈고 **단조로
나빠진다**(1.0R −0.0726 → 3.0R −0.1891). 이 표는 **아래쪽**만 본다: 1.0R이 그 격자의
끝값이었고 **그 아래는 아무도 안 쟀다**.

## 🚨 주 판정 열은 `mean_net_r`이 아니라 **`mean_gross_r`과 나란히** 읽는다

WAN-386 실측에서 가드 축의 gross가 **−0.0421 → −0.0370 → −0.0372로 평평**했다(0.40→0.50은
오히려 −0.0002). 즉 가드가 하는 일은 **「좋은 거래 선별」이 아니라 「비싼 거래 버리기」**다.
0.60·0.80%에서도 평평하면 **가드 축은 닫힌 것**이고, 그 결론만으로 이 표의 값어치가 있다.

배수 축은 성질이 다르다 — 같은 실측에서 gross가 1.5R −0.0421 vs 1.0R **+0.0007**로 **부호가
바뀐다**. 목표를 당기면 「수수료 전」에서 0을 넘는 자리가 실제로 있다는 뜻이라, 아래쪽
(0.6·0.8R)에 바닥이 있는지가 이 표의 §3이다.

## 읽는 법 · 금지

* **판정 자는 거래당 net R**이고 **거래 수를 항상 옆에 둔다** — 가드를 올리면 거래가
  사라지므로 「좋아진 것」과 **「덜 매매한 것」**을 구분해야 한다(WAN-374/378).
* **argmax는 채택 근거가 아니다**(WAN-161: 배수 argmax가 8칸 중 7칸 IS→OOS 뒤집힘). 이 표가
  내는 것은 **공선의 모양**(꺾이는가·어디서)과 **뒤집힘 개수**다.
* 🚨 **꺾이지 않으면 「이 격자에서도 안 꺾였다」로 쓴다** — 억지로 최고점을 고르지 않는다.
* 지갑 층 열(총수익·MDD·수익/MDD)은 이 좌표에서 **뜻을 잃는다**(WAN-386 `wallet_defined`).
* ±0.005R 안은 「0과 구분되지 않는다」(WAN-366/370 규약).

## 검산

* **(a-1)** 기준 팔 후보 ≡ 엔진 base+재진입 (칸·구간별, 진입·청산까지)
* **(a-2)** 그 후보로 배치한 지갑 ≡ **인자 없는 채택 북**(복리 켬)
* **(b)** 같은 배수들의 진입 집합이 비트 일치 — 익절은 청산만 바꾼다(WAN-137/143)
* **(c)** 진입 유동성이 전부 메이커(라벨이 아니라 **후보의 값**으로 — WAN-370)
* **(d)** 겹치는 6조합 ≡ `wan386_confirmation_grid.csv`의 `기준` 팔 **비트 일치**

재현::

    uv run python -m backtest.wan381_exit_scales --pilot      # 한 칸 견적
    uv run python -m backtest.wan381_exit_scales --jobs 4     # 48칸 격자
    uv run python -m backtest.wan381_exit_scales --from-csv   # 요약만
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import median

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments
from backtest.confirmation_arm import ARM_BASE
from backtest.leverage_book import LeverageBookParams
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import MIN_TRADES_PER_SYMBOL
from backtest.wan169_leverage_book import CellPayload, arm_key
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan386_confirmation_pnl import (
    NEW_THREE,
    ChecksumRow,
    GridRow,
    LooRow,
    _compare_segments,
    _pct,
    _r,
    _row_kwargs,
    _short,
    arm_payloads,
    build_payloads,
    guard_census,
    place,
    wallet_defined,
)
from common.costs import Liquidity

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan381_exit_scales_grid.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan381_leave_one_out.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan381_checksum.csv"
STOP_WIDTH_CSV_PATH = REPORTS_DIR / "wan381_stop_width.csv"
SUMMARY_PATH = REPORTS_DIR / "wan381_exit_scales_summary.md"

#: WAN-386 격자 — 겹치는 칸의 비트 일치 검산 상대(같은 좌표·같은 엔진).
WAN386_GRID_PATH = REPORTS_DIR / "wan386_confirmation_grid.csv"

#: 손절폭 가드 5점(분수). `0.003`이 채택값이다(WAN-79) — 개발자가 점을 더하거나 빼지 않는다.
GUARD_POINTS: tuple[float, ...] = (ADOPTED_STOP_GUARD, 0.0040, 0.0050, 0.0060, 0.0080)

#: 익절 배수 4점. `1.5`가 채택값이다(WAN-81/90). ⚠️ 위쪽은 WAN-386이 이미 냈다(단조 악화).
MULTIPLES: tuple[float, ...] = (0.6, 0.8, 1.0, 1.5)
ADOPTED_MULTIPLE = 1.5

#: 검산 (d)가 덮는 겹침 — WAN-386의 `기준` 팔이 이미 낸 칸들.
CHECK_GUARDS: tuple[float, ...] = (ADOPTED_STOP_GUARD, 0.0040, 0.0050)
CHECK_MULTIPLES: tuple[float, ...] = (1.0, 1.5)
_CROSS_METRICS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "mean_net_r",
    "mean_gross_r",
    "total_return_flat",
    "max_drawdown",
    "guard_cut",
)

#: 「0과 구분되지 않는다」 선 — WAN-366/370 규약.
NOISE_R = 0.005

#: leave-one-out 구간 — `full`과 `oos_warm`(주 수치).
LOO_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class StopWidthRow(BaseModel):
    """가드 점마다 **살아남은 거래의 손절폭 분포** — 완료기준 3의 「무엇이 남았나」.

    가드를 올리면 net R이 오르는데, 그것이 「좋은 거래가 남아서」인지 「비싼 거래를 버려서」
    인지는 gross 열이 답하고(§2) **어떤 거래가 남았는지**는 이 분포가 답한다.
    """

    model_config = ConfigDict(frozen=True)

    guard: float
    multiple: float
    segment: str
    num_trades: int
    p10: float
    """손절폭 ÷ 진입 체결가의 10분위."""
    median: float
    p90: float


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def build_grid(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    log: bool = True,
) -> list[GridRow]:
    """가드 × 배수 × 구간 — 배치만 반복한다(후보는 이미 있다).

    가드는 **배치 축**이고(WAN-197) 배수는 **청산만** 바꾸므로(WAN-137/143), 무거운 후보
    생성은 이 함수 밖에서 딱 한 번 돈다.
    """
    census = {g: guard_census(payloads, arm=ARM_BASE, guard=g) for g in GUARD_POINTS}
    rows: list[GridRow] = []
    for multiple in MULTIPLES:
        scoped = arm_payloads(payloads, arm=ARM_BASE, multiple=multiple)
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
                        arm=ARM_BASE,
                        guard=guard,
                        multiple=multiple,
                        adopted_point=(
                            guard == ADOPTED_STOP_GUARD and multiple == ADOPTED_MULTIPLE
                        ),
                        guard_cut=cut,
                        guard_kept=kept,
                        **_row_kwargs(segment, num_symbols=num_symbols),
                    )
                )
        if log:
            print(f"[wan381] 배수 {multiple:g}R: 가드 {len(GUARD_POINTS)}점 배치 완료", flush=True)
    return rows


def _stop_width_fractions(segment: BookSegment) -> list[float]:
    """이 구간 거래들의 「손절폭 ÷ 진입 체결가」. 사이징 가드가 보는 그 양이다."""
    out: list[float] = []
    for trade, placement in segment.trades_with_placements():
        if trade.entry_price <= 0.0 or placement.stop_price <= 0.0:
            continue
        out.append(abs(trade.entry_price - placement.stop_price) / trade.entry_price)
    return out


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def build_stop_widths(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    multiple: float = ADOPTED_MULTIPLE,
) -> list[StopWidthRow]:
    """가드 점마다 남은 거래의 손절폭 분포(채택 배수 하나로 — 익절은 진입 집합을 안 바꾼다)."""
    scoped = arm_payloads(payloads, arm=ARM_BASE, multiple=multiple)
    rows: list[StopWidthRow] = []
    for guard in GUARD_POINTS:
        for segment in place(
            scoped,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=list(SEGMENT_ORDER),
            guard=guard,
        ):
            widths = _stop_width_fractions(segment)
            rows.append(
                StopWidthRow(
                    guard=guard,
                    multiple=multiple,
                    segment=segment.segment,
                    num_trades=len(widths),
                    p10=_quantile(widths, 0.10),
                    median=median(widths) if widths else 0.0,
                    p90=_quantile(widths, 0.90),
                )
            )
    return rows


def build_leave_one_out(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    points: Sequence[tuple[float, float]],
    log: bool = True,
) -> list[LooRow]:
    """지목한 (가드, 배수) 점마다 종목 하나씩 빼고 **지갑을 다시 배치**한다(WAN-316 스코프)."""
    rows: list[LooRow] = []
    all_symbols = sorted({_short(p.symbol) for p in payloads})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    for guard, multiple in points:
        scoped = arm_payloads(payloads, arm=ARM_BASE, multiple=multiple)
        for drop_label, dropped in drops:
            drop = {s.upper() for s in dropped}
            kept_payloads = [p for p in scoped if _short(p.symbol) not in drop]
            if not kept_payloads:
                continue
            cut, kept = guard_census(
                [p for p in payloads if _short(p.symbol) not in drop],
                arm=ARM_BASE,
                guard=guard,
            )
            for segment in place(
                kept_payloads,
                start_ms=start_ms,
                end_ms=end_ms,
                segments=list(LOO_SEGMENTS),
                guard=guard,
            ):
                rows.append(
                    LooRow(
                        arm=ARM_BASE,
                        guard=guard,
                        multiple=multiple,
                        adopted_point=False,
                        exclude=drop_label,
                        guard_cut=cut,
                        guard_kept=kept,
                        **_row_kwargs(segment, num_symbols=len({p.symbol for p in kept_payloads})),
                    )
                )
        if log:
            print(
                f"[wan381] leave-one-out 가드 {guard:.2%} × {multiple:g}R: {len(drops)}판 완료",
                flush=True,
            )
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def on_adopted_coordinates(symbols: Sequence[str], timeframes: Sequence[str]) -> bool:
    """이 실행이 **채택 좌표 전부**를 도는가 — 검산 (d)가 성립할 조건.

    🚨 좁혀 돌린 판을 WAN-386 격자와 대조하면 「다른 좌표의 두 표」를 비교하게 되어 차가
    커지고, 그 차가 **배선 오류처럼 보인다**(파일럿에서 실제로 그랬다). 좌표가 다르면
    대조하지 않고 **그 사실을 표에 찍는다** — 조용히 건너뛰지 않는다.
    """
    return set(symbols) == set(harness.DEFAULT_SYMBOLS) and set(timeframes) == set(
        harness.DEFAULT_TIMEFRAMES
    )


def cross_check_wan386(
    rows: Sequence[GridRow], *, path: Path = WAN386_GRID_PATH
) -> list[ChecksumRow]:
    """검산 (d) — 겹치는 6조합이 `wan386_confirmation_grid.csv`의 `기준` 팔과 비트 일치.

    🚨 **이 검산이 없으면 두 표를 이어 읽을 수 없다.** WAN-386이 가드 0.50%까지 냈으므로 이
    이슈가 새로 여는 것은 0.60·0.80%와 배수 0.6·0.8R뿐이고, 겹치는 칸이 어긋나면 그 「새로
    연 점」이 다른 눈금 위에 서게 된다.
    """
    if not path.exists():
        return [
            ChecksumRow(
                check="(d) WAN-386 격자 대조 — 파일 없음",
                segment="all",
                metric="missing_csv",
                left=1.0,
                right=0.0,
                abs_diff=1.0,
            )
        ]
    frame = pd.read_csv(path)
    ours = {(r.guard, r.multiple, r.segment): r for r in rows}
    out: list[ChecksumRow] = []
    for rec in frame.to_dict(orient="records"):
        if str(rec["arm"]) != ARM_BASE:
            continue
        guard, multiple = float(rec["guard"]), float(rec["multiple"])
        if guard not in CHECK_GUARDS or multiple not in CHECK_MULTIPLES:
            continue
        mine = ours.get((guard, multiple, str(rec["segment"])))
        if mine is None:
            continue
        for metric in _CROSS_METRICS:
            left = float(getattr(mine, metric))
            right = float(rec[metric])
            out.append(
                ChecksumRow(
                    check=f"(d) WAN-386 대조 · 가드 {guard:.2%} × {multiple:g}R",
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
                check="(d) WAN-386 격자 대조 — 겹치는 행이 없음",
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
    rows: Sequence[GridRow],
    *,
    start_ms: int,
    end_ms: int,
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

    # (a-2) 그 후보로 배치한 지갑 ≡ 인자 없는 채택 북(복리 켬).
    if log:
        print("[wan381] 검산 (a-2) — 기준 팔 지갑 ≡ 채택 북 지갑(복리 켬)", flush=True)
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
    checks.extend(
        _compare_segments(left_segments, right_segments, check="(a-2) 기준 팔 지갑 ≡ 채택 북 지갑")
    )

    # (b) 배수들의 진입 집합이 비트 일치 — 익절은 청산만 바꾼다(WAN-137/143 훅).
    entry_sets = {
        tuple(
            (c.entry_time, c.entry_price)
            for p in payloads
            for c in p.arm_candidates[arm_key(ARM_BASE, m)].get("full", ())
        )
        for m in MULTIPLES
    }
    checks.append(
        ChecksumRow(
            check="(b) 배수 불변 진입 집합",
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

    # (d) 겹치는 6조합 ≡ WAN-386 격자 — **채택 좌표를 돌 때만** 성립한다.
    if cross_check:
        checks.extend(cross_check_wan386(rows))
    else:
        checks.append(
            ChecksumRow(
                check="(d) WAN-386 격자 대조 — 좌표가 달라 **건너뜀**(좁혀 돈 실행)",
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


def _fmt_guard(value: float) -> str:
    return f"{value:.2%}"


def _fmt_multiple(value: float) -> str:
    return f"{value:g}R"


def _axis_fmt(axis: str) -> Callable[[float], str]:
    """축 값의 표기 — 가드는 퍼센트, 배수는 `R`."""
    return _fmt_guard if axis == "guard" else _fmt_multiple


def _pick(
    rows: Sequence[GridRow], *, guard: float, multiple: float, segment: str
) -> GridRow | None:
    for row in rows:
        if row.guard == guard and row.multiple == multiple and row.segment == segment:
            return row
    return None


def curve(
    rows: Sequence[GridRow], *, axis: str, fixed: float, segment: str
) -> list[tuple[float, GridRow]]:
    """한 축의 공선 — `axis="guard"`면 배수를 `fixed`로 고정하고 가드를 훑는다."""
    points = GUARD_POINTS if axis == "guard" else MULTIPLES
    out: list[tuple[float, GridRow]] = []
    for point in points:
        row = (
            _pick(rows, guard=point, multiple=fixed, segment=segment)
            if axis == "guard"
            else _pick(rows, guard=fixed, multiple=point, segment=segment)
        )
        if row is not None:
            out.append((point, row))
    return out


def bend_verdict(rows: Sequence[GridRow], *, axis: str, fixed: float, segment: str) -> str:
    """완료기준 2·9 — 꺾이는가, 꺾이면 어디서인가. **억지로 최고점을 고르지 않는다.**"""
    points = curve(rows, axis=axis, fixed=fixed, segment=segment)
    if len(points) < 2:
        return "판정 불가 — 점이 모자란다."
    label = "가드" if axis == "guard" else "익절 배수"
    fmt = _axis_fmt(axis)
    values = [(x, row.mean_net_r) for x, row in points]
    best_x, best_r = max(values, key=lambda pair: pair[1])
    last_x, last_r = values[-1]
    first_x, first_r = values[0]
    span = best_r - min(r for _x, r in values)
    if best_x == last_x:
        tail = values[-1][1] - values[-2][1]
        note = " (마지막 한 걸음이 잡음선 안이라 사실상 평평하다)" if abs(tail) < NOISE_R else ""
        return (
            f"**{label} 공선은 이 격자에서도 안 꺾였다** — 끝점 {fmt(last_x)}가 최선"
            f"({_r(last_r)})이고 그 앞 점과의 차가 {_r(tail)}다{note}. "
            f"⚠️ **끝점을 「최적값」으로 인용하지 말 것** — 격자가 거기서 끝났을 뿐이다."
        )
    if best_x == first_x:
        return (
            f"**{label} 공선은 시작점부터 내려간다** — {fmt(first_x)}가 최선({_r(first_r)})이고 "
            f"이후 단조로 나빠진다(끝점 {fmt(last_x)} {_r(last_r)}). 이 방향으로는 여지가 없다."
        )
    return (
        f"**{label} 공선이 {fmt(best_x)}에서 꺾인다**({_r(best_r)}) — 그 뒤 끝점 {fmt(last_x)}는 "
        f"{_r(last_r)}로 내려간다(전 구간 진폭 {span:.4f}R). ⚠️ **argmax를 채택 권고로 쓰지 "
        f"않는다**(WAN-161) — 이 줄은 공선의 **모양**이다."
    )


def best_by(rows: Sequence[GridRow], *, axis: str, fixed: float, segment: str) -> float | None:
    """그 조건에서 거래당 net R이 가장 큰 축 값."""
    points = curve(rows, axis=axis, fixed=fixed, segment=segment)
    if not points:
        return None
    return max(points, key=lambda pair: pair[1].mean_net_r)[0]


def interaction_line(rows: Sequence[GridRow], *, segment: str) -> str:
    """완료기준 10 — *「가드를 올리면 최적 배수가 움직이는가」*. 따로 돌려서는 못 얻는 답."""
    best = {g: best_by(rows, axis="multiple", fixed=g, segment=segment) for g in GUARD_POINTS}
    found = {g: m for g, m in best.items() if m is not None}
    if not found:
        return "판정 불가 — 행이 없다."
    distinct = set(found.values())
    listing = " / ".join(f"{g:.2%}→{m:g}R" for g, m in found.items())
    if len(distinct) == 1:
        only = next(iter(distinct))
        return (
            f"**가드를 바꿔도 최적 배수가 안 움직인다** ({segment}) — 다섯 가드 전부 "
            f"{only:g}R이다. 두 눈금이 얽혀 있다는 산수(`손익분기 승률 = (1+비용R)/(1+목표R)`)는 "
            "맞지만, **이 격자의 폭에서는 그 이동이 관측되지 않는다** — 가드가 옮기는 비용R이 "
            "배수 순위를 뒤집을 만큼 크지 않다."
        )
    return (
        f"🚨 **가드를 올리면 최적 배수가 실제로 움직인다** ({segment}) — {listing}. "
        "**따로 돌렸으면 서로를 무효화했을 자리다**(이 이슈가 두 눈금을 합친 이유). "
        "⚠️ 단 argmax는 채택 근거가 아니다(WAN-161)."
    )


def flip_rows(rows: Sequence[GridRow]) -> list[tuple[str, str, str, bool]]:
    """완료기준 4·11 — 앞구간(`is`)에서 고른 값이 뒷구간(`oos_warm`)에서도 최선인가."""
    out: list[tuple[str, str, str, bool]] = []
    for guard in GUARD_POINTS:
        is_best = best_by(rows, axis="multiple", fixed=guard, segment="is")
        oos_best = best_by(rows, axis="multiple", fixed=guard, segment=PRIMARY_OOS)
        out.append(
            (
                f"가드 {guard:.2%} 고정 → 최적 배수",
                f"{is_best:g}R" if is_best is not None else "—",
                f"{oos_best:g}R" if oos_best is not None else "—",
                is_best != oos_best,
            )
        )
    for multiple in MULTIPLES:
        is_best = best_by(rows, axis="guard", fixed=multiple, segment="is")
        oos_best = best_by(rows, axis="guard", fixed=multiple, segment=PRIMARY_OOS)
        out.append(
            (
                f"배수 {multiple:g}R 고정 → 최적 가드",
                f"{is_best:.2%}" if is_best is not None else "—",
                f"{oos_best:.2%}" if oos_best is not None else "—",
                is_best != oos_best,
            )
        )
    return out


def judgment_points(rows: Sequence[GridRow]) -> list[tuple[float, float]]:
    """leave-one-out을 걸 점 — **채택 점**과 **주 구간 최선 점**(꺾임 근방, 완료기준 5)."""
    points: list[tuple[float, float]] = [(ADOPTED_STOP_GUARD, ADOPTED_MULTIPLE)]
    subset = [r for r in rows if r.segment == PRIMARY_OOS]
    if subset:
        best = max(subset, key=lambda r: r.mean_net_r)
        if (best.guard, best.multiple) not in points:
            points.append((best.guard, best.multiple))
    return points


# --------------------------------------------------------------------------- #
# 표 · 요약
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def grid_from_csv(path: Path = GRID_CSV_PATH) -> list[GridRow]:
    frame = pd.read_csv(path)
    return [GridRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def loo_from_csv(path: Path = LOO_CSV_PATH) -> list[LooRow]:
    frame = pd.read_csv(path)
    return [LooRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def checksum_from_csv(path: Path = CHECKSUM_CSV_PATH) -> list[ChecksumRow]:
    frame = pd.read_csv(path)
    return [ChecksumRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def stop_width_from_csv(path: Path = STOP_WIDTH_CSV_PATH) -> list[StopWidthRow]:
    frame = pd.read_csv(path)
    return [StopWidthRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def _net_table(rows: Sequence[GridRow], *, segment: str) -> list[str]:
    """가드(행) × 배수(열) — 거래당 net R (거래 수)."""
    out = ["| 가드 \\ 배수 | " + " | ".join(f"{m:g}R" for m in MULTIPLES) + " |"]
    out.append("| -- | " + " | ".join(["--:"] * len(MULTIPLES)) + " |")
    for guard in GUARD_POINTS:
        cells: list[str] = []
        for multiple in MULTIPLES:
            row = _pick(rows, guard=guard, multiple=multiple, segment=segment)
            if row is None:
                cells.append("—")
                continue
            mark = " ✅" if row.adopted_point else ""
            cells.append(f"{_r(row.mean_net_r)} ({row.num_trades:,}){mark}")
        out.append(f"| **{guard:.2%}** | " + " | ".join(cells) + " |")
    return out


def _gross_table(rows: Sequence[GridRow], *, segment: str) -> list[str]:
    out = ["| 가드 \\ 배수 | " + " | ".join(f"{m:g}R" for m in MULTIPLES) + " |"]
    out.append("| -- | " + " | ".join(["--:"] * len(MULTIPLES)) + " |")
    for guard in GUARD_POINTS:
        cells = []
        for multiple in MULTIPLES:
            row = _pick(rows, guard=guard, multiple=multiple, segment=segment)
            cells.append(_r(row.mean_gross_r) if row else "—")
        out.append(f"| **{guard:.2%}** | " + " | ".join(cells) + " |")
    return out


def gate_line(rows: Sequence[GridRow], *, segment: str) -> str:
    """완료기준 3 — 표본 게이트가 어느 점에서 깨지는가. 깨지면 그것 자체가 답의 일부다."""
    broken = [row for row in rows if row.segment == segment and row.symbols_below_gate > 0]
    if not broken:
        worst = min(
            (r for r in rows if r.segment == segment),
            key=lambda r: r.min_symbol_trades,
            default=None,
        )
        tail = (
            f" 가장 얇은 칸이 종목당 {worst.min_symbol_trades}거래"
            f"(가드 {worst.guard:.2%} × {worst.multiple:g}R)다."
            if worst is not None
            else ""
        )
        return (
            f"**표본은 이 격자 어디에서도 안 깨진다**(종목당 {MIN_TRADES_PER_SYMBOL}건 게이트, "
            f"{segment}).{tail}"
        )
    listing = ", ".join(
        f"가드 {r.guard:.2%} × {r.multiple:g}R({r.symbols_below_gate}종목)" for r in broken
    )
    return (
        f"🚨 **표본이 깨지는 점이 있다**({segment}) — {listing}. **억지로 살리지 않는다** — "
        "「여기서 표본이 깨졌다」가 답의 일부다(이슈 ★결정이 0.80%를 넣은 이유)."
    )


def build_summary_markdown(
    rows: Sequence[GridRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    widths: Sequence[StopWidthRow],
    *,
    elapsed: float | None = None,
    num_cells: int | None = None,
) -> str:
    seg = PRIMARY_OOS
    adopted = _pick(rows, guard=ADOPTED_STOP_GUARD, multiple=ADOPTED_MULTIPLE, segment=seg)
    best = max((r for r in rows if r.segment == seg), key=lambda r: r.mean_net_r, default=None)
    out: list[str] = [
        "# WAN-381 — 출구 눈금 둘을 한 격자로 (손절폭 가드 5점 × 익절 배수 4점)",
        "",
        "**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`LeverageBookParams()` 그대로 · "
        "`min_stop_distance_fraction=0.003`·`take_profit_r=1.5` 안 건드렸다 · 핀 없음(WAN-305) · "
        "실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        f"팔은 **하나**(오늘 페이퍼가 돌리는 그 규칙)이고 주 수치는 **{seg}**다. "
        "**판정 자는 거래당 net R**이고 괄호는 거래 수다 — 가드를 올리면 거래가 사라지므로 "
        "「좋아진 것」과 **「덜 매매한 것」**을 구분해야 한다(WAN-374/378).",
        "",
        "## 1. 거래당 net R — 가드 × 배수 (주 수치)",
        "",
        *_net_table(rows, segment=seg),
        "",
        "✅ = 채택 좌표(가드 0.30% × 1.5R). "
        f"±{NOISE_R}R 안은 「0과 구분되지 않는다」(WAN-366/370 규약).",
        "",
    ]
    if adopted is not None and best is not None:
        gap = best.mean_net_r - adopted.mean_net_r
        cells = [r for r in rows if r.segment == seg]
        positives = sum(1 for r in cells if r.mean_net_r > 0)
        sign_note = (
            f"🚨 **{len(cells)}조합 전부 음수다** — 고른 것이 있다면 「덜 잃는 좌표」이지 "
            "흑자가 아니다(WAN-370: 비용을 전부 0으로 만들어도 시장에서 얻은 것의 천장이 "
            "＋0.09R)."
            if positives == 0
            else f"📌 **{len(cells)}조합 중 {positives}조합이 양수다** — ⚠️ 그래도 전부 "
            "`baseline`(닿으면 체결) 낙관 렌즈 위 값이고 **체결 보수화(`pen_5bp`)는 범위 "
            "밖**이다. 「흑자를 찾았다」로 인용 금지(WAN-370: 비용을 전부 0으로 만들어도 "
            "시장에서 얻은 것의 천장이 ＋0.09R)."
        )
        out += [
            f"채택 좌표는 **{_r(adopted.mean_net_r)}**({adopted.num_trades:,}거래)이고 격자 최선은 "
            f"**가드 {best.guard:.2%} × {best.multiple:g}R {_r(best.mean_net_r)}**"
            f"({best.num_trades:,}거래) — 차 {_r(gap)}. " + sign_note,
            "",
        ]
    out += [
        "## 2. 🚨 수수료·펀딩 전(gross) — 주 판정 열",
        "",
        *_gross_table(rows, segment=seg),
        "",
        "⚠️ gross는 **수수료·펀딩 전**이고 슬리피지는 체결가에 이미 녹아 있어 빠지지 않는다 "
        "— 상한이 아니다.",
        "",
        "**가드 축**: " + _gross_axis_note(rows, axis="guard", fixed=ADOPTED_MULTIPLE, segment=seg),
        "",
        "**배수 축**: "
        + _gross_axis_note(rows, axis="multiple", fixed=ADOPTED_STOP_GUARD, segment=seg),
        "",
        "## 3. 공선의 모양 (완료기준 2 · 9)",
        "",
        "* **가드**(배수 1.5R 고정): "
        + bend_verdict(rows, axis="guard", fixed=ADOPTED_MULTIPLE, segment=seg),
        "* **익절 배수**(가드 0.30% 고정): "
        + bend_verdict(rows, axis="multiple", fixed=ADOPTED_STOP_GUARD, segment=seg),
        "",
        "## 4. 두 눈금의 상호작용 (완료기준 10 — 이 이슈가 합쳐진 이유)",
        "",
        interaction_line(rows, segment=seg),
        "",
        "## 5. 거래 수와 표본 게이트 (완료기준 3)",
        "",
        gate_line(rows, segment=seg),
        "",
        "| 가드 | 잘린 후보 | 남은 후보 | 거래 수(1.5R) | 종목당 최소 | 손절폭 p10 / 중앙 / p90 |",
        "| -- | --: | --: | --: | --: | -- |",
    ]
    width_by_guard = {w.guard: w for w in widths if w.segment == seg}
    for guard in GUARD_POINTS:
        row = _pick(rows, guard=guard, multiple=ADOPTED_MULTIPLE, segment=seg)
        width = width_by_guard.get(guard)
        dist = (
            f"{width.p10:.3%} / {width.median:.3%} / {width.p90:.3%}" if width is not None else "—"
        )
        if row is None:
            continue
        out.append(
            f"| {guard:.2%} | {row.guard_cut:,} | {row.guard_kept:,} | {row.num_trades:,} | "
            f"{row.min_symbol_trades} | {dist} |"
        )
    out += [
        "",
        "「잘린 후보」는 후보 층(전 구간)이고 「거래 수」는 그 구간 배치 결과다 — 칸 점유·명목 "
        "상한이 그 사이에서 또 깎으므로 두 수는 같지 않다.",
        "",
        "## 6. 앞구간에서 고르고 뒷구간에서 확인 (완료기준 4 · 11)",
        "",
        "| 고정 축 | IS 최적 | " + f"{seg} 최적 | 뒤집힘 |",
        "| -- | -- | -- | -- |",
    ]
    flips = flip_rows(rows)
    for label, is_best, oos_best, flipped in flips:
        out.append(f"| {label} | {is_best} | {oos_best} | {'🚨 예' if flipped else '아니오'} |")
    flip_count = sum(1 for _l, _i, _o, f in flips if f)
    out += [
        "",
        f"**{flip_count}/{len(flips)}줄이 뒤집힌다.** 🚨 **argmax를 채택 권고로 쓰지 않는다** — "
        "이 줄은 「눈금을 앞구간에서 고르면 안 된다」를 세는 데만 쓴다(WAN-161: 배수 argmax가 "
        "8칸 중 7칸 뒤집힘).",
        "",
        "## 7. 위험의 모양 — 채택 좌표 주변",
        "",
        "| 가드 × 배수 | 승률 | 복리 끈 수익 | MDD | 최대 동시 칸 | 상한 발동률 | 청산 |",
        "| -- | --: | --: | --: | --: | --: | --: |",
    ]
    ruined = 0
    for guard, multiple in judgment_points(rows):
        row = _pick(rows, guard=guard, multiple=multiple, segment=seg)
        if row is None:
            continue
        label = f"{guard:.2%} × {multiple:g}R"
        if not wallet_defined(row):
            ruined += 1
            lost = "🚨 정의 상실"
            out.append(
                f"| {label} | {_pct(row.win_rate)} | {lost} | {lost} | {row.peak_concurrency} | "
                f"{_pct(row.clamp_rate)} | {lost} |"
            )
            continue
        out.append(
            f"| {label} | {_pct(row.win_rate)} | {_pct(row.total_return_flat)} | "
            f"{_pct(row.max_drawdown)} | {row.peak_concurrency} | {_pct(row.clamp_rate)} | "
            f"{row.liquidation_events} |"
        )
    if ruined:
        out += [
            "",
            "🚨 **지갑 층 열이 이 좌표에서 뜻을 잃는다** — 복리를 껐는데도(사이징은 초기 자본 "
            "고정, WAN-346 §2) 잔고가 0을 뚫으므로 「자본 대비 비율」(MDD·수익/MDD·동시 리스크·"
            "청산)은 분모가 부호를 바꾸며 무의미해진다. **비율을 내지 않고 「정의 상실」로 "
            "찍는다**(WAN-115가 세운 관행의 이 축 판 · WAN-386과 같은 술어). "
            "**거래당 net R은 이 함정에 안 걸린다** — 분모가 초기 자본으로 사이징된 값이라 "
            "잔고와 무관하다. ⚠️ **팔의 성질이 아니라 좌표의 성질이다**(WAN-378: 108팔 전부 음수).",
        ]
    out += [
        "",
        "## 8. 종목 하나씩 빼보기 (완료기준 5 · 지갑 재배치)",
        "",
        "| 가드 × 배수 | 기준 | 최악(빼면 가장 나빠짐) | 최선 | 부호 유지 |",
        "| -- | --: | -- | -- | -- |",
    ]
    for guard, multiple in judgment_points(rows):
        base_row = _pick(rows, guard=guard, multiple=multiple, segment=seg)
        subset = [
            r for r in loo if r.guard == guard and r.multiple == multiple and r.segment == seg
        ]
        if base_row is None or not subset:
            continue
        worst = min(subset, key=lambda r: r.mean_net_r)
        top = max(subset, key=lambda r: r.mean_net_r)
        same = all((r.mean_net_r >= 0) == (base_row.mean_net_r >= 0) for r in subset)
        out.append(
            f"| {guard:.2%} × {multiple:g}R | {_r(base_row.mean_net_r)} | "
            f"{worst.exclude} {_r(worst.mean_net_r)} | {top.exclude} {_r(top.mean_net_r)} | "
            f"{'예' if same else '🚨 아니오'} |"
        )
    out += [
        "",
        "**지갑을 다시 배치**한다(라벨 필터가 아니다 — WAN-316 스코프 패턴): 종목을 빼면 그 "
        "자본·슬롯을 남은 칸이 쓴다.",
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
        verdict + ".",
        "",
        "📌 **(d)가 이 표를 WAN-386과 이어 붙인다** — 겹치는 6조합(가드 0.30·0.40·0.50% × "
        "1.0·1.5R)이 비트 일치해야 이 이슈가 **새로 연 점**(가드 0.60·0.80% · 배수 0.6·0.8R)을 "
        "그 표와 한 줄에 놓을 수 있다.",
        "",
        "## 10. 경고 (전부 유효)",
        "",
        "* ❌ **가드·익절 배수 기본값 전환 제안이 아니다** — `min_stop_distance_fraction=0.003`"
        "(WAN-76/79)·`take_profit_r=1.5`(WAN-81/90) 불변이고 변경은 **재-베이스라인 = 사용자 "
        "결정**이다. 개발자 임의 착수 금지.",
        "* 🚨 **「가드를 올리면 흑자」로 기대하지 말 것** — 이 격자도 전 조합 음수다(WAN-378의 "
        "108팔 전부 음수 · WAN-370의 「비용 0에서도 천장 ＋0.09R」과 같은 자리).",
        "* 🚨 **끝점을 「최적값」으로 인용하지 말 것** — 공선이 안 꺾이면 그건 「거기가 최적」이 "
        "아니라 「격자가 거기서 끝났다」다.",
        "* ⚠️ **거래를 줄여서 좋아 보이는 것**과 구분할 것 — §5의 거래 수·표본 게이트가 그 자다.",
        "* ⚠️ 판단은 북에서(WAN-341) · 핀 없이(WAN-305) · 전부 `baseline`(닿으면 체결) 낙관 렌즈 "
        "위 값이고 **체결 보수화(`pen_5bp`)는 범위 밖** · 6년 MDD는 폭락 미포함 **바닥선**.",
        "* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 표는 *어디서 손절을 "
        "접고 어디서 이익을 챙길 것인가*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 "
        "않는다. **다른 질문이다.**",
        "* ⚠️ **재무장 일정(재진입)은 채택 배수의 것을 쓴다** — 재진입 후보는 base 후보의 "
        "per-cell 시퀀싱에서 나오므로(WAN-261) 배수마다 다시 파생하면 재무장 **시점**까지 "
        "배수를 따라 움직여 축이 둘이 된다(`backtest.confirmation_arm`이 명시한 알려진 한계).",
    ]
    if elapsed is not None:
        cell_note = f"{num_cells}칸" if num_cells is not None else "칸 수 미상"
        out += [
            "",
            f"실측 비용: **{elapsed:,.0f}초**({cell_note} · 후보 생성 1회 + 배치 반복). "
            "⚠️ **다른 모듈의 칸 비용을 옮기지 말 것**(WAN-203 → WAN-312 · WAN-383 선례).",
        ]
    return "\n".join(out) + "\n"


def _gross_axis_note(rows: Sequence[GridRow], *, axis: str, fixed: float, segment: str) -> str:
    """그 축에서 gross가 움직이는가 — 「선별인가 비용 버리기인가」를 가르는 한 줄."""
    points = curve(rows, axis=axis, fixed=fixed, segment=segment)
    if len(points) < 2:
        return "판정 불가 — 점이 모자란다."
    fmt = _axis_fmt(axis)
    listing = " → ".join(f"{fmt(x)} {row.mean_gross_r:+.4f}" for x, row in points)
    span = max(r.mean_gross_r for _x, r in points) - min(r.mean_gross_r for _x, r in points)
    crosses = any(r.mean_gross_r > 0 for _x, r in points)
    if span < NOISE_R:
        return (
            f"{listing} — **평평하다**(진폭 {span:.4f}R < {NOISE_R}R). 이 축이 하는 일은 "
            "**「좋은 거래 선별」이 아니라 「비싼 거래 버리기」**다: net R이 올라도 수수료 전 "
            "기대값은 안 움직인다. **그러면 이 축은 닫힌 것**이고, 그 결론 자체가 산출물이다."
        )
    marker = (
        " 🚨 **어느 점에서 gross가 0을 넘는다** — 「수수료 전에도 못 번다」가 그 점에서는 "
        "성립하지 않는다."
        if crosses
        else " 어느 점에서도 **gross가 음수**다 — 수수료를 0으로 만들어도 못 번다."
    )
    return f"{listing} — **움직인다**(진폭 {span:.4f}R).{marker}"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-381 출구 눈금 격자 (가드 × 익절 배수)")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    parser.add_argument("--pilot", action="store_true", help="한 칸 견적(첫 종목 4h)")
    parser.add_argument("--no-checksum", action="store_true", help="검산을 건너뛴다")
    args = parser.parse_args(argv)

    if args.from_csv:
        SUMMARY_PATH.write_text(
            build_summary_markdown(
                grid_from_csv(),
                loo_from_csv() if LOO_CSV_PATH.exists() else [],
                checksum_from_csv() if CHECKSUM_CSV_PATH.exists() else [],
                stop_width_from_csv() if STOP_WIDTH_CSV_PATH.exists() else [],
            ),
            encoding="utf-8",
        )
        print(f"요약 갱신: {SUMMARY_PATH}")
        return 0

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    if args.pilot:
        symbols, timeframes = symbols[:1], ["4h"]
        print(f"[wan381] 파일럿 — {symbols[0]} 4h (⚠️ 이 값을 격자 견적으로 인용 금지)")

    started = time.monotonic()
    payloads = build_payloads(
        symbols,
        timeframes,
        start=args.start,
        end=args.end,
        jobs=args.jobs,
        arms=(ARM_BASE,),
        multiples=MULTIPLES,
    )
    built = time.monotonic() - started
    print(f"[wan381] 후보 생성 {built:,.0f}초 ({len(payloads)}칸)", flush=True)

    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)
    num_symbols = len({p.symbol for p in payloads})
    rows = build_grid(payloads, start_ms=start_ms, end_ms=end_ms, num_symbols=num_symbols)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(GRID_CSV_PATH, index=False)

    widths = build_stop_widths(payloads, start_ms=start_ms, end_ms=end_ms)
    rows_to_frame(widths).to_csv(STOP_WIDTH_CSV_PATH, index=False)

    loo = build_leave_one_out(
        payloads, start_ms=start_ms, end_ms=end_ms, points=judgment_points(rows)
    )
    rows_to_frame(loo).to_csv(LOO_CSV_PATH, index=False)

    checks: list[ChecksumRow] = []
    if not args.no_checksum:
        checks = run_checksum(
            payloads,
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
            cross_check=on_adopted_coordinates(symbols, timeframes),
        )
        rows_to_frame(checks).to_csv(CHECKSUM_CSV_PATH, index=False)

    elapsed = time.monotonic() - started
    SUMMARY_PATH.write_text(
        build_summary_markdown(rows, loo, checks, widths, elapsed=elapsed, num_cells=len(payloads)),
        encoding="utf-8",
    )
    print(f"[wan381] 완료 {elapsed:,.0f}초 → {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
