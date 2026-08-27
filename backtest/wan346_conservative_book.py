"""WAN-346: 가장 보수적인 가정 위의 채택 북 — 2×2 격자 + 복리 착시 없이 읽기.

## 사용자 요청 (2026-08-21)

*"1분만에 익절된 그것마저 가장 보수적으로 쟀을 때 전체 거래내역을 보고싶어. 그리고 그렇게
레버리지 북으로 설정했을 때 결론적으로 MDD와 수익, 리스크가 어떻게 나오는지."* + *"복리도
그렇게 하지 말고."*

## 축 — 보수 축 둘을 **쌓아서** 2×2

두 축은 **다른 질문**이고 이 저장소에서 함께 측정된 적이 없다:

* **가로축 `pen_5bp`** = *「주문이 채워지느냐」*(큐 우선순위, WAN-96/124).
* **세로축 `no_same_step_tp`** = *「채워진 뒤 그 1분 안의 순서」*(WAN-336).

|  | `baseline` | `pen_5bp` |
| -- | -- | -- |
| 같은 분 익절 허용(현행) | **A** = 인자 없는 채택 북 | **B** |
| 같은 분 익절 금지 | **C** | **D = 가장 보수적** |

🚨 **직교한다** — WAN-336이 *"모든 체결 보수화 관문이 이 낙관을 통과시켜 왔다"*고 한 이유가
이것이다. 그래서 D는 이 저장소가 낸 적 없는 팔이다.

## 복리 착시 없이 (§2 · 사용자 명시 요청)

📌 **먼저 못 박는다 — 지금 계산은 「평균 × 횟수 제곱」이 아니다.** `build_result_from_trades`가
초기자본에서 시작해 **청산 시각 순으로 실현손익을 하나씩 더하는 진짜 장부**이고 MDD도 그
곡선에서 나온다. 산수는 정직하다. 문제는 **사이징이 「현재 자본의 %」**라 돈이 불면 베팅도
커진다는 것이다 — 6년 × 수천 거래면 작은 우위가 기하급수로 부풀어 그 수가 현실에서 달성
가능하지 않다(호가 깊이 · 6년 내내 같은 엣지 · 낙관 체결).

그래서 팔마다 **복리 켠 판과 끈 판을 나란히** 낸다(`compound_sizing=False` = 베팅 크기를
초기 자본에 못 박음, 옵트인 · 켜면 비트 재현). 그리고 **총수익 %를 헤드라인으로 쓰지
않는다** — 거래당 자(net R 합 · 거래당 중앙값 · 승률 · 손익비)와 CAGR을 함께 싣는다.

## 리스크는 네 열로 (§3)

**MDD**(주 판정) · **최대 동시 리스크**(계획) · **실효 동시 리스크**(WAN-312) · **청산 건수**.
🚨 **「청산 0건」을 안전 근거로 쓰지 말 것** — 사이징이 자본의 %라 연쇄 손실로는 청산 조건이
**구조적으로 안 걸린다**(WAN-312 §4: 청산 0건인데 MDD 98%인 셀이 있었다). 그래서 요약이
**파괴선(MDD 50%)** 을 함께 찍는다.

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 4TF 한 지갑 · 못 박은 6년 창 · 재진입 ON(band) ·
cap_only 5배 · 존폭 필터 1.28 · 오프셋 2bp · 손절폭 가드 0.3% · 유동성 한도 채택값.
구간은 `oos_warm`(주, WAN-166) + `oos`(스트레스) + `full`·`is` 병기.

## 검산

* **(a) 팔 A ≡ 인자 없는 채택 북** — `wan336.verify_adopted_identity`를 그대로 재사용한다
  (같은 payload를 `book_cli`의 채택 경로 두 단계에 넣어 대조). 회귀 테스트가 `run_cells`
  호출 인자까지 채택 경로와 대조해 **동작으로** 고정한다.
* **(b) 반사실 팔이 실제로 동작했나** — 팔 C·D의 후보 층 카운터
  (`_Candidate.same_step_take_profit`)가 **전 구간 0**이어야 한다(WAN-336 검산 (d)와 같은 자).
* **(c) 복리 끈 판이 라벨이 아니다** — 같은 팔·같은 구간에서 거래 수나 총수익이 실제로
  달라져야 한다(안 달라지면 노브가 안 걸린 것이다).

재현:

```
uv run python -m backtest.wan346_conservative_book --arms A --jobs 4          # 배선 검산 먼저
uv run python -m backtest.wan346_conservative_book --arms B,C,D --jobs 4 --append
uv run python -m backtest.wan346_conservative_book --from-csv                 # 요약만
```
"""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import (
    BookSegment,
    book_equity_to_display_frame,
    book_trades_to_display_frame,
    net_r,
)
from backtest.leverage_book import PlacedSetup
from backtest.models import Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, _segment_cells, run_cells
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import (
    ADOPTED_CELL_KWARGS,
    book_segments_for_payloads,
    verify_adopted_identity,
)

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan346_conservative_book.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan346_conservative_book_loo.csv"
SUMMARY_PATH = REPORTS_DIR / "wan346_conservative_book_summary.md"

#: 복리 팔 이름 — CSV 열 값이자 파일 이름 조각.
COMPOUND_ON = "on"
COMPOUND_OFF = "off"
COMPOUND_ORDER: tuple[str, str] = (COMPOUND_ON, COMPOUND_OFF)

