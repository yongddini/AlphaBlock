"""엔진 C 러너 배선(WAN-451 ⑥⑦⑧⑨⑩) — 확정봉만 · 하루 1회 · 소급 거래 없음 · 지갑 합산."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from config.settings import Settings
from data.funding import FundingRateStore
from data.models import Candle, FundingRate
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.tsmom_runner import TsmomRebalancer, format_daily_rebalance
from live.zone_limit_runner import ZoneLimitPaperRunner, engine_settings_for
from paper.store import PaperTradeRecorder, PaperTradeStore
from paper.tsmom_ledger import TsmomLedgerStore
from strategy import tsmom

DAY = tsmom.DAY_MS
N_DAYS = 320
T0 = 1_650_000_000_000 // DAY * DAY
BTC, ETH = tsmom.ASSETS


def _seed_daily(store: OhlcvStore, n: int = N_DAYS, *, skip: int | None = None) -> None:
    rng = np.random.default_rng(451)
    for asset, base in ((BTC, 30_000.0), (ETH, 2_000.0)):
        closes = base * np.cumprod(1.0 + rng.normal(0.001, 0.03, n))
        candles = [
            Candle(asset, "1d", T0 + i * DAY, c, c * 1.02, c * 0.98, float(c), 1.0)
            for i, c in enumerate(closes)
            if i != skip
        ]
        store.upsert_candles(candles)


def _add_day(store: OhlcvStore, day: int, factor: float = 1.01) -> None:
    for asset in tsmom.ASSETS:
        prev = store.load(
            asset, "1d", start_ms=T0 + (day - 1) * DAY, end_ms=T0 + (day - 1) * DAY + 1
        )
        c = float(prev["close"].iloc[-1]) * factor
        store.upsert_candles([Candle(asset, "1d", T0 + day * DAY, c, c, c, c, 1.0)])


def _executor(db: Path, ledger: TsmomLedgerStore | None) -> PaperExecutor:
    settings = Settings(db_path=str(db))
    paper = PaperTradeStore(db)
    return PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper,
        recorder=PaperTradeRecorder(paper, cost_model=settings.costs, funding_store=None),
        sizing=settings.risk_sizing,
        tsmom_ledger=ledger,
    )


def _after_close(day: int) -> int:
    """일봉 `day`가 닫힌 직후(다음 날 00:01 UTC)."""
    return T0 + (day + 1) * DAY + 60_000


def test_rebalances_once_per_closed_day_and_moves_the_wallet(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    _seed_daily(store)
    ledger = TsmomLedgerStore(db)
    wallet = _executor(db, ledger)
    sent: list[str] = []
    reb = TsmomRebalancer(store=store, ledger=ledger, wallet=wallet, notify=sent.append)
    last = N_DAYS - 1

    rows = reb.tick(_after_close(last))
    assert [r.asset for r in rows] == [BTC, ETH]
    assert all(r.as_of_ms == T0 + last * DAY for r in rows)
    assert wallet.equity == pytest.approx(10_000.0 + sum(r.net_pnl for r in rows))
    assert len(sent) == 1 and "엔진 C 리밸런싱" in sent[0]
    assert len(ledger.marks()) == 1

    assert reb.tick(_after_close(last) + 5 * 60_000) == []  # 같은 날 — 할 일 없음
    assert len(sent) == 1

    _add_day(store, N_DAYS, factor=1.05)
    nxt = reb.tick(_after_close(N_DAYS))
    btc = next(r for r in nxt if r.asset == BTC)
    assert btc.settled_pnl == pytest.approx(rows[0].new_qty * btc.prev_price * 0.05)  # type: ignore[operator]
    assert ledger.total_net_pnl() == pytest.approx(sum(r.net_pnl for r in ledger.rows()))


def test_never_uses_the_forming_daily_bar(tmp_path: Path) -> None:
    """오늘 봉(아직 안 닫힘)이 저장소에 있어도 그 종가로 포지션을 정하지 않는다(룩어헤드)."""
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    _seed_daily(store)
    ledger = TsmomLedgerStore(db)
    reb = TsmomRebalancer(store=store, ledger=ledger, wallet=_executor(db, ledger))
    forming_open = T0 + N_DAYS * DAY
    store.upsert_candles(
        [Candle(a, "1d", forming_open, 1.0, 1.0, 1.0, 1.0, 1.0, closed=False) for a in tsmom.ASSETS]
    )
    rows = reb.tick(forming_open + 3_600_000)  # 오늘 01:00 UTC — 오늘 봉은 형성 중
    assert rows and all(r.as_of_ms == forming_open - DAY for r in rows)


def test_missing_day_holds_off_without_fabricating(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    _seed_daily(store, skip=N_DAYS - 10)
    ledger = TsmomLedgerStore(db)
    sent: list[str] = []
    reb = TsmomRebalancer(
        store=store, ledger=ledger, wallet=_executor(db, ledger), notify=sent.append
    )
    assert reb.tick(_after_close(N_DAYS - 1)) == []
    assert ledger.rows() == []
    assert len(sent) == 2 and all("보류" in m for m in sent)  # 자산마다 한 번
    reb.tick(_after_close(N_DAYS - 1) + 60_000)
    assert len(sent) == 2  # 같은 실패를 매 폴링 반복해 알리지 않는다


def test_downtime_is_settled_without_backdated_trades(tmp_path: Path) -> None:
    """러너가 사흘 죽어 있었다 — 빠진 날을 소급 거래하지 않고 보유 유지로 한 번에 정산한다."""
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    _seed_daily(store)
    funding = FundingRateStore(db)
    ledger = TsmomLedgerStore(db)
    reb = TsmomRebalancer(
        store=store, ledger=ledger, wallet=_executor(db, ledger), funding_store=funding
    )
    first = reb.tick(_after_close(N_DAYS - 1))
    for d in range(N_DAYS, N_DAYS + 3):
        _add_day(store, d)
    held_from = T0 + N_DAYS * DAY
    funding.upsert_rates(
        [FundingRate(BTC, held_from + k * 8 * 3_600_000, 0.0001) for k in range(9)]
    )
    rows = reb.tick(_after_close(N_DAYS + 2))
    btc = next(r for r in rows if r.asset == BTC)
    assert btc.as_of_ms - first[0].as_of_ms == 3 * DAY  # 행 하나 · 사흘 건너뜀
    assert len(ledger.rows(BTC)) == 2
    first_btc = next(r for r in first if r.asset == BTC)
    side = 1.0 if first_btc.new_qty > 0 else -1.0
    assert btc.funding == pytest.approx(
        side * abs(first_btc.new_qty) * first_btc.price * 0.0001 * 9
    )


def test_wallet_restores_from_both_ledgers(tmp_path: Path) -> None:
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    _seed_daily(store)
    ledger = TsmomLedgerStore(db)
    reb = TsmomRebalancer(store=store, ledger=ledger, wallet=_executor(db, ledger))
    rows = reb.tick(_after_close(N_DAYS - 1))
    restarted = _executor(db, TsmomLedgerStore(db))
    assert restarted.equity == pytest.approx(10_000.0 + sum(r.net_pnl for r in rows))
    assert _executor(db, None).equity == 10_000.0  # 원장을 안 물리면 예전과 같다


def test_engine_c_turns_the_circuit_breaker_off_only_when_enabled() -> None:
    off = Settings()
    assert engine_settings_for(off) is off
    assert off.risk_limits.daily_loss_limit_fraction == 0.05
    on = Settings(live_tsmom_enabled=True)
    assert engine_settings_for(on).risk_limits.daily_loss_limit_fraction is None
    assert on.risk_limits.daily_loss_limit_fraction == 0.05  # 원본 설정은 안 바뀐다


def test_switch_defaults_keep_todays_behaviour() -> None:
    """배포만으로 규칙이 조용히 바뀌지 않는다 — 전환은 리셋 이음매에서 사람이 한다."""
    s = Settings()
    assert s.live_zone_limit_enabled is True and s.live_tsmom_enabled is False


def test_runner_poll_drives_the_rebalancer(tmp_path: Path) -> None:
    """시리즈 0개(옛 오더블록 꺼짐)여도 러너 루프가 엔진 C를 하루 1회 돌린다."""
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    _seed_daily(store)
    ledger = TsmomLedgerStore(db)
    wallet = _executor(db, ledger)
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    settings = Settings(db_path=str(db))
    clock = [_after_close(N_DAYS - 1)]
    runner = ZoneLimitPaperRunner(
        store=store,
        engine=ZoneLimitLiveEngine(params=settings.confluence, journal=journal, session_id=session),
        journal=journal,
        session_id=session,
        executor=wallet,
        params=settings.confluence,
        series=[],
        lookback_bars=settings.live_signal_lookback_bars,
        poll_interval_seconds=0,
        tsmom=TsmomRebalancer(store=store, ledger=ledger, wallet=wallet),
        now_ms=lambda: clock[0],
    )
    runner.poll_once()
    runner.poll_once()
    assert len(ledger.rows()) == 2


def test_daily_message_flags_long_short_flip() -> None:
    from paper.tsmom_ledger import RebalanceRow

    row = RebalanceRow(
        asset=BTC,
        as_of_ms=T0,
        executed_ms=T0 + DAY,
        price=100.0,
        prev_price=110.0,
        signal=0.5,
        annual_vol=0.5,
        weight=0.4,
        equity_basis=10_000.0,
        target_notional=2_814.3,
        prev_qty=-10.0,
        new_qty=28.143,
        traded_notional=3_814.3,
        settled_pnl=100.0,
        funding=-1.0,
        cost=3.43,
    )
    text = format_daily_rebalance([row], equity=10_097.57, gaps={BTC: 3})
    assert "🔁 BTC: 숏 → 롱" in text
    assert "상승 신호 3/4" in text
    assert "3일 공백" in text
