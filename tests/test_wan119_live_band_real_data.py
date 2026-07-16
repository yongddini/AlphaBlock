"""WAN-119: 봉내 라이브 밴드가 채택 경로에서 **실제로 다르게 돈다**(실데이터).

합성 데이터로는 이 배선을 증명할 수 없다 — 볼린저 기본값이 작은 합성셋의 후보를 거의
전부 걸러내 `tap`도 `intrabar_live`도 거래 0건이라 **두 모드가 구분되지 않는다**
(`tests/test_zone_limit_backtest.py`가 그래서 공급자·밴드를 단위로 격리해 본다). 배선이
빠져도 합성 테스트는 초록불이므로, "새 모드를 켠 채 옛 밴드로 돌면서 트레이딩뷰대로
쟀다고 믿는" WAN-100/115 부류의 사고를 막으려면 실데이터 한 셀이 필요하다.

실데이터(`data/ohlcv.db`)는 저장소에 없으므로 CI에서는 skip된다(`tests/
test_run_regression_real_data.py`와 같은 규약 — 파일 존재가 아니라 **봉 유무**로 판정한다).
비용을 감당 가능하게 두려고 심볼 1개 × TF 1개 × 3개월로 좁혔다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import load_market_data
from backtest.models import BacktestConfig
from backtest.zone_limit_backtest import ZoneLimitStats, build_zone_limit_candidates
from strategy.models import ConfluenceParams

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "15m"
_START = "2025-01-01"
_END = "2025-04-01"


def _ms(date: str) -> int:
    return int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)


@pytest.fixture(scope="module")
def market() -> object:
    data = load_market_data(_SYMBOL, _TIMEFRAME, start_ms=_ms(_START), end_ms=_ms(_END))
    if data.empty or data.df_1m.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터(1분봉 포함)가 없어 건너뜁니다(CI 기본).")
    return data


def _band(band_bar: str) -> ConfluenceParams:
    """채택 기본값에서 `band_bar`만 갈아끼운다(리포트 사다리와 같은 방식)."""
    params = ConfluenceParams()
    assert params.deviation_filter is not None
    return params.model_copy(
        update={
            "deviation_filter": params.deviation_filter.model_copy(update={"band_bar": band_bar})
        }
    )


def _run(market: object, band_bar: str) -> tuple[list[tuple[int, float]], ZoneLimitStats]:
    candidates, stats = build_zone_limit_candidates(
        htf_df=market.htf_df,  # type: ignore[attr-defined]
        df_1m=market.df_1m,  # type: ignore[attr-defined]
        timeframe=_TIMEFRAME,
        params=_band(band_bar),
        cfg=BacktestConfig(),
    )
    return [(c.entry_time, c.entry_price) for c in candidates], stats


def test_intrabar_live_produces_a_distinct_engine_run(market: object) -> None:
    """세 밴드 정의가 실데이터에서 서로 다른 진입을 낸다 = 배선이 살아 있다."""
    tap, _ = _run(market, "tap")
    prev, _ = _run(market, "prev_closed")
    live, live_stats = _run(market, "intrabar_live")

    assert live, "전제: live 모드가 실제 거래를 낸다"
    assert live != tap, "live가 탭 봉 밴드와 같은 결과면 배선이 안 된 것이다"
    assert live != prev, "live가 직전 확정봉 밴드와 같으면 봉내 재산정이 안 도는 것이다"
    assert live_stats.filled > 0


def test_intrabar_live_fill_rate_stays_in_the_same_ballpark(market: object) -> None:
    """체결률이 정적 모드와 같은 자릿수여야 한다 — 규칙 층이 만든 ~50%(WAN-114).

    live 모드는 밴드가 움직이는 표적이라 체결이 조금 줄 수 있지만, 0에 붙거나 100%로 뛰면
    그건 시장이 아니라 **버그**다(예: 매 스텝 주문이 사라지거나, 반대로 항상 체결되거나).
    """
    _, tap_stats = _run(market, "tap")
    _, live_stats = _run(market, "intrabar_live")
    assert tap_stats.fill_rate is not None and live_stats.fill_rate is not None
    assert 0.2 < live_stats.fill_rate < 0.9
    assert abs(live_stats.fill_rate - tap_stats.fill_rate) < 0.25


def test_intrabar_live_eligible_counts_only_setups_that_had_an_order(market: object) -> None:
    """체결률의 분모가 모드 간 같은 것을 재야 3자 비교표가 성립한다.

    live 모드는 밴드가 움직여 "지금은 주문 없음"이 나올 수 있는데, 그것까지 분모에 세면
    정적 모드(탭 봉에서 걸러냄)와 다른 것을 재게 된다 — `ZoneLimitOutcome.order_rested`가
    그 분모를 맞춘다.
    """
    _, tap_stats = _run(market, "tap")
    _, live_stats = _run(market, "intrabar_live")
    # 움직이는 밴드는 주문이 걸릴 기회가 더 많으므로 eligible이 같거나 많다. 다만 그 차이가
    # 배수로 벌어지면 분모가 부풀었다는 뜻이다(= `order_rested` 가드가 안 먹은 것).
    assert live_stats.eligible >= tap_stats.eligible
    assert live_stats.eligible < tap_stats.eligible * 1.5
