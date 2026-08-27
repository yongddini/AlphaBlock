"""WAN-372: 봉내 실시간 MACD 상태 머신 — 수식·시점·색 규칙을 **동작으로** 고정한다.

고정하는 것 넷:

1. **수식 패리티** — 확정봉만 커밋한 뒤 다음 종가를 얹으면 `indicators.ema`로 만든 MACD
   히스토그램과 **비트 단위로 같다**(`RealtimeRsi`가 `indicators.rsi`와 맺은 계약의 MACD 판).
2. **시점** — `value`는 상태를 굴리지 않고, `hist_prev`는 언제나 **직전 확정봉** 값이다.
   미래 종가를 하나도 안 본다(같은 접두사면 뒤에 무엇이 오든 같은 값).
3. **워밍업은 지어내지 않는다** — `slow + signal`봉 전에는 `None`이고, 그 셋업은 표에서
   「워밍업」으로 보인다.
4. **색 규칙은 Pine 원본 그대로** — 경계(`hist == 0` · `hist == hist[1]`)가 원본과 같은
   쪽으로 떨어진다. 부등호를 한 칸 옮기면 같은 봉이 다른 색이 되므로 값으로 못 박는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy.indicators import ema
from strategy.realtime_macd import (
    COLOR_ORDER,
    DEFAULT_MACD_PARAMS,
    MacdColor,
    MacdParams,
    MacdSample,
    RealtimeMacd,
    macd_color,
)


def _closes(seed: int = 7, bars: int = 240) -> list[float]:
    rng = np.random.default_rng(seed)
    return [float(v) for v in 100.0 + np.cumsum(rng.normal(0.0, 1.0, bars))]


def _reference_hist(closes: list[float], params: MacdParams = DEFAULT_MACD_PARAMS) -> list[float]:
    """`strategy.indicators.ema`로 만든 확정봉 MACD 히스토그램 — 패리티의 기준."""
    frame = pd.DataFrame({"close": closes})
    macd = ema(frame, params.fast_length) - ema(frame, params.slow_length)
    signal = pd.DataFrame({"close": macd}).pipe(ema, params.signal_length)
    return [float(v) for v in (macd - signal).tolist()]


# --------------------------------------------------------------------------- #
# 1 · 수식 패리티
# --------------------------------------------------------------------------- #


def test_live_value_matches_the_indicator_series_bit_for_bit() -> None:
    """`closes[:i]`를 커밋한 뒤 `value(closes[i])` == 지표 시리즈의 i번째 히스토그램.

    이 등식이 「봉이 닫히는 순간 라이브 값 = 확정 값」이라는 계약이다 — 깨지면 화면(라이브)과
    표(백테)가 같은 봉에서 다른 색을 낸다.
    """
    closes = _closes()
    expected = _reference_hist(closes)
    warmup = DEFAULT_MACD_PARAMS.warmup_bars
    checked = 0
    for i in range(warmup, len(closes)):
        sample = RealtimeMacd.seed_from_closed(closes, end=i).value(closes[i])
        assert sample is not None
        assert sample.hist == pytest.approx(expected[i], abs=1e-12)
        assert sample.hist_prev == pytest.approx(expected[i - 1], abs=1e-12)
        checked += 1
    assert checked > 100, "비교한 봉이 너무 적어 이 테스트는 아무것도 안 지킨다."


def test_incremental_commit_equals_seeding_from_scratch() -> None:
    """한 봉씩 굴린 상태 == 처음부터 시딩한 상태 — 증분 시더가 기대는 성질이다."""
    closes = _closes(seed=3, bars=120)
    rolling = RealtimeMacd()
    for i, close in enumerate(closes):
        fresh = RealtimeMacd.seed_from_closed(closes, end=i)
        assert (fresh.fast_ema, fresh.slow_ema) == (rolling.fast_ema, rolling.slow_ema)
        assert (fresh.signal_ema, fresh.closed_hist) == (rolling.signal_ema, rolling.closed_hist)
        assert fresh.committed == rolling.committed
        rolling.commit(close)


# --------------------------------------------------------------------------- #
# 2 · 시점 — 미래를 안 본다, 상태를 안 굴린다
# --------------------------------------------------------------------------- #


def test_value_does_not_mutate_state() -> None:
    """`value`는 조회다 — 상태를 굴리면 같은 봉을 두 번 물었을 때 답이 달라진다."""
    closes = _closes(bars=100)
    state = RealtimeMacd.seed_from_closed(closes, end=60)
    before = (state.fast_ema, state.slow_ema, state.signal_ema, state.closed_hist, state.committed)
    first = state.value(closes[60])
    second = state.value(closes[60])
    after = (state.fast_ema, state.slow_ema, state.signal_ema, state.closed_hist, state.committed)
    assert before == after
    assert first == second


def test_future_closes_never_reach_the_value() -> None:
    """같은 접두사면 **뒤에 무엇이 오든** 같은 값이다 = 룩어헤드가 없다."""
    closes = _closes(bars=140)
    tampered = [*closes[:80], *(c * 3.0 for c in closes[80:])]
    a = RealtimeMacd.seed_from_closed(closes, end=80).value(closes[80])
    b = RealtimeMacd.seed_from_closed(tampered, end=80).value(closes[80])
    assert a is not None and a == b


def test_hist_prev_is_the_last_closed_bar_not_the_live_one() -> None:
    """`hist[1]`은 직전 **확정봉** 값이다 — 현재가를 아무리 흔들어도 안 움직인다."""
    closes = _closes(bars=100)
    state = RealtimeMacd.seed_from_closed(closes, end=70)
    low = state.value(closes[70] * 0.9)
    high = state.value(closes[70] * 1.1)
    assert low is not None and high is not None
    assert low.hist_prev == high.hist_prev == state.closed_hist
    assert low.hist != high.hist  # 라이브 쪽은 움직인다(그래서 봉 안에서 색이 바뀔 수 있다).


def test_copy_is_independent() -> None:
    """사본을 굴려도 원본이 안 움직인다 — 시더가 이 성질에 기댄다."""
    closes = _closes(bars=80)
    state = RealtimeMacd.seed_from_closed(closes, end=50)
    clone = state.copy()
    clone.commit(999.0)
    assert clone.committed == state.committed + 1
    assert clone.closed_hist != state.closed_hist


# --------------------------------------------------------------------------- #
# 3 · 워밍업은 지어내지 않는다
# --------------------------------------------------------------------------- #


def test_warmup_returns_none_and_the_boundary_is_exact() -> None:
    closes = _closes(bars=80)
    warmup = DEFAULT_MACD_PARAMS.warmup_bars
    assert warmup == 26 + 9
    for i in range(warmup):
        assert RealtimeMacd.seed_from_closed(closes, end=i).value(closes[i]) is None, i
    assert RealtimeMacd.seed_from_closed(closes, end=warmup).value(closes[warmup]) is not None


def test_nan_price_yields_no_value() -> None:
    """값을 지어내느니 「없다」고 한다."""
    closes = _closes(bars=80)
    assert RealtimeMacd.seed_from_closed(closes, end=60).value(float("nan")) is None


def test_params_reject_a_backwards_pair() -> None:
    """빠른 EMA가 느린 EMA보다 길면 색 규칙의 뜻이 뒤집힌다 — 조용히 돌지 않는다."""
    with pytest.raises(ValueError, match="짧아야"):
        MacdParams(fast_length=26, slow_length=12)
    with pytest.raises(ValueError, match="1 이상"):
        MacdParams(signal_length=0)


# --------------------------------------------------------------------------- #
# 4 · 색 규칙 — Pine 원본 그대로
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("hist", "hist_prev", "expected"),
    [
        (2.0, 1.0, MacdColor.STRONG_GREEN),  # 양수 · 상승 가속
        (1.0, 2.0, MacdColor.WEAK_GREEN),  # 양수 · 상승 둔화
        (-1.0, -2.0, MacdColor.WEAK_RED),  # 음수 · 하락 둔화
        (-2.0, -1.0, MacdColor.STRONG_RED),  # 음수 · 하락 가속
        (0.0, -1.0, MacdColor.STRONG_GREEN),  # `hist == 0`은 **초록 쪽**(원본 `hist >= 0`)
        (0.0, 1.0, MacdColor.WEAK_GREEN),
        (1.0, 1.0, MacdColor.WEAK_GREEN),  # 변화 없음은 **연한 쪽**(`hist[1] < hist`가 거짓)
        (-1.0, -1.0, MacdColor.STRONG_RED),
    ],
)
def test_color_rule_matches_pine(hist: float, hist_prev: float, expected: MacdColor) -> None:
    assert macd_color(hist, hist_prev) is expected
    assert MacdSample(hist=hist, hist_prev=hist_prev).color is expected


def test_color_metadata_is_complete_and_distinct() -> None:
    """네 색이 전부 · 이름과 색 코드가 서로 다르다(표에서 두 색이 한 칸으로 접히지 않는다)."""
    assert set(COLOR_ORDER) == set(MacdColor)
    assert len({c.label for c in COLOR_ORDER}) == 4
    assert len({c.hex for c in COLOR_ORDER}) == 4
    assert MacdColor.STRONG_RED.hex == "#FF5252"
    assert MacdColor.STRONG_GREEN.hex == "#26A69A"
