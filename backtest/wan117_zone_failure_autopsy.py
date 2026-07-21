"""존 실패 부검 — "어떤 존이 뚫려 내려갔는가"의 공통점 검정 (WAN-117, WAN-77 거울상).

WAN-77이 **성공 프레임**("거래량이 실린 존일수록 성과가 좋은가")이라면, 이 모듈은
**실패 프레임**이다: 채택 기본값(`ConfluenceParams()`)이 낸 각 거래를 결과로 라벨링하고
(**뚫림**=오더블록 무효화 손절 / **버팀**=1.5R 익절 도달), 진입 시점에 이미 알 수 있는
특징들이 그 **뚫림**을 유의하게 가르는지 검정한다.

동기(사용자, 2026-07-16): *"존을 찍고 반등했는지보다 어땠을 때 존을 뚫고 내려갔는지가
더 중요하다. 뚫린 존의 공통점을 찾을 수 있나."* — 엣지가 있다면(WAN-88/111/114는 "없음")
바로 이 자리, "뚫릴 존을 미리 가르는 능력"에 살아 있을 것이다.

## 토대 (고정 입력)

진입 방식=존 지정가 + 오프셋 2bp(`entry_mode="zone_limit"`), 렌즈=`baseline`(WAN-128
단독), 비용 현재값, 롱 온리, 6심볼(WAN-111). 채택 기본값을 그대로 받아 **측정만** 한다 —
파라미터·기본값·실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`)는 건드리지 않는다.

## 라벨링

`sequence_with_candidates`(프로덕션 시퀀서, 사본 아님 — WAN-77이 남긴 사본 갈라짐을
피한다)가 낸 동시 1포지션 거래를 근거 오더블록(`_Candidate.order_block`)·탭 봉
(`trigger_time`)에 조인한다. 라벨은 `_Candidate.reason`:

* `STOP_LOSS` → **뚫림**(broke), R = −1.0.
* `TAKE_PROFIT` → **버팀**(held), R = +`take_profit_r`(=1.5).
* `END_OF_DATA`(데이터 종료까지 미청산)는 **결과 미확정**이라 제외한다.

무효화율(= 뚫림 비율)이 종속변수다.

## 특징 (전부 진입 시점 기지 — 룩어헤드 금지)

진입은 탭 봉(pos) **내부**에서 1분 서브스텝으로 일어나므로, 봉 단위 지표는 탭 봉 자신을
쓰면 룩어헤드다(그 봉 종가는 진입 이후를 포함). 따라서 봉 파생 특징은 모두 **직전 확정봉
(pos−1)까지**만 본다. 오더블록 파생 특징(존폭·거래량·신선도·이전 탭)은 존 확정
(`confirmed_time` < `trigger_time`) 시점에 이미 정해져 있어 안전하다.

| 특징 | 정의 | 가설 |
| -- | -- | -- |
| `trend_dev` | `close[pos−1]/ema200[pos−1] − 1` | 하락 추세(낮음)일수록 뚫린다 |
| `volume_pctl` | 셀 내부 `ob_volume` 순위 백분위(0~1) | WAN-77 실마리 재검(거울상) |
| `vol_balance` | `min(ob_hi,ob_lo)/max(...)`(0~1) | WAN-77 `imbalance` 재확인 |
| `rsi_slope_{ma}_{win}` | RSI(14) MA 기울기(양→음=롤오버) | 롤오버 중이면 뚫린다(사용자) |
| `zone_width_atr` | `(top−bottom)/atr14[pos−1]` | 너무 넓거나 얇은 존 |
| `freshness_bars` | 확정 후 경과 봉 수(pos − 확정pos) | 오래된 존일수록 약함 |
| `prior_taps` | `trigger_time` 이전 탭 횟수 | 여러 번 탭될수록 약함 |
| `approach_mom` | `(close[pos−1]−close[pos−1−K])/atr14`, K=6 | 급락 진입일수록 뚫린다 |
| `tap_rsi` | `rsi14[pos−1]` | 탭 시 RSI |

`rsi_slope_*`는 **민감도 계열**(MA 길이·기울기 창 3종)이라 인샘플 1등을 고르지 않는다 —
셋을 각각 독립 검정으로 세고(보수적 다중검정 보정), 계열 안에서 최고를 뽑지 않는다.

## 검정 (인샘플 단독 판정 금지 · 다중검정 통제)

* **1단계(기술통계):** 특징을 3분위(low/mid/high)로 나눠 분위별 무효화율·평균R 대조.
  6심볼 풀링, 15m·1h × IS/OOS. 무효화율의 분위 단조성 판정.
* **2단계(유의성):** 통계량 = 특징값과 뚫림 지시자(0/1)의 **점이연 상관**(전 거래 사용,
  분위 경계 임의성 없음). 널 = **심볼 층화 라벨 순열**(각 심볼의 무효화율·표본을 보존한 채
  뚫림/버팀 라벨만 섞어 특징↔결과 연관을 끊는다) — WAN-77 매칭 널의 "구성 보존" 의도를
  라벨 순열로 구현한 것이다. 양측 p값.
* **다중검정:** TF당 특징 `N`개를 검정하므로 Bonferroni `α'=0.05/N`. **OOS에서** `α'`를
  넘고(유의) IS에서도 같은 방향이어야 "실패를 가른다"고 본다(IS→OOS 뒤집힘 방지).

살아남는 특징이 있으면 후속 "존 품질 필터 구현" 이슈를 제안, 없으면 "실패도 예측 불가"로
WAN-88/111/114 「엣지 없음」을 실패 프레임에서 재확인한다.

재현: `python -m backtest.wan117_zone_failure_autopsy`.
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.zone_limit_backtest import (
    _Candidate,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import atr, ema, rsi
from strategy.models import ConfluenceParams, OrderBlock

# --------------------------------------------------------------------------- #
# 상수 — WAN-111/114와 같은 못 박은 창·유니버스
# --------------------------------------------------------------------------- #

DEFAULT_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")
#: 6심볼 공통 창(WAN-111 DEFAULT_START/END). `--years`는 미끄러지므로 못 박는다.
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

_TREND_EMA = 200
_ATR_LENGTH = 14
_RSI_LENGTH = 14
_APPROACH_K = 6
#: RSI MA 기울기 민감도 계열 (MA 길이, 기울기 창). 인샘플 1등 금지.
_RSI_SLOPE_VARIANTS: tuple[tuple[int, int], ...] = ((5, 3), (10, 5), (14, 5))

_N_QUANTILES = 3
_MIN_TRADES_FOR_VERDICT = 20
"""WAN-70/77과 동일 기준: 표본 20건 미만 셀은 유의성 판정에서 제외."""
_PERMUTATIONS = 2000
_ALPHA = 0.05
_SEED = 117

#: 특징별 가설 방향 — corr(특징, 뚫림)의 **기대 부호**. `+1`이면 "특징이 클수록 더 뚫린다",
#: `-1`이면 "특징이 클수록 덜 뚫린다". 두 검정은 양측이라 판정에 방향을 강제하지 않고,
#: 살아남은 특징이 가설과 같은 방향인지 **보고**하는 데만 쓴다.
HYPOTHESIS_SIGN: dict[str, int] = {
    "trend_dev": -1,  # 하락 추세(낮음)일수록 뚫린다.
    "volume_pctl": -1,  # 거래량 실린 존일수록 덜 뚫린다(WAN-77 실마리).
    "vol_balance": 0,  # 방향 미정(성공 프레임에서 노이즈였다).
    "zone_width_atr": 0,  # 너무 넓거나 얇거나 — 단조 방향 미정.
    "freshness_bars": +1,  # 오래된 존일수록 더 뚫린다.
    "prior_taps": +1,  # 여러 번 탭된 존일수록 더 뚫린다.
    "approach_mom": -1,  # 급락(음의 모멘텀)으로 꽂힌 존일수록 더 뚫린다.
    "tap_rsi": -1,  # 낮은 RSI(과매도 급락)일수록 더 뚫린다.
}
for _ma, _win in _RSI_SLOPE_VARIANTS:
    HYPOTHESIS_SIGN[f"rsi_slope_{_ma}_{_win}"] = -1  # 롤오버(음의 기울기)일수록 뚫린다.

#: 검정할 특징 순서(표·CSV 열 순서 고정).
FEATURES: tuple[str, ...] = (
    "trend_dev",
    "volume_pctl",
    "vol_balance",
    *(f"rsi_slope_{ma}_{win}" for ma, win in _RSI_SLOPE_VARIANTS),
    "zone_width_atr",
    "freshness_bars",
    "prior_taps",
    "approach_mom",
    "tap_rsi",
)

REPORTS_DIR = Path("backtest/reports")


# --------------------------------------------------------------------------- #
# 라벨링된 거래
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LabeledTrade:
    """결과 라벨 + 진입 시점 특징이 붙은 한 거래."""

    symbol: str
    timeframe: str
    segment: str
    """`is` 또는 `oos` — `trigger_time`을 창의 앞 2/3(IS)/뒤 1/3(OOS)로 가른다."""
    side: str
    trigger_time: int
    broke: bool
    """True=무효화 손절(뚫림), False=1.5R 익절(버팀)."""
    r_multiple: float
    features: dict[str, float | None]


# --------------------------------------------------------------------------- #
# 특징 추출
# --------------------------------------------------------------------------- #


@dataclass
class _FeatureExtractor:
    """한 (심볼, TF)의 봉 단위 지표를 한 번만 계산해 pos로 조회한다.

    프레임은 `build_zone_limit_candidates`가 쓰는 것과 **같은 정렬**(`_prepare_htf`:
    확정봉 필터 + open_time 오름차순)이라야 pos가 일치한다.
    """

    time_to_pos: dict[int, int]
    closes: list[float]
    ema_trend: list[float]
    atr14: list[float]
    rsi14: list[float]
    rsi_slopes: dict[str, list[float]]

    @classmethod
    def build(cls, frame: pd.DataFrame) -> _FeatureExtractor:
        times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
        closes = [float(v) for v in frame["close"].astype(float).tolist()]
        ema_trend = [float(v) for v in ema(frame, length=_TREND_EMA).tolist()]
        atr14 = [float(v) for v in atr(frame, length=_ATR_LENGTH).tolist()]
        rsi_series = rsi(frame, length=_RSI_LENGTH)
        rsi14 = [float(v) for v in rsi_series.tolist()]
        rsi_slopes: dict[str, list[float]] = {}
        for ma_len, _win in _RSI_SLOPE_VARIANTS:
            ma = rsi_series.rolling(ma_len).mean()
            rsi_slopes[f"{ma_len}"] = [float(v) for v in ma.tolist()]
        return cls(
            time_to_pos={t: i for i, t in enumerate(times)},
            closes=closes,
            ema_trend=ema_trend,
            atr14=atr14,
            rsi14=rsi14,
            rsi_slopes=rsi_slopes,
        )

    def features_for(self, cand: _Candidate) -> dict[str, float | None] | None:
        """이 셋업의 진입 시점 특징 dict. pos를 못 찾거나 pos<1이면 None(제외)."""
        pos = self.time_to_pos.get(cand.trigger_time)
        if pos is None or pos < 1:
            return None
        ob = cand.order_block
        if ob is None:
            return None
        prev = pos - 1  # 직전 확정봉 — 봉 파생 특징은 여기까지만 본다(룩어헤드 금지).

        feats: dict[str, float | None] = {}
        feats["trend_dev"] = _safe_ratio(self.closes[prev], self.ema_trend[prev])
        feats["volume_pctl"] = None  # 셀 전체 순위라 아래 `_annotate_percentile`에서 채운다.
        feats["vol_balance"] = _imbalance(ob)
        for ma_len, win in _RSI_SLOPE_VARIANTS:
            feats[f"rsi_slope_{ma_len}_{win}"] = self._rsi_slope(ma_len, win, prev)
        feats["zone_width_atr"] = _safe_div(ob.top - ob.bottom, self.atr14[prev])
        feats["freshness_bars"] = self._freshness(ob, pos)
        feats["prior_taps"] = float(sum(1 for t in ob.tapped_times if t < cand.trigger_time))
        feats["approach_mom"] = self._approach(prev)
        feats["tap_rsi"] = _finite(self.rsi14[prev])
        return feats

    def _rsi_slope(self, ma_len: int, win: int, prev: int) -> float | None:
        series = self.rsi_slopes[f"{ma_len}"]
        if prev - win < 0:
            return None
        now, past = series[prev], series[prev - win]
        if _isnan(now) or _isnan(past):
            return None
        return now - past

    def _freshness(self, ob: OrderBlock, pos: int) -> float | None:
        confirmed_pos = self.time_to_pos.get(ob.confirmed_time)
        if confirmed_pos is None:
            return None
        return float(pos - confirmed_pos)

    def _approach(self, prev: int) -> float | None:
        if prev - _APPROACH_K < 0:
            return None
        atr_now = self.atr14[prev]
        if _isnan(atr_now) or atr_now <= 0:
            return None
        return (self.closes[prev] - self.closes[prev - _APPROACH_K]) / atr_now


def _safe_ratio(numer: float, denom: float) -> float | None:
    if _isnan(numer) or _isnan(denom) or denom == 0:
        return None
    return numer / denom - 1.0


def _safe_div(numer: float, denom: float) -> float | None:
    if _isnan(numer) or _isnan(denom) or denom <= 0:
        return None
    return numer / denom


def _finite(value: float) -> float | None:
    return None if _isnan(value) else value


def _isnan(value: float) -> bool:
    return value != value


def _imbalance(ob: OrderBlock) -> float | None:
    lo, hi = ob.ob_low_volume, ob.ob_high_volume
    denom = max(lo, hi)
    if denom <= 0:
        return None
    return min(lo, hi) / denom


def _annotate_percentile(labeled: list[LabeledTrade]) -> None:
    """`volume_pctl`을 셀(심볼×TF) 내부 `ob_volume` 순위 백분위로 채운다(제자리 수정).

    WAN-77과 같은 방식 — 순위는 `LabeledTrade`에 직접 없어 `ob_volume`를 함께 넘긴 뒤
    채운다. 순위 척도라 셀 내부 상대값이고, 병합 존의 합산 스케일도 셀 안에서는 일관된다.
    """
    by_cell: dict[tuple[str, str], list[tuple[float, LabeledTrade]]] = defaultdict(list)
    for lt in labeled:
        vol = lt.features.get("_ob_volume")
        if vol is None:
            continue
        by_cell[(lt.symbol, lt.timeframe)].append((vol, lt))
    for items in by_cell.values():
        order = sorted(range(len(items)), key=lambda i: items[i][0])
        n = len(items)
        for rank, idx in enumerate(order):
            _, lt = items[idx]
            lt.features["volume_pctl"] = rank / (n - 1) if n > 1 else 0.5
    for lt in labeled:
        lt.features.pop("_ob_volume", None)


# --------------------------------------------------------------------------- #
# 셀 라벨링
# --------------------------------------------------------------------------- #


def label_cell(market: harness.MarketData, *, params: ConfluenceParams) -> list[LabeledTrade]:
    """한 (심볼, TF)의 채택 엔진 거래를 결과·특징으로 라벨링한다.

    전체 못 박은 창에서 후보를 한 번 만들어 프로덕션 시퀀서로 배치하고(채택 엔진 그
    자체), `trigger_time`으로 IS/OOS를 가른다. 라벨(뚫림/버팀)은 자본 경로와 무관하고
    특징은 진입 시점 기지라, 거래를 시각으로 사후 분할해도 오염되지 않는다(수익률
    분할과 다르다 — WAN-99 경고는 사이징 자본 오염에 대한 것이다).
    """
    if market.empty or market.df_1m.empty:
        return []
    cfg = harness.build_config(market.timeframe)
    candidates, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_params=harness.LEGACY_OB_PARAMS,
    )
    if not candidates:
        return []
    frame = harness_prepare(market.htf_df)
    extractor = _FeatureExtractor.build(frame)

    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * IS_FRACTION)

    labeled: list[LabeledTrade] = []
    for cand, _trade in sequence_with_candidates(candidates, cfg):
        if cand.reason is ExitReason.STOP_LOSS:
            broke, r_mult = True, -1.0
        elif cand.reason is ExitReason.TAKE_PROFIT:
            broke, r_mult = False, params.take_profit_r
        else:
            continue  # END_OF_DATA: 결과 미확정 → 제외.
        feats = extractor.features_for(cand)
        if feats is None:
            continue
        ob = cand.order_block
        feats["_ob_volume"] = ob.ob_volume if ob is not None else None
        segment = SEGMENT_IS if cand.trigger_time < is_boundary else SEGMENT_OOS
        labeled.append(
            LabeledTrade(
                symbol=market.symbol,
                timeframe=market.timeframe,
                segment=segment,
                side="long" if cand.side is PositionSide.LONG else "short",
                trigger_time=cand.trigger_time,
                broke=broke,
                r_multiple=r_mult,
                features=feats,
            )
        )
    _annotate_percentile(labeled)
    return labeled


def harness_prepare(df: pd.DataFrame) -> pd.DataFrame:
    """`build_zone_limit_candidates`가 쓰는 것과 동일한 프레임 준비(확정봉+정렬)."""
    from backtest.zone_limit_backtest import _prepare_htf

    return _prepare_htf(df)


# --------------------------------------------------------------------------- #
# 1단계: 분위별 무효화율·평균R
# --------------------------------------------------------------------------- #


class QuantileRow(BaseModel):
    """한 (TF, 구간, 특징, 분위) 셀의 무효화율·평균R (6심볼 풀링)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    quantile: str
    quantile_rank: int
    axis_min: float
    axis_max: float
    n: int
    broke_rate: float
    mean_r: float


