"""볼린저 하단 기울기 「과열 진입 회피」 필터를 실제 손익으로 검정 (WAN-211, WAN-209 §C 후속).

## 배경 — WAN-209 §C가 부호로 뒤집은 것

WAN-209(`wan209_death_autopsy_axes`) §C가 사용자 가설("하단 밴드가 **내려갈** 때 즉사")을
**부호로 반증**했다: 실제는 **양의 상관** — 하단 밴드(SMA20 − 2σ)가 **오를**수록(σ를 위로
밀며 강세·과열) 롱 진입 **즉사**(손절 & MFE<0.5R)가 잦다(15m OOS 상관 +0.089~+0.093,
존폭·손절폭 통제 편상관 +0.072, p=0.001, 9종목 편중 아님 — 판정 (a)). WAN-150 §3의 "강세/
과열에 올라타면 즉사"와 같은 방향이고 WAN-210의 "과열 반등 회피"와 통한다.

이 이슈는 그 신호로 **「과열 진입」을 걸러내면 순수익이 실제로 오르는가**를 손익으로 검정한다.
상관을 잘 가르는 것과, 그 거래를 걸렀을 때 돈을 더 버는 것은 별개 질문이다.

## 두 갈래 — 무엇이 싼가 (WAN-210 패턴)

라벨은 이미 `wan150_labeled.csv`에 있다(핀 없는 오늘 엔진 · 9종목 · 못 박은 6년 · 필터 1.28
켜짐). `band_lower_slope`는 그 CSV에 없지만 **탭 직전 확정봉만으로**(HTF 프레임, 서브스텝
없이) 다시 계산할 수 있다 — WAN-210이 §3-bis 특징을 즉석 계산한 것과 같은 싼 길이다.

* **Part A(싼 길 · 3 TF 전부, 1분봉 서브스텝 없음)** — `wan150_labeled.csv` 위에서:
  - §1 게이트가 **즉사를 무작위 제거보다 더 잘 줄이는가**(즉사 대 승자 매칭 널, OOS).
  - §2 **존폭·손절폭과 독립인가** — `band_lower_slope`↔즉사 상관을 `zone_width_atr`·
    `stop_width_atr`로 통제한 **편상관** + 심볼 층화 순열 → (a) 독립 / (b) 공선 / (c) 부분.
  - leave-one-out(ETH·SOL 편중 전례 — §C는 드물게 편중 아님).

* **Part B(비싼 길 · 후보 재빌드) — 실제 시퀀싱 손익 (이 이슈의 주 산출물)** — 게이트를 후보
  단계에 걸고(값 ≥ 문턱이면 진입 스킵 = 과열 진입 회피) **재시퀀싱**해
  `total_return`·MDD·수익/MDD·거래 수·즉사율을 필터 off·무작위 제거와 대조한다. 문턱은 IS에서
  고르고 OOS 검증(상위 1/4·1/3·1/2 전수). `pen_5bp` 체결 보수화 재검 포함. 후보 재빌드가
  15m·6년에서 초선형이라 이 길만 `--timeframes`/`--append`로 나눠 돈다.

## ⚠️ 사전 무게 · 인용 경고

* 🚨 **(b) net loss·무영향으로 닫힐 공산이 크다** — WAN-210이 정확히 같은 방향의 신호(과열
  반등 회피)를 손익으로 재서 net loss (b)로 닫았고, WAN-209 §C도 결정문서가 "WAN-210과 같은
  방향"이라 명시했다. **그래서 첫 단계가 상관이 아니라 손익 게이트다.**
* 🚨 **이건 사실상 「볼린저 상한 게이트」다** — 볼린저는 진입가를 만드는 도구 자체(WAN-131:
  기여의 84%가 선별 아닌 가격)라 「선별 대 가격」을 못 가른다. "선별 축을 찾았다"로 인용 금지.
* 전부 `baseline`(낙관) 렌즈 위 → `pen_5bp` 재검이 관문이다(게이트가 마진 체결에 기대는지).
* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지** — 다른 질문
  (*이미 진입한 손절 중 즉사를 진입 시점에 거르는가*)이다.
* **측정 전용 — 기본값·토대·실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`)는 건드리지 않는다.**

라벨 정합성: Part B가 후보를 재빌드할 때 게이트 off(default) 팔은 `harness.run_once` 생산과
거래 수가 일치해야 한다(검산: `--checksum`) — funding=False로 뽑는 것도 WAN-150/210과 같다.

재현:
    python -m backtest.wan211_band_slope_filter --part null      # Part A (싼 길, 3 TF)
    python -m backtest.wan211_band_slope_filter --part pnl --timeframes 1h,4h
    python -m backtest.wan211_band_slope_filter --part pnl --timeframes 15m --append
    python -m backtest.wan211_band_slope_filter --from-csv        # 요약만 재생성
    python -m backtest.wan211_band_slope_filter --checksum        # 라벨/생산 정합 검산
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
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
    classify,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import sma, stdev
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

#: 볼린저 하단 밴드 정의 (WAN-209 §C와 동일: SMA20 − 2σ).
_BAND_SMA = 20
_BAND_STD = 2.0
#: 기울기 창(봉). WAN-209 §C와 동일.
_BAND_SLOPE_WINDOWS: tuple[int, ...] = (3, 5)

#: §1/Part B 상한 게이트 후보 — 값이 문턱 이상이면 진입 스킵(과열 진입 회피).
GATE_FEATURES: tuple[str, ...] = tuple(
    f"band_lower_slope_{k}_{scale}" for k in _BAND_SLOPE_WINDOWS for scale in ("atr", "pct")
)

#: 게이트 특징의 가설 부호 — 즉사일수록 값이 크다(WAN-209 §C가 부호 반증: 양의 기울기 → 즉사).
GATE_HYPOTHESIS_SIGN: dict[str, int] = {f: +1 for f in GATE_FEATURES}

#: 편상관 통제 축 — 존폭·손절폭 기하(WAN-209 §C가 통제한 두 축).
CONTROL_FEATURES: tuple[str, ...] = ("zone_width_atr", "stop_width_atr")

#: 상위에서 잘라내는 비율(문턱 = IS 분위 1−fraction). 전수 병기(IS 1등 뽑기 금지, WAN-90/161).
REMOVE_FRACTIONS: tuple[float, ...] = (0.25, 1.0 / 3.0, 0.5)

_PERMUTATIONS = 2000
_SEED = 211
_MIN_N = MIN_TRADES_FOR_PNL  # 유효 셀 = 거래 20건(WAN-84 기준, wan150과 같음).


# --------------------------------------------------------------------------- #
# 하단 밴드 기울기 특징 (HTF 프레임만, 서브스텝 없음)
# --------------------------------------------------------------------------- #


def band_lower_slope_features(
    band_lower: Sequence[float],
    closes: Sequence[float],
    atr14: Sequence[float],
    prev: int,
) -> dict[str, float | None]:
    """탭 직전 확정봉(`prev`)의 하단 밴드 기울기 — ÷ATR·가격% 두 척도 × 두 창.

    WAN-209 `_Wan209Extractor._band_lower_slope`와 동일한 산식(기울기 = (now−past)/k). 닫힌
    봉만 쓰므로 룩어헤드가 구조적으로 없다. 워밍업/경계로 값이 모자라면 None(조용한 통과 금지).
    """
    out: dict[str, float | None] = dict.fromkeys(GATE_FEATURES)
    if prev < 0 or prev >= len(band_lower):
        return out
    atr_now = atr14[prev]
    close_now = closes[prev]
    for k in _BAND_SLOPE_WINDOWS:
        if prev - k < 0:
            continue
        now, past = band_lower[prev], band_lower[prev - k]
        if _isnan(now) or _isnan(past):
            continue
        slope = (now - past) / k
        if not _isnan(atr_now) and atr_now > 0:
            out[f"band_lower_slope_{k}_atr"] = slope / atr_now
        if not _isnan(close_now) and close_now > 0:
            out[f"band_lower_slope_{k}_pct"] = slope / close_now
    return out


@dataclass
class _BandSlopeExtractor:
    """라벨된 탭 시각에서 하단 밴드 기울기 특징을 낸다 — HTF 프레임만, 서브스텝 없음."""

    time_to_pos: dict[int, int]
    band_lower: list[float]
    closes: list[float]
    atr14: list[float]

    @classmethod
    def build(cls, frame: pd.DataFrame) -> _BandSlopeExtractor:
        base = _FeatureExtractor.build(frame)
        band_mid = sma(frame, length=_BAND_SMA)
        band_sd = stdev(frame, length=_BAND_SMA)
        lower = band_mid - _BAND_STD * band_sd
        return cls(
            time_to_pos=dict(base.time_to_pos),
            band_lower=[float(v) for v in lower.tolist()],
            closes=list(base.closes),
            atr14=list(base.atr14),
        )

    def features_for_time(self, trigger_time: int) -> dict[str, float | None]:
        pos = self.time_to_pos.get(int(trigger_time))
        if pos is None or pos < 1:
            return dict.fromkeys(GATE_FEATURES)
        return band_lower_slope_features(self.band_lower, self.closes, self.atr14, pos - 1)


# --------------------------------------------------------------------------- #
# 검정 통계 — 상관 + 편상관(다중 통제) + 심볼 층화 순열
# --------------------------------------------------------------------------- #


def _residualize(values: Sequence[float], controls: Sequence[Sequence[float]]) -> list[float]:
    """`values`에서 `controls`(하나 이상)의 선형 성분을 뺀 OLS 잔차(절편 포함)."""
    y = np.asarray(values, dtype=float)
    if not controls:
        return [float(v - y.mean()) for v in y]
    cols = [np.asarray(c, dtype=float) for c in controls]
    x = np.column_stack([np.ones(len(y)), *cols])
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    return [float(v) for v in resid]


def _partial_corr(
    values: Sequence[float], controls: Sequence[Sequence[float]], target: Sequence[float]
) -> float | None:
    """corr(values, target | controls) — 둘 다 controls로 잔차화한 뒤 상관."""
    if len(values) < 3:
        return None
    rv = _residualize(values, controls)
    rt = _residualize([float(t) for t in target], controls)
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
    controls: Sequence[Sequence[float]] | None = None,
    permutations: int = _PERMUTATIONS,
    seed: int = _SEED,
) -> _StrataPerm:
    """corr(또는 편상관)의 심볼 층화 라벨 순열 검정(WAN-117/150/210 자)."""
    n = len(values)
    if n < _MIN_N:
        return _StrataPerm(None, None, n)
    tgt = [float(t) for t in target]
    actual = (
        _partial_corr(values, controls, tgt) if controls is not None else _corr(list(values), tgt)
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
            _partial_corr(values, controls, shuffled)
            if controls is not None
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
    """존폭·손절폭 통제 편상관(§2 독립성)."""
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


def _pooled_quantile(values: Sequence[float], q: float) -> float | None:
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    return float(pd.Series(vals).quantile(q))


def corr_rows_from_labeled(df: pd.DataFrame, *, permutations: int = _PERMUTATIONS) -> list[CorrRow]:
    """§1/§2 게이트 특징의 즉사 대 승자 상관/편상관(존폭·손절폭 통제, TF·구간 풀링)."""
    rows: list[CorrRow] = []
    sub = df[df["label"].isin([Label.INSTANT_DEATH.value, Label.WINNER.value])]
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            cell = sub[(sub["timeframe"] == timeframe) & (sub["segment"] == segment)]
            if cell.empty:
                continue
            death_all = (cell["label"] == Label.INSTANT_DEATH.value).astype(float).tolist()
            syms_all = cell["symbol"].tolist()
            for feature in GATE_FEATURES:
                if feature not in cell.columns:
                    continue
                mask = cell[feature].notna()
                values = cell.loc[mask, feature].astype(float).tolist()
                target = [death_all[i] for i in range(len(cell)) if bool(mask.iloc[i])]
                symbols = [syms_all[i] for i in range(len(cell)) if bool(mask.iloc[i])]
                n = len(values)
                hyp = GATE_HYPOTHESIS_SIGN.get(feature, 0)
                perm = _strata_permutation(
                    values, target, symbols, permutations=permutations, seed=_SEED
                )
                partial: float | None = None
                p_partial: float | None = None
                have_controls = all(c in cell.columns for c in CONTROL_FEATURES)
                if have_controls:
                    cmask = mask
                    for c in CONTROL_FEATURES:
                        cmask = cmask & cell[c].notna()
                    cv = cell.loc[cmask, feature].astype(float).tolist()
                    controls = [cell.loc[cmask, c].astype(float).tolist() for c in CONTROL_FEATURES]
                    ct = (
                        (cell.loc[cmask, "label"] == Label.INSTANT_DEATH.value)
                        .astype(float)
                        .tolist()
                    )
                    cs = cell.loc[cmask, "symbol"].tolist()
                    pperm = _strata_permutation(
                        cv, ct, cs, controls=controls, permutations=permutations, seed=_SEED
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
    게이트 = 값 ≥ 문턱 제거(과열 진입 회피). 매칭 널 = 같은 개수(k)를 무작위 제거(시드 20개).
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


def band_slope_features_for_labeled(
    df: pd.DataFrame,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> pd.DataFrame:
    """라벨 원자료에 하단 밴드 기울기 열을 붙인다 — HTF 프레임만(서브스텝 없음, 싼 길).

    각 (심볼, TF)의 HTF 프레임을 로드해 `_BandSlopeExtractor`로 라벨된 `trigger_time`에서
    기울기 특징을 계산한다. `need_1m=False`라 1분봉 서브스텝을 돌지 않는다.
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    out = df.copy()
    for feature in GATE_FEATURES:
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
            extractor = _BandSlopeExtractor.build(harness_prepare(market.htf_df))
            for idx in out.index[mask]:
                feats = extractor.features_for_time(int(out.at[idx, "trigger_time"]))
                for feature in GATE_FEATURES:
                    out.at[idx, feature] = feats.get(feature)
    for feature in GATE_FEATURES:
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
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_params=order_block_params,
    )
    if not cands:
        return None
    frame = harness_prepare(market.htf_df)
    band = _BandSlopeExtractor.build(frame)
    feats: list[dict[str, float | None]] = []
    labels: list[Label | None] = []
    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * IS_FRACTION)
    for cand in cands:
        feats.append(band.features_for_time(cand.trigger_time))
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
# Part B 집계 · 매칭 검정
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


