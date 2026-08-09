"""WAN-271 — 재진입 북 band 팔 체결 보수화 (pen_5bp에서 band−freeze 무승부가 유지되나).

## 무엇을 묻나 (WAN-269/270 후속 · WAN-264 의 band 버전)

WAN-269(PR #222)+WAN-270(PR #223)이 재진입 band 팔(진입가 = 봉내 라이브 밴드 재산정)을
채택 레버리지 북(cap_only 5배)에 얹어 off/freeze/band 세 팔을 15m·1h·2h·4h 전 축으로 냈고,
판정은 **사실상 무승부**였다(band−freeze MDD: 4h만 반대이고 15m −1.14%p·1h·2h는 band가 근소
우위 · 청산 0 · LOO 편중 아님). 그런데 그 판정은 **전부** `baseline`(닿으면 체결) **낙관 위의
값**이다. WAN-264가 이미 **freeze 팔**에 대해 체결 보수화(`pen_5bp`)를 냈지만 **band 팔은**
WAN-269/270에서 처음 북에 얹혀 아직 `pen_5bp`로 안 쟀다.

**왜 band 팔이 특히 취약한가**: band는 밴드가에 지정가를 거는 체결이라 큐 우선순위상 가장
안 될 체결이다. WAN-267 격리 분해에서 band 재진입의 `pen_5bp` 잔존율이 **15m 48.7% vs
2h 80.9%** — 15m band 이득의 절반이 관통 요구 한 번에 사라진다. 즉 「무승부」가 baseline
에서만의 무승부인지, 관통을 요구해도 유지되는지가 이 측정의 질문이다.

## 방법 — WAN-269 세 팔 × WAN-264 렌즈 축

WAN-269 의 계산기(`_cell_return_rows`·`_book_scope_rows`)를 **그대로 재사용**하고 렌즈 축
(`baseline` vs `pen_5bp`)만 두른다 — 렌즈마다 `run_cells(fill=…)`로 후보를 다시 생성해야
하므로(관통 벌점이 substep 체결 판정에 적용) `run_cells`를 렌즈 × 재무장규칙(freeze·band)
= 최대 4회 돈다. base 후보는 재무장 규칙과 무관해 팔 사이에서 비트 동일하다.

세 팔(off/freeze/band)의 정의·payload 세트는 WAN-269 와 같다 — **렌즈만 추가**되므로,
`lens="baseline"` 행은 WAN-269/270 CSV 와 **비트 재현**된다(완료기준 3, 회귀 테스트가 고정).

## 무엇을 내나 (완료기준)

* **① 대조표 + 잔존율** — band·freeze·off × (baseline·pen_5bp) × TF × 구간. reentry 이득
  (off→arm)의 렌즈 잔존율 = δ(pen_5bp) ÷ δ(baseline).
* **② band−freeze 무승부 판정** — `pen_5bp`에서 band−freeze 위험 델타(MDD·동시 리스크·청산)가
  부호를 지키나, 아니면 band 이득이 마진 체결에 실려 사라지나 — 숫자로.
* **15m 최우선** — 낙관 가정 최대 의존 칸(WAN-267 잔존 48.7%).

## 성격 · 경고 (그대로 옮길 것)

측정 전용. 핀 없음(오늘 엔진) · 채택 좌표(9종목 × 못 박은 6년, WAN-182) · 채택 북
(cap_only 5배, WAN-213) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).
⚠️ 레버리지 북 **총수익%는 복리 착시**라 실현 수익이 아니다(WAN-213) — 판단은 **MDD ·
최대 동시 리스크 · 청산**의 팔 간 차이와 그 렌즈 잔존율이다. ⚠️ **틱·호가(WAN-98, Canceled)로도
완전 해소 안 됨** — `pen_5bp`는 **관통 벌점**이지 큐 우선순위·부분 체결 모델이 아니라, 이
표도 **상한을 조인 것**이지 실체결이 아니다. 「엣지 없음」(WAN-84/88/111/114/124/151/201/248)
과 **다른 질문**이라 뒤집는 게 아니다. WAN-128 이 신규 리포트를 `baseline` 단독으로 정했으나
**이 이슈는 체결 보수화 자체를 재는 게 목적**이라 `pen_5bp` 옵트인 측정이 규칙에 맞다
(수동 확인 경로). 격리 값(WAN-267)과 **셀 직접 비교 금지**(격리는 1포지션·재진입만, 이 표는
북 공유 자본).

## 재현

```
uv run python -m backtest.wan271_reentry_book_band_stress --tf 1h,2h,4h --jobs 6
uv run python -m backtest.wan271_reentry_book_band_stress --tf 15m --append --jobs 6  # 무거움
uv run python -m backtest.wan271_reentry_book_band_stress --from-csv  # 요약만 재생성
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

from backtest import harness
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
)
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import CellPayload, _short, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan228_reentry_census import ReentryEntryRule
from backtest.wan269_reentry_book_band import (
    ARMS,
    _book_scope_rows,
    _cell_return_rows,
)

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan271_reentry_band_stress_cells.csv"
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan271_reentry_band_stress_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan271_reentry_band_stress_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
ALL_SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS

#: 기본 TF = 1h·2h·4h(컴퓨트 실현 가능). 15m은 셀당 ~37분 × 렌즈 수라 별도 무거운 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1h", "2h", "4h")

#: 정본 구간 — WAN-166 규약(oos_warm 주 · oos 스트레스).
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 체결 렌즈 축. `baseline`(닿으면 체결) · `pen_5bp`(관통 5bp 요구). 후보 집합을 바꾼다.
DEFAULT_LENSES: tuple[str, ...] = ("baseline", "pen_5bp")

#: 잔존율 기준 렌즈 — 이 렌즈에서의 off→arm 델타를 100%로 둔다.
REF_LENS = "baseline"

#: 재무장 규칙 두 세트(WAN-269) — off·freeze는 freeze 세트, band는 band 세트.
_RULES: tuple[ReentryEntryRule, ...] = ("freeze", "band")

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 스코프·구간은 판정에서 뺀다.
MIN_TRADES = 20

#: 잔존율/무승부 문턱 분모가 0에 가까우면 비율이 무의미하다 — `—`로 찍는다.
_RESIDUAL_EPS = 1e-9

#: band−freeze 「무승부」 문턱(WAN-269 판정과 같은 자) — MDD·동시 리스크 ≤ 1%p · 청산 불변.
_WASH_PP = 1.0


# --------------------------------------------------------------------------- #
# 행 모델 (WAN-261/269 스키마에 lens 축 추가)
# --------------------------------------------------------------------------- #


class BandStressCellRow(BaseModel):
    """한 (종목, TF) 칸 × 구간 × 팔 × 렌즈의 격리(단일 포지션) 개별 수익률."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    lens: str
    num_candidates: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float


