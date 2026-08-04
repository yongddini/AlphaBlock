"""WAN-231 — 익절 후 존 내 재진입 (B) 매칭 널 (탭 없는 재진입 타이밍이 무작위보다 나은가).

## 무엇을 묻나 (WAN-228/229 후속 · 사용자 요청 2026-08-02)

WAN-228(4h·1h)·WAN-229(15m)이 **(B) 익절 후 존 내 재진입**의 *크기*를 census로 쟀다
((B) 재진입 = 채택 진입의 60.7%, 기계적 (a) GO). 그러나 **매칭 널을 안 돌렸다** — census는
"몇 건이 체결되고 손익이 얼마인가"만 셌지 "무작위보다 나은가"는 답하지 않는다. 이 모듈이 그
질문에 답한다: **익절 후 "이겼던 자리로 되돌아왔을 때" 재진입하는 타이밍이, 같은 존이 살아
있는 창 안에서 무작위 시각에 들어가는 것보다 나은가.**

## 왜 새 질문인가 (기존 「엣지 없음」이 안 덮는다)

「엣지 없음」(WAN-84/88/111/114/124/151/201)은 **전부 탭(바깥→안 전이) 기준 진입**을 매칭
널로 검정했다. 이 (B) 재진입은 **탭이 없다** — 가격이 존 밖으로 나가지 않아도 익절 후 존
안에서 되돌아와 지정가에 닿으면 재진입한다(현행 엔진이 하지 않는 동작). 그래서 옛 판정으로
"없다"고 못 박을 수 없고, 이 표가 **그 축의 첫 매칭 널**이다(옛 판정을 뒤집는 게 아니다).

## 대조군 설계 — (a) 무작위 *시각* (이슈 §1의 두 후보 중 택 1, 근거 명시)

이슈는 (a) 무작위 시각 / (b) 무작위 가격 중 하나를 개발자가 고르게 했다. **(a)를 고른다.**

* 이슈의 핵심 가설은 **"엣지가 있다면 되돌림 타이밍에서만 온다"** 이다. 그 가설을 검정하려면
  "되돌림 시각에 들어감" 대(對) "**되돌림이 아닌** 아무 시각에 들어감"을 대조해야 한다 —
  즉 무작위 **시각**. WAN-90이 실측한 익절 후 평균회귀(본절 복귀율 97~99%)가 사실이면,
  "이겼던 자리로 되돌아온 순간"이 무작위 순간보다 나은 진입일 수 있다. 이 표가 그걸 잰다.
* (b) 무작위 **가격**은 다른(더 좁은) 질문이다 — 되돌림 구조는 그대로 두고 *어느 레벨*이
  특별한가만 묻는다. 되돌림 타이밍 구조 자체가 레벨을 안 가리고 엣지를 준다면 (b)는 그걸
  **못 잡는다**(무작위 레벨도 자기 레벨로의 되돌림에 체결되므로). 그래서 (b)는 (a)가 유의일
  때의 후속 정밀 분해로 남긴다(레벨 특정성).

**널 정의**: 익절로 닫힌 존마다 실제 팔(WAN-228 `reentry_events`)이 IS/따뜻OOS 버킷에 각각
`k_is`·`k_oos` 재진입을 낸다. 널은 **같은 유효 창의 같은 버킷 하위창**에서 무작위 시각을
`k_is`·`k_oos`개 뽑아(버킷별 개수 정확히 일치), 그 서브스텝 가격에 진입해(손절 = 존 무효화
경계 그대로 · 목표 = 진입가 기준 고정 1.5R) **엔진과 동일한** `simulate_zone_limit_trade`로
청산까지 돌린다. 실제와 널의 유일한 차이는 **진입 시각(과 그 결과 가격)** 이다.

## 자(尺) · 판정 (WAN-88/124/151/164/201 관행 계승)

* 통계량 = 버킷 격리 순수익 %p 합(`_to_trade` 독립 체결). 실제 팔의 이 값은 WAN-228/229
  census의 `re_*_net_pp_sum`과 **비트 일치**한다(검산 = 완료기준 4).
* 시드 **20개**, 단측 순위 p = `(1 + #{널합 ≥ 실제}) / (1 + 20)` → 하한 0.048
  (WAN-142/152/154 관행). 셀당 IS·따뜻OOS 각각.
* 유의 = **거래 20건 이상**(WAN-84 유효 기준) **그리고** p ≤ 0.05 **그리고** 실제 > 널 평균.
* 편중: ETH·SOL·DOGE leave-one-out으로 심볼평균 부호가 뒤집히는지 확인.

## 성격 · 경고 (이슈 「결과 해석 주의」 — 반드시)

측정 전용. 렌즈 `baseline` 단독(WAN-128) · 핀 없음 · 채택 좌표 9종목 × 15m·1h·4h × 못 박은
6년(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지). ⚠️ 유의가 나와도:
(1) 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 **재진입이 그 가정에 가장 크게
의존**한다(스치듯 닿은 체결). (2) §손익은 **격리 상한**(동시 1포지션·자본·북 상한 미모델링).
(3) 존 **선택**은 같은 오더블록이라 이미 엣지 없음 — 엣지가 있다면 **되돌림 타이밍**에서만.
유의 ≠ 수익 ≠ 채택. 채택(층 2 resting-order sim·재무장 기본값화)은 재-베이스라인 = 사용자
결정 · 개발자 임의 착수 금지(큐 우선순위 WAN-98 Canceled · 라이브 충실도 WAN-45 선행).

## 재현

```
uv run python -m backtest.wan231_reentry_null --tf 4h,1h --jobs 6
uv run python -m backtest.wan231_reentry_null --tf 15m --jobs 9   # 무거움(WAN-229 15m 선례)
uv run python -m backtest.wan231_reentry_null --from-csv          # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import bisect
import random
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.substep import (
    SubStep,
    ZoneLimitStatus,
    build_substeps,
    simulate_zone_limit_trade,
)
from backtest.sweep import timeframe_to_ms
from backtest.wan228_reentry_census import (
    FUNDING_GAP_SYMBOLS,
    _direction,
    _Reentry,
    reentry_events,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    _prepare_htf,
    _to_trade,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.models import ConfluenceParams, OrderBlockParams, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan231_reentry_null.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan231_reentry_null_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
ALL_SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS

#: 기본 TF = 4h·1h(컴퓨트 실현 가능). 15m은 무거움(WAN-229 선례) — 별도 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h")

#: 매칭 널 시드 수 — WAN-142/152/154 관행. 단측 순위 p 하한 = 1/(SEEDS+1) = 0.048.
SEEDS = 20

#: 시드 스트림의 기준값(재현성). 셀은 (심볼, TF) 단위로 독립 계산되므로 이 하나로 족하다.
BASE_SEED = 231_000

#: 유의 게이트 — WAN-84 유효 기준(거래 20건)과 p 문턱.
MIN_TRADES_GATE = 20
ALPHA = 0.05

#: 편중 확인 대상(이슈 완료기준 2). 15m 새 종목 DOGE도 포함.
BIAS_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE")


# --------------------------------------------------------------------------- #
# 순수 자(尺) — 순위 p값
# --------------------------------------------------------------------------- #


def rank_p_value(actual: float, null_values: Sequence[float]) -> float | None:
    """단측 순위 p = (1 + #{널 ≥ 실제}) / (1 + K). 널이 없으면 None.

    WAN-70/88 관행("실제 이상을 낸 비율")에 +1 보정을 얹은 형태로, K=20이면 하한 1/21 =
    0.048(WAN-142/152/154에서 관측된 바닥)이 그대로 나온다.
    """
    k = len(null_values)
    if k == 0:
        return None
    ge = sum(1 for v in null_values if v >= actual)
    return (1 + ge) / (1 + k)


def is_significant(*, n: int, p_value: float | None, actual: float, null_mean: float) -> bool:
    """유의 = 거래 20건 이상 & p ≤ 0.05 & 실제 > 널 평균(WAN-84 게이트 + WAN-88 방향)."""
    return n >= MIN_TRADES_GATE and p_value is not None and p_value <= ALPHA and actual > null_mean


# --------------------------------------------------------------------------- #
# 널 한 건의 진입 시뮬레이션 (무작위 시각 → 그 서브스텝 가격 진입)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ZoneNullContext:
    """한 익절 존의 널 표본추출에 필요한 모든 것 — 실제 팔이 낸 버킷 개수 포함.

    유효 창 하위집합을 IS/OOS 사전분리해 둔다(버킷별 개수 정확 매칭). `valid_*`는 진입이
    기하적으로 성립하는(롱=진입가 > 손절) 서브스텝 인덱스 풀이다.
    """

    side: PositionSide
    stop_price: float
    take_profit_r: float
    valid_is_idx: tuple[int, ...]
    valid_oos_idx: tuple[int, ...]
    k_is: int
    k_oos: int


def _net_pp_of_entry(
    *,
    start: int,
    cand_side: PositionSide,
    stop_price: float,
    take_profit_r: float,
    substeps: Sequence[SubStep],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    invalidation_time: int | None,
) -> float | None:
    """서브스텝 `start`의 종가에 진입해 청산까지 돌린 격리 순수익 %p. 기하 무효면 None.

    엔진과 **동일한** `simulate_zone_limit_trade`를 쓴다 — 지정가를 그 서브스텝 종가로 두면
    즉시 체결되고(롱 low ≤ close · 숏 high ≥ close), 그 뒤 손절·익절·무효화 로직이 실제 팔과
    한 글자도 다르지 않다. 실제와 널의 차이는 진입 시각(과 그 결과 가격)뿐이다.
    """
    entry_price = substeps[start].close
    is_long = cand_side is PositionSide.LONG
    risk = abs(entry_price - stop_price)
    if risk <= 0.0:
        return None
    # 이미 손절 너머(롱=진입가가 손절 이하)면 그 시각엔 들어갈 수 없다.
    if is_long and entry_price <= stop_price:
        return None
    if not is_long and entry_price >= stop_price:
        return None
    take_profit_price = (
        entry_price + take_profit_r * risk if is_long else entry_price - take_profit_r * risk
    )
    cut = bisect.bisect_left(htf_times, substeps[start].htf_bar_time)
    rsi_state = RealtimeRsi.seed_from_closed(htf_closes[:cut], length=params.rsi_length)
    outcome = simulate_zone_limit_trade(
        direction=_direction(cand_side),
        limit_price=entry_price,
        stop_price=stop_price,
        substeps=substeps,
        start=start,
        rsi_state=rsi_state,
        rsi_oversold=params.rsi_oversold,
        rsi_overbought=params.rsi_overbought,
        take_profit_price=take_profit_price,
        limit_valid_bars=None,
        invalidation_time=invalidation_time,
        rsi_gate_mode=params.rsi_gate_mode,
        rsi_neutral_band=params.rsi_neutral_band,
        penetration_bps=params.fill_penetration_bps,
    )
    if not outcome.filled or outcome.entry_time is None or outcome.entry_price is None:
        return None
    if outcome.status is ZoneLimitStatus.FILLED_EXITED:
        assert outcome.exit_time is not None and outcome.exit_price is not None
        is_win = outcome.exit_reason is SignalExitReason.TAKE_PROFIT
        exit_time, exit_price = outcome.exit_time, outcome.exit_price
        reason = ExitReason.TAKE_PROFIT if is_win else ExitReason.STOP_LOSS
    else:
        exit_time, exit_price = substeps[-1].time, substeps[-1].close
        reason = ExitReason.END_OF_DATA
    re_cand = _Candidate(
        side=cand_side,
        entry_time=outcome.entry_time,
        entry_price=outcome.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        reason=reason,
        stop_price=stop_price,
    )
    trade = _to_trade(re_cand, cfg.initial_capital, cfg, funding_rates)
    return (trade.return_pct * 100.0) if trade is not None else 0.0


def _valid_indices(
    lo_time: int,
    hi_time: int,
    *,
    cand_side: PositionSide,
    stop_price: float,
    substeps: Sequence[SubStep],
    substep_times: Sequence[int],
    invalidation_time: int | None,
) -> tuple[int, ...]:
    """[lo_time, hi_time) 안에서 진입이 기하적으로 성립하는 서브스텝 인덱스들.

    `hi_time`은 배타적이고, 존 무효화 시각이 그 전이면 거기서 자른다(실제 팔과 같은 상한).
    """
    upper = hi_time if invalidation_time is None else min(hi_time, invalidation_time)
    start = bisect.bisect_right(substep_times, lo_time)  # 익절/경계 **직후**
    end = bisect.bisect_left(substep_times, upper)
    is_long = cand_side is PositionSide.LONG
    out: list[int] = []
    for j in range(start, end):
        price = substeps[j].close
        if is_long:
            if price > stop_price:
                out.append(j)
        elif price < stop_price:
            out.append(j)
    return tuple(out)


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class NullRow(BaseModel):
    """한 (심볼, TF)의 (B) 재진입 매칭 널 한 줄 — IS·따뜻OOS 각각 실제 vs 널 + p값.

    `re_*_n`·`actual_*_net_pp`·`reentries_total`은 WAN-228/229 census와 비트 일치한다
    (실제 팔이 같은 `reentry_events`이므로). `null_*_mean_net_pp`·`p_*`가 이 모듈이 더한 것이다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    window_start: int
    window_end: int
    seeds: int

    adopted_entries: int
    tp_entries: int
    reentries_total: int
    """(B) 후보 재진입 수(전 구간) — census 검산 대상."""

    re_is_n: int
    actual_is_net_pp: float
    null_is_mean_net_pp: float
    p_is: float | None

    re_oos_n: int
    actual_oos_net_pp: float
    null_oos_mean_net_pp: float
    p_oos: float | None

    funding_coverage: float | None

    @property
    def sig_is(self) -> bool:
        return is_significant(
            n=self.re_is_n,
            p_value=self.p_is,
            actual=self.actual_is_net_pp,
            null_mean=self.null_is_mean_net_pp,
        )

    @property
    def sig_oos(self) -> bool:
        return is_significant(
            n=self.re_oos_n,
            p_value=self.p_oos,
            actual=self.actual_oos_net_pp,
            null_mean=self.null_oos_mean_net_pp,
        )


# --------------------------------------------------------------------------- #
# 순수 함수 — 집계·편중·판정
# --------------------------------------------------------------------------- #


def oos_actual_symbol_mean(rows: Sequence[NullRow]) -> float:
    """따뜻한 OOS 실제 격리 순수익 %p의 심볼평균. 대상 없으면 0.0."""
    vals = [r.actual_oos_net_pp for r in rows]
    return sum(vals) / len(vals) if vals else 0.0


def leave_one_out(rows: Sequence[NullRow], drop_short: str) -> float | None:
    """한 심볼을 뺀 OOS 실제 순수익 심볼평균. 대상 심볼이 없으면 None."""
    kept = [r for r in rows if _short(r.symbol) != drop_short]
    if len(kept) == len(rows):
        return None
    return oos_actual_symbol_mean(kept)


def significant_counts(rows: Sequence[NullRow], timeframe: str) -> tuple[int, int, int]:
    """(유효 OOS 셀 수, OOS 유의 셀 수, IS 유의 셀 수) — 한 TF."""
    scoped = [r for r in rows if r.timeframe == timeframe]
    valid_oos = sum(1 for r in scoped if r.re_oos_n >= MIN_TRADES_GATE)
    sig_oos = sum(1 for r in scoped if r.sig_oos)
    sig_is = sum(1 for r in scoped if r.sig_is)
    return valid_oos, sig_oos, sig_is


def verdict(rows: Sequence[NullRow]) -> str:
    """유의 판정 — (B) 되돌림 타이밍이 무작위보다 나은가. 숫자는 전부 행에서 계산한다."""
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    per_tf: list[str] = []
    any_oos_sig = False
    for tf in timeframes:
        valid_oos, sig_oos, sig_is = significant_counts(rows, tf)
        any_oos_sig = any_oos_sig or sig_oos > 0
        per_tf.append(f"{tf} OOS 유의 {sig_oos}/{valid_oos}(유효) · IS 유의 {sig_is}")
    oos_mean = oos_actual_symbol_mean(rows)
    head = (
        "**(a) 실제 > 무작위-시각 — 되돌림 진입이 창 안 아무 시각 진입을 이긴다(적어도 한 TF).**"
        if any_oos_sig
        else ("**(b) 무의 — 되돌림 진입이 무작위-시각과 구분되지 않는다.**")
    )
    return (
        f"{head} " + " · ".join(per_tf) + f". 따뜻한 OOS 실제 격리 순수익 심볼평균 "
        f"{oos_mean:+.2f}%p. 🚨 **이 우위는 「타이밍」이 아니라 「가격」일 공산이 크다** — 무작위 "
        "시각 진입은 존에서 멀리 떨어진(1R이 큰) 자리에도 들어가 크게 잃는데, 되돌림 진입은 "
        "이겼던 좁은 레벨(작은 1R · 고정 1.5R에 가까움)로 돌아온다. 이는 WAN-131이 이미 「선별이 "
        "아니라 가격」으로 가른 그 기전이지 새 타이밍 알파가 아니다. 레벨 특정성(그 **특정** "
        "레벨이 존 안 임의 레벨보다 나은가)은 이 (a) 널이 못 가른다 — (b) 무작위 **가격** 널이 "
        "그 결정적 후속이다. ⚠️ 또한 유의여도 채택 근거가 아니다: 전부 `baseline`(닿으면 체결) "
        "낙관 렌즈 위 값이고 재진입이 그 가정에 가장 크게 의존하며(스치듯 닿은 체결), 손익은 "
        "격리 상한(동시 1포지션·자본·북 상한 미모델링)이다. 「엣지 없음」(WAN-84/88/111/114/124/"
        "151/201)은 탭 기준 진입 판정이라 이 축과 별개이며, 이 표가 유의여도 그 판정을 뒤집는 게 "
        "아니라 새 축(탭 없는 재진입)의 첫 측정이다. 채택은 재-베이스라인 = 사용자 결정."
    )


# --------------------------------------------------------------------------- #
# 실행 (칸별)
# --------------------------------------------------------------------------- #


def describe_engine() -> str:
    """이 널이 돌린 엔진 지문 — 산출물만 봐도 어떤 엔진인지 드러나게(WAN-164 패턴)."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={OrderBlockParams().combine_obs}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, limit_valid_bars={p.limit_valid_bars}, "
        f"short_enabled={p.short_enabled}"
    )


@dataclass(frozen=True)
class _Task:
    """fan-out 한 단위 = (심볼, TF) 칸 — 워커가 자기 데이터를 자기가 로드한다."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def run_cell(task: _Task, *, log: bool = True) -> NullRow | None:
    """한 칸을 돌려 (B) 재진입 매칭 널 한 줄을 낸다."""
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms, need_1m=True
    )
    if market.empty or market.df_1m.empty:
        return None
    ob = harness.detect_order_blocks(market, OrderBlockParams())
    cfg = harness.build_config(task.timeframe)
    params = harness.build_params()  # 채택 기본값(핀 없음).

    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        task.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=ob,
    )
    paired = sequence_with_candidates(candidates, cfg, market.funding_rates)

    htf_ms = timeframe_to_ms(task.timeframe)
    frame = _prepare_htf(market.htf_df)
    htf_times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    htf_closes = [float(v) for v in frame["close"].astype(float).tolist()]
    substeps = build_substeps(market.df_1m, htf_ms)
    substep_times = [s.time for s in substeps]

    boundary = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    assert boundary is not None

    # 실제 팔(= WAN-228 census와 동일) + 존별 널 표본추출 맥락을 함께 모은다.
    actual_is: list[_Reentry] = []
    actual_oos: list[_Reentry] = []
    zone_ctx: list[_ZoneNullContext] = []
    tp_entries = 0
    for cand, trade in paired:
        if cand.reason is not ExitReason.TAKE_PROFIT or cand.order_block is None:
            continue
        tp_entries += 1
        events = reentry_events(
            cand,
            parent_exit_time=trade.exit_time,
            substeps=substeps,
            substep_times=substep_times,
            htf_times=htf_times,
            htf_closes=htf_closes,
            params=params,
            cfg=cfg,
            funding_rates=market.funding_rates,
        )
        if not events:
            continue
        z_is = [e for e in events if e.entry_time < boundary]
        z_oos = [e for e in events if e.entry_time >= boundary]
        actual_is.extend(z_is)
        actual_oos.extend(z_oos)

        invalidation_time = cand.order_block.break_time if params.use_order_block_stop else None
        # 널 하위창: IS = [익절, min(경계, 무효화)) · OOS = [max(익절, 경계), 무효화).
        parent_exit = trade.exit_time
        valid_is = _valid_indices(
            parent_exit,
            boundary,
            cand_side=cand.side,
            stop_price=cand.stop_price,
            substeps=substeps,
            substep_times=substep_times,
            invalidation_time=invalidation_time,
        )
        valid_oos = _valid_indices(
            max(parent_exit, boundary - 1),
            task.end_ms,
            cand_side=cand.side,
            stop_price=cand.stop_price,
            substeps=substeps,
            substep_times=substep_times,
            invalidation_time=invalidation_time,
        )
        zone_ctx.append(
            _ZoneNullContext(
                side=cand.side,
                stop_price=cand.stop_price,
                take_profit_r=params.take_profit_r,
                valid_is_idx=valid_is,
                valid_oos_idx=valid_oos,
                k_is=len(z_is),
                k_oos=len(z_oos),
            )
        )

    actual_is_sum = sum(e.net_return_pp for e in actual_is)
    actual_oos_sum = sum(e.net_return_pp for e in actual_oos)

    # 무작위 시각 매칭 널 — 시드마다 존별 버킷 개수를 정확히 맞춰 뽑아 격리 순수익을 합한다.
    # 진입 net_pp는 (존, start)로 캐시해 같은 draw를 두 번 시뮬하지 않는다(WAN-70 성능 관행).
    def _sim_cached(ctx: _ZoneNullContext, start: int, cache: dict[int, float]) -> float:
        # 상한(무효화 시각)은 valid_indices가 이미 잘랐으므로 sim엔 invalidation_time=None.
        if start not in cache:
            net = _net_pp_of_entry(
                start=start,
                cand_side=ctx.side,
                stop_price=ctx.stop_price,
                take_profit_r=ctx.take_profit_r,
                substeps=substeps,
                htf_times=htf_times,
                htf_closes=htf_closes,
                params=params,
                cfg=cfg,
                funding_rates=market.funding_rates,
                invalidation_time=None,
            )
            cache[start] = 0.0 if net is None else net
        return cache[start]

    caches: list[dict[int, float]] = [{} for _ in zone_ctx]
    null_is_sums: list[float] = []
    null_oos_sums: list[float] = []
    for seed_idx in range(SEEDS):
        rng = random.Random(BASE_SEED + seed_idx)
        s_is = 0.0
        s_oos = 0.0
        for ci, ctx in enumerate(zone_ctx):
            if ctx.k_is and ctx.valid_is_idx:
                for j in _sample(rng, ctx.valid_is_idx, ctx.k_is):
                    s_is += _sim_cached(ctx, j, caches[ci])
            if ctx.k_oos and ctx.valid_oos_idx:
                for j in _sample(rng, ctx.valid_oos_idx, ctx.k_oos):
                    s_oos += _sim_cached(ctx, j, caches[ci])
        null_is_sums.append(s_is)
        null_oos_sums.append(s_oos)

    null_is_mean = sum(null_is_sums) / SEEDS
    null_oos_mean = sum(null_oos_sums) / SEEDS
    coverage = harness.run_once(
        market, params=params, cfg=cfg, order_block_result=ob
    ).result.metrics.funding_coverage

    row = NullRow(
        symbol=task.symbol,
        timeframe=task.timeframe,
        window_start=task.start_ms,
        window_end=task.end_ms,
        seeds=SEEDS,
        adopted_entries=len(paired),
        tp_entries=tp_entries,
        reentries_total=len(actual_is) + len(actual_oos),
        re_is_n=len(actual_is),
        actual_is_net_pp=actual_is_sum,
        null_is_mean_net_pp=null_is_mean,
        p_is=rank_p_value(actual_is_sum, null_is_sums),
        re_oos_n=len(actual_oos),
        actual_oos_net_pp=actual_oos_sum,
        null_oos_mean_net_pp=null_oos_mean,
        p_oos=rank_p_value(actual_oos_sum, null_oos_sums),
        funding_coverage=coverage,
    )
    if log:
        p_oos = f"{row.p_oos:.3f}" if row.p_oos is not None else "—"
        print(
            f"[wan231] {task.symbol} {task.timeframe}: reentries={row.reentries_total} "
            f"OOS n={row.re_oos_n} actual={row.actual_oos_net_pp:+.1f}%p "
            f"null={row.null_oos_mean_net_pp:+.1f}%p p={p_oos} "
            f"{'SIG' if row.sig_oos else '—'}",
            flush=True,
        )
    return row


