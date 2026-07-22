"""손절폭 축 통합 측정 — 실현 손익비 · 비용 · 베팅 크기 · 가드 · 체결 보수화 · 문턱 민감도
(WAN-154 = WAN-153 흡수 · 사용자 질문 2026-07-21).

## 이 모듈이 재는 것

존폭 필터 판정(WAN-142 「표본 축소 아님」 → WAN-152 「기하 아님 = (a) 선별」)이 남긴
구멍들을 **한 실행**에서 잰다 — 따로 짜면 표들이 다른 실행에서 나와 나란히 놓을 수 없다:

* **§0** 세 번째 장벽 `zone_height`(1R = 존 높이) — 세 자(`zone`·`atr`·`zone_height`)
  전부에서 필터가 이기면 판정이 자에 의존하지 않는다.
* **§1′** 거래당 실현 손익비 — `total_return`은 복리 합산이라 「1R당 얼마 남나」에 답하지
  못한다. ⚠️ `harness.mean_r`은 청산 사유로 ±1.5/−1.0을 넣는 **승률의 대수적 재탕**이라
  쓰지 않는다(PM 정정). 실현 손익 ÷ 그 거래의 리스크 금액(비용 반영 후)을 잰다.
* **§2** 베팅 크기 오염 — 좁은 손절은 레버리지 1배 상한에 걸려 **실효 리스크가 1% 아래로
  깎인다**. 존폭 필터는 정의상 좁은 존만 고르므로 두 팔이 서로 다른 판돈으로 겨뤘을 수
  있다 — 상한 발동률·실효 리스크를 처음으로 실측한다.
* **§3** 손절폭 세 장치(가드 · 상한 · 고정 R+비용)를 한 표에서 — §3-A 손절폭 버킷 진단표
  (**가드를 끄고** 재야 가드가 자르는 구간이 보인다), §3-B 가드 5값(0% 포함) 손익 대조로
  「가드가 이득인가」를 **장벽별로** 판정.
* **§4** 체결 보수화(`pen_5bp` 옵트인) — WAN-152 판정 셀은 전부 `baseline` 위의 값이었다.
* **§5** 문턱 민감도(하위 1/4 · 1/3 · 1/2) — 1/3 하나만 통하면 IS에서 고른 자유 파라미터다.

## 파이프라인 재사용 (새 파이프라인 금지 — 이슈 사양)

셀 빌드·공통 풀·매칭 추첨·검정은 전부 `backtest.wan152_selection_vs_geometry`의 것이다.
이 모듈은 축(렌즈·가드·문턱)을 조합해 그 파이프라인을 **오케스트레이션**만 한다.
가드·문턱은 시퀀싱/부분집합만 바꾸므로 재시퀀싱으로 돌고, 렌즈(`pen_5bp`)는 체결 집합을
바꾸므로 셀 자체를 그 파라미터로 다시 빌드한다.

🚨 **옛 핀 미사용** — `LEGACY_COMBINE_OBS`·`LEGACY_BAND_BAR`·`pin_band_bar`·
`LEGACY_OB_PARAMS`를 쓰지 않는다(오늘의 채택 기본값 그대로). 회귀 테스트가 후보 집합으로
고정한다(WAN-152 패턴).

재현: `python -m backtest.wan154_stop_width_audit --timeframes 1h` →
`--timeframes 15m --append` (요약만 재생성: `--from-csv`).
"""

from __future__ import annotations

import argparse
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import (
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
)
from backtest.wan133_geometry_vs_selection import (
    ARM_DEFAULT,
    ARM_FILTER,
    FILTER_FRACTION,
    MIN_TRADES_FOR_PNL,
    REPORTS_DIR,
    STOP_GUARD_FRACTION,
    _bare,
    _write_csv,
)
from backtest.wan142_zone_width_filter_verdict import (
    ALPHA,
    ARM_MATCHED,
    MATCH_SEEDS,
    SEED_AGGREGATE,
)
from backtest.wan152_selection_vs_geometry import (
    ARMS,
    ATR_K,
    BARRIER_ATR,
    BARRIER_ZONE,
    BARRIER_ZONE_HEIGHT,
    BARRIERS,
    LENS_PRIMARY,
    GeoCell,
    MatchedTestRow,
    PnlRow,
    TradeRecord,
    _aggregate_records,
    _rows_to_frame,
    _val,
    build_cell,
    is_threshold,
    leave_one_out,
    matched_test_row,
    per_trade_records,
    pnl_rows_for_cell,
    symbol_mean,
)

# --------------------------------------------------------------------------- #
# 상수 — WAN-154의 축
# --------------------------------------------------------------------------- #

LENS_PEN = "pen_5bp"
"""§4 체결 보수화 렌즈(옵트인, WAN-128 이후 수동 확인용). 주문가 5bp 관통을 요구한다."""

ROUND_TRIP_COST = 0.0011
"""왕복 체결 비용 근사(슬리피지 5bp + 테이커 4bp + 메이커 2bp, `common.costs`). §3-B 가드
후보값의 기준이다(WAN-79가 0.3% ≈ 3배 여유를 고른 그 산수)."""

GUARD_VALUES: tuple[float, ...] = (
    0.0,
    2 * ROUND_TRIP_COST,  # 0.22%
    STOP_GUARD_FRACTION,  # 0.30% — 현행 기본값(WAN-79)
    3 * ROUND_TRIP_COST,  # 0.33%
    5 * ROUND_TRIP_COST,  # 0.55%
)
"""§3-B 가드 축. **0%(끔)가 이득 판정의 분모다** — 민감도만으로는 이득/손해를 못 잰다."""

THRESHOLD_FRACTIONS: tuple[float, ...] = (0.25, FILTER_FRACTION, 0.5)
"""§5 문턱 축(존폭 하위 분위). 1/3이 WAN-133/142/152의 유일한 문턱이었다."""

STOP_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.002),
    (0.002, 0.003),
    (0.003, 0.005),
    (0.005, 0.01),
    (0.01, 0.02),
    (0.02, float("inf")),
)
"""§3-A 손절폭 구간(분수). 처음 두 구간이 현행 가드(0.3%)가 자르는 자리다."""

EFFECTIVE_RISK_EPS = 0.0005
"""§2 판정에서 「실질적으로 같다」로 읽는 실효 리스크 차이(0.05%p — 만기 리스크 1%의 5%)."""


def bucket_label(lo: float, hi: float) -> str:
    if hi == float("inf"):
        return f">{lo * 100:g}%"
    return f"{lo * 100:g}~{hi * 100:g}%"


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class BucketRow(BaseModel):
    """§3-A 손절폭 버킷 진단 행 — (TF, 구간, 장벽, 팔, 버킷), 심볼 풀링.

    ⚠️ **가드를 끄고(0%) 시퀀싱한 값이다** — 가드를 켜면 0.3% 미만 버킷에 거래가 아예
    없어서 「가드가 자르는 구간이 어떤 구간인가」를 볼 수 없다. 매칭 팔은 시드 20개 풀링.
    """

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    barrier: str
    arm: str
    bucket: str
    n_trades: int
    survival_rate: float | None
    """1 − 손절 비율(익절·데이터끝 포함 생존)."""
    win_rate: float | None
    mean_net_r: float | None
    cost_r_median: float | None
    breakeven_win_rate: float | None
    win_rate_margin: float | None
    cap_hit_rate: float | None
    effective_risk_mean: float | None


