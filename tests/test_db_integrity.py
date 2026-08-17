"""DB 무결성·위생 점검 테스트 (WAN-194 §2·§4·§5).

WAN-194의 판별 논리를 동작으로 고정한다: 복구 산출물 탐지, 빈 장부 판별(다른 테이블이
성한 채 장부만 비면 광범위 유실이 아니다), 처분 미기록 체결, 그리고 파괴적 정리가
**옵트인이며 VACUUM을 하지 않는다**는 것.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from data.integrity import (
    CUMULATIVE_LEDGER_TABLES,
    LEDGER_TABLES,
    RECOVERY_ARTIFACT_TABLES,
    STATE_LEDGER_TABLES,
    SalvageableRowsPresent,
    drop_recovery_artifacts,
    inspect,
    render_report,
    salvage_ohlcv,
)
from live.limit_orders import LimitFill, PendingLimitOrder
from live.order_journal import OrderJournal
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi


def _journal_db(path: Path) -> None:
    """장부 스키마를 만든다(OrderJournal이 소유한 스키마를 재구현하지 않는다)."""
    journal = OrderJournal(path)
    journal.close()


def _add_recovery_artifact(path: Path, rows: int = 3) -> None:
    """SQLite `.recover`가 남기는 `lost_and_found`를 흉내 낸다(스키마도 그쪽 모양)."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "CREATE TABLE lost_and_found ("
            " rootpgno INTEGER, pgno INTEGER, nfield INTEGER, id INTEGER, c0 ANY)"
        )
        conn.executemany(
            "INSERT INTO lost_and_found VALUES (?, ?, ?, ?, ?)",
            [(1, i, 5, i, "x") for i in range(rows)],
        )
    conn.close()


def _pending_order() -> PendingLimitOrder:
    return PendingLimitOrder(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=1_000,
    )


def _limit_fill() -> LimitFill:
    return LimitFill(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        price=100.0,
        time=61_000,
        rsi=25.0,
        stop_price=90.0,
        take_profit_price=115.0,
        penetration_bps=0.0,
        waited_ms=60_000,
    )


def _full_ledger_db(path: Path) -> None:
    """장부 4개(누적 3 + 상태 1)가 모두 존재하는 DB — 성격 구분을 재려면 표가 다 있어야 한다.

    스키마는 소유자(`OrderJournal`·`PaperTradeStore`)가 만든다 — 여기서 재구현하면 실제
    스키마와 갈라진 채 초록불이 난다.
    """
    _journal_db(path)
    store = PaperTradeStore(path)
    store.close()


def _ledger_rows(path: Path, *, record_disposition: bool = True) -> None:
    """누적 장부를 정상 상태로 채운다 — 체결 1건(처분 기록) + 청산된 거래 1건.

    `open_positions`는 **일부러 비운 채 둔다**(포지션이 닫힌 뒤의 정상 상태 = WAN-321이
    재는 바로 그 모양). 행은 스키마 소유자의 API로만 넣는다 — 손으로 쓴 INSERT는 스키마가
    움직이면 테스트만 조용히 다른 표를 재게 된다.
    """
    journal = OrderJournal(path)
    session = journal.start_session(now_ms=0)
    journal_id = journal.record_placed(
        _pending_order(), session_id=session, zone_start_time=0, zone_confirmed_time=0
    )
    journal.record_filled(journal_id, _limit_fill())
    if record_disposition:
        journal.record_entry_result(journal_id, entered=True)
    journal.close()

    store = PaperTradeStore(path)
    store.upsert_record(
        PaperTradeRecord(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=0,
            entry_price=100.0,
            exit_time=60_000,
            exit_price=115.0,
            reason=SignalExitReason.TAKE_PROFIT,
            gross_pct=15.0,
            fee_pct=0.04,
            funding_pct=0.0,
            net_pct=14.96,
        )
    )
    store.close()


# --- 장부의 성격 구분: 누적 vs 현재 상태 (WAN-321 §1) --------------------------


