"""WAN-304 — 종목 수 사다리 재측정: 재진입 켠 채택 북(band) × 4TF(15m·1h·2h·4h).

## 왜 다시 재나 (사용자 발견 + 지시 2026-08-14)

WAN-300/303 종목 수 사다리(9→12→15→19)는 **재진입 꺼진 북**으로 측정됐는데, 채택 북은
WAN-273(2026-08-09)부터 **재진입 켜짐(band)이 기본**이다 — 사다리가 잰 북과 실제 굴리는
북이 다르다. 재진입은 MDD를 키우므로(WAN-273 채택 좌표 실측 12.17%→15.59% — ⚠️ 다른
좌표라 크기 이식 금지) 꺼짐 판의 MDD 사다리는 채택 북 기준 **과소평가**다. 사용자 지시:
*"일단 그러면 다시 돌려야지 종목수 늘리는거."*

추가로(사용자 지적 「2시간도 재야 하는 거 아닌가」) 채택 작업 TF는 WAN-252부터
**15m·1h·2h·4h 네 축**인데 꺼짐 판 사다리에는 2h가 없다 — 이 이슈가 **두 팔 다 4TF**로
낸다(꺼짐 팔의 15m·1h·4h 행은 wan300 CSV 비트 재현 검산, 2h 행은 신규. `all` 스코프는
4TF 구성이라 wan300의 3TF `all`과 **직접 비교 금지**).

## 기계 재사용 (새 파이프라인 금지)

* **셀 = wan169 `run_cells`**(WAN-300과 같은 노브: `cold_segments=False` ·
  `engine_check`는 기준 렌즈만 · ADV 상한 끔) + **`reentry=True,
  reentry_entry_rule="band"`**(WAN-269/272 기계 · 채택 규칙 = WAN-273). base 후보·격리
  성과는 재진입 생성과 무관해(별도 dict) **한 payload 세트가 두 팔을 다 낸다** — off는
  `include_reentry=False`, band는 `True`(wan300 `build_rows_for_cells`의 WAN-304 옵트인).
* **북 = wan300 조립 그대로**(`LeverageBookParams(5, cap_only)` 명시 핀 · 유니버스는
  같은 셀 payload의 부분집합 선택 · oos_warm 정본 + full · 렌즈 baseline+pen_5bp).
* **검산(완료기준 2)** — off 팔의 15m·1h·4h 스코프 행이 `wan300_universe_grid.csv`를,
  base 셀 행이 `wan300_universe_cells.csv`를 **비트 재현**해야 한다(움직인 축이 재진입
  하나임을 증명 — 자동 대조·불일치는 RuntimeError). 2h 행·4TF `all` 행은 신규라 대조
  대상이 아니다.

## 재현

```
uv run python -m backtest.wan304_universe_reentry --jobs 4                  # 4TF × 2렌즈 (무거움)
uv run python -m backtest.wan304_universe_reentry --fill baseline --jobs 4  # 렌즈 하나만 먼저
uv run python -m backtest.wan304_universe_reentry --fill pen_5bp --append --jobs 4
uv run python -m backtest.wan304_universe_reentry --from-csv                # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE
from backtest.harness import SEGMENT_FULL, SEGMENT_OOS_WARM
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import CellRow, _short, run_cells, verify_cells
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_END, DEFAULT_START
from backtest.wan300_universe_size import (
    ALL_SYMBOLS,
    DEFAULT_LENSES,
    MEASURED_SEGMENTS,
    REF_LENS,
    UNIVERSE_SIZES,
    UniverseCellRow,
    UniverseRow,
    _loo_table,
    apply_funding_proxy_any,
    build_rows_for_cells,
    cells_from_csv,
    cells_to_frame,
    cross_check_wan180,
    find_row,
    rank_candidates_by_adv,
    universe_symbols,
    verdict,
)
from backtest.wan300_universe_size import (
    DEFAULT_CELLS_CSV as WAN300_CELLS_CSV,
)
from backtest.wan300_universe_size import (
    DEFAULT_GRID_CSV as WAN300_GRID_CSV,
)
from backtest.wan300_universe_size import (
    merge_cells as _merge_cells_wan300,
)
from data.storage import OhlcvStore

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan304_universe_reentry_cells.csv"
DEFAULT_GRID_CSV = REPORTS_DIR / "wan304_universe_reentry_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan304_universe_reentry_summary.md"

#: 두 팔 — `off`(WAN-300 꺼짐 판 = base만) · `band`(채택 북 = base + 재진입 band).
ARMS: tuple[str, ...] = ("off", "band")

#: 4TF 전부(WAN-252 채택 작업 TF). 꺼짐 판(wan300)의 2h 공백도 이 실행이 채운다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "2h", "4h")

#: wan300 꺼짐 판과 비트 대조하는 스코프/TF — 2h는 그쪽에 없어 신규, `all`은 TF 구성이
#: 달라(3TF vs 4TF) 대조 대상이 아니다.
WAN300_CHECK_TFS: tuple[str, ...] = ("15m", "1h", "4h")

#: 부동소수 잡음 상한 — **상대** 기준(WAN-151 「일치·잡음·불일치」 3갈래의 경계).
#: 절대 기준을 쓰면 5배 북의 복리 `total_return`(수백~조 단위 분수)에서 CSV 파서의
#: 마지막 ulp(상대 ~1e-16)가 "불일치"로 둔갑한다 — 첫 실행이 실제로 그렇게 죽었다.
_FLOAT_NOISE_REL = 1e-9


class ReentryUniverseRow(UniverseRow):
    """wan300 북 행 + 팔 축(`reentry`) — `off`/`band`."""

    reentry: str


def arm_rows(rows: Sequence[ReentryUniverseRow], arm: str) -> list[ReentryUniverseRow]:
    """한 팔의 행만 고른다 — wan300 헬퍼(`find_row`/`verdict`/`_loo_table`)는 팔 축을
    모르므로 **반드시 이걸로 거른 뒤** 넘긴다(안 거르면 첫 팔 행이 조용히 잡힌다)."""
    return [r for r in rows if r.reentry == arm]


# --------------------------------------------------------------------------- #
# 렌즈 실행 — 한 payload 세트에서 두 팔
# --------------------------------------------------------------------------- #


def build_rows_for_lens(
    lens: str,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    funding_proxy: bool = True,
    sizes: Sequence[int] = UNIVERSE_SIZES,
) -> tuple[list[ReentryUniverseRow], list[UniverseCellRow], dict[str, str], list[str], list[str]]:
    """한 렌즈의 셀(재진입 후보 포함)을 돌고 off·band 두 팔의 북 행을 낸다.

    base 후보·격리 성과는 재진입 생성과 무관하므로(wan169 규약 · 회귀 테스트 고정)
    **payload 세트는 렌즈당 하나**다 — off 팔은 그 세트에서 `include_reentry=False`로
    조립돼 wan300 꺼짐 판과 비트 동일해야 한다(자동 대조는 `cross_check_wan300_*`).
    """
    fill = None if lens == REF_LENS else harness.fill_preset(lens)
    engine_check = lens == REF_LENS
    cells = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        fill=fill,
        reentry=True,
        reentry_entry_rule=ADOPTED_REENTRY_ENTRY_RULE,
        cold_segments=False,
        engine_check=engine_check,
        invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
    )
    notes: dict[str, str] = {}
    gapped = sorted({_short(c.symbol) for c in cells if not c.funding[SEGMENT_FULL]})
    if funding_proxy:
        cells, note = apply_funding_proxy_any(cells)
        if note:
            # `_reprice_with_funding`은 dataclasses.replace라 reentry_candidates를 보존한다.
            # 단 대체 칸의 재진입 후보는 원본(공백) 펀딩으로 생성된 것 — WAN-292 백필 후
            # 공백이 없어야 정상이므로 발동 자체를 요약에 그대로 적는다.
            notes[lens] = note
    cell_rows = [UniverseCellRow.from_cell_row(row, lens) for c in cells for row in c.rows]
    verify_lines: list[str] = []
    if engine_check:
        line, worst = verify_cells([row for c in cells for row in c.rows])
        print(f"[wan304] 검산({lens}): {line}", flush=True)
        if "🚨" in line:
            raise RuntimeError(f"엔진 검산 실패(worst={worst}): {line}")
        verify_lines.append(f"`{lens}` 렌즈: {line}")
        verify_lines.extend(cross_check_wan180(cell_rows))
    rows: list[ReentryUniverseRow] = []
    for arm in ARMS:
        base_rows = build_rows_for_cells(
            cells, lens=lens, sizes=sizes, include_reentry=(arm == "band")
        )
        rows.extend(ReentryUniverseRow(reentry=arm, **r.model_dump()) for r in base_rows)
        print(f"[wan304] {lens} · {arm}: 북 {len(base_rows)}행", flush=True)
    return rows, cell_rows, notes, gapped, verify_lines


# --------------------------------------------------------------------------- #
# wan300 꺼짐 판 비트 대조 (완료기준 2)
# --------------------------------------------------------------------------- #


def _compare_numbers(ref: dict[str, object], new: dict[str, object], fields: Sequence[str]) -> str:
    """공유 필드를 대조해 `bit`(전부 동일) / `noise`(부동소수 끝자리) / 불일치 필드명을 낸다."""
    worst = "bit"
    for field in fields:
        a, b = ref[field], new[field]
        if isinstance(b, int) and not isinstance(b, bool):
            if int(str(a)) != b:
                return field
            continue
        fa, fb = float(str(a)), float(b)  # type: ignore[arg-type]
        diff = abs(fa - fb)
        if diff == 0.0:
            continue
        if diff <= _FLOAT_NOISE_REL * max(abs(fa), abs(fb), 1.0):
            worst = "noise"
            continue
        return field
    return worst


_CELL_FIELDS = ("num_candidates", "num_trades", "win_rate", "total_return", "max_drawdown")
_GRID_FIELDS = (
    "num_cells",
    "num_trades",
    "win_rate",
    "total_return",
    "max_drawdown",
    "peak_concurrency",
    "max_concurrent_risk",
    "max_open_notional_ratio",
    "liquidation_events",
    "clamped_entries",
    "skipped_cell_busy",
    "skipped_notional",
)


def cross_check_wan300_cells(
    cell_rows: Sequence[UniverseCellRow], path: Path = WAN300_CELLS_CSV
) -> list[str]:
    """base 셀 행을 wan300 꺼짐 판 셀 CSV와 대조한다 — 재진입 생성이 base를 안 건드린 증명.

    15m·1h·4h(꺼짐 판이 가진 TF)의 (렌즈 × 심볼 × 구간)마다 다섯 지표 전부를 비교한다.
    불일치는 RuntimeError(base가 움직였다 = 배선 오류)다. 2h는 그쪽에 없어 신규다.
    """
    if not path.exists():
        return [f"⚠️ wan300 셀 CSV가 없어 대조 생략(`{path}`)."]
    ref = pd.read_csv(path)
    ref_keyed = {
        (str(r["lens"]), str(r["symbol"]), str(r["timeframe"]), str(r["segment"])): r
        for r in ref.to_dict("records")
    }
    checked = noise = 0
    for row in cell_rows:
        if row.timeframe not in WAN300_CHECK_TFS:
            continue
        key = (row.lens, row.symbol, row.timeframe, row.segment)
        if key not in ref_keyed:
            continue
        checked += 1
        outcome = _compare_numbers(dict(ref_keyed[key]), row.model_dump(), _CELL_FIELDS)
        if outcome == "noise":
            noise += 1
        elif outcome != "bit":
            raise RuntimeError(
                f"🚨 wan300 셀 대조 불일치 — {key}의 `{outcome}`가 다릅니다. "
                "재진입 생성이 base 후보를 건드렸다(배선 오류)."
            )
    if checked == 0:
        return ["⚠️ wan300 셀 CSV와 겹치는 (렌즈·심볼·TF·구간)이 없어 대조 생략."]
    tail = f"(부동소수 끝자리 {noise}행)" if noise else "(전부 비트 일치)"
    return [
        f"✅ wan300 꺼짐 판 셀 대조 — 15m·1h·4h {checked}행의 다섯 지표 전부 재현 {tail}. "
        "재진입 생성은 base 후보·격리 성과를 건드리지 않았다."
    ]


def cross_check_wan300_grid(
    rows: Sequence[ReentryUniverseRow], path: Path = WAN300_GRID_CSV
) -> list[str]:
    """off 팔의 단일 TF 스코프 행을 wan300 꺼짐 판 북 CSV와 대조한다(완료기준 2).

    `include_reentry=False`(off)로 조립한 북이 꺼짐 판과 **비트 재현**돼야 움직인 축이
    재진입 하나임이 증명된다. `all` 스코프는 TF 구성이 달라(3TF vs 4TF) 대조하지 않는다.
    """
    if not path.exists():
        return [f"⚠️ wan300 북 CSV가 없어 대조 생략(`{path}`)."]
    ref = pd.read_csv(path, keep_default_na=False)
    ref_keyed = {
        (
            str(r["lens"]),
            int(r["universe"]),
            str(r["scope"]),
            str(r["segment"]),
            str(r["exclude_symbol"]),
        ): r
        for r in ref.to_dict("records")
    }
    checked = noise = 0
    for row in arm_rows(list(rows), "off"):
        if row.scope not in WAN300_CHECK_TFS:
            continue
        key = (row.lens, row.universe, row.scope, row.segment, row.exclude_symbol)
        if key not in ref_keyed:
            continue
        checked += 1
        outcome = _compare_numbers(dict(ref_keyed[key]), row.model_dump(), _GRID_FIELDS)
        if outcome == "noise":
            noise += 1
        elif outcome != "bit":
            raise RuntimeError(
                f"🚨 wan300 북 대조 불일치 — off 팔 {key}의 `{outcome}`가 다릅니다. "
                "off 팔이 꺼짐 판을 재현하지 못했다(움직인 축이 재진입만이 아니다)."
            )
    if checked == 0:
        return ["⚠️ wan300 북 CSV와 겹치는 off 팔 행이 없어 대조 생략."]
    tail = f"(부동소수 끝자리 {noise}행)" if noise else "(전부 비트 일치)"
    return [
        f"✅ wan300 꺼짐 판 북 대조(완료기준 2) — off 팔 15m·1h·4h 스코프 {checked}행 "
        f"열두 지표 전부 재현 {tail}. **움직인 축은 재진입 하나다.**"
    ]


# --------------------------------------------------------------------------- #
# CSV 왕복 · 병합
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[ReentryUniverseRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [r.model_dump() for r in rows], columns=list(ReentryUniverseRow.model_fields)
    )


def grid_from_csv(path: Path) -> list[ReentryUniverseRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [ReentryUniverseRow.model_validate(rec) for rec in frame.to_dict("records")]


def merge_grid(
    existing: Sequence[ReentryUniverseRow], new: Sequence[ReentryUniverseRow]
) -> list[ReentryUniverseRow]:
    """--append: (팔, 렌즈, 유니버스, 스코프, 구간, 제외) 키로 덮어쓴다(wan300 규약 + 팔 축)."""
    keyed = {
        (r.reentry, r.lens, r.universe, r.scope, r.segment, r.exclude_symbol): r for r in existing
    }
    for r in new:
        keyed[(r.reentry, r.lens, r.universe, r.scope, r.segment, r.exclude_symbol)] = r
    return [keyed[k] for k in sorted(keyed, key=str)]


merge_cells = _merge_cells_wan300


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _fmt_rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _fmt_pp(value: float) -> str:
    return f"{value * 100:+.2f}%p"


def _present_scopes(rows: Sequence[ReentryUniverseRow]) -> list[str]:
    seen = {r.scope for r in rows}
    tf = sorted((s for s in seen if s != "all"), key=timeframe_to_ms)
    return (["all"] if "all" in seen else []) + tf


def _arm_ladder_table(
    rows: Sequence[ReentryUniverseRow], lens: str, scope: str, segment: str
) -> str:
    """한 (렌즈 × 스코프 × 구간)의 off·band 사다리를 한 표에 병기한다(ΔMDD 포함)."""
    lines = [
        "| 종목 수 | 거래 off→band | 총수익 off | band | MDD off | band | **ΔMDD** | "
        "수익/MDD off | band | 밀림율 off | band | 청산 off→band |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    off_rows = arm_rows(list(rows), "off")
    band_rows = arm_rows(list(rows), "band")
    for size in sorted({r.universe for r in rows}):
        off = find_row(off_rows, lens, size, scope, segment)
        band = find_row(band_rows, lens, size, scope, segment)
        if off is None or band is None:
            continue
        lines.append(
            f"| **{size}** | {off.num_trades}→{band.num_trades} | "
            f"{_fmt_pct(off.total_return)} | {_fmt_pct(band.total_return)} | "
            f"{_fmt_pct(off.max_drawdown)} | **{_fmt_pct(band.max_drawdown)}** | "
            f"**{_fmt_pp(band.max_drawdown - off.max_drawdown)}** | "
            f"{_fmt_rr(off.return_over_mdd)} | {_fmt_rr(band.return_over_mdd)} | "
            f"{_fmt_rate(off.skip_notional_rate)} | {_fmt_rate(band.skip_notional_rate)} | "
            f"{off.liquidation_events}→{band.liquidation_events} |"
        )
    return "\n".join(lines)


def build_summary_markdown(
    rows: Sequence[ReentryUniverseRow],
    *,
    grid_csv: Path = DEFAULT_GRID_CSV,
    cells_csv: Path = DEFAULT_CELLS_CSV,
    funding_notes: dict[str, str] | None = None,
    gapped_symbols: Sequence[str] = (),
    verify_lines: Sequence[str] = (),
    adv_table: Sequence[tuple[str, float]] = (),
) -> str:
    timeframes = [s for s in _present_scopes(rows) if s != "all"]
    band = arm_rows(list(rows), "band")
    off = arm_rows(list(rows), "off")
    parts: list[str] = []
    parts.append("# WAN-304 — 종목 수 사다리 재측정 (재진입 켠 채택 북 band · 4TF)\n")
    parts.append(
        "> WAN-300/303 사다리는 재진입 꺼진 북이었는데 채택 북은 WAN-273부터 재진입(band)이\n"
        "> 기본이다 — 같은 셀·같은 좌표에서 재진입 축 하나만 켜고(off ↔ band) 유니버스\n"
        "> 9→12→15→19를 다시 잰다. 2h(WAN-252 작업 TF)도 두 팔 다 새로 채운다.\n"
        "> 측정 전용 · oos_warm 정본(WAN-166) · 렌즈 baseline+pen_5bp · cap_only 5배.\n"
    )
    scope_note = "+".join(timeframes) if timeframes else "(TF 없음)"
    parts.append(
        f"`all` 스코프의 TF 구성: **{scope_note}** — ⚠️ wan300의 `all`(15m+1h+4h)과 TF "
        "구성이 달라 **직접 비교 금지**(같은 팔 대조는 단일 TF 스코프로만).\n"
    )
    if gapped_symbols:
        parts.append(
            f"🚨 **펀딩 공백 종목**: {', '.join(gapped_symbols)} — 자기 펀딩 0행"
            "(대리 발동 여부는 아래 노트).\n"
        )
    else:
        parts.append("펀딩: 전 종목 실데이터(자기 펀딩 0행 종목 없음 — 대리 미발동).\n")
    for lens, note in sorted((funding_notes or {}).items()):
        parts.append(f"펀딩 대리 발동(`{lens}`): {note}\n")

    parts.append("## 판정 (재진입 켠 채택 북 = band 팔)\n")
    parts.append(verdict(band) + "\n")
    parts.append("### 대조 — 꺼짐 팔(off · 4TF 동일 좌표)의 판정\n")
    parts.append(verdict(off) + "\n")

    parts.append("## Δ 헤드라인 — 재진입이 사다리에 얹는 것 (기준 렌즈 · `all` 4TF)\n")
    for segment in MEASURED_SEGMENTS:
        seg_label = "oos_warm (정본)" if segment == SEGMENT_OOS_WARM else segment
        parts.append(f"### 구간 `{seg_label}`\n")
        parts.append(_arm_ladder_table(rows, REF_LENS, "all", segment) + "\n")

    lenses = [lens for lens in DEFAULT_LENSES if any(r.lens == lens for r in rows)]
    smallest = min({r.universe for r in rows}, default=0)
    for segment in MEASURED_SEGMENTS:
        seg_label = "oos_warm (정본)" if segment == SEGMENT_OOS_WARM else segment
        parts.append(f"## 구간 `{seg_label}` — 스코프별 사다리 (off ↔ band)\n")
        for lens in lenses:
            for scope in _present_scopes(rows):
                if scope == "all" and lens == REF_LENS:
                    continue  # Δ 헤드라인이 이미 실었다.
                if find_row(arm_rows(list(rows), "band"), lens, smallest, scope, segment) is None:
                    continue
                parts.append(f"### 렌즈 `{lens}` · 스코프 `{scope}`\n")
                parts.append(_arm_ladder_table(rows, lens, scope, segment) + "\n")

    parts.append("## leave-one-out (band 팔 · 기준 렌즈 · all 스코프 · oos_warm)\n")
    parts.append(_loo_table(band, SEGMENT_OOS_WARM) + "\n")
    parts.append("### leave-one-out (off 팔 · 동일 좌표 대조)\n")
    parts.append(_loo_table(off, SEGMENT_OOS_WARM) + "\n")

    if adv_table:
        parts.append("## 후보 ADV (합류 순서의 자 — wan300 동결 상수의 재계산)\n")
        parts.append("| 순위 | 종목 | ADV(백만$) |")
        parts.append("| --- | --- | --- |")
        for i, (sym, adv) in enumerate(adv_table, start=1):
            parts.append(f"| {i} | {sym} | {adv / 1e6:,.1f} |")
        parts.append("")

    parts.append("## ⚠️ 경고 (인용 시 함께 옮길 것)\n")
    parts.append(
        "- **「종목 늘리면 돈 더 번다」로 인용 금지** — WAN-111 희석 선례(3→6종목 OOS "
        "심볼평균 +19% → +3.9%). 이 표는 용량 vs 희석의 교차점 측정이지 확대 권고가 아니다.\n"
        "- `total_return` %는 5배 북 복리 착시(WAN-213) — 판단은 **MDD·밀림율·수익/MDD**로. "
        "재진입 판정의 저울도 같다(WAN-261/269: 재진입은 알파가 아니라 위험의 모양, WAN-90).\n"
        "- 전부 `baseline`(닿으면 체결) 위 값이고 pen_5bp까지가 모델 밴드 — **band 재진입은 "
        "밴드가 체결이라 큐 우선순위상 가장 안 될 체결에 기댄다**(WAN-272). 신규 후보일수록 "
        "유동성이 얇아 낙관이 더 크다(합류 순서가 ADV 순인 이유). 틱·호가는 WAN-98(Canceled) "
        "소관.\n"
        "- **6년 MDD는 천장이 아니라 바닥선** — 창이 2020-09 시작이라 2018·2020-03 폭락 "
        "미포함.\n"
        "- **「엣지 없음」(WAN-84/88/111/114/124/151/201) 불변** — 유니버스 크기도 재진입도 "
        "알파가 아니라 용량·위험의 모양을 바꾼다.\n"
        "- 측정 전용 — 유니버스 확대 채택은 **재-베이스라인 = 사용자 결정**(개발자 임의 착수 "
        "금지). `DEFAULT_SYMBOLS`·`ConfluenceParams()`·`LeverageBookParams()` 불변 · 실거래 "
        "보류(`ALPHABLOCK_LIVE_TRADING=false`) 유지.\n"
    )

    parts.append("## 검산\n")
    if verify_lines:
        for line in verify_lines:
            parts.append(f"- {line}")
        parts.append("")
    else:
        parts.append("- 검산 문장이 없다 — 셀 CSV가 없어 재계산하지 못했다.\n")

    parts.append("## 재현\n")
    parts.append(
        "```\n"
        "uv run python -m backtest.wan304_universe_reentry --jobs 4\n"
        "uv run python -m backtest.wan304_universe_reentry --fill baseline --jobs 4\n"
        "uv run python -m backtest.wan304_universe_reentry --fill pen_5bp --append --jobs 4\n"
        "uv run python -m backtest.wan304_universe_reentry --from-csv\n"
        "```\n"
        f"원자료: `{grid_csv}` · `{cells_csv}`. cap_only 5배 · 재진입 규칙 "
        f"`{ADOPTED_REENTRY_ENTRY_RULE}`(WAN-273 채택) · 창 못 박음"
        f"({DEFAULT_START}~{DEFAULT_END}).\n"
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-304 종목 수 사다리 재측정(재진입 band)")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--fill", type=str, default=",".join(DEFAULT_LENSES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--jobs",
        type=int,
        default=harness.default_jobs(),
        help="(심볼, TF) 단위 병렬 워커 수(미지정이면 ALPHABLOCK_BACKTEST_JOBS, WAN-294)",
    )
    parser.add_argument("--no-funding-proxy", action="store_true", help="펀딩 대리 끔.")
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument("--append", action="store_true", help="새 렌즈/TF를 기존 CSV에 병합.")
    parser.add_argument("--grid-csv", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--cells-csv", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    grid_csv: Path = args.grid_csv
    cells_csv: Path = args.cells_csv
    out_md: Path = args.summary
    funding_notes: dict[str, str] = {}
    gapped: list[str] = []
    verify_lines: list[str] = []

    if args.from_csv:
        rows = grid_from_csv(grid_csv)
        cell_rows = cells_from_csv(cells_csv) if cells_csv.exists() else []
        print(f"[wan304] CSV 로드 — 북 {len(rows)}행 · 셀 {len(cell_rows)}행 (재실행 없음)")
        if cell_rows:
            # 요약 자가 치유(wan291 관행) — 셀 CSV가 재료를 다 갖고 있어 검산을 다시 낸다.
            line, worst = verify_cells(
                [
                    CellRow(**{k: v for k, v in r.model_dump().items() if k != "lens"})
                    for r in cell_rows
                    if r.lens == REF_LENS
                ]
            )
            if "🚨" in line:
                raise RuntimeError(f"엔진 검산 실패(worst={worst}): {line}")
            verify_lines.append(f"`{REF_LENS}` 렌즈: {line}")
            verify_lines.extend(cross_check_wan180(cell_rows))
            verify_lines.extend(cross_check_wan300_cells(cell_rows))
        verify_lines.extend(cross_check_wan300_grid(rows))
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        lenses = tuple(s.strip() for s in str(args.fill).split(",") if s.strip())
        for lens in lenses:
            if lens != REF_LENS:
                harness.fill_preset(lens)  # 지원하지 않는 렌즈는 여기서 거부(WAN-95 교훈).
        for size in UNIVERSE_SIZES:
            universe_symbols(size)  # 사다리 상수 온전성(부분 실행 전에 죽는다).
        rows = grid_from_csv(grid_csv) if args.append and grid_csv.exists() else []
        cell_rows = cells_from_csv(cells_csv) if args.append and cells_csv.exists() else []
        grid_csv.parent.mkdir(parents=True, exist_ok=True)
        for lens in lenses:
            lens_rows, lens_cells, notes, lens_gapped, lens_verify = build_rows_for_lens(
                lens,
                symbols,
                timeframes,
                start=args.start,
                end=args.end,
                jobs=args.jobs,
                funding_proxy=not args.no_funding_proxy,
            )
            funding_notes.update(notes)
            gapped = sorted(set(gapped) | set(lens_gapped))
            verify_lines.extend(lens_verify)
            rows = merge_grid(rows, lens_rows)
            cell_rows = merge_cells(cell_rows, lens_cells)
            # 렌즈마다 즉시 저장 — 긴 실행이 죽어도 끝난 렌즈는 남는다(--append로 재개).
            grid_to_frame(rows).to_csv(grid_csv, index=False)
            cells_to_frame(cell_rows).to_csv(cells_csv, index=False)
            print(f"[wan304] {lens} 저장 — 북 {len(rows)}행 · 셀 {len(cell_rows)}행", flush=True)
        verify_lines.extend(cross_check_wan300_cells(cell_rows))
        verify_lines.extend(cross_check_wan300_grid(rows))

    adv_table: list[tuple[str, float]] = []
    try:
        from config.settings import get_settings

        with OhlcvStore(get_settings().db_path) as store:
            adv_table = rank_candidates_by_adv(store, start=args.start, end=args.end)
    except Exception as exc:  # noqa: BLE001 — 요약 재생성은 DB 없이도 가능해야 한다.
        print(f"[wan304] ADV 표 생략(데이터 없음): {exc}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            rows,
            grid_csv=grid_csv,
            cells_csv=cells_csv,
            funding_notes=funding_notes,
            gapped_symbols=gapped,
            verify_lines=verify_lines,
            adv_table=adv_table,
        ),
        encoding="utf-8",
    )
    print(f"[wan304] 요약 → {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
