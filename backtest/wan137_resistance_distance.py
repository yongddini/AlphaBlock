"""WAN-137 Phase 1: 저항 오더블록까지의 거리 분포 — (진입TF × 저항TF)로 쪼갠 구조 측정.

사용자 제안(WAN-90 변형 C 구체화): 익절 목표를 고정 1.5R이 아니라 **가장 가까운 반대편
(저항) 오더블록**으로 잡되, 저항은 진입 TF 하나로 한정하지 않고 **사다리 전체**에서 찾는다.

이 모듈은 **Phase 1(분포 측정)** 만 한다 — 손익을 재기 전에 "이 방식이 될 수 있는 구조인가"
부터 본다. 채택 엔진의 진입 셋업마다 진입 시점 기준 **가장 가까운 상방 저항 OB까지의 거리를
R 단위로** 재고, 그 분포를 **저항의 출처 TF별로 분해**해 게이트를 판정한다:

* (a) 다수가 0.5R 미만 → 왕복 비용(~11bp)에 먹힘.
* (b) 다수가 MFE 너머 → 도달 불가(WAN-90 무검열 MFE 대조).
* (c) 저항 없음이 과반 → 폴백이 곧 규칙.

셋 중 하나라도 성립하면 그 (진입TF × 저항TF) 조합은 탈락하고, 분포가 A/B/C 무게 규칙을
**데이터로** 고른다(감으로 고르지 않는다 — 이슈의 최대 장점).

## 무엇을 재는가 — 셋업 단위, 무검열 MFE (설계 결정)

거리·도달성은 **셋업의 성질**(진입가·손절·시각)이지 동시 1포지션 시퀀싱의 산물이 아니다.
그래서 시퀀싱된 6,827거래가 아니라 **체결된 셋업 전체**(baseline "닿으면 체결")에서 잰다 —
시퀀싱은 거리·도달성과 직교하는 동시성 인공물이다. 체결 셋업 집합은 익절과 무관하므로
(익절은 청산만 바꾼다) 채택 엔진의 진입 집합과 **글자 그대로 같고**, 익절만 끈
(`take_profit_mode="line"`) 무검열 팔에서 돌려 `mfe_r`이 1.5R에 잘리지 않게 한다(WAN-90
`_uncensored_params`와 같은 방식). 그래서 도달성 게이트가 정직하다.

## 저항 선택 규칙 (이슈 §3, 착수 전 결정)

* **① 뚫린 저항 제외** — `alive_at`은 breaker(무효화)도 True를 돌려주므로 그것만 믿으면
  "이미 돌파된 자리에 매도"가 된다. 진입 시각까지 클리핑(`_clip_to_time`)한 뒤 `breaker`가
  참인 존(= `break_time <= 진입시각`)을 뺀다. 회귀 테스트가 **동작**으로 고정한다.
* **② 확정 시점 준수(룩어헤드)** — `confirmed_time <= 진입시각`인 존만 본다. WAN-126의
  `indexed_zone_provider`(= `select_active` 클리핑)로만 질의해 미래 존이 새지 않게 한다.
* **③ 근단(아랫변)에 매도** — 롱 익절 목표 = 약세 존의 `bottom`. 진입이 존 근단 지정가인
  것과 대칭. 오프셋 2bp는 Phase 1 거리 측정에는 얹지 않는다(집행 세부 → Phase 2).
* **⑤ 병합 on/off는 격자 축** — `combine=True/False` 둘 다 잰다(WAN-134: 병합 존 폭이
  ~1.8배라 아랫변이 내려와 목표가 가까워진다). 정본은 `combine=False`(뚜렷한 개별 저항).

## 인프라 재사용

* 저항 탐지는 새로 만들지 않는다 — 엔진이 `bearish` 존을 이미 만든다. 진입 TF의 저항은
  후보 생성에 쓴 `OrderBlockResult` 그대로, 하위TF 저항은 WAN-126의 디스크 캐시된 아카이브
  (`detect_ltf_archives`, `data/cache/wan126_ob`)에서 온다.
* 룩어헤드 가드·클리핑은 `backtest.multi_tf_overlap`(WAN-126)을 그대로 쓴다.

⚠️ WAN-126의 판정(「겹침은 유효 선별 규칙 아님」)은 이 이슈와 **무관**하다 — 그쪽은 하위TF
존을 진입 필터로 썼고 여기는 익절 목표로 쓴다. 코드만 재사용한다.

재현:

```
uv run python -m backtest.wan137_resistance_distance --tf 1h            # 1h 먼저(가벼움)
uv run python -m backtest.wan137_resistance_distance --tf 15m --append  # 15m 뒤에 붙임
uv run python -m backtest.wan137_resistance_distance --from-csv         # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import PositionSide
from backtest.multi_tf_overlap import ZoneProvider, indexed_zone_provider
from backtest.run import parse_date_ms
from backtest.wan126_multi_tf_overlap import DEFAULT_CACHE_DIR, detect_ltf_archives
from backtest.zone_limit_backtest import (
    _Candidate,
    _prepare_htf,
    build_zone_limit_candidates,
)
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

# --------------------------------------------------------------------------- #
# 상수 — WAN-111/117/126과 같은 못 박은 창·유니버스
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

#: 진입 TF별 저항 후보 TF 사다리(진입 TF 자신 + 하위TF, 위→아래). 이슈 §Phase 1 사양.
RESISTANCE_TFS: dict[str, tuple[str, ...]] = {
    "15m": ("15m", "5m", "1m"),
    "1h": ("1h", "15m", "5m"),
}
#: "전 TF 최근접" 규칙을 나타내는 가상 저항 TF 이름(사다리에서 가장 가까운 것).
COMBINED_TF = "combined"

SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS)

#: 게이트 임계값(이슈 §Phase 1). 비용 잠식·도달 불가·저항 없음 판정선.
NEAR_R = 0.5  # 이보다 가까운 저항은 왕복 비용에 먹힌다.
FAR_R = 3.0  # 이보다 먼 저항은 사실상 홀딩(현행 1.5R보다 멀다).
MAJORITY = 0.5  # 게이트가 "다수"로 성립하는 비율.

REPORTS_DIR = Path("backtest/reports")


# --------------------------------------------------------------------------- #
# 저항 선택 — 순수 함수(회귀 테스트가 동작으로 고정하는 핵심)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResistanceHit:
    """가장 가까운 유효 저항 하나."""

    timeframe: str
    """이 저항을 그린 TF(사다리 칸)."""
    target: float
    """매도 지정가 = 약세 존 근단(`bottom`)."""
    distance_r: float
    """(target − 진입가) / 1R. 항상 양수(진입가 위 저항만 센다)."""


def nearest_resistance(
    zones: Sequence[OrderBlock],
    *,
    entry_price: float,
    risk: float,
    timeframe: str,
) -> ResistanceHit | None:
    """진입가 위쪽에서 **가장 먼저 닿는 유효 저항**까지의 거리(R)를 낸다.

    `zones`는 진입 시각까지 **이미 클리핑된**(② 확정 시점 준수) 약세(BEARISH) 존이어야
    한다. 여기서 두 필터를 더 건다:

    * **① 뚫린 저항 제외** — 클리핑된 `breaker`가 참인 존(진입 시각 이전에 무효화)은
      저항이 아니라 뒤집힌 지지다. `alive_at`만 믿으면 놓치는 함정(이슈 §①).
    * **③ 진입가 위 근단** — 롱 익절이므로 근단 `bottom`이 진입가보다 위인 존만 저항이다.
      가격은 아래에서 올라오며 `bottom`(가장 낮은 것)에 **먼저 닿는다**.

    `risk`(= 진입가 − 손절가, 롱)가 0 이하면 R을 못 재므로 None. 유효 저항이 없어도 None.
    """
    if risk <= 0:
        return None
    best: OrderBlock | None = None
    for zone in zones:
        if zone.breaker:
            continue  # ① 뚫린 저항 제외.
        if zone.bottom <= entry_price:
            continue  # ③ 진입가 위 근단만.
        if best is None or zone.bottom < best.bottom:
            best = zone
    if best is None:
        return None
    return ResistanceHit(
        timeframe=timeframe,
        target=best.bottom,
        distance_r=(best.bottom - entry_price) / risk,
    )


# --------------------------------------------------------------------------- #
# 셋업 측정
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SetupResistance:
    """체결된 셋업 하나의 (저항TF별) 거리 + 무검열 MFE."""

    symbol: str
    entry_tf: str
    segment: str
    mfe_r: float
    """WAN-90 무검열 MFE(익절 안 끔). 도달성 게이트의 기준."""
    #: 저항TF → 그 TF에서 가장 가까운 저항까지 거리(R). 저항 없으면 키가 없다.
    by_tf: dict[str, float]
    #: "전 TF 최근접"(사다리에서 가장 가까운) 거리와 그 출처 TF. 없으면 (None, None).
    combined_r: float | None
    combined_source: str | None


def _uncensored_params(base: ConfluenceParams) -> ConfluenceParams:
    """익절만 끈 채택 기본값 — MFE를 1.5R에서 검열하지 않는다(WAN-90과 동일).

    `take_profit_mode="line"` + 기본 `use_line_take_profit=False`면 익절 목표가 None이라
    익절 청산이 없다. 진입가·손절·볼린저·오프셋·RSI 게이트는 채택 기본값 그대로다.
    """
    return base.model_copy(update={"take_profit_mode": "line"})


def build_resistance_archives(
    symbol: str,
    entry_tf: str,
    htf_obr: OrderBlockResult,
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: str | None,
    log: bool,
) -> dict[str, OrderBlockResult]:
    """저항 질의용 {저항TF: 아카이브}. 진입 TF 자신은 후보 생성에 쓴 결과 재사용, 하위TF는
    WAN-126 디스크 캐시(`detect_ltf_archives`)에서 온다."""
    ladder = RESISTANCE_TFS[entry_tf]
    lower = tuple(tf for tf in ladder if tf != entry_tf)
    archives: dict[str, OrderBlockResult] = {entry_tf: htf_obr}
    archives.update(
        detect_ltf_archives(
            symbol, lower, start_ms=start_ms, end_ms=end_ms, cache_dir=cache_dir, log=log
        )
    )
    return archives


def _segment_for(trigger_time: int, is_boundary: int) -> str:
    return SEGMENT_IS if trigger_time < is_boundary else SEGMENT_OOS


def measure_setups(
    market: MarketData,
    *,
    combine: bool,
    window_start_ms: int,
    window_end_ms: int,
    cache_dir: str | None = DEFAULT_CACHE_DIR,
    log: bool = False,
) -> list[SetupResistance]:
    """한 (심볼, 진입TF)의 체결 셋업마다 저항 거리 + 무검열 MFE를 잰다.

    후보는 못 박은 전체 창에서 한 번 만들고(무검열 팔), `trigger_time`으로 IS/OOS를 가른다
    (WAN-117 `label_cell`과 같은 방식 — 라벨·특징이 진입 시점 기지라 시각 분할이 안전하다).

    `window_start_ms`/`window_end_ms`는 **요청 창의 경계**(`parse_date_ms(start/end)`)여야
    한다 — 하위TF 아카이브 캐시 키가 WAN-126과 같아야 그 디스크 캐시를 재사용한다. `market`의
    실제 첫/마지막 봉 시각을 쓰면 키가 어긋나 1분봉을 매번 재탐지(심볼당 8분+)하게 된다.
    """
    if market.empty or market.df_1m.empty:
        return []
    entry_tf = market.timeframe
    # ⚠️ 밴드는 WAN-132 이전 값(`tap`)으로 고정한다(Phase 2와 같은 셋업 풀을 봐야 한다).
    params = _uncensored_params(harness.pin_band_bar(harness.build_params(entry_mode="zone_limit")))
    cfg = harness.build_config(entry_tf)

    # 후보 생성에 쓸 진입 TF 오더블록(라인 577과 같이 raw htf_df에서 탐지) — 저항 질의에도
    # 이 결과를 그대로 재사용해 자기-TF 저항이 엔진이 본 존과 정확히 일치하게 한다.
    htf_obr = OrderBlockDetector(harness.LEGACY_OB_PARAMS).run(market.htf_df)
    candidates, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        entry_tf,
        params=params,
        cfg=cfg,
        order_block_result=htf_obr,
    )
    if not candidates:
        return []

    frame = _prepare_htf(market.htf_df)
    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * IS_FRACTION)

    archives = build_resistance_archives(
        market.symbol,
        entry_tf,
        htf_obr,
        start_ms=window_start_ms,
        end_ms=window_end_ms,
        cache_dir=cache_dir,
        log=log,
    )
    provider = indexed_zone_provider(archives, combine=combine)
    ladder = RESISTANCE_TFS[entry_tf]

    out: list[SetupResistance] = []
    for cand in candidates:
        measured = _measure_one(cand, ladder, provider, archives)
        if measured is None:
            continue
        mfe_r, by_tf, combined_r, combined_src = measured
        out.append(
            SetupResistance(
                symbol=market.symbol,
                entry_tf=entry_tf,
                segment=_segment_for(cand.trigger_time, is_boundary),
                mfe_r=mfe_r,
                by_tf=by_tf,
                combined_r=combined_r,
                combined_source=combined_src,
            )
        )
    return out


def _measure_one(
    cand: _Candidate,
    ladder: Sequence[str],
    provider: ZoneProvider,
    archives: dict[str, OrderBlockResult],
) -> tuple[float, dict[str, float], float | None, str | None] | None:
    """한 후보의 (무검열 MFE, 저항TF별 거리, 전TF 최근접). MFE를 못 재면 제외."""
    if cand.mfe_r is None:
        return None  # 무검열 팔에서도 1R을 못 재면(risk<=0) 제외.
    # 롱 온리 채택 기본값 — 숏 저항(지지) 대칭은 이 이슈 범위 밖.
    if cand.side is not PositionSide.LONG:
        return None
    mfe_r = cand.mfe_r
    entry_price = cand.entry_price
    risk = entry_price - cand.stop_price
    if risk <= 0:
        return None

    by_tf: dict[str, float] = {}
    best_r: float | None = None
    best_src: str | None = None
    for tf in ladder:
        if tf not in archives:
            continue  # 데이터 없는 칸은 조용히 건너뛴다.
        zones = provider(tf, cand.trigger_time, OrderBlockDirection.BEARISH)
        hit = nearest_resistance(zones, entry_price=entry_price, risk=risk, timeframe=tf)
        if hit is None:
            continue
        by_tf[tf] = hit.distance_r
        if best_r is None or hit.distance_r < best_r:
            best_r, best_src = hit.distance_r, tf
    return mfe_r, by_tf, best_r, best_src


# --------------------------------------------------------------------------- #
# 집계 — (진입TF × 저항TF × 구간 × 심볼) 분포 셀
# --------------------------------------------------------------------------- #


class DistRow(BaseModel):
    """한 (심볼, 진입TF, 저항TF, 구간, 병합) 셀의 거리 분포 요약."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    entry_tf: str
    resistance_tf: str
    """`15m`/`5m`/`1m`/`1h` 등 개별 TF, 또는 `combined`(전 TF 최근접)."""
    segment: str
    combine: bool
    n_setups: int
    """이 (심볼, 진입TF, 구간)의 체결 셋업 수(저항 유무 무관)."""
    n_with_resistance: int
    frac_no_resistance: float
    dist_median: float | None
    dist_q25: float | None
    dist_q75: float | None
    frac_below_near: float | None
    """저항 있는 셋업 중 거리 < 0.5R 비율(비용 잠식)."""
    frac_above_far: float | None
    """저항 있는 셋업 중 거리 > 3R 비율(사실상 홀딩)."""
    frac_within_mfe: float | None
    """저항 있는 셋업 중 거리 <= 무검열 MFE 비율(도달 가능)."""


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(pd.Series(values).quantile(q))


