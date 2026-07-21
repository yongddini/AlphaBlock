"""WAN-155: 좁은 존 고정 + 익절 「자 vs 배수」 8조합 격자 — 오늘 엔진에서 처음 잰다.

## 질문 (사용자 원문 · PM 범위 확정 2026-07-21)

> "나는 좁은존을 기본으로 두고 1R의 기준을 변경하는것과, 손익비 차이를 두는거에 차이를
> 보고싶다"

**존폭 필터를 고정 조건으로 깔고**(`max_zone_width_atr=1.28` — 비교 축이 아니다), 그 위에서
「익절 목표를 멀리 두는」 **두 지렛대**를 같은 표에서 비교한다:

* **축 1 — 1R의 자**: `entry_r`(현행: 진입가 → 무효화 경계) vs `zone_height`(존 위−아래 경계).
  볼린저가 진입가를 존 안쪽으로 끌어내리므로 진입가 ≤ 존 위 경계 → **존 높이 ≥ 현행 1R**,
  즉 자를 바꾸면 목표가 **셋업마다 다른 비율로** 멀어진다.
* **축 2 — 배수**: `take_profit_r` 1.0 · 1.5(현행) · 2.0 · 3.0. 자는 그대로 두고
  **모든 셋업에 똑같은 비율로** 목표를 민다.

2 × 4 = **8조합**을 15m·1h × IS/OOS × 6심볼에서 돌린다. 따로 돌리면 두 지렛대를 나란히
놓을 수 없다(이 저장소가 반복해 겪은 문제).

## WAN-143과의 관계 — ⚠️ 셀 직접 비교 금지

WAN-143(존높이 익절 4조합)은 **존 병합이 켜져 있던 시절**(`LEGACY_OB_PARAMS`) 표이고, 병합은
존의 키를 직접 부풀린다(위=최대·아래=최소). 그 판정 (c)와 수치(1h OOS +2.98% vs +5.48% 등)는
오늘 엔진 값이 아니다 — **이 표가 그 자리를 대체한다**. 이 모듈은 wan143의 격자 골격과
`make_zone_height_override`(순수 함수)만 물려받고 **핀은 하나도 물려받지 않는다**
(`LEGACY_COMBINE_OBS`·`LEGACY_BAND_BAR`·`pin_band_bar`·`LEGACY_OB_PARAMS` 미사용 — 회귀
테스트가 후보 집합으로 고정, WAN-152 패턴). 그리고 이 표에는 WAN-143에 없던 축이 둘 있다:
존폭 필터 고정(WAN-158 배선)과 배수 스윕을 **두 자 모두**에서 돌리는 것.

## 고정 입력 (오늘의 채택 기본값 + 필터)

오프셋 2bp(WAN-112) × `intrabar_live` 밴드(WAN-132) × `unconditional` 게이트(WAN-123) ×
롱 온리(WAN-87) × 분리 존(WAN-149) × **`max_zone_width_atr=1.28`**(WAN-158 파라미터 —
기본값 전환이 아니라 이 모듈의 명시 입력이다. 단위는 **ATR 배수**, 출처는
`wan154_threshold_stability.csv` 하위 1/3 분위 1.16~1.39 군집 + 사용자 결정) × 못 박은 창
2023-07-14~2026-07-15 × 렌즈 `baseline` 단독(WAN-128).

⚠️ **`baseline`은 낙관 렌즈다**(닿으면 체결) — 모든 수치는 상한으로 읽는다. WAN-154 §4에서
존높이류 축은 15m 관통 요구에 유의성을 잃었다.

## 기준점 팔

`reference` = **필터 끔** × `entry_r` × 1.5R(= 오늘의 채택 기본값 그대로). 「좁은 존만
매매하면 존 높이도 작다 — 존높이 자가 *현행 대비*로는 목표를 밀지만 **절대적으로는 지금
기본값보다 가까운 목표**일 수 있다」는 판정문 필수 문장을 이 팔이 계량한다
(`tp_dist_frac_median` 대조).

재현:

```
uv run python -m backtest.wan155_tp_ruler_vs_multiple --tf 1h            # 1h 먼저(가벼움)
uv run python -m backtest.wan155_tp_ruler_vs_multiple --tf 15m --append  # 15m 뒤에 붙임
uv run python -m backtest.wan155_tp_ruler_vs_multiple --from-csv         # 요약만 재생성
```

`--filter off`는 **옵트인 참고 축**이다(이 이슈의 산출 표에는 없다 — 사용자가 2026-07-21
대화에서 요청했다가 **취소**했다). ⚠️ 필터 끔 = **넓은 존까지 포함한 전체 매매**다. 그
축에서만 볼린저가 진입가를 존 안으로 끌 수 있어 존높이 자의 지렛대(존높이 > 현행 1R)가
실제로 작동하므로, WAN-143의 원 질문(필터 없는 엔진의 존높이 익절)을 오늘 엔진에서 다시
재고 싶어지면 `--filter off --append` 두 번(1h · 15m)으로 돌릴 수 있다. `--append`는 TF
단위가 아니라 **행 좌표 단위**로 병합한다(`merge_rows`) — 같은 TF의 부분 재실행이 기존
행을 지우지 않는다.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.wan137_resistance_distance import DEFAULT_END, DEFAULT_START, DEFAULT_SYMBOLS
from backtest.wan143_zone_height_tp import (
    TP_ENTRY,
    TP_RULES,
    TP_ZONE,
    apply_guard,
    make_zone_height_override,
)
from backtest.wan152_selection_vs_geometry import per_trade_records
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockResult

REPORTS_DIR = Path("backtest/reports")

FILTER_THRESHOLD = 1.28
"""존폭 필터 문턱(**ATR 배수** — `0.0128` 같은 분수를 넣으면 거의 전부 걸러진다, WAN-158
단위 경고). 사용자 결정값(2026-07-21) — WAN-158 권고(15m 1.24 · 1h 1.32)의 가운데."""

R_MULTIPLES: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0)
"""축 2 — 두 자(`entry_r`·`zone_height`) **모두**에서 돈다(PM 범위 확정: 따로 돌리면 두
지렛대를 나란히 놓을 수 없다)."""

R_DEFAULT = 1.5
"""현행 채택 배수(WAN-90 검증·유지). 자 축 판정(축 1)은 이 배수에서 낸다."""

GUARD_ON = 0.003
"""WAN-79 채택 가드. **주 격자는 이 값 고정**이고 가드 축은 부차다(PM 범위 확정 —
「가드를 푸는 것」은 이 이슈의 결론이 아니다. 옛 표에서 15m은 가드를 풀면 −4.09%p였다)."""
GUARD_OFF = 0.0
GUARDS: tuple[float, ...] = (GUARD_ON, GUARD_OFF)

MIN_TRADES_PER_SYMBOL = 20
"""WAN-84 유효 기준 — 이 미만인 (심볼, 셀)은 심볼평균에서 **제외**한다(제외 수 병기).
필터가 거래를 3분의 1로 줄이고 존높이 팔은 슬롯을 더 오래 잠가 표본이 얇다(PM 경고:
TRX 15m은 필터만으로 16거래)."""

MIN_SYMBOLS_FOR_VERDICT = 3
"""유효 심볼이 이보다 적으면 (a)/(b)/(c) 대신 「판정 불가」(WAN-142/143/152 게이트 관행)."""

TRADE_GAP_DEMOTE = 0.05
"""두 자의 시퀀싱 거래 수 차이가 이 비율을 넘으면 판정을 자동 강등한다(이슈 §4 —
존높이 팔은 목표가 멀어 동시 1포지션 슬롯을 더 오래 잠근다)."""


def arm_label(tp_rule: str, r_multiple: float, *, filter_on: bool = True) -> str:
    base = f"{tp_rule} {r_multiple:.1f}R"
    return base if filter_on else f"{base} (필터 끔)"


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class RulerRow(BaseModel):
    """한 (심볼, TF, 구간, 자, 배수, 가드, 필터) 셀.

    실현 손익비 열은 WAN-154 §1′ 산식(`per_trade_records` — 실현 손익 ÷ 그 거래의 리스크
    금액)을 그대로 재사용한다. ⚠️ 단 **손익분기 승률은 wan152의 고정 1.5R 식이 아니라 실현
    분포에서 낸다**(`net_r_win`·`net_r_loss`의 기대값 0 조건) — 배수 스윕은 R이 1.5가
    아니고, 존높이 자는 실현 승리 R이 셋업마다 다르기 때문이다.

    📌 **두 자의 `mean_net_r`은 직접 비교 가능하다** — 익절 자만 바뀌고 손절(무효화 경계)은
    두 자가 같아, 1R(리스크 금액)이 셋업 단위로 동일하다(WAN-154의 「장벽 간 비교 금지」와
    다른 상황 — 그쪽은 손절 자체가 달랐다).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    tp_rule: str
    r_multiple: float
    guard: float
    filter_on: bool
    num_candidates: int
    """이 구간의 체결 셋업 수(시퀀싱 이전). 같은 (필터, 구간)이면 자·배수와 무관하게 같다."""
    num_trades: int
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float | None
    n_take_profit: int
    n_stop_loss: int
    n_end_of_data: int
    fill_rate_full: float | None
    """전체 창 체결률(구간 분해 전 — 같은 팔의 IS/OOS 행에 같은 값이 실린다, 진단용)."""
    mean_net_r: float | None
    net_r_win: float | None
    net_r_loss: float | None
    cost_r_median: float | None
    breakeven_win_rate: float | None
    win_rate_margin: float | None
    profit_factor: float | None
    cap_hit_rate: float | None
    effective_risk_mean: float | None
    guard_reject_rate: float | None
    """후보 중 가드(0.3%)에 걸리는 비율 — 후보 단위 결정값(시퀀싱 순서 무관)."""
    tp_dist_frac_median: float | None
    """익절 목표까지 거리 ÷ 진입가, 후보 중앙값 — 「절대 목표 거리」 문장의 계량."""
    height_over_stop_med: float | None = None
    """존높이 ÷ 현행 1R(진입가→손절) 후보 중앙값 — 두 지렛대가 「같은 것」인지의 계량.
    이 비율이 상수면 자 교체는 배수 조절과 같은 지렛대이고, 흩어지면 다른 지렛대다."""
    height_over_stop_p25: float | None = None
    height_over_stop_p75: float | None = None

    @property
    def arm(self) -> str:
        return arm_label(self.tp_rule, self.r_multiple, filter_on=self.filter_on)


