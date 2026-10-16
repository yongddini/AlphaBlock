"""재시작 불변 회귀 테스트 (WAN-306 §3).

같은 1분봉 열을 (a) 끊김 없이 폴링한 러너와 (b) 중간에 저장 → 재기동 → 따라잡기 재생한
러너가 **탭 이력·주문 상태·체결·청산까지 동일**함을 동작으로 고정한다(합성 + 실데이터).
2026-08-13 실사례(WAN-305 픽스처 — 재시작 직후 탭이 세션 로컬로 리셋돼 즉시 예약·체결)가
재발하지 않음을 확인하는 것이 목적이다.

동등성 판정의 지문은 세 겹이다:

1. **장부**(`live_limit_orders`) — 예약·체결·만료·폐기 행이 세션 축을 뺀 모든 열에서 같다.
2. **페이퍼 거래**(`paper_trades`) — 진입/청산 가격·시각·사유가 같다.
3. **엔진 스냅샷**(`SeriesStateSnapshot`) — 전이 맥락·형성 봉 누적·대기 주문 스칼라가
   저장 시각(`saved_ms`)을 뺀 모든 필드에서 같다.
4. **신호 장부**(`arm_signals`)와 **진입 처분**(`entry_status`·`entry_reject_code`) — 몰림
   규칙이 세는 체결과 그 판정이 같다(WAN-453 — 그전에는 러너에 신호 장부가 안 붙어 있어 이
   축을 아예 안 거쳤다).

러너는 스토캐스틱 팔 규칙으로 진입한다(WAN-451 — 옛 오더블록 페이퍼의 고정 R 익절·재진입은
없어졌다). 그래서 합성 시나리오의 청산은 익절이 아니라 2배 손절이다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from config.settings import Settings
from data.models import Candle
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live import limit_engine
from live.arm_rules import REJECT_CODE_ARM_CROWD_SKIP, ArmParams
from live.arm_signals import ArmSignalStore
from live.engine_state import EngineStateStore, PersistedPendingOrder, SeriesStateSnapshot
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.limit_orders import PendingLimitOrder
from live.order_journal import OrderJournal
from live.zone_limit_runner import ZoneLimitPaperRunner, expire_stale_pending
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

#: 실데이터 저장소(있으면 실데이터 축도 검증 — CI에서는 파일이 없어 skip).
_REAL_DB = Path("data/ohlcv.db")


def _resting_zone() -> OrderBlock:
    """하락 시딩(130→101)에서 밴드(≈98.8)가 존 안에 앉는 존 — 탭해도 즉시 체결이 아닌
    「예약만」 상태를 만든다(기존 러너 테스트와 같은 좌표)."""
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=99.5,
        bottom=90.0,
        start_time=0,
        confirmed_time=_H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _install_stub_detector(monkeypatch: pytest.MonkeyPatch, zone: OrderBlock) -> None:
    result = OrderBlockResult(order_blocks=[zone], signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)


def _htf_candle(i: int, close: float) -> Candle:
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


class _Rig:
    """러너 한 벌 — `restart()`가 실제 기동 순서(복원 → 만료 재평가 → 폐기)를 재현한다."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        name: str,
        params: ConfluenceParams | None = None,
        catchup_max_ms: int = 48 * 3_600_000,
    ) -> None:
        self.db = tmp_path / f"{name}.db"
        self._params = params if params is not None else ConfluenceParams(max_zone_width_atr=None)
        self._catchup_max_ms = catchup_max_ms
        self.now = {"ms": 0}
        self.store = OhlcvStore(self.db)
        self._open()

    def _open(self) -> None:
        settings = Settings(db_path=str(self.db))
        self.journal = OrderJournal(self.db)
        self.session = self.journal.start_session(now_ms=self.now["ms"])
        self.paper_store = PaperTradeStore(self.db)
        recorder = PaperTradeRecorder(
            self.paper_store, cost_model=settings.costs, funding_store=None
        )
        self.executor = PaperExecutor(
            engine=build_execution_engine(settings),
            store=self.paper_store,
            recorder=recorder,
            sizing=settings.risk_sizing,
        )
        self.engine = ZoneLimitLiveEngine(
            params=self._params, journal=self.journal, session_id=self.session
        )
        self.engine_state = EngineStateStore(self.db)
        # 몰림 규칙의 신호 장부(WAN-453) — 실제 기동(`run_zone_limit_runner`)과 같은 DB 파일에
        # 붙인다. 이게 빠져 있으면 재시작 동등성이 신호 장부·몰림 판정을 아예 안 거친다.
        self.signals = ArmSignalStore(self.db)
        self.runner = ZoneLimitPaperRunner(
            store=self.store,
            engine=self.engine,
            journal=self.journal,
            session_id=self.session,
            executor=self.executor,
            params=self._params,
            series=[(_SYMBOL, _TF)],
            lookback_bars=500,
            poll_interval_seconds=1.0,
            engine_state=self.engine_state,
            catchup_max_ms=self._catchup_max_ms,
            arm=ArmParams(),
            signals=self.signals,
            now_ms=lambda: self.now["ms"],
        )

    def feed(self, candles: list[Candle], *, poll_each: bool = True) -> None:
        """1분봉을 넣고 폴링한다 — `poll_each=True`면 연속 가동(봉마다 폴링)을 재현한다."""
        if poll_each:
            for candle in candles:
                self.store.upsert_candles([candle])
                self.now["ms"] = candle.open_time + _M
                self.runner.poll_once()
        else:
            self.store.upsert_candles(candles)
            if candles:
                self.now["ms"] = candles[-1].open_time + _M
            self.runner.poll_once()

    def restart(self) -> set[int]:
        """실제 기동 순서(`run_zone_limit_runner`)를 재현한다: 새 세션 + 복원 → 만료
        재평가 → 재시작 폐기(복원분 제외)."""
        self.journal.close()
        self.paper_store.close()
        self.engine_state.close()
        self.signals.close()
        self._open()
        restored = self.runner.restore_state(now_ms=self.now["ms"])
        expire_stale_pending(self.journal, now_ms=self.now["ms"], exclude_ids=restored)
        self.journal.discard_stale_pending(now_ms=self.now["ms"], exclude_ids=restored)
        return restored

    def close(self) -> None:
        self.store.close()
        self.journal.close()
        self.paper_store.close()
        self.engine_state.close()
        self.signals.close()

    # -- 동등성 지문 ---------------------------------------------------------

    def journal_rows(self) -> list[tuple[object, ...]]:
        """세션 축을 뺀 장부 행 지문(예약 시각순)."""
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT symbol, timeframe, direction, tap_index, placed_ms, status,"
                " terminal_ms, first_rested_ms, fill_ms, fill_price, stop_price,"
                " take_profit_price, wait_ms, skip_reason, limit_valid_bars"
                " FROM live_limit_orders ORDER BY placed_ms, id"
            ).fetchall()
        finally:
            conn.close()
        return [tuple(r) for r in rows]

    def signal_rows(self) -> list[tuple[object, ...]]:
        """신호 장부 지문(체결 시각순) — 몰림 규칙이 세는 모집단과 세지 않은 사유까지."""
        return [
            (
                s.symbol,
                s.timeframe,
                s.zone_start,
                s.zone_confirmed,
                s.fill_ms,
                s.price,
                s.ob_stop,
                s.counted,
                s.reason_code,
            )
            for s in self.signals.rows(0, 2**62)
        ]

    def entry_decisions(self) -> list[tuple[object, ...]]:
        """체결된 주문의 진입 처분(진입/거부 · 사유 코드) — 몰림 판정이 재시작을 넘어 같은가."""
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT fill_ms, entry_status, COALESCE(entry_reject_code, '')"
                " FROM live_limit_orders WHERE status = 'filled' ORDER BY fill_ms, id"
            ).fetchall()
        finally:
            conn.close()
        return [tuple(r) for r in rows]

    def trade_rows(self) -> list[tuple[object, ...]]:
        records = self.paper_store.list_records(_SYMBOL, _TF)
        return [
            (r.entry_time, r.entry_price, r.exit_time, r.exit_price, r.reason.value)
            for r in records
        ]

    def snapshot_fingerprint(self) -> dict[str, object] | None:
        """엔진 상태 지문 — 저장 시각을 뺀 스냅샷 전체(전이 맥락·주문 스칼라)."""
        last_htf = self.runner._last_htf.get((_SYMBOL, _TF))  # noqa: SLF001
        if last_htf is None:
            return None
        snap = self.engine.snapshot_series(_SYMBOL, _TF, last_htf_time=last_htf, saved_ms=0)
        if snap is None:
            return None
        dump = snap.model_dump()
        dump.pop("saved_ms")
        return dump


