"""WAN-264 — 비용·체결 보수화 시 재진입 북 이득 잔존 (pen_5bp + 수수료/슬리피지 민감도).

## 무엇을 묻나 (WAN-262 후속 · WAN-92 축)

WAN-261/262가 잰 재진입 북 이득(off→on 의 MDD·최대 동시 리스크·거래·칸별 수익 변화)은 **전부**
`baseline`(주문이 닿으면 무조건 체결된다는 낙관 가정) 위의 값이다. WAN-96/124가 반복해서
보였듯 이 저장소의 수익 상당분은 「스치듯 닿고 되돌아선 체결」(마진 체결)에 실려 있어, 낙관
가정을 조이면 가장 먼저 사라질 후보가 재진입 이득이다. 또 비용 현실화(WAN-92)는 아직
미반영이라 현재 비용은 정본이되 상한일 수 있다.

**질문 하나**: 체결 보수화(관통 요구, `pen_5bp`)·비용 현실화(테이커 4→5bp 등)를 얹으면
재진입 on의 off→on 델타가 **얼마나 남나?** 남으면 실효적이고, 사라지면 낙관 가정의 산물이다.

## 방법 — 렌즈 × 비용 격자, 후보는 렌즈당 한 번만 생성

두 축이 성격이 다르다:

* **체결 렌즈**(`baseline` vs `pen_5bp`)는 **후보 집합**을 바꾼다 — 관통 벌점은 substep 체결
  판정에서 적용되므로(`ConfluenceParams.fill_penetration_bps`) 렌즈마다 후보 생성을 **다시**
  해야 한다. 이게 유일한 무거운 연산이다.
* **비용**(테이커/슬리피지 bp)은 후보에 **무관**하다 — `BookCell`은 「비용 미반영 원가
  셋업」이고 비용은 시퀀싱(`_to_trade`)에서만 적용된다. 그래서 렌즈당 한 번 생성한 후보를
  **여러 비용에 재사용**한다(WAN-264 컴퓨트 최적화 — 비용 축이 공짜다).

각 (렌즈, 비용)에서 재진입 두 팔(off = 채택 북 그대로 / on = 재진입 얹은 북)을 돌리고,
off→on 델타의 **잔존율** = δ(렌즈, 비용) ÷ δ(baseline, base)를 낸다.

## 무엇을 내나 (완료기준)

* **잔존 표** — 각 TF · (렌즈 × 비용)마다 off→on 의 MDD·최대 동시 리스크·거래·칸별 수익
  델타와, 그 델타가 기준(baseline·base) 대비 몇 % 남는지(방향+크기).
* **15m 최우선** — 낙관 가정 최대 의존 TF라 여기서 이득이 얼마나 무너지는지가 핵심.

## 성격 · 경고 (그대로 옮길 것)

측정 전용. 핀 없음(오늘 엔진) · 채택 좌표(9종목 × 못 박은 6년) · 채택 북(cap_only 5배) ·
기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지). ⚠️ 레버리지 북 **총수익%는 복리
착시**라 실현 수익이 아니다(WAN-213) — 판단은 MDD·최대 동시 리스크·청산으로 한다.
⚠️ **틱·호가(WAN-98)로도 완전 해소 안 된다** — `pen_5bp`는 **관통 벌점**이지 큐 우선순위·부분
체결 모델이 아니고, WAN-98은 Canceled다. 이 표도 **상한을 조인 것**이지 실체결이 아니다.
「엣지 없음」(WAN-84/88/111/114/124/151/201)과 **다른 질문**이라 뒤집는 게 아니다. WAN-128이
신규 리포트를 `baseline` 단독으로 정했으나 **이 이슈는 체결 보수화 자체를 재는 게 목적**이라
`pen_5bp` 옵트인 측정이 규칙에 맞다(수동 확인 경로).

## 재현

```
uv run python -m backtest.wan264_reentry_book_stress --tf 1h,2h,4h --jobs 6
uv run python -m backtest.wan264_reentry_book_stress --tf 15m --append --jobs 9   # 15m 무거움
uv run python -m backtest.wan264_reentry_book_stress --from-csv                   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import book_cli, harness
from backtest.book_cli import BookRunRow
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
)
from backtest.models import BacktestConfig
from backtest.run import ADOPTED_BOOK, parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import (
    CellPayload,
    _isolated_metrics,
    _segment_cells,
    _short,
    run_cells,
)
from backtest.wan180_leverage_book_nine import apply_funding_proxy

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan264_reentry_stress_cells.csv"
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan264_reentry_stress_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan264_reentry_stress_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 기본 TF = 1h·2h·4h(컴퓨트 실현 가능). 15m은 셀당 ~37분 × 렌즈 수라 별도 무거운 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1h", "2h", "4h")

#: 정본 구간 — WAN-166 규약(oos_warm 주 · oos 스트레스).
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 두 팔 — 재진입 off(채택 북 그대로) / on(재진입 얹은 북).
ARMS: tuple[str, ...] = ("off", "on")

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 스코프·구간은 판정에서 뺀다.
MIN_TRADES = 20

#: 잔존율 분모가 0에 가까우면 비율이 무의미하다 — `—`로 찍는다.
_RESIDUAL_EPS = 1e-9


# --------------------------------------------------------------------------- #
# 축 — 체결 렌즈 · 비용
# --------------------------------------------------------------------------- #

#: 체결 렌즈 축. `baseline`(닿으면 체결) · `pen_5bp`(관통 5bp 요구). 후보 집합을 바꾼다.
DEFAULT_LENSES: tuple[str, ...] = ("baseline", "pen_5bp")

#: 잔존율 기준점 — 이 (렌즈, 비용)에서의 off→on 델타를 100%로 둔다.
REF_LENS = "baseline"
REF_COST = "base"


@dataclass(frozen=True)
class CostPreset:
    """비용 축 한 점(WAN-92 방향). `None` 필드는 채택 기본값(테이커 4bp·메이커 2bp·슬리피지
    5bp)을 그대로 쓴다 — 비용은 후보에 무관하고 시퀀싱에서만 적용된다."""

    name: str
    fee_rate: float | None = None
    maker_fee_rate: float | None = None
    slippage: float | None = None
    note: str = ""


#: 비용 축. `base`(채택) · `taker5`(테이커 5bp, WAN-92 헤드라인) · `taker5_slip7`(스트레스).
#: 진입은 메이커(지정가)라 슬리피지 0 · 청산은 항상 테이커라 이 값들이 **청산 비용**에 문다 —
#: 재진입은 거래를 늘리므로 청산 비용 인상이 재진입 팔에 더 크게 문다(측정 목적에 부합).
COST_PRESETS: tuple[CostPreset, ...] = (
    CostPreset("base", note="채택 비용(테이커 4bp·메이커 2bp·슬리피지 5bp)"),
    CostPreset("taker5", fee_rate=0.0005, note="테이커 5bp(WAN-92 헤드라인)"),
    CostPreset(
        "taker5_slip7",
        fee_rate=0.0005,
        slippage=0.0007,
        note="테이커 5bp·슬리피지 7bp(스트레스)",
    ),
)
COST_BY_NAME: dict[str, CostPreset] = {c.name: c for c in COST_PRESETS}
DEFAULT_COSTS: tuple[str, ...] = tuple(c.name for c in COST_PRESETS)


def cost_preset(name: str) -> CostPreset:
    try:
        return COST_BY_NAME[name]
    except KeyError:
        supported = ", ".join(COST_BY_NAME)
        raise ValueError(f"알 수 없는 비용 프리셋: {name!r} (지원: {supported})") from None


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CellStressRow(BaseModel):
    """한 (종목, TF) 칸 × 구간 × 팔 × 렌즈 × 비용의 격리(단일 포지션) 개별 수익률."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    lens: str
    cost: str
    num_candidates: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float


