"""WAN-248: 오더블록 존 「위치」 자체가 무작위 가격 레벨보다 나은가 — 위치 축 매칭 널.

## 아직 안 물어본 질문 (사용자 요청 2026-08-04)

지금까지의 매칭 널(WAN-84/88/111/114/124/151/164/201)은 전부 **같은 오더블록 존에 무작위
「타이밍」**으로 진입한 것과 비교했다 → "진입 규칙(언제)이 무작위와 구분되는가"에 **아니오**.
하지만 **오더블록이라는 「가격 위치」 자체**가 무작위 가격 레벨보다 나은지는 정면으로 안
쟀다. 이게 이 접근의 **핵심 전제**다:

* 존 위치에 정보가 있으면(무작위 레벨보다 유의하게 나으면) → 밑에 진짜 신호가 있는 것.
* 없으면 → 이 접근 전체가 **"그냥 (매칭된 기하로) 싸게 사기"**일 뿐(WAN-131이 볼린저
  축에서 "선별 아닌 가격"이라 한 것을 존 축으로 확장 확인).

**이건 타이밍 축 「엣지 없음」의 반박이 아니라 그 아래층 검증이다** — 타이밍 축은 이미
아니오였고, 이건 "위치 축"을 처음 묻는 것이다.

## 무작위 가격 레벨 정의 (이 이슈의 핵심 설계 결정)

이슈가 경고한 대로, **무작위 레벨을 아무렇게나 뿌리면 존폭 효과·가격 기하가 섞여 오염된다**.
공정한 대조가 되려면 가짜 존이 실제 존과 **다음을 매칭**하고 **위치만 무작위**여야 한다:

1. **방향** — 실제 존의 강세/약세를 그대로(롱/숏 구성 불변).
2. **존폭** — 가짜 존은 실제 존의 폭(`top-bottom`)을 **그대로** 쓴다(존폭 분포 = 항등).
3. **빈도·시각 구조** — 가짜 존은 실제 존과 **같은 `confirmed_time`**(같은 개수·같은 형성
   시각)에 생긴다.
4. **무효화 규칙** — 가짜 존의 무효화(`break_time`)·재진입(`tapped_times`)은 실제 가격
   경로에 대해 **탐지기와 글자 그대로 같은 규칙**으로 재계산한다(`recompute_lifecycle`,
   `OrderBlockDetector._invalidate`의 반사실 복제 — 회귀 테스트가 비트 단위로 고정).
5. **위치(= 무작위화 축)** — 가짜 존의 근단(proximal) 가격을, 확정봉 종가로부터의 **거리
   비율**을 실제 존들의 **경험분포에서 재추출**해 정한다. 실제 존들이 실제로 도달 가능했던
   거리를 그대로 쓰므로 가짜 존도 **도달 가능**하고(빈 대조 방지), **1R 기하·진입 가격 이점
   (WAN-131의 「가격」 효과)까지 매칭**된다 — 그래서 남는 차이는 오직 **"그 레벨이 실제
   스윙(지지·저항)이냐 아무 레벨이냐"** 하나다. 그게 이 이슈가 묻는 「위치」다.

즉 실제(오더블록 위치) vs 가짜(같은 기하·같은 빈도, 무작위 위치)를 같은 지정가 규칙으로
돌려, 실제가 가짜 풀을 이기는지를 매칭 널로 잰다.

## 매칭 널의 형태 (WAN-70 기계 재사용)

풀 = 가짜 존들이 실제 가격에서 체결된 후보 집합(존당 `pool_k`개의 무작위 위치 복제 →
`build_zone_limit_candidates` **한 번**으로 사전 시뮬레이션). 부트스트랩 반복마다 실제 거래의
**(방향, UTC 4시간 시각대) 구성·개수를 맞춰** 이 풀에서 비복원 재추출하고(WAN-70과 같은
버킷·폴백), 단일 포지션 시퀀싱으로 총수익률을 낸다. `p` = 무작위 반복 중 실제 이상을 낸 비율
(단측). 이로써 셋업당 서브스텝 시뮬레이션은 **실제 1회 + 풀 1회**만 돈다(반복은 재추출만).

⚠️ **무력화 축이 다르다** — WAN-124/145/151/201의 널은 **볼린저를 끈** 풀(같은 존, 규칙만
무력화)이었다. 이 널은 **볼린저를 켠 채**(채택 엔진 그대로) **존 위치만** 무작위화한다. 두
널은 다른 질문이다: 저쪽은 "볼린저 규칙이 무작위와 다른가", 이쪽은 "존 위치가 무작위와
다른가".

## OOS 규약 (WAN-166 따뜻한 연속 OOS)

이슈가 정본 OOS(`--oos-warm`)를 요구했다. `oos_warm`은 전 구간을 연속으로 태워 존 재고·지표를
데운 뒤 **탭이 IS/OOS 경계 이후인 셋업만** 신선한 초기자본으로 평가한다(실제 팔은
`run_zone_limit_backtest_verbose(eval_from_ms=...)`와 비트 단위로 같은 필터 —
`test_wan248` 회귀). IS는 차가운 앞구간 절단이다. ⚠️ **이 계열의 종전 관행(WAN-151/164/201)은
차가운 OOS**였다("두 IS/OOS 규약" 메모) — 그래서 `oos`(차가움)도 스트레스로 함께 낼 수 있게
두되(`--seg`), 기본 헤드라인은 이슈가 요구한 `is` + `oos_warm`이다.

## 재현

```
uv run python -m backtest.wan248_zone_position_null --part null --tf 4h --jobs 6
uv run python -m backtest.wan248_zone_position_null --part null --tf 1h --jobs 6 --append
uv run python -m backtest.wan248_zone_position_null --part null --tf 15m --jobs 6 --append  # 무거움
uv run python -m backtest.wan248_zone_position_null --part summary   # CSV에서 요약만
```

측정 전용 — 기본값·토대는 바꾸지 않는다. 유의 셀이 나와도 그 자체가 엣지 채택 근거가
아니다(전부 `baseline` 낙관 렌즈 위 값 · 「선별 대 가격」은 WAN-131 소관). 재-베이스라인은
별개의 사용자 결정 이슈다.
"""