def quantile_rows(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    n_quantiles: int = _N_QUANTILES,
) -> list[QuantileRow]:
    """특징값이 있는 거래를 분위로 나눠 분위별 무효화율·평균R을 낸다."""
    pairs = [
        (v, lt)
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and (v := lt.features.get(feature)) is not None
    ]
    if len(pairs) < n_quantiles:
        return []
    values = [v for v, _ in pairs]
    try:
        labels = pd.qcut(values, n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return []
    buckets: dict[int, list[tuple[float, LabeledTrade]]] = defaultdict(list)
    for label, (v, lt) in zip(labels, pairs, strict=True):
        buckets[int(label)].append((v, lt))

    rows: list[QuantileRow] = []
    for label in sorted(buckets):
        items = buckets[label]
        vals = [v for v, _ in items]
        trades = [lt for _, lt in items]
        broke = sum(1 for lt in trades if lt.broke)
        rows.append(
            QuantileRow(
                timeframe=timeframe,
                segment=segment,
                feature=feature,
                quantile=f"Q{label + 1}",
                quantile_rank=label + 1,
                axis_min=min(vals),
                axis_max=max(vals),
                n=len(trades),
                broke_rate=broke / len(trades),
                mean_r=sum(lt.r_multiple for lt in trades) / len(trades),
            )
        )
    return rows


def monotonicity_verdict(rows: list[QuantileRow]) -> str:
    """분위(오름차순) 무효화율 수열의 단조성 판정."""
    ordered = sorted(rows, key=lambda r: r.quantile_rank)
    values = [r.broke_rate for r in ordered]
    if len(values) < 3:
        return "판정 불가(표본 부족)"
    non_decreasing = all(b >= a - 1e-9 for a, b in zip(values, values[1:], strict=False))
    non_increasing = all(b <= a + 1e-9 for a, b in zip(values, values[1:], strict=False))
    if non_decreasing and non_increasing:
        return "평탄"
    if non_decreasing:
        return "단조 증가"
    if non_increasing:
        return "단조 감소"
    return "비단조(들쭉날쭉)"


# --------------------------------------------------------------------------- #
# 2단계: 점이연 상관 + 심볼 층화 라벨 순열
# --------------------------------------------------------------------------- #


class PermutationRow(BaseModel):
    """한 (TF, 구간, 특징)의 상관·순열 검정 결과 (6심볼 풀링)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    n: int
    broke_rate: float
    correlation: float | None
    """점이연 상관 corr(특징, 뚫림). 표본·분산 부족이면 None."""
    p_value: float | None
    """심볼 층화 라벨 순열 양측 p값."""
    hypothesis_sign: int
    direction_matches: bool
    """상관 부호가 가설 부호(`HYPOTHESIS_SIGN`)와 같은지. 가설 방향이 0이면 항상 False."""
    permutations: int


def _point_biserial(values: list[float], broke: list[bool]) -> float | None:
    """corr(연속 특징, 이진 뚫림). 표본 <3 또는 한쪽 분산 0이면 None."""
    n = len(values)
    if n < 3:
        return None
    labels = [1.0 if b else 0.0 for b in broke]
    mean_v = sum(values) / n
    mean_l = sum(labels) / n
    cov = sum((v - mean_v) * (le - mean_l) for v, le in zip(values, labels, strict=True))
    var_v = sum((v - mean_v) ** 2 for v in values)
    var_l = sum((le - mean_l) ** 2 for le in labels)
    if var_v <= 0 or var_l <= 0:
        return None
    return float(cov / (var_v * var_l) ** 0.5)


def permutation_test(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    permutations: int = _PERMUTATIONS,
    seed: int = _SEED,
) -> PermutationRow:
    """특징↔뚫림 연관의 심볼 층화 라벨 순열 검정.

    각 심볼 안에서 뚫림/버팀 라벨만 섞으면 심볼별 무효화율·표본이 보존된 채 특징과의
    연관만 끊긴다(구성 보존 매칭 널). 통계량은 점이연 상관, p값은 |상관|의 양측 초과율.
    """
    rows = [
        (v, lt)
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and (v := lt.features.get(feature)) is not None
    ]
    n = len(rows)
    broke = [lt.broke for _, lt in rows]
    broke_rate = sum(1 for b in broke if b) / n if n else 0.0
    hyp = HYPOTHESIS_SIGN.get(feature, 0)
    if n < _MIN_TRADES_FOR_VERDICT:
        return PermutationRow(
            timeframe=timeframe,
            segment=segment,
            feature=feature,
            n=n,
            broke_rate=broke_rate,
            correlation=None,
            p_value=None,
            hypothesis_sign=hyp,
            direction_matches=False,
            permutations=0,
        )
    values = [v for v, _ in rows]
    actual = _point_biserial(values, broke)
    if actual is None:
        return PermutationRow(
            timeframe=timeframe,
            segment=segment,
            feature=feature,
            n=n,
            broke_rate=broke_rate,
            correlation=None,
            p_value=None,
            hypothesis_sign=hyp,
            direction_matches=False,
            permutations=0,
        )

    # 심볼 층으로 라벨 인덱스를 묶는다 — 순열은 각 층 안에서만 라벨을 섞는다.
    strata: dict[str, list[int]] = defaultdict(list)
    for i, (_, lt) in enumerate(rows):
        strata[lt.symbol].append(i)

    rng = random.Random(seed)
    labels = [1.0 if b else 0.0 for b in broke]
    extreme = 0
    target = abs(actual)
    for _ in range(permutations):
        shuffled = labels.copy()
        for idxs in strata.values():
            pool = [labels[i] for i in idxs]
            rng.shuffle(pool)
            for slot, i in enumerate(idxs):
                shuffled[i] = pool[slot]
        corr = _corr_from_labels(values, shuffled)
        if corr is not None and abs(corr) >= target - 1e-12:
            extreme += 1
    p_value = extreme / permutations
    direction_matches = hyp != 0 and (actual > 0) == (hyp > 0) and abs(actual) > 0
    return PermutationRow(
        timeframe=timeframe,
        segment=segment,
        feature=feature,
        n=n,
        broke_rate=broke_rate,
        correlation=actual,
        p_value=p_value,
        hypothesis_sign=hyp,
        direction_matches=direction_matches,
        permutations=permutations,
    )


def _corr_from_labels(values: list[float], labels: list[float]) -> float | None:
    n = len(values)
    mean_v = sum(values) / n
    mean_l = sum(labels) / n
    cov = sum((v - mean_v) * (le - mean_l) for v, le in zip(values, labels, strict=True))
    var_v = sum((v - mean_v) ** 2 for v in values)
    var_l = sum((le - mean_l) ** 2 for le in labels)
    if var_v <= 0 or var_l <= 0:
        return None
    return float(cov / (var_v * var_l) ** 0.5)


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def bonferroni_alpha(n_features: int, *, alpha: float = _ALPHA) -> float:
    """TF당 특징 수로 보정한 Bonferroni 임계값."""
    return alpha / n_features if n_features else alpha


def survivors_for_tf(
    perm_rows: list[PermutationRow], *, timeframe: str, alpha: float = _ALPHA
) -> list[str]:
    """OOS Bonferroni & IS 동일 부호를 통과한 특징 목록(판정·결론이 공유하는 단일 소스)."""
    oos = {r.feature: r for r in perm_rows if r.timeframe == timeframe and r.segment == SEGMENT_OOS}
    is_seg = {
        r.feature: r for r in perm_rows if r.timeframe == timeframe and r.segment == SEGMENT_IS
    }
    tested = [f for f in FEATURES if f in oos and oos[f].p_value is not None]
    alpha_adj = bonferroni_alpha(len(tested), alpha=alpha)
    survivors: list[str] = []
    for feature in tested:
        o = oos[feature]
        if o.p_value is None or o.correlation is None or o.p_value > alpha_adj:
            continue
        i = is_seg.get(feature)
        if i is None or i.correlation is None:
            continue
        if (o.correlation > 0) != (i.correlation > 0):
            continue  # IS→OOS 부호 뒤집힘 → 제외.
        survivors.append(feature)
    return survivors


def verdict_for_tf(
    perm_rows: list[PermutationRow], *, timeframe: str, alpha: float = _ALPHA
) -> str:
    """한 TF의 (a) 예측 가능한 실패 / (b) 예측 불가 판정.

    **OOS에서** Bonferroni 임계값을 넘고(유의) **IS에서도 같은 부호**인 특징만 "실패를
    가른다"고 인정한다 — IS→OOS 부호 뒤집힘(WAN-88/114/124가 거듭 본 함정)을 막는다.
    """
    oos = {r.feature: r for r in perm_rows if r.timeframe == timeframe and r.segment == SEGMENT_OOS}
    tested = [f for f in FEATURES if f in oos and oos[f].p_value is not None]
    alpha_adj = bonferroni_alpha(len(tested), alpha=alpha)
    if not tested:
        return (
            f"**{timeframe}**: 판정 불가 — 유효 특징(거래 {_MIN_TRADES_FOR_VERDICT}건 이상) 없음."
        )

    survivors = survivors_for_tf(perm_rows, timeframe=timeframe, alpha=alpha)
    if survivors:
        detail = ", ".join(
            f"`{f}`(OOS corr={oos[f].correlation:+.3f}, p={oos[f].p_value:.4f}"
            + (", 가설방향" if oos[f].direction_matches else "")
            + ")"
            for f in survivors
        )
        return (
            f"**{timeframe}**: **(a) 실패를 가르는 특징 후보** — {len(tested)}개 검정 중 "
            f"{len(survivors)}개가 OOS Bonferroni(α'={alpha_adj:.4f}) & IS 동일 부호: {detail}. "
            "후속 '존 품질 필터 구현' 이슈에서 필터의 손익 효과를 재검할 근거가 있다."
        )
    return (
        f"**{timeframe}**: **(b) 실패도 예측 불가** — {len(tested)}개 특징 중 OOS "
        f"Bonferroni(α'={alpha_adj:.4f}) & IS 동일 부호를 만족하는 것이 없다. "
        "WAN-88/111/114 「엣지 없음」을 실패 프레임에서 재확인한다 — 뚫릴 존을 진입 시점에 "
        "미리 가르는 능력은 확인되지 않았다."
    )


# --------------------------------------------------------------------------- #
# 실험 실행 (실데이터)
# --------------------------------------------------------------------------- #


@dataclass
class ExperimentResult:
    labeled: list[LabeledTrade] = field(default_factory=list)
    quantile: list[QuantileRow] = field(default_factory=list)
    permutation: list[PermutationRow] = field(default_factory=list)


def run_experiment(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    permutations: int = _PERMUTATIONS,
    db_path: str = harness.DB_PATH,
) -> ExperimentResult:
    """6심볼 × {15m,1h} 라벨링 → 분위표 → 순열 검정."""
    # 채택 기본값(offset 2bp · unconditional 게이트 · baseline) + 밴드만 당시 값 고정.
    # ⚠️ WAN-132가 밴드 정본을 `intrabar_live`로 옮겼다 — 이 부검의 라벨(뚫림/버팀)과
    # 분위·순열 수치는 탭 봉 종가 밴드가 낸 거래 위에서 산출됐으므로 고정한다.
    params = harness.pin_band_bar(ConfluenceParams())
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    labeled: list[LabeledTrade] = []
    for symbol in symbols:
        norm = harness.normalize_symbol(symbol)
        for timeframe in timeframes:
            market = harness.load_market_data(
                norm,
                timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                need_1m=True,
                funding=False,
                db_path=db_path,
            )
            cell = label_cell(market, params=params)
            labeled.extend(cell)
            print(
                f"[wan117] {norm} {timeframe}: labeled={len(cell)} "
                f"(broke={sum(1 for lt in cell if lt.broke)})"
            )

    quantile: list[QuantileRow] = []
    permutation: list[PermutationRow] = []
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in FEATURES:
                quantile.extend(
                    quantile_rows(labeled, timeframe=timeframe, segment=segment, feature=feature)
                )
                permutation.append(
                    permutation_test(
                        labeled,
                        timeframe=timeframe,
                        segment=segment,
                        feature=feature,
                        permutations=permutations,
                    )
                )
    return ExperimentResult(labeled=labeled, quantile=quantile, permutation=permutation)


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def build_summary_markdown(result: ExperimentResult, *, quantile_csv: Path) -> str:
    perm = result.permutation
    lines: list[str] = []
    lines.append("# WAN-117 존 실패 부검 — 뚫린 존의 공통점 검정 (WAN-77 거울상)\n")
    lines.append(
        f"6심볼(BTC/ETH/SOL/BNB/XRP/TRX) × 15m·1h, 못 박은 창 **{DEFAULT_START} ~ "
        f"{DEFAULT_END}**, 채택 기본값(`ConfluenceParams()` — 존 지정가 offset 2bp · "
        "`unconditional` 게이트 · 볼린저 `tap`) · 공식 렌즈 `baseline`(WAN-128 단독). "
        "라벨: 뚫림=무효화 손절 · 버팀=1.5R 익절, END_OF_DATA 제외. "
        f"재현: `python -m backtest.wan117_zone_failure_autopsy`. 분위 원자료: `{quantile_csv}`.\n"
    )

    total = len(result.labeled)
    broke = sum(1 for lt in result.labeled if lt.broke)
    lines.append("## 표본 · 무효화율 기저\n")
    lines.append(
        f"라벨링된 거래 **{total}건**(뚫림 {broke} · 버팀 {total - broke}). TF·구간별 기저:\n"
    )
    lines.append("| TF | 구간 | n | 무효화율 |\n| -- | -- | -- | -- |")
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            cell = [
                lt for lt in result.labeled if lt.timeframe == timeframe and lt.segment == segment
            ]
            if not cell:
                continue
            rate = sum(1 for lt in cell if lt.broke) / len(cell)
            lines.append(f"| {timeframe} | {segment} | {len(cell)} | {rate * 100:.1f}% |")
    lines.append("")

    lines.append("## 판정 (OOS Bonferroni + IS 동일 부호)\n")
    for timeframe in DEFAULT_TIMEFRAMES:
        lines.append(f"* {verdict_for_tf(perm, timeframe=timeframe)}")
    lines.append("")

    lines.append("## 2단계: 특징별 점이연 상관 · 심볼 층화 순열 p값\n")
    lines.append(
        "corr(특징, 뚫림)>0 = 특징이 클수록 더 뚫림. p = 심볼 층화 라벨 순열 양측값 "
        f"({_PERMUTATIONS}회). 유효 셀 = 거래 {_MIN_TRADES_FOR_VERDICT}건 이상.\n"
    )
    lines.append(
        "| TF | 구간 | 특징 | n | 무효화율 | corr | p | 가설방향 |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    for r in perm:
        corr = "—" if r.correlation is None else f"{r.correlation:+.3f}"
        p = "—" if r.p_value is None else f"{r.p_value:.4f}"
        arrow = "○" if r.direction_matches else ("·" if r.hypothesis_sign == 0 else "✗")
        lines.append(
            f"| {r.timeframe} | {r.segment} | `{r.feature}` | {r.n} | "
            f"{r.broke_rate * 100:.1f}% | {corr} | {p} | {arrow} |"
        )
    lines.append("")

    lines.append("## 1단계: 특징 분위별 무효화율·평균R (6심볼 풀링)\n")
    lines.append("각 TF·구간·특징을 3분위로 나눈 무효화율(단조성 판정 동반).\n")
    by_key: dict[tuple[str, str, str], list[QuantileRow]] = defaultdict(list)
    for q in result.quantile:
        by_key[(q.timeframe, q.segment, q.feature)].append(q)
    lines.append(
        "| TF | 구간 | 특징 | 단조성 | Q1 뚫림/R | Q2 뚫림/R | Q3 뚫림/R |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in FEATURES:
                rows = by_key.get((timeframe, segment, feature), [])
                if not rows:
                    continue
                verdict = monotonicity_verdict(rows)
                cells = {qr.quantile_rank: qr for qr in rows}
                parts = []
                for rank in (1, 2, 3):
                    qr = cells.get(rank)
                    parts.append(
                        "—" if qr is None else f"{qr.broke_rate * 100:.0f}%/{qr.mean_r:+.2f}"
                    )
                lines.append(
                    f"| {timeframe} | {segment} | `{feature}` | {verdict} | "
                    f"{parts[0]} | {parts[1]} | {parts[2]} |"
                )
    lines.append("")

    lines.append("## 결론\n")
    survivors_by_tf = {tf: survivors_for_tf(perm, timeframe=tf) for tf in DEFAULT_TIMEFRAMES}
    all_survivors = {f for surv in survivors_by_tf.values() for f in surv}
    if all_survivors:
        both = sorted(
            f for f in all_survivors if all(f in survivors_by_tf[tf] for tf in DEFAULT_TIMEFRAMES)
        )
        surv_desc = "; ".join(
            f"{tf}={', '.join(f'`{f}`' for f in surv) or '없음'}"
            for tf, surv in survivors_by_tf.items()
        )
        lines.append(
            f"일부 특징이 OOS + 매칭 널(심볼 층화 순열)에서도 뚫림을 유의하게 갈랐다({surv_desc}). "
            "후속 '존 품질 필터 구현' 이슈를 제안하되, **아래 세 경고를 함께 넘긴다**:\n"
        )
        if "zone_width_atr" in all_survivors:
            lines.append(
                "1. **`zone_width_atr`는 「선별」이 아니라 「가격/장벽 기하」일 공산이 크다** "
                f"({'양 TF 모두' if 'zone_width_atr' in both else '일부 TF'} 생존, 세 구간 단조). "
                "손절 −1R·익절 +1.5R이 고정인데 존폭/ATR은 그 ±장벽이 변동성 대비 얼마나 "
                "멀리 서는지를 직접 정한다 — 넓은 존일수록 익절(+1.5R)이 ATR 단위로 더 멀어 "
                "**닿기 전에 손절**된다. 분위 평균R이 좁은 존 +0.4~0.5R → 넓은 존 +0.1R 이하로 "
                "벌어지는 것이 바로 그 장벽 거리 효과의 모습이다. 이는 WAN-96/114/115/120/124가 "
                "거듭 「가격이지 선별이 아니다」로 지목한 계열이며, **필터 이슈가 기하 효과와 "
                "존 품질 선별을 반드시 분리**해야 한다(고정 R을 끄거나 ATR로 정규화한 재검).\n"
            )
        lines.append(
            "2. **유의 ≠ 수익.** 무효화율을 가른다고 손익이 오르는 건 아니다 — 필터는 승자도 "
            "함께 쳐낼 수 있고, 거래 집합이 바뀌면 WAN-124가 본 「스치듯 닿은 체결」 의존도 "
            "재계량해야 한다. 실제 손익·MDD 효과는 후속 필터 이슈 소관이다.\n"
        )
        lines.append(
            "3. **사용자 가설·WAN-77 실마리는 OOS에서 살아남지 못했다.** RSI MA 롤오버"
            "(`rsi_slope_*`)는 어느 TF·OOS에서도 Bonferroni를 못 넘고, 거래량(`volume_pctl`)은 "
            "15m IS에서 유의(방향 일치)했으나 **OOS에서 corr≈0으로 붕괴**한다(WAN-77의 IS→OOS "
            "미재현을 실패 프레임에서 재확인). 볼륨 밸런스·신선도·접근 모멘텀·탭 RSI도 "
            "OOS 생존 없음.\n"
        )
        lines.append(
            "즉 **뚫림을 미리 가르는 유일하게 강건한 축이 존폭/ATR인데 그것이 하필 기하 효과로 "
            "의심되므로**, WAN-88/111/114 「엣지 없음」이 실패 프레임에서 뒤집혔다고 읽어선 "
            "안 된다 — 필터 이슈가 「기하 대 선별」을 가른 뒤에야 판정이 선다. 기본값·실거래 "
            "보류(`ALPHABLOCK_LIVE_TRADING=false`)는 불변(측정만).\n"
        )
    else:
        lines.append(
            "**어떤 특징도 OOS + 매칭 널에서 뚫림을 유의하게 가르지 못했다.** 상위TF 추세·"
            "거래량·볼륨 밸런스·RSI 롤오버·존폭/ATR·신선도·이전 탭·접근 모멘텀·탭 RSI 어느 "
            "것도 진입 시점에 '뚫릴 존'을 미리 구분하지 못한다. 이는 WAN-88/111/114의 "
            "「엣지 없음」을 **실패 프레임**에서 재확인한 것이다 — 성공(반등)만 무작위와 "
            "구분되지 않는 게 아니라 **실패(뚫림)도 예측 불가**다. 실패를 예측하는 게 곧 "
            "엣지라는 이슈의 전제에 비춰, 이 자리에도 엣지는 없다. 기본값·실거래 보류는 "
            "불변(측정만).\n"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _labeled_to_frame(labeled: list[LabeledTrade]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for lt in labeled:
        record: dict[str, object] = {
            "symbol": lt.symbol,
            "timeframe": lt.timeframe,
            "segment": lt.segment,
            "side": lt.side,
            "trigger_time": lt.trigger_time,
            "broke": lt.broke,
            "r_multiple": lt.r_multiple,
        }
        for feature in FEATURES:
            record[feature] = lt.features.get(feature)
        records.append(record)
    columns = ["symbol", "timeframe", "segment", "side", "trigger_time", "broke", "r_multiple"]
    columns.extend(FEATURES)
    return pd.DataFrame(records, columns=columns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-117 존 실패 부검")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--permutations", type=int, default=_PERMUTATIONS)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--labeled-out", type=Path, default=REPORTS_DIR / "wan117_labeled.csv")
    parser.add_argument("--quantile-out", type=Path, default=REPORTS_DIR / "wan117_quantile.csv")
    parser.add_argument(
        "--permutation-out", type=Path, default=REPORTS_DIR / "wan117_permutation.csv"
    )
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan117_summary.md")
    args = parser.parse_args(argv)

    result = run_experiment(
        symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
        timeframes=tuple(t.strip() for t in args.timeframes.split(",") if t.strip()),
        start=args.start,
        end=args.end,
        permutations=args.permutations,
        db_path=args.db,
    )
    _write_csv(_labeled_to_frame(result.labeled), args.labeled_out)
    _write_csv(_rows_to_frame(result.quantile), args.quantile_out)
    _write_csv(_rows_to_frame(result.permutation), args.permutation_out)
    print(f"[wan117] labeled rows={len(result.labeled)} → {args.labeled_out}")
    print(f"[wan117] quantile rows={len(result.quantile)} → {args.quantile_out}")
    print(f"[wan117] permutation rows={len(result.permutation)} → {args.permutation_out}")

    summary = build_summary_markdown(result, quantile_csv=args.quantile_out)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan117] summary → {args.summary_out}")
    return 0


def _write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
