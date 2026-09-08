"""WAN-409의 자(尺)를 **동작으로** 고정한다 — 라벨이 아니라 숫자가 갈리는지로 건다.

백테스트 격자(48칸 · 6년)는 여기서 안 돌린다. 대신 이 이슈가 지는 방식 넷을 각각 잠근다:

1. **존 식별자 교체가 실제로 뜻이 있는가** — 원 관측의 대리변수(`손절가` 일치)와 진짜
   `zone_key`가 **갈리는 표본**에서 두 판정이 다르게 나오는지. 갈리지 않으면 완료기준 1이
   아무것도 안 한 것이다.
2. **재진입 후보에 존 식별자가 실제로 붙는가**(WAN-409 배선) — 안 붙으면 채택 북 거래의 약
   10.7%가 「모르는 존」으로 새고, 그건 **개수만 세면 안 보인다**(WAN-345 부류).
3. **읽으면 틀리는 비율을 안 찍는가**(WAN-115 부호·크기 함정).
4. **라이브 표본이 문턱 미만이면 판정하지 않는가**(WAN-194: 안 났다와 못 봤다는 다르다).
"""

from __future__ import annotations

import math

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan228_reentry_census import reentry_candidates
from backtest.wan409_invalidation_cascade import (
    GROUP_CASCADE,
    GROUP_STOP_OTHER,
    GROUP_TP_OTHER,
    GROUP_TP_SAME,
    ZONE_ID_PROXY,
    ZONE_ID_REAL,
    ArmChecksumRow,
    CascadeRow,
    TradeFact,
    _cell_kwargs,
    _share_of_net_total,
    agreement_row,
    build_summary_markdown,
    census_rows,
    chain_rows,
    classify,
)
from backtest.zone_limit_backtest import _Candidate
from strategy.models import ConfluenceParams, OrderBlock, OrderBlockDirection

CELL = ("BTC/USDT:USDT", "15m")
MINUTE = 60_000
HTF_MS = 3_600_000


def _fact(
    *,
    entry: int,
    exit_: int,
    is_stop: bool,
    zone: int | None,
    stop_price: float,
    net_r: float = -1.0,
    invalidation: bool = False,
    reentry: bool = False,
    cell: tuple[str, str] = CELL,
    entry_price: float = 101.0,
    exit_price: float = 100.0,
) -> TradeFact:
    return TradeFact(
        cell=cell,
        entry_time=entry * MINUTE,
        exit_time=exit_ * MINUTE,
        is_stop=is_stop,
        entry_price=entry_price,
        exit_price=exit_price,
        net_r=net_r,
        zone_key=None if zone is None else frozenset({zone}),
        stop_price=stop_price,
        entry_after_invalidation=invalidation,
        is_reentry=reentry,
    )


# --------------------------------------------------------------------------- #
# 1. 존 식별자 교체 — 두 자가 실제로 갈린다
# --------------------------------------------------------------------------- #


def test_proxy_counts_a_different_zone_that_shares_a_stop_price() -> None:
    """🚨 이 이슈의 핵심 — **손절가가 같은 다른 존**을 대리변수는 무더기로 세고 진짜는 안 센다.

    원 관측 391건에 이 부류가 섞여 있었는지가 완료기준 1이다. 갈리는 표본에서 두 판정이 같은
    답을 내면 존 식별자를 갈아 끼운 의미가 없다.
    """
    facts = [
        _fact(entry=0, exit_=1, is_stop=True, zone=1, stop_price=100.0),
        _fact(entry=2, exit_=3, is_stop=True, zone=2, stop_price=100.0),  # 다른 존, 같은 손절가
    ]
    second = classify(facts)[0]
    assert second.group_proxy == GROUP_CASCADE
    assert second.group_real == GROUP_STOP_OTHER

    agree = agreement_row(classify(facts), arm="base", segment="full")
    assert (agree.both, agree.real_only, agree.proxy_only) == (0, 0, 1)


def test_same_zone_with_moved_stop_price_is_caught_only_by_the_real_id() -> None:
    """거울상 — 같은 존인데 손절가가 다르면(밴드 오버라이드) 대리변수가 **놓친다**."""
    facts = [
        _fact(entry=0, exit_=1, is_stop=True, zone=7, stop_price=100.0),
        _fact(entry=2, exit_=3, is_stop=True, zone=7, stop_price=100.5),
    ]
    second = classify(facts)[0]
    assert second.group_real == GROUP_CASCADE
    assert second.group_proxy == GROUP_STOP_OTHER


