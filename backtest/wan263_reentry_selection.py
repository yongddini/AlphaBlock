"""WAN-263 — 조건부 재진입이 무작위보다 나은가 (재진입 이득/손해의 조건 축 + 매칭 널).

## 무엇을 묻나 (WAN-262 후속 · 사용자 요청 2026-08-07)

WAN-262(오늘 엔진 · 재진입 북 15m append)에서 익절 후 존 내 재진입 on의 효과가 **종목마다
갈렸다**: DOGE·ETH는 크게 이득인데 **LINK·SOL은 오히려 손해**다(다른 TF도 여러 종목이
마이너스). 즉 재진입은 "전 종목 균일 상승"이 아니라 **일부 자리에서 해롭다**.

질문: 재진입 후보를 **관찰 가능한 조건**으로 걸러내면(= 해로운 자리를 안 들어가면) 무작위로
같은 수만큼 거른 것보다 나은가? 나으면 그게 "선별"이고, 아니면 그냥 사후 편향이다.

## WAN-231과 무엇이 같고 무엇이 다른가

**같다**: 재진입 이벤트 자체는 WAN-228/231의 재무장 루프(`_iter_reentries`)로 만든다 —
새 파이프라인 없음(이슈 §1). 핀 없음 · 오늘 엔진(`ConfluenceParams()`·채택 북 회계 없이
격리 상한) · 9종목 · 못 박은 6년 창 · `baseline` 단독. 순위 p·유의 게이트도 WAN-231 그대로.

**다르다 — 널의 축이 「시각」이 아니라 「선별」이다.** WAN-231은 *되돌림 시각 진입 vs 무작위
시각 진입*을 물었다(WAN-142/152의 존폭 선별 널과 같은 종류지만 재진입 이벤트에 얹은 것).
이 모듈은 *조건으로 고른 재진입 부분집합 vs 같은 개수를 무작위로 고른 부분집합*을 묻는다 —
WAN-142/152/154의 **매칭 대조군**(same-count random draw)을 재진입 이벤트에 그대로 적용한다.

## 조건 축 (이슈 §2 · 각각 별도로)

* **(a) `zone_width`** — 존폭 ÷ ATR. 좁은 존만 재진입(WAN-159 채택 필터와 같은 자, ATR은
  탭 봉 **직전 확정봉**). 유리 방향 = 작을수록(좁은 존).
* **(b) `price_adv`** — 볼린저 재산정 진입가가 존 근단보다 얼마나 유리한가(1R 단위). 유리
  방향 = 클수록(더 싸게 산 롱 / 더 비싸게 판 숏). WAN-131이 "볼린저 이득은 대부분 가격"이라
  가른 그 축을 재진입에서 잰다.
* **(c) `prior_exit`** — 직전 청산 결과(익절 뒤 vs 손절 뒤). 🚨 **구조적으로 변별력이 없다**:
  (B) 재무장 루프는 **익절(승리) 뒤에만** 다시 무장한다(`_iter_reentries`의 `if not is_win:
  break`). 그래서 모든 (B) 재진입은 **익절 뒤**다 — "손절 뒤 재진입"은 이 모델에 존재하지
  않는다. 데이터 축을 만들지 않고 이 사실을 판정으로 기록한다(조용히 빼면 "쟀는데 무의"로
  오독된다). 손절 뒤 재무장은 별도 모델(재-베이스라인 = 사용자 결정)이다.
* **(d) `trend`** — 재진입 시각의 추세 방향. 추세순(롱이 상승장·숏이 하락장)만 재진입. 추세
  = 재진입 봉 **직전 확정봉**의 종가 대(對) EMA{TREND_EMA_LENGTH}(룩어헤드 안전, RSI 시딩과
  같은 컷). 이진 축이라 문턱이 없다.

## 대조군 설계 — same-count 매칭 널 (이슈 §3)

한 조건은 한 버킷(IS/따뜻OOS)에서 `m`개를 남긴다(전체 `N`개 중). 널은 **같은 버킷의 전체
`N`개에서 `m`개를 무작위로 뽑아**(개수 정확 일치) 격리 순수익 합을 낸다. 실제 남긴 합이 그
무작위 합을 이기면 = 조건이 "무작위 선별보다 낫다". 시드 **20개**, 단측 순위 p =
`(1 + #{널합 ≥ 실제합}) / 21`(하한 0.048). 유의 = 남긴 수 ≥ 20 & p ≤ 0.05 & 실제 > 널 평균.

## 문턱 · 자유 파라미터 경고 (완료기준 · WAN-108)

연속 축(a·b)의 문턱은 **각 칸의 앞구간(IS) 중앙값**으로 얼리고 **그 값을 뒷구간(OOS)에
그대로** 적용한다 — argmax가 아니라 고정 규칙이라 과최적화가 아니다(WAN-159 패턴). 유리
**방향**(좁을수록·클수록·추세순)은 WAN-262 가설에서 **선험적으로** 정했다. 🚨 이슈가 경고한
"종목별로 재진입 켜고 끄기"(IS에서 고르는 자유 파라미터, WAN-108)는 **하지 않는다** — 조건은
전 종목 **균일**하게 건다. LOO 표가 그 균일 조건의 이득이 한 종목에 기대는지 검사한다.

## 성격 · 경고

측정 전용. ⚠️ 유의가 나와도: (1) 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 **재진입이
그 가정에 가장 크게 의존**한다(스치듯 닿은 체결). (2) 손익은 **격리 상한**(동시 1포지션·자본·
북 상한 미모델링). (3) 존 **선택**은 같은 오더블록이라 이미 엣지 없음 — 조건 선별이 있어도
그건 이미 진입한 재진입 중 **무엇을 버릴까**의 문제다. 「엣지 없음」(WAN-84/88/111/114/124/
151/201)은 **탭 기준 진입이 무작위와 구분되는가**라는 다른 질문이라 불변이다. 유의 ≠ 수익 ≠
채택. 채택은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지.

## 재현

```
uv run python -m backtest.wan263_reentry_selection --tf 4h,1h,2h --jobs 6
uv run python -m backtest.wan263_reentry_selection --tf 15m --append --jobs 9  # 무거움(셀당 ~37분)
uv run python -m backtest.wan263_reentry_selection --from-csv                  # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.substep import build_substeps
from backtest.sweep import timeframe_to_ms
from backtest.wan228_reentry_census import (
    FUNDING_GAP_SYMBOLS,
    _iter_reentries,
)
from backtest.wan231_reentry_null import (
    ALPHA,
    BASE_SEED,
    MIN_TRADES_GATE,
    SEEDS,
    _sample,
    _short,
    rank_p_value,
)
from backtest.zone_limit_backtest import (
    _prepare_htf,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import atr, ema
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan263_reentry_selection.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan263_reentry_selection_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 기본 TF = 4h·1h·2h(컴퓨트 실현 가능). 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "2h")

#: 추세 축(d)의 EMA 길이 — 전략의 표시/익절 EMA 집합에 이미 있는 값(새 매직 넘버 회피).
TREND_EMA_LENGTH = 60

#: 존폭 필터가 읽는 ATR 길이(WAN-158 채택 필터와 같은 자).
ZONE_WIDTH_ATR_LENGTH = 14

#: 편중 LOO 대상 — WAN-262가 손해를 낸 LINK·SOL을 반드시 포함한다(이슈 배경).
BIAS_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE", "LINK")

#: 조건 축 — (c) `prior_exit`는 구조적 변별력이 없어(모든 재진입이 익절 뒤) 데이터 축을
#: 만들지 않고 판정으로만 기록한다. 아래는 실제로 매칭 널을 돌리는 세 축이다.
CONDITIONS: tuple[str, ...] = ("zone_width", "price_adv", "trend")

_COND_LABEL = {
    "zone_width": "(a) 존폭÷ATR (좁은 존만)",
    "price_adv": "(b) 볼린저 가격이점 (클수록)",
    "trend": "(d) 추세순 (롱=상승장)",
}

_BUCKETS = ("is", "oos")


# --------------------------------------------------------------------------- #
# 조건 주석이 달린 재진입 이벤트
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _AnnotatedReentry:
    """(B) 재진입 1건 + 조건 축 값. 손익은 격리 순수익 %p(WAN-228 정의 그대로)."""

    entry_time: int
    net_pp: float
    zone_width_atr: float | None
    """존폭 ÷ ATR(탭 봉 직전 확정봉). None = ATR 워밍업/미커버 → 조건 (a)가 못 고른다."""
    price_adv_r: float
    """진입가가 존 근단보다 유리한 정도(1R 단위, 클수록 유리)."""
    with_trend: bool
    """재진입 시각(직전 확정봉)의 추세가 포지션 방향과 같은가."""


def _condition_value(event: _AnnotatedReentry, condition: str) -> float | None:
    if condition == "zone_width":
        return event.zone_width_atr
    if condition == "price_adv":
        return event.price_adv_r
    return None  # trend은 이진 — 문턱 없음.


def is_threshold(events: Sequence[_AnnotatedReentry], condition: str) -> float | None:
    """앞구간(IS) 이벤트에서 문턱을 얼린다 = 그 축 값의 중앙값. 값이 없으면 None.

    argmax가 아니라 **중앙값**(고정 규칙)이라 IS에서 고른 자유 파라미터가 아니다(WAN-159).
    trend은 이진이라 문턱이 필요 없다(None).
    """
    if condition == "trend":
        return None
    vals = [v for e in events if (v := _condition_value(e, condition)) is not None]
    return median(vals) if vals else None


def keep_mask(
    events: Sequence[_AnnotatedReentry], condition: str, threshold: float | None
) -> list[bool]:
    """이 버킷 이벤트 중 조건이 남기는 것(True). 유리 방향은 WAN-262 가설에서 선험 고정.

    * `zone_width`: 유리 = 작을수록 → 값 ≤ 문턱을 남긴다(좁은 존).
    * `price_adv`: 유리 = 클수록 → 값 ≥ 문턱을 남긴다(더 유리한 가격).
    * `trend`: 추세순만 남긴다(문턱 무관).
    """
    if condition == "trend":
        return [e.with_trend for e in events]
    if threshold is None:
        return [False] * len(events)
    if condition == "zone_width":
        return [e.zone_width_atr is not None and e.zone_width_atr <= threshold for e in events]
    # price_adv
    return [e.price_adv_r >= threshold for e in events]


# --------------------------------------------------------------------------- #
# same-count 매칭 널 (한 버킷)
# --------------------------------------------------------------------------- #


def matched_null_sums(pool_net: Sequence[float], m: int) -> list[float]:
    """전체 풀에서 `m`개를 무작위로 뽑은 격리 순수익 합 — 시드 SEEDS개.

    시드 스트림을 `BASE_SEED + seed_idx`로 고정해 **심볼 간 seed-정렬 합**이 유효한 풀드 널
    표본이 되게 한다(각 심볼이 자기 풀에서 독립적으로 뽑되 같은 시드 인덱스끼리 더한다).
    `m == 0`이면 모든 시드가 0(뽑을 게 없다).
    """
    if m <= 0 or not pool_net:
        return [0.0] * SEEDS
    idx = list(range(len(pool_net)))
    sums: list[float] = []
    for seed_idx in range(SEEDS):
        rng = random.Random(BASE_SEED + seed_idx)
        drawn = _sample(rng, idx, m)
        sums.append(sum(pool_net[j] for j in drawn))
    return sums


# --------------------------------------------------------------------------- #
# 행 모델 — long-format (심볼 × TF × 조건 × 버킷)
# --------------------------------------------------------------------------- #


class CondRow(BaseModel):
    """한 (심볼, TF, 조건, 버킷)의 매칭 널 한 줄.

    `null_sums`(JSON 리스트 문자열)를 실어 두면 요약 재생성 시 **심볼 간 seed-정렬 합**으로
    풀드 널·풀드 p를 CSV만으로 복원한다(백테스트 재실행 없이).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    condition: str
    bucket: str  # "is" | "oos"
    window_start: int
    window_end: int

    n_bucket: int
    """이 버킷의 전체 재진입 수(널 표본추출 풀 크기)."""
    n_kept: int
    """조건이 남긴 수."""
    threshold: float | None
    """IS에서 얼린 문턱(trend·미산출은 None)."""

    all_net_pp: float
    """무필터(전체 재진입) 격리 순수익 %p 합 — 조건 없이 다 들어갈 때의 기준점."""
    actual_net_pp: float
    """조건이 남긴 재진입의 격리 순수익 %p 합."""
    null_mean_net_pp: float
    null_sums: str
    """JSON 리스트(SEEDS개) — 풀링용."""
    p_value: float | None

    funding_coverage: float | None

    @property
    def null_sum_list(self) -> list[float]:
        return [float(v) for v in json.loads(self.null_sums)]

    @property
    def valid(self) -> bool:
        return self.n_kept >= MIN_TRADES_GATE

    @property
    def significant(self) -> bool:
        return (
            self.valid
            and self.p_value is not None
            and self.p_value <= ALPHA
            and self.actual_net_pp > self.null_mean_net_pp
        )


