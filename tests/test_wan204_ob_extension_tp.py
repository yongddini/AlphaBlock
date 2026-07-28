"""WAN-204 격자의 계약 테스트 — OB 연장 익절·판정·검산이 라벨이 아니라 동작이다.

고정하는 것:

* **연장 오버라이드의 산식** — `max(진입가+1.5R, OB 윗경계)`(롱)/`min(…, OB 아랫경계)`(숏).
  진입가=OB 윗경계면 floor(1.5R)가 이기고(연장 0), OB 윗경계가 더 멀면 거기로 연장한다.
* **override=None 팔이 표준 엔진(`harness.run_once`)과 비트 단위로 같다**(실데이터 회귀).
* **집계·판정 문장이 표의 숫자에서 실제로 계산된다** — (a)/(b)/(c) 분기·거래 수 강등·
  편중 leave-one-out.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import (
    BASELINE_FILL,
    SEGMENT_OOS_WARM,
    build_config,
    build_params,
    detect_order_blocks,
    eval_boundary_ms,
    load_market_data,
    normalize_symbol,
    run_once,
    segments_for,
    slice_market,
)
from backtest.run import parse_date_ms
from backtest.wan204_ob_extension_tp import (
    ARM_EXTEND,
    ARM_FIXED,
    VerdictKind,
    Wan204Row,
    leave_one_out,
    make_ob_extension_override,
    pooled,
    rows_to_frame,
    run_cell,
    tf_verdict,
    trade_gap,
)
from backtest.zone_limit_backtest import TakeProfitContext
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "4h"
_START = "2024-01-01"
_END = "2026-07-22"


# --------------------------------------------------------------------------- #
# 연장 오버라이드 산식
# --------------------------------------------------------------------------- #


def _ob(top: float = 110.0, bottom: float = 100.0) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )


def _ctx(
    entry_price: float,
    stop_price: float,
    *,
    top: float,
    bottom: float = 100.0,
    is_long: bool = True,
) -> TakeProfitContext:
    return TakeProfitContext(
        is_long=is_long,
        entry_price=entry_price,
        stop_price=stop_price,
        trigger_time=0,
        order_block=_ob(top=top, bottom=bottom),
    )


def test_extension_reaches_the_ob_boundary_when_it_is_farther() -> None:
    """OB 윗경계가 1.5R 목표보다 멀면 거기까지 연장한다."""
    override = make_ob_extension_override(ConfluenceParams())
    # entry=101 stop=100 → 1R=1 → floor=102.5; OB top=110 → 연장 목표 110.
    assert override(_ctx(101.0, 100.0, top=110.0)) == 110.0


def test_extension_floors_at_1_5r_when_boundary_is_closer() -> None:
    """OB 윗경계가 1.5R보다 가까우면 floor(1.5R)가 이긴다 — 최소 1.5R 보장."""
    override = make_ob_extension_override(ConfluenceParams())
    # entry=101 stop=100 → floor=102.5; OB top=102 → max = 102.5.
    assert override(_ctx(101.0, 100.0, top=102.0)) == pytest.approx(102.5)


def test_proximal_fill_gives_exactly_the_floor() -> None:
    """근단 체결(진입가=OB 윗경계)이면 max가 floor를 준다 — 「내부체결만」과 「전체」가 동치."""
    override = make_ob_extension_override(ConfluenceParams())
    # entry=110(=top) stop=100 → 1R=10 → floor=125; OB top=110 → max=125(floor).
    assert override(_ctx(110.0, 100.0, top=110.0)) == pytest.approx(125.0)


def test_extension_is_mirrored_for_shorts() -> None:
    override = make_ob_extension_override(ConfluenceParams())
    # 숏 entry=99 stop=100 → floor=99-1.5=97.5; OB bottom=90 → min=90.
    ctx = _ctx(99.0, 100.0, top=100.0, bottom=90.0, is_long=False)
    assert override(ctx) == 90.0


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _row(
    *,
    symbol: str,
    arm: str,
    total_return: float,
    segment: str = SEGMENT_OOS_WARM,
    fill: str = "baseline",
    num_trades: int = 50,
    ret_over_mdd_num: float | None = None,
    mean_net_r: float = 0.1,
    max_drawdown: float = 0.1,
) -> Wan204Row:
    return Wan204Row(
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        arm=arm,
        fill=fill,
        eligible=100,
        filled=80,
        num_trades=num_trades,
        fill_rate=0.8,
        total_return=total_return,
        max_drawdown=max_drawdown,
        win_rate=0.5,
        sharpe=None,
        mean_gross_r=0.1,
        mean_net_r=mean_net_r,
        net_r_win=0.5,
        net_r_loss=-0.5,
        hold_bars_median=3.0,
        n_take_profit=20,
        n_stop_loss=30,
        n_end_of_data=0,
    )


def _grid(specs: dict[tuple[str, str], tuple[float, float, float]]) -> pd.DataFrame:
    """specs: (arm, symbol) → (total_return, max_drawdown, mean_net_r)."""
    rows = [
        _row(
            symbol=symbol,
            arm=arm,
            total_return=ret,
            max_drawdown=mdd,
            mean_net_r=nr,
        )
        for (arm, symbol), (ret, mdd, nr) in specs.items()
    ]
    return rows_to_frame(rows)


def _two_symbol_grid(
    fixed: tuple[float, float, float], extend: tuple[float, float, float]
) -> pd.DataFrame:
    """판정 게이트(유효 심볼 3개)를 넘기려고 3심볼로 구성한다."""
    specs: dict[tuple[str, str], tuple[float, float, float]] = {}
    for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"):
        specs[(ARM_FIXED, symbol)] = fixed
        specs[(ARM_EXTEND, symbol)] = extend
    return _grid(specs)


def test_verdict_extend_wins_when_all_three_metrics_favor_it() -> None:
    frame = _two_symbol_grid(fixed=(0.05, 0.10, 0.10), extend=(0.08, 0.08, 0.15))
    v = tf_verdict(frame, "1h")
    assert v.kind is VerdictKind.EXTEND
    assert v.text.startswith("**1h**: (a)")


def test_verdict_fixed_wins_when_all_three_metrics_favor_it() -> None:
    frame = _two_symbol_grid(fixed=(0.08, 0.08, 0.15), extend=(0.05, 0.12, 0.10))
    assert tf_verdict(frame, "1h").kind is VerdictKind.FIXED


def test_verdict_mixed_when_metrics_split() -> None:
    # 수익은 연장이 높지만 위험조정(수익/MDD)은 현행이 높다 → (c).
    frame = _two_symbol_grid(fixed=(0.05, 0.05, 0.20), extend=(0.08, 0.20, 0.10))
    v = tf_verdict(frame, "1h")
    assert v.kind is VerdictKind.MIXED


def test_verdict_refuses_thin_sample() -> None:
    frame = rows_to_frame(
        [
            _row(symbol="BTC/USDT:USDT", arm=ARM_FIXED, total_return=0.05, num_trades=10),
            _row(symbol="BTC/USDT:USDT", arm=ARM_EXTEND, total_return=0.08, num_trades=10),
        ]
    )
    v = tf_verdict(frame, "1h")
    assert v.kind is VerdictKind.INDETERMINATE
    assert "판정 불가" in v.text


def test_verdict_demotes_on_large_trade_gap() -> None:
    """연장 팔의 거래 수가 현행 대비 5% 넘게 줄면 판정을 강등한다(슬롯 잠금)."""
    rows = []
    for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"):
        rows.append(_row(symbol=symbol, arm=ARM_FIXED, total_return=0.05, num_trades=100))
        rows.append(_row(symbol=symbol, arm=ARM_EXTEND, total_return=0.08, num_trades=70))
    v = tf_verdict(rows_to_frame(rows), "1h")
    assert v.demoted
    assert "판정 강등" in v.text


def test_trade_gap_is_relative_difference() -> None:
    rows = []
    for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        rows.append(_row(symbol=symbol, arm=ARM_FIXED, total_return=0.0, num_trades=100))
        rows.append(_row(symbol=symbol, arm=ARM_EXTEND, total_return=0.0, num_trades=80))
    # (80+80) - (100+100) = -40 / 200 = -0.20
    assert trade_gap(rows_to_frame(rows), "1h") == pytest.approx(-0.20)


def test_pooled_excludes_thin_symbol_cells() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", arm=ARM_EXTEND, total_return=0.10, num_trades=50),
        _row(symbol="ETH/USDT:USDT", arm=ARM_EXTEND, total_return=0.40, num_trades=10),  # 제외
    ]
    cell = pooled(rows_to_frame(rows), "1h", SEGMENT_OOS_WARM, ARM_EXTEND)
    assert cell["n_symbols"] == 1.0
    assert cell["n_excluded"] == 1.0
    assert cell["total_return"] == pytest.approx(0.10)


def test_leave_one_out_names_every_valid_symbol() -> None:
    rows = [
        _row(symbol="BTC/USDT:USDT", arm=ARM_EXTEND, total_return=0.40),
        _row(symbol="ETH/USDT:USDT", arm=ARM_EXTEND, total_return=-0.02),
    ]
    text = leave_one_out(rows_to_frame(rows), "1h", ARM_EXTEND)
    assert "−BTC -2.00%" in text and "−ETH +40.00%" in text


# --------------------------------------------------------------------------- #
# 실데이터 회귀 — override=None 팔이 표준 엔진과 비트 단위로 같다
# --------------------------------------------------------------------------- #


@pytest.fixture
def _market():  # type: ignore[no-untyped-def]
    market = load_market_data(
        normalize_symbol(_SYMBOL),
        _TIMEFRAME,
        start_ms=parse_date_ms(_START),
        end_ms=parse_date_ms(_END),
        need_1m=True,
        funding=True,
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터가 없어 회귀 대조를 건너뜁니다(CI 기본).")
    return market


def test_fixed_arm_reproduces_run_once(_market) -> None:  # type: ignore[no-untyped-def]
    """override=None(팔 A) 셀이 표준 CLI 경로(`harness.run_once`)와 구간마다 일치한다.

    이것이 검산의 핵심이다 — 연장 훅을 배선해도 기본 실행이 조용히 달라지지 않았음을,
    따뜻한 연속 OOS(`eval_from_ms`)까지 포함해 실데이터 숫자로 고정한다.
    """
    rows = run_cell(_market, fills=[BASELINE_FILL], log=False)
    fixed_rows = {r.segment: r for r in rows if r.arm == ARM_FIXED}
    assert fixed_rows, "팔 A 행이 없다"

    cfg = build_config(_market.timeframe, funding_enabled=True)
    for segment in segments_for(warm_oos=True):
        window = slice_market(_market, segment)
        if window.empty or window.df_1m.empty:
            continue
        eval_ms = eval_boundary_ms(window, segment)
        obr = detect_order_blocks(window)
        params = build_params()
        outcome = run_once(
            window, params=params, cfg=cfg, order_block_result=obr, eval_from_ms=eval_ms
        )
        row = fixed_rows[segment.name]
        assert row.total_return == pytest.approx(outcome.result.metrics.total_return, abs=1e-9)
        assert row.num_trades == outcome.result.metrics.num_trades
        assert row.max_drawdown == pytest.approx(outcome.result.metrics.max_drawdown, abs=1e-9)


def test_extend_arm_shares_the_fill_set_with_fixed(_market) -> None:  # type: ignore[no-untyped-def]
    """익절 자만 청산을 바꾸므로 두 팔의 체결 셋업(따라서 eligible/filled)이 같아야 한다."""
    rows = run_cell(_market, fills=[BASELINE_FILL], log=False)
    by_seg: dict[str, dict[str, Wan204Row]] = {}
    for r in rows:
        by_seg.setdefault(r.segment, {})[r.arm] = r
    for seg, arms in by_seg.items():
        assert arms[ARM_FIXED].filled == arms[ARM_EXTEND].filled, seg
        assert arms[ARM_FIXED].eligible == arms[ARM_EXTEND].eligible, seg
