"""A vs B 진입 방식 **본실험** 러너: 다심볼 × 다TF × 다년 실데이터 (WAN-46).

WAN-41(`backtest.ab_run`)의 A/B 파이프라인은 단일 심볼·TF·결정적 픽스처 수준이라
통계적 결론을 낼 수 없다. 이 모듈은 로컬 `data/ohlcv.db`의 실데이터로 **여러 심볼 ×
여러 상위TF × 다년 구간**을 한 번에 돌려, 심볼·TF별 + 합산 A/B 비교 CSV와 1분봉
커버리지 리포트를 함께 낸다. 진입 방식 기본값 전환 판단의 근거 데이터를 만든다.

## 공정 비교 창

각 (심볼, TF)에서 성과 집계 창은 **상위TF·1분봉·요청 구간이 모두 겹치는 구간**으로
한정한다. 상위TF 히스토리 전체는 오더블록 탐지·지표·RSI 시딩에 그대로 쓰되(워밍업),
`df_1m`을 이 교집합으로 잘라 `ab_run.build_ab_entries`에 넘긴다. A안은 1분봉 커버
창으로, B안은 1분봉이 있는 셋업으로 자연히 한정되므로 A·B가 같은 창에서 비교된다.

## 1분봉 결측 정책

1분봉이 커버하지 않는 상위TF 봉의 셋업은 B안 파이프라인에서 **평가 제외**된다
(`zone_limit_backtest` 참고 — 보수적 폴백이 아니라 제외). 얼마나 제외되는지 보이도록
커버리지 리포트에 창 내 1분봉 커버리지 비율을 기록한다.

## 재현성

`build_ab_entries`가 결정적이므로, 같은 `ohlcv.db` 스냅샷에서 이 러너를 다시 돌리면
동일한 CSV가 나온다. 단일 커맨드: ``uv run python -m backtest.ab_experiment``.
"""

from __future__ import annotations

import argparse
import csv
import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.ab_report import ABEntry, build_ab_report
from backtest.ab_run import add_cost_args, build_ab_entries, cost_config
from backtest.models import BacktestConfig
from backtest.sweep import timeframe_to_ms

#: 본실험 대상 심볼·상위TF·기간(년).
DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
DEFAULT_YEARS: float = 3.0

#: 출력 경로(재현 증적으로 커밋).
DEFAULT_DB_PATH = Path("data/ohlcv.db")
DEFAULT_REPORT_PATH = Path("backtest/reports/wan46_ab_experiment.csv")
DEFAULT_COVERAGE_PATH = Path("backtest/reports/wan46_coverage.csv")

_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)
_MINUTE_MS = 60_000


@dataclass(frozen=True)
class Coverage:
    """한 (심볼, TF)의 비교 창·1분봉 커버리지 진단."""

    symbol: str
    timeframe: str
    window_start_ms: int | None
    window_end_ms: int | None
    htf_bars: int
    one_min_bars: int
    expected_one_min: int
    note: str

    @property
    def coverage_ratio(self) -> float | None:
        """창 내 실제 1분봉 수 / 기대 1분봉 수. 창이 없으면 None."""
        if self.expected_one_min <= 0:
            return None
        return self.one_min_bars / self.expected_one_min


_COVERAGE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "window_start",
    "window_end",
    "htf_bars",
    "one_min_bars",
    "expected_one_min",
    "coverage_ratio",
    "missing_policy",
    "note",
)


def _iso(ms: int | None) -> str:
    if ms is None:
        return ""
    return str(pd.Timestamp(ms, unit="ms", tz="UTC").isoformat())


def _fmt_ratio(value: float | None) -> str:
    return "" if value is None else f"{round(value, 6)}"


def _window(
    htf_df: pd.DataFrame, df_1m: pd.DataFrame, timeframe: str, years: float
) -> tuple[int, int] | None:
    """상위TF·1분봉·요청 기간이 모두 겹치는 비교 창 `[start, end]`(ms).

    요청 기간은 가용 데이터의 최신 끝에서 `years`년 뒤로 잡는다. 겹치는 구간이 없으면
    None을 반환한다(그 (심볼, TF)는 건너뛴다).
    """
    if htf_df.empty or df_1m.empty:
        return None
    htf_min = int(htf_df["open_time"].min())
    htf_max = int(htf_df["open_time"].max()) + timeframe_to_ms(timeframe)
    m_min = int(df_1m["open_time"].min())
    m_max = int(df_1m["open_time"].max())
    req_start = m_max - int(years * _YEAR_MS)
    start = max(htf_min, m_min, req_start)
    end = min(htf_max, m_max)
    if start >= end:
        return None
    return start, end


