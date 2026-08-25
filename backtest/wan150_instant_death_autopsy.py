"""즉사 부검 — 손절을 「즉사」와 「애매 실패」로 갈라 본다 (WAN-150, WAN-117 3분류판).

WAN-117 존 실패 부검이 손절/익절 **2분류**로 11개 특징을 검정해 `zone_width_atr` 하나
빼고 전멸시켰다. 이 모듈은 그 검정의 사각을 메운다: **손절 거래를 「즉사」와 「애매 실패」로
갈라** 3분류로 다시 검정한다.

동기(사용자, 2026-07-21): *"1.5배를 못 가고 손절한 경우가 많아서 … 즉사 거래를 좀 걸러낸다면
수익률이 많이 차이가 날까? 이런 거래를 잘 추려내기 위해서는 뭘 볼 거 같아?"*

물리적 그림이 다르다 — 섞으면 신호가 희석된다:

* **즉사** = 존에 닿자마자 반대로 간다(MFE < 0.5R) → "파는 힘이 살아있는 채로 존에 들어왔다".
* **애매 실패** = 반등은 했는데 힘이 빠져 1.5R 전에 되돌아온다(0.5R ≤ MFE < 1.5R) →
  "수요는 있었으나 부족했다".
* **승자** = 1.5R 익절 도달(TAKE_PROFIT).

## 토대 (사용자 결정 2026-07-29 — §1 라벨을 **오늘 엔진에서 다시 뽑는다**)

옛 `wan117_labeled.csv`는 **병합 존 · 옛 밴드 `tap` · 3종목 · 필터 없음**에서 만들어져
오늘 엔진의 분류가 아니다. 이 모듈은 라벨을 **오늘의 채택 기본값**으로 재생성한다:

* 분리 존(`combine_obs=False`, WAN-149) · `intrabar_live` 밴드(WAN-132) · 존폭 필터
  `max_zone_width_atr=1.28`(WAN-159) · 오프셋 2bp · `unconditional` 게이트 · 고정 1.5R ·
  롱 온리 — 즉 **핀을 하나도 걸지 않은** `ConfluenceParams()` · `OrderBlockParams()`.
* 9종목(BTC·ETH·SOL·BNB·XRP·TRX·DOGE·LINK·LTC) · 못 박은 6년 창(2020-09-15~2026-07-22) ·
  15m·1h·4h · 렌즈 `baseline` 단독.

**측정 전용 — 기본값·토대·실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`)는 건드리지 않는다.**

## §1 — 3분류 재분석 (게이트)

WAN-117과 **같은 자**를 쓴다: 심볼 층화 라벨 순열 2,000회 + Bonferroni + OOS 유의 & IS
동일 부호 + 유효 셀 20건. 새 특징을 만들지 않고 **기존 11개 특징을 그대로** 재검정한다.

종속변수가 둘이다:

* **즉사 대 나머지**(주 검정): 이진 타깃 = 즉사(1) / 애매·승자(0). WAN-117 Bonferroni 자.
* **즉사 대 승자**(실무 문턱 열): {즉사, 승자} 부분집합에서 즉사(1)/승자(0). 손익분기가
  관대하므로(즉사를 승자보다 조금만 더 자주 잡아도 이득) **통계적 유의는 못 넘어도
  무작위(순열)를 넘는지**를 별도로 낸다. ⚠️ 채택 근거로 쓰려면 **OOS에서 무작위를 넘어야**
  한다(단순 비율 차이는 근거가 아니다).

**게이트**: 11개 중 어느 것도 즉사 축에서 (주 검정 Bonferroni 또는 실무 문턱) 무작위를 못
넘으면 **「즉사는 진입 시점에 안 보인다」(b)로 닫는다.** ⚠️ WAN-137 Phase 1의 결함(도달률로
손익 판정) 반복 금지 — "신호가 아예 없다"일 때만 닫고, 약하더라도 방향이 있으면 §2/§3의
기대치를 낮추되 표는 낸다.

## §2 — 손절폭의 절대 크기

`zone_width_atr`이 살아남은 이유가 **장벽 거리**였다면, 그 사촌인 **손절폭(1R) 자체**도
즉사를 가를 수 있다. 두 척도로 낸다: `stop_width_frac`(=|진입−손절|/진입, 가격 대비 %) ·
`stop_width_atr`(=|진입−손절|/ATR). ⚠️ WAN-79 가드(`min_stop_distance_fraction=0.3%`)가
이미 좁은 셋업을 거절하므로 **관측 범위 하한을 명시**한다. ⚠️ 결과가 나와도 「선별」이
아니라 「기하」일 공산이 크다(WAN-117 §1과 같은 자리) — 판정에 명시.

## §3 — RSI-EMA 곡률 (사용자 못박음 후보 2026-07-29)

사용자 가설: **RSI의 지수이동평균이 "위가 볼록(∩)한 채로 하락"하면 즉사 경향**(탭 접근 시
매도 힘이 아직 살아있음 = 반등이 아니라 롤오버). 못박은 사양:

* RSI Wilder **length=14** → 그 위 **EMA span=14**. 스무딩값 `E`에서 연속 특징 2개:
  기울기 `d1 = E_t − E_{t−1}`, 곡률 `d2 = E_t − 2·E_{t−1} + E_{t−2}`. 보조 불리언
  `death_shape = (d1<0 & d2<0)`. 가설 = 즉사일수록 d1·d2가 더 음수.
* **평가 시점 = 탭 직전 확정봉(pos−1)** → E_t/E_{t−1}/E_{t−2} 전부 탭보다 앞선 닫힌 봉이라
  **룩어헤드가 구조적으로 없다**(회귀 테스트가 동작으로 고정). 워밍업 NaN 탭은 **명시적
  제외**(조용한 통과 금지, WAN-123 교훈).
* span 14 하나로 판정(다중 span은 Bonferroni 가족만 키운다 — 사용자 지시).

⚠️ 이슈 §3의 나머지 후보(탭 봉 몸통/ATR·관통 깊이·꼬리 비율)는 **체결 순간의 1분봉
재구성**이 필요해 §1·§2 게이트 뒤로 미룬다(gate 뒤 별도 이슈 소관) — 이 모듈은 사용자가
못박은 **닫힌 봉 RSI-EMA 곡률**을 §3의 정식 후보로 낸다.

## ⚠️ 어느 판정이든

* **「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지** — 그쪽은
  *진입 규칙이 무작위와 구분되는가*를 물었고, 이 모듈은 *이미 진입한 손절 중 즉사를 진입
  시점에 알아보는가*를 묻는다(다른 질문).
* 전부 `baseline`(낙관) 렌즈 위의 값이다. 존폭 축 체결 보수화(`pen_5bp`)는 안 쟀다.
* 심볼 편중 leave-one-out을 병기한다(이 저장소의 플러스는 반복적으로 ETH 하나가 만들었다).

재현: `python -m backtest.wan150_instant_death_autopsy`(검산만: `--checksum`).
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import (
    FEATURES as WAN117_FEATURES,
)
from backtest.wan117_zone_failure_autopsy import (
    HYPOTHESIS_SIGN as _WAN117_HYPOTHESIS_SIGN,
)
from backtest.wan117_zone_failure_autopsy import (
    _FeatureExtractor,
    _isnan,
    _safe_div,
    bonferroni_alpha,
    harness_prepare,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import rsi
from strategy.models import ConfluenceParams, OrderBlockParams

# --------------------------------------------------------------------------- #
# 상수 — 오늘 엔진 좌표 (WAN-182 채택 기본값)
# --------------------------------------------------------------------------- #

#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
DEFAULT_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS
DEFAULT_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

#: MFE(R) 이 값 미만이면 「즉사」. 이슈 §사전계산이 쓴 0.5R 문턱.
DEATH_MFE_THRESHOLD = 0.5

_RSI_LENGTH = 14
_RSI_EMA_SPAN = 14
_MIN_TRADES_FOR_VERDICT = 20
_PERMUTATIONS = 2000
_ALPHA = 0.05
#: 실무 문턱 열(즉사 대 승자)은 Bonferroni가 아니라 순열 α=0.05로만 무작위 초과를 본다.
_PRACTICAL_ALPHA = 0.05
_SEED = 150

REPORTS_DIR = Path("backtest/reports")

# §1: WAN-117 그대로 물려받은 11개 특징(게이트 Bonferroni 가족).
S1_FEATURES: tuple[str, ...] = WAN117_FEATURES
# §2: 손절폭 두 척도.
S2_FEATURES: tuple[str, ...] = ("stop_width_frac", "stop_width_atr")
# §3: 사용자 못박은 RSI-EMA 곡률(닫힌 봉).
S3_FEATURES: tuple[str, ...] = ("rsi_ema_slope", "rsi_ema_curv", "rsi_ema_death_shape")
FEATURES: tuple[str, ...] = (*S1_FEATURES, *S2_FEATURES, *S3_FEATURES)

#: corr(특징, 즉사)>0 이 "특징이 클수록 더 즉사"이므로, 가설 부호는 그 기준으로 적는다.
#: §1 특징은 WAN-117의 뚫림(broke) 가설 부호를 그대로 물려받는다(즉사 ⊂ 뚫림).
HYPOTHESIS_SIGN: dict[str, int] = dict(_WAN117_HYPOTHESIS_SIGN)
HYPOTHESIS_SIGN.update(
    {
        "stop_width_frac": -1,  # 좁은 손절일수록 노이즈에 털려 즉사.
        "stop_width_atr": -1,  # 상동(ATR 정규화).
        "rsi_ema_slope": -1,  # RSI-EMA가 내려가는(음의 d1) 롤오버일수록 즉사.
        "rsi_ema_curv": -1,  # ∩(음의 d2)일수록 즉사.
        "rsi_ema_death_shape": +1,  # death_shape=1(∩+하락)이면 즉사.
    }
)


# --------------------------------------------------------------------------- #
# 3분류 라벨
# --------------------------------------------------------------------------- #


class Label(StrEnum):
    """거래의 3분류 결과."""

    INSTANT_DEATH = "instant_death"
    """즉사 — 손절인데 MFE < 0.5R(존에 닿자마자 반대로)."""
    AMBIGUOUS = "ambiguous"
    """애매 실패 — 손절인데 0.5R ≤ MFE < 1.5R(반등은 했으나 부족)."""
    WINNER = "winner"
    """승자 — 1.5R 익절 도달(TAKE_PROFIT)."""


def classify(reason: ExitReason, mfe_r: float | None) -> Label | None:
    """청산 사유·MFE로 3분류 라벨을 낸다. 분류 불가(END_OF_DATA·MFE 결측)면 None.

    * `TAKE_PROFIT` → 승자.
    * `STOP_LOSS` → MFE < 0.5R 즉사, 아니면 애매 실패. **MFE 결측이면 분류 불가**(None).
    * 그 외(`END_OF_DATA`) → None(결과 미확정).
    """
    if reason is ExitReason.TAKE_PROFIT:
        return Label.WINNER
    if reason is ExitReason.STOP_LOSS:
        if mfe_r is None or _isnan(mfe_r):
            return None
        return Label.INSTANT_DEATH if mfe_r < DEATH_MFE_THRESHOLD else Label.AMBIGUOUS
    return None


@dataclass(frozen=True)
class LabeledTrade:
    """3분류 라벨 + 진입 시점 특징이 붙은 한 거래."""

    symbol: str
    timeframe: str
    segment: str
    side: str
    trigger_time: int
    label: Label
    mfe_r: float | None
    r_multiple: float
    features: dict[str, float | None]

    @property
    def is_death(self) -> bool:
        return self.label is Label.INSTANT_DEATH

    @property
    def is_winner(self) -> bool:
        return self.label is Label.WINNER


# --------------------------------------------------------------------------- #
# 특징 추출 (WAN-117 11개 + §2 손절폭 + §3 RSI-EMA 곡률)
# --------------------------------------------------------------------------- #


@dataclass
class _Wan150Extractor:
    """WAN-117 `_FeatureExtractor`(11개 특징)를 감싸고 §2·§3 특징을 얹는다.

    RSI-EMA는 RSI(14) 시리즈에 `ewm(span=14, adjust=False)`(= Pine `ta.ema` 시드 규칙,
    `strategy.indicators.ema`와 동일)을 씌운 값이다. §2·§3 모두 **탭 직전 확정봉(pos−1)**
    까지만 본다 — 봉 파생 특징의 룩어헤드를 WAN-117과 같은 규칙으로 막는다.
    """

    base: _FeatureExtractor
    rsi_ema: list[float]

    @classmethod
    def build(cls, frame: pd.DataFrame) -> _Wan150Extractor:
        base = _FeatureExtractor.build(frame)
        rsi_series = rsi(frame, length=_RSI_LENGTH)
        rsi_ema = rsi_series.ewm(span=_RSI_EMA_SPAN, adjust=False).mean()
        return cls(base=base, rsi_ema=[float(v) for v in rsi_ema.tolist()])

    def features_for(self, cand: _Candidate) -> dict[str, float | None] | None:
        feats = self.base.features_for(cand)
        if feats is None:
            return None
        pos = self.base.time_to_pos.get(cand.trigger_time)
        if pos is None or pos < 1:
            return None
        prev = pos - 1

        # §2 — 손절폭(1R) 절대 크기. 진입가·손절가는 존 확정 시점에 이미 정해져 있다.
        stop_dist = abs(cand.entry_price - cand.stop_price)
        feats["stop_width_frac"] = stop_dist / cand.entry_price if cand.entry_price > 0 else None
        feats["stop_width_atr"] = _safe_div(stop_dist, self.base.atr14[prev])

        # §3 — RSI-EMA 곡률(닫힌 봉 pos−1/pos−2/pos−3 → prev/prev−1/prev−2).
        feats.update(self._rsi_ema_shape(prev))
        return feats

    def _rsi_ema_shape(self, prev: int) -> dict[str, float | None]:
        e = self.rsi_ema
        if prev - 2 < 0 or any(_isnan(e[prev - k]) for k in (0, 1, 2)):
            return {"rsi_ema_slope": None, "rsi_ema_curv": None, "rsi_ema_death_shape": None}
        d1 = e[prev] - e[prev - 1]
        d2 = e[prev] - 2.0 * e[prev - 1] + e[prev - 2]
        return {
            "rsi_ema_slope": d1,
            "rsi_ema_curv": d2,
            "rsi_ema_death_shape": 1.0 if (d1 < 0.0 and d2 < 0.0) else 0.0,
        }


def _annotate_percentile(labeled: list[LabeledTrade]) -> None:
    """`volume_pctl`을 셀(심볼×TF) 내부 `ob_volume` 순위 백분위로 채운다(WAN-117과 동일)."""
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
# 셀 라벨링 (오늘 엔진 — 핀 없음)
# --------------------------------------------------------------------------- #


@dataclass
class LabelStats:
    """라벨링 진행·제외 카운트(검산·투명성용)."""

    sequenced: int = 0
    """시퀀서가 낸 거래 수(= 채택 엔진 num_trades와 일치해야 함)."""
    end_of_data: int = 0
    mfe_missing: int = 0
    feature_missing: int = 0
    labeled: int = 0


def label_cell(
    market: harness.MarketData,
    *,
    params: ConfluenceParams,
    order_block_params: OrderBlockParams,
    stats: LabelStats | None = None,
) -> list[LabeledTrade]:
    """한 (심볼, TF)의 **오늘 엔진** 거래를 3분류·특징으로 라벨링한다.

    WAN-117 `label_cell`과 골격이 같되 두 곳이 다르다: (1) `order_block_params`를 인자로
    받아 **오늘 기본값(`combine_obs=False`)** 을 쓴다(WAN-117은 `LEGACY_OB_PARAMS` 고정),
    (2) 라벨이 뚫림/버팀 2분류가 아니라 **즉사/애매/승자 3분류**(MFE 기반)다.
    """
    st = stats if stats is not None else LabelStats()
    if market.empty or market.df_1m.empty:
        return []
    cfg = harness.legacy_build_config(market.timeframe)
    candidates, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_params=order_block_params,
    )
    if not candidates:
        return []
    frame = harness_prepare(market.htf_df)
    extractor = _Wan150Extractor.build(frame)

    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * IS_FRACTION)

    labeled: list[LabeledTrade] = []
    for cand, _trade in sequence_with_candidates(candidates, cfg):
        st.sequenced += 1
        label = classify(cand.reason, cand.mfe_r)
        if label is None:
            if cand.reason is ExitReason.STOP_LOSS:
                st.mfe_missing += 1
            else:
                st.end_of_data += 1
            continue
        feats = extractor.features_for(cand)
        if feats is None:
            st.feature_missing += 1
            continue
        ob = cand.order_block
        feats["_ob_volume"] = ob.ob_volume if ob is not None else None
        segment = SEGMENT_IS if cand.trigger_time < is_boundary else SEGMENT_OOS
        r_mult = params.take_profit_r if label is Label.WINNER else -1.0
        labeled.append(
            LabeledTrade(
                symbol=market.symbol,
                timeframe=market.timeframe,
                segment=segment,
                side="long" if cand.side is PositionSide.LONG else "short",
                trigger_time=cand.trigger_time,
                label=label,
                mfe_r=cand.mfe_r,
                r_multiple=r_mult,
                features=feats,
            )
        )
    st.labeled += len(labeled)
    _annotate_percentile(labeled)
    return labeled


# --------------------------------------------------------------------------- #
# 1단계: 3분류 층화표
# --------------------------------------------------------------------------- #


class QuantileRow(BaseModel):
    """한 (TF, 구간, 특징, 분위) 셀의 3분류 비율 (심볼 풀링)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    quantile: str
    quantile_rank: int
    axis_min: float
    axis_max: float
    n: int
    death_rate: float
    ambiguous_rate: float
    winner_rate: float
    mean_r: float


