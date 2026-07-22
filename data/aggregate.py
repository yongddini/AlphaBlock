"""1분봉 → native 상위 TF 집계 봉 빌드 (WAN-175).

백테스트 로더(`data.storage.OhlcvStore._load_native`)는 미리 저장된 native TF
봉을 직접 읽는다 — 1분봉을 그때그때 합치지 않는다(`_DERIVED_TIMEFRAMES`의 2h만
예외). 따라서 native 봉이 없는 (심볼, TF)는 백테스트가 조용히 건너뛴다.
이 모듈은 저장된 1분봉을 `data.resample.resample_ohlcv`(WAN-24, 파리티 검증과
같은 정의)로 상위 TF에 집계해 **native 봉으로 저장**한다.

안전 규약(완료 기준의 「기존 행 비트 단위 불변」):

- 각 (심볼, TF)에서 **기존 첫 봉 이전 구간만** 만든다(`open_time < 기존 첫
  open_time` 필터). 기존 봉이 없으면(신규 심볼) 전 구간을 만든다.
- 저장은 `OhlcvStore.insert_candles_ignore`(충돌 시 무시)라 필터를 뚫는 행이
  있어도 기존 행은 SQL 수준에서 보존된다 — 이중 방어.
- 리샘플러는 구성 1분봉이 **빠짐없이** 모인 버킷만 만들므로(WAN-24), 1분봉
  갭·양끝의 미완 버킷은 상위 봉이 생성되지 않는다. 즉 생성 봉은 전부 원본이
  온전한 버킷이다(파리티 오탐 없음).

실행(빌드는 명시적 1회성 작업이다 — 수집기·백테스트가 자동으로 부르지 않는다)::

    uv run python -m data.aggregate                    # 1m 보유 전 심볼 × 15m/1h/4h/1d
    uv run python -m data.aggregate --symbols DOGE/USDT:USDT --timeframes 15m,1h
    uv run python -m data.aggregate --dry-run          # 쓰지 않고 계획만 출력
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass

import pandas as pd

from data.models import Candle
from data.resample import resample_ohlcv
from data.storage import OhlcvStore

logger = logging.getLogger(__name__)

# 빌드 대상 기본 TF — 백테스트가 읽는 native TF 전부 (WAN-175).
DEFAULT_TARGET_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
# 집계 원본 TF. 지정가 백테스트(WAN-41 서브스텝)의 원본과 같은 1분봉이다.
SOURCE_TIMEFRAME = "1m"


@dataclass(frozen=True, slots=True)
class BuildResult:
    """한 (심볼, 대상 TF) 빌드의 결과."""

    symbol: str
    timeframe: str
    existing_first_ms: int | None
    """빌드 전 그 TF의 기존 첫 open_time. None이면 기존 봉 없음(전 구간 빌드)."""
    built: int
    """리샘플로 생성된(컷오프 필터 후) 후보 봉 수."""
    inserted: int
    """실제 새로 삽입된 봉 수. dry-run이면 0."""
    dry_run: bool = False


def build_symbol(
    store: OhlcvStore,
    symbol: str,
    target_timeframes: tuple[str, ...] = DEFAULT_TARGET_TIMEFRAMES,
    *,
    dry_run: bool = False,
) -> list[BuildResult]:
    """한 심볼의 1분봉을 상위 TF들로 집계해 저장한다.

    각 대상 TF마다 기존 첫 봉 **이전** 구간만 만든다(기존 봉이 없으면 전 구간).
    1분봉이 없으면 빈 리스트를 반환한다(만들 것이 없다).
    """
    src_first = store.first_open_time(symbol, SOURCE_TIMEFRAME)
    if src_first is None:
        logger.warning("%s: %s 봉이 없어 건너뜀", symbol, SOURCE_TIMEFRAME)
        return []

    cutoffs: dict[str, int | None] = {
        tf: store.first_open_time(symbol, tf) for tf in target_timeframes
    }
    # 1분봉 로드 창: 어떤 TF든 기존 봉이 없으면 끝까지, 전부 있으면 가장 늦은
    # 기존 첫 봉까지만 읽는다(그 뒤 1분봉은 어떤 TF에도 쓰이지 않는다).
    if any(cutoff is None for cutoff in cutoffs.values()):
        load_end: int | None = None
    else:
        load_end = max(cutoff for cutoff in cutoffs.values() if cutoff is not None)

    t0 = time.monotonic()
    source = store.load(symbol, SOURCE_TIMEFRAME, end_ms=load_end)
    logger.info(
        "%s: %s %d행 로드 (%.1fs)",
        symbol,
        SOURCE_TIMEFRAME,
        len(source),
        time.monotonic() - t0,
    )

    results: list[BuildResult] = []
    for tf in target_timeframes:
        cutoff = cutoffs[tf]
        t1 = time.monotonic()
        resampled = resample_ohlcv(source, SOURCE_TIMEFRAME, tf)
        if cutoff is not None:
            resampled = resampled[resampled["open_time"] < cutoff]
        built = len(resampled)
        inserted = 0
        if built and not dry_run:
            inserted = store.insert_candles_ignore(_frame_to_candles(resampled))
        logger.info(
            "%s %s: 생성 %d · 삽입 %d (기존 첫 봉 %s, %.1fs)",
            symbol,
            tf,
            built,
            inserted,
            cutoff,
            time.monotonic() - t1,
        )
        results.append(
            BuildResult(
                symbol=symbol,
                timeframe=tf,
                existing_first_ms=cutoff,
                built=built,
                inserted=inserted,
                dry_run=dry_run,
            )
        )
    return results


def _frame_to_candles(df: pd.DataFrame) -> list[Candle]:
    """리샘플 결과 DataFrame(`resample_ohlcv` 스키마)을 `Candle` 목록으로 변환한다."""
    return [
        Candle(
            symbol=str(row.symbol),
            timeframe=str(row.timeframe),
            open_time=int(row.open_time),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            closed=bool(row.closed),
        )
        for row in df.itertuples(index=False)
    ]


def symbols_with_source(store: OhlcvStore) -> list[str]:
    """1분봉을 보유한 심볼 목록(빌드 가능 대상)을 반환한다."""
    return sorted({sym for sym, tf in store.list_series() if tf == SOURCE_TIMEFRAME})


def format_results(results: list[BuildResult]) -> str:
    """빌드 결과를 사람이 읽을 표로 렌더링한다."""
    if not results:
        return "빌드된 것 없음 (1m 원본이 없는 심볼)"
    lines = [
        f"{'symbol':<18} {'tf':<4} {'existing_first':<24} {'built':>8} {'inserted':>8}",
    ]
    for r in results:
        if r.existing_first_ms is None:
            first = "(없음 → 전 구간)"
        else:
            first = str(pd.Timestamp(r.existing_first_ms, unit="ms", tz="UTC"))
        inserted = "(dry-run)" if r.dry_run else str(r.inserted)
        lines.append(f"{r.symbol:<18} {r.timeframe:<4} {first:<24} {r.built:>8} {inserted:>8}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: 1분봉 → native 상위 TF 집계 봉 빌드."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else "")
    parser.add_argument(
        "--db",
        default="data/ohlcv.db",
        help="OHLCV SQLite 경로 (기본: data/ohlcv.db)",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="쉼표 구분 심볼 목록 (기본: 1m 봉을 보유한 전 심볼)",
    )
    parser.add_argument(
        "--timeframes",
        default=",".join(DEFAULT_TARGET_TIMEFRAMES),
        help=f"쉼표 구분 대상 TF (기본: {','.join(DEFAULT_TARGET_TIMEFRAMES)})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB에 쓰지 않고 생성될 봉 수만 출력",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    timeframes = tuple(tf.strip() for tf in args.timeframes.split(",") if tf.strip())

    all_results: list[BuildResult] = []
    with OhlcvStore(args.db) as store:
        if args.symbols:
            symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        else:
            symbols = symbols_with_source(store)
        for symbol in symbols:
            all_results.extend(build_symbol(store, symbol, timeframes, dry_run=args.dry_run))
    print(format_results(all_results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