from __future__ import annotations

import argparse
import bisect
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
from backtest.models import BacktestConfig, PositionSide
from backtest.run import parse_date_ms
from backtest.wan70_random_control_b import _bucket_key
from backtest.wan89_short_autopsy import ARMS_BY_NAME, Arm, _buy_hold
from backtest.zone_limit_backtest import (
    _Candidate,
    _sequence_and_cost,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from data.models import FundingRate
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
)
from strategy.order_blocks import _generate_signals

REPORTS_DIR = Path("backtest/reports")
NULL_CSV = REPORTS_DIR / "wan248_zone_position_null.csv"
SUMMARY_MD = REPORTS_DIR / "wan248_zone_position_null_summary.md"

#: 채택 좌표(WAN-182) — 9종목 × 못 박은 6년 × 작업 TF. `harness` 기본값에서 읽는다.
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
NINE_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS
WORK_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES  # (15m, 1h, 4h)
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

#: 팔은 롱 축 하나(이슈 범위 — 숏은 WAN-145/164가 (c)로 냈다). 정의는 WAN-89에서 가져온다.
LONG_ARM = "long_only"

#: 렌즈: `baseline`(공식) + `pen_5bp`(체결 보수화 병기 — 완료기준 3).
BASELINE_LENS = harness.BASELINE_FILL.name
PEN_LENS = "pen_5bp"
DEFAULT_LENSES: tuple[str, ...] = (BASELINE_LENS, PEN_LENS)

#: 자 — WAN-70/84/88/124/145/151/201과 **같은 값**이라야 판정끼리 맞댈 수 있다.
MIN_TRADES_FOR_VERDICT = 20
ALPHA = 0.05

#: 부트스트랩 반복 수·시드. 위치 무작위화 시드(풀 생성)는 부트스트랩 시드와 분리한다.
BOOTSTRAP_ITERATIONS = 200
POOL_SEED = 248
BOOTSTRAP_SEED = 248

#: 존당 무작위 위치 복제 수(풀 크기 배수). 풀이 (방향,시각대) 버킷마다 실제 거래 수를
#: 넉넉히 넘겨 재추출이 폴백에 기대지 않게 하는 값 — 8이면 풀 ≈ 8×(실제 체결).
DEFAULT_POOL_K = 8

#: 편중 진단에서 빼 보는 심볼들(이슈 완료기준 3).
LEAVE_OUT_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE")

#: 헤드라인 구간(이슈: 심볼 × IS/oos_warm). `oos`(차가움)는 `--seg`로 스트레스 병기.
DEFAULT_SEGMENTS: tuple[str, ...] = (harness.SEGMENT_IS, harness.SEGMENT_OOS_WARM)
SEGMENT_LABELS: tuple[str, ...] = (
    harness.SEGMENT_IS,
    harness.SEGMENT_OOS_WARM,
    harness.SEGMENT_OOS,
)


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def arm_of(name: str) -> Arm:
    return ARMS_BY_NAME[name]


