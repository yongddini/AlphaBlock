"""WAN-330: 반익절 래더를 **채택 좌표(4TF 한 지갑)** 에서 다시 + 체결 보수화 축.

## 무엇이 비어 있었나

WAN-323의 헤드라인(채택 북 `oos_warm` **MDD 19.58→14.73%(−4.84%p) · 승률 +10.5%p ·
수익/MDD +92.2% · 청산 0**)은 **4h·2h·1h 세 TF 지갑** 위에서 나왔다. 그런데 채택 북은
**15m을 포함한 4TF**이고, WAN-312 실측으로 `oos_warm` 거래의 **66.4%가 15m**이다 — 즉 그
표는 채택 지갑이 실제로 하는 매매의 **3분의 1만** 본 값이었다.

🚨 **북은 이어붙일 수 없다**(WAN-316) — 칸=(종목,TF)를 **한 프로세스·한 지갑**으로 묶으므로
`--append`로 15m을 더할 수 없다. 4TF를 **한 실행으로** 돌려야 한다. 그래서 이 모듈은
WAN-312→WAN-316과 **같은 스코프 패턴**을 쓴다: 한 번 만든 칸 후보를 `both`(4TF 채택 지갑)와
`both_no15m`(15m을 뺀 3TF 지갑 = WAN-323 판)으로 **각각 배치**해, 새 판과 옛 판이 **같은 표
안에서** 읽히고 옛 판이 라벨로 보존된다.

## 축 — 팔 3 × 렌즈 2 × 스코프 2

* **팔**: `A0`(현행 기준선 = 전량 1.5R) · `A1_be_off`(1.0R에서 절반) · `A1_be_on`(+ 본절).
  정의는 WAN-323 모듈에서 **import**한다(팔을 다시 정의하면 두 표가 갈라진다).
* **렌즈**(WAN-331 흡수): `baseline`(§1 · 주 수치) · `pen_5bp`(§2 · 체결 보수화 민감도).
  📌 **부분 익절은 체결을 하나 더 요구한다** — 「진입 + **분할 청산** + 최종 청산」이라 래더의
  이득 자체가 낙관 체결 가정에 **더 깊이** 기댄다. 이 렌즈가 그 의존을 가른다.
* **스코프**: `both`(15m·1h·2h·4h) · `both_no15m`(1h·2h·4h) · 단일 TF(요청하면).

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 못 박은 6년 창 · 재진입 ON(band) · cap_only 5배 ·
존폭 필터 1.28 · 오프셋 2bp · 손절폭 가드 0.3% · 유동성 한도 채택값. 구간은 `oos_warm`(주,
WAN-166) + `oos`(스트레스) + `full`·`is` 병기.

## 판정 열 — 총수익 %가 아니다

MDD · 수익/MDD · 최대 동시 리스크 · **실효 동시 리스크**(WAN-312) · 청산 건수로 읽는다.
`total_return` %는 수천 거래 복리 착시라 실현 수익이 아니다(WAN-169/213).

⚠️ **실효 동시 리스크는 채택 회계(k=1)에서 계획값과 정의상 같다** — 이 표는 그 등식을
그대로 싣고, 손절이 계획 1R보다 밀리는 축(k>1)은 WAN-312/316 소관이라 여기서 쓸지 말지를
`--stress-k`로 **명시 옵트인**하게 뒀다(기본 1.0 = 채택 회계).

## 검산

* **(a) A0 ≡ 인자 없는 채택 북** — 같은 payload를 `book_cli.run_book`의 마지막 두 단계
  (`apply_funding_proxy` → `build_book_rows`)에 그대로 넣어 낸 행과 대조한다. 남는 고리
  (`run_cells` 인자가 채택 경로와 같은가)는 `tests/test_wan330_partial_tp_ladder_4tf.py`가
  **동작으로** 고정한다(모듈 상수 대조가 아니라 실제 호출 인자 캡처).
* **(c) 3TF 판 보존** — `--check-legacy-grid`가 `baseline × both_no15m` 행을 WAN-323
  `wan323_partial_tp_ladder_book.csv`와 대조한다. 「보존한다」는 라벨이 아니라 **같은 숫자**
  여야 성립한다(WAN-316 패턴).

재현:

```
uv run python -m backtest.wan330_partial_tp_ladder_4tf --lens baseline --jobs 4
uv run python -m backtest.wan330_partial_tp_ladder_4tf --lens pen_5bp --jobs 4 --append
uv run python -m backtest.wan330_partial_tp_ladder_4tf --check-legacy-grid
uv run python -m backtest.wan330_partial_tp_ladder_4tf --from-csv      # 요약만
```
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE, BookRunRow, build_book_rows
from backtest.leverage_book import LeverageBookParams
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import (
    ARMS_BY_NAME,
    PARTIAL_FRACTION,
    PRIMARY_OOS,
    SEGMENT_ORDER,
    STRESS_OOS,
    LadderArm,
)

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan330_partial_tp_ladder_4tf.csv"
SUMMARY_PATH = REPORTS_DIR / "wan330_partial_tp_ladder_4tf_summary.md"

#: WAN-323 북 판(3TF 지갑) — 검산 (c)의 기준 표. 덮지 않는다.
LEGACY_BOOK_CSV = REPORTS_DIR / "wan323_partial_tp_ladder_book.csv"

#: 이 이슈가 돌리는 팔(사용자 사양) — B 계열은 범위 밖이다.
DEFAULT_ARMS: tuple[str, ...] = ("A0", "A1_be_off", "A1_be_on")

#: 렌즈 축(WAN-331 흡수). `baseline`이 §1 주 수치, `pen_5bp`가 §2 민감도다.
#: ⚠️ WAN-128이 폐지한 것은 **모든 리포트에 3렌즈를 병기하는 관행**이지 `pen_5bp` 자체가
#: 아니다 — 옵트인 민감도로 존치하며 이 모듈이 정확히 그 용법이다.
BASELINE_LENS = "baseline"
STRESS_LENS = "pen_5bp"
DEFAULT_LENSES: tuple[str, ...] = (BASELINE_LENS, STRESS_LENS)

BOTH_SCOPE = "both"
#: 15m을 뺀 지갑 = WAN-323이 실제로 돌린 3TF 판. **스코프로** 표현하는 것이 핵심이다 —
#: 지갑은 한 프로세스의 칸 집합이므로, 같은 payload에서 칸을 골라내면 3TF 실행과 같은 값이
#: 나온다(검산 (c)가 그것을 숫자로 증명한다).
BOTH_NO_15M_SCOPE = "both_no15m"
EXCLUDED_FROM_NO_15M = "15m"

#: 판정에 쓰는 대조 쌍 — 래더 팔은 **같은 계열 기준선(`A0`) 대비**로만 읽는다.
BASELINE_ARM = "A0"


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class LadderBookRow(BaseModel):
    """한 (렌즈, 스코프, 팔, 구간)의 북 집계 행 — 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    lens: str
    scope: str
    arm: str
    family: str
    take_profit_r: float
    partial_r: float | None
    breakeven: bool
    segment: str
    stress_k: float
    """실효 동시 리스크의 스트레스 배수(WAN-312). 채택 회계는 `1.0`이다."""
    num_cells: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    max_effective_concurrent_risk: float
    liquidation_events: int
    skipped_notional: int


