"""WAN-277 — 손절 갭-체결 스트레스를 오늘의 실제 채택 북으로 확장: 재진입 ON(band) + 15m·2h.

## 배경 — WAN-276 후속

WAN-276(`backtest.wan276_stop_gap_fill`, PR #229)이 손절 갭-체결 민감도(시장가 슬리피지 α
스윕 + 지정가 미체결)를 냈지만 **재진입 OFF 북**(WAN-213 기준) · **1h·4h·both(1h+4h)** 에서만
쟀다. 그 사이 오늘의 채택 북이 두 곳에서 바뀌었다:

1. **WAN-273**(2026-08-09 Done)이 「익절 후 존 내 재진입 = ON(band 규칙)」을 채택 기본으로
   승격했다 — 재진입은 MDD를 12.17→15.59%, 최대 동시 리스크를 9.80→11.36%로 키운다. 즉
   WAN-276의 "청산 0건 · MDD 20%대"는 **재진입 OFF 값**이라 오늘 채택 북의 값이 아니다.
2. 채택 작업 TF가 **15m·1h·2h·4h**(WAN-252)인데 WAN-276은 15m·2h를 안 쟀다.

## 이 모듈이 하는 일 (측정 전용 · 옵트인 · 엔진 기본값 불변)

**WAN-276 모듈을 재사용한다**(새 파이프라인 없음) — α 사후 변환(`apply_stop_slippage`) ·
행 모델(`StressRow`/`CrashRow`) · 격자 빌드(`build_grid`) · 렌더는 전부 WAN-276 것을 그대로
쓰고, 딱 두 가지만 바꾼다:

* **재진입 ON(band)** 채택 북 위에서 잰다 — `run_cells(reentry=True,
  reentry_entry_rule="band")`로 payload에 재진입 후보(WAN-273 채택 규칙)를 싣고,
  `build_grid(include_reentry=True)`로 북에 base+재진입을 함께 넣는다. α 슬리피지는 base와
  **재진입 손절 둘 다**에 얹힌다(WAN-277에서 재진입 후보도 `exit_extreme`를 싣는다,
  `wan228_reentry_census`).
* **좌표에 15m·2h를 더한다**(`--append` 계열) — `both`는 한 실행에 모인 전 TF의 공유 지갑이라
  채택 TF 집합(15m·1h·2h·4h)을 한 번에 돌려야 완성된다.

판정 기준은 WAN-276과 동일하고, **재진입 OFF(WAN-276) 대비 MDD·청산이 얼마나 벌어지나**를
한 표로 병기한다(완료 기준 3).

## ⚠️ 경고 (WAN-276에서 그대로 옮김)

* **1분봉 경계이지 진값이 아니다** — α=1도 1분 해상도 상한, 호가 깊이는 미측정(WAN-98,
  Canceled).
* 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값 · 6년 MDD는 2018·2020-03 폭락 미포함 바닥선
  (WAN-213) · **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 위험의 모양을 재는
  것이지 알파가 아니다(WAN-90). 총수익%는 복리 착시(WAN-169/213). **채택은 재-베이스라인 =
  사용자 결정.**

## 재현

```
uv run python -m backtest.wan277_stop_gap_reentry --tf 4h --jobs 6            # 가벼운 TF부터
uv run python -m backtest.wan277_stop_gap_reentry --tf 2h --jobs 6 --append
uv run python -m backtest.wan277_stop_gap_reentry --tf 1h --jobs 6 --append
uv run python -m backtest.wan277_stop_gap_reentry --tf 15m --jobs 6 --append  # 무겁다(~37분/셀)
uv run python -m backtest.wan277_stop_gap_reentry --from-csv                  # 요약만 재생성
```

⚠️ `both`(채택 TF 집합 공유 지갑)는 전 TF가 **한 실행에 모여 있을 때만** 나온다 — 완성판은
`--tf 15m,1h,2h,4h`를 한 프로세스로 돌린다(15m 탓에 무겁다, WAN-203). 15m을 뒤로 미룬 부분
실행은 `both`가 그때까지의 TF 집합만 담는다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE
from backtest.harness import SEGMENT_FULL, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.wan169_leverage_book import run_cells
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_END as NEW_END
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_START as NEW_START
from backtest.wan176_nine_symbol_rebaseline import NINE_SYMBOLS
from backtest.wan180_leverage_book_nine import apply_funding_proxy

# WAN-276 기계 재사용 (새 파이프라인 금지).
from backtest.wan276_stop_gap_fill import (
    ALPHAS,
    CRASH_DAYS,
    NONFILL_LABEL,
    CrashRow,
    StressRow,
    _crash_table,
    _merge_crash,
    _pct,
    _resolve_scopes,
    _scenario_label,
    _stress_table,
    build_crash_rows,
    build_grid,
    crash_from_csv,
    crash_to_frame,
    grid_from_csv,
    grid_to_frame,
    verify_alpha0_identity,
)

REPORTS_DIR = Path("backtest/reports")
DEFAULT_GRID_CSV = REPORTS_DIR / "wan277_stop_gap_grid.csv"
DEFAULT_CRASH_CSV = REPORTS_DIR / "wan277_crash_days.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan277_stop_gap_summary.md"
DEFAULT_META_JSON = REPORTS_DIR / "wan277_run_meta.json"

#: WAN-276 재진입 OFF 격자 — 재진입 ON 대비 MDD·청산 델타(완료 기준 3)를 낼 때 대조로 읽는다.
WAN276_GRID_CSV = REPORTS_DIR / "wan276_stop_gap_grid.csv"

#: 재진입 규칙 = 채택(band, WAN-273). `book_cli`가 채택 값을 갖고 있어 그대로 물려받는다.
REENTRY_ENTRY_RULE = ADOPTED_REENTRY_ENTRY_RULE


# --------------------------------------------------------------------------- #
# 재진입 OFF(WAN-276) 대조
# --------------------------------------------------------------------------- #


def _off_lookup(rows: Sequence[StressRow]) -> dict[tuple[str, str, str], StressRow]:
    """(scope, segment, scenario) → WAN-276 재진입 OFF 행."""
    return {(r.scope, r.segment, r.scenario): r for r in rows}


def _reentry_delta_table(on_rows: Sequence[StressRow], off_rows: Sequence[StressRow]) -> list[str]:
    """재진입 ON vs OFF(WAN-276) — 겹치는 단일 TF 스코프에서 MDD·청산·최대 동시 리스크 델타.

    `both`은 WAN-276(1h+4h)과 WAN-277(채택 집합)이 서로 다른 지갑이라 직접 비교하지 않는다 —
    단일 TF(1h·4h)만 좌표가 같아 대조가 성립한다(15m·2h는 WAN-276에 없어 대조 불가)."""
    off = _off_lookup(off_rows)
    lines = [
        "| 스코프 | 구간 | 시나리오 | MDD OFF→ON | Δ | 청산 OFF→ON | 최대동시리스크 OFF→ON |",
        "| -- | -- | -- | --: | --: | --: | --: |",
    ]
    order = {f"alpha_{a:.2f}": i for i, a in enumerate(ALPHAS)}
    order[NONFILL_LABEL] = len(ALPHAS)
    picked = sorted(
        (
            r
            for r in on_rows
            # `both`은 WAN-276(1h+4h)과 WAN-277(15m·1h·2h·4h)이 다른 지갑이라 제외 — 단일 TF
            # (1h·4h)만 지갑 구성이 같아 ON/OFF 대조가 성립한다(15m·2h는 WAN-276에 없다).
            if r.scope != "both"
            and (r.scope, r.segment, r.scenario) in off
            and r.scenario in {"alpha_0.00", "alpha_1.00", NONFILL_LABEL}
            and r.segment in {SEGMENT_FULL, SEGMENT_OOS_WARM}
        ),
        key=lambda r: (r.scope, r.segment, order.get(r.scenario, 99)),
    )
    for r in picked:
        b = off[(r.scope, r.segment, r.scenario)]
        delta = r.max_drawdown - b.max_drawdown
        lines.append(
            f"| {r.scope} | {r.segment} | {_scenario_label(r.scenario)} | "
            f"{_pct(b.max_drawdown)}→{_pct(r.max_drawdown)} | {delta * 100:+.2f}%p | "
            f"{b.liquidation_events}→{r.liquidation_events} | "
            f"{_pct(b.max_concurrent_risk)}→{_pct(r.max_concurrent_risk)} |"
        )
    return lines


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def build_summary_markdown(
    grid_rows: Sequence[StressRow],
    crash_rows: Sequence[CrashRow],
    off_rows: Sequence[StressRow],
    *,
    grid_csv: Path,
    crash_csv: Path,
    identity_diff: float | None = None,
    funding_note: str = "",
) -> str:
    scopes = sorted({r.scope for r in grid_rows})
    identity_line = (
        f"α=0 사후 변환 북이 재진입 ON 원본 북과 **비트 일치**(최대 수익률 차 {identity_diff:.2e})."
        if identity_diff is not None
        else "α=0 사후 변환 항등은 회귀 테스트가 고정한다(이 실행에선 재검산 생략)."
    )
    lines = [
        "# WAN-277 — 손절 갭-체결 스트레스 (재진입 ON · band): 채택 북 확장 + 15m·2h 좌표",
        "",
        "**성격** 측정 + 옵트인 엔진(손절 슬리피지·미체결) 전용 — **기본값·토대·손절폭 가드·"
        "`LeverageBookParams()` 불변**(`ALPHABLOCK_LIVE_TRADING=false` 유지). 채택 북(cap_only "
        f"5배, WAN-213) · **재진입 ON(band, WAN-273)** · 못 박은 창 **{NEW_START} ~ {NEW_END}**"
        "(WAN-175/176 6년) · 9종목 · 렌즈 `baseline` 단독.",
        "",
        "WAN-276(재진입 OFF · 1h·4h)의 후속이다 — 같은 모듈을 재사용하되 (1) **재진입 ON(band)** "
        "채택 북 위에서 잰다(base·재진입 손절 둘 다에 α가 얹힌다), (2) 좌표에 **15m·2h**를 더하고 "
        "`both`를 채택 TF 집합에 맞춘다.",
        "",
        "**팔 1** 시장가 손절 슬리피지 `exit = active_stop − α·(active_stop − 봉저가)`, "
        "α ∈ {0, 0.25, 0.5, 1.0}(0 = 현행 · 1 = 봉 저가 = 1분 해상도 최악). **팔 2** 지정가 손절 "
        "미체결(갭 관통 봉은 미체결 → 손절가 복귀 봉에서 체결 · 안 돌아오면 데이터종료 홀드).",
        "",
        *([f"📌 {funding_note}", ""] if funding_note else []),
        "재현: `uv run python -m backtest.wan277_stop_gap_reentry --tf 4h --jobs 6` "
        "(가벼운 TF부터, 15m은 `--append`; 요약만: `--from-csv`). 원자료: "
        f"`{grid_csv}`(격자) · `{crash_csv}`(급락일).",
        "",
        "## 0. 검산",
        "",
        f"- {identity_line}",
        "- α=0 재진입 ON 북이 현행(WAN-273 채택 북)과 **비트 재현**되고(회귀 테스트 "
        "`test_reentry_on_alpha_zero_bit_identical`), 재진입 후보가 실제로 북에 들어가는지 "
        "**동작으로 고정**된다(`test_reentry_candidates_enter_book`).",
        "- 재진입 손절 후보가 `exit_extreme`를 실어 base와 **같은 α 슬리피지**를 받는지 "
        "고정된다(`test_reentry_stop_gets_slippage`).",
        "",
    ]
    for scope in scopes:
        lines += [f"## 1. 채택 북(재진입 ON) 스트레스 — {scope}", ""]
        for seg, title in (
            (SEGMENT_FULL, "full (6년 MDD가 사는 구간)"),
            (SEGMENT_OOS_WARM, "oos_warm (주)"),
            (SEGMENT_OOS, "oos (차가운 스트레스)"),
        ):
            lines += [f"### {title}", "", *_stress_table(grid_rows, scope, seg), ""]
    lines += [
        "🚨 **총수익%는 수백~수천 거래의 복리 값이다 — 달성 가능 성과로 인용 금지**(WAN-169/213). "
        "결정에 실질적인 열은 **청산 트리거 · MDD · 최대 동시 리스크**다. **6년 MDD는 창이 "
        "2020-09 시작이라 2018·2020-03 폭락을 안 담은 바닥선**이다(WAN-213).",
        "",
    ]
    if off_rows:
        lines += [
            "## 2. 재진입 ON vs OFF(WAN-276) — 겹치는 단일 TF에서 MDD·청산 델타",
            "",
            *_reentry_delta_table(grid_rows, off_rows),
            "",
            "재진입은 익절 후 같은 존에 다시 지정가를 걸어 노출을 늘리므로 MDD·최대 동시 리스크가 "
            "커진다(WAN-273 기록: MDD 12.17→15.59% · 최대 동시 리스크 9.80→11.36%). "
            "`both`·15m·2h는 WAN-276에 대응 좌표가 없어(다른 지갑/미측정) 이 표에서 뺐다 — "
            "§1이 절대값을 낸다.",
            "",
        ]
    lines += [
        "## 3. 대표 급락일 손절 실현손실 — α에 따라 벌어지는 폭",
        "",
        *_crash_table(crash_rows),
        "",
        "대표 급락일: " + " · ".join(desc for _, desc in CRASH_DAYS) + ". ⚠️ **대표이지 완전한 "
        "목록이 아니다** — 격리(단일 포지션) full 구간 **base** 손절 거래 중 그 창에 청산된 것만 "
        "모은 거래당 실현수익률이다(사이징·복리 무관, α 민감도만 본다). 손절 갭-체결의 α 민감도는 "
        "재진입 유무와 무관한 기하라(손절은 손절이다) WAN-276과 같은 base-격리 표를 쓴다.",
        "",
        "## 판정",
        "",
        *_verdict(grid_rows, off_rows),
        "",
        "⚠️ **1분봉 경계이지 진값이 아니다** — α=1도 1분 해상도 상한이고 호가 깊이는 1분봉이 "
        "과소평가한다. 진값은 틱·호가(WAN-98, Canceled) 소관. **진입 쪽 낙관(WAN-96)은 별개 축** — "
        "이 표는 청산만 잰다, 전부 `baseline` 위 값. **「엣지 없음」(WAN-84/88/111/114/124/151/201/"
        "248) 불변** — 위험의 모양을 재는 것이지 알파가 아니다(WAN-90). **채택은 재-베이스라인 = "
        "사용자 결정.**",
        "",
    ]
    return "\n".join(lines) + "\n"


def _verdict(rows: Sequence[StressRow], off_rows: Sequence[StressRow]) -> list[str]:
    """판정 — 청산이 나는가 · MDD가 α=1/미체결에서 얼마나 깊어지나 · 재진입 OFF 대비."""
    lines: list[str] = []
    liq = [r for r in rows if r.liquidation_events > 0]
    if liq:
        worst = max(liq, key=lambda r: r.liquidation_events)
        lines.append(
            f"🚨 **청산이 발생한다** — {len(liq)}개 (시나리오×스코프×구간) 셀에서 청산 트리거가 "
            f"났다(최대 {worst.liquidation_events}건 @ {worst.scenario}·{worst.scope}·"
            f"{worst.segment}). 재진입 ON 5배 북의 청산 0건은 손절 체결 가정에 기대고 있었다."
        )
    else:
        lines.append(
            "📌 **청산은 전 시나리오에서 0건이다** — 재진입 ON(band) 채택 북에서도 α=1(봉 저가 "
            "최악 체결)·팔 2(지정가 미체결)에서 청산 트리거가 나지 않았다(1분봉 경계 안에서). "
            "⚠️ 「5배가 안전」이 아니라 「1분봉 해상도의 갭 슬리피지가 이 사이즈(1% 리스크·명목 "
            "상한)를 청산 사거리로 밀지 못한다」는 뜻이다 — 호가 깊이(WAN-98)는 여전히 미측정."
        )

    def _find(scope: str, segment: str, scenario: str) -> StressRow | None:
        for r in rows:
            if r.scope == scope and r.segment == segment and r.scenario == scenario:
                return r
        return None

    scopes = sorted({r.scope for r in rows})
    for scope in scopes:
        base = _find(scope, SEGMENT_FULL, "alpha_0.00")
        a1 = _find(scope, SEGMENT_FULL, "alpha_1.00")
        nf = _find(scope, SEGMENT_FULL, NONFILL_LABEL)
        if base is None:
            continue
        parts = [f"α=0 {_pct(base.max_drawdown)}"]
        if a1 is not None:
            parts.append(f"α=1 {_pct(a1.max_drawdown)}")
        if nf is not None:
            parts.append(f"미체결 {_pct(nf.max_drawdown)}")
        lines.append(f"**MDD 심화({scope}·full)**: " + " → ".join(parts) + ".")

    # 재진입 OFF(WAN-276) 대비 — 완료 기준 3.
    off = _off_lookup(off_rows)
    deltas: list[float] = []
    for scope in ("1h", "4h"):
        on_r = _find(scope, SEGMENT_FULL, "alpha_1.00")
        off_r = off.get((scope, SEGMENT_FULL, "alpha_1.00"))
        if on_r is not None and off_r is not None:
            deltas.append(on_r.max_drawdown - off_r.max_drawdown)
    if deltas:
        avg = sum(deltas) / len(deltas)
        lines.append(
            f"**재진입 OFF(WAN-276) 대비(α=1·full·단일 TF)**: MDD가 평균 {avg * 100:+.2f}%p "
            "움직인다(재진입은 익절 후 재노출이라 방향은 통상 심화, WAN-273). "
            "절대값·전 좌표는 §1·§2."
        )

    def _diverges(r: StressRow, b: StressRow | None) -> bool:
        return b is not None and (
            r.total_return != b.total_return or r.max_drawdown != b.max_drawdown
        )

    nonfill = [r for r in rows if r.scenario == NONFILL_LABEL]
    diverged = [r for r in nonfill if _diverges(r, _find(r.scope, r.segment, "alpha_0.00"))]
    if nonfill and not diverged:
        lines.append(
            "📌 **팔 2(지정가 미체결)는 전 셀에서 α=0과 완전히 같다** — 1분 서브스텝 해상도에서 "
            "손절 봉이 손절가 **전체를 갭 관통**하는 일이 사실상 없다. 재진입 손절도 같은 1분봉 "
            "체결이라 갈리지 않는다 — 진짜 갭(1분 미만·거래정지)은 틱·호가(WAN-98) 소관."
        )
    elif diverged:
        lines.append(
            f"📌 **팔 2(지정가 미체결)가 {len(diverged)}개 셀에서 α=0과 갈린다** — 갭 관통 봉이 "
            "존재해 손절가 복귀 대기/홀드가 손익을 바꾼다(원자료 참조)."
        )
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_off_rows() -> list[StressRow]:
    """WAN-276 재진입 OFF 격자를 대조로 읽는다(없으면 빈 목록 → 대조 표 생략)."""
    if WAN276_GRID_CSV.exists():
        return grid_from_csv(WAN276_GRID_CSV)
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-277 손절 갭-체결 스트레스 (재진입 ON)")
    parser.add_argument("--symbols", type=str, default=",".join(NINE_SYMBOLS))
    parser.add_argument(
        "--tf", type=str, default="4h", help="쉼표 구분. 가벼운 TF부터, 15m은 --append."
    )
    parser.add_argument("--start", type=str, default=NEW_START)
    parser.add_argument("--end", type=str, default=NEW_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 칸 단위 병렬 워커 수")
    parser.add_argument("--out-grid", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--out-crash", type=Path, default=DEFAULT_CRASH_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--out-meta", type=Path, default=DEFAULT_META_JSON)
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 격자 CSV에 이어붙인다(무거운 TF를 나눠 돌릴 때).",
    )
    parser.add_argument(
        "--from-csv", action="store_true", help="백테스트를 다시 돌리지 않고 CSV에서 요약만 재생성."
    )
    parser.add_argument(
        "--no-funding-proxy", action="store_true", help="신규 종목 펀딩 대리를 끈다."
    )
    parser.add_argument("--skip-verify", action="store_true", help="α=0 항등 재검산을 생략(속도).")
    args = parser.parse_args(argv)

    out_grid = Path(args.out_grid)
    out_crash = Path(args.out_crash)
    out_md = Path(args.out_md)
    out_meta = Path(args.out_meta)
    off_rows = _load_off_rows()

    if args.from_csv:
        grid_rows = grid_from_csv(out_grid)
        crash_rows = crash_from_csv(out_crash) if out_crash.exists() else []
        funding_note = ""
        identity_diff = None
        if out_meta.exists():
            meta = json.loads(out_meta.read_text(encoding="utf-8"))
            funding_note = str(meta.get("funding_note", ""))
            identity_diff = meta.get("identity_diff")
        print(
            f"[wan277] CSV 로드 — 격자 {len(grid_rows)}행 · "
            f"급락일 {len(crash_rows)}행 (재실행 없음)"
        )
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        # 재진입 ON(band) 채택 북 — base·nonfill 둘 다 재진입 후보를 싣는다(WAN-273).
        base_payloads = run_cells(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            reentry=True,
            reentry_entry_rule=REENTRY_ENTRY_RULE,
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
            max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
        )
        nonfill_payloads = run_cells(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            reentry=True,
            reentry_entry_rule=REENTRY_ENTRY_RULE,
            limit_stop_nonfill=True,
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
            max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
        )
        funding_note = ""
        if not args.no_funding_proxy:
            base_payloads, funding_note = apply_funding_proxy(base_payloads)
            nonfill_payloads, _ = apply_funding_proxy(nonfill_payloads)
            if funding_note:
                print(f"[wan277] {funding_note}")

        existing_rows: list[StressRow] = []
        existing_crash: list[CrashRow] = []
        if args.append and out_grid.exists():
            existing_rows = grid_from_csv(out_grid)
            existing_crash = crash_from_csv(out_crash) if out_crash.exists() else []
        existing_scopes = {r.scope for r in existing_rows}
        scopes = _resolve_scopes(timeframes, sorted(existing_scopes))
        scopes = [s for s in scopes if s != "both" or len(timeframes) >= 2]

        identity_diff = (
            None
            if args.skip_verify
            else verify_alpha0_identity(base_payloads, scopes, include_reentry=True)
        )
        grid_rows = [
            *existing_rows,
            *build_grid(base_payloads, nonfill_payloads, scopes, include_reentry=True),
        ]
        new_crash = build_crash_rows(base_payloads, nonfill_payloads)
        crash_rows = _merge_crash(existing_crash, new_crash) if args.append else new_crash

        out_grid.parent.mkdir(parents=True, exist_ok=True)
        grid_to_frame(grid_rows).to_csv(out_grid, index=False)
        crash_to_frame(crash_rows).to_csv(out_crash, index=False)
        out_meta.write_text(
            json.dumps(
                {"funding_note": funding_note, "identity_diff": identity_diff},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[wan277] 격자 {len(grid_rows)}행 → {out_grid}")
        print(f"[wan277] 급락일 {len(crash_rows)}행 → {out_crash}")
        if identity_diff is not None:
            print(f"[wan277] α=0 항등 검산: 최대 수익률 차 {identity_diff:.2e}")
            if identity_diff >= 1e-9:
                print("[wan277] 🚨 검산 실패 — α=0 사후 변환이 재진입 ON 북과 갈라졌습니다.")
                return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            grid_rows,
            crash_rows,
            off_rows,
            grid_csv=out_grid,
            crash_csv=out_crash,
            identity_diff=identity_diff,
            funding_note=funding_note,
        ),
        encoding="utf-8",
    )
    print(f"[wan277] summary → {out_md}")
    return 0


def _rebuild_summary() -> pd.DataFrame:  # pragma: no cover - 편의 훅
    """대화형 편의: 현재 CSV로 요약만 다시 만든다."""
    main(["--from-csv"])
    return grid_to_frame(grid_from_csv(DEFAULT_GRID_CSV))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
