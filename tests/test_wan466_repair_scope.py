"""수집기 시작 갭 복구도 「지금 수집 계획」으로 판정한다 (WAN-466) 회귀 테스트.

2026-10-20 WAN-461 배포로 수집기를 재시작하자 텔레그램에 「🚨 수집 정지(꼬리 신선도)」가
BR·LSK·ONE × 8TF = 24줄로 왔다. 셋 다 10-19 주간 교체로 팔 목록에서 빠져 **일부러 안 모으는**
종목이다. WAN-458이 감시 세 곳(`status`·Health·`watch`)만 수집 계획으로 좁혔고, 수집기 시작
복구(`repair_all`)는 여전히 DB의 **모든** 시리즈를 판정했다.

이 파일은 **동작**을 고정한다(라벨이 아니라):

* (a) 수집기 시작 → 목록에서 빠진 종목은 텔레그램에 안 나간다(정보로만 상태 파일에 남는다).
* (b) 같은 실행에서 목록에 있는 종목이 멈췄으면 **여전히 경보가 간다**.
* (c) 계획을 못 읽으면(또는 옛 코드처럼 범위를 안 넘기면) 옛 동작 — 빠진 종목도 경보된다.
  즉 (a)는 범위 배선이 실제로 걸려야만 통과한다.
* (d) `alphablock backfill --repair`(`run_repair`)도 같은 범위를 쓰고, 그때 팔 TF(3h 등)를
  설정 TF 필터로 숨기지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from config.settings import Settings
from data.collection_plan import MonitorScope
from data.collector import run_collector
from data.models import Candle
from data.repair import (
    RepairStateStore,
    RepairSummary,
    UntrackedSeries,
    format_plan_excluded,
    repair_all,
    run_repair,
)
from data.storage import OhlcvStore
from data.universe_pool import RotationSnapshot, UniverseStore

HOUR = 3_600_000
DAY = 24 * HOUR

BTC = "BTC/USDT:USDT"  # 기본 격자
KEEP = "KEEP/USDT:USDT"  # 이번 주도 목록 — 여기서 멈추면 진짜 정지다
DROPPED = "ONE/USDT:USDT"  # 이번 주 빠짐(사고 종목 자리)


def _now() -> int:
    """수집기는 실제 시각으로 판정하므로 픽스처도 실제 시각 기준으로 만든다."""
    return int(time.time() * 1000) // HOUR * HOUR


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


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "db_path": str(tmp_path / "ohlcv.db"),
        "symbols": [BTC],
        "timeframes": ["1h"],
        "universe_enabled": True,
        "funding_enabled": False,
        "repair_state_path": str(tmp_path / "repair_state.json"),
        "collector_heartbeat_path": str(tmp_path / "hb.json"),
        "telegram_bot_token": "",
        "telegram_chat_id": "",
    }
    base.update(overrides)
    return Settings(**base)


def _seed(tmp_path: Path, *, keep_stalled: bool, rotations: bool = True) -> int:
    now = _now()
    t2 = now - 3 * HOUR  # 이번 주 리밸런싱
    t1 = t2 - 7 * DAY
    db = tmp_path / "ohlcv.db"
    keep_last = now - 10 * HOUR if keep_stalled else now - HOUR
    with OhlcvStore(db) as store:
        store.upsert_candles(
            [
                _bar(BTC, "1h", now - HOUR),
                _bar(KEEP, "1h", keep_last),
                # 수집기가 1h에서 접어 저장하는 팔 TF — 설정 TF(1h)에는 없지만 진짜 수집 대상이다.
                _bar(KEEP, "3h", now - 12 * HOUR if keep_stalled else now - 3 * HOUR),
                # 이번 주 빠진 종목 — 교체 시각 언저리부터 멈췄다(일부러 안 모은다).
                _bar(DROPPED, "1h", t2 - 7 * HOUR),
                _bar(DROPPED, "15m", t2 - 7 * HOUR),
                _bar(DROPPED, "4h", t2 - 8 * 4 * HOUR),
            ]
        )
    if rotations:
        with UniverseStore(db) as universe:
            for t, members in ((t1, [KEEP, DROPPED]), (t2, [KEEP])):
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
    return now


class _FakeTelegram:
    def __init__(self, sent: list[str]) -> None:
        self._sent = sent

    def send_message(self, text: str, **_: Any) -> bool:
        self._sent.append(text)
        return True


class _NoFetchExchange:
    """갭이 없어 호출되면 안 된다 — 불리면 테스트가 무엇을 재는지가 바뀐다."""

    def fetch_ohlcv(self, *args: Any, **kwargs: Any) -> list[list[float]]:
        raise AssertionError("이 픽스처에는 메울 내부 갭이 없다")


def _stub_collector(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """거래소·백필·펀딩을 걷어내되 **진짜** `repair_all`·`alert_on_failure`를 돌린다."""
    sent: list[str] = []
    monkeypatch.setattr("data.collector.create_exchange", lambda settings: _NoFetchExchange())
    monkeypatch.setattr("data.collector.backfill_all", lambda *a, **k: {})
    monkeypatch.setattr("data.repair.build_telegram_client", lambda settings: _FakeTelegram(sent))

    async def no_funding(settings: Settings, exchange: Any) -> None:
        return None

    monkeypatch.setattr("data.collector._backfill_funding", no_funding)
    return sent


# -- (a)(b) 수집기 시작 ----------------------------------------------------------


def test_collector_start_does_not_alert_dropped_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) 빠진 종목은 경보 없이 상태 파일에 「계획 밖」으로만 남는다 — 아무것도 안 울린다."""
    sent = _stub_collector(monkeypatch)
    _seed(tmp_path, keep_stalled=False)
    settings = _settings(tmp_path)

    asyncio.run(run_collector(settings, run_stream=False))

    assert sent == []
    saved = RepairStateStore(settings.repair_state_path).load()
    assert saved is not None
    assert saved.stale_series == []
    plan_excluded = {(u.symbol, u.timeframe) for u in saved.untracked_series if u.reason == "plan"}
    assert plan_excluded == {(DROPPED, "1h"), (DROPPED, "15m"), (DROPPED, "4h")}
    # 복구 대상에서도 빠졌다(빠진 종목의 갭을 매번 거래소에서 받지 않는다).
    assert DROPPED not in {s.symbol for s in saved.series}


