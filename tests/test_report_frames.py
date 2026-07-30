"""리포트 프레임·CSV·요약 렌더 테스트 (WAN-65 / WAN-215).

`backtest.report`의 거래/자본곡선 DataFrame·CSV 라이터·요약 텍스트가 결과의 값을
그대로 실어 내는지 검증한다. 옛 A안 엔진(`run_backtest`)이 WAN-208/WAN-215로 제거돼,
이 테스트들은 `BacktestResult`를 **직접 구성**해 리포트 헬퍼만 격리 검증한다
(엔진과 무관하게 리포트 계층을 지킨다 — 결과 생성 경로는 `test_zone_limit_backtest`가
지정가(B안) 엔진으로 따로 덮는다).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.metrics import build_metrics
from backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    ExitReason,
    PositionSide,
    Trade,
    TradeFill,
)
from backtest.report import (
    format_summary,
    sizing_mode_banner,
    summary_dict,
    trades_to_dataframe,
    write_equity_csv,
    write_trades_csv,
)
from execution import PositionSizingParams
from strategy.models import ConfluenceParams, OrderBlockParams


def _winning_trade(pnl: float = 1_000.0) -> Trade:
    """익절로 청산된 롱 거래 하나(진입가 100, 수량 100 → 실현손익 `pnl`)."""
    return Trade(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        quantity=100.0,
        entry_fee=0.0,
        exits=[
            TradeFill(
                time=120_000,
                price=110.0,
                quantity=100.0,
                fee=0.0,
                reason=ExitReason.TAKE_PROFIT,
            )
        ],
        funding_cost=0.0,
        realized_pnl=pnl,
        return_pct=pnl / 10_000.0,
    )


def _result(
    *,
    risk_sizing: PositionSizingParams | None = None,
    with_equity: bool = True,
) -> BacktestResult:
    trades = [_winning_trade()]
    equities = [10_000.0, 11_000.0]
    metrics = build_metrics(
        initial_capital=10_000.0,
        equities=equities,
        trades=trades,
        funding_coverage=None,
    )
    cfg = BacktestConfig(risk_sizing=risk_sizing) if risk_sizing else BacktestConfig()
    equity_curve = (
        [EquityPoint(time=0, equity=10_000.0), EquityPoint(time=120_000, equity=11_000.0)]
        if with_equity
        else []
    )
    return BacktestResult(config=cfg, trades=trades, equity_curve=equity_curve, metrics=metrics)


def test_report_dataframes_and_summary() -> None:
    result = _result()

    trades_df = trades_to_dataframe(result)
    assert len(trades_df) == 1
    assert "realized_pnl" in trades_df.columns

    summary = summary_dict(result)
    assert summary["num_trades"] == 1
    assert "seed" in summary

    text = format_summary(result)
    assert "Total Return" in text
    assert "Params" in text


def test_summary_reports_sizing_mode() -> None:
    """WAN-65: 요약·리포트 텍스트에 사이징 방식이 드러나고, 전액 진입이면 배너가 뜬다."""
    unsized = _result()
    summary = summary_dict(unsized)
    assert summary["sizing_mode"] == "full_position"
    assert summary["risk_per_trade"] is None
    assert "sizing_mode=full_position" in format_summary(unsized)
    assert sizing_mode_banner(unsized) is not None

    sized = _result(risk_sizing=PositionSizingParams(risk_per_trade=0.02))
    summary = summary_dict(sized)
    assert summary["sizing_mode"] == "risk_sizing"
    assert summary["risk_per_trade"] == pytest.approx(0.02)
    assert sizing_mode_banner(sized) is None


def test_reports_carry_entry_mode_rsi_mode_combine_obs() -> None:
    """WAN-65: 거래/요약 리포트에 진입 방식·RSI 모드·병합 여부가 함께 기록된다.

    이 컬럼들이 없으면 CSV 파일만 봐서는 병합이 켜졌는지 알 수 없다(WAN-47/56/59/63과
    동일 패턴의 재발 방지).
    """
    result = _result()
    conf = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    ob = OrderBlockParams(combine_obs=False)

    summary = summary_dict(result, confluence=conf, order_block=ob)
    assert summary["entry_mode"] == "zone_limit"
    assert summary["rsi_mode"] == "realtime"
    assert summary["combine_obs"] is False

    text = format_summary(result, confluence=conf, order_block=ob)
    assert "entry_mode=zone_limit" in text
    assert "combine_obs=False" in text

    trades_df = trades_to_dataframe(result, confluence=conf, order_block=ob)
    assert (trades_df["entry_mode"] == "zone_limit").all()
    assert (trades_df["combine_obs"] == False).all()  # noqa: E712

    # WAN-95: 명시하지 않으면 "unknown"이다 — 모르면 모른다고 적는다. 컬럼 자체는
    # 항상 존재한다.
    default_summary = summary_dict(result)
    assert default_summary["entry_mode"] == "unknown"
    assert default_summary["rsi_mode"] == "unknown"
    # ⚠️ `combine_obs`만 "unknown"이 아니라 **채택 기본값**으로 채워진다(`report.py`가
    # `OrderBlockParams()`로 폴백한다). 리터럴로 박으면 기본값이 움직일 때 깨지므로
    # 기본값에서 읽는다.
    assert default_summary["combine_obs"] is OrderBlockParams().combine_obs


def test_csv_writers(tmp_path: Path) -> None:
    result = _result()

    trades_path = write_trades_csv(result, tmp_path / "trades.csv")
    equity_path = write_equity_csv(result, tmp_path / "equity.csv")
    assert trades_path.exists()
    assert equity_path.exists()

    loaded = pd.read_csv(trades_path)
    assert len(loaded) == 1
    assert loaded["realized_pnl"].iloc[0] == pytest.approx(1_000.0)

    equity_loaded = pd.read_csv(equity_path)
    assert len(equity_loaded) == 2
    assert equity_loaded["equity"].iloc[-1] == pytest.approx(11_000.0)
