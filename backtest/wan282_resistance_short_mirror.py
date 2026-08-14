"""WAN-282/283 — 저항-존(공급 OB) 숏을 현행 롱 모델의 거울로 (측정 전용 · 옵트인 · 기본값 불변).

WAN-283 후속: 체결 렌즈(`baseline`·`pen_5bp`)를 행 차원으로 실어 **열로 병기**하고(북·격리 숏
진단·헤지 분해·leave-one-out 전부), 15m 스코프를 `--append`로 잇는다. `--fill pen_5bp --append`가
baseline 행을 덮지 않고 나란히 남는다(병합 키에 렌즈가 들어간다 — WAN-282는 스코프만 봤다).

## 무엇을 묻나 (사용자 아이디어 2026-08-10)

WAN-280은 지지 존(수요 OB)의 **위쪽 경계에서** 숏을 쳐 「이 지지가 뚫린다」에 걸었고, 존이
버틸 때마다 손절나 모든 N에서 격리 숏 순 R이 음수였다 — **잘못된 존에서 숏친 것**이다. 이번은
**완전히 다른 존**이다: 위에 있는 **저항 존(베어리시/공급 OB)**에서 숏을 친다. 롱이 「지지가
버티고 오른다」에 걸듯 숏은 「저항이 버티고 내린다」에 건다 — **지금 롱 모델을 좌우 반전한 진짜
거울**이다.

⚠️ **이 표가 답하는 것은 「엣지」가 아니라 「위험의 모양」이다.** 저항-존 숏의 진입 엣지(무작위
보다 나은가)는 오늘 엔진에서 이미 아니오로 나왔다(WAN-164/201, 판정 c). 따라서 질문은
**「롱과 숏을 한 지갑에서 같이 굴리면 하락장에 숏이 헤지가 돼서 북 낙폭(MDD)이 줄고 위험조정
수익(수익/MDD)이 오르나」** 다(WAN-90 = 익절·손절 구조는 알파가 아니라 위험의 모양, 그 계열의
롱↔숏 버전).

## 전략 스펙 (현행 모델의 거울 · 옵트인)

지금 롱 진입 기하를 좌우 반전한다 — 새 규칙 없이 방향만 뒤집는다:

|          | 지금 (롱)              | 거울 (숏)                    |
| -------- | --------------------- | --------------------------- |
| 존       | 아래 수요 존(불리시 OB) | 위 저항 존(베어리시 OB)      |
| 진입     | 근단 지정가 매수       | 근단 지정가 매도             |
| 진입가   | band(볼린저) 재산정    | band(대칭)                   |
| 손절     | 존 아래로 무효화       | 존 위로 무효화               |
| 익절     | 손절폭 고정 1.5R 위    | 고정 1.5R 아래               |
| 재진입   | 익절 후 band 재무장    | 익절 후 band 재무장(대칭)    |

나머지(좁은-존 필터 1.28 · `intrabar_live` · 고정 1.5R · 채택 북 cap_only 5배 · 펀딩)는 **전부
채택 기본값 그대로**. 사실상 **채택 북에서 측정용으로 `short_enabled`만 켠 것**(베어리시 OB 숏 =
기존 숏 경로 WAN-89/145/164가 쓴 그 경로 · 재진입은 `_iter_reentries`가 숏을 대칭으로 처리)이되,
판정을 **북 비교**로 낸다. 그래서 숏 기하를 이 모듈이 다시 짜지 않는다 — 엔진이 이미 대칭이라
`short_enabled=True`가 곧 거울이다(회귀 테스트가 그 대칭을 동작으로 고정한다).

## 작업 범위 (측정 전용 · 옵트인 · 엔진 기본값 불변)

1. **측정 모듈** — 채택 북(5배·band 재진입)으로 **롱-온리 북**(= 인자 없는 `backtest.run
   --oos-warm`, `short_enabled=False`) vs **롱+숏 북**(`short_enabled=True`)을 돌린다. 후보 생성은
   `wan169.run_cells(short_enabled=...)`(WAN-282 옵트인), 배치는 `book_cli.build_book_rows(
   include_reentry=True)` — 채택 북과 같은 함수라 롱-온리 팔이 인자 없는 `backtest.run`과 구성상
   비트 일치한다.
2. **판정 = 북 비교(정본)**: 롱-온리 북 vs 롱+숏 북. `total_return`·**MDD·수익/MDD·최대 동시
   리스크·청산**. 총수익%는 복리 착시(WAN-213)라 판단은 위험조정·MDD·청산으로.
3. **격리 숏 진단**: 저항-존 숏만 per-cell 단독으로 시퀀싱해 표본·승률·`mean_net_r`(WAN-154)·
   청산 사유(붕괴익절/무효화손절/미청산). 진단이지 실매매가 아니다.
4. **분해**: 롱-온리 북의 최악 낙폭 창에서 롱+숏 북이 얼마나 덜 빠지나(같은 시각 헤지) · 종목별
   숏 기여.
5. **좌표**: 9종목 · 못 박은 6년(WAN-182) · 15m·1h·2h·4h(WAN-252) · IS·oos_warm(WAN-166) ·
   핀 없음(`ConfluenceParams()` = 오늘 엔진 · 재진입 ON band) · `baseline`(+`--fill pen_5bp`
   옵트인) · ETH·SOL·DOGE leave-one-out · 신규 3종목 펀딩 대리(BTC 도너).

## 성격 · 경고 (그대로 옮길 것)

측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·채택 북 cap_only 5배·band 재진입) 돌리며 옛
핀은 하나도 물려받지 않는다. `short_enabled=False` **기본값 유지**(측정용 숏이지 재활성화 아님).
⚠️ 전부 `baseline`(닿으면 체결) 낙관 위 값 — 숏은 존 경계 체결이라 큐 우선순위(WAN-98 Canceled)에
특히 약하다(`--fill pen_5bp` 병기). 총수익%는 복리 착시라 판단은 MDD·최대 동시 리스크·청산으로
한다(WAN-213/90). **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 헤지로 위험조정이
좋아져도 진입 알파가 아니라 위험의 모양(WAN-90)이다. 채택은 재-베이스라인 = 사용자 결정 ·
개발자 임의 착수 금지.

## 재현

```
uv run python -m backtest.wan282_resistance_short_mirror --tf 4h,1h,2h --jobs 6
uv run python -m backtest.wan282_resistance_short_mirror --tf 15m --append --jobs 9   # 무거움
uv run python -m backtest.wan282_resistance_short_mirror --fill pen_5bp --append      # 스트레스
uv run python -m backtest.wan282_resistance_short_mirror --from-csv                   # 요약만
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
from backtest.book_cli import ADOPTED_REENTRY_ENTRY_RULE, BookRunRow
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
)
from backtest.leverage_book import run_leverage_book
from backtest.models import EquityPoint, ExitReason, PositionSide, Trade
from backtest.run import ADOPTED_BOOK, parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    _segment_cells,
    _short,
    run_cells,
)
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan282_resistance_short_book.csv"
DEFAULT_DIAG_CSV = REPORTS_DIR / "wan282_resistance_short_diag.csv"
DEFAULT_HEDGE_CSV = REPORTS_DIR / "wan282_resistance_short_hedge.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan282_resistance_short_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 기본 TF = 4h·1h·2h(컴퓨트 실현 가능). 15m은 셀당 ~37분(WAN-203)이라 별도 무거운 실행.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "2h")

#: 북 비교 두 팔. 롱-온리 = 채택 band 재진입 북(현행) · 롱+숏 = 거기에 베어리시 OB 숏 추가.
ARM_LONG_ONLY = "long_only"
ARM_LONG_SHORT = "long_short"
ARMS: tuple[str, ...] = (ARM_LONG_ONLY, ARM_LONG_SHORT)

#: 정본 구간 — WAN-166 규약(oos_warm 주 · oos 스트레스).
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 셀·스코프는 판정에서 뺀다.
MIN_TRADES = 20

#: 신규 3종목 — 펀딩 0행이라 순수익 열이 낙관적(WAN-178 백필 전, †로 표시).
FUNDING_GAP_SYMBOLS: frozenset[str] = frozenset(
    harness.normalize_symbol(s) for s in ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
)

#: leave-one-out 대상(편중 진단, 완료기준 4·5).
LOO_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE")


# --------------------------------------------------------------------------- #
# 격리 숏 순 R (WAN-154 정의, WAN-280 재사용)
# --------------------------------------------------------------------------- #


def _net_r(trade_return_pct: float, entry_price: float, stop_price: float) -> float:
    """격리 순 R = 순수익률 ÷ (|진입−손절| ÷ 진입) = 실현손익 ÷ 리스크 금액(WAN-154).

    `Trade.return_pct` = 실현손익 ÷ 진입 명목이고 리스크 금액 = 수량 × |진입−손절| =
    (명목/진입) × |진입−손절| 이므로, 수량이 상쇄돼 사이징과 무관한 거래당 R을 낸다.
    """
    risk_frac = abs(entry_price - stop_price) / entry_price if entry_price else 0.0
    return trade_return_pct / risk_frac if risk_frac > 0 else 0.0


# --------------------------------------------------------------------------- #
# 팔 셀 생성 (채택 북 후보 — short off/on)
# --------------------------------------------------------------------------- #


def run_arm_cells(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    short_enabled: bool,
    fill: harness.FillPreset | None = None,
    jobs: int = 1,
    funding_proxy: bool = True,
    log: bool = True,
) -> list[CellPayload]:
    """한 팔의 채택 북 후보 셀을 낸다 — `short_enabled`만 다르고 나머지는 채택 북 그대로.

    `adv_fraction=UNSET`(유동성 한도 0.005 켬)·`reentry=True`·`reentry_entry_rule="band"`는
    `book_cli.run_book`(= 인자 없는 `backtest.run`)과 같은 채택 값이라, `short_enabled=False`
    팔은 인자 없는 `backtest.run --oos-warm`과 구성상 비트 일치한다(검산). 펀딩 대리(BTC 도너)도
    채택 북과 같다.
    """
    cells = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        adv_fraction=harness.UNSET,
        reentry=True,
        reentry_entry_rule=ADOPTED_REENTRY_ENTRY_RULE,
        fill=fill,
        short_enabled=short_enabled,
    )
    if funding_proxy:
        cells, note = apply_funding_proxy(cells)
        if note and log:
            print(
                f"[wan282] 펀딩 대리({'롱+숏' if short_enabled else '롱-온리'}): {note}", flush=True
            )
    return cells


# --------------------------------------------------------------------------- #
# 북 비교 (완료기준 2)
# --------------------------------------------------------------------------- #


class BookScopeRow(BaseModel):
    """한 스코프 × 구간 × 팔 × 제외종목의 북 집계 (완료기준 2)."""

    model_config = ConfigDict(frozen=True)

    scope: str
    arm: str
    fill: str = "baseline"
    """체결 렌즈(WAN-283) — `baseline`(닿으면 체결) · `pen_5bp`(관통 5bp 스트레스, WAN-96).
    옛 CSV(WAN-282)엔 이 열이 없어 로드 시 기본값 `baseline`으로 채워진다."""
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
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


def _to_scope_row(br: BookRunRow, *, scope: str, arm: str, exclude: str, fill: str) -> BookScopeRow:
    return BookScopeRow(
        scope=scope,
        arm=arm,
        fill=fill,
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


def build_book_scope_rows(
    long_only_cells: Sequence[CellPayload],
    long_short_cells: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    fill: str = "baseline",
) -> list[BookScopeRow]:
    """스코프(각 TF + 전체 `all`) × 구간 × 팔(롱-온리·롱+숏) × leave-one-out의 북 집계."""
    arm_cells = {ARM_LONG_ONLY: long_only_cells, ARM_LONG_SHORT: long_short_cells}
    timeframes = sorted({c.timeframe for c in long_only_cells}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    rows: list[BookScopeRow] = []
    for scope in scopes:
        for arm, cells in arm_cells.items():
            scoped = cells if scope == "all" else [c for c in cells if c.timeframe == scope]
            for exclude in ["", *LOO_SYMBOLS]:
                kept = [c for c in scoped if not exclude or _short(c.symbol) != exclude]
                if not kept:
                    continue
                book_rows = book_cli.build_book_rows(
                    kept,
                    book=ADOPTED_BOOK,
                    segments=SEGMENTS,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    include_reentry=True,
                )
                rows.extend(
                    _to_scope_row(br, scope=scope, arm=arm, exclude=exclude, fill=fill)
                    for br in book_rows
                )
    return rows


# --------------------------------------------------------------------------- #
# 격리 숏 진단 (완료기준 3)
# --------------------------------------------------------------------------- #


def _short_candidates(cell: CellPayload, segment: str) -> list[_Candidate]:
    """이 셀·구간의 저항-존 숏 후보(base + band 재진입) — 롱은 뺀다.

    베어리시 OB 숏은 `short_enabled=True` 후보 생성이 이미 낸 것이라 방향으로 거른다. `oos_warm`은
    base·재진입 공히 full 후보를 칸 경계로 걸러(진입 시각 ≥ boundary) 만든다(북과 같은 규약).
    """
    if segment == SEGMENT_OOS_WARM:
        base = cell.candidates[SEGMENT_FULL]
        reentry = cell.reentry_candidates.get(SEGMENT_FULL, ())
        boundary = cell.boundary_ms
    else:
        base = cell.candidates[segment]
        reentry = cell.reentry_candidates.get(segment, ())
        boundary = 0
    return [
        c for c in (*base, *reentry) if c.side is PositionSide.SHORT and c.trigger_time >= boundary
    ]


def _funding_for(cell: CellPayload, segment: str) -> tuple[FundingRate, ...]:
    return cell.funding[SEGMENT_FULL] if segment == SEGMENT_OOS_WARM else cell.funding[segment]


class ShortDiagRow(BaseModel):
    """한 (심볼, TF) × 구간의 격리 저항-존 숏 진단 (완료기준 3)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    fill: str = "baseline"
    """체결 렌즈(WAN-283) — `baseline` · `pen_5bp`. 옛 CSV엔 없어 로드 시 `baseline`."""
    segment: str
    short_trades: int
    """per-cell 단독 시퀀싱으로 실제 잡힌 숏 거래 수."""
    wins: int
    """붕괴로 익절(진입−1.5R)한 숏 = 저항이 깨져 하락."""
    stops: int
    """무효화 손절(진입+1R)한 숏 = 저항이 버티고 상승."""
    unclosed: int
    """데이터끝까지 미청산 숏."""
    net_r_sum: float
    """격리 순 R 합(WAN-154 — 실현손익 ÷ 리스크 금액)."""
    net_pp_sum: float
    """격리 순수익 %p 합(진입 명목 대비)."""
    funding_coverage_gap: bool
    """신규 종목(펀딩 0행)이라 순수익 낙관인가(†)."""

    @property
    def win_rate(self) -> float | None:
        decided = self.wins + self.stops
        return self.wins / decided if decided else None

    @property
    def mean_net_r(self) -> float | None:
        return self.net_r_sum / self.short_trades if self.short_trades else None


