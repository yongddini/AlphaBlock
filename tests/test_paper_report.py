"""paper.report 화면 표시 프레임 테스트 (WAN-190).

페이퍼 성과 탭 화면 표가 KST 시각 + 한글 컬럼으로 나오는지, 그리고 CSV·데이터 축은
UTC(epoch ms) + 영문 컬럼 그대로인지(회귀)를 고정한다. 포맷터는 "저장된 거래" 탭
(WAN-146)과 **같은 함수 하나**를 쓴다(이중화 금지).
"""

from __future__ import annotations

import pytest

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
        "slippage_pct",
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


def _reconcilable_record() -> PaperTradeRecord:
    """비용 항등식(net = gross − fee − slippage − funding)과 R 정의를 지키는 손절 거래.

    사용자가 본 BTC 1h 손절 거래를 본떠, 좁은 손절(진입 대비 ~0.35%)에서 비용이 R로
    크게 잡히는 상황을 재현한다: 가격은 정확히 -1R(손절선 체결)인데 비용까지 더하면 -1.5R.
    """
    entry, stop = 63_985.89, 63_760.5
    risk_pct = abs(entry - stop) / entry * 100.0  # ≈ 0.35226
    gross_pct = -risk_pct  # 손절선에 정확히 체결 → 가격만 보면 -1R
    fee_pct, slippage_pct, funding_pct = 0.08, 0.09, 0.01
    net_pct = gross_pct - fee_pct - slippage_pct - funding_pct
    return PaperTradeRecord(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_time=_ENTRY_MS,
        entry_price=entry,
        exit_time=_EXIT_MS,
        exit_price=stop,
        reason=SignalExitReason.STOP_LOSS,
        gross_pct=gross_pct,
        fee_pct=fee_pct,
        slippage_pct=slippage_pct,
        funding_pct=funding_pct,
        net_pct=net_pct,
        risk_pct=risk_pct,
        r_multiple=net_pct / risk_pct,
        stop_price=stop,
    )


def test_display_frame_has_slippage_and_gross_is_pre_cost() -> None:
    """WAN-212: 슬리피지 열이 화면에 있고 gross 라벨이 비용 전임을 명시한다.

    슬리피지가 빠져 있으면 `가격손익% − 수수료% − 펀딩%`이 `순손익%`와 안 맞아
    "비용이 안 맞아" 보인다(사용자 발견). 그리고 gross 라벨이 "총손익%"이면 net으로
    오해된다.
    """
    df = records_to_display_frame([_reconcilable_record()])
    assert "슬리피지%" in df.columns
    # gross 라벨은 net과 헷갈리지 않게 비용 전임을 명시한다("총손익%" 단독 금지).
    assert "가격손익%(비용전)" in df.columns
    assert "총손익%" not in df.columns


def test_display_frame_cost_decomposition_reconciles() -> None:
    """WAN-212: 한 화면에서 gross − fee − slippage − funding = net 이 재구성된다."""
    df = records_to_display_frame([_reconcilable_record()])
    row = df.iloc[0]
    reconstructed = row["가격손익%(비용전)"] - row["수수료%"] - row["슬리피지%"] - row["펀딩%"]
    assert reconstructed == pytest.approx(row["순손익%"])
    # R배수는 net 기준(net_pct / risk_pct) — 원장 한 줄에서 재계산과 일치.
    assert row["R배수"] == pytest.approx(row["순손익%"] / row["리스크%"])


def test_ledger_csv_summary_share_the_same_net_r() -> None:
    """WAN-212: 원장(화면)·CSV·요약이 같은 거래에 같은 net R을 쓴다(WAN-146/207 취지).

    사용자가 본 모순(-0.35% vs -1.5R)의 근원은 두 화면이 비용 전/후를 엇갈려 적은
    것이었다. 세 경로가 같은 net R을 쓰는지 동작으로 고정한다.
    """
    record = _reconcilable_record()
    disp = records_to_display_frame([record]).iloc[0]
    csv = records_to_dataframe([record]).iloc[0]
    perf = build_performance([record])

    # 세 경로가 같은 net R.
    assert disp["R배수"] == pytest.approx(record.r_multiple)
    assert csv["r_multiple"] == pytest.approx(record.r_multiple)
    assert perf.overall.total_r == pytest.approx(record.r_multiple)
    # 비용까지 반영하면 -1R(가격)이 아니라 ~-1.5R(비용 포함)임이 드러난다.
    assert record.r_multiple is not None
    assert record.r_multiple < -1.0
    # CSV도 슬리피지를 실어 net이 재구성된다.
    assert csv["net_pct"] == pytest.approx(
        csv["gross_pct"] - csv["fee_pct"] - csv["slippage_pct"] - csv["funding_pct"]
    )


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
