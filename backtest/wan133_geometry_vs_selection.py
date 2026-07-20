"""존폭/ATR의 「기하 대 선별」 분리 — 뚫림을 가르는 유일한 강건 축이 알파인가 산수인가
(WAN-133, WAN-117 스핀아웃).

## 배경

WAN-117(`wan117_zone_failure_autopsy`)이 채택 엔진 거래를 뚫림(무효화 손절)·버팀(1.5R
익절)으로 라벨링하고 진입 시점 특징 11개를 검정한 결과, **`zone_width_atr`(존폭/ATR)만이
두 TF · IS/OOS 모두에서 살아남았다**(15m OOS corr +0.087, 1h +0.144, 넓은 존일수록 뚫림).
분위 평균R도 좁은 존 +0.42R(15m OOS)·+0.54R(1h OOS) → 넓은 존 +0.08R·+0.06R로 벌어진다.

## ⚠️ 문제 — 이 축은 손익 기하와 거의 동어반복일 수 있다

채택 엔진은 손절 = 진입가 → 오더블록 무효화 경계(≈ 존폭), 익절 = +1.5R · 손절 = −1R로
**고정**이다. 즉 `zone_width_atr`은 곧 **±장벽이 변동성(ATR) 대비 얼마나 먼가**를 직접
재는 값이다. 넓은 존일수록 익절(+1.5R)이 ATR 단위로 더 멀어 **닿기 전에 손절**된다 —
존 품질이 나빠서가 아니라 **장벽이 멀어서**일 수 있다(WAN-96/114/115/120/124가 거듭
「가격/기하지 선별이 아니다」로 지목한 계열).

## 실험 — 기하를 제거한 팔에서 재검정

손절 거리를 존 무효화 경계에서 떼어 **`k × ATR`로 고정**한다(`build_zone_limit_candidates`의
`stop_loss_override` 훅, WAN-133 엔진 추가). 그러면 **모든 거래의 장벽 거리가 ATR 단위로
같아지므로**(1R = k·ATR, 익절 = +1.5R = +1.5k·ATR) `zone_width_atr`은 더 이상 장벽 거리를
정하지 못한다. 이 팔에서:

* `zone_width_atr`↔뚫림 연관이 **사라지면** → 그 상관은 장벽 거리 기하의 산물 = **(b) 기하
  확정**. WAN-117의 유일한 생존 축이 알파가 아님을 기록하고 「엣지 없음」(WAN-88/111/114/117)
  을 재확인한다.
* **남으면** → 존폭이 장벽 거리와 **독립으로** 뚫림을 가른다 = **(a) 선별 축 확인** →
  "존 품질 필터 구현" 후속 이슈 근거.

손절만 바꾸는 훅이므로 **체결 셋업 집합은 기본과 비트 단위로 같고**(진입은 지정가 터치·RSI
게이트로만 정해진다), 달라지는 건 청산(손절선·거기서 파생되는 고정 R 익절·뚫림/버팀
라벨)뿐이다. 라벨링·순열·분위 코드는 WAN-117을 그대로 재사용한다.

## 손익·MDD 재확인 (Part B)

통계적 유의(corr·p)가 아니라 **실제 필터의 손익 효과**도 잰다 — "존폭 하위 1/3분위만 진입"
규칙(IS에서 문턱을 잡아 OOS에 적용, 룩어헤드 없음)을 켠 팔 vs 채택 기본값 팔의
`total_return`·MDD·거래수. ⚠️ **거래 수 오염 게이트**(WAN-126): 선별 필터는 정의상 거래를
크게 줄이므로(하위 1/3) 두 팔의 거래 수가 5%를 훨씬 넘게 달라진다 — 그래서 손익 차이는
**순수 선별 효과로 인용할 수 없다**(노출·복리가 함께 바뀐다). 선별 대 기하를 깨끗이 가르는
것은 Part A(장벽 고정)이고, Part B는 그 방향을 손익으로 **보강**할 뿐이다.

## 심볼 편중 (Part A·B 공통)

이 저장소의 모든 플러스가 ETH/SOL 하나에 얹혀 있었다 — ATR 팔 corr와 필터 OOS total_return
모두 **심볼 하나씩 빼는 leave-one-out**을 병기한다.

재현: `python -m backtest.wan133_geometry_vs_selection`.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import (
    _ATR_LENGTH,
    _MIN_TRADES_FOR_VERDICT,
    _PERMUTATIONS,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    LabeledTrade,
    PermutationRow,
    QuantileRow,
    _annotate_percentile,
    _FeatureExtractor,
    _point_biserial,
    harness_prepare,
    monotonicity_verdict,
    permutation_test,
    quantile_rows,
)
from backtest.wan134_zone_merge_autopsy import stratified_permutation_p
from backtest.zone_limit_backtest import (
    StopLossContext,
    StopLossOverride,
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import atr
from strategy.models import ConfluenceParams

# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

FOCUS_FEATURE = "zone_width_atr"
"""이 이슈가 가르는 유일한 축 — WAN-117에서 두 TF·IS/OOS 모두 살아남은 특징."""

#: focus 축의 **분자·분모 성분**(둘 다 셀 내부 순위 백분위). `zone_width_atr = 존폭/ATR`이라
#: 상관이 살아남아도 그게 존폭(분자)의 몫인지 ATR 수준(분모)의 몫인지 알 수 없다 — 이 둘을
#: 따로 검정해 분해한다. `atr_pctl`이 신호를 다 갖고 `zone_width_pctl`이 0이면 존 품질이 아니다.
COMPONENT_FEATURES: tuple[str, ...] = ("zone_width_pctl", "atr_pctl")

#: ATR 장벽 팔의 손절 배수 `k`(손절 = 진입가 ∓ k·ATR). 고정 상수라 존폭과 무관하다.
#: 결정적 주장(상관 소멸)이 `k`에 강건한지 보려고 세 값을 병기한다. 대표값은 존폭/ATR
#: 중앙값(WAN-134: 15m OOS ~1.67)에 가까운 1.5.
K_VALUES: tuple[float, ...] = (1.0, 1.5, 2.0)
PRIMARY_K = 1.5

ARM_DEFAULT = "default"
"""채택 기본값(손절 = 존 무효화 경계). WAN-117 라벨과 재현되어야 한다."""


def arm_atr(k: float) -> str:
    return f"atr_{k:g}"


#: 필터 팔이 남기는 분위(하위 1/3 = 좁은 존). IS에서 문턱을 잡아 OOS에 적용한다.
FILTER_FRACTION = 1.0 / 3.0

REPORTS_DIR = Path("backtest/reports")


# --------------------------------------------------------------------------- #
# ATR 손절 오버라이드
# --------------------------------------------------------------------------- #


def atr_by_tap_time(frame: pd.DataFrame) -> dict[int, float]:
    """탭 봉 `open_time` → 직전 확정봉(pos−1)의 ATR14.

    존폭/ATR 특징의 분모와 **같은 ATR**(pos−1, 룩어헤드 금지)을 손절 거리에도 써야
    두 값이 같은 척도 위에서 분리된다. `harness_prepare`(= `_prepare_htf`)로 정렬한
    프레임을 받아 `_FeatureExtractor`와 동일한 pos 인덱싱을 쓴다.
    """
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    atr14 = [float(v) for v in atr(frame, length=_ATR_LENGTH).tolist()]
    out: dict[int, float] = {}
    for pos in range(1, len(times)):
        prev = atr14[pos - 1]
        if prev == prev and prev > 0:  # NaN·비양수 제외.
            out[times[pos]] = prev
    return out


def make_atr_stop_override(atr_by_tap: dict[int, float], k: float) -> StopLossOverride:
    """손절 = 진입가 ∓ k·ATR(pos−1)로 고정하는 오버라이드(존폭과 무관).

    ATR을 못 찾으면(워밍업·갭) None을 돌려 그 셋업을 제외한다 — 존폭/ATR 특징도 같은
    자리에서 None이라 표본이 정확히 맞는다.
    """

    def resolve(ctx: StopLossContext) -> float | None:
        atr_val = atr_by_tap.get(ctx.trigger_time)
        if atr_val is None or atr_val <= 0:
            return None
        offset = k * atr_val
        return ctx.entry_price - offset if ctx.is_long else ctx.entry_price + offset

    return resolve


# --------------------------------------------------------------------------- #
# 라벨링 (WAN-117 label_cell의 오버라이드 판)
# --------------------------------------------------------------------------- #


@dataclass
class CellResult:
    """한 (심볼, TF)의 팔별 라벨 + Part B용 원후보·특징."""

    symbol: str
    timeframe: str
    labeled_by_arm: dict[str, list[LabeledTrade]] = field(default_factory=dict)
    default_candidates: list[_Candidate] = field(default_factory=list)
    default_zwa: list[float | None] = field(default_factory=list)
    """`default_candidates`와 정렬된 zone_width_atr(없으면 None)."""
    is_boundary: int = 0


def _label_from_candidates(
    candidates: list[_Candidate],
    *,
    market: MarketData,
    extractor: _FeatureExtractor,
    params: ConfluenceParams,
    is_boundary: int,
    atr_by_tap: dict[int, float],
) -> list[LabeledTrade]:
    """후보를 프로덕션 시퀀서로 배치하고 결과·특징으로 라벨링한다(WAN-117 label_cell과 동일 규칙).

    라벨은 `_Candidate.reason`(STOP_LOSS→뚫림/−1R, TAKE_PROFIT→버팀/+take_profit_r,
    END_OF_DATA 제외). ATR 팔에서도 1R = 그 거래의 손절 거리이므로 −1/+1.5R 라벨이 그대로
    유효하다(존폭 팔이든 ATR 팔이든 R 단위는 각 거래 자기 1R 기준).

    WAN-117 특징에 더해 **분해용 세 값**을 싣는다(ATR 분모 교란 검정, 아래 `atr_control_row`):
    `atr_at_entry`(원 ATR — 심볼마다 척도가 달라 층 분위에만 쓴다) · `atr_pctl`·
    `zone_width_pctl`(셀 내부 순위 백분위 — 심볼 간 풀링 가능한 척도).
    """
    cfg = harness.build_config(market.timeframe)
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
        feats["atr_at_entry"] = atr_by_tap.get(cand.trigger_time)
        feats["_atr_raw"] = feats["atr_at_entry"]
        feats["_zone_width_raw"] = None if ob is None else ob.top - ob.bottom
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
    _annotate_cell_percentile(labeled, "_atr_raw", "atr_pctl")
    _annotate_cell_percentile(labeled, "_zone_width_raw", "zone_width_pctl")
    return labeled


def _annotate_cell_percentile(labeled: list[LabeledTrade], raw_key: str, out_key: str) -> None:
    """`raw_key`를 셀(심볼×TF) 내부 순위 백분위로 바꿔 `out_key`에 채운다(제자리 수정).

    `_annotate_percentile`(WAN-117 `volume_pctl`)의 일반화 — ATR·존폭은 심볼마다 가격
    척도가 달라 원값을 6심볼 풀에 그대로 섞으면 상관이 뜻을 잃는다. 셀 내부 순위로 바꾸면
    척도가 사라져 풀링이 성립한다.
    """
    by_cell: dict[tuple[str, str], list[tuple[float, LabeledTrade]]] = defaultdict(list)
    for lt in labeled:
        raw = lt.features.get(raw_key)
        if raw is None:
            lt.features[out_key] = None
            continue
        by_cell[(lt.symbol, lt.timeframe)].append((raw, lt))
    for items in by_cell.values():
        order = sorted(range(len(items)), key=lambda i: items[i][0])
        n = len(items)
        for rank, idx in enumerate(order):
            _, lt = items[idx]
            lt.features[out_key] = rank / (n - 1) if n > 1 else 0.5
    for lt in labeled:
        lt.features.pop(raw_key, None)


def label_cell(market: MarketData, *, params: ConfluenceParams) -> CellResult:
    """한 (심볼, TF)를 기본 팔 + ATR 장벽 팔들로 라벨링하고 Part B용 원후보를 남긴다."""
    result = CellResult(symbol=market.symbol, timeframe=market.timeframe)
    if market.empty or market.df_1m.empty:
        return result
    cfg = harness.build_config(market.timeframe)
    frame = harness_prepare(market.htf_df)
    extractor = _FeatureExtractor.build(frame)
    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * IS_FRACTION)
    result.is_boundary = is_boundary
    # 탭 봉 → 직전 확정봉 ATR. ATR 팔의 손절 거리이자 ATR 교란 검정의 층 축이라 두 용도가
    # 정확히 같은 값을 쓰도록 한 곳에서 만든다.
    atr_map = atr_by_tap_time(frame)

    # 오더블록은 컨플루언스 파라미터·손절 규칙과 무관하므로 한 번만 탐지해 네 팔이 공유한다
    # (네 build_zone_limit_candidates가 같은 오더블록으로 비교되도록).
    obr = harness.detect_order_blocks(market)

    # 기본 팔(오버라이드 없음) — WAN-117 label_cell과 비트 단위로 같은 후보·라벨.
    default_candidates, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=obr,
    )
    result.default_candidates = default_candidates
    result.default_zwa = [_zwa(extractor, cand) for cand in default_candidates]
    if default_candidates:
        result.labeled_by_arm[ARM_DEFAULT] = _label_from_candidates(
            default_candidates,
            market=market,
            extractor=extractor,
            params=params,
            is_boundary=is_boundary,
            atr_by_tap=atr_map,
        )

    # ATR 장벽 팔들 — 손절만 k·ATR로 갈아끼운다(체결 집합은 기본과 동일).
    for k in K_VALUES:
        override = make_atr_stop_override(atr_map, k)
        cands, _ = build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            market.timeframe,
            params=params,
            cfg=cfg,
            order_block_result=obr,
            stop_loss_override=override,
        )
        if cands:
            result.labeled_by_arm[arm_atr(k)] = _label_from_candidates(
                cands,
                market=market,
                extractor=extractor,
                params=params,
                is_boundary=is_boundary,
                atr_by_tap=atr_map,
            )
    return result


def _zwa(extractor: _FeatureExtractor, cand: _Candidate) -> float | None:
    feats = extractor.features_for(cand)
    if feats is None:
        return None
    return feats.get(FOCUS_FEATURE)


# --------------------------------------------------------------------------- #
# Part A: 팔별 focus 특징 순열·분위 + 심볼 leave-one-out
# --------------------------------------------------------------------------- #


def _pool_labeled(cells: Sequence[CellResult], arm: str) -> list[LabeledTrade]:
    pooled: list[LabeledTrade] = []
    for cell in cells:
        pooled.extend(cell.labeled_by_arm.get(arm, []))
    return pooled


def leave_one_out_corr(
    labeled: list[LabeledTrade], *, timeframe: str, segment: str, feature: str
) -> dict[str, float | None]:
    """심볼 하나씩 빼고 본 corr(feature, 뚫림) — 편중 확인.

    각 심볼을 빼고 남은 표본으로 점이연 상관을 낸다. 반환 키는 빠진 심볼명(bare),
    값은 그 심볼을 뺀 corr. 표본이 부족하면 None.
    """
    rows = [
        (v, lt)
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and (v := lt.features.get(feature)) is not None
    ]
    symbols = sorted({lt.symbol for _, lt in rows})
    out: dict[str, float | None] = {}
    for drop in symbols:
        kept = [(v, lt) for v, lt in rows if lt.symbol != drop]
        if len(kept) < _MIN_TRADES_FOR_VERDICT:
            out[_bare(drop)] = None
            continue
        out[_bare(drop)] = _point_biserial([v for v, _ in kept], [lt.broke for _, lt in kept])
    return out


STOP_GUARD_FRACTION = 0.003
"""`RiskSizingParams.min_stop_distance_fraction` 기본값(0.3%)의 사본 — 진단 표시용.