def _ratio(ret: float | None, mdd: float | None) -> float | None:
    """수익/MDD(위험조정). MDD가 0이거나 없으면 None."""
    if ret is None or mdd is None or mdd <= 0:
        return None
    return ret / mdd


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
    """판정 — 문장이 아니라 이 값이 정본이다(WAN-142 열거형 교훈)."""

    # P&L 판정.
    PNL_GAIN = "pnl_gain"  # (a) 필터가 순수익을 올리고 무작위를 이긴다.
    PNL_LOSS = "pnl_loss"  # (b) net loss·무영향 → 닫음.
    PNL_MIXED = "pnl_mixed"  # (c) TF·위험조정·렌즈에 갈림.
    # §2 독립성 판정.
    INDEPENDENT = "independent"
    COLLINEAR = "collinear"
    PARTIAL = "partial"
    INDETERMINATE = "indeterminate"  # 표본 부족.


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    text: str

    def __str__(self) -> str:
        return self.text


def _beats_default(t: PnlTestRow) -> bool:
    """필터 수익이 default를 넘는가(둘 다 존재할 때만)."""
    return (
        t.filter_return is not None
        and t.default_return is not None
        and t.filter_return > t.default_return
    )


def _pnl_cell_helps(t: PnlTestRow) -> bool:
    """필터가 이 셀에서 도움이 되는가 — default를 이기고 · 무작위를 이기고 · 위험조정도 개선."""
    if t.filter_return is None or t.default_return is None:
        return False
    beats_default = t.filter_return > t.default_return
    beats_random = t.p_return is not None and t.p_return <= 0.05
    fr = _ratio(t.filter_return, t.filter_mdd)
    dr = _ratio(t.default_return, t.default_mdd)
    risk_ok = fr is None or dr is None or fr >= dr
    return beats_default and beats_random and risk_ok


