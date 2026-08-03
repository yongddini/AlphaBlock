"""당일 라이브 vs 백테스트 대조 도구 테스트 (WAN-233).

- 탭/예약 카운트가 엔진과 같은 탭 생성기·존폭 필터·직전봉 ATR을 쓰는지(합성).
- 라이브 쪽이 `OrderJournal.day_summary`(WAN-232)와 정확히 일치하고 심볼×TF별 합이 전체와
  같은지(CTA #3).
- 렌더가 네 단계 표와 "무엇을 의심하나"를 담는지.
- (실데이터) 워밍업 연속 + 그날만 평가라 미래 봉을 잘라도 그날 funnel이 비트 동일(누수 0),
  그리고 `체결 ≤ 예약 ≤ 탭`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import (
    MarketData,
    build_config,
    build_params,
    detect_order_blocks,
    load_market_data,
)
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder
from live.live_vs_backtest import (
    BacktestFunnel,
    CellFunnel,
    DayComparison,
    _live_by_cell,
    backtest_cell_funnel,
    cell_funnel,
    count_taps_reservations,
    render_comparison,
    resolve_day_window,
)
from live.order_journal import DaySummary, OrderJournal
from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
)
from strategy.realtime_rsi import RealtimeRsi

# --------------------------------------------------------------------------- #
# 합성: 탭/예약 카운트
# --------------------------------------------------------------------------- #

_HTF_MS = 3_600_000  # 1h.


def _htf_frame(n: int = 30, base_ms: int = 1_600_000_000_000) -> pd.DataFrame:
    """모든 봉이 고정 폭(high−low=10, 종가 100)인 1h 프레임 → ATR14가 워밍업 뒤 10으로 수렴.

    존폭/ATR = 문턱(1.28) 판정을 예측 가능하게 하려고 실질변동폭을 상수로 만든다.
    """
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": base_ms + i * _HTF_MS,
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 100.0,
                "volume": 1.0,
            }
        )
    return pd.DataFrame(rows)


def _ob(direction: OrderBlockDirection, top: float, bottom: float, t: int) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=t,
        confirmed_time=t,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=1.5,
    )


def _signal(
    direction: OrderBlockDirection,
    top: float,
    bottom: float,
    trigger_time: int,
    *,
    status: str = "active",
) -> OrderBlockSignal:
    return OrderBlockSignal(
        direction=direction,
        trigger_time=trigger_time,
        price=(top + bottom) / 2,
        order_block=_ob(direction, top, bottom, trigger_time),
        status=status,
    )


def _market_from_htf(htf: pd.DataFrame) -> MarketData:
    return MarketData(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        htf_df=htf,
        df_1m=pd.DataFrame(),
        funding_rates=[],
    )


def test_count_taps_reservations_mirrors_engine_gates() -> None:
    """탭은 `entry_candidate_signals` + 방향/숏 게이트, 예약은 존폭 필터(1.28)·직전봉 ATR로 센다.

    ATR14 = 10(고정 폭)이라 폭 5는 통과(0.5 ≤ 1.28), 폭 20은 기각(2.0 > 1.28). 창 밖·취소·숏은
    탭에서 빠진다.
    """
    htf = _htf_frame(n=30)
    times = htf["open_time"].tolist()
    market = _market_from_htf(htf)
    params = build_params()  # 채택 기본값: max_zone_width_atr=1.28, short_enabled=False.
    cfg = build_config("1h")

    signals = [
        _signal(OrderBlockDirection.BULLISH, 105.0, 100.0, times[16]),  # 폭 5 → 탭+예약.
        _signal(OrderBlockDirection.BULLISH, 120.0, 100.0, times[17]),  # 폭 20 → 탭, 예약 X.
        _signal(OrderBlockDirection.BULLISH, 105.0, 100.0, times[5]),  # 창 밖.
        _signal(OrderBlockDirection.BULLISH, 105.0, 100.0, times[18], status="cancelled"),
        _signal(OrderBlockDirection.BEARISH, 105.0, 100.0, times[19]),  # 숏 → 게이트 제외.
    ]
    ob_result = OrderBlockResult(
        order_blocks=[s.order_block for s in signals],
        signals=signals,
        retap_signals=signals,
    )

    taps, reservations = count_taps_reservations(
        ob_result,
        market,
        params,
        cfg,
        day_start_ms=times[10],
        day_end_ms=times[20],
    )
    assert taps == 2  # 폭5 · 폭20 (창 안, 롱, active). 창 밖·취소·숏 제외.
    assert reservations == 1  # 폭5만 존폭 필터 통과.


def test_count_taps_reservations_filter_off_counts_all_reservations() -> None:
    """존폭 필터를 끄면(`max_zone_width_atr=None`) 넓은 존도 예약으로 센다(탭==예약)."""
    htf = _htf_frame(n=30)
    times = htf["open_time"].tolist()
    market = _market_from_htf(htf)
    params = build_params(max_zone_width_atr=None)
    cfg = build_config("1h")
    signals = [
        _signal(OrderBlockDirection.BULLISH, 120.0, 100.0, times[16]),  # 폭 20.
        _signal(OrderBlockDirection.BULLISH, 130.0, 100.0, times[17]),  # 폭 30.
    ]
    ob_result = OrderBlockResult(
        order_blocks=[s.order_block for s in signals], signals=signals, retap_signals=signals
    )
    taps, reservations = count_taps_reservations(
        ob_result, market, params, cfg, day_start_ms=times[10], day_end_ms=times[20]
    )
    assert taps == 2
    assert reservations == 2  # 필터 꺼짐 → 전부 통과.


# --------------------------------------------------------------------------- #
# 라이브: day_summary 일치 · 심볼×TF 합
# --------------------------------------------------------------------------- #


def _place(
    journal: OrderJournal,
    session: int,
    *,
    symbol: str,
    timeframe: str,
    placed_ms: int,
) -> int:
    order = PendingLimitOrder(
        symbol=symbol,
        timeframe=timeframe,
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=placed_ms,
    )
    return journal.record_placed(
        order, session_id=session, zone_start_time=0, zone_confirmed_time=1
    )


def _fill(time_ms: int) -> LimitFill:
    return LimitFill(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        price=100.0,
        time=time_ms,
        rsi=25.0,
        stop_price=90.0,
        take_profit_price=115.0,
        penetration_bps=0.0,
        waited_ms=60_000,
    )


def test_live_by_cell_sums_to_day_summary(tmp_path: Path) -> None:
    """CTA #3: 심볼×TF별 요약을 다 더하면 전체 `day_summary`와 정확히 같다(같은 분류 공유)."""
    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)

    # BTC 1h: 체결+진입 하나, 미체결 하나.
    entered = _place(journal, session, symbol="BTC/USDT:USDT", timeframe="1h", placed_ms=1100)
    journal.record_filled(entered, _fill(1400))
    journal.record_entry_result(entered, entered=True)
    journal.record_cancelled(
        _place(journal, session, symbol="BTC/USDT:USDT", timeframe="1h", placed_ms=1200),
        LimitOrderStatus.CANCELLED_EXPIRED,
        now_ms=1600,
    )
    # ETH 4h: 체결+거부 하나.
    rejected = _place(journal, session, symbol="ETH/USDT:USDT", timeframe="4h", placed_ms=1150)
    journal.record_filled(rejected, _fill(1450))
    journal.record_entry_result(rejected, entered=False, reason="손절 너무 짧음")

    orders = journal.orders_placed_between(start_ms=1000, end_ms=2000)
    by_cell = _live_by_cell(orders)
    whole = journal.day_summary(start_ms=1000, end_ms=2000)

    assert {(s, tf) for s, tf, _ in by_cell} == {("BTC/USDT:USDT", "1h"), ("ETH/USDT:USDT", "4h")}
    assert sum(c.taps for _, _, c in by_cell) == whole.taps
    assert sum(c.reserved for _, _, c in by_cell) == whole.reserved
    assert sum(c.filled for _, _, c in by_cell) == whole.filled
    assert sum(c.entered for _, _, c in by_cell) == whole.entered
    assert sum(c.entry_rejected for _, _, c in by_cell) == whole.entry_rejected
    journal.close()


