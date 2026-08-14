"""가설을 뒤집은 「과열 반등 회피」 필터를 실제 손익으로 검정 (WAN-210, WAN-150 §3 후속).

## 배경 — WAN-150이 부호로 반증한 것

WAN-150(`wan150_instant_death_autopsy`)이 사용자의 RSI-EMA 곡률 가설(∩+하락=롤오버=즉사)을
**부호로 반증**했다: 즉사를 부르는 건 롤오버가 아니라 **과열된 반등(높은/오르는 RSI)에
올라타는 것**이었다(`tap_rsi`가 즉사 대 승자에서 +0.137(15m)·+0.115(1h) OOS 상관). 사용자:
*"그럼 가설의 반대로 하면?"* → 부호를 뒤집어 **RSI가 높은 상태의 진입을 거르는 상한 게이트**가
실제로 즉사를 줄이고 수익을 올리는지 검정한다.

## 두 갈래 — 무엇이 싼가

라벨·특징의 대부분은 이미 `wan150_labeled.csv`에 있다(핀 없는 오늘 엔진 · 9종목 · 못 박은
6년 · 필터 1.28 켜짐). 그래서 이 모듈은 비용을 둘로 가른다:

* **Part A(싼 길 · 3 TF 전부, 1분봉 서브스텝 없음)** — `wan150_labeled.csv` 위에서:
  - §1 게이트가 **즉사를 무작위 제거보다 더 잘 줄이는가**(즉사 대 승자 매칭 널, OOS).
  - §2 **존폭과 독립인가** — `tap_rsi`↔즉사 상관을 `zone_width_atr`로 통제한 **편상관** +
    심볼 층화 순열 → (a) 독립 / (b) 공선 / (c) 부분 독립.
  - §3-bis **지속-심화 추세**(사용자 정정 2026-07-29) — RSI-EMA 기울기가 여러 봉에 걸쳐
    "0에서 계속 음수로 벌어지는지"를 세 특징(연속 심화 run·N봉 단조 하락·N봉 기울기 추세)으로
    조작화해 같은 자(즉사 대 승자 상관+매칭 널)로 검정한다. **N∈{4,5,6} 착수 전 못박고 전수
    병기.** 특징은 라벨 원자료에 없으므로 **HTF 프레임만으로**(서브스텝 없이) 라벨된 탭 시각에
    다시 계산한다 — 여전히 싼 길이다.
  - leave-one-out(ETH·SOL 편중 전례).

* **Part B(비싼 길 · 후보 재빌드) — 실제 시퀀싱 손익** — 게이트를 후보 단계에 걸고(값 ≥ 문턱
  이면 진입 스킵) **재시퀀싱**해 `total_return`·승률·MDD·즉사율을 필터 off·무작위 제거와
  대조한다. 문턱은 IS에서 고르고 OOS 검증(상위 1/4·1/3·1/2 전수). `pen_5bp` 체결 보수화 재검
  포함(셀을 그 렌즈로 다시 빌드). 후보 재빌드가 15m·6년에서 초선형이라(메모리) 이 길만
  `--timeframes`/`--append`로 나눠 돈다.

## ⚠️ 사전 무게 · 인용 경고

* 🚨 **이건 사실상 「RSI 상한 게이트」다** — WAN-123이 RSI 재탭 게이트를 **순손해라서
  제거**했다(성격은 다르나 RSI로 진입을 거르는 시도가 여기서 반복 실패). 표에 명시한다.
* 전부 `baseline`(낙관) 렌즈 위 → `pen_5bp` 재검이 관문이다(게이트가 마진 체결에 기대는지).
* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지** — 다른 질문
  (*이미 진입한 손절 중 즉사를 진입 시점에 거르는가*)이다.
* **측정 전용 — 기본값·토대·실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`)는 건드리지 않는다.**

라벨 정합성: Part B가 후보를 재빌드할 때 게이트 off(default) 팔의 라벨 카운트는
`wan150_labeled.csv`의 그 셀과 일치해야 한다(검산: `--checksum`) — funding=False로 라벨을
뽑는 것도 WAN-150과 같다(즉사 분류에 펀딩은 무관하고, 매칭 널은 같은 후보 풀 안 상대 대조라
펀딩이 양팔에 동일하게 적용된다).

재현:
    python -m backtest.wan210_reverse_rsi_filter --part null      # Part A (싼 길, 3 TF)
    python -m backtest.wan210_reverse_rsi_filter --part pnl --timeframes 1h,4h
    python -m backtest.wan210_reverse_rsi_filter --part pnl --timeframes 15m --append
    python -m backtest.wan210_reverse_rsi_filter --from-csv        # 요약만 재생성
    python -m backtest.wan210_reverse_rsi_filter --checksum        # 라벨/생산 정합 검산
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import _FeatureExtractor, _isnan, harness_prepare
from backtest.wan133_geometry_vs_selection import ARM_DEFAULT, ARM_FILTER, MIN_TRADES_FOR_PNL, _bare
from backtest.wan142_zone_width_filter_verdict import ARM_MATCHED, MATCH_SEEDS, SEED_AGGREGATE
from backtest.wan150_instant_death_autopsy import (
    Label,
    _corr,
    _Wan150Extractor,
    classify,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockParams

# --------------------------------------------------------------------------- #
# 상수 — 오늘 엔진 좌표 (핀 없음)
# --------------------------------------------------------------------------- #

#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
DEFAULT_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS
DEFAULT_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

REPORTS_DIR = Path("backtest/reports")

LENS_PRIMARY = "baseline"
LENS_PEN5 = "pen_5bp"

#: §1 상한 게이트 후보. 값이 문턱 이상이면 진입 스킵(과열 반등 회피).
GATE_FEATURES: tuple[str, ...] = ("tap_rsi", "rsi_ema_slope")

#: 상위에서 잘라내는 비율(문턱 = IS 분위 1−fraction). 전수 병기(IS 1등 뽑기 금지, WAN-90/161).
REMOVE_FRACTIONS: tuple[float, ...] = (0.25, 1.0 / 3.0, 0.5)

#: §3-bis 지속-심화 추세 창(사용자 정정 2026-07-29). 착수 전 못박고 전수 병기.
DEEPEN_WINDOWS: tuple[int, ...] = (4, 5, 6)

_RSI_LENGTH = 14
_RSI_EMA_SPAN = 14
_PERMUTATIONS = 2000
_SEED = 210
_MIN_N = MIN_TRADES_FOR_PNL  # 유효 셀 = 거래 20건(WAN-84 기준, wan150과 같음).

#: §3-bis 특징의 가설 부호(corr(특징, 즉사)>0 = 특징↑→즉사↑).
#: 지속 심화(롤오버)일수록 즉사라는 사용자 가설을 부호로 적는다.
DEEPEN_HYPOTHESIS_SIGN: dict[str, int] = {}
for _n in DEEPEN_WINDOWS:
    DEEPEN_HYPOTHESIS_SIGN[f"deepen_run_{_n}"] = +1  # 심화 run이 길수록 즉사.
    DEEPEN_HYPOTHESIS_SIGN[f"monotone_fall_{_n}"] = +1  # N봉 내내 하락이면 즉사.
    DEEPEN_HYPOTHESIS_SIGN[f"slope_trend_{_n}"] = -1  # 기울기 추세가 더 음수(가속 하락)면 즉사.

DEEPEN_FEATURES: tuple[str, ...] = tuple(DEEPEN_HYPOTHESIS_SIGN)

#: 게이트 특징의 가설 부호 — 즉사일수록 값이 크다(WAN-150 반증 결과).
GATE_HYPOTHESIS_SIGN: dict[str, int] = {"tap_rsi": +1, "rsi_ema_slope": +1}


# --------------------------------------------------------------------------- #
# §3-bis — 지속-심화 추세 특징 (HTF 프레임만, 서브스텝 없음)
# --------------------------------------------------------------------------- #


def deepening_features(rsi_ema: Sequence[float], prev: int, window: int) -> dict[str, float | None]:
    """RSI-EMA 기울기가 직전 `window`봉에 걸쳐 "계속 음수로 벌어지는지"를 세 특징으로.

    * `deepen_run_N` — 탭 직전(prev)에서 뒤로, 기울기 s_j<0 이며 s_j<s_{j-1}(더 음수로
      심화)인 봉이 연속으로 몇 개 이어지는가. 사용자의 "0에서 계속 음수로 벌어짐"의 직역.
    * `monotone_fall_N` — 창 안 N개 기울기가 전부 음수(하락 지속)면 1.0.
    * `slope_trend_N` — 창 안 기울기 시퀀스의 선형 추세(가속). 더 음수면 심화.

    ⚠️ **룩어헤드 없음** — `prev = pos−1`(탭 직전 확정봉)이고, 기울기는 그보다 앞선 닫힌
    봉들만 쓴다. 워밍업/경계로 값이 모자라면 None(조용한 통과 금지, WAN-123 교훈).
    """
    empty: dict[str, float | None] = {
        f"deepen_run_{window}": None,
        f"monotone_fall_{window}": None,
        f"slope_trend_{window}": None,
    }
    # 창 안 N개 기울기 s_j = E[j]−E[j−1], j ∈ (prev−N+1 .. prev). 가장 이른 s는 E[prev−N]이
    # 필요하다. 심화 run은 s_{j−1}까지 봐야 하므로 한 개 더 필요(E[prev−N−1]).
    lo = prev - window
    if lo - 1 < 0:
        return empty
    e = [rsi_ema[prev - window - 1 + k] for k in range(window + 2)]
    if any(_isnan(v) for v in e):
        return empty
    # slopes[k] = e[k+1] − e[k], k=0..window (총 window+1개; 마지막 window개가 창).
    slopes = [e[k + 1] - e[k] for k in range(window + 1)]
    win_slopes = slopes[1:]  # 창 = 마지막 window개.
    # deepen_run: 끝(prev)에서 뒤로 s<0 & s<이전 s 가 이어지는 길이.
    run = 0
    for k in range(window, 0, -1):
        if slopes[k] < 0.0 and slopes[k] < slopes[k - 1]:
            run += 1
        else:
            break
    monotone = 1.0 if all(s < 0.0 for s in win_slopes) else 0.0
    # slope_trend: 창 기울기의 선형회귀 기울기(가속). x=0..N−1.
    n = len(win_slopes)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(win_slopes) / n
    var_x = sum((x - mx) ** 2 for x in xs)
    trend = (
        sum((x - mx) * (y - my) for x, y in zip(xs, win_slopes, strict=True)) / var_x
        if var_x > 0
        else 0.0
    )
    return {
        f"deepen_run_{window}": float(run),
        f"monotone_fall_{window}": monotone,
        f"slope_trend_{window}": float(trend),
    }


@dataclass
class _DeepenExtractor:
    """라벨된 탭 시각에서 §3-bis 특징을 낸다 — HTF 프레임만, 서브스텝 없음."""

    time_to_pos: dict[int, int]
    rsi_ema: list[float]

    @classmethod
    def build(cls, frame: pd.DataFrame) -> _DeepenExtractor:
        base = _FeatureExtractor.build(frame)
        from strategy.indicators import rsi

        rsi_ema = rsi(frame, length=_RSI_LENGTH).ewm(span=_RSI_EMA_SPAN, adjust=False).mean()
        return cls(time_to_pos=dict(base.time_to_pos), rsi_ema=[float(v) for v in rsi_ema.tolist()])

    def features_for_time(self, trigger_time: int) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        pos = self.time_to_pos.get(int(trigger_time))
        if pos is None or pos < 1:
            return dict.fromkeys(DEEPEN_FEATURES)
        prev = pos - 1
        for window in DEEPEN_WINDOWS:
            out.update(deepening_features(self.rsi_ema, prev, window))
        return out


# --------------------------------------------------------------------------- #
# 검정 통계 — 상관 + 편상관 + 심볼 층화 순열
# --------------------------------------------------------------------------- #


def _residualize(values: Sequence[float], control: Sequence[float]) -> list[float]:
    """`values`에서 `control`의 선형 성분을 뺀 잔차(편상관용)."""
    n = len(values)
    mx = sum(control) / n
    my = sum(values) / n
    var_x = sum((x - mx) ** 2 for x in control)
    if var_x <= 0:
        return [y - my for y in values]
    b = sum((x - mx) * (y - my) for x, y in zip(control, values, strict=True)) / var_x
    a = my - b * mx
    return [y - (a + b * x) for y, x in zip(values, control, strict=True)]


def _partial_corr(
    values: Sequence[float], control: Sequence[float], target: Sequence[float]
) -> float | None:
    """corr(values, target | control) — 둘 다 control로 잔차화한 뒤 상관."""
    if len(values) < 3:
        return None
    rv = _residualize(values, control)
    rt = _residualize([float(t) for t in target], control)
    return _corr(rv, rt)


@dataclass(frozen=True)
class _StrataPerm:
    """심볼 층화 라벨 순열 결과."""

    correlation: float | None
    p_value: float | None
    n: int


def _strata_permutation(
    values: Sequence[float],
    target: Sequence[float],
    symbols: Sequence[str],
    *,
    control: Sequence[float] | None = None,
    permutations: int = _PERMUTATIONS,
    seed: int = _SEED,
) -> _StrataPerm:
    """corr(또는 편상관)의 심볼 층화 라벨 순열 검정(WAN-117/150 자)."""
    n = len(values)
    if n < _MIN_N:
        return _StrataPerm(None, None, n)
    tgt = [float(t) for t in target]
    actual = (
        _partial_corr(values, control, tgt) if control is not None else _corr(list(values), tgt)
    )
    if actual is None:
        return _StrataPerm(None, None, n)
    strata: dict[str, list[int]] = defaultdict(list)
    for i, sym in enumerate(symbols):
        strata[sym].append(i)
    rng = random.Random(seed)
    goal = abs(actual)
    extreme = 0
    for _ in range(permutations):
        shuffled = tgt.copy()
        for idxs in strata.values():
            pool = [tgt[i] for i in idxs]
            rng.shuffle(pool)
            for slot, i in enumerate(idxs):
                shuffled[i] = pool[slot]
        corr = (
            _partial_corr(values, control, shuffled)
            if control is not None
            else _corr(list(values), shuffled)
        )
        if corr is not None and abs(corr) >= goal - 1e-12:
            extreme += 1
    return _StrataPerm(actual, extreme / permutations, n)


# --------------------------------------------------------------------------- #
# Part A — 라벨 원자료 위의 상관/편상관/매칭 널 (싼 길)
# --------------------------------------------------------------------------- #


class CorrRow(BaseModel):
    """한 (TF, 구간, 특징)의 상관·편상관 검정(즉사 대 승자, 심볼 풀링)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    n: int
    positive_rate: float
    correlation: float | None
    p_value: float | None
    partial_correlation: float | None
    """`tap_rsi`류만 — `zone_width_atr`로 통제한 편상관(§2 독립성). 그 외는 None."""
    p_partial: float | None
    hypothesis_sign: int
    direction_matches: bool


