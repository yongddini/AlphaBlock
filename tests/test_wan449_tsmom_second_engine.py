"""WAN-449 — 시계열 모멘텀 엔진 C · 합성 · 판정 · 캐리 일 배율."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import wan449_tsmom_second_engine as e


def test_constants_are_the_issue_spec() -> None:
    assert e.LOOKBACKS == (20, 60, 120, 250)
    assert (e.VOL_TARGET, e.VOL_WINDOW, e.MAX_LEVERAGE) == (0.20, 60, 2.0)
    assert pytest.approx(0.0009) == e.COST
    assert e.FUT_FROM == "2020-09-15" and e.FRONT_END == "2024-08-08"
    assert tuple(range(2018, 2026)) == e.YEARS
    assert (e.TARGET_MEDIAN, e.MAX_NEGATIVE_YEARS) == (0.20, 1)
    assert e.CARRY_FRACTION == 0.5


def _walk(n: int, seed: int = 449) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=n, freq="D")
    return pd.Series(rng.normal(0.001, 0.03, n), index=idx)


def test_weights_have_no_lookahead() -> None:
    """미래 날을 잘라도 그 전에 정한 배율은 비트 단위로 같다."""
    r = _walk(600)
    full = e.tsmom_weights((1 + r).cumprod(), r)
    cut = r.iloc[:400]
    part = e.tsmom_weights((1 + cut).cumprod(), cut)
    pd.testing.assert_series_equal(full.iloc[:400], part)


def test_weights_bounded_and_zero_during_warmup() -> None:
    r = _walk(600)
    w = e.tsmom_weights((1 + r).cumprod(), r)
    assert float(w.abs().max()) <= e.MAX_LEVERAGE + 1e-12
    assert (w.iloc[: max(e.LOOKBACKS)] == 0.0).all()  # 가장 긴 L이 차기 전엔 신호 NaN → 0


def test_position_applies_to_next_day_and_funding_sign() -> None:
    """오늘 정한 배율은 다음 날 수익에만 걸린다 · 롱은 양(+) 펀딩을 낸다."""
    r = _walk(400)
    zero = pd.Series(0.0, index=r.index)
    base = e.tsmom_returns(r, zero).ret
    bumped = r.copy()
    bumped.iloc[-1] += 0.5  # 마지막 날 수익만 바꾼다
    out = e.tsmom_returns(bumped, zero).ret
    pd.testing.assert_series_equal(base.iloc[:-1], out.iloc[:-1])
    w = e.tsmom_weights((1 + r).cumprod(), r)
    held = w.shift(1).fillna(0.0)
    fund = pd.Series(0.001, index=r.index)
    diff = e.tsmom_returns(r, zero).ret - e.tsmom_returns(r, fund).ret
    np.testing.assert_allclose(diff.to_numpy(), held.to_numpy() * 0.001, atol=1e-15)


def test_adverse_low_uses_low_for_long_and_high_for_short() -> None:
    r = _walk(400)
    zero = pd.Series(0.0, index=r.index)
    low_r = r - 0.05
    high_r = r + 0.05
    eng = e.tsmom_returns(r, zero, low_r, high_r)
    held = e.tsmom_weights((1 + r).cumprod(), r).shift(1).fillna(0.0)
    gap = (eng.ret - eng.low).to_numpy()
    np.testing.assert_allclose(gap, np.abs(held.to_numpy()) * 0.05, atol=1e-12)
    assert (eng.low <= eng.ret + 1e-15).all()


def test_combine_with_zero_engine_is_the_arm() -> None:
    idx = pd.date_range("2020-01-01", periods=50, freq="D")
    ra = pd.Series(np.linspace(-0.02, 0.03, 50), index=idx)
    la = ra - 0.01
    rc = pd.Series(0.01, index=idx)
    eq, low = e.combine(ra, la, rc, rc, 0.0, 1.0)
    np.testing.assert_allclose(eq.to_numpy(), (1 + ra).cumprod().to_numpy())
    eq2, _ = e.combine(ra, la, rc, rc, 1.0, 2.0)
    np.testing.assert_allclose(eq2.to_numpy(), (1 + 2 * (ra + rc)).cumprod().to_numpy())


def test_fit_lambda_hits_the_target() -> None:
    r = _walk(1500, seed=7)
    lam = e.fit_lambda(r, r - 0.01, r * 0, r * 0, 1.0, 0.35)
    assert e.mdd_of(*e.combine(r, r - 0.01, r * 0, r * 0, 1.0, lam)) == pytest.approx(
        0.35, abs=1e-3
    )


def test_verdict_rule() -> None:
    good = pd.Series([0.25, 0.3, -0.1, 0.4, 0.21, 0.5, 0.22, 0.23], index=list(e.YEARS))
    ok, med, neg = e.verdict(good)
    assert ok and neg == 1 and med >= 0.20
    two_neg = good.copy()
    two_neg[2019] = -0.01
    assert not e.verdict(two_neg)[0]
    low_med = pd.Series([0.1] * 8, index=list(e.YEARS))
    assert not e.verdict(low_med)[0]
    with pytest.raises(ValueError):
        e.verdict(good.drop(2020))


@pytest.mark.parametrize("unit", ["ns", "us", "ms", "s"])
def test_to_ms_is_resolution_independent(unit: str) -> None:
    """`asi8 // 1e6`은 ns가 아닌 해상도에서 조용히 틀린다 — 캐리가 전부 0으로 나온 실제 "
    "사고(개발 중)."""
    idx = pd.DatetimeIndex(["2021-01-01", "2021-01-02"]).as_unit(unit)
    assert list(e._to_ms(idx)) == [1_609_459_200_000, 1_609_545_600_000]


def test_carry_daily_moves_and_guards_hard_zero() -> None:
    idx = pd.date_range("2021-01-01", periods=5, freq="D").as_unit("ms")
    day = 86_400_000
    t0 = 1_609_459_200_000
    rows = {
        e.ASSETS[0]: [(t0 + day + 3_600_000, 0.001)],
        e.ASSETS[1]: [(t0 + day + 3_600_000, 0.001)],
    }
    out = e.carry_daily(rows, idx)
    assert out.iloc[1] == pytest.approx(0.5 * 0.001)  # 둘째 날 끝까지 정산된 펀딩 · 자본 절반
    far = {e.ASSETS[0]: [(t0 + 100 * day, 0.001)]}
    with pytest.raises(ValueError, match="전부 0"):
        e.carry_daily(far, idx)
