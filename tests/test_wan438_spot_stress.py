"""WAN-438: 현물 적재 경로와 측정 배선을 동작으로 고정한다.

지키는 것:

1. **읽기** — 거래소 CSV(ms · µs · 헤더 유무)를 같은 스키마로 읽는다.
2. **검증** — 봉 수 · 구멍 · 중복 · 격자 밖 · 달 밖을 센다(구멍을 지어내지 않는다).
3. **받기** — 체크섬이 다르면 최종 이름을 붙이지 않는다 · 네트워크 오류만 다시 부른다.
4. **격리** — 루트가 운영 DB면 거부한다 · 적재는 (심볼, TF)별 하한과 공통 상한을 지킨다.
5. **옵트인** — `wan424.base_cell_kwargs`가 `build_base_payloads`의 인자 그 자체다(두 벌 금지) ·
   `wan436.build_entries`의 새 인자 기본값이 옛 동작이다.
6. **규칙 고정** — 이슈 §무엇을의 숫자가 그대로 박혀 있다.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import sqlite3
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import wan424_stoch_ob_arm as w424
from backtest import wan436_stoch_crowd_rules as w436
from backtest import wan438_spot_stress as m
from data import spot_klines as sk
from data.tick_probe import HttpResponse

MIN = 60_000
JAN_2018 = 1_514_764_800_000  # 2018-01-01 00:00 UTC (월요일)


def _zip(rows: list[list[object]], *, header: bool = False) -> bytes:
    text = io.StringIO()
    if header:
        text.write("open_time,open,high,low,close,volume,close_time\n")
    for r in rows:
        text.write(",".join(str(x) for x in r) + "\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X-1m-2018-01.csv", text.getvalue())
    return buf.getvalue()


def _row(t: int) -> list[object]:
    return [t, 1.0, 2.0, 0.5, 1.5, 10.0, t + MIN - 1]


# --- 1. 읽기 -----------------------------------------------------------------------------


def test_read_zip_ms_and_micros_are_the_same_frame() -> None:
    ms_frame = sk.read_zip_bytes(_zip([_row(JAN_2018), _row(JAN_2018 + MIN)]))
    us_frame = sk.read_zip_bytes(_zip([_row(JAN_2018 * 1000), _row((JAN_2018 + MIN) * 1000)]))
    assert list(ms_frame.columns) == ["open_time", "open", "high", "low", "close", "volume"]
    assert ms_frame["open_time"].tolist() == [JAN_2018, JAN_2018 + MIN]
    assert us_frame["open_time"].tolist() == ms_frame["open_time"].tolist()


def test_read_zip_with_header_row() -> None:
    frame = sk.read_zip_bytes(_zip([_row(JAN_2018)], header=True))
    assert frame["open_time"].tolist() == [JAN_2018]
    assert frame["close"].tolist() == [1.5]


# --- 2. 검증 -----------------------------------------------------------------------------


def test_check_month_counts_gaps_without_inventing_bars() -> None:
    lo, hi = sk.month_bounds("2018-01")
    assert (lo, hi) == (JAN_2018, JAN_2018 + 31 * 86_400_000)
    t = np.arange(lo, hi, MIN)
    t = np.delete(t, [10, 11, 12, 500])  # 구멍 두 개(3칸 · 1칸)
    frame = pd.DataFrame({"open_time": t})
    c = sk.check_month(frame, symbol="BTCUSDT", timeframe="1m", month="2018-01")
    assert c.expected == 31 * 1440 and c.bars == 31 * 1440 - 4
    assert c.gaps == 4 and c.longest_gap_bars == 3
    assert c.ok


def test_check_month_first_month_starts_at_first_bar_and_flags_bad_rows() -> None:
    lo, hi = sk.month_bounds("2018-01")
    start = lo + 10 * 86_400_000
    t = np.concatenate([np.arange(start, hi, MIN), [start, start + 7, hi + MIN]])
    c = sk.check_month(pd.DataFrame({"open_time": t}), symbol="X", timeframe="1m", month="2018-01")
    assert c.expected == 21 * 1440
    assert c.duplicates == 1 and c.off_grid == 1 and c.outside_month == 1
    assert not c.ok


def test_check_month_weekly_is_monday_anchored() -> None:
    mondays = np.array([JAN_2018, JAN_2018 + 7 * 86_400_000])
    ok = sk.check_month(
        pd.DataFrame({"open_time": mondays}), symbol="X", timeframe="1w", month="2018-01"
    )
    assert ok.ok and ok.expected == -1
    thursday = np.array([JAN_2018 + 3 * 86_400_000])
    bad = sk.check_month(
        pd.DataFrame({"open_time": thursday}), symbol="X", timeframe="1w", month="2018-01"
    )
    assert bad.off_grid == 1 and not bad.ok


def test_on_grid_drops_shifted_exchange_rows() -> None:
    """거래소 원본의 +20.8초 밀린 1분봉(2017-12 실측)은 격자 밖이라 걸러진다."""
    frame = pd.DataFrame({"open_time": [JAN_2018, JAN_2018 + MIN + 20_799, JAN_2018 + 2 * MIN]})
    assert sk.on_grid(frame, "1m").tolist() == [True, False, True]
    weekly = pd.DataFrame({"open_time": [JAN_2018, JAN_2018 + 3 * 86_400_000]})
    assert sk.on_grid(weekly, "1w").tolist() == [True, False]


# --- 3. 받기 -----------------------------------------------------------------------------


def test_list_months_parses_sizes_and_skips_checksums() -> None:
    xml = (
        "<ListBucketResult><IsTruncated>false</IsTruncated>"
        "<Contents><Key>data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2018-01.zip</Key>"
        "<LastModified>x</LastModified><Size>123</Size></Contents>"
        "<Contents><Key>data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2018-01.zip.CHECKSUM</Key>"
        "<LastModified>x</LastModified><Size>88</Size></Contents>"
        "</ListBucketResult>"
    )
    files = sk.list_months("BTCUSDT", "1m", transport=lambda _u: HttpResponse(200, xml.encode()))
    assert files == [sk.MonthFile("BTCUSDT", "1m", "2018-01", 123)]
    assert sk.select_months(files, "2018-02", "2020-08") == []


def test_retrying_repeats_network_errors_only() -> None:
    calls: list[int] = []

    def flaky(_url: str) -> HttpResponse:
        calls.append(1)
        return HttpResponse(0 if len(calls) < 3 else 200, b"ok")

    assert sk.retrying(flaky, pause_s=0.0)("u").status == 200 and len(calls) == 3
    calls.clear()
    assert sk.retrying(lambda _u: HttpResponse(404, b""), pause_s=0.0)("u").status == 404


def test_fetch_month_refuses_checksum_mismatch(tmp_path: Path) -> None:
    body = _zip([_row(JAN_2018)])
    f = sk.MonthFile("BTCUSDT", "1m", "2018-01", len(body))

    def transport(url: str) -> HttpResponse:
        if url.endswith(".CHECKSUM"):
            return HttpResponse(200, b"0" * 64 + b"  BTCUSDT-1m-2018-01.zip")
        return HttpResponse(200, body)

    bad = sk.fetch_month(f, cache_dir=tmp_path, transport=transport)
    assert not bad.ok and "체크섬" in bad.note
    assert not sk.cache_path(f, tmp_path).exists()
    digest = hashlib.sha256(body).hexdigest().encode()

    def good_transport(url: str) -> HttpResponse:
        return HttpResponse(200, digest + b"  x" if url.endswith(".CHECKSUM") else body)

    good = sk.fetch_month(f, cache_dir=tmp_path, transport=good_transport)
    assert good.ok and sk.cache_path(f, tmp_path).read_bytes() == body
    again = sk.fetch_month(f, cache_dir=tmp_path, transport=good_transport)
    assert again.cached


# --- 4. 격리 · 적재 -------------------------------------------------------------------------


def test_store_symbol_is_the_runner_spelling() -> None:
    assert sk.to_store_symbol("BTCUSDT") == "BTC/USDT:USDT"
    assert m.store_symbols()[0] == "BTC/USDT:USDT" and len(m.store_symbols()) == 21
    with pytest.raises(ValueError):
        sk.to_store_symbol("BTCBUSD")


def test_guard_root_refuses_the_production_db(tmp_path: Path) -> None:
    repo_db = tmp_path / "data" / "ohlcv.db"
    repo_db.parent.mkdir(parents=True)
    repo_db.write_bytes(b"")
    with pytest.raises(ValueError, match="운영 DB"):
        sk.guard_root(tmp_path, repo_db=repo_db)
    sk.guard_root(tmp_path / "elsewhere", repo_db=repo_db)


def test_load_into_root_respects_bounds(tmp_path: Path) -> None:
    root = tmp_path / "root"
    frame = sk.read_zip_bytes(_zip([_row(JAN_2018 + i * MIN) for i in range(10)]))
    counts = sk.load_into_root(
        root,
        [("BTC/USDT:USDT", "1m", frame), ("BTC/USDT:USDT", "1m", frame)],
        start_ms={("BTC/USDT:USDT", "1m"): JAN_2018 + 2 * MIN},
        end_ms=JAN_2018 + 8 * MIN,
    )
    assert counts == {("BTC/USDT:USDT", "1m"): 6}  # 두 번째 같은 봉은 무시(중복 0)
    conn = sqlite3.connect(sk.root_db_path(root))
    times = [r[0] for r in conn.execute("SELECT open_time FROM ohlcv ORDER BY open_time")]
    conn.close()
    assert times == [JAN_2018 + i * MIN for i in range(2, 8)]


def test_quarantine_drops_impossible_tick_and_repairs_htf(tmp_path: Path) -> None:
    """LINK 2020-03-12 실측형: 1분 저가 0.0001 → 1분봉은 지우고 상위TF 저가는 남은 1분봉으로."""
    root = tmp_path / "root"
    rows = [[JAN_2018 + i * MIN, 2.1, 2.2, 2.0, 2.15, 1.0] for i in range(60)]
    rows[30] = [JAN_2018 + 30 * MIN, 2.137, 2.4998, 0.0001, 2.2, 1.0]
    one_m = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    hour = pd.DataFrame(
        [[JAN_2018, 2.1, 2.4998, 0.0001, 2.15, 60.0]],
        columns=["open_time", "open", "high", "low", "close", "volume"],
    )
    sk.load_into_root(root, [("LINK/USDT:USDT", "1m", one_m), ("LINK/USDT:USDT", "1h", hour)])
    got = sk.quarantine_impossible_ticks(root)
    assert [(q.timeframe, q.action) for q in got] == [("1m", "삭제"), ("1h", "저가·고가 재계산")]
    assert got[1].new_low == 2.0 and got[1].new_high == 2.2
    conn = sqlite3.connect(sk.root_db_path(root))
    n = conn.execute("SELECT COUNT(*) FROM ohlcv WHERE timeframe='1m'").fetchone()[0]
    o, c = conn.execute("SELECT open, close FROM ohlcv WHERE timeframe='1h'").fetchone()
    conn.close()
    assert n == 59 and (o, c) == (2.1, 2.15)  # 시가·종가는 그대로


def test_quarantine_leaves_real_crash_wicks() -> None:
    """50% 꼬리(실제 폭락)는 거래소 필터 안이라 격리하지 않는다."""
    frame = pd.DataFrame({"open": [100.0], "close": [95.0], "low": [40.0], "high": [101.0]})
    assert not sk.impossible_mask(frame).any()
    frame.loc[0, "low"] = 18.0
    assert sk.impossible_mask(frame).all()


def test_working_root_restores_cwd(tmp_path: Path) -> None:
    import os

    before = os.getcwd()
    with m.working_root(tmp_path):
        assert Path(os.getcwd()).resolve() == tmp_path.resolve()
    assert os.getcwd() == before


# --- 5. 옵트인 ----------------------------------------------------------------------------


def test_base_payloads_read_the_shared_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build_base_payloads`가 넘기는 인자가 `base_cell_kwargs` 그대로다(현물 루트도 같은 팔)."""
    seen: dict[str, object] = {}

    def fake_run_cells(symbols: object, timeframes: object, **kwargs: object) -> list[object]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(w424, "run_cells", fake_run_cells)
    monkeypatch.setattr(w424, "apply_funding_proxy", lambda p: (p, None))
    w424.build_base_payloads(jobs=1, payload_dir=Path("x"))
    shared = w424.base_cell_kwargs()
    assert {k: seen[k] for k in shared} == shared
    assert shared["retap_mode"] == "once" and shared["reentry"] is False
    assert shared["no_same_step_tp"] is True
    assert w424.base_cell_kwargs("baseline")["fill"] != shared["fill"]