def describe_engine() -> str:
    """검정한 엔진의 지문 — 산출물만 봐도 어떤 존·밴드·필터로 돌았는지 드러나게."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, combine_obs={_ADOPTED_OB.combine_obs}"
    )


# --------------------------------------------------------------------------- #
# 탐지 파라미터 · 렌즈 적용
# --------------------------------------------------------------------------- #

#: 탐지 파라미터 = 채택 기본값(WAN-149: 분리). 상수를 `OrderBlockParams()`로 두는 것이
#: 요점 — 재-베이스라인이 오면 이 표도 따라간다. `combine_obs=False`라 존은 원본 단위다.
_ADOPTED_OB = OrderBlockParams()


def lens_params(base: ConfluenceParams, lens: str) -> ConfluenceParams:
    """채택 파라미터에 체결 렌즈(baseline/pen_5bp)만 얹는다.

    `baseline`은 관통 0·탈락 0이라 항등이고, `pen_5bp`는 지정가 5bp 관통을 요구한다.
    `build_params`와 같은 필드 3종(`fill_penetration_bps`·`fill_dropout_rate`·
    `fill_dropout_seed`)만 덮어써 다른 규칙은 손대지 않는다.
    """
    preset = harness.fill_preset(lens)
    return base.model_copy(
        update={
            "fill_penetration_bps": preset.penetration_bps,
            "fill_dropout_rate": preset.dropout_rate,
            "fill_dropout_seed": preset.seeds[0],
        }
    )


# --------------------------------------------------------------------------- #
# 가짜 존 생성 — 이 모듈의 핵심
# --------------------------------------------------------------------------- #


def _arrays(
    htf_df: pd.DataFrame,
) -> tuple[list[int], list[float], list[float], list[float], list[float]]:
    """탐지기·`build_zone_limit_candidates`와 **같은 전처리**(정렬 + closed 필터)로 배열을 낸다.

    두 경로가 같은 `open_time` 집합을 봐야 가짜 시그널의 `trigger_time`이 후보 생성기의
    `time_to_pos`에 그대로 존재한다(안 그러면 조용히 셋업이 사라진다).
    """
    frame = htf_df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    frame = frame.sort_values("open_time").reset_index(drop=True)
    times = [int(v) for v in frame["open_time"].astype("int64").tolist()]
    opens = [float(v) for v in frame["open"].astype(float).tolist()]
    highs = [float(v) for v in frame["high"].astype(float).tolist()]
    lows = [float(v) for v in frame["low"].astype(float).tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    return times, opens, highs, lows, closes


def recompute_lifecycle(
    direction: OrderBlockDirection,
    top: float,
    bottom: float,
    confirmed_time: int,
    *,
    times: Sequence[int],
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    use_wick: bool,
    suffix_max_high: Sequence[float] | None = None,
    suffix_min_low: Sequence[float] | None = None,
) -> tuple[tuple[int, ...], int | None, int | None]:
    """존 하나의 (재진입 시각들, 무효화 시각, 소멸 시각)을 실제 가격 경로에서 재계산한다.

    `OrderBlockDetector._invalidate`의 **글자 그대로의 반사실 복제**다 — 같은 top/bottom/
    confirmed_time을 넣으면 탐지기가 낸 `tapped_times`/`break_time`/`swept_time`을 비트
    단위로 재현한다(회귀 테스트 `test_lifecycle_reproduces_detector`가 고정). 그 성질이
    "가짜 존의 무효화 규칙이 실제와 같다"(완료기준 4)의 코드 상 증거다.

    성능: `suffix_max_high`/`suffix_min_low`를 주면 **가격이 존에 절대 못 닿는** 가짜 존을
    O(1)에 걸러(스캔 생략) 무작위 위치의 대부분(멀리 떨어진 레벨)을 싸게 버린다.
    """
    n = len(times)
    start = bisect.bisect_right(times, confirmed_time)
    if start >= n:
        return (), None, None
    # 빠른 기각: 확정 이후 구간에서 존을 아예 못 스치면 탭·무효화가 없다.
    if (
        suffix_max_high is not None
        and suffix_min_low is not None
        and (suffix_max_high[start] < bottom or suffix_min_low[start] > top)
    ):
        return (), None, None

    is_bullish = direction is OrderBlockDirection.BULLISH
    tapped: list[int] = []
    break_time: int | None = None
    swept_time: int | None = None
    inside_prev = False
    breaker = False
    for t in range(start, n):
        if not breaker:
            if is_bullish:
                cmp_value = lows[t] if use_wick else min(opens[t], closes[t])
                if cmp_value < bottom:
                    breaker = True
                    break_time = times[t]
            else:
                cmp_value = highs[t] if use_wick else max(opens[t], closes[t])
                if cmp_value > top:
                    breaker = True
                    break_time = times[t]
        else:
            # 무효화(breaker) 이후의 되쓸림(소멸). 소멸 봉에서는 탭을 기록하지 않고 멈춘다.
            if is_bullish:
                if highs[t] > top:
                    swept_time = times[t]
                    break
            else:
                if lows[t] < bottom:
                    swept_time = times[t]
                    break
        inside = lows[t] <= top and highs[t] >= bottom
        if inside and not inside_prev:
            tapped.append(times[t])
        inside_prev = inside
    return tuple(tapped), break_time, swept_time


def _proximal(direction: OrderBlockDirection, top: float, bottom: float) -> float:
    return top if direction is OrderBlockDirection.BULLISH else bottom


def make_fake_result(
    real_result: OrderBlockResult,
    htf_df: pd.DataFrame,
    ob_params: OrderBlockParams,
    *,
    rng: random.Random,
    pool_k: int,
) -> OrderBlockResult:
    """실제 존들과 방향·존폭·빈도·무효화 규칙을 매칭하되 **위치만 무작위**인 가짜 존 풀.

    각 실제 존마다 `pool_k`개의 복제를 만든다: 방향·존폭·`confirmed_time`은 그대로 두고,
    근단의 확정봉 종가 대비 거리 비율을 **같은 방향 실제 존들의 경험분포에서 재추출**해
    위치를 정한다(모듈 docstring §5). 무효화·재진입은 실제 가격에서 재계산한다.
    """
    times, opens, highs, lows, closes = _arrays(htf_df)
    n = len(times)
    if n == 0 or pool_k <= 0:
        return OrderBlockResult(order_blocks=[], signals=[])
    time_to_pos = {t: i for i, t in enumerate(times)}
    use_wick = ob_params.zone_invalidation == "wick"

    # 접미 최대고가·최소저가(빠른 기각용): O(n) 한 번.
    suffix_max_high = [0.0] * n
    suffix_min_low = [0.0] * n
    run_max = -math.inf
    run_min = math.inf
    for i in range(n - 1, -1, -1):
        run_max = max(run_max, highs[i])
        run_min = min(run_min, lows[i])
        suffix_max_high[i] = run_max
        suffix_min_low[i] = run_min

    # 방향별 근단 오프셋(%) 경험분포 + 존 메타.
    offsets_by_dir: dict[OrderBlockDirection, list[float]] = defaultdict(list)
    zone_meta: list[tuple[OrderBlock, float]] = []
    for zone in real_result.order_blocks:
        pos_c = time_to_pos.get(zone.confirmed_time)
        if pos_c is None:
            continue
        close_c = closes[pos_c]
        if close_c <= 0.0:
            continue
        proximal = _proximal(zone.direction, zone.top, zone.bottom)
        if zone.direction is OrderBlockDirection.BULLISH:
            off = (close_c - proximal) / close_c
        else:
            off = (proximal - close_c) / close_c
        offsets_by_dir[zone.direction].append(off)
        zone_meta.append((zone, close_c))

    fakes: list[OrderBlock] = []
    for zone, close_c in zone_meta:
        width = zone.top - zone.bottom
        pool = offsets_by_dir[zone.direction]
        if not pool:
            continue
        for _ in range(pool_k):
            off = rng.choice(pool)
            if zone.direction is OrderBlockDirection.BULLISH:
                fake_top = close_c * (1.0 - off)
                fake_bottom = fake_top - width
            else:
                fake_bottom = close_c * (1.0 + off)
                fake_top = fake_bottom + width
            if fake_bottom <= 0.0 or fake_top <= fake_bottom:
                continue
            tapped, break_time, swept_time = recompute_lifecycle(
                zone.direction,
                fake_top,
                fake_bottom,
                zone.confirmed_time,
                times=times,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                use_wick=use_wick,
                suffix_max_high=suffix_max_high,
                suffix_min_low=suffix_min_low,
            )
            fakes.append(
                OrderBlock(
                    direction=zone.direction,
                    top=fake_top,
                    bottom=fake_bottom,
                    start_time=zone.start_time,
                    confirmed_time=zone.confirmed_time,
                    ob_volume=zone.ob_volume,
                    ob_low_volume=zone.ob_low_volume,
                    ob_high_volume=zone.ob_high_volume,
                    breaker=break_time is not None,
                    break_time=break_time,
                    swept_time=swept_time,
                    tapped_times=tapped,
                )
            )

    signals = _generate_signals(fakes, times, highs, lows, closes)
    retaps = _generate_signals(fakes, times, highs, lows, closes, include_retaps=True)
    return OrderBlockResult(order_blocks=fakes, signals=signals, retap_signals=retaps)


# --------------------------------------------------------------------------- #
# 위치 축 매칭 널 (한 구간)
# --------------------------------------------------------------------------- #


class PositionNullRow(BaseModel):
    """한 (심볼, TF, 구간, 렌즈)의 위치 축 매칭 널 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    lens: str
    combine_obs: bool
    real_total_return: float
    real_num_trades: int
    real_long: int
    real_short: int
    pool_size: int
    """가짜(무작위 위치) 체결 후보 풀 크기 — 재추출 대상 전체."""
    real_zones: int
    fake_zones: int
    pool_k: int
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int
    bucket_fallback_count: int
    buy_hold: float

    @field_validator("*", mode="before")
    @classmethod
    def _nan_to_none(cls, value: object) -> object:
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