def test_collector_start_still_alerts_a_stalled_listed_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) 목록에 있는 종목이 멈추면 경보가 간다 — 그리고 그 경보에 빠진 종목은 안 섞인다."""
    sent = _stub_collector(monkeypatch)
    _seed(tmp_path, keep_stalled=True)
    settings = _settings(tmp_path)

    asyncio.run(run_collector(settings, run_stream=False))

    assert len(sent) == 1
    assert "수집 정지" in sent[0]
    assert KEEP in sent[0]
    assert DROPPED not in sent[0]


def test_collector_start_falls_back_to_old_behaviour_without_a_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) 계획을 못 읽으면(그 주 목록 없음) 옛 동작 — 빠진 종목까지 판정한다.

    감시가 꺼지는 쪽으로 실패하면 안 된다(WAN-458과 같은 안전 규칙). 이 테스트는 동시에
    (a)가 「범위가 실제로 걸려서」 통과한다는 대조군이다 — 범위가 없으면 같은 DB가 울린다.
    """
    sent = _stub_collector(monkeypatch)
    _seed(tmp_path, keep_stalled=False, rotations=False)
    settings = _settings(tmp_path)

    asyncio.run(run_collector(settings, run_stream=False))

    assert len(sent) == 1
    assert DROPPED in sent[0]


def test_collector_start_without_scope_wiring_would_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c′) 옛 코드(수집기가 범위를 안 넘김)로 되돌리면 (a)의 DB가 그대로 울린다."""
    sent = _stub_collector(monkeypatch)
    _seed(tmp_path, keep_stalled=False)
    settings = _settings(tmp_path)
    monkeypatch.setattr("data.collector.monitor_scope_for", lambda settings: None)

    asyncio.run(run_collector(settings, run_stream=False))

    assert len(sent) == 1
    assert DROPPED in sent[0]


# -- (d) alphablock backfill --repair --------------------------------------------


def test_run_repair_uses_the_plan_and_keeps_arm_timeframes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(d) `run_repair`도 같은 범위 — 그리고 팔 TF(3h)를 설정 TF 필터로 숨기지 않는다.

    `settings.timeframes`(=기본 격자 TF ["1h"])로 거르면 KEEP 3h가 「미추적」으로 숨어 그
    정지가 안 보인다 — 계획이 읽히면 TF 필터를 걸지 않는다.
    """
    sent: list[str] = []
    monkeypatch.setattr("data.exchange.create_exchange", lambda settings: _NoFetchExchange())
    monkeypatch.setattr("data.repair.build_telegram_client", lambda settings: _FakeTelegram(sent))
    _seed(tmp_path, keep_stalled=True)
    settings = _settings(tmp_path)

    summary = run_repair(settings)

    stale = {(s.symbol, s.timeframe) for s in summary.stale_series}
    assert (KEEP, "3h") in stale  # 12시간 전 3h 봉 = 진짜 정지(팔 TF)
    assert (KEEP, "1h") in stale
    assert all(sym != DROPPED for sym, _ in stale)
    # 팔 TF(15m·4h)를 다른 종목에서는 모으므로 빠진 종목의 시리즈는 전부 「계획 밖」이다.
    assert {(u.symbol, u.reason) for u in summary.untracked_series} == {(DROPPED, "plan")}
    assert len(sent) == 1
    assert DROPPED not in sent[0]


