"""WAN-258 — 무효화 돌파(브레이커) 진입 전략 + 방향-매칭 무작위 위치 널.

## 무엇을 검정하나 (사용자 아이디어 2026-08-06)

현행 채택 엔진은 오더블록에 가격이 **되돌아오는 재탭**에서 진입한다(존을 지지/저항으로 믿고
반대로 매매 — 불리시 OB에서 롱). 이 모듈은 **정반대**를 잰다: **오더블록이 무효화되는(깨지는)
순간, 깨는 방향으로 따라 들어간다.**

* 지지(불리시 OB)가 **뚫려 내려가면 → 숏**(돌파 방향).
* 저항(베어리시 OB)이 **뚫려 올라가면 → 롱**(돌파 방향).

즉 "존이 버틴다"가 아니라 **"존이 깨지면 그 방향으로 계속 간다"**에 베팅한다(실패한 오더블록이
오히려 모멘텀의 방아쇠 = 「브레이커 블록」 가설). WAN-255(형성 진입)가 이미 한 가지 모멘텀-
연속 진입을 쟀고 「재탭보다도 무작위보다도 낫지 않다」로 닫혔다 — 이건 **다른 이벤트**(OB를
*만드는* 돌파가 아니라 OB를 *깨는* 돌파)라 자동으로 안 덮이지만 **기대는 낮춰 읽는다.**

## 진입 트리거 = OB 무효화(`break_time`) · 방향 = 돌파 방향

진입 시점은 탐지기가 낸 무효화 봉(`OrderBlock.break_time`, 기본 `zone_invalidation="wick"`)
이다. ⚠️ **룩어헤드 금지** — 무효화 봉이 닫힌 **뒤**(A: 그 봉 종가) 또는 다음 봉 시가(B)에
진입한다. 트레이드 방향은 OB 방향의 **반대**다(불리시 OB 하향 돌파 → 숏 · 베어리시 OB 상향
돌파 → 롱).

세 팔(이슈 §3, WAN-255와 동형):

* **A_close** 돌파(무효화) 봉 종가(순수 모멘텀).
* **B_open** 다음 봉 시가(봉내 룩어헤드 없음).
* 손절 두 변형:
  * **ob** 존 **반대편 경계 재탈환**(숏=`top` · 롱=`bottom`) → **1R = 존 높이**(사용자 확정
    2026-08-06). 가격이 존을 통째로 되돌려 탈환하면 브레이커가 실패한 것 = 손절. 실배포에
    가까운 자이되 결과가 존 **폭**에 묶인다(WAN-131/152/159 가격 기하).
  * **atr** 진입가 ∓ `STOP_ATR_MULT`·ATR = 존폭과 **무관한 고정 1R** = 신호 격리용 주.
* 익절 = 고정 1.5R(WAN-90). 비용 = **테이커**(진입·청산 4bp + 슬리피지 5bp — 브레이커는
  돌파 시점 시장가라 지정가/메이커가 아니다).

## ⚠️ 왜 위치 널은 `ob` 손절에서만 내나 (WAN-255 관찰 계승)

무작위 위치 널(WAN-248 기계)은 **가짜 존의 위치**가 손익에 들어갈 때만 실제와 갈린다.

* **ob 손절**: 손절이 존 경계라 위치가 **1R·익절 목표**에 들어간다 → 실제(진짜 스윙 경계)와
  가짜(무작위 경계)가 갈린다 → 널이 **비퇴화**. WAN-131/248의 위치 축과 같은 질문이다:
  「진짜 OB 경계가 무작위 레벨보다 나은 손절 기준인가(= 1R 기하에 정보가 있나)」.
* **atr 손절**: 손절이 진입가 ∓ k·ATR라 존 폭·경계와 무관하다. 브레이커는 형성과 달리
  진입가(무효화 봉가)가 가짜 존 위치에 따라 **조금 달라지지만**(가짜는 다른 시각에 깨진다),
  이슈가 정한 대로 위치 널은 **`ob` 손절에만** 붙이고 `atr`은 **원값 + 재탭 대비**만 낸다.
  브레이커의 「타이밍(모멘텀) 엣지」는 **무작위 시각 널**(WAN-231 계열)이 더 날카로운 자이며,
  그 축은 이 이슈 범위 밖(후속)이다.

## ➕ 활주로(다음 반대 존까지 거리) 필터 축 (옵트인, 사용자 지적 2026-08-06)

⚠️ 뚫렸는데 진행 방향 바로 앞에 또 반대 존이 있으면 그 벽에 부딪혀 튕긴다. **활주로** =
진입가에서 **진행 방향의 가장 가까운 반대 존**까지 거리 ÷ ATR(숏=아래 첫 불리시 OB의 `top` ·
롱=위 첫 베어리시 OB의 `bottom`, 진입 시각까지 클리핑·뚫린 존 제외 = WAN-137 규칙 ①②③).

* **기본 = 자기-TF**(WAN-137 `resistance_self` — 「전 TF 최근접」은 ~67%가 1분봉으로
  퇴화해 못 쓴다). 상위 TF(②)·익절 대체(b)·전 TF는 **후속**(§remaining).
* `--runway` 문턱을 주면 **필터 켠 팔**(활주로 ≥ 문턱만 진입)을 **필터 끈 팔과 병기**한다 —
  「활주로가 값을 더하나 vs 표본이 무너지나」를 같이 본다(20건 게이트). 필터 팔은 실제만
  낸다(널은 base 팔에만 · 필터 팔 위치 널은 후속).
* ⚠️ **활주로 ≈ 존 밀도/변동성**일 수 있다 — 활주로(ATR)와 진입 봉 ATR%의 상관을 **한 줄**로
  같이 낸다(WAN-131/251 규율). 상관이 크면 새 정보가 아니라 변동성의 재탕이다.

## 네 관문 (완료기준 2 — 「엣지 찾았다」는 넷 다 통과 시에만)

① 방향-매칭 무작위 위치 널 이김(p≤0.05, `ob` 손절) · ② OOS(warm) 생존 · ③ 테이커 비용/
렌즈 성격 주석(브레이커는 테이커라 `pen_5bp` 관통 벌점이 성격상 안 걸린다 — 보수화는 이미
테이커 비용에 있고, `pen_5bp`는 재탭(D) 팔 주석 전용) · ④ ETH·SOL·DOGE leave-one-out.

## 성격 · 경고

측정 전용(기본값·토대 불변 · `short_enabled` 기본값 불변 · `ALPHABLOCK_LIVE_TRADING=false`
유지). 핀 없음(`OrderBlockParams()` = 오늘 엔진 · 분리 존 WAN-149). 못 박은 6년 창(WAN-182) ·
9종목 · 15m·1h·2h·4h(WAN-252) · `baseline`(브레이커는 테이커) · warm/cold(WAN-166).
「엣지 없음」(WAN-84/88/111/114/124/151/201/248)은 **다른 질문**(재탭 진입 규칙이 무작위와
구분되나)이라 이 표가 뒤집지 않는다. 숏 축은 WAN-89/145/164에서 (c)로 닫혔다.

## 재현 (이 PR 실측)

```
# 1h·2h·4h: 브레이커+널 + 재탭(D) 대비 병기(--with-retap · 1분봉·무거움)
uv run python -m backtest.wan258_breaker_null --part null --tf 1h,2h,4h \
    --with-retap --jobs 6
# 15m: 브레이커+널만(재탭 미측정 — run_once 비용). --append로 덧붙인다.
uv run python -m backtest.wan258_breaker_null --part null --tf 15m --jobs 6 --append
# 활주로 필터 축(옵트인 · 자기-TF): 필터 끈 팔 + 문턱 켠 팔 병기
uv run python -m backtest.wan258_breaker_null --part null --tf 1h --runway 1.0,2.0,3.0 --jobs 6
uv run python -m backtest.wan258_breaker_null --part summary   # CSV에서 요약만
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
from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
)

REPORTS_DIR = Path("backtest/reports")
NULL_CSV = REPORTS_DIR / "wan258_breaker_null.csv"
SUMMARY_MD = REPORTS_DIR / "wan258_breaker_null_summary.md"

#: 채택 좌표(WAN-182/252) — 9종목 × 못 박은 6년 × 작업 TF. `harness` 기본값에서 읽는다.
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
NINE_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS
WORK_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

#: 브레이커 진입 = 무효화 봉 시가/종가 시장가 · 고정 1.5R 익절(WAN-90 채택값).
TAKE_PROFIT_R = 1.5

#: ATR 손절 배수(진입가 ∓ k·ATR = 1R). WAN-152/255 `atr` 선례(1.5·ATR)와 같은 값.
STOP_ATR_MULT = 1.5

#: ATR 길이(봉). 존폭 필터(`zone_width_atr_length=14`)와 같은 값이라 자를 통일한다.
ATR_LENGTH = 14

#: 판정 게이트 — 심볼당 거래 20건(WAN-84/143/248/255 유효 기준).
MIN_TRADES_FOR_VERDICT = 20
ALPHA = 0.05

#: 부트스트랩 반복 수·시드. 위치 무작위화 시드(풀 생성)는 부트스트랩 시드와 분리한다.
BOOTSTRAP_ITERATIONS = 200
POOL_SEED = 258
BOOTSTRAP_SEED = 258

#: 존당 무작위 위치 복제 수(풀 크기 배수) — WAN-248/255와 같은 값.
DEFAULT_POOL_K = 8

#: 편중 진단에서 빼 보는 심볼들(완료기준 4).
LEAVE_OUT_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE")

#: 진입 시점 사다리 A/B(시장가).
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
    pos_by_time: dict[int, int]


def _atr_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], length: int
) -> list[float]:
    """단순 ATR(True Range의 `length` 이동평균). 존폭 필터(`zone_width_atr`)와 같은 정의.

    각 인덱스 `i`의 값은 `[i-length+1, i]` 봉의 TR 평균이라 **그 봉(i)까지만** 쓴다(룩어헤드
    없음 — 브레이커 진입은 무효화 봉 시점에 이 값을 안다). 워밍업(i<length-1)은 사용 가능한
    TR로 부분 평균한다(NaN 대신 — 셋업이 워밍업에서 조용히 사라지지 않게).
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
    pos_by_time = {t: i for i, t in enumerate(times)}
    return _Arrays(times, opens, highs, lows, closes, atr, pos_by_time)