def _row_key(row: LadderBookRow) -> tuple[str, str, str, str]:
    return (row.lens, row.scope, row.arm, row.segment)


CSV_KEYS: tuple[str, ...] = ("lens", "scope", "arm", "segment")


# --------------------------------------------------------------------------- #
# 스코프 — 한 payload 집합에서 여러 지갑을 만든다
# --------------------------------------------------------------------------- #


def scope_payloads(payloads: Sequence[CellPayload], scope: str) -> list[CellPayload]:
    """스코프의 칸 — `both`(전 TF) · `both_no15m`(15m 뺀 지갑) · 단일 TF."""
    if scope == BOTH_SCOPE:
        return list(payloads)
    if scope == BOTH_NO_15M_SCOPE:
        return [p for p in payloads if p.timeframe != EXCLUDED_FROM_NO_15M]
    return [p for p in payloads if p.timeframe == scope]


def resolve_scopes(timeframes: Sequence[str]) -> list[str]:
    """이번 실행이 낼 스코프 — 채택 지갑 + (15m이 섞였고 남는 칸이 2개 이상이면) 3TF 판.

    단일 TF 스코프는 내지 않는다. 이 이슈의 질문은 **지갑**에 관한 것이고, per-cell·단일 TF
    래더 표는 WAN-323이 이미 냈다(그쪽은 `--append`가 되는 성격이라 여기 섞으면 혼동된다).
    """
    scopes = [BOTH_SCOPE]
    others = [tf for tf in timeframes if tf != EXCLUDED_FROM_NO_15M]
    if EXCLUDED_FROM_NO_15M in timeframes and len(others) >= 2:
        scopes.append(BOTH_NO_15M_SCOPE)
    return scopes


# --------------------------------------------------------------------------- #
# 한 팔 실행
# --------------------------------------------------------------------------- #


def _lens_arg(lens: str) -> harness.FillPreset | None:
    """`baseline`은 `None`으로 넘긴다 — 채택 기본값이라 옛 CSV와 비트 단위로 같다."""
    if lens == BASELINE_LENS:
        return None
    return harness.fill_preset(lens)


