"""WAN-269 — 재진입을 채택 북에 켜되 진입가 = band(볼린저 재산정)로 (freeze→band 재실행).

## 무엇을 묻나 (WAN-261 후속 · WAN-267 리더를 북에 얹기)

WAN-261/262가 「익절 후 존 내 재진입」을 채택 레버리지 북(cap_only 5배)에 통째로 켜서 손익·
MDD·위험을 쟀지만, 그 재진입 후보는 `_iter_reentries`의 **기본값** `entry_rule="freeze"`
(첫 체결가 고정)로만 돌았다. 그런데 WAN-267/268 **격리 완전 분해**가 진입가 3팔
(freeze/zone/**band**)을 비교해 **band(재무장 순간 봉내 라이브 밴드 재산정)가 리더**라고
판정했다. 즉 「제일 나은 재진입 진입가(band)를 실제 채택 북에 통째로 켠 손익·MDD·위험」은
아직 없었다 — 격리 상한(WAN-267)은 자본 경합·슬롯·북 상한을 모델링하지 않으므로 북에
얹으면 이보다 줄어든다. 그 실측이 이 이슈다.

이 이슈는 **「그 band 가격 효과를 채택 북에 실제로 얹으면 freeze 대비 전체 손익·위험이
어떻게 되나」** 하나만 답한다 — **「엣지가 있나」는 묻지 않는다**(WAN-261과 같은 질문 성격).

## 방법 — 세 팔 (WAN-261의 두 팔에 band 하나 추가)

`reentry_candidates`(WAN-228) → `reentry_candidates_for_window`(WAN-169) → `run_cells`에
`entry_rule` 인자를 배선했다(기본 `"freeze"`, 안 넘기면 wan261/262 CSV 비트 재현). 세 팔:

* **off**: 채택 북(base 재탭 후보만) = 인자 없는 `backtest.run` = wan261 off 팔.
* **freeze**: 재진입을 첫 체결가 고정으로 북에 얹음 = wan261 on 팔(검산 기준점).
* **band**: 재진입을 봉내 라이브 밴드 재산정으로 북에 얹음 = WAN-267 리더 팔을 북에.

off·freeze는 **한 payload 세트**(freeze 재무장)에서 나온다 — off는 base만, freeze는
base+재진입. band는 별도 payload 세트(band 재무장)에서 base+재진입으로 낸다. base 후보는
`entry_rule`과 무관해 두 세트에서 **비트 동일**하므로 off는 어느 세트로 계산해도 같다.

## 무엇을 내나 (완료기준)

* **① 칸별 개별 수익률** — 각 (종목, TF) 칸을 격리(단일 포지션)로 돌린 수익률(off/freeze/band).
* **② 북 전체** — 스코프별(각 TF + 전체 `all`) 집계 · **③ MDD · ④ 최대 동시 리스크 ·
  ⑤ 청산** 병기. 팔 간 차이(band−freeze, band−off)로 읽는다.
* **leave-one-out** — 종목 하나씩 빼 band 팔의 이득/손해가 특정 종목에 쏠리는지.

## 성격 · 경고 (그대로 옮길 것)

측정 전용. 렌즈 `baseline` 단독(WAN-128) · 핀 없음(오늘 엔진) · 채택 좌표(9종목 × 못 박은
6년, WAN-182) · 채택 북(cap_only 5배, WAN-213) · 기본값·토대 불변
(`ALPHABLOCK_LIVE_TRADING=false` 유지). ⚠️ 레버리지 북 **총수익%는 복리 착시**라 실현 수익이
아니다(WAN-213) — 판단은 **MDD · 최대 동시 리스크 · 청산**의 팔 간 차이다. ⚠️ 전부
`baseline`(닿으면 체결) 낙관 위 값이고 **band 재진입은 밴드가 체결이라 큐 우선순위상 가장
안 될 체결에 기댄다** — 체결 보수화(pen_5bp)가 핵심이고 15m은 특히 못 믿는다(격리에서 15m
잔존 48.7% vs 2h 80.9%, WAN-267). 「엣지 없음」(WAN-84/88/111/114/124/151/201/248)과 **다른
질문**이라 뒤집는 게 아니다. 격리 값(WAN-267)과 **셀 직접 비교 금지**(격리는 1포지션·재진입만,
이 표는 북 공유 자본).

## 재현

```
uv run python -m backtest.wan269_reentry_book_band --tf 1h,2h,4h --jobs 6
uv run python -m backtest.wan269_reentry_book_band --tf 15m --append --jobs 6   # 무거움(셀당 ~37분)
uv run python -m backtest.wan269_reentry_book_band --from-csv                   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest import book_cli, harness
from backtest.book_cli import BookRunRow
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
)
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
from backtest.wan228_reentry_census import ReentryEntryRule
from backtest.wan261_reentry_book import BookScopeRow, CellReturnRow

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan269_reentry_book_band_cells.csv"
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan269_reentry_book_band_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan269_reentry_book_band_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 기본 TF = 1h·2h·4h(컴퓨트 실현 가능). 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1h", "2h", "4h")

#: 정본 구간 — WAN-166 규약(oos_warm 주 · oos 스트레스).
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 세 팔 — off(채택 북) / freeze(WAN-261 on) / band(WAN-267 리더를 북에).
ARMS: tuple[str, ...] = ("off", "freeze", "band")

#: 각 팔이 어느 payload 세트를 쓰고 재진입을 얹는지.
#: `(재무장 규칙 세트, include_reentry)` — off는 base만(어느 세트든 base 동일).
ARM_SPEC: dict[str, tuple[str, bool]] = {
    "off": ("freeze", False),
    "freeze": ("freeze", True),
    "band": ("band", True),
}

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 스코프·구간은 판정에서 뺀다.
MIN_TRADES = 20


# --------------------------------------------------------------------------- #
# 계산 (순수 — payloads → 행)
# --------------------------------------------------------------------------- #


def _cell_return_rows(payloads_by_rule: dict[str, Sequence[CellPayload]]) -> list[CellReturnRow]:
    """칸별 개별(격리 단일 포지션) 수익률 — off·freeze·band 세 팔.

    후보 선택은 북과 **같은** `_segment_cells`를 쓴다(한 칸만 넘겨) — 팔이 실제로 북에서
    보는 것과 같은 후보 집합의 격리 성과라, 북과 격리를 나란히 읽을 수 있다. base 후보는
    두 payload 세트에서 비트 동일하므로 팔마다 자기 세트의 칸을 쓴다.
    """
    rows: list[CellReturnRow] = []
    # 칸 순서·심볼은 두 세트가 같으므로 freeze 세트의 칸 목록을 기준으로 짝짓는다.
    freeze_payloads = payloads_by_rule["freeze"]
    band_by_key = {(p.symbol, p.timeframe): p for p in payloads_by_rule["band"]}
    for freeze_payload in freeze_payloads:
        key = (freeze_payload.symbol, freeze_payload.timeframe)
        cfg = harness.build_config(freeze_payload.timeframe)
        for segment in SEGMENTS:
            for arm in ARMS:
                rule, include = ARM_SPEC[arm]
                payload = freeze_payload if rule == "freeze" else band_by_key[key]
                cell = _segment_cells([payload], segment, "", include_reentry=include)[0]
                num_trades, win_rate, total_return, mdd = _isolated_metrics(
                    cell.candidates, cfg, freeze_payload.timeframe, cell.funding_rates
                )
                rows.append(
                    CellReturnRow(
                        symbol=freeze_payload.symbol,
                        timeframe=freeze_payload.timeframe,
                        segment=segment,
                        arm=arm,
                        num_candidates=len(cell.candidates),
                        num_trades=num_trades,
                        win_rate=win_rate,
                        total_return=total_return,
                        max_drawdown=mdd,
                    )
                )
    return rows


def _book_scope_rows(
    payloads_by_rule: dict[str, Sequence[CellPayload]], *, start_ms: int, end_ms: int
) -> list[BookScopeRow]:
    """스코프(각 TF + 전체) × 구간 × 팔 × leave-one-out의 북 집계.

    `book_cli.build_book_rows`(채택 북)를 팔마다 그 팔의 payload 세트로 호출한다 — off·freeze는
    freeze 세트(off는 include_reentry=False), band는 band 세트다.
    """
    freeze_payloads = list(payloads_by_rule["freeze"])
    timeframes = sorted({p.timeframe for p in freeze_payloads}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    symbols = sorted({_short(p.symbol) for p in freeze_payloads})
    rows: list[BookScopeRow] = []
    for scope in scopes:
        for arm in ARMS:
            rule, include = ARM_SPEC[arm]
            arm_payloads = list(payloads_by_rule[rule])
            scoped = (
                arm_payloads
                if scope == "all"
                else [p for p in arm_payloads if p.timeframe == scope]
            )
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
                    include_reentry=include,
                )
                for br in book_rows:
                    rows.append(_to_scope_row(br, scope=scope, arm=arm, exclude=exclude))
    return rows


def _to_scope_row(br: BookRunRow, *, scope: str, arm: str, exclude: str) -> BookScopeRow:
    return BookScopeRow(
        scope=scope,
        arm=arm,
        segment=br.segment,
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
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    funding_proxy: bool = True,
    log: bool = True,
) -> tuple[list[CellReturnRow], list[BookScopeRow]]:
    """9종목 × TF 칸을 재무장 규칙 두 세트(freeze·band)로 돌고, 세 팔의 칸·북 행을 낸다.

    `run_cells`를 두 번 부른다(freeze·band) — base 후보는 규칙과 무관해 두 세트에서 비트
    동일하고, 재진입 후보만 달라진다(off는 base만이라 어느 세트든 같다). 두 번 도는 대가로
    base 후보 생성이 두 번 돌지만, 이 측정의 정확성이 컴퓨트 절약보다 우선이다.
    """
    payloads_by_rule: dict[str, Sequence[CellPayload]] = {}
    rules: tuple[ReentryEntryRule, ...] = ("freeze", "band")
    for rule in rules:
        payloads = run_cells(
            symbols,
            timeframes,
            start=start,
            end=end,
            jobs=jobs,
            reentry=True,
            reentry_entry_rule=rule,
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        )
        if funding_proxy:
            payloads, note = apply_funding_proxy(payloads)
            if note and log:
                print(f"[wan269] 펀딩 대리({rule}): {note}", flush=True)
        payloads_by_rule[rule] = payloads
    cell_rows = _cell_return_rows(payloads_by_rule)
    book_rows = _book_scope_rows(
        payloads_by_rule, start_ms=parse_date_ms(start), end_ms=parse_date_ms(end)
    )
    return cell_rows, book_rows


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _pick_book(
    rows: Sequence[BookScopeRow], *, scope: str, arm: str, segment: str, exclude: str = ""
) -> BookScopeRow | None:
    for r in rows:
        if (
            r.scope == scope
            and r.arm == arm
            and r.segment == segment
            and r.exclude_symbol == exclude
        ):
            return r
    return None


def _main_scope(book_rows: Sequence[BookScopeRow]) -> str:
    if any(r.scope == "all" for r in book_rows):
        return "all"
    scopes = sorted({r.scope for r in book_rows}, key=timeframe_to_ms)
    return scopes[0] if scopes else ""


def _risk_delta(a: BookScopeRow, b: BookScopeRow) -> tuple[float, float, int]:
    """b − a 의 (MDD %p, 최대 동시 리스크 %p, 청산 건수) 델타."""
    return (
        (b.max_drawdown - a.max_drawdown) * 100,
        (b.max_concurrent_risk - a.max_concurrent_risk) * 100,
        b.liquidation_events - a.liquidation_events,
    )


def verdict(book_rows: Sequence[BookScopeRow]) -> str:
    """band를 북에 얹으면 freeze(현행 재진입) 대비 위험이 어떻게 바뀌나 — MDD·동시 리스크·청산.

    총수익%는 복리 착시라 판정에 쓰지 않는다(WAN-213). 주 수치 = `all` 스코프 · oos_warm.
    band−freeze(핵심)와 band−off(재진입 없음 대비)를 함께 낸다. 숫자는 전부 행에서 계산한다
    (문장에 박으면 재실행 뒤 거짓말한다 — WAN-164 패턴).
    """
    scope = _main_scope(book_rows)
    off = _pick_book(book_rows, scope=scope, arm="off", segment=SEGMENT_OOS_WARM)
    freeze = _pick_book(book_rows, scope=scope, arm="freeze", segment=SEGMENT_OOS_WARM)
    band = _pick_book(book_rows, scope=scope, arm="band", segment=SEGMENT_OOS_WARM)
    if off is None or freeze is None or band is None:
        return "**판정 불가** — 주 스코프의 oos_warm 세 팔 행이 모두 있어야 합니다."
    d_mdd, d_risk, d_liq = _risk_delta(freeze, band)
    o_mdd, o_risk, o_liq = _risk_delta(off, band)
    coords = (
        f"{scope}·oos_warm: MDD off {off.max_drawdown * 100:.2f}% → freeze "
        f"{freeze.max_drawdown * 100:.2f}% → band {band.max_drawdown * 100:.2f}% "
        f"(band−freeze Δ{d_mdd:+.2f}%p · band−off Δ{o_mdd:+.2f}%p) · 최대 동시 리스크 "
        f"{off.max_concurrent_risk * 100:.2f}% → {freeze.max_concurrent_risk * 100:.2f}% → "
        f"{band.max_concurrent_risk * 100:.2f}% (band−freeze Δ{d_risk:+.2f}%p) · 청산 "
        f"{off.liquidation_events} → {freeze.liquidation_events} → {band.liquidation_events} "
        f"(band−freeze Δ{d_liq:+d} · band−off Δ{o_liq:+d}) · 거래 {off.num_trades} → "
        f"{freeze.num_trades} → {band.num_trades}"
    )
    if d_liq > 0 or d_mdd > 1.0 or d_risk > 1.0:
        head = "**band가 freeze보다 북 위험을 키운다.**"
    elif d_liq == 0 and abs(d_mdd) <= 1.0 and abs(d_risk) <= 1.0:
        head = "**band와 freeze의 위험 차이는 작다(MDD·동시 리스크 ≤ 1%p · 청산 불변).**"
    else:
        head = "**band가 freeze보다 북 위험을 소폭 줄인다.**"
    return (
        f"{head} {coords}. ⚠️ 총수익% 변화는 복리 착시라 판정에 넣지 않는다(WAN-213) — 판단은 "
        "위 낙폭·위험이다. band 재진입은 밴드가 체결이라 `baseline`(닿으면 체결) 낙관에 가장 "
        "크게 의존한다(큐 우선순위상 가장 안 될 체결) — 체결 보수화(pen_5bp)로 얼마나 남는지가 "
        "핵심이며 15m이 특히 못 믿을 값이다(WAN-267). 이 표는 「엣지가 있나」를 묻지 않는다 — "
        "band 가격 효과를 북에 얹은 전체 손익·위험의 측정이다(채택은 재-베이스라인 = 사용자 결정)."
    )


def _verdict_15m(book_rows: Sequence[BookScopeRow]) -> str:
    """15m 스코프가 있으면 band−freeze 위험 델타 + 낙관 가정 최대 의존 경고를 낸다.

    주 판정(`verdict`)은 `all`(1h·2h·4h) 스코프라 15m이 빠진다. 15m은 봉이 짧아 「스치듯 닿은
    체결」(`baseline` 낙관)에 네 TF 중 가장 크게 의존하므로(WAN-267/231/203) 별도로 명시한다.
    숫자는 행에서 계산한다. 15m 스코프가 없으면 빈 문자열이라 렌더러가 문단을 건너뛴다.
    """
    freeze = _pick_book(book_rows, scope="15m", arm="freeze", segment=SEGMENT_OOS_WARM)
    band = _pick_book(book_rows, scope="15m", arm="band", segment=SEGMENT_OOS_WARM)
    if freeze is None or band is None:
        return ""
    d_mdd, d_risk, d_liq = _risk_delta(freeze, band)
    return (
        f"📌 **15m — 낙관 가정 최대 의존 TF.** 15m·oos_warm: MDD freeze "
        f"{freeze.max_drawdown * 100:.2f}% → band {band.max_drawdown * 100:.2f}% "
        f"(Δ{d_mdd:+.2f}%p) · 최대 동시 리스크 {freeze.max_concurrent_risk * 100:.2f}% → "
        f"{band.max_concurrent_risk * 100:.2f}% (Δ{d_risk:+.2f}%p) · 청산 "
        f"{freeze.liquidation_events} → {band.liquidation_events} (Δ{d_liq:+d}) · 거래 "
        f"{freeze.num_trades} → {band.num_trades}. "
        "⚠️ 15m은 봉이 짧아 band 재진입(밴드가 체결)이 「스치듯 닿은 체결」(`baseline` 낙관)에 네 "
        "TF 중 가장 크게 의존하므로(WAN-267 격리 잔존 15m 48.7% vs 2h 80.9%) 위 수치는 상한이다 — "
        "1h·2h·4h보다 못 믿을 값이며, `all` 주 판정에는 15m을 섞지 않았다(부분 TF 조합의 무의미한 "
        "`all`을 막는 병합 규칙)."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복 (wan261 스키마 재사용 — 팔만 3종)
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[CellReturnRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CellReturnRow.model_fields))


def book_to_frame(rows: Sequence[BookScopeRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookScopeRow.model_fields))


def cells_from_csv(path: Path) -> list[CellReturnRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [CellReturnRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def book_from_csv(path: Path) -> list[BookScopeRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [BookScopeRow.model_validate(rec) for rec in records]


def merge_cells(
    existing: Sequence[CellReturnRow], new: Sequence[CellReturnRow]
) -> list[CellReturnRow]:
    new_tfs = {r.timeframe for r in new}
    kept = [r for r in existing if r.timeframe not in new_tfs]
    return [*kept, *new]


def merge_book(existing: Sequence[BookScopeRow], new: Sequence[BookScopeRow]) -> list[BookScopeRow]:
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


def _book_table(book_rows: Sequence[BookScopeRow], scope: str, segment: str) -> list[str]:
    lines = [
        "| 팔 | 거래 | 총수익%† | MDD | 수익/MDD | 최대동시리스크 | 청산 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for arm in ARMS:
        r = _pick_book(book_rows, scope=scope, arm=arm, segment=segment)
        if r is None:
            continue
        gate = "" if r.sample_ok else " ⚠️"
        lines.append(
            f"| 재진입 {arm} | {r.num_trades}{gate} | {_pct(r.total_return)} | "
            f"{_pct(r.max_drawdown)} | {_rr(r.return_over_mdd)} | "
            f"{_pct(r.max_concurrent_risk)} | {r.liquidation_events} | {r.peak_concurrency} |"
        )
    return lines


def _cell_table(cell_rows: Sequence[CellReturnRow], timeframe: str, segment: str) -> list[str]:
    scoped = [r for r in cell_rows if r.timeframe == timeframe and r.segment == segment]
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


def _loo_table(book_rows: Sequence[BookScopeRow], scope: str, segment: str) -> list[str]:
    symbols = sorted({r.exclude_symbol for r in book_rows if r.exclude_symbol})
    lines = [
        "| 팔 | 전체 | " + " | ".join(f"−{s}" for s in symbols) + " |",
        "| -- | --: | " + " | ".join("--:" for _ in symbols) + " |",
    ]
    for arm in ARMS:
        base = _pick_book(book_rows, scope=scope, arm=arm, segment=segment)
        if base is None:
            continue
        cells = []
        for s in symbols:
            r = _pick_book(book_rows, scope=scope, arm=arm, segment=segment, exclude=s)
            cells.append(_pct(r.total_return) if r is not None else "—")
        lines.append(f"| {arm} | {_pct(base.total_return)} | " + " | ".join(cells) + " |")
    return lines


def build_summary_markdown(
    cell_rows: Sequence[CellReturnRow],
    book_rows: Sequence[BookScopeRow],
    *,
    cells_csv: Path,
    book_csv: Path,
) -> str:
    timeframes = sorted({r.timeframe for r in cell_rows}, key=timeframe_to_ms)
    main_scope = _main_scope(book_rows)
    tf_label = ",".join(timeframes)
    lines = [
        "# WAN-269 — 재진입을 채택 북에 켜되 진입가 = band(볼린저 재산정)로 (freeze→band 재실행)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`·채택 "
        "북 cap_only 5배) 돌리며 옛 핀은 하나도 물려받지 않는다. 렌즈 `baseline` 단독(WAN-128) · "
        "못 박은 6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "세 팔: **off**(채택 북 그대로 = 인자 없는 `backtest.run` = wan261 off) · "
        "**freeze**(재진입을 첫 체결가 고정으로 북에 = wan261 on · 검산 기준점) · "
        "**band**(재진입을 봉내 라이브 밴드 재산정으로 북에 = WAN-267 격리 리더 팔을 북에). "
        "재진입 후보는 WAN-228 `reentry_candidates` 로직에 `entry_rule` 인자를 배선해 만든다"
        "(안 넘기면 freeze = wan261/262 CSV 비트 재현).",
        "",
        f"재현: `uv run python -m backtest.wan269_reentry_book_band --tf {tf_label} --jobs 6` "
        "(요약만: `--from-csv`). 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 실행"
        f"(`--tf 15m --append`). 원자료: `{cells_csv}`(칸별 개별 수익률) · `{book_csv}`(북 격자).",
        "",
        "⚠️ **총수익%는 복리 착시** — 레버리지 북의 %는 수백~수천 거래 복리 값이라 실현 수익이 "
        "아니다(WAN-213). 판단은 **MDD · 최대 동시 리스크 · 청산 건수**의 팔 간 차이(band−freeze가 "
        "핵심, band−off는 재진입 없음 대비)다. band 재진입은 밴드가 체결이라 `baseline`(닿으면 "
        "체결) 낙관에 가장 크게 의존하고(큐 우선순위상 가장 안 될 체결), 이 표는 「엣지가 있나」가 "
        "아니라 「band 가격 효과를 북에 얹은 전체 위험」을 잰다.",
        "",
        f"## 1. 북 전체 (완료기준 ②③④⑤) — {main_scope}",
        "",
    ]
    for seg_title, segment in (
        ("oos_warm (주 수치)", SEGMENT_OOS_WARM),
        ("oos (차가운 스트레스)", SEGMENT_OOS),
        ("full (맥락)", SEGMENT_FULL),
        ("is (맥락)", SEGMENT_IS),
    ):
        lines += [f"### {seg_title}", "", *_book_table(book_rows, main_scope, segment), ""]
    if main_scope == "all":
        lines += ["## 2. TF 스코프별 북 (oos_warm 주)", ""]
        for tf in timeframes:
            lines += [f"### {tf}", "", *_book_table(book_rows, tf, SEGMENT_OOS_WARM), ""]
    lines += [
        "## 3. 칸별 개별 수익률 (완료기준 ①) — oos_warm",
        "",
        "각 칸을 격리(단일 포지션)로 돌린 수익률이다 — 북 행(공유 자본)과 자가 다르다. band가 "
        "칸 하나에 미치는 순효과를 본다(Δ수익 = band − freeze).",
        "",
    ]
    for tf in timeframes:
        lines += [f"### {tf}", "", *_cell_table(cell_rows, tf, SEGMENT_OOS_WARM), ""]
    lines += [
        "## 4. leave-one-out — 종목 편중 (oos_warm · 총수익%)",
        "",
        "band 팔의 이득/손해가 특정 종목에 쏠리는지. ⚠️ 총수익%는 복리 착시라 방향만 읽는다.",
        "",
        f"### {main_scope}",
        "",
        *_loo_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
    ]
    if main_scope == "all":
        for tf in timeframes:
            lines += [f"### {tf}", "", *_loo_table(book_rows, tf, SEGMENT_OOS_WARM), ""]
    lines += [
        "## 판정 — band를 북에 얹으면 freeze 대비 전체 위험이 어떻게 되나",
        "",
        verdict(book_rows),
        "",
    ]
    note_15m = _verdict_15m(book_rows)
    if note_15m:
        lines += [note_15m, ""]
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
    parser = argparse.ArgumentParser(description="WAN-269 재진입 북 band 팔 측정")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
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
        print(f"[wan269] CSV 로드 — 칸 {len(cell_rows)}행 · 북 {len(book_rows)}행 (재실행 없음)")
    else:
        new_cells, new_book = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
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
        print(f"[wan269] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan269] 북 {len(book_rows)}행 → {out_book}")

    if not cell_rows or not book_rows:
        print("[wan269] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(cell_rows, book_rows, cells_csv=out_cells, book_csv=out_book),
        encoding="utf-8",
    )
    print(f"[wan269] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