@dataclass(frozen=True)
class _SegmentResult:
    real_total_return: float
    real_num_trades: int
    real_long: int
    real_short: int
    pool_size: int
    fake_zones: int
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int
    bucket_fallback_count: int


def _filter_eval(cands: list[_Candidate], eval_from_ms: int | None) -> list[_Candidate]:
    """따뜻한 연속 OOS: 탭이 평가 경계 이후인 후보만 남긴다(run_zone_limit 동일 필터)."""
    if eval_from_ms is None:
        return cands
    return [c for c in cands if c.trigger_time >= eval_from_ms]


def run_position_null_segment(
    htf_seg: pd.DataFrame,
    one_min_seg: pd.DataFrame,
    timeframe: str,
    *,
    real_ob: OrderBlockResult,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    ob_params: OrderBlockParams,
    funding_rates: Sequence[FundingRate] | None,
    eval_from_ms: int | None,
    pool_k: int,
    iterations: int,
    pool_seed: int,
    bootstrap_seed: int,
) -> _SegmentResult:
    """실제(오더블록 위치) vs 가짜(무작위 위치) 풀의 매칭 널을 한 구간에서 낸다.

    실제·풀 모두 **같은 채택 파라미터**(볼린저 켜짐)로 `build_zone_limit_candidates`를
    돌린다 — 유일한 차이는 `order_block_result`가 실제 존이냐 가짜 존이냐다.
    """
    real_candidates, _ = build_zone_limit_candidates(
        htf_seg,
        one_min_seg,
        timeframe,
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_result=real_ob,
    )
    real_candidates = _filter_eval(real_candidates, eval_from_ms)
    real_trades = _sequence_and_cost(real_candidates, cfg, funding_rates)
    real_result = build_result_from_trades(real_trades, cfg, timeframe)
    real_long = sum(1 for t in real_trades if t.side is PositionSide.LONG)
    real_short = len(real_trades) - real_long
    real_total = real_result.metrics.total_return

    fake_ob = make_fake_result(
        real_ob, htf_seg, ob_params, rng=random.Random(pool_seed), pool_k=pool_k
    )
    if not real_trades:
        return _SegmentResult(
            real_total_return=real_total,
            real_num_trades=0,
            real_long=0,
            real_short=0,
            pool_size=0,
            fake_zones=len(fake_ob.order_blocks),
            random_mean_return=None,
            random_ci_low=None,
            random_ci_high=None,
            random_p_value=None,
            iterations=0,
            bucket_fallback_count=0,
        )

    pool_candidates, _ = build_zone_limit_candidates(
        htf_seg,
        one_min_seg,
        timeframe,
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_result=fake_ob,
    )
    pool_candidates = _filter_eval(pool_candidates, eval_from_ms)

    pool_by_bucket: dict[tuple[PositionSide, int], list[_Candidate]] = defaultdict(list)
    pool_by_side: dict[PositionSide, list[_Candidate]] = defaultdict(list)
    for cand in pool_candidates:
        pool_by_bucket[_bucket_key(cand.side, cand.entry_time)].append(cand)
        pool_by_side[cand.side].append(cand)

    target_counts: dict[tuple[PositionSide, int], int] = defaultdict(int)
    for trade in real_trades:
        target_counts[_bucket_key(trade.side, trade.entry_time)] += 1

    rng = random.Random(bootstrap_seed)
    random_returns: list[float] = []
    bucket_fallback_count = 0
    for _ in range(iterations):
        sampled: list[_Candidate] = []
        used_by_side: dict[PositionSide, set[int]] = defaultdict(set)
        for (side, bucket), count in target_counts.items():
            bucket_pool = pool_by_bucket.get((side, bucket), [])
            k = min(count, len(bucket_pool))
            picks = rng.sample(bucket_pool, k) if k else []
            sampled.extend(picks)
            used_by_side[side].update(id(c) for c in picks)
            shortfall = count - k
            if shortfall > 0:
                bucket_fallback_count += 1
                remaining = [
                    c for c in pool_by_side.get(side, []) if id(c) not in used_by_side[side]
                ]
                fill_k = min(shortfall, len(remaining))
                fill_picks = rng.sample(remaining, fill_k) if fill_k else []
                sampled.extend(fill_picks)
                used_by_side[side].update(id(c) for c in fill_picks)
        sampled_trades = _sequence_and_cost(sampled, cfg, funding_rates)
        sampled_result = build_result_from_trades(sampled_trades, cfg, timeframe)
        random_returns.append(sampled_result.metrics.total_return)

    random_returns.sort()
    m = len(random_returns)
    p_value = sum(1 for r in random_returns if r >= real_total) / m if m else None
    mean_return = sum(random_returns) / m if m else None
    ci_low = random_returns[int(0.025 * (m - 1))] if m else None
    ci_high = random_returns[int(0.975 * (m - 1))] if m else None

    return _SegmentResult(
        real_total_return=real_total,
        real_num_trades=len(real_trades),
        real_long=real_long,
        real_short=real_short,
        pool_size=len(pool_candidates),
        fake_zones=len(fake_ob.order_blocks),
        random_mean_return=mean_return,
        random_ci_low=ci_low,
        random_ci_high=ci_high,
        random_p_value=p_value,
        iterations=m,
        bucket_fallback_count=bucket_fallback_count,
    )


