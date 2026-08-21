"""짝 없는 라이브 셋업 × 백테 존 대장 감사 (WAN-343 §2).

라벨이 아니라 **동작**으로 고정한다 — 같은 「존 없음」 관찰이 창 밖인지·미탐지인지·무효화
선행인지에 따라 후속이 완전히 달라지므로(문서화된 근사 대 파리티 결함), 갈래를 섞는 회귀는
테스트가 죽어야 한다.
"""

from __future__ import annotations

import pytest

from live.unpaired_setups import (
    BUCKET_NO_KEY,
    BUCKET_TAP_DIFFERS,
    BUCKET_ZONE_MISSING,
    UnpairedSetup,
)
from live.zone_audit import (
    REASON_CONFIRM_DIFFERS,
    REASON_INVALIDATED,
    REASON_NO_TAP,
    REASON_NOT_APPLICABLE,
    REASON_NOT_DETECTED,
    REASON_SWEPT,
    REASON_UNEXPLAINED,
    REASON_WINDOW,
    audit_unpaired,
    render_zone_audit,
)
from live.zone_facts import CellZoneFacts, ZoneFact

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"
_H = 3_600_000
#: 못 박은 기준 시각 — 봉 경계에 정렬돼 있어야 `_bar_of`가 예상대로 떨어진다.
_T0 = 1_784_505_600_000  # 2026-07-20 00:00:00 UTC


def _setup(
    *,
    zone_start: int | None = _T0,
    zone_confirmed: int | None = _T0 + 2 * _H,
    focus_ms: int | None = _T0 + 5 * _H,
    bucket: str = BUCKET_ZONE_MISSING,
    side: str = "라이브",
    symbol: str = _SYMBOL,
    timeframe: str = _TF,
) -> UnpairedSetup:
    return UnpairedSetup(
        side=side,
        symbol=symbol,
        timeframe=timeframe,
        is_long=True,
        focus_ms=focus_ms,
        status="미체결",
        zone_start_time=zone_start,
        zone_confirmed_time=zone_confirmed,
        tap_index=0,
        is_reentry=False,
        bucket=bucket,
    )


def _facts(*zones: ZoneFact, window_start: int = _T0 - 100 * _H) -> CellZoneFacts:
    return CellZoneFacts(
        symbol=_SYMBOL,
        timeframe=_TF,
        window_start_ms=window_start,
        window_end_ms=_T0 + 100 * _H,
        zones=zones,
    )


def _zone(
    *,
    start: int = _T0,
    confirmed: int = _T0 + 2 * _H,
    break_time: int | None = None,
    swept_time: int | None = None,
    tapped: tuple[int, ...] = (_T0 + 5 * _H,),
    is_long: bool = True,
) -> ZoneFact:
    return ZoneFact(
        is_long=is_long,
        start_time=start,
        confirmed_time=confirmed,
        break_time=break_time,
        swept_time=swept_time,
        tapped_times=tapped,
    )


def _one(setup: UnpairedSetup, facts: CellZoneFacts | None) -> str:
    mapping = {} if facts is None else {(_SYMBOL, _TF): facts}
    report = audit_unpaired([setup], mapping)
    assert len(report.verdicts) == 1
    return report.verdicts[0].reason


def test_zone_older_than_detection_window_is_window_not_a_defect() -> None:
    """창보다 이른 존은 `창 밖`이다 — 백테가 만들 기회조차 없었으므로 탐지 결함이 아니다."""
    setup = _setup(zone_start=_T0 - 200 * _H)
    assert _one(setup, _facts(_zone(), window_start=_T0 - 100 * _H)) == REASON_WINDOW


def test_zone_absent_inside_window_is_a_detection_gap() -> None:
    """창 안인데 그 존 시작이 대장에 없으면 `존 미탐지` — 엔진 파리티 결함 쪽이다."""
    assert _one(_setup(), _facts(_zone(start=_T0 - 50 * _H))) == REASON_NOT_DETECTED


def test_direction_matters_for_zone_lookup() -> None:
    """같은 시작이어도 **방향이 다르면 다른 존**이다 — 롱 셋업이 숏 존으로 짝지어지면 안 된다."""
    assert _one(_setup(), _facts(_zone(is_long=False))) == REASON_NOT_DETECTED


def test_same_start_different_confirm_is_confirm_bucket() -> None:
    assert _one(_setup(), _facts(_zone(confirmed=_T0 + 3 * _H))) == REASON_CONFIRM_DIFFERS


def test_invalidated_before_tap_is_the_documented_one_bar_lag() -> None:
    """백테가 라이브 탭 봉 **전에** 무효화한 존 — `on_htf_bars`가 적어 둔 알려진 근사."""
    setup = _setup(focus_ms=_T0 + 5 * _H)
    facts = _facts(_zone(break_time=_T0 + 4 * _H))
    report = audit_unpaired([setup], {(_SYMBOL, _TF): facts})
    assert report.verdicts[0].reason == REASON_INVALIDATED
    assert report.verdicts[0].invalidation_lag_bars == 1.0


def test_invalidated_on_the_same_bar_is_counted_separately() -> None:
    """같은 봉 무효화가 그 근사의 **직접 서명**이다 — 요약이 따로 센다."""
    setup = _setup(focus_ms=_T0 + 5 * _H + 17 * 60_000)  # 봉 한가운데 탭.
    facts = _facts(_zone(break_time=_T0 + 5 * _H))
    report = audit_unpaired([setup], {(_SYMBOL, _TF): facts})
    assert report.verdicts[0].reason == REASON_INVALIDATED
    assert report.verdicts[0].invalidation_lag_bars == 0.0
    assert report.same_bar_invalidations == 1


