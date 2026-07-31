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
from paper.performance import build_performance
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


def test_wallet_reconstruction_matches_runner_equity(rig: dict[str, object]) -> None:
    """청산 시 달러 금액이 남고, 그것으로 재구성한 자본 곡선이 러너 실제 지갑과 일치한다.

    §2 완료 기준(WAN-207): 전액배팅이 아니라 실제 지갑 기준 total_return — 재구성 곡선의
    최종 자본이 러너의 실제 `equity`와 같고, 총수익률이 (최종−초기)/초기와 정합한다.
    """
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]
    paper_store: PaperTradeStore = rig["paper_store"]  # type: ignore[assignment]

    initial = executor.equity  # 거래 전 초기 자본.

    store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])
    runner.poll_once()
    store.upsert_candles([_m1(_FORMING + 2 * _M, 95.0, 103.5, 103.0)])
    runner.poll_once()

    records = paper_store.list_records(_SYMBOL, _TF)
    assert len(records) == 1
    rec = records[0]
    # 달러 금액이 영속됐다("얼마 들어갔는지").
    assert rec.quantity is not None and rec.quantity > 0.0
    assert rec.notional == pytest.approx(rec.entry_price * rec.quantity)
    assert rec.risk_amount is not None and rec.risk_amount > 0.0
    assert rec.realized_pnl is not None
    assert rec.equity_after == pytest.approx(executor.equity)

    perf = build_performance(records, initial_equity=initial)
    expected = (executor.equity - initial) / initial * 100.0
    assert perf.overall.total_return_pct == pytest.approx(expected)
    assert perf.overall.total_realized_pnl == pytest.approx(rec.realized_pnl)


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
    완료 기준이다. 옛 A안(종가 시그널) 경로는 WAN-208에서 제거돼, 진입점은 항상 이
    존-지정가 러너로 위임한다.
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


def test_rejected_entry_stores_reject_code(
    rig: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """거부의 **코드**(WAN-221)가 자유 텍스트와 함께 남는다 — 손절폭 가드(0.3%)는 `sizing`.

    일일 요약의 사유 카운트가 이 코드로 명목/사이징/슬롯참을 가른다(자유 텍스트 파싱 금지)."""
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]
    _install_narrow_zone(monkeypatch)

    store.upsert_candles([_m1(_FORMING + _M, 94.79, 99.0, 94.9)])
    runner.poll_once()

    code = journal._conn.execute(  # noqa: SLF001 — 코드가 실제로 찍혔는지 보는 회귀 테스트.
        "SELECT entry_reject_code FROM live_limit_orders WHERE status = 'filled'"
    ).fetchone()[0]
    assert code == "sizing"

    # 그 코드로 요약 깔때기가 사이징 거부를 센다(전 구간 창).
    funnel = journal.funnel_counts(start_ms=0, end_ms=_FORMING + 10 * _M)
    assert funnel.sizing == 1
    assert funnel.filled == 1  # 체결 자체는 났다(거부는 하류 처분).