class ThresholdStabilityRow(BaseModel):
    """§5 문턱 안정성 행 — IS에서 잡은 절대 문턱값이 OOS에서도 같은 자리인가.

    `oos_coverage`가 `threshold_fraction`과 비슷하면 분포가 구간을 넘어 안정적이라는 뜻이고,
    크게 다르면 IS 문턱이 OOS에서 다른 분위를 자르고 있다(구간 이동)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    threshold_fraction: float
    is_threshold_value: float | None
    """IS 공통 풀 존폭(zone_width_atr)의 하위 `threshold_fraction` 분위값."""
    oos_coverage: float | None
    """OOS 후보 중 그 IS 문턱값 이하 비율 — 안정적이면 ≈ `threshold_fraction`."""


# --------------------------------------------------------------------------- #
# 실행 결과
# --------------------------------------------------------------------------- #


@dataclass
class AuditResult:
    pnl_rows: list[PnlRow] = field(default_factory=list)
    """§0·§1′·§2 — baseline 렌즈 · 현행 가드 · 문턱 1/3 · 장벽 3종."""
    pen_rows: list[PnlRow] = field(default_factory=list)
    """§4 — `pen_5bp` 렌즈로 **다시 빌드한 셀**의 같은 격자."""
    guard_rows: list[PnlRow] = field(default_factory=list)
    """§3-B — 가드 5값 격자(baseline 렌즈 · 문턱 1/3)."""
    threshold_rows: list[PnlRow] = field(default_factory=list)
    """§5 — 문턱 3값 격자(baseline 렌즈 · 현행 가드)."""
    bucket_rows: list[BucketRow] = field(default_factory=list)
    """§3-A — 손절폭 버킷 진단(가드 0%)."""
    stability_rows: list[ThresholdStabilityRow] = field(default_factory=list)
    pool_notes: list[str] = field(default_factory=list)
    pen_pool_notes: list[str] = field(default_factory=list)

    def matched_tests(self, rows: Sequence[PnlRow]) -> list[MatchedTestRow]:
        timeframes = tuple(dict.fromkeys(r.timeframe for r in rows))
        return [
            matched_test_row(rows, barrier=b, timeframe=tf, segment=seg)
            for b in BARRIERS
            for tf in timeframes
            for seg in (SEGMENT_IS, SEGMENT_OOS)
        ]


# --------------------------------------------------------------------------- #
# 셀 하나의 §3-A 버킷 레코드 · §5 안정성
# --------------------------------------------------------------------------- #


def _segment_indices(cell: GeoCell, segment: str) -> list[int]:
    zone_cands = cell.by_barrier[BARRIER_ZONE]
    return [
        i
        for i, cand in enumerate(zone_cands)
        if (cand.trigger_time < cell.is_boundary) == (segment == SEGMENT_IS)
    ]


def _filter_indices(cell: GeoCell, idx_seg: list[int], fraction: float) -> list[int]:
    threshold = is_threshold(cell, fraction=fraction)
    if threshold is None:
        return []
    return [i for i in idx_seg if (cell.zwa[i] or 0.0) <= threshold]


def bucket_records_for_cell(
    cell: GeoCell, market: MarketData
) -> dict[tuple[str, str, str], list[TradeRecord]]:
    """(구간, 장벽, 팔) → 거래 레코드(**가드 0%**). 심볼 풀링은 호출부가 한다.

    매칭 팔은 시드 20개를 **전부 풀링**한다 — 버킷을 시드별로 쪼개면 표본이 산산조각 난다.
    추첨은 `pnl_rows_for_cell`과 같은 규칙(같은 시드·같은 인덱스)이다.
    """
    out: dict[tuple[str, str, str], list[TradeRecord]] = {}
    if not cell.zwa:
        return out
    barriers = [b for b in BARRIERS if b in cell.by_barrier]
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        idx_seg = _segment_indices(cell, segment)
        idx_filter = _filter_indices(cell, idx_seg, FILTER_FRACTION)
        k = min(len(idx_filter), len(idx_seg))
        drawn = [
            i for seed in MATCH_SEEDS for i in (random.Random(seed).sample(idx_seg, k) if k else [])
        ]
        for barrier in barriers:
            cands = cell.by_barrier[barrier]
            for arm, idx in (
                (ARM_DEFAULT, idx_seg),
                (ARM_FILTER, idx_filter),
                (ARM_MATCHED, drawn),
            ):
                records = per_trade_records(
                    [cands[i] for i in idx], market, cell.timeframe, guard=0.0
                )
                out.setdefault((segment, barrier, arm), []).extend(records)
    return out


def bucket_rows_from_records(
    timeframe: str, pooled: dict[tuple[str, str, str], list[TradeRecord]]
) -> list[BucketRow]:
    rows: list[BucketRow] = []
    for (segment, barrier, arm), records in sorted(pooled.items()):
        for lo, hi in STOP_BUCKETS:
            sub = [r for r in records if lo <= r.stop_frac < hi]
            agg = _aggregate_records(sub)
            rows.append(
                BucketRow(
                    timeframe=timeframe,
                    segment=segment,
                    barrier=barrier,
                    arm=arm,
                    bucket=bucket_label(lo, hi),
                    n_trades=len(sub),
                    survival_rate=agg["survival_rate"],
                    win_rate=agg["win_rate"],
                    mean_net_r=agg["mean_net_r"],
                    cost_r_median=agg["cost_r_median"],
                    breakeven_win_rate=agg["breakeven_win_rate"],
                    win_rate_margin=agg["win_rate_margin"],
                    cap_hit_rate=agg["cap_hit_rate"],
                    effective_risk_mean=agg["effective_risk_mean"],
                )
            )
    return rows


def stability_rows_for_cell(cell: GeoCell) -> list[ThresholdStabilityRow]:
    rows: list[ThresholdStabilityRow] = []
    oos_vals = [
        z
        for z, cand in zip(cell.zwa, cell.by_barrier.get(BARRIER_ZONE, []), strict=True)
        if z is not None and cand.trigger_time >= cell.is_boundary
    ]
    for fraction in THRESHOLD_FRACTIONS:
        thr = is_threshold(cell, fraction=fraction)
        coverage = (
            sum(1 for z in oos_vals if z <= thr) / len(oos_vals)
            if thr is not None and oos_vals
            else None
        )
        rows.append(
            ThresholdStabilityRow(
                symbol=cell.symbol,
                timeframe=cell.timeframe,
                threshold_fraction=fraction,
                is_threshold_value=thr,
                oos_coverage=coverage,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def run_audit(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> AuditResult:
    """6심볼 × TF × 장벽 3종 × (렌즈 2 · 가드 5 · 문턱 3) — 셀은 렌즈당 한 번만 빌드한다."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    result = AuditResult()
    # 🚨 이중 필터 방지(WAN-159): 존폭 필터를 후보 리스트로 직접 만드므로 엔진의 채택
    # 기본값(1.28)을 꺼야 대조군·매칭이 오염되지 않는다(WAN-152 확장 모듈).
    base_params = harness.build_params(
        fill=harness.BASELINE_FILL, max_zone_width_atr=harness.LEGACY_MAX_ZONE_WIDTH_ATR
    )
    pen_params = harness.build_params(
        fill=harness.fill_preset(LENS_PEN), max_zone_width_atr=harness.LEGACY_MAX_ZONE_WIDTH_ATR
    )
    for timeframe in timeframes:
        tf_bucket_pool: dict[tuple[str, str, str], list[TradeRecord]] = {}
        for symbol in symbols:
            norm = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                norm, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, db_path=db_path
            )
            # --- baseline 렌즈 셀(§0·§1′·§2·§3·§5 전부 이 셀 위) ---
            cell = build_cell(market, params=base_params)
            raw = " · ".join(f"`{b}` {cell.n_raw.get(b, 0)}" for b in BARRIERS)
            note = f"{_bare(norm)} {timeframe}: 후보 {raw} → 공통 풀 {cell.n_pool}"
            result.pool_notes.append(note)
            print(f"[wan154] {note}", flush=True)

            result.pnl_rows.extend(pnl_rows_for_cell(cell, market))
            for guard in GUARD_VALUES:
                result.guard_rows.extend(pnl_rows_for_cell(cell, market, guard=guard))
            for fraction in THRESHOLD_FRACTIONS:
                result.threshold_rows.extend(
                    pnl_rows_for_cell(cell, market, threshold_fraction=fraction)
                )
            for key, records in bucket_records_for_cell(cell, market).items():
                tf_bucket_pool.setdefault(key, []).extend(records)
            result.stability_rows.extend(stability_rows_for_cell(cell))

            # --- pen_5bp 렌즈 셀(§4) — 체결 집합이 달라지므로 다시 빌드한다 ---
            pen_cell = build_cell(market, params=pen_params)
            pen_raw = " · ".join(f"`{b}` {pen_cell.n_raw.get(b, 0)}" for b in BARRIERS)
            pen_note = (
                f"{_bare(norm)} {timeframe} [{LENS_PEN}]: 후보 {pen_raw} → 공통 풀 "
                f"{pen_cell.n_pool}"
            )
            result.pen_pool_notes.append(pen_note)
            print(f"[wan154] {pen_note}", flush=True)
            result.pen_rows.extend(pnl_rows_for_cell(pen_cell, market, lens=LENS_PEN))
        result.bucket_rows.extend(bucket_rows_from_records(timeframe, tf_bucket_pool))
    return result


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


