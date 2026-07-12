"""운영 상태(Health) 데이터 접근 + 뷰 조립 (WAN-30).

SQLite(OHLCV·펀딩)와 러너 상태파일을 읽어 `dashboard.health`의 순수 판정 함수에
넘기고, 대시보드가 그대로 그릴 수 있는 `HealthView`로 조립한다.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict

from common.heartbeat import HeartbeatStore
from dashboard.health import (
    CollectorStatus,
    FundingFreshness,
    OverallBadge,
    RunnerStatus,
    SeriesFreshness,
    compute_collector_status,
    compute_freshness,
    compute_funding_status,
    compute_overall,
    compute_runner_status,
)
from data.funding import FundingRateStore
from data.repair import RepairStateStore, RepairSummary
from data.storage import OhlcvStore
from live.runtime_state import EventRecord, PositionSnapshot, RunnerRuntimeState, RuntimeStateStore
from strategy.models import OrderBlockDirection


def _now_ms() -> int:
    return int(time.time() * 1000)


class OpenPositionView(BaseModel):
    """오픈 페이퍼 포지션 + 현재가 기준 미실현 손익."""

    model_config = ConfigDict(frozen=True)

    snapshot: PositionSnapshot
    current_price: float | None
    unrealized_pct: float | None


class HealthView(BaseModel):
    """Health 탭이 그리는 데 필요한 모든 상태를 담은 뷰 모델."""

    model_config = ConfigDict(frozen=True)

    now_ms: int
    overall: OverallBadge
    freshness: list[SeriesFreshness]
    funding: list[FundingFreshness]
    collector: CollectorStatus
    runner: RunnerStatus
    positions: list[OpenPositionView]
    recent_events: list[EventRecord]
    last_repair: RepairSummary | None = None
    """마지막 갭 복구 요약(WAN-35). 복구를 한 번도 안 돌렸으면 None."""


def series_freshness_rows(store: OhlcvStore) -> list[tuple[str, str, int | None, int]]:
    """저장된 시리즈별 `(symbol, timeframe, last_open_time, bar_count)`을 모은다."""
    rows: list[tuple[str, str, int | None, int]] = []
    for symbol, timeframe in store.list_series():
        rows.append(
            (
                symbol,
                timeframe,
                store.last_open_time(symbol, timeframe),
                store.count(symbol, timeframe),
            )
        )
    return rows


def funding_rows(
    store: FundingRateStore, symbols: list[str]
) -> list[tuple[str, float | None, int | None, int | None, bool]]:
    """심볼별 최신 펀딩비 `(symbol, rate, funding_time, next_funding_time, is_predicted)`."""
    rows: list[tuple[str, float | None, int | None, int | None, bool]] = []
    for symbol in symbols:
        latest = store.latest(symbol)
        if latest is None:
            rows.append((symbol, None, None, None, False))
        else:
            rows.append(
                (
                    symbol,
                    latest.rate,
                    latest.funding_time,
                    latest.next_funding_time,
                    latest.is_predicted,
                )
            )
    return rows


def _latest_close(store: OhlcvStore, symbol: str, timeframe: str) -> float | None:
    """해당 시리즈 최신 봉의 종가. 없으면 None."""
    df = store.load(symbol, timeframe)
    if df.empty:
        return None
    return float(df["close"].iloc[-1])


def _unrealized_pct(snapshot: PositionSnapshot, price: float) -> float:
    """진입가 대비 방향을 반영한 미실현 손익률(%)."""
    sign = 1.0 if snapshot.direction is OrderBlockDirection.BULLISH else -1.0
    return sign * (price - snapshot.entry_price) / snapshot.entry_price * 100.0


def build_position_views(
    store: OhlcvStore, positions: list[PositionSnapshot]
) -> list[OpenPositionView]:
    """오픈 포지션에 현재가·미실현 손익을 붙여 뷰로 만든다."""
    views: list[OpenPositionView] = []
    for pos in positions:
        price = _latest_close(store, pos.symbol, pos.timeframe)
        pnl = _unrealized_pct(pos, price) if price is not None else None
        views.append(OpenPositionView(snapshot=pos, current_price=price, unrealized_pct=pnl))
    return views


def build_health_view(
    db_path: str,
    *,
    runtime_state_path: str,
    poll_interval_seconds: float,
    stale_multiplier: float,
    collector_heartbeat_path: str | None = None,
    collector_heartbeat_interval_seconds: float = 60.0,
    funding_symbols: list[str] | None = None,
    repair_state_path: str | None = None,
    now_ms: int | None = None,
) -> HealthView:
    """DB·상태파일을 읽어 완성된 `HealthView`를 조립한다."""
    now = now_ms if now_ms is not None else _now_ms()

    with OhlcvStore(db_path) as ohlcv:
        fresh_rows = series_freshness_rows(ohlcv)
        symbols = funding_symbols
        if symbols is None:
            symbols = sorted({symbol for symbol, _, _, _ in fresh_rows})

        with FundingRateStore(db_path) as funding_store:
            fund_rows = funding_rows(funding_store, symbols)

        runtime: RunnerRuntimeState = RuntimeStateStore(runtime_state_path).load()
        positions = build_position_views(ohlcv, runtime.open_positions)

    last_beat_ms = (
        HeartbeatStore(collector_heartbeat_path).load().updated_at
        if collector_heartbeat_path is not None
        else None
    )

    freshness = compute_freshness(fresh_rows, now_ms=now, stale_multiplier=stale_multiplier)
    funding = compute_funding_status(fund_rows, now_ms=now, stale_multiplier=stale_multiplier)
    collector = compute_collector_status(
        last_beat_ms=last_beat_ms,
        now_ms=now,
        heartbeat_interval_seconds=collector_heartbeat_interval_seconds,
        stale_multiplier=stale_multiplier,
    )
    runner = compute_runner_status(
        last_poll_ms=runtime.updated_at,
        last_notification_ms=runtime.last_notification_at,
        now_ms=now,
        poll_interval_seconds=poll_interval_seconds,
        stale_multiplier=stale_multiplier,
    )
    overall = compute_overall(freshness, funding, runner, collector)

    last_repair: RepairSummary | None = (
        RepairStateStore(repair_state_path).load() if repair_state_path is not None else None
    )

    return HealthView(
        now_ms=now,
        overall=overall,
        freshness=freshness,
        funding=funding,
        collector=collector,
        runner=runner,
        positions=positions,
        recent_events=list(reversed(runtime.recent_events)),
        last_repair=last_repair,
    )
