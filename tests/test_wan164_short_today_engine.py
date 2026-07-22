"""backtest.wan164_short_today_engine 테스트 (WAN-164).

이 파일이 지키는 것은 다섯이다:

1. **오늘 엔진을 잰다** — 분리 존(`combine_obs=False`)·존폭 필터 `1.28`·봉내 라이브 밴드.
   누군가 `LEGACY_OB_PARAMS`(병합)나 필터 끄기를 다시 끼우면 이 표는 WAN-145의 사본이 된다.
2. **무력화 축이 살아 있다** — 풀이 실제와 같아지면 널은 자기 자신을 검정한다(WAN-124 함정).
   숏 경로에서도 동작으로 막힌다.
3. **자·팔·시드를 남에게서 가져온다** — 자는 WAN-70/84/88/124/145와 같은 값, 팔은 WAN-89.
4. **판정 문장이 행에서 계산된다** — 숫자를 문장에 박으면 재실행 뒤 리포트가 거짓말을 한다.
5. **검산이 일치·잡음·불일치를 다르게 찍는다** — 널 실제 다리 ≡ 성과 표(WAN-151 패턴).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest import wan89_short_autopsy
from backtest.harness import LEGACY_BAND_BAR, LEGACY_OB_PARAMS
from backtest.wan70_random_control_b import run_random_control_b_segment
from backtest.wan88_long_only_validation import _MIN_TRADES_FOR_VERDICT as WAN88_MIN_TRADES
from backtest.wan123_matched_null import NEUTRALIZED_POOL_UPDATES as WAN124_NEUTRALIZED
from backtest.wan145_new_band_null import BOOTSTRAP_SEED as WAN145_SEED
from backtest.wan145_new_band_null import NEUTRALIZED_POOL_UPDATES as WAN145_NEUTRALIZED
from backtest.wan164_short_today_engine import (
    ADOPTED_OB_PARAMS,
    ARM_NAMES,
    BOOTSTRAP_SEED,
    LONG_ARM,
    MIN_TRADES_FOR_VERDICT,
    NEUTRALIZED_POOL_UPDATES,
    OFFICIAL_LENS,
    NullRow,
    PnlRow,
    arm_of,
    build_summary_markdown,
    cell_table,
    cross_check,
    describe_engine,
    is_significant,
    null_from_csv,
    null_to_frame,
    pnl_from_csv,
    pnl_to_frame,
    pool_params,
    real_params,
    short_axis_verdict,
    verdict,
)
from strategy.models import ConfluenceParams

# ------------------------------------------------------- 1. 오늘 엔진을 잰다


def test_detection_uses_separated_zones_not_legacy_merge() -> None:
    """🚨 이 모듈의 존재 이유 — WAN-145가 병합에 묶여 있어서 여기가 필요하다."""
    assert ADOPTED_OB_PARAMS.combine_obs is False  # 분리 존(WAN-149 채택 기본값)
    assert ADOPTED_OB_PARAMS.combine_obs != LEGACY_OB_PARAMS.combine_obs
    assert "combine_obs=False" in describe_engine()


def test_real_params_carry_the_adopted_filter_and_band() -> None:
    adopted = ConfluenceParams()
    p = real_params(arm_of(LONG_ARM))
    assert p.max_zone_width_atr == adopted.max_zone_width_atr == 1.28
    assert p.deviation_filter is not None
    assert p.deviation_filter.band_bar == "intrabar_live" != LEGACY_BAND_BAR
    assert "max_zone_width_atr=1.28" in describe_engine()
    assert "band_bar=intrabar_live" in describe_engine()


def test_long_arm_is_exactly_the_adopted_default() -> None:
    assert real_params(arm_of(LONG_ARM)) == ConfluenceParams()


# ------------------------------------------------------- 2. 무력화 축이 살아 있다


def test_pool_turns_bollinger_off_and_keeps_arm_and_filter() -> None:
    for name in ARM_NAMES:
        real = real_params(arm_of(name))
        pool = pool_params(arm_of(name))
        assert real.deviation_filter is not None
        assert pool.deviation_filter is None
        # 팔·필터·오프셋이 어긋나면 널이 볼린저가 아니라 다른 것을 재게 된다.
        assert pool.short_enabled == real.short_enabled
        assert pool.max_zone_width_atr == real.max_zone_width_atr == 1.28
        assert pool.zone_limit_offset_bps == real.zone_limit_offset_bps
        assert pool.rsi_gate_mode == real.rsi_gate_mode


def test_neutralized_axis_matches_wan124_and_wan145() -> None:
    assert (
        NEUTRALIZED_POOL_UPDATES
        == WAN124_NEUTRALIZED
        == WAN145_NEUTRALIZED
        == {"deviation_filter": None}
    )


@pytest.mark.parametrize("arm_name", ["short_only", "both"])
def test_degenerate_pool_rejected_on_short_path(arm_name: str) -> None:
    """숏 경로에서도 풀 == 실제 퇴화를 **예외로** 막는다(WAN-124 함정)."""
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


# ------------------------------------------------------- 3. 자·팔·시드를 남에게서


def test_ruler_and_seed_match_wan145() -> None:
    assert MIN_TRADES_FOR_VERDICT == WAN88_MIN_TRADES == 20
    assert BOOTSTRAP_SEED == WAN145_SEED == 124


def test_arms_come_from_wan89() -> None:
    for name in ARM_NAMES:
        assert arm_of(name) is wan89_short_autopsy.ARMS_BY_NAME[name]
    assert arm_of("short_only").short_enabled is True
    assert arm_of("short_only").allow_long is False
    assert arm_of("both").short_enabled is True


def test_short_default_switch_unchanged() -> None:
    assert ConfluenceParams().short_enabled is False


def test_official_lens_is_baseline_only() -> None:
    assert OFFICIAL_LENS == "baseline"


# ------------------------------------------------------- 4. 판정 문장


def _null(
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
        buy_hold=-0.2,
    )


def test_significance_requires_direction_not_just_p() -> None:
    assert is_significant(_null(real=0.1, mean=0.0, p=0.01))
    assert not is_significant(_null(real=-0.1, mean=0.0, p=0.01))


def test_thin_cells_excluded_from_verdict() -> None:
    thin = _null(trades=MIN_TRADES_FOR_VERDICT - 1)
    assert "판정 불가" in verdict([thin], arm=LONG_ARM)
    assert "표본부족" in cell_table([thin], arm=LONG_ARM)


def test_verdict_b_when_nothing_significant() -> None:
    rows = [_null(p=0.9, real=-0.05), _null(symbol="ETH/USDT:USDT", p=0.7, real=-0.02)]
    assert "(b) 무작위와 구분되지 않는다" in verdict(rows, arm=LONG_ARM)


def test_verdict_a_when_all_significant() -> None:
    rows = [_null(p=0.01), _null(symbol="ETH/USDT:USDT", p=0.02)]
    assert "(a) 무작위와 구분된다" in verdict(rows, arm=LONG_ARM)


def test_verdict_c_when_cells_disagree() -> None:
    rows = [_null(p=0.01), _null(symbol="ETH/USDT:USDT", p=0.9, real=-0.05)]
    assert "(c) 일부 셀에만" in verdict(rows, arm=LONG_ARM)


def test_short_axis_verdict_reads_both_short_arms() -> None:
    # 두 숏 팔 전부 유의 → (a)
    sig = [_null(arm=a, p=0.01) for a in ("short_only", "both")]
    assert "(a) 숏은 오늘 엔진에서 값을 더한다" in short_axis_verdict(sig)
    # 아무것도 유의하지 않음 → (b)
    none = [_null(arm=a, p=0.9, real=-0.05) for a in ("short_only", "both")]
    assert "(b) 숏은 오늘 엔진에서 값을 더하지 않는다" in short_axis_verdict(none)
    # 하나만 유의 → (c)
    mixed = [_null(arm="short_only", p=0.01), _null(arm="both", p=0.9, real=-0.05)]
    assert "(c) TF·구간에 갈린다" in short_axis_verdict(mixed)


# ------------------------------------------------------- 5. 검산


def _pnl(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
    arm: str = LONG_ARM,
    total: float = 0.1,
) -> PnlRow:
    return PnlRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        arm=arm,
        num_bars=1000,
        num_trades=50,
        win_rate=0.5,
        total_return=total,
        max_drawdown=0.1,
        sharpe=1.0,
        mean_r=0.1,
        fill_rate=0.8,
        eligible_setups=100,
        num_filled=80,
        buy_hold=-0.2,
    )


def test_cross_check_passes_on_float_noise() -> None:
    check = cross_check([_pnl(total=0.1)], [_null(real=0.1 + 1e-15)])
    assert check.compared == 1
    assert not check.mismatches
    assert check.max_abs_diff is not None and check.max_abs_diff < 1e-9


def test_cross_check_flags_real_mismatch() -> None:
    check = cross_check([_pnl(total=0.1)], [_null(real=0.25)])
    assert check.mismatches
    assert check.mismatches[0][0] == "BTC"


def test_cross_check_ignores_short_arms() -> None:
    """숏 팔은 검산 대상이 아니다(시퀀싱 경로 차이) — 롱만 본다."""
    check = cross_check([_pnl(arm="both", total=0.1)], [_null(arm="both", real=0.9)])
    assert check.compared == 0
    assert not check.mismatches


# ------------------------------------------------------- 왕복 · 렌더


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    null_rows = [_null(), _null(symbol="ETH/USDT:USDT", arm="short_only")]
    pnl_rows = [_pnl(), _pnl(symbol="ETH/USDT:USDT", arm="short_only")]
    npath, ppath = tmp_path / "null.csv", tmp_path / "pnl.csv"
    null_to_frame(null_rows).to_csv(npath, index=False)
    pnl_to_frame(pnl_rows).to_csv(ppath, index=False)
    assert null_from_csv(npath) == null_rows
    assert pnl_from_csv(ppath) == pnl_rows


def test_summary_renders_and_names_the_engine() -> None:
    null_rows = [_null(arm=name) for name in ARM_NAMES]
    pnl_rows = [_pnl(arm=name) for name in ARM_NAMES]
    summary = build_summary_markdown(
        pnl_rows, null_rows, pnl_csv=Path("p.csv"), null_csv=Path("n.csv")
    )
    assert "# WAN-164" in summary
    assert "combine_obs=False" in summary
    assert "max_zone_width_atr=1.28" in summary
    assert "short_only" in summary
    assert "leave-one-out" in summary
    assert "숏 축 종합 판정" in summary