class DeathNullRow(BaseModel):
    """한 (TF, 구간, 특징, 제거비율)의 게이트 대 무작위 즉사율(매칭 널)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    remove_fraction: float
    threshold: float | None
    n_subset: int
    """즉사+승자 부분집합 크기."""
    n_removed: int
    base_death_rate: float
    gate_death_rate: float | None
    matched_death_mean: float | None
    p_death: float | None
    """단측 순위 p — 무작위 제거의 즉사율이 게이트 이하(같거나 더 잘 줄임)일 확률."""
    winner_removed_rate: float | None
    """게이트가 제거한 것 중 승자 비율(오폭). 낮을수록 정밀."""
    death_removed_rate: float | None
    """게이트가 제거한 것 중 즉사 비율."""


@dataclass
class _LabeledCell:
    """한 (TF) 라벨 원자료 뷰(즉사·승자만, 특징 붙음)."""

    timeframe: str
    segment: str
    symbols: list[str]
    death: list[float]  # 1.0 즉사 / 0.0 승자
    feats: dict[str, list[float | None]]


def _pooled_quantile(values: Sequence[float], q: float) -> float | None:
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    return float(pd.Series(vals).quantile(q))


def corr_rows_from_labeled(df: pd.DataFrame, *, permutations: int = _PERMUTATIONS) -> list[CorrRow]:
    """§1 게이트 특징 + §3-bis 특징의 즉사 대 승자 상관/편상관(TF·구간 풀링)."""
    rows: list[CorrRow] = []
    sub = df[df["label"].isin([Label.INSTANT_DEATH.value, Label.WINNER.value])]
    all_features = (*GATE_FEATURES, *DEEPEN_FEATURES)
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            cell = sub[(sub["timeframe"] == timeframe) & (sub["segment"] == segment)]
            if cell.empty:
                continue
            death_all = (cell["label"] == Label.INSTANT_DEATH.value).astype(float).tolist()
            syms_all = cell["symbol"].tolist()
            for feature in all_features:
                if feature not in cell.columns:
                    continue
                mask = cell[feature].notna()
                values = cell.loc[mask, feature].astype(float).tolist()
                target = [death_all[i] for i in range(len(cell)) if bool(mask.iloc[i])]
                symbols = [syms_all[i] for i in range(len(cell)) if bool(mask.iloc[i])]
                n = len(values)
                hyp = GATE_HYPOTHESIS_SIGN.get(feature) or DEEPEN_HYPOTHESIS_SIGN.get(feature, 0)
                perm = _strata_permutation(
                    values, target, symbols, permutations=permutations, seed=_SEED
                )
                partial: float | None = None
                p_partial: float | None = None
                if feature in GATE_FEATURES and "zone_width_atr" in cell.columns:
                    zmask = mask & cell["zone_width_atr"].notna()
                    zv = cell.loc[zmask, feature].astype(float).tolist()
                    zc = cell.loc[zmask, "zone_width_atr"].astype(float).tolist()
                    zt = (
                        (cell.loc[zmask, "label"] == Label.INSTANT_DEATH.value)
                        .astype(float)
                        .tolist()
                    )
                    zs = cell.loc[zmask, "symbol"].tolist()
                    pperm = _strata_permutation(
                        zv, zt, zs, control=zc, permutations=permutations, seed=_SEED
                    )
                    partial, p_partial = pperm.correlation, pperm.p_value
                corr = perm.correlation
                direction = hyp != 0 and corr is not None and (corr > 0) == (hyp > 0)
                rows.append(
                    CorrRow(
                        timeframe=timeframe,
                        segment=segment,
                        feature=feature,
                        n=n,
                        positive_rate=(sum(target) / n if n else 0.0),
                        correlation=corr,
                        p_value=perm.p_value,
                        partial_correlation=partial,
                        p_partial=p_partial,
                        hypothesis_sign=hyp,
                        direction_matches=direction,
                    )
                )
    return rows


def death_null_rows_from_labeled(df: pd.DataFrame) -> list[DeathNullRow]:
    """§1 매칭 널 — 게이트가 무작위 제거보다 즉사율을 더 잘 줄이는가(즉사 대 승자, TF 풀링).

    문턱은 IS 풀링 분위(1−fraction)에서 고르고 OOS에도 같은 값을 적용한다(룩어헤드 없음).
    게이트 = 값 ≥ 문턱 제거. 매칭 널 = 같은 개수(k)를 무작위 제거(시드 20개).
    """
    rows: list[DeathNullRow] = []
    sub = df[df["label"].isin([Label.INSTANT_DEATH.value, Label.WINNER.value])]
    for timeframe in DEFAULT_TIMEFRAMES:
        tf_rows = sub[sub["timeframe"] == timeframe]
        is_cell = tf_rows[tf_rows["segment"] == SEGMENT_IS]
        for feature in GATE_FEATURES:
            if feature not in tf_rows.columns:
                continue
            for fraction in REMOVE_FRACTIONS:
                threshold = _pooled_quantile(
                    is_cell[feature].dropna().astype(float).tolist(), 1.0 - fraction
                )
                for segment in (SEGMENT_IS, SEGMENT_OOS):
                    cell = tf_rows[tf_rows["segment"] == segment]
                    cell = cell[cell[feature].notna()]
                    n = len(cell)
                    death = (cell["label"] == Label.INSTANT_DEATH.value).astype(float).tolist()
                    feat = cell[feature].astype(float).tolist()
                    base_rate = sum(death) / n if n else 0.0
                    if threshold is None or n < _MIN_N:
                        rows.append(
                            DeathNullRow(
                                timeframe=timeframe,
                                segment=segment,
                                feature=feature,
                                remove_fraction=fraction,
                                threshold=threshold,
                                n_subset=n,
                                n_removed=0,
                                base_death_rate=base_rate,
                                gate_death_rate=None,
                                matched_death_mean=None,
                                p_death=None,
                                winner_removed_rate=None,
                                death_removed_rate=None,
                            )
                        )
                        continue
                    removed = [i for i in range(n) if feat[i] >= threshold]
                    kept = [i for i in range(n) if feat[i] < threshold]
                    k = len(removed)
                    gate_rate = sum(death[i] for i in kept) / len(kept) if kept else None
                    win_removed = sum(1 for i in removed if death[i] == 0.0) / k if k else None
                    death_removed = sum(death[i] for i in removed) / k if k else None
                    matched: list[float] = []
                    for s in MATCH_SEEDS:
                        drop = set(random.Random(s).sample(range(n), k)) if k else set()
                        keep = [i for i in range(n) if i not in drop]
                        if keep:
                            matched.append(sum(death[i] for i in keep) / len(keep))
                    m_mean = sum(matched) / len(matched) if matched else None
                    p_death: float | None = None
                    if gate_rate is not None and matched:
                        # 무작위가 게이트 이하(같거나 더 잘 줄임)인 시드 비율.
                        p_death = (sum(1 for v in matched if v <= gate_rate) + 1) / (
                            len(matched) + 1
                        )
                    rows.append(
                        DeathNullRow(
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            remove_fraction=fraction,
                            threshold=threshold,
                            n_subset=n,
                            n_removed=k,
                            base_death_rate=base_rate,
                            gate_death_rate=gate_rate,
                            matched_death_mean=m_mean,
                            p_death=p_death,
                            winner_removed_rate=win_removed,
                            death_removed_rate=death_removed,
                        )
                    )
    return rows


def deepen_features_for_labeled(
    df: pd.DataFrame,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> pd.DataFrame:
    """라벨 원자료에 §3-bis 특징 열을 붙인다 — HTF 프레임만(서브스텝 없음, 싼 길).

    각 (심볼, TF)의 HTF 프레임을 로드해 `_DeepenExtractor`로 라벨된 `trigger_time`에서
    지속-심화 특징을 계산한다. `need_1m=False`라 1분봉 서브스텝을 돌지 않는다.
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    out = df.copy()
    for feature in DEEPEN_FEATURES:
        out[feature] = pd.Series([None] * len(out), dtype="object")
    for symbol in sorted(out["symbol"].unique()):
        for timeframe in sorted(out["timeframe"].unique()):
            mask = (out["symbol"] == symbol) & (out["timeframe"] == timeframe)
            if not mask.any():
                continue
            market = harness.load_market_data(
                harness.normalize_symbol(symbol),
                timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                need_1m=False,
                funding=False,
                db_path=db_path,
            )
            if market.empty:
                continue
            extractor = _DeepenExtractor.build(harness_prepare(market.htf_df))
            for idx in out.index[mask]:
                feats = extractor.features_for_time(int(out.at[idx, "trigger_time"]))
                for feature in DEEPEN_FEATURES:
                    out.at[idx, feature] = feats.get(feature)
    for feature in DEEPEN_FEATURES:
        out[feature] = pd.to_numeric(out[feature], errors="coerce")
    return out


