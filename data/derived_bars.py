"""수집기가 만들어 저장하는 TF — 지금은 3h 하나 (WAN-451 ③).

3h는 바이낸스 원천 kline이 없다. 백테스트 정본은 1분봉에서 접어 저장했는데(`wan440.aggregate_3h`),
라이브는 **1h에서** 접는다. 둘은 같은 값이다 — 로컬 DB 31종목 3h 549,269봉에서 1h로 다시 접은
값과 시가·고가·저가·종가·거래량이 **전부 같았다**(불일치 0, 2026-10-15). 1h로 접으면 1분봉 룩백
(3일)에 묶이지 않고 1h 이력(신규 종목도 룩백만큼)으로 과거 3h를 바로 채울 수 있다.

규약은 `data.aggregate`와 같다: 구성 봉이 **빠짐없이 확정**된 버킷만 만들고(`resample_ohlcv`),
이미 저장된 봉은 건드리지 않는다(`insert_candles_ignore`). 저장된 마지막 3h 다음부터만 본다.
3h는 하루를 나누므로 epoch 정렬(`floor(t / 3h)`)이 날짜 경계와 맞는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from data.models import Candle, timeframe_to_ms
from data.resample import resample_ohlcv
from data.storage import OhlcvStore
from data.universe import BUILT_TIMEFRAMES

logger = logging.getLogger(__name__)


def build_derived(
    store: OhlcvStore, symbol: str, target: str, *, backfill_older: bool = False
) -> int:
    """`target`(예 3h)을 원천 TF에서 접어 저장된 마지막 봉 다음부터 채운다. 넣은 봉 수.

    `backfill_older`면 원천 이력이 저장된 첫 봉보다 앞으로 늘어난 구간도 채운다(이력 확장 뒤 ·
    매 봉 훅에서는 끈다 — 원천 첫 버킷이 불완전하면 매번 전 이력을 다시 읽게 된다).
    """
    source = BUILT_TIMEFRAMES[target]
    tgt_ms = timeframe_to_ms(target)
    first_src = store.first_open_time(symbol, source)
    if first_src is None:
        return 0
    lowest = -(-first_src // tgt_ms) * tgt_ms  # 첫 원천 봉 이후의 첫 온전한 버킷
    first = store.first_open_time(symbol, target)
    last = store.last_open_time(symbol, target)
    inserted = 0
    if backfill_older and first is not None and lowest < first:
        # 원천 이력이 뒤로 늘었다(팔 종목 이력 확장, WAN-451) — 그 앞 구간도 채운다.
        inserted += _build_range(store, symbol, target, source, lowest, first)
    start = lowest if last is None else last + tgt_ms
    return inserted + _build_range(store, symbol, target, source, start, None)


def _build_range(
    store: OhlcvStore, symbol: str, target: str, source: str, start: int, end: int | None
) -> int:
    """`[start, end)`(end=None이면 끝까지) 구간의 온전한 버킷을 접어 넣는다."""
    src = store.load(symbol, source, start_ms=start)
    if end is not None and not src.empty:
        src = src[src["open_time"].astype("int64") < end]
    if src.empty:
        return 0
    if "closed" in src.columns:
        src = src[src["closed"].astype(bool)]
    out = resample_ohlcv(src, source, target)
    if out.empty:
        return 0
    out = out[out["open_time"].astype("int64") >= start]
    if end is not None:
        out = out[out["open_time"].astype("int64") < end]
    inserted = store.insert_candles_ignore(
        Candle(
            symbol=symbol,
            timeframe=target,
            open_time=int(r.open_time),
            open=float(r.open),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=float(r.volume),
        )
        for r in out.itertuples(index=False)
    )
    if inserted:
        logger.debug("%s %s: %d봉 생성(원천 %s)", symbol, target, inserted, source)
    return inserted


def build_all_derived(
    store: OhlcvStore, symbols: list[str], *, progress: Callable[[], None] | None = None
) -> int:
    """여러 종목의 생성 TF 전부를 채운다(실패한 종목은 로그만 남기고 계속).

    `progress`는 종목 하나를 시작할 때마다 부른다(수집기 워치독 진행 표시 ·
    `ArmCollection.catch_up`).
    """
    total = 0
    for sym in symbols:
        if progress is not None:
            progress()
        for target in BUILT_TIMEFRAMES:
            try:
                total += build_derived(store, sym, target, backfill_older=True)
            except Exception:  # noqa: BLE001 — 한 종목이 수집기를 멈추면 안 된다
                logger.exception("%s %s 생성 실패(다음 기회에 다시 시도)", sym, target)
    return total


def source_closes_bucket(timeframe: str, open_time: int) -> list[str]:
    """이 원천 봉이 닫히며 버킷을 완성하는 생성 TF 목록(스트림 훅용)."""
    out: list[str] = []
    end = open_time + timeframe_to_ms(timeframe)
    for target, source in BUILT_TIMEFRAMES.items():
        if source == timeframe and end % timeframe_to_ms(target) == 0:
            out.append(target)
    return out
