"""WAN-134: 존 병합(`combine_obs`)이 손절을 늘리는가 — 폭 통제 + ablation.

사용자 질문(2026-07-19): *"combined된 오더블록에서 더 손절이 많고 이런 게 있어?"* — 기본값이
`combine_obs=True`인데 그 부품의 한계 기여가 한 번도 격리된 적이 없다(WAN-114 ablation 축은
`retap`·`rsi_gate`·`deviation_filter` 셋뿐, WAN-117 라벨 특징 11개에 `combined` 없음).

## 왜 폭 통제가 필수인가 (PM 경고 · 사양 §2)

`_make_merged_group`이 병합 존의 손절선을 **구성 존 중 가장 바깥 존**에 맞춰 손절 거리를
기계적으로 넓힌다(WAN-56 영향 #2). 그런데 병합 존은 **정의상** 원본보다 넓으므로
`combined=True`가 곧 `zone_width_atr` 상위 분위일 공산이 크다 — WAN-117이 뚫림을 가른
**유일한 강건 축**이 바로 그 `zone_width_atr`였고, 그것마저 「존 품질 선별」이 아니라
**고정 R 장벽 기하**로 의심됐다(WAN-133이 그 축을 판다). 따라서 "병합 존이 더 손절난다"가
나와도 그게 **병합의 효과인지 존폭의 효과인지** 갈리지 않는다. 이 모듈은 존폭을 통제한 뒤에도
병합이 남는지를 핵심 질문으로 삼는다.

## 토대 (고정 입력 · 측정만)

진입=존 지정가 offset 2bp(`entry_mode="zone_limit"`), 렌즈=`baseline` 단독(WAN-128), 비용
현재값, 롱 온리, 6심볼(WAN-111), 못 박은 창 2023-07-14~2026-07-15. 15m·1h 병기. 기본값·실거래
보류(`ALPHABLOCK_LIVE_TRADING=false`)는 건드리지 않는다.

## 다섯 층 (사양)

1. **관측** — 채택 엔진 거래를 결과(뚫림/버팀)로 라벨링하고 `combined`·`num_component_obs`를
   실어 병합존/단일존의 손절률·평균R·존폭·거래수를 대조한다(WAN-117 심볼 층화 라벨 순열).
2. **교란 분리** — 존폭(`zone_width_atr`) 분위 안에서 병합/단일을 대조한다. 순열의 층을
   (심볼 × 존폭 분위)로 잡아, 폭·심볼 구성을 보존한 채 병합↔뚫림 연관만 끊는다. 폭을
   통제해도 병합이 남으면 (a), 사라지면 (b) 병합은 `zone_width_atr`의 대리변수다.
3. **Ablation** — `combine_obs=False` 팔의 total_return·MDD·거래수·체결률을 채택 기본값과
   대조한다. ⚠️ 병합을 끄면 거래 집합 자체가 바뀌므로(WAN-123/126 부류) **거래 수 오염
   게이트**(5% 초과면 순수 효과로 인용 금지)를 적용한다.
4. **부수 확인** — `_make_merged_group`이 `ob_volume`을 구성 존 **합산**으로 만들어 병합존의
   거래량 퍼센타일이 자동으로 부풀려진다. `combined`↔`volume_pctl` 오염을 확인한다.
5. **심볼 편중** — leave-one-out 병기.

재현: `python -m backtest.wan134_zone_merge_autopsy`(1h·15m 함께) 또는
`--timeframes 1h`(가벼움) 후 `--timeframes 15m --append`, 요약만은 `--from-csv`.
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
from backtest.harness import SEGMENT_IS, SEGMENT_OOS
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import _FeatureExtractor, harness_prepare
from backtest.zone_limit_backtest import (
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockParams
from strategy.order_blocks import OrderBlockDetector

# --------------------------------------------------------------------------- #
# 상수 — WAN-111/114/117과 같은 못 박은 창·유니버스
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
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

SEGMENTS: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS)

_WIDTH_QUANTILES = 5
"""존폭 통제 층 수. 층 안에서 병합/단일을 대조한다."""
_MIN_TRADES_FOR_VERDICT = 20  # WAN-70/77/117과 동일.
_MIN_GROUP = 5
"""대조 한쪽(병합 또는 단일)이 이 미만이면 그 셀의 대조는 판정 불가로 둔다."""
_PERMUTATIONS = 2000
_ALPHA = 0.05
_SEED = 134
_CONTAM_THRESHOLD = 0.05
"""ablation 거래 수 차이(%). 초과면 off−on 델타를 순수 병합 효과로 인용하지 않는다."""

REPORTS_DIR = Path("backtest/reports")


# --------------------------------------------------------------------------- #
# 라벨링된 거래 (병합 축)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MergeTrade:
    """결과 라벨 + 병합 특징이 붙은 한 거래."""

    symbol: str
    timeframe: str
    segment: str
    trigger_time: int
    broke: bool
    """True=무효화 손절(뚫림), False=1.5R 익절(버팀)."""
    r_multiple: float
    combined: bool
    num_component_obs: int
    zone_width_atr: float | None
    volume_pctl: float | None
    """셀(심볼×TF) 내부 `ob_volume` 순위 백분위(0~1). `_annotate_volume_pctl`이 채운다."""
    ob_volume: float | None


def label_merge_cell(market: harness.MarketData, *, params: ConfluenceParams) -> list[MergeTrade]:
    """한 (심볼, TF)의 채택 엔진 거래를 결과·병합 특징으로 라벨링한다.

    WAN-117 `label_cell`과 같은 파이프라인(같은 후보·시퀀서·`_FeatureExtractor`)을 쓰되,
    `zone_width_atr` + 병합 축(`combined`·`num_component_obs`·`ob_volume`)만 실어 낸다.
    `zone_width_atr`는 WAN-117과 **같은 계산**(직전 확정봉 ATR)이라 두 리포트가 교차 검산된다.
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
    )
    if not candidates:
        return []
    frame = harness_prepare(market.htf_df)
    extractor = _FeatureExtractor.build(frame)

    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * harness.IS_FRACTION)

    labeled: list[MergeTrade] = []
    for cand, _trade in sequence_with_candidates(candidates, cfg):
        if cand.reason is ExitReason.STOP_LOSS:
            broke, r_mult = True, -1.0
        elif cand.reason is ExitReason.TAKE_PROFIT:
            broke, r_mult = False, params.take_profit_r
        else:
            continue  # END_OF_DATA: 결과 미확정 → 제외.
        ob = cand.order_block
        if ob is None:
            continue
        feats = extractor.features_for(cand)
        zone_width = feats.get("zone_width_atr") if feats is not None else None
        segment = SEGMENT_IS if cand.trigger_time < is_boundary else SEGMENT_OOS
        labeled.append(
            MergeTrade(
                symbol=market.symbol,
                timeframe=market.timeframe,
                segment=segment,
                trigger_time=cand.trigger_time,
                broke=broke,
                r_multiple=r_mult,
                combined=bool(ob.combined),
                num_component_obs=int(ob.num_component_obs),
                zone_width_atr=zone_width,
                volume_pctl=None,
                ob_volume=float(ob.ob_volume),
            )
        )
    return _annotate_volume_pctl(labeled)


