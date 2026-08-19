"""페이퍼↔백테 셋업 단위 조인·대조 테스트 (WAN-295).

`live.setup_compare`의 순수 계층을 화면 없이 고정한다:

- 조인 키는 체결 시각이 아니라 존 정체성(시작·확정)+탭 순번이라, 체결 시각이 갈려도 같은
  셋업이 한 줄로 묶인다(완료 기준 1).
- 판정 갈림(한쪽만 진입)=🔴, 가격 벗어남(진입가차 임계 초과)=🟠가 구분된다(완료 기준 2).
- Δ 임계값이 `tick_parity` 측정 분포(매칭 체결 가격차)에서 나오고 하드코딩이 아니다(완료 기준 4).
- 요약 카드 분해·필터 칩이 맞다.
"""

from __future__ import annotations

from live.setup_compare import (
    TICK_DIVERGENCE_K,
    build_setup_comparisons,
    filter_comparisons,
    price_off_threshold_bps,
    setup_key,
)
from live.trade_timeline import (
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    STATUS_BACKTEST_CLOSED,
    STATUS_BACKTEST_SKIP_ZONE_WIDTH,
    STATUS_BACKTEST_UNFILLED,
    TimelineRow,
)

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"


def _live(
    *,
    status: str = "청산",
    fill_price: float | None = 100.0,
    pnl_pct: float | None = -0.66,
    zone_start: int | None = 1000,
    zone_confirmed: int | None = 1100,
    tap_index: int | None = 0,
    fill_ms: int | None = 1_700_000_060_000,
) -> TimelineRow:
    return TimelineRow(
        source=SOURCE_LIVE,
        symbol=_SYMBOL,
        timeframe=_TF,
        is_long=True,
        status=status,
        reserve_ms=1_700_000_000_000,
        limit_price=100.0,
        fill_ms=fill_ms,
        fill_price=fill_price,
        stop_price=90.0,
        take_profit_price=110.0,
        exit_ms=1_700_003_600_000 if pnl_pct is not None else None,
        exit_price=99.0 if pnl_pct is not None else None,
        exit_reason="stop_loss" if pnl_pct is not None else None,
        pnl_pct=pnl_pct,
        pnl_amount=None,
        zone_start_time=zone_start,
        zone_confirmed_time=zone_confirmed,
        tap_index=tap_index,
    )


def _bt(
    *,
    status: str = STATUS_BACKTEST_CLOSED,
    fill_price: float | None = 100.0,
    pnl_pct: float | None = -0.52,
    zone_start: int | None = 1000,
    zone_confirmed: int | None = 1100,
    tap_index: int | None = 0,
) -> TimelineRow:
    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=_SYMBOL,
        timeframe=_TF,
        is_long=True,
        status=status,
        reserve_ms=None,
        limit_price=None,
        fill_ms=1_700_000_040_000 if fill_price is not None else None,
        fill_price=fill_price,
        stop_price=90.0,
        take_profit_price=None,
        exit_ms=1_700_003_600_000 if pnl_pct is not None else None,
        exit_price=99.2 if pnl_pct is not None else None,
        exit_reason="stop_loss" if pnl_pct is not None else None,
        pnl_pct=pnl_pct,
        pnl_amount=None,
        zone_start_time=zone_start,
        zone_confirmed_time=zone_confirmed,
        tap_index=tap_index,
        trigger_time=1_700_000_000_000,
    )


def test_join_by_zone_identity_not_fill_time() -> None:
    """체결 시각이 갈려도 같은 존·탭이면 한 줄로 묶인다 — 조인 키는 존 정체성이다."""
    live = _live(fill_ms=1_700_000_060_000)  # 라이브 틱 체결 시각.
    bt = _bt()  # 백테 1분봉 체결 시각(다르다).
    result = build_setup_comparisons([live], [bt])
    assert len(result.comparisons) == 1
    comp = result.comparisons[0]
    assert comp.live is live and comp.backtest is bt
    assert comp.live_entered and comp.backtest_entered
    assert not comp.verdict_differs
    assert comp.pnl_delta_pct is not None
    assert abs(comp.pnl_delta_pct - (-0.66 - -0.52)) < 1e-9