class RobustnessKind(StrEnum):
    """§0 판정 — 존폭 필터 우위가 세 자(장벽) 전부에서 서는가."""

    ALL = "all"  # 세 자 전부 유지
    PARTIAL = "partial"  # 일부만
    COLLAPSED = "collapsed"  # 통제 장벽(atr·zone_height)에서 전부 무너짐
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class Judgement:
    kind: str
    text: str

    def __str__(self) -> str:
        return self.text


def _find_test(
    tests: Sequence[MatchedTestRow], *, barrier: str, timeframe: str, segment: str = SEGMENT_OOS
) -> MatchedTestRow | None:
    return next(
        (
            t
            for t in tests
            if t.barrier == barrier and t.timeframe == timeframe and t.segment == segment
        ),
        None,
    )


def robustness_verdict(tests: Sequence[MatchedTestRow], *, timeframe: str) -> Judgement:
    """§0 — 세 장벽 OOS 매칭 검정에서 필터 우위(p ≤ α)가 몇 자에서 서는가."""
    per_barrier: dict[str, MatchedTestRow | None] = {
        b: _find_test(tests, barrier=b, timeframe=timeframe) for b in BARRIERS
    }
    missing = [b for b, t in per_barrier.items() if t is None or t.p_return is None]
    if missing:
        return Judgement(
            RobustnessKind.INDETERMINATE,
            f"**{timeframe}**: ⚠️ 판정 불가 — {', '.join(f'`{b}`' for b in missing)} 장벽의 "
            "유효 표본 셀이 없다.",
        )
    sig = [b for b, t in per_barrier.items() if t is not None and (t.p_return or 1.0) <= ALPHA]
    detail = " · ".join(
        f"`{b}` p={t.p_return:.3f}"
        for b, t in per_barrier.items()
        if t is not None and t.p_return is not None
    )
    controls_sig = [b for b in sig if b != BARRIER_ZONE]
    if len(sig) == len(BARRIERS):
        return Judgement(
            RobustnessKind.ALL,
            f"**{timeframe}**: **유지(세 자 전부)** — `zone`(진입가에 묶인 자) · "
            f"`atr`(존과 끊긴 자) · `zone_height`(존 크기 반영·진입가 무관) 세 장벽 모두에서 "
            f"필터가 매칭 대조군을 유의하게 이긴다({detail}). 판정이 자에 의존하지 않는다.",
        )
    if controls_sig:
        return Judgement(
            RobustnessKind.PARTIAL,
            f"**{timeframe}**: **일부만 유지** — 유의 장벽 "
            f"{', '.join(f'`{b}`' for b in sig)} / 비유의 "
            f"{', '.join(f'`{b}`' for b in BARRIERS if b not in sig)}({detail}).",
        )
    return Judgement(
        RobustnessKind.COLLAPSED,
        f"**{timeframe}**: **무너짐** — 통제 장벽(`{BARRIER_ATR}`·`{BARRIER_ZONE_HEIGHT}`) "
        f"어느 자에서도 필터 우위가 서지 않는다({detail}). 우위는 채택 장벽의 기하일 가능성이 "
        "크다.",
    )


def bet_size_verdict(
    rows: Sequence[PnlRow], *, barrier: str, timeframe: str, segment: str = SEGMENT_OOS
) -> Judgement:
    """§2 — (a) 필터가 덜 걸고도 이겼다(과소평가) / (b) 더 걸어서 이겼다 / (c) 같다."""
    filt = symbol_mean(rows, barrier=barrier, timeframe=timeframe, segment=segment, arm=ARM_FILTER)
    match = symbol_mean(
        rows, barrier=barrier, timeframe=timeframe, segment=segment, arm=ARM_MATCHED
    )
    f_risk, m_risk = filt.get("effective_risk_mean"), match.get("effective_risk_mean")
    f_ret, m_ret = filt.get("total_return"), match.get("total_return")
    if f_risk is None or m_risk is None or f_ret is None or m_ret is None:
        return Judgement("indeterminate", f"`{barrier}` {timeframe}: ⚠️ 판정 불가(표본 부족).")
    f_cap, m_cap = filt.get("cap_hit_rate"), match.get("cap_hit_rate")
    cap_txt = (
        f"상한 발동률 {f_cap * 100:.1f}% vs {m_cap * 100:.1f}% · "
        if f_cap is not None and m_cap is not None
        else ""
    )
    diff = f_risk - m_risk
    won = f_ret > m_ret
    base = (
        f"`{barrier}` {timeframe} {segment}: {cap_txt}실효 리스크 {f_risk * 100:.3f}% vs "
        f"{m_risk * 100:.3f}%(Δ{diff * 100:+.3f}%p) · 수익 {f_ret * 100:+.2f}% vs "
        f"{m_ret * 100:+.2f}%"
    )
    if abs(diff) <= EFFECTIVE_RISK_EPS:
        return Judgement("same", f"{base} → **(c) 실질적으로 같은 판돈**이다.")
    if diff < 0 and won:
        return Judgement(
            "underrated",
            f"{base} → **(a) 필터가 덜 걸고도 이겼다** — `total_return` 우위는 베팅 크기의 "
            "산물이 아니고 오히려 **과소평가**돼 있다.",
        )
    if diff > 0 and won:
        return Judgement(
            "inflated",
            f"{base} → **(b) 필터가 더 걸어서 이겼다** — 우위의 일부가 판돈 차이일 수 있다.",
        )
    return Judgement("same", f"{base} → 필터가 이기지 못한 셀이라 판돈 판정의 대상이 아니다.")


def divergent_cells(rows: Sequence[PnlRow]) -> list[str]:
    """§2 — `total_return` 우위와 `mean_net_r` 우위의 **부호가 갈리는** (장벽, TF, 구간) 목록."""
    out: list[str] = []
    timeframes = tuple(dict.fromkeys(r.timeframe for r in rows))
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                filt = symbol_mean(
                    rows, barrier=barrier, timeframe=timeframe, segment=segment, arm=ARM_FILTER
                )
                match = symbol_mean(
                    rows, barrier=barrier, timeframe=timeframe, segment=segment, arm=ARM_MATCHED
                )
                f_ret, m_ret = filt.get("total_return"), match.get("total_return")
                f_r, m_r = filt.get("mean_net_r"), match.get("mean_net_r")
                if None in (f_ret, m_ret, f_r, m_r):
                    continue
                assert f_ret is not None and m_ret is not None
                assert f_r is not None and m_r is not None
                if ((f_ret - m_ret) > 0) != ((f_r - m_r) > 0):
                    out.append(
                        f"`{barrier}` {timeframe} {segment}: total_return Δ"
                        f"{(f_ret - m_ret) * 100:+.2f}%p vs mean_net_r Δ{f_r - m_r:+.3f}R"
                    )
    return out


