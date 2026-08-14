"""저장된 거래 조회의 표시 계층 (WAN-106).

사용자의 원 요청은 **"어디서 손절났는지 보고싶다"** 였다. 그래서 이 층이 지켜야 하는
성질은 둘이다: 청산사유 필터가 실제로 동작할 것, 그리고 **필터가 걸려도 행 클릭이 같은
거래를 가리킬 것**(위치 인덱스를 그대로 쓰면 3번째 손절이 3번째 거래가 돼 버린다).
"""

from __future__ import annotations

import pandas as pd

from backtest.report import COL_EXIT_REASON, COL_NO, trades_to_display_frame
from backtest.trade_store import RunFingerprint, RunSummary
from dashboard.live_board import REASON_FILTER_ALL, REASON_FILTER_OPTIONS
from dashboard.saved_trades import (
    ALL_REASONS,
    exit_reason_options,
    filter_by_exit_reason,
    filter_by_reason_chip,
    reason_chip_labels,
    run_label,
    selected_trade_no,
    setups_display_frame,
    zone_limit_runs,
)
from dashboard.trade_table import selected_trade_window
from strategy.models import ConfluenceParams, OrderBlockParams
from tests.test_trade_display_frame import _win_then_loss


def _fingerprint() -> RunFingerprint:
    return RunFingerprint(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        segment="oos",
        entry_mode="zone_limit",
        fill="baseline",
        confluence_json=ConfluenceParams().model_dump_json(),
        order_block_json=OrderBlockParams().model_dump_json(),
        config_json='{"initial_capital": 10000.0}',
        revision="abc1234",
    )


def test_exit_reason_filter_uses_the_labels_the_table_actually_shows() -> None:
    """필터 선택지와 표의 값이 두 벌로 갈라지면 "손절"을 골라도 빈 표가 뜬다."""
    frame = trades_to_display_frame(_win_then_loss())

    assert set(frame[COL_EXIT_REASON]) <= set(exit_reason_options())

    losses = filter_by_exit_reason(frame, "손절", column=COL_EXIT_REASON)
    wins = filter_by_exit_reason(frame, "익절", column=COL_EXIT_REASON)

    assert len(losses) == 1
    assert len(wins) == 1
    assert len(filter_by_exit_reason(frame, ALL_REASONS, column=COL_EXIT_REASON)) == 2


def test_reason_chips_share_the_wallet_tab_vocabulary_and_really_filter() -> None:
    """WAN-289 병합 화면: 청산사유 칩은 잔고 탭과 **같은 어휘**(전체/익절만/손절만)이고
    그 어휘로 백테스트 표가 실제로 좁혀진다 — 낱말이 갈라지면 같은 질문에 두 UI가 생긴다.
    """
    frame = trades_to_display_frame(_win_then_loss())

    for choice in REASON_FILTER_OPTIONS:
        filtered = filter_by_reason_chip(frame, choice, column=COL_EXIT_REASON)
        if choice == REASON_FILTER_ALL:
            assert len(filtered) == 2
        else:
            assert len(filtered) == 1

    labels = reason_chip_labels("익절만")
    assert labels is not None and "익절" in labels and "부분익절" in labels
    assert reason_chip_labels(REASON_FILTER_ALL) is None
    # 모르는 선택은 「전체」로 접는다 — 빈 화면은 고장으로 읽힌다.
    assert len(filter_by_reason_chip(frame, "이상한값", column=COL_EXIT_REASON)) == 2


def test_filtered_row_still_points_at_the_original_trade() -> None:
    """손절만 걸러 본 표의 첫 행은 전체에서 **두 번째** 거래다 — 차트도 거기로 가야 한다."""
    result = _win_then_loss()
    frame = trades_to_display_frame(result)
    losses = filter_by_exit_reason(frame, "손절", column=COL_EXIT_REASON)

    trade_no = selected_trade_no(losses, [0])

    assert trade_no == 2
    assert trade_no is not None
    window = selected_trade_window(result, [trade_no - 1])
    assert window == (result.trades[1].entry_time, result.trades[1].exit_time)


