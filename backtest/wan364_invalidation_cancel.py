"""WAN-364: 「존이 깨진 봉」의 주문을 봉 시작으로 소급 취소하는 룩어헤드 — 크기와 반사실.

## 한 줄

백테스트는 **봉이 끝나야 알 수 있는 사실**(「이 봉에서 존이 깨졌다」)로 그 봉 처음으로
돌아가 대기 지정가를 취소한다. `ob.break_time`이 존을 깬 상위TF 봉의 `open_time`이라
(`strategy/order_blocks.py`) **그 봉 안에서 체결됐을 주문이 없던 일이 된다**.

## 왜 무작위가 아닌가 — 순서 논증

롱 기준으로 지정가는 존 안 어딘가에 있고(밴드가 존보다 아래면 진입 자체가 기각 — WAN-75
규칙 3) 손절선은 존 아랫변이다. 그러니 순서가 **강제된다**:

```
가격 하락 → 지정가 통과(체결) → 계속 하락 → 존 아랫변 돌파(무효화 = 손절)
```

체결이 무효화보다 **반드시 먼저**다. 무효화 시점엔 이미 포지션을 들고 있어 취소할 주문이
없다 — 즉 이 규칙은 **미래를 알 때만** 무언가를 지운다. 그리고 그렇게 지워지는 셋업은
가격이 존을 뚫고 내려간 셋업, 곧 **손절로 끝났을 셋업**이다.

⚠️ **단 「전부 −1R」은 아니다** — 그 봉 안에서 먼저 익절선까지 갔다가 나중에 존을 깨는
경로가 있다(실측: 되살아난 거래의 4분의 1이 익절이다). 그래서 §1이 **손익 분포**를 낸다.

## 두 층에서 지운다 (배선이 하나가 아니다)

1. **시그널 층** — 무효화 봉에서 난 탭은 `status="cancelled"`로 나오고
   `build_zone_limit_candidates`가 그것을 통째로 건너뛴다.
2. **시뮬레이터 층** — 그 전에 걸린 주문도 `step.time >= invalidation_time`이면 체결 판정
   **전에** 취소된다(`backtest/substep.py`).

인과 팔은 두 층을 함께 바꾼다 — 한쪽만 바꾸면 「무효화 봉의 탭은 여전히 안 보는데 취소만
늦춘」 잡종이 된다.

## 팔

* **A(현행 = 채택 북)** `invalidation_cancel="bar_open"` — 무효화 봉의 시작부터 취소.
* **B(인과)** `invalidation_cancel="bar_close"` — 무효화 봉의 탭도 후보로 받고, 취소는 그
  봉이 **닫힐 때** 발효한다. 봉 안의 체결은 살아남아 손절 규칙이 결과를 낸다.

📌 **B는 「취소를 아예 끄는」 팔이 아니다** — 봉이 닫힌 뒤에는 두 팔 모두 취소한다. 존이
죽은 걸 안 다음에도 주문을 걸어 두는 것은 인과가 아니라 다른 엔진이다.

## §2 — 크기를 먼저 싸게 (탐지 층만)

「탭이 있었는데 **같은 상위TF 봉에서** 존이 깨진」 시그널 수를 센다. 1분봉을 읽지 않으므로
칸당 1초 미만이다. ⚠️ 이것은 §1이 되살릴 거래 수의 **상한도 하한도 아니다**: 존폭 필터·
손절폭 가드·볼린저 기각이 아래에서 더 깎고(↓), 그 봉에 **이미 걸려 있던** 주문은 이 수에
안 들어간다(↑). 크기의 자릿수를 먼저 보는 자다.

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 4TF 한 지갑 · 못 박은 6년 창 · 재진입 ON(band) ·
cap_only 5배 · 존폭 필터 1.28 · 오프셋 2bp · 손절폭 가드 0.3% · 유동성 한도 채택값.
구간은 `oos_warm`(주, WAN-166) + `oos`(스트레스) + `full`·`is` 병기. **판단은 북에서
낸다**(WAN-341).

## 검산

* **(a) 팔 A ≡ 인자 없는 채택 북** — `wan336.verify_adopted_identity` 재사용.
* **(b) 팔 A의 「무효화 봉 체결」이 전 구간 0건** — 채택 팔에서는 정의상 있을 수 없다.
  0이 아니면 라벨이나 배선이 틀린 것이다. 팔 B는 반대로 **0이면** 팔이 안 걸린 것이다.
* **(c) 되살아난 거래의 체결 시각이 전부 무효화 봉 **안**이다 — 회귀 테스트가 동작으로
  고정한다(봉이 닫힌 뒤 체결이 하나라도 있으면 팔 B가 「취소 끔」으로 새어 나간 것이다).

재현:

```
uv run python -m backtest.wan364_invalidation_cancel --census-only     # §2만(싸다)
uv run python -m backtest.wan364_invalidation_cancel --arms A --jobs 4 # 배선 검산 먼저
uv run python -m backtest.wan364_invalidation_cancel --arms B --jobs 4 --append
uv run python -m backtest.wan364_invalidation_cancel --from-csv        # 요약만
```
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, net_r
from backtest.leverage_book import PlacedSetup
from backtest.models import ExitReason, Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, _segment_cells, run_cells
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import (
    ADOPTED_CELL_KWARGS,
    book_segments_for_payloads,
    verify_adopted_identity,
)
from backtest.zone_limit_backtest import InvalidationCancel

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan364_invalidation_cancel.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan364_invalidation_cancel_loo.csv"
CENSUS_CSV_PATH = REPORTS_DIR / "wan364_break_bar_taps.csv"
SUMMARY_PATH = REPORTS_DIR / "wan364_invalidation_cancel_summary.md"

#: 파괴선 — MDD가 이 선을 넘으면 「청산 0건」이라도 계좌는 사실상 끝났다(WAN-312 §4).
RUIN_MDD = 0.50

_FLOOR_NOTE = "6년 MDD는 2018·2020-03 폭락을 **포함하지 않는** 창이라 천장이 아니라 **바닥선**이다"


@dataclass(frozen=True)
class Arm:
    """취소 시점 축의 한 팔."""

    name: str
    cancel: InvalidationCancel
    label: str

    @property
    def is_adopted(self) -> bool:
        """이 팔이 **인자 없는 채택 북** 그 자체인가 — 검산 (a)를 걸 수 있는 유일한 팔."""
        return self.cancel == "bar_open"


ARMS: tuple[Arm, ...] = (
    Arm("A", "bar_open", "현행(소급 취소) = 인자 없는 backtest.run"),
    Arm("B", "bar_close", "인과 — 무효화 봉 안의 체결은 살리고 손절에 맡긴다"),
)
ARMS_BY_NAME: dict[str, Arm] = {a.name: a for a in ARMS}
ARM_ORDER: tuple[str, ...] = tuple(a.name for a in ARMS)
ADOPTED_ARM = "A"
CAUSAL_ARM = "B"

CSV_KEYS: tuple[str, ...] = ("arm", "segment")
LOO_CSV_KEYS: tuple[str, ...] = ("arm", "segment", "excluded")
CENSUS_KEYS: tuple[str, ...] = ("symbol", "timeframe")


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CensusRow(BaseModel):
    """§2 — 탐지 층에서만 센 「무효화 봉에서 난 탭」."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    num_bars: int
    num_zones: int
    active_taps: int
    """무효화 **전** 봉에서 난 탭 — 지금 후보가 되는 시그널."""
    break_bar_taps: int
    """무효화 **봉에서** 난 탭 — 채택 팔이 통째로 버리는 시그널(시그널 층)."""
    break_bar_share: float


