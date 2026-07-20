"""backtest.wan145_entry_ablation 테스트 (WAN-145 §2).

이 파일이 지키는 것은 셋이다:

1. **밴드가 고정돼 있지 않다** — WAN-114 사다리는 `tap`에 묶여 있고, 이 모듈은 그 고정을
   **일부러 뺀 것**이 존재 이유다. 누가 `pin_band_bar`를 다시 끼우면 사본이 된다.
2. **「어느 단이 채택 기본값인가」가 코드로 고정된다** — WAN-123 이후 `L2` ≠ 채택 기본값이고
   (게이트가 `first_tap_free`로 고정돼 있다) WAN-132 이후 괴리가 한 겹 더 늘었다. 이 표에서
   채택 기본값은 `L2u`다. 라벨이 틀리면 표 전체가 오독된다.
3. **앞 네 단을 다시 정의하지 않는다** — WAN-114의 `RUNGS`를 그대로 import해야 두 표가
   같은 라벨로 같은 설정을 가리킨다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest import wan114_entry_rule_ablation as wan114
from backtest.harness import LEGACY_BAND_BAR, fill_preset
from backtest.wan145_entry_ablation import (
    ADDS_OVERRIDES,
    ADOPTED_RUNG_NAME,
    BOLLINGER_STEP,
    LADDER,
    RUNGS,
    RUNGS_BY_NAME,
    AblationRow,
    adds_of,
    bollinger_verdict,
    build_summary_markdown,
    describe_engine,
    incremental,
    ladder_table,
    ladder_verdict,
    per_symbol,
    rows_from_csv,
    rows_to_frame,
    rung_params,
    rung_summary,
)
from strategy.models import ConfluenceParams

# ------------------------------------------------------- 1. 밴드를 고정하지 않는다


def test_rungs_follow_the_adopted_band_and_are_not_pinned() -> None:
    """🚨 이 모듈의 존재 이유 — WAN-114는 `tap`에 고정돼 있다."""
    for rung in RUNGS:
        band = rung_params(rung).deviation_filter
        if band is None:
            continue  # 볼린저를 끈 단(L0·L0r·L1)에는 밴드가 없다.
        assert band.band_bar == "intrabar_live"
        assert band.band_bar != LEGACY_BAND_BAR
    assert "band_bar=intrabar_live" in describe_engine()


def test_wan114_ladder_is_still_pinned_to_the_old_band() -> None:
    """옛 표는 손대지 않았다 — `tap` 고정이 살아 있어야 그 CSV가 비트 단위로 재현된다."""
    band = wan114.rung_params(wan114.RUNGS_BY_NAME["L2"], fill=fill_preset("baseline"))
    assert band.deviation_filter is not None
    assert band.deviation_filter.band_bar == LEGACY_BAND_BAR


# ------------------------------------------------------- 2. 어느 단이 채택 기본값인가


def test_adopted_rung_is_exactly_the_default() -> None:
    assert rung_params(RUNGS_BY_NAME[ADOPTED_RUNG_NAME]) == ConfluenceParams()


def test_l2_is_no_longer_the_adopted_default() -> None:
    """WAN-123 이후 `L2` 라벨은 「WAN-122까지의 채택 기본값」이다 — 그 사실을 고정한다."""
    l2 = rung_params(RUNGS_BY_NAME["L2"])
    assert l2 != ConfluenceParams()
    assert l2.rsi_gate_mode == "first_tap_free"
    assert ConfluenceParams().rsi_gate_mode == "unconditional"


def test_ladder_table_marks_the_adopted_rung() -> None:
    table = ladder_table()
    assert ADOPTED_RUNG_NAME in table
    assert "**예**" in table  # 채택 기본값 열이 한 단을 가리킨다.


# ------------------------------------------------------- 3. 앞 네 단은 WAN-114 것


def test_first_four_rungs_are_the_wan114_objects() -> None:
    assert RUNGS[: len(wan114.RUNGS)] == wan114.RUNGS
    assert LADDER == ("L0", "L0r", "L1", "L2", ADOPTED_RUNG_NAME)


def test_bollinger_step_is_the_rung_that_carries_wan114_conclusion() -> None:
    assert BOLLINGER_STEP == ("L1", "L2")


# ------------------------------------------------------- 집계 · 판정


def _row(
    *,
    level: str,
    symbol: str = "BTC/USDT:USDT",
    segment: str = "oos",
    total_return: float = 0.1,
    num_trades: int = 100,
) -> AblationRow:
    return AblationRow(
        level=level,
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        window=0,
        entry_mode="zone_limit",
        take_profit_r=1.5,
        offset_bps=2.0,
        retap_mode="every_tap",
        position_mode="single",
        portfolio_leverage=None,
        fill="baseline",
        seed=0,
        start_time=0,
        end_time=1,
        num_bars=100,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.1,
        sharpe=1.0,
        profit_factor=1.2,
        mean_r=0.2,
        fill_rate=0.8,
        eligible_setups=200,
        num_filled=160,
        funding_coverage=1.0,
    )


def _ladder_rows() -> list[AblationRow]:
    """두 심볼 × 다섯 단. 값은 **CSV 왕복이 정확한 리터럴**로 적는다(부동소수 합 금지)."""
    values = {
        "BTC/USDT:USDT": {
            "L0": -0.05,
            "L0r": -0.08,
            "L1": -0.06,
            "L2": 0.1,
            ADOPTED_RUNG_NAME: 0.14,
        },
        "ETH/USDT:USDT": {"L0": 0.15, "L0r": 0.12, "L1": 0.14, "L2": 0.3, ADOPTED_RUNG_NAME: 0.34},
    }
    return [
        _row(level=level, symbol=symbol, total_return=value)
        for symbol, levels in values.items()
        for level, value in levels.items()
    ]


def test_incremental_pairs_symbols_before_averaging() -> None:
    steps = incremental(per_symbol(_ladder_rows()))
    bollinger = steps[steps["step"] == "L1→L2"].iloc[0]
    assert bollinger["delta_return"] == pytest.approx(0.16)
    assert bollinger["symbols_up"] == 2.0


def test_incremental_keeps_the_ladder_order() -> None:
    steps = incremental(per_symbol(_ladder_rows()))
    assert list(steps["step"]) == ["L0→L0r", "L0r→L1", "L1→L2", f"L2→{ADOPTED_RUNG_NAME}"]


def test_verdicts_read_the_adopted_rung_not_l2() -> None:
    frame = per_symbol(_ladder_rows())
    lines = ladder_verdict(rung_summary(frame), incremental(frame))
    assert any(ADOPTED_RUNG_NAME in line for line in lines)
    assert any("규칙 층 전체 기여" in line for line in lines)


def test_bollinger_verdict_reports_sign_and_size() -> None:
    frame = per_symbol(_ladder_rows())
    lines = bollinger_verdict(incremental(frame))
    assert lines and "+16.00%p" in lines[0]


# ------------------------------------------------------- 왕복 · 렌더


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    rows = _ladder_rows()
    path = tmp_path / "abl.csv"
    rows_to_frame(rows).to_csv(path, index=False)

    assert rows_from_csv(path) == rows


def test_summary_warns_that_l2_is_not_the_adopted_default() -> None:
    summary = build_summary_markdown(_ladder_rows(), csv_path=Path("x.csv"))
    assert "# WAN-145 §2" in summary
    assert "`L2`는 채택 기본값이 아니다" in summary
    assert "ETH 제외" in summary


def test_stale_wan114_label_is_corrected_in_this_table() -> None:
    """WAN-114의 `L2` 라벨("= 채택 기본값")은 WAN-123 이후 거짓이다 — 표에서만 고쳐 쓴다.

    설정은 그대로 물려받아야(두 표가 같은 것을 가리켜야) 대조가 성립하므로, 옛 모듈을
    고치지 않고 **찍히는 문장만** 교정한다.
    """
    assert "채택 기본값" in wan114.RUNGS_BY_NAME["L2"].adds  # 옛 라벨은 손대지 않았다.
    assert adds_of("L2") == ADDS_OVERRIDES["L2"]
    assert "WAN-122까지" in adds_of("L2")
    assert adds_of(ADOPTED_RUNG_NAME) == RUNGS_BY_NAME[ADOPTED_RUNG_NAME].adds