def pnl_verdict(
    tests: Sequence[PnlTestRow], *, timeframe: str, lens: str = LENS_PRIMARY
) -> Verdict:
    """P&L 판정 — 필터가 순수익을 올리고 무작위 제거를 이기는가 (공식 렌즈 OOS).

    (a) 유효 셀의 과반이 「도움」(default·무작위·위험조정 모두 통과)이고 `pen_5bp`에서 부호가
        뒤집히지 않음 / (b) 「도움」이 없고 필터가 default를 못 넘김(net loss·무영향) /
        (c) 그 사이(일부만 개선·위험조정 상충). 셀 = 4특징 × 3제거비율.

    표본 게이트(WAN-143 패턴): 유효 종목 수가 5개 미만이면 「판정 불가(대조군)」 — 4h처럼 20건
    게이트에 대부분 종목이 걸리면 심볼평균이 소수 종목에 기대므로 (a)/(b)/(c)를 내지 않는다.
    """
    cells = [
        t
        for t in tests
        if t.timeframe == timeframe
        and t.segment == SEGMENT_OOS
        and t.lens == lens
        and t.filter_return is not None
        and t.default_return is not None
    ]
    if not cells:
        return Verdict(VerdictKind.INDETERMINATE, f"**{timeframe}**: P&L 판정 불가(표본 부족).")
    max_symbols = max(t.n_symbols for t in cells)
    if max_symbols < 5:
        return Verdict(
            VerdictKind.INDETERMINATE,
            f"**{timeframe}**: ⚠️ 판정 불가(대조군) — 유효 종목 {max_symbols}개(20건 게이트에 "
            "대부분 탈락). 심볼평균이 소수에 기댄다.",
        )
    n = len(cells)
    n_help = sum(1 for t in cells if _pnl_cell_helps(t))
    n_beats_default = sum(1 for t in cells if _beats_default(t))
    # pen_5bp OOS에서 필터가 default를 여전히 이기는 비율(관문).
    pen = [
        t
        for t in tests
        if t.timeframe == timeframe
        and t.segment == SEGMENT_OOS
        and t.lens == LENS_PEN5
        and t.filter_return is not None
        and t.default_return is not None
    ]
    pen_holds = sum(1 for t in pen if _beats_default(t)) >= len(pen) / 2 if pen else True
    if n_help == 0 and n_beats_default <= n / 2:
        return Verdict(
            VerdictKind.PNL_LOSS,
            f"**{timeframe}**: (b) net loss·무영향 — 필터가 default를 못 넘고(도움 0/{n}) "
            "무작위 제거도 못 이긴다. 닫는다(WAN-210 선례와 같은 결말).",
        )
    if n_help >= n / 2 and pen_holds:
        return Verdict(
            VerdictKind.PNL_GAIN,
            f"**{timeframe}**: (a) 필터가 순수익·위험조정을 올리고 무작위를 이긴다"
            f"(도움 {n_help}/{n}, pen_5bp 유지). ⚠️ 채택은 사용자 결정 · baseline 낙관 렌즈 위.",
        )
    return Verdict(
        VerdictKind.PNL_MIXED,
        f"**{timeframe}**: (c) 갈린다 — 일부 셀만 개선(도움 {n_help}/{n})하거나 위험조정·"
        f"pen_5bp(유지 {'예' if pen_holds else '아니오'})에서 상충. 채택 근거 약함.",
    )


