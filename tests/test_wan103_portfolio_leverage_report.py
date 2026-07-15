"""WAN-103 리포트의 조립 로직 테스트 (실데이터 없이 합성 후보로).

리포트가 **숫자를 잘못 짝지을** 자리들을 고정한다: pooled에서 심볼이 다른 존이 같은
존으로 뭉치지 않는지, 후보가 자기 심볼의 펀딩을 들고 가는지, N배 시나리오가 중복
행을 내지 않는지.
"""

from __future__ import annotations

from backtest.models import ExitReason, PositionSide
from backtest.portfolio import PortfolioParams, sequence_portfolio
from backtest.sweep import default_backtest_config
from backtest.wan103_portfolio_leverage_report import (
    FIXED_LEVERAGES,
    SCENARIO_PEAK,
    _scenarios,
    _Tagged,
    _tagged_to_trade,
    measure_peak,
)
from backtest.zone_limit_backtest import _Candidate, _to_trade
from data.models import FundingRate

_HOUR = 60 * 60_000


def _cand(*, entry: int, exit_: int, zone_key: frozenset[int] | None = None) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry,
        entry_price=100.0,
        exit_time=exit_,
        exit_price=101.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=95.0,
        zone_key=zone_key,
    )


def _tagged(symbol: str, cand: _Candidate, rates: tuple[FundingRate, ...] = ()) -> _Tagged:
    return _Tagged(inner=cand, symbol=symbol, timeframe="1h", rates=rates)


def test_same_zone_key_in_different_symbols_does_not_collide() -> None:
    """`zone_key`는 봉 시각의 집합이라 심볼이 달라도 값이 같을 수 있다.

    네임스페이스가 없으면 BTC의 존과 ETH의 존이 한 존으로 취급돼 `one_per_zone`이
    엉뚱한 진입을 막는다 — pooled 수치가 조용히 작아지는 종류의 버그다.
    """
    zone = frozenset({0, 1})
    btc = _tagged("BTC/USDT:USDT", _cand(entry=0, exit_=_HOUR, zone_key=zone))
    eth = _tagged("ETH/USDT:USDT", _cand(entry=0, exit_=_HOUR, zone_key=zone))
    assert btc.zone_key != eth.zone_key

    paired, stats = sequence_portfolio(
        [btc, eth],
        default_backtest_config("1h"),
        _tagged_to_trade,
        portfolio=PortfolioParams(leverage=10.0),
    )
    assert len(paired) == 2
    assert stats.skipped_zone == 0


def test_same_zone_within_one_symbol_still_blocked() -> None:
    """네임스페이스가 같은 시리즈 안의 존 제약까지 없애 버리면 안 된다."""
    zone = frozenset({0, 1})
    first = _tagged("BTC/USDT:USDT", _cand(entry=0, exit_=_HOUR, zone_key=zone))
    second = _tagged("BTC/USDT:USDT", _cand(entry=1, exit_=_HOUR, zone_key=zone))
    assert first.zone_key == second.zone_key

    paired, stats = sequence_portfolio(
        [first, second],
        default_backtest_config("1h"),
        _tagged_to_trade,
        portfolio=PortfolioParams(leverage=10.0),
    )
    assert len(paired) == 1
    assert stats.skipped_zone == 1


def test_tagged_none_zone_key_stays_none() -> None:
    tagged = _tagged("BTC/USDT:USDT", _cand(entry=0, exit_=_HOUR, zone_key=None))
    assert tagged.zone_key is None


def test_tagged_to_trade_uses_the_candidates_own_funding() -> None:
    """pooled에서 후보는 자기 심볼의 펀딩을 써야 한다(공용 요율을 물리면 안 된다)."""
    cfg = default_backtest_config("1h").model_copy(update={"funding_enabled": True})
    rates = tuple(
        FundingRate(
            symbol="BTC/USDT:USDT",
            funding_time=t,
            rate=0.01,  # 과장된 요율 — 펀딩이 실제로 붙었는지 드러나게.
            is_predicted=False,
        )
        for t in (0, _HOUR // 2)
    )
    cand = _cand(entry=0, exit_=_HOUR)

    with_funding = _tagged_to_trade(_tagged("BTC/USDT:USDT", cand, rates), 10_000.0, cfg, None, 0.0)
    without = _tagged_to_trade(_tagged("BTC/USDT:USDT", cand, ()), 10_000.0, cfg, None, 0.0)
    assert with_funding is not None and without is not None
    assert with_funding.funding_cost > 0.0
    assert without.funding_cost == 0.0
    assert with_funding.realized_pnl < without.realized_pnl


def test_tagged_to_trade_ignores_the_shared_funding_argument() -> None:
    """시퀀서가 넘기는 유니버스 공용 요율은 무시된다 — 남의 심볼 펀딩을 물지 않기 위해."""
    cfg = default_backtest_config("1h").model_copy(update={"funding_enabled": True})
    foreign = [FundingRate(symbol="ETH/USDT:USDT", funding_time=0, rate=0.01, is_predicted=False)]
    trade = _tagged_to_trade(
        _tagged("BTC/USDT:USDT", _cand(entry=0, exit_=_HOUR)), 10_000.0, cfg, foreign, 0.0
    )
    assert trade is not None
    assert trade.funding_cost == 0.0


def test_measure_peak_is_not_limited_by_leverage() -> None:
    """겹침 계측은 명목 상한이 후보를 거르기 전의 **자연 수요**를 재야 한다."""
    cands = [_cand(entry=i, exit_=_HOUR, zone_key=frozenset({i})) for i in range(5)]
    peak = measure_peak(cands, default_backtest_config("1h"), _to_trade)
    assert peak == 5


def test_scenarios_include_peak_when_it_is_not_a_fixed_axis() -> None:
    names = [name for name, _lev in _scenarios(7)]
    assert names == [f"lev_{lev:g}" for lev in FIXED_LEVERAGES] + [SCENARIO_PEAK]


def test_scenarios_do_not_duplicate_a_fixed_leverage() -> None:
    """N이 고정 축과 같으면 같은 실행을 두 줄로 내지 않는다."""
    assert [lev for _name, lev in _scenarios(2)] == list(FIXED_LEVERAGES)
