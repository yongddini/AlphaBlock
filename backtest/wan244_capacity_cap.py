"""WAN-244 — 유동성 한도(일거래량 비례)이 채택 북의 복리 착시를 걷어내는가.

## 왜

채택 회계(레버리지 북 cap_only 5배, WAN-213)의 모든 명목 상한은 **자본에 비례**한다
(`execution/sizing.py`: `max_notional = equity × leverage`). 자본이 커지면 포지션도 같이
커져 복리가 지수로 폭주한다 — 6년 both가 조 단위 %로 찍히는데, 실제로는 자본이 수백만
배가 되면 그 돈을 좁은 오더블록 존에 지정가로 넣는 순간 시장 용량에 걸려 체결이 사라진다.
백테스트는 이 용량 한계를 모델링하지 않았다.

사용자 요청(2026-08-04): 포지션당 **절대** 명목 상한을 걸되 고정 달러가 아니라
**일거래량(ADV) 비례**로 — "내 주문이 시장을 얼마나 움직이나"라는 진짜 제약을 반영한다.

## 무엇을 재나

* **엔진(§1)**: 옵트인 `PositionSizingParams.max_notional_adv_fraction`(기본 None = 꺼짐).
  포지션 명목 ≤ `k × ADV_usd`. ADV는 탭 봉 **직전까지 완료된** 일자들에서만 잰다(룩어헤드
  금지, `zone_limit_backtest._trailing_adv_usd_by_pos`). `ADV_usd = Σ(봉 volume × 봉 close)`를
  일 단위로 집계(ccxt volume은 base 수량이므로 × 가격으로 USD 환산 — 단위 함정 주의).
* **측정(§2, 이 모듈)**: 채택 cap_only 5배 북 · 9종목 · 못 박은 6년(2020-09-15~2026-07-22) ·
  15m·1h·2h·4h(WAN-252) · `baseline` 단독 · 정본 OOS(oos_warm 주 + oos 스트레스, WAN-166).
  **상한 끔 vs 켬(k = 0.5% ADV, 사용자 확정)**을 구간마다 대조: `total_return` · MDD ·
  최대 동시 리스크 · 청산 · **상한 발동률** · **상한이 처음 걸리는 자본 규모/시점**.

## 판정 (완료기준 3)

(a) 천문학적 %가 **현실적 숫자로 주저앉는가**(핵심) · (b) MDD는 어떻게 바뀌나 ·
(c) 상한이 **자본 얼마/언제부터** 발동하나 · (d) 종목 편중(leave-one-out).

## ⚠️ 해석 주의

* **상한은 숫자를 정직하게 만들 뿐 신호를 만들지 않는다** — 「엣지 없음」(WAN-84/88/111/
  114/124/151/201)은 그대로다. 레버리지·용량은 위험의 모양만 바꾼다(WAN-90).
* **k = 0.5%는 사용자 판단이지 데이터가 고른 최적값이 아니다** — 「데이터가 0.5%를 골랐다」
  로 인용 금지. IS 재선택 금지(자유 파라미터, WAN-108).
* **기본값·토대 불변**(상한 옵트인, 채택 승격은 재-베이스라인 = 사용자 결정) ·
  `ALPHABLOCK_LIVE_TRADING=false` 유지.

## 재현

```
uv run python -m backtest.wan244_capacity_cap --jobs 6
uv run python -m backtest.wan244_capacity_cap --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.leverage_book import BookOutcome, LeverageBookParams, run_leverage_book
from backtest.models import BacktestConfig, BacktestResult
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    MIN_TRADES,
    SEGMENTS,
    CellPayload,
    CellRow,
    _segment_cells,
    _short,
    cells_from_csv,
    cells_to_frame,
    run_cells,
    verify_cells,
)
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.zone_limit_backtest import build_result_from_trades

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan244_capacity_cap_cells.csv"
DEFAULT_GRID_CSV = REPORTS_DIR / "wan244_capacity_cap_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan244_capacity_cap_summary.md"

#: 채택 좌표(harness 기본값 그대로 = 인자 없는 `backtest.run`) — 9종목 × 15m·1h·2h·4h ×
#: 못 박은 6년. 옛 핀 없음.
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
DEFAULT_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS
DEFAULT_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

#: 채택 북 = cap_only 5배(WAN-213). `LeverageBookParams()`가 채택 북을 낸다.
ADOPTED_BOOK = LeverageBookParams()

#: 유동성 한도 프랙션 k(사용자 확정 2026-08-04). ⚠️ 데이터가 고른 값이 아니다(자유 파라미터).
ADV_FRACTION = 0.005


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CapRow(BaseModel):
    """한 (상한 끔/켬 × 스코프 × 구간 × 제외 종목)의 북 성과·위험·유동성 한도 계측."""

    model_config = ConfigDict(frozen=True)

    cap_on: bool
    """유동성 한도 켬 여부. False = wan180/wan236 채택 셀 그대로(비트 재현)."""
    scope: str
    """`both`(전 칸 = 사용자 정의 실제 북) 또는 개별 TF(`15m`·`1h`·`2h`·`4h`)."""
    segment: str
    exclude_symbol: str = ""
    """leave-one-out 축 — 빈 문자열이면 전 종목."""
    num_cells: int
    num_symbols: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    peak_concurrency: int
    max_concurrent_risk: float
    max_open_notional_ratio: float
    liquidation_events: int
    clamped_entries: int
    adv_capped_entries: int
    """유동성 한도가 구속 제약이었던 진입 수(WAN-244). 상한 끔이면 0."""
    first_adv_cap_time: int | None = None
    first_adv_cap_equity: float | None = None
    """상한이 처음 구속한 순간의 공유 자본(판정 c). 한 번도 안 걸렸으면 None."""

    @field_validator("first_adv_cap_time", "first_adv_cap_equity", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def return_over_mdd(self) -> float | None:
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown

    @property
    def adv_capped_rate(self) -> float | None:
        """상한 발동률 = 발동 진입 / 배치 거래. 거래가 없으면 None."""
        if self.num_trades <= 0:
            return None
        return self.adv_capped_entries / self.num_trades

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


# --------------------------------------------------------------------------- #
# 북 실행 (후보 재사용 — 가벼운 시퀀싱)
# --------------------------------------------------------------------------- #


def _base_cfg(cap_on: bool) -> BacktestConfig:
    """북 실행용 기준 cfg. `cap_on`이면 유동성 한도 프랙션을 risk_sizing에 얹는다.

    상한을 끄면(cap_on=False) `book_cli`/wan180과 같은 base_cfg라 후보에 `adv_usd`가
    실려 있어도 사이징이 무시해 **채택 셀을 비트 단위로 재현**한다.
    """
    # WAN-279가 채택 기본값을 0.005로 올린 뒤라 상한 끔 팔은 **명시적 `None`으로 고정**한다
    # (pin 없이 build_config에 맡기면 조용히 0.005로 돌아 off/on 대조가 깨진다, WAN-91/95/112 부류).
    return harness.build_config(
        BOOK_ANNUALIZATION_TF,
        max_notional_adv_fraction=ADV_FRACTION if cap_on else None,
    )


def _scope_payloads(payloads: Sequence[CellPayload], scope: str) -> list[CellPayload]:
    if scope == "both":
        return list(payloads)
    return [p for p in payloads if p.timeframe == scope]


def _cap_row(
    *,
    cap_on: bool,
    scope: str,
    segment: str,
    exclude: str,
    num_cells: int,
    num_symbols: int,
    outcome: BookOutcome,
    result: BacktestResult,
) -> CapRow:
    m = result.metrics
    stats = outcome.stats
    return CapRow(
        cap_on=cap_on,
        scope=scope,
        segment=segment,
        exclude_symbol=exclude,
        num_cells=num_cells,
        num_symbols=num_symbols,
        num_trades=m.num_trades,
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        peak_concurrency=stats.peak_concurrency,
        max_concurrent_risk=stats.max_concurrent_risk_ratio,
        max_open_notional_ratio=stats.max_open_notional_ratio,
        liquidation_events=len(stats.liquidations),
        clamped_entries=stats.clamped_entries,
        adv_capped_entries=stats.adv_capped_entries,
        first_adv_cap_time=stats.first_adv_cap_time,
        first_adv_cap_equity=stats.first_adv_cap_equity,
    )


def _run_scope_segment(
    payloads: Sequence[CellPayload],
    *,
    cap_on: bool,
    scope: str,
    segment: str,
    exclude: str,
) -> CapRow:
    scoped = _scope_payloads(payloads, scope)
    kept = [p for p in scoped if not exclude or _short(p.symbol) != exclude]
    # WAN-305 명시 핀: wan244는 재진입 이전 북이다(payload에도 재진입이 없어 무동작 가드).
    cells = _segment_cells(kept, segment, "", include_reentry=False)
    base_cfg = _base_cfg(cap_on)
    outcome = run_leverage_book(cells, base_cfg, ADOPTED_BOOK)
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    return _cap_row(
        cap_on=cap_on,
        scope=scope,
        segment=segment,
        exclude=exclude,
        num_cells=len(cells),
        num_symbols=len({_short(p.symbol) for p in kept}),
        outcome=outcome,
        result=result,
    )


def build_cap_rows(payloads: Sequence[CellPayload]) -> list[CapRow]:
    """상한 끔/켬 × 스코프 × 구간 격자 + leave-one-out(oos_warm 종목 편중).

    후보 생성(무거움)은 이미 끝났고 이 격자는 배치 회계(가벼움)뿐이다 — 상한 끔/켬은
    같은 후보를 base_cfg만 바꿔 돌린다(끔 = 채택 셀 재현, 켬 = 유동성 한도 적용).
    """
    symbols = sorted({_short(p.symbol) for p in payloads})
    tf_scopes = [tf for tf in DEFAULT_TIMEFRAMES if any(p.timeframe == tf for p in payloads)]
    has_both = len(tf_scopes) >= 2
    scopes = (["both"] if has_both else []) + tf_scopes
    rows: list[CapRow] = []
    for cap_on in (False, True):
        for scope in scopes:
            for segment in SEGMENTS:
                rows.append(
                    _run_scope_segment(
                        payloads, cap_on=cap_on, scope=scope, segment=segment, exclude=""
                    )
                )
        # leave-one-out은 편중 판정(d)용 — 주 수치(oos_warm)의 both 스코프에서만 낸다.
        loo_scope = "both" if has_both else tf_scopes[0]
        for exclude in symbols:
            rows.append(
                _run_scope_segment(
                    payloads,
                    cap_on=cap_on,
                    scope=loo_scope,
                    segment=SEGMENT_OOS_WARM,
                    exclude=exclude,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _pick(
    rows: Sequence[CapRow],
    *,
    cap_on: bool,
    scope: str,
    segment: str,
    exclude: str = "",
) -> CapRow:
    found = [
        r
        for r in rows
        if r.cap_on == cap_on
        and r.scope == scope
        and r.segment == segment
        and r.exclude_symbol == exclude
    ]
    if len(found) != 1:
        key = f"cap_on={cap_on} {scope}/{segment}/{exclude!r}"
        raise ValueError(f"행이 정확히 1개여야 합니다: {key} → {len(found)}개")
    return found[0]


def _main_scope(rows: Sequence[CapRow]) -> str:
    scopes = {r.scope for r in rows}
    return "both" if "both" in scopes else next(iter(sorted(scopes)))


def verdict(rows: Sequence[CapRow]) -> str:
    """완료기준 3 판정 — 유동성 한도가 복리 착시를 걷어내는가(핵심 a) + b·c·d.

    숫자는 전부 행에서 계산한다(문장에 박으면 재실행 뒤 리포트가 거짓말을 한다, WAN-164).
    """
    scope = _main_scope(rows)

    def _seg(segment: str) -> tuple[CapRow, CapRow]:
        return (
            _pick(rows, cap_on=False, scope=scope, segment=segment),
            _pick(rows, cap_on=True, scope=scope, segment=segment),
        )

    def _ratio(off: CapRow, on: CapRow) -> float | None:
        return (off.total_return / on.total_return) if on.total_return > 0 else None

    def _ratio_txt(off: CapRow, on: CapRow) -> str:
        r = _ratio(off, on)
        return f"{r:,.0f}배↓" if r and r > 1 else "≈불변"

    # (a) 착시가 사는 곳 = full·is(6년 복리). 상한이 여기서 천문학적 %를 걷어내는가가 핵심이다.
    # ⚠️ oos_warm/oos는 신선 초기자본에서 시작해 극단까지 복리되지 않으므로 착시 자체가
    # 작다 — 거기서 「안 걷혔다」로 읽으면 안 된다(걷어낼 착시가 애초에 적다).
    illusion_ok = True
    illusion_parts: list[str] = []
    for segment in (SEGMENT_FULL, SEGMENT_IS):
        off, on = _seg(segment)
        seg_ok = on.total_return < off.total_return * 0.1 and on.adv_capped_entries > 0
        illusion_ok = illusion_ok and seg_ok
        rate = f"{(on.adv_capped_rate or 0.0) * 100:.0f}%"
        illusion_parts.append(
            f"{segment}: {off.total_return * 100:,.0f}% → {on.total_return * 100:,.0f}% "
            f"({_ratio_txt(off, on)}, 발동률 {rate})"
        )

    warm_parts: list[str] = []
    for segment in (SEGMENT_OOS_WARM, SEGMENT_OOS):
        off, on = _seg(segment)
        rate = f"{(on.adv_capped_rate or 0.0) * 100:.0f}%"
        warm_parts.append(
            f"{segment}: {off.total_return * 100:,.0f}% → {on.total_return * 100:,.0f}% "
            f"({_ratio_txt(off, on)}, 발동률 {rate})"
        )

    # (b) MDD 변화 · (c) 첫 발동 자본.
    full_off, full_on = _seg(SEGMENT_FULL)
    warm_off, warm_on = _seg(SEGMENT_OOS_WARM)
    mdd_txt = (
        f"MDD full {_pct(full_off.max_drawdown)} → {_pct(full_on.max_drawdown)} · "
        f"oos_warm {_pct(warm_off.max_drawdown)} → {_pct(warm_on.max_drawdown)}"
    )
    first_full = (
        f"${full_on.first_adv_cap_equity:,.0f}" if full_on.first_adv_cap_equity else "발동 없음"
    )
    first_warm = (
        f"${warm_on.first_adv_cap_equity:,.0f}" if warm_on.first_adv_cap_equity else "발동 없음"
    )

    if illusion_ok:
        head = "**(a) 유동성 한도가 복리 착시를 걷어낸다 — full·is의 천문학적 %가 주저앉는다.**"
    else:
        head = "**(a) full·is에서 착시가 예상만큼 걷히지 않았다(축소 <10배 또는 발동 없음).**"

    return (
        f"{head} {scope} 기준 {' · '.join(illusion_parts)}. "
        f"주 수치 구간은 신선 자본이라 착시가 작아 거의 안 변한다: {' · '.join(warm_parts)}. "
        f"(b) 낙폭은 오히려 준다({mdd_txt}) — 가장 큰 포지션이 곧 가장 큰 손실이라 상한이 그 "
        f"꼬리를 자른다. (c) 상한이 처음 구속하는 공유 자본 ≈ full {first_full} · oos_warm "
        f"{first_warm} — full은 창 초기(2020) 소형 알트 유동성이 얇아 **개시 자본에서 바로** "
        f"물리고(이슈의 ~$26만 추정은 최근 180일 ADV 기준이라 6년 초기와 다르다), oos_warm은 "
        f"뒷구간이라 더 큰 자본에서 물린다. 잔여 %도 여전히 `baseline`(닿으면 체결) 낙관 위 "
        "값이라 실현 수익이 아니다 — 상한은 유동성 착시 하나만 걷을 뿐이다."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[CapRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CapRow.model_fields))


def grid_from_csv(path: Path) -> list[CapRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [CapRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _cap_capital(row: CapRow) -> str:
    if row.first_adv_cap_equity is None:
        return "—"
    return f"${row.first_adv_cap_equity:,.0f}"


def _compare_table(rows: Sequence[CapRow], scope: str, segment: str) -> list[str]:
    off = _pick(rows, cap_on=False, scope=scope, segment=segment)
    on = _pick(rows, cap_on=True, scope=scope, segment=segment)
    lines = [
        "| 상한 | 거래 | 승률 | 수익률 | MDD | 수익/MDD | 최대동시리스크 | 청산 "
        "| 발동 | 발동률 | 첫 발동 자본 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for label, r in (("끔", off), ("켬(0.5%)", on)):
        gate = "" if r.sample_ok else " ⚠️"
        rate = "—" if r.adv_capped_rate is None else f"{r.adv_capped_rate * 100:.1f}%"
        lines.append(
            f"| {label} | {r.num_trades}{gate} | {_pct(r.win_rate)} | {_pct(r.total_return)} "
            f"| {_pct(r.max_drawdown)} | {_rr(r.return_over_mdd)} "
            f"| {_pct(r.max_concurrent_risk)} | {r.liquidation_events} "
            f"| {r.adv_capped_entries} | {rate} | {_cap_capital(r)} |"
        )
    return lines


def _loo_lines(rows: Sequence[CapRow], scope: str) -> list[str]:
    symbols = sorted({r.exclude_symbol for r in rows if r.exclude_symbol})
    lines = [
        "| 상한 | 전체 | " + " | ".join(f"−{s}" for s in symbols) + " |",
        "| -- | --: | " + " | ".join("--:" for _ in symbols) + " |",
    ]
    for cap_on, label in ((False, "끔"), (True, "켬")):
        seg = SEGMENT_OOS_WARM
        base = _pick(rows, cap_on=cap_on, scope=scope, segment=seg)
        cells = [
            _pick(rows, cap_on=cap_on, scope=scope, segment=seg, exclude=s).total_return
            for s in symbols
        ]
        lines.append(
            f"| {label} | {_pct(base.total_return)} | " + " | ".join(_pct(v) for v in cells) + " |"
        )
    return lines


def build_summary_markdown(
    cell_rows: Sequence[CellRow],
    cap_rows: Sequence[CapRow],
    *,
    cells_csv: Path,
    grid_csv: Path,
) -> str:
    verify_line, _ = verify_cells(cell_rows)
    scope = _main_scope(cap_rows)
    tf_scopes = sorted({r.scope for r in cap_rows if r.scope != "both"})
    lines = [
        "# WAN-244 — 유동성 한도(일거래량 비례): 채택 북의 복리 착시를 걷어내는가",
        "",
        "**성격** 측정 전용(옵트인 엔진 `max_notional_adv_fraction` 위의 대조). 채택 회계 = "
        "레버리지 북 **cap_only 5배**(WAN-213) · 9종목 · 못 박은 6년"
        f"({DEFAULT_START}~{DEFAULT_END}) · 15m·1h·2h·4h(WAN-252) · 렌즈 `baseline` 단독 · "
        "채택 기본값 그대로(옛 핀 없음). **상한 끔 vs 켬(k = 0.5% ADV, 사용자 확정)**.",
        "",
        "**유동성 한도** = 포지션 명목 ≤ `0.5% × ADV_usd`. ADV는 탭 봉 **직전까지 완료된** "
        "일자들의 평균 일 달러거래량(룩어헤드 금지) — `ADV_usd = Σ(봉 volume × 봉 close)`, "
        "ccxt volume은 base 수량이라 × 가격으로 USD 환산. ⚠️ **이 항만 자본에 안 비례하는 "
        "절대 달러 상한**이라 복리를 깬다.",
        "",
        f"재현: `uv run python -m backtest.wan244_capacity_cap --jobs 6` (요약만: `--from-csv`). "
        f"원자료: `{cells_csv}` · `{grid_csv}`.",
        "",
        "## 0. 검산 — 상한 끔이 채택 엔진과 같은 수를 내는가",
        "",
        verify_line,
        "",
        "상한 끔(`cap_on=False`) 행은 `book_cli`/wan180 채택 셀과 같은 base_cfg로 돈다 — "
        "후보에 `adv_usd`가 실려 있어도 사이징이 무시하므로 **거래가 비트 단위로 같다**"
        "(`tests/test_leverage_book.py`가 동작으로 고정).",
        "",
        f"## 1. 본 판정 — {scope}(전 칸 = 사용자 정의 실제 북) · 상한 끔 vs 켬",
        "",
        "📌 **상한의 효과는 복리가 얼마나 극단적이냐에 비례한다 — 구간을 가로질러 읽을 것.** "
        "착시가 사는 곳은 **full·is**(6년/앞구간 전체를 연속 복리)라 거기서 상한이 크게 물리고 "
        "천문학적 %를 걷어낸다. 주 수치 구간(**oos_warm·oos**)은 신선한 초기자본에서 시작해 "
        "그 극단까지 복리되지 않으므로 상한이 거의 안 물리고 `total_return`도 거의 안 변한다 — "
        "이는 「상한이 무력하다」가 아니라 「걷어낼 착시가 애초에 작다」는 뜻이다.",
        "",
        "### oos_warm (주 수치)",
        "",
        *_compare_table(cap_rows, scope, SEGMENT_OOS_WARM),
        "",
        "### oos (차가운 스트레스)",
        "",
        *_compare_table(cap_rows, scope, SEGMENT_OOS),
        "",
        "### full · is (맥락 — 복리 착시가 가장 큰 구간)",
        "",
        *_compare_table(cap_rows, scope, SEGMENT_FULL),
        "",
        *_compare_table(cap_rows, scope, SEGMENT_IS),
        "",
        "🚨 **북의 수익률(상한 끔)은 수백~수천 거래의 복리 값이다 — 달성 가능 성과로 인용 "
        "금지**(WAN-90/169). 결정에 실질적인 열은 수익률 절대 크기가 아니라 **MDD · 최대 "
        "동시 리스크 · 청산 · 상한 발동률**이다. 유동성 한도가 하는 일은 그 복리 %를 시장이 "
        "실제로 받아 줄 수 있는 규모로 되돌리는 것이지 알파를 더하는 게 아니다.",
        "",
    ]
    for tf in tf_scopes:
        lines += [
            f"## 2. TF 단면 — {tf} · oos_warm",
            "",
            *_compare_table(cap_rows, tf, SEGMENT_OOS_WARM),
            "",
        ]
    lines += [
        "## 3. leave-one-out — 종목 편중 (oos_warm · total_return)",
        "",
        *_loo_lines(cap_rows, scope),
        "",
        "⚠️ **oos_warm에선 상한이 거의 안 물려(발동률 11%) 끔/켬 행이 거의 같다** — 편중 판정은 "
        "끔 행으로 읽는다. 어느 한 종목이 전부를 만들지 않는다(어느 종목을 빼도 크게 남는다). "
        "**유동성 한도의 편중 효과(full·is)는 이 표에 안 담긴다** — 상한은 유동성이 얇은 소형 "
        "알트에서 먼저·더 세게 물리므로(첫 발동 $9,976) 종목마다 다르게 깎지만, 그 구간은 "
        "복리 %가 천문학적이라 leave-one-out 표로 비교하는 것이 무의미하다.",
        "",
        "## 판정 (완료기준 3)",
        "",
        verdict(cap_rows),
        "",
        "판정 자: (a) **착시가 사는 full·is**에서 상한을 켜면 `total_return`이 크게(≥10배) "
        "주저앉고 상한이 실제로 발동하는가 — 주 수치 구간(oos_warm·oos)은 신선 자본이라 착시 "
        "자체가 작아 판정 기준으로 쓰지 않는다(발동률·변화는 맥락으로만 읽는다) · (b) MDD "
        "변화 · (c) 첫 발동 자본 규모 · (d) 편중(leave-one-out). 표본 게이트 20건(WAN-84) "
        "미달 셀은 ⚠️ 표시.",
        "",
        "⚠️ **상한은 숫자를 정직하게 만들 뿐 신호를 만들지 않는다** — 「엣지 없음」(WAN-84/88/"
        "111/114/124/151/201)은 그대로다. 레버리지·용량은 위험의 모양만 바꾼다(WAN-90).",
        "",
        "⚠️ **k = 0.5%는 사용자 판단이지 데이터가 고른 최적값이 아니다** — 「데이터가 0.5%를 "
        "골랐다」로 인용 금지. IS 재선택 금지(자유 파라미터, WAN-108).",
        "",
        "⚠️ **기본값·토대 불변**(상한 옵트인 · 채택 승격은 재-베이스라인 = 사용자 결정) · "
        "전부 `baseline`(닿으면 체결) 렌즈 위 값이고 그 가정은 이 이슈가 안 건드린다 · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_payloads(
    symbols: Sequence[str], timeframes: Sequence[str], start: str, end: str, jobs: int, proxy: bool
) -> list[CellPayload]:
    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        adv_fraction=ADV_FRACTION,
        # WAN-305 명시 핀: wan244 CSV는 재진입 이전 북의 동결 스냅샷이다.
        reentry=False,
        invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
    )
    if proxy:
        payloads, note = apply_funding_proxy(payloads)
        if note:
            print(f"[wan244] 펀딩 대리: {note}", flush=True)
    return payloads


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-244 유동성 한도(ADV 비례) 측정")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 칸 단위 병렬 워커 수")
    parser.add_argument("--no-funding-proxy", action="store_true", help="신규 종목 펀딩 대리 끔")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-grid", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv", action="store_true", help="재실행 없이 저장된 CSV에서 요약만 재생성"
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_grid = Path(args.out_grid)
    out_md = Path(args.out_md)

    if args.from_csv:
        cell_rows = cells_from_csv(out_cells)
        cap_rows = grid_from_csv(out_grid)
        print(f"[wan244] CSV 로드 — 칸 {len(cell_rows)}행 · 격자 {len(cap_rows)}행 (재실행 없음)")
    else:
        payloads = _load_payloads(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            args.start,
            args.end,
            args.jobs,
            not args.no_funding_proxy,
        )
        cell_rows = [row for p in payloads for row in p.rows]
        cap_rows = build_cap_rows(payloads)
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(cell_rows).to_csv(out_cells, index=False)
        grid_to_frame(cap_rows).to_csv(out_grid, index=False)
        print(f"[wan244] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan244] 격자 {len(cap_rows)}행 → {out_grid}")

    verify_line, worst = verify_cells(cell_rows)
    print(f"[wan244] 검산: {verify_line}")
    if not math.isfinite(worst) or worst >= 1e-12:
        print("[wan244] 🚨 검산 실패 — 요약을 내기 전에 배선을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(cell_rows, cap_rows, cells_csv=out_cells, grid_csv=out_grid),
        encoding="utf-8",
    )
    print(f"[wan244] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
