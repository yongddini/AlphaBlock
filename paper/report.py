"""페이퍼 성과 표·CSV·요약 텍스트 (WAN-33).

`paper.store`/`paper.performance`가 만든 값 객체를 사람이 읽는 요약 문자열과 pandas
DataFrame(CSV 내보내기·대시보드 표)으로 변환한다. 스크립트(`scripts/paper_report.py`)와
대시보드(`dashboard/app.py`)가 공용으로 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

# 표시 계층은 "저장된 거래" 탭(WAN-146)과 **같은 포맷터·같은 한글 컬럼명**을 쓴다.
# 두 탭이 시각을 각자 포맷하거나 다른 한글을 쓰면 같은 사건이 화면마다 다르게 보인다
# (WAN-172/146 공용 규칙). 그래서 새 포맷터를 만들지 않고 기존 것을 가져다 쓴다.
from backtest.report import (
    COL_ENTRY_KST,
    COL_ENTRY_PRICE,
    COL_EXIT_KST,
    COL_EXIT_PRICE,
    COL_EXIT_REASON,
    COL_SIDE,
    format_time_kst,
)
from paper.parity import ParityReport
from paper.performance import PaperPerformance, PerfMetrics
from paper.store import PaperTradeRecord
from strategy.models import OrderBlockDirection, SignalExitReason

# 거래 CSV 컬럼 순서.
_TRADE_COLUMNS = [
    "symbol",
    "timeframe",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "reason",
    "gross_pct",
    "fee_pct",
    "funding_pct",
    "net_pct",
    "risk_pct",
    "r_multiple",
    "stop_price",
    "take_profit_price",
]

# 성과 요약 표 컬럼 순서.
_PERF_COLUMNS = [
    "scope",
    "num_trades",
    "num_wins",
    "num_losses",
    "win_rate",
    "total_return_pct",
    "total_r",
    "avg_r",
    "payoff_ratio",
    "profit_factor",
    "max_drawdown_pct",
]


def records_to_dataframe(records: Sequence[PaperTradeRecord]) -> pd.DataFrame:
    """페이퍼 거래 목록을 DataFrame으로(CSV 내보내기용)."""
    rows = [
        {
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "direction": r.direction.value,
            "entry_time": r.entry_time,
            "entry_price": r.entry_price,
            "exit_time": r.exit_time,
            "exit_price": r.exit_price,
            "reason": r.reason.value,
            "gross_pct": r.gross_pct,
            "fee_pct": r.fee_pct,
            "funding_pct": r.funding_pct,
            "net_pct": r.net_pct,
            "risk_pct": r.risk_pct,
            "r_multiple": r.r_multiple,
            "stop_price": r.stop_price,
            "take_profit_price": r.take_profit_price,
        }
        for r in records
    ]
    return pd.DataFrame(rows, columns=_TRADE_COLUMNS)


def _metrics_row(scope: str, m: PerfMetrics) -> dict[str, object]:
    return {
        "scope": scope,
        "num_trades": m.num_trades,
        "num_wins": m.num_wins,
        "num_losses": m.num_losses,
        "win_rate": m.win_rate,
        "total_return_pct": m.total_return_pct,
        "total_r": m.total_r,
        "avg_r": m.avg_r,
        "payoff_ratio": m.payoff_ratio,
        "profit_factor": m.profit_factor,
        "max_drawdown_pct": m.max_drawdown_pct,
    }


def performance_to_dataframe(perf: PaperPerformance) -> pd.DataFrame:
    """전체 + 시리즈별 성과를 한 DataFrame으로(맨 위 행이 전체)."""
    rows = [_metrics_row("ALL", perf.overall)]
    rows += [_metrics_row(f"{s.symbol} {s.timeframe}", s.metrics) for s in perf.by_series]
    return pd.DataFrame(rows, columns=_PERF_COLUMNS)


# --------------------------------------------------------------------------- #
# 화면 표시용 프레임 (WAN-190) — KST 시각 + 한글 컬럼.
#
# ⚠️ 위의 `records_to_dataframe`/`performance_to_dataframe`/`ParityReport.to_dataframe`
# 는 **CSV·데이터 축이라 UTC(epoch ms) + 영문 컬럼 그대로다**(WAN-172/106 규약). 아래
# 함수들은 화면(`st.dataframe`) 전용이고, 시각 포맷은 "저장된 거래" 탭과 같은
# `format_time_kst` 하나만 쓴다(포맷터 이중화 금지).
# --------------------------------------------------------------------------- #

#: 페이퍼 거래 원장 화면 컬럼(한글). 공통 컬럼명은 `backtest.report`에서 가져와
#: "저장된 거래" 탭과 **글자까지 동일**하게 맞춘다.
COL_SYMBOL = "종목"
COL_TIMEFRAME = "시간대"
COL_GROSS_PCT = "총손익%"
COL_FEE_PCT = "수수료%"
COL_FUNDING_PCT = "펀딩%"
COL_NET_PCT = "순손익%"
COL_RISK_PCT = "리스크%"
COL_R_MULTIPLE = "R배수"
COL_STOP_PRICE = "손절가"
COL_TAKE_PROFIT_PRICE = "익절가"

#: 성과 요약 화면 컬럼(한글).
COL_SCOPE = "구분"
COL_NUM_TRADES = "거래수"
COL_NUM_WINS = "승"
COL_NUM_LOSSES = "패"
COL_WIN_RATE = "승률%"
COL_TOTAL_RETURN = "총수익률%"
COL_TOTAL_R = "총R"
COL_AVG_R = "평균R"
COL_PAYOFF = "손익비"
COL_PROFIT_FACTOR = "PF"
COL_MAX_DD = "MDD%"

#: 패리티 화면 컬럼(한글).
COL_PAPER_TRADES = "페이퍼 거래수"
COL_BACKTEST_TRADES = "백테 거래수"
COL_TRADE_DIFF = "거래수차"
COL_PAPER_WIN_RATE = "페이퍼 승률%"
COL_BACKTEST_WIN_RATE = "백테 승률%"
COL_WIN_RATE_DIFF = "승률차%p"
COL_PAPER_AVG_R = "페이퍼 평균R"
COL_BACKTEST_AVG_R = "백테 평균R"
COL_AVG_R_DIFF = "평균R차"
COL_PAPER_RETURN = "페이퍼 총수익%"
COL_BACKTEST_RETURN = "백테 총수익%"
COL_RETURN_DIFF = "총수익차%p"
COL_FLAGGED = "불일치"

#: `bull`/`bear` → 롱/숏. "저장된 거래" 탭의 `SIDE_LABELS`(롱/숏)와 같은 용어.
_DIRECTION_LABELS: Mapping[OrderBlockDirection, str] = {
    OrderBlockDirection.BULLISH: "롱",
    OrderBlockDirection.BEARISH: "숏",
}

#: 청산 사유 한글 라벨. `backtest.report.EXIT_REASON_LABELS`와 같은 단어(익절/손절)를
#: 쓰되 enum이 다르므로(`SignalExitReason`) 여기서 따로 매핑한다.
_EXIT_REASON_LABELS: Mapping[SignalExitReason, str] = {
    SignalExitReason.TAKE_PROFIT: "익절",
    SignalExitReason.STOP_LOSS: "손절",
}

#: 전체 성과 행의 `scope` 원문 라벨.
_SCOPE_ALL = "ALL"


def records_to_display_frame(records: Sequence[PaperTradeRecord]) -> pd.DataFrame:
    """페이퍼 거래 원장을 **화면용** 표로 (KST 시각 + 한글 컬럼, WAN-190).

    CSV용 `records_to_dataframe`와 달리 시각을 KST 문자열로 바꾸고 컬럼을 한글화한다.
    저장·계산·CSV는 UTC(epoch ms) 그대로다.
    """
    rows = [
        {
            COL_SYMBOL: r.symbol,
            COL_TIMEFRAME: r.timeframe,
            COL_SIDE: _DIRECTION_LABELS.get(r.direction, r.direction.value),
            COL_ENTRY_KST: format_time_kst(r.entry_time),
            COL_ENTRY_PRICE: r.entry_price,
            COL_EXIT_KST: format_time_kst(r.exit_time),
            COL_EXIT_PRICE: r.exit_price,
            COL_EXIT_REASON: _EXIT_REASON_LABELS.get(r.reason, r.reason.value),
            COL_GROSS_PCT: r.gross_pct,
            COL_FEE_PCT: r.fee_pct,
            COL_FUNDING_PCT: r.funding_pct,
            COL_NET_PCT: r.net_pct,
            COL_RISK_PCT: r.risk_pct,
            COL_R_MULTIPLE: r.r_multiple,
            COL_STOP_PRICE: r.stop_price,
            COL_TAKE_PROFIT_PRICE: r.take_profit_price,
        }
        for r in records
    ]
    return pd.DataFrame(rows, columns=_DISPLAY_TRADE_COLUMNS)


_DISPLAY_TRADE_COLUMNS = [
    COL_SYMBOL,
    COL_TIMEFRAME,
    COL_SIDE,
    COL_ENTRY_KST,
    COL_ENTRY_PRICE,
    COL_EXIT_KST,
    COL_EXIT_PRICE,
    COL_EXIT_REASON,
    COL_GROSS_PCT,
    COL_FEE_PCT,
    COL_FUNDING_PCT,
    COL_NET_PCT,
    COL_RISK_PCT,
    COL_R_MULTIPLE,
    COL_STOP_PRICE,
    COL_TAKE_PROFIT_PRICE,
]


def _display_metrics_row(scope: str, m: PerfMetrics) -> dict[str, object]:
    return {
        COL_SCOPE: "전체" if scope == _SCOPE_ALL else scope,
        COL_NUM_TRADES: m.num_trades,
        COL_NUM_WINS: m.num_wins,
        COL_NUM_LOSSES: m.num_losses,
        COL_WIN_RATE: m.win_rate * 100.0,
        COL_TOTAL_RETURN: m.total_return_pct,
        COL_TOTAL_R: m.total_r,
        COL_AVG_R: m.avg_r,
        COL_PAYOFF: m.payoff_ratio,
        COL_PROFIT_FACTOR: m.profit_factor,
        COL_MAX_DD: m.max_drawdown_pct,
    }


def performance_to_display_frame(perf: PaperPerformance) -> pd.DataFrame:
    """전체 + 시리즈별 성과를 **화면용** 표로 (한글 컬럼, WAN-190)."""
    rows = [_display_metrics_row(_SCOPE_ALL, perf.overall)]
    rows += [_display_metrics_row(f"{s.symbol} {s.timeframe}", s.metrics) for s in perf.by_series]
    return pd.DataFrame(rows, columns=_DISPLAY_PERF_COLUMNS)


_DISPLAY_PERF_COLUMNS = [
    COL_SCOPE,
    COL_NUM_TRADES,
    COL_NUM_WINS,
    COL_NUM_LOSSES,
    COL_WIN_RATE,
    COL_TOTAL_RETURN,
    COL_TOTAL_R,
    COL_AVG_R,
    COL_PAYOFF,
    COL_PROFIT_FACTOR,
    COL_MAX_DD,
]


def parity_to_display_frame(report: ParityReport) -> pd.DataFrame:
    """패리티 비교 행을 **화면용** 표로 (한글 컬럼, WAN-190).

    승률·승률차는 분수라 화면에서는 %로 환산한다(CSV `to_dataframe`는 분수 그대로).
    """
    rows = [
        {
            COL_SYMBOL: r.symbol,
            COL_TIMEFRAME: r.timeframe,
            COL_PAPER_TRADES: r.paper.num_trades,
            COL_BACKTEST_TRADES: r.backtest.num_trades,
            COL_TRADE_DIFF: r.trade_count_diff,
            COL_PAPER_WIN_RATE: r.paper.win_rate * 100.0,
            COL_BACKTEST_WIN_RATE: r.backtest.win_rate * 100.0,
            COL_WIN_RATE_DIFF: r.win_rate_diff * 100.0,
            COL_PAPER_AVG_R: r.paper.avg_r,
            COL_BACKTEST_AVG_R: r.backtest.avg_r,
            COL_AVG_R_DIFF: r.avg_r_diff,
            COL_PAPER_RETURN: r.paper.total_return_pct,
            COL_BACKTEST_RETURN: r.backtest.total_return_pct,
            COL_RETURN_DIFF: r.total_return_diff,
            COL_FLAGGED: "⚠" if r.flagged else "",
        }
        for r in report.rows
    ]
    return pd.DataFrame(rows, columns=_DISPLAY_PARITY_COLUMNS)


_DISPLAY_PARITY_COLUMNS = [
    COL_SYMBOL,
    COL_TIMEFRAME,
    COL_PAPER_TRADES,
    COL_BACKTEST_TRADES,
    COL_TRADE_DIFF,
    COL_PAPER_WIN_RATE,
    COL_BACKTEST_WIN_RATE,
    COL_WIN_RATE_DIFF,
    COL_PAPER_AVG_R,
    COL_BACKTEST_AVG_R,
    COL_AVG_R_DIFF,
    COL_PAPER_RETURN,
    COL_BACKTEST_RETURN,
    COL_RETURN_DIFF,
    COL_FLAGGED,
]


def _fmt(value: float | None, *, pct: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%" if pct else f"{value:.2f}"


def _metrics_line(scope: str, m: PerfMetrics) -> str:
    return (
        f"{scope:<20} {m.num_trades:>4} {m.win_rate * 100:>6.1f} "
        f"{m.total_return_pct:>+8.2f} {_fmt(m.total_r):>7} {_fmt(m.avg_r):>6} "
        f"{_fmt(m.payoff_ratio):>6} {_fmt(m.profit_factor):>6} {m.max_drawdown_pct:>6.2f}"
    )


def format_performance(perf: PaperPerformance) -> str:
    """전체·시리즈별 성과를 정렬된 표 문자열로 반환한다."""
    header = (
        f"{'scope':<20} {'trd':>4} {'win%':>6} "
        f"{'ret%':>8} {'totR':>7} {'avgR':>6} {'payf':>6} {'pf':>6} {'mdd%':>6}"
    )
    lines = ["=== Paper Trading Performance ===", header, "-" * len(header)]
    lines.append(_metrics_line("ALL", perf.overall))
    for s in perf.by_series:
        lines.append(_metrics_line(f"{s.symbol} {s.timeframe}", s.metrics))
    return "\n".join(lines)
