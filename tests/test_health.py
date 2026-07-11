"""dashboard.health 순수 판정 로직 테스트 (WAN-30)."""

from __future__ import annotations

from dashboard.health import (
    FUNDING_INTERVAL_MS,
    HealthLevel,
    classify_lag,
    compute_collector_status,
    compute_freshness,
    compute_funding_status,
    compute_overall,
    compute_runner_status,
)

_HOUR = 3_600_000


def test_classify_lag_ok_stale_and_unknown() -> None:
    interval = _HOUR
    # 최신값(지연 0) → 정상
    lag, level = classify_lag(1_000_000, 1_000_000, interval, stale_multiplier=2.5)
    assert level is HealthLevel.OK
    assert lag == 0
    # 주기의 2.5배를 넘는 지연 → stale
    lag, level = classify_lag(0, 3 * interval, interval, stale_multiplier=2.5)
    assert level is HealthLevel.STALE
    assert lag == 3 * interval
    # 기준값 없음 → unknown
    _, level = classify_lag(None, 100, interval, stale_multiplier=2.5)
    assert level is HealthLevel.UNKNOWN
    # 주기 모름 → unknown
    _, level = classify_lag(0, 100, None, stale_multiplier=2.5)
    assert level is HealthLevel.UNKNOWN


def test_compute_freshness_flags_stale_series() -> None:
    now = 10 * _HOUR
    rows = [
        ("BTC/USDT:USDT", "1h", 10 * _HOUR, 100),  # 최신 → OK
        ("ETH/USDT:USDT", "1h", 2 * _HOUR, 50),  # 8시간 지연 → STALE
        ("SOL/USDT:USDT", "1h", None, 0),  # 데이터 없음 → UNKNOWN
        ("BTC/USDT:USDT", "weird", 0, 3),  # 미지원 TF → UNKNOWN
    ]
    result = compute_freshness(rows, now_ms=now, stale_multiplier=2.5)
    by_key = {(r.symbol, r.timeframe): r for r in result}

    assert by_key[("BTC/USDT:USDT", "1h")].level is HealthLevel.OK
    assert by_key[("BTC/USDT:USDT", "1h")].lag_ms == 0
    assert by_key[("ETH/USDT:USDT", "1h")].level is HealthLevel.STALE
    assert by_key[("SOL/USDT:USDT", "1h")].level is HealthLevel.UNKNOWN
    assert by_key[("BTC/USDT:USDT", "weird")].level is HealthLevel.UNKNOWN
    assert by_key[("BTC/USDT:USDT", "weird")].expected_interval_ms is None


def test_compute_funding_status_predicted_future_is_ok() -> None:
    now = 100 * FUNDING_INTERVAL_MS
    rows = [
        # 예측 현재값: 다음 정산이 미래(=지연 음수) → 정상
        ("BTC/USDT:USDT", 0.0001, now + FUNDING_INTERVAL_MS, now + FUNDING_INTERVAL_MS, True),
        # 확정값이 한참 과거 → stale
        ("ETH/USDT:USDT", 0.0002, now - 30 * FUNDING_INTERVAL_MS, None, False),
        # 데이터 없음 → unknown
        ("SOL/USDT:USDT", None, None, None, False),
    ]
    result = compute_funding_status(rows, now_ms=now, stale_multiplier=2.5)
    by_symbol = {r.symbol: r for r in result}

    assert by_symbol["BTC/USDT:USDT"].level is HealthLevel.OK
    assert by_symbol["ETH/USDT:USDT"].level is HealthLevel.STALE
    assert by_symbol["SOL/USDT:USDT"].level is HealthLevel.UNKNOWN


def test_compute_runner_status_ran_stale_and_never() -> None:
    now = 1_000_000_000
    # 방금 폴링 → 살아있음
    alive = compute_runner_status(
        last_poll_ms=now - 30_000,
        last_notification_ms=now - 60_000,
        now_ms=now,
        poll_interval_seconds=60,
        stale_multiplier=2.5,
    )
    assert alive.ran is True
    assert alive.level is HealthLevel.OK

    # 폴링 간격의 2.5배를 넘겨 하트비트 끊김 → 멈춤
    stale = compute_runner_status(
        last_poll_ms=now - 10 * 60_000,
        last_notification_ms=None,
        now_ms=now,
        poll_interval_seconds=60,
        stale_multiplier=2.5,
    )
    assert stale.level is HealthLevel.STALE

    # 하트비트 없음 → 미실행(unknown)
    never = compute_runner_status(
        last_poll_ms=None,
        last_notification_ms=None,
        now_ms=now,
        poll_interval_seconds=60,
        stale_multiplier=2.5,
    )
    assert never.ran is False
    assert never.level is HealthLevel.UNKNOWN


