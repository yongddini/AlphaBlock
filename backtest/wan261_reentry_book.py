"""WAN-261 — 익절 후 존 내 재진입을 채택 북에 켜고 통째로 (격리 아닌 전체 손익·MDD·위험).

## 무엇을 묻나 (사용자 결정 2026-08-07)

WAN-228/229(census)·WAN-231/242/243(매칭 널)이 「익절 후 존 내 재진입」을 **격리**해서만
쟀다 — 한 번에 한 포지션씩, 재진입 거래만 따로 떼어낸 「격리 순수익」. 사용자 지적:
*"그 전체를 해봐야지."* 실제 채택 회계는 칸=(종목,TF)마다 1포지션·한 지갑 공유하는
**레버리지 북(cap_only 5배, WAN-213)** 인데, 거기에 재진입을 **켜고 통째로** 돌린 값이 없다.
격리 값과 북 전체 값은 다를 수 있다(동시 포지션·공유 자본·상한 제약).

이 이슈는 **「그 가격 효과를 채택 북에 실제로 얹으면 전체 손익·위험이 어떻게 되나」** 하나만
답한다 — **「엣지가 있나」(실력 대 가격 효과)는 묻지 않는다**(사용자가 가격 효과를 노리는
것을 알고 선택 · (b) 무작위 가격 널은 이 이슈 범위 밖).

## 방법 — 재진입 후보를 채택 북에 옵트인 주입

`wan169.run_cells(reentry=True)`가 칸마다 base 재탭 후보와 함께 「익절 후 재무장」 재진입
후보(WAN-228 `reentry_candidates` 로직 공유)를 만든다. 두 팔:

* **off**: 채택 북(base 재탭 후보만) = 인자 없는 `backtest.run`과 비트 단위로 같다.
* **on**: 채택 북에 재진입 후보를 base와 **함께** 한 지갑에서 시퀀싱
  (`_segment_cells(include_reentry=True)` → `run_leverage_book`). 칸당 1포지션·공유 자본·
  명목 상한이 재탭과 재진입에 함께 적용된다.

## 무엇을 내나 (완료기준 1)

* **① 칸별 개별 수익률** — 각 (종목, TF) 칸을 격리(단일 포지션)로 돌린 수익률(off/on).
* **② 북 전체 수익률** — 요청 칸 전체를 한 지갑으로 묶은 집계(off/on), 스코프별(각 TF +
  전체 `all`).
* **③ MDD · ④ 최대 동시 리스크 · ⑤ 청산 건수** — 북 행에 병기(off/on의 차이로 읽는다).
* **leave-one-out** — 종목 하나씩 빼 재진입 on의 이득/손해가 특정 종목에 쏠리는지.

## 성격 · 경고 (그대로 옮길 것)

측정 전용. 렌즈 `baseline` 단독 · 핀 없음(오늘 엔진) · 채택 좌표(9종목 × 못 박은 6년) ·
기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지). ⚠️ 레버리지 북 **총수익%는 복리
착시**라 실현 수익이 아니다(WAN-213) — 판단은 **MDD·최대 동시 리스크·청산**으로 한다.
전부 `baseline`(닿으면 체결) 낙관 위 값이고 **재진입이 이 가정에 가장 크게 의존**한다
(스치듯 닿은 체결 · 봉이 짧은 15m이 가장 못 믿을 숫자, WAN-231 경고). 「엣지 없음」
(WAN-84/88/111/114/124/151/201)과 **다른 질문**이라 뒤집는 게 아니다. 격리 값
(WAN-231/242/243)과 **셀 직접 비교 금지**(격리는 1포지션·재진입만, 이 표는 북 공유 자본).

## 재현

```
uv run python -m backtest.wan261_reentry_book --tf 1h,2h,4h --jobs 6
uv run python -m backtest.wan261_reentry_book --tf 15m --append --jobs 9   # 무거움(셀당 ~37분)
uv run python -m backtest.wan261_reentry_book --from-csv                   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
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
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan261_reentry_book_cells.csv"
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan261_reentry_book_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan261_reentry_book_summary.md"

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

#: 두 팔 — 재진입 off(채택 북 그대로) / on(재진입 얹은 북).
ARMS: tuple[str, ...] = ("off", "on")

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 스코프·구간은 판정에서 뺀다.
MIN_TRADES = 20


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CellReturnRow(BaseModel):
    """한 (종목, TF) 칸 × 구간 × 팔의 **격리(단일 포지션) 개별 수익률** (완료기준 ①).

    북 행(전체 공유 자본)과 자가 다르다 — 이 행은 「이 칸이 혼자였다면」의 수익률이라
    재진입이 그 칸 하나에 미치는 순효과를 본다(북에서는 공유 자본·상한이 섞인다).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    num_candidates: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float


