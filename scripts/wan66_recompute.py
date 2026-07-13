"""WAN-66 — 익절 목표선 오기(EMA 6개 → EMA 60 + VWMA 100) 전/후 재산출.

WAN-23 명세가 "차트 표시선"(EMA 20/60/120/240/365)과 "익절 목표선"을 한 배열로
뒤섞어 적어, 익절 판정이 표시선 5개 전부를 후보로 써 왔다 — 가장 빠른 EMA 20에서
사실상 항상 조기 익절. 사용자 확정 규칙은 익절선 = EMA 60 + VWMA 100 두 개다.

이 스크립트는 동일 조건(병합 존 + 펀딩비 + 리스크 사이징)에서 **BEFORE(EMA
20/60/120/240/365 + VWMA 100 익절)**와 **AFTER(EMA 60 + VWMA 100 익절)**의 성과를
(심볼 × TF) × (롱/숏)으로 분해 재산출하고, 특히 **평균 익절 거리**가 얼마나 늘어나는지
정량화한다. 오더블록 탐지는 confluence 파라미터와 무관하므로 BEFORE/AFTER가 공유한다.

사용법::

    uv run python scripts/wan66_recompute.py \
        --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT \
        --timeframes 15m,1h,2h,4h,1d --out reports/wan66_recompute.csv
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from pathlib import Path

import pandas as pd

from backtest.models import BacktestResult, ExitReason, PositionSide
from backtest.sweep import default_backtest_config, evaluate
from config.settings import get_settings
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import (
    DEFAULT_CONFLUENCE_EMA_LENGTHS,
    ConfluenceParams,
    OrderBlockParams,
)
from strategy.order_blocks import OrderBlockDetector

# BEFORE: 버그 상태 — 표시선 5개 EMA를 전부 익절 후보로(WAN-66 이전 기본값).
_BEFORE = ConfluenceParams(tp_ema_lengths=DEFAULT_CONFLUENCE_EMA_LENGTHS, tp_vwma_length=100)
# AFTER: 사용자 확정 규칙 — EMA 60 + VWMA 100 (WAN-66 이후 기본값).
_AFTER = ConfluenceParams()


def _load_funding(symbol: str, db_path: str) -> list[FundingRate]:
    if not Path(db_path).exists():
        return []
    with FundingRateStore(db_path) as store:
        return store.get_rates(symbol, include_predicted=True)


def _equity_at(result: BacktestResult, entry_time: int) -> float:
    """진입 봉 시점(직전 확정 자본)의 계좌 평가금. 사이징의 리스크 기준이 된다."""
    times = [p.time for p in result.equity_curve]
    if not times:
        return result.config.initial_capital
    idx = bisect_right(times, entry_time) - 1
    if idx < 0:
        return result.config.initial_capital
    return result.equity_curve[idx].equity


def _side_stats(
    result: BacktestResult, side: PositionSide, risk_per_trade: float | None
) -> dict[str, float]:
    """한 방향(롱/숏)의 성과 지표. 평균 익절 거리·평균 R 포함."""
    trades = [t for t in result.trades if t.side is side]
    n = len(trades)
    wins = sum(1 for t in trades if t.is_win)
    gross_profit = sum(t.realized_pnl for t in trades if t.realized_pnl > 0)
    gross_loss = -sum(t.realized_pnl for t in trades if t.realized_pnl < 0)
    sum_return_pct = sum(t.return_pct for t in trades)

    # 평균 익절 거리: 익절(take_profit)로 청산된 거래의 진입가 대비 유리 방향 이동 %.
    tp_dists: list[float] = []
    for t in trades:
        if t.exits[-1].reason is not ExitReason.TAKE_PROFIT:
            continue
        exit_price = t.exits[-1].price
        sign = 1.0 if side is PositionSide.LONG else -1.0
        tp_dists.append(sign * (exit_price - t.entry_price) / t.entry_price)

    # 평균 R: 순손익 / 리스크 금액(= risk_per_trade × 진입 시점 자본). 사이징이 목표로
    # 삼는 R 그 자체다. 사이징이 꺼져 있으면(None) R은 정의하지 않는다.
    r_multiples: list[float] = []
    if risk_per_trade is not None:
        for t in trades:
            risk_amount = risk_per_trade * _equity_at(result, t.entry_time)
            if risk_amount > 0:
                r_multiples.append(t.realized_pnl / risk_amount)

    return {
        "num_trades": float(n),
        "num_wins": float(wins),
        "win_rate": wins / n if n else 0.0,
        "avg_r": sum(r_multiples) / len(r_multiples) if r_multiples else float("nan"),
        "num_tp_exits": float(len(tp_dists)),
        "avg_tp_distance_pct": sum(tp_dists) / len(tp_dists) if tp_dists else float("nan"),
        "sum_return_pct": sum_return_pct,
        "realized_pnl": sum(t.realized_pnl for t in trades),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("nan"),
    }


def _rows_for_combo(
    symbol: str, timeframe: str, df: pd.DataFrame, funding: list[FundingRate]
) -> list[dict[str, object]]:
    """한 (심볼, TF)의 BEFORE/AFTER × 롱/숏 행들을 만든다."""
    cfg = default_backtest_config(timeframe).model_copy(
        update={"funding_enabled": bool(funding), "funding_missing_policy": "zero"}
    )
    # 오더블록 탐지는 confluence 파라미터와 무관 — BEFORE/AFTER가 공유한다.
    ob_result = OrderBlockDetector(OrderBlockParams()).run(df)
    rows: list[dict[str, object]] = []
    for version, params in (("before", _BEFORE), ("after", _AFTER)):
        result = evaluate(
            df,
            confluence_params=params,
            backtest_config=cfg,
            order_block_result=ob_result,
            funding_rates=funding or None,
        )
        for side in (PositionSide.LONG, PositionSide.SHORT):
            stats = _side_stats(result, side, cfg.risk_per_trade)
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "version": version,
                    "side": side.value,
                    "total_return": result.metrics.total_return,
                    "max_drawdown": result.metrics.max_drawdown,
                    "funding_coverage": result.metrics.funding_coverage,
                    **stats,
                }
            )
    return rows


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT")
    parser.add_argument("--timeframes", default="15m,1h,2h,4h,1d")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--out", type=Path, default=Path("reports/wan66_recompute.csv"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    db_path = args.db_path or get_settings().db_path
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    all_rows: list[dict[str, object]] = []
    for symbol in symbols:
        funding = _load_funding(symbol, db_path)
        for timeframe in timeframes:
            with OhlcvStore(db_path) as store:
                df = store.load(symbol, timeframe)
            if df.empty:
                print(f"[skip] {symbol} {timeframe}: 데이터 없음")
                continue
            rows = _rows_for_combo(symbol, timeframe, df, funding)
            all_rows.extend(rows)
            print(f"[done] {symbol} {timeframe}: bars={len(df)} funding={len(funding)}")

    out = pd.DataFrame(all_rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"\n재산출 CSV 저장: {args.out} ({len(out)} 행)")


if __name__ == "__main__":
    main()