# --------------------------------------------------------------------------- #
# 브레이커 셋업 (한 OB) — 진입/손절/익절/청산 확정
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _BreakerSetup:
    """한 OB의 무효화 돌파(브레이커) 진입 셋업(비용 미반영 원가 정보)."""

    side: PositionSide
    entry_time: int
    entry_price: float
    stop_price: float
    exit_time: int
    exit_price: float
    reason: ExitReason
    gross_r: float
    """청산 사유 기준 gross R(비용 전): 손절 −1.0 · 익절 +1.5 · 미청산 부분 R."""
    runway_atr: float
    """진입가에서 진행 방향 가장 가까운 반대 존까지 거리 ÷ ATR. 벽 없으면 `inf`.
    미계산(활주로 축 꺼짐)이면 `inf`."""
    atr_pct: float
    """진입 봉 ATR ÷ 진입가(변동성 대리 · 활주로 상관 진단용)."""


def _passes_guard(entry_price: float, stop_price: float, cfg: BacktestConfig) -> bool:
    """손절폭 가드(WAN-79) — 진입가·손절가만 보는 자본 무관 게이트.

    실제·가짜 셋업을 **같은 자로** 사전 필터해 부트스트랩 개수를 맞춘다. `risk_sizing`이
    없으면 가드도 없다(고정 비율 경로).
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
    """브레이커 진입(start_idx 봉부터)의 청산 (시각, 가격, 사유, gross R).

    손절/익절 중 **먼저 닿은 쪽**. 같은 봉에 둘 다 닿으면 **보수적으로 손절**. 둘 다 안 닿으면
    데이터 끝 종가에서 부분 R로 청산(`END_OF_DATA`).
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


