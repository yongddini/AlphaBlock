"""부분 봉(형성 도중에 확정 라벨을 달고 저장된 상위TF 봉) 스캔 (WAN-327).

`data.verify`의 리샘플 정합성 검사는 「저장 봉 ≠ 1분봉 합」을 잡아내지만 **왜 다른지는
가르지 않는다**. 그래서 두 가지가 한 수로 뭉쳐 나온다:

* **손상** — 봉이 형성 도중에 잘려 저장된 것(거래량이 통째로 모자라고, 잘린 뒤에 찍혔을
  고가·저가·종가가 빠져 있다). WAN-314 §2가 진단·수정한 그 서명이다.
* **노이즈** — 가격은 완전히 같고 거래량 끝자리만 다른 것(저장이 오히려 **더 크다**).
  1분봉 쪽이 조금 모자란 것이라 상위TF 봉은 멀쩡하고, **엔진은 거래량을 읽지 않으므로**
  백테·매매 판단에 영향이 없다.

두 부류를 안 가르면 감시가 상시 빨간불이 되어 **진짜 부분 봉이 그 안에 묻힌다**(WAN-318
§3 「정상 정지가 failed」·WAN-321 「거짓 경보로 진짜가 안 보임」과 같은 부류). 실제로
WAN-327에서 그 일이 일어났다 — 「BTC 4h 136건」이 두 부류의 합이라 진행 중인 고장으로
두 번 잘못 읽혔다.

📌 **판정자는 가격이 아니라 거래량이다.** 「가격이 틀렸는가」로 가르면 부분 봉을 놓친다 —
그 버킷의 고가·저가가 **잘리기 전에 이미 찍혀** 있으면 부분 봉이어도 가격이 맞는다(실측
반례: 2026-07-21 BNB 4h는 거래량 41.9%인데 high 오차 0.0bp). 그래도 종가·거래량이 틀리고
극값이 갱신될 구간을 통째로 잃은 손상된 봉이다. 그래서 판정자는
**`저장 거래량 < 리샘플 × 0.99`**이고, 실측에서 두 부류는 이 선에서 완전히 갈린다
(손상 ≤70% · 노이즈 ≥100.0%).

읽기 전용이다 — 이 모듈은 아무것도 쓰지 않는다(고치는 것은 사람이 하는 백필, WAN-194 원칙).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from data.models import timeframe_to_ms
from data.resample import resample_ohlcv
from data.storage import OhlcvStore

#: 가격 비교 허용 상대 오차(`data.verify._REL_TOL`과 같은 자 — 부동소수 왕복 오차 흡수).
PRICE_REL_TOL = 1e-6
#: 거래량이 이 비율 미만이면 「형성 도중에 잘린 봉」으로 본다(위 도크스트링 §판정자).
PARTIAL_VOLUME_RATIO = 0.99
#: 거래량 비교 허용 상대 오차 — 이 안이면 「같다」로 보고 노이즈로도 세지 않는다.
VOLUME_REL_TOL = 1e-6

_PRICE_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")
_ALL_FIELDS: tuple[str, ...] = (*_PRICE_FIELDS, "volume")

#: 불일치의 성격. `partial`·`price_only`는 **손상**(엔진 영향 가능), `volume_noise`는 무해.
BarKind = Literal["partial", "price_only", "volume_noise"]


def _values_match(a: float, b: float, tol: float) -> bool:
    """두 값이 상대/절대 허용오차 내에서 같은지(`data.verify._values_match`와 같은 식)."""
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _field(row: object, name: str) -> float:
    """봉 한 행(`itertuples` 네임드튜플 또는 `Series`)에서 숫자 필드를 읽는다."""
    return float(getattr(row, name))


@dataclass(frozen=True, slots=True)
class BarDiscrepancy:
    """저장 봉 하나와 그 구간 1분봉 합의 차이 — 성격까지 분류한 한 건."""

    symbol: str
    timeframe: str
    open_time: int
    kind: BarKind
    resampled_volume: float
    stored_volume: float
    price_fields: tuple[str, ...]
    """허용오차를 벗어난 OHLC 필드 이름(없으면 빈 튜플)."""
    max_price_bp: float
    """OHLC 필드 중 최대 상대 오차(bp). 가격이 전부 맞으면 0.0."""

    @property
    def volume_ratio(self) -> float:
        """저장 거래량 ÷ 리샘플 거래량. 리샘플이 0이면 `float('nan')`."""
        if self.resampled_volume == 0.0:
            return float("nan")
        return self.stored_volume / self.resampled_volume

    @property
    def damaged(self) -> bool:
        """엔진에 영향을 줄 수 있는 손상인지(노이즈가 아닌지)."""
        return self.kind != "volume_noise"

    @property
    def price_wrong(self) -> bool:
        return bool(self.price_fields)


def is_bit_identical(resampled: object, stored: object) -> bool:
    """저장 봉이 리샘플 값과 **비트 단위로** 같은지(허용오차 없음).

    이것은 **유래의 지문**이다(WAN-327 §1-3). 상위TF 봉이 거래소에서 독립적으로 온 것이면
    거래량 합의 부동소수 누적 순서가 달라 끝자리가 어긋나는 버킷이 섞인다(실측 BTC·ETH 4h
    67~68%). 반대로 `data.aggregate`(WAN-175/307)가 1분봉에서 **집계해 넣은** 봉은 리샘플
    값을 그대로 쓴 것이라 **100%** 일치한다 — 그러면 이 스캔도 `verify`의 정합성 검사도 그
    시리즈에 **아무것도 물어보지 못한다**(자기 자신과의 비교). 「불일치 0」을 「깨끗함」으로
    읽기 전에 이 비율을 봐야 한다.
    """
    return all(_field(resampled, fld) == _field(stored, fld) for fld in _ALL_FIELDS)


def classify_bucket(
    symbol: str,
    timeframe: str,
    open_time: int,
    resampled: object,
    stored: object,
) -> BarDiscrepancy | None:
    """한 버킷의 리샘플 값과 저장 값을 비교해 분류한다(순수 함수).

    허용오차 안이면 `None`(불일치 아님). 아니면 성격을 판정해 한 건으로 낸다:

    * `partial` — 저장 거래량 < 리샘플 × `PARTIAL_VOLUME_RATIO`(형성 중 저장 서명).
      **가격이 맞아도 손상이다**(위 도크스트링 §판정자).
    * `price_only` — 거래량은 모자라지 않는데 OHLC가 다르다. 원인 미상이지만 엔진이
      읽는 값이 틀린 것이라 손상으로 센다(조용히 무해로 넘기지 않는다).
    * `volume_noise` — 가격은 같고 거래량만 다르되 모자라지 않다(저장 ≥ 리샘플×0.99).
    """
    price_fields = tuple(
        fld
        for fld in _PRICE_FIELDS
        if not _values_match(_field(resampled, fld), _field(stored, fld), PRICE_REL_TOL)
    )
    max_bp = 0.0
    for fld in _PRICE_FIELDS:
        rv = _field(resampled, fld)
        sv = _field(stored, fld)
        max_bp = max(max_bp, 10_000.0 * abs(rv - sv) / max(abs(rv), 1e-12))
    rvol = _field(resampled, "volume")
    svol = _field(stored, "volume")
    volume_differs = not _values_match(rvol, svol, VOLUME_REL_TOL)
    if not price_fields and not volume_differs:
        return None

    kind: BarKind
    if svol < PARTIAL_VOLUME_RATIO * rvol:
        kind = "partial"
    elif price_fields:
        kind = "price_only"
    else:
        kind = "volume_noise"
    return BarDiscrepancy(
        symbol=symbol,
        timeframe=timeframe,
        open_time=int(open_time),
        kind=kind,
        resampled_volume=rvol,
        stored_volume=svol,
        price_fields=price_fields,
        max_price_bp=max_bp if price_fields else 0.0,
    )


@dataclass(frozen=True, slots=True)
class SeriesScan:
    """한 (심볼, TF) 시리즈의 전 구간 스캔 결과."""

    symbol: str
    timeframe: str
    source_timeframe: str
    compared: int
    """리샘플과 저장 봉 양쪽에 존재해 비교한 버킷 수."""
    discrepancies: list[BarDiscrepancy] = field(default_factory=list)
    exact_matches: int = 0
    """리샘플과 **비트 단위로** 같은 버킷 수 — 유래의 지문(`is_bit_identical`)."""

    @property
    def bit_identical_ratio(self) -> float | None:
        """비트 일치 비율. 1.0이면 이 시리즈는 1분봉에서 **집계돼 들어온** 것으로 읽는다
        (= 검사가 성립하지 않는다). 비교 버킷이 없으면 `None`."""
        if self.compared == 0:
            return None
        return self.exact_matches / self.compared

    @property
    def damaged(self) -> list[BarDiscrepancy]:
        return [d for d in self.discrepancies if d.damaged]

    @property
    def noise(self) -> list[BarDiscrepancy]:
        return [d for d in self.discrepancies if not d.damaged]

    @property
    def ok(self) -> bool:
        """손상이 하나도 없으면 참(노이즈는 통과 — 엔진이 거래량을 안 읽는다)."""
        return not self.damaged

    @property
    def damaged_span(self) -> tuple[int, int] | None:
        """손상 봉의 (첫 시각, 마지막 시각). 손상이 없으면 `None`."""
        times = [d.open_time for d in self.damaged]
        return (min(times), max(times)) if times else None


def _iter_windows(lo: int, hi: int, step_ms: int) -> Iterator[tuple[int, int]]:
    """`[lo, hi]`를 `step_ms` 창으로 끊어 (시작, 끝) 쌍을 낸다(끝은 포함)."""
    cur = lo
    while cur <= hi:
        yield cur, min(cur + step_ms - 1, hi)
        cur += step_ms


def scan_symbol(
    store: OhlcvStore,
    symbol: str,
    timeframes: Sequence[str],
    *,
    source_timeframe: str = "1m",
    start_ms: int | None = None,
    end_ms: int | None = None,
    chunk_days: int = 120,
) -> list[SeriesScan]:
    """한 심볼의 여러 상위TF를 **1분봉을 한 번만 읽어** 전 구간 대조한다(읽기 전용).

    `data.verify.verify_resample_parity`가 최근 `sample_buckets`개만 보는 것과 달리 전
    이력을 훑는다 — 「몇 개·언제」를 세는 것이 이 스캔의 목적이기 때문이다. 1분봉 로딩이
    비용의 대부분이라 TF마다 다시 읽지 않고 시간 창 하나에서 모든 대상 TF로 리샘플한다.
    메모리는 `chunk_days` 창으로 묶인다.

    하위TF 커버리지가 온전한 버킷만 리샘플되므로(`resample_ohlcv` 규약) 하위TF 갭이
    만드는 오탐은 없다 — 갭은 `verify_series`가 따로 본다. 판정은 `classify_bucket`
    한 곳에서만 내려 `verify`와 자가 갈라지지 않는다.
    """
    targets = [tf for tf in timeframes if store.count(symbol, tf) > 0]
    stored: dict[str, pd.DataFrame] = {
        tf: store.load(symbol, tf, start_ms=start_ms, end_ms=end_ms) for tf in targets
    }
    stored = {tf: df for tf, df in stored.items() if not df.empty}
    if not stored:
        return [SeriesScan(symbol, tf, source_timeframe, compared=0) for tf in timeframes]

    lo = min(int(df["open_time"].iloc[0]) for df in stored.values())
    hi = max(int(df["open_time"].iloc[-1]) for df in stored.values())
    widest_ms = max(timeframe_to_ms(tf) for tf in stored)
    step_ms = max(chunk_days * 86_400_000, widest_ms)

    compared: dict[str, int] = {tf: 0 for tf in stored}
    exact: dict[str, int] = {tf: 0 for tf in stored}
    found: dict[str, list[BarDiscrepancy]] = {tf: [] for tf in stored}
    for win_lo, win_hi in _iter_windows(lo, hi, step_ms):
        # 창 끝 버킷을 온전히 구성하도록 가장 넓은 TF 한 버킷만큼 끝을 넓힌다.
        source = store.load(symbol, source_timeframe, start_ms=win_lo, end_ms=win_hi + widest_ms)
        if source.empty:
            continue
        for tf, df in stored.items():
            chunk = df[(df["open_time"] >= win_lo) & (df["open_time"] <= win_hi)]
            if chunk.empty:
                continue
            resampled = resample_ohlcv(source, source_timeframe, tf)
            stored_by_time = {int(r.open_time): r for r in chunk.itertuples(index=False)}
            for row in resampled.itertuples(index=False):
                ref = stored_by_time.get(int(row.open_time))
                if ref is None:
                    continue
                compared[tf] += 1
                if is_bit_identical(row, ref):
                    exact[tf] += 1
                one = classify_bucket(symbol, tf, int(row.open_time), row, ref)
                if one is not None:
                    found[tf].append(one)

    return [
        SeriesScan(
            symbol=symbol,
            timeframe=tf,
            source_timeframe=source_timeframe,
            compared=compared.get(tf, 0),
            discrepancies=found.get(tf, []),
            exact_matches=exact.get(tf, 0),
        )
        for tf in timeframes
        if tf in stored
    ]


def scan_series(
    store: OhlcvStore,
    symbol: str,
    timeframe: str,
    *,
    source_timeframe: str = "1m",
    start_ms: int | None = None,
    end_ms: int | None = None,
    chunk_days: int = 120,
) -> SeriesScan:
    """`scan_symbol`의 한 시리즈용 얇은 래퍼(같은 판정·같은 규약)."""
    scans = scan_symbol(
        store,
        symbol,
        [timeframe],
        source_timeframe=source_timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        chunk_days=chunk_days,
    )
    if scans:
        return scans[0]
    return SeriesScan(symbol, timeframe, source_timeframe, compared=0)


def scan_all(
    store: OhlcvStore,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    source_timeframe: str = "1m",
    start_ms: int | None = None,
    end_ms: int | None = None,
    chunk_days: int = 120,
) -> list[SeriesScan]:
    """여러 심볼×TF를 스캔한다. 하위TF나 대상 TF가 없는 조합은 조용히 건너뛴다."""
    scans: list[SeriesScan] = []
    for symbol in symbols:
        if store.count(symbol, source_timeframe) == 0:
            continue
        scans.extend(
            scan_symbol(
                store,
                symbol,
                timeframes,
                source_timeframe=source_timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                chunk_days=chunk_days,
            )
        )
    return scans


def repair_frame(stored: pd.DataFrame, resampled: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """저장 프레임의 **손상 봉만** 리샘플 값으로 갈아끼운 사본을 낸다(메모리 전용).

    ⚠️ **DB를 쓰지 않는다** — 「고치기 전후로 같은 좌표를 돌려 본다」(WAN-327 완료기준 2)를
    위한 비파괴 반사실이다. 실제 수정은 거래소 재수집(백필)이고 사람이 한다.

    노이즈 봉은 **건드리지 않는다**(저장이 정본에 더 가깝다 — 1분봉 쪽이 모자란 것이라
    그 값으로 덮으면 멀쩡한 봉을 미세하게 망친다).

    Returns:
        (갈아끼운 사본, 바뀐 봉 수).
    """
    if stored.empty or resampled.empty:
        return stored.copy(), 0
    symbol = str(stored["symbol"].iloc[0]) if len(stored) else ""
    timeframe = str(stored["timeframe"].iloc[0]) if len(stored) else ""
    by_time = {int(r.open_time): r for r in resampled.itertuples(index=False)}
    out = stored.copy().reset_index(drop=True)
    replaced = 0
    for idx, ref in enumerate(stored.itertuples(index=False)):
        src = by_time.get(int(ref.open_time))
        if src is None:
            continue
        found = classify_bucket(symbol, timeframe, int(ref.open_time), src, ref)
        if found is None or not found.damaged:
            continue
        for fld in (*_PRICE_FIELDS, "volume"):
            out.loc[idx, fld] = _field(src, fld)
        replaced += 1
    return out, replaced
