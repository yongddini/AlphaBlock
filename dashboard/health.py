"""운영 상태(Health) 판정 로직 (WAN-30) — 순수 함수.

DB·상태파일에서 읽은 **원시 값**(최신 봉 시각, 펀딩 시각, 러너 하트비트 등)을 받아
신선도/생존 여부/종합 배지를 계산한다. I/O는 `dashboard.health_data`가 담당하고,
여기서는 부수효과 없는 계산만 두어 단위 테스트가 쉽게 한다.

판정 기준
---------
* **데이터 신선도**: `lag = now - 최신봉 open_time`. TF 주기 대비 `stale_multiplier`
  배를 넘으면 stale(빨강). 최신 봉이 없으면 UNKNOWN.
* **펀딩 신선도**: 예측 현재값의 `funding_time`은 다음 정산(미래)이라 lag가 음수면
  정상. 정산 주기(기본 8h) 대비 배수를 넘으면 stale.
* **러너 생존**: `lag = now - 마지막 폴링`. 폴링 간격 대비 배수를 넘으면 멈춤.
  한 번도 돌지 않았으면 UNKNOWN(미실행).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from data.models import timeframe_to_ms

#: 무기한 선물 펀딩 정산 주기(기본 8시간, ms).
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000


class HealthLevel(StrEnum):
    """상태 심각도. UI 색상(초록/빨강/회색)에 대응한다."""

    OK = "ok"
    """정상."""
    STALE = "stale"
    """지연/멈춤(빨강 경고)."""
    UNKNOWN = "unknown"
    """데이터 없음 / 미실행(회색)."""


class SeriesFreshness(BaseModel):
    """시리즈(심볼·TF) 하나의 데이터 신선도."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    last_open_time: int | None
    bar_count: int
    expected_interval_ms: int | None
    lag_ms: int | None
    level: HealthLevel