def _to_row(
    *,
    lens: str,
    scope: str,
    arm: LadderArm,
    stress_k: float,
    row: BookRunRow,
) -> LadderBookRow:
    return LadderBookRow(
        lens=lens,
        scope=scope,
        arm=arm.name,
        family=arm.family,
        take_profit_r=arm.take_profit_r,
        partial_r=arm.partial_r,
        breakeven=arm.breakeven,
        segment=row.segment,
        stress_k=stress_k,
        num_cells=row.num_cells,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd,
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=row.max_concurrent_risk,
        max_effective_concurrent_risk=row.max_effective_concurrent_risk,
        liquidation_events=row.liquidation_events,
        skipped_notional=row.skipped_notional,
    )


def book_rows_for_payloads(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    stress_k: float = 1.0,
) -> list[BookRunRow]:
    """채택 북 배치 — `book_cli.run_book`의 마지막 두 단계와 **같은 함수·같은 인자**다.

    `apply_funding_proxy`를 여기서 거치는 것이 요점이다(WAN-305: 기본이 채택 규칙). 12종목이
    전부 자기 확정 펀딩을 갖는 오늘 좌표에서는 **무동작**이고, 검산 (a)가 그 사실을 숫자로
    남긴다 — 무동작이어야 이 표의 3TF 스코프가 WAN-323 판과 셀 대 셀로 비교된다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return build_book_rows(
        proxied,
        book=LeverageBookParams(),
        segments=SEGMENT_ORDER,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        stress_risk_multiple=stress_k,
    )


def verify_adopted_identity(
    payloads: Sequence[CellPayload], *, start_ms: int, end_ms: int
) -> float:
    """검산 (a) — 펀딩 대리가 이 좌표에서 **무동작**인가(= 원 payload 행 ≡ 채택 경로 행).

    돌려주는 값은 최대 절대차. `0.0`이면 (1) 12종목이 전부 자기 펀딩을 갖고 (2) 이 표의
    `both_no15m` 스코프가 대리를 안 쓴 WAN-323 3TF 판과 **같은 자로 잰 값**이라는 뜻이다.

    ⚠️ 이 함수가 못 잡는 고리 하나 — `run_cells`에 넘긴 인자가 채택 경로와 같은가. 그건
    회귀 테스트가 실제 호출 인자를 캡처해 동작으로 고정한다(모듈 상수 대조가 아니다).
    """
    raw = {
        r.segment: r
        for r in build_book_rows(
            payloads,
            book=LeverageBookParams(),
            segments=SEGMENT_ORDER,
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
        )
    }
    worst = 0.0
    for row in book_rows_for_payloads(payloads, start_ms=start_ms, end_ms=end_ms):
        other = raw[row.segment]
        worst = max(
            worst,
            abs(row.total_return - other.total_return),
            abs(row.max_drawdown - other.max_drawdown),
            float(abs(row.num_trades - other.num_trades)),
        )
    return worst


#: `run_cells`에 넘기는 **채택 좌표 인자** — `book_cli.run_book`이 쓰는 것과 같아야 한다.
#: 회귀 테스트가 두 호출의 인자를 실제로 캡처해 대조하므로, 여기를 바꾸면 테스트가 깨진다.
ADOPTED_CELL_KWARGS: dict[str, object] = {
    "adv_fraction": harness.UNSET,
    "reentry": True,
    "reentry_entry_rule": ADOPTED_REENTRY_ENTRY_RULE,
}


def run_arm(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arm: LadderArm,
    *,
    lens: str,
    scopes: Sequence[str],
    start: str,
    end: str,
    jobs: int,
    stress_k: float = 1.0,
    log: bool = True,
) -> tuple[list[LadderBookRow], float | None]:
    """한 (팔, 렌즈)의 후보를 한 번 만들고 **스코프마다** 지갑을 돌린다.

    후보 생성이 비용의 전부이고 배치 회계는 싸므로(`book_cli` 설계), 4TF 지갑과 3TF 지갑을
    한 실행에서 같이 내는 데 추가 비용이 거의 없다 — 이것이 「옛 판을 라벨로 보존」이 공짜인
    이유다.

    돌려주는 두 번째 값은 기준선 팔에서만 계산하는 검산 (a)의 최대 절대차(그 외에는 `None`).
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        fill=_lens_arg(lens),
        # ⚠️ 래더 팔은 `engine_check`를 끈다 — 그 검산은 격리 성과가 `harness.run_once`(래더
        # 없는 per-cell)와 비트 일치하는지 보는 것이라 래더를 켠 팔에서는 **당연히** 어긋난다.
        engine_check=arm.is_baseline and lens == BASELINE_LENS,
        take_profit_r=arm.take_profit_r,
        partial_take_profit_r=arm.partial_r,
        partial_take_profit_fraction=PARTIAL_FRACTION,
        breakeven_after_partial=arm.breakeven,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )
    identity: float | None = None
    if arm.is_baseline and lens == BASELINE_LENS:
        identity = verify_adopted_identity(payloads, start_ms=start_ms, end_ms=end_ms)
        if log:
            print(f"[wan330] 검산(a) 펀딩 대리 무동작 최대차: {identity:.2e}", flush=True)

    rows: list[LadderBookRow] = []
    for scope in scopes:
        scoped = scope_payloads(payloads, scope)
        if not scoped:
            continue
        for book_row in book_rows_for_payloads(
            scoped, start_ms=start_ms, end_ms=end_ms, stress_k=stress_k
        ):
            rows.append(_to_row(lens=lens, scope=scope, arm=arm, stress_k=stress_k, row=book_row))
    return rows, identity


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    arms: Sequence[LadderArm] | None = None,
    lenses: Sequence[str] = (BASELINE_LENS,),
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    stress_k: float = 1.0,
    on_arm: Callable[[list[LadderBookRow]], None] | None = None,
    log: bool = True,
) -> list[LadderBookRow]:
    """(렌즈 × 팔)마다 4TF 지갑을 한 실행으로 돈다.

    📌 팔마다 즉시 적재한다(`on_arm`) — 한 팔이 12종목 × 4TF라 60~90분이고, 팔은 각자 독립
    지갑이라 중간에 끊겨도 끝난 팔은 보존된다. **끊길 수 없는 것은 한 팔 안의 4TF뿐이다.**
    """
    selected = list(arms) if arms is not None else [ARMS_BY_NAME[n] for n in DEFAULT_ARMS]
    scopes = resolve_scopes(timeframes)
    rows: list[LadderBookRow] = []
    for lens in lenses:
        for arm in selected:
            t0 = time.time()
            arm_rows, _identity = run_arm(
                symbols,
                timeframes,
                arm,
                lens=lens,
                scopes=scopes,
                start=start,
                end=end,
                jobs=jobs,
                stress_k=stress_k,
                log=log,
            )
            rows.extend(arm_rows)
            if on_arm is not None:
                on_arm(arm_rows)
            if log:
                print(
                    f"[wan330] {lens}/{arm.name}: {len(arm_rows)}행 ({time.time() - t0:.0f}s)",
                    flush=True,
                )
    return rows


