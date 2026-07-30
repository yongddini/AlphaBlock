"""성과 지표 순수 함수 테스트 (WAN-8 / WAN-215).

MDD·봉 수익률·샤프는 자본곡선(리스트)만으로 정해지는 순수 함수라 백테스트 엔진과
무관하게 검증한다. 옛 A안 엔진(`run_backtest`)이 WAN-208/WAN-215로 제거되면서
`test_backtest.py`가 삭제됐지만, 그 안에 있던 이 지표 테스트들은 여기로 옮겨 보존한다.
"""

from __future__ import annotations

import pytest

from backtest import max_drawdown, sharpe_ratio
from backtest.metrics import bar_returns


def test_max_drawdown_known_series() -> None:
    assert max_drawdown([100, 120, 90, 110, 80]) == pytest.approx(40 / 120)
    assert max_drawdown([100, 110, 120]) == pytest.approx(0.0)
    assert max_drawdown([]) == pytest.approx(0.0)


def test_bar_returns() -> None:
    assert bar_returns([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])
    assert bar_returns([100.0]) == []


def test_sharpe_ratio() -> None:
    assert sharpe_ratio([100.0]) is None  # 표본 부족
    assert sharpe_ratio([100.0, 100.0, 100.0]) is None  # 표준편차 0
    sharpe = sharpe_ratio([100.0, 110.0, 121.0])  # 일정 수익률 → std 0
    assert sharpe is None
    varied = sharpe_ratio([100.0, 110.0, 105.0, 120.0])
    assert varied is not None


def test_annualized_sharpe_scales() -> None:
    equities = [100.0, 110.0, 105.0, 120.0, 118.0]
    base = sharpe_ratio(equities)
    annual = sharpe_ratio(equities, annualization_factor=4.0)
    assert base is not None and annual is not None
    assert annual == pytest.approx(base * 2.0)  # sqrt(4) = 2