def quantile_rows(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    n_quantiles: int = 3,
) -> list[QuantileRow]:
    """특징값이 있는 거래를 분위로 나눠 분위별 3분류 비율을 낸다."""
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
        n = len(trades)
        death = sum(1 for lt in trades if lt.label is Label.INSTANT_DEATH)
        amb = sum(1 for lt in trades if lt.label is Label.AMBIGUOUS)
        win = sum(1 for lt in trades if lt.label is Label.WINNER)
        rows.append(
            QuantileRow(
                timeframe=timeframe,
                segment=segment,
                feature=feature,
                quantile=f"Q{label + 1}",
                quantile_rank=label + 1,
                axis_min=min(vals),
                axis_max=max(vals),
                n=n,
                death_rate=death / n,
                ambiguous_rate=amb / n,
                winner_rate=win / n,
                mean_r=sum(lt.r_multiple for lt in trades) / n,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 2단계: 점이연 상관 + 심볼 층화 라벨 순열 (즉사 축)
# --------------------------------------------------------------------------- #


def _corr(values: list[float], target: list[float]) -> float | None:
    """corr(연속 특징, 이진 타깃). 표본<3 또는 한쪽 분산 0이면 None."""
    n = len(values)
    if n < 3:
        return None
    mean_v = sum(values) / n
    mean_t = sum(target) / n
    cov = sum((v - mean_v) * (t - mean_t) for v, t in zip(values, target, strict=True))
    var_v = sum((v - mean_v) ** 2 for v in values)
    var_t = sum((t - mean_t) ** 2 for t in target)
    if var_v <= 0 or var_t <= 0:
        return None
    return float(cov / (var_v * var_t) ** 0.5)


#: 축 이름 → (부분집합 술어, 양성 술어). 부분집합에 속하는 거래만 검정하고, 양성=타깃 1.
Axis = Callable[[LabeledTrade], bool]
_DEATH_VS_REST: tuple[Axis, Axis] = (lambda lt: True, lambda lt: lt.is_death)
_DEATH_VS_WINNER: tuple[Axis, Axis] = (
    lambda lt: lt.is_death or lt.is_winner,
    lambda lt: lt.is_death,
)


class PermutationRow(BaseModel):
    """한 (TF, 구간, 특징, 축)의 상관·순열 검정 결과 (심볼 풀링)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    axis: str
    """`death_vs_rest`(주 검정) 또는 `death_vs_winner`(실무 문턱)."""
    n: int
    positive_rate: float
    """부분집합 안 양성(즉사) 비율."""
    correlation: float | None
    p_value: float | None
    hypothesis_sign: int
    direction_matches: bool
    permutations: int


def permutation_test(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    axis: str,
    subset: Axis,
    positive: Axis,
    permutations: int = _PERMUTATIONS,
    seed: int = _SEED,
    hypothesis_sign: int | None = None,
) -> PermutationRow:
    """특징↔즉사 연관의 심볼 층화 라벨 순열 검정(WAN-117 자, 타깃만 3분류로 일반화).

    `hypothesis_sign`을 주면 모듈 전역 `HYPOTHESIS_SIGN` 조회 대신 그 값을 쓴다 — WAN-209처럼
    이 모듈에 없는 특징을 재사용 검정할 때만 쓴다. `None`(기본)이면 기존 동작과 비트 동일.
    """
    rows = [
        (v, lt)
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and subset(lt)
        and (v := lt.features.get(feature)) is not None
    ]
    n = len(rows)
    hyp = HYPOTHESIS_SIGN.get(feature, 0) if hypothesis_sign is None else hypothesis_sign
    target = [1.0 if positive(lt) else 0.0 for _, lt in rows]
    positive_rate = sum(target) / n if n else 0.0

    def _null_row() -> PermutationRow:
        return PermutationRow(
            timeframe=timeframe,
            segment=segment,
            feature=feature,
            axis=axis,
            n=n,
            positive_rate=positive_rate,
            correlation=None,
            p_value=None,
            hypothesis_sign=hyp,
            direction_matches=False,
            permutations=0,
        )

    if n < _MIN_TRADES_FOR_VERDICT:
        return _null_row()
    values = [v for v, _ in rows]
    actual = _corr(values, target)
    if actual is None:
        return _null_row()

    strata: dict[str, list[int]] = defaultdict(list)
    for i, (_, lt) in enumerate(rows):
        strata[lt.symbol].append(i)

    rng = random.Random(seed)
    extreme = 0
    goal = abs(actual)
    for _ in range(permutations):
        shuffled = target.copy()
        for idxs in strata.values():
            pool = [target[i] for i in idxs]
            rng.shuffle(pool)
            for slot, i in enumerate(idxs):
                shuffled[i] = pool[slot]
        corr = _corr(values, shuffled)
        if corr is not None and abs(corr) >= goal - 1e-12:
            extreme += 1
    direction_matches = hyp != 0 and (actual > 0) == (hyp > 0) and abs(actual) > 0
    return PermutationRow(
        timeframe=timeframe,
        segment=segment,
        feature=feature,
        axis=axis,
        n=n,
        positive_rate=positive_rate,
        correlation=actual,
        p_value=extreme / permutations,
        hypothesis_sign=hyp,
        direction_matches=direction_matches,
        permutations=permutations,
    )


# --------------------------------------------------------------------------- #
# leave-one-out (심볼 편중)
# --------------------------------------------------------------------------- #


class LeaveOneOutRow(BaseModel):
    """한 특징·축·TF·구간의 심볼 제외 상관(부호 안정성)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    axis: str
    excluded_symbol: str
    n: int
    correlation: float | None


def leave_one_out(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    axis: str,
    subset: Axis,
    positive: Axis,
    symbols: Sequence[str],
) -> list[LeaveOneOutRow]:
    """한 특징을 심볼 하나씩 빼가며 상관 부호가 유지되는지(ETH 편중 진단)."""
    out: list[LeaveOneOutRow] = []
    for excluded in symbols:
        rows = [
            (v, lt)
            for lt in labeled
            if lt.timeframe == timeframe
            and lt.segment == segment
            and lt.symbol != excluded
            and subset(lt)
            and (v := lt.features.get(feature)) is not None
        ]
        if len(rows) < _MIN_TRADES_FOR_VERDICT:
            out.append(
                LeaveOneOutRow(
                    timeframe=timeframe,
                    segment=segment,
                    feature=feature,
                    axis=axis,
                    excluded_symbol=excluded,
                    n=len(rows),
                    correlation=None,
                )
            )
            continue
        values = [v for v, _ in rows]
        target = [1.0 if positive(lt) else 0.0 for _, lt in rows]
        out.append(
            LeaveOneOutRow(
                timeframe=timeframe,
                segment=segment,
                feature=feature,
                axis=axis,
                excluded_symbol=excluded,
                n=len(rows),
                correlation=_corr(values, target),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _rows_by_key(perm: list[PermutationRow]) -> dict[tuple[str, str, str, str], PermutationRow]:
    return {(r.timeframe, r.segment, r.feature, r.axis): r for r in perm}


def s1_survivors(
    perm: list[PermutationRow], *, timeframe: str, alpha: float = _ALPHA
) -> dict[str, list[str]]:
    """§1(11개 특징) 즉사 축 생존자.

    두 축을 따로 판정한다:
    * `death_vs_rest`: OOS Bonferroni(α/11) & IS 동일 부호.
    * `death_vs_winner`(실무): OOS 순열 p<0.05 & IS 동일 부호(Bonferroni 아님).
    """
    by = _rows_by_key(perm)
    tested = [
        f
        for f in S1_FEATURES
        if (r := by.get((timeframe, SEGMENT_OOS, f, "death_vs_rest"))) is not None
        and r.p_value is not None
    ]
    alpha_adj = bonferroni_alpha(len(tested), alpha=alpha)

    def _passes(feature: str, axis: str, thresh: float) -> bool:
        o = by.get((timeframe, SEGMENT_OOS, feature, axis))
        i = by.get((timeframe, SEGMENT_IS, feature, axis))
        if o is None or o.p_value is None or o.correlation is None or o.p_value > thresh:
            return False
        if i is None or i.correlation is None:
            return False
        return (o.correlation > 0) == (i.correlation > 0)

    return {
        "death_vs_rest": [f for f in tested if _passes(f, "death_vs_rest", alpha_adj)],
        "death_vs_winner": [f for f in tested if _passes(f, "death_vs_winner", _PRACTICAL_ALPHA)],
    }


def s1_gate_verdict(perm: list[PermutationRow], *, timeframe: str) -> tuple[str, str]:
    """§1 게이트: 어느 특징이든 즉사 축(주 검정 또는 실무 문턱)에서 무작위를 넘는가.

    반환 `(verdict_code, 문장)`. verdict_code ∈ {"a", "b"}.
    """
    surv = s1_survivors(perm, timeframe=timeframe)
    rest, prac = surv["death_vs_rest"], surv["death_vs_winner"]
    by = _rows_by_key(perm)
    tested = [
        f
        for f in S1_FEATURES
        if (r := by.get((timeframe, SEGMENT_OOS, f, "death_vs_rest"))) is not None
        and r.p_value is not None
    ]
    if not tested:
        return (
            "b",
            f"**{timeframe}**: 판정 불가 — 유효 특징(거래 {_MIN_TRADES_FOR_VERDICT}건 이상) 없음.",
        )
    if rest or prac:
        parts = []
        if rest:
            parts.append("주 검정 Bonferroni 생존 " + ", ".join(f"`{f}`" for f in rest))
        if prac:
            parts.append(
                "실무 문턱(즉사 대 승자, OOS 순열 p<0.05) 생존 " + ", ".join(f"`{f}`" for f in prac)
            )
        return (
            "a",
            f"**{timeframe}**: **(a) 즉사 축에서 무작위를 넘는 특징 있음** — {'; '.join(parts)}. "
            "§2/§3의 손익 효과를 재검할 근거가 있다(단 「선별」 대 「기하/가격」은 미분리).",
        )
    return (
        "b",
        f"**{timeframe}**: **(b) 즉사는 진입 시점에 안 보인다** — {len(tested)}개 특징 중 "
        "어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 "
        "못한다. 「손절은 진입 시점에 예측되지 않는다」를 즉사 축에서 확인한다"
        "(WAN-117 2분류의 사각을 메움).",
    )


# --------------------------------------------------------------------------- #
# 실험 실행
# --------------------------------------------------------------------------- #


@dataclass
class ExperimentResult:
    labeled: list[LabeledTrade] = field(default_factory=list)
    quantile: list[QuantileRow] = field(default_factory=list)
    permutation: list[PermutationRow] = field(default_factory=list)
    leave_one_out: list[LeaveOneOutRow] = field(default_factory=list)
    stats: dict[tuple[str, str], LabelStats] = field(default_factory=dict)


_AXES: tuple[tuple[str, Axis, Axis], ...] = (
    ("death_vs_rest", _DEATH_VS_REST[0], _DEATH_VS_REST[1]),
    ("death_vs_winner", _DEATH_VS_WINNER[0], _DEATH_VS_WINNER[1]),
)


def run_experiment(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    permutations: int = _PERMUTATIONS,
    db_path: str = harness.DB_PATH,
) -> ExperimentResult:
    """오늘 엔진 라벨링 → 3분류 층화표 → 즉사 축 순열 검정 → leave-one-out."""
    # 핀을 하나도 걸지 않는다 — 오늘의 채택 기본값 그대로(intrabar_live · 필터 1.28 · 분리 존).
    params = ConfluenceParams()
    ob_params = OrderBlockParams()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    labeled: list[LabeledTrade] = []
    stats: dict[tuple[str, str], LabelStats] = {}
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
            st = LabelStats()
            cell = label_cell(market, params=params, order_block_params=ob_params, stats=st)
            labeled.extend(cell)
            stats[(norm, timeframe)] = st
            deaths = sum(1 for lt in cell if lt.is_death)
            print(
                f"[wan150] {norm} {timeframe}: labeled={len(cell)} "
                f"(death={deaths} seq={st.sequenced} eod={st.end_of_data} "
                f"mfe_missing={st.mfe_missing})"
            )

    quantile: list[QuantileRow] = []
    permutation: list[PermutationRow] = []
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in FEATURES:
                quantile.extend(
                    quantile_rows(labeled, timeframe=timeframe, segment=segment, feature=feature)
                )
                for axis, subset, positive in _AXES:
                    permutation.append(
                        permutation_test(
                            labeled,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            axis=axis,
                            subset=subset,
                            positive=positive,
                            permutations=permutations,
                        )
                    )

    # leave-one-out: 즉사 축 두 축에서 무작위를 넘은 생존자에 대해서만(비용 절약).
    norm_symbols = tuple(harness.normalize_symbol(s) for s in symbols)
    loo: list[LeaveOneOutRow] = []
    for timeframe in timeframes:
        surv = s1_survivors(permutation, timeframe=timeframe)
        want: dict[str, set[str]] = defaultdict(set)
        for axis, feats in surv.items():
            for f in feats:
                want[axis].add(f)
        for axis, subset, positive in _AXES:
            for feature in sorted(want.get(axis, set())):
                for segment in (SEGMENT_IS, SEGMENT_OOS):
                    loo.extend(
                        leave_one_out(
                            labeled,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            axis=axis,
                            subset=subset,
                            positive=positive,
                            symbols=norm_symbols,
                        )
                    )

    return ExperimentResult(
        labeled=labeled,
        quantile=quantile,
        permutation=permutation,
        leave_one_out=loo,
        stats=stats,
    )


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _fmt_corr(r: PermutationRow | None) -> str:
    if r is None or r.correlation is None:
        return "—"
    return f"{r.correlation:+.3f}"


def _fmt_p(r: PermutationRow | None) -> str:
    if r is None or r.p_value is None:
        return "—"
    return f"{r.p_value:.4f}"


def build_summary_markdown(result: ExperimentResult, *, quantile_csv: Path) -> str:
    perm = result.permutation
    by = _rows_by_key(perm)
    lines: list[str] = []
    lines.append(
        "# WAN-150 즉사 부검 — 손절을 「즉사」와 「애매 실패」로 갈라 본다 (WAN-117 3분류판)\n"
    )
    lines.append(
        f"9종목 × {', '.join(DEFAULT_TIMEFRAMES)}, 못 박은 6년 창 **{DEFAULT_START} ~ "
        f"{DEFAULT_END}**, 오늘의 채택 기본값(`ConfluenceParams()` · `OrderBlockParams()` — "
        "존 지정가 offset 2bp · `intrabar_live` 밴드 · `unconditional` 게이트 · 존폭 필터 "
        "1.28 · 분리 존 · 고정 1.5R · 롱 온리) · 공식 렌즈 `baseline`(WAN-128 단독). "
        f"3분류: 승자=1.5R 익절 · 즉사=손절 & MFE<{DEATH_MFE_THRESHOLD}R · 애매=손절 & "
        f"{DEATH_MFE_THRESHOLD}R≤MFE. END_OF_DATA·MFE 결측 제외. "
        f"재현: `python -m backtest.wan150_instant_death_autopsy`. 분위 원자료: `{quantile_csv}`.\n"
    )

    # 라벨 재생성 검산.
    total = len(result.labeled)
    n_death = sum(1 for lt in result.labeled if lt.label is Label.INSTANT_DEATH)
    n_amb = sum(1 for lt in result.labeled if lt.label is Label.AMBIGUOUS)
    n_win = sum(1 for lt in result.labeled if lt.label is Label.WINNER)
    seq_total = sum(s.sequenced for s in result.stats.values())
    eod_total = sum(s.end_of_data for s in result.stats.values())
    mfe_missing = sum(s.mfe_missing for s in result.stats.values())
    feat_missing = sum(s.feature_missing for s in result.stats.values())
    lines.append("## §0 라벨 재생성 검산 (오늘 엔진)\n")
    lines.append(
        f"라벨링된 거래 **{total}건** = 즉사 {n_death} · 애매 {n_amb} · 승자 {n_win}. "
        f"시퀀서 거래 {seq_total}건 = 라벨 {total} + END_OF_DATA {eod_total} + MFE결측 "
        f"{mfe_missing} + 특징결측 {feat_missing}. **`sequenced`는 채택 엔진(인자 없는 "
        "`backtest.run`)의 num_trades와 일치해야 한다**(검산: `--checksum`).\n"
    )
    lines.append("| TF | 구간 | n | 즉사% | 애매% | 승자% |\n| -- | -- | -- | -- | -- | -- |")
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            cell = [
                lt for lt in result.labeled if lt.timeframe == timeframe and lt.segment == segment
            ]
            if not cell:
                continue
            n = len(cell)
            d = sum(1 for lt in cell if lt.label is Label.INSTANT_DEATH)
            a = sum(1 for lt in cell if lt.label is Label.AMBIGUOUS)
            w = sum(1 for lt in cell if lt.label is Label.WINNER)
            lines.append(
                f"| {timeframe} | {segment} | {n} | {d / n * 100:.1f}% | "
                f"{a / n * 100:.1f}% | {w / n * 100:.1f}% |"
            )
    lines.append("")

    # §1 게이트.
    lines.append("## §1 게이트 판정 — 즉사가 진입 시점에 보이는가\n")
    gate_codes: dict[str, str] = {}
    for timeframe in DEFAULT_TIMEFRAMES:
        code, sentence = s1_gate_verdict(perm, timeframe=timeframe)
        gate_codes[timeframe] = code
        lines.append(f"* {sentence}")
    all_b = all(c == "b" for c in gate_codes.values())
    lines.append("")
    if all_b:
        lines.append(
            "📌 **종합 (b): 즉사는 진입 시점에 안 보인다.** 모든 작업 TF에서 11개 특징 중 어느 "
            "것도 즉사 축에서 무작위를 넘지 못했다. WAN-117이 2분류로 뭉뚱그려 놓쳤을 가능성을 "
            "3분류로 공정하게 닫는다 — **「손절은 진입 시점에 예측되지 않는다」가 즉사 축에서도 "
            "성립한다.** §2·§3은 참고로 병기하되(아래) 채택 근거는 게이트에서 이미 닫혔다.\n"
        )
    else:
        lines.append(
            "📌 **일부 TF에서 즉사 축이 무작위를 넘었다(a).** §2·§3에서 손익·기하를 재검할 "
            "근거가 있으나, ⚠️ **「선별」과 「기하/가격」은 이 표가 못 가른다**(WAN-117 §1과 "
            "같은 자리) — 채택은 후속 이슈(사용자 결정)의 몫이다.\n"
        )

    # §1 특징별 표 (두 축 병기).
    lines.append("## §1 — 11개 특징 × 즉사 축 두 검정 (심볼 층화 순열, 2000회)\n")
    lines.append(
        "corr(특징, 즉사)>0 = 특징이 클수록 더 즉사. `주검정` = 즉사 대 나머지(Bonferroni 자), "
        "`실무` = 즉사 대 승자(부분집합, α=0.05 무작위 초과). 유효 셀 = 거래 20건 이상. "
        "가설방향 ○=일치.\n"
    )
    lines.append(_feature_table(by, S1_FEATURES))
    lines.append("")

    # §2.
    lines.append("## §2 — 손절폭(1R) 절대 크기\n")
    lines.append(
        "`stop_width_frac` = |진입−손절|/진입(가격 대비) · `stop_width_atr` = |진입−손절|/ATR. "
        "⚠️ WAN-79 가드(`min_stop_distance_fraction=0.3%`)가 이미 좁은 셋업을 거절하므로 "
        "관측 하한이 잘려 있다(아래 하한 참고). ⚠️ 살아남아도 「선별」이 아니라 「기하」일 "
        "공산이 크다.\n"
    )
    lines.append(_stop_width_floor_note(result.labeled))
    lines.append(_feature_table(by, S2_FEATURES))
    lines.append("")

    # §3.
    lines.append("## §3 — RSI-EMA 곡률 (사용자 못박은 후보 · 닫힌 봉 · 룩어헤드 없음)\n")
    lines.append(
        "RSI(14) 위 EMA(span14)의 기울기 `d1`·곡률 `d2`(탭 직전 확정봉). "
        "`death_shape=1`은 ∩(d2<0)+하락(d1<0). 가설: 즉사일수록 d1·d2가 음수(롤오버). "
        "⚠️ span 14 하나로 판정(다중 span은 Bonferroni 가족만 키운다). 회귀 테스트가 "
        "룩어헤드 없음을 동작으로 고정한다.\n"
    )
    lines.append(_feature_table(by, S3_FEATURES))
    lines.append("")

    # leave-one-out.
    if result.leave_one_out:
        lines.append("## leave-one-out (심볼 편중 진단 — 생존자만)\n")
        lines.append(
            "생존 특징을 심볼 하나씩 빼가며 OOS 상관 부호가 유지되는지. 이 저장소의 플러스는 "
            "반복적으로 ETH 하나가 만들었다.\n"
        )
        lines.append(
            "| TF | 특징 | 축 | 구간 | 제외 | n | corr |\n| -- | -- | -- | -- | -- | -- | -- |"
        )
        for r in result.leave_one_out:
            corr = "—" if r.correlation is None else f"{r.correlation:+.3f}"
            lines.append(
                f"| {r.timeframe} | `{r.feature}` | {r.axis} | {r.segment} | "
                f"{r.excluded_symbol.split('/')[0]} | {r.n} | {corr} |"
            )
        lines.append("")

    # 1단계 분위표.
    lines.append("## 1단계: 특징 분위별 3분류 비율 (심볼 풀링)\n")
    lines.append("각 TF·구간·특징을 3분위로 나눈 즉사%/애매%/승자% (Q1<Q2<Q3).\n")
    lines.append(
        "| TF | 구간 | 특징 | Q1 즉사/애매/승 | Q2 | Q3 |\n| -- | -- | -- | -- | -- | -- |"
    )
    by_q: dict[tuple[str, str, str], list[QuantileRow]] = defaultdict(list)
    for q in result.quantile:
        by_q[(q.timeframe, q.segment, q.feature)].append(q)
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in FEATURES:
                rows = by_q.get((timeframe, segment, feature), [])
                if not rows:
                    continue
                cells = {qr.quantile_rank: qr for qr in rows}
                parts = []
                for rank in (1, 2, 3):
                    qr = cells.get(rank)
                    if qr is None:
                        parts.append("—")
                    else:
                        parts.append(
                            f"{qr.death_rate * 100:.0f}/"
                            f"{qr.ambiguous_rate * 100:.0f}/"
                            f"{qr.winner_rate * 100:.0f}"
                        )
                row = f"| {timeframe} | {segment} | `{feature}` | "
                row += f"{parts[0]} | {parts[1]} | {parts[2]} |"
                lines.append(row)
    lines.append("")

    lines.append("## ⚠️ 인용 경고\n")
    lines.append(
        "* **「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지** — 다른 "
        "질문(*이미 진입한 손절 중 즉사를 진입 시점에 알아보는가*)이다.\n"
        "* 전부 `baseline`(낙관) 렌즈 위의 값 · 존폭 축 체결 보수화(`pen_5bp`)는 안 쟀다.\n"
        "* §2/§3이 무작위를 넘어도 「선별」이 아니라 「기하/가격」일 공산이 크다"
        "(WAN-96/114/115/120/124/117).\n"
        "* 기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지(측정 전용).\n"
    )
    return "\n".join(lines)


def _feature_table(
    by: dict[tuple[str, str, str, str], PermutationRow], features: Sequence[str]
) -> str:
    header = (
        "| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    rows = [header]
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in features:
                rest = by.get((timeframe, segment, feature, "death_vs_rest"))
                prac = by.get((timeframe, segment, feature, "death_vs_winner"))
                if rest is None:
                    continue
                arrow = (
                    "○" if rest.direction_matches else ("·" if rest.hypothesis_sign == 0 else "✗")
                )
                rows.append(
                    f"| {timeframe} | {segment} | `{feature}` | {rest.n} | "
                    f"{rest.positive_rate * 100:.1f}% | {_fmt_corr(rest)} | {_fmt_p(rest)} | "
                    f"{_fmt_corr(prac)} | {_fmt_p(prac)} | {arrow} |"
                )
    return "\n".join(rows)


def _stop_width_floor_note(labeled: list[LabeledTrade]) -> str:
    fracs = [v for lt in labeled if (v := lt.features.get("stop_width_frac")) is not None]
    if not fracs:
        return "관측된 손절폭 없음.\n"
    lo = min(fracs)
    below_guard = sum(1 for v in fracs if v < 0.003)
    return (
        f"관측 손절폭(가격 대비) 하한 **{lo * 100:.3f}%** · 가드 0.3% 미만 {below_guard}건 "
        f"({below_guard / len(fracs) * 100:.1f}%). 가드가 좁은 손절을 이미 잘라 관측 범위가 "
        "0.3% 근방에서 절단돼 있음을 전제로 읽을 것.\n"
    )


# --------------------------------------------------------------------------- #
# 검산 — sequenced == 채택 엔진 num_trades
# --------------------------------------------------------------------------- #


def checksum(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> tuple[int, int, bool]:
    """한 셀의 시퀀서 거래 수 ≡ 채택 엔진 `run_once` num_trades 를 확인한다.

    반환 `(sequenced, production_num_trades, matches)`. 라벨링이 프로덕션 시퀀서를 그대로
    쓰므로 두 값은 일치해야 한다(라벨 재생성이 채택 엔진과 정합함의 독립 증거).
    """
    norm = harness.normalize_symbol(symbol)
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    market = harness.load_market_data(
        norm,
        timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        need_1m=True,
        funding=False,
        db_path=db_path,
    )
    st = LabelStats()
    label_cell(market, params=ConfluenceParams(), order_block_params=OrderBlockParams(), stats=st)
    outcome = harness.run_once(
        market,
        params=harness.pin_invalidation_cancel(ConfluenceParams()),
        cfg=harness.legacy_build_config(timeframe),
    )
    prod = outcome.result.metrics.num_trades
    return st.sequenced, prod, st.sequenced == prod


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
            "label": lt.label.value,
            "mfe_r": lt.mfe_r,
            "r_multiple": lt.r_multiple,
        }
        for feature in FEATURES:
            record[feature] = lt.features.get(feature)
        records.append(record)
    columns = [
        "symbol",
        "timeframe",
        "segment",
        "side",
        "trigger_time",
        "label",
        "mfe_r",
        "r_multiple",
    ]
    columns.extend(FEATURES)
    return pd.DataFrame(records, columns=columns)


def _write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-150 즉사 부검")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--permutations", type=int, default=_PERMUTATIONS)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--checksum", action="store_true", help="검산만 돌리고 종료")
    parser.add_argument("--labeled-out", type=Path, default=REPORTS_DIR / "wan150_labeled.csv")
    parser.add_argument("--quantile-out", type=Path, default=REPORTS_DIR / "wan150_quantile.csv")
    parser.add_argument(
        "--permutation-out", type=Path, default=REPORTS_DIR / "wan150_permutation.csv"
    )
    parser.add_argument("--loo-out", type=Path, default=REPORTS_DIR / "wan150_leave_one_out.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan150_summary.md")
    args = parser.parse_args(argv)

    if args.checksum:
        seq, prod, ok = checksum(start=args.start, end=args.end, db_path=args.db)
        print(f"[wan150] checksum BTC 1h: sequenced={seq} production={prod} match={ok}")
        return 0 if ok else 1

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
    if result.leave_one_out:
        _write_csv(_rows_to_frame(result.leave_one_out), args.loo_out)
    print(f"[wan150] labeled rows={len(result.labeled)} → {args.labeled_out}")

    summary = build_summary_markdown(result, quantile_csv=args.quantile_out)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan150] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