def _assert_equivalent(continuous: _Rig, restarted: _Rig) -> None:
    assert restarted.journal_rows() == continuous.journal_rows()
    assert restarted.trade_rows() == continuous.trade_rows()
    assert restarted.snapshot_fingerprint() == continuous.snapshot_fingerprint()
    assert restarted.signal_rows() == continuous.signal_rows()
    assert restarted.entry_decisions() == continuous.entry_decisions()


def _seed_htf(rig: _Rig, closes: list[float]) -> None:
    rig.store.upsert_candles([_htf_candle(i, close=closes[i]) for i in range(len(closes))])


_DESC_CLOSES = [130.0 - i for i in range(_N_CLOSED)]


def _pending(rig: _Rig) -> PendingLimitOrder | None:
    """걸린 주문 하나(없으면 None) — 이 파일의 시나리오는 존이 하나라 주문도 많아야 하나다."""
    orders = rig.engine.open_orders()
    assert len(orders) <= 1
    return orders[0] if orders else None


# -- 합성: 대기 주문·전이 맥락·체결이 재시작을 넘어 이어진다 -------------------


def test_restart_midbar_with_resting_order_matches_continuous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """형성 중 봉 한가운데서 재시작해도 연속 실행과 같은 장부·거래·엔진 상태가 나온다.

    이것이 08-13 픽스처의 부류다: 옛 정책은 재시작이 대기 주문을 폐기하고 전이 맥락을
    잃어 같은 존을 새 탭(#0)으로 다시 예약·즉시 체결했다 — 복원 + 따라잡기는 그 주문을
    그대로 이어받아 연속 실행과 같은 자리에서 체결한다.
    """
    _install_stub_detector(monkeypatch, _resting_zone())
    bars = [
        _m1(_FORMING, 99.4, 101.5, 100.0),  # 탭 — 예약만(지정가 ≈98.8에는 안 닿음).
        _m1(_FORMING + _M, 99.6, 101.0, 100.5),  # 존 밖 — 대기 유지.
        _m1(_FORMING + 2 * _M, 96.0, 99.0, 98.0),  # 지정가 관통 — 체결.
        _m1(_FORMING + 3 * _M, 80.0, 99.0, 81.0),  # 2배 손절(≈81) 관통 — 청산.
    ]

    a = _Rig(tmp_path, name="continuous")
    _seed_htf(a, _DESC_CLOSES)
    a.feed(bars)

    b = _Rig(tmp_path, name="restarted")
    _seed_htf(b, _DESC_CLOSES)
    b.feed(bars[:2])
    restored = b.restart()
    assert len(restored) == 1  # 대기 주문이 복원됐다(폐기가 아니라).
    b.feed(bars[2:])

    _assert_equivalent(a, b)
    # 폐기 행이 아예 없다 — 정상 재시작의 기본 경로가 아니다(완료 기준 4).
    assert all(row[5] != "discarded_restart" for row in b.journal_rows())
    # 실제로 체결·거래가 났다(공허한 동등성이 아니다).
    assert any(row[5] == "filled" for row in b.journal_rows())
    assert len(b.trade_rows()) == 1
    a.close()
    b.close()