def test_no_selection_means_no_jump() -> None:
    frame = trades_to_display_frame(_win_then_loss())

    assert selected_trade_no(frame, []) is None
    assert selected_trade_no(frame, [99]) is None
    assert selected_trade_no(pd.DataFrame(columns=[COL_NO]), [0]) is None


def _summary(
    *, entry_mode: str, symbol: str = "BTC/USDT:USDT", timeframe: str = "1h", run_id: str = "0" * 32
) -> RunSummary:
    fp = RunFingerprint(
        symbol=symbol,
        timeframe=timeframe,
        entry_mode=entry_mode,
        fill="baseline",
        confluence_json=ConfluenceParams().model_dump_json(),
        order_block_json=OrderBlockParams().model_dump_json(),
        config_json='{"initial_capital": 10000.0}',
        revision="abc1234",
    )
    return RunSummary(
        run_id=run_id,
        fingerprint=fp,
        created_at=0,
        num_trades=1,
        total_return=0.0,
        max_drawdown=0.0,
        win_rate=0.0,
        final_equity=10_000.0,
        fill_rate=None,
        eligible_setups=None,
        num_filled=None,
    )


def test_zone_limit_runs_keeps_only_matching_b_plan_runs() -> None:
    """분석 탭 조회 필터(WAN-199): 이 (심볼·TF)의 B안(존-지정가) 실행만, 입력 순서 유지.

    종가(A안) 실행은 사용자의 실매매가 아니라 제외하고, 다른 심볼·TF는 섞이지 않는다 —
    판별은 라벨이 아니라 지문(`entry_mode`)으로 한다.
    """
    close_run = _summary(entry_mode="close", run_id="a" * 32)
    b_match = _summary(entry_mode="zone_limit", run_id="b" * 32)
    b_other_tf = _summary(entry_mode="zone_limit", timeframe="15m", run_id="c" * 32)
    b_other_symbol = _summary(entry_mode="zone_limit", symbol="ETH/USDT:USDT", run_id="d" * 32)

    picked = zone_limit_runs(
        [close_run, b_match, b_other_tf, b_other_symbol], symbol="BTC/USDT:USDT", timeframe="1h"
    )

    assert [s.run_id for s in picked] == ["b" * 32]
    assert zone_limit_runs([close_run], symbol="BTC/USDT:USDT", timeframe="1h") == []


def test_run_label_keeps_the_engine_visible() -> None:
    """어느 엔진의 거래인지가 화면에서 사라지면 분석 탭의 A안 배지가 하던 역할이 끊긴다."""
    summary = RunSummary(
        run_id="a" * 32,
        fingerprint=_fingerprint(),
        created_at=0,
        num_trades=12,
        total_return=0.0834,
        max_drawdown=0.1,
        win_rate=0.5,
        final_equity=10_834.0,
        fill_rate=0.81,
        eligible_setups=15,
        num_filled=12,
    )

    label = run_label(summary)

    assert "B안(존-지정가)" in label
    assert "15m" in label
    assert "abc1234" in label
    assert "거래 12건" in label
    assert "+8.34%" in label


def test_setup_table_is_readable_and_keeps_unknown_status_verbatim() -> None:
    frame = pd.DataFrame(
        {
            "setup_no": [1, 2],
            "trigger_time": [0, 0],
            "tap_bar_time": [0, 0],
            "side": ["long", "short"],
            "tap_close": [100.0, 200.0],
            "limit_price": [None, 199.0],
            "stop_price": [95.0, 205.0],
            "filled": [False, False],
            "dropped": [False, False],
            "status": ["no_touch", "brand_new_status"],
            "tap_index": [0, 1],
        }
    )

    out = setups_display_frame(frame, to_kst=lambda ms: "2026-07-20 09:00")

    assert list(out["방향"]) == ["롱", "숏"]
    assert out["상태"].iloc[0] == "가격이 안 옴"
    # 모르는 상태를 조용히 빈칸으로 만들지 않는다 — 원문이 보여야 고칠 수 있다.
    assert out["상태"].iloc[1] == "brand_new_status"
    assert out["탭시각(KST)"].iloc[0] == "2026-07-20 09:00"


def test_setup_table_keeps_its_skeleton_when_empty() -> None:
    out = setups_display_frame(pd.DataFrame())

    assert out.empty
    assert "상태" in out.columns
