"""엔진 C 대조 도구(WAN-451) — 정상이면 전부 일치 · 일봉이 고쳐지면 잡는다 · 공백은 실패 아님."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from cli.main import main as cli_main
from config.settings import Settings
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live.executor import PaperExecutor
from live.tsmom_parity import check_tsmom_parity, render_tsmom_parity
from live.tsmom_runner import TsmomRebalancer
from paper.store import PaperTradeRecorder, PaperTradeStore
from paper.tsmom_ledger import TsmomLedgerStore
from strategy import tsmom

DAY = tsmom.DAY_MS
N_DAYS = 320
T0 = 1_650_000_000_000 // DAY * DAY
BTC, ETH = tsmom.ASSETS


def _seed(store: OhlcvStore) -> None:
    rng = np.random.default_rng(7)
    for asset, base in ((BTC, 30_000.0), (ETH, 2_000.0)):
        closes = base * np.cumprod(1.0 + rng.normal(0.001, 0.03, N_DAYS + 5))
        store.upsert_candles(
            Candle(asset, "1d", T0 + i * DAY, float(c), float(c), float(c), float(c), 1.0)
            for i, c in enumerate(closes)
        )


def _after_close(day: int) -> int:
    return T0 + (day + 1) * DAY + 60_000


def _run_days(db: Path, days: list[int]) -> TsmomLedgerStore:
    store = OhlcvStore(db)
    _seed(store)
    settings = Settings(db_path=str(db))
    paper = PaperTradeStore(db)
    ledger = TsmomLedgerStore(db)
    wallet = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper,
        recorder=PaperTradeRecorder(paper, cost_model=settings.costs, funding_store=None),
        sizing=settings.risk_sizing,
        tsmom_ledger=ledger,
    )
    reb = TsmomRebalancer(store=store, ledger=ledger, wallet=wallet)
    for d in days:
        reb.tick(_after_close(d))
    return ledger


def test_daily_rebalances_match_recomputation(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    ledger = _run_days(db, list(range(N_DAYS - 5, N_DAYS)))
    rows = ledger.rows()
    assert len(rows) == 10  # 5일 × 2자산
    with OhlcvStore(db) as store:
        report = check_tsmom_parity(store, rows, now_ms=_after_close(N_DAYS - 1))
    assert report.ok, render_tsmom_parity(report)
    assert report.skipped_days == {}
    assert all(c.weight_ok and c.price_ok and c.notional_ok for c in report.checks)
    assert "일치 10개 · 불일치 0개" in render_tsmom_parity(report)


def test_downtime_days_are_listed_but_not_failures(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    ledger = _run_days(db, [N_DAYS - 5, N_DAYS - 1])  # 사이 사흘은 러너가 꺼져 있었다
    with OhlcvStore(db) as store:
        report = check_tsmom_parity(store, ledger.rows(), now_ms=_after_close(N_DAYS - 1))
    assert report.ok
    assert report.skipped_days == {
        a: [T0 + d * DAY for d in (N_DAYS - 4, N_DAYS - 3, N_DAYS - 2)] for a in (BTC, ETH)
    }
    assert "건너뛴 날" in render_tsmom_parity(report)


def test_a_daily_close_fixed_afterwards_is_caught(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    ledger = _run_days(db, [N_DAYS - 2, N_DAYS - 1])
    day = T0 + (N_DAYS - 1) * DAY
    with OhlcvStore(db) as store:
        store.upsert_candles([Candle(BTC, "1d", day, 1.0, 1.0, 1.0, 1.0, 1.0)])  # 백필 복구 흉내
        report = check_tsmom_parity(store, ledger.rows(), now_ms=_after_close(N_DAYS - 1))
    bad = [c for c in report.mismatches]
    assert [(c.asset, c.as_of_ms) for c in bad] == [(BTC, day)]
    assert not bad[0].price_ok
    assert "🔴 불일치" in render_tsmom_parity(report)


def test_cli_exit_code_follows_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "d.db"
    _run_days(db, [N_DAYS - 1])
    assert cli_main(["tsmom-parity", "--db", str(db)]) == 0
    assert "엔진 C 페이퍼 ↔ 다시 계산 대조" in capsys.readouterr().out
    conn = sqlite3.connect(db)
    conn.execute("UPDATE tsmom_rebalances SET weight = weight + 0.01 WHERE asset = ?", (ETH,))
    conn.commit()
    conn.close()
    assert cli_main(["tsmom-parity", "--db", str(db)]) == 1


def test_empty_ledger_says_so(tmp_path: Path) -> None:
    db = tmp_path / "e.db"
    OhlcvStore(db).close()
    with OhlcvStore(db) as store:
        report = check_tsmom_parity(store, [], now_ms=0)
    assert report.ok and "행이 없습니다" in render_tsmom_parity(report)