def test_catchup_replays_gap_bars_and_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """죽어 있던 구간에 도착한 1분봉(체결·청산 포함)을 따라잡기 재생이 페이퍼로 집행한다.

    재시작 전에 걸린 주문이 공백 구간의 봉에서 체결·청산까지 끝나는 시나리오 — 복원된
    주문이 실제로 걸려 있었으므로 그 구간 체결은 뒷북이 아니라 따라잡기다(완료 기준 3).
    """
    _install_stub_detector(monkeypatch, _resting_zone())
    bars = [
        _m1(_FORMING, 99.4, 101.5, 100.0),  # 탭 — 예약만.
        _m1(_FORMING + _M, 96.0, 99.0, 98.0),  # (공백 중) 체결.
        _m1(_FORMING + 2 * _M, 80.0, 99.0, 81.0),  # (공백 중) 2배 손절(≈81) 청산.
        _m1(_FORMING + 3 * _M, 100.0, 101.0, 100.5),  # 재기동 후 정상 폴링.
    ]

    a = _Rig(tmp_path, name="continuous")
    _seed_htf(a, _DESC_CLOSES)
    a.feed(bars)

    b = _Rig(tmp_path, name="restarted")
    _seed_htf(b, _DESC_CLOSES)
    b.feed(bars[:1])
    # 러너가 죽어 있는 동안 수집기는 계속 쌓는다.
    b.store.upsert_candles(bars[1:3])
    b.now["ms"] = bars[2].open_time + _M
    restored = b.restart()
    assert len(restored) == 1
    b.feed(bars[3:])

    _assert_equivalent(a, b)
    assert len(b.trade_rows()) == 1  # 공백 구간의 체결·청산이 집행됐다.
    a.close()
    b.close()


