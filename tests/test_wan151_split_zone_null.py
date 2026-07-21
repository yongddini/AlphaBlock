"""backtest.wan151_split_zone_null 테스트 (WAN-151 §1).

이 파일이 지키는 것은 넷이다:

1. **존 정의가 병합에 고정돼 있지 않다** — 이 리포트의 존재 이유가 "분리 존에서 다시 재는
   것"이라, 누군가 `harness.LEGACY_OB_PARAMS`를 다시 끼우면 리포트가 조용히 WAN-145의
   사본이 된다. 라벨이 아니라 **탐지에 넘어간 값**으로 고정한다.
2. **무력화 축이 살아 있다** — 풀이 실제와 같아지면 널은 자기 자신을 검정하면서도 p값을
   멀쩡히 뱉는다(WAN-124가 발견한 함정).
3. **자와 팔 정의를 남에게서 가져온다** — 자는 WAN-70/84/88/124/145와 같은 값, 팔은 WAN-89의
   정의 그대로. 각자 정의하면 두 표가 같은 라벨로 다른 것을 재게 된다.
4. **판정 문장이 행에서 계산된다** — 숫자를 문장에 박으면 재실행 뒤 리포트가 거짓말을 한다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from backtest import wan89_short_autopsy
from backtest import wan145_new_band_null as wan145
from backtest.harness import LEGACY_COMBINE_OBS, LEGACY_OB_PARAMS
from backtest.wan70_random_control_b import run_random_control_b_segment
from backtest.wan88_long_only_validation import _MIN_TRADES_FOR_VERDICT as WAN88_MIN_TRADES
from backtest.wan123_matched_null import (
    NEUTRALIZED_POOL_UPDATES as WAN124_NEUTRALIZED,
)
from backtest.wan151_split_zone_null import (
    ADOPTED_OB_PARAMS,
    ARM_NAMES,
    LONG_ARM,
    MIN_TRADES_FOR_VERDICT,
    NEUTRALIZED_POOL_UPDATES,
    OFFICIAL_LENS,
    NullRow,
    arm_of,
    arm_summary,
    build_conclusion,
    build_summary_markdown,
    cell_table,
    describe_engine,
    eth_dependence,
    is_significant,
    pool_growth_note,
    pool_params,
    real_params,
    rows_from_csv,
    rows_to_frame,
    split_zone_note,
    verdict,
)
from strategy.models import ConfluenceParams, OrderBlockParams

# ------------------------------------------------- 1. 존을 병합에 고정하지 않는다


def test_detection_params_follow_the_adopted_split_default() -> None:
    """🚨 이 모듈의 존재 이유 — WAN-145는 `LEGACY_OB_PARAMS`(병합)에 묶여 있다.

    이 테스트가 깨진다면 누군가 병합 핀을 다시 끼웠다는 뜻이고, 그러면 이 표는 「지금
    매매하는 존 정의에 엣지가 있는가」에 답하지 못한다.
    """
    assert OrderBlockParams() == ADOPTED_OB_PARAMS
    assert ADOPTED_OB_PARAMS.combine_obs is False
    assert ADOPTED_OB_PARAMS.combine_obs != LEGACY_COMBINE_OBS
    assert ADOPTED_OB_PARAMS != LEGACY_OB_PARAMS
    assert "combine_obs=False" in describe_engine()


def test_wan145_null_is_still_pinned_to_the_merged_zone() -> None:
    """옛 표는 손대지 않았다 — 병합 고정이 살아 있어야 그 CSV가 비트 단위로 재현된다."""
    assert LEGACY_OB_PARAMS.combine_obs is True
    assert "LEGACY_OB_PARAMS" in inspect.getsource(wan145.run_cell)


def test_long_arm_is_exactly_the_adopted_default() -> None:
    """검정 대상은 「지금 채택된 것」 그 자체다(팔이 아무것도 덧붙이지 않는다)."""
    assert real_params(arm_of(LONG_ARM)) == ConfluenceParams()


def test_scope_is_the_long_axis_only() -> None:
    """이슈 §3: 숏 축은 범위 밖(WAN-145가 이미 처음 쟀고 판정 (c)였다)."""
    assert ARM_NAMES == (LONG_ARM,)


# ------------------------------------------------------- 2. 무력화 축이 살아 있다


def test_pool_turns_bollinger_off_and_keeps_everything_else() -> None:
    real = real_params(arm_of(LONG_ARM))
    pool = pool_params(arm_of(LONG_ARM))
    assert real.deviation_filter is not None
    assert pool.deviation_filter is None
    # 팔·체결 가정·오프셋이 어긋나면 널이 규칙이 아니라 다른 것을 재게 된다.
    assert pool.short_enabled == real.short_enabled
    assert pool.fill_penetration_bps == real.fill_penetration_bps
    assert pool.zone_limit_offset_bps == real.zone_limit_offset_bps
    assert pool.rsi_gate_mode == real.rsi_gate_mode


def test_neutralized_axis_is_the_same_as_wan124_and_wan145() -> None:
    """무력화 축을 물려받는다 — 축이 다르면 두 표를 맞댈 수 없다."""
    assert (
        NEUTRALIZED_POOL_UPDATES
        == WAN124_NEUTRALIZED
        == wan145.NEUTRALIZED_POOL_UPDATES
        == {"deviation_filter": None}
    )


def test_degenerate_pool_is_rejected() -> None:
    """🚨 풀 == 실제 퇴화를 **동작으로** 막는다(라벨이 아니라 예외로)."""
    params = real_params(arm_of(LONG_ARM))
    with pytest.raises(ValueError, match="무력화 풀이 실제 후보 집합과"):
        run_random_control_b_segment(
            pd.DataFrame(),
            pd.DataFrame(),
            "1h",
            symbol="BTC/USDT:USDT",
            segment="OOS",
            gate=LONG_ARM,
            confluence_params=params,
            pool_params=params,
        )


def test_pool_growth_note_reports_the_ratio() -> None:
    assert "2.00배" in pool_growth_note([_row(trades=50, pool=100)])


def test_split_zone_note_reads_the_rows_not_a_label() -> None:
    note = split_zone_note([_row()])
    assert "[False]" in note
    assert "분리" in note


# ------------------------------------------------- 3. 자와 팔을 남에게서 가져온다


def test_ruler_matches_wan70_84_88_124_145() -> None:
    assert MIN_TRADES_FOR_VERDICT == WAN88_MIN_TRADES == wan145.MIN_TRADES_FOR_VERDICT == 20


def test_bootstrap_seed_and_iterations_match_wan145() -> None:
    """존 말고 다른 것이 움직이면 두 표를 맞댈 수 없다."""
    from backtest.wan151_split_zone_null import BOOTSTRAP_ITERATIONS, BOOTSTRAP_SEED

    assert (BOOTSTRAP_ITERATIONS, BOOTSTRAP_SEED) == (
        wan145.BOOTSTRAP_ITERATIONS,
        wan145.BOOTSTRAP_SEED,
    )


def test_arm_comes_from_wan89() -> None:
    """팔 정의를 여기서 다시 쓰지 않는다 — 같은 라벨로 다른 설정을 돌면 대조가 깨진다."""
    assert arm_of(LONG_ARM) is wan89_short_autopsy.ARMS_BY_NAME[LONG_ARM]
    assert arm_of(LONG_ARM).short_enabled is False


def test_short_default_is_untouched() -> None:
    assert ConfluenceParams().short_enabled is False


def test_official_lens_is_baseline_only() -> None:
    assert OFFICIAL_LENS == "baseline"


# ------------------------------------------------------- 4. 판정 문장


def _row(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
    real: float = 0.1,
    trades: int = 50,
    mean: float | None = 0.0,
    p: float | None = 0.01,
    pool: int = 120,
    zones: int = 300,
    buy_hold: float = -0.2,
) -> NullRow:
    return NullRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        arm=LONG_ARM,
        fill=OFFICIAL_LENS,
        combine_obs=False,
        real_total_return=real,
        real_num_trades=trades,
        real_long=trades,
        real_short=0,
        pool_size=pool,
        random_mean_return=mean,
        random_ci_low=-0.1,
        random_ci_high=0.1,
        random_p_value=p,
        iterations=200,
        bucket_fallback_count=0,
        zones=zones,
        buy_hold=buy_hold,
    )


def test_significance_requires_direction_not_just_p_value() -> None:
    """실제가 무작위보다 **나쁜데** p가 낮은 것은 하방이라 채택 근거가 아니다(WAN-70)."""
    assert is_significant(_row(real=0.1, mean=0.0, p=0.01))
    assert not is_significant(_row(real=-0.1, mean=0.0, p=0.01))


def test_thin_cells_are_excluded_from_the_verdict() -> None:
    thin = _row(trades=MIN_TRADES_FOR_VERDICT - 1)
    assert "판정 불가" in verdict([thin])
    assert "표본부족" in cell_table([thin])


def test_verdict_b_when_nothing_is_significant() -> None:
    rows = [_row(p=0.9, real=-0.05), _row(symbol="ETH/USDT:USDT", p=0.7, real=-0.02)]
    assert "(b) 무작위와 구분되지 않는다" in verdict(rows)


def test_verdict_a_when_every_eligible_cell_is_significant() -> None:
    rows = [_row(p=0.01), _row(symbol="ETH/USDT:USDT", p=0.02)]
    assert "(a) 무작위와 구분된다" in verdict(rows)


def test_verdict_c_when_cells_disagree() -> None:
    rows = [_row(p=0.01), _row(symbol="ETH/USDT:USDT", p=0.9, real=-0.05)]
    assert "(c) 일부 셀에만" in verdict(rows)


def test_eth_dependence_flags_a_sign_flip() -> None:
    """§4 요구 — 유의 셀이 나와도 ETH 의존이면 그렇게 적는다."""
    rows = [
        _row(symbol="ETH/USDT:USDT", real=0.4),
        _row(symbol="BTC/USDT:USDT", real=-0.1),
    ]
    lines = eth_dependence(rows, arm=LONG_ARM, segment="oos")
    assert lines and "부호가 뒤집힌다" in lines[0]


def test_arm_summary_keeps_positive_count_and_ex_eth_mean() -> None:
    rows = [
        _row(symbol="ETH/USDT:USDT", real=0.4),
        _row(symbol="BTC/USDT:USDT", real=-0.1),
    ]
    summary = arm_summary(rows)
    record = summary.iloc[0]
    assert record["positive"] == 1.0
    assert record["symbols"] == 2.0
    assert record["ex_eth_mean"] == pytest.approx(-0.1)


def test_conclusion_contrasts_the_merged_table_and_warns() -> None:
    text = build_conclusion([_row(p=0.9, real=-0.02)])
    assert "WAN-145" in text  # 병합 판과의 대조 문단
    assert "엣지 찾았다" in text  # 인용 금지 경고
    assert "낙관 렌즈" in text


# ------------------------------------------------------- 왕복 · 렌더


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    rows = [_row(), _row(symbol="ETH/USDT:USDT", timeframe="15m")]
    path = tmp_path / "null.csv"
    rows_to_frame(rows).to_csv(path, index=False)

    assert rows_from_csv(path) == rows


def test_summary_renders_and_names_the_zone_policy() -> None:
    summary = build_summary_markdown([_row()], csv_path=Path("x.csv"))

    assert "# WAN-151" in summary
    assert "combine_obs=False" in summary
    assert "ETH leave-one-out" in summary
