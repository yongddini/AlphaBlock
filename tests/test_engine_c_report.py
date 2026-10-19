"""엔진 C 표시(WAN-451 ⑩) — 읽기 전용 조회 · 다이제스트 줄 · 대시보드 블록."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config.settings import Settings, get_settings
from paper.digest import build_digest
from paper.engine_c_report import (
    engine_c_digest_lines,
    holdings_frame,
    mark_stats,
    read_engine_c,
    rebalance_frame,
)
from paper.performance import build_performance
from paper.tsmom_ledger import EquityMark, Holding, TsmomLedgerStore, plan_rebalance
from scripts import paper_digest
from strategy import tsmom

DAY = tsmom.DAY_MS
T0 = 1_700_000_000_000 // DAY * DAY
BTC, ETH = tsmom.ASSETS


def _target(asset: str, weight: float, as_of: int) -> tsmom.TsmomTarget:
    return tsmom.TsmomTarget(asset=asset, as_of_ms=as_of, signal=0.5, annual_vol=0.5, weight=weight)


def _seed_ledger(db: Path) -> float:
    """BTC 이틀(롱 → 숏) · ETH 하루. 지갑 기여 합을 돌려준다."""
    total = 0.0
    with TsmomLedgerStore(db) as ledger:
        a = plan_rebalance(_target(BTC, 1.0, T0), price=100.0, equity_before=10_000.0, holding=None)
        ledger.append(a)
        b = plan_rebalance(
            _target(BTC, -0.5, T0 + DAY),
            price=110.0,
            equity_before=10_000.0 + a.net_pnl,
            holding=Holding(BTC, a.new_qty, 100.0, T0),
        )
        ledger.append(b)
        c = plan_rebalance(
            _target(ETH, 0.5, T0 + DAY), price=50.0, equity_before=10_000.0, holding=None
        )
        ledger.append(c)
        ledger.record_mark(EquityMark(T0 + DAY, 10_000.0, 0.0, 0))
        ledger.record_mark(EquityMark(T0 + 2 * DAY, 9_000.0, 0.0, 0))
        ledger.record_mark(EquityMark(T0 + 3 * DAY, 9_900.0, 0.0, 0))
        total = a.net_pnl + b.net_pnl + c.net_pnl
    return total


def test_missing_db_or_tables_reads_empty_and_creates_nothing(tmp_path: Path) -> None:
    assert read_engine_c(tmp_path / "nope.db").empty
    db = tmp_path / "plain.db"
    sqlite3.connect(db).execute("CREATE TABLE ohlcv (x INTEGER)").connection.close()
    assert read_engine_c(db).empty
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master")}
    assert names == {"ohlcv"}  # 조회가 테이블을 만들지 않았다


def test_read_period_filter_holdings_and_all_time_total(tmp_path: Path) -> None:
    db = tmp_path / "e.db"
    total = _seed_ledger(db)
    view = read_engine_c(db, since_ms=T0 + 2 * DAY)  # 둘째 날 리밸런싱(거래 시각 T0+2d)만
    assert [(r.asset, r.as_of_ms) for r in view.rows] == [(BTC, T0 + DAY), (ETH, T0 + DAY)]
    assert {h.asset: h.qty < 0 for h in view.holdings} == {BTC: True, ETH: False}
    assert view.total_net_pnl == pytest.approx(total)  # 기간과 무관한 전 기간 합
    assert [m.mark_ms for m in view.marks] == [T0 + 2 * DAY, T0 + 3 * DAY]


def test_mark_stats_change_and_drawdown() -> None:
    marks = [
        EquityMark(1, 10_000.0, 0.0, 0),
        EquityMark(2, 9_000.0, 0.0, 0),
        EquityMark(3, 9_900.0, 0.0, 0),
    ]
    stats = mark_stats(marks)
    assert stats is not None
    assert stats.change_pct == pytest.approx(-1.0)
    assert stats.max_drawdown_pct == pytest.approx(10.0)
    assert mark_stats([]) is None


def test_digest_lines_and_frames(tmp_path: Path) -> None:
    db = tmp_path / "e.db"
    _seed_ledger(db)
    view = read_engine_c(db)
    lines = engine_c_digest_lines(view)
    assert lines[0] == "*추세추종 (BTC·ETH)*"
    assert "BTC 숏 -0.50배" in lines[1] and "ETH 롱 +0.50배" in lines[1]
    assert lines[2].startswith("리밸런싱 2일")
    assert "낙폭 최대 `10.00%`" in lines[3]
    assert "_" not in "\n".join(lines)  # 텔레그램 레거시 Markdown이 깨지지 않게(WAN-321)
    assert list(holdings_frame(view)["자산"]) == ["BTC", "ETH"]
    assert len(rebalance_frame(view)) == 3
    assert engine_c_digest_lines(read_engine_c(tmp_path / "none.db")) == []


def test_digest_shows_engine_c_when_arm_has_no_trades() -> None:
    perf = build_performance([])
    text = build_digest(perf, period_label="p", engine_c_lines=["*추세추종 (BTC·ETH)*", "x"])
    assert "팔: 기간 내 청산 거래 없음" in text and "*추세추종 (BTC·ETH)*" in text
    assert "기간 내 청산된 페이퍼 거래가 없습니다." not in text
    assert "기간 내 청산된 페이퍼 거래가 없습니다." in build_digest(perf, period_label="p")


def test_generate_digest_includes_engine_c(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    _seed_ledger(db)
    text = paper_digest.generate_digest(
        Settings(db_path=str(db)), since_ms=None, until_ms=None, symbols=None, timeframes=None
    )
    assert "*추세추종 (BTC·ETH)*" in text and "보유:" in text


@pytest.fixture
def engine_c_only_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    db = tmp_path / "ohlcv.db"
    _seed_ledger(db)
    monkeypatch.setenv("ALPHABLOCK_DB_PATH", str(db))
    get_settings.cache_clear()
    yield str(db)
    get_settings.cache_clear()


def test_dashboard_balance_tab_shows_engine_c_without_arm_trades(engine_c_only_db: str) -> None:
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    assert "추세추종 (BTC·ETH)" in [s.value for s in at.subheader]
    labels = {m.label for m in at.metric}
    assert {"지갑 잔고(팔 + 추세추종)", "추세추종 누적 손익($)"} <= labels
