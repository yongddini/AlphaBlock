"""WAN-284 — 저항-존 숏을 「헤지」가 아니라 「수익」으로 재판정 (측정 전용 · 옵트인 · 기본값 불변).

## 무엇을 묻나 (사용자 질문 2026-08-11 — "헷지가 아닌 수익을 내기 위함이라면 어떻게 판단할 거야")

WAN-282/283은 저항-존(공급 OB) 숏을 채택 북에 얹고 **헤지 자(전체 낙폭 MDD)** 로 판정해
**(c) 엇갈림 = 채택 안 함**으로 닫았다. 그런데 같은 CSV를 **수익 자**로 읽으면 표면 그림이
뒤집힌다 — 격리 숏 순 R이 IS·OOS 양쪽에서 플러스이고, 숏을 얹으면 수익/MDD가 대부분 오른다.

**"돈을 벌었다" ≠ "무작위보다 낫다".** 하락 구간에 아무 숏이나 쳐도 돈은 벌린다(시장 베타).
그래서 이 표는 저항-존 숏의 **수익 자를 같은 구간·같은 개수의 무작위 숏과 대조**한다:

* **알파** = 실제 − 널 평균(같은 창에 무작위 시각으로 들어간 숏이 버는 몫을 뺀 나머지).
* **베타** = 널 평균 그 자체(장세 라벨 `buy_hold`와 함께 읽는다 — 창이 내리면 널도 번다).

WAN-164/201은 **엣지 질문**(진입 규칙이 무작위와 구분되는가)에 「아니오」를 냈지만, 그 자는
볼린저 무력화 풀이었고 **수익 질문**(수익 자가 무작위보다 나은가)이 아니었다. 이 모듈이 그
축을 새로 낸다.

## 대조군 설계 — 무작위 **시각** 숏 (기하 고정)

널은 실제 저항-존 숏과 **개수·기하**를 맞추고 **시각만** 무작위로 바꾼다:

* **개수** — 그 (칸, 구간)의 실제 숏 후보 수 `k`와 정확히 같다(base + band 재진입 합).
* **기하** — 1R(= |진입−손절| ÷ 진입) 비율을 **실제 숏들의 경험분포에서 재표집**해 손절을
  진입가 위에 놓는다. 익절은 그 1R의 고정 1.5R 아래(채택 규칙 그대로). 기하를 안 맞추면
  널이 퇴화해 「타이밍」이 아니라 「1R 크기」를 재게 된다(WAN-255가 실측한 함정).
* **시각** — 그 구간 창의 1분 서브스텝에서 균등 추출. 그 서브스텝 종가에 지정가를 두면
  즉시 체결되고, 그 뒤 손절·익절 로직은 **엔진과 한 글자도 다르지 않다**
  (`simulate_zone_limit_trade`). 실제와 널의 유일한 차이는 **진입 시각**이다.
* **렌즈** — 관통 벌점(`--fill pen_5bp`)은 `params.fill_penetration_bps`로 **실제·널에 동일**
  하게 배선된다(완료기준 4).

⚠️ 널은 **존이 없다** — 재진입 구조(익절 후 재무장)를 모델링하지 않고 `k`개를 통째로 무작위
시각에 뿌린다. 그래서 이 표가 재는 것은 「저항 존이라는 **자리**와 그 재무장 구조를 합친
타이밍」이 무작위 시각보다 나은가이지, 재진입만의 몫이 아니다.

## 두 개의 수익 자 (완료기준 1)

1. **격리 순 R** — 그 칸의 숏만 단독 시퀀싱(WAN-282 §3과 같은 자, WAN-154 정의). 실제 합 대
   시드 20개의 널 합 → 단측 순위 p = `(1 + #{널 ≥ 실제}) / (1 + 20)`(하한 0.048).
2. **북 수익/MDD** — 채택 북(cap_only 5배 · band 재진입)에서 **롱은 그대로 두고 숏만** 널로
   갈아끼운 「널 북」 20개와 실제 롱+숏 북을 대조한다. 롱-온리 북(= 숏 0개)은 기준선으로 병기.
   실제와 널 북은 **롱 후보가 글자 그대로 같다** — 차이는 숏의 시각뿐이다.

## 좌표 · 성격 · 경고

9종목 · 못 박은 6년(WAN-182) · `is`·`oos_warm`(WAN-166 정본) · `baseline`(+`--fill pen_5bp`)
· 핀 없음(`ConfluenceParams()` = 오늘 엔진) · ETH·DOGE·LINK leave-one-out(각각 + 셋 다) ·
신규 3종목 펀딩 대리(BTC 도너) · 20거래 미만 셀 표시(WAN-84 유효 기준).

측정 전용. `short_enabled=False` **기본값 유지**(측정용 숏이지 재활성화 아님) · 기본값·토대
불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지. ⚠️ 총수익%는 복리 착시(WAN-213)라 방향만 읽는다.
⚠️ 전부 `baseline`(닿으면 체결) 낙관 위 값이고 숏은 존 경계(밴드가) 체결이라 큐 우선순위에
특히 약하다 — `pen_5bp`로 드러내되 **실해소는 틱·호가(WAN-98, Canceled) 소관**이다.
⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 표는 그 판정을 뒤집는 게
아니라 수익이 알파인지 베타인지를 **가르는** 측정이다(WAN-90 = 위험의 모양). 채택은
재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지.

## 재현

```
M=backtest.wan284_resistance_short_profit_null
uv run python -m $M --tf 4h --jobs 6                       # WAN-284
uv run python -m $M --fill pen_5bp --append                # WAN-284 (4h)
uv run python -m $M --tf 1h --append --jobs 6              # ↓ WAN-285: 한 TF씩
uv run python -m $M --tf 1h --fill pen_5bp --append --jobs 6
uv run python -m $M --tf 2h --append --jobs 6
uv run python -m $M --tf 2h --fill pen_5bp --append --jobs 6
uv run python -m $M --tf 15m --append --jobs 6             # 무거움(WAN-203)
uv run python -m $M --tf 15m --fill pen_5bp --append --jobs 6
uv run python -m $M --from-csv                             # 요약만
```

⚠️ **축은 한 TF씩 잇는다**(WAN-285). `--tf 1h,2h`처럼 여러 TF를 한 번에 돌리면 북 자에
**그 두 TF만 담은 교차 스코프**가 생기는데, 4h·15m을 따로 이어 붙인 CSV에서는 그 라벨이
「전부」로 읽혀 WAN-283이 교정한 **`all` 스코프 오염**을 되풀이한다. 그래서 교차 스코프
라벨에는 구성 TF가 박히고(`all:1h+2h`, `cross_scope_label`) 한 TF씩 이으면 그 스코프가
아예 생기지 않는다.
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import math
import random
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import book_cli, harness
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.run import ADOPTED_BOOK, parse_date_ms
from backtest.substep import (
    SubStep,
    ZoneLimitStatus,
    build_substeps,
    simulate_zone_limit_trade,
)
from backtest.sweep import timeframe_to_ms
from backtest.wan89_short_autopsy import _buy_hold
from backtest.wan169_leverage_book import CellPayload, _short
from backtest.wan228_reentry_census import _direction
from backtest.wan231_reentry_null import rank_p_value
from backtest.wan282_resistance_short_mirror import (
    FUNDING_GAP_SYMBOLS,
    _net_r,
    _short_candidates,
    run_arm_cells,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    _prepare_htf,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.models import ConfluenceParams, OrderBlockParams, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

REPORTS_DIR = Path("backtest/reports")
DEFAULT_SHORT_CSV = REPORTS_DIR / "wan284_short_profit_null.csv"
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan284_book_profit_null.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan284_short_profit_null_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 기본 TF = 4h(컴퓨트 실현 가능). 1h·2h·15m은 `--append`로 잇는다(15m은 셀당 무거움 WAN-203).
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h",)

#: 매칭 널 시드 수 — WAN-142/152/154/231 관행. 단측 순위 p 하한 = 1/(SEEDS+1) = 0.048.
SEEDS = 20

#: 시드 스트림 기준값(재현성). 칸은 (심볼, TF) 단위로 독립이라 이 하나로 족하다.
BASE_SEED = 284_000

#: 판정 구간 — WAN-166 정본(oos_warm 주 · is 맥락). 널은 이 둘에서만 돈다(wan231 선례).
NULL_SEGMENTS: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS_WARM)

#: 유의 게이트 — WAN-84 유효 기준(거래 20건)과 p 문턱.
MIN_TRADES_GATE = 20
ALPHA = 0.05

#: leave-one-out 대상(이슈 완료기준 3) — 하나씩, 그리고 셋 다 함께.
LOO_SYMBOLS: tuple[str, ...] = ("ETH", "DOGE", "LINK")
LOO_ALL = "ETH+DOGE+LINK"

#: 렌즈 표시 순서 — baseline(공식) 먼저, 그 뒤 스트레스.
LENS_ORDER: tuple[str, ...] = ("baseline", "pen_1bp", "pen_5bp")

#: 여러 TF를 한 지갑으로 묶은 북 스코프의 라벨 머리(구성 TF가 뒤에 박힌다).
CROSS_SCOPE_PREFIX = "all"


def cross_scope_label(timeframes: Sequence[str]) -> str:
    """교차 TF 북 스코프의 라벨 — **구성 TF를 이름에 박는다**(WAN-285).

    옛 라벨은 그냥 `all`이었는데, 축을 `--append`로 잇는 이 모듈에서는 그 이름이 거짓말을
    한다: `--tf 1h,2h`만 돌린 실행이 만든 `all`은 4h·15m을 이어 붙인 CSV 안에서 「전부」로
    읽힌다(WAN-283이 헤지 표에서 교정한 `all` 스코프 오염과 같은 부류). 구성 TF를 라벨에
    박으면 부분 집합이 전체를 사칭할 수 없고, 한 TF씩 이으면 이 스코프는 아예 안 생긴다.
    """
    return CROSS_SCOPE_PREFIX + ":" + "+".join(sorted(timeframes, key=timeframe_to_ms))


def is_cross_scope(scope: str) -> bool:
    """교차 TF 스코프인가(옛 `all` 라벨과 새 `all:15m+1h` 라벨 둘 다)."""
    return scope == CROSS_SCOPE_PREFIX or scope.startswith(CROSS_SCOPE_PREFIX + ":")


# --------------------------------------------------------------------------- #
# 널 숏 한 건 — 엔진과 같은 시뮬레이터로 청산까지
# --------------------------------------------------------------------------- #


#: RSI를 **읽지 않는** 게이트 모드 — 시딩을 건너뛰어도 결과가 비트 동일한 모드다.
#:
#: `simulate_zone_limit_trade`의 진입 판정은 `unconditional`에서 `rsi_gate_mode ==
#: "unconditional"`에 단락돼 `live_rsi`를 **보지 않는다**(`backtest/substep.py`). 그런데
#: 널은 후보 하나마다 그 시점까지의 확정봉 전부를 커밋해 상태를 만들었다 — 후보 수 × 봉 수의
#: O(N×M)이라 15m·6년(상위TF 21만 봉 × 시드 20 × 후보 수천)에서 널 생성이 통째로 이 시딩에
#: 잡아먹힌다. WAN-203이 밴드 시딩·서브스텝 슬라이스에서 고친 것과 **같은 부류**의 초선형
#: 비용이고, 여기서도 고침은 **비트 동일**이다(테스트가 동작으로 고정).
#:
#: ⚠️ 다른 모드에서는 건너뛰면 안 된다 — 그 모드들은 실제로 RSI 값을 읽는다.
RSI_FREE_GATE_MODES: frozenset[str] = frozenset({"unconditional"})


def _null_rsi_state(
    *,
    start: int,
    substeps: Sequence[SubStep],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
) -> RealtimeRsi:
    """이 널 후보에 물릴 RSI 상태 — 게이트가 RSI를 안 읽으면 시딩을 건너뛴다(비트 동일)."""
    if params.rsi_gate_mode in RSI_FREE_GATE_MODES:
        return RealtimeRsi(length=params.rsi_length)
    cut = bisect.bisect_left(htf_times, substeps[start].htf_bar_time)
    return RealtimeRsi.seed_from_closed(htf_closes[:cut], length=params.rsi_length)


def _null_short_candidate(
    *,
    start: int,
    stop_ratio: float,
    substeps: Sequence[SubStep],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
) -> _Candidate | None:
    """서브스텝 `start` 종가에 숏 진입해 청산까지 돌린 후보. 기하가 무효면 None.

    지정가를 그 서브스텝 종가로 두면 숏은 즉시 체결되고(high ≥ close), 그 뒤 손절·익절
    로직이 실제 팔과 한 글자도 다르지 않다 — 실제와 널의 차이는 **진입 시각**뿐이다.
    손절은 진입가 위 `stop_ratio`(실제 숏들의 1R 비율 경험분포에서 재표집), 익절은 그
    1R의 고정 `take_profit_r` 아래(채택 규칙 그대로). 존이 없으므로 무효화 시각·지정가
    유효기간은 걸지 않는다(널은 그 자리에서 바로 체결되므로 유효기간이 무의미하다).
    """
    entry_price = substeps[start].close
    risk = entry_price * stop_ratio
    if entry_price <= 0.0 or risk <= 0.0:
        return None
    stop_price = entry_price + risk
    take_profit_price = entry_price - params.take_profit_r * risk
    if take_profit_price <= 0.0:
        return None
    rsi_state = _null_rsi_state(
        start=start, substeps=substeps, htf_times=htf_times, htf_closes=htf_closes, params=params
    )
    outcome = simulate_zone_limit_trade(
        direction=_direction(PositionSide.SHORT),
        limit_price=entry_price,
        stop_price=stop_price,
        substeps=substeps,
        start=start,
        rsi_state=rsi_state,
        rsi_oversold=params.rsi_oversold,
        rsi_overbought=params.rsi_overbought,
        take_profit_price=take_profit_price,
        limit_valid_bars=None,
        invalidation_time=None,
        rsi_gate_mode=params.rsi_gate_mode,
        rsi_neutral_band=params.rsi_neutral_band,
        penetration_bps=params.fill_penetration_bps,
    )
    if not outcome.filled or outcome.entry_time is None or outcome.entry_price is None:
        return None
    if outcome.status is ZoneLimitStatus.FILLED_EXITED:
        assert outcome.exit_time is not None and outcome.exit_price is not None
        reason = (
            ExitReason.TAKE_PROFIT
            if outcome.exit_reason is SignalExitReason.TAKE_PROFIT
            else ExitReason.STOP_LOSS
        )
        exit_time, exit_price = outcome.exit_time, outcome.exit_price
    else:
        exit_time, exit_price = substeps[-1].time, substeps[-1].close
        reason = ExitReason.END_OF_DATA
    return _Candidate(
        side=PositionSide.SHORT,
        entry_time=outcome.entry_time,
        entry_price=outcome.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        reason=reason,
        stop_price=stop_price,
        # 북이 따뜻한 OOS 경계로 후보를 거를 때 쓰는 키 — 널은 탭이 없으므로 진입 시각.
        trigger_time=outcome.entry_time,
    )


def _sample_indices(rng: random.Random, pool: Sequence[int], k: int) -> list[int]:
    """무작위 시각 `k`개. 풀이 넉넉하면 비복원, 아니면 복원(wan231과 같은 규약)."""
    if k <= 0 or not pool:
        return []
    if len(pool) >= k:
        return rng.sample(list(pool), k)
    return [rng.choice(list(pool)) for _ in range(k)]


def draw_null_shorts(
    *,
    seed: int,
    k: int,
    stop_ratios: Sequence[float],
    pool: Sequence[int],
    substeps: Sequence[SubStep],
    htf_times: Sequence[int],
    htf_closes: Sequence[float],
    params: ConfluenceParams,
) -> list[_Candidate]:
    """한 시드의 널 숏 `k`건 — 시각은 무작위, 기하(1R 비율)는 실제 분포에서 재표집."""
    if k <= 0 or not stop_ratios or not pool:
        return []
    rng = random.Random(seed)
    out: list[_Candidate] = []
    for idx in _sample_indices(rng, pool, k):
        cand = _null_short_candidate(
            start=idx,
            stop_ratio=rng.choice(list(stop_ratios)),
            substeps=substeps,
            htf_times=htf_times,
            htf_closes=htf_closes,
            params=params,
        )
        if cand is not None:
            out.append(cand)
    return out


# --------------------------------------------------------------------------- #
# 칸 단위 널 생성 (fan-out)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _NullTask:
    """fan-out 한 단위 = (심볼, TF) 칸 — 워커가 자기 데이터를 자기가 로드한다.

    실제 숏의 **개수**와 **1R 비율 분포**는 이미 만들어진 셀(WAN-282 `run_arm_cells`)에서
    뽑아 실어 보낸다 — 워커가 오더블록을 다시 탐지해 개수를 재세면 실제 팔과 갈라진다.
    """

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    fill_name: str
    seeds: int
    counts: tuple[tuple[str, int], ...]
    """구간 → 실제 숏 후보 수(base + 재진입)."""
    stop_ratios: tuple[tuple[str, tuple[float, ...]], ...]
    """구간 → 실제 숏들의 1R 비율(|진입−손절| ÷ 진입) 경험분포."""
    boundary_ms: int


@dataclass(frozen=True)
class NullDraws:
    """한 칸이 낸 널 숏 — 구간 × 시드. 북 그래프팅과 격리 자 양쪽에 쓰인다."""

    symbol: str
    timeframe: str
    draws: dict[str, tuple[tuple[_Candidate, ...], ...]]
    buy_hold: dict[str, float]
    """구간별 장세 라벨(비용 없는 원가 매수보유, WAN-89 `_buy_hold`) — 베타 축의 눈금."""


def _segment_window(market: harness.MarketData, segment: str, boundary_ms: int) -> tuple[int, int]:
    """[하한, 상한) 시각 — `is`는 경계 앞, `oos_warm`은 경계 뒤(북과 같은 규약)."""
    first = int(market.htf_df["open_time"].iloc[0])
    last = int(market.htf_df["open_time"].iloc[-1])
    if segment == SEGMENT_OOS_WARM:
        return boundary_ms, last + 1
    return first, boundary_ms


def run_null_cell(task: _NullTask, *, log: bool = True) -> NullDraws | None:
    """한 칸의 널 숏을 구간 × 시드로 낸다(실제 숏은 이미 셀에 있다)."""
    market = harness.load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=True,
    )
    if market.empty or market.df_1m.empty:
        if log:
            print(f"[wan284] {task.symbol} {task.timeframe}: 데이터 없음 — 건너뜀", flush=True)
        return None

    params = harness.build_params(fill=harness.fill_preset(task.fill_name), short_enabled=True)
    frame = _prepare_htf(market.htf_df)
    htf_times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    htf_closes = [float(v) for v in frame["close"].astype(float).tolist()]
    substeps = build_substeps(market.df_1m, timeframe_to_ms(task.timeframe))
    substep_times = [s.time for s in substeps]

    counts = dict(task.counts)
    ratios = {seg: vals for seg, vals in task.stop_ratios}
    draws: dict[str, tuple[tuple[_Candidate, ...], ...]] = {}
    buy_hold: dict[str, float] = {}
    for segment in NULL_SEGMENTS:
        lo, hi = _segment_window(market, segment, task.boundary_ms)
        start = bisect.bisect_left(substep_times, lo)
        end = bisect.bisect_left(substep_times, hi)
        pool = tuple(range(start, end))
        seg_frame = market.htf_df[
            (market.htf_df["open_time"] >= lo) & (market.htf_df["open_time"] < hi)
        ]
        buy_hold[segment] = _buy_hold(seg_frame)
        draws[segment] = tuple(
            tuple(
                draw_null_shorts(
                    seed=BASE_SEED + i,
                    k=counts.get(segment, 0),
                    stop_ratios=ratios.get(segment, ()),
                    pool=pool,
                    substeps=substeps,
                    htf_times=htf_times,
                    htf_closes=htf_closes,
                    params=params,
                )
            )
            for i in range(task.seeds)
        )
    if log:
        made = {seg: len(draws[seg][0]) if draws[seg] else 0 for seg in NULL_SEGMENTS}
        print(f"[wan284] {task.symbol} {task.timeframe}: 널 {made} (시드 {task.seeds})", flush=True)
    return NullDraws(symbol=task.symbol, timeframe=task.timeframe, draws=draws, buy_hold=buy_hold)


def _stop_ratios(cands: Sequence[_Candidate]) -> tuple[float, ...]:
    """실제 숏들의 1R 비율(|진입−손절| ÷ 진입) — 널의 기하를 여기에 맞춘다."""
    out: list[float] = []
    for c in cands:
        if c.entry_price > 0:
            ratio = abs(c.stop_price - c.entry_price) / c.entry_price
            if ratio > 0:
                out.append(ratio)
    return tuple(out)


def build_null_tasks(
    cells: Sequence[CellPayload], *, start: str, end: str, fill_name: str, seeds: int
) -> list[_NullTask]:
    """셀에서 실제 숏 개수·기하를 읽어 워커 과제를 만든다."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    tasks: list[_NullTask] = []
    for cell in cells:
        counts: list[tuple[str, int]] = []
        ratios: list[tuple[str, tuple[float, ...]]] = []
        for segment in NULL_SEGMENTS:
            shorts = _short_candidates(cell, segment)
            counts.append((segment, len(shorts)))
            ratios.append((segment, _stop_ratios(shorts)))
        tasks.append(
            _NullTask(
                symbol=cell.symbol,
                timeframe=cell.timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                fill_name=fill_name,
                seeds=seeds,
                counts=tuple(counts),
                stop_ratios=tuple(ratios),
                boundary_ms=cell.boundary_ms,
            )
        )
    return tasks