# -- 몰림 규칙: 재시작 전에 남은 신호를 재시작 뒤 판정이 센다 (WAN-453) ---------


def _second_zone() -> OrderBlock:
    """존 A(`_resting_zone`)의 2배 손절(≈82.5)보다 아래에 놓인 강세 존 B — A가 손절된 뒤에야
    탭된다(같은 1분 안에서 A 손절과 B 체결이 겹치지 않는다)."""
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=80.0,
        bottom=70.0,
        start_time=2 * _H,
        confirmed_time=3 * _H,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _install_two_zone_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    result = OrderBlockResult(order_blocks=[_resting_zone(), _second_zone()], signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)


#: 존 A 탭 → A 체결(센 신호) → A 2배 손절 → 존 B 탭·체결 → 대기. B의 체결은 직전 24h에 「다른
#: 신호」(A) 1개를 보므로 몰림 규칙(1~10개 = 급락 초반 건너뛰기)이 진입을 거부한다.
_CROWD_BARS = [
    _m1(_FORMING, 99.4, 101.5, 100.0),  # 존 A 탭 — 예약만.
    _m1(_FORMING + _M, 96.0, 99.0, 98.0),  # A 체결 — 신호 0개라 진입, 신호 장부에 1행(셈).
    _m1(_FORMING + 2 * _M, 81.0, 98.0, 81.5),  # A 2배 손절(≈82.5) — 칸이 빈다.
    _m1(_FORMING + 3 * _M, 78.0, 82.0, 79.0),  # 존 B 탭·체결 — 직전 24h 신호 1개 → 건너뜀.
    _m1(_FORMING + 4 * _M, 78.5, 80.5, 79.5),  # 그 뒤 정상 폴링.
]


def _assert_crowd_outcome(rig: _Rig) -> None:
    """공허한 동등성 방지 — 신호 둘(둘 다 셈)이 남고 B 진입이 몰림 규칙으로 거부됐다."""
    signals = rig.signal_rows()
    assert len(signals) == 2 and all(row[7] for row in signals)
    decisions = rig.entry_decisions()
    assert [d[1:] for d in decisions] == [
        ("entered", ""),
        ("rejected", REJECT_CODE_ARM_CROWD_SKIP),
    ]
    assert len(rig.trade_rows()) == 1