def test_invalidated_after_the_tap_does_not_count_as_preceding() -> None:
    """무효화가 탭 **뒤**면 그 존은 탭 시점에 살아 있었다 — 무효화로 설명하면 안 된다."""
    facts = _facts(_zone(break_time=_T0 + 9 * _H))
    assert _one(_setup(), facts) == REASON_UNEXPLAINED


def test_swept_before_tap() -> None:
    assert _one(_setup(), _facts(_zone(swept_time=_T0 + 4 * _H))) == REASON_SWEPT


def test_alive_zone_without_that_tap_is_the_tap_axis() -> None:
    assert _one(_setup(), _facts(_zone(tapped=(_T0 + 9 * _H,)))) == REASON_NO_TAP


def test_alive_and_tapped_zone_is_unexplained_not_silently_absorbed() -> None:
    """존도 탭도 있는데 셋업 행이 없으면 **설명 안 됨**으로 남긴다 — 조용히 흡수하지 않는다."""
    assert _one(_setup(), _facts(_zone())) == REASON_UNEXPLAINED


def test_longest_lived_zone_wins_so_invalidation_is_not_overcounted() -> None:
    """같은 정체성의 존이 둘이면 **오래 산 쪽**으로 판정한다(무효화 과대 계상 방지)."""
    facts = _facts(_zone(break_time=_T0 + 4 * _H), _zone(break_time=None))
    assert _one(_setup(), facts) == REASON_UNEXPLAINED


def test_no_key_rows_are_not_audited() -> None:
    """부류 (0)은 재진입 등으로 **조인이 성립하지 않는** 행이다 — 탐지 결함으로 세면 안 된다."""
    setup = _setup(bucket=BUCKET_NO_KEY, zone_start=None, zone_confirmed=None)
    assert _one(setup, _facts(_zone())) == REASON_NOT_APPLICABLE


def test_backtest_side_rows_are_not_audited() -> None:
    """백테만 있는 행은 **라이브 존 대장이 없어** 같은 자로 못 잰다 — 지어내지 않는다."""
    assert _one(_setup(side="백테"), _facts(_zone())) == REASON_NOT_APPLICABLE


def test_missing_cell_facts_are_not_a_detection_gap() -> None:
    """대장을 못 받은 칸을 「존이 하나도 없다 = 미탐지」로 읽으면 결함으로 오분류된다."""
    assert _one(_setup(), None) == REASON_NOT_APPLICABLE


def test_cells_are_looked_up_per_symbol_and_timeframe() -> None:
    """다른 칸의 대장으로 판정하지 않는다 — 칸 키가 실제로 조회에 쓰인다."""
    facts = _facts(_zone())
    report = audit_unpaired([_setup(timeframe="4h")], {(_SYMBOL, _TF): facts})
    assert report.verdicts[0].reason == REASON_NOT_APPLICABLE


def test_audited_count_excludes_not_applicable() -> None:
    setups = [_setup(), _setup(side="백테")]
    report = audit_unpaired(setups, {(_SYMBOL, _TF): _facts(_zone())})
    assert len(report.verdicts) == 2
    assert report.audited == 1


def test_verdict_refuses_to_close_on_a_tie() -> None:
    """동률이면 한 사유로 닫지 않는다 — argmax만 보고 결론 내기 금지(WAN-161 규약)."""
    facts = _facts(_zone(), _zone(start=_T0 + _H, confirmed=_T0 + 3 * _H, break_time=_T0 + 4 * _H))
    setups = [
        _setup(),  # 설명 안 됨
        _setup(zone_start=_T0 + _H, zone_confirmed=_T0 + 3 * _H),  # 무효화 선행
    ]
    report = audit_unpaired(setups, {(_SYMBOL, _TF): facts})
    assert "과반 사유 없음" in report.verdict


def test_verdict_names_the_follow_up_when_a_reason_dominates() -> None:
    facts = _facts(_zone(break_time=_T0 + 4 * _H))
    report = audit_unpaired([_setup(), _setup()], {(_SYMBOL, _TF): facts})
    assert REASON_INVALIDATED in report.verdict
    assert "알려진 근사" in report.verdict


def test_verdict_says_so_when_nothing_was_audited() -> None:
    report = audit_unpaired([_setup(side="백테")], {})
    assert "판정된 행이 0건" in report.verdict


def test_empty_report_renders_without_claiming_anything() -> None:
    report = audit_unpaired([], {})
    assert "감사할 짝 없는 셋업이 없습니다" in render_zone_audit(report)


def test_render_shows_reason_and_evidence() -> None:
    facts = _facts(_zone(break_time=_T0 + 4 * _H))
    text = render_zone_audit(audit_unpaired([_setup()], {(_SYMBOL, _TF): facts}))
    assert REASON_INVALIDATED in text
    assert "백테 무효화" in text
    # 「대상 아님」의 뜻을 화면이 밝힌다 — 빈 칸을 「결함 없음」으로 읽지 않도록.
    assert "러너는 존 대장을 영속화하지 않아" in text


@pytest.mark.parametrize("bucket", [BUCKET_ZONE_MISSING, BUCKET_TAP_DIFFERS])
def test_audit_does_not_reclassify_the_join_bucket(bucket: str) -> None:
    """감사는 **다시 분류하지 않는다** — 원래 부류를 그대로 들고 다닌다.

    두 블록이 서로 다른 분류를 얻으면 같은 셋업이 화면에서 두 얼굴을 갖는다.
    """
    report = audit_unpaired([_setup(bucket=bucket)], {(_SYMBOL, _TF): _facts(_zone())})
    assert report.verdicts[0].setup.bucket == bucket
