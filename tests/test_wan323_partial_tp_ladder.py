"""WAN-323 반익절 래더 리포트 모듈 테스트.

격자 자체(실데이터·수 시간)는 여기서 돌리지 않는다. 대신 **결론을 바꿀 수 있는 규칙**을
합성으로 고정한다: 14팔의 정의, 교환비 판정이 0 근처 잡음을 「낙폭을 샀다」로 승격시키지
않는 것, 본절 축 분해가 짝을 정확히 잡는 것, 요약이 유효 표본 게이트를 지키는 것.

엔진 쪽(부분 청산·본절 스탑·비트 재현·승률 정의)은 `tests/test_substep.py`와
`tests/test_zone_limit_backtest.py`가 동작으로 고정한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.wan323_partial_tp_ladder import (
    ARMS,
    ARMS_BY_NAME,
    BASELINE_OF,
    EPS,
    MIN_TRADES,
    PARTIAL_FRACTION,
    PRIMARY_OOS,
    LadderRow,
    _verdict,
    breakeven_conversion,
    breakeven_split,
    build_summary,
    leave_one_out,
    partial_reach_rate,
    rows_to_frame,
    sort_rows,
    symbol_mean,
    trade_off,
)

# --------------------------------------------------------------------------- #
# 팔 정의 (사용자 사양 2026-08-18)
# --------------------------------------------------------------------------- #


def test_grid_is_exactly_the_fourteen_specified_arms() -> None:
    """기준선 2 + 분할 6 × 본절 2 = 14팔. 이름·분할 지점·전량 익절 R까지 사양 그대로."""
    assert [arm.name for arm in ARMS] == [
        "A0",
        "A1_be_off",
        "A1_be_on",
        "A2_be_off",
        "A2_be_on",
        "A3_be_off",
        "A3_be_on",
        "B0",
        "B1_be_off",
        "B1_be_on",
        "B2_be_off",
        "B2_be_on",
        "B3_be_off",
        "B3_be_on",
    ]
    assert len(ARMS) == 14
    a_splits = [arm.partial_r for arm in ARMS if arm.family == "A" and not arm.is_baseline]
    b_splits = [arm.partial_r for arm in ARMS if arm.family == "B" and not arm.is_baseline]
    assert sorted(set(a_splits)) == [1.0, 1.2, 1.3]  # type: ignore[type-var]
    assert sorted(set(b_splits)) == [1.0, 1.3, 1.5]  # type: ignore[type-var]
    assert {arm.take_profit_r for arm in ARMS if arm.family == "A"} == {1.5}
    assert {arm.take_profit_r for arm in ARMS if arm.family == "B"} == {2.0}
    # 분할 비율은 절반 고정(비율 스윕은 이 이슈의 범위 밖).
    assert PARTIAL_FRACTION == 0.5


def test_baselines_are_within_family() -> None:
    """판정은 **같은 계열 기준선 대비**로만 낸다(A·B의 절대값을 섞지 않기 위해)."""
    assert BASELINE_OF["A2_be_on"] == "A0"
    assert BASELINE_OF["B3_be_off"] == "B0"
    assert ARMS_BY_NAME["A0"].is_baseline and ARMS_BY_NAME["B0"].is_baseline
    assert not ARMS_BY_NAME["A1_be_off"].is_baseline


def test_baseline_arm_carries_the_adopted_take_profit() -> None:
    """`A0`는 곧 현행 채택 엔진이다 — 전량 익절 1.5R · 래더 끔 · 본절 끔."""
    a0 = ARMS_BY_NAME["A0"]
    assert (a0.take_profit_r, a0.partial_r, a0.breakeven) == (1.5, None, False)


# --------------------------------------------------------------------------- #
# 교환비 판정
# --------------------------------------------------------------------------- #


def test_tiny_mdd_delta_is_not_promoted_to_a_purchase() -> None:
    """0 근처 잡음을 「낙폭을 샀다」로 읽지 않는다 — 교환비 분모 폭주의 원인이었다."""
    assert _verdict("A", -0.02, -EPS / 10) == "낙폭은 그대로인데 기대값만 깎였다"
    assert _verdict("A", -0.02, -0.01) == "낙폭을 샀다"


def test_free_lunch_in_family_a_is_flagged_as_a_bug_suspicion() -> None:
    """A0는 이미 OOS 최적 배수(WAN-90)라 수익까지 이기면 설명이 없다 — 버그부터 의심."""
    assert "배선 버그" in _verdict("A", +0.02, -0.01)
    # B0(2.0R)는 열등한 기준선이라 같은 모양이 예상된 결과다.
    assert "배선 버그" not in _verdict("B", +0.02, -0.01)
    assert "B0이 열등한 기준선" in _verdict("B", +0.02, -0.01)


def test_verdict_handles_missing_samples() -> None:
    assert _verdict("A", None, -0.01) == "판정 불가"
    assert _verdict("A", -0.01, None) == "판정 불가"


# --------------------------------------------------------------------------- #
# 집계 — 합성 프레임
# --------------------------------------------------------------------------- #


def _row(
    *,
    symbol: str,
    arm: str,
    total_return: float,
    max_drawdown: float,
    num_trades: int = 40,
    win_rate: float = 0.5,
    n_partial: int = 0,
    n_partial_then_stop: int = 0,
    segment: str = PRIMARY_OOS,
    timeframe: str = "1h",
) -> LadderRow:
    spec = ARMS_BY_NAME[arm]
    return LadderRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        arm=arm,
        family=spec.family,
        take_profit_r=spec.take_profit_r,
        partial_r=spec.partial_r,
        breakeven=spec.breakeven,
        eligible=100,
        filled=80,
        num_trades=num_trades,
        total_return=total_return,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        sharpe=None,
        mean_net_r=0.1,
        mean_gross_r=0.2,
        cost_total=1.0,
        n_take_profit=num_trades,
        n_stop_loss=0,
        n_end_of_data=0,
        n_partial=n_partial,
        n_partial_then_stop=n_partial_then_stop,
        funding_rows=10,
    )


def _frame() -> pd.DataFrame:
    rows = [
        _row(symbol="BTC/USDT:USDT", arm="A0", total_return=0.20, max_drawdown=0.10),
        _row(symbol="ETH/USDT:USDT", arm="A0", total_return=0.10, max_drawdown=0.20),
        _row(
            symbol="BTC/USDT:USDT",
            arm="A1_be_off",
            total_return=0.16,
            max_drawdown=0.08,
            n_partial=20,
            n_partial_then_stop=4,
        ),
        _row(
            symbol="ETH/USDT:USDT",
            arm="A1_be_off",
            total_return=0.08,
            max_drawdown=0.16,
            n_partial=20,
            n_partial_then_stop=6,
        ),
        _row(
            symbol="BTC/USDT:USDT",
            arm="A1_be_on",
            total_return=0.14,
            max_drawdown=0.06,
            win_rate=0.6,
            n_partial=20,
            n_partial_then_stop=10,
        ),
        _row(
            symbol="ETH/USDT:USDT",
            arm="A1_be_on",
            total_return=0.06,
            max_drawdown=0.12,
            win_rate=0.6,
            n_partial=20,
            n_partial_then_stop=10,
        ),
    ]
    return rows_to_frame(rows)


def test_trade_off_is_the_symbol_mean_delta_against_the_family_baseline() -> None:
    off = trade_off(_frame(), "1h", PRIMARY_OOS, "A1_be_off")
    assert off.baseline == "A0"
    assert off.d_return == pytest.approx(0.12 - 0.15)  # 기대값을 깎았다
    assert off.d_mdd == pytest.approx(0.12 - 0.15)  # 낙폭도 줄었다
    assert off.ratio == pytest.approx(1.0)  # MDD 1%p를 수익 1%p로 샀다
    assert off.verdict == "낙폭을 샀다"
    assert "기대값 -3.00%p 대신 MDD -3.00%p" in off.text


def test_breakeven_split_pairs_the_twin_arm() -> None:
    """본절 축 분해 = (분할+본절) − (분할만). 짝이 아닌 팔에는 값이 없다."""
    frame = _frame()
    assert breakeven_split(frame, "1h", PRIMARY_OOS, "A1_be_on") == pytest.approx(0.09 - 0.12)
    assert breakeven_split(frame, "1h", PRIMARY_OOS, "A1_be_off") is None
    assert breakeven_split(frame, "1h", PRIMARY_OOS, "A0") is None


def test_partial_reach_and_breakeven_conversion() -> None:
    frame = _frame()
    assert partial_reach_rate(frame, "1h", PRIMARY_OOS, "A1_be_off") == pytest.approx(40 / 80)
    assert partial_reach_rate(frame, "1h", PRIMARY_OOS, "A0") == 0.0
    assert breakeven_conversion(frame, "1h", PRIMARY_OOS, "A1_be_on") == pytest.approx(20 / 40)
    # 기준선은 부분 익절이 없으므로 전환율이 정의되지 않는다(0이 아니라 None).
    assert breakeven_conversion(frame, "1h", PRIMARY_OOS, "A0") is None


def test_sample_gate_excludes_thin_cells() -> None:
    """심볼당 `MIN_TRADES` 미만 셀은 심볼평균에서 빠진다(WAN-84 유효 기준)."""
    thin = _row(
        symbol="TRX/USDT:USDT",
        arm="A0",
        total_return=5.0,
        max_drawdown=0.9,
        num_trades=MIN_TRADES - 1,
    )
    frame = rows_to_frame([*[LadderRow(**r) for r in _frame().to_dict("records")], thin])
    # 얇은 셀의 +500%가 섞이면 평균이 폭주한다 — 게이트가 그것을 막는다.
    assert symbol_mean(frame, "1h", PRIMARY_OOS, "A0", "total_return") == pytest.approx(0.15)


def test_leave_one_out_drops_one_symbol_at_a_time() -> None:
    loo = dict(leave_one_out(_frame(), "1h", PRIMARY_OOS, "A0"))
    assert loo == {"BTC": pytest.approx(0.10), "ETH": pytest.approx(0.20)}


def test_sort_rows_is_deterministic_regardless_of_completion_order() -> None:
    """병렬 완료 순서가 CSV 순서를 흔들지 않는다(`--jobs`는 성능 노브일 뿐)."""
    rows = [
        _row(symbol="ETH/USDT:USDT", arm="B0", total_return=0.1, max_drawdown=0.1),
        _row(symbol="BTC/USDT:USDT", arm="A1_be_on", total_return=0.1, max_drawdown=0.1),
        _row(symbol="BTC/USDT:USDT", arm="A0", total_return=0.1, max_drawdown=0.1),
    ]
    assert [(r.symbol, r.arm) for r in sort_rows(rows)] == [
        ("BTC/USDT:USDT", "A0"),
        ("BTC/USDT:USDT", "A1_be_on"),
        ("ETH/USDT:USDT", "B0"),
    ]
    assert sort_rows(rows) == sort_rows(list(reversed(rows)))


def test_summary_renders_the_trade_off_sentences() -> None:
    """완료기준 3 — 팔마다 「기대값 −a%p 대신 MDD −b%p」 문장이 요약에 실린다."""
    text = build_summary(_frame())
    assert "## 판정 문장 — 팔마다 「기대값 −a%p 대신 MDD −b%p」" in text
    assert "A1_be_off: 기대값 -3.00%p 대신 MDD -3.00%p" in text
    # 완료기준 4 — 본절 축 분해가 열로 나온다.
    assert "## 본절 스탑 축 분해" in text
    assert "`A1_be_on`" in text
    # 완료기준 5 — 승률 정의가 문서에 명시된다.
    assert "순손익 > 0이면 승리" in text
    # 기본값 전환이 아니라는 것과 렌즈 한계가 늘 함께 실린다.
    assert "재-베이스라인 = 사용자 결정" in text
    assert "체결 보수화(`pen_5bp`) 미측정" in text