def _guard_cell(
    rows: Sequence[PnlRow],
    *,
    barrier: str,
    timeframe: str,
    guard: float,
    arm: str,
    segment: str = SEGMENT_OOS,
) -> dict[str, float | None]:
    sub = [r for r in rows if abs(r.guard - guard) < 1e-12]
    return symbol_mean(sub, barrier=barrier, timeframe=timeframe, segment=segment, arm=arm)


def guard_verdict(rows: Sequence[PnlRow], *, barrier: str) -> Judgement:
    """§3-B — 장벽별 「가드(0.3%)가 이득인가」. 분모는 가드 0%(끔) 팔이다.

    판정은 `default` 팔(채택 엔진 그 자체) OOS로 낸다 — 가드는 필터 이전의 엔진 장치다.
    `total_return`만으로 내지 않는다: 수익/MDD(위험조정) · 손익분기 승률 여유를 함께 본다
    (가드는 수익 장치가 아니라 신뢰성 장치라는 WAN-76 취지).
    """
    timeframes = tuple(dict.fromkeys(r.timeframe for r in rows))
    per_tf: list[str] = []
    ret_deltas: list[float] = []
    ra_deltas: list[float] = []
    margin_deltas: list[float] = []
    for timeframe in timeframes:
        on = _guard_cell(
            rows, barrier=barrier, timeframe=timeframe, guard=STOP_GUARD_FRACTION, arm=ARM_DEFAULT
        )
        off = _guard_cell(rows, barrier=barrier, timeframe=timeframe, guard=0.0, arm=ARM_DEFAULT)
        ret_on, ret_off = on.get("total_return"), off.get("total_return")
        mdd_on, mdd_off = on.get("max_drawdown"), off.get("max_drawdown")
        mg_on, mg_off = on.get("win_rate_margin"), off.get("win_rate_margin")
        if None in (ret_on, ret_off, mdd_on, mdd_off):
            per_tf.append(f"{timeframe}: 표본 부족")
            continue
        assert ret_on is not None and ret_off is not None
        assert mdd_on is not None and mdd_off is not None
        ra_on = ret_on / mdd_on if mdd_on > 0 else 0.0
        ra_off = ret_off / mdd_off if mdd_off > 0 else 0.0
        ret_deltas.append(ret_on - ret_off)
        ra_deltas.append(ra_on - ra_off)
        if mg_on is not None and mg_off is not None:
            margin_deltas.append(mg_on - mg_off)
        per_tf.append(
            f"{timeframe}: 수익 {ret_off * 100:+.2f}%(끔) → {ret_on * 100:+.2f}%(0.3%) "
            f"Δ{(ret_on - ret_off) * 100:+.2f}%p · 수익/MDD {ra_off:.2f} → {ra_on:.2f} · "
            f"승률 여유 Δ{(mg_on - mg_off) * 100:+.2f}%p"
            if mg_on is not None and mg_off is not None
            else f"{timeframe}: 수익 Δ{(ret_on - ret_off) * 100:+.2f}%p"
        )
    detail = " / ".join(per_tf)
    if not ret_deltas:
        return Judgement("indeterminate", f"`{barrier}`: ⚠️ 판정 불가 — {detail}")
    # ±0.1%p 미만은 「효과 없음」으로 읽는다 — 0을 어느 한쪽 부호로 세면 「무영향 + 이득」이
    # 「TF에 갈린다」로 둔갑한다(부호 함정, WAN-115/120이 겪은 부류).
    eps = 0.001
    pos = [d for d in ret_deltas if d > eps]
    neg = [d for d in ret_deltas if d < -eps]
    if not pos and not neg:
        return Judgement(
            "no_effect",
            f"`{barrier}`: **(c) 중립(무영향)** — 가드를 끄나 켜나 결과가 사실상 같다"
            f"({detail}). 이 장벽에서는 가드(0.3%)에 걸리는 거래가 거의 없다는 뜻이다.",
        )
    ra_ok = all(d > -1e-9 for d in ra_deltas) if ra_deltas else False
    if not neg and ra_ok:
        note = "(나머지 TF는 무영향)" if len(pos) < len(ret_deltas) else ""
        return Judgement(
            "benefit",
            f"`{barrier}`: **(a) 가드가 이득{note}** — 수익이 내리는 TF가 없고 위험조정도 "
            f"내려가지 않는다({detail}).",
        )
    if not pos and all(d < 1e-9 for d in ra_deltas):
        return Judgement(
            "harm",
            f"`{barrier}`: **(b) 가드가 손해** — 수익이 오르는 TF가 없고 위험조정도 오르지 "
            f"않는다({detail}).",
        )
    if pos and neg:
        return Judgement(
            "neutral",
            f"`{barrier}`: **(c) 중립(TF에 갈린다)** — 수익 방향이 TF마다 다르다({detail}). "
            "「TF마다 다른 가드」는 IS에서 고르는 새 자유 파라미터다(WAN-108/143이 경계한 자리).",
        )
    return Judgement(
        "neutral",
        f"`{barrier}`: **(c) 중립** — 수익과 위험조정(또는 신뢰성 지표)의 방향이 갈린다"
        f"({detail}). 가드는 수익 장치가 아니라 신뢰성 장치라는 WAN-76 취지 그대로다.",
    )


def pen_verdict(
    base_tests: Sequence[MatchedTestRow], pen_tests: Sequence[MatchedTestRow]
) -> Judgement:
    """§4 — baseline에서 유의했던 OOS 셀이 `pen_5bp`에서도 유의한가(같이 깎이는가)."""
    timeframes = tuple(dict.fromkeys(t.timeframe for t in base_tests))
    kept: list[str] = []
    lost: list[str] = []
    for barrier in BARRIERS:
        for timeframe in timeframes:
            b = _find_test(base_tests, barrier=barrier, timeframe=timeframe)
            p = _find_test(pen_tests, barrier=barrier, timeframe=timeframe)
            if b is None or b.p_return is None or b.p_return > ALPHA:
                continue
            label = f"`{barrier}` {timeframe}"
            if p is not None and p.p_return is not None and p.p_return <= ALPHA:
                kept.append(f"{label}(p={p.p_return:.3f})")
            else:
                lost.append(
                    f"{label}(p={'—' if p is None or p.p_return is None else f'{p.p_return:.3f}'})"
                )
    if not kept and not lost:
        return Judgement(
            "indeterminate", "⚠️ baseline에서 유의한 OOS 셀이 없어 §4 판정 대상이 없다."
        )
    if not lost:
        return Judgement(
            "kept",
            f"**관통을 요구해도 필터 우위가 전부 남는다** — baseline 유의 셀 {len(kept)}개가 "
            f"`pen_5bp`에서도 전부 유의({', '.join(kept)}). 두 팔이 **같이 깎인다**는 뜻이라 "
            "우위가 「스치듯 닿은 체결」에 실려 있지 않다(WAN-88의 `baseline` 유의 4셀이 "
            "`pen_5bp` 한 번에 전멸한 것과 성질이 다르다 — WAN-142의 `zone` 장벽 확인을 "
            "통제 장벽까지 확장).",
        )
    return Judgement(
        "lost",
        f"🚨 **관통을 요구하면 일부 우위가 사라진다** — 유지 {len(kept)}개"
        f"({', '.join(kept) or '없음'}) · 소멸 {len(lost)}개({', '.join(lost)}). 소멸한 셀의 "
        "우위는 실거래에서 가장 안 될 「스치듯 닿은 체결」에 실려 있었다는 뜻이다.",
    )