class BandStressBookRow(BaseModel):
    """한 스코프 × 구간 × 팔 × 렌즈 × 제외종목의 북 집계.

    판정 열은 위험조정(`max_drawdown`·`max_concurrent_risk`·`liquidation_events`)이다
    (총수익%는 복리 착시 — WAN-213).
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    arm: str
    segment: str
    lens: str
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
# 계산 (순수 — payloads → 행) — WAN-269 계산기 재사용 + lens 태그
# --------------------------------------------------------------------------- #


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    lenses: Sequence[str] = DEFAULT_LENSES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    funding_proxy: bool = True,
    log: bool = True,
) -> tuple[list[BandStressCellRow], list[BandStressBookRow]]:
    """렌즈 × 재무장규칙(freeze·band) 격자를 돈다 — 렌즈마다 후보 재생성.

    각 렌즈에서 WAN-269 의 `_cell_return_rows`·`_book_scope_rows`를 그대로 호출해 세 팔
    (off/freeze/band)의 칸·북 행을 얻고, lens 태그를 붙여 담는다. `baseline` 렌즈는 WAN-269
    와 같은 payload·계산기라 그 CSV 와 비트 재현된다(완료기준 3).
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    cell_rows: list[BandStressCellRow] = []
    book_rows: list[BandStressBookRow] = []
    for lens in lenses:
        fill = None if lens == REF_LENS else harness.fill_preset(lens)
        if log:
            print(f"[wan271] 렌즈 {lens}: 재무장 규칙 2세트 후보 생성 중(무거움) …", flush=True)
        payloads_by_rule: dict[str, Sequence[CellPayload]] = {}
        for rule in _RULES:
            payloads = run_cells(
                symbols,
                timeframes,
                start=start,
                end=end,
                jobs=jobs,
                reentry=True,
                reentry_entry_rule=rule,
                fill=fill,
            )
            if funding_proxy:
                payloads, note = apply_funding_proxy(payloads)
                if note and log:
                    print(f"[wan271] 펀딩 대리({lens}/{rule}): {note}", flush=True)
            payloads_by_rule[rule] = payloads
        for cr in _cell_return_rows(payloads_by_rule):
            cell_rows.append(BandStressCellRow(**cr.model_dump(), lens=lens))
        for br in _book_scope_rows(payloads_by_rule, start_ms=start_ms, end_ms=end_ms):
            book_rows.append(BandStressBookRow(**br.model_dump(), lens=lens))
    return cell_rows, book_rows


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _pick_book(
    rows: Sequence[BandStressBookRow],
    *,
    scope: str,
    arm: str,
    segment: str,
    lens: str,
    exclude: str = "",
) -> BandStressBookRow | None:
    for r in rows:
        if (
            r.scope == scope
            and r.arm == arm
            and r.segment == segment
            and r.lens == lens
            and r.exclude_symbol == exclude
        ):
            return r
    return None