def test_run_repair_falls_back_to_tf_filter_without_a_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """계획을 못 읽으면 옛 동작(WAN-157 TF 필터) 그대로다."""
    monkeypatch.setattr("data.exchange.create_exchange", lambda settings: _NoFetchExchange())
    monkeypatch.setattr("data.repair.build_telegram_client", lambda settings: None)
    _seed(tmp_path, keep_stalled=False, rotations=False)
    settings = _settings(tmp_path)

    summary = run_repair(settings, dry_run=True)

    untracked = {(u.symbol, u.timeframe, u.reason) for u in summary.untracked_series}
    assert untracked == {
        (KEEP, "3h", "timeframe"),
        (DROPPED, "15m", "timeframe"),
        (DROPPED, "4h", "timeframe"),
    }
    assert (DROPPED, "1h") in {(s.symbol, s.timeframe) for s in summary.stale_series}


# -- 단위 -------------------------------------------------------------------------


def test_repair_all_applies_timeframe_then_plan(tmp_path: Path) -> None:
    """둘 다 주면 TF로 먼저 거르고 남은 것을 계획으로 거른다 — 사유가 섞이지 않는다."""
    now = _seed(tmp_path, keep_stalled=False)
    scope = MonitorScope(pairs=frozenset({(BTC, "1h"), (KEEP, "1h")}))
    with OhlcvStore(tmp_path / "ohlcv.db") as store:
        summary = repair_all(
            _NoFetchExchange(),
            store,
            now_ms=lambda: now,
            tracked_timeframes=["1h"],
            scope=scope,
        )
    reasons = {(u.symbol, u.timeframe): u.reason for u in summary.untracked_series}
    assert reasons == {
        (KEEP, "3h"): "timeframe",
        (DROPPED, "15m"): "timeframe",
        (DROPPED, "4h"): "timeframe",
        (DROPPED, "1h"): "plan",
    }
    assert summary.stale_series == []


def test_repair_all_without_scope_is_unchanged(tmp_path: Path) -> None:
    """범위를 안 주면(기본) 예전처럼 저장된 전부를 판정한다 — 기존 호출부 비트 불변."""
    now = _seed(tmp_path, keep_stalled=False)
    with OhlcvStore(tmp_path / "ohlcv.db") as store:
        summary = repair_all(_NoFetchExchange(), store, now_ms=lambda: now)
    assert summary.untracked_series == []
    assert DROPPED in {s.symbol for s in summary.stale_series}


def test_old_state_file_loads_as_timeframe_reason() -> None:
    """WAN-466 이전 상태 파일(사유 필드 없음)은 「설정 TF 아님」으로 읽힌다."""
    raw = {
        "ran_at_ms": 0,
        "untracked_series": [{"symbol": BTC, "timeframe": "5m", "last_ms": 1, "lag_ms": 2}],
    }
    summary = RepairSummary.model_validate(raw)
    assert summary.untracked_series[0].reason == "timeframe"


def test_plan_excluded_folds_to_symbols() -> None:
    """매주 교체가 쌓여도 한 줄 — 사람이 볼 것은 「어느 종목이 빠졌나」다."""
    items = [
        UntrackedSeries(symbol=s, timeframe=tf, last_ms=None, lag_ms=None, reason="plan")
        for s in ("ONE/USDT:USDT", "BR/USDT:USDT")
        for tf in ("1m", "15m", "1h")
    ]
    text = format_plan_excluded(items)
    assert "6 시리즈" in text
    assert text.endswith("BR/USDT:USDT, ONE/USDT:USDT")
