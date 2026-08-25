"""WAN-323: 반익절 래더 — 「기대값 얼마를 내고 MDD 얼마를 사는가」의 교환비.

## 질문 (사용자 목표 2026-08-18: *"MDD는 내리고, 승률을 같이 올릴 방법을 찾고싶어."*)

익절 목표에 **한 번에 전량**을 내는 대신, 도중의 한 지점에서 **절반을 먼저 팔고**
잔량을 원래 목표까지 끌고 간다. 첫 청산 뒤 손절을 **본전**으로 옮기면 지던 거래가
본전 거래로 바뀌어 손실 분포가 잘린다(MDD↓ · 승률↑ 가설).

## 🔒 WAN-90이 이미 답한 것 — 다시 재지 않는다

`docs/decisions/wan90.md`: **E[러너] ≈ 0R**(공식 렌즈 OOS 중앙값 6셀 전부 0.000R ·
본절 복귀율 97~99%)이므로 부호식 `제안 − 현재 = (1−X)(E[러너] − 1.5R)`의 **부호는
음수**다 — 래더는 **기대값을 깎는다**. 그건 판정났다.

🚨 **그래서 이 표의 질문은 「더 버는가」가 아니라 「기대값을 얼마 깎고 MDD를 얼마
줄이는가」다.** 래더가 수익에서 기준선을 이기면 **발견이 아니라 배선 버그를 먼저
의심할 것**(그 경우 검산 `--checksum`부터 본다).

## 격자 (사용자 사양 확정 2026-08-18) — 14팔

분할 방식 = **한 지점씩 비교**(스윕) · 본절 스탑 = **축**(off/on) · 분할 비율 =
**절반 고정**(비율 스윕은 범위 밖).

| 계열 | 기준선 | 분할 지점 |
| -- | -- | -- |
| **A**(전량 익절 1.5R = 현행) | `A0` | 1.0R · 1.2R · 1.3R |
| **B**(전량 익절 2.0R) | `B0` | 1.0R · 1.3R · 1.5R |

⚠️ **B 계열은 「B0 대비」로만 읽는다** — WAN-90 R 스윕이 OOS 최적을 1.5R로 냈으므로
B0 자체가 A0보다 나쁠 것으로 예상된다. A·B의 절대값을 섞지 말 것.

## 좌표 (WAN-305 원칙 — 핀 하나도 없다)

채택 좌표 그대로: 12종목(`harness.DEFAULT_SYMBOLS`) · 못 박은 6년 창 · 분리 존 ·
`intrabar_live` · 존폭 필터 1.28 · 오프셋 2bp · `unconditional` · 롱 온리 · 손절폭
가드 0.3%. 렌즈는 `baseline` 단독(WAN-128), 구간은 `oos_warm`(주) + `oos`(스트레스,
WAN-166), 포지션 회계는 **per-cell 단일**(익절 측정 관행 WAN-90/143/206).

⚠️ **이 표의 MDD는 per-cell MDD다** — 채택 북(WAN-213) MDD와 직접 비교 금지.
⚠️ **체결 보수화(`pen_5bp`)는 안 쟀다** — 부분 익절은 청산을 한 번 더 늘리므로 낙관
체결 가정에 **더** 기댄다. 판정문이 이 한계를 명시한다.

## 승률의 정의 (WAN-323 §3-1)

**순손익 > 0이면 승리**(수수료·슬리피지·펀딩 반영 후). 부분 익절 뒤 본전으로 나간
거래는 그로스가 0 언저리여도 비용 때문에 대개 **패배**로 센다. 엔진의
`Trade.is_win`(= `realized_pnl > 0`)이 곧 이 정의이고,
`tests/test_zone_limit_backtest.py::test_win_rate_definition_is_net_pnl_positive`가
**동작으로** 고정한다.

재현:

```
uv run python -m backtest.wan323_partial_tp_ladder --tf 4h --jobs 4
uv run python -m backtest.wan323_partial_tp_ladder --tf 2h --jobs 4 --append
uv run python -m backtest.wan323_partial_tp_ladder --tf 1h --jobs 4 --append
uv run python -m backtest.wan323_partial_tp_ladder --tf 15m --jobs 4 --append  # 무겁다
uv run python -m backtest.wan323_partial_tp_ladder --from-csv                  # 요약만
uv run python -m backtest.wan323_partial_tp_ladder --checksum --tf 4h          # A0 ≡ run_once
```
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE, BookRunRow, build_book_rows
from backtest.harness import (
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    MarketData,
    Segment,
    segments_for,
)
from backtest.leverage_book import LeverageBookParams
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import run_cells
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    ZoneLimitStats,
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan323_partial_tp_ladder.csv"
SUMMARY_PATH = REPORTS_DIR / "wan323_partial_tp_ladder_summary.md"
BOOK_CSV_PATH = REPORTS_DIR / "wan323_partial_tp_ladder_book.csv"

#: 북 판에서 돌릴 기본 팔 — **결정적인 것만**(사용자 결정 2026-08-18 「재진입 무조건」).
#: 14팔 전부를 북으로 돌리면 후보 생성을 14번 다시 해야 해 per-cell 격자와 맞먹는 비용이
#: 든다. per-cell 표가 이미 팔 순위를 냈으므로 북은 **각 계열의 기준선과 그 계열에서 가장
#: 싸게 낙폭을 산 팔**만 확인한다(`--book-arms all`로 전부 돌릴 수 있다).
DEFAULT_BOOK_ARMS: tuple[str, ...] = ("A0", "A1_be_off", "A1_be_on", "B0", "B1_be_on")

#: 판정의 주 구간(WAN-166) = 따뜻한 연속 OOS. 차가운 `oos`는 스트레스로 병기한다.
PRIMARY_OOS = SEGMENT_OOS_WARM
STRESS_OOS = SEGMENT_OOS
SEGMENT_ORDER: tuple[str, ...] = ("full", SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 유효 표본 게이트(WAN-84) — 심볼당 이 미만이면 그 셀은 판정에 쓰지 않는다.
MIN_TRADES = 20


# --------------------------------------------------------------------------- #
# 팔 정의 — 14팔
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LadderArm:
    """한 팔. `partial_r=None`이면 기준선(전량 익절)이고 `breakeven`은 의미가 없다."""

    name: str
    family: str
    """`A`(전량 1.5R) 또는 `B`(전량 2.0R). 판정은 **같은 계열 기준선 대비**로만 낸다."""
    take_profit_r: float
    partial_r: float | None
    breakeven: bool

    @property
    def is_baseline(self) -> bool:
        return self.partial_r is None


#: 분할 비율은 절반 고정(사용자 사양 — 비율 스윕은 범위 밖).
PARTIAL_FRACTION = 0.5

#: 계열별 (전량 익절 R, 분할 지점들). A0가 곧 현행 채택 엔진이다(`take_profit_r=1.5`).
_FAMILIES: tuple[tuple[str, float, tuple[float, ...]], ...] = (
    ("A", 1.5, (1.0, 1.2, 1.3)),
    ("B", 2.0, (1.0, 1.3, 1.5)),
)


def _build_arms() -> tuple[LadderArm, ...]:
    arms: list[LadderArm] = []
    for family, tp_r, splits in _FAMILIES:
        arms.append(
            LadderArm(
                name=f"{family}0",
                family=family,
                take_profit_r=tp_r,
                partial_r=None,
                breakeven=False,
            )
        )
        for index, split in enumerate(splits, start=1):
            for breakeven in (False, True):
                suffix = "be_on" if breakeven else "be_off"
                arms.append(
                    LadderArm(
                        name=f"{family}{index}_{suffix}",
                        family=family,
                        take_profit_r=tp_r,
                        partial_r=split,
                        breakeven=breakeven,
                    )
                )
    return tuple(arms)


ARMS: tuple[LadderArm, ...] = _build_arms()
ARMS_BY_NAME: dict[str, LadderArm] = {arm.name: arm for arm in ARMS}
BASELINE_OF: dict[str, str] = {arm.name: f"{arm.family}0" for arm in ARMS}


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class LadderRow(BaseModel):
    """한 (심볼, TF, 구간, 팔) 셀."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    family: str
    take_profit_r: float
    partial_r: float | None
    breakeven: bool
    eligible: int
    filled: int
    """체결 셋업 수(시퀀싱 이전). 래더는 청산만 바꾸므로 같은 셀의 14팔이 모두 같아야 한다."""
    num_trades: int
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float | None
    mean_net_r: float | None
    """거래당 순 R = 실현손익 ÷ 그 거래의 리스크 금액(WAN-154 §1′ 정의).

    ⚠️ `harness.mean_r`(청산 사유 → ±1.5/−1.0)은 승률의 대수적 재탕이라 쓰지 않는다."""
    mean_gross_r: float | None
    """비용 반영 **전** 거래당 R. `mean_net_r`과의 차이가 곧 비용의 R 비중이다."""
    cost_total: float
    """수수료(진입+청산 전부) + 펀딩 + 슬리피지의 합(통화). 래더는 청산이 2회라 는다."""
    n_take_profit: int
    n_stop_loss: int
    n_end_of_data: int
    n_partial: int
    """부분 청산이 한 번이라도 일어난 거래 수. 기준선은 항상 0."""
    n_partial_then_stop: int
    """부분 청산 뒤 손절로 끝난 거래 수 — 본절 켜짐이면 곧 **본절 전환** 건수다."""
    funding_rows: int
    """이 셀의 펀딩 정산 건수. 0이면 펀딩비가 반영되지 않았다는 뜻이라 표에 드러낸다."""