def test_unknown_zone_key_is_never_called_the_same_zone() -> None:
    """`zone_key`가 없으면 「모른다」이지 「같다」가 아니다 — 무더기를 부풀리지 않는다."""
    facts = [
        _fact(entry=0, exit_=1, is_stop=True, zone=None, stop_price=100.0),
        _fact(entry=2, exit_=3, is_stop=True, zone=None, stop_price=100.0),
    ]
    assert classify(facts)[0].group_real == GROUP_STOP_OTHER


def test_groups_split_on_previous_outcome_and_first_trade_is_excluded() -> None:
    """부류 넷 · 칸의 첫 거래는 직전이 없어 분류에서 빠진다(원 관측 표의 분모와 같은 규약)."""
    facts = [
        _fact(entry=0, exit_=1, is_stop=False, zone=1, stop_price=100.0),
        _fact(entry=2, exit_=3, is_stop=True, zone=1, stop_price=100.0),  # 직전 익절 · 같은 존
        _fact(entry=4, exit_=5, is_stop=False, zone=1, stop_price=100.0),  # 직전 손절 · 같은 존
        _fact(entry=6, exit_=7, is_stop=True, zone=9, stop_price=200.0),  # 직전 익절 · 다른 존
    ]
    groups = [c.group_real for c in classify(facts)]
    assert groups == [GROUP_TP_SAME, GROUP_CASCADE, GROUP_TP_OTHER]


def test_cells_do_not_bleed_into_each_other() -> None:
    """다른 칸의 거래는 「직전 거래」가 될 수 없다 — 북의 칸은 각자 한 포지션이다."""
    other = ("ETH/USDT:USDT", "15m")
    facts = [
        _fact(entry=0, exit_=1, is_stop=True, zone=1, stop_price=100.0),
        _fact(entry=2, exit_=3, is_stop=True, zone=1, stop_price=100.0, cell=other),
    ]
    assert classify(facts) == []


# --------------------------------------------------------------------------- #
# 2. 사슬 길이 · 인구조사 열
# --------------------------------------------------------------------------- #


def test_chain_of_three_is_one_chain_of_length_three() -> None:
    """같은 존에서 연달아 셋이면 **사슬 1개 · 길이 3**이다(쌍 2개가 아니다)."""
    facts = [
        _fact(entry=0, exit_=0, is_stop=True, zone=3, stop_price=100.0),
        _fact(entry=0, exit_=0, is_stop=True, zone=3, stop_price=100.0),
        _fact(entry=0, exit_=0, is_stop=True, zone=3, stop_price=100.0),
    ]
    real = [r for r in chain_rows(facts, arm="base", segment="full") if r.zone_id == ZONE_ID_REAL]
    assert [(r.chain_length, r.num_chains) for r in real] == [(3, 1)]


def test_census_row_carries_the_invalidation_and_same_minute_columns() -> None:
    """§2의 증거 열 — 무더기의 「무효화 봉 체결」·「같은 1분」 비율이 실제로 실린다."""
    facts = [
        _fact(entry=0, exit_=0, is_stop=True, zone=1, stop_price=100.0),
        _fact(entry=0, exit_=0, is_stop=True, zone=1, stop_price=100.0, invalidation=True),
    ]
    rows = census_rows(classify(facts), arm="base", segment="full", net_total=-2.0)
    cascade = next(r for r in rows if r.zone_id == ZONE_ID_REAL and r.group == GROUP_CASCADE)
    assert cascade.num_trades == 1
    assert cascade.invalidation_bar_share == 100.0
    assert cascade.same_minute_share == 100.0
    assert cascade.median_gap_minutes == 0.0


def test_proxy_and_real_rows_are_both_emitted() -> None:
    """두 식별자의 행이 **함께** 나와야 교차표가 표에서 대조된다."""
    facts = [
        _fact(entry=0, exit_=1, is_stop=True, zone=1, stop_price=100.0),
        _fact(entry=2, exit_=3, is_stop=True, zone=2, stop_price=100.0),
    ]
    rows = census_rows(classify(facts), arm="base", segment="full", net_total=-2.0)
    assert {r.zone_id for r in rows} == {ZONE_ID_REAL, ZONE_ID_PROXY}


