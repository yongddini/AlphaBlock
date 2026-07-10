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
    """심볼·타임프레임(+기간)으로 OHLCV를 조회한다."""
    with OhlcvStore(db_path) as store:
        return store.load(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