def threshold_verdict(threshold_rows: Sequence[PnlRow]) -> Judgement:
    """§5 — 세 문턱 전부에서 필터가 매칭을 이기는가(판정 셀 = `atr` 장벽 OOS)."""
    timeframes = tuple(dict.fromkeys(r.timeframe for r in threshold_rows))
    held: list[str] = []
    failed: list[str] = []
    for fraction in THRESHOLD_FRACTIONS:
        sub = [r for r in threshold_rows if abs(r.threshold_fraction - fraction) < 1e-12]
        for timeframe in timeframes:
            t = matched_test_row(sub, barrier=BARRIER_ATR, timeframe=timeframe, segment=SEGMENT_OOS)
            p_txt = "—" if t.p_return is None else f"{t.p_return:.3f}"
            label = f"하위 {fraction:.2g} {timeframe}(p={p_txt})"
            if t.p_return is not None and t.p_return <= ALPHA:
                held.append(label)
            else:
                failed.append(label)
    if not failed:
        return Judgement(
            "robust",
            f"**세 문턱 전부 유의** — {', '.join(held)}. 1/3은 IS에서 고른 자유 파라미터가 "
            "아니라 문턱 구간 전반에서 서는 성질이다.",
        )
    if not held:
        return Judgement("fragile", f"🚨 **어느 문턱에서도 서지 않는다** — {', '.join(failed)}.")
    return Judgement(
        "partial",
        f"⚠️ **문턱에 민감하다** — 유의 {', '.join(held)} / 비유의 {', '.join(failed)}. "
        "특정 문턱에서만 통하면 그 값은 IS에서 고른 자유 파라미터로 읽어야 한다.",
    )


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _pct(value: float | None, *, signed: bool = False, digits: int = 2) -> str:
    v = _val(value)
    if v is None:
        return "—"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


def _num(value: float | None, *, digits: int = 3, signed: bool = True) -> str:
    v = _val(value)
    if v is None:
        return "—"
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


def build_summary_markdown(result: AuditResult, *, timeframes: Sequence[str]) -> str:
    tests = result.matched_tests(result.pnl_rows)
    pen_tests = result.matched_tests(result.pen_rows)
    lines: list[str] = []
    lines.append("# WAN-154 손절폭 축 통합 측정 — 실현 손익비·비용·베팅 크기·가드·체결·문턱\n")
    symbols = sorted({_bare(r.symbol) for r in result.pnl_rows}) or ["—"]
    lines.append(
        f"{len(symbols)}심볼({'/'.join(symbols)}) × {'·'.join(timeframes)}, 못 박은 창 "
        f"**{DEFAULT_START} ~ {DEFAULT_END}**, **오늘의 채택 기본값**(`ConfluenceParams()` · "
        "`OrderBlockParams()` — 오프셋 2bp(WAN-112) · `intrabar_live` 밴드(WAN-132) · "
        "`unconditional` 게이트(WAN-123) · 고정 1.5R · 롱 온리 · `combine_obs=False`"
        "(WAN-149)). 공식 렌즈 **`baseline` 단독**(WAN-128), `pen_5bp`는 §4의 옵트인 "
        "민감도다.\n"
    )
    lines.append(
        f"장벽 3종: `{BARRIER_ZONE}`(채택 기본값 — 1R이 진입가~존 아랫 경계라 **존폭과 "
        f"진입가에 묶인다**) · `{BARRIER_ATR}`(1R = {ATR_K:g}·ATR — **존과 완전히 끊긴 자**) · "
        f"`{BARRIER_ZONE_HEIGHT}`(1R = 존 높이 — **존 크기 반영·진입가 무관**, WAN-154 §0). "
        "📌 **장벽마다 1R 정의가 다르다** — 모든 R 정규화 열은 **그 거래 자신의 1R**(그 장벽의 "
        "손절 거리 × 수량 = 리스크 금액) 기준이라, 장벽 간 비교는 「같은 자 안의 팔 대조」로만 "
        "성립한다.\n"
    )
    lines.append(
        "📌 **핀을 하나도 쓰지 않는다** — `LEGACY_COMBINE_OBS`·`LEGACY_BAND_BAR`·"
        "`pin_band_bar`·`LEGACY_OB_PARAMS` 미사용(오늘의 엔진 그대로, WAN-152 패턴 — 회귀 "
        "테스트가 후보 집합으로 고정). ⚠️ **WAN-142/152 표와 셀을 직접 비교하지 말 것** — "
        "이 표는 **세 장벽 공통 풀** 위다(§0). ⚠️ `harness.mean_r`은 쓰지 않았다 — 그 값은 "
        "청산 사유로 ±1.5/−1.0을 넣는 승률의 대수적 재탕이다(WAN-154 PM 정정).\n"
    )
    lines.append(
        "재현: `python -m backtest.wan154_stop_width_audit --timeframes 1h` → "
        "`--timeframes 15m --append`.\n"
    )

    lines.append("## 판정 모음\n")
    lines.append("**§0 세 자 강건성(OOS):**\n")
    for timeframe in timeframes:
        lines.append(f"* {robustness_verdict(tests, timeframe=timeframe).text}")
    lines.append("")
    lines.append("**§2 베팅 크기(OOS):**\n")
    for barrier in BARRIERS:
        for timeframe in timeframes:
            lines.append(
                f"* {bet_size_verdict(result.pnl_rows, barrier=barrier, timeframe=timeframe).text}"
            )
    lines.append("")
    lines.append("**§3-B 가드(장벽별):**\n")
    for barrier in BARRIERS:
        lines.append(f"* {guard_verdict(result.guard_rows, barrier=barrier).text}")
    lines.append("")
    lines.append(f"**§4 체결 보수화:** {pen_verdict(tests, pen_tests).text}\n")
    lines.append(f"**§5 문턱 민감도:** {threshold_verdict(result.threshold_rows).text}\n")

    lines.append(_pool_section(result))
    lines.append(_pnl_section(result, tests, timeframes))
    lines.append(_bet_size_section(result, timeframes))
    lines.append(_bucket_section(result, timeframes))
    lines.append(_guard_section(result, timeframes))
    lines.append(_pen_section(result, tests, pen_tests, timeframes))
    lines.append(_threshold_section(result, timeframes))
    lines.append("## 결론\n")
    lines.append(_conclusion(result, tests, pen_tests, timeframes))
    return "\n".join(lines)


def _pool_section(result: AuditResult) -> str:
    lines = ["## §0 공통 풀 — 세 장벽이 같은 셋업을 본다\n"]
    lines.append(
        "`atr` 오버라이드는 ATR을 못 찾는 셋업(워밍업·갭)에서 None을 돌려 미체결로 남기고, "
        "`zone_height`는 항상 유효하다(존 높이 > 0). `(탭 시각, 체결 시각, 방향)` 키 "
        "교집합 + 존폭 유효 조건이 공통 풀이다 — **축소는 사실상 전부 `atr` 워밍업의 몫**"
        "이라 WAN-152의 2장벽 공통 풀과 같은 집합이다(회귀 테스트가 고정).\n"
    )
    for note in result.pool_notes:
        lines.append(f"* {note}")
    lines.append("")
    if result.pen_pool_notes:
        lines.append(f"`{LENS_PEN}` 렌즈 셀(§4 — 체결 집합이 달라 풀도 다르다):\n")
        for note in result.pen_pool_notes:
            lines.append(f"* {note}")
        lines.append("")
    return "\n".join(lines)


