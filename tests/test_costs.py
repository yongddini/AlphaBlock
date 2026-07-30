"""공용 체결 비용 모델 테스트 (WAN-37).

`common.costs.CostModel`의 메이커/테이커 수수료·슬리피지 산식과, 그 모델이 페이퍼
(`paper.store.build_record`)에 적용돼 각 비용(수수료·슬리피지·펀딩)이 순손익에서
개별적으로 반영되는지 검증한다.

⚠️ 옛 페이퍼 ↔ A안 백테스트 패리티 테스트(`run_backtest`)는 A안 엔진이 WAN-208/
WAN-215로 제거되면서 함께 삭제됐다. 지정가(B안) 엔진의 비용 반영은
`test_zone_limit_backtest`가 덮는다.
"""

from __future__ import annotations

import pytest

from common.costs import CostModel, Liquidity
from data.models import FundingRate
from live.paper import ClosedTrade, PaperPosition
from paper.store import build_record
from strategy.models import OrderBlockDirection, SignalExitReason

# --------------------------------------------------------------------------- #
# CostModel 단위
# --------------------------------------------------------------------------- #


def test_fee_rate_distinguishes_maker_and_taker() -> None:
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_bps=5.0)
    assert model.fee_rate(Liquidity.TAKER) == 0.0004
    assert model.fee_rate(Liquidity.MAKER) == 0.0002
    assert model.fee(1_000.0, Liquidity.TAKER) == pytest.approx(0.4)
    assert model.fee(1_000.0, Liquidity.MAKER) == pytest.approx(0.2)


def test_slippage_applies_to_taker_only() -> None:
    model = CostModel(slippage_bps=5.0)
    assert model.slippage_fraction == pytest.approx(0.0005)
    assert model.slippage_for(Liquidity.TAKER) == pytest.approx(0.0005)
    assert model.slippage_for(Liquidity.MAKER) == 0.0
    # 메이커(지정가) 체결은 참조가 그대로, 테이커는 불리하게 미끄러진다.
    assert model.entry_fill(100.0, is_long=True, liquidity=Liquidity.MAKER) == 100.0
    assert model.entry_fill(100.0, is_long=True, liquidity=Liquidity.TAKER) == pytest.approx(100.05)
    assert model.entry_fill(100.0, is_long=False, liquidity=Liquidity.TAKER) == pytest.approx(99.95)
    assert model.exit_fill(100.0, is_long=True, liquidity=Liquidity.TAKER) == pytest.approx(99.95)
    assert model.exit_fill(100.0, is_long=False, liquidity=Liquidity.TAKER) == pytest.approx(100.05)


def test_trade_costs_breakdown_components_are_nonnegative_and_sum() -> None:
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_bps=10.0)
    bd = model.trade_costs(
        100.0,
        110.0,
        is_long=True,
        entry_liquidity=Liquidity.TAKER,
        exit_liquidity=Liquidity.TAKER,
    )
    assert bd.gross_frac == pytest.approx(0.10)  # (110-100)/100 원가격 손익
    assert bd.slippage_frac > 0.0  # 테이커 진입·청산 슬리피지
    assert bd.fee_frac > 0.0
    assert bd.net_frac == pytest.approx(bd.gross_frac - bd.slippage_frac - bd.fee_frac)


def test_trade_costs_maker_entry_has_no_entry_slippage() -> None:
    """지정가(메이커) 진입은 진입 슬리피지가 없어 테이커 진입보다 순손익이 높다."""
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0004, slippage_bps=10.0)
    taker = model.trade_costs(
        100.0, 110.0, is_long=True, entry_liquidity=Liquidity.TAKER, exit_liquidity=Liquidity.TAKER
    )
    maker = model.trade_costs(
        100.0, 110.0, is_long=True, entry_liquidity=Liquidity.MAKER, exit_liquidity=Liquidity.TAKER
    )
    assert maker.slippage_frac < taker.slippage_frac
    assert maker.net_frac > taker.net_frac


# --------------------------------------------------------------------------- #
# build_record — 각 비용이 개별적으로 반영되는지
# --------------------------------------------------------------------------- #


def _closed_long(entry: float = 100.0, exit_price: float = 110.0) -> ClosedTrade:
    return ClosedTrade(
        position=PaperPosition(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=1_000,
            entry_price=entry,
            stop_price=95.0,
        ),
        exit_time=2_000,
        exit_price=exit_price,
        reason=SignalExitReason.TAKE_PROFIT,
    )


def test_build_record_reflects_fee_slippage_funding_individually() -> None:
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0002, slippage_bps=5.0)
    funding = [FundingRate(symbol="BTC/USDT:USDT", funding_time=1_500, rate=0.0001)]
    rec = build_record(
        _closed_long(),
        cost_model=model,
        entry_liquidity=Liquidity.TAKER,
        exit_liquidity=Liquidity.TAKER,
        funding_rates=funding,
    )
    # 개별 비용이 각각 잡힌다.
    assert rec.gross_pct == pytest.approx(10.0)
    assert rec.fee_pct > 0.0
    assert rec.slippage_pct > 0.0
    assert rec.funding_pct == pytest.approx(0.01)  # 1.0 × 0.0001 × 100 (롱 지불)
    # net = gross − fee − slippage − funding.
    assert rec.net_pct == pytest.approx(
        rec.gross_pct - rec.fee_pct - rec.slippage_pct - rec.funding_pct
    )
    assert rec.net_pct < rec.gross_pct  # 비용이 순손익을 깎는다


def test_build_record_maker_entry_beats_taker_entry() -> None:
    """지정가(메이커) 진입 기록이 시장가(테이커) 진입보다 슬리피지가 적어 유리하다."""
    model = CostModel(taker_fee_rate=0.0004, maker_fee_rate=0.0004, slippage_bps=10.0)
    taker = build_record(_closed_long(), cost_model=model, entry_liquidity=Liquidity.TAKER)
    maker = build_record(_closed_long(), cost_model=model, entry_liquidity=Liquidity.MAKER)
    assert maker.slippage_pct < taker.slippage_pct
    assert maker.net_pct > taker.net_pct


def test_build_record_legacy_fee_rate_path_unchanged() -> None:
    """cost_model 미지정 시 레거시(왕복 fee_rate·슬리피지 0) 동작을 보존한다."""
    rec = build_record(_closed_long(), fee_rate=0.0004)
    assert rec.gross_pct == pytest.approx(10.0)
    assert rec.fee_pct == pytest.approx(0.08)  # 2 × 0.0004 × 100
    assert rec.slippage_pct == 0.0
    assert rec.net_pct == pytest.approx(9.92)