def _diag_row(
    cell: CellPayload,
    segment: str,
    paired: Sequence[tuple[_Candidate, Trade]],
    gap: bool,
    *,
    fill: str,
) -> ShortDiagRow:
    wins = stops = unclosed = 0
    net_r_sum = net_pp_sum = 0.0
    for cand, trade in paired:
        reason = trade.exits[-1].reason if trade.exits else ExitReason.END_OF_DATA
        if reason is ExitReason.TAKE_PROFIT:
            wins += 1
        elif reason is ExitReason.STOP_LOSS:
            stops += 1
        else:
            unclosed += 1
        net_r_sum += _net_r(trade.return_pct, trade.entry_price, cand.stop_price)
        net_pp_sum += trade.return_pct * 100.0
    return ShortDiagRow(
        symbol=cell.symbol,
        timeframe=cell.timeframe,
        fill=fill,
        segment=segment,
        short_trades=len(paired),
        wins=wins,
        stops=stops,
        unclosed=unclosed,
        net_r_sum=net_r_sum,
        net_pp_sum=net_pp_sum,
        funding_coverage_gap=gap,
    )


def build_diag_rows(
    long_short_cells: Sequence[CellPayload], *, fill: str = "baseline"
) -> list[ShortDiagRow]:
    """롱+숏 셀에서 저항-존 숏만 per-cell 단독으로 시퀀싱해 진단 행을 낸다.

    격리 = 실매매가 아니라 「숏이 애초에 돈이 되나」 진단이다(실제 북은 롱과 슬롯을 공유한다).
    시퀀싱 cfg는 표준 채택 cfg(격리는 단일 포지션이라 유동성 한도가 무관하다).
    """
    rows: list[ShortDiagRow] = []
    for cell in long_short_cells:
        gap = cell.symbol in FUNDING_GAP_SYMBOLS
        cfg = harness.build_config(cell.timeframe)
        for segment in SEGMENTS:
            shorts = _short_candidates(cell, segment)
            paired = sequence_with_candidates(shorts, cfg, _funding_for(cell, segment))
            rows.append(_diag_row(cell, segment, paired, gap, fill=fill))
    return rows


