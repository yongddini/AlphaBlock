"""WAN-409 §2 — 「한 서브스텝에 같은 칸을 두 번 열지 않는다」 훅을 **동작으로** 고정한다.

배치기는 반개구간 규약(`exit_time == now`도 닫는다)을 쓴다. 겹침을 옳게 세려고 넣은 것인데,
후보가 **각자 따로** 시뮬레이션된다는 성질과 만나면 한 1분봉의 저가 하나가 **같은 진입가·같은
청산가의 왕복을 N번** 만들어 낸다(실측: 무더기 391건 중 318건이 직전 거래와 진입가·청산가·분이
전부 같다). 그 N번이 성립하려면 가격이 매번 지정가까지 되돌아와야 하는데 1분봉에는 그 증거가
없다.

🚨 **라벨이 아니라 숫자로 건다** — 인자를 넘기는지가 아니라 **거래가 실제로 줄어드는지**,
그리고 **끄면 예전과 한 건도 안 다른지**로.
"""

from __future__ import annotations

from backtest.leverage_book import (
    BookCell,
    LeverageBookParams,
    _Candidate,
    run_leverage_book,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide
from execution.sizing import PositionSizingParams

MINUTE = 60_000
CELL = ("BTC/USDT:USDT", "15m")


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        initial_capital=10_000.0,
        risk_sizing=PositionSizingParams(
            sizing_mode="risk_pct",
            risk_per_trade=0.01,
            leverage=1.0,
            min_stop_distance_fraction=0.0,
        ),
    )


def _stop_at(t: int) -> _Candidate:
    """같은 1분에 체결·손절나는 후보 — **값까지 똑같이** 만든다(중복 계상의 지문 그대로)."""
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=t,
        entry_price=101.0,
        exit_time=t,
        exit_price=99.0,
        reason=ExitReason.STOP_LOSS,
        stop_price=99.0,
        trigger_time=t,
    )


def _book(candidates: list[_Candidate]) -> list[BookCell]:
    return [BookCell(symbol=CELL[0], timeframe=CELL[1], candidates=candidates)]


def test_off_by_default_charges_the_same_minute_five_times() -> None:
    """🚨 기본값(끔)은 **예전 그대로** — 같은 1분·같은 가격의 왕복을 다섯 번 센다.

    이 테스트는 「고쳐졌는지」가 아니라 **결함이 실재하는지**를 못 박는다. 이게 통과하지
    않으면 WAN-409 §2의 관측 자체가 재현되지 않는 것이다.
    """
    cells = _book([_stop_at(10 * MINUTE) for _ in range(5)])
    outcome = run_leverage_book(cells, _cfg(), LeverageBookParams())
    assert len(outcome.trades) == 5
    assert len({(t.entry_time, t.entry_price, t.exits[-1].price) for t in outcome.trades}) == 1


def test_on_keeps_only_the_first_of_that_minute() -> None:
    """켜면 그 1분의 **첫 거래 하나만** 남는다 — 나머지는 배치되지 않는다."""
    cells = _book([_stop_at(10 * MINUTE) for _ in range(5)])
    outcome = run_leverage_book(cells, _cfg(), LeverageBookParams(), one_entry_per_step=True)
    assert len(outcome.trades) == 1
    reasons = [r.reason for r in outcome.stats.skip_records]
    assert reasons.count("same_step_reopen") == 4


def test_a_later_minute_still_enters() -> None:
    """🚨 다음 분에는 다시 들어간다 — 이 훅은 「그 칸을 영구히 막는」 게 아니다.

    막아 버리면 재탭을 끈 것과 같아져(팔 1) 이 팔이 재는 축이 달라진다.
    """
    cells = _book([_stop_at(10 * MINUTE), _stop_at(11 * MINUTE)])
    outcome = run_leverage_book(cells, _cfg(), LeverageBookParams(), one_entry_per_step=True)
    assert [t.entry_time for t in outcome.trades] == [10 * MINUTE, 11 * MINUTE]


def test_other_cells_are_untouched() -> None:
    """칸마다 따로 본다 — 한 칸의 청산이 다른 칸의 진입을 막지 않는다."""
    cells = [
        BookCell(symbol="BTC/USDT:USDT", timeframe="15m", candidates=[_stop_at(10 * MINUTE)]),
        BookCell(symbol="ETH/USDT:USDT", timeframe="15m", candidates=[_stop_at(10 * MINUTE)]),
    ]
    outcome = run_leverage_book(cells, _cfg(), LeverageBookParams(), one_entry_per_step=True)
    assert len(outcome.trades) == 2


def test_off_is_bit_identical_to_before() -> None:
    """🚨 안 켜면 **한 건도 안 달라진다** — 옵트인이라는 주장을 값으로 건다."""
    cands = [_stop_at(10 * MINUTE) for _ in range(3)] + [_stop_at(20 * MINUTE)]
    left = run_leverage_book(_book(list(cands)), _cfg(), LeverageBookParams())
    right = run_leverage_book(
        _book(list(cands)), _cfg(), LeverageBookParams(), one_entry_per_step=False
    )
    assert [(t.entry_time, t.realized_pnl) for t in left.trades] == [
        (t.entry_time, t.realized_pnl) for t in right.trades
    ]
    assert left.stats.skipped_cell_busy == right.stats.skipped_cell_busy
