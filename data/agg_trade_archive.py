"""일자별 체결내역(aggTrades) 아카이브에서 **문제의 1분만** 꺼내 오는 자 (WAN-348).

## 왜 이 모듈이 있나

WAN-336이 잰 것: 채택 북 `oos_warm` 순손익의 **약 48%**가 「진입한 그 1분 안에서 익절」한
거래(467건)에 실려 있다. 그게 성립하려면 그 1분 안에서 **저가가 먼저 · 고가가 나중**이어야
하는데 **1분봉은 안의 순서를 모른다** — 엔진은 그걸 확인하지 않고 **가정**한다. 체결내역에는
그 순서가 그대로 들어 있으므로, **그 1분치만** 펼치면 가정이 아니라 사실이 나온다.

## 경로가 하나뿐인 것은 WAN-347 §0이 이미 실측했다

선물 `aggTrades` REST는 **최근 2일**만 준다(`-4166`). 6년 전 거래는 REST로 **불가능**하고
일자별 아카이브(`data.binance.vision`)가 **유일한 길**이다. 그 판정을 여기서 다시 주장하지
않고 `data.tick_probe`의 주소 조립·파싱을 **그대로 재사용**한다 — 두 모듈이 각자 CSV 형식을
알면 헤더 유무·시각 단위 함정(WAN-347 §형식-함정)을 두 곳에서 따로 틀린다.

## 이 모듈이 하지 않는 것

* ❌ **상시 수집·DB 적재.** 받은 zip은 **캐시 파일**로만 두고(기본 `data/cache/` 아래,
  gitignore) 프로덕션 DB는 열지도 않는다(WAN-194 원칙).
* ❌ **호가창(depth).** 「우리 주문이 채워졌을까」(큐 우선순위)는 체결내역이 답하지 못한다 —
  별개 축이고 WAN-98은 Canceled다.
* ❌ **전수 받기.** 하루 파일이 종목에 따라 수 MB~수십 MB라(WAN-347 §0 실측) 6년 × 12종목은
  이 모듈의 용도가 아니다. 부르는 쪽이 **표본**을 정한다.

## ⚠️ 캐시는 「같은 이름이면 같은 내용」에 기댄다

아카이브는 확정된 과거 하루라 내용이 바뀌지 않는다. 그래서 파일이 있으면 다시 받지 않고,
**받다가 끊긴 파일이 정상 파일과 같은 이름으로 남지 않게** 임시 이름으로 받아 zip이 열리는
것까지 확인한 뒤에 최종 이름을 붙인다(WAN-318 §백업 스크립트와 같은 규약).
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from data.tick_probe import (
    HttpResponse,
    ProbeSpec,
    iter_agg_trade_rows,
    urllib_transport,
    vision_url,
)

logger = logging.getLogger(__name__)

#: 캐시 기본 위치 — `data/cache/`는 이미 gitignore다(1분봉 parquet 캐시와 같은 자리).
DEFAULT_CACHE_DIR = Path("data/cache/wan348-aggtrades")

Transport = Callable[[str], HttpResponse]


def archive_symbol(symbol: str) -> str:
    """ccxt 표기(`ETH/USDT:USDT`)를 아카이브 표기(`ETHUSDT`)로 바꾼다.

    DB·리포트는 ccxt 표기를 쓰고 거래소 아카이브는 맨 이름을 쓴다 — 그 사이를 문자열
    조작으로 매번 하면 한 곳에서 틀린다. 이미 맨 이름이면 그대로 돌려준다.
    """
    return symbol.split(":", 1)[0].replace("/", "")


def day_of(epoch_ms: int) -> str:
    """UTC 기준 `YYYY-MM-DD`. 아카이브가 UTC 일 단위라 시간대를 섞으면 파일이 어긋난다."""
    return datetime.fromtimestamp(epoch_ms / 1000.0, tz=UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class Tick:
    """체결 하나 — 시각(ms) · 가격 · 수량."""

    time_ms: int
    price: float
    qty: float


@dataclass(frozen=True, slots=True)
class DayFetch:
    """하루 파일 하나를 확보한 결과 (§1 비용 실측의 원자료)."""

    symbol: str
    """아카이브 표기(맨 이름)."""
    day: str
    url: str
    status: int
    """HTTP 상태. 캐시 적중이면 200으로 적는다(이미 받아 둔 것이 곧 200이었다)."""
    path: Path | None
    """확보한 zip 경로. 실패면 None."""
    size_bytes: int
    seconds: float
    """받는 데 걸린 시간. **캐시 적중이면 0.0**이라 비용 표에서 빼고 읽어야 한다."""
    cached: bool
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None


def fetch_day(
    symbol: str,
    day: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    transport: Transport = urllib_transport,
) -> DayFetch:
    """그 종목·그 하루의 체결내역 zip을 확보한다(있으면 캐시, 없으면 받는다)."""
    bare = archive_symbol(symbol)
    spec = ProbeSpec(symbol=bare, market="future", source="agg_trades")
    url = vision_url(spec, day)
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_dir / f"{bare}-aggTrades-{day}.zip"
    if final.exists():
        return DayFetch(bare, day, url, 200, final, final.stat().st_size, 0.0, True)

    started = time.monotonic()
    response = transport(url)
    elapsed = time.monotonic() - started
    if response.status != 200:
        note = f"HTTP {response.status}"
        return DayFetch(bare, day, url, response.status, None, 0, elapsed, False, note)

    # 받다가 끊긴 파일이 **정상 파일과 같은 이름으로** 남지 않게 임시 이름으로 받아
    # zip이 실제로 열리는 것까지 확인한 뒤 최종 이름을 붙인다(WAN-318 규약).
    tmp = final.with_suffix(".zip.part")
    tmp.write_bytes(response.body)
    try:
        with zipfile.ZipFile(tmp) as archive:
            if not archive.namelist():
                raise zipfile.BadZipFile("빈 zip")
    except zipfile.BadZipFile as exc:
        tmp.unlink(missing_ok=True)
        return DayFetch(bare, day, url, response.status, None, 0, elapsed, False, f"손상: {exc}")
    tmp.replace(final)
    return DayFetch(bare, day, url, 200, final, len(response.body), elapsed, False)


def iter_ticks(path: Path, start_ms: int, end_ms: int) -> Iterator[Tick]:
    """`[start_ms, end_ms)` 구간의 체결을 **파일에 적힌 순서 그대로** 낸다.

    ⚠️ 정렬하지 않는다 — 이 모듈의 존재 이유가 **순서**이고, 아카이브는 체결 묶음 id
    오름차순(= 시간순)으로 적혀 있다. 여기서 다시 정렬하면 같은 밀리초 안의 순서가 바뀌어
    「무엇이 먼저였나」가 정렬 알고리즘의 산물이 된다.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names:  # pragma: no cover - fetch_day가 이미 막는다
            return
        with archive.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8")
            for _agg_id, time_ms, price, qty in iter_agg_trade_rows(text):
                if time_ms >= end_ms:
                    break  # 시간순이라 더 볼 것이 없다.
                if time_ms >= start_ms:
                    yield Tick(time_ms=time_ms, price=price, qty=qty)


