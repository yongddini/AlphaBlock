"""사람이 읽는 거래 표 (WAN-146).

화면(대시보드)과 파일(CSV — WAN-106)이 공유하는 `trades_to_display_frame`의 파생값과,
그 위에 얹히는 표시 계층(`dashboard.trade_table`)을 검증한다. 핵심은 **표의 숫자가 상단
성과 지표와 갈라지지 않는 것** — 두 표시가 다른 값을 내면 어느 쪽을 믿어야 할지 알 수 없다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pandas as pd
import pytest

from backtest import BacktestConfig, PositionSide
from backtest.metrics import build_metrics
from backtest.models import BacktestResult, EquityPoint, ExitReason, Trade, TradeFill
from backtest.report import (
    COL_ENTRY_KST,
    COL_ENTRY_UTC,
    COL_EQUITY_AFTER,
    COL_EQUITY_BEFORE,
    COL_EXIT_KST,
    COL_EXIT_REASON,
    COL_HOLDING_HOURS,
    COL_NOTIONAL,
    COL_NOTIONAL_PCT,
    COL_PNL,
    COL_QUANTITY,
    COL_RETURN_PCT,
    display_columns,
    trades_to_display_frame,
)
from dashboard.trade_table import (
    engine_label_caption,
    parse_selected_rows,
    selected_trade_window,
    style_trade_frame,
)
from strategy.models import ConfluenceParams

_STEP = 3_600_000  # 1시간

#: 한국시간 오전 9시 = UTC 자정. KST 변환이 실제로 일어났는지 이 오프셋으로 확인한다.
_UTC_MIDNIGHT_MS = int(datetime(2025, 3, 14, 0, 0, tzinfo=UTC).timestamp() * 1000)


def _df(bars: Sequence[tuple[float, float, float, float]], *, start_ms: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [start_ms + i * _STEP for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [10.0] * len(bars),
        }
    )


def _long_trade(
    *,
    entry_ms: int,
    entry_price: float,
    exit_ms: int,
    exit_price: float,
    quantity: float,
    reason: ExitReason,
) -> Trade:
    realized = (exit_price - entry_price) * quantity
    return Trade(
        side=PositionSide.LONG,
        entry_time=entry_ms,
        entry_price=entry_price,
        quantity=quantity,
        entry_fee=0.0,
        exits=[
            TradeFill(time=exit_ms, price=exit_price, quantity=quantity, fee=0.0, reason=reason)
        ],
        funding_cost=0.0,
        realized_pnl=realized,
        return_pct=(exit_price - entry_price) / entry_price,
    )


def _win_then_loss() -> BacktestResult:
    """익절 1건 + 손절 1건. 비용 0·전액 진입이라 손익이 손으로 계산된다.

    옛 A안 엔진(`run_backtest`)이 WAN-208/WAN-215로 제거돼, 표시 계층이 읽는
    `BacktestResult`(거래·지표)를 **직접 구성**한다 — 진입가 100→익절 110(+1,000),
    시드 11,000에서 재진입 110→손절 104.5(−550) → 최종 10,450. 엔진이 내던 값과 같다.
    """
    win = _long_trade(
        entry_ms=_UTC_MIDNIGHT_MS,
        entry_price=100.0,
        exit_ms=_UTC_MIDNIGHT_MS + _STEP,
        exit_price=110.0,
        quantity=100.0,
        reason=ExitReason.TAKE_PROFIT,
    )
    loss = _long_trade(
        entry_ms=_UTC_MIDNIGHT_MS + 2 * _STEP,
        entry_price=110.0,
        exit_ms=_UTC_MIDNIGHT_MS + 3 * _STEP,
        exit_price=104.5,
        quantity=100.0,
        reason=ExitReason.STOP_LOSS,
    )
    trades = [win, loss]
    equities = [10_000.0, 11_000.0, 11_000.0, 10_450.0]
    metrics = build_metrics(
        initial_capital=10_000.0,
        equities=equities,
        trades=trades,
    )
    equity_curve = [
        EquityPoint(time=_UTC_MIDNIGHT_MS + i * _STEP, equity=eq) for i, eq in enumerate(equities)
    ]
    cfg = BacktestConfig(
        initial_capital=10_000.0,
        fee_rate=0.0,
        slippage=0.0,
        position_fraction=1.0,
        risk_sizing=None,
    )
    return BacktestResult(config=cfg, trades=trades, equity_curve=equity_curve, metrics=metrics)


def test_times_are_rendered_in_korean_time() -> None:
    """UTC 자정 진입은 KST 오전 9시로 보여야 한다(내부 값은 UTC 그대로)."""
    result = _win_then_loss()

    frame = trades_to_display_frame(result)

    assert frame.loc[0, COL_ENTRY_KST] == "2025-03-14 09:00"
    assert frame.loc[0, COL_EXIT_KST] == "2025-03-14 10:00"
    # 원본(엔진)은 손대지 않는다 — 변환은 표시 계층에서만 일어난다.
    assert result.trades[0].entry_time == _UTC_MIDNIGHT_MS


def test_utc_columns_are_opt_in_for_file_export() -> None:
    """화면은 KST 단독, 파일(WAN-106)은 UTC 병기 — 같은 함수가 둘 다 낸다."""
    result = _win_then_loss()

    screen = trades_to_display_frame(result)
    exported = trades_to_display_frame(result, include_utc=True)

    assert COL_ENTRY_UTC not in screen.columns
    assert exported.loc[0, COL_ENTRY_UTC] == "2025-03-14 00:00"
    assert exported.loc[0, COL_ENTRY_KST] == "2025-03-14 09:00"


def test_entry_notional_and_seed_share_answer_how_much_went_in() -> None:
    result = _win_then_loss()
    trade = result.trades[0]

    frame = trades_to_display_frame(result)

    expected = trade.entry_price * trade.quantity
    assert frame.loc[0, COL_NOTIONAL] == pytest.approx(expected)
    assert frame.loc[0, COL_QUANTITY] == pytest.approx(trade.quantity)
    assert frame.loc[0, COL_NOTIONAL_PCT] == pytest.approx(expected / 10_000.0 * 100.0)
    assert frame.loc[0, COL_HOLDING_HOURS] == pytest.approx(1.0)


def test_equity_chain_matches_top_metrics() -> None:
    """🔒 표의 합계(거래 수·최종 시드)가 상단 지표와 일치한다 — 완료 기준.

    표는 초기자본에서 시작해 거래별 순손익을 누적하고, 지표는 엔진의 자본곡선 끝값을
    쓴다. 두 경로가 갈라지면 화면이 서로 다른 "내 돈"을 말하게 된다.
    """
    result = _win_then_loss()

    frame = trades_to_display_frame(result)

    assert len(frame) == result.metrics.num_trades
    assert frame.loc[0, COL_EQUITY_BEFORE] == pytest.approx(result.metrics.initial_capital)
    assert frame[COL_EQUITY_AFTER].iloc[-1] == pytest.approx(result.metrics.final_equity)
    # 행마다 시드(전) → 시드(후)가 손익만큼 움직이고, 다음 행의 시드(전)로 이어진다.
    for i in range(len(frame)):
        assert frame.loc[i, COL_EQUITY_AFTER] == pytest.approx(
            frame.loc[i, COL_EQUITY_BEFORE] + frame.loc[i, COL_PNL]
        )
        if i:
            assert frame.loc[i, COL_EQUITY_BEFORE] == pytest.approx(
                frame.loc[i - 1, COL_EQUITY_AFTER]
            )


def test_exit_reasons_are_korean_labels() -> None:
    result = _win_then_loss()

    frame = trades_to_display_frame(result)

    assert list(frame[COL_EXIT_REASON]) == ["익절", "손절"]
    assert frame.loc[0, COL_PNL] > 0
    assert frame.loc[1, COL_PNL] < 0
    assert frame.loc[1, COL_RETURN_PCT] < 0


def test_engine_labels_are_not_repeated_in_every_row() -> None:
    """매 행에서 값이 같던 엔진 라벨 6개는 표 본문에서 빠진다(삭제가 아니라 이동)."""
    result = _win_then_loss()

    columns = set(trades_to_display_frame(result).columns)

    assert not columns & {
        "entry_mode",
        "rsi_mode",
        "combine_obs",
        "sizing_mode",
        "risk_per_trade",
        "funding_coverage",
    }


def test_engine_labels_survive_in_the_caption() -> None:
    """⚠️ WAN-65의 요구("어떤 설정으로 나온 거래인지 안다")는 표 밖에서 지켜진다."""
    from strategy.models import OrderBlockParams

    result = _win_then_loss()

    caption = engine_label_caption(
        result,
        ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime"),
        OrderBlockParams(combine_obs=True),
        result.config,
    )

    for label in ("entry_mode", "rsi_mode", "combine_obs", "sizing_mode", "risk_per_trade"):
        assert label in caption
    assert "funding_coverage" in caption


def test_display_columns_stable_when_no_trades() -> None:
    """거래 0건이어도 표 골격(컬럼)은 같다 — 빈 화면에서 컬럼이 사라지면 안 된다."""
    metrics = build_metrics(initial_capital=10_000.0, equities=[10_000.0], trades=[])
    result = BacktestResult(config=BacktestConfig(), trades=[], equity_curve=[], metrics=metrics)

    frame = trades_to_display_frame(result)

    assert frame.empty
    assert tuple(frame.columns) == display_columns()


def test_style_colors_wins_green_and_losses_red() -> None:
    """익절/손절이 색으로 갈린다(차트 마커에서 텍스트를 뺀 만큼 표가 사유를 진다)."""
    result = _win_then_loss()
    frame = trades_to_display_frame(result)

    html = style_trade_frame(frame).to_html()

    assert "#2e7d32" in html  # 익절 초록
    assert "#c62828" in html  # 손절 빨강
    # 숫자 포맷도 사람이 읽는 형태다(천단위 구분·부호).
    assert "+" in html


def test_selected_row_maps_to_that_trades_window() -> None:
    result = _win_then_loss()

    window = selected_trade_window(result, [1])

    trade = result.trades[1]
    assert window == (trade.entry_time, trade.exit_time)
    assert trade.side is PositionSide.LONG


class _Selection:
    rows = [2]


class _State:
    selection = _Selection()


def test_selection_state_parsed_from_dict_or_object() -> None:
    """Streamlit 버전에 따라 선택 상태 모양이 달라도 같은 답을 낸다."""
    assert parse_selected_rows({"selection": {"rows": [1], "columns": []}}) == [1]
    assert parse_selected_rows(_State()) == [2]
    assert parse_selected_rows(None) == []
    assert parse_selected_rows({"selection": {"rows": []}}) == []
    assert parse_selected_rows({}) == []


def test_selection_out_of_range_is_ignored() -> None:
    """표와 결과가 어긋난 재실행 순간에도 화면이 깨지지 않는다."""
    result = _win_then_loss()

    assert selected_trade_window(result, []) is None
    assert selected_trade_window(result, [99]) is None
    assert selected_trade_window(result, [-1]) is None
