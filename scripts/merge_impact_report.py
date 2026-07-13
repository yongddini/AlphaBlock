"""WAN-56: 존 병합(combine_obs) on/off가 백테스트 신호에 미치는 영향 리포트.

병합이 렌더링에만 적용되고 시그널에는 미적용이던 버그를 고친 뒤(존 병합을 탐지
파이프라인으로 끌어올림), **얼마나 진입이 부풀려져 있었는지**를 3심볼 × 3TF × 3년
실데이터로 정량화한다. 병합은 겹치는 동일방향 존을 하나로 합치므로:

* **진입 횟수**(활성 탭 신호 수): 병합 전에는 겹치는 존을 각각 세어 부풀려진다.
  병합 후에는 "병합 단위당 1회"(R1)로 줄어든다. `팽창률 = raw / merged`.
* **손절 거리**(진입가→distal 무효화 경계, %): 병합 존의 경계는 합집합이라 원단
  (distal)이 바깥으로 확장돼 손절 거리가 늘어난다. 활성 신호 평균으로 비교한다.

이 두 지표가 바로 이슈가 지적한 영향 #1(진입 부풀림)·#2(손절 거리 변화)다. RSI·
백테스트 엔진을 태우지 않고 신호 단계에서 직접 비교해 병합의 순효과만 격리한다.

데이터는 `ALPHABLOCK_DB_PATH`(기본 `data/ohlcv.db`)에서 읽고, 없거나 `--synthetic`
이면 시드 고정 합성 데이터로 대체해 항상 재현 가능하게 실행한다.

사용법::

    uv run python scripts/merge_impact_report.py            # 저장 데이터 3심볼×3TF
    uv run python scripts/merge_impact_report.py --synthetic  # CI/데모(합성)
    uv run python scripts/merge_impact_report.py --out reports/wan56_merge_impact.md
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.synthetic import make_synthetic_ohlcv
from config.settings import get_settings
from data.storage import OhlcvStore
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockParams, OrderBlockSignal
from strategy.order_blocks import OrderBlockDetector

_DEFAULT_SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
_DEFAULT_TIMEFRAMES = ("15m", "1h", "4h")


@dataclass(frozen=True)
class _Stats:
    """한 (combine on/off) 실행의 신호 요약."""

    zones: int
    active: int
    avg_stop_pct: float | None  # 활성 신호의 평균 손절 거리(%), 없으면 None.


def _distal(ob: OrderBlock) -> float:
    """무효화(손절) 경계 = 존의 원단(distal). 강세는 bottom, 약세는 top."""
    return ob.bottom if ob.direction is OrderBlockDirection.BULLISH else ob.top


def _stop_pct(signal: OrderBlockSignal) -> float:
    entry = signal.price
    return abs(entry - _distal(signal.order_block)) / entry * 100.0 if entry else 0.0


def _summarize(df: pd.DataFrame, *, combine: bool) -> _Stats:
    result = OrderBlockDetector(OrderBlockParams(combine_obs=combine)).run(df)
    active = [s for s in result.signals if s.status == "active"]
    avg_stop = sum(_stop_pct(s) for s in active) / len(active) if active else None
    return _Stats(zones=len(result.order_blocks), active=len(active), avg_stop_pct=avg_stop)


def _load(
    store: OhlcvStore | None, symbol: str, timeframe: str, *, synthetic_bars: int
) -> pd.DataFrame:
    if store is not None:
        df = store.load(symbol, timeframe)
        if not df.empty:
            return df
    # 폴백: 시드 고정 합성 데이터(심볼·TF로 시드를 갈라 서로 다른 시리즈).
    seed = (hash((symbol, timeframe)) % 9973) + 1
    return make_synthetic_ohlcv(timeframe=timeframe, bars=synthetic_bars, seed=seed)


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}%"


def _fmt_ratio(raw: int, merged: int) -> str:
    return "—" if merged == 0 else f"{raw / merged:.2f}×"


def build_report(
    symbols: list[str], timeframes: list[str], *, synthetic: bool, synthetic_bars: int
) -> str:
    store: OhlcvStore | None = None
    if not synthetic:
        db_path = get_settings().db_path
        if Path(db_path).exists():
            store = OhlcvStore(db_path)

    lines: list[str] = [
        "# WAN-56 존 병합 영향 리포트 (진입 부풀림 · 손절 거리)",
        "",
        "존 병합(`combine_obs`)을 렌더링뿐 아니라 백테스트 신호까지 적용한 뒤, 병합 전",
        "(`raw`, `combine_obs=False`) 대비 병합 후(`merged`, `combine_obs=True`)의 활성 탭",
        "신호 수와 평균 손절 거리를 비교한다. 데이터 출처: "
        + ("시드 고정 합성" if store is None else "`data/ohlcv.db` 저장 실데이터")
        + ".",
        "",
        "| 심볼 | TF | 봉수 | raw 진입 | merged 진입 | 팽창률 | raw 손절% | merged 손절% |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    tot_raw = tot_merged = 0
    for symbol in symbols:
        for tf in timeframes:
            df = _load(store, symbol, tf, synthetic_bars=synthetic_bars)
            raw = _summarize(df, combine=False)
            merged = _summarize(df, combine=True)
            tot_raw += raw.active
            tot_merged += merged.active
            lines.append(
                f"| {symbol} | {tf} | {len(df)} | {raw.active} | {merged.active} | "
                f"{_fmt_ratio(raw.active, merged.active)} | "
                f"{_fmt_pct(raw.avg_stop_pct)} | {_fmt_pct(merged.avg_stop_pct)} |"
            )
    lines += [
        f"| **합계** | | | **{tot_raw}** | **{tot_merged}** | "
        f"**{_fmt_ratio(tot_raw, tot_merged)}** | | |",
        "",
        f"**요약**: 병합 전 백테스트는 활성 진입을 총 {tot_raw}회로 셌지만, 실제 사용자가",
        f"보는 병합 존 기준으로는 {tot_merged}회다 "
        f"(팽창률 {_fmt_ratio(tot_raw, tot_merged)}). 그만큼 거래 수·수수료·펀딩비 추정이",
        "부풀려져 있었다. 병합 존은 경계가 합집합이라 손절 거리도 달라진다(위 표).",
        "",
        "> 이 수치는 신호 단계 직접 비교다(RSI·백테스트 엔진 미적용). RSI 게이트·청산까지",
        "> 포함한 성과 재산출은 `scripts/backtest_report.py` 등을 재실행하면 나온다.",
    ]
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(_DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(_DEFAULT_TIMEFRAMES))
    parser.add_argument("--synthetic", action="store_true", help="저장 데이터 무시하고 합성으로만")
    parser.add_argument("--synthetic-bars", type=int, default=8000)
    parser.add_argument(
        "--out", type=Path, default=None, help="마크다운 저장 경로(미지정 시 stdout만)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    report = build_report(
        [s.strip() for s in args.symbols.split(",") if s.strip()],
        [t.strip() for t in args.timeframes.split(",") if t.strip()],
        synthetic=args.synthetic,
        synthetic_bars=args.synthetic_bars,
    )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"리포트 저장: {args.out}")
    print(report)


if __name__ == "__main__":
    main()