def _runway_atr(
    *,
    traded: OrderBlock,
    is_long: bool,
    entry_price: float,
    entry_time: int,
    atr_val: float,
    all_obs: Sequence[OrderBlock],
) -> float:
    """진입가에서 **진행 방향의 가장 가까운 반대 존**까지 거리 ÷ ATR (WAN-137 규칙).

    숏(하향 돌파)은 **아래 첫 불리시 OB**(지지)의 `top`, 롱(상향 돌파)은 **위 첫 베어리시 OB**
    (저항)의 `bottom`까지 잰다. 진입 시각까지 클리핑(①확정 시점 ②뚫린/소멸 존 제외 ③진행
    방향 근단)한다. 벽이 없으면 `inf`(활주로 무한 = 필터 항상 통과). ATR≤0이면 `inf`.
    """
    if atr_val <= 0.0:
        return math.inf
    best_dist: float | None = None
    for zone in all_obs:
        if zone is traded:
            continue
        if zone.confirmed_time > entry_time:
            continue  # ② 룩어헤드: 진입 시각 이후 확정 존 제외.
        if zone.break_time is not None and zone.break_time <= entry_time:
            continue  # ① 이미 무효화된 존은 벽이 아니다.
        if zone.swept_time is not None and zone.swept_time <= entry_time:
            continue
        if is_long:
            if zone.direction is not OrderBlockDirection.BEARISH:
                continue
            if zone.bottom <= entry_price:
                continue  # ③ 진입가 위 저항만.
            dist = zone.bottom - entry_price
        else:
            if zone.direction is not OrderBlockDirection.BULLISH:
                continue
            if zone.top >= entry_price:
                continue  # ③ 진입가 아래 지지만.
            dist = entry_price - zone.top
        if best_dist is None or dist < best_dist:
            best_dist = dist
    if best_dist is None:
        return math.inf
    return best_dist / atr_val