def _frac(count: int, total: int) -> float | None:
    return count / total if total else None


def aggregate(setups: Sequence[SetupResistance], *, combine: bool) -> list[DistRow]:
    """셋업 목록을 (심볼, 진입TF, 저항TF, 구간) 분포 셀로 접는다."""
    # (심볼, 진입TF, 구간) → 체결 셋업 수.
    n_by_cell: Counter[tuple[str, str, str]] = Counter()
    # (심볼, 진입TF, 저항TF, 구간) → [(거리, mfe)] 목록.
    dist_by_cell: dict[tuple[str, str, str, str], list[tuple[float, float]]] = defaultdict(list)
    for s in setups:
        cell = (s.symbol, s.entry_tf, s.segment)
        n_by_cell[cell] += 1
        for tf, dist in s.by_tf.items():
            dist_by_cell[(s.symbol, s.entry_tf, tf, s.segment)].append((dist, s.mfe_r))
        if s.combined_r is not None:
            dist_by_cell[(s.symbol, s.entry_tf, COMBINED_TF, s.segment)].append(
                (s.combined_r, s.mfe_r)
            )

    rows: list[DistRow] = []
    seen: set[tuple[str, str, str, str]] = set(dist_by_cell)
    # 저항이 아예 하나도 없던 셀도 표에 남긴다(frac_no_resistance=1.0).
    for symbol, entry_tf, segment in n_by_cell:
        for tf in (*RESISTANCE_TFS[entry_tf], COMBINED_TF):
            seen.add((symbol, entry_tf, tf, segment))
    for symbol, entry_tf, tf, segment in sorted(seen):
        n_setups = n_by_cell[(symbol, entry_tf, segment)]
        pairs = dist_by_cell.get((symbol, entry_tf, tf, segment), [])
        dists = [d for d, _ in pairs]
        n_res = len(dists)
        below = sum(1 for d in dists if d < NEAR_R)
        above = sum(1 for d in dists if d > FAR_R)
        within = sum(1 for d, mfe in pairs if d <= mfe)
        rows.append(
            DistRow(
                symbol=symbol,
                entry_tf=entry_tf,
                resistance_tf=tf,
                segment=segment,
                combine=combine,
                n_setups=n_setups,
                n_with_resistance=n_res,
                frac_no_resistance=_frac(n_setups - n_res, n_setups) or 0.0,
                dist_median=_quantile(dists, 0.5),
                dist_q25=_quantile(dists, 0.25),
                dist_q75=_quantile(dists, 0.75),
                frac_below_near=_frac(below, n_res),
                frac_above_far=_frac(above, n_res),
                frac_within_mfe=_frac(within, n_res),
            )
        )
    return rows