# --------------------------------------------------------------------------- #
# 손익분기 승률 — 실현 분포 기반
# --------------------------------------------------------------------------- #


def empirical_breakeven(net_r_win: float | None, net_r_loss: float | None) -> float | None:
    """실현 승/패 R 평균으로 낸 손익분기 승률: p·W + (1−p)·L = 0 → p = −L/(W−L).

    wan152의 식은 고정 익절 배수(1.5)를 전제한다 — 배수 스윕과 존높이 자(실현 R이 셋업마다
    다름)에서는 그 전제가 깨지므로 실현 분포에서 직접 낸다. 승리나 패배가 아예 없으면(또는
    부호가 정의를 깨면) None — 무한대·음수 승률을 지어내지 않는다.
    """
    if net_r_win is None or net_r_loss is None:
        return None
    if net_r_win <= 0 or net_r_loss >= 0:
        return None
    return -net_r_loss / (net_r_win - net_r_loss)


# --------------------------------------------------------------------------- #
# 셀 — 한 (심볼, TF)의 팔별 후보
# --------------------------------------------------------------------------- #


def _tp_distance(cand: _Candidate, tp_rule: str, r_multiple: float) -> float | None:
    """이 셋업의 익절 목표까지 거리(가격 단위). 존높이 자는 height ≤ 0이면 현행 폴백."""
    stop_dist = (
        cand.entry_price - cand.stop_price
        if cand.side is PositionSide.LONG
        else cand.stop_price - cand.entry_price
    )
    if tp_rule == TP_ZONE and cand.order_block is not None:
        height = cand.order_block.top - cand.order_block.bottom
        if height > 0:
            return height * r_multiple
    if stop_dist <= 0:
        return None
    return stop_dist * r_multiple


def _height_over_stop(cand: _Candidate) -> float | None:
    """존높이 ÷ 현행 1R — 자 교체가 목표를 몇 배로 미는지(셋업 단위)."""
    stop_dist = (
        cand.entry_price - cand.stop_price
        if cand.side is PositionSide.LONG
        else cand.stop_price - cand.entry_price
    )
    if stop_dist <= 0 or cand.order_block is None:
        return None
    height = cand.order_block.top - cand.order_block.bottom
    if height <= 0:
        return None
    return height / stop_dist


def _quantiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    series = pd.Series(values)
    return (
        float(series.quantile(0.25)),
        float(series.median()),
        float(series.quantile(0.75)),
    )


def build_arm_candidates(
    market: MarketData,
    *,
    tp_rule: str,
    r_multiple: float,
    filter_on: bool = True,
    base: ConfluenceParams | None = None,
    order_block_result: OrderBlockResult | None = None,
) -> tuple[list[_Candidate], float | None]:
    """한 팔의 후보(전체 창)와 전체 창 체결률.

    ⚠️ **핀을 쓰지 않는다** — 탐지·컨플루언스 전부 오늘의 채택 기본값이고, 움직이는 것은
    익절 자·배수·존폭 필터뿐이다(`harness.build_params` 경유 = CLI와 같은 조립 경로).
    `base`·`order_block_result`는 테스트 주입구다(합성 데이터는 채택 기본값에서 후보가
    0개라 검정이 공허해진다 — wan152 테스트 관행) — 실행 경로(`run_cell`)는 넘기지 않는다.
    """
    params = harness.build_params(
        take_profit_r=r_multiple,
        max_zone_width_atr=FILTER_THRESHOLD if filter_on else None,
        base=base,
    )
    override = None if tp_rule == TP_ENTRY else make_zone_height_override(params, r_multiple)
    cfg = harness.build_config(market.timeframe)
    obr = order_block_result or harness.detect_order_blocks(market)
    candidates, stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=obr,
        take_profit_override=override,
    )
    return candidates, stats.fill_rate


