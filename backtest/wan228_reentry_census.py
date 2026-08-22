"""WAN-228 — 익절 후 존 내 재진입 census ((B) 구멍의 크기 · GO/STOP 게이트).

## 무엇을 세나 (사용자 질문 "나는 B를 알아야 해", 2026-08-02)

WAN-223/226이 잰 것은 **(A)** 만료 후 재탭 실패로 첫 진입을 놓치는 구멍이었다. 이 모듈은
**(B)** 를 잰다: **익절(고정 1.5R) 후 포지션이 닫힌 뒤, 같은 존이 아직 유효한데(무효화
`break_time` 이전) 가격이 진입 지정가로 되돌아오면 재진입해야 하나?**

현행 엔진은 이 되돌림을 통째로 놓친다 — 지정가 주문은 **탭(바깥→안 전이)** 순간에만
걸리는데(`strategy/order_blocks.py`의 `inside and not _inside`), 익절 후 존 안에서 오르내리는
동안에는 새 탭이 안 생긴다. WAN-90이 익절 후 **E[러너]≈0R · 본절 복귀율 97~99%(평균회귀)**
를 실측했으니, 가격이 자주 되돌아온다는 것 자체가 (B) 기회가 자주 생긴다는 뜻이다(단 그
기회가 **수익**인지는 별개 — 아래 경고).

## 방법 — 채택 엔진 위에서 "익절마다 지정가 재무장" 시뮬레이션

WAN-223 census처럼 **핀 없이 채택 기본값**(`ConfluenceParams()`·`OrderBlockParams()`)으로
전 구간을 돌려 실제 채택 거래를 얻는다(`build_zone_limit_candidates` →
`sequence_with_candidates` = 동시 1포지션). 익절(`ExitReason.TAKE_PROFIT`)로 닫힌 거래마다:

* 그 존의 **실제 체결가**(`_Candidate.entry_price`)를 지정가로, 무효화 경계
  (`_Candidate.stop_price`)를 손절로, 고정 1.5R을 익절 목표로 삼아,
* 익절 시각 **직후**부터 존 무효화(`order_block.break_time`) 전까지 **주문을 다시 걸고**
  (`limit_valid_bars=None` = 무기한 대기 = 형성-즉시 예약의 체결 대리, WAN-223 §1 정의),
* `simulate_zone_limit_trade`(엔진과 **같은** 체결·손절·익절·무효화 로직)로 체결 여부를
  본다. 체결되면 = **(B) 후보 재진입 1건**. 익절로 닫히면 다시 무장(가격이 또 되돌아올 수
  있다), 손절(존 무효화)·미체결·데이터 끝이면 그 존은 끝난다.

핵심: **재무장은 채택 엔진이 하지 않는 유일한 동작**이라 두-패스 차이(WAN-223 (A))로는
잴 수 없다 — 그래서 익절 거래마다 재무장 루프를 돌리되, 체결·청산 판정은 **기존
시뮬레이터를 그대로 재사용**해 엔진과 갈라지지 않게 한다.

⚠️ **지정가는 고정한다(진입 지정가 = 실제 체결가)** — 이슈 §1 정의("가격이 다시 밴드
가격(진입 지정가)에 닿는")를 따른다. 봉내 라이브 밴드(`intrabar_live`)는 매 서브스텝
움직이지만, (B) **크기의 상한**을 재는 census라 재진입 문턱을 원래 체결가로 고정한다
(밴드 재산정 재진입은 층 2 sim 소관). 그래서 정적 `limit_price` 경로를 쓴다.

## §1 SIZE · §2 PnL · §3 GO/STOP

* **§1**: (B) 후보 재진입 수 ÷ 채택 진입 수(전 구간). 칸별·심볼별.
* **§2**: 후보 재진입의 손익 — 거래당 gross R(+1.5 익절 / −1.0 손절 / 데이터끝 부분 R)과
  **격리 순수익 %p**(각 재진입을 기준자본에서 독립 체결시킨 `_to_trade` 순손익 합). IS와
  따뜻한 OOS(WAN-166)로 가른다(재진입의 **진입 시각**이 평가 경계 전/후인가로 버킷).
* **§3**: (A)와 같은 자 — (B) 구멍이 채택 진입 대비 크고(≥20%) 메우면 수익이 붙는가
  (따뜻한 OOS 심볼평균 순수익 ≥ 1%p)면 GO, 작으면 STOP, 사이면 경계.

## 성격 · 경고

측정 전용. 렌즈 `baseline` 단독(WAN-128) · 못 박은 6년 창(WAN-182) · 기본값·토대 불변
(`ALPHABLOCK_LIVE_TRADING=false` 유지). ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이라
**「재진입 기회 많다 = 좋다」가 아니다**(WAN-222/223: 체결↑ ≠ 수익↑ — (A)를 메워도 수익이
종목에 갈렸다). §2 손익은 **격리 상한**이다(동시 1포지션·자본·슬롯 경합·북 상한 미모델링
= 층 2 sim 소관). 「엣지 없음」(WAN-84/88/111/114/124/151)은 다른 질문이라 불변 — 재진입은
알파가 아니라 체결/자본 배분의 모양만 바꾼다.

## 재현

```
uv run python -m backtest.wan228_reentry_census --tf 4h,1h --jobs 6
uv run python -m backtest.wan228_reentry_census --tf 15m --jobs 9   # 무거움(셀당 ~37분)
uv run python -m backtest.wan228_reentry_census --from-csv          # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import bisect
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

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
from backtest.zone_limit_backtest import (
    _Candidate,
    _IntrabarLiveLimit,
    _prepare_htf,
    _to_trade,
    build_zone_limit_candidates,
    is_same_step_take_profit,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.models import (
    ConfluenceParams,
    OrderBlockDirection,
    OrderBlockParams,
    SignalExitReason,
)
from strategy.realtime_band import RealtimeBand
from strategy.realtime_rsi import RealtimeRsi

#: 재무장 지정가 규칙 (WAN-267) — 어떤 가격에 재진입 주문을 다시 거는가.
#:
#: * ``"freeze"`` — 첫 진입가(`cand.entry_price`)를 얼려 재사용(현행 = 검산 기준점).
#:   override 안 주면(기본) wan228/231/263 CSV가 비트 단위로 재현된다.
#: * ``"zone"`` — 존 근단(`zone_limit_price` + 오프셋)에 재무장. 볼린저 재산정 없음.
#: * ``"band"`` — 재무장 순간의 봉내 라이브 밴드로 지정가 재산정(`_IntrabarLiveLimit`).
#:   ⚠️ 볼린저 규칙 3(밴드가 존 반대편이면 진입 없음) 때문에 재진입을 **아예 건너뛰는**
#:   경우가 생겨 세 팔의 체결 집합이 달라진다(combine_obs 부류 — 직접 비교 시 명시).
ReentryEntryRule = Literal["freeze", "zone", "band"]

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan228_reentry_census.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan228_reentry_census_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 기본 TF = 4h·1h(컴퓨트 실현 가능). 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h")

#: 신규 3종목 — 펀딩 0행이라 `net_return` 열이 낙관적(WAN-178 백필 전). 재진입 **수**는
#: 펀딩과 무관하므로 대리를 얹지 않는다(census 관행). 표에서 †로 표시한다.
FUNDING_GAP_SYMBOLS: frozenset[str] = frozenset(
    harness.normalize_symbol(s) for s in ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
)

#: 판정 문턱 — (B) 후보 재진입이 채택 진입 대비 차지하는 비율. WAN-223 (A)와 같은 자.
SIGNIFICANT_MISS_SHARE = 0.20
NEGLIGIBLE_MISS_SHARE = 0.05

#: 메운 재진입이 **수익도 더하는가**의 문턱(따뜻한 OOS 심볼평균 격리 순수익, %p).
MATERIAL_RETURN_DELTA_PCT = 1.0

#: 한 존에서 재무장 루프의 안전 상한(무한 루프 방지 — 실제로는 break_time이 자른다).
_MAX_REARM_PER_ZONE = 10_000


# --------------------------------------------------------------------------- #
# 재진입 한 건의 손익 (§2)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Reentry:
    """(B) 후보 재진입 1건 — 재무장한 지정가가 다시 체결된 결과."""

    entry_time: int
    is_win: bool
    """익절(1.5R)로 닫혔나. 손절·데이터끝은 False."""
    is_stop: bool
    """손절(존 무효화)로 닫혔나."""
    gross_r: float
    """비용 전 R. 익절 +1.5 · 손절 −1.0 · 데이터끝(FILLED_OPEN)은 부분 R."""
    net_return_pp: float
    """격리 순수익(%p) — 기준자본에서 독립 체결시킨 `_to_trade` 순손익 ÷ 진입 명목."""
    depth: int = 1
    """이 존 사슬 안 재진입 순번(1-indexed, WAN-267). 부모 익절 거래는 depth 0(=재진입
    아님)이라 이 열에 안 들어간다 — 승자-생존 편향 통제상 다른 모집단이다(WAN-149 §4)."""


def _direction(side: PositionSide) -> OrderBlockDirection:
    return OrderBlockDirection.BULLISH if side is PositionSide.LONG else OrderBlockDirection.BEARISH


def _iter_reentries(
    cand: _Candidate,
    *,
    parent_exit_time: int,
    substeps: Sequence[SubStep],
    substep_times: Sequence[int],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    entry_rule: ReentryEntryRule = "freeze",
    partial_take_profit_r: float | None = None,
    partial_take_profit_fraction: float = 0.5,
    breakeven_after_partial: bool = False,
    no_same_step_tp: bool = False,
    no_same_step_tp_minutes: frozenset[int] | None = None,
) -> Iterator[tuple[_Candidate, _Reentry]]:
    """익절로 닫힌 한 존의 재무장 루프 코어 — `(_Candidate, _Reentry)`를 하나씩 낸다.

    `reentry_events`(격리 손익 measurement)와 `reentry_candidates`(북 시퀀서 주입, WAN-261)가
    **같은 이 루프**를 공유한다 — 두 곳이 재무장 로직을 복제하면 갈라진다(WAN-95 교훈). 낸
    `_Candidate`는 익절·손절·데이터끝 청산이 확정된 값이라 북이 재시뮬 없이 `_to_trade`로
    배치할 수 있고, `_Reentry`는 census/널의 격리 손익 자다. 체결·손절·익절·무효화 판정은
    `simulate_zone_limit_trade`(엔진과 동일)를 그대로 쓴다. 익절로 닫히면 다시 무장,
    손절·미체결·데이터끝이면 그 존은 끝난다.

    `entry_rule`(WAN-267)이 재무장 지정가를 정한다 — `"freeze"`(기본)면 원래 체결가
    (`cand.entry_price`)로 **고정**(이슈 §1 정의, 기존 wan228/231/263 CSV 비트 재현),
    `"zone"`이면 존 근단+오프셋에 다시 걸고, `"band"`면 재무장 순간의 봉내 라이브 밴드로
    지정가를 재산정한다(`_IntrabarLiveLimit` = 엔진 본 진입과 같은 사슬). 익절 목표는 어느
    팔이든 진입가 기준 고정 1.5R이다(밴드 팔은 시뮬레이터가 체결 순간에 낸다).

    `partial_take_profit_r`·`partial_take_profit_fraction`·`breakeven_after_partial`
    (WAN-323 반익절 래더, 옵트인)은 시뮬레이터로 그대로 흘러 **재진입 거래도 base 거래와
    같은 래더 규칙**을 받는다 — 팔마다 규칙이 갈리면 "재진입만 전량 익절"인 잡종 엔진을
    재게 된다. 안 주면(기본) 예전과 **비트 단위로 같다**(기존 wan228/231/261/263/267/269/
    271/280/282 CSV 재현).
    """
    ob = cand.order_block
    if ob is None:
        return
    stop_price = cand.stop_price
    is_long = cand.side is PositionSide.LONG
    direction = _direction(cand.side)
    invalidation_time = ob.break_time if params.use_order_block_stop else None
    deviation = params.deviation_filter
    if entry_rule == "band" and deviation is None:
        return  # 재계산할 밴드가 없다 — 팔 1(band)은 볼린저 필터가 있어야 성립한다.

    # 정적 지정가(freeze·zone)는 사슬 내내 상수다. 밴드 팔은 봉내에 정해지므로 여기서 None.
    static_limit: float | None
    if entry_rule == "freeze":
        static_limit = cand.entry_price
    elif entry_rule == "zone":
        static_limit = params.apply_zone_limit_offset(params.zone_limit_price(ob), is_long=is_long)
    else:
        static_limit = None

    cursor = parent_exit_time  # 익절 시각. 재무장은 그 **직후** 서브스텝부터.
    for depth in range(1, _MAX_REARM_PER_ZONE + 1):
        start = bisect.bisect_right(substep_times, cursor)
        if start >= len(substeps):
            break
        if invalidation_time is not None and substeps[start].time >= invalidation_time:
            break
        # 재무장 봉 직전까지의 확정봉으로 RSI 시딩(엔진과 같은 규칙) — 채택 게이트가
        # `unconditional`이라 값은 안 보지만, 다른 게이트에서도 올바르게 돌게 시딩한다.
        cut = bisect.bisect_left(htf_times, substeps[start].htf_bar_time)
        rsi_state = RealtimeRsi.seed_from_closed(htf_closes[:cut], length=params.rsi_length)

        if entry_rule == "band":
            assert deviation is not None
            # 밴드를 재무장 봉 **직전까지의** 확정봉으로 시딩한다 — 20번째 표본은 현재가
            # 몫으로 비워, 엔진 본 진입(WAN-119)과 완전히 같은 사슬을 돌린다. 익절·손절은
            # 시뮬레이터가 체결 순간에 낸다(`resolve_exits`, 오버라이드 없음 = 고정 1.5R).
            live_limit = _IntrabarLiveLimit(
                band=RealtimeBand.seed_from_closed(htf_closes, deviation, end=cut),
                order_block=ob,
                is_long=is_long,
                params=params,
                stop_price=stop_price,
                lines=[],
                trigger_time=substeps[start].time,
            )
            outcome = simulate_zone_limit_trade(
                direction=direction,
                live_limit=live_limit,
                stop_price=stop_price,
                substeps=substeps,
                start=start,
                rsi_state=rsi_state,
                rsi_oversold=params.rsi_oversold,
                rsi_overbought=params.rsi_overbought,
                take_profit_price=None,
                limit_valid_bars=None,
                invalidation_time=invalidation_time,
                rsi_gate_mode=params.rsi_gate_mode,
                rsi_neutral_band=params.rsi_neutral_band,
                penetration_bps=params.fill_penetration_bps,
                partial_take_profit_r=partial_take_profit_r,
                partial_take_profit_fraction=partial_take_profit_fraction,
                breakeven_after_partial=breakeven_after_partial,
                no_same_step_tp=no_same_step_tp,
                no_same_step_tp_minutes=no_same_step_tp_minutes,
            )
        else:
            assert static_limit is not None
            risk_ref = abs(static_limit - stop_price)
            if risk_ref <= 0.0:
                break  # 1R을 못 재는 존은 재진입 손익도 못 낸다.
            take_profit_price = (
                static_limit + params.take_profit_r * risk_ref
                if is_long
                else static_limit - params.take_profit_r * risk_ref
            )
            outcome = simulate_zone_limit_trade(
                direction=direction,
                limit_price=static_limit,
                stop_price=stop_price,
                substeps=substeps,
                start=start,
                rsi_state=rsi_state,
                rsi_oversold=params.rsi_oversold,
                rsi_overbought=params.rsi_overbought,
                take_profit_price=take_profit_price,
                limit_valid_bars=None,  # 무기한 대기 = 형성-즉시 예약의 체결 대리(WAN-223 §1).
                invalidation_time=invalidation_time,
                rsi_gate_mode=params.rsi_gate_mode,
                rsi_neutral_band=params.rsi_neutral_band,
                penetration_bps=params.fill_penetration_bps,
                partial_take_profit_r=partial_take_profit_r,
                partial_take_profit_fraction=partial_take_profit_fraction,
                breakeven_after_partial=breakeven_after_partial,
                no_same_step_tp=no_same_step_tp,
                no_same_step_tp_minutes=no_same_step_tp_minutes,
            )
        if not outcome.filled or outcome.entry_time is None or outcome.entry_price is None:
            break  # NO_TOUCH / CANCELLED_INVALIDATED — 더는 되돌아오지 않았다.

        # 1R은 **실제 체결가** 기준이다 — 정적 팔은 체결가 = 지정가라 예전과 같고, 밴드
        # 팔은 봉내 확정가라 다르다. 0R이면(밴드가 손절선에 붙었다면) 손익을 못 낸다.
        risk = abs(outcome.entry_price - stop_price)
        if risk <= 0.0:
            break

        if outcome.status is ZoneLimitStatus.FILLED_EXITED:
            assert outcome.exit_time is not None and outcome.exit_price is not None
            is_win = outcome.exit_reason is SignalExitReason.TAKE_PROFIT
            # WAN-323: 본절 청산은 **존 무효화 경계를 안 건드렸다** — 우리가 스스로 일찍
            # 나온 것이라 그 존은 아직 살아 있고 재무장 대상이다. 이 구분이 없으면 래더가
            # 멀쩡한 존을 죽여 재진입을 18~20% 없애 버린다(사용자 지적 2026-08-18).
            zone_alive = is_win or outcome.exit_at_breakeven
            is_stop = outcome.exit_reason is SignalExitReason.STOP_LOSS
            exit_time, exit_price = outcome.exit_time, outcome.exit_price
            reason = ExitReason.TAKE_PROFIT if is_win else ExitReason.STOP_LOSS
        else:
            # 데이터 끝까지 보유(FILLED_OPEN) → 마지막 봉 종가로 마크. 승/패 아님.
            is_win = is_stop = False
            zone_alive = False  # 데이터 끝 — 더 볼 봉이 없다.
            exit_time, exit_price = substeps[-1].time, substeps[-1].close
            reason = ExitReason.END_OF_DATA

        gross_r = cand.side.sign * (exit_price - outcome.entry_price) / risk
        re_cand = _Candidate(
            side=cand.side,
            entry_time=outcome.entry_time,
            entry_price=outcome.entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            reason=reason,
            stop_price=stop_price,
            # 아래 셋은 진단·북 배선 전용이라 `_to_trade`가 무시한다(격리 손익 불변) —
            # `order_block`은 재진입도 같은 존을 근거로 삼음을 남기고, `trigger_time`은
            # 탭이 없는 재진입을 북의 구간 버킷(`trigger_time >= 경계`)에 올바로 넣는 키다.
            # `exit_extreme`(WAN-276/277)은 손절 봉의 불리 극값이라 재진입 손절도 base 후보와
            # 같은 시장가 슬리피지 α 사후 변환을 받을 수 있다(손절 아닌 청산이면 엔진이 None).
            # 이 값은 `slip_candidate`만 읽고 손익·시퀀싱은 무시하므로 wan261/269 북 CSV는
            # 비트 재현된다(손절 슬리피지를 안 얹으면 exit_price가 그대로다).
            order_block=ob,
            trigger_time=outcome.entry_time,
            exit_extreme=outcome.exit_extreme,
            exit_at_breakeven=outcome.exit_at_breakeven,
            # WAN-323: 래더를 켰으면 재진입 거래의 부분 청산도 북 회계로 넘긴다(안 켜면 빈 튜플).
            partial_exits=outcome.partial_exits,
            # WAN-336 순수 관측: 재진입 거래도 base 후보와 **같은 술어**로 라벨을 단다 —
            # 한쪽만 달면 「같은 분 익절」 인구조사가 재진입을 통째로 놓친다.
            same_step_take_profit=is_same_step_take_profit(outcome.entry_time, exit_time, reason),
        )
        # 격리 순손익: 기준자본에서 독립 체결(동시 1포지션·자본 경합 미반영 = 상한).
        trade = _to_trade(re_cand, cfg.initial_capital, cfg, funding_rates)
        net_return_pp = (trade.return_pct * 100.0) if trade is not None else 0.0
        yield (
            re_cand,
            _Reentry(
                entry_time=outcome.entry_time,
                is_win=is_win,
                is_stop=is_stop,
                gross_r=gross_r,
                net_return_pp=net_return_pp,
                depth=depth,
            ),
        )
        if not zone_alive:
            break  # 손절(존 무효화)·데이터끝이면 이 존은 끝. 익절이라야 또 무장한다.
        cursor = exit_time


def reentry_events(
    cand: _Candidate,
    *,
    parent_exit_time: int,
    substeps: Sequence[SubStep],
    substep_times: Sequence[int],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    entry_rule: ReentryEntryRule = "freeze",
) -> list[_Reentry]:
    """익절로 닫힌 한 존을 익절 직후부터 무효화까지 지정가 재무장해 재진입 손익을 센다.

    ⚠️ **래더(WAN-323)를 여기로는 흘리지 않는다** — 이 함수는 WAN-228 census의 격리 손익
    자이고 그 CSV는 전량 익절 기록으로 얼어붙어 있다. 래더가 필요한 곳은 북에 주입하는
    `reentry_candidates` 쪽이다."""
    return [
        event
        for _cand, event in _iter_reentries(
            cand,
            parent_exit_time=parent_exit_time,
            substeps=substeps,
            substep_times=substep_times,
            htf_times=htf_times,
            htf_closes=htf_closes,
            params=params,
            cfg=cfg,
            funding_rates=funding_rates,
            entry_rule=entry_rule,
        )
    ]


def reentry_candidates(
    cand: _Candidate,
    *,
    parent_exit_time: int,
    substeps: Sequence[SubStep],
    substep_times: Sequence[int],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    entry_rule: ReentryEntryRule = "freeze",
    partial_take_profit_r: float | None = None,
    partial_take_profit_fraction: float = 0.5,
    breakeven_after_partial: bool = False,
    no_same_step_tp: bool = False,
    no_same_step_tp_minutes: frozenset[int] | None = None,
) -> list[_Candidate]:
    """익절 후 재무장 재진입을 **북 시퀀서에 주입할 `_Candidate`로** 낸다(WAN-261).

    `reentry_events`와 같은 재무장 루프(`_iter_reentries`)를 쓰되 손익 요약이 아니라 청산이
    확정된 후보를 돌려준다 — 북(`run_leverage_book`)이 채택 지정가(재탭) 후보와 함께 한
    지갑에서 시퀀싱하면 칸당 1포지션·공유 자본·명목 상한 제약이 자연히 적용된다. 청산이
    미리 정해져 있어 북은 재시뮬 없이 `_to_trade`로 배치한다(base 후보와 같은 규약).

    `entry_rule`(WAN-269, 옵트인)은 `_iter_reentries`로 그대로 흘러 재무장 지정가를 정한다 —
    `"freeze"`(기본)면 첫 체결가를 얼려 **기존 wan261/262 북 CSV가 비트 재현**되고, `"band"`면
    재무장 순간의 봉내 라이브 밴드로 지정가를 재산정한다(WAN-267 격리 분해의 리더 팔을 북에
    얹는 경로). 재무장 루프가 하나뿐이라 census·격리 널·북 주입이 같은 규칙을 공유한다.

    `no_same_step_tp`(WAN-336, 옵트인)도 루프로 그대로 흘러 **재진입 거래가 base 거래와 같은
    자를 받는다** — 한쪽만 걸면 「base는 진입 스텝 익절 금지, 재진입은 허용」인 잡종 팔을 재게
    된다. 끄면(기본) 예전과 비트 단위로 같다.

    래더 셋(`partial_take_profit_r`·`partial_take_profit_fraction`·`breakeven_after_partial`,
    WAN-323 옵트인)도 마찬가지로 루프를 그대로 탄다 — **재진입 거래도 base 거래와 같은 래더
    규칙**을 받는다. ⚠️ **여기는 고쳐진 자리다(WAN-345)**: WAN-323 커밋 `af1a550`이 시그니처만
    넓히고 배선을 빠뜨려, 래더를 켠 북 팔에서도 **재진입 거래만 조용히 전량 익절**로 돌았다
    (`_iter_reentries` 독스트링이 「팔마다 규칙이 갈리면 잡종 엔진」이라 적어 둔 바로 그 상태).
    회귀 테스트는 인자 전달 여부가 아니라 **재진입 거래에 부분 청산이 실제로 생기는지**로
    건다 — 넘기는 줄만 보는 테스트는 같은 실패를 또 통과시킨다.

    🚨 **WAN-323 §2 · WAN-330의 공개 CSV는 이 결함 위에서 산출됐다** — 그 표들의 재진입
    거래는 래더를 안 받았다. 고친 엔진으로 재산출할지는 **사용자 결정**이라(팔당 66분 ×
    6팔 = 7시간+) WAN-345는 재산출 대신 두 리포트 md와 CLAUDE.md에 시점 배너를 달았다."""
    # WAN-346: 재진입 후보에 라벨을 단다(`is_reentry=True`) — `_segment_cells`가 base
    # 재탭 후보와 합친 뒤에는 둘을 되가를 방법이 없었다. 순수 라벨이라 체결·청산·손익·
    # 후보 집합 어디에도 안 쓰이고, 라벨을 다는 **한 곳**이 여기다(합류 지점에서 달면
    # 다른 호출부가 라벨 없는 재진입을 흘린다).
    return [
        replace(re_cand, is_reentry=True)
        for re_cand, _event in _iter_reentries(
            cand,
            parent_exit_time=parent_exit_time,
            substeps=substeps,
            substep_times=substep_times,
            htf_times=htf_times,
            htf_closes=htf_closes,
            params=params,
            cfg=cfg,
            funding_rates=funding_rates,
            entry_rule=entry_rule,
            partial_take_profit_r=partial_take_profit_r,
            partial_take_profit_fraction=partial_take_profit_fraction,
            breakeven_after_partial=breakeven_after_partial,
            no_same_step_tp=no_same_step_tp,
            no_same_step_tp_minutes=no_same_step_tp_minutes,
        )
    ]


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CellRow(BaseModel):
    """한 (심볼, TF)의 (B) 재진입 census 한 줄 — 전 구간 + IS/따뜻OOS 손익 버킷.

    §2 버킷은 재진입의 **진입 시각**이 평가 경계(WAN-166 따뜻한 OOS 경계) 전/후인가로
    가른다 — 전 구간을 연속으로 돌리므로 경계 이후 재진입은 자연히 '따뜻'하다(앞 데이터가
    존·지표를 데웠다).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    window_start: int
    window_end: int
    window_days: float

    # §1 — 크기(전 구간)
    adopted_entries: int
    """채택 엔진 진입 수(동시 1포지션 시퀀싱 후)."""
    tp_entries: int
    """그중 익절(1.5R)로 닫힌 수 = 재무장 대상 존."""
    reentries_total: int
    """(B) 후보 재진입 수(전 구간). 익절마다 재무장해 다시 체결된 건의 합."""

    # §2 — 손익(IS / 따뜻 OOS 버킷). n·wins·stops·gross_r 합·격리순수익 %p 합.
    re_is_n: int
    re_is_wins: int
    re_is_stops: int
    re_is_gross_r_sum: float
    re_is_net_pp_sum: float
    re_oos_n: int
    re_oos_wins: int
    re_oos_stops: int
    re_oos_gross_r_sum: float
    re_oos_net_pp_sum: float

    funding_coverage: float | None
    """펀딩 커버리지. 신규 3종목은 0.0(순수익 열 낙관 — 재진입 수엔 무관)."""

    @property
    def reentry_share(self) -> float | None:
        """(B) 재진입 ÷ 채택 진입. 진입 0이면 None."""
        return self.reentries_total / self.adopted_entries if self.adopted_entries else None

    @property
    def reentries_per_tp(self) -> float | None:
        """익절 존 하나당 평균 재진입 수. 익절 0이면 None."""
        return self.reentries_total / self.tp_entries if self.tp_entries else None

    def _mean(self, total: float, n: int) -> float | None:
        return total / n if n else None

    @property
    def gross_r_mean_is(self) -> float | None:
        return self._mean(self.re_is_gross_r_sum, self.re_is_n)

    @property
    def gross_r_mean_oos(self) -> float | None:
        return self._mean(self.re_oos_gross_r_sum, self.re_oos_n)

    @property
    def win_rate_is(self) -> float | None:
        decided = self.re_is_wins + self.re_is_stops
        return self.re_is_wins / decided if decided else None

    @property
    def win_rate_oos(self) -> float | None:
        decided = self.re_oos_wins + self.re_oos_stops
        return self.re_oos_wins / decided if decided else None