# --------------------------------------------------------------------------- #
# 실행 (칸별)
# --------------------------------------------------------------------------- #


def describe_engine() -> str:
    """이 측정이 돌린 엔진 지문(WAN-164 패턴)."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={OrderBlockParams().combine_obs}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, limit_valid_bars={p.limit_valid_bars}, "
        f"short_enabled={p.short_enabled}, trend_ema={TREND_EMA_LENGTH}"
    )


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def _annotate_reentries(task: _Task) -> tuple[list[_AnnotatedReentry], int, float | None]:
    """한 칸의 (B) 재진입을 조건 축과 함께 모은다 → (이벤트, 평가경계, 펀딩커버리지).

    재진입 이벤트·손익은 WAN-228 `_iter_reentries`(재무장 루프)를 그대로 쓴다 — 이 모듈은
    거기에 존폭÷ATR·가격이점·추세 세 축을 얹기만 한다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms, need_1m=True
    )
    if market.empty or market.df_1m.empty:
        return [], 0, None
    ob = harness.detect_order_blocks(market, OrderBlockParams())
    cfg = harness.legacy_build_config(task.timeframe)
    params = harness.build_params()  # 채택 기본값(핀 없음).

    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        task.timeframe,
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_result=ob,
    )
    paired = sequence_with_candidates(candidates, cfg, market.funding_rates)

    htf_ms = timeframe_to_ms(task.timeframe)
    frame = _prepare_htf(market.htf_df)
    htf_times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    htf_closes = [float(v) for v in frame["close"].astype(float).tolist()]
    atr_vals = [float(v) for v in atr(frame, length=ZONE_WIDTH_ATR_LENGTH).tolist()]
    ema_vals = [float(v) for v in ema(frame, length=TREND_EMA_LENGTH).tolist()]
    substeps = build_substeps(market.df_1m, htf_ms)
    substep_times = [s.time for s in substeps]

    boundary = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    assert boundary is not None

    events: list[_AnnotatedReentry] = []
    for cand, trade in paired:
        if cand.reason is not ExitReason.TAKE_PROFIT or cand.order_block is None:
            continue
        obk = cand.order_block
        # 존 축 값(재진입은 부모 존을 그대로 물려받아 고정 지정가·손절을 쓴다).
        pos = bisect.bisect_left(htf_times, cand.trigger_time)
        atr_prev = atr_vals[pos - 1] if 1 <= pos <= len(atr_vals) else None
        zone_width = obk.top - obk.bottom
        zwatr = (
            zone_width / atr_prev
            if atr_prev is not None and atr_prev > 0.0 and zone_width == zone_width  # NaN 가드
            else None
        )
        if zwatr is not None and zwatr != zwatr:  # ATR NaN → nan/finite = nan
            zwatr = None
        proximal = obk.top if cand.side.sign > 0 else obk.bottom
        risk = abs(cand.entry_price - cand.stop_price)
        adv_r = (cand.side.sign * (proximal - cand.entry_price) / risk) if risk > 0.0 else 0.0

        for re_cand, ev in _iter_reentries(
            cand,
            parent_exit_time=trade.exit_time,
            substeps=substeps,
            substep_times=substep_times,
            htf_times=htf_times,
            htf_closes=htf_closes,
            params=harness.pin_invalidation_cancel(params),
            cfg=cfg,
            funding_rates=market.funding_rates,
        ):
            # 추세 = 재진입 봉 직전 확정봉의 종가 대 EMA(룩어헤드 안전, RSI 시딩과 같은 컷).
            forming = bisect.bisect_right(htf_times, re_cand.entry_time) - 1
            confirmed = forming - 1
            with_trend = False
            if 0 <= confirmed < len(ema_vals):
                em = ema_vals[confirmed]
                if em == em:  # EMA 워밍업 NaN이 아니면
                    up = htf_closes[confirmed] > em
                    with_trend = up if cand.side.sign > 0 else not up
            events.append(
                _AnnotatedReentry(
                    entry_time=re_cand.entry_time,
                    net_pp=ev.net_return_pp,
                    zone_width_atr=zwatr,
                    price_adv_r=adv_r,
                    with_trend=with_trend,
                )
            )

    coverage = harness.run_once(
        market, params=harness.pin_invalidation_cancel(params), cfg=cfg, order_block_result=ob
    ).result.metrics.funding_coverage
    return events, boundary, coverage