# --------------------------------------------------------------------------- #
# 한 팔 실행
# --------------------------------------------------------------------------- #


#: 래더의 폐쇄형 항등식 (WAN-323 검산). 분할 지점 `k`R에서 비율 `f`를 팔고 잔량을 `T`R
#: 전량 익절까지 끌고 갈 때, **본절을 끄면 청산 시각이 안 바뀌므로** 같은 거래의 그로스 R이
#: 딱 두 가지로만 움직인다:
#:
#:   * `T`R 익절로 끝난 거래 — 절반을 더 낮은 값에 팔았으니 `−f·(T − k)`
#:   * `k`R을 찍고 되돌아 손절난 거래 — 절반이 `−1R` 대신 `+k R`이니 `+f·(k + 1)`
#:   * 분할 지점을 못 찍고 손절난 거래 — `0`(래더가 아무 일도 안 했다)
#:
#: 즉 승자에게서 조금 잃고 **구제된 패자에게서 크게 번다**. 부호는 두 부류의 개수 비가
#: 정하는 것이지 미리 정해져 있지 않다 — WAN-90의 부호식은 **익절선 그 자리에서** 반익절하는
#: 다른 제안의 것이라 여기 적용되지 않는다(초안의 「이기면 버그」 판정을 실측으로 교정).
#: `run_cell`이 매 셀에서 이 항등식을 실제 거래로 검산한다.
_IDENTITY_TOL = 1e-6


def expected_ladder_delta(arm: LadderArm, *, rescued: bool) -> float:
    """본절 끈 래더 팔이 기준선 대비 내야 할 그로스 R 차이(폐쇄형)."""
    assert arm.partial_r is not None
    if rescued:
        return PARTIAL_FRACTION * (arm.partial_r + 1.0)
    return -PARTIAL_FRACTION * (arm.take_profit_r - arm.partial_r)


@dataclass(frozen=True)
class TradeLedger:
    """검산용 거래 한 줄 — 진입 시각으로 팔 사이를 조인한다."""

    entry_time: int
    reason: ExitReason
    has_partial: bool
    gross_r: float


@dataclass(frozen=True)
class ArmOutcome:
    row: LadderRow
    entry_times: list[int]
    """체결 셋업의 진입 시각(정렬). 래더는 청산만 바꾸므로 14팔이 비트 단위로 같아야 한다."""
    ledger: list[TradeLedger]
    """거래 단위 그로스 R — `run_cell`의 항등식 검산 입력."""


def _risk_amount(cand: _Candidate, entry_price: float, quantity: float) -> float:
    """그 거래 자신의 1R 금액 = 수량 × |진입 체결가 − 손절 참조가|."""
    return quantity * abs(entry_price - cand.stop_price)