class CancelRow(BaseModel):
    """한 (팔, 구간)의 북 집계 — 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    arm: str
    arm_label: str
    invalidation_cancel: str
    segment: str

    num_cells: int
    num_trades: int
    win_rate: float
    total_return: float
    """⚠️ 6년 복리라 실현 수익이 아니다(WAN-169/213) — 아래 거래당 net R과 나란히 읽는다."""
    mean_net_r: float
    """거래당 실현 net R = 복리와 무관한 「실력」(WAN-154 `mean_net_r`와 같은 자)."""
    net_r: float
    profit_factor: float | None

    max_drawdown: float
    return_over_mdd: float | None
    ruin: bool
    peak_concurrency: int
    max_concurrent_risk: float
    max_effective_concurrent_risk: float
    liquidation_events: int

    # 되살아난 거래 — 이 표의 주인공(완료기준 2 「늘어난 거래의 손익 분포」).
    revived_trades: int
    """체결이 무효화 봉 **안**에서 일어난 거래 수. 팔 A에서는 정의상 0(검산 (b))."""
    revived_share: float
    revived_win_rate: float
    revived_mean_net_r: float
    revived_net_r: float
    revived_stop_losses: int
    revived_take_profits: int
    revived_other_exits: int
    """손절·익절이 아닌 청산(데이터끝·유효기간 등) — 0이 아니면 그 자체가 관찰거리다."""
    reentry_trades: int


class CancelLooRow(BaseModel):
    """종목 하나를 뺀 **지갑 재배치**(라벨 필터가 아니다 — WAN-316 스코프 패턴)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    excluded: str
    num_trades: int
    total_return: float
    max_drawdown: float
    mean_net_r: float
    revived_trades: int


