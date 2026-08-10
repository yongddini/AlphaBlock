"""WAN-280 — band 재진입 존 「N회 지지 후 숏 전환」 (측정 전용 · 옵트인 · 엔진 기본값 불변).

## 무엇을 묻나 (사용자 아이디어 2026-08-10)

채택 엔진은 지지 존에서 익절 후 band(볼린저 재산정) 재진입으로 **롱**을 반복한다(WAN-273).
사용자 가설: **존이 여러 번 지지(재진입)될수록 약해지니, N회 지지 후에는 롱을 멈추고
오더블록 윗경계에서 숏으로 갈아타 존이 깨질 때까지 숏을 반복하면 그 붕괴를 먹을 수 있다.**

핵심 질문(WAN-278 버퍼와 같은 구조의 순효과): 존은 결국 거의 다 깨지므로(WAN-149 무효화
93~96%) 언젠가 숏 하나가 붕괴를 크게 먹는데, **그 붕괴 한 방이 그 전까지 존이 버티며 난
숏 손절들을 덮고도 남느냐**. 「엣지 발견」이 아니라 「이 전환이 돈이 되나」를 답하는 측정이다.

⚠️ **사전 증거는 유리하지 않다**: WAN-149(존 무효화 위험률이 지지 회차와 무관하게 ~72%로
평평 → 「여러 번 지지 = 더 잘 깨짐」 (b) 기각) · 숏 축(WAN-89/145/164 = 하락장 손실은 아니나
무작위 대비 엣지 미확인). 그래도 이 손익비 조합의 실손익은 잰 적이 없다 — 이 표가 처음이다.

## 전략 스펙 (사용자 정의 · 확정)

* 첫진입 + 재진입 **N회**까지 = 현행처럼 **롱**(band 재진입, WAN-228/261/269 재무장 루프).
* N회째부터 = 그 존에서 **롱 중단, 숏 전환.** 진입 = 오더블록 **윗경계**(존 상단 `ob.top`)
  지정가 · **1R = 존 너비**(`ob.top − ob.bottom`) · 손절 = 진입 **+1R**(존 위) · 익절 = 진입
  **−1.5R**(존 아래, `take_profit_r` 배수 = 손익비 1:1.5).
* 숏이 손절나도(존이 또 버텨도) **존 무효화까지 윗경계 숏 재시도 반복**(롱 재진입이 익절 후
  재무장하듯, 숏도 손절 후 재무장). 숏이 익절(붕괴 포착)·미체결·존 무효화·데이터끝이면 그
  존은 끝난다.
* 팔: **N ∈ {1, 2, 3}** 각각 별도(+ 대조 **N=0** = 첫진입 익절 후 바로 숏 전환).

### ⚠️ 모델링 결정 — 숏 재무장의 「진짜 재-탭」 게이트

숏 지정가는 존 **상단**(`ob.top`)에 걸린다. 숏이 손절나면(가격이 `top+1R` 위로 감) 그 순간
가격은 이미 지정가 위에 있어, 다음 서브스텝에서 `high >= top`이 곧바로 참이라 **즉시 재체결
→ 즉시 재손절**이 무한 반복되는 시뮬 인공물이 생긴다(가격이 위로 달아나면 `break_time`이
없어 무한 손절). 실전의 「윗경계 숏 재시도」는 **가격이 상단으로 되돌아오는 재-탭**을 기다리는
것이므로, 재무장은 가격이 상단 **이하로 되돌아온**(`low <= ob.top`, 존 안으로 복귀) 봉부터만
무장한다(`_next_short_arm`). 이 게이트가 없으면 위로 달아난 존이 무한 숏 손실을 낸다 —
롱 재무장(지정가가 상단 아래라 되돌림을 이미 요구)에는 없는 비대칭이라 숏에만 배선한다.

## 작업 범위 (측정 전용 · 옵트인 · 엔진 기본값 불변)

1. **측정 모듈** — 채택 band 재진입 엔진으로 전 구간을 돌려 존별 재진입 회차를 얻고(WAN-228/
   261/269 machinery 재사용), 회차 N 도달 시 롱을 끊고 위 스펙대로 숏 후보를 반복 생성해
   `simulate_zone_limit_trade`(엔진 동일)로 체결·청산. `short_enabled` 기본값 **불변**.
2. **판정 = 북 비교(정본)**: 전환 규칙 넣은 북 vs **현행 롱-온리 북**(= 채택 band 재진입 북
   = 인자 없는 `backtest.run --oos-warm`, WAN-273). total_return·MDD·수익/MDD·최대 동시
   리스크·청산. N별로.
3. **첫 진단(격리)**: 숏 거래만 per-cell 단독으로 떼어 「숏이 애초에 돈이 되나」 — 표본 수·
   승률·`mean_net_r`(실현손익 ÷ 리스크 금액)·청산 사유(붕괴익절/위이탈손절/미청산). 격리는
   실매매가 아니라 진단이다(실매매는 롱↔숏 전환이라 동시 보유 없음).
4. **분해 열**: N 도달 존 수 → 실제 숏 체결 수(N 커질수록 급감, 20거래 게이트 자동) · 존당
   반복 숏 횟수 · 붕괴로 익절한 숏의 R 합 vs 버티는 동안 난 숏 손절 R 합 = 순효과.
5. **좌표**: 9종목 · 못 박은 6년(WAN-182) · 15m·1h·2h·4h(WAN-252) · IS·oos_warm(WAN-166) ·
   핀 없음(`ConfluenceParams()` = 오늘 엔진 · 재진입 ON band) · `baseline`(+`--fill pen_5bp`
   옵트인 스트레스) · ETH·SOL·DOGE leave-one-out · 신규 3종목 펀딩 대리(BTC 도너).

## 성격 · 경고 (그대로 옮길 것)

측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`·채택 북 cap_only
5배) 돌리며 옛 핀은 하나도 물려받지 않는다. `short_enabled=False` 유지(측정용 숏이지
재활성화 아님). ⚠️ 전부 `baseline`(닿으면 체결) 낙관 위 값 — 숏 지정가·존 경계 체결이
「닿으면 체결」이라 큐 우선순위(WAN-98 Canceled) 미모델. 총수익%는 복리 착시라 판단은 MDD·
최대 동시 리스크·청산으로 한다(WAN-213/90). **「엣지 없음」(WAN-84/88/111/114/124/151/201/
248) 불변** — 순 net 플러스여도 거래 관리(위험의 모양, WAN-90)이지 진입 알파가 아니며
**매칭 널은 아니다**(후속). 채택은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지.

## 재현

```
uv run python -m backtest.wan280_reentry_short_transition --tf 4h,1h,2h --jobs 6
uv run python -m backtest.wan280_reentry_short_transition --tf 15m --append --jobs 9   # 무거움
uv run python -m backtest.wan280_reentry_short_transition --fill pen_5bp --append   # 스트레스
uv run python -m backtest.wan280_reentry_short_transition --from-csv                    # 요약만
```
"""

from __future__ import annotations

