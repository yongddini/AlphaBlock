"""페이퍼↔백테 3열 대조 표시 계층 테스트 (WAN-295).

`dashboard/setup_compare_view.py`의 순수 페이로드·HTML을 화면 없이 고정한다:

- 페이로드가 목업 JS 스키마(sym·p·b·diverge·flag·bps)를 그대로 낸다.
- 판정갈림/가격벗어남/미진입이 페이로드 플래그로 구분된다.
- HTML이 요약 카드·칩·행 데이터·범례를 담고, 데이터 주입이 안전하다(MAX/ROWS 치환).
"""

from __future__ import annotations

import json

from dashboard.setup_compare_view import compare_rows_payload, setup_compare_html
from live.setup_compare import build_setup_comparisons
from live.trade_timeline import (
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    STATUS_BACKTEST_CLOSED,
    STATUS_BACKTEST_SKIP_ZONE_WIDTH,
    TimelineRow,
)

_TS = 1_754_870_400_000  # 2026-08-11 09:00 KST 근방.


def _live(**kw: object) -> TimelineRow:
    base: dict[str, object] = dict(
        source=SOURCE_LIVE,
        symbol="SOL/USDT:USDT",
        timeframe="1h",
        is_long=True,
        status="청산",
        reserve_ms=_TS,
        limit_price=76.07,
        fill_ms=_TS,
        fill_price=76.07,
        stop_price=75.7,
        take_profit_price=76.6,
        exit_ms=_TS + 3_600_000,
        exit_price=75.7,
        exit_reason="stop_loss",
        pnl_pct=-0.66,
        pnl_amount=-1.0,
        zone_start_time=1000,
        zone_confirmed_time=1100,
        tap_index=0,
    )
    base.update(kw)
    return TimelineRow(**base)  # type: ignore[arg-type]


def _bt(**kw: object) -> TimelineRow:
    base: dict[str, object] = dict(
        source=SOURCE_BACKTEST,
        symbol="SOL/USDT:USDT",
        timeframe="1h",
        is_long=True,
        status=STATUS_BACKTEST_CLOSED,
        reserve_ms=None,
        limit_price=None,
        fill_ms=_TS,
        fill_price=76.05,
        stop_price=75.7,
        take_profit_price=None,
        exit_ms=_TS + 3_600_000,
        exit_price=75.75,
        exit_reason="stop_loss",
        pnl_pct=-0.52,
        pnl_amount=-0.8,
        zone_start_time=1000,
        zone_confirmed_time=1100,
        tap_index=0,
        trigger_time=_TS,
    )
    base.update(kw)
    return TimelineRow(**base)  # type: ignore[arg-type]


def test_payload_matches_mockup_schema() -> None:
    """페이로드 행이 목업 JS가 읽는 키(sym·p·b·diverge·flag·bps)를 낸다."""
    result = build_setup_comparisons([_live()], [_bt()])
    payload = compare_rows_payload(result)
    assert len(payload) == 1
    row = payload[0]
    assert set(row) == {"sym", "p", "b", "diverge", "flag", "bps"}
    p = row["p"]
    assert isinstance(p, dict) and set(p) == {"s", "v", "px", "entered"}
    assert row["sym"] == "SOL·1h·롱·09:00"  # 심볼·TF·방향·KST 시각.
    assert row["diverge"] is False
    assert p["entered"] is True


def test_payload_marks_diverge_and_unentered() -> None:
    """한쪽만 진입한 셋업은 diverge=True, 미진입 쪽은 entered=False·v=None."""
    live = _live(
        status="건너뜀(존폭)", fill_price=None, pnl_pct=None, exit_ms=None, exit_price=None
    )
    result = build_setup_comparisons([live], [_bt()])
    row = compare_rows_payload(result)[0]
    assert row["diverge"] is True
    p = row["p"]
    assert isinstance(p, dict)
    assert p["entered"] is False and p["v"] is None


def test_html_embeds_cards_chips_rows_and_is_injection_safe() -> None:
    """HTML이 요약 카드·칩·주입된 행 JSON·범례를 담고, MAX/ROWS 치환이 깨지지 않는다."""
    live = _live()
    bt = _bt()
    result = build_setup_comparisons([live], [bt])
    html = setup_compare_html(result, day_key="2026-08-11")
    assert '<div id="rows">' in html
    assert "판정 갈림" in html and "가격 벗어남" in html  # 범례.
    assert "전체 1" in html and "불일치만 0" in html  # 칩 카운트.
    # 행 데이터가 JSON 배열로 그대로 주입됐다.
    payload = compare_rows_payload(result)
    assert json.dumps(payload, ensure_ascii=False) in html
    # 치환 토큰이 남아 있지 않다(ROWS·MAX가 데이터/숫자로 바뀌었다).
    assert "ROWS" not in html
    assert "/MAX," not in html


def test_html_handles_empty_and_skip_only() -> None:
    """건너뜀만 있는 날도 카드·행을 낸다(빈 화면이 아니다)."""
    live = _live(
        status="건너뜀(존폭)", fill_price=None, pnl_pct=None, exit_ms=None, exit_price=None
    )
    bt = _bt(
        status=STATUS_BACKTEST_SKIP_ZONE_WIDTH,
        fill_price=None,
        pnl_pct=None,
        exit_ms=None,
        exit_price=None,
    )
    result = build_setup_comparisons([live], [bt])
    html = setup_compare_html(result, day_key="2026-08-11")
    assert "오늘 셋업" in html
    assert result.summary.matched == 1  # 둘 다 미진입 = 매칭.
