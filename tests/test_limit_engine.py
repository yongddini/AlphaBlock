"""존-지정가 라이브 엔진 테스트 (WAN-45).

핵심 검증 두 갈래:

1. **백테스트와 같은 부품·같은 판정** — 지정가 재산정 공급자가 백테스트와 **동일
   객체**(`IntrabarLiveLimit`)임을 `is`로 고정하고, 같은 1분봉을 넣은
   `simulate_zone_limit_trade`와 체결 시각·가격이 일치함을 동작으로 고정한다
   (완료 기준 "동일한 터치 판정 함수 공유 — 로직 이중화 금지").
2. **주문 생애 3+1 경로** — 체결 / 만료 / 무효화 / 조건 미충족(옵트인 게이트),
   그리고 데이터 공백 폐기까지 각각 이벤트·장부 상태로 확인한다.

탐지기는 스텁으로 갈아끼운다 — 이 테스트의 관심사는 존 탐지가 아니라 "탐지된 존을
라이브에서 백테스트와 같은 규칙으로 매매하는가"다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import backtest.zone_limit_backtest as zlb
import strategy.confluence as confluence
import strategy.realtime_band as realtime_band
from backtest.substep import SubStep, simulate_zone_limit_trade
from live import limit_engine
from live.limit_engine import ZoneLimitLiveEngine
from live.order_journal import OrderJournal
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
)
from strategy.realtime_band import RealtimeBand
from strategy.realtime_rsi import RealtimeRsi

_H = 3_600_000  # 1h(ms)
_M = 60_000  # 1m(ms)
_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"

# 확정 상위TF 봉 30개(종가 100 고정) — SMA20 워밍업(19봉)을 넉넉히 채운다.
_N_CLOSED = 30
_FORMING = _N_CLOSED * _H  # 형성 중 봉의 open_time.


def _htf_df(n: int = _N_CLOSED, closes: list[float] | None = None) -> pd.DataFrame:
    times = [i * _H for i in range(n)]
    close_vals = closes if closes is not None else [100.0] * n
    assert len(close_vals) == n
    return pd.DataFrame(
        {
            "open_time": times,
            "open": close_vals,
            "high": [c + 0.5 for c in close_vals],
            "low": [c - 0.5 for c in close_vals],
            "close": close_vals,
            "volume": [1.0] * n,
            "closed": [True] * n,
        }
    )


#: 하락 추세 시딩(130→101): σ가 커져 볼린저 하단이 존 **안**에 앉는다 — "예약은 되지만
#: 즉시 체결은 아닌" 시나리오용. (역사 종가가 100 고정이면 밴드가 항상 현재가 위에 있어
#: 예약 = 즉시 터치가 돼 만료·무효화 경로를 검증할 수 없다.)
_DESC_CLOSES = [130.0 - i for i in range(_N_CLOSED)]  # 마지막 확정 종가 101.
_DESC_ZONE_TOP = 99.5
_DESC_ZONE_BOTTOM = 90.0


def _zone(top: float = 95.0, bottom: float = 90.0) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=_H,  # 두 번째 확정봉에서 확정 — 형성 중 봉보다 훨씬 이전.
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


def _params(**kw: object) -> ConfluenceParams:
    # 존폭 필터는 기본 켜짐(1.28, WAN-159)인데 이 합성 데이터의 ATR(≈1.0)로는 폭 5짜리
    # 존이 걸러진다 — 필터 자체를 검증하는 테스트 말고는 명시적으로 끈다.
    kw.setdefault("max_zone_width_atr", None)
    return ConfluenceParams(**kw)


def _install_stub_detector(monkeypatch: pytest.MonkeyPatch, order_blocks: list[OrderBlock]) -> None:
    result = OrderBlockResult(order_blocks=order_blocks, signals=[])

    class _Stub:
        def __init__(self, params: object = None) -> None:
            pass

        def run(self, df: pd.DataFrame) -> OrderBlockResult:
            return result

    monkeypatch.setattr(limit_engine, "OrderBlockDetector", _Stub)


def _engine(
    monkeypatch: pytest.MonkeyPatch,
    zones: list[OrderBlock],
    *,
    params: ConfluenceParams | None = None,
    journal: OrderJournal | None = None,
    session_id: int | None = None,
    has_position: object = None,
    closes: list[float] | None = None,
) -> ZoneLimitLiveEngine:
    _install_stub_detector(monkeypatch, zones)
    engine = ZoneLimitLiveEngine(
        params=params if params is not None else _params(),
        journal=journal,
        session_id=session_id,
        has_position=has_position,  # type: ignore[arg-type]
    )
    engine.on_htf_bars(_SYMBOL, _TF, _htf_df(closes=closes))
    return engine


def _rest_without_touch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    params: ConfluenceParams | None = None,
    journal: OrderJournal | None = None,
    session_id: int | None = None,
) -> ZoneLimitLiveEngine:
    """하락 시딩으로 "예약됐지만 아직 터치는 아님" 상태의 엔진을 만든다.

    밴드(≈98.8)가 존(90~99.5) 안에 앉아 지정가 ≈ 98.80 — 저가 99.4로 존은 탭하되
    지정가에는 닿지 않는다.
    """
    zone = _zone(top=_DESC_ZONE_TOP, bottom=_DESC_ZONE_BOTTOM)
    engine = _engine(
        monkeypatch,
        [zone],
        params=params,
        journal=journal,
        session_id=session_id,
        closes=_DESC_CLOSES,
    )
    events = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=99.4, high=101.5, close=100.0)
    assert [e.kind for e in events] == ["placed"]
    assert engine.book.pending(_SYMBOL, _TF) is not None
    return engine


def test_backtest_and_live_share_the_same_price_chain_objects() -> None:
    """완료 기준: 백테스트와 라이브가 같은 함수를 공유한다(로직 이중화 금지).

    별칭이 아니라 사본이 되는 순간 두 경로가 갈라질 수 있으므로 `is`로 고정한다.
    """
    assert limit_engine.IntrabarLiveLimit is zlb._IntrabarLiveLimit
    assert limit_engine.RealtimeBand is realtime_band.RealtimeBand
    assert limit_engine.RealtimeRsi is RealtimeRsi
    assert limit_engine.fixed_r_take_profit_price is confluence.fixed_r_take_profit_price


def test_tap_places_order_and_touch_fills_with_band_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """탭(바깥→안 전이) → 예약, 터치 → 체결. 지정가는 밴드→오프셋 사슬이 정한다."""
    engine = _engine(monkeypatch, [_zone()])

    # 존 밖(저가 96 > 존 상단 95): 예약 없음.
    events = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=96.0, high=100.0, close=99.0)
    assert events == []
    assert engine.book.pending(_SYMBOL, _TF) is None

    # 존 진입(저가 94.9): 같은 서브스텝에서 예약 + 체결(백테스트도 탭 봉 안에서 체결한다).
    events = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING + _M, low=94.9, high=99.0, close=95.2)
    kinds = [e.kind for e in events]
    assert kinds == ["placed", "filled"]
    fill = events[1].fill
    assert fill is not None
    # 밴드(≈97.7)가 존 상단(95)보다 위 → 규칙 1: 근단 진입 → 오프셋 2bp(WAN-112).
    assert fill.price == pytest.approx(95.0 * 1.0002)
    assert fill.stop_price == 90.0
    # 고정 1.5R 익절(WAN-81/90): 진입가 + 1.5 × (진입가 − 손절가).
    expected_tp = fill.price + 1.5 * (fill.price - 90.0)
    assert fill.take_profit_price == pytest.approx(expected_tp)
    assert engine.book.pending(_SYMBOL, _TF) is None


def test_fill_parity_with_backtest_simulator(monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 1분봉을 넣으면 백테스트 시뮬레이터와 체결 시각·가격이 일치한다.

    라이브 엔진과 `simulate_zone_limit_trade`가 **같은 공급자·같은 상태 머신**을 쓰는지의
    동작 검증이다 — 여기가 갈라지면 체결률 실측을 백테스트와 나란히 놓을 수 없다.
    """
    params = _params()
    ob = _zone()
    steps = [
        (_FORMING, 96.0, 100.0, 99.0),
        (_FORMING + _M, 94.9, 99.0, 95.2),
        (_FORMING + 2 * _M, 94.0, 95.0, 94.5),
    ]

    # 라이브 엔진.
    engine = _engine(monkeypatch, [ob], params=params)
    live_fill = None
    for t, low, high, close in steps:
        for event in engine.on_substep(_SYMBOL, _TF, time_ms=t, low=low, high=high, close=close):
            if event.kind == "filled":
                live_fill = event.fill
    assert live_fill is not None

    # 백테스트 시뮬레이터 — 같은 확정봉 시딩, 같은 서브스텝.
    closes = [100.0] * _N_CLOSED
    deviation = params.deviation_filter
    assert deviation is not None
    provider = zlb.IntrabarLiveLimit(
        band=RealtimeBand.seed_from_closed(closes, deviation),
        order_block=ob,
        is_long=True,
        params=params,
        stop_price=ob.bottom,
        lines=[],
        trigger_time=_FORMING,
    )
    outcome = simulate_zone_limit_trade(
        direction=ob.direction,
        live_limit=provider,
        stop_price=ob.bottom,
        substeps=[
            SubStep(time=t, high=h, low=lo, close=c, htf_bar_time=(t // _H) * _H)
            for t, lo, h, c in steps
        ],
        rsi_state=RealtimeRsi.seed_from_closed(closes, params.rsi_length),
        rsi_oversold=params.rsi_oversold,
        rsi_overbought=params.rsi_overbought,
        limit_valid_bars=params.limit_valid_bars,
        rsi_gate_mode=params.rsi_gate_mode,
    )
    assert outcome.filled
    assert outcome.entry_price == pytest.approx(live_fill.price)
    assert outcome.entry_time == live_fill.time


def test_expiry_after_limit_valid_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _rest_without_touch(monkeypatch, params=_params(limit_valid_bars=2))
    # 상위TF 봉 경계를 2번 넘기면 만료(WAN-73 `limit_valid_bars`). 가격은 존 밖(저가
    # 100.2 > 존 상단 99.5)에 머물러 터치도 재탭도 없다.
    ev1 = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING + _H, low=100.2, high=101.0, close=100.5)
    assert ev1 == []
    ev2 = engine.on_substep(
        _SYMBOL, _TF, time_ms=_FORMING + 2 * _H, low=100.2, high=101.0, close=100.5
    )
    assert [e.kind for e in ev2] == ["cancelled_expired"]
    assert engine.book.pending(_SYMBOL, _TF) is None


def test_invalidated_zone_cancels_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _rest_without_touch(monkeypatch)

    # 다음 확정봉 창에서 존이 무효화(breaker)로 나타난다 → 대기 주문 취소.
    broken = _zone(top=_DESC_ZONE_TOP, bottom=_DESC_ZONE_BOTTOM).model_copy(
        update={"breaker": True, "break_time": _FORMING}
    )
    _install_stub_detector(monkeypatch, [broken])
    events = engine.on_htf_bars(_SYMBOL, _TF, _htf_df(_N_CLOSED + 1, closes=[*_DESC_CLOSES, 100.0]))
    assert [e.kind for e in events] == ["cancelled_invalidated"]
    assert engine.book.pending(_SYMBOL, _TF) is None


def test_optin_gate_condition_fail_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """옵트인 게이트(`extreme` + `cancel_limit_on_condition_fail`)의 조건 미충족 취소.

    기본값(`unconditional`)에는 이 경로가 **없다** — WAN-123이 게이트를 뺐고, 옵트인일
    때만 돈다(이슈 배너 4번의 지시).
    """
    params = _params(rsi_gate_mode="extreme", cancel_limit_on_condition_fail=True)
    # 상승 추세 시딩(71→100) → 실시간 RSI가 높아 롱 극단(≤30) 미충족. 밴드(≈80.4)가
    # 존(70~81) 안에 앉고, 급락 봉(저가 79)이 탭과 동시에 지정가를 관통한다.
    asc = [71.0 + i for i in range(_N_CLOSED)]
    engine = _engine(monkeypatch, [_zone(top=81.0, bottom=70.0)], params=params, closes=asc)
    events = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=79.0, high=100.5, close=95.0)
    kinds = [e.kind for e in events]
    assert kinds == ["placed", "cancelled_condition_failed"]
    assert engine.book.pending(_SYMBOL, _TF) is None