def build_breaker_setups(
    obs: Sequence[OrderBlock],
    arrays: _Arrays,
    *,
    entry_point: str,
    stop_variant: str,
    direction: str,
    cfg: BacktestConfig,
    tp_r: float = TAKE_PROFIT_R,
    compute_runway: bool = False,
    all_obs: Sequence[OrderBlock] | None = None,
) -> list[_BreakerSetup]:
    """무효화된 OB마다 브레이커 진입 셋업을 만든다(방향 = OB 방향의 반대).

    무효화 봉(`ob.break_time`) 종가(A)/다음 봉 시가(B)에 돌파 방향으로 진입하고, 손절(ob 반대
    경계/atr)·고정 1.5R 익절을 확정한다. 손절폭 가드를 통과한 셋업만 남긴다. `compute_runway`
    면 진입가에서 다음 반대 존까지 활주로(÷ATR)를 계산해 실는다(자기-TF, `all_obs` 대상).
    """
    n = len(arrays.times)
    walls = all_obs if all_obs is not None else obs
    setups: list[_BreakerSetup] = []
    for ob in obs:
        if ob.break_time is None:
            continue
        is_bull_ob = ob.direction is OrderBlockDirection.BULLISH
        is_long = not is_bull_ob  # 베어리시 OB 상향 돌파 → 롱 · 불리시 OB 하향 돌파 → 숏.
        if direction == DIR_LONG and not is_long:
            continue
        if direction == DIR_SHORT and is_long:
            continue
        break_pos = arrays.pos_by_time.get(ob.break_time)
        if break_pos is None or break_pos + 1 >= n:
            continue
        if entry_point == ENTRY_A_CLOSE:
            entry_time = arrays.times[break_pos]
            entry_price = arrays.closes[break_pos]
        else:  # ENTRY_B_OPEN
            entry_time = arrays.times[break_pos + 1]
            entry_price = arrays.opens[break_pos + 1]
        atr_val = arrays.atr[break_pos]
        if stop_variant == STOP_OB:
            # 존 반대편 경계 재탈환: 숏=top(위) · 롱=bottom(아래). 1R = 존 높이.
            stop = ob.bottom if is_long else ob.top
        else:  # STOP_ATR
            unit = STOP_ATR_MULT * atr_val
            stop = entry_price - unit if is_long else entry_price + unit
        risk = (entry_price - stop) if is_long else (stop - entry_price)
        if risk <= 0.0:
            continue
        if not _passes_guard(entry_price, stop, cfg):
            continue
        runway = (
            _runway_atr(
                traded=ob,
                is_long=is_long,
                entry_price=entry_price,
                entry_time=entry_time,
                atr_val=atr_val,
                all_obs=walls,
            )
            if compute_runway
            else math.inf
        )
        exit_time, exit_price, reason, gross_r = _walk_exit(
            is_long=is_long,
            entry_price=entry_price,
            stop=stop,
            start_idx=break_pos + 1,
            arrays=arrays,
            tp_r=tp_r,
        )
        setups.append(
            _BreakerSetup(
                side=PositionSide.LONG if is_long else PositionSide.SHORT,
                entry_time=entry_time,
                entry_price=entry_price,
                stop_price=stop,
                exit_time=exit_time,
                exit_price=exit_price,
                reason=reason,
                gross_r=gross_r,
                runway_atr=runway,
                atr_pct=(atr_val / entry_price) if entry_price > 0 else 0.0,
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


def _breaker_trade(
    setup: _BreakerSetup,
    equity: float,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
) -> _SeqTrade | None:
    """브레이커 셋업을 **테이커** 진입·청산 `Trade`로 변환(재탭은 메이커 · 이건 시장가)."""
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
    return _SeqTrade(trade=trade, net_r=net_r)


def sequence_breaker(
    setups: Sequence[_BreakerSetup],
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
) -> list[_SeqTrade]:
    """동시 1포지션 제약으로 브레이커 셋업을 시간순 배치(`_sequence_and_cost`와 같은 규칙)."""
    ordered = sorted(setups, key=lambda s: (s.entry_time, s.exit_time))
    equity = cfg.initial_capital
    last_exit = -1
    out: list[_SeqTrade] = []
    for setup in ordered:
        if setup.entry_time < last_exit:
            continue
        seq = _breaker_trade(setup, equity, cfg, funding_rates)
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
    fake_setups: Sequence[_BreakerSetup],
    *,
    real_total: float,
    cfg: BacktestConfig,
    timeframe: str,
    funding_rates: Sequence[FundingRate] | None,
    iterations: int,
    bootstrap_seed: int,
    fake_zones: int,
) -> _NullResult:
    """실제 브레이커 거래의 (방향, 4시각대) 구성을 맞춰 가짜 풀에서 재추출 → 총수익 분포 → p."""
    if not real_trades or not fake_setups:
        return _NullResult(None, None, None, None, 0, len(fake_setups), fake_zones)

    pool_by_bucket: dict[tuple[PositionSide, int], list[_BreakerSetup]] = defaultdict(list)
    pool_by_side: dict[PositionSide, list[_BreakerSetup]] = defaultdict(list)
    for setup in fake_setups:
        pool_by_bucket[_bucket_key(setup.side, setup.entry_time)].append(setup)
        pool_by_side[setup.side].append(setup)

    target_counts: dict[tuple[PositionSide, int], int] = defaultdict(int)
    for seq in real_trades:
        target_counts[_bucket_key(seq.trade.side, seq.trade.entry_time)] += 1

    rng = random.Random(bootstrap_seed)
    returns: list[float] = []
    for _ in range(iterations):
        sampled: list[_BreakerSetup] = []
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
        seq_trades = sequence_breaker(sampled, cfg, funding_rates)
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


class BreakerRow(BaseModel):
    """한 (심볼, TF, 구간, 방향, 진입점, 손절변형, 활주로문턱)의 브레이커 전략 + 널 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    direction: str
    entry_point: str
    stop_variant: str
    runway_atr: float | None = None
    """활주로 필터 문턱(ATR 배수). `None` = 필터 끔(base 팔 · 널·재탭 병기). 양수 = 필터 켠
    팔(실제만). 옛 CSV(열 없음)는 None으로 로드된다."""
    real_total_return: float
    real_num_trades: int
    real_mean_net_r: float | None
    real_max_drawdown: float
    real_win_rate: float | None
    pool_size: int
    fake_zones: int
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    """`ob` 손절 · base 팔에서만 비퇴화(§docstring). `atr`·필터 팔은 None(원값만)."""
    iterations: int
    retap_total_return: float | None
    """(b) 재탭(D) 비교 — `--with-retap`일 때만. 없으면 None."""
    retap_num_trades: int | None
    runway_density_corr: float | None
    """활주로(ATR) vs 진입 봉 ATR%의 Pearson 상관(필터 팔 · 변동성 대리 진단). 없으면 None."""
    buy_hold: float

    @field_validator("*", mode="before")
    @classmethod
    def _nan_to_none(cls, value: object) -> object:
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """두 계열의 Pearson 상관(표본 < 3이거나 분산 0이면 None)."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


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
    runway_atrs: tuple[float | None, ...]
    pool_k: int
    iterations: int
    with_retap: bool


def _segment_window(
    market: harness.MarketData, segment: str
) -> tuple[harness.MarketData, int | None, pd.DataFrame]:
    """(평가용 창, 평가 경계 ms, buy_hold 계산용 HTF 프레임). WAN-248/255와 같은 규약."""
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
    setups: Sequence[_BreakerSetup], eval_from_ms: int | None
) -> list[_BreakerSetup]:
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
        params=harness.pin_invalidation_cancel(arm.params()),
        cfg=arm.config(market.timeframe),
        eval_from_ms=eval_from_ms,
    )
    metrics = outcome.result.metrics
    return metrics.total_return, len(outcome.result.trades)


