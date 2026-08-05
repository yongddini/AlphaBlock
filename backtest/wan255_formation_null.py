"""WAN-255 §2 — 형성(돌파) 진입 전략 + 방향-매칭 무작위 위치 널.

## 무엇을 검정하나 (WAN-254 §1 스핀아웃, 전제 성립 확정)

WAN-254 §1 census가 「형성 진입이 실제로 뭔가를 놓치고 있다」(강한 변위 OB일수록 **늦게**
되돌아온다 — 재탭 엔진은 그 긴 비행 동안 포지션이 없다)를 확정했다. 이제 **그 비행을 실제로
타면 방향-매칭 무작위를 이기나**를 검정한다.

세 팔(이슈 §2):

1. **재탭**(현행 채택 엔진, 비교 기준 D) — `--with-retap`으로 `harness.run_once`를 태워 같은
   (심볼, TF, 구간, 방향)의 지정가(B안) 성과를 병기한다(무거워서 옵트인 · 1분봉 필요).
2. **형성 전량** — 확정 OB마다 **전량 형성 진입**(되돌아옴 여부로 거르면 룩어헤드 · 설계
   함정 1). 진입 시점 사다리:
   * **A_close** 돌파 봉 종가(순수 모멘텀 · 1R 최대).
   * **B_open** 다음 봉 시가(깨끗한 모멘텀 · 봉내 룩어헤드 없음 — census와 같은 정의).
   손절 두 변형:
   * **ob** OB 무효화 경계(현행 파리티 · 실배포 모양 · 존 위치가 1R에 들어옴).
   * **atr** 진입가 ∓ `STOP_ATR_MULT`·ATR(**팔마다 1R을 존 위치와 무관하게 고정** = 진입
     타이밍만 격리 = 공정 비교의 주, WAN-152 `atr` 장벽 선례).
   익절 = 고정 1.5R. 비용 = **테이커**(진입·청산 둘 다 4bp + 슬리피지 5bp · 설계 함정 4).
   * **RSI 게이트**(사용자 제안, 옵트인 `--rsi-gate`) — 롱은 진입 직전 봉 RSI<30(과매도), 숏은
     RSI>70(과매수). ⚠️ WAN-81 재탭 게이트와 같은 정의라 WAN-114/123의 net-마이너스 판정이
     사전 확률이고, **형성(모멘텀)과 정면 충돌**한다: 불리시 돌파 확정봉은 강한 상승봉이라
     RSI가 <30일 수 없다 → 게이트를 켜면 표본이 **0에 수렴**한다(판정 불가 = 「형성에 과매도
     게이트는 정의상 성립 불가」가 곧 결론). 기본값은 항상 게이트 없음.
3. **무작위 위치 형성 널** — WAN-248 기계(`make_fake_result`)로 방향·존폭·빈도·무효화 규칙을
   매칭하고 **위치만 무작위**인 가짜 존을 만들어 **같은 형성 진입**을 태운다. 부트스트랩은
   실제 형성 거래의 (방향, UTC 4시간 시각대) 구성·개수를 맞춰 가짜 풀에서 재추출한다(WAN-70
   버킷). `p` = 무작위 반복 중 실제 이상을 낸 비율(단측).

## ⚠️ 왜 널은 `atr` 손절에서 퇴화하나 (이 모듈의 핵심 설계 관찰)

무작위 위치 널(WAN-248 계열)은 **가짜 존의 위치**가 손익에 들어갈 때만 실제와 갈린다. 형성
진입가는 **돌파 시점의 시장가**(종가/다음 시가)라 존 위치와 무관하다 — 그래서:

* **ob 손절**: 손절이 존 경계라 위치가 **1R·익절 목표**에 들어간다 → 실제(진짜 스윙 경계)와
  가짜(무작위 경계)가 갈린다 → 널이 **비퇴화**. 이 널이 묻는 것은 WAN-131/248의 위치 축과
  같다: 「진짜 OB 경계가 무작위 레벨보다 나은 손절 기준인가(= 1R 기하에 정보가 있나)」.
* **atr 손절**: 손절이 진입가 ∓ k·ATR라 **존 위치와 완전히 무관** → 실제 ≡ 가짜(진입가·손절·
  익절 전부 동일) → 널이 **퇴화**(p ≈ 1, 무의미). 그래서 `atr` 변형은 널 없이 **원값 + 재탭
  대비**만 낸다. atr 형성의 올바른 널은 **무작위 시각**(WAN-231 계열)이지 무작위 위치가
  아니다 — 이 이슈 범위 밖이라 명시한다.

즉 위치 널은 **형성 진입의 「타이밍 엣지」를 재지 않는다** — `ob` 손절의 **1R 기하 정보**를
잰다. 「형성 타이밍이 무작위 시각을 이기나」는 별도 이슈(무작위 시각 널)다.

## 네 관문 (이슈 완료기준 2 — 「엣지 찾았다」는 넷 다 통과 시에만)

① 무작위 위치 널 이김(p≤0.05, `ob` 손절) · ② OOS(warm) 생존 · ③ `pen_5bp`는 **형성 A/B가
시장가(테이커)라 성격이 다르다**(지정가 관통 벌점이 시장가엔 안 걸린다) — 형성 팔의 보수화는
이미 테이커 비용에 반영돼 있고, `pen_5bp`는 재탭(D)·지정가(C, 후속) 팔에만 의미가 있다(주석
전용) · ④ ETH·SOL·DOGE leave-one-out.

## 판정

* **(a) 형성이 무작위 위치를 이기나** — `ob` 손절 널 유의 셀.
* **(b) 재탭보다 나은가** — `--with-retap` 대비 total_return·거래당 net R.
* **(c) 안 되돌아온 OB에서 순양수인가** — never-retraced 형성 net R(사후 분해, **사후 조건부**
  라 엣지 아님 · WAN-254 §1(c)와 같은 경고).

## 성격 · 경고

측정 전용(기본값·토대 불변 · `short_enabled` 기본값 불변 · `ALPHABLOCK_LIVE_TRADING=false`
유지). 핀 없음(`OrderBlockParams()` = 오늘 엔진 · 분리 존 WAN-149). 못 박은 6년 창(WAN-182) ·
9종목 · 15m·1h·2h·4h · `baseline`(형성은 테이커) · warm/cold(WAN-166). 「엣지 없음」
(WAN-84/88/111/114/124/151/201/248)은 **다른 질문**(재탭 진입 규칙이 무작위와 구분되나)이라
이 표가 뒤집지 않는다.

## 범위 밖 (후속 이슈 — decision doc §remaining)

* 진입 사다리 **C**(돌파된 스윙 되테스트 지정가 · `limit_valid_bars` 격자, WAN-222 재사용).
* **§4 병행 북** — 통과 팔을 레버리지 북(WAN-213)에 얹어 재탭/형성/병행 대조.
* 전체 9종목 × 4TF × 200반복 격자(비용이 커서 이 PR은 축소 실행 · reproduce 명령은 아래).

## 재현

```
uv run python -m backtest.wan255_formation_null --part null --tf 1h --jobs 6
uv run python -m backtest.wan255_formation_null --part null --tf 2h,4h --jobs 6 --append
uv run python -m backtest.wan255_formation_null --part null --tf 15m --jobs 6 --append  # 무거움
uv run python -m backtest.wan255_formation_null --part summary   # CSV에서 요약만
```
"""

