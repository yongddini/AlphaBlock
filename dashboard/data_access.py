"""대시보드용 데이터 조회 헬퍼 (WAN-6 SQLite OHLCV 저장소 래핑)."""

from __future__ import annotations

import pandas as pd

from data.storage import OhlcvStore


def list_series(db_path: str) -> list[tuple[str, str]]:
    """저장된 (symbol, timeframe) 조합 목록을 반환한다."""
    with OhlcvStore(db_path) as store:
        return store.list_series()


def load_ohlcv(
    db_path: str,
    symbol: str,
    timeframe: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> pd.DataFrame:
    """심볼·타임프레임(+기간)으로 OHLCV를 조회한다.

    ⚠️ `end_ms`는 **배타**(`open_time < end_ms`)다 — 마지막 봉을 포함하려면 호출부가
    `end_ms + 1`을 넘긴다(`dashboard.app`의 기간 선택이 그렇게 한다).
    """
    with OhlcvStore(db_path) as store:
        return store.load(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)


def series_bounds(db_path: str, symbol: str, timeframe: str) -> tuple[int, int] | None:
    """시리즈의 (첫 봉, 마지막 봉) `open_time`. 데이터가 없으면 `None` (WAN-188).

    기간 선택 위젯의 범위를 잡는 데는 **경계 두 개만** 있으면 된다. 예전에는 이걸 알려고
    전 구간을 통째로 로드했는데(6년 15m ≈ 21만 봉 · 19MB), 여기서 쓰는 `MIN`/`MAX`는
    `(symbol, timeframe, open_time)` 인덱스만 읽어 6년 DB에서도 즉시 끝난다.

    파생 TF(`2h` 등)는 저장소에 행이 없어 인덱스 경로가 `None`을 내므로, 그때만
    리샘플 로드로 물러나 경계를 구한다 — 조용히 "데이터 없음"으로 접히지 않게 한다.
    """
    with OhlcvStore(db_path) as store:
        first = store.first_open_time(symbol, timeframe)
        last = store.last_open_time(symbol, timeframe)
        if first is not None and last is not None:
            return first, last
        frame = store.load(symbol, timeframe)
    if frame.empty:
        return None
    return int(frame["open_time"].min()), int(frame["open_time"].max())