def leave_one_out_death(
    df: pd.DataFrame, *, feature: str, fraction: float
) -> list[tuple[str, str, float | None]]:
    """OOS 게이트 즉사율을 심볼 하나씩 빼고 재계산(편중 진단). 반환 (TF, 제외심볼, 게이트즉사율)."""
    sub = df[df["label"].isin([Label.INSTANT_DEATH.value, Label.WINNER.value])]
    out: list[tuple[str, str, float | None]] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        tf_rows = sub[sub["timeframe"] == timeframe]
        is_cell = tf_rows[tf_rows["segment"] == SEGMENT_IS]
        threshold = _pooled_quantile(
            is_cell[feature].dropna().astype(float).tolist(), 1.0 - fraction
        )
        oos = tf_rows[(tf_rows["segment"] == SEGMENT_OOS) & (tf_rows[feature].notna())]
        if threshold is None or oos.empty:
            continue
        for drop in sorted(oos["symbol"].unique()):
            rest = oos[oos["symbol"] != drop]
            feat = rest[feature].astype(float).tolist()
            death = (rest["label"] == Label.INSTANT_DEATH.value).astype(float).tolist()
            kept = [i for i in range(len(rest)) if feat[i] < threshold]
            rate = sum(death[i] for i in kept) / len(kept) if kept else None
            out.append((timeframe, drop, rate))
    return out


