"""WAN-229 — 익절 후 존 내 재진입 census 15m 축 완결 ((B) §1c/§2 15m).

WAN-228(PR #187)이 4h·1h만 내고 15m을 미추진으로 남긴 것을 **완결**한다 — 판정을 바꾸는
축이 아니라 **그림 완성**(WAN-228 §3의 GO/STOP은 4h·1h로 이미 (a) GO). census 엔진(익절
후 지정가 재무장 루프·행 모델·판정·자)은 **WAN-228 모듈을 그대로 재사용**하고(핀 없음 ·
채택 좌표 `ConfluenceParams()`·`OrderBlockParams()`), **TF만 15m으로 돌린다.** 그래서 이
모듈이 내는 CSV는 `wan228_reentry_census.csv`(4h·1h)와 **열이 같아 교차검산**된다.

이 모듈이 WAN-228 CLI에 더하는 것은 단 셋이다: (1) TF 기본값 15m, (2) 산출물을 WAN-229
파일(`wan229_reentry_census_15m.{csv,md}`)로 분리해 4h·1h 원본을 **덮지 않음**, (3) 요약을
WAN-229 제목·15m 재현 명령으로 렌더. census 로직은 한 줄도 갈라지지 않는다.

## ⚠️ 결과 해석 (WAN-228 경고 계승)

* 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고, **15m이 그 낙관 가정에 가장 크게
  의존**한다 — 체결 대부분이 "스치듯 닿은 체결"(실거래 큐 우선순위상 가장 안 될 체결,
  WAN-96 비대칭). **15m (B) 재진입이 더 많이 나와도 가장 못 믿을 숫자다** — 크기는 참고하되
  채택 근거로 쓰지 말 것.
* §2 손익은 **격리 상한**(동시 1포지션·자본·슬롯 경합·북 상한 미모델링 = 층 2 sim 소관).
* 「엣지 없음」(WAN-84/88/111/114/124/151) 불변 · 「재진입 많다 = 좋다」 아님(WAN-222/223) ·
  층 2 resting-order sim 채택은 재-베이스라인 = 사용자 결정.

## 재현

```
# WAN-228 census 엔진을 15m으로 직접(이슈 §3 재현 명령 — CSV만 분리):
uv run python -m backtest.wan228_reentry_census --tf 15m --jobs 9 \
    --out-cells backtest/reports/wan229_reentry_census_15m.csv \
    --out-md backtest/reports/wan229_reentry_census_15m_summary.md

# 또는 이 모듈로(같은 census 엔진 · 15m 기본 · WAN-229 제목 요약):
uv run python -m backtest.wan229_reentry_census_15m --jobs 9   # 무거움(셀당 ~37분, WAN-203)
uv run python -m backtest.wan229_reentry_census_15m --from-csv  # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

# census 엔진은 WAN-228 모듈에서 그대로 가져온다(한 줄도 갈라지지 않게).
from backtest.wan228_reentry_census import (
    ALL_SYMBOLS,
    DEFAULT_END,
    DEFAULT_START,
    MATERIAL_RETURN_DELTA_PCT,
    NEGLIGIBLE_MISS_SHARE,
    REPORTS_DIR,
    SIGNIFICANT_MISS_SHARE,
    CellRow,
    _cell_table,
    cells_from_csv,
    cells_to_frame,
    describe_engine,
    run_report,
    verdict,
)

# 재사용 census 엔진을 이 모듈의 공개 API로도 명시 재노출한다(CSV 교차검산·테스트용).
__all__ = [
    "CellRow",
    "DEFAULT_CELLS_CSV",
    "DEFAULT_SUMMARY",
    "TIMEFRAME",
    "build_summary_markdown",
    "cells_from_csv",
    "cells_to_frame",
    "main",
    "run_report",
    "verdict",
]

#: WAN-229 전용 산출물 — 4h·1h(`wan228_reentry_census.csv`)를 덮지 않게 분리한다.
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan229_reentry_census_15m.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan229_reentry_census_15m_summary.md"

#: 이 이슈의 축은 15m 하나다(4h·1h는 WAN-228이 냈다).
TIMEFRAME = "15m"


def build_summary_markdown(rows: Sequence[CellRow], *, cells_csv: Path) -> str:
    """WAN-229 15m 요약 — census 표·판정은 WAN-228 렌더러(`_cell_table`·`verdict`)를 재사용한다."""
    window = next(iter({(r.window_start, r.window_end) for r in rows}), (0, 0))
    lines = [
        "# WAN-229 — 익절 후 존 내 재진입 census 15m 축 완결 ((B) §1c/§2 15m)",
        "",
        "**성격** 측정 전용 · WAN-228의 15m 스핀아웃(그림 완성, 판정을 바꾸는 축이 아니다). "
        "census 엔진(재무장 루프·행 모델·판정·자)은 `backtest.wan228_reentry_census`를 그대로 "
        "재사용하고 **TF만 15m**으로 돌린다 — 이 CSV는 WAN-228 4h·1h CSV와 열이 같아 "
        "교차검산된다. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`) · 옛 핀은 "
        "하나도 물려받지 않음 · 렌즈 `baseline` 단독(WAN-128) · 못 박은 6년 창(WAN-182) · "
        "기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "## 이 census가 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영.",
        "",
        "## 방법 — 익절마다 지정가 재무장 (WAN-228과 동일)",
        "",
        "채택 엔진으로 전 구간을 돌려 실제 채택 거래를 얻고, 익절(1.5R)로 닫힌 거래마다 그 "
        "존의 **실제 체결가**를 지정가로, 무효화 경계를 손절로, 고정 1.5R을 익절로 삼아 익절 "
        "**직후**부터 존 무효화(`break_time`)까지 **주문을 다시 걸고**(`limit_valid_bars=None`) "
        "`simulate_zone_limit_trade`(엔진과 동일)로 체결을 본다. 체결 = (B) 후보 재진입 1건. "
        "익절이면 다시 무장, 손절·미체결·데이터끝이면 그 존은 끝. 지정가는 원래 체결가로 "
        "**고정**한다(이슈 §1 정의 · 크기의 상한 · 밴드 재산정 재진입은 층 2 sim 소관).",
        "",
        "§2 손익은 IS와 **따뜻한 OOS**(WAN-166)로 가른다 — 재진입의 **진입 시각**이 평가 "
        "경계 전/후인가로 버킷. `격리 순수익`은 각 재진입을 기준자본에서 독립 체결시킨 "
        "`_to_trade` 순손익이다(동시 1포지션·자본·슬롯 경합·북 상한 미모델링 = **격리 상한**).",
        "",
        f"재현: `uv run python -m backtest.wan229_reentry_census_15m --jobs 9` "
        f"(요약만: `--from-csv`). 원자료: `{cells_csv}`. 창=[{window[0]}, {window[1]}).",
        "",
        "## 15m 칸별 census (§1c 크기 + §2 따뜻 OOS 손익)",
        "",
        "`채택진입` = 동시 1포지션 채택 거래 수 · `익절존` = 그중 1.5R 익절로 닫힌(재무장 "
        "대상) 수 · `(B)재진입` = 익절 후 재무장이 다시 체결된 수(전 구간) · `재진입비율` = "
        "(B)재진입 ÷ 채택진입 · `OOS 평균R`/`승률`/`격리순수익` = 따뜻한 OOS 버킷의 재진입 "
        "손익(승률은 데이터끝 보유 제외). †=신규 종목(펀딩 0행 → 순수익 낙관, 재진입 수엔 무관).",
        "",
    ]
    lines += _cell_table(rows, TIMEFRAME)
    lines += [
        "",
        "## 판정 — 15m도 층 2(resting-order sim)를 지을 값이 있는가",
        "",
        verdict(rows),
        "",
        f"판정 자: (B)재진입 ÷ 채택진입 ≥ {SIGNIFICANT_MISS_SHARE * 100:.0f}% **그리고** 따뜻한 "
        f"OOS 격리 순수익 심볼평균 ≥ {MATERIAL_RETURN_DELTA_PCT:.0f}%p → (a) GO · "
        f"< {NEGLIGIBLE_MISS_SHARE * 100:.0f}% **또는** 순수익 < {MATERIAL_RETURN_DELTA_PCT:.0f}%p "
        "→ (b) STOP · 사이 → (c). 문턱은 코드 상수다(WAN-228과 동일).",
        "",
        "⚠️ **이 표는 채택 근거가 아니라 크기 조사다** — (B) 구멍이 커도 「익절 후 재무장을 "
        "채택하라」가 아니고(손익·라이브 충실도는 층 2 sim·WAN-45 소관), 「엣지 없음」"
        "(WAN-84/88/111/114/124/151)도 그대로다. **15m은 세 TF 중 `baseline` 낙관 가정에 가장 "
        "크게 의존**하므로(체결 대부분이 스치듯 닿은 체결) (B) 재진입이 더 많아도 **가장 못 믿을 "
        "숫자다**. §2는 **격리 상한**이라(동시 1포지션·자본·북 상한 미모델링) 큐 우선순위"
        "(WAN-98 Canceled) 실측 없이는 이점 검증이 반쪽이다. **기본값·토대 불변**"
        "(측정 전용 · `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-229 (B) 익절 후 재진입 census — 15m 축 완결(WAN-228 재사용)"
    )
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=TIMEFRAME, help="기본 15m(이 이슈의 유일 축)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_md = Path(args.out_md)

    if args.from_csv:
        rows = cells_from_csv(out_cells)
        print(f"[wan229] CSV에서 {len(rows)}행 로드 — 백테스트 재실행 없음")
    else:
        rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(rows).to_csv(out_cells, index=False)
        print(f"[wan229] census {len(rows)}행 → {out_cells}")

    if not rows:
        print("[wan229] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_summary_markdown(rows, cells_csv=out_cells), encoding="utf-8")
    print(f"[wan229] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