def test_verdict_differs_when_only_one_side_enters() -> None:
    """한쪽만 진입 = 🔴 판정 갈림(핵심 신호). 백테는 청산, 라이브는 거부."""
    live = _live(status="거부(사이징0)", fill_price=None, pnl_pct=None)
    result = build_setup_comparisons([live], [_bt()])
    comp = result.comparisons[0]
    assert comp.verdict_differs is True
    assert result.summary.diverged == 1
    assert result.summary.backtest_only_entered == 1
    assert result.summary.live_only_entered == 0


def test_both_not_entered_is_a_match() -> None:
    """양쪽 다 미진입(라이브 건너뜀 + 백테 건너뜀)은 불일치가 아니라 매칭이다."""
    live = _live(status="건너뜀(존폭)", fill_price=None, pnl_pct=None)
    bt = _bt(status=STATUS_BACKTEST_SKIP_ZONE_WIDTH, fill_price=None, pnl_pct=None)
    result = build_setup_comparisons([live], [bt])
    comp = result.comparisons[0]
    assert not comp.verdict_differs
    assert result.summary.matched == 1
    assert result.summary.diverged == 0


def test_live_only_setup_still_shown() -> None:
    """백테에 대응 셋업이 없어도(조인 안 됨) 라이브 줄은 그대로 한 칸으로 남는다."""
    result = build_setup_comparisons([_live()], [])
    comp = result.comparisons[0]
    assert comp.live is not None and comp.backtest is None
    # 라이브만 진입 → 백테 미진입이므로 판정 갈림.
    assert comp.verdict_differs is True
    assert result.summary.live_only_entered == 1


def test_price_off_flags_only_beyond_tick_error() -> None:
    """진입가차가 측정 임계(평균×K)를 넘는 셋업만 🟠 가격 벗어남. 작은 차이는 조용히."""
    # 세 개는 진입가차 ≈2bp, 하나는 ≈20bp. 임계 = 3×평균 = 3×6.5 = 19.5bp → 20bp만 초과.
    pairs = [
        (_live(zone_start=z, fill_price=100.02, pnl_pct=0.1), _bt(zone_start=z, fill_price=100.0))
        for z in (1000, 2000, 3000)
    ]
    big_live = _live(zone_start=4000, fill_price=100.20, pnl_pct=0.1)
    big_bt = _bt(zone_start=4000, fill_price=100.0)
    live_rows = [p[0] for p in pairs] + [big_live]
    bt_rows = [p[1] for p in pairs] + [big_bt]
    result = build_setup_comparisons(live_rows, bt_rows)
    flagged = [c for c in result.comparisons if c.price_off]
    assert len(flagged) == 1
    assert flagged[0].key[3] == 4000  # 20bp 셋업(존 시작 4000)만 플래그.
    assert result.summary.price_off == 1


def test_price_off_threshold_is_measured_not_hardcoded() -> None:
    """임계값 = tick_parity가 잰 매칭 체결 가격차 평균 × K(하드코딩된 bp 값이 아니다)."""
    assert price_off_threshold_bps([2.0, 2.0, 2.0]) == TICK_DIVERGENCE_K * 2.0
    assert price_off_threshold_bps([1.0, 1.0, 10.0]) == TICK_DIVERGENCE_K * 4.0
    assert price_off_threshold_bps([]) == 0.0  # 표본 없으면 임계 없음(벗어남으로 안 침).


def test_setup_key_none_when_identity_missing() -> None:
    """존 정체성/탭 순번 중 하나라도 없으면 조인 키가 없다(강제 조인 방지)."""
    assert setup_key(_live()) == (_SYMBOL, _TF, True, 1000, 1100, 0)
    assert setup_key(_live(zone_start=None)) is None
    assert setup_key(_live(tap_index=None)) is None


def test_keyless_rows_are_not_force_joined() -> None:
    """존 정체성이 없는 라이브·백테 행은 각자 유일 키라 서로 강제 조인되지 않는다."""
    live = _live(zone_start=None)  # 키 없음.
    bt = _bt(zone_start=None)  # 키 없음.
    result = build_setup_comparisons([live], [bt])
    # 두 줄(각자 한쪽만)로 남아야 한다 — 한 줄로 합쳐지지 않는다.
    assert len(result.comparisons) == 2
    assert {(c.live is not None, c.backtest is not None) for c in result.comparisons} == {
        (True, False),
        (False, True),
    }