def _sample(rng: random.Random, pool: Sequence[int], k: int) -> list[int]:
    """풀에서 k개 표본 — 풀이 k 이상이면 비복원, 아니면 복원(항상 k개 보장)."""
    if k <= len(pool):
        return rng.sample(list(pool), k)
    return [rng.choice(list(pool)) for _ in range(k)]


def _run_task_logged(task: _Task) -> NullRow | None:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[NullRow]:
    """9종목 × TF 칸을 돌아 매칭 널 행을 모은다.

    `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121) — (심볼, TF) 단위로만 갈라 제출 순서대로
    모으고, 각 셀의 시드 스트림은 셀 안에서 결정적이라 직렬과 행·수치가 같다.
    """
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
        results = [run_cell(task, log=log) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            results = list(executor.map(_run_task_logged, tasks))
    return [r for r in results if r is not None]


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[NullRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(NullRow.model_fields))


def cells_from_csv(path: Path) -> list[NullRow]:
    frame = pd.read_csv(path)
    # object 캐스트가 먼저다 — float 열에 바로 None을 넣으면 다시 NaN으로 강제된다(p값의
    # None이 NaN으로 되살아나 유의 판정을 오염시킨다). object 열에서라야 None이 보존된다.
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [NullRow.model_validate(rec) for rec in records]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _pp(value: float) -> str:
    return f"{value:+.1f}%p"


def _p(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "—"


def _cell_table(rows: Sequence[NullRow], timeframe: str) -> list[str]:
    scoped = sorted((r for r in rows if r.timeframe == timeframe), key=lambda r: r.symbol)
    lines = [
        f"### {timeframe}",
        "",
        "| 심볼 | (B)재진입 | OOS n | OOS 실제 | OOS 널평균 | OOS p | 유의 | "
        "IS n | IS 실제 | IS 널평균 | IS p |",
        "| -- | --: | --: | --: | --: | --: | :--: | --: | --: | --: | --: |",
    ]
    for r in scoped:
        fund = "†" if r.symbol in FUNDING_GAP_SYMBOLS else ""
        mark = "✅" if r.sig_oos else ("·" if r.re_oos_n >= MIN_TRADES_GATE else "n<20")
        lines.append(
            f"| {_short(r.symbol)}{fund} | {r.reentries_total} | {r.re_oos_n} | "
            f"{_pp(r.actual_oos_net_pp)} | {_pp(r.null_oos_mean_net_pp)} | {_p(r.p_oos)} | "
            f"{mark} | {r.re_is_n} | {_pp(r.actual_is_net_pp)} | {_pp(r.null_is_mean_net_pp)} | "
            f"{_p(r.p_is)} |"
        )
    if scoped:
        valid_oos, sig_oos, sig_is = significant_counts(scoped, timeframe)
        oos_mean = oos_actual_symbol_mean(scoped)
        loo = [
            f"−{s} {v:+.2f}%p" for s in BIAS_SYMBOLS if (v := leave_one_out(scoped, s)) is not None
        ]
        lines += [
            "",
            f"**{timeframe} 합계**: OOS 유의 **{sig_oos}/{valid_oos}**(유효 셀) · IS 유의 "
            f"{sig_is} · 따뜻한 OOS 실제 순수익 심볼평균 **{oos_mean:+.2f}%p** "
            f"(leave-one-out: {' · '.join(loo) if loo else '—'}).",
        ]
    return lines


def build_summary_markdown(rows: Sequence[NullRow], *, cells_csv: Path) -> str:
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    window = next(iter({(r.window_start, r.window_end) for r in rows}), (0, 0))
    lines = [
        "# WAN-231 — 익절 후 존 내 재진입 (B) 매칭 널 (되돌림 타이밍 vs 무작위)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`) "
        "돌리며 옛 핀은 하나도 물려받지 않는다. 렌즈 `baseline` 단독(WAN-128) · 못 박은 "
        "6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "## 이 널이 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영.",
        "",
        "## 대조군 — (a) 무작위 *시각*",
        "",
        "이슈가 (a) 무작위 시각 / (b) 무작위 가격 중 하나를 고르게 했고 **(a)를 골랐다**. "
        "이슈의 핵심 가설이 「엣지가 있다면 되돌림 타이밍에서만」이라, 「되돌림 시각 진입」 대 "
        "「되돌림이 아닌 아무 시각 진입」을 대조해야 그 가설을 검정한다. (b) 무작위 가격은 "
        "되돌림 구조를 그대로 두고 *레벨*만 묻는 더 좁은 질문이라(무작위 레벨도 자기 레벨로의 "
        "되돌림에 체결) 구조적 타이밍 엣지를 못 잡는다 — (a)가 유의일 때의 후속 정밀 분해로 "
        "남긴다.",
        "",
        "**널 정의**: 익절로 닫힌 존마다 실제 팔(WAN-228 `reentry_events`)이 IS/따뜻OOS 버킷에 "
        "각각 `k` 재진입을 낸다. 널은 같은 유효 창의 같은 버킷 하위창에서 무작위 시각을 `k`개 "
        "뽑아(개수 정확 일치) 그 서브스텝 가격에 진입하고(손절 = 존 무효화 · 목표 = 진입가 기준 "
        "1.5R) 엔진과 **동일한** `simulate_zone_limit_trade`로 청산한다. 실제와 널의 차이는 "
        "**진입 시각(과 결과 가격)** 뿐이다. 시드 "
        f"**{SEEDS}개**, 단측 순위 p = `(1 + #{{널 ≥ 실제}}) / (1 + {SEEDS})`(하한 "
        f"{1 / (SEEDS + 1):.3f}). 유의 = 거래 {MIN_TRADES_GATE}건 이상 & p ≤ {ALPHA} & "
        "실제 > 널 평균(WAN-84 게이트 + WAN-88 방향).",
        "",
        f"재현: `uv run python -m backtest.wan231_reentry_null --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` (요약만: `--from-csv`). 원자료: `{cells_csv}`. "
        f"창=[{window[0]}, {window[1]}).",
        "",
        "## 칸별 매칭 널 (실제 vs 무작위 · p값)",
        "",
        "`(B)재진입` = 익절 후 재무장이 다시 체결된 수(전 구간, census와 비트 일치) · "
        "`OOS 실제`/`IS 실제` = 그 버킷 재진입의 격리 순수익 %p 합 · `널평균` = 개수 맞춘 "
        "무작위 시각 진입의 같은 합(시드 평균) · `p` = 단측 순위 p · 유의 ✅ = n≥20 & p≤0.05 & "
        "실제>널. †=신규 종목(펀딩 0행 → 순수익 낙관, 재진입 수·p엔 실제·널 대칭이라 무관).",
        "",
    ]
    for tf in timeframes:
        lines += _cell_table(rows, tf)
        lines.append("")
    lines += [
        "## 판정 — 되돌림 타이밍이 무작위보다 나은가",
        "",
        verdict(rows),
        "",
        "⚠️ **이 표는 채택 근거가 아니라 엣지 측정이다.** 유의여도 (1) 전부 `baseline`(닿으면 "
        "체결) 낙관 렌즈 위 값이고 재진입이 그 가정에 가장 크게 의존하며(스치듯 닿은 체결), "
        "(2) §손익은 **격리 상한**(동시 1포지션·자본·북 상한 미모델링), (3) 존 선택 자체는 같은 "
        "오더블록이라 이미 엣지 없음 — 엣지가 있다면 되돌림 **타이밍**에서만 온다. 「엣지 없음」"
        "(WAN-84/88/111/114/124/151/201)은 **탭 기준 진입** 판정이라 이 축과 별개이며, 이 표가 "
        "유의여도 그 판정을 뒤집는 게 아니라 **새 축의 첫 측정**이다. 채택(층 2 resting-order "
        "sim·재무장 기본값화)은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지(큐 우선순위 "
        "WAN-98 Canceled · 라이브 충실도 WAN-45 선행). **기본값·토대 불변**(측정 전용 · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-231 (B) 재진입 매칭 널")
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
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_md = Path(args.out_md)

    if args.from_csv:
        rows = cells_from_csv(out_cells)
        print(f"[wan231] CSV에서 {len(rows)}행 로드 — 백테스트 재실행 없음")
    else:
        rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(rows).to_csv(out_cells, index=False)
        print(f"[wan231] 매칭 널 {len(rows)}행 → {out_cells}")

    if not rows:
        print("[wan231] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_summary_markdown(rows, cells_csv=out_cells), encoding="utf-8")
    print(f"[wan231] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