# --------------------------------------------------------------------------- #
# 3. 읽으면 틀리는 비율은 안 찍는다 (WAN-115)
# --------------------------------------------------------------------------- #


def test_share_of_net_total_refuses_a_positive_denominator() -> None:
    assert _share_of_net_total(-5.0, 12.0) is None


def test_share_of_net_total_refuses_a_share_above_one_hundred_percent() -> None:
    """그룹 손실이 구간 순손익보다 크면 「비중」이라는 낱말이 거짓이 된다."""
    assert _share_of_net_total(-5.7, -2.0) is None
    assert _share_of_net_total(-441.3, -1792.1) is not None


def test_summary_prints_a_dash_instead_of_a_misleading_ratio() -> None:
    """표에 `—`가 찍히고 숫자가 새지 않는다 — 렌더러까지 동작으로 건다."""
    row = CascadeRow(
        arm="base",
        segment="full",
        zone_id=ZONE_ID_REAL,
        group=GROUP_CASCADE,
        num_trades=3,
        num_classified=10,
        share_of_classified=30.0,
        stop_rate=100.0,
        mean_net_r=-1.1,
        sum_net_r=-3.3,
        share_of_net_total=None,
        same_minute_share=100.0,
        median_gap_minutes=0.0,
        invalidation_bar_share=100.0,
        duplicate_share=0.0,
        reentry_share=0.0,
    )
    markdown = build_summary_markdown([row], [], [], [], [])
    assert "| — |" in markdown


def test_summary_says_which_arms_lack_the_cold_segments() -> None:
    """없는 구간을 빈 값으로 위장하지 않는다 — 커버리지 줄이 표에 뜬다."""
    row = CascadeRow(
        arm="base",
        segment="full",
        zone_id=ZONE_ID_REAL,
        group=GROUP_CASCADE,
        num_trades=0,
        num_classified=0,
        share_of_classified=0.0,
        stop_rate=0.0,
        mean_net_r=0.0,
        sum_net_r=0.0,
        share_of_net_total=None,
        same_minute_share=0.0,
        median_gap_minutes=0.0,
        invalidation_bar_share=0.0,
        duplicate_share=0.0,
        reentry_share=0.0,
    )
    markdown = build_summary_markdown([row], [], [], [], [])
    assert "차가운 `is`/`oos` 구간이 없는 팔이 있다" in markdown
    assert "| base | full |" in markdown


def test_checksum_row_carries_the_arm_label() -> None:
    """`--append`가 팔 단위로 갈아 끼우려면 검산 줄에도 팔이 있어야 한다."""
    row = ArmChecksumRow(
        arm="base", check="(b)", segment="all", metric="x", left=0.0, right=0.0, abs_diff=0.0
    )
    assert row.arm == "base"


# --------------------------------------------------------------------------- #
# 4. 팔 배선 — 축을 **하나만** 얹는다
# --------------------------------------------------------------------------- #


