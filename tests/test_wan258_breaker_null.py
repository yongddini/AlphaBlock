"""backtest.wan258_breaker_null 테스트 (WAN-258).

이 파일이 동작으로 고정하는 것:

1. **브레이커 진입 = 무효화 봉 · 돌파 방향(OB 반대)** — 불리시 OB 하향 돌파 → 숏(손절=top),
   베어리시 OB 상향 돌파 → 롱(손절=bottom). 무효화 안 된 OB는 셋업이 없다.
2. **진입점 A/B** — A는 무효화 봉 종가, B는 다음 봉 시가.
3. **청산 규칙** — `_walk_exit` 손절/익절/미청산, 같은 봉 동시 도달은 보수적 손절.
4. **ATR 룩어헤드 없음** — `atr[i]`가 `[i-length+1, i]`만 본다.
5. **테이커 진입** — 진입가에 슬리피지(재탭=메이커와 다름).
6. **단일 포지션 시퀀싱** — 겹치는 셋업 스킵.
7. **널: `atr` 퇴화(생략) · `ob` 비퇴화(p 계산)**.
8. **따뜻한 OOS 필터**.
9. **활주로**: `_runway_atr`(진행 방향 가장 가까운 반대 존 · 클리핑 · 뚫린 존 제외),
   필터 팔은 base 팔의 부분집합.
10. **CSV 왕복 재현**.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest.models import ExitReason, PositionSide
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan248_zone_position_null import make_fake_result
from backtest.wan258_breaker_null import (
    ENTRY_A_CLOSE,
    ENTRY_B_OPEN,
    STOP_ATR,
    STOP_OB,
    BreakerRow,
    _Arrays,
    _arrays_from_frame,
    _atr_series,
    _breaker_trade,
    _BreakerSetup,
    _pearson,
    _runway_atr,
    _walk_exit,
    build_breaker_setups,
    retap_compare_table,
    rows_from_csv,
    rows_to_frame,
    run_null,
    sequence_breaker,
)
from common.costs import Liquidity
from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
)
from strategy.order_blocks import OrderBlockDetector


def _arrays_from(
    times: list[int], o: list[float], h: list[float], low: list[float], c: list[float]
) -> _Arrays:
    frame = pd.DataFrame(
        {"open_time": times, "open": o, "high": h, "low": low, "close": c, "closed": True}
    )
    return _arrays_from_frame(frame)


def _ob(
    direction: OrderBlockDirection,
    *,
    top: float,
    bottom: float,
    confirmed_time: int,
    break_time: int | None,
) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=confirmed_time,
        confirmed_time=confirmed_time,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=break_time is not None,
        break_time=break_time,
    )


# --------------------------------------------------------------------------- #
# 1 + 2. 브레이커 방향·손절·진입점
# --------------------------------------------------------------------------- #


def test_bullish_break_down_is_short_stop_top() -> None:
    """불리시 OB(지지) 하향 돌파 → 숏 · 손절 = 존 상단(top) 재탈환. 1R = 존 높이."""
    times = [0, 1, 2, 3, 4]
    # bar 2에 무효화(break_time=2), 진입 close[2]=90(존 아래).
    arrays = _arrays_from(
        times,
        [100, 98, 96, 90, 80],
        [101, 99, 97, 91, 81],
        [99, 96, 88, 80, 70],
        [98, 96, 90, 82, 72],
    )
    ob = _ob(OrderBlockDirection.BULLISH, top=100, bottom=95, confirmed_time=0, break_time=2)
    cfg = harness.build_config("1h")
    setups = build_breaker_setups(
        [ob], arrays, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_OB, direction="both", cfg=cfg
    )
    assert len(setups) == 1
    s = setups[0]
    assert s.side is PositionSide.SHORT
    assert s.entry_time == 2
    assert s.entry_price == 90.0  # 무효화 봉 종가.
    assert s.stop_price == 100.0  # 존 상단 재탈환.


def test_bearish_break_up_is_long_stop_bottom() -> None:
    """베어리시 OB(저항) 상향 돌파 → 롱 · 손절 = 존 하단(bottom) 재탈환."""
    times = [0, 1, 2, 3, 4]
    arrays = _arrays_from(
        times,
        [100, 102, 104, 110, 120],
        [101, 103, 112, 120, 130],
        [99, 101, 103, 109, 119],
        [102, 104, 110, 118, 128],
    )
    ob = _ob(OrderBlockDirection.BEARISH, top=105, bottom=100, confirmed_time=0, break_time=2)
    cfg = harness.build_config("1h")
    setups = build_breaker_setups(
        [ob], arrays, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_OB, direction="both", cfg=cfg
    )
    assert len(setups) == 1
    s = setups[0]
    assert s.side is PositionSide.LONG
    assert s.entry_price == 110.0
    assert s.stop_price == 100.0  # 존 하단.


def test_unbroken_ob_has_no_setup() -> None:
    times = [0, 1, 2, 3]
    arrays = _arrays_from(times, [100] * 4, [101] * 4, [99] * 4, [100] * 4)
    ob = _ob(OrderBlockDirection.BULLISH, top=100, bottom=95, confirmed_time=0, break_time=None)
    cfg = harness.build_config("1h")
    setups = build_breaker_setups(
        [ob], arrays, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_OB, direction="both", cfg=cfg
    )
    assert setups == []


def test_direction_filter_and_entry_point_b() -> None:
    times = [0, 1, 2, 3, 4]
    arrays = _arrays_from(
        times,
        [100, 98, 96, 90, 80],
        [101, 99, 97, 91, 81],
        [99, 96, 88, 80, 70],
        [98, 96, 90, 82, 72],
    )
    ob = _ob(OrderBlockDirection.BULLISH, top=100, bottom=95, confirmed_time=0, break_time=2)
    cfg = harness.build_config("1h")
    # 숏 방향 필터: 불리시 돌파는 숏이므로 통과.
    assert build_breaker_setups(
        [ob], arrays, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_OB, direction="short", cfg=cfg
    )
    # 롱 방향 필터: 이 셋업은 숏이라 빠진다.
    assert (
        build_breaker_setups(
            [ob], arrays, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_OB, direction="long", cfg=cfg
        )
        == []
    )
    # B_open: 진입 = 다음 봉(pos 3) 시가.
    b = build_breaker_setups(
        [ob], arrays, entry_point=ENTRY_B_OPEN, stop_variant=STOP_OB, direction="short", cfg=cfg
    )
    assert b[0].entry_time == 3
    assert b[0].entry_price == 90.0  # opens[3].


def test_atr_stop_is_zone_independent() -> None:
    times = [0, 1, 2, 3, 4]
    arrays = _arrays_from(
        times,
        [100, 98, 96, 90, 80],
        [101, 99, 97, 91, 81],
        [99, 96, 88, 80, 70],
        [98, 96, 90, 82, 72],
    )
    ob = _ob(OrderBlockDirection.BULLISH, top=100, bottom=95, confirmed_time=0, break_time=2)
    cfg = harness.build_config("1h")
    s = build_breaker_setups(
        [ob], arrays, entry_point=ENTRY_A_CLOSE, stop_variant=STOP_ATR, direction="short", cfg=cfg
    )[0]
    # 숏 atr 손절 = 진입가 + 1.5·ATR(존 top과 무관).
    from backtest.wan258_breaker_null import STOP_ATR_MULT

    expected = 90.0 + STOP_ATR_MULT * arrays.atr[2]
    assert math.isclose(s.stop_price, expected)


# --------------------------------------------------------------------------- #
# 3. 청산 규칙 (숏 방향)
# --------------------------------------------------------------------------- #


def test_walk_exit_short_take_profit() -> None:
    times = [0, 1, 2]
    # 숏 진입 100, 손절 110(1R=10) → tp = 85. 다음 봉 저가 84 → 익절.
    arrays = _arrays_from(times, [100, 100, 100], [101, 101, 101], [99, 84, 84], [100, 90, 90])
    t, price, reason, gross = _walk_exit(
        is_long=False, entry_price=100, stop=110, start_idx=1, arrays=arrays, tp_r=1.5
    )
    assert reason is ExitReason.TAKE_PROFIT
    assert price == 85.0
    assert gross == 1.5
    assert t == 1


def test_walk_exit_same_bar_conservative_stop_short() -> None:
    times = [0, 1]
    arrays = _arrays_from(times, [100, 100], [101, 111], [99, 84], [100, 100])
    _t, _price, reason, gross = _walk_exit(
        is_long=False, entry_price=100, stop=110, start_idx=1, arrays=arrays, tp_r=1.5
    )
    assert reason is ExitReason.STOP_LOSS
    assert gross == -1.0


# --------------------------------------------------------------------------- #
# 4. ATR 룩어헤드 없음
# --------------------------------------------------------------------------- #


def test_atr_series_uses_only_up_to_i() -> None:
    highs = [10.0, 12.0, 14.0, 40.0]
    lows = [8.0, 9.0, 11.0, 20.0]
    closes = [9.0, 11.0, 13.0, 30.0]
    atr_full = _atr_series(highs, lows, closes, length=2)
    atr_prefix = _atr_series(highs[:3], lows[:3], closes[:3], length=2)
    assert atr_full[:3] == atr_prefix


# --------------------------------------------------------------------------- #
# 5. 테이커 진입
# --------------------------------------------------------------------------- #


def _setup(side: PositionSide, entry: float, stop: float, exit_t: int) -> _BreakerSetup:
    return _BreakerSetup(
        side=side,
        entry_time=0,
        entry_price=entry,
        stop_price=stop,
        exit_time=exit_t,
        exit_price=entry * (0.9 if side is PositionSide.SHORT else 1.1),
        reason=ExitReason.TAKE_PROFIT,
        gross_r=1.5,
        runway_atr=math.inf,
        atr_pct=0.02,
    )


def test_breaker_entry_is_taker() -> None:
    cfg = harness.build_config("1h")
    seq = _breaker_trade(
        _setup(PositionSide.SHORT, 100.0, 110.0, 10), cfg.initial_capital, cfg, None
    )
    assert seq is not None
    taker_entry = cfg.cost_model.entry_fill(100.0, is_long=False, liquidity=Liquidity.TAKER)
    assert seq.trade.entry_price == taker_entry
    assert taker_entry < 100.0  # 숏 진입은 슬리피지로 아래로 밀린다.


# --------------------------------------------------------------------------- #
# 6. 단일 포지션 시퀀싱
# --------------------------------------------------------------------------- #


def test_single_position_sequencing_skips_overlap() -> None:
    cfg = harness.build_config("1h")

    def mk(entry_time: int, exit_time: int) -> _BreakerSetup:
        return _BreakerSetup(
            side=PositionSide.SHORT,
            entry_time=entry_time,
            entry_price=100.0,
            stop_price=110.0,
            exit_time=exit_time,
            exit_price=90.0,
            reason=ExitReason.TAKE_PROFIT,
            gross_r=1.5,
            runway_atr=math.inf,
            atr_pct=0.02,
        )

    setups = [mk(0, 100), mk(50, 60), mk(100, 110)]
    trades = sequence_breaker(setups, cfg, None)
    assert [s.trade.entry_time for s in trades] == [0, 100]


# --------------------------------------------------------------------------- #
# 9. 활주로
# --------------------------------------------------------------------------- #


def test_runway_short_finds_support_below() -> None:
    traded = _ob(OrderBlockDirection.BULLISH, top=100, bottom=95, confirmed_time=0, break_time=2)
    wall = _ob(OrderBlockDirection.BULLISH, top=80, bottom=75, confirmed_time=1, break_time=None)
    above = _ob(OrderBlockDirection.BULLISH, top=95, bottom=90, confirmed_time=1, break_time=None)
    runway = _runway_atr(
        traded=traded,
        is_long=False,
        entry_price=90.0,
        entry_time=2,
        atr_val=5.0,
        all_obs=[traded, wall, above],
    )
    assert math.isclose(runway, (90.0 - 80.0) / 5.0)  # 아래 첫 지지의 top까지.


def test_runway_no_wall_is_inf_and_excludes_broken_future() -> None:
    traded = _ob(OrderBlockDirection.BULLISH, top=100, bottom=95, confirmed_time=0, break_time=2)
    broken = _ob(OrderBlockDirection.BULLISH, top=80, bottom=75, confirmed_time=1, break_time=2)
    future = _ob(OrderBlockDirection.BULLISH, top=80, bottom=75, confirmed_time=9, break_time=None)
    runway = _runway_atr(
        traded=traded,
        is_long=False,
        entry_price=90.0,
        entry_time=2,
        atr_val=5.0,
        all_obs=[traded, broken, future],
    )
    assert runway == math.inf  # 뚫린 존·미래 존은 벽이 아니다.


def test_runway_filter_is_subset() -> None:
    _df, result, arrays = _detect_obs()
    cfg = harness.build_config("1h")
    base = build_breaker_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_OB,
        direction="both",
        cfg=cfg,
        compute_runway=True,
        all_obs=result.order_blocks,
    )
    assert base
    filtered = [s for s in base if s.runway_atr >= 2.0]
    assert len(filtered) <= len(base)
    assert all(s.runway_atr >= 2.0 for s in filtered)


def test_pearson_basic() -> None:
    assert _pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)
    assert _pearson([1.0, 2.0], [1.0, 2.0]) is None  # 표본 부족.


# --------------------------------------------------------------------------- #
# 7 + 8 + 10. 통합 · CSV 왕복
# --------------------------------------------------------------------------- #


def _detect_obs(seed: int = 11, bars: int = 1500) -> tuple[pd.DataFrame, OrderBlockResult, _Arrays]:
    df = make_synthetic_ohlcv(bars=bars, seed=seed)
    result = OrderBlockDetector(OrderBlockParams(combine_obs=False)).run(df)
    arrays = _arrays_from_frame(df)
    return df, result, arrays


def test_atr_degenerate_ob_nondegenerate_synthetic() -> None:
    """`atr` 손절은 존 경계와 무관하나 브레이커는 진입가(무효화 시각)가 위치에 따라 달라진다 —
    그래도 이슈 정책상 널은 `ob`에만(비퇴화), `atr`은 생략. 여기선 `ob` 가짜 존이 실제와 다른
    손절가를 냄(널 비퇴화 근거)을 고정한다."""
    df = make_synthetic_ohlcv(bars=1500, seed=11)
    ob_params = OrderBlockParams(combine_obs=False)
    real = OrderBlockDetector(ob_params).run(df)
    arrays = _arrays_from_frame(df)
    cfg = harness.build_config("1h")
    fake = make_fake_result(real, df, ob_params, rng=random.Random(1), pool_k=4)
    assert fake.order_blocks

    def _setups(obs: Sequence[OrderBlock]) -> list[_BreakerSetup]:
        return build_breaker_setups(
            obs, arrays, entry_point=ENTRY_B_OPEN, stop_variant=STOP_OB, direction="both", cfg=cfg
        )

    real_ob: dict[int, list[float]] = {}
    for s in _setups(real.order_blocks):
        real_ob.setdefault(s.entry_time, []).append(s.stop_price)
    differs = any(
        s.stop_price not in real_ob.get(s.entry_time, []) for s in _setups(fake.order_blocks)
    )
    assert differs, "ob 손절 가짜 존은 손절가가 실제와 달라야 한다(널 비퇴화)."


def test_integration_null_degeneracy_and_roundtrip(tmp_path: Path) -> None:
    probe = harness.load_market_data(
        harness.normalize_symbol("BTCUSDT"),
        "1h",
        start_ms=0,
        end_ms=None,
        need_1m=False,
        funding=False,
    )
    if probe.empty:
        pytest.skip("실데이터(data/ohlcv.db) 없음 — 통합 실행은 로컬에서만(CI 빈 DB).")
    rows = run_null(
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        segments=[harness.SEGMENT_IS, harness.SEGMENT_OOS_WARM],
        directions=["short", "both"],
        entry_points=[ENTRY_B_OPEN],
        stop_variants=[STOP_OB, STOP_ATR],
        runway_atrs=[None, 2.0],
        start="2023-01-01",
        end="2024-12-31",
        iterations=40,
        with_retap=False,
        jobs=1,
        log=False,
    )
    assert rows
    base_ob = [r for r in rows if r.stop_variant == STOP_OB and r.runway_atr is None]
    atr_rows = [r for r in rows if r.stop_variant == STOP_ATR]
    filtered = [r for r in rows if r.runway_atr is not None]
    # ob base: 위치가 1R에 들어가 널 비퇴화 → p 계산.
    assert any(r.random_p_value is not None for r in base_ob)
    # atr: 위치 널 생략 → p None.
    assert all(r.random_p_value is None for r in atr_rows)
    # 필터 팔: 문턱 설정 · 널 생략 · 거래 수 ≤ base.
    assert filtered and all(r.runway_atr == 2.0 for r in filtered)
    assert all(r.random_p_value is None for r in filtered)

    # CSV 왕복 재현(부동소수 끝자리 제외 · 문자열·None은 정확).
    frame = rows_to_frame(rows)
    csv = tmp_path / "wan258.csv"
    frame.to_csv(csv, index=False)
    restored = rows_from_csv(csv)
    assert len(restored) == len(rows)
    a = restored[0].model_dump()
    b = rows[0].model_dump()
    assert a.keys() == b.keys()
    for key, bv in b.items():
        av = a[key]
        if isinstance(bv, float) and av is not None:
            assert math.isclose(av, bv, rel_tol=1e-9, abs_tol=1e-12), key
        else:
            assert av == bv, key


def test_warm_oos_filters_early_setups() -> None:
    _df, result, arrays = _detect_obs()
    cfg = harness.build_config("1h")
    from backtest.wan258_breaker_null import _filter_eval_setups

    setups = build_breaker_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_OB,
        direction="both",
        cfg=cfg,
    )
    assert setups
    mid = arrays.times[len(arrays.times) // 2]
    warm = _filter_eval_setups(setups, mid)
    assert all(s.entry_time >= mid for s in warm)
    assert len(warm) < len(setups)


def _row(
    *,
    timeframe: str,
    entry_point: str = ENTRY_B_OPEN,
    stop_variant: str = STOP_OB,
    runway_atr: float | None = None,
    real_total_return: float,
    retap_total_return: float | None,
) -> BreakerRow:
    return BreakerRow(
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        segment="is",
        direction="short",
        entry_point=entry_point,
        stop_variant=stop_variant,
        runway_atr=runway_atr,
        real_total_return=real_total_return,
        real_num_trades=30,
        real_mean_net_r=0.05,
        real_max_drawdown=0.1,
        real_win_rate=0.5,
        pool_size=10,
        fake_zones=100,
        random_mean_return=0.0,
        random_ci_low=-0.1,
        random_ci_high=0.1,
        random_p_value=0.5,
        iterations=200,
        retap_total_return=retap_total_return,
        retap_num_trades=None if retap_total_return is None else 40,
        runway_density_corr=None,
        buy_hold=0.5,
    )


def test_retap_compare_table_aggregates_primary_and_excludes_15m() -> None:
    rows = [
        _row(timeframe="1h", real_total_return=0.05, retap_total_return=0.80),
        _row(
            timeframe="1h", entry_point=ENTRY_A_CLOSE, real_total_return=0.9, retap_total_return=0.1
        ),
        _row(timeframe="1h", stop_variant=STOP_ATR, real_total_return=0.9, retap_total_return=0.1),
        _row(timeframe="1h", runway_atr=2.0, real_total_return=0.9, retap_total_return=0.1),
        _row(timeframe="15m", real_total_return=0.9, retap_total_return=None),
    ]
    table = retap_compare_table(rows)
    lines = [ln for ln in table.splitlines() if ln.startswith("| 1h")]
    assert len(lines) == 1  # 주 설정 B_open/ob/base 한 행만.
    assert "15m" not in table
    assert "0/1" in lines[0]  # 브레이커 5% < 재탭 80%.


def test_row_nan_to_none_roundtrip() -> None:
    row = _row(
        timeframe="1h", stop_variant=STOP_ATR, real_total_return=0.01, retap_total_return=None
    )
    frame = rows_to_frame([row])
    restored = BreakerRow.model_validate(frame.to_dict(orient="records")[0])
    assert restored.retap_total_return is None
