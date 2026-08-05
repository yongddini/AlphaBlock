"""WAN-246 추가 범위: 라이브 2h 감시 (WAN-252 흡수, 사용자 결정 2026-08-05).

라벨이 아니라 **동작**으로 고정한다:

- 감시 TF 기본값에 2h가 들어가고 러너가 9종목 × 4TF = 36 시리즈를 낸다.
- 2h는 DB에 물리 적재하지 않고 `OhlcvStore.load`가 1h에서 파생한다(WAN-24) — 러너의
  상위TF 로딩이 그 파생 경로를 그대로 타 별도 수집이 필요 없다.
- 봉 마감·만료 계수는 `timeframe_to_ms("2h")`(2시간 경계)를 TF 특수 분기 없이 일반
  처리한다 — 러너가 2h 상위TF 봉을 반영해 주문을 예약한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from backtest.sweep import timeframe_to_ms
from config.settings import Settings, _default_live_signal_timeframes
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live import limit_engine
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.zone_limit_runner import ZoneLimitPaperRunner, build_series
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
)

_SYMBOL = "BTC/USDT:USDT"
_TF = "2h"
_TWO_H = timeframe_to_ms(_TF)
_H = 3_600_000
_M = 60_000
_N_CLOSED = 40
_FORMING = _N_CLOSED * _TWO_H


def test_2h_is_in_default_watch_timeframes() -> None:
    """감시 TF 기본값에 2h가 있다(15m·1h·2h·4h)."""
    assert "2h" in _default_live_signal_timeframes()
    assert _default_live_signal_timeframes() == ["15m", "1h", "2h", "4h"]


def test_build_series_includes_2h_for_every_symbol() -> None:
    """러너 시리즈에 각 심볼의 2h 조합이 정확히 한 번씩 들어간다."""
    series = build_series(Settings())
    symbols = {s for s, _ in series}
    for sym in symbols:
        assert (sym, "2h") in series
    assert sum(1 for _, tf in series if tf == "2h") == len(symbols)


def test_2h_ohlcv_is_derived_not_stored(tmp_path: Path) -> None:
    """2h는 DB에 없고 1h에서 파생돼 읽힌다(WAN-24) — 별도 수집 불필요."""
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    # 1h 네 봉만 적재한다(2h는 한 줄도 넣지 않는다).
    base = 1_700_000_000_000 - (1_700_000_000_000 % _TWO_H)  # 2h 경계에 정렬.
    ones = [
        Candle(
            symbol=_SYMBOL,
            timeframe="1h",
            open_time=base + i * _H,
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1.0,
            closed=True,
        )
        for i in range(4)
    ]
    store.upsert_candles(ones)
    try:
        # 저장소에 2h 물리 행이 없음을 직접 확인.
        stored_2h = store.load(_SYMBOL, "2h")
        assert not stored_2h.empty  # 파생으로 나온다.
        # 1h 두 봉이 2h 한 봉으로 무손실 합쳐졌다.
        assert len(stored_2h) == 2
        first = stored_2h.iloc[0]
        assert float(first["open"]) == pytest.approx(100.0)  # 첫 1h 시가.
        assert float(first["high"]) == pytest.approx(102.0)  # 두 1h 고가 중 최대.
        assert float(first["low"]) == pytest.approx(99.0)  # 두 1h 저가 중 최소.
        assert float(first["close"]) == pytest.approx(101.5)  # 둘째 1h 종가.
    finally:
        store.close()


def _install_stub_detector(monkeypatch: pytest.MonkeyPatch, ob: OrderBlock) -> None:
    result = OrderBlockResult(order_blocks=[ob], signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)


@pytest.fixture()
def rig_2h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, object]]:
    """2h 시리즈 하나짜리 러너 — 1h 원본만 적재하고 2h는 파생으로 읽는다."""
    zone = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=95.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=_TWO_H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    _install_stub_detector(monkeypatch, zone)
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    # 2h 파생의 소스인 1h 봉을 적재한다(2h는 한 줄도 안 넣는다). 각 2h가 1h 두 봉.
    ones = []
    for i in range(_N_CLOSED * 2):
        t = i * _H
        ones.append(
            Candle(
                symbol=_SYMBOL,
                timeframe="1h",
                open_time=t,
                open=100.0,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=1.0,
                closed=True,
            )
        )
    store.upsert_candles(ones)

    settings = Settings(db_path=str(db))
    params = ConfluenceParams(max_zone_width_atr=None)
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    paper_store = PaperTradeStore(db)
    recorder = PaperTradeRecorder(paper_store, cost_model=settings.costs, funding_store=None)
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper_store,
        recorder=recorder,
        sizing=settings.risk_sizing,
    )
    engine = ZoneLimitLiveEngine(
        params=params,
        journal=journal,
        session_id=session,
        has_position=lambda s, t: any(
            p.symbol == s and p.timeframe == t for p in executor.open_positions
        ),
    )
    runner = ZoneLimitPaperRunner(
        store=store,
        engine=engine,
        journal=journal,
        session_id=session,
        executor=executor,
        params=params,
        series=[(_SYMBOL, _TF)],
        lookback_bars=500,
        poll_interval_seconds=1.0,
        now_ms=lambda: 999_999,
    )
    yield {"store": store, "runner": runner, "executor": executor, "journal": journal}
    store.close()
    journal.close()
    paper_store.close()


def _m1(t: int, low: float, high: float, close: float) -> Candle:
    return Candle(
        symbol=_SYMBOL,
        timeframe="1m",
        open_time=t,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        closed=True,
    )


def test_runner_arms_and_fills_on_2h_series(rig_2h: dict[str, object]) -> None:
    """러너가 2h 시리즈(파생 상위TF)에서 지정가를 예약·체결해 페이퍼 진입을 낸다.

    2h 봉 마감·만료 계수가 `timeframe_to_ms("2h")`로 일반 처리됨을 왕복으로 고정한다.
    """
    store: OhlcvStore = rig_2h["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig_2h["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig_2h["executor"]  # type: ignore[assignment]
    journal: OrderJournal = rig_2h["journal"]  # type: ignore[assignment]

    # 형성 중 2h 봉 안에서 존을 탭하고 지정가에 닿는 1분봉 → 예약+체결.
    store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])
    runner.poll_once()

    positions = executor.open_positions
    assert len(positions) == 1
    assert positions[0].timeframe == "2h"
    stats = journal.fill_stats()
    assert len(stats) == 1 and stats[0].filled == 1
