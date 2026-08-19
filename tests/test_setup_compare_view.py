"""페이퍼↔백테 3열 대조 표시 계층 테스트 (WAN-295).

`dashboard/setup_compare_view.py`의 순수 페이로드·HTML을 화면 없이 고정한다:

- 페이로드가 목업 JS 스키마(sym·p·b·diverge·flag·bps·unpaired)를 그대로 낸다.
- 라벨이 **어느 존의 몇 번째 탭**인지 밝혀 서로 다른 존이 겹쳐 보이지 않는다(WAN-333 §1).
- 짝 없는 줄이 「매칭」이 아니라 「짝 없음 · 대조 불가」로 선다(WAN-333 §2/§3).
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
    """페이로드 행이 목업 JS가 읽는 키(sym·p·b·diverge·flag·bps·unpaired)를 낸다."""
    result = build_setup_comparisons([_live()], [_bt()])
    payload = compare_rows_payload(result)
    assert len(payload) == 1
    row = payload[0]
    # `unpaired`는 WAN-333이 더한 키다 — 조인 실패를 「매칭」으로 숨기지 않기 위한 것.
    assert set(row) == {"sym", "p", "b", "diverge", "flag", "bps", "unpaired"}
    p = row["p"]
    assert isinstance(p, dict) and set(p) == {"s", "v", "px", "entered"}
    # 심볼·TF·방향·KST 시각 + 존 정체성·탭 순번(WAN-333 §1 — 서로 다른 존을 가른다).
    assert row["sym"] == "SOL·1h·롱·09:00 · 존 01-01 09:00→01-01 09:00 · 탭 0"
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


# --- WAN-333 §1: 서로 다른 존이 라벨에서 갈린다 ------------------------------


def test_labels_differ_for_two_zones_tapped_at_the_same_time() -> None:
    """🐛 같은 심볼·TF·방향·시각에 **서로 다른 두 존**을 탭하면 라벨이 갈려야 한다 (WAN-333 §1).

    존 병합 폐지(`combine_obs=False`, WAN-149) 이후 겹치는 오더블록은 각자 남으므로 이 상황이
    정상적으로 생긴다 — 옛 라벨은 심볼·TF·방향·시각만 찍어 두 줄이 **복사된 것처럼** 보였다.
    라벨 문자열 자체가 아니라 「갈라진다」는 성질로 고정한다.
    """
    zone_a_live = _live(zone_start_time=1_754_000_000_000, zone_confirmed_time=1_754_100_000_000)
    zone_a_bt = _bt(zone_start_time=1_754_000_000_000, zone_confirmed_time=1_754_100_000_000)
    zone_b_live = _live(zone_start_time=1_754_050_000_000, zone_confirmed_time=1_754_150_000_000)
    zone_b_bt = _bt(zone_start_time=1_754_050_000_000, zone_confirmed_time=1_754_150_000_000)
    result = build_setup_comparisons([zone_a_live, zone_b_live], [zone_a_bt, zone_b_bt])
    labels = [row["sym"] for row in compare_rows_payload(result)]
    assert len(labels) == 2
    assert len(set(labels)) == 2, f"서로 다른 존이 같은 라벨로 겹쳐 보인다: {labels}"


def test_labels_differ_for_two_taps_of_the_same_zone() -> None:
    """같은 존의 여러 탭도 라벨에서 갈린다(탭 순번을 싣는다)."""
    result = build_setup_comparisons(
        [_live(tap_index=0), _live(tap_index=1)],
        [_bt(tap_index=0), _bt(tap_index=1)],
    )
    labels = [row["sym"] for row in compare_rows_payload(result)]
    assert len(set(labels)) == 2, labels


# --- WAN-333 §2/§3: 짝 없는 줄이 「매칭」으로 보이지 않는다 -------------------


def test_unpaired_row_is_flagged_and_not_labelled_as_match() -> None:
    """🚨 짝 없는 줄은 페이로드에 `unpaired`로 서고, 가운데 칸이 「매칭」이라 말하지 않는다."""
    live = _live(
        status="건너뜀(존폭)", fill_price=None, pnl_pct=None, exit_ms=None, exit_price=None
    )
    result = build_setup_comparisons([live], [])  # 백테 짝 없음.
    row = compare_rows_payload(result)[0]
    assert row["unpaired"] is True
    assert row["diverge"] is False
    html = setup_compare_html(result, day_key="2026-08-11")
    assert "짝 없음 · 대조 불가" in html  # 가운데 칸 라벨(JS).
    assert "짝 없음 (대조 불가)" in html  # 요약 카드.
    assert "일치 0" in html  # 🚨 옛 동작은 「일치 1」이었다.


def test_paired_both_not_entered_still_reads_as_match() -> None:
    """❌ 「둘 다 미진입 = 매칭」 규칙은 그대로다 — **짝지어진** 줄에만 적용될 뿐이다."""
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
    row = compare_rows_payload(result)[0]
    assert row["unpaired"] is False
    assert result.summary.matched == 1
    html = setup_compare_html(result, day_key="2026-08-11")
    assert "둘 다 미진입 · 매칭" in html


def test_html_states_the_per_cell_single_position_approximation() -> None:
    """대조 백테가 per-cell 단일 포지션이라는 **알려진 근사**가 화면에 적힌다 (WAN-333).

    페이퍼는 레버리지 북(공유 자본, WAN-213)이라 「자리가 없어서」 못 들어간 셋업이 있는데,
    대조 백테에는 그 제약이 없다 — 라벨(`단일포지션`)에만 있고 함의는 어디에도 없던 자리다.
    """
    html = setup_compare_html(build_setup_comparisons([_live()], [_bt()]), day_key="2026-08-11")
    assert "per-cell 단일 포지션" in html