# --------------------------------------------------------------------------- #
# Part B — 실제 시퀀싱 손익 (후보 재빌드, 비싼 길)
# --------------------------------------------------------------------------- #


@dataclass
class GatedCell:
    """한 (심볼, TF)의 후보 + 후보별 게이트 특징 + 라벨(재빌드)."""

    symbol: str
    timeframe: str
    is_boundary: int
    cands: list[_Candidate]
    feats: list[dict[str, float | None]]
    labels: list[Label | None]

    def segment_idx(self, segment: str) -> list[int]:
        return [
            i
            for i, c in enumerate(self.cands)
            if (c.trigger_time < self.is_boundary) == (segment == SEGMENT_IS)
        ]


def build_gated_cell(
    market: harness.MarketData, *, params: ConfluenceParams, order_block_params: OrderBlockParams
) -> GatedCell | None:
    """오늘 엔진으로 후보를 한 번 빌드하고 게이트 특징·라벨을 붙인다(핀 없음)."""
    if market.empty or market.df_1m.empty:
        return None
    cfg = harness.build_config(market.timeframe)
    cands, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_params=order_block_params,
    )
    if not cands:
        return None
    frame = harness_prepare(market.htf_df)
    extractor = _Wan150Extractor.build(frame)
    deepen = _DeepenExtractor.build(frame)
    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * IS_FRACTION)
    feats: list[dict[str, float | None]] = []
    labels: list[Label | None] = []
    for cand in cands:
        f = extractor.features_for(cand) or {}
        f.update(deepen.features_for_time(cand.trigger_time))
        feats.append(f)
        labels.append(classify(cand.reason, cand.mfe_r))
    return GatedCell(
        symbol=market.symbol,
        timeframe=market.timeframe,
        is_boundary=is_boundary,
        cands=cands,
        feats=feats,
        labels=labels,
    )


def is_gate_threshold(cell: GatedCell, feature: str, fraction: float) -> float | None:
    """IS 후보의 `feature` 분위(1−fraction) — 상한 게이트 문턱(OOS에도 적용)."""
    vals = [
        v for i in cell.segment_idx(SEGMENT_IS) if (v := cell.feats[i].get(feature)) is not None
    ]
    return _pooled_quantile(vals, 1.0 - fraction)