def test_compute_collector_status_alive_stale_and_never_ran() -> None:
    now = 100 * _HOUR
    interval_s = 60

    alive = compute_collector_status(
        last_beat_ms=now - 30_000,
        now_ms=now,
        heartbeat_interval_seconds=interval_s,
        stale_multiplier=2.5,
    )
    assert alive.ran is True
    assert alive.level is HealthLevel.OK

    # 마지막 하트비트가 간격의 2.5배(=150s)를 훨씬 넘김 → 멈춤
    dead = compute_collector_status(
        last_beat_ms=now - 10 * 60_000,
        now_ms=now,
        heartbeat_interval_seconds=interval_s,
        stale_multiplier=2.5,
    )
    assert dead.level is HealthLevel.STALE

    never = compute_collector_status(
        last_beat_ms=None,
        now_ms=now,
        heartbeat_interval_seconds=interval_s,
        stale_multiplier=2.5,
    )
    assert never.ran is False
    assert never.level is HealthLevel.UNKNOWN


def _collector(level: HealthLevel):  # type: ignore[no-untyped-def]
    from dashboard.health import CollectorStatus

    return CollectorStatus(
        ran=level is not HealthLevel.UNKNOWN,
        last_beat_ms=None if level is HealthLevel.UNKNOWN else 0,
        lag_ms=None if level is HealthLevel.UNKNOWN else 0,
        level=level,
    )


def test_compute_overall_collector_down_is_stopped() -> None:
    badge = compute_overall(
        [_fresh(HealthLevel.OK)],
        [],
        _runner(HealthLevel.OK),
        _collector(HealthLevel.STALE),
    )
    assert badge.label == "멈춤"


def test_compute_overall_collector_never_ran_is_partial() -> None:
    badge = compute_overall(
        [_fresh(HealthLevel.OK)],
        [],
        _runner(HealthLevel.OK),
        _collector(HealthLevel.UNKNOWN),
    )
    assert badge.label == "일부 지연"


def test_compute_overall_healthy_with_collector() -> None:
    badge = compute_overall(
        [_fresh(HealthLevel.OK)],
        [],
        _runner(HealthLevel.OK),
        _collector(HealthLevel.OK),
    )
    assert badge.level is HealthLevel.OK


def _fresh(level: HealthLevel):  # type: ignore[no-untyped-def]
    from dashboard.health import SeriesFreshness

    return SeriesFreshness(
        symbol="X",
        timeframe="1h",
        last_open_time=0,
        bar_count=1,
        expected_interval_ms=_HOUR,
        lag_ms=0,
        level=level,
    )


def _runner(level: HealthLevel):  # type: ignore[no-untyped-def]
    from dashboard.health import RunnerStatus

    return RunnerStatus(
        ran=level is not HealthLevel.UNKNOWN,
        last_poll_ms=None if level is HealthLevel.UNKNOWN else 0,
        last_notification_ms=None,
        lag_ms=None if level is HealthLevel.UNKNOWN else 0,
        level=level,
    )


def test_compute_overall_healthy() -> None:
    badge = compute_overall([_fresh(HealthLevel.OK)], [], _runner(HealthLevel.OK))
    assert badge.level is HealthLevel.OK
    assert badge.label == "정상"


def test_compute_overall_partial_delay() -> None:
    badge = compute_overall(
        [_fresh(HealthLevel.OK), _fresh(HealthLevel.STALE)], [], _runner(HealthLevel.OK)
    )
    assert badge.label == "일부 지연"


def test_compute_overall_runner_down_is_stopped() -> None:
    badge = compute_overall([_fresh(HealthLevel.OK)], [], _runner(HealthLevel.STALE))
    assert badge.label == "멈춤"


def test_compute_overall_all_data_stale_is_stopped() -> None:
    badge = compute_overall([_fresh(HealthLevel.STALE)], [], _runner(HealthLevel.OK))
    assert badge.label == "멈춤"


def test_compute_overall_runner_never_ran_is_partial() -> None:
    badge = compute_overall([_fresh(HealthLevel.OK)], [], _runner(HealthLevel.UNKNOWN))
    assert badge.label == "일부 지연"