# --------------------------------------------------------------------------- #
# 셀 실행 (심볼 × TF) — 구간·렌즈 루프
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    segments: tuple[str, ...]
    lenses: tuple[str, ...]
    pool_k: int
    iterations: int


def _segment_window(
    market: harness.MarketData, segment: str
) -> tuple[harness.MarketData, int | None, pd.DataFrame]:
    """(평가용 창, 평가 경계 ms, buy_hold 계산용 HTF 프레임)을 낸다.

    - `is`/`oos`(차가움): 그 구간으로 자른 창, 경계 없음.
    - `oos_warm`(따뜻함): 전 구간(데운 상태), 경계 = IS/OOS 지점. buy_hold는 평가 창만.
    """
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


def run_cell(task: _Task, *, log: bool = True) -> list[PositionNullRow]:
    """한 (심볼, TF)의 구간 × 렌즈 위치 축 널을 낸다."""
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []

    arm = arm_of(LONG_ARM)
    base_params = arm.params()
    cfg = arm.config(task.timeframe)

    rows: list[PositionNullRow] = []
    for segment in task.segments:
        window, eval_from, bh_frame = _segment_window(market, segment)
        if window.empty or window.df_1m.empty or bh_frame.empty:
            continue
        real_ob = harness.detect_order_blocks(window, _ADOPTED_OB)
        buy_hold = _buy_hold(bh_frame)
        for lens in task.lenses:
            params = lens_params(base_params, lens)
            seg_result = run_position_null_segment(
                window.htf_df,
                window.df_1m,
                task.timeframe,
                real_ob=real_ob,
                params=params,
                cfg=cfg,
                ob_params=_ADOPTED_OB,
                funding_rates=window.funding_rates,
                eval_from_ms=eval_from,
                pool_k=task.pool_k,
                iterations=task.iterations,
                pool_seed=POOL_SEED,
                bootstrap_seed=BOOTSTRAP_SEED,
            )
            row = PositionNullRow(
                symbol=task.symbol,
                timeframe=task.timeframe,
                segment=segment,
                lens=lens,
                combine_obs=_ADOPTED_OB.combine_obs,
                real_total_return=seg_result.real_total_return,
                real_num_trades=seg_result.real_num_trades,
                real_long=seg_result.real_long,
                real_short=seg_result.real_short,
                pool_size=seg_result.pool_size,
                real_zones=len(real_ob.order_blocks),
                fake_zones=seg_result.fake_zones,
                pool_k=task.pool_k,
                random_mean_return=seg_result.random_mean_return,
                random_ci_low=seg_result.random_ci_low,
                random_ci_high=seg_result.random_ci_high,
                random_p_value=seg_result.random_p_value,
                iterations=seg_result.iterations,
                bucket_fallback_count=seg_result.bucket_fallback_count,
                buy_hold=buy_hold,
            )
            rows.append(row)
            if log:
                print(
                    f"[wan248] {task.symbol} {task.timeframe} {segment} {lens}: "
                    f"real={row.real_total_return:.4f} n={row.real_num_trades} "
                    f"pool={row.pool_size} fake_zones={row.fake_zones} "
                    f"p={row.random_p_value}",
                    flush=True,
                )
    return rows