def run_arm(
    seg_market: MarketData,
    segment_name: str,
    arm: LadderArm,
    *,
    obr: OrderBlockResult,
    eval_from_ms: int | None,
) -> ArmOutcome:
    """한 팔을 `seg_market`에서 돌려 행 하나를 만든다.

    `eval_from_ms`(따뜻한 연속 OOS, WAN-166)를 주면 `run_zone_limit_backtest_verbose`와
    **글자 그대로 같은 절차**로 후보를 평가 창으로 좁힌다 — 그래야 기준선 팔(`A0`)이
    `harness.run_once`와 비트 단위로 일치한다(`run_checksum`이 고정).
    """
    params = harness.build_params(entry_mode="zone_limit", take_profit_r=arm.take_profit_r)
    cfg = harness.legacy_build_config(seg_market.timeframe)
    sink: list[SetupDiagnostic] = []
    candidates, stats = build_zone_limit_candidates(
        seg_market.htf_df,
        seg_market.df_1m,
        seg_market.timeframe,
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_result=obr,
        setup_sink=sink,
        partial_take_profit_r=arm.partial_r,
        partial_take_profit_fraction=PARTIAL_FRACTION,
        breakeven_after_partial=arm.breakeven,
    )
    if eval_from_ms is not None:
        candidates = [c for c in candidates if c.trigger_time >= eval_from_ms]
        kept = [d for d in sink if d.trigger_time >= eval_from_ms]
        stats = ZoneLimitStats(
            eligible=len(kept),
            filled=sum(1 for d in kept if d.filled),
            penetrations=sum(1 for c in candidates if c.penetration),
            dropped=sum(1 for d in kept if d.dropped),
        )
    paired = sequence_with_candidates(candidates, cfg, seg_market.funding_rates)
    trades = [trade for _, trade in paired]
    metrics = build_result_from_trades(trades, cfg, seg_market.timeframe).metrics

    ledger: list[TradeLedger] = []
    net_rs: list[float] = []
    gross_rs: list[float] = []
    cost_total = 0.0
    n_partial = 0
    n_partial_then_stop = 0
    reasons = {ExitReason.TAKE_PROFIT: 0, ExitReason.STOP_LOSS: 0, ExitReason.END_OF_DATA: 0}
    for cand, trade in paired:
        reasons[cand.reason] = reasons.get(cand.reason, 0) + 1
        if cand.partial_exits:
            n_partial += 1
            if cand.reason is ExitReason.STOP_LOSS:
                n_partial_then_stop += 1
        exit_fees = sum(f.fee for f in trade.exits)
        # 슬리피지 = 청산 체결가와 **원가**(시뮬레이터가 낸 목표가)의 차이. 청산 체결은
        # 부분·최종 모두 테이커라 각 줄에 붙는다. 체결 순서는 부분들 → 최종이고
        # `_to_trade`가 그 순서로 `exits`를 만든다.
        raw_prices = [p.price for p in cand.partial_exits] + [cand.exit_price]
        slip = sum(
            abs(fill.price - raw) * fill.quantity
            for fill, raw in zip(trade.exits, raw_prices, strict=True)
        )
        cost = trade.entry_fee + exit_fees + trade.funding_cost + slip
        cost_total += cost
        risk = _risk_amount(cand, trade.entry_price, trade.quantity)
        if risk > 0:
            net_rs.append(trade.realized_pnl / risk)
            gross_rs.append((trade.realized_pnl + cost) / risk)
            # 그로스 R = 비용 반영 전 실현 R. 항등식은 비용을 타지 않으므로 이 자로 잰다
            # (체결가에는 슬리피지가 들어 있어 `slip`을 되돌린 원가 기준으로 낸다).
            ledger.append(
                TradeLedger(
                    entry_time=trade.entry_time,
                    reason=cand.reason,
                    has_partial=bool(cand.partial_exits),
                    gross_r=(trade.realized_pnl + cost) / risk,
                )
            )

    row = LadderRow(
        symbol=seg_market.symbol,
        timeframe=seg_market.timeframe,
        segment=segment_name,
        arm=arm.name,
        family=arm.family,
        take_profit_r=arm.take_profit_r,
        partial_r=arm.partial_r,
        breakeven=arm.breakeven,
        eligible=stats.eligible,
        filled=stats.filled,
        num_trades=metrics.num_trades,
        total_return=metrics.total_return,
        max_drawdown=metrics.max_drawdown,
        win_rate=metrics.win_rate,
        sharpe=metrics.sharpe,
        mean_net_r=statistics.fmean(net_rs) if net_rs else None,
        mean_gross_r=statistics.fmean(gross_rs) if gross_rs else None,
        cost_total=cost_total,
        n_take_profit=reasons.get(ExitReason.TAKE_PROFIT, 0),
        n_stop_loss=reasons.get(ExitReason.STOP_LOSS, 0),
        n_end_of_data=reasons.get(ExitReason.END_OF_DATA, 0),
        n_partial=n_partial,
        n_partial_then_stop=n_partial_then_stop,
        funding_rows=len(seg_market.funding_rates),
    )
    return ArmOutcome(row=row, entry_times=sorted(c.entry_time for c in candidates), ledger=ledger)


def run_cell(
    market: MarketData, segment: Segment, *, arms: Sequence[LadderArm] = ARMS
) -> list[LadderRow]:
    """한 (심볼, TF, 구간)의 14팔을 돈다.

    🚨 **검산이 코드에 있다** — 래더는 청산만 바꾸므로 모든 팔의 체결 셋업 진입 시각
    집합이 **비트 단위로 같아야 한다**. 어긋나면 익절이 진입을 바꾼 배선 버그이므로
    조용히 넘기지 않고 `AssertionError`로 멈춘다(WAN-206 관행).
    """
    seg_market = harness.slice_market(market, segment)
    if seg_market.empty or seg_market.df_1m.empty:
        return []
    eval_from_ms = harness.eval_boundary_ms(market, segment)
    # ⚠️ 핀 없음(WAN-305) — `OrderBlockParams()`가 곧 오늘의 채택 탐지 파라미터다.
    obr: OrderBlockResult = OrderBlockDetector(OrderBlockParams()).run(seg_market.htf_df)

    rows: list[LadderRow] = []
    entry_sets: dict[str, list[int]] = {}
    ledgers: dict[str, list[TradeLedger]] = {}
    for arm in arms:
        outcome = run_arm(seg_market, segment.name, arm, obr=obr, eval_from_ms=eval_from_ms)
        rows.append(outcome.row)
        entry_sets[arm.name] = outcome.entry_times
        ledgers[arm.name] = outcome.ledger
    # 같은 전량 익절 R 안에서만 대조한다 — 익절 R이 다르면 청산 시각이 달라 시퀀싱이
    # 다른 셋업을 집는 것이 정상이다(A0 vs B0는 서로 다른 엔진이다).
    for family, _tp_r, _splits in _FAMILIES:
        names = [arm.name for arm in arms if arm.family == family]
        if len(names) < 2:
            continue
        head = entry_sets[names[0]]
        for name in names[1:]:
            if entry_sets[name] != head:
                raise AssertionError(
                    f"후보 집합 불일치 — {seg_market.symbol} {seg_market.timeframe} "
                    f"{segment.name} {name}: {len(entry_sets[name])} vs "
                    f"{names[0]} {len(head)}. 래더가 진입을 바꾸는 배선 버그다."
                )
    _check_ladder_identity(ledgers, seg_market.symbol, seg_market.timeframe, segment.name, arms)
    return rows


