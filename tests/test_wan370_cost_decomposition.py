"""WAN-370 회귀 — 익절만 메이커로 갈리는가(라벨이 아니라 **동작**) + 비용 분해 항등식.

이 저장소가 반복해 겪은 실패는 「바꿨다고 믿으면서 안 바뀐 것」이다(WAN-91/95/112/123/159).
그래서 여기서 고정하는 것은 필드 값이 아니라 **같은 거래의 수수료가 실제로 달라지는가**다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backtest import harness
from backtest.leverage_book import LeverageBookParams
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import PartialExit
from backtest.wan370_cost_decomposition import (
    NOISE_R,
    decompose_trade,
    stop_width_bucket,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate, _to_trade
from common.costs import Liquidity
from execution.sizing import PositionSizingParams
from strategy.models import SignalExitReason

_ADOPTED = harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
_LEGACY = harness.LEGACY_TAKE_PROFIT_LIQUIDITY


def _cfg(take_profit_liquidity: Liquidity = _ADOPTED) -> BacktestConfig:
    return BacktestConfig(
        initial_capital=10_000.0,
        entry_liquidity=Liquidity.MAKER,
        take_profit_liquidity=take_profit_liquidity,
        risk_sizing=PositionSizingParams(risk_per_trade=0.01, leverage=1.0),
    )


def _cand(reason: ExitReason, *, exit_price: float) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=1_000,
        exit_price=exit_price,
        reason=reason,
        stop_price=99.0,
        trigger_time=0,
    )


def _ladder_cand() -> _Candidate:
    return replace(
        _cand(ExitReason.TAKE_PROFIT, exit_price=101.5),
        partial_exits=(
            PartialExit(time=500, price=101.0, fraction=0.5, reason=SignalExitReason.TAKE_PROFIT),
        ),
    )


# --------------------------------------------------------------------------- #
# §2 — 사유별로 실제로 갈리는가
# --------------------------------------------------------------------------- #


def test_exit_liquidity_splits_by_reason() -> None:
    """단일 소스가 익절만 갈라 낸다 — 손절·데이터 종료는 언제나 테이커."""
    adopted = _cfg()
    assert adopted.exit_liquidity(ExitReason.TAKE_PROFIT) is Liquidity.MAKER
    assert adopted.exit_liquidity(ExitReason.PARTIAL_TAKE_PROFIT) is Liquidity.MAKER
    assert adopted.exit_liquidity(ExitReason.STOP_LOSS) is Liquidity.TAKER
    assert adopted.exit_liquidity(ExitReason.END_OF_DATA) is Liquidity.TAKER
    legacy = _cfg(_LEGACY)
    for reason in ExitReason:
        assert legacy.exit_liquidity(reason) is Liquidity.TAKER


def test_take_profit_and_stop_fees_actually_differ() -> None:
    """익절로 끝난 거래와 손절로 끝난 거래의 **수수료율이 실제로 다르다**(완료기준 3).

    라벨(`take_profit_liquidity` 필드)이 아니라 체결에 붙은 금액으로 고정한다 — 엔진이
    사유를 안 보고 한 덩어리로 취급하면 이 두 비율이 같아진다.
    """
    cfg = _cfg()
    tp = _to_trade(_cand(ExitReason.TAKE_PROFIT, exit_price=101.5), cfg.initial_capital, cfg)
    sl = _to_trade(_cand(ExitReason.STOP_LOSS, exit_price=99.0), cfg.initial_capital, cfg)
    assert tp is not None and sl is not None
    tp_fill, sl_fill = tp.exits[-1], sl.exits[-1]
    tp_rate = tp_fill.fee / (tp_fill.price * tp_fill.quantity)
    sl_rate = sl_fill.fee / (sl_fill.price * sl_fill.quantity)
    assert tp_rate == pytest.approx(cfg.cost_model.maker_fee_rate)
    assert sl_rate == pytest.approx(cfg.cost_model.taker_fee_rate)
    assert tp_rate < sl_rate
    # 익절은 지정가라 **슬리피지도 0**이다 — 체결가가 목표가 그대로여야 한다.
    assert tp_fill.price == pytest.approx(101.5)
    assert sl_fill.price < 99.0


def test_partial_take_profit_is_maker_too() -> None:
    """부분 익절(래더, WAN-323)도 익절이라 **두 체결 다** 메이커다.

    ⚠️ 「청산이 2회라 수수료가 는다」로 읽지 말 것 — 수수료는 **청산 명목에 비례**하므로
    같은 가격에서 반으로 쪼개면 총액이 같다(래더가 실제로 더 내는지는 분할 가격이 정한다).
    이 테스트가 고정하는 것은 **요율이 갈리지 않는다**는 것 하나다.
    """
    cfg = _cfg()
    trade = _to_trade(_ladder_cand(), cfg.initial_capital, cfg)
    assert trade is not None
    assert len(trade.exits) == 2
    for fill in trade.exits:
        rate = fill.fee / (fill.price * fill.quantity)
        assert rate == pytest.approx(cfg.cost_model.maker_fee_rate)
    legacy = _cfg(_LEGACY)
    old = _to_trade(_ladder_cand(), legacy.initial_capital, legacy)
    assert old is not None
    for fill in old.exits:
        rate = fill.fee / (fill.price * fill.quantity)
        assert rate == pytest.approx(legacy.cost_model.taker_fee_rate)


def test_end_of_data_exit_stays_taker() -> None:
    """만료·데이터 종료는 시장가 성격이라 **그대로 테이커**(범위 밖 · 완료기준 3)."""
    cfg = _cfg()
    trade = _to_trade(_cand(ExitReason.END_OF_DATA, exit_price=100.5), cfg.initial_capital, cfg)
    assert trade is not None
    fill = trade.exits[-1]
    assert fill.fee / (fill.price * fill.quantity) == pytest.approx(cfg.cost_model.taker_fee_rate)
    assert fill.price < 100.5  # 슬리피지가 붙는다


def test_legacy_pin_reproduces_pre_wan370_accounting() -> None:
    """옛 동작 명시 핀 — 익절도 테이커였던 손익이 **그대로** 나온다(완료기준 4)."""
    legacy = _cfg(_LEGACY)
    adopted = _cfg()
    cand = _cand(ExitReason.TAKE_PROFIT, exit_price=101.5)
    old = _to_trade(cand, legacy.initial_capital, legacy)
    new = _to_trade(cand, adopted.initial_capital, adopted)
    assert old is not None and new is not None
    # 손절 거래는 두 회계가 **글자 그대로 같다**(익절만 갈렸으니).
    stop = _cand(ExitReason.STOP_LOSS, exit_price=99.0)
    assert _to_trade(stop, legacy.initial_capital, legacy).realized_pnl == pytest.approx(  # type: ignore[union-attr]
        _to_trade(stop, adopted.initial_capital, adopted).realized_pnl,  # type: ignore[union-attr]
        rel=1e-15,
    )
    # 익절 거래는 새 회계가 더 낫다 — 크기는 왕복 7bp(테이커 4＋슬립 5 → 메이커 2) 몫이다.
    assert new.realized_pnl > old.realized_pnl


def test_legacy_build_config_pins_and_does_not_take_the_axis() -> None:
    """`legacy_build_config`은 축을 인자로 받지 않는다 — 받으면 라벨과 동작이 갈린다."""
    assert harness.legacy_build_config("1h").take_profit_liquidity is _LEGACY
    assert harness.build_config("1h").take_profit_liquidity is _ADOPTED
    with pytest.raises(TypeError):
        harness.legacy_build_config("1h", take_profit_liquidity=_ADOPTED)  # type: ignore[call-arg]


def test_adopted_default_is_maker_everywhere_it_matters() -> None:
    """채택 기본값이 실제로 메이커다 — 모델·팩토리·harness가 한 값을 말한다."""
    assert BacktestConfig().take_profit_liquidity is _ADOPTED
    assert _ADOPTED is Liquidity.MAKER
    assert _LEGACY is Liquidity.TAKER


# --------------------------------------------------------------------------- #
# 채택 북 경로가 **명시로** 채택 값을 넘기는가 (호출 인자 캡처)
# --------------------------------------------------------------------------- #


def test_run_book_passes_adopted_cost_to_both_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    """채택 북(`backtest.run`)이 후보 생성·배치 **양쪽에** 채택 비용을 명시로 넘긴다.

    두 층의 기본값이 옛 회계(중앙 핀)라 명시가 빠지면 「채택 북」이 조용히 옛 비용으로
    돈다 — 그래서 모듈 상수 대조가 아니라 **실제 호출 인자**를 캡처해 고정한다.
    """
    from backtest import book_cli

    seen: dict[str, object] = {}

    def _fake_run_cells(*_args: object, **kwargs: object) -> list[object]:
        seen["cells"] = kwargs.get("take_profit_liquidity")
        return []

    def _fake_iter(*_args: object, **kwargs: object) -> list[object]:
        seen["book"] = kwargs.get("take_profit_liquidity")
        return []

    monkeypatch.setattr(book_cli, "run_cells", _fake_run_cells)
    monkeypatch.setattr(book_cli, "iter_book_segments", _fake_iter)
    monkeypatch.setattr(book_cli, "apply_funding_proxy", lambda p: (p, ""))
    book_cli.run_book_segments(
        ["BTCUSDT"],
        ["1h"],
        start="2024-01-01",
        end="2024-02-01",
        book=LeverageBookParams(),
        segments=["full"],
        log=False,
    )
    assert seen["cells"] is _ADOPTED
    assert seen["book"] is _ADOPTED


def test_measurement_helpers_default_to_the_legacy_pin() -> None:
    """공유 헬퍼의 기본값이 옛 회계다 — 이것이 북 측정 CSV 20여 개를 한 곳에서 보존한다."""
    import inspect

    from backtest import book_cli
    from backtest.wan169_leverage_book import run_cells

    for fn in (book_cli.iter_book_segments, book_cli.build_book_rows, run_cells):
        default = inspect.signature(fn).parameters["take_profit_liquidity"].default
        assert default is _LEGACY, fn.__name__


# --------------------------------------------------------------------------- #
# §1 — 분해 항등식
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "candidate_factory",
    [
        lambda: _cand(ExitReason.TAKE_PROFIT, exit_price=101.5),
        lambda: _cand(ExitReason.STOP_LOSS, exit_price=99.0),
        lambda: _cand(ExitReason.END_OF_DATA, exit_price=100.2),
        _ladder_cand,
    ],
)
@pytest.mark.parametrize("liquidity", [_ADOPTED, _LEGACY])
def test_decomposition_closes_with_realized_pnl(
    candidate_factory: object, liquidity: Liquidity
) -> None:
    """분해가 실현손익과 **닫힌다**(검산 (c)) — 안 닫히면 표가 손익과 다른 얘기를 한다."""
    cfg = _cfg(liquidity)
    trade = _to_trade(candidate_factory(), cfg.initial_capital, cfg)  # type: ignore[operator]
    assert trade is not None
    parts = decompose_trade(trade, cfg)
    assert parts.residual == pytest.approx(0.0, abs=1e-9)
    assert parts.net == pytest.approx(trade.realized_pnl, rel=1e-15)
    for component in (parts.slippage, parts.entry_fee, parts.take_profit_fee, parts.stop_fee):
        assert component >= 0.0


def test_decomposition_attributes_fees_to_the_right_bucket() -> None:
    """익절 수수료 줄과 손절 수수료 줄이 **서로의 자리로 새지 않는다**."""
    cfg = _cfg()
    tp = decompose_trade(
        _to_trade(_cand(ExitReason.TAKE_PROFIT, exit_price=101.5), cfg.initial_capital, cfg),  # type: ignore[arg-type]
        cfg,
    )
    sl = decompose_trade(
        _to_trade(_cand(ExitReason.STOP_LOSS, exit_price=99.0), cfg.initial_capital, cfg),  # type: ignore[arg-type]
        cfg,
    )
    assert tp.take_profit_fee > 0.0 and tp.stop_fee == 0.0 and tp.other_fee == 0.0
    assert sl.stop_fee > 0.0 and sl.take_profit_fee == 0.0 and sl.other_fee == 0.0
    # 익절은 지정가라 청산 슬리피지가 없다 — 진입도 메이커이므로 이 거래의 슬리피지는 0이다.
    assert tp.slippage == pytest.approx(0.0)
    assert sl.slippage > 0.0


def test_stop_width_buckets_are_ordered_and_cover_the_line() -> None:
    """손절폭 버킷이 가드 하한(0.3%)을 경계로 갖는다 — 그 아래는 정의상 빈다."""
    assert stop_width_bucket(0.001) == "0.00~0.30%"
    assert stop_width_bucket(0.0035) == "0.30~0.40%"
    assert stop_width_bucket(0.02).startswith("≥1.50")


def test_verdict_reads_the_gross_sign_not_the_net() -> None:
    """§1-3의 갈림은 **비용 전** 값의 부호로 난다(0 근처 폭은 ±0.005R)."""
    assert verdict(0.05).startswith("(나)")
    assert verdict(-0.05).startswith("(가)")
    assert verdict(NOISE_R / 2).startswith("(0 근처)")