def test_build_entries_new_args_default_to_old_behaviour() -> None:
    sig = inspect.signature(w436.build_entries)
    assert sig.parameters["k_threshold"].default == w436.K_THRESHOLD == 25.0
    assert sig.parameters["start_ms"].default is None
    assert sig.parameters["end_ms"].default is None


# --- 6. 규칙 고정 ---------------------------------------------------------------------------


def test_constants_are_the_issue_spec() -> None:
    assert m.TIMEFRAMES == ("1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w")
    assert "3d" not in m.TIMEFRAMES
    assert m.K_ARMS == (25.0, 30.0) and m.PRIMARY_K == 25.0
    assert m.GATE == -0.025 and m.RISK == 0.02
    assert m.GATED.btc_gate == -0.025
    assert m.GATED.stop_multiple == 2.0 and m.GATED.skip_early and m.GATED.budget == 10.0
    assert w436.BASE.btc_gate is None and dict(m.RULE_ARMS)["게이트 없음"] is w436.BASE
    assert len(sk.SPOT_SYMBOLS) == 21 and "SOLUSDT" not in sk.SPOT_SYMBOLS
    assert m.OVERLAP_START == "2020-09-15"


def test_in_window_is_half_open() -> None:
    def trade(t: int) -> w436.PlacedTrade:
        empty = np.array([], dtype=np.int64)
        return w436.PlacedTrade(
            "X", "1h", t, t + 1, 0.0, False, False, 1.0, 1.0, 0.9, empty, empty, empty
        )

    a, b = m.ms("2020-02-01"), m.ms("2020-05-01")
    got = m.in_window([trade(a - 1), trade(a), trade(b - 1), trade(b)], "2020-02-01", "2020-05-01")
    assert [t.entry_time for t in got] == [a, b - 1]


def test_overlap_row_matches_exact_keys() -> None:
    empty = np.array([], dtype=np.int64)

    def trade(sym: str, t: int, r: float) -> w436.PlacedTrade:
        return w436.PlacedTrade(
            sym, "1h", t, t + 1, r, False, False, 1.0, 1.0, 0.9, empty, empty, empty
        )

    spot = [trade("A", 1, 1.0), trade("B", 2, -1.0)]
    fut = [trade("A", 1, 0.5), trade("C", 3, 1.0)]
    row = m.overlap_row(25.0, "x", spot, fut)
    assert (row.common, row.futures_trades) == (1, 2)
    assert row.common_share_of_futures == 0.5 and row.mean_abs_net_r_diff == 0.5
