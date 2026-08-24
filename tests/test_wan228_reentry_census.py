"""WAN-228 (B) 재진입 census의 자(尺)를 동작으로 고정한다.

백테스트 전체는 안 돌린다 — 재무장 루프·판정·CSV 왕복·파생 속성은 합성 값으로 검증하고,
재무장이 실제로 엔진 시뮬레이터를 타는지는 통제된 서브스텝으로 못 박는다. 고정하는 함정들:

1. **재무장 루프** — 익절 후 되돌아온 지정가가 다시 체결되면 재진입 1건, 익절이면 또
   무장, 손절(존 무효화)·미체결·데이터끝이면 멈춘다(손절 뒤엔 재무장하지 않는다).
2. **지정가 고정** — 재진입 문턱은 원래 체결가이고, 되돌아오지 않으면 0건이다.
3. **무효화 컷** — `break_time` 이후 서브스텝에선 재무장하지 않는다.
4. **판정 두 자** — (B) 비율 **그리고** 따뜻한 OOS 순수익이 GO/STOP을 함께 가른다
   (문턱이 문장이 아니라 코드 상수 · (a)/(b)/(c)가 실제로 갈린다).
5. **파생 속성·CSV 왕복** — 비율·평균R·승률과 프레임 왕복이 값을 보존한다.
"""

from __future__ import annotations

