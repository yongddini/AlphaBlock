"""존-지정가 페이퍼 러너 통합 테스트 (WAN-45).

완료 기준의 통합 시나리오를 저장소(실제 SQLite) + 모의 틱 스트림(합성 1분봉)으로
재현한다: 기본값(`entry_mode="zone_limit"`)에서 러너가 지정가를 예약하고, 터치에
체결돼 페이퍼 포지션이 열리고, 손절/익절로 청산되며, 그 전 과정이 체결률 장부와
운영 상태 스냅샷에 남는다. `python -m live.runner`의 기본값 위임도 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

import live.runner as runner_mod
import live.zone_limit_runner as zlr
from config.settings import Settings
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live import limit_engine
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.runtime_state import RuntimeStateStore
from live.zone_limit_notifier import ZoneLimitNotifier
from live.zone_limit_runner import ZoneLimitPaperRunner
from paper.store import PaperTradeRecorder, PaperTradeStore
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
)

_H = 3_600_000
_M = 60_000
_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"
_N_CLOSED = 30
_FORMING = _N_CLOSED * _H


def _zone() -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=95.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=_H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _install_stub_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    result = OrderBlockResult(order_blocks=[_zone()], signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)


def _htf_candle(i: int, close: float = 100.0) -> Candle:
    return Candle(
        symbol=_SYMBOL,
        timeframe=_TF,
        open_time=i * _H,
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=1.0,
        closed=True,
    )


def _m1(t: int, low: float, high: float, close: float) -> Candle:
    return Candle(
        symbol=_SYMBOL,
        timeframe="1m",
        open_time=t,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        closed=True,
    )


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, object]]:
    _install_stub_detector(monkeypatch)
    db = tmp_path / "ohlcv.db"
    store = OhlcvStore(db)
    store.upsert_candles([_htf_candle(i) for i in range(_N_CLOSED)])

    settings = Settings(db_path=str(db))
    params = ConfluenceParams(max_zone_width_atr=None)  # 합성 데이터 ATR로는 존이 걸러진다.
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    paper_store = PaperTradeStore(db)
    recorder = PaperTradeRecorder(paper_store, cost_model=settings.costs, funding_store=None)
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper_store,
        recorder=recorder,
        sizing=settings.risk_sizing,
    )
    engine = ZoneLimitLiveEngine(
        params=params,
        journal=journal,
        session_id=session,
        has_position=lambda s, t: any(
            p.symbol == s and p.timeframe == t for p in executor.open_positions
        ),
    )
    state_path = tmp_path / "runtime_state.json"
    runner = ZoneLimitPaperRunner(
        store=store,
        engine=engine,
        journal=journal,
        session_id=session,
        executor=executor,
        params=params,
        series=[(_SYMBOL, _TF)],
        lookback_bars=500,
        poll_interval_seconds=1.0,
        runtime_state=RuntimeStateStore(state_path),
        now_ms=lambda: 999_999,
    )
    yield {
        "store": store,
        "runner": runner,
        "executor": executor,
        "journal": journal,
        "paper_store": paper_store,
        "state_path": state_path,
    }
    store.close()
    journal.close()
    paper_store.close()


def test_reserve_fill_open_and_exit_roundtrip(rig: dict[str, object]) -> None:
    """예약 → 체결 → 페이퍼 포지션 오픈 → 익절 청산의 전체 왕복(통합, 완료 기준 1)."""
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]

    # 1) 존 밖 1분봉만 있는 첫 폴링 — 아무 일도 없다.
    store.upsert_candles([_m1(_FORMING, 99.0, 100.5, 100.0)])
    runner.poll_once()
    assert executor.open_positions == []

    # 2) 존 탭 + 터치 1분봉 → 예약과 체결이 같은 서브스텝에서 일어나고 포지션이 열린다.
    store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])
    runner.poll_once()
    positions = executor.open_positions
    assert len(positions) == 1
    entry = positions[0]
    assert entry.entry_price == pytest.approx(95.0 * 1.0002)
    assert entry.stop_price == 90.0
    expected_tp = entry.entry_price + 1.5 * (entry.entry_price - 90.0)
    assert entry.take_profit_price == pytest.approx(expected_tp)

    # 체결이 장부에 남았다(체결률 실측 — 1급 산출물).
    stats = journal.fill_stats()
    assert len(stats) == 1 and stats[0].filled == 1

    # 3) 익절 목표를 관통하는 1분봉 → 포지션 청산(고정 1.5R).
    store.upsert_candles([_m1(_FORMING + 2 * _M, 95.0, 103.5, 103.0)])
    runner.poll_once()
    assert executor.open_positions == []
    paper_store: PaperTradeStore = rig["paper_store"]  # type: ignore[assignment]
    assert paper_store.count(_SYMBOL, _TF) == 1


def test_stop_loss_exit(rig: dict[str, object]) -> None:
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]

    store.upsert_candles([_m1(_FORMING, 94.9, 100.0, 95.2)])
    runner.poll_once()
    assert len(executor.open_positions) == 1

    # 손절 참조가(존 하단 90)를 관통 → 손절 청산. 같은 봉에서 손절이 익절보다 우선한다.
    store.upsert_candles([_m1(_FORMING + _M, 89.5, 104.0, 90.5)])
    runner.poll_once()
    assert executor.open_positions == []


def test_pending_order_exposed_in_runtime_state(
    rig: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """대기 주문 목록이 러너 상태 스냅샷에 노출된다(완료 기준: 로그·스냅샷 노출).

    하락 시딩(130→101)으로 밴드(≈98.8)를 존(90~99.5) 안에 앉혀 "예약은 됐지만 아직
    터치는 아닌" 대기 상태를 만든다.
    """
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    state_path: Path = rig["state_path"]  # type: ignore[assignment]

    desc_zone = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=99.5,
        bottom=90.0,
        start_time=0,
        confirmed_time=_H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    result = OrderBlockResult(order_blocks=[desc_zone], signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)
    store.upsert_candles([_htf_candle(i, close=130.0 - i) for i in range(_N_CLOSED)])
    # 존 탭(저가 99.4 ≤ 상단 99.5)이되 지정가(≈98.8)에는 안 닿는 1분봉 → 대기.
    store.upsert_candles([_m1(_FORMING, 99.4, 101.5, 100.0)])
    runner.poll_once()

    state = RuntimeStateStore(state_path).load()
    assert state.updated_at == 999_999
    assert state.open_positions == []
    assert len(state.pending_orders) == 1
    pending = state.pending_orders[0]
    assert pending.symbol == _SYMBOL
    assert pending.limit_price is not None and 97.0 < pending.limit_price < 99.4
    assert pending.stop_price == 90.0


def test_priming_does_not_replay_history(rig: dict[str, object]) -> None:
    """첫 폴링은 현재 형성 중인 상위TF 봉부터 소비한다 — 과거 탭에 뒷북 주문 금지."""
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]

    # 지나간 확정 봉 안의 탭 1분봉(재생하면 예약·체결이 났을 데이터).
    old = _FORMING - 2 * _H + 5 * _M
    store.upsert_candles([_m1(old, 94.0, 100.0, 94.5)])
    runner.poll_once()
    assert executor.open_positions == []
    assert journal.fill_stats() == []


def test_runner_forwards_events_to_notifier(rig: dict[str, object]) -> None:
    """러너가 체결·청산을 알림기로 넘긴다(WAN-189) — 배선을 동작으로 고정."""
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]

    fills: list[object] = []
    exits: list[object] = []

    class _Spy:
        def note_placed(self) -> None: ...
        def note_expired(self) -> None: ...
        def tick(self, equity: float, *, now_ms: int | None = None) -> None: ...
        def handle_fill(self, fill: object, report: object) -> None:
            fills.append(fill)

        def handle_exit(
            self, report: object, *, exit_price: float, reason: object, exit_time: int
        ) -> None:
            exits.append((exit_price, reason))

    runner._notifier = _Spy()  # type: ignore[assignment]

    # 예약+체결이 같은 서브스텝 → handle_fill 한 번.
    store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])
    runner.poll_once()
    assert len(fills) == 1

    # 익절 관통 → handle_exit 한 번.
    store.upsert_candles([_m1(_FORMING + 2 * _M, 95.0, 103.5, 103.0)])
    runner.poll_once()
    assert len(exits) == 1


def test_default_settings_dispatch_to_zone_limit_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python -m live.runner` 기본값(`entry_mode="zone_limit"`)이 이 러너로 위임된다.

    WAN-95 이후 채택 기본값에서 옛 A안 경로가 도는 것이 비정상이라는 이슈 배너 1번의
    완료 기준이다. A안은 `entry_mode="close"`로 명시했을 때만 돈다.
    """
    calls: list[bool] = []
    monkeypatch.setattr(zlr, "run_zone_limit_runner", lambda settings, once: calls.append(once))
    settings = Settings(live_signal_symbols=[], live_signal_timeframes=[])
    assert settings.confluence.entry_mode == "zone_limit"
    runner_mod.run_signal_runner(settings, once=True)
    assert calls == [True]


