"""꼬리 신선도 회귀 테스트 (WAN-156).

이 파일이 고정하는 것은 **동작**이지 라벨이 아니다(WAN-112/123 패턴). 핵심 명제 하나:

    5일 멈춘 시리즈는 「이상 없음」으로 보고되지 않는다.

WAN-156 사고에서 BNB·XRP·TRX가 5일 멈췄는데 복구 보고서는 전 TF `gaps_found: 0`,
검증은 `갭 0 → 통과`였다. 갭 검사가 봉과 봉 **사이**만 보기 때문이다. 여기서는 그
정확한 모양(꼬리만 잘린 시리즈 = 내부 갭 0)을 만들어 각 층이 결함으로 부르는지 본다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from data.freshness import (
    FUNDING_INTERVAL_MS,
    find_stale_funding,
    find_stale_series,
    window_gap_summary,
)
from data.gaps import find_gaps
from data.models import Candle
from data.repair import repair_all
from data.storage import OhlcvStore
from data.verify import verify_all

SYMBOL = "BTC/USDT:USDT"
TF = "15m"
TF_MS = 900_000
T0 = 1_700_000_000_000
#: 정지 폭 — 사고 당시와 같은 5일.
FIVE_DAYS_MS = 5 * 24 * 3_600_000


def _candles(
    count: int, *, start: int = T0, timeframe: str = TF, step: int = TF_MS
) -> list[Candle]:
    """구멍 없이 이어지는 봉 `count`개."""
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


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    with OhlcvStore(":memory:") as s:
        yield s


# -- 사고의 모양 자체 ---------------------------------------------------------


def test_tail_stall_has_no_internal_gap() -> None:
    """전제 확인: 꼬리만 멈춘 시리즈는 `find_gaps`가 **정직하게** 0을 낸다.

    이게 참이라서 신선도 층이 따로 필요하다. `find_gaps`를 고치는 게 아니다.
    """
    times = [T0 + i * TF_MS for i in range(200)]
    assert find_gaps(times, TF) == []


def test_five_day_stall_is_reported_stale() -> None:
    now = T0 + 199 * TF_MS + FIVE_DAYS_MS
    stale = find_stale_series([(SYMBOL, TF, T0 + 199 * TF_MS)], now_ms=now)
    assert len(stale) == 1
    assert stale[0].symbol == SYMBOL
    assert stale[0].lag_ms == FIVE_DAYS_MS
    assert stale[0].lag_intervals > 400  # 15m 기준 5일은 480주기


def test_fresh_series_is_not_stale() -> None:
    last = T0 + 199 * TF_MS
    assert find_stale_series([(SYMBOL, TF, last)], now_ms=last + TF_MS) == []


def test_empty_series_is_not_stale() -> None:
    """봉이 하나도 없는 건 정지가 아니라 미시작이다(Health의 UNKNOWN과 같은 취급)."""
    assert find_stale_series([(SYMBOL, TF, None)], now_ms=T0) == []


def test_unknown_timeframe_is_skipped() -> None:
    """주기를 모르면 문턱을 지어내지 않는다(오탐 방지)."""
    assert find_stale_series([(SYMBOL, "7s", T0)], now_ms=T0 + FIVE_DAYS_MS) == []


def test_stale_funding_series_detected() -> None:
    """펀딩비도 같은 자로 본다 — OHLCV 백필이 채우지 않는 별도 경로다(§5)."""
    now = T0 + 6 * 24 * 3_600_000
    stale = find_stale_funding([(SYMBOL, T0)], now_ms=now)
    assert len(stale) == 1
    assert stale[0].expected_interval_ms == FUNDING_INTERVAL_MS
    assert find_stale_funding([(SYMBOL, now - FUNDING_INTERVAL_MS)], now_ms=now) == []


# -- 복구 층 (§2) -------------------------------------------------------------


class _NoCallExchange:
    """호출되면 실패하는 거래소 — 갭이 없으면 네트워크를 안 탄다는 성질을 유지한다."""

    def fetch_ohlcv(self, *args: object, **kwargs: object) -> list[list[float]]:
        raise AssertionError("갭이 없는데 거래소를 호출했다")


def test_repair_reports_stall_even_with_zero_gaps(store: OhlcvStore) -> None:
    """🚨 회귀의 핵심: `gaps_found: 0`인데도 정지가 보고서에 뜬다."""
    store.upsert_candles(_candles(200))
    last = T0 + 199 * TF_MS

    summary = repair_all(
        _NoCallExchange(),
        store,
        now_ms=lambda: last + FIVE_DAYS_MS,
    )

    assert [s.gaps_found for s in summary.series] == [0]  # 갭 검사는 여전히 조용하다
    assert not summary.has_error
    assert summary.has_stale, "5일 멈춘 시리즈가 「이상 없음」으로 보고됐다"
    assert summary.has_defect
    assert summary.stale_series[0].timeframe == TF


def test_repair_of_fresh_series_reports_no_defect(store: OhlcvStore) -> None:
    store.upsert_candles(_candles(200))
    last = T0 + 199 * TF_MS
    summary = repair_all(_NoCallExchange(), store, now_ms=lambda: last + TF_MS)
    assert not summary.has_stale
    assert not summary.has_defect


def test_repair_summary_roundtrips_stale_series(store: OhlcvStore) -> None:
    """상태 파일(JSON)로 나갔다 들어와도 정지 정보가 남는다."""
    from data.repair import RepairSummary

    store.upsert_candles(_candles(200))
    last = T0 + 199 * TF_MS
    summary = repair_all(_NoCallExchange(), store, now_ms=lambda: last + FIVE_DAYS_MS)
    restored = RepairSummary.model_validate_json(summary.model_dump_json())
    assert restored.has_stale
    assert restored.stale_series[0].lag_ms == FIVE_DAYS_MS


def test_repair_includes_funding_staleness(store: OhlcvStore) -> None:
    store.upsert_candles(_candles(200))
    last = T0 + 199 * TF_MS
    summary = repair_all(
        _NoCallExchange(),
        store,
        now_ms=lambda: last + TF_MS,  # OHLCV는 신선
        funding_last_times=[(SYMBOL, last - 6 * 24 * 3_600_000)],
    )
    assert [s.timeframe for s in summary.stale_series] == ["funding"]


# -- 검증 층 (§2) -------------------------------------------------------------


def test_verify_flags_stall_and_fails_verdict(store: OhlcvStore) -> None:
    """갭·중복·정합성이 전부 깨끗해도 정지면 `sound`가 아니다."""
    store.upsert_candles(_candles(200))
    last = T0 + 199 * TF_MS

    report = verify_all(store, [SYMBOL], [TF], now_ms=last + FIVE_DAYS_MS)

    assert report.total_gaps == 0
    assert report.ok, "무결성 자체는 깨끗해야 이 테스트가 정지만 격리한다"
    assert report.has_stale
    assert not report.sound
    assert not report.strict_ok


def test_verify_fresh_series_is_sound(store: OhlcvStore) -> None:
    store.upsert_candles(_candles(200))
    report = verify_all(store, [SYMBOL], [TF], now_ms=T0 + 199 * TF_MS + TF_MS)
    assert report.sound
    assert not report.has_stale


# -- 창 연속성 (§3) -----------------------------------------------------------


def test_window_gap_summary_detects_hole() -> None:
    times = [T0 + i * TF_MS for i in range(10)] + [
        T0 + (10 + 480) * TF_MS + i * TF_MS for i in range(10)
    ]
    summary = window_gap_summary(times, TF)
    assert summary is not None
    assert "구멍" in summary


def test_window_gap_summary_none_when_contiguous() -> None:
    assert window_gap_summary([T0 + i * TF_MS for i in range(30)], TF) is None