def run_cell(task: _Task, *, log: bool = True) -> list[BreakerRow]:
    """한 (심볼, TF)의 구간 × 방향 × 진입점 × 손절변형 × 활주로문턱 브레이커 전략 + 널."""
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

    need_runway = any(t is not None for t in task.runway_atrs)
    rows: list[BreakerRow] = []
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
        cfg = harness.legacy_build_config(task.timeframe, funding_enabled=True)

        for direction in task.directions:
            retap: tuple[float, int] | None = None
            if task.with_retap:
                retap = _retap_return(window, direction, eval_from_ms=eval_from)
            for entry_point in task.entry_points:
                for stop_variant in task.stop_variants:
                    rows.extend(
                        _rows_for_arm(
                            task=task,
                            segment=segment,
                            direction=direction,
                            entry_point=entry_point,
                            stop_variant=stop_variant,
                            arrays=arrays,
                            real_ob=real_ob,
                            fake_ob=fake_ob,
                            cfg=cfg,
                            funding_rates=window.funding_rates,
                            eval_from_ms=eval_from,
                            need_runway=need_runway,
                            retap=retap,
                            buy_hold=buy_hold,
                            log=log,
                        )
                    )
    return rows


def _rows_for_arm(
    *,
    task: _Task,
    segment: str,
    direction: str,
    entry_point: str,
    stop_variant: str,
    arrays: _Arrays,
    real_ob: OrderBlockResult,
    fake_ob: OrderBlockResult,
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    eval_from_ms: int | None,
    need_runway: bool,
    retap: tuple[float, int] | None,
    buy_hold: float,
    log: bool,
) -> list[BreakerRow]:
    """한 (진입점 × 손절변형)에서 활주로 문턱마다 행을 낸다(None=base 팔은 널·재탭 병기)."""
    base_setups = _filter_eval_setups(
        build_breaker_setups(
            real_ob.order_blocks,
            arrays,
            entry_point=entry_point,
            stop_variant=stop_variant,
            direction=direction,
            cfg=cfg,
            compute_runway=need_runway,
            all_obs=real_ob.order_blocks,
        ),
        eval_from_ms,
    )
    # base 팔의 위치 널(ob 손절에서만 비퇴화).
    base_null: _NullResult
    if stop_variant == STOP_OB:
        fake_setups = _filter_eval_setups(
            build_breaker_setups(
                fake_ob.order_blocks,
                arrays,
                entry_point=entry_point,
                stop_variant=stop_variant,
                direction=direction,
                cfg=cfg,
            ),
            eval_from_ms,
        )
        base_real_trades = sequence_breaker(base_setups, cfg, funding_rates)
        base_null = _matched_null(
            base_real_trades,
            fake_setups,
            real_total=_total_return(base_real_trades, cfg, task.timeframe),
            cfg=cfg,
            timeframe=task.timeframe,
            funding_rates=funding_rates,
            iterations=task.iterations,
            bootstrap_seed=BOOTSTRAP_SEED,
            fake_zones=len(fake_ob.order_blocks),
        )
    else:
        base_null = _NullResult(None, None, None, None, 0, 0, len(fake_ob.order_blocks))

    rows: list[BreakerRow] = []
    for runway_thr in task.runway_atrs:
        if runway_thr is None:
            setups = base_setups
            null = base_null
            corr = None
        else:
            setups = [s for s in base_setups if s.runway_atr >= runway_thr]
            null = _NullResult(None, None, None, None, 0, 0, len(fake_ob.order_blocks))
            finite = [(s.runway_atr, s.atr_pct) for s in base_setups if math.isfinite(s.runway_atr)]
            corr = _pearson([x for x, _ in finite], [y for _, y in finite])
        rows.append(
            _make_row(
                task=task,
                segment=segment,
                direction=direction,
                entry_point=entry_point,
                stop_variant=stop_variant,
                runway_atr=runway_thr,
                setups=setups,
                cfg=cfg,
                funding_rates=funding_rates,
                null=null,
                retap=retap,
                runway_density_corr=corr,
                buy_hold=buy_hold,
            )
        )
        if log:
            r = rows[-1]
            tag = "base" if runway_thr is None else f"rw>={runway_thr}"
            print(
                f"[wan258] {task.symbol} {task.timeframe} {segment} {direction} "
                f"{entry_point}/{stop_variant}/{tag}: "
                f"real={r.real_total_return:.4f} n={r.real_num_trades} "
                f"netR={r.real_mean_net_r} p={r.random_p_value}",
                flush=True,
            )
    return rows