import argparse
import bisect
import math
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import book_cli, harness
from backtest.book_cli import BookRunRow
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    WARM_OOS_SEGMENT,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.run import ADOPTED_BOOK, parse_date_ms
from backtest.substep import (
    SubStep,
    ZoneLimitStatus,
    build_substeps,
    simulate_zone_limit_trade,
)
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import (
    IS_SEGMENT,
    OOS_SEGMENT,
    CellPayload,
    _short,
)
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan228_reentry_census import ReentryEntryRule, _iter_reentries
from backtest.zone_limit_backtest import (
    _Candidate,
    _prepare_htf,
    _to_trade,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    SignalExitReason,
)
from strategy.realtime_rsi import RealtimeRsi

REPORTS_DIR = Path("backtest/reports")
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan280_short_transition_book.csv"
DEFAULT_DIAG_CSV = REPORTS_DIR / "wan280_short_transition_diag.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan280_short_transition_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
ALL_SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS

#: 기본 TF = 4h·1h·2h(컴퓨트 실현 가능). 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "2h")

#: 지지 회차 팔. N=0은 첫진입 익절 후 바로 숏 전환(대조), N∈{1,2,3}은 N회 롱 재진입 후 전환.
SUPPORT_NS: tuple[int, ...] = (0, 1, 2, 3)

#: 롱-온리(현행 채택 북) 팔의 이름. 북 비교의 기준선이다.
LONG_ONLY_ARM = "long_only"

#: 정본 구간 — WAN-166 규약(oos_warm 주 · oos 스트레스).
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)
_CANDIDATE_SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS)

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 셀·스코프는 판정에서 뺀다.
MIN_TRADES = 20

#: 재무장 안전 상한(무한 루프 방지 — 실제로는 break_time·재-탭 게이트가 자른다).
_MAX_REARM_PER_ZONE = 10_000

#: 신규 3종목 — 펀딩 0행이라 순수익 열이 낙관적(WAN-178 백필 전, †로 표시).
FUNDING_GAP_SYMBOLS: frozenset[str] = frozenset(
    harness.normalize_symbol(s) for s in ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
)

#: leave-one-out 대상(편중 진단, 완료기준 5).
LOO_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE")


def _arm_name(support_n: int) -> str:
    return f"N{support_n}"


# --------------------------------------------------------------------------- #
# 숏 스펙 기하 (테스트가 여기를 고정한다 — 완료기준 3)
# --------------------------------------------------------------------------- #


def short_zone_geometry(ob: OrderBlock, params: ConfluenceParams) -> tuple[float, float, float]:
    """존 상단 숏의 (진입 지정가, 손절가, 익절 목표가). 사용자 스펙 그대로.

    진입 = 오더블록 **윗경계**(`ob.top`) · 1R = 존 너비(`ob.top − ob.bottom`) · 손절 = 진입
    **+1R**(존 위) · 익절 = 진입 **−`take_profit_r`R**(존 아래, 채택 1.5R = 손익비 1:1.5).
    존 너비가 0 이하면 1R을 못 재므로 호출부가 이 존을 건너뛴다(여기선 그대로 낸다).
    """
    width = ob.top - ob.bottom
    entry = ob.top
    stop = ob.top + width
    take_profit = ob.top - params.take_profit_r * width
    return entry, stop, take_profit


@dataclass(frozen=True)
class _ShortEvent:
    """윗경계 숏 한 건(진단·분해용) — 붕괴 익절 / 위이탈 손절 / 미청산."""

    entry_time: int
    is_win: bool
    """붕괴로 익절(진입−1.5R)했나 = 존이 아래로 깨졌나."""
    is_stop: bool
    """위이탈 손절(진입+1R)했나 = 존이 또 버텨(위로) 튕겼나."""
    gross_r: float
    """비용 전 R. 붕괴 익절 +1.5 · 위이탈 손절 −1.0 · 데이터끝(FILLED_OPEN)은 부분 R."""
    net_return_pp: float
    """격리 순수익(%p) — 기준자본에서 독립 체결시킨 `_to_trade` 순손익 ÷ 진입 명목."""
    net_r: float
    """격리 순 R = 실현손익 ÷ 리스크 금액(WAN-154 정의). 미체결(_to_trade None)이면 0."""
    depth: int
    """이 존 숏 사슬 안 순번(1-indexed) = 존당 반복 숏 횟수 계량."""


# --------------------------------------------------------------------------- #
# 숏 재무장 사슬 (스펙 코어)
# --------------------------------------------------------------------------- #


def _net_r(trade_return_pct: float, entry_price: float, stop_price: float) -> float:
    """격리 순 R = 순수익률 × 진입명목 ÷ 리스크 금액 = return_pct × entry ÷ |entry−stop|.

    `_to_trade.return_pct`는 실현손익 ÷ 진입 명목이고 리스크 금액 = 수량 × |entry−stop| =
    (명목/entry) × |entry−stop| 이므로, net_r = return_pct ÷ (|entry−stop| ÷ entry)다.
    수량이 상쇄돼 사이징과 무관한 거래당 R을 낸다(WAN-154 mean_net_r와 같은 자).
    """
    risk_frac = abs(entry_price - stop_price) / entry_price
    return trade_return_pct / risk_frac if risk_frac > 0 else 0.0


def _next_short_arm(
    substep_times: Sequence[int],
    substeps: Sequence[SubStep],
    *,
    after_time: int,
    entry_limit: float,
    invalidation_time: int | None,
) -> int | None:
    """숏을 다시 무장할 첫 서브스텝 인덱스 — 가격이 상단 **이하로 되돌아온**(재-탭) 봉.

    `after_time` 이후 첫 봉 중 `low <= entry_limit`(존 안으로 복귀)이고 무효화 전인 것을
    찾는다. 없으면 None = 더는 상단으로 되돌아오지 않았다(위로 달아난 존 → 숏 없음). 이
    게이트가 「즉시 재체결→즉시 재손절」 시뮬 인공물을 막는다(모듈 docstring 참고).
    """
    idx = bisect.bisect_right(substep_times, after_time)
    while idx < len(substeps):
        step = substeps[idx]
        if invalidation_time is not None and step.time >= invalidation_time:
            return None
        if step.low <= entry_limit:
            return idx
        idx += 1
    return None


