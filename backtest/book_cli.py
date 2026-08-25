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
from dataclasses import dataclass

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.leverage_book import (
    BookOutcome,
    LeverageBookParams,
    PlacedSetup,
    run_leverage_book,
)
from backtest.models import BacktestResult, Trade
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    _segment_cells,
    run_cells,
)
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan228_reentry_census import ReentryEntryRule
from backtest.zone_limit_backtest import build_result_from_trades
from common.costs import Liquidity
from common.timefmt import format_kst
from strategy.models import InvalidationCancel

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
    max_effective_concurrent_risk: float
    """**실효** 동시 리스크(WAN-312 신설 열) = 계획 동시 리스크 × `stress_risk_multiple`.

    ⚠️ 채택 회계(`stress_risk_multiple=1.0`)에서는 `max_concurrent_risk`와 **정의상 같다** —
    이 열이 뜻을 갖는 것은 손절이 계획 1R보다 크게 밀리는 스트레스(k>1)를 얹었을 때다
    (WAN-312/316). 값이 같다고 열이 비어 있는 게 아니라 「계획 = 실효」가 그 축의 답이다."""
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
        max_effective_concurrent_risk=stats.max_effective_concurrent_risk_ratio,
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
    include_reentry: bool = True,
    fee_rate: float | None = None,
    maker_fee_rate: float | None = None,
    slippage: float | None = None,
    stress_risk_multiple: float = 1.0,
    compound_sizing: bool = True,
    min_stop_distance_fraction: float | None = None,
    take_profit_liquidity: Liquidity = harness.LEGACY_TAKE_PROFIT_LIQUIDITY,
) -> list[BookRunRow]:
    """이미 만든 칸 후보(payloads)에서 요청 구간별 북 행을 낸다.

    후보 생성(무거운 연산)과 배치 회계(가벼운 연산)를 분리한다 — 검산 테스트가 이 함수에
    직접 payloads를 넘겨 실데이터 로딩 없이 배치 회계만 비트 대조할 수 있게 한다.

    ⚠️ **`include_reentry` 기본값은 켬이다(WAN-305)** — 채택 북(WAN-273 재진입 = 인자 없는
    `backtest.run`)이 「아무것도 안 하면」 나오게 한다. 켜면 각 칸의 재진입 후보(payload에
    실려 있을 때만)를 base 재탭 후보와 합쳐 한 지갑에서 시퀀싱한다. payload에 재진입이
    없으면 켜져 있어도 base만 남는다. `False`는 옛 CSV 재현용 **명시 핀**이다(WAN-305).

    `fee_rate`·`maker_fee_rate`·`slippage`(WAN-264, 옵트인)를 주면 북 실행 cfg의 비용을
    오버라이드한다 — 비용은 후보 집합에 무관하고 시퀀싱(`_to_trade`)에서만 적용되므로
    (BookCell = 「비용 미반영 원가 셋업」) 같은 payloads를 여러 비용으로 재사용할 수 있다.
    전부 None(기본)이면 채택 비용 그대로라 예전과 비트 단위로 같다.

    `stress_risk_multiple`(WAN-312, 옵트인)은 한 포지션의 최악 손실을 계획 1R의 몇 배로 볼지다
    — 청산 검사와 `max_effective_concurrent_risk`에만 흘러 들고 거래 자체는 안 바꾼다.
    `1.0`(기본)이면 예전과 비트 단위로 같다.

    `compound_sizing=False`(WAN-346 §2, 옵트인)는 베팅 크기를 **초기 자본에 못 박아** 복리
    착시 없이 읽는 팔을 낸다 — `True`(기본)면 예전과 비트 단위로 같다.

    🚨 **`take_profit_liquidity` 기본값은 옛 값(`taker`)이다(WAN-370)** — `include_reentry`와
    **반대 방향의 기본값**이고, 그 이유는 `wan169.run_cells`의 `adv_fraction`과 같다: 이 함수를
    쓰는 북 측정 모듈이 20개 가까이 되는데 그 CSV가 전부 옛 비용 회계 위의 기록이라, 한 곳의
    기본값으로 그것들을 통째로 보존한다. 채택 북(`run_book`)과 재산출 대상(wan366·wan370)만
    `harness.ADOPTED_TAKE_PROFIT_LIQUIDITY`를 **명시로** 넘긴다. ⚠️ 새 측정 모듈은 그 명시를
    잊으면 옛 회계로 돈다 — 새 모듈은 반드시 채택 값을 넘길 것(WAN-305). `run_cells`에 넘긴
    값과 **같아야** 한 표가 한 회계다.

    `min_stop_distance_fraction`(WAN-366, 옵트인)은 손절폭 가드를 이 배치에서만 갈아끼운다 —
    `None`(기본)이면 채택 0.3%라 예전과 비트 단위로 같다. 가드는 **사이징**에 걸려 후보를 안
    바꾸므로(WAN-197) 같은 payload를 가드만 바꿔 다시 배치할 수 있다.
    """
    return [
        seg.row
        for seg in iter_book_segments(
            payloads,
            book=book,
            segments=segments,
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=include_reentry,
            fee_rate=fee_rate,
            maker_fee_rate=maker_fee_rate,
            slippage=slippage,
            stress_risk_multiple=stress_risk_multiple,
            compound_sizing=compound_sizing,
            min_stop_distance_fraction=min_stop_distance_fraction,
            take_profit_liquidity=take_profit_liquidity,
        )
    ]