def test_day_summary_taps_is_reserved_plus_skipped(tmp_path: Path) -> None:
    """탭 = 예약 + 필터제외 — 진입 깔때기 최상단(라이브 대조 값)."""
    from live.order_journal import SKIP_REASON_ZONE_WIDTH

    journal = OrderJournal(tmp_path / "j.db")
    session = journal.start_session(now_ms=0)
    _place(journal, session, symbol="BTC/USDT:USDT", timeframe="1h", placed_ms=1100)
    journal.record_skipped(
        session_id=session,
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH.value,
        tap_index=0,
        placed_ms=1150,
        reason=SKIP_REASON_ZONE_WIDTH,
        zone_start_time=0,
        zone_confirmed_time=1,
    )
    summary = journal.day_summary(start_ms=1000, end_ms=2000)
    assert summary.reserved == 1
    assert summary.skipped == 1
    assert summary.taps == 2
    journal.close()


# --------------------------------------------------------------------------- #
# 렌더 · 날짜 창
# --------------------------------------------------------------------------- #


def _empty_summary(**kw: int) -> DaySummary:
    base = dict(
        reserved=0,
        filled=0,
        no_fill=0,
        deviation=0,
        invalidated=0,
        condition_failed=0,
        pending=0,
        discarded_restart=0,
        skipped=0,
        entered=0,
        entry_rejected=0,
        entry_unrecorded=0,
    )
    base.update(kw)
    return DaySummary(**base)


def test_render_comparison_shows_four_stages_and_diff() -> None:
    live = _empty_summary(reserved=8, skipped=4, filled=3, entered=2)
    bt = BacktestFunnel(
        cells=(
            CellFunnel(
                "BTC/USDT:USDT",
                "1h",
                taps=12,
                reservations=9,
                fills_baseline=6,
                fills_pen5=4,
                entries=5,
            ),
            CellFunnel("XRP/USDT:USDT", "4h", 0, 0, 0, 0, 0, has_data=False),
        )
    )
    comp = DayComparison(day_key="2026-08-02", live=live, backtest=bt, live_by_cell=())
    out = render_comparison(comp)

    assert "탭/셋업 감지 | 12 | 12" in out  # live taps = reserved+skipped = 12.
    assert "예약(존폭 1.28 통과) | 8 | 9 | +1" in out
    assert "3 (틱) | 6 (baseline) | +3" in out
    assert "진입(집행 가드 통과) | 2 | 5 | +3" in out
    assert "pen_5bp" in out and "무엇을 의심하나" in out
    assert "데이터 없어 백테스트에서 뺀 셀 1개: XRP 4h" in out