def source_breakdown(setups: Sequence[SetupResistance]) -> pd.DataFrame:
    """ "전 TF 최근접"의 출처 TF 분포 — 최근접이 거의 항상 최하단 TF에서 나오는지 확인.

    이슈의 핵심 경고("전 TF 최근접 = 최하단 TF 최근접")를 데이터로 보인다.
    """
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for s in setups:
        if s.combined_source is not None:
            counts[(s.entry_tf, s.segment)][s.combined_source] += 1
    records: list[dict[str, object]] = []
    for (entry_tf, segment), ctr in sorted(counts.items()):
        total = sum(ctr.values())
        rec: dict[str, object] = {"entry_tf": entry_tf, "segment": segment, "n": total}
        for tf in RESISTANCE_TFS[entry_tf]:
            rec[f"from_{tf}"] = _frac(ctr.get(tf, 0), total)
        records.append(rec)
    return pd.DataFrame(records)


# --------------------------------------------------------------------------- #
# 게이트 판정 + A/B/C 권고 (분포에서 도출)
# --------------------------------------------------------------------------- #


def _pooled_cell(
    frame: pd.DataFrame, entry_tf: str, resistance_tf: str, segment: str, combine: bool
) -> dict[str, float | None]:
    """한 (진입TF, 저항TF, 구간, 병합)을 6심볼 풀링해 분포 지표를 낸다.

    셀별 비율을 셋업 수로 가중 평균한다(심볼 편중을 한 심볼이 좌우하지 않게)."""
    sub = frame[
        (frame["entry_tf"] == entry_tf)
        & (frame["resistance_tf"] == resistance_tf)
        & (frame["segment"] == segment)
        & (frame["combine"] == combine)
    ]
    if sub.empty:
        return {"n_setups": 0, "n_with_resistance": 0}
    n_setups = int(sub["n_setups"].sum())
    n_res = int(sub["n_with_resistance"].sum())

    def wmean(col: str, weight: str) -> float | None:
        w = sub[weight].astype(float)
        v = sub[col].astype(float)
        mask = v.notna() & (w > 0)
        return float((v[mask] * w[mask]).sum() / w[mask].sum()) if w[mask].sum() > 0 else None

    return {
        "n_setups": float(n_setups),
        "n_with_resistance": float(n_res),
        "frac_no_resistance": _frac(n_setups - n_res, n_setups),
        "frac_below_near": wmean("frac_below_near", "n_with_resistance"),
        "frac_above_far": wmean("frac_above_far", "n_with_resistance"),
        "frac_within_mfe": wmean("frac_within_mfe", "n_with_resistance"),
        "dist_median": wmean("dist_median", "n_with_resistance"),
    }


