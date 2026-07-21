"""WAN-161: 존폭 문턱 × 익절 배수 16조합 — 최적 배수가 문턱을 따라 움직이는가.

## 질문 (사용자 원문 2026-07-21)

PM이 15분봉 손익비별 수익률 표를 보고하자 사용자가 되물었다:

> "근데 이건 1.28 기준이지? 이게 바뀌면 새로 돌려야돼?"

**맞다 — 그리고 그 답을 낼 표가 없었다.** WAN-155가 배수 스윕(1.0·1.5·2.0·3.0)을 돌렸지만
존폭 문턱은 **`1.28` 한 점에 고정**돼 있었다(필터 끔 팔은 24행 전부 1.5R 한 점).

## 🚨 이 표가 답하지 **않는** 것

**「어느 문턱·어느 배수가 제일 돈을 잘 버나」가 아니다.** 그건 뒷구간 성적을 보고 값을
고르는 것이고 이 저장소가 반복해 경계해 온 자리다(WAN-90/143/155가 같은 이유로 OOS 최적을
채택 근거에서 뺐다).

**묻는 것은 「민감도」 하나다**: 앞구간(IS)에서 고른 최적 배수가 **문턱을 바꿔도 같은
값인가**.

* **같으면** (b) — 배수는 문턱과 **독립**이고, 문턱을 만져도 손익비 표를 다시 안 돌려도 된다.
* **다르면** (a) — 배수는 **문턱에 종속**이고, 문턱을 건드리는 모든 결정이 손익비 표를
  무효화한다. 그 사실 자체가 기록돼야 다음에 같은 질문에 "안 쟀습니다"를 반복하지 않는다.
* TF에 갈리면 (c).

### 왜 두 축이 물려 있나 (구조적 이유)

**존폭 문턱 → 매매 대상 존의 폭 → 손절 거리(1R) → 고정 배수 익절 목표의 절대 거리.**
문턱을 올리면 넓은 존이 들어오고, 넓은 존은 1R이 길고, 1R이 길면 같은 1.5배라도 목표가 훨씬
멀다 — 목표가 멀어지면 승률이 내려가고 이긴 거래는 커진다. **즉 최적 배수가 이동할 이유가
구조적으로 존재한다.**

## 고정 입력 (오늘의 채택 기본값)

오프셋 2bp(WAN-112) × `intrabar_live` 밴드(WAN-132) × `unconditional` 게이트(WAN-123) ×
롱 온리(WAN-87) × 분리 존(WAN-149) × 못 박은 창 2023-07-14~2026-07-15 × 6심볼 × 렌즈
`baseline` 단독(WAN-128) × 손절폭 가드 `0.003`(WAN-79 현행) × 1R의 자는 **현행
`entry_r`**(진입가→무효화 경계 — 자 축은 WAN-155가 (b)로 닫았다).

🚨 **옛 핀을 하나도 물려받지 않는다** — `LEGACY_COMBINE_OBS`·`LEGACY_BAND_BAR`·
`pin_band_bar`·`LEGACY_OB_PARAMS` 미사용(회귀 테스트가 후보 집합으로 고정, WAN-152/155 패턴).

⚠️ **문턱은 이 모듈의 명시 입력이다** — WAN-159가 `max_zone_width_atr` 기본값을 `1.28`로
옮기더라도 이 표는 **전후 어느 쪽에서 돌려도 같은 행**을 낸다. `harness.build_params`의
`max_zone_width_atr=None`은 규약상 "손대지 않는다"(= 기본값에 맡긴다)이지 "끄라"가
아니므로, **필터 끔 팔은 `None`을 명시적으로 덮어써야** 한다 — 안 그러면 WAN-159 이후
「필터 끔」 라벨이 붙은 채 1.28로 도는 **이중 필터**가 된다(WAN-91/95/112/123 부류의 조용한
실패). `build_arm_params`가 그 덮어쓰기를 하고 회귀 테스트가 동작으로 고정한다.

## 검산 — WAN-155 CSV와 비트 단위로 겹치는 두 셀

* 문턱 `1.28` × 1.5R ≡ `wan155_tp_ruler_vs_multiple.csv`의 `entry_r`·`filter_on=True`·1.5R
* 문턱 없음 × 1.5R ≡ 같은 CSV의 기준점 팔(`filter_on=False`)

⚠️ **그 두 셀 말고는 WAN-155/143/90 표와 셀을 직접 비교하지 말 것** — 격자·엔진이 다르다.

재현:

```
uv run python -m backtest.wan161_threshold_x_tp_multiple --tf 1h            # 1h 먼저(가벼움)
uv run python -m backtest.wan161_threshold_x_tp_multiple --tf 15m --append  # 15m 뒤에 붙임
uv run python -m backtest.wan161_threshold_x_tp_multiple --from-csv         # 요약만 재생성
```
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
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.wan137_resistance_distance import DEFAULT_END, DEFAULT_START, DEFAULT_SYMBOLS
from backtest.wan143_zone_height_tp import apply_guard
from backtest.wan152_selection_vs_geometry import per_trade_records
from backtest.wan155_tp_ruler_vs_multiple import empirical_breakeven
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockResult

REPORTS_DIR = Path("backtest/reports")

THRESHOLDS: tuple[float | None, ...] = (None, 1.15, 1.28, 1.55)
"""축 1 — 존폭 문턱(**ATR 배수** 단위. `0.0128` 같은 분수를 넣으면 거의 전부 걸러진다 —
WAN-158 단위 경고). `None` = 필터 끔 = **넓은 존까지 포함한 전체 매매**.

