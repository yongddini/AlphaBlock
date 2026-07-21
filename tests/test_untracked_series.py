"""수집 대상이 아닌 TF의 처리 회귀 테스트 (WAN-157).

이 파일이 고정하는 것은 **동작**이지 라벨이 아니다(WAN-112/123 패턴). 명제는 둘이고
둘 다 있어야 의미가 있다:

1. 설정에서 빠진 TF(5m)는 **결함이 아니다** — `alphablock backfill`이 그것 때문에
   종료 코드 1을 내지 않고 텔레그램도 울리지 않는다. 고칠 계획이 없는 항목이 매번
   빨간불이면 사람이 경고를 무시하게 되고, 그게 WAN-156 사고의 재발 경로다.
2. 그렇다고 **사라지지도 않는다** — 보고서·CLI·`alphablock status`에 「저장돼 있으나
   수집 대상이 아님(낡습니다)」로 계속 보인다. 판정에서 뺀 것을 화면에서까지 지우면
   WAN-156과 **같은 종류의 침묵**을 새로 만든다.

3번 명제(안전장치)도 함께 고정한다: 수집 대상인 TF가 멈추면 **여전히** 결함이다 —
필터가 진짜 정지까지 삼키면 안 된다.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.main import cmd_backfill, format_status
from config.settings import Settings
from dashboard.health_data import build_health_view
from data.models import Candle
from data.repair import RepairSummary, alert_on_failure, repair_all
from data.storage import OhlcvStore

SYMBOL = "BTC/USDT:USDT"
TRACKED_TF = "15m"
UNTRACKED_TF = "5m"
TF_MS = 900_000
T0 = 1_700_000_000_000
#: 사고 당시와 같은 5일.
FIVE_DAYS_MS = 5 * 24 * 3_600_000


def _candles(count: int, *, timeframe: str, step: int, start: int = T0) -> list[Candle]:
    return [
        Candle(
            symbol=SYMBOL,
            timeframe=timeframe,
            open_time=start + i * step,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1.0,
        )
        for i in range(count)
    ]


class _NoCallExchange:
    """호출되면 실패하는 거래소 — 갭이 없으면 네트워크를 안 탄다는 성질을 유지한다."""

    def fetch_ohlcv(self, *args: object, **kwargs: object) -> list[list[float]]:
        raise AssertionError("갭이 없는데 거래소를 호출했다")


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    with OhlcvStore(":memory:") as s:
        yield s


def _seed_both(store: OhlcvStore) -> int:
    """신선한 15m(수집 대상) + 5일 멈춘 5m(수집 대상 아님). 반환값은 「지금」."""
    now = T0 + 200 * TF_MS + FIVE_DAYS_MS
    # 5m은 옛날에 받다 만 시리즈 — 내부 갭은 없고 꼬리만 멈췄다.
    store.upsert_candles(_candles(200, timeframe=UNTRACKED_TF, step=300_000))
    # 15m은 「지금」까지 이어진다.
    fresh_start = now - 199 * TF_MS
    store.upsert_candles(_candles(200, timeframe=TRACKED_TF, step=TF_MS, start=fresh_start))
    return now


# -- 명제 1: 수집 대상이 아니면 결함이 아니다 --------------------------------


def test_untracked_timeframe_is_not_a_defect(store: OhlcvStore) -> None:
    now = _seed_both(store)

    summary = repair_all(
        _NoCallExchange(),
        store,
        now_ms=lambda: now,
        tracked_timeframes=[TRACKED_TF],
    )

    assert not summary.has_stale, "수집 대상이 아닌 5m이 정지로 찍혔다"
    assert not summary.has_defect, "고칠 계획이 없는 항목이 종료 코드 1을 만든다"
    assert [s.timeframe for s in summary.series] == [TRACKED_TF]


def test_untracked_timeframe_does_not_alert(
    store: OhlcvStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """텔레그램도 안 탄다 — 매번 우는 늑대가 되면 진짜 경고까지 무시된다."""
    now = _seed_both(store)
    summary = repair_all(
        _NoCallExchange(), store, now_ms=lambda: now, tracked_timeframes=[TRACKED_TF]
    )

    def _boom(_settings: Settings) -> object:
        raise AssertionError("미추적 시리즈로 텔레그램 클라이언트를 만들었다")

    monkeypatch.setattr("data.repair.build_telegram_client", _boom)
    assert alert_on_failure(summary, Settings()) is False


# -- 명제 2: 그래도 눈에 보인다 -----------------------------------------------


def test_untracked_series_stays_visible(store: OhlcvStore) -> None:
    now = _seed_both(store)

    summary = repair_all(
        _NoCallExchange(),
        store,
        now_ms=lambda: now,
        tracked_timeframes=[TRACKED_TF],
    )

    assert [(u.symbol, u.timeframe) for u in summary.untracked_series] == [(SYMBOL, UNTRACKED_TF)]
    untracked = summary.untracked_series[0]
    assert untracked.lag_ms is not None and untracked.lag_ms > FIVE_DAYS_MS


def test_untracked_series_survives_state_file_roundtrip(store: OhlcvStore) -> None:
    """상태 파일(JSON)로 나갔다 들어와도 남는다 — 대시보드·status가 그걸 읽는다."""
    now = _seed_both(store)
    summary = repair_all(
        _NoCallExchange(), store, now_ms=lambda: now, tracked_timeframes=[TRACKED_TF]
    )
    restored = RepairSummary.model_validate_json(summary.model_dump_json())
    assert [u.timeframe for u in restored.untracked_series] == [UNTRACKED_TF]


def test_status_shows_untracked_series(tmp_path: Path, store: OhlcvStore) -> None:
    now = _seed_both(store)
    summary = repair_all(
        _NoCallExchange(), store, now_ms=lambda: now, tracked_timeframes=[TRACKED_TF]
    )
    state_path = tmp_path / "repair_state.json"
    state_path.write_text(summary.model_dump_json(), encoding="utf-8")

    view = build_health_view(
        str(tmp_path / "empty.db"),
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        collector_heartbeat_path=str(tmp_path / "no_hb.json"),
        repair_state_path=str(state_path),
        now_ms=now,
    )

    text = format_status(view)
    assert "저장돼 있으나 수집 대상이 아님(낡습니다)" in text
    assert UNTRACKED_TF in text


def test_backfill_exits_zero_but_prints_untracked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """완료 기준의 두 축을 한 번에: 종료 코드 0 + 그래도 화면에 보인다."""
    db_path = str(tmp_path / "ohlcv.db")
    with OhlcvStore(db_path) as s:
        now = _seed_both(s)

    settings = Settings(
        db_path=db_path,
        timeframes=[TRACKED_TF],
        repair_state_path=str(tmp_path / "repair_state.json"),
    )
    monkeypatch.setattr("data.exchange.create_exchange", lambda _s: _NoCallExchange())
    monkeypatch.setattr("data.repair.time.time", lambda: now / 1000)

    rc = cmd_backfill(argparse.Namespace(dry_run=True), settings)

    assert rc == 0, "수집 대상이 아닌 TF 때문에 빨간불이 떴다"
    out = capsys.readouterr().out
    assert "저장돼 있으나 수집 대상이 아님(낡습니다)" in out
    assert f"{SYMBOL} {UNTRACKED_TF}" in out


# -- 명제 3: 진짜 정지는 여전히 결함이다 --------------------------------------


def test_tracked_timeframe_stall_is_still_a_defect(store: OhlcvStore) -> None:
    """필터가 진짜 정지까지 삼키면 WAN-156을 원위치시킨다."""
    store.upsert_candles(_candles(200, timeframe=TRACKED_TF, step=TF_MS))
    now = T0 + 199 * TF_MS + FIVE_DAYS_MS

    summary = repair_all(
        _NoCallExchange(),
        store,
        now_ms=lambda: now,
        tracked_timeframes=[TRACKED_TF],
    )

    assert summary.has_stale
    assert summary.has_defect
    assert summary.untracked_series == []


def test_default_behaviour_judges_everything(store: OhlcvStore) -> None:
    """`tracked_timeframes`를 안 주면 예전 그대로 — 기존 호출부가 안 바뀐다."""
    now = _seed_both(store)

    summary = repair_all(_NoCallExchange(), store, now_ms=lambda: now)

    assert summary.has_defect, "필터를 안 걸었는데 5m 정지가 사라졌다"
    assert [s.timeframe for s in summary.stale_series] == [UNTRACKED_TF]
    assert summary.untracked_series == []
