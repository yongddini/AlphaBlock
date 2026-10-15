"""그림자 주문 체결 ≡ 백테스트 팔 후보 체결 — 실데이터 고정 창(WAN-451 ③ 몰림 규칙의 모집단).

몰림 규칙은 「팔 후보 전부의 가상 체결」을 센다. 백테스트는 그 모집단을
`build_zone_limit_candidates`(탭마다 독립 시뮬레이션)로 만들고, 라이브는 엔진의 그림자 주문으로
만든다. 두 모집단이 **같은 체결
시각·체결가·손절가**를 내야 같은 규칙이다 — 실데이터로 그 등식을 건다(라이브는 러너의 재생 경로 =
연속 가동과 같다고 검증된 경로, WAN-306).

⚠️ 알려진 차이 하나: 라이브 탐지 창은 최근 `live_signal_lookback_bars`(1500)봉이라 **그보다 오래된
존**은 모른다(정본 팔 후보의 약 7% — 15m 14% · 1h 12% … 1d 1%). 아래 창은 그런 존이 없는 칸이다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backtest import harness
from backtest.wan169_leverage_book import run_cells
from backtest.wan424_stoch_ob_arm import arm_pool, base_cell_kwargs
from config.settings import Settings
from data.storage import OhlcvStore
from execution.engine import build_execution_engine
from live.executor import PaperExecutor
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from live.zone_limit_runner import ZoneLimitPaperRunner, build_paper_recorder
from paper.store import PaperTradeStore

_REAL_DB = Path("data/ohlcv.db")
_DAY = 86_400_000
#: 못 박은 창 — 2026-09-14 ~ 2026-10-14 UTC(BTC 1h · 탐지 창 밖 존 없음 · 대조 7/7 실측).
_START = 1_789_344_000_000
_END = _START + 30 * _DAY
_SYMBOL, _TF = "BTC/USDT:USDT", "1h"


def _available() -> bool:
    if not _REAL_DB.exists():
        return False
    try:
        conn = sqlite3.connect(_REAL_DB)
        try:
            row = conn.execute(
                "SELECT MAX(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = '1m'",
                (_SYMBOL,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row and row[0] and row[0] >= _END)


@pytest.mark.skipif(not _available(), reason="실데이터(data/ohlcv.db) 없음")
def test_shadow_fills_equal_backtest_arm_candidates(tmp_path: Path) -> None:
    settings = Settings(
        db_path=str(tmp_path / "j.db"),
        confluence={"retap_mode": "once", "fill_penetration_bps": 5.0},
    )
    params = settings.confluence
    paper = PaperTradeStore(tmp_path / "j.db")
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    real = OhlcvStore(_REAL_DB)
    runner = ZoneLimitPaperRunner(
        store=real,
        engine=ZoneLimitLiveEngine(
            params=params, journal=journal, session_id=session, track_shadows=True
        ),
        journal=journal,
        session_id=session,
        executor=PaperExecutor(
            engine=build_execution_engine(settings),
            store=paper,
            recorder=build_paper_recorder(paper, settings),
            sizing=settings.risk_sizing,
        ),
        params=params,
        series=[(_SYMBOL, _TF)],
        lookback_bars=settings.live_signal_lookback_bars,
        poll_interval_seconds=0,
    )
    runner._last_substep[(_SYMBOL, _TF)] = _START - 60_000
    events = runner.poll_series(_SYMBOL, _TF)
    live = sorted(
        (e.fill.time, round(e.fill.price, 8), round(float(e.fill.stop_price or 0), 8))
        for e in events
        if e.kind == "shadow_filled" and e.fill is not None and e.fill.time < _END
    )
    (payload,) = run_cells(
        [_SYMBOL],
        (_TF,),
        start="2026-02-26",  # 창보다 200일 앞 — 백테스트 탐지가 창 안 존을 다 안다
        end="2026-10-14",
        jobs=1,
        cold_segments=False,
        **base_cell_kwargs(),
    )
    backtest = sorted(
        (int(c.entry_time), round(float(c.entry_price), 8), round(float(c.stop_price), 8))
        for c in arm_pool(payload.candidates[harness.SEGMENT_FULL], min_width=0.0)
        if _START <= c.entry_time < _END
    )
    real.close()
    assert len(backtest) >= 5
    assert live == backtest