세 절대값의 출처는 `wan154_threshold_stability.csv`(36행)의 `is_threshold_value`가 종목·TF에
걸쳐 하위 1/4에서 1.07~1.25 · 1/3에서 1.22~1.39 · 1/2에서 1.45~1.75에 몰린 것이다 — 세 값이
그 세 구간의 대표점이다. **분위가 아니라 절대값으로 잡는 이유는 WAN-158 설계 결정과 같다**
(실시간에는 IS 공통 풀이 없어 분위를 그대로 옮기면 실거래 배선에서 막힌다)."""

R_MULTIPLES: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0)
"""축 2 — 익절 배수. WAN-155/90과 같은 네 점이라 곡선 모양을 나란히 읽을 수 있다."""

R_DEFAULT = 1.5
"""현행 채택 배수(WAN-90 검증·유지)."""

GUARD = 0.003
"""WAN-79 채택 가드 — **고정 입력이고 축이 아니다**. 「가드를 풀자」는 이 표의 결론이 아니고
가드 변경은 WAN-76/79 소관(재-베이스라인 = 사용자 결정). 문턱별 **탈락률만** 기록한다."""

MIN_TRADES_PER_SYMBOL = 20
"""WAN-84 유효 기준 — 이 미만인 (심볼, 셀)은 심볼평균에서 **제외**하고 제외 수를 병기한다.
🚨 문턱을 조일수록 표본이 얇아진다(WAN-155 실측: TRX 15m은 1.28에서 이미 18거래)."""

MIN_SYMBOLS_FOR_VERDICT = 3
"""유효 심볼이 이보다 적은 (문턱, TF)는 판정에서 **빠진다**(WAN-142/143/152/155 게이트 관행)."""

MIN_THRESHOLDS_FOR_VERDICT = 2
"""비교할 문턱이 둘은 있어야 「문턱을 바꾸면」이라는 질문 자체가 성립한다."""

PLATEAU_GAP = 0.05
"""「IS 최적 − 현행 1.5R」이 이보다 작으면 판정문이 **고원 위의 argmax**라고 경고한다.

5%p는 이 저장소의 심볼평균 수익률 스케일(구간 수익 10~40%)에서 「몇 %p 차이로 순위가
뒤집혔다」와 「실질적으로 다른 값을 써야 한다」를 가르는 눈금이다. 정확한 경계가 아니라
**argmax만 보고 결론을 내지 말라는 신호**다."""


def threshold_label(threshold: float | None) -> str:
    return "필터 끔" if threshold is None else f"{threshold:.2f}"


def arm_label(threshold: float | None, r_multiple: float) -> str:
    return f"문턱 {threshold_label(threshold)} × {r_multiple:.1f}R"


# --------------------------------------------------------------------------- #
# 파라미터 조립 — 이중 필터 방지
# --------------------------------------------------------------------------- #


def build_arm_params(
    *,
    threshold: float | None,
    r_multiple: float,
    base: ConfluenceParams | None = None,
) -> ConfluenceParams:
    """한 팔의 `ConfluenceParams` — 문턱을 **양방향으로** 명시한다.

    `harness.build_params(max_zone_width_atr=None)`은 규약상 "손대지 않는다"라 `base`(=
    채택 기본값)가 켜 둔 필터를 끄지 않는다. WAN-159가 기본값을 `1.28`로 옮기면 그 규약
    그대로는 **「필터 끔」 팔이 조용히 1.28로 도는 이중 필터**가 되므로, 여기서 `None`을
    명시적으로 덮어쓴다. 반대 방향(기본값이 켜진 뒤 `1.15` 팔이 두 문턱에 걸리는 일)은
    `build_params`가 이미 덮어쓰므로 생기지 않는다.

    반환값의 `max_zone_width_atr`은 **항상 이 함수의 인자와 같다** — 그게 이 모듈이
    WAN-159 전후 어느 쪽에서도 같은 행을 내는 근거다(회귀 테스트가 고정).
    """
    params = harness.build_params(take_profit_r=r_multiple, max_zone_width_atr=threshold, base=base)
    if threshold is None:
        params = params.model_copy(update={"max_zone_width_atr": None})
    if params.max_zone_width_atr != threshold:  # pragma: no cover - 배선 가드
        raise AssertionError(
            f"문턱 배선 실패 — 요청 {threshold!r}인데 조립된 값은 "
            f"{params.max_zone_width_atr!r}다. 「필터 끔」 라벨이 붙은 채 다른 문턱으로 도는 "
            "이중 필터이고, 이 저장소가 네 번 겪은 조용한 실패다(WAN-91/95/112/123)."
        )
    return params


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class ThresholdRow(BaseModel):
    """한 (심볼, TF, 구간, 문턱, 배수) 셀.

    실현 손익비 열은 WAN-154 §1′ 산식(`per_trade_records` — 실현 손익 ÷ 그 거래의 리스크
    금액)을 그대로 재사용한다. 손익분기 승률은 고정 1.5R 식이 아니라 **실현 승/패 R
    분포**에서 낸다(`empirical_breakeven`, WAN-155) — 배수 스윕에서는 고정 R 전제가 깨진다.

    ⚠️ **문턱이 다른 행끼리 `mean_net_r`을 「어느 쪽이 좋은 매매인가」로 읽지 말 것** —
    문턱은 매매 **대상 집합**을 바꾸므로 분모(리스크 금액)의 모집단 자체가 다르다. 같은
    문턱 안에서 배수만 다른 행끼리는 비교 가능하다(손절이 같아 1R이 셋업 단위로 동일).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    threshold: float | None
    r_multiple: float
    guard: float
    num_candidates: int
    """이 구간의 체결 셋업 수(시퀀싱 이전). 같은 (문턱, 구간)이면 배수와 무관하게 같다."""
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
    """후보 중 손절폭 가드(0.3%)에 걸리는 비율 — 문턱을 조일수록 오른다(이슈 함정 검사)."""
    tp_dist_frac_median: float | None
    """익절 목표까지 거리 ÷ 진입가, 후보 중앙값 — 「문턱 → 1R → 목표 거리」 사슬의 계량."""
    stop_frac_median: float | None
    """손절 거리 ÷ 진입가, 후보 중앙값 — 사슬의 가운데 고리(문턱이 1R을 실제로 줄이는가)."""

    @property
    def arm(self) -> str:
        return arm_label(self.threshold, self.r_multiple)


# --------------------------------------------------------------------------- #
# 셀 실행
# --------------------------------------------------------------------------- #


def _stop_frac(cand: _Candidate) -> float:
    return abs(cand.entry_price - cand.stop_price) / cand.entry_price


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def build_arm_candidates(
    market: MarketData,
    *,
    threshold: float | None,
    r_multiple: float,
    base: ConfluenceParams | None = None,
    order_block_result: OrderBlockResult | None = None,
) -> tuple[list[_Candidate], float | None]:
    """한 팔의 후보(전체 창)와 전체 창 체결률.

    ⚠️ **핀을 쓰지 않는다** — 탐지·컨플루언스 전부 오늘의 채택 기본값이고 움직이는 것은
    존폭 문턱과 익절 배수뿐이다(`harness.build_params` 경유 = CLI와 같은 조립 경로).
    `base`·`order_block_result`는 테스트 주입구다(합성 데이터는 채택 기본값에서 후보가
    0개라 검정이 공허해진다 — wan152/155 테스트 관행). 실행 경로는 넘기지 않는다.
    """
    params = build_arm_params(threshold=threshold, r_multiple=r_multiple, base=base)
    cfg = harness.build_config(market.timeframe)
    obr = order_block_result or harness.detect_order_blocks(market)
    candidates, stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=obr,
    )
    return candidates, stats.fill_rate


