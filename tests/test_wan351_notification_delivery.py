"""「알림 경로가 막힌 것」과 「보낼 게 없는 것」을 가른다 (WAN-351).

`alphablock status`의 「마지막 알림」은 채택 러너가 한 번도 채우지 않았다 — 러너가 상태
파일에 `new_events=[]`를 고정으로 넘겨, 화면의 `—`는 「알림이 없었다」가 아니라 「이 칸은
원래 안 채워진다」였다. 「인자는 있는데 늘 빈 값」(WAN-345 부류)이라 **인자 전달만 보는
테스트로는 안 잡힌다** — 여기 테스트는 실제 체결 → 실제 알림 → 상태 파일까지 동작으로 건다.

* 성공한 발송만 「마지막 알림」으로 센다 — 드라이런·실패는 안 센다.
* 드라이런·전송 실패가 화면(`status`)과 감시(`watch`)에 보인다.
* 조용한 날(성공 기록만 있고 실패 0)에는 아무 경고도 안 나온다(거짓 경보 금지, WAN-321).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.main import format_status
from dashboard.health import HealthLevel, compute_runner_status
from dashboard.health_data import build_health_view
from live.health_watch import RUNNER_DELIVERY_ALERT_KEY, evaluate_alerts
from live.notification_delivery import DeliveryStatus, NotificationDelivery
from live.runtime_state import RuntimeStateStore
from live.zone_limit_notifier import ZoneLimitNotifier
from live.zone_limit_runner import ZoneLimitPaperRunner
from tests.test_zone_limit_runner import _FORMING, _M, _close_rig, _m1, _make_rig

_NOW = 1_700_000_000_000
_HOUR = 3_600_000


class _FakeTelegram:
    """보낸 메시지를 모으고 정해진 결과를 돌려주는 가짜 클라이언트."""

    def __init__(self, *, ok: bool = True, raises: bool = False) -> None:
        self.ok = ok
        self.raises = raises
        self.sent: list[tuple[str, object]] = []

    def send_message(self, text: str, *, parse_mode: str | None = "Markdown") -> bool:
        self.sent.append((text, parse_mode))
        if self.raises:
            raise ConnectionError("network down")
        return self.ok


# -- 추적기 -------------------------------------------------------------------


def test_success_records_time_and_clears_failures() -> None:
    tg = _FakeTelegram(ok=False)
    d = NotificationDelivery(tg, now_ms=lambda: 100)
    assert d.send("a") is False
    assert d.send("b") is False
    assert d.status().failures_since_success == 2
    assert d.status().last_success_ms is None
    assert d.status().last_failure_ms == 100

    tg.ok = True
    d = NotificationDelivery(tg, now_ms=lambda: 200)
    d.restore(DeliveryStatus(mode="telegram", last_failure_ms=100, failures_since_success=2))
    assert d.send("c") is True
    st = d.status()
    assert st.last_success_ms == 200
    assert st.failures_since_success == 0
    assert st.last_failure_ms == 100  # 마지막 실패 시각은 기록으로 남는다.


def test_dry_run_never_counts_as_a_notification() -> None:
    d = NotificationDelivery(None, now_ms=lambda: 100)
    assert d.mode == "dry_run"
    assert d.send("a") is False
    st = d.status()
    assert st.last_success_ms is None
    # 드라이런은 「실패」도 아니다 — 보낼 길이 애초에 없다(모드가 그 사실을 말한다).
    assert st.failures_since_success == 0


def test_exception_is_a_failure_not_a_crash() -> None:
    d = NotificationDelivery(_FakeTelegram(raises=True), now_ms=lambda: 100)
    assert d.send("a") is False
    assert d.status().failures_since_success == 1


def test_parse_mode_is_passed_through_only_when_given() -> None:
    tg = _FakeTelegram()
    d = NotificationDelivery(tg)
    d.send("default")
    d.send("plain", parse_mode=None)
    assert tg.sent == [("default", "Markdown"), ("plain", None)]


def test_disabled_mode() -> None:
    assert NotificationDelivery(_FakeTelegram(), enabled=False).mode == "disabled"


# -- 러너 끝단: 실제 체결 → 실제 알림 → 상태 파일 ---------------------------------


def _fill_through_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notifier: ZoneLimitNotifier
) -> tuple[RuntimeStateStore, ZoneLimitPaperRunner]:
    rig = _make_rig(tmp_path, monkeypatch, notifier=notifier)
    try:
        runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
        store = rig["store"]
        # 존 탭 + 터치 1분봉 → 체결 → 팔 진입(진입 체결 알림이 나간다).
        store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])  # type: ignore[attr-defined]
        runner.poll_once()
        assert rig["executor"].open_positions  # type: ignore[attr-defined]
        return RuntimeStateStore(rig["state_path"]), runner  # type: ignore[arg-type]
    finally:
        _close_rig(rig)


def test_runner_fills_last_notification_on_real_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """완료 기준 1 — 채택 러너가 실제로 보낸 알림이 「마지막 알림」을 채운다."""
    tg = _FakeTelegram(ok=True)
    delivery = NotificationDelivery(tg, now_ms=lambda: 123_456)
    notifier = ZoneLimitNotifier(tg, delivery=delivery)  # type: ignore[arg-type]
    state, _ = _fill_through_runner(tmp_path, monkeypatch, notifier)

    assert any("진입 체결" in text for text, _ in tg.sent)  # 알림이 실제로 나갔다.
    loaded = state.load()
    assert loaded.last_notification_at == 123_456
    assert loaded.notification_mode == "telegram"
    assert loaded.notification_failures == 0


def test_runner_dry_run_leaves_last_notification_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """완료 기준 1 — 드라이런(텔레그램 미설정)에서는 「마지막 알림」이 안 채워진다."""
    notifier = ZoneLimitNotifier(None)
    state, _ = _fill_through_runner(tmp_path, monkeypatch, notifier)

    loaded = state.load()
    assert loaded.last_notification_at is None
    assert loaded.notification_mode == "dry_run"


def test_runner_failed_send_is_counted_not_counted_as_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tg = _FakeTelegram(ok=False)
    notifier = ZoneLimitNotifier(tg, delivery=NotificationDelivery(tg, now_ms=lambda: 7))  # type: ignore[arg-type]
    state, _ = _fill_through_runner(tmp_path, monkeypatch, notifier)

    assert tg.sent  # 시도는 했다.
    loaded = state.load()
    assert loaded.last_notification_at is None  # 시도한 시각이 아니라 성공한 시각만.
    assert loaded.notification_failures >= 1
    assert loaded.last_notification_failure_at == 7


# -- 상태 파일 -----------------------------------------------------------------


def test_record_without_delivery_preserves_delivery_fields(tmp_path: Path) -> None:
    """발송 정보를 안 주는 기록(옛 호출부)이 남은 발송 기록을 지우지 않는다."""
    store = RuntimeStateStore(tmp_path / "s.json")
    store.record(
        now_ms=1,
        open_positions=[],
        new_events=[],
        delivery=DeliveryStatus(
            mode="telegram", last_success_ms=5, last_failure_ms=6, failures_since_success=1
        ),
    )
    store.record(now_ms=2, open_positions=[], new_events=[])
    store.touch(3)
    loaded = RuntimeStateStore(tmp_path / "s.json").load()
    assert (loaded.last_notification_at, loaded.notification_mode) == (5, "telegram")
    assert (loaded.last_notification_failure_at, loaded.notification_failures) == (6, 1)


def test_restart_keeps_last_success_and_unresolved_failures(tmp_path: Path) -> None:
    """재시작 한 번으로 막힌 경로가 정상처럼 보이면 안 된다 — 추적기가 기록을 이어받는다."""
    path = tmp_path / "s.json"
    RuntimeStateStore(path).record(
        now_ms=1,
        open_positions=[],
        new_events=[],
        delivery=DeliveryStatus(
            mode="telegram", last_success_ms=5, last_failure_ms=9, failures_since_success=3
        ),
    )
    store = RuntimeStateStore(path)
    d = NotificationDelivery(_FakeTelegram())
    d.restore(store.delivery_status())
    store.record(now_ms=10, open_positions=[], new_events=[], delivery=d.status())
    loaded = RuntimeStateStore(path).load()
    assert loaded.last_notification_at == 5
    assert loaded.notification_failures == 3


# -- 화면 · 감시 -----------------------------------------------------------------


def _status_text(tmp_path: Path, delivery: DeliveryStatus) -> str:
    path = tmp_path / "runtime.json"
    RuntimeStateStore(path).record(
        now_ms=_NOW - 30_000, open_positions=[], new_events=[], delivery=delivery
    )
    view = build_health_view(
        str(tmp_path / "ohlcv.db"),
        runtime_state_path=str(path),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        now_ms=_NOW,
    )
    return format_status(view)


def test_status_shows_when_and_how_long_ago(tmp_path: Path) -> None:
    """완료 기준 §2-3 — `—`가 아니라 시각 + 몇 시간 전."""
    text = _status_text(
        tmp_path, DeliveryStatus(mode="telegram", last_success_ms=_NOW - 15 * _HOUR)
    )
    assert "마지막 알림 2023-11-14 16:13 KST (15.0시간 전)" in text
    assert "드라이런" not in text and "전송 실패" not in text  # 조용한 날은 조용하다.


def test_status_shows_dry_run_on_screen(tmp_path: Path) -> None:
    """완료 기준 2 — 드라이런이 로그가 아니라 화면에 보인다."""
    text = _status_text(tmp_path, DeliveryStatus(mode="dry_run"))
    assert "드라이런" in text
    assert "텔레그램 미설정" in text


def test_status_shows_send_failures(tmp_path: Path) -> None:
    text = _status_text(
        tmp_path,
        DeliveryStatus(
            mode="telegram",
            last_success_ms=_NOW - 30 * _HOUR,
            last_failure_ms=_NOW - _HOUR,
            failures_since_success=4,
        ),
    )
    assert "알림 전송 실패 4회" in text


def test_old_runner_without_delivery_info_says_so(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    RuntimeStateStore(path).record(now_ms=_NOW - 30_000, open_positions=[], new_events=[])
    view = build_health_view(
        str(tmp_path / "ohlcv.db"),
        runtime_state_path=str(path),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        now_ms=_NOW,
    )
    assert "기록 없음 (러너가 이 칸을 남기지 않음)" in format_status(view)


def _runner(**kwargs: object) -> object:
    return compute_runner_status(
        last_poll_ms=_NOW - 1_000,
        now_ms=_NOW,
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        **kwargs,  # type: ignore[arg-type]
    )


def test_delivery_problems_do_not_change_runner_liveness() -> None:
    """러너는 살아 있는데 알림만 막힌 상태를 「러너 멈춤」으로 읽으면 안 된다."""
    status = _runner(
        last_notification_ms=None, notification_mode="telegram", notification_failures=9
    )
    assert status.level is HealthLevel.OK  # type: ignore[attr-defined]


def _alert_keys(**kwargs: object) -> set[str]:
    from dashboard.health import CollectorStatus, OverallBadge
    from dashboard.health_data import HealthView

    view = HealthView(
        now_ms=_NOW,
        overall=OverallBadge(level=HealthLevel.OK, label="정상"),
        freshness=[],
        funding=[],
        collector=CollectorStatus(
            ran=False, last_beat_ms=None, lag_ms=None, level=HealthLevel.UNKNOWN
        ),
        runner=_runner(**kwargs),
        positions=[],
        recent_events=[],
    )
    return {a.key for a in evaluate_alerts(view)}


def test_watch_alerts_on_failure_and_dry_run_only() -> None:
    """§3 — 감시는 조용한 날에 원리적으로 안 생기는 두 신호(실패·드라이런)만 경고한다."""
    # 조용한 날: 마지막 성공이 이틀 전이어도(보낼 게 없었다) 경고하지 않는다.
    assert RUNNER_DELIVERY_ALERT_KEY not in _alert_keys(
        last_notification_ms=_NOW - 48 * _HOUR, notification_mode="telegram"
    )
    # 한 번도 성공 발송이 없어도 실패가 없으면 경고하지 않는다.
    assert RUNNER_DELIVERY_ALERT_KEY not in _alert_keys(
        last_notification_ms=None, notification_mode="telegram"
    )
    # 꺼짐(일부러 끔)은 경고하지 않는다.
    assert RUNNER_DELIVERY_ALERT_KEY not in _alert_keys(
        last_notification_ms=None, notification_mode="disabled"
    )
    assert RUNNER_DELIVERY_ALERT_KEY in _alert_keys(
        last_notification_ms=_NOW - _HOUR,
        notification_mode="telegram",
        notification_failures=1,
        last_notification_failure_ms=_NOW - 60_000,
    )
    assert RUNNER_DELIVERY_ALERT_KEY in _alert_keys(
        last_notification_ms=None, notification_mode="dry_run"
    )