def test_zone_width_filter_blocks_wide_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """존폭 필터(WAN-159 채택 기본값 1.28)가 라이브 예약 경로에도 걸린다."""
    wide = _zone(top=95.0, bottom=90.0)  # 폭 5 vs ATR≈1 → 5.0 > 1.28 기각.
    engine = _engine(monkeypatch, [wide], params=ConfluenceParams())
    events = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=94.9, high=99.0, close=95.2)
    assert events == []
    assert engine.book.pending(_SYMBOL, _TF) is None


def test_no_retap_arming_while_price_stays_inside(monkeypatch: pytest.MonkeyPatch) -> None:
    """탭은 바깥→안 전이만 센다 — 존 안에 머무는 연속 봉은 새 예약을 만들지 않는다."""
    engine = _rest_without_touch(monkeypatch)
    order = engine.book.pending(_SYMBOL, _TF)
    assert order is not None
    # 다음 봉에서 만료시켜 슬롯을 비운다. 탭 봉(저가 99.4)이 존 안이었으므로, 다음 봉이
    # 계속 존 안(저가 99.3)이어도 전이가 아니다 — 재예약 없이 조용해야 한다.
    order.limit_valid_bars = 1
    ev = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING + _H, low=99.3, high=100.5, close=100.0)
    assert [e.kind for e in ev] == ["cancelled_expired"]
    ev2 = engine.on_substep(
        _SYMBOL, _TF, time_ms=_FORMING + _H + _M, low=99.3, high=100.0, close=99.8
    )
    assert ev2 == []
    assert engine.book.pending(_SYMBOL, _TF) is None