def run_cell(task: _Task, *, log: bool = True) -> list[CondRow]:
    """한 칸을 돌려 (조건 × 버킷) 매칭 널 행들을 낸다."""
    events, boundary, coverage = _annotate_reentries(task)
    if not events:
        return []
    is_events = [e for e in events if e.entry_time < boundary]
    oos_events = [e for e in events if e.entry_time >= boundary]
    by_bucket = {"is": is_events, "oos": oos_events}

    rows: list[CondRow] = []
    for condition in CONDITIONS:
        threshold = is_threshold(is_events, condition)  # 문턱은 IS에서 얼려 두 버킷에 공통.
        for bucket in _BUCKETS:
            bucket_events = by_bucket[bucket]
            pool_net = [e.net_pp for e in bucket_events]
            mask = keep_mask(bucket_events, condition, threshold)
            kept_net = [n for n, k in zip(pool_net, mask, strict=True) if k]
            m = len(kept_net)
            actual = sum(kept_net)
            null_sums = matched_null_sums(pool_net, m)
            null_mean = sum(null_sums) / SEEDS
            rows.append(
                CondRow(
                    symbol=task.symbol,
                    timeframe=task.timeframe,
                    condition=condition,
                    bucket=bucket,
                    window_start=task.start_ms,
                    window_end=task.end_ms,
                    n_bucket=len(bucket_events),
                    n_kept=m,
                    threshold=threshold,
                    all_net_pp=sum(pool_net),
                    actual_net_pp=actual,
                    null_mean_net_pp=null_mean,
                    null_sums=json.dumps([round(v, 10) for v in null_sums]),
                    p_value=rank_p_value(actual, null_sums),
                    funding_coverage=coverage,
                )
            )
    if log:
        oos = {(r.condition): r for r in rows if r.bucket == "oos"}
        parts = []
        for c in CONDITIONS:
            r = oos[c]
            p = f"{r.p_value:.3f}" if r.p_value is not None else "—"
            parts.append(f"{c} m={r.n_kept} act={r.actual_net_pp:+.0f} p={p}")
        print(f"[wan263] {task.symbol} {task.timeframe} OOS: " + " · ".join(parts), flush=True)
    return rows


