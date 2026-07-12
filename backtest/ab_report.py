"""A안(종가/확정봉) vs B안(존-지정가/실시간 RSI) 비교 리포트 (WAN-41).

같은 심볼·타임프레임·기간에서 진입 방식만 A/B로 바꾼 백테스트 결과를 나란히 놓고
거래 수·승률·손익비·평균 거래수익률·총수익률·MDD·샤프를 CSV로 낸다. 심볼·TF별
행에 더해, 모든 셋업의 **거래를 풀링한 합산 행**(symbol/timeframe=`ALL`)을 변형별로
붙인다. 수수료·슬리피지 비용 모델은 A·B가 동일해야 비교가 공정하다(호출자 책임).

거래를 풀링할 수 있는 지표(거래 수·승률·손익비·평균 수익률)만 합산 행에 채우고,
자본곡선에 의존하는 지표(총수익률·MDD·샤프)는 독립 실행을 뭉뚱그릴 수 없어 합산
행에서는 비운다.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass

from backtest.models import BacktestResult, Trade

#: 리포트 컬럼 순서.
_COLUMNS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "variant",
    "num_trades",
    "num_wins",
    "num_losses",
    "win_rate",
    "profit_factor",
    "avg_win",
    "avg_loss",
    "avg_trade_return",
    "total_return",
    "max_drawdown",
    "sharpe",
    # WAN-46 진단: 지정가 체결률·관통(낙관 편향 감사). B안에서만 채워지고 A안은 빈 칸.
    "eligible_setups",
    "num_filled",
    "fill_rate",
    "num_penetrations",
)


@dataclass(frozen=True)
class ABEntry:
    """비교 대상 한 칸: 심볼·TF·변형(A/B)과 그 백테스트 결과.

    `eligible_setups`·`num_filled`·`num_penetrations`는 존-지정가(B안) 진단 통계로,
    지정가 체결률과 관통(같은 스텝 진입+손절) 감사에 쓴다. 확정봉 종가 진입(A안)에는
    해당 개념이 없어 None이며 리포트에서 빈 칸으로 남는다(WAN-46).
    """

    symbol: str
    timeframe: str
    variant: str
    result: BacktestResult
    eligible_setups: int | None = None
    num_filled: int | None = None
    num_penetrations: int | None = None


def _fmt(value: float | int | None) -> str:
    """리포트 셀 포맷. None은 빈 칸, 실수는 6자리 반올림."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{round(value, 6)}"
    return str(value)


def _fill_rate(eligible: int | None, filled: int | None) -> float | None:
    """지정가 체결률 = filled / eligible. 대상 셋업이 없거나 미측정이면 None."""
    if not eligible or filled is None:
        return None
    return filled / eligible


def _row_from_entry(entry: ABEntry) -> dict[str, str]:
    m = entry.result.metrics
    return {
        "symbol": entry.symbol,
        "timeframe": entry.timeframe,
        "variant": entry.variant,
        "num_trades": _fmt(m.num_trades),
        "num_wins": _fmt(m.num_wins),
        "num_losses": _fmt(m.num_losses),
        "win_rate": _fmt(m.win_rate),
        "profit_factor": _fmt(m.profit_factor),
        "avg_win": _fmt(m.avg_win),
        "avg_loss": _fmt(m.avg_loss),
        "avg_trade_return": _fmt(_avg_trade_return(entry.result.trades)),
        "total_return": _fmt(m.total_return),
        "max_drawdown": _fmt(m.max_drawdown),
        "sharpe": _fmt(m.sharpe),
        "eligible_setups": _fmt(entry.eligible_setups),
        "num_filled": _fmt(entry.num_filled),
        "fill_rate": _fmt(_fill_rate(entry.eligible_setups, entry.num_filled)),
        "num_penetrations": _fmt(entry.num_penetrations),
    }


def _avg_trade_return(trades: list[Trade]) -> float | None:
    """거래별 수익률(`return_pct`) 평균. 거래가 없으면 None."""
    if not trades:
        return None
    return sum(t.return_pct for t in trades) / len(trades)


def _aggregate_row(variant: str, entries: list[ABEntry]) -> dict[str, str]:
    """한 변형의 모든 셋업 거래를 풀링한 합산 행(자본곡선 지표는 비움)."""
    trades: list[Trade] = [t for e in entries for t in e.result.trades]
    num_trades = len(trades)
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    gross_profit = sum(t.realized_pnl for t in wins)
    gross_loss = -sum(t.realized_pnl for t in losses)  # 양수로
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    eligible = _sum_optional(e.eligible_setups for e in entries)
    num_filled = _sum_optional(e.num_filled for e in entries)
    penetrations = _sum_optional(e.num_penetrations for e in entries)
    return {
        "symbol": "ALL",
        "timeframe": "ALL",
        "variant": variant,
        "num_trades": _fmt(num_trades),
        "num_wins": _fmt(len(wins)),
        "num_losses": _fmt(len(losses)),
        "win_rate": _fmt(len(wins) / num_trades if num_trades else None),
        "profit_factor": _fmt(profit_factor),
        "avg_win": _fmt(gross_profit / len(wins) if wins else None),
        "avg_loss": _fmt(-gross_loss / len(losses) if losses else None),
        "avg_trade_return": _fmt(_avg_trade_return(trades)),
        "total_return": "",
        "max_drawdown": "",
        "sharpe": "",
        "eligible_setups": _fmt(eligible),
        "num_filled": _fmt(num_filled),
        "fill_rate": _fmt(_fill_rate(eligible, num_filled)),
        "num_penetrations": _fmt(penetrations),
    }


def _sum_optional(values: Iterable[int | None]) -> int | None:
    """None이 아닌 값만 합산. 전부 None이면 None(미측정)."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def build_ab_report(entries: list[ABEntry]) -> str:
    """A/B 비교 리포트를 CSV 문자열로 만든다.

    심볼·TF·변형별 행을 (심볼, TF, 변형) 순으로 정렬해 쓰고, 변형별 합산 행을
    (등장 순서 유지) 맨 끝에 붙인다. 결정적(deterministic)이라 재현 가능하다.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_COLUMNS))
    writer.writeheader()

    for entry in sorted(entries, key=lambda e: (e.symbol, e.timeframe, e.variant)):
        writer.writerow(_row_from_entry(entry))

    # 변형별 합산(등장 순서 보존).
    seen: list[str] = []
    for entry in entries:
        if entry.variant not in seen:
            seen.append(entry.variant)
    for variant in seen:
        writer.writerow(_aggregate_row(variant, [e for e in entries if e.variant == variant]))

    return buffer.getvalue()
