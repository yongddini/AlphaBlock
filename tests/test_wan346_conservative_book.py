"""WAN-346: 보수 축 2×2 채택 북 + 북 거래별 내역(§0) + 복리 끈 팔(§2) 테스트.

고정하는 것 넷: (1) **북이 거래별 내역을 낸다**(열·짝 계약·구간 고르기), (2) **복리 노브가
라벨이 아니라 사이징을 실제로 바꾼다**(끄면 비트 재현), (3) **관측 필드(익절가·재진입
라벨)가 순수 관측이다**(손익·체결 불변), (4) **팔이 채택 좌표 위에서 돈다**(`run_cells`
호출 인자 대조 — 라벨만 붙는 실패가 이 저장소의 상습 사고다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backtest import book_cli, harness
from backtest.book_cli import (
    BOOK_TRADE_COLUMNS,
    BookSegment,
    book_equity_to_display_frame,
    book_trades_to_display_frame,
)
from backtest.leverage_book import (
    BookCell,
    LeverageBookParams,
    PlacedSetup,
    run_leverage_book,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import SubStep, ZoneLimitStatus, simulate_zone_limit_trade
from backtest.wan169_leverage_book import BOOK_ANNUALIZATION_TF
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan346_conservative_book import (
    ADOPTED_ARM,
    ARM_ORDER,
    ARMS_BY_NAME,
    COMPOUND_OFF,
    COMPOUND_ON,
    MOST_CONSERVATIVE_ARM,
    RUIN_MDD,
    build_summary,
    cagr,
    detail_paths,
    run_arm,
    span_years,
    trade_rulers,
)
from backtest.zone_limit_backtest import _Candidate, build_result_from_trades
from execution.sizing import PositionSizingParams
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi


@pytest.fixture(autouse=True)
def _reports_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """리포트 산출물은 **저장소가 아니라 tmp**에 쓴다.

    `run_arm`이 팔 D에서 거래별 CSV를 남기므로, 이 가드가 없으면 테스트가 저장소의
    `backtest/reports/`에 **빈 표를 덮어쓴다** — 나중에 그 파일이 「팔 D의 거래내역」으로
    인용되면 이 이슈의 산출물이 통째로 거짓이 된다(WAN-106 교훈의 테스트 축 변종).
    """
    monkeypatch.setattr("backtest.wan346_conservative_book.REPORTS_DIR", tmp_path)


# --------------------------------------------------------------------------- #
# 픽스처 — 실제 엔진 자료형을 쓴다(대역이면 사이징 검증이 라벨 검증으로 퇴화한다)
# --------------------------------------------------------------------------- #

_OVERSOLD_SEED = [140.0, 130.0, 120.0, 110.0, 105.0]


def _cand(
    entry_time: int,
    exit_time: int,
    *,
    entry_price: float = 100.0,
    exit_price: float = 101.5,
    stop_price: float = 99.0,
    take_profit_price: float | None = 101.5,
    is_reentry: bool = False,
    same_step_take_profit: bool = False,
    reason: ExitReason = ExitReason.TAKE_PROFIT,
) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        reason=reason,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        is_reentry=is_reentry,
        same_step_take_profit=same_step_take_profit,
        trigger_time=entry_time,
    )


def _cfg(initial_capital: float = 10_000.0) -> BacktestConfig:
    return BacktestConfig(
        initial_capital=initial_capital,
        risk_sizing=PositionSizingParams(
            sizing_mode="risk_pct",
            risk_per_trade=0.01,
            leverage=1.0,
            min_stop_distance_fraction=0.0,
        ),
    )


def _segment(cells: list[BookCell], *, compound_sizing: bool = True) -> BookSegment:
    cfg = _cfg()
    outcome = run_leverage_book(cells, cfg, LeverageBookParams(), compound_sizing=compound_sizing)
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    return BookSegment(
        segment="full",
        row=book_cli._book_row(  # noqa: SLF001 — 행 생성기도 프로덕션 것을 그대로 쓴다
            "full",
            len(cells),
            len({c.symbol for c in cells}),
            0,
            1,
            outcome,
            result,
            LeverageBookParams(),
        ),
        outcome=outcome,
        result=result,
    )


# --------------------------------------------------------------------------- #
# 1. §0 — 북 거래별 내역
# --------------------------------------------------------------------------- #


def test_book_trade_frame_carries_the_cell_and_the_book_only_columns() -> None:
    """per-cell 표에 없는 열(칸·손절가·익절가·리스크·net R·라벨)이 실려야 한다."""
    cells = [
        BookCell("BTCUSDT", "15m", [_cand(0, 10, same_step_take_profit=True)]),
        BookCell("ETHUSDT", "1h", [_cand(5, 20, is_reentry=True)]),
    ]
    frame = book_trades_to_display_frame(_segment(cells))
    assert len(frame) == 2
    for column in BOOK_TRADE_COLUMNS:
        assert column in frame.columns, column
    # 표는 **청산 시각 순**이라 BTC(10) → ETH(20)다.
    assert list(frame["칸(종목)"]) == ["BTCUSDT", "ETHUSDT"]
    assert list(frame["칸(TF)"]) == ["15m", "1h"]
    assert list(frame["같은분익절"]) == [True, False]
    assert list(frame["재진입"]) == [False, True]
    assert frame["손절가"].iloc[0] == pytest.approx(99.0)
    assert frame["익절가"].iloc[0] == pytest.approx(101.5)
    # net R = 실현손익 ÷ 그 거래의 리스크 금액 — 사람이 계산해도 같아야 한다.
    assert frame["net R"].iloc[0] == pytest.approx(
        frame["손익"].iloc[0] / frame["리스크금액"].iloc[0]
    )


def test_book_trade_frame_reuses_the_shared_display_columns() -> None:
    """열 정의를 복제하지 않는다 — 화면·CSV·DB가 갈라지면 같은 거래가 다르게 보인다."""
    from backtest.report import display_columns

    frame = book_trades_to_display_frame(_segment([BookCell("BTCUSDT", "1h", [_cand(0, 10)])]))
    assert list(frame.columns)[: len(display_columns(include_utc=True))] == list(
        display_columns(include_utc=True)
    )


def test_book_trade_frame_dies_when_the_pairing_contract_breaks() -> None:
    """짝이 어긋나면 칸 라벨이 **다른 거래**에 붙은 표가 조용히 나간다 — 시끄럽게 죽는다."""
    segment = _segment([BookCell("BTCUSDT", "1h", [_cand(0, 10)])])
    segment.outcome.stats.placed_records.clear()
    with pytest.raises(AssertionError, match="길이가 다릅니다"):
        book_trades_to_display_frame(segment)


def test_book_trade_frame_survives_an_empty_book() -> None:
    """거래가 0건인 구간도 **표 골격이 같아야** 한다 — 열이 사라지면 이어붙인 CSV가 어긋난다."""
    frame = book_trades_to_display_frame(_segment([BookCell("BTCUSDT", "1h", [])]))
    assert frame.empty
    for column in BOOK_TRADE_COLUMNS:
        assert column in frame.columns, column


def test_book_equity_frame_ends_at_final_equity() -> None:
    frame = book_equity_to_display_frame(_segment([BookCell("BTCUSDT", "1h", [_cand(0, 10)])]))
    assert not frame.empty
    assert "시각(UTC)" in frame.columns


# --------------------------------------------------------------------------- #
# 2. §2 — 복리 노브가 라벨이 아니다
# --------------------------------------------------------------------------- #


def _repeated_cells(n: int = 12) -> list[BookCell]:
    """한 칸에서 이기는 거래를 반복 — 복리면 뒤로 갈수록 베팅이 커진다."""
    cands = [_cand(i * 10, i * 10 + 5) for i in range(n)]
    return [BookCell("BTCUSDT", "1h", cands)]


def test_compound_sizing_on_is_the_default_and_bit_identical() -> None:
    """기본값이 채택 회계다 — 명시해도 같은 값이어야 한다(옵트인의 정의)."""
    cells = _repeated_cells()
    default = run_leverage_book(cells, _cfg(), LeverageBookParams())
    explicit = run_leverage_book(cells, _cfg(), LeverageBookParams(), compound_sizing=True)
    assert [t.quantity for t in default.trades] == [t.quantity for t in explicit.trades]
    assert [t.realized_pnl for t in default.trades] == [t.realized_pnl for t in explicit.trades]


def test_compound_sizing_off_freezes_the_bet_size() -> None:
    """끄면 베팅이 **안 커진다** — 이기는 거래를 반복해도 수량이 첫 거래와 같다."""
    cells = _repeated_cells()
    on = run_leverage_book(cells, _cfg(), LeverageBookParams(), compound_sizing=True)
    off = run_leverage_book(cells, _cfg(), LeverageBookParams(), compound_sizing=False)
    assert on.trades[-1].quantity > on.trades[0].quantity, "복리 켠 판이 베팅을 안 키운다"
    assert off.trades[-1].quantity == pytest.approx(off.trades[0].quantity)
    # 그런데 **현금은 그대로 쌓인다** — 자본곡선·MDD는 진짜 장부다.
    assert sum(t.realized_pnl for t in off.trades) > 0.0


def test_compound_sizing_off_still_records_the_real_wallet() -> None:
    """`PlacedSetup.equity`는 사이징 자본이 아니라 **진짜 현금**이다(리스크 열의 분모)."""
    off = run_leverage_book(_repeated_cells(), _cfg(), LeverageBookParams(), compound_sizing=False)
    equities = [p.equity for p in off.stats.placed_records]
    assert equities[-1] > equities[0], "복리를 껐다고 현금까지 얼면 자본곡선이 거짓이 된다"


# --------------------------------------------------------------------------- #
# 3. 관측 필드 — 순수 관측인가
# --------------------------------------------------------------------------- #


def _step(t: int, high: float, low: float, close: float) -> SubStep:
    return SubStep(time=t, high=high, low=low, close=close, htf_bar_time=0)


def _simulate(take_profit_price: float | None = 110.0) -> Any:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        substeps=[
            _step(0, high=101.0, low=99.0, close=100.5),
            _step(1, high=111.0, low=100.0, close=110.5),
        ],
        rsi_state=RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=take_profit_price,
        rsi_gate_mode="unconditional",
    )


def test_take_profit_price_is_reported_at_the_fill() -> None:
    """익절 목표는 손절과 같은 자리에서 확정된다 — 지어내지 않고 그 값을 돌려준다."""
    outcome = _simulate()
    assert outcome.status is ZoneLimitStatus.FILLED_EXITED
    assert outcome.exit_reason is SignalExitReason.TAKE_PROFIT
    assert outcome.take_profit_price == pytest.approx(110.0)
    assert outcome.stop_price == pytest.approx(90.0)


def test_take_profit_price_is_none_when_the_target_is_off() -> None:
    """익절이 꺼진 변형에서는 체결돼도 None이다 — 값을 만들어 내면 CSV가 거짓이 된다."""
    outcome = _simulate(take_profit_price=None)
    assert outcome.take_profit_price is None


def test_take_profit_observation_does_not_move_the_trade() -> None:
    """순수 관측이다 — 체결가·청산가·사유가 관측 필드에 흔들리지 않는다."""
    outcome = _simulate()
    assert outcome.entry_price == pytest.approx(100.0)
    assert outcome.exit_price == pytest.approx(110.0)
    assert outcome.exit_time == 1


def test_reentry_label_defaults_to_false() -> None:
    """base 재탭 후보는 라벨이 없다 — 기본이 `False`라 옛 CSV가 비트 재현된다."""
    assert _cand(0, 1).is_reentry is False


# --------------------------------------------------------------------------- #
# 4. 거래당 자 · CAGR
# --------------------------------------------------------------------------- #


class _FakeTrade:
    def __init__(self, pnl: float) -> None:
        self.realized_pnl = pnl


def test_trade_rulers_are_size_normalised() -> None:
    """거래당 net R은 크기 정규화 자다 — USD 합과 갈리는 것이 이 표의 요점이다."""
    pairs = [
        (_FakeTrade(100.0), PlacedSetup(("BTCUSDT", "15m"), 1.0, 50.0, 100.0)),
        (_FakeTrade(-30.0), PlacedSetup(("ETHUSDT", "1h"), 1.0, 30.0, -30.0, is_reentry=True)),
        (
            _FakeTrade(20.0),
            PlacedSetup(("SOLUSDT", "4h"), 1.0, 20.0, 20.0, same_step_take_profit=True),
        ),
    ]
    rulers = trade_rulers(pairs)  # type: ignore[arg-type]
    assert rulers["net_pnl"] == pytest.approx(90.0)
    assert rulers["net_r"] == pytest.approx(2.0 - 1.0 + 1.0)
    assert rulers["mean_net_r"] == pytest.approx(2.0 / 3.0)
    assert rulers["median_net_r"] == pytest.approx(1.0)
    assert rulers["same_step_tp_trades"] == 1
    assert rulers["reentry_trades"] == 1


def test_trade_rulers_handle_an_empty_book() -> None:
    assert trade_rulers([])["mean_net_r"] == 0.0


def test_cagr_refuses_a_meaningless_input() -> None:
    """총수익 ≤ −100%면 실수 거듭제곱이 정의되지 않는다 — 억지로 찍으면 파산과 −99.9%가
    같아 보인다."""
    assert cagr(0.0, 1.0) == pytest.approx(0.0)
    assert cagr(3.0, 2.0) == pytest.approx(1.0)
    assert cagr(-1.0, 6.0) is None
    assert cagr(0.5, 0.0) is None
    # 1년 미만은 측정이 아니라 **외삽**이다 — 석 달치 +1.9%가 연 1,857%로 찍히면 정반대로
    # 읽힌다(축소 실행 스모크에서 실제로 그렇게 나왔다). 채택 구간은 전부 1년을 넘는다.
    assert cagr(0.019, 0.25) is None


def test_span_years_uses_the_traded_span_not_the_window() -> None:
    """`oos_warm`은 창이 아니라 칸별 경계로 잘리므로(WAN-166) 인자 창을 쓰면 CAGR이
    조용히 낙관이 된다."""
    year_ms = int(365.25 * 24 * 3_600_000)
    cells = [BookCell("BTCUSDT", "1h", [_cand(0, year_ms), _cand(year_ms * 2, year_ms * 3)])]
    assert span_years(_segment(cells)) == pytest.approx(3.0, abs=0.01)


# --------------------------------------------------------------------------- #
# 5. 팔 정의 · 채택 좌표 배선
# --------------------------------------------------------------------------- #


def test_arms_form_the_two_by_two() -> None:
    """2×2가 실제로 2×2인가 — (렌즈, 같은 분 익절) 조합이 네 개 모두 있어야 한다."""
    combos = {(ARMS_BY_NAME[a].lens_name, ARMS_BY_NAME[a].no_same_step_tp) for a in ARM_ORDER}
    assert combos == {
        ("baseline", False),
        ("pen_5bp", False),
        ("baseline", True),
        ("pen_5bp", True),
    }
    assert ARMS_BY_NAME[ADOPTED_ARM].is_adopted, "팔 A가 채택 북이 아니면 검산 (a)가 성립 안 한다"
    worst = ARMS_BY_NAME[MOST_CONSERVATIVE_ARM]
    assert worst.lens_name == "pen_5bp" and worst.no_same_step_tp


def test_only_the_adopted_arm_claims_the_adopted_identity() -> None:
    """보수 축을 켠 팔은 `harness.run_once`와 어긋나는 것이 **정상**이다 — 검산을 켜 두면
    정상 실행이 실패로 보인다."""
    assert [ARMS_BY_NAME[a].is_adopted for a in ARM_ORDER] == [True, False, False, False]


def test_run_arm_matches_the_adopted_book_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 표가 「채택 북 위에서 잰 값」이라는 주장의 유일한 증거 — 실제 호출 인자 대조."""
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan346_conservative_book.run_cells", _fake)
    monkeypatch.setattr(book_cli, "run_cells", _fake)
    monkeypatch.setattr(book_cli, "apply_funding_proxy", lambda p: (list(p), ""))
    monkeypatch.setattr("backtest.wan336_same_step_tp.apply_funding_proxy", lambda p: (list(p), ""))

    run_arm(
        ["BTCUSDT"],
        ["1h"],
        ARMS_BY_NAME[ADOPTED_ARM],
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
    assert mine["no_same_step_tp"] is False
    assert mine["fill"] is None, "채택 팔이 렌즈를 복사하면 기본값이 움직일 때 갈라진다"


def test_conservative_arms_carry_their_axes_and_drop_the_engine_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """팔 이름이 실제 인자로 이어지는가 — 라벨만 붙고 기본 엔진이 도는 것이 이 저장소가
    반복해 겪은 실패다(WAN-91/95/112/123/159)."""
    captured: list[dict[str, Any]] = []

    def _fake(*_args: Any, **kwargs: Any) -> list[Any]:
        captured.append(kwargs)
        return []

    monkeypatch.setattr("backtest.wan346_conservative_book.run_cells", _fake)
    monkeypatch.setattr("backtest.wan336_same_step_tp.apply_funding_proxy", lambda p: (list(p), ""))
    expected = {
        "A": (None, False, True),
        "B": ("pen_5bp", False, False),
        "C": (None, True, False),
        "D": ("pen_5bp", True, False),
    }
    for name, (lens, flag, check) in expected.items():
        captured.clear()
        run_arm(
            ["BTCUSDT"],
            ["1h"],
            ARMS_BY_NAME[name],
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            jobs=1,
            segments=(harness.SEGMENT_FULL,),
            log=False,
        )
        got = captured[0]
        assert (got["fill"].name if got["fill"] else None) == lens, name
        assert got["no_same_step_tp"] is flag, name
        assert got["engine_check"] is check, name


def test_detail_paths_name_the_arm_and_the_segment() -> None:
    """파일 이름이 팔·구간을 밝힌다 — 「채택 북의 거래」로 오인용되는 것이 WAN-106의 교훈."""
    trades, equity = detail_paths(MOST_CONSERVATIVE_ARM, "oos_warm")
    assert trades.name == "wan346_trades_D_oos_warm.csv"
    assert equity.name == "wan346_equity_D_oos_warm.csv"


# --------------------------------------------------------------------------- #
# 6. 요약 — 판정문이 실제 숫자를 읽는가
# --------------------------------------------------------------------------- #


def _row(arm: str, compounding: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "arm": arm,
        "arm_label": ARMS_BY_NAME[arm].label,
        "lens": ARMS_BY_NAME[arm].lens_name,
        "no_same_step_tp": ARMS_BY_NAME[arm].no_same_step_tp,
        "compounding": compounding,
        "segment": "oos_warm",
        "num_cells": 48,
        "num_trades": 6336,
        "win_rate": 0.55,
        "total_return": 10.0,
        "cagr": 0.5,
        "span_years": 6.0,
        "net_pnl": 1000.0,
        "net_r": 1257.9,
        "mean_net_r": 0.2,
        "median_net_r": 0.0,
        "profit_factor": 1.4,
        "max_drawdown": 0.229,
        "return_over_mdd": 40.0,
        "ruin": False,
        "peak_concurrency": 14,
        "max_concurrent_risk": 0.1176,
        "max_effective_concurrent_risk": 0.1176,
        "liquidation_events": 0,
        "same_step_tp_trades": 467,
        "same_step_tp_trade_share": 0.0737,
        "candidate_same_step_tps": 6787,
        "reentry_trades": 900,
    }
    base.update(over)
    return base


def test_summary_flags_the_ruin_line_even_with_zero_liquidations() -> None:
    """「청산 0건」을 안전 근거로 쓰지 말라는 것이 WAN-312 §4의 요점이다."""
    frame = pd.DataFrame(
        [
            _row("A", COMPOUND_ON),
            _row("A", COMPOUND_OFF, total_return=1.2),
            _row(
                "D",
                COMPOUND_ON,
                max_drawdown=RUIN_MDD + 0.1,
                ruin=True,
                candidate_same_step_tps=0,
                mean_net_r=0.05,
            ),
            _row("D", COMPOUND_OFF, total_return=0.4, candidate_same_step_tps=0),
        ]
    )
    text = build_summary(frame, pd.DataFrame())
    assert "파괴선" in text
    assert "🚨 **파괴선(MDD 50%)을 넘는다**" in text
    assert "검산 (b) 통과" in text
    assert "검산 (c)" in text and "실제로 옮겼다" in text


def test_summary_reports_a_stuck_compounding_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """복리 끈 판이 총수익을 못 옮기면 노브가 안 걸린 것이다 — 조용히 넘기지 않는다."""
    frame = pd.DataFrame([_row("A", COMPOUND_ON), _row("A", COMPOUND_OFF)])
    text = build_summary(frame, pd.DataFrame())
    assert "옮기지 못했다" in text


def test_summary_catches_a_counterfactual_arm_that_did_nothing() -> None:
    """반사실 팔에 「같은 분 익절」 후보가 남아 있으면 팔이 라벨만 붙은 것이다."""
    frame = pd.DataFrame(
        [_row("A", COMPOUND_ON), _row("D", COMPOUND_ON, candidate_same_step_tps=123)]
    )
    text = build_summary(frame, pd.DataFrame())
    assert "검산 (b) 실패" in text


def test_summary_names_the_costlier_axis() -> None:
    """2×2를 쌓은 값은 「합쳐서 얼마」가 아니라 **「어느 쪽이 얼마」**다 — 한 축씩 켠 팔이
    있어야 읽히고, 요약이 그걸 문장으로 낸다."""
    frame = pd.DataFrame(
        [
            _row("A", COMPOUND_ON, mean_net_r=0.200),
            _row("A", COMPOUND_OFF, total_return=1.2),
            _row("B", COMPOUND_ON, mean_net_r=0.050),  # 체결 축이 −0.150
            _row("C", COMPOUND_ON, mean_net_r=0.180, candidate_same_step_tps=0),
            _row("D", COMPOUND_ON, mean_net_r=0.040, candidate_same_step_tps=0),
        ]
    )
    text = build_summary(frame, pd.DataFrame())
    assert "어느 축이 더 비싼가" in text
    assert "체결(`pen_5bp`) 쪽이 더 크다" in text


def test_summary_axis_note_is_silent_without_the_middle_arms() -> None:
    """팔 B·C가 없으면 「어느 축이 비싼가」를 물을 수 없다 — 지어내지 않는다."""
    frame = pd.DataFrame([_row("A", COMPOUND_ON), _row("D", COMPOUND_ON)])
    assert "어느 축이 더 비싼가" not in build_summary(frame, pd.DataFrame())