def gate_verdict(
    frame: pd.DataFrame, entry_tf: str, resistance_tf: str, *, combine: bool = False
) -> tuple[str, str]:
    """공식 OOS 6심볼 풀에서 (a)/(b)/(c) 게이트를 읽는다. (판정, 근거문) 반환."""
    cell = _pooled_cell(frame, entry_tf, resistance_tf, SEGMENT_OOS, combine)
    n_setups = int(cell.get("n_setups") or 0)
    if n_setups == 0:
        return "판정 불가", "OOS 셋업 없음."
    no_res = cell.get("frac_no_resistance") or 0.0
    below = cell.get("frac_below_near")
    above = cell.get("frac_above_far")
    within = cell.get("frac_within_mfe")
    reasons: list[str] = []
    failed = False
    if no_res > MAJORITY:
        failed = True
        reasons.append(f"(c) 저항 없음 {no_res * 100:.0f}% 과반 → 폴백이 곧 규칙")
    if below is not None and below > MAJORITY:
        failed = True
        reasons.append(f"(a) 거리<0.5R {below * 100:.0f}% 다수 → 비용에 먹힘")
    if within is not None and (1 - within) > MAJORITY:
        failed = True
        reasons.append(f"(b) MFE 너머 {(1 - within) * 100:.0f}% 다수 → 도달 불가")
    median = cell.get("dist_median")
    detail = (
        f"저항없음 {no_res * 100:.0f}% · "
        f"거리<0.5R {'—' if below is None else format(below * 100, '.0f') + '%'} · "
        f"거리>3R {'—' if above is None else format(above * 100, '.0f') + '%'} · "
        f"MFE내 {'—' if within is None else format(within * 100, '.0f') + '%'} · "
        f"중앙값 {'—' if median is None else format(median, '.2f') + 'R'}"
    )
    if failed:
        return "탈락", "; ".join(reasons) + f" ({detail})"
    return "통과 후보", detail


