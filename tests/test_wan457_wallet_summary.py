"""WAN-457 §1: 하루 1회 「💰 지갑 요약」 — 숫자 일치를 라벨이 아니라 동작으로 건다.

실제 `PaperExecutor`(팔 진입·청산 · 비용이 0이 아닌 장부) + 실제 `TsmomRebalancer`(엔진 C 정산)를
한 DB에서 돌리고, 리밸런싱 직후 보낸 요약이:

1. 총자산 == 같은 시각 `paper_equity_marks.equity`(장부 값)
2. 내역의 합 == 어제→오늘 총자산 변화(잔차 포함 정확히) · 정상 운영에서 잔차 ≈ 0
3. 실현 잔고 == 대시보드 「지갑 잔고(팔 + 엔진 C)」 공식

인지 확인한다. 지갑과 장부가 어긋나면 잔차가 0이 아니게 되고 요약이 그 사실을 찍는다(숨기지 않는다).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from config.settings import Settings
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import EntryIntent, build_execution_engine
from live.executor import PaperExecutor
from live.tsmom_runner import TsmomRebalancer
from paper.engine_c_report import read_engine_c
from paper.store import PaperTradeRecorder, PaperTradeStore
from paper.tsmom_ledger import EquityMark, TsmomLedgerStore
from paper.wallet_summary import (
    build_balance_reply,
    build_daily_wallet_summary,
    format_daily_wallet_summary,
    last_close,
    read_marks_before,
    read_wallet_snapshot,
    wallet_change,
)
from strategy import tsmom
from strategy.models import OrderBlockDirection, SignalExitReason

DAY = tsmom.DAY_MS
N_DAYS = 320
T0 = 1_650_000_000_000 // DAY * DAY
BTC, ETH = tsmom.ASSETS
SOL = "SOL/USDT:USDT"
DOGE = "DOGE/USDT:USDT"
INITIAL = 10_000.0


def _seed_daily(store: OhlcvStore) -> None:
    rng = np.random.default_rng(457)
    for asset, base in ((BTC, 30_000.0), (ETH, 2_000.0)):
        closes = base * np.cumprod(1.0 + rng.normal(0.001, 0.03, N_DAYS))
        store.upsert_candles(
            [
                Candle(asset, "1d", T0 + i * DAY, c, c * 1.02, c * 0.98, float(c), 1.0)
                for i, c in enumerate(closes)
            ]
        )


def _add_day(store: OhlcvStore, day: int, factor: float) -> None:
    for asset in tsmom.ASSETS:
        prev = store.load(
            asset, "1d", start_ms=T0 + (day - 1) * DAY, end_ms=T0 + (day - 1) * DAY + 1
        )
        c = float(prev["close"].iloc[-1]) * factor
        store.upsert_candles([Candle(asset, "1d", T0 + day * DAY, c, c, c, c, 1.0)])


def _minute(store: OhlcvStore, symbol: str, t: int, close: float) -> None:
    store.upsert_candles([Candle(symbol, "1m", t, close, close, close, close, 1.0)])


def _executor(db: Path, ledger: TsmomLedgerStore) -> PaperExecutor:
    settings = Settings(db_path=str(db), paper_equity=INITIAL)
    paper = PaperTradeStore(db)
    return PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper,
        recorder=PaperTradeRecorder(paper, cost_model=settings.costs, funding_store=None),
        sizing=settings.risk_sizing,
        tsmom_ledger=ledger,
    )


def _intent(symbol: str, t: int, entry: float) -> EntryIntent:
    return EntryIntent(
        symbol=symbol,
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_price=entry,
        entry_time=t,
        stop_price=entry * 0.95,
        take_profit_price=entry * 1.2,
    )


def _after_close(day: int) -> int:
    return T0 + (day + 1) * DAY + 60_000


class _Rig:
    """한 DB 위의 러너 부품 — 팔(실행기) + 엔진 C(리밸런서) + 요약 배선."""

    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "ohlcv.db"
        self.store = OhlcvStore(self.db)
        _seed_daily(self.store)
        self.ledger = TsmomLedgerStore(self.db)
        self.wallet = _executor(self.db, self.ledger)
        self.sent: list[str] = []
        self.reb = TsmomRebalancer(
            store=self.store,
            ledger=self.ledger,
            wallet=self.wallet,
            notify=self.sent.append,
            wallet_summary=lambda mark: build_daily_wallet_summary(
                self.db, mark=mark, initial_equity=INITIAL
            ),
        )

    def day_one_then_arm_then_day_two(self) -> None:
        last = N_DAYS - 1
        assert self.reb.tick(_after_close(last))
        mark1_ms = T0 + N_DAYS * DAY
        # 그사이 팔: SOL 왕복(익절 · 비용 있음) + DOGE 열어 둔 채 다음 평가.
        t = mark1_ms + 3_600_000
        report = self.wallet.enter(_intent(SOL, t, 100.0), now_ms=t)
        assert report.outcome.accepted
        self.wallet.exit(
            SOL,
            "1h",
            exit_price=110.0,
            exit_time=t + 3_600_000,
            reason=SignalExitReason.TAKE_PROFIT,
            now_ms=t + 3_600_000,
        )
        report = self.wallet.enter(_intent(DOGE, t + 7_200_000, 0.2), now_ms=t + 7_200_000)
        assert report.outcome.accepted
        mark2_ms = mark1_ms + DAY
        _minute(self.store, DOGE, mark2_ms - 120_000, 0.21)  # 평가가 = 0.21(+5%)
        _add_day(self.store, N_DAYS, factor=1.03)
        assert self.reb.tick(_after_close(N_DAYS))


def test_daily_summary_is_sent_after_each_rebalance_even_on_quiet_days(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    assert rig.reb.tick(_after_close(N_DAYS - 1))
    # 리밸런싱 알림 + 지갑 요약 두 통 — 팔 거래가 하나도 없던 날에도 온다.
    assert len(rig.sent) == 2
    assert rig.sent[0].startswith("📈 엔진 C 리밸런싱")
    assert rig.sent[1].startswith("💰 지갑 요약")
    assert "시작 대비" in rig.sent[1]  # 첫날은 비교할 어제가 없다.


def test_total_equals_the_ledger_mark_and_breakdown_sums_to_the_change(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    marks = rig.ledger.marks()
    assert len(marks) == 2
    prev, mark = marks
    summary = rig.sent[-1]

    # 1) 총자산 == 장부 값(같은 시각 paper_equity_marks.equity) — 찍힌 문자열로 건다.
    conn = sqlite3.connect(rig.db)
    stored = conn.execute(
        "SELECT equity FROM paper_equity_marks WHERE mark_ms = ?", (mark.mark_ms,)
    ).fetchone()[0]
    conn.close()
    assert f"평가 포함 총자산 ${stored:,.2f}" in summary

    # 2) 내역의 합 == 변화(정확히) · 정상 운영에서 잔차 ≈ 0.
    change = wallet_change(rig.db, current=mark, previous=prev, initial_equity=INITIAL)
    assert change.total == pytest.approx(mark.equity - prev.equity, abs=1e-9)
    assert change.explained == pytest.approx(change.total, abs=1e-9)
    assert abs(change.residual) < 1e-6
    # 각 칸이 장부 행 그대로다.
    record = next(r for r in rig.wallet._store.list_records() if r.symbol == SOL)  # noqa: SLF001
    assert record.realized_pnl is not None
    assert change.arm_realized == pytest.approx(record.realized_pnl)
    assert record.realized_pnl != pytest.approx(10.0 * (record.quantity or 0.0))  # 비용이 빠졌다
    day2 = [r for r in rig.ledger.rows() if r.executed_ms == mark.mark_ms]
    assert change.engine_c_net == pytest.approx(sum(r.net_pnl for r in day2))
    assert change.arm_unrealized_change == pytest.approx(mark.unrealized - prev.unrealized)
    assert mark.unrealized != 0.0  # DOGE 평가손익이 실제로 들어 있다.
    assert "장부로 설명 안 되는 몫" not in summary
    assert f"{prev.mark_ms}" not in summary  # 시각은 KST로 찍힌다(ms 원값 아님)


def test_realized_balance_matches_the_dashboard_formula(tmp_path: Path) -> None:
    """PM 지적 — 텔레그램 「실현 잔고」 == 대시보드 「지갑 잔고(팔 + 엔진 C)」."""
    from dashboard.app import _wallet_balance

    rig = _Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    mark = rig.ledger.marks()[-1]
    snap = read_wallet_snapshot(rig.db, initial_equity=INITIAL, at_ms=mark.mark_ms)
    records = PaperTradeStore(rig.db).list_records()
    arm_balance = _wallet_balance(records, initial_equity=INITIAL)
    assert arm_balance is not None
    dashboard_wallet = INITIAL + (arm_balance - INITIAL) + read_engine_c(rig.db).total_net_pnl
    assert snap.realized == pytest.approx(dashboard_wallet, abs=1e-9)
    assert f"실현 잔고 ${snap.realized:,.2f} (대시보드와 같음)" in rig.sent[-1]
    # 그리고 지갑 자본(장부 mark.realized)과도 같다 — 두 엔진이 한 지갑을 쓴다.
    assert snap.realized == pytest.approx(mark.realized, abs=1e-6)
    # 포지션별 평가손익 합이 장부의 미실현과 같은 가격으로 계산된다.
    assert snap.equity == pytest.approx(mark.equity, abs=1e-6)
    assert "DOGE 1h 롱 @ 0.2" in rig.sent[-1]


def test_wallet_ledger_divergence_shows_up_as_residual_not_hidden(tmp_path: Path) -> None:
    """장부에 지갑이 모르는 청산이 끼면 잔차가 0이 아니고 요약이 그 사실을 찍는다."""
    rig = _Rig(tmp_path)
    rig.day_one_then_arm_then_day_two()
    prev, mark = rig.ledger.marks()
    store = PaperTradeStore(rig.db)
    sol = next(r for r in store.list_records() if r.symbol == SOL)
    store.upsert_record(
        sol.model_copy(
            update={"symbol": "XRP/USDT:USDT", "exit_time": mark.mark_ms - 1, "realized_pnl": -42.0}
        )
    )
    store.close()
    change = wallet_change(rig.db, current=mark, previous=prev, initial_equity=INITIAL)
    assert change.residual == pytest.approx(42.0)
    assert change.explained == pytest.approx(change.total, abs=1e-9)
    snap = read_wallet_snapshot(rig.db, initial_equity=INITIAL, at_ms=mark.mark_ms)
    text = format_daily_wallet_summary(snap, mark=mark, change=change)
    assert "장부로 설명 안 되는 몫 +$42.00" in text
    assert "지갑 자본과 장부 합이 +$42.00 어긋남" in text


def test_reads_are_read_only(tmp_path: Path) -> None:
    """조회는 DB를 만들지도, 테이블을 만들지도 않는다(DB 쓰기 없음)."""
    missing = tmp_path / "none.db"
    snap = read_wallet_snapshot(missing, initial_equity=INITIAL, at_ms=T0)
    assert snap.realized == INITIAL and not missing.exists()
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    reply = build_balance_reply(empty, initial_equity=INITIAL, now_ms=T0)
    assert "정산 기준 자본 $10,000.00" in reply
    conn = sqlite3.connect(empty)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    conn.close()
    assert read_marks_before(empty, T0) is None


def test_last_close_uses_the_rebalancer_price_window(tmp_path: Path) -> None:
    """평가가 규칙이 `TsmomRebalancer._last_price`와 같다 — `open_time ∈ [at − 10분, at)`."""
    rig = _Rig(tmp_path)
    at = T0 + N_DAYS * DAY
    _minute(rig.store, DOGE, at - 11 * 60_000, 1.0)  # 창 밖
    _minute(rig.store, DOGE, at - 3 * 60_000, 2.0)
    _minute(rig.store, DOGE, at, 3.0)  # at 자신은 제외
    assert last_close(rig.db, DOGE, at) == 2.0
    assert rig.reb._last_price(DOGE, at) == 2.0  # noqa: SLF001
    assert last_close(rig.db, SOL, at) is None


def test_summary_failure_does_not_block_the_rebalance(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)

    def boom(_: EquityMark) -> str:
        raise RuntimeError("조회 실패")

    rig.reb.wallet_summary = boom
    rows = rig.reb.tick(_after_close(N_DAYS - 1))
    assert rows and len(rig.ledger.marks()) == 1
    assert len(rig.sent) == 1  # 리밸런싱 알림은 갔다