@pytest.mark.parametrize("restart_after", [1, 2, 3, 4])
def test_crowd_signal_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restart_after: int
) -> None:
    """재시작 불변을 몰림 규칙까지 넓힌다 — 어느 봉 뒤에서 재시작해도 신호 장부·진입 처분이
    연속 실행과 같다.

    재시작 전에 체결된 A의 신호는 장부(같은 DB 파일)에 남아 재시작 뒤 B의 판정에서 세어진다.
    러너가 신호를 메모리에만 들고 있었다면 재시작 판에서 B가 「신호 0개」로 진입했을 것이다.
    """
    _install_two_zone_detector(monkeypatch)

    a = _Rig(tmp_path, name="continuous")
    _seed_htf(a, _DESC_CLOSES)
    a.feed(_CROWD_BARS)
    _assert_crowd_outcome(a)

    b = _Rig(tmp_path, name="restarted")
    _seed_htf(b, _DESC_CLOSES)
    b.feed(_CROWD_BARS[:restart_after])
    b.restart()
    b.feed(_CROWD_BARS[restart_after:])

    _assert_equivalent(a, b)
    _assert_crowd_outcome(b)
    a.close()
    b.close()


def test_crowd_signal_from_catchup_gap_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """죽어 있던 구간의 체결도 따라잡기 재생이 신호 장부에 남기고, 같은 재생 안에서 뒤이은
    체결의 몰림 판정이 그 신호를 센다 — 그리고 한 체결이 두 번 기록되지 않는다(기본키)."""
    _install_two_zone_detector(monkeypatch)

    a = _Rig(tmp_path, name="continuous")
    _seed_htf(a, _DESC_CLOSES)
    a.feed(_CROWD_BARS)

    b = _Rig(tmp_path, name="restarted")
    _seed_htf(b, _DESC_CLOSES)
    b.feed(_CROWD_BARS[:1])  # A 탭 — 예약만 한 채로 죽는다.
    # 러너가 죽어 있는 동안 수집기는 계속 쌓는다(A 체결 · A 손절 · B 체결이 전부 공백 구간).
    b.store.upsert_candles(_CROWD_BARS[1:4])
    b.now["ms"] = _CROWD_BARS[3].open_time + _M
    restored = b.restart()
    assert len(restored) == 1  # A의 대기 주문이 복원됐다.
    b.feed(_CROWD_BARS[4:])

    _assert_equivalent(a, b)
    _assert_crowd_outcome(b)
    a.close()
    b.close()


def test_expiry_clock_survives_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """만료 시계(`limit_valid_bars`)가 재시작을 넘어 이어진다 — #0부터 다시 세지 않는다.

    유효 2봉 주문이 재시작 전 1봉을 소모했으면, 재기동 후 다음 경계에서 만료된다
    (연속 실행과 같은 봉). 옛 정책이면 폐기됐거나, 시계가 리셋됐다면 2봉을 더 살았을
    것이다(완료 기준 2).
    """
    _install_stub_detector(monkeypatch, _resting_zone())
    params = ConfluenceParams(max_zone_width_atr=None, limit_valid_bars=2)
    bars = [
        _m1(_FORMING, 99.4, 101.5, 100.0),  # 탭 — 예약만.
        _m1(_FORMING + _H, 100.0, 101.0, 100.5),  # 경계 1 — bars_elapsed=1.
        _m1(_FORMING + 2 * _H, 100.0, 101.0, 100.5),  # 경계 2 — 만료.
    ]

    a = _Rig(tmp_path, name="continuous", params=params)
    _seed_htf(a, _DESC_CLOSES)
    a.feed(bars)

    b = _Rig(tmp_path, name="restarted", params=params)
    _seed_htf(b, _DESC_CLOSES)
    b.feed(bars[:2])
    restored = b.restart()
    assert len(restored) == 1
    b.feed(bars[2:])

    _assert_equivalent(a, b)
    rows = b.journal_rows()
    assert len(rows) == 1 and rows[0][5] == "cancelled_expired"
    a.close()
    b.close()