def _make_row(
    *,
    task: _Task,
    segment: str,
    direction: str,
    entry_point: str,
    stop_variant: str,
    runway_atr: float | None,
    setups: Sequence[_BreakerSetup],
    cfg: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None,
    null: _NullResult,
    retap: tuple[float, int] | None,
    runway_density_corr: float | None,
    buy_hold: float,
) -> BreakerRow:
    trades = sequence_breaker(setups, cfg, funding_rates)
    total = _total_return(trades, cfg, task.timeframe)
    mdd = _max_drawdown(trades, cfg, task.timeframe)
    net_rs = [s.net_r for s in trades]
    wins = sum(1 for s in trades if s.trade.realized_pnl > 0)
    win_rate = wins / len(trades) if trades else None
    return BreakerRow(
        symbol=task.symbol,
        timeframe=task.timeframe,
        segment=segment,
        direction=direction,
        entry_point=entry_point,
        stop_variant=stop_variant,
        runway_atr=runway_atr,
        real_total_return=total,
        real_num_trades=len(trades),
        real_mean_net_r=_mean(net_rs),
        real_max_drawdown=mdd,
        real_win_rate=win_rate,
        pool_size=null.pool_size,
        fake_zones=null.fake_zones,
        random_mean_return=null.random_mean_return,
        random_ci_low=null.random_ci_low,
        random_ci_high=null.random_ci_high,
        random_p_value=null.random_p_value,
        iterations=null.iterations,
        retap_total_return=retap[0] if retap is not None else None,
        retap_num_trades=retap[1] if retap is not None else None,
        runway_density_corr=runway_density_corr,
        buy_hold=buy_hold,
    )


def _run_task_logged(task: _Task) -> list[BreakerRow]:
    return run_cell(task, log=True)


