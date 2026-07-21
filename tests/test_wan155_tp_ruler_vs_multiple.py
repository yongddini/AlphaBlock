"""WAN-155 「자 vs 배수」 격자 — 라벨이 아니라 **동작**으로 고정한다.

이 파일이 지키는 것:

1. **옛 핀을 물려받지 않는다**(WAN-152 패턴) — 격자 팔의 후보 집합이 오늘의 채택 기본값
   (분리 존 · `intrabar_live`)과 같고, 병합 존(`LEGACY_OB_PARAMS`)과는 다르다. wan143을
   복사하면 255번 줄의 핀이 따라오는 것이 이 이슈의 명시 경고였다.
2. **존폭 필터가 격자 팔에 실제로 걸려 있다** — 심어 둔 넓은 존(존폭/ATR ≈ 2.6 > 1.28)이
   필터 켠 팔에서는 주문조차 안 걸리고, 기준점 팔(필터 끔)에서는 걸린다. 「고정 조건」이
   라벨만 붙는 것이 이 저장소가 네 번 겪은 조용한 실패다(WAN-91/95/112/123).
3. **존높이 자가 실제로 다른 목표를 낸다** — 진입가가 존 안쪽일 때 두 자의 목표가 다르다.
4. **손익분기 승률이 실현 분포 식이다** — 고정 1.5R 식(wan152)은 배수 스윕·존높이 자에서
   전제가 깨진다.
5. **판정 게이트** — 유효 심볼 3개 미만이면 판정하지 않고, 20거래 미만 셀은 심볼평균에서
   빠지고, 두 자의 거래 수 차이 5% 초과면 판정이 강등된다(이슈 §4).
6. **판정 네 분기가 의도한 입력에서 나오고 요약 필수 문장이 박혀 있다.**
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import LEGACY_OB_PARAMS, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import ExitReason, PositionSide
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan143_zone_height_tp import TP_ENTRY, TP_ZONE, make_zone_height_override
from backtest.wan155_tp_ruler_vs_multiple import (
    FILTER_THRESHOLD,
    GUARD_ON,
    MIN_SYMBOLS_FOR_VERDICT,
    MIN_TRADES_PER_SYMBOL,
    R_DEFAULT,
    RulerRow,
    VerdictKind,
    _tp_distance,
    build_arm_candidates,
    build_summary_markdown,
    empirical_breakeven,
    overall_verdict,
    pooled,
    rows_to_frame,
    tf_verdict,
)
from backtest.zone_limit_backtest import (
    TakeProfitContext,
    _Candidate,
    build_zone_limit_candidates,
)
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
# 픽스처
# --------------------------------------------------------------------------- #


def _synthetic_market(bars: int = 600, span: int = 400) -> MarketData:
    """합성 시장(wan152 테스트와 같은 조합 — 1h에서 후보가 실제로 나오는 시드)."""
    htf = make_synthetic_ohlcv(timeframe=_TF, bars=bars, seed=7)
    start = int(htf["open_time"].iloc[-span])
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=span * 60, seed=11, start_time_ms=start, swing_period=180
    )
    return MarketData(_SYMBOL, _TF, htf, one_min, [])


def _relaxed_base() -> ConfluenceParams:
    """합성 데이터에서 **실제로 후보가 나오는** 엔진 설정(wan152 테스트 관행 그대로).

    채택 기본값 그대로는 이 시리즈에서 후보가 0개라 핀 검정이 `[] == []`로 공허해진다.
    탐지(`OrderBlockParams`)는 건드리지 않으므로 핀 검정의 축은 살아 있다.
    """
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )


def _key(c: _Candidate) -> tuple[int, int, float]:
    return (c.trigger_time, c.entry_time, c.entry_price)


def _staged_market_and_obr(
    *, zone_bottom: float = 90.0
) -> tuple[MarketData, OrderBlockResult, OrderBlock]:
    """존 근단 100 · 무효화 `zone_bottom`인 롱 오더블록 하나를 심는다.

    앞선 봉 폭 4로 ATR(직전 확정봉) ≈ 3.86 — 기본 존폭 10이면 존폭/ATR ≈ 2.6 >
    `FILTER_THRESHOLD`(1.28)라 필터가 걸러야 한다(test_wan158 픽스처와 같은 구조).
    """
    bars = 40
    htf = pd.DataFrame(
        {
            "open_time": [i * _HTF_MS for i in range(bars)],
            "open": [105.0 for _ in range(bars)],
            "close": [105.0 + (1.0 if i % 2 else -1.0) for i in range(bars)],
            "high": [107.0] * (bars - 1) + [107.0],
            "low": [103.0] * (bars - 1) + [103.0],
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
            "open": [105.0] + [p for p in path[:-1]],
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
    return MarketData(_SYMBOL, _TF, htf, one_min, []), obr, ob


def _staged_base() -> ConfluenceParams:
    """심어 둔 셋업이 실제로 체결되는 최소 설정(볼린저·게이트를 끈다 — 검증 대상은
    존폭 필터·익절 자이지 진입 규칙이 아니다)."""
    return ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        rsi_gate_mode="none",
        deviation_filter=None,
    )


# --------------------------------------------------------------------------- #
# 1. 옛 핀을 물려받지 않는다 (WAN-152 패턴)
# --------------------------------------------------------------------------- #


def test_arm_candidates_use_adopted_defaults_not_legacy_pins() -> None:
    """격자 팔(필터 끔 기준점)의 후보 = 분리 존(오늘 기본값) 후보 ≠ 병합 존(핀) 후보."""
    market = _synthetic_market()
    base = _relaxed_base()
    got, _ = build_arm_candidates(
        market, tp_rule=TP_ENTRY, r_multiple=R_DEFAULT, filter_on=False, base=base
    )
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


def test_band_bar_is_intrabar_live_not_pinned_tap() -> None:
    """진입가 정본이 `intrabar_live`다(WAN-132) — 이 모듈은 `pin_band_bar`를 쓰지 않는다."""
    params = harness.build_params(max_zone_width_atr=FILTER_THRESHOLD)
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"
    assert params.deviation_filter.band_bar != harness.LEGACY_BAND_BAR
    assert params.rsi_gate_mode == "unconditional"


def test_filter_threshold_is_an_atr_multiple_not_a_fraction() -> None:
    """단위 함정(WAN-158/112) — `0.0128` 같은 분수가 아니라 ATR **배수**다."""
    assert FILTER_THRESHOLD == 1.28
    assert FILTER_THRESHOLD > 1.0


# --------------------------------------------------------------------------- #
# 2. 존폭 필터가 격자 팔에 실제로 걸려 있다
# --------------------------------------------------------------------------- #


def test_grid_arm_drops_wide_zone_and_reference_arm_keeps_it() -> None:
    """존폭/ATR ≈ 2.6 셋업: 필터 켠 팔은 주문조차 안 걸고, 기준점 팔(끔)은 건다."""
    market, obr, _ = _staged_market_and_obr()
    base = _staged_base()
    filtered, _ = build_arm_candidates(
        market,
        tp_rule=TP_ENTRY,
        r_multiple=R_DEFAULT,
        filter_on=True,
        base=base,
        order_block_result=obr,
    )
    reference, _ = build_arm_candidates(
        market,
        tp_rule=TP_ENTRY,
        r_multiple=R_DEFAULT,
        filter_on=False,
        base=base,
        order_block_result=obr,
    )
    assert reference, "기준점 팔에서 심어 둔 셋업이 체결되지 않았다 — 픽스처가 죽었다."
    assert filtered == [], "존폭/ATR > 문턱인 셋업이 필터 켠 팔에서 살아남았다 — 배선 버그."


def test_grid_arm_keeps_narrow_zone() -> None:
    """좁은 존(존폭/ATR < 1.28)은 필터 켠 팔에서도 산다 — 필터가 전부 거르는 게 아니다."""
    market, obr, _ = _staged_market_and_obr(zone_bottom=96.0)  # 존폭 4 → 비율 ≈ 1.04
    filtered, _ = build_arm_candidates(
        market,
        tp_rule=TP_ENTRY,
        r_multiple=R_DEFAULT,
        filter_on=True,
        base=_staged_base(),
        order_block_result=obr,
    )
    assert len(filtered) == 1


# --------------------------------------------------------------------------- #
# 3. 존높이 자가 실제로 다른 목표를 낸다
# --------------------------------------------------------------------------- #


def test_zone_height_ruler_moves_the_target_when_entry_is_inside_the_zone() -> None:
    """진입 95 · 손절 90 · 존 100~90: 현행 1R=5 → 목표 102.5, 존높이 1R=10 → 목표 110."""
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=_HTF_MS,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )
    params = harness.build_params(take_profit_r=R_DEFAULT)
    ctx = TakeProfitContext(
        is_long=True, entry_price=95.0, stop_price=90.0, trigger_time=0, order_block=ob
    )
    resolved = make_zone_height_override(params, R_DEFAULT)(ctx)
    assert resolved == pytest.approx(95.0 + 1.5 * 10.0)

    cand = _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=95.0,
        exit_time=1,
        exit_price=100.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
        order_block=ob,
        trigger_time=0,
    )
    assert _tp_distance(cand, TP_ENTRY, R_DEFAULT) == pytest.approx(7.5)
    assert _tp_distance(cand, TP_ZONE, R_DEFAULT) == pytest.approx(15.0)


# --------------------------------------------------------------------------- #
# 4. 손익분기 승률 — 실현 분포 식
# --------------------------------------------------------------------------- #


def test_empirical_breakeven_solves_the_expected_value_zero_condition() -> None:
    assert empirical_breakeven(1.5, -1.0) == pytest.approx(0.4)
    assert empirical_breakeven(3.0, -1.0) == pytest.approx(0.25)
    assert empirical_breakeven(None, -1.0) is None
    assert empirical_breakeven(1.5, None) is None
    assert empirical_breakeven(-0.1, -1.0) is None  # 승리 평균이 음수면 식이 깨진다
    assert empirical_breakeven(1.5, 0.1) is None


# --------------------------------------------------------------------------- #
# 5·6. 게이트 · 판정 분기 · 요약 필수 문장
# --------------------------------------------------------------------------- #


def _row(
    symbol: str,
    tp_rule: str,
    *,
    ret: float,
    mdd: float = 0.10,
    net_r: float = 0.10,
    trades: int = 50,
    segment: str = SEGMENT_OOS,
    r_multiple: float = R_DEFAULT,
    filter_on: bool = True,
    timeframe: str = _TF,
) -> RulerRow:
    return RulerRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        tp_rule=tp_rule,
        r_multiple=r_multiple,
        guard=GUARD_ON,
        filter_on=filter_on,
        num_candidates=trades,
        num_trades=trades,
        total_return=ret,
        max_drawdown=mdd,
        win_rate=0.5,
        sharpe=None,
        n_take_profit=trades // 2,
        n_stop_loss=trades - trades // 2,
        n_end_of_data=0,
        fill_rate_full=0.8,
        mean_net_r=net_r,
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
        height_over_stop_med=1.3,
        height_over_stop_p25=1.1,
        height_over_stop_p75=1.8,
    )


def _grid_rows(
    *,
    zone_ret: float,
    entry_ret: float,
    zone_mdd: float = 0.10,
    entry_mdd: float = 0.10,
    zone_net_r: float = 0.12,
    entry_net_r: float = 0.10,
    zone_trades: int = 50,
    entry_trades: int = 50,
    symbols: int = 4,
    timeframe: str = _TF,
) -> list[RulerRow]:
    rows: list[RulerRow] = []
    for i in range(symbols):
        sym = f"S{i}/USDT:USDT"
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for r_multiple in (1.0, 1.5, 2.0, 3.0):
                rows.append(
                    _row(
                        sym,
                        TP_ZONE,
                        ret=zone_ret,
                        mdd=zone_mdd,
                        net_r=zone_net_r,
                        trades=zone_trades,
                        segment=segment,
                        r_multiple=r_multiple,
                        timeframe=timeframe,
                    )
                )
                rows.append(
                    _row(
                        sym,
                        TP_ENTRY,
                        ret=entry_ret,
                        mdd=entry_mdd,
                        net_r=entry_net_r,
                        trades=entry_trades,
                        segment=segment,
                        r_multiple=r_multiple,
                        timeframe=timeframe,
                    )
                )
        rows.append(_row(sym, TP_ENTRY, ret=entry_ret, filter_on=False, timeframe=timeframe))
    return rows


def test_pooled_gate_excludes_thin_cells() -> None:
    rows = _grid_rows(zone_ret=0.10, entry_ret=0.05, symbols=3)
    rows.append(_row("S9/USDT:USDT", TP_ZONE, ret=9.9, trades=MIN_TRADES_PER_SYMBOL - 1))
    cell = pooled(rows_to_frame(rows), _TF, SEGMENT_OOS, TP_ZONE, R_DEFAULT)
    assert cell["n_symbols"] == 3.0
    assert cell["n_excluded"] == 1.0
    assert cell["total_return"] == pytest.approx(0.10)


def test_verdict_zone_when_all_three_metrics_agree() -> None:
    frame = rows_to_frame(_grid_rows(zone_ret=0.20, entry_ret=0.05))
    v = tf_verdict(frame, _TF)
    assert v.kind is VerdictKind.ZONE
    assert not v.demoted


def test_verdict_entry_when_zone_loses_everywhere() -> None:
    frame = rows_to_frame(
        _grid_rows(zone_ret=0.02, entry_ret=0.10, zone_net_r=0.01, entry_net_r=0.10)
    )
    assert tf_verdict(frame, _TF).kind is VerdictKind.ENTRY


def test_verdict_mixed_when_raw_wins_but_risk_adjusted_loses() -> None:
    """WAN-137의 「raw만 승, 위험조정하면 증발」 모양 — (c)로 읽어야 한다."""
    frame = rows_to_frame(
        _grid_rows(
            zone_ret=0.12,
            entry_ret=0.10,
            zone_mdd=0.30,
            entry_mdd=0.10,
            zone_net_r=0.05,
            entry_net_r=0.10,
        )
    )
    assert tf_verdict(frame, _TF).kind is VerdictKind.MIXED


def test_verdict_indeterminate_when_too_few_valid_symbols() -> None:
    frame = rows_to_frame(
        _grid_rows(zone_ret=0.20, entry_ret=0.05, symbols=MIN_SYMBOLS_FOR_VERDICT - 1)
    )
    v = tf_verdict(frame, _TF)
    assert v.kind is VerdictKind.INDETERMINATE
    assert "판정 불가" in v.text


def test_verdict_demoted_when_trade_counts_diverge() -> None:
    """존높이 팔의 슬롯 잠금이 표본을 갈라놓으면(>5%) 판정이 강등된다(이슈 §4)."""
    frame = rows_to_frame(
        _grid_rows(zone_ret=0.20, entry_ret=0.05, zone_trades=45, entry_trades=50)
    )
    v = tf_verdict(frame, _TF)
    assert v.demoted
    assert "판정 강등" in v.text


def test_overall_verdict_splits_by_timeframe() -> None:
    rows = _grid_rows(zone_ret=0.20, entry_ret=0.05, timeframe="1h") + _grid_rows(
        zone_ret=0.02, entry_ret=0.10, zone_net_r=0.01, timeframe="15m"
    )
    text = overall_verdict(rows_to_frame(rows), ["1h", "15m"])
    assert "(c) TF에 갈린다" in text


def test_overall_verdict_is_keep_current_when_zone_never_wins() -> None:
    """한 TF는 (b) 완패, 다른 TF는 (c) 지표 갈림(0 언저리) — 존높이가 어디서도 이기지
    못하므로 종합은 (b) 현행 유지다. MIXED를 (c)로 세면 「어디서도 안 이기는」 표가 「TF에
    갈린다」로 둔갑한다(실제 15m/1h 격자가 정확히 이 모양이었다)."""
    entry_tf = _grid_rows(zone_ret=0.02, entry_ret=0.10, zone_net_r=0.01, timeframe="15m")
    mixed_tf = _grid_rows(
        zone_ret=0.12,
        entry_ret=0.10,
        zone_mdd=0.30,
        entry_mdd=0.10,
        zone_net_r=0.05,
        entry_net_r=0.10,
        timeframe="1h",
    )
    text = overall_verdict(rows_to_frame(entry_tf + mixed_tf), ["15m", "1h"])
    assert "(b) 현행 유지" in text


def test_merge_rows_replaces_same_coordinates_and_keeps_the_rest() -> None:
    """`--append` 병합은 TF 단위가 아니라 행 좌표 단위다 — 같은 TF의 부분 재실행(예: 필터
    끔 격자만 추가)이 기존 필터 켬 행을 지우면 조용한 데이터 손실이다."""
    from backtest.wan155_tp_ruler_vs_multiple import merge_rows

    on_row = _row("S0/USDT:USDT", TP_ENTRY, ret=0.10)
    off_row = _row("S0/USDT:USDT", TP_ENTRY, ret=0.05, filter_on=False)
    merged = merge_rows([on_row, off_row], [off_row.model_copy(update={"total_return": 0.07})])
    assert len(merged) == 2
    kept_on = next(r for r in merged if r.filter_on)
    replaced_off = next(r for r in merged if not r.filter_on)
    assert kept_on.total_return == pytest.approx(0.10)  # 같은 TF지만 좌표가 달라 살아남는다
    assert replaced_off.total_return == pytest.approx(0.07)  # 같은 좌표는 새 행이 이긴다


def test_off_grid_reference_section_appears_when_off_rows_exist() -> None:
    """필터 끔 격자를 돌리면 요약에 참고 절이 생기고, 기준점 하나뿐이면 안 생긴다."""
    rows_with_off = _grid_rows(zone_ret=0.20, entry_ret=0.05)
    for i in range(4):
        sym = f"S{i}/USDT:USDT"
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for rule in (TP_ENTRY, TP_ZONE):
                rows_with_off.append(_row(sym, rule, ret=0.03, segment=segment, filter_on=False))
    text = build_summary_markdown(rows_with_off, timeframes=[_TF])
    assert "필터 끔 격자 (참고" in text
    text_without = build_summary_markdown(
        _grid_rows(zone_ret=0.20, entry_ret=0.05), timeframes=[_TF]
    )
    assert "필터 끔 격자 (참고" not in text_without


def test_summary_contains_the_mandated_statements() -> None:
    rows = _grid_rows(zone_ret=0.20, entry_ret=0.05)
    text = build_summary_markdown(rows, timeframes=[_TF])
    assert "WAN-143 표와 셀 직접 비교 금지" in text
    assert "ALPHABLOCK_LIVE_TRADING=false" in text
    assert "ATR 배수" in text  # 단위 경고(WAN-158)
    assert "절대 목표 거리" in text  # 좁은 존 → 존 높이도 작다 필수 문장
    assert "엣지 없음" in text