def test_gap_over_limit_resets_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """따라잡기 한도를 넘긴 공백은 명시적으로 초기화한다 — 대기 주문은 재시작 폐기로
    남고(이 경로가 이제 `discarded_restart`의 존재 이유다, 완료 기준 4) 스냅샷은 지운다."""
    _install_stub_detector(monkeypatch, _resting_zone())
    b = _Rig(tmp_path, name="reset", catchup_max_ms=2 * _H)
    _seed_htf(b, _DESC_CLOSES)
    b.feed([_m1(_FORMING, 99.4, 101.5, 100.0)])  # 탭 — 예약만.
    assert _pending(b) is not None

    b.now["ms"] = _FORMING + 3 * _H  # 한도(2h) 초과 공백.
    restored = b.restart()
    assert restored == set()
    assert _pending(b) is None
    assert b.engine_state.load(_SYMBOL, _TF) is None  # 스냅샷 명시 삭제.
    # 기한(24봉) 전이므로 만료가 아니라 재시작 폐기로 남는다.
    statuses = [row[5] for row in b.journal_rows()]
    assert statuses == ["discarded_restart"]
    b.close()


def test_snapshot_pending_already_terminal_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """스냅샷과 장부가 어긋나면(저장·쓰기 사이 크래시) 장부가 정본이다 — 이미 종결된
    주문은 되걸지 않는다."""
    _install_stub_detector(monkeypatch, _resting_zone())
    b = _Rig(tmp_path, name="reconcile")
    _seed_htf(b, _DESC_CLOSES)
    b.feed([_m1(_FORMING, 99.4, 101.5, 100.0)])
    order = _pending(b)
    assert order is not None and order.journal_id is not None
    # 스냅샷 저장 후, 장부만 앞서 나간 상황을 주입(취소 종결).
    from live.limit_orders import LimitOrderStatus

    b.journal.record_cancelled(
        order.journal_id, LimitOrderStatus.CANCELLED_INVALIDATED, now_ms=_FORMING + _M
    )
    b.now["ms"] = _FORMING + 2 * _M
    restored = b.restart()
    assert restored == set()
    assert _pending(b) is None  # 되걸지 않았다.
    b.close()


def test_engine_state_store_roundtrip(tmp_path: Path) -> None:
    """스냅샷 저장/복원/삭제 왕복 + 손상 JSON은 조용히 무시(None)."""
    store = EngineStateStore(tmp_path / "state.db")
    snap = SeriesStateSnapshot(
        saved_ms=1,
        last_substep_time=2,
        last_htf_time=3,
        forming_bar=4,
        forming_low=1.0,
        forming_high=2.0,
        running_close=1.5,
        zones=[],
        shadows=[
            PersistedPendingOrder(
                journal_id=7,
                zone=("bullish", 0, 1),
                tap_index=0,
                placed_ms=4,
                limit_valid_bars=None,
                bars_elapsed=1,
                first_rested_ms=None,
                last_limit_price=None,
            )
        ],
    )
    store.save(_SYMBOL, _TF, snap)
    loaded = store.load(_SYMBOL, _TF)
    assert loaded == snap
    assert loaded is not None and loaded.shadows[0].zone == ("bullish", 0, 1)
    assert loaded.shadows[0].limit_valid_bars is None  # 무기한은 null로 왕복한다
    store.delete(_SYMBOL, _TF)
    assert store.load(_SYMBOL, _TF) is None

    conn = sqlite3.connect(tmp_path / "state.db")
    with conn:
        conn.execute(
            "INSERT INTO live_engine_state (symbol, timeframe, saved_ms, state_json)"
            " VALUES (?, ?, 0, 'not-json')",
            (_SYMBOL, _TF),
        )
    conn.close()
    assert store.load(_SYMBOL, _TF) is None
    store.close()


# -- 실데이터: 진짜 탐지기·진짜 봉으로 같은 동등성 -----------------------------


def _real_data_available() -> bool:
    if not _REAL_DB.exists():
        return False
    try:
        conn = sqlite3.connect(_REAL_DB)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM ohlcv WHERE symbol = ? AND timeframe = '1m'"
                " AND open_time >= ? AND open_time < ?",
                (_SYMBOL, _REAL_START, _REAL_END),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row and row[0] > 600)


#: 못 박은 실데이터 창 — 창을 고정해야 재현된다(`--years N` 미끄러짐 교훈).
_REAL_START = 1_784_505_600_000  # 2026-07-20 00:00:00 UTC
_REAL_END = _REAL_START + 30 * _H
_REAL_SEED_START = _REAL_START - 200 * _H  # 상위TF 시딩(밴드 SMA20·RSI 워밍업 여유).