def test_empty_open_positions_is_healthy(tmp_path: Path) -> None:
    """완료기준 1 — `open_positions` 0행 + 처분 미기록 0이면 **종료 코드 0**이다.

    포지션은 닫히므로 「지금 열린 포지션이 없다」는 페이퍼 러너의 정상 상태다. 옛 규칙은
    이것을 매시간 경보로 냈고(싼 판 1시간 주기 × 24회/일), 그 상시 빨간불이 진짜 이상을
    가렸다(WAN-321 §1).
    """
    db = tmp_path / "ohlcv.db"
    _full_ledger_db(db)
    _ledger_rows(db)

    report = inspect(db)

    assert [t.name for t in report.empty_state_ledgers] == ["open_positions"]
    assert report.empty_cumulative_ledgers == []
    assert report.orphan_fills == []
    assert report.healthy, "열린 포지션이 없는 것은 정상이다 — 경보가 아니다"


def test_empty_cumulative_ledger_still_trips(tmp_path: Path) -> None:
    """완료기준 2 — 누적 장부가 통째로 비면 **여전히 종료 코드 1**이다.

    🚨 WAN-321이 지우려는 것은 거짓 경보 하나이지 WAN-194의 신호가 아니다. `paper_trades`가
    0인 것은 「매매 기록이 사라졌다」는 뜻이라 성격이 완전히 다르다.
    """
    db = tmp_path / "ohlcv.db"
    _full_ledger_db(db)
    journal = OrderJournal(db)
    journal.start_session(now_ms=0)
    journal.close()

    report = inspect(db)

    names = {t.name for t in report.empty_cumulative_ledgers}
    assert "paper_trades" in names
    assert "live_limit_orders" in names
    assert not report.healthy


def test_orphan_fill_still_trips_with_empty_open_positions(tmp_path: Path) -> None:
    """완료기준 2 — WAN-194의 **진짜 사고 모양**은 그대로 잡힌다.

    「체결은 `filled`인데 처분 NULL」 + `open_positions` 0행. 옛 규칙에서는 빈 장부가 먼저
    울어서 이 신호가 묻혔는데, 이제 `orphan_fills`가 **혼자서** 종료 코드 1을 낸다 — 즉
    WAN-194가 `entry_status` 열을 넣어 만든 정밀한 자가 판정의 주인이 된다.
    """
    db = tmp_path / "ohlcv.db"
    _full_ledger_db(db)
    _ledger_rows(db)
    conn = sqlite3.connect(db)
    with conn:  # 처분을 지운다 = 러너가 두 쓰기 사이에서 죽은 모양.
        conn.execute("UPDATE live_limit_orders SET entry_status = NULL")
    conn.close()

    report = inspect(db)

    assert report.empty_cumulative_ledgers == []  # 누적 장부는 성하다.
    assert [t.name for t in report.empty_state_ledgers] == ["open_positions"]
    assert len(report.orphan_fills) == 1
    assert not report.healthy, "처분 미기록 체결 하나만으로도 경보여야 한다"


def test_report_prints_empty_state_ledger_as_information(tmp_path: Path) -> None:
    """리포트에는 계속 찍되 **경고가 아니라 정보**로 적는다(WAN-321 §1).

    점검 항목을 줄인 게 아니라는 것을 사람이 읽는 자리에서도 확인한다.
    """
    db = tmp_path / "ohlcv.db"
    _full_ledger_db(db)
    _ledger_rows(db)

    text = render_report(inspect(db))

    assert "open_positions" in text
    assert "열린 포지션이 없" in text
    assert "⚠️ 빈 누적 장부" not in text