def is_boundary_ms(market: MarketData) -> int:
    """IS/OOS 경계(상위TF open_time 축의 2/3 지점) — wan152/155 셀과 같은 정의."""
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
    threshold: float | None,
    r_multiple: float,
) -> list[ThresholdRow]:
    """한 팔의 후보를 IS/OOS로 시퀀싱해 행을 만든다(구간별 초기자본 재시작)."""
    boundary = is_boundary_ms(market)
    rows: list[ThresholdRow] = []
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        seg_cands = _segment_candidates(candidates, boundary, segment)
        stop_fracs = [_stop_frac(c) for c in seg_cands]
        tp_dists = [f * r_multiple for f in stop_fracs if f > 0]
        cfg = apply_guard(harness.build_config(market.timeframe), GUARD)
        paired = sequence_with_candidates(seg_cands, cfg, market.funding_rates)
        trades = [t for _, t in paired]
        metrics = build_result_from_trades(trades, cfg, market.timeframe).metrics
        reasons = Counter(cand.reason for cand, _ in paired)
        records = per_trade_records(seg_cands, market, market.timeframe, guard=GUARD)
        wins = [r.net_r for r in records if r.win]
        losses = [r.net_r for r in records if not r.win]
        net_r_win = statistics.fmean(wins) if wins else None
        net_r_loss = statistics.fmean(losses) if losses else None
        breakeven = empirical_breakeven(net_r_win, net_r_loss)
        gains = sum(r.pnl for r in records if r.win)
        loss_amt = sum(-r.pnl for r in records if not r.win)
        cap = [r.cap_hit for r in records if r.cap_hit is not None]
        eff = [r.effective_risk for r in records if r.effective_risk is not None]
        rows.append(
            ThresholdRow(
                symbol=market.symbol,
                timeframe=market.timeframe,
                segment=segment,
                threshold=threshold,
                r_multiple=r_multiple,
                guard=GUARD,
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
                cost_r_median=(statistics.median(r.cost_r for r in records) if records else None),
                breakeven_win_rate=breakeven,
                win_rate_margin=(None if breakeven is None else metrics.win_rate - breakeven),
                profit_factor=(gains / loss_amt) if loss_amt > 0 else None,
                cap_hit_rate=(sum(cap) / len(cap)) if cap else None,
                effective_risk_mean=statistics.fmean(eff) if eff else None,
                guard_reject_rate=(
                    sum(1 for f in stop_fracs if f < GUARD) / len(stop_fracs)
                    if stop_fracs
                    else None
                ),
                tp_dist_frac_median=_median(tp_dists),
                stop_frac_median=_median(stop_fracs),
            )
        )
    return rows


def run_cell(market: MarketData, *, log: bool = True) -> list[ThresholdRow]:
    """한 (심볼, TF)의 문턱 × 배수 16팔 — 두 가지 불변을 **동작으로** 검산한다.

    1. **같은 문턱의 4배수는 체결 셋업 집합이 비트 단위로 같다** — 익절은 청산만 바꾸고
       진입·체결 판정에 안 쓰인다(WAN-137 Phase 2 / WAN-143 / WAN-155와 같은 불변).
       어긋나면 배선 버그다.
    2. **문턱을 조이면 후보는 늘 수 없다** — 필터 끔 ⊇ 1.55 ⊇ 1.28 ⊇ 1.15(포함 관계까지
       확인한다. 개수만 보면 서로 다른 셋업이 같은 개수로 바뀌어도 통과한다).
    """
    rows: list[ThresholdRow] = []
    entry_sets: dict[tuple[float | None, float], list[int]] = {}
    for threshold in THRESHOLDS:
        for r_multiple in R_MULTIPLES:
            t0 = time.time()
            candidates, fill_rate = build_arm_candidates(
                market, threshold=threshold, r_multiple=r_multiple
            )
            entry_sets[(threshold, r_multiple)] = sorted(c.entry_time for c in candidates)
            rows.extend(
                build_rows_for_arm(
                    market, candidates, fill_rate, threshold=threshold, r_multiple=r_multiple
                )
            )
            if log:
                print(
                    f"[wan161] {market.symbol} {market.timeframe} "
                    f"{arm_label(threshold, r_multiple)}: 후보 {len(candidates)} "
                    f"({time.time() - t0:.0f}s)",
                    flush=True,
                )
        base_key = (threshold, R_MULTIPLES[0])
        for r_multiple in R_MULTIPLES:
            if entry_sets[(threshold, r_multiple)] != entry_sets[base_key]:
                raise AssertionError(
                    f"후보 집합 불일치 — {market.symbol} {market.timeframe} "
                    f"{arm_label(threshold, r_multiple)}: 익절 배수가 진입을 바꾸는 배선 버그다."
                )
    check_threshold_nesting(entry_sets, market)
    return rows


def check_threshold_nesting(
    entry_sets: dict[tuple[float | None, float], list[int]], market: MarketData
) -> None:
    """문턱을 조인 팔의 체결 셋업이 느슨한 팔의 **부분집합**인지 — 개수가 아니라 집합으로."""
    ordered = sorted(
        (t for t in THRESHOLDS if t is not None), reverse=True
    )  # 느슨한 것부터: 1.55 → 1.28 → 1.15
    chain: list[float | None] = [None, *ordered]
    for looser, tighter in zip(chain, chain[1:], strict=False):
        wide = set(entry_sets[(looser, R_MULTIPLES[0])])
        narrow = set(entry_sets[(tighter, R_MULTIPLES[0])])
        if not narrow <= wide:
            raise AssertionError(
                f"문턱 포함 관계 위반 — {market.symbol} {market.timeframe}: 문턱 "
                f"{threshold_label(tighter)}의 셋업 {len(narrow - wide)}개가 문턱 "
                f"{threshold_label(looser)}에 없다. 존폭 필터는 후보를 늘릴 수 없다(배선 버그)."
            )


def run_report(
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = ("1h",),
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    log: bool = True,
) -> list[ThresholdRow]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    rows: list[ThresholdRow] = []
    for timeframe in timeframes:
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=True
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan161] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            rows.extend(run_cell(market, log=log))
    return rows


