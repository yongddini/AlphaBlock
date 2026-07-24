"""dashboard.health_data 조립 테스트 (WAN-30) — DB·상태파일 통합."""

from __future__ import annotations

from pathlib import Path

from common.heartbeat import HeartbeatStore
from dashboard.health import HealthLevel
from dashboard.health_data import build_health_view, series_freshness_rows
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

    hb_path = tmp_path / "collector_heartbeat.json"
    HeartbeatStore(hb_path, label="collector", now_ms=lambda: _NOW - 30_000).beat()

    view = build_health_view(
        db_path,
        runtime_state_path=str(state_path),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=str(hb_path),
        collector_heartbeat_interval_seconds=60,
        now_ms=_NOW,
    )

    assert view.overall.level is HealthLevel.OK
    assert view.freshness[0].level is HealthLevel.OK
    assert view.funding[0].level is HealthLevel.OK
    assert view.collector.ran is True
    assert view.collector.level is HealthLevel.OK
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
    assert view.collector.ran is False
    assert view.collector.level is HealthLevel.UNKNOWN
    assert view.runner.ran is False
    assert view.runner.level is HealthLevel.UNKNOWN


# --- 봉 수는 옵트인 (WAN-186) -----------------------------------------------


def test_series_freshness_rows_does_not_count_bars_by_default(tmp_path: Path) -> None:
    """기본 경로는 `COUNT(*)`를 **호출조차 하지 않는다**(WAN-186).

    시리즈별 COUNT는 PK 인덱스의 그 시리즈 구간을 전부 훑는다 — 6년 × 9종목 DB에서
    1분봉 한 시리즈가 ~315만 행이라, 화면의 「N봉」 하나 때문에 status가 멈췄다.
    라벨이 아니라 **호출 여부**로 고정한다(안 세는 척하며 세면 그대로 느리다).
    """
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, last_open_time=_NOW)

    with OhlcvStore(db_path) as store:
        calls: list[tuple[str | None, str | None]] = []
        real_count = store.count

        def spy(symbol: str | None = None, timeframe: str | None = None) -> int:
            calls.append((symbol, timeframe))
            return real_count(symbol, timeframe)

        store.count = spy  # type: ignore[method-assign]

        rows = series_freshness_rows(store)
        assert calls == []
        assert [r[3] for r in rows] == [None]
        # 신선도에 필요한 최신 봉 시각은 그대로 나온다(뺀 것은 봉 수뿐).
        assert rows[0][:3] == ("BTC/USDT:USDT", "1h", _NOW)

        counted = series_freshness_rows(store, include_bar_count=True)
        assert calls == [("BTC/USDT:USDT", "1h")]
        assert [r[3] for r in counted] == [5]


def test_build_health_view_bar_count_is_opt_in(tmp_path: Path) -> None:
    """`build_health_view`도 기본은 안 세고, 켜면 정확한 수를 준다(WAN-186)."""
    db_path = str(tmp_path / "ohlcv.db")
    _seed(db_path, last_open_time=_NOW)
    kwargs = {
        "runtime_state_path": str(tmp_path / "missing.json"),
        "poll_interval_seconds": 60,
        "stale_multiplier": 2.5,
        "now_ms": _NOW,
    }

    default_view = build_health_view(db_path, **kwargs)  # type: ignore[arg-type]
    assert default_view.freshness[0].bar_count is None
    # 봉 수를 안 세도 신선도 판정은 멀쩡해야 한다 — 그게 이 화면의 본업이다.
    assert default_view.freshness[0].level is HealthLevel.OK

    counted_view = build_health_view(db_path, include_bar_count=True, **kwargs)  # type: ignore[arg-type]
    assert counted_view.freshness[0].bar_count == 5
    assert counted_view.freshness[0].level is HealthLevel.OK