def independence_verdict(corr_rows: Sequence[CorrRow], *, timeframe: str) -> Verdict:
    """§2 — 게이트 신호의 OOS 편상관(존폭·손절폭 통제)이 살아남는가로 독립/공선 판정.

    대표 특징은 `band_lower_slope_3_atr`(§C에서 편상관이 가장 강하게 살아남은 ÷ATR 축).
    """
    rep = "band_lower_slope_3_atr"
    row = next(
        (
            r
            for r in corr_rows
            if r.timeframe == timeframe and r.segment == SEGMENT_OOS and r.feature == rep
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
            f"**{timeframe}**: (a) 존폭·손절폭 통제 후에도 편상관 유지(raw {row.correlation:+.3f} "
            f"→ partial {part:+.3f}, p={p_part:.4f}) — 기하와 부분 독립. P&L·pen_5bp 관문은 별개.",
        )
    if part_sig and (part > 0) == (row.correlation > 0):
        return Verdict(
            VerdictKind.PARTIAL,
            f"**{timeframe}**: (c) 부분 독립 — 편상관이 줄지만(raw {row.correlation:+.3f} → "
            f"partial {part:+.3f}, p={p_part:.4f}) 절반 이상 소멸, 기하가 상당 부분 설명.",
        )
    return Verdict(
        VerdictKind.COLLINEAR,
        f"**{timeframe}**: (b) 존폭·손절폭 통제로 편상관이 붕괴(raw {row.correlation:+.3f} → "
        f"partial {part:+.3f}, p={p_part:.4f}) — 게이트는 기하 필터의 재선별.",
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
    """Part A — 라벨 원자료 + 밴드 기울기 특징 위의 상관/편상관/매칭 널(서브스텝 없음)."""
    df = pd.read_csv(labeled_csv)
    df = band_slope_features_for_labeled(df, start=start, end=end, db_path=db_path)
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
                    print(f"[wan211] {norm} {timeframe} {lens}: 후보 없음")
                    continue
                cell_rows = pnl_rows_for_cell(cell, market, lens=lens)
                rows.extend(cell_rows)
                deaths = sum(1 for lab in cell.labels if lab is Label.INSTANT_DEATH)
                print(
                    f"[wan211] {norm} {timeframe} {lens}: cands={len(cell.cands)} "
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
    """게이트 off(default 팔) 후보 수 ≡ 생산 num_trades ≡ wan150 라벨 카운트 이상.

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
        market,
        params=harness.pin_invalidation_cancel(ConfluenceParams()),
        cfg=harness.build_config(timeframe),
    )
    prod = outcome.result.metrics.num_trades
    df = pd.read_csv(labeled_csv)
    wan150_count = int(((df["symbol"] == norm) & (df["timeframe"] == timeframe)).sum())
    # wan150 라벨은 END_OF_DATA·MFE결측을 뺀 값이라 시퀀서 거래 수의 부분집합이다.
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
    lines.append("# WAN-211 볼린저 하단 기울기 「과열 진입 회피」 필터 — 실제 손익으로 검정\n")
    lines.append(
        f"9종목 × {', '.join(DEFAULT_TIMEFRAMES)} · 못 박은 6년 창 **{DEFAULT_START} ~ "
        f"{DEFAULT_END}** · 오늘의 채택 기본값(핀 없음 · 필터 1.28 · `intrabar_live` · "
        "`unconditional` · 고정 1.5R · 롱 온리 · 분리 존) · 공식 렌즈 `baseline`(+ Part B "
        "`pen_5bp` 재검). 라벨은 `wan150_labeled.csv` 재사용 · funding=False(WAN-150과 같음). "
        "게이트 = **하단 밴드 기울기 ≥ IS 분위 문턱이면 진입 스킵**(과열 진입 회피, WAN-209 §C가 "
        "부호 반증한 방향: 하단 밴드가 오를수록 즉사). 🚨 사실상 「볼린저 상한 게이트」 — "
        "볼린저는 진입가를 만드는 도구 자체라(WAN-131) 「선별 대 가격」을 못 가른다.\n"
    )

    # Part B P&L 판정 (주 산출물이므로 먼저).
    lines.append(
        "## Part B — 실제 시퀀싱 손익 판정 (주 산출물 · 게이트 off vs on vs 무작위 제거)\n"
    )
    if not pnl_rows:
        lines.append(
            "⚠️ **Part B 미실행** — 후보 재빌드(15m·6년 초선형)가 무거워 별도 실행/후속 커밋으로 "
            "낸다: `python -m backtest.wan211_band_slope_filter --part pnl --timeframes 1h,4h` → "
            "`... --timeframes 15m --append`.\n"
        )
        tests: list[PnlTestRow] = []
    else:
        tests = pnl_test_rows(pnl_rows)
        for timeframe in DEFAULT_TIMEFRAMES:
            v = pnl_verdict(tests, timeframe=timeframe)
            lines.append(f"* {v}")
        lines.append("")
        lines.append(
            "심볼평균 `total_return`(유효 거래 20건 이상). `p_ret` = 무작위 제거가 필터 이상으로 "
            "벌 확률(단측). 필터가 무작위를 못 넘으면 채택 근거 없음. `수익/MDD` = 위험조정.\n"
        )
        lines.append(
            "| 렌즈 | TF | 구간 | 특징 | 제거 | n | default | filter | 무작위 | p_ret | "
            "def수익/MDD | filt수익/MDD | def즉사% | filt즉사% |\n"
            "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |"
        )
        for t in tests:
            lines.append(
                f"| {t.lens} | {t.timeframe} | {t.segment} | `{t.feature}` | "
                f"{t.remove_fraction:.2f} | {t.n_symbols} | "
                f"{_fmt(t.default_return, pct=True, sign=True)} | "
                f"{_fmt(t.filter_return, pct=True, sign=True)} | "
                f"{_fmt(t.matched_return_mean, pct=True, sign=True)} | {_fmt(t.p_return)} | "
                f"{_fmt(_ratio(t.default_return, t.default_mdd))} | "
                f"{_fmt(_ratio(t.filter_return, t.filter_mdd))} | "
                f"{_fmt(t.default_death, pct=True)} | {_fmt(t.filter_death, pct=True)} |"
            )
        lines.append("")

    # §1 매칭 널 (즉사율).
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
    lines.append("## §2 존폭·손절폭과 독립인가 — 하단 밴드 기울기↔즉사 편상관(기하 통제)\n")
    lines.append(
        "raw = 심볼 층화 순열 상관 · partial = `zone_width_atr`·`stop_width_atr` 잔차화 후 상관"
        "(기하 통제 순열). 편상관이 절반 이상 남고 유의하면 (a) 독립, 붕괴하면 (b) 공선.\n"
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
        arrow = "○" if cr.direction_matches else ("·" if cr.hypothesis_sign == 0 else "✗")
        lines.append(
            f"| {cr.timeframe} | {cr.segment} | `{cr.feature}` | {cr.n} | "
            f"{_fmt(cr.correlation, sign=True)} | {_fmt(cr.p_value)} | "
            f"{_fmt(cr.partial_correlation, sign=True)} | {_fmt(cr.p_partial)} | {arrow} |"
        )
    lines.append("")

    # leave-one-out.
    if part_a.loo:
        lines.append("## leave-one-out — 게이트 OOS 즉사율(심볼 하나씩 빼고, 제거 1/3)\n")
        lines.append("| 특징 | TF | 제외 | 게이트 즉사% |\n| -- | -- | -- | -- |")
        for feature, tf, drop, rate in part_a.loo:
            lines.append(f"| `{feature}` | {tf} | {_bare(drop)} | {_fmt(rate, pct=True)} |")
        lines.append("")

    lines.append("## ⚠️ 인용 경고\n")
    lines.append(
        "* 🚨 **이건 「볼린저 상한 게이트」다** — 볼린저는 진입가를 만드는 도구 자체(WAN-131: "
        '기여의 84%가 선별 아닌 가격)라 「선별 대 가격」을 못 가른다. "선별 축을 찾았다"로 인용 '
        "금지.\n"
        "* 🚨 WAN-210이 같은 방향 신호(과열 반등 회피)를 손익으로 재서 net loss (b)로 닫은 선례.\n"
        "* 전부 `baseline`(낙관) 렌즈 위 · Part B가 `pen_5bp` 재검을 낸다(게이트가 마진 체결에 "
        "기대는지).\n"
        "* **「엣지 없음」(WAN-84/88/111/114/124/145/151) 뒤집기 인용 금지** — 다른 질문이다.\n"
        "* leave-one-out 병기(ETH·SOL 편중 전례 · §C는 드물게 편중 아님) · 채택은 사용자 결정.\n"
        "* 기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지(측정 전용).\n"
    )
    lines.append(f"\n원자료: `{corr_csv}` · `{death_csv}` · `{pnl_csv}`.\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

CORR_CSV = REPORTS_DIR / "wan211_corr.csv"
DEATH_CSV = REPORTS_DIR / "wan211_death_null.csv"
PNL_CSV = REPORTS_DIR / "wan211_pnl.csv"
SUMMARY_MD = REPORTS_DIR / "wan211_summary.md"


def _load_pnl_csv() -> list[PnlRow]:
    if not PNL_CSV.exists():
        return []
    df = pd.read_csv(PNL_CSV)
    return [
        PnlRow(**{k: (None if pd.isna(v) else v) for k, v in rec.items()})
        for rec in df.to_dict(orient="records")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-211 볼린저 하단 기울기 과열 진입 회피 필터")
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
            f"[wan211] checksum BTC 1h: gate_off={off} production={prod} "
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
        print(f"[wan211] summary → {SUMMARY_MD}")
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
        print(f"[wan211] pnl rows={len(pnl_rows)} → {PNL_CSV}")
    else:
        pnl_rows = _load_pnl_csv()

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
        part_a, pnl_rows, corr_csv=CORR_CSV, death_csv=DEATH_CSV, pnl_csv=PNL_CSV
    )
    SUMMARY_MD.write_text(summary, encoding="utf-8")
    print(f"[wan211] summary → {SUMMARY_MD}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