def recommend_weight_rule(frame: pd.DataFrame, entry_tf: str) -> str:
    """분포에서 A/B/C 무게 규칙을 도출한다(감으로 고르지 않는다 — 이슈 완료 기준).

    * 하위TF 저항이 대부분 0.5R 미만이면 그 TF는 자동 탈락(A의 하한을 우리가 고를 필요
      없이 분포가 고른다) → 자기-TF만 남으면 **B**.
    * 자기-TF 저항이 대부분 MFE 너머면 **B는 죽는다**.
    * "전 TF 최근접"이 최하단 TF에서만 나오고 그게 0.5R 미만이면 순진한 규칙은 퇴화.
    """
    ladder = RESISTANCE_TFS[entry_tf]
    self_tf = entry_tf
    lines: list[str] = []
    # 하위TF들이 비용에 먹히는지.
    doomed_low: list[str] = []
    for tf in ladder:
        if tf == self_tf:
            continue
        cell = _pooled_cell(frame, entry_tf, tf, SEGMENT_OOS, False)
        below = cell.get("frac_below_near")
        if below is not None and below > MAJORITY:
            doomed_low.append(f"{tf}(거리<0.5R {below * 100:.0f}%)")
    self_cell = _pooled_cell(frame, entry_tf, self_tf, SEGMENT_OOS, False)
    self_within = self_cell.get("frac_within_mfe")
    self_no_res = self_cell.get("frac_no_resistance") or 0.0
    comb_cell = _pooled_cell(frame, entry_tf, COMBINED_TF, SEGMENT_OOS, False)
    comb_below = comb_cell.get("frac_below_near")

    if doomed_low:
        lines.append(
            f"하위TF 저항이 분포상 자동 탈락({', '.join(doomed_low)}) — A의 하한을 손으로 "
            "고를 필요 없이 데이터가 하위TF를 뺀다."
        )
    if comb_below is not None and comb_below > MAJORITY:
        lines.append(
            f"순진한 「전 TF 최근접」은 퇴화 — 최근접의 {comb_below * 100:.0f}%가 0.5R 미만"
            "(최하단 TF에서 나온 촘촘한 존)이라 비용에 먹힌다."
        )
    self_ok = self_within is not None and (1 - self_within) <= MAJORITY and self_no_res <= MAJORITY
    if self_ok and self_within is not None:
        lines.append(
            f"자기-TF({self_tf}) 저항은 살아 있음(MFE내 {self_within * 100:.0f}% · 저항없음 "
            f"{self_no_res * 100:.0f}%) → **B(진입 TF 이상)** 가 파라미터 0으로 성립하는 후보."
        )
    else:
        why = (
            "MFE 너머"
            if (self_within is not None and (1 - self_within) > MAJORITY)
            else "저항 부재"
        )
        lines.append(
            f"자기-TF({self_tf}) 저항도 {why}로 무너짐 → **B도 죽는다**. 남는 건 하위TF를 "
            "여는 C(임계·우선순위 2파라미터)뿐인데, 그건 이 이슈의 최대 장점(파라미터 최소)을 "
            "훼손하므로 채택 근거가 약하다."
        )
    return " ".join(lines)


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