def test_base_arm_adds_no_axis_of_its_own() -> None:
    """기준 팔이 채택 기본값을 그대로 읽어야 검산 (a)가 성립한다(WAN-305)."""
    kwargs = _cell_kwargs("base")
    assert "retap_mode" not in kwargs
    assert "invalidation_cancel" not in kwargs
    assert kwargs["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


def test_counterfactual_arms_move_exactly_one_axis() -> None:
    assert _cell_kwargs("retap_once")["retap_mode"] == "once"
    assert "invalidation_cancel" not in _cell_kwargs("retap_once")
    assert _cell_kwargs("cancel_bar_open")["invalidation_cancel"] == "bar_open"
    assert "retap_mode" not in _cell_kwargs("cancel_bar_open")


def test_unknown_arm_is_refused() -> None:
    """라벨만 붙은 실행을 막는다 — 모르는 팔은 조용히 기준 팔로 돌지 않는다."""
    try:
        _cell_kwargs("nope")
    except ValueError:
        return
    raise AssertionError("모르는 팔이 거부되지 않았습니다")


# --------------------------------------------------------------------------- #
# 5. 재진입 후보에 존 식별자가 **실제로** 붙는가 (WAN-409 배선)
# --------------------------------------------------------------------------- #


def _substeps(bars: list[tuple[int, float, float, float]]) -> list[SubStep]:
    return [
        SubStep(
            time=minute * MINUTE,
            high=high,
            low=low,
            close=close,
            htf_bar_time=((minute * MINUTE) // HTF_MS) * HTF_MS,
        )
        for minute, high, low, close in bars
    ]


def _parent_with_zone(zone: frozenset[int] | None) -> _Candidate:
    ob = OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=105.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=1.5,
        break_time=None,
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
        zone_key=zone,
        tap_index=2,
    )


def _reentries(parent: _Candidate) -> list[_Candidate]:
    bars = [
        (1, 116.0, 114.0, 115.0),
        (10, 101.0, 99.0, 100.5),  # 지정가 100 터치 → 재진입 체결
        (20, 116.0, 108.0, 115.0),  # 익절
    ]
    substeps = _substeps(bars)
    return reentry_candidates(
        parent,
        parent_exit_time=0,
        substeps=substeps,
        substep_times=[s.time for s in substeps],
        htf_times=[0],
        htf_closes=[100.0],
        params=harness.pin_invalidation_cancel(ConfluenceParams()),
        cfg=BacktestConfig(),
        funding_rates=None,
    )


def test_reentry_candidates_carry_the_parent_zone_key() -> None:
    """🚨 라벨이 아니라 **값**으로 건다 — 재진입은 정의상 부모와 같은 존이다(WAN-273).

    안 붙어 있으면 존 단위 인구조사가 재진입 거래를 통째로 「모르는 존」으로 흘린다(채택 북
    `oos_warm` 거래의 약 10.7%). 개수만 세는 테스트는 그 실패를 통과시킨다.
    """
    zone = frozenset({41})
    reentries = _reentries(_parent_with_zone(zone))
    assert reentries, "재진입 후보가 하나도 안 나왔습니다 — 픽스처가 깨졌습니다"
    assert all(c.zone_key == zone for c in reentries)


def test_reentry_candidates_do_not_invent_a_tap_index() -> None:
    """재진입에는 탭이 없다 — 부모의 탭 번호를 베끼면 「몇 번째 탭인가」 표가 조용히 틀린다."""
    reentries = _reentries(_parent_with_zone(frozenset({41})))
    assert all(c.tap_index == 0 for c in reentries)


def test_reentry_zone_key_stays_none_when_the_parent_has_none() -> None:
    """부모가 모르면 재진입도 모른다 — 지어내지 않는다(WAN-194)."""
    reentries = _reentries(_parent_with_zone(None))
    assert all(c.zone_key is None for c in reentries)


def test_classified_reentry_is_grouped_with_its_parent_zone() -> None:
    """배선의 **효과**까지 — 존 식별자가 붙으면 재진입 거래가 같은 존으로 묶인다."""
    facts = [
        _fact(entry=0, exit_=1, is_stop=True, zone=5, stop_price=100.0),
        _fact(entry=2, exit_=3, is_stop=True, zone=5, stop_price=100.0, reentry=True),
    ]
    item = classify(facts)[0]
    assert item.group_real == GROUP_CASCADE
    rows = census_rows([item], arm="base", segment="full", net_total=-2.0)
    cascade = next(r for r in rows if r.zone_id == ZONE_ID_REAL and r.group == GROUP_CASCADE)
    assert cascade.reentry_share == 100.0


def test_gap_minutes_measures_previous_exit_to_this_entry() -> None:
    """간격의 정의를 못 박는다 — 「같은 1분」이 무엇인지가 판정 열이다."""
    facts = [
        _fact(entry=0, exit_=5, is_stop=True, zone=1, stop_price=100.0),
        _fact(entry=8, exit_=9, is_stop=True, zone=1, stop_price=100.0),
    ]
    assert classify(facts)[0].gap_minutes == 3.0
    assert not math.isnan(classify(facts)[0].gap_minutes)


def test_csv_round_trip_keeps_none_instead_of_nan() -> None:
    """🚨 `--from-csv`·`--append`가 「비율을 내지 않는다」를 `nan%`로 되살리지 않는다.

    pandas가 빈 칸을 `NaN`으로 읽고 pydantic이 그것을 유효한 float으로 받는다 — 가드가
    조용히 뚫리는 자리다(WAN-395가 같은 함정을 겪었다).
    """
    import tempfile
    from pathlib import Path

    from backtest.wan409_invalidation_cascade import _read, rows_to_frame

    row = CascadeRow(
        arm="base",
        segment="full",
        zone_id=ZONE_ID_REAL,
        group=GROUP_CASCADE,
        num_trades=1,
        num_classified=2,
        share_of_classified=50.0,
        stop_rate=100.0,
        mean_net_r=-1.0,
        sum_net_r=-1.0,
        share_of_net_total=None,
        same_minute_share=100.0,
        median_gap_minutes=0.0,
        invalidation_bar_share=100.0,
        duplicate_share=0.0,
        reentry_share=0.0,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "census.csv"
        rows_to_frame([row]).to_csv(path, index=False)
        back = _read(path, CascadeRow)[0]
    assert back.share_of_net_total is None
    assert "nan" not in build_summary_markdown([back], [], [], [], []).lower()


# --------------------------------------------------------------------------- #
# 6. 중복 계상 — 같은 가격 움직임이 두 번 청구되는가 (WAN-409 §2 후속)
# --------------------------------------------------------------------------- #


def test_identical_round_trip_in_the_same_minute_is_flagged_as_duplicate() -> None:
    """🚨 같은 1분 · 같은 진입가 · 같은 청산가 = **같은 모델 사건의 반복**이다.

    실제로 두 번 성립하려면 가격이 그 1분 안에서 지정가까지 되돌아와야 하는데 1분봉에는 그
    증거가 없다. 이 술어가 없으면 「무더기」와 「중복 계상」이 표에서 구분되지 않는다.
    """
    facts = [
        _fact(
            entry=0,
            exit_=0,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=101.0,
            exit_price=99.5,
        ),
        _fact(
            entry=0,
            exit_=0,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=101.0,
            exit_price=99.5,
        ),
    ]
    item = classify(facts)[0]
    assert item.group_real == GROUP_CASCADE
    assert item.duplicate_of_prev


def test_same_minute_but_a_different_price_is_not_a_duplicate() -> None:
    """가격이 다르면 진짜 다른 체결일 수 있다 — 덧세지 않는다(보수적인 쪽)."""
    facts = [
        _fact(
            entry=0,
            exit_=0,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=101.0,
            exit_price=99.5,
        ),
        _fact(
            entry=0,
            exit_=0,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=100.8,
            exit_price=99.5,
        ),
    ]
    assert not classify(facts)[0].duplicate_of_prev


def test_same_price_in_a_later_minute_is_not_a_duplicate() -> None:
    """분이 다르면 가격이 되돌아왔다는 증거가 봉 사이에 있다 — 중복이 아니다."""
    facts = [
        _fact(
            entry=0,
            exit_=0,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=101.0,
            exit_price=99.5,
        ),
        _fact(
            entry=5,
            exit_=5,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=101.0,
            exit_price=99.5,
        ),
    ]
    assert not classify(facts)[0].duplicate_of_prev


def test_duplicate_share_reaches_the_census_row() -> None:
    facts = [
        _fact(
            entry=0,
            exit_=0,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=101.0,
            exit_price=99.5,
        ),
        _fact(
            entry=0,
            exit_=0,
            is_stop=True,
            zone=1,
            stop_price=100.0,
            entry_price=101.0,
            exit_price=99.5,
        ),
    ]
    rows = census_rows(classify(facts), arm="base", segment="full", net_total=-2.0)
    cascade = next(r for r in rows if r.zone_id == ZONE_ID_REAL and r.group == GROUP_CASCADE)
    assert cascade.duplicate_share == 100.0


def test_placement_arm_shares_the_base_candidate_recipe() -> None:
    """🚨 배치 축 팔은 후보를 **다시 만들지 않는다** — 캐시가 히트해야 분 단위로 끝난다.

    후보 인자가 기준 팔과 한 글자라도 다르면 payload 키가 달라져 4~5시간짜리 재생성이 돈다.
    """
    from backtest.wan409_invalidation_cascade import ARM_NO_SAME_STEP_REOPEN, PLACEMENT_ARMS

    assert ARM_NO_SAME_STEP_REOPEN in PLACEMENT_ARMS
    assert _cell_kwargs(ARM_NO_SAME_STEP_REOPEN) == _cell_kwargs("base")
