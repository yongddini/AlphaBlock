"""live.health_watch 경고 판정·쿨다운·복구 로직 테스트 (WAN-32)."""

from __future__ import annotations

from pathlib import Path

from dashboard.health import (
    CollectorStatus,
    FundingFreshness,
    HealthLevel,
    OverallBadge,
    RunnerStatus,
    SeriesFreshness,
)
from dashboard.health_data import HealthView
from live.health_watch import (
    Alert,
    AlertRecord,
    HealthWatch,
    WatchState,
    WatchStateStore,
    evaluate_alerts,
    reconcile,
)

_HOUR = 3_600_000
_MIN = 60_000


def _series(
    symbol: str, tf: str, level: HealthLevel, lag_ms: int | None = 3 * _HOUR
) -> SeriesFreshness:
    return SeriesFreshness(
        symbol=symbol,
        timeframe=tf,
        last_open_time=0,
        bar_count=100,
        expected_interval_ms=_HOUR,
        lag_ms=lag_ms,
        level=level,
    )


def _funding(symbol: str, level: HealthLevel, lag_ms: int | None = 20 * _HOUR) -> FundingFreshness:
    return FundingFreshness(
        symbol=symbol,
        rate=0.0001,
        funding_time=0,
        next_funding_time=None,
        is_predicted=False,
        lag_ms=lag_ms,
        level=level,
    )


def _runner(*, ran: bool, level: HealthLevel, lag_ms: int | None = 10 * _MIN) -> RunnerStatus:
    return RunnerStatus(
        ran=ran,
        last_poll_ms=0 if ran else None,
        last_notification_ms=None,
        lag_ms=lag_ms if ran else None,
        level=level,
    )


def _collector(*, ran: bool, level: HealthLevel, lag_ms: int | None = 10 * _MIN) -> CollectorStatus:
    return CollectorStatus(
        ran=ran,
        last_beat_ms=0 if ran else None,
        lag_ms=lag_ms if ran else None,
        level=level,
    )


def _view(
    *,
    freshness: list[SeriesFreshness] | None = None,
    funding: list[FundingFreshness] | None = None,
    runner: RunnerStatus | None = None,
    collector: CollectorStatus | None = None,
) -> HealthView:
    return HealthView(
        now_ms=100 * _HOUR,
        overall=OverallBadge(level=HealthLevel.OK, label="정상"),
        freshness=freshness if freshness is not None else [],
        funding=funding if funding is not None else [],
        collector=collector or _collector(ran=True, level=HealthLevel.OK),
        runner=runner or _runner(ran=True, level=HealthLevel.OK),
        positions=[],
        recent_events=[],
    )


# -- evaluate_alerts ----------------------------------------------------------


def test_evaluate_alerts_flags_each_stale_source() -> None:
    view = _view(
        freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.STALE)],
        funding=[_funding("BTC/USDT:USDT", HealthLevel.STALE)],
        runner=_runner(ran=True, level=HealthLevel.STALE),
        collector=_collector(ran=True, level=HealthLevel.STALE),
    )
    keys = {a.key for a in evaluate_alerts(view)}
    assert keys == {"data:BTC/USDT:USDT:1h", "funding:BTC/USDT:USDT", "runner", "collector"}


def test_evaluate_alerts_ignores_ok_and_unknown() -> None:
    view = _view(
        freshness=[
            _series("BTC/USDT:USDT", "1h", HealthLevel.OK),
            _series("ETH/USDT:USDT", "1h", HealthLevel.UNKNOWN, lag_ms=None),
        ],
        funding=[_funding("BTC/USDT:USDT", HealthLevel.OK)],
        runner=_runner(ran=True, level=HealthLevel.OK),
        collector=_collector(ran=True, level=HealthLevel.OK),
    )
    assert evaluate_alerts(view) == []


def test_evaluate_alerts_skips_never_ran_processes() -> None:
    # 한 번도 실행되지 않은(UNKNOWN) 러너·수집기는 "끊김"이 아니라 미실행 → 경고 없음.
    view = _view(
        runner=_runner(ran=False, level=HealthLevel.UNKNOWN),
        collector=_collector(ran=False, level=HealthLevel.UNKNOWN),
    )
    assert evaluate_alerts(view) == []