class BookStressRow(BaseModel):
    """한 스코프 × 구간 × 팔 × 렌즈 × 비용 × 제외종목의 북 집계.

    판정 열은 위험조정(`max_drawdown`·`max_concurrent_risk`·`liquidation_events`)이다
    (총수익%는 복리 착시 — WAN-213).
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    arm: str
    segment: str
    lens: str
    cost: str
    exclude_symbol: str = ""
    num_cells: int
    num_symbols: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    liquidation_events: int

    @field_validator("return_over_mdd", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


# --------------------------------------------------------------------------- #
# 계산 (순수 — payloads → 행)
# --------------------------------------------------------------------------- #


def _cost_config(timeframe: str, cost: CostPreset) -> BacktestConfig:
    """이 TF·비용의 실행 cfg. `None` 필드는 채택 기본값을 그대로 쓴다(비트 재현)."""
    return harness.build_config(
        timeframe,
        fee_rate=cost.fee_rate,
        maker_fee_rate=cost.maker_fee_rate,
        slippage=cost.slippage,
    )


def _cell_stress_rows(
    payloads: Sequence[CellPayload], *, lens: str, cost: CostPreset
) -> list[CellStressRow]:
    """칸별 개별(격리 단일 포지션) 수익률 — off(base)·on(base+재진입) 두 팔, 이 렌즈·비용에서.

    후보 선택은 북과 **같은** `_segment_cells`를 쓴다(한 칸만 넘겨). 비용만 cfg로 갈아끼운다 —
    후보는 이미 렌즈로 걸러진 payloads에서 온다(비용은 후보 집합 무관).
    """
    rows: list[CellStressRow] = []
    for payload in payloads:
        cfg = _cost_config(payload.timeframe, cost)
        for segment in SEGMENTS:
            for arm in ARMS:
                cell = _segment_cells([payload], segment, "", include_reentry=arm == "on")[0]
                num_trades, win_rate, total_return, mdd = _isolated_metrics(
                    cell.candidates, cfg, payload.timeframe, cell.funding_rates
                )
                rows.append(
                    CellStressRow(
                        symbol=payload.symbol,
                        timeframe=payload.timeframe,
                        segment=segment,
                        arm=arm,
                        lens=lens,
                        cost=cost.name,
                        num_candidates=len(cell.candidates),
                        num_trades=num_trades,
                        win_rate=win_rate,
                        total_return=total_return,
                        max_drawdown=mdd,
                    )
                )
    return rows


def _book_stress_rows(
    payloads: Sequence[CellPayload],
    *,
    lens: str,
    cost: CostPreset,
    start_ms: int,
    end_ms: int,
) -> list[BookStressRow]:
    """스코프(각 TF + 전체) × 구간 × 팔 × leave-one-out의 북 집계, 이 렌즈·비용에서.

    `book_cli.build_book_rows`(채택 북)를 그대로 호출하되 비용만 오버라이드한다 — 후보는
    렌즈로 걸러진 payloads에서 오고, 비용은 시퀀싱에서만 적용된다.
    """
    timeframes = sorted({p.timeframe for p in payloads}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    symbols = sorted({_short(p.symbol) for p in payloads})
    rows: list[BookStressRow] = []
    for scope in scopes:
        scoped = payloads if scope == "all" else [p for p in payloads if p.timeframe == scope]
        for arm in ARMS:
            for exclude in ["", *symbols]:
                kept = [p for p in scoped if not exclude or _short(p.symbol) != exclude]
                if not kept:
                    continue
                book_rows = book_cli.build_book_rows(
                    kept,
                    book=ADOPTED_BOOK,
                    segments=SEGMENTS,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    include_reentry=arm == "on",
                    fee_rate=cost.fee_rate,
                    maker_fee_rate=cost.maker_fee_rate,
                    slippage=cost.slippage,
                )
                for br in book_rows:
                    rows.append(
                        _to_stress_row(
                            br, scope=scope, arm=arm, lens=lens, cost=cost.name, exclude=exclude
                        )
                    )
    return rows


def _to_stress_row(
    br: BookRunRow, *, scope: str, arm: str, lens: str, cost: str, exclude: str
) -> BookStressRow:
    return BookStressRow(
        scope=scope,
        arm=arm,
        segment=br.segment,
        lens=lens,
        cost=cost,
        exclude_symbol=exclude,
        num_cells=br.num_cells,
        num_symbols=br.num_symbols,
        num_trades=br.num_trades,
        win_rate=br.win_rate,
        total_return=br.total_return,
        max_drawdown=br.max_drawdown,
        return_over_mdd=br.return_over_mdd,
        peak_concurrency=br.peak_concurrency,
        max_concurrent_risk=br.max_concurrent_risk,
        liquidation_events=br.liquidation_events,
    )


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    lenses: Sequence[str] = DEFAULT_LENSES,
    costs: Sequence[str] = DEFAULT_COSTS,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    funding_proxy: bool = True,
    log: bool = True,
) -> tuple[list[CellStressRow], list[BookStressRow]]:
    """렌즈 × 비용 격자를 돈다 — 후보 생성은 렌즈당 한 번, 비용은 값싸게 팬아웃.

    렌즈마다 `run_cells(reentry=True, fill=렌즈)` 한 번이면 base 후보(off 팔)와 재진입 후보
    (on 팔)를 모두 담는다. 그 payloads를 여러 비용으로 재사용해 칸·북 행을 낸다.
    """
    cost_objs = [cost_preset(c) for c in costs]
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    cell_rows: list[CellStressRow] = []
    book_rows: list[BookStressRow] = []
    for lens in lenses:
        fill = None if lens == "baseline" else harness.fill_preset(lens)
        if log:
            print(f"[wan264] 렌즈 {lens}: 후보 생성 중(무거움) …", flush=True)
        # WAN-305 명시 핀: wan264 CSV는 freeze 규칙(WAN-269 이전) 재진입의 동결 스냅샷이다.
        payloads = run_cells(
            symbols,
            timeframes,
            start=start,
            end=end,
            jobs=jobs,
            reentry=True,
            reentry_entry_rule="freeze",
            fill=fill,
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        )
        if funding_proxy:
            payloads, note = apply_funding_proxy(payloads)
            if note and log:
                print(f"[wan264] 펀딩 대리: {note}", flush=True)
        for cost in cost_objs:
            if log:
                print(f"[wan264]   비용 {cost.name}: 시퀀싱(가벼움) …", flush=True)
            cell_rows.extend(_cell_stress_rows(payloads, lens=lens, cost=cost))
            book_rows.extend(
                _book_stress_rows(payloads, lens=lens, cost=cost, start_ms=start_ms, end_ms=end_ms)
            )
    return cell_rows, book_rows


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _pick_book(
    rows: Sequence[BookStressRow],
    *,
    scope: str,
    arm: str,
    segment: str,
    lens: str,
    cost: str,
    exclude: str = "",
) -> BookStressRow | None:
    for r in rows:
        if (
            r.scope == scope
            and r.arm == arm
            and r.segment == segment
            and r.lens == lens
            and r.cost == cost
            and r.exclude_symbol == exclude
        ):
            return r
    return None


@dataclass(frozen=True)
class _Delta:
    """한 (스코프, 구간, 렌즈, 비용)의 off→on 델타 묶음 — 잔존율 표의 한 행."""

    lens: str
    cost: str
    off_trades: int
    on_trades: int
    d_mdd: float
    """MDD off→on (%p 아님, 분수). 렌더에서 ×100."""
    d_risk: float
    d_return: float
    """북 총수익 off→on(분수). ⚠️ 복리 착시 — 방향만."""
    d_liq: int
    sample_ok: bool


def _delta(
    rows: Sequence[BookStressRow], *, scope: str, segment: str, lens: str, cost: str
) -> _Delta | None:
    off = _pick_book(rows, scope=scope, arm="off", segment=segment, lens=lens, cost=cost)
    on = _pick_book(rows, scope=scope, arm="on", segment=segment, lens=lens, cost=cost)
    if off is None or on is None:
        return None
    return _Delta(
        lens=lens,
        cost=cost,
        off_trades=off.num_trades,
        on_trades=on.num_trades,
        d_mdd=on.max_drawdown - off.max_drawdown,
        d_risk=on.max_concurrent_risk - off.max_concurrent_risk,
        d_return=on.total_return - off.total_return,
        d_liq=on.liquidation_events - off.liquidation_events,
        sample_ok=off.sample_ok and on.sample_ok,
    )


def _residual_pct(value: float, ref: float) -> str:
    """δ(렌즈,비용) ÷ δ(기준) × 100. 분모가 0에 가까우면 무의미하므로 `—`."""
    if abs(ref) < _RESIDUAL_EPS:
        return "—"
    return f"{value / ref * 100:.0f}%"


def verdict(book_rows: Sequence[BookStressRow], scope: str) -> str:
    """이 스코프에서 재진입 off→on 델타가 렌즈·비용 보수화로 얼마나 남나 — oos_warm 주 수치.

    숫자는 전부 행에서 계산한다(문장에 박으면 재실행 뒤 거짓말한다 — WAN-164 패턴).
    """
    ref = _delta(book_rows, scope=scope, segment=SEGMENT_OOS_WARM, lens=REF_LENS, cost=REF_COST)
    if ref is None:
        return f"**판정 불가** — {scope}·oos_warm 의 기준(baseline·base) 행이 없습니다."
    parts: list[str] = [
        f"기준({REF_LENS}·{REF_COST})의 재진입 off→on: MDD Δ{ref.d_mdd * 100:+.2f}%p · "
        f"최대 동시 리스크 Δ{ref.d_risk * 100:+.2f}%p · 거래 {ref.off_trades}→{ref.on_trades} · "
        f"청산 Δ{ref.d_liq:+d}."
    ]
    pen = _delta(book_rows, scope=scope, segment=SEGMENT_OOS_WARM, lens="pen_5bp", cost=REF_COST)
    if pen is not None:
        parts.append(
            f"관통 요구(`pen_5bp`·base)에서 MDD 델타 잔존 {_residual_pct(pen.d_mdd, ref.d_mdd)} · "
            f"리스크 델타 잔존 {_residual_pct(pen.d_risk, ref.d_risk)} · 거래 델타 잔존 "
            f"{_residual_pct(pen.on_trades - pen.off_trades, ref.on_trades - ref.off_trades)}."
        )
    if abs(ref.d_mdd) <= 0.005 and abs(ref.d_risk) <= 0.005 and ref.d_liq == 0:
        head = (
            "**기준에서 재진입의 위험 변화 자체가 작다** — 렌즈·비용을 조여도 크게 달라질 "
            "여지가 크지 않다(잔존율은 분모가 작아 방향으로만 읽는다)."
        )
    elif pen is not None and pen.d_mdd > ref.d_mdd + _RESIDUAL_EPS:
        # MDD 델타 잔존이 100%를 넘는다 = 관통을 요구할수록 재진입이 위험을 더 키운다.
        head = (
            "**재진입의 위험 증가는 낙관 가정의 산물이 아니다 — 보수화에서 남고 오히려 커진다.** "
            "체결을 보수화(관통 요구)하면 재진입 off→on 의 MDD 증가가 줄기는커녕 늘어난다(잔존 "
            f"{_residual_pct(pen.d_mdd, ref.d_mdd)}). 청산은 여전히 0이고, 겉보기 총수익 증가는 "
            "복리 착시라 판정에서 뺀다 — 남는 것은 견고한 위험 증가다."
        )
    else:
        head = (
            "**재진입 off→on 델타가 렌즈·비용 보수화에서 얼마나 남나** — 아래 잔존 표가 "
            "정본이고, 이 문장은 주 셀 요약이다."
        )
    return (
        f"{head} {' '.join(parts)} ⚠️ 총수익% 변화는 복리 착시라 판정에 넣지 않는다(WAN-213). "
        "전부 `baseline`(닿으면 체결) 낙관을 조인 값이고 `pen_5bp`는 **관통 벌점**이지 큐 "
        "우선순위·부분 체결 모델이 아니다 — 틱·호가(WAN-98, Canceled)로도 완전 해소되지 않아 "
        "이 표는 상한을 조인 것이지 실체결이 아니다. 「엣지가 있나」는 묻지 않는다 — 재진입 "
        "가격 효과를 북에 얹은 전체 위험을 렌즈·비용으로 스트레스한 측정이다(채택은 "
        "재-베이스라인 = 사용자 결정)."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[CellStressRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CellStressRow.model_fields))


def book_to_frame(rows: Sequence[BookStressRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookStressRow.model_fields))


def cells_from_csv(path: Path) -> list[CellStressRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [CellStressRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def book_from_csv(path: Path) -> list[BookStressRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [BookStressRow.model_validate(rec) for rec in records]


def merge_cells(
    existing: Sequence[CellStressRow], new: Sequence[CellStressRow]
) -> list[CellStressRow]:
    new_tfs = {r.timeframe for r in new}
    kept = [r for r in existing if r.timeframe not in new_tfs]
    return [*kept, *new]


def merge_book(
    existing: Sequence[BookStressRow], new: Sequence[BookStressRow]
) -> list[BookStressRow]:
    """새 TF 축을 붙일 때 옛 행을 보존한다(북 행의 스코프 = TF 또는 `all`).

    같은 TF 스코프는 새 행이 이기고, `all` 스코프는 TF 조합이 바뀌면 무의미해지므로 새
    실행이 `all`을 내면 옛 `all`은 버린다(부분 TF의 `all`이 남지 않게).
    """
    new_scopes = {r.scope for r in new if r.scope != "all"}
    has_new_all = any(r.scope == "all" for r in new)
    kept = [
        r for r in existing if r.scope not in new_scopes and not (has_new_all and r.scope == "all")
    ]
    return [*kept, *new]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _residual_table(book_rows: Sequence[BookStressRow], scope: str, segment: str) -> list[str]:
    """이 스코프·구간의 렌즈 × 비용 잔존 표 — off→on 델타와 기준 대비 잔존율.

    기준 행(baseline·base)이 100%이고, 나머지 행은 그 델타가 몇 % 남는지 병기한다.
    """
    ref = _delta(book_rows, scope=scope, segment=segment, lens=REF_LENS, cost=REF_COST)
    lenses = sorted({r.lens for r in book_rows if r.scope == scope and r.segment == segment})
    costs = [c for c in DEFAULT_COSTS if any(r.cost == c for r in book_rows if r.scope == scope)]
    lines = [
        "| 렌즈 | 비용 | off→on MDDΔ | (잔존) | 리스크Δ | (잔존) | 거래 off→on | (잔존) | 청산Δ |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for lens in lenses:
        for cost in costs:
            d = _delta(book_rows, scope=scope, segment=segment, lens=lens, cost=cost)
            if d is None:
                continue
            gate = "" if d.sample_ok else " ⚠️"
            is_ref = (lens, cost) == (REF_LENS, REF_COST)
            if ref is None or is_ref:
                r_mdd = r_risk = r_tr = "기준" if is_ref else "—"
            else:
                r_mdd = _residual_pct(d.d_mdd, ref.d_mdd)
                r_risk = _residual_pct(d.d_risk, ref.d_risk)
                r_tr = _residual_pct(d.on_trades - d.off_trades, ref.on_trades - ref.off_trades)
            lines.append(
                f"| {lens} | {cost} | {d.d_mdd * 100:+.2f}%p | {r_mdd} | "
                f"{d.d_risk * 100:+.2f}%p | {r_risk} | "
                f"{d.off_trades}→{d.on_trades}{gate} | {r_tr} | {d.d_liq:+d} |"
            )
    return lines


def _book_arm_table(
    book_rows: Sequence[BookStressRow], scope: str, segment: str, lens: str, cost: str
) -> list[str]:
    lines = [
        "| 팔 | 거래 | 총수익%† | MDD | 최대동시리스크 | 청산 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: |",
    ]
    for arm in ARMS:
        r = _pick_book(book_rows, scope=scope, arm=arm, segment=segment, lens=lens, cost=cost)
        if r is None:
            continue
        gate = "" if r.sample_ok else " ⚠️"
        lines.append(
            f"| 재진입 {arm} | {r.num_trades}{gate} | {_pct(r.total_return)} | "
            f"{_pct(r.max_drawdown)} | {_pct(r.max_concurrent_risk)} | "
            f"{r.liquidation_events} | {r.peak_concurrency} |"
        )
    return lines


def _cell_delta_table(
    cell_rows: Sequence[CellStressRow], timeframe: str, lens: str, cost: str
) -> list[str]:
    """oos_warm 칸별 off→on Δ수익(격리) — 이 렌즈·비용에서."""
    scoped = [
        r
        for r in cell_rows
        if r.timeframe == timeframe
        and r.segment == SEGMENT_OOS_WARM
        and r.lens == lens
        and r.cost == cost
    ]
    by_key = {(r.symbol, r.arm): r for r in scoped}
    symbols = sorted({r.symbol for r in scoped})
    lines = [
        "| 심볼 | off 거래 | off 수익 | on 거래 | on 수익 | Δ수익 |",
        "| -- | --: | --: | --: | --: | --: |",
    ]
    for symbol in symbols:
        off = by_key.get((symbol, "off"))
        on = by_key.get((symbol, "on"))
        if off is None or on is None:
            continue
        delta = (on.total_return - off.total_return) * 100
        lines.append(
            f"| {_short(symbol)} | {off.num_trades} | {_pct(off.total_return)} | "
            f"{on.num_trades} | {_pct(on.total_return)} | {delta:+.2f}%p |"
        )
    return lines


def build_summary_markdown(
    cell_rows: Sequence[CellStressRow],
    book_rows: Sequence[BookStressRow],
    *,
    cells_csv: Path,
    book_csv: Path,
) -> str:
    timeframes = sorted({r.timeframe for r in cell_rows}, key=timeframe_to_ms)
    main_scope = (
        "all" if any(r.scope == "all" for r in book_rows) else (timeframes[0] if timeframes else "")
    )
    tf_label = ",".join(timeframes)
    lenses = sorted({r.lens for r in book_rows})
    costs = [c for c in DEFAULT_COSTS if any(r.cost == c for r in book_rows)]
    lens_label = " · ".join(lenses)
    cost_label = " · ".join(f"`{c}`({COST_BY_NAME[c].note})" for c in costs if c in COST_BY_NAME)
    lines = [
        "# WAN-264 — 비용·체결 보수화 시 재진입 북 이득 잔존 (pen_5bp + 수수료/슬리피지 민감도)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`·채택 "
        "북 cap_only 5배) 돌리며 옛 핀은 하나도 물려받지 않는다. 못 박은 6년 창(WAN-182) · "
        "기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        f"**축** 체결 렌즈 {lens_label} × 비용 {cost_label}. WAN-262가 잰 재진입 off→on 델타가 "
        "체결 보수화(`pen_5bp` = 관통 5bp 요구)·비용 현실화(WAN-92 방향)로 얼마나 남는지를 잰다.",
        "",
        "두 축은 성격이 다르다: **렌즈는 후보 집합을 바꾸므로**(관통 벌점이 substep 체결 "
        "판정에 적용) 렌즈마다 후보 생성을 다시 하지만, **비용은 후보에 무관**해(`BookCell` = "
        "「비용 미반영 원가 셋업」, 시퀀싱에서만 적용) 렌즈당 한 번 생성한 후보를 재사용한다.",
        "",
        f"재현: `uv run python -m backtest.wan264_reentry_book_stress --tf {tf_label} --jobs 6` "
        "(요약만: `--from-csv`). 15m은 셀당 ~37분 × 렌즈 수라 별도 무거운 실행(`--tf 15m "
        f"--append`). 원자료: `{cells_csv}`(칸별) · `{book_csv}`(북 격자).",
        "",
        "⚠️ **총수익%는 복리 착시** — 레버리지 북의 %는 수백~수천 거래 복리 값이라 실현 수익이 "
        "아니다(WAN-213). 판단은 **MDD · 최대 동시 리스크 · 청산 건수**의 off→on 차이와 그 잔존율. "
        "⚠️ **틱·호가(WAN-98, Canceled)로도 완전 해소 안 됨** — `pen_5bp`는 관통 벌점이지 큐 "
        "우선순위·부분 체결 모델이 아니라, 이 표는 상한을 조인 것이지 실체결이 아니다.",
        "",
        f"## 1. 잔존 표 (완료기준) — {main_scope} · oos_warm (주 수치)",
        "",
        "재진입 off→on 델타가 기준(`baseline`·`base`) 대비 렌즈·비용별로 몇 % 남는지. 잔존율 "
        "분모(기준 델타)가 0에 가까우면 `—`(방향만 읽는다).",
        "",
        *_residual_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
    ]
    lines += [
        "### oos (차가운 스트레스)",
        "",
        *_residual_table(book_rows, main_scope, SEGMENT_OOS),
        "",
    ]
    if main_scope == "all":
        lines += [
            "## 2. TF 스코프별 잔존 표 (oos_warm 주)",
            "",
            "⚠️ **분모가 작은 TF의 잔존율은 방향으로만 읽는다** — 기준(baseline·base) 델타가 "
            "0에 가까우면(예: 2h MDD Δ+0.19%p) 잔존율이 수백 %로 폭발한다. 그 셀은 잔존율이 "
            "아니라 **원 델타(%p 열)**를 본다(작은 델타 나누기라 비율이 과장된다).",
            "",
        ]
        for tf in timeframes:
            lines += [f"### {tf}", "", *_residual_table(book_rows, tf, SEGMENT_OOS_WARM), ""]
    lines += [
        "## 3. 북 팔 상세 (oos_warm · 렌즈 × 비용) — " + main_scope,
        "",
        "각 (렌즈, 비용)의 off/on 북 행. 총수익%는 복리 착시라 †.",
        "",
    ]
    for lens in lenses:
        for cost in costs:
            lines += [
                f"### {lens} · {cost}",
                "",
                *_book_arm_table(book_rows, main_scope, SEGMENT_OOS_WARM, lens, cost),
                "",
            ]
    lines += [
        "## 4. 칸별 off→on Δ수익 (격리 · oos_warm) — 기준 vs 관통 요구",
        "",
        "각 칸을 격리(단일 포지션)로 돌린 수익률의 재진입 Δ. 북 행(공유 자본)과 자가 다르다. "
        "기준(baseline·base)과 관통 요구(pen_5bp·base)를 나란히 둬 마진 체결 의존을 본다.",
        "",
    ]
    for tf in timeframes:
        lines += [
            f"### {tf} · baseline·base",
            "",
            *_cell_delta_table(cell_rows, tf, "baseline", "base"),
            "",
        ]
        if "pen_5bp" in lenses:
            lines += [
                f"### {tf} · pen_5bp·base",
                "",
                *_cell_delta_table(cell_rows, tf, "pen_5bp", "base"),
                "",
            ]
    lines += [
        "## 판정 — 재진입 이득이 보수화에서 얼마나 남나",
        "",
        verdict(book_rows, main_scope),
        "",
    ]
    if main_scope == "all" and any(r.scope == "15m" for r in book_rows):
        lines += [
            "📌 **15m — 낙관 가정 최대 의존 TF.**",
            "",
            verdict(book_rows, "15m"),
            "",
        ]
    lines += [
        "⚠️ **이 표는 채택 근거가 아니라 측정이다.** 격리 값(WAN-231/242/243)과 셀 직접 비교 "
        "금지(격리는 1포지션·재진입만, 이 표는 북 공유 자본). 「엣지 없음」"
        "(WAN-84/88/111/114/124/151/201)은 탭 기준 진입 판정이라 이 축과 별개다. 채택(재진입 "
        "기본값화)은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지. **기본값·토대 불변**"
        "(측정 전용 · `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-264 비용·체결 보수화 시 재진입 북 이득 잔존")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--lenses", type=str, default=",".join(DEFAULT_LENSES))
    parser.add_argument("--costs", type=str, default=",".join(DEFAULT_COSTS))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-book", type=Path, default=DEFAULT_BOOK_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="새로 돌린 행을 기존 CSV에 병합한다(같은 TF는 갱신, 새 TF는 추가). "
        "15m 무거운 축을 1h·2h·4h 표에 덧붙일 때 쓴다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_book = Path(args.out_book)
    out_md = Path(args.out_md)

    if args.from_csv:
        cell_rows = cells_from_csv(out_cells)
        book_rows = book_from_csv(out_book)
        print(f"[wan264] CSV 로드 — 칸 {len(cell_rows)}행 · 북 {len(book_rows)}행 (재실행 없음)")
    else:
        new_cells, new_book = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            lenses=tuple(x.strip() for x in str(args.lenses).split(",") if x.strip()),
            costs=tuple(c.strip() for c in str(args.costs).split(",") if c.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        if args.append and out_cells.exists() and out_book.exists():
            cell_rows = merge_cells(cells_from_csv(out_cells), new_cells)
            book_rows = merge_book(book_from_csv(out_book), new_book)
        else:
            cell_rows, book_rows = list(new_cells), list(new_book)
        cells_to_frame(cell_rows).to_csv(out_cells, index=False)
        book_to_frame(book_rows).to_csv(out_book, index=False)
        print(f"[wan264] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan264] 북 {len(book_rows)}행 → {out_book}")

    if not cell_rows or not book_rows:
        print("[wan264] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(cell_rows, book_rows, cells_csv=out_cells, book_csv=out_book),
        encoding="utf-8",
    )
    print(f"[wan264] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
