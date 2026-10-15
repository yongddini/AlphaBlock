"""스토캐스틱 팔 진입·청산 규칙(WAN-451 ③) — 백테스트 정본과 같은 판정 · 러너 배선."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from backtest import wan439_crash_size_layer as w439
from common.costs import Liquidity
from config.settings import Settings
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import EntryIntent, build_execution_engine
from live import arm_rules as ar
from live.executor import PaperExecutor
from live.limit_engine import EngineEvent, ZoneLimitLiveEngine
from live.limit_orders import LimitFill, PendingLimitOrder
from live.order_journal import OrderJournal
from live.zone_limit_runner import (
    ARM_RISK_PER_TRADE,
    ZoneLimitPaperRunner,
    arm_settings_problems,
    build_paper_recorder,
    engine_settings_for,
)
from paper.store import PaperTradeStore, adopted_exit_liquidity
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

M = 60_000
TF = 15 * M
P = ar.ArmParams()


def _decide(
    *,
    entry: float = 100.0,
    ob_stop: float = 95.0,
    k: float | None = 10.0,
    btc_drop: float = 0.0,
    entry_ms: int = 100 * TF + 3 * M,
) -> ar.ArmDecision:
    times = [i * TF for i in range(100)]  # 확정봉 0..99, 진입은 봉 100 안
    ks: list[float | None] = [k] * 100
    minutes = list(range(0, entry_ms, M))
    closes = [100.0] * len(minutes)
    if btc_drop:
        closes[-1] = 100.0 * (1.0 + btc_drop)
    return ar.evaluate_arm_fill(
        P,
        entry_price=entry,
        ob_stop=ob_stop,
        entry_ms=entry_ms,
        tf_ms=TF,
        htf_closed_times=times,
        htf_closed_k=ks,
        btc_minute_times=minutes,
        btc_minute_closes=closes,
    )


def test_accepts_with_double_stop_and_no_crash() -> None:
    d = _decide()
    assert d.accepted and d.stop_price == pytest.approx(90.0) and d.size_scale == 1.0
    assert d.stop_width == pytest.approx(0.05) and d.k_value == 10.0


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"ob_stop": 96.5}, ar.REJECT_CODE_ARM_STOP_FLOOR),  # 3.5% < 4%
        ({"k": 25.0}, ar.REJECT_CODE_ARM_STOCH),  # 엄격 < 25
        ({"k": None}, ar.REJECT_CODE_ARM_STOCH_UNKNOWN),
        ({"ob_stop": 49.0}, ar.REJECT_CODE_ARM_STOP_BELOW_ZERO),  # 2배 손절가 −2
    ],
)
def test_rejections_in_backtest_order(kwargs: dict[str, object], code: str) -> None:
    d = _decide(**kwargs)  # type: ignore[arg-type]
    assert not d.accepted and d.reason_code == code and d.reason


def test_floor_is_inclusive_and_checked_before_k() -> None:
    assert _decide(ob_stop=96.0).accepted  # 정확히 4%
    assert _decide(ob_stop=96.5, k=None).reason_code == ar.REJECT_CODE_ARM_STOP_FLOOR


def test_crash_multiplier_inclusive_threshold() -> None:
    assert _decide(btc_drop=-0.05).size_scale == 0.5
    assert _decide(btc_drop=-0.049).size_scale == 1.0


def test_btc_lookback_matches_backtest_definition() -> None:
    rng = np.random.default_rng(3)
    times = sorted({int(t) * M for t in rng.integers(0, 4000, 3000)})
    closes = list(map(float, 100 * np.cumprod(1 + rng.normal(0, 0.001, len(times)))))
    for entry in map(int, rng.integers(1500, 4000, 200)):
        entry_ms = entry * M
        mine = ar.btc_lookback_return(times, closes, entry_ms, 1440)
        theirs = w439.btc_lookback_return(np.array(times), np.array(closes), entry_ms, 1440)
        assert (mine is None and np.isnan(theirs)) or mine == pytest.approx(theirs, abs=0)


def test_time_exit_last_minute_before_fourth_bar_and_weekly_epoch() -> None:
    entry = 10 * TF + 7 * M  # 봉 10 안
    assert ar.time_exit_boundary(entry, TF, 4) == 14 * TF
    assert not ar.is_time_exit_minute(14 * TF - 2 * M, entry, TF, 4)
    assert ar.is_time_exit_minute(14 * TF - M, entry, TF, 4)
    week = 7 * 86_400_000
    assert ar.time_exit_boundary(5 * 86_400_000, week, 4) % week == 0  # epoch 주(목요일) 경계


def test_settings_check_lists_what_to_set() -> None:
    problems = arm_settings_problems(Settings())
    joined = "\n".join(problems)
    for key in (
        "RETAP_MODE=once",
        "FILL_PENETRATION_BPS=5",
        "RISK_PER_TRADE=",
        "UNIVERSE_ENABLED=true",
    ):
        assert key in joined
    ok = Settings(
        live_arm_enabled=True,
        confluence={"retap_mode": "once", "fill_penetration_bps": 5.0},
        risk_sizing={"risk_per_trade": ARM_RISK_PER_TRADE},
        universe_enabled=True,
    )
    assert arm_settings_problems(ok) == []
    assert engine_settings_for(ok).risk_limits.daily_loss_limit_fraction is None


def test_removed_old_paper_keys_are_harmless(monkeypatch: pytest.MonkeyPatch) -> None:
    """옛 오더블록 페이퍼의 설정 키(틱 피드 · 재진입)는 WAN-451 옛 규칙 정리로 없어졌다.

    서버 `.env`에 남아 있어도 기동이 죽으면 안 된다 — 환경변수·생성자 인자 모두 무시된다."""
    monkeypatch.setenv("ALPHABLOCK_LIVE_TICK_FEED_ENABLED", "true")
    monkeypatch.setenv("ALPHABLOCK_LIVE_TICK_FEED_EXCHANGE", "binanceusdm")
    monkeypatch.setenv("ALPHABLOCK_LIVE_REENTRY_ENTRY_RULE", "band")
    settings = Settings(live_reentry_entry_rule="off")
    for gone in ("live_tick_feed_enabled", "live_tick_feed_exchange", "live_reentry_entry_rule"):
        assert not hasattr(settings, gone)


def test_time_exit_is_taker_like_backtest_end_of_data() -> None:
    assert adopted_exit_liquidity(SignalExitReason.TIME_EXIT) is Liquidity.TAKER


def test_risk_scale_halves_the_position() -> None:
    engine_a = build_execution_engine(Settings())
    engine_b = build_execution_engine(Settings())
    base = dict(
        symbol="X",
        timeframe="15m",
        direction=OrderBlockDirection.BULLISH,
        entry_price=100.0,
        entry_time=0,
        stop_price=90.0,
    )
    full = engine_a.on_entry(EntryIntent(**base), now_ms=0)
    half = engine_b.on_entry(EntryIntent(**base, risk_scale=0.5), now_ms=0)
    assert full.position is not None and half.position is not None
    assert half.position.quantity == pytest.approx(full.position.quantity * 0.5)


# --------------------------------------------------------------------- 러너 배선


def _runner(db: Path) -> tuple[ZoneLimitPaperRunner, OrderJournal, int]:
    settings = Settings(db_path=str(db))
    paper = PaperTradeStore(db)
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper,
        recorder=build_paper_recorder(paper, settings),
        sizing=settings.risk_sizing,
    )
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    runner = ZoneLimitPaperRunner(
        store=OhlcvStore(db),
        engine=ZoneLimitLiveEngine(params=settings.confluence, journal=journal, session_id=session),
        journal=journal,
        session_id=session,
        executor=executor,
        params=settings.confluence,
        series=[],
        lookback_bars=100,
        poll_interval_seconds=0,
        arm=ar.ArmParams(),
    )
    return runner, journal, session


def _seed(db: Path, *, falling: bool) -> None:
    """15m 확정봉 60개(하락이면 %K 낮음) + BTC 1분봉 하루치."""
    with OhlcvStore(db) as store:
        closes = [200.0 - i if falling else 100.0 + i for i in range(60)]
        store.upsert_candles(
            Candle("SYN/USDT:USDT", "15m", i * TF, c + 0.5, c + 1.0, c - 1.0, c, 1.0)
            for i, c in enumerate(closes)
        )
        store.upsert_candles(
            Candle("BTC/USDT:USDT", "1m", t, 100.0, 100.0, 100.0, 100.0, 1.0)
            for t in range(0, 60 * TF + 10 * M, M)
        )


def _fill_event(
    journal: OrderJournal, session: int, *, price: float, ob_stop: float
) -> EngineEvent:
    order = PendingLimitOrder(
        symbol="SYN/USDT:USDT",
        timeframe="15m",
        direction=OrderBlockDirection.BULLISH,
        limit_price=price,
        stop_price=ob_stop,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=60 * TF,
    )
    order.journal_id = journal.record_placed(
        order, session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    fill = LimitFill(
        symbol="SYN/USDT:USDT",
        timeframe="15m",
        direction=OrderBlockDirection.BULLISH,
        price=price,
        time=60 * TF + 2 * M,
        rsi=None,
        stop_price=ob_stop,
        take_profit_price=price * 1.1,
        penetration_bps=6.0,
        waited_ms=0,
    )
    journal.record_filled(order.journal_id, fill)
    return EngineEvent(
        kind="filled",
        symbol="SYN/USDT:USDT",
        timeframe="15m",
        time_ms=fill.time,
        order=order,
        fill=fill,
    )


def test_runner_enters_with_arm_stop_then_time_exits(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _seed(db, falling=True)
    runner, journal, session = _runner(db)
    runner._handle_events([_fill_event(journal, session, price=140.0, ob_stop=133.0)])  # 5%
    (pos,) = runner._executor.open_positions
    assert pos.stop_price == pytest.approx(126.0)  # 140 − 2×7
    assert pos.take_profit_price is None  # 팔은 익절이 없다
    boundary = 64 * TF
    runner._check_exits(
        "SYN/USDT:USDT", "15m", low=139.0, high=141.0, close=139.5, time_ms=boundary - 2 * M
    )
    assert runner._executor.open_positions
    runner._check_exits(
        "SYN/USDT:USDT", "15m", low=139.0, high=141.0, close=139.5, time_ms=boundary - M
    )
    assert not runner._executor.open_positions
    reason = sqlite3.connect(db).execute("SELECT reason, exit_price FROM paper_trades").fetchone()
    assert reason == ("time_exit", 139.5)


def test_runner_records_arm_rejection_with_its_code(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    _seed(db, falling=False)  # 상승 → %K 높음
    runner, journal, session = _runner(db)
    runner._handle_events([_fill_event(journal, session, price=140.0, ob_stop=133.0)])
    assert not runner._executor.open_positions
    row = (
        sqlite3.connect(db)
        .execute("SELECT entry_status, entry_reject_code FROM live_limit_orders")
        .fetchone()
    )
    assert row == ("rejected", ar.REJECT_CODE_ARM_STOCH)
