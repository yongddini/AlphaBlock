"""WAN-301 — 롱+숏 북 정밀 MDD·청산·최대 동시 리스크: 롱-온리 대비 (WAN-287 결정 근거).

WAN-296까지의 롱+숏 월별 표에는 **총수익 %(방향·분포용)** 뿐이라 "숏을 켜는 게 좋냐"를
판단할 수 없다 — 총수익 %는 5배 북 복리 착시(WAN-213)이고, 숏 온오프 결정의 저울은
**낙폭(MDD)·청산·동시 리스크**이기 때문이다(WAN-90 계열: 레버리지·숏은 알파가 아니라
위험의 모양을 바꾼다). WAN-296 월별 에쿼티의 월말 낙폭(롱-온리 −3.2% → 롱+숏 −11.3%,
scope=all·baseline)은 **월말 해상도라 얕다** — 이 모듈이 북 에쿼티 곡선에서 직접 잰
정밀 값을 낸다.

## 기계 재사용 (새 파이프라인 금지)

* **후보·북 = WAN-169/180 기계 그대로** — `wan169.run_cells(short_enabled=True, fill=렌즈,
  seed=시드)`(WAN-282/264/293 옵트인)로 롱+숏 후보를 낸 뒤, 롱-온리 팔은 WAN-288
  `_filter_long_only`(숏 게이트가 롱을 안 건드림이 보장하므로 `short_enabled=False`와 비트
  동일)로 걸러 컴퓨트를 절반으로 줄인다. 북 = `run_leverage_book`(cap_only 5배, WAN-180
  스냅샷 북 = 재진입 없음 — wan288/293과 같은 구성·같은 이유: 재진입은 별도 축).
* **정밀 위험 지표 = `BookOutcome.stats` + 북 에쿼티 곡선** — `max_drawdown`(거래 단위
  에쿼티 곡선)·`liquidation_events`·`max_concurrent_risk`·`peak_concurrency`를 팔·렌즈·
  스코프·구간별로 낸다(월별 접기 없음).
* **컴퓨트 노브(WAN-301 신설, 옵트인)** — `run_cells(cold_segments=False, engine_check=...)`
  로 쓰지 않는 차가운 절단(`is`/`oos`) 생성과 렌즈 반복분의 표준 경로 검산을 생략한다.
  검산은 기준(baseline) 팔만 켠다(배선은 렌즈 축과 무관 — 어느 팔이 검산됐는지 요약에 적음).

## 격자

9종목 × TF(기본 4h·1h·2h — 15m은 셀당 무거우니 `--append` 후속, WAN-293→296 패턴 —
🔁 그 후속은 WAN-302가 냈다) × 못 박은 6년 창 × 팔 2(long_only vs long_short) × 렌즈 3
(baseline·pen_5bp·pen_5bp_drop_50, 탈락 렌즈는 시드 5개 평균) × 구간 2(oos_warm 정본 +
full) × 스코프(all + TF별). ⚠️ `all`은 그 실행이 커버한 전 TF 셀의 합산 북이라 15m 단독
append로는 재계산되지 않는다(`merge_risk` 규약) — WAN-302는 4TF를 **한 실행**으로 돌려
15m 행을 잇고 `all`을 15m+1h+2h+4h(36칸)로 재산출했다(재실행된 1h·2h·4h 행은 기존 CSV와
비트 일치 — 결정적 재현 확인). 단 **`pen_5bp_drop_50`은 15m을 재지 않았다**(사용자 결정
2026-08-13: "그냥 봐도 결과 대충 예상가능") — 그 렌즈는 WAN-301 초판(`all`=1h+2h+4h ·
27칸)으로 동결이고 요약이 렌즈 간 `all` 직접 비교를 막는 경고를 찍는다.

## ⚠️ 경고 (요약에 그대로)

* **6년 MDD는 천장이 아니라 바닥선** — 창이 2020-09 시작이라 2018·2020-03 폭락 미포함.
* 전부 `baseline`(닿으면 체결) 위 값이고 pen_5bp까지가 모델 밴드 — 진짜 큐 우선순위·부분
  체결은 틱·호가(WAN-98, Canceled) 소관.
* 숏은 **엣지가 아니다**(WAN-164 (c) · 「엣지 없음」 WAN-84/88/… 불변) — 이 표는 "숏이
  북의 위험을 얼마나 키우나"이지 엣지를 뒤집는 게 아니다. 총수익 %는 복리 착시(WAN-213).
* 측정 전용 · 숏 재활성화(WAN-287)는 별도 사용자 결정 — 기본값을 켜지 않는다.

## 재현

```
# WAN-301 초판: 4h·1h·2h 3렌즈(dropout 5시드). 15m은 셀당 무거우니(WAN-203) 후속으로.
uv run python -m backtest.wan301_short_book_risk --tf 4h,1h,2h --jobs 4
# WAN-302: 15m append + `all` 재산출 — all은 전 TF 셀이 필요해 4TF를 한 실행으로 돈다.
# drop_50 렌즈는 사용자 결정(2026-08-13)으로 생략 — 그 렌즈의 15m·`all` 4TF는 미측정이다.
uv run python -m backtest.wan301_short_book_risk --tf 15m,1h,2h,4h \\
    --fill baseline,pen_5bp --append --jobs 4
uv run python -m backtest.wan301_short_book_risk --from-csv                     # 요약만
```
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_FULL, SEGMENT_OOS_WARM
from backtest.leverage_book import LeverageBookParams, run_leverage_book
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    _segment_cells,
    _short,
    run_cells,
    verify_cells,
)
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan288_monthly_long_short import (
    ADOPTED_MODE,
    ADOPTED_MULTIPLE,
    ALL_SYMBOLS,
    ARM_LONG_ONLY,
    ARM_LONG_SHORT,
    DEFAULT_END,
    DEFAULT_START,
    _filter_long_only,
)
from backtest.zone_limit_backtest import build_result_from_trades

REPORTS_DIR = Path("backtest/reports")
DEFAULT_RISK_CSV = REPORTS_DIR / "wan301_book_risk.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan301_book_risk_summary.md"

#: 렌즈 밴드(baseline → 겨냥형 → 최악). WAN-128의 「baseline 단독」은 일반 리포트 규칙이고,
#: 이 이슈는 「pen_5bp에서도 숏 낙폭 증폭이 유지되나」가 명시 질문인 옵트인 스트레스다.
DEFAULT_LENSES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")
REF_LENS = "baseline"

#: 기본 TF = 4h·1h·2h(컴퓨트 실현 가능 · WAN-301 초판 좌표 보존). 15m을 포함한 전체
#: 재산출은 `--tf 15m,1h,2h,4h --append`(WAN-302 — `all` 스코프 재계산에 4TF가 필요).
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "2h")

#: 측정 구간 — oos_warm(정본, WAN-166) + full. 차가운 절단(is/oos)은 이 표의 질문(실전
#: 낙폭 추정)에 안 쓰므로 생성조차 안 한다(`run_cells(cold_segments=False)`).
MEASURED_SEGMENTS: tuple[str, ...] = (SEGMENT_OOS_WARM, SEGMENT_FULL)

#: 잔존/배율 계산에서 0으로 나누지 않기 위한 하한(WAN-115 부호 함정 가드).
_RATIO_EPS = 1e-9


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class RiskRow(BaseModel):
    """(렌즈 × 스코프 × 팔 × 구간)의 채택 북(cap_only 5배) 정밀 위험 성과 한 행.

    `max_drawdown`은 북 에쿼티 곡선(거래 청산 단위)에서 직접 잰 값 — 월별 해상도(WAN-296)
    보다 깊거나 같다. 탈락 렌즈(`pen_5bp_drop_50`)는 `num_seeds`개 시드 평균이라 카운트
    열이 분수일 수 있다(그래서 float). ⚠️ `total_return`은 5배 북 복리 착시(WAN-213) —
    방향·`return_over_mdd` 비교용으로만 싣는다.
    """

    model_config = ConfigDict(frozen=True)

    lens: str
    scope: str
    """`all`(이 실행이 커버한 전 TF 합친 북) 또는 개별 TF."""
    arm: str
    """`long_only` · `long_short`."""
    segment: str
    """`oos_warm`(정본) · `full`."""
    num_cells: int
    num_trades: float
    win_rate: float
    total_return: float
    max_drawdown: float
    peak_concurrency: float
    max_concurrent_risk: float
    max_open_notional_ratio: float
    liquidation_events: float
    skipped_cell_busy: float
    skipped_notional: float
    num_seeds: int

    @property
    def return_over_mdd(self) -> float | None:
        """수익/MDD(위험조정 판정 지표). MDD가 0이면 정의하지 않는다."""
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown


# --------------------------------------------------------------------------- #
# 북 실행 — 한 (스코프 × 팔 × 구간)
# --------------------------------------------------------------------------- #


def _book_risk_row(
    cells: Sequence[CellPayload], *, lens: str, scope: str, arm: str, segment: str
) -> RiskRow:
    """이 팔·스코프·구간의 채택(cap_only 5배 · WAN-180 스냅샷) 북을 돌려 위험 행을 낸다."""
    # WAN-305 명시 핀: wan301 판은 재진입 꺼진 북이다(payload에도 재진입이 없어 무동작 가드).
    seg_cells = _segment_cells(list(cells), segment, "", include_reentry=False)
    base_cfg = harness.legacy_build_config(BOOK_ANNUALIZATION_TF)
    outcome = run_leverage_book(
        seg_cells,
        base_cfg,
        # WAN-213 명시 핀: 채택 값(cap_only 5배)을 클래스 기본값에 맡기지 않고 못 박는다 —
        # 기본값이 또 이동해도 이 CSV가 조용히 다른 북으로 다시 돌지 않게(WAN-91/95 부류 방지).
        LeverageBookParams(leverage_multiple=ADOPTED_MULTIPLE, leverage_mode=ADOPTED_MODE),
    )
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    m = result.metrics
    s = outcome.stats
    return RiskRow(
        lens=lens,
        scope=scope,
        arm=arm,
        segment=segment,
        num_cells=len(seg_cells),
        num_trades=float(m.num_trades),
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        peak_concurrency=float(s.peak_concurrency),
        max_concurrent_risk=s.max_concurrent_risk_ratio,
        max_open_notional_ratio=s.max_open_notional_ratio,
        liquidation_events=float(len(s.liquidations)),
        skipped_cell_busy=float(s.skipped_cell_busy),
        skipped_notional=float(s.skipped_notional),
        num_seeds=1,
    )


def build_rows_for_cells(long_short_cells: Sequence[CellPayload], *, lens: str) -> list[RiskRow]:
    """한 렌즈·시드의 롱+숏 셀에서 (스코프 × 팔 × 구간) 위험 행 전부를 낸다."""
    long_only_cells = [_filter_long_only(c) for c in long_short_cells]
    timeframes = sorted({c.timeframe for c in long_short_cells}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    rows: list[RiskRow] = []
    for scope in scopes:
        lo = (
            long_only_cells
            if scope == "all"
            else [c for c in long_only_cells if c.timeframe == scope]
        )
        ls = (
            list(long_short_cells)
            if scope == "all"
            else [c for c in long_short_cells if c.timeframe == scope]
        )
        for segment in MEASURED_SEGMENTS:
            rows.append(
                _book_risk_row(lo, lens=lens, scope=scope, arm=ARM_LONG_ONLY, segment=segment)
            )
            rows.append(
                _book_risk_row(ls, lens=lens, scope=scope, arm=ARM_LONG_SHORT, segment=segment)
            )
    return rows


# --------------------------------------------------------------------------- #
# 시드 평균 (탈락 렌즈)
# --------------------------------------------------------------------------- #

#: 시드 평균 대상 float 필드(카운트 포함 — 시드마다 다를 수 있어 평균이 분수가 된다).
_AVERAGED_FIELDS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "total_return",
    "max_drawdown",
    "peak_concurrency",
    "max_concurrent_risk",
    "max_open_notional_ratio",
    "liquidation_events",
    "skipped_cell_busy",
    "skipped_notional",
)


def _average_risk(
    per_seed: Sequence[Sequence[RiskRow]], lens: str, num_seeds: int
) -> list[RiskRow]:
    """(스코프, 팔, 구간) 키로 시드 간 float 지표를 평균한다.

    북 실행은 시드마다 같은 키 집합을 내므로(후보가 전멸해도 0-거래 행이 나온다) 키 누락이
    없고, 합을 **고정 시드 수**로 나눈다(WAN-96 관행 · wan293 `_average_*`와 같은 규약).
    `num_cells`는 시드와 무관(결정적)해 아무 시드 값이나 쓴다. `num_seeds=1`이면 원값
    보존이다(baseline 팔 불변 — 회귀 테스트가 고정).
    """
    acc: dict[tuple[str, str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    meta: dict[tuple[str, str, str], RiskRow] = {}
    for rows in per_seed:
        for r in rows:
            key = (r.scope, r.arm, r.segment)
            for field in _AVERAGED_FIELDS:
                acc[key][field] += float(getattr(r, field))
            meta[key] = r
    out: list[RiskRow] = []
    for key in sorted(acc):
        r = meta[key]
        averaged = {field: acc[key][field] / num_seeds for field in _AVERAGED_FIELDS}
        out.append(
            RiskRow(
                lens=lens,
                scope=r.scope,
                arm=r.arm,
                segment=r.segment,
                num_cells=r.num_cells,
                num_seeds=num_seeds,
                **averaged,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 격자 조립 — 렌즈 × 시드
# --------------------------------------------------------------------------- #


def build_lens_rows(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    lenses: Sequence[str] = DEFAULT_LENSES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    funding_proxy: bool = True,
) -> tuple[list[RiskRow], dict[str, str], list[str], str]:
    """각 렌즈(탈락은 시드 평균)의 위험 행 + 펀딩 대리 노트 + 펀딩 공백 종목 + 검산 문장.

    렌즈마다 `run_cells(fill=렌즈, seed=s, short_enabled=True)`로 후보를 다시 생성한다
    (렌즈는 후보 집합을 바꾼다, WAN-264). 차가운 절단(`is`/`oos`)은 이 표가 안 쓰므로
    `cold_segments=False`로 생성조차 안 하고, 표준 경로 검산(`engine_check`)은 기준
    렌즈(baseline)만 켠다 — 배선은 렌즈 축과 무관하다(WAN-301 컴퓨트 노브).

    펀딩 공백 종목(자기 펀딩 0행 = 순수익 낙관)은 셀이 실제로 실은 펀딩으로 동적 판정한다 —
    WAN-292 백필 후에는 빈 목록이어야 하고, 요약이 그 확인을 찍는다(이슈 경고의
    「funding_gap=False 확인」).
    """
    out: list[RiskRow] = []
    notes: dict[str, str] = {}
    gapped: set[str] = set()
    verify_line = ""
    for lens in lenses:
        fill = None if lens == REF_LENS else harness.fill_preset(lens)
        seeds = fill.seeds if fill is not None else (0,)
        engine_check = lens == REF_LENS
        per_seed: list[list[RiskRow]] = []
        for seed in seeds:
            cells = run_cells(
                symbols,
                timeframes,
                start=start,
                end=end,
                jobs=jobs,
                short_enabled=True,
                fill=fill,
                seed=seed,
                cold_segments=False,
                engine_check=engine_check,
                # WAN-305 명시 핀: wan301/302 CSV는 재진입 꺼진 북의 동결 기록이다.
                reentry=False,
                invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
            )
            if funding_proxy:
                cells, note = apply_funding_proxy(cells)
                if note:
                    notes[lens] = note
            if engine_check:
                # 검산(WAN-164 패턴): 격리 행 ↔ 표준 경로 비트 대조. 계산만 하고 버리면
                # 「검산했다」가 주장으로만 남는다 — 여기서 실제로 비교하고 불일치면 죽는다.
                line, worst = verify_cells([row for c in cells for row in c.rows])
                print(f"[wan301] 검산({lens}): {line}", flush=True)
                if "🚨" in line:
                    raise RuntimeError(f"엔진 검산 실패(worst={worst}): {line}")
                verify_line = f"`{lens}` 렌즈: {line}"
            gapped.update(_short(c.symbol) for c in cells if not c.funding[SEGMENT_FULL])
            rows = build_rows_for_cells(cells, lens=lens)
            per_seed.append(rows)
            print(f"[wan301] {lens} seed={seed}: 위험 {len(rows)}행", flush=True)
        out.extend(_average_risk(per_seed, lens, len(seeds)))
    return out, notes, sorted(gapped), verify_line


# --------------------------------------------------------------------------- #
# 프레임 · CSV 왕복 · append 병합
# --------------------------------------------------------------------------- #


def risk_to_frame(rows: Sequence[RiskRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(RiskRow.model_fields))


def risk_from_csv(path: Path) -> list[RiskRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [RiskRow.model_validate(rec) for rec in frame.to_dict("records")]


def merge_risk(existing: Sequence[RiskRow], new: Sequence[RiskRow]) -> list[RiskRow]:
    """--append: (렌즈, 스코프, 팔, 구간) 키로 덮어쓴다.

    ⚠️ `all` 스코프는 그 실행이 커버한 TF 집합의 집계라 단일 TF만 append하면 재계산 불가다 —
    새로 온 스코프만 덮어쓰고 기존 `all`은 남긴다(wan288/293 `merge_book`과 같은 규약 ·
    wan296이 같은 이유로 `all`을 1h+2h+4h로 유지했다). 요약이 `all`의 TF 구성을 명시한다.
    🔁 그래서 WAN-302의 `all` 4TF 재산출은 append 병합이 아니라 **4TF 한 실행**으로 했다
    (그 실행의 새 `all` 행이 여기서 옛 `all`을 키 일치로 덮어쓴다).
    """
    keyed = {(r.lens, r.scope, r.arm, r.segment): r for r in existing}
    for r in new:
        keyed[(r.lens, r.scope, r.arm, r.segment)] = r
    return [keyed[k] for k in sorted(keyed)]


# --------------------------------------------------------------------------- #
# 분석 (요약용 · 테스트 대상)
# --------------------------------------------------------------------------- #


def find_row(
    rows: Sequence[RiskRow], lens: str, scope: str, arm: str, segment: str
) -> RiskRow | None:
    for r in rows:
        if r.lens == lens and r.scope == scope and r.arm == arm and r.segment == segment:
            return r
    return None


def mdd_delta(rows: Sequence[RiskRow], lens: str, scope: str, segment: str) -> float | None:
    """숏이 낙폭을 얼마나 키우나 = (롱+숏 MDD) − (롱-온리 MDD). 한쪽이 없으면 None."""
    lo = find_row(rows, lens, scope, ARM_LONG_ONLY, segment)
    ls = find_row(rows, lens, scope, ARM_LONG_SHORT, segment)
    if lo is None or ls is None:
        return None
    return ls.max_drawdown - lo.max_drawdown


def mdd_ratio(rows: Sequence[RiskRow], lens: str, scope: str, segment: str) -> float | None:
    """롱+숏 MDD ÷ 롱-온리 MDD(배율). 분모가 0 근처면 정의하지 않는다(WAN-115 가드)."""
    lo = find_row(rows, lens, scope, ARM_LONG_ONLY, segment)
    ls = find_row(rows, lens, scope, ARM_LONG_SHORT, segment)
    if lo is None or ls is None or abs(lo.max_drawdown) < _RATIO_EPS:
        return None
    return ls.max_drawdown / lo.max_drawdown


def _present_lenses(rows: Sequence[RiskRow]) -> list[str]:
    seen = {r.lens for r in rows}
    ordered = [lens for lens in DEFAULT_LENSES if lens in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return ordered


def _present_scopes(rows: Sequence[RiskRow]) -> list[str]:
    scopes = {r.scope for r in rows}
    tf_scopes = sorted((s for s in scopes if s != "all"), key=timeframe_to_ms)
    return (["all"] if "all" in scopes else []) + tf_scopes


def _agg_scope(rows: Sequence[RiskRow]) -> str:
    scopes = _present_scopes(rows)
    return "all" if "all" in scopes else (scopes[0] if scopes else "")


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _fmt_pp(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%p"


def _fmt_ratio(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}배"


def _fmt_rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _fmt_count(value: float) -> str:
    return f"{value:g}"


def _scope_table(rows: Sequence[RiskRow], scope: str, segment: str) -> str:
    """한 스코프·구간의 렌즈 × 두 팔 위험 표 + 숏 델타 열."""
    lines = [
        "| 렌즈 | 팔 | MDD | 수익/MDD | 청산 | 최대 동시 리스크 | 최대 동시 칸 | 거래수 "
        "| Δ MDD(숏) | MDD 배율 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for lens in _present_lenses(rows):
        delta = mdd_delta(rows, lens, scope, segment)
        ratio = mdd_ratio(rows, lens, scope, segment)
        for arm in (ARM_LONG_ONLY, ARM_LONG_SHORT):
            r = find_row(rows, lens, scope, arm, segment)
            if r is None:
                continue
            delta_cell = _fmt_pp(delta) if arm == ARM_LONG_SHORT else ""
            ratio_cell = _fmt_ratio(ratio) if arm == ARM_LONG_SHORT else ""
            lines.append(
                f"| {lens} | {arm} | **{_fmt_pct(r.max_drawdown)}** | "
                f"{_fmt_rr(r.return_over_mdd)} | {_fmt_count(r.liquidation_events)} | "
                f"{_fmt_pct(r.max_concurrent_risk)} | {_fmt_count(r.peak_concurrency)} | "
                f"{_fmt_count(r.num_trades)} | {delta_cell} | {ratio_cell} |"
            )
    return "\n".join(lines)


def _delta_matrix(rows: Sequence[RiskRow], segment: str) -> str:
    """스코프 × 렌즈의 Δ MDD(롱+숏 − 롱-온리) 한눈 표."""
    lenses = _present_lenses(rows)
    header = "| 스코프 | " + " | ".join(lenses) + " |"
    sep = "| --- | " + " | ".join(["---"] * len(lenses)) + " |"
    lines = [header, sep]
    for scope in _present_scopes(rows):
        cells = [_fmt_pp(mdd_delta(rows, lens, scope, segment)) for lens in lenses]
        lines.append(f"| {scope} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _liq_total(rows: Sequence[RiskRow]) -> float:
    return sum(r.liquidation_events for r in rows)


def _verdict_line(rows: Sequence[RiskRow]) -> str:
    """숏이 낙폭·동시 리스크를 얼마나 키우고, 그것이 pen_5bp에서 유지되나(완료기준 1의 판정)."""
    agg = _agg_scope(rows)
    if not agg:
        return "**판정**: 위험 행이 없어 판정 표본이 없다."
    seg = SEGMENT_OOS_WARM
    lo = find_row(rows, REF_LENS, agg, ARM_LONG_ONLY, seg)
    ls = find_row(rows, REF_LENS, agg, ARM_LONG_SHORT, seg)
    if lo is None or ls is None:
        return "**판정**: 기준 렌즈 두 팔이 없어 판정 표본이 없다."
    ratio = mdd_ratio(rows, REF_LENS, agg, seg)
    pen_delta = mdd_delta(rows, "pen_5bp", agg, seg)
    pen_ratio = mdd_ratio(rows, "pen_5bp", agg, seg)
    liq = _liq_total(rows)
    risk_note = (
        f"최대 동시 리스크 {_fmt_pct(lo.max_concurrent_risk)} → {_fmt_pct(ls.max_concurrent_risk)}"
    )
    pen_note = (
        f" pen_5bp에서도 Δ {_fmt_pp(pen_delta)}({_fmt_ratio(pen_ratio)})로 증폭이 유지된다."
        if pen_delta is not None
        else " (pen_5bp 미측정)"
    )
    return (
        f"**판정 — 숏을 켜면(스코프 `{agg}` · oos_warm 정본)**: 북 MDD "
        f"{_fmt_pct(lo.max_drawdown)} → **{_fmt_pct(ls.max_drawdown)}**"
        f"({_fmt_ratio(ratio)}, Δ {_fmt_pp(mdd_delta(rows, REF_LENS, agg, seg))}) · {risk_note}."
        f"{pen_note} 청산은 전 렌즈·전 스코프 합산 **{_fmt_count(liq)}건**."
    )


def build_summary_markdown(
    rows: Sequence[RiskRow],
    *,
    risk_csv: Path = DEFAULT_RISK_CSV,
    funding_notes: dict[str, str] | None = None,
    gapped_symbols: Sequence[str] = (),
    engine_verify_line: str = "",
) -> str:
    tf_in_all = sorted(
        {r.scope for r in rows if r.scope != "all"},
        key=timeframe_to_ms,
    )
    parts: list[str] = []
    parts.append("# WAN-301 — 롱+숏 북 정밀 MDD·청산·최대 동시 리스크 (롱-온리 대비)\n")
    parts.append(
        "> WAN-287(숏 재-베이스라인) 결정 근거. 채택 북(cap_only 5배 · WAN-180 스냅샷 =\n"
        "> 재진입 없음)에 숏을 켠 팔과 롱-온리 팔의 **북 에쿼티 곡선 정밀 위험 지표**를 렌즈\n"
        "> 3개로 병기한다. 측정 전용 · `short_enabled`는 이 모듈 안에서만 True · oos_warm\n"
        "> 정본(WAN-166) · 탈락 렌즈는 시드 5개 평균.\n"
    )
    if gapped_symbols:
        parts.append(
            f"🚨 **펀딩 공백 종목**: {', '.join(gapped_symbols)} — 자기 펀딩 0행이라 순수익이 "
            "낙관이다(WAN-292 백필 상태 확인 필요).\n"
        )
    else:
        parts.append(
            "펀딩: 전 종목 실데이터(자기 펀딩 0행 종목 없음 — WAN-292 백필 확인, "
            "funding_gap=False).\n"
        )
    if funding_notes:
        note = next((v for v in funding_notes.values() if v), "")
        if note:
            parts.append(f"신규 3종목 펀딩 대리 발동: {note}\n")
    scope_note = "+".join(tf_in_all) if tf_in_all else "(TF 없음)"
    parts.append(
        f"`all` 스코프의 TF 구성: **{scope_note}** (단일 TF append는 `all`을 재계산하지 "
        "못한다 — wan296과 같은 규약).\n"
    )
    all_cells: dict[str, int] = {r.lens: r.num_cells for r in rows if r.scope == "all"}
    if len(set(all_cells.values())) > 1:
        detail = " · ".join(f"`{lens}` {cells}칸" for lens, cells in sorted(all_cells.items()))
        parts.append(
            f"⚠️ **`all`의 칸 수가 렌즈마다 다르다**: {detail}. 칸 수가 작은 렌즈는 15m "
            "미측정(WAN-302 사용자 결정 — `pen_5bp_drop_50` 생략)이라 그 `all`은 "
            "1h+2h+4h 북이다. **렌즈 간 `all` 직접 비교 금지.**\n"
        )

    parts.append("## 판정\n")
    parts.append(_verdict_line(rows) + "\n")

    parts.append("## Δ MDD 한눈 표 (롱+숏 − 롱-온리, oos_warm 정본)\n")
    parts.append(_delta_matrix(rows, SEGMENT_OOS_WARM) + "\n")

    for segment in MEASURED_SEGMENTS:
        seg_label = "oos_warm (정본)" if segment == SEGMENT_OOS_WARM else segment
        parts.append(f"## 구간 `{seg_label}`\n")
        for scope in _present_scopes(rows):
            parts.append(f"### 스코프 `{scope}`\n")
            parts.append(_scope_table(rows, scope, segment) + "\n")

    parts.append("## ⚠️ 경고 (인용 시 함께 옮길 것)\n")
    parts.append(
        "- **6년 MDD는 천장이 아니라 바닥선** — 못 박은 창이 2020-09 시작이라 2018·2020-03 "
        "폭락이 빠져 있다. 실제 최악은 이보다 깊다.\n"
        "- 전부 `baseline`(닿으면 체결) 위 값이고 pen_5bp까지가 **모델 밴드**다 — 진짜 큐 "
        "우선순위·부분 체결은 틱·호가(WAN-98, **Canceled**) 소관.\n"
        "- 숏은 **엣지가 아니다**(WAN-164 오늘 엔진 (c) · 「엣지 없음」 WAN-84/88/111/114/124/"
        '151/201/248 불변) — 이 표는 "숏이 북의 위험을 얼마나 키우나"이지 엣지를 뒤집는 게 '
        "아니다. `total_return` %는 5배 북 복리 착시(WAN-213)라 방향·수익/MDD 비교로만.\n"
        "- MDD는 북 에쿼티 곡선(거래 청산 단위)에서 잰 값 — 월별(WAN-296)보다 정밀하나 "
        "**봉내 미실현 낙폭은 아니다**(청산 시점 사이 장중 저점은 이 곡선에 없다).\n"
        "- 측정 전용 · 숏 재활성화(WAN-287)는 **별도 사용자 결정** — 이 표는 그 결정의 근거일 "
        "뿐 개발자가 기본값을 켜지 않는다(`ConfluenceParams().short_enabled == False` · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지).\n"
    )

    parts.append("## 검산\n")
    engine_line = (
        f"- {engine_verify_line} (표준 경로 `harness.run_once` 대조는 기준 렌즈만 — 배선은 "
        "렌즈 축과 무관, WAN-301 컴퓨트 노브 `engine_check`.)\n"
        if engine_verify_line
        else "- 표준 경로 검산 문장은 측정 실행 시에만 나온다(`--from-csv` 재생성엔 없음 — "
        "실행 로그와 아래 회귀 테스트가 정본).\n"
    )
    parts.append(
        engine_line
        + "- baseline·pen_5bp의 롱-온리/롱+숏 `all`·TF 스코프 행은 wan293 월별 북 CSV와 교차 "
        "검산된다(∏(1+월수익) ≡ total_return · Σ월 청산수 ≡ 거래수 — 회귀 테스트 "
        "`test_committed_rows_cross_check_wan293`).\n"
    )

    parts.append("## 재현\n")
    parts.append(
        "```\n"
        "uv run python -m backtest.wan301_short_book_risk --tf 4h,1h,2h --jobs 4\n"
        "# WAN-302: 15m append + all 재산출(4TF 한 실행 · drop_50 생략 = 사용자 결정)\n"
        "uv run python -m backtest.wan301_short_book_risk --tf 15m,1h,2h,4h "
        "--fill baseline,pen_5bp --append --jobs 4\n"
        "uv run python -m backtest.wan301_short_book_risk --from-csv   # 요약만 재생성\n"
        "```\n"
        f"원자료: `{risk_csv}`. cap_only={ADOPTED_MODE} 배수={ADOPTED_MULTIPLE} · "
        "창 못 박음(WAN-182) · 9종목.\n"
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-301 롱+숏 북 정밀 MDD·청산·동시 리스크")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--fill", type=str, default=",".join(DEFAULT_LENSES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--jobs",
        type=int,
        default=harness.default_jobs(),
        help="(심볼, TF) 단위 병렬 워커 수(미지정이면 ALPHABLOCK_BACKTEST_JOBS, 기본 4, WAN-294)",
    )
    parser.add_argument("--no-funding-proxy", action="store_true", help="신규 종목 펀딩 대리 끔.")
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument("--append", action="store_true", help="새 TF를 기존 CSV에 이어 붙인다.")
    parser.add_argument("--risk-csv", type=Path, default=DEFAULT_RISK_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    out_csv: Path = args.risk_csv
    out_md: Path = args.summary
    funding_notes: dict[str, str] = {}
    gapped: list[str] = []
    verify_line = ""

    if args.from_csv:
        rows = risk_from_csv(out_csv)
        print(f"[wan301] CSV 로드 — 위험 {len(rows)}행 (재실행 없음)")
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        lenses = tuple(s.strip() for s in str(args.fill).split(",") if s.strip())
        # 지원하지 않는 렌즈는 여기서 거부(조용히 무시 금지, WAN-95 교훈).
        for lens in lenses:
            if lens != REF_LENS:
                harness.fill_preset(lens)
        rows, funding_notes, gapped, verify_line = build_lens_rows(
            symbols,
            timeframes,
            lenses=lenses,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            funding_proxy=not args.no_funding_proxy,
        )
        if args.append and out_csv.exists():
            rows = merge_risk(risk_from_csv(out_csv), rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        risk_to_frame(rows).to_csv(out_csv, index=False)
        print(f"[wan301] 위험 {len(rows)}행 → {out_csv}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            rows,
            risk_csv=out_csv,
            funding_notes=funding_notes,
            gapped_symbols=gapped,
            engine_verify_line=verify_line,
        ),
        encoding="utf-8",
    )
    print(f"[wan301] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
