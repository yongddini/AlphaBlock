"""backtest.wan248_zone_position_null 테스트 (WAN-248).

이 파일이 지키는 것:

1. **가짜 존 무효화 규칙 = 탐지기와 비트 단위로 같다** — `recompute_lifecycle`이 실제 존의
   자기 기하를 넣으면 탐지기가 낸 `tapped_times`/`break_time`/`swept_time`을 그대로 재현
   한다(모듈의 핵심 계약 · 완료기준의 「같은 무효화 규칙」).
2. **가짜 존이 방향·존폭·빈도를 매칭하고 위치만 무작위다** — 공정한 대조의 정의.
3. **풀 생성이 결정적이다** — 같은 시드 → 같은 가짜 존(재현성).
4. **렌즈가 라벨이 아니라 파라미터다** — baseline 항등 · pen_5bp 관통 5bp.
5. **판정·유의성이 행에서 계산된다**(주의문이 아니라 코드).
6. **따뜻한 실제 팔 == `run_once(eval_from_ms)`**(실데이터 게이트) — OOS 규약이 정본과 같다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from backtest import harness
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan248_zone_position_null import (
    BASELINE_LENS,
    PEN_LENS,
    PositionNullRow,
    _arrays,
    eligible_rows,
    is_significant,
    lens_params,
    make_fake_result,
    recompute_lifecycle,
    significance_counts,
    verdict,
)
from strategy.models import ConfluenceParams, OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import OrderBlockDetector

# --------------------------------------------------------------------------- #
# 1. 무효화 규칙 재현 (핵심 계약)
# --------------------------------------------------------------------------- #


def _reproduce_check(zone_invalidation: Literal["wick", "close"]) -> None:
    params = OrderBlockParams(combine_obs=False, zone_invalidation=zone_invalidation)
    df = make_synthetic_ohlcv(bars=1200, seed=11)
    result = OrderBlockDetector(params).run(df)
    times, opens, highs, lows, closes = _arrays(df)
    use_wick = zone_invalidation == "wick"

    assert result.order_blocks, "합성 데이터가 존을 만들지 못했다 — 시나리오 점검."
    checked = 0
    for zone in result.order_blocks:
        tapped, break_time, swept_time = recompute_lifecycle(
            zone.direction,
            zone.top,
            zone.bottom,
            zone.confirmed_time,
            times=times,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            use_wick=use_wick,
        )
        assert tapped == zone.tapped_times, f"tapped 불일치: {zone.confirmed_time}"
        assert break_time == zone.break_time, f"break 불일치: {zone.confirmed_time}"
        assert swept_time == zone.swept_time, f"swept 불일치: {zone.confirmed_time}"
        checked += 1
    assert checked > 5, "재현을 확인한 존이 너무 적다."


def test_lifecycle_reproduces_detector_wick() -> None:
    _reproduce_check("wick")


def test_lifecycle_reproduces_detector_close() -> None:
    _reproduce_check("close")


def test_reject_fast_path_preserves_taps() -> None:
    """빠른 기각(접미 min/max)은 **탭(= 시그널의 근거)**을 절대 바꾸지 않는다.

    빠른 기각은 가격이 존을 아예 못 스치는 경우에만 발동하고, 그때 탭은 정의상 비어
    있으므로 시그널이 생기지 않는다(무효화 시각이 달라져도 탭 없는 존은 후보를 안 낸다).
    도달 가능한 존은 전 결과가 비트 단위로 같아야 한다.
    """
    df = make_synthetic_ohlcv(bars=800, seed=5)
    times, opens, highs, lows, closes = _arrays(df)
    n = len(times)
    suffix_max = [0.0] * n
    suffix_min = [0.0] * n
    run_max = float("-inf")
    run_min = float("inf")
    for i in range(n - 1, -1, -1):
        run_max = max(run_max, highs[i])
        run_min = min(run_min, lows[i])
        suffix_max[i] = run_max
        suffix_min[i] = run_min

    _Life = tuple[tuple[int, ...], int | None, int | None]

    def both(top: float, bottom: float) -> tuple[_Life, _Life]:
        confirmed = times[10]
        fast = recompute_lifecycle(
            OrderBlockDirection.BULLISH,
            top,
            bottom,
            confirmed,
            times=times,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            use_wick=True,
            suffix_max_high=suffix_max,
            suffix_min_low=suffix_min,
        )
        slow = recompute_lifecycle(
            OrderBlockDirection.BULLISH,
            top,
            bottom,
            confirmed,
            times=times,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            use_wick=True,
        )
        # 탭은 두 경로가 언제나 같다(빠른 기각의 안전 불변식).
        assert fast[0] == slow[0]
        return fast, slow

    # 도달 가능한 가운데 존: 전 결과 비트 일치.
    mid = (max(highs) + min(lows)) / 2.0
    fast_mid, slow_mid = both(mid * 1.01, mid * 0.99)
    assert fast_mid == slow_mid
    # 도달 불가한 먼 존: 탭은 둘 다 비어 있다(무효화 시각은 시그널과 무관).
    fast_far, _slow_far = both(max(highs) * 5.0, max(highs) * 4.9)
    assert fast_far[0] == ()


# --------------------------------------------------------------------------- #
# 2. 가짜 존이 방향·존폭·빈도를 매칭하고 위치만 무작위
# --------------------------------------------------------------------------- #


def _real_result_and_df() -> tuple[object, pd.DataFrame, OrderBlockParams]:
    params = OrderBlockParams()  # 채택 기본값(분리)
    df = make_synthetic_ohlcv(bars=1500, seed=3)
    result = OrderBlockDetector(params).run(df)
    return result, df, params


def test_fake_zones_match_geometry_and_frequency() -> None:
    import random

    result, df, ob = _real_result_and_df()
    pool_k = 5
    fake = make_fake_result(result, df, ob, rng=random.Random(1), pool_k=pool_k)  # type: ignore[arg-type]

    # 실제 존을 (방향, confirmed_time, 폭)으로 색인 — 가짜는 이 중 하나와 매칭돼야 한다.
    reals = result.order_blocks  # type: ignore[attr-defined]
    real_widths: dict[tuple[OrderBlockDirection, int], set[int]] = {}
    for z in reals:
        key = (z.direction, z.confirmed_time)
        real_widths.setdefault(key, set()).add(round((z.top - z.bottom) * 1e6))

    assert fake.order_blocks, "가짜 존이 비었다."
    positions_differ = 0
    for f in fake.order_blocks:
        key = (f.direction, f.confirmed_time)
        assert key in real_widths, "가짜 존의 (방향,확정시각)이 실제에 없다."
        assert round((f.top - f.bottom) * 1e6) in real_widths[key], "존폭이 실제와 다르다."
        # 대응 실제 존과 근단 위치가 다른 경우가 대부분이어야 한다(위치 무작위화).
        matching = [
            z for z in reals if z.direction == f.direction and z.confirmed_time == f.confirmed_time
        ]
        if all(abs(z.top - f.top) > 1e-9 for z in matching):
            positions_differ += 1
    # 무작위 위치라 상당수가 원본과 다른 근단을 가져야 한다.
    assert positions_differ > len(fake.order_blocks) // 2


def test_make_fake_result_deterministic() -> None:
    import random

    result, df, ob = _real_result_and_df()
    a = make_fake_result(result, df, ob, rng=random.Random(42), pool_k=4)  # type: ignore[arg-type]
    b = make_fake_result(result, df, ob, rng=random.Random(42), pool_k=4)  # type: ignore[arg-type]
    assert [(z.top, z.bottom, z.confirmed_time) for z in a.order_blocks] == [
        (z.top, z.bottom, z.confirmed_time) for z in b.order_blocks
    ]


# --------------------------------------------------------------------------- #
# 3. 렌즈
# --------------------------------------------------------------------------- #


def test_lens_params_baseline_is_identity() -> None:
    base = ConfluenceParams()
    out = lens_params(base, BASELINE_LENS)
    assert out.fill_penetration_bps == 0.0
    assert out.fill_dropout_rate == 0.0


def test_lens_params_pen5bp_sets_penetration() -> None:
    base = ConfluenceParams()
    out = lens_params(base, PEN_LENS)
    assert out.fill_penetration_bps == 5.0
    assert out.fill_dropout_rate == 0.0


# --------------------------------------------------------------------------- #
# 4. 판정·유의성
# --------------------------------------------------------------------------- #


def _row(**over: object) -> PositionNullRow:
    base: dict[str, object] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "segment": "oos_warm",
        "lens": BASELINE_LENS,
        "combine_obs": False,
        "real_total_return": 0.1,
        "real_num_trades": 40,
        "real_long": 40,
        "real_short": 0,
        "pool_size": 300,
        "real_zones": 50,
        "fake_zones": 250,
        "pool_k": 5,
        "random_mean_return": 0.02,
        "random_ci_low": -0.05,
        "random_ci_high": 0.09,
        "random_p_value": 0.03,
        "iterations": 200,
        "bucket_fallback_count": 0,
        "buy_hold": 0.5,
    }
    base.update(over)
    return PositionNullRow.model_validate(base)


def test_is_significant_requires_beating_random_mean() -> None:
    assert is_significant(_row(random_p_value=0.03, real_total_return=0.1, random_mean_return=0.02))
    # p 낮아도 실제가 무작위평균 이하면 유의 아님(하방 엣지는 채택 근거 아님).
    assert not is_significant(
        _row(random_p_value=0.01, real_total_return=-0.1, random_mean_return=0.02)
    )
    # 표본 부족은 유효 셀이 아니다.
    assert eligible_rows([_row(real_num_trades=5)]) == []


def test_verdict_branches() -> None:
    # 전부 유의 → (a)
    rows_a = [_row(symbol=f"S{i}", random_p_value=0.01) for i in range(3)]
    assert "(a)" in verdict(rows_a)
    # 아무도 유의 아님 → (b)
    rows_b = [_row(symbol=f"S{i}", random_p_value=0.9) for i in range(3)]
    assert "(b)" in verdict(rows_b)
    # 일부만 → (c)
    rows_c = [_row(symbol="S0", random_p_value=0.01), _row(symbol="S1", random_p_value=0.9)]
    assert "(c)" in verdict(rows_c)
    # 유효 셀 없음 → 판정 불가
    assert "판정 불가" in verdict([_row(real_num_trades=1)])


def test_significance_counts() -> None:
    rows = [_row(symbol="S0", random_p_value=0.01), _row(symbol="S1", random_p_value=0.9)]
    assert significance_counts(rows) == (1, 2)


# --------------------------------------------------------------------------- #
# 5. 따뜻한 실제 팔 == run_once(eval_from_ms) (실데이터 게이트)
# --------------------------------------------------------------------------- #

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"
_START = "2023-07-15"
_END = "2026-07-15"


def test_warm_real_arm_matches_run_once() -> None:
    """따뜻한 OOS의 실제 팔이 정본 `run_once(eval_from_ms)`와 총수익률이 같다.

    실데이터가 없으면(CI) skip. 있으면 위치 널의 실제 팔 = 채택 엔진 warm-OOS임을 못 박는다.
    """
    from backtest.wan89_short_autopsy import ARMS_BY_NAME
    from backtest.wan248_zone_position_null import run_position_null_segment

    market = harness.load_market_data(_SYMBOL, _TIMEFRAME, start_ms=_start_ms(), end_ms=_end_ms())
    if market.empty or market.df_1m.empty:
        import pytest

        pytest.skip("실데이터(data/ohlcv.db) 없음")

    eval_from = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    ob = harness.detect_order_blocks(market, OrderBlockParams())
    arm = ARMS_BY_NAME["long_only"]
    params = arm.params()
    cfg = arm.config(_TIMEFRAME)

    seg = run_position_null_segment(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        real_ob=ob,
        params=params,
        cfg=cfg,
        ob_params=OrderBlockParams(),
        funding_rates=market.funding_rates,
        eval_from_ms=eval_from,
        pool_k=2,
        iterations=10,
        pool_seed=1,
        bootstrap_seed=1,
    )
    outcome = harness.run_once(
        market, params=params, cfg=cfg, order_block_result=ob, eval_from_ms=eval_from
    )
    assert abs(seg.real_total_return - outcome.result.metrics.total_return) < 1e-9


def _start_ms() -> int:
    from backtest.run import parse_date_ms

    return parse_date_ms(_START)


def _end_ms() -> int:
    from backtest.run import parse_date_ms

    return parse_date_ms(_END)


# --------------------------------------------------------------------------- #
# 6. 산출 경로가 존재한다(요약 렌더 스모크)
# --------------------------------------------------------------------------- #


def test_summary_renders_from_rows(tmp_path: Path) -> None:
    from backtest.wan248_zone_position_null import build_summary_markdown

    rows = [
        _row(symbol="BTC/USDT:USDT", segment="is", random_p_value=0.9),
        _row(symbol="BTC/USDT:USDT", segment="oos_warm", random_p_value=0.01),
        _row(symbol="ETH/USDT:USDT", segment="oos_warm", lens=PEN_LENS, random_p_value=0.5),
    ]
    md = build_summary_markdown(rows)
    assert "WAN-248" in md
    assert "위치 축" in md
    (tmp_path / "s.md").write_text(md, encoding="utf-8")