def test_evaluate_alerts_detail_names_series_and_lag() -> None:
    view = _view(freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.STALE, lag_ms=3 * _HOUR)])
    (alert,) = evaluate_alerts(view)
    assert "BTC/USDT:USDT" in alert.detail
    assert "1h" in alert.detail
    assert "3.0시간" in alert.detail


def test_evaluate_alerts_detail_shows_absolute_time_in_kst() -> None:
    """경고 본문에 마지막 갱신 **절대 시각**이 KST로 붙는다(WAN-172).

    폰으로 받는 경고라 "3.0시간 전"만으로는 언제인지 감이 안 온다. UTC epoch 0은
    KST로 1970-01-01 09:00 — 이 +9h 오프셋이 변환이 실제로 일어났다는 증거다.
    """
    view = _view(freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.STALE)])
    (alert,) = evaluate_alerts(view)
    assert "1970-01-01 09:00 KST" in alert.detail
    assert "UTC" not in alert.detail


def test_evaluate_alerts_detail_covers_every_alert_kind() -> None:
    """네 종류(데이터·펀딩·러너·수집기) 전부 KST 절대 시각을 싣는다."""
    view = _view(
        freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.STALE)],
        funding=[_funding("BTC/USDT:USDT", HealthLevel.STALE)],
        runner=_runner(ran=True, level=HealthLevel.STALE),
        collector=_collector(ran=True, level=HealthLevel.STALE),
    )
    alerts = evaluate_alerts(view)
    assert len(alerts) == 4
    assert all("KST" in a.detail for a in alerts)


# -- reconcile: 쿨다운·복구 ---------------------------------------------------


_TITLE = "수집기 하트비트 끊김"


def _alert(key: str = "collector", title: str = _TITLE) -> Alert:
    return Alert(key=key, title=title, detail=f"경고 {key}")


def _active(last_notified_ms: int, *, key: str = "collector", title: str = _TITLE) -> WatchState:
    return WatchState(active={key: AlertRecord(title=title, last_notified_ms=last_notified_ms)})


def test_reconcile_new_alert_sends_once() -> None:
    outbound, state = reconcile([_alert()], WatchState(), now_ms=1000, cooldown_ms=_HOUR)
    assert [m.kind for m in outbound] == ["alert"]
    assert "collector" in state.active


def test_reconcile_suppresses_within_cooldown() -> None:
    outbound, state = reconcile([_alert()], _active(0), now_ms=_MIN, cooldown_ms=_HOUR)
    assert outbound == []  # 쿨다운 이내 — 중복 억제
    assert state.active["collector"].last_notified_ms == 0  # 타임스탬프 유지


def test_reconcile_reminds_after_cooldown() -> None:
    outbound, state = reconcile([_alert()], _active(0), now_ms=2 * _HOUR, cooldown_ms=_HOUR)
    assert [m.kind for m in outbound] == ["alert"]  # 쿨다운 경과 → 리마인더
    assert state.active["collector"].last_notified_ms == 2 * _HOUR


def test_reconcile_recovers_once_when_cleared() -> None:
    prev = _active(0)
    # 이상 사라짐 → 복구 알림 1회, 상태에서 제거.
    outbound, state = reconcile([], prev, now_ms=_HOUR, cooldown_ms=_HOUR)
    assert [m.kind for m in outbound] == ["recovery"]
    assert "정상 복구" in outbound[0].text
    assert state.active == {}
    # 다음 점검엔 아무것도 보내지 않음(복구는 1회만).
    outbound2, _ = reconcile([], state, now_ms=2 * _HOUR, cooldown_ms=_HOUR)
    assert outbound2 == []


# -- WatchStateStore ----------------------------------------------------------


def test_watch_state_store_round_trip(tmp_path: Path) -> None:
    store = WatchStateStore(tmp_path / "watch.json")
    assert store.load().active == {}
    state = _active(42, key="runner", title="러너 하트비트 끊김")
    store.save(state)
    loaded = store.load()
    assert loaded.active["runner"].last_notified_ms == 42


