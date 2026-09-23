"""WAN-422: 페이퍼 장부 펀딩 0 · `status`의 예측 행 [OK] · 확정 이력의 기동-전용 수집.

세 결함이 한 사슬이다:

1. 수집기는 확정 펀딩 이력을 **기동 백필 때만** 받았다(`refresh_funding`은 다음 정산의
   예측값만 저장). 그래서 「마지막 확정 = 마지막 기동」이 되고 그 뒤 정산은 예측 행으로만
   남는다.
2. 페이퍼 장부는 청산 순간 **확정만** 읽어(`include_predicted=False`) 그 정산들을 조용히
   0으로 냈다 — 롱 온리라 성과가 좋게 나오는 방향이다(2026-09-01~17 186거래 전부 0).
3. `status`/`watch`는 최신 행을 예측 포함으로 읽어 **미래 시각**으로 지연을 쟀다 — 확정이
   12일 멈춰도 12종목 전부 `[OK]`였다.

테스트는 전부 **라벨이 아니라 동작**으로 건다(정산을 넘긴 거래에 실제로 펀딩이 붙는가 ·
확정이 멈추면 실제로 STALE이 뜨는가 · 수집 루프가 실제로 확정 행을 쌓는가).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from config.settings import Settings
from dashboard.health import FUNDING_INTERVAL_MS, HealthLevel, compute_funding_status
from dashboard.health_data import funding_rows
from data.collector import report_funding_task_exit
from data.funding import (
    FundingRateStore,
    run_funding_refresh,
    settled_funding_rates,
)
from data.models import FundingRate
from execution.engine import EntryIntent, build_execution_engine
from execution.leverage import book_per_trade_sizing
from live.executor import PaperExecutor
from live.zone_limit_runner import build_paper_recorder
from paper.store import PaperTradeStore
from strategy.models import SignalExitReason

SYMBOL = "BTC/USDT:USDT"
H8 = 8 * 3_600_000
#: 8시간 경계에 정렬된 정산 시각(에폭 배수).
BASE = 2_235 * H8
RATE = 0.001  # 정산당 0.1% — 3정산이면 0.3%


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "wan422.db"


@pytest.fixture
def funding_store(db_path: Path) -> Iterator[FundingRateStore]:
    store = FundingRateStore(db_path)
    try:
        yield store
    finally:
        store.close()


def _predicted(t: int, rate: float = RATE) -> FundingRate:
    """수집기 `refresh_funding`이 저장하는 모양 — 정각 · 예측."""
    return FundingRate(symbol=SYMBOL, funding_time=t, rate=rate, is_predicted=True)


def _confirmed(t: int, rate: float = RATE, *, late_ms: int = 1) -> FundingRate:
    """거래소 이력 API가 주는 모양 — 정각보다 1~5ms 늦은 타임스탬프 · 확정(PM 진단)."""
    return FundingRate(symbol=SYMBOL, funding_time=t + late_ms, rate=rate, is_predicted=False)


def _round_trip(db_path: Path, funding_store: FundingRateStore) -> tuple[float, float]:
    """실제 러너 경로(PaperExecutor → 엔진 → 채택 장부 기록기)로 한 거래를 돌린다.

    진입 = 첫 정산 1시간 전, 청산 = 셋째 정산 1분 뒤 → 정산 3개를 넘는 롱.
    """
    settings = Settings(db_path=str(db_path))
    paper_store = PaperTradeStore(db_path)
    recorder = build_paper_recorder(paper_store, settings, funding_store=funding_store)
    book = settings.live_leverage_book
    executor = PaperExecutor(
        engine=build_execution_engine(settings, leverage_book=book),
        store=paper_store,
        recorder=recorder,
        sizing=book_per_trade_sizing(settings.risk_sizing, book),
    )
    t_in = BASE - 3_600_000
    report = executor.enter(
        EntryIntent(
            symbol=SYMBOL,
            timeframe="1h",
            direction="bull",
            entry_price=100.0,
            entry_time=t_in,
            stop_price=98.0,
            take_profit_price=103.0,
        ),
        now_ms=t_in,
    )
    assert report.accepted
    t_out = BASE + 2 * H8 + 60_000
    report = executor.exit(
        SYMBOL,
        "1h",
        exit_price=103.0,
        exit_time=t_out,
        reason=SignalExitReason.TAKE_PROFIT,
        now_ms=t_out,
    )
    assert report.accepted
    [row] = paper_store._conn.execute("SELECT funding_pct, net_pct FROM paper_trades").fetchall()
    return float(row[0]), float(row[1])


# --------------------------------------------------------------------------- #
# §1 — 장부가 정산을 넘긴 거래에 펀딩을 문다
# --------------------------------------------------------------------------- #


def test_ledger_charges_settlements_that_only_exist_as_predicted_rows(
    db_path: Path, funding_store: FundingRateStore
) -> None:
    """🚨 사고 재현: 청산 순간 정산 3개가 **예측 행으로만** 있다(확정 이력 미도착).

    옛 코드(확정만 읽음)는 여기서 `funding_pct = 0`을 냈다 — 186거래가 그랬다.
    """
    funding_store.upsert_rates([_predicted(BASE + k * H8) for k in range(3)])
    funding_pct, _ = _round_trip(db_path, funding_store)
    assert funding_pct == pytest.approx(0.3)


def test_ledger_counts_each_settlement_once_when_both_rows_exist(
    db_path: Path, funding_store: FundingRateStore
) -> None:
    """확정(+1~5ms)과 예측(정각)이 둘 다 남은 정산은 **확정으로 한 번만** 센다.

    기본키가 1ms 차로 두 행을 만들어도(PM 진단 · §3-a는 별건) 이중 계상하지 않는다 —
    그리고 두 값이 다르면 **확정값**을 쓴다.
    """
    rows = []
    for k in range(3):
        rows.append(_predicted(BASE + k * H8, rate=0.009))  # 예측은 틀린 값
        rows.append(_confirmed(BASE + k * H8, late_ms=k + 1))  # 확정이 이긴다
    funding_store.upsert_rates(rows)
    funding_pct, _ = _round_trip(db_path, funding_store)
    assert funding_pct == pytest.approx(0.3)


def test_ledger_mixes_confirmed_and_predicted_per_settlement(
    db_path: Path, funding_store: FundingRateStore
) -> None:
    """앞 두 정산은 확정이 왔고 마지막 정산만 예측으로 남은 흔한 경우."""
    funding_store.upsert_rates(
        [
            _confirmed(BASE),
            _confirmed(BASE + H8, late_ms=4),
            _predicted(BASE + H8),
            _predicted(BASE + 2 * H8),
        ]
    )
    funding_pct, net_pct = _round_trip(db_path, funding_store)
    assert funding_pct == pytest.approx(0.3)
    # 순손익이 펀딩만큼 실제로 줄었다(지갑 정산 = 장부 행, WAN-392).
    no_funding_pct, no_funding_net = _round_trip_without_funding(db_path.parent)
    assert no_funding_pct == 0.0
    assert net_pct == pytest.approx(no_funding_net - 0.3)


def _round_trip_without_funding(tmp: Path) -> tuple[float, float]:
    db = tmp / "no-funding.db"
    store = FundingRateStore(db)
    try:
        return _round_trip(db, store)
    finally:
        store.close()


def test_settled_rates_excludes_settlements_outside_the_holding_window(
    funding_store: FundingRateStore,
) -> None:
    """보유 구간 `[진입, 청산)` 밖 정산(다음 정산의 미래 예측 포함)은 안 센다."""
    funding_store.upsert_rates([_confirmed(BASE - H8), _predicted(BASE), _predicted(BASE + H8)])
    picked = settled_funding_rates(funding_store, SYMBOL, start_ms=BASE - 60_000, end_ms=BASE + 1)
    assert [r.funding_time for r in picked] == [BASE]


# --------------------------------------------------------------------------- #
# §2 — 판정은 확정 행만 본다 (예측 행은 표시만)
# --------------------------------------------------------------------------- #


def test_status_is_stale_when_confirmed_stops_even_with_fresh_predicted(
    funding_store: FundingRateStore,
) -> None:
    """🚨 사고 재현: 확정이 12일 멈췄고 예측 행만 매 주기 미래로 갱신된다 → STALE.

    옛 판정(최신 행 = 예측으로 지연을 잼)은 여기서 OK를 냈다.
    """
    now = BASE + 12 * 3 * H8
    funding_store.upsert_rates([_confirmed(BASE), _predicted(now + H8 // 2)])
    [fresh] = compute_funding_status(
        funding_rows(funding_store, [SYMBOL]), now_ms=now, stale_multiplier=2.5
    )
    assert fresh.level is HealthLevel.STALE
    # 표시는 그대로 최신(예측) 행이다 — 점검 항목을 줄인 게 아니다.
    assert fresh.is_predicted is True
    assert fresh.funding_time == now + H8 // 2
    assert fresh.confirmed_funding_time == BASE + 1
    assert fresh.lag_ms == now - (BASE + 1)


def test_status_is_ok_when_confirmed_is_recent(funding_store: FundingRateStore) -> None:
    now = BASE + 2 * H8 + 600_000
    funding_store.upsert_rates([_confirmed(BASE + 2 * H8), _predicted(BASE + 3 * H8)])
    [fresh] = compute_funding_status(
        funding_rows(funding_store, [SYMBOL]), now_ms=now, stale_multiplier=2.5
    )
    assert fresh.level is HealthLevel.OK


def test_status_is_stale_when_only_predicted_rows_exist(funding_store: FundingRateStore) -> None:
    """확정 이력이 한 번도 안 들어왔으면 STALE — 확정만 읽는 소비자에겐 펀딩이 없다."""
    funding_store.upsert_rates([_predicted(BASE + H8)])
    [fresh] = compute_funding_status(
        funding_rows(funding_store, [SYMBOL]), now_ms=BASE, stale_multiplier=2.5
    )
    assert fresh.level is HealthLevel.STALE
    assert fresh.confirmed_funding_time is None


def test_status_is_unknown_when_nothing_is_stored(funding_store: FundingRateStore) -> None:
    [fresh] = compute_funding_status(
        funding_rows(funding_store, [SYMBOL]), now_ms=BASE, stale_multiplier=2.5
    )
    assert fresh.level is HealthLevel.UNKNOWN


def test_watch_alert_names_the_confirmed_time() -> None:
    """텔레그램 경고가 예측 시각이 아니라 **마지막 확정 정산**을 말한다."""
    from live.health_watch import evaluate_alerts
    from tests.test_wan407_funding_visibility import _view_with_funding

    now = BASE + 40 * FUNDING_INTERVAL_MS
    [fresh] = compute_funding_status(
        [(SYMBOL, RATE, now + H8, now + H8, True, BASE + 1)], now_ms=now, stale_multiplier=2.5
    )
    alerts = [a for a in evaluate_alerts(_view_with_funding([fresh])) if a.key.startswith("fund")]
    assert len(alerts) == 1
    assert "마지막 확정 정산" in alerts[0].detail


# --------------------------------------------------------------------------- #
# §3 — 확정 이력을 매 주기 이어받는다 · 태스크 종료는 조용하지 않다
# --------------------------------------------------------------------------- #


def _loop_settings() -> Settings:
    # 저장된 확정이 없을 때의 시작점을 합성 정산(`BASE`)보다 앞에 둔다(기본은 30일 룩백).
    return Settings(
        symbols=[SYMBOL], funding_refresh_interval_seconds=0, funding_backfill_start="1971-01-01"
    )


class _GrowingHistoryExchange:
    """사이클마다 새 정산이 하나씩 확정되는 거래소(이력은 정각+2ms로 준다)."""

    def __init__(self) -> None:
        self.settled = 1

    def fetch_funding_rate(
        self, symbol: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        nxt = BASE + self.settled * H8
        return {
            "fundingRate": RATE,
            "fundingTimestamp": nxt,
            "nextFundingTimestamp": nxt,
            "markPrice": 100.0,
        }

    def fetch_funding_rate_history(
        self,
        symbol: str = SYMBOL,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = [
            {"symbol": SYMBOL, "timestamp": BASE + k * H8 + 2, "fundingRate": RATE}
            for k in range(self.settled)
        ]
        return [r for r in rows if since is None or int(r["timestamp"]) >= since]


def test_refresh_loop_accumulates_confirmed_history_without_restart(
    funding_store: FundingRateStore,
) -> None:
    """수집기 기동 뒤(`backfill=False` — 수집기가 실제로 부르는 형태) 새로 확정된 정산이
    **재시작 없이** 쌓인다. 옛 루프는 예측 행만 저장해 확정 수가 그대로였다."""
    exchange = _GrowingHistoryExchange()
    settings = _loop_settings()

    async def advance(_: float) -> None:
        exchange.settled += 1  # 사이클 사이에 정산이 하나씩 지나간다

    asyncio.run(
        run_funding_refresh(
            settings,
            exchange=exchange,
            store=funding_store,
            backfill=False,
            max_cycles=3,
            sleeper=advance,
        )
    )
    confirmed = [r for r in funding_store.get_rates(SYMBOL, include_predicted=False)]
    assert len(confirmed) == 3
    assert funding_store.last_funding_time(SYMBOL, confirmed_only=True) == BASE + 2 * H8 + 2


def test_refresh_loop_opt_out_keeps_old_boot_only_behaviour(
    funding_store: FundingRateStore,
) -> None:
    exchange = _GrowingHistoryExchange()
    settings = _loop_settings()

    async def advance(_: float) -> None:
        exchange.settled += 1

    asyncio.run(
        run_funding_refresh(
            settings,
            exchange=exchange,
            store=funding_store,
            backfill=False,
            confirmed_catchup=False,
            max_cycles=3,
            sleeper=advance,
        )
    )
    assert funding_store.get_rates(SYMBOL, include_predicted=False) == []


def test_catchup_failure_does_not_kill_the_refresh_loop(
    funding_store: FundingRateStore, caplog: pytest.LogCaptureFixture
) -> None:
    """이력 조회가 죽어도 루프는 돌고(예측 갱신 계속) 실패는 ERROR로 남는다."""

    class _BrokenHistory(_GrowingHistoryExchange):
        def fetch_funding_rate_history(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
            raise RuntimeError("history down")

    exchange = _BrokenHistory()
    settings = _loop_settings()

    async def noop(_: float) -> None:
        return None

    with caplog.at_level(logging.ERROR, logger="data.funding"):
        asyncio.run(
            run_funding_refresh(
                settings,
                exchange=exchange,
                store=funding_store,
                backfill=False,
                max_cycles=2,
                sleeper=noop,
            )
        )
    assert funding_store.count(SYMBOL) == 1  # 예측 행은 계속 저장됐다
    assert any("이어받기 실패" in r.getMessage() for r in caplog.records)


def test_funding_task_death_is_logged_as_error(caplog: pytest.LogCaptureFixture) -> None:
    async def boom() -> None:
        raise RuntimeError("funding loop died")

    async def run() -> None:
        task: asyncio.Task[None] = asyncio.create_task(boom())
        task.add_done_callback(report_funding_task_exit)
        with pytest.raises(RuntimeError):
            await task

    with caplog.at_level(logging.ERROR, logger="data.collector"):
        asyncio.run(run())
    assert any("예외로 죽었습니다" in r.getMessage() for r in caplog.records)


def test_funding_task_cancel_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """수집기 정상 종료 때의 취소는 경보가 아니다."""

    async def run() -> None:
        task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(10))
        task.add_done_callback(report_funding_task_exit)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger="data.collector"):
        asyncio.run(run())
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