def test_render_by_cell_lists_both_sides() -> None:
    live = _empty_summary(reserved=1, filled=1, entered=1)
    bt = BacktestFunnel(cells=(CellFunnel("BTC/USDT:USDT", "1h", 3, 2, 2, 1, 1),))
    by_cell = (("BTC/USDT:USDT", "1h", _empty_summary(reserved=1, filled=1, entered=1)),)
    comp = DayComparison(day_key="2026-08-02", live=live, backtest=bt, live_by_cell=by_cell)
    out = render_comparison(comp, by_cell=True)
    assert "심볼×TF별 대조" in out
    assert "| BTC | 1h |" in out


def test_resolve_day_window_parses_and_defaults() -> None:
    start, end, key = resolve_day_window("2026-08-02")
    assert key == "2026-08-02"
    assert end - start == 86_400_000  # KST 하루 = 24h.
    # 인자 없이(today)도 예외 없이 하루 창을 낸다.
    s2, e2, _ = resolve_day_window("today")
    assert e2 - s2 == 86_400_000


# --------------------------------------------------------------------------- #
# 실데이터: 누수 0 · 체결 ≤ 예약 ≤ 탭
# --------------------------------------------------------------------------- #

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"
_DAY = "2026-07-15"


def _truncate_market(market: MarketData, end_ms: int) -> MarketData:
    """`open_time < end_ms`인 봉만 남긴 시장(미래 봉을 물리적으로 잘라 낸다)."""
    htf = market.htf_df[market.htf_df["open_time"] < end_ms].reset_index(drop=True)
    df_1m = market.df_1m[market.df_1m["open_time"] < end_ms].reset_index(drop=True)
    return replace(market, htf_df=htf, df_1m=df_1m)


@pytest.fixture
def _real_day_window() -> tuple[int, int]:
    start_ms, end_ms, _ = resolve_day_window(_DAY)
    warmup_start = start_ms - 30 * 86_400_000
    market = load_market_data(
        _SYMBOL, _TF, start_ms=warmup_start, end_ms=end_ms, need_1m=False, funding=False
    )
    if market.empty:
        pytest.skip(f"{_SYMBOL} {_TF} 실데이터가 없어 대조 회귀를 건너뜁니다(CI 기본).")
    return start_ms, end_ms


def test_backtest_cell_funnel_ignores_future_bars(_real_day_window: tuple[int, int]) -> None:
    """CTA #2(누수 0): 그날 이후 봉을 물리적으로 잘라도 그날 funnel이 비트 동일하다.

    긴 시장(그날 자정 + 30일)을 로드해 그날 자정에서 잘라 낸 것과, 처음부터 그날 자정까지만
    로드한 것이 같은 카운트를 낸다 — 그날 판정이 미래 봉에 의존하지 않음을 동작으로 고정한다.
    """
    start_ms, end_ms = _real_day_window
    warmup_start = start_ms - 30 * 86_400_000

    # (A) 처음부터 그날 자정까지만.
    a = backtest_cell_funnel(_SYMBOL, _TF, day_start_ms=start_ms, day_end_ms=end_ms, warmup_days=30)
    # (B) 미래 봉을 포함해 로드한 뒤 그날 자정에서 잘라 재산출.
    long_market = load_market_data(
        _SYMBOL,
        _TF,
        start_ms=warmup_start,
        end_ms=end_ms + 30 * 86_400_000,
        need_1m=True,
        funding=False,
    )
    truncated = _truncate_market(long_market, end_ms)
    ob = detect_order_blocks(truncated)
    b = cell_funnel(truncated, ob, day_start_ms=start_ms, day_end_ms=end_ms)

    assert a.has_data and b.has_data
    assert (a.taps, a.reservations, a.fills_baseline, a.fills_pen5, a.entries) == (
        b.taps,
        b.reservations,
        b.fills_baseline,
        b.fills_pen5,
        b.entries,
    )


def test_backtest_cell_funnel_monotone(_real_day_window: tuple[int, int]) -> None:
    """진입 깔때기는 단조 감소: 체결(baseline) ≤ 예약 ≤ 탭, 진입 ≤ 체결, pen5 ≤ baseline."""
    start_ms, end_ms = _real_day_window
    cell = backtest_cell_funnel(
        _SYMBOL, _TF, day_start_ms=start_ms, day_end_ms=end_ms, warmup_days=30
    )
    assert cell.has_data
    assert cell.fills_baseline <= cell.reservations <= cell.taps
    assert cell.entries <= cell.fills_baseline
    assert cell.fills_pen5 <= cell.fills_baseline
