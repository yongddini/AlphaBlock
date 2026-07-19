"""WAN-137 Phase 2 손익 격자 테스트 — 익절 오버라이드가 진입을 안 바꾸고 ①②를 지킨다.

PM 변경요청(2026-07-19)의 회귀 요구를 **동작으로** 고정한다:

* **진입 불변(거래 수 검산의 근거)** — 익절 오버라이드는 청산만 바꾸고 진입 결정·체결에는
  전혀 안 쓰인다. 그래서 오버라이드를 걸어도 후보(체결 셋업)의 진입 집합은 기본 익절과
  비트 단위로 같아야 한다. 이게 깨지면 "익절이 진입을 바꾼" 배선 버그다.
* **①(뚫린 저항 제외)·②(확정 시점 준수)** — 손익 팔의 저항 오버라이드가 순수 함수·클리핑
  provider를 거쳐 뚫린 저항·미래 저항을 목표로 삼지 않는지 산출물(선택된 목표가)로 확인한다.
"""

from __future__ import annotations

import pandas as pd

from backtest.models import BacktestConfig
from backtest.multi_tf_overlap import ZoneProvider, indexed_zone_provider
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan137_phase2_pnl import _OverrideStats, make_resistance_override
from backtest.zone_limit_backtest import TakeProfitContext, build_zone_limit_candidates
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection, OrderBlockResult

BEAR = OrderBlockDirection.BEARISH


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _engine_params() -> ConfluenceParams:
    # 이 합성 시드는 숏 셋업만 낸다 — 엔진 배선(오버라이드 훅)만 보려고 숏을 켜고 볼린저는
    # 작은 데이터셋에서 후보를 모두 걸러낼 수 있어 꺼 둔다(기존 엔진 테스트와 같은 관행).
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )


def _bear(
    bottom: float,
    top: float,
    *,
    confirmed_time: int = 0,
    break_time: int | None = None,
    breaker: bool = False,
) -> OrderBlock:
    return OrderBlock(
        direction=BEAR,
        top=top,
        bottom=bottom,
        start_time=confirmed_time,
        confirmed_time=confirmed_time,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=breaker,
        break_time=break_time,
    )


def _ctx(entry: float = 100.0, stop: float = 98.0, trigger: int = 1_000) -> TakeProfitContext:
    return TakeProfitContext(
        is_long=True,
        entry_price=entry,
        stop_price=stop,
        trigger_time=trigger,
        order_block=_bear(200.0, 202.0),  # 오버라이드는 ctx.order_block을 안 쓴다.
    )


# --------------------------------------------------------------------------- #
# 엔진 훅 — 오버라이드는 진입을 바꾸지 않는다 (거래 수 검산의 근거)
# --------------------------------------------------------------------------- #


def test_override_none_reproduces_default_bitwise() -> None:
    """`take_profit_override=None`은 인자를 아예 안 준 것과 비트 단위로 같다(기본값 불변)."""
    htf, one_min = _synthetic_pair()
    params = _engine_params()
    base, _ = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=_cfg())
    explicit, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=_cfg(), take_profit_override=None
    )
    assert [(c.entry_time, c.exit_time, c.exit_price, c.reason) for c in base] == [
        (c.entry_time, c.exit_time, c.exit_price, c.reason) for c in explicit
    ]


def test_override_is_invoked_and_leaves_entries_identical() -> None:
    """오버라이드는 셋업마다 **호출되지만**(죽은 코드가 아님) 진입 집합은 그대로다.

    진입 시각·진입가·손절가가 기본 익절과 동일해야 한다 — 익절이 진입을 안 건드린다는 것이
    거래 수 검산(팔별 후보 집합 일치)의 동작 근거다. 오버라이드가 실제로 청산 판정 경로에
    닿는지는 호출 횟수(≥ 후보 수)로 확인한다. (이 합성 시드는 유리 이탈이 없어 TP 체결
    자체는 안 나므로, 목표가 청산을 옮기는지는 ①②·폴백 테스트가 목표가로 검증한다.)
    """
    htf, one_min = _synthetic_pair()
    params = _engine_params()
    base, _ = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=_cfg())

    calls: list[TakeProfitContext] = []

    def spy_tp(ctx: TakeProfitContext) -> float | None:
        calls.append(ctx)
        return ctx.entry_price * (1.001 if ctx.is_long else 0.999)

    overridden, _ = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=_cfg(), take_profit_override=spy_tp
    )
    assert calls  # 훅이 실제로 셋업 루프에서 불린다(죽은 코드가 아님).
    assert len(calls) >= len(overridden)  # 후보(체결)마다 최소 한 번은 목표를 물어본다.
    assert [(c.entry_time, c.entry_price, c.stop_price) for c in base] == [
        (c.entry_time, c.entry_price, c.stop_price) for c in overridden
    ]


# --------------------------------------------------------------------------- #
# ① 뚫린 저항 제외 · ② 확정 시점 — 손익 팔의 저항 오버라이드에서 동작으로 고정
# --------------------------------------------------------------------------- #


def _provider(zones: list[OrderBlock]) -> ZoneProvider:
    result = OrderBlockResult(order_blocks=zones, signals=[], retap_signals=[])
    return indexed_zone_provider({"15m": result}, combine=False)


def test_override_excludes_broken_resistance() -> None:
    """① 더 가까운 저항이 진입 전 무효화(breaker)면 목표로 삼지 않고 다음 유효 저항을 쓴다."""
    broken = _bear(101.0, 103.0, confirmed_time=100, break_time=900, breaker=True)
    valid = _bear(105.0, 107.0, confirmed_time=100)
    stats = _OverrideStats()
    override = make_resistance_override(
        _provider([broken, valid]), ("15m",), ConfluenceParams(), stats
    )
    target = override(_ctx(entry=100.0, stop=98.0, trigger=1_000))
    assert target == 105.0  # 뚫린 101을 건너뛴다.
    assert stats.resistance == 1 and stats.fallback == 0


def test_override_ignores_future_confirmed_resistance() -> None:
    """② 진입 이후 확정된 미래 저항은 클리핑 provider에 안 보여 목표가 되지 못한다."""
    past = _bear(104.0, 106.0, confirmed_time=900)
    future = _bear(101.0, 103.0, confirmed_time=1_100)  # 더 가깝지만 진입(1000) 이후 확정.
    stats = _OverrideStats()
    override = make_resistance_override(
        _provider([past, future]), ("15m",), ConfluenceParams(), stats
    )
    target = override(_ctx(entry=100.0, stop=98.0, trigger=1_000))
    assert target == 104.0  # 미래 존(101)은 안 보인다.


def test_override_falls_back_to_fixed_r_when_no_resistance() -> None:
    """저항이 없으면 고정 1.5R로 폴백한다(현행 익절 유지) — fallback 카운트가 는다."""
    stats = _OverrideStats()
    override = make_resistance_override(_provider([]), ("15m",), ConfluenceParams(), stats)
    target = override(_ctx(entry=100.0, stop=98.0))
    # 1R = 100 − 98 = 2, 고정 1.5R → 100 + 3 = 103.
    assert target == 103.0
    assert stats.fallback == 1 and stats.resistance == 0


# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #


def _cfg() -> BacktestConfig:
    return BacktestConfig()