def is_boundary_ms(market: MarketData) -> int:
    """IS/OOS 경계(상위TF open_time 축의 2/3 지점) — wan152 셀과 같은 정의."""
    times = market.htf_df["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    return start + int((end - start) * IS_FRACTION)


def _segment_candidates(
    candidates: list[_Candidate], boundary: int, segment: str
) -> list[_Candidate]:
    return [c for c in candidates if (c.trigger_time < boundary) == (segment == SEGMENT_IS)]


def build_rows_for_arm(
    market: MarketData,
    candidates: list[_Candidate],
    fill_rate_full: float | None,
    *,
    tp_rule: str,
    r_multiple: float,
    filter_on: bool,
    guards: Sequence[float] = GUARDS,
) -> list[RulerRow]:
    """한 팔의 후보를 IS/OOS × 가드로 시퀀싱해 행을 만든다(구간별 초기자본 재시작)."""
    boundary = is_boundary_ms(market)
    rows: list[RulerRow] = []
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        seg_cands = _segment_candidates(candidates, boundary, segment)
        hos_values = [v for v in (_height_over_stop(c) for c in seg_cands) if v is not None]
        hos_p25, hos_med, hos_p75 = _quantiles(hos_values)
        tp_dists = [
            d / c.entry_price
            for c in seg_cands
            if (d := _tp_distance(c, tp_rule, r_multiple)) is not None
        ]
        _, tp_med, _ = _quantiles(tp_dists)
        for guard in guards:
            cfg = apply_guard(harness.build_config(market.timeframe), guard)
            paired = sequence_with_candidates(seg_cands, cfg, market.funding_rates)
            trades = [t for _, t in paired]
            metrics = build_result_from_trades(trades, cfg, market.timeframe).metrics
            reasons = Counter(cand.reason for cand, _ in paired)
            records = per_trade_records(seg_cands, market, market.timeframe, guard=guard)
            wins = [r.net_r for r in records if r.win]
            losses = [r.net_r for r in records if not r.win]
            net_r_win = statistics.fmean(wins) if wins else None
            net_r_loss = statistics.fmean(losses) if losses else None
            breakeven = empirical_breakeven(net_r_win, net_r_loss)
            gains = sum(r.pnl for r in records if r.win)
            loss_amt = sum(-r.pnl for r in records if not r.win)
            cap = [r.cap_hit for r in records if r.cap_hit is not None]
            eff = [r.effective_risk for r in records if r.effective_risk is not None]
            reject = (
                sum(1 for c in seg_cands if _stop_frac(c) < guard) / len(seg_cands)
                if seg_cands
                else None
            )
            rows.append(
                RulerRow(
                    symbol=market.symbol,
                    timeframe=market.timeframe,
                    segment=segment,
                    tp_rule=tp_rule,
                    r_multiple=r_multiple,
                    guard=guard,
                    filter_on=filter_on,
                    num_candidates=len(seg_cands),
                    num_trades=metrics.num_trades,
                    total_return=metrics.total_return,
                    max_drawdown=metrics.max_drawdown,
                    win_rate=metrics.win_rate,
                    sharpe=metrics.sharpe,
                    n_take_profit=reasons.get(ExitReason.TAKE_PROFIT, 0),
                    n_stop_loss=reasons.get(ExitReason.STOP_LOSS, 0),
                    n_end_of_data=reasons.get(ExitReason.END_OF_DATA, 0),
                    fill_rate_full=fill_rate_full,
                    mean_net_r=statistics.fmean(r.net_r for r in records) if records else None,
                    net_r_win=net_r_win,
                    net_r_loss=net_r_loss,
                    cost_r_median=(
                        statistics.median(r.cost_r for r in records) if records else None
                    ),
                    breakeven_win_rate=breakeven,
                    win_rate_margin=(None if breakeven is None else metrics.win_rate - breakeven),
                    profit_factor=(gains / loss_amt) if loss_amt > 0 else None,
                    cap_hit_rate=(sum(cap) / len(cap)) if cap else None,
                    effective_risk_mean=statistics.fmean(eff) if eff else None,
                    guard_reject_rate=reject,
                    tp_dist_frac_median=tp_med,
                    height_over_stop_med=hos_med,
                    height_over_stop_p25=hos_p25,
                    height_over_stop_p75=hos_p75,
                )
            )
    return rows


def _stop_frac(cand: _Candidate) -> float:
    return abs(cand.entry_price - cand.stop_price) / cand.entry_price


def _run_grid_arms(
    market: MarketData, *, filter_on: bool, log: bool = True
) -> tuple[list[RulerRow], int]:
    """한 필터 상태의 자 × 배수 8팔 — 후보 집합 불변 검산 포함. 반환: (행, 후보 수).

    **검산**: 익절은 청산만 바꾸고 존폭 필터는 자·배수를 읽지 않으므로, 같은 필터 상태의
    8팔은 체결 셋업 집합(진입 시각)이 비트 단위로 같아야 한다 — 어긋나면 배선 버그다
    (wan143과 같은 불변, 팔이 8개로 늘었다).
    """
    rows: list[RulerRow] = []
    entry_sets: dict[tuple[str, float], list[int]] = {}
    tag = "" if filter_on else " (필터 끔)"
    for r_multiple in R_MULTIPLES:
        for tp_rule in TP_RULES:
            t0 = time.time()
            candidates, fill_rate = build_arm_candidates(
                market, tp_rule=tp_rule, r_multiple=r_multiple, filter_on=filter_on
            )
            entry_sets[(tp_rule, r_multiple)] = sorted(c.entry_time for c in candidates)
            rows.extend(
                build_rows_for_arm(
                    market,
                    candidates,
                    fill_rate,
                    tp_rule=tp_rule,
                    r_multiple=r_multiple,
                    filter_on=filter_on,
                )
            )
            if log:
                print(
                    f"[wan155] {market.symbol} {market.timeframe} "
                    f"{arm_label(tp_rule, r_multiple)}{tag}: 후보 {len(candidates)} "
                    f"({time.time() - t0:.0f}s)",
                    flush=True,
                )
    base_key = (TP_ENTRY, R_MULTIPLES[0])
    for key, entries in entry_sets.items():
        if entries != entry_sets[base_key]:
            raise AssertionError(
                f"후보 집합 불일치 — {market.symbol} {market.timeframe} {key}{tag}: "
                f"{len(entries)} vs {len(entry_sets[base_key])}. "
                "익절 자·배수가 진입을 바꾸는 배선 버그다."
            )
    return rows, len(entry_sets[base_key])


def run_cell(
    market: MarketData,
    *,
    filter_states: tuple[bool, ...] = (True,),
    log: bool = True,
) -> list[RulerRow]:
    """한 (심볼, TF)의 자 × 배수 격자(필터 상태별) + 기준점 팔.

    후보 생성(비싼 부분)은 팔마다 한 번이고 가드 축은 사이징 단계라 같은 후보를
    재시퀀싱한다. 필터 끔 격자(`filter_states`에 `False`)를 돌리면 기준점 팔(필터 끔 ×
    `entry_r` × 1.5R)이 그 격자에 포함되므로 따로 돌리지 않는다.
    """
    rows: list[RulerRow] = []
    counts: dict[bool, int] = {}
    for state in filter_states:
        state_rows, n_cands = _run_grid_arms(market, filter_on=state, log=log)
        rows.extend(state_rows)
        counts[state] = n_cands
    if True in counts and False not in counts:
        # 기준점 팔만 따로 — 필터 끔 × entry_r × 1.5R = 오늘의 채택 기본값 그대로.
        t0 = time.time()
        ref_cands, ref_fill = build_arm_candidates(
            market, tp_rule=TP_ENTRY, r_multiple=R_DEFAULT, filter_on=False
        )
        rows.extend(
            build_rows_for_arm(
                market,
                ref_cands,
                ref_fill,
                tp_rule=TP_ENTRY,
                r_multiple=R_DEFAULT,
                filter_on=False,
                guards=(GUARD_ON,),
            )
        )
        counts[False] = len(ref_cands)
        if log:
            print(
                f"[wan155] {market.symbol} {market.timeframe} 기준점(필터 끔): "
                f"후보 {len(ref_cands)} ({time.time() - t0:.0f}s)",
                flush=True,
            )
    if True in counts and False in counts and counts[True] > counts[False]:
        raise AssertionError(
            f"필터 팔 후보({counts[True]}) > 필터 끔 후보({counts[False]}) — "
            "존폭 필터가 후보를 늘릴 수는 없다. 필터 배선 버그다."
        )
    return rows


def run_report(
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = ("1h",),
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    filter_states: tuple[bool, ...] = (True,),
    log: bool = True,
) -> list[RulerRow]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    rows: list[RulerRow] = []
    for timeframe in timeframes:
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=True
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan155] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            rows.extend(run_cell(market, filter_states=filter_states, log=log))
    return rows