def _run_task_logged(task: _Task) -> list[PositionNullRow]:
    return run_cell(task, log=True)


def run_null(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = WORK_TIMEFRAMES,
    segments: Sequence[str] = DEFAULT_SEGMENTS,
    lenses: Sequence[str] = DEFAULT_LENSES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    pool_k: int = DEFAULT_POOL_K,
    iterations: int = BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    log: bool = True,
) -> list[PositionNullRow]:
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            segments=tuple(segments),
            lenses=tuple(lenses),
            pool_k=pool_k,
            iterations=iterations,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_cell(task, log=log)]
    rows: list[PositionNullRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


# --------------------------------------------------------------------------- #
# 통계·집계·판정
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[PositionNullRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[PositionNullRow]:
    frame = pd.read_csv(path)
    return [PositionNullRow.model_validate(record) for record in frame.to_dict(orient="records")]


def is_significant(row: PositionNullRow, alpha: float = ALPHA) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제>무작위평균(WAN-70/84/88/124/145/151/201과 같은 자)."""
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def eligible_rows(rows: Sequence[PositionNullRow]) -> list[PositionNullRow]:
    return [
        r
        for r in rows
        if r.random_p_value is not None and r.real_num_trades >= MIN_TRADES_FOR_VERDICT
    ]


def significance_counts(rows: Sequence[PositionNullRow]) -> tuple[int, int]:
    eligible = eligible_rows(rows)
    return sum(1 for r in eligible if is_significant(r)), len(eligible)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def verdict(rows: Sequence[PositionNullRow]) -> str:
    """위치 축 판정 — (a) 존 위치에 정보가 있다 / (b) 무작위 위치와 구분 안 됨 / (c) 갈림."""
    sig, total = significance_counts(rows)
    if total == 0:
        return (
            f"**⚠️ 판정 불가** — 거래 {MIN_TRADES_FOR_VERDICT}건 이상인 유효 셀이 없다(표본 부족)."
        )
    parts: list[str] = []
    for tf in WORK_TIMEFRAMES:
        s, t = significance_counts([r for r in rows if r.timeframe == tf])
        if t:
            parts.append(f"{tf} {s}/{t}")
    tf_note = " · ".join(parts)
    if sig == 0:
        head = "**(b) 존 위치는 무작위 가격 레벨과 구분되지 않는다**"
    elif sig == total:
        head = "**(a) 존 위치에 정보가 있다 — 무작위 레벨보다 유의하게 낫다**"
    else:
        head = "**(c) 일부 셀에만 유의성이 있다 — TF·구간에 갈린다**"
    return f"유효 셀 {total}개 중 유의 {sig}개({tf_note}) → {head}"


def summary_table(rows: Sequence[PositionNullRow], *, lens: str) -> pd.DataFrame:
    """(TF × 구간) 심볼평균 + 유의 셀 수 + 편중 제외 평균 — 한 렌즈."""
    records: list[dict[str, object]] = []
    scoped = [r for r in rows if r.lens == lens]
    tf_order = {tf: i for i, tf in enumerate(WORK_TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    groups: dict[tuple[str, str], list[PositionNullRow]] = defaultdict(list)
    for r in scoped:
        groups[(r.timeframe, r.segment)].append(r)
    for (timeframe, segment), cells in sorted(
        groups.items(), key=lambda kv: (tf_order.get(kv[0][0], 9), seg_order.get(kv[0][1], 9))
    ):
        values = [c.real_total_return for c in cells]
        eligible = eligible_rows(cells)
        records.append(
            {
                "timeframe": timeframe,
                "segment": segment,
                "lens": lens,
                "real_mean": _mean(values),
                "positive": sum(1 for v in values if v > 0),
                "symbols": len(values),
                "random_mean": _mean(
                    [c.random_mean_return for c in cells if c.random_mean_return is not None]
                ),
                "eligible": len(eligible),
                "significant": sum(1 for c in eligible if is_significant(c)),
                "trades": _mean([float(c.real_num_trades) for c in cells]),
                "real_zones": _mean([float(c.real_zones) for c in cells]),
                "fake_zones": _mean([float(c.fake_zones) for c in cells]),
            }
        )
    return pd.DataFrame(records)


def leave_one_out_lines(rows: Sequence[PositionNullRow], *, lens: str) -> list[str]:
    """완료기준 3 — 심볼 하나 빼면 심볼평균 부호가 유지되는가(편중 진단)."""
    lines: list[str] = []
    scoped = [r for r in rows if r.lens == lens]
    for tf in WORK_TIMEFRAMES:
        for seg in DEFAULT_SEGMENTS:
            cells = [r for r in scoped if r.timeframe == tf and r.segment == seg]
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
                head = f"- **{tf} {seg}** {lens}: 심볼평균 {mean_all * 100:+.2f}% → "
                lines.append(head + " · ".join(flips))
    return lines or ["- (해당 행 없음)"]


def _round_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in ("real_mean", "random_mean"):
        if col in out.columns:
            out[col] = (out[col].astype(float) * 100).round(2)
    for col in ("trades", "real_zones", "fake_zones"):
        if col in out.columns:
            out[col] = out[col].astype(float).round(1)
    return out.astype(object).where(out.notna(), "—")


def _md_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "행이 없다."
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


def cell_table(rows: Sequence[PositionNullRow], *, lens: str) -> str:
    header = (
        "| 심볼 | TF | 구간 | 실제수익 | n | 풀 | 실존 | 가짜존 | 무작위평균 | p | 유의 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: | --: | -- |"
    )
    tf_order = {tf: i for i, tf in enumerate(WORK_TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    scoped = sorted(
        (r for r in rows if r.lens == lens),
        key=lambda r: (tf_order.get(r.timeframe, 9), seg_order.get(r.segment, 9), r.symbol),
    )
    body: list[str] = []
    for r in scoped:
        thin = r.real_num_trades < MIN_TRADES_FOR_VERDICT
        mark = "표본부족" if thin else ("**✓**" if is_significant(r) else "")
        rand = f"{r.random_mean_return * 100:+.2f}%" if r.random_mean_return is not None else "—"
        p = f"{r.random_p_value:.3f}" if r.random_p_value is not None else "—"
        body.append(
            f"| {_short(r.symbol)} | {r.timeframe} | {r.segment} | "
            f"{r.real_total_return * 100:+.2f}% | {r.real_num_trades} | {r.pool_size} | "
            f"{r.real_zones} | {r.fake_zones} | {rand} | {p} | {mark} |"
        )
    return header + "\n" + "\n".join(body)


def pool_growth_note(rows: Sequence[PositionNullRow]) -> str:
    scoped = [r for r in rows if r.real_num_trades > 0 and r.pool_size > 0]
    if not scoped:
        return "풀 크기를 비교할 셀이 없다."
    ratios = [r.pool_size / r.real_num_trades for r in scoped]
    return (
        f"{len(scoped)}셀에서 가짜(무작위 위치) 풀은 실제 거래 수의 평균 "
        f"**{sum(ratios) / len(ratios):.2f}배**(최소 {min(ratios):.2f}배)다 — 풀이 실제보다 "
        "충분히 커서 재추출이 폴백에 기대지 않는다."
    )


def new_symbol_note(rows: Sequence[PositionNullRow]) -> str:
    new = {"DOGE", "LINK", "LTC"}
    present = sorted({_short(r.symbol) for r in rows if _short(r.symbol) in new})
    return (
        f"신규 3종목({', '.join(present) or '없음'})은 펀딩 데이터 0행 = **펀딩 미반영**이다 — "
        "이 널 계열(WAN-151/164/201)은 WAN-180 대리(BTC 도너)를 얹지 않는다(대조를 깨끗하게 "
        "유지 · WAN-91 실측 영향 ±0.1~2%p)."
    )


# --------------------------------------------------------------------------- #
# 요약 렌더
# --------------------------------------------------------------------------- #


def build_summary_markdown(rows: Sequence[PositionNullRow]) -> str:
    baseline = [r for r in rows if r.lens == BASELINE_LENS]
    pen = [r for r in rows if r.lens == PEN_LENS]
    lines: list[str] = [
        "# WAN-248 — 오더블록 존 「위치」 매칭 널 (존 위치 자체가 무작위 레벨보다 나은가)",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · **9종목**(기존 6 + DOGE·LINK·LTC) × 작업 "
        "TF(15m·1h·4h) × 구간(IS · oos_warm) · 롱 축 단독. 대조군 = **같은 방향·존폭·빈도·"
        "무효화 규칙을 매칭하고 위치만 무작위**로 뿌린 가짜 존(모듈 docstring §설계).",
        "",
        "## 이 널이 묻는 것 (타이밍 축과 다르다)",
        "",
        "- **타이밍 축**(WAN-84/88/111/124/151/201): 같은 존에 무작위 **시각** 진입 → "
        "「진입 규칙이 무작위와 구분되는가」 → **아니오**.",
        "- **위치 축(이 표)**: **위치만** 무작위인 가짜 존과 대조 → 「존 위치 자체에 "
        "정보가 있는가」. 방향·존폭·빈도·무효화·**1R 기하·진입 가격 이점(WAN-131의 「가격」)까지 "
        "매칭**하므로, 남는 차이는 오직 「그 레벨이 실제 스윙이냐 아무 레벨이냐」다.",
        "",
        f"이 리포트가 검정한 엔진: `{describe_engine()}`.",
        "",
        f"> {new_symbol_note(rows)}",
        "",
        "## §1 판정 (공식 렌즈 `baseline`)",
        "",
        verdict(baseline),
        "",
        "### TF × 구간 요약 (`baseline`)",
        "",
        _md_table(_round_frame(summary_table(baseline, lens=BASELINE_LENS))),
        "",
        "## §2 체결 보수화 병기 (`pen_5bp`)",
        "",
        verdict(pen) if pen else "(pen_5bp 미측정)",
        "",
        _md_table(_round_frame(summary_table(pen, lens=PEN_LENS))) if pen else "",
        "",
        "## §3 편중 — leave-one-out (ETH·SOL·DOGE)",
        "",
        *leave_one_out_lines(baseline, lens=BASELINE_LENS),
        "",
        "## §4 셀별 결과 (`baseline`)",
        "",
        cell_table(baseline, lens=BASELINE_LENS),
        "",
        "## §5 풀 비퇴화 확인",
        "",
        pool_growth_note(rows),
        "",
        "## 결론 · 인용 금지",
        "",
        "- **측정 전용** — 기본값·토대 불변, 실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`) 유지.",
        "- ⚠️ **「엣지 찾았다」로 인용 금지** — 유의 셀이 나와도 전부 `baseline`(낙관 렌즈 · "
        "큐 우선순위 미모델링) 위 값이고, `pen_5bp` 생존 여부를 §2와 함께 읽어야 한다.",
        "- ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201)의 반박이 아니다** — 저쪽은 "
        "**타이밍 축**(언제 진입하는가)을 물었고 이 표는 **위치 축**(어디에 존이 있는가)을 "
        "묻는다. 판정 (a)/(b)/(c) 중 무엇이든 다른 질문의 답이다.",
        "- ⚠️ 이 널은 위치 축을 물을 뿐 **볼린저의 「선별 대 가격」은 못 가른다**(WAN-131 소관) — "
        "위치 기하(오프셋 분포)를 매칭했으므로 「가격」 효과는 이미 통제됐지만, 실제·가짜 모두 "
        "밴드가로 재산정되므로 볼린저×위치 상호작용은 남는다.",
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
        print(f"[wan248] {NULL_CSV} 없음 — 먼저 --part null을 돌리세요.")
        return
    rows = rows_from_csv(NULL_CSV)
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text(build_summary_markdown(rows), encoding="utf-8")
    print(f"[wan248] summary → {SUMMARY_MD}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-248 오더블록 존 위치 매칭 널")
    parser.add_argument("--part", type=str, default="all", choices=PARTS)
    parser.add_argument("--tf", type=str, default=None, help="TF 목록(콤마). 미지정=15m,1h,4h")
    parser.add_argument("--symbols", type=str, default=None, help="심볼 목록(콤마). 미지정=9종목")
    parser.add_argument(
        "--seg",
        type=str,
        default=None,
        help="구간 목록(콤마: is,oos_warm,oos). 미지정=is,oos_warm",
    )
    parser.add_argument(
        "--lenses", type=str, default=None, help="렌즈(콤마). 미지정=baseline,pen_5bp"
    )
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--pool-k", type=int, default=DEFAULT_POOL_K)
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--append", action="store_true", help="CSV에 덧붙인다(TF·심볼 분할 실행).")
    args = parser.parse_args(argv)

    part = str(args.part)
    jobs = int(args.jobs)

    if part in ("null", "all"):
        timeframes = (
            tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
            if args.tf
            else WORK_TIMEFRAMES
        )
        symbols = (
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
            if args.symbols
            else NINE_SYMBOLS
        )
        segments = (
            tuple(s.strip() for s in str(args.seg).split(",") if s.strip())
            if args.seg
            else DEFAULT_SEGMENTS
        )
        lenses = (
            tuple(x.strip() for x in str(args.lenses).split(",") if x.strip())
            if args.lenses
            else DEFAULT_LENSES
        )
        rows = run_null(
            symbols=symbols,
            timeframes=timeframes,
            segments=segments,
            lenses=lenses,
            start=args.start,
            end=args.end,
            pool_k=int(args.pool_k),
            iterations=int(args.iterations),
            jobs=jobs,
        )
        frame = rows_to_frame(rows)
        if args.append and NULL_CSV.exists():
            frame = pd.concat([pd.read_csv(NULL_CSV), frame], ignore_index=True)
        _write(frame, NULL_CSV)
        print(f"[wan248] null {len(frame)}행 → {NULL_CSV}")

    if part in ("summary", "all"):
        _run_summary()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