def gate_keep_indices(
    seg_idx: Sequence[int],
    feats: Sequence[dict[str, float | None]],
    feature: str,
    threshold: float,
) -> list[int]:
    """상한 게이트가 남기는 인덱스 — 값 < 문턱만 진입(값 ≥ 문턱은 스킵).

    ⚠️ **특징이 None(워밍업/경계)이면 남긴다** — 잴 수 없는 것을 거르지 않는다(게이트는
    "값 ≥ 문턱이면 스킵"이지 "값이 없으면 스킵"이 아니다). 회귀 테스트가 이 계약을 고정한다.
    """
    return [i for i in seg_idx if (v := feats[i].get(feature)) is None or v < threshold]


class PnlRow(BaseModel):
    """한 (심볼, TF, 구간, 특징, 제거비율, 팔, 시드, 렌즈)의 시퀀싱 손익."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    feature: str
    remove_fraction: float
    arm: str
    seed: int
    lens: str
    threshold: float | None
    num_candidates: float
    num_trades: float
    total_return: float
    max_drawdown: float
    win_rate: float
    death_rate: float | None


@dataclass(frozen=True)
class _SeqStats:
    total_return: float
    max_drawdown: float
    win_rate: float
    num_trades: int
    death_rate: float | None


def _seq_stats(cands: list[_Candidate], market: harness.MarketData, timeframe: str) -> _SeqStats:
    """후보 목록을 프로덕션 시퀀서로 배치한 손익 + 즉사율(funding=False, wan150과 같음)."""
    cfg = harness.build_config(timeframe)
    pairs = sequence_with_candidates(cands, cfg)
    trades = [t for _, t in pairs]
    m = build_result_from_trades(trades, cfg, timeframe).metrics
    labeled = [lab for c, _ in pairs if (lab := classify(c.reason, c.mfe_r)) is not None]
    deaths = sum(1 for lab in labeled if lab is Label.INSTANT_DEATH)
    death_rate = deaths / len(labeled) if labeled else None
    return _SeqStats(m.total_return, m.max_drawdown, m.win_rate, m.num_trades, death_rate)


def pnl_rows_for_cell(cell: GatedCell, market: harness.MarketData, *, lens: str) -> list[PnlRow]:
    """게이트 off(default) · on(filter) · 무작위 제거(matched)의 구간별 손익."""
    rows: list[PnlRow] = []

    def _row(
        segment: str,
        feature: str,
        fraction: float,
        arm: str,
        seed: int,
        threshold: float | None,
        idx: list[int],
    ) -> PnlRow:
        s = _seq_stats([cell.cands[i] for i in idx], market, cell.timeframe)
        return PnlRow(
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            segment=segment,
            feature=feature,
            remove_fraction=fraction,
            arm=arm,
            seed=seed,
            lens=lens,
            threshold=threshold,
            num_candidates=float(len(idx)),
            num_trades=float(s.num_trades),
            total_return=s.total_return,
            max_drawdown=s.max_drawdown,
            win_rate=s.win_rate,
            death_rate=s.death_rate,
        )

    for feature in GATE_FEATURES:
        for fraction in REMOVE_FRACTIONS:
            threshold = is_gate_threshold(cell, feature, fraction)
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                seg_idx = cell.segment_idx(segment)
                # default(게이트 off)는 특징·비율과 무관하지만 좌표를 남겨 대조를 쉽게 한다.
                rows.append(
                    _row(
                        segment, feature, fraction, ARM_DEFAULT, SEED_AGGREGATE, threshold, seg_idx
                    )
                )
                if threshold is None:
                    continue
                keep = gate_keep_indices(seg_idx, cell.feats, feature, threshold)
                rows.append(
                    _row(segment, feature, fraction, ARM_FILTER, SEED_AGGREGATE, threshold, keep)
                )
                k = len(seg_idx) - len(keep)
                for s in MATCH_SEEDS:
                    drop = set(random.Random(s).sample(seg_idx, k)) if k else set()
                    idx = [i for i in seg_idx if i not in drop]
                    rows.append(_row(segment, feature, fraction, ARM_MATCHED, s, threshold, idx))
    return rows


# --------------------------------------------------------------------------- #
# Part B 집계 — 심볼평균 · 매칭 검정
# --------------------------------------------------------------------------- #


def _pnl_symbol_mean(
    rows: Sequence[PnlRow],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    fraction: float,
    arm: str,
    seed: int,
    lens: str,
) -> dict[str, float | None]:
    sub = [
        r
        for r in rows
        if r.timeframe == timeframe
        and r.segment == segment
        and r.feature == feature
        and abs(r.remove_fraction - fraction) < 1e-9
        and r.arm == arm
        and r.seed == seed
        and r.lens == lens
        and r.num_trades >= _MIN_N
    ]
    if not sub:
        return {
            "total_return": None,
            "win_rate": None,
            "max_drawdown": None,
            "death_rate": None,
            "n_symbols": 0.0,
        }
    n = len(sub)
    deaths = [r.death_rate for r in sub if r.death_rate is not None]
    return {
        "total_return": sum(r.total_return for r in sub) / n,
        "win_rate": sum(r.win_rate for r in sub) / n,
        "max_drawdown": sum(r.max_drawdown for r in sub) / n,
        "death_rate": (sum(deaths) / len(deaths)) if deaths else None,
        "n_symbols": float(n),
    }


class PnlTestRow(BaseModel):
    """한 (TF, 구간, 특징, 비율, 렌즈)의 필터 대 무작위·default 대조."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    remove_fraction: float
    lens: str
    n_symbols: int
    default_return: float | None
    filter_return: float | None
    matched_return_mean: float | None
    p_return: float | None
    default_death: float | None
    filter_death: float | None
    default_mdd: float | None
    filter_mdd: float | None


