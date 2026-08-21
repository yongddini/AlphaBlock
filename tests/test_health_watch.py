"""live.health_watch 경고 판정·쿨다운·복구 로직 테스트 (WAN-32, 전송 실패·종료 코드 = WAN-344)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from config.settings import Settings
from dashboard.health import (
    CollectorStatus,
    FundingFreshness,
    HealthLevel,
    OverallBadge,
    RunnerStatus,
    SeriesFreshness,
)
from dashboard.health_data import HealthView
from live import health_watch
from live.health_watch import (
    WATCH_DELIVERY_FAILED,
    WATCH_OK,
    WATCH_TELEGRAM_UNCONFIGURED,
    Alert,
    AlertRecord,
    HealthWatch,
    Outbound,
    WatchState,
    WatchStateStore,
    apply_delivery_failures,
    evaluate_alerts,
    reconcile,
    run_health_watch,
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


def test_evaluate_alerts_cycle_stall_gets_distinct_message() -> None:
    """하트비트는 뛰는데 한 바퀴 완주가 늦으면(WAN-313) 원인 안내가 다른 경고가 나온다."""
    stalled = RunnerStatus(
        ran=True,
        last_poll_ms=0,
        last_notification_ms=None,
        lag_ms=30_000,
        level=HealthLevel.STALE,
        heartbeat_stale=False,
        last_cycle_ms=0,
        cycle_duration_ms=16 * _MIN,
        cycle_lag_ms=20 * _MIN,
        cycle_stale=True,
    )
    view = _view(runner=stalled)
    (alert,) = evaluate_alerts(view)
    assert alert.key == "runner"
    assert "완주 지연" in alert.title
    assert "KST" in alert.detail
    assert "하트비트 끊김" not in alert.detail


# -- 전송 실패 처리 (WAN-344) --------------------------------------------------


class _FailingNotify:
    """전송이 실패하는 알림자(텔레그램 400·네트워크 소진)."""

    def __init__(self, *, fail_first: int = 1) -> None:
        self.sent: list[str] = []
        self._fail_first = fail_first

    def __call__(self, text: str) -> bool:
        self.sent.append(text)
        return len(self.sent) > self._fail_first


def _stale_watch(
    tmp_path: Path,
    notify: Callable[[str], bool],
    now_ms: int,
    *,
    view: HealthView | None = None,
) -> HealthWatch:
    return HealthWatch(
        view_provider=lambda: view or _view(runner=_runner(ran=True, level=HealthLevel.STALE)),
        notify=notify,
        store=WatchStateStore(tmp_path / "watch.json"),
        cooldown_seconds=3600,
        interval_seconds=0,
        now_ms=lambda: now_ms,
    )


def test_apply_delivery_failures_forgets_a_brand_new_alert() -> None:
    """실패한 첫 경고는 「보냈다」로 남지 않는다 → 다음 점검에 새 경고로 다시 나간다."""
    sent = WatchState(active={"runner": AlertRecord(title=_TITLE, last_notified_ms=_HOUR)})
    undelivered = [Outbound(kind="alert", key="runner", text="…")]
    assert apply_delivery_failures(sent, WatchState(), undelivered).active == {}


def test_apply_delivery_failures_keeps_the_old_timestamp_for_a_reminder() -> None:
    """실패한 리마인더는 쿨다운 시계를 앞당기지 않는다(옛 타임스탬프 유지 → 즉시 재시도)."""
    previous = _active(0, key="runner")
    sent = WatchState(active={"runner": AlertRecord(title=_TITLE, last_notified_ms=2 * _HOUR)})
    undelivered = [Outbound(kind="alert", key="runner", text="…")]
    result = apply_delivery_failures(sent, previous, undelivered)
    assert result.active["runner"].last_notified_ms == 0


def test_apply_delivery_failures_revives_a_failed_recovery() -> None:
    """복구 알림이 실패하면 기록을 되살려 다음 점검에서 다시 복구로 잡히게 한다."""
    previous = _active(0, key="runner")
    undelivered = [Outbound(kind="recovery", key="runner", text="…")]
    assert apply_delivery_failures(WatchState(), previous, undelivered).active == previous.active


def test_failed_alert_is_retried_on_the_next_check_despite_cooldown(tmp_path: Path) -> None:
    """🚨 핵심 회귀: 전송 실패가 쿨다운을 걸어 「안 갔는데 1시간 침묵」이 되면 안 된다.

    옛 동작은 `reconcile` 결과를 그대로 저장해 실패해도 「방금 보냄」으로 남겼다.
    """
    notify = _FailingNotify(fail_first=1)
    _stale_watch(tmp_path, notify, 0).check_once()
    assert len(notify.sent) == 1  # 시도했으나 실패

    # 쿨다운(1시간) 한참 이내인데도 다시 보낸다 — 실패는 발송으로 치지 않는다.
    _stale_watch(tmp_path, notify, _MIN).check_once()
    assert len(notify.sent) == 2

    # 이번엔 성공했으므로 그다음 점검은 쿨다운이 정상적으로 억제한다.
    _stale_watch(tmp_path, notify, 2 * _MIN).check_once()
    assert len(notify.sent) == 2


def test_failed_sends_are_counted(tmp_path: Path) -> None:
    notify = _FailingNotify(fail_first=1)
    watch = _stale_watch(tmp_path, notify, 0)
    watch.check_once()
    assert watch.failed_sends == 1


def test_successful_send_leaves_no_failure(tmp_path: Path) -> None:
    watch = _stale_watch(tmp_path, _FakeNotify(), 0)
    watch.check_once()
    assert watch.failed_sends == 0


# -- 종료 코드 (WAN-344) -------------------------------------------------------


class _FakeTelegram:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    def send_message(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


def _patch_telegram(
    monkeypatch: pytest.MonkeyPatch, client: _FakeTelegram | None
) -> _FakeTelegram | None:
    monkeypatch.setattr(health_watch, "build_telegram_client", lambda _s: client)
    return client


def _watch_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=str(tmp_path / "ohlcv.db"),
        live_runtime_state_path=str(tmp_path / "runtime.json"),
        collector_heartbeat_path=str(tmp_path / "hb.json"),
        health_watch_state_path=str(tmp_path / "watch.json"),
    )


def test_test_message_reports_success_as_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _patch_telegram(monkeypatch, _FakeTelegram(ok=True))
    code = run_health_watch(_watch_settings(tmp_path), test_message=True)
    assert code == WATCH_OK
    assert client is not None and len(client.sent) == 1


def test_test_message_reports_a_failed_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🚨 WAN-344 §4-4: 연결 확인이 실패했는데 0을 내면 그건 확인이 아니다."""
    _patch_telegram(monkeypatch, _FakeTelegram(ok=False))
    assert run_health_watch(_watch_settings(tmp_path), test_message=True) == WATCH_DELIVERY_FAILED