def _main_scope(book_rows: Sequence[BandStressBookRow]) -> str:
    if any(r.scope == "all" for r in book_rows):
        return "all"
    scopes = sorted({r.scope for r in book_rows}, key=timeframe_to_ms)
    return scopes[0] if scopes else ""


@dataclass(frozen=True)
class _PairDelta:
    """한 (스코프, 구간, 렌즈)의 arm − base_arm 델타 묶음.

    off→arm(reentry 이득)이면 base_arm="off", band−freeze(무승부)면 base_arm="freeze".
    """

    lens: str
    base_arm: str
    arm: str
    base_trades: int
    arm_trades: int
    d_mdd: float
    """MDD 델타(분수, 렌더에서 ×100)."""
    d_risk: float
    d_return: float
    """북 총수익 델타(분수). ⚠️ 복리 착시 — 방향만."""
    d_liq: int
    sample_ok: bool


def _pair_delta(
    rows: Sequence[BandStressBookRow],
    *,
    scope: str,
    segment: str,
    lens: str,
    base_arm: str,
    arm: str,
) -> _PairDelta | None:
    a = _pick_book(rows, scope=scope, arm=base_arm, segment=segment, lens=lens)
    b = _pick_book(rows, scope=scope, arm=arm, segment=segment, lens=lens)
    if a is None or b is None:
        return None
    return _PairDelta(
        lens=lens,
        base_arm=base_arm,
        arm=arm,
        base_trades=a.num_trades,
        arm_trades=b.num_trades,
        d_mdd=b.max_drawdown - a.max_drawdown,
        d_risk=b.max_concurrent_risk - a.max_concurrent_risk,
        d_return=b.total_return - a.total_return,
        d_liq=b.liquidation_events - a.liquidation_events,
        sample_ok=a.sample_ok and b.sample_ok,
    )


def _residual_pct(value: float, ref: float) -> str:
    """δ(렌즈) ÷ δ(기준 렌즈) × 100. 분모가 0에 가까우면 무의미하므로 `—`."""
    if abs(ref) < _RESIDUAL_EPS:
        return "—"
    return f"{value / ref * 100:.0f}%"