_PCT_COLS = frozenset(
    {
        "frac_no_resistance",
        "frac_below_near",
        "frac_above_far",
        "frac_within_mfe",
    }
    | {f"from_{tf}" for tfs in RESISTANCE_TFS.values() for tf in tfs}
)


def _fmt(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.columns:
        if col in _PCT_COLS:
            out[col] = (out[col].astype(float) * 100).round(1)
        elif col.startswith("dist_"):
            out[col] = out[col].astype(float).round(2)
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(lambda s: str(s).split("/")[0])
    return out.astype(object).where(out.notna(), "—")


def _md_table(frame: pd.DataFrame) -> str:
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, rec in frame.iterrows():
        lines.append("| " + " | ".join(str(rec[h]) for h in headers) + " |")
    return "\n".join(lines)


def pooled_table(frame: pd.DataFrame, *, combine: bool = False) -> pd.DataFrame:
    """(진입TF × 저항TF × 구간) 6심볼 풀 요약표."""
    records: list[dict[str, object]] = []
    for entry_tf in sorted(set(frame["entry_tf"])):
        for tf in (*RESISTANCE_TFS[entry_tf], COMBINED_TF):
            for segment in SEGMENT_ORDER:
                cell = _pooled_cell(frame, entry_tf, tf, segment, combine)
                if int(cell.get("n_setups") or 0) == 0:
                    continue
                records.append(
                    {
                        "entry_tf": entry_tf,
                        "resistance_tf": tf,
                        "segment": segment,
                        "n_setups": int(cell["n_setups"] or 0),
                        "n_with_resistance": int(cell["n_with_resistance"] or 0),
                        "frac_no_resistance": cell.get("frac_no_resistance"),
                        "dist_median": cell.get("dist_median"),
                        "frac_below_near": cell.get("frac_below_near"),
                        "frac_above_far": cell.get("frac_above_far"),
                        "frac_within_mfe": cell.get("frac_within_mfe"),
                    }
                )
    return pd.DataFrame(records)


def write_summary(frame: pd.DataFrame, source: pd.DataFrame, path: Path) -> None:
    entry_tfs = sorted(set(frame["entry_tf"]))
    pooled = pooled_table(frame, combine=False)
    lines = [
        "# WAN-137 Phase 1: 저항 오더블록까지의 거리 분포 (진입TF × 저항TF)",
        "",
        "재현: `uv run python -m backtest.wan137_resistance_distance`",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · 6심볼 · 롱 온리 · 공식 렌즈 `baseline`"
        "(WAN-128) · 채택 기본값(존 지정가 + 오프셋 2bp) 고정. 셋업 단위(시퀀싱 이전) 측정, "
        "무검열 MFE(익절 끔, WAN-90 방식). 정본 병합 `combine=False`.",
        "",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 체결률·거리 분포는 **상한**이다.",
        "> ⚠️ 거리 = (약세 존 `bottom` − 진입가)/1R. 오프셋 2bp는 Phase 1에 안 얹었다(§③).",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124)은 불변 — 익절 구조는 위험의 모양만 바꾼다"
        "(WAN-90).",
        "> ⚠️ ⑤ 병합 축: 정본 `combine=False`. `combine=True`는 옵트인이나 1분봉 병합이 "
        "O(활성²)(WAN-126)라 기본 격자에서 뺐다 — 병합이 목표를 당기는 **방향**은 WAN-134가 "
        "이미 쟀다(병합 폭 ~1.8배).",
        "",
        "## 게이트 판정 — 공식 OOS 6심볼 풀 (`combine=False`)",
        "",
    ]
    for entry_tf in entry_tfs:
        lines.append(f"### 진입 {entry_tf}")
        for tf in (*RESISTANCE_TFS[entry_tf], COMBINED_TF):
            tag, detail = gate_verdict(frame, entry_tf, tf, combine=False)
            lines.append(f"- **저항 {tf}**: {tag} — {detail}")
        lines.append("")
        lines.append(f"**A/B/C 권고(분포 도출)**: {recommend_weight_rule(frame, entry_tf)}")
        lines.append("")
    lines += [
        "## 1. (진입TF × 저항TF × 구간) 분포 — 6심볼 풀, `combine=False`",
        "",
        "거리 중앙값·게이트 비율(저항 있는 셋업 기준). `combined` = 사다리 전 TF 최근접.",
        "",
        _md_table(_fmt(pooled)),
        "",
        "## 2. 「전 TF 최근접」 출처 TF 분포 (`combine=False`)",
        "",
        "최근접 저항이 어느 TF에서 나왔나 — 최하단 TF 편중이면 순진한 규칙이 퇴화한다.",
        "",
        _md_table(_fmt(source)),
        "",
        "## 3. 심볼별 원자료 (`combine=False`, OOS)",
        "",
        _md_table(
            _fmt(
                frame[(~frame["combine"]) & (frame["segment"] == SEGMENT_OOS)].drop(
                    columns=["combine", "dist_q25", "dist_q75"]
                )
            )
        ),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[DistRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[DistRow]:
    frame = pd.read_csv(path)
    return [DistRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def run_report(
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = ("1h",),
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    cache_dir: str | None = DEFAULT_CACHE_DIR,
    combines: Sequence[bool] = (False,),
    log: bool = True,
) -> tuple[list[DistRow], list[SetupResistance]]:
    """격자를 돈다: 심볼 × 진입TF × 병합. 셀 행 + (combine=False) 셋업 목록을 낸다.

    ⚠️ **병합 축(⑤)의 기본은 `combine=False`(정본)다.** `combine=True`는 옵트인 민감도로
    남기되 **기본 격자에서 뺀다** — WAN-126이 밝힌 대로 1분봉 아카이브를 셋업마다 병합하면
    O(활성²)이라 격자가 몇 시간이 된다(그쪽이 LTF를 `combine=False`로 둔 바로 그 이유).
    병합이 목표를 얼마나 당기는지의 **방향**은 WAN-134가 이미 쟀다(병합 존 폭 ~1.8배 →
    아랫변이 내려와 목표가 가까워짐). 그래서 1분봉 병합 비용을 치르지 않고도 결론이 선다.
    """
    import time

    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    rows: list[DistRow] = []
    setups_for_source: list[SetupResistance] = []
    for timeframe in timeframes:
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=False
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan137] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            for combine in combines:
                t0 = time.time()
                setups = measure_setups(
                    market,
                    combine=combine,
                    window_start_ms=start_ms,
                    window_end_ms=end_ms,
                    cache_dir=cache_dir,
                    log=log,
                )
                rows.extend(aggregate(setups, combine=combine))
                if not combine:
                    setups_for_source.extend(setups)
                if log:
                    print(
                        f"[wan137] {sym} {timeframe} combine={combine}: "
                        f"셋업 {len(setups)} 측정 ({time.time() - t0:.0f}s)",
                        flush=True,
                    )
    return rows, setups_for_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default="1h", help="콤마로 여러 개(예: 1h,15m)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--out-csv", type=str, default=str(REPORTS_DIR / "wan137_resistance_distance.csv")
    )
    parser.add_argument(
        "--source-csv", type=str, default=str(REPORTS_DIR / "wan137_source_breakdown.csv")
    )
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan137_resistance_distance_summary.md")
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 새 TF 행을 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument("--cache-dir", type=str, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--no-cache", action="store_true", help="LTF 아카이브 캐시를 쓰지 않는다")
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    source_csv = Path(args.source_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        source = pd.read_csv(source_csv) if source_csv.exists() else pd.DataFrame()
        print(f"[wan137] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        cache_dir = None if args.no_cache else args.cache_dir
        rows, setups = run_report(
            symbols, timeframes, start=args.start, end=args.end, cache_dir=cache_dir
        )
        source = source_breakdown(setups)
        if args.append and out_csv.exists():
            existing = rows_from_csv(out_csv)
            keep = [r for r in existing if r.entry_tf not in set(timeframes)]
            rows = keep + rows
            if source_csv.exists():
                old_src = pd.read_csv(source_csv)
                old_src = old_src[~old_src["entry_tf"].isin(timeframes)]
                source = pd.concat([old_src, source], ignore_index=True)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
        source.to_csv(source_csv, index=False)
    write_summary(rows_to_frame(rows), source, Path(args.out_md))
    print(f"[wan137] 저장: {out_csv}, {source_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