@dataclass(frozen=True)
class BookSegment:
    """한 구간의 북 실행 결과 전체 — 집계 행 + 그 행을 만든 원자료 (WAN-336).

    `build_book_rows`는 `row`만 돌려주므로 「이 지갑이 실제로 한 거래 하나하나」를 볼 수 없다.
    거래 단위 귀속(예: 「같은 분 익절이 순손익의 몇 %인가」)을 재는 리포트는 `outcome`이
    필요하다 — 그 리포트가 자기 배치 루프를 따로 짜면 두 경로가 갈라지므로(WAN-95/112/123의
    조용한 실패) 같은 함수에서 한 번에 낸다.
    """

    segment: str
    row: BookRunRow
    outcome: BookOutcome
    result: BacktestResult

    def trades_with_placements(self) -> list[tuple[Trade, PlacedSetup]]:
        """거래를 그 거래의 배치 스냅샷(칸·진입 시점 자본·리스크 금액)과 짝지어 돌려준다.

        북은 한 지갑이라 `Trade`에 심볼·TF가 없다 — 칸은 `BookStats.placed_records`에만 있고,
        시퀀서가 두 리스트에 **같은 순서로** append하므로 위치가 곧 짝이다. 그 계약이 조용히
        깨지면 귀속이 통째로 어긋나므로(라벨은 멀쩡한 채) 여기서 **길이와 손익 둘 다** 대조해
        어긋나면 시끄럽게 죽는다. 회귀 테스트가 이 계약을 동작으로 고정한다.

        `PlacedSetup.risk_amount`가 함께 나오는 것이 요점이다 — 복리 지갑에서 USD 손익을 그냥
        더하면 **뒤쪽 거래가 표를 지배**하므로(WAN-169/213 복리 착시), 크기를 정규화한 자
        (net R = 실현손익 ÷ 그 거래의 리스크 금액)로도 같은 질문을 물을 수 있어야 한다.
        """
        placed = self.outcome.stats.placed_records
        trades = self.outcome.trades
        if len(placed) != len(trades):
            raise AssertionError(
                f"북 배치 기록과 거래 목록의 길이가 다릅니다({len(placed)} != {len(trades)}) — "
                "시퀀서가 두 리스트를 짝으로 append한다는 계약이 깨졌습니다(WAN-336 귀속 전제)."
            )
        for index, (trade, record) in enumerate(zip(trades, placed, strict=True)):
            if record.realized_pnl != trade.realized_pnl:
                raise AssertionError(
                    f"북 배치 기록 {index}번의 손익이 같은 자리 거래와 다릅니다 — 두 리스트가 "
                    "같은 순서라는 계약이 깨졌습니다(WAN-336 귀속 전제)."
                )
        return list(zip(trades, placed, strict=True))


