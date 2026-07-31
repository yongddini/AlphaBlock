"""레버리지 북을 실시간 집행 엔진에 배선한 것의 테스트 (WAN-171).

`ExecutionEngine`이 `leverage_book`을 받으면 칸=(종목,TF)들이 한 지갑(공유 자본·공유
`equity`)을 나눠 쓰고, 진입마다 백테스트와 **같은** `resolve_book_sizing`으로 배수·북
명목 상한을 건다. 여기서 고정하는 것:

* **N=1(중립) 북 = 북 없는 단일 포지션 경로**(비트 단위, 회귀 — 완료 기준 4).
* **칸당 1포지션 + 칸 간 공유 자본**이 동작으로 존재(완료 기준 1·2).
* **북 명목 상한**이 여러 칸을 가로질러 걸리고 소진되면 진입이 스킵된다.
* **`RiskParams` 기본값(동시 1포지션·명목 1×)이 북을 되돌리지 않는다** — 북 모드는
  명목·동시 상한을 북에 맡기고 일일 손실 서킷브레이커만 남긴다(WAN-38).
"""

from __future__ import annotations

from execution.broker import PaperBroker
from execution.engine import EntryIntent, ExecutionEngine
from execution.leverage import LEGACY_BOOK_PARAMS, LeverageBookParams
from execution.risk import RiskManager, RiskParams
from execution.sizing import PositionSizingParams
from strategy.models import OrderBlockDirection, SignalExitReason

_DAY0 = 1_700_000_000_000


def _sizing() -> PositionSizingParams:
    # leverage=1.0 → 거래당 명목 천장 = 1× 자본. min_stop=0으로 사이징 가드는 끈다.
    return PositionSizingParams(risk_per_trade=0.01, leverage=1.0, min_stop_distance_fraction=0.0)


def _engine(
    *,
    book: LeverageBookParams | None = None,
    risk: RiskParams | None = None,
    equity: float = 10_000.0,
) -> ExecutionEngine:
    return ExecutionEngine(
        broker=PaperBroker(),
        risk_manager=RiskManager(risk if risk is not None else RiskParams(max_leverage=100.0)),
        sizing_params=_sizing(),
        equity=equity,
        leverage_book=book,
    )


def _intent(symbol: str, *, entry: float = 100.0, stop: float = 99.0) -> EntryIntent:
    return EntryIntent(
        symbol=symbol,
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_price=entry,
        entry_time=_DAY0,
        stop_price=stop,
        take_profit_price=entry + 5.0,
    )


# --------------------------------------------------------------------------- #
# 회귀: N=1(중립) 북 = 북 없는 단일 포지션 경로 (완료 기준 4)
# --------------------------------------------------------------------------- #


def test_neutral_book_matches_no_book_single_position() -> None:
    """칸이 하나면 `LEGACY_BOOK_PARAMS`(배수1·combined) 북은 북 없는 경로와 같은 포지션을 연다.

    칸당 1포지션이 겹침을 막아 `open_notional`이 0이므로 사이징이 완전히 같아야 한다 —
    WAN-45 단일 포지션 러너와의 동작 항등(백테스트 `test_single_cell_book_matches_
    adopted_sequencer_bit_for_bit`의 라이브판)."""
    no_book = _engine(book=None)
    with_book = _engine(book=LEGACY_BOOK_PARAMS)

    out_a = no_book.on_entry(_intent("BTC/USDT:USDT"), now_ms=_DAY0)
    out_b = with_book.on_entry(_intent("BTC/USDT:USDT"), now_ms=_DAY0)

    assert out_a.accepted and out_b.accepted
    assert out_a.position is not None and out_b.position is not None
    assert out_a.position.quantity == out_b.position.quantity
    assert out_a.position.notional == out_b.position.notional
    assert no_book.equity == with_book.equity


# --------------------------------------------------------------------------- #
# 칸당 1포지션 (완료 기준 2)
# --------------------------------------------------------------------------- #


