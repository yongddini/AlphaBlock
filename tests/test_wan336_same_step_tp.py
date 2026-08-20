"""WAN-336: 「진입한 그 1분 안의 익절」 관측 + 보수적 반사실 테스트.

이 파일이 지키는 계약은 넷이다.

1. **팔은 옵트인이고 끄면 비트 재현** — 라벨이 아니라 **동작**으로 고정한다(끈 팔이 켠 팔과
   같은 결과를 내면 팔이 아무것도 안 한 것이므로, 켠 팔이 **실제로 갈라지는지**를 함께 본다).
2. **관측은 순수 관측** — 카운터를 세는 것이 체결·청산·손익 어디도 바꾸지 않는다.
3. **두 층이 같은 술어를 쓴다** — 후보 층 카운터와 북 거래 층 귀속이 갈라지면 두 표가 다른
   것을 센다(WAN-91/95/112/123/159가 반복해 경계한 자리).
4. **북 귀속의 짝 계약** — 거래와 배치 기록이 같은 순서라는 전제가 깨지면 시끄럽게 죽는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from backtest import book_cli, harness
from backtest.book_cli import BookSegment, build_book_rows, iter_book_segments
from backtest.leverage_book import (
    BookOutcome,
    BookStats,
    LeverageBookParams,
    PlacedSetup,
)
from backtest.models import ExitReason
from backtest.substep import SubStep, ZoneLimitOutcome, ZoneLimitStatus, simulate_zone_limit_trade
from backtest.wan336_same_step_tp import (
    ADOPTED_CELL_KWARGS,
    BASE_ARM,
    COUNTERFACTUAL_ARM,
    _arm_did_something,
    _loo_rows,
    classify_trades,
    pnl_share,
    run_arm,
)
from backtest.zone_limit_backtest import is_same_step_take_profit
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

# 롱 셋업: 존 상단(지정가)=100, 존 하단(손절)=90, 익절=110.
_LIMIT = 100.0
_STOP = 90.0
_TP = 110.0
_OVERSOLD_SEED = [140.0, 130.0, 120.0, 110.0, 105.0]


def _step(t: int, high: float, low: float, close: float, htf: int = 0) -> SubStep:
    return SubStep(time=t, high=high, low=low, close=close, htf_bar_time=htf)


def _simulate(steps: list[SubStep], *, no_same_step_tp: bool = False) -> ZoneLimitOutcome:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        substeps=steps,
        rsi_state=RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=_TP,
        rsi_gate_mode="unconditional",
        no_same_step_tp=no_same_step_tp,
    )


#: 진입 스텝 하나가 지정가(100)와 익절(110)을 **함께** 품는다 — 「저가 먼저 · 고가 나중」을
#: 가정해야만 성립하는, WAN-336이 재려는 바로 그 봉이다.
_SAME_MINUTE_TP = [
    _step(0, high=111.0, low=99.0, close=110.5),
    _step(1, high=112.0, low=109.0, close=111.0),
    _step(2, high=113.0, low=110.0, close=112.0),
]

#: 진입 스텝은 익절에 못 닿고 **다음** 스텝에서 닿는다 — 팔이 건드리면 안 되는 대조군이다.
_LATER_TP = [
    _step(0, high=101.0, low=99.0, close=100.5),
    _step(1, high=111.0, low=100.0, close=110.5),
    _step(2, high=112.0, low=110.0, close=111.0),
]


# ------------------------------------------------ 1. 팔은 옵트인이고 끄면 비트 재현


def test_default_takes_profit_in_the_entry_minute() -> None:
    """기본 엔진은 진입한 그 1분 안에서 익절을 낸다 — 이것이 WAN-336의 관측 대상이다."""
    outcome = _simulate(_SAME_MINUTE_TP)
    assert outcome.status is ZoneLimitStatus.FILLED_EXITED
    assert outcome.exit_reason is SignalExitReason.TAKE_PROFIT
    assert outcome.entry_time == outcome.exit_time == 0


def test_arm_defers_take_profit_out_of_the_entry_minute() -> None:
    """팔을 켜면 그 스텝에서는 익절이 안 나고 **다음** 스텝으로 미뤄진다."""
    outcome = _simulate(_SAME_MINUTE_TP, no_same_step_tp=True)
    assert outcome.status is ZoneLimitStatus.FILLED_EXITED
    assert outcome.exit_reason is SignalExitReason.TAKE_PROFIT
    assert outcome.entry_time == 0
    assert outcome.exit_time == 1, "진입 스텝 익절이 미뤄지지 않았다 — 팔이 동작하지 않는다"


def test_arm_is_inert_when_take_profit_is_not_in_the_entry_minute() -> None:
    """진입 스텝에 익절이 없으면 두 팔이 **같은 결과**다 — 팔이 건드리는 스텝은 하나뿐이다."""
    off = _simulate(_LATER_TP)
    on = _simulate(_LATER_TP, no_same_step_tp=True)
    assert off == on


def test_arm_does_not_defer_the_stop_in_the_entry_minute() -> None:
    """손절은 그대로 진입 스텝에서 판정한다 — 익절만 미루는 것이 이 팔의 정의다.

    양쪽을 다 미루면 「진입을 한 스텝 늦춘 것」이 되어 다른 실험이 된다.
    """
    penetrating = [
        _step(0, high=101.0, low=89.0, close=90.5),
        _step(1, high=112.0, low=90.0, close=111.0),
    ]
    on = _simulate(penetrating, no_same_step_tp=True)
    assert on.exit_reason is SignalExitReason.STOP_LOSS
    assert on.entry_time == on.exit_time == 0
    assert on == _simulate(penetrating), "손절 경로가 팔에 흔들렸다"


def test_arm_also_defers_the_partial_ladder_rung() -> None:
    """분할 지점도 **위쪽** 목표라 같은 가정 위에 선다 — 한 팔 안에서 자가 갈리면 안 된다."""
    kwargs = {"partial_take_profit_r": 0.5, "partial_take_profit_fraction": 0.5}
    off = simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        substeps=_SAME_MINUTE_TP,
        rsi_state=RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=_TP,
        rsi_gate_mode="unconditional",
        **kwargs,  # type: ignore[arg-type]
    )
    on = simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        substeps=_SAME_MINUTE_TP,
        rsi_state=RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=_TP,
        rsi_gate_mode="unconditional",
        no_same_step_tp=True,
        **kwargs,  # type: ignore[arg-type]
    )
    assert [p.time for p in off.partial_exits] == [0], "기본 엔진이 진입 스텝에서 분할하지 않았다"
    assert [p.time for p in on.partial_exits] == [1], "팔이 분할 지점을 미루지 않았다"


# ------------------------------------------------ 2·3. 관측은 순수하고 술어는 하나다


def test_shared_predicate_needs_both_take_profit_and_the_same_minute() -> None:
    assert is_same_step_take_profit(5, 5, ExitReason.TAKE_PROFIT)
    assert not is_same_step_take_profit(5, 6, ExitReason.TAKE_PROFIT)
    assert not is_same_step_take_profit(5, 5, ExitReason.STOP_LOSS)
    assert not is_same_step_take_profit(5, 5, ExitReason.END_OF_DATA)


def test_candidate_counter_is_pure_observation() -> None:
    """카운터를 세는 것이 체결·청산·손익 어디도 바꾸지 않는다(WAN-90 `mfe_r` 부류).

    같은 셋업을 두 번 돌려 **관측 필드를 뺀 나머지가 전부 같은지**로 고정한다 — 관측이
    대상을 바꾸면 그 순간 이 측정은 무효다.
    """
    outcome = _simulate(_SAME_MINUTE_TP)
    assert outcome.exit_price == _TP
    assert outcome.entry_price == _LIMIT
    # 같은 입력은 같은 결과 — 관측이 상태를 오염시키지 않는다.
    assert _simulate(_SAME_MINUTE_TP) == outcome


def test_pnl_share_refuses_a_meaningless_denominator() -> None:
    """분모가 0 언저리이거나 음수면 비율을 내지 않는다(WAN-115가 문서화한 함정)."""
    assert pnl_share(5.0, 10.0) == pytest.approx(0.5)
    assert pnl_share(5.0, 0.0) is None
    assert pnl_share(-5.0, -10.0) is None


# ------------------------------------------------ 4. 북 귀속의 짝 계약


class _FakeTrade:
    """`classify_trades`·짝 계약이 보는 최소 표면만 흉내 낸다."""

    def __init__(self, entry: int, exit_time: int, reason: ExitReason, pnl: float) -> None:
        self.entry_time = entry
        self.exit_time = exit_time
        self.exits = [type("Fill", (), {"reason": reason})()]
        self.realized_pnl = pnl


def test_classify_trades_splits_same_minute_exits() -> None:
    def _placed(cell: tuple[str, str], pnl: float, risk: float) -> PlacedSetup:
        return PlacedSetup(cell=cell, equity=1.0, risk_amount=risk, realized_pnl=pnl)

    pairs = [
        (_FakeTrade(1, 1, ExitReason.TAKE_PROFIT, 100.0), _placed(("BTCUSDT", "15m"), 100.0, 50.0)),
        (_FakeTrade(2, 9, ExitReason.TAKE_PROFIT, 40.0), _placed(("BTCUSDT", "1h"), 40.0, 20.0)),
        (_FakeTrade(3, 3, ExitReason.STOP_LOSS, -30.0), _placed(("ETHUSDT", "15m"), -30.0, 30.0)),
    ]
    counts = classify_trades(pairs)  # type: ignore[arg-type]
    assert counts["tp_trades"] == 1
    assert counts["tp_pnl"] == pytest.approx(100.0)
    assert counts["stop_trades"] == 1
    assert counts["net_pnl"] == pytest.approx(110.0)
    # net R은 크기 정규화 자 — 같은 분 익절 +2.0R / 전체 +2.0 + 2.0 − 1.0 = 3.0R.
    assert counts["tp_net_r"] == pytest.approx(2.0)
    assert counts["net_r"] == pytest.approx(3.0)


def _segment_with(trades: list[object], placed: list[PlacedSetup]) -> BookSegment:
    stats = BookStats(placed=len(placed), placed_records=placed)
    outcome = BookOutcome(trades=trades, stats=stats, effective_config=None)  # type: ignore[arg-type]
    return BookSegment(segment="full", row=None, outcome=outcome, result=None)  # type: ignore[arg-type]


def test_trades_with_cells_pairs_by_position() -> None:
    trade = _FakeTrade(1, 1, ExitReason.TAKE_PROFIT, 100.0)
    placed = [PlacedSetup(cell=("BTCUSDT", "15m"), equity=1.0, risk_amount=1.0, realized_pnl=100.0)]
    pairs: list[Any] = list(_segment_with([trade], placed).trades_with_placements())
    assert [(t is trade, rec.cell) for t, rec in pairs] == [(True, ("BTCUSDT", "15m"))]


def test_trades_with_cells_dies_loudly_when_the_pairing_contract_breaks() -> None:
    """길이·손익 둘 다 대조한다 — 짝이 어긋나면 귀속이 통째로 틀리는데 라벨은 멀쩡하다."""
    trade = _FakeTrade(1, 1, ExitReason.TAKE_PROFIT, 100.0)
    with pytest.raises(AssertionError, match="길이가 다릅니다"):
        _segment_with([trade], []).trades_with_placements()
    mismatched = [
        PlacedSetup(cell=("BTCUSDT", "15m"), equity=1.0, risk_amount=1.0, realized_pnl=7.0)
    ]
    with pytest.raises(AssertionError, match="손익이 같은 자리 거래와 다릅니다"):
        _segment_with([trade], mismatched).trades_with_placements()


def test_build_book_rows_is_a_thin_wrapper_over_iter_book_segments() -> None:
    """리팩터가 동작을 안 바꿨는가 — 두 경로가 **같은 행**을 내야 한다."""
    kwargs = {
        "book": LeverageBookParams(),
        "segments": ("full",),
        "start_ms": 0,
        "end_ms": 1,
    }
    rows = build_book_rows([], **kwargs)  # type: ignore[arg-type]
    segments = iter_book_segments([], **kwargs)  # type: ignore[arg-type]
    assert rows == [s.row for s in segments]


# ------------------------------------------------ 채택 좌표 배선


def test_arms_are_named_and_the_base_arm_is_the_adopted_book() -> None:
    assert BASE_ARM == "base" and COUNTERFACTUAL_ARM == "no_same_step_tp"
    assert ADOPTED_CELL_KWARGS["reentry"] is True
    assert ADOPTED_CELL_KWARGS["reentry_entry_rule"] == "band"


def test_checksum_d_reads_whether_the_arm_actually_fired() -> None:
    """반사실 팔의 후보 층 카운터는 **정의상 0**이다 — 남아 있으면 팔이 라벨만 붙은 것이다."""
    passing = pd.DataFrame(
        [
            {"arm": BASE_ARM, "candidate_same_step_tps": 12},
            {"arm": COUNTERFACTUAL_ARM, "candidate_same_step_tps": 0},
        ]
    )
    assert "검산 (d) 통과" in _arm_did_something(passing)
    failing = passing.copy()
    failing.loc[failing["arm"] == COUNTERFACTUAL_ARM, "candidate_same_step_tps"] = 3
    assert "검산 (d) 실패" in _arm_did_something(failing)


def test_leave_one_out_dies_when_it_excludes_nothing() -> None:
    """LOO 필터가 아무것도 못 빼면 **모든 행이 기준 행과 같아진다** — 그러면 「한 종목이
    만드는 결과가 아니다」가 근거 없이 만들어진다. 라벨은 멀쩡한 채 표만 거짓이 되는 부류라
    조용히 넘기지 않고 죽는다.
    """
    payload = SimpleNamespace(symbol="BTC/USDT:USDT")
    with pytest.raises(AssertionError, match="아무 칸도 빼지 못했습니다"):
        _loo_rows(
            arm=BASE_ARM,
            payloads=[payload],  # type: ignore[list-item]
            symbols=["BTCUSDT"],  # 정규화 안 된 표기 — payload와 안 맞는다
            start_ms=0,
            end_ms=1,
        )


def test_run_arm_matches_the_adopted_book_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """검산 (a)가 못 잡는 고리 — `run_cells`에 넘긴 인자가 채택 경로와 같은가.

    모듈 상수를 서로 대조하는 것으로는 부족하다(상수는 맞는데 안 넘길 수 있다). 두 경로의
    **실제 호출 인자**를 캡처해 비교한다 — 이 표가 「채택 북 위에서 잰 값」이라는 주장의
    유일한 증거다(WAN-305).
    """
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan336_same_step_tp.run_cells", _fake)
    monkeypatch.setattr(book_cli, "run_cells", _fake)
    monkeypatch.setattr(book_cli, "apply_funding_proxy", lambda p: (list(p), ""))
    monkeypatch.setattr("backtest.wan336_same_step_tp.apply_funding_proxy", lambda p: (list(p), ""))

    run_arm(
        ["BTCUSDT"],
        ["1h"],
        BASE_ARM,
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        jobs=1,
        segments=(harness.SEGMENT_FULL,),
        log=False,
    )
    book_cli.run_book(
        ["BTCUSDT"],
        ["1h"],
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        book=LeverageBookParams(),
        segments=[harness.SEGMENT_FULL],
        jobs=1,
        log=False,
    )

    assert len(captured) == 2
    mine, adopted = captured
    for key in ADOPTED_CELL_KWARGS:
        assert mine[key] == adopted[key], f"{key}가 채택 북 경로와 다르다"
    # 기준선 팔은 반사실을 켜지 않는다 — 켜면 「base = 현행 채택 북」이 아니게 된다.
    assert mine["no_same_step_tp"] is False


def test_counterfactual_arm_turns_the_flag_on_and_drops_the_engine_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """팔 이름이 실제 인자로 이어지는가 — 라벨만 붙고 기본 엔진이 도는 것이 이 저장소가
    반복해 겪은 실패다(WAN-91/95/112/123/159).

    `engine_check`도 함께 본다: 그 검산은 격리 성과가 반사실 없는 per-cell과 비트 일치하는지
    보는 것이라 팔을 켠 쪽에서는 **당연히** 어긋난다(끄지 않으면 정상 실행이 실패로 보인다).
    """
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan336_same_step_tp.run_cells", _fake)
    monkeypatch.setattr("backtest.wan336_same_step_tp.apply_funding_proxy", lambda p: (list(p), ""))
    for arm, expect_flag in ((BASE_ARM, False), (COUNTERFACTUAL_ARM, True)):
        captured.clear()
        run_arm(
            ["BTCUSDT"],
            ["1h"],
            arm,
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            jobs=1,
            segments=(harness.SEGMENT_FULL,),
            log=False,
        )
        assert captured[0]["no_same_step_tp"] is expect_flag
        assert captured[0]["engine_check"] is (not expect_flag)
