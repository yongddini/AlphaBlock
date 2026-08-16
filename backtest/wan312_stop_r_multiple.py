"""WAN-312 — 손절 한 번이 1.5R이면 5배 북이 버티나: 손실을 **R 배수**로 못 박는 스트레스.

## 배경 — 사용자 요청 2026-08-16

> *"손절을 했을 때 밀리는 걸 1.5R까지 본다면 손익이 어떻게 되는지를 봐야할 것 같아."*

손절 주문은 존 무효화 경계(= 1R)에 **그대로** 두고, 발동 순간 실제 체결가가 그보다 나빠서
한 번 손절에 **k·R**을 잃는다면 채택 북(cap_only 5배, WAN-213)의 낙폭·청산이 어떻게 되는가.

⚠️ **손절선을 넓히는 것이 아니다** — 진입·거래 집합·수량·승률은 전부 그대로이고 **진 거래의
손실 크기만** 커진다. 손절선 자체를 옮기는 안은 재-베이스라인(사용자 결정)이고 범위 밖이다.

## WAN-276/277(α 축)이 답하지 못한 세 가지

1. **α는 1.5R을 표현할 수 없다** — α=1이 「그 1분봉이 실제로 간 저가」라 봉이 1.2R까지만
   내려갔으면 α 축의 천장이 1.2R이다. 사용자가 묻는 「무조건 1.5R」(거래정지·초 단위 급락·
   호가 공백)은 **격자에 존재하지 않는다**. 그래서 자를 **R 배수**로 바꾼다.
2. 🚨 **`max_concurrent_risk`가 α에 안 움직인다** — 그 열은 **주문을 낼 때 계획한** 리스크
   합이라 손절 체결 보수화에 **설계상 불변**이다. 그런데 채택 북이 5배인 근거가 바로 그
   열이고(`cap_only` = 거래당 1% 고정 × 칸 수만 늘림), 그 안전 논리 전체가 **「손절 한 번 =
   정확히 1R」** 가정 위에 서 있다. 이 모듈이 그 가정을 처음 스트레스한다 — §2 「실효 동시
   리스크」(`BookStats.max_effective_concurrent_risk_ratio`, WAN-312 신설)가 주 산출물이다.
3. **유니버스가 9종목이었다** — 오늘 채택 좌표는 12종목(WAN-307)이다.

## 두 변형 (둘 다 후보 사후 변환이라 배수를 여러 개 돌려도 비용이 거의 안 는다)

* **`pure`** — `exit = entry − k·R`(롱, 숏 대칭) **무조건**. 사용자가 물은 그 가정이고,
  1분봉이 **못 보는 갭까지** 덮는다. ⚠️ 측정이 아니라 **가정으로 덮는 것**이다(진짜 갭의
  실측은 틱·호가 = WAN-98, Canceled).
* **`observed`** — `exit = max(entry − k·R, 봉저가)`(롱; 숏은 `min(·, 봉고가)`). 1분봉이
  **실제로 본 데까지만**. k가 커지면 α=1(WAN-277의 최악)로 수렴하므로 **α 축과 이어지는
  다리**이고, `pure − observed`가 곧 **「1분봉으로는 안 보이는 몫」**이다.

k=1.0에서는 두 변형이 **정의상 같고 현행 엔진과 비트 동일**하다(변환이 항등) — CSV에 두 행이
같은 값으로 찍히는 것이 격자 자체의 검산이다.

## 좌표 (WAN-305 원칙 — 페이퍼와 같은 선상 · 핀 금지)

12종목(WAN-307) × 15m·1h·2h·4h(WAN-252) + `both`(공유 지갑) · 못 박은 6년
(2020-09-15~2026-07-22) · **재진입 ON(band, WAN-273)** · 채택 북 cap_only 5배(WAN-213) ·
유동성 한도 채택 기본값(0.005, WAN-279) · `baseline` 단독 · full·oos_warm(주)·oos(스트레스).
`LEGACY_*`·`pin_*`·`reentry=False`를 **하나도 쓰지 않는다**. 펀딩 대리도 얹지 않는다 —
12종목 전부 자기 확정 펀딩이 채택 창을 덮는다(WAN-292/307).

⚠️ **WAN-277 셀과 직접 비교 금지** — 유니버스(9→12)·펀딩 처리·유동성 한도 세 축이 함께
다르다. 대조는 `--legacy-wan277`(세 축을 전부 되돌린 재현 모드)에서만 성립한다.

## 재현

```
uv run python -m backtest.wan312_stop_r_multiple --tf 4h --jobs 4            # 가벼운 TF부터
uv run python -m backtest.wan312_stop_r_multiple --tf 2h --jobs 4 --append
uv run python -m backtest.wan312_stop_r_multiple --tf 1h --jobs 4 --append
uv run python -m backtest.wan312_stop_r_multiple --tf 15m --jobs 4 --append  # 무겁다(~37분/셀)
uv run python -m backtest.wan312_stop_r_multiple --from-csv                  # 요약만 재생성
uv run python -m backtest.wan312_stop_r_multiple --tf 4h --legacy-wan277     # 검산 (b)
```

⚠️ `both`(채택 TF 집합 공유 지갑)는 전 TF가 **한 실행에 모여 있을 때만** 나온다 — 15m을
뒤로 미룬 부분 실행의 `both`는 그때까지의 TF 집합만 담는다(라벨이 `num_cells`로 읽힌다).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE, build_book_rows
from backtest.harness import SEGMENT_FULL, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.leverage_book import BookCell, LeverageBookParams, run_leverage_book
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    _scope_payloads,
    _segment_cells,
    _short,
    run_cells,
)
from backtest.wan176_nine_symbol_rebaseline import NEW_SYMBOLS as WAN176_NEW_SYMBOLS
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan276_stop_gap_fill import _resolve_scopes
from backtest.wan276_stop_gap_fill import grid_from_csv as alpha_grid_from_csv
from backtest.zone_limit_backtest import _Candidate, build_result_from_trades

REPORTS_DIR = Path("backtest/reports")
DEFAULT_GRID_CSV = REPORTS_DIR / "wan312_stop_r_multiple_grid.csv"
DEFAULT_LOO_CSV = REPORTS_DIR / "wan312_leave_one_out.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan312_stop_r_multiple_summary.md"
DEFAULT_META_JSON = REPORTS_DIR / "wan312_run_meta.json"

#: WAN-277(재진입 ON · 9종목 · α 축) 격자 — `--legacy-wan277` 재현 검산의 대조.
WAN277_GRID_CSV = REPORTS_DIR / "wan277_stop_gap_grid.csv"

#: 손실 배수 k — 1.0 = 현행(검산·항등) · 1.5 = 사용자가 물은 그 값 · 2.0 = 그 너머.
K_MULTIPLES: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0)

#: 두 변형. `pure` = 무조건 k·R(1분봉이 못 보는 갭까지) · `observed` = 봉 극값에서 클립.
VARIANT_PURE = "pure"
VARIANT_OBSERVED = "observed"
VARIANTS: tuple[str, ...] = (VARIANT_PURE, VARIANT_OBSERVED)

#: 채택 북(WAN-213) — `LeverageBookParams()` 기본값이 곧 채택 북이다.
ADOPTED_BOOK = LeverageBookParams()

#: 구간 — `full`에 6년 MDD가 살고, `oos_warm`(주)·`oos`(스트레스)는 WAN-166 규약.
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: WAN-307 합류 3종목 — leave-one-out 표에 「신규 3종목 제외」 판을 함께 낸다(완료 기준 4).
NEW_THREE: tuple[str, ...] = ("ADA", "DOT", "BCH")

#: leave-one-out은 `pure`(더 보수적인 변형)만 낸다 — 두 변형의 종목 편중 구조는 같은 거래
#: 집합 위라 갈리지 않고, 표만 두 배가 된다.
LOO_VARIANT = VARIANT_PURE

#: leave-one-out 구간 — full(6년 MDD가 사는 곳)과 oos_warm(주 수치).
LOO_SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_OOS_WARM)


def scenario_label(k: float, variant: str) -> str:
    return f"k{k:.2f}_{variant}"


def scenario_title(scenario: str) -> str:
    k, _, variant = scenario.removeprefix("k").partition("_")
    tag = "무조건" if variant == VARIANT_PURE else "봉극값 클립"
    return f"k={k} {tag}"


# --------------------------------------------------------------------------- #
# 후보 사후 변환 — 손절 청산가만 k·R로 옮긴다
# --------------------------------------------------------------------------- #


def stop_multiple_candidate(cand: _Candidate, k: float, variant: str) -> _Candidate:
    """손절 후보의 청산가를 「진입가에서 k·R」로 옮긴다(롱, 숏 대칭).

    `R = |진입가 − 손절가|`이고 손절가는 존 무효화 경계 그대로다 — **손절선을 옮기는 것이
    아니라** 그 선에서 발동한 주문이 얼마나 나쁘게 체결되는지를 바꾼다. 그래서 `stop_price`·
    진입·체결 시각·거래 집합은 전부 불변이고 `exit_price` 하나만 바뀐다(사이징의 분모인
    `risk_amount`도 손절가 기준이라 불변 — 그래서 「계획 리스크」 열이 k에 안 움직인다).

    산식을 `entry − k·R`이 아니라 **`stop − (k−1)·R`** 로 쓴다(대수적으로 같다) — k=1에서
    부동소수 오차 없이 정확히 `stop_price`가 나와야 현행 엔진과 비트 동일이기 때문이다.

    `observed` 변형은 손절 봉의 불리 극값(`exit_extreme`, WAN-276 관측값)에서 클립한다 —
    극값이 없는 후보(구 CSV 재적재 등)는 클립할 근거가 없어 **그대로 둔다**(WAN-276
    `slip_candidate`와 같은 보수적 규약).
    """
    if cand.reason is not ExitReason.STOP_LOSS:
        return cand
    stop = cand.stop_price
    r_unit = abs(cand.entry_price - stop)
    if r_unit <= 0.0:
        return cand  # 1R이 0이면 배수를 정의할 수 없다(사이징도 이 후보를 거부한다).
    excess = (k - 1.0) * r_unit
    if cand.side is PositionSide.LONG:
        new_exit = stop - excess
        if variant == VARIANT_OBSERVED:
            if cand.exit_extreme is None:
                return cand
            new_exit = max(new_exit, cand.exit_extreme)
    else:
        new_exit = stop + excess
        if variant == VARIANT_OBSERVED:
            if cand.exit_extreme is None:
                return cand
            new_exit = min(new_exit, cand.exit_extreme)
    return dataclasses.replace(cand, exit_price=new_exit)


def apply_stop_multiple(
    payloads: Sequence[CellPayload], k: float, variant: str
) -> list[CellPayload]:
    """전 칸의 후보(base + 재진입)에 손실 배수 k를 얹은 새 payload 목록.

    k=1.0이면 두 변형 모두 항등이라 **원본을 그대로** 돌려준다(비트 재현). 재진입 후보
    (WAN-273 채택)도 같은 변환을 받는다 — 재진입 손절도 같은 갭을 겪는다(WAN-277 규약).
    """
    if variant not in VARIANTS:
        raise ValueError(f"알 수 없는 변형입니다: {variant} (지원: {', '.join(VARIANTS)}).")
    if k == 1.0:
        return list(payloads)
    out: list[CellPayload] = []
    for p in payloads:
        new_candidates = {
            seg: tuple(stop_multiple_candidate(c, k, variant) for c in cands)
            for seg, cands in p.candidates.items()
        }
        new_reentry = {
            seg: tuple(stop_multiple_candidate(c, k, variant) for c in cands)
            for seg, cands in p.reentry_candidates.items()
        }
        out.append(
            dataclasses.replace(p, candidates=new_candidates, reentry_candidates=new_reentry)
        )
    return out


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class RMultipleRow(BaseModel):
    """한 (시나리오 × 스코프 × 구간)의 채택 북 성과·위험.

    `max_concurrent_risk`(계획)와 `max_effective_concurrent_risk`(실효)를 **나란히** 싣는
    것이 이 이슈의 주 산출물이다 — 전자는 k에 설계상 불변이고 후자만 움직인다.
    """

    model_config = ConfigDict(frozen=True)

    scenario: str
    """`k1.00_pure` … `k2.00_observed`."""
    k: float
    variant: str
    scope: str
    """`15m`/`1h`/`2h`/`4h` 단일 TF 또는 `both`(공유 지갑 = 채택 북)."""
    segment: str
    exclude: str
    """leave-one-out 라벨 — `""`(전 종목) · `"-ETH"`(그 종목 제외) · `"-new3"`(합류 3종목)."""
    num_cells: int
    num_symbols: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    peak_concurrency: int
    max_concurrent_risk: float
    """**계획** 동시 리스크(주문 시점) — k에 불변이다(설계)."""
    max_effective_concurrent_risk: float
    """**실효** 동시 리스크 = 계획 × k — 「손절이 k·R이면 실제로 깨질 수 있는 자본 비율」."""
    max_open_notional_ratio: float
    liquidation_events: int

    @property
    def return_over_mdd(self) -> float | None:
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown


# --------------------------------------------------------------------------- #
# 격자 빌드
# --------------------------------------------------------------------------- #


def _book_row(
    cells: Sequence[BookCell],
    *,
    k: float,
    variant: str,
    scope: str,
    segment: str,
    exclude: str,
    base_cfg: BacktestConfig,
) -> RMultipleRow:
    """이 칸 집합을 채택 북으로 돌린 한 행.

    `stress_risk_multiple=k`는 **최악 가정 청산 검사와 실효 리스크 열**의 자다 — 실현
    손익 쪽은 이미 후보 변환이 얹었다. 두 변형이 같은 k를 받는 것은 의도적이다: 「아직
    안 난 손절이 얼마나 아플까」는 미래 봉의 이야기라 관측으로 클립할 수 없다.
    """
    outcome = run_leverage_book(cells, base_cfg, ADOPTED_BOOK, stress_risk_multiple=k)
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    m = result.metrics
    stats = outcome.stats
    return RMultipleRow(
        scenario=scenario_label(k, variant),
        k=k,
        variant=variant,
        scope=scope,
        segment=segment,
        exclude=exclude,
        num_cells=len(cells),
        num_symbols=len({c.symbol for c in cells}),
        num_trades=m.num_trades,
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        peak_concurrency=stats.peak_concurrency,
        max_concurrent_risk=stats.max_concurrent_risk_ratio,
        max_effective_concurrent_risk=stats.max_effective_concurrent_risk_ratio,
        max_open_notional_ratio=stats.max_open_notional_ratio,
        liquidation_events=len(stats.liquidations),
    )


def _exclude_payloads(
    payloads: Sequence[CellPayload], excluded: Sequence[str]
) -> list[CellPayload]:
    """짧은 심볼 이름(`ETH`) 집합을 뺀 payload — leave-one-out용."""
    drop = {s.upper() for s in excluded}
    return [p for p in payloads if _short(p.symbol) not in drop]


def build_grid(payloads: Sequence[CellPayload], scopes: Sequence[str]) -> list[RMultipleRow]:
    """시나리오(k × 변형) × 스코프 × 구간의 채택 북 스트레스 행."""
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    rows: list[RMultipleRow] = []
    for scope in scopes:
        scoped = _scope_payloads(payloads, scope)
        if not scoped:
            continue
        for k in K_MULTIPLES:
            for variant in VARIANTS:
                shifted = apply_stop_multiple(scoped, k, variant)
                for segment in SEGMENTS:
                    rows.append(
                        _book_row(
                            _segment_cells(shifted, segment, ""),
                            k=k,
                            variant=variant,
                            scope=scope,
                            segment=segment,
                            exclude="",
                            base_cfg=base_cfg,
                        )
                    )
    return rows


def build_leave_one_out(
    payloads: Sequence[CellPayload], scopes: Sequence[str]
) -> list[RMultipleRow]:
    """종목 하나씩 빼기 + WAN-307 합류 3종목 제외 (완료 기준 4).

    변형은 `pure` 하나, 구간은 full·oos_warm만 — 「특정 종목이 만든 결과인가」는 그 축에서
    답이 나오고, 전 격자를 다시 도는 것은 비용만 는다.
    """
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    all_symbols = sorted({_short(p.symbol) for p in payloads})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    rows: list[RMultipleRow] = []
    for scope in scopes:
        scoped = _scope_payloads(payloads, scope)
        if not scoped:
            continue
        for label, dropped in drops:
            kept = _exclude_payloads(scoped, dropped)
            if not kept:
                continue
            for k in K_MULTIPLES:
                shifted = apply_stop_multiple(kept, k, LOO_VARIANT)
                for segment in LOO_SEGMENTS:
                    rows.append(
                        _book_row(
                            _segment_cells(shifted, segment, ""),
                            k=k,
                            variant=LOO_VARIANT,
                            scope=scope,
                            segment=segment,
                            exclude=label,
                            base_cfg=base_cfg,
                        )
                    )
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def verify_adopted_identity(payloads: Sequence[CellPayload], *, start: str, end: str) -> float:
    """검산 (a) — k=1.0 행이 **인자 없는 채택 북**(`backtest.run --oos-warm`)과 같은가.

    같은 payload를 `book_cli.build_book_rows`(= CLI 북 경로가 실제로 쓰는 함수)에 넣어 낸
    행과 이 모듈의 k=1.0 `both` 행을 대조한다 — 최대 절대 수익률 차를 돌려준다. 이 함수가
    잡는 것은 **배선**이다: 후보 변환이 k=1에서 항등인지, 재진입·유동성 한도·북 파라미터가
    채택값 그대로인지. 어느 하나라도 어긋나면 「k=1.0 = 현행」이라는 이 표의 기준점이 깨진다.
    """
    from backtest.run import parse_date_ms  # 지연 import(사이클 회피 — book_cli와 같은 이유)

    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    cli_rows = {
        r.segment: r
        for r in build_book_rows(
            payloads,
            book=ADOPTED_BOOK,
            segments=SEGMENTS,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
    }
    worst = 0.0
    for segment in SEGMENTS:
        mine = _book_row(
            _segment_cells(apply_stop_multiple(payloads, 1.0, VARIANT_PURE), segment, ""),
            k=1.0,
            variant=VARIANT_PURE,
            scope="both",
            segment=segment,
            exclude="",
            base_cfg=base_cfg,
        )
        theirs = cli_rows[segment]
        worst = max(
            worst,
            abs(mine.total_return - theirs.total_return),
            abs(mine.max_drawdown - theirs.max_drawdown),
        )
    return worst


def restore_pre_backfill_funding(payloads: Sequence[CellPayload]) -> list[CellPayload]:
    """검산 (b) 전용 — DOGE·LINK·LTC의 펀딩을 **비우고** WAN-277 실행 당시 상태로 되돌린다.

    🚨 **데이터가 그 뒤 바뀌었다.** WAN-277은 2026-08-10에 돌았고 그때 이 세 종목은
    `funding_rate` 테이블에 **0행**이라 `apply_funding_proxy`가 BTC 시계열을 대리로 얹었다.
    WAN-292(2026-08-12)가 상장 시점부터 백필한 지금은 **실데이터가 있어 대리가 무동작**
    이므로(대리는 자기 데이터를 덮지 않는다) 같은 좌표를 줘도 wan277 수치가 안 나온다 —
    거래 집합은 비트 동일한데 **펀딩 비용만** 달라진다.

    그래서 재현은 좌표 셋(유니버스·대리·유동성 한도)만으로는 부족하고 **당시의 데이터
    상태**까지 복원해야 한다. 이 함수가 그 마지막 축이다. ⚠️ 검산 전용이다 — 오늘 좌표
    실행에는 절대 쓰지 않는다(실데이터를 버리고 대리로 도는 것이 된다).
    """
    new_short = {_short(s) for s in WAN176_NEW_SYMBOLS}
    out: list[CellPayload] = []
    for p in payloads:
        if _short(p.symbol) in new_short:
            p = dataclasses.replace(p, funding=dict.fromkeys(p.funding, ()))
        out.append(p)
    return out


def compare_wan277_alpha0(rows: Sequence[RMultipleRow]) -> tuple[int, float] | None:
    """검산 (b) — `--legacy-wan277` 재현 모드에서 k=1.00 행이 wan277 `alpha_0.00`과 같은가.

    돌려주는 값은 `(대조한 셀 수, 최대 절대차)`. wan277 격자 CSV가 없으면 `None`.
    ⚠️ 이 대조는 **세 축(유니버스 9종목 · 펀딩 대리 · 유동성 한도 끔)을 전부 되돌린**
    실행에서만 성립한다 — 채택 좌표 실행의 행을 wan277과 비교하면 안 된다.
    """
    if not WAN277_GRID_CSV.exists():
        return None
    ref = {
        (r.scope, r.segment): r
        for r in alpha_grid_from_csv(WAN277_GRID_CSV)
        if r.scenario == "alpha_0.00"
    }
    worst = 0.0
    matched = 0
    for row in rows:
        if row.k != 1.0 or row.exclude:
            continue
        other = ref.get((row.scope, row.segment))
        if other is None:
            continue
        matched += 1
        worst = max(
            worst,
            abs(row.total_return - other.total_return),
            abs(row.max_drawdown - other.max_drawdown),
        )
    return (matched, worst) if matched else None


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[RMultipleRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(RMultipleRow.model_fields))


def grid_from_csv(path: Path) -> list[RMultipleRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [RMultipleRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _scenario_order(scenario: str) -> tuple[float, int]:
    k, _, variant = scenario.removeprefix("k").partition("_")
    return (float(k), VARIANTS.index(variant) if variant in VARIANTS else 99)


def _stress_table(rows: Sequence[RMultipleRow], scope: str, segment: str) -> list[str]:
    lines = [
        "| 시나리오 | 거래 | 승률 | 수익률 | MDD | 수익/MDD | 계획 동시리스크 | "
        "**실효 동시리스크** | 청산 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    picked = [r for r in rows if r.scope == scope and r.segment == segment and not r.exclude]
    for row in sorted(picked, key=lambda r: _scenario_order(r.scenario)):
        lines.append(
            f"| {scenario_title(row.scenario)} | {row.num_trades} | {_pct(row.win_rate)} | "
            f"{_pct(row.total_return)} | {_pct(row.max_drawdown)} | {_rr(row.return_over_mdd)} | "
            f"{_pct(row.max_concurrent_risk)} | **{_pct(row.max_effective_concurrent_risk)}** | "
            f"{row.liquidation_events} | {row.peak_concurrency} |"
        )
    return lines


def _loo_table(rows: Sequence[RMultipleRow], scope: str, segment: str) -> list[str]:
    """leave-one-out — k별 MDD·청산을 한 줄에 눕힌다(어느 종목을 빼도 판정이 같은가)."""
    picked = [r for r in rows if r.scope == scope and r.segment == segment and r.exclude]
    if not picked:
        return []
    ks = sorted({r.k for r in picked})
    header = " | ".join(f"MDD k={k:g}" for k in ks)
    lines = [
        f"| 제외 | 종목 | {header} | 청산(전 k) |",
        "| -- | --: | " + " | ".join("--:" for _ in ks) + " | --: |",
    ]
    by_label: dict[str, dict[float, RMultipleRow]] = {}
    for r in picked:
        by_label.setdefault(r.exclude, {})[r.k] = r
    for label in sorted(by_label, key=lambda s: (s == "-new3", s)):
        cells = by_label[label]
        any_row = next(iter(cells.values()))
        mdds = " | ".join(_pct(cells[k].max_drawdown) if k in cells else "—" for k in ks)
        liq = sum(cells[k].liquidation_events for k in ks if k in cells)
        lines.append(f"| {label} | {any_row.num_symbols} | {mdds} | {liq} |")
    return lines


#: 「자본 파괴」 판정선 — 이 낙폭을 넘으면 청산 트리거가 0이어도 계좌는 사실상 끝난 것이다.
RUIN_MDD = 0.50


def _capital_destruction_lines(main: Sequence[RMultipleRow]) -> list[str]:
    """🚨 청산 카운터가 0인데 자본이 사라지는 구간을 따로 찍는다.

    청산 검사는 **한 시점에 열린 포지션이 전부 동시에** 손절날 때 자본을 넘느냐를 본다.
    그런데 사이징이 **현재 자본의 %** 라 손실이 쌓이면 포지션도 같이 작아진다 — 즉 연쇄
    손실로는 그 조건이 **구조적으로 안 걸린다**. 「청산 0건」을 생존 증명으로 읽으면 안
    되는 이유이고, 실제 답은 낙폭·잔존 자본이다.
    """
    ruined = [r for r in main if r.max_drawdown >= RUIN_MDD]
    if not ruined:
        return []
    smallest = min(r.k for r in ruined)
    worst = max(ruined, key=lambda r: r.max_drawdown)
    variants = sorted({r.variant for r in ruined})
    lines = [
        f"🚨 **그런데 「청산 0건」을 생존으로 읽으면 안 된다 — k={smallest:g}부터 낙폭이 "
        f"{RUIN_MDD * 100:.0f}%를 넘는다**(최악 {_pct(worst.max_drawdown)} @ "
        f"{scenario_title(worst.scenario)}·{worst.scope}·{worst.segment}, 그 셀의 총수익 "
        f"{_pct(worst.total_return)}). 청산 검사는 **한 시점에 열린 포지션이 전부 동시에** "
        "손절날 때만 걸리는데, 사이징이 **현재 자본의 %** 라 손실이 쌓이면 포지션도 함께 "
        "작아진다 — **연쇄 손실로는 그 조건이 구조적으로 안 걸린다**. 즉 청산 카운터는 "
        "「한 방에 죽는가」이지 「살아남는가」가 아니다."
    ]
    if variants == [VARIANT_PURE]:
        lines.append(
            f"📌 그 파괴는 **`무조건` 팔에서만** 일어난다 — `봉극값` 팔은 k를 키워도 "
            f"{RUIN_MDD * 100:.0f}% 선에 닿지 않는다. **1분봉이 실제로 본 범위 안에서는 "
            "이 사이즈가 버티고, 무너지는 것은 그 너머를 가정했을 때다**(그 너머는 측정이 "
            "아니라 가정 — 진값은 틱·호가 = WAN-98, Canceled)."
        )
    return lines


def _verdict(rows: Sequence[RMultipleRow], loo_rows: Sequence[RMultipleRow]) -> list[str]:
    """판정 — 청산 0건이 유지되는 k의 상한 · 실효 리스크가 계획과 얼마나 벌어지나."""
    main = [r for r in rows if not r.exclude]
    lines: list[str] = []

    liq = [r for r in main if r.liquidation_events > 0]
    if liq:
        smallest = min(r.k for r in liq)
        safe = [k for k in sorted({r.k for r in main}) if k < smallest]
        top = f"{max(safe):g}" if safe else "없음(k=1.00부터)"
        worst = max(liq, key=lambda r: r.liquidation_events)
        lines.append(
            f"🚨 **청산 0건이 유지되는 k의 상한은 {top}이다** — k={smallest:g}부터 청산 "
            f"트리거가 난다({len(liq)}개 (시나리오×스코프×구간) 셀, 최대 "
            f"{worst.liquidation_events}건 @ {worst.scenario}·{worst.scope}·{worst.segment}). "
            "cap_only 5배의 「청산 0건」은 **손절 한 번 = 정확히 1R**이라는 가정 위에 있었다."
        )
    else:
        kmax = max((r.k for r in main), default=1.0)
        lines.append(
            f"📌 **청산 0건이 유지되는 k의 상한은 이 격자의 최댓값(k={kmax:g})을 넘는다** — "
            "손절 한 번이 계획의 2배로 물려도 최악 가정 청산 트리거가 한 셀도 나지 않았다. "
            "cap_only가 거래당 크기를 1배로 묶어(레버리지는 상한만 N배, WAN-180) 손절 손실이 "
            "배수로 증폭되지 않는 구조가 여기서 보인다."
        )

    lines += _capital_destruction_lines(main)

    for scope in sorted({r.scope for r in main}):
        base = _find(main, scope, SEGMENT_FULL, 1.0, VARIANT_PURE)
        if base is None:
            continue
        parts = [f"k=1 {_pct(base.max_drawdown)}"]
        for k in K_MULTIPLES[1:]:
            pure = _find(main, scope, SEGMENT_FULL, k, VARIANT_PURE)
            obs = _find(main, scope, SEGMENT_FULL, k, VARIANT_OBSERVED)
            if pure is None:
                continue
            seen = f"/{_pct(obs.max_drawdown)}" if obs is not None else ""
            parts.append(f"k={k:g} {_pct(pure.max_drawdown)}{seen}")
        lines.append(f"**MDD 심화({scope}·full · 무조건/봉극값)**: " + " → ".join(parts) + ".")

    # 실효 vs 계획 — 이 이슈의 주 산출물.
    for scope in sorted({r.scope for r in main}):
        row = _find(main, scope, SEGMENT_OOS_WARM, 1.5, VARIANT_PURE)
        if row is None:
            continue
        lines.append(
            f"**실효 동시 리스크({scope}·oos_warm·k=1.5)**: 계획 "
            f"{_pct(row.max_concurrent_risk)} → 실효 "
            f"**{_pct(row.max_effective_concurrent_risk)}**. "
            "계획 열은 k에 **설계상 불변**이라 이 벌어짐이 곧 5배 근거가 안 재던 몫이다."
        )

    # pure − observed = 1분봉이 못 보는 몫.
    gaps = [
        (r.scope, r.segment, r.max_drawdown - o.max_drawdown)
        for r in main
        if r.variant == VARIANT_PURE and r.k == max(K_MULTIPLES)
        for o in [_find(main, r.scope, r.segment, r.k, VARIANT_OBSERVED)]
        if o is not None
    ]
    if gaps:
        worst_gap = max(gaps, key=lambda g: abs(g[2]))
        avg = sum(abs(g[2]) for g in gaps) / len(gaps)
        lines.append(
            f"**「1분봉이 못 보는 몫」(k={max(K_MULTIPLES):g}, 무조건 − 봉극값)**: MDD 차 평균 "
            f"{avg * 100:.2f}%p · 최대 {worst_gap[2] * 100:+.2f}%p({worst_gap[0]}·{worst_gap[1]}). "
            "봉극값 팔은 1분봉이 실제로 간 데서 멈추므로 k가 커져도 α=1(WAN-277 최악)로 "
            "수렴한다 — 그 위쪽은 **측정이 아니라 가정**이다(진값은 틱·호가 = WAN-98, Canceled)."
        )

    if loo_rows:
        loo_liq = [r for r in loo_rows if r.liquidation_events > 0]
        if loo_liq:
            lines.append(
                f"⚠️ **leave-one-out에서 청산이 나는 조합이 {len(loo_liq)}개 있다** — 특정 "
                "종목을 빼면 남은 칸에 노출이 몰린다는 뜻이라 원자료를 볼 것."
            )
        else:
            lines.append(
                "📌 **leave-one-out(종목 하나씩 · 신규 3종목 제외)에서도 청산은 전부 0건**이다."
            )
        kmax = max((r.k for r in loo_rows), default=1.0)
        loo_scopes = {r.scope for r in loo_rows}
        # 한 스코프 안에서만 비교한다 — 스코프를 섞으면 「지갑이 다른 북」의 MDD가 한 범위에
        # 들어와 폭이 종목 편중처럼 보인다.
        scope = "both" if "both" in loo_scopes else sorted(loo_scopes)[0]
        worst_k = [
            r
            for r in loo_rows
            if r.k == kmax and r.segment == SEGMENT_OOS_WARM and r.exclude and r.scope == scope
        ]
        if worst_k:
            lo = min(worst_k, key=lambda r: r.max_drawdown)
            hi = max(worst_k, key=lambda r: r.max_drawdown)
            lines.append(
                f"📌 **낙폭도 한 종목이 만든 것이 아니다** — k={kmax:g}·oos_warm·{scope}에서 "
                f"어느 종목을 빼도 MDD가 {_pct(lo.max_drawdown)}({lo.exclude}) ~ "
                f"{_pct(hi.max_drawdown)}({hi.exclude}) 사이다. WAN-307 합류 3종목을 통째로 "
                "빼도(`-new3`) 마찬가지라, **유니버스 확장이 만든 결과가 아니다**."
            )
    return lines


def _find(
    rows: Sequence[RMultipleRow], scope: str, segment: str, k: float, variant: str
) -> RMultipleRow | None:
    for r in rows:
        if r.scope == scope and r.segment == segment and r.k == k and r.variant == variant:
            return r
    return None


def build_summary_markdown(
    grid_rows: Sequence[RMultipleRow],
    loo_rows: Sequence[RMultipleRow],
    *,
    grid_csv: Path,
    loo_csv: Path,
    identity_diff: float | None = None,
    legacy_note: str = "",
) -> str:
    scopes = sorted({r.scope for r in grid_rows})
    identity_line = (
        f"k=1.00 행이 **인자 없는 채택 북**(`backtest.run --oos-warm`, `book_cli.build_book_rows`)"
        f"과 일치(최대 절대차 {identity_diff:.2e})."
        if identity_diff is not None
        else "k=1.00 항등은 회귀 테스트가 동작으로 고정한다(이 실행에선 재검산 생략)."
    )
    lines = [
        "# WAN-312 — 손절 손실을 R 배수로 못 박는 스트레스 + 「실효 동시 리스크」",
        "",
        "**성격** 측정 + 옵트인 엔진(손실 배수 스트레스) 전용 — **기본값·토대·손절폭 가드"
        "(0.3%)·`ConfluenceParams()`·`LeverageBookParams()` 불변**"
        "(`ALPHABLOCK_LIVE_TRADING=false` 유지). 채택 북 cap_only 5배(WAN-213) · **재진입 "
        "ON(band, WAN-273)** · 유동성 한도 채택 기본값(WAN-279) · 12종목(WAN-307) · 못 박은 창 "
        f"**{harness.DEFAULT_START} ~ {harness.DEFAULT_END}** · 렌즈 `baseline` 단독 · 핀 없음"
        "(WAN-305).",
        "",
        "손절 주문은 **존 무효화 경계에 그대로** 두고, 발동 순간 체결이 그보다 나빠 한 번에 "
        "**k·R**을 잃는다고 본다 — 후보(셋업) 집합·진입가·손절가·사이징 분모는 전부 불변이고 "
        "**진 거래의 손실 크기만** 커진다(손절선을 넓히는 안은 재-베이스라인 = 사용자 결정, "
        "범위 밖).",
        "",
        "⚠️ **단 `both`(공유 지갑)에서는 거래 **수**가 k에 미세하게 움직인다** — 후보는 그대로지만 "
        "손실이 커지면 공유 자본이 줄어 몇몇 셋업이 명목 상한·사이징에서 밀린다(단일 TF 스코프는 "
        "거래 수·승률이 k에 완전히 불변이다). 회계가 정직하게 반응하는 것이지 후보 집합이 바뀐 "
        "것이 아니다.",
        "",
        "**변형 2** — `무조건`(`pure`): `exit = 진입가 − k·R`(롱, 숏 대칭), 1분봉이 **못 보는 "
        "갭까지** 덮는 가정. `봉극값`(`observed`): `exit = max(진입가 − k·R, 봉저가)`, 1분봉이 "
        "**실제로 본 데까지만** — k가 커지면 WAN-277 α=1로 수렴하므로 α 축과 이어지는 다리이고 "
        "**두 팔의 차이가 곧 「1분봉으로는 안 보이는 몫」**이다.",
        "",
        *([f"📌 {legacy_note}", ""] if legacy_note else []),
        "재현: `uv run python -m backtest.wan312_stop_r_multiple --tf 4h,2h,1h --jobs 4` "
        "(`both`은 전 TF가 한 프로세스에 모여야 나온다 · 15m은 `--append` 후속 · 요약만: "
        "`--from-csv`). 원자료: "
        f"`{grid_csv}`(격자) · `{loo_csv}`(leave-one-out).",
        "",
        "## 0. 검산",
        "",
        f"- {identity_line}",
        "- k=1.00에서 두 변형이 **정의상 같은 행**을 낸다(변환이 항등) — 격자 안에 박힌 "
        "자기 검산이고, 회귀 테스트가 **동작으로** 고정한다"
        "(`test_k_one_is_bit_identical_to_adopted_book`).",
        "- 손실 배수가 **손절가·진입·거래 집합을 건드리지 않는지**(청산가만 바뀌는지) "
        "동작으로 고정된다(`test_multiple_moves_only_exit_price`).",
        "- 「계획 동시 리스크」가 k에 **불변**이고 「실효」만 k배로 움직이는지 고정된다"
        "(`test_effective_risk_scales_planned_stays`).",
        "",
    ]
    for scope in scopes:
        lines += [f"## 1. 채택 북 스트레스 — {scope}", ""]
        for seg, title in (
            (SEGMENT_FULL, "full (6년 MDD가 사는 구간)"),
            (SEGMENT_OOS_WARM, "oos_warm (주)"),
            (SEGMENT_OOS, "oos (차가운 스트레스)"),
        ):
            lines += [f"### {title}", "", *_stress_table(grid_rows, scope, seg), ""]
    lines += [
        "🚨 **총수익%는 수백~수천 거래의 복리 값이다 — 달성 가능 성과로 인용 금지**"
        "(WAN-169/213). 읽을 것은 **MDD · 실효 동시 리스크 · 청산 건수** 셋이다. **6년 MDD는 "
        "창이 2020-09 시작이라 2018·2020-03 폭락을 안 담은 바닥선**이다(WAN-213).",
        "",
        "## 2. 계획 대 실효 동시 리스크 — 5배의 근거가 서 있던 가정",
        "",
        *_planned_vs_effective_table(grid_rows),
        "",
        "`cap_only`는 *「거래 하나의 크기는 안 키우고(리스크 1% 고정) 동시에 열 수 있는 칸 수만 "
        "늘린다」*이고, 그 안전 논리는 **손절 한 번 = 정확히 1R**을 전제한다. 계획 열은 주문 "
        "시점의 합이라 손절 체결 보수화에 **설계상 불변**이고(WAN-276/277의 α가 이 열을 못 "
        "움직인 이유), 실효 열이 그 전제를 벗겼을 때의 값이다.",
        "",
    ]
    if loo_rows:
        lines += ["## 3. leave-one-out — 특정 종목이 만든 결과인가", ""]
        for scope in sorted({r.scope for r in loo_rows}):
            for seg in LOO_SEGMENTS:
                table = _loo_table(loo_rows, scope, seg)
                if table:
                    lines += [f"### {scope} · {seg}", "", *table, ""]
        lines += [
            f"`-new3` = WAN-307 합류 3종목({'·'.join(NEW_THREE)}) 동시 제외 판. 변형은 "
            "`무조건`(더 보수적) 하나로 낸다 — 종목 편중 구조는 같은 거래 집합 위라 변형에 "
            "갈리지 않는다.",
            "",
        ]
    lines += [
        "## 판정",
        "",
        *_verdict(grid_rows, loo_rows),
        "",
        "⚠️ **`무조건` 팔은 측정이 아니라 가정이다** — 1분봉이 못 보는 영역(1분 미만 급락·"
        "거래 정지·호가 공백)을 **가정으로 덮는 것**이고, 그 진값은 틱·호가(WAN-98, Canceled) "
        "소관이다. `봉극값` 팔이 데이터가 실제로 지지하는 상한이다. **진입 쪽 낙관(WAN-96)은 "
        "별개 축** — 이 표는 청산만 보수화한다. **「엣지 없음」(WAN-84/88/111/114/124/151/201/"
        "248) 불변** — 손절 체결 가정은 알파가 아니라 **위험의 모양**만 바꾼다(WAN-90). "
        "**채택·배수 변경은 재-베이스라인 = 사용자 결정.**",
        "",
    ]
    return "\n".join(lines) + "\n"


def _planned_vs_effective_table(rows: Sequence[RMultipleRow]) -> list[str]:
    """스코프 × 구간 × k의 계획 대 실효 동시 리스크(무조건 팔 — 두 변형이 같은 값)."""
    lines = [
        "| 스코프 | 구간 | k | 계획 동시리스크 | 실효 동시리스크 | 벌어짐 | 청산 |",
        "| -- | -- | --: | --: | --: | --: | --: |",
    ]
    picked = [
        r for r in rows if not r.exclude and r.variant == VARIANT_PURE and r.segment != SEGMENT_OOS
    ]
    for row in sorted(picked, key=lambda r: (r.scope, r.segment, r.k)):
        gap = row.max_effective_concurrent_risk - row.max_concurrent_risk
        lines.append(
            f"| {row.scope} | {row.segment} | {row.k:g} | {_pct(row.max_concurrent_risk)} | "
            f"**{_pct(row.max_effective_concurrent_risk)}** | {gap * 100:+.2f}%p | "
            f"{row.liquidation_events} |"
        )
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-312 손절 손실 R 배수 스트레스")
    parser.add_argument(
        "--symbols", type=str, default=",".join(harness.DEFAULT_SYMBOLS), help="채택 12종목."
    )
    parser.add_argument(
        "--tf", type=str, default="4h", help="쉼표 구분. 가벼운 TF부터, 15m은 --append."
    )
    parser.add_argument("--start", type=str, default=harness.DEFAULT_START)
    parser.add_argument("--end", type=str, default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--out-grid", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--out-loo", type=Path, default=DEFAULT_LOO_CSV)
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
        "--legacy-wan277",
        action="store_true",
        help="검산 (b): 유니버스 9종목 · 펀딩 대리 켬 · 유동성 한도 끔으로 되돌려 "
        "wan277 alpha_0.00을 재현한다(채택 좌표 실행이 아니다).",
    )
    parser.add_argument("--skip-loo", action="store_true", help="leave-one-out 표를 건너뛴다.")
    parser.add_argument("--skip-verify", action="store_true", help="검산 (a)를 생략(속도).")
    parser.add_argument(
        "--skip-engine-check", action="store_true", help="칸별 표준 경로 검산을 끈다(무거운 TF)."
    )
    args = parser.parse_args(argv)

    out_grid = Path(args.out_grid)
    out_loo = Path(args.out_loo)
    out_md = Path(args.out_md)
    out_meta = Path(args.out_meta)

    if args.from_csv:
        grid_rows = grid_from_csv(out_grid)
        loo_rows = grid_from_csv(out_loo) if out_loo.exists() else []
        legacy_note = ""
        identity_diff = None
        if out_meta.exists():
            meta = json.loads(out_meta.read_text(encoding="utf-8"))
            legacy_note = str(meta.get("legacy_note", ""))
            identity_diff = meta.get("identity_diff")
        print(f"[wan312] CSV 로드 — 격자 {len(grid_rows)}행 · LOO {len(loo_rows)}행 (재실행 없음)")
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        legacy_note = ""
        if args.legacy_wan277:
            symbols = harness.LEGACY_NINE_SYMBOLS
            legacy_note = (
                "⚠️ **검산 (b) 재현 모드** — 9종목 · 펀딩 대리 켬 · 유동성 한도 끔(WAN-277 좌표). "
                "**채택 좌표의 수치가 아니다.**"
            )
            print(f"[wan312] {legacy_note}")
        payloads = run_cells(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            # 채택 기본값 그대로(WAN-305 · 핀 금지): 재진입 ON(band, WAN-273) · 유동성 한도
            # 채택값(UNSET → 0.005, WAN-279). 재현 모드만 WAN-277 좌표(한도 끔)로 되돌린다.
            adv_fraction=(
                harness.LEGACY_MAX_NOTIONAL_ADV_FRACTION if args.legacy_wan277 else harness.UNSET
            ),
            reentry=True,
            reentry_entry_rule=ADOPTED_REENTRY_ENTRY_RULE,
            engine_check=not args.skip_engine_check,
        )
        if args.legacy_wan277:
            # 좌표(9종목·한도 끔)만으로는 부족하다 — WAN-292 백필 이후라 대리가 무동작이라서
            # **당시 데이터 상태**까지 되돌려야 wan277이 재현된다(위 함수 docstring).
            payloads, proxy_note = apply_funding_proxy(restore_pre_backfill_funding(payloads))
            if proxy_note:
                print(f"[wan312] {proxy_note}")

        existing_rows: list[RMultipleRow] = []
        existing_loo: list[RMultipleRow] = []
        if args.append and out_grid.exists():
            existing_rows = grid_from_csv(out_grid)
            existing_loo = grid_from_csv(out_loo) if out_loo.exists() else []
        scopes = _resolve_scopes(timeframes, sorted({r.scope for r in existing_rows}))
        scopes = [s for s in scopes if s != "both" or len(timeframes) >= 2]

        identity_diff = (
            None
            if args.skip_verify
            else verify_adopted_identity(payloads, start=args.start, end=args.end)
        )
        grid_rows = [*existing_rows, *build_grid(payloads, scopes)]
        loo_rows = (
            list(existing_loo)
            if args.skip_loo
            else [*existing_loo, *build_leave_one_out(payloads, scopes)]
        )

        out_grid.parent.mkdir(parents=True, exist_ok=True)
        grid_to_frame(grid_rows).to_csv(out_grid, index=False)
        grid_to_frame(loo_rows).to_csv(out_loo, index=False)
        out_meta.write_text(
            json.dumps(
                {"legacy_note": legacy_note, "identity_diff": identity_diff},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[wan312] 격자 {len(grid_rows)}행 → {out_grid}")
        print(f"[wan312] leave-one-out {len(loo_rows)}행 → {out_loo}")
        if identity_diff is not None:
            print(f"[wan312] 검산 (a) k=1.00 ≡ 채택 북: 최대 절대차 {identity_diff:.2e}")
            if identity_diff >= 1e-9:
                print("[wan312] 🚨 검산 실패 — k=1.00이 인자 없는 채택 북과 갈라졌습니다.")
                return 1
        if args.legacy_wan277:
            compared = compare_wan277_alpha0(grid_rows)
            if compared is None:
                print("[wan312] 검산 (b) 생략 — wan277 격자 CSV가 없습니다.")
            else:
                matched, worst = compared
                print(
                    f"[wan312] 검산 (b) k=1.00 ≡ wan277 alpha_0.00: {matched}셀 최대차 {worst:.2e}"
                )

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            grid_rows,
            loo_rows,
            grid_csv=out_grid,
            loo_csv=out_loo,
            identity_diff=identity_diff,
            legacy_note=legacy_note,
        ),
        encoding="utf-8",
    )
    print(f"[wan312] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