def test_expiry_forwards_deviation_not_no_fill_to_filter_skip() -> None:
    """만료 중 밴드기각(deviation)만 옵트인 건별 알림으로 넘긴다 — no_fill은 안 넘긴다(WAN-221).

    `_handle_events`를 직접 태워 배선을 고정한다: 걸렸다 안 닿은 순수 만료(no_fill,
    `first_rested_ms` 있음)는 `note_expired`만, 밴드가 한 번도 유리하지 않은 만료
    (`first_rested_ms is None`)는 거기 더해 `note_filter_skip("deviation")`."""
    from live.limit_engine import EngineEvent
    from live.limit_orders import PendingLimitOrder
    from strategy.realtime_rsi import RealtimeRsi

    class _NeverRests:
        """봉내 지정가가 한 번도 유리하지 않아 주문판에 실린 적 없는 공급자(deviation 모양)."""

        def commit(self, closed_price: float) -> None: ...
        def limit_price(self, live_price: float) -> float | None:
            return None

        def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
            return None

    def _no_fill_order() -> PendingLimitOrder:
        # 정적 지정가는 예약 즉시 주문판에 걸린다(first_rested_ms=placed_ms) → 순수 no_fill.
        return PendingLimitOrder(
            symbol=_SYMBOL,
            timeframe=_TF,
            direction=OrderBlockDirection.BULLISH,
            limit_price=100.0,
            stop_price=90.0,
            rsi_state=RealtimeRsi(length=3),
            placed_ms=1_000,
        )

    def _deviation_order() -> PendingLimitOrder:
        # 봉내 재산정이 계속 기각 → first_rested_ms NULL → 밴드 규칙 3 기각(deviation).
        return PendingLimitOrder(
            symbol=_SYMBOL,
            timeframe=_TF,
            direction=OrderBlockDirection.BULLISH,
            live_limit=_NeverRests(),
            stop_price=90.0,
            rsi_state=RealtimeRsi(length=3),
            placed_ms=1_000,
        )

    expired: list[str] = []
    skips: list[str] = []

    class _Spy:
        def note_expired(self) -> None:
            expired.append("x")

        def note_filter_skip(
            self, reason: str, *, symbol: str, timeframe: str, time_ms: int
        ) -> None:
            skips.append(reason)

    runner: ZoneLimitPaperRunner = _make_runner_with_notifier(_Spy())
    runner._handle_events(  # noqa: SLF001 — 배선을 동작으로 고정.
        [
            EngineEvent("cancelled_expired", _SYMBOL, _TF, 2_000, _no_fill_order()),
            EngineEvent("cancelled_expired", _SYMBOL, _TF, 2_100, _deviation_order()),
        ]
    )
    assert expired == ["x", "x"]  # 둘 다 만료 카운터엔 잡힌다.
    assert skips == ["deviation"]  # deviation만 건별로 넘어간다(no_fill은 안 넘어간다).


def _make_runner_with_notifier(notifier: object) -> ZoneLimitPaperRunner:
    """알림기만 바꾼 최소 러너(이벤트 처리 배선 테스트용). 이 테스트는 `_handle_events`만
    태우므로 폴링 데이터·시리즈는 필요 없다."""
    import tempfile

    db = Path(tempfile.mkdtemp()) / "ohlcv.db"
    store = OhlcvStore(db)
    journal = OrderJournal(db)
    session = journal.start_session(now_ms=0)
    settings = Settings(db_path=str(db))
    paper_store = PaperTradeStore(db)
    executor = PaperExecutor(
        engine=build_execution_engine(settings),
        store=paper_store,
        recorder=PaperTradeRecorder(paper_store, cost_model=settings.costs, funding_store=None),
        sizing=settings.risk_sizing,
    )
    engine = ZoneLimitLiveEngine(params=settings.confluence, journal=journal, session_id=session)
    return ZoneLimitPaperRunner(
        store=store,
        engine=engine,
        journal=journal,
        session_id=session,
        executor=executor,
        params=settings.confluence,
        series=[],
        lookback_bars=settings.live_signal_lookback_bars,
        poll_interval_seconds=settings.live_poll_interval_seconds,
        notifier=notifier,  # type: ignore[arg-type]
    )


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


