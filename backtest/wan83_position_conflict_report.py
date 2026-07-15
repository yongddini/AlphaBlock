"""WAN-83: 포지션 보유 중 놓친 첫 탭의 "무조건 진입권" 소각 계측.

배경은 이슈 WAN-83 본문 참고(재작성판). 채택 기본값(`ConfluenceParams()` = 롱 온리,
지정가, `first_tap_free`)에서 존 확정 후 **첫 탭**(`tap_index=0`)은 RSI 무관 무조건
진입인데, 이 면제는 `tap_index`만 보고 판정되고 **포지션이 이미 있으면 그 탭은 그냥
버려진다**(백테스트 엔진은 플랫일 때만 진입). 자본이 풀린 뒤 같은 존이 다시 탭되면
신호는 다시 나오지만 `tap_index>=1`이라 RSI 게이트(롱 `RSI<=30`)를 통과해야 한다 —
막히면 그 존은 "무조건 진입"이 약속한 기회를 영영 잃는다.

이 모듈은 **계측만 한다** — 진입 로직은 바꾸지 않는다(이슈 범위). 다음을 센다:

1. `tap0_filled` — 포지션 제약이 없다고 가정했을 때(각 탭을 독립적으로 지정가
   시뮬레이션) 체결됐을 첫 탭(`tap_index=0`) 수.
2. `dropped_by_position` — 그중 동시 1포지션 제약(WAN-23) 때문에 실제로는 스킵된 수.
3. `dropped_with_retap` — 스킵된 첫 탭 중, 존이 무효화되기 전에 **다시 탭된**(재탭
   이벤트가 실제로 존재하는) 수(복구 기회가 왔던 건).
4. `dropped_retap_recovered` — 그 재탭 중 **적어도 하나가 결국 체결**된 수(RSI 게이트를
   통과해 진입 시점만 늦어졌을 뿐 실질 손실은 없음).
5. `dropped_retap_blocked` — 재탭은 있었지만 **하나도 체결되지 못한** 수 — RSI 게이트
   (또는 그사이 만료·무효화)에 막혀 실제로 진입을 잃은 첫 탭. **이 숫자가 이 이슈의
   실제 피해 규모다.**
6. `dropped_no_retap` — 스킵된 뒤 무효화될 때까지 한 번도 다시 탭되지 않은 수.

## 두 범위 (WAN-83 코멘트, 2026-07-14)

포지션 1개 제약의 적용 범위가 백테스트와 라이브에서 다르다:

- **series(A)** — 백테스트 현행 조건. (심볼, TF) 시리즈 내부에서만 포지션을 공유한다
  (`BacktestEngine`/`_sequence_and_cost`가 시리즈 하나만 본다).
- **global(B)** — 라이브 페이퍼 러너 조건. 3심볼 × 4TF 전체가 포지션 1개 예산을
  **공유**한다고 가정하고 모든 시리즈의 체결 후보를 시간순으로 합쳐 시퀀싱한다.

`global`이 `series`의 합보다 크게 나오면, 백테스트가 이 소각을 과소평가하고 있다는 뜻이다.

## 방법

각 (심볼, TF, 구간)에서 `backtest.zone_limit_backtest.build_zone_limit_candidates`로
포지션 제약 없이 독립 시뮬레이션한 체결 후보 목록을 얻는다(이 함수 자체가 이미 각 탭을
포지션 상태와 무관하게 개별 평가한다 — 포지션 제약은 그 뒤 시퀀싱 단계에서만 걸린다).
`_Candidate`/`OrderBlockSignal`에 실린 `tap_index`·`zone_key`(WAN-83이 진단용으로 추가한
필드, 병합 존 재계산에도 안정적인 존 식별자)로 "같은 존의 몇 번째 탭인가"를 추적하고,
`_sequence_and_cost`와 동일한 시간순 배치 규칙(진입 시각 < 직전 청산 시각이면 스킵)을
재현해 어떤 탭이 포지션 충돌로 빠지는지 가려낸다.

## 재현

```
uv run python -m backtest.wan83_position_conflict_report
```

기본은 3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × (IS/OOS)를 채택 기본값으로 1회씩만
돈다(파라미터 스윕 없음 — WAN-96/99와 달리 이 리포트는 여러 조합을 비교하지 않는다).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    DEFAULT_YEARS,
    MarketData,
    Segment,
    detect_order_blocks,
    load_market_data,
    segments_for,
    slice_market,
)
from backtest.sweep import default_backtest_config
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.models import ConfluenceParams, OrderBlockDirection, OrderBlockSignal

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
#: WAN-97 결정과 같은 4TF(15m/1h/4h/1d) — 채택 판단 대상 전체를 계측한다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")

#: 채택 기본값 그대로(WAN-81 롱 온리·지정가·first_tap_free·고정 1.5R). 이 리포트는
#: 파라미터를 하나도 바꾸지 않는다 — 이슈가 묻는 건 "지금 기본값에서 얼마나 소각되는가"다.
PARAMS = ConfluenceParams()

SCOPE_SERIES = "series"
SCOPE_GLOBAL = "global"


@dataclass(frozen=True)
class _CellData:
    """한 (심볼, TF, 구간)의 미체결 무관 후보 + 재탭 이벤트."""

    symbol: str
    timeframe: str
    segment: str
    candidates: list[_Candidate]
    """포지션 제약 없이 독립 평가했을 때 체결된 후보 전체(모든 tap_index)."""
    retap_events: list[OrderBlockSignal]
    """존 무효화 전(`status="active"`)의 재탭(`tap_index>=1`) 이벤트(롱만, 체결 여부 무관)."""


@dataclass(frozen=True)
class ConflictCounts:
    tap0_filled: int
    dropped_by_position: int
    dropped_with_retap: int
    dropped_retap_recovered: int
    dropped_retap_blocked: int
    dropped_no_retap: int


class ConflictRow(BaseModel):
    """결과 표 한 행."""

    model_config = ConfigDict(frozen=True)

    scope: str
    symbol: str
    timeframe: str
    segment: str
    tap0_filled: int
    dropped_by_position: int
    dropped_with_retap: int
    dropped_retap_recovered: int
    dropped_retap_blocked: int
    dropped_no_retap: int


ROW_COLUMNS: tuple[str, ...] = tuple(ConflictRow.model_fields)

_COUNT_FIELDS: tuple[str, ...] = (
    "tap0_filled",
    "dropped_by_position",
    "dropped_with_retap",
    "dropped_retap_recovered",
    "dropped_retap_blocked",
    "dropped_no_retap",
)


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이(별도 `tabulate` 불요) 파이프 마크다운 표를 만든다."""
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