def _run_task_logged(task: _Task) -> list[CondRow]:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[CondRow]:
    """9종목 × TF 칸을 돌아 매칭 널 행을 모은다. `jobs`는 성능 노브(WAN-121)."""
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        nested = [run_cell(task, log=log) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            nested = list(executor.map(_run_task_logged, tasks))
    return [row for rows in nested for row in rows]


# --------------------------------------------------------------------------- #
# 풀링 · 판정 (순수 함수 — 테스트가 여기를 고정한다)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Pooled:
    """한 (TF, 조건, 버킷)에서 심볼을 가로질러 seed-정렬 합한 매칭 널."""

    timeframe: str
    condition: str
    bucket: str
    n_kept: int
    n_bucket: int
    all_net_pp: float
    actual_net_pp: float
    null_mean_net_pp: float
    p_value: float | None

    @property
    def valid(self) -> bool:
        return self.n_kept >= MIN_TRADES_GATE

    @property
    def significant(self) -> bool:
        return (
            self.valid
            and self.p_value is not None
            and self.p_value <= ALPHA
            and self.actual_net_pp > self.null_mean_net_pp
        )


def pool(rows: Sequence[CondRow], timeframe: str, condition: str, bucket: str) -> Pooled | None:
    """심볼 간 seed-정렬 합으로 풀드 매칭 널을 만든다. 대상 행이 없으면 None.

    풀드 널[k] = Σ_심볼 (그 심볼의 널합[k]) — 각 심볼이 자기 풀에서 독립적으로 뽑은
    같은 시드 인덱스끼리 더한 것이라 유효한 결합 몬테카를로 표본이다. 풀드 실제 = Σ 실제.
    """
    scoped = [
        r
        for r in rows
        if r.timeframe == timeframe and r.condition == condition and r.bucket == bucket
    ]
    if not scoped:
        return None
    pooled_null = [0.0] * SEEDS
    for r in scoped:
        for k, v in enumerate(r.null_sum_list):
            pooled_null[k] += v
    actual = sum(r.actual_net_pp for r in scoped)
    return Pooled(
        timeframe=timeframe,
        condition=condition,
        bucket=bucket,
        n_kept=sum(r.n_kept for r in scoped),
        n_bucket=sum(r.n_bucket for r in scoped),
        all_net_pp=sum(r.all_net_pp for r in scoped),
        actual_net_pp=actual,
        null_mean_net_pp=sum(pooled_null) / SEEDS,
        p_value=rank_p_value(actual, pooled_null),
    )


def per_cell_sig_counts(rows: Sequence[CondRow], timeframe: str, condition: str) -> tuple[int, int]:
    """(유효 OOS 셀 수, OOS 유의 셀 수) — 한 (TF, 조건)."""
    oos = [
        r
        for r in rows
        if r.timeframe == timeframe and r.condition == condition and r.bucket == "oos"
    ]
    valid = sum(1 for r in oos if r.valid)
    sig = sum(1 for r in oos if r.significant)
    return valid, sig


def oos_kept_symbol_mean(rows: Sequence[CondRow], timeframe: str, condition: str) -> float:
    """따뜻한 OOS 조건-남긴 격리 순수익 %p의 심볼평균(칸당 남긴 합). 대상 없으면 0.0."""
    vals = [
        r.actual_net_pp
        for r in rows
        if r.timeframe == timeframe and r.condition == condition and r.bucket == "oos"
    ]
    return sum(vals) / len(vals) if vals else 0.0


def leave_one_out(
    rows: Sequence[CondRow], timeframe: str, condition: str, drop: str
) -> float | None:
    """한 심볼을 뺀 OOS 조건-남긴 순수익 심볼평균. 대상 심볼이 없으면 None."""
    scoped = [
        r
        for r in rows
        if r.timeframe == timeframe and r.condition == condition and r.bucket == "oos"
    ]
    kept = [r.actual_net_pp for r in scoped if _short(r.symbol) != drop]
    if len(kept) == len(scoped):
        return None
    return sum(kept) / len(kept) if kept else 0.0


def verdict(rows: Sequence[CondRow]) -> str:
    """조건 선별이 무작위 선별보다 나은가 — 풀드 OOS 매칭 널로 판정."""
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    any_sig = False
    per_line: list[str] = []
    for tf in timeframes:
        marks = []
        for c in CONDITIONS:
            p = pool(rows, tf, c, "oos")
            if p is None:
                continue
            sig = p.significant
            any_sig = any_sig or sig
            pv = f"{p.p_value:.3f}" if p.p_value is not None else "—"
            marks.append(f"{_COND_LABEL[c].split()[0]} p={pv}{'✅' if sig else ''}")
        per_line.append(f"{tf}: " + " · ".join(marks))
    head = (
        "**(a) 조건 선별이 무작위 선별을 이긴다(적어도 한 축·한 TF의 풀드 OOS에서).**"
        if any_sig
        else "**(b) 무의 — 어떤 조건도 무작위 선별과 구분되지 않는다(풀드 OOS).**"
    )
    return (
        f"{head} " + " / ".join(per_line) + ". 🚨 **(c) `prior_exit` 축은 구조적으로 변별력이 "
        "없다** — (B) 재무장은 익절(승리) 뒤에만 다시 무장하므로 모든 재진입이 익절 뒤다"
        "(손절 뒤 재진입은 이 모델에 없다). ⚠️ 유의여도 채택 근거가 아니다: 전부 `baseline`"
        "(닿으면 체결) 낙관 렌즈 위 값이고 재진입이 그 가정에 가장 크게 의존하며, 손익은 격리 "
        "상한(동시 1포지션·자본·북 상한 미모델링)이다. 조건의 유리 **방향**은 WAN-262 가설에서 "
        "선험적으로 골랐고 문턱만 IS 중앙값으로 얼렸다 — 「종목별 on/off」(WAN-108 자유 "
        "파라미터)는 하지 않았다(조건은 전 종목 균일). 「엣지 없음」(WAN-84/88/111/114/124/151/"
        "201)은 탭 기준 진입 판정이라 이 축과 별개다. 채택은 재-베이스라인 = 사용자 결정."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[CondRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CondRow.model_fields))


def cells_from_csv(path: Path) -> list[CondRow]:
    frame = pd.read_csv(path)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [CondRow.model_validate(rec) for rec in records]


def merge_rows(existing: Sequence[CondRow], new: Sequence[CondRow]) -> list[CondRow]:
    """기존 행에 새 TF 행을 병합 — 같은 (심볼, TF, 조건, 버킷)은 새 행이 이긴다(--append)."""
    new_keys = {(r.symbol, r.timeframe, r.condition, r.bucket) for r in new}
    kept = [r for r in existing if (r.symbol, r.timeframe, r.condition, r.bucket) not in new_keys]
    return [*kept, *new]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _p(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "—"


def _pooled_table(rows: Sequence[CondRow], timeframe: str) -> list[str]:
    """조건 축별 풀드 매칭 널 (IS/OOS 갈라서) — 완료기준 1."""
    lines = [
        f"### {timeframe} — 조건 축별 풀드 매칭 널",
        "",
        "| 조건 | OOS 남김 | OOS 무필터 | OOS 조건 | OOS 널평균 | OOS p | 유의 | "
        "셀 유의 | IS 조건 | IS 널평균 | IS p |",
        "| -- | --: | --: | --: | --: | --: | :--: | :--: | --: | --: | --: |",
    ]
    for c in CONDITIONS:
        po = pool(rows, timeframe, c, "oos")
        pi = pool(rows, timeframe, c, "is")
        if po is None or pi is None:
            continue
        valid, sig = per_cell_sig_counts(rows, timeframe, c)
        mark = "✅" if po.significant else ("·" if po.valid else "n<20")
        lines.append(
            f"| {_COND_LABEL[c]} | {po.n_kept}/{po.n_bucket} | {po.all_net_pp:+.0f}%p | "
            f"{po.actual_net_pp:+.0f}%p | {po.null_mean_net_pp:+.0f}%p | {_p(po.p_value)} | "
            f"{mark} | {sig}/{valid} | {pi.actual_net_pp:+.0f}%p | {pi.null_mean_net_pp:+.0f}%p | "
            f"{_p(pi.p_value)} |"
        )
    return lines


def _loo_table(rows: Sequence[CondRow], timeframe: str) -> list[str]:
    """종목 편중 LOO 표 (OOS 조건-남긴 순수익 심볼평균) — 완료기준 1."""
    lines = [
        f"### {timeframe} — 종목 편중 LOO (OOS 조건-남긴 순수익 심볼평균)",
        "",
        "| 조건 | 전체 | " + " | ".join(f"−{s}" for s in BIAS_SYMBOLS) + " |",
        "| -- | --: | " + " | ".join("--:" for _ in BIAS_SYMBOLS) + " |",
    ]
    for c in CONDITIONS:
        full = oos_kept_symbol_mean(rows, timeframe, c)
        loos = []
        for s in BIAS_SYMBOLS:
            v = leave_one_out(rows, timeframe, c, s)
            loos.append(f"{v:+.2f}%p" if v is not None else "—")
        lines.append(f"| {_COND_LABEL[c]} | {full:+.2f}%p | " + " | ".join(loos) + " |")
    return lines


def build_summary_markdown(rows: Sequence[CondRow], *, cells_csv: Path) -> str:
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    window = next(iter({(r.window_start, r.window_end) for r in rows}), (0, 0))
    funded = sorted({_short(r.symbol) for r in rows if r.symbol in FUNDING_GAP_SYMBOLS})
    lines = [
        "# WAN-263 — 조건부 재진입이 무작위보다 나은가 (조건 축 + 매칭 널)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`) "
        "돌리며 옛 핀은 하나도 물려받지 않는다. 렌즈 `baseline` 단독(WAN-128) · 못 박은 "
        "6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "## 이 측정이 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영.",
        "",
        "## 무엇을 묻나",
        "",
        "WAN-262에서 재진입 on의 효과가 **종목마다 갈렸다**(DOGE·ETH 이득 · LINK·SOL 손해). "
        "재진입 후보를 **관찰 가능한 조건**으로 걸러내면 무작위로 같은 수만큼 거른 것보다 "
        "나은가? 나으면 「선별」, 아니면 사후 편향이다. 재진입 이벤트·손익은 WAN-228/231의 "
        "재무장 루프(`_iter_reentries`)를 그대로 쓰고(새 파이프라인 없음), 이 모듈은 세 조건 "
        "축과 **same-count 매칭 널**(WAN-142/152/154 자)을 얹는다.",
        "",
        "## 조건 축",
        "",
        f"* **{_COND_LABEL['zone_width']}** — 존폭 ÷ ATR(탭 봉 직전 확정봉, WAN-158 자). "
        "유리 = 좁은 존.",
        f"* **{_COND_LABEL['price_adv']}** — 볼린저 재산정 진입가가 존 근단보다 유리한 정도"
        "(1R 단위). 유리 = 클수록. WAN-131 「선별 대 가격」의 가격 축을 재진입에서 잰다.",
        f"* **{_COND_LABEL['trend']}** — 재진입 봉 직전 확정봉 종가 대 "
        f"EMA{TREND_EMA_LENGTH}(룩어헤드 안전). 이진 축.",
        "* 🚨 **(c) 직전 청산 결과(익절 뒤 vs 손절 뒤)는 구조적으로 변별력이 없다** — (B) "
        "재무장 루프는 **익절(승리) 뒤에만** 다시 무장하므로(`if not is_win: break`) 모든 "
        "재진입이 익절 뒤다. 「손절 뒤 재진입」은 이 모델에 존재하지 않는다 → 데이터 축을 "
        "만들지 않고 판정으로만 기록한다(조용히 빼면 '쟀는데 무의'로 오독된다).",
        "",
        "## 대조군 · 문턱 · 자유 파라미터",
        "",
        "한 조건은 한 버킷(IS/따뜻OOS)에서 `m`개를 남긴다(전체 `N`개 중). 널은 같은 버킷의 "
        f"전체 `N`개에서 `m`개를 무작위로 뽑아(개수 정확 일치) 격리 순수익 합을 낸다. 시드 "
        f"**{SEEDS}개**, 단측 순위 p = `(1 + #{{널합 ≥ 실제합}}) / {SEEDS + 1}`(하한 "
        f"{1 / (SEEDS + 1):.3f}). 유의 = 남긴 수 ≥ {MIN_TRADES_GATE} & p ≤ {ALPHA} & 실제 > "
        "널 평균. **풀드** 매칭 널은 심볼을 가로질러 같은 시드 인덱스끼리 더한 결합 표본이다"
        "(각 심볼이 자기 풀에서 독립적으로 뽑음).",
        "",
        "🚨 **자유 파라미터 경고(WAN-108)**: 연속 축(a·b)의 문턱은 **각 칸의 IS 중앙값**으로 "
        "얼려 **OOS에 그대로** 적용한다(argmax 아님 = 과최적화 아님, WAN-159 패턴). 유리 "
        "**방향**은 WAN-262 가설에서 **선험적으로** 골랐다. 이슈가 경고한 「종목별로 재진입 "
        "켜고 끄기」(IS에서 고르는 자유 파라미터)는 **하지 않는다** — 조건은 전 종목 **균일**"
        "하다. LOO 표가 그 균일 조건의 이득이 한 종목에 기대는지 검사한다.",
        "",
        f"재현: `uv run python -m backtest.wan263_reentry_selection --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` · 15m은 `--tf 15m --append --jobs 9`"
        f"(셀당 ~37분, WAN-203 선례) (요약만: `--from-csv`). 원자료: `{cells_csv}`. "
        f"창=[{window[0]}, {window[1]}).",
        "",
        "⚠️ **TF 범위**: 이 표는 **"
        + " · ".join(timeframes)
        + "**다. 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 append로 남긴다(WAN-231→242, "
        "WAN-261→262와 같은 분할). 15m은 WAN-262가 종목 갈림을 처음 보인 축이라 이 조건 "
        "선별의 15m 재검이 결정적 후속이다."
        if "15m" not in timeframes
        else "TF 범위: " + " · ".join(timeframes) + ".",
        "",
    ]
    if funded:
        lines += [
            f"⚠️ 신규 종목({', '.join(funded)})은 펀딩 0행이라 순수익이 낙관적이다(WAN-178 백필 "
            "전) — 실제·널에 대칭이라 p엔 무관하되 절대 순수익 수준엔 얹힌다.",
            "",
        ]
    lines += [
        "## 조건 축별 풀드 매칭 널 (IS/OOS 갈라서)",
        "",
        "`OOS 남김` = 조건이 남긴 수 / 버킷 전체 수 · `무필터` = 전체 재진입 순수익 합(조건 "
        "없이 다 들어갈 때) · `조건` = 조건이 남긴 순수익 합 · `널평균` = 같은 수 무작위 선별의 "
        "합(시드 평균) · `p` = 단측 순위 p · `유의` = 풀드 n≥20 & p≤0.05 & 조건>널 · `셀 유의` "
        "= 개별 칸에서 유의한 수 / 유효(n≥20) 칸 수.",
        "",
    ]
    for tf in timeframes:
        lines += _pooled_table(rows, tf)
        lines.append("")
        lines += _loo_table(rows, tf)
        lines.append("")
    lines += [
        "## 판정 — 조건 선별이 무작위 선별보다 나은가",
        "",
        verdict(rows),
        "",
        "⚠️ **이 표는 채택 근거가 아니라 선별 측정이다.** 유의여도 (1) 전부 `baseline`(닿으면 "
        "체결) 낙관 렌즈 위 값이고 재진입이 그 가정에 가장 크게 의존하며(스치듯 닿은 체결), "
        "(2) 손익은 **격리 상한**(동시 1포지션·자본·북 상한 미모델링), (3) 존 선택 자체는 같은 "
        "오더블록이라 이미 엣지 없음 — 조건 선별은 **이미 진입한 재진입 중 무엇을 버릴까**의 "
        "문제다. 「엣지 없음」(WAN-84/88/111/114/124/151/201)은 **탭 기준 진입**이 무작위와 "
        "구분되는가라는 다른 질문이라 불변이다. 채택(층 2 resting-order sim·재무장 기본값화)은 "
        "재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지(큐 우선순위 WAN-98 Canceled · "
        "라이브 충실도 WAN-45 선행). **기본값·토대 불변**(측정 전용 · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-263 조건부 재진입 매칭 널")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="새로 돌린 행을 기존 --out-cells CSV에 병합한다(같은 (심볼,TF,조건,버킷)은 갱신). "
        "15m 축을 4h·1h·2h 표에 덧붙일 때 쓴다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_md = Path(args.out_md)

    if args.from_csv:
        rows = cells_from_csv(out_cells)
        print(f"[wan263] CSV에서 {len(rows)}행 로드 — 백테스트 재실행 없음")
    else:
        new_rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        if args.append and out_cells.exists():
            existing = cells_from_csv(out_cells)
            new_keys = {(r.symbol, r.timeframe, r.condition, r.bucket) for r in new_rows}
            overlaps = any(
                (r.symbol, r.timeframe, r.condition, r.bucket) in new_keys for r in existing
            )
            rows = merge_rows(existing, new_rows)
            if overlaps:
                cells_to_frame(rows).to_csv(out_cells, index=False)
            else:
                cells_to_frame(new_rows).to_csv(out_cells, index=False, mode="a", header=False)
            print(
                f"[wan263] --append: 기존 유지 {len(rows) - len(new_rows)}행 + "
                f"신규 {len(new_rows)}행 = {len(rows)}행"
                + ("(겹침 → 전체 재작성)" if overlaps else "(순수 추가 → 기존 바이트 보존)")
            )
        else:
            rows = new_rows
            cells_to_frame(rows).to_csv(out_cells, index=False)
        print(f"[wan263] 매칭 널 {len(rows)}행 → {out_cells}")

    if not rows:
        print("[wan263] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_summary_markdown(rows, cells_csv=out_cells), encoding="utf-8")
    print(f"[wan263] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
