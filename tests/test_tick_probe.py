"""1분보다 잘게 보는 데이터 하루치 실측 도구 테스트 (WAN-347 §0).

네트워크를 타지 않는다 — `HttpTransport`를 주입해 **실제 아카이브와 같은 모양**의 zip을
돌려준다(`common.telegram` 선례). 여기서 고정하는 것은 라벨이 아니라 **동작**이다:

* 시각 단위 정규화(현물 1초봉은 마이크로초다) — 안 하면 2026년이 서기 58,000년이 되는데
  행 수도 크기도 멀쩡해 보인다.
* 헤더 유무(선물 체결내역엔 있고 현물 1초봉엔 없다) — 헤더를 세면 행이 하나 는다.
* **행이 조용히 접히지 않는다** — 같은 시각·가격·수량의 체결이 실제로 있어서, 묶음 id가
  키에서 빠지면 서로 다른 체결이 하나로 접힌다.
* **없는 칸이 표에서 사라지지 않는다** — 선물에는 1초봉이 없는데, 조용히 빼면 다음 사람이
  「왜 열이 비었지」를 다시 조사한다.
* **프로덕션 DB를 건드리지 않는다**(WAN-194 원칙).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from data.tick_probe import (
    DEFAULT_PROBE_SYMBOLS,
    SCRATCH_DIRNAME,
    HttpResponse,
    ProbeResult,
    ProbeSpec,
    day_start_ms,
    days_until_full,
    default_specs,
    iter_agg_trade_rows,
    iter_kline_rows,
    measured_required_kinds,
    normalize_epoch_ms,
    probe_all,
    probe_day,
    probe_rest_availability,
    project,
    rest_probe_url,
    unavailable,
    vision_url,
)

DAY = "2026-08-19"
#: 실제 아카이브 형태 그대로 — 헤더가 **있고** `transact_time`이 **밀리초**다.
AGG_CSV = (
    "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
    "250216866,0.33258,196.0,701161250,701161251,1787097600036,true\n"
    "250216867,0.33259,569.0,701161252,701161252,1787097600344,false\n"
)
#: 실제 아카이브 형태 그대로 — 헤더가 **없고** `open_time`이 **마이크로초**다.
KLINE_CSV = (
    "1787097600000000,0.33280000,0.33280000,0.33280000,0.33280000,276.0,"
    "1787097600999999,91.85,3,0.0,0.0,0\n"
    "1787097601000000,0.33280000,0.33290000,0.33270000,0.33280000,0.0,"
    "1787097601999999,0.0,0,0.0,0.0,0\n"
)


def _zip_bytes(name: str, body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, body)
    return buf.getvalue()


def _transport(payloads: dict[str, bytes | int]) -> Callable[[str], HttpResponse]:
    """URL → 본문(bytes) 또는 상태코드(int, 4xx 흉내). 없는 URL은 404."""

    def _call(url: str) -> HttpResponse:
        found = payloads.get(url)
        if found is None:
            return HttpResponse(status=404, body=b"not found")
        if isinstance(found, int):
            return HttpResponse(status=found, body=b'{"code":-1120,"msg":"Invalid interval."}')
        return HttpResponse(status=200, body=found)

    return _call


# ---------------------------------------------------------------------------
# 형식 함정
# ---------------------------------------------------------------------------


def test_microsecond_timestamps_are_normalised_to_ms() -> None:
    micros = 1_787_097_600_000_000
    assert normalize_epoch_ms(micros) == 1_787_097_600_000
    # 이미 ms인 값은 건드리지 않는다(선물 체결내역).
    assert normalize_epoch_ms(1_787_097_600_036) == 1_787_097_600_036


def test_kline_rows_land_in_the_right_century() -> None:
    """정규화를 빼면 2026년이 서기 58,000년이 되는데 행 수·크기는 멀쩡해 보인다."""
    rows = list(iter_kline_rows(KLINE_CSV.splitlines()))
    assert [r[0] for r in rows] == [1_787_097_600_000, 1_787_097_601_000]
    # 2026-08-19 근처(±1일)인지 — 세기가 틀리면 여기서 죽는다.
    assert abs(rows[0][0] - day_start_ms(DAY)) < 86_400_000


def test_header_row_is_skipped_only_where_it_exists() -> None:
    assert len(list(iter_agg_trade_rows(AGG_CSV.splitlines()))) == 2  # 헤더 1줄 제외
    assert len(list(iter_kline_rows(KLINE_CSV.splitlines()))) == 2  # 헤더 없음


def test_agg_rows_carry_id_time_price_qty() -> None:
    first = next(iter_agg_trade_rows(AGG_CSV.splitlines()))
    assert first == (250216866, 1_787_097_600_036, 0.33258, 196.0)


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------


def test_vision_url_splits_futures_and_spot_roots() -> None:
    fut = vision_url(ProbeSpec("TRXUSDT", "future", "agg_trades"), DAY)
    spot = vision_url(ProbeSpec("TRXUSDT", "spot", "klines_1s"), DAY)
    assert "/data/futures/um/daily/aggTrades/TRXUSDT/" in fut
    assert "/data/spot/daily/klines/TRXUSDT/1s/" in spot


def test_rest_probe_url_uses_each_market_host() -> None:
    fut = rest_probe_url(ProbeSpec("TRXUSDT", "future", "agg_trades"), day_start_ms(DAY))
    spot = rest_probe_url(ProbeSpec("TRXUSDT", "spot", "klines_1s"), day_start_ms(DAY))
    assert fut.startswith("https://fapi.binance.com/fapi/v1/aggTrades")
    assert spot.startswith("https://api.binance.com/api/v3/klines")
    assert "interval=1s" in spot


# ---------------------------------------------------------------------------
# 격자 — 없는 칸을 지우지 않는다
# ---------------------------------------------------------------------------


def test_default_specs_keep_the_impossible_futures_1s_cell() -> None:
    """선물 1초봉은 존재하지 않지만 **표에 남아야** 한다(없음도 실측 결과다)."""
    specs = default_specs(["TRXUSDT"])
    assert ProbeSpec("TRXUSDT", "future", "klines_1s") in specs
    assert len(specs) == 3


def test_missing_archive_becomes_a_row_not_a_gap(tmp_path: Path) -> None:
    specs = default_specs(["TRXUSDT"])
    payloads: dict[str, bytes | int] = {
        vision_url(specs[0], DAY): _zip_bytes("agg.csv", AGG_CSV),
        vision_url(specs[2], DAY): _zip_bytes("kline.csv", KLINE_CSV),
        # specs[1](선물 1초봉)은 일부러 없다 → 404
    }
    results = probe_all(
        specs, DAY, scratch_dir=tmp_path / "scratch", transport=_transport(payloads), keep=True
    )
    assert len(results) == 3  # 조용히 빠진 칸이 없다
    missing = [r for r in results if not r.available]
    assert len(missing) == 1
    assert missing[0].spec.source == "klines_1s"
    assert missing[0].spec.market == "future"
    assert "404" in missing[0].note


# ---------------------------------------------------------------------------
# 적재 — 행이 조용히 접히지 않는다
# ---------------------------------------------------------------------------


def test_same_time_price_qty_trades_are_not_folded(tmp_path: Path) -> None:
    """같은 시각·가격·수량의 체결 둘 — 묶음 id가 키에 없으면 하나로 접힌다."""
    csv = (
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
        "1,0.5,10.0,1,1,1787097600000,true\n"
        "2,0.5,10.0,2,2,1787097600000,false\n"
    )
    spec = ProbeSpec("TRXUSDT", "future", "agg_trades")
    res = probe_day(
        spec,
        DAY,
        scratch_dir=tmp_path,
        transport=_transport({vision_url(spec, DAY): _zip_bytes("a.csv", csv)}),
    )
    assert res.available
    assert res.rows == 2
    assert res.sqlite_bytes > 0


def test_duplicate_agg_id_fails_loudly(tmp_path: Path) -> None:
    """같은 묶음 id가 두 번 오면 **죽는다** — 조용히 하나로 접지 않는다."""
    csv = (
        "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
        "1,0.5,10.0,1,1,1787097600000,true\n"
        "1,0.6,11.0,2,2,1787097600500,false\n"
    )
    spec = ProbeSpec("TRXUSDT", "future", "agg_trades")
    with pytest.raises(Exception):  # noqa: B017 - sqlite3.IntegrityError 계열이면 무엇이든
        probe_day(
            spec,
            DAY,
            scratch_dir=tmp_path,
            transport=_transport({vision_url(spec, DAY): _zip_bytes("a.csv", csv)}),
        )


def test_row_count_mismatch_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """저장 행 수가 파싱 행 수와 다르면 표가 거짓이므로 죽는다(둘째 그물)."""
    import data.tick_probe as tp

    monkeypatch.setattr(tp, "_count_sql", lambda source: "SELECT 0")
    spec = ProbeSpec("TRXUSDT", "future", "agg_trades")
    with pytest.raises(RuntimeError, match="키가 행을 접었다"):
        probe_day(
            spec,
            DAY,
            scratch_dir=tmp_path,
            transport=_transport({vision_url(spec, DAY): _zip_bytes("a.csv", AGG_CSV)}),
        )


def test_measures_all_four_sizes(tmp_path: Path) -> None:
    spec = ProbeSpec("TRXUSDT", "spot", "klines_1s")
    res = probe_day(
        spec,
        DAY,
        scratch_dir=tmp_path,
        transport=_transport({vision_url(spec, DAY): _zip_bytes("k.csv", KLINE_CSV)}),
    )
    assert res.rows == 2
    assert res.download_bytes > 0  # zip = 압축 후 디스크
    assert res.raw_bytes == len(KLINE_CSV)  # 푼 CSV
    assert res.gzip_bytes > 0  # 최소 열만 남긴 보관본
    assert res.sqlite_bytes > 0  # DB에 넣었을 때
    assert res.elapsed_s == pytest.approx(res.download_s + res.ingest_s)


def test_no_sqlite_skips_db_cost_only(tmp_path: Path) -> None:
    spec = ProbeSpec("TRXUSDT", "spot", "klines_1s")
    res = probe_day(
        spec,
        DAY,
        scratch_dir=tmp_path,
        transport=_transport({vision_url(spec, DAY): _zip_bytes("k.csv", KLINE_CSV)}),
        with_sqlite=False,
    )
    assert res.rows == 2
    assert res.gzip_bytes > 0
    assert res.sqlite_bytes == 0


# ---------------------------------------------------------------------------
# 읽기 전용 · 뒷정리
# ---------------------------------------------------------------------------


def test_production_db_is_never_touched(tmp_path: Path) -> None:
    """WAN-194 원칙 — 이 도구는 기존 DB를 열지도 않는다."""
    prod = tmp_path / "ohlcv.db"
    prod.write_bytes(b"pretend-production-db")
    before = (prod.stat().st_size, prod.stat().st_mtime_ns)
    spec = ProbeSpec("TRXUSDT", "future", "agg_trades")
    probe_day(
        spec,
        DAY,
        scratch_dir=tmp_path / "scratch",
        transport=_transport({vision_url(spec, DAY): _zip_bytes("a.csv", AGG_CSV)}),
    )
    assert (prod.stat().st_size, prod.stat().st_mtime_ns) == before


def test_scratch_is_removed_unless_kept(tmp_path: Path) -> None:
    specs = [ProbeSpec("TRXUSDT", "future", "agg_trades")]
    payloads: dict[str, bytes | int] = {vision_url(specs[0], DAY): _zip_bytes("a.csv", AGG_CSV)}

    gone = tmp_path / "gone"
    probe_all(specs, DAY, scratch_dir=gone, transport=_transport(payloads), keep=False)
    assert not (gone / SCRATCH_DIRNAME).exists()

    kept = tmp_path / "kept"
    probe_all(specs, DAY, scratch_dir=kept, transport=_transport(payloads), keep=True)
    work = kept / SCRATCH_DIRNAME
    assert work.exists() and any(work.iterdir())


def test_scratch_never_deletes_what_it_did_not_create(tmp_path: Path) -> None:
    """🚨 `--scratch`에 공용 경로를 주면 남의 파일을 날리는 자리 — 울타리를 동작으로 건다."""
    shared = tmp_path / "shared"
    shared.mkdir()
    bystander = shared / "someone-elses.txt"
    bystander.write_text("건드리면 안 된다", encoding="utf-8")

    specs = [ProbeSpec("TRXUSDT", "future", "agg_trades")]
    payloads: dict[str, bytes | int] = {vision_url(specs[0], DAY): _zip_bytes("a.csv", AGG_CSV)}
    probe_all(specs, DAY, scratch_dir=shared, transport=_transport(payloads), keep=False)

    assert shared.exists()
    assert bystander.read_text(encoding="utf-8") == "건드리면 안 된다"


def test_files_do_not_pile_up_across_cells(tmp_path: Path) -> None:
    """칸마다 바로 지운다 — 12종목을 끝까지 쌓으면 1GB 박스에서 GB 단위로 붇는다."""
    specs = default_specs(["BTCUSDT", "SOLUSDT", "TRXUSDT"])
    payloads: dict[str, bytes | int] = {
        vision_url(spec, DAY): _zip_bytes("a.csv", AGG_CSV)
        for spec in specs
        if spec.source == "agg_trades"
    }

    def _run(root: Path, *, keep: bool) -> int:
        """다음 칸을 받기 **직전** 스크래치에 몇 개가 남아 있는지의 최댓값."""
        seen: list[int] = []
        inner = _transport(payloads)

        def _watching(url: str) -> HttpResponse:
            work = root / SCRATCH_DIRNAME
            seen.append(len(list(work.iterdir())) if work.exists() else 0)
            return inner(url)

        probe_all(specs, DAY, scratch_dir=root, transport=_watching, keep=keep)
        return max(seen)

    # `keep=True`면 쌓이는 것이 정상 — 그 판이 이 테스트의 대조군이다(0이면 무의미해진다).
    assert _run(tmp_path / "kept", keep=True) > 0
    # 지우는 판에서는 한 칸이 끝날 때마다 비어 있다.
    assert _run(tmp_path / "swept", keep=False) == 0


# ---------------------------------------------------------------------------
# REST 가용성 — 거래소 응답을 그대로 적는다
# ---------------------------------------------------------------------------


def test_rest_availability_records_the_exchange_message() -> None:
    spec = ProbeSpec("TRXUSDT", "future", "klines_1s")

    def _call(url: str) -> HttpResponse:
        return HttpResponse(status=400, body=b'{"code":-1120,"msg":"Invalid interval."}')

    rows = probe_rest_availability([spec], DAY, transport=_call)
    assert len(rows) == 1
    assert not rows[0].ok
    assert "-1120" in rows[0].message
    assert "Invalid interval" in rows[0].message


def test_rest_availability_marks_ok() -> None:
    spec = ProbeSpec("TRXUSDT", "spot", "klines_1s")
    rows = probe_rest_availability([spec], DAY, transport=lambda url: HttpResponse(200, b"[]"))
    assert rows[0].ok and rows[0].message == "ok"


# ---------------------------------------------------------------------------
# 환산 · 판정 게이트
# ---------------------------------------------------------------------------


def _result(symbol: str, market: str, source: str, sqlite_bytes: int) -> ProbeResult:
    spec = ProbeSpec(symbol, market, source)  # type: ignore[arg-type]
    return ProbeResult(
        spec=spec,
        day=DAY,
        available=True,
        note="",
        rows=1,
        download_bytes=10,
        raw_bytes=20,
        gzip_bytes=5,
        sqlite_bytes=sqlite_bytes,
        download_s=0.1,
        ingest_s=0.2,
    )


def test_projection_scales_by_universe_and_keeps_a_band() -> None:
    results = [
        _result("BTCUSDT", "future", "agg_trades", 1_000),
        _result("TRXUSDT", "future", "agg_trades", 100),
    ]
    (proj,) = project(results, universe=12)
    assert proj.measured_symbols == 2
    assert proj.projected_daily_sqlite == pytest.approx(550 * 12)
    # 띠는 추정의 폭 — 체결내역은 종목별로 자릿수가 갈린다.
    assert proj.projected_daily_low == pytest.approx(100 * 12)
    assert proj.projected_daily_high == pytest.approx(1_000 * 12)
    assert proj.projected_yearly_sqlite == pytest.approx(550 * 12 * 365)


def test_projection_ignores_unavailable_cells() -> None:
    results = [
        _result("BTCUSDT", "future", "agg_trades", 1_000),
        unavailable(ProbeSpec("BTCUSDT", "future", "klines_1s"), DAY, "404"),
    ]
    projections = project(results, universe=12)
    assert [(p.market, p.source) for p in projections] == [("future", "agg_trades")]


def test_days_until_full_is_none_when_nothing_grows() -> None:
    assert days_until_full(1_000, 0.0) is None
    assert days_until_full(1_000, 100.0) == pytest.approx(10.0)


def test_required_kinds_gate_needs_both_data_kinds() -> None:
    agg = _result("BTCUSDT", "future", "agg_trades", 1)
    kline = _result("BTCUSDT", "spot", "klines_1s", 1)
    assert not measured_required_kinds([agg])
    assert not measured_required_kinds([kline])
    assert measured_required_kinds([agg, kline])


def test_default_probe_symbols_are_active_mid_quiet() -> None:
    """이슈가 지정한 대표 3종목 — 바꾸면 표의 뜻(활발·중간·한산)이 바뀐다."""
    assert DEFAULT_PROBE_SYMBOLS == ("BTCUSDT", "SOLUSDT", "TRXUSDT")
