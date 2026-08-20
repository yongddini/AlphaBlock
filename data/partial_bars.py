"""부분 봉(형성 도중에 확정 라벨을 달고 저장된 상위TF 봉) 스캔 (WAN-327).

`data.verify`의 리샘플 정합성 검사는 「저장 봉 ≠ 1분봉 합」을 잡아내지만 **왜 다른지는
가르지 않는다**. 그래서 두 가지가 한 수로 뭉쳐 나온다:

* **손상** — 봉이 형성 도중에 잘려 저장된 것(거래량이 통째로 모자라고, 잘린 뒤에 찍혔을
  고가·저가·종가가 빠져 있다). WAN-314 §2가 진단·수정한 그 서명이다.
* **노이즈** — 두 부류다. (1) **거래량 노이즈**: 가격은 완전히 같고 거래량 끝자리만 다른
  것(저장이 오히려 **더 크다**). 1분봉 쪽이 조금 모자란 것이라 상위TF 봉은 멀쩡하고,
  **엔진은 거래량을 읽지 않으므로** 백테·매매 판단에 영향이 없다. (2) **가격 노이즈**
  (WAN-337 §2): 가격 차이가 **호가 눈금 몇 칸**에 불과한 것 — 다른 가격대가 아니라 같은
  가격의 다른 표현에 가깝다.

두 부류를 안 가르면 감시가 상시 빨간불이 되어 **진짜 부분 봉이 그 안에 묻힌다**(WAN-318
§3 「정상 정지가 failed」·WAN-321 「거짓 경보로 진짜가 안 보임」과 같은 부류). 실제로
WAN-327에서 그 일이 일어났다 — 「BTC 4h 136건」이 두 부류의 합이라 진행 중인 고장으로
두 번 잘못 읽혔다.

📌 **부분 봉의 판정자는 가격이 아니라 거래량이다.** 「가격이 틀렸는가」로 가르면 부분 봉을
놓친다 — 그 버킷의 고가·저가가 **잘리기 전에 이미 찍혀** 있으면 부분 봉이어도 가격이
맞는다(실측 반례: 2026-07-21 BNB 4h는 거래량 41.9%인데 high 오차 0.0bp). 그래도 종가·
거래량이 틀리고 극값이 갱신될 구간을 통째로 잃은 손상된 봉이다. 그래서 판정자는
**`저장 거래량 < 리샘플 × 0.99`**이고, 실측에서 두 부류는 이 선에서 완전히 갈린다
(손상 ≤70% · 노이즈 ≥100.0%).

📌 **가격 축에도 같은 구분이 필요했다(WAN-337 §2).** 거래량이 멀쩡한데 OHLC만 다른 부류
(`price_only`)는 옛 판정에서 **크기와 무관하게 전부 손상**이었다. 그래서 **호가 한 칸짜리
차이도 🚨로 찍혀** 거래량 축에서 겪은 그 상황(상시 빨간불 → 진짜 이상이 그 안에 묻힘)이
가격 축에서 그대로 재발했다 — 실제로 2026-08-19 ETH 1봉이 「WAN-314 재발 방지 이후의 새
손상」처럼 읽혔는데 크기를 보니 **0.1bp**였다. 이제 그 자는 **틱 배수**이고
(`PRICE_NOISE_TICKS`), 그 아래는 `price_noise`로 따로 보고한다. ⚠️ **점검 항목이 준 것이
아니다** — 무엇을 보는가는 불변이고 **결과를 종료 코드·보고로 옮기는 법**만 바뀐다.

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

#: 가격 오차가 **호가 단위(틱)의 이 배수 이하**면 손상이 아니라 노이즈로 본다(WAN-337 §2).
#:
#: 🚨 **자가 절대 bp가 아니라 틱 배수인 것은 취향이 아니라 실측이다 — 두 축은 같은 순서로
#: 정렬되지도 않는다.** 로컬 15m 전 이력(6종목 · `price_only` 21건) 실측에서 **1틱짜리가
#: bp로는 0.0145 ~ 1.3286bp로 91배 흩어지고**(BTC 2024-10-28 0.1/68,900 = 0.0145bp ↔
#: SOL 2026-07-18 0.01/75.3 = 1.3286bp — **둘 다 정확히 1틱**), 거꾸로 **bp가 작은 쪽이
#: 틱으로는 큰 경우**가 있다(BTC 2026-07-16은 0.2337bp인데 **15틱**이라 위 SOL 1틱보다
#: bp는 작고 틱은 15배 크다). 즉 어떤 bp 문턱을 고르든 **같은 현상을 종목·가격대에 따라
#: 다르게 찍는다** — 「TRX의 1bp와 BTC의 1bp는 뜻이 다르다」.
#:
#: 📌 **값의 근거 — 틱 축에서는 분포가 실제로 갈린다.** 같은 21건의 틱 배수는
#: `1(9건) · 2(3건) · 3 · 5`(호가 잔돈)와 `12 · 15 · 36 · 41`(2026-07-15~21) 그리고
#: `289 · 314 · 475`(2024-03-27 BTC·ETH·SOL)로 나뉘고, **5와 12 사이가 유일한 빈 구간**이다.
#: 10은 그 간극 안이라 잔돈 최대(5)의 2배 · 손상 최소(12)의 1/1.2로 양쪽에 여유가 있다.
#: 📌 중간 무리(12~41틱)가 **손상으로 남는 것이 맞다** — 전부 WAN-327이 지목한 2026-07-12~24
#: 손상 무리 안이고 전부 `close` 필드다(형성 중에 잘린 봉이 잃는 바로 그 값).
#:
#: ⚠️ **자를 느슨하게 해 경보를 줄이려는 값이 아니다** — 회귀 테스트가 2024-03-27의 **실제
#: 값**으로 「이 문턱이 그 손상을 지우지 않음」을 고정한다(WAN-330의 잔존율 가드가 함정 값으로
#: 테스트를 건 것과 같은 방식). 점검 항목은 불변이고 **결과를 종료 코드·보고로 옮기는 법**만
#: 바뀐다(WAN-327 §3 · WAN-318 §3 · WAN-321과 같은 문장).
PRICE_NOISE_TICKS = 10.0

#: 호가 단위 추정 시 인정하는 최대 소수 자릿수 — 부동소수 표현이 만든 꼬리를 「더 고운
#: 눈금」으로 오해하지 않도록 한다.
_MAX_PRICE_DECIMALS = 12

_PRICE_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")
_ALL_FIELDS: tuple[str, ...] = (*_PRICE_FIELDS, "volume")

#: 불일치의 성격. `partial`·`price_only`는 **손상**(엔진 영향 가능),
#: `volume_noise`·`price_noise`는 무해.
BarKind = Literal["partial", "price_only", "price_noise", "volume_noise"]

#: 손상이 아닌(= 감시를 빨갛게 만들지 않는) 부류. `damaged`가 이 집합을 **읽는다** — 새
#: 부류를 넣을 때 성격을 고르도록 강제한다(WAN-321이 장부 분류에서 쓴 것과 같은 규약).
NOISE_KINDS: frozenset[str] = frozenset({"volume_noise", "price_noise"})


def infer_price_tick(values: Sequence[float]) -> float:
    """주어진 가격들이 놓인 **호가 눈금**을 소수 자릿수에서 추정한다(순수 함수).

    ⚠️ **거래소 메타데이터를 쓰지 않는다** — 이 저장소에는 틱 정보가 없고, 있어도 틱 크기는
    시간에 따라 바뀌므로 「그 시점의 눈금」은 **그 시점의 값**에서 읽는 것이 맞다. 실제로
    6년 전 이력에서 뽑은 시리즈 단위 추정은 SOL을 `0.0001`로 보는데 2023-09의 SOL 값은
    `17.979`(= `0.001` 눈금)라, 같은 2틱 차이가 20틱으로 부풀려진다. 그래서 추정은
    **비교하는 두 봉의 값만** 본다.

    파이썬 `repr`은 왕복 최단 표기라 SQLite REAL로 저장된 십진 호가가 꼬리를 만들지 않는다.
    지수 표기·과도한 자릿수는 버린다(`_MAX_PRICE_DECIMALS`).

    📌 **틀리는 방향이 안전하다** — 값이 모두 거칠어 눈금을 과대 추정하면 같은 차이가 **더
    적은** 틱으로 세어져 노이즈로 삼켜질 수 있는데, `repr(float)`가 정수에도 `'100.0'`을
    내므로 추정 눈금은 실제보다 **고운** 쪽으로 치우친다(= 틱이 더 많이 세어져 손상으로
    찍힌다). 잔돈을 손상이라 부르는 실수는 보이지만 그 반대는 안 보인다.
    """
    decimals = 0
    for value in values:
        text = repr(float(value))
        if "e" in text or "E" in text or "." not in text:
            continue
        decimals = max(decimals, min(len(text.split(".")[1]), _MAX_PRICE_DECIMALS))
    return 10.0**-decimals


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
    max_price_ticks: float = 0.0
    """OHLC 필드 중 최대 오차를 **호가 단위(틱)의 배수**로 (WAN-337 §2).

    📌 **판정에 쓰는 자는 bp가 아니라 이것이다** — 두 축은 같은 순서로 정렬되지도 않는다
    (`PRICE_NOISE_TICKS` 주석의 실측). `max_price_bp`는 사람이 읽는 참고 열로 남는다."""
    price_tick: float = 0.0
    """이 버킷의 추정 호가 단위(`infer_price_tick`). 0.0이면 추정하지 않았다(가격이 다 맞음)."""

    @property
    def volume_ratio(self) -> float:
        """저장 거래량 ÷ 리샘플 거래량. 리샘플이 0이면 `float('nan')`."""
        if self.resampled_volume == 0.0:
            return float("nan")
        return self.stored_volume / self.resampled_volume

    @property
    def damaged(self) -> bool:
        """엔진에 영향을 줄 수 있는 손상인지(노이즈가 아닌지).

        🚨 `NOISE_KINDS`를 **읽는다** — 「노이즈가 아니면 손상」을 부류 이름 하나로 적으면
        새 노이즈 부류를 넣을 때 조용히 손상으로 세어져 감시가 다시 빨개진다."""
        return self.kind not in NOISE_KINDS

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
    *,
    price_tick: float | None = None,
) -> BarDiscrepancy | None:
    """한 버킷의 리샘플 값과 저장 값을 비교해 분류한다(순수 함수).

    허용오차 안이면 `None`(불일치 아님). 아니면 성격을 판정해 한 건으로 낸다:

    * `partial` — 저장 거래량 < 리샘플 × `PARTIAL_VOLUME_RATIO`(형성 중 저장 서명).
      **가격이 맞아도 손상이다**(위 도크스트링 §판정자).
    * `price_only` — 거래량은 모자라지 않는데 OHLC가 **호가 눈금 여러 칸만큼** 다르다.
      원인 미상이지만 엔진이 읽는 값이 틀린 것이라 손상으로 센다.
    * `price_noise` — 가격 차이가 `PRICE_NOISE_TICKS`틱 이하다(호가 잔돈 · WAN-337 §2).
    * `volume_noise` — 가격은 같고 거래량만 다르되 모자라지 않다(저장 ≥ 리샘플×0.99).

    🚨 **분류는 여기 한 곳에서만 내린다** — 전 이력 스캔(`scan_symbol`)과 `data.verify`가
    같은 자를 쓰도록(WAN-327 규약). `price_tick`을 주면 그 눈금을 쓰고, 안 주면 비교하는 두
    봉의 OHLC에서 추정한다(`infer_price_tick` — 시리즈 단위 추정이 왜 틀리는지는 그 함수).
    """
    price_fields = tuple(
        fld
        for fld in _PRICE_FIELDS
        if not _values_match(_field(resampled, fld), _field(stored, fld), PRICE_REL_TOL)
    )
    max_bp = 0.0
    max_abs = 0.0
    for fld in _PRICE_FIELDS:
        rv = _field(resampled, fld)
        sv = _field(stored, fld)
        max_bp = max(max_bp, 10_000.0 * abs(rv - sv) / max(abs(rv), 1e-12))
        max_abs = max(max_abs, abs(rv - sv))
    rvol = _field(resampled, "volume")
    svol = _field(stored, "volume")
    volume_differs = not _values_match(rvol, svol, VOLUME_REL_TOL)
    if not price_fields and not volume_differs:
        return None

    tick = 0.0
    max_ticks = 0.0
    if price_fields:
        tick = (
            price_tick
            if price_tick is not None and price_tick > 0.0
            else infer_price_tick(
                [_field(resampled, f) for f in _PRICE_FIELDS]
                + [_field(stored, f) for f in _PRICE_FIELDS]
            )
        )
        max_ticks = max_abs / tick

    kind: BarKind
    if svol < PARTIAL_VOLUME_RATIO * rvol:
        # 거래량이 모자라면 가격이 몇 틱이든 **형성 중에 잘린 봉**이다(판정자는 거래량).
        kind = "partial"
    elif price_fields:
        kind = "price_noise" if max_ticks <= PRICE_NOISE_TICKS else "price_only"
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
        max_price_ticks=max_ticks,
        price_tick=tick,
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
        """무해한 불일치 — 거래량 잔돈(`volume_noise`)과 호가 잔돈(`price_noise`)."""
        return [d for d in self.discrepancies if not d.damaged]

    @property
    def ok(self) -> bool:
        """손상이 하나도 없으면 참 — 노이즈는 통과한다.

        거래량 노이즈는 엔진이 거래량을 안 읽어서, 가격 노이즈는 차이가 호가 몇 칸이라
        같은 가격의 다른 표현에 가까워서다(WAN-337 §2)."""
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

    노이즈 봉은 **건드리지 않는다** — 거래량 노이즈는 저장이 정본에 더 가깝고(1분봉 쪽이
    모자란 것이라 그 값으로 덮으면 멀쩡한 봉을 미세하게 망친다), 가격 노이즈는 차이가 호가
    몇 칸이라 어느 쪽이 정본인지 이 함수가 알 수 없다(WAN-337 §2).

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