@pytest.mark.skipif(not _real_data_available(), reason="실데이터(data/ohlcv.db) 없음")
def test_restart_matches_continuous_on_real_data(tmp_path: Path) -> None:
    """실데이터(BTC 1h + 1m, 못 박은 30h 창) + 진짜 탐지기에서도 재시작 ≡ 연속 실행.

    합성 테스트가 고정한 동등성(장부·거래·엔진 스냅샷)을 실제 시장 봉·실제 오더블록
    탐지 위에서 재확인한다 — 스텁이 가려 줄 수 있는 창 공급 순서·시딩 재구성의 어긋남을
    실데이터가 드러낸다.
    """
    real = OhlcvStore(_REAL_DB)
    try:
        htf = real.load(_SYMBOL, _TF, start_ms=_REAL_SEED_START, end_ms=_REAL_END)
        m1 = real.load(_SYMBOL, "1m", start_ms=_REAL_START, end_ms=_REAL_END)
    finally:
        real.close()
    htf = htf[htf["closed"].astype(bool)]
    m1 = m1[m1["closed"].astype(bool)]
    if htf.empty or m1.empty:
        pytest.skip("창에 실데이터 없음")

    def _candles(df: pd.DataFrame, timeframe: str) -> list[Candle]:
        return [
            Candle(
                symbol=_SYMBOL,
                timeframe=timeframe,
                open_time=int(r.open_time),
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=float(r.volume),
                closed=True,
            )
            for r in df.itertuples(index=False)
        ]

    htf_candles = _candles(htf, _TF)
    m1_candles = _candles(m1, "1m")
    params = ConfluenceParams()  # 채택 기본값 그대로(진입 판정은 팔 규칙).

    def _upsert_until(rig: _Rig, cutoff_close_ms: int, already: set[int]) -> None:
        """`cutoff_close_ms`까지 닫힌 상위TF 봉을 저장소에 싣는다 — 수집기가 봉이 닫힐
        때마다 쓰는 것을 재현한다(미래 봉을 미리 실으면 프라이밍·창 공급이 어긋난다)."""
        fresh = [
            c
            for c in htf_candles
            if c.open_time + _H <= cutoff_close_ms and c.open_time not in already
        ]
        if fresh:
            rig.store.upsert_candles(fresh)
            already.update(c.open_time for c in fresh)

    def _run(name: str, restart_at: int | None) -> _Rig:
        rig = _Rig(tmp_path, name=name, params=params)
        seeded: set[int] = set()
        # 시딩: 1m 창 시작 전에 닫힌 상위TF 봉만 실은 채 첫 1m 봉을 소비한다 → 프라이밍
        # 커서(since = 마지막 확정봉 닫힘 - 1분)가 1m 창 첫 봉에 앵커된다. 창 내 상위TF
        # 봉을 먼저 실으면 프라이밍이 창 끝으로 점프해 아무것도 소비하지 않는다.
        _upsert_until(rig, _REAL_START, seeded)
        rig.feed(m1_candles[:1], poll_each=False)
        rest = m1_candles[1:]
        halves = [rest] if restart_at is None else [rest[: restart_at - 1], rest[restart_at - 1 :]]
        for i, half in enumerate(halves):
            if i > 0:
                rig.restart()
            _upsert_until(rig, half[-1].open_time + _M, seeded)
            rig.feed(half, poll_each=False)
        return rig

    a = _run("real-continuous", restart_at=None)
    b = _run("real-restarted", restart_at=len(m1_candles) // 2)
    try:
        _assert_equivalent(a, b)
        # 공허한 동등성 방지 — 이 못 박은 창은 실제로 예약·체결을 낸다(진짜 탐지기 기준).
        assert a.snapshot_fingerprint() is not None
        assert len(a.journal_rows()) >= 1
        assert any(row[5] == "filled" for row in a.journal_rows())
        assert len(a.signal_rows()) >= 1  # 체결이 신호 장부에도 남았다(WAN-453).
    finally:
        a.close()
        b.close()