def test_ledger_tables_classification_is_total() -> None:
    """모든 장부가 두 성격 중 하나로 분류돼 있다 — 분류 없는 장부가 생기면 실패한다.

    `LEDGER_TABLES`를 파생값으로 둔 이유를 동작으로 고정한다(새 장부를 추가하면서 성격을
    안 고르면 종료 코드가 어느 쪽으로 갈지 아무도 모른다).
    """
    assert set(LEDGER_TABLES) == set(CUMULATIVE_LEDGER_TABLES) | set(STATE_LEDGER_TABLES)
    assert not set(CUMULATIVE_LEDGER_TABLES) & set(STATE_LEDGER_TABLES)


def test_inspect_missing_file_raises(tmp_path: Path) -> None:
    """없는 경로를 조용히 새 DB로 만들지 않는다 — 경로 오타가 초록불로 보이면 안 된다."""
    with pytest.raises(FileNotFoundError):
        inspect(tmp_path / "nope.db")


def test_inspect_clean_db_is_healthy(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)  # 장부가 비지 않도록 세션 1행.
    journal.close()
    assert session == 1

    # `open_positions`·`paper_trades`는 이 DB에 아예 없다(테이블 부재는 빈 장부가 아니다).
    report = inspect(db)
    assert report.quick_check == ["ok"]
    assert report.quick_check_ok
    assert report.recovery_artifacts == []
    assert report.orphan_fills == []
    assert [t.name for t in report.empty_cumulative_ledgers] == ["live_limit_orders"]
    assert not report.healthy  # 빈 누적 장부가 있으므로 경고 상태다.


def test_inspect_detects_recovery_artifact(tmp_path: Path) -> None:
    """`lost_and_found`의 존재 자체가 "이 DB는 복구됐다"는 증거로 잡힌다."""
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    _add_recovery_artifact(db, rows=7)

    report = inspect(db, quick_check=False)
    artifacts = report.recovery_artifacts
    assert [(a.name, a.rows) for a in artifacts] == [("lost_and_found", 7)]
    assert not report.healthy
    assert report.quick_check == []  # 건너뛰면 빈 리스트다.
    assert report.quick_check_ok  # 미확인은 실패로 세지 않는다(리포트가 그렇게 적는다).

    text = render_report(report)
    assert "복구 산출물" in text
    assert "7행" in text
    assert "quick_check 건너뜀" in text or "건너뜀" in text


def test_inspect_flags_orphan_fills_and_respects_since(tmp_path: Path) -> None:
    """처분 미기록 체결이 잡히고 `orphan_since_ms`로 도입 이전 기록을 가를 수 있다."""
    db = tmp_path / "ohlcv.db"
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    conn = journal._conn  # noqa: SLF001 — 임의 체결 시각을 심는 픽스처.
    with conn:
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
            " placed_ms, status, fill_ms, fill_price, stop_price)"
            " VALUES (?, 'LINK/USDT:USDT', '15m', 'bullish', 10, 'filled', 1000, 8.35, 8.33)",
            (session,),
        )
        conn.execute(
            "INSERT INTO live_limit_orders (session_id, symbol, timeframe, direction,"
            " placed_ms, status, fill_ms, fill_price, stop_price, entry_status)"
            " VALUES (?, 'BTC/USDT:USDT', '1h', 'bullish', 20, 'filled', 2000, 100.0, 90.0,"
            " 'entered')",
            (session,),
        )
    journal.close()

    report = inspect(db, quick_check=False)
    assert [o.symbol for o in report.orphan_fills] == ["LINK/USDT:USDT"]
    assert report.orphan_fills[0].fill_price == pytest.approx(8.35)

    # 창을 체결 이후로 자르면 판별 불가 행이 빠진다.
    later = inspect(db, quick_check=False, orphan_since_ms=1_500)
    assert later.orphan_fills == []

    text = render_report(report)
    assert "처분 미기록" in text
    assert "LINK/USDT:USDT" in text