def test_slot_busy_blocks_new_arming(monkeypatch: pytest.MonkeyPatch) -> None:
    """단일 포지션 규칙: 오픈 포지션이 있으면 새 주문을 걸지 않는다."""
    engine = _engine(monkeypatch, [_zone()], has_position=lambda s, t: True)
    events = engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING, low=94.9, high=99.0, close=95.2)
    assert events == []


def test_htf_gap_discards_pending(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """1분봉이 상위TF 봉을 통째로 건너뛰면 대기 주문을 폐기한다(측정 오염 방지)."""
    journal = OrderJournal(tmp_path / "journal.db")
    session = journal.start_session(now_ms=0)
    engine = _rest_without_touch(monkeypatch, journal=journal, session_id=session)
    # 2시간 건너뛴 서브스텝(상위TF 봉 하나 통째 누락) → 폐기.
    events = engine.on_substep(
        _SYMBOL, _TF, time_ms=_FORMING + 3 * _H, low=100.2, high=101.0, close=100.5
    )
    assert [e.kind for e in events] == ["discarded"]
    stats = journal.fill_stats()
    assert len(stats) == 1
    assert stats[0].discarded_restart == 1
    assert stats[0].placed == 0  # 폐기 건은 유효 표본이 아니다.
    journal.close()


def test_journal_records_full_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "journal.db")
    session = journal.start_session(now_ms=0)
    engine = _engine(monkeypatch, [_zone()], journal=journal, session_id=session)
    engine.on_substep(_SYMBOL, _TF, time_ms=_FORMING + _M, low=94.9, high=99.0, close=95.2)
    stats = journal.fill_stats()
    assert len(stats) == 1
    s = stats[0]
    assert (s.placed, s.filled) == (1, 1)
    assert s.fill_rate == 1.0
    # 저가 94.9 vs 지정가 95.019 → 관통 ≈ 12.5bp ≥ 5bp — 스침이 아니다.
    assert s.marginal_fills == 0
    assert s.median_wait_ms == 0  # 예약 서브스텝에서 곧바로 체결.
    journal.close()


class TestLiveParamValidation:
    """라이브가 재현 못 하는 설정은 조용히 무시하지 않고 거부한다(WAN-95의 교훈)."""

    def test_rejects_close_entry_mode(self) -> None:
        with pytest.raises(ValueError, match="zone_limit"):
            ZoneLimitLiveEngine(params=_params(entry_mode="close", rsi_mode="closed_bar"))

    def test_rejects_tap_band(self) -> None:
        params = _params()
        assert params.deviation_filter is not None
        tap_filter = params.deviation_filter.model_copy(update={"band_bar": "tap"})
        tap = params.model_copy(update={"deviation_filter": tap_filter})
        with pytest.raises(ValueError, match="재현할 수 없습니다"):
            ZoneLimitLiveEngine(params=tap)

    def test_rejects_fill_conservatism_lenses(self) -> None:
        with pytest.raises(ValueError, match="민감도"):
            ZoneLimitLiveEngine(params=_params(fill_penetration_bps=5.0))

    def test_rejects_min_rr(self) -> None:
        with pytest.raises(ValueError, match="min_rr"):
            ZoneLimitLiveEngine(params=_params(min_rr=1.0))
