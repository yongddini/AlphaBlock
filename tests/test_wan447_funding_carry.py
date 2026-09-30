"""WAN-447 — 펀딩 캐리 합성: 바스켓 · 정산 시각 · 캐리 지수 · 판정."""

from __future__ import annotations

import numpy as np
import pytest

from backtest import wan447_funding_carry as fc

H = fc.HOUR_MS


def test_constants_are_the_issue_spec() -> None:
    assert fc.VERDICT_FRACTION == 0.5
    assert fc.TARGET_CAGR == 0.25
    assert fc.TRAILING_DAYS == 365
    assert fc.FRACTIONS == (0.0, 0.5, 1.0)
    assert fc.CARRY_SYMBOLS == ("BTC/USDT:USDT", "ETH/USDT:USDT")


def test_basket_averages_only_symbols_present_at_that_settlement() -> None:
    f = fc.basket({"A": [(0, 0.001), (8 * H + 3, 0.003)], "B": [(8 * H, 0.001)]})
    assert list(f.times) == [0, 8 * H]  # 몇 ms 어긋난 정산은 같은 정시로 묶는다
    assert f.rates == pytest.approx([0.001, 0.002])


def test_carry_index_counts_only_settled_funding() -> None:
    f = fc.Funding(np.array([10, 20], dtype=np.int64), np.array([0.01, -0.02]))
    ci = fc.carry_index(f, np.array([0, 9, 10, 19, 20, 30]), 0.5)
    # 정산 시각 이전에는 아무것도 안 받는다(룩어헤드 없음) · 그 시각부터 받는다
    assert ci == pytest.approx([1.0, 1.0, 1.005, 1.005, 1.005 * 0.99, 1.005 * 0.99])


def test_zero_fraction_is_identity_and_bad_fraction_rejected() -> None:
    f = fc.Funding(np.array([10], dtype=np.int64), np.array([0.5]))
    assert fc.carry_index(f, np.array([0, 100]), 0.0) == pytest.approx([1.0, 1.0])
    with pytest.raises(ValueError):
        fc.carry_index(f, np.array([0]), 1.5)


def test_trailing_mean_and_flat_keep_settlement_times() -> None:
    day = fc.DAY_MS
    f = fc.Funding(np.array([0, 400 * day, 500 * day], dtype=np.int64), np.array([1.0, 0.2, 0.4]))
    assert fc.trailing_mean(f, 500 * day) == pytest.approx(0.3)  # 365일 밖 0번은 뺀다
    g = fc.flat(f, 0.3)
    assert list(g.times) == list(f.times) and g.rates == pytest.approx([0.3] * 3)
    with pytest.raises(ValueError):
        fc.trailing_mean(f, -1)


def _row(series: str, fraction: float, full: float, back: float) -> fc.Result:
    return fc.Result(
        stage=fc.STAGE_LIQ,
        config=fc.STAGE_LIQ,
        series=series,
        fraction=fraction,
        trades=10,
        front_risk=0.02,
        front_cagr=0.1,
        back_cagr=back,
        back_mdd=0.2,
        full_risk=0.02,
        full_cagr=full,
        full_stop_risk=0.02,
        full_stop_cagr=full,
    )


def _rows(actual: float, trailing: float, back0: float, back5: float) -> list[fc.Result]:
    return [
        _row(fc.SERIES_ACTUAL, 0.0, 0.185, back0),
        _row(fc.SERIES_ACTUAL, 0.5, actual, back5),
        _row(fc.SERIES_TRAILING, 0.0, 0.185, back0),
        _row(fc.SERIES_TRAILING, 0.5, trailing, back5),
    ]


def test_verdict_three_branches() -> None:
    assert fc.verdict(_rows(0.26, 0.25, 0.10, 0.12))[0] == "캐리 합성으로 25% 달성"
    assert fc.verdict(_rows(0.26, 0.21, 0.10, 0.12))[0].startswith("과거 펀딩에만")
    assert fc.verdict(_rows(0.24, 0.21, 0.10, 0.12))[0] == "미달"
    with pytest.raises(ValueError):
        fc.verdict(_rows(0.26, 0.25, 0.1, 0.1)[:3])
