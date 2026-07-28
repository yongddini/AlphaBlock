"""paper.report 화면 표시 프레임 테스트 (WAN-190).

페이퍼 성과 탭 화면 표가 KST 시각 + 한글 컬럼으로 나오는지, 그리고 CSV·데이터 축은
UTC(epoch ms) + 영문 컬럼 그대로인지(회귀)를 고정한다. 포맷터는 "저장된 거래" 탭
(WAN-146)과 **같은 함수 하나**를 쓴다(이중화 금지).
"""

from __future__ import annotations

from backtest.report import (
    COL_ENTRY_KST,
    COL_EXIT_KST,
    COL_SIDE,
    format_time_kst,
)
from paper.performance import build_performance
from paper.report import (
    performance_to_dataframe,
    performance_to_display_frame,
    records_to_dataframe,
    records_to_display_frame,
)
from paper.store import PaperTradeRecord
from strategy.models import OrderBlockDirection, SignalExitReason

# 2024-06-01 12:00:00 UTC = 2024-06-01 21:00 KST — KST 변환이 실제로 일어나는지 확인용.
_ENTRY_MS = 1_717_243_200_000
_EXIT_MS = _ENTRY_MS + 3_600_000


def _record(
    *,
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    reason: SignalExitReason = SignalExitReason.TAKE_PROFIT,
    net_pct: float = 1.5,
    r: float | None = 1.5,
) -> PaperTradeRecord:
    return PaperTradeRecord(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=direction,
        entry_time=_ENTRY_MS,
        entry_price=100.0,
        exit_time=_EXIT_MS,
        exit_price=100.0 + net_pct,
        reason=reason,
        gross_pct=net_pct,
        fee_pct=0.1,
        funding_pct=0.0,
        net_pct=net_pct,
        risk_pct=None if r is None else abs(net_pct / r),
        r_multiple=r,
        stop_price=99.0,
        take_profit_price=101.5,
    )


def test_display_frame_uses_korean_columns_and_kst_time() -> None:
    df = records_to_display_frame([_record()])

    # 한글 컬럼(공용 상수와 글자까지 동일).
    assert COL_SIDE in df.columns
    assert COL_ENTRY_KST in df.columns
    assert "종목" in df.columns
    assert "순손익%" in df.columns
    # 영문 원본 컬럼은 화면 프레임에 없다.
    assert "symbol" not in df.columns
    assert "net_pct" not in df.columns
    assert "entry_time" not in df.columns

    row = df.iloc[0]
    # 시각은 "저장된 거래" 탭과 같은 포맷터 하나로 찍는다(이중화 금지).
    assert row[COL_ENTRY_KST] == format_time_kst(_ENTRY_MS)
    assert row[COL_EXIT_KST] == format_time_kst(_EXIT_MS)
    # 에폭 밀리초 원본이 화면에 새지 않는다.
    assert str(_ENTRY_MS) not in row[COL_ENTRY_KST]
    # 방향·사유 한글화.
    assert row[COL_SIDE] == "롱"
    assert row["청산사유"] == "익절"


def test_display_frame_direction_and_reason_labels() -> None:
    df = records_to_display_frame(
        [_record(direction=OrderBlockDirection.BEARISH, reason=SignalExitReason.STOP_LOSS)]
    )
    row = df.iloc[0]
    assert row[COL_SIDE] == "숏"
    assert row["청산사유"] == "손절"


def test_csv_frame_unchanged_utc_and_english() -> None:
    """회귀: CSV·데이터 축은 영문 컬럼 + epoch ms 원본 그대로."""
    df = records_to_dataframe([_record()])
    assert list(df.columns) == [
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
        "notional",
        "risk_amount",
        "realized_pnl",
    ]
    # 시각은 변환 없이 epoch ms 원본(저장·계산 축은 UTC 불변).
    assert df.iloc[0]["entry_time"] == _ENTRY_MS
    assert df.iloc[0]["direction"] == "bull"


def test_performance_display_frame_korean_and_scope_all() -> None:
    perf = build_performance([_record(net_pct=2.0, r=2.0), _record(net_pct=-1.0, r=-1.0)])
    disp = performance_to_display_frame(perf)

    assert "구분" in disp.columns
    assert "승률%" in disp.columns
    assert "scope" not in disp.columns
    # 전체 행의 scope는 한글 "전체".
    assert disp.iloc[0]["구분"] == "전체"
    # 승률은 화면에서 %로 환산(분수 아님).
    assert disp.iloc[0]["승률%"] == perf.overall.win_rate * 100.0

    # CSV 프레임(회귀)은 영문 + 분수 그대로.
    csv = performance_to_dataframe(perf)
    assert "scope" in csv.columns
    assert csv.iloc[0]["scope"] == "ALL"
    assert csv.iloc[0]["win_rate"] == perf.overall.win_rate
