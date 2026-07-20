"""저장된 거래 조회의 표시 계층 (WAN-106).

대시보드의 **분석 탭**은 화면을 열 때마다 오더블록 탐지와 백테스트를 다시 돌린다(그래서
1분봉을 못 읽어 A안 종가 진입으로 내려가 있다 — 사용자의 실매매가 아니다). 이 모듈이
받치는 **저장된 거래 탭**은 정반대다: `backtest.run --persist`가 한 번 계산해 DB에 넣어
둔 **채택 엔진(B안 지정가)** 의 거래를 **계산 없이 조회**만 한다.

Streamlit에 의존하지 않는다 — 필터·라벨·표 변환이 전부 순수 함수라 화면 없이 테스트된다
(`dashboard/trade_table.py`와 같은 규칙).
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from backtest.report import COL_NO, EXIT_REASON_LABELS, format_time_kst
from backtest.trade_store import RunSummary

#: 청산사유 필터의 "전체" 토큰. 사용자의 원 요청("어디서 손절났는지 보고싶다")이 곧
#: 이 필터라, 라벨은 표에 실제로 찍히는 한글 사유(`EXIT_REASON_LABELS`)와 **같은 값**을
#: 쓴다 — 두 벌로 갈라지면 "손절"을 골랐는데 아무것도 안 나오는 화면이 된다.
ALL_REASONS = "전체"


def exit_reason_options() -> tuple[str, ...]:
    """청산사유 필터의 선택지 (`전체` + 표에 찍히는 한글 사유들)."""
    return (ALL_REASONS, *EXIT_REASON_LABELS.values())


def filter_by_exit_reason(frame: pd.DataFrame, reason: str, *, column: str) -> pd.DataFrame:
    """청산사유로 거래 표를 좁힌다.

    ⚠️ **시드(전)/시드(후)는 좁혀도 전체 실행 기준 그대로다** — 손절만 뽑았다고 해서
    "손절만 했을 때의 시드 흐름"이 되지는 않는다(그건 다른 백테스트다). 행 번호(`#`)를
    남겨 두는 것도 같은 이유다: 필터된 표에서도 그 거래가 전체에서 몇 번째였는지 보인다.
    """
    if reason == ALL_REASONS:
        return frame
    return frame[frame[column] == reason].reset_index(drop=True)


def selected_trade_no(frame: pd.DataFrame, rows: list[int]) -> int | None:
    """선택된 표 행(위치) → 그 거래의 전체 실행 기준 번호(`#`). 선택이 없으면 None.

    위치 인덱스를 그대로 거래 인덱스로 쓰지 않는 이유는 **필터 때문**이다 — 손절만
    걸러 본 표의 3번째 행은 전체에서 3번째 거래가 아니다. `#` 열을 거치면 필터가
    걸려 있든 없든 같은 거래를 가리킨다.
    """
    if not rows or frame.empty:
        return None
    index = rows[0]
    if index < 0 or index >= len(frame):
        return None
    return int(frame.iloc[index][COL_NO])


def run_label(summary: RunSummary) -> str:
    """실행 선택 드롭다운에 찍을 한 줄.

    지문 라벨(`RunFingerprint.label`)을 그대로 앞세운다 — **지금 보고 있는 게 어느
    엔진의 거래인지**가 화면에서 사라지면, 분석 탭의 "A안(봉 마감 종가)" 배지가 하던
    역할이 끊긴다(WAN-65/95).
    """
    return (
        f"{summary.fingerprint.label()} · 거래 {summary.num_trades}건 · "
        f"수익 {summary.total_return * 100:+.2f}%"
    )


SETUP_COLUMN_LABELS: dict[str, str] = {
    "setup_no": "#",
    "trigger_time": "탭시각(KST)",
    "side": "방향",
    "tap_close": "탭 봉 종가",
    "limit_price": "지정가",
    "stop_price": "손절가",
    "status": "상태",
    "tap_index": "탭 순번",
}

#: 미체결 사유(`ZoneLimitStatus`) 한글 라벨. 원문(`no_touch`)은 화면에 그대로 나가면
#: 안 되고, 모르는 값은 원문을 남긴다(조용히 빈칸으로 만들지 않는다).
SETUP_STATUS_LABELS: dict[str, str] = {
    "no_touch": "가격이 안 옴",
    "cancelled_expired": "유효기간 만료",
    "cancelled_invalidated": "존 무효화",
    "cancelled_condition_failed": "조건 미충족",
    "filled_open": "체결(보유 중)",
    "filled_exited": "체결(청산 완료)",
}


def setups_display_frame(
    frame: pd.DataFrame, *, to_kst: Callable[[int], str] | None = None
) -> pd.DataFrame:
    """미체결 셋업 표를 사람이 읽는 형태로 (WAN-106).

    "살 뻔했는데 못 산 자리"는 규칙을 판단하는 데 체결된 거래만큼 중요하다 — 지금
    화면에는 아예 존재하지 않던 정보다. `to_kst`는 시각 포맷터(테스트·호출부 주입용)이며
    주지 않으면 `backtest.report`의 KST 포맷을 쓴다(표시 포맷은 한 곳에서만 정의한다).
    """
    fmt = to_kst or format_time_kst
    if frame.empty:
        return pd.DataFrame(columns=list(SETUP_COLUMN_LABELS.values()))
    out = pd.DataFrame(
        {
            SETUP_COLUMN_LABELS["setup_no"]: frame["setup_no"],
            SETUP_COLUMN_LABELS["trigger_time"]: [fmt(int(t)) for t in frame["trigger_time"]],
            SETUP_COLUMN_LABELS["side"]: ["롱" if s == "long" else "숏" for s in frame["side"]],
            SETUP_COLUMN_LABELS["tap_close"]: frame["tap_close"],
            SETUP_COLUMN_LABELS["limit_price"]: frame["limit_price"],
            SETUP_COLUMN_LABELS["stop_price"]: frame["stop_price"],
            SETUP_COLUMN_LABELS["status"]: [
                SETUP_STATUS_LABELS.get(str(s), str(s)) for s in frame["status"]
            ],
            SETUP_COLUMN_LABELS["tap_index"]: frame["tap_index"],
        }
    )
    return out