def _short_chain(
    cand: _Candidate,
    *,
    switch_time: int,
    substeps: Sequence[SubStep],
    substep_times: Sequence[int],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
) -> tuple[list[_Candidate], list[_ShortEvent]]:
    """N 도달 후 존 무효화까지 윗경계 숏을 반복 무장·체결·청산한다(사용자 스펙).

    숏이 위이탈 손절(존이 또 버팀)이면 다시 무장, 붕괴 익절·미체결·무효화·데이터끝이면 그
    존은 끝난다. 재무장 지정가·손절·익절은 `short_zone_geometry`(존 상단·1R=존너비) 고정이고,
    체결·청산 판정은 `simulate_zone_limit_trade`(엔진과 동일)를 그대로 쓴다. `_next_short_arm`
    이 진짜 재-탭(가격이 상단으로 복귀)만 무장하게 게이트한다.
    """
    ob = cand.order_block
    if ob is None:
        return [], []
    width = ob.top - ob.bottom
    if width <= 0.0:
        return [], []  # 1R(존 너비)을 못 재는 존은 숏 손익도 못 낸다.
    entry_limit, stop_price, take_profit = short_zone_geometry(ob, params)
    invalidation_time = ob.break_time if params.use_order_block_stop else None

    short_cands: list[_Candidate] = []
    events: list[_ShortEvent] = []
    cursor = switch_time
    for depth in range(1, _MAX_REARM_PER_ZONE + 1):
        start = _next_short_arm(
            substep_times,
            substeps,
            after_time=cursor,
            entry_limit=entry_limit,
            invalidation_time=invalidation_time,
        )
        if start is None:
            break
        cut = bisect.bisect_left(htf_times, substeps[start].htf_bar_time)
        rsi_state = RealtimeRsi.seed_from_closed(htf_closes[:cut], length=params.rsi_length)
        outcome = simulate_zone_limit_trade(
            direction=OrderBlockDirection.BEARISH,
            limit_price=entry_limit,
            stop_price=stop_price,
            substeps=substeps,
            start=start,
            rsi_state=rsi_state,
            rsi_oversold=params.rsi_oversold,
            rsi_overbought=params.rsi_overbought,
            take_profit_price=take_profit,
            limit_valid_bars=None,  # 무기한 대기 = 무효화까지(재-탭 게이트가 재무장 시점 통제).
            invalidation_time=invalidation_time,
            rsi_gate_mode=params.rsi_gate_mode,
            rsi_neutral_band=params.rsi_neutral_band,
            penetration_bps=params.fill_penetration_bps,
        )
        if not outcome.filled or outcome.entry_time is None or outcome.entry_price is None:
            break  # NO_TOUCH / CANCELLED_INVALIDATED — 더는 상단으로 되돌아오지 않았다.
        risk = abs(outcome.entry_price - stop_price)
        if risk <= 0.0:
            break

        if outcome.status is ZoneLimitStatus.FILLED_EXITED:
            assert outcome.exit_time is not None and outcome.exit_price is not None
            is_win = outcome.exit_reason is SignalExitReason.TAKE_PROFIT  # 붕괴 포착
            is_stop = outcome.exit_reason is SignalExitReason.STOP_LOSS  # 존이 또 버팀
            exit_time, exit_price = outcome.exit_time, outcome.exit_price
            reason = ExitReason.TAKE_PROFIT if is_win else ExitReason.STOP_LOSS
        else:
            is_win = is_stop = False
            exit_time, exit_price = substeps[-1].time, substeps[-1].close
            reason = ExitReason.END_OF_DATA

        gross_r = PositionSide.SHORT.sign * (exit_price - outcome.entry_price) / risk
        short_cand = _Candidate(
            side=PositionSide.SHORT,
            entry_time=outcome.entry_time,
            entry_price=outcome.entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            reason=reason,
            stop_price=stop_price,
            order_block=ob,
            trigger_time=outcome.entry_time,  # 탭 없는 재진입 → 북 구간 버킷 키(진입 시각).
            exit_extreme=outcome.exit_extreme,
        )
        trade = _to_trade(short_cand, cfg.initial_capital, cfg, funding_rates)
        net_pp = (trade.return_pct * 100.0) if trade is not None else 0.0
        net_r = (
            _net_r(trade.return_pct, outcome.entry_price, stop_price) if trade is not None else 0.0
        )
        short_cands.append(short_cand)
        events.append(
            _ShortEvent(
                entry_time=outcome.entry_time,
                is_win=is_win,
                is_stop=is_stop,
                gross_r=gross_r,
                net_return_pp=net_pp,
                net_r=net_r,
                depth=depth,
            )
        )
        if not is_stop:
            break  # 붕괴 익절 · 데이터끝이면 이 존은 끝. 위이탈 손절이라야 또 무장한다.
        cursor = exit_time
    return short_cands, events


# --------------------------------------------------------------------------- #
# 존 전환 (롱 N회 + 숏 사슬)
# --------------------------------------------------------------------------- #