# -- 체결 ≠ 진입 (WAN-194) -----------------------------------------------------


def _install_narrow_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """손절폭 가드(0.3%)에 걸릴 만큼 좁은 존을 물린다 — WAN-194 증상의 재현 장치.

    존 하단이 진입가에 너무 가까워 `position_size`가 0을 낸다(WAN-79 가드). 백테스트도
    같은 자로 이 후보를 버리므로 거부 자체는 정상이다 — 이 테스트가 고정하는 것은 그
    거부가 **기록되는지**다.
    """
    narrow = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=95.0,
        bottom=94.8,  # 진입가(≈95.019) 대비 손절 거리 0.23% < 가드 0.3%.
        start_time=0,
        confirmed_time=_H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    result = OrderBlockResult(order_blocks=[narrow], signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)


def test_rejected_entry_is_recorded_not_silent(
    rig: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """체결됐지만 진입이 거부되면 그 처분이 장부에 남는다 (WAN-194 §3).

    이것이 이 이슈의 증상 그 자체다: `live_limit_orders`는 `filled`인데 `open_positions`·
    `paper_trades`가 비어 DB 손상으로 보였다. 거부가 기록되면 그 모양이 **정상 동작**으로
    읽히고, 기록이 없는 행만 진짜 유실로 남는다.
    """
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]
    paper_store: PaperTradeStore = rig["paper_store"]  # type: ignore[assignment]
    _install_narrow_zone(monkeypatch)

    store.upsert_candles([_m1(_FORMING + _M, 94.79, 99.0, 94.9)])
    runner.poll_once()

    # 증상 재현: 체결은 났는데 포지션·거래가 없다.
    stats = journal.fill_stats()
    assert len(stats) == 1 and stats[0].filled == 1
    assert executor.open_positions == []
    assert paper_store.count(_SYMBOL, _TF) == 0

    # 그러나 이제 그 이유가 장부에 있다 — 거부로 분류되고 유실 후보가 아니다.
    assert stats[0].entry_rejected == 1
    assert stats[0].entered == 0
    assert stats[0].entry_unrecorded == 0
    assert journal.orphan_fills() == []
    reason = journal._conn.execute(  # noqa: SLF001 — 사유가 실제로 찍혔는지 보는 회귀 테스트.
        "SELECT entry_reject_reason FROM live_limit_orders WHERE status = 'filled'"
    ).fetchone()[0]
    assert reason and "사이징" in reason


def test_accepted_entry_records_entered_disposition(rig: dict[str, object]) -> None:
    """정상 진입도 처분이 남는다 — 안 남기면 성공한 체결이 유실 후보로 잡힌다."""
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]

    store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])
    runner.poll_once()

    stats = journal.fill_stats()[0]
    assert (stats.entered, stats.entry_rejected, stats.entry_unrecorded) == (1, 0, 0)
    assert stats.entry_rate == 1.0
    assert journal.orphan_fills() == []


def test_rejected_entry_notifies(rig: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    """거부도 알림으로 나간다 — 조용히 넘기면 폰에서 "아무 일도 없었다"와 같아 보인다."""
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    _install_narrow_zone(monkeypatch)

    sent: list[str] = []

    class _Telegram:
        def send_message(self, text: str) -> bool:
            sent.append(text)
            return True

    notifier = ZoneLimitNotifier(_Telegram())  # type: ignore[arg-type]
    runner._notifier = notifier

    store.upsert_candles([_m1(_FORMING + _M, 94.79, 99.0, 94.9)])
    runner.poll_once()

    assert len(sent) == 1
    assert "진입 거부" in sent[0]
    assert "사이징" in sent[0]
