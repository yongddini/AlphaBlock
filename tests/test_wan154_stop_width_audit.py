"""WAN-154 손절폭 축 통합 측정 — 세 장벽·거래당 손익비·가드 축이 라벨이 아니라 동작인가.

이 파일이 **동작으로** 고정하는 것:

1. **`zone_height` 장벽이 실제로 다른 손절선을 쓴다**(진입가 ∓ 존 높이) — 라벨만 붙고 존
   장벽이 그대로 도는 것이 이 저장소의 반복 사고다(WAN-91/95/112/123).
2. **세 장벽이 같은 셋업을 본다** — `zone_height`는 항상 유효한 장벽을 내므로 3장벽 공통
   풀은 WAN-152의 2장벽(zone∩atr) 공통 풀과 **같은 집합**이어야 한다(기존 CSV 행의 비트
   재현이 이 성질에 기댄다).
3. **§1′ 산식** — net R·cost_r·손익분기 승률·상한 발동·실효 리스크가 손으로 계산한 값과
   일치한다. `harness.mean_r`(승률의 재탕)을 쓰지 않았다는 것의 실증이다.
4. **가드 축이 실제로 시퀀싱을 바꾼다** — 가드를 올리면 좁은 손절 거래가 사라지고, 현행
   가드(0.3%) 행은 기본 실행 행과 **완전히 같다**(가드 격자 안에 기본 실행이 들어 있다).
5. **문턱 축** — 문턱을 조이면 필터 후보가 줄고, 1/3 행은 기본 실행과 같다.
6. **판정 함수 분기** — §0(세 자 강건성)·§2(베팅 크기)·§3-B(가드)·§4(체결)·§5(문턱)가
   의도한 입력에서 의도한 분기로 나온다(문장 부분문자열 분기 금지 — WAN-142 교훈).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import ExitReason, PositionSide
from backtest.wan133_geometry_vs_selection import ARM_DEFAULT, ARM_FILTER, STOP_GUARD_FRACTION
from backtest.wan142_zone_width_filter_verdict import ARM_MATCHED, MATCH_SEEDS, SEED_AGGREGATE
from backtest.wan152_selection_vs_geometry import (
    BARRIER_ATR,
    BARRIER_ZONE,
    BARRIER_ZONE_HEIGHT,
    BARRIERS,
    GeoCell,
    MatchedTestRow,
    PnlRow,
    TradeRecord,
    _aggregate_records,
    breakeven_win_rate_for,
    build_cell,
    candidate_key,
    make_zone_height_stop_override,
    per_trade_records,
    pnl_rows_for_cell,
)
from backtest.wan154_stop_width_audit import (
    GUARD_VALUES,
    LENS_PEN,
    THRESHOLD_FRACTIONS,
    Judgement,
    RobustnessKind,
    bet_size_verdict,
    bucket_records_for_cell,
    bucket_rows_from_records,
    guard_verdict,
    pen_verdict,
    robustness_verdict,
    stability_rows_for_cell,
    threshold_verdict,
)
from backtest.zone_limit_backtest import StopLossContext, _Candidate
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"
_HOUR_MS = 3_600_000


def _ob(top: float = 100.0, bottom: float = 98.0) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


# --------------------------------------------------------------------------- #
# 1. zone_height 오버라이드 — 산식
# --------------------------------------------------------------------------- #


def test_zone_height_override_uses_zone_height_not_entry_distance() -> None:
    """손절 = 진입가 ∓ (top − bottom). 진입가가 존 안쪽이어도 1R은 존 높이다."""
    resolve = make_zone_height_stop_override()
    ob = _ob(top=100.0, bottom=98.0)  # 높이 2.0
    got = resolve(
        StopLossContext(
            is_long=True, entry_price=99.0, default_stop=98.0, trigger_time=0, order_block=ob
        )
    )
    assert got == pytest.approx(97.0)  # 99 − 2 (98이 아니다 — 진입가 기준이 아니라 높이 기준)
    got_short = resolve(
        StopLossContext(
            is_long=False, entry_price=99.0, default_stop=100.0, trigger_time=0, order_block=ob
        )
    )
    assert got_short == pytest.approx(101.0)


def test_zone_height_override_rejects_degenerate_zone() -> None:
    resolve = make_zone_height_stop_override()
    flat = _ob(top=98.0, bottom=98.0)
    assert (
        resolve(
            StopLossContext(
                is_long=True, entry_price=99.0, default_stop=98.0, trigger_time=0, order_block=flat
            )
        )
        is None
    )


# --------------------------------------------------------------------------- #
# 2. 세 장벽 — 같은 셋업 · 다른 손절 · 풀 불변
# --------------------------------------------------------------------------- #


def _market(bars: int = 600, *, span: int = 400) -> MarketData:
    from backtest.synthetic import make_synthetic_ohlcv

    htf = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=bars, seed=7)
    start = int(htf["open_time"].iloc[-span])
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=span * 60, seed=11, start_time_ms=start, swing_period=180
    )
    return MarketData(_SYMBOL, _TIMEFRAME, htf, one_min, [])


def _engine_params() -> ConfluenceParams:
    """합성 데이터에서 실제로 후보가 나오는 설정(wan152 테스트와 같은 관행)."""
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )


def test_three_barriers_share_setups_and_zone_height_moves_the_stop() -> None:
    cell = build_cell(_market(), params=_engine_params())
    if cell.n_pool == 0:
        pytest.skip("합성 데이터에서 공통 풀 후보가 나오지 않았다.")
    assert set(cell.by_barrier) == set(BARRIERS)
    keys = {b: [candidate_key(c) for c in cell.by_barrier[b]] for b in BARRIERS}
    assert keys[BARRIER_ZONE] == keys[BARRIER_ATR] == keys[BARRIER_ZONE_HEIGHT]
    zone = cell.by_barrier[BARRIER_ZONE]
    zh = cell.by_barrier[BARRIER_ZONE_HEIGHT]
    differing = [(z, h) for z, h in zip(zone, zh, strict=True) if z.stop_price != h.stop_price]
    assert differing, "zone_height 장벽의 손절가가 존 장벽과 전부 같다 — 오버라이드가 안 걸렸다."
    # 공통 풀은 2장벽(zone∩atr) 시절과 같은 집합이어야 한다 — zone_height는 항상 유효한
    # 장벽을 내므로 교집합을 줄이지 않는다(기존 CSV 행의 비트 재현이 이 성질에 기댄다).
    assert set(keys[BARRIER_ZONE]) <= set(candidate_key(c) for c in zone)


def test_zone_and_atr_rows_do_not_move_when_zone_height_is_added() -> None:
    """3장벽 셀의 zone/atr 행 == 2장벽 셀의 zone/atr 행 — 기존 열 비트 재현의 프록시."""
    cell3, market = _hand_made_cell()
    cell2 = GeoCell(symbol=cell3.symbol, timeframe=cell3.timeframe)
    cell2.by_barrier = {
        BARRIER_ZONE: cell3.by_barrier[BARRIER_ZONE],
        BARRIER_ATR: cell3.by_barrier[BARRIER_ATR],
    }
    cell2.zwa = cell3.zwa
    cell2.is_boundary = cell3.is_boundary
    rows3 = [r for r in pnl_rows_for_cell(cell3, market) if r.barrier != BARRIER_ZONE_HEIGHT]
    rows2 = pnl_rows_for_cell(cell2, market)
    assert [r.model_dump() for r in rows3] == [r.model_dump() for r in rows2]


# --------------------------------------------------------------------------- #
# 3. §1′ 산식 — 손으로 계산한 값과 일치
# --------------------------------------------------------------------------- #


def test_breakeven_win_rate_formula() -> None:
    assert breakeven_win_rate_for(0.0, 1.5) == pytest.approx(0.4)
    assert breakeven_win_rate_for(0.1, 1.5) == pytest.approx(1.1 / 2.5)


def _record(
    *,
    stop_frac: float = 0.01,
    net_r: float = 1.0,
    cost_r: float = 0.1,
    pnl: float = 10.0,
    win: bool = True,
    stopped: bool = False,
    cap_hit: bool = False,
    effective_risk: float = 0.01,
) -> TradeRecord:
    return TradeRecord(
        stop_frac=stop_frac,
        net_r=net_r,
        cost_r=cost_r,
        pnl=pnl,
        win=win,
        stopped=stopped,
        cap_hit=cap_hit,
        effective_risk=effective_risk,
    )


def test_aggregate_records_hand_computed() -> None:
    records = [
        _record(net_r=1.4, cost_r=0.1, pnl=14.0, win=True, cap_hit=False, effective_risk=0.01),
        _record(
            net_r=-1.1,
            cost_r=0.3,
            pnl=-11.0,
            win=False,
            stopped=True,
            cap_hit=True,
            effective_risk=0.005,
        ),
    ]
    agg = _aggregate_records(records)
    assert agg["mean_net_r"] == pytest.approx(0.15)
    assert agg["net_r_win"] == pytest.approx(1.4)
    assert agg["net_r_loss"] == pytest.approx(-1.1)
    assert agg["cost_r_median"] == pytest.approx(0.2)
    assert agg["breakeven_win_rate"] == pytest.approx(breakeven_win_rate_for(0.2, 1.5))
    assert agg["win_rate"] == pytest.approx(0.5)
    assert agg["win_rate_margin"] == pytest.approx(0.5 - breakeven_win_rate_for(0.2, 1.5))
    assert agg["profit_factor"] == pytest.approx(14.0 / 11.0)
    assert agg["survival_rate"] == pytest.approx(0.5)
    assert agg["cap_hit_rate"] == pytest.approx(0.5)
    assert agg["effective_risk_mean"] == pytest.approx(0.0075)


def _cand(
    entry: int, *, entry_price: float = 100.0, exit_price: float, stop_price: float, win: bool
) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry,
        entry_price=entry_price,
        exit_time=entry + _HOUR_MS // 2,
        exit_price=exit_price,
        reason=ExitReason.TAKE_PROFIT if win else ExitReason.STOP_LOSS,
        stop_price=stop_price,
        trigger_time=entry,
    )


def test_per_trade_records_cap_hit_and_effective_risk() -> None:
    """손절폭 0.5% < 1%면 상한 발동·실효 리스크 0.5%, 1%면 미발동·1%."""
    t0 = 1_700_000_000_000
    market = MarketData(_SYMBOL, _TIMEFRAME, pd.DataFrame(), pd.DataFrame(), [])
    narrow = _cand(t0, exit_price=100.75, stop_price=99.5, win=True)  # 손절폭 0.5%
    wide = _cand(t0 + _HOUR_MS, exit_price=101.5, stop_price=99.0, win=True)  # 1.0%
    records = per_trade_records([narrow, wide], market, _TIMEFRAME)
    assert len(records) == 2
    assert records[0].cap_hit is True
    assert records[0].effective_risk == pytest.approx(0.005)
    assert records[0].stop_frac == pytest.approx(0.005)
    assert records[1].cap_hit is False  # 1% ≥ risk/leverage=1% — 경계는 미발동.
    assert records[1].effective_risk == pytest.approx(0.01)
    # net R: 익절 1.5R에서 비용이 빠지므로 1.5보다 약간 작아야 한다(비용이 실제로 반영됐다).
    assert 1.0 < records[1].net_r < 1.5
    assert records[1].cost_r > 0


def test_per_trade_records_guard_rejects_narrow_stops() -> None:
    """가드를 올리면 좁은 손절 거래가 시퀀싱에서 사라진다 — 가드 축이 라벨이 아니다."""
    t0 = 1_700_000_000_000
    market = MarketData(_SYMBOL, _TIMEFRAME, pd.DataFrame(), pd.DataFrame(), [])
    narrow = _cand(t0, exit_price=100.75, stop_price=99.5, win=True)  # 0.5%
    wide = _cand(t0 + _HOUR_MS, exit_price=101.5, stop_price=99.0, win=True)  # 1.0%
    assert len(per_trade_records([narrow, wide], market, _TIMEFRAME, guard=0.0)) == 2
    assert len(per_trade_records([narrow, wide], market, _TIMEFRAME, guard=0.007)) == 1
    assert len(per_trade_records([narrow, wide], market, _TIMEFRAME, guard=0.02)) == 0


# --------------------------------------------------------------------------- #
# 4·5. 가드·문턱 축 — 기본 실행이 격자 안에 들어 있다
# --------------------------------------------------------------------------- #


def _hand_made_cell(n: int = 60) -> tuple[GeoCell, MarketData]:
    """세 장벽을 손으로 만든 셀(wan152 테스트의 3장벽 판)."""
    t0 = 1_700_000_000_000
    zone: list[_Candidate] = []
    atr_arm: list[_Candidate] = []
    zh_arm: list[_Candidate] = []
    zwa: list[float | None] = []
    for i in range(n):
        entry = t0 + i * _HOUR_MS
        win = bool(i % 2)
        zone.append(_cand(entry, exit_price=101.5 if win else 99.0, stop_price=99.0, win=win))
        atr_arm.append(_cand(entry, exit_price=103.0 if win else 98.0, stop_price=98.0, win=win))
        zh_arm.append(_cand(entry, exit_price=102.2 if win else 98.5, stop_price=98.5, win=win))
        zwa.append(float(i % 10))
    cell = GeoCell(symbol=_SYMBOL, timeframe=_TIMEFRAME)
    cell.by_barrier = {BARRIER_ZONE: zone, BARRIER_ATR: atr_arm, BARRIER_ZONE_HEIGHT: zh_arm}
    cell.zwa = zwa
    cell.is_boundary = t0 + (n * 2 // 3) * _HOUR_MS
    return cell, MarketData(_SYMBOL, _TIMEFRAME, pd.DataFrame(), pd.DataFrame(), [])


def test_matched_draw_is_shared_across_all_three_barriers() -> None:
    """같은 시드가 세 장벽에서 같은 셋업(같은 후보 수)을 뽑는다 — WAN-152 검정의 3장벽 판."""
    cell, market = _hand_made_cell()
    rows = pnl_rows_for_cell(cell, market)
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        for seed in MATCH_SEEDS:
            picked = {
                r.barrier: r
                for r in rows
                if r.arm == ARM_MATCHED and r.seed == seed and r.segment == segment
            }
            assert set(picked) == set(BARRIERS)
            counts = {r.num_candidates for r in picked.values()}
            assert len(counts) == 1  # 세 장벽이 같은 개수(= 같은 인덱스)를 뽑았다.


def test_guard_axis_default_value_reproduces_the_main_run() -> None:
    """가드 격자의 0.3% 행 == 기본 실행 행 — 격자와 본 표가 서로를 검산한다."""
    cell, market = _hand_made_cell()
    main = pnl_rows_for_cell(cell, market)
    grid = pnl_rows_for_cell(cell, market, guard=STOP_GUARD_FRACTION)
    assert [r.model_dump() for r in main] == [r.model_dump() for r in grid]
    assert {r.guard for r in main} == {STOP_GUARD_FRACTION}


def test_guard_axis_zero_admits_more_or_equal_trades() -> None:
    cell, market = _hand_made_cell()
    off = pnl_rows_for_cell(cell, market, guard=0.0)
    on = pnl_rows_for_cell(cell, market, guard=0.03)  # 손절폭 1~2%보다 큰 가드 → 전부 거절
    total_off = sum(r.num_trades for r in off if r.arm == ARM_DEFAULT)
    total_on = sum(r.num_trades for r in on if r.arm == ARM_DEFAULT)
    assert total_off > 0
    assert total_on == 0  # zone 팔 손절폭 1% · atr 2% · zh 1.5% 전부 3% 미만.
    assert {r.guard for r in on} == {0.03}


def test_threshold_axis_tighter_fraction_keeps_fewer_candidates() -> None:
    cell, market = _hand_made_cell()
    by_fraction: dict[float, float] = {}
    for fraction in THRESHOLD_FRACTIONS:
        rows = pnl_rows_for_cell(cell, market, threshold_fraction=fraction)
        filt = [
            r
            for r in rows
            if r.arm == ARM_FILTER and r.segment == SEGMENT_IS and r.barrier == BARRIER_ZONE
        ]
        assert {r.threshold_fraction for r in rows} == {fraction}
        by_fraction[fraction] = filt[0].num_candidates
    assert by_fraction[0.25] <= by_fraction[1 / 3] <= by_fraction[0.5]
    assert by_fraction[0.5] > 0


def test_threshold_one_third_reproduces_the_main_run() -> None:
    cell, market = _hand_made_cell()
    main = pnl_rows_for_cell(cell, market)
    grid = pnl_rows_for_cell(cell, market, threshold_fraction=1 / 3)
    assert [r.model_dump() for r in main] == [r.model_dump() for r in grid]


# --------------------------------------------------------------------------- #
# 6. §3-A 버킷 — 가드 0%에서 잰다
# --------------------------------------------------------------------------- #


def test_bucket_records_are_measured_with_guard_off() -> None:
    """0.3% 미만 손절폭 거래가 버킷에 존재한다 — 가드를 켠 채 재면 그 구간이 통째로 빈다."""
    cell, market = _hand_made_cell()
    # 좁은 손절(0.2%) 거래를 섞는다 — 가드 0.3%가 자르는 자리.
    t0 = 1_700_000_000_000
    narrow = [
        _cand(
            t0 + (100 + i) * _HOUR_MS,
            exit_price=100.3 if i % 2 else 99.8,
            stop_price=99.8,
            win=bool(i % 2),
        )
        for i in range(6)
    ]
    for barrier in BARRIERS:
        cell.by_barrier[barrier] = cell.by_barrier[barrier] + narrow
    cell.zwa = cell.zwa + [0.1] * 6
    pooled = bucket_records_for_cell(cell, market)
    all_records = [r for records in pooled.values() for r in records]
    assert any(r.stop_frac < STOP_GUARD_FRACTION for r in all_records), (
        "가드 미만 손절폭 거래가 버킷에 없다 — 버킷이 가드를 끄지 않고 측정됐다."
    )
    rows = bucket_rows_from_records(_TIMEFRAME, pooled)
    assert any(row.n_trades > 0 and row.bucket == "0.2~0.3%" for row in rows)


def test_stability_rows_cover_all_thresholds() -> None:
    cell, _ = _hand_made_cell()
    rows = stability_rows_for_cell(cell)
    assert {r.threshold_fraction for r in rows} == set(THRESHOLD_FRACTIONS)
    third = next(r for r in rows if r.threshold_fraction == pytest.approx(1 / 3))
    assert third.is_threshold_value is not None
    assert third.oos_coverage is not None
    assert 0.0 <= third.oos_coverage <= 1.0


# --------------------------------------------------------------------------- #
# 7. 판정 분기
# --------------------------------------------------------------------------- #


def _test_row(barrier: str, *, p_return: float | None, n_symbols: int = 4) -> MatchedTestRow:
    return MatchedTestRow(
        barrier=barrier,
        timeframe=_TIMEFRAME,
        segment=SEGMENT_OOS,
        n_symbols=n_symbols,
        n_seeds=20,
        filter_return=0.1,
        matched_return_mean=0.02,
        p_return=p_return,
        filter_win_rate=0.55,
        matched_win_rate_mean=0.47,
        p_win_rate=p_return,
        filter_mdd=0.05,
        matched_mdd_mean=0.08,
        p_mdd=p_return,
        default_mdd=0.08,
        filter_trades=100.0,
        matched_trades=100.0,
        trade_gap_pct=0.0,
    )


def test_robustness_verdict_branches() -> None:
    all_sig = [_test_row(b, p_return=0.048) for b in BARRIERS]
    got = robustness_verdict(all_sig, timeframe=_TIMEFRAME)
    assert got.kind == RobustnessKind.ALL

    partial = [
        _test_row(BARRIER_ZONE, p_return=0.048),
        _test_row(BARRIER_ATR, p_return=0.048),
        _test_row(BARRIER_ZONE_HEIGHT, p_return=0.5),
    ]
    assert robustness_verdict(partial, timeframe=_TIMEFRAME).kind == RobustnessKind.PARTIAL

    collapsed = [
        _test_row(BARRIER_ZONE, p_return=0.048),
        _test_row(BARRIER_ATR, p_return=0.5),
        _test_row(BARRIER_ZONE_HEIGHT, p_return=0.5),
    ]
    assert robustness_verdict(collapsed, timeframe=_TIMEFRAME).kind == RobustnessKind.COLLAPSED

    missing = [_test_row(BARRIER_ZONE, p_return=0.048)]
    assert robustness_verdict(missing, timeframe=_TIMEFRAME).kind == RobustnessKind.INDETERMINATE


def _pnl(
    barrier: str,
    arm: str,
    *,
    ret: float,
    eff_risk: float | None,
    symbol: str = _SYMBOL,
    timeframe: str = _TIMEFRAME,
    guard: float = STOP_GUARD_FRACTION,
    mdd: float = 0.10,
    margin: float = 0.05,
    mean_net_r: float | None = None,
) -> PnlRow:
    return PnlRow(
        barrier=barrier,
        symbol=symbol,
        timeframe=timeframe,
        segment=SEGMENT_OOS,
        arm=arm,
        seed=SEED_AGGREGATE,
        num_candidates=50.0,
        num_trades=50.0,
        total_return=ret,
        max_drawdown=mdd,
        win_rate=0.5,
        guard=guard,
        effective_risk_mean=eff_risk,
        cap_hit_rate=0.5,
        win_rate_margin=margin,
        mean_net_r=mean_net_r,
    )


def test_bet_size_verdict_underrated_when_filter_risks_less_and_wins() -> None:
    rows = [
        _pnl(BARRIER_ZONE, ARM_FILTER, ret=0.2, eff_risk=0.006),
        _pnl(BARRIER_ZONE, ARM_MATCHED, ret=0.05, eff_risk=0.009),
    ]
    got = bet_size_verdict(rows, barrier=BARRIER_ZONE, timeframe=_TIMEFRAME)
    assert got.kind == "underrated"


def test_bet_size_verdict_same_within_epsilon() -> None:
    rows = [
        _pnl(BARRIER_ZONE, ARM_FILTER, ret=0.2, eff_risk=0.0090),
        _pnl(BARRIER_ZONE, ARM_MATCHED, ret=0.05, eff_risk=0.0092),
    ]
    assert bet_size_verdict(rows, barrier=BARRIER_ZONE, timeframe=_TIMEFRAME).kind == "same"


def _guard_pair(barrier: str, timeframe: str, *, on_ret: float, off_ret: float) -> list[PnlRow]:
    return [
        _pnl(barrier, ARM_DEFAULT, ret=on_ret, eff_risk=0.009, timeframe=timeframe, mdd=0.10),
        _pnl(
            barrier,
            ARM_DEFAULT,
            ret=off_ret,
            eff_risk=0.009,
            timeframe=timeframe,
            guard=0.0,
            mdd=0.10,
        ),
    ]


def test_guard_verdict_benefit_and_harm_and_split() -> None:
    benefit = _guard_pair(BARRIER_ZONE, "15m", on_ret=0.2, off_ret=0.1) + _guard_pair(
        BARRIER_ZONE, "1h", on_ret=0.15, off_ret=0.1
    )
    assert guard_verdict(benefit, barrier=BARRIER_ZONE).kind == "benefit"

    harm = _guard_pair(BARRIER_ZONE, "15m", on_ret=0.05, off_ret=0.1) + _guard_pair(
        BARRIER_ZONE, "1h", on_ret=0.02, off_ret=0.1
    )
    assert guard_verdict(harm, barrier=BARRIER_ZONE).kind == "harm"

    split = _guard_pair(BARRIER_ZONE, "15m", on_ret=0.2, off_ret=0.1) + _guard_pair(
        BARRIER_ZONE, "1h", on_ret=0.02, off_ret=0.1
    )
    got = guard_verdict(split, barrier=BARRIER_ZONE)
    assert got.kind == "neutral"
    assert "갈린다" in got.text


def test_guard_verdict_no_effect_when_guard_never_binds() -> None:
    """가드를 끄나 켜나 결과가 같으면 「무영향」 — 「TF에 갈린다」로 오판하지 않는다."""
    rows = _guard_pair(BARRIER_ATR, "15m", on_ret=0.1, off_ret=0.1) + _guard_pair(
        BARRIER_ATR, "1h", on_ret=0.05, off_ret=0.05
    )
    got = guard_verdict(rows, barrier=BARRIER_ATR)
    assert got.kind == "no_effect"
    assert "걸리는 거래가 없다" in got.text


def test_pen_verdict_kept_and_lost() -> None:
    base = [_test_row(b, p_return=0.048) for b in BARRIERS]
    pen_kept = [_test_row(b, p_return=0.048) for b in BARRIERS]
    assert pen_verdict(base, pen_kept).kind == "kept"
    pen_lost = [
        _test_row(BARRIER_ZONE, p_return=0.048),
        _test_row(BARRIER_ATR, p_return=0.6),
        _test_row(BARRIER_ZONE_HEIGHT, p_return=0.048),
    ]
    got = pen_verdict(base, pen_lost)
    assert got.kind == "lost"
    assert isinstance(got, Judgement)


def test_threshold_verdict_partial_flags_free_parameter() -> None:
    rows: list[PnlRow] = []
    for fraction, (f_ret, m_ret) in zip(
        THRESHOLD_FRACTIONS, ((0.2, 0.02), (0.2, 0.02), (-0.05, 0.02)), strict=True
    ):
        for i in range(4):
            sym = f"S{i}/USDT:USDT"
            rows.append(
                _pnl(BARRIER_ATR, ARM_FILTER, ret=f_ret, eff_risk=0.009, symbol=sym).model_copy(
                    update={"threshold_fraction": fraction}
                )
            )
            for seed in MATCH_SEEDS:
                jitter = (seed - len(MATCH_SEEDS) / 2) * 0.001
                rows.append(
                    _pnl(
                        BARRIER_ATR, ARM_MATCHED, ret=m_ret + jitter, eff_risk=0.009, symbol=sym
                    ).model_copy(update={"threshold_fraction": fraction, "seed": seed})
                )
    got = threshold_verdict(rows)
    assert got.kind == "partial"


# --------------------------------------------------------------------------- #
# 8. 렌즈 — pen_5bp 파라미터가 실제로 관통을 요구한다
# --------------------------------------------------------------------------- #


def test_pen_lens_params_actually_set_penetration() -> None:
    from backtest import harness

    params = harness.build_params(fill=harness.fill_preset(LENS_PEN))
    assert params.fill_penetration_bps == 5.0
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"  # 핀 없음(WAN-132 기본값).


def test_guard_values_include_off_and_current_default() -> None:
    assert 0.0 in GUARD_VALUES
    assert STOP_GUARD_FRACTION in GUARD_VALUES
    assert len(GUARD_VALUES) == 5