def merge_rows(existing: Sequence[RulerRow], new: Sequence[RulerRow]) -> list[RulerRow]:
    """좌표(심볼·TF·구간·자·배수·가드·필터)가 같은 행은 새 행이 이긴다 — `--append`의 병합.

    ⚠️ TF 단위 keep(wan143 방식)이 아니다 — 이 모듈은 같은 TF를 필터 상태별로 **부분
    재실행**할 수 있어서, TF 단위로 지우면 기존 필터 켬 행이 필터 끔 재실행에 통째로
    사라진다(조용한 데이터 손실).
    """

    def key(r: RulerRow) -> tuple[str, str, str, str, float, float, bool]:
        return (r.symbol, r.timeframe, r.segment, r.tp_rule, r.r_multiple, r.guard, r.filter_on)

    new_keys = {key(r) for r in new}
    return [r for r in existing if key(r) not in new_keys] + list(new)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def _subset(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    tp_rule: str,
    r_multiple: float,
    guard: float = GUARD_ON,
    *,
    filter_on: bool = True,
) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["tp_rule"] == tp_rule)
        & (frame["r_multiple"] == r_multiple)
        & (frame["guard"] == guard)
        & (frame["filter_on"] == filter_on)
    ]


def pooled(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    tp_rule: str,
    r_multiple: float,
    guard: float = GUARD_ON,
    *,
    filter_on: bool = True,
) -> dict[str, float | None]:
    """심볼평균 — **거래 20건 미만 셀은 제외**하고 제외 수를 병기한다(WAN-84 게이트).

    수익률·MDD 등은 유효 심볼 단순평균, 거래·청산 사유는 유효 심볼 합이다.
    """
    sub = _subset(frame, timeframe, segment, tp_rule, r_multiple, guard, filter_on=filter_on)
    if sub.empty:
        return {}
    valid = sub[sub["num_trades"] >= MIN_TRADES_PER_SYMBOL]
    excluded = sub[sub["num_trades"] < MIN_TRADES_PER_SYMBOL]
    if valid.empty:
        return {
            "n_symbols": 0.0,
            "n_excluded": float(len(excluded)),
            "excluded_symbols": None,
        }

    def avg(col: str) -> float | None:
        vals = valid[col].astype(float).dropna()
        return float(vals.mean()) if len(vals) else None

    ret, mdd = avg("total_return"), avg("max_drawdown")
    tp = int(valid["n_take_profit"].sum())
    sl = int(valid["n_stop_loss"].sum())
    eod = int(valid["n_end_of_data"].sum())
    closed = tp + sl + eod
    return {
        "n_symbols": float(len(valid)),
        "n_excluded": float(len(excluded)),
        "total_return": ret,
        "max_drawdown": mdd,
        "ret_over_mdd": (ret / mdd) if (ret is not None and mdd) else None,
        "win_rate": avg("win_rate"),
        "mean_net_r": avg("mean_net_r"),
        "net_r_win": avg("net_r_win"),
        "net_r_loss": avg("net_r_loss"),
        "cost_r_median": avg("cost_r_median"),
        "breakeven_win_rate": avg("breakeven_win_rate"),
        "win_rate_margin": avg("win_rate_margin"),
        "num_trades": float(valid["num_trades"].sum()),
        "num_candidates": float(valid["num_candidates"].sum()),
        "n_take_profit": float(tp),
        "n_stop_loss": float(sl),
        "n_end_of_data": float(eod),
        "stop_rate": (sl / closed) if closed else None,
        "n_positive": float((valid["total_return"].astype(float) > 0).sum()),
        "tp_dist_frac_median": avg("tp_dist_frac_median"),
        "height_over_stop_med": avg("height_over_stop_med"),
        "height_over_stop_p25": avg("height_over_stop_p25"),
        "height_over_stop_p75": avg("height_over_stop_p75"),
        "guard_reject_rate": avg("guard_reject_rate"),
    }


