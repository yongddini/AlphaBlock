"""상폐된 종목의 풀 일봉이 「데이터 수집 지연」으로 울리지 않는다 (WAN-471) 회귀 테스트.

2026-10-22 바이낸스가 EPIC 선물을 상폐(정산)하자, 후보 풀 수집은 이미 EPIC을 뺐는데(`status ==
"TRADING"`) 감시는 풀 일봉(1d)을 「DB에 1d가 있는 모든 종목」으로 봐서 매시간 경보했다. 이 파일은
**동작**을 고정한다(라벨이 아니라):

* (a) 마지막 후보 풀에 없는 종목의 오래된 1d → 경보 없음(「상장 폐지·정산」 정보로 남는다).
* (b) 풀 안 종목의 1d가 멈춤 → 경보.
* (c) 풀 목록을 못 읽으면 → 옛 동작(저장된 1d 전부 판정) — 감시가 꺼지는 쪽으로 실패하지 않는다.
* (d) 수집기가 매 갱신마다 그때의 풀(TRADING만)을 저장하고, 빈 풀로는 덮지 않는다.
* (e) 수집기 시작 갭 복구도 같은 범위다 — 상폐 종목 1d를 「수집 정지」로 세지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cli.main import format_status
from config.settings import Settings
from dashboard.health_data import build_health_view, monitor_scope_for
from data.models import Candle
from data.repair import repair_all
from data.storage import OhlcvStore
from data.universe_pool import (
    UNIVERSE_TABLES,
    RotationSnapshot,
    UniverseStore,
    pool_members_latest,
)
from live.health_watch import evaluate_alerts, excluded_alert_keys
from tests.test_universe import T as MONDAY
from tests.test_universe import _FakeExchange, _market, _run

HOUR = 3_600_000
DAY = 24 * HOUR
NOW = MONDAY + 3 * HOUR

BTC = "BTC/USDT:USDT"  # 기본 격자
KEEP = "KEEP/USDT:USDT"  # 그 주 팔 목록
LIVE = "LIVE/USDT:USDT"  # 풀에만 있다(1d만 모은다)
EPIC = "EPIC/USDT:USDT"  # 상폐 — 풀에서 빠졌다

DAILY_FRESH = NOW - DAY - 3 * HOUR  # 어제 00:00 UTC 일봉(정상)
DAILY_STALE = NOW - 4 * DAY  # 사흘 넘게 멈춘 일봉


def _bar(symbol: str, timeframe: str, open_time: int) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "ohlcv.db"),
        symbols=[BTC],
        timeframes=["1h"],
        universe_enabled=True,
        live_runtime_state_path=str(tmp_path / "runtime.json"),
        collector_heartbeat_path=str(tmp_path / "hb.json"),
        health_watch_state_path=str(tmp_path / "watch.json"),
    )


def _seed(tmp_path: Path, *, live_last: int = DAILY_FRESH, pool: bool = True) -> Path:
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store:
        store.upsert_candles(
            [
                _bar(BTC, "1h", NOW - 2 * HOUR),
                _bar(KEEP, "1h", NOW - 2 * HOUR),
                _bar(KEEP, "1d", DAILY_FRESH),
                _bar(LIVE, "1d", live_last),
                _bar(EPIC, "1d", DAILY_STALE),  # 상폐 뒤로 다시는 안 온다
            ]
        )
    with UniverseStore(db) as universe:
        universe.save(
            RotationSnapshot(
                rebalance_ms=MONDAY,
                computed_ms=MONDAY,
                pool_size=3,
                stale_size=0,
                ranked=((KEEP, 1.0),),
                top_n=31,
            )
        )
        if pool:
            assert universe.save_pool([BTC, KEEP, LIVE], captured_ms=NOW - HOUR)
    return db


def _view(settings: Settings):  # type: ignore[no-untyped-def]
    return build_health_view(
        settings.db_path,
        runtime_state_path=settings.live_runtime_state_path,
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=settings.collector_heartbeat_path,
        now_ms=NOW,
        scope=monitor_scope_for(settings, now_ms=NOW),
    )


def _data_keys(view) -> set[str]:  # type: ignore[no-untyped-def]
    return {a.key for a in evaluate_alerts(view) if a.key.startswith("data:")}


# -- (a)~(c) -----------------------------------------------------------------


def test_a_delisted_symbol_daily_does_not_alert(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    view = _view(settings)
    assert view.monitor_scope_applied
    assert f"data:{EPIC}:1d" not in _data_keys(view)
    # 지우지 않고 「상장 폐지·정산」 정보로 남긴다(WAN-157).
    assert (EPIC, "1d") in {(f.symbol, f.timeframe) for f in view.excluded}
    assert view.left_pool_symbols == frozenset({EPIC})
    # 경보 중이었다면 「정상 복구」가 아니라 「감시 대상에서 제외」로 나간다.
    assert f"data:{EPIC}:1d" in excluded_alert_keys(view)


def test_b_pool_symbol_daily_stall_still_alerts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path, live_last=DAILY_STALE)
    view = _view(settings)
    assert f"data:{LIVE}:1d" in _data_keys(view)
    assert LIVE not in {f.symbol for f in view.excluded}
    assert LIVE not in view.left_pool_symbols


def test_b_fresh_pool_daily_is_monitored_not_excluded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    monitored = {(f.symbol, f.timeframe) for f in _view(settings).freshness}
    assert {(LIVE, "1d"), (KEEP, "1d"), (BTC, "1h"), (KEEP, "1h")} <= monitored


def test_c_unreadable_pool_falls_back_to_watching_all_daily(tmp_path: Path) -> None:
    """풀을 못 읽으면 1d는 옛 동작 — 상폐 종목이 다시 울리는 것이 감시가 꺼지는 것보다 낫다.

    같은 DB에서 풀 저장만 뺐을 때 (a)가 깨진다 = (a)가 풀 목록 덕분임을 보인다.
    """
    settings = _settings(tmp_path)
    _seed(tmp_path, pool=False)
    scope = monitor_scope_for(settings, now_ms=NOW)
    assert scope is not None and scope.pool_symbols is None
    view = _view(settings)
    assert f"data:{EPIC}:1d" in _data_keys(view)
    assert view.left_pool_symbols == frozenset()


# -- (d) 수집기 쪽 ---------------------------------------------------------------


class _Delisting(_FakeExchange):
    """한 종목을 정산 중(`SETTLING`)으로 내는 거래소 — 풀 수집이 그것을 빼야 한다."""

    def __init__(self, bars: dict[str, tuple[int, int, float]], settling: str) -> None:
        super().__init__(bars)
        self.settling = settling
        self.empty = False

    def load_markets(self, reload: bool = False) -> dict[str, dict[str, Any]]:
        if self.empty:
            return {}
        out = super().load_markets(reload)
        out[self.settling] = _market(self.settling.split("/")[0] + "USDT", status="SETTLING")
        return out


def test_d_collector_saves_the_trading_pool_and_ignores_empty(tmp_path: Path) -> None:
    db = tmp_path / "w.db"
    bars = {f"S{i:02d}/USDT:USDT": (MONDAY - 300 * DAY, MONDAY + 6 * DAY, 100.0) for i in range(3)}
    ex = _Delisting(bars, settling=EPIC)
    _run(ex, db, MONDAY + HOUR, [])
    assert pool_members_latest(db) == frozenset(bars)  # EPIC(정산 중)은 없다
    # 거래소가 빈 응답을 주면 풀을 비우지 않는다(감시가 1d를 통째로 놓친다).
    ex.empty = True
    _run(ex, db, MONDAY + 2 * DAY, [])
    assert pool_members_latest(db) == frozenset(bars)


def test_d_pool_is_replaced_not_accumulated(tmp_path: Path) -> None:
    with UniverseStore(tmp_path / "u.db") as store:
        assert store.pool() is None
        assert store.save_pool([EPIC, LIVE], captured_ms=1)
        assert store.save_pool([LIVE], captured_ms=2)  # EPIC 상폐
        assert store.pool() == frozenset({LIVE})
        assert not store.save_pool([], captured_ms=3)
        assert store.pool() == frozenset({LIVE})
    assert "universe_pool_members" in UNIVERSE_TABLES  # 페이퍼 리셋이 지우지 않는 시장 기록
    assert pool_members_latest(tmp_path / "missing.db") is None


# -- (e) 갭 복구도 같은 범위 ----------------------------------------------------


class _NoFetch:
    def fetch_ohlcv(self, *args: object, **kwargs: object) -> list[list[float]]:
        return []


def test_e_startup_repair_does_not_call_delisted_daily_a_stall(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = _seed(tmp_path)
    scope = monitor_scope_for(settings, now_ms=NOW)
    with OhlcvStore(db) as store:
        summary = repair_all(
            _NoFetch(), store, now_ms=lambda: NOW, scope=scope, sleeper=lambda s: None
        )
    assert (EPIC, "1d") not in {(s.symbol, s.timeframe) for s in summary.stale_series}
    assert (EPIC, "1d", "plan") in {
        (u.symbol, u.timeframe, u.reason) for u in summary.untracked_series
    }


# -- 화면 ----------------------------------------------------------------------


def test_status_folds_delisted_under_its_own_reason(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    text = format_status(_view(settings), configured_symbols=[BTC])
    assert "상장 폐지·정산(풀에서 빠짐) 1종목 · 1시리즈" in text
    assert EPIC in text
    assert "수집 대상 아님(목록 제외)" not in text  # EPIC을 팔 목록 제외로 부르지 않는다


def test_dashboard_frame_labels_delisted_rows(tmp_path: Path) -> None:
    from dashboard.app import _excluded_frame

    settings = _settings(tmp_path)
    _seed(tmp_path)
    view = _view(settings)
    frame = _excluded_frame(view.excluded, view.left_pool_symbols)
    assert set(frame.loc[frame["심볼"] == EPIC, "상태"]) == {"상장 폐지·정산(풀에서 빠짐)"}
