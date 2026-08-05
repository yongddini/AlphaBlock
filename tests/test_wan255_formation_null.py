"""backtest.wan255_formation_null 테스트 (WAN-255 §2).

이 파일이 동작으로 고정하는 것:

1. **전량 진입(룩어헤드 없음)** — 되돌아온/안된 OB를 가리지 않고 **모든** 유효 OB에 형성
   셋업이 생긴다(설계 함정 1). 분해는 사후 전용.
2. **청산 규칙** — `_walk_exit`이 손절 우선/익절 우선/미청산을 정확히 가르고, 같은 봉 동시
   도달은 **보수적으로 손절**.
3. **ATR 자에 룩어헤드가 없다** — `atr[i]`가 `[i-length+1, i]`만 본다.
4. **진입이 테이커다** — 형성 진입가에 슬리피지가 붙는다(재탭=메이커와 다름 · 설계 함정 4).
5. **단일 포지션 시퀀싱** — 겹치는 셋업은 스킵.
6. **널이 `atr`에서 퇴화, `ob`에서 비퇴화** — 위치가 손익에 들어갈 때만 실제≠가짜.
7. **방향 필터** — long → 롱만, short → 숏만.
8. **따뜻한 OOS 필터** — 진입 시각이 경계 이전인 셋업은 빠진다.
9. **CSV 왕복 재현** — `rows_from_csv`가 저장 전 행과 같다.
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
from backtest.wan255_formation_null import (
    ENTRY_A_CLOSE,
    ENTRY_B_OPEN,
    STOP_ATR,
    STOP_OB,
    FormationRow,
    _Arrays,
    _arrays_from_frame,
    _atr_series,
    _formation_trade,
    _FormationSetup,
    _walk_exit,
    build_formation_setups,
    retap_compare_table,
    rows_from_csv,
    rows_to_frame,
    run_null,
    sequence_formation,
)
from common.costs import Liquidity
from strategy.models import OrderBlock, OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector


def _arrays_from(
    times: list[int], o: list[float], h: list[float], low: list[float], c: list[float]
) -> _Arrays:
    frame = pd.DataFrame(
        {"open_time": times, "open": o, "high": h, "low": low, "close": c, "closed": True}
    )
    return _arrays_from_frame(frame)


# --------------------------------------------------------------------------- #
# 2. 청산 규칙
# --------------------------------------------------------------------------- #


def test_walk_exit_take_profit_first() -> None:
    # 진입 100, 손절 90(1R=10) → tp = 115. 다음 봉에 고가 116 → 익절.
    times = [0, 1, 2]
    arrays = _arrays_from(times, [100, 100, 100], [101, 116, 120], [99, 99, 99], [100, 110, 110])
    t, price, reason, gross = _walk_exit(
        is_long=True, entry_price=100, stop=90, start_idx=1, arrays=arrays, tp_r=1.5
    )
    assert reason is ExitReason.TAKE_PROFIT
    assert price == 115.0
    assert gross == 1.5
    assert t == 1


def test_walk_exit_stop_first() -> None:
    times = [0, 1, 2]
    arrays = _arrays_from(times, [100, 100, 100], [101, 101, 101], [99, 89, 89], [100, 95, 95])
    t, price, reason, gross = _walk_exit(
        is_long=True, entry_price=100, stop=90, start_idx=1, arrays=arrays, tp_r=1.5
    )
    assert reason is ExitReason.STOP_LOSS
    assert price == 90.0
    assert gross == -1.0


def test_walk_exit_same_bar_is_conservative_stop() -> None:
    # 같은 봉에 손절(89)·익절(116) 둘 다 닿음 → 보수적으로 손절.
    times = [0, 1]
    arrays = _arrays_from(times, [100, 100], [101, 116], [99, 89], [100, 100])
    _t, _price, reason, gross = _walk_exit(
        is_long=True, entry_price=100, stop=90, start_idx=1, arrays=arrays, tp_r=1.5
    )
    assert reason is ExitReason.STOP_LOSS
    assert gross == -1.0


def test_walk_exit_end_of_data_partial() -> None:
    times = [0, 1, 2]
    arrays = _arrays_from(times, [100, 100, 100], [101, 105, 108], [99, 99, 99], [100, 105, 106])
    _t, price, reason, gross = _walk_exit(
        is_long=True, entry_price=100, stop=90, start_idx=1, arrays=arrays, tp_r=1.5
    )
    assert reason is ExitReason.END_OF_DATA
    assert price == 106.0
    assert math.isclose(gross, (106 - 100) / 10)


# --------------------------------------------------------------------------- #
# 3. ATR 룩어헤드 없음
# --------------------------------------------------------------------------- #


def test_atr_series_uses_only_up_to_i() -> None:
    highs = [10.0, 12.0, 14.0, 40.0]
    lows = [8.0, 9.0, 11.0, 20.0]
    closes = [9.0, 11.0, 13.0, 30.0]
    atr_full = _atr_series(highs, lows, closes, length=2)
    # 마지막 봉의 큰 TR을 앞 봉이 미리 알면 안 된다 → 앞 3개는 4번째 없이 계산한 값과 동일.
    atr_prefix = _atr_series(highs[:3], lows[:3], closes[:3], length=2)
    assert atr_full[:3] == atr_prefix


# --------------------------------------------------------------------------- #
# 1 + 7. 전량 진입 · 방향 필터
# --------------------------------------------------------------------------- #


def _detect_obs(seed: int = 11, bars: int = 1200) -> tuple[pd.DataFrame, OrderBlockResult, _Arrays]:
    df = make_synthetic_ohlcv(bars=bars, seed=seed)
    result = OrderBlockDetector(OrderBlockParams(combine_obs=False)).run(df)
    arrays = _arrays_from_frame(df)
    return df, result, arrays


def test_full_population_no_lookahead() -> None:
    _df, result, arrays = _detect_obs()
    cfg = harness.build_config("1h")
    setups = build_formation_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_OB,
        direction="both",
        cfg=cfg,
    )
    # 되돌아온·안된 OB가 둘 다 셋업이 된다(사후 분해 전용) — retraced로 거르지 않았음을 확인.
    assert any(s.retraced for s in setups)
    assert any(not s.retraced for s in setups)
    # 방향 필터: long은 롱만.
    long_setups = build_formation_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_OB,
        direction="long",
        cfg=cfg,
    )
    assert long_setups
    assert all(s.side is PositionSide.LONG for s in long_setups)


def test_rsi_gate_filters_and_is_subset() -> None:
    """RSI 게이트(롱<30·숏>70)는 무게이트의 부분집합이고, 통과 셋업은 직전 봉 임계값을
    만족한다(룩어헤드 없음). 형성=모멘텀이라 대개 크게 걸러진다(0에 수렴)."""
    from backtest.wan255_formation_null import RSI_LONG_MAX

    _df, result, arrays = _detect_obs()
    cfg = harness.build_config("1h")
    off = build_formation_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_OB,
        direction="long",
        cfg=cfg,
        rsi_gate=False,
    )
    on = build_formation_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_OB,
        direction="long",
        cfg=cfg,
        rsi_gate=True,
    )
    assert off, "게이트 없는 롱 셋업이 있어야 비교가 성립한다."
    off_times = {s.entry_time for s in off}
    assert {s.entry_time for s in on} <= off_times  # 부분집합.
    assert len(on) <= len(off)
    # 통과한 롱 셋업은 진입 직전 봉(entry_time 위치 −1) RSI<30을 만족한다.
    for s in on:
        gate_idx = arrays.pos_by_time[s.entry_time] - 1
        assert arrays.rsi[gate_idx] < RSI_LONG_MAX


def test_entry_point_a_uses_confirm_close_b_uses_next_open() -> None:
    _df, result, arrays = _detect_obs()
    cfg = harness.build_config("1h")
    a = build_formation_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_A_CLOSE,
        stop_variant=STOP_OB,
        direction="long",
        cfg=cfg,
    )
    b = build_formation_setups(
        result.order_blocks,
        arrays,
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_OB,
        direction="long",
        cfg=cfg,
    )
    # A와 B는 같은 OB에서 서로 다른 진입가/시각을 낸다(다음 봉이 존재하는 한).
    assert a and b
    a_by = {s.stop_price: s for s in a}
    shared = [s for s in b if s.stop_price in a_by]
    assert shared
    changed = [s for s in shared if a_by[s.stop_price].entry_time != s.entry_time]
    assert changed, "A(종가)와 B(다음 시가)의 진입 시각이 전부 같을 수 없다."


# --------------------------------------------------------------------------- #
# 4. 테이커 진입 (재탭=메이커와 다름)
# --------------------------------------------------------------------------- #


def test_formation_entry_is_taker() -> None:
    from backtest.wan255_formation_null import _FormationSetup

    cfg = harness.build_config("1h")
    setup = _FormationSetup(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        stop_price=90.0,
        exit_time=10,
        exit_price=115.0,
        reason=ExitReason.TAKE_PROFIT,
        gross_r=1.5,
        retraced=False,
    )
    seq = _formation_trade(setup, cfg.initial_capital, cfg, None)
    assert seq is not None
    # 테이커 진입 체결가 = 슬리피지 포함 > 원가(롱은 위로 밀림). 메이커면 원가 그대로였을 것.
    taker_entry = cfg.cost_model.entry_fill(100.0, is_long=True, liquidity=Liquidity.TAKER)
    assert seq.trade.entry_price == taker_entry
    assert taker_entry > 100.0


# --------------------------------------------------------------------------- #
# 5. 단일 포지션 시퀀싱
# --------------------------------------------------------------------------- #


def test_single_position_sequencing_skips_overlap() -> None:
    from backtest.wan255_formation_null import _FormationSetup

    cfg = harness.build_config("1h")

    def mk(entry_time: int, exit_time: int) -> _FormationSetup:
        return _FormationSetup(
            side=PositionSide.LONG,
            entry_time=entry_time,
            entry_price=100.0,
            stop_price=90.0,
            exit_time=exit_time,
            exit_price=115.0,
            reason=ExitReason.TAKE_PROFIT,
            gross_r=1.5,
            retraced=False,
        )

    # 셋업 1: [0, 100) · 셋업 2: [50, 60)(겹침, 스킵) · 셋업 3: [100, 110)(허용).
    setups = [mk(0, 100), mk(50, 60), mk(100, 110)]
    trades = sequence_formation(setups, cfg, None)
    entry_times = [s.trade.entry_time for s in trades]
    assert entry_times == [0, 100], entry_times


# --------------------------------------------------------------------------- #
# 6 + 8 + 9. 통합: 널 퇴화/비퇴화 · 따뜻한 OOS · CSV 왕복
# --------------------------------------------------------------------------- #


def test_atr_degenerate_ob_nondegenerate_synthetic() -> None:
    """핵심 설계 주장(DB 없이 합성으로 고정): `atr` 손절은 위치 무관이라 실제 셋업 == 가짜
    셋업(널 퇴화), `ob` 손절은 존 경계라 가짜 위치가 손절가를 바꾼다(널 비퇴화)."""
    df = make_synthetic_ohlcv(bars=1200, seed=11)
    ob_params = OrderBlockParams(combine_obs=False)
    real = OrderBlockDetector(ob_params).run(df)
    arrays = _arrays_from_frame(df)
    cfg = harness.build_config("1h")
    fake = make_fake_result(real, df, ob_params, rng=random.Random(1), pool_k=4)
    assert fake.order_blocks, "합성 데이터가 가짜 존을 만들지 못했다 — 시나리오 점검."

    def _setups(obs: Sequence[OrderBlock], stop_variant: str) -> list[_FormationSetup]:
        return build_formation_setups(
            obs,
            arrays,
            entry_point=ENTRY_B_OPEN,
            stop_variant=stop_variant,
            direction="both",
            cfg=cfg,
        )

    # atr: 같은 진입시각의 실제·가짜 셋업이 손절가·청산까지 동일(위치가 손익에 안 들어감).
    real_atr = {s.entry_time: s for s in _setups(real.order_blocks, STOP_ATR)}
    matched = [s for s in _setups(fake.order_blocks, STOP_ATR) if s.entry_time in real_atr]
    assert matched, "atr 셋업이 confirmed_time을 공유해야 한다."
    for s in matched:
        r = real_atr[s.entry_time]
        assert s.stop_price == r.stop_price
        assert s.exit_time == r.exit_time
        assert s.gross_r == r.gross_r

    # ob: 가짜(무작위 위치) 존은 손절가가 실제와 다르다(위치 정보가 1R에 들어감).
    real_ob: dict[int, list[float]] = {}
    for s in _setups(real.order_blocks, STOP_OB):
        real_ob.setdefault(s.entry_time, []).append(s.stop_price)
    differs = any(
        s.stop_price not in real_ob.get(s.entry_time, [])
        for s in _setups(fake.order_blocks, STOP_OB)
    )
    assert differs, "ob 손절 가짜 존은 손절가가 실제와 달라야 한다(널 비퇴화 근거)."


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
        directions=["long", "both"],
        entry_points=[ENTRY_B_OPEN],
        stop_variants=[STOP_OB, STOP_ATR],
        start="2024-01-01",
        end="2024-12-31",
        iterations=40,
        with_retap=False,
        jobs=1,
        log=False,
    )
    assert rows
    ob_rows = [r for r in rows if r.stop_variant == STOP_OB]
    atr_rows = [r for r in rows if r.stop_variant == STOP_ATR]
    # ob 손절: 위치가 1R에 들어가 널이 비퇴화 → p가 계산된다.
    assert any(r.random_p_value is not None for r in ob_rows)
    # atr 손절: 위치 무관 → 널 퇴화(생략) → p None.
    assert all(r.random_p_value is None for r in atr_rows)
    # 분해 합 = 거래 수.
    for r in rows:
        assert r.returned_n + r.never_n == r.real_num_trades

    # CSV 왕복 재현.
    frame = rows_to_frame(rows)
    csv = tmp_path / "wan255.csv"
    frame.to_csv(csv, index=False)
    restored = rows_from_csv(csv)
    assert len(restored) == len(rows)
    # 부동소수 끝자리(≈1e-16)를 빼면 왕복 재현(WAN-151 패턴 · 문자열·None 필드는 정확 일치).
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
    from backtest.wan255_formation_null import _filter_eval_setups

    setups = build_formation_setups(
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


def test_row_nan_to_none_roundtrip() -> None:
    # atr 행은 p/무작위평균이 None(NaN) — CSV 왕복에서 None으로 되돌아와야 한다.
    row = FormationRow(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="is",
        direction="long",
        entry_point=ENTRY_B_OPEN,
        stop_variant=STOP_ATR,
        real_total_return=0.01,
        real_num_trades=30,
        real_mean_net_r=0.05,
        real_max_drawdown=0.1,
        returned_n=25,
        returned_mean_net_r=0.04,
        never_n=5,
        never_mean_net_r=0.2,
        pool_size=0,
        fake_zones=100,
        random_mean_return=None,
        random_ci_low=None,
        random_ci_high=None,
        random_p_value=None,
        iterations=0,
        retap_total_return=None,
        retap_num_trades=None,
        buy_hold=0.5,
    )
    frame = rows_to_frame([row])
    restored = FormationRow.model_validate(frame.to_dict(orient="records")[0])
    assert restored.random_p_value is None
    assert restored.retap_total_return is None


def _retap_row(
    *,
    timeframe: str,
    entry_point: str = ENTRY_B_OPEN,
    stop_variant: str = STOP_OB,
    rsi_gate: bool = False,
    real_total_return: float,
    retap_total_return: float | None,
) -> FormationRow:
    return FormationRow(
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        segment="is",
        direction="long",
        entry_point=entry_point,
        stop_variant=stop_variant,
        rsi_gate=rsi_gate,
        real_total_return=real_total_return,
        real_num_trades=30,
        real_mean_net_r=0.05,
        real_max_drawdown=0.1,
        returned_n=25,
        returned_mean_net_r=0.04,
        never_n=5,
        never_mean_net_r=0.2,
        pool_size=10,
        fake_zones=100,
        random_mean_return=0.0,
        random_ci_low=-0.1,
        random_ci_high=0.1,
        random_p_value=0.5,
        iterations=200,
        retap_total_return=retap_total_return,
        retap_num_trades=None if retap_total_return is None else 40,
        buy_hold=0.5,
    )


def test_retap_compare_table_aggregates_primary_config_and_excludes_15m() -> None:
    """(b) 표는 주 설정(B_open/ob/nogate)만·(TF×구간×방향) 집계하고 retap 없는 15m은 뺀다."""
    rows = [
        # 주 설정 1h 롱 IS — 형성 < 재탭(형성>재탭 = 0/1)
        _retap_row(timeframe="1h", real_total_return=0.05, retap_total_return=0.80),
        # 다른 진입점(A_close)·손절(atr)·게이트(on)는 (b) 표에서 제외돼야 한다
        _retap_row(
            timeframe="1h",
            entry_point=ENTRY_A_CLOSE,
            real_total_return=0.99,
            retap_total_return=0.10,
        ),
        _retap_row(
            timeframe="1h", stop_variant=STOP_ATR, real_total_return=0.99, retap_total_return=0.10
        ),
        _retap_row(timeframe="1h", rsi_gate=True, real_total_return=0.99, retap_total_return=0.10),
        # 15m은 retap=None → (b) 표에서 빠진다(널 판정에는 남는다)
        _retap_row(timeframe="15m", real_total_return=0.99, retap_total_return=None),
    ]
    table = retap_compare_table(rows)
    lines = [ln for ln in table.splitlines() if ln.startswith("| 1h")]
    # 주 설정 1h 롱 IS 한 행만 남는다(A_close·atr·게이트·15m 전부 제외)
    assert len(lines) == 1
    assert "15m" not in table
    # 형성 5% vs 재탭 80% → 형성>재탭 0/1
    assert "0/1" in lines[0]


def test_retap_compare_table_empty_when_no_retap() -> None:
    rows = [_retap_row(timeframe="15m", real_total_return=0.05, retap_total_return=None)]
    assert "재탭 비교 없음" in retap_compare_table(rows)