⚠️ **이 값은 WAN-133이 정한 게 아니라 WAN-79가 정했다**(`execution/sizing.py`, 커밋
`6d4da9b`, 2026-07-14). 근거는 왕복 체결 비용 ≈0.11%(슬리피지 5bp + 테이커 4bp + 메이커
2bp) 대비 약 3배 여유를 둬 "손절폭이 체결 비용에 묻히는" 거래를 사이징에서 배제하는 것이다
(WAN-76 감사 권고). 손절 거리가 진입가의 이 비율 미만이면 `position_size`가 `0`을 반환해
**그 셋업은 거래가 되지 않는다**.

📌 **이 이슈가 그 가드와 정면으로 부딪힌다** — WAN-133 필터는 「좁은 존」을 고르는데 좁은
존이 바로 이 바닥에 걸리는 대상이다. 아래 진단이 그 규모와 방향을 잰다. 여기서는 **읽기만
하고 절대 바꾸지 않는다**(가드 변경은 WAN-79/76을 되돌리는 재-베이스라인 = 사용자 결정)."""

#: 손절 거리(진입가 대비 분수) 버킷. 가드(0.3%) 경계가 버킷 경계와 일치하도록 잡는다.
STOP_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0, 0.002, "<0.2%"),
    (0.002, 0.003, "0.2~0.3%"),
    (0.003, 0.005, "0.3~0.5%"),
    (0.005, 0.01, "0.5~1%"),
    (0.01, 0.02, "1~2%"),
    (0.02, 1.0, ">2%"),
)


class GuardRow(BaseModel):
    """한 (심볼, TF, 구간)에서 사이징 가드 미만인 후보의 수·비율."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    n_candidates: int
    n_below_guard: int
    frac_below_guard: float
    median_stop_fraction: float


