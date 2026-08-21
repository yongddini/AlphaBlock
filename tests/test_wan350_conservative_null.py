"""backtest.wan350_conservative_null 테스트 (WAN-350).

이 파일이 지키는 것은 다섯이다:

1. **보수 축이 실제 팔과 무력화 풀에 똑같이 걸린다** — 한쪽만 걸면 「실제는 보수, 널은
   낙관」인 잡종 대조가 되어 p값이 규칙이 아니라 **가정 차이**를 잰다. 인자를 넘기는 줄이
   아니라 **두 후보 집합이 실제로 갈리는지**로 건다.
2. **노브를 끄면 널 기계가 예전과 같다** — WAN-350의 리팩터(후보 생성을 밖으로 빼고 여러
   평가창이 나눠 쓰게 한 것)가 숫자를 움직이면 옛 널 계열 CSV가 전부 무효가 된다.
3. **따뜻한 평가창이 라벨이 아니라 동작이다** — `eval_from_ms`가 실제로 탭 시각으로 자르고,
   실제·풀 **양쪽**에 걸려야 두 집합이 같은 기간을 본다.
4. **자와 좌표를 남에게서 가져온다** — 자는 WAN-70/84/88/124/145/151과 같은 값, 좌표는
   채택 기본값. 여기서 새로 쓰면 두 표가 같은 라벨로 다른 것을 재게 된다.
5. **집계가 조용히 두 배가 되지 않는다** — `--append`가 같은 셀을 덮어쓰지 않으면 유의 셀
   수가 소리 없이 부풀려진다.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest import wan70_random_control_b as wan70
from backtest import wan151_split_zone_null as wan151
from backtest import wan350_conservative_null as wan350
from backtest.run import parse_date_ms
from backtest.wan176_nine_symbol_rebaseline import NEW_SYMBOLS, OLD_SYMBOLS
from backtest.zone_limit_backtest import build_zone_limit_candidates
from strategy.models import ConfluenceParams, OrderBlockParams

# --------------------------------------------------------------- 1. 보수 축의 대칭


def test_arm_d_applies_the_lens_to_both_real_and_pool() -> None:
    """🚨 이 모듈의 핵심 계약 — 렌즈가 한쪽에만 걸리면 p값이 가정 차이를 잰다.

    렌즈는 `ConfluenceParams`에 실려 있으므로 풀이 실제에서 파생되는 한 자동으로 따라온다.
    그 「자동으로」가 깨지지 않는지를 값으로 확인한다(파생 방식을 바꾸면 여기서 걸린다).
    """
    arm_d = wan350.ARMS_BY_NAME["D"]
    real, pool = arm_d.params(), arm_d.pool_params()
    assert real.fill_penetration_bps == harness.fill_preset("pen_5bp").penetration_bps > 0
    assert pool.fill_penetration_bps == real.fill_penetration_bps
    assert pool.fill_dropout_rate == real.fill_dropout_rate


def test_no_same_step_tp_reaches_both_candidate_builders() -> None:
    """두 생성이 한 함수에 묶여 있고 팔 인자가 **양쪽에** 흘러가는가.

    ⚠️ 「인자를 넘기는 줄이 있다」로 거는 것은 약한 테스트지만(WAN-345가 정확히 그
    실패였다), 이 축의 동작 확인은 실데이터 셀이 필요하므로 아래
    `test_no_same_step_tp_changes_the_real_arm`이 값으로 다시 건다. 여기서는 **두 호출이
    한 함수 안에 나란히 있다**는 구조를 고정한다 — 떨어지면 한쪽에만 축이 붙는다.
    """
    source = inspect.getsource(wan70._build_both_pools)
    assert source.count("no_same_step_tp=no_same_step_tp") == 2
    assert source.count("build_zone_limit_candidates(") == 2


def test_pool_neutralizes_bollinger_and_is_not_the_real_arm() -> None:
    """무력화 축이 살아 있는가 — 풀이 실제와 같아지면 널은 자기 자신을 검정한다(WAN-124)."""
    for arm in wan350.ARMS:
        real, pool = arm.params(), arm.pool_params()
        assert real.deviation_filter is not None
        assert pool.deviation_filter is None
        assert pool != real  # `run_random_control_b_segment`가 같으면 거부한다


def test_neutralized_axis_is_borrowed_not_redefined() -> None:
    """무력화 축·자·팔을 남에게서 가져온다 — 각자 정의하면 두 표가 갈린다."""
    assert wan350.NEUTRALIZED_POOL_UPDATES is wan151.NEUTRALIZED_POOL_UPDATES
    assert wan350.MIN_TRADES_FOR_VERDICT == wan151.MIN_TRADES_FOR_VERDICT == 20
    assert wan350.ALPHA == wan151.ALPHA == 0.05
    assert wan350.BOOTSTRAP_ITERATIONS == wan151.BOOTSTRAP_ITERATIONS == 200
    assert wan350.BOOTSTRAP_SEED == wan151.BOOTSTRAP_SEED == 124


# --------------------------------------------------------------- 2. 좌표에 핀이 없다


def test_coordinates_follow_the_adopted_defaults() -> None:
    """🚨 핀이 하나도 없어야 재-베이스라인이 오면 이 표도 따라간다(WAN-305)."""
    assert wan350.SYMBOLS is harness.DEFAULT_SYMBOLS
    assert wan350.TIMEFRAMES is harness.DEFAULT_TIMEFRAMES
    assert wan350.START == harness.DEFAULT_START
    assert wan350.END == harness.DEFAULT_END
    assert OrderBlockParams() == wan350.ADOPTED_OB_PARAMS


def test_arm_a_is_exactly_the_adopted_engine() -> None:
    """기준선 팔은 채택 기본값 그 자체여야 「보수화의 몫」이 갈린다."""
    arm_a = wan350.ARMS_BY_NAME["A"]
    assert arm_a.is_adopted
    assert arm_a.lens is None  # 채택 렌즈를 호출부가 복사하지 않는다(WAN-159 규약)
    assert arm_a.params() == wan151.arm_of(wan350.LONG_ARM).params()


def test_zone_width_filter_stays_on_at_the_adopted_threshold() -> None:
    """존폭 필터를 끄는 것은 「필터 끔」 팔이다 — 여기서는 채택 1.28을 물려받아야 한다.

    `build_params`에 `max_zone_width_atr`를 안 넘기면 센티넬 `UNSET`이라 base의 값이 남는다.
    `None`을 넘기면 **끄기**가 되므로(WAN-159) 그 실수를 값으로 막는다.
    """
    for arm in wan350.ARMS:
        assert arm.params().max_zone_width_atr == ConfluenceParams().max_zone_width_atr == 1.28
    assert "max_zone_width_atr=1.28" in wan350.describe_engine()


def test_segment_labels_match_the_harness_convention() -> None:
    """구간 라벨이 harness와 갈리면 CSV의 구간 이름이 다른 리포트와 안 맞는다."""
    assert wan350.SEGMENT_FULL == harness.SEGMENT_FULL
    assert wan350.SEGMENT_OOS_WARM == harness.SEGMENT_OOS_WARM
    assert set(wan350.SEGMENT_ORDER) <= set(wan70.Segment.__args__)  # type: ignore[attr-defined]


# --------------------------------------------------------------- 3. 판정 자


def _row(**overrides: object) -> wan350.NullRow:
    base: dict[str, object] = dict(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment=wan350.SEGMENT_OOS_WARM,
        arm="A",
        arm_label="x",
        lens="baseline",
        no_same_step_tp=False,
        combine_obs=False,
        max_zone_width_atr=1.28,
        real_total_return=0.5,
        real_num_trades=50,
        real_long=50,
        real_short=0,
        pool_size=120,
        random_mean_return=0.1,
        random_ci_low=0.0,
        random_ci_high=0.3,
        random_p_value=0.01,
        iterations=200,
        bucket_fallback_count=0,
        zones=10,
        buy_hold=0.2,
    )
    base.update(overrides)
    return wan350.NullRow.model_validate(base)


def test_significance_needs_both_p_value_and_beating_the_random_mean() -> None:
    """유의 = p≤α **이면서** 실제>무작위평균 — 한쪽만 보면 지면서 유의한 셀이 통과한다."""
    assert wan350.is_significant(_row())
    assert not wan350.is_significant(_row(random_p_value=0.20))
    assert not wan350.is_significant(_row(random_mean_return=0.9))  # p는 작지만 진다
    assert not wan350.is_significant(_row(random_p_value=None))


def test_thin_cells_are_excluded_from_the_verdict_but_counted() -> None:
    """표본 미달은 주의문이 아니라 **집계**다 — 판정에서 빠지되 표에 몇 개인지 남는다."""
    rows = [_row(), _row(symbol="ETH/USDT:USDT", real_num_trades=5)]
    assert len(wan350.eligible_rows(rows)) == 1
    frame = wan350.grid_summary(rows)
    cell = frame[(frame.arm == "A") & (frame.segment == wan350.SEGMENT_OOS_WARM)].iloc[0]
    assert int(cell.eligible) == 1
    assert int(cell.thin) == 1
    assert int(cell.symbols) == 2


def test_leave_one_out_recounts_without_the_excluded_symbol() -> None:
    """LOO는 per-cell 널에서 **재집계**다 — 뺀 종목의 셀이 실제로 빠져야 한다."""
    rows = [_row(), _row(symbol="ETH/USDT:USDT", random_p_value=0.9)]
    loo = {(r.excluded): r for r in wan350.leave_one_out(rows) if r.arm == "A"}
    assert loo["(없음)"].significant == 1 and loo["(없음)"].eligible == 2
    assert loo["BTC"].significant == 0 and loo["BTC"].eligible == 1
    assert loo["ETH"].significant == 1 and loo["ETH"].eligible == 1


# --------------------------------------------------------------- 4. 이어붙이기


def test_append_overwrites_the_same_cell_instead_of_duplicating(tmp_path: Path) -> None:
    """🚨 같은 셀이 두 번 실리면 유의 셀 수가 조용히 두 배가 된다."""
    path = tmp_path / "null.csv"
    first = wan350.rows_to_frame([_row(real_total_return=0.1)])
    first.to_csv(path, index=False)
    second = wan350.rows_to_frame([_row(real_total_return=0.9)])
    merged = wan350._merge_append(second, path)
    assert len(merged) == 1
    assert float(merged.iloc[0]["real_total_return"]) == 0.9

    other = wan350.rows_to_frame([_row(timeframe="4h")])
    assert len(wan350._merge_append(other, path)) == 2


# --------------------------------------------------------------- 5. 검산의 정직함


def test_zero_compared_rows_is_a_failure_not_a_pass() -> None:
    """비교한 행이 없으면 「차이 0」이 나온다 — 그건 재현이 아니라 대조 실패다(WAN-333)."""
    assert wan350._classify(0, None)[0] == "불일치"
    assert wan350._classify(0, 0.0)[0] == "불일치"
    assert wan350._classify(5, 0.0)[0] == "일치"
    assert wan350._classify(5, 1e-15)[0] == "잡음"
    assert wan350._classify(5, 0.01)[0] == "불일치"


def test_verify_blanks_funding_only_for_the_backfilled_symbols() -> None:
    """검산은 옛 실행 당시의 데이터 상태를 복원한다 — 그 복원이 **세 종목에만** 걸려야 한다."""
    sentinel = ("funding",)
    for symbol in NEW_SYMBOLS:
        assert wan350._verify_funding(symbol, sentinel) == ()
    for symbol in OLD_SYMBOLS:
        assert wan350._verify_funding(symbol, sentinel) == sentinel


def test_verify_runs_the_adopted_arm_with_both_knobs_off() -> None:
    """검산 팔은 노브를 끈 채여야 옛 CSV와 대조가 성립한다."""
    arm = wan350.ARMS_BY_NAME[wan350.ADOPTED_ARM]
    assert not arm.no_same_step_tp and arm.lens is None
    source = inspect.getsource(wan350.run_verify_cell)
    assert "no_same_step_tp" not in source  # 기본값(끔)을 쓴다 = 넘기지 않는다
    assert "eval_from_ms" not in source  # 차가운 절단이라 평가창을 안 준다


def test_summary_numbers_come_from_rows_not_prose() -> None:
    """판정 문장이 행에서 계산돼야 재실행 뒤 리포트가 거짓말을 하지 않는다."""
    rows = [_row(), _row(symbol="ETH/USDT:USDT", random_p_value=0.9)]
    text = wan350.build_summary(rows, wan350.leave_one_out(rows), [])
    assert "팔 A **1/2**" in text
    assert "검산 미실행" in text
    for warning in ("재진입이 이 널에 없다", "per-cell이라 채택 근거가 아니다", "민감도"):
        assert warning in text


# --------------------------------------------------------------- 6. 널 기계 리팩터


def test_multi_eval_shares_one_candidate_generation() -> None:
    """평가창이 여럿이어도 **후보 생성은 한 번**이라야 이 격자가 감당 가능하다."""
    source = inspect.getsource(wan70.run_random_control_b_evals)
    assert source.count("_build_both_pools(") == 1
    assert "_null_from_candidates(" in source


def test_single_segment_entry_point_still_exists_for_the_cold_convention() -> None:
    """차가운 절단(구간마다 재탐지)은 후보를 공유할 수 없어 옛 진입점을 계속 쓴다."""
    assert callable(wan70.run_random_control_b_segment)
    params = inspect.signature(wan70.run_random_control_b_segment).parameters
    assert params["no_same_step_tp"].default is False
    assert params["eval_from_ms"].default is None


def test_evaluated_from_cuts_on_tap_time_and_is_identity_when_off() -> None:
    """평가 경계는 **탭 시각**으로 자른다(진입 시각이 아니다) — 북·wan206과 같은 규약."""

    class _Fake:
        def __init__(self, trigger_time: int) -> None:
            self.trigger_time = trigger_time

    cands: list[Any] = [_Fake(10), _Fake(20), _Fake(30)]
    assert wan70._evaluated_from(cands, None) is cands
    kept: list[Any] = wan70._evaluated_from(cands, 20)
    assert [c.trigger_time for c in kept] == [20, 30]


# --------------------------------------------------------------- 7. 실데이터 회귀


_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "4h"


def _market() -> harness.MarketData:
    return harness.load_market_data(
        _SYMBOL,
        _TIMEFRAME,
        start_ms=parse_date_ms(wan350.START),
        end_ms=parse_date_ms(wan350.END),
    )


@pytest.mark.skipif(not wan350.NULL_CSV.exists(), reason="wan350 격자 CSV 없음(실데이터 실행 전)")
def test_grid_csv_carries_the_engine_it_actually_ran() -> None:
    """산출물만 봐도 어떤 존·필터로 돌았는지 드러나야 한다 — 라벨과 동작이 갈리지 않게."""
    frame = pd.read_csv(wan350.NULL_CSV)
    assert set(frame["arm"]) <= set(wan350.ARM_ORDER)
    assert (~frame["combine_obs"].astype(bool)).all()  # 채택 기본값(분리 존, WAN-149)
    assert (frame["max_zone_width_atr"] == 1.28).all()
    for arm, lens in zip(frame["arm"], frame["lens"], strict=True):
        assert lens == wan350.ARMS_BY_NAME[arm].lens_name


def test_real_arm_of_the_null_is_the_adopted_per_cell_engine() -> None:
    """🚨 검산 — 널의 「실제」 다리가 곧 채택 per-cell 엔진인가(실데이터).

    이것이 어긋나면 p값의 분자(실제 수익)가 우리가 실제로 매매하는 엔진의 것이 아니게 되어
    표 전체가 다른 경기의 기록이 된다. `harness.run_once`(= 인자 없는 `backtest.run`이 타는
    per-cell 경로)와 **같은 총수익률·거래 수**를 내는지로 건다.

    📌 기대값을 상수로 박지 않는다 — 두 경로를 같은 실행에서 나란히 돌려 비교한다(상수를
    박으면 재-베이스라인이 올 때 「엔진이 바뀐 것」과 「테스트가 낡은 것」이 안 갈린다).
    """
    market = _market()
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터가 없어 건너뜁니다(CI 기본).")

    arm = wan350.ARMS_BY_NAME[wan350.ADOPTED_ARM]
    ob_result = harness.detect_order_blocks(market, wan350.ADOPTED_OB_PARAMS)
    cfg = wan151.arm_of(wan350.LONG_ARM).config(_TIMEFRAME)
    expected = harness.run_once(market, params=arm.params(), cfg=cfg, order_block_result=ob_result)

    result = wan70.run_random_control_b_evals(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        symbol=_SYMBOL,
        evals=((wan350.SEGMENT_FULL, None),),
        gate=wan350.LONG_ARM,
        confluence_params=arm.params(),
        backtest_config=cfg,
        order_block_result=ob_result,
        iterations=1,
        seed=wan350.BOOTSTRAP_SEED,
        funding_rates=market.funding_rates,
        pool_params=arm.pool_params(),
    )[wan350.SEGMENT_FULL]

    assert result.real_num_trades == len(expected.result.trades)
    assert result.real_total_return == pytest.approx(
        expected.result.metrics.total_return, abs=1e-12
    )


def test_conservative_arm_actually_removes_same_step_take_profits() -> None:
    """🚨 팔 D가 라벨이 아니라 **동작**인가(실데이터) — WAN-345가 겪은 실패의 이 축 판.

    `no_same_step_tp`를 켜면 「진입한 그 1분 안에 익절」이 사라져야 한다. 인자를 넘기는 줄이
    아니라 **후보 층 카운터가 0이 되는지**로 건다(WAN-336 §검산 (d)와 같은 자).
    """
    market = _market()
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터가 없어 건너뜁니다(CI 기본).")

    ob_result = harness.detect_order_blocks(market, wan350.ADOPTED_OB_PARAMS)
    cfg = wan151.arm_of(wan350.LONG_ARM).config(_TIMEFRAME)
    counts: dict[bool, int] = {}
    for no_same_step in (False, True):
        _, stats = build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            _TIMEFRAME,
            params=wan350.ARMS_BY_NAME["D"].params(),
            cfg=cfg,
            order_block_result=ob_result,
            no_same_step_tp=no_same_step,
        )
        counts[no_same_step] = stats.same_step_take_profits
    assert counts[False] > 0, "기준선에 같은 분 익절이 없으면 이 테스트가 아무것도 안 잰다"
    assert counts[True] == 0


def test_warm_window_is_a_strict_subset_of_the_full_window() -> None:
    """따뜻한 평가창이 동작인가(실데이터) — 라벨만 바뀌면 두 구간이 같은 수를 낸다."""
    market = _market()
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터가 없어 건너뜁니다(CI 기본).")

    arm = wan350.ARMS_BY_NAME[wan350.ADOPTED_ARM]
    ob_result = harness.detect_order_blocks(market, wan350.ADOPTED_OB_PARAMS)
    warm_from = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    assert warm_from is not None
    cfg = wan151.arm_of(wan350.LONG_ARM).config(_TIMEFRAME)
    results = wan70.run_random_control_b_evals(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        symbol=_SYMBOL,
        evals=((wan350.SEGMENT_FULL, None), (wan350.SEGMENT_OOS_WARM, warm_from)),
        gate=wan350.LONG_ARM,
        confluence_params=arm.params(),
        backtest_config=cfg,
        order_block_result=ob_result,
        iterations=1,
        seed=wan350.BOOTSTRAP_SEED,
        funding_rates=market.funding_rates,
        pool_params=arm.pool_params(),
    )
    full = results[wan350.SEGMENT_FULL]
    warm = results[wan350.SEGMENT_OOS_WARM]
    assert 0 < warm.real_num_trades < full.real_num_trades
    assert 0 < warm.pool_size < full.pool_size  # 풀에도 똑같이 걸린다(대칭)
