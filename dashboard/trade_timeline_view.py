"""당일 거래별 타임라인 조회의 표시 계층 (WAN-234).

`live.trade_timeline`이 라이브(주문 장부 + 페이퍼 라운드트립)와 백테스트(채택 엔진)를 거래
한 줄로 이어 준다. 이 모듈은 그 행들을 화면 표·선택→차트 점프로 바꾸는 **순수 함수**만
둔다 — Streamlit에 의존하지 않아 화면 없이 테스트된다(`dashboard/saved_trades.py`·
`dashboard/funnel_ledger.py`와 같은 규칙). 표 자체는 `live.trade_timeline`의 공용 표시 프레임
(`timeline_to_display_frame`)을 그대로 쓴다 — 화면·터미널·CSV가 같은 거래를 같은 숫자로
본다(완료 기준 3).
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from live.trade_timeline import (
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    DayTimeline,
    TimelineRow,
    timeline_to_display_frame,
)

#: 선택된 행이 없을 때 차트가 볼 기본 창 폭(체결 시각 앞뒤). 청산 시각이 없거나(미청산)
#: 예약만 있는 줄에 쓴다 — 그 지점 근방을 보여 주면 된다(하위TF 한 시간 남짓).
_DEFAULT_HALF_WINDOW_MS = 6 * 3_600_000


def timeline_frame(timeline: DayTimeline) -> pd.DataFrame:
    """당일 타임라인을 화면 표로 (공용 표시 프레임 재사용)."""
    return timeline_to_display_frame(timeline.rows)


def selected_row(timeline: DayTimeline, positions: Sequence[int]) -> TimelineRow | None:
    """선택된 표 행(위치 인덱스) → 그 `TimelineRow`. 없거나 범위 밖이면 None.

    표는 `DayTimeline.rows` 순서 그대로 그려지므로 행 위치가 곧 행 인덱스다(저장된 거래
    탭과 같은 규약). 범위 밖(표와 데이터가 어긋난 재실행 순간)은 조용히 무시한다.
    """
    if not positions:
        return None
    index = positions[0]
    rows = timeline.rows
    if index < 0 or index >= len(rows):
        return None
    return rows[index]


def chart_window(row: TimelineRow) -> tuple[int, int] | None:
    """선택된 행 → 차트가 볼 (시작 ms, 끝 ms). 기준 시각이 없으면 None.

    체결~청산이 있으면 그 구간을, 청산이 없으면 기준 시각(체결→예약→존확정) 앞뒤 기본
    창을 돌려준다. 저장된 거래 탭의 `selected_trade_window`와 같은 자리를 채운다.
    """
    focus = row.focus_ms
    if focus is None:
        return None
    if row.exit_ms is not None and row.exit_ms > focus:
        return focus, row.exit_ms
    return focus - _DEFAULT_HALF_WINDOW_MS, focus + _DEFAULT_HALF_WINDOW_MS


def backtest_only_note(timeline: DayTimeline) -> str | None:
    """「백테만 있는 줄」이 몇 개인지 한 줄로 — 이 도구의 핵심 신호(WAN-234).

    라이브 진입이 하나도 없는 (심볼, TF) 셀에서 백테스트만 진입한 거래 수를 센다. 0이면
    None(잡음 없는 화면). 셀 단위로 세는 것은 라이브↔백테 거래를 존 단위로 1:1 짝짓지
    않기 때문이다(라이브 체결 축과 백테 탭 축이 달라 짝짓기가 불안정하다 — 대신 「어느
    셀에서 라이브가 통째로 비었나」를 신호로 읽는다).
    """
    live_cells = {
        (r.symbol, r.timeframe)
        for r in timeline.live
        if r.source == SOURCE_LIVE and r.status in ("진입", "청산")
    }
    backtest_only = sum(
        1
        for r in timeline.backtest
        if r.source == SOURCE_BACKTEST and (r.symbol, r.timeframe) not in live_cells
    )
    if backtest_only == 0:
        return None
    return (
        f"🚨 백테스트만 진입한 줄 {backtest_only}건 — 그 셀은 라이브가 통째로 비었다."
        " 상태 열로 어느 단계에서 끊겼는지 본다(탭 없음/예약 없음/미체결/거부·가드)."
    )
