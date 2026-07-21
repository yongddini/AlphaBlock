"""OHLCV 무결성 검증 (WAN-44).

대량 1분봉 백필 이후 데이터가 백테스트(특히 WAN-41 1분봉 서브스텝)에 쓸 만큼
온전한지 확인한다. 세 가지를 본다:

1. **연속성(갭)**: 저장된 봉 사이의 내부 누락 구간. `data.gaps.find_gaps`를 재사용한다.
   신규 상장 이전·현재 진행 중 구간은 갭이 아니다(그 정의도 `find_gaps`가 처리).
2. **중복·정렬**: `(symbol, timeframe, open_time)` 기본키가 중복을 막지만, 방어적으로
   총 봉 수 대비 고유 open_time 수를 비교하고 오름차순 정렬을 확인한다.
3. **상위 TF 정합성**: 1분봉을 상위 TF(15m/1h/4h/1d)로 리샘플한 결과가 거래소에서
   직접 받아 저장한 상위 TF 봉과 (샘플 구간에서) OHLCV까지 일치하는지 본다.
   1m 커버리지가 온전한 버킷만 리샘플되므로, 갭 때문에 생기는 오탐은 없다.
4. **꼬리 신선도**(WAN-156): 시리즈의 마지막 봉이 TF 주기 대비 크게 지연됐는지.
   1~3은 **저장된 봉들 사이**만 보므로 시리즈가 통째로 멈춘 정지를 통과시킨다 —
   실제로 5일 멈춘 시리즈가 전 TF `갭 0`으로 「이상 없음」이었다. `data.freshness`가
   그 구멍을 메운다.

모두 순수 검증(읽기 전용)이라 부수효과가 없다. CLI(`alphablock verify`)와 테스트가
공유한다.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from data.freshness import DEFAULT_STALE_MULTIPLIER, StaleSeries, find_stale_series
from data.gaps import Gap, find_gaps, total_missing
from data.storage import OhlcvStore

# 리샘플 정합성 비교 시 허용 상대 오차(부동소수 왕복 오차 흡수).
_REL_TOL = 1e-6
# OHLC 필드 이름.
_PRICE_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True, slots=True)
class SeriesReport:
    """한 (심볼, TF) 시리즈의 연속성·중복·정렬 검증 결과."""

    symbol: str
    timeframe: str
    bar_count: int
    first_ms: int | None
    last_ms: int | None
    gaps: list[Gap]
    duplicates: int
    """총 봉 수 − 고유 open_time 수(기본키가 있으면 항상 0)."""
    monotonic: bool
    """open_time이 순수 오름차순인지(정렬·중복 없음)."""

    @property
    def missing(self) -> int:
        """갭에 포함된 총 누락 봉 수."""
        return total_missing(self.gaps)

    @property
    def has_gaps(self) -> bool:
        return bool(self.gaps)

    @property
    def integrity_ok(self) -> bool:
        """중복 없음 + 오름차순이면 참(갭은 거래소 원인일 수 있어 별도 취급)."""
        return self.duplicates == 0 and self.monotonic


@dataclass(frozen=True, slots=True)
class ParityMismatch:
    """리샘플 결과와 저장된 상위 TF 봉이 어긋난 한 지점."""

    open_time: int
    field: str
    resampled: float
    stored: float


@dataclass(frozen=True, slots=True)
class ParityReport:
    """1m→상위 TF 리샘플 정합성 결과(샘플 구간)."""

    symbol: str
    source_timeframe: str
    target_timeframe: str
    compared: int
    """리샘플과 저장 봉 양쪽에 존재해 비교한 버킷 수."""
    mismatches: list[ParityMismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """전체 검증 결과 집계."""

    series: list[SeriesReport]
    parity: list[ParityReport]
    stale: list[StaleSeries] = field(default_factory=list)
    """꼬리가 멈춘 시리즈(WAN-156). 갭·중복·정합성이 전부 깨끗해도 여기가 비지 않을 수 있다."""

    @property
    def ok(self) -> bool:
        """하드 실패(중복·역순·정합성 불일치)가 하나도 없으면 참.

        갭은 거래소 원인일 수 있어 하드 실패로 보지 않는다(`strict_ok` 참고).
        ⚠️ **정지도 여기 포함되지 않는다** — 신선도는 「저장된 데이터가 맞는가」가
        아니라 「수집이 돌고 있는가」의 문제라 성격이 다르다. 운영 판정은
        `sound`(= 정지까지 본 판정)를 쓴다. CLI는 `sound`로 종료 코드를 낸다.
        """
        return all(s.integrity_ok for s in self.series) and all(p.ok for p in self.parity)

    @property
    def has_stale(self) -> bool:
        return bool(self.stale)

    @property
    def sound(self) -> bool:
        """`ok`이면서 꼬리가 멈춘 시리즈도 없을 때만 참(운영 판정의 정본)."""
        return self.ok and not self.has_stale

    @property
    def strict_ok(self) -> bool:
        """`sound`이면서 어떤 시리즈에도 갭이 없을 때만 참."""
        return self.sound and not any(s.has_gaps for s in self.series)

    @property
    def total_gaps(self) -> int:
        return sum(len(s.gaps) for s in self.series)


def verify_series(store: OhlcvStore, symbol: str, timeframe: str) -> SeriesReport:
    """한 시리즈의 갭·중복·정렬을 검증한다."""
    times = store.open_times(symbol, timeframe)
    unique = len(set(times))
    monotonic = all(a < b for a, b in zip(times, times[1:], strict=False))
    gaps = find_gaps(times, timeframe) if times else []
    return SeriesReport(
        symbol=symbol,
        timeframe=timeframe,
        bar_count=len(times),
        first_ms=times[0] if times else None,
        last_ms=times[-1] if times else None,
        gaps=gaps,
        duplicates=len(times) - unique,
        monotonic=monotonic,
    )


def _values_match(a: float, b: float) -> bool:
    """두 값이 상대/절대 허용오차 내에서 같은지."""
    return abs(a - b) <= _REL_TOL * max(1.0, abs(a), abs(b))


def verify_resample_parity(
    store: OhlcvStore,
    symbol: str,
    source_timeframe: str,
    target_timeframe: str,
    *,
    sample_buckets: int = 500,
) -> ParityReport:
    """`source_timeframe`(예: 1m)을 `target_timeframe`으로 리샘플해 저장 봉과 비교한다.

    저장된 상위 TF 봉 중 **최근 `sample_buckets`개**만 표본으로 삼아, 그 구간의 하위
    TF 봉을 리샘플해 open_time이 겹치는 버킷의 OHLCV를 대조한다. 하위 TF 커버리지가
    온전한 버킷만 리샘플되므로, 하위 TF 갭이 만드는 오탐은 없다.
    """
    stored = store.load(symbol, target_timeframe)
    if stored.empty:
        return ParityReport(symbol, source_timeframe, target_timeframe, compared=0)
    stored = stored.tail(sample_buckets)
    window_start = int(stored["open_time"].iloc[0])
    window_end = int(stored["open_time"].iloc[-1])

    from data.models import timeframe_to_ms
    from data.resample import resample_ohlcv

    tgt_ms = timeframe_to_ms(target_timeframe)
    # 마지막 버킷을 온전히 구성하도록 한 버킷만큼 끝을 넓혀 하위 TF를 로드한다.
    source = store.load(
        symbol,
        source_timeframe,
        start_ms=window_start,
        end_ms=window_end + tgt_ms,
    )
    if source.empty:
        return ParityReport(symbol, source_timeframe, target_timeframe, compared=0)

    resampled = resample_ohlcv(source, source_timeframe, target_timeframe)
    stored_by_time = {int(r.open_time): r for r in stored.itertuples(index=False)}

    mismatches: list[ParityMismatch] = []
    compared = 0
    for row in resampled.itertuples(index=False):
        ot = int(row.open_time)
        ref = stored_by_time.get(ot)
        if ref is None:
            continue
        compared += 1
        for fld in _PRICE_FIELDS:
            rv = float(getattr(row, fld))
            sv = float(getattr(ref, fld))
            if not _values_match(rv, sv):
                mismatches.append(ParityMismatch(ot, fld, rv, sv))
        rv = float(row.volume)
        sv = float(ref.volume)
        if not _values_match(rv, sv):
            mismatches.append(ParityMismatch(ot, "volume", rv, sv))

    return ParityReport(
        symbol=symbol,
        source_timeframe=source_timeframe,
        target_timeframe=target_timeframe,
        compared=compared,
        mismatches=mismatches,
    )


def verify_all(
    store: OhlcvStore,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    parity_source: str = "1m",
    parity_targets: Sequence[str] = ("15m", "1h", "4h", "1d"),
    sample_buckets: int = 500,
    now_ms: int | None = None,
    stale_multiplier: float = DEFAULT_STALE_MULTIPLIER,
) -> VerifyReport:
    """지정한 심볼×TF의 시리즈 검증 + 정합성 검증 + 꼬리 신선도 판정을 모아 반환한다.

    정합성 검증은 `parity_source`(기본 1m)를 가진 심볼에 대해서만, 그 심볼이
    저장하고 있는 `parity_targets` TF와 대조한다.

    신선도(WAN-156)는 `now_ms` 기준으로 판정한다. None이면 현재 시각을 쓴다 —
    「지금 수집이 돌고 있는가」를 묻는 질문이라 기준 시각 없이는 답이 없다.
    """
    reference_ms = int(time.time() * 1000) if now_ms is None else now_ms
    series: list[SeriesReport] = [
        verify_series(store, symbol, tf) for symbol in symbols for tf in timeframes
    ]
    parity: list[ParityReport] = []
    for symbol in symbols:
        if store.count(symbol, parity_source) == 0:
            continue
        for target in parity_targets:
            if store.count(symbol, target) == 0:
                continue
            parity.append(
                verify_resample_parity(
                    store,
                    symbol,
                    parity_source,
                    target,
                    sample_buckets=sample_buckets,
                )
            )
    # 봉이 하나도 없는 시리즈(last_ms=None)는 정지가 아니라 미시작이라 판정에서 빠진다.
    stale = find_stale_series(
        [(s.symbol, s.timeframe, s.last_ms) for s in series],
        now_ms=reference_ms,
        stale_multiplier=stale_multiplier,
    )
    return VerifyReport(series=series, parity=parity, stale=stale)
