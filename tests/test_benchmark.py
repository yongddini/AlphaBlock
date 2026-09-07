"""같은 창 buy&hold 벤치마크 (WAN-407 §4 · CLAUDE.md WAN-393 원칙).

우리는 롱 온리라 **오르는 장에서는 실력 없이도 계좌가 는다**. 2026-08-31에 실제로 난 오독이
그것이다 — 페이퍼가 +8.5%로 보였는데 같은 창 12종목 buy&hold가 +22.55%였고, 비용을 제대로
세니 −9.4%였다(WAN-392/393). 이 테스트는 그 병기가 **숫자로** 서는지 본다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data.models import Candle, timeframe_to_ms
from data.storage import OhlcvStore
from live.benchmark import BENCHMARK_TIMEFRAMES, buy_hold_row, buy_hold_summary

_START = 1_700_000_000_000
_HOUR_MS = 3_600_000


def _seed(db_path: str, symbol: str, timeframe: str, closes: list[float]) -> None:
    step = timeframe_to_ms(timeframe)
    with OhlcvStore(db_path) as store:
        store.upsert_candles(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=_START + i * step,
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=1.0,
            )
            for i, close in enumerate(closes)
        )


def test_buy_hold_is_first_to_last_close(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, "A/USDT:USDT", "1h", [100.0, 120.0, 110.0])
    summary = buy_hold_summary(
        db_path, ["A/USDT:USDT"], start_ms=_START, end_ms=_START + 10 * _HOUR_MS
    )
    assert summary.n == 1
    assert summary.rows[0].total_return == 110.0 / 100.0 - 1.0


def test_median_is_reported_because_the_mean_lies(tmp_path: Path) -> None:
    """🚨 한 종목이 평균을 통째로 끌어올린다.

    WAN-346이 「평균 −6.6% vs 중앙값 −26.6%」로 겪은 자리다.
    """
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, "MOON/USDT:USDT", "1h", [100.0, 400.0])  # +300%
    for name in ("A", "B", "C"):
        _seed(db_path, f"{name}/USDT:USDT", "1h", [100.0, 80.0])  # −20%

    summary = buy_hold_summary(
        db_path,
        ["MOON/USDT:USDT", "A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT"],
        start_ms=_START,
        end_ms=_START + 10 * _HOUR_MS,
    )
    assert summary.mean is not None and summary.mean > 0.0
    assert summary.median is not None and summary.median < 0.0
    assert summary.up_count == 1
    assert summary.n == 4


def test_missing_symbols_are_excluded_not_counted_as_zero(tmp_path: Path) -> None:
    """창에 봉이 없는 종목은 **분모에서 뺀다** — 「안 올랐다」와 「데이터가 없다」는 다르다."""
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, "A/USDT:USDT", "1h", [100.0, 150.0])
    summary = buy_hold_summary(
        db_path,
        ["A/USDT:USDT", "GONE/USDT:USDT"],
        start_ms=_START,
        end_ms=_START + 10 * _HOUR_MS,
    )
    assert summary.n == 1
    assert summary.missing == ("GONE/USDT:USDT",)
    assert summary.mean == 0.5  # 0으로 셌다면 0.25가 됐을 것이다.


def test_timeframe_preference_falls_back_when_the_fine_one_is_empty(tmp_path: Path) -> None:
    """촘촘한 TF가 없으면 성긴 쪽으로 내려간다 — 창에 2봉 이상 있는 첫 TF를 쓴다."""
    assert BENCHMARK_TIMEFRAMES[0] == "15m"
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, "A/USDT:USDT", "4h", [100.0, 90.0])
    with OhlcvStore(db_path) as store:
        row = buy_hold_row(store, "A/USDT:USDT", start_ms=_START, end_ms=_START + 10 * _HOUR_MS)
    assert row.timeframe == "4h"
    assert row.total_return == pytest.approx(-0.1)


def test_single_bar_window_is_not_measurable(tmp_path: Path) -> None:
    """봉이 하나뿐이면 「사서 판다」가 성립하지 않는다 — 0%가 아니라 측정 불가다."""
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, "A/USDT:USDT", "1h", [100.0])
    summary = buy_hold_summary(
        db_path, ["A/USDT:USDT"], start_ms=_START, end_ms=_START + 10 * _HOUR_MS
    )
    assert summary.n == 0
    assert summary.mean is None and summary.median is None
    assert summary.missing == ("A/USDT:USDT",)


def test_parity_report_carries_the_benchmark_when_the_universe_is_given(tmp_path: Path) -> None:
    """`build_parity_report`가 같은 창의 벤치마크를 **실제로 실어 온다**(WAN-407 §4 배선).

    렌더 테스트는 손으로 만든 요약을 그리지만, 이 테스트는 **리포트 조립 경로**가 DB를 읽어
    채우는지를 본다 — 인자를 안 주면 None이어야 하고(없는 표를 지어내지 않는다), 주면 값이
    있어야 한다. `cells=[]`라 백테스트 엔진은 돌지 않는다(순수 배선 검사).
    """
    from live.order_journal import OrderJournal
    from live.paper_parity import build_parity_report
    from paper.store import PaperTradeStore

    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, "A/USDT:USDT", "1h", [100.0, 130.0])
    journal = OrderJournal(db_path)
    store = PaperTradeStore(db_path)
    try:
        kwargs = dict(
            start_ms=_START,
            end_ms=_START + 10 * _HOUR_MS,
            start_key="2026-09-01",
            end_key="2026-09-02",
            cells=[],
        )
        without = build_parity_report(journal, store, **kwargs)  # type: ignore[arg-type]
        assert without.benchmark is None

        with_bench = build_parity_report(
            journal,
            store,
            db_path=db_path,
            benchmark_symbols=["A/USDT:USDT"],
            **kwargs,  # type: ignore[arg-type]
        )
        assert with_bench.benchmark is not None
        assert with_bench.benchmark.mean == pytest.approx(0.3)
    finally:
        journal.close()
        store.close()