def test_watch_state_store_tolerates_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "watch.json"
    path.write_text("{not json", encoding="utf-8")
    assert WatchStateStore(path).load().active == {}


# -- HealthWatch end-to-end (완료 기준) ---------------------------------------


class _FakeNotify:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, text: str) -> bool:
        self.sent.append(text)
        return True


def test_health_watch_alerts_then_recovers_without_flapping(tmp_path: Path) -> None:
    """완료 기준: 수집 멈춤 → 경고 1회, 중복 억제, 복구 시 정상 알림 1회."""
    views = iter(
        [
            # 1) 수집 정상
            _view(freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.OK)]),
            # 2) 수집 멈춤(stale) → 경고 1회
            _view(freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.STALE)]),
            # 3) 여전히 멈춤(쿨다운 이내) → 중복 경고 억제
            _view(freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.STALE)]),
            # 4) 복구 → 정상 알림 1회
            _view(freshness=[_series("BTC/USDT:USDT", "1h", HealthLevel.OK)]),
        ]
    )
    notify = _FakeNotify()
    clock = {"t": 0}
    watch = HealthWatch(
        view_provider=lambda: next(views),
        notify=notify,
        store=WatchStateStore(tmp_path / "watch.json"),
        cooldown_seconds=3600,
        interval_seconds=0,
        sleep=lambda _s: None,
        now_ms=lambda: clock["t"],
    )

    watch.check_once()  # 정상
    assert notify.sent == []

    clock["t"] = _MIN
    watch.check_once()  # 멈춤 → 경고 1회
    assert len(notify.sent) == 1
    assert "데이터 수집 지연" in notify.sent[0]

    clock["t"] = 2 * _MIN
    watch.check_once()  # 여전히 멈춤(쿨다운 이내) → 추가 발송 없음
    assert len(notify.sent) == 1

    clock["t"] = 3 * _MIN
    watch.check_once()  # 복구 → 정상 알림 1회
    assert len(notify.sent) == 2
    assert "정상 복구" in notify.sent[1]


def test_health_watch_persists_across_restart(tmp_path: Path) -> None:
    # 재시작(새 HealthWatch 인스턴스)해도 쿨다운 상태가 파일로 남아 중복 경고를 막는다.
    state_path = tmp_path / "watch.json"
    stale_view = _view(runner=_runner(ran=True, level=HealthLevel.STALE))

    def make_watch(notify: _FakeNotify, now_ms: int) -> HealthWatch:
        return HealthWatch(
            view_provider=lambda: stale_view,
            notify=notify,
            store=WatchStateStore(state_path),
            cooldown_seconds=3600,
            interval_seconds=0,
            now_ms=lambda: now_ms,
        )

    first = _FakeNotify()
    make_watch(first, 0).check_once()
    assert len(first.sent) == 1  # 첫 경고

    second = _FakeNotify()
    make_watch(second, _MIN).check_once()  # 재시작 직후, 쿨다운 이내
    assert second.sent == []  # 중복 경고 없음


def test_health_watch_survives_view_error(tmp_path: Path) -> None:
    def boom() -> HealthView:
        raise RuntimeError("DB 잠김")

    notify = _FakeNotify()
    watch = HealthWatch(
        view_provider=boom,
        notify=notify,
        store=WatchStateStore(tmp_path / "watch.json"),
        cooldown_seconds=3600,
        interval_seconds=0,
    )
    # 예외를 삼키고 빈 결과 → 루프가 죽지 않는다.
    assert watch.check_once() == []
    assert notify.sent == []


def test_health_watch_run_loops_max_checks(tmp_path: Path) -> None:
    calls = {"n": 0}

    def provider() -> HealthView:
        calls["n"] += 1
        return _view()

    watch = HealthWatch(
        view_provider=provider,
        notify=_FakeNotify(),
        store=WatchStateStore(tmp_path / "watch.json"),
        cooldown_seconds=3600,
        interval_seconds=0,
        sleep=lambda _s: None,
    )
    watch.run(max_checks=3)
    assert calls["n"] == 3