# --------------------------------------------------------------------------- #
# §2 — 탐지 층 인구조사 (싸다)
# --------------------------------------------------------------------------- #


def census_cell(symbol: str, timeframe: str, *, start_ms: int, end_ms: int) -> CensusRow | None:
    """한 칸의 「무효화 봉에서 난 탭」 수 — 1분봉을 읽지 않는다.

    `status="cancelled"`는 **무효화 봉에서 난 탭**이라는 뜻이다(그 뒤의 탭은 시그널로
    나오지도 않는다 — `strategy/order_blocks.py`의 `in_window` 가드). 그래서 이 한 열을
    세는 것만으로 시그널 층이 무엇을 버리는지가 나온다.
    """
    market = harness.load_market_data(
        harness.normalize_symbol(symbol),
        timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        need_1m=False,
        funding=False,
    )
    if market.empty:
        return None
    result = harness.detect_order_blocks(market)
    active = sum(1 for s in result.retap_signals if s.status == "active")
    broken = sum(1 for s in result.retap_signals if s.status == "cancelled")
    total = active + broken
    return CensusRow(
        symbol=harness.normalize_symbol(symbol),
        timeframe=timeframe,
        num_bars=len(market.htf_df),
        num_zones=len(result.order_blocks),
        active_taps=active,
        break_bar_taps=broken,
        break_bar_share=broken / total if total else 0.0,
    )