def _long_reentries(
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
    entry_rule: ReentryEntryRule,
) -> list[tuple[_Candidate, bool, int]]:
    """이 존의 롱 재진입 사슬 — (후보, is_win, depth) 리스트(band 재무장 = WAN-273 채택).

    `_iter_reentries`(WAN-228 재무장 루프)를 그대로 쓴다 — 이 리스트 전체가 롱-온리 팔의
    재진입이고, 전환 팔은 여기서 depth ≤ N만 취한다.
    """
    return [
        (re_cand, ev.is_win, ev.depth)
        for re_cand, ev in _iter_reentries(
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


@dataclass(frozen=True)
class _ZoneArms:
    """익절로 닫힌 한 존의 팔별 산출 — 롱-온리 재진입 + N별 전환 후보·숏 이벤트."""

    long_only: list[_Candidate]
    transition: dict[int, list[_Candidate]]
    short_events: dict[int, list[_ShortEvent]]


def zone_arms(
    cand: _Candidate,
    *,
    parent_exit_time: int,
    support_ns: Sequence[int],
    substeps: Sequence[SubStep],
    substep_times: Sequence[int],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    entry_rule: ReentryEntryRule = "band",
) -> _ZoneArms:
    """한 익절 존을 롱 재진입 사슬 + N별 숏 전환으로 분해한다(사용자 스펙).

    롱 재진입 사슬은 **한 번만** 돌려(band 재무장) 모든 팔이 공유한다 — 롱-온리 = 전체 사슬,
    전환 N = depth ≤ N 롱 + N 도달 후 숏 사슬. N 도달 정의(switch):

    * **N=0** — 부모 익절 직후 바로 숏 전환(대조). 롱 재진입 0건.
    * **N≥1** — N번째 롱 재진입이 **익절(is_win)**로 닫히면 그 직후 숏 전환. 롱 재진입이
      N 도달 전에 손절(존 붕괴)·미체결(가격 미복귀)이면 전환 안 함 = 그 존은 롱-온리와 동일.
      (붕괴가 롱 구간에 오면 롱이 이미 그 손실을 먹었으므로 숏 기회가 없다.)
    """
    long_chain = _long_reentries(
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
    long_only = [c for c, _win, _d in long_chain]

    transition: dict[int, list[_Candidate]] = {}
    short_events: dict[int, list[_ShortEvent]] = {}
    for support_n in support_ns:
        switch_time = _switch_time(cand, parent_exit_time, support_n, long_chain)
        long_part = [c for c, _win, d in long_chain if d <= support_n]
        if switch_time is None:
            # 전환 안 함 — 이 존은 롱-온리와 동일(N 도달 전에 붕괴/미복귀했거나 못 도달).
            transition[support_n] = long_only
            short_events[support_n] = []
            continue
        shorts, events = _short_chain(
            cand,
            switch_time=switch_time,
            substeps=substeps,
            substep_times=substep_times,
            htf_times=htf_times,
            htf_closes=htf_closes,
            params=params,
            cfg=cfg,
            funding_rates=funding_rates,
        )
        transition[support_n] = [*long_part, *shorts]
        short_events[support_n] = events
    return _ZoneArms(long_only=long_only, transition=transition, short_events=short_events)


def _switch_time(
    cand: _Candidate,
    parent_exit_time: int,
    support_n: int,
    long_chain: Sequence[tuple[_Candidate, bool, int]],
) -> int | None:
    """숏 전환 시각 — 스펙대로 N회 지지(익절)를 확인한 직후. 못 도달하면 None.

    N=0은 부모 익절 직후(부모는 호출부가 익절로 필터). N≥1은 depth==N 롱 재진입이 익절로
    닫혔을 때만 그 exit_time. N 도달 전에 손절·미체결(사슬이 N보다 짧음)이면 None.
    """
    if support_n == 0:
        return parent_exit_time
    for re_cand, is_win, depth in long_chain:
        if depth == support_n:
            return re_cand.exit_time if is_win else None
    return None  # 사슬이 N보다 짧다 = N회 지지에 도달하지 못했다.


# --------------------------------------------------------------------------- #
# 칸 실행 (payload 빌드 — 시장 데이터 1회 로드)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Wan280Cell:
    """한 (심볼, TF) 칸의 산출 — 팔별 구간 후보(북 입력) + 숏 이벤트(진단·분해)."""

    symbol: str
    timeframe: str
    boundary_ms: int
    funding: dict[str, tuple[FundingRate, ...]]
    base: dict[str, tuple[_Candidate, ...]]
    long_only_reentry: dict[str, tuple[_Candidate, ...]]
    transition_reentry: dict[int, dict[str, tuple[_Candidate, ...]]]
    #: (N, segment) → 그 셀·구간의 숏 이벤트(진단·분해).
    short_events: dict[int, dict[str, tuple[_ShortEvent, ...]]]


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    fill: harness.FillPreset | None


def _segment_arms_for_window(
    window: harness.MarketData,
    base_cands: Sequence[_Candidate],
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    timeframe: str,
) -> tuple[list[_Candidate], dict[int, list[_Candidate]], dict[int, list[_ShortEvent]]]:
    """이 창의 base 후보에서 롱-온리 재진입 + N별 전환 후보·숏 이벤트를 낸다(한 패스)."""
    long_only: list[_Candidate] = []
    transition: dict[int, list[_Candidate]] = {n: [] for n in SUPPORT_NS}
    short_events: dict[int, list[_ShortEvent]] = {n: [] for n in SUPPORT_NS}
    if not base_cands:
        return long_only, transition, short_events

    paired = sequence_with_candidates(list(base_cands), cfg, window.funding_rates)
    htf_ms = timeframe_to_ms(timeframe)
    frame = _prepare_htf(window.htf_df)
    htf_times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    htf_closes = [float(v) for v in frame["close"].astype(float).tolist()]
    substeps = build_substeps(window.df_1m, htf_ms)
    substep_times = [s.time for s in substeps]

    for cand, trade in paired:
        if cand.reason is not ExitReason.TAKE_PROFIT or cand.order_block is None:
            continue
        arms = zone_arms(
            cand,
            parent_exit_time=trade.exit_time,
            support_ns=SUPPORT_NS,
            substeps=substeps,
            substep_times=substep_times,
            htf_times=htf_times,
            htf_closes=htf_closes,
            params=params,
            cfg=cfg,
            funding_rates=window.funding_rates,
        )
        long_only.extend(arms.long_only)
        for n in SUPPORT_NS:
            transition[n].extend(arms.transition[n])
            short_events[n].extend(arms.short_events[n])
    return long_only, transition, short_events


def run_cell(task: _Task, *, log: bool = True) -> Wan280Cell | None:
    """한 칸을 돌려 팔별 후보·숏 이벤트를 낸다 — 채택 기본값 그대로(옛 핀 없음)."""
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms, need_1m=True
    )
    if market.empty or market.df_1m.empty:
        return None
    params = harness.build_params() if task.fill is None else harness.build_params(fill=task.fill)
    # 측정 모듈이라 유동성 한도는 명시적으로 끈다(옛 북 CSV 비트 재현 규약, wan169 참고).
    cfg = harness.build_config(task.timeframe)

    boundary = harness.eval_boundary_ms(market, WARM_OOS_SEGMENT)
    assert boundary is not None

    base: dict[str, tuple[_Candidate, ...]] = {}
    funding: dict[str, tuple[FundingRate, ...]] = {}
    long_only: dict[str, tuple[_Candidate, ...]] = {}
    transition: dict[int, dict[str, tuple[_Candidate, ...]]] = {n: {} for n in SUPPORT_NS}
    short_events: dict[int, dict[str, tuple[_ShortEvent, ...]]] = {n: {} for n in SUPPORT_NS}

    for segment_name, segment in (
        (SEGMENT_FULL, None),
        (SEGMENT_IS, IS_SEGMENT),
        (SEGMENT_OOS, OOS_SEGMENT),
    ):
        win = market if segment is None else harness.slice_market(market, segment)
        ob_result = harness.detect_order_blocks(win)
        cands, _stats = build_zone_limit_candidates(
            win.htf_df,
            win.df_1m,
            task.timeframe,
            params=params,
            cfg=cfg,
            order_block_result=ob_result,
        )
        base[segment_name] = tuple(cands)
        funding[segment_name] = tuple(win.funding_rates)
        lo, tr, se = _segment_arms_for_window(
            win, cands, params=params, cfg=cfg, timeframe=task.timeframe
        )
        long_only[segment_name] = tuple(lo)
        for n in SUPPORT_NS:
            transition[n][segment_name] = tuple(tr[n])
            short_events[n][segment_name] = tuple(se[n])

    if log:
        n_short = sum(len(short_events[0].get(s, ())) for s in _CANDIDATE_SEGMENTS)
        print(
            f"[wan280] {task.symbol} {task.timeframe}: base(full)={len(base[SEGMENT_FULL])} "
            f"long_re={len(long_only[SEGMENT_FULL])} N0_shorts(full+is+oos)={n_short}",
            flush=True,
        )
    return Wan280Cell(
        symbol=task.symbol,
        timeframe=task.timeframe,
        boundary_ms=boundary,
        funding=funding,
        base=base,
        long_only_reentry=long_only,
        transition_reentry=transition,
        short_events=short_events,
    )


def _run_task_logged(task: _Task) -> Wan280Cell | None:
    return run_cell(task, log=True)


def run_cells(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    fill: harness.FillPreset | None = None,
    jobs: int = 1,
) -> list[Wan280Cell]:
    """전 칸을 돈다. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            fill=fill,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        results = [run_cell(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            results = list(executor.map(_run_task_logged, tasks))
    return [r for r in results if r is not None]


# --------------------------------------------------------------------------- #
# 북 팔 (payload 조립 → build_book_rows)
# --------------------------------------------------------------------------- #


def _payloads_for_arm(cells: Sequence[Wan280Cell], arm: str) -> list[CellPayload]:
    """한 팔의 CellPayload 리스트 — base 후보 + 그 팔의 재진입 후보(북 주입).

    `arm`은 `long_only` 또는 `N{n}`. `build_book_rows(include_reentry=True)`가 base +
    reentry_candidates를 한 지갑에서 시퀀싱한다(WAN-261 경로 그대로).
    """
    payloads: list[CellPayload] = []
    for cell in cells:
        if arm == LONG_ONLY_ARM:
            reentry = dict(cell.long_only_reentry)
        else:
            support_n = int(arm[1:])
            reentry = dict(cell.transition_reentry[support_n])
        payloads.append(
            CellPayload(
                symbol=cell.symbol,
                timeframe=cell.timeframe,
                boundary_ms=cell.boundary_ms,
                candidates=dict(cell.base),
                funding=dict(cell.funding),
                rows=(),
                reentry_candidates=reentry,
            )
        )
    return payloads


class BookScopeRow(BaseModel):
    """한 스코프 × 구간 × 팔 × 제외종목의 북 집계 (완료기준 2)."""

    model_config = ConfigDict(frozen=True)

    scope: str
    arm: str
    segment: str
    exclude_symbol: str = ""
    num_cells: int
    num_symbols: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    liquidation_events: int

    @field_validator("return_over_mdd", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


def _to_scope_row(br: BookRunRow, *, scope: str, arm: str, exclude: str) -> BookScopeRow:
    return BookScopeRow(
        scope=scope,
        arm=arm,
        segment=br.segment,
        exclude_symbol=exclude,
        num_cells=br.num_cells,
        num_symbols=br.num_symbols,
        num_trades=br.num_trades,
        win_rate=br.win_rate,
        total_return=br.total_return,
        max_drawdown=br.max_drawdown,
        return_over_mdd=br.return_over_mdd,
        peak_concurrency=br.peak_concurrency,
        max_concurrent_risk=br.max_concurrent_risk,
        liquidation_events=br.liquidation_events,
    )


def _book_rows_for_payloads(
    payloads: Sequence[CellPayload],
    *,
    arm: str,
    scope: str,
    start_ms: int,
    end_ms: int,
    exclude: str,
) -> list[BookScopeRow]:
    kept = [p for p in payloads if not exclude or _short(p.symbol) != exclude]
    if not kept:
        return []
    book_rows = book_cli.build_book_rows(
        kept,
        book=ADOPTED_BOOK,
        segments=SEGMENTS,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
    )
    return [_to_scope_row(br, scope=scope, arm=arm, exclude=exclude) for br in book_rows]


def build_book_scope_rows(
    cells: Sequence[Wan280Cell], *, start_ms: int, end_ms: int
) -> list[BookScopeRow]:
    """스코프(각 TF + 전체 `all`) × 구간 × 팔(롱-온리 + N별) × leave-one-out의 북 집계."""
    timeframes = sorted({c.timeframe for c in cells}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    arms = [LONG_ONLY_ARM, *[_arm_name(n) for n in SUPPORT_NS]]
    rows: list[BookScopeRow] = []
    for scope in scopes:
        scoped = cells if scope == "all" else [c for c in cells if c.timeframe == scope]
        for arm in arms:
            payloads = _payloads_for_arm(scoped, arm)
            for exclude in ["", *LOO_SYMBOLS]:
                rows.extend(
                    _book_rows_for_payloads(
                        payloads,
                        arm=arm,
                        scope=scope,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        exclude=exclude,
                    )
                )
    return rows


# --------------------------------------------------------------------------- #
# 격리 숏 진단 + 분해 (완료기준 3·4)
# --------------------------------------------------------------------------- #


class ShortDiagRow(BaseModel):
    """한 (심볼, TF, N) × 구간의 격리 숏 진단 + 분해 (완료기준 3·4)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    support_n: int
    segment: str
    zones_switched: int
    """숏 전환한 존 수(N회 지지 도달 · 붕괴가 롱 구간에 안 온 존)."""
    short_fills: int
    """실제 숏 체결 수(= 반복 숏 횟수 합)."""
    wins: int
    """붕괴로 익절한 숏 수."""
    stops: int
    """위이탈 손절 숏 수(존이 또 버팀)."""
    unclosed: int
    """데이터끝까지 미청산 숏 수."""
    collapse_r_sum: float
    """붕괴로 익절한 숏의 gross R 합(+1.5 each)."""
    stop_r_sum: float
    """위이탈 손절 숏의 gross R 합(−1.0 each)."""
    net_r_sum: float
    """격리 순 R 합(붕괴 한 방 vs 반복 손절 순효과)."""
    net_pp_sum: float
    """격리 순수익 %p 합."""
    funding_coverage_gap: bool
    """신규 종목(펀딩 0행)이라 순수익 낙관인가(†)."""

    @property
    def win_rate(self) -> float | None:
        decided = self.wins + self.stops
        return self.wins / decided if decided else None

    @property
    def shorts_per_zone(self) -> float | None:
        return self.short_fills / self.zones_switched if self.zones_switched else None

    @property
    def mean_net_r(self) -> float | None:
        return self.net_r_sum / self.short_fills if self.short_fills else None


def _diag_rows_for_cell(cell: Wan280Cell) -> list[ShortDiagRow]:
    """한 셀의 N × 구간별 격리 숏 진단 행. oos_warm은 full을 칸 경계로 걸러 만든다."""
    gap = cell.symbol in FUNDING_GAP_SYMBOLS
    rows: list[ShortDiagRow] = []
    for support_n in SUPPORT_NS:
        for segment in SEGMENTS:
            if segment == SEGMENT_OOS_WARM:
                events = [
                    e
                    for e in cell.short_events[support_n].get(SEGMENT_FULL, ())
                    if e.entry_time >= cell.boundary_ms
                ]
            else:
                events = list(cell.short_events[support_n].get(segment, ()))
            rows.append(_diag_row(cell, support_n, segment, events, gap))
    return rows


def _diag_row(
    cell: Wan280Cell, support_n: int, segment: str, events: Sequence[_ShortEvent], gap: bool
) -> ShortDiagRow:
    zones = len({e.entry_time for e in events if e.depth == 1})  # depth 1 = 존당 첫 숏
    return ShortDiagRow(
        symbol=cell.symbol,
        timeframe=cell.timeframe,
        support_n=support_n,
        segment=segment,
        zones_switched=zones,
        short_fills=len(events),
        wins=sum(1 for e in events if e.is_win),
        stops=sum(1 for e in events if e.is_stop),
        unclosed=sum(1 for e in events if not e.is_win and not e.is_stop),
        collapse_r_sum=sum(e.gross_r for e in events if e.is_win),
        stop_r_sum=sum(e.gross_r for e in events if e.is_stop),
        net_r_sum=sum(e.net_r for e in events),
        net_pp_sum=sum(e.net_return_pp for e in events),
        funding_coverage_gap=gap,
    )


def build_diag_rows(cells: Sequence[Wan280Cell]) -> list[ShortDiagRow]:
    rows: list[ShortDiagRow] = []
    for cell in cells:
        rows.extend(_diag_rows_for_cell(cell))
    return rows


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _pick_book(
    rows: Sequence[BookScopeRow], *, scope: str, arm: str, segment: str, exclude: str = ""
) -> BookScopeRow | None:
    for r in rows:
        if (
            r.scope == scope
            and r.arm == arm
            and r.segment == segment
            and r.exclude_symbol == exclude
        ):
            return r
    return None


def _short_net_r_by_n(diag_rows: Sequence[ShortDiagRow], segment: str) -> dict[int, float]:
    """N별 격리 숏 순 R 합(전 심볼·전 TF, 해당 구간) — 순효과의 직접 답."""
    out: dict[int, float] = {}
    for support_n in SUPPORT_NS:
        out[support_n] = sum(
            r.net_r_sum for r in diag_rows if r.support_n == support_n and r.segment == segment
        )
    return out


def verdict(book_rows: Sequence[BookScopeRow], diag_rows: Sequence[ShortDiagRow]) -> str:
    """N회 지지 후 숏 전환이 롱-온리보다 버나 = 붕괴 한 방이 반복 손절을 덮나(주 스코프·oos_warm).

    직접 답은 **격리 숏 순 R의 부호**다(붕괴 익절 R 합 − 반복 손절 R 합). 북 비교(정본)는
    위험조정(MDD·수익/MDD·청산)으로 병기하되, 숏이 순 손실인데 북 지표가 노이즈로 미세하게
    나아지는 셀은 「이겼다」로 읽지 않는다(높은 N일수록 전환 존이 급감해 롱-온리와 사실상
    같아지기 때문). 숫자는 전부 행에서 계산한다(문장에 박으면 재실행 뒤 거짓말 — WAN-164).
    """
    main_scope = "all" if any(r.scope == "all" for r in book_rows) else None
    if main_scope is None:
        scopes = sorted({r.scope for r in book_rows}, key=timeframe_to_ms)
        main_scope = scopes[0] if scopes else ""
    base = _pick_book(book_rows, scope=main_scope, arm=LONG_ONLY_ARM, segment=SEGMENT_OOS_WARM)
    if base is None:
        return "**판정 불가** — 주 스코프의 롱-온리 oos_warm 행이 없습니다."
    short_net = _short_net_r_by_n(diag_rows, SEGMENT_OOS_WARM)
    parts: list[str] = []
    genuine_win = False  # 숏이 순 R 양(+) **그리고** 북 위험조정 개선인 N이 있나.
    for support_n in SUPPORT_NS:
        tr = _pick_book(
            book_rows, scope=main_scope, arm=_arm_name(support_n), segment=SEGMENT_OOS_WARM
        )
        if tr is None:
            continue
        d_mdd = (tr.max_drawdown - base.max_drawdown) * 100
        base_rm, tr_rm = base.return_over_mdd, tr.return_over_mdd
        net_r = short_net.get(support_n, 0.0)
        book_up = tr_rm is not None and base_rm is not None and tr_rm > base_rm and tr.sample_ok
        genuine_win = genuine_win or (book_up and net_r > 0.0)
        gate = "" if tr.sample_ok else " ⚠️(20거래 미만)"
        rm_txt = f"{base_rm:.2f}→{tr_rm:.2f}" if base_rm is not None and tr_rm is not None else "—"
        parts.append(
            f"N={support_n}: 숏 순R {net_r:+.1f} · 수익/MDD {rm_txt} · MDD Δ{d_mdd:+.2f}%p · "
            f"청산 {tr.liquidation_events} · 거래 {tr.num_trades}{gate}"
        )
    all_neg = all(v <= 0.0 for v in short_net.values())
    if genuine_win:
        head = (
            "**숏이 순 R 양(+)이면서 북 위험조정도 개선하는 N이 있다 — "
            "붕괴가 손절을 덮는 셀이 존재.**"
        )
    elif all_neg:
        head = (
            "**붕괴 한 방이 반복 손절을 못 덮는다 — 격리 숏 순 R이 모든 N에서 음수다.** "
            "북 지표가 높은 N에서 미세하게 나아 보이는 것은 노이즈다(전환 존이 급감해 "
            "롱-온리와 사실상 같은 팔)."
        )
    else:
        head = (
            "**전환이 롱-온리를 강건하게 이기지 못한다 — "
            "숏 순 R 양인 N이 북 개선과 겹치지 않는다.**"
        )
    coords = (
        f"{main_scope}·oos_warm 롱-온리 기준: MDD {base.max_drawdown * 100:.2f}% · 수익/MDD "
        f"{base.return_over_mdd:.2f} · 청산 {base.liquidation_events} · 거래 {base.num_trades}. "
        if base.return_over_mdd is not None
        else f"{main_scope}·oos_warm 롱-온리 기준: MDD {base.max_drawdown * 100:.2f}%. "
    )
    return (
        f"{head} {coords}"
        + " · ".join(parts)
        + ". ⚠️ 총수익% 변화는 복리 착시라 판정에 넣지 않는다(WAN-213) — 판단은 위 숏 순 R과 "
        "위험조정이다. 전부 `baseline`(닿으면 체결) 낙관 위 값이고 숏은 존 경계 체결이라 큐 "
        "우선순위(WAN-98 Canceled)에 특히 약하다. 「엣지 없음」(WAN-84/88/111/114/124/151/201/"
        "248)은 다른 질문이라 불변이고, 채택은 재-베이스라인 = 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def book_to_frame(rows: Sequence[BookScopeRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookScopeRow.model_fields))


def diag_to_frame(rows: Sequence[ShortDiagRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(ShortDiagRow.model_fields))


def book_from_csv(path: Path) -> list[BookScopeRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [BookScopeRow.model_validate(rec) for rec in records]


def diag_from_csv(path: Path) -> list[ShortDiagRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [ShortDiagRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def merge_book(existing: Sequence[BookScopeRow], new: Sequence[BookScopeRow]) -> list[BookScopeRow]:
    new_scopes = {r.scope for r in new if r.scope != "all"}
    has_new_all = any(r.scope == "all" for r in new)
    kept = [
        r for r in existing if r.scope not in new_scopes and not (has_new_all and r.scope == "all")
    ]
    return [*kept, *new]


def merge_diag(existing: Sequence[ShortDiagRow], new: Sequence[ShortDiagRow]) -> list[ShortDiagRow]:
    new_tfs = {r.timeframe for r in new}
    kept = [r for r in existing if r.timeframe not in new_tfs]
    return [*kept, *new]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def describe_engine() -> str:
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={OrderBlockParams().combine_obs}, max_zone_width_atr={p.max_zone_width_atr}, "
        f"short_enabled={p.short_enabled}, "
        f"book={ADOPTED_BOOK.leverage_mode}×{ADOPTED_BOOK.leverage_multiple}"
    )


def _book_table(book_rows: Sequence[BookScopeRow], scope: str, segment: str) -> list[str]:
    lines = [
        "| 팔 | 거래 | 총수익%† | MDD | 수익/MDD | 최대동시리스크 | 청산 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for arm in [LONG_ONLY_ARM, *[_arm_name(n) for n in SUPPORT_NS]]:
        r = _pick_book(book_rows, scope=scope, arm=arm, segment=segment)
        if r is None:
            continue
        gate = "" if r.sample_ok else " ⚠️"
        label = "롱-온리(현행)" if arm == LONG_ONLY_ARM else f"전환 {arm}"
        lines.append(
            f"| {label} | {r.num_trades}{gate} | {_pct(r.total_return)} | {_pct(r.max_drawdown)} | "
            f"{_rr(r.return_over_mdd)} | {_pct(r.max_concurrent_risk)} | {r.liquidation_events} | "
            f"{r.peak_concurrency} |"
        )
    return lines


def _diag_table(diag_rows: Sequence[ShortDiagRow], timeframe: str, segment: str) -> list[str]:
    scoped = sorted(
        (r for r in diag_rows if r.timeframe == timeframe and r.segment == segment),
        key=lambda r: (r.support_n, r.symbol),
    )
    lines = [
        "| N | 심볼 | 전환존 | 숏 | 존당숏 | 승률 | 붕괴R | 손절R | 순R | 평균R | 순%p |",
        "| --: | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for r in scoped:
        if r.short_fills == 0:
            continue
        fund = "†" if r.funding_coverage_gap else ""
        wr = f"{r.win_rate * 100:.1f}%" if r.win_rate is not None else "—"
        spz = f"{r.shorts_per_zone:.2f}" if r.shorts_per_zone is not None else "—"
        mnr = f"{r.mean_net_r:+.3f}" if r.mean_net_r is not None else "—"
        lines.append(
            f"| {r.support_n} | {_short(r.symbol)}{fund} | {r.zones_switched} | {r.short_fills} | "
            f"{spz} | {wr} | {r.collapse_r_sum:+.1f} | {r.stop_r_sum:+.1f} | {r.net_r_sum:+.2f} | "
            f"{mnr} | {r.net_pp_sum:+.1f} |"
        )
    return lines


def _decomp_table(diag_rows: Sequence[ShortDiagRow], segment: str) -> list[str]:
    """N × TF 순효과 집계 — 붕괴 한 방(익절 R 합) vs 반복 손절 R 합."""
    timeframes = sorted({r.timeframe for r in diag_rows}, key=timeframe_to_ms)
    lines = [
        "| N | TF | 전환존 | 숏체결 | 붕괴수 | 손절수 | 미청산 | 붕괴R합 | 손절R합 | 순R합 |",
        "| --: | -- | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for support_n in SUPPORT_NS:
        for tf in timeframes:
            scoped = [
                r
                for r in diag_rows
                if r.support_n == support_n and r.timeframe == tf and r.segment == segment
            ]
            if not scoped:
                continue
            fills = sum(r.short_fills for r in scoped)
            if fills == 0:
                continue
            zones = sum(r.zones_switched for r in scoped)
            wins = sum(r.wins for r in scoped)
            stops = sum(r.stops for r in scoped)
            unclosed = sum(r.unclosed for r in scoped)
            coll = sum(r.collapse_r_sum for r in scoped)
            stop_r = sum(r.stop_r_sum for r in scoped)
            net_r = sum(r.net_r_sum for r in scoped)
            lines.append(
                f"| {support_n} | {tf} | {zones} | {fills} | {wins} | {stops} | {unclosed} | "
                f"{coll:+.1f} | {stop_r:+.1f} | {net_r:+.2f} |"
            )
    return lines


def _loo_table(book_rows: Sequence[BookScopeRow], scope: str, segment: str) -> list[str]:
    lines = [
        "| 팔 | 전체 | " + " | ".join(f"−{s}" for s in LOO_SYMBOLS) + " |",
        "| -- | --: | " + " | ".join("--:" for _ in LOO_SYMBOLS) + " |",
    ]
    for arm in [LONG_ONLY_ARM, *[_arm_name(n) for n in SUPPORT_NS]]:
        base = _pick_book(book_rows, scope=scope, arm=arm, segment=segment)
        if base is None:
            continue
        cells = []
        for s in LOO_SYMBOLS:
            r = _pick_book(book_rows, scope=scope, arm=arm, segment=segment, exclude=s)
            cells.append(_pct(r.total_return) if r is not None else "—")
        label = "롱-온리" if arm == LONG_ONLY_ARM else arm
        lines.append(f"| {label} | {_pct(base.total_return)} | " + " | ".join(cells) + " |")
    return lines


def build_summary_markdown(
    book_rows: Sequence[BookScopeRow],
    diag_rows: Sequence[ShortDiagRow],
    *,
    book_csv: Path,
    diag_csv: Path,
) -> str:
    timeframes = sorted({r.timeframe for r in diag_rows}, key=timeframe_to_ms)
    main_scope = (
        "all" if any(r.scope == "all" for r in book_rows) else (timeframes[0] if timeframes else "")
    )
    lines = [
        "# WAN-280 — band 재진입 존 「N회 지지 후 숏 전환」 (측정 전용)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`·채택 "
        "북 cap_only 5배 · 재진입 ON band) 돌리며 옛 핀은 하나도 물려받지 않는다. "
        "`short_enabled=False` 유지(측정용 숏이지 재활성화 아님). 렌즈 `baseline`(+`--fill "
        "pen_5bp` 옵트인) · 못 박은 6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING"
        "=false` 유지).",
        "",
        "## 이 표가 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영. 숏 스펙: 진입=오더블록 윗경계(`ob.top`) 지정가 · "
        "1R=존 너비(`top−bottom`) · 손절=진입+1R(존 위) · 익절=진입−1.5R(존 아래) · 위이탈 "
        "손절나면 존 무효화까지 윗경계 숏 재시도 반복(진짜 재-탭만 무장).",
        "",
        "**팔**: `롱-온리(현행)` = 채택 band 재진입 북(= 인자 없는 `backtest.run`) · `전환 N` = "
        "첫진입+재진입 N회까지 롱, N회째부터 존 무효화까지 윗경계 숏 반복. N=0은 첫 익절 후 바로 "
        "숏 전환(대조).",
        "",
        f"재현: `uv run python -m backtest.wan280_reentry_short_transition --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` (요약만: `--from-csv`). 15m은 별도 무거운 "
        f"실행(`--tf 15m --append`). 원자료: `{book_csv}`(북) · `{diag_csv}`(격리 숏 진단).",
        "",
        "⚠️ **총수익%는 복리 착시**(WAN-213) — 판단은 **MDD · 수익/MDD · 최대 동시 리스크 · "
        "청산**의 롱-온리→전환 차이다. 전부 `baseline`(닿으면 체결) 낙관 위 값이고 숏은 존 경계 "
        "체결이라 큐 우선순위(WAN-98 Canceled)에 특히 약하다.",
        "",
        f"## 1. 북 비교 (완료기준 2) — {main_scope}",
        "",
    ]
    for seg_title, segment in (
        ("oos_warm (주 수치)", SEGMENT_OOS_WARM),
        ("oos (차가운 스트레스)", SEGMENT_OOS),
        ("full (맥락)", SEGMENT_FULL),
        ("is (맥락)", SEGMENT_IS),
    ):
        lines += [f"### {seg_title}", "", *_book_table(book_rows, main_scope, segment), ""]
    if main_scope == "all":
        lines += ["## 2. TF 스코프별 북 (oos_warm 주)", ""]
        for tf in timeframes:
            lines += [f"### {tf}", "", *_book_table(book_rows, tf, SEGMENT_OOS_WARM), ""]
    lines += [
        "## 3. 격리 숏 진단 (완료기준 3) — oos_warm",
        "",
        "숏 거래만 per-cell 단독으로 떼어 「숏이 애초에 돈이 되나」. `평균순R` = 실현손익 ÷ 리스크 "
        "금액(WAN-154). 격리는 실매매가 아니라 진단이다(실매매는 롱↔숏 전환이라 동시 보유 없음). "
        "†=신규 종목(펀딩 0행 → 순수익 낙관).",
        "",
    ]
    for tf in timeframes:
        table = _diag_table(diag_rows, tf, SEGMENT_OOS_WARM)
        if len(table) > 2:
            lines += [f"### {tf}", "", *table, ""]
    lines += [
        "## 4. 분해 — 붕괴 한 방 vs 반복 손절 (완료기준 4) — oos_warm",
        "",
        "`붕괴R합` = 붕괴로 익절한 숏의 gross R 합(+1.5 each) · `손절R합` = 위이탈 손절 R 합"
        "(−1.0 each) · `순R합` = 격리 순 R 합. N 커질수록 전환존·숏체결이 급감한다(20거래 게이트).",
        "",
        *_decomp_table(diag_rows, SEGMENT_OOS_WARM),
        "",
        "## 5. leave-one-out — 종목 편중 (oos_warm · 총수익%)",
        "",
        "전환 팔의 이득/손해가 ETH·SOL·DOGE에 쏠리는지. ⚠️ 총수익%는 복리 착시라 방향만 읽는다.",
        "",
        f"### {main_scope}",
        "",
        *_loo_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "## 판정 — N회 지지 후 숏 전환이 롱-온리보다 버나",
        "",
        verdict(book_rows, diag_rows),
        "",
        "⚠️ **이 표는 채택 근거가 아니라 측정이다.** 「엣지 없음」(WAN-84/88/111/114/124/151/201/"
        "248)은 다른 질문(진입 규칙이 무작위와 구분되는가)이라 불변이고, **매칭 널은 아니다**"
        "(후속). 순 net 플러스여도 거래 관리(위험의 모양, WAN-90)이지 진입 알파 주장이 아니다. "
        "**숏 재활성화 아님**(`short_enabled=False` 유지, 측정용) · 채택은 재-베이스라인 = 사용자 "
        "결정 · 개발자 임의 착수 금지. **기본값·토대 불변**(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    fill: harness.FillPreset | None = None,
    jobs: int = 1,
    funding_proxy: bool = True,
    log: bool = True,
) -> tuple[list[BookScopeRow], list[ShortDiagRow]]:
    cells = run_cells(symbols, timeframes, start=start, end=end, fill=fill, jobs=jobs)
    if funding_proxy:
        cells = _apply_funding_proxy(cells, log=log)
    book_rows = build_book_scope_rows(
        cells, start_ms=parse_date_ms(start), end_ms=parse_date_ms(end)
    )
    diag_rows = build_diag_rows(cells)
    return book_rows, diag_rows


def _apply_funding_proxy(cells: Sequence[Wan280Cell], *, log: bool) -> list[Wan280Cell]:
    """신규 3종목 펀딩 대리를 base payload로 계산해 셀의 funding에 반영한다(BTC 도너).

    `apply_funding_proxy`는 `CellPayload`에 작용하므로 base payload로 도너를 정하고, 그
    대체된 funding 시계열을 원 셀에 다시 싣는다(모든 팔이 같은 funding을 공유하므로 한 번이면
    충분하다). 신규 종목 격리 숏 순수익은 재계산 없이 낙관(†) 표기로만 남긴다(census 관행).
    """
    base_payloads = [
        CellPayload(
            symbol=c.symbol,
            timeframe=c.timeframe,
            boundary_ms=c.boundary_ms,
            candidates=dict(c.base),
            funding=dict(c.funding),
            rows=(),
        )
        for c in cells
    ]
    proxied, note = apply_funding_proxy(base_payloads)
    if note and log:
        print(f"[wan280] 펀딩 대리: {note}", flush=True)
    by_key = {(p.symbol, p.timeframe): p for p in proxied}
    out: list[Wan280Cell] = []
    for c in cells:
        p = by_key[(c.symbol, c.timeframe)]
        out.append(
            Wan280Cell(
                symbol=c.symbol,
                timeframe=c.timeframe,
                boundary_ms=c.boundary_ms,
                funding=dict(p.funding),
                base=c.base,
                long_only_reentry=c.long_only_reentry,
                transition_reentry=c.transition_reentry,
                short_events=c.short_events,
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-280 band 재진입 N회 지지 후 숏 전환 측정")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument(
        "--fill",
        type=str,
        default="baseline",
        choices=("baseline", "pen_1bp", "pen_5bp"),
        help="체결 렌즈(관통만 — 탈락 렌즈는 이 측정에 미배선). baseline(기본) · "
        "pen_5bp(관통 5bp 스트레스, WAN-96).",
    )
    parser.add_argument("--out-book", type=Path, default=DEFAULT_BOOK_CSV)
    parser.add_argument("--out-diag", type=Path, default=DEFAULT_DIAG_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="새로 돌린 행을 기존 CSV에 병합한다(같은 TF는 갱신, 새 TF는 추가).",
    )
    args = parser.parse_args(argv)

    out_book = Path(args.out_book)
    out_diag = Path(args.out_diag)
    out_md = Path(args.out_md)
    fill_preset = harness.fill_preset(args.fill)

    if args.from_csv:
        book_rows = book_from_csv(out_book)
        diag_rows = diag_from_csv(out_diag)
        print(f"[wan280] CSV 로드 — 북 {len(book_rows)}행 · 진단 {len(diag_rows)}행 (재실행 없음)")
    else:
        new_book, new_diag = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            fill=fill_preset,
            jobs=args.jobs,
        )
        out_book.parent.mkdir(parents=True, exist_ok=True)
        if args.append and out_book.exists() and out_diag.exists():
            book_rows = merge_book(book_from_csv(out_book), new_book)
            diag_rows = merge_diag(diag_from_csv(out_diag), new_diag)
        else:
            book_rows, diag_rows = list(new_book), list(new_diag)
        book_to_frame(book_rows).to_csv(out_book, index=False)
        diag_to_frame(diag_rows).to_csv(out_diag, index=False)
        print(f"[wan280] 북 {len(book_rows)}행 → {out_book}")
        print(f"[wan280] 진단 {len(diag_rows)}행 → {out_diag}")

    if not book_rows or not diag_rows:
        print("[wan280] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(book_rows, diag_rows, book_csv=out_book, diag_csv=out_diag),
        encoding="utf-8",
    )
    print(f"[wan280] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
