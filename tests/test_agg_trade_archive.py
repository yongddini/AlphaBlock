"""일자별 체결내역 아카이브 접근 테스트 (WAN-348).

네트워크를 타지 않는다 — 전송 계층을 주입해 **실제 아카이브와 같은 모양**의 zip을 돌려준다
(WAN-347 `tests/test_tick_probe.py` 선례). 여기서 고정하는 것은 라벨이 아니라 **동작**이다:

* **순서를 다시 정렬하지 않는다** — 이 모듈의 존재 이유가 「무엇이 먼저였나」라서, 정렬하면
  답이 정렬 알고리즘의 산물이 된다.
* **끊긴 파일이 정상 파일과 같은 이름으로 남지 않는다**(WAN-318 §백업 규약).
* 캐시 적중은 다시 받지 않는다.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from data.agg_trade_archive import (
    DayFetch,
    archive_symbol,
    day_of,
    fetch_day,
    iter_ticks,
    minute_ticks,
)
from data.tick_probe import HttpResponse

_MINUTE = 1_724_424_480_000  # 2024-08-23 14:48 UTC


def _zip_bytes(rows: list[tuple[int, int, float, float]]) -> bytes:
    """선물 `aggTrades` 아카이브와 같은 모양(헤더 있음 · transact_time은 ms)."""
    header = "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
    lines = [header]
    lines += [f"{aid},{price},{qty},{aid},{aid},{ts},false" for aid, ts, price, qty in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-aggTrades-2024-08-23.csv", "\n".join(lines))
    return buffer.getvalue()


def test_archive_symbol_maps_ccxt_and_bare_names() -> None:
    assert archive_symbol("ETH/USDT:USDT") == "ETHUSDT"
    assert archive_symbol("BTCUSDT") == "BTCUSDT"


def test_day_of_is_utc() -> None:
    assert day_of(_MINUTE) == "2024-08-23"


def test_fetch_day_downloads_then_serves_from_cache(tmp_path: Path) -> None:
    body = _zip_bytes([(1, _MINUTE, 100.0, 1.0)])
    calls: list[str] = []

    def transport(url: str) -> HttpResponse:
        calls.append(url)
        return HttpResponse(status=200, body=body)

    first = fetch_day("BTC/USDT:USDT", "2024-08-23", cache_dir=tmp_path, transport=transport)
    assert first.ok and not first.cached and first.size_bytes == len(body)
    second = fetch_day("BTC/USDT:USDT", "2024-08-23", cache_dir=tmp_path, transport=transport)
    assert second.cached and second.path == first.path
    assert len(calls) == 1, "캐시 적중인데 다시 받았다"


def test_fetch_day_reports_missing_file_instead_of_writing_one(tmp_path: Path) -> None:
    fetch = fetch_day(
        "BTC/USDT:USDT",
        "1999-01-01",
        cache_dir=tmp_path,
        transport=lambda _url: HttpResponse(status=404, body=b""),
    )
    assert not fetch.ok and fetch.status == 404
    assert list(tmp_path.glob("*.zip")) == []


def test_truncated_download_does_not_land_under_the_real_name(tmp_path: Path) -> None:
    """끊긴 파일이 정상 파일과 **같은 이름으로** 남으면 캐시가 조용히 거짓말을 한다."""
    fetch = fetch_day(
        "BTC/USDT:USDT",
        "2024-08-23",
        cache_dir=tmp_path,
        transport=lambda _url: HttpResponse(status=200, body=b"not-a-zip"),
    )
    assert not fetch.ok and "손상" in fetch.note
    assert list(tmp_path.glob("*.zip")) == []
    assert list(tmp_path.glob("*.part")) == []


def test_minute_ticks_keeps_file_order_even_when_prices_zigzag(tmp_path: Path) -> None:
    """정렬하면 「무엇이 먼저였나」가 사라진다 — 값이 아니라 **순서**를 고정한다."""
    rows = [
        (1, _MINUTE + 0, 105.0, 1.0),
        (2, _MINUTE + 10, 95.0, 1.0),
        (3, _MINUTE + 20, 101.0, 1.0),
    ]
    path = tmp_path / "a.zip"
    path.write_bytes(_zip_bytes(rows))
    assert [tick.price for tick in minute_ticks(path, _MINUTE)] == [105.0, 95.0, 101.0]


def test_iter_ticks_window_is_half_open(tmp_path: Path) -> None:
    rows = [
        (1, _MINUTE - 1, 1.0, 1.0),
        (2, _MINUTE, 2.0, 1.0),
        (3, _MINUTE + 59_999, 3.0, 1.0),
        (4, _MINUTE + 60_000, 4.0, 1.0),
    ]
    path = tmp_path / "a.zip"
    path.write_bytes(_zip_bytes(rows))
    assert [tick.price for tick in iter_ticks(path, _MINUTE, _MINUTE + 60_000)] == [2.0, 3.0]


def test_day_fetch_ok_is_about_the_file_not_the_status() -> None:
    assert not DayFetch("BTCUSDT", "d", "u", 200, None, 0, 0.0, False).ok