def test_filter_comparisons_modes() -> None:
    """필터 칩: 전체/불일치만/일치."""
    matched = build_setup_comparisons([_live()], [_bt()]).comparisons
    diverged = build_setup_comparisons(
        [_live(status="거부(사이징0)", fill_price=None, pnl_pct=None)], [_bt()]
    ).comparisons
    both = list(matched) + list(diverged)
    assert len(filter_comparisons(both, "all")) == 2
    assert len(filter_comparisons(both, "diverge")) == 1
    assert len(filter_comparisons(both, "match")) == 1
    assert len(filter_comparisons(both, "그밖")) == 2  # 알 수 없는 모드 → 전체.


def test_unfilled_backtest_does_not_count_as_entered() -> None:
    """백테 미체결 셋업은 진입이 아니다 — 라이브가 진입했으면 판정 갈림."""
    live = _live()  # 청산(진입).
    bt = _bt(status=STATUS_BACKTEST_UNFILLED, fill_price=None, pnl_pct=None)
    result = build_setup_comparisons([live], [bt])
    comp = result.comparisons[0]
    assert comp.live is not None and comp.backtest is not None
    assert comp.verdict_differs is True  # 라이브 진입 · 백테 미체결.
    assert result.summary.live_only_entered == 1


# --------------------------------------------------------------------------- #
# 재진입 구제 조인 (WAN-305)
# --------------------------------------------------------------------------- #

from dataclasses import replace  # noqa: E402


def test_reentry_rescue_join_both_marked() -> None:
    """재진입 행이 존 정체성으로 짝지어져 🔴 판정 갈림이 아니다(WAN-305 완료기준 3).

    라이브 재무장(origin='reentry', tap_index = 재무장 시점 탭 카운트)과 백테 재진입
    (tap_index 0)은 탭 순번이 달라 정확 키로는 영원히 안 맞는다 — 구제 조인이 잇는다.
    """
    live = replace(_live(tap_index=5), is_reentry=True)
    bt = replace(_bt(tap_index=0), is_reentry=True)
    result = build_setup_comparisons([live], [bt])
    assert len(result.comparisons) == 1
    comp = result.comparisons[0]
    assert comp.live is live and comp.backtest is bt
    assert not comp.verdict_differs
    assert result.summary.diverged == 0


def test_reentry_rescue_join_one_side_marked() -> None:
    """재시작-재탭 모양: 라이브는 일반 탭 행(origin 미상 NULL), 백테만 재진입 — 그래도 잇는다.

    2026-08-13 LTC 1h 실사례의 옛 장부 모양(재무장이 재시작으로 폐기된 뒤 새 탭으로 재진입,
    origin 열 도입 전이라 판별 불가) — 백테 쪽 재진입 표기 하나로 존 정체성 조인이 성립한다.
    """
    live = _live(tap_index=2)  # is_reentry=None(옛 장부 행).
    bt = replace(_bt(tap_index=0), is_reentry=True)
    result = build_setup_comparisons([live], [bt])
    assert len(result.comparisons) == 1
    assert not result.comparisons[0].verdict_differs


def test_non_reentry_singles_are_not_force_joined() -> None:
    """둘 다 재진입이 아닌 한쪽짜리 행은 강제 조인하지 않는다 — 진짜 판정 갈림은 남는다."""
    live = _live(tap_index=1)
    bt = _bt(tap_index=2)  # 같은 존, 다른 탭 — 어느 쪽도 재진입 아님.
    result = build_setup_comparisons([live], [bt])
    assert len(result.comparisons) == 2
    assert result.summary.diverged == 2


def test_ltc_fixture_shape_base_plus_reentry() -> None:
    """LTC 1h 픽스처 모양: base 탭#0 정확 조인 + 재진입 구제 조인 — 빨간 카드 0."""
    live_base = _live(tap_index=0)
    bt_base = _bt(tap_index=0)
    live_re = replace(_live(tap_index=2, fill_ms=1_700_600_000_000), is_reentry=True)
    bt_re = replace(_bt(tap_index=0), is_reentry=True)
    result = build_setup_comparisons([live_base, live_re], [bt_base, bt_re])
    assert result.summary.total == 2
    assert result.summary.diverged == 0


# --- WAN-333: 짝 없는 줄을 「일치」로 세지 않는다 -----------------------------