def _check_ladder_identity(
    ledgers: dict[str, list[TradeLedger]],
    symbol: str,
    timeframe: str,
    segment: str,
    arms: Sequence[LadderArm],
) -> None:
    """폐쇄형 항등식을 **실제 거래로** 검산한다 (WAN-323).

    본절을 끈 래더 팔은 청산 시각이 기준선과 같으므로 같은 거래를 조인할 수 있고, 그 거래의
    그로스 R 차이가 `expected_ladder_delta`와 정확히 일치해야 한다. 어긋나면 부분 청산의
    수량·가격·수수료 배분 어딘가가 틀린 것이라 조용히 넘기지 않는다 — 이 표의 핵심 주장
    (「승자에게서 조금 잃고 구제된 패자에게서 크게 번다」)이 곧 이 항등식이다.

    본절을 켠 팔(`be_on`)은 손절선이 움직여 청산 자체가 달라지므로 대상이 아니다.
    """
    for arm in arms:
        if arm.is_baseline or arm.breakeven or arm.partial_r is None:
            continue
        base = ledgers.get(BASELINE_OF[arm.name])
        mine = ledgers.get(arm.name)
        if not base or not mine:
            continue
        by_time = {row.entry_time: row for row in base}
        for row in mine:
            twin = by_time.get(row.entry_time)
            if twin is None or row.reason is ExitReason.END_OF_DATA:
                continue  # 데이터끝 청산은 마지막 종가라 항등식 대상이 아니다.
            if not row.has_partial:
                expected = 0.0
            elif row.reason is ExitReason.TAKE_PROFIT:
                expected = expected_ladder_delta(arm, rescued=False)
            else:
                expected = expected_ladder_delta(arm, rescued=True)
            actual = row.gross_r - twin.gross_r
            if abs(actual - expected) > _IDENTITY_TOL:
                raise AssertionError(
                    f"래더 항등식 위반 — {symbol} {timeframe} {segment} {arm.name} "
                    f"진입 {row.entry_time} ({row.reason}, 분할 "
                    f"{'O' if row.has_partial else 'X'}): 실측 Δ{actual:+.6f}R vs "
                    f"폐쇄형 {expected:+.6f}R. 부분 청산 회계가 틀렸다."
                )


# --------------------------------------------------------------------------- #
# 드라이버 — (심볼, TF) 단위 병렬
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CellWork:
    symbol: str
    timeframe: str
    start: str
    end: str
    db_path: str


def _cell_worker(work: CellWork) -> tuple[list[LadderRow], str]:
    start_ms, end_ms = parse_date_ms(work.start), parse_date_ms(work.end)
    market = harness.load_market_data(
        work.symbol,
        work.timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        need_1m=True,
        funding=True,
        db_path=work.db_path,
    )
    if market.empty or market.df_1m.empty:
        return [], f"{work.symbol} {work.timeframe}: 데이터 없음 — 건너뜀"
    t0 = time.time()
    rows: list[LadderRow] = []
    for segment in segments_for(warm_oos=True):
        rows.extend(run_cell(market, segment))
    gap = "" if market.funding_rates else " ⚠️ 펀딩 0행"
    return rows, f"{work.symbol} {work.timeframe}: {len(rows)}행 ({time.time() - t0:.0f}s){gap}"


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    db_path: str = harness.DB_PATH,
    jobs: int = 1,
    log: bool = True,
) -> list[LadderRow]:
    """격자 실행. `jobs`는 **순수 성능 노브**다(직렬과 행이 같다 — WAN-121 관행)."""
    works = [
        CellWork(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start=start,
            end=end,
            db_path=db_path,
        )
        for timeframe in timeframes
        for symbol in symbols
    ]
    rows: list[LadderRow] = []
    done = 0

    def _absorb(cell: list[LadderRow], note: str) -> None:
        nonlocal done
        done += 1
        rows.extend(cell)
        if log:
            print(f"[wan323] ({done}/{len(works)}) {note}", flush=True)

    if jobs and jobs != 1:
        with ProcessPoolExecutor(max_workers=jobs if jobs > 0 else None) as pool:
            futures = [pool.submit(_cell_worker, work) for work in works]
            for fut in as_completed(futures):
                cell, note = fut.result()
                _absorb(cell, note)
    else:
        for work in works:
            cell, note = _cell_worker(work)
            _absorb(cell, note)
    return sort_rows(rows)


def sort_rows(rows: Sequence[LadderRow]) -> list[LadderRow]:
    """CSV 순서를 실행 순서(병렬이면 완료 순)와 무관하게 못 박는다."""
    seg_rank = {name: i for i, name in enumerate(SEGMENT_ORDER)}
    arm_rank = {arm.name: i for i, arm in enumerate(ARMS)}
    return sorted(
        rows,
        key=lambda r: (
            r.timeframe,
            r.symbol,
            seg_rank.get(r.segment, len(seg_rank)),
            arm_rank.get(r.arm, len(arm_rank)),
        ),
    )


# --------------------------------------------------------------------------- #
# 북 판 — 채택 회계(레버리지 북 cap_only 5배 + 재진입 band)에서 다시 잰다
# --------------------------------------------------------------------------- #
#
# 🗣️ **사용자 결정 2026-08-18: "앞으로 재진입은 무조건 한다는 전제하에 하자."**
#
# per-cell 표(위)는 칸마다 독립 자본이고 **재진입이 없다** — 재진입은 북 회계에만 있는
# 기능이라 코드가 `--positions single`과의 조합을 거부한다(`backtest/run.py`). 즉 그 표는
# 팔 간 비교로는 유효해도 **실매매 조건이 아니다**. 이 절이 같은 14팔(기본은 결정적인
# 5팔)을 **채택 북 + 재진입 band** 위에서 다시 재 그 간극을 닫는다.
#
# 📌 **북에 부분 청산을 얹으려면 배선이 필요했다** — 북은 진입 시점의 명목·리스크 스냅샷을
# 최종 청산까지 들고 있어 「도중에 줄었다」는 이벤트가 없었다. 그대로 얹으면 이미 덜어낸
# 명목·위험을 계속 세서 **하필 래더에 불리한 방향으로** 편향된다. `leverage_book._Reduction`
# 이 그 이벤트이고, 총액은 최종 청산이 `realized_pnl − 이미 반영한 누계`를 내 정의상 정확하다.