def build_cell(
    symbol: str, timeframe: str, market: MarketData, segment: Segment
) -> _CellData | None:
    """한 (심볼, TF, 구간)의 후보·재탭 이벤트를 계산한다. 데이터가 없으면 None."""
    window = slice_market(market, segment)
    if window.empty or window.df_1m.empty:
        return None
    ob_result = detect_order_blocks(window)
    cfg = default_backtest_config(timeframe)
    candidates, _stats = build_zone_limit_candidates(
        window.htf_df,
        window.df_1m,
        timeframe,
        params=PARAMS,
        cfg=cfg,
        order_block_result=ob_result,
    )
    retap_events = [
        s
        for s in ob_result.retap_signals
        if s.direction is OrderBlockDirection.BULLISH and s.tap_index >= 1 and s.status == "active"
    ]
    return _CellData(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment.name,
        candidates=candidates,
        retap_events=retap_events,
    )


def _survivor_ids(candidates: list[tuple[str, str, _Candidate]]) -> set[int]:
    """동시 1포지션 제약(`_sequence_and_cost`와 동일 규칙)을 적용해 살아남는 후보의
    `id()` 집합을 낸다. 여러 시리즈를 섞어 넘기면 포지션 예산을 전역으로 공유한
    시뮬레이션이 된다(WAN-83 (B) 전역 범위)."""
    ordered = sorted(candidates, key=lambda item: (item[2].entry_time, item[2].exit_time))
    busy_until = -1
    survivors: set[int] = set()
    for _symbol, _timeframe, cand in ordered:
        if cand.entry_time < busy_until:
            continue
        survivors.add(id(cand))
        busy_until = cand.exit_time
    return survivors