def test_test_message_reports_missing_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_telegram(monkeypatch, None)
    code = run_health_watch(_watch_settings(tmp_path), test_message=True)
    assert code == WATCH_TELEGRAM_UNCONFIGURED


def test_require_delivery_refuses_to_run_without_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """조용히 드라이런으로 접지 않는다 — 그 상태가 systemd 에서 성공으로 보이면 안 된다."""
    _patch_telegram(monkeypatch, None)
    code = run_health_watch(_watch_settings(tmp_path), once=True, require_delivery=True)
    assert code == WATCH_TELEGRAM_UNCONFIGURED


def test_without_require_delivery_missing_telegram_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기본 동작은 예전 그대로 — 미설정이면 드라이런으로 돌고 0을 낸다."""
    _patch_telegram(monkeypatch, None)
    assert run_health_watch(_watch_settings(tmp_path), once=True) == WATCH_OK


def test_require_delivery_reports_a_failed_alert_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_telegram(monkeypatch, _FakeTelegram(ok=False))
    stale = _view(runner=_runner(ran=True, level=HealthLevel.STALE))
    monkeypatch.setattr(health_watch, "build_view_provider", lambda _s: lambda: stale)
    code = run_health_watch(_watch_settings(tmp_path), once=True, require_delivery=True)
    assert code == WATCH_DELIVERY_FAILED


def test_require_delivery_is_zero_when_everything_is_fine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_telegram(monkeypatch, _FakeTelegram(ok=True))
    monkeypatch.setattr(health_watch, "build_view_provider", lambda _s: lambda: _view())
    code = run_health_watch(_watch_settings(tmp_path), once=True, require_delivery=True)
    assert code == WATCH_OK


def test_dry_run_and_require_delivery_are_rejected(tmp_path: Path) -> None:
    """보내지 않는 모드에 「도착을 요구」를 붙이면 라벨과 동작이 어긋난다 — 거부한다."""
    with pytest.raises(ValueError):
        run_health_watch(_watch_settings(tmp_path), once=True, dry_run=True, require_delivery=True)


def test_timer_mode_does_not_duplicate_alerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """systemd 타이머 판(매번 새 프로세스 = `--once`)에서도 쿨다운이 산다(WAN-344 §4-3).

    상태가 `health_watch_state_path` JSON 으로 영속화되므로 상시 프로세스가 아니어도
    중복 경고가 나지 않는다 — 이 성질이 timer + `--once` 선택의 근거다.
    """
    client = _FakeTelegram(ok=True)
    _patch_telegram(monkeypatch, client)
    stale = _view(runner=_runner(ran=True, level=HealthLevel.STALE))
    monkeypatch.setattr(health_watch, "build_view_provider", lambda _s: lambda: stale)
    settings = _watch_settings(tmp_path)

    for _ in range(3):  # 타이머가 세 번 트리거 = 프로세스 세 번
        assert run_health_watch(settings, once=True, require_delivery=True) == WATCH_OK
    assert len(client.sent) == 1  # 쿨다운(1시간) 이내라 첫 경고 1건뿐


def test_dry_run_keeps_the_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """드라이런은 「로그에 남겼다」를 전달로 친다 — 안 그러면 점검마다 같은 경고가 쌓인다."""
    stale = _view(runner=_runner(ran=True, level=HealthLevel.STALE))
    monkeypatch.setattr(health_watch, "build_view_provider", lambda _s: lambda: stale)
    logged: list[str] = []

    def _record(text: str) -> bool:
        logged.append(text)
        return True

    monkeypatch.setattr(health_watch, "_log_notify", _record)
    settings = _watch_settings(tmp_path)

    for _ in range(3):
        assert run_health_watch(settings, once=True, dry_run=True) == WATCH_OK
    assert len(logged) == 1  # 쿨다운 이내라 1건뿐