def iter_book_segments(
    payloads: Sequence[CellPayload],
    *,
    book: LeverageBookParams,
    segments: Sequence[str],
    start_ms: int,
    end_ms: int,
    include_reentry: bool = True,
    fee_rate: float | None = None,
    maker_fee_rate: float | None = None,
    slippage: float | None = None,
    stress_risk_multiple: float = 1.0,
    compound_sizing: bool = True,
    min_stop_distance_fraction: float | None = None,
    take_profit_liquidity: Liquidity = harness.LEGACY_TAKE_PROFIT_LIQUIDITY,
) -> list[BookSegment]:
    """`build_book_rows`의 속 — 집계 행뿐 아니라 그 행을 만든 `BookOutcome`까지 돌려준다.

    행만 필요하면 `build_book_rows`를 쓴다(그쪽이 이 함수의 얇은 래퍼라 **같은 숫자**다).
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
        take_profit_liquidity=take_profit_liquidity,
        min_stop_distance_fraction=min_stop_distance_fraction,
    )
    num_symbols = len({p.symbol for p in payloads})
    out: list[BookSegment] = []
    for segment in segments:
        cells = _segment_cells(payloads, segment, "", include_reentry=include_reentry)
        outcome = run_leverage_book(
            cells,
            base_cfg,
            book,
            stress_risk_multiple=stress_risk_multiple,
            compound_sizing=compound_sizing,
        )
        result = build_result_from_trades(
            outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
        )
        row = _book_row(segment, len(cells), num_symbols, start_ms, end_ms, outcome, result, book)
        out.append(BookSegment(segment=segment, row=row, outcome=outcome, result=result))
    return out


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
    adv_fraction: harness.AdvCapArg = harness.UNSET,
    invalidation_cancel: InvalidationCancel | None = None,
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

    ⚠️ **채택 기본값은 유동성 한도 켬(0.005)이다(WAN-279 = 사용자 결정 2026-08-10)** —
    `adv_fraction` 기본이 `UNSET`이라 `run_cells`가 채택 기본값(`PositionSizingParams`의 0.005)을
    물려받아 후보에 룩어헤드-안전 `adv_usd`를 싣고, 북 시퀀싱이 명목을 `0.005 × ADV_usd`로
    자른다(자본에 안 비례하는 절대 상한이라 복리 착시를 깬다, WAN-90/213). `adv_fraction=None`은
    **WAN-279 이전의 상한-끔 북**이다(옛 CSV 비트 재현) — 미지정(`UNSET`)과 다르다(WAN-159 규약).

    ⚠️ **채택 기본값은 익절 지정가(메이커 2bp)다(WAN-370)** — 이 함수가 후보 생성·배치 양쪽에
    `harness.ADOPTED_TAKE_PROFIT_LIQUIDITY`를 **명시로** 넘긴다(측정용 기본값은 옛 `taker`라
    명시가 없으면 옛 회계로 돈다). 인자로 열지 않은 것도 의도다 — 여는 순간 "채택 북" 이름을
    달고 옛 비용으로 도는 호출이 생긴다.

    ⚠️ **채택 기본값은 인과 취소(`"bar_close"`)다(WAN-365)** — `invalidation_cancel` 기본이
    `None`이라 `run_cells`가 채택 기본값(`ConfluenceParams().invalidation_cancel`)을 물려받는다.
    `"bar_open"`은 **WAN-365 이전의 소급 취소 북**이다(옛 CSV 비트 재현) — 미지정과 다르다.
    """
    return [
        seg.row
        for seg in run_book_segments(
            symbols,
            timeframes,
            start=start,
            end=end,
            book=book,
            segments=segments,
            funding_proxy=funding_proxy,
            jobs=jobs,
            log=log,
            reentry=reentry,
            reentry_entry_rule=reentry_entry_rule,
            adv_fraction=adv_fraction,
            invalidation_cancel=invalidation_cancel,
        )
    ]