# --------------------------------------------------------------------------- #
# 순수 함수 (테스트가 여기를 고정한다)
# --------------------------------------------------------------------------- #


def aggregate_symbol_mean(rows: Sequence[CellRow], field: str) -> float:
    """심볼평균(단순) — 한 TF 안 심볼들의 평균. 대상 없으면 0.0."""
    vals = [float(getattr(r, field)) for r in rows]
    return sum(vals) / len(vals) if vals else 0.0


def _oos_net_pp_symbol_mean(rows: Sequence[CellRow]) -> float:
    """따뜻한 OOS 격리 순수익 %p의 심볼평균(한 셀당 그 셀 재진입 순수익 합)."""
    vals = [r.re_oos_net_pp_sum for r in rows]
    return sum(vals) / len(vals) if vals else 0.0


def verdict(rows: Sequence[CellRow]) -> str:
    """GO/STOP 판정 — (B) 구멍이 층 2(resting-order sim)를 지을 만큼 큰가.

    자 둘: (1) 재진입이 채택 진입 대비 차지하는 비율(전 구간), (2) 메운 재진입이 수익도
    더하는가(따뜻한 OOS 심볼평균 격리 순수익 %p). 숫자는 전부 행에서 계산한다.
    """
    total_adopted = sum(r.adopted_entries for r in rows)
    total_reentries = sum(r.reentries_total for r in rows)
    share = total_reentries / total_adopted if total_adopted else 0.0
    oos_net = _oos_net_pp_symbol_mean(rows)
    coords = (
        f"(B) 재진입 = 채택 진입의 **{share * 100:.1f}%** "
        f"({total_reentries}건 / 진입 {total_adopted}건) · 따뜻한 OOS 격리 순수익 "
        f"**{oos_net:+.2f}%p**(심볼평균)"
    )
    material = oos_net >= MATERIAL_RETURN_DELTA_PCT
    if share >= SIGNIFICANT_MISS_SHARE and material:
        return (
            f"**(a) GO — 구멍이 크고 수익도 는다.** {coords}. (B) 재진입이 진입의 "
            f"{SIGNIFICANT_MISS_SHARE * 100:.0f}% 이상이고 따뜻한 OOS 격리 순수익이 "
            f"{MATERIAL_RETURN_DELTA_PCT:.0f}%p 이상 — 층 2(resting-order sim)를 지어 "
            "대조할 값이 있다. ⚠️ 단 채택은 재-베이스라인 = 사용자 결정이고, 늘어난 체결은 "
            "전부 `baseline` 낙관 렌즈 위 값이며 손익은 격리 상한이다(WAN-98 Canceled)."
        )
    if share < NEGLIGIBLE_MISS_SHARE or not material:
        why = (
            f"(B) 재진입이 진입의 {NEGLIGIBLE_MISS_SHARE * 100:.0f}% 미만"
            if share < NEGLIGIBLE_MISS_SHARE
            else f"따뜻한 OOS 격리 순수익이 {MATERIAL_RETURN_DELTA_PCT:.0f}%p 미만"
        )
        return (
            f"**(b) STOP — 여기서 멈추고 기록만.** {coords}. {why}이라, 익절 후 재무장 "
            "모델(층 2)을 지어도 실익이 작다(WAN-222/223의 '체결은 늘지만 수익은 안 "
            "따라온다'와 정합). 층 2 착수는 사용자가 이 크기를 알고도 원하면 별도로."
        )
    return (
        f"**(c) 경계 — 크기를 알고 결정할 것.** {coords}. 구멍이 무시할 수준은 아니나 "
        f"일상({SIGNIFICANT_MISS_SHARE * 100:.0f}% 이상)도 아니거나 수익이 애매하다. "
        "층 2 착수는 이 크기를 알고 내리는 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 실행 (칸별)
# --------------------------------------------------------------------------- #


def describe_engine() -> str:
    """이 census가 돌린 엔진 지문 — 산출물만 봐도 어떤 엔진인지 드러나게(WAN-164 패턴)."""
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


def run_cell(task: _Task, *, log: bool = True) -> CellRow | None:
    """한 칸을 돌려 (B) 재진입 census 한 줄을 낸다."""
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

    # 따뜻한 OOS 평가 경계(전체 창의 IS_FRACTION 지점) — 재진입 진입 시각으로 IS/OOS 버킷.
    boundary = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    assert boundary is not None  # WARM_OOS_SEGMENT엔 eval_start_fraction이 있다.

    tp_entries = 0
    reentries: list[_Reentry] = []
    for cand, trade in paired:
        if cand.reason is not ExitReason.TAKE_PROFIT:
            continue
        tp_entries += 1
        reentries.extend(
            reentry_events(
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
        )

    is_bucket = [r for r in reentries if r.entry_time < boundary]
    oos_bucket = [r for r in reentries if r.entry_time >= boundary]
    window_days = (task.end_ms - task.start_ms) / 86_400_000.0
    # run_once로 채택 성과의 펀딩 커버리지만 확인(재진입 수엔 무관, 열 낙관 표시용).
    coverage = harness.run_once(
        market, params=params, cfg=cfg, order_block_result=ob
    ).result.metrics.funding_coverage

    row = CellRow(
        symbol=task.symbol,
        timeframe=task.timeframe,
        window_start=task.start_ms,
        window_end=task.end_ms,
        window_days=window_days,
        adopted_entries=len(paired),
        tp_entries=tp_entries,
        reentries_total=len(reentries),
        re_is_n=len(is_bucket),
        re_is_wins=sum(1 for r in is_bucket if r.is_win),
        re_is_stops=sum(1 for r in is_bucket if r.is_stop),
        re_is_gross_r_sum=sum(r.gross_r for r in is_bucket),
        re_is_net_pp_sum=sum(r.net_return_pp for r in is_bucket),
        re_oos_n=len(oos_bucket),
        re_oos_wins=sum(1 for r in oos_bucket if r.is_win),
        re_oos_stops=sum(1 for r in oos_bucket if r.is_stop),
        re_oos_gross_r_sum=sum(r.gross_r for r in oos_bucket),
        re_oos_net_pp_sum=sum(r.net_return_pp for r in oos_bucket),
        funding_coverage=coverage,
    )
    if log:
        share = row.reentry_share
        share_txt = f"{share * 100:.1f}%" if share is not None else "—"
        print(
            f"[wan228] {task.symbol} {task.timeframe}: adopted={row.adopted_entries} "
            f"tp={row.tp_entries} reentries={row.reentries_total} ({share_txt}) "
            f"oos_net={row.re_oos_net_pp_sum:+.1f}%p",
            flush=True,
        )
    return row


def _run_task_logged(task: _Task) -> CellRow | None:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[CellRow]:
    """9종목 × TF 칸을 돌아 census 행을 모은다.

    `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121) — (심볼, TF) 단위로만 갈라 제출
    순서대로 모으므로 직렬과 행·순서가 같다.
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


def cells_to_frame(rows: Sequence[CellRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CellRow.model_fields))


def cells_from_csv(path: Path) -> list[CellRow]:
    frame = pd.read_csv(path)
    return [CellRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "—"


def _r(value: float | None) -> str:
    return f"{value:+.2f}R" if value is not None else "—"


def _cell_table(rows: Sequence[CellRow], timeframe: str) -> list[str]:
    scoped = sorted((r for r in rows if r.timeframe == timeframe), key=lambda r: r.symbol)
    lines = [
        f"### {timeframe}",
        "",
        "| 심볼 | 채택진입 | 익절존 | (B)재진입 | 재진입비율 | 존당재진입 | "
        "OOS n | OOS 평균R | OOS 승률 | OOS 격리순수익 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for r in scoped:
        fund = "†" if r.symbol in FUNDING_GAP_SYMBOLS else ""
        rpt = f"{r.reentries_per_tp:.2f}" if r.reentries_per_tp is not None else "—"
        lines.append(
            f"| {_short(r.symbol)}{fund} | {r.adopted_entries} | {r.tp_entries} | "
            f"**{r.reentries_total}** | {_pct(r.reentry_share)} | {rpt} | "
            f"{r.re_oos_n} | {_r(r.gross_r_mean_oos)} | {_pct(r.win_rate_oos)} | "
            f"{r.re_oos_net_pp_sum:+.1f}%p |"
        )
    if scoped:
        tot_adopted = sum(r.adopted_entries for r in scoped)
        tot_re = sum(r.reentries_total for r in scoped)
        share = tot_re / tot_adopted if tot_adopted else 0.0
        oos_net = _oos_net_pp_symbol_mean(scoped)
        lines += [
            "",
            f"**{timeframe} 합계**: (B) 재진입 {tot_re}건 / 채택 진입 {tot_adopted}건 "
            f"= **{share * 100:.1f}%** · 따뜻한 OOS 격리 순수익 심볼평균 "
            f"**{oos_net:+.2f}%p**.",
        ]
    return lines


def build_summary_markdown(rows: Sequence[CellRow], *, cells_csv: Path) -> str:
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    window = next(iter({(r.window_start, r.window_end) for r in rows}), (0, 0))
    lines = [
        "# WAN-228 — 익절 후 존 내 재진입 census ((B) 구멍의 크기 · GO/STOP 게이트)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`) "
        "돌리며 옛 핀은 하나도 물려받지 않는다. 렌즈 `baseline` 단독(WAN-128) · 못 박은 "
        "6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "## 이 census가 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영.",
        "",
        "## 방법 — 익절마다 지정가 재무장",
        "",
        "채택 엔진으로 전 구간을 돌려 실제 채택 거래를 얻고, 익절(1.5R)로 닫힌 거래마다 그 "
        "존의 **실제 체결가**를 지정가로, 무효화 경계를 손절로, 고정 1.5R을 익절로 삼아 익절 "
        "**직후**부터 존 무효화(`break_time`)까지 **주문을 다시 걸고**(`limit_valid_bars=None`) "
        "`simulate_zone_limit_trade`(엔진과 동일)로 체결을 본다. 체결 = (B) 후보 재진입 1건. "
        "익절로 닫히면 다시 무장, 손절·미체결·데이터끝이면 그 존은 끝. 지정가는 원래 체결가로 "
        "**고정**한다(이슈 §1 정의 · 크기의 상한 · 밴드 재산정 재진입은 층 2 sim 소관).",
        "",
        "§2 손익은 IS와 **따뜻한 OOS**(WAN-166)로 가른다 — 재진입의 **진입 시각**이 평가 "
        "경계 전/후인가로 버킷(전 구간을 연속으로 돌리므로 경계 이후는 자연히 '따뜻'하다). "
        "`격리 순수익`은 각 재진입을 기준자본에서 독립 체결시킨 `_to_trade` 순손익이다 — "
        "동시 1포지션·자본·슬롯 경합·북 상한을 모델링하지 않는 **격리 상한**이다(층 2 sim 소관).",
        "",
        f"재현: `uv run python -m backtest.wan228_reentry_census --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` (요약만: `--from-csv`). 원자료: `{cells_csv}`. "
        f"창=[{window[0]}, {window[1]}).",
        "",
        "## 칸별 census (§1 크기 + §2 따뜻 OOS 손익)",
        "",
        "`채택진입` = 동시 1포지션 채택 거래 수 · `익절존` = 그중 1.5R 익절로 닫힌(재무장 "
        "대상) 수 · `(B)재진입` = 익절 후 재무장이 다시 체결된 수(전 구간) · `재진입비율` = "
        "(B)재진입 ÷ 채택진입 · `OOS 평균R`/`승률`/`격리순수익` = 따뜻한 OOS 버킷의 재진입 "
        "손익(승률은 데이터끝 보유 제외). †=신규 종목(펀딩 0행 → 순수익 낙관, 재진입 수엔 무관).",
        "",
    ]
    for tf in timeframes:
        lines += _cell_table(rows, tf)
        lines.append("")
    lines += [
        "## 판정 — 층 2(resting-order sim)를 지을 값이 있는가",
        "",
        verdict(rows),
        "",
        f"판정 자: (B)재진입 ÷ 채택진입 ≥ {SIGNIFICANT_MISS_SHARE * 100:.0f}% **그리고** 따뜻한 "
        f"OOS 격리 순수익 심볼평균 ≥ {MATERIAL_RETURN_DELTA_PCT:.0f}%p → (a) GO · "
        f"< {NEGLIGIBLE_MISS_SHARE * 100:.0f}% **또는** 순수익 < {MATERIAL_RETURN_DELTA_PCT:.0f}%p "
        "→ (b) STOP · 사이 → (c). 문턱은 코드 상수다(`SIGNIFICANT_MISS_SHARE`·"
        "`NEGLIGIBLE_MISS_SHARE`·`MATERIAL_RETURN_DELTA_PCT`).",
        "",
        "⚠️ **이 표는 채택 근거가 아니라 크기 조사다** — (B) 구멍이 커도 「익절 후 재무장을 "
        "채택하라」가 아니고(그 손익·라이브 충실도는 층 2 sim·WAN-45 소관), 「엣지 없음」"
        "(WAN-84/88/111/114/124/151)도 그대로다. 늘어난 체결·손익은 전부 `baseline`(닿으면 "
        "체결) 낙관 렌즈 위 값이고 §2는 **격리 상한**이라(동시 1포지션·자본·북 상한 미모델링) "
        "큐 우선순위(WAN-98 Canceled) 실측 없이는 이점 검증이 반쪽이다. **기본값·토대 불변**"
        "(측정 전용 · `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-228 (B) 익절 후 재진입 census")
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
        print(f"[wan228] CSV에서 {len(rows)}행 로드 — 백테스트 재실행 없음")
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
        print(f"[wan228] census {len(rows)}행 → {out_cells}")

    if not rows:
        print("[wan228] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_summary_markdown(rows, cells_csv=out_cells), encoding="utf-8")
    print(f"[wan228] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
