"""WAN-90 이탈(MFE/MAE)·본절-러너 계측 테스트.

완료기준: 합성 데이터로 MFE/MAE·E[러너] 계산의 정확성을 검증한다. 1R = 진입가 →
무효화 경계(entry=100·stop=90 → 1R=10). 러너는 1.5R(=115)에 무장하면 손절을 본절(100)로
옮기고, 이후 진입가 복귀 시 0R·끝까지 안 오면 마지막 종가 R로 청산한다.
"""

from __future__ import annotations

import pytest

from backtest.substep import SubStep
from backtest.wan90_mfe_distribution import simulate_excursion


def _step(high: float, low: float, close: float) -> SubStep:
    return SubStep(time=0, high=high, low=low, close=close, htf_bar_time=0)


def test_runner_rides_to_end() -> None:
    """1.5R 무장 후 본절로 안 돌아오고 계속 오르면 러너는 마지막 종가 R로 청산(미실현)."""
    steps = [
        _step(high=101, low=95, close=100),  # fav 0.1R — 무장 전
        _step(high=116, low=105, close=115),  # fav 1.6R → 무장, 저가 105>100
        _step(high=131, low=120, close=130),  # 끝까지 상승
    ]
    exc = simulate_excursion(steps, entry_price=100.0, stop_price=90.0, is_long=True)
    assert exc is not None
    assert exc.reached_arm is True
    assert exc.mfe_r == pytest.approx(3.1)  # (131 - 100) / 10
    assert exc.mae_r == pytest.approx(-0.5)  # (95 - 100) / 10
    assert exc.runner_open_at_end is True
    assert exc.runner_r == pytest.approx(3.0)  # (130 - 100) / 10


def test_runner_returns_to_breakeven() -> None:
    """1.5R 무장 후 진입가로 되돌아오면 러너는 0R(본절 청산)."""
    steps = [
        _step(high=101, low=98, close=100),
        _step(high=116, low=110, close=115),  # 무장
        _step(high=118, low=99, close=101),  # 저가 99 ≤ 진입가 100 → 본절
    ]
    exc = simulate_excursion(steps, entry_price=100.0, stop_price=90.0, is_long=True)
    assert exc is not None
    assert exc.reached_arm is True
    assert exc.runner_open_at_end is False
    assert exc.runner_r == pytest.approx(0.0)
    assert exc.mfe_r == pytest.approx(1.8)  # (118 - 100) / 10


def test_runner_none_when_never_reaches_arm() -> None:
    """1.5R에 못 닿으면 러너 대상이 아니다(runner_r None)."""
    steps = [
        _step(high=112, low=98, close=105),  # fav 1.2R < 1.5R
        _step(high=95, low=88, close=90),
    ]
    exc = simulate_excursion(steps, entry_price=100.0, stop_price=90.0, is_long=True)
    assert exc is not None
    assert exc.reached_arm is False
    assert exc.runner_r is None
    assert exc.mfe_r == pytest.approx(1.2)  # (112 - 100) / 10
    assert exc.mae_r == pytest.approx(-1.2)  # (88 - 100) / 10


def test_runner_short_symmetry() -> None:
    """숏: 유리=가격 하락. entry=100·stop=110 → 1R=10. 무장 후 계속 하락하면 미실현 R."""
    steps = [
        _step(high=100, low=99, close=99),  # fav 0.1R
        _step(high=99, low=83, close=85),  # fav 1.7R → 무장, 고가 99<100
        _step(high=88, low=80, close=82),  # 끝까지 하락
    ]
    exc = simulate_excursion(steps, entry_price=100.0, stop_price=110.0, is_long=False)
    assert exc is not None
    assert exc.reached_arm is True
    assert exc.mfe_r == pytest.approx(2.0)  # (100 - 80) / 10
    assert exc.runner_open_at_end is True
    assert exc.runner_r == pytest.approx(1.8)  # (100 - 82) / 10


def test_breakeven_on_arming_bar_is_conservative() -> None:
    """무장 봉에서 진입가 터치가 동시 성립하면 보수적으로 본절(0R) 처리한다."""
    steps = [_step(high=116, low=99, close=110)]  # 같은 봉에서 1.6R 무장 + 저가 99≤100
    exc = simulate_excursion(steps, entry_price=100.0, stop_price=90.0, is_long=True)
    assert exc is not None
    assert exc.reached_arm is True
    assert exc.runner_r == pytest.approx(0.0)
    assert exc.runner_open_at_end is False


def test_zero_risk_returns_none() -> None:
    """진입가=손절가면 1R을 못 재므로 None."""
    steps = [_step(high=101, low=99, close=100)]
    assert simulate_excursion(steps, entry_price=100.0, stop_price=100.0, is_long=True) is None