def _annotate_volume_pctl(labeled: list[MergeTrade]) -> list[MergeTrade]:
    """`volume_pctl`을 셀(심볼×TF) 내부 `ob_volume` 순위 백분위로 채운다(WAN-117과 동일).

    `MergeTrade`가 frozen이라 제자리 수정 대신 교체 리스트를 만든다.
    """
    by_cell: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, lt in enumerate(labeled):
        if lt.ob_volume is not None:
            by_cell[(lt.symbol, lt.timeframe)].append(i)
    pctl: dict[int, float] = {}
    for idxs in by_cell.values():
        order = sorted(idxs, key=lambda i: labeled[i].ob_volume or 0.0)
        n = len(order)
        for rank, i in enumerate(order):
            pctl[i] = rank / (n - 1) if n > 1 else 0.5
    out: list[MergeTrade] = []
    for i, lt in enumerate(labeled):
        out.append(
            MergeTrade(
                symbol=lt.symbol,
                timeframe=lt.timeframe,
                segment=lt.segment,
                trigger_time=lt.trigger_time,
                broke=lt.broke,
                r_multiple=lt.r_multiple,
                combined=lt.combined,
                num_component_obs=lt.num_component_obs,
                zone_width_atr=lt.zone_width_atr,
                volume_pctl=pctl.get(i),
                ob_volume=lt.ob_volume,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 통계 유틸 — 점이연 상관 + 층화 라벨 순열 (WAN-117 일반화)
# --------------------------------------------------------------------------- #


def _corr(values: list[float], labels: list[float]) -> float | None:
    """corr(연속/이진 값, 이진 라벨). 표본 <3 또는 한쪽 분산 0이면 None."""
    n = len(values)
    if n < 3:
        return None
    mean_v = sum(values) / n
    mean_l = sum(labels) / n
    cov = sum((v - mean_v) * (le - mean_l) for v, le in zip(values, labels, strict=True))
    var_v = sum((v - mean_v) ** 2 for v in values)
    var_l = sum((le - mean_l) ** 2 for le in labels)
    if var_v <= 0 or var_l <= 0:
        return None
    return float(cov / (var_v * var_l) ** 0.5)


def stratified_permutation_p(
    values: list[float],
    broke: list[bool],
    strata: Sequence[object],
    *,
    permutations: int = _PERMUTATIONS,
    seed: int = _SEED,
) -> tuple[float | None, float | None]:
    """(상관, 양측 p값). 각 층 안에서 뚫림/버팀 라벨만 섞는 매칭 널.

    층을 심볼로 잡으면 WAN-117 널과 같고, (심볼 × 존폭 분위)로 잡으면 폭·심볼 구성을
    보존한 채 병합↔뚫림 연관만 끊는다(폭 통제 검정).
    """
    n = len(values)
    if n < _MIN_TRADES_FOR_VERDICT:
        return None, None
    labels = [1.0 if b else 0.0 for b in broke]
    actual = _corr(values, labels)
    if actual is None:
        return None, None
    groups: dict[object, list[int]] = defaultdict(list)
    for i, key in enumerate(strata):
        groups[key].append(i)
    rng = random.Random(seed)
    target = abs(actual)
    extreme = 0
    for _ in range(permutations):
        shuffled = labels.copy()
        for idxs in groups.values():
            pool = [labels[i] for i in idxs]
            rng.shuffle(pool)
            for slot, i in enumerate(idxs):
                shuffled[i] = pool[slot]
        corr = _corr(values, shuffled)
        if corr is not None and abs(corr) >= target - 1e-12:
            extreme += 1
    return actual, extreme / permutations


def _width_quantile_labels(widths: list[float], n_quantiles: int) -> list[int] | None:
    """존폭을 분위 정수 라벨로. 분위가 안 나뉘면(중복 과다) None."""
    if len(widths) < n_quantiles:
        return None
    try:
        labels = pd.qcut(widths, n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return None
    return [int(x) for x in labels]


# --------------------------------------------------------------------------- #
# 1층: 병합 vs 단일 대조 + 유의성
# --------------------------------------------------------------------------- #


class ContrastRow(BaseModel):
    """한 (TF, 구간)의 병합/단일 대조 + 심볼 층화 순열 검정 (6심볼 풀링)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    n: int
    n_merged: int
    n_single: int
    broke_merged: float | None
    broke_single: float | None
    broke_diff: float | None
    """병합 손절률 − 단일 손절률(%p 원자료, 소수). 양수 = 병합이 더 뚫림."""
    meanr_merged: float | None
    meanr_single: float | None
    width_merged: float | None
    width_single: float | None
    correlation: float | None
    """corr(combined, 뚫림). 심볼 층화 순열의 통계량."""
    p_value: float | None
    permutations: int


def _cell_trades(labeled: list[MergeTrade], timeframe: str, segment: str) -> list[MergeTrade]:
    return [lt for lt in labeled if lt.timeframe == timeframe and lt.segment == segment]


def _group_stats(trades: list[MergeTrade]) -> tuple[float | None, float | None, float | None]:
    """(손절률, 평균R, 평균 존폭). 표본 없으면 None."""
    if not trades:
        return None, None, None
    broke = sum(1 for lt in trades if lt.broke) / len(trades)
    meanr = sum(lt.r_multiple for lt in trades) / len(trades)
    widths = [lt.zone_width_atr for lt in trades if lt.zone_width_atr is not None]
    width = sum(widths) / len(widths) if widths else None
    return broke, meanr, width


def contrast_row(
    labeled: list[MergeTrade],
    *,
    timeframe: str,
    segment: str,
    permutations: int = _PERMUTATIONS,
) -> ContrastRow:
    """병합/단일 손절률·평균R·존폭 대조 + corr(combined, 뚫림)의 심볼 층화 순열."""
    trades = _cell_trades(labeled, timeframe, segment)
    merged = [lt for lt in trades if lt.combined]
    single = [lt for lt in trades if not lt.combined]
    bm, rm, wm = _group_stats(merged)
    bs, rs, ws = _group_stats(single)
    diff = bm - bs if bm is not None and bs is not None else None

    values = [1.0 if lt.combined else 0.0 for lt in trades]
    broke = [lt.broke for lt in trades]
    strata = [lt.symbol for lt in trades]
    corr, p = (None, None)
    used_perm = 0
    if len(merged) >= _MIN_GROUP and len(single) >= _MIN_GROUP:
        corr, p = stratified_permutation_p(values, broke, strata, permutations=permutations)
        if p is not None:
            used_perm = permutations
    return ContrastRow(
        timeframe=timeframe,
        segment=segment,
        n=len(trades),
        n_merged=len(merged),
        n_single=len(single),
        broke_merged=bm,
        broke_single=bs,
        broke_diff=diff,
        meanr_merged=rm,
        meanr_single=rs,
        width_merged=wm,
        width_single=ws,
        correlation=corr,
        p_value=p,
        permutations=used_perm,
    )


# --------------------------------------------------------------------------- #
# 2층: 존폭 통제
# --------------------------------------------------------------------------- #


class WidthControlRow(BaseModel):
    """한 (TF, 구간)의 존폭 통제 검정 + 분위별 병합/단일 손절률."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    n: int
    correlation: float | None
    """corr(combined, 뚫림). 순열 층 = (심볼 × 존폭 분위)."""
    p_value: float | None
    permutations: int
    quantile_broke: list[dict[str, float | int | None]]
    """존폭 분위별 {q, n_merged, n_single, broke_merged, broke_single}."""


def width_control_row(
    labeled: list[MergeTrade],
    *,
    timeframe: str,
    segment: str,
    permutations: int = _PERMUTATIONS,
    n_quantiles: int = _WIDTH_QUANTILES,
) -> WidthControlRow:
    """존폭 분위 안에서 병합/단일을 대조. 순열 층에 존폭 분위를 넣어 폭을 통제한다."""
    trades = [
        lt for lt in _cell_trades(labeled, timeframe, segment) if lt.zone_width_atr is not None
    ]
    widths = [lt.zone_width_atr for lt in trades if lt.zone_width_atr is not None]
    q_labels = _width_quantile_labels(widths, n_quantiles)
    if q_labels is None or len(trades) < _MIN_TRADES_FOR_VERDICT:
        return WidthControlRow(
            timeframe=timeframe,
            segment=segment,
            n=len(trades),
            correlation=None,
            p_value=None,
            permutations=0,
            quantile_broke=[],
        )
    values = [1.0 if lt.combined else 0.0 for lt in trades]
    broke = [lt.broke for lt in trades]
    strata = [(lt.symbol, q) for lt, q in zip(trades, q_labels, strict=True)]
    corr, p = stratified_permutation_p(values, broke, list(strata), permutations=permutations)

    per_q: dict[int, list[MergeTrade]] = defaultdict(list)
    for lt, q in zip(trades, q_labels, strict=True):
        per_q[q].append(lt)
    quantile_broke: list[dict[str, float | int | None]] = []
    for q in sorted(per_q):
        cell = per_q[q]
        merged = [lt for lt in cell if lt.combined]
        single = [lt for lt in cell if not lt.combined]
        quantile_broke.append(
            {
                "q": q + 1,
                "n_merged": len(merged),
                "n_single": len(single),
                "broke_merged": (
                    sum(1 for lt in merged if lt.broke) / len(merged) if merged else None
                ),
                "broke_single": (
                    sum(1 for lt in single if lt.broke) / len(single) if single else None
                ),
            }
        )
    return WidthControlRow(
        timeframe=timeframe,
        segment=segment,
        n=len(trades),
        correlation=corr,
        p_value=p,
        permutations=permutations if p is not None else 0,
        quantile_broke=quantile_broke,
    )


# --------------------------------------------------------------------------- #
# 4층: 거래량 오염 · 5층: 심볼 편중
# --------------------------------------------------------------------------- #


class VolumeRow(BaseModel):
    """한 (TF, 구간)의 `combined`↔`volume_pctl` 오염."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    n: int
    vol_merged: float | None
    vol_single: float | None
    correlation: float | None
    """corr(combined, volume_pctl). 양수 = 병합존일수록 거래량 퍼센타일이 높다."""


def volume_row(labeled: list[MergeTrade], *, timeframe: str, segment: str) -> VolumeRow:
    trades = [lt for lt in _cell_trades(labeled, timeframe, segment) if lt.volume_pctl is not None]
    merged = [lt.volume_pctl for lt in trades if lt.combined and lt.volume_pctl is not None]
    single = [lt.volume_pctl for lt in trades if not lt.combined and lt.volume_pctl is not None]
    values = [1.0 if lt.combined else 0.0 for lt in trades]
    pctls = [lt.volume_pctl for lt in trades if lt.volume_pctl is not None]
    corr = _corr(values, pctls) if len(trades) >= _MIN_TRADES_FOR_VERDICT else None
    return VolumeRow(
        timeframe=timeframe,
        segment=segment,
        n=len(trades),
        vol_merged=sum(merged) / len(merged) if merged else None,
        vol_single=sum(single) / len(single) if single else None,
        correlation=corr,
    )


def leave_one_out(
    labeled: list[MergeTrade], *, timeframe: str, segment: str
) -> dict[str, float | None]:
    """심볼을 하나씩 빼며 병합−단일 손절률 차이를 재계산(편중 검사)."""
    trades = _cell_trades(labeled, timeframe, segment)
    symbols = sorted({lt.symbol for lt in trades})
    out: dict[str, float | None] = {}
    for drop in symbols:
        kept = [lt for lt in trades if lt.symbol != drop]
        merged = [lt for lt in kept if lt.combined]
        single = [lt for lt in kept if not lt.combined]
        if len(merged) < _MIN_GROUP or len(single) < _MIN_GROUP:
            out[drop] = None
            continue
        bm = sum(1 for lt in merged if lt.broke) / len(merged)
        bs = sum(1 for lt in single if lt.broke) / len(single)
        out[drop] = bm - bs
    return out


# --------------------------------------------------------------------------- #
# 3층: combine_obs ablation
# --------------------------------------------------------------------------- #


class AblationRow(BaseModel):
    """`combine_obs` on/off 한 팔·한 셀."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    """`on`=채택 기본값(combine_obs=True) · `off`=combine_obs=False."""
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    fill_rate: float | None
    mean_r: float | None


#: on/off 팔의 `combine_obs` 값. 진입 파라미터(`ConfluenceParams`)는 두 팔이 **동일**하다 —
#: `combine_obs`는 `OrderBlockParams`(탐지 파라미터)에 있고 `ConfluenceParams`에는 없기
#: 때문이다. ⚠️ 그래서 팔의 차이는 **탐지 결과**(`OrderBlockResult`)에서만 나온다.
_ARM_COMBINE: dict[str, bool] = {"on": True, "off": False}


def run_ablation_cell(market: harness.MarketData) -> list[AblationRow]:
    """한 (심볼, TF)에서 on/off 팔을 IS/OOS로 돌려 손익·MDD·거래수·체결률을 낸다.

    ⚠️ `combine_obs`는 **탐지 시점** 파라미터다 — `OrderBlockDetector.run`이 병합 여부에 따라
    `retap_signals`를 다르게 낸다(`order_blocks.py:566`). 따라서 각 팔은 자기 `combine_obs`로
    **따로 탐지**해야 한다. 한 번 탐지해 공유하면 두 팔이 같은 병합 시그널을 봐 결과가 비트
    단위로 같아진다(WAN-91/95/112/123 부류의 조용한 실패). 진입 파라미터는 두 팔이 같다
    (`combine_obs`는 `ConfluenceParams`에 없으므로 그 축은 탐지에서만 갈린다).
    """
    if market.empty or market.df_1m.empty:
        return []
    params = harness.build_params()  # 채택 기본값. 두 팔 공통(진입 규칙은 combine_obs와 무관).
    cfg = harness.build_config(market.timeframe)
    rows: list[AblationRow] = []
    for segment in harness.segments_for(oos=True):
        if segment.name == harness.SEGMENT_FULL:
            continue  # IS/OOS만 병기(전 구간은 리포트에서 안 쓴다).
        seg_market = harness.slice_market(market, segment)
        if seg_market.empty or seg_market.df_1m.empty:
            continue
        for arm, combine in _ARM_COMBINE.items():
            ob_params = OrderBlockParams(combine_obs=combine)
            ob_result = OrderBlockDetector(ob_params).run(seg_market.htf_df)
            outcome = harness.run_once(
                seg_market, params=params, cfg=cfg, order_block_result=ob_result
            )
            m = outcome.result.metrics
            stats = outcome.stats
            rows.append(
                AblationRow(
                    symbol=market.symbol,
                    timeframe=market.timeframe,
                    segment=segment.name,
                    arm=arm,
                    num_trades=m.num_trades,
                    win_rate=m.win_rate,
                    total_return=m.total_return,
                    max_drawdown=m.max_drawdown,
                    fill_rate=stats.fill_rate if stats else None,
                    mean_r=harness.mean_r(outcome.result, params.take_profit_r),
                )
            )
    return rows


def ablation_symbol_mean(rows: list[AblationRow]) -> pd.DataFrame:
    """(TF, 구간, 팔)별 심볼평균 + 거래수 오염 게이트."""
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([r.model_dump() for r in rows])
    grouped = frame.groupby(["timeframe", "segment", "arm"], as_index=False).agg(
        total_return=("total_return", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        win_rate=("win_rate", "mean"),
        num_trades=("num_trades", "mean"),
        fill_rate=("fill_rate", "mean"),
        mean_r=("mean_r", "mean"),
        n_symbols=("symbol", "nunique"),
    )
    return grouped


def ablation_delta(mean_frame: pd.DataFrame) -> pd.DataFrame:
    """(TF, 구간)별 off − on 델타 + 거래수 오염 플래그."""
    if mean_frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for (tf, seg), grp in mean_frame.groupby(["timeframe", "segment"]):
        by_arm = {str(r["arm"]): r for _, r in grp.iterrows()}
        if "on" not in by_arm or "off" not in by_arm:
            continue
        on, off = by_arm["on"], by_arm["off"]
        on_tr, off_tr = float(on["num_trades"]), float(off["num_trades"])
        contam = abs(off_tr - on_tr) / on_tr if on_tr else 0.0
        rows.append(
            {
                "timeframe": tf,
                "segment": seg,
                "ret_on": float(on["total_return"]),
                "ret_off": float(off["total_return"]),
                "ret_delta": float(off["total_return"]) - float(on["total_return"]),
                "mdd_on": float(on["max_drawdown"]),
                "mdd_off": float(off["max_drawdown"]),
                "trades_on": on_tr,
                "trades_off": off_tr,
                "trade_contam": contam,
                "contaminated": bool(contam > _CONTAM_THRESHOLD),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _pick_contrast(rows: list[ContrastRow], timeframe: str, segment: str) -> ContrastRow | None:
    return next((r for r in rows if r.timeframe == timeframe and r.segment == segment), None)


def _pick_width(
    rows: list[WidthControlRow], timeframe: str, segment: str
) -> WidthControlRow | None:
    return next((r for r in rows if r.timeframe == timeframe and r.segment == segment), None)


def verdict_for_tf(
    contrast: list[ContrastRow],
    width: list[WidthControlRow],
    *,
    timeframe: str,
    alpha: float = _ALPHA,
) -> str:
    """(a) 폭 통제 후에도 병합이 손절을 늘림 / (b) 폭의 대리변수 / (c) 차이 없음.

    유의는 **OOS에서** α를 넘고(단일 특징이라 Bonferroni 보정 없음) **IS에서도 같은 부호**를
    요구한다(IS→OOS 부호 뒤집힘 방지 — WAN-88/114/117이 거듭 본 함정).
    """
    c_oos = _pick_contrast(contrast, timeframe, SEGMENT_OOS)
    c_is = _pick_contrast(contrast, timeframe, SEGMENT_IS)
    w_oos = _pick_width(width, timeframe, SEGMENT_OOS)
    w_is = _pick_width(width, timeframe, SEGMENT_IS)

    if c_oos is None or c_oos.p_value is None or c_oos.correlation is None:
        return f"**{timeframe}**: 판정 불가 — 유효 표본(병합/단일 각 {_MIN_GROUP}건 이상) 없음."

    raw_sig = (
        c_oos.p_value <= alpha
        and c_is is not None
        and c_is.correlation is not None
        and (c_oos.correlation > 0) == (c_is.correlation > 0)
    )
    if not raw_sig:
        return (
            f"**{timeframe}**: **(c) 병합은 손절률에 중립** — 병합/단일 손절률 대조가 OOS에서 "
            f"매칭 널을 못 넘거나(p={c_oos.p_value:.4f}) IS와 부호가 어긋난다. "
            f"OOS 병합 {(_pct(c_oos.broke_merged))} vs 단일 {(_pct(c_oos.broke_single))} "
            f"(corr={_num(c_oos.correlation)}). 병합이 손절을 늘린다는 증거가 없다."
        )

    width_sig = (
        w_oos is not None
        and w_oos.p_value is not None
        and w_oos.correlation is not None
        and w_oos.p_value <= alpha
        and w_is is not None
        and w_is.correlation is not None
        and (w_oos.correlation > 0) == (w_is.correlation > 0)
    )
    if width_sig and w_oos is not None:
        return (
            f"**{timeframe}**: **(a) 폭을 통제해도 병합이 손절을 늘린다** — 원 대조 OOS "
            f"corr={_num(c_oos.correlation)}(p={c_oos.p_value:.4f})가 존폭 분위 통제 후에도 "
            f"corr={_num(w_oos.correlation)}(p={w_oos.p_value:.4f})로 살아남고 IS와 같은 부호다. "
            "병합 규칙(손절선을 최외곽 존에 맞춤) 재검토 이슈를 제안한다."
        )
    w_txt = "판정 불가" if w_oos is None or w_oos.p_value is None else f"p={w_oos.p_value:.4f}"
    return (
        f"**{timeframe}**: **(b) 병합은 `zone_width_atr`의 대리변수** — 원 대조는 OOS에서 "
        f"유의(corr={_num(c_oos.correlation)}, p={c_oos.p_value:.4f})하지만 존폭 분위로 통제하면 "
        f"사라진다({w_txt}). 병합존이 더 뚫리는 건 병합 자체가 아니라 **정의상 넓어서**이고, "
        "이는 WAN-117의 유일 강건 축 `zone_width_atr`와 같은 축이다(WAN-133과 합류)."
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:+.3f}"


# --------------------------------------------------------------------------- #
# 실험 실행
# --------------------------------------------------------------------------- #


@dataclass
class ExperimentResult:
    labeled: list[MergeTrade] = field(default_factory=list)
    contrast: list[ContrastRow] = field(default_factory=list)
    width: list[WidthControlRow] = field(default_factory=list)
    volume: list[VolumeRow] = field(default_factory=list)
    ablation: list[AblationRow] = field(default_factory=list)


def run_experiment(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    permutations: int = _PERMUTATIONS,
    db_path: str = harness.DB_PATH,
    skip_ablation: bool = False,
) -> ExperimentResult:
    """6심볼 × {15m,1h}: 라벨링 → 병합 대조 → 폭 통제 → 거래량 → ablation."""
    params = ConfluenceParams()  # 채택 기본값.
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    labeled: list[MergeTrade] = []
    ablation: list[AblationRow] = []
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
            cell = label_merge_cell(market, params=params)
            labeled.extend(cell)
            merged_n = sum(1 for lt in cell if lt.combined)
            print(
                f"[wan134] {norm} {timeframe}: labeled={len(cell)} "
                f"(merged={merged_n}, single={len(cell) - merged_n})"
            )
            if not skip_ablation:
                abl = run_ablation_cell(market)
                ablation.extend(abl)
                print(f"[wan134] {norm} {timeframe}: ablation rows={len(abl)}")

    contrast: list[ContrastRow] = []
    width: list[WidthControlRow] = []
    volume: list[VolumeRow] = []
    for timeframe in timeframes:
        for segment in SEGMENTS:
            contrast.append(
                contrast_row(
                    labeled, timeframe=timeframe, segment=segment, permutations=permutations
                )
            )
            width.append(
                width_control_row(
                    labeled, timeframe=timeframe, segment=segment, permutations=permutations
                )
            )
            volume.append(volume_row(labeled, timeframe=timeframe, segment=segment))
    return ExperimentResult(
        labeled=labeled, contrast=contrast, width=width, volume=volume, ablation=ablation
    )


# --------------------------------------------------------------------------- #
# CSV 직렬화
# --------------------------------------------------------------------------- #


def _labeled_to_frame(labeled: list[MergeTrade]) -> pd.DataFrame:
    columns = [
        "symbol",
        "timeframe",
        "segment",
        "trigger_time",
        "broke",
        "r_multiple",
        "combined",
        "num_component_obs",
        "zone_width_atr",
        "volume_pctl",
        "ob_volume",
    ]
    records = [
        {
            "symbol": lt.symbol,
            "timeframe": lt.timeframe,
            "segment": lt.segment,
            "trigger_time": lt.trigger_time,
            "broke": lt.broke,
            "r_multiple": lt.r_multiple,
            "combined": lt.combined,
            "num_component_obs": lt.num_component_obs,
            "zone_width_atr": lt.zone_width_atr,
            "volume_pctl": lt.volume_pctl,
            "ob_volume": lt.ob_volume,
        }
        for lt in labeled
    ]
    return pd.DataFrame(records, columns=columns)


def _labeled_from_frame(frame: pd.DataFrame) -> list[MergeTrade]:
    out: list[MergeTrade] = []
    for _, r in frame.iterrows():
        out.append(
            MergeTrade(
                symbol=str(r["symbol"]),
                timeframe=str(r["timeframe"]),
                segment=str(r["segment"]),
                trigger_time=int(r["trigger_time"]),
                broke=bool(r["broke"]),
                r_multiple=float(r["r_multiple"]),
                combined=bool(r["combined"]),
                num_component_obs=int(r["num_component_obs"]),
                zone_width_atr=_opt_float(r["zone_width_atr"]),
                volume_pctl=_opt_float(r["volume_pctl"]),
                ob_volume=_opt_float(r["ob_volume"]),
            )
        )
    return out


def _ablation_to_frame(rows: list[AblationRow]) -> pd.DataFrame:
    columns = [
        "symbol",
        "timeframe",
        "segment",
        "arm",
        "num_trades",
        "win_rate",
        "total_return",
        "max_drawdown",
        "fill_rate",
        "mean_r",
    ]
    return pd.DataFrame([r.model_dump() for r in rows], columns=columns)


def _ablation_from_frame(frame: pd.DataFrame) -> list[AblationRow]:
    out: list[AblationRow] = []
    for _, r in frame.iterrows():
        out.append(
            AblationRow(
                symbol=str(r["symbol"]),
                timeframe=str(r["timeframe"]),
                segment=str(r["segment"]),
                arm=str(r["arm"]),
                num_trades=int(r["num_trades"]),
                win_rate=float(r["win_rate"]),
                total_return=float(r["total_return"]),
                max_drawdown=float(r["max_drawdown"]),
                fill_rate=_opt_float(r["fill_rate"]),
                mean_r=_opt_float(r["mean_r"]),
            )
        )
    return out


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    fv = float(value)  # type: ignore[arg-type]
    return None if fv != fv else fv


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def build_summary_markdown(
    result: ExperimentResult, *, labeled_csv: Path, ablation_csv: Path
) -> str:
    lines: list[str] = []
    lines.append("# WAN-134 존 병합(`combine_obs`) 부검 — 폭 통제 + ablation\n")
    lines.append(
        f"6심볼(BTC/ETH/SOL/BNB/XRP/TRX) × 15m·1h, 못 박은 창 **{DEFAULT_START} ~ "
        f"{DEFAULT_END}**, 채택 기본값(`ConfluenceParams()`) · 공식 렌즈 `baseline`(WAN-128 단독). "
        "라벨: 뚫림=무효화 손절 · 버팀=1.5R 익절, END_OF_DATA 제외. "
        f"재현: `python -m backtest.wan134_zone_merge_autopsy`. 라벨 원자료: `{labeled_csv}` · "
        f"ablation: `{ablation_csv}`.\n"
    )

    # 표본 기저
    lines.append("## 0. 표본 · 병합 비율\n")
    lines.append("| TF | 구간 | n | 병합 | 단일 | 병합비율 |\n| -- | -- | -- | -- | -- | -- |")
    for tf in DEFAULT_TIMEFRAMES:
        for seg in SEGMENTS:
            cell = _cell_trades(result.labeled, tf, seg)
            if not cell:
                continue
            merged = sum(1 for lt in cell if lt.combined)
            frac = merged / len(cell) if cell else 0.0
            single = len(cell) - merged
            lines.append(
                f"| {tf} | {seg} | {len(cell)} | {merged} | {single} | {frac * 100:.1f}% |"
            )
    lines.append("")

    # 판정
    lines.append("## 판정 (OOS 매칭 널 + IS 동일 부호 · 폭 통제)\n")
    for tf in DEFAULT_TIMEFRAMES:
        lines.append(f"* {verdict_for_tf(result.contrast, result.width, timeframe=tf)}")
    lines.append("")

    # 1층 대조
    lines.append("## 1. 병합 vs 단일 대조 (6심볼 풀링 · 심볼 층화 순열)\n")
    lines.append(
        "corr = corr(combined, 뚫림)>0 이면 병합존일수록 더 뚫림. p = 심볼 층화 라벨 순열 양측값 "
        f"({_PERMUTATIONS}회). 존폭은 `zone_width_atr` 평균.\n"
    )
    lines.append(
        "| TF | 구간 | 병합 손절/R/폭 | 단일 손절/R/폭 | 손절차 | corr | p |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    for cr in result.contrast:
        mstats = f"{_pct(cr.broke_merged)}/{_num(cr.meanr_merged)}/{_num(cr.width_merged)}"
        sstats = f"{_pct(cr.broke_single)}/{_num(cr.meanr_single)}/{_num(cr.width_single)}"
        diff = "—" if cr.broke_diff is None else f"{cr.broke_diff * 100:+.1f}%p"
        p_str = "—" if cr.p_value is None else f"{cr.p_value:.4f}"
        lines.append(
            f"| {cr.timeframe} | {cr.segment} | {mstats} | {sstats} | {diff} | "
            f"{_num(cr.correlation)} | {p_str} |"
        )
    lines.append("")

    # 2층 폭 통제
    lines.append("## 2. 존폭 통제 — (심볼 × 존폭 분위) 층 순열\n")
    lines.append(
        "폭을 분위로 통제한 뒤에도 corr(combined, 뚫림)이 남는지. 존폭 분위 안에서 병합/단일 "
        "손절률도 병기(넓은 존일수록 둘 다 더 뚫리면 폭이 원인).\n"
    )
    lines.append("| TF | 구간 | n | 통제 corr | p |\n| -- | -- | -- | -- | -- |")
    for wr in result.width:
        p_str = "—" if wr.p_value is None else f"{wr.p_value:.4f}"
        lines.append(
            f"| {wr.timeframe} | {wr.segment} | {wr.n} | {_num(wr.correlation)} | {p_str} |"
        )
    lines.append("")
    lines.append("존폭 분위별 손절률(병합 / 단일):\n")
    lines.append("| TF | 구간 | Q1 | Q2 | Q3 | Q4 | Q5 |\n| -- | -- | -- | -- | -- | -- | -- |")
    for wr in result.width:
        by_q: dict[int, dict[str, float | int | None]] = {}
        for q in wr.quantile_broke:
            q_rank = q["q"]
            if isinstance(q_rank, int):
                by_q[q_rank] = q
        parts: list[str] = []
        for qi in range(1, _WIDTH_QUANTILES + 1):
            q_cell = by_q.get(qi)
            if q_cell is None:
                parts.append("—")
                continue
            bm = q_cell["broke_merged"]
            bs = q_cell["broke_single"]
            parts.append(
                f"{_pct(bm if isinstance(bm, float) else None)}/"
                f"{_pct(bs if isinstance(bs, float) else None)}"
            )
        lines.append(f"| {wr.timeframe} | {wr.segment} | " + " | ".join(parts) + " |")
    lines.append("")

    # 4층 거래량 오염
    lines.append("## 4. 거래량 오염 — `_make_merged_group`이 `ob_volume`을 합산\n")
    lines.append(
        "병합존은 구성 존 거래량의 **합**이라 `volume_pctl`이 자동으로 부풀려진다. "
        "corr(combined, volume_pctl)>0이면 그 오염이 실재한다.\n"
    )
    lines.append(
        "| TF | 구간 | n | 병합 vol% | 단일 vol% | corr |\n| -- | -- | -- | -- | -- | -- |"
    )
    for vr in result.volume:
        lines.append(
            f"| {vr.timeframe} | {vr.segment} | {vr.n} | {_pct(vr.vol_merged)} | "
            f"{_pct(vr.vol_single)} | {_num(vr.correlation)} |"
        )
    lines.append("")

    # 5층 심볼 편중
    lines.append("## 5. 심볼 편중 — leave-one-out 병합−단일 손절차 (OOS)\n")
    lines.append("한 심볼을 빼도 손절차 부호·크기가 유지되면 편중 아님.\n")
    all_syms = sorted({lt.symbol for lt in result.labeled})
    header = "| TF | " + " | ".join(f"−{s.split('/')[0]}" for s in all_syms) + " |"
    lines.append(header + "\n| -- | " + " | ".join("--" for _ in all_syms) + " |")
    for tf in DEFAULT_TIMEFRAMES:
        loo = leave_one_out(result.labeled, timeframe=tf, segment=SEGMENT_OOS)
        cells = []
        for s in all_syms:
            v = loo.get(s)
            cells.append("—" if v is None else f"{v * 100:+.1f}%p")
        lines.append(f"| {tf} | " + " | ".join(cells) + " |")
    lines.append("")

    # 3층 ablation
    lines.append("## 3. Ablation — `combine_obs=False` 팔 (⚠️ 거래 집합이 바뀜)\n")
    if result.ablation:
        mean_frame = ablation_symbol_mean(result.ablation)
        delta = ablation_delta(mean_frame)
        lines.append(
            "심볼평균. `on`=채택 기본값(병합) · `off`=병합 끔. ⚠️ **거래 수 차이 "
            f"{int(_CONTAM_THRESHOLD * 100)}% 초과면 off−on 델타를 순수 병합 효과로 인용 금지** "
            "(병합을 끄면 존 개수·첫 탭 정의가 바뀌어 거래 집합이 달라진다 — WAN-123/126 부류).\n"
        )
        lines.append(
            "| TF | 구간 | ret on→off | Δret | MDD on→off | 거래 on→off | 오염 |\n"
            "| -- | -- | -- | -- | -- | -- | -- |"
        )
        for _, r in delta.iterrows():
            contam = f"{r['trade_contam'] * 100:.0f}%"
            flag = " ⚠️" if r["contaminated"] else ""
            lines.append(
                f"| {r['timeframe']} | {r['segment']} | "
                f"{r['ret_on'] * 100:+.2f}→{r['ret_off'] * 100:+.2f}% | "
                f"{r['ret_delta'] * 100:+.2f}%p | "
                f"{r['mdd_on'] * 100:.2f}→{r['mdd_off'] * 100:.2f}% | "
                f"{r['trades_on']:.0f}→{r['trades_off']:.0f} | {contam}{flag} |"
            )
    else:
        lines.append("_(ablation 미실행 — `--skip-ablation` 또는 데이터 없음.)_")
    lines.append("")

    lines.append("## 결론\n")
    lines.append(
        "위 TF별 판정 참조. **어느 판정이든 「엣지 없음」(WAN-84/88/111/114/124)을 뒤집지 "
        "않는다** — 이 이슈는 병합이 손절을 늘리는 **드래그**인지를 물을 뿐이고, `combined`가 "
        "폭의 대리변수로 판명되면 WAN-133(존폭 기하 대 선별)과 합류한다. 기본값·실거래 "
        "보류(`ALPHABLOCK_LIVE_TRADING=false`)는 불변(측정만). ⚠️ ablation 델타는 거래 집합이 "
        "바뀌므로 오염 게이트를 통과한 셀만 방향으로 읽는다(크기 아님).\n"
    )
    vol_corrs = [vr.correlation for vr in result.volume if vr.correlation is not None]
    if vol_corrs:
        lo, hi = min(vol_corrs), max(vol_corrs)
        lines.append(
            f"**부수 발견 — 거래량 오염 실재**: corr(combined, volume_pctl)이 전 셀 "
            f"{lo:+.2f}~{hi:+.2f}로, `_make_merged_group`의 `ob_volume` 합산이 병합존의 거래량 "
            "퍼센타일을 부풀린다. 거래량 특징(WAN-77/117에서 OOS 붕괴)을 쓰는 후속 이슈는 이 "
            "합산 스케일을 정규화해 재검할 것.\n"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-134 존 병합 부검")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--permutations", type=int, default=_PERMUTATIONS)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--skip-ablation", action="store_true", help="라벨링·검정만(빠름).")
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 라벨/ablation CSV에 이어 붙인다(TF를 나눠 돌릴 때).",
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자 실행 없이 기존 CSV로 검정·요약만 재생성한다.",
    )
    parser.add_argument("--labeled-out", type=Path, default=REPORTS_DIR / "wan134_labeled.csv")
    parser.add_argument("--ablation-out", type=Path, default=REPORTS_DIR / "wan134_ablation.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan134_summary.md")
    args = parser.parse_args(argv)

    timeframes = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())

    if args.from_csv:
        labeled = _labeled_from_frame(pd.read_csv(args.labeled_out))
        ablation = (
            _ablation_from_frame(pd.read_csv(args.ablation_out))
            if args.ablation_out.exists()
            else []
        )
        result = _rebuild_result(labeled, ablation, permutations=args.permutations)
    else:
        run = run_experiment(
            symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            timeframes=timeframes,
            start=args.start,
            end=args.end,
            permutations=args.permutations,
            db_path=args.db,
            skip_ablation=args.skip_ablation,
        )
        labeled = run.labeled
        ablation = run.ablation
        if args.append and args.labeled_out.exists():
            labeled = _labeled_from_frame(pd.read_csv(args.labeled_out)) + labeled
        if args.append and ablation and args.ablation_out.exists():
            ablation = _ablation_from_frame(pd.read_csv(args.ablation_out)) + ablation
        _write_csv(_labeled_to_frame(labeled), args.labeled_out)
        if ablation:
            _write_csv(_ablation_to_frame(ablation), args.ablation_out)
        print(f"[wan134] labeled rows={len(labeled)} → {args.labeled_out}")
        if ablation:
            print(f"[wan134] ablation rows={len(ablation)} → {args.ablation_out}")
        result = _rebuild_result(labeled, ablation, permutations=args.permutations)

    summary = build_summary_markdown(
        result, labeled_csv=args.labeled_out, ablation_csv=args.ablation_out
    )
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan134] summary → {args.summary_out}")
    return 0


def _rebuild_result(
    labeled: list[MergeTrade], ablation: list[AblationRow], *, permutations: int
) -> ExperimentResult:
    """라벨/ablation 리스트로 검정 행들을 재계산한다(--from-csv·--append 공용)."""
    timeframes = tuple(dict.fromkeys(lt.timeframe for lt in labeled))
    contrast: list[ContrastRow] = []
    width: list[WidthControlRow] = []
    volume: list[VolumeRow] = []
    for tf in DEFAULT_TIMEFRAMES:
        if tf not in timeframes:
            continue
        for seg in SEGMENTS:
            contrast.append(
                contrast_row(labeled, timeframe=tf, segment=seg, permutations=permutations)
            )
            width.append(
                width_control_row(labeled, timeframe=tf, segment=seg, permutations=permutations)
            )
            volume.append(volume_row(labeled, timeframe=tf, segment=seg))
    return ExperimentResult(
        labeled=labeled, contrast=contrast, width=width, volume=volume, ablation=ablation
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
