"""WAN-400: 존 병합 규칙이 원본 파인스크립트와 갈리는 세 자리를 **동작으로** 못 박는다.

세 자리는 성격이 완전히 다르다 — 이 스위트가 그 차이까지 고정한다:

* **A(소멸 존 제외)** — 원본과 **이미 같다**. 원본이 되쓸린 존을 `box.delete()`(렌더)가
  아니라 `bullishOrderBlocksList.remove(i)`로 **데이터 리스트**에서 빼고 그 리스트가 곧
  병합 입력이라는 사슬을, 원본 파일 자체를 읽어 고정한다(§1). 코드 변경 없음.
* **B(`break_time` 산정)** — 진짜로 갈린다. 두 규칙을 **반례로** 고정하고, **기본값이
  여전히 `distal`**임을 함께 건다(★ 사용자 결정 전에 조용히 넘어가지 않게).
* **C(탭 상태 키)** — 현행 `cluster`가 「구성이 바뀌면 리셋」이라 **가짜 재탭**을 내는
  것과, 옵트인 `zone`이 그것만 없애고 **WAN-82(새 구성 존의 첫 탭)는 유지**하는 것을
  함께 건다.

그리고 **기본값에서는 아무것도 안 바뀐다** — 노브를 명시하지 않은 병합 실행이 명시적
채택값과 비트 단위로 같다(`combine_obs=False` 채택 경로는 병합을 아예 안 돈다).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan400_merge_parity_census import (
    _first_taps,
    distal_break_time,
    pine_max_break_time,
)
from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockSignal,
)
from strategy.order_blocks import (
    OrderBlockDetector,
    _generate_merged_signals,
    _make_merged_group,
    _pine_max_break_time,
)

PINE_PATH = Path("strategy/reference/fluxchart_volumized_ob.pine")


def _bull(
    top: float,
    bottom: float,
    *,
    confirmed: int,
    break_time: int | None = None,
    swept_time: int | None = None,
) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=top,
        bottom=bottom,
        start_time=0,
        confirmed_time=confirmed,
        ob_volume=30.0,
        ob_low_volume=10.0,
        ob_high_volume=20.0,
        breaker=break_time is not None,
        break_time=break_time,
        swept_time=swept_time,
    )


def _keys(signals: list[OrderBlockSignal]) -> list[frozenset[int]]:
    return [sig.zone_key for sig in signals if sig.zone_key is not None]


# --------------------------------------------------------------------------- #
# A — 소멸 존을 병합 후보에서 빼는 것은 **원본과 같다** (§1)
# --------------------------------------------------------------------------- #


def test_pine_removes_swept_zone_from_the_data_list_not_only_the_box() -> None:
    """원본은 되쓸린 존을 **데이터 리스트**에서 뺀다 — §1 판정의 근거를 원본에서 고정한다.

    🚨 이 테스트가 지키는 것은 코드가 아니라 **판정**이다. 「원본은 소멸 존을 겹침 판정에서
    안 뺀다」는 읽기가 다시 나오면 A를 「고쳐야 할 차이」로 오해하게 되는데, 그러면 우리가
    원본과 **멀어지는** 방향으로 엔진을 바꾸게 된다.
    """
    text = PINE_PATH.read_text(encoding="utf-8")
    # (1) breaker가 된 뒤 반대쪽으로 되쓸리면 리스트에서 뺀다(강세·약세 대칭).
    assert "bullishOrderBlocksList.remove(i)" in text
    assert "bearishOrderBlocksList.remove(i)" in text
    # (2) 그 리스트가 곧 병합 입력(`allOrderBlocksList`)의 원천이다.
    assert "curTimeframe.bullishOrderBlocksList.get(j)" in text
    assert "allOrderBlocksList.unshift(createOrderBlock(orderBlockInfo.copy(orderBlockInfoF)))" in (
        text
    )
    # (3) 병합은 그 `allOrderBlocksList`만 훑는다.
    assert "curOB1 = allOrderBlocksList.get(i)" in text


def test_swept_zone_leaves_the_merge_candidate_set() -> None:
    """소멸한 구성 존이 빠지면 병합 존이 **줄어든다**(= 원본 `remove(i)`와 같은 결과)."""
    # A(위)가 소멸하고 B(아래·distal)가 남는다 → 클러스터가 {A,B} → {B}로 줄어든다.
    archive = [
        _bull(105.0, 100.0, confirmed=1, break_time=3, swept_time=5),  # A
        _bull(103.0, 98.0, confirmed=1),  # B (distal, 계속 살아 있음)
    ]
    times = [0, 1, 2, 3, 4, 5]
    highs = [125.0, 115.0, 104.0, 104.0, 104.0, 104.0]
    lows = [120.0, 110.0, 99.0, 99.0, 99.0, 99.0]
    closes = [122.0, 112.0, 101.0, 101.0, 101.0, 101.0]

    signals = _generate_merged_signals(archive, times, highs, lows, closes, include_retaps=True)
    # t2의 첫 탭은 합집합 {A,B}에서 난다.
    assert _keys(signals)[0] == frozenset({0, 1})
    # t5(소멸 이후)에는 A가 후보에서 빠져 클러스터가 {B}뿐이다.
    late = [sig for sig in signals if sig.trigger_time == 5]
    assert late and all(sig.zone_key == frozenset({1}) for sig in late)
    assert all(sig.order_block.top == 103.0 for sig in late)  # 합집합 105가 아니다.


# --------------------------------------------------------------------------- #
# B — `break_time` 산정 (★ 사용자 결정 대기)
# --------------------------------------------------------------------------- #


def test_default_break_time_rule_is_still_distal() -> None:
    """★ 사용자 결정 전까지 기본값은 `distal`이다 — 조용히 넘어가지 않게 건다."""
    assert OrderBlockParams().merged_break_time_rule == "distal"
    assert OrderBlockParams().merged_tap_state == "cluster"


def test_pine_max_folds_to_the_latest_death_on_a_configuration_price_cannot_produce() -> None:
    """🚨 이 배치는 **어떤 가격 경로로도 만들 수 없다** — 접기 함수의 단위 테스트일 뿐이다.

    이슈 §2(그리고 이 PR의 초판 보고)가 *"distal이 먼저 죽고 다른 존이 나중에 죽으면
    원본이 더 오래 산다"* 를 두 규칙의 갈림 중 하나로 적었는데, **사용자 지적대로 그
    상황은 물리적으로 불가능하다.** 강세 기준 증명(약세는 대칭):

    * A를 distal(`bottom` 최저), B를 같은 클러스터의 다른 구성 존(`bottom_B > bottom_A`)
      이라 하자. A가 `t_A`에 깨지면 `low(t_A) < bottom_A < bottom_B`다.
    * **B가 `t_A` 시점에 이미 확정돼 있었다면** 같은 봉이 `bottom_B`도 뚫으므로 B는
      `t_A` **이전이나 같은 봉**에 깨진다 → 「B가 더 늦게」가 불가능하다.
    * **B가 `t_A` 뒤에 확정됐다면** `_create_bullish`가 B의 `bottom`을 그 형성 창의
      **최저 저가**로 잡고 `start_time`을 **바로 그 봉**으로 잡는다(원본 `boxBtm`/`boxLoc`와
      같다). 그러면 둘 중 하나다:
      - `t_A`가 그 창 **안**이면 `bottom_B <= low(t_A) < bottom_A`라 **B가 distal**이 된다
        (가정 위배).
      - `t_A`가 창 **밖**(앞)이면 `start_time_B > t_A = break_time_A`라 `obs_touch`의
        시간축 교집합이 음수가 되어 **애초에 같은 클러스터가 아니다**.

    📌 **그래서 두 규칙의 실제 차이는 「위쪽 존만 깨진 상태에서 계속 진입할 것인가」 하나뿐**
    이고, 실측이 그 방향을 뒷받침한다(§0: 없어진 탭 6 · **새로 생긴 탭 0**).
    여기서는 `break_time`을 가격 경로 없이 **직접 심어** 접기 규칙만 확인한다.
    """
    a = _bull(105.0, 100.0, confirmed=0, break_time=10)  # distal(bottom 100) · 먼저 죽음
    b = _bull(107.0, 105.0, confirmed=0, break_time=15)  # 나중에 죽음
    members = [(0, a), (1, b)]

    distal_group = _make_merged_group(OrderBlockDirection.BULLISH, members)
    pine_group = _make_merged_group(
        OrderBlockDirection.BULLISH, members, break_time_rule="pine_max"
    )
    assert distal_group.break_time == 10
    assert pine_group.break_time == 15
    assert pine_group.merged_ob.breaker is True


def test_pine_max_kills_the_cluster_even_when_a_member_is_still_alive() -> None:
    """🚨 `nz(na)=0` — 원본은 **한쪽만 죽어도** 병합 존을 죽인다(살아 있는 쪽이 못 살린다)."""
    alive = _bull(105.0, 100.0, confirmed=0)  # distal이자 **살아 있음**
    dead = _bull(107.0, 105.0, confirmed=0, break_time=15)
    members = [(0, alive), (1, dead)]

    assert _make_merged_group(OrderBlockDirection.BULLISH, members).break_time is None
    pine_group = _make_merged_group(
        OrderBlockDirection.BULLISH, members, break_time_rule="pine_max"
    )
    assert pine_group.break_time == 15


def test_pine_max_is_na_only_when_every_member_is_alive() -> None:
    assert _pine_max_break_time([_bull(105.0, 100.0, confirmed=0)]) is None
    assert (
        _pine_max_break_time([_bull(105.0, 100.0, confirmed=0), _bull(107.0, 105.0, confirmed=0)])
        is None
    )
    assert (
        _pine_max_break_time(
            [
                _bull(105.0, 100.0, confirmed=0, break_time=7),
                _bull(107.0, 105.0, confirmed=0),
                _bull(109.0, 107.0, confirmed=0, break_time=3),
            ]
        )
        == 7
    )


def test_census_reimplementation_agrees_with_the_engine() -> None:
    """인구조사는 팔을 안 돌리고도 B를 세려고 같은 식을 다시 쓴다 — 두 구현이 같아야 한다."""
    cases = [
        [_bull(105.0, 100.0, confirmed=0)],
        [_bull(105.0, 100.0, confirmed=0, break_time=10), _bull(107.0, 105.0, confirmed=0)],
        [
            _bull(105.0, 100.0, confirmed=0, break_time=10),
            _bull(107.0, 105.0, confirmed=0, break_time=15),
        ],
    ]
    for members in cases:
        assert pine_max_break_time(members) == _pine_max_break_time(members)
        indexed = list(enumerate(members))
        assert (
            distal_break_time(members)
            == _make_merged_group(OrderBlockDirection.BULLISH, indexed).break_time
        )


# --------------------------------------------------------------------------- #
# C — 탭 상태 키 (구성 집합 vs 존 단위)
# --------------------------------------------------------------------------- #

# A(위)가 t5에 소멸해 클러스터가 {A,B} → {B}로 줄어든다. 가격은 t2부터 **한 번도 밖으로
# 나가지 않는다** — 그런데도 현행 `cluster`는 키가 바뀌어 t5를 새 탭으로 센다.
_C_ARCHIVE = [
    _bull(105.0, 100.0, confirmed=1, break_time=3, swept_time=5),  # A
    _bull(103.0, 98.0, confirmed=1),  # B (distal · 살아 있음)
]
_C_TIMES = [0, 1, 2, 3, 4, 5]
_C_HIGHS = [125.0, 115.0, 104.0, 104.0, 104.0, 104.0]
_C_LOWS = [120.0, 110.0, 99.0, 99.0, 99.0, 99.0]
_C_CLOSES = [122.0, 112.0, 101.0, 101.0, 101.0, 101.0]


def _c_signals(tap_state: str) -> list[OrderBlockSignal]:
    return _generate_merged_signals(
        _C_ARCHIVE,
        _C_TIMES,
        _C_HIGHS,
        _C_LOWS,
        _C_CLOSES,
        include_retaps=True,
        tap_state=tap_state,
    )


def test_cluster_key_counts_a_spurious_retap_when_composition_changes() -> None:
    """현행 기본값의 결함을 **재현**한다 — 가격이 계속 안에 있었는데 재탭이 하나 더 난다."""
    signals = _c_signals("cluster")
    assert [sig.trigger_time for sig in signals] == [2, 5]
    assert [sig.tap_index for sig in signals] == [0, 1]  # t5가 가짜 재탭


def test_zone_key_drops_the_spurious_retap() -> None:
    """옵트인 `zone`은 구성이 줄기만 한 봉에서 탭을 내지 않는다."""
    signals = _c_signals("zone")
    assert [sig.trigger_time for sig in signals] == [2]


# ★ 사용자 결정(2026-09-01)이 가르는 두 상황. 하나만 고정하면 나머지가 조용히 무너진다.
#
#   가격이 계속 박스 안에 있는데 구성이 바뀜  → ❌ 탭 안 셈  ("존만 넓어지는 것")
#   가격이 밖으로 나갔다가 다시 들어옴        → ✅ 탭 세고 미진입 구성 존에 기회 (WAN-82)

_JOIN_ARCHIVE = [
    _bull(105.0, 100.0, confirmed=1),  # A — t2에 탭
    _bull(103.0, 98.0, confirmed=3),  # B — t3에 확정되며 클러스터에 편입
]
_JOIN_TIMES = [0, 1, 2, 3, 4, 5, 6]


def _join_bars(reentry: bool) -> tuple[list[float], list[float], list[float]]:
    """`reentry=False`면 t2부터 계속 안에 있고, `True`면 t5에 나갔다 t6에 다시 들어온다."""
    #          t0     t1     t2    t3    t4    t5                t6
    inside = [False, False, True, True, True, not reentry, True]
    lows = [101.0 if flag else 120.0 for flag in inside]
    highs = [104.0 if flag else 125.0 for flag in inside]
    closes = [(lo + hi) / 2 for lo, hi in zip(lows, highs, strict=True)]
    return highs, lows, closes


def test_a_zone_joining_while_price_stays_inside_does_not_count_a_tap() -> None:
    """★ 사용자 결정: *"단순히 존만 넓어지는거야"* — 가격이 안 움직였으면 기회가 아니다.

    현행 `cluster`와 초안 `zone_all`은 **여기서 탭을 셉니다**(합류한 존이 「안에 있었다」
    기록이 없어서). 사용자 결정은 반대이고, `zone`이 그 결정입니다.
    """
    highs, lows, closes = _join_bars(reentry=False)

    def taps(tap_state: str) -> list[tuple[int, int | None]]:
        signals = _generate_merged_signals(
            _JOIN_ARCHIVE,
            _JOIN_TIMES,
            highs,
            lows,
            closes,
            include_retaps=True,
            tap_state=tap_state,
        )
        return [(sig.trigger_time, sig.tap_index) for sig in signals]

    # A 혼자의 첫 탭(t2) 하나뿐 — B 합류(t3)는 새 탭이 아니다.
    assert taps("zone") == [(2, 0)]
    # 대체된 두 규칙은 t4를 새 탭으로 센다(= 이 결정이 없애는 그 탭).
    assert taps("zone_all") == [(2, 0), (4, 0)]
    assert taps("cluster") == [(2, 0), (4, 0)]


def test_a_newly_joined_zone_still_gets_its_chance_when_price_comes_back() -> None:
    """🚨 WAN-82를 되돌리는 것이 아니다 — 가격이 **나갔다 다시 들어오면** 기회가 있다.

    WAN-82의 원래 문제는 새로 편입된 존이 **영구적으로** 기회를 잃는 것이었다. 이번
    결정은 「가격이 안 움직인 동안에는 기회를 만들지 않는다」까지이고, 이 테스트가
    그 경계를 지킨다(이게 없으면 `any` 전환이 WAN-82를 조용히 되돌린다).
    """
    highs, lows, closes = _join_bars(reentry=True)
    signals = _generate_merged_signals(
        _JOIN_ARCHIVE,
        _JOIN_TIMES,
        highs,
        lows,
        closes,
        include_retaps=True,
        tap_state="zone",
    )
    # t2: A의 첫 탭 · t5에 밖으로 · t6에 다시 들어오며 **B가 자기 몫의 첫 탭**(tap_index=0).
    assert [(sig.trigger_time, sig.tap_index) for sig in signals] == [(2, 0), (6, 0)]
    assert signals[-1].zone_key == frozenset({0, 1})


def test_the_new_rule_never_counts_more_taps_than_the_draft() -> None:
    """단조성 — `any`는 `all`보다 항상 덜 센다. 인구조사가 이 성질에 기대고 있다."""
    for reentry in (False, True):
        highs, lows, closes = _join_bars(reentry=reentry)
        counts = {
            state: len(
                _generate_merged_signals(
                    _JOIN_ARCHIVE,
                    _JOIN_TIMES,
                    highs,
                    lows,
                    closes,
                    include_retaps=True,
                    tap_state=state,
                )
            )
            for state in ("zone", "zone_all")
        }
        assert counts["zone"] <= counts["zone_all"], (reentry, counts)


# --------------------------------------------------------------------------- #
# 기본값에서는 아무것도 안 바뀐다
# --------------------------------------------------------------------------- #


def test_defaults_reproduce_the_previous_merged_engine() -> None:
    """노브를 명시하지 않은 병합 실행 ≡ 채택값을 명시한 실행(비트 단위)."""
    archive = _C_ARCHIVE
    args = (archive, _C_TIMES, _C_HIGHS, _C_LOWS, _C_CLOSES)
    for include_retaps in (False, True):
        implicit = _generate_merged_signals(*args, include_retaps=include_retaps)
        explicit = _generate_merged_signals(
            *args,
            include_retaps=include_retaps,
            break_time_rule="distal",
            tap_state="cluster",
        )
        assert implicit == explicit


def test_merge_knobs_are_rejected_on_the_split_path() -> None:
    """분리 경로(`combine_obs=False`)에 병합 노브를 주면 **거부**한다 — 라벨만 붙는 실행 방지.

    채택 기본값은 분리라(WAN-149) 이 가드가 없으면 「pine 규칙으로 쟀다」고 믿으면서
    현행 분리 엔진을 돌리게 된다(WAN-91/95/112/123/159 부류).
    """
    OrderBlockParams(combine_obs=False)  # 기본값끼리는 당연히 통과한다.
    OrderBlockParams(combine_obs=True, merged_break_time_rule="pine_max")
    OrderBlockParams(combine_obs=True, merged_tap_state="zone")
    for kwargs in (
        {"merged_break_time_rule": "pine_max"},
        {"merged_tap_state": "zone"},
    ):
        with pytest.raises(ValidationError):
            OrderBlockParams(combine_obs=False, **kwargs)


def test_split_path_signals_are_untouched_by_this_issue() -> None:
    """채택 경로(`combine_obs=False`)는 이 이슈가 손대지 않았다 — 합성 데이터로 동작 고정.

    완료기준 3: 병합 경로만 건드린다. 분리 경로가 새 노브를 읽지도 않는다는 것은 위
    가드가 이미 증명하지만, 여기서는 **탐지 결과 자체**가 그대로임을 본다.
    """
    df = make_synthetic_ohlcv(bars=600, seed=400)
    split = OrderBlockDetector(OrderBlockParams(combine_obs=False)).run(df)
    assert split.signals or split.order_blocks  # 표본이 비면 이 테스트가 아무것도 안 지킨다.
    again = OrderBlockDetector(OrderBlockParams()).run(df)
    assert split.signals == again.signals
    assert split.retap_signals == again.retap_signals
    assert split.order_blocks == again.order_blocks


def test_first_taps_can_be_derived_from_all_taps() -> None:
    """인구조사가 팔마다 replay를 **한 번만** 돌 수 있는 근거를 동작으로 고정한다.

    `include_retaps=False` 목록 ≡ `include_retaps=True` 목록의 `tap_index == 0` 부분.
    두 경로가 `entered`·포함 상태 갱신을 똑같이 하고 **재탭 분기만** 다르기 때문인데,
    그 성질이 깨지면 인구조사의 「첫 탭」 열이 조용히 틀린 수가 된다.
    """
    args = (_C_ARCHIVE, _C_TIMES, _C_HIGHS, _C_LOWS, _C_CLOSES)
    for kwargs in (
        {},
        {"break_time_rule": "pine_max"},
        {"tap_state": "zone"},
    ):
        first = _generate_merged_signals(*args, **kwargs)  # type: ignore[arg-type]
        all_taps = _generate_merged_signals(*args, include_retaps=True, **kwargs)  # type: ignore[arg-type]
        assert first == _first_taps(all_taps), kwargs
        # 이 픽스처가 실제로 재탭을 내야 이 테스트가 무언가를 지킨다.
        if not kwargs:
            assert len(all_taps) > len(first)


def test_zone_key_keeps_counting_retaps_across_a_composition_change() -> None:
    """재탭 카운터가 구성 변경으로 **1로 되돌아가지 않는다**.

    가격이 실제로 나갔다 들어왔으므로 두 모드 모두 탭을 내지만, 현행 `cluster`는 키가
    바뀌어 「이 클러스터의 첫 재탭」이라고 세고(`tap_index=1`) `zone`은 구성 존이 들고
    있던 횟수에서 이어 센다. `tap_index`는 재탭 정책이 소비하는 값이라(WAN-81/123)
    이 되돌아감은 라벨이 아니라 **동작**이다.
    """
    archive = [
        _bull(105.0, 100.0, confirmed=1, break_time=3, swept_time=7),  # A
        _bull(103.0, 98.0, confirmed=1),  # B (distal · 살아 있음)
    ]
    times = list(range(10))
    inside = [99.0, 104.0]
    outside = [120.0, 125.0]
    #        t0       t1       t2      t3       t4      t5       t6      t7       t8      t9
    pattern = [False, False, True, False, True, False, True, False, True, True]
    lows = [inside[0] if flag else outside[0] for flag in pattern]
    highs = [inside[1] if flag else outside[1] for flag in pattern]
    closes = [(lo + hi) / 2 for lo, hi in zip(lows, highs, strict=True)]

    by_mode = {
        mode: _generate_merged_signals(
            archive, times, highs, lows, closes, include_retaps=True, tap_state=mode
        )
        for mode in ("cluster", "zone")
    }
    # 두 모드가 **같은 봉들에** 탭을 낸다 — 갈리는 것은 재탭 번호뿐이다.
    for mode, signals in by_mode.items():
        assert [sig.trigger_time for sig in signals] == [2, 4, 6, 8], mode
    assert [sig.tap_index for sig in by_mode["cluster"]] == [0, 1, 2, 1]  # ← t8이 1로 리셋
    assert [sig.tap_index for sig in by_mode["zone"]] == [0, 1, 2, 3]