# --------------------------------------------------------------------------- #
# 하락장 헤지 분해 (완료기준 4) — 롱-온리 최악 낙폭 창에서 롱+숏이 덜 빠지나
# --------------------------------------------------------------------------- #


def _book_equity_curve(cells: Sequence[CellPayload], segment: str) -> list[EquityPoint]:
    """이 팔·구간의 채택 북 자본곡선 — build_book_rows와 같은 배치를 다시 돌려 곡선만 낸다."""
    seg_cells = _segment_cells(list(cells), segment, "", include_reentry=True)
    cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    outcome = run_leverage_book(seg_cells, cfg, ADOPTED_BOOK)
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    return result.equity_curve


def _max_drawdown_window(curve: Sequence[EquityPoint]) -> tuple[float, int, int]:
    """(최대낙폭, 고점 시각, 저점 시각). 곡선이 비면 (0, 0, 0)."""
    if not curve:
        return 0.0, 0, 0
    peak = curve[0].equity
    peak_time = curve[0].time
    best_mdd = 0.0
    best_peak_t = curve[0].time
    best_trough_t = curve[0].time
    for point in curve:
        if point.equity > peak:
            peak = point.equity
            peak_time = point.time
        dd = (peak - point.equity) / peak if peak > 0 else 0.0
        if dd > best_mdd:
            best_mdd = dd
            best_peak_t = peak_time
            best_trough_t = point.time
    return best_mdd, best_peak_t, best_trough_t


def _window_return(curve: Sequence[EquityPoint], start_t: int, end_t: int) -> float | None:
    """[start_t, end_t] 창에서의 수익률 = (창끝 자본 / 창시작 자본) − 1(같은 시각 헤지 비교).

    창시작 = start_t 이하 마지막 점의 자본(그 시점 계좌), 창끝 = end_t 이하 마지막 점의 자본.
    창을 벗어난 거래가 없으면(점 부족) None.
    """
    if not curve or start_t > end_t:
        return None
    start_eq: float | None = None
    end_eq: float | None = None
    for point in curve:
        if point.time <= start_t:
            start_eq = point.equity
        if point.time <= end_t:
            end_eq = point.equity
    if start_eq is None or end_eq is None or start_eq <= 0:
        return None
    return end_eq / start_eq - 1.0


