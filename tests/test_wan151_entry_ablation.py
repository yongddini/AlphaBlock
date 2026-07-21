"""backtest.wan151_entry_ablation 테스트 (WAN-151 §2).

이 파일이 지키는 것은 넷이다:

1. **존 정의가 병합에 고정돼 있지 않다** — WAN-114/145 사다리는 `LEGACY_OB_PARAMS`에
   묶여 있고, 이 모듈은 그 고정을 **일부러 뺀 것**이 존재 이유다.
2. **「어느 단이 채택 기본값인가」가 코드로 고정된다** — WAN-123 이후 `L2` ≠ 채택 기본값이다.
3. **사다리를 다시 정의하지 않는다** — WAN-145의 `RUNGS`(= WAN-114 네 단 + `L2u`)를 그대로
   import해야 세 표가 같은 라벨로 같은 설정을 가리킨다.
4. **검산이 동작한다** — `L2u` 단이 §1 널의 실제 팔과 어긋나면 요약이 그 사실을 말해야 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest import wan145_entry_ablation as wan145
from backtest.harness import LEGACY_COMBINE_OBS, LEGACY_OB_PARAMS
from backtest.wan151_entry_ablation import (
    ADOPTED_RUNG_NAME,
    BOLLINGER_STEP,
    FLOAT_NOISE,
    LADDER,
    RUNGS,
    RUNGS_BY_NAME,
    AblationRow,
    bollinger_verdict,
    build_summary_markdown,
    crosscheck_against_null,
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
from backtest.wan151_split_zone_null import ADOPTED_OB_PARAMS, LONG_ARM, OFFICIAL_LENS, NullRow
from backtest.wan151_split_zone_null import rows_to_frame as null_rows_to_frame
from strategy.models import ConfluenceParams, OrderBlockParams

# ------------------------------------------------- 1. 존을 병합에 고정하지 않는다


def test_detection_params_follow_the_adopted_split_default() -> None:
    """🚨 이 모듈의 존재 이유 — WAN-114/145는 병합에 고정돼 있다."""
    assert OrderBlockParams() == ADOPTED_OB_PARAMS
    assert ADOPTED_OB_PARAMS.combine_obs is False
    assert ADOPTED_OB_PARAMS.combine_obs != LEGACY_COMBINE_OBS
    assert ADOPTED_OB_PARAMS != LEGACY_OB_PARAMS
    assert "combine_obs=False" in describe_engine()


def test_bands_still_follow_the_adopted_default_too() -> None:
    """존을 옮기면서 밴드를 잃지 않는다 — 두 축이 다 채택 기본값이라야 오늘의 엔진이다."""
    for rung in RUNGS:
        band = rung_params(rung).deviation_filter
        if band is None:
            continue  # 볼린저를 끈 단(L0·L0r·L1)에는 밴드가 없다.
        assert band.band_bar == "intrabar_live"


# ------------------------------------------------- 2. 어느 단이 채택 기본값인가


def test_adopted_rung_is_exactly_the_default() -> None:
    assert rung_params(RUNGS_BY_NAME[ADOPTED_RUNG_NAME]) == ConfluenceParams()


def test_l2_is_not_the_adopted_default() -> None:
    l2 = rung_params(RUNGS_BY_NAME["L2"])
    assert l2 != ConfluenceParams()
    assert l2.rsi_gate_mode == "first_tap_free"
    assert ConfluenceParams().rsi_gate_mode == "unconditional"


def test_ladder_table_marks_the_adopted_rung() -> None:
    table = ladder_table()
    assert ADOPTED_RUNG_NAME in table
    assert "**예**" in table


# ------------------------------------------------- 3. 사다리를 다시 정의하지 않는다


def test_ladder_is_the_wan145_objects() -> None:
    assert RUNGS == wan145.RUNGS
    assert LADDER == ("L0", "L0r", "L1", "L2", ADOPTED_RUNG_NAME)


def test_bollinger_step_is_the_rung_that_carries_the_conclusion() -> None:
    assert BOLLINGER_STEP == ("L1", "L2") == wan145.BOLLINGER_STEP


# ------------------------------------------------------- 집계 · 판정


def _row(
    *,
    level: str,
    symbol: str = "BTC/USDT:USDT",
    segment: str = "oos",
    timeframe: str = "1h",
    total_return: float = 0.1,
    num_trades: int = 100,
) -> AblationRow:
    return AblationRow(
        level=level,
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        window=0,
        entry_mode="zone_limit",
        take_profit_r=1.5,
        offset_bps=2.0,
        retap_mode="every_tap",
        position_mode="single",
        portfolio_leverage=None,
        combine_obs=False,
        fill=OFFICIAL_LENS,
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


# ------------------------------------------------------- 4. 검산


def _null_row(*, symbol: str, real: float, trades: int) -> NullRow:
    return NullRow(
        symbol=symbol,
        timeframe="1h",
        segment="oos",
        arm=LONG_ARM,
        fill=OFFICIAL_LENS,
        combine_obs=False,
        real_total_return=real,
        real_num_trades=trades,
        real_long=trades,
        real_short=0,
        pool_size=trades * 2,
        random_mean_return=0.0,
        random_ci_low=-0.1,
        random_ci_high=0.1,
        random_p_value=0.5,
        iterations=200,
        bucket_fallback_count=0,
        zones=300,
        buy_hold=-0.2,
    )


def _write_null(tmp_path: Path, *, btc: float, eth: float) -> Path:
    path = tmp_path / "null.csv"
    null_rows_to_frame(
        [
            _null_row(symbol="BTC/USDT:USDT", real=btc, trades=100),
            _null_row(symbol="ETH/USDT:USDT", real=eth, trades=100),
        ]
    ).to_csv(path, index=False)
    return path


def test_crosscheck_passes_when_the_two_modules_agree(tmp_path: Path) -> None:
    """`L2u` ≡ §1 널의 실제 팔 — 같은 것을 두 경로로 돌린 값이라 차이가 0이어야 한다."""
    note, worst = crosscheck_against_null(_ladder_rows(), _write_null(tmp_path, btc=0.14, eth=0.34))
    assert worst == 0.0
    assert "차이 0" in note


def test_crosscheck_reports_a_mismatch(tmp_path: Path) -> None:
    """어긋나면 **조용히 넘어가지 않는다** — 그게 검산의 존재 이유다."""
    note, worst = crosscheck_against_null(_ladder_rows(), _write_null(tmp_path, btc=0.20, eth=0.34))
    assert worst is not None and worst > 0
    assert "어긋난다" in note


def test_crosscheck_calls_float_noise_by_its_name(tmp_path: Path) -> None:
    """끝자리 차이를 「어긋난다」로 찍으면 진짜 어긋남과 구분되지 않는다 — 다만 **숨기지도
    않는다**(크기와 문턱을 같이 적는다)."""
    noise = 0.14 + 1e-16
    null_csv = _write_null(tmp_path, btc=noise, eth=0.34)
    note, worst = crosscheck_against_null(_ladder_rows(), null_csv)
    assert worst is not None and 0 < worst <= FLOAT_NOISE
    assert "부동소수 오차 이내" in note
    assert "어긋난다" not in note


def test_crosscheck_says_so_when_it_cannot_run(tmp_path: Path) -> None:
    note, worst = crosscheck_against_null(_ladder_rows(), tmp_path / "missing.csv")
    assert worst is None
    assert "검산 불가" in note


# ------------------------------------------------------- 왕복 · 렌더


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    rows = _ladder_rows()
    path = tmp_path / "abl.csv"
    rows_to_frame(rows).to_csv(path, index=False)

    assert rows_from_csv(path) == rows


def test_summary_names_the_zone_policy_and_carries_the_crosscheck(tmp_path: Path) -> None:
    summary = build_summary_markdown(
        _ladder_rows(), csv_path=Path("x.csv"), null_csv=_write_null(tmp_path, btc=0.14, eth=0.34)
    )
    assert "# WAN-151 §2" in summary
    assert "`L2`는 채택 기본값이 아니다" in summary
    assert "combine_obs=False" in summary
    assert "차이 0" in summary
    assert "ETH 제외" in summary