def _wash_holds(d: _PairDelta) -> bool:
    """band−freeze 위험 델타가 「무승부」인가 — WAN-269 판정과 같은 자."""
    return abs(d.d_mdd) * 100 <= _WASH_PP and abs(d.d_risk) * 100 <= _WASH_PP and d.d_liq == 0


def verdict(book_rows: Sequence[BandStressBookRow], scope: str) -> str:
    """pen_5bp에서 band−freeze 무승부가 유지되나 — 주 수치 = 이 스코프 · oos_warm.

    숫자는 전부 행에서 계산한다(문장에 박으면 재실행 뒤 거짓말한다 — WAN-164 패턴).
    두 가지를 답한다: (1) band−freeze 위험 델타가 렌즈를 바꿔도 무승부인가, (2) band 의
    reentry 이득(off→band)이 관통 요구로 얼마나 남나(마진 체결 의존).
    """
    base = _pair_delta(
        book_rows,
        scope=scope,
        segment=SEGMENT_OOS_WARM,
        lens=REF_LENS,
        base_arm="freeze",
        arm="band",
    )
    pen = _pair_delta(
        book_rows,
        scope=scope,
        segment=SEGMENT_OOS_WARM,
        lens="pen_5bp",
        base_arm="freeze",
        arm="band",
    )
    if base is None or pen is None:
        return (
            f"**판정 불가** — {scope}·oos_warm 의 baseline·pen_5bp band/freeze 행이 모두 "
            "있어야 합니다."
        )
    base_wash = _wash_holds(base)
    pen_wash = _wash_holds(pen)
    sign_kept = (base.d_mdd >= 0) == (pen.d_mdd >= 0)
    if base_wash and pen_wash:
        head = (
            "**무승부가 pen_5bp에서도 유지된다.** baseline 에서 band−freeze 위험 차이가 작았고"
            "(MDD·동시 리스크 ≤ 1%p · 청산 불변), 관통을 요구해도 그 무승부가 유지된다 — band 의 "
            "미세 우위/열위가 마진 체결의 산물이 아니라는 뜻이되, 애초에 판정할 우위가 없었다."
        )
    elif base_wash and not pen_wash:
        if pen.d_mdd > 0:
            direction = (
                "band 가 관통 요구에서 freeze 보다 위험해진다 — band 의 겉보기 대등함이 밴드가 "
                "마진 체결에 실려 있었다는 신호다(밴드가 체결이 큐 우선순위상 가장 안 될 체결)."
            )
        else:
            direction = (
                "다만 그 방향은 band 에 유리하다 — 관통을 요구하면 freeze 재진입이 band 보다 더 "
                "큰 낙폭을 얹는다(아래 잔존 표의 off→freeze MDD 잔존이 off→band 보다 크다). "
                "band 은 밴드가 마진 체결이 빠지면서 오히려 덜 위험한 재진입이 된다 — 어느 쪽이든 "
                "「무승부」는 baseline 낙관의 산물이지 관통 요구에서 유지되는 성질이 아니다."
            )
        head = (
            "**baseline 무승부가 pen_5bp에서 깨진다.** 관통을 요구하면 band−freeze 위험 차이가 "
            f"1%p 문턱을 넘는다 — {direction}"
        )
    else:
        verb = "유지된다" if sign_kept else "부호가 뒤집힌다"
        head = (
            f"**baseline 에서 이미 band−freeze 위험 차이가 작지 않았다"
            f"({base.d_mdd * 100:+.2f}%p).** 관통 요구에서 그 델타의 부호가 {verb}."
        )
    off_band_base = _pair_delta(
        book_rows,
        scope=scope,
        segment=SEGMENT_OOS_WARM,
        lens=REF_LENS,
        base_arm="off",
        arm="band",
    )
    off_band_pen = _pair_delta(
        book_rows,
        scope=scope,
        segment=SEGMENT_OOS_WARM,
        lens="pen_5bp",
        base_arm="off",
        arm="band",
    )
    coords = (
        f"{scope}·oos_warm band−freeze: baseline MDD Δ{base.d_mdd * 100:+.2f}%p · 동시 리스크 "
        f"Δ{base.d_risk * 100:+.2f}%p · 청산 Δ{base.d_liq:+d} · 거래 {base.base_trades}→"
        f"{base.arm_trades} → pen_5bp MDD Δ{pen.d_mdd * 100:+.2f}%p · 동시 리스크 "
        f"Δ{pen.d_risk * 100:+.2f}%p · 청산 Δ{pen.d_liq:+d} · 거래 {pen.base_trades}→"
        f"{pen.arm_trades}."
    )
    gain = ""
    if off_band_base is not None and off_band_pen is not None:
        r_tr = _residual_pct(
            off_band_pen.arm_trades - off_band_pen.base_trades,
            off_band_base.arm_trades - off_band_base.base_trades,
        )
        gain = (
            f" 재진입 이득(off→band) 잔존: MDD 델타 "
            f"{_residual_pct(off_band_pen.d_mdd, off_band_base.d_mdd)} · 거래 델타 {r_tr}"
            "(관통을 요구하면 band 재진입이 북에 더하는 거래·위험이 얼마나 남나)."
        )
    return (
        f"{head} {coords}{gain} ⚠️ 총수익% 변화는 복리 착시라 판정에 넣지 않는다(WAN-213) — "
        "판단은 위 낙폭·동시 리스크·청산이다. `pen_5bp`는 **관통 벌점**이지 큐 우선순위·부분 "
        "체결 모델이 아니라(틱·호가 WAN-98 Canceled) 이 표는 상한을 조인 것이지 실체결이 "
        "아니다. 「엣지가 있나」는 묻지 않는다 — band 가격 효과를 북에 얹은 전체 위험을 렌즈로 "
        "스트레스한 측정이다(채택은 재-베이스라인 = 사용자 결정)."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[BandStressCellRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [r.model_dump() for r in rows], columns=list(BandStressCellRow.model_fields)
    )


def book_to_frame(rows: Sequence[BandStressBookRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [r.model_dump() for r in rows], columns=list(BandStressBookRow.model_fields)
    )


def cells_from_csv(path: Path) -> list[BandStressCellRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [BandStressCellRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def book_from_csv(path: Path) -> list[BandStressBookRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [BandStressBookRow.model_validate(rec) for rec in records]


def merge_cells(
    existing: Sequence[BandStressCellRow], new: Sequence[BandStressCellRow]
) -> list[BandStressCellRow]:
    new_tfs = {r.timeframe for r in new}
    kept = [r for r in existing if r.timeframe not in new_tfs]
    return [*kept, *new]


def merge_book(
    existing: Sequence[BandStressBookRow], new: Sequence[BandStressBookRow]
) -> list[BandStressBookRow]:
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


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _gain_residual_table(
    book_rows: Sequence[BandStressBookRow], scope: str, segment: str
) -> list[str]:
    """reentry 이득(off→arm) 잔존 표 — arm ∈ {freeze, band} × 렌즈, 기준 렌즈 대비 잔존율."""
    lenses = [
        lens
        for lens in DEFAULT_LENSES
        if any(r.lens == lens and r.scope == scope for r in book_rows)
    ]
    lines = [
        "| 팔 | 렌즈 | off→arm MDDΔ | (잔존) | 리스크Δ | (잔존) | 거래 off→arm | (잔존) | 청산Δ |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for arm in ("freeze", "band"):
        ref = _pair_delta(
            book_rows, scope=scope, segment=segment, lens=REF_LENS, base_arm="off", arm=arm
        )
        for lens in lenses:
            d = _pair_delta(
                book_rows, scope=scope, segment=segment, lens=lens, base_arm="off", arm=arm
            )
            if d is None:
                continue
            gate = "" if d.sample_ok else " ⚠️"
            is_ref = lens == REF_LENS
            if ref is None or is_ref:
                r_mdd = r_risk = r_tr = "기준" if is_ref else "—"
            else:
                r_mdd = _residual_pct(d.d_mdd, ref.d_mdd)
                r_risk = _residual_pct(d.d_risk, ref.d_risk)
                r_tr = _residual_pct(d.arm_trades - d.base_trades, ref.arm_trades - ref.base_trades)
            lines.append(
                f"| {arm} | {lens} | {d.d_mdd * 100:+.2f}%p | {r_mdd} | "
                f"{d.d_risk * 100:+.2f}%p | {r_risk} | "
                f"{d.base_trades}→{d.arm_trades}{gate} | {r_tr} | {d.d_liq:+d} |"
            )
    return lines


def _band_freeze_table(
    book_rows: Sequence[BandStressBookRow], scope: str, segment: str
) -> list[str]:
    """band−freeze 위험 델타 표(핵심 무승부 질문) — 렌즈별 %p + 무승부 여부.

    분모가 작아 잔존율이 무의미하므로 %p 를 두 렌즈에 나란히 낸다(WAN-269 판정과 같은 자).
    """
    lenses = [
        lens
        for lens in DEFAULT_LENSES
        if any(r.lens == lens and r.scope == scope for r in book_rows)
    ]
    lines = [
        "| 렌즈 | band−freeze MDDΔ | 동시 리스크Δ | 청산Δ | 거래 freeze→band | 무승부? |",
        "| -- | --: | --: | --: | --: | :-: |",
    ]
    for lens in lenses:
        d = _pair_delta(
            book_rows, scope=scope, segment=segment, lens=lens, base_arm="freeze", arm="band"
        )
        if d is None:
            continue
        gate = "" if d.sample_ok else " ⚠️"
        wash = "○" if _wash_holds(d) else "✕"
        lines.append(
            f"| {lens} | {d.d_mdd * 100:+.2f}%p | {d.d_risk * 100:+.2f}%p | {d.d_liq:+d} | "
            f"{d.base_trades}→{d.arm_trades}{gate} | {wash} |"
        )
    return lines


def _book_arm_table(
    book_rows: Sequence[BandStressBookRow], scope: str, segment: str, lens: str
) -> list[str]:
    lines = [
        "| 팔 | 거래 | 총수익%† | MDD | 수익/MDD | 최대동시리스크 | 청산 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for arm in ARMS:
        r = _pick_book(book_rows, scope=scope, arm=arm, segment=segment, lens=lens)
        if r is None:
            continue
        gate = "" if r.sample_ok else " ⚠️"
        lines.append(
            f"| 재진입 {arm} | {r.num_trades}{gate} | {_pct(r.total_return)} | "
            f"{_pct(r.max_drawdown)} | {_rr(r.return_over_mdd)} | "
            f"{_pct(r.max_concurrent_risk)} | {r.liquidation_events} | {r.peak_concurrency} |"
        )
    return lines


def _cell_delta_table(
    cell_rows: Sequence[BandStressCellRow], timeframe: str, lens: str
) -> list[str]:
    """oos_warm 칸별 band−freeze Δ수익(격리) — 이 렌즈에서."""
    scoped = [
        r
        for r in cell_rows
        if r.timeframe == timeframe and r.segment == SEGMENT_OOS_WARM and r.lens == lens
    ]
    by_key = {(r.symbol, r.arm): r for r in scoped}
    symbols = sorted({r.symbol for r in scoped})
    lines = [
        "| 심볼 | freeze 거래 | freeze 수익 | band 거래 | band 수익 | Δ수익(band−freeze) |",
        "| -- | --: | --: | --: | --: | --: |",
    ]
    for symbol in symbols:
        freeze = by_key.get((symbol, "freeze"))
        band = by_key.get((symbol, "band"))
        if freeze is None or band is None:
            continue
        delta = (band.total_return - freeze.total_return) * 100
        lines.append(
            f"| {_short(symbol)} | {freeze.num_trades} | {_pct(freeze.total_return)} | "
            f"{band.num_trades} | {_pct(band.total_return)} | {delta:+.2f}%p |"
        )
    return lines


def build_summary_markdown(
    cell_rows: Sequence[BandStressCellRow],
    book_rows: Sequence[BandStressBookRow],
    *,
    cells_csv: Path,
    book_csv: Path,
) -> str:
    timeframes = sorted({r.timeframe for r in cell_rows}, key=timeframe_to_ms)
    main_scope = _main_scope(book_rows)
    tf_label = ",".join(timeframes)
    lenses = [lens for lens in DEFAULT_LENSES if any(r.lens == lens for r in book_rows)]
    lens_label = " · ".join(lenses)
    lines = [
        "# WAN-271 — 재진입 북 band 팔 체결 보수화 (pen_5bp에서 band−freeze 무승부가 유지되나)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`·채택 "
        "북 cap_only 5배) 돌리며 옛 핀은 하나도 물려받지 않는다. 못 박은 6년 창(WAN-182) · "
        "기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        f"**축** 세 팔(off/freeze/band, WAN-269) × 체결 렌즈 {lens_label}. WAN-269/270 이 낸 "
        "band−freeze 무승부(전부 `baseline` 낙관 위)가 **관통 요구(`pen_5bp`)에서도 유지되나**를 "
        "잰다. band 는 밴드가에 지정가를 거는 체결이라 큐 우선순위상 가장 안 될 체결이고"
        "(WAN-267 격리 잔존 15m 48.7% vs 2h 80.9%), 렌즈를 조이면 가장 먼저 사라질 후보다.",
        "",
        "WAN-269 의 세 팔 계산기(`_cell_return_rows`·`_book_scope_rows`)를 그대로 재사용하고 "
        "렌즈 축만 두른다 — 렌즈마다 후보를 다시 생성한다(관통 벌점이 substep 체결 판정에 "
        "적용). `baseline` 행은 WAN-269/270 CSV 와 **비트 재현**된다(완료기준 3, 회귀 테스트).",
        "",
        f"재현: `uv run python -m backtest.wan271_reentry_book_band_stress --tf {tf_label} "
        "--jobs 6` (요약만: `--from-csv`). 15m은 셀당 ~37분 × 렌즈 수라 별도 무거운 실행"
        f"(`--tf 15m --append`). 원자료: `{cells_csv}`(칸별) · `{book_csv}`(북 격자).",
        "",
        "⚠️ **총수익%는 복리 착시** — 레버리지 북의 %는 수백~수천 거래 복리 값이라 실현 수익이 "
        "아니다(WAN-213). 판단은 **MDD · 최대 동시 리스크 · 청산 건수**의 팔 간 차이(band−freeze가 "
        "핵심)와 그 렌즈 잔존율이다. ⚠️ **틱·호가(WAN-98, Canceled)로도 완전 해소 안 됨** — "
        "`pen_5bp`는 관통 벌점이지 큐 우선순위·부분 체결 모델이 아니라, 이 표는 상한을 조인 것이지 "
        "실체결이 아니다.",
        "",
        f"## 1. band−freeze 무승부 (완료기준 ②) — {main_scope}",
        "",
        "핵심 질문: `baseline` 무승부가 관통 요구에서도 유지되나. band−freeze 위험 델타를 "
        "렌즈별로 %p 로 병기한다(분모가 작아 잔존율은 무의미). 무승부 = MDD·동시 리스크 ≤ 1%p · "
        "청산 불변(WAN-269 판정과 같은 자).",
        "",
        "### oos_warm (주 수치)",
        "",
        *_band_freeze_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "### oos (차가운 스트레스)",
        "",
        *_band_freeze_table(book_rows, main_scope, SEGMENT_OOS),
        "",
    ]
    if main_scope == "all":
        lines += ["## 2. TF 스코프별 band−freeze 무승부 (oos_warm 주)", ""]
        for tf in timeframes:
            lines += [f"### {tf}", "", *_band_freeze_table(book_rows, tf, SEGMENT_OOS_WARM), ""]
    lines += [
        f"## 3. reentry 이득(off→arm) 렌즈 잔존 표 (완료기준 ①) — {main_scope} · oos_warm",
        "",
        "재진입이 북에 더하는 위험(off→freeze·off→band)이 기준 렌즈(`baseline`) 대비 관통 요구에서 "
        "몇 % 남는지. 잔존율 분모(기준 델타)가 0에 가까우면 `—`(방향만 읽는다).",
        "",
        *_gain_residual_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "### oos (차가운 스트레스)",
        "",
        *_gain_residual_table(book_rows, main_scope, SEGMENT_OOS),
        "",
        f"## 4. 북 팔 상세 (oos_warm · 렌즈별) — {main_scope}",
        "",
        "각 렌즈의 off/freeze/band 북 행. 총수익%는 복리 착시라 †.",
        "",
    ]
    for lens in lenses:
        lines += [
            f"### {lens}",
            "",
            *_book_arm_table(book_rows, main_scope, SEGMENT_OOS_WARM, lens),
            "",
        ]
    lines += [
        "## 5. 칸별 band−freeze Δ수익 (격리 · oos_warm) — 기준 vs 관통 요구",
        "",
        "각 칸을 격리(단일 포지션)로 돌린 수익률의 band−freeze Δ. 북 행(공유 자본)과 자가 다르다. "
        "baseline 과 pen_5bp 를 나란히 둬 band 우위의 마진 체결 의존을 본다.",
        "",
    ]
    for tf in timeframes:
        for lens in lenses:
            lines += [
                f"### {tf} · {lens}",
                "",
                *_cell_delta_table(cell_rows, tf, lens),
                "",
            ]
    lines += [
        "## 판정 — pen_5bp에서 band−freeze 무승부가 유지되나",
        "",
        verdict(book_rows, main_scope),
        "",
    ]
    if main_scope == "all" and any(r.scope == "15m" for r in book_rows):
        lines += [
            "📌 **15m — 낙관 가정 최대 의존 TF(WAN-267 격리 잔존 48.7%).**",
            "",
            verdict(book_rows, "15m"),
            "",
        ]
    lines += [
        "⚠️ **이 표는 채택 근거가 아니라 측정이다.** 격리 값(WAN-267)과 셀 직접 비교 금지"
        "(격리는 1포지션·재진입만, 이 표는 북 공유 자본). 「엣지 없음」"
        "(WAN-84/88/111/114/124/151/201/248)은 탭 기준 진입 판정이라 이 축과 별개다. 채택(재진입 "
        "기본값화 · band 진입가)은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지(큐 우선순위 "
        "WAN-98 Canceled · 라이브 충실도 WAN-45 선행). **기본값·토대 불변**(측정 전용 · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-271 재진입 북 band 팔 체결 보수화(pen_5bp)")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--lenses", type=str, default=",".join(DEFAULT_LENSES))
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
        print(f"[wan271] CSV 로드 — 칸 {len(cell_rows)}행 · 북 {len(book_rows)}행 (재실행 없음)")
    else:
        new_cells, new_book = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            lenses=tuple(x.strip() for x in str(args.lenses).split(",") if x.strip()),
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
        print(f"[wan271] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan271] 북 {len(book_rows)}행 → {out_book}")

    if not cell_rows or not book_rows:
        print("[wan271] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(cell_rows, book_rows, cells_csv=out_cells, book_csv=out_book),
        encoding="utf-8",
    )
    print(f"[wan271] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