def merge_rows(existing: Sequence[ThresholdRow], new: Sequence[ThresholdRow]) -> list[ThresholdRow]:
    """좌표(심볼·TF·구간·문턱·배수)가 같은 행은 새 행이 이긴다 — `--append`의 병합.

    TF 단위 keep이 아니라 **행 좌표 단위**다(wan155와 같은 이유 — 같은 TF의 부분 재실행이
    기존 행을 통째로 지우면 조용한 데이터 손실이다).
    """

    def key(r: ThresholdRow) -> tuple[str, str, str, float, float]:
        # 문턱 None은 NaN이 아니라 -1.0으로 접는다(NaN != NaN이라 좌표 비교가 깨진다).
        return (
            r.symbol,
            r.timeframe,
            r.segment,
            -1.0 if r.threshold is None else r.threshold,
            r.r_multiple,
        )

    new_keys = {key(r) for r in new}
    return [r for r in existing if key(r) not in new_keys] + list(new)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def _threshold_mask(frame: pd.DataFrame, threshold: float | None) -> pd.Series[bool]:
    col = frame["threshold"]
    return col.isna() if threshold is None else (col == threshold)


def _subset(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    threshold: float | None,
    r_multiple: float,
) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & _threshold_mask(frame, threshold)
        & (frame["r_multiple"] == r_multiple)
    ]


def pooled(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    threshold: float | None,
    r_multiple: float,
) -> dict[str, float | None]:
    """심볼평균 — **거래 20건 미만 셀은 제외**하고 제외 수를 병기한다(WAN-84 게이트).

    수익률·MDD 등은 유효 심볼 단순평균, 거래·청산 사유는 유효 심볼 합이다.
    """
    sub = _subset(frame, timeframe, segment, threshold, r_multiple)
    if sub.empty:
        return {}
    valid = sub[sub["num_trades"] >= MIN_TRADES_PER_SYMBOL]
    excluded = sub[sub["num_trades"] < MIN_TRADES_PER_SYMBOL]
    if valid.empty:
        return {"n_symbols": 0.0, "n_excluded": float(len(excluded))}

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
        "stop_frac_median": avg("stop_frac_median"),
        "guard_reject_rate": avg("guard_reject_rate"),
    }


def excluded_cells(frame: pd.DataFrame, timeframe: str) -> list[str]:
    """유효 표본(20거래) 미달로 심볼평균에서 빠진 (팔, 구간, 심볼) 목록."""
    sub = frame[frame["timeframe"] == timeframe]
    out: list[str] = []
    for _, row in sub[sub["num_trades"] < MIN_TRADES_PER_SYMBOL].iterrows():
        threshold = None if pd.isna(row["threshold"]) else float(row["threshold"])
        label = arm_label(threshold, float(row["r_multiple"]))
        out.append(
            f"`{label}` {row['segment']} {_bare(str(row['symbol']))}({int(row['num_trades'])}거래)"
        )
    return out