def run_book_segments(
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
    adv_fraction: harness.AdvCapArg = harness.UNSET,
    invalidation_cancel: InvalidationCancel | None = None,
) -> list[BookSegment]:
    """`run_book`의 속 — 집계 행뿐 아니라 그 행을 만든 거래·배치 기록까지 돌려준다.

    행만 필요하면 `run_book`을 쓴다(그쪽이 이 함수의 얇은 래퍼라 **같은 숫자**다). 거래별
    CSV(WAN-346 §0)처럼 「이 지갑이 실제로 한 거래 하나하나」가 필요한 호출부가 자기 배치
    루프를 따로 짜면 두 경로가 갈라지므로(WAN-95/112/123의 조용한 실패) 여기서 한 번에 낸다.
    """
    from backtest.run import parse_date_ms  # 지연 import(사이클 회피)

    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        adv_fraction=adv_fraction,
        # WAN-370: 채택 북은 익절을 지정가(메이커 2bp)로 값매김한다 — 측정 모듈용 기본값
        # (옛 taker)을 여기서 **명시로** 덮어쓰는 것이 「채택 북 = 인자 없는 실행」의 일부다.
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        reentry=reentry,
        reentry_entry_rule=reentry_entry_rule,
        invalidation_cancel=invalidation_cancel,
    )
    if funding_proxy:
        payloads, note = apply_funding_proxy(payloads)
        if note and log:
            print(f"[book] 펀딩 대리: {note}", flush=True)
    return iter_book_segments(
        payloads,
        book=book,
        segments=segments,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        include_reentry=reentry,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
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


# --------------------------------------------------------------------------- #
# 거래별 내역 · 시드곡선 (WAN-346 §0)
# --------------------------------------------------------------------------- #

#: 북 거래별 CSV가 per-cell 표(`report.trades_to_display_frame`) 위에 **덧붙이는** 열.
#: 북은 칸을 가로지르는 한 지갑이라 per-cell 표에 없는 정보가 셋 있다 — 어느 칸인가,
#: 그 거래가 건 리스크가 얼마인가(net R의 분모), 그리고 채택 규칙의 라벨(재진입·같은 분 익절).
COL_CELL_SYMBOL = "칸(종목)"
COL_CELL_TF = "칸(TF)"
COL_STOP_PRICE = "손절가"
COL_TAKE_PROFIT_PRICE = "익절가"
COL_RISK_AMOUNT = "리스크금액"
COL_NET_R = "net R"
COL_IS_REENTRY = "재진입"
COL_SAME_STEP_TP = "같은분익절"
COL_TRIGGER_KST = "탭시각(KST)"

BOOK_TRADE_COLUMNS: tuple[str, ...] = (
    COL_CELL_SYMBOL,
    COL_CELL_TF,
    COL_STOP_PRICE,
    COL_TAKE_PROFIT_PRICE,
    COL_RISK_AMOUNT,
    COL_NET_R,
    COL_IS_REENTRY,
    COL_SAME_STEP_TP,
    COL_TRIGGER_KST,
)


def _ordered_pairs(segment: BookSegment) -> list[tuple[Trade, PlacedSetup]]:
    """거래·배치 짝을 **청산 시각 순**으로 — `build_result_from_trades`와 같은 순열.

    `BookSegment.result.trades`는 `sorted(outcome.trades, key=exit_time)`이고 이쪽도 같은
    원본 리스트를 같은 키로 정렬하므로(파이썬 정렬은 안정) **두 순열은 정의상 같다**.
    객체 동일성(`id()`)에 기대지 않는 이유는 그것이 pydantic의 재검증 정책에 달려 있어
    조용히 깨질 수 있기 때문이다 — 대신 아래에서 값으로 대조한다.
    """
    return sorted(segment.trades_with_placements(), key=lambda pair: pair[0].exit_time)


def book_trades_to_display_frame(segment: BookSegment) -> pd.DataFrame:
    """북 한 구간의 **거래별 내역** 표 (WAN-346 §0 · KST/UTC 병기).

    표시 열은 `report.trades_to_display_frame`을 **재사용**한다(WAN-146/106 관행) — 화면·
    CSV·DB가 각자 표를 만들면 같은 거래가 세 곳에서 다른 숫자로 보인다. 그 위에
    `BOOK_TRADE_COLUMNS`(칸·손절가·익절가·리스크 금액·net R·재진입·같은 분 익절·탭 시각)를
    덧붙인다 — 북에만 있는 정보이고, 값은 전부 배치 기록(`PlacedSetup`)에서 그대로 온다.

    🚨 두 리스트가 같은 순서라는 계약은 **값으로** 확인한다(진입/청산 시각·손익 3중 대조).
    어긋나면 칸 라벨이 다른 거래에 붙은 표가 조용히 나가므로, 시끄럽게 죽는 쪽을 고른다.
    """
    from backtest.report import trades_to_display_frame  # 지연 import(사이클 회피)

    pairs = _ordered_pairs(segment)
    frame = trades_to_display_frame(segment.result, include_utc=True)
    if len(frame) != len(pairs):
        raise AssertionError(
            f"북 거래 표와 배치 짝의 길이가 다릅니다({len(frame)} != {len(pairs)}) — "
            "`build_result_from_trades`의 정렬과 어긋났습니다(WAN-346 §0 전제)."
        )
    for index, ((trade, _placement), engine_trade) in enumerate(
        zip(pairs, segment.result.trades, strict=True)
    ):
        if (
            trade.entry_time != engine_trade.entry_time
            or trade.exit_time != engine_trade.exit_time
            or trade.realized_pnl != engine_trade.realized_pnl
        ):
            raise AssertionError(
                f"북 거래 표 {index}번이 배치 기록과 다른 거래입니다 — 두 정렬이 같은 "
                "순열이라는 계약이 깨졌습니다(WAN-346 §0 전제)."
            )
    frame[COL_CELL_SYMBOL] = [p.cell[0] for _t, p in pairs]
    frame[COL_CELL_TF] = [p.cell[1] for _t, p in pairs]
    frame[COL_STOP_PRICE] = [p.stop_price for _t, p in pairs]
    frame[COL_TAKE_PROFIT_PRICE] = [p.take_profit_price for _t, p in pairs]
    frame[COL_RISK_AMOUNT] = [p.risk_amount for _t, p in pairs]
    frame[COL_NET_R] = [net_r(t, p) for t, p in pairs]
    frame[COL_IS_REENTRY] = [p.is_reentry for _t, p in pairs]
    frame[COL_SAME_STEP_TP] = [p.same_step_take_profit for _t, p in pairs]
    frame[COL_TRIGGER_KST] = [format_kst(p.trigger_time) for _t, p in pairs]
    return frame


def book_equity_to_display_frame(segment: BookSegment) -> pd.DataFrame:
    """북 한 구간의 **시드곡선** 표 (WAN-346 §0) — per-cell과 같은 함수·같은 열."""
    from backtest.report import equity_to_display_frame  # 지연 import(사이클 회피)

    return equity_to_display_frame(segment.result, include_utc=True)


def net_r(trade: Trade, placement: PlacedSetup) -> float:
    """거래당 실현 net R = 실현손익 ÷ **그 거래의** 리스크 금액(WAN-154 `mean_net_r`와 같은 자).

    복리 지갑에서 USD를 그냥 더하면 뒤쪽 거래가 표를 지배하므로(WAN-169/213), 크기를
    정규화한 이 자를 항상 나란히 낸다. 리스크가 0이면(사이징이 그런 거래를 내지 않지만
    방어적으로) 0으로 본다.
    """
    return trade.realized_pnl / placement.risk_amount if placement.risk_amount > 0 else 0.0