def test_orphan_fills_tolerates_legacy_schema(tmp_path: Path) -> None:
    """`entry_status` 열이 없는 옛 DB에서도 점검이 죽지 않는다(조용히 빈 결과)."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "CREATE TABLE live_limit_orders (id INTEGER PRIMARY KEY, status TEXT, fill_ms INTEGER)"
        )
        conn.execute("INSERT INTO live_limit_orders (status, fill_ms) VALUES ('filled', 1)")
    conn.close()

    report = inspect(db, quick_check=False)
    assert report.orphan_fills == []


def test_drop_recovery_artifacts_is_opt_in_and_leaves_size(tmp_path: Path) -> None:
    """정리는 테이블만 지우고 **파일 크기를 줄이지 않는다**(VACUUM은 사람이 한다).

    자동 VACUUM은 DB를 독점 락하고 같은 크기의 임시 파일을 쓰므로 러너·수집기가 붙은
    서버에서 코드가 스스로 돌 일이 아니다 — 회수 가능 공간만 보고한다.
    """
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    _add_recovery_artifact(db, rows=500)
    before = db.stat().st_size

    dropped = drop_recovery_artifacts(db)
    assert dropped == ["lost_and_found"]
    assert db.stat().st_size == before  # 페이지가 프리리스트로 갔을 뿐이다.

    report = inspect(db, quick_check=False)
    assert report.recovery_artifacts == []
    assert report.space.freelist_count > 0
    assert report.space.reclaimable_bytes > 0

    # 두 번 불러도 안전하다(없으면 아무것도 안 한다).
    assert drop_recovery_artifacts(db) == []


def test_drop_recovery_artifacts_keeps_app_tables(tmp_path: Path) -> None:
    """앱 테이블은 절대 지우지 않는다 — 정리가 데이터 유실이 되면 안 된다."""
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    _add_recovery_artifact(db)
    journal = OrderJournal(db)
    journal.start_session(now_ms=0)
    journal.close()

    drop_recovery_artifacts(db)
    report = inspect(db, quick_check=False)
    names = {t.name for t in report.tables}
    assert "live_limit_orders" in names
    assert "live_runner_sessions" in names
    assert not names & RECOVERY_ARTIFACT_TABLES


def _candle_artifact_db(path: Path, *, live_5m: int = 0) -> None:
    """캔들 고아 행이 든 `lost_and_found` + 살아 있는 `ohlcv`를 만든다(WAN-195).

    실제 사고의 모양이다: `.recover`가 `ohlcv`의 5m 부분을 테이블에 못 붙이고 고아로
    쏟아부어, 본 테이블에는 5m이 0행인데 산출물에는 145만 행이 있었다.
    """
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ohlcv ("
            " symbol TEXT NOT NULL, timeframe TEXT NOT NULL, open_time INTEGER NOT NULL,"
            " open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,"
            " volume REAL NOT NULL, closed INTEGER NOT NULL DEFAULT 1,"
            " PRIMARY KEY (symbol, timeframe, open_time))"
        )
        conn.execute(
            "CREATE TABLE lost_and_found ("
            " rootpgno INTEGER, pgno INTEGER, nfield INTEGER, id INTEGER,"
            " c0, c1, c2, c3, c4, c5, c6, c7, c8)"
        )
        # 고아 5m 캔들 10개(본 테이블엔 없다) + 살아 있는 1m 1개(중복).
        conn.executemany(
            "INSERT INTO lost_and_found VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, i, 9, i, "BTC/USDT:USDT", "5m", 300_000 * i, 1.0, 2.0, 0.5, 1.5, 10.0, 1)
                for i in range(10)
            ]
            + [(1, 99, 9, 99, "BTC/USDT:USDT", "1m", 60_000, 1.0, 2.0, 0.5, 1.5, 10.0, 1)],
        )
        # 인덱스 조각(4필드) — 캔들로 오인하면 안 된다.
        conn.executemany(
            "INSERT INTO lost_and_found (rootpgno, pgno, nfield, id, c0, c1, c2, c3)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(2, i, 4, i, "BTC/USDT:USDT", "5m", 300_000 * i, i) for i in range(5)],
        )
        conn.execute("INSERT INTO ohlcv VALUES ('BTC/USDT:USDT','1m',60000,1.0,2.0,0.5,1.5,10.0,1)")
        for i in range(live_5m):
            conn.execute(
                "INSERT INTO ohlcv VALUES ('BTC/USDT:USDT','5m',?,9.0,9.0,9.0,9.0,9.0,1)",
                (300_000 * i,),
            )
    conn.close()


def test_census_separates_unique_candles_from_duplicates(tmp_path: Path) -> None:
    """산출물 안을 TF별로 갈라 「유일본」과 「본 테이블에 있음」을 구분한다."""
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db)

    report = inspect(db, quick_check=False)
    (census,) = report.artifact_census
    assert census.table == "lost_and_found"
    by_tf = {g.timeframe: g for g in census.candles}

    # 4필드 인덱스 조각은 캔들로 세지 않는다.
    assert by_tf["5m"].rows == 10
    assert by_tf["5m"].live_rows == 0
    assert by_tf["5m"].timeframe_is_lost  # 본 테이블에 없다 = 유일본
    assert by_tf["1m"].rows == 1
    assert not by_tf["1m"].timeframe_is_lost  # 본 테이블에 있다 = 버려도 된다

    assert [g.timeframe for g in report.salvageable_candles] == ["5m"]


def test_census_proves_trade_ledger_cannot_be_present(tmp_path: Path) -> None:
    """열 수로 「없다」가 아니라 「담길 수 없다」를 말한다(WAN-195 §4).

    `.recover`는 가장 넓은 고아 행에 맞춰 `c0..cN`을 만든다. 최대 9필드면 17열
    `paper_trades`는 구조적으로 들어갈 수 없다 — 훨씬 강한 진술이라 렌더에도 나온다.
    """
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db)

    (census,) = inspect(db, quick_check=False).artifact_census
    assert census.max_fields == 9
    assert not census.could_contain(17)  # paper_trades
    assert not census.could_contain(10)  # open_positions
    assert census.could_contain(9)  # ohlcv
    assert "있을 수 없다" in render_report(inspect(db, quick_check=False))


def test_drop_refuses_while_unique_candles_remain(tmp_path: Path) -> None:
    """유일본이 남아 있으면 드롭을 거부한다 — WAN-194 도구의 실제 위험이었다.

    행 수만 보면 "283만 행 쓰레기"라 통째로 버리게 되는데, 그 안에 본 테이블에 없는
    5m 145만 행이 들어 있었다. 드롭은 되돌릴 수 없으므로 기본을 거부로 둔다.
    """
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db)

    with pytest.raises(SalvageableRowsPresent, match="5m"):
        drop_recovery_artifacts(db)
    # 거부했으면 실제로 남아 있어야 한다(경고만 하고 지우면 최악이다).
    assert inspect(db, quick_check=False).recovery_artifacts != []

    # 복원한 뒤에는 통과한다.
    salvage_ohlcv(db)
    assert drop_recovery_artifacts(db) == ["lost_and_found"]


def test_drop_force_overrides_the_guard(tmp_path: Path) -> None:
    """버릴 것이 확실하면 명시적으로 버릴 수 있다(가드가 막다른 길이면 안 된다)."""
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db)
    assert drop_recovery_artifacts(db, force=True) == ["lost_and_found"]


def test_salvage_restores_lost_timeframe_only(tmp_path: Path) -> None:
    """인자 없이 부르면 **사라진 TF만** 되돌린다(중복 대량 삽입을 피한다)."""
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db)

    (result,) = salvage_ohlcv(db)
    assert result.timeframe == "5m"
    assert result.candidates == 10
    assert result.inserted == 10
    assert not result.dry_run

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM ohlcv WHERE timeframe = '5m'").fetchone()[0]
    conn.close()
    assert rows == 10


def test_salvage_never_overwrites_live_rows(tmp_path: Path) -> None:
    """충돌하면 살아 있는 쪽이 이긴다 — 거래소 봉이 복구 산출물로 바뀌면 더 나쁜 사고다."""
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db, live_5m=3)  # 앞 3개는 이미 본 테이블에 있다(값 9.0).

    (result,) = salvage_ohlcv(db, timeframes=("5m",))
    assert result.candidates == 10
    assert result.inserted == 7  # 겹치는 3개는 건너뛴다
    assert result.skipped == 3

    conn = sqlite3.connect(db)
    kept = conn.execute(
        "SELECT open FROM ohlcv WHERE timeframe = '5m' AND open_time = 0"
    ).fetchone()[0]
    conn.close()
    assert kept == 9.0  # 살아 있던 값이 그대로다(산출물의 1.0으로 덮이지 않았다)


def test_salvage_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db)

    (result,) = salvage_ohlcv(db, dry_run=True)
    assert result.candidates == 10
    assert result.inserted == 0
    assert result.dry_run

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM ohlcv WHERE timeframe = '5m'").fetchone()[0]
    conn.close()
    assert rows == 0


def test_salvage_rejects_unknown_timeframe(tmp_path: Path) -> None:
    """오타를 조용히 0건으로 넘기면 「복원했다」고 믿게 된다(WAN-91/95/112 부류)."""
    db = tmp_path / "ohlcv.db"
    _candle_artifact_db(db)
    with pytest.raises(ValueError, match="3m"):
        salvage_ohlcv(db, timeframes=("3m",))


def test_salvage_ignores_non_candle_rows(tmp_path: Path) -> None:
    """모양이 안 맞는 고아 행(인덱스 조각·쓰레기)은 `ohlcv`로 흘려보내지 않는다."""
    db = tmp_path / "ohlcv.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT NOT NULL, timeframe TEXT NOT NULL,"
            " open_time INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,"
            " low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,"
            " closed INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (symbol, timeframe, open_time))"
        )
        conn.execute(
            "CREATE TABLE lost_and_found (rootpgno INTEGER, pgno INTEGER, nfield INTEGER,"
            " id INTEGER, c0, c1, c2, c3, c4, c5, c6, c7, c8)"
        )
        # 9필드지만 타입이 캔들이 아니다(open_time 자리에 텍스트).
        conn.execute(
            "INSERT INTO lost_and_found VALUES (1,1,9,1,'BTC','5m','nope',1.0,2.0,0.5,1.5,1.0,1)"
        )
    conn.close()

    (census,) = inspect(db, quick_check=False).artifact_census
    assert census.candles == []
    assert salvage_ohlcv(db) == []


def test_census_survives_unexpected_artifact_shape(tmp_path: Path) -> None:
    """이름만 `lost_and_found`이고 모양이 다르면 조용히 넘어간다 — 죽으면 안 된다.

    `doctor`는 **망가진 DB에서** 도는 도구다. 예상 밖 스키마 하나에 점검 전체가
    `OperationalError`로 죽으면 정작 필요할 때 아무것도 못 본다.
    """
    db = tmp_path / "ohlcv.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("CREATE TABLE lost_and_found (rootpgno INTEGER, pgno INTEGER)")
    conn.close()

    report = inspect(db, quick_check=False)
    (census,) = report.artifact_census
    assert census.candles == []
    assert census.max_fields == 0
    assert report.salvageable_candles == []
    assert salvage_ohlcv(db) == []
    # 복원할 유일본이 없으므로 드롭도 막히지 않는다.
    assert drop_recovery_artifacts(db) == ["lost_and_found"]


def test_space_report_reads_wal_and_disk(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    _journal_db(db)
    report = inspect(db, quick_check=False)
    space = report.space
    assert space.page_size > 0
    assert space.page_count > 0
    assert space.journal_mode == "wal"  # configure_connection이 WAL을 건다.
    assert space.disk_total_bytes > 0
    assert 0.0 < space.disk_free_fraction <= 1.0
    assert "공간·저널" in render_report(report)