def test_same_cell_second_entry_rejected() -> None:
    """같은 (종목,TF)에 이미 포지션이 있으면 두 번째 진입은 스킵된다(칸당 1포지션)."""
    engine = _engine(book=LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only"))
    first = engine.on_entry(_intent("BTC/USDT:USDT"), now_ms=_DAY0)
    assert first.accepted
    second = engine.on_entry(_intent("BTC/USDT:USDT"), now_ms=_DAY0)
    assert not second.accepted
    assert "이미 오픈 포지션" in second.reason
    assert len(engine.open_positions) == 1


# --------------------------------------------------------------------------- #
# 공유 자본 + 칸 간 동시 + 북 명목 상한 (완료 기준 1)
# --------------------------------------------------------------------------- #


def test_cells_share_capital_and_book_cap_binds_across_cells() -> None:
    """여러 칸이 한 지갑을 나눠 쓰고, 북 명목 상한이 칸을 가로질러 소진되면 스킵된다.

    자본 10_000 · 거래당 천장 1×(10_000) · cap_only 배수 2 → 북 상한 20_000. 두 칸이
    각각 10_000 명목을 채우면 북이 꽉 차, 세 번째 칸은 `북 명목 상한 소진`으로 스킵된다.
    `RiskParams(max_concurrent_positions=1)`인데도 둘째 칸이 열리는 것이 핵심 — 북 모드는
    동시 포지션 상한을 북(칸당 1포지션)에 맡긴다(그 전역 상한이 북을 되돌리지 않는다).
    """
    book = LeverageBookParams(leverage_multiple=2.0, leverage_mode="cap_only")
    engine = _engine(book=book, risk=RiskParams(max_concurrent_positions=1))

    a = engine.on_entry(_intent("BTC/USDT:USDT"), now_ms=_DAY0)
    b = engine.on_entry(_intent("ETH/USDT:USDT"), now_ms=_DAY0)
    c = engine.on_entry(_intent("SOL/USDT:USDT"), now_ms=_DAY0)

    assert a.accepted and b.accepted  # 공유 자본 위 동시 두 칸(전역 1포지션 상한 무관).
    assert len(engine.open_positions) == 2
    assert a.position is not None and a.position.notional == 10_000.0
    assert b.position is not None and b.position.notional == 10_000.0
    assert engine.open_notional == 20_000.0  # 북 상한(20_000)에 정확히 도달.
    assert not c.accepted and "북 명목 상한 소진" in c.reason


def test_realized_pnl_from_one_cell_grows_shared_wallet_for_next() -> None:
    """한 칸의 실현 손익이 공유 지갑에 쌓여 다음 칸 사이징 자본이 된다."""
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    engine = _engine(book=book)
    engine.on_entry(_intent("BTC/USDT:USDT", entry=100.0, stop=99.0), now_ms=_DAY0)
    equity_before_exit = engine.equity
    # 익절 청산 → 실현 손익이 공유 자본에 반영된다.
    engine.on_exit(
        "BTC/USDT:USDT",
        "1h",
        exit_price=110.0,
        reason=SignalExitReason.TAKE_PROFIT,
        now_ms=_DAY0,
    )
    assert engine.equity > equity_before_exit  # 공유 지갑이 커졌다.


# --------------------------------------------------------------------------- #
# 일일 손실 서킷브레이커는 북 모드에서도 살아 있다 (WAN-38)
# --------------------------------------------------------------------------- #


def test_circuit_breaker_still_blocks_in_book_mode() -> None:
    """북 모드는 명목·동시 상한을 북에 맡기되 일일 손실 서킷브레이커는 유지한다(WAN-38)."""
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    engine = _engine(book=book, risk=RiskParams(daily_loss_limit_fraction=0.05))
    # 기준 자본 10_000의 10% 손실을 누적 → 한도(5%) 초과.
    engine._risk.register_realized_pnl(-1_000.0, now_ms=_DAY0, equity=10_000.0)
    out = engine.on_entry(_intent("BTC/USDT:USDT"), now_ms=_DAY0)
    assert not out.accepted and "서킷브레이커" in out.reason