def run_census(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    log: bool = True,
) -> list[CensusRow]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    rows: list[CensusRow] = []
    for symbol in symbols:
        for timeframe in timeframes:
            row = census_cell(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
            if row is None:
                if log:
                    print(f"[wan364] {symbol} {timeframe}: 데이터 없음 — 건너뜀", flush=True)
                continue
            rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# §1 — 북 두 팔
# --------------------------------------------------------------------------- #


def revived_rulers(pairs: Sequence[tuple[Trade, PlacedSetup]]) -> dict[str, float]:
    """되살아난 거래(무효화 봉 안 체결)의 손익 분포 — 완료기준 2의 핵심 열들."""
    revived = [(t, p) for t, p in pairs if p.entry_after_invalidation]
    rs = [net_r(t, p) for t, p in revived]
    wins = sum(1 for t, _p in revived if t.realized_pnl > 0)
    # 청산 사유는 **마지막 체결**의 것이다(부분 청산이 있으면 `exits`가 여럿).
    reasons = [t.exits[-1].reason for t, _p in revived if t.exits]
    stops = sum(1 for r in reasons if r is ExitReason.STOP_LOSS)
    tps = sum(1 for r in reasons if r is ExitReason.TAKE_PROFIT)
    return {
        "revived_trades": float(len(revived)),
        "revived_win_rate": wins / len(revived) if revived else 0.0,
        "revived_mean_net_r": sum(rs) / len(rs) if rs else 0.0,
        "revived_net_r": sum(rs),
        "revived_stop_losses": float(stops),
        "revived_take_profits": float(tps),
        "revived_other_exits": float(len(revived) - stops - tps),
    }


def candidate_total(payloads: Sequence[CellPayload], segment: str) -> int:
    """이 구간에 북이 받는 후보 총수 — 검산 (b)의 분모."""
    return sum(
        len(cell.candidates) for cell in _segment_cells(payloads, segment, "", include_reentry=True)
    )


def candidate_revived(payloads: Sequence[CellPayload], segment: str) -> int:
    """후보 층(시퀀싱 전) 카운터 — 북이 실제로 받는 그 후보 집합에서 센다."""
    return sum(
        1
        for cell in _segment_cells(payloads, segment, "", include_reentry=True)
        for cand in cell.candidates
        if cand.entry_after_invalidation
    )


def _to_row(*, arm: Arm, segment: BookSegment) -> CancelRow:
    row = segment.row
    pairs = segment.trades_with_placements()
    rs = [net_r(t, p) for t, p in pairs]
    rev = revived_rulers(pairs)
    return CancelRow(
        arm=arm.name,
        arm_label=arm.label,
        invalidation_cancel=arm.cancel,
        segment=segment.segment,
        num_cells=row.num_cells,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        mean_net_r=sum(rs) / len(rs) if rs else 0.0,
        net_r=sum(rs),
        profit_factor=segment.result.metrics.profit_factor,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd,
        ruin=row.max_drawdown >= RUIN_MDD,
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=row.max_concurrent_risk,
        max_effective_concurrent_risk=row.max_effective_concurrent_risk,
        liquidation_events=row.liquidation_events,
        revived_trades=int(rev["revived_trades"]),
        revived_share=rev["revived_trades"] / row.num_trades if row.num_trades else 0.0,
        revived_win_rate=rev["revived_win_rate"],
        revived_mean_net_r=rev["revived_mean_net_r"],
        revived_net_r=rev["revived_net_r"],
        revived_stop_losses=int(rev["revived_stop_losses"]),
        revived_take_profits=int(rev["revived_take_profits"]),
        revived_other_exits=int(rev["revived_other_exits"]),
        reentry_trades=sum(1 for _t, p in pairs if p.is_reentry),
    )


def _loo_rows(
    *,
    arm: Arm,
    payloads: Sequence[CellPayload],
    symbols: Sequence[str],
    start_ms: int,
    end_ms: int,
) -> list[CancelLooRow]:
    """종목을 하나씩 뺀 **지갑 재배치** — 후보는 이미 있으니 사실상 공짜다."""
    present = {p.symbol for p in payloads}
    unmatched = [s for s in symbols if s not in present]
    if present and unmatched:
        raise AssertionError(
            f"leave-one-out이 아무 칸도 빼지 못했습니다: {unmatched} — 심볼 표기가 "
            f"payload({sorted(present)[0]!r} 형식)와 어긋납니다."
        )
    rows: list[CancelLooRow] = []
    for excluded in ("-", *symbols):
        scoped = [p for p in payloads if p.symbol != excluded]
        if not scoped:
            continue
        for seg in book_segments_for_payloads(
            scoped, start_ms=start_ms, end_ms=end_ms, segments=(PRIMARY_OOS,)
        ):
            pairs = seg.trades_with_placements()
            rs = [net_r(t, p) for t, p in pairs]
            rows.append(
                CancelLooRow(
                    arm=arm.name,
                    segment=seg.segment,
                    excluded=excluded,
                    num_trades=seg.row.num_trades,
                    total_return=seg.row.total_return,
                    max_drawdown=seg.row.max_drawdown,
                    mean_net_r=sum(rs) / len(rs) if rs else 0.0,
                    revived_trades=sum(1 for _t, p in pairs if p.entry_after_invalidation),
                )
            )
    return rows


def run_arm(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arm: Arm,
    *,
    start: str,
    end: str,
    jobs: int,
    segments: Sequence[str] = SEGMENT_ORDER,
    log: bool = True,
) -> tuple[list[CancelRow], list[CancelLooRow], float | None]:
    """한 팔의 후보를 **한 번** 만들고 구간 행·종목 LOO를 낸다."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        # ⚠️ 채택 팔에서만 `engine_check`를 켠다 — 그 검산은 격리 성과가 `harness.run_once`
        # (취소 축이 없는 per-cell)와 비트 일치하는지 보는 것이라, 축을 켠 팔에서는
        # **당연히** 어긋난다(WAN-336/346 관행 그대로).
        engine_check=arm.is_adopted,
        invalidation_cancel=arm.cancel,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )
    identity: float | None = None
    if arm.is_adopted:
        identity = verify_adopted_identity(payloads, start_ms=start_ms, end_ms=end_ms)
        if log:
            print(f"[wan364] 검산(a) 채택 경로 최대차: {identity:.2e}", flush=True)

    book = book_segments_for_payloads(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments)
    rows = [_to_row(arm=arm, segment=seg) for seg in book]
    # 검산 (b) — 후보 층과 거래 층이 같은 방향인지. 팔 A는 둘 다 0, 팔 B는 둘 다 >0이어야 한다.
    # ⚠️ 후보가 아예 없는 구간(데이터 없음·창 축소)에는 걸지 않는다 — 「0이라 실패」가
    # 「돌릴 게 없었다」와 같은 모양이면 진짜 배선 실패가 그 잡음에 묻힌다.
    for row in rows:
        total = candidate_total(payloads, row.segment)
        cands = candidate_revived(payloads, row.segment)
        if arm.is_adopted and (cands or row.revived_trades):
            raise AssertionError(
                f"검산(b) 실패 — 채택 팔 {row.segment}에 무효화 봉 체결이 있습니다"
                f"(후보 {cands} · 거래 {row.revived_trades}). 정의상 있을 수 없습니다."
            )
        if not arm.is_adopted and total and not cands:
            raise AssertionError(
                f"검산(b) 실패 — 인과 팔 {row.segment}의 후보 {total}건 중 무효화 봉 "
                "체결이 0건입니다. 팔이 라벨만 붙고 실제로 안 걸렸습니다(WAN-345 부류)."
            )
    loo = _loo_rows(
        arm=arm,
        payloads=payloads,
        symbols=[harness.normalize_symbol(s) for s in symbols],
        start_ms=start_ms,
        end_ms=end_ms,
    )
    return rows, loo, identity


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    arms: Sequence[str] = ARM_ORDER,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    on_arm: Callable[[list[CancelRow], list[CancelLooRow]], None] | None = None,
    log: bool = True,
) -> tuple[list[CancelRow], list[CancelLooRow]]:
    """팔마다 4TF 지갑을 한 실행으로 돈다.

    📌 팔마다 즉시 적재한다(`on_arm`) — 팔은 각자 독립 지갑이라 중간에 끊겨도 끝난 팔은
    보존된다. **끊길 수 없는 것은 한 팔 안의 4TF뿐이다**(북은 이어붙일 수 없다 — WAN-316).
    """
    rows: list[CancelRow] = []
    loo: list[CancelLooRow] = []
    for name in arms:
        arm = ARMS_BY_NAME[name]
        t0 = time.time()
        arm_rows, arm_loo, _identity = run_arm(
            symbols,
            timeframes,
            arm,
            start=start,
            end=end,
            jobs=jobs,
            segments=segments,
            log=log,
        )
        rows.extend(arm_rows)
        loo.extend(arm_loo)
        if on_arm is not None:
            on_arm(arm_rows, arm_loo)
        if log:
            print(
                f"[wan364] {arm.name}({arm.label}): {len(arm_rows)}행 ({time.time() - t0:.0f}s)",
                flush=True,
            )
    return rows, loo


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[CancelRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def loo_to_frame(rows: Sequence[CancelLooRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def census_to_frame(rows: Sequence[CensusRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _missing(value: object) -> bool:
    """`None`과 **NaN을 함께** 결측으로 본다 — CSV 왕복이 `None`을 NaN으로 바꾼다."""
    return value is None or (isinstance(value, float) and pd.isna(value))


def _pct(value: object) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:.2f}%"  # type: ignore[arg-type]


def _pp(value: object) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:+.2f}%p"  # type: ignore[arg-type]


def _num(value: object, digits: int = 4) -> str:
    return "—" if _missing(value) else f"{float(value):.{digits}f}"  # type: ignore[arg-type]


def _pick(frame: pd.DataFrame, arm: str, segment: str) -> pd.Series | None:
    hit = frame[(frame["arm"] == arm) & (frame["segment"] == segment)]
    return None if hit.empty else hit.iloc[0]


def _verdict(frame: pd.DataFrame) -> str:
    """완료기준 3 — **한 문장 판정**: 이 룩어헤드가 채택 성과를 얼마나 부풀렸는가."""
    a = _pick(frame, ADOPTED_ARM, PRIMARY_OOS)
    b = _pick(frame, CAUSAL_ARM, PRIMARY_OOS)
    if a is None or b is None:
        return (
            "⚠️ **판정 불가** — 팔 A와 팔 B의 주 구간 행이 둘 다 있어야 대조가 성립한다"
            f"(지금 있는 팔: {', '.join(sorted(frame['arm'].unique()))})."
        )
    grew = int(b["num_trades"]) - int(a["num_trades"])
    d_mean = float(b["mean_net_r"]) - float(a["mean_net_r"])
    direction = "부풀렸다" if d_mean < 0 else "오히려 낮췄다"
    flipped = (
        " 🚨 **크기가 준 것이 아니라 부호가 뒤집힌다** — 채택 북이 벌던 것이 "
        "잃는 것이 된다(WAN-346이 보수 축 둘을 쌓고도 「크기는 절반, 부호는 남는다」였던 "
        "것과 **다른 부류**다)."
        if float(a["mean_net_r"]) > 0 > float(b["mean_net_r"])
        else ""
    )
    ruin_bit = (
        " 🚨 **인과 팔은 파괴선(MDD 50%)을 넘는다 — 청산 트리거가 0건이어도 계좌는 사실상 "
        "끝났다**(WAN-312 §4: 사이징이 자본의 %라 연쇄 손실로는 청산 조건이 구조적으로 안 "
        "걸린다 — 그래서 이 표에서 「청산 0건」은 아무것도 보증하지 않는다)."
        if bool(b["ruin"])
        else ""
    )
    return (
        f"📌 **소급 취소를 인과로 바꾸면(`{PRIMARY_OOS}`) 거래가 "
        f"{int(a['num_trades'])} → {int(b['num_trades'])}건({grew:+}, "
        f"{grew / int(a['num_trades']) * 100:+.1f}%)으로 늘고 "
        f"**거래당 net R이 {_num(a['mean_net_r'])} → {_num(b['mean_net_r'])}"
        f"({d_mean:+.4f})**, 승률 {_pct(a['win_rate'])} → {_pct(b['win_rate'])}"
        f"({_pp(float(b['win_rate']) - float(a['win_rate']))}), "
        f"MDD {_pct(a['max_drawdown'])} → {_pct(b['max_drawdown'])}"
        f"({_pp(float(b['max_drawdown']) - float(a['max_drawdown']))}), "
        f"청산 {int(a['liquidation_events'])} → {int(b['liquidation_events'])}건이다 — "
        f"즉 이 룩어헤드는 채택 성과를 거래당 {abs(d_mean):.4f}R만큼 {direction}.**"
        + flipped
        + ruin_bit
    )


def _revived_note(frame: pd.DataFrame) -> str:
    """되살아난 거래의 손익 분포 — 「전부 −1R일 것」이라는 사전 예상의 검정."""
    b = _pick(frame, CAUSAL_ARM, PRIMARY_OOS)
    if b is None:
        return ""
    n = int(b["revived_trades"])
    if n == 0:
        return "⚠️ 인과 팔의 되살아난 거래가 0건이다 — 팔이 실제로 안 걸렸는지 확인할 것."
    tps = int(b["revived_take_profits"])
    stops = int(b["revived_stop_losses"])
    other = int(b["revived_other_exits"])
    return (
        f"📌 **되살아난 거래 {n}건의 정체**: 손절 {stops}건 · 익절 {tps}건"
        f"({tps / n * 100:.1f}%) · 그 외 {other}건 · 승률 {_pct(b['revived_win_rate'])} · "
        f"거래당 net R {_num(b['revived_mean_net_r'])} · 합 {_num(b['revived_net_r'], 1)}R. "
        "🚨 **「전부 −1R」이 아니다** — 그 봉 안에서 먼저 익절선까지 갔다가 나중에 존을 깨는 "
        "경로가 실제로 있다. 다만 손절이 압도적이라 방향은 이슈의 예상대로다."
    )


def _loo_note(loo: pd.DataFrame) -> str:
    """편중인가 — 종목을 하나씩 빼고 **지갑을 재배치해도** 부호가 유지되나."""
    causal = loo[loo["arm"] == CAUSAL_ARM]
    if causal.empty:
        return ""
    positive = int((causal["mean_net_r"] > 0).sum())
    lo, hi = float(causal["mean_net_r"].min()), float(causal["mean_net_r"].max())
    if positive:
        return (
            f"⚠️ 인과 팔의 거래당 net R이 {positive}/{len(causal)} 지갑에서 양수다 — "
            "부호가 종목에 갈린다는 뜻이니 판정을 그만큼 약하게 읽을 것."
        )
    return (
        f"📌 **종목 편중이 아니다** — 인과 팔은 {len(causal)}개 지갑(기준 + 종목별 제외) "
        f"**전부** 거래당 net R이 음수다({lo:.4f} ~ {hi:.4f}). ETH·SOL 편중 계열"
        "(WAN-111/119/124/151)이 여기서는 성립하지 않는다."
    )


def _census_note(census: pd.DataFrame) -> str:
    if census.empty:
        return "⚠️ §2 인구조사 CSV가 없다 — `--census-only`로 먼저 낼 것."
    lines = [
        "| TF | 칸 | 무효화 **전** 탭 | 무효화 **봉** 탭 | 비중 |",
        "| -- | --: | --: | --: | --: |",
    ]
    for tf, group in census.groupby("timeframe", sort=False):
        active = int(group["active_taps"].sum())
        broken = int(group["break_bar_taps"].sum())
        total = active + broken
        lines.append(
            f"| {tf} | {len(group)} | {active:,} | **{broken:,}** | {broken / total * 100:.2f}% |"
            if total
            else f"| {tf} | {len(group)} | 0 | 0 | — |"
        )
    return "\n".join(lines)


def build_summary(frame: pd.DataFrame, loo: pd.DataFrame, census: pd.DataFrame) -> str:
    """요약 md — 판정 한 문장 · §2 인구조사 · §1 두 팔 표 · LOO."""
    parts: list[str] = [
        "# WAN-364 — 「존이 깨진 봉」 소급 취소의 크기와 반사실",
        "",
        "`backtest/wan364_invalidation_cancel.py` 자동 생성. 좌표는 채택 그대로",
        "(12종목 × 4TF 한 지갑 × 못 박은 6년 · 재진입 ON(band) · cap_only 5배 · 핀 없음).",
        "",
        "## 판정 (완료기준 3)",
        "",
        _verdict(frame) if not frame.empty else "⚠️ §1 격자가 아직 없다.",
        "",
        _revived_note(frame) if not frame.empty else "",
        "",
        "## §2 — 탐지 층 인구조사: 시그널이 먼저 얼마나 버려지나 (완료기준 1)",
        "",
        "「무효화 **봉에서** 난 탭」은 채택 팔이 후보로 만들지도 않는다.",
        "⚠️ 이 수는 §1이 되살릴 거래 수의 상한도 하한도 아니다 — 아래 층(존폭 필터·손절폭",
        "가드·볼린저 기각)이 더 깎고(↓), 그 봉에 **이미 걸려 있던** 주문은 여기 안 들어간다(↑).",
        "",
        _census_note(census),
        "",
    ]
    if not census.empty:
        worst = census.sort_values("break_bar_share", ascending=False).head(5)
        parts += [
            "종목·TF 상위 5칸(비중 순):",
            "",
            "| 종목 | TF | 무효화 전 | 무효화 봉 | 비중 |",
            "| -- | -- | --: | --: | --: |",
        ]
        parts += [
            f"| {r.symbol} | {r.timeframe} | {int(r.active_taps):,} | "
            f"{int(r.break_bar_taps):,} | {r.break_bar_share * 100:.2f}% |"
            for r in worst.itertuples()
        ]
        parts.append("")

    parts += ["## §1 — 북 두 팔 (완료기준 2)", ""]
    if frame.empty:
        parts.append("⚠️ 아직 격자가 없다.")
    else:
        parts += [
            "| 팔 | 구간 | 거래 | 승률 | 총수익 | 거래당 netR | MDD | 수익/MDD | "
            "최대동시리스크 | 실효 | 청산 | 되살아난 거래 |",
            "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
        ]
        seg_rank = {name: i for i, name in enumerate(SEGMENT_ORDER)}
        ordered = frame.assign(_r=frame["segment"].map(seg_rank)).sort_values(["arm", "_r"])
        for r in ordered.itertuples():
            parts.append(
                f"| {r.arm} | {r.segment} | {int(r.num_trades):,} | {_pct(r.win_rate)} | "
                f"{_pct(r.total_return)} | {_num(r.mean_net_r)} | {_pct(r.max_drawdown)} | "
                f"{_num(r.return_over_mdd, 2)} | {_pct(r.max_concurrent_risk)} | "
                f"{_pct(r.max_effective_concurrent_risk)} | {int(r.liquidation_events)} | "
                f"{int(r.revived_trades):,} |"
            )
        parts += [
            "",
            "🚨 **총수익 %는 헤드라인이 아니다** — 6년 · 수천 거래 복리라 실현 수익이 아니고"
            "(WAN-169/213), 이 표의 자는 **거래당 net R**과 **MDD**다. "
            f"{_FLOOR_NOTE}.",
            "",
        ]

    if not loo.empty:
        parts += [
            f"## 종목 leave-one-out (`{PRIMARY_OOS}` · 지갑 재배치)",
            "",
            "| 팔 | 뺀 종목 | 거래 | 총수익 | MDD | 거래당 netR | 되살아난 거래 |",
            "| -- | -- | --: | --: | --: | --: | --: |",
        ]
        parts += [
            f"| {r.arm} | {r.excluded} | {int(r.num_trades):,} | {_pct(r.total_return)} | "
            f"{_pct(r.max_drawdown)} | {_num(r.mean_net_r)} | {int(r.revived_trades):,} |"
            for r in loo.itertuples()
        ]
        parts += ["", _loo_note(loo), ""]

    parts += [
        "## 읽는 법 · 경고",
        "",
        "* 🚨 **이 표는 기본값 전환 제안이 아니다** — 취소 끄기는 옵트인이고, 채택 팔은",
        "  손대지 않았다. 기본값을 바꾸는 것은 **재-베이스라인 = 사용자 결정**이고 그 위에",
        "  쌓인 모든 표가 「그때는 그랬다」로 얼어붙는다(WAN-132/149/159급 파급).",
        "* ⚠️ **성과가 나빠지는 것이 이 표의 예상이자 요점이다** — 되살아나는 거래가 대부분",
        "  손절이기 때문이다. 그래도 **그게 맞는 숫자**다.",
        "* ⚠️ 전부 `baseline`(닿으면 체결) 렌즈 위 값이고 큐 우선순위는 다른 축이다",
        "  (WAN-98 Canceled). 진입한 그 1분 안의 **익절** 축(WAN-336/348/359)은 또 다른 축이다 —",
        "  이건 **손절** 축이다.",
        "* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 축은 *진입 규칙이",
        "  무작위와 구분되는가*가 아니라 *이미 잰 숫자가 얼마나 낙관인가*를 묻는다. 게다가 이",
        "  결함은 널(대조군) 양쪽에 똑같이 걸리므로 그 판정을 뒤집을 방향이 아니다.",
        "* ⚠️ **판단은 북에서 낸다**(WAN-341) — per-cell 격리 성과는 이 표에 싣지 않는다.",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-364 무효화 취소 시점 격자")
    parser.add_argument("--symbols", default=None, help="쉼표 구분(기본: 채택 12종목)")
    parser.add_argument("--tf", default=None, help="쉼표 구분(기본: 채택 4TF)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--arms", default=None, help=f"쉼표 구분(기본: {','.join(ARM_ORDER)})")
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 쓴다")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 재생성")
    parser.add_argument(
        "--census-only", action="store_true", help="§2(탐지 층 인구조사)만 — 1분봉을 안 읽는다"
    )
    parser.add_argument("--skip-census", action="store_true", help="§2를 건너뛴다(이미 냈다면)")
    return parser.parse_args(argv)


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    if existing.empty:
        return fresh
    merged = pd.concat([existing, fresh], ignore_index=True)
    return merged.drop_duplicates(subset=list(keys), keep="last").reset_index(drop=True)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    symbols = (
        [s.strip() for s in args.symbols.split(",")] if args.symbols else harness.DEFAULT_SYMBOLS
    )
    timeframes = [t.strip() for t in args.tf.split(",")] if args.tf else harness.DEFAULT_TIMEFRAMES

    if args.from_csv:
        frame, loo, census = _read(CSV_PATH), _read(LOO_CSV_PATH), _read(CENSUS_CSV_PATH)
        if frame.empty and census.empty:
            print(f"[wan364] {CSV_PATH}도 {CENSUS_CSV_PATH}도 없습니다 — 먼저 돌리세요.")
            return 1
        SUMMARY_PATH.write_text(build_summary(frame, loo, census), encoding="utf-8")
        print(f"[wan364] 요약 재생성: {SUMMARY_PATH}")
        return 0

    census_frame = _read(CENSUS_CSV_PATH)
    if not args.skip_census:
        t0 = time.time()
        fresh = census_to_frame(run_census(symbols, timeframes, start=args.start, end=args.end))
        if not fresh.empty:
            census_frame = _merge(census_frame, fresh, CENSUS_KEYS)
            census_frame.to_csv(CENSUS_CSV_PATH, index=False)
        print(
            f"[wan364] §2 인구조사: {CENSUS_CSV_PATH} ({len(census_frame)}행, "
            f"{time.time() - t0:.0f}s)",
            flush=True,
        )

    if args.census_only:
        SUMMARY_PATH.write_text(
            build_summary(_read(CSV_PATH), _read(LOO_CSV_PATH), census_frame), encoding="utf-8"
        )
        print(f"[wan364] 요약: {SUMMARY_PATH}")
        return 0

    arms = [a.strip() for a in args.arms.split(",")] if args.arms else list(ARM_ORDER)
    unknown = [a for a in arms if a not in ARMS_BY_NAME]
    if unknown:
        print(f"[wan364] 모르는 팔: {unknown} (가능: {', '.join(ARM_ORDER)})")
        return 2

    base_rows = _read(CSV_PATH) if args.append else pd.DataFrame()
    base_loo = _read(LOO_CSV_PATH) if args.append else pd.DataFrame()

    def persist(rows: list[CancelRow], loo: list[CancelLooRow]) -> None:
        nonlocal base_rows, base_loo
        base_rows = _merge(base_rows, rows_to_frame(rows), CSV_KEYS)
        base_loo = _merge(base_loo, loo_to_frame(loo), LOO_CSV_KEYS)
        base_rows.to_csv(CSV_PATH, index=False)
        base_loo.to_csv(LOO_CSV_PATH, index=False)
        print(f"[wan364] 적재: {CSV_PATH} ({len(base_rows)}행)", flush=True)

    run_report(
        symbols,
        timeframes,
        arms=arms,
        start=args.start,
        end=args.end,
        jobs=args.jobs,
        on_arm=persist,
    )
    SUMMARY_PATH.write_text(build_summary(base_rows, base_loo, census_frame), encoding="utf-8")
    print(f"[wan364] 요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
