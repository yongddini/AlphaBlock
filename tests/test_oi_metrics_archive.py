"""바이낸스 metrics(미결제약정 5분 스냅샷) 아카이브 접근 테스트 (WAN-417).

네트워크를 타지 않는다 — 전송 계층을 주입한다(WAN-347/348 선례). 고정하는 것은 **동작**이다:

* 옛 파일의 **두 번 적힌 행**을 접되, 같은 시각에 **값이 다르면** 센다(조용히 하나를 고르지 않는다).
* 간격은 실측 상수(300초)이고 파일이 어긋나면 **거부**한다 — 「5분이라고 들었다」가 아니다.
* 파일 안 종목이 이름과 다르면 거부한다(엉뚱한 파일 → 판정 전체 무효, WAN-348 (c)).
* 조회는 **정확히 그 시각**만 — 없으면 NaN(보간 없음).
* 끊긴 파일이 정상 파일과 같은 이름으로 남지 않는다 · 캐시 적중은 다시 받지 않는다.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest

from data.oi_metrics_archive import (
    SNAPSHOT_INTERVAL_MS,
    build_series,
    day_range,
    fetch_day,
    list_available_days,
    listing_url,
    metrics_url,
    parse_create_time,
    parse_listing_page,
    read_day,
    read_day_text,
)
from data.tick_probe import HttpResponse

_HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio"
)


def _row(ts: str, coin: float, usdt: float, symbol: str = "BTCUSDT") -> str:
    return f"{ts},{symbol},{coin:.8f},{usdt:.8f},1,1,1,1"


def _day_text(rows: list[str]) -> str:
    return "\n".join([_HEADER, *rows]) + "\n"


def _zip_bytes(text: str, name: str = "BTCUSDT-metrics-2020-10-01.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def test_urls_use_bare_symbol() -> None:
    assert metrics_url("BTC/USDT:USDT", "2020-10-01").endswith(
        "/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-10-01.zip"
    )
    assert "prefix=data/futures/um/daily/metrics/ETHUSDT/" in listing_url("ETH/USDT:USDT")
    assert listing_url("ETHUSDT", "abc").endswith("&marker=abc")


def test_parse_create_time_is_utc() -> None:
    assert parse_create_time("2020-10-01 00:05:00") == 1_601_510_700_000


def test_duplicated_rows_are_folded_and_conflicts_are_counted() -> None:
    text = _day_text(
        [
            _row("2020-10-01 00:00:00", 10.0, 100.0),
            _row("2020-10-01 00:00:00", 10.0, 100.0),  # 옛 파일의 두 번 적힌 행
            _row("2020-10-01 00:05:00", 11.0, 110.0),
            _row("2020-10-01 00:05:00", 11.5, 110.0),  # 같은 시각인데 값이 다르다
        ]
    )
    rows = read_day_text(text, symbol="BTC/USDT:USDT", day="2020-10-01")
    assert rows.snapshots == 2
    assert rows.raw_rows == 4
    assert rows.duplicate_rows == 2
    assert rows.conflicting_rows == 1
    assert rows.min_gap_ms == SNAPSHOT_INTERVAL_MS
    # 첫 값이 남는다 — 값이 다른 것은 세기만 하고 고르지 않는다는 뜻을 인구조사가 실어 준다.
    assert rows.oi_coin.tolist() == [10.0, 11.0]
    assert rows.oi_usdt.tolist() == [100.0, 110.0]


def test_missing_snapshot_is_a_hole_not_a_cadence_change() -> None:
    # 실측 BTC 2021-02-18: `00:00 → 00:10`(00:05가 빠짐) — 첫 두 행 차로 간격을 재면 오판한다.
    rows = read_day_text(
        _day_text(
            [
                _row("2020-10-01 00:00:00", 1, 1),
                _row("2020-10-01 00:10:00", 2, 2),
                _row("2020-10-01 00:15:00", 3, 3),
            ]
        ),
        symbol="BTCUSDT",
        day="2020-10-01",
    )
    assert rows.snapshots == 3 and rows.min_gap_ms == SNAPSHOT_INTERVAL_MS
    series = build_series("BTCUSDT", [rows])
    assert series.census["gaps_longer"] == 1 and series.census["gaps_regular"] == 1


def test_offgrid_jitter_is_dropped_and_counted_not_snapped() -> None:
    rows = read_day_text(
        _day_text(
            [
                _row("2020-10-01 00:00:00", 1, 1),
                _row("2020-10-01 00:05:00", 2, 2),
                _row("2020-10-01 00:10:01", 3, 3),  # 몇 초 흔들림 — 격자로 끌어오지 않는다
                _row("2020-10-01 00:15:00", 4, 4),
            ]
        ),
        symbol="BTCUSDT",
        day="2020-10-01",
    )
    assert rows.offgrid_rows == 1
    assert rows.oi_coin.tolist() == [1.0, 2.0, 4.0]
    t0 = parse_create_time("2020-10-01 00:00:00")
    assert rows.times_ms.tolist() == [t0, t0 + SNAPSHOT_INTERVAL_MS, t0 + 3 * SNAPSHOT_INTERVAL_MS]


def test_next_midnight_spill_row_belongs_to_the_next_file() -> None:
    # 실측 SOL 2024-04-02 파일 끝에 `2024-04-03 00:00:00` 행 — 다음 날 파일과 값이 달랐다.
    rows = read_day_text(
        _day_text(
            [
                _row("2020-10-01 23:55:00", 1, 1),
                _row("2020-10-02 00:00:00", 9, 9),
                _row("2020-10-02 00:00:01", 9, 9),
            ]
        ),
        symbol="BTCUSDT",
        day="2020-10-01",
    )
    assert rows.spill_rows == 2 and rows.snapshots == 1 and rows.oi_coin.tolist() == [1.0]


def test_unexpected_interval_symbol_or_day_is_rejected() -> None:
    with pytest.raises(ValueError, match="간격"):
        read_day_text(  # 10분 격자뿐인 파일 = 간격 자체가 다르다
            _day_text(
                [
                    _row("2020-10-01 00:00:00", 1, 1),
                    _row("2020-10-01 00:10:00", 1, 1),
                    _row("2020-10-01 00:20:00", 1, 1),
                ]
            ),
            symbol="BTCUSDT",
            day="2020-10-01",
        )
    with pytest.raises(ValueError, match="엉뚱한 파일"):
        read_day_text(
            _day_text([_row("2020-10-01 00:00:00", 1, 1, symbol="ETHUSDT")]),
            symbol="BTCUSDT",
            day="2020-10-01",
        )
    with pytest.raises(ValueError, match="파일 밖 날짜"):
        read_day_text(
            _day_text([_row("2020-10-02 00:05:00", 1, 1)]), symbol="BTCUSDT", day="2020-10-01"
        )
    with pytest.raises(ValueError, match="기대한 열"):
        read_day_text("a,b,c\n1,2,3\n", symbol="BTCUSDT", day="2020-10-01")


def test_series_lookup_is_exact_and_never_interpolates() -> None:
    day1 = read_day_text(
        _day_text([_row("2020-10-01 00:00:00", 1, 10), _row("2020-10-01 00:05:00", 2, 20)]),
        symbol="BTCUSDT",
        day="2020-10-01",
    )
    day2 = read_day_text(
        _day_text([_row("2020-10-02 00:00:00", 3, 30), _row("2020-10-02 00:05:00", 4, 40)]),
        symbol="BTCUSDT",
        day="2020-10-02",
    )
    series = build_series("BTCUSDT", [day2, day1])  # 순서를 섞어 줘도 시각순으로 잇는다.
    t0 = parse_create_time("2020-10-01 00:00:00")
    values = series.values_at(
        np.array([t0, t0 + SNAPSHOT_INTERVAL_MS, t0 + 60_000, t0 + 86_400_000], dtype=np.int64),
        "sum_open_interest",
    )
    assert values[0] == 1.0 and values[1] == 2.0 and values[3] == 3.0
    assert np.isnan(values[2])  # 00:01은 스냅샷이 아니다 — 보간하지 않는다.
    assert series.index_at_or_before(t0 + 60_000) == 0
    assert series.index_at_or_before(t0 - 1) == -1
    assert series.census["duplicate_rows"] == 0 and series.census["files_short"] == 2


def test_listing_pagination_follows_marker_and_skips_checksums() -> None:
    page1 = (
        "<ListBucketResult><IsTruncated>true</IsTruncated>"
        "<Key>data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-09-01.zip</Key>"
        "<Key>data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-09-01.zip.CHECKSUM</Key>"
        "<NextMarker>data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-09-01.zip.CHECKSUM"
        "</NextMarker></ListBucketResult>"
    )
    page2 = (
        "<ListBucketResult><IsTruncated>false</IsTruncated>"
        "<Key>data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2020-09-02.zip</Key>"
        "</ListBucketResult>"
    )
    days, marker = parse_listing_page(page1, "BTCUSDT")
    assert days == ["2020-09-01"] and marker is not None
    calls: list[str] = []

    def transport(url: str) -> HttpResponse:
        calls.append(url)
        return HttpResponse(status=200, body=(page2 if "marker=" in url else page1).encode())

    assert list_available_days("BTCUSDT", transport=transport) == ["2020-09-01", "2020-09-02"]
    assert len(calls) == 2 and "marker=" in calls[1]
    with pytest.raises(RuntimeError):
        list_available_days("BTCUSDT", transport=lambda _u: HttpResponse(status=403, body=b""))


def test_fetch_day_verifies_zip_and_serves_cache(tmp_path: Path) -> None:
    body = _zip_bytes(_day_text([_row("2020-10-01 00:00:00", 1, 10)]))
    calls: list[str] = []

    def transport(url: str) -> HttpResponse:
        calls.append(url)
        return HttpResponse(status=200, body=body)

    first = fetch_day("BTC/USDT:USDT", "2020-10-01", cache_dir=tmp_path, transport=transport)
    assert first.ok and not first.cached and first.path is not None
    second = fetch_day("BTCUSDT", "2020-10-01", cache_dir=tmp_path, transport=transport)
    assert second.cached and second.seconds == 0.0 and len(calls) == 1
    rows = read_day(first.path, symbol="BTCUSDT", day="2020-10-01")
    assert rows.snapshots == 1
    # 손상된 응답은 최종 이름으로 남지 않는다.
    broken = fetch_day(
        "ETHUSDT",
        "2020-10-01",
        cache_dir=tmp_path,
        transport=lambda _u: HttpResponse(status=200, body=b"not-a-zip"),
    )
    assert not broken.ok and not (tmp_path / "ETHUSDT-metrics-2020-10-01.zip").exists()
    assert not list(tmp_path.glob("*.part"))
    missing = fetch_day(
        "ETHUSDT",
        "2020-10-02",
        cache_dir=tmp_path,
        transport=lambda _u: HttpResponse(status=404, body=b""),
    )
    assert missing.status == 404 and not missing.ok


def test_day_range_is_inclusive_utc() -> None:
    assert day_range("2020-12-30", "2021-01-02") == [
        "2020-12-30",
        "2020-12-31",
        "2021-01-01",
        "2021-01-02",
    ]


def test_zero_open_interest_is_a_publishing_defect_not_a_value() -> None:
    # 실측: 12종목이 같은 달(2022-03 종목마다 117행)에 `0E-16` — 값으로 쓰면 −100% 점.
    rows = read_day_text(
        _day_text(
            [
                _row("2020-10-01 00:00:00", 5, 50),
                "2020-10-01 00:05:00,BTCUSDT,0E-16,0E-16,1,1,1,1",
                _row("2020-10-01 00:10:00", 0.0, 60),  # 한 단위만 0이어도 스냅샷 전체를 뺀다
                _row("2020-10-01 00:15:00", 7, 70),
                _row("2020-10-01 00:20:00", 8, 80),
            ]
        ),
        symbol="BTCUSDT",
        day="2020-10-01",
    )
    assert rows.zero_rows == 2
    assert rows.oi_coin.tolist() == [5.0, 7.0, 8.0]
    assert rows.oi_usdt.tolist() == [50.0, 70.0, 80.0]
    series = build_series("BTCUSDT", [rows])
    assert series.census["zero_rows"] == 2 and series.census["gaps_longer"] == 1