def test_fill_on_later_poll_records_disposition(
    rig: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """예약과 체결이 **다른 폴링 배치**에서 나도 처분·진입이 남는다 (WAN-207 근본 원인).

    이전 버그: 체결 처리가 `order.journal_id`를 읽었는데 `order`는 `placed` 분기에서만
    대입되는 지역 변수였다. 주문이 걸린 뒤 나중 폴링에서 가격이 닿아 체결되면 그 배치엔
    `placed`가 없어 `UnboundLocalError`가 진입 **직후** 터졌고 — 포지션은 열렸는데
    `entry_status`는 NULL, 진입 알림도 유실됐다. 이제 체결 이벤트가 실어 온 주문을 쓴다.
    """
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]

    # 하락 시딩(130→101)으로 밴드(≈98.8)를 넓은 존(90~99.5) 안에 앉혀 "예약은 됐지만
    # 아직 터치는 아닌" 대기 상태를 만든다(test_pending_order_exposed와 같은 장치).
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

    # 폴링 1: 존 탭(저가 99.4)이되 지정가(≈98.8)엔 안 닿음 → 주문이 쉰다(대기).
    store.upsert_candles([_m1(_FORMING, 99.4, 101.5, 100.0)])
    runner.poll_once()
    assert executor.open_positions == []
    assert journal.fill_stats()[0].pending == 1

    # 폴링 2: 지정가를 관통하는 1분봉 → 체결(이 배치엔 placed가 없다 = 옛 버그의 방아쇠).
    store.upsert_candles([_m1(_FORMING + _M, 96.0, 99.0, 98.0)])
    runner.poll_once()

    assert len(executor.open_positions) == 1
    stats = journal.fill_stats()[0]
    assert stats.filled == 1
    assert (stats.entered, stats.entry_unrecorded) == (1, 0)
    assert journal.orphan_fills() == []


def test_notifier_exception_does_not_lose_disposition(rig: dict[str, object]) -> None:
    """알림기가 예외를 던져도 처분은 남고 러너는 계속 돈다 (WAN-207 예외 주입).

    처분 기록은 알림보다 먼저이고 알림 실패는 삼켜 로그로만 드러내므로, 다운스트림
    예외가 `entry_status`를 NULL로 남기지 않는다.
    """
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]

    class _Boom:
        def note_placed(self) -> None: ...
        def note_expired(self) -> None: ...
        def tick(self, equity: float, *, now_ms: int | None = None) -> None: ...
        def handle_fill(self, fill: object, report: object) -> None:
            raise RuntimeError("텔레그램 다운")

        def handle_exit(
            self, report: object, *, exit_price: float, reason: object, exit_time: int
        ) -> None: ...

    runner._notifier = _Boom()  # type: ignore[assignment]

    store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])
    runner.poll_once()  # 예외가 poll을 죽이지 않는다.

    assert len(executor.open_positions) == 1
    stats = journal.fill_stats()[0]
    assert (stats.entered, stats.entry_unrecorded) == (1, 0)
    assert journal.orphan_fills() == []


def test_enter_exception_still_records_disposition(
    rig: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """진입 집행이 예외로 끊겨도 처분이 NULL로 남지 않는다 (WAN-207 try/finally).

    `filled` + `entry_status IS NULL`은 "쓰기 사이의 유실"의 유일한 서명이라(WAN-194),
    진입 처리가 터지면 최소한 사유를 붙인 `rejected`를 남겨 그 서명을 오염시키지 않는다.
    """
    store: OhlcvStore = rig["store"]  # type: ignore[assignment]
    runner: ZoneLimitPaperRunner = rig["runner"]  # type: ignore[assignment]
    executor: PaperExecutor = rig["executor"]  # type: ignore[assignment]
    journal: OrderJournal = rig["journal"]  # type: ignore[assignment]

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("DB busy")

    monkeypatch.setattr(executor, "enter", _boom)

    store.upsert_candles([_m1(_FORMING + _M, 94.9, 99.0, 95.2)])
    runner.poll_once()

    stats = journal.fill_stats()[0]
    assert stats.filled == 1
    assert stats.entry_unrecorded == 0  # NULL로 남지 않았다.
    assert stats.entry_rejected == 1
    assert journal.orphan_fills() == []
    reason = journal._conn.execute(  # noqa: SLF001 — 폴백 사유가 찍혔는지 보는 회귀 테스트.
        "SELECT entry_reject_reason FROM live_limit_orders WHERE status = 'filled'"
    ).fetchone()[0]
    assert reason and "WAN-207" in reason


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
