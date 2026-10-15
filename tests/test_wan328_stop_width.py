"""WAN-328 — 손절폭 해부: 틱 추종 체결가 관측(엔진) + 라이브↔백테 귀속(장부).

세 갈래를 **동작으로** 고정한다:

1. `observe_path_fill`은 **옵트인이고 관측 전용**이다 — 안 켜면 결과가 비트 단위로 같고,
   켜도 체결가·손익·상태가 안 바뀐다(라벨만 붙는 실패의 반대편 — 관측이 대상을 바꾸면 안 된다).
2. 틱 추종 체결가는 밴드 표본과 터치를 **같은 가격으로** 굴린 고정점이다 — 엔진의
   「종가 표본 · 저가 터치」 비대칭이 만드는 차이가 실제로 잡히는지.
3. ~~귀속 규칙~~ — 라이브 장부 쪽(`live.stop_width_parity` · `alphablock stop-width`)은 옛 규칙
   페이퍼와 함께 은퇴해 그 테스트도 지웠다(WAN-451 ②). 엔진 관측·백테 표 쪽은 그대로다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backtest.substep import (
    SubStep,
    ZoneLimitOutcome,
    ZoneLimitStatus,
    simulate_zone_limit_trade,
)
from backtest.wan328_stop_width_parity import GUARD_FRACTION, setup_rows, summarize
from backtest.zone_limit_backtest import SetupDiagnostic
from strategy.models import OrderBlockDirection
from strategy.realtime_rsi import RealtimeRsi

_STOP = 95.0


def _step(time_ms: int, *, high: float, low: float, close: float) -> SubStep:
    return SubStep(time=time_ms, high=high, low=low, close=close, htf_bar_time=0)


def _rsi() -> RealtimeRsi:
    return RealtimeRsi(length=14)


class _LinearLiveLimit:
    """지정가 = `a + b·표본` 인 봉내 공급자 (밴드가 표본을 따라가는 것의 최소 모형).

    실제 밴드는 SMA20의 20번째 자리에 현재가가 들어가므로 기울기가 1/20 부근이다 — 여기서도
    `b < 1`이라 `p − L(p)`가 단조 증가하고 고정점이 하나 있다(같은 성질).
    """

    def __init__(self, a: float, b: float, *, stop: float = _STOP) -> None:
        self.a = a
        self.b = b
        self._stop = stop
        self.probe_calls = 0

    def commit(self, closed_price: float) -> None:  # pragma: no cover - 이 테스트는 봉내만 본다
        return None

    def limit_price(self, live_price: float) -> float | None:
        return self.a + self.b * live_price

    def probe_limit(self, live_price: float) -> float | None:
        self.probe_calls += 1
        return self.a + self.b * live_price

    def resolve_exits(self, limit_price: float) -> tuple[float, float | None] | None:
        return self._stop, None


def _simulate(
    provider: _LinearLiveLimit, steps: list[SubStep], *, observe: bool
) -> ZoneLimitOutcome:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        live_limit=provider,
        stop_price=_STOP,
        substeps=steps,
        rsi_state=_rsi(),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        rsi_gate_mode="unconditional",
        limit_valid_bars=24,
        observe_path_fill=observe,
    )


# --------------------------------------------------------------------------- #
# 1) 옵트인 · 관측 전용
# --------------------------------------------------------------------------- #


def test_path_fill_is_opt_in_and_absent_by_default() -> None:
    steps = [_step(0, high=101.0, low=98.0, close=99.0)]
    out = _simulate(_LinearLiveLimit(50.0, 0.5), steps, observe=False)
    assert out.status is ZoneLimitStatus.FILLED_OPEN
    assert out.path_fill_price is None


def test_observing_the_path_does_not_change_the_trade() -> None:
    """관측이 대상을 바꾸면 안 된다 — 켜고 끈 결과가 `path_fill_price`만 빼면 동일하다."""
    steps = [
        _step(0, high=101.0, low=98.0, close=99.0),
        _step(60_000, high=102.0, low=97.0, close=101.0),
    ]
    off = _simulate(_LinearLiveLimit(50.0, 0.5), steps, observe=False)
    on = _simulate(_LinearLiveLimit(50.0, 0.5), steps, observe=True)
    assert on.path_fill_price is not None
    assert replace(on, path_fill_price=None) == off


def test_observe_path_fill_rejects_a_constant_limit() -> None:
    """상수 지정가에는 「틱 추종 체결가」가 정의되지 않는다 — 조용히 무시하지 않고 거부한다."""
    with pytest.raises(ValueError, match="observe_path_fill"):
        simulate_zone_limit_trade(
            direction=OrderBlockDirection.BULLISH,
            limit_price=100.0,
            stop_price=_STOP,
            substeps=[_step(0, high=101.0, low=99.0, close=100.5)],
            rsi_state=_rsi(),
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            rsi_gate_mode="unconditional",
            observe_path_fill=True,
        )


# --------------------------------------------------------------------------- #
# 2) 틱 추종 체결가 = 고정점
# --------------------------------------------------------------------------- #


def test_path_fill_is_the_fixed_point_of_the_moving_limit() -> None:
    """`L(p) = 50 + 0.5p` → 고정점 `p* = 100`. 엔진은 종가 표본이라 더 높은 값에 체결한다."""
    # 종가 99 → 엔진 지정가 99.5, 저가 98 <= 99.5라 체결. 틱 추종은 p*=100에서 체결.
    steps = [_step(0, high=101.0, low=98.0, close=99.0)]
    out = _simulate(_LinearLiveLimit(50.0, 0.5), steps, observe=True)
    assert out.entry_price == pytest.approx(99.5)
    assert out.path_fill_price == pytest.approx(100.0, abs=1e-6)


def test_path_fill_uses_the_favourable_edge_when_the_whole_bar_is_already_filled() -> None:
    """봉 고가에서도 이미 `p <= L(p)`면 봉이 열리자마자 체결 — 그 순간의 지정가를 낸다."""
    steps = [_step(0, high=99.0, low=98.0, close=98.5)]
    out = _simulate(_LinearLiveLimit(50.0, 0.5), steps, observe=True)
    assert out.path_fill_price == pytest.approx(50.0 + 0.5 * 99.0)


def test_path_fill_is_none_when_the_tick_model_would_not_fill_in_that_bar() -> None:
    """엔진의 「종가 표본 · 저가 터치」가 **더 관대**한 경우 — 틱 추종은 그 봉에서 안 닿는다."""
    # 고정점 p* = 100인데 봉 범위가 [100.5, 103]이라 어느 가격에서도 p <= L(p)가 아니다.
    # 그런데 종가 103 → 지정가 101.5이고 저가 100.5 <= 101.5라 엔진은 체결시킨다.
    steps = [_step(0, high=103.0, low=100.5, close=103.0)]
    out = _simulate(_LinearLiveLimit(50.0, 0.5), steps, observe=True)
    assert out.status is ZoneLimitStatus.FILLED_OPEN
    assert out.path_fill_price is None


def test_path_fill_mirrors_for_shorts() -> None:
    """숏은 가격이 올라오며 `L(p) <= p`가 처음 성립하는 점 — 같은 고정점의 거울상."""
    steps = [_step(0, high=102.0, low=99.0, close=101.0)]
    provider = _LinearLiveLimit(50.0, 0.5, stop=105.0)
    out = simulate_zone_limit_trade(
        direction=OrderBlockDirection.BEARISH,
        live_limit=provider,
        stop_price=105.0,
        substeps=steps,
        rsi_state=_rsi(),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        rsi_gate_mode="unconditional",
        limit_valid_bars=24,
        observe_path_fill=True,
    )
    assert out.status is ZoneLimitStatus.FILLED_OPEN
    assert out.path_fill_price == pytest.approx(100.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# 3) 백테 쪽 집계 — 구조 천장이 「존이 얇아서」를 가른다
# --------------------------------------------------------------------------- #


def _diag(
    *,
    entry: float,
    stop: float,
    zone_height: float | None,
    path: float | None = None,
    trigger: int = 1_600_000_000_000,
) -> SetupDiagnostic:
    from backtest.models import PositionSide

    return SetupDiagnostic(
        trigger_time=trigger,
        tap_bar_time=trigger,
        tap_close=entry,
        side=PositionSide.LONG,
        limit_price=entry,
        stop_price=stop,
        filled=True,
        dropped=False,
        status=ZoneLimitStatus.FILLED_EXITED,
        zone_height=zone_height,
        path_fill_price=path,
    )


def test_setup_rows_flag_the_structural_ceiling_below_the_guard() -> None:
    """존 높이가 가드보다 낮으면 진입가를 어디에 잡아도 걸린다 — 그 열이 실제로 찍힌다."""
    guard_pct = GUARD_FRACTION * 100.0
    thin = _diag(entry=100.0, stop=99.9, zone_height=0.1)  # 천장 0.1% < 0.3%
    fat = _diag(entry=100.0, stop=99.0, zone_height=2.0)  # 천장 2.0% > 0.3%
    rows = setup_rows("BTC/USDT:USDT", "15m", [thin, fat])
    assert [r.guard_passed for r in rows] == [False, True]
    assert rows[0].zone_ceiling_pct is not None and rows[0].zone_ceiling_pct < guard_pct
    assert rows[1].zone_ceiling_pct is not None and rows[1].zone_ceiling_pct > guard_pct

    summary = {(r.timeframe, r.month): r for r in summarize(rows)}
    total = summary[("15m", "ALL")]
    assert total.filled == 2
    assert total.guard_rejected == 1
    # 탈락 1건이 전부 「존이 얇아서」 — 체결 모델과 무관한 불가피한 탈락이다.
    assert total.ceiling_below_guard == 1
    assert total.ceiling_share_of_rejects == pytest.approx(1.0)


def test_setup_rows_skip_unfilled_setups() -> None:
    """체결되지 않은 셋업에는 체결가가 없으므로 손절폭도 없다 — 지어내지 않는다."""
    unfilled = SetupDiagnostic(
        trigger_time=1_600_000_000_000,
        tap_bar_time=1_600_000_000_000,
        tap_close=100.0,
        side=_diag(entry=1.0, stop=0.9, zone_height=None).side,
        limit_price=None,
        stop_price=99.0,
        filled=False,
        dropped=False,
        status=ZoneLimitStatus.CANCELLED_EXPIRED,
    )
    assert setup_rows("BTC/USDT:USDT", "1h", [unfilled]) == []


def test_summary_counts_bars_where_the_tick_model_would_not_have_filled() -> None:
    rows = setup_rows(
        "BTC/USDT:USDT",
        "1h",
        [
            _diag(entry=100.0, stop=99.0, zone_height=2.0, path=99.8),
            _diag(entry=100.0, stop=99.0, zone_height=2.0, path=None),
        ],
    )
    total = next(r for r in summarize(rows) if r.month == "ALL")
    assert total.path_observed == 1
    assert total.path_unfilled == 1
    # 틱 추종 체결가(99.8)가 경계(99.0)에 더 가까워 손절폭이 좁아진 셋업이다.
    assert total.path_deeper_share == pytest.approx(1.0)