class BookLadderRow(BaseModel):
    """한 (팔, 구간)의 채택 북 집계 행. 북은 칸을 가로지른 **한 지갑**이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    arm: str
    family: str
    take_profit_r: float
    partial_r: float | None
    breakeven: bool
    segment: str
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    liquidation_events: int
    skipped_notional: int


def run_book_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    arms: Sequence[LadderArm] | None = None,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    on_arm: Callable[[list[BookLadderRow]], None] | None = None,
    log: bool = True,
) -> list[BookLadderRow]:
    """팔마다 채택 북(cap_only 5배 · 재진입 band)을 돌려 집계 행을 낸다.

    좌표·회계는 **인자 없는 `backtest.run --oos-warm`과 같은 것**을 쓴다 — 유동성 한도는
    채택값(`UNSET`), 재진입은 켬(band), 북 파라미터는 `LeverageBookParams()`(= 채택 북).
    래더 인자만 팔마다 갈아끼운다.

    ⚠️ 래더 팔은 `engine_check`를 끈다 — 그 검산은 격리 성과가 `harness.run_once`(래더 없는
    per-cell)와 비트 일치하는지 보는 것이라, 래더를 켠 팔에서는 **당연히** 어긋난다. 기준선
    팔(`A0`/`B0`)에서는 켜 둬 배선이 안 틀어졌음을 계속 확인한다.
    """
    selected = list(arms) if arms is not None else [ARMS_BY_NAME[n] for n in DEFAULT_BOOK_ARMS]
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    rows: list[BookLadderRow] = []
    for arm in selected:
        t0 = time.time()
        payloads = run_cells(
            symbols,
            timeframes,
            start=start,
            end=end,
            jobs=jobs,
            # 채택 좌표: 유동성 한도 켬(채택 0.005) · 재진입 켬(band) — 핀 없음(WAN-305).
            adv_fraction=harness.UNSET,
            reentry=True,
            reentry_entry_rule=ADOPTED_REENTRY_ENTRY_RULE,
            engine_check=arm.is_baseline,
            take_profit_r=arm.take_profit_r,
            partial_take_profit_r=arm.partial_r,
            partial_take_profit_fraction=PARTIAL_FRACTION,
            breakeven_after_partial=arm.breakeven,
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        )
        book_rows = build_book_rows(
            payloads,
            book=LeverageBookParams(),
            segments=SEGMENT_ORDER,
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
        )
        arm_rows = [_book_ladder_row(arm, row) for row in book_rows]
        rows.extend(arm_rows)
        if on_arm is not None:
            # 팔 하나가 12종목 × 3TF를 다 돌아 ~50분이라, 중간에 죽으면 전부 잃는다.
            # 팔마다 즉시 적재해 재실행이 남은 팔만 돌 수 있게 한다.
            on_arm(arm_rows)
        if log:
            print(
                f"[wan323·book] {arm.name}: {len(book_rows)}구간 ({time.time() - t0:.0f}s)",
                flush=True,
            )
    return rows


def _book_ladder_row(arm: LadderArm, row: BookRunRow) -> BookLadderRow:
    return BookLadderRow(
        arm=arm.name,
        family=arm.family,
        take_profit_r=arm.take_profit_r,
        partial_r=arm.partial_r,
        breakeven=arm.breakeven,
        segment=row.segment,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd,
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=row.max_concurrent_risk,
        liquidation_events=row.liquidation_events,
        skipped_notional=row.skipped_notional,
    )


def build_book_summary(frame: pd.DataFrame) -> str:
    """북 판 표 — 판정 열은 위험조정 축이다(총수익 %는 복리 착시, WAN-169/213)."""
    lines: list[str] = [
        "## 채택 북 판 (cap_only 5배 · 재진입 band) — 실매매 회계",
        "",
        "🗣️ 사용자 결정 2026-08-18: **「앞으로 재진입은 무조건 한다는 전제하에 하자」**. 위 "
        "per-cell 표는 재진입이 없어(북 전용 기능) 실매매 조건이 아니다 — 이 표가 같은 팔을 "
        "채택 북 위에서 다시 잰다.",
        "",
        "🚨 **판정은 위험조정 축으로 읽는다** — `total_return` %는 수천 거래 복리라 실현 "
        "수익이 아니다(WAN-169/213). MDD · 최대 동시 리스크 · 청산 건수가 판정 열이다.",
        "",
    ]
    for segment in (PRIMARY_OOS, STRESS_OOS):
        subset = frame[frame["segment"] == segment]
        if subset.empty:
            continue
        label = "주 수치(따뜻한 연속 OOS)" if segment == PRIMARY_OOS else "스트레스(차가운 OOS)"
        lines += [
            f"### {label}",
            "",
            "| 팔 | 전량 익절 | 분할 | 본절 | 거래 | 총수익 | MDD | 수익/MDD | 승률 | "
            "최대 동시 리스크 | 최대 동시 칸 | 청산 | 명목 밀림 |",
            "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |",
        ]
        for arm in ARMS:
            hit = subset[subset["arm"] == arm.name]
            if hit.empty:
                continue
            row = hit.iloc[0]
            split = "—" if arm.partial_r is None else f"{arm.partial_r:.1f}R"
            be = "on" if arm.breakeven else ("—" if arm.is_baseline else "off")
            over = row["return_over_mdd"]
            lines.append(
                f"| `{arm.name}` | {arm.take_profit_r:.1f}R | {split} | {be} | "
                f"{int(row['num_trades'])} | {float(row['total_return']) * 100:+,.0f}% | "
                f"{float(row['max_drawdown']) * 100:.2f}% | "
                f"{'—' if pd.isna(over) else f'{float(over):,.1f}'} | "
                f"{float(row['win_rate']) * 100:.2f}% | "
                f"{float(row['max_concurrent_risk']) * 100:.2f}% | "
                f"{int(row['peak_concurrency'])} | {int(row['liquidation_events'])} | "
                f"{int(row['skipped_notional'])} |"
            )
        lines.append("")
    lines += [
        "⚠️ **per-cell 표와 셀을 직접 비교하지 말 것** — 회계가 통째로 다르다(독립 자본 vs "
        "공유 지갑 · 재진입 없음 vs 있음 · 배수 1 vs 5).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 검산 — 기준선 팔이 곧 채택 엔진인가
# --------------------------------------------------------------------------- #


def run_checksum(
    symbols: Sequence[str], timeframes: Sequence[str], *, start: str, end: str, db_path: str
) -> list[str]:
    """`A0`(전량 1.5R · 래더 끔) ≡ `harness.run_once`(인자 없는 채택 per-cell 단일).

    이 등식이 서야 「기준선 = 현행 채택 엔진」이라는 표의 전제가 참이다. 래더 인자를
    엔진에 얹은 것이 기본 실행을 건드리지 않았다는 직접 증거이기도 하다.
    """
    notes: list[str] = []
    for timeframe in timeframes:
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym,
                timeframe,
                start_ms=parse_date_ms(start),
                end_ms=parse_date_ms(end),
                need_1m=True,
                funding=True,
                db_path=db_path,
            )
            if market.empty or market.df_1m.empty:
                continue
            for segment in segments_for(warm_oos=True):
                seg_market = harness.slice_market(market, segment)
                if seg_market.empty or seg_market.df_1m.empty:
                    continue
                eval_from_ms = harness.eval_boundary_ms(market, segment)
                obr = OrderBlockDetector(OrderBlockParams()).run(seg_market.htf_df)
                arm = ARMS_BY_NAME["A0"]
                mine = run_arm(
                    seg_market, segment.name, arm, obr=obr, eval_from_ms=eval_from_ms
                ).row
                theirs = harness.run_once(
                    seg_market,
                    params=harness.pin_invalidation_cancel(
                        harness.build_params(entry_mode="zone_limit")
                    ),
                    cfg=harness.legacy_build_config(seg_market.timeframe),
                    order_block_result=obr,
                    eval_from_ms=eval_from_ms,
                ).result.metrics
                diff = abs(mine.total_return - theirs.total_return)
                notes.append(
                    f"{sym} {timeframe} {segment.name}: A0 {mine.total_return:+.6%} vs "
                    f"run_once {theirs.total_return:+.6%} (차 {diff:.2e}, "
                    f"거래 {mine.num_trades} vs {theirs.num_trades})"
                )
                if diff > 1e-12 or mine.num_trades != theirs.num_trades:
                    raise AssertionError(f"A0가 채택 엔진과 다르다: {notes[-1]}")
    return notes


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[LadderRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def symbol_mean(
    frame: pd.DataFrame, timeframe: str, segment: str, arm: str, column: str
) -> float | None:
    """유효 표본(심볼당 `MIN_TRADES` 이상)만 쓰는 심볼평균."""
    subset = _valid(frame, timeframe, segment, arm)
    if subset.empty:
        return None
    values = subset[column].dropna()
    return float(values.mean()) if not values.empty else None


def _valid(frame: pd.DataFrame, timeframe: str, segment: str, arm: str) -> pd.DataFrame:
    subset = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["arm"] == arm)
        & (frame["num_trades"] >= MIN_TRADES)
    ]
    return subset


#: 「달라졌다」로 볼 최소 폭(분수). 이보다 작으면 무변으로 읽는다 — 부동소수 잔차나
#: 심볼 하나의 끝자리 차이를 "낙폭을 샀다"로 승격시키지 않기 위한 문턱이다.
EPS = 1e-5


def _verdict(family: str, d_return: float | None, d_mdd: float | None) -> str:
    """교환비 판정 문자. 🚨 **문장이 아니라 이 값이 정본**(WAN-142 열거형 교훈).

    ⚠️ **「수익↑·낙폭↓」은 버그 신호가 아니다**(초안의 판정을 실측으로 교정 — 아래 📌).
    WAN-90이 부호를 확정한 것은 **익절선 그 자리에서** 반익절하고 러너를 홀딩하는 제안
    (E[러너] vs 1.5R)이었다. 이 이슈는 **익절선보다 아래**에서 절반을 파므로 그 식이 적용되지
    않는다 — `_LADDER_IDENTITY` 문단의 폐쇄형대로 승자에게서 `f·(T−k)`를 잃고 분할 지점을
    찍고 되돌아 손절난 거래에서 `f·(k+1)`을 번다. 부호는 두 부류의 **개수 비**가 정한다.
    """
    if d_return is None or d_mdd is None:
        return "판정 불가"
    mdd_down, mdd_up = d_mdd < -EPS, d_mdd > EPS
    ret_up, ret_down = d_return > EPS, d_return < -EPS
    if mdd_down and ret_up:
        if family == "A":
            return "수익↑·낙폭↓ (구제된 손절이 승자의 양보를 넘었다)"
        # B0(2.0R)는 A0보다 열등한 기준선이라 그 효과에 「최적 쪽으로 당김」이 섞인다.
        return "수익↑·낙폭↓ (B0이 열등한 기준선이라 효과가 섞인다 — A계열과 섞지 말 것)"
    if mdd_down:
        return "낙폭을 샀다" if ret_down else "낙폭↓·수익 무변"
    if mdd_up:
        return "둘 다 나빠졌다" if ret_down else "낙폭↑·수익 무변"
    if ret_down:
        return "낙폭은 그대로인데 기대값만 깎였다"
    return "무변"


@dataclass(frozen=True)
class TradeOff:
    """한 팔의 「기대값 −a%p 대신 MDD −b%p」 교환비 (WAN-323 완료기준 3)."""

    timeframe: str
    segment: str
    arm: str
    baseline: str
    d_return: float | None
    """총수익 심볼평균 차이(%p 단위 아님 — 분수). 음수 = 기대값을 깎았다."""
    d_mdd: float | None
    """MDD 심볼평균 차이(분수). 음수 = 낙폭을 줄였다(= 사려던 것)."""
    d_win: float | None
    d_net_r: float | None
    ratio: float | None
    """MDD 1%p를 사는 데 낸 수익 %p — 작을수록 싸다(음수면 오히려 받았다).

    MDD가 `EPS`보다 크게 줄어든 팔에서만 정의된다(0에 가까운 분모의 폭주 방지)."""
    verdict: str

    @property
    def text(self) -> str:
        if self.d_return is None or self.d_mdd is None:
            return f"{self.arm}: ⚠️ 판정 불가(유효 표본 부족)"
        return (
            f"{self.arm}: 기대값 {self.d_return * 100:+.2f}%p 대신 "
            f"MDD {self.d_mdd * 100:+.2f}%p ({self.verdict})"
        )


def trade_off(frame: pd.DataFrame, timeframe: str, segment: str, arm: str) -> TradeOff:
    base = BASELINE_OF[arm]

    def delta(column: str) -> float | None:
        mine = symbol_mean(frame, timeframe, segment, arm, column)
        theirs = symbol_mean(frame, timeframe, segment, base, column)
        if mine is None or theirs is None:
            return None
        return mine - theirs

    d_return, d_mdd = delta("total_return"), delta("max_drawdown")
    ratio: float | None = None
    if d_return is not None and d_mdd is not None and d_mdd < -EPS:
        # ⚠️ `EPS` 아래의 MDD 델타에서는 비를 내지 않는다 — 0에 가까운 분모가 교환비를
        # 1e14 같은 값으로 부풀려 "공짜로 샀다"처럼 읽히기 때문이다.
        ratio = -d_return / -d_mdd
    verdict = _verdict(ARMS_BY_NAME[arm].family, d_return, d_mdd)
    return TradeOff(
        timeframe=timeframe,
        segment=segment,
        arm=arm,
        baseline=base,
        d_return=d_return,
        d_mdd=d_mdd,
        d_win=delta("win_rate"),
        d_net_r=delta("mean_net_r"),
        ratio=ratio,
        verdict=verdict,
    )


def breakeven_split(frame: pd.DataFrame, timeframe: str, segment: str, arm: str) -> float | None:
    """본절 스탑 축 분해 — (분할+본절) − (분할만)의 MDD 델타 (WAN-323 완료기준 4).

    MDD가 줄었다면 그게 「미리 덜어낸 덕」인지 「본절 스탑 덕」인지 가른다. `arm`은
    `be_on` 팔이어야 하고, 짝인 `be_off` 팔과 대조한다.
    """
    if not arm.endswith("be_on"):
        return None
    twin = arm.replace("be_on", "be_off")
    return _arm_delta(frame, timeframe, segment, arm, twin, "max_drawdown")


def _arm_delta(
    frame: pd.DataFrame, timeframe: str, segment: str, arm: str, other: str, column: str
) -> float | None:
    """두 팔의 심볼평균 차이(`arm` − `other`). 어느 쪽이든 유효 표본이 없으면 None."""
    mine = symbol_mean(frame, timeframe, segment, arm, column)
    theirs = symbol_mean(frame, timeframe, segment, other, column)
    return None if mine is None or theirs is None else mine - theirs


def leave_one_out(
    frame: pd.DataFrame, timeframe: str, segment: str, arm: str
) -> list[tuple[str, float]]:
    """종목 하나씩 빼며 총수익 심볼평균을 다시 낸다(강건성, WAN-323 §3-5)."""
    subset = _valid(frame, timeframe, segment, arm)
    out: list[tuple[str, float]] = []
    for symbol in sorted(subset["symbol"].unique()):
        rest = subset[subset["symbol"] != symbol]["total_return"]
        if not rest.empty:
            out.append((_bare(str(symbol)), float(rest.mean())))
    return out


def partial_reach_rate(frame: pd.DataFrame, timeframe: str, segment: str, arm: str) -> float | None:
    """분할 지점 도달률 = 부분 청산이 일어난 거래 / 전체 거래.

    ⚠️ **도달률로 손익을 대신하지 말 것**(WAN-137 방법론 경고 상속) — 판정은 손익으로만.
    """
    subset = _valid(frame, timeframe, segment, arm)
    trades = float(subset["num_trades"].sum())
    return float(subset["n_partial"].sum()) / trades if trades else None


def breakeven_conversion(
    frame: pd.DataFrame, timeframe: str, segment: str, arm: str
) -> float | None:
    """본절 전환율 = 부분 익절 뒤 손절로 끝난 거래 / 부분 익절이 일어난 거래.

    본절이 켜진 팔에서는 그 손절이 곧 **진입가 청산**이라 이 값이 장치의 발동률이다.
    """
    subset = _valid(frame, timeframe, segment, arm)
    partial = float(subset["n_partial"].sum())
    return float(subset["n_partial_then_stop"].sum()) / partial if partial else None


# --------------------------------------------------------------------------- #
# 요약 md
# --------------------------------------------------------------------------- #


def _pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%" if signed else f"{value * 100:.2f}%"


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:+.{digits}f}"


def build_summary(frame: pd.DataFrame) -> str:
    timeframes = [tf for tf in harness.DEFAULT_TIMEFRAMES if tf in set(frame["timeframe"])]
    lines: list[str] = [
        "# WAN-323 — 반익절 래더: 「기대값 얼마를 내고 MDD 얼마를 사는가」",
        "",
        "재현: `uv run python -m backtest.wan323_partial_tp_ladder --tf 4h --jobs 4` → "
        "`--tf 2h --append` → `--tf 1h --append` (`--from-csv`로 요약만 재생성).",
        "",
        "🔒 **WAN-90이 이미 답한 것**: E[러너] ≈ 0R이라 부호식의 부호는 **음수** — 래더는 "
        "기대값을 깎는다. 이 표의 질문은 「더 버는가」가 아니라 **교환비**다. 래더가 수익에서 "
        "기준선을 이기면 발견이 아니라 **배선 버그를 먼저 의심**할 것.",
        "",
        "**승률의 정의(WAN-323 §3-1)**: **순손익 > 0이면 승리**(수수료·슬리피지·펀딩 반영 후). "
        "부분 익절 뒤 본전으로 나간 거래는 그로스가 0 언저리여도 비용 때문에 대개 패배로 센다 — "
        "`Trade.is_win`이 곧 이 정의이고 회귀 테스트가 동작으로 고정한다.",
        "",
        f"**좌표**: {frame['symbol'].nunique()}종목 × "
        f"못 박은 6년({harness.DEFAULT_START}~{harness.DEFAULT_END}) × "
        f"{'·'.join(timeframes) if timeframes else '—'} · `baseline` 단독 · per-cell 단일 포지션 · "
        "핀 없음(WAN-305). 주 구간 = `oos_warm`(따뜻), 스트레스 = `oos`(차가움).",
        "",
        "⚠️ **이 표의 MDD는 per-cell MDD**라 채택 북(WAN-213) MDD와 직접 비교 금지. "
        "⚠️ **체결 보수화(`pen_5bp`) 미측정** — 부분 익절은 청산을 한 번 더 늘려 낙관 체결 "
        "가정에 **더** 기댄다. ⚠️ **A·B 계열의 절대값을 섞지 말 것**(B는 「B0 대비」로만).",
        "",
    ]

    for segment in (PRIMARY_OOS, STRESS_OOS):
        label = "주 수치(따뜻한 연속 OOS)" if segment == PRIMARY_OOS else "스트레스(차가운 OOS)"
        lines += [f"## {label} — 팔별 성적과 교환비", ""]
        for timeframe in timeframes:
            lines += [
                f"### {timeframe} · {segment}",
                "",
                "| 팔 | 전량 익절 | 분할 | 본절 | 거래 | 총수익 | MDD | 승률 | "
                "mean_net_r | mean_gross_r | 도달률 | 본절 전환 | 교환비(수익%p/MDD%p) | 판정 |",
                "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |",
            ]
            for arm in ARMS:
                subset = _valid(frame, timeframe, segment, arm.name)
                if subset.empty:
                    continue
                off = trade_off(frame, timeframe, segment, arm.name)

                def mean(
                    column: str,
                    name: str = arm.name,
                    tf: str = timeframe,
                    seg: str = segment,
                ) -> float | None:
                    return symbol_mean(frame, tf, seg, name, column)

                split = "—" if arm.partial_r is None else f"{arm.partial_r:.1f}R"
                be = "on" if arm.breakeven else ("—" if arm.is_baseline else "off")
                reach = partial_reach_rate(frame, timeframe, segment, arm.name)
                conv = breakeven_conversion(frame, timeframe, segment, arm.name)
                lines.append(
                    f"| `{arm.name}` | {arm.take_profit_r:.1f}R | {split} | {be} | "
                    f"{int(subset['num_trades'].sum())} | "
                    f"{_pct(mean('total_return'))} | "
                    f"{_pct(mean('max_drawdown'), signed=False)} | "
                    f"{_pct(mean('win_rate'), signed=False)} | "
                    f"{_num(mean('mean_net_r'))} | {_num(mean('mean_gross_r'))} | "
                    f"{_pct(reach, signed=False)} | {_pct(conv, signed=False)} | "
                    f"{'—' if off.ratio is None else f'{off.ratio:.2f}'} | "
                    f"{'기준선' if arm.is_baseline else off.verdict} |"
                )
            lines.append("")

    # 교환비 판정 문장 (완료기준 3)
    lines += ["## 판정 문장 — 팔마다 「기대값 −a%p 대신 MDD −b%p」", ""]
    for timeframe in timeframes:
        lines.append(f"**{timeframe}** ({PRIMARY_OOS}):")
        lines.append("")
        for arm in ARMS:
            if arm.is_baseline:
                continue
            off = trade_off(frame, timeframe, PRIMARY_OOS, arm.name)
            lines.append(f"* {off.text}")
        lines.append("")

    # 본절 스탑 축 분해 (완료기준 4)
    lines += [
        "## 본절 스탑 축 분해 — MDD 개선이 「덜어낸 덕」인가 「본절 덕」인가",
        "",
        "열은 **(분할+본절) − (분할만)**이다. 음수면 그 몫이 본절 스탑의 것이다.",
        "",
        "| TF | 구간 | 팔 | ΔMDD(본절의 몫) | Δ총수익(본절의 몫) | Δ승률(본절의 몫) |",
        "| -- | -- | -- | -- | -- | -- |",
    ]
    for timeframe in timeframes:
        for segment in (PRIMARY_OOS, STRESS_OOS):
            for arm in ARMS:
                if not arm.name.endswith("be_on"):
                    continue
                twin = arm.name.replace("be_on", "be_off")
                mdd = _arm_delta(frame, timeframe, segment, arm.name, twin, "max_drawdown")
                if mdd is None:
                    continue
                ret = _arm_delta(frame, timeframe, segment, arm.name, twin, "total_return")
                win = _arm_delta(frame, timeframe, segment, arm.name, twin, "win_rate")
                lines.append(
                    f"| {timeframe} | {segment} | `{arm.name}` | {_pct(mdd)} | "
                    f"{_pct(ret)} | {_pct(win)} |"
                )
    lines.append("")

    # 강건성 (완료기준 §3-5)
    lines += [
        "## 강건성 — leave-one-out (종목 하나씩 빼기)",
        "",
        "⚠️ 이 저장소는 플러스가 ETH 하나에서 나오는 일이 반복됐다. 최악 케이스를 함께 읽는다.",
        "",
        "| TF | 팔 | 총수익(전체) | 최악(제외 종목) |",
        "| -- | -- | -- | -- |",
    ]
    for timeframe in timeframes:
        for arm in ARMS:
            loo = leave_one_out(frame, timeframe, PRIMARY_OOS, arm.name)
            if not loo:
                continue
            worst = min(loo, key=lambda pair: pair[1])
            lines.append(
                f"| {timeframe} | `{arm.name}` | "
                f"{_pct(symbol_mean(frame, timeframe, PRIMARY_OOS, arm.name, 'total_return'))} | "
                f"{_pct(worst[1])} (−{worst[0]}) |"
            )
    lines += [
        "",
        "## 안전 (기록)",
        "",
        "* **기본값·토대 불변** — 래더는 전부 **옵트인**이다(`ConfluenceParams()` 그대로 · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지). 채택은 **재-베이스라인 = 사용자 결정**이고 "
        "개발자 임의 착수 금지.",
        "* 🚨 **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 익절 구조는 알파를 "
        "만들지 못하고 **위험의 모양만 바꾼다**(WAN-90). 래더의 값은 수익 증가가 아니라 "
        "위험 관리다.",
        "* ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값 · 큐 우선순위 미모델(WAN-98 Canceled).",
        "* ⚠️ **도달률로 손익을 대신하지 말 것**(WAN-137 방법론 경고) — 분할 지점 도달률이 높아도 "
        "손익이 좋다는 뜻이 아니다.",
        "* ⚠️ 자본곡선은 **거래 단위**라 부분 익절의 실현손익도 최종 청산 시각에 반영된다 — "
        "래더의 MDD 이득이 이 회계에서 **과소평가**될 수 있다(두 팔이 같은 회계라 방향은 유효).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-323 반익절 래더 격자")
    parser.add_argument("--tf", default=None, help="쉼표 구분 TF(기본: 채택 4TF)")
    parser.add_argument("--symbols", default=None, help="쉼표 구분 심볼(기본: 채택 12종목)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=None, help="(심볼, TF) 병렬 워커 수")
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자를 돌지 않고 요약만 재생성")
    parser.add_argument("--checksum", action="store_true", help="A0 ≡ harness.run_once 검산만")
    parser.add_argument(
        "--book",
        action="store_true",
        help="채택 북(cap_only 5배 · 재진입 band)에서 팔을 다시 잰다(별도 CSV)",
    )
    parser.add_argument(
        "--book-arms",
        default=None,
        help=f"북 판에서 돌릴 팔(쉼표) 또는 all. 기본: {','.join(DEFAULT_BOOK_ARMS)}",
    )
    return parser.parse_args(argv)


def _resolve_book_arms(arg: str | None) -> list[LadderArm]:
    if arg is None:
        return [ARMS_BY_NAME[name] for name in DEFAULT_BOOK_ARMS]
    if arg.strip().lower() == "all":
        return list(ARMS)
    names = [name.strip() for name in arg.split(",") if name.strip()]
    unknown = [name for name in names if name not in ARMS_BY_NAME]
    if unknown:
        raise ValueError(f"모르는 팔입니다: {unknown} (가능: {', '.join(ARMS_BY_NAME)})")
    return [ARMS_BY_NAME[name] for name in names]


def _write_summary(frame: pd.DataFrame) -> None:
    """per-cell 요약 + (있으면) 북 판 절을 한 파일로 낸다."""
    text = build_summary(frame)
    if BOOK_CSV_PATH.exists():
        text = text + "\n" + build_book_summary(pd.read_csv(BOOK_CSV_PATH))
    SUMMARY_PATH.write_text(text, encoding="utf-8")
    print(f"[wan323] 요약: {SUMMARY_PATH}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    timeframes = (
        tuple(t.strip() for t in args.tf.split(",") if t.strip())
        if args.tf
        else harness.DEFAULT_TIMEFRAMES
    )
    symbols = (
        tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        if args.symbols
        else harness.DEFAULT_SYMBOLS
    )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.checksum:
        for note in run_checksum(
            symbols, timeframes, start=args.start, end=args.end, db_path=harness.DB_PATH
        ):
            print(f"[wan323] 검산 {note}", flush=True)
        return 0

    if args.book:

        def _persist(arm_rows: list[BookLadderRow]) -> None:
            frame = pd.DataFrame([r.model_dump() for r in arm_rows])
            if BOOK_CSV_PATH.exists():
                prior = pd.read_csv(BOOK_CSV_PATH)
                frame = pd.concat([prior, frame], ignore_index=True).drop_duplicates(
                    subset=["arm", "segment"], keep="last"
                )
            frame.to_csv(BOOK_CSV_PATH, index=False)
            print(f"[wan323] 북 CSV 적재: {BOOK_CSV_PATH} ({len(frame)}행)", flush=True)

        run_book_report(
            symbols,
            timeframes,
            arms=_resolve_book_arms(args.book_arms),
            start=args.start,
            end=args.end,
            jobs=args.jobs if args.jobs is not None else harness.default_jobs(),
            on_arm=_persist,
        )
        if CSV_PATH.exists():
            _write_summary(pd.read_csv(CSV_PATH))
        return 0

    if args.from_csv:
        if not CSV_PATH.exists():
            print(f"[wan323] {CSV_PATH}가 없습니다 — 먼저 격자를 돌리세요.", flush=True)
            return 1
        frame = pd.read_csv(CSV_PATH)
    else:
        rows = run_report(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            jobs=args.jobs if args.jobs is not None else harness.default_jobs(),
        )
        frame = rows_to_frame(rows)
        if args.append and CSV_PATH.exists():
            old = pd.read_csv(CSV_PATH)
            # 같은 (TF, 심볼, 구간, 팔)이 두 번 들어가지 않게 새 판이 이긴다.
            keys = ["timeframe", "symbol", "segment", "arm"]
            merged = pd.concat([old, frame], ignore_index=True)
            frame = merged.drop_duplicates(subset=keys, keep="last")
        frame.to_csv(CSV_PATH, index=False)
        print(f"[wan323] CSV: {CSV_PATH} ({len(frame)}행)", flush=True)

    _write_summary(frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