def run_nulls(tasks: Sequence[_NullTask], *, jobs: int = 1, log: bool = True) -> list[NullDraws]:
    """칸별 널 생성 — `--jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    if jobs <= 1:
        return [d for t in tasks if (d := run_null_cell(t, log=log)) is not None]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run_null_cell, tasks))
    return [d for d in results if d is not None]


# --------------------------------------------------------------------------- #
# 자 1 — 격리 순 R (완료기준 1·2)
# --------------------------------------------------------------------------- #


class ShortNullRow(BaseModel):
    """한 (심볼, TF) × 구간 × 렌즈의 격리 저항-존 숏 수익 자 대 매칭 널."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    fill: str
    segment: str
    seeds: int
    buy_hold: float
    """장세 라벨(비용 없는 매수보유 수익률) — 음수면 하락 구간(숏의 베타가 크다)."""
    actual_candidates: int
    """실제 숏 후보 수(= 널의 추출 개수)."""
    actual_trades: int
    """단독 시퀀싱 뒤 실제로 잡힌 숏 거래 수(20건 게이트의 대상)."""
    actual_net_r: float
    null_mean_trades: float
    null_mean_net_r: float
    """널 평균 순 R = **베타 대리**(같은 창에 무작위 시각으로 들어간 숏이 버는 몫)."""
    null_median_net_r: float
    null_min_net_r: float
    null_max_net_r: float
    p_value: float | None
    """단측 순위 p = (1 + #{널 ≥ 실제}) / (1 + 시드). 널이 없으면 None."""
    null_mean_per_trade_r: float
    """널의 **거래당** 평균 순 R(시드별 합÷거래수의 평균) — 거래 수 비대칭 통제용."""
    p_value_per_trade: float | None
    """거래당 순 R 자에서의 단측 순위 p — 아래 ⚠️ 때문에 **함께** 읽어야 한다.

    ⚠️ 널은 개수를 **후보 수**로 맞추는데, 실제 숏은 같은 존 재탭이 겹쳐 단독 시퀀싱에서
    더 많이 잘려 나간다(실측: 널이 조금 더 많이 거래한다). 합(`p_value`)만 보면 그 비대칭이
    널에 유리하게 작용하므로, **거래당** 자를 나란히 둬 통제한다. 두 자가 갈리면 판정은
    「합이 크다」가 아니라 「거래당이 낫다」 쪽으로 읽는다.
    """

    @field_validator("p_value", "p_value_per_trade", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def alpha_net_r(self) -> float:
        """실제 − 널 평균 = 타이밍이 더한 몫(베타를 뺀 나머지)."""
        return self.actual_net_r - self.null_mean_net_r

    @property
    def actual_per_trade_r(self) -> float:
        return self.actual_net_r / self.actual_trades if self.actual_trades else 0.0

    @property
    def sample_ok(self) -> bool:
        return self.actual_trades >= MIN_TRADES_GATE

    @property
    def significant(self) -> bool:
        """합 자 유의 — 20거래 이상 · p ≤ 0.05 · 실제 > 널 평균(WAN-84/231 관행)."""
        return (
            self.sample_ok
            and self.p_value is not None
            and self.p_value <= ALPHA
            and self.actual_net_r > self.null_mean_net_r
        )

    @property
    def significant_per_trade(self) -> bool:
        """거래당 자 유의 — 거래 수 비대칭을 통제한 판정."""
        return (
            self.sample_ok
            and self.p_value_per_trade is not None
            and self.p_value_per_trade <= ALPHA
            and self.actual_per_trade_r > self.null_mean_per_trade_r
        )


def _isolated_net_r(
    cands: Sequence[_Candidate], cfg: BacktestConfig, rates: Sequence[FundingRate]
) -> tuple[int, float]:
    """단독 시퀀싱 뒤 (거래 수, 순 R 합) — WAN-282 §3과 같은 자."""
    paired = sequence_with_candidates(list(cands), cfg, rates)
    total = sum(_net_r(t.return_pct, t.entry_price, c.stop_price) for c, t in paired)
    return len(paired), total


def _funding_for(cell: CellPayload, segment: str) -> tuple[FundingRate, ...]:
    return cell.funding[SEGMENT_FULL] if segment == SEGMENT_OOS_WARM else cell.funding[segment]


def build_short_null_rows(
    cells: Sequence[CellPayload], draws: Sequence[NullDraws], *, fill: str
) -> list[ShortNullRow]:
    """격리 순 R 자에서 실제 저항-존 숏 대 무작위-시각 숏(시드 20)."""
    by_key = {(d.symbol, d.timeframe): d for d in draws}
    rows: list[ShortNullRow] = []
    for cell in cells:
        drawn = by_key.get((cell.symbol, cell.timeframe))
        if drawn is None:
            continue
        cfg = harness.build_config(cell.timeframe)
        rates = {seg: _funding_for(cell, seg) for seg in NULL_SEGMENTS}
        for segment in NULL_SEGMENTS:
            shorts = _short_candidates(cell, segment)
            n_actual, actual_net = _isolated_net_r(shorts, cfg, rates[segment])
            seeds = drawn.draws.get(segment, ())
            null_totals: list[float] = []
            null_counts: list[int] = []
            null_per_trade: list[float] = []
            for seed_cands in seeds:
                n_null, net = _isolated_net_r(seed_cands, cfg, rates[segment])
                null_totals.append(net)
                null_counts.append(n_null)
                null_per_trade.append(net / n_null if n_null else 0.0)
            actual_per_trade = actual_net / n_actual if n_actual else 0.0
            rows.append(
                ShortNullRow(
                    symbol=cell.symbol,
                    timeframe=cell.timeframe,
                    fill=fill,
                    segment=segment,
                    seeds=len(null_totals),
                    buy_hold=drawn.buy_hold.get(segment, 0.0),
                    actual_candidates=len(shorts),
                    actual_trades=n_actual,
                    actual_net_r=actual_net,
                    null_mean_trades=(sum(null_counts) / len(null_counts) if null_counts else 0.0),
                    null_mean_net_r=(sum(null_totals) / len(null_totals) if null_totals else 0.0),
                    null_median_net_r=_median(null_totals),
                    null_min_net_r=min(null_totals) if null_totals else 0.0,
                    null_max_net_r=max(null_totals) if null_totals else 0.0,
                    p_value=rank_p_value(actual_net, null_totals),
                    null_mean_per_trade_r=(
                        sum(null_per_trade) / len(null_per_trade) if null_per_trade else 0.0
                    ),
                    p_value_per_trade=rank_p_value(actual_per_trade, null_per_trade),
                )
            )
    return rows


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


# --------------------------------------------------------------------------- #
# 자 2 — 북 수익/MDD (완료기준 1)
# --------------------------------------------------------------------------- #


def _replace_shorts(
    cell: CellPayload, segment: str, null_shorts: Sequence[_Candidate]
) -> CellPayload:
    """이 셀의 숏을 널 숏으로 갈아끼운다 — **롱은 글자 그대로 그대로** 둔다.

    `oos_warm`은 북이 `full` 키를 칸 경계로 걸러 쓰므로 `full` 키를, `is`는 `is` 키를
    바꾼다. 재진입 후보에서도 숏을 걷어낸다(널이 그 구조를 대신 표현한다 — 널 `k`는
    base + 재진입 숏의 합이다). 널 숏은 `trigger_time = entry_time`이라 경계 필터를
    실제와 같은 규칙으로 통과한다.
    """
    key = SEGMENT_FULL if segment == SEGMENT_OOS_WARM else segment
    longs = tuple(c for c in cell.candidates[key] if c.side is not PositionSide.SHORT)
    re_longs = tuple(
        c for c in cell.reentry_candidates.get(key, ()) if c.side is not PositionSide.SHORT
    )
    return dataclasses.replace(
        cell,
        candidates={**cell.candidates, key: (*longs, *null_shorts)},
        reentry_candidates={**cell.reentry_candidates, key: re_longs},
    )


def _drop_shorts(cell: CellPayload) -> CellPayload:
    """숏을 전부 걷어낸 셀 — 롱-온리 기준선(같은 롱 후보 위)."""
    return dataclasses.replace(
        cell,
        candidates={
            k: tuple(c for c in v if c.side is not PositionSide.SHORT)
            for k, v in cell.candidates.items()
        },
        reentry_candidates={
            k: tuple(c for c in v if c.side is not PositionSide.SHORT)
            for k, v in cell.reentry_candidates.items()
        },
    )


class BookNullRow(BaseModel):
    """한 스코프 × 구간 × 렌즈 × 제외종목의 북 수익 자 대 매칭 널."""

    model_config = ConfigDict(frozen=True)

    scope: str
    fill: str
    segment: str
    exclude_symbol: str = ""
    seeds: int
    num_cells: int
    long_only_return: float
    long_only_mdd: float
    long_only_return_over_mdd: float | None
    actual_trades: int
    actual_return: float
    actual_mdd: float
    actual_return_over_mdd: float | None
    null_mean_trades: float
    null_mean_return: float
    null_mean_mdd: float
    null_mean_return_over_mdd: float | None
    null_median_return_over_mdd: float | None
    p_return: float | None
    p_return_over_mdd: float | None

    @field_validator(
        "long_only_return_over_mdd",
        "actual_return_over_mdd",
        "null_mean_return_over_mdd",
        "null_median_return_over_mdd",
        "p_return",
        "p_return_over_mdd",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def sample_ok(self) -> bool:
        return self.actual_trades >= MIN_TRADES_GATE

    @property
    def significant_rm(self) -> bool:
        return (
            self.sample_ok
            and self.p_return_over_mdd is not None
            and self.p_return_over_mdd <= ALPHA
            and self.actual_return_over_mdd is not None
            and self.null_mean_return_over_mdd is not None
            and self.actual_return_over_mdd > self.null_mean_return_over_mdd
        )


@dataclass(frozen=True)
class _BookStat:
    trades: int
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None


def _book_stat(
    cells: Sequence[CellPayload], *, segment: str, start_ms: int, end_ms: int
) -> _BookStat | None:
    rows = book_cli.build_book_rows(
        list(cells),
        book=ADOPTED_BOOK,
        segments=(segment,),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
    )
    if not rows:
        return None
    br = rows[0]
    return _BookStat(
        trades=br.num_trades,
        total_return=br.total_return,
        max_drawdown=br.max_drawdown,
        return_over_mdd=br.return_over_mdd,
    )


def _mean_opt(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _median_opt(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return _median(present) if present else None


def build_book_null_rows(
    cells: Sequence[CellPayload],
    draws: Sequence[NullDraws],
    *,
    start_ms: int,
    end_ms: int,
    fill: str,
) -> list[BookNullRow]:
    """채택 북 수익 자에서 실제 롱+숏 대 「숏만 널로 갈아끼운」 북 20개 + 롱-온리 기준선."""
    by_key = {(d.symbol, d.timeframe): d for d in draws}
    timeframes = sorted({c.timeframe for c in cells}, key=timeframe_to_ms)
    scopes: list[str] = (
        [cross_scope_label(timeframes), *timeframes] if len(timeframes) > 1 else list(timeframes)
    )
    seeds = min((len(d.draws.get(NULL_SEGMENTS[0], ())) for d in draws), default=0)
    rows: list[BookNullRow] = []
    for scope in scopes:
        scoped = cells if scope == "all" else [c for c in cells if c.timeframe == scope]
        for exclude in ["", *LOO_SYMBOLS, LOO_ALL]:
            dropped = set(LOO_SYMBOLS) if exclude == LOO_ALL else ({exclude} if exclude else set())
            kept = [c for c in scoped if _short(c.symbol) not in dropped]
            if not kept:
                continue
            for segment in NULL_SEGMENTS:
                actual = _book_stat(kept, segment=segment, start_ms=start_ms, end_ms=end_ms)
                long_only = _book_stat(
                    [_drop_shorts(c) for c in kept],
                    segment=segment,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
                if actual is None or long_only is None:
                    continue
                null_stats: list[_BookStat] = []
                for seed_idx in range(seeds):
                    grafted: list[CellPayload] = []
                    for cell in kept:
                        drawn = by_key.get((cell.symbol, cell.timeframe))
                        seed_cands = (
                            drawn.draws[segment][seed_idx]
                            if drawn is not None and seed_idx < len(drawn.draws.get(segment, ()))
                            else ()
                        )
                        grafted.append(_replace_shorts(cell, segment, seed_cands))
                    stat = _book_stat(grafted, segment=segment, start_ms=start_ms, end_ms=end_ms)
                    if stat is not None:
                        null_stats.append(stat)
                rows.append(
                    BookNullRow(
                        scope=scope,
                        fill=fill,
                        segment=segment,
                        exclude_symbol=exclude,
                        seeds=len(null_stats),
                        num_cells=len(kept),
                        long_only_return=long_only.total_return,
                        long_only_mdd=long_only.max_drawdown,
                        long_only_return_over_mdd=long_only.return_over_mdd,
                        actual_trades=actual.trades,
                        actual_return=actual.total_return,
                        actual_mdd=actual.max_drawdown,
                        actual_return_over_mdd=actual.return_over_mdd,
                        null_mean_trades=(
                            sum(s.trades for s in null_stats) / len(null_stats)
                            if null_stats
                            else 0.0
                        ),
                        null_mean_return=(
                            sum(s.total_return for s in null_stats) / len(null_stats)
                            if null_stats
                            else 0.0
                        ),
                        null_mean_mdd=(
                            sum(s.max_drawdown for s in null_stats) / len(null_stats)
                            if null_stats
                            else 0.0
                        ),
                        null_mean_return_over_mdd=_mean_opt(
                            [s.return_over_mdd for s in null_stats]
                        ),
                        null_median_return_over_mdd=_median_opt(
                            [s.return_over_mdd for s in null_stats]
                        ),
                        p_return=rank_p_value(
                            actual.total_return, [s.total_return for s in null_stats]
                        ),
                        p_return_over_mdd=(
                            rank_p_value(
                                actual.return_over_mdd,
                                [
                                    s.return_over_mdd
                                    for s in null_stats
                                    if s.return_over_mdd is not None
                                ],
                            )
                            if actual.return_over_mdd is not None
                            else None
                        ),
                    )
                )
    return rows


# --------------------------------------------------------------------------- #
# 검산 — 실제 팔이 WAN-282 §3 격리 진단과 같은가 (완료기준 4)
# --------------------------------------------------------------------------- #

#: 두 모듈의 부동소수 끝자리 잡음 한계. 이보다 크면 **불일치**로 찍는다(조용한 통과 금지).
CROSSCHECK_TOL = 1e-9

WAN282_DIAG_CSV = REPORTS_DIR / "wan282_resistance_short_diag.csv"


def crosscheck_wan282(
    rows: Sequence[ShortNullRow], diag_csv: Path = WAN282_DIAG_CSV
) -> tuple[str, float, int]:
    """이 표의 **실제 팔**이 WAN-282 §3 격리 숏 진단과 비트 일치하는지 (설명, 최대차, 대조행수).

    두 모듈은 같은 자를 쓴다 — 같은 `_short_candidates` 필터 · 같은 `sequence_with_candidates`
    단독 시퀀싱 · 같은 `_net_r`(WAN-154). 그래서 실제 팔의 `actual_net_r`·`actual_trades`는
    `wan282_resistance_short_diag.csv`의 `net_r_sum`·`short_trades`와 **비트 단위로 같아야
    한다**. 이 검산이 통과하면 이 이슈가 더한 것은 오직 **널**이라는 직접 증거다(실제 팔을
    새로 짜다가 조용히 갈라지는 WAN-91/95/112 부류를 막는다).

    코드가 **일치 · 잡음 · 불일치를 다르게 찍는다**(WAN-151 패턴) — 대조할 행이 없으면 그
    사실을 말하지 조용히 통과하지 않는다.
    """
    if not diag_csv.exists():
        return (f"⚠️ 검산 불가 — `{diag_csv}`가 없습니다(WAN-282 미실행).", float("nan"), 0)
    frame = pd.read_csv(diag_csv, keep_default_na=False)
    ref = {
        (str(r["symbol"]), str(r["timeframe"]), str(r["fill"]), str(r["segment"])): (
            float(r["net_r_sum"]),
            int(r["short_trades"]),
        )
        for r in frame.to_dict(orient="records")
    }
    worst = 0.0
    compared = 0
    mismatched: list[str] = []
    for row in rows:
        key = (row.symbol, row.timeframe, row.fill, row.segment)
        hit = ref.get(key)
        if hit is None:
            continue
        compared += 1
        diff = abs(row.actual_net_r - hit[0])
        worst = max(worst, diff)
        if diff > CROSSCHECK_TOL or row.actual_trades != hit[1]:
            mismatched.append(f"{_short(row.symbol)} {row.timeframe} {row.segment}")
    if compared == 0:
        return (
            "⚠️ 검산 불가 — WAN-282 진단 CSV에 겹치는 (심볼, TF, 렌즈, 구간)이 없습니다.",
            0.0,
            0,
        )
    if mismatched:
        return (
            f"🚨 **불일치 {len(mismatched)}행** — {', '.join(mismatched[:5])}"
            f"{' 외' if len(mismatched) > 5 else ''} (최대차 {worst:.2e}). 실제 팔이 WAN-282 "
            "§3과 갈라졌습니다.",
            worst,
            compared,
        )
    label = "비트 일치" if worst == 0.0 else f"부동소수 잡음({worst:.2e})"
    return (
        f"✅ 실제 팔 ≡ WAN-282 §3 격리 숏 진단 — **{compared}행 {label}**"
        f"(`net_r_sum`·`short_trades`). 이 이슈가 더한 것은 오직 **널**이다.",
        worst,
        compared,
    )


# --------------------------------------------------------------------------- #
# 판정 (완료기준 2 — 베타/알파)
# --------------------------------------------------------------------------- #


def _lenses_present(rows: Sequence[ShortNullRow] | Sequence[BookNullRow]) -> list[str]:
    present = {r.fill for r in rows}
    ordered = [lens for lens in LENS_ORDER if lens in present]
    ordered += sorted(present - set(ordered))
    return ordered


def _eligible(rows: Sequence[ShortNullRow], *, segment: str, fill: str) -> list[ShortNullRow]:
    return [r for r in rows if r.segment == segment and r.fill == fill and r.sample_ok]


@dataclass(frozen=True)
class AxisStat:
    """한 작업 TF(축)의 격리 순 R 자 성적 — 판정 문자는 셀 수에서 계산한다."""

    timeframe: str
    cells: int
    """그 축의 행 수(20거래 게이트 이전)."""
    eligible: int
    """유효 셀(20거래 이상) — 게이트에 걸린 셀은 판정에서 빠진다."""
    significant: int
    significant_per_trade: int
    alpha: float
    beat: int

    @property
    def strongest(self) -> int:
        """두 자 중 더 많이 유의한 쪽 — 판정은 강한 쪽으로 낸다(합 대 거래당)."""
        return max(self.significant, self.significant_per_trade)

    @property
    def letter(self) -> str:
        if not self.eligible:
            return "?"
        if self.strongest * 2 > self.eligible:
            return "a"
        return "c" if self.strongest else "b"

    @property
    def passes(self) -> bool:
        """이 축이 매칭 널을 **이겼다**고 부를 수 있는가 = 유효 셀의 과반이 유의."""
        return self.letter == "a"


def axis_stats(
    rows: Sequence[ShortNullRow], *, segment: str = SEGMENT_OOS_WARM, fill: str = "baseline"
) -> list[AxisStat]:
    """축(TF)별 격리 순 R 성적 — 완료기준 2의 「이기는 축이 있는가」를 행에서 센다."""
    scoped = [r for r in rows if r.segment == segment and r.fill == fill]
    out: list[AxisStat] = []
    for tf in sorted({r.timeframe for r in scoped}, key=timeframe_to_ms):
        cells = [r for r in scoped if r.timeframe == tf]
        eligible = [r for r in cells if r.sample_ok]
        out.append(
            AxisStat(
                timeframe=tf,
                cells=len(cells),
                eligible=len(eligible),
                significant=sum(1 for r in eligible if r.significant),
                significant_per_trade=sum(1 for r in eligible if r.significant_per_trade),
                alpha=sum(r.alpha_net_r for r in eligible),
                beat=sum(1 for r in eligible if r.actual_net_r > r.null_mean_net_r),
            )
        )
    return out


def _axis_sentence(stats: Sequence[AxisStat]) -> str:
    if not stats:
        return ""
    parts = [
        f"{s.timeframe} ({s.letter}) 유효 {s.eligible}/{s.cells}셀 · 유의 합 {s.significant} · "
        f"거래당 {s.significant_per_trade} · 알파 {s.alpha:+.1f}"
        for s in stats
    ]
    return " **축별(따뜻한 OOS·baseline):** " + " / ".join(parts) + "."


#: 채택 작업 TF(WAN-252) — 완료기준 2의 「네 작업 TF 전부 …」를 라벨이 아니라 목록으로 센다.
WORKING_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "2h", "4h")


def _book_pick(rows: Sequence[BookNullRow], *, scope: str, fill: str) -> BookNullRow | None:
    return next(
        (
            b
            for b in rows
            if b.scope == scope
            and b.segment == SEGMENT_OOS_WARM
            and b.fill == fill
            and not b.exclude_symbol
        ),
        None,
    )


def _beats_null(row: BookNullRow) -> bool:
    a, n = row.actual_return_over_mdd, row.null_mean_return_over_mdd
    return a is not None and n is not None and a > n


def _book_sentence(book_rows: Sequence[BookNullRow], fill: str) -> str:
    """북 자를 **스코프별로** 읽는다 — 축을 이어 붙이면 스코프가 TF마다 하나씩 는다."""
    scopes = sorted({b.scope for b in book_rows}, key=_scope_sort_key)
    picked = [row for s in scopes if (row := _book_pick(book_rows, scope=s, fill=fill))]
    if not picked:
        return ""
    beat = sum(1 for b in picked if _beats_null(b))
    sig = sum(1 for b in picked if b.significant_rm)
    detail = " / ".join(
        f"{b.scope} 롱온리 {_rr(b.long_only_return_over_mdd)} → 실제 "
        f"{_rr(b.actual_return_over_mdd)} vs 널 {_rr(b.null_mean_return_over_mdd)}"
        f"(p={_p(b.p_return_over_mdd)})"
        for b in picked
    )
    return (
        f" **북 자(따뜻한 OOS · 스코프 {len(picked)}개):** 널을 이긴 스코프 "
        f"{beat}/{len(picked)} · 유의 {sig}/{len(picked)} — {detail}. 즉 숏을 얹어 수익/MDD가 "
        "올라도 **그 상승분이 무작위 숏으로도 나오는지**가 판정이다."
    )


def _loo_signs(book_rows: Sequence[BookNullRow], *, scope: str, fill: str) -> tuple[int, int]:
    """그 스코프의 leave-one-out에서 실제 > 널 부호가 유지된 팔 수 / 전체."""
    rows = [
        b
        for b in book_rows
        if b.scope == scope
        and b.segment == SEGMENT_OOS_WARM
        and b.fill == fill
        and b.exclude_symbol
    ]
    return sum(1 for b in rows if _beats_null(b)), len(rows)


def _conclusion(
    axes: Sequence[AxisStat],
    short_rows: Sequence[ShortNullRow],
    book_rows: Sequence[BookNullRow],
) -> str:
    """완료기준 2 — 이기는 축이 있는가, 있으면 pen_5bp·leave-one-out에서 살아남는가."""
    covered = [s.timeframe for s in axes]
    missing = [tf for tf in WORKING_TIMEFRAMES if tf not in covered]
    passing = [s.timeframe for s in axes if s.passes]
    if not passing:
        label = (
            "네 작업 TF 전부" if not missing else f"측정한 축({', '.join(covered) or '없음'}) 전부"
        )
        tail = f" (미측정 축: {', '.join(missing)})" if missing else ""
        return (
            f" **종합: {label} 채택 근거 아님** — 저항-존 숏의 수익 자가 매칭 널을 유효 셀의 "
            f"과반으로 이기는 축이 하나도 없다.{tail}"
        )
    pen_pass = {s.timeframe for s in axis_stats(short_rows, fill="pen_5bp") if s.passes}
    survived = [tf for tf in passing if tf in pen_pass]
    loo: list[str] = []
    for tf in passing:
        ok, total = _loo_signs(book_rows, scope=tf, fill="baseline")
        if total:
            loo.append(f"{tf} {ok}/{total}")
    return (
        f" **종합: 이기는 축 {', '.join(passing)}** — 그중 pen_5bp(체결 보수화)에서도 이기는 축 "
        f"{', '.join(survived) if survived else '없음'} · 북 자 leave-one-out 부호 유지"
        f"{(' ' + ' / '.join(loo)) if loo else ' —'}. ⚠️ **그래도 채택 근거가 아니다** — 측정 "
        "전용이고 채택은 재-베이스라인 = 사용자 결정이다."
    )


def verdict(short_rows: Sequence[ShortNullRow], book_rows: Sequence[BookNullRow]) -> str:
    """저항-존 숏의 **수익**이 무작위 숏 대비 초과분(알파)인가, 하락 노출(베타)인가.

    숫자는 전부 행에서 계산한다(문장에 박으면 재실행 뒤 거짓말 — WAN-164 관행).
    """
    lens = "baseline"
    eligible = _eligible(short_rows, segment=SEGMENT_OOS_WARM, fill=lens)
    if not eligible:
        return "**판정 불가** — 따뜻한 OOS에서 20거래 기준을 넘긴 격리 숏 셀이 없습니다."
    sig = [r for r in eligible if r.significant]
    sig_pt = [r for r in eligible if r.significant_per_trade]
    beat = [r for r in eligible if r.actual_net_r > r.null_mean_net_r]
    actual_sum = sum(r.actual_net_r for r in eligible)
    null_sum = sum(r.null_mean_net_r for r in eligible)
    falling = sum(1 for r in eligible if r.buy_hold < 0)
    null_pos = sum(1 for r in eligible if r.null_mean_net_r > 0)
    strongest = max(len(sig), len(sig_pt))

    if strongest * 2 > len(eligible):
        head = (
            f"**(a) 알파 쪽 — 유효 {len(eligible)}셀 중 합 자 {len(sig)}셀 · 거래당 자 "
            f"{len(sig_pt)}셀이 무작위 숏을 유의하게 이긴다.** 수익이 하락 노출만으로 "
            "설명되지 않는다."
        )
    elif strongest:
        head = (
            f"**(c) 갈린다 — 유효 {len(eligible)}셀 중 합 자 {len(sig)}셀 · 거래당 자 "
            f"{len(sig_pt)}셀만 유의**(과반 미달)이고 나머지는 무작위 숏과 구분되지 않는다."
        )
    else:
        head = (
            f"**(b) 베타/평균회귀로 설명된다 — 유효 {len(eligible)}셀 중 유의 0셀"
            "(합 자·거래당 자 모두).** 저항-존 숏의 플러스 순 R은 「저항 존을 골랐기 때문」이 "
            "아니라 **같은 창에 아무 시각으로나 숏을 쳐도 나오는 몫**이다."
        )

    body = (
        f" 따뜻한 OOS·{lens}: 실제 순R 합 {actual_sum:+.1f} vs 널 평균 합 {null_sum:+.1f} "
        f"(알파 {actual_sum - null_sum:+.1f}) · 널을 이긴 셀 {len(beat)}/{len(eligible)} · "
        f"널 평균이 플러스인 셀 {null_pos}/{len(eligible)} · 하락 구간(buy_hold<0) 셀 "
        f"{falling}/{len(eligible)}."
    )

    axes = axis_stats(short_rows, segment=SEGMENT_OOS_WARM, fill=lens)
    axis_txt = _axis_sentence(axes)

    pen = _eligible(short_rows, segment=SEGMENT_OOS_WARM, fill="pen_5bp")
    if pen:
        pen_sig = sum(1 for r in pen if r.significant)
        pen_sig_pt = sum(1 for r in pen if r.significant_per_trade)
        pen_alpha = sum(r.alpha_net_r for r in pen)
        pen_axes = axis_stats(short_rows, segment=SEGMENT_OOS_WARM, fill="pen_5bp")
        pen_pass = [s.timeframe for s in pen_axes if s.passes]
        pen_txt = (
            f" **pen_5bp 병기:** 유효 {len(pen)}셀 중 유의 합 {pen_sig}셀 · 거래당 "
            f"{pen_sig_pt}셀 · 알파 합 {pen_alpha:+.1f} · 관통을 요구해도 이기는 축 "
            f"{', '.join(pen_pass) if pen_pass else '없음'}."
        )
    else:
        pen_txt = " (pen_5bp 행 없음 — `--fill pen_5bp --append` 미실행.)"

    return (
        f"{head}{body}{axis_txt}{pen_txt}{_book_sentence(book_rows, lens)}"
        f"{_conclusion(axes, short_rows, book_rows)}"
        " ⚠️ 총수익%는 복리 착시라 방향만 읽는다(WAN-213). "
        "전부 `baseline`(닿으면 체결) 낙관 위 값이고 숏은 존 경계 체결이라 큐 우선순위에 특히 "
        "약하다(실해소는 틱·호가 WAN-98, Canceled). 「엣지 없음」(WAN-84/88/111/114/124/151/201/"
        "248)은 다른 질문이라 불변이고, 유의가 나와도 진입 알파가 아니라 위험의 모양(WAN-90)일 "
        "수 있다. **숏 재활성화 아님**(`short_enabled=False` 기본값 유지) · 채택은 재-베이스라인 "
        "= 사용자 결정 · 개발자 임의 착수 금지."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복 · 병합
# --------------------------------------------------------------------------- #


def short_to_frame(rows: Sequence[ShortNullRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(ShortNullRow.model_fields))


def book_to_frame(rows: Sequence[BookNullRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookNullRow.model_fields))


#: CSV 왕복을 **무손실**로 만드는 파서 옵션 — `--append`가 남의 행을 바꾸지 않게 하는 장치.
#:
#: pandas 기본 부동소수 파서는 마지막 자리에서 값을 바꿀 수 있다. 이 모듈은 축을 `--append`로
#: 이으므로 **읽고 다시 쓰는 일이 반복**되는데, 그때마다 손실이 얹히면 이미 확정된 축(WAN-284
#: 4h)의 행이 재실행도 안 했는데 CSV에서 슬금슬금 달라진다 — 완료기준 「기존 행을 안 건드림」이
#: 텍스트 층에서 깨진다. `round_trip`은 정확 반올림이라 `repr` 왕복이 바이트로 닫힌다.
FLOAT_ROUND_TRIP = "round_trip"


def short_from_csv(path: Path) -> list[ShortNullRow]:
    frame = pd.read_csv(path, keep_default_na=False, float_precision=FLOAT_ROUND_TRIP)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [ShortNullRow.model_validate(rec) for rec in records]


def book_from_csv(path: Path) -> list[BookNullRow]:
    frame = pd.read_csv(path, keep_default_na=False, float_precision=FLOAT_ROUND_TRIP)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [BookNullRow.model_validate(rec) for rec in records]


def merge_short(
    existing: Sequence[ShortNullRow], new: Sequence[ShortNullRow]
) -> list[ShortNullRow]:
    """(TF, 렌즈) 키로 병합 — 같은 렌즈의 같은 TF만 갱신하고 다른 렌즈·TF는 보존한다."""
    keys = {(r.timeframe, r.fill) for r in new}
    return [*[r for r in existing if (r.timeframe, r.fill) not in keys], *new]


def merge_book(existing: Sequence[BookNullRow], new: Sequence[BookNullRow]) -> list[BookNullRow]:
    """(스코프, 렌즈) 키로 병합 — WAN-283 PM 교정과 같은 규칙.

    단일 TF 실행은 `all`을 만들지 않으므로(`build_book_null_rows`), `--tf 15m --append`가
    자기 숫자를 `all` 라벨로 조용히 덮어쓰지 못한다(WAN-91/95/112/123/159 부류 방지).
    """
    keys = {(r.scope, r.fill) for r in new}
    return [*[r for r in existing if (r.scope, r.fill) not in keys], *new]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _scope_sort_key(scope: str) -> int:
    return -1 if is_cross_scope(scope) else timeframe_to_ms(scope)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _p(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def describe_engine() -> str:
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={OrderBlockParams().combine_obs}, max_zone_width_atr={p.max_zone_width_atr}, "
        f"limit_valid_bars={p.limit_valid_bars}, short_enabled={p.short_enabled}(기본), "
        f"book={ADOPTED_BOOK.leverage_mode}×{ADOPTED_BOOK.leverage_multiple}, "
        f"reentry={ADOPTED_REENTRY_ENTRY_RULE}"
    )


def _short_table(rows: Sequence[ShortNullRow], timeframe: str, segment: str) -> list[str]:
    """격리 순 R 자 — 심볼 × 렌즈. 알파 = 실제 − 널 평균(베타를 뺀 나머지)."""
    scoped = [r for r in rows if r.timeframe == timeframe and r.segment == segment]
    lenses = _lenses_present(scoped) or ["baseline"]
    lines = [
        "| 심볼 | 렌즈 | 장세(BH) | 숏후보 | 숏거래(실제/널) | 실제 순R | 널 평균 순R(베타) | "
        "알파 | p(합) | 거래당 실제/널 | p(거래당) | 유의 |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | :-: |",
    ]
    for symbol in sorted({r.symbol for r in scoped}):
        for lens in lenses:
            r = next((x for x in scoped if x.symbol == symbol and x.fill == lens), None)
            if r is None or r.actual_candidates == 0:
                continue
            fund = "†" if r.symbol in FUNDING_GAP_SYMBOLS else ""
            gate = "" if r.sample_ok else " ⚠️"
            mark = "✅" if r.significant else ("(거래당)" if r.significant_per_trade else "—")
            lines.append(
                f"| {_short(r.symbol)}{fund} | {lens} | {_pct(r.buy_hold)} | "
                f"{r.actual_candidates} | {r.actual_trades}{gate}/{r.null_mean_trades:.1f} | "
                f"{r.actual_net_r:+.1f} | {r.null_mean_net_r:+.1f} | {r.alpha_net_r:+.1f} | "
                f"{_p(r.p_value)} | {r.actual_per_trade_r:+.3f}/"
                f"{r.null_mean_per_trade_r:+.3f} | {_p(r.p_value_per_trade)} | {mark} |"
            )
    return lines


def _book_table(rows: Sequence[BookNullRow], scope: str, segment: str) -> list[str]:
    scoped = [r for r in rows if r.scope == scope and r.segment == segment and not r.exclude_symbol]
    lenses = _lenses_present(scoped) or ["baseline"]
    lines = [
        "| 렌즈 | 팔 | 거래 | 총수익%† | MDD | 수익/MDD | p(수익/MDD) |",
        "| -- | -- | --: | --: | --: | --: | --: |",
    ]
    for lens in lenses:
        r = next((x for x in scoped if x.fill == lens), None)
        if r is None:
            continue
        gate = "" if r.sample_ok else " ⚠️"
        lines.append(
            f"| {lens} | 롱-온리(숏 0) | — | {_pct(r.long_only_return)} | "
            f"{_pct(r.long_only_mdd)} | {_rr(r.long_only_return_over_mdd)} | — |"
        )
        lines.append(
            f"| {lens} | 실제 롱+숏 | {r.actual_trades}{gate} | {_pct(r.actual_return)} | "
            f"{_pct(r.actual_mdd)} | {_rr(r.actual_return_over_mdd)} | "
            f"{_p(r.p_return_over_mdd)} |"
        )
        lines.append(
            f"| {lens} | 널 롱+무작위숏(평균 {r.seeds}시드) | {r.null_mean_trades:.0f} | "
            f"{_pct(r.null_mean_return)} | {_pct(r.null_mean_mdd)} | "
            f"{_rr(r.null_mean_return_over_mdd)} | — |"
        )
    return lines


def _loo_table(rows: Sequence[BookNullRow], scope: str, segment: str) -> list[str]:
    """leave-one-out — 종목을 빼도 실제가 널을 이기는 방향이 유지되나(완료기준 3)."""
    scoped = [r for r in rows if r.scope == scope and r.segment == segment]
    lenses = _lenses_present(scoped) or ["baseline"]
    excludes = ["", *LOO_SYMBOLS, LOO_ALL]
    lines = [
        "| 렌즈 | 제외 | 실제 수익/MDD | 널 평균 수익/MDD | p | 실제−널 부호 |",
        "| -- | -- | --: | --: | --: | :-: |",
    ]
    for lens in lenses:
        for exclude in excludes:
            r = next((x for x in scoped if x.fill == lens and x.exclude_symbol == exclude), None)
            if r is None:
                continue
            a, n = r.actual_return_over_mdd, r.null_mean_return_over_mdd
            sign = "—" if a is None or n is None else ("＋" if a > n else "－")
            lines.append(
                f"| {lens} | {exclude or '없음(전체)'} | {_rr(a)} | {_rr(n)} | "
                f"{_p(r.p_return_over_mdd)} | {sign} |"
            )
    return lines


def build_summary_markdown(
    short_rows: Sequence[ShortNullRow],
    book_rows: Sequence[BookNullRow],
    *,
    short_csv: Path,
    book_csv: Path,
) -> str:
    timeframes = sorted({r.timeframe for r in short_rows}, key=timeframe_to_ms)
    scopes = sorted({r.scope for r in book_rows}, key=_scope_sort_key)
    lenses_txt = " · ".join(_lenses_present(short_rows) or ["baseline"])
    lines = [
        "# WAN-284/285 — 저항-존 숏을 「헤지」가 아니라 「수익」으로 재판정 (측정 전용)",
        "",
        f"**축** 4h는 WAN-284, {', '.join(tf for tf in WORKING_TIMEFRAMES if tf != '4h')}는 "
        "WAN-285가 같은 모듈에 `--append`로 이어 붙였다(새 파이프라인·새 기하 없음). 이 표에 "
        f"실린 축: `{', '.join(timeframes) or '없음'}`.",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()` · 채택 북 cap_only 5배 · "
        "재진입 ON band) 돌리며 핀을 하나도 쓰지 않는다. `short_enabled=False` **기본값 유지**"
        "(측정용 숏이지 재활성화 아님). 못 박은 6년 창(WAN-182) · 기본값·토대 불변"
        "(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "**무엇이 새 질문인가** — WAN-282/283은 **헤지 자**(전체 MDD)로 (c) 엇갈림을 냈다. "
        '수익 자로 읽으면 격리 숏 순 R이 플러스이고 수익/MDD도 대부분 오르는데, **"돈을 '
        '벌었다" ≠ "무작위보다 낫다"** 다 — 하락 구간엔 아무 숏이나 벌린다(시장 베타). '
        "이 표가 같은 구간·같은 개수의 **무작위 시각 숏**과 대조해 **알파(타이밍)와 "
        "베타(하락 노출)를 가른다**. WAN-164/201은 볼린저 무력화 풀의 **엣지 질문**이었지 "
        "수익 자가 아니었다.",
        "",
        "## 대조군 — 무작위 **시각** 숏 (개수·기하 고정)",
        "",
        "널은 실제 저항-존 숏과 **개수**(그 칸·구간의 숏 후보 수, base + band 재진입 합)와 "
        "**기하**(1R 비율 = |진입−손절| ÷ 진입 을 실제 분포에서 재표집)를 맞추고 **시각만** "
        "무작위로 바꾼다. 그 서브스텝 종가에 지정가를 두면 즉시 체결되고 이후 손절·익절은 "
        "엔진과 같은 `simulate_zone_limit_trade`가 돌린다 — **차이는 진입 시각뿐**이다. "
        "체결 렌즈(관통 벌점)는 실제·널에 **동일하게** 배선된다.",
        "",
        "⚠️ 널에는 **존이 없다** — 재진입 구조를 따로 모델링하지 않고 `k`개를 통째로 무작위 "
        "시각에 뿌린다. 그래서 이 표는 「저항 존이라는 자리 + 재무장 구조」를 합친 타이밍을 "
        "재지, 재진입만의 몫을 가르지 않는다.",
        "",
        "## 이 표가 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영(신규 3종목 BTC 대리). 렌즈: `{lenses_txt}`. "
        f"시드 {SEEDS}개 → 단측 순위 p 하한 {1 / (SEEDS + 1):.3f}. 유의 = 거래 "
        f"{MIN_TRADES_GATE}건 이상(WAN-84) **그리고** p ≤ {ALPHA} **그리고** 실제 > 널 평균.",
        "",
        f"재현: `uv run python -m backtest.wan284_resistance_short_profit_null --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` → `--fill pen_5bp --append` → 축마다 "
        "`--tf <TF> --append`와 `--tf <TF> --fill pen_5bp --append`(**한 TF씩** — 여러 TF를 "
        "한 번에 돌리면 그 TF들만 담은 교차 스코프가 생긴다). 요약만: `--from-csv`. "
        f"원자료: `{short_csv}`(격리 순R 자) · `{book_csv}`(북 수익/MDD 자).",
        "",
        "⚠️ **총수익%는 복리 착시**(WAN-213) — 방향만 읽는다. 판정은 **널 대비 초과분**이다.",
        "",
        "## 1. 격리 순 R 자 — 실제 저항-존 숏 대 무작위-시각 숏 (완료기준 1)",
        "",
        "`장세(BH)` = 그 구간의 비용 없는 매수보유 수익률(음수 = 하락 구간 → 숏의 **베타**가 "
        "크다). `널 평균 순R`이 곧 **베타 대리**이고 `알파` = 실제 − 널 평균이다. "
        "†=신규 종목(펀딩 BTC 대리).",
        "",
        "🚨 **합 자와 거래당 자를 함께 읽을 것** — 널은 개수를 **후보 수**로 맞추는데, 실제 "
        "숏은 같은 존 재탭이 겹쳐 단독 시퀀싱에서 더 많이 잘려 나간다(표의 `숏거래(실제/널)` "
        "가 그 비대칭을 드러낸다 — 널이 조금 더 많이 거래한다). 그래서 합(`p(합)`)만 보면 "
        "비대칭이 **널에 유리**하게 작용한다. `p(거래당)`이 그 통제이고, 두 자가 갈리면 "
        "판정은 거래당 쪽으로 읽는다. 유의 열의 `(거래당)`은 합 자로는 유의가 아닌데 거래당 "
        "자로만 유의라는 뜻이다.",
        "",
    ]
    for seg_title, segment in (
        ("oos_warm (주 수치)", SEGMENT_OOS_WARM),
        ("is (맥락)", SEGMENT_IS),
    ):
        lines += [f"### {seg_title}", ""]
        for tf in timeframes:
            table = _short_table(short_rows, tf, segment)
            if len(table) > 2:
                lines += [f"#### {tf}", "", *table, ""]
    lines += [
        "## 2. 북 수익/MDD 자 — 롱은 그대로, 숏만 널로",
        "",
        "실제 롱+숏 북과 **롱 후보가 글자 그대로 같은** 널 북(숏만 무작위 시각)을 대조한다. "
        "롱-온리(숏 0개)는 같은 롱 위의 기준선이다.",
        "",
        "⚠️ **스코프는 그 스코프가 실제로 담은 칸만 뜻한다** — 축을 한 TF씩 `--append`로 이었으므로 "
        "TF마다 독립된 북(그 TF 칸들만 공유 자본)이고, 네 TF를 한 지갑으로 묶은 교차 스코프는 "
        "네 TF를 **한 실행에서** 돌려야 나온다(그 라벨에는 구성 TF가 박힌다 — "
        f"`{cross_scope_label(('1h', '2h'))}` 꼴).",
        "",
    ]
    for scope in scopes:
        lines += [f"### {scope}", ""]
        for seg_title, segment in (
            ("oos_warm (주 수치)", SEGMENT_OOS_WARM),
            ("is (맥락)", SEGMENT_IS),
        ):
            table = _book_table(book_rows, scope, segment)
            if len(table) > 2:
                lines += [f"#### {seg_title}", "", *table, ""]
    lines += [
        "## 3. leave-one-out — 종목 편중 (완료기준 3 · oos_warm)",
        "",
        "수익이 DOGE·LINK·ETH가 만드는지. 신규 종목(DOGE·LINK)은 펀딩이 BTC 대리라 값에 가정이 "
        "섞여 있다. `실제−널 부호`가 종목을 빼도 유지되면 편중이 아니다.",
        "",
    ]
    for scope in scopes:
        table = _loo_table(book_rows, scope, SEGMENT_OOS_WARM)
        if len(table) > 2:
            lines += [f"### {scope}", "", *table, ""]
    lines += [
        "## 4. 검산 — 실제 팔이 WAN-282 §3과 같은가 (완료기준 4)",
        "",
        crosscheck_wan282(short_rows)[0],
        "",
        "두 모듈은 같은 자를 쓴다(같은 숏 필터 · 같은 단독 시퀀싱 · 같은 `_net_r` WAN-154). "
        "그래서 실제 팔은 `wan282_resistance_short_diag.csv`와 비트 단위로 같아야 하고, "
        "이 이슈가 더한 것은 **널**뿐이어야 한다. 렌즈(관통 벌점)는 실제·널 **양쪽에** 같은 "
        "`params.fill_penetration_bps`로 배선된다.",
        "",
        "## 판정 — 알파인가 베타인가 (완료기준 2)",
        "",
        verdict(short_rows, book_rows),
        "",
        "⚠️ **이 표는 채택 근거가 아니라 측정이다.** 「엣지 없음」(WAN-84/88/111/114/124/151/"
        "201/248)은 다른 질문(진입 규칙이 무작위와 구분되는가)이라 불변이고, 저항-존 숏 진입 "
        "엣지는 WAN-164/201이 이미 「없음」으로 냈다. **숏 재활성화 아님**"
        "(`short_enabled=False` 기본값 유지) · 채택은 재-베이스라인 = 사용자 결정 · 개발자 "
        "임의 착수 금지. **기본값·토대 불변**(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Wan284Report:
    short_rows: list[ShortNullRow]
    book_rows: list[BookNullRow]


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    fill: harness.FillPreset | None = None,
    seeds: int = SEEDS,
    jobs: int = 1,
    funding_proxy: bool = True,
    log: bool = True,
) -> Wan284Report:
    """롱+숏 셀(WAN-282 재사용) → 널 숏 생성 → 두 수익 자를 낸다."""
    cells = run_arm_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        short_enabled=True,
        fill=fill,
        jobs=jobs,
        funding_proxy=funding_proxy,
        log=log,
    )
    lens = fill.name if fill is not None else "baseline"
    tasks = build_null_tasks(cells, start=start, end=end, fill_name=lens, seeds=seeds)
    draws = run_nulls(tasks, jobs=jobs, log=log)
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    return Wan284Report(
        short_rows=build_short_null_rows(cells, draws, fill=lens),
        book_rows=build_book_null_rows(cells, draws, start_ms=start_ms, end_ms=end_ms, fill=lens),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-284 저항-존 숏 수익 자 매칭 널 (알파 대 베타)"
    )
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--seeds", type=int, default=SEEDS, help="매칭 널 시드 수(기본 20)")
    parser.add_argument(
        "--fill",
        type=str,
        default="baseline",
        choices=("baseline", "pen_1bp", "pen_5bp"),
        help="체결 렌즈(관통만) — 실제·널에 동일하게 배선된다.",
    )
    parser.add_argument("--out-short", type=Path, default=DEFAULT_SHORT_CSV)
    parser.add_argument("--out-book", type=Path, default=DEFAULT_BOOK_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="새로 돌린 행을 기존 CSV에 병합한다((TF, 렌즈)·(스코프, 렌즈) 키).",
    )
    args = parser.parse_args(argv)

    out_short, out_book, out_md = Path(args.out_short), Path(args.out_book), Path(args.out_md)

    if args.from_csv:
        short_rows = short_from_csv(out_short)
        book_rows = book_from_csv(out_book) if out_book.exists() else []
        print(f"[wan284] CSV 로드 — 격리 {len(short_rows)}행 · 북 {len(book_rows)}행 (재실행 없음)")
    else:
        report = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            fill=harness.fill_preset(args.fill),
            seeds=int(args.seeds),
            jobs=args.jobs,
        )
        out_short.parent.mkdir(parents=True, exist_ok=True)
        if args.append and out_short.exists():
            short_rows = merge_short(short_from_csv(out_short), report.short_rows)
            book_rows = (
                merge_book(book_from_csv(out_book), report.book_rows)
                if out_book.exists()
                else list(report.book_rows)
            )
        else:
            short_rows = list(report.short_rows)
            book_rows = list(report.book_rows)
        short_to_frame(short_rows).to_csv(out_short, index=False)
        book_to_frame(book_rows).to_csv(out_book, index=False)
        print(f"[wan284] 격리 {len(short_rows)}행 → {out_short}")
        print(f"[wan284] 북 {len(book_rows)}행 → {out_book}")

    if not short_rows:
        print("[wan284] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    check, worst, compared = crosscheck_wan282(short_rows)
    print(f"[wan284] 검산 {check} (대조 {compared}행 · 최대차 {worst:.2e})")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(short_rows, book_rows, short_csv=out_short, book_csv=out_book),
        encoding="utf-8",
    )
    print(f"[wan284] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