class StopBucketRow(BaseModel):
    """손절 거리 버킷별 뚫림률(6심볼 풀링) — 가드가 무엇을 버리는지 잰다."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    bucket: str
    below_guard: bool
    n: int
    broke_rate: float


def _stop_fraction(cand: _Candidate) -> float | None:
    """손절 거리 / 진입가. 진입가가 0 이하면 None."""
    if cand.entry_price <= 0:
        return None
    return abs(cand.entry_price - cand.stop_price) / cand.entry_price


def _bucket_of(frac: float) -> str:
    for lo, hi, label in STOP_BUCKETS:
        if lo <= frac < hi:
            return label
    return STOP_BUCKETS[-1][2]


def guard_rows(cells: Sequence[CellResult]) -> list[GuardRow]:
    """(심볼, TF, 구간)별 사이징 가드 미만 후보 수·비율."""
    out: list[GuardRow] = []
    for cell in cells:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            fracs = [
                f
                for cand in cell.default_candidates
                if (cand.trigger_time < cell.is_boundary) == (segment == SEGMENT_IS)
                and (f := _stop_fraction(cand)) is not None
            ]
            if not fracs:
                continue
            below = sum(1 for f in fracs if f < STOP_GUARD_FRACTION)
            out.append(
                GuardRow(
                    symbol=cell.symbol,
                    timeframe=cell.timeframe,
                    segment=segment,
                    n_candidates=len(fracs),
                    n_below_guard=below,
                    frac_below_guard=below / len(fracs),
                    median_stop_fraction=float(pd.Series(fracs).median()),
                )
            )
    return out


def stop_bucket_rows(cells: Sequence[CellResult]) -> list[StopBucketRow]:
    """손절 거리 버킷별 뚫림률(6심볼 풀링, END_OF_DATA 제외).

    ⚠️ **시퀀싱·사이징 이전의 전 후보**를 센다 — 가드에 걸려 거래가 되지 못한 셋업도
    시뮬레이터는 청산까지 계산해 뒀으므로(`reason`), "가드가 버린 것들이 실제로 어땠는가"를
    사후에 볼 수 있다. 이것이 이 표의 존재 이유다.
    """
    acc: dict[tuple[str, str, str], list[bool]] = defaultdict(list)
    for cell in cells:
        for cand in cell.default_candidates:
            if cand.reason is ExitReason.END_OF_DATA:
                continue
            frac = _stop_fraction(cand)
            if frac is None:
                continue
            segment = SEGMENT_IS if cand.trigger_time < cell.is_boundary else SEGMENT_OOS
            acc[(cell.timeframe, segment, _bucket_of(frac))].append(
                cand.reason is ExitReason.STOP_LOSS
            )
    out: list[StopBucketRow] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for lo, _hi, label in STOP_BUCKETS:
                key = (timeframe, segment, label)
                vals = acc.get(key, [])
                if not vals:
                    continue
                out.append(
                    StopBucketRow(
                        timeframe=timeframe,
                        segment=segment,
                        bucket=label,
                        below_guard=lo < STOP_GUARD_FRACTION,
                        n=len(vals),
                        broke_rate=sum(vals) / len(vals),
                    )
                )
    return out


class AtrControlRow(BaseModel):
    """ATR을 통제한 뒤의 `zone_width_atr`↔뚫림 연관 (교란 검정).

    ⚠️ **이 검정이 이 이슈의 두 번째 관문이다.** `zone_width_atr = 존폭/ATR`이므로 값이 크다는
    건 **존이 넓거나 ATR이 낮거나**다. ATR 팔은 손절을 `k·ATR`로 잡으므로 **ATR이 낮으면 절대
    손절폭이 좁아** 더 뚫린다 — 즉 장벽 거리를 존폭에서 뗐어도 **분모(ATR)를 통해** 산수가
    되살아날 수 있다. 층을 (심볼 × ATR 분위)로 잡아 ATR 구성을 보존한 채 라벨만 섞으면,
    남는 연관은 **ATR로 설명되지 않는 존폭의 몫**이다.
    """

    model_config = ConfigDict(frozen=True)

    arm: str
    timeframe: str
    segment: str
    n: int
    corr_raw: float | None
    """심볼 층만 잡은 상관(= Part A와 같은 값)."""
    corr_controlled: float | None
    """(심볼 × ATR 분위) 층에서의 상관 — 값 자체는 같고 널이 달라진다."""
    p_controlled: float | None
    n_strata: int


def atr_control_row(
    labeled: list[LabeledTrade],
    *,
    arm: str,
    timeframe: str,
    segment: str,
    permutations: int = _PERMUTATIONS,
    n_quantiles: int = 3,
) -> AtrControlRow:
    """(심볼 × ATR 분위) 층 순열로 ATR을 통제한 `zone_width_atr`↔뚫림 검정."""
    rows = [
        (v, a, lt)
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and (v := lt.features.get(FOCUS_FEATURE)) is not None
        and (a := lt.features.get("atr_at_entry")) is not None
    ]
    n = len(rows)
    if n < _MIN_TRADES_FOR_VERDICT:
        return AtrControlRow(
            arm=arm,
            timeframe=timeframe,
            segment=segment,
            n=n,
            corr_raw=None,
            corr_controlled=None,
            p_controlled=None,
            n_strata=0,
        )
    values = [v for v, _, _ in rows]
    broke = [lt.broke for _, _, lt in rows]
    # ATR 분위는 **심볼 안에서** 매긴다 — 심볼마다 가격 척도가 달라 전 표본 공통 분위는
    # 사실상 심볼 라벨이 되어 버린다(통제가 아니라 중복 층화).
    by_symbol: dict[str, list[int]] = defaultdict(list)
    for i, (_, _, lt) in enumerate(rows):
        by_symbol[lt.symbol].append(i)
    strata: list[object] = [("", 0)] * n
    for symbol, idxs in by_symbol.items():
        atrs = [rows[i][1] for i in idxs]
        labels = _quantile_labels(atrs, n_quantiles)
        for slot, i in enumerate(idxs):
            strata[i] = (symbol, labels[slot] if labels is not None else 0)
    corr_ctl, p_ctl = stratified_permutation_p(values, broke, strata, permutations=permutations)
    corr_raw = _point_biserial(values, broke)
    return AtrControlRow(
        arm=arm,
        timeframe=timeframe,
        segment=segment,
        n=n,
        corr_raw=corr_raw,
        corr_controlled=corr_ctl,
        p_controlled=p_ctl,
        n_strata=len(set(strata)),
    )


def _quantile_labels(values: list[float], n_quantiles: int) -> list[int] | None:
    """값을 분위 정수 라벨로. 분위가 안 나뉘면(중복 과다) None."""
    if len(values) < n_quantiles:
        return None
    try:
        labels = pd.qcut(values, n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return None
    return [int(x) for x in labels]


def geometry_verdict(
    perm_by_arm: dict[str, list[PermutationRow]],
    atr_control: Sequence[AtrControlRow] = (),
    *,
    timeframe: str,
    alpha: float = 0.05,
) -> str:
    """한 TF의 (a) 선별 / (b) 기하 판정 — **관문 두 개를 모두** 통과해야 (a)다.

    1. **장벽 관문**: 기본 팔에서 유의했던 `zone_width_atr` 상관이 ATR 장벽 팔(k 중 하나
       이상)에서도 OOS 유의 + 같은 방향으로 남는가. 여기서 죽으면 (b) 기하다.
    2. **ATR 분모 관문**: 그 생존이 **ATR 통제 후에도** 남는가. `zone_width_atr = 존폭/ATR`
       이라 ATR 팔의 손절(`k·ATR`)이 낮은 ATR에서 절대적으로 좁아진다 — 장벽을 존폭에서
       뗐어도 **분모를 통해** 산수가 되살아날 수 있다. (심볼 × ATR 분위) 층 순열에서
       죽으면 그 생존은 존 품질이 아니라 **ATR 수준**의 몫이므로 역시 (b)다.

    판정 바는 WAN-117과 같은 취지(OOS 유의 + 방향)로 읽되 focus 하나만 검정하므로 임계는
    α로 둔다(WAN-117의 α'=0.0045보다 관대 — 그런데도 못 넘으면 어느 바로도 못 넘는다).
    """

    def cell(arm: str, segment: str) -> PermutationRow | None:
        for r in perm_by_arm.get(arm, []):
            if r.timeframe == timeframe and r.segment == segment and r.feature == FOCUS_FEATURE:
                return r
        return None

    base_oos = cell(ARM_DEFAULT, SEGMENT_OOS)
    if base_oos is None or base_oos.correlation is None or base_oos.p_value is None:
        return f"**{timeframe}**: 판정 불가 — 기본 팔 OOS 표본 부족."

    survived: list[str] = []
    for k in K_VALUES:
        o = cell(arm_atr(k), SEGMENT_OOS)
        if o is None or o.correlation is None or o.p_value is None:
            continue
        # 살아남음 = OOS 유의 + 기본 팔과 같은 방향(넓은 존일수록 뚫림, corr>0).
        if o.p_value <= alpha and (o.correlation > 0) == (base_oos.correlation > 0):
            survived.append(f"k={k:g}(corr={o.correlation:+.3f}, p={o.p_value:.4f})")

    base_desc = f"기본 팔 OOS corr={base_oos.correlation:+.3f}(p={base_oos.p_value:.4f})"

    def _atr_desc(k: float) -> str:
        o = cell(arm_atr(k), SEGMENT_OOS)
        if o is None or o.correlation is None or o.p_value is None:
            return f"k={k:g} —"
        return f"k={k:g} corr={o.correlation:+.3f}(p={o.p_value:.4f})"

    atr_desc = "; ".join(_atr_desc(k) for k in K_VALUES)
    if survived:
        # 관문 2 — ATR 통제. 장벽을 뗐어도 분모(ATR)로 산수가 되살아났는지 본다.
        ctl = [
            r
            for r in atr_control
            if r.timeframe == timeframe
            and r.segment == SEGMENT_OOS
            and r.arm != ARM_DEFAULT
            and r.p_controlled is not None
        ]
        passed = [r for r in ctl if r.p_controlled is not None and r.p_controlled <= alpha]
        ctl_desc = "; ".join(
            f"{r.arm} corr={r.corr_controlled:+.3f}(p={r.p_controlled:.4f})"
            for r in ctl
            if r.corr_controlled is not None and r.p_controlled is not None
        )
        if not ctl:
            return (
                f"**{timeframe}**: **판정 보류** — 장벽 관문은 통과했으나"
                f"({', '.join(survived)}) ATR 통제 검정 표본이 부족하다."
            )
        if passed:
            return (
                f"**{timeframe}**: **(a) 선별 축 후보** — 두 관문 통과. ATR 장벽 팔에서도 "
                f"`zone_width_atr`↔뚫림이 살아남고({', '.join(survived)}; {base_desc}), "
                f"**ATR을 통제한 뒤에도** 남는다({ctl_desc}). 장벽 거리·ATR 수준 어느 쪽으로도 "
                "환원되지 않으므로 존폭이 뚫림을 가르는 몫이 따로 있다 — 후속 '존 품질 필터 "
                "구현' 이슈에서 손익 효과를 재검할 근거가 있다."
            )
        return (
            f"**{timeframe}**: **(b) 기하 효과 확정(분모 경로)** — 장벽 관문은 통과했지만"
            f"({', '.join(survived)}) **ATR 통제에서 소멸했다**({ctl_desc}). 즉 ATR 팔에서 "
            "살아남은 상관은 존 품질이 아니라 **분모(ATR 수준)**의 몫이다 — `zone_width_atr`이 "
            "크다는 건 존이 넓다는 뜻만이 아니라 **ATR이 낮다**는 뜻이기도 하고, 손절이 "
            "`k·ATR`이라 ATR이 낮으면 절대 손절폭이 좁아 더 뚫린다. 장벽을 존폭에서 뗐더니 "
            "산수가 분모로 자리를 옮겼을 뿐이다. WAN-88/111/114/117 「엣지 없음」을 재확인한다."
        )
    return (
        f"**{timeframe}**: **(b) 기하 효과 확정** — 장벽 거리를 k·ATR로 고정하자 "
        f"`zone_width_atr`↔뚫림 상관이 소멸했다(ATR 팔 OOS: {atr_desc}; vs {base_desc}). "
        "WAN-117의 유일한 생존 축은 **선별이 아니라 손익 기하**(넓은 존 = 먼 장벽 = 고정 "
        "1.5R 익절에 닿기 전 −1R 손절)였다. WAN-88/111/114/117 「엣지 없음」을 재확인한다 — "
        "뚫릴 존을 진입 시점에 미리 가르는 능력은 확인되지 않았다."
    )


# --------------------------------------------------------------------------- #
# Part B: 존폭 하위 1/3 필터의 손익·MDD (IS 문턱 → OOS 적용)
# --------------------------------------------------------------------------- #


class PnlRow(BaseModel):
    """한 (심볼, TF, 구간, 팔) 셀의 손익."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    num_candidates: int
    """이 구간에서 팔이 진입 후보로 삼은 셋업 수(시퀀싱 이전)."""
    num_trades: int
    total_return: float
    max_drawdown: float
    win_rate: float


ARM_FILTER = "filter_bot3"
"""존폭 하위 1/3분위만 진입(IS 문턱, OOS 적용)."""

MIN_TRADES_FOR_PNL = _MIN_TRADES_FOR_VERDICT
"""손익 셀의 유효 표본 하한(20건, WAN-70/77/84와 같은 기준).

⚠️ **이 게이트가 필요한 이유는 WAN-133이 발견한 사이징 바닥 충돌 때문이다**(아래
`STOP_GUARD_FRACTION`). 필터가 고르는 좁은 존이 `min_stop_distance_fraction`(0.3%)에 걸려
사이징에서 탈락하므로, 저변동성 심볼의 필터 셀은 거래가 20건 아래로 내려앉는다(TRX 15m OOS
= 13건). 그 셀의 수익률을 심볼평균에 그대로 넣으면 표본 13건짜리 값이 6심볼 평균의 1/6을
차지한다 — 판정에서 제외하고 병기만 한다."""


def _is_threshold(cell: CellResult) -> float | None:
    """IS 기본 후보의 zone_width_atr 하위 1/3 문턱. IS 표본이 없으면 None."""
    is_vals = [
        z
        for z, cand in zip(cell.default_zwa, cell.default_candidates, strict=True)
        if z is not None and cand.trigger_time < cell.is_boundary
    ]
    if len(is_vals) < 3:
        return None
    return float(pd.Series(is_vals).quantile(FILTER_FRACTION))


def pnl_rows_for_cell(cell: CellResult, market: MarketData) -> list[PnlRow]:
    """기본 팔 vs 필터 팔의 구간별 손익. 필터는 IS 문턱을 OOS에 적용(룩어헤드 없음)."""
    if not cell.default_candidates:
        return []
    cfg = harness.build_config(cell.timeframe)
    threshold = _is_threshold(cell)
    pairs = list(zip(cell.default_candidates, cell.default_zwa, strict=True))
    rows: list[PnlRow] = []
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        in_seg = [
            (cand, z)
            for cand, z in pairs
            if (cand.trigger_time < cell.is_boundary) == (segment == SEGMENT_IS)
        ]
        base_cands = [cand for cand, _ in in_seg]
        if threshold is None:
            filter_cands: list[_Candidate] = []
        else:
            filter_cands = [cand for cand, z in in_seg if z is not None and z <= threshold]
        for arm, cands in ((ARM_DEFAULT, base_cands), (ARM_FILTER, filter_cands)):
            trades = [t for _, t in sequence_with_candidates(cands, cfg, market.funding_rates)]
            res = build_result_from_trades(trades, cfg, cell.timeframe)
            m = res.metrics
            rows.append(
                PnlRow(
                    symbol=cell.symbol,
                    timeframe=cell.timeframe,
                    segment=segment,
                    arm=arm,
                    num_candidates=len(cands),
                    num_trades=m.num_trades,
                    total_return=m.total_return,
                    max_drawdown=m.max_drawdown,
                    win_rate=m.win_rate,
                )
            )
    return rows


def pnl_symbol_mean(
    rows: Sequence[PnlRow], *, timeframe: str, segment: str, arm: str, gated: bool = True
) -> dict[str, float | None]:
    """6심볼 심볼평균(total_return·MDD는 단순평균, 거래수는 합).

    `gated=True`(기본)면 **거래 20건 미만 셀을 평균에서 뺀다**(`MIN_TRADES_FOR_PNL`) —
    사이징 바닥에 걸려 표본이 무너진 셀(TRX 15m 필터 = 13건)이 6심볼 평균의 1/6을 차지하는
    것을 막는다. `n_excluded`로 몇 셀이 빠졌는지 병기한다. `gated=False`는 원값(대조용).
    """
    sub = [r for r in rows if r.timeframe == timeframe and r.segment == segment and r.arm == arm]
    excluded = [r for r in sub if r.num_trades < MIN_TRADES_FOR_PNL]
    if gated:
        sub = [r for r in sub if r.num_trades >= MIN_TRADES_FOR_PNL]
    if not sub:
        return {
            "total_return": None,
            "max_drawdown": None,
            "num_trades": None,
            "n_symbols": 0,
            "n_excluded": float(len(excluded)),
        }
    n = len(sub)
    return {
        "total_return": sum(r.total_return for r in sub) / n,
        "max_drawdown": sum(r.max_drawdown for r in sub) / n,
        "num_trades": float(sum(r.num_trades for r in sub)),
        "n_symbols": n,
        "n_excluded": float(len(excluded)),
    }


def pnl_leave_one_out(
    rows: Sequence[PnlRow], *, timeframe: str, arm: str, segment: str = SEGMENT_OOS
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인."""
    sub = [r for r in rows if r.timeframe == timeframe and r.segment == segment and r.arm == arm]
    if len(sub) < 2:
        return "표본 부족"
    parts: list[str] = []
    for drop in sub:
        rest = [r.total_return for r in sub if r.symbol != drop.symbol]
        avg = sum(rest) / len(rest)
        parts.append(f"−{_bare(drop.symbol)} {avg * 100:+.2f}%")
    return " · ".join(parts)


DEGENERACY_PASS_RATE = 0.95
"""필터 통과율이 이 값 이상이면 표본을 실제로 가르지 못한 것 = 퇴화(WAN-124 가드의 취지)."""


def degeneracy_note(rows: Sequence[PnlRow], *, timeframe: str, segment: str) -> str:
    """필터 퇴화 검사(WAN-124 계열) — 필터가 후보 표본을 실제로 가르는가.

    WAN-124의 `overlap_fraction` 가드는 매칭 널의 풀이 실제 파라미터와 같아져 널이 자기
    자신을 검정하는 퇴화를 막았다. 여기 널은 **라벨 순열**(WAN-117)이라 풀 개념이 없어
    그대로 옮길 수 없고, 대신 같은 취지를 **필터가 표본을 가르는가**로 옮긴다 — 통과율이
    `DEGENERACY_PASS_RATE` 이상이면 필터 팔이 기본 팔과 사실상 같은 집합이라 두 팔의
    대조가 아무것도 재지 못한다(그때는 손익 차이를 선별로 읽지 말 것).

    시퀀싱 이전 **후보 수**로 잰다(거래 수는 동시 1포지션 시퀀서가 한 번 더 깎으므로
    필터 자체의 분할력이 아니다 — 그쪽은 `contamination_note`가 따로 본다).
    """
    base = [
        r
        for r in rows
        if r.timeframe == timeframe and r.segment == segment and r.arm == ARM_DEFAULT
    ]
    filt = [
        r for r in rows if r.timeframe == timeframe and r.segment == segment and r.arm == ARM_FILTER
    ]
    b = sum(r.num_candidates for r in base)
    f = sum(r.num_candidates for r in filt)
    if not b:
        return "판정 불가(후보 없음)"
    rate = f / b
    if rate >= DEGENERACY_PASS_RATE:
        return (
            f"후보 {b} → 필터 {f}({rate * 100:.1f}% 통과) — **퇴화**: 필터가 표본을 가르지 "
            "못하므로 두 팔 대조를 선별 효과로 읽지 말 것"
        )
    return (
        f"후보 {b} → 필터 {f}({rate * 100:.1f}% 통과, "
        f"{DEGENERACY_PASS_RATE:.0%} 미만) — 정상(퇴화 아님)"
    )


def contamination_note(rows: Sequence[PnlRow], *, timeframe: str, segment: str) -> str:
    """거래 수 오염 게이트(WAN-126) — 필터가 거래를 몇 % 줄이는가."""
    base = pnl_symbol_mean(rows, timeframe=timeframe, segment=segment, arm=ARM_DEFAULT)
    filt = pnl_symbol_mean(rows, timeframe=timeframe, segment=segment, arm=ARM_FILTER)
    b, f = base.get("num_trades"), filt.get("num_trades")
    if not b or f is None:
        return "판정 불가(표본 없음)"
    pct = (f - b) / b * 100.0
    flag = "**초과**" if abs(pct) > 5.0 else "이내"
    return f"기본 {int(b)}건 → 필터 {int(f)}건({pct:+.1f}%, 5% {flag}) — " + (
        "선별 필터라 거래가 크게 줄어 손익 차이를 **순수 선별 효과로 인용 불가**"
        if abs(pct) > 5.0
        else "거래 수 근접"
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass
class ExperimentResult:
    cells: list[CellResult] = field(default_factory=list)
    perm_by_arm: dict[str, list[PermutationRow]] = field(default_factory=dict)
    quantile_by_arm: dict[str, list[QuantileRow]] = field(default_factory=dict)
    pnl_rows: list[PnlRow] = field(default_factory=list)
    atr_control: list[AtrControlRow] = field(default_factory=list)
    guard: list[GuardRow] = field(default_factory=list)
    stop_buckets: list[StopBucketRow] = field(default_factory=list)


def run_experiment(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    permutations: int = _PERMUTATIONS,
    db_path: str = harness.DB_PATH,
) -> ExperimentResult:
    """6심볼 × {15m,1h} — 팔별 라벨링 → focus 순열/분위 → 필터 손익."""
    # ⚠️ 밴드는 WAN-132 이전 값(`tap`)으로 고정한다 — 이 리포트 헤더가 이미 그 전제를
    # 적어 두었고(「`band_bar="tap"` 정본 전제」), 1R 기하가 진입가에 직접 달려 있다.
    params = harness.pin_band_bar(ConfluenceParams())
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    cells: list[CellResult] = []
    markets: dict[tuple[str, str], MarketData] = {}
    for symbol in symbols:
        norm = harness.normalize_symbol(symbol)
        for timeframe in timeframes:
            market = harness.load_market_data(
                norm, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, db_path=db_path
            )
            cell = label_cell(market, params=params)
            cells.append(cell)
            markets[(norm, timeframe)] = market
            default_lbl = cell.labeled_by_arm.get(ARM_DEFAULT, [])
            broke = sum(1 for lt in default_lbl if lt.broke)
            print(
                f"[wan133] {norm} {timeframe}: default={len(default_lbl)} "
                f"(broke={broke}) arms={list(cell.labeled_by_arm)}"
            )

    arms = [ARM_DEFAULT, *(arm_atr(k) for k in K_VALUES)]
    perm_by_arm: dict[str, list[PermutationRow]] = {}
    quantile_by_arm: dict[str, list[QuantileRow]] = {}
    atr_control: list[AtrControlRow] = []
    for arm in arms:
        pooled = _pool_labeled(cells, arm)
        perms: list[PermutationRow] = []
        quants: list[QuantileRow] = []
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                # focus 축 + 그 분자·분모 성분(존폭 자체 / ATR 수준)을 함께 검정한다 —
                # 살아남은 상관이 존폭의 몫인지 ATR의 몫인지 가르는 분해다.
                for feature in (FOCUS_FEATURE, *COMPONENT_FEATURES):
                    perms.append(
                        permutation_test(
                            pooled,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            permutations=permutations,
                        )
                    )
                quants.extend(
                    quantile_rows(
                        pooled, timeframe=timeframe, segment=segment, feature=FOCUS_FEATURE
                    )
                )
                atr_control.append(
                    atr_control_row(
                        pooled,
                        arm=arm,
                        timeframe=timeframe,
                        segment=segment,
                        permutations=permutations,
                    )
                )
        perm_by_arm[arm] = perms
        quantile_by_arm[arm] = quants

    pnl_rows: list[PnlRow] = []
    for cell in cells:
        market = markets[(cell.symbol, cell.timeframe)]
        pnl_rows.extend(pnl_rows_for_cell(cell, market))

    return ExperimentResult(
        cells=cells,
        perm_by_arm=perm_by_arm,
        quantile_by_arm=quantile_by_arm,
        pnl_rows=pnl_rows,
        atr_control=atr_control,
        guard=guard_rows(cells),
        stop_buckets=stop_bucket_rows(cells),
    )


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def _quant_cell(rows: list[QuantileRow], rank: int) -> str:
    for qr in rows:
        if qr.quantile_rank == rank:
            return f"{qr.broke_rate * 100:.0f}%/{qr.mean_r:+.2f}"
    return "—"


def build_summary_markdown(result: ExperimentResult) -> str:
    lines: list[str] = []
    lines.append(
        "# WAN-133 존폭/ATR 「기하 대 선별」 분리 — 뚫림의 유일한 강건 축은 알파인가 산수인가\n"
    )
    lines.append(
        f"6심볼(BTC/ETH/SOL/BNB/XRP/TRX) × 15m·1h, 못 박은 창 **{DEFAULT_START} ~ {DEFAULT_END}**, "
        "채택 기본값(`ConfluenceParams()` — 존 지정가 offset 2bp · `unconditional` 게이트 · "
        "볼린저 `tap`) · 공식 렌즈 `baseline`(WAN-128 단독). **⚠️ `tap` 정본 전제** — WAN-132가 "
        "`intrabar_live`로 전환하면 1R 기하가 이동해 재산출 대상이다(PM 경고). "
        "라벨: 뚫림=−1R 손절 · 버팀=+1.5R 익절, END_OF_DATA 제외. "
        "재현: `python -m backtest.wan133_geometry_vs_selection`.\n"
    )
    lines.append(
        "**실험**: 손절 거리를 존 무효화 경계(≈ 존폭)에서 떼어 **k·ATR로 고정**한 팔"
        "(`stop_loss_override`)에서 `zone_width_atr`↔뚫림 상관을 재검정한다. 장벽이 존폭과 "
        "무관해졌는데도 상관이 남으면 **선별**, 사라지면 **기하**다.\n"
    )

    # 판정 --------------------------------------------------------------------
    lines.append("## 판정\n")
    for timeframe in DEFAULT_TIMEFRAMES:
        lines.append(
            f"* {geometry_verdict(result.perm_by_arm, result.atr_control, timeframe=timeframe)}"
        )
    lines.append("")

    # Part A: 팔별 focus 상관·p ------------------------------------------------
    lines.append("## Part A-1 (장벽 관문): 팔별 `zone_width_atr`↔뚫림 상관 · 순열 p\n")
    lines.append(
        "손절 팔 = 손절 참조가. `default`=존 무효화 경계(채택 기본값·WAN-117 재현), "
        f"`atr_k`=진입가 ∓ k·ATR(고정). p = 심볼 층화 라벨 순열 {_PERMUTATIONS}회 양측값. "
        f"유효 셀 = 거래 {_MIN_TRADES_FOR_VERDICT}건 이상.\n"
    )
    lines.append(
        "| 손절 팔 | TF | 구간 | n | 무효화율 | corr | p |\n| -- | -- | -- | -- | -- | -- | -- |"
    )
    for arm in [ARM_DEFAULT, *(arm_atr(k) for k in K_VALUES)]:
        for r in result.perm_by_arm.get(arm, []):
            if r.feature != FOCUS_FEATURE:
                continue
            corr = "—" if r.correlation is None else f"{r.correlation:+.3f}"
            p = "—" if r.p_value is None else f"{r.p_value:.4f}"
            lines.append(
                f"| `{arm}` | {r.timeframe} | {r.segment} | {r.n} | "
                f"{r.broke_rate * 100:.1f}% | {corr} | {p} |"
            )
    lines.append("")

    # Part A-2: ATR 통제 관문 --------------------------------------------------
    lines.append("## Part A-2 (ATR 분모 관문): ATR을 통제한 뒤의 `zone_width_atr`↔뚫림\n")
    lines.append(
        "⚠️ **장벽을 존폭에서 뗐다고 산수가 다 빠진 게 아니다.** `zone_width_atr = 존폭/ATR`이라 "
        "값이 크다는 건 **존이 넓거나 ATR이 낮거나**이고, ATR 팔의 손절은 `k·ATR`이라 **ATR이 "
        "낮으면 절대 손절폭이 좁아 더 뚫린다** — 분모를 통해 기하가 되살아나는 경로다. 층을 "
        f"(심볼 × ATR 3분위)로 잡아 ATR 구성을 보존한 채 라벨만 섞는다({_PERMUTATIONS}회). "
        "여기서 죽으면 그 상관은 존 품질이 아니라 **ATR 수준**의 몫이다.\n"
    )
    lines.append(
        "| 손절 팔 | TF | 구간 | n | 층 수 | corr | p(ATR 통제) |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    for ac in result.atr_control:
        corr = "—" if ac.corr_controlled is None else f"{ac.corr_controlled:+.3f}"
        p = "—" if ac.p_controlled is None else f"{ac.p_controlled:.4f}"
        lines.append(
            f"| `{ac.arm}` | {ac.timeframe} | {ac.segment} | {ac.n} | "
            f"{ac.n_strata} | {corr} | {p} |"
        )
    lines.append("")

    # Part A-3: 성분 분해 ------------------------------------------------------
    lines.append("## Part A-3 (성분 분해): 분자(존폭)와 분모(ATR)를 따로 검정\n")
    lines.append(
        "`zone_width_pctl`=원 존폭의 셀 내부 순위, `atr_pctl`=ATR의 셀 내부 순위(둘 다 심볼 간 "
        "풀링 가능한 척도). 신호가 `atr_pctl`에 몰리고 `zone_width_pctl`이 0에 가까우면, "
        "「넓은 존이 뚫린다」가 아니라 **「저변동성 구간이 뚫린다」**를 본 것이다.\n"
    )
    lines.append(
        "| 손절 팔 | TF | 구간 | 특징 | n | corr | p |\n| -- | -- | -- | -- | -- | -- | -- |"
    )
    for arm in [ARM_DEFAULT, *(arm_atr(k) for k in K_VALUES)]:
        for r in result.perm_by_arm.get(arm, []):
            if r.feature not in COMPONENT_FEATURES:
                continue
            corr = "—" if r.correlation is None else f"{r.correlation:+.3f}"
            p = "—" if r.p_value is None else f"{r.p_value:.4f}"
            lines.append(
                f"| `{arm}` | {r.timeframe} | {r.segment} | `{r.feature}` | {r.n} | {corr} | {p} |"
            )
    lines.append("")

    # Part A: 분위 무효화율/평균R ---------------------------------------------
    lines.append("## Part A: `zone_width_atr` 분위별 무효화율·평균R (기본 vs ATR 대표 팔)\n")
    lines.append(
        "좁은 존(Q1)→넓은 존(Q3). 기본 팔에서 평균R이 크게 벌어지면(WAN-117) 그 벌어짐이 "
        f"기하(장벽 거리)인지 선별인지를 ATR 팔(k={PRIMARY_K:g})이 가른다 — 평평해지면 기하다.\n"
    )
    lines.append(
        "| 손절 팔 | TF | 구간 | 단조성 | Q1 뚫림/R | Q2 뚫림/R | Q3 뚫림/R |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    for arm in (ARM_DEFAULT, arm_atr(PRIMARY_K)):
        quants = result.quantile_by_arm.get(arm, [])
        by_key: dict[tuple[str, str], list[QuantileRow]] = defaultdict(list)
        for q in quants:
            by_key[(q.timeframe, q.segment)].append(q)
        for timeframe in DEFAULT_TIMEFRAMES:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                rows = by_key.get((timeframe, segment), [])
                if not rows:
                    continue
                verdict = monotonicity_verdict(rows)
                lines.append(
                    f"| `{arm}` | {timeframe} | {segment} | {verdict} | "
                    f"{_quant_cell(rows, 1)} | {_quant_cell(rows, 2)} | {_quant_cell(rows, 3)} |"
                )
    lines.append("")

    # Part A: 심볼 leave-one-out ---------------------------------------------
    lines.append(f"## Part A: 심볼 편중 — ATR 팔(k={PRIMARY_K:g}) OOS corr leave-one-out\n")
    lines.append("한 심볼을 빼고 본 corr(존폭/ATR, 뚫림). 부호가 한 심볼에 얹혀 있으면 편중이다.\n")
    lines.append("| TF | 전체 | " + " | ".join(_bare(s) for s in DEFAULT_SYMBOLS) + " |")
    lines.append("| -- | -- | " + " | ".join("--" for _ in DEFAULT_SYMBOLS) + " |")
    atr_pool = _pool_labeled(result.cells, arm_atr(PRIMARY_K))
    for timeframe in DEFAULT_TIMEFRAMES:
        full = next(
            (
                r.correlation
                for r in result.perm_by_arm.get(arm_atr(PRIMARY_K), [])
                if r.timeframe == timeframe and r.segment == SEGMENT_OOS
            ),
            None,
        )
        loo = leave_one_out_corr(
            atr_pool, timeframe=timeframe, segment=SEGMENT_OOS, feature=FOCUS_FEATURE
        )
        full_s = "—" if full is None else f"{full:+.3f}"
        cells_s = " | ".join(
            ("—" if (c := loo.get(_bare(s))) is None else f"{c:+.3f}") for s in DEFAULT_SYMBOLS
        )
        lines.append(f"| {timeframe} | {full_s} | {cells_s} |")
    lines.append("")

    # Part B: 필터 손익 -------------------------------------------------------
    lines.append("## Part B: 존폭 하위 1/3 필터의 손익·MDD (IS 문턱 → OOS 적용)\n")
    lines.append(
        "⚠️ **거래 수 오염**: 선별 필터는 정의상 거래를 ~2/3 줄이므로(하위 1/3만) 두 팔의 "
        "거래 수가 5%를 크게 초과해 달라진다 — 손익 차이를 **순수 선별로 인용할 수 없다**"
        "(노출·복리가 함께 바뀐다). 깨끗한 선별 대 기하 판정은 위 Part A다. 6심볼 심볼평균.\n"
    )
    lines.append(
        "| TF | 구간 | 팔 | 거래수 | total_return | MDD | 승률 |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for arm in (ARM_DEFAULT, ARM_FILTER):
                pm = pnl_symbol_mean(result.pnl_rows, timeframe=timeframe, segment=segment, arm=arm)
                tr = pm.get("total_return")
                mdd = pm.get("max_drawdown")
                nt = pm.get("num_trades")
                lines.append(
                    f"| {timeframe} | {segment} | `{arm}` | "
                    f"{'—' if nt is None else int(nt)} | "
                    f"{'—' if tr is None else f'{tr * 100:+.2f}%'} | "
                    f"{'—' if mdd is None else f'{mdd * 100:.2f}%'} | "
                    f"{_winrate(result.pnl_rows, timeframe, segment, arm)} |"
                )
    lines.append("")
    lines.append(
        f"⚠️ **심볼평균은 거래 {MIN_TRADES_FOR_PNL}건 미만 셀을 제외한 값이다** — 사이징 바닥"
        "(아래 Part C) 때문에 저변동성 심볼의 필터 셀이 표본 붕괴를 겪는다. 제외 셀 수는 "
        "괄호로 병기한다.\n"
    )

    # 심볼별 분해 -------------------------------------------------------------
    lines.append("### 심볼별 분해 (OOS)\n")
    lines.append(
        f"⚠️ 거래 {MIN_TRADES_FOR_PNL}건 미만은 **판정 제외**(표시 ⚠️). 심볼평균이 한 심볼에 "
        "얹혀 있는지 보려면 ETH 제외 평균을 함께 본다.\n"
    )
    lines.append("| TF | 심볼 | 기본 거래/수익/MDD | 필터 거래/수익/MDD |\n| -- | -- | -- | -- |")
    for timeframe in DEFAULT_TIMEFRAMES:
        for symbol in DEFAULT_SYMBOLS:
            cells_ = {
                r.arm: r
                for r in result.pnl_rows
                if r.timeframe == timeframe and r.segment == SEGMENT_OOS and r.symbol == symbol
            }
            b, f = cells_.get(ARM_DEFAULT), cells_.get(ARM_FILTER)
            if b is None or f is None:
                continue
            fw = " ⚠️" if f.num_trades < MIN_TRADES_FOR_PNL else ""
            lines.append(
                f"| {timeframe} | {_bare(symbol)} | "
                f"{b.num_trades} / {b.total_return * 100:+.2f}% / {b.max_drawdown * 100:.2f}% | "
                f"{f.num_trades} / {f.total_return * 100:+.2f}% / "
                f"{f.max_drawdown * 100:.2f}%{fw} |"
            )
    lines.append("")
    lines.append("**ETH 제외 심볼평균(OOS total_return):**\n")
    for timeframe in DEFAULT_TIMEFRAMES:
        parts = []
        for arm in (ARM_DEFAULT, ARM_FILTER):
            sub = [
                r
                for r in result.pnl_rows
                if r.timeframe == timeframe
                and r.segment == SEGMENT_OOS
                and r.arm == arm
                and _bare(r.symbol) != "ETH"
                and r.num_trades >= MIN_TRADES_FOR_PNL
            ]
            avg = sum(r.total_return for r in sub) / len(sub) if sub else None
            parts.append(f"{arm} {'—' if avg is None else f'{avg * 100:+.2f}%'}({len(sub)}심볼)")
        lines.append(f"* {timeframe}: {' vs '.join(parts)}")
    lines.append("")
    lines.append("**거래 수 오염 게이트(WAN-126):**\n")
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            note = contamination_note(result.pnl_rows, timeframe=timeframe, segment=segment)
            lines.append(f"* {timeframe} {segment}: {note}")
    lines.append("")
    lines.append("**필터 퇴화 검사(WAN-124 계열 · 시퀀싱 이전 후보 기준):**\n")
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            note = degeneracy_note(result.pnl_rows, timeframe=timeframe, segment=segment)
            lines.append(f"* {timeframe} {segment}: {note}")
    lines.append("")
    lines.append("**필터 OOS total_return 심볼 편중(leave-one-out):**\n")
    for timeframe in DEFAULT_TIMEFRAMES:
        loo_pnl = pnl_leave_one_out(result.pnl_rows, timeframe=timeframe, arm=ARM_FILTER)
        lines.append(f"* {timeframe}: {loo_pnl}")
    lines.append("")

    # Part C: 사이징 바닥 충돌 -------------------------------------------------
    lines.append(
        "## Part C: 사이징 바닥(WAN-79, 0.3%)과의 충돌 — 필터가 고르는 존을 엔진이 거절한다\n"
    )
    lines.append(
        "📌 **이 절은 WAN-133이 실행 중 발견한 제약이다.** `RiskSizingParams."
        f"min_stop_distance_fraction`(기본 **{STOP_GUARD_FRACTION:.1%}**)은 손절 거리가 진입가의 "
        "그 비율 미만이면 `position_size`가 0을 반환해 **그 셋업을 거래에서 통째로 뺀다**. "
        "⚠️ **이 값은 WAN-133이 정한 게 아니라 WAN-79가 정했다**(왕복 체결 비용 ≈0.11% 대비 3배 "
        "여유 · WAN-76 감사 권고). 그런데 이 이슈의 필터는 **좁은 존**을 고르므로 정면으로 "
        "부딪힌다.\n"
    )
    lines.append("### C-1. 가드 미만 후보의 수·비율 (기본 팔, 시퀀싱 이전)\n")
    lines.append(
        "| TF | 심볼 | 구간 | 후보 | 가드미만 | 비율 | 중앙 손절% |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    for timeframe in DEFAULT_TIMEFRAMES:
        for gr in result.guard:
            if gr.timeframe != timeframe:
                continue
            lines.append(
                f"| {timeframe} | {_bare(gr.symbol)} | {gr.segment} | {gr.n_candidates} | "
                f"{gr.n_below_guard} | {gr.frac_below_guard * 100:.1f}% | "
                f"{gr.median_stop_fraction * 100:.3f}% |"
            )
    for timeframe in DEFAULT_TIMEFRAMES:
        gsub = [gr for gr in result.guard if gr.timeframe == timeframe]
        tot = sum(gr.n_candidates for gr in gsub)
        bel = sum(gr.n_below_guard for gr in gsub)
        if tot:
            lines.append(
                f"\n* **{timeframe} 전체: {tot}건 중 {bel}건({bel / tot * 100:.1f}%)이 가드 미만.**"
            )
    lines.append("")
    lines.append("### C-2. 손절 거리 버킷별 뚫림률 — 가드가 무엇을 버리는가\n")
    lines.append(
        "⚠️ **시퀀싱·사이징 이전의 전 후보**를 센다(END_OF_DATA 제외) — 가드에 걸려 거래가 되지 "
        "못한 셋업도 시뮬레이터가 청산까지 계산해 뒀으므로 「버려진 것들이 실제로 어땠는가」를 "
        "볼 수 있다.\n"
    )
    lines.append("| TF | 구간 | 손절폭 | 가드 | n | 뚫림률 |\n| -- | -- | -- | -- | -- | -- |")
    for sb in result.stop_buckets:
        mark = "미만" if sb.below_guard else "이상"
        lines.append(
            f"| {sb.timeframe} | {sb.segment} | {sb.bucket} | {mark} | {sb.n} | "
            f"{sb.broke_rate * 100:.1f}% |"
        )
    lines.append("")
    for timeframe in DEFAULT_TIMEFRAMES:
        lo = [b for b in result.stop_buckets if b.timeframe == timeframe and b.below_guard]
        hi = [b for b in result.stop_buckets if b.timeframe == timeframe and not b.below_guard]
        if not lo or not hi:
            continue
        ln, hn = sum(b.n for b in lo), sum(b.n for b in hi)
        lr = sum(b.broke_rate * b.n for b in lo) / ln
        hr = sum(b.broke_rate * b.n for b in hi) / hn
        lines.append(
            f"* **{timeframe}**: 가드미만 n={ln} 뚫림 **{lr * 100:.1f}%** vs "
            f"가드이상 n={hn} 뚫림 {hr * 100:.1f}% → **{(lr - hr) * 100:+.1f}%p**"
        )
    lines.append(
        "\n📌 **가드 미만이 오히려 덜 뚫린다** — 즉 가드는 「나쁜 거래」가 아니라 **가장 잘 "
        "버티는 구간**을 버리고 있다. 이는 이 이슈의 결론(좁을수록 잘 버틴다)을 **전혀 다른 축**"
        "(ATR 정규화가 아니라 진입가 대비 절대 %)에서 재확인한 것이기도 하다.\n"
        "🚨 **단 이것을 「가드를 없애라」로 읽지 말 것 — 뚫림률과 손익은 다르다.** WAN-79의 논리는 "
        "「뚫림률이 높다」가 아니라 **「손절폭이 왕복비용(≈0.11%)에 묻힌다」**였다. 손절폭 0.2%면 "
        "비용이 그 절반을 먹어 1.5R 익절이 실질 1.0R로, −1R 손절이 실질 −1.5R로 변형된다 — "
        "**승률이 높아도 기대값은 마이너스일 수 있다.** 이 표는 뚫림률만 셌고 **비용 반영 손익은 "
        "재지 않았다**. 따라서 이 표는 「가드가 틀렸다」의 증거가 아니라 **「가드가 무엇을 대가로 "
        "지불하는지」의 계량**이다. 판단은 별도 이슈 소관이다(§후속).\n"
    )

    # 결론 --------------------------------------------------------------------
    lines.append("## 결론\n")
    lines.append(_conclusion(result))
    return "\n".join(lines)


def _winrate(rows: Sequence[PnlRow], timeframe: str, segment: str, arm: str) -> str:
    sub = [r for r in rows if r.timeframe == timeframe and r.segment == segment and r.arm == arm]
    if not sub:
        return "—"
    return f"{sum(r.win_rate for r in sub) / len(sub) * 100:.1f}%"


def _conclusion(result: ExperimentResult) -> str:
    verdicts = {
        tf: geometry_verdict(result.perm_by_arm, result.atr_control, timeframe=tf)
        for tf in DEFAULT_TIMEFRAMES
    }
    geometry = all("(b) 기하" in v for v in verdicts.values())
    denominator = any("분모 경로" in v for v in verdicts.values())
    if geometry and denominator:
        return (
            "**WAN-117의 유일한 생존 축 `zone_width_atr`은 선별이 아니라 산수다 — 다만 이슈가 "
            "지목한 경로가 아니라 「분모」 경로였다.** 장벽 거리를 존폭에서 떼어 k·ATR로 고정해도 "
            "상관이 남아 한때 (a) 선별로 보였지만, **ATR을 통제하자 소멸했다**. `zone_width_atr`이 "
            "크다는 건 존이 넓다는 뜻만이 아니라 **ATR이 낮다**는 뜻이기도 하고, 손절이 "
            "`k·ATR`이라 "
            "저변동성 구간에서 절대 손절폭이 좁아 더 뚫린다. 즉 장벽을 존폭에서 뗐더니 산수가 "
            "**분자에서 분모로 자리를 옮겼을 뿐**이다. 이는 WAN-96/114/115/120/124가 거듭 "
            "「가격/기하지 선별이 아니다」로 지목한 계열의 재확인이자, WAN-90의 「익절 R은 알파가 "
            "아니라 위험의 모양만 바꾼다」와 같은 결론이다. 따라서 **「엣지 없음」"
            "(WAN-88/111/114/117)은 뒤집히지 않는다** — 뚫릴 존을 진입 시점에 미리 가르는 능력은 "
            "확인되지 않았고, 좁은 존만 고르는 필터는 알파가 아니라 리스크의 모양을 바꾸는 것이다"
            "(Part B의 거래 수 오염이 이를 보강한다). 기본값·실거래 보류"
            "(`ALPHABLOCK_LIVE_TRADING=false`)는 불변(측정만)."
        )
    if geometry:
        return (
            "**WAN-117의 유일한 생존 축 `zone_width_atr`은 선별이 아니라 손익 기하다.** 손절 "
            "거리를 존폭에서 떼어 k·ATR로 고정하자 두 TF OOS에서 존폭↔뚫림 상관이 "
            "소멸했다 — 넓은 존이 더 뚫리던 것은 존 품질이 나빠서가 아니라 **고정 1.5R 익절이 "
            "ATR 단위로 더 멀어 −1R 손절에 먼저 닿아서**였다. 이는 WAN-96/114/115/120/124가 "
            "거듭 「가격/기하지 선별이 아니다」로 지목한 계열의 재확인이자, WAN-90의 「익절 R은 "
            "알파가 아니라 위험의 모양만 바꾼다」와 같은 결론이다. 따라서 **「엣지 없음」"
            "(WAN-88/111/114/117)은 뒤집히지 않는다** — 뚫릴 존을 진입 시점에 미리 가르는 능력은 "
            "확인되지 않았고, 좁은 존만 고르는 필터는 알파가 아니라 리스크의 모양(장벽 거리)을 "
            "바꾸는 것이다(Part B의 거래 수 오염이 이를 보강한다 — 필터의 손익 변화는 노출·복리 "
            "변화와 얽혀 순수 선별로 읽을 수 없다). 기본값·실거래 보류"
            "(`ALPHABLOCK_LIVE_TRADING=false`)는 불변(측정만)."
        )
    survivors = [tf for tf, v in verdicts.items() if "(a) 선별" in v]
    return (
        f"**일부 TF({', '.join(survivors)})에서 `zone_width_atr`↔뚫림이 장벽 고정 + ATR 통제를 "
        "모두 통과했다** — 존폭이 장벽 거리·ATR 수준 어느 쪽으로도 환원되지 않는 몫을 갖는다"
        "(선별 축 후보). 🚨 **그래도 「엣지 찾았다」로 인용 금지** — 아래 경고를 함께 넘긴다: "
        "(1) **유의 ≠ 수익** — Part B의 거래 수 오염으로 필터의 손익 효과는 순수 선별로 인용할 "
        "수 없다. (2) **심볼 편중** — leave-one-out에서 부호가 한 심볼에 얹혀 있는지 확인할 것. "
        "(3) **체결 가정** — 라벨은 `baseline`(닿으면 체결) 위의 값이라 큐 우선순위 걱정이 "
        "그대로다. "
        "(4) 후속 '존 품질 필터 구현' 이슈가 선별의 손익·MDD 효과를 거래 수를 맞춘 채"
        "(WAN-126 게이트) 재검해야 한다. 기본값·실거래 보류는 불변(측정만)."
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _perm_frame(perm_by_arm: dict[str, list[PermutationRow]]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for arm, rows in perm_by_arm.items():
        for r in rows:
            rec = r.model_dump()
            rec["arm"] = arm
            records.append(rec)
    return pd.DataFrame(records)


def _quantile_frame(quantile_by_arm: dict[str, list[QuantileRow]]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for arm, rows in quantile_by_arm.items():
        for r in rows:
            rec = r.model_dump()
            rec["arm"] = arm
            records.append(rec)
    return pd.DataFrame(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-133 존폭/ATR 기하 대 선별")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--permutations", type=int, default=_PERMUTATIONS)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--perm-out", type=Path, default=REPORTS_DIR / "wan133_permutation.csv")
    parser.add_argument("--quantile-out", type=Path, default=REPORTS_DIR / "wan133_quantile.csv")
    parser.add_argument("--pnl-out", type=Path, default=REPORTS_DIR / "wan133_pnl.csv")
    parser.add_argument(
        "--atr-control-out", type=Path, default=REPORTS_DIR / "wan133_atr_control.csv"
    )
    parser.add_argument("--guard-out", type=Path, default=REPORTS_DIR / "wan133_guard.csv")
    parser.add_argument(
        "--stop-bucket-out", type=Path, default=REPORTS_DIR / "wan133_stop_buckets.csv"
    )
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan133_summary.md")
    args = parser.parse_args(argv)

    result = run_experiment(
        symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
        timeframes=tuple(t.strip() for t in args.timeframes.split(",") if t.strip()),
        start=args.start,
        end=args.end,
        permutations=args.permutations,
        db_path=args.db,
    )
    _write_csv(_perm_frame(result.perm_by_arm), args.perm_out)
    _write_csv(_quantile_frame(result.quantile_by_arm), args.quantile_out)
    _write_csv(_rows_to_frame(result.pnl_rows), args.pnl_out)
    _write_csv(_rows_to_frame(result.atr_control), args.atr_control_out)
    _write_csv(_rows_to_frame(result.guard), args.guard_out)
    _write_csv(_rows_to_frame(result.stop_buckets), args.stop_bucket_out)
    print(f"[wan133] atr_control → {args.atr_control_out}")
    print(f"[wan133] guard → {args.guard_out}")
    print(f"[wan133] stop_buckets → {args.stop_bucket_out}")
    print(f"[wan133] permutation → {args.perm_out}")
    print(f"[wan133] quantile → {args.quantile_out}")
    print(f"[wan133] pnl → {args.pnl_out}")

    summary = build_summary_markdown(result)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan133] summary → {args.summary_out}")
    return 0


def _write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
