"""페이퍼 ↔ 백테스트 파리티 리포트 검증 (WAN-247).

합성 장부(`OrderJournal`·`PaperTradeStore`)로 라이브 쪽 집계·실현 R·창 필터·렌더를 CI에서
고정하고(실데이터 불필요), 백테스트 엔진 경로는 실데이터가 있을 때만 도는 게이트 테스트로
불변식(체결 ≤ eligible ≤ 탭 등)을 확인한다 — 후자는 로컬 `ohlcv.db`에 봉이 없으면 skip한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from execution.engine import REJECT_CODE_SIZING
from live.limit_orders import LimitFill, LimitOrderStatus, PendingLimitOrder
from live.order_journal import OrderJournal
from live.paper_parity import (
    BacktestParityCell,
    PaperParityCell,
    ParityReport,
    RStats,
    _agg_paper,  # noqa: PLC2701  — 집계 규약을 렌더 문자열이 아니라 동작으로 고정한다(WAN-406)
    backtest_parity_cell,
    build_parity_report,
    paper_cells,
    r_stats_from_reasons,
    render_parity,
    resolve_cells,
    resolve_window,
)
from paper.store import PaperTradeRecord, PaperTradeStore
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"
_DAY_MS = 86_400_000


# ---------------------------------------------------------------------------
# 합성 장부 헬퍼
# ---------------------------------------------------------------------------


def _order(*, first_rested_ms: int | None = None, placed_ms: int = 1_000) -> PendingLimitOrder:
    return PendingLimitOrder(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        limit_price=100.0,
        stop_price=90.0,
        rsi_state=RealtimeRsi(length=3),
        placed_ms=placed_ms,
        first_rested_ms=first_rested_ms,
    )


def _fill(*, time_ms: int, penetration_bps: float = 12.0) -> LimitFill:
    return LimitFill(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        price=100.0,
        time=time_ms,
        rsi=25.0,
        stop_price=90.0,
        take_profit_price=115.0,
        penetration_bps=penetration_bps,
        waited_ms=60_000,
    )


def _paper_record(
    *,
    entry_time: int,
    exit_time: int,
    reason: SignalExitReason,
    r_multiple: float,
) -> PaperTradeRecord:
    net = 1.5 if reason is SignalExitReason.TAKE_PROFIT else -1.0
    return PaperTradeRecord(
        symbol=_SYMBOL,
        timeframe=_TF,
        direction=OrderBlockDirection.BULLISH,
        entry_time=entry_time,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=115.0 if reason is SignalExitReason.TAKE_PROFIT else 90.0,
        reason=reason,
        gross_pct=net,
        fee_pct=0.0,
        slippage_pct=0.0,
        funding_pct=0.0,
        net_pct=net,
        risk_pct=1.0,
        r_multiple=r_multiple,
        stop_price=90.0,
        take_profit_price=115.0,
    )


# ---------------------------------------------------------------------------
# RStats
# ---------------------------------------------------------------------------


def test_r_stats_reason_based_and_net() -> None:
    stats = r_stats_from_reasons(
        [True, True, False, None],  # 익절 2 · 손절 1 · 그 밖 1(제외)
        take_profit_r=1.5,
        net_r_values=[1.4, 1.4, -1.02],
    )
    assert stats.wins == 2
    assert stats.losses == 1
    assert stats.n == 3
    assert stats.mean_r == pytest.approx((2 * 1.5 - 1) / 3)
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.net_mean_r == pytest.approx((1.4 + 1.4 - 1.02) / 3)


def test_r_stats_empty_is_none() -> None:
    stats = r_stats_from_reasons([None, None], take_profit_r=1.5)
    assert stats.n == 0
    assert stats.mean_r is None
    assert stats.win_rate is None
    assert stats.net_mean_r is None


# ---------------------------------------------------------------------------
# 라이브 쪽 집계 — 체결 깔때기 + 실현 R
# ---------------------------------------------------------------------------


def _seed_journal(journal: OrderJournal) -> None:
    """1 진입 · 1 거부(사이징) · 1 스침체결(진입) · 1 미체결을 심는다(전부 창 안)."""
    session = journal.start_session(now_ms=1_000)

    # 체결 → 진입.
    jid = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(jid, _fill(time_ms=50_000, penetration_bps=12.0))
    journal.record_entry_result(jid, entered=True)

    # 체결 → 거부(사이징 가드).
    jid = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(jid, _fill(time_ms=51_000, penetration_bps=12.0))
    journal.record_entry_result(jid, entered=False, reason="사이징", reason_code=REJECT_CODE_SIZING)

    # 스침 체결(관통 < 5bp) → 진입.
    jid = journal.record_placed(
        _order(), session_id=session, zone_start_time=0, zone_confirmed_time=1
    )
    journal.record_filled(jid, _fill(time_ms=52_000, penetration_bps=2.0))
    journal.record_entry_result(jid, entered=True)

    # 미체결(걸렸으나 안 닿음) — first_rested 있음 → no_fill.
    jid = journal.record_placed(
        _order(first_rested_ms=1_500),
        session_id=session,
        zone_start_time=0,
        zone_confirmed_time=1,
    )
    journal.record_cancelled(jid, LimitOrderStatus.CANCELLED_EXPIRED, now_ms=53_000)


def test_paper_cells_classifies_funnel_and_realized_r(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    _seed_journal(journal)
    entries = journal.ledger_entries(start_ms=0, end_ms=100_000)
    journal.close()

    records = [
        _paper_record(
            entry_time=40_000, exit_time=60_000, reason=SignalExitReason.TAKE_PROFIT, r_multiple=1.4
        ),
        _paper_record(
            entry_time=41_000, exit_time=61_000, reason=SignalExitReason.STOP_LOSS, r_multiple=-1.02
        ),
    ]

    cells = paper_cells(entries, records, start_ms=0, end_ms=100_000, take_profit_r=1.5)
    assert len(cells) == 1
    cell = cells[0]
    assert (cell.symbol, cell.timeframe) == (_SYMBOL, _TF)
    assert cell.filled == 3  # 진입·거부·스침 모두 체결
    assert cell.no_fill == 1
    assert cell.entered == 2
    assert cell.entry_rejected == 1
    assert cell.marginal_fills == 1
    assert cell.fill_rate == pytest.approx(3 / 4)
    assert cell.entry_rate == pytest.approx(2 / 3)
    # 실현 R(사유 기준): 익절 1 · 손절 1 → (1.5 − 1)/2 = 0.25R.
    assert cell.r.mean_r == pytest.approx(0.25)
    assert cell.r.win_rate == pytest.approx(0.5)
    assert cell.r.net_mean_r == pytest.approx((1.4 - 1.02) / 2)


def test_paper_cells_excludes_trades_outside_window() -> None:
    records = [
        _paper_record(
            entry_time=40_000, exit_time=60_000, reason=SignalExitReason.TAKE_PROFIT, r_multiple=1.4
        ),
        # 청산이 창 밖(exit_time ≥ end_ms) → 실현 R에서 제외.
        _paper_record(
            entry_time=41_000,
            exit_time=500_000,
            reason=SignalExitReason.STOP_LOSS,
            r_multiple=-1.0,
        ),
    ]
    cells = paper_cells([], records, start_ms=0, end_ms=100_000, take_profit_r=1.5)
    assert len(cells) == 1
    assert cells[0].r.wins == 1
    assert cells[0].r.losses == 0
    assert cells[0].r.mean_r == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# 창 해석 · 가동 시간
# ---------------------------------------------------------------------------


def test_resolve_window_explicit_dates(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    try:
        start_ms, end_ms, start_key, end_key = resolve_window(
            journal, store, start="2026-08-01", end="2026-08-03"
        )
    finally:
        journal.close()
        store.close()
    assert start_key == "2026-08-01"
    assert end_key == "2026-08-03"
    # 끝은 포함이라 08-03 자정 다음(= 08-04 자정)까지, 사흘 창.
    assert end_ms - start_ms == 3 * _DAY_MS


def test_resolve_window_from_sessions(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    # 2026-08-01 12:00 KST 즈음의 ms.
    base = 1_785_000_000_000
    journal.start_session(now_ms=base)
    journal.heartbeat(1, now_ms=base + 2 * _DAY_MS)
    try:
        start_ms, end_ms, _, _ = resolve_window(journal, store, start=None, end=None)
    finally:
        journal.close()
        store.close()
    # 세션 구간을 감싸는 자정 경계 창.
    assert start_ms <= base
    assert end_ms >= base + 2 * _DAY_MS


def test_resolve_window_empty_falls_back_to_today(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    try:
        start_ms, end_ms, start_key, end_key = resolve_window(journal, store, start=None, end=None)
    finally:
        journal.close()
        store.close()
    assert start_key == end_key
    assert end_ms - start_ms == _DAY_MS


# ---------------------------------------------------------------------------
# 리포트 조립 · 렌더 (엔진 없이 — 라이브만, cells=())
# ---------------------------------------------------------------------------


def test_build_report_paper_only_and_uptime(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    _seed_journal(journal)  # 세션 [1_000, ?]; 기본 heartbeat=start
    journal.heartbeat(1, now_ms=80_000)
    store = PaperTradeStore(tmp_path / "j.db")
    store.upsert_record(
        _paper_record(
            entry_time=40_000, exit_time=60_000, reason=SignalExitReason.TAKE_PROFIT, r_multiple=1.4
        )
    )
    try:
        report = build_parity_report(
            journal,
            store,
            start_ms=0,
            end_ms=100_000,
            start_key="2026-08-01",
            end_key="2026-08-01",
            cells=(),  # 백테스트 생략(엔진 미실행 — CI 안전)
        )
    finally:
        journal.close()
        store.close()

    assert report.has_paper
    assert report.backtest == ()
    # 세션 [1_000, 80_000]과 창 [0, 100_000]의 겹침.
    assert report.uptime_ms == 79_000
    text = render_parity(report, by_cell=True)
    assert "페이퍼 ↔ 백테스트 파리티" in text
    assert "## 판정" in text
    assert "라이브 표본 없음" not in text
    # 심볼×TF 표에 셀이 나온다.
    assert "BTC" in text


def test_build_report_empty_ledger_renders_no_sample(tmp_path: Path) -> None:
    journal = OrderJournal(tmp_path / "j.db")
    store = PaperTradeStore(tmp_path / "j.db")
    try:
        report = build_parity_report(
            journal,
            store,
            start_ms=0,
            end_ms=_DAY_MS,
            start_key="2026-08-01",
            end_key="2026-08-01",
            cells=(),
        )
    finally:
        journal.close()
        store.close()
    assert not report.has_paper
    text = render_parity(report)
    assert "라이브 표본 없음" in text
    assert "지어내지 않는다" in text


def test_render_full_synthetic_report_has_both_sides() -> None:
    """엔진 없이 손으로 만든 라이브·백테스트 셀이 집계·판정에 함께 나온다."""
    paper = PaperParityCell(
        symbol=_SYMBOL,
        timeframe=_TF,
        filled=8,
        no_fill=2,
        entered=6,
        entry_rejected=2,
        marginal_fills=3,
        r=RStats(n=6, wins=4, losses=2, mean_r=(4 * 1.5 - 2) / 6, net_n=6, net_sum=3.0),
    )
    backtest = BacktestParityCell(
        symbol=_SYMBOL,
        timeframe=_TF,
        taps=20,
        reservations=15,
        eligible=15,
        fills_baseline=12,
        fills_pen5=9,
        entries=10,
        r=RStats(n=10, wins=7, losses=3, mean_r=(7 * 1.5 - 3) / 10),
    )
    report = ParityReport(
        start_ms=0,
        end_ms=100_000,
        start_key="2026-08-01",
        end_key="2026-08-02",
        take_profit_r=1.5,
        uptime_ms=90_000,
        window_ms=100_000,
        paper=(paper,),
        backtest=(backtest,),
    )
    text = render_parity(report, by_cell=True)
    assert "집계 대조" in text
    assert "체결률" in text
    assert "실현 R" in text
    # 백테스트 baseline 체결률 12/15 = 80.0%.
    assert "80.0%" in text
    # 심볼×TF별 표.
    assert "심볼×TF별 대조" in text


def test_resolve_cells_explicit_product() -> None:
    cells = resolve_cells(
        journal=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        symbols="BTC/USDT:USDT,ETH/USDT:USDT",
        timeframes="15m,1h",
    )
    assert cells == [
        ("BTC/USDT:USDT", "15m"),
        ("BTC/USDT:USDT", "1h"),
        ("ETH/USDT:USDT", "15m"),
        ("ETH/USDT:USDT", "1h"),
    ]


# ---------------------------------------------------------------------------
# 백테스트 엔진 경로 — 실데이터 게이트
# ---------------------------------------------------------------------------


def _has_real_data(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> bool:
    """실제 봉이 있는지 — 파일 존재가 아니라 데이터 유무로 판정(WAN 회귀 테스트 관행)."""
    from backtest.harness import load_market_data

    try:
        market = load_market_data(
            symbol, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=False
        )
    except Exception:
        return False
    return not (market.htf_df.empty or market.df_1m.empty)


def test_backtest_parity_cell_real_data_invariants() -> None:
    """실데이터가 있으면 채택 엔진 경로의 불변식을 확인한다(체결 ≤ eligible ≤ 탭 등)."""
    from datetime import date

    from common.timefmt import kst_day_bounds_for_date

    start_ms = kst_day_bounds_for_date(date(2024, 1, 10))[0]
    end_ms = kst_day_bounds_for_date(date(2024, 1, 12))[1]
    warmup_start = start_ms - 30 * _DAY_MS
    if not _has_real_data(_SYMBOL, _TF, warmup_start, end_ms):
        pytest.skip("실데이터(ohlcv.db) 없음 — 백테스트 엔진 경로 skip")

    cell = backtest_parity_cell(_SYMBOL, _TF, start_ms=start_ms, end_ms=end_ms, warmup_days=30)
    assert cell.has_data
    assert cell.reservations <= cell.taps
    assert cell.fills_baseline <= cell.eligible
    assert cell.fills_pen5 <= cell.eligible
    assert cell.entries <= cell.fills_baseline
    # 실현 R 표본은 진입 거래의 부분집합(익절+손절만, EOD 제외).
    assert cell.r.n == cell.r.wins + cell.r.losses
    assert cell.r.n <= cell.entries


def test_backtest_parity_cell_no_data_when_missing() -> None:
    """수집 안 된 심볼은 has_data=False로 대조에서 빠진다(0으로 세지 않음)."""
    cell = backtest_parity_cell("NOPE/USDT:USDT", _TF, start_ms=0, end_ms=_DAY_MS, warmup_days=1)
    assert not cell.has_data
    assert cell.taps == 0
    assert cell.r.n == 0


# ---------------------------------------------------------------------------
# WAN-406 — 합산 행의 net은 셀이 아니라 거래를 센다
# ---------------------------------------------------------------------------


def _paper_cell_with_net(
    tf: str, *, wins: int, losses: int, net_values: list[float], n: int | None = None
) -> PaperParityCell:
    """실현 R만 채운 합성 페이퍼 셀(체결 깔때기는 이 절의 관심사가 아니다)."""
    return PaperParityCell(
        symbol=_SYMBOL,
        timeframe=tf,
        filled=0,
        no_fill=0,
        entered=0,
        entry_rejected=0,
        marginal_fills=0,
        r=RStats(
            n=wins + losses if n is None else n,
            wins=wins,
            losses=losses,
            mean_r=(wins * 1.5 - losses) / (wins + losses) if (wins + losses) else None,
            net_n=len(net_values),
            net_sum=sum(net_values),
        ),
    )


def test_aggregate_net_is_trade_weighted_not_cell_weighted() -> None:
    """🚨 거래 1건짜리 셀이 10건짜리 셀과 같은 무게를 가지면 안 된다 (WAN-406).

    비가중(셀별 평균의 평균) = (+1.50 + −1.00) / 2 = **+0.25**
    가중(거래 단위)         = (+1.50 + 10 × −1.00) / 11 = **−0.7727**

    두 값이 부호까지 갈리는 픽스처라, 옛 코드(`sum(nets) / len(nets)`)로 되돌리면 이
    테스트는 반드시 실패한다 — 라벨이 아니라 동작으로 건다.
    """
    thin = _paper_cell_with_net("1h", wins=1, losses=0, net_values=[1.5])
    thick = _paper_cell_with_net("15m", wins=0, losses=10, net_values=[-1.0] * 10)

    *_, stats, _ = _agg_paper([thin, thick], 1.5)

    assert stats.net_n == 11
    assert stats.net_mean_r == pytest.approx((1.5 + 10 * -1.0) / 11)
    # 돌연변이 방어: 비가중 평균이 나오면 안 된다(부호부터 다르다).
    assert stats.net_mean_r is not None
    assert stats.net_mean_r < 0.0
    assert stats.net_mean_r != pytest.approx(0.25)


def test_aggregate_net_population_differs_from_reason_population() -> None:
    """⚠️ `r_multiple`이 None인 거래는 net에서 빠지고 사유에는 남는다 — `net_n != n` (WAN-406).

    `c.r.n`으로 가중하면 틀린다: 셀 A는 사유 3건인데 net은 2건뿐이다.
    """
    # 사유 3건(익절 2 · 손절 1)인데 net은 2건만 확정.
    partial = _paper_cell_with_net("1h", wins=2, losses=1, net_values=[1.4, -1.02])
    full = _paper_cell_with_net("15m", wins=0, losses=4, net_values=[-1.2] * 4)

    *_, stats, _ = _agg_paper([partial, full], 1.5)

    assert stats.n == 7  # 사유 기준 모집단
    assert stats.net_n == 6  # net 모집단 — 같다고 가정하면 안 된다
    assert stats.net_mean_r == pytest.approx((1.4 - 1.02 + 4 * -1.2) / 6)
    # `n`으로 가중한 잘못된 값과 실제로 갈린다.
    wrong_by_n = (3 * ((1.4 - 1.02) / 2) + 4 * -1.2) / 7
    assert stats.net_mean_r != pytest.approx(wrong_by_n)


def test_aggregate_net_reproduces_server_ledger_measurement() -> None:
    """실측 재현 (WAN-406 이슈 본문): 익절 36 @ +1.4393 · 손절 35 @ −1.2126 → **+0.1320R**.

    거래를 여러 셀에 쪼개 담아도(얇은 셀 포함) 합산 행이 참값을 낸다.
    """
    tp_r, sl_r = 1.4393, -1.2126
    # 36 익절 / 35 손절을 「얇은 셀 여럿 + 두꺼운 셀 하나」로 쪼갠다 — 비가중이면 위로 끌린다.
    cells = [
        _paper_cell_with_net(f"thin{i}", wins=1, losses=0, net_values=[tp_r]) for i in range(6)
    ]
    cells.append(
        _paper_cell_with_net("15m", wins=30, losses=35, net_values=[tp_r] * 30 + [sl_r] * 35)
    )

    *_, stats, _ = _agg_paper(cells, 1.5)

    assert stats.wins == 36
    assert stats.losses == 35
    assert stats.net_n == 71
    assert stats.net_mean_r == pytest.approx((36 * tp_r + 35 * sl_r) / 71, abs=5e-5)
    assert stats.net_mean_r == pytest.approx(0.1320, abs=5e-5)
    # 사유 기준(+0.2676R)은 손대지 않았고, 비용을 뺀 net은 반드시 그보다 작다 (WAN-392 성질).
    assert stats.mean_r == pytest.approx((36 * 1.5 - 35) / 71)
    assert stats.mean_r is not None
    assert stats.net_mean_r is not None
    assert stats.net_mean_r < stats.mean_r


def test_r_stats_net_mean_is_derived_not_stored() -> None:
    """📌 `net_mean_r`은 저장 필드가 아니라 `net_sum / net_n` 파생이다 (WAN-406).

    평균을 실을 수 있으면 「셀별 평균을 다시 평균」이 언제든 되살아난다 — 타입으로 막는다.
    """
    with pytest.raises(TypeError):
        RStats(n=1, wins=1, losses=0, mean_r=1.5, net_mean_r=0.5)  # type: ignore[call-arg]

    stats = r_stats_from_reasons([True, False], take_profit_r=1.5, net_r_values=[1.4, -1.02])
    assert stats.net_n == 2
    assert stats.net_sum == pytest.approx(1.4 - 1.02)
    assert stats.net_mean_r == pytest.approx((1.4 - 1.02) / 2)


def test_render_aggregate_flags_net_population_mismatch() -> None:
    """`net_n != n`이면 표가 그 사실을 밝힌다(감추지 않는다) — 같으면 조용하다."""
    mismatch = _paper_cell_with_net("1h", wins=2, losses=1, net_values=[1.4, -1.02])
    matched = _paper_cell_with_net("1h", wins=2, losses=1, net_values=[1.4, 1.4, -1.02])
    backtest = BacktestParityCell(
        symbol=_SYMBOL,
        timeframe=_TF,
        taps=5,
        reservations=5,
        eligible=5,
        fills_baseline=3,
        fills_pen5=2,
        entries=3,
        r=RStats(n=3, wins=2, losses=1, mean_r=(2 * 1.5 - 1) / 3),
    )

    def _render(cell: PaperParityCell) -> str:
        return render_parity(
            ParityReport(
                start_ms=0,
                end_ms=_DAY_MS,
                start_key="2026-09-01",
                end_key="2026-09-02",
                take_profit_r=1.5,
                uptime_ms=_DAY_MS,
                window_ms=_DAY_MS,
                paper=(cell,),
                backtest=(backtest,),
            )
        )

    assert "net 표본 2" in _render(mismatch)
    assert "net 표본" not in _render(matched)
