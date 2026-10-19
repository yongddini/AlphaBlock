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
from live.tsmom_parity import (
    RowCheck,
    TsmomParityReport,
    check_tsmom_parity,
    render_tsmom_parity,
)
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
    assert "일치 10개 · 거의 일치 0개 · 불일치 0개" in render_tsmom_parity(report)


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
    assert "추세추종 페이퍼 ↔ 다시 계산 대조" in capsys.readouterr().out
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


# -- WAN-459 §3: 판정 세 단계 · 배율 ✓/✗ · 차이 크기 ------------------------------


def _nudge_old_eth_close(db: Path, factor: float) -> None:
    """원장 기준일보다 앞선(변동성 창 안) ETH 일봉 하나를 고친다 — 백필 복구 흉내."""
    from tests import test_wan457_wallet_summary as ws

    day = ws.T0 + (ws.N_DAYS - 10) * DAY
    with OhlcvStore(db) as store:
        old = store.load(ETH, "1d", start_ms=day, end_ms=day + 1)
        c = float(old["close"].iloc[-1]) * factor
        store.upsert_candles([Candle(ETH, "1d", day, c, c, c, c, 1.0)])


def _rig_db(tmp_path: Path) -> Path:
    from tests import test_wan457_wallet_summary as ws

    rig = ws._Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    weights = [r.weight for r in rig.ledger.rows() if r.asset == ETH]
    assert weights and all(w != 0.0 for w in weights)  # 끝자리를 볼 배율이 있다
    return rig.db


def _report(db: Path) -> TsmomParityReport:
    from paper.engine_c_report import read_engine_c

    with OhlcvStore(db) as store:
        return check_tsmom_parity(store, read_engine_c(db).rows, now_ms=_after_close(10_000))


def test_last_digit_weight_difference_is_near_match_exit_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(a) 지나간 일봉 끝자리만 고쳐짐 → 배율 끝자리만 다름 → 「거의 일치」 · 종료 코드 0."""
    db = _rig_db(tmp_path)
    _nudge_old_eth_close(db, 1.0 + 1e-12)
    report = _report(db)
    near = report.near_matches
    assert near and all(c.asset == ETH for c in near)
    for c in near:
        assert not c.weight_ok and c.weight_near  # 정확히 같지는 않다(덮은 게 아니다)
        assert c.signal_ok and c.price_ok and c.notional_ok
        assert c.verdict == "거의 일치"
    assert report.ok and not report.mismatches
    assert cli_main(["tsmom-parity", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "ℹ️ 거의 일치" in out and "🔴 불일치 |" not in out


def test_really_different_weight_is_mismatch_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(b) 배율이 실제로 다름 → 「🔴 불일치」 · 종료 코드 1 (문턱이 진짜 어긋남을 덮지 않는다)."""
    db = _rig_db(tmp_path)
    _nudge_old_eth_close(db, 1.5)
    report = _report(db)
    bad = report.mismatches
    assert bad and all(c.asset == ETH for c in bad)
    assert all(not c.weight_ok and not c.weight_near for c in bad)
    assert cli_main(["tsmom-parity", "--db", str(db)]) == 1
    assert "🔴 불일치" in capsys.readouterr().out


def test_signal_price_and_notional_stay_exact_even_when_weight_is_near() -> None:
    """문턱은 배율에만 — 배율이 끝자리만 달라도 다른 칸이 틀리면 🔴 불일치다."""
    base = {
        "asset": ETH,
        "as_of_ms": T0,
        "ledger_weight": 0.158589,
        "recomputed_weight": 0.158589 * (1 + 1e-9),
        "ledger_signal": 0.5,
        "recomputed_signal": 0.5,
        "ledger_price": 2000.0,
        "stored_close": 2000.0,
        "notional_ok": True,
    }
    assert RowCheck(**base).verdict == "거의 일치"  # type: ignore[arg-type]
    for broken in (
        {"recomputed_signal": -0.5},
        {"stored_close": 2000.0 + 1e-9},
        {"notional_ok": False},
        {"recomputed_weight": 0.158589 * (1 + 2e-6)},
        {"recomputed_weight": None},
    ):
        assert RowCheck(**(base | broken)).verdict == "🔴 불일치", broken  # type: ignore[arg-type]
    exact = RowCheck(**(base | {"recomputed_weight": 0.158589}))  # type: ignore[arg-type]
    assert exact.verdict == "일치" and exact.weight_diff == 0.0


def test_table_shows_weight_mark_and_difference(tmp_path: Path) -> None:
    """(c) 표에 배율 ✓/≈/✗ 열과 차이(절대·상대)가 찍힌다 — 6자리로 같아 보여도 원인이 보인다."""
    near = RowCheck(ETH, T0, 0.158589, 0.158589 * (1 + 1e-8), 0.5, 0.5, 2000.0, 2000.0, True)
    bad = RowCheck(BTC, T0, 0.30, 0.25, 1.0, 1.0, 30_000.0, 30_000.0, True)
    good = RowCheck(BTC, T0 + DAY, 0.30, 0.30, 1.0, 1.0, 30_000.0, 30_000.0, True)
    text = render_tsmom_parity(TsmomParityReport(checks=[near, bad, good]))
    assert "| 배율 |" in text.splitlines()[4]
    assert "일치 1개 · 거의 일치 1개 · 불일치 1개" in text
    near_line = next(line for line in text.splitlines() if "| ETH |" in line)
    assert "| +0.158589 | +0.158589 | ≈ |" in near_line and near_line.endswith("거의 일치 |")
    assert f"배율 차이(원장 − 재계산) {near.weight_diff:+.3e} · 상대 1.00e-08" in text
    assert f"배율 차이(원장 − 재계산) {0.30 - 0.25:+.3e} · 상대 {0.05 / 0.25:.2e}" in text
    bad_line = next(line for line in text.splitlines() if "| +0.300000 | +0.250000 |" in line)
    assert "| ✗ |" in bad_line and bad_line.endswith("🔴 불일치 |")
    good_line = next(line for line in text.splitlines() if "| +0.300000 | +0.300000 |" in line)
    assert "| ✓ |" in good_line and good_line.endswith("| 일치 |")
    assert text.count("배율 차이") == 2  # 정확히 같은 행에는 차이 줄이 없다