def run_null(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = WORK_TIMEFRAMES,
    segments: Sequence[str] = DEFAULT_SEGMENTS,
    directions: Sequence[str] = DIRECTIONS,
    entry_points: Sequence[str] = ENTRY_POINTS,
    stop_variants: Sequence[str] = STOP_VARIANTS,
    runway_atrs: Sequence[float | None] = (None,),
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    pool_k: int = DEFAULT_POOL_K,
    iterations: int = BOOTSTRAP_ITERATIONS,
    with_retap: bool = False,
    jobs: int = 1,
    log: bool = True,
) -> list[BreakerRow]:
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
            runway_atrs=tuple(runway_atrs),
            pool_k=pool_k,
            iterations=iterations,
            with_retap=with_retap,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_cell(task, log=log)]
    rows: list[BreakerRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


# --------------------------------------------------------------------------- #
# 통계·집계·판정
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[BreakerRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[BreakerRow]:
    frame = pd.read_csv(path)
    return [BreakerRow.model_validate(record) for record in frame.to_dict(orient="records")]


def _is_base(row: BreakerRow) -> bool:
    return row.runway_atr is None


def is_significant(row: BreakerRow, alpha: float = ALPHA) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제>무작위평균(WAN-70/84/248/255와 같은 자)."""
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def eligible_rows(rows: Sequence[BreakerRow]) -> list[BreakerRow]:
    return [
        r
        for r in rows
        if r.random_p_value is not None and r.real_num_trades >= MIN_TRADES_FOR_VERDICT
    ]


def significance_counts(rows: Sequence[BreakerRow]) -> tuple[int, int]:
    eligible = eligible_rows(rows)
    return sum(1 for r in eligible if is_significant(r)), len(eligible)


def verdict_null(rows: Sequence[BreakerRow]) -> str:
    """(a) 브레이커가 무작위 위치를 이기나 — `ob` 손절 base 팔 널 기준(§docstring)."""
    ob_rows = [r for r in rows if r.stop_variant == STOP_OB and _is_base(r)]
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
        head = "**(b) 브레이커 진입(ob 손절)은 무작위 위치와 구분되지 않는다**"
    elif sig == total:
        head = "**(a) 브레이커 진입(ob 손절)이 무작위 위치를 유의하게 이긴다**"
    else:
        head = "**(c) 일부 셀에만 유의성 — TF·구간·방향에 갈린다**"
    return f"유효 셀 {total}개 중 유의 {sig}개({tf_note}) → {head}"


def _round(v: float | None, scale: float = 1.0, digits: int = 2) -> object:
    return round(v * scale, digits) if v is not None else "—"


def summary_table(rows: Sequence[BreakerRow], *, entry_point: str, stop_variant: str) -> str:
    """(TF × 구간 × 방향) 심볼평균 요약 — base 팔(활주로 필터 없음) 한 진입점·손절변형."""
    header = (
        "| TF | 구간 | 방향 | 실제수익 | +심볼 | n | 승률 | net R | 무작위평균 | 유의 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: | --: |"
    )
    scoped = [
        r
        for r in rows
        if r.entry_point == entry_point and r.stop_variant == stop_variant and _is_base(r)
    ]
    tf_order = {tf: i for i, tf in enumerate(WORK_TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    dir_order = {d: i for i, d in enumerate(DIRECTIONS)}
    groups: dict[tuple[str, str, str], list[BreakerRow]] = defaultdict(list)
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
        win = _mean([c.real_win_rate for c in cells if c.real_win_rate is not None])
        rand = _mean([c.random_mean_return for c in cells if c.random_mean_return is not None])
        pos = sum(1 for v in real_vals if v > 0)
        mean_n = round(_mean([float(c.real_num_trades) for c in cells]) or 0, 1)
        body.append(
            f"| {tf} | {seg} | {direction} | {_round(_mean(real_vals), 100)}% | "
            f"{pos} | {mean_n} | {_round(win, 100)}% | "
            f"{_round(net_r, 1, 3)} | {_round(rand, 100)}% | {sig}/{len(eligible)} |"
        )
    return header + "\n" + "\n".join(body)


def retap_compare_table(rows: Sequence[BreakerRow]) -> str:
    """(b) 재탭(D) 대비 — 심볼평균 (TF × 구간 × 방향).

    브레이커 = 주 설정 **B_open/ob**(다음 봉 시가 · 존 경계 손절 = 실배포 파리티). 재탭값은
    (심볼,TF,구간,방향)당 상수라 이 한 설정에 붙여 비교한다. ⚠️ **15m은 retap 미측정**
    (`run_once`가 15m·6yr에서 셀당 ~37분, WAN-203). 널 판정 (a)는 15m 포함이다.
    """
    scoped = [
        r
        for r in rows
        if r.entry_point == ENTRY_B_OPEN
        and r.stop_variant == STOP_OB
        and _is_base(r)
        and r.retap_total_return is not None
    ]
    if not scoped:
        return "_(재탭 비교 없음 — `--with-retap`으로 채운다.)_"
    header = (
        "| TF | 구간 | 방향 | 브레이커수익 | 재탭수익 | 브레이커>재탭 | net R |\n"
        "| -- | -- | -- | --: | --: | --: | --: |"
    )
    tf_order = {tf: i for i, tf in enumerate(WORK_TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    dir_order = {d: i for i, d in enumerate(DIRECTIONS)}
    groups: dict[tuple[str, str, str], list[BreakerRow]] = defaultdict(list)
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
        form_mean = _mean([c.real_total_return for c in cells])
        retap_vals = [c.retap_total_return for c in cells if c.retap_total_return is not None]
        retap_mean = _mean(retap_vals)
        beats = sum(
            1
            for c in cells
            if c.retap_total_return is not None and c.real_total_return > c.retap_total_return
        )
        net_r = _mean([c.real_mean_net_r for c in cells if c.real_mean_net_r is not None])
        body.append(
            f"| {tf} | {seg} | {direction} | {_round(form_mean, 100)}% | "
            f"{_round(retap_mean, 100)}% | {beats}/{len(cells)} | {_round(net_r, 1, 3)} |"
        )
    return header + "\n" + "\n".join(body)


def leave_one_out_lines(rows: Sequence[BreakerRow]) -> list[str]:
    """완료기준 4 — 심볼 하나 빼면 심볼평균 부호가 유지되는가(`ob` 손절 · base 팔)."""
    lines: list[str] = []
    scoped = [r for r in rows if r.stop_variant == STOP_OB and _is_base(r)]
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


def _runway_section(rows: Sequence[BreakerRow]) -> list[str]:
    """활주로 필터 축이 실행된 경우에만 대비 섹션을 낸다(옵트인 · 자기-TF)."""
    filtered = [r for r in rows if r.runway_atr is not None]
    if not filtered:
        return []
    thresholds = sorted({r.runway_atr for r in filtered if r.runway_atr is not None})
    corrs = [r.runway_density_corr for r in filtered if r.runway_density_corr is not None]
    corr_note = (
        f"활주로(ATR) vs 진입 봉 ATR% Pearson 상관 심볼평균 **{sum(corrs) / len(corrs):+.2f}**"
        if corrs
        else "상관 계산 표본 부족"
    )
    header = (
        "| TF | 구간 | 방향 | 문턱 | 실제수익 | +심볼 | n | 승률 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | --: |"
    )
    tf_order = {tf: i for i, tf in enumerate(WORK_TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    dir_order = {d: i for i, d in enumerate(DIRECTIONS)}
    # 필터 끈(base) 팔도 대비용으로 문턱 "0"으로 병기(같은 진입점·손절 = B_open/ob).
    base_ob = [
        r
        for r in rows
        if _is_base(r) and r.entry_point == ENTRY_B_OPEN and r.stop_variant == STOP_OB
    ]
    scoped = [r for r in filtered if r.entry_point == ENTRY_B_OPEN and r.stop_variant == STOP_OB]
    groups: dict[tuple[str, str, str, float], list[BreakerRow]] = defaultdict(list)
    for r in base_ob:
        groups[(r.timeframe, r.segment, r.direction, 0.0)].append(r)
    for r in scoped:
        assert r.runway_atr is not None
        groups[(r.timeframe, r.segment, r.direction, r.runway_atr)].append(r)
    body: list[str] = []
    for (tf, seg, direction, thr), cells in sorted(
        groups.items(),
        key=lambda kv: (
            tf_order.get(kv[0][0], 9),
            seg_order.get(kv[0][1], 9),
            dir_order.get(kv[0][2], 9),
            kv[0][3],
        ),
    ):
        real_vals = [c.real_total_return for c in cells]
        win = _mean([c.real_win_rate for c in cells if c.real_win_rate is not None])
        pos = sum(1 for v in real_vals if v > 0)
        mean_n = round(_mean([float(c.real_num_trades) for c in cells]) or 0, 1)
        thr_label = "끔" if thr == 0.0 else f"≥{thr:g}"
        body.append(
            f"| {tf} | {seg} | {direction} | {thr_label} | {_round(_mean(real_vals), 100)}% | "
            f"{pos} | {mean_n} | {_round(win, 100)}% |"
        )
    return [
        "## §5 활주로(다음 반대 존까지 거리) 필터 (옵트인 · 자기-TF)",
        "",
        "⚠️ 활주로 = 진입가에서 진행 방향 가장 가까운 반대 존까지 거리 ÷ ATR(숏=아래 첫 불리시 "
        "OB · 롱=위 첫 베어리시 OB · 진입 시각 클리핑 · 뚫린 존 제외). 문턱 이상만 진입하면 "
        "「튕김」을 피하나 표본이 준다(20건 게이트). B_open/ob 손절 기준.",
        "",
        f"⚠️ **{corr_note}** — 크면 활주로가 새 정보가 아니라 변동성/구간의 재탕(WAN-131/251).",
        "",
        f"문턱: {', '.join(f'{t:g}' for t in thresholds)} (ATR 배수). 필터 끈 팔은 「끔」 행.",
        "",
        header,
        "\n".join(body),
        "",
    ]


def build_summary_markdown(rows: Sequence[BreakerRow]) -> str:
    lines: list[str] = [
        "# WAN-258 — 무효화 돌파(브레이커) 진입 전략 + 방향-매칭 무작위 위치 널",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · 9종목 × 작업 TF(15m·1h·2h·4h) × 구간"
        "(IS·oos_warm) × 방향(롱·숏·롱숏) × 진입점(A 종가·B 다음시가) × 손절(ob·atr). "
        "브레이커 = OB 무효화(`break_time`) 시 돌파 방향 진입(테이커) · 고정 1.5R · 손절 = "
        "존 반대경계 재탈환/ATR. 대조군 = **방향·존폭·빈도·무효화 매칭, 위치만 무작위**"
        "(WAN-248 기계).",
        "",
        "## ⚠️ 널은 `ob` 손절 · base 팔에서만 비퇴화",
        "",
        "브레이커 진입가는 무효화 봉 시장가라 `atr` 손절(진입가 ∓ k·ATR)은 존 경계와 무관하다 "
        "→ `atr`은 원값·재탭 대비만, 널은 `ob`(존 경계가 1R에 들어옴)에서만 낸다. 이 널은 "
        "**브레이커 「타이밍 엣지」가 아니라 「1R 기하 위치 정보」**를 잰다(무작위 시각 널은 "
        "별도 이슈 · WAN-231 계열).",
        "",
        "## §1 판정 (a) — 브레이커가 무작위 위치를 이기나 (`ob` 손절)",
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
        "브레이커 = 주 설정 **B_open/ob** · 심볼평균. 재탭값은 (심볼,TF,구간,방향)당 상수라 "
        "이 설정에 붙여 비교한다. ⚠️ **15m은 retap 미측정**(run_once 비용 · WAN-203).",
        "",
        retap_compare_table(rows),
        "",
        "## §3 편중 — leave-one-out (ETH·SOL·DOGE, `ob` 손절)",
        "",
        *leave_one_out_lines(rows),
        "",
        *_runway_section(rows),
        "## 결론 · 인용 금지",
        "",
        "- **측정 전용** — 기본값·토대 불변, `short_enabled` 기본값 불변, 실거래 보류 유지.",
        "- ⚠️ **「엣지 찾았다」는 네 관문(널·OOS·pen 성격·leave-one-out) 다 통과 시에만.** "
        "브레이커는 테이커라 `pen_5bp`(지정가 관통 벌점)가 성격상 안 걸린다 — 보수화는 이미 "
        "테이커 비용에 있고, `pen_5bp`는 재탭(D) 팔 주석 전용.",
        "- ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248)의 반박이 아니다** — 저건 "
        "재탭 진입 규칙이 무작위와 구분되나, 이건 브레이커의 1R 기하 위치 정보를 묻는다.",
        "- ⚠️ WAN-255(형성 진입)가 이미 모멘텀-연속 진입 하나에서 부정 판정을 냈다 — 기대를 "
        "낮춰 읽는다. 숏 축은 WAN-89/145/164에서 (c)로 닫혔다.",
        "- 범위 밖(후속): 무작위 **시각** 널(WAN-231) · 활주로 상위-TF(②)·익절 대체(b) · "
        "진입점 C(지정가 되테스트) · §병행 북(WAN-213) · **15m 재탭(b) 대비**.",
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
        print(f"[wan258] {NULL_CSV} 없음 — 먼저 --part null을 돌리세요.")
        return
    rows = rows_from_csv(NULL_CSV)
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text(build_summary_markdown(rows), encoding="utf-8")
    print(f"[wan258] summary → {SUMMARY_MD}")


def _split(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(x.strip() for x in value.split(",") if x.strip())


def _parse_runway(value: str | None) -> tuple[float | None, ...]:
    """`--runway 1.0,2.0` → (None, 1.0, 2.0). 미지정=(None,)(활주로 축 꺼짐).

    base 팔(None)은 항상 포함한다(널·재탭 병기의 헤드라인). 숫자 문턱은 필터 켠 팔.
    """
    if not value:
        return (None,)
    out: list[float | None] = [None]
    for token in value.split(","):
        token = token.strip().lower()
        if not token or token in ("none", "off"):
            continue
        out.append(float(token))
    return tuple(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-258 브레이커 진입 전략 + 무작위 위치 널")
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
        "--runway",
        type=str,
        default=None,
        help="활주로 필터 문턱(콤마 · ATR 배수). 미지정=축 꺼짐(base 팔만).",
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
            runway_atrs=_parse_runway(args.runway),
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
        print(f"[wan258] null {len(frame)}행 → {NULL_CSV}")

    if part in ("summary", "all"):
        _run_summary()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