class BookScopeRow(BaseModel):
    """한 스코프(각 TF + 전체 `all`) × 구간 × 팔 × 제외종목의 북 집계 (완료기준 ②③④⑤).

    `book_cli.BookRunRow`를 감싸 스코프·팔·제외종목 축을 더한다 — 판정 열은 위험조정
    (`max_drawdown`·`max_concurrent_risk`·`liquidation_events`)이다(총수익%는 복리 착시).
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    """`all`(요청 칸 전체 한 지갑) · 개별 TF(그 TF의 칸만 한 지갑)."""
    arm: str
    segment: str
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
        """CSV 왕복의 빈 칸(`""`/NaN)을 `None`으로 — MDD 0인 행의 `return_over_mdd`가 NaN으로
        되살아나 판정을 오염시키지 않게(WAN-130/169 함정과 같은 가드)."""
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


# --------------------------------------------------------------------------- #
# 계산 (순수 — payloads → 행)
# --------------------------------------------------------------------------- #


def _cell_return_rows(payloads: Sequence[CellPayload]) -> list[CellReturnRow]:
    """칸별 개별(격리 단일 포지션) 수익률 — off(base)·on(base+재진입) 두 팔.

    후보 선택은 북과 **같은** `_segment_cells`를 쓴다(한 칸만 넘겨) — 팔이 실제로 북에서
    보는 것과 같은 후보 집합의 격리 성과라, 북과 격리를 나란히 읽을 수 있다.
    """
    rows: list[CellReturnRow] = []
    for payload in payloads:
        cfg = harness.build_config(payload.timeframe)
        for segment in SEGMENTS:
            for arm in ARMS:
                cell = _segment_cells([payload], segment, "", include_reentry=arm == "on")[0]
                num_trades, win_rate, total_return, mdd = _isolated_metrics(
                    cell.candidates, cfg, payload.timeframe, cell.funding_rates
                )
                rows.append(
                    CellReturnRow(
                        symbol=payload.symbol,
                        timeframe=payload.timeframe,
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
    payloads: Sequence[CellPayload], *, start_ms: int, end_ms: int
) -> list[BookScopeRow]:
    """스코프(각 TF + 전체) × 구간 × 팔 × leave-one-out의 북 집계.

    `book_cli.build_book_rows`(채택 북)를 그대로 호출한다 — CLI `backtest.run --reentry on/off`가
    타는 것과 **같은 배선**이라 이 표가 그 CLI 결과의 분해다.
    """
    timeframes = sorted({p.timeframe for p in payloads}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    symbols = sorted({_short(p.symbol) for p in payloads})
    rows: list[BookScopeRow] = []
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
    """9종목 × TF 칸을 재진입 켜서 한 번 돌고(base+재진입), 두 팔의 칸·북 행을 낸다.

    `run_cells(reentry=True)` 한 번이면 base 후보(off 팔)와 재진입 후보(on 팔)를 모두 담으므로
    팔마다 다시 돌 필요가 없다 — off는 base만, on은 base+재진입을 `_segment_cells`가 고른다.
    """
    # WAN-305 명시 핀: wan261/262 CSV는 freeze 규칙(WAN-269 이전) 재진입의 동결 스냅샷이다.
    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        reentry=True,
        reentry_entry_rule="freeze",
    )
    if funding_proxy:
        payloads, note = apply_funding_proxy(payloads)
        if note and log:
            print(f"[wan261] 펀딩 대리: {note}", flush=True)
    cell_rows = _cell_return_rows(payloads)
    book_rows = _book_scope_rows(payloads, start_ms=parse_date_ms(start), end_ms=parse_date_ms(end))
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


def verdict(book_rows: Sequence[BookScopeRow]) -> str:
    """재진입 on이 북 전체의 위험을 어떻게 바꾸나 — MDD·최대 동시 리스크·청산으로 읽는다.

    총수익%는 복리 착시라 판정에 쓰지 않는다(WAN-213). 주 수치 = `all` 스코프 · oos_warm.
    숫자는 전부 행에서 계산한다(문장에 박으면 재실행 뒤 거짓말한다 — WAN-164 패턴).
    """
    main_scope = "all" if any(r.scope == "all" for r in book_rows) else None
    if main_scope is None:
        scopes = sorted({r.scope for r in book_rows}, key=timeframe_to_ms)
        main_scope = scopes[0] if scopes else ""
    off = _pick_book(book_rows, scope=main_scope, arm="off", segment=SEGMENT_OOS_WARM)
    on = _pick_book(book_rows, scope=main_scope, arm="on", segment=SEGMENT_OOS_WARM)
    if off is None or on is None:
        return "**판정 불가** — 주 스코프의 oos_warm 행이 없습니다."
    d_mdd = (on.max_drawdown - off.max_drawdown) * 100
    d_risk = (on.max_concurrent_risk - off.max_concurrent_risk) * 100
    d_liq = on.liquidation_events - off.liquidation_events
    coords = (
        f"{main_scope}·oos_warm: MDD {off.max_drawdown * 100:.2f}% → {on.max_drawdown * 100:.2f}% "
        f"(Δ{d_mdd:+.2f}%p) · 최대 동시 리스크 {off.max_concurrent_risk * 100:.2f}% → "
        f"{on.max_concurrent_risk * 100:.2f}% (Δ{d_risk:+.2f}%p) · 청산 {off.liquidation_events} → "
        f"{on.liquidation_events} (Δ{d_liq:+d}) · 거래 {off.num_trades} → {on.num_trades}"
    )
    if d_liq > 0 or d_mdd > 1.0 or d_risk > 1.0:
        head = "**재진입 on이 북 위험을 키운다.**"
    elif d_liq == 0 and abs(d_mdd) <= 1.0 and abs(d_risk) <= 1.0:
        head = "**재진입 on의 위험 변화는 작다(MDD·동시 리스크 ≤ 1%p · 청산 불변).**"
    else:
        head = "**재진입 on이 북 위험을 소폭 줄인다.**"
    return (
        f"{head} {coords}. ⚠️ 총수익% 변화는 복리 착시라 판정에 넣지 않는다(WAN-213) — 판단은 "
        "위 낙폭·위험이다. 전부 `baseline`(닿으면 체결) 낙관 위 값이고 재진입이 그 가정에 가장 "
        "크게 의존한다(스치듯 닿은 체결). 이 표는 「엣지가 있나」를 묻지 않는다 — 가격 효과를 "
        "북에 얹은 전체 손익·위험의 측정이다(채택은 재-베이스라인 = 사용자 결정)."
    )


def _verdict_15m(book_rows: Sequence[BookScopeRow]) -> str:
    """15m 스코프가 있으면 그 위험 델타 + 낙관 가정 최대 의존 경고를 낸다(완료기준 — 15m 명시).

    주 판정(`verdict`)은 `all`(1h·2h·4h) 스코프라 15m이 빠진다(부분 TF 조합의 무의미한 `all`을
    막는 병합 규칙 때문에 15m 단독 append는 `all`을 재계산하지 않는다). 15m은 봉이 짧아
    「스치듯 닿은 체결」(`baseline` 낙관)에 네 TF 중 **가장 크게 의존**하므로(WAN-231/203) 별도로
    명시한다. 숫자는 행에서 계산한다(문장에 박으면 재실행 뒤 거짓말한다 — WAN-164 패턴).
    15m 스코프가 없으면(1h·2h·4h만 있는 실행) 빈 문자열이라 렌더러가 문단을 건너뛴다.
    """
    off = _pick_book(book_rows, scope="15m", arm="off", segment=SEGMENT_OOS_WARM)
    on = _pick_book(book_rows, scope="15m", arm="on", segment=SEGMENT_OOS_WARM)
    if off is None or on is None:
        return ""
    d_mdd = (on.max_drawdown - off.max_drawdown) * 100
    d_risk = (on.max_concurrent_risk - off.max_concurrent_risk) * 100
    d_liq = on.liquidation_events - off.liquidation_events
    return (
        f"📌 **15m — 낙관 가정 최대 의존 TF.** 15m·oos_warm: MDD "
        f"{off.max_drawdown * 100:.2f}% → {on.max_drawdown * 100:.2f}% (Δ{d_mdd:+.2f}%p) · "
        f"최대 동시 리스크 {off.max_concurrent_risk * 100:.2f}% → "
        f"{on.max_concurrent_risk * 100:.2f}% (Δ{d_risk:+.2f}%p) · 청산 {off.liquidation_events} → "
        f"{on.liquidation_events} (Δ{d_liq:+d}) · 거래 {off.num_trades} → {on.num_trades}. "
        "⚠️ 15m은 봉이 짧아 「스치듯 닿은 체결」(`baseline` 「닿으면 체결」 낙관)에 네 TF 중 가장 "
        "크게 의존하므로(WAN-231/203) 위 수치는 상한이다 — 1h·2h·4h보다 못 믿을 값이며, `all` 주 "
        "판정에는 15m을 섞지 않았다(부분 TF 조합의 무의미한 `all`을 막는 병합 규칙)."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
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
    scoped = sorted(
        (r for r in cell_rows if r.timeframe == timeframe and r.segment == segment),
        key=lambda r: r.symbol,
    )
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
    main_scope = (
        "all" if any(r.scope == "all" for r in book_rows) else (timeframes[0] if timeframes else "")
    )
    tf_label = ",".join(timeframes)
    lines = [
        "# WAN-261 — 익절 후 존 내 재진입을 채택 북에 켜고 통째로 (전체 손익·MDD·위험)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`·채택 "
        "북 cap_only 5배) 돌리며 옛 핀은 하나도 물려받지 않는다. 렌즈 `baseline` 단독(WAN-128) · "
        "못 박은 6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "두 팔: **재진입 off**(채택 북 그대로 = 인자 없는 `backtest.run`) vs **재진입 on**(익절 후 "
        "재무장 재진입 후보를 base 재탭 후보와 함께 한 지갑에서 시퀀싱). 재진입 후보는 WAN-228 "
        "`reentry_candidates` 로직을 그대로 재사용한다(격리 census/널과 같은 재무장 규칙).",
        "",
        f"재현: `uv run python -m backtest.wan261_reentry_book --tf {tf_label} --jobs 6` "
        "(요약만: `--from-csv`). 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 실행"
        f"(`--tf 15m --append`). 원자료: `{cells_csv}`(칸별 개별 수익률) · `{book_csv}`(북 격자).",
        "",
        "⚠️ **총수익%는 복리 착시** — 레버리지 북의 %는 수백~수천 거래 복리 값이라 실현 수익이 "
        "아니다(WAN-213). 판단은 **MDD · 최대 동시 리스크 · 청산 건수**의 off→on 차이다. "
        "재진입은 전부 `baseline`(닿으면 체결) 낙관 위 값에 가장 크게 의존하고(스치듯 닿은 체결), "
        "이 표는 「엣지가 있나」가 아니라 「가격 효과를 북에 얹은 전체 위험」을 잰다.",
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
        "각 칸을 격리(단일 포지션)로 돌린 수익률이다 — 북 행(공유 자본)과 자가 다르다. 재진입이 "
        "칸 하나에 미치는 순효과를 본다(Δ수익 = on − off).",
        "",
    ]
    for tf in timeframes:
        lines += [f"### {tf}", "", *_cell_table(cell_rows, tf, SEGMENT_OOS_WARM), ""]
    lines += [
        "## 4. leave-one-out — 종목 편중 (oos_warm · 총수익%)",
        "",
        "재진입 on의 이득/손해가 특정 종목에 쏠리는지. ⚠️ 총수익%는 복리 착시라 방향만 읽는다.",
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
        "## 판정 — 재진입을 북에 얹으면 전체 위험이 어떻게 되나",
        "",
        verdict(book_rows),
        "",
    ]
    note_15m = _verdict_15m(book_rows)
    if note_15m:
        lines += [note_15m, ""]
    lines += [
        "⚠️ **이 표는 채택 근거가 아니라 측정이다.** 격리 값(WAN-231/242/243)과 셀 직접 비교 "
        "금지(격리는 1포지션·재진입만, 이 표는 북 공유 자본). 「엣지 없음」"
        "(WAN-84/88/111/114/124/151/201)은 탭 기준 진입 판정이라 이 축과 별개다. 채택(재진입 "
        "기본값화)은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지(큐 우선순위 WAN-98 "
        "Canceled · 라이브 충실도 WAN-45 선행). **기본값·토대 불변**(측정 전용 · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-261 재진입 북 측정")
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
        print(f"[wan261] CSV 로드 — 칸 {len(cell_rows)}행 · 북 {len(book_rows)}행 (재실행 없음)")
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
        print(f"[wan261] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan261] 북 {len(book_rows)}행 → {out_book}")

    if not cell_rows or not book_rows:
        print("[wan261] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(cell_rows, book_rows, cells_csv=out_cells, book_csv=out_book),
        encoding="utf-8",
    )
    print(f"[wan261] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
