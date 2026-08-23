"""좁은 존 선별 심화 — 존폭 문턱 스윕 × 매칭 널(기하 통제) (WAN-203, 트랙 1).

## 이 모듈이 재는 것

존폭 필터(좁은 존만 매매)는 이 저장소에서 **유일하게 매칭 널의 여러 관문을 통과한 선별
축**이다 — WAN-142(「표본 축소」 아님) · WAN-152(「기하」 아님) · WAN-154(세 장벽·문턱
3종·`pen_5bp` 생존). WAN-159가 이 필터를 채택 기본값(`max_zone_width_atr=1.28`)으로 올렸다.

이 이슈는 그 선별 축을 **더 밀어붙여** "좁을수록 정보가 많은가"를 정량화한다. WAN-152/154는
필터를 **IS 분위**(하위 1/3)로 정의했지만, 채택 기본값(WAN-159)은 **절대 문턱**(ATR 배수
1.28)이다. 여기서는 그 절대 문턱을 축으로 스윕한다:

* 문턱 = {1.6, 1.28, 1.0, 0.8, 0.6} (`zone_width_atr ≤ 문턱`인 좁은 존만 필터 팔에 남긴다).
* 기하 통제 = WAN-152의 ATR 장벽(`stop_loss_override` = 진입가 ∓ `ATR_K`·ATR). 손절이
  존폭과 무관해져 「좁아서 익절선이 가까워진다」는 산수를 지운다. 그 뒤에도 필터가 같은
  개수의 무작위 대조군을 이기면 = **선별의 잔여분**.
* **판정 질문**: 조일수록 (필터 − 매칭) 마진이 **단조 증가**하다 표본이 죽기 직전 붕괴하는가
  (= 좁을수록 정보 많음)? 아니면 1.28 근처에서 평평/역전되는가?

## 파이프라인 재사용 — 새 파이프라인 금지 (이슈 사양)

셀 빌드·공통 풀·매칭 추첨·검정은 전부 `backtest.wan152_selection_vs_geometry`의 것이다.
이 모듈은 **문턱 축**만 얹어 그 파이프라인을 오케스트레이션한다. WAN-152에 추가한
`pnl_rows_for_cell(..., abs_threshold=)`가 필터 팔을 IS 분위 대신 절대 문턱으로 고른다
(`None`이면 예전과 비트 동일 — WAN-152/154 CSV 재현).

🚨 **옛 핀 미사용** — `LEGACY_COMBINE_OBS`·`LEGACY_BAND_BAR`·`pin_band_bar`·`LEGACY_OB_PARAMS`를
쓰지 않는다(오늘의 채택 기본값 그대로). 단 **엔진의 채택 필터(1.28)는 끈다**
(`LEGACY_MAX_ZONE_WIDTH_ATR`) — 좁은 존 팔을 후보 리스트로 직접 만드므로 엔진이 먼저
1.28로 거르면 대조군·매칭이 오염되는 이중 필터가 된다(WAN-159/152 선례). 회귀 테스트가
라벨이 아니라 **후보 집합**으로 이것을 고정한다.

## 좌표 — 채택 좌표(WAN-182)

9종목 × 못 박은 6년(2020-09-15~2026-07-22) × 작업 TF 15m·1h·4h × IS/OOS × `baseline`.
신규 3종목(DOGE·LINK·LTC)은 펀딩 0행이고 **널 계열 관행대로 대리를 얹지 않는다**
(WAN-201) — 필터·매칭 두 팔이 같은 펀딩을 공유하므로 마진에는 대칭으로 상쇄된다.

재현:
```
python -m backtest.wan203_narrow_zone_selection --jobs 4    # baseline+pen_5bp 전 격자
python -m backtest.wan203_narrow_zone_selection --checksum  # 6종목·3년 default ≡ wan152
python -m backtest.wan203_narrow_zone_selection --from-csv  # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_IS, SEGMENT_OOS
from backtest.run import parse_date_ms
from backtest.wan133_geometry_vs_selection import (
    ARM_DEFAULT,
    ARM_FILTER,
    REPORTS_DIR,
    STOP_GUARD_FRACTION,
    _bare,
    _write_csv,
)
from backtest.wan142_zone_width_filter_verdict import (
    ALPHA,
    MATCH_SEEDS,
    SEED_AGGREGATE,
)
from backtest.wan152_selection_vs_geometry import (
    ATR_K,
    BARRIER_ATR,
    BARRIER_ZONE,
    LENS_PRIMARY,
    MatchedTestRow,
    PnlRow,
    _p,
    _pct,
    build_cell,
    guard_note,
    leave_one_out,
    matched_test_row,
    pnl_rows_for_cell,
)
from backtest.wan154_stop_width_audit import LENS_PEN

# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

#: 채택 좌표(WAN-182) — 9종목 × 6년 × 작업 TF 15m·1h·4h.
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
DEFAULT_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS
DEFAULT_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

#: 절대 존폭÷ATR 문턱 축(loose→tight). 채택 기본값 1.28(WAN-159)을 가운데 두고 양쪽으로
#: 벌린다. `zone_width_atr ≤ 문턱`인 좁은 존만 필터 팔에 남는다.
THRESHOLDS_ATR: tuple[float, ...] = (1.6, 1.28, 1.0, 0.8, 0.6)

#: `zone` = 채택 장벽(1R이 존폭에 비례 = 기하가 산다) · `atr` = 기하 통제 장벽(1R = k·ATR,
#: 존폭 무관). 판정은 WAN-152 원 사양대로 `zone` vs `atr`로 낸다. ⚠️ WAN-154의 세 번째 자
#: `zone_height`는 **컴퓨트 예산으로 생략**한다 — 장벽마다 1분봉 서브스텝을 다시 돌려
#: 15m·6년에서 초선형이라, 판정에 안 쓰는 강건성 자 하나를 빼 셀당 비용을 3분의 1 줄인다.
BARRIERS_USED: tuple[str, ...] = (BARRIER_ZONE, BARRIER_ATR)

#: 4관문 판정에 쓰는 자 = `atr` 장벽(기하 통제). 여기서 필터가 무작위를 이기면 선별의 잔여분.
VERDICT_BARRIER = BARRIER_ATR

LENSES: tuple[str, ...] = (LENS_PRIMARY, LENS_PEN)

#: 검산용 옛 좌표 — WAN-152/154는 6종목 × 못 박은 3년 창에서 나왔다(PM 착수 메모 #1).
#: 본 격자(9종목·6년)로는 비트 재현이 성립하지 않으므로, 겹치는 이 좌표로 별도 대조 실행을
#: 돌려 `default` 팔(문턱 무관)을 `wan152_barrier_pnl.csv`에 붙인다.
CHECKSUM_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)
CHECKSUM_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")
CHECKSUM_START = "2023-07-14"
CHECKSUM_END = "2026-07-15"


# --------------------------------------------------------------------------- #
# 행 모델 — 문턱·렌즈를 좌표로 얹은 손익/검정 행
# --------------------------------------------------------------------------- #


class SweepPnlRow(BaseModel):
    """`PnlRow`에 문턱(절대 ATR 배수)·렌즈 좌표를 얹은 행. CSV의 한 줄이다.

    `PnlRow`를 상속하지 않고 조합한다 — `PnlRow`에 열을 더하면 WAN-152/154 CSV가 열 하나
    늘어 비트 재현이 깨진다(그쪽 모듈이 같은 모델을 dump한다). 여기서만 문턱/렌즈를 얹는다.
    """

    model_config = ConfigDict(frozen=True)

    threshold_atr: float
    lens: str
    inner: PnlRow

    def flat(self) -> dict[str, object]:
        d = self.inner.model_dump()
        d.pop("lens", None)  # PnlRow.lens는 항상 그 셀 렌즈라 바깥 lens와 중복.
        return {"threshold_atr": self.threshold_atr, "lens": self.lens, **d}


class SweepTestRow(BaseModel):
    """한 (문턱, 렌즈, 장벽, TF, 구간)의 매칭 검정 + (필터 − 매칭) 마진."""

    model_config = ConfigDict(frozen=True)

    threshold_atr: float
    lens: str
    barrier: str
    timeframe: str
    segment: str
    n_symbols: int
    n_seeds: int
    filter_return: float | None
    matched_return_mean: float | None
    margin_return: float | None
    """필터 수익 − 매칭 수익 평균(선별의 잔여분 크기)."""
    p_return: float | None
    filter_win_rate: float | None
    matched_win_rate_mean: float | None
    margin_win_rate: float | None
    p_win_rate: float | None
    filter_mdd: float | None
    matched_mdd_mean: float | None
    p_mdd: float | None
    filter_trades: float | None
    matched_trades: float | None
    trade_gap_pct: float | None

    @classmethod
    def build(cls, t: MatchedTestRow, *, threshold_atr: float, lens: str) -> SweepTestRow:
        def _margin(a: float | None, b: float | None) -> float | None:
            return None if a is None or b is None else a - b

        return cls(
            threshold_atr=threshold_atr,
            lens=lens,
            barrier=t.barrier,
            timeframe=t.timeframe,
            segment=t.segment,
            n_symbols=t.n_symbols,
            n_seeds=t.n_seeds,
            filter_return=t.filter_return,
            matched_return_mean=t.matched_return_mean,
            margin_return=_margin(t.filter_return, t.matched_return_mean),
            p_return=t.p_return,
            filter_win_rate=t.filter_win_rate,
            matched_win_rate_mean=t.matched_win_rate_mean,
            margin_win_rate=_margin(t.filter_win_rate, t.matched_win_rate_mean),
            p_win_rate=t.p_win_rate,
            filter_mdd=t.filter_mdd,
            matched_mdd_mean=t.matched_mdd_mean,
            p_mdd=t.p_mdd,
            filter_trades=t.filter_trades,
            matched_trades=t.matched_trades,
            trade_gap_pct=t.trade_gap_pct,
        )


# --------------------------------------------------------------------------- #
# 셀 워커 (병렬)
# --------------------------------------------------------------------------- #


@dataclass
class CellWork:
    symbol: str
    timeframe: str
    start: str
    end: str
    db_path: str
    lenses: tuple[str, ...]
    thresholds: tuple[float, ...]


def _cell_worker(work: CellWork) -> tuple[list[SweepPnlRow], list[str]]:
    """한 (심볼, TF)의 모든 렌즈 × 문턱 손익 행 + 풀 메모.

    1분봉은 한 번만 로드하고 렌즈별로 셀을 다시 빌드한다(체결 집합이 렌즈마다 다르다).
    문턱은 재시퀀싱/부분집합만 바꾸므로 셀을 재사용한다.
    """
    start_ms, end_ms = parse_date_ms(work.start), parse_date_ms(work.end)
    norm = harness.normalize_symbol(work.symbol)
    market = harness.load_market_data(
        norm, work.timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, db_path=work.db_path
    )
    rows: list[SweepPnlRow] = []
    notes: list[str] = []
    for lens in work.lenses:
        # 🚨 이중 필터 방지(WAN-159): 좁은 존 팔을 직접 만드므로 엔진 필터(1.28)를 끈다.
        params = harness.build_params(
            fill=harness.fill_preset(lens),
            max_zone_width_atr=harness.LEGACY_MAX_ZONE_WIDTH_ATR,
            # WAN-365 명시 핀: 이 표는 **소급 취소** 시절의 결론이다.
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        )
        cell = build_cell(market, params=params, barriers=BARRIERS_USED)
        raw = " · ".join(f"`{b}` {cell.n_raw.get(b, 0)}" for b in BARRIERS_USED)
        notes.append(f"{_bare(norm)} {work.timeframe} [{lens}]: 후보 {raw} → 공통 풀 {cell.n_pool}")
        for t in work.thresholds:
            for pr in pnl_rows_for_cell(cell, market, lens=lens, abs_threshold=t):
                rows.append(SweepPnlRow(threshold_atr=t, lens=lens, inner=pr))
    return rows, notes


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass
class SweepResult:
    pnl_rows: list[SweepPnlRow] = field(default_factory=list)
    test_rows: list[SweepTestRow] = field(default_factory=list)
    pool_notes: list[str] = field(default_factory=list)


def _partition(rows: Sequence[SweepPnlRow], *, threshold: float, lens: str) -> list[PnlRow]:
    """한 (문턱, 렌즈)의 순수 `PnlRow` 목록 — 검정·집계는 이 위에서 돈다."""
    return [r.inner for r in rows if abs(r.threshold_atr - threshold) < 1e-12 and r.lens == lens]


def _build_tests(rows: Sequence[SweepPnlRow], *, timeframes: Sequence[str]) -> list[SweepTestRow]:
    lenses = tuple(dict.fromkeys(r.lens for r in rows))
    thresholds = sorted({r.threshold_atr for r in rows}, reverse=True)
    out: list[SweepTestRow] = []
    for lens in lenses:
        for threshold in thresholds:
            partition = _partition(rows, threshold=threshold, lens=lens)
            for barrier in BARRIERS_USED:
                for timeframe in timeframes:
                    for segment in (SEGMENT_IS, SEGMENT_OOS):
                        t = matched_test_row(
                            partition, barrier=barrier, timeframe=timeframe, segment=segment
                        )
                        out.append(SweepTestRow.build(t, threshold_atr=threshold, lens=lens))
    return out


def run_sweep(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    lenses: tuple[str, ...] = LENSES,
    thresholds: tuple[float, ...] = THRESHOLDS_ATR,
    db_path: str = harness.DB_PATH,
    jobs: int = 1,
) -> SweepResult:
    """문턱 스윕 × 매칭 널 — 셀 빌드는 (심볼, TF) 단위로 병렬화한다.

    ⚠️ `jobs`는 성능 노브일 뿐이다 — 직렬(`jobs=1`)과 행이 비트 단위로 같다(회귀 테스트가
    합성으로 고정). 병렬은 (심볼, TF) 워커 사이에 상태 공유가 없어 안전하다.
    """
    works = [
        CellWork(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            db_path=db_path,
            lenses=lenses,
            thresholds=thresholds,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    result = SweepResult()
    done = 0

    def _absorb(rows: list[SweepPnlRow], notes: list[str]) -> None:
        nonlocal done
        done += 1
        result.pnl_rows.extend(rows)
        result.pool_notes.extend(notes)
        for note in notes:
            print(f"[wan203] ({done}/{len(works)}) {note}", flush=True)

    if jobs and jobs != 1:
        max_workers = jobs if jobs > 0 else None
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            # as_completed(map 아님) — 완료 셀을 순서 상관없이 즉시 찍어 진행이 보이게 한다.
            futures = [pool.submit(_cell_worker, work) for work in works]
            for fut in as_completed(futures):
                rows, notes = fut.result()
                _absorb(rows, notes)
    else:
        for work in works:
            rows, notes = _cell_worker(work)
            _absorb(rows, notes)
    result.test_rows = _build_tests(result.pnl_rows, timeframes=timeframes)
    return result


# --------------------------------------------------------------------------- #
# 판정 — 단조성 (조일수록 마진 증가 후 붕괴 vs 평평/역전)
# --------------------------------------------------------------------------- #


class TrajectoryKind(StrEnum):
    """한 TF의 문턱 스윕 궤적 종류 — **문장이 아니라 이 값이 정본**(WAN-142 열거형 교훈)."""

    STRENGTHENS = "strengthens"  # 조일수록 마진 단조 증가(표본 붕괴 직전까지) = 좁을수록 정보 많음
    FLAT_OR_REVERSAL = "flat_or_reversal"  # 1.28 근처 평평/역전 = 조여도 정보 안 는다
    NO_EDGE = "no_edge"  # 유효 문턱에서 유의(p≤0.05)가 하나도 없다
    INDETERMINATE = "indeterminate"  # 유효 표본 부족


@dataclass(frozen=True)
class Trajectory:
    kind: TrajectoryKind
    text: str


def _find_test(
    tests: Sequence[SweepTestRow],
    *,
    threshold: float,
    lens: str,
    barrier: str,
    timeframe: str,
    segment: str = SEGMENT_OOS,
) -> SweepTestRow | None:
    return next(
        (
            t
            for t in tests
            if abs(t.threshold_atr - threshold) < 1e-12
            and t.lens == lens
            and t.barrier == barrier
            and t.timeframe == timeframe
            and t.segment == segment
        ),
        None,
    )


def trajectory(
    tests: Sequence[SweepTestRow],
    *,
    timeframe: str,
    lens: str = LENS_PRIMARY,
    barrier: str = VERDICT_BARRIER,
    thresholds: Sequence[float] = THRESHOLDS_ATR,
) -> Trajectory:
    """`atr` 장벽 OOS에서 문턱을 조일수록 (필터 − 매칭) 마진이 어떻게 움직이는가.

    조일수록 마진이 **단조 증가**하다 표본이 죽으면(유효 심볼 < 3) = 좁을수록 정보 많음
    (STRENGTHENS). 1.28 근처에서 마진이 정점을 찍고 평평·역전하면 = 조여도 정보 안 는다
    (FLAT_OR_REVERSAL). 유의(p≤0.05)가 한 문턱도 없으면 NO_EDGE.
    """
    ordered = sorted(thresholds, reverse=True)  # loose(1.6) → tight(0.6)
    valid: list[tuple[float, float, float, int]] = []  # (문턱, 마진, p, n_symbols)
    collapsed: list[float] = []
    for t in ordered:
        row = _find_test(tests, threshold=t, lens=lens, barrier=barrier, timeframe=timeframe)
        if row is None or row.margin_return is None or row.p_return is None:
            collapsed.append(t)
            continue
        if row.n_symbols < 3:
            collapsed.append(t)
            continue
        valid.append((t, row.margin_return, row.p_return, row.n_symbols))
    collapse_txt = (
        f" · 표본 붕괴(유효 심볼<3): {', '.join(f'{c:g}' for c in collapsed)}" if collapsed else ""
    )
    if not valid:
        return Trajectory(
            TrajectoryKind.INDETERMINATE,
            f"**{timeframe}** [`{barrier}`·{lens}]: ⚠️ 판정 불가 — 유효 표본(심볼≥3) 문턱이 "
            f"없다{collapse_txt}.",
        )
    any_sig = any(p <= ALPHA for _, _, p, _ in valid)
    detail = " → ".join(f"{t:g}: 마진 {m * 100:+.2f}%p(p={p:.3f}, n={n})" for t, m, p, n in valid)
    if not any_sig:
        return Trajectory(
            TrajectoryKind.NO_EDGE,
            f"**{timeframe}** [`{barrier}`·{lens}]: **엣지 없음** — 유효 문턱 어디서도 필터가 "
            f"무작위 대조군을 유의하게(p≤{ALPHA}) 이기지 못한다({detail}){collapse_txt}.",
        )
    margins = [m for _, m, _, _ in valid]
    monotone = all(b >= a - 1e-9 for a, b in zip(margins, margins[1:], strict=False))
    peak_at_tightest = margins[-1] >= max(margins) - 1e-9
    if monotone and peak_at_tightest and len(valid) >= 2:
        return Trajectory(
            TrajectoryKind.STRENGTHENS,
            f"**{timeframe}** [`{barrier}`·{lens}]: **(강화) 조일수록 마진이 커진다** — "
            f"문턱을 조일수록 (필터 − 매칭) 마진이 단조 증가한다({detail})"
            f"{collapse_txt}. 좁을수록 존폭이 더 많은 정보를 갖는다는 서명이다(🚨 그래도 채택이 "
            "아니다 — `baseline` 렌즈·심볼 편중·체결 현실화가 남아 있다).",
        )
    peak_t = max(valid, key=lambda v: v[1])[0]
    return Trajectory(
        TrajectoryKind.FLAT_OR_REVERSAL,
        f"**{timeframe}** [`{barrier}`·{lens}]: **(평평/역전) 조여도 마진이 안 는다** — 마진이 "
        f"문턱 {peak_t:g} 근처에서 정점을 찍고 더 조여도 커지지 않는다({detail})"
        f"{collapse_txt}. 「좁을수록 정보 많음」은 성립하지 않는다 — 채택 문턱 1.28이 특별히 "
        "많은 정보를 가진 지점이 아니라는 뜻이다.",
    )


# --------------------------------------------------------------------------- #
# 4관문
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateReport:
    threshold: float
    timeframe: str
    gate1_matched_null: bool | None  # atr 장벽 OOS p_return ≤ 0.05
    gate2_oos: bool | None  # OOS에서 마진 > 0 (판정은 OOS로 낸다)
    gate3_pen_survives: bool | None  # pen_5bp에서도 p ≤ 0.05
    gate4_eth_loo: str  # ETH leave-one-out 문구(전부 + 여부)
    p_baseline: float | None
    p_pen: float | None


def gate_report(result: SweepResult, *, threshold: float, timeframe: str) -> GateReport:
    base = _find_test(
        result.test_rows,
        threshold=threshold,
        lens=LENS_PRIMARY,
        barrier=VERDICT_BARRIER,
        timeframe=timeframe,
    )
    pen = _find_test(
        result.test_rows,
        threshold=threshold,
        lens=LENS_PEN,
        barrier=VERDICT_BARRIER,
        timeframe=timeframe,
    )
    g1 = None if base is None or base.p_return is None else base.p_return <= ALPHA
    g2 = None if base is None or base.margin_return is None else base.margin_return > 0
    g3 = None if pen is None or pen.p_return is None else pen.p_return <= ALPHA
    loo = leave_one_out(
        _partition(result.pnl_rows, threshold=threshold, lens=LENS_PRIMARY),
        barrier=VERDICT_BARRIER,
        timeframe=timeframe,
        arm=ARM_FILTER,
    )
    return GateReport(
        threshold=threshold,
        timeframe=timeframe,
        gate1_matched_null=g1,
        gate2_oos=g2,
        gate3_pen_survives=g3,
        gate4_eth_loo=loo,
        p_baseline=None if base is None else base.p_return,
        p_pen=None if pen is None else pen.p_return,
    )


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _b(v: bool | None) -> str:
    return "—" if v is None else ("✅" if v else "❌")


def build_summary_markdown(result: SweepResult, *, timeframes: Sequence[str]) -> str:
    lines: list[str] = []
    lines.append("# WAN-203 좁은 존 선별 심화 — 존폭 문턱 스윕 × 매칭 널(기하 통제)\n")
    symbols = sorted({_bare(r.inner.symbol) for r in result.pnl_rows}) or ["—"]
    thresholds = sorted({r.threshold_atr for r in result.pnl_rows}, reverse=True)
    lines.append(
        f"{len(symbols)}종목({'/'.join(symbols)}) × {'·'.join(timeframes)}, 못 박은 창 "
        f"**{DEFAULT_START} ~ {DEFAULT_END}**(채택 좌표 WAN-182), **오늘의 채택 기본값**"
        "(`ConfluenceParams()` — 오프셋 2bp · `intrabar_live` 밴드 · `unconditional` 게이트 · "
        "고정 1.5R · 롱 온리 · `combine_obs=False`, 단 **엔진 필터 1.28은 끄고** 좁은 존 팔을 "
        "직접 만든다). 공식 렌즈 `baseline`(+ `pen_5bp` 관문 3 옵트인).\n"
    )
    lines.append(
        f"문턱 축(절대 존폭÷ATR): {', '.join(f'{t:g}' for t in thresholds)} — "
        "`zone_width_atr ≤ 문턱`인 좁은 존만 필터 팔에 남긴다(`max_zone_width_atr` 의미). "
        f"기하 통제 = `{BARRIER_ATR}` 장벽(손절 = 진입가 ∓ {ATR_K:g}·ATR, 존폭 무관). "
        f"매칭 대조군 = 같은 개수 무작위 추첨(시드 {len(MATCH_SEEDS)}개, 단측 순위 p).\n"
    )
    lines.append(
        "📌 **신규 3종목 펀딩 대리 미적용**(널 계열 관행 WAN-201) — 필터·매칭 두 팔이 같은 "
        "펀딩을 공유해 마진에 대칭 상쇄된다. 📌 **핀 미사용** — 회귀 테스트가 후보 집합으로 "
        "고정한다.\n"
    )
    lines.append("재현: `python -m backtest.wan203_narrow_zone_selection --jobs 4`.\n")

    lines.append("## 판정 — 조일수록 마진이 커지는가 (`atr` 장벽 OOS, `baseline`)\n")
    for timeframe in timeframes:
        lines.append(f"* {trajectory(result.test_rows, timeframe=timeframe).text}")
    lines.append("")
    lines.append(_overall_conclusion(result, timeframes))

    lines.append(_gate_section(result, timeframes, thresholds))
    lines.append(_sweep_section(result, timeframes, thresholds))
    lines.append(_guard_section(result, timeframes, thresholds))
    lines.append(_pool_section(result))
    return "\n".join(lines)


def _overall_conclusion(result: SweepResult, timeframes: Sequence[str]) -> str:
    trajs = {tf: trajectory(result.test_rows, timeframe=tf) for tf in timeframes}
    strengthen = [tf for tf, v in trajs.items() if v.kind is TrajectoryKind.STRENGTHENS]
    flat = [tf for tf, v in trajs.items() if v.kind is TrajectoryKind.FLAT_OR_REVERSAL]
    no_edge = [tf for tf, v in trajs.items() if v.kind is TrajectoryKind.NO_EDGE]
    tail = (
        " 🚨 **어느 쪽이든 「엣지 찾았다」로 인용 금지** — 전부 `baseline`(닿으면 체결) 렌즈 "
        "위의 값이고, 유의가 나와도 「선별」의 잔여분이지 「가격」과 완전히 갈린 건 아니다"
        "(WAN-131은 볼린저 축 소관). **기본값·토대는 바꾸지 않았다**(측정 전용 · "
        "`ConfluenceParams()` 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지). 문턱/사이징 변경은 "
        "별도 재-베이스라인(사용자 결정) 이슈다."
    )
    parts = [
        f"**결론**: 강화 TF({', '.join(strengthen) or '없음'}) · "
        f"평평/역전 TF({', '.join(flat) or '없음'}) · "
        f"엣지 없음 TF({', '.join(no_edge) or '없음'}).\n"
    ]
    if strengthen and not flat and not no_edge:
        parts.append(
            "모든 작업 TF에서 **조일수록 마진이 단조 증가**한다 — 좁을수록 존폭이 더 많은 "
            "정보를 갖는다는 서명이다. 존폭은 이 저장소에서 처음으로 「좁을수록 정보 많음」을 "
            "오늘의 엔진 위에서 보인 축이다." + tail
        )
    elif flat and not strengthen:
        parts.append(
            "채택 문턱 1.28 근처에서 마진이 정점을 찍고 **더 조여도 커지지 않는다** — "
            "「좁을수록 정보 많음」은 성립하지 않는다. 1.28은 특별히 정보가 많은 지점이 아니라 "
            "WAN-159가 데이터가 아니라 판단으로 고른 값(15m 1.24·1h 1.32의 중간)임과 정합적이다."
            + tail
        )
    else:
        parts.append("TF에 갈린다 — 하나의 문턱으로 세 TF를 다 좋게 할 수 없다." + tail)
    return "\n".join(parts)


def _gate_section(
    result: SweepResult, timeframes: Sequence[str], thresholds: Sequence[float]
) -> str:
    lines = ["## §1 4관문 — 문턱마다 (1)매칭 널 (2)OOS (3)pen_5bp (4)ETH LOO\n"]
    lines.append(
        "관문 = (1) `atr` 장벽 OOS 매칭 널 p≤0.05 · (2) OOS 마진>0 · (3) `pen_5bp`에서도 "
        "p≤0.05 · (4) ETH leave-one-out. 하나라도 실패하면 그렇게 적는다.\n"
    )
    lines.append(
        "| TF | 문턱 | (1)매칭널 p(base) | (2)OOS>0 | (3)pen p | (4)ETH LOO |\n" + "| -- " * 6 + "|"
    )
    for timeframe in timeframes:
        for t in thresholds:
            g = gate_report(result, threshold=t, timeframe=timeframe)
            g1 = f"{_b(g.gate1_matched_null)} ({_p(g.p_baseline)})"
            g3 = f"{_b(g.gate3_pen_survives)} ({_p(g.p_pen)})"
            lines.append(
                f"| {timeframe} | {t:g} | {g1} | {_b(g.gate2_oos)} | {g3} | {g.gate4_eth_loo} |"
            )
    lines.append("")
    return "\n".join(lines)


def _sweep_section(
    result: SweepResult, timeframes: Sequence[str], thresholds: Sequence[float]
) -> str:
    lines = ["## §2 문턱 스윕 매칭 검정 (장벽 × 렌즈 × 구간)\n"]
    lines.append(
        "p는 단측 순위값(매칭 대조군이 필터 이상으로 벌 확률, +1 보정)이라 하한이 "
        f"1/{len(MATCH_SEEDS) + 1} ≈ {1 / (len(MATCH_SEEDS) + 1):.3f}이다. 마진 = 필터 − 매칭.\n"
    )
    lines.append(
        "| 렌즈 | 장벽 | TF | 구간 | 문턱 | 심볼 | 필터 수익 | 매칭 수익 | 마진 | p(수익) | "
        "필터 승률 | 매칭 승률 | p(승률) | 거래 잔차 |\n" + "| -- " * 14 + "|"
    )
    for lens in LENSES:
        for barrier in BARRIERS_USED:
            for timeframe in timeframes:
                for segment in (SEGMENT_IS, SEGMENT_OOS):
                    for t in thresholds:
                        row = _find_test(
                            result.test_rows,
                            threshold=t,
                            lens=lens,
                            barrier=barrier,
                            timeframe=timeframe,
                            segment=segment,
                        )
                        if row is None:
                            continue
                        if row.trade_gap_pct is None:
                            gap = "—"
                        else:
                            warn = " 🚨" if abs(row.trade_gap_pct) > 5.0 else ""
                            gap = f"{row.trade_gap_pct:+.1f}%{warn}"
                        lines.append(
                            f"| {lens} | `{barrier}` | {timeframe} | {segment} | {t:g} | "
                            f"{row.n_symbols} | {_pct(row.filter_return, signed=True)} | "
                            f"{_pct(row.matched_return_mean, signed=True)} | "
                            f"{_pct(row.margin_return, signed=True)} | {_p(row.p_return)} | "
                            f"{_pct(row.filter_win_rate)} | {_pct(row.matched_win_rate_mean)} | "
                            f"{_p(row.p_win_rate)} | {gap} |"
                        )
    lines.append("")
    return "\n".join(lines)


def _guard_section(
    result: SweepResult, timeframes: Sequence[str], thresholds: Sequence[float]
) -> str:
    lines = ["## §3 손절폭 가드 × 좁은 문턱 충돌 (WAN-79)\n"]
    lines.append(
        f"필터는 「좁은 존」을 고르는데 가드(`min_stop_distance_fraction="
        f"{STOP_GUARD_FRACTION:.1%}`)는 「짧은 손절폭」을 거절한다 — 정면 충돌이다. 문턱을 "
        "조일수록 좁은 손절이 늘어 TRX류가 20건 미만으로 무너진다(코드가 심볼평균에서 뺀다). "
        f"`{BARRIER_ATR}` 장벽은 손절 거리를 존폭에서 떼어내므로 이 충돌이 완화되는지가 함께 "
        "보인다. **가드 자체는 손대지 않는다**(WAN-76/79 소관 · 재-베이스라인).\n"
    )
    lines.append("| 장벽 | TF | 구간 | 문턱 | " + "필터 표본 붕괴 |\n" + "| -- " * 5 + "|")
    for barrier in (BARRIER_ZONE, BARRIER_ATR):
        for timeframe in timeframes:
            for segment in (SEGMENT_OOS,):
                for t in thresholds:
                    part = _partition(result.pnl_rows, threshold=t, lens=LENS_PRIMARY)
                    note = guard_note(part, barrier=barrier, timeframe=timeframe, segment=segment)
                    lines.append(f"| `{barrier}` | {timeframe} | {segment} | {t:g} | {note} |")
    lines.append("")
    return "\n".join(lines)


def _pool_section(result: SweepResult) -> str:
    lines = ["## §4 공통 풀 메모\n"]
    for note in result.pool_notes:
        lines.append(f"* {note}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 검산 — 6종목·3년 default 팔 ≡ wan152
# --------------------------------------------------------------------------- #


def run_checksum(*, db_path: str = harness.DB_PATH, jobs: int = 1) -> tuple[float, int, str]:
    """6종목·3년 baseline `default` 팔이 `wan152_barrier_pnl.csv`를 비트 재현하는지.

    문턱은 `default` 팔에 영향을 주지 않으므로(전 구간 후보) 아무 문턱이나 뽑아 비교한다.
    WAN-152 필터 팔은 **분위**(하위 1/3)라 절대 문턱과 다르므로 검산 축이 아니다 —
    threshold-무관한 `default` 팔과 공통 풀이 파이프라인 동일성의 증거다.
    """
    wan152_csv = REPORTS_DIR / "wan152_barrier_pnl.csv"
    if not wan152_csv.exists():
        return math.nan, 0, f"⚠️ {wan152_csv} 없음 — 검산 불가."
    result = run_sweep(
        symbols=CHECKSUM_SYMBOLS,
        timeframes=CHECKSUM_TIMEFRAMES,
        start=CHECKSUM_START,
        end=CHECKSUM_END,
        lenses=(LENS_PRIMARY,),
        thresholds=(THRESHOLDS_ATR[0],),
        db_path=db_path,
        jobs=jobs,
    )
    ours = {
        (r.inner.barrier, r.inner.symbol, r.inner.timeframe, r.inner.segment): r.inner
        for r in result.pnl_rows
        if r.inner.arm == ARM_DEFAULT and r.inner.seed == SEED_AGGREGATE
    }
    ref = pd.read_csv(wan152_csv)
    ref = ref[(ref["arm"] == ARM_DEFAULT) & (ref["seed"] == SEED_AGGREGATE)]
    max_diff = 0.0
    compared = 0
    for rec in ref.to_dict("records"):
        key = (rec["barrier"], rec["symbol"], rec["timeframe"], rec["segment"])
        mine = ours.get(key)
        if mine is None:
            continue
        for attr in ("total_return", "max_drawdown", "win_rate", "num_trades", "num_candidates"):
            max_diff = max(max_diff, abs(getattr(mine, attr) - float(rec[attr])))
        compared += 1
    if max_diff < 1e-9:
        verdict = "✅ 비트 재현"
    elif max_diff < 1e-6:
        verdict = "잡음(부동소수)"
    else:
        verdict = "🚨 불일치"
    return max_diff, compared, verdict


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _pnl_frame(rows: Sequence[SweepPnlRow]) -> pd.DataFrame:
    return pd.DataFrame([r.flat() for r in rows])


def _test_frame(rows: Sequence[SweepTestRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _load_from_csv(pnl_path: Path) -> SweepResult:
    frame = pd.read_csv(pnl_path)
    rows: list[SweepPnlRow] = []
    for rec in frame.to_dict("records"):
        threshold = float(rec.pop("threshold_atr"))
        lens = str(rec.pop("lens"))
        inner = PnlRow.model_validate({**rec, "lens": lens})
        rows.append(SweepPnlRow(threshold_atr=threshold, lens=lens, inner=inner))
    timeframes = tuple(dict.fromkeys(r.inner.timeframe for r in rows))
    tests = _build_tests(rows, timeframes=timeframes)
    return SweepResult(pnl_rows=rows, test_rows=tests)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-203 좁은 존 선별 심화 문턱 스윕")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    _default_thresholds = ",".join(f"{t:g}" for t in THRESHOLDS_ATR)
    parser.add_argument("--thresholds", type=str, default=_default_thresholds)
    parser.add_argument("--lenses", type=str, default=",".join(LENSES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 병렬 워커 수. 0=auto.")
    parser.add_argument("--pnl-out", type=Path, default=REPORTS_DIR / "wan203_sweep_pnl.csv")
    parser.add_argument("--test-out", type=Path, default=REPORTS_DIR / "wan203_sweep_test.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan203_summary.md")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성.")
    parser.add_argument(
        "--append", action="store_true", help="기존 pnl CSV에 이어 붙인다(TF 분할 실행)."
    )
    parser.add_argument(
        "--checksum", action="store_true", help="6종목·3년 default 팔이 wan152를 재현하는지 검산."
    )
    args = parser.parse_args(argv)

    if args.checksum:
        max_diff, compared, verdict = run_checksum(db_path=args.db, jobs=args.jobs)
        print(f"[wan203] 검산: {compared}셀 비교 · 최대 절대차 {max_diff:.2e} → {verdict}")
        return 0

    if args.from_csv:
        result = _load_from_csv(args.pnl_out)
        timeframes = tuple(dict.fromkeys(r.inner.timeframe for r in result.pnl_rows))
    else:
        timeframes = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())
        result = run_sweep(
            symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            timeframes=timeframes,
            thresholds=tuple(float(t) for t in args.thresholds.split(",") if t.strip()),
            lenses=tuple(s.strip() for s in args.lenses.split(",") if s.strip()),
            start=args.start,
            end=args.end,
            db_path=args.db,
            jobs=args.jobs,
        )
        frame = _pnl_frame(result.pnl_rows)
        if args.append and args.pnl_out.exists():
            frame = pd.concat([pd.read_csv(args.pnl_out), frame], ignore_index=True)
        _write_csv(frame, args.pnl_out)
        print(f"[wan203] pnl → {args.pnl_out}")
        # 이어 붙였으면 검정·요약은 **합친 표** 위에서 다시 낸다(WAN-152 패턴).
        notes = result.pool_notes
        result = _load_from_csv(args.pnl_out)
        result.pool_notes = notes
        timeframes = tuple(dict.fromkeys(r.inner.timeframe for r in result.pnl_rows))

    _write_csv(_test_frame(result.test_rows), args.test_out)
    print(f"[wan203] test → {args.test_out}")
    summary = build_summary_markdown(result, timeframes=timeframes)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan203] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