def pnl_test_rows(rows: Sequence[PnlRow]) -> list[PnlTestRow]:
    """필터 팔을 무작위 제거 시드 분포 위에 놓고 단측 순위 p(수익)."""
    out: list[PnlTestRow] = []
    lenses = sorted({r.lens for r in rows})
    for lens in lenses:
        for timeframe in DEFAULT_TIMEFRAMES:
            for feature in GATE_FEATURES:
                for fraction in REMOVE_FRACTIONS:
                    for segment in (SEGMENT_IS, SEGMENT_OOS):
                        base = _pnl_symbol_mean(
                            rows,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            fraction=fraction,
                            arm=ARM_DEFAULT,
                            seed=SEED_AGGREGATE,
                            lens=lens,
                        )
                        filt = _pnl_symbol_mean(
                            rows,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            fraction=fraction,
                            arm=ARM_FILTER,
                            seed=SEED_AGGREGATE,
                            lens=lens,
                        )
                        if filt["n_symbols"] == 0.0:
                            continue
                        seed_returns: list[float] = []
                        for s in MATCH_SEEDS:
                            m = _pnl_symbol_mean(
                                rows,
                                timeframe=timeframe,
                                segment=segment,
                                feature=feature,
                                fraction=fraction,
                                arm=ARM_MATCHED,
                                seed=s,
                                lens=lens,
                            )
                            if m["total_return"] is not None:
                                seed_returns.append(m["total_return"])
                        f_ret = filt["total_return"]
                        p_ret: float | None = None
                        if f_ret is not None and seed_returns:
                            p_ret = (sum(1 for v in seed_returns if v >= f_ret) + 1) / (
                                len(seed_returns) + 1
                            )
                        out.append(
                            PnlTestRow(
                                timeframe=timeframe,
                                segment=segment,
                                feature=feature,
                                remove_fraction=fraction,
                                lens=lens,
                                n_symbols=int(filt["n_symbols"] or 0),
                                default_return=base["total_return"],
                                filter_return=f_ret,
                                matched_return_mean=(
                                    sum(seed_returns) / len(seed_returns) if seed_returns else None
                                ),
                                p_return=p_ret,
                                default_death=base["death_rate"],
                                filter_death=filt["death_rate"],
                                default_mdd=base["max_drawdown"],
                                filter_mdd=filt["max_drawdown"],
                            )
                        )
    return out


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


class VerdictKind(StrEnum):
    """§2 독립성 판정 — 문장이 아니라 이 값이 정본이다(WAN-142 열거형 교훈)."""

    INDEPENDENT = "independent"  # (a) 편상관이 무작위를 넘고 부호 유지
    COLLINEAR = "collinear"  # (b) 편상관이 0으로 붕괴 or 게이트가 널 미달
    PARTIAL = "partial"  # (c) 편상관은 남지만 P&L에서 무작위 미달 등
    INDETERMINATE = "indeterminate"  # 표본 부족


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    text: str

    def __str__(self) -> str:
        return self.text


def independence_verdict(corr_rows: Sequence[CorrRow], *, timeframe: str) -> Verdict:
    """§2 — `tap_rsi`의 OOS 편상관(존폭 통제)이 살아남는가로 독립/공선 판정."""
    row = next(
        (
            r
            for r in corr_rows
            if r.timeframe == timeframe and r.segment == SEGMENT_OOS and r.feature == "tap_rsi"
        ),
        None,
    )
    if row is None or row.correlation is None or row.p_value is None:
        return Verdict(VerdictKind.INDETERMINATE, f"**{timeframe}**: 판정 불가(표본 부족).")
    raw_sig = row.p_value < 0.05 and row.direction_matches
    part = row.partial_correlation
    p_part = row.p_partial
    if not raw_sig:
        return Verdict(
            VerdictKind.COLLINEAR,
            f"**{timeframe}**: (b) 게이트 신호가 OOS 무작위를 못 넘는다(raw corr "
            f"{row.correlation:+.3f}, p={row.p_value:.4f}) — 채택 근거 없음.",
        )
    if part is None or p_part is None:
        return Verdict(VerdictKind.INDETERMINATE, f"**{timeframe}**: 편상관 표본 부족.")
    keep = abs(part) >= 0.5 * abs(row.correlation) and (part > 0) == (row.correlation > 0)
    part_sig = p_part < 0.05
    if keep and part_sig:
        return Verdict(
            VerdictKind.INDEPENDENT,
            f"**{timeframe}**: (a) 존폭 통제 후에도 편상관 유지(raw {row.correlation:+.3f} "
            f"→ partial {part:+.3f}, p={p_part:.4f}) — 존폭과 부분 독립. P&L·pen_5bp 관문은 별개.",
        )
    if part_sig and (part > 0) == (row.correlation > 0):
        return Verdict(
            VerdictKind.PARTIAL,
            f"**{timeframe}**: (c) 부분 독립 — 편상관이 줄지만(raw {row.correlation:+.3f} → "
            f"partial {part:+.3f}, p={p_part:.4f}) 절반 이상 소멸, 존폭이 상당 부분 설명.",
        )
    return Verdict(
        VerdictKind.COLLINEAR,
        f"**{timeframe}**: (b) 존폭 통제로 편상관이 붕괴(raw {row.correlation:+.3f} → "
        f"partial {part:+.3f}, p={p_part:.4f}) — 게이트는 존폭 필터의 재선별.",
    )


# --------------------------------------------------------------------------- #
# 실험 실행
# --------------------------------------------------------------------------- #


@dataclass
class PartAResult:
    corr: list[CorrRow] = field(default_factory=list)
    death_null: list[DeathNullRow] = field(default_factory=list)
    loo: list[tuple[str, str, str, float | None]] = field(default_factory=list)
    labeled_augmented: pd.DataFrame | None = None


def run_part_a(
    *,
    labeled_csv: Path,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    permutations: int = _PERMUTATIONS,
    db_path: str = harness.DB_PATH,
) -> PartAResult:
    """Part A — 라벨 원자료 + §3-bis 특징 위의 상관/편상관/매칭 널(서브스텝 없음)."""
    df = pd.read_csv(labeled_csv)
    df = deepen_features_for_labeled(df, start=start, end=end, db_path=db_path)
    corr = corr_rows_from_labeled(df, permutations=permutations)
    death_null = death_null_rows_from_labeled(df)
    loo: list[tuple[str, str, str, float | None]] = []
    for feature in GATE_FEATURES:
        for tf, drop, rate in leave_one_out_death(df, feature=feature, fraction=1.0 / 3.0):
            loo.append((feature, tf, drop, rate))
    return PartAResult(corr=corr, death_null=death_null, loo=loo, labeled_augmented=df)


def run_part_b(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    lenses: tuple[str, ...] = (LENS_PRIMARY, LENS_PEN5),
    db_path: str = harness.DB_PATH,
) -> list[PnlRow]:
    """Part B — 후보 재빌드 → 게이트 재시퀀싱 손익(비싼 길)."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    ob_params = OrderBlockParams()
    rows: list[PnlRow] = []
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
            for lens in lenses:
                params = (
                    ConfluenceParams()
                    if lens == LENS_PRIMARY
                    else harness.build_params(fill=harness.fill_preset(lens))
                )
                cell = build_gated_cell(market, params=params, order_block_params=ob_params)
                if cell is None:
                    print(f"[wan210] {norm} {timeframe} {lens}: 후보 없음")
                    continue
                cell_rows = pnl_rows_for_cell(cell, market, lens=lens)
                rows.extend(cell_rows)
                deaths = sum(1 for lab in cell.labels if lab is Label.INSTANT_DEATH)
                print(
                    f"[wan210] {norm} {timeframe} {lens}: cands={len(cell.cands)} "
                    f"deaths={deaths} rows={len(cell_rows)}"
                )
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def checksum(
    *,
    labeled_csv: Path,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> tuple[int, int, int, bool]:
    """게이트 off(default 팔) 후보 수 ≡ 생산 num_trades ≡ wan150 라벨 카운트.

    반환 `(gate_off_trades, production_num_trades, wan150_labeled_count, matches)`.
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
    cell = build_gated_cell(
        market, params=ConfluenceParams(), order_block_params=OrderBlockParams()
    )
    assert cell is not None
    # 게이트 off = 전 후보 시퀀싱.
    stats = _seq_stats(cell.cands, market, timeframe)
    outcome = harness.run_once(
        market, params=ConfluenceParams(), cfg=harness.build_config(timeframe)
    )
    prod = outcome.result.metrics.num_trades
    df = pd.read_csv(labeled_csv)
    wan150_count = int(((df["symbol"] == norm) & (df["timeframe"] == timeframe)).sum())
    # wan150 라벨은 END_OF_DATA·MFE결측을 뺀 값이라 시퀀서 거래 수의 부분집합이다.
    # 여기서는 게이트 off 시퀀싱이 생산과 같은지(핵심)와 라벨 <= 거래를 본다.
    ok = stats.num_trades == prod and wan150_count <= prod
    return stats.num_trades, prod, wan150_count, ok