def test_unpaired_row_is_not_counted_as_matched() -> None:
    """🚨 짝이 없는데 양쪽 다 미진입으로 보이는 줄이 「일치」로 집계되면 안 된다 (WAN-333).

    이것이 이 이슈의 핵심이다 — 조인이 실패한 줄은 왼쪽(또는 오른쪽)이 비어 `live_entered ==
    backtest_entered == False`가 되고, 옛 셈법(`matched = total − diverged`)은 그것을 「판정
    일치」로 세었다. 즉 **파리티가 깨진 것을 파리티 측정기가 「일치합니다」로 보고**했다.
    """
    live = _live(status="건너뜀(존폭)", fill_price=None, pnl_pct=None)
    result = build_setup_comparisons([live], [])  # 백테 짝 없음.
    comp = result.comparisons[0]
    assert comp.paired is False
    assert comp.live_entered is False and comp.backtest_entered is False
    assert comp.verdict_differs is False  # 「둘 다 미진입」으로 보인다(그래서 위험하다).
    assert result.summary.matched == 0  # 🚨 옛 동작은 1이었다.
    assert result.summary.unpaired == 1
    assert result.summary.unpaired_live_only == 1
    assert result.summary.unpaired_backtest_only == 0
    assert result.summary.paired == 0


def test_unpaired_backtest_only_row_is_counted_on_the_other_side() -> None:
    """백테만 있는 「건너뜀(존폭)」 줄도 같은 규칙 — 짝 없음이지 일치가 아니다."""
    bt = _bt(status=STATUS_BACKTEST_SKIP_ZONE_WIDTH, fill_price=None, pnl_pct=None)
    result = build_setup_comparisons([], [bt])
    assert result.summary.matched == 0
    assert result.summary.unpaired_backtest_only == 1
    assert result.summary.unpaired_live_only == 0


def test_join_census_counts_both_sides_and_pairs() -> None:
    """인구조사가 양쪽 셋업 수·키 있는 수·짝 수를 낸다 — 「짝 0」의 이유가 보인다."""
    from live.stop_width_parity import join_census

    live_rows = [_live(), _live(zone_start=5000, tap_index=0)]
    bt_rows = [_bt()]
    result = build_setup_comparisons(live_rows, bt_rows)
    census = join_census(live_rows, bt_rows, result.comparisons)
    assert census.live_rows == 2 and census.backtest_rows == 1
    assert census.live_keyed == 2 and census.backtest_keyed == 1
    assert census.paired == 1
    assert census.unpaired_live_only == 1 and census.unpaired_backtest_only == 0
    assert census.key_wiring_broken is False


def test_join_census_flags_missing_join_key_wiring() -> None:
    """🚨 백테 쪽에 조인 키가 통째로 없으면 「배선 오류」로 지목된다 (WAN-333 §2 원인).

    `backtest_timeline_rows`(거래 행)를 먹이면 실제로 이 상태가 된다 — 그 행은
    `zone_start_time`·`zone_confirmed_time`·`tap_index`를 아예 싣지 않아 `setup_key`가
    전부 `None`이고, 짝이 **영원히 0건**이 된다(워밍업·좌표와 무관).
    """
    from live.stop_width_parity import join_census

    live_rows = [_live()]
    bt_rows = [_bt(zone_start=None, zone_confirmed=None, tap_index=None)]
    result = build_setup_comparisons(live_rows, bt_rows)
    census = join_census(live_rows, bt_rows, result.comparisons)
    assert census.backtest_rows == 1 and census.backtest_keyed == 0
    assert census.paired == 0
    assert census.key_wiring_broken is True


def test_filter_match_mode_excludes_unpaired_rows() -> None:
    """「일치」 칩은 짝지어진 줄만 낸다 — 요약 카드와 화면이 갈라지지 않게."""
    paired = build_setup_comparisons([_live()], [_bt()]).comparisons
    unpaired = build_setup_comparisons(
        [_live(status="건너뜀(존폭)", fill_price=None, pnl_pct=None)], []
    ).comparisons
    both = list(paired) + list(unpaired)
    assert len(filter_comparisons(both, "match")) == 1
    assert len(filter_comparisons(both, "unpaired")) == 1
    assert len(filter_comparisons(both, "all")) == 2
