"""backtest.wan145_new_band_null 테스트 (WAN-145 §1·§3).

이 파일이 지키는 것은 넷이다:

1. **밴드가 고정돼 있지 않다** — 이 리포트의 존재 이유가 "새 밴드에서 다시 재는 것"이라,
   누군가 `pin_band_bar`를 다시 끼우면 리포트가 조용히 WAN-124의 사본이 된다.
2. **무력화 축이 살아 있다** — 풀이 실제와 같아지면 널은 자기 자신을 검정하면서도 p값을
   멀쩡히 뱉는다(WAN-124가 발견한 함정). **숏 경로에서도** 동작으로 막힌다(§3 완료기준).
3. **자와 팔 정의를 남에게서 가져온다** — 자는 WAN-70/84/88/124와 같은 값, 팔은 WAN-89의
   정의 그대로. 각자 정의하면 두 표가 같은 라벨로 다른 것을 재게 된다.
4. **판정 문장이 행에서 계산된다** — 숫자를 문장에 박으면 재실행 뒤 리포트가 거짓말을 한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest import wan89_short_autopsy
from backtest.harness import LEGACY_BAND_BAR
from backtest.wan70_random_control_b import run_random_control_b_segment
from backtest.wan88_long_only_validation import _MIN_TRADES_FOR_VERDICT as WAN88_MIN_TRADES
from backtest.wan123_matched_null import (
    NEUTRALIZED_POOL_UPDATES as WAN124_NEUTRALIZED,
)
from backtest.wan145_new_band_null import (
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
    verdict,
)
from strategy.models import ConfluenceParams

# ------------------------------------------------------- 1. 밴드를 고정하지 않는다


def test_real_params_follow_the_adopted_band_and_are_not_pinned() -> None:
    """🚨 이 모듈의 존재 이유 — WAN-124가 `tap`에 묶여 있어서 여기가 필요하다.

    이 테스트가 깨진다면 누군가 `pin_band_bar`를 다시 끼웠다는 뜻이고, 그러면 이 표는
    「지금 매매하는 밴드에 엣지가 있는가」에 답하지 못한다.
    """
    adopted = ConfluenceParams()
    assert adopted.deviation_filter is not None
    band = real_params(arm_of(LONG_ARM)).deviation_filter
    assert band is not None
    assert band.band_bar == adopted.deviation_filter.band_bar == "intrabar_live"
    assert band.band_bar != LEGACY_BAND_BAR  # WAN-124가 고정한 옛 밴드가 아니다.
    assert "band_bar=intrabar_live" in describe_engine()


def test_long_arm_is_exactly_the_adopted_default() -> None:
    """§1의 검정 대상은 「지금 채택된 것」 그 자체다(팔이 아무것도 덧붙이지 않는다)."""
    assert real_params(arm_of(LONG_ARM)) == ConfluenceParams()


# ------------------------------------------------------- 2. 무력화 축이 살아 있다


def test_pool_turns_bollinger_off_and_keeps_the_arm_switch() -> None:
    for name in ARM_NAMES:
        real = real_params(arm_of(name))
        pool = pool_params(arm_of(name))
        assert real.deviation_filter is not None
        assert pool.deviation_filter is None
        # 팔·체결 가정·오프셋이 어긋나면 널이 규칙이 아니라 다른 것을 재게 된다.
        assert pool.short_enabled == real.short_enabled
        assert pool.fill_penetration_bps == real.fill_penetration_bps
        assert pool.zone_limit_offset_bps == real.zone_limit_offset_bps
        assert pool.rsi_gate_mode == real.rsi_gate_mode


def test_neutralized_axis_is_the_same_as_wan124() -> None:
    """무력화 축을 WAN-124에서 물려받는다 — 축이 다르면 두 표를 맞댈 수 없다."""
    assert NEUTRALIZED_POOL_UPDATES == WAN124_NEUTRALIZED == {"deviation_filter": None}


@pytest.mark.parametrize("arm_name", ["short_only", "both"])
def test_degenerate_pool_is_rejected_on_the_short_path(arm_name: str) -> None:
    """🚨 §3 완료기준 — 숏 경로에서도 풀 == 실제 퇴화를 **동작으로** 막는다.

    라벨이 아니라 예외로 막혀야 한다. 게이트가 없는 엔진에서 RSI 무력화가 아무것도 하지
    않는다는 WAN-124의 함정은 롱/숏을 가리지 않는다.
    """
    params = real_params(arm_of(arm_name))
    with pytest.raises(ValueError, match="무력화 풀이 실제 후보 집합과"):
        run_random_control_b_segment(
            pd.DataFrame(),
            pd.DataFrame(),
            "1h",
            symbol="BTC/USDT:USDT",
            segment="OOS",
            gate=arm_name,
            confluence_params=params,
            pool_params=params,
        )


def test_pool_growth_note_reports_the_ratio() -> None:
    assert "2.00배" in pool_growth_note([_row(trades=50, pool=100)])


# ------------------------------------------------------- 3. 자와 팔을 남에게서 가져온다


def test_ruler_matches_wan70_84_88_124() -> None:
    assert MIN_TRADES_FOR_VERDICT == WAN88_MIN_TRADES == 20


def test_arms_come_from_wan89() -> None:
    """팔 정의를 여기서 다시 쓰지 않는다 — 같은 라벨로 다른 설정을 돌면 대조가 깨진다."""
    for name in ARM_NAMES:
        assert arm_of(name) is wan89_short_autopsy.ARMS_BY_NAME[name]
    assert arm_of("short_only").short_enabled is True
    assert arm_of("short_only").allow_long is False
    assert arm_of("both").short_enabled is True


def test_short_arms_do_not_change_the_default_short_switch() -> None:
    """`short_enabled` 기본값은 불변(§3 완료기준) — 실험은 명시적 오버라이드로만."""
    assert ConfluenceParams().short_enabled is False


def test_official_lens_is_baseline_only() -> None:
    assert OFFICIAL_LENS == "baseline"


# ------------------------------------------------------- 4. 판정 문장


def _row(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
    arm: str = LONG_ARM,
    real: float = 0.1,
    trades: int = 50,
    mean: float | None = 0.0,
    p: float | None = 0.01,
    pool: int = 120,
    buy_hold: float = -0.2,
) -> NullRow:
    return NullRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        arm=arm,
        fill=OFFICIAL_LENS,
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
        buy_hold=buy_hold,
    )


def test_significance_requires_direction_not_just_p_value() -> None:
    """실제가 무작위보다 **나쁜데** p가 낮은 것은 하방이라 채택 근거가 아니다(WAN-70)."""
    assert is_significant(_row(real=0.1, mean=0.0, p=0.01))
    assert not is_significant(_row(real=-0.1, mean=0.0, p=0.01))


def test_thin_cells_are_excluded_from_the_verdict() -> None:
    thin = _row(trades=MIN_TRADES_FOR_VERDICT - 1)
    assert "판정 불가" in verdict([thin], arm=LONG_ARM)
    assert "표본부족" in cell_table([thin], arm=LONG_ARM)


def test_verdict_b_when_nothing_is_significant() -> None:
    rows = [_row(p=0.9, real=-0.05), _row(symbol="ETH/USDT:USDT", p=0.7, real=-0.02)]
    assert "(b) 무작위와 구분되지 않는다" in verdict(rows, arm=LONG_ARM)


def test_verdict_a_when_every_eligible_cell_is_significant() -> None:
    rows = [_row(p=0.01), _row(symbol="ETH/USDT:USDT", p=0.02)]
    assert "(a) 무작위와 구분된다" in verdict(rows, arm=LONG_ARM)


def test_verdict_c_when_cells_disagree() -> None:
    rows = [_row(p=0.01), _row(symbol="ETH/USDT:USDT", p=0.9, real=-0.05)]
    assert "(c) 일부 셀에만" in verdict(rows, arm=LONG_ARM)


def test_eth_dependence_flags_a_sign_flip() -> None:
    """§3 요구 1 — 유의 셀이 나와도 ETH 의존이면 그렇게 적는다."""
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


def test_conclusion_covers_both_axes_and_the_lens_warning() -> None:
    rows = [_row(arm=name, p=0.9, real=-0.02) for name in ARM_NAMES]
    text = build_conclusion(rows)
    assert "롱 축" in text and "숏 축" in text
    assert "낙관 렌즈" in text
    # 숏 축 판정을 롱 축 판정의 반박으로 읽지 말라는 경고(§3 완료기준).
    assert "반박이 아니다" in text


# ------------------------------------------------------- 왕복 · 렌더


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    rows = [_row(), _row(symbol="ETH/USDT:USDT", arm="short_only")]
    path = tmp_path / "null.csv"
    rows_to_frame(rows).to_csv(path, index=False)

    assert rows_from_csv(path) == rows


def test_summary_renders_and_names_the_band() -> None:
    rows = [_row(arm=name) for name in ARM_NAMES]
    summary = build_summary_markdown(rows, csv_path=Path("x.csv"))

    assert "# WAN-145" in summary
    assert "intrabar_live" in summary
    assert "short_only" in summary
    assert "ETH leave-one-out" in summary