from __future__ import annotations

import argparse
import math
import random
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade, TradeFill
from backtest.run import parse_date_ms
from backtest.wan70_random_control_b import _bucket_key
from backtest.wan89_short_autopsy import ARMS_BY_NAME, Arm, _buy_hold
from backtest.wan248_zone_position_null import make_fake_result
from backtest.zone_limit_backtest import build_result_from_trades
from common.costs import Liquidity
from data.funding import cumulative_funding_cost
from data.models import FundingRate
from execution.sizing import position_size
from strategy.indicators import rsi
from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
)

REPORTS_DIR = Path("backtest/reports")
NULL_CSV = REPORTS_DIR / "wan255_formation_null.csv"
SUMMARY_MD = REPORTS_DIR / "wan255_formation_null_summary.md"

#: 채택 좌표(WAN-182/252) — 9종목 × 못 박은 6년 × 작업 TF. `harness` 기본값에서 읽는다.
NINE_SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS
WORK_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

#: 형성 진입 = 확정 다음 봉 시가/종가 시장가 · 고정 1.5R 익절(WAN-90 채택값).
TAKE_PROFIT_R = 1.5

#: ATR 손절 배수(진입가 ∓ k·ATR = 1R). WAN-152 `atr` 장벽 선례(1.5·ATR)와 같은 값.
STOP_ATR_MULT = 1.5

#: ATR 길이(봉). 존폭 필터(`zone_width_atr_length=14`)와 같은 값이라 자를 통일한다.
ATR_LENGTH = 14

#: RSI 게이트(사용자 제안, 옵트인) — 롱은 전봉 RSI<30(과매도), 숏은 전봉 RSI>70(과매수).
#: ⚠️ WAN-81 재탭 게이트와 같은 정의이고, 그 게이트는 WAN-114/123이 net-마이너스로 판정해
#: 기본값에서 제거했다. 형성(모멘텀)에 얹으면 과매도/과매수 조건이 돌파와 충돌해 표본이 크게
#: 준다 — 이 축은 그 실측용이다(기본값은 항상 게이트 없음).
RSI_LENGTH = 14
RSI_LONG_MAX = 30.0
RSI_SHORT_MIN = 70.0

#: 판정 게이트 — 심볼당 거래 20건(WAN-84/143/248 유효 기준).
MIN_TRADES_FOR_VERDICT = 20
ALPHA = 0.05

#: 부트스트랩 반복 수·시드. 위치 무작위화 시드(풀 생성)는 부트스트랩 시드와 분리한다.
BOOTSTRAP_ITERATIONS = 200
POOL_SEED = 255
BOOTSTRAP_SEED = 255

#: 존당 무작위 위치 복제 수(풀 크기 배수) — WAN-248과 같은 값.
DEFAULT_POOL_K = 8

#: 편중 진단에서 빼 보는 심볼들(완료기준 4).
LEAVE_OUT_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE")

#: 진입 시점 사다리 A/B(시장가) — C(지정가 되테스트)·D(재탭)는 후속/옵트인.
ENTRY_A_CLOSE = "A_close"
ENTRY_B_OPEN = "B_open"
ENTRY_POINTS: tuple[str, ...] = (ENTRY_A_CLOSE, ENTRY_B_OPEN)

#: 손절 변형. `ob`만 위치 널이 비퇴화(§docstring), `atr`는 원값만.
STOP_OB = "ob"
STOP_ATR = "atr"
STOP_VARIANTS: tuple[str, ...] = (STOP_OB, STOP_ATR)

#: 방향 축(§3) — 롱/숏/롱숏. 방향마다 자기 매칭 널.
DIR_LONG = "long"
DIR_SHORT = "short"
DIR_BOTH = "both"
DIRECTIONS: tuple[str, ...] = (DIR_LONG, DIR_SHORT, DIR_BOTH)

#: 방향 → 재탭(D) 비교용 팔(WAN-89 정의 재사용).
_RETAP_ARM_BY_DIR: dict[str, str] = {
    DIR_LONG: "long_only",
    DIR_SHORT: "short_only",
    DIR_BOTH: "both",
}

#: 헤드라인 구간(이슈: 심볼 × IS/oos_warm). `oos`(차가움)는 `--seg`로 스트레스 병기.
DEFAULT_SEGMENTS: tuple[str, ...] = (harness.SEGMENT_IS, harness.SEGMENT_OOS_WARM)
SEGMENT_LABELS: tuple[str, ...] = (
    harness.SEGMENT_IS,
    harness.SEGMENT_OOS_WARM,
    harness.SEGMENT_OOS,
)

#: 탐지 파라미터 = 채택 기본값(WAN-149: 분리). 재-베이스라인이 오면 이 표도 따라간다.
_ADOPTED_OB = OrderBlockParams()


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


# --------------------------------------------------------------------------- #
# HTF 배열 · ATR
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Arrays:
    """탐지기와 같은 전처리(정렬 + closed 필터)로 낸 HTF 배열."""

    times: list[int]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    atr: list[float]
    rsi: list[float]
    """확정봉 Wilder RSI(`strategy.indicators.rsi`). 워밍업 구간은 NaN(룩어헤드 없음)."""
    pos_by_time: dict[int, int]


def _atr_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], length: int
) -> list[float]:
    """단순 ATR(True Range의 `length` 이동평균). 존폭 필터(`zone_width_atr`)와 같은 정의.

    각 인덱스 `i`의 값은 `[i-length+1, i]` 봉의 TR 평균이라 **그 봉(i)까지만** 쓴다(룩어헤드
    없음 — 형성 진입은 확정봉 시점에 이 값을 안다). 워밍업(i<length-1) 구간은 사용 가능한
    TR로 평균한다(NaN 대신 부분 평균 — 형성 셋업이 워밍업에서 조용히 사라지지 않게).
    """
    n = len(closes)
    trs: list[float] = [0.0] * n
    for i in range(n):
        hi = highs[i]
        lo = lows[i]
        if i == 0:
            trs[i] = hi - lo
        else:
            prev_close = closes[i - 1]
            trs[i] = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
    atr: list[float] = [0.0] * n
    run = 0.0
    for i in range(n):
        run += trs[i]
        if i >= length:
            run -= trs[i - length]
            atr[i] = run / length
        else:
            atr[i] = run / (i + 1)
    return atr