def classify(
    tagged_candidates: list[tuple[str, str, _Candidate]],
    retaps_by_series: dict[tuple[str, str], list[OrderBlockSignal]],
) -> ConflictCounts:
    """포지션 충돌로 스킵된 첫 탭을 재탭 여부·체결 여부로 분해한다.

    `tagged_candidates`가 한 시리즈만 담으면 series 범위, 여러 시리즈를 담으면 그
    전체가 포지션 1개를 공유하는 global 범위가 된다 — 호출부가 범위를 결정한다.
    """
    survivors = _survivor_ids(tagged_candidates)
    tap0 = [item for item in tagged_candidates if item[2].tap_index == 0]
    dropped = [item for item in tap0 if id(item[2]) not in survivors]

    with_retap = 0
    recovered = 0
    blocked = 0
    no_retap = 0
    for symbol, timeframe, cand in dropped:
        zone_key = cand.zone_key
        if zone_key is None:
            no_retap += 1  # 방어적: 기본 경로에서는 항상 채워지므로 실질적으로 발생하지 않는다.
            continue
        retaps = [
            s
            for s in retaps_by_series.get((symbol, timeframe), ())
            if s.zone_key == zone_key and s.trigger_time > cand.entry_time
        ]
        if not retaps:
            no_retap += 1
            continue
        with_retap += 1
        recovered_here = any(
            osymbol == symbol
            and otimeframe == timeframe
            and other.zone_key == zone_key
            and other.tap_index >= 1
            and other.entry_time > cand.entry_time
            for osymbol, otimeframe, other in tagged_candidates
        )
        if recovered_here:
            recovered += 1
        else:
            blocked += 1

    return ConflictCounts(
        tap0_filled=len(tap0),
        dropped_by_position=len(dropped),
        dropped_with_retap=with_retap,
        dropped_retap_recovered=recovered,
        dropped_retap_blocked=blocked,
        dropped_no_retap=no_retap,
    )


def _row(
    scope: str, symbol: str, timeframe: str, segment: str, counts: ConflictCounts
) -> ConflictRow:
    return ConflictRow(
        scope=scope,
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        tap0_filled=counts.tap0_filled,
        dropped_by_position=counts.dropped_by_position,
        dropped_with_retap=counts.dropped_with_retap,
        dropped_retap_recovered=counts.dropped_retap_recovered,
        dropped_retap_blocked=counts.dropped_retap_blocked,
        dropped_no_retap=counts.dropped_no_retap,
    )


def run_report(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    *,
    log: bool = True,
) -> list[ConflictRow]:
    segments = segments_for(oos=True)
    cells_by_segment: dict[str, list[_CellData]] = {seg.name: [] for seg in segments}

    for symbol in symbols:
        for timeframe in timeframes:
            market = load_market_data(symbol, timeframe, years=years, need_1m=True)
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan83] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
                continue
            for segment in segments:
                cell = build_cell(symbol, timeframe, market, segment)
                if cell is None:
                    continue
                cells_by_segment[segment.name].append(cell)
            if log:
                print(f"[wan83] {symbol} {timeframe}: 완료")

    rows: list[ConflictRow] = []
    for segment_name, cells in cells_by_segment.items():
        if segment_name == "full":
            continue  # 이슈가 요구하는 건 IS/OOS — full은 참고용으로 생략(중복 방지).
        retaps_by_series = {(c.symbol, c.timeframe): c.retap_events for c in cells}

        for cell in cells:
            tagged = [(cell.symbol, cell.timeframe, c) for c in cell.candidates]
            counts = classify(tagged, retaps_by_series)
            rows.append(_row(SCOPE_SERIES, cell.symbol, cell.timeframe, segment_name, counts))

        pooled = [(c.symbol, c.timeframe, cand) for c in cells for cand in c.candidates]
        global_counts = classify(pooled, retaps_by_series)
        rows.append(_row(SCOPE_GLOBAL, "ALL", "ALL", segment_name, global_counts))
    return rows


