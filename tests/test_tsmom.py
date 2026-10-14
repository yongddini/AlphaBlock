"""엔진 C 목표 배율 단일 소스(WAN-451) — 백테스트 경로와 비트 동일 · 인과 · 거부 규칙."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import trade_store
from backtest import wan449_tsmom_second_engine as e449
from strategy import tsmom

DAY = tsmom.DAY_MS
T0 = 1_600_000_000_000 // DAY * DAY


def _walk(n: int, seed: int = 451) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0005, 0.035, n)
    close = pd.Series(30_000.0 * np.cumprod(1.0 + r))
    times = pd.Series(T0 + DAY * np.arange(n), dtype="int64")
    return times, close


def _backtest_weights(close: pd.Series) -> pd.Series:
    """`wan449.tsmom_returns`가 쓰는 경로 그대로(수익 → 누적곱 가격 → 배율)."""
    returns = close.pct_change()
    price = (1.0 + returns.fillna(0.0)).cumprod()
    return e449.tsmom_weights(price, returns)


def test_backtest_module_uses_the_single_source() -> None:
    """정본 모듈이 식을 다시 적지 않고 이 함수·상수를 그대로 쓴다(두 벌로 갈라지지 않게)."""
    assert e449.tsmom_weights is tsmom.tsmom_weights
    assert e449.ASSETS == tsmom.ASSETS
    assert e449.LOOKBACKS == tsmom.LOOKBACKS == (20, 60, 120, 250)
    assert (e449.VOL_TARGET, e449.VOL_WINDOW, e449.MAX_LEVERAGE) == (0.20, 60, 2.0)
    assert e449.COST == tsmom.COST == 0.0009
    assert e449.DAYS_PER_YEAR == tsmom.DAYS_PER_YEAR == 365.0


def test_canonical_sizing_constants() -> None:
    """이슈가 못 박은 정본 k·λ — 자산당 명목 = 0.7036 × w × 총자본."""
    assert (tsmom.K, tsmom.LAMBDA, tsmom.ASSET_SHARE) == (1.590, 0.885, 0.5)
    assert tsmom.notional_fraction(1.0) == pytest.approx(0.703575)
    assert tsmom.notional_fraction(-2.0) == pytest.approx(-1.40715)


def test_target_is_bit_identical_to_backtest_last_weight() -> None:
    times, close = _walk(400)
    got = tsmom.target_from_daily_closes("BTC/USDT:USDT", times, close)
    assert got.weight == _backtest_weights(close).iloc[-1]
    assert got.as_of_ms == int(times.iloc[-1])
    assert got.effective_from_ms == int(times.iloc[-1]) + DAY
    assert got.notional_fraction == tsmom.notional_fraction(got.weight)


def test_target_never_reads_the_future() -> None:
    """그날까지의 봉만으로 낸 목표 ≡ 전체 시계열로 낸 그날 값(뒤 봉이 앞을 안 바꾼다)."""
    times, close = _walk(320, seed=7)
    full = _backtest_weights(close)
    for i in range(240, 320):
        t = tsmom.target_from_daily_closes("ETH/USDT:USDT", times[: i + 1], close[: i + 1])
        assert t.weight == full.iloc[i], i


def test_warmup_gives_zero_weight_until_longest_lookback_exists() -> None:
    times, close = _walk(tsmom.MIN_HISTORY)
    short = tsmom.target_from_daily_closes("BTC/USDT:USDT", times[:-1], close[:-1])
    assert np.isnan(short.signal) and short.weight == 0.0
    ready = tsmom.target_from_daily_closes("BTC/USDT:USDT", times, close)
    assert ready.signal in {-1.0, -0.5, 0.0, 0.5, 1.0}
    assert ready.weight == _backtest_weights(close).iloc[-1]


def test_weight_is_clipped_to_two() -> None:
    """낮은 변동성 + 강한 추세면 배율이 상한 2에 걸린다."""
    n = 300
    times = pd.Series(T0 + DAY * np.arange(n), dtype="int64")
    rng = np.random.default_rng(1)
    close = pd.Series(100.0 * np.cumprod(1.0 + 0.002 + rng.normal(0, 0.0005, n)))
    t = tsmom.target_from_daily_closes("BTC/USDT:USDT", times, close)
    assert t.signal == 1.0 and t.weight == tsmom.MAX_LEVERAGE


@pytest.mark.parametrize(
    ("times", "close", "msg"),
    [
        ([], [], "확정 일봉이 없다"),
        ([T0, T0 + DAY], [1.0], "길이가 다르다"),
        ([T0, T0 + DAY, T0 + 3 * DAY], [1.0, 1.1, 1.2], "연속이 아니다"),
        ([T0, T0 + DAY], [1.0, float("nan")], "결측"),
        ([T0, T0 + DAY], [1.0, 0.0], "비양수"),
    ],
)
def test_refuses_to_fabricate(times: list[int], close: list[float], msg: str) -> None:
    """수집 공백이 있으면 「N일 전」이 다른 날을 가리킨다 — 지어내지 않고 거부한다."""
    with pytest.raises(ValueError, match=msg):
        tsmom.target_from_daily_closes(
            "BTC/USDT:USDT", pd.Series(times, dtype="int64"), pd.Series(close, dtype="float64")
        )


def test_declared_non_engine_so_caches_survive() -> None:
    """오더블록 백테 수치를 안 움직이므로 엔진 지문에 안 들어간다(캐시 통째 무효화 방지)."""
    assert "strategy/tsmom.py" not in trade_store.ENGINE_SOURCE_FILES
    assert "strategy/tsmom.py" in trade_store._ENGINE_NON_SOURCE
    assert trade_store.scan_engine_source_tree() == []