#: 파괴선 — MDD가 이 선을 넘으면 「청산 0건」이라도 계좌는 사실상 끝났다(WAN-312 §4).
RUIN_MDD = 0.50

#: 6년 MDD가 폭락 미포함 **바닥선**임을 요약이 매번 밝히도록 문장을 한 곳에 둔다.
_FLOOR_NOTE = "6년 MDD는 2018·2020-03 폭락을 **포함하지 않는** 창이라 천장이 아니라 **바닥선**이다"


@dataclass(frozen=True)
class Arm:
    """보수 축 2×2의 한 팔.

    `lens`가 `None`이면 채택 렌즈(`baseline`)라 `run_cells`에 아무것도 넘기지 않는다 —
    「채택 기본값을 CLI가 복사하지 않는다」는 이 저장소의 규약(WAN-159 `UNSET` 계열)이다.
    """

    name: str
    lens: str | None
    no_same_step_tp: bool
    label: str

    @property
    def lens_name(self) -> str:
        return self.lens or harness.BASELINE_FILL.name

    @property
    def is_adopted(self) -> bool:
        """이 팔이 **인자 없는 채택 북** 그 자체인가 — 검산 (a)를 걸 수 있는 유일한 팔."""
        return self.lens is None and not self.no_same_step_tp


ARMS: tuple[Arm, ...] = (
    Arm("A", None, False, "채택 북(현행) = 인자 없는 backtest.run"),
    Arm("B", "pen_5bp", False, "체결 보수화만"),
    Arm("C", None, True, "같은 분 익절 금지만"),
    Arm("D", "pen_5bp", True, "가장 보수적 — 두 축을 쌓음"),
)
ARMS_BY_NAME: dict[str, Arm] = {a.name: a for a in ARMS}
ARM_ORDER: tuple[str, ...] = tuple(a.name for a in ARMS)
ADOPTED_ARM = "A"
MOST_CONSERVATIVE_ARM = "D"

CSV_KEYS: tuple[str, ...] = ("arm", "compounding", "segment")
LOO_CSV_KEYS: tuple[str, ...] = ("arm", "compounding", "segment", "excluded")


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class ConservativeRow(BaseModel):
    """한 (팔, 복리, 구간)의 북 집계 — 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    arm: str
    arm_label: str
    lens: str
    no_same_step_tp: bool
    compounding: str
    segment: str

    num_cells: int
    num_trades: int
    win_rate: float

    # §2 — 복리 착시 없이 읽는 자들. `total_return`은 **헤드라인이 아니다**.
    total_return: float
    """6년 복리 총수익. ⚠️ 수천 거래 복리라 실현 수익이 아니다(WAN-169/213) — 아래 거래당
    자·CAGR과 **반드시 나란히** 읽는다."""
    cagr: float | None
    """연환산 수익률. 자본이 0 이하로 간 팔(총수익 ≤ −100%)이면 None."""
    span_years: float
    """자본곡선이 실제로 덮은 햇수 — CAGR의 지수."""
    net_pnl: float
    """거래 순손익의 **단순 합**(USD). 복리 곡선의 총수익과 다른 자다."""
    net_r: float
    """거래당 실현 net R의 합 — 크기 정규화(WAN-154 `mean_net_r`와 같은 자)."""
    mean_net_r: float
    """거래당 net R 평균 = 「실력」. 복리와 무관하다."""
    median_net_r: float
    """거래당 net R 중앙값 — 소수 대박이 평균을 끌지 못하게."""
    profit_factor: float | None
    """총이익 ÷ 총손실(손익비). 손실이 0이면 None."""

    # §3 — 리스크 네 열 + 파괴선.
    max_drawdown: float
    return_over_mdd: float | None
    """⚠️ 분자가 6년 복리 총수익이라 **절댓값은 읽을 수 없다** — 팔 사이 배율로만 본다."""
    ruin: bool
    """MDD가 파괴선(50%)을 넘었나 — 「청산 0건」이 안전을 뜻하지 않는다는 표시(WAN-312 §4)."""
    peak_concurrency: int
    max_concurrent_risk: float
    max_effective_concurrent_risk: float
    liquidation_events: int

    # 축이 실제로 걸렸는지 보이는 열들.
    same_step_tp_trades: int
    same_step_tp_trade_share: float
    candidate_same_step_tps: int
    """후보 층(시퀀싱 전) 카운터 합 — 반사실 팔에서는 **정의상 0**이어야 한다(검산 (b))."""
    reentry_trades: int
    """재진입 후보로 배치된 거래 수(WAN-273 채택 규칙이 실제로 도는지의 계측)."""


class ConservativeLooRow(BaseModel):
    """종목 하나를 뺀 **지갑 재배치** 결과 (완료기준 5 — 라벨 필터가 아니다)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    compounding: str
    segment: str
    excluded: str
    """빼낸 종목(`"-"`이면 전 종목 = 기준 행)."""
    num_trades: int
    total_return: float
    max_drawdown: float
    mean_net_r: float
    net_r: float


# --------------------------------------------------------------------------- #
# 거래 단위 자 — 복리와 무관한 「실력」
# --------------------------------------------------------------------------- #


