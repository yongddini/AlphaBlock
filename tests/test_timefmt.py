"""운영 출력 시각의 KST 통일 테스트 (WAN-172).

이 파일이 고정하는 계약은 셋이다.

1. **표시만 바뀌고 저장·계산은 UTC 그대로다** — 같은 epoch ms가 화면에서만 +9h로
   보이고, 판정에 쓰는 값(`StaleSeries.last_ms` 등)은 손대지 않는다.
2. **포맷터는 한 벌뿐이다** — cli·live·data·dashboard·backtest가 `common.timefmt`의
   같은 함수를 쓴다. 두 벌로 갈라지면 같은 사건이 화면과 로그에서 다른 시각으로
   보인다(이 저장소가 여러 번 겪은 부류의 사고 — WAN-91/95/112).
3. **데이터 열의 UTC는 안 건드린다** — 백테스트 CSV의 UTC 병기 열(WAN-106)은 KST
   전환의 영향을 받지 않는다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import dashboard.app as dashboard_app
import live.fill_report as fill_report
import live.notifier as notifier
from backtest.report import format_time_kst
from cli.main import _fmt_time as cli_fmt_time
from common import timefmt
from common.timefmt import KST_LABEL, format_kst, format_kst_zoned, format_utc
from data.freshness import StaleSeries, format_stale

#: UTC 자정 = KST 오전 9시. 변환이 실제로 일어났는지 이 오프셋 하나로 확인한다.
_UTC_MIDNIGHT_MS = int(datetime(2026, 7, 22, 0, 0, tzinfo=UTC).timestamp() * 1000)


# -- 1. 표시만 KST, 값은 UTC 그대로 --------------------------------------------


def test_format_kst_shifts_display_by_nine_hours() -> None:
    assert format_kst(_UTC_MIDNIGHT_MS) == "2026-07-22 09:00"
    assert format_utc(_UTC_MIDNIGHT_MS) == "2026-07-22 00:00"


def test_format_kst_zoned_labels_timezone() -> None:
    """시각이 문장 속에 단독으로 나가면 시간대 표기가 붙는다."""
    assert format_kst_zoned(_UTC_MIDNIGHT_MS) == f"2026-07-22 09:00 {KST_LABEL}"
    assert format_kst_zoned(_UTC_MIDNIGHT_MS, seconds=True).startswith("2026-07-22 09:00:00")


def test_missing_time_is_placeholder_not_epoch() -> None:
    """시각이 없으면 1970년을 찍지 않는다 — 없는 것과 오래된 것은 다르다."""
    assert format_kst(None) == timefmt.MISSING_TIME
    assert format_kst_zoned(None) == timefmt.MISSING_TIME
    assert format_utc(None) == timefmt.MISSING_TIME


def test_formatting_does_not_touch_stored_values() -> None:
    """포맷은 순수 함수다 — 같은 입력을 두 번 찍어도 값이 안 변한다."""
    stale = StaleSeries(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        last_ms=_UTC_MIDNIGHT_MS,
        lag_ms=3 * 3_600_000,
        expected_interval_ms=3_600_000,
        threshold_ms=2 * 3_600_000,
    )
    first = format_stale(stale)
    second = format_stale(stale)

    assert first == second
    # 표시에는 KST가 들어가지만 판정에 쓰는 값은 UTC epoch ms 그대로다.
    assert f"2026-07-22 09:00 {KST_LABEL}" in first
    assert stale.last_ms == _UTC_MIDNIGHT_MS
    assert stale.lag_ms == 3 * 3_600_000


# -- 2. 포맷터는 한 벌 ---------------------------------------------------------


def test_cli_status_time_uses_shared_kst_formatter() -> None:
    assert cli_fmt_time(_UTC_MIDNIGHT_MS) == format_kst_zoned(_UTC_MIDNIGHT_MS)
    assert "UTC" not in cli_fmt_time(_UTC_MIDNIGHT_MS)


def test_dashboard_health_time_uses_shared_kst_formatter() -> None:
    assert dashboard_app._fmt_time(_UTC_MIDNIGHT_MS) == format_kst_zoned(_UTC_MIDNIGHT_MS)


def test_live_outputs_use_shared_kst_formatter() -> None:
    """텔레그램 알림·체결률 리포트가 같은 함수를 쓴다."""
    assert notifier._fmt_time(_UTC_MIDNIGHT_MS) == format_kst_zoned(_UTC_MIDNIGHT_MS)
    assert fill_report._fmt_ms(_UTC_MIDNIGHT_MS) == format_kst(_UTC_MIDNIGHT_MS)


def test_backtest_report_kst_helper_delegates_to_common() -> None:
    """대시보드 거래 표(WAN-146)의 기존 진입점도 같은 구현을 쓴다."""
    assert format_time_kst(_UTC_MIDNIGHT_MS) == format_kst(_UTC_MIDNIGHT_MS)


# -- 3. 로그 시각 ---------------------------------------------------------------


def test_use_kst_logging_makes_asctime_korean_time() -> None:
    """로그 `%(asctime)s`가 머신 로컬이 아니라 KST로 찍힌다.

    ⚠️ `staticmethod` 래핑이 빠지면 여기서 TypeError가 난다(함수가 디스크립터라
    `self`가 첫 인자로 묶인다) — 라벨이 아니라 동작으로 고정한다.
    """
    previous = logging.Formatter.converter
    try:
        timefmt.use_kst_logging()
        formatter = logging.Formatter("%(asctime)s", datefmt="%Y-%m-%d %H:%M")
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="x",
            args=(),
            exc_info=None,
        )
        record.created = _UTC_MIDNIGHT_MS / 1000
        assert formatter.format(record) == "2026-07-22 09:00"
    finally:
        logging.Formatter.converter = previous


def test_kst_log_format_carries_timezone_label() -> None:
    assert KST_LABEL in timefmt.kst_log_format()
    assert timefmt.kst_log_format("%(message)s") == "%(message)s"