class HedgeRow(BaseModel):
    """한 스코프 × 구간의 하락장 헤지 분해 (완료기준 4).

    롱-온리 북의 **최악 낙폭 창**을 잡고, 그 **같은 시각 창**에서 롱+숏 북이 얼마나 빠졌는지를
    나란히 놓는다 — 숏이 하락장 헤지가 되면 롱+숏의 창 수익률이 롱-온리보다 덜 마이너스다.
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    fill: str = "baseline"
    """체결 렌즈(WAN-283) — `baseline` · `pen_5bp`. 옛 CSV엔 없어 로드 시 `baseline`."""
    segment: str
    long_only_mdd: float
    long_short_mdd: float
    window_start: int
    window_end: int
    long_only_window_return: float | None
    long_short_window_return: float | None

    @field_validator("long_only_window_return", "long_short_window_return", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value


def build_hedge_rows(
    long_only_cells: Sequence[CellPayload],
    long_short_cells: Sequence[CellPayload],
    *,
    fill: str = "baseline",
) -> list[HedgeRow]:
    """스코프(전체 `all` + 각 TF)의 구간별 하락장 헤지 분해. 곡선 재계산이 필요해 별도로 낸다.

    스코프는 `build_book_scope_rows`와 **같은 규칙**으로 정한다 — 여러 TF면 `all` + 각 TF,
    단일 TF면 그 TF뿐(`all` 없음). 이렇게 해야 `--tf 15m --append`가 15m 곡선을 `all`
    라벨로 조용히 덮어쓰지 못한다(WAN-283 PM 지적: 라벨은 all인데 실제론 15m인 오염 —
    WAN-91/95/112/123/159 부류). `all`은 book.csv의 `all`과 **항상 같은 TF 집합**을 가리킨다.
    """
    timeframes = sorted({c.timeframe for c in long_only_cells}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    rows: list[HedgeRow] = []
    for scope in scopes:
        lo_cells = (
            long_only_cells
            if scope == "all"
            else [c for c in long_only_cells if c.timeframe == scope]
        )
        ls_cells = (
            long_short_cells
            if scope == "all"
            else [c for c in long_short_cells if c.timeframe == scope]
        )
        for segment in SEGMENTS:
            lo_curve = _book_equity_curve(lo_cells, segment)
            ls_curve = _book_equity_curve(ls_cells, segment)
            lo_mdd, peak_t, trough_t = _max_drawdown_window(lo_curve)
            ls_mdd, _lp, _lt = _max_drawdown_window(ls_curve)
            rows.append(
                HedgeRow(
                    scope=scope,
                    fill=fill,
                    segment=segment,
                    long_only_mdd=lo_mdd,
                    long_short_mdd=ls_mdd,
                    window_start=peak_t,
                    window_end=trough_t,
                    long_only_window_return=_window_return(lo_curve, peak_t, trough_t),
                    long_short_window_return=_window_return(ls_curve, peak_t, trough_t),
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


#: 렌즈 표시 순서(WAN-283) — baseline(공식) 먼저, 그 뒤 스트레스 렌즈.
LENS_ORDER: tuple[str, ...] = ("baseline", "pen_1bp", "pen_5bp", "pen_5bp_drop_50")


def _lenses_present(rows: Sequence[BookScopeRow] | Sequence[ShortDiagRow]) -> list[str]:
    """행에 실제로 있는 렌즈를 표시 순서대로. 알 수 없는 렌즈는 뒤에 붙인다."""
    present = {r.fill for r in rows}
    ordered = [lens for lens in LENS_ORDER if lens in present]
    ordered += sorted(present - set(ordered))
    return ordered


def _pick_book(
    rows: Sequence[BookScopeRow],
    *,
    scope: str,
    arm: str,
    segment: str,
    exclude: str = "",
    fill: str = "baseline",
) -> BookScopeRow | None:
    for r in rows:
        if (
            r.scope == scope
            and r.arm == arm
            and r.segment == segment
            and r.exclude_symbol == exclude
            and r.fill == fill
        ):
            return r
    return None


def _short_net_r(
    diag_rows: Sequence[ShortDiagRow], segment: str, *, fill: str = "baseline"
) -> float:
    return sum(r.net_r_sum for r in diag_rows if r.segment == segment and r.fill == fill)


def _lens_headline(*, mdd_down: bool, rm_up: bool, sample_ok: bool) -> str:
    if mdd_down and rm_up and sample_ok:
        return (
            "**저항-존 숏이 하락장 헤지가 된다 — 롱+숏이 롱-온리보다 MDD가 낮고 수익/MDD가 높다.** "
            "단 격리 숏 순 R을 함께 읽을 것(아래)."
        )
    if not mdd_down and not rm_up:
        return (
            "**헤지가 되지 않는다 — 저항-존 숏을 얹어도 롱+숏의 MDD가 안 줄고 수익/MDD도 안 "
            "오른다.** 숏이 롱과 슬롯·자본을 나눠 쓰며 노출만 키운다."
        )
    return (
        "**엇갈린다 — MDD와 수익/MDD가 같은 방향으로 개선되지 않는다(한쪽만 나아진다).** "
        "강건한 헤지 이득으로 읽지 않는다."
    )


def _lens_body(
    lo: BookScopeRow, ls: BookScopeRow, short_net: float, *, main_scope: str, label: str
) -> str:
    d_mdd = (ls.max_drawdown - lo.max_drawdown) * 100
    lo_rm, ls_rm = lo.return_over_mdd, ls.return_over_mdd
    rm_txt = f"{lo_rm:.2f}→{ls_rm:.2f}" if lo_rm is not None and ls_rm is not None else "—"
    gate = "" if (ls.sample_ok and lo.sample_ok) else " ⚠️(20거래 미만)"
    return (
        f"{main_scope}·oos_warm·{label}: MDD {lo.max_drawdown * 100:.2f}%→"
        f"{ls.max_drawdown * 100:.2f}% (Δ{d_mdd:+.2f}%p) · 수익/MDD {rm_txt} · 최대동시리스크 "
        f"{lo.max_concurrent_risk * 100:.2f}%→{ls.max_concurrent_risk * 100:.2f}% · 청산 "
        f"{lo.liquidation_events}→{ls.liquidation_events} · 거래 {lo.num_trades}→{ls.num_trades}"
        f"{gate} · 격리 숏 순R {short_net:+.1f}."
    )


def verdict(book_rows: Sequence[BookScopeRow], diag_rows: Sequence[ShortDiagRow]) -> str:
    """저항-존 숏을 얹으면 북의 MDD가 줄고 수익/MDD가 오르나(= 하락장 헤지가 되나).

    주 스코프(all) oos_warm에서 롱-온리 vs 롱+숏의 MDD·수익/MDD·최대 동시 리스크·청산을 비교하고,
    격리 숏 순 R의 부호를 병기한다(숏이 순 손실이면 헤지 이득도 진입 알파가 아니라 위험의 모양일
    뿐이다). WAN-283은 `baseline`(주) 판정에 이어 **`pen_5bp`(체결 보수화)에서 헤지 방향과 격리
    숏 순 R의 부호가 살아남는지**를 병기한다. 숫자는 전부 행에서 계산한다(문장에 박으면 재실행 뒤
    거짓말 — WAN-164).
    """
    main_scope = "all" if any(r.scope == "all" for r in book_rows) else None
    if main_scope is None:
        scopes = sorted({r.scope for r in book_rows}, key=timeframe_to_ms)
        main_scope = scopes[0] if scopes else ""
    lo = _pick_book(book_rows, scope=main_scope, arm=ARM_LONG_ONLY, segment=SEGMENT_OOS_WARM)
    ls = _pick_book(book_rows, scope=main_scope, arm=ARM_LONG_SHORT, segment=SEGMENT_OOS_WARM)
    if lo is None or ls is None:
        return "**판정 불가** — 주 스코프의 oos_warm 롱-온리/롱+숏 행이 없습니다."
    short_net = _short_net_r(diag_rows, SEGMENT_OOS_WARM)
    lo_rm, ls_rm = lo.return_over_mdd, ls.return_over_mdd
    rm_up = lo_rm is not None and ls_rm is not None and ls_rm > lo_rm
    mdd_down = ls.max_drawdown < lo.max_drawdown
    sample_ok = ls.sample_ok and lo.sample_ok
    head = _lens_headline(mdd_down=mdd_down, rm_up=rm_up, sample_ok=sample_ok)
    body = _lens_body(lo, ls, short_net, main_scope=main_scope, label="baseline")

    # WAN-283 — pen_5bp(체결 보수화)에서 헤지 방향·격리 숏 순R 부호가 살아남는지 병기.
    pen_lo = _pick_book(
        book_rows, scope=main_scope, arm=ARM_LONG_ONLY, segment=SEGMENT_OOS_WARM, fill="pen_5bp"
    )
    pen_ls = _pick_book(
        book_rows, scope=main_scope, arm=ARM_LONG_SHORT, segment=SEGMENT_OOS_WARM, fill="pen_5bp"
    )
    pen_sentence = ""
    if pen_lo is not None and pen_ls is not None:
        pen_short = _short_net_r(diag_rows, SEGMENT_OOS_WARM, fill="pen_5bp")
        pen_mdd_down = pen_ls.max_drawdown < pen_lo.max_drawdown
        base_short = _short_net_r(diag_rows, SEGMENT_OOS_WARM, fill="baseline")
        hedge_kept = "유지" if pen_mdd_down == mdd_down else "뒤집힘"
        sign_kept = "유지" if (pen_short >= 0) == (base_short >= 0) else "뒤집힘"
        pen_body = _lens_body(pen_lo, pen_ls, pen_short, main_scope=main_scope, label="pen_5bp")
        pen_sentence = (
            f" **pen_5bp 병기:** {pen_body} 관통 5bp에서 헤지 방향(MDD 감소 여부) 부호 "
            f"{hedge_kept}, 격리 숏 순R 부호 {sign_kept}."
        )
    else:
        pen_sentence = " (pen_5bp 행 없음 — `--fill pen_5bp --append` 미실행 스코프.)"

    return (
        f"{head} {body}{pen_sentence} ⚠️ 총수익% 변화는 복리 착시라 판정에 넣지 않는다(WAN-213). "
        "전부 `baseline`(닿으면 체결) 낙관 위 값이고 숏은 존 경계 체결이라 큐 우선순위(WAN-98 "
        "Canceled)에 특히 약하다. 「엣지 없음」(WAN-84/88/111/114/124/151/201/248)은 다른 질문이라 "
        "불변이고, 헤지 이득이 있어도 진입 알파가 아니라 위험의 모양(WAN-90)이다. 채택은 "
        "재-베이스라인 = 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def book_to_frame(rows: Sequence[BookScopeRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookScopeRow.model_fields))


def diag_to_frame(rows: Sequence[ShortDiagRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(ShortDiagRow.model_fields))


def hedge_to_frame(rows: Sequence[HedgeRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(HedgeRow.model_fields))


def book_from_csv(path: Path) -> list[BookScopeRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [BookScopeRow.model_validate(rec) for rec in records]


def diag_from_csv(path: Path) -> list[ShortDiagRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [ShortDiagRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def hedge_from_csv(path: Path) -> list[HedgeRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
    return [HedgeRow.model_validate(rec) for rec in records]


def merge_book(existing: Sequence[BookScopeRow], new: Sequence[BookScopeRow]) -> list[BookScopeRow]:
    """(스코프, 렌즈) 키로 병합 — 같은 렌즈의 같은 스코프만 갱신하고 다른 렌즈는 보존한다.

    렌즈를 키에 넣기 전(WAN-282)에는 스코프만 봤으므로 `--fill pen_5bp --append`가 baseline
    행을 덮어썼다. WAN-283은 `pen_5bp`를 `baseline` 옆에 나란히 두기 위해 렌즈를 키로 쓴다.
    """
    new_keys = {(r.scope, r.fill) for r in new}
    kept = [r for r in existing if (r.scope, r.fill) not in new_keys]
    return [*kept, *new]


def merge_diag(existing: Sequence[ShortDiagRow], new: Sequence[ShortDiagRow]) -> list[ShortDiagRow]:
    new_keys = {(r.timeframe, r.fill) for r in new}
    kept = [r for r in existing if (r.timeframe, r.fill) not in new_keys]
    return [*kept, *new]


def merge_hedge(existing: Sequence[HedgeRow], new: Sequence[HedgeRow]) -> list[HedgeRow]:
    """(스코프, 렌즈) 키로 병합 — book/diag과 같은 규칙(WAN-283 PM 교정).

    옛 구현은 렌즈만 키로 봐서 `--tf 15m --append`가 만든 `scope="all"` 15m 행이 진짜
    `all`(1h·2h·4h) 행을 덮었다(라벨은 all인데 15m 숫자인 조용한 오염). 스코프를 키에 넣으면
    15m은 이제 `scope="15m"`으로만 들어가 `all`을 건드리지 않는다(build_hedge_rows가 단일 TF에
    `all`을 만들지 않으므로). 같은 (스코프, 렌즈)는 새 전 구간 곡선으로 통째 교체한다.
    """
    new_keys = {(r.scope, r.fill) for r in new}
    kept = [r for r in existing if (r.scope, r.fill) not in new_keys]
    return [*kept, *new]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _scope_sort_key(scope: str) -> int:
    """스코프 정렬 키 — `all`은 맨 앞(−1), 나머지는 TF 크기순. `timeframe_to_ms('all')`은
    ValueError를 내므로 여기서 가른다(헤지/북 스코프에 `all`이 섞여 있다)."""
    return -1 if scope == "all" else timeframe_to_ms(scope)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _signed_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def describe_engine() -> str:
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={OrderBlockParams().combine_obs}, max_zone_width_atr={p.max_zone_width_atr}, "
        f"short_enabled={p.short_enabled}(기본), "
        f"book={ADOPTED_BOOK.leverage_mode}×{ADOPTED_BOOK.leverage_multiple}, "
        f"reentry={ADOPTED_REENTRY_ENTRY_RULE}"
    )


def _book_table(book_rows: Sequence[BookScopeRow], scope: str, segment: str) -> list[str]:
    """렌즈(baseline·pen_5bp)를 열로 병기(WAN-283) — 각 렌즈 안에서 롱-온리→롱+숏."""
    lenses = _lenses_present(book_rows) or ["baseline"]
    lines = [
        "| 렌즈 | 팔 | 거래 | 총수익%† | MDD | 수익/MDD | 최대동시리스크 | 청산 | 최대칸 |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for lens in lenses:
        for arm in ARMS:
            r = _pick_book(book_rows, scope=scope, arm=arm, segment=segment, fill=lens)
            if r is None:
                continue
            gate = "" if r.sample_ok else " ⚠️"
            label = "롱-온리(현행)" if arm == ARM_LONG_ONLY else "롱+숏(거울)"
            lines.append(
                f"| {lens} | {label} | {r.num_trades}{gate} | {_pct(r.total_return)} | "
                f"{_pct(r.max_drawdown)} | {_rr(r.return_over_mdd)} | "
                f"{_pct(r.max_concurrent_risk)} | {r.liquidation_events} | {r.peak_concurrency} |"
            )
    return lines


def _diag_table(diag_rows: Sequence[ShortDiagRow], timeframe: str, segment: str) -> list[str]:
    """격리 숏 진단 — 렌즈를 열로 병기(WAN-283). 심볼별로 baseline→pen_5bp를 나란히."""
    lenses = _lenses_present(diag_rows) or ["baseline"]
    symbols = sorted(
        {r.symbol for r in diag_rows if r.timeframe == timeframe and r.segment == segment}
    )
    lines = [
        "| 심볼 | 렌즈 | 숏 | 승률 | 붕괴익절 | 무효화손절 | 미청산 | 순R | 평균순R | 순%p |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for symbol in symbols:
        for lens in lenses:
            r = next(
                (
                    x
                    for x in diag_rows
                    if x.symbol == symbol
                    and x.timeframe == timeframe
                    and x.segment == segment
                    and x.fill == lens
                    and x.short_trades
                ),
                None,
            )
            if r is None:
                continue
            fund = "†" if r.funding_coverage_gap else ""
            wr = f"{r.win_rate * 100:.1f}%" if r.win_rate is not None else "—"
            mnr = f"{r.mean_net_r:+.3f}" if r.mean_net_r is not None else "—"
            lines.append(
                f"| {_short(r.symbol)}{fund} | {lens} | {r.short_trades} | {wr} | {r.wins} | "
                f"{r.stops} | {r.unclosed} | {r.net_r_sum:+.2f} | {mnr} | {r.net_pp_sum:+.1f} |"
            )
    return lines


def _per_symbol_short_table(diag_rows: Sequence[ShortDiagRow], segment: str) -> list[str]:
    """종목별 숏 기여(완료기준 4) — 전 TF 합. 렌즈를 열로 병기(WAN-283)."""
    lenses = _lenses_present(diag_rows) or ["baseline"]
    symbols = sorted({r.symbol for r in diag_rows})
    lines = [
        "| 심볼 | 렌즈 | 숏 | 순R합 | 순%p합 |",
        "| -- | -- | --: | --: | --: |",
    ]
    for symbol in symbols:
        for lens in lenses:
            scoped = [
                r
                for r in diag_rows
                if r.symbol == symbol and r.segment == segment and r.fill == lens
            ]
            fills = sum(r.short_trades for r in scoped)
            if fills == 0:
                continue
            fund = "†" if any(r.funding_coverage_gap for r in scoped) else ""
            net_r = sum(r.net_r_sum for r in scoped)
            net_pp = sum(r.net_pp_sum for r in scoped)
            lines.append(
                f"| {_short(symbol)}{fund} | {lens} | {fills} | {net_r:+.2f} | {net_pp:+.1f} |"
            )
    return lines


def _hedge_table(hedge_rows: Sequence[HedgeRow], scope: str) -> list[str]:
    """하락장 헤지 분해 — 렌즈를 열로 병기(WAN-283). 구간 안에서 baseline→pen_5bp.

    `scope`로 골라내므로 `all`(= book.csv의 all)과 각 TF 헤지가 섞이지 않는다(WAN-283 PM
    교정: `all` 라벨로 15m 숫자를 쓰지 말 것).
    """
    scoped = [h for h in hedge_rows if h.scope == scope]
    lenses = [lens for lens in LENS_ORDER if any(h.fill == lens for h in scoped)] or ["baseline"]
    lines = [
        "| 구간 | 렌즈 | 롱-온리 MDD | 롱+숏 MDD | 롱-온리 창수익 | 롱+숏 창수익 |",
        "| -- | -- | --: | --: | --: | --: |",
    ]
    for segment in SEGMENTS:
        for lens in lenses:
            r = next((h for h in scoped if h.segment == segment and h.fill == lens), None)
            if r is None:
                continue
            lines.append(
                f"| {segment} | {lens} | {_pct(r.long_only_mdd)} | {_pct(r.long_short_mdd)} | "
                f"{_signed_pct(r.long_only_window_return)} | "
                f"{_signed_pct(r.long_short_window_return)} |"
            )
    return lines


def _loo_table(book_rows: Sequence[BookScopeRow], scope: str, segment: str) -> list[str]:
    """종목 편중(leave-one-out) — 렌즈를 열로 병기(WAN-283)."""
    lenses = _lenses_present(book_rows) or ["baseline"]
    lines = [
        "| 렌즈 | 팔 | 전체 | " + " | ".join(f"−{s}" for s in LOO_SYMBOLS) + " |",
        "| -- | -- | --: | " + " | ".join("--:" for _ in LOO_SYMBOLS) + " |",
    ]
    for lens in lenses:
        for arm in ARMS:
            base = _pick_book(book_rows, scope=scope, arm=arm, segment=segment, fill=lens)
            if base is None:
                continue
            cells = []
            for s in LOO_SYMBOLS:
                r = _pick_book(
                    book_rows, scope=scope, arm=arm, segment=segment, exclude=s, fill=lens
                )
                cells.append(_pct(r.total_return) if r is not None else "—")
            label = "롱-온리" if arm == ARM_LONG_ONLY else "롱+숏"
            lines.append(
                f"| {lens} | {label} | {_pct(base.total_return)} | " + " | ".join(cells) + " |"
            )
    return lines


def build_summary_markdown(
    book_rows: Sequence[BookScopeRow],
    diag_rows: Sequence[ShortDiagRow],
    hedge_rows: Sequence[HedgeRow],
    *,
    book_csv: Path,
    diag_csv: Path,
) -> str:
    timeframes = sorted({r.timeframe for r in diag_rows}, key=timeframe_to_ms)
    main_scope = (
        "all" if any(r.scope == "all" for r in book_rows) else (timeframes[0] if timeframes else "")
    )
    lenses_txt = " · ".join(_lenses_present(book_rows) or ["baseline"])
    lines = [
        "# WAN-282/283 — 저항-존(공급 OB) 숏을 현행 롱 모델의 거울로 (측정 전용)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·채택 북 cap_only 5배 · 재진입 "
        "ON band) 돌리며 옛 핀은 하나도 물려받지 않는다. `short_enabled=False` **기본값 유지**"
        "(측정용 숏이지 재활성화 아님). 못 박은 6년 창(WAN-182) · 기본값·토대 불변"
        "(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        f"**WAN-283 후속**: 체결 렌즈 `{lenses_txt}`를 **열로 병기**한다 — `pen_5bp`(관통 5bp "
        "스트레스, WAN-96)에서 헤지 방향(MDD 감소)과 격리 숏 순 R의 부호가 살아남는지, 그리고 "
        "**15m 스코프**가 1h·2h·4h와 같은 방향인지가 이 후속의 질문이다. 숏은 존 경계(밴드가) "
        "체결이라 큐 우선순위(WAN-98 Canceled)에 가장 약해 `baseline`(닿으면 체결) 낙관이 특히 "
        "위험한 축이다.",
        "",
        "## 이 표가 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영. 숏 = 현행 롱의 거울: 베어리시 OB **근단** 지정가 "
        "매도 · band(볼린저) 재산정 · 손절=존 **위로** 무효화 · 익절=진입 −1.5R(아래) · 익절 후 "
        "band 재무장. 숏 기하를 이 모듈이 다시 짜지 않는다 — 엔진이 이미 좌우 대칭이라 "
        "`short_enabled=True`가 곧 거울이다(회귀 테스트가 대칭을 동작으로 고정).",
        "",
        "**팔**: `롱-온리(현행)` = 채택 band 재진입 북(= 인자 없는 `backtest.run --oos-warm`) · "
        "`롱+숏(거울)` = 거기에 베어리시 OB 숏을 같은 지갑에 추가.",
        "",
        f"재현: `uv run python -m backtest.wan282_resistance_short_mirror --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` → `--fill pen_5bp --append` → `--tf 15m "
        f"--append`(baseline·pen_5bp, 무거움). 요약만: `--from-csv`. 원자료: `{book_csv}`(북) · "
        f"`{diag_csv}`(격리 숏 진단).",
        "",
        "⚠️ **총수익%는 복리 착시**(WAN-213) — 판단은 **MDD · 수익/MDD · 최대 동시 리스크 · "
        "청산**의 롱-온리→롱+숏 차이다. 전부 `baseline`(닿으면 체결) 낙관 위 값이고 숏은 존 경계 "
        "체결이라 큐 우선순위(WAN-98 Canceled)에 특히 약하다.",
        "",
        f"## 1. 북 비교 (완료기준 2) — {main_scope}",
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
        "## 3. 격리 저항-존 숏 진단 (완료기준 3) — oos_warm",
        "",
        "저항-존 숏만 per-cell 단독으로 시퀀싱해 「숏이 애초에 돈이 되나」. `평균순R` = 실현손익 ÷ "
        "리스크 금액(WAN-154). 붕괴익절 = 저항 깨져 하락(−1.5R 도달) · 무효화손절 = 저항 버티고 "
        "상승(+1R). 격리는 실매매가 아니라 진단이다. †=신규 종목(펀딩 0행 → 순수익 낙관).",
        "",
    ]
    for tf in timeframes:
        table = _diag_table(diag_rows, tf, SEGMENT_OOS_WARM)
        if len(table) > 2:
            lines += [f"### {tf}", "", *table, ""]
    hedge_scopes = sorted({h.scope for h in hedge_rows}, key=_scope_sort_key)
    hedge_tfs = [s for s in hedge_scopes if s != "all"]
    hedge_missing = [tf for tf in timeframes if tf not in hedge_scopes]
    lines += [
        f"## 4. 하락장 헤지 분해 (완료기준 4) — 전체 북({main_scope})",
        "",
        "롱-온리 북의 **최악 낙폭 창**을 잡고, 그 같은 시각 창에서 롱+숏 북이 얼마나 빠졌는지 "
        "나란히 본다 — 숏이 헤지가 되면 롱+숏의 창수익이 롱-온리보다 덜 마이너스다(또는 플러스).",
        "",
        f"⚠️ **`{main_scope}` 스코프는 book.csv의 같은 스코프와 동일한 TF 집합**이다(헤지 CSV의 "
        "스코프 라벨이 북과 항상 일치한다 — WAN-283 PM 교정, 회귀 테스트로 고정). TF별 헤지는 "
        "아래에 **자기 TF 스코프**로 병기한다.",
        "",
        *_hedge_table(hedge_rows, main_scope),
        "",
    ]
    if main_scope == "all" and hedge_tfs:
        lines += ["### TF 스코프별 헤지", ""]
        for tf in hedge_tfs:
            lines += [f"#### {tf}", "", *_hedge_table(hedge_rows, tf), ""]
    if hedge_missing:
        lines += [
            f"⚠️ **{', '.join(hedge_missing)} 헤지 미병기** — 헤지 분해는 곡선 재계산이 필요해 "
            "그 TF의 셀을 다시 돌려야 한다(15m은 셀당 무거움 · WAN-203). 그 TF의 북 비교(§2)와 "
            "격리 숏 진단(§3)은 병기돼 판정에 쓰인다. `all`은 이 TF를 포함하지 않는다.",
            "",
        ]
    lines += [
        "### 종목별 숏 기여 (oos_warm · 전 TF 합)",
        "",
        "헤지가 특정 종목에 쏠리는지. ⚠️ 순%p는 복리 전 격리 값이라 방향만 읽는다.",
        "",
        *_per_symbol_short_table(diag_rows, SEGMENT_OOS_WARM),
        "",
        "## 5. leave-one-out — 종목 편중 (oos_warm · 총수익%)",
        "",
        "롱+숏 팔의 이득/손해가 ETH·SOL·DOGE에 쏠리는지. ⚠️ 총수익%는 복리 착시라 방향만 읽는다.",
        "",
        f"### {main_scope}",
        "",
        *_loo_table(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        "## 판정 — 저항-존 숏이 하락장 헤지가 되나",
        "",
        verdict(book_rows, diag_rows),
        "",
        "⚠️ **이 표는 채택 근거가 아니라 측정이다.** 「엣지 없음」(WAN-84/88/111/114/124/151/201/"
        "248)은 다른 질문(진입 규칙이 무작위와 구분되는가)이라 불변이고, 저항-존 숏 진입 엣지는 "
        "WAN-164/201이 이미 「없음」으로 냈다. 헤지로 위험조정이 좋아져도 진입 알파가 아니라 "
        "위험의 모양(WAN-90)이다. **숏 재활성화 아님**(`short_enabled=False` 기본값 유지, "
        "측정용) · 채택은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지. **기본값·토대 불변**"
        "(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Wan282Report:
    book_rows: list[BookScopeRow]
    diag_rows: list[ShortDiagRow]
    hedge_rows: list[HedgeRow]


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    fill: harness.FillPreset | None = None,
    jobs: int = 1,
    funding_proxy: bool = True,
    log: bool = True,
) -> Wan282Report:
    long_only = run_arm_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        short_enabled=False,
        fill=fill,
        jobs=jobs,
        funding_proxy=funding_proxy,
        log=log,
    )
    long_short = run_arm_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        short_enabled=True,
        fill=fill,
        jobs=jobs,
        funding_proxy=funding_proxy,
        log=log,
    )
    lens = fill.name if fill is not None else "baseline"
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    book_rows = build_book_scope_rows(
        long_only, long_short, start_ms=start_ms, end_ms=end_ms, fill=lens
    )
    diag_rows = build_diag_rows(long_short, fill=lens)
    hedge_rows = build_hedge_rows(long_only, long_short, fill=lens)
    return Wan282Report(book_rows=book_rows, diag_rows=diag_rows, hedge_rows=hedge_rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-282 저항-존 숏 거울 (롱+숏 북 헤지 측정)")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument(
        "--fill",
        type=str,
        default="baseline",
        choices=("baseline", "pen_1bp", "pen_5bp"),
        help="체결 렌즈(관통만). baseline(기본) · pen_5bp(관통 5bp 스트레스, WAN-96).",
    )
    parser.add_argument("--out-book", type=Path, default=DEFAULT_BOOK_CSV)
    parser.add_argument("--out-diag", type=Path, default=DEFAULT_DIAG_CSV)
    parser.add_argument("--out-hedge", type=Path, default=DEFAULT_HEDGE_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="새로 돌린 행을 기존 CSV에 병합한다(같은 TF는 갱신, 새 TF는 추가).",
    )
    args = parser.parse_args(argv)

    out_book = Path(args.out_book)
    out_diag = Path(args.out_diag)
    out_hedge = Path(args.out_hedge)
    out_md = Path(args.out_md)
    fill_preset = harness.fill_preset(args.fill)

    if args.from_csv:
        book_rows = book_from_csv(out_book)
        diag_rows = diag_from_csv(out_diag)
        hedge_rows = hedge_from_csv(out_hedge) if out_hedge.exists() else []
        print(
            f"[wan282] CSV 로드 — 북 {len(book_rows)}행 · 진단 {len(diag_rows)}행 · "
            f"헤지 {len(hedge_rows)}행 (재실행 없음)"
        )
    else:
        report = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            fill=fill_preset,
            jobs=args.jobs,
        )
        out_book.parent.mkdir(parents=True, exist_ok=True)
        if args.append and out_book.exists() and out_diag.exists():
            book_rows = merge_book(book_from_csv(out_book), report.book_rows)
            diag_rows = merge_diag(diag_from_csv(out_diag), report.diag_rows)
            hedge_rows = (
                merge_hedge(hedge_from_csv(out_hedge), report.hedge_rows)
                if out_hedge.exists()
                else list(report.hedge_rows)
            )
        else:
            book_rows = list(report.book_rows)
            diag_rows = list(report.diag_rows)
            hedge_rows = list(report.hedge_rows)
        book_to_frame(book_rows).to_csv(out_book, index=False)
        diag_to_frame(diag_rows).to_csv(out_diag, index=False)
        hedge_to_frame(hedge_rows).to_csv(out_hedge, index=False)
        print(f"[wan282] 북 {len(book_rows)}행 → {out_book}")
        print(f"[wan282] 진단 {len(diag_rows)}행 → {out_diag}")
        print(f"[wan282] 헤지 {len(hedge_rows)}행 → {out_hedge}")

    if not book_rows or not diag_rows:
        print("[wan282] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            book_rows, diag_rows, hedge_rows, book_csv=out_book, diag_csv=out_diag
        ),
        encoding="utf-8",
    )
    print(f"[wan282] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
