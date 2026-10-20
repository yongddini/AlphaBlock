"""WAN-463 — 엔진 C 숏 배율(`short_mult`)을 동작으로 고정한다.

1. 기본값 1.0은 예전과 비트 동일하다.
2. 숏 보유일만 바뀐다 — 롱이 이어지는 날(비용도 같은 날)의 수익은 그대로다.
3. 숏 보유일 배율은 정확히 s배다(보유 배율 관측 `held`로).
4. 0 이하는 거부한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import wan449_tsmom_second_engine as e
from backtest import wan463_short_side_mult as w


def _series(n: int = 900, seed: int = 463) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 0.03, n) + np.where((np.arange(n) // 150) % 2 == 0, 0.004, -0.004)
    idx = pd.date_range("2018-01-01", periods=n, freq="D")
    ret = pd.Series(r, index=idx)
    fund = pd.Series(0.0001, index=idx)
    low = ret - 0.01
    high = ret + 0.01
    return ret, fund, low, high


def test_default_is_bit_identical() -> None:
    ret, fund, low, high = _series()
    a = e.tsmom_returns(ret, fund, low, high)
    b = e.tsmom_returns(ret, fund, low, high, short_mult=1.0)
    pd.testing.assert_series_equal(a.ret, b.ret)
    pd.testing.assert_series_equal(a.low, b.low)


def test_only_short_days_change() -> None:
    ret, fund, low, high = _series()
    base = e.tsmom_returns(ret, fund, low, high)
    alt = e.tsmom_returns(ret, fund, low, high, short_mult=1.5)
    assert base.held is not None and alt.held is not None
    h = base.held
    steady_long = (h > 0) & (h.shift(1).fillna(0.0) >= 0)
    assert steady_long.sum() > 50 and (h < 0).sum() > 50  # 합성 자료가 두 방향을 다 낸다
    pd.testing.assert_series_equal(base.ret[steady_long], alt.ret[steady_long])
    short = h < 0
    np.testing.assert_allclose(alt.held[short], base.held[short] * 1.5)
    np.testing.assert_allclose(alt.held[h >= 0], base.held[h >= 0])


def test_rejects_non_positive() -> None:
    ret, fund, low, high = _series(400)
    with pytest.raises(ValueError, match="short_mult"):
        e.tsmom_returns(ret, fund, low, high, short_mult=0.0)


def test_down_span_mdd_resets_peak_each_span() -> None:
    idx = pd.date_range("2020-01-01", periods=6, freq="D")
    eq = pd.Series([1.0, 1.2, 1.1, 1.3, 1.0, 1.4], index=idx)
    low = eq.copy()
    down = pd.Series([False, True, True, False, True, False], index=idx)
    # 첫 구간: 직전 종가 1.0 → 고점 1.2 → 1.1 = 8.3% · 둘째: 직전 1.3 → 1.0 = 23.1%
    assert w.down_span_mdd(eq, low, down) == pytest.approx(1 - 1.0 / 1.3)
