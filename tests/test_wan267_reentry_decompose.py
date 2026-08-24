"""WAN-267 재진입 완전 분해의 자(尺)를 동작으로 고정한다.

백테스트 전체는 안 돌린다. 고정하는 함정들:

1. **깊이 라벨** — 재무장 사슬의 재진입에 1-indexed 순번이 붙고, 부모 익절 거래(깊이 0)는
   이 열에 안 들어간다(승자-생존 편향 통제, WAN-149 §4).
2. **진입가 3팔** — freeze(첫가격고정)는 wan228 재무장과 비트 동일, zone·band는 체결 집합이
   달라진다(combine_obs 부류). band는 밴드 필터가 없으면 성립하지 않는다(가드).
3. **상한 재집계** — 격리 손익에서 상한 N = 「깊이 ≤ N 재집계」이고 무제한이 전체 합과 같다.
4. **판정 자** — (a)/(b)/(c)가 행에서 계산되고 20거래 게이트·LOO가 동작한다.
5. **프레임 왕복·append 병합** — 값 보존 · 같은 키는 새 행이 이긴다.
"""

from __future__ import annotations

from pathlib import Path

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan228_reentry_census import _iter_reentries, _Reentry, reentry_events
from backtest.wan267_reentry_decompose import (
    ARMS,
    MIN_TRADES_GATE,
    DepthRow,
    capped_oos_net_by_symbol,
    capped_oos_net_symbol_mean,
    cells_from_csv,
    cells_to_frame,
    depth_profile,
    leave_one_out,
    max_depth,
    merge_append,
    oos_trade_count,
    verdict,
    win_rate,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

HTF_MS = 3_600_000  # 1h


# --------------------------------------------------------------------------- #
# 합성 서브스텝 (깊이·진입가 팔 코어)
# --------------------------------------------------------------------------- #


def _substeps(bars: list[tuple[int, float, float, float]]) -> list[SubStep]:
    out: list[SubStep] = []
    for minute, high, low, close in bars:
        t = minute * 60_000
        out.append(
            SubStep(time=t, high=high, low=low, close=close, htf_bar_time=(t // HTF_MS) * HTF_MS)
        )
    return out


def _long_candidate(*, break_time: int | None = None) -> _Candidate:
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


def _cfg() -> BacktestConfig:
    return harness.build_config("1h")


def _events(
    bars: list[tuple[int, float, float, float]], entry_rule: str = "freeze"
) -> list[tuple[_Candidate, _Reentry]]:
    substeps = _substeps(bars)
    return list(
        _iter_reentries(
            _long_candidate(),
            parent_exit_time=0,
            substeps=substeps,
            substep_times=[s.time for s in substeps],
            htf_times=[0],
            htf_closes=[100.0],
            params=harness.pin_invalidation_cancel(ConfluenceParams()),  # WAN-365 핀(옛 계약)
            cfg=_cfg(),
            funding_rates=None,
            entry_rule=entry_rule,  # type: ignore[arg-type]
        )
    )


def test_depth_labels_are_sequential() -> None:
    """세 번 연속 익절하면 재진입 깊이가 1·2·3으로 붙는다(부모=깊이0은 없다)."""
    bars = [
        (1, 116.0, 114.0, 115.0),
        (10, 101.0, 99.0, 100.5),  # 재진입 1 체결
        (20, 116.0, 108.0, 115.0),  # 익절 → 승
        (30, 101.0, 99.5, 100.2),  # 재진입 2 체결
        (40, 116.0, 108.0, 115.0),  # 익절 → 승
        (50, 101.0, 99.0, 100.0),  # 재진입 3 체결
        (60, 116.0, 108.0, 115.0),  # 익절 → 승
        (70, 130.0, 120.0, 125.0),  # 되돌아오지 않음 → 사슬 끝
    ]
    events = _events(bars)
    assert [re.depth for _c, re in events] == [1, 2, 3]
    assert all(re.is_win for _c, re in events)


def test_depth_zero_never_emitted() -> None:
    """재진입이 없어도(되돌림 없음) 깊이 0 행은 절대 안 나온다."""
    bars = [(1, 116.0, 114.0, 115.0), (10, 130.0, 116.0, 125.0)]
    assert _events(bars) == []


def test_freeze_arm_bit_matches_reentry_events() -> None:
    """freeze 팔은 기존 `reentry_events`(기본 인자)와 값이 같다 — 검산 기준점."""
    bars = [
        (1, 116.0, 114.0, 115.0),
        (10, 101.0, 99.0, 100.5),
        (20, 116.0, 108.0, 115.0),
        (30, 101.0, 99.5, 100.2),
        (40, 95.0, 89.0, 90.5),
    ]
    substeps = _substeps(bars)
    default = reentry_events(
        _long_candidate(),
        parent_exit_time=0,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=harness.pin_invalidation_cancel(ConfluenceParams()),  # WAN-365 핀(옛 계약)
        cfg=_cfg(),
        funding_rates=None,
    )
    freeze = [re for _c, re in _events(bars, entry_rule="freeze")]
    assert [(e.gross_r, e.is_win, e.is_stop, e.depth) for e in default] == [
        (e.gross_r, e.is_win, e.is_stop, e.depth) for e in freeze
    ]


def test_zone_arm_differs_from_freeze() -> None:
    """zone 팔은 존 근단(=105)에 걸어 1R이 커져 freeze와 체결·손익이 갈린다."""
    bars = [
        (1, 116.0, 114.0, 115.0),
        (10, 106.0, 99.0, 100.5),  # 105 터치(zone) · 100 터치(freeze)
        (20, 116.0, 108.0, 115.0),
        (30, 106.0, 99.5, 100.2),
        (40, 95.0, 89.0, 90.5),
    ]
    freeze = [re for _c, re in _events(bars, entry_rule="freeze")]
    zone = [re for _c, re in _events(bars, entry_rule="zone")]
    # 존 근단이 존재하고 두 팔이 다른 손익 경로를 낸다(정확한 값이 아니라 갈림을 고정).
    assert freeze != zone


def test_band_arm_needs_a_filter() -> None:
    """볼린저 필터가 없으면 band 팔은 성립하지 않는다(빈 결과)."""
    substeps = _substeps([(1, 116.0, 114.0, 115.0), (10, 101.0, 99.0, 100.5)])
    params = harness.pin_invalidation_cancel(  # WAN-365 핀(옛 계약)
        ConfluenceParams().model_copy(update={"deviation_filter": None})
    )
    events = list(
        _iter_reentries(
            _long_candidate(),
            parent_exit_time=0,
            substeps=substeps,
            substep_times=[s.time for s in substeps],
            htf_times=[0],
            htf_closes=[100.0],
            params=params,
            cfg=_cfg(),
            funding_rates=None,
            entry_rule="band",
        )
    )
    assert events == []


# --------------------------------------------------------------------------- #
# 집계 (상한 재집계·심볼평균·LOO)
# --------------------------------------------------------------------------- #


def _row(
    *,
    arm: str = "freeze",
    lens: str = "baseline",
    symbol: str = "BTC/USDT",
    tf: str = "1h",
    depth: int,
    oos_n: int = 0,
    oos_wins: int = 0,
    oos_stops: int = 0,
    oos_net: float = 0.0,
    oos_gross: float = 0.0,
) -> DepthRow:
    return DepthRow(
        arm=arm,
        lens=lens,
        symbol=symbol,
        timeframe=tf,
        window_start=0,
        window_end=1_000,
        window_days=1.0,
        adopted_entries=100,
        tp_entries=40,
        depth=depth,
        is_n=0,
        is_wins=0,
        is_stops=0,
        is_gross_r_sum=0.0,
        is_net_pp_sum=0.0,
        oos_n=oos_n,
        oos_wins=oos_wins,
        oos_stops=oos_stops,
        oos_gross_r_sum=oos_gross,
        oos_net_pp_sum=oos_net,
        funding_coverage=1.0,
    )


def test_cap_reaggregation_is_cumulative() -> None:
    """상한 N = 깊이 ≤ N 합 · 무제한 = 전체 합. 단조 부분집합."""
    rows = [
        _row(symbol="BTC/USDT", depth=1, oos_n=10, oos_net=5.0),
        _row(symbol="BTC/USDT", depth=2, oos_n=4, oos_net=2.0),
        _row(symbol="BTC/USDT", depth=3, oos_n=1, oos_net=-1.0),
    ]
    per1 = capped_oos_net_by_symbol(rows, arm="freeze", lens="baseline", timeframe="1h", cap=1)
    per2 = capped_oos_net_by_symbol(rows, arm="freeze", lens="baseline", timeframe="1h", cap=2)
    perinf = capped_oos_net_by_symbol(rows, arm="freeze", lens="baseline", timeframe="1h", cap=None)
    assert per1["BTC/USDT"] == 5.0
    assert per2["BTC/USDT"] == 7.0
    assert perinf["BTC/USDT"] == 6.0
    assert oos_trade_count(rows, arm="freeze", lens="baseline", timeframe="1h", cap=2) == 14
    assert oos_trade_count(rows, arm="freeze", lens="baseline", timeframe="1h") == 15
    assert max_depth(rows) == 3


def test_symbol_mean_over_symbols() -> None:
    rows = [
        _row(symbol="BTC/USDT", depth=1, oos_n=5, oos_net=10.0),
        _row(symbol="ETH/USDT", depth=1, oos_n=5, oos_net=-2.0),
    ]
    mean = capped_oos_net_symbol_mean(rows, arm="freeze", lens="baseline", timeframe="1h", cap=None)
    assert mean == 4.0


def test_leave_one_out_drops_symbol() -> None:
    rows = [
        _row(symbol="BTC/USDT", depth=1, oos_net=10.0),
        _row(symbol="ETH/USDT", depth=1, oos_net=-4.0),
    ]
    # ETH를 빼면 BTC만 남아 +10.
    assert leave_one_out(rows, arm="freeze", lens="baseline", timeframe="1h", drop="ETH") == 10.0


def test_depth_profile_conditional_win_rate() -> None:
    rows = [
        _row(depth=1, oos_n=10, oos_wins=4, oos_stops=6, oos_gross=1.0),
        _row(depth=2, oos_n=4, oos_wins=1, oos_stops=3, oos_gross=-2.0),
    ]
    prof = depth_profile(rows, arm="freeze", lens="baseline", timeframe="1h")
    assert prof[0][0] == 1 and prof[0][1] == 10
    assert win_rate(prof[0][2], prof[0][3]) == 0.4
    assert win_rate(prof[1][2], prof[1][3]) == 0.25


def test_verdict_gates_on_sample() -> None:
    """OOS 재진입 표본이 게이트 미만이면 판정 불가를 찍는다."""
    rows = [_row(arm=a, depth=1, oos_n=3, oos_net=1.0) for a in ARMS]
    text = verdict(rows)
    assert "판정 불가" in text
    assert str(MIN_TRADES_GATE) in text


def test_verdict_names_leader_when_gated() -> None:
    """표본이 게이트를 넘으면 리더 팔을 짚는다(zone이 가장 높게)."""
    rows: list[DepthRow] = []
    for a, net in (("freeze", 5.0), ("zone", 12.0), ("band", 3.0)):
        rows.append(
            _row(
                arm=a, symbol="BTC/USDT", depth=1, oos_n=25, oos_wins=15, oos_stops=10, oos_net=net
            )
        )
    text = verdict(rows)
    assert "zone(존근단)" in text


def test_frame_roundtrip(tmp_path: Path) -> None:
    rows = [_row(arm=a, depth=d, oos_n=d, oos_net=float(d)) for a in ARMS for d in (1, 2)]
    path = tmp_path / "wan267.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    back = cells_from_csv(path)
    assert len(back) == len(rows)
    assert {(r.arm, r.depth) for r in back} == {(r.arm, r.depth) for r in rows}


def test_merge_append_new_wins() -> None:
    old = [_row(arm="freeze", tf="4h", depth=1, oos_net=1.0)]
    fresh = [
        _row(arm="freeze", tf="4h", depth=1, oos_net=9.0),  # 같은 키 → 덮어씀
        _row(arm="freeze", tf="15m", depth=1, oos_net=2.0),  # 새 TF → 추가
    ]
    merged = merge_append(old, fresh)
    by_tf = {(r.timeframe, r.depth): r.oos_net_pp_sum for r in merged}
    assert by_tf[("4h", 1)] == 9.0
    assert by_tf[("15m", 1)] == 2.0
    assert len(merged) == 2
