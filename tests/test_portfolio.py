"""동시 다중 포지션 회계 테스트 (WAN-103).

핵심은 **기본값이 안 바뀌었다는 것**(동시 1포지션 경로가 비트 단위로 그대로)과, 포트폴리오
경로의 회계 규칙(명목 상한·존 제약·청산 검사)이 결정 문서대로 동작한다는 것이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade, TradeFill
from backtest.portfolio import (
    PortfolioParams,
    ToTrade,
    sequence_portfolio,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    _to_trade,
    apply_portfolio_leverage,
    sequence_with_candidates,
)
from data.models import FundingRate
from execution.sizing import PositionSizingParams, position_size


@dataclass(frozen=True)
class _Cand:
    """시퀀서가 요구하는 최소 필드만 가진 테스트용 후보(`CandidateLike` 구조적 만족)."""

    entry_time: int
    exit_time: int
    entry_price: float = 100.0
    stop_price: float = 95.0
    zone_key: frozenset[int] | None = None


def _trade(cand: _Cand, qty: float, pnl: float) -> Trade:
    return Trade(
        side=PositionSide.LONG,
        entry_time=cand.entry_time,
        entry_price=cand.entry_price,
        quantity=qty,
        entry_fee=0.0,
        exits=[
            TradeFill(
                time=cand.exit_time,
                price=cand.entry_price,
                quantity=qty,
                fee=0.0,
                reason=ExitReason.TAKE_PROFIT,
            )
        ],
        realized_pnl=pnl,
        return_pct=0.0,
    )


def _fixed_to_trade(pnl: float = 0.0, qty: float = 1.0) -> ToTrade[_Cand]:
    """명목 상한을 무시하고 항상 같은 크기로 진입하는 변환기(제약 검증용)."""

    def _tt(
        cand: _Cand,
        equity: float,
        cfg: BacktestConfig,
        funding_rates: Sequence[FundingRate] | None,
        open_notional: float,
    ) -> Trade | None:
        return _trade(cand, qty, pnl)

    return _tt


CFG = BacktestConfig(initial_capital=10_000.0, risk_sizing=None, position_fraction=1.0)


# --------------------------------------------------------------------------- #
# 사이징: 포트폴리오 명목 상한 (결정 2)
# --------------------------------------------------------------------------- #


def test_open_notional_shrinks_the_new_position() -> None:
    """이미 열린 명목이 상한을 먹으면 새 포지션은 남은 여유분으로 축소된다."""
    params = PositionSizingParams(leverage=1.0, min_stop_distance_fraction=0.0)
    full = position_size(equity=10_000.0, entry_price=100.0, stop_price=99.0, params=params)
    assert full == pytest.approx(100.0)  # 상한(자본×1배 = 10,000)에 clamp된 값.

    half = position_size(
        equity=10_000.0,
        entry_price=100.0,
        stop_price=99.0,
        params=params,
        open_notional=5_000.0,
    )
    assert half == pytest.approx(50.0)  # 남은 5,000 / 100.


def test_open_notional_exhausted_skips_entry() -> None:
    params = PositionSizingParams(leverage=1.0, min_stop_distance_fraction=0.0)
    qty = position_size(
        equity=10_000.0,
        entry_price=100.0,
        stop_price=99.0,
        params=params,
        open_notional=10_000.0,
    )
    assert qty == 0.0


def test_open_notional_default_preserves_per_trade_clamp() -> None:
    """`open_notional`을 안 주면 WAN-102 이전과 정확히 같다 — 기본값 불변의 근거."""
    params = PositionSizingParams(leverage=2.0)
    without = position_size(equity=10_000.0, entry_price=100.0, stop_price=95.0, params=params)
    with_zero = position_size(
        equity=10_000.0, entry_price=100.0, stop_price=95.0, params=params, open_notional=0.0
    )
    assert without == with_zero


def test_negative_open_notional_rejected() -> None:
    with pytest.raises(ValueError, match="open_notional"):
        position_size(
            equity=10_000.0,
            entry_price=100.0,
            stop_price=95.0,
            params=PositionSizingParams(),
            open_notional=-1.0,
        )


# --------------------------------------------------------------------------- #
# 시퀀서: 동시 진입 (결정 1)
# --------------------------------------------------------------------------- #


def test_overlapping_candidates_both_enter() -> None:
    """겹치는 두 셋업이 **둘 다** 진입한다 — 이 이슈가 푸는 제약 그 자체."""
    cands = [_Cand(entry_time=0, exit_time=100), _Cand(entry_time=10, exit_time=50)]
    paired, stats = sequence_portfolio(
        cands, CFG, _fixed_to_trade(), portfolio=PortfolioParams(leverage=10.0)
    )
    assert len(paired) == 2
    assert stats.peak_concurrency == 2


def test_max_concurrent_one_reproduces_single_position_rule() -> None:
    """`max_concurrent=1`은 동시 1포지션 규칙과 같다(pooled 대조군이 이걸 쓴다)."""
    cands = [_Cand(entry_time=0, exit_time=100), _Cand(entry_time=10, exit_time=50)]
    paired, stats = sequence_portfolio(
        cands, CFG, _fixed_to_trade(), portfolio=PortfolioParams(leverage=10.0, max_concurrent=1)
    )
    assert len(paired) == 1
    assert stats.skipped_max_concurrent == 1
    assert stats.peak_concurrency == 1


def test_entry_at_exact_exit_time_is_allowed() -> None:
    """직전 포지션의 청산 시각에 진입하는 건 겹침이 아니다(단일 포지션 엔진과 같은 경계)."""
    cands = [_Cand(entry_time=0, exit_time=100), _Cand(entry_time=100, exit_time=200)]
    paired, stats = sequence_portfolio(
        cands, CFG, _fixed_to_trade(), portfolio=PortfolioParams(leverage=10.0, max_concurrent=1)
    )
    assert len(paired) == 2
    assert stats.peak_concurrency == 1


def test_same_zone_cannot_open_twice(caplog: pytest.LogCaptureFixture) -> None:
    zone = frozenset({1, 2})
    cands = [
        _Cand(entry_time=0, exit_time=100, zone_key=zone),
        _Cand(entry_time=10, exit_time=50, zone_key=zone),
    ]
    paired, stats = sequence_portfolio(
        cands, CFG, _fixed_to_trade(), portfolio=PortfolioParams(leverage=10.0)
    )
    assert len(paired) == 1
    assert stats.skipped_zone == 1


def test_different_zones_open_together() -> None:
    cands = [
        _Cand(entry_time=0, exit_time=100, zone_key=frozenset({1})),
        _Cand(entry_time=10, exit_time=50, zone_key=frozenset({2})),
    ]
    paired, _stats = sequence_portfolio(
        cands, CFG, _fixed_to_trade(), portfolio=PortfolioParams(leverage=10.0)
    )
    assert len(paired) == 2


def test_one_per_zone_off_allows_stacking() -> None:
    zone = frozenset({1, 2})
    cands = [
        _Cand(entry_time=0, exit_time=100, zone_key=zone),
        _Cand(entry_time=10, exit_time=50, zone_key=zone),
    ]
    paired, _stats = sequence_portfolio(
        cands,
        CFG,
        _fixed_to_trade(),
        portfolio=PortfolioParams(leverage=10.0, one_per_zone=False),
    )
    assert len(paired) == 2


def test_realized_pnl_feeds_later_sizing() -> None:
    """청산된 포지션의 손익이 그 뒤 진입의 사이징 자본에 실린다."""
    seen: list[float] = []

    def _tt(
        cand: _Cand,
        equity: float,
        cfg: BacktestConfig,
        funding_rates: Sequence[FundingRate] | None,
        open_notional: float,
    ) -> Trade | None:
        seen.append(equity)
        return _trade(cand, 1.0, 500.0)

    cands = [_Cand(entry_time=0, exit_time=10), _Cand(entry_time=20, exit_time=30)]
    sequence_portfolio(cands, CFG, _tt, portfolio=PortfolioParams(leverage=10.0))
    assert seen == [10_000.0, 10_500.0]


def test_open_position_pnl_not_yet_in_sizing_equity() -> None:
    """열린 포지션의 미실현손익은 사이징 자본에 들어가지 않는다(회계 규칙: 실현 현금)."""
    seen: list[float] = []

    def _tt(
        cand: _Cand,
        equity: float,
        cfg: BacktestConfig,
        funding_rates: Sequence[FundingRate] | None,
        open_notional: float,
    ) -> Trade | None:
        seen.append(equity)
        return _trade(cand, 1.0, 500.0)

    # 두 번째 진입 시점(10)에 첫 포지션은 아직 열려 있다(청산 100).
    cands = [_Cand(entry_time=0, exit_time=100), _Cand(entry_time=10, exit_time=20)]
    sequence_portfolio(cands, CFG, _tt, portfolio=PortfolioParams(leverage=10.0))
    assert seen == [10_000.0, 10_000.0]


def test_open_notional_accumulates_across_positions() -> None:
    """시퀀서가 열린 명목 합을 사이징에 정확히 넘긴다."""
    seen: list[float] = []

    def _tt(
        cand: _Cand,
        equity: float,
        cfg: BacktestConfig,
        funding_rates: Sequence[FundingRate] | None,
        open_notional: float,
    ) -> Trade | None:
        seen.append(open_notional)
        return _trade(cand, 2.0, 0.0)  # 명목 = 100 × 2 = 200.

    cands = [
        _Cand(entry_time=0, exit_time=100),
        _Cand(entry_time=10, exit_time=100),
        _Cand(entry_time=20, exit_time=100),
    ]
    sequence_portfolio(cands, CFG, _tt, portfolio=PortfolioParams(leverage=10.0))
    assert seen == [0.0, 200.0, 400.0]


def test_concurrency_histogram_is_time_weighted() -> None:
    cands = [_Cand(entry_time=0, exit_time=100), _Cand(entry_time=40, exit_time=60)]
    _paired, stats = sequence_portfolio(
        cands, CFG, _fixed_to_trade(), portfolio=PortfolioParams(leverage=10.0)
    )
    # [0,40) 1개 · [40,60) 2개 · [60,100) 1개.
    assert stats.concurrency_histogram[2] == 20
    assert stats.concurrency_histogram[1] == 80
    assert stats.time_share(2) == pytest.approx(0.2)


# --------------------------------------------------------------------------- #
# 청산 검사 (결정 4)
# --------------------------------------------------------------------------- #


def test_no_liquidation_under_risk_sizing() -> None:
    """거래당 1% 리스크에서는 겹쳐도 청산이 안 난다 — 이 이슈의 핵심 구조적 결론."""
    cands = [_Cand(entry_time=i, exit_time=1000, zone_key=frozenset({i})) for i in range(10)]

    def _tt(
        cand: _Cand,
        equity: float,
        cfg: BacktestConfig,
        funding_rates: Sequence[FundingRate] | None,
        open_notional: float,
    ) -> Trade | None:
        # 손절 거리 5% × 수량 → 리스크는 자본의 1%.
        qty = (equity * 0.01) / 5.0
        return _trade(cand, qty, 0.0)

    _paired, stats = sequence_portfolio(cands, CFG, _tt, portfolio=PortfolioParams(leverage=10.0))
    assert stats.peak_concurrency == 10
    assert stats.max_concurrent_risk_ratio == pytest.approx(0.10, abs=1e-9)
    assert not stats.liquidated


def test_liquidation_triggers_when_worst_case_eats_equity() -> None:
    """리스크가 자본을 다 먹는 크기로 겹치면 최악 가정 청산이 잡힌다."""
    cands = [_Cand(entry_time=i, exit_time=1000, zone_key=frozenset({i})) for i in range(3)]

    def _tt(
        cand: _Cand,
        equity: float,
        cfg: BacktestConfig,
        funding_rates: Sequence[FundingRate] | None,
        open_notional: float,
    ) -> Trade | None:
        # 손절 거리 5 × 수량 800 = 4,000 리스크 → 3개면 12,000 > 자본 10,000.
        return _trade(cand, 800.0, 0.0)

    _paired, stats = sequence_portfolio(cands, CFG, _tt, portfolio=PortfolioParams(leverage=100.0))
    assert stats.liquidated
    event = stats.liquidations[0]
    assert event.worst_equity <= event.maintenance_margin


# --------------------------------------------------------------------------- #
# 레버리지 배선 (결정 2)
# --------------------------------------------------------------------------- #


def test_apply_portfolio_leverage_overrides_sizing() -> None:
    cfg = BacktestConfig(risk_sizing=PositionSizingParams(leverage=1.0))
    updated = apply_portfolio_leverage(cfg, PortfolioParams(leverage=3.0))
    assert updated.risk_sizing is not None
    assert updated.risk_sizing.leverage == 3.0
    assert cfg.risk_sizing is not None
    assert cfg.risk_sizing.leverage == 1.0  # 원본 불변.


def test_apply_portfolio_leverage_without_risk_sizing_is_noop() -> None:
    cfg = BacktestConfig(risk_sizing=None)
    assert apply_portfolio_leverage(cfg, PortfolioParams(leverage=3.0)) is cfg


def test_max_concurrent_one_matches_the_adopted_single_position_engine() -> None:
    """`max_concurrent=1` + `leverage=1`은 채택 엔진과 **같은 거래**를 낸다.

    pooled 대조군이 이 등가에 기대고 있다(`sequence_with_candidates` 대신 시퀀서가
    대조군까지 낸다) — 여기서 갈라지면 "포지션 제약을 풀어서 좋아졌다"가 실은 시퀀서
    버그일 수 있다.
    """
    cfg = BacktestConfig(initial_capital=10_000.0, risk_sizing=PositionSizingParams(leverage=1.0))
    cands = [
        _Candidate(
            side=PositionSide.LONG,
            entry_time=t,
            entry_price=100.0,
            exit_time=t + 50,
            exit_price=101.0 if t % 20 == 0 else 96.0,
            reason=ExitReason.TAKE_PROFIT if t % 20 == 0 else ExitReason.STOP_LOSS,
            stop_price=95.0,
            zone_key=frozenset({t}),
        )
        # 일부러 겹치게(간격 10 < 보유 50) 깔아 단일 포지션 규칙이 실제로 후보를 거르게 한다.
        for t in range(0, 200, 10)
    ]

    adopted = [trade for _, trade in sequence_with_candidates(cands, cfg)]
    paired, _stats = sequence_portfolio(
        cands, cfg, _to_trade, portfolio=PortfolioParams(leverage=1.0, max_concurrent=1)
    )
    portfolio_trades = [trade for _, trade in paired]

    assert len(adopted) > 1  # 대조가 성립하는지(다 걸러지지 않았는지) 확인.
    assert [t.entry_time for t in adopted] == [t.entry_time for t in portfolio_trades]
    assert [t.quantity for t in adopted] == [t.quantity for t in portfolio_trades]
    assert [t.realized_pnl for t in adopted] == [t.realized_pnl for t in portfolio_trades]


def test_notional_cap_skip_is_counted_separately_from_sizing_skip() -> None:
    """상한 소진 스킵과 사이징 거부 스킵을 구분해 센다."""
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        risk_sizing=PositionSizingParams(leverage=1.0, min_stop_distance_fraction=0.0),
    )
    # 손절 거리 1% → 명목 = 자본 전액. 두 번째 셋업은 상한이 소진돼 스킵된다.
    cands = [
        _Candidate(
            side=PositionSide.LONG,
            entry_time=t,
            entry_price=100.0,
            exit_time=1000,
            exit_price=100.0,
            reason=ExitReason.TAKE_PROFIT,
            stop_price=99.0,
            zone_key=frozenset({t}),
        )
        for t in (0, 10)
    ]
    paired, stats = sequence_portfolio(
        cands, cfg, _to_trade, portfolio=PortfolioParams(leverage=1.0)
    )
    assert len(paired) == 1
    assert stats.skipped_notional == 1
    assert stats.skipped_sizing == 0
