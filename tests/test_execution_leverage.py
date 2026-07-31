"""레버리지 북 사이징 회계(공유 함수) 단위 테스트 (WAN-171).

`execution.leverage`는 백테스트 배치(`backtest.leverage_book.run_leverage_book`)와 실시간
집행(`execution.engine`)이 **같은** 사이징 결정을 쓰도록 하는 정본이다. 여기서는 그
결정 함수(`resolve_book_sizing`·`scale_sizing_params`·`book_per_trade_sizing`·
`sizing_notional_cap`)의 회계를 고정하고, 백테스트 쪽 재수출이 **같은 객체**임을 확인한다
(로직 이중화 금지).
"""

from __future__ import annotations

import backtest.leverage_book as blb
from execution.leverage import (
    LEGACY_BOOK_PARAMS,
    LeverageBookParams,
    book_per_trade_sizing,
    resolve_book_sizing,
    scale_sizing_params,
    sizing_notional_cap,
)
from execution.sizing import PositionSizingParams


def _base() -> PositionSizingParams:
    return PositionSizingParams(risk_per_trade=0.01, leverage=1.0, min_stop_distance_fraction=0.0)


# --------------------------------------------------------------------------- #
# 재수출 항등: 백테스트와 라이브가 같은 함수 객체를 쓴다(로직 이중화 금지)
# --------------------------------------------------------------------------- #


def test_backtest_reexports_are_the_same_objects() -> None:
    """`backtest.leverage_book`이 `execution.leverage`의 함수·상수를 **그대로** 재수출한다.

    두 모듈이 각자 복제하면 갈라진다(WAN-95/112/123의 조용한 실패). `is` 비교로 같은
    객체임을 못 박아, 미래에 누가 backtest 쪽에 사본을 만들면 이 테스트가 깨진다.
    """
    assert blb.resolve_book_sizing is resolve_book_sizing
    assert blb.scale_sizing_params is scale_sizing_params
    assert blb.sizing_notional_cap is sizing_notional_cap
    assert blb.LEGACY_BOOK_PARAMS is LEGACY_BOOK_PARAMS
    assert blb.LeverageBookParams is LeverageBookParams


# --------------------------------------------------------------------------- #
# scale_sizing_params · book_per_trade_sizing
# --------------------------------------------------------------------------- #


def test_combined_scales_all_three_size_knobs() -> None:
    scaled = scale_sizing_params(_base(), 3.0, mode="combined")
    assert scaled.risk_per_trade == 0.03
    assert scaled.notional_fraction == 3.0
    assert scaled.leverage == 3.0


def test_cap_only_scales_only_the_leverage_cap() -> None:
    scaled = scale_sizing_params(_base(), 5.0, mode="cap_only")
    assert scaled.risk_per_trade == 0.01  # 거래당 크기 불변
    assert scaled.notional_fraction == 1.0
    assert scaled.leverage == 5.0  # 북 상한만 5배


def test_per_trade_sizing_is_base_for_cap_only_and_scaled_for_combined() -> None:
    base = _base()
    cap5 = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    comb3 = LeverageBookParams(leverage_multiple=3.0, leverage_mode="combined")
    # cap_only: 거래당 사이징은 1배 그대로(거래당 리스크 금액 라벨이 실제와 어긋나지 않게).
    assert book_per_trade_sizing(base, cap5).risk_per_trade == 0.01
    # combined: 거래당 리스크가 N배.
    assert book_per_trade_sizing(base, comb3).risk_per_trade == 0.03


# --------------------------------------------------------------------------- #
# resolve_book_sizing — combined
# --------------------------------------------------------------------------- #


def test_combined_passes_real_open_notional_and_scaled_params() -> None:
    book = LeverageBookParams(leverage_multiple=3.0, leverage_mode="combined")
    out = resolve_book_sizing(_base(), book, equity=10_000.0, open_notional=5_000.0)
    assert not out.cap_exhausted
    assert out.params.risk_per_trade == 0.03  # 매 거래 N배
    assert out.synthetic_open == 5_000.0  # combined은 실제 열린 명목 그대로


# --------------------------------------------------------------------------- #
# resolve_book_sizing — cap_only (합성 여유 = min(거래당 천장, 북 여유))
# --------------------------------------------------------------------------- #


def test_cap_only_per_trade_cap_binds_when_book_has_room() -> None:
    """북 여유가 넉넉하면 이 거래는 **거래당 천장(1배)** 까지만 — 합성 여유 = 1배 여유."""
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    out = resolve_book_sizing(_base(), book, equity=10_000.0, open_notional=5_000.0)
    assert not out.cap_exhausted
    assert out.params.leverage == 1.0  # 거래당 사이징은 1배
    # per_trade_cap=10_000, book 여유=45_000 → allowed=10_000 → synthetic_open=0.
    assert out.synthetic_open == 0.0


def test_cap_only_book_headroom_binds_when_almost_full() -> None:
    """북이 거의 찼으면 남은 여유만 이 거래에 — allowed = 북 여유 < 거래당 천장."""
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    out = resolve_book_sizing(_base(), book, equity=10_000.0, open_notional=48_000.0)
    assert not out.cap_exhausted
    # per_trade_cap=10_000, book 여유=2_000 → allowed=2_000 → synthetic_open=8_000.
    # position_size의 remaining = equity*1.0 - 8_000 = 2_000(= 북 여유).
    assert out.synthetic_open == 8_000.0


def test_cap_exhausted_when_open_notional_reaches_book_cap() -> None:
    book = LeverageBookParams(leverage_multiple=5.0, leverage_mode="cap_only")
    out = resolve_book_sizing(_base(), book, equity=10_000.0, open_notional=50_000.0)
    assert out.cap_exhausted


# --------------------------------------------------------------------------- #
# sizing_notional_cap
# --------------------------------------------------------------------------- #


def test_sizing_notional_cap_takes_min_of_leverage_and_fraction() -> None:
    sizing = PositionSizingParams(leverage=5.0, max_notional_fraction=2.0)
    assert sizing_notional_cap(sizing, 10_000.0) == 20_000.0  # min(5×, 2×)


def test_legacy_book_params_is_neutral_multiple_one_combined() -> None:
    assert LEGACY_BOOK_PARAMS.leverage_multiple == 1.0
    assert LEGACY_BOOK_PARAMS.leverage_mode == "combined"