def rows_to_frame(rows: Sequence[LadderBookRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


# --------------------------------------------------------------------------- #
# 검산 (c) — 3TF 판이 보존되는가
# --------------------------------------------------------------------------- #


def compare_legacy_book(frame: pd.DataFrame) -> tuple[int, float] | None:
    """`baseline × both_no15m` 행이 WAN-323 `_book.csv`와 **같은 숫자**인가.

    돌려주는 값은 `(대조한 행 수, 최대 절대차)`. 옛 표가 없거나 겹치는 팔이 없으면 `None`.
    「보존한다」가 라벨이 아니라 같은 값임을 보이는 것이 이 검산의 전부다(WAN-316 패턴).

    ⚠️ 4TF `both` 스코프는 옛 판에 대응물이 **없다** — 그게 이 이슈가 존재하는 이유다.
    """
    if not LEGACY_BOOK_CSV.exists():
        return None
    legacy = pd.read_csv(LEGACY_BOOK_CSV)
    ref = {(str(r["arm"]), str(r["segment"])): r for _, r in legacy.iterrows()}
    subset = frame[(frame["lens"] == BASELINE_LENS) & (frame["scope"] == BOTH_NO_15M_SCOPE)]
    matched = 0
    worst = 0.0
    for _, row in subset.iterrows():
        other = ref.get((str(row["arm"]), str(row["segment"])))
        if other is None:
            continue
        matched += 1
        worst = max(
            worst,
            abs(float(row["total_return"]) - float(other["total_return"])),
            abs(float(row["max_drawdown"]) - float(other["max_drawdown"])),
            abs(float(row["max_concurrent_risk"]) - float(other["max_concurrent_risk"])),
            float(abs(int(row["num_trades"]) - int(other["num_trades"]))),
        )
    return (matched, worst) if matched else None


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _pick(frame: pd.DataFrame, lens: str, scope: str, arm: str, segment: str) -> pd.Series | None:
    hit = frame[
        (frame["lens"] == lens)
        & (frame["scope"] == scope)
        & (frame["arm"] == arm)
        & (frame["segment"] == segment)
    ]
    return None if hit.empty else hit.iloc[0]


def _pp(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%p"


def _delta(
    frame: pd.DataFrame, lens: str, scope: str, arm: str, segment: str, column: str
) -> float | None:
    """팔 − 기준선(`A0`). 판정은 언제나 같은 (렌즈, 스코프, 구간) 안에서만 낸다."""
    mine = _pick(frame, lens, scope, arm, segment)
    base = _pick(frame, lens, scope, BASELINE_ARM, segment)
    if mine is None or base is None:
        return None
    return float(mine[column]) - float(base[column])


def _verdict_word(delta: float | None, *, legacy: float) -> str:
    """MDD 개선(음수 델타)이 옛 판(`legacy`) 대비 유지/축소/소멸/역전 중 무엇인가."""
    if delta is None:
        return "판정 불가(행 없음)"
    if delta >= 0.0:
        return "**소멸(역전)** — 래더가 낙폭을 오히려 키운다"
    ratio = delta / legacy if legacy != 0 else 0.0
    if ratio >= 0.9:
        return "**유지**"
    if ratio >= 0.4:
        return "**축소**"
    return "**대부분 소멸**"


#: WAN-323이 3TF 지갑에서 낸 `A1_be_on` − `A0`의 `oos_warm` MDD 델타(−4.84%p). 판정
#: 문장이 「그래서 얼마가 됐나」를 이 값 대비로 말한다.
LEGACY_MDD_DELTA = -0.0484


#: 잔존율의 분모 하한 — 기준 증분이 이보다 작으면 비율을 내지 않는다.
#: 🚨 **WAN-115가 문서화한 함정이다** — 기준 증분이 0 언저리면 비율이 폭발해(391% · 1155%)
#: 「유지」로 읽히는데 실제로는 **원래 0이었던 것**이다. 그런 셀은 **부호만** 본다.
_RESIDUAL_FLOOR = 0.005

#: 잔존율을 못 낼 때 판정문에 붙이는 주의 — 안 낸 것과 못 낸 것을 구분해 적는다.
_RESIDUAL_CAVEAT = " (⚠️ 기준 증분이 0 언저리이거나 부호가 갈려 잔존율은 뜻이 없다 — 부호만 읽는다)"


def _residual_ratio(base: float | None, stressed: float | None) -> float | None:
    """`pen_5bp` 증분 ÷ `baseline` 증분 — 뜻이 설 때만 낸다.

    기준 증분이 `_RESIDUAL_FLOOR`(0.5%p) 미만이거나 두 증분의 **부호가 다르면** `None`이다
    (후자는 「잔존」이라는 말 자체가 성립하지 않는다 — 이득이 손해로 바뀐 것이라 크기가
    아니라 부호가 답이다).
    """
    if base is None or stressed is None or abs(base) < _RESIDUAL_FLOOR:
        return None
    if (base < 0) != (stressed < 0):
        return None
    return stressed / base


def build_summary(frame: pd.DataFrame) -> str:
    lenses = [lens for lens in DEFAULT_LENSES if lens in set(frame["lens"])]
    scopes = [s for s in (BOTH_SCOPE, BOTH_NO_15M_SCOPE) if s in set(frame["scope"])]
    arms = [n for n in DEFAULT_ARMS if n in set(frame["arm"])]

    lines: list[str] = [
        "# WAN-330 — 반익절 래더를 채택 좌표(4TF 한 지갑)에서 · 체결 보수화 축 병기",
        "",
        "WAN-323의 북 판은 **4h·2h·1h 세 TF 지갑**이었다. 채택 북은 **15m을 포함한 4TF**이고 "
        "`oos_warm` 거래의 **66.4%가 15m**이라(WAN-312), 그 표는 채택 지갑이 실제로 하는 "
        "매매의 3분의 1만 본 값이었다. 이 표가 **한 실행**으로 4TF 지갑(`both`)과 3TF 지갑"
        "(`both_no15m` = 옛 판)을 함께 낸다 — 북은 이어붙일 수 없으므로(WAN-316) 이것이 "
        "옛 판을 보존하면서 새 판을 얻는 유일한 방법이다.",
        "",
        "🚨 **판정은 위험조정 축으로 읽는다** — `total_return` %는 수천 거래 복리라 실현 "
        "수익이 아니다(WAN-169/213). MDD · 수익/MDD · 최대(실효) 동시 리스크 · 청산이 판정 "
        "열이다.",
        "",
        "📌 **좌표**: 12종목 · 못 박은 6년 창(2020-09-15~2026-07-22) · 재진입 ON(band) · "
        "cap_only 5배 · 존폭 필터 1.28 · 오프셋 2bp · 손절폭 가드 0.3% · **핀 하나도 없음**"
        "(WAN-305).",
        "",
    ]

    # ── 본표 ────────────────────────────────────────────────────────────────
    for lens in lenses:
        label = "§1 `baseline` (주 수치)" if lens == BASELINE_LENS else "§2 `pen_5bp` (체결 보수화)"
        lines += [f"## {label}", ""]
        for scope in scopes:
            title = (
                "채택 지갑 4TF(15m·1h·2h·4h)"
                if scope == BOTH_SCOPE
                else "3TF 지갑(1h·2h·4h) — WAN-323 판 보존"
            )
            lines += [
                f"### `{scope}` — {title}",
                "",
                "| 구간 | 팔 | 거래 | 승률 | 총수익 | MDD | 수익/MDD | 최대 동시 리스크 | "
                "실효 동시 리스크 | 최대 동시 칸 | 청산 | 명목 밀림 |",
                "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |",
            ]
            for segment in SEGMENT_ORDER:
                for arm_name in arms:
                    row = _pick(frame, lens, scope, arm_name, segment)
                    if row is None:
                        continue
                    over = row["return_over_mdd"]
                    lines.append(
                        f"| {segment} | `{arm_name}` | {int(row['num_trades'])} | "
                        f"{float(row['win_rate']) * 100:.2f}% | "
                        f"{float(row['total_return']) * 100:+,.0f}% | "
                        f"{float(row['max_drawdown']) * 100:.2f}% | "
                        f"{'—' if pd.isna(over) else f'{float(over):,.1f}'} | "
                        f"{float(row['max_concurrent_risk']) * 100:.2f}% | "
                        f"{float(row['max_effective_concurrent_risk']) * 100:.2f}% | "
                        f"{int(row['peak_concurrency'])} | "
                        f"{int(row['liquidation_events'])} | "
                        f"{int(row['skipped_notional'])} |"
                    )
            lines.append("")

    # ── 팔 − 기준선 델타 ────────────────────────────────────────────────────
    lines += [
        "## 팔 − 기준선(`A0`) 델타 — 「무엇을 내고 무엇을 사는가」",
        "",
        "| 렌즈 | 스코프 | 구간 | 팔 | ΔMDD | Δ승률 | Δ총수익 | Δ거래 수 |",
        "| -- | -- | -- | -- | -- | -- | -- | -- |",
    ]
    for lens in lenses:
        for scope in scopes:
            for segment in (PRIMARY_OOS, STRESS_OOS, "full"):
                for arm_name in arms:
                    if arm_name == BASELINE_ARM:
                        continue
                    mdd = _delta(frame, lens, scope, arm_name, segment, "max_drawdown")
                    if mdd is None:
                        continue
                    win = _delta(frame, lens, scope, arm_name, segment, "win_rate")
                    ret = _delta(frame, lens, scope, arm_name, segment, "total_return")
                    trades = _delta(frame, lens, scope, arm_name, segment, "num_trades")
                    lines.append(
                        f"| `{lens}` | `{scope}` | {segment} | `{arm_name}` | {_pp(mdd)} | "
                        f"{_pp(win)} | {'—' if ret is None else f'{ret * 100:+,.0f}%p'} | "
                        f"{'—' if trades is None else f'{trades:+,.0f}'} |"
                    )
    lines.append("")

    # ── 판정 ────────────────────────────────────────────────────────────────
    lines += _verdict_lines(frame, lenses)

    # ── 검산 ────────────────────────────────────────────────────────────────
    legacy = compare_legacy_book(frame)
    lines += [
        "## 검산",
        "",
        "* **(a) `A0` ≡ 인자 없는 채택 북** — 같은 payload를 채택 경로의 마지막 두 단계"
        "(`apply_funding_proxy` → `build_book_rows`)에 넣은 행과 대조한다(실행 로그에 최대차가 "
        "찍힌다). 남는 고리(`run_cells` 인자가 채택 경로와 같은가)는 회귀 테스트가 실제 호출 "
        "인자를 캡처해 **동작으로** 고정한다.",
    ]
    if legacy is None:
        lines.append(
            "* **(c) 3TF 판 보존** — 옛 표(`wan323_partial_tp_ladder_book.csv`)가 없거나 "
            "겹치는 팔이 없어 대조하지 못했다(**안 돈 것이 아니라 대조 대상이 없다**)."
        )
    else:
        matched, worst = legacy
        verdict = (
            "비트 일치" if worst == 0.0 else ("부동소수 잡음" if worst < 1e-9 else "🚨 불일치")
        )
        lines.append(
            f"* **(c) 3TF 판 보존** — `baseline × both_no15m` **{matched}행** 대조, 최대 "
            f"절대차 **{worst:.2e}** ({verdict}). 「보존한다」가 라벨이 아니라 **같은 숫자**임을 "
            "뜻한다. 4TF `both`는 옛 판에 대응물이 없다 — 그게 이 이슈가 있는 이유다."
        )
    lines += [
        "",
        "## 안전 (기록)",
        "",
        "* **기본값·토대 불변** — 래더는 전부 **옵트인**이다(`ConfluenceParams()`·"
        "`LeverageBookParams()` 그대로 · `ALPHABLOCK_LIVE_TRADING=false` 유지). 채택은 "
        "**재-베이스라인 = 사용자 결정**이고 개발자 임의 착수 금지.",
        "* 🚨 **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 익절 구조는 알파를 "
        "만들지 못하고 **위험의 모양만 바꾼다**(WAN-90).",
        "* ⚠️ `pen_5bp`**도 진값이 아니다** — 큐 우선순위·부분 체결은 틱·호가(WAN-98 Canceled) "
        "소관이라 이 렌즈는 **민감도**이지 실측이 아니다. 「보수화해도 살아남았다」는 「실전에서 "
        "된다」가 아니다.",
        "* ⚠️ 총수익 %는 복리 착시(WAN-169/213) · 6년 MDD는 폭락 미포함 **바닥선**(창이 "
        "2020-09 시작이라 2018·2020-03 폭락이 없다).",
        "* ⚠️ **실효 동시 리스크는 채택 회계(k=1)에서 계획값과 정의상 같다** — 손절이 계획 "
        "1R보다 밀리는 축(k>1)은 WAN-312/316 소관이다.",
        "* ⚠️ 4h 봉은 WAN-329 재수집 **전** 데이터다(WAN-327 실측: per-cell 22/24셀 비트 불변 · "
        "MDD 전 셀 불변이라 영향은 작다 — 시점만 기록).",
        "* ⚠️ 자본곡선은 **거래 단위**라 부분 익절의 실현손익도 최종 청산 시각에 반영된다 — "
        "래더의 MDD 이득이 이 회계에서 **과소평가**될 수 있다(두 팔이 같은 회계라 방향은 유효).",
        "",
    ]
    return "\n".join(lines)


def _verdict_lines(frame: pd.DataFrame, lenses: Sequence[str]) -> list[str]:
    """완료기준 3·6의 한 문장 판정 — 「15m을 넣으면 −4.84%p가 얼마가 되는가」."""
    lines = ["## 판정", ""]
    for lens in lenses:
        four = _delta(frame, lens, BOTH_SCOPE, "A1_be_on", PRIMARY_OOS, "max_drawdown")
        three = _delta(frame, lens, BOTH_NO_15M_SCOPE, "A1_be_on", PRIMARY_OOS, "max_drawdown")
        trades_four = _delta(frame, lens, BOTH_SCOPE, "A1_be_on", PRIMARY_OOS, "num_trades")
        base = _pick(frame, lens, BOTH_SCOPE, BASELINE_ARM, PRIMARY_OOS)
        base_trades = None if base is None else int(base["num_trades"])
        share = (
            None if (trades_four is None or not base_trades) else trades_four / float(base_trades)
        )
        word = _verdict_word(four, legacy=three if three is not None else LEGACY_MDD_DELTA)
        lines += [
            f"### `{lens}` — `A1_be_on` vs `A0`, `{PRIMARY_OOS}`",
            "",
            f"* **4TF 채택 지갑 ΔMDD = {_pp(four)}** · 같은 실행의 3TF 지갑 ΔMDD = "
            f"{_pp(three)} → {word}.",
            f"* 거래 수 변화 {'—' if trades_four is None else f'{trades_four:+,.0f}건'}"
            f"{'' if share is None else f' ({share * 100:+.1f}%)'} — ⚠️ **크기가 아니라 "
            "부호와 잔존율로** 읽는다(거래는 거의 안 줄었는데 수익만 사라지는 WAN-96 "
            "비대칭이 이 저장소의 반복 서명이다).",
            "",
        ]
    if BASELINE_LENS in lenses and STRESS_LENS in lenses:
        base_delta = _delta(
            frame, BASELINE_LENS, BOTH_SCOPE, "A1_be_on", PRIMARY_OOS, "max_drawdown"
        )
        pen_delta = _delta(frame, STRESS_LENS, BOTH_SCOPE, "A1_be_on", PRIMARY_OOS, "max_drawdown")
        base_row = _pick(frame, BASELINE_LENS, BOTH_SCOPE, BASELINE_ARM, PRIMARY_OOS)
        pen_row = _pick(frame, STRESS_LENS, BOTH_SCOPE, BASELINE_ARM, PRIMARY_OOS)
        drop = (
            None
            if base_row is None or pen_row is None or not int(base_row["num_trades"])
            else int(pen_row["num_trades"]) / int(base_row["num_trades"]) - 1.0
        )
        residual = _residual_ratio(base_delta, pen_delta)
        lines += [
            "### §2 체결 보수화 판정 (완료기준 6)",
            "",
            f"* 4TF `{PRIMARY_OOS}` ΔMDD: `baseline` {_pp(base_delta)} → `pen_5bp` "
            f"{_pp(pen_delta)}"
            f"{'' if residual is None else f' (잔존 {residual * 100:.1f}%)'}"
            f"{'' if residual is not None else _RESIDUAL_CAVEAT}.",
            f"* 기준선 팔 거래 수 감소율 "
            f"{'—' if drop is None else f'{drop * 100:+.2f}%'} — WAN-96 비대칭 대조용으로 "
            "**반드시 함께** 읽는다.",
            "",
        ]
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-330 반익절 래더 · 채택 4TF 북")
    parser.add_argument("--tf", default=None, help="쉼표 구분 TF(기본: 채택 4TF)")
    parser.add_argument("--symbols", default=None, help="쉼표 구분 심볼(기본: 채택 12종목)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=None, help="(심볼, TF) 병렬 워커 수")
    parser.add_argument(
        "--arms", default=None, help=f"쉼표 구분 팔(기본: {','.join(DEFAULT_ARMS)})"
    )
    parser.add_argument(
        "--lens",
        default=BASELINE_LENS,
        help=f"쉼표 구분 렌즈(기본: {BASELINE_LENS} · 가능: {','.join(DEFAULT_LENSES)})",
    )
    parser.add_argument(
        "--stress-k",
        type=float,
        default=1.0,
        help="실효 동시 리스크의 스트레스 배수(WAN-312). 기본 1.0 = 채택 회계",
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자를 돌지 않고 요약만 재생성")
    parser.add_argument(
        "--check-legacy-grid",
        action="store_true",
        help="검산 (c)만 — baseline × both_no15m 행을 WAN-323 _book.csv와 대조",
    )
    return parser.parse_args(argv)


def _resolve_arms(arg: str | None) -> list[LadderArm]:
    names = DEFAULT_ARMS if arg is None else tuple(n.strip() for n in arg.split(",") if n.strip())
    unknown = [name for name in names if name not in ARMS_BY_NAME]
    if unknown:
        raise ValueError(f"모르는 팔입니다: {unknown} (가능: {', '.join(ARMS_BY_NAME)})")
    return [ARMS_BY_NAME[name] for name in names]


def _resolve_lenses(arg: str) -> list[str]:
    lenses = [name.strip() for name in arg.split(",") if name.strip()]
    for name in lenses:
        # `baseline`은 `None`으로 넘기지만 이름 자체는 harness가 알아야 한다(오타 방지).
        harness.fill_preset(name)
    return lenses


def _persist(rows: Sequence[LadderBookRow]) -> pd.DataFrame:
    """팔 하나가 끝날 때마다 즉시 적재 — 중간에 죽어도 끝난 팔은 남는다."""
    frame = rows_to_frame(rows)
    if CSV_PATH.exists():
        prior = pd.read_csv(CSV_PATH)
        frame = pd.concat([prior, frame], ignore_index=True).drop_duplicates(
            subset=list(CSV_KEYS), keep="last"
        )
    frame.to_csv(CSV_PATH, index=False)
    print(f"[wan330] CSV 적재: {CSV_PATH} ({len(frame)}행)", flush=True)
    return frame


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    timeframes = (
        tuple(t.strip() for t in args.tf.split(",") if t.strip())
        if args.tf
        else harness.DEFAULT_TIMEFRAMES
    )
    symbols = (
        tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        if args.symbols
        else harness.DEFAULT_SYMBOLS
    )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.check_legacy_grid:
        if not CSV_PATH.exists():
            print(f"[wan330] {CSV_PATH}가 없습니다 — 먼저 격자를 돌리세요.", flush=True)
            return 1
        result = compare_legacy_book(pd.read_csv(CSV_PATH))
        if result is None:
            print("[wan330] 검산(c): 대조할 옛 행이 없습니다(안 돈 것이 아니다).", flush=True)
            return 1
        matched, worst = result
        print(f"[wan330] 검산(c): {matched}행 대조 · 최대 절대차 {worst:.2e}", flush=True)
        return 0 if worst < 1e-9 else 1

    if args.from_csv:
        if not CSV_PATH.exists():
            print(f"[wan330] {CSV_PATH}가 없습니다 — 먼저 격자를 돌리세요.", flush=True)
            return 1
        frame = pd.read_csv(CSV_PATH)
    else:
        if not args.append and CSV_PATH.exists():
            CSV_PATH.unlink()  # 새 판 — 이어붙이려면 `--append`를 명시한다.
        frame = pd.DataFrame()

        def _on_arm(rows: list[LadderBookRow]) -> None:
            nonlocal frame
            frame = _persist(rows)

        run_report(
            symbols,
            timeframes,
            arms=_resolve_arms(args.arms),
            lenses=_resolve_lenses(args.lens),
            start=args.start,
            end=args.end,
            jobs=args.jobs if args.jobs is not None else harness.default_jobs(),
            stress_k=args.stress_k,
            on_arm=_on_arm,
        )
        if frame.empty and CSV_PATH.exists():
            frame = pd.read_csv(CSV_PATH)

    if frame.empty:
        print("[wan330] 낸 행이 없습니다.", flush=True)
        return 1
    SUMMARY_PATH.write_text(build_summary(frame), encoding="utf-8")
    print(f"[wan330] 요약: {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
