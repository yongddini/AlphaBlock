"""WAN-161 「문턱 × 배수」 격자 — 라벨이 아니라 **동작**으로 고정한다.

이 파일이 지키는 것:

1. **옛 핀을 물려받지 않는다**(WAN-152/155 패턴) — 격자 팔의 후보 집합이 오늘의 채택
   기본값(분리 존 · `intrabar_live` · `unconditional`)과 같고 병합 존(`LEGACY_OB_PARAMS`)과
   다르다.
2. **이중 필터가 없다** — 문턱은 이 모듈의 명시 입력이라 WAN-159가 `max_zone_width_atr`
   기본값을 `1.28`로 옮겨도 「필터 끔」 팔은 여전히 끔이고 `1.55` 팔은 여전히 1.55다.
   `harness.build_params`의 `None`은 규약상 「손대지 않는다」라 이 덮어쓰기가 없으면
   기본값 전환 이후 라벨만 남는다(WAN-91/95/112/123 부류의 조용한 실패).
3. **문턱이 실제로 걸린다** — 넓은 존은 조인 문턱에서 사라지고 느슨한 문턱에서는 산다.
4. **포함 관계 검산이 개수가 아니라 집합으로 돈다.**
5. **표본 게이트가 주의문이 아니라 코드다** — 20거래 미만 셀은 심볼평균에서 빠지고, 유효
   심볼 3개 미만 점은 최적 배수 곡선에서 빠지고, 비교할 문턱이 2개 미만이면 판정 불가다.
6. **판정 네 분기와 요약 필수 문장.**
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import LEGACY_OB_PARAMS, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan161_threshold_x_tp_multiple import (
    GUARD,
    MIN_SYMBOLS_FOR_VERDICT,
    MIN_TRADES_PER_SYMBOL,
    R_DEFAULT,
    R_MULTIPLES,
    THRESHOLDS,
    ThresholdRow,
    VerdictKind,
    best_r,
    build_arm_candidates,
    build_arm_params,
    build_summary_markdown,
    check_threshold_nesting,
    classify_diff,
    curve_shape,
    default_cost_note,
    merge_rows,
    overall_verdict,
    pooled,
    rows_to_frame,
    tf_verdict,
    threshold_label,
)
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"
_HTF_MS = timeframe_to_ms(_TF)
_MINUTE = 60_000


# --------------------------------------------------------------------------- #
# 픽스처 (wan155 테스트와 같은 시드 — 후보가 실제로 나오는 조합)
# --------------------------------------------------------------------------- #


def _synthetic_market(bars: int = 600, span: int = 400) -> MarketData:
    htf = make_synthetic_ohlcv(timeframe=_TF, bars=bars, seed=7)
    start = int(htf["open_time"].iloc[-span])
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=span * 60, seed=11, start_time_ms=start, swing_period=180
    )
    return MarketData(_SYMBOL, _TF, htf, one_min, [])


def _relaxed_base() -> ConfluenceParams:
    """합성 데이터에서 후보가 실제로 나오는 설정 — 탐지는 안 건드리므로 핀 검정의 축은 산다."""
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )


def _key(c: _Candidate) -> tuple[int, int, float]:
    return (c.trigger_time, c.entry_time, c.entry_price)


def _staged_market_and_obr(*, zone_bottom: float = 90.0) -> tuple[MarketData, OrderBlockResult]:
    """존 근단 100 · 무효화 `zone_bottom`인 롱 오더블록 하나(test_wan155/158과 같은 구조).

    앞선 봉 폭 4로 직전 확정봉 ATR ≈ 3.86 — 존폭 10이면 존폭/ATR ≈ 2.6이라 세 문턱 전부가
    걸러야 하고, 존폭 4면 ≈ 1.04라 세 문턱 전부 통과해야 한다.
    """
    bars = 40
    htf = pd.DataFrame(
        {
            "open_time": [i * _HTF_MS for i in range(bars)],
            "open": [105.0 for _ in range(bars)],
            "close": [105.0 + (1.0 if i % 2 else -1.0) for i in range(bars)],
            "high": [107.0] * bars,
            "low": [103.0] * bars,
            "volume": [1_000.0 for _ in range(bars)],
        }
    )
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=zone_bottom,
        start_time=0,
        confirmed_time=_HTF_MS,
        ob_volume=1_000.0,
        ob_low_volume=400.0,
        ob_high_volume=600.0,
    )
    tap_time = int(htf["open_time"].iloc[-1])
    path = [99.5] + [100.0 + i * 0.35 for i in range(60)]
    one_min = pd.DataFrame(
        {
            "open_time": [tap_time + i * _MINUTE for i in range(len(path))],
            "open": [105.0] + list(path[:-1]),
            "high": [105.0] + [p + 1.0 for p in path[1:]],
            "low": path,
            "close": [100.0] + [p + 0.5 for p in path[1:]],
            "volume": [10.0 for _ in path],
        }
    )
    signal = OrderBlockSignal(
        direction=OrderBlockDirection.BULLISH,
        trigger_time=tap_time,
        price=100.0,
        order_block=ob,
    )
    obr = OrderBlockResult(order_blocks=[ob], signals=[signal], retap_signals=[signal])
    return MarketData(_SYMBOL, _TF, htf, one_min, []), obr


def _staged_base() -> ConfluenceParams:
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", rsi_gate_mode="none", deviation_filter=None
    )


# --------------------------------------------------------------------------- #
# 1. 옛 핀을 물려받지 않는다
# --------------------------------------------------------------------------- #


def test_arm_candidates_use_adopted_defaults_not_legacy_pins() -> None:
    market = _synthetic_market()
    base = _relaxed_base()
    got, _ = build_arm_candidates(market, threshold=None, r_multiple=R_DEFAULT, base=base)
    cfg = harness.build_config(_TF)
    params = harness.build_params(take_profit_r=R_DEFAULT, base=base)
    separated, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TF,
        params=params,
        cfg=cfg,
        order_block_result=harness.detect_order_blocks(market, OrderBlockParams()),
    )
    merged, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TF,
        params=params,
        cfg=cfg,
        order_block_result=harness.detect_order_blocks(market, LEGACY_OB_PARAMS),
    )
    assert got, "후보가 0개다 — 이 검정이 공허하게 참이 됐다."
    assert [_key(c) for c in got] == [_key(c) for c in separated]
    if [_key(c) for c in separated] != [_key(c) for c in merged]:
        assert [_key(c) for c in got] != [_key(c) for c in merged]


def test_engine_is_todays_adopted_default_not_pinned_band_or_gate() -> None:
    params = build_arm_params(threshold=1.28, r_multiple=R_DEFAULT)
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"
    assert params.deviation_filter.band_bar != harness.LEGACY_BAND_BAR
    assert params.rsi_gate_mode == "unconditional"
    assert params.take_profit_mode == "fixed_r"


def test_thresholds_are_atr_multiples_not_fractions() -> None:
    """단위 함정(WAN-158/112) — 분수를 넣으면 거의 전부 걸러진다."""
    assert THRESHOLDS[0] is None
    assert all(t is not None and t > 1.0 for t in THRESHOLDS[1:])


# --------------------------------------------------------------------------- #
# 2. 이중 필터 없음 — WAN-159 전후 어느 쪽에서도 같은 행
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("threshold", THRESHOLDS)
def test_arm_params_pin_the_threshold_regardless_of_the_adopted_default(
    threshold: float | None,
) -> None:
    """기본값이 이미 켜져 있어도(= WAN-159 이후 세계) 팔의 문턱은 인자 그대로다."""
    after_wan159 = ConfluenceParams(max_zone_width_atr=1.28)
    params = build_arm_params(threshold=threshold, r_multiple=R_DEFAULT, base=after_wan159)
    assert params.max_zone_width_atr == threshold


def test_filter_off_arm_is_really_off_after_a_default_flip() -> None:
    """🚨 이 이슈의 핵심 함정 — 기본값이 1.28로 옮겨간 뒤에도 「필터 끔」 팔은 넓은 존을
    매매해야 한다. `build_params`의 `None`은 「손대지 않는다」라 덮어쓰기가 없으면 라벨만
    남고 조용히 1.28로 돈다."""
    market, obr = _staged_market_and_obr()  # 존폭/ATR ≈ 2.6
    after_wan159 = _staged_base().model_copy(update={"max_zone_width_atr": 1.28})
    off_arm, _ = build_arm_candidates(
        market,
        threshold=None,
        r_multiple=R_DEFAULT,
        base=after_wan159,
        order_block_result=obr,
    )
    assert off_arm, "기본값이 켜진 세계에서 「필터 끔」 팔이 이중 필터에 걸렸다."


# --------------------------------------------------------------------------- #
# 3. 문턱이 실제로 걸린다
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("threshold", [t for t in THRESHOLDS if t is not None])
def test_wide_zone_is_dropped_by_every_threshold(threshold: float) -> None:
    market, obr = _staged_market_and_obr()  # 존폭/ATR ≈ 2.6 > 1.55
    filtered, _ = build_arm_candidates(
        market,
        threshold=threshold,
        r_multiple=R_DEFAULT,
        base=_staged_base(),
        order_block_result=obr,
    )
    assert filtered == [], f"문턱 {threshold}에서 존폭/ATR ≈ 2.6 셋업이 살아남았다."


@pytest.mark.parametrize("threshold", THRESHOLDS)
def test_narrow_zone_survives_every_threshold(threshold: float | None) -> None:
    market, obr = _staged_market_and_obr(zone_bottom=96.0)  # 존폭 4 → 비율 ≈ 1.04
    filtered, _ = build_arm_candidates(
        market,
        threshold=threshold,
        r_multiple=R_DEFAULT,
        base=_staged_base(),
        order_block_result=obr,
    )
    assert len(filtered) == 1, "좁은 존까지 걸러지면 필터가 전부를 죽이는 것이다."


# --------------------------------------------------------------------------- #
# 4. 포함 관계 검산 — 개수가 아니라 집합
# --------------------------------------------------------------------------- #


def _entry_sets(
    per_threshold: dict[float | None, list[int]],
) -> dict[tuple[float | None, float], list[int]]:
    return {(t, r): entries for t, entries in per_threshold.items() for r in R_MULTIPLES}


def test_nesting_check_passes_for_a_proper_subset_chain() -> None:
    market = _synthetic_market(bars=60, span=20)
    check_threshold_nesting(
        _entry_sets({None: [1, 2, 3, 4], 1.55: [1, 2, 3], 1.28: [1, 2], 1.15: [1]}), market
    )


def test_nesting_check_catches_same_count_but_different_setups() -> None:
    """개수만 보면 통과하는 배선 버그(같은 개수의 **다른** 셋업)를 집합 비교가 잡는다."""
    market = _synthetic_market(bars=60, span=20)
    with pytest.raises(AssertionError, match="포함 관계 위반"):
        check_threshold_nesting(
            _entry_sets({None: [1, 2, 3], 1.55: [1, 2, 3], 1.28: [1, 2, 9], 1.15: [1]}),
            market,
        )


# --------------------------------------------------------------------------- #
# 5·6. 게이트 · 판정 · 요약
# --------------------------------------------------------------------------- #


def _row(
    symbol: str,
    threshold: float | None,
    r_multiple: float,
    *,
    ret: float,
    trades: int = 50,
    segment: str = SEGMENT_OOS,
    timeframe: str = _TF,
) -> ThresholdRow:
    return ThresholdRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        threshold=threshold,
        r_multiple=r_multiple,
        guard=GUARD,
        num_candidates=trades,
        num_trades=trades,
        total_return=ret,
        max_drawdown=0.10,
        win_rate=0.5,
        sharpe=None,
        n_take_profit=trades // 2,
        n_stop_loss=trades - trades // 2,
        n_end_of_data=0,
        fill_rate_full=0.8,
        mean_net_r=0.1,
        net_r_win=1.4,
        net_r_loss=-1.05,
        cost_r_median=0.2,
        breakeven_win_rate=0.45,
        win_rate_margin=0.05,
        profit_factor=1.2,
        cap_hit_rate=0.9,
        effective_risk_mean=0.006,
        guard_reject_rate=0.1,
        tp_dist_frac_median=0.01,
        stop_frac_median=0.007,
    )


def _grid_rows(
    *,
    best_by_threshold: dict[float | None, float],
    oos_best_by_threshold: dict[float | None, float] | None = None,
    symbols: int = 4,
    trades: int = 50,
    timeframe: str = _TF,
) -> list[ThresholdRow]:
    """문턱마다 지정한 배수가 최적이 되도록 수익률을 심는다."""
    oos_best = oos_best_by_threshold or best_by_threshold
    rows: list[ThresholdRow] = []
    for i in range(symbols):
        sym = f"S{i}/USDT:USDT"
        for threshold in THRESHOLDS:
            for segment, table in ((SEGMENT_IS, best_by_threshold), (SEGMENT_OOS, oos_best)):
                for r_multiple in R_MULTIPLES:
                    peak = table.get(threshold, R_DEFAULT)
                    ret = 0.20 if r_multiple == peak else 0.05
                    rows.append(
                        _row(
                            sym,
                            threshold,
                            r_multiple,
                            ret=ret,
                            trades=trades,
                            segment=segment,
                            timeframe=timeframe,
                        )
                    )
    return rows


def test_pooled_gate_excludes_thin_cells() -> None:
    rows = _grid_rows(best_by_threshold={t: R_DEFAULT for t in THRESHOLDS}, symbols=3)
    rows.append(_row("S9/USDT:USDT", 1.28, R_DEFAULT, ret=9.9, trades=MIN_TRADES_PER_SYMBOL - 1))
    cell = pooled(rows_to_frame(rows), _TF, SEGMENT_OOS, 1.28, R_DEFAULT)
    assert cell["n_symbols"] == 3.0
    assert cell["n_excluded"] == 1.0
    assert cell["total_return"] == pytest.approx(0.20)


def test_best_r_drops_thin_points_from_the_curve() -> None:
    """🚨 한두 심볼만 남은 극단값이 「최적 배수」로 올라오면 민감도 판정이 통째로 흔들린다."""
    rows = _grid_rows(best_by_threshold={t: R_DEFAULT for t in THRESHOLDS}, symbols=4)
    # 3.0R 점만 표본을 무너뜨리고, 그 얇은 셀에 큰 수익을 심는다.
    rows = [r for r in rows if not (r.threshold == 1.15 and r.r_multiple == 3.0)]
    rows += [
        _row("S0/USDT:USDT", 1.15, 3.0, ret=9.9, trades=MIN_TRADES_PER_SYMBOL - 1, segment=seg)
        for seg in (SEGMENT_IS, SEGMENT_OOS)
    ]
    got = best_r(rows_to_frame(rows), _TF, SEGMENT_IS, 1.15)
    assert got.best == R_DEFAULT
    assert 3.0 in got.excluded
    assert "표본 미달 제외" in got.curve


def test_verdict_independent_when_every_threshold_picks_the_same_r() -> None:
    frame = rows_to_frame(_grid_rows(best_by_threshold={t: R_DEFAULT for t in THRESHOLDS}))
    v = tf_verdict(frame, _TF)
    assert v.kind is VerdictKind.INDEPENDENT
    assert "문턱과 독립" in v.text


def test_verdict_dependent_when_thresholds_pick_different_r() -> None:
    frame = rows_to_frame(
        _grid_rows(best_by_threshold={None: 3.0, 1.55: 2.0, 1.28: 1.5, 1.15: 1.0})
    )
    v = tf_verdict(frame, _TF)
    assert v.kind is VerdictKind.DEPENDENT
    assert "문턱에 종속" in v.text


def test_verdict_indeterminate_when_too_few_thresholds_survive_the_gate() -> None:
    frame = rows_to_frame(
        _grid_rows(
            best_by_threshold={t: R_DEFAULT for t in THRESHOLDS},
            symbols=MIN_SYMBOLS_FOR_VERDICT - 1,
        )
    )
    v = tf_verdict(frame, _TF)
    assert v.kind is VerdictKind.INDETERMINATE
    assert "판정 불가" in v.text


def test_oos_flips_are_counted_but_do_not_change_the_verdict() -> None:
    """뒷구간 최적은 **참고**다 — 전부 뒤집혀도 판정은 IS 축에서 낸다."""
    frame = rows_to_frame(
        _grid_rows(
            best_by_threshold={t: R_DEFAULT for t in THRESHOLDS},
            oos_best_by_threshold={t: 3.0 for t in THRESHOLDS},
        )
    )
    v = tf_verdict(frame, _TF)
    assert v.kind is VerdictKind.INDEPENDENT
    assert v.oos_flips == len(THRESHOLDS)
    assert "채택 근거로 쓰지 않는다" in v.text


def test_overall_verdict_splits_by_timeframe() -> None:
    rows = _grid_rows(best_by_threshold={t: R_DEFAULT for t in THRESHOLDS}, timeframe="1h")
    rows += _grid_rows(
        best_by_threshold={None: 3.0, 1.55: 2.0, 1.28: 1.5, 1.15: 1.0}, timeframe="15m"
    )
    text = overall_verdict(rows_to_frame(rows), ["1h", "15m"])
    assert "(c) TF에 갈린다" in text


def test_merge_rows_keeps_the_filter_off_coordinate_apart() -> None:
    """문턱 `None`이 NaN으로 접히면 좌표 비교(NaN != NaN)가 깨져 행이 중복된다."""
    off = _row("S0/USDT:USDT", None, R_DEFAULT, ret=0.10)
    on = _row("S0/USDT:USDT", 1.28, R_DEFAULT, ret=0.05)
    merged = merge_rows([off, on], [off.model_copy(update={"total_return": 0.07})])
    assert len(merged) == 2
    assert next(r for r in merged if r.threshold is None).total_return == pytest.approx(0.07)
    assert next(r for r in merged if r.threshold == 1.28).total_return == pytest.approx(0.05)


def test_curve_shape_measures_the_plateau_not_just_the_argmax() -> None:
    """🚨 (a) 판정의 **크기**를 재는 자 — argmax는 고원이면 몇 %p로도 뒤집힌다."""
    rows = _grid_rows(best_by_threshold={t: 3.0 for t in THRESHOLDS})
    spread, gap = curve_shape(rows_to_frame(rows), _TF, SEGMENT_IS, 1.28)
    assert spread == pytest.approx(0.15)  # 0.20 − 0.05
    assert gap == pytest.approx(0.15)  # 최적 0.20 − 1.5R 0.05


def test_verdict_flags_a_small_gap_as_a_plateau_argmax() -> None:
    """최적이 현행 1.5R을 근소하게만 이기면 「다른 값을 써야 한다」로 읽지 말라고 찍는다."""
    rows: list[ThresholdRow] = []
    for i in range(4):
        sym = f"S{i}/USDT:USDT"
        for threshold in THRESHOLDS:
            peak = 3.0 if threshold in (1.28, 1.55) else 2.0
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                for r_multiple in R_MULTIPLES:
                    rows.append(
                        _row(
                            sym,
                            threshold,
                            r_multiple,
                            ret=0.1005 if r_multiple == peak else 0.10,
                            segment=segment,
                        )
                    )
    v = tf_verdict(rows_to_frame(rows), _TF)
    assert v.kind is VerdictKind.DEPENDENT  # argmax는 문턱마다 다르다
    assert v.max_gap_vs_default is not None
    assert v.max_gap_vs_default == pytest.approx(0.0005)
    assert "고원 위의 argmax" in v.text


def test_default_cost_note_reports_upkeep_not_an_oos_choice() -> None:
    """유지 비용 줄은 「뒷구간이 1.5R을 골랐다」로 읽히면 안 된다 — 경고문이 박혀 있다."""
    rows = _grid_rows(
        best_by_threshold={t: R_DEFAULT for t in THRESHOLDS},
        oos_best_by_threshold={t: 3.0 for t in THRESHOLDS},
    )
    text = default_cost_note(rows_to_frame(rows), _TF)
    assert "−15.00%p" in text  # OOS 최적 0.20 − 1.5R 0.05
    assert "골랐다」로 읽지 말 것" in text


def test_default_cost_note_marks_a_tie_with_the_oos_optimum() -> None:
    rows = _grid_rows(best_by_threshold={t: R_DEFAULT for t in THRESHOLDS})
    assert "최적과 같다" in default_cost_note(rows_to_frame(rows), _TF)


def test_classify_diff_separates_exact_noise_and_mismatch() -> None:
    """세 갈래를 다르게 찍는다(WAN-151 선례) — 잡음을 ✅로 뭉개면 불일치가 위장한다."""
    assert "비트 일치" in classify_diff(0.0)
    assert "부동소수 끝자리" in classify_diff(9.7e-17)
    assert "불일치" in classify_diff(1e-4)


def test_threshold_label_names_the_off_arm() -> None:
    assert threshold_label(None) == "필터 끔"
    assert threshold_label(1.28) == "1.28"


def test_summary_contains_the_mandated_statements() -> None:
    rows = _grid_rows(best_by_threshold={t: R_DEFAULT for t in THRESHOLDS})
    text = build_summary_markdown(rows, timeframes=[_TF])
    assert "WAN-155·WAN-143·WAN-90 표와 셀 직접 비교 금지" in text
    assert "채택 근거로 쓰지 않는다" in text  # 뒷구간 최적을 근거로 쓰지 않는다
    assert "ALPHABLOCK_LIVE_TRADING=false" in text
    assert "ATR 배수" in text  # 단위 경고(WAN-158)
    assert "엣지 없음" in text
    assert "이중 필터 없음" in text
    assert "가드 변경 제안이 아니다" in text  # WAN-76/79 소관