def _arrays_from_frame(htf_df: pd.DataFrame) -> _Arrays:
    frame = htf_df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    frame = frame.sort_values("open_time").reset_index(drop=True)
    times = [int(v) for v in frame["open_time"].astype("int64").tolist()]
    opens = [float(v) for v in frame["open"].astype(float).tolist()]
    highs = [float(v) for v in frame["high"].astype(float).tolist()]
    lows = [float(v) for v in frame["low"].astype(float).tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    atr = _atr_series(highs, lows, closes, ATR_LENGTH)
    rsi_series = rsi(frame, length=RSI_LENGTH)
    rsi_vals = [float(v) for v in rsi_series.tolist()]
    pos_by_time = {t: i for i, t in enumerate(times)}
    return _Arrays(times, opens, highs, lows, closes, atr, rsi_vals, pos_by_time)


# --------------------------------------------------------------------------- #
# 형성 셋업 (한 OB) — 진입/손절/익절/청산 확정
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _FormationSetup:
    """한 OB의 형성 진입 셋업(비용 미반영 원가 정보)."""

    side: PositionSide
    entry_time: int
    entry_price: float
    stop_price: float
    exit_time: int
    exit_price: float
    reason: ExitReason
    gross_r: float
    """청산 사유 기준 gross R(비용 전): 손절 −1.0 · 익절 +1.5 · 미청산 부분 R."""
    retraced: bool
    """이 OB가 무효화 전에 존에 탭이 한 번이라도 있었나(사후 분해 전용, 진입엔 안 씀)."""


def _passes_guard(entry_price: float, stop_price: float, cfg: BacktestConfig) -> bool:
    """손절폭 가드(WAN-79) — 진입가·손절가만 보는 자본 무관 게이트.

    실제·가짜 셋업을 **같은 자로** 사전 필터해 부트스트랩 개수를 맞춘다(시퀀싱 중 자본
    의존 스킵을 없앤다). `risk_sizing`이 없으면 가드도 없다(고정 비율 경로).
    """
    sizing = cfg.risk_sizing
    if sizing is None:
        return True
    distance = abs(entry_price - stop_price)
    return distance > 0.0 and distance >= sizing.min_stop_distance_fraction * entry_price


def _walk_exit(
    *,
    is_long: bool,
    entry_price: float,
    stop: float,
    start_idx: int,
    arrays: _Arrays,
    tp_r: float,
) -> tuple[int, float, ExitReason, float]:
    """형성 진입(start_idx 봉부터)의 청산 (시각, 가격, 사유, gross R).

    손절/익절 중 **먼저 닿은 쪽**. 같은 봉에 둘 다 닿으면 **보수적으로 손절**. 둘 다 안
    닿으면 데이터 끝 종가에서 부분 R로 청산(`END_OF_DATA`).
    """
    highs = arrays.highs
    lows = arrays.lows
    closes = arrays.closes
    times = arrays.times
    n = len(times)
    risk = (entry_price - stop) if is_long else (stop - entry_price)
    tp_level = entry_price + tp_r * risk if is_long else entry_price - tp_r * risk
    for j in range(start_idx, n):
        hi = highs[j]
        lo = lows[j]
        stop_now = lo <= stop if is_long else hi >= stop
        tp_now = hi >= tp_level if is_long else lo <= tp_level
        if stop_now:
            return times[j], stop, ExitReason.STOP_LOSS, -1.0
        if tp_now:
            return times[j], tp_level, ExitReason.TAKE_PROFIT, tp_r
    terminal = closes[n - 1]
    gross = (terminal - entry_price) / risk if is_long else (entry_price - terminal) / risk
    return times[n - 1], terminal, ExitReason.END_OF_DATA, gross


def _rsi_gate_passes(rsi_vals: Sequence[float], gate_idx: int, is_long: bool) -> bool:
    """진입 캔들 직전 봉 RSI 게이트: 롱 RSI<30 · 숏 RSI>70. 워밍업 NaN·범위밖은 기각(보수)."""
    if gate_idx < 0 or gate_idx >= len(rsi_vals):
        return False
    value = rsi_vals[gate_idx]
    if math.isnan(value):
        return False
    return value < RSI_LONG_MAX if is_long else value > RSI_SHORT_MIN


def build_formation_setups(
    obs: Sequence[OrderBlock],
    arrays: _Arrays,
    *,
    entry_point: str,
    stop_variant: str,
    direction: str,
    cfg: BacktestConfig,
    tp_r: float = TAKE_PROFIT_R,
    rsi_gate: bool = False,
) -> list[_FormationSetup]:
    """확정 OB마다 **전량** 형성 셋업을 만든다(되돌아옴 여부로 안 거른다 · 설계 함정 1).

    진입 시점(A 종가/B 다음 시가)·손절(ob 무효화/atr)·고정 1.5R 익절을 확정하고, 손절폭
    가드를 통과한 셋업만 남긴다(자본 무관 필터 — 실제·가짜 개수 정합). 방향 필터(롱/숏/
    롱숏)를 여기서 건다.

    `rsi_gate`(옵트인, 사용자 제안)를 켜면 진입 캔들 **직전 봉**의 RSI로 거른다: 롱은
    RSI<`RSI_LONG_MAX`(과매도), 숏은 RSI>`RSI_SHORT_MIN`(과매수). ⚠️ WAN-81 재탭 게이트와
    같은 정의라 WAN-114/123의 net-마이너스 판정이 사전 확률이고, 형성(모멘텀)과 충돌해 표본이
    크게 준다. RSI가 **실제 가격 계열**이라 실제·가짜 존이 같은 확정시각에서 같은 게이트
    판정을 받아 매칭 널은 비퇴화를 유지한다(위치 축만 갈린다). 워밍업 NaN은 **보수적으로
    기각**(과매도/과매수를 확인 못 하면 진입 안 함).
    """
    n = len(arrays.times)
    setups: list[_FormationSetup] = []
    for ob in obs:
        is_long = ob.direction is OrderBlockDirection.BULLISH
        if direction == DIR_LONG and not is_long:
            continue
        if direction == DIR_SHORT and is_long:
            continue
        confirm_pos = arrays.pos_by_time.get(ob.confirmed_time)
        if confirm_pos is None or confirm_pos + 1 >= n:
            continue
        if entry_point == ENTRY_A_CLOSE:
            entry_time = arrays.times[confirm_pos]
            entry_price = arrays.closes[confirm_pos]
            gate_idx = confirm_pos - 1  # 진입 캔들(확정봉) 직전 봉.
        else:  # ENTRY_B_OPEN
            entry_time = arrays.times[confirm_pos + 1]
            entry_price = arrays.opens[confirm_pos + 1]
            gate_idx = confirm_pos  # 진입 캔들(다음 봉) 직전 봉 = 확정봉.
        if rsi_gate and not _rsi_gate_passes(arrays.rsi, gate_idx, is_long):
            continue
        if stop_variant == STOP_OB:
            stop = ob.bottom if is_long else ob.top
        else:  # STOP_ATR
            unit = STOP_ATR_MULT * arrays.atr[confirm_pos]
            stop = entry_price - unit if is_long else entry_price + unit
        risk = (entry_price - stop) if is_long else (stop - entry_price)
        if risk <= 0.0:
            continue
        if not _passes_guard(entry_price, stop, cfg):
            continue
        exit_time, exit_price, reason, gross_r = _walk_exit(
            is_long=is_long,
            entry_price=entry_price,
            stop=stop,
            start_idx=confirm_pos + 1,
            arrays=arrays,
            tp_r=tp_r,
        )
        setups.append(
            _FormationSetup(
                side=PositionSide.LONG if is_long else PositionSide.SHORT,
                entry_time=entry_time,
                entry_price=entry_price,
                stop_price=stop,
                exit_time=exit_time,
                exit_price=exit_price,
                reason=reason,
                gross_r=gross_r,
                retraced=len(ob.tapped_times) > 0,
            )
        )
    return setups


# --------------------------------------------------------------------------- #
# 단일 포지션 시퀀싱 → Trade (테이커 비용)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _SeqTrade:
    trade: Trade
    net_r: float
    retraced: bool


def _formation_trade(
    setup: _FormationSetup,
    equity: float,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
) -> _SeqTrade | None:
    """형성 셋업을 **테이커** 진입·청산 `Trade`로 변환(설계 함정 4 · 재탭은 메이커).

    `_to_trade`(지정가=메이커)와 유일하게 다른 점: 진입이 **테이커**(시장가·슬리피지)다.
    나머지(사이징·수수료·펀딩·손익)는 같은 공용 모델을 쓴다.
    """
    side = setup.side
    is_long = side is PositionSide.LONG
    costs = cfg.cost_model
    entry_fill = costs.entry_fill(setup.entry_price, is_long=is_long, liquidity=Liquidity.TAKER)
    if cfg.risk_sizing is not None:
        qty = position_size(
            equity=equity,
            entry_price=entry_fill,
            stop_price=setup.stop_price,
            params=cfg.risk_sizing,
        )
        if qty <= 0.0:
            return None
    else:
        qty = (equity * cfg.position_fraction) / entry_fill
    entry_notional = entry_fill * qty
    entry_fee = costs.fee(entry_notional, Liquidity.TAKER)
    exit_fill = costs.exit_fill(setup.exit_price, is_long=is_long, liquidity=Liquidity.TAKER)
    exit_fee = costs.fee(exit_fill * qty, Liquidity.TAKER)
    gross = side.sign * (exit_fill - entry_fill) * qty
    funding_cost = 0.0
    if cfg.funding_enabled and funding_rates:
        funding_cost = cumulative_funding_cost(
            funding_rates,
            position_notional=entry_notional,
            direction="long" if is_long else "short",
            start_ms=setup.entry_time,
            end_ms=setup.exit_time,
            include_predicted=cfg.funding_include_predicted,
        )
    realized = gross - entry_fee - exit_fee - funding_cost
    risk_amount = qty * abs(entry_fill - setup.stop_price)
    net_r = realized / risk_amount if risk_amount > 0 else 0.0
    trade = Trade(
        side=side,
        entry_time=setup.entry_time,
        entry_price=entry_fill,
        quantity=qty,
        entry_fee=entry_fee,
        exits=[
            TradeFill(
                time=setup.exit_time,
                price=exit_fill,
                quantity=qty,
                fee=exit_fee,
                reason=setup.reason,
            )
        ],
        funding_cost=funding_cost,
        realized_pnl=realized,
        return_pct=realized / entry_notional if entry_notional else 0.0,
    )
    return _SeqTrade(trade=trade, net_r=net_r, retraced=setup.retraced)


def sequence_formation(
    setups: Sequence[_FormationSetup],
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
) -> list[_SeqTrade]:
    """동시 1포지션 제약으로 형성 셋업을 시간순 배치(`_sequence_and_cost`와 같은 규칙).

    진입 시각 오름차순으로 훑으며 직전 청산 시각 이후 진입만 채택한다. 자본은 진행 중
    현금(A안 엔진과 동일).
    """
    ordered = sorted(setups, key=lambda s: (s.entry_time, s.exit_time))
    equity = cfg.initial_capital
    last_exit = -1
    out: list[_SeqTrade] = []
    for setup in ordered:
        if setup.entry_time < last_exit:
            continue
        seq = _formation_trade(setup, equity, cfg, funding_rates)
        if seq is None:
            continue
        equity += seq.trade.realized_pnl
        last_exit = setup.exit_time
        out.append(seq)
    return out


def _total_return(seq_trades: Sequence[_SeqTrade], cfg: BacktestConfig, timeframe: str) -> float:
    result = build_result_from_trades([s.trade for s in seq_trades], cfg, timeframe)
    return result.metrics.total_return


def _max_drawdown(seq_trades: Sequence[_SeqTrade], cfg: BacktestConfig, timeframe: str) -> float:
    result = build_result_from_trades([s.trade for s in seq_trades], cfg, timeframe)
    return result.metrics.max_drawdown


# --------------------------------------------------------------------------- #
# 방향-매칭 무작위 위치 널 (한 구간)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _NullResult:
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int
    pool_size: int
    fake_zones: int


def _matched_null(
    real_trades: Sequence[_SeqTrade],
    fake_setups: Sequence[_FormationSetup],
    *,
    real_total: float,
    cfg: BacktestConfig,
    timeframe: str,
    funding_rates: Sequence[FundingRate] | None,
    iterations: int,
    bootstrap_seed: int,
    fake_zones: int,
) -> _NullResult:
    """실제 형성 거래의 (방향, 4시각대) 구성을 맞춰 가짜 풀에서 재추출 → 총수익 분포 → p."""
    if not real_trades or not fake_setups:
        return _NullResult(None, None, None, None, 0, len(fake_setups), fake_zones)

    pool_by_bucket: dict[tuple[PositionSide, int], list[_FormationSetup]] = defaultdict(list)
    pool_by_side: dict[PositionSide, list[_FormationSetup]] = defaultdict(list)
    for setup in fake_setups:
        pool_by_bucket[_bucket_key(setup.side, setup.entry_time)].append(setup)
        pool_by_side[setup.side].append(setup)

    target_counts: dict[tuple[PositionSide, int], int] = defaultdict(int)
    for seq in real_trades:
        target_counts[_bucket_key(seq.trade.side, seq.trade.entry_time)] += 1

    rng = random.Random(bootstrap_seed)
    returns: list[float] = []
    for _ in range(iterations):
        sampled: list[_FormationSetup] = []
        used_by_side: dict[PositionSide, set[int]] = defaultdict(set)
        for (side, bucket), count in target_counts.items():
            bucket_pool = pool_by_bucket.get((side, bucket), [])
            k = min(count, len(bucket_pool))
            picks = rng.sample(bucket_pool, k) if k else []
            sampled.extend(picks)
            used_by_side[side].update(id(c) for c in picks)
            shortfall = count - k
            if shortfall > 0:
                remaining = [
                    c for c in pool_by_side.get(side, []) if id(c) not in used_by_side[side]
                ]
                fill_k = min(shortfall, len(remaining))
                fill_picks = rng.sample(remaining, fill_k) if fill_k else []
                sampled.extend(fill_picks)
                used_by_side[side].update(id(c) for c in fill_picks)
        seq_trades = sequence_formation(sampled, cfg, funding_rates)
        returns.append(_total_return(seq_trades, cfg, timeframe))

    returns.sort()
    m = len(returns)
    p_value = sum(1 for r in returns if r >= real_total) / m if m else None
    mean_return = sum(returns) / m if m else None
    ci_low = returns[int(0.025 * (m - 1))] if m else None
    ci_high = returns[int(0.975 * (m - 1))] if m else None
    return _NullResult(
        random_mean_return=mean_return,
        random_ci_low=ci_low,
        random_ci_high=ci_high,
        random_p_value=p_value,
        iterations=m,
        pool_size=len(fake_setups),
        fake_zones=fake_zones,
    )


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class FormationRow(BaseModel):
    """한 (심볼, TF, 구간, 방향, 진입점, 손절변형)의 형성 전략 + 널 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    direction: str
    entry_point: str
    stop_variant: str
    rsi_gate: bool = False
    """RSI 게이트(롱<30·숏>70) 적용 여부(사용자 제안 옵트인 축, 기본 False). 옛 CSV(열 없음)는
    False로 로드된다."""
    real_total_return: float
    real_num_trades: int
    real_mean_net_r: float | None
    real_max_drawdown: float
    returned_n: int
    returned_mean_net_r: float | None
    never_n: int
    never_mean_net_r: float | None
    """(c) 안 되돌아온 OB 형성 진입의 거래당 순 R(사후 조건부 · 엣지 아님)."""
    pool_size: int
    fake_zones: int
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    """`ob` 손절에서만 비퇴화(§docstring). `atr`는 None(널 퇴화 · 원값만)."""
    iterations: int
    retap_total_return: float | None
    """(b) 재탭(D) 비교 — `--with-retap`일 때만. 없으면 None."""
    retap_num_trades: int | None
    buy_hold: float

    @field_validator("*", mode="before")
    @classmethod
    def _nan_to_none(cls, value: object) -> object:
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


# --------------------------------------------------------------------------- #
# 셀 실행 (심볼 × TF)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    segments: tuple[str, ...]
    directions: tuple[str, ...]
    entry_points: tuple[str, ...]
    stop_variants: tuple[str, ...]
    rsi_gates: tuple[bool, ...]
    pool_k: int
    iterations: int
    with_retap: bool


def _segment_window(
    market: harness.MarketData, segment: str
) -> tuple[harness.MarketData, int | None, pd.DataFrame]:
    """(평가용 창, 평가 경계 ms, buy_hold 계산용 HTF 프레임). WAN-248과 같은 규약."""
    if segment == harness.SEGMENT_OOS_WARM:
        eval_from = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
        bh_frame = market.htf_df
        if eval_from is not None:
            times = bh_frame["open_time"].astype("int64")
            bh_frame = bh_frame[times >= eval_from].reset_index(drop=True)
        return market, eval_from, bh_frame
    spec = {
        harness.SEGMENT_IS: harness.Segment(
            name=harness.SEGMENT_IS, window=0, start_fraction=0.0, end_fraction=harness.IS_FRACTION
        ),
        harness.SEGMENT_OOS: harness.Segment(
            name=harness.SEGMENT_OOS, window=0, start_fraction=harness.IS_FRACTION, end_fraction=1.0
        ),
    }[segment]
    window = harness.slice_market(market, spec)
    return window, None, window.htf_df


def _filter_eval_setups(
    setups: Sequence[_FormationSetup], eval_from_ms: int | None
) -> list[_FormationSetup]:
    """따뜻한 연속 OOS: 진입 시각이 평가 경계 이후인 셋업만(run_once eval_from_ms와 같은 뜻)."""
    if eval_from_ms is None:
        return list(setups)
    return [s for s in setups if s.entry_time >= eval_from_ms]


def _retap_return(
    market: harness.MarketData,
    direction: str,
    *,
    eval_from_ms: int | None,
) -> tuple[float, int] | None:
    """재탭(D) 비교 — 채택 지정가 엔진(`run_once`)의 (총수익, 거래수). 1분봉 필요(무거움)."""
    if market.df_1m.empty:
        return None
    arm: Arm = ARMS_BY_NAME[_RETAP_ARM_BY_DIR[direction]]
    outcome = harness.run_once(
        market,
        params=arm.params(),
        cfg=arm.config(market.timeframe),
        eval_from_ms=eval_from_ms,
    )
    metrics = outcome.result.metrics
    return metrics.total_return, len(outcome.result.trades)


def run_cell(task: _Task, *, log: bool = True) -> list[FormationRow]:
    """한 (심볼, TF)의 구간 × 방향 × 진입점 × 손절변형 형성 전략 + 널."""
    market = harness.load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=task.with_retap,
        funding=True,
    )
    if market.empty:
        return []

    rows: list[FormationRow] = []
    for segment in task.segments:
        window, eval_from, bh_frame = _segment_window(market, segment)
        if window.empty or bh_frame.empty:
            continue
        arrays = _arrays_from_frame(window.htf_df)
        if len(arrays.times) < 2:
            continue
        real_ob = harness.detect_order_blocks(window, _ADOPTED_OB)
        fake_ob = make_fake_result(
            real_ob, window.htf_df, _ADOPTED_OB, rng=random.Random(POOL_SEED), pool_k=task.pool_k
        )
        buy_hold = _buy_hold(bh_frame)
        cfg = harness.build_config(task.timeframe, funding_enabled=True)

        for direction in task.directions:
            retap: tuple[float, int] | None = None
            if task.with_retap:
                # 재탭은 형성 팔과 **같은 구간 창**에서 돈다: is/oos는 슬라이스,
                # oos_warm은 전 창 + eval_from(WAN-166) — `window`가 그 규약을 담는다.
                retap = _retap_return(window, direction, eval_from_ms=eval_from)
            for entry_point in task.entry_points:
                for stop_variant in task.stop_variants:
                    for rsi_gate in task.rsi_gates:
                        row = _one_row(
                            symbol=task.symbol,
                            timeframe=task.timeframe,
                            segment=segment,
                            direction=direction,
                            entry_point=entry_point,
                            stop_variant=stop_variant,
                            rsi_gate=rsi_gate,
                            arrays=arrays,
                            real_ob=real_ob,
                            fake_ob=fake_ob,
                            cfg=cfg,
                            funding_rates=window.funding_rates,
                            eval_from_ms=eval_from,
                            iterations=task.iterations,
                            retap=retap,
                            buy_hold=buy_hold,
                        )
                        rows.append(row)
                        if log:
                            gate = "rsi" if rsi_gate else "nogate"
                            print(
                                f"[wan255] {task.symbol} {task.timeframe} {segment} {direction} "
                                f"{entry_point}/{stop_variant}/{gate}: "
                                f"real={row.real_total_return:.4f} n={row.real_num_trades} "
                                f"netR={row.real_mean_net_r} p={row.random_p_value}",
                                flush=True,
                            )
    return rows


def _one_row(
    *,
    symbol: str,
    timeframe: str,
    segment: str,
    direction: str,
    entry_point: str,
    stop_variant: str,
    rsi_gate: bool,
    arrays: _Arrays,
    real_ob: OrderBlockResult,
    fake_ob: OrderBlockResult,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    eval_from_ms: int | None,
    iterations: int,
    retap: tuple[float, int] | None,
    buy_hold: float,
) -> FormationRow:
    real_setups = _filter_eval_setups(
        build_formation_setups(
            real_ob.order_blocks,
            arrays,
            entry_point=entry_point,
            stop_variant=stop_variant,
            direction=direction,
            cfg=cfg,
            rsi_gate=rsi_gate,
        ),
        eval_from_ms,
    )
    real_trades = sequence_formation(real_setups, cfg, funding_rates)
    real_total = _total_return(real_trades, cfg, timeframe)
    real_mdd = _max_drawdown(real_trades, cfg, timeframe)
    net_rs = [s.net_r for s in real_trades]
    returned = [s.net_r for s in real_trades if s.retraced]
    never = [s.net_r for s in real_trades if not s.retraced]

    null: _NullResult
    if stop_variant == STOP_OB:
        fake_setups = _filter_eval_setups(
            build_formation_setups(
                fake_ob.order_blocks,
                arrays,
                entry_point=entry_point,
                stop_variant=stop_variant,
                direction=direction,
                cfg=cfg,
                rsi_gate=rsi_gate,
            ),
            eval_from_ms,
        )
        null = _matched_null(
            real_trades,
            fake_setups,
            real_total=real_total,
            cfg=cfg,
            timeframe=timeframe,
            funding_rates=funding_rates,
            iterations=iterations,
            bootstrap_seed=BOOTSTRAP_SEED,
            fake_zones=len(fake_ob.order_blocks),
        )
    else:
        # atr 손절: 위치 널 퇴화(실제 ≡ 가짜) → 원값만.
        null = _NullResult(None, None, None, None, 0, 0, len(fake_ob.order_blocks))

    return FormationRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        direction=direction,
        entry_point=entry_point,
        stop_variant=stop_variant,
        rsi_gate=rsi_gate,
        real_total_return=real_total,
        real_num_trades=len(real_trades),
        real_mean_net_r=_mean(net_rs),
        real_max_drawdown=real_mdd,
        returned_n=len(returned),
        returned_mean_net_r=_mean(returned),
        never_n=len(never),
        never_mean_net_r=_mean(never),
        pool_size=null.pool_size,
        fake_zones=null.fake_zones,
        random_mean_return=null.random_mean_return,
        random_ci_low=null.random_ci_low,
        random_ci_high=null.random_ci_high,
        random_p_value=null.random_p_value,
        iterations=null.iterations,
        retap_total_return=retap[0] if retap is not None else None,
        retap_num_trades=retap[1] if retap is not None else None,
        buy_hold=buy_hold,
    )


def _run_task_logged(task: _Task) -> list[FormationRow]:
    return run_cell(task, log=True)


def run_null(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = WORK_TIMEFRAMES,
    segments: Sequence[str] = DEFAULT_SEGMENTS,
    directions: Sequence[str] = DIRECTIONS,
    entry_points: Sequence[str] = ENTRY_POINTS,
    stop_variants: Sequence[str] = STOP_VARIANTS,
    rsi_gates: Sequence[bool] = (False,),
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    pool_k: int = DEFAULT_POOL_K,
    iterations: int = BOOTSTRAP_ITERATIONS,
    with_retap: bool = False,
    jobs: int = 1,
    log: bool = True,
) -> list[FormationRow]:
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            segments=tuple(segments),
            directions=tuple(directions),
            entry_points=tuple(entry_points),
            stop_variants=tuple(stop_variants),
            rsi_gates=tuple(rsi_gates),
            pool_k=pool_k,
            iterations=iterations,
            with_retap=with_retap,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_cell(task, log=log)]
    rows: list[FormationRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


# --------------------------------------------------------------------------- #
# 통계·집계·판정
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[FormationRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[FormationRow]:
    frame = pd.read_csv(path)
    return [FormationRow.model_validate(record) for record in frame.to_dict(orient="records")]


def is_significant(row: FormationRow, alpha: float = ALPHA) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제>무작위평균(WAN-70/84/248과 같은 자)."""
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def eligible_rows(rows: Sequence[FormationRow]) -> list[FormationRow]:
    return [
        r
        for r in rows
        if r.random_p_value is not None and r.real_num_trades >= MIN_TRADES_FOR_VERDICT
    ]


def significance_counts(rows: Sequence[FormationRow]) -> tuple[int, int]:
    eligible = eligible_rows(rows)
    return sum(1 for r in eligible if is_significant(r)), len(eligible)


def verdict_null(rows: Sequence[FormationRow], *, rsi_gate: bool = False) -> str:
    """(a) 형성이 무작위 위치를 이기나 — `ob` 손절 널 기준(§docstring)."""
    ob_rows = [r for r in rows if r.stop_variant == STOP_OB and r.rsi_gate == rsi_gate]
    sig, total = significance_counts(ob_rows)
    if total == 0:
        return f"**⚠️ 판정 불가** — 거래 {MIN_TRADES_FOR_VERDICT}건 이상 유효 셀 없음(표본 부족)."
    parts: list[str] = []
    for tf in WORK_TIMEFRAMES:
        s, t = significance_counts([r for r in ob_rows if r.timeframe == tf])
        if t:
            parts.append(f"{tf} {s}/{t}")
    tf_note = " · ".join(parts)
    if sig == 0:
        head = "**(b) 형성 진입(ob 손절)은 무작위 위치와 구분되지 않는다**"
    elif sig == total:
        head = "**(a) 형성 진입(ob 손절)이 무작위 위치를 유의하게 이긴다**"
    else:
        head = "**(c) 일부 셀에만 유의성 — TF·구간·방향에 갈린다**"
    return f"유효 셀 {total}개 중 유의 {sig}개({tf_note}) → {head}"


def _round(v: float | None, scale: float = 1.0, digits: int = 2) -> object:
    return round(v * scale, digits) if v is not None else "—"


def summary_table(
    rows: Sequence[FormationRow], *, entry_point: str, stop_variant: str, rsi_gate: bool = False
) -> str:
    """(TF × 구간 × 방향) 심볼평균 요약 — 한 진입점·손절변형·게이트."""
    header = (
        "| TF | 구간 | 방향 | 실제수익 | +심볼 | n | net R | never net R | 무작위평균 | 유의 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: | --: |"
    )
    scoped = [
        r
        for r in rows
        if r.entry_point == entry_point
        and r.stop_variant == stop_variant
        and r.rsi_gate == rsi_gate
    ]
    tf_order = {tf: i for i, tf in enumerate(WORK_TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    dir_order = {d: i for i, d in enumerate(DIRECTIONS)}
    groups: dict[tuple[str, str, str], list[FormationRow]] = defaultdict(list)
    for r in scoped:
        groups[(r.timeframe, r.segment, r.direction)].append(r)
    body: list[str] = []
    for (tf, seg, direction), cells in sorted(
        groups.items(),
        key=lambda kv: (
            tf_order.get(kv[0][0], 9),
            seg_order.get(kv[0][1], 9),
            dir_order.get(kv[0][2], 9),
        ),
    ):
        real_vals = [c.real_total_return for c in cells]
        eligible = eligible_rows(cells)
        sig = sum(1 for c in eligible if is_significant(c))
        net_r = _mean([c.real_mean_net_r for c in cells if c.real_mean_net_r is not None])
        never = _mean([c.never_mean_net_r for c in cells if c.never_mean_net_r is not None])
        rand = _mean([c.random_mean_return for c in cells if c.random_mean_return is not None])
        pos = sum(1 for v in real_vals if v > 0)
        mean_n = round(_mean([float(c.real_num_trades) for c in cells]) or 0, 1)
        body.append(
            f"| {tf} | {seg} | {direction} | {_round(_mean(real_vals), 100)}% | "
            f"{pos} | {mean_n} | "
            f"{_round(net_r, 1, 3)} | {_round(never, 1, 3)} | {_round(rand, 100)}% | "
            f"{sig}/{len(eligible)} |"
        )
    return header + "\n" + "\n".join(body)


def retap_compare_table(rows: Sequence[FormationRow]) -> str:
    """(b) 재탭 대비 — `--with-retap`으로 채운 셀만."""
    scoped = [r for r in rows if r.retap_total_return is not None]
    if not scoped:
        return "_(재탭 비교 없음 — `--with-retap`으로 채운다.)_"
    header = (
        "| 심볼 | TF | 구간 | 방향 | 진입/손절 | 형성수익 | 재탭수익 | 형성n | 재탭n |\n"
        "| -- | -- | -- | -- | -- | --: | --: | --: | --: |"
    )
    body: list[str] = []
    for r in scoped:
        body.append(
            f"| {_short(r.symbol)} | {r.timeframe} | {r.segment} | {r.direction} | "
            f"{r.entry_point}/{r.stop_variant} | {r.real_total_return * 100:+.2f}% | "
            f"{(r.retap_total_return or 0.0) * 100:+.2f}% | {r.real_num_trades} | "
            f"{r.retap_num_trades} |"
        )
    return header + "\n" + "\n".join(body)


def leave_one_out_lines(rows: Sequence[FormationRow], *, rsi_gate: bool = False) -> list[str]:
    """완료기준 4 — 심볼 하나 빼면 심볼평균 부호가 유지되는가(`ob` 손절)."""
    lines: list[str] = []
    scoped = [r for r in rows if r.stop_variant == STOP_OB and r.rsi_gate == rsi_gate]
    for tf in WORK_TIMEFRAMES:
        for seg in DEFAULT_SEGMENTS:
            for entry_point in ENTRY_POINTS:
                for direction in DIRECTIONS:
                    cells = [
                        r
                        for r in scoped
                        if r.timeframe == tf
                        and r.segment == seg
                        and r.entry_point == entry_point
                        and r.direction == direction
                    ]
                    if len(cells) < 2:
                        continue
                    mean_all = _mean([c.real_total_return for c in cells])
                    if mean_all is None:
                        continue
                    flips: list[str] = []
                    for sym in LEAVE_OUT_SYMBOLS:
                        kept = [c for c in cells if _short(c.symbol) != sym]
                        if len(kept) == len(cells) or not kept:
                            continue
                        mean_ex = _mean([c.real_total_return for c in kept])
                        if mean_ex is None:
                            continue
                        mark = "부호 유지" if (mean_all > 0) == (mean_ex > 0) else "부호 뒤집힘"
                        flips.append(f"−{sym} {mean_ex * 100:+.2f}%({mark})")
                    if flips:
                        label = f"{tf} {seg} {direction} {entry_point}"
                        head = f"- **{label}**: {mean_all * 100:+.2f}% → "
                        lines.append(head + " · ".join(flips))
    return lines or ["- (해당 행 없음)"]


def _rsi_gate_section(rows: Sequence[FormationRow]) -> list[str]:
    """RSI 게이트(롱<30·숏>70) 축이 실행된 경우에만 대비 섹션을 낸다(사용자 제안)."""
    if not any(r.rsi_gate for r in rows):
        return []
    return [
        "## §5 RSI 게이트 (롱<30·숏>70, 사용자 제안 · 옵트인)",
        "",
        "⚠️ WAN-81 재탭 게이트와 같은 정의이고 WAN-114/123이 net-마이너스로 판정해 기본값에서 "
        "제거했다. 형성(모멘텀)에 얹으면 과매도/과매수가 돌파와 충돌해 표본이 크게 준다 — "
        "심볼당 20건 미달로 판정 불가 셀이 늘면 그것이 결론(필터가 형성을 못 구한다)이다. "
        "게이트를 켜도 실제·가짜는 같은 확정시각에서 같은 게이트를 통과하므로 널은 비퇴화다.",
        "",
        "### 판정 (a) — 게이트 켜짐 (`ob` 손절)",
        "",
        verdict_null(rows, rsi_gate=True),
        "",
        "### B_open / ob 손절 · RSI 게이트 켜짐",
        "",
        summary_table(rows, entry_point=ENTRY_B_OPEN, stop_variant=STOP_OB, rsi_gate=True),
        "",
        "### A_close / ob 손절 · RSI 게이트 켜짐",
        "",
        summary_table(rows, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_OB, rsi_gate=True),
        "",
        "### 편중 — leave-one-out (게이트 켜짐)",
        "",
        *leave_one_out_lines(rows, rsi_gate=True),
        "",
    ]


def build_summary_markdown(rows: Sequence[FormationRow]) -> str:
    lines: list[str] = [
        "# WAN-255 §2 — 형성(돌파) 진입 전략 + 방향-매칭 무작위 위치 널",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · 9종목 × 작업 TF(15m·1h·2h·4h) × 구간"
        "(IS·oos_warm) × 방향(롱·숏·롱숏) × 진입점(A 종가·B 다음시가) × 손절(ob·atr). "
        "형성 = 확정 OB 전량 진입(테이커) · 고정 1.5R · 손절 = OB 무효화/ATR. 대조군 = "
        "**방향·존폭·빈도·무효화 매칭, 위치만 무작위**(WAN-248 기계).",
        "",
        "## ⚠️ 널은 `ob` 손절에서만 비퇴화",
        "",
        "형성 진입가는 돌파 시점 시장가라 존 위치와 무관하다. `atr` 손절도 위치와 무관해 "
        "실제 ≡ 가짜(널 퇴화) → `atr`은 원값·재탭 대비만, 널은 `ob`(존 경계가 1R에 들어옴)"
        "에서만 낸다. 즉 이 널은 **형성 「타이밍 엣지」가 아니라 「1R 기하 위치 정보」**를 "
        "잰다(무작위 시각 널은 별도 이슈).",
        "",
        "## §1 판정 (a) — 형성이 무작위 위치를 이기나 (`ob` 손절)",
        "",
        verdict_null(rows),
        "",
        "### A_close / ob 손절",
        "",
        summary_table(rows, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_OB),
        "",
        "### B_open / ob 손절",
        "",
        summary_table(rows, entry_point=ENTRY_B_OPEN, stop_variant=STOP_OB),
        "",
        "### B_open / atr 손절 (원값 · 널 퇴화)",
        "",
        summary_table(rows, entry_point=ENTRY_B_OPEN, stop_variant=STOP_ATR),
        "",
        "## §2 판정 (b) — 재탭 대비",
        "",
        retap_compare_table(rows),
        "",
        "## §3 판정 (c) — 안 되돌아온 OB 형성 net R (사후 조건부 · 엣지 아님)",
        "",
        "위 요약표의 `never net R` 열 참조. WAN-254 §1(c)와 같은 경고: 안 되돌아온 롱 = "
        "상승 = 롱 이익이라 순환적, 「놓친 돈의 실재」만 뜻한다.",
        "",
        "## §4 편중 — leave-one-out (ETH·SOL·DOGE, `ob` 손절)",
        "",
        *leave_one_out_lines(rows),
        "",
        *_rsi_gate_section(rows),
        "## 결론 · 인용 금지",
        "",
        "- **측정 전용** — 기본값·토대 불변, `short_enabled` 기본값 불변, 실거래 보류 유지.",
        "- ⚠️ **「엣지 찾았다」는 네 관문(널·OOS·pen 성격·leave-one-out) 다 통과 시에만.** "
        "형성 A/B는 테이커라 `pen_5bp`(지정가 관통 벌점)가 성격상 안 걸린다 — 보수화는 이미 "
        "테이커 비용에 있고, `pen_5bp`는 재탭(D)·지정가(C, 후속) 팔 주석 전용.",
        "- ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248)의 반박이 아니다** — 저건 "
        "재탭 진입 규칙이 무작위 시각과 구분되나, 이건 형성의 1R 기하 위치 정보를 묻는다.",
        "- 범위 밖(후속): 진입점 C(지정가 되테스트) · §4 병행 북(WAN-213) · 전체 격자.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

PARTS: tuple[str, ...] = ("null", "summary", "all")


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _run_summary() -> None:
    if not NULL_CSV.exists():
        print(f"[wan255] {NULL_CSV} 없음 — 먼저 --part null을 돌리세요.")
        return
    rows = rows_from_csv(NULL_CSV)
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text(build_summary_markdown(rows), encoding="utf-8")
    print(f"[wan255] summary → {SUMMARY_MD}")


def _split(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(x.strip() for x in value.split(",") if x.strip())


def _parse_rsi_gates(value: str | None) -> tuple[bool, ...]:
    """`off|on|off,on` → (False,)·(True,)·(False, True). 미지정=off."""
    if not value:
        return (False,)
    out: list[bool] = []
    for token in value.split(","):
        token = token.strip().lower()
        if token in ("off", "false", "0", "nogate"):
            out.append(False)
        elif token in ("on", "true", "1", "rsi"):
            out.append(True)
    return tuple(out) if out else (False,)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-255 형성 진입 전략 + 무작위 위치 널")
    parser.add_argument("--part", type=str, default="all", choices=PARTS)
    parser.add_argument("--tf", type=str, default=None, help="TF 목록(콤마). 미지정=15m,1h,2h,4h")
    parser.add_argument("--symbols", type=str, default=None, help="심볼 목록(콤마). 미지정=9종목")
    parser.add_argument("--seg", type=str, default=None, help="구간(콤마). 미지정=is,oos_warm")
    parser.add_argument(
        "--direction", type=str, default=None, help="방향(콤마) 미지정=long,short,both"
    )
    parser.add_argument(
        "--entry", type=str, default=None, help="진입점(콤마) 미지정=A_close,B_open"
    )
    parser.add_argument("--stop", type=str, default=None, help="손절(콤마). 미지정=ob,atr")
    parser.add_argument(
        "--rsi-gate",
        type=str,
        default="off",
        help="RSI 게이트(롱<30·숏>70) 축: off|on|off,on. 미지정=off(기본=게이트 없음)",
    )
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--pool-k", type=int, default=DEFAULT_POOL_K)
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--with-retap", action="store_true", help="재탭(D) 비교 병기(1분봉·무거움)")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--append", action="store_true", help="CSV에 덧붙인다(TF·심볼 분할 실행).")
    args = parser.parse_args(argv)

    part = str(args.part)
    if part in ("null", "all"):
        rows = run_null(
            symbols=_split(args.symbols, NINE_SYMBOLS),
            timeframes=_split(args.tf, WORK_TIMEFRAMES),
            segments=_split(args.seg, DEFAULT_SEGMENTS),
            directions=_split(args.direction, DIRECTIONS),
            entry_points=_split(args.entry, ENTRY_POINTS),
            stop_variants=_split(args.stop, STOP_VARIANTS),
            rsi_gates=_parse_rsi_gates(args.rsi_gate),
            start=args.start,
            end=args.end,
            pool_k=int(args.pool_k),
            iterations=int(args.iterations),
            with_retap=bool(args.with_retap),
            jobs=int(args.jobs),
        )
        frame = rows_to_frame(rows)
        if args.append and NULL_CSV.exists():
            frame = pd.concat([pd.read_csv(NULL_CSV), frame], ignore_index=True)
        _write(frame, NULL_CSV)
        print(f"[wan255] null {len(frame)}행 → {NULL_CSV}")

    if part in ("summary", "all"):
        _run_summary()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