def minute_ticks(path: Path, minute_ms: int) -> list[Tick]:
    """그 1분(`[분, 분+60초)`)의 체결 목록."""
    return list(iter_ticks(path, minute_ms, minute_ms + 60_000))


def minutes_ticks(path: Path, minutes: Iterable[int]) -> dict[int, list[Tick]]:
    """여러 분을 **파일 한 번 훑기로** 꺼낸다 — 결과는 `minute_ticks`와 같다.

    `minute_ticks`는 부를 때마다 zip을 처음부터 다시 읽으므로, 하루에서 여러 분을 보는
    실행(WAN-359 모집단 전수: 파일 257개에 거래 467건)에서는 같은 파일을 몇 번씩 판다.
    이 함수는 필요한 분들의 **마지막 분까지** 한 번만 훑는다.

    ⚠️ **여기서도 정렬하지 않는다** — 이 자료의 존재 이유가 순서다(`iter_ticks` 참고).
    요청한 분에 체결이 없으면 빈 목록이 담긴다(키는 언제나 전부 있다 — 「없음」과
    「안 물어봄」이 호출부에서 구분돼야 한다).
    """
    wanted = sorted(set(minutes))
    out: dict[int, list[Tick]] = {minute: [] for minute in wanted}
    if not wanted:
        return out
    buckets = {minute: out[minute] for minute in wanted}
    for tick in iter_ticks(path, wanted[0], wanted[-1] + 60_000):
        bucket = buckets.get(tick.time_ms - tick.time_ms % 60_000)
        if bucket is not None:
            bucket.append(tick)
    return out
