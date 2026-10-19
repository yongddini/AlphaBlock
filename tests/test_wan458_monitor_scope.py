"""신선도 감시 범위 = 지금 수집 계획 (WAN-458) 회귀 테스트.

2026-10-19 첫 팔 목록 교체 직후, 목록에서 빠져 **일부러 안 모으는** 종목(BR·LSK·ONE)이
「데이터 수집 지연」으로 매시간 경보됐다. 감시가 「지금 모으는 목록」이 아니라 「DB에 있는
모든 시리즈」를 봤기 때문이다. 이 파일은 **동작**을 고정한다(라벨이 아니라):

* (a) 목록에서 빠진 종목의 오래된 봉 → 경보 없음(정보로만 남는다).
* (b) 목록에 있는 종목이 멈춤 → 여전히 경보.
* (c) 목록에서 빠졌지만 보유 중이라 계속 모으는 종목이 멈춤 → 경보.
* (d) 계획을 못 읽으면 → 옛 동작(저장된 시리즈 전부 판정). 감시가 꺼지는 쪽으로 실패하지 않는다.
* (e) 옛 코드(범위 없음)로 되돌리면 (a)가 실패한다 — 같은 DB에서 범위를 빼면 경보가 난다.

그리고 이미 경보 중이던 빠진 종목에 「✅ 정상 복구」가 나가지 않는다(복구가 아니다).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cli.main import format_status
from config.settings import Settings
from dashboard.health_data import build_health_view, monitor_scope_for
from data.collection_plan import monitor_scope
from data.models import Candle
from data.storage import OhlcvStore
from data.universe_pool import RotationSnapshot, UniverseStore, rotation_members_at
from live.health_watch import (
    AlertRecord,
    HealthWatch,
    WatchState,
    WatchStateStore,
    build_view_provider,
    evaluate_alerts,
    reconcile,
)

HOUR = 3_600_000
DAY = 24 * HOUR
T1 = 1_760_918_400_000  # 지난주 리밸런싱
T2 = T1 + 7 * DAY  # 이번 주 리밸런싱(BR·LSK·ONE 사고 자리)
NOW = T2 + 3 * HOUR

BTC = "BTC/USDT:USDT"  # 기본 격자
KEEP = "KEEP/USDT:USDT"  # 두 주 연속 목록
DROPPED = "ONE/USDT:USDT"  # 이번 주 빠짐
HELD = "HELD/USDT:USDT"  # 지난주에 빠졌지만 포지션 보유
NEW = "NEW/USDT:USDT"  # 이번 주 들어옴

FRESH = NOW - 2 * HOUR
STALE = NOW - 10 * HOUR


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


def _settings(tmp_path: Path, *, universe_enabled: bool = True) -> Settings:
    return Settings(
        db_path=str(tmp_path / "ohlcv.db"),
        symbols=[BTC],
        timeframes=["1h"],
        universe_enabled=universe_enabled,
        live_runtime_state_path=str(tmp_path / "runtime.json"),
        collector_heartbeat_path=str(tmp_path / "hb.json"),
        health_watch_state_path=str(tmp_path / "watch.json"),
    )


def _seed(
    tmp_path: Path,
    *,
    keep_last: int = FRESH,
    held_last: int = FRESH,
    rotations: bool = True,
    held: bool = True,
) -> Path:
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store:
        store.upsert_candles(
            [
                _bar(BTC, "1h", FRESH),
                _bar(KEEP, "1h", keep_last),
                _bar(KEEP, "3h", NOW - 4 * HOUR),  # 수집기가 1h에서 접어 저장하는 팔 TF
                _bar(NEW, "1h", FRESH),
                _bar(HELD, "1h", held_last),
                # 이번 주 빠진 종목 — 교체 직후부터 1h·15m이 멈췄다(일부러 안 모은다).
                _bar(DROPPED, "1h", STALE),
                _bar(DROPPED, "15m", STALE),
                # 풀 일봉은 빠진 종목도 계속 모은다.
                _bar(DROPPED, "1d", NOW - DAY - HOUR),
            ]
        )
    if rotations:
        with UniverseStore(db) as universe:
            for t, members in ((T1, [KEEP, DROPPED, HELD]), (T2, [KEEP, NEW])):
                universe.save(
                    RotationSnapshot(
                        rebalance_ms=t,
                        computed_ms=t,
                        pool_size=10,
                        stale_size=0,
                        ranked=tuple((s, 1.0) for s in members),
                        top_n=31,
                    )
                )
    if held:
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS open_positions (symbol TEXT, timeframe TEXT)")
        conn.execute("INSERT INTO open_positions VALUES (?, '1h')", (HELD,))
        conn.commit()
        conn.close()
    return db


def _view(settings: Settings, *, scoped: bool = True):  # type: ignore[no-untyped-def]
    return build_health_view(
        settings.db_path,
        runtime_state_path=settings.live_runtime_state_path,
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=settings.collector_heartbeat_path,
        now_ms=NOW,
        scope=monitor_scope_for(settings, now_ms=NOW) if scoped else None,
    )


def _data_keys(view) -> set[str]:  # type: ignore[no-untyped-def]
    return {a.key for a in evaluate_alerts(view) if a.key.startswith("data:")}


# -- (a)~(e) -----------------------------------------------------------------


def test_a_dropped_symbol_does_not_alert(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    view = _view(settings)
    assert view.monitor_scope_applied
    assert not {k for k in _data_keys(view) if DROPPED in k}
    # 지우지 않고 정보로 남긴다(WAN-157).
    assert {(f.symbol, f.timeframe) for f in view.excluded} == {(DROPPED, "1h"), (DROPPED, "15m")}


def test_b_member_stall_still_alerts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path, keep_last=STALE)
    assert f"data:{KEEP}:1h" in _data_keys(_view(settings))


def test_c_held_symbol_outside_list_still_alerts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path, held_last=STALE)
    view = _view(settings)
    assert f"data:{HELD}:1h" in _data_keys(view)
    assert HELD not in {f.symbol for f in view.excluded}


def test_c_without_position_the_same_symbol_is_excluded(tmp_path: Path) -> None:
    """(c)의 대조 — 보유가 없으면 같은 종목이 목록 제외다(보유가 실제로 범위를 정한다)."""
    settings = _settings(tmp_path)
    _seed(tmp_path, held_last=STALE, held=False)
    view = _view(settings)
    assert f"data:{HELD}:1h" not in _data_keys(view)
    assert HELD in {f.symbol for f in view.excluded}


def test_d_unreadable_plan_falls_back_to_old_behaviour(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path, rotations=False)
    assert monitor_scope_for(settings, now_ms=NOW) is None
    view = _view(settings)
    assert not view.monitor_scope_applied
    assert view.excluded == []
    assert f"data:{DROPPED}:1h" in _data_keys(view)


def test_d_before_first_rotation_falls_back(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    assert monitor_scope_for(settings, now_ms=T1 - 1) is None


def test_e_without_scope_the_dropped_symbol_alerts(tmp_path: Path) -> None:
    """옛 동작(범위 없음)이 같은 DB에서 거짓 경보를 낸다 — (a)가 범위 덕분임을 보인다."""
    settings = _settings(tmp_path)
    _seed(tmp_path)
    assert f"data:{DROPPED}:1h" in _data_keys(_view(settings, scoped=False))


def test_e_watch_provider_actually_applies_the_scope(tmp_path: Path) -> None:
    """배선 고정 — 워치가 범위를 안 넘기면(옛 코드) 이 테스트와 (a)가 함께 깨진다."""
    settings = _settings(tmp_path)
    _seed(tmp_path)
    view = build_view_provider(settings)()  # 실제 시계 — 범위 계산도 「지금」으로 한다
    assert view.monitor_scope_applied
    assert f"data:{DROPPED}:1h" not in _data_keys(view)
    assert f"data:{DROPPED}:15m" not in _data_keys(view)


# -- 범위의 세부 ---------------------------------------------------------------


def test_scope_keeps_built_3h_and_pool_daily(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    view = _view(settings)
    monitored = {(f.symbol, f.timeframe) for f in view.freshness}
    assert (KEEP, "3h") in monitored  # 팔 종목의 접어 저장한 3h
    assert (DROPPED, "1d") in monitored  # 풀 일봉은 빠진 종목도 계속 모은다
    assert (BTC, "1h") in monitored


def test_universe_disabled_scope_is_base_grid(tmp_path: Path) -> None:
    settings = _settings(tmp_path, universe_enabled=False)
    _seed(tmp_path)
    scope = monitor_scope_for(settings, now_ms=NOW)
    assert scope is not None
    assert scope.covers(BTC, "1h")
    assert not scope.covers(KEEP, "1h")
    assert not scope.covers(DROPPED, "1d")


def test_scope_matches_the_collector_plan(tmp_path: Path) -> None:
    """감시 범위는 수집기와 같은 `build_plan`에서 나온다 — 두 목록이 갈라지면 같은 병이 돌아온다."""
    from data.collection_plan import ArmCollection

    _seed(tmp_path)
    db = tmp_path / "ohlcv.db"
    with OhlcvStore(db) as store, UniverseStore(db) as universe:
        arm = ArmCollection(
            exchange=None,  # type: ignore[arg-type]
            store=store,
            universe=universe,
            db_path=db,
            base_symbols=[BTC],
            base_timeframes=["1h"],
            lookback_days=lambda tf: 1,
            pause_seconds=0,
            now_ms=lambda: NOW,
        )
        plan_pairs = set(arm.compute().pairs())
    scope = monitor_scope(
        db, base_symbols=[BTC], base_timeframes=["1h"], universe_enabled=True, now_ms=NOW
    )
    assert scope is not None
    assert plan_pairs <= scope.pairs


def test_rotation_members_at_is_read_only(tmp_path: Path) -> None:
    db = tmp_path / "plain.db"
    with OhlcvStore(db):
        pass
    assert rotation_members_at(db, NOW) is None
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "universe_rotations" not in names  # 감시가 표를 만들지 않는다
    assert rotation_members_at(tmp_path / "missing.db", NOW) is None


# -- 복구 알림 구분 -------------------------------------------------------------


def test_reconcile_excluded_key_is_not_a_recovery() -> None:
    key = f"data:{DROPPED}:1h"
    state = WatchState(
        active={
            key: AlertRecord(title=f"데이터 수집 지연: {DROPPED} 1h", last_notified_ms=0),
            "collector": AlertRecord(title="수집기 하트비트 끊김", last_notified_ms=0),
        }
    )
    out, new_state = reconcile(
        [], state, now_ms=NOW, cooldown_ms=HOUR, excluded_keys=frozenset({key})
    )
    by_key = {m.key: m for m in out}
    assert by_key[key].kind == "excluded"
    assert "정상 복구" not in by_key[key].text
    assert "감시 대상에서 제외" in by_key[key].text
    assert by_key["collector"].kind == "recovery"  # 진짜 복구는 그대로
    assert new_state.active == {}


def test_watch_sends_excluded_not_recovery_for_an_alerting_dropped_symbol(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    store = WatchStateStore(settings.health_watch_state_path)
    key = f"data:{DROPPED}:1h"
    store.save(
        WatchState(
            active={key: AlertRecord(title=f"데이터 수집 지연: {DROPPED} 1h", last_notified_ms=0)}
        )
    )
    sent: list[str] = []

    def notify(text: str) -> bool:
        sent.append(text)
        return True

    watch = HealthWatch(
        view_provider=lambda: _view(settings),
        notify=notify,
        store=store,
        cooldown_seconds=3600,
        interval_seconds=60,
        now_ms=lambda: NOW,
    )
    out = watch.check_once()
    assert [m.kind for m in out if m.key == key] == ["excluded"]
    assert not any("정상 복구" in t and DROPPED in t for t in sent)
    # 두 번째 점검에서는 아무것도 다시 안 보낸다(상태에서 빠졌다).
    assert [m for m in watch.check_once() if m.key == key] == []


# -- 화면 ----------------------------------------------------------------------


def test_status_shows_excluded_as_info(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path)
    text = format_status(_view(settings), configured_symbols=[BTC])
    assert "수집 대상 아님(목록 제외) 1종목 · 2시리즈" in text
    assert DROPPED in text
    # 지금 모으는 팔 종목을 「낡습니다」로 부르지 않는다.
    assert "저장돼 있으나 수집 대상이 아님(낡습니다)" not in text
    assert "수집 계획(팔 목록·보유)에 있음" in text


def test_status_says_when_the_plan_was_not_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed(tmp_path, rotations=False)
    assert "수집 계획을 못 읽어" in format_status(_view(settings))