class FundingFreshness(BaseModel):
    """심볼 하나의 펀딩비 신선도."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    rate: float | None
    funding_time: int | None
    next_funding_time: int | None
    is_predicted: bool
    lag_ms: int | None
    level: HealthLevel


class RunnerStatus(BaseModel):
    """실시간 러너 생존 상태."""

    model_config = ConfigDict(frozen=True)

    ran: bool
    """한 번이라도 폴링 흔적이 있는지(=하트비트 존재)."""
    last_poll_ms: int | None
    last_notification_ms: int | None
    lag_ms: int | None
    level: HealthLevel


class CollectorStatus(BaseModel):
    """상시 구동 수집기(WAN-31) 생존 상태."""

    model_config = ConfigDict(frozen=True)

    ran: bool
    """한 번이라도 하트비트를 남긴 적이 있는지."""
    last_beat_ms: int | None
    lag_ms: int | None
    level: HealthLevel


class OverallBadge(BaseModel):
    """상단 종합 상태 배지."""

    model_config = ConfigDict(frozen=True)

    level: HealthLevel
    label: str


def _interval_ms(timeframe: str) -> int | None:
    """TF 주기(ms). 지원하지 않는 TF면 None."""
    try:
        return timeframe_to_ms(timeframe)
    except ValueError:
        return None


def classify_lag(
    reference_ms: int | None,
    now_ms: int,
    interval_ms: int | None,
    stale_multiplier: float,
) -> tuple[int | None, HealthLevel]:
    """`(lag, level)`을 계산한다.

    `reference_ms`가 없거나 주기를 모르면 `(None, UNKNOWN)`. lag가 주기의
    `stale_multiplier`배를 넘으면 STALE, 아니면 OK.
    """
    if reference_ms is None or interval_ms is None:
        return None, HealthLevel.UNKNOWN
    lag = now_ms - reference_ms
    if lag > stale_multiplier * interval_ms:
        return lag, HealthLevel.STALE
    return lag, HealthLevel.OK


def compute_freshness(
    rows: list[tuple[str, str, int | None, int]],
    *,
    now_ms: int,
    stale_multiplier: float,
) -> list[SeriesFreshness]:
    """시리즈별 원시값 `(symbol, timeframe, last_open_time, bar_count)`을 신선도로 변환."""
    out: list[SeriesFreshness] = []
    for symbol, timeframe, last_open_time, bar_count in rows:
        interval = _interval_ms(timeframe)
        lag, level = classify_lag(last_open_time, now_ms, interval, stale_multiplier)
        out.append(
            SeriesFreshness(
                symbol=symbol,
                timeframe=timeframe,
                last_open_time=last_open_time,
                bar_count=bar_count,
                expected_interval_ms=interval,
                lag_ms=lag,
                level=level,
            )
        )
    return out


def compute_funding_status(
    rows: list[tuple[str, float | None, int | None, int | None, bool]],
    *,
    now_ms: int,
    stale_multiplier: float,
    interval_ms: int = FUNDING_INTERVAL_MS,
) -> list[FundingFreshness]:
    """심볼별 `(symbol, rate, funding_time, next_funding_time, is_predicted)`을 신선도로 변환."""
    out: list[FundingFreshness] = []
    for symbol, rate, funding_time, next_funding_time, is_predicted in rows:
        lag, level = classify_lag(funding_time, now_ms, interval_ms, stale_multiplier)
        out.append(
            FundingFreshness(
                symbol=symbol,
                rate=rate,
                funding_time=funding_time,
                next_funding_time=next_funding_time,
                is_predicted=is_predicted,
                lag_ms=lag,
                level=level,
            )
        )
    return out


def compute_runner_status(
    *,
    last_poll_ms: int | None,
    last_notification_ms: int | None,
    now_ms: int,
    poll_interval_seconds: float,
    stale_multiplier: float,
) -> RunnerStatus:
    """러너 하트비트로 생존 상태를 판정한다.

    하트비트가 없으면 미실행(UNKNOWN). 있으면 폴링 간격의 `stale_multiplier`배를
    넘겨 갱신이 없을 때 멈춤(STALE).
    """
    interval_ms = int(poll_interval_seconds * 1000)
    lag, level = classify_lag(last_poll_ms, now_ms, interval_ms, stale_multiplier)
    return RunnerStatus(
        ran=last_poll_ms is not None,
        last_poll_ms=last_poll_ms,
        last_notification_ms=last_notification_ms,
        lag_ms=lag,
        level=level,
    )


def compute_collector_status(
    *,
    last_beat_ms: int | None,
    now_ms: int,
    heartbeat_interval_seconds: float,
    stale_multiplier: float,
) -> CollectorStatus:
    """수집기 하트비트로 생존 상태를 판정한다.

    하트비트가 없으면 미실행(UNKNOWN). 있으면 기대 하트비트 간격의
    `stale_multiplier`배를 넘겨 갱신이 없을 때 멈춤(STALE).
    """
    interval_ms = int(heartbeat_interval_seconds * 1000)
    lag, level = classify_lag(last_beat_ms, now_ms, interval_ms, stale_multiplier)
    return CollectorStatus(
        ran=last_beat_ms is not None,
        last_beat_ms=last_beat_ms,
        lag_ms=lag,
        level=level,
    )


def compute_overall(
    freshness: list[SeriesFreshness],
    funding: list[FundingFreshness],
    runner: RunnerStatus,
    collector: CollectorStatus | None = None,
) -> OverallBadge:
    """종합 배지: 전부 정상 / 일부 지연 / 멈춤.

    - 수집기·러너가 멈췄거나(데이터가 있고) 전부 stale이면 **멈춤**.
    - 일부만 stale하거나 수집기·러너가 미실행이면 **일부 지연**.
    - 그 외(데이터 정상 + 프로세스 정상)면 **정상**.
    """
    data_levels = [f.level for f in freshness] + [f.level for f in funding]
    known = [lvl for lvl in data_levels if lvl is not HealthLevel.UNKNOWN]
    any_stale = any(lvl is HealthLevel.STALE for lvl in known)
    all_stale = bool(known) and all(lvl is HealthLevel.STALE for lvl in known)

    proc_levels = [runner.level]
    if collector is not None:
        proc_levels.append(collector.level)
    proc_stale = any(lvl is HealthLevel.STALE for lvl in proc_levels)
    proc_unknown = any(lvl is HealthLevel.UNKNOWN for lvl in proc_levels)

    if proc_stale or all_stale:
        return OverallBadge(level=HealthLevel.STALE, label="멈춤")
    if any_stale or proc_unknown:
        return OverallBadge(level=HealthLevel.STALE, label="일부 지연")
    return OverallBadge(level=HealthLevel.OK, label="정상")
