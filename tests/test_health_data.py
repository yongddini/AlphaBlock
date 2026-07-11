"""dashboard.health_data 조립 테스트 (WAN-30) — DB·상태파일 통합."""

from __future__ import annotations

from pathlib import Path

from dashboard.health import HealthLevel
from dashboard.health_data import build_health_view
from data.funding import FundingRateStore
from data.models import Candle, FundingRate
from data.storage import OhlcvStore
from live.paper import PaperPosition
from live.runtime_state import RuntimeStateStore
from strategy.models import OrderBlockDirection

_HOUR = 3_600_000
_NOW = 1_000 * _HOUR


def _seed(db_path: str, *, last_open_time: int) -> None:
    with OhlcvStore(db_path) as store:
        store.upsert_candles(
            Candle(
                "BTC/USDT:USDT", "1h", last_open_time - i * _HOUR, 100.0, 105.0, 95.0, 100.0, 1.0
            )
            for i in range(5)
        )
    with FundingRateStore(db_path) as funding:
        funding.upsert_rates(
            [
                FundingRate(
                    symbol="BTC/USDT:USDT",
                    funding_time=_NOW + 4 * _HOUR,
                    rate=0.0001,
                    mark_price=100.0,
                    next_funding_time=_NOW + 4 * _HOUR,
                    is_predicted=True,
                )
            ]
        )


def test_build_health_view_fresh_data_and_running_runner(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, last_open_time=_NOW)

    state_path = tmp_path / "runtime.json"
    RuntimeStateStore(state_path).record(
        now_ms=_NOW - 30_000,
        open_positions=[
            PaperPosition(
                symbol="BTC/USDT:USDT",
                timeframe="1h",
                direction=OrderBlockDirection.BULLISH,
                entry_time=_NOW - 10 * _HOUR,
                entry_price=90.0,
                stop_price=85.0,
                take_profit_price=110.0,
            )
        ],
        new_events=[],
    )

    view = build_health_view(
        db_path,
        runtime_state_path=str(state_path),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        now_ms=_NOW,
    )

    assert view.overall.level is HealthLevel.OK
    assert view.freshness[0].level is HealthLevel.OK
    assert view.funding[0].level is HealthLevel.OK
    assert view.runner.ran is True
    assert view.runner.level is HealthLevel.OK
    # 현재가(최신 종가 100) 대비 진입가 90 롱 → +11.11% 근처
    pos = view.positions[0]
    assert pos.current_price == 100.0
    assert pos.unrealized_pct is not None and pos.unrealized_pct > 10.0


def test_build_health_view_detects_stale_collection_and_dead_runner(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ohlcv.db")
    # 최신 봉이 20시간 전 → 수집 멈춤
    _seed(db_path, last_open_time=_NOW - 20 * _HOUR)

    state_path = tmp_path / "runtime.json"
    # 러너 하트비트가 1시간 전(폴링 60s 대비 한참) → 멈춤
    RuntimeStateStore(state_path).record(now_ms=_NOW - _HOUR, open_positions=[], new_events=[])

    view = build_health_view(
        db_path,
        runtime_state_path=str(state_path),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        now_ms=_NOW,
    )

    assert view.freshness[0].level is HealthLevel.STALE
    assert view.runner.level is HealthLevel.STALE
    assert view.overall.label == "멈춤"


def test_build_health_view_no_data_and_no_runner(tmp_path: Path) -> None:
    db_path = str(tmp_path / "empty.db")
    view = build_health_view(
        db_path,
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        now_ms=_NOW,
    )
    assert view.freshness == []
    assert view.positions == []
    assert view.runner.ran is False
    assert view.runner.level is HealthLevel.UNKNOWN