def leave_one_out(
    frame: pd.DataFrame,
    timeframe: str,
    threshold: float | None,
    r_multiple: float,
    segment: str = SEGMENT_OOS,
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인(이 저장소는 플러스가 ETH
    하나에서 나오는 일이 반복됐다). 게이트를 통과한 유효 심볼 안에서만 뺀다."""
    sub = _subset(frame, timeframe, segment, threshold, r_multiple)
    sub = sub[sub["num_trades"] >= MIN_TRADES_PER_SYMBOL]
    if sub.empty:
        return "—"
    parts: list[str] = []
    for _, drop in sub.iterrows():
        rest = sub[sub["symbol"] != drop["symbol"]]["total_return"].astype(float)
        if len(rest):
            parts.append(f"−{_bare(str(drop['symbol']))} {rest.mean() * 100:+.2f}%")
    # 유효 심볼이 하나면 뺄 것이 없다 — 빈 문자열을 흘리면 표에 공백이 찍혀 「편중 없음」
    # 처럼 읽힌다.
    return " · ".join(parts) if parts else "— (유효 심볼 1개, 뺄 것이 없다)"


# --------------------------------------------------------------------------- #
# 축 2 — 문턱별 최적 배수
# --------------------------------------------------------------------------- #


class BestR(BaseModel):
    """한 (TF, 구간, 문턱)의 최적 배수 — 표본 게이트를 통과한 점들 안에서만 고른다."""

    model_config = ConfigDict(frozen=True)

    threshold: float | None
    segment: str
    best: float | None
    """유효한 점이 없으면 `None`(= 판정 재료 아님)."""
    n_symbols_min: float
    """곡선 위 유효 점들의 최소 유효 심볼 수 — 게이트 판단에 쓴다."""
    curve: str
    excluded: tuple[float, ...]
    """표본 게이트로 곡선에서 빠진 배수들."""

    @property
    def usable(self) -> bool:
        return self.best is not None and self.n_symbols_min >= MIN_SYMBOLS_FOR_VERDICT


def best_r(frame: pd.DataFrame, timeframe: str, segment: str, threshold: float | None) -> BestR:
    """그 (문턱, 구간)의 최적 배수(심볼평균 `total_return`)와 곡선 전체.

    🚨 **표본 게이트가 곡선 안에서 먼저 돈다** — 유효 심볼 3개 미만인 점은 곡선에서 빼고
    「제외」로 적는다. 안 그러면 한두 심볼만 남은 극단값이 「최적 배수」로 올라와 민감도
    판정을 통째로 흔든다(문턱을 조일수록 그 위험이 커진다는 게 이 이슈의 함정 검사다).
    """
    points: list[tuple[float, float, float]] = []
    excluded: list[float] = []
    for r_multiple in R_MULTIPLES:
        cell = pooled(frame, timeframe, segment, threshold, r_multiple)
        ret = cell.get("total_return") if cell else None
        n_sym = (cell.get("n_symbols") or 0.0) if cell else 0.0
        if ret is None or n_sym < MIN_SYMBOLS_FOR_VERDICT:
            if cell:
                excluded.append(r_multiple)
            continue
        points.append((r_multiple, ret, n_sym))
    if not points:
        return BestR(
            threshold=threshold,
            segment=segment,
            best=None,
            n_symbols_min=0.0,
            curve="⚠️ 판정 불가(표본 게이트)",
            excluded=tuple(excluded),
        )
    top = max(points, key=lambda p: p[1])
    body = " · ".join(f"{r:.1f}R {v * 100:+.2f}%" for r, v, _ in points)
    tail = (
        "" if not excluded else f" · ⚠️ 표본 미달 제외: {', '.join(f'{r:.1f}R' for r in excluded)}"
    )
    return BestR(
        threshold=threshold,
        segment=segment,
        best=top[0],
        n_symbols_min=min(p[2] for p in points),
        curve=f"최적 **{top[0]:.1f}R** ({top[1] * 100:+.2f}%) — {body}{tail}",
        excluded=tuple(excluded),
    )


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


class VerdictKind(StrEnum):
    DEPENDENT = "dependent"  # (a) 최적 배수가 문턱에 종속
    INDEPENDENT = "independent"  # (b) 문턱과 독립
    SPLIT = "split"  # (c) TF에 갈린다
    INDETERMINATE = "indeterminate"  # 표본 게이트 미달


class TfVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: VerdictKind
    is_best: dict[str, float | None]
    """문턱 라벨 → IS 최적 배수(게이트 통과분만)."""
    oos_flips: int
    """IS 최적 배수가 OOS 최적과 어긋난 문턱 수 — **참고**다(채택 근거 아님)."""
    max_gap_vs_default: float | None
    """유효 문턱 중 「IS 최적 − 현행 1.5R」 수익 격차의 최대값.

    🚨 **(a) 판정의 크기를 재는 자다.** argmax는 곡선이 고원이면 몇 %p 차이로도 뒤집히므로
    「문턱마다 다른 값이 뽑힌다」와 「문턱마다 실질적으로 다른 값을 써야 한다」는 같은 말이
    아니다. 이 값이 작으면 (a)는 **argmax의 불안정성**이지 배수를 갈아야 한다는 뜻이 아니다."""
    text: str


def curve_shape(
    frame: pd.DataFrame, timeframe: str, segment: str, threshold: float | None
) -> tuple[float | None, float | None]:
    """(곡선 폭, 최적 − 현행 1.5R) — argmax가 고원 위에 있는지의 계량.

    곡선 폭 = 유효 점들의 max − min. 둘째 값이 0에 가까우면 「최적 배수가 문턱을 따라
    움직인다」는 관찰이 **수익으로는 거의 아무것도 뜻하지 않는다**(현행 1.5R을 그대로 써도
    최적과 그만큼밖에 차이 나지 않는다).
    """
    points: list[tuple[float, float]] = []
    for r_multiple in R_MULTIPLES:
        cell = pooled(frame, timeframe, segment, threshold, r_multiple)
        ret = cell.get("total_return") if cell else None
        n_sym = (cell.get("n_symbols") or 0.0) if cell else 0.0
        if ret is not None and n_sym >= MIN_SYMBOLS_FOR_VERDICT:
            points.append((r_multiple, ret))
    if not points:
        return None, None
    values = [v for _, v in points]
    spread = max(values) - min(values)
    default = next((v for r, v in points if r == R_DEFAULT), None)
    gap = None if default is None else max(values) - default
    return spread, gap


def tf_verdict(frame: pd.DataFrame, timeframe: str) -> TfVerdict:
    """한 TF의 민감도 판정 — **앞구간(IS) 최적 배수가 문턱에 따라 달라지는가** 하나.

    ⚠️ 순서를 어기지 않는다: IS 최적을 먼저 확정하고, OOS는 **그 배수를 그대로 적용한
    성적**으로만 읽는다. OOS 최적 배수는 병기하되 **판정에 쓰지 않는다**(WAN-155 §3가
    15m 2.0R을 같은 이유로 채택 근거에서 뺀 자리).
    """
    is_points = {t: best_r(frame, timeframe, SEGMENT_IS, t) for t in THRESHOLDS}
    usable = {t: b for t, b in is_points.items() if b.usable}
    labels = {threshold_label(t): b.best for t, b in usable.items()}
    oos_points = {t: best_r(frame, timeframe, SEGMENT_OOS, t) for t in THRESHOLDS}
    flips = sum(
        1 for t, b in usable.items() if oos_points[t].usable and oos_points[t].best != b.best
    )
    gaps = [
        g
        for g in (curve_shape(frame, timeframe, SEGMENT_IS, t)[1] for t in usable)
        if g is not None
    ]
    max_gap = max(gaps) if gaps else None
    if len(usable) < MIN_THRESHOLDS_FOR_VERDICT:
        return TfVerdict(
            kind=VerdictKind.INDETERMINATE,
            is_best=labels,
            oos_flips=flips,
            max_gap_vs_default=max_gap,
            text=(
                f"**{timeframe}**: ⚠️ **판정 불가** — 표본 게이트(유효 심볼 "
                f"{MIN_SYMBOLS_FOR_VERDICT}개 · 셀당 거래 {MIN_TRADES_PER_SYMBOL}건)를 "
                f"통과한 문턱이 {len(usable)}개뿐이라 「문턱을 바꾸면」이라는 비교가 "
                "성립하지 않는다."
            ),
        )
    chosen = {b.best for b in usable.values()}
    detail = " · ".join(f"문턱 {threshold_label(t)} → **{b.best:.1f}R**" for t, b in usable.items())
    flip_txt = (
        f" IS 최적이 OOS 최적과 어긋난 문턱 {flips}/{len(usable)}개"
        "(⚠️ **참고일 뿐 — 「뒷구간이 X를 골랐다」를 채택 근거로 쓰지 않는다**)."
    )
    if len(chosen) == 1:
        only = next(iter(chosen))
        kind = VerdictKind.INDEPENDENT
        head = (
            f"(b) **문턱과 독립** — 유효 문턱 {len(usable)}개 전부 IS 최적이 "
            f"**{only:.1f}R**로 같다"
            + ("(현행 채택 배수)" if only == R_DEFAULT else f"(현행 {R_DEFAULT:.1f}R과 다름)")
        )
    else:
        kind = VerdictKind.DEPENDENT
        head = (
            f"(a) **문턱에 종속** — 유효 문턱 {len(usable)}개가 IS 최적으로 "
            f"{len(chosen)}가지 배수를 고른다"
        )
    plateau = "(고원 위의 argmax라 「다른 값을 써야 한다」는 뜻이 아니다)"
    gap_txt = (
        ""
        if max_gap is None
        else (
            f" 🚨 **크기 주의** — 유효 문턱 어디서도 IS 최적이 현행 1.5R을 "
            f"**최대 {max_gap * 100:.2f}%p**밖에 못 이긴다"
            f"{plateau if max_gap < PLATEAU_GAP else ''}."
        )
    )
    return TfVerdict(
        kind=kind,
        is_best=labels,
        oos_flips=flips,
        max_gap_vs_default=max_gap,
        text=f"**{timeframe}**: {head}. {detail}.{flip_txt}{gap_txt}",
    )


def overall_verdict(frame: pd.DataFrame, timeframes: Sequence[str]) -> str:
    """(a)/(b)/(c) 종합 — 「문턱을 만지면 손익비 표를 다시 돌려야 하는가」의 답."""
    verdicts = {tf: tf_verdict(frame, tf) for tf in timeframes}
    known = {v.kind for v in verdicts.values() if v.kind is not VerdictKind.INDETERMINATE}
    if not known:
        head = "⚠️ **판정 불가** — 두 작업 TF 모두 표본 게이트 미달"
    elif known == {VerdictKind.INDEPENDENT}:
        head = (
            "**(b) 최적 배수는 문턱과 독립** — 작업 TF 전부에서 앞구간 최적 배수가 문턱을 "
            "가리지 않는다. 문턱을 만져도 손익비 표를 다시 돌릴 필요가 없다"
        )
    elif known == {VerdictKind.DEPENDENT}:
        head = (
            "**(a) 최적 배수는 문턱에 종속** — 작업 TF 전부에서 문턱마다 다른 배수가 뽑힌다. "
            "**문턱을 건드리는 모든 결정이 손익비 표를 무효화한다**"
        )
    else:
        head = "**(c) TF에 갈린다** — 한쪽 TF에서만 배수가 문턱을 따라 움직인다"
    if any(v.kind is VerdictKind.INDETERMINATE for v in verdicts.values()) and known:
        head += " · ⚠️ 일부 TF는 표본 게이트로 판정 불가"
    return head


# --------------------------------------------------------------------------- #
# WAN-155 검산
# --------------------------------------------------------------------------- #

CROSSCHECK_COLS = ("total_return", "max_drawdown", "win_rate", "num_trades", "num_candidates")
WAN155_CSV = REPORTS_DIR / "wan155_tp_ruler_vs_multiple.csv"

CROSSCHECK_NOISE_ATOL = 1e-9
"""이 이하의 차이는 **부동소수 끝자리**로 읽는다(CSV 왕복의 십진 표기 오차).

🚨 **세 갈래를 다르게 찍는 것이 요점이다**(WAN-151 선례) — 「차이 0」과 「잡음」을 같은
✅로 뭉개면 진짜 불일치가 잡음으로 위장할 여지가 생기고, 반대로 잡음을 🚨로 찍으면
검산이 늘 빨간불이라 아무도 안 본다."""


def classify_diff(worst: float) -> str:
    """검산 결과 세 갈래 — 일치 · 잡음 · 불일치."""
    if worst == 0.0:
        return "✅ 차이 0(비트 일치)"
    if worst <= CROSSCHECK_NOISE_ATOL:
        return f"📌 최대 차이 {worst:.2e} — 부동소수 끝자리(CSV 왕복), 실질 일치"
    return f"🚨 최대 차이 {worst:.3e} — **불일치**"


def crosscheck_wan155(rows: Sequence[ThresholdRow], path: Path = WAN155_CSV) -> str:
    """겹치는 두 셀(문턱 1.28 × 1.5R · 필터 끔 × 1.5R)이 WAN-155 CSV와 같은가.

    같은 엔진·같은 창·같은 자(`entry_r`)라 **비트 단위로 같아야 한다** — 이 이슈의 검산이다.
    WAN-155 CSV가 없으면(옛 체크아웃) 그 사실만 적는다.
    """
    if not path.exists():
        return f"⚠️ 검산 생략 — `{path}`가 없다."
    ref = pd.read_csv(path)
    ref = ref[
        (ref["tp_rule"] == "entry_r") & (ref["r_multiple"] == R_DEFAULT) & (ref["guard"] == GUARD)
    ]
    mine = pd.DataFrame([r.model_dump() for r in rows])
    mine = mine[mine["r_multiple"] == R_DEFAULT]
    lines: list[str] = []
    for threshold, filter_on in ((1.28, True), (None, False)):
        got = mine[_threshold_mask(mine, threshold)]
        want = ref[ref["filter_on"] == filter_on]
        keys = ["symbol", "timeframe", "segment"]
        merged = got.merge(want, on=keys, suffixes=("_new", "_ref"))
        if merged.empty:
            lines.append(f"- 문턱 {threshold_label(threshold)} × 1.5R: ⚠️ 겹치는 행 없음")
            continue
        worst = 0.0
        for col in CROSSCHECK_COLS:
            diff = (merged[f"{col}_new"].astype(float) - merged[f"{col}_ref"].astype(float)).abs()
            worst = max(worst, float(diff.max()))
        mark = classify_diff(worst)
        lines.append(
            f"- 문턱 {threshold_label(threshold)} × 1.5R ≡ WAN-155 "
            f"`{'filter_on' if filter_on else 'reference'}` 팔: {len(merged)}행 × "
            f"{len(CROSSCHECK_COLS)}지표 {mark}"
        )
    return "\n".join(lines)


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
    "손절폭%",
    "tp거리%",
    "가드기각%",
    "+심볼(제외)",
)


def _grid_table(frame: pd.DataFrame, timeframe: str) -> str:
    lines = [
        "| " + " | ".join(_GRID_COLS) + " |",
        "| " + " | ".join("--" for _ in _GRID_COLS) + " |",
    ]
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        for threshold in THRESHOLDS:
            for r_multiple in R_MULTIPLES:
                c = pooled(frame, timeframe, segment, threshold, r_multiple)
                if not c:
                    continue
                n_sym, n_exc = c.get("n_symbols"), c.get("n_excluded")
                label = arm_label(threshold, r_multiple)
                if not n_sym:
                    sym_txt = f"0({n_exc:.0f} 제외)" if n_exc else "0"
                    lines.append(
                        f"| {segment} | {label} | "
                        + " | ".join("—" for _ in range(len(_GRID_COLS) - 3))
                        + f" | {sym_txt} |"
                    )
                    continue
                stop_rate = c.get("stop_rate")
                n_pos = c.get("n_positive")
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            segment,
                            label,
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
                            _pct3(c.get("stop_frac_median")),
                            _pct3(c.get("tp_dist_frac_median")),
                            _pct3(c.get("guard_reject_rate"), digits=1),
                            f"{0 if n_pos is None else int(n_pos)}/{int(n_sym)}"
                            + (f"({int(n_exc)} 제외)" if n_exc else ""),
                        ]
                    )
                    + " |"
                )
    return "\n".join(lines)


def _pct3(v: float | None, *, digits: int = 3) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}"


def best_r_table(frame: pd.DataFrame, timeframe: str) -> str:
    """문턱별 **IS 최적 배수 vs OOS 최적 배수** 대조표 — 뒤집히는 셀을 눈에 띄게 표시한다.

    🚨 오른쪽 두 열의 읽는 법: **「IS 최적을 OOS에 그대로 적용한 성적」이 판정 재료**이고
    **「OOS 최적」은 참고**다. 후자를 채택 근거로 쓰면 뒷구간을 보고 값을 고르는 것이다.
    """
    headers = (
        "문턱",
        "IS 최적",
        "IS 최적 − 1.5R",
        "IS 곡선 폭",
        "OOS 최적(참고)",
        "뒤집힘",
        "IS 최적을 OOS에 적용한 수익",
        "OOS 유효심볼",
    )
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    for threshold in THRESHOLDS:
        is_b = best_r(frame, timeframe, SEGMENT_IS, threshold)
        oos_b = best_r(frame, timeframe, SEGMENT_OOS, threshold)
        if not is_b.usable:
            blanks = " | ".join("—" for _ in range(len(headers) - 2))
            lines.append(f"| {threshold_label(threshold)} | ⚠️ 판정 불가(표본) | {blanks} |")
            continue
        applied = pooled(frame, timeframe, SEGMENT_OOS, threshold, is_b.best or R_DEFAULT)
        flip = "—" if not oos_b.usable else ("🚨 예" if oos_b.best != is_b.best else "아니오")
        spread, gap = curve_shape(frame, timeframe, SEGMENT_IS, threshold)
        lines.append(
            "| "
            + " | ".join(
                [
                    threshold_label(threshold),
                    f"**{is_b.best:.1f}R**" if is_b.best is not None else "—",
                    _fmt_pct(gap),
                    _fmt_pct(spread),
                    f"{oos_b.best:.1f}R" if oos_b.usable and oos_b.best is not None else "—",
                    flip,
                    _fmt_pct(applied.get("total_return")),
                    _fmt_num(applied.get("n_symbols"), ".0f"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def default_cost_note(frame: pd.DataFrame, timeframe: str) -> str:
    """**현행 1.5R을 그냥 두면 뒷구간에서 얼마를 잃나** — 이 표의 실무 답.

    ⚠️ **이것은 「OOS가 1.5R을 골랐다」가 아니다.** 1.5R은 이 격자와 무관하게 이미 채택돼
    있는 값이고(WAN-81 확정 · WAN-90 검증), 여기서 하는 일은 **고르는 것이 아니라 유지
    비용을 적는 것**이다. 뒷구간에서 값을 고르는 것은 여전히 금지다 — 그래서 「OOS 최적」
    열은 이 문장에서도 **비용의 분모로만** 쓰인다.
    """
    parts: list[str] = []
    for threshold in THRESHOLDS:
        oos_b = best_r(frame, timeframe, SEGMENT_OOS, threshold)
        if not oos_b.usable:
            continue
        _, gap = curve_shape(frame, timeframe, SEGMENT_OOS, threshold)
        if gap is None:
            continue
        tag = "**최적과 같다**" if gap == 0.0 else f"−{gap * 100:.2f}%p"
        parts.append(f"문턱 {threshold_label(threshold)}: {tag}")
    if not parts:
        return "계측 불가(표본 부족)."
    return (
        " · ".join(parts)
        + ". ⚠️ **「뒷구간이 1.5R을 골랐다」로 읽지 말 것** — 1.5R은 이 격자와 무관하게 "
        "이미 채택된 값이고(WAN-81 확정 · WAN-90 검증), 이 줄은 값을 **고르는** 것이 "
        "아니라 **유지 비용**을 적는 것이다."
    )


def chain_note(frame: pd.DataFrame, timeframe: str) -> str:
    """문턱 → 손절폭(1R) → 목표 거리 사슬이 실제로 작동하는지 — 이슈 §동기의 계량."""
    parts: list[str] = []
    for threshold in THRESHOLDS:
        cell = pooled(frame, timeframe, SEGMENT_OOS, threshold, R_DEFAULT)
        if not cell or not cell.get("n_symbols"):
            continue
        parts.append(
            f"문턱 {threshold_label(threshold)}: 손절폭 중앙값 "
            f"{_pct3(cell.get('stop_frac_median'))}% → 1.5R 목표 "
            f"{_pct3(cell.get('tp_dist_frac_median'))}% (가드 기각 "
            f"{_pct3(cell.get('guard_reject_rate'), digits=1)}%)"
        )
    if not parts:
        return "사슬 계측 불가(표본 부족)."
    return (
        " · ".join(parts)
        + ". 문턱을 조일수록 손절폭이 줄면 사슬(문턱 → 1R → 목표 절대 거리)이 실제로 "
        "작동하는 것이고, 그게 최적 배수가 이동할 구조적 이유다. ⚠️ 가드 기각률이 같이 "
        "오르는 것은 **관찰이지 가드 변경 제안이 아니다**(WAN-76/79 소관)."
    )


def _symbol_table(frame: pd.DataFrame, timeframe: str, segment: str = SEGMENT_OOS) -> str:
    sub = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["r_multiple"] == R_DEFAULT)
    ].copy()
    if sub.empty:
        return "(없음)"
    headers = ["symbol", "arm", "return%", "mdd%", "win%", "netR", "trades", "TP", "SL", "유효"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    sub["arm"] = [
        arm_label(None if pd.isna(t) else float(t), float(m))
        for t, m in zip(sub["threshold"], sub["r_multiple"], strict=True)
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


def build_summary_markdown(rows: Sequence[ThresholdRow], *, timeframes: Sequence[str]) -> str:
    frame = rows_to_frame(rows)
    symbols = sorted({_bare(r.symbol) for r in rows}) or ["—"]
    lines: list[str] = [
        "# WAN-161: 존폭 문턱 × 익절 배수 16조합 — 최적 배수가 문턱을 따라 움직이는가",
        "",
        "재현: `uv run python -m backtest.wan161_threshold_x_tp_multiple --tf 1h` → "
        "`--tf 15m --append` (요약만: `--from-csv`)",
        "",
        f"{len(symbols)}심볼({'/'.join(symbols)}) × {'·'.join(timeframes)} × IS/OOS × "
        f"문턱 {len(THRESHOLDS)}개 × 배수 {len(R_MULTIPLES)}개 = **16조합**. 못 박은 창 "
        f"**{DEFAULT_START} ~ {DEFAULT_END}**, 공식 렌즈 **`baseline` 단독**(WAN-128), "
        "오늘의 채택 기본값(오프셋 2bp · `intrabar_live` · `unconditional` · 롱 온리 · "
        f"분리 존) × 손절폭 가드 **{GUARD}** 고정 × 1R의 자는 현행 `entry_r`.",
        "",
        "문턱 단위는 **ATR 배수**다(`0.0128` 같은 분수가 아니다 — WAN-158 단위 경고). "
        "`필터 끔` = 넓은 존까지 포함한 전체 매매.",
        "",
        "> 🚨 **이 표는 「어느 문턱·어느 배수가 제일 돈을 잘 버나」에 답하지 않는다.** 묻는 "
        "것은 **민감도** 하나다 — 앞구간(IS)이 고른 최적 배수가 문턱을 바꿔도 같은 값인가. "
        "**「뒷구간이 X를 골랐다」를 채택 근거로 쓰지 않는다**(WAN-155 §3가 같은 이유로 15m "
        "2.0R을 뺐다).",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 손익은 **상한**이다. 존폭 축 체결 "
        "보수화는 WAN-154 §4가 `zone` 장벽에서 냈고 **이 표는 관통 축을 안 쟀다**.",
        "> ⚠️ **WAN-155·WAN-143·WAN-90 표와 셀 직접 비교 금지** — 격자·엔진이 다르다. "
        "**단 문턱 1.28 × 1.5R과 필터 끔 × 1.5R 두 셀만은 WAN-155 CSV와 비트 단위로 "
        "일치해야 하고**, 그게 이 표의 검산이다(맨 아래 절).",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124/151)은 불변 — 익절 배수는 알파가 아니라 "
        "**위험의 모양**만 바꾼다(WAN-90).",
        '> ⚠️ **기본값·토대 불변**(`take_profit_r=1.5` · `take_profit_mode="fixed_r"` · '
        "`min_stop_distance_fraction=0.003` 그대로, `ALPHABLOCK_LIVE_TRADING=false` 유지). "
        "**채택은 별도 재-베이스라인 결정 이슈이자 사용자 결정이다.**",
        "",
        "## 종합 판정",
        "",
        overall_verdict(frame, timeframes),
        "",
    ]
    for timeframe in timeframes:
        v = tf_verdict(frame, timeframe)
        lines += [
            f"## {timeframe}",
            "",
            f"**민감도 판정**: {v.text}",
            "",
            "### 문턱별 IS 최적 vs OOS 최적 (판정 절차 — IS 먼저, OOS는 적용 성적)",
            "",
            best_r_table(frame, timeframe),
            "",
            "**곡선 전체**:",
            "",
        ]
        for threshold in THRESHOLDS:
            lines += [
                f"- 문턱 {threshold_label(threshold)} IS: "
                f"{best_r(frame, timeframe, SEGMENT_IS, threshold).curve}",
                f"- 문턱 {threshold_label(threshold)} OOS: "
                f"{best_r(frame, timeframe, SEGMENT_OOS, threshold).curve}",
            ]
        lines += [
            "",
            f"**현행 1.5R 유지 비용(OOS 최적 대비)**: {default_cost_note(frame, timeframe)}",
            "",
            f"**사슬(문턱 → 1R → 목표 거리, OOS · 1.5R)**: {chain_note(frame, timeframe)}",
            "",
            "**Leave-one-out(OOS · 1.5R)**:",
            "",
        ]
        for threshold in THRESHOLDS:
            lines.append(
                f"- 문턱 {threshold_label(threshold)}: "
                f"{leave_one_out(frame, timeframe, threshold, R_DEFAULT)}"
            )
        lines += [
            "",
            "### 16조합 × IS/OOS (심볼평균 — 거래 20건 미만 셀 제외)",
            "",
            _grid_table(frame, timeframe),
            "",
            "### 심볼별 (OOS · 1.5R)",
            "",
            _symbol_table(frame, timeframe),
            "",
            "**유효 표본(20거래) 미달 셀:** "
            + (" · ".join(excluded_cells(frame, timeframe)) or "없음"),
            "",
        ]
    lines += [
        "## 검산",
        "",
        "**후보 집합 불변** — 익절 배수는 청산만 바꾸므로 같은 문턱의 4배수는 체결 셋업 "
        "집합이 비트 단위로 같고, 문턱을 조인 팔의 셋업은 느슨한 팔의 **부분집합**이다"
        "(개수가 아니라 집합으로 확인한다). 어긋나면 격자가 `AssertionError`로 멈춘다. "
        "표의 `trades`(시퀀싱 후)가 배수마다 다른 것은 **관찰**이다 — 목표가 멀수록 동시 "
        "1포지션 슬롯이 더 오래 잠긴다.",
        "",
        "**WAN-155 대조**:",
        "",
        crosscheck_wan155(rows),
        "",
        "**이중 필터 없음** — 문턱은 이 모듈의 명시 입력이라 WAN-159가 "
        "`max_zone_width_atr` 기본값을 옮겨도 같은 행이 나온다(`build_arm_params`가 "
        "「필터 끔」에 `None`을 명시적으로 덮어쓴다 — `harness.build_params`의 `None`은 "
        "규약상 「손대지 않는다」라 기본값이 켜져 있으면 조용히 그 문턱으로 돈다).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[ThresholdRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[ThresholdRow]:
    frame = pd.read_csv(path)
    records = frame.to_dict(orient="records")
    for rec in records:
        if pd.isna(rec.get("threshold")):
            rec["threshold"] = None
    return [ThresholdRow.model_validate(rec) for rec in records]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default="1h", help="콤마로 여러 개(예: 1h,15m)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--out-csv", type=str, default=str(REPORTS_DIR / "wan161_threshold_x_tp_multiple.csv")
    )
    parser.add_argument(
        "--out-md",
        type=str,
        default=str(REPORTS_DIR / "wan161_threshold_x_tp_multiple_summary.md"),
    )
    parser.add_argument(
        "--append", action="store_true", help="기존 CSV에 병합(좌표가 같은 행은 새 행이 이긴다)"
    )
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan161] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        rows = run_report(symbols, timeframes, start=args.start, end=args.end)
        if args.append and out_csv.exists():
            rows = merge_rows(rows_from_csv(out_csv), rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    timeframes_seen = list(dict.fromkeys(r.timeframe for r in rows))
    Path(args.out_md).write_text(
        build_summary_markdown(rows, timeframes=timeframes_seen), encoding="utf-8"
    )
    print(f"[wan161] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
