"""거래 표의 표시 계층 — 숫자 포맷·색·선택 행 → 차트 구간 (WAN-146).

표의 **내용**(어떤 컬럼을 어떤 값으로 채우나)은 `backtest.report.trades_to_display_frame`이
정한다(대시보드와 CSV 내보내기가 같은 함수를 쓴다 — WAN-106). 이 모듈은 그 위에
**보기**만 얹는다: 자릿수, 익절 초록 / 손절 빨강, 그리고 선택된 행이 가리키는 거래의
차트 구간.

Streamlit에 의존하지 않는다 — 순수 pandas/dataclass라 테스트가 화면 없이 돌아간다.
"""

from __future__ import annotations

import pandas as pd
from pandas.io.formats.style import Styler

from backtest.models import BacktestConfig, BacktestResult, ExitReason
from backtest.report import (
    COL_ENTRY_PRICE,
    COL_EQUITY_AFTER,
    COL_EQUITY_BEFORE,
    COL_EXIT_PRICE,
    COL_EXIT_REASON,
    COL_HOLDING_HOURS,
    COL_NOTIONAL,
    COL_NOTIONAL_PCT,
    COL_PNL,
    COL_QUANTITY,
    COL_RETURN_PCT,
    EXIT_REASON_LABELS,
)
from strategy.models import ConfluenceParams, OrderBlockParams

#: 손익 부호 색 — 차트 청산 마커(익절 초록 / 손절 빨강)와 같은 뜻을 같은 색으로 쓴다.
_WIN_COLOR = "#2e7d32"
_LOSS_COLOR = "#c62828"

_NUMBER_FORMATS: dict[str, str] = {
    COL_HOLDING_HOURS: "{:,.1f}",
    COL_ENTRY_PRICE: "{:,.2f}",
    COL_EXIT_PRICE: "{:,.2f}",
    COL_QUANTITY: "{:,.6f}",
    COL_NOTIONAL: "{:,.2f}",
    COL_NOTIONAL_PCT: "{:,.1f}%",
    COL_PNL: "{:+,.2f}",
    COL_RETURN_PCT: "{:+,.2f}%",
    COL_EQUITY_BEFORE: "{:,.2f}",
    COL_EQUITY_AFTER: "{:,.2f}",
}


def _sign_color(value: object) -> str:
    if not isinstance(value, (int, float)) or pd.isna(value):
        return ""
    if value > 0:
        return f"color: {_WIN_COLOR}; font-weight: 600;"
    if value < 0:
        return f"color: {_LOSS_COLOR}; font-weight: 600;"
    return ""


_REASON_COLORS: dict[str, str] = {
    EXIT_REASON_LABELS[ExitReason.TAKE_PROFIT]: _WIN_COLOR,
    EXIT_REASON_LABELS[ExitReason.PARTIAL_TAKE_PROFIT]: _WIN_COLOR,
    EXIT_REASON_LABELS[ExitReason.STOP_LOSS]: _LOSS_COLOR,
}


def _reason_color(value: object) -> str:
    color = _REASON_COLORS.get(str(value))
    return f"color: {color}; font-weight: 600;" if color else ""


def style_trade_frame(frame: pd.DataFrame) -> Styler:
    """자릿수·부호 색을 입힌 Styler를 만든다(익절 초록 / 손절 빨강).

    색을 표에 넣는 이유는 차트 마커에서 텍스트를 걷어냈기 때문이다(WAN-146) —
    "차트는 눈으로 훑고 표는 숫자를 읽는다"에서 표 쪽이 사유를 잃으면 안 된다.
    """
    formats = {col: fmt for col, fmt in _NUMBER_FORMATS.items() if col in frame.columns}
    styler = frame.style.format(formats, na_rep="—")
    sign_cols = [c for c in (COL_PNL, COL_RETURN_PCT) if c in frame.columns]
    if sign_cols:
        styler = styler.map(_sign_color, subset=sign_cols)
    if COL_EXIT_REASON in frame.columns:
        styler = styler.map(_reason_color, subset=[COL_EXIT_REASON])
    return styler


def engine_label_caption(
    result: BacktestResult,
    conf_params: ConfluenceParams,
    ob_params: OrderBlockParams,
    bt_config: BacktestConfig,
) -> str:
    """매 행에서 값이 같던 엔진 라벨 6개를 표 밖 한 줄로 보존한다(WAN-146).

    ⚠️ **삭제가 아니라 이동이다** — WAN-65가 "이 표만 봐도 어떤 설정으로 나온 거래인지
    알 수 있게" 하려고 넣은 값들이라(라벨과 실제가 갈라지는 사고를 여러 번 겪었다) 표
    본문에서 빼되 화면에서 사라지게 두지 않는다. 원본 컬럼 전체는 호출부가
    `trades_to_dataframe`으로 함께 펼쳐 둔다.
    """
    coverage = result.metrics.funding_coverage
    coverage_text = "N/A(off)" if coverage is None else f"{coverage * 100:.1f}%"
    return (
        f"entry_mode={conf_params.entry_mode} · rsi_mode={conf_params.rsi_mode} · "
        f"combine_obs={ob_params.combine_obs} · sizing_mode={bt_config.sizing_mode} · "
        f"risk_per_trade={bt_config.risk_per_trade} · funding_coverage={coverage_text}"
    )


def parse_selected_rows(state: object) -> list[int]:
    """`st.dataframe`의 선택 상태 → 선택된 행 위치 목록. 없으면 빈 목록.

    Streamlit 버전에 따라 선택 상태가 dict로도 객체로도 오므로 둘 다 받아 준다 —
    표시 편의 기능 하나 때문에 대시보드 전체가 예외로 죽으면 안 된다.
    """
    if state is None:
        return []
    selection = (
        state.get("selection") if isinstance(state, dict) else getattr(state, "selection", None)
    )
    if selection is None:
        return []
    rows = (
        selection.get("rows") if isinstance(selection, dict) else getattr(selection, "rows", None)
    )
    if not rows:
        return []
    return [int(r) for r in rows]


def selected_trade_window(result: BacktestResult, rows: list[int]) -> tuple[int, int] | None:
    """선택된 표 행(위치 인덱스) → 그 거래의 (진입 ms, 청산 ms). 선택이 없으면 None.

    표는 `result.trades` 순서 그대로 그려지므로 행 위치가 곧 거래 인덱스다. 범위 밖
    인덱스(표와 결과가 어긋난 재실행 순간)는 조용히 무시한다 — 화면이 깨지는 것보다
    점프를 한 번 건너뛰는 게 낫다.
    """
    if not rows:
        return None
    index = rows[0]
    if index < 0 or index >= len(result.trades):
        return None
    trade = result.trades[index]
    return trade.entry_time, trade.exit_time
