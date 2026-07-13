"""strategy.confluence 전략 단위 테스트 (WAN-23).

진입=오더블록 탭+RSI, 익절=진입가 너머 가장 가까운 EMA/VWMA 선 도달,
손절=오더블록 무효화(breaker) 규칙을 검증한다. 지표 수치 자체는
test_indicators가 검증하므로, 여기서는 오더블록 탭 시그널을 주입해
진입 판정·계획 청산(익절/손절)·우선순위·백테스트 연동을 확인한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import run_backtest
from backtest.models import ExitReason
from strategy.confluence import (
    ConfluenceSignal,
    ConfluenceStrategy,
    IndicatorSnapshot,
    SignalKind,
    generate_confluence_signals,
)
from strategy.indicators import ema
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
    PlannedExit,
    SignalExitReason,
)

_BULL = OrderBlockDirection.BULLISH
_BEAR = OrderBlockDirection.BEARISH
_STEP = 60_000


def _order_block(
    direction: OrderBlockDirection,
    *,
    top: float = 1_000.0,
    bottom: float = 0.0,
    break_time: int | None = None,
) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=break_time is not None,
        break_time=break_time,
    )


def _signal(
    direction: OrderBlockDirection,
    pos: int,
    price: float,
    order_block: OrderBlock | None = None,
) -> OrderBlockSignal:
    return OrderBlockSignal(
        direction=direction,
        trigger_time=pos * _STEP,
        price=price,
        order_block=order_block or _order_block(direction),
        status="active",
    )


def _df(closes: list[float], *, wick: float = 2.0, volume: float = 10.0) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "open_time": [i * _STEP for i in range(n)],
            "open": closes,
            "high": [c + wick for c in closes],
            "low": [c - wick for c in closes],
            "close": closes,
            "volume": [volume] * n,
        }
    )


# --------------------------------------------------------------------------- 진입 (오더블록 + RSI)


def _falling_then_rising(
    down: int, up: int, *, start: float = 200.0, step: float = 3.0
) -> list[float]:
    """`down`봉 하락 후 `up`봉 상승하는 V자 종가. 저점에서 RSI 과매도."""
    fall = [start - i * step for i in range(down)]
    rise = [fall[-1] + (i + 1) * step for i in range(up)]
    return fall + rise


def test_bullish_tap_with_oversold_rsi_confirms_long() -> None:
    closes = [200.0 - i * 3.0 for i in range(25)]  # 단조 하락 -> RSI 과매도
    df = _df(closes)
    pos = 24
    params = ConfluenceParams(use_line_take_profit=False, use_order_block_stop=False)
    result = ConfluenceStrategy(params=params).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, closes[pos])])
    )

    entry = result.entries[0]
    assert entry.kind is SignalKind.ENTRY
    assert entry.rsi is not None and entry.rsi <= params.rsi_oversold
    assert entry.confirmed is True
    assert result.confirmed_entries == [entry]


def test_bearish_tap_with_overbought_rsi_confirms_short() -> None:
    closes = [100.0 + i * 3.0 for i in range(25)]  # 단조 상승 -> RSI 과매수
    df = _df(closes)
    pos = 24
    params = ConfluenceParams(use_line_take_profit=False, use_order_block_stop=False)
    result = ConfluenceStrategy(params=params).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BEAR, pos, closes[pos])])
    )
    entry = result.entries[0]
    assert entry.rsi is not None and entry.rsi >= params.rsi_overbought
    assert entry.confirmed is True


def test_long_tap_rejected_when_rsi_not_oversold() -> None:
    closes = [100.0 + i * 3.0 for i in range(25)]  # 상승 -> RSI 높음 -> 롱 기각
    df = _df(closes)
    pos = 24
    result = ConfluenceStrategy(params=ConfluenceParams()).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, closes[pos])])
    )
    entry = result.entries[0]
    assert entry.confirmed is False
    assert result.confirmed_entries == []
    assert result.exits == []


def test_ema_vwma_do_not_gate_entry() -> None:
    """상승 추세(종가>EMA·정배열)라도 롱은 RSI 과매도가 아니면 진입하지 않는다.

    구 규칙(EMA/VWMA 게이트)에서는 상승 추세 롱이 확정됐지만, 새 규칙은 RSI만 본다.
    """
    closes = [100.0 + i * 3.0 for i in range(25)]  # 강한 상승
    df = _df(closes)
    pos = 24
    result = ConfluenceStrategy(params=ConfluenceParams()).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, closes[pos])])
    )
    # 종가가 EMA 위(정배열)이지만 RSI 과매도가 아니므로 롱 진입은 없다.
    assert result.entries[0].confirmed is False


def test_rsi_warmup_nan_does_not_confirm() -> None:
    closes = [200.0 - i * 3.0 for i in range(5)]  # RSI 워밍업(length=14) 구간
    df = _df(closes)
    pos = 3
    result = ConfluenceStrategy(params=ConfluenceParams()).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, closes[pos])])
    )
    entry = result.entries[0]
    assert entry.rsi is None
    assert entry.confirmed is False


def test_tap_after_invalidation_is_skipped() -> None:
    closes = [200.0 - i * 3.0 for i in range(25)]
    df = _df(closes)
    pos = 20
    # 무효화 시각이 탭보다 앞(<=탭)이면 활성 오더블록 탭이 아니므로 건너뛴다.
    ob = _order_block(_BULL, bottom=0.0, break_time=10 * _STEP)
    result = ConfluenceStrategy(params=ConfluenceParams()).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, closes[pos], ob)])
    )
    assert result.entries == []


# ----------------------------------------------------------------- 익절(선 도달) 순수 로직


def test_take_profit_price_long_nearest_line_above() -> None:
    tp = ConfluenceStrategy._take_profit_price
    lines = {"ema_a": 110.0, "ema_b": 105.0, "vwma": 95.0}
    # 진입가 100 너머(위) 가장 가까운 선 = 105. 고가가 그에 도달하면 그 가격.
    assert tp(1, 100.0, high=106.0, low=99.0, lines=lines) == pytest.approx(105.0)
    # 고가가 105에 못 미치면 익절 없음.
    assert tp(1, 100.0, high=104.0, low=99.0, lines=lines) is None


def test_take_profit_price_short_nearest_line_below() -> None:
    tp = ConfluenceStrategy._take_profit_price
    lines = {"ema_a": 90.0, "ema_b": 95.0, "vwma": 105.0}
    # 진입가 100 너머(아래) 가장 가까운 선 = 95. 저가가 그에 도달하면 그 가격.
    assert tp(-1, 100.0, high=101.0, low=94.0, lines=lines) == pytest.approx(95.0)
    assert tp(-1, 100.0, high=101.0, low=96.0, lines=lines) is None


def test_take_profit_price_none_when_no_line_beyond_entry() -> None:
    tp = ConfluenceStrategy._take_profit_price
    # 롱인데 진입가 위에 선이 하나도 없음 -> 익절 목표 없음.
    assert tp(1, 100.0, high=120.0, low=90.0, lines={"a": 90.0, "b": 95.0}) is None


# --------------------------------------------------------------------------- 계획 청산 (_plan_exit)


def _plan(
    strategy: ConfluenceStrategy,
    *,
    break_pos: int | None,
    line_cols: dict[str, list[float]],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    direction: OrderBlockDirection = _BULL,
    order_block: OrderBlock | None = None,
) -> PlannedExit | None:
    times = [i * _STEP for i in range(len(closes))]
    snap = IndicatorSnapshot(time=0, close=100.0, rsi=25.0, lines={})
    entry = ConfluenceSignal(
        kind=SignalKind.ENTRY,
        direction=direction,
        time=0,
        price=100.0,
        confirmed=True,
        rsi=25.0,
        order_block=order_block or _order_block(direction),
        indicators=snap,
    )
    return strategy._plan_exit(
        entry,
        entry_pos=0,
        break_pos=break_pos,
        n=len(closes),
        times=times,
        highs=highs,
        lows=lows,
        closes=closes,
        line_cols=line_cols,
    )


def test_plan_exit_take_profit_at_nearest_line() -> None:
    strategy = ConfluenceStrategy(ConfluenceParams(tp_ema_lengths=(5,), tp_vwma_length=None))
    # 선 ema_5가 진입가(100) 위 105에 있고 봉2에서 고가가 도달 -> 익절.
    planned = _plan(
        strategy,
        break_pos=None,
        line_cols={"ema_5": [100.0, 105.0, 105.0]},
        highs=[100.0, 104.0, 106.0],
        lows=[100.0, 100.0, 100.0],
        closes=[100.0, 102.0, 103.0],
    )
    assert planned is not None
    assert planned.reason is SignalExitReason.TAKE_PROFIT
    assert planned.time == 2 * _STEP
    assert planned.price == pytest.approx(105.0)


def test_plan_exit_stop_loss_at_break_bar() -> None:
    """종가가 무효화 경계보다 더 불리하면(더 하락) 종가를 그대로 손절가로 쓴다."""
    strategy = ConfluenceStrategy(
        ConfluenceParams(use_line_take_profit=False, tp_vwma_length=None, tp_ema_lengths=(5,))
    )
    # 진입가 100, 오더블록 하단(무효화 경계) 95 — 종가 90은 경계보다 더 불리하다.
    ob = _order_block(_BULL, bottom=95.0)
    planned = _plan(
        strategy,
        break_pos=2,
        line_cols={"ema_5": [100.0, 100.0, 100.0]},
        highs=[100.0, 100.0, 100.0],
        lows=[100.0, 100.0, 100.0],
        closes=[100.0, 99.0, 90.0],
        order_block=ob,
    )
    assert planned is not None
    assert planned.reason is SignalExitReason.STOP_LOSS
    assert planned.time == 2 * _STEP
    assert planned.price == pytest.approx(90.0)  # 무효화 봉 종가


def test_plan_exit_stop_loss_never_favorable_vs_entry_long() -> None:
    """무효화 봉이 wick으로만 경계를 찍고 진입가보다 유리하게 마감해도 손절은 손실이다(WAN-65).

    오더블록 하단(무효화 경계) 95 아래로 wick이 찍혀 breaker가 됐지만, 그 봉의 종가는
    102(진입가 100보다 위)로 마감했다. 종가를 그대로 쓰면 "손절인데 이익"이 되므로,
    체결가는 경계(95)로 clamp돼야 한다.
    """
    strategy = ConfluenceStrategy(ConfluenceParams(use_line_take_profit=False))
    ob = _order_block(_BULL, bottom=95.0)
    planned = _plan(
        strategy,
        break_pos=1,
        line_cols={},
        highs=[100.0, 103.0],
        lows=[100.0, 94.0],
        closes=[100.0, 102.0],
        order_block=ob,
    )
    assert planned is not None
    assert planned.reason is SignalExitReason.STOP_LOSS
    assert planned.price == pytest.approx(95.0)
    assert planned.price < 100.0  # 진입가보다 반드시 불리하다(손실 보장).


def test_plan_exit_stop_loss_never_favorable_vs_entry_short() -> None:
    """숏 대칭: wick이 상단 경계를 찍고 종가가 진입가보다 유리(더 낮게) 마감해도 손실이다."""
    strategy = ConfluenceStrategy(ConfluenceParams(use_line_take_profit=False))
    ob = _order_block(_BEAR, top=105.0)
    planned = _plan(
        strategy,
        break_pos=1,
        line_cols={},
        highs=[100.0, 106.0],
        lows=[100.0, 97.0],
        closes=[100.0, 98.0],
        direction=_BEAR,
        order_block=ob,
    )
    assert planned is not None
    assert planned.reason is SignalExitReason.STOP_LOSS
    assert planned.price == pytest.approx(105.0)
    assert planned.price > 100.0  # 진입가보다 반드시 불리하다(손실 보장).


def test_plan_exit_stop_priority_when_both_hit_same_bar() -> None:
    line_cols = {"ema_5": [100.0, 105.0, 105.0]}
    highs = [100.0, 106.0, 106.0]  # 봉1에서 익절선(105) 도달
    lows = [100.0, 100.0, 100.0]
    closes = [100.0, 101.0, 102.0]
    # 봉1에서 손절(break_pos=1)과 익절 동시 충족.
    strict = ConfluenceStrategy(
        ConfluenceParams(tp_ema_lengths=(5,), tp_vwma_length=None, stop_before_take_profit=True)
    )
    planned = _plan(strict, break_pos=1, line_cols=line_cols, highs=highs, lows=lows, closes=closes)
    assert planned is not None and planned.reason is SignalExitReason.STOP_LOSS

    relaxed = ConfluenceStrategy(
        ConfluenceParams(tp_ema_lengths=(5,), tp_vwma_length=None, stop_before_take_profit=False)
    )
    planned2 = _plan(
        relaxed, break_pos=1, line_cols=line_cols, highs=highs, lows=lows, closes=closes
    )
    assert planned2 is not None and planned2.reason is SignalExitReason.TAKE_PROFIT


def test_plan_exit_none_when_no_target_reached() -> None:
    strategy = ConfluenceStrategy(ConfluenceParams(tp_ema_lengths=(5,), tp_vwma_length=None))
    planned = _plan(
        strategy,
        break_pos=None,
        line_cols={"ema_5": [100.0, 90.0, 90.0]},  # 선이 진입가 아래 -> 롱 익절 목표 없음
        highs=[100.0, 95.0, 95.0],
        lows=[100.0, 90.0, 90.0],
        closes=[100.0, 92.0, 93.0],
    )
    assert planned is None


# ----------------------------------------------------------- 통합: 익절/손절 + 백테스트


def test_confirmed_long_take_profit_end_to_end() -> None:
    closes = _falling_then_rising(down=22, up=12)  # 저점서 과매도, 이후 상승
    df = _df(closes, wick=1.0)
    trough = 21
    params = ConfluenceParams(tp_ema_lengths=(5,), tp_vwma_length=None, use_order_block_stop=False)
    ob = _order_block(_BULL, bottom=0.0)  # 무효화 없음
    result = ConfluenceStrategy(params=params).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, trough, closes[trough], ob)])
    )
    entry = result.entries[0]
    assert entry.confirmed is True
    assert entry.planned_exit is not None
    assert entry.planned_exit.reason is SignalExitReason.TAKE_PROFIT
    # 익절가는 청산 봉의 ema_5 선 값과 일치한다.
    exit_pos = entry.planned_exit.time // _STEP
    ema5 = float(ema(df, length=5)[exit_pos])
    assert entry.planned_exit.price == pytest.approx(ema5)
    # 명시적 청산 이벤트로도 내보내진다.
    assert len(result.exits) == 1
    assert result.exits[0].kind is SignalKind.EXIT
    assert result.exits[0].exit_reason is SignalExitReason.TAKE_PROFIT


def test_confirmed_long_stop_loss_end_to_end() -> None:
    closes = [200.0 - i * 3.0 for i in range(25)]  # 지속 하락
    df = _df(closes)
    entry_pos = 18
    break_pos = 22
    ob = _order_block(_BULL, bottom=closes[entry_pos] - 1.0, break_time=break_pos * _STEP)
    params = ConfluenceParams(use_line_take_profit=False)
    result = ConfluenceStrategy(params=params).run(
        df,
        OrderBlockResult(
            order_blocks=[], signals=[_signal(_BULL, entry_pos, closes[entry_pos], ob)]
        ),
    )
    entry = result.entries[0]
    assert entry.confirmed is True
    assert entry.planned_exit is not None
    assert entry.planned_exit.reason is SignalExitReason.STOP_LOSS
    assert entry.planned_exit.time == break_pos * _STEP
    assert entry.planned_exit.price == pytest.approx(closes[break_pos])


def test_backtest_consumes_planned_take_profit() -> None:
    closes = _falling_then_rising(down=22, up=12)
    df = _df(closes, wick=1.0)
    trough = 21
    params = ConfluenceParams(tp_ema_lengths=(5,), tp_vwma_length=None, use_order_block_stop=False)
    ob = _order_block(_BULL, bottom=0.0)
    result = generate_confluence_signals(
        df,
        params,
        order_block_result=OrderBlockResult(
            order_blocks=[], signals=[_signal(_BULL, trough, closes[trough], ob)]
        ),
    )
    signals = result.order_block_signals
    assert len(signals) == 1
    assert signals[0].planned_exit is not None

    bt = run_backtest(df, signals)
    assert bt.metrics.num_trades == 1
    trade = bt.trades[0]
    assert trade.exits[-1].reason is ExitReason.TAKE_PROFIT


def test_backtest_consumes_planned_stop_loss() -> None:
    closes = [200.0 - i * 3.0 for i in range(25)]
    df = _df(closes)
    entry_pos = 18
    break_pos = 22
    ob = _order_block(_BULL, bottom=closes[entry_pos] - 1.0, break_time=break_pos * _STEP)
    params = ConfluenceParams(use_line_take_profit=False)
    result = generate_confluence_signals(
        df,
        params,
        order_block_result=OrderBlockResult(
            order_blocks=[], signals=[_signal(_BULL, entry_pos, closes[entry_pos], ob)]
        ),
    )
    bt = run_backtest(df, result.order_block_signals)
    assert bt.metrics.num_trades == 1
    assert bt.trades[0].exits[-1].reason is ExitReason.STOP_LOSS


def test_no_planned_exit_falls_through_to_end_of_data() -> None:
    """익절선·무효화가 모두 없으면 계획 청산이 없고, 백테스트는 데이터 끝에서 청산한다."""
    closes = [200.0 - i * 3.0 for i in range(25)]  # 계속 하락, 롱 익절선 위 없음
    df = _df(closes)
    entry_pos = 20
    ob = _order_block(_BULL, bottom=0.0)  # 무효화 없음
    params = ConfluenceParams(use_order_block_stop=False)
    result = generate_confluence_signals(
        df,
        params,
        order_block_result=OrderBlockResult(
            order_blocks=[], signals=[_signal(_BULL, entry_pos, closes[entry_pos], ob)]
        ),
    )
    entry = result.entries[0]
    assert entry.confirmed is True
    assert entry.planned_exit is None
    assert result.exits == []

    bt = run_backtest(df, result.order_block_signals)
    assert bt.trades[0].exits[-1].reason is ExitReason.END_OF_DATA


# --------------------------------------------------------------------------- WAN-8 연동 / 엣지


def test_order_block_signals_bridge_only_confirmed() -> None:
    closes = [200.0 - i * 3.0 for i in range(25)]  # 하락 -> 롱 확정, 숏 기각
    df = _df(closes)
    pos = 24
    signals = [
        _signal(_BULL, pos, closes[pos]),  # 과매도 롱 -> 확정
        _signal(_BEAR, pos, closes[pos]),  # 과매도인데 숏 -> 기각
    ]
    result = ConfluenceStrategy(
        params=ConfluenceParams(use_line_take_profit=False, use_order_block_stop=False)
    ).run(df, OrderBlockResult(order_blocks=[], signals=signals))
    bridged = result.order_block_signals
    assert len(bridged) == 1
    assert bridged[0].direction is _BULL
    assert bridged[0].status == "active"


def test_run_end_to_end_detects_and_filters() -> None:
    """실제 오더블록 탐지까지 포함한 엔드투엔드 스모크 테스트."""
    closes = _falling_then_rising(down=40, up=40, start=300.0, step=2.0)
    df = _df(closes)
    result = generate_confluence_signals(df)
    for entry in result.confirmed_entries:
        assert entry.order_block is not None
        assert entry.rsi is not None
    assert isinstance(result.params, ConfluenceParams)


def test_empty_dataframe_returns_empty_result() -> None:
    empty = pd.DataFrame({c: [] for c in ("open_time", "open", "high", "low", "close", "volume")})
    result = generate_confluence_signals(empty)
    assert result.entries == []
    assert result.exits == []


def test_planned_exit_model_roundtrip() -> None:
    exit_ = PlannedExit(time=123, price=99.5, reason=SignalExitReason.STOP_LOSS)
    assert exit_.reason is SignalExitReason.STOP_LOSS
    assert exit_.model_dump()["reason"] == "stop_loss"


# --------------------------------------------------- WAN-41 진입 방식 전환 설정


def test_entry_mode_defaults_preserve_variant_a() -> None:
    """새 설정의 기본값은 현행(A안)을 보존한다: 종가 진입 + 확정봉 RSI."""
    params = ConfluenceParams()
    assert params.entry_mode == "close"
    assert params.rsi_mode == "closed_bar"
    assert params.zone_limit_ref == "proximal"
    assert params.limit_valid_bars == 24
    assert params.cancel_limit_on_condition_fail is False


def test_zone_limit_price_by_reference() -> None:
    long_ob = _order_block(_BULL, top=100.0, bottom=90.0)
    short_ob = _order_block(_BEAR, top=100.0, bottom=90.0)
    # 롱: proximal=상단, distal=하단. 숏: proximal=하단, distal=상단. mid=중앙.
    assert ConfluenceParams(zone_limit_ref="proximal").zone_limit_price(long_ob) == 100.0
    assert ConfluenceParams(zone_limit_ref="distal").zone_limit_price(long_ob) == 90.0
    assert ConfluenceParams(zone_limit_ref="mid").zone_limit_price(long_ob) == 95.0
    assert ConfluenceParams(zone_limit_ref="proximal").zone_limit_price(short_ob) == 90.0
    assert ConfluenceParams(zone_limit_ref="distal").zone_limit_price(short_ob) == 100.0


# ---------------------------------------- WAN-66 익절 목표선 = EMA 60 + VWMA 100


def test_default_take_profit_lines_are_ema60_and_vwma100_only() -> None:
    """기본 익절 판정선은 EMA 60 + VWMA 100 두 개뿐이다(WAN-66).

    표시선(EMA 20/120/240/365)이 익절 후보로 새어 들어가면 가장 빠른 선에서
    조기 익절하던 버그가 재발한다 — 이를 회귀로 고정한다.
    """
    params = ConfluenceParams()
    assert params.tp_ema_lengths == (60,)
    assert params.tp_vwma_length == 100

    strategy = ConfluenceStrategy(params)
    # 익절 판정이 실제로 참조하는 선 집합(_line_columns)에 EMA 20/120/240/365가 없다.
    df = _df([100.0 + i * 0.1 for i in range(400)])
    line_cols = strategy._line_columns(df)
    assert set(line_cols) == {"ema_60", "vwma_100"}
    for excluded in ("ema_20", "ema_120", "ema_240", "ema_365"):
        assert excluded not in line_cols


def test_display_lines_are_separate_from_take_profit_lines() -> None:
    """차트 표시선(display_ema_lengths)은 익절 판정선과 분리된 필드다(WAN-66)."""
    params = ConfluenceParams()
    # 표시선은 5개 EMA 전부, 익절선은 EMA 60뿐 — 서로 다른 필드가 담는다.
    assert params.sorted_display_ema_lengths == [20, 60, 120, 240, 365]
    assert params.sorted_tp_ema_lengths == [60]
    # 표시선을 바꿔도 익절 판정선은 영향받지 않는다.
    wider = ConfluenceParams(display_ema_lengths=(7, 25, 99))
    assert wider.sorted_display_ema_lengths == [7, 25, 99]
    assert wider.tp_ema_lengths == (60,)


def test_display_ema_lengths_rejects_duplicates_and_nonpositive() -> None:
    with pytest.raises(ValueError, match="display_ema_lengths"):
        ConfluenceParams(display_ema_lengths=(20, 20))
    with pytest.raises(ValueError, match="display_ema_lengths"):
        ConfluenceParams(display_ema_lengths=(0, 60))


# ------------------------------------------------- WAN-68 진입 근거 게이트 (min_rr·이격도·숏 존폐)


def test_wan68_gate_defaults_preserve_current_behavior() -> None:
    """새 게이트 3종은 모두 기본값이 꺼짐(또는 현행 동작)이라 기존 백테스트가 불변이다."""
    params = ConfluenceParams()
    assert params.min_rr is None
    assert params.long_max_deviation is None
    assert params.long_deviation_gate_ema_length == 240
    assert params.short_enabled is True


def test_min_rr_gate_rejects_low_reward_to_risk_ratio() -> None:
    passes = ConfluenceStrategy._passes_min_rr
    ob = _order_block(_BULL, top=200.0, bottom=108.0)  # risk = 128 - 108 = 20
    lines = {"ema_60": 129.5}  # reward = 129.5 - 128 = 1.5 -> rr = 0.075
    assert passes(1, 128.0, ob, lines, min_rr=1.0) is False


def test_min_rr_gate_accepts_high_reward_to_risk_ratio() -> None:
    passes = ConfluenceStrategy._passes_min_rr
    ob = _order_block(_BULL, top=200.0, bottom=127.0)  # risk = 128 - 127 = 1
    lines = {"ema_60": 129.5}  # reward = 1.5 -> rr = 1.5
    assert passes(1, 128.0, ob, lines, min_rr=1.0) is True


def test_min_rr_gate_treats_missing_target_as_zero_reward() -> None:
    passes = ConfluenceStrategy._passes_min_rr
    ob = _order_block(_BULL, top=200.0, bottom=120.0)
    assert passes(1, 128.0, ob, lines={}, min_rr=0.01) is False


def test_min_rr_gate_works_for_short_direction() -> None:
    passes = ConfluenceStrategy._passes_min_rr
    ob = _order_block(_BEAR, top=110.0, bottom=0.0)  # risk = 110 - 100 = 10
    lines = {"ema_60": 80.0}  # reward = 100 - 80 = 20 -> rr = 2.0
    assert passes(-1, 100.0, ob, lines, min_rr=1.5) is True
    assert passes(-1, 100.0, ob, lines, min_rr=3.0) is False


def test_min_rr_gate_rejects_when_risk_distance_not_positive() -> None:
    passes = ConfluenceStrategy._passes_min_rr
    # 오더블록 경계가 진입가보다 유리한 쪽(비정상 입력) -> risk <= 0 -> 항상 기각.
    ob = _order_block(_BULL, top=200.0, bottom=140.0)
    assert passes(1, 128.0, ob, lines={"ema_60": 500.0}, min_rr=0.01) is False


def test_min_rr_gate_end_to_end_blocks_and_allows_entry() -> None:
    """`run()`을 통해 min_rr 게이트가 실제로 확정 진입을 기각/허용하는지 확인한다."""
    closes = [200.0 - i * 3.0 for i in range(25)]  # 단조 하락 -> RSI 과매도, pos=24
    df = _df(closes)
    pos = 24
    entry_price = closes[pos]  # 128.0
    # tp_vwma_length=2 -> 진입 스냅샷 vwma = (close[23]+close[24])/2 = 129.5 (진입가 너머 1.5).
    base = ConfluenceParams(
        use_line_take_profit=True,
        tp_ema_lengths=(),
        tp_vwma_length=2,
        use_order_block_stop=False,
    )

    tight_ob = _order_block(_BULL, bottom=entry_price - 20.0)  # risk=20 -> rr=1.5/20 낮음
    blocked = ConfluenceStrategy(base.model_copy(update={"min_rr": 1.0})).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, entry_price, tight_ob)])
    )
    assert blocked.entries[0].confirmed is False

    close_ob = _order_block(_BULL, bottom=entry_price - 1.0)  # risk=1 -> rr=1.5/1 충분
    allowed = ConfluenceStrategy(base.model_copy(update={"min_rr": 1.0})).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, entry_price, close_ob)])
    )
    assert allowed.entries[0].confirmed is True

    # 게이트가 꺼져 있으면(min_rr=None) risk가 커도 그대로 확정(현행 동작 보존).
    unblocked = ConfluenceStrategy(base).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, entry_price, tight_ob)])
    )
    assert unblocked.entries[0].confirmed is True


def test_deviation_gate_passes_when_sufficiently_below_ema() -> None:
    passes = ConfluenceStrategy._passes_deviation_gate
    assert passes(0, closes=[90.0], ema_vals=[100.0], threshold=-0.05) is True


def test_deviation_gate_rejects_when_not_far_enough_below() -> None:
    passes = ConfluenceStrategy._passes_deviation_gate
    assert passes(0, closes=[98.0], ema_vals=[100.0], threshold=-0.05) is False


def test_deviation_gate_rejects_on_nan_ema() -> None:
    passes = ConfluenceStrategy._passes_deviation_gate
    assert passes(0, closes=[90.0], ema_vals=[float("nan")], threshold=-0.05) is False


def test_deviation_gate_end_to_end_blocks_and_allows_long_entry() -> None:
    """`run()`을 통해 롱 이격도 게이트가 실제로 동작하는지 확인한다(EMA 5, 하락 추세)."""
    closes = [200.0 - i * 3.0 for i in range(25)]  # 단조 하락 -> RSI 과매도, 종가<EMA5
    df = _df(closes)
    pos = 24
    signals = [_signal(_BULL, pos, closes[pos])]
    base = ConfluenceParams(
        use_line_take_profit=False,
        use_order_block_stop=False,
        long_deviation_gate_ema_length=5,
    )

    lenient = ConfluenceStrategy(base.model_copy(update={"long_max_deviation": -0.005})).run(
        df, OrderBlockResult(order_blocks=[], signals=signals)
    )
    assert lenient.entries[0].confirmed is True

    strict = ConfluenceStrategy(base.model_copy(update={"long_max_deviation": -0.5})).run(
        df, OrderBlockResult(order_blocks=[], signals=signals)
    )
    assert strict.entries[0].confirmed is False

    # 게이트가 꺼져 있으면(long_max_deviation=None) 현행 동작 보존.
    off = ConfluenceStrategy(base).run(df, OrderBlockResult(order_blocks=[], signals=signals))
    assert off.entries[0].confirmed is True


def test_deviation_gate_does_not_affect_short_entries() -> None:
    closes = [100.0 + i * 3.0 for i in range(25)]  # 상승 -> RSI 과매수, 숏 신호
    df = _df(closes)
    pos = 24
    params = ConfluenceParams(
        use_line_take_profit=False,
        use_order_block_stop=False,
        long_deviation_gate_ema_length=5,
        long_max_deviation=-0.5,  # 매우 엄격해도 숏에는 적용되지 않는다.
    )
    result = ConfluenceStrategy(params).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BEAR, pos, closes[pos])])
    )
    assert result.entries[0].confirmed is True


def test_short_enabled_false_rejects_short_even_when_rsi_overbought() -> None:
    closes = [100.0 + i * 3.0 for i in range(25)]  # 상승 -> RSI 과매수 -> 숏 조건 충족
    df = _df(closes)
    pos = 24
    params = ConfluenceParams(
        use_line_take_profit=False, use_order_block_stop=False, short_enabled=False
    )
    result = ConfluenceStrategy(params).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BEAR, pos, closes[pos])])
    )
    assert result.entries[0].confirmed is False


def test_short_enabled_false_does_not_affect_long_entries() -> None:
    closes = [200.0 - i * 3.0 for i in range(25)]  # 하락 -> RSI 과매도 -> 롱 조건 충족
    df = _df(closes)
    pos = 24
    params = ConfluenceParams(
        use_line_take_profit=False, use_order_block_stop=False, short_enabled=False
    )
    result = ConfluenceStrategy(params).run(
        df, OrderBlockResult(order_blocks=[], signals=[_signal(_BULL, pos, closes[pos])])
    )
    assert result.entries[0].confirmed is True