from pathlib import Path

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan228_reentry_census import (
    MATERIAL_RETURN_DELTA_PCT,
    NEGLIGIBLE_MISS_SHARE,
    SIGNIFICANT_MISS_SHARE,
    CellRow,
    _direction,
    aggregate_symbol_mean,
    cells_from_csv,
    cells_to_frame,
    reentry_candidates,
    reentry_events,
    verdict,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

HTF_MS = 3_600_000  # 1h
WINDOW = (0, 1_000_000_000)


# --------------------------------------------------------------------------- #
# 합성 서브스텝 재무장 루프 (§1/§2 코어)
# --------------------------------------------------------------------------- #


def _substeps(bars: list[tuple[int, float, float, float]]) -> list[SubStep]:
    """(분오프셋, high, low, close) → SubStep 리스트. htf_bar_time은 분오프셋으로 슬롯팅."""
    out: list[SubStep] = []
    for minute, high, low, close in bars:
        t = minute * 60_000
        out.append(
            SubStep(time=t, high=high, low=low, close=close, htf_bar_time=(t // HTF_MS) * HTF_MS)
        )
    return out


def _long_candidate(*, break_time: int | None) -> _Candidate:
    """롱 셋업: 지정가 100 · 손절 90(1R=10) · 익절 목표 115(1.5R). 근거 존 하나."""
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=105.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=1.5,
        break_time=break_time,
    )
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=100.0,
        exit_time=0,
        exit_price=115.0,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=90.0,
        order_block=ob,
    )


def _params() -> ConfluenceParams:
    # 채택 게이트(unconditional)라 RSI 값은 안 본다.
    # WAN-365: 이 픽스처는 **소급 취소 시절의 재무장 계약**을 검정한다(WAN-228/261/267/269).
    # 채택 기본값은 인과(`bar_close`)라 무효화 컷오프가 한 봉 뒤로 밀리므로, 팔을 **명시**한다
    # — 안 밝히면 「옛 계약」 라벨을 단 채 새 엔진으로 도는 그 실패가 테스트에서 재현된다.
    return harness.pin_invalidation_cancel(ConfluenceParams())


def _cfg() -> BacktestConfig:
    return harness.build_config("1h")


def test_rearm_loop_counts_win_then_stop() -> None:
    """익절 후 되돌아와 체결→익절(재진입 1, 승) → 다시 되돌아와 손절(재진입 2, 손절).

    손절(존 무효화)이 나면 재무장을 멈춘다 = 정확히 2건.
    """
    # 부모 익절은 t=0(가격 115). 이후:
    #   t=10 지정가 100 터치(체결) → t=20 익절 115 도달(승)
    #   t=30 지정가 100 재터치(체결) → t=40 손절 90 도달(패) → 존 사망
    #   t=50 이후 또 100을 쳐도 재무장 안 함(손절로 끝났으므로)
    bars = [
        (1, 116.0, 114.0, 115.0),  # 부모 익절 직후, 아직 위
        (10, 101.0, 99.0, 100.5),  # 지정가 100 터치 → 재진입 1 체결
        (20, 116.0, 108.0, 115.0),  # 익절 115 도달 → 승
        (30, 101.0, 99.5, 100.2),  # 지정가 100 재터치 → 재진입 2 체결
        (40, 95.0, 89.0, 90.5),  # 손절 90 도달 → 패 · 존 사망
        (50, 101.0, 99.0, 100.0),  # 다시 100을 쳐도 재무장 없음
    ]
    substeps = _substeps(bars)
    events = reentry_events(
        _long_candidate(break_time=None),
        parent_exit_time=0,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=_params(),
        cfg=_cfg(),
        funding_rates=None,
    )
    assert len(events) == 2
    assert events[0].is_win and not events[0].is_stop
    assert events[0].gross_r == 1.5  # 익절 = +1.5R
    assert events[1].is_stop and not events[1].is_win
    assert events[1].gross_r == -1.0  # 손절 = −1R


def test_no_return_no_reentry() -> None:
    """가격이 지정가로 되돌아오지 않으면 재진입 0건(지정가는 원래 체결가로 고정)."""
    bars = [
        (1, 116.0, 114.0, 115.0),
        (10, 130.0, 116.0, 125.0),  # 계속 위에서만 논다 — 100을 안 친다
        (20, 140.0, 120.0, 135.0),
    ]
    substeps = _substeps(bars)
    events = reentry_events(
        _long_candidate(break_time=None),
        parent_exit_time=0,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=_params(),
        cfg=_cfg(),
        funding_rates=None,
    )
    assert events == []


def test_invalidation_cuts_rearm() -> None:
    """`break_time` 이후 서브스텝에선 재무장하지 않는다 — 그 전에 닿아야만 센다."""
    # 지정가 터치가 break_time(t=15분 = 900_000ms) 이후에만 온다 → 재진입 0.
    bars = [
        (1, 116.0, 114.0, 115.0),
        (20, 101.0, 99.0, 100.0),  # break_time 이후 터치 → 무시
    ]
    substeps = _substeps(bars)
    events = reentry_events(
        _long_candidate(break_time=15 * 60_000),
        parent_exit_time=0,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=_params(),
        cfg=_cfg(),
        funding_rates=None,
    )
    assert events == []


def test_direction_mapping() -> None:
    assert _direction(PositionSide.LONG) is OrderBlockDirection.BULLISH
    assert _direction(PositionSide.SHORT) is OrderBlockDirection.BEARISH


# --------------------------------------------------------------------------- #
# 재진입 × 반익절 래더 배선 (WAN-345)
# --------------------------------------------------------------------------- #
#
# 🚨 **라벨이 아니라 동작으로 건다.** WAN-323 커밋 `af1a550`이 `reentry_candidates`의
# 시그니처만 넓히고 `_iter_reentries` 호출에 세 인자를 안 넘겨, 래더를 켠 북 팔에서도
# **재진입 거래만 조용히 전량 익절**로 돌았다. 「인자를 넘기는가」만 보는 테스트는 같은
# 실패를 또 통과시키므로, 여기서는 **재진입 거래에 부분 청산이 실제로 생기는지** ·
# **본절 스탑이 실제로 움직이는지**로 잠근다.

# 재진입 시나리오: 부모가 t=0에 익절(115) → 지정가 100으로 재무장.
#   t=10  100 터치 → 재진입 체결(1R = 100−90 = 10)
#   t=20  110 도달 → 분할 지점(1.0R). 익절 115는 아직 안 닿았다.
#   t=30  진입가 100까지 되돌림(저가 99.5) — 손절 90은 안 닿는다.
#   t=40  115 도달
# 래더 off면 t=40에 전량 익절이고, `breakeven_after_partial`을 켜면 t=30에 본절로 끝난다.
_LADDER_BARS = [
    (1, 116.0, 114.0, 115.0),
    (10, 101.0, 99.0, 100.5),
    (20, 111.0, 105.0, 110.5),
    (30, 105.0, 99.5, 100.0),
    (40, 116.0, 108.0, 115.0),
]


def _ladder_candidates(**ladder: object) -> list[_Candidate]:
    substeps = _substeps(_LADDER_BARS)
    return reentry_candidates(
        _long_candidate(break_time=None),
        parent_exit_time=0,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=_params(),
        cfg=_cfg(),
        funding_rates=None,
        **ladder,  # type: ignore[arg-type]
    )


def test_reentry_trades_actually_get_the_ladder() -> None:
    """래더를 켜면 재진입 거래에 **부분 청산이 실제로 생긴다**(WAN-345 회귀).

    고치기 전에는 `partial_exits`가 빈 튜플이라 북이 전량 익절로 배치했다.
    """
    cands = _ladder_candidates(partial_take_profit_r=1.0, partial_take_profit_fraction=0.5)
    assert len(cands) == 1
    partials = cands[0].partial_exits
    assert len(partials) == 1, "재진입 거래가 래더를 못 받았다 — 전량 익절로 돌고 있다"
    assert partials[0].price == 110.0  # 진입 100 + 1.0R(=10)
    assert partials[0].fraction == 0.5
    assert partials[0].time == 20 * 60_000
    # 잔량은 목표까지 끌려가 t=40에 익절한다(분할이 청산 시각을 안 당긴다).
    assert cands[0].exit_price == 115.0
    assert cands[0].exit_time == 40 * 60_000


def test_ladder_off_is_the_old_behaviour() -> None:
    """기본값(래더 끔)에서는 부분 청산이 없다 — 래더를 안 쓰는 북 CSV가 비트 재현된다."""
    default = _ladder_candidates()
    explicit_off = _ladder_candidates(partial_take_profit_r=None)
    assert [c.partial_exits for c in default] == [()]
    assert default == explicit_off
    assert default[0].exit_price == 115.0  # 전량 익절 = 예전 그대로


def test_breakeven_after_partial_moves_the_reentry_stop() -> None:
    """분할 후 본절 스탑도 재진입 거래에 걸린다 — 켜고 끄면 **청산 자체가 달라진다**.

    비율(`fraction`)과 달리 본절은 청산 시각·가격을 바꾸므로, 이 팔이 갈리지 않으면
    세 인자 중 `breakeven_after_partial`만 조용히 버려져도 안 걸린다.
    """
    on = _ladder_candidates(partial_take_profit_r=1.0, breakeven_after_partial=True)
    off = _ladder_candidates(partial_take_profit_r=1.0, breakeven_after_partial=False)

    # 켜면 t=20 분할 → t=30 진입가(100)로 옮긴 손절에 걸려 본절 청산.
    assert len(on) == 1
    assert on[0].exit_time == 30 * 60_000
    assert on[0].exit_price == 100.0
    assert on[0].exit_at_breakeven is True
    # 끄면 손절이 90에 남아 t=30을 버티고 t=40에 익절한다.
    assert off[0].exit_time == 40 * 60_000
    assert off[0].exit_price == 115.0
    assert off[0].exit_at_breakeven is False


# --------------------------------------------------------------------------- #
# 판정 (§3) · 파생 속성 · CSV 왕복
# --------------------------------------------------------------------------- #


def _cell(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    adopted_entries: int = 100,
    tp_entries: int = 50,
    reentries_total: int = 30,
    re_oos_n: int = 15,
    re_oos_wins: int = 8,
    re_oos_stops: int = 5,
    re_oos_gross_r_sum: float = 5.0,
    re_oos_net_pp_sum: float = 5.0,
) -> CellRow:
    return CellRow(
        symbol=symbol,
        timeframe=timeframe,
        window_start=WINDOW[0],
        window_end=WINDOW[1],
        window_days=100.0,
        adopted_entries=adopted_entries,
        tp_entries=tp_entries,
        reentries_total=reentries_total,
        re_is_n=reentries_total - re_oos_n,
        re_is_wins=0,
        re_is_stops=0,
        re_is_gross_r_sum=0.0,
        re_is_net_pp_sum=0.0,
        re_oos_n=re_oos_n,
        re_oos_wins=re_oos_wins,
        re_oos_stops=re_oos_stops,
        re_oos_gross_r_sum=re_oos_gross_r_sum,
        re_oos_net_pp_sum=re_oos_net_pp_sum,
        funding_coverage=1.0,
    )


def test_derived_properties() -> None:
    row = _cell(
        adopted_entries=100,
        tp_entries=40,
        reentries_total=30,
        re_oos_n=10,
        re_oos_wins=6,
        re_oos_stops=4,
        re_oos_gross_r_sum=5.0,
    )
    assert row.reentry_share == 0.30
    assert row.reentries_per_tp == 30 / 40
    assert row.gross_r_mean_oos == 0.5
    assert row.win_rate_oos == 0.6  # 6 / (6+4), 데이터끝 보유는 분모에서 빠진다
    # 진입 0 방어
    assert _cell(adopted_entries=0, tp_entries=0, reentries_total=0).reentry_share is None
    assert _cell(tp_entries=0).reentries_per_tp is None


def test_verdict_go() -> None:
    # 비율 큰(≥20%) + 따뜻한 OOS 순수익 큰(≥1%p) → GO.
    rows = [_cell(adopted_entries=100, reentries_total=30, re_oos_net_pp_sum=5.0)]
    assert verdict(rows).startswith("**(a) GO")


def test_verdict_stop_small_share() -> None:
    rows = [_cell(adopted_entries=100, reentries_total=3, re_oos_net_pp_sum=5.0)]
    out = verdict(rows)
    assert out.startswith("**(b) STOP")


def test_verdict_stop_immaterial_return() -> None:
    # 비율은 크지만 수익이 안 붙으면 STOP(WAN-222 함정).
    rows = [_cell(adopted_entries=100, reentries_total=40, re_oos_net_pp_sum=0.2)]
    assert verdict(rows).startswith("**(b) STOP")


def test_verdict_borderline() -> None:
    # 5%~20% 사이 + 수익은 유의 → 경계.
    rows = [_cell(adopted_entries=100, reentries_total=10, re_oos_net_pp_sum=5.0)]
    assert verdict(rows).startswith("**(c)")


def test_thresholds_are_constants() -> None:
    assert 0.0 < NEGLIGIBLE_MISS_SHARE < SIGNIFICANT_MISS_SHARE < 1.0
    assert MATERIAL_RETURN_DELTA_PCT > 0.0


def test_aggregate_symbol_mean() -> None:
    rows = [_cell(re_oos_net_pp_sum=4.0), _cell(re_oos_net_pp_sum=6.0)]
    assert aggregate_symbol_mean(rows, "re_oos_net_pp_sum") == 5.0
    assert aggregate_symbol_mean([], "re_oos_net_pp_sum") == 0.0


def test_frame_roundtrip(tmp_path: Path) -> None:
    rows = [
        _cell(symbol="BTC/USDT:USDT", timeframe="4h"),
        _cell(symbol="ETH/USDT:USDT", timeframe="1h", re_oos_net_pp_sum=-3.0),
    ]
    path = tmp_path / "wan228.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    restored = cells_from_csv(path)
    assert len(restored) == 2
    assert {r.symbol for r in restored} == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    assert restored[0].reentry_share == rows[0].reentry_share
    assert restored[1].re_oos_net_pp_sum == -3.0
