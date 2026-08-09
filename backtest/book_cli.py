"""채택 레버리지 북을 범용 CLI(`backtest.run`)에서 도는 오케스트레이터 (WAN-213).

WAN-213(2026-07-30 사용자 결정 「전부 다」)이 회계 모델을 「동시 1포지션」에서 **레버리지
북(cap_only 5배)**으로 옮겼다 — 칸 = (종목, TF)마다 1포지션, 여러 칸 동시, 한 지갑 공유.
`ConfluenceParams()`가 채택 전략을 내듯 `LeverageBookParams()`가 채택 북을 낸다.

## 왜 별도 모듈인가 (그리고 왜 wan169/wan180을 재사용하나)

북은 **칸을 가로지르는** 회계라 (종목, TF)마다 한 행을 내는 기존 per-cell 파이프라인
(`run.run_grid_full` → `RunRow`)으로 표현되지 않는다 — 요청된 모든 칸이 **하나의 지갑**을
나눠 쓰고 결과는 구간마다 **집계 행 하나**다. 그래서 북 모드는 per-cell 파이프라인을
건드리지 않고 이 모듈로 분기한다(`--positions single`/숫자는 예전 경로 그대로).

측정 리포트(WAN-169/180)가 이미 칸 후보 생성·구간 매핑·펀딩 대리·북 실행을 구현해
검산까지 붙여 뒀다(`wan180_leverage_book_grid.csv`). 이 모듈은 그 **정확히 같은 함수들**
(`wan169.run_cells`·`_segment_cells`·`wan180.apply_funding_proxy`·`run_leverage_book`)을
호출한다 — 그래서 CLI 북 경로가 wan180 채택 셀과 **구성상 비트 일치**한다(따로 재현
로직을 짜면 갈라진다 — WAN-95/112/123의 조용한 실패를 피하는 유일한 방법이 코드 공유다).

## warm/cold OOS 파리티 (WAN-166 · WAN-213 §3)

wan169가 칸마다 full·is·oos 후보와 따뜻한 경계(`boundary_ms`)를 이미 만든다.
`_segment_cells`가 `oos_warm`을 full 후보에서 칸별 경계로 걸러(straddle (b) — 워밍업
셋업은 배치조차 안 함) 만들므로, 북 경로의 `--oos-warm`은 wan180의 `oos_warm`/`oos`
셀과 같은 방식으로 나온다. 정본 리포트 = `uv run python -m backtest.run --oos-warm`.

## import 사이클 주의

`backtest.run`이 이 모듈을 **지연 import**한다 — 이 모듈이 `wan169`를 쓰고 wan169가
`backtest.run.parse_date_ms`를 쓰기 때문이다(모듈 로드 시점에 서로를 import하면 사이클).
`run.run_book_main`이 함수 안에서 `from backtest import book_cli`를 한다.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.leverage_book import BookOutcome, LeverageBookParams, run_leverage_book
from backtest.models import BacktestResult
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    _segment_cells,
    run_cells,
)
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan228_reentry_census import ReentryEntryRule
from backtest.zone_limit_backtest import build_result_from_trades

#: 채택 재진입 규칙(WAN-273 = 사용자 결정 2026-08-09) — 「익절 후 존 내 재진입」의 재무장
#: 지정가를 봉내 라이브 밴드(볼린저)로 재산정한다. `"freeze"`(첫 체결가 고정)·`"zone"`(존
#: 근단)이 옵트인으로 존치한다. 채택 근거는 WAN-272 CSV(band가 pen_5bp에서 가장 튼튼).
ADOPTED_REENTRY_ENTRY_RULE: ReentryEntryRule = "band"

#: CLI 북 모드가 낼 수 있는 구간 이름 — wan169가 만든 후보 구간과 같다(walkforward 미배선).
SUPPORTED_SEGMENTS: tuple[str, ...] = (
    harness.SEGMENT_FULL,
    harness.SEGMENT_IS,
    harness.SEGMENT_OOS_WARM,
    harness.SEGMENT_OOS,
)


class BookRunRow(BaseModel):
    """북 실행 한 구간의 집계 행 — wan180 `BookRow`의 CLI판(universe/scope/exclude 없음).

    per-cell `RunRow`와 달리 이 행은 **요청된 모든 칸을 가로지른 한 지갑**의 결과다.
    판정 열은 WAN-169 지시대로 위험조정 축(`max_drawdown`·`return_over_mdd`·
    `max_concurrent_risk`·`liquidation_events`)이다 — `total_return`은 수천 거래 복리라
    실현 수익이 아니다(WAN-90).
    """

    model_config = ConfigDict(frozen=True)

    segment: str
    leverage_mode: str
    leverage_multiple: float
    num_cells: int
    num_symbols: int
    start_time: int
    end_time: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    max_open_notional_ratio: float
    liquidation_events: int
    clamped_entries: int
    skipped_cell_busy: int
    skipped_notional: int
    skipped_sizing: int


def _book_row(
    segment: str,
    cells_count: int,
    symbols_count: int,
    start_ms: int,
    end_ms: int,
    outcome: BookOutcome,
    result: BacktestResult,
    book: LeverageBookParams,
) -> BookRunRow:
    m = result.metrics
    stats = outcome.stats
    over_mdd = m.total_return / m.max_drawdown if m.max_drawdown > 0 else None
    return BookRunRow(
        segment=segment,
        leverage_mode=book.leverage_mode,
        leverage_multiple=book.leverage_multiple,
        num_cells=cells_count,
        num_symbols=symbols_count,
        start_time=start_ms,
        end_time=end_ms,
        num_trades=m.num_trades,
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        return_over_mdd=over_mdd,
        peak_concurrency=stats.peak_concurrency,
        max_concurrent_risk=stats.max_concurrent_risk_ratio,
        max_open_notional_ratio=stats.max_open_notional_ratio,
        liquidation_events=len(stats.liquidations),
        clamped_entries=stats.clamped_entries,
        skipped_cell_busy=stats.skipped_cell_busy,
        skipped_notional=stats.skipped_notional,
        skipped_sizing=stats.skipped_sizing,
    )


def build_book_rows(
    payloads: Sequence[CellPayload],
    *,
    book: LeverageBookParams,
    segments: Sequence[str],
    start_ms: int,
    end_ms: int,
    include_reentry: bool = False,
    fee_rate: float | None = None,
    maker_fee_rate: float | None = None,
    slippage: float | None = None,
) -> list[BookRunRow]:
    """이미 만든 칸 후보(payloads)에서 요청 구간별 북 행을 낸다.

    후보 생성(무거운 연산)과 배치 회계(가벼운 연산)를 분리한다 — 검산 테스트가 이 함수에
    직접 payloads를 넘겨 실데이터 로딩 없이 배치 회계만 비트 대조할 수 있게 한다.

    `include_reentry`(WAN-261, 옵트인)를 켜면 각 칸의 재진입 후보(payload에 실려 있을 때만)를
    base 재탭 후보와 합쳐 한 지갑에서 시퀀싱한다. 기본(False)이면 base만 돌아 인자 없는
    `backtest.run`과 비트 단위로 같다(완료기준 2).

    `fee_rate`·`maker_fee_rate`·`slippage`(WAN-264, 옵트인)를 주면 북 실행 cfg의 비용을
    오버라이드한다 — 비용은 후보 집합에 무관하고 시퀀싱(`_to_trade`)에서만 적용되므로
    (BookCell = 「비용 미반영 원가 셋업」) 같은 payloads를 여러 비용으로 재사용할 수 있다.
    전부 None(기본)이면 채택 비용 그대로라 예전과 비트 단위로 같다.
    """
    unknown = [s for s in segments if s not in SUPPORTED_SEGMENTS]
    if unknown:
        raise ValueError(
            f"북 모드가 지원하지 않는 구간입니다: {unknown} "
            f"(지원: {', '.join(SUPPORTED_SEGMENTS)})."
        )
    base_cfg = harness.build_config(
        BOOK_ANNUALIZATION_TF,
        fee_rate=fee_rate,
        maker_fee_rate=maker_fee_rate,
        slippage=slippage,
    )
    num_symbols = len({p.symbol for p in payloads})
    rows: list[BookRunRow] = []
    for segment in segments:
        cells = _segment_cells(payloads, segment, "", include_reentry=include_reentry)
        outcome = run_leverage_book(cells, base_cfg, book)
        result = build_result_from_trades(
            outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
        )
        rows.append(
            _book_row(segment, len(cells), num_symbols, start_ms, end_ms, outcome, result, book)
        )
    return rows


def run_book(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    book: LeverageBookParams,
    segments: Sequence[str],
    funding_proxy: bool = True,
    jobs: int = 1,
    log: bool = True,
    reentry: bool = True,
    reentry_entry_rule: ReentryEntryRule = ADOPTED_REENTRY_ENTRY_RULE,
) -> list[BookRunRow]:
    """채택 북을 실데이터에서 돌려 구간별 집계 행을 낸다.

    `wan169.run_cells`(칸별 후보·따뜻한 경계) → `apply_funding_proxy`(신규 종목 BTC 대리) →
    `_segment_cells`(구간별 칸) → `run_leverage_book`(공유 자본 배치). 전부 측정 리포트가
    쓰는 함수 그대로라 wan180 셀과 구성상 비트 일치한다.

    ⚠️ **채택 기본값은 재진입 켬(band)이다(WAN-273 = 사용자 결정 2026-08-09)** — `reentry`
    기본이 `True`, `reentry_entry_rule` 기본이 `"band"`라 인자 없는 `run_book()`이 채택 북을
    낸다(`LeverageBookParams()`가 채택 북을 내는 것과 대칭). 「익절 후 존 내 재진입」 후보를
    만들어(`run_cells`) base 재탭 후보와 함께 한 지갑에서 시퀀싱한다
    (`build_book_rows(include_reentry=True)`).

    `reentry=False`는 **WAN-273 이전의 재진입-off 북**이다(옛 CSV 비트 재현) — 라벨이 아니라
    후보 집합으로 갈린다(회귀 테스트가 동작으로 고정). `reentry_entry_rule`은 `reentry=True`일
    때만 의미가 있고 `"freeze"`(첫 체결가 고정)·`"zone"`(존 근단)이 옵트인으로 존치한다.
    """
    from backtest.run import parse_date_ms  # 지연 import(사이클 회피)

    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        reentry=reentry,
        reentry_entry_rule=reentry_entry_rule,
    )
    if funding_proxy:
        payloads, note = apply_funding_proxy(payloads)
        if note and log:
            print(f"[book] 펀딩 대리: {note}", flush=True)
    return build_book_rows(
        payloads,
        book=book,
        segments=segments,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        include_reentry=reentry,
    )


# --------------------------------------------------------------------------- #
# 렌더링 (per-cell `RunRow` 파이프라인과 분리 — 열이 다르다)
# --------------------------------------------------------------------------- #

_PERCENT_FIELDS: tuple[str, ...] = (
    "win_rate",
    "total_return",
    "max_drawdown",
    "max_concurrent_risk",
)


def rows_to_frame(rows: Sequence[BookRunRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def render_book(rows: Sequence[BookRunRow], fmt: str) -> str:
    """`table`/`csv`/`json` — 이름은 per-cell `render`와 같지만 열이 북 열이다."""
    if fmt == "csv":
        buffer = io.StringIO()
        rows_to_frame(rows).to_csv(buffer, index=False)
        return buffer.getvalue().rstrip("\n")
    if fmt == "json":
        return str(rows_to_frame(rows).to_json(orient="records"))
    return _render_table(rows)


def _render_table(rows: Sequence[BookRunRow]) -> str:
    if not rows:
        return "(행 없음)"
    lines = [
        "구간       배수  거래   승률    수익률       MDD      수익/MDD  최대동시리스크  청산  칸",
    ]
    for r in rows:
        over = f"{r.return_over_mdd:8.2f}" if r.return_over_mdd is not None else "     n/a"
        lines.append(
            f"{r.segment:<9} {r.leverage_multiple:>4.1f} {r.num_trades:>5} "
            f"{r.win_rate * 100:>5.1f}% {r.total_return * 100:>10.2f}% "
            f"{r.max_drawdown * 100:>6.2f}% {over} {r.max_concurrent_risk * 100:>10.2f}%  "
            f"{r.liquidation_events:>3}  {r.num_cells:>2}"
        )
    return "\n".join(lines)
