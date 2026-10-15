"""팔 켜기 전 점검(WAN-451 ③ 전환 런북) — 준비됨 · 막힘을 각각 잡는가."""

from __future__ import annotations

from pathlib import Path

from config.settings import Settings
from data.models import Candle, timeframe_to_ms
from data.storage import OhlcvStore
from data.universe import rebalance_at
from data.universe_pool import RotationSnapshot, UniverseStore
from live import arm_check as ac
from live.zone_limit_runner import ARM_FILL_PENETRATION_BPS, ARM_RISK_PER_TRADE

DAY = 86_400_000
NOW = 1_790_000_000_000 - 1_790_000_000_000 % 60_000
SYMS = ("AAA/USDT:USDT", "BBB/USDT:USDT")


def _arm_settings(db: Path, **over: object) -> Settings:
    base: dict[str, object] = {
        "db_path": str(db),
        "confluence": {"retap_mode": "once", "fill_penetration_bps": ARM_FILL_PENETRATION_BPS},
        "risk_sizing": {"risk_per_trade": ARM_RISK_PER_TRADE},
        "live_zone_limit_enabled": True,
        "universe_enabled": True,
        "live_arm_enabled": True,
        "live_tsmom_enabled": True,
        "live_zone_memory_days": 30,
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def _seed(
    db: Path,
    *,
    history_days: int = 40,
    daily_days: int | None = None,
    stale: tuple[str, str] | None = None,
) -> None:
    """`daily_days`를 주면 1d만 그만큼 오래됐다(수집기가 다른 TF 이력을 아직 못 늘린 경우)."""
    with OhlcvStore(db) as store:
        for sym in SYMS:
            for tf in ac.CHECK_TIMEFRAMES:
                tf_ms = timeframe_to_ms(tf)
                last = (NOW // tf_ms) * tf_ms - tf_ms  # 마지막 확정봉
                if (sym, tf) == stale:
                    last -= 5 * tf_ms + 3 * DAY
                days = daily_days if (tf == "1d" and daily_days is not None) else history_days
                listed = ((NOW - days * DAY) // DAY) * DAY  # 상장일 00:00 — 같은 시작
                first = listed if tf != "1m" else last - 10 * 60_000
                times = sorted({first, last})
                store.upsert_candles(Candle(sym, tf, t, 1, 1, 1, 1, 1) for t in times)
    with UniverseStore(db) as u:
        t = rebalance_at(NOW)
        u.save(
            RotationSnapshot(
                rebalance_ms=t,
                computed_ms=t,
                pool_size=2,
                stale_size=0,
                ranked=tuple((s, 1.0) for s in SYMS),
                top_n=31,
            )
        )


def test_ready_when_settings_list_and_data_are_in_place(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _seed(db)
    check = ac.run_check(_arm_settings(db), db, now_ms=NOW)
    assert check.ready, ac.render(check)
    assert check.members == SYMS and check.notes == []
    assert "준비됨 ✅" in ac.render(check)


def test_each_blocker_is_reported(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    _seed(db, history_days=10, daily_days=40, stale=("AAA/USDT:USDT", "4h"))
    check = ac.run_check(
        _arm_settings(db, live_arm_enabled=False, universe_enabled=False), db, now_ms=NOW
    )
    assert not check.ready
    assert any("ALPHABLOCK_LIVE_ARM_ENABLED" in p for p in check.settings_problems)
    assert any("UNIVERSE_ENABLED" in p for p in check.settings_problems)
    stale = [i for i in check.pair_issues if i.problem.startswith("마지막 봉")]
    assert [(i.symbol, i.timeframe) for i in stale] == [("AAA/USDT:USDT", "4h")]
    short = {i.timeframe for i in check.pair_issues if i.problem.startswith("이력")}
    assert short == set(ac.CHECK_TIMEFRAMES) - {"1m", "1d"}  # 1m은 이력을 안 본다 · 1d는 충분


def test_listed_late_symbol_is_enough_from_listing(tmp_path: Path) -> None:
    """상장이 목표보다 늦으면 상장부터 있으면 된다(거래소가 더 줄 게 없다)."""
    db = tmp_path / "c.db"
    _seed(db, history_days=10)
    with OhlcvStore(db) as store:
        issues = ac.pair_issues(store, SYMS, now_ms=NOW, history_days=30)
    # 1d 첫 봉이 곧 상장 — 모든 TF가 그때부터 있으니 이력 문제는 없다.
    assert [i for i in issues if i.problem.startswith("이력")] == []


def test_missing_list_and_leftovers(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    OhlcvStore(db).close()
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE open_positions (symbol TEXT)")
    conn.execute("INSERT INTO open_positions VALUES ('X')")
    conn.commit()
    conn.close()
    check = ac.run_check(_arm_settings(db), db, now_ms=NOW)
    assert check.list_missing and not check.ready
    assert check.leftovers == {"open_positions": 1}
    assert "리셋 이음매" in ac.render(check)