# --------------------------------------------------------------------------- #
# CSV I/O · 요약
# --------------------------------------------------------------------------- #


def _rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def _fmt(x: float | None, pct: bool = False, sign: bool = False) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if pct:
        return f"{x * 100:{'+' if sign else ''}.2f}%"
    return f"{x:{'+' if sign else ''}.3f}"


def build_summary_markdown(
    part_a: PartAResult,
    pnl_rows: Sequence[PnlRow],
    *,
    corr_csv: Path,
    death_csv: Path,
    pnl_csv: Path,
) -> str:
    lines: list[str] = []
    lines.append("# WAN-210 과열 반등 회피 필터 — 뒤집은 가설을 실제 손익으로 검정\n")
    lines.append(
        f"9종목 × {', '.join(DEFAULT_TIMEFRAMES)} · 못 박은 6년 창 **{DEFAULT_START} ~ "
        f"{DEFAULT_END}** · 오늘의 채택 기본값(핀 없음 · 필터 1.28 · `intrabar_live` · "
        "`unconditional` · 고정 1.5R · 롱 온리 · 분리 존) · 공식 렌즈 `baseline`(+ Part B "
        "`pen_5bp` 재검). 라벨은 `wan150_labeled.csv` 재사용 · funding=False(WAN-150과 같음). "
        "게이트 = **값 ≥ IS 분위 문턱이면 진입 스킵**(과열 반등 회피, WAN-150이 부호 반증한 "
        "방향). 🚨 사실상 「RSI 상한 게이트」 — WAN-123이 RSI 재탭 게이트를 순손해라서 제거한 "
        "전례가 있다.\n"
    )

    # §1 매칭 널.
    lines.append("## §1 게이트가 무작위 제거보다 즉사를 더 잘 줄이는가 (즉사 대 승자 매칭 널)\n")
    lines.append(
        "`p_death` = 무작위 제거(20시드)의 즉사율이 게이트 이하일 확률(단측). 낮을수록 게이트가 "
        "무작위보다 낫다. `오폭%` = 게이트가 제거한 것 중 승자 비율. 유효 = 부분집합 20건 이상.\n"
    )
    lines.append(
        "| TF | 구간 | 특징 | 제거 | 문턱 | n | 기본즉사 | 게이트즉사 | 무작위즉사 | p_death | 오폭 |\n"  # noqa: E501
        "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    for r in part_a.death_null:
        lines.append(
            f"| {r.timeframe} | {r.segment} | `{r.feature}` | {r.remove_fraction:.2f} | "
            f"{_fmt(r.threshold)} | {r.n_subset} | {_fmt(r.base_death_rate, pct=True)} | "
            f"{_fmt(r.gate_death_rate, pct=True)} | {_fmt(r.matched_death_mean, pct=True)} | "
            f"{_fmt(r.p_death)} | {_fmt(r.winner_removed_rate, pct=True)} |"
        )
    lines.append("")

    # §2 편상관 독립성.
    lines.append("## §2 존폭과 독립인가 — `tap_rsi`↔즉사 편상관(존폭 통제)\n")
    lines.append(
        "raw = 심볼 층화 순열 상관 · partial = `zone_width_atr` 잔차화 후 상관(존폭 통제 순열). "
        "편상관이 절반 이상 남고 유의하면 (a) 독립, 붕괴하면 (b) 공선.\n"
    )
    for timeframe in DEFAULT_TIMEFRAMES:
        v = independence_verdict(part_a.corr, timeframe=timeframe)
        lines.append(f"* {v}")
    lines.append("")
    lines.append(
        "| TF | 구간 | 특징 | n | raw corr | raw p | partial | p_partial | 가설 |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    for cr in part_a.corr:
        if cr.feature not in GATE_FEATURES:
            continue
        arrow = "○" if cr.direction_matches else ("·" if cr.hypothesis_sign == 0 else "✗")
        lines.append(
            f"| {cr.timeframe} | {cr.segment} | `{cr.feature}` | {cr.n} | "
            f"{_fmt(cr.correlation, sign=True)} | {_fmt(cr.p_value)} | "
            f"{_fmt(cr.partial_correlation, sign=True)} | {_fmt(cr.p_partial)} | {arrow} |"
        )
    lines.append("")

    # §3-bis 지속-심화.
    lines.append("## §3-bis 지속-심화 추세 (사용자 정정 2026-07-29 · 닫힌 봉 · 룩어헤드 없음)\n")
    lines.append(
        'RSI-EMA 기울기가 여러 봉에 걸쳐 "0에서 계속 음수로 벌어지는지"의 세 조작화 × '
        f"N∈{{{', '.join(map(str, DEEPEN_WINDOWS))}}}. WAN-150 스냅샷(한 봉)과 달리 지속성을 본다. "
        "가설: 지속 심화(롤오버)일수록 즉사(corr>0, slope_trend는 corr<0).\n"
    )
    lines.append("| TF | 구간 | 특징 | n | corr | p | 가설 |\n| -- | -- | -- | -- | -- | -- | -- |")
    for cr in part_a.corr:
        if cr.feature not in DEEPEN_FEATURES or cr.segment != SEGMENT_OOS:
            continue
        arrow = "○" if cr.direction_matches else ("·" if cr.hypothesis_sign == 0 else "✗")
        lines.append(
            f"| {cr.timeframe} | {cr.segment} | `{cr.feature}` | {cr.n} | "
            f"{_fmt(cr.correlation, sign=True)} | {_fmt(cr.p_value)} | {arrow} |"
        )
    lines.append("")

    # leave-one-out.
    if part_a.loo:
        lines.append("## leave-one-out — 게이트 OOS 즉사율(심볼 하나씩 빼고, 제거 1/3)\n")
        lines.append("| 특징 | TF | 제외 | 게이트 즉사% |\n| -- | -- | -- | -- |")
        for feature, tf, drop, rate in part_a.loo:
            lines.append(f"| `{feature}` | {tf} | {_bare(drop)} | {_fmt(rate, pct=True)} |")
        lines.append("")

    # Part B 손익.
    lines.append("## Part B — 실제 시퀀싱 손익 (게이트 off vs on vs 무작위 제거)\n")
    if not pnl_rows:
        lines.append(
            "⚠️ **Part B 미실행** — 후보 재빌드(15m·6년 초선형)가 무거워 별도 실행/후속 커밋으로 "
            "낸다: `python -m backtest.wan210_reverse_rsi_filter --part pnl --timeframes 1h,4h`.\n"
        )
    else:
        tests = pnl_test_rows(pnl_rows)
        lines.append(
            "심볼평균 `total_return`(유효 거래 20건 이상). `p_return` = 무작위 제거가 필터 "
            "이상으로 벌 확률(단측). 필터가 무작위를 못 넘으면 채택 근거 없음.\n"
        )
        lines.append(
            "| 렌즈 | TF | 구간 | 특징 | 제거 | n | default | filter | 무작위 | p_ret | "
            "def즉사% | filt즉사% |\n"
            "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |"
        )
        for t in tests:
            lines.append(
                f"| {t.lens} | {t.timeframe} | {t.segment} | `{t.feature}` | "
                f"{t.remove_fraction:.2f} | "
                f"{t.n_symbols} | {_fmt(t.default_return, pct=True, sign=True)} | "
                f"{_fmt(t.filter_return, pct=True, sign=True)} | "
                f"{_fmt(t.matched_return_mean, pct=True, sign=True)} | {_fmt(t.p_return)} | "
                f"{_fmt(t.default_death, pct=True)} | {_fmt(t.filter_death, pct=True)} |"
            )
        lines.append("")

    lines.append("## ⚠️ 인용 경고\n")
    lines.append(
        "* 🚨 **이건 「RSI 상한 게이트」다** — WAN-123이 RSI 재탭 게이트를 순손해라서 제거했다.\n"
        "* 전부 `baseline`(낙관) 렌즈 위 · Part B가 `pen_5bp` 재검을 낸다(게이트가 마진 체결에 "
        "기대는지).\n"
        "* **「엣지 없음」(WAN-84/88/111/114/124/145/151) 뒤집기 인용 금지** — 다른 질문이다.\n"
        "* leave-one-out 병기(ETH·SOL 편중 전례) · 채택은 사용자 결정(개발자 임의 착수 금지).\n"
        "* 기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지(측정 전용).\n"
    )
    lines.append(f"\n원자료: `{corr_csv}` · `{death_csv}` · `{pnl_csv}`.\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

CORR_CSV = REPORTS_DIR / "wan210_corr.csv"
DEATH_CSV = REPORTS_DIR / "wan210_death_null.csv"
PNL_CSV = REPORTS_DIR / "wan210_pnl.csv"
SUMMARY_MD = REPORTS_DIR / "wan210_summary.md"


def _load_pnl_csv() -> list[PnlRow]:
    if not PNL_CSV.exists():
        return []
    df = pd.read_csv(PNL_CSV)
    return [
        PnlRow(**{k: (None if pd.isna(v) else v) for k, v in rec.items()})
        for rec in df.to_dict(orient="records")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-210 과열 반등 회피 필터")
    parser.add_argument("--part", choices=["null", "pnl", "both"], default="both")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--lenses", type=str, default=f"{LENS_PRIMARY},{LENS_PEN5}")
    parser.add_argument("--permutations", type=int, default=_PERMUTATIONS)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--labeled-csv", type=Path, default=REPORTS_DIR / "wan150_labeled.csv")
    parser.add_argument("--append", action="store_true", help="Part B: 기존 pnl CSV에 추가")
    parser.add_argument("--from-csv", action="store_true", help="기존 CSV로 요약만 재생성")
    parser.add_argument("--checksum", action="store_true", help="라벨/생산 정합 검산만")
    args = parser.parse_args(argv)

    if args.checksum:
        off, prod, wan150, ok = checksum(
            labeled_csv=args.labeled_csv, start=args.start, end=args.end, db_path=args.db
        )
        print(
            f"[wan210] checksum BTC 1h: gate_off={off} production={prod} "
            f"wan150_labeled={wan150} match={ok}"
        )
        return 0 if ok else 1

    if args.from_csv:
        part_a = run_part_a(
            labeled_csv=args.labeled_csv,
            start=args.start,
            end=args.end,
            permutations=args.permutations,
            db_path=args.db,
        )
        _write_csv(_rows_to_frame(part_a.corr), CORR_CSV)
        _write_csv(_rows_to_frame(part_a.death_null), DEATH_CSV)
        summary = build_summary_markdown(
            part_a, _load_pnl_csv(), corr_csv=CORR_CSV, death_csv=DEATH_CSV, pnl_csv=PNL_CSV
        )
        SUMMARY_MD.write_text(summary, encoding="utf-8")
        print(f"[wan210] summary → {SUMMARY_MD}")
        return 0

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    timeframes = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())
    lenses = tuple(x.strip() for x in args.lenses.split(",") if x.strip())

    pnl_rows: list[PnlRow] = []
    if args.part in ("pnl", "both"):
        new_rows = run_part_b(
            symbols=symbols,
            timeframes=timeframes,
            start=args.start,
            end=args.end,
            lenses=lenses,
            db_path=args.db,
        )
        existing = _load_pnl_csv() if args.append else []
        # append 시 같은 좌표(심볼·TF·렌즈)는 새 값으로 대체.
        new_keys = {(r.symbol, r.timeframe, r.lens) for r in new_rows}
        kept = [r for r in existing if (r.symbol, r.timeframe, r.lens) not in new_keys]
        pnl_rows = kept + new_rows
        _write_csv(_rows_to_frame(pnl_rows), PNL_CSV)
        print(f"[wan210] pnl rows={len(pnl_rows)} → {PNL_CSV}")
    else:
        pnl_rows = _load_pnl_csv()

    if args.part in ("null", "both"):
        part_a = run_part_a(
            labeled_csv=args.labeled_csv,
            start=args.start,
            end=args.end,
            permutations=args.permutations,
            db_path=args.db,
        )
        _write_csv(_rows_to_frame(part_a.corr), CORR_CSV)
        _write_csv(_rows_to_frame(part_a.death_null), DEATH_CSV)
    else:
        part_a = run_part_a(
            labeled_csv=args.labeled_csv,
            start=args.start,
            end=args.end,
            permutations=args.permutations,
            db_path=args.db,
        )

    summary = build_summary_markdown(
        part_a, pnl_rows, corr_csv=CORR_CSV, death_csv=DEATH_CSV, pnl_csv=PNL_CSV
    )
    SUMMARY_MD.write_text(summary, encoding="utf-8")
    print(f"[wan210] summary → {SUMMARY_MD}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