def _arm_table(
    rows: Sequence[PnlRow], timeframes: Sequence[str], *, title: str, note: str = ""
) -> str:
    lines = [title + "\n"]
    if note:
        lines.append(note + "\n")
    lines.append(
        "| 장벽 | TF | 구간 | 팔 | 거래 | total_return | MDD | 승률 | mean_net_r | "
        "net_r 승/패 | cost_r 중앙값 | 손익분기 승률 | 여유 | PF | 심볼(제외) |\n"
        + "| -- " * 15
        + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                for arm in ARMS:
                    m = symbol_mean(
                        rows, barrier=barrier, timeframe=timeframe, segment=segment, arm=arm
                    )
                    if not m.get("n_symbols"):
                        continue
                    nt = m.get("num_trades")
                    lines.append(
                        f"| `{barrier}` | {timeframe} | {segment} | `{arm}` | "
                        f"{'—' if nt is None else f'{nt:.0f}'} | "
                        f"{_pct(m.get('total_return'), signed=True)} | "
                        f"{_pct(m.get('max_drawdown'))} | {_pct(m.get('win_rate'))} | "
                        f"{_num(m.get('mean_net_r'))} | "
                        f"{_num(m.get('net_r_win'))}/{_num(m.get('net_r_loss'))} | "
                        f"{_num(m.get('cost_r_median'), signed=False)} | "
                        f"{_pct(m.get('breakeven_win_rate'))} | "
                        f"{_pct(m.get('win_rate_margin'), signed=True)} | "
                        f"{_num(m.get('profit_factor'), signed=False, digits=2)} | "
                        f"{m.get('n_symbols'):.0f}({m.get('n_excluded'):.0f}) |"
                    )
    lines.append("")
    return "\n".join(lines)


def _pnl_section(
    result: AuditResult, tests: Sequence[MatchedTestRow], timeframes: Sequence[str]
) -> str:
    lines = ["## §1′ 거래당 실현 손익비 — 세 팔 대조표 (심볼평균, `baseline`)\n"]
    lines.append(
        f"⚠️ 심볼평균은 거래 {MIN_TRADES_FOR_PNL}건 미만 셀을 제외한다(제외 수 병기). "
        "`cost_r`은 왕복 비용(수수료+청산 슬리피지+펀딩) ÷ 그 거래의 리스크 금액, 손익분기 "
        "승률 = (1+cost_r)/((1.5−cost_r)+(1+cost_r)), 여유 = 실제 승률 − 손익분기.\n"
    )
    lines.append(_arm_table(result.pnl_rows, timeframes, title="### 팔 대조표"))
    lines.append("### 장벽별 매칭 검정 (수익·승률·MDD 순위 p)\n")
    lines.append(
        "| 장벽 | TF | 구간 | 심볼 | 필터 수익 | 매칭 수익 | p(수익) | 필터 승률 | 매칭 승률 | "
        "p(승률) | 거래 잔차 |\n" + "| -- " * 11 + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                t = _find_test(tests, barrier=barrier, timeframe=timeframe, segment=segment)
                if t is None or t.n_symbols == 0:
                    continue
                gap = "—"
                if t.trade_gap_pct is not None:
                    warn = " 🚨" if abs(t.trade_gap_pct) > 5.0 else ""
                    gap = f"{t.trade_gap_pct:+.1f}%{warn}"
                lines.append(
                    f"| `{barrier}` | {timeframe} | {segment} | {t.n_symbols} | "
                    f"{_pct(t.filter_return, signed=True)} | "
                    f"{_pct(t.matched_return_mean, signed=True)} | "
                    f"{'—' if t.p_return is None else f'{t.p_return:.3f}'} | "
                    f"{_pct(t.filter_win_rate)} | {_pct(t.matched_win_rate_mean)} | "
                    f"{'—' if t.p_win_rate is None else f'{t.p_win_rate:.3f}'} | {gap} |"
                )
    lines.append("")
    lines.append("**심볼 편중(OOS 필터 팔 leave-one-out):**\n")
    for barrier in BARRIERS:
        for timeframe in timeframes:
            loo = leave_one_out(
                result.pnl_rows, barrier=barrier, timeframe=timeframe, arm=ARM_FILTER
            )
            lines.append(f"* `{barrier}` {timeframe} `{ARM_FILTER}`: {loo}")
    lines.append("")
    return "\n".join(lines)


def _bet_size_section(result: AuditResult, timeframes: Sequence[str]) -> str:
    lines = ["## §2 베팅 크기 — 레버리지 상한 발동률 · 실효 리스크\n"]
    lines.append(
        "상한 발동 = 손절 거리 분수 < `risk_per_trade`/`leverage`(= 1%). 발동하면 원하는 "
        "수량을 못 사서 실효 리스크 = `leverage` × 손절 거리 분수 < 1%가 된다. 자본과 무관한 "
        "해석식이라 시퀀싱 순서에 의존하지 않는다.\n"
    )
    lines.append(
        "| 장벽 | TF | 구간 | 팔 | 상한 발동률 | 실효 리스크 평균 | 가드 탈락률(후보) |\n"
        + "| -- " * 7
        + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                for arm in ARMS:
                    m = symbol_mean(
                        result.pnl_rows,
                        barrier=barrier,
                        timeframe=timeframe,
                        segment=segment,
                        arm=arm,
                    )
                    if not m.get("n_symbols"):
                        continue
                    lines.append(
                        f"| `{barrier}` | {timeframe} | {segment} | `{arm}` | "
                        f"{_pct(m.get('cap_hit_rate'), digits=1)} | "
                        f"{_pct(m.get('effective_risk_mean'), digits=3)} | "
                        f"{_pct(m.get('guard_reject_rate'), digits=1)} |"
                    )
    lines.append("")
    lines.append("**`total_return` 우위와 `mean_net_r` 우위가 갈리는 셀(필터 vs 매칭):**\n")
    cells = divergent_cells(result.pnl_rows)
    if cells:
        for c in cells:
            lines.append(f"* 🚨 {c}")
    else:
        lines.append("* 갈리는 셀 없음 — 두 지표가 같은 방향을 가리킨다.")
    lines.append("")
    return "\n".join(lines)


def _bucket_section(result: AuditResult, timeframes: Sequence[str]) -> str:
    lines = ["## §3-A 손절폭 버킷 진단표 (가드 0%로 시퀀싱 · 심볼 풀링)\n"]
    lines.append(
        "⚠️ **가드를 끄고 잰 표다** — 가드를 켜면 0.3% 미만 버킷에 거래가 없어 「가드가 "
        "자르는 구간」이 안 보인다. 앞 두 버킷(<0.2% · 0.2~0.3%)이 현행 가드(0.3%)가 자르는 "
        "자리다. 생존율 = 1 − 손절 비율. 매칭 팔은 시드 20개 풀링.\n"
    )
    lines.append(
        "| TF | 구간 | 장벽 | 팔 | 버킷 | 거래 | 생존율 | 승률 | mean_net_r | cost_r | "
        "손익분기 | 여유 | 상한 발동 | 실효 리스크 |\n" + "| -- " * 14 + "|"
    )
    for row in result.bucket_rows:
        lines.append(
            f"| {row.timeframe} | {row.segment} | `{row.barrier}` | `{row.arm}` | {row.bucket} | "
            f"{row.n_trades} | {_pct(row.survival_rate)} | {_pct(row.win_rate)} | "
            f"{_num(row.mean_net_r)} | {_num(row.cost_r_median, signed=False)} | "
            f"{_pct(row.breakeven_win_rate)} | {_pct(row.win_rate_margin, signed=True)} | "
            f"{_pct(row.cap_hit_rate, digits=1)} | {_pct(row.effective_risk_mean, digits=3)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _guard_section(result: AuditResult, timeframes: Sequence[str]) -> str:
    lines = ["## §3-B 가드 5값 × 장벽 3종 — 「가드가 이득인가」 (OOS · `default` 팔)\n"]
    lines.append(
        f"가드 축: 0%(끔 — 판정의 분모) · 0.22%/0.33%/0.55%(왕복 비용 {ROUND_TRIP_COST:.2%}의 "
        "2/3/5배) · **0.3%(현행, WAN-79)**. 가드는 시퀀싱(`position_size`)에서만 걸리므로 "
        "체결 집합은 다섯 값이 같고, 거절된 거래의 슬롯이 비어 다른 셋업이 들어오는 것까지가 "
        "차이다.\n"
    )
    lines.append(
        "| 장벽 | TF | 팔 | 가드 | 거래 | total_return | MDD | 수익/MDD | 승률 여유 | "
        "가드 탈락률(후보) |\n" + "| -- " * 10 + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for arm in (ARM_DEFAULT, ARM_FILTER):
                for guard in GUARD_VALUES:
                    m = _guard_cell(
                        result.guard_rows,
                        barrier=barrier,
                        timeframe=timeframe,
                        guard=guard,
                        arm=arm,
                    )
                    if not m.get("n_symbols"):
                        continue
                    ret, mdd = m.get("total_return"), m.get("max_drawdown")
                    ra = "—"
                    if ret is not None and mdd is not None and mdd > 0:
                        ra = f"{ret / mdd:.2f}"
                    mark = " ←현행" if abs(guard - STOP_GUARD_FRACTION) < 1e-12 else ""
                    nt = m.get("num_trades")
                    lines.append(
                        f"| `{barrier}` | {timeframe} | `{arm}` | {guard * 100:.2f}%{mark} | "
                        f"{'—' if nt is None else f'{nt:.0f}'} | {_pct(ret, signed=True)} | "
                        f"{_pct(mdd)} | {ra} | {_pct(m.get('win_rate_margin'), signed=True)} | "
                        f"{_pct(m.get('guard_reject_rate'), digits=1)} |"
                    )
    lines.append("")
    lines.append("**가드 × 존폭 필터 충돌 — 심볼별 가드 탈락률(후보 기준 · 현행 0.3% · OOS):**\n")
    lines.append(
        "| 장벽 | TF | 심볼 | 필터 후보 | 탈락률 | 필터 거래 | 유효 표본 |\n" + "| -- " * 7 + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for row in result.pnl_rows:
                if (
                    row.barrier != barrier
                    or row.timeframe != timeframe
                    or row.segment != SEGMENT_OOS
                    or row.arm != ARM_FILTER
                    or row.seed != SEED_AGGREGATE
                ):
                    continue
                ok = "✅" if row.num_trades >= MIN_TRADES_FOR_PNL else "🚨 붕괴"
                lines.append(
                    f"| `{barrier}` | {timeframe} | {_bare(row.symbol)} | "
                    f"{row.num_candidates:.0f} | {_pct(row.guard_reject_rate, digits=1)} | "
                    f"{row.num_trades:.0f} | {ok} |"
                )
    lines.append("")
    collapse = [
        f"`{r.barrier}` {r.timeframe} {r.segment} {_bare(r.symbol)}({r.num_trades:.0f}거래)"
        for r in result.pnl_rows
        if r.arm == ARM_FILTER and r.seed == SEED_AGGREGATE and r.num_trades < MIN_TRADES_FOR_PNL
    ]
    lines.append(
        "**유효 표본(거래 20건) 붕괴 셀:** " + (" · ".join(collapse) if collapse else "없음") + "\n"
    )
    return "\n".join(lines)


def _pen_section(
    result: AuditResult,
    tests: Sequence[MatchedTestRow],
    pen_tests: Sequence[MatchedTestRow],
    timeframes: Sequence[str],
) -> str:
    lines = ["## §4 체결 보수화 — `baseline` vs `pen_5bp` 병기 (OOS)\n"]
    lines.append(
        "`pen_5bp` = 주문가를 5bp **관통**해야 체결(옵트인, WAN-128 이후 수동 확인용). 체결 "
        "집합이 달라지므로 셀을 그 파라미터로 다시 빌드했다 — 질문은 「필터와 매칭이 같이 "
        "깎이는가, 필터만 깎이는가」다.\n"
    )
    lines.append(
        "| 장벽 | TF | 렌즈 | 필터 수익 | 매칭 수익 | p(수익) | 필터 승률 | 손익분기 | 여유 |\n"
        + "| -- " * 9
        + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for lens, tset, rows in (
                (LENS_PRIMARY, tests, result.pnl_rows),
                (LENS_PEN, pen_tests, result.pen_rows),
            ):
                t = _find_test(tset, barrier=barrier, timeframe=timeframe)
                if t is None or t.n_symbols == 0:
                    continue
                m = symbol_mean(
                    rows,
                    barrier=barrier,
                    timeframe=timeframe,
                    segment=SEGMENT_OOS,
                    arm=ARM_FILTER,
                )
                lines.append(
                    f"| `{barrier}` | {timeframe} | `{lens}` | "
                    f"{_pct(t.filter_return, signed=True)} | "
                    f"{_pct(t.matched_return_mean, signed=True)} | "
                    f"{'—' if t.p_return is None else f'{t.p_return:.3f}'} | "
                    f"{_pct(t.filter_win_rate)} | {_pct(m.get('breakeven_win_rate'))} | "
                    f"{_pct(m.get('win_rate_margin'), signed=True)} |"
                )
    lines.append("")
    lines.append("**`pen_5bp` 필터 팔 leave-one-out(OOS):**\n")
    for barrier in BARRIERS:
        for timeframe in timeframes:
            loo = leave_one_out(
                result.pen_rows, barrier=barrier, timeframe=timeframe, arm=ARM_FILTER
            )
            lines.append(f"* `{barrier}` {timeframe}: {loo}")
    lines.append("")
    return "\n".join(lines)


def _threshold_section(result: AuditResult, timeframes: Sequence[str]) -> str:
    lines = ["## §5 문턱 민감도 — 하위 1/4 · 1/3 · 1/2 (OOS)\n"]
    lines.append(
        "| 문턱 | 장벽 | TF | 필터 수익 | 매칭 수익 | p(수익) | 필터 거래 | 가드 탈락률(후보) |\n"
        + "| -- " * 8
        + "|"
    )
    for fraction in THRESHOLD_FRACTIONS:
        sub = [r for r in result.threshold_rows if abs(r.threshold_fraction - fraction) < 1e-12]
        for barrier in BARRIERS:
            for timeframe in timeframes:
                t = matched_test_row(sub, barrier=barrier, timeframe=timeframe, segment=SEGMENT_OOS)
                if t.n_symbols == 0:
                    continue
                m = symbol_mean(
                    sub, barrier=barrier, timeframe=timeframe, segment=SEGMENT_OOS, arm=ARM_FILTER
                )
                nt = m.get("num_trades")
                lines.append(
                    f"| 하위 {fraction:.2g} | `{barrier}` | {timeframe} | "
                    f"{_pct(t.filter_return, signed=True)} | "
                    f"{_pct(t.matched_return_mean, signed=True)} | "
                    f"{'—' if t.p_return is None else f'{t.p_return:.3f}'} | "
                    f"{'—' if nt is None else f'{nt:.0f}'} | "
                    f"{_pct(m.get('guard_reject_rate'), digits=1)} |"
                )
    lines.append("")
    lines.append("**IS 문턱값의 OOS 안정성(zone_width_atr 절대값 · 심볼별):**\n")
    lines.append("| 심볼 | TF | 문턱 | IS 문턱값 | OOS에서 그 값 이하 비율 |\n" + "| -- " * 5 + "|")
    for row in result.stability_rows:
        lines.append(
            f"| {_bare(row.symbol)} | {row.timeframe} | 하위 {row.threshold_fraction:.2g} | "
            f"{_num(row.is_threshold_value, signed=False)} | {_pct(row.oos_coverage, digits=1)} |"
        )
    lines.append("")
    lines.append(
        "읽는 법: 마지막 열이 문턱 분위와 비슷하면 IS에서 잡은 절대 문턱값이 OOS에서도 같은 "
        "자리를 자른다(분포 안정). 크게 벗어나면 존폭 분포가 구간을 넘어 이동했다는 뜻이다.\n"
    )
    return "\n".join(lines)


def _conclusion(
    result: AuditResult,
    tests: Sequence[MatchedTestRow],
    pen_tests: Sequence[MatchedTestRow],
    timeframes: Sequence[str],
) -> str:
    robust = [robustness_verdict(tests, timeframe=tf) for tf in timeframes]
    kinds = {r.kind for r in robust}
    if kinds == {RobustnessKind.ALL}:
        head = (
            "**§0: 존폭 필터 우위는 세 자(`zone`·`atr`·`zone_height`) 전부에서 유지된다 — "
            "판정이 자에 의존하지 않는다.**"
        )
    elif RobustnessKind.COLLAPSED in kinds:
        head = "**§0: 통제 장벽에서 우위가 무너지는 TF가 있다 — 세 자 강건성은 성립하지 않는다.**"
    else:
        head = "**§0: 세 자 강건성은 부분적이다 — 장벽·TF별 상세는 판정 모음 참고.**"
    return (
        head + " §2(베팅 크기)·§3-B(가드)·§4(체결)·§5(문턱)의 판정 문장은 위 「판정 모음」이 "
        "정본이다.\n\n"
        "🚨 **「엣지 찾았다」로 인용 금지** — 이 표는 `baseline`(닿으면 체결) 위의 값이고"
        "(§4의 `pen_5bp`는 옵트인 민감도 병기다), 「엣지 없음」(WAN-84/88/111/114/124/145)은 "
        "다른 질문(진입 규칙 vs 무작위)이라 뒤집히지 않는다. **기본값·토대는 바꾸지 않았다**"
        "(측정 전용 — `ConfluenceParams()` 불변, `min_stop_distance_fraction=0.003` 불변, "
        "`leverage=1.0` 불변, `take_profit_r=1.5` 불변, 실거래 보류 "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지). 가드·상한·사이징·익절·필터 채택 변경은 전부 "
        "**재-베이스라인 = 사용자 결정**이고 **개발자 임의 착수 금지**다(WAN-112/123/132/149와 "
        "같은 부류)."
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _load_pnl(path: Path) -> list[PnlRow]:
    if not path.exists():
        return []
    return [PnlRow.model_validate(rec) for rec in pd.read_csv(path).to_dict("records")]


def _write_rows(rows: Sequence[BaseModel], path: Path, *, append: bool) -> pd.DataFrame:
    frame = pd.DataFrame([r.model_dump() for r in rows])
    if append and path.exists():
        frame = pd.concat([pd.read_csv(path), frame], ignore_index=True)
    _write_csv(frame, path)
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-154 손절폭 축 통합 측정")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--pnl-out", type=Path, default=REPORTS_DIR / "wan152_barrier_pnl.csv")
    parser.add_argument("--test-out", type=Path, default=REPORTS_DIR / "wan152_matched_test.csv")
    parser.add_argument("--pen-out", type=Path, default=REPORTS_DIR / "wan154_pen_pnl.csv")
    parser.add_argument("--pen-test-out", type=Path, default=REPORTS_DIR / "wan154_pen_test.csv")
    parser.add_argument("--guard-out", type=Path, default=REPORTS_DIR / "wan154_guard_grid.csv")
    parser.add_argument("--threshold-out", type=Path, default=REPORTS_DIR / "wan154_threshold.csv")
    parser.add_argument(
        "--threshold-test-out", type=Path, default=REPORTS_DIR / "wan154_threshold_test.csv"
    )
    parser.add_argument("--bucket-out", type=Path, default=REPORTS_DIR / "wan154_stop_buckets.csv")
    parser.add_argument(
        "--stability-out", type=Path, default=REPORTS_DIR / "wan154_threshold_stability.csv"
    )
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan154_summary.md")
    parser.add_argument(
        "--append", action="store_true", help="기존 CSV들에 이어 붙인다(TF 분할 실행)."
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 기존 CSV로 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    if args.from_csv:
        result = AuditResult(
            pnl_rows=_load_pnl(args.pnl_out),
            pen_rows=_load_pnl(args.pen_out),
            guard_rows=_load_pnl(args.guard_out),
            threshold_rows=_load_pnl(args.threshold_out),
            bucket_rows=[
                BucketRow.model_validate(rec)
                for rec in pd.read_csv(args.bucket_out).to_dict("records")
            ]
            if args.bucket_out.exists()
            else [],
            stability_rows=[
                ThresholdStabilityRow.model_validate(rec)
                for rec in pd.read_csv(args.stability_out).to_dict("records")
            ]
            if args.stability_out.exists()
            else [],
        )
    else:
        result = run_audit(
            symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in args.timeframes.split(",") if t.strip()),
            start=args.start,
            end=args.end,
            db_path=args.db,
        )
        _write_rows(result.pnl_rows, args.pnl_out, append=args.append)
        print(f"[wan154] pnl → {args.pnl_out}")
        _write_rows(result.pen_rows, args.pen_out, append=args.append)
        _write_rows(result.guard_rows, args.guard_out, append=args.append)
        _write_rows(result.threshold_rows, args.threshold_out, append=args.append)
        _write_rows(result.bucket_rows, args.bucket_out, append=args.append)
        _write_rows(result.stability_rows, args.stability_out, append=args.append)
        # 이어 붙였으면 검정·요약은 합친 표 위에서 다시 낸다(풀 메모는 이번 실행 몫만 남는다).
        notes, pen_notes = result.pool_notes, result.pen_pool_notes
        result = AuditResult(
            pnl_rows=_load_pnl(args.pnl_out),
            pen_rows=_load_pnl(args.pen_out),
            guard_rows=_load_pnl(args.guard_out),
            threshold_rows=_load_pnl(args.threshold_out),
            bucket_rows=[
                BucketRow.model_validate(rec)
                for rec in pd.read_csv(args.bucket_out).to_dict("records")
            ],
            stability_rows=[
                ThresholdStabilityRow.model_validate(rec)
                for rec in pd.read_csv(args.stability_out).to_dict("records")
            ],
            pool_notes=notes,
            pen_pool_notes=pen_notes,
        )

    tests = result.matched_tests(result.pnl_rows)
    _write_csv(_rows_to_frame(tests), args.test_out)
    print(f"[wan154] matched_test → {args.test_out}")
    pen_tests = result.matched_tests(result.pen_rows)
    pen_frame = _rows_to_frame(pen_tests)
    pen_frame["lens"] = LENS_PEN
    _write_csv(pen_frame, args.pen_test_out)
    thr_frames: list[pd.DataFrame] = []
    for fraction in THRESHOLD_FRACTIONS:
        sub = [r for r in result.threshold_rows if abs(r.threshold_fraction - fraction) < 1e-12]
        if not sub:
            continue
        frame = _rows_to_frame(result.matched_tests(sub))
        frame["threshold_fraction"] = fraction
        thr_frames.append(frame)
    if thr_frames:
        _write_csv(pd.concat(thr_frames, ignore_index=True), args.threshold_test_out)

    timeframes = tuple(dict.fromkeys(r.timeframe for r in result.pnl_rows))
    summary = build_summary_markdown(result, timeframes=timeframes)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan154] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
