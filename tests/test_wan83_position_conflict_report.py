"""WAN-83: 포지션 충돌 계측 리포트의 분류 로직 테스트.

실데이터 없이 `_Candidate`를 합성해 `classify`/`_survivor_ids`의 핵심 규칙을 고정한다:
포지션 충돌로 스킵된 tap_index=0 후보가 재탭 여부·체결 여부로 어떻게 갈리는지, 그리고
series(개별 시리즈) 범위와 global(전역 포지션 공유) 범위가 실제로 다른 답을 내는지.
"""

from __future__ import annotations

from backtest.models import ExitReason, PositionSide
from backtest.wan83_position_conflict_report import classify
from backtest.zone_limit_backtest import _Candidate
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockSignal

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"
_HOUR = 60 * 60_000


def _cand(*, entry: int, exit_: int, tap_index: int, zone_key: frozenset[int] | None) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry,
        entry_price=100.0,
        exit_time=exit_,
        exit_price=101.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=95.0,
        tap_index=tap_index,
        zone_key=zone_key,
    )


def _retap_signal(*, trigger_time: int, zone_key: frozenset[int]) -> OrderBlockSignal:
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=105.0,
        bottom=100.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=30.0,
        ob_low_volume=10.0,
        ob_high_volume=20.0,
    )
    return OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=trigger_time,
        price=102.0,
        order_block=ob,
        status="active",
        tap_index=1,
        zone_key=zone_key,
    )


def test_tap0_survivor_is_not_counted_as_dropped() -> None:
    """포지션 충돌이 없으면(겹치는 후보 없음) tap0는 그대로 살아남는다."""
    tap0 = _cand(entry=0, exit_=_HOUR, tap_index=0, zone_key=frozenset({0}))
    tagged = [(_SYMBOL, _TF, tap0)]

    counts = classify(tagged, {})

    assert counts.tap0_filled == 1
    assert counts.dropped_by_position == 0


def test_dropped_tap0_with_recovered_retap() -> None:
    """겹쳐서 스킵된 tap0가 나중에 같은 존에서 재탭돼 체결되면 recovered로 센다."""
    blocking = _cand(entry=0, exit_=3 * _HOUR, tap_index=0, zone_key=frozenset({99}))
    tap0 = _cand(entry=_HOUR, exit_=2 * _HOUR, tap_index=0, zone_key=frozenset({0}))
    retap_filled = _cand(entry=5 * _HOUR, exit_=6 * _HOUR, tap_index=1, zone_key=frozenset({0}))
    tagged = [
        (_SYMBOL, _TF, blocking),
        (_SYMBOL, _TF, tap0),
        (_SYMBOL, _TF, retap_filled),
    ]
    retap_signal = _retap_signal(trigger_time=5 * _HOUR, zone_key=frozenset({0}))

    counts = classify(tagged, {(_SYMBOL, _TF): [retap_signal]})

    assert counts.tap0_filled == 2  # blocking도 다른 존의 tap0(선점한 포지션)다.
    assert counts.dropped_by_position == 1
    assert counts.dropped_with_retap == 1
    assert counts.dropped_retap_recovered == 1
    assert counts.dropped_retap_blocked == 0
    assert counts.dropped_no_retap == 0


def test_dropped_tap0_with_retap_but_never_filled_is_blocked() -> None:
    """재탭은 있었지만(가격이 다시 존을 건드림) 그 재탭이 하나도 체결되지 않으면
    (RSI 게이트에 막힘) blocked로 센다 — 이 이슈가 측정하려는 실제 피해."""
    blocking = _cand(entry=0, exit_=3 * _HOUR, tap_index=0, zone_key=frozenset({99}))
    tap0 = _cand(entry=_HOUR, exit_=2 * _HOUR, tap_index=0, zone_key=frozenset({0}))
    tagged = [(_SYMBOL, _TF, blocking), (_SYMBOL, _TF, tap0)]
    # 재탭 이벤트는 기록됐지만(가격이 존을 다시 건드림) 그 재탭이 체결(candidate)로
    # 이어지지는 않았다 — RSI 게이트에 막혔거나 그사이 만료/무효화됐다는 뜻.
    retap_signal = _retap_signal(trigger_time=5 * _HOUR, zone_key=frozenset({0}))

    counts = classify(tagged, {(_SYMBOL, _TF): [retap_signal]})

    assert counts.dropped_by_position == 1
    assert counts.dropped_with_retap == 1
    assert counts.dropped_retap_recovered == 0
    assert counts.dropped_retap_blocked == 1


def test_dropped_tap0_never_retapped() -> None:
    """스킵된 tap0의 존이 무효화될 때까지 한 번도 다시 탭되지 않으면 no_retap으로 센다."""
    blocking = _cand(entry=0, exit_=3 * _HOUR, tap_index=0, zone_key=frozenset({99}))
    tap0 = _cand(entry=_HOUR, exit_=2 * _HOUR, tap_index=0, zone_key=frozenset({0}))
    tagged = [(_SYMBOL, _TF, blocking), (_SYMBOL, _TF, tap0)]

    counts = classify(tagged, {(_SYMBOL, _TF): []})

    assert counts.dropped_by_position == 1
    assert counts.dropped_with_retap == 0
    assert counts.dropped_no_retap == 1


def test_series_scope_excludes_other_series_candidates() -> None:
    """series 범위에서 만든 `tagged_candidates`는 그 시리즈만 담으므로, 다른 시리즈의
    후보가 없어도(빈 리스트로 시작) 정상적으로 분류된다 — global과 달리 다른 심볼·TF의
    포지션이 이 시리즈의 스킵 판정에 영향을 주지 않는다는 계약을 확인한다."""
    tap0 = _cand(entry=0, exit_=_HOUR, tap_index=0, zone_key=frozenset({0}))
    tagged = [(_SYMBOL, _TF, tap0)]

    counts = classify(tagged, {})

    assert counts.dropped_by_position == 0  # 겹치는 후보가 없으므로 스킵되지 않는다.


def test_global_scope_pools_series_and_can_drop_what_series_would_keep() -> None:
    """global 범위는 여러 시리즈의 후보를 한 포지션 예산으로 합쳐 시퀀싱한다 — 다른
    심볼의 겹치는 포지션 때문에 series 범위에서는 살아남았을 tap0가 global에서는
    스킵될 수 있다(WAN-83 코멘트의 (B) 전역 범위)."""
    other_symbol = "ETH/USDT:USDT"
    other_blocking = _cand(entry=0, exit_=3 * _HOUR, tap_index=0, zone_key=frozenset({99}))
    tap0 = _cand(entry=_HOUR, exit_=2 * _HOUR, tap_index=0, zone_key=frozenset({0}))

    series_only = [(_SYMBOL, _TF, tap0)]
    series_counts = classify(series_only, {})
    assert series_counts.dropped_by_position == 0  # 자기 시리즈 안에서는 겹치는 후보가 없다.

    pooled = [(other_symbol, _TF, other_blocking), (_SYMBOL, _TF, tap0)]
    global_counts = classify(pooled, {(_SYMBOL, _TF): []})
    assert global_counts.dropped_by_position == 1  # 다른 심볼의 포지션 때문에 스킵된다.