def trade_rulers(pairs: Sequence[tuple[Trade, PlacedSetup]]) -> dict[str, float]:
    """거래·배치 짝에서 §2의 거래당 자와 §1의 라벨 카운터를 함께 낸다."""
    if not pairs:
        return {
            "net_pnl": 0.0,
            "net_r": 0.0,
            "mean_net_r": 0.0,
            "median_net_r": 0.0,
            "same_step_tp_trades": 0.0,
            "reentry_trades": 0.0,
        }
    rs = [net_r(trade, placement) for trade, placement in pairs]
    return {
        "net_pnl": sum(trade.realized_pnl for trade, _p in pairs),
        "net_r": sum(rs),
        "mean_net_r": sum(rs) / len(rs),
        "median_net_r": float(pd.Series(rs).median()),
        "same_step_tp_trades": float(sum(1 for _t, p in pairs if p.same_step_take_profit)),
        "reentry_trades": float(sum(1 for _t, p in pairs if p.is_reentry)),
    }


_MS_PER_YEAR = 365.25 * 24 * 3_600_000


def span_years(segment: BookSegment) -> float:
    """자본곡선이 실제로 덮은 햇수 — 첫 진입부터 마지막 청산까지.

    창 인자(`start`/`end`)가 아니라 **실제로 거래가 있었던 구간**을 쓴다. `oos_warm`은 창이
    아니라 칸별 경계로 잘리므로(WAN-166) 인자 창을 쓰면 CAGR이 조용히 낙관이 된다.
    """
    curve = segment.result.equity_curve
    if len(curve) < 2:
        return 0.0
    return (curve[-1].time - curve[0].time) / _MS_PER_YEAR


#: CAGR을 낼 수 있는 최소 구간(년). 이보다 짧으면 연환산은 측정이 아니라 **외삽**이다 —
#: 석 달치 +1.9%가 연 1,857%로 찍히면 읽는 사람이 정반대로 읽는다. 채택 좌표의 네 구간은
#: 전부 1년을 크게 넘으므로(6년 창) 이 가드는 축소 실행에서만 걸린다.
_MIN_CAGR_YEARS = 1.0


def cagr(total_return: float, years: float) -> float | None:
    """연환산 수익률. 6년 총수익보다 읽을 수 있는 수다(§2-7).

    **내지 않는 경우가 둘 있다**: (1) 자본이 0 이하로 간 팔(총수익 ≤ −100%) — 실수
    거듭제곱이 정의되지 않고 억지로 −100%를 찍으면 「파산」과 「−99.9%」가 같아 보인다.
    (2) 구간이 1년 미만 — 짧은 구간의 연환산은 측정이 아니라 외삽이라 수가 폭주한다.
    """
    if years < _MIN_CAGR_YEARS or total_return <= -1.0:
        return None
    return math.pow(1.0 + total_return, 1.0 / years) - 1.0


def _candidate_same_step_tps(payloads: Sequence[CellPayload], segment: str) -> int:
    """후보 층(시퀀싱 전) 카운터 합 — 검산 (b)의 한쪽.

    북이 실제로 받는 **그 후보 집합**에서 센다(`_segment_cells` 재사용) — 그래야 두 수가
    같은 모집단의 부분·전체가 된다.
    """
    return sum(
        1
        for cell in _segment_cells(payloads, segment, "", include_reentry=True)
        for cand in cell.candidates
        if cand.same_step_take_profit
    )