def _clip_1m(df_1m: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    mask = (df_1m["open_time"] >= start) & (df_1m["open_time"] <= end)
    return df_1m.loc[mask].reset_index(drop=True)


def run_experiment(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    backtest_config: BacktestConfig | None = None,
) -> tuple[list[ABEntry], list[Coverage]]:
    """실데이터로 심볼 × TF A/B 실험을 돌려 리포트 엔트리와 커버리지를 만든다.

    로컬 저장소(`data.storage.OhlcvStore`)가 없거나 DB가 없으면 빈 결과를 반환한다.
    """
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return [], []
    if not db_path.exists():
        return [], []

    entries: list[ABEntry] = []
    coverages: list[Coverage] = []
    with OhlcvStore(db_path) as store:
        for symbol in symbols:
            one_min_full = store.load(symbol, "1m")
            for timeframe in timeframes:
                htf_df = store.load(symbol, timeframe)
                cov, clipped = _evaluate_pair(symbol, timeframe, htf_df, one_min_full, years)
                coverages.append(cov)
                if clipped is None:
                    continue
                entries.extend(
                    build_ab_entries(
                        htf_df,
                        clipped,
                        symbol=symbol,
                        timeframe=timeframe,
                        backtest_config=backtest_config,
                    )
                )
    return entries, coverages


def _evaluate_pair(
    symbol: str,
    timeframe: str,
    htf_df: pd.DataFrame,
    one_min_full: pd.DataFrame,
    years: float,
) -> tuple[Coverage, pd.DataFrame | None]:
    """한 (심볼, TF)의 커버리지를 계산하고 창으로 자른 1분봉을 반환(없으면 None)."""
    if htf_df.empty or one_min_full.empty:
        note = "no HTF data" if htf_df.empty else "no 1m data"
        return _empty_coverage(symbol, timeframe, note), None
    window = _window(htf_df, one_min_full, timeframe, years)
    if window is None:
        return _empty_coverage(symbol, timeframe, "no overlapping window"), None
    start, end = window
    clipped = _clip_1m(one_min_full, start, end)
    htf_in_window = int(((htf_df["open_time"] >= start) & (htf_df["open_time"] <= end)).sum())
    expected = (end - start) // _MINUTE_MS + 1
    cov = Coverage(
        symbol=symbol,
        timeframe=timeframe,
        window_start_ms=start,
        window_end_ms=end,
        htf_bars=htf_in_window,
        one_min_bars=len(clipped),
        expected_one_min=int(expected),
        note="ok",
    )
    if clipped.empty:
        return cov, None
    return cov, clipped


def _empty_coverage(symbol: str, timeframe: str, note: str) -> Coverage:
    return Coverage(
        symbol=symbol,
        timeframe=timeframe,
        window_start_ms=None,
        window_end_ms=None,
        htf_bars=0,
        one_min_bars=0,
        expected_one_min=0,
        note=note,
    )


def build_coverage_csv(coverages: list[Coverage]) -> str:
    """커버리지 리포트를 CSV 문자열로 만든다."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_COVERAGE_COLUMNS))
    writer.writeheader()
    for cov in coverages:
        writer.writerow(
            {
                "symbol": cov.symbol,
                "timeframe": cov.timeframe,
                "window_start": _iso(cov.window_start_ms),
                "window_end": _iso(cov.window_end_ms),
                "htf_bars": cov.htf_bars,
                "one_min_bars": cov.one_min_bars,
                "expected_one_min": cov.expected_one_min,
                "coverage_ratio": _fmt_ratio(cov.coverage_ratio),
                # 1분봉 미커버 상위TF 봉의 셋업은 B안 평가에서 제외된다(보수적 폴백 아님).
                "missing_policy": "exclude_uncovered_setups",
                "note": cov.note,
            }
        )
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    """A/B 본실험을 실행해 비교 CSV와 커버리지 CSV를 파일로 쓴다."""
    parser = argparse.ArgumentParser(description="WAN-46 A vs B 본실험(다심볼×다TF×다년)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--coverage-out", type=Path, default=DEFAULT_COVERAGE_PATH)
    add_cost_args(parser)
    args = parser.parse_args(argv)

    entries, coverages = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        backtest_config=cost_config(args),
    )
    report_csv = build_ab_report(entries)
    coverage_csv = build_coverage_csv(coverages)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report_csv, encoding="utf-8")
    args.coverage_out.parent.mkdir(parents=True, exist_ok=True)
    args.coverage_out.write_text(coverage_csv, encoding="utf-8")

    n_pairs = sum(1 for c in coverages if c.note == "ok")
    print(f"[ab_experiment] pairs_evaluated={n_pairs}/{len(coverages)} entries={len(entries)}")
    print(f"[ab_experiment] report → {args.out}")
    print(f"[ab_experiment] coverage → {args.coverage_out}")
    print(report_csv)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