def excluded_cells(frame: pd.DataFrame, timeframe: str) -> list[str]:
    """유효 표본(20거래) 미달로 심볼평균에서 빠진 (팔, 구간, 심볼) 목록."""
    sub = frame[(frame["timeframe"] == timeframe) & (frame["guard"] == GUARD_ON)]
    out: list[str] = []
    for _, row in sub[sub["num_trades"] < MIN_TRADES_PER_SYMBOL].iterrows():
        label = arm_label(
            str(row["tp_rule"]), float(row["r_multiple"]), filter_on=bool(row["filter_on"])
        )
        out.append(
            f"`{label}` {row['segment']} {_bare(str(row['symbol']))}({int(row['num_trades'])}거래)"
        )
    return out


def leave_one_out(
    frame: pd.DataFrame,
    timeframe: str,
    tp_rule: str,
    r_multiple: float,
    segment: str = SEGMENT_OOS,
    *,
    filter_on: bool = True,
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인(이슈 필수 축).

    게이트(20거래)를 통과한 유효 심볼 안에서만 뺀다 — 제외 셀은 애초에 평균에 없다.
    """
    sub = _subset(frame, timeframe, segment, tp_rule, r_multiple, filter_on=filter_on)
    sub = sub[sub["num_trades"] >= MIN_TRADES_PER_SYMBOL]
    if sub.empty:
        return "—"
    parts: list[str] = []
    for _, drop in sub.iterrows():
        rest = sub[sub["symbol"] != drop["symbol"]]["total_return"].astype(float)
        if len(rest):
            parts.append(f"−{_bare(str(drop['symbol']))} {rest.mean() * 100:+.2f}%")
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


class VerdictKind(StrEnum):
    ZONE = "zone"  # (a) 존높이 자가 이긴다
    ENTRY = "entry"  # (b) 현행 자가 이긴다
    MIXED = "mixed"  # 지표가 갈린다
    INDETERMINATE = "indeterminate"  # 표본 게이트 미달


class TfVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: VerdictKind
    demoted: bool
    """두 자의 거래 수 차이 5% 초과 — 판정 자동 강등(이슈 §4)."""
    text: str


def trade_gap(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str = SEGMENT_OOS,
    *,
    filter_on: bool = True,
) -> float | None:
    """같은 배수(1.5R)에서 두 자의 시퀀싱 거래 수 상대 차이(존높이 − 현행)/현행."""
    entry = pooled(frame, timeframe, segment, TP_ENTRY, R_DEFAULT, filter_on=filter_on)
    zone = pooled(frame, timeframe, segment, TP_ZONE, R_DEFAULT, filter_on=filter_on)
    e, z = entry.get("num_trades"), zone.get("num_trades")
    if not e or z is None:
        return None
    return (z - e) / e


def tf_verdict(frame: pd.DataFrame, timeframe: str, *, filter_on: bool = True) -> TfVerdict:
    """축 1(자) 판정 — 현행 배수(1.5R) · 가드 켬 · OOS 심볼평균.

    `filter_on=False`는 필터 끔 격자의 **참고 판정**이다(고정 조건이 필터 켬이므로 공식
    판정은 켬 축이다) — WAN-143의 원 질문(필터 없는 엔진에서 존높이 익절)을 오늘 엔진에서
    다시 재는 축.

    `total_return`만으로 내지 않는다(이슈 §3 — WAN-137의 「raw만 승, 위험조정하면 증발」
    전례): 수익 · 수익/MDD · `mean_net_r` 세 지표가 **모두 같은 방향**일 때만 (a)/(b)이고,
    갈리면 (c)다. 표본 게이트(유효 심볼 3개 미만)면 판정하지 않고, 두 자의 거래 수 차이가
    5%를 넘으면 판정을 강등한다(슬롯 잠금이 표본을 갈라놓았다는 뜻이라 순수한 규칙 비교가
    아니다).
    """
    entry = pooled(frame, timeframe, SEGMENT_OOS, TP_ENTRY, R_DEFAULT, filter_on=filter_on)
    zone = pooled(frame, timeframe, SEGMENT_OOS, TP_ZONE, R_DEFAULT, filter_on=filter_on)
    label = timeframe if filter_on else f"{timeframe} (필터 끔)"
    n_valid = min(entry.get("n_symbols") or 0.0, zone.get("n_symbols") or 0.0)
    if n_valid < MIN_SYMBOLS_FOR_VERDICT:
        return TfVerdict(
            kind=VerdictKind.INDETERMINATE,
            demoted=False,
            text=(
                f"**{label}**: ⚠️ **판정 불가** — 유효 심볼(거래 {MIN_TRADES_PER_SYMBOL}건 "
                f"이상)이 {n_valid:.0f}개로 {MIN_SYMBOLS_FOR_VERDICT}개 미만이다. 존폭 필터가 "
                "거래를 3분의 1로 줄인 표본 위라 게이트가 빡빡하게 걸린다(PM 예고 그대로)."
            ),
        )
    gap = trade_gap(frame, timeframe, filter_on=filter_on)
    demoted = gap is not None and abs(gap) > TRADE_GAP_DEMOTE
    deltas = {
        name: (zone.get(name), entry.get(name))
        for name in ("total_return", "ret_over_mdd", "mean_net_r")
    }
    directions = {
        name: (z > e) for name, (z, e) in deltas.items() if z is not None and e is not None
    }
    ret_z, ret_e = deltas["total_return"]
    ra_z, ra_e = deltas["ret_over_mdd"]
    nr_z, nr_e = deltas["mean_net_r"]
    numbers = (
        f"OOS 심볼평균({n_valid:.0f}심볼) total_return "
        f"{_fmt_pct(ret_e)} → {_fmt_pct(ret_z)} · 수익/MDD "
        f"{_fmt_num(ra_e)} → {_fmt_num(ra_z)} · mean_net_r "
        f"{_fmt_num(nr_e, '.3f')} → {_fmt_num(nr_z, '.3f')}"
    )
    gap_txt = "" if gap is None else f" 거래 수 차이 {gap * 100:+.1f}%."
    demote_txt = (
        f" 🚨 **판정 강등** — 두 자의 거래 수 차이가 {TRADE_GAP_DEMOTE:.0%}를 넘는다"
        "(존높이 팔의 슬롯 잠금이 표본을 갈라놓았다). 방향 참고까지만."
        if demoted
        else ""
    )
    if len(directions) < 3:
        kind, head = VerdictKind.MIXED, "(c) **판정 지표 결손** — 일부 지표를 못 냈다"
    elif all(directions.values()):
        kind, head = VerdictKind.ZONE, "(a) **존높이 자가 이긴다** — 세 지표 전부"
    elif not any(directions.values()):
        kind, head = VerdictKind.ENTRY, "(b) **현행 자가 이긴다** — 세 지표 전부"
    else:
        won = [k for k, v in directions.items() if v]
        kind, head = (
            VerdictKind.MIXED,
            f"(c) **지표가 갈린다** — 존높이 우위는 {', '.join(f'`{w}`' for w in won)}뿐",
        )
    return TfVerdict(
        kind=kind, demoted=demoted, text=f"**{label}**: {head}. {numbers}.{gap_txt}{demote_txt}"
    )


def best_r(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    tp_rule: str,
    *,
    filter_on: bool = True,
) -> tuple[float | None, str]:
    """축 2(배수) — 그 (자, 구간)의 최적 배수(심볼평균 total_return)와 전체 곡선."""
    values: list[tuple[float, float]] = []
    for r_multiple in R_MULTIPLES:
        cell = pooled(frame, timeframe, segment, tp_rule, r_multiple, filter_on=filter_on)
        ret = cell.get("total_return") if cell else None
        if ret is not None:
            values.append((r_multiple, ret))
    if not values:
        return None, "—"
    top = max(values, key=lambda kv: kv[1])
    body = " · ".join(f"{r:.1f}R {v * 100:+.2f}%" for r, v in values)
    return top[0], f"최적 {top[0]:.1f}R ({top[1] * 100:+.2f}%) — {body}"


def lever_comparison(frame: pd.DataFrame, timeframe: str) -> str:
    """두 지렛대(자 교체 vs 배수 인상)를 같은 문장에서 — 이슈 필수 판정 축.

    자 교체는 목표를 **셋업마다 다른 비율**로 밀고(존높이/현행 1R의 산포가 그 계량),
    배수 인상은 **모든 셋업에 똑같이**민다. OOS에서 각 지렛대의 최선을 나란히 놓는다.
    """
    zone_15 = pooled(frame, timeframe, SEGMENT_OOS, TP_ZONE, R_DEFAULT)
    best_entry_r, entry_curve = best_r(frame, timeframe, SEGMENT_OOS, TP_ENTRY)
    if not zone_15 or best_entry_r is None:
        return "—"
    entry_best = pooled(frame, timeframe, SEGMENT_OOS, TP_ENTRY, best_entry_r)
    hos_med = zone_15.get("height_over_stop_med")
    hos_p25 = zone_15.get("height_over_stop_p25")
    hos_p75 = zone_15.get("height_over_stop_p75")
    spread = (
        f"존높이/현행 1R 중앙값 {_fmt_num(hos_med)} (IQR {_fmt_num(hos_p25)}~{_fmt_num(hos_p75)})"
        if hos_med is not None
        else "존높이/현행 1R 산포 — 계측 불가"
    )
    return (
        f"자 교체(`zone_height` 1.5R): {_fmt_pct(zone_15.get('total_return'))} · 수익/MDD "
        f"{_fmt_num(zone_15.get('ret_over_mdd'))} vs 배수 인상(`entry_r`, OOS 최선 "
        f"{best_entry_r:.1f}R): {_fmt_pct(entry_best.get('total_return'))} · 수익/MDD "
        f"{_fmt_num(entry_best.get('ret_over_mdd'))}. {spread} — 비율이 1에 몰려 있으면 두 "
        f"지렛대는 같은 것을 사는 것이고, 흩어져 있으면 자 교체는 셋업 선별적 지렛대다. "
        f"(entry_r 곡선: {entry_curve})"
    )


def absolute_target_note(frame: pd.DataFrame, timeframe: str) -> str:
    """⚠️ 필수 문장 — 좁은 존만 매매하면 존 높이도 작다. 존높이 자는 *현행 대비*로는 목표를
    밀지만 **오늘의 채택 기본값(필터 끔 · entry_r 1.5R)보다 절대적으로 가까울 수 있다**."""
    zone = pooled(frame, timeframe, SEGMENT_OOS, TP_ZONE, R_DEFAULT)
    ref = pooled(frame, timeframe, SEGMENT_OOS, TP_ENTRY, R_DEFAULT, filter_on=False)
    entry = pooled(frame, timeframe, SEGMENT_OOS, TP_ENTRY, R_DEFAULT)
    z, r, e = (
        zone.get("tp_dist_frac_median"),
        ref.get("tp_dist_frac_median"),
        entry.get("tp_dist_frac_median"),
    )
    if z is None or r is None or e is None:
        return "목표 거리 계측 불가(표본 부족)."
    rel = "멀다" if z > r else "**가깝다**"
    return (
        f"익절 목표 거리 중앙값(÷진입가, OOS): 필터 안 `entry_r` {e * 100:.3f}% → "
        f"`zone_height` {z * 100:.3f}%(현행 대비 {z / e:.2f}배로 민다) vs **오늘의 채택 "
        f"기본값(필터 끔) {r * 100:.3f}%**. 즉 존높이 자의 절대 목표는 지금 기본값보다 "
        f"{rel}({z / r:.2f}배) — 「익절 구간을 더 높게」라는 동기가 절대 거리 기준으로 "
        f"{'충족된다' if z > r else '충족되지 않는다(좁은 존의 존 높이가 그만큼 작다)'}."
    )


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.2f}%"


def _fmt_num(v: float | None, fmt: str = ".2f") -> str:
    return "—" if v is None else format(v, fmt)


_GRID_COLS = (
    "segment",
    "arm",
    "return%",
    "mdd%",
    "ret/mdd",
    "win%",
    "netR",
    "cost_r",
    "손익분기",
    "여유",
    "trades",
    "cands",
    "stop%",
    "tp거리%",
    "+심볼(제외)",
)


def _grid_table(frame: pd.DataFrame, timeframe: str, *, guard: float = GUARD_ON) -> str:
    lines = [
        "| " + " | ".join(_GRID_COLS) + " |",
        "| " + " | ".join("--" for _ in _GRID_COLS) + " |",
    ]
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        # 필터 끔 팔은 있는 만큼만 나온다 — 기준점 하나뿐이면 그 행만, 끔 격자를 돌렸으면
        # 8팔 전부(pooled가 빈 조합을 건너뛴다).
        arm_axis: list[tuple[str, float, bool]] = [
            (rule, r, state) for state in (True, False) for r in R_MULTIPLES for rule in TP_RULES
        ]
        for rule, r_multiple, filter_on in arm_axis:
            c = pooled(frame, timeframe, segment, rule, r_multiple, guard, filter_on=filter_on)
            if not c:
                continue
            n_sym, n_exc = c.get("n_symbols"), c.get("n_excluded")
            if not n_sym:
                sym_txt = f"0({n_exc:.0f} 제외)" if n_exc else "0"
                lines.append(
                    f"| {segment} | {arm_label(rule, r_multiple, filter_on=filter_on)} | "
                    + " | ".join("—" for _ in range(len(_GRID_COLS) - 3))
                    + f" | {sym_txt} |"
                )
                continue
            stop_rate = c.get("stop_rate")
            tp_dist = c.get("tp_dist_frac_median")
            n_pos = c.get("n_positive")
            lines.append(
                "| "
                + " | ".join(
                    [
                        segment,
                        arm_label(rule, r_multiple, filter_on=filter_on),
                        _fmt_pct(c.get("total_return")),
                        _fmt_pct(c.get("max_drawdown")),
                        _fmt_num(c.get("ret_over_mdd")),
                        _fmt_pct(c.get("win_rate")),
                        _fmt_num(c.get("mean_net_r"), ".3f"),
                        _fmt_num(c.get("cost_r_median"), ".3f"),
                        _fmt_pct(c.get("breakeven_win_rate")),
                        _fmt_pct(c.get("win_rate_margin")),
                        _fmt_num(c.get("num_trades"), ".0f"),
                        _fmt_num(c.get("num_candidates"), ".0f"),
                        "—" if stop_rate is None else f"{stop_rate * 100:.1f}",
                        "—" if tp_dist is None else f"{tp_dist * 100:.3f}",
                        f"{0 if n_pos is None else int(n_pos)}/{int(n_sym)}"
                        + (f"({int(n_exc)} 제외)" if n_exc else ""),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def _symbol_table(frame: pd.DataFrame, timeframe: str, segment: str = SEGMENT_OOS) -> str:
    sub = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["guard"] == GUARD_ON)
        & (frame["r_multiple"] == R_DEFAULT)
    ].copy()
    if sub.empty:
        return "(없음)"
    headers = ["symbol", "arm", "return%", "mdd%", "win%", "netR", "trades", "TP", "SL", "유효"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    sub["arm"] = [
        arm_label(str(r), float(m), filter_on=bool(f))
        for r, m, f in zip(sub["tp_rule"], sub["r_multiple"], sub["filter_on"], strict=True)
    ]
    for _, r in sub.sort_values(["symbol", "arm"]).iterrows():
        ok = "✅" if int(r["num_trades"]) >= MIN_TRADES_PER_SYMBOL else "🚨 미달"
        mean_net_r = r["mean_net_r"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _bare(str(r["symbol"])),
                    str(r["arm"]),
                    _fmt_pct(float(r["total_return"])),
                    _fmt_pct(float(r["max_drawdown"])),
                    _fmt_pct(float(r["win_rate"])),
                    _fmt_num(float(mean_net_r) if pd.notna(mean_net_r) else None, ".3f"),
                    str(int(r["num_trades"])),
                    str(int(r["n_take_profit"])),
                    str(int(r["n_stop_loss"])),
                    ok,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def overall_verdict(frame: pd.DataFrame, timeframes: Sequence[str]) -> str:
    """(a)/(b)/(c) 종합 — 판정 대상은 「존높이 자를 채택할 근거가 있는가」다.

    (b)의 기준은 「존높이가 어디서도 **이기지 못한다**」이지 「모든 TF에서 세 지표 전부
    진다」가 아니다 — MIXED(지표가 갈림)는 우위가 아니므로 채택 근거가 못 되고, 그런 TF를
    (c)로 세면 "어디서도 안 이기는" 표가 「TF에 갈린다」로 둔갑한다(부호 함정의 판정판 —
    WAN-115/120이 겪은 부류).
    """
    verdicts = {tf: tf_verdict(frame, tf) for tf in timeframes}
    kinds = {v.kind for v in verdicts.values()}
    known = {k for k in kinds if k is not VerdictKind.INDETERMINATE}
    if not known:
        head = "⚠️ **판정 불가** — 표본 게이트 미달"
    elif known == {VerdictKind.ZONE}:
        head = "**(a) 존높이 자 채택 권고 후보** — 작업 TF 전부 세 지표에서 이긴다"
    elif VerdictKind.ZONE in known:
        head = "**(c) TF에 갈린다** — 하나의 기본값으로 둘 다 좋게 할 수 없다(WAN-143/108 자리)"
    elif VerdictKind.ENTRY in known:
        head = (
            "**(b) 현행 유지** — 존높이 자가 어느 TF에서도 이기지 못한다"
            "(세 지표 전부 지거나, 지표가 갈려 우위가 아니다)"
        )
    else:
        head = "**(c) 지표가 갈린다** — 어느 TF에서도 세 지표가 한 방향이 아니다(0 언저리)"
    if VerdictKind.INDETERMINATE in kinds and known:
        head += " · ⚠️ 일부 TF는 표본 게이트로 판정 불가"
    if any(v.demoted for v in verdicts.values()):
        head += " · 🚨 일부 TF는 거래 수 차이 5% 초과로 **강등된 판정**이다"
    return head


def build_summary_markdown(rows: Sequence[RulerRow], *, timeframes: Sequence[str]) -> str:
    frame = rows_to_frame(rows)
    symbols = sorted({_bare(r.symbol) for r in rows}) or ["—"]
    lines: list[str] = [
        "# WAN-155: 좁은 존 고정 + 익절 「자 vs 배수」 8조합 격자",
        "",
        "재현: `uv run python -m backtest.wan155_tp_ruler_vs_multiple --tf 1h` → "
        "`--tf 15m --append` (요약만: `--from-csv`)",
        "",
        f"{len(symbols)}심볼({'/'.join(symbols)}) × {'·'.join(timeframes)} × IS/OOS, 못 박은 창 "
        f"**{DEFAULT_START} ~ {DEFAULT_END}**, 공식 렌즈 **`baseline` 단독**(WAN-128). "
        "**고정 조건 = 존폭 필터 켬**(`max_zone_width_atr="
        f"{FILTER_THRESHOLD}` — **ATR 배수 단위**, 사용자 결정 2026-07-21. 기본값 전환이 "
        "아니라 이 모듈의 명시 입력이다) × 오늘의 채택 기본값(오프셋 2bp · `intrabar_live` · "
        "`unconditional` · 롱 온리 · 분리 존). 가드(0.3%)는 주 격자에서 **켬 고정**이다.",
        "",
        "축 1 = 1R의 자: `entry_r`(현행: 진입가→무효화 경계) vs `zone_height`(존 위−아래 "
        "경계, 손절은 두 자 모두 무효화 경계 그대로). 축 2 = 배수: "
        f"{' · '.join(f'{r:g}' for r in R_MULTIPLES)}. `reference` = 필터 끔 × `entry_r` × "
        "1.5R = 오늘의 채택 기본값 그대로(절대 목표 거리 대조용).",
        "",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 손익은 **상한**이다. WAN-154 §4에서 "
        "존높이류 축은 15m 관통 요구(`pen_5bp`)에 유의성을 잃었다(이 표는 관통 축을 안 쟀다).",
        "> ⚠️ **WAN-143 표와 셀 직접 비교 금지** — 그쪽은 존 병합이 켜져 있던 엔진(`tap` 밴드 "
        "아님, `LEGACY_OB_PARAMS` 핀)이고 필터도 없었다. 이 표가 그 판정을 대체한다.",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124/151)은 불변 — 익절 구조는 알파가 아니라 "
        "**위험의 모양**만 바꾼다(WAN-90).",
        '> ⚠️ **기본값·토대 불변**(`take_profit_r=1.5` · `take_profit_mode="fixed_r"` · '
        "`min_stop_distance_fraction=0.003` · 필터 기본 꺼짐 그대로, "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지). **채택은 별도 재-베이스라인 결정 이슈이자 "
        "사용자 결정이다.**",
        "",
        "## 종합 판정",
        "",
        overall_verdict(frame, timeframes),
        "",
    ]
    for timeframe in timeframes:
        v = tf_verdict(frame, timeframe)
        gap_is = trade_gap(frame, timeframe, SEGMENT_IS)
        lines += [
            f"## {timeframe}",
            "",
            f"**축 1(자) 판정**: {v.text}",
            "",
            f"**두 지렛대 비교(OOS)**: {lever_comparison(frame, timeframe)}",
            "",
            f"**절대 목표 거리**: {absolute_target_note(frame, timeframe)}",
            "",
            "**축 2(배수) — IS 최적 vs OOS 최적**:",
            "",
        ]
        for rule in TP_RULES:
            is_best, is_curve = best_r(frame, timeframe, SEGMENT_IS, rule)
            oos_best, oos_curve = best_r(frame, timeframe, SEGMENT_OOS, rule)
            flip = (
                ""
                if is_best is None or oos_best is None
                else (
                    " ⚠️ **IS→OOS 뒤집힘**(과최적화 서명, WAN-90/143 재현)"
                    if is_best != oos_best
                    else " (IS·OOS 일치)"
                )
            )
            lines += [
                f"- `{rule}` IS: {is_curve}",
                f"- `{rule}` OOS: {oos_curve}{flip}",
            ]
        lines += [
            "",
            f"**IS 거래 수 차이(존높이 − 현행, 1.5R)**: "
            f"{'—' if gap_is is None else f'{gap_is * 100:+.1f}%'}",
            "",
            "**Leave-one-out(OOS)**:",
            "",
            f"- `zone_height 1.5R`: {leave_one_out(frame, timeframe, TP_ZONE, R_DEFAULT)}",
            f"- `entry_r 1.5R`: {leave_one_out(frame, timeframe, TP_ENTRY, R_DEFAULT)}",
            "",
            "### 8조합 + 기준점 × IS/OOS (심볼평균 — 거래 20건 미만 셀 제외)",
            "",
            _grid_table(frame, timeframe),
            "",
            "### 가드 끔(부차 축 — ⚠️ 「가드를 풀자」는 이 표의 결론이 아니다, WAN-79/143)",
            "",
            _grid_table(frame, timeframe, guard=GUARD_OFF),
            "",
            "### 심볼별 (OOS · 1.5R)",
            "",
            _symbol_table(frame, timeframe),
            "",
        ]
        cells = excluded_cells(frame, timeframe)
        lines += [
            "**유효 표본(20거래) 미달 셀:** " + (" · ".join(cells) if cells else "없음"),
            "",
        ]
        has_off_grid = not _subset(
            frame, timeframe, SEGMENT_OOS, TP_ZONE, R_DEFAULT, filter_on=False
        ).empty
        if has_off_grid:
            v_off = tf_verdict(frame, timeframe, filter_on=False)
            lines += [
                "### 필터 끔 격자 (참고 — WAN-143의 원 질문을 오늘 엔진에서)",
                "",
                "공식 판정은 위 필터 켬 축이다(고정 조건). 이 절은 **참고**다 — 필터가 없으면 "
                "넓은 존이 남아 볼린저가 진입가를 존 안으로 끌 수 있으므로, 존높이 자의 "
                "지렛대(존높이 > 현행 1R)가 실제로 작동하는 축은 이쪽이다. 행 자체는 위 두 "
                "격자 표의 `(필터 끔)` 팔에 있다.",
                "",
                f"**축 1(자) 판정(참고)**: {v_off.text}",
                "",
                "**축 2(배수) — 필터 끔**:",
                "",
            ]
            for rule in TP_RULES:
                _, is_curve = best_r(frame, timeframe, SEGMENT_IS, rule, filter_on=False)
                _, oos_curve = best_r(frame, timeframe, SEGMENT_OOS, rule, filter_on=False)
                lines += [
                    f"- `{rule}` IS: {is_curve}",
                    f"- `{rule}` OOS: {oos_curve}",
                ]
            lines += [
                "",
                "**Leave-one-out(OOS · 필터 끔)**:",
                "",
                "- `zone_height 1.5R`: "
                f"{leave_one_out(frame, timeframe, TP_ZONE, R_DEFAULT, filter_on=False)}",
                "- `entry_r 1.5R`: "
                f"{leave_one_out(frame, timeframe, TP_ENTRY, R_DEFAULT, filter_on=False)}",
                "",
            ]
    lines += [
        "## 후보 집합 검산",
        "",
        "익절 자·배수는 청산만 바꾸고 존폭 필터는 자·배수를 읽지 않으므로, **필터 켠 8팔의 "
        "체결 셋업 집합은 비트 단위로 같다** — 격자가 진입 시각 집합을 대조해 어긋나면 "
        "`AssertionError`로 멈춘다. 표의 `trades`(시퀀싱 후)가 팔마다 다른 것은 **관찰**이다: "
        "목표가 멀수록 동시 1포지션 슬롯이 더 오래 잠긴다.",
        "",
        "📌 두 자의 `mean_net_r`은 직접 비교 가능하다 — 손절(무효화 경계)이 두 자에서 같아 "
        "1R(리스크 금액)이 셋업 단위로 동일하다. 손익분기 승률은 고정 1.5R 식이 아니라 실현 "
        "승/패 R 분포에서 냈다(배수 스윕·존높이 자는 고정 R 전제가 깨진다).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[RulerRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[RulerRow]:
    frame = pd.read_csv(path)
    return [RulerRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default="1h", help="콤마로 여러 개(예: 1h,15m)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--out-csv", type=str, default=str(REPORTS_DIR / "wan155_tp_ruler_vs_multiple.csv")
    )
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan155_tp_ruler_vs_multiple_summary.md")
    )
    parser.add_argument(
        "--append", action="store_true", help="기존 CSV에 병합(좌표가 같은 행은 새 행이 이긴다)"
    )
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument(
        "--filter",
        choices=("on", "off", "both"),
        default="on",
        help="존폭 필터 격자 상태 — on(8팔 + 기준점, 기본) · off(필터 끔 8팔) · both",
    )
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan155] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        filter_states = {"on": (True,), "off": (False,), "both": (True, False)}[args.filter]
        rows = run_report(
            symbols, timeframes, start=args.start, end=args.end, filter_states=filter_states
        )
        if args.append and out_csv.exists():
            rows = merge_rows(rows_from_csv(out_csv), rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    timeframes_seen = list(dict.fromkeys(r.timeframe for r in rows))
    Path(args.out_md).write_text(
        build_summary_markdown(rows, timeframes=timeframes_seen), encoding="utf-8"
    )
    print(f"[wan155] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