def _to_row(
    *,
    arm: Arm,
    compounding: str,
    segment: BookSegment,
    payloads: Sequence[CellPayload],
) -> ConservativeRow:
    row = segment.row
    pairs = segment.trades_with_placements()
    rulers = trade_rulers(pairs)
    years = span_years(segment)
    return ConservativeRow(
        arm=arm.name,
        arm_label=arm.label,
        lens=arm.lens_name,
        no_same_step_tp=arm.no_same_step_tp,
        compounding=compounding,
        segment=segment.segment,
        num_cells=row.num_cells,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        cagr=cagr(row.total_return, years),
        span_years=years,
        net_pnl=rulers["net_pnl"],
        net_r=rulers["net_r"],
        mean_net_r=rulers["mean_net_r"],
        median_net_r=rulers["median_net_r"],
        profit_factor=segment.result.metrics.profit_factor,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd,
        ruin=row.max_drawdown >= RUIN_MDD,
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=row.max_concurrent_risk,
        max_effective_concurrent_risk=row.max_effective_concurrent_risk,
        liquidation_events=row.liquidation_events,
        same_step_tp_trades=int(rulers["same_step_tp_trades"]),
        same_step_tp_trade_share=(
            rulers["same_step_tp_trades"] / row.num_trades if row.num_trades else 0.0
        ),
        candidate_same_step_tps=_candidate_same_step_tps(payloads, segment.segment),
        reentry_trades=int(rulers["reentry_trades"]),
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _loo_rows(
    *,
    arm: Arm,
    compounding: str,
    payloads: Sequence[CellPayload],
    symbols: Sequence[str],
    start_ms: int,
    end_ms: int,
    compound_sizing: bool,
) -> list[ConservativeLooRow]:
    """종목을 하나씩 뺀 **지갑 재배치** — 라벨 필터가 아니다(WAN-316 스코프 패턴).

    후보 생성이 비용의 전부이고 배치는 싸므로 12종목 LOO가 사실상 공짜다. 라벨만 걸러 내면
    「그 종목이 안 썼을 자본을 다른 칸이 쓴다」는 북의 본질이 빠져 per-cell 표가 된다.
    """
    # 🚨 조용한 실패 방지 — 심볼 표기가 어긋나면 아무것도 안 빠져 **모든 LOO 행이 기준
    # 행과 같아지고**, 그러면 「한 종목이 만드는 결과가 아니다」가 근거 없이 만들어진다.
    present = {p.symbol for p in payloads}
    unmatched = [s for s in symbols if s not in present]
    if present and unmatched:
        raise AssertionError(
            f"leave-one-out이 아무 칸도 빼지 못했습니다: {unmatched} — 심볼 표기가 "
            f"payload({sorted(present)[0]!r} 형식)와 어긋납니다."
        )

    rows: list[ConservativeLooRow] = []
    for excluded in ("-", *symbols):
        scoped = [p for p in payloads if p.symbol != excluded]
        if not scoped:
            continue
        for seg in book_segments_for_payloads(
            scoped,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=(PRIMARY_OOS,),
            compound_sizing=compound_sizing,
        ):
            rulers = trade_rulers(seg.trades_with_placements())
            rows.append(
                ConservativeLooRow(
                    arm=arm.name,
                    compounding=compounding,
                    segment=seg.segment,
                    excluded=excluded,
                    num_trades=seg.row.num_trades,
                    total_return=seg.row.total_return,
                    max_drawdown=seg.row.max_drawdown,
                    mean_net_r=rulers["mean_net_r"],
                    net_r=rulers["net_r"],
                )
            )
    return rows


#: 거래별 CSV·시드곡선을 남길 (팔, 구간) — 사용자가 요청한 「전체 거래내역」이 팔 D다.
#: 팔 A의 주 구간은 대조용(검산 (a)가 「이 파일이 곧 채택 북의 거래」임을 보증한다).
DETAIL_TARGETS: tuple[tuple[str, str], ...] = (
    (MOST_CONSERVATIVE_ARM, harness.SEGMENT_FULL),
    (MOST_CONSERVATIVE_ARM, PRIMARY_OOS),
    (ADOPTED_ARM, PRIMARY_OOS),
)


def detail_paths(arm: str, segment: str) -> tuple[Path, Path]:
    """(거래별 CSV, 시드곡선 CSV) 경로."""
    return (
        REPORTS_DIR / f"wan346_trades_{arm}_{segment}.csv",
        REPORTS_DIR / f"wan346_equity_{arm}_{segment}.csv",
    )


def write_details(arm: Arm, segments: Sequence[BookSegment], *, log: bool = True) -> list[Path]:
    """이 팔이 남겨야 할 거래별 내역·시드곡선을 쓴다 (완료기준 3 · §0-2)."""
    written: list[Path] = []
    for target_arm, target_segment in DETAIL_TARGETS:
        if target_arm != arm.name:
            continue
        chosen = next((s for s in segments if s.segment == target_segment), None)
        if chosen is None:
            continue
        trades_path, equity_path = detail_paths(arm.name, target_segment)
        book_trades_to_display_frame(chosen).to_csv(trades_path, index=False)
        book_equity_to_display_frame(chosen).to_csv(equity_path, index=False)
        written += [trades_path, equity_path]
        if log:
            print(
                f"[wan346] 거래별 내역: {trades_path} ({chosen.row.num_trades}건)",
                flush=True,
            )
    return written


def run_arm(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arm: Arm,
    *,
    start: str,
    end: str,
    jobs: int,
    segments: Sequence[str] = SEGMENT_ORDER,
    log: bool = True,
) -> tuple[list[ConservativeRow], list[ConservativeLooRow], float | None]:
    """한 팔의 후보를 **한 번** 만들고 복리 켠 판·끈 판·종목 LOO를 전부 낸다.

    후보 생성이 비용의 전부이므로(북 한 팔 ~66분, WAN-330 실측) 배치만 여러 번 돌린다 —
    복리 노브도 LOO도 **같은 후보 위의 다른 배치**라 사실상 공짜다.
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        # ⚠️ 채택 팔에서만 `engine_check`를 켠다 — 그 검산은 격리 성과가 `harness.run_once`
        # (보수 축이 없는 per-cell)와 비트 일치하는지 보는 것이라, 축을 켠 팔에서는
        # **당연히** 어긋난다(WAN-336 관행 그대로).
        engine_check=arm.is_adopted,
        no_same_step_tp=arm.no_same_step_tp,
        # `None`이면 인자를 **넘기지 않는다** — 채택 렌즈를 CLI가 복사하면 기본값이
        # 움직일 때 이 경로만 옛 값을 물고 돈다(WAN-159 `UNSET` 계열 규약).
        fill=harness.fill_preset(arm.lens) if arm.lens else None,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
        invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
    )
    identity: float | None = None
    if arm.is_adopted:
        identity = verify_adopted_identity(payloads, start_ms=start_ms, end_ms=end_ms)
        if log:
            print(f"[wan346] 검산(a) 채택 경로 최대차: {identity:.2e}", flush=True)

    rows: list[ConservativeRow] = []
    loo: list[ConservativeLooRow] = []
    for compounding in COMPOUND_ORDER:
        compound_sizing = compounding == COMPOUND_ON
        book = book_segments_for_payloads(
            payloads,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=segments,
            compound_sizing=compound_sizing,
        )
        rows += [
            _to_row(arm=arm, compounding=compounding, segment=seg, payloads=payloads)
            for seg in book
        ]
        loo += _loo_rows(
            arm=arm,
            compounding=compounding,
            payloads=payloads,
            symbols=[harness.normalize_symbol(s) for s in symbols],
            start_ms=start_ms,
            end_ms=end_ms,
            compound_sizing=compound_sizing,
        )
        if compound_sizing:
            write_details(arm, book, log=log)
    return rows, loo, identity


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    arms: Sequence[str] = ARM_ORDER,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    on_arm: Callable[[list[ConservativeRow], list[ConservativeLooRow]], None] | None = None,
    log: bool = True,
) -> tuple[list[ConservativeRow], list[ConservativeLooRow]]:
    """팔마다 4TF 지갑을 한 실행으로 돈다.

    📌 팔마다 즉시 적재한다(`on_arm`) — 한 팔이 12종목 × 4TF라 한 시간 안팎이고, 팔은 각자
    독립 지갑이라 중간에 끊겨도 끝난 팔은 보존된다. **끊길 수 없는 것은 한 팔 안의 4TF뿐이다**
    (북은 이어붙일 수 없다 — WAN-316).
    """
    rows: list[ConservativeRow] = []
    loo: list[ConservativeLooRow] = []
    for name in arms:
        arm = ARMS_BY_NAME[name]
        t0 = time.time()
        arm_rows, arm_loo, _identity = run_arm(
            symbols,
            timeframes,
            arm,
            start=start,
            end=end,
            jobs=jobs,
            segments=segments,
            log=log,
        )
        rows.extend(arm_rows)
        loo.extend(arm_loo)
        if on_arm is not None:
            on_arm(arm_rows, arm_loo)
        if log:
            print(
                f"[wan346] {arm.name}({arm.label}): {len(arm_rows)}행 ({time.time() - t0:.0f}s)",
                flush=True,
            )
    return rows, loo


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[ConservativeRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def loo_to_frame(rows: Sequence[ConservativeLooRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _missing(value: object) -> bool:
    """`None`과 **NaN을 함께** 결측으로 본다 — CSV 왕복이 `None`을 NaN으로 바꾼다."""
    return value is None or (isinstance(value, float) and pd.isna(value))


def _pct(value: object) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:.2f}%"  # type: ignore[arg-type]


def _pp(value: object) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:+.2f}%p"  # type: ignore[arg-type]


def _num(value: object, digits: int = 3) -> str:
    return "—" if _missing(value) else f"{float(value):.{digits}f}"  # type: ignore[arg-type]


def _pick(frame: pd.DataFrame, arm: str, segment: str, compounding: str) -> pd.Series | None:
    hit = frame[
        (frame["arm"] == arm)
        & (frame["segment"] == segment)
        & (frame["compounding"] == compounding)
    ]
    return None if hit.empty else hit.iloc[0]


def _verdict(frame: pd.DataFrame) -> str:
    """완료기준 2·4 — 한 문단 판정. **총수익 %를 단독 헤드라인으로 쓰지 않는다**(§2-8)."""
    adopted = _pick(frame, ADOPTED_ARM, PRIMARY_OOS, COMPOUND_ON)
    worst = _pick(frame, MOST_CONSERVATIVE_ARM, PRIMARY_OOS, COMPOUND_ON)
    if adopted is None or worst is None:
        return (
            "⚠️ **판정 불가** — 팔 A와 팔 D의 주 구간 행이 둘 다 있어야 2×2가 성립한다"
            f"(지금 있는 팔: {', '.join(sorted(frame['arm'].unique()))})."
        )
    ruin_bit = (
        " 🚨 **파괴선(MDD 50%)을 넘는다** — 청산 트리거가 0건이어도 계좌는 사실상 끝났다"
        "(WAN-312 §4: 사이징이 자본의 %라 연쇄 손실로는 청산 조건이 구조적으로 안 걸린다)."
        if bool(worst["ruin"])
        else ""
    )
    return (
        f"📌 **가장 보수적인 팔 D(`pen_5bp` × 같은 분 익절 금지)의 `{PRIMARY_OOS}`: "
        f"거래 {int(worst['num_trades'])}건"
        f"({int(worst['num_trades']) - int(adopted['num_trades']):+}) · "
        f"승률 {_pct(worst['win_rate'])}({_pp(worst['win_rate'] - adopted['win_rate'])}) · "
        f"**거래당 net R {_num(worst['mean_net_r'])}"
        f"({float(worst['mean_net_r']) - float(adopted['mean_net_r']):+.3f})** · "
        f"**MDD {_pct(worst['max_drawdown'])}"
        f"({_pp(worst['max_drawdown'] - adopted['max_drawdown'])})** · "
        f"CAGR {_pct(worst['cagr'])}(채택 {_pct(adopted['cagr'])}) · "
        f"청산 {int(worst['liquidation_events'])}건.**"
        + ruin_bit
        + " 🚨 **총수익 %를 헤드라인으로 읽지 말 것** — 거래당 net R과 CAGR이 이 표의 자다"
        f"(WAN-169/213). {_FLOOR_NOTE}."
    )


def _axis_note(frame: pd.DataFrame) -> str:
    """2×2를 쌓은 값 — **어느 축이 더 비싼가**. 팔 A만 보면 안 보이는 것이 이것이다.

    한 축씩 켠 팔(B·C)이 있어야 「합쳐서 얼마」가 아니라 「어느 쪽이 얼마」가 읽힌다. 자는
    거래당 net R(복리와 무관한 「실력」)이다 — 총수익 %로 재면 복리가 축의 크기를 왜곡한다.
    """
    base = _pick(frame, ADOPTED_ARM, PRIMARY_OOS, COMPOUND_ON)
    lens_only = _pick(frame, "B", PRIMARY_OOS, COMPOUND_ON)
    order_only = _pick(frame, "C", PRIMARY_OOS, COMPOUND_ON)
    both = _pick(frame, MOST_CONSERVATIVE_ARM, PRIMARY_OOS, COMPOUND_ON)
    if base is None or lens_only is None or order_only is None or both is None:
        return ""
    b0 = float(base["mean_net_r"])
    lens_cost = float(lens_only["mean_net_r"]) - b0
    order_cost = float(order_only["mean_net_r"]) - b0
    both_cost = float(both["mean_net_r"]) - b0
    louder = "체결(`pen_5bp`)" if abs(lens_cost) > abs(order_cost) else "그 1분의 순서"
    additive = lens_cost + order_cost
    # 두 축이 「직교」한다는 말은 서로 다른 질문이라는 뜻이지 효과가 더해진다는 뜻이 아니다 —
    # 실제로 더해지는지는 재 봐야 알고, 그 어긋남 자체가 읽을 거리다.
    gap = both_cost - additive
    return (
        f"📌 **어느 축이 더 비싼가(거래당 net R, `{PRIMARY_OOS}`)** — 체결 보수화만(팔 B) "
        f"{lens_cost:+.3f} · 같은 분 익절 금지만(팔 C) {order_cost:+.3f} · 둘 다(팔 D) "
        f"{both_cost:+.3f}. **{louder} 쪽이 더 크다.** 두 축을 따로 켠 합({additive:+.3f})과 "
        f"함께 켠 값의 차이는 {gap:+.3f}이다 — ⚠️ **「직교한다」는 것은 두 축이 다른 질문이라는 "
        "뜻이지 효과가 더해진다는 뜻이 아니다**(둘 다 켜면 거래 집합 자체가 달라지고, 북에서는 "
        "슬롯·자본 점유까지 갈린다)."
    )


_RISK_HEADER = (
    "| 팔 | 렌즈 | 같은 분 익절 | 거래 | 승률 | 거래당 net R | **MDD** | 계획 동시리스크 "
    "| 실효 동시리스크 | 최대 동시칸 | 청산 | 파괴선 |"
)
_RISK_SEP = "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :-: |"


def _risk_table(frame: pd.DataFrame, segment: str, compounding: str) -> list[str]:
    lines = [_RISK_HEADER, _RISK_SEP]
    for name in ARM_ORDER:
        row = _pick(frame, name, segment, compounding)
        if row is None:
            continue
        lines.append(
            f"| **{name}** | `{row['lens']}` | {'금지' if row['no_same_step_tp'] else '허용'} "
            f"| {int(row['num_trades'])} | {_pct(row['win_rate'])} "
            f"| {_num(row['mean_net_r'])} | **{_pct(row['max_drawdown'])}** "
            f"| {_pct(row['max_concurrent_risk'])} "
            f"| {_pct(row['max_effective_concurrent_risk'])} "
            f"| {int(row['peak_concurrency'])} | {int(row['liquidation_events'])} "
            f"| {'🚨' if bool(row['ruin']) else '—'} |"
        )
    return lines


def _compounding_table(frame: pd.DataFrame, segment: str) -> list[str]:
    lines = [
        "| 팔 | 총수익(복리 켬) | 총수익(**복리 끔**) | CAGR(복리 켬) | 구간(년) | net R 합 | "
        "거래당 net R | 중앙값 net R | 손익비 | MDD(켬 → 끔) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ARM_ORDER:
        on = _pick(frame, name, segment, COMPOUND_ON)
        off = _pick(frame, name, segment, COMPOUND_OFF)
        if on is None:
            continue
        lines.append(
            f"| **{name}** | {_pct(on['total_return'])} "
            f"| {'—' if off is None else _pct(off['total_return'])} "
            f"| {_pct(on['cagr'])} | {_num(on['span_years'], 2)} "
            f"| {_num(on['net_r'], 1)} | {_num(on['mean_net_r'])} "
            f"| {_num(on['median_net_r'])} | {_num(on['profit_factor'], 2)} "
            f"| {_pct(on['max_drawdown'])} → "
            f"{'—' if off is None else _pct(off['max_drawdown'])} |"
        )
    return lines


def _checksum_notes(frame: pd.DataFrame) -> list[str]:
    """검산 (b)·(c)를 **숫자로** 남긴다 — 라벨만 붙는 실패가 이 저장소의 상습 사고다."""
    notes: list[str] = []
    for name in ARM_ORDER:
        arm = ARMS_BY_NAME.get(name)
        cut = frame[(frame["arm"] == name) & (frame["compounding"] == COMPOUND_ON)]
        if arm is None or cut.empty or not arm.no_same_step_tp:
            continue
        leftover = int(cut["candidate_same_step_tps"].sum())
        notes.append(
            f"* {'🚨 **검산 (b) 실패**' if leftover else '📌 **검산 (b) 통과**'} — 팔 "
            f"`{name}`(같은 분 익절 금지)의 후보 층 카운터가 전 구간 **{leftover}**건이다"
            + ("." if leftover else " (정의상 0이어야 하고, 실제로 0이다).")
        )
    for name in ARM_ORDER:
        on = _pick(frame, name, PRIMARY_OOS, COMPOUND_ON)
        off = _pick(frame, name, PRIMARY_OOS, COMPOUND_OFF)
        if on is None or off is None:
            continue
        delta = float(off["total_return"]) - float(on["total_return"])
        moved = delta != 0.0
        notes.append(
            f"* {'📌' if moved else '🚨'} **검산 (c)** — 팔 `{name}`의 복리 끈 판이 총수익을 "
            f"{_pct(on['total_return'])} → {_pct(off['total_return'])}"
            f"(Δ {delta * 100:+.6f}%p)로 "
            + (
                "실제로 옮겼다(라벨이 아니다)."
                if moved
                else "**옮기지 못했다 — 노브가 안 걸렸다.**"
            )
        )
        break
    return notes


def _loo_note(loo: pd.DataFrame) -> str:
    """완료기준 5 — 종목 하나씩 빼고 **지갑을 다시 배치**한 폭."""
    if loo.empty:
        return "⚠️ leave-one-out 행이 없다."
    cut = loo[
        (loo["arm"] == MOST_CONSERVATIVE_ARM)
        & (loo["compounding"] == COMPOUND_ON)
        & (loo["excluded"] != "-")
    ]
    if cut.empty:
        return "⚠️ 팔 D의 leave-one-out 행이 없다."
    worst = cut.loc[cut["max_drawdown"].idxmax()]
    best = cut.loc[cut["max_drawdown"].idxmin()]
    return (
        f"📌 **종목을 하나씩 빼고 지갑을 다시 배치해도**(라벨 필터가 아니다 — WAN-316 스코프 "
        f"패턴) 팔 D의 `{PRIMARY_OOS}` MDD는 {_pct(best['max_drawdown'])}(−{best['excluded']})"
        f"~{_pct(worst['max_drawdown'])}(−{worst['excluded']}) 사이다 — "
        "한 종목이 만드는 결과가 아니다."
    )


def build_summary(frame: pd.DataFrame, loo: pd.DataFrame) -> str:
    lines: list[str] = [
        "# WAN-346: 가장 보수적인 가정 위의 채택 북 — 2×2 + 복리 착시 없이",
        "",
        "보수 축 **둘**을 쌓았다. 가로축 `pen_5bp`는 *「주문이 채워지느냐」*(큐 우선순위, "
        "WAN-96/124)를 묻고, 세로축 `no_same_step_tp`는 *「채워진 뒤 그 1분 안의 순서」*"
        "(WAN-336)를 묻는다 — **직교한다.** 그래서 이 저장소의 모든 체결 보수화 관문이 "
        "「같은 분 익절」 낙관을 통과시켜 왔고, **팔 D는 여기서 처음 재는 팔**이다.",
        "",
        "좌표: 12종목 × 4TF 한 지갑 · 못 박은 6년 창 · 재진입 ON(band) · cap_only 5배 · "
        "**핀 하나도 없음**(WAN-305). 주 구간은 `oos_warm`(WAN-166).",
        "",
        "## 판정",
        "",
        _verdict(frame),
        "",
        _axis_note(frame),
        "",
        _loo_note(loo),
        "",
        f"## §1·§3 — 2×2 리스크 표 (`{PRIMARY_OOS}` · 복리 켬 = 채택 회계)",
        "",
        *_risk_table(frame, PRIMARY_OOS, COMPOUND_ON),
        "",
        "🚨 **「청산 0건」을 안전 근거로 쓰지 말 것** — 사이징이 **현재 자본의 %**라 손실이 "
        "쌓이면 포지션도 함께 작아져 **연쇄 손실로는 청산 조건이 구조적으로 안 걸린다**"
        "(WAN-312 §4). 판정 열은 **MDD**이고 파괴선(50%)을 함께 찍는다.",
        "",
        "📌 **계획 동시 리스크와 실효 동시 리스크가 같은 것은 열이 빈 게 아니다** — 채택 "
        "회계(`stress_risk_multiple=1.0`)에서는 **정의상 같고**, 그 열이 벌어지는 것은 손절이 "
        "계획 1R보다 밀리는 스트레스를 얹었을 때다(WAN-312/316).",
        "",
        "### 다른 구간",
        "",
    ]
    for segment in SEGMENT_ORDER:
        if segment == PRIMARY_OOS:
            continue
        lines += [f"**`{segment}`**", "", *_risk_table(frame, segment, COMPOUND_ON), ""]
    lines += [
        f"## §2 — 복리 착시 없이 (`{PRIMARY_OOS}`)",
        "",
        "📌 **먼저 못 박는다 — 지금 계산은 「평균 × 횟수 제곱」이 아니다.** 자본곡선은 초기"
        "자본에서 시작해 **청산 시각 순으로 실현손익을 하나씩 더한 진짜 장부**이고 MDD도 그 "
        "곡선에서 나온다. 문제는 **사이징이 「현재 자본의 %」**라 돈이 불면 베팅도 커진다는 "
        "것이다 — 6년 × 수천 거래면 작은 우위가 기하급수로 부풀어 그 수가 현실에서 달성 "
        "가능하지 않다(호가 깊이 · 6년 내내 같은 엣지 · 낙관 체결).",
        "",
        "그래서 **베팅 크기를 초기 자본에 못 박은 판**(복리 끔)을 나란히 싣는다. 총수익이 "
        "우위에 **선형**이라 읽힌다. ⚠️ **성과를 좋게 만드는 장치가 아니라 다른 자다.**",
        "",
        *_compounding_table(frame, PRIMARY_OOS),
        "",
        "🚨 **총수익 %는 단독 헤드라인이 아니다** — 거래당 net R(복리와 무관한 「실력」)과 "
        "CAGR과 **반드시 나란히** 읽는다(WAN-169/213).",
        "",
        "🚨 **위 표의 「MDD(켬 → 끔)」에서 끈 쪽이 작다고 「더 안전」으로 읽지 말 것** — 베팅은 "
        "초기 자본에 고정인데 지갑은 계속 불어나므로 **낙폭의 분자는 그대로인데 분모만 커진다**. "
        "복리 착시의 **거울상**이라 두 MDD는 서로 비교할 수 없다. 복리 끈 판에서 읽을 것은 "
        "**총수익·CAGR·거래당 net R**이고, 위험은 **복리 켠 판(= 채택 회계)의 MDD**로 읽는다.",
        "",
        "## 검산",
        "",
        "* 📌 **(a) 팔 A ≡ 인자 없는 채택 북** — 같은 payload를 `book_cli`의 채택 경로에 "
        "그대로 넣어 대조한다(`wan336.verify_adopted_identity` 재사용). 회귀 테스트가 "
        "`run_cells` 호출 인자까지 채택 경로와 대조해 **동작으로** 고정한다.",
        *_checksum_notes(frame),
        "",
        "## 읽지 말아야 할 것",
        "",
        "* ❌ **기본값 전환 제안이 아니다** — `no_same_step_tp`나 `pen_5bp`를 기본으로 켜는 "
        "것은 이 저장소의 **모든 지정가 백테스트 수치**를 움직이는 재-베이스라인이고 **사용자 "
        "결정**이다(WAN-132/149/159급 파급). 개발자 임의 착수 금지.",
        "* ⚠️ **팔 D도 진값이 아니다** — 같은 분 익절 금지는 **반대쪽 극단**이고(순서가 반대"
        "였다면 그 거래는 손실이 아니라 **더 오래 보유**이며 그 뒤는 미지다), `pen_5bp`도 "
        "실측이 아니라 **민감도**다(큐 우선순위는 틱·호가 WAN-98 소관 · Canceled). **진값은 "
        "A와 D 사이**이고 그 폭이 이 표의 산출물이다.",
        "* 🚨 **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 축은 *진입 규칙이 "
        "무작위와 구분되는가*가 아니라 *이미 잰 숫자가 얼마나 낙관인가*를 묻는다. **다른 "
        "질문이다.**",
        f"* ⚠️ {_FLOOR_NOTE}. 손절폭 가드(0.3%)·존폭 필터(1.28)·배수(cap_only 5배)는 "
        "**손대지 않았다**(WAN-76/79 · WAN-159 · WAN-213 소관).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-346 보수 축 2×2 채택 북")
    parser.add_argument("--symbols", default=None, help="쉼표 구분(기본: 채택 12종목)")
    parser.add_argument("--tf", default=None, help="쉼표 구분(기본: 채택 4TF)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--arms", default=None, help=f"쉼표 구분(기본: {','.join(ARM_ORDER)})")
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 쓴다")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 재생성")
    return parser.parse_args(argv)


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    if existing.empty:
        return fresh
    merged = pd.concat([existing, fresh], ignore_index=True)
    return merged.drop_duplicates(subset=list(keys), keep="last").reset_index(drop=True)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.from_csv:
        frame, loo = _read(CSV_PATH), _read(LOO_CSV_PATH)
        if frame.empty:
            print(f"[wan346] {CSV_PATH}가 없습니다 — 먼저 격자를 돌리세요.")
            return 1
        SUMMARY_PATH.write_text(build_summary(frame, loo), encoding="utf-8")
        print(f"[wan346] 요약 재생성: {SUMMARY_PATH}")
        return 0

    symbols = (
        [s.strip() for s in args.symbols.split(",")] if args.symbols else harness.DEFAULT_SYMBOLS
    )
    timeframes = [t.strip() for t in args.tf.split(",")] if args.tf else harness.DEFAULT_TIMEFRAMES
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else list(ARM_ORDER)
    unknown = [a for a in arms if a not in ARMS_BY_NAME]
    if unknown:
        print(f"[wan346] 모르는 팔: {unknown} (가능: {', '.join(ARM_ORDER)})")
        return 2

    base_rows = _read(CSV_PATH) if args.append else pd.DataFrame()
    base_loo = _read(LOO_CSV_PATH) if args.append else pd.DataFrame()

    def persist(rows: list[ConservativeRow], loo: list[ConservativeLooRow]) -> None:
        nonlocal base_rows, base_loo
        base_rows = _merge(base_rows, rows_to_frame(rows), CSV_KEYS)
        base_loo = _merge(base_loo, loo_to_frame(loo), LOO_CSV_KEYS)
        base_rows.to_csv(CSV_PATH, index=False)
        base_loo.to_csv(LOO_CSV_PATH, index=False)
        print(f"[wan346] 적재: {CSV_PATH} ({len(base_rows)}행)", flush=True)

    run_report(
        symbols,
        timeframes,
        arms=arms,
        start=args.start,
        end=args.end,
        jobs=args.jobs,
        on_arm=persist,
    )
    SUMMARY_PATH.write_text(build_summary(base_rows, base_loo), encoding="utf-8")
    print(f"[wan346] 요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
