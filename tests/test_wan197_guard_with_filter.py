"""WAN-197 손절폭 가드 × 존폭 필터 재측정 — 라벨이 아니라 동작인가.

이 파일이 **동작으로** 고정하는 것:

1. **필터가 실제로 켜져 있다** — 채택 기본값 `params.max_zone_width_atr == 1.28`이고,
   실데이터에서 필터 켠 후보가 끈 후보보다 **적다**. 라벨만 「필터 켜짐」이고 조용히 1.28을
   안 태우는 것이 이 저장소의 반복 사고다(WAN-91/95/112/123/159).
2. **`default` 팔이 곧 채택 엔진이다** — 후보 전체를 가드 0.3%로 시퀀싱한 수익이
   `harness.run_once`(프로덕션 경로)와 **비트 단위로 같다**. 이 표가 「인자 없는
   backtest.run」과 같은 엔진이라는 직접 증거다(실데이터).
3. **가드 축이 실제로 시퀀싱을 바꾼다** — 가드를 올리면 좁은 손절 거래가 사라지고
   (단조 비증가), 가드 탈락률이 손으로 센 값과 일치한다.
4. **IS/OOS를 탭 시각으로 가른다** — 경계 전 후보는 IS, 이후는 OOS.
5. **`symbol_mean`이 거래 20건 미만 셀을 뺀다**(TRX류 붕괴 셀이 평균을 오염시키지 않는다).
6. **판정 분기** — (a) 이득 / (b) 손해 / (c) 무영향·갈림 / 판정 불가가 의도한 입력에서
   나온다(문장 부분문자열 분기 금지 — WAN-142 교훈).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import SEGMENT_IS, SEGMENT_OOS, MarketData, load_market_data
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.wan133_geometry_vs_selection import STOP_GUARD_FRACTION
from backtest.wan152_selection_vs_geometry import trade_stats
from backtest.wan154_stop_width_audit import GUARD_VALUES
from backtest.wan197_guard_with_filter import (
    GuardKind,
    GuardRow,
    guard_reject_rate,
    guard_rows_for_cell,
    guard_verdict,
    is_boundary_ms,
    production_candidates,
    symbol_mean,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"
_HOUR_MS = 3_600_000

# 실데이터 교차검산 창 — 채택 창 전체(6년)는 1분봉 로딩이 무거워 1년으로 좁힌다.
# 검산 불변식(후보 전체 가드 0.3% == run_once)은 창과 무관하다.
_XCHK_START = "2025-07-01"
_XCHK_END = "2026-07-01"


def _empty_market() -> MarketData:
    return MarketData(_SYMBOL, _TIMEFRAME, pd.DataFrame(), pd.DataFrame(), [])


def _cand(entry: int, *, exit_price: float, stop_price: float, win: bool) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry,
        entry_price=100.0,
        exit_time=entry + _HOUR_MS // 2,
        exit_price=exit_price,
        reason=ExitReason.TAKE_PROFIT if win else ExitReason.STOP_LOSS,
        stop_price=stop_price,
        trigger_time=entry,
    )


# --------------------------------------------------------------------------- #
# 1. 가드 탈락률 · 가드 축이 동작이다
# --------------------------------------------------------------------------- #


def test_guard_reject_rate_hand_computed() -> None:
    """손절폭 0.2% · 0.5% · 1.5% 세 후보 — 가드가 그 아래를 후보 단위로 센다."""
    cands = [
        _cand(0, exit_price=100.3, stop_price=99.8, win=True),  # 0.2%
        _cand(1, exit_price=100.75, stop_price=99.5, win=True),  # 0.5%
        _cand(2, exit_price=102.25, stop_price=98.5, win=True),  # 1.5%
    ]
    assert guard_reject_rate(cands, 0.0) == pytest.approx(0.0)
    assert guard_reject_rate(cands, 0.003) == pytest.approx(1 / 3)  # 0.2%만
    assert guard_reject_rate(cands, 0.006) == pytest.approx(2 / 3)  # 0.2% · 0.5%
    assert guard_reject_rate(cands, 0.02) == pytest.approx(1.0)
    assert guard_reject_rate([], 0.003) is None


def test_guard_sweep_is_monotone_non_increasing_trades() -> None:
    """가드를 올리면 거래 수가 줄거나 같다(늘지 않는다) · 0.3% 행이 격자 안에 있다."""
    t0 = 1_700_000_000_000
    # 손절폭이 서로 다른 후보들(0.2% ~ 2%)을 IS 구간에 몰아 넣는다.
    cands = [
        _cand(t0 + i * _HOUR_MS, exit_price=100.0 + d * 1.5, stop_price=100.0 - d, win=True)
        for i, d in enumerate([0.2, 0.35, 0.5, 0.8, 1.2, 2.0])
    ]
    market = _empty_market()
    boundary = t0 + 100 * _HOUR_MS  # 전부 IS
    rows = guard_rows_for_cell(market, cands, boundary, GUARD_VALUES)
    is_rows = sorted((r for r in rows if r.segment == SEGMENT_IS), key=lambda r: r.guard)
    assert {r.guard for r in is_rows} == set(GUARD_VALUES)
    trades = [r.num_trades for r in is_rows]
    # 가드↑ → 거래 비증가(pairwise라 길이가 1 다르므로 strict=False)
    assert all(a >= b for a, b in zip(trades, trades[1:], strict=False)), trades
    assert trades[0] >= trades[-1]  # 가드 0 ≥ 가드 최대


def test_guard_rows_split_is_oos_by_trigger_time() -> None:
    t0 = 1_700_000_000_000
    cands = [
        _cand(t0 + i * _HOUR_MS, exit_price=101.5, stop_price=99.0, win=True) for i in range(10)
    ]
    boundary = t0 + 6 * _HOUR_MS  # 앞 6개 IS, 뒤 4개 OOS
    rows = guard_rows_for_cell(
        market=_empty_market(), cands=cands, is_boundary=boundary, guards=(0.0,)
    )
    is_row = next(r for r in rows if r.segment == SEGMENT_IS)
    oos_row = next(r for r in rows if r.segment == SEGMENT_OOS)
    assert is_row.num_candidates == 6.0
    assert oos_row.num_candidates == 4.0


# --------------------------------------------------------------------------- #
# 2. 필터가 실제로 켜져 있다
# --------------------------------------------------------------------------- #


def test_adopted_params_have_filter_on() -> None:
    """채택 기본값의 존폭 필터가 1.28로 켜져 있다 — 라벨이 아니라 값."""
    assert harness.build_params().max_zone_width_atr == 1.28
    assert ConfluenceParams().max_zone_width_atr == 1.28


# --------------------------------------------------------------------------- #
# 3. 집계 — 20건 미만 제외
# --------------------------------------------------------------------------- #


def _grow(
    symbol: str,
    *,
    guard: float,
    ret: float,
    mdd: float = 0.10,
    trades: float = 50.0,
    timeframe: str = _TIMEFRAME,
    segment: str = SEGMENT_OOS,
) -> GuardRow:
    return GuardRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        guard=guard,
        num_candidates=trades,
        num_trades=trades,
        total_return=ret,
        max_drawdown=mdd,
        win_rate=0.5,
    )


def test_symbol_mean_excludes_small_cells() -> None:
    rows = [
        _grow("A/USDT:USDT", guard=0.003, ret=0.2, trades=50),
        _grow("B/USDT:USDT", guard=0.003, ret=0.1, trades=50),
        _grow("C/USDT:USDT", guard=0.003, ret=-5.0, trades=5),  # 붕괴 셀 — 제외돼야 한다
    ]
    m = symbol_mean(rows, timeframe=_TIMEFRAME, segment=SEGMENT_OOS, guard=0.003)
    assert m["n_symbols"] == 2.0
    assert m["n_excluded"] == 1.0
    assert m["total_return"] == pytest.approx(0.15)  # C가 평균을 오염시키지 않는다


# --------------------------------------------------------------------------- #
# 4. 판정 분기
# --------------------------------------------------------------------------- #


def _pair(
    tf: str, *, on: float, off: float, mdd_on: float = 0.10, mdd_off: float = 0.10
) -> list[GuardRow]:
    """세 심볼 × (가드 0.3% · 끔) — 판정이 유효 심볼 3개를 요구한다."""
    rows: list[GuardRow] = []
    for i in range(3):
        sym = f"S{i}/USDT:USDT"
        rows.append(_grow(sym, guard=STOP_GUARD_FRACTION, ret=on, mdd=mdd_on, timeframe=tf))
        rows.append(_grow(sym, guard=0.0, ret=off, mdd=mdd_off, timeframe=tf))
    return rows


def test_guard_verdict_benefit() -> None:
    got = guard_verdict(_pair("15m", on=0.20, off=0.10), timeframe="15m")
    assert got.kind == GuardKind.BENEFIT


def test_guard_verdict_harm() -> None:
    got = guard_verdict(_pair("1h", on=0.05, off=0.10), timeframe="1h")
    assert got.kind == GuardKind.HARM


def test_guard_verdict_no_effect() -> None:
    got = guard_verdict(_pair("1h", on=0.10, off=0.10), timeframe="1h")
    assert got.kind == GuardKind.NEUTRAL
    assert "무영향" in got.text


def test_guard_verdict_split_direction() -> None:
    """수익은 오르는데 위험조정은 내려가면 (c) 방향 갈림."""
    got = guard_verdict(_pair("15m", on=0.15, off=0.10, mdd_on=0.30, mdd_off=0.10), timeframe="15m")
    assert got.kind == GuardKind.NEUTRAL
    assert "갈림" in got.text


def test_guard_verdict_indeterminate_when_too_few_symbols() -> None:
    rows = [
        _grow("A/USDT:USDT", guard=STOP_GUARD_FRACTION, ret=0.2),
        _grow("A/USDT:USDT", guard=0.0, ret=0.1),
        _grow("B/USDT:USDT", guard=STOP_GUARD_FRACTION, ret=0.2),
        _grow("B/USDT:USDT", guard=0.0, ret=0.1),
    ]
    got = guard_verdict(rows, timeframe=_TIMEFRAME)
    assert got.kind == GuardKind.INDETERMINATE


# --------------------------------------------------------------------------- #
# 5. 실데이터 교차검산 — CI에서는 skip
# --------------------------------------------------------------------------- #


@pytest.fixture
def _btc_market() -> MarketData:
    start_ms, end_ms = parse_date_ms(_XCHK_START), parse_date_ms(_XCHK_END)
    market = load_market_data(_SYMBOL, _TIMEFRAME, start_ms=start_ms, end_ms=end_ms, need_1m=True)
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터가 없어 교차검산을 건너뜁니다(CI 기본).")
    return market


def test_full_list_guard_default_reproduces_production(_btc_market: MarketData) -> None:
    """후보 전체를 가드 0.3%로 시퀀싱한 수익 == `run_once` 프로덕션(비트 일치).

    이 표의 `default` 팔이 곧 「인자 없는 backtest.run」임을 실데이터로 고정한다.
    """
    params = harness.build_params()
    cands = production_candidates(_btc_market, params)
    s = trade_stats(list(cands), _btc_market, _TIMEFRAME, guard=STOP_GUARD_FRACTION)
    cfg = harness.build_config(_TIMEFRAME)
    out = harness.run_once(_btc_market, params=params, cfg=cfg)
    pm = out.result.metrics
    assert s.total_return == pytest.approx(pm.total_return, abs=1e-9)
    assert s.num_trades == pm.num_trades
    assert s.max_drawdown == pytest.approx(pm.max_drawdown, abs=1e-9)
    assert s.win_rate == pytest.approx(pm.win_rate, abs=1e-9)


def test_filter_on_yields_fewer_candidates_than_off(_btc_market: MarketData) -> None:
    """필터 1.28이 실제로 후보를 걷어낸다 — 끈 것보다 켠 것이 적다(조용한 라벨 방지)."""
    on = production_candidates(_btc_market, harness.build_params())
    off = production_candidates(_btc_market, harness.build_params(max_zone_width_atr=None))
    assert len(off) > len(on) > 0
    assert is_boundary_ms(_btc_market.htf_df) > int(_btc_market.htf_df["open_time"].iloc[0])
