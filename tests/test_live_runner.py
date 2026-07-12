"""live.runner 테스트 — 폴링·프라이밍·중복방지·재시작 안전."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from common.telegram import TelegramClient, TelegramResponse
from data.models import Candle
from data.storage import OhlcvStore
from live.notifier import Notifier
from live.runner import SignalRunner, WatermarkStore, build_series
from strategy.confluence import ConfluenceResult, ConfluenceSignal, IndicatorSnapshot, SignalKind
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection, SignalExitReason

SYMBOL = "BTC/USDT:USDT"
TF = "1h"
HOUR_MS = 3_600_000


class _StubStrategy:
    """df와 무관하게 미리 지정한 결과를 돌려주는 전략 스텁.

    `ConfluenceStrategy`의 `run` 인터페이스만 흉내 낸다(런타임 덕타이핑).
    """

    def __init__(self) -> None:
        self.result = ConfluenceResult(params=ConfluenceParams(), entries=[], exits=[])
        self.calls = 0

    def run(self, df: pd.DataFrame, order_block_result: object | None = None) -> ConfluenceResult:
        self.calls += 1
        return self.result


def _order_block() -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=105.0,
        bottom=95.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _entry(time: int, price: float = 100.0) -> ConfluenceSignal:
    return ConfluenceSignal(
        kind=SignalKind.ENTRY,
        direction=OrderBlockDirection.BULLISH,
        time=time,
        price=price,
        confirmed=True,
        rsi=25.0,
        order_block=_order_block(),
        indicators=IndicatorSnapshot(time=time, close=price, rsi=25.0, lines={"ema_20": 110.0}),
    )


def _exit(time: int, price: float = 110.0) -> ConfluenceSignal:
    return ConfluenceSignal(
        kind=SignalKind.EXIT,
        direction=OrderBlockDirection.BULLISH,
        time=time,
        price=price,
        confirmed=True,
        rsi=None,
        order_block=_order_block(),
        indicators=IndicatorSnapshot(time=time, close=price, rsi=None, lines={}),
        exit_reason=SignalExitReason.TAKE_PROFIT,
    )


def _candle(open_time: int, *, closed: bool = True) -> Candle:
    return Candle(SYMBOL, TF, open_time, 100.0, 105.0, 95.0, 100.0, 10.0, closed)


@pytest.fixture
def store() -> Iterator[OhlcvStore]:
    s = OhlcvStore(":memory:")
    try:
        yield s
    finally:
        s.close()


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, url: str, payload: dict[str, object]) -> TelegramResponse:
        self.messages.append(str(payload["text"]))
        return TelegramResponse(ok=True, status_code=200)


def _make_runner(
    store: OhlcvStore, strategy: _StubStrategy, state_path: Path, recorder: _Recorder
) -> SignalRunner:
    notifier = Notifier(TelegramClient("t", "c", transport=recorder))
    return SignalRunner(
        store=store,
        strategy=strategy,  # type: ignore[arg-type]  # 덕타이핑 스텁
        notifier=notifier,
        state=WatermarkStore(state_path),
        series=[(SYMBOL, TF)],
        lookback_bars=1000,
        poll_interval_seconds=0.0,
        sleep=lambda _seconds: None,
    )


def test_priming_suppresses_existing_signals(store: OhlcvStore, tmp_path: Path) -> None:
    """첫 폴링은 과거 신호를 보내지 않고 워터마크만 최신 봉으로 올린다."""
    store.upsert_candles([_candle(0), _candle(HOUR_MS), _candle(2 * HOUR_MS)])
    strategy = _StubStrategy()
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(), entries=[_entry(HOUR_MS)], exits=[]
    )
    recorder = _Recorder()
    state_path = tmp_path / "state.json"
    runner = _make_runner(store, strategy, state_path, recorder)

    emitted = runner.poll_once()
    assert emitted == []  # 프라이밍: 발송 없음
    assert recorder.messages == []
    assert WatermarkStore(state_path).get(SYMBOL, TF) == 2 * HOUR_MS


def test_new_signal_sent_once_and_deduplicated(store: OhlcvStore, tmp_path: Path) -> None:
    """프라이밍 후 새 봉의 신호는 1회만 전송되고, 재폴링에도 재전송되지 않는다."""
    store.upsert_candles([_candle(0), _candle(HOUR_MS)])
    strategy = _StubStrategy()
    recorder = _Recorder()
    state_path = tmp_path / "state.json"
    runner = _make_runner(store, strategy, state_path, recorder)

    # 1) 프라이밍(워터마크=HOUR_MS)
    runner.poll_once()
    assert recorder.messages == []

    # 2) 새 확정봉 + 그 봉의 진입 신호 등장
    store.upsert_candles([_candle(2 * HOUR_MS)])
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(), entries=[_entry(2 * HOUR_MS)], exits=[]
    )
    emitted = runner.poll_once()
    assert len(emitted) == 1
    assert len(recorder.messages) == 1
    assert "진입 신호" in recorder.messages[0]

    # 3) 같은 상태로 재폴링 → 중복 발송 없음
    emitted_again = runner.poll_once()
    assert emitted_again == []
    assert len(recorder.messages) == 1


def test_entry_then_exit_flow_tracks_paper_position(store: OhlcvStore, tmp_path: Path) -> None:
    store.upsert_candles([_candle(0)])
    strategy = _StubStrategy()
    recorder = _Recorder()
    state_path = tmp_path / "state.json"
    runner = _make_runner(store, strategy, state_path, recorder)
    runner.poll_once()  # 프라이밍(워터마크=0)

    # 진입 봉
    store.upsert_candles([_candle(HOUR_MS)])
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(), entries=[_entry(HOUR_MS, price=100.0)], exits=[]
    )
    runner.poll_once()
    assert runner._notifier.book.position(SYMBOL, TF) is not None

    # 청산 봉(익절)
    store.upsert_candles([_candle(2 * HOUR_MS)])
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(),
        entries=[_entry(HOUR_MS, price=100.0)],
        exits=[_exit(2 * HOUR_MS, price=110.0)],
    )
    runner.poll_once()
    assert runner._notifier.book.position(SYMBOL, TF) is None  # 청산됨
    assert len(runner._notifier.book.closed) == 1
    assert runner._notifier.book.closed[0].realized_pct == pytest.approx(10.0)
    assert any("+10.00%" in m for m in recorder.messages)


def test_restart_does_not_resend(store: OhlcvStore, tmp_path: Path) -> None:
    """워터마크 파일이 남아 있어 러너를 새로 만들어도 같은 신호를 다시 보내지 않는다."""
    store.upsert_candles([_candle(0), _candle(HOUR_MS)])
    strategy = _StubStrategy()
    state_path = tmp_path / "state.json"

    recorder1 = _Recorder()
    runner1 = _make_runner(store, strategy, state_path, recorder1)
    runner1.poll_once()  # 프라이밍(워터마크=HOUR_MS)

    store.upsert_candles([_candle(2 * HOUR_MS)])
    strategy.result = ConfluenceResult(
        params=ConfluenceParams(), entries=[_entry(2 * HOUR_MS)], exits=[]
    )
    runner1.poll_once()
    assert len(recorder1.messages) == 1

    # 재시작: 새 러너 + 새 워터마크 스토어(같은 파일)
    recorder2 = _Recorder()
    runner2 = _make_runner(store, strategy, state_path, recorder2)
    emitted = runner2.poll_once()
    assert emitted == []  # 이미 처리된 신호 → 재전송 없음
    assert recorder2.messages == []


def test_run_loops_max_polls_times(store: OhlcvStore, tmp_path: Path) -> None:
    store.upsert_candles([_candle(0)])
    strategy = _StubStrategy()
    recorder = _Recorder()
    sleeps: list[float] = []
    notifier = Notifier(TelegramClient("t", "c", transport=recorder))
    runner = SignalRunner(
        store=store,
        strategy=strategy,  # type: ignore[arg-type]  # 덕타이핑 스텁
        notifier=notifier,
        state=WatermarkStore(tmp_path / "state.json"),
        series=[(SYMBOL, TF)],
        lookback_bars=1000,
        poll_interval_seconds=1.5,
        sleep=sleeps.append,
    )

    runner.run(max_polls=3)
    assert strategy.calls == 3  # 시리즈 1개 × 3회 폴링
    assert sleeps == [1.5, 1.5]  # 폴링 사이 2회만 대기(마지막 뒤엔 없음)


def test_empty_series_when_no_symbols() -> None:
    from config.settings import Settings

    settings = Settings(live_signal_symbols=[], live_signal_timeframes=["1h"])
    assert build_series(settings) == []