def rows_to_frame(rows: list[ConflictRow]) -> pd.DataFrame:
    records = [row.model_dump() for row in rows]
    return pd.DataFrame(records, columns=list(ROW_COLUMNS))


def write_summary(rows: list[ConflictRow], path: Path) -> None:
    frame = rows_to_frame(rows)
    series = frame[frame["scope"] == SCOPE_SERIES]
    global_rows = frame[frame["scope"] == SCOPE_GLOBAL]

    lines = ["# WAN-83: 포지션 충돌로 인한 첫 탭 RSI 면제권 소각 계측", ""]
    lines.append(
        "채택 기본값(`ConfluenceParams()` = 롱 온리·지정가·`first_tap_free`·고정 1.5R)에서, "
        "`tap_index=0`(무조건 진입)인데 포지션 보유로 스킵된 첫 탭이 존 무효화 전에 "
        "재탭됐는지, 재탭이 RSI 게이트를 통과했는지 분해한다. **이 이슈는 계측만 하며 "
        "진입 로직은 바꾸지 않는다.**"
    )
    lines.append("")
    lines.append("## series 범위 (현행 백테스트 — (심볼,TF) 시리즈 내부에서만 포지션 공유)")
    lines.append("")
    lines.append(_md_table(series.drop(columns=["scope"]).reset_index(drop=True)))
    lines.append("")

    total_series = series[list(_COUNT_FIELDS)].sum()
    lines.append(
        f"series 합계: tap0_filled={total_series['tap0_filled']}, "
        f"dropped_by_position={total_series['dropped_by_position']}, "
        f"dropped_with_retap={total_series['dropped_with_retap']}, "
        f"dropped_retap_recovered={total_series['dropped_retap_recovered']}, "
        f"**dropped_retap_blocked={total_series['dropped_retap_blocked']}**, "
        f"dropped_no_retap={total_series['dropped_no_retap']}"
    )
    lines.append("")
    lines.append("## global 범위 (라이브 조건 — 3심볼×4TF 전체가 포지션 1개를 공유)")
    lines.append("")
    lines.append(
        _md_table(global_rows.drop(columns=["scope", "symbol", "timeframe"]).reset_index(drop=True))
    )
    lines.append("")

    total_global = global_rows[list(_COUNT_FIELDS)].sum()
    lines.append(
        f"global 합계: tap0_filled={total_global['tap0_filled']}, "
        f"dropped_by_position={total_global['dropped_by_position']}, "
        f"dropped_with_retap={total_global['dropped_with_retap']}, "
        f"dropped_retap_recovered={total_global['dropped_retap_recovered']}, "
        f"**dropped_retap_blocked={total_global['dropped_retap_blocked']}**, "
        f"dropped_no_retap={total_global['dropped_no_retap']}"
    )
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument(
        "--out-csv", type=str, default="backtest/reports/wan83_position_conflict.csv"
    )
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan83_position_conflict_summary.md"
    )
    args = parser.parse_args()

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    timeframes = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())

    rows = run_report(symbols=symbols, timeframes=timeframes, years=args.years)
    frame = rows_to_frame(rows)
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_csv, index=False)
    write_summary(rows, Path(args.out_md))
    print(f"[wan83] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
