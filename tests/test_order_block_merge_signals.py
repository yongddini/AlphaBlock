"""WAN-56: 존 병합(combine_obs)이 백테스트 시그널까지 적용되는지에 대한 테스트.

버그: 병합은 렌더링 뷰에만 적용되고 시그널은 병합 전 원본 존으로 생성돼, 트레이딩뷰
화면과 백테스트가 서로 다른 존 집합을 봤다. 이 스위트는 수정 후 불변식을 고정한다.

1. **병합 단위당 1회 진입(R1)**: 겹치는 존 여러 개가 병합되면 진입은 1회다(원본 경로는
   존마다 세어 진입이 부풀려진다 — 그 차이를 함께 검증해 얼마나 부풀려졌는지 못박는다).
2. **손절 거리**: 병합 존의 손절선(무효화 경계)은 구성 존의 **합집합** 원단(distal)이다.
3. **단일 존 패리티**: 겹치지 않는 존은 `combine_obs=False`와 완전히 동일한 시그널을 낸다.
4. **look-ahead 부재**: 병합이 실제로 일어나는 규모에서도 데이터를 T에서 잘라 실행한
   T 이전 신호가 전체 실행과 동일하다(전 구간 일괄 병합이면 깨진다).
"""

from __future__ import annotations

from backtest.synthetic import make_synthetic_ohlcv
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import (
    OrderBlockDetector,
    _generate_merged_signals,
    _generate_signals,
)


def _bull(
    top: float, bottom: float, *, confirmed: int, break_time: int | None = None
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
    )


# 6봉 타임라인(ms=인덱스). 확정 이전(0..2)엔 존 밖, t4에서 합집합 존에 탭.
_TIMES = [0, 1, 2, 3, 4, 5]
#           t0    t1    t2    t3    t4(tap)  t5
_HIGHS = [120.0, 118.0, 116.0, 112.0, 101.0, 108.0]
_LOWS = [115.0, 113.0, 111.0, 110.0, 99.0, 106.0]
_CLOSES = [117.0, 115.0, 113.0, 111.0, 100.0, 107.0]


def test_overlapping_zones_merge_to_single_entry_r1() -> None:
    """겹치는 두 존이 병합돼 진입이 **1회**다(원본 경로는 2회 — 부풀려짐)."""
    a = _bull(105.0, 100.0, confirmed=2)  # 가격 [100,105]
    b = _bull(103.0, 98.0, confirmed=2)  # 가격 [98,103] — a와 [100,103] 겹침 → 병합
    archive = [a, b]

    merged = _generate_merged_signals(archive, _TIMES, _HIGHS, _LOWS, _CLOSES)
    raw = _generate_signals(archive, _TIMES, _HIGHS, _LOWS, _CLOSES)

    # 병합: 진입 1회. 원본: 존마다 1회씩 2회(진입 부풀림).
    assert len(merged) == 1
    assert len(raw) == 2

    signal = merged[0]
    assert signal.trigger_time == 4
    assert signal.status == "active"
    ob = signal.order_block
    assert ob.combined is True
    # 합집합 경계: top=max(105,103)=105, bottom=min(100,98)=98.
    assert ob.top == 105.0
    assert ob.bottom == 98.0


def test_merged_stop_is_union_distal_boundary() -> None:
    """병합 존의 손절(무효화)은 **가장 바깥 구성 존**의 무효화와 같다(손절 거리 확장).

    강세 병합 존의 distal은 최저 bottom을 가진 구성 존이다. 그 존이 깨지는 시각이
    병합 존의 `break_time`이 된다.
    """
    # a는 t5에 무효화되지만 distal(더 낮은 bottom=98)은 b. b는 무효화되지 않음(None).
    a = _bull(105.0, 100.0, confirmed=2, break_time=5)
    b = _bull(103.0, 98.0, confirmed=2)
    signal = _generate_merged_signals([a, b], _TIMES, _HIGHS, _LOWS, _CLOSES)[0]
    # distal = b(bottom 98) → 병합 존은 아직 무효화되지 않았다(합집합 하단 미이탈).
    assert signal.order_block.bottom == 98.0
    assert signal.order_block.break_time is None
    assert signal.status == "active"


def test_non_overlapping_zones_match_raw_path() -> None:
    """겹치지 않는 존은 병합 경로와 원본 경로가 **동일한** 시그널을 낸다."""
    a = _bull(105.0, 100.0, confirmed=2)
    b = _bull(90.0, 85.0, confirmed=2)  # 가격 [85,90] — a와 안 겹침(단일 유지)
    archive = [a, b]

    def project(signals: object) -> list[tuple[str, int, float, str, float, float]]:
        return sorted(
            (
                s.direction.value,
                s.trigger_time,
                s.price,
                s.status,
                s.order_block.top,
                s.order_block.bottom,
            )
            for s in signals  # type: ignore[attr-defined]
        )

    merged = _generate_merged_signals(archive, _TIMES, _HIGHS, _LOWS, _CLOSES)
    raw = _generate_signals(archive, _TIMES, _HIGHS, _LOWS, _CLOSES)
    assert project(merged) == project(raw)
    assert all(not s.order_block.combined for s in merged)


def _entry_projection(signals: object) -> list[tuple[str, int, float, str]]:
    return sorted(
        (s.direction.value, s.trigger_time, s.price, s.status)
        for s in signals  # type: ignore[attr-defined]
    )


def test_merged_signals_are_look_ahead_free_at_scale() -> None:
    """병합이 실제로 일어나는 규모에서 병합 시그널이 look-ahead가 없다.

    데이터를 T에서 잘라 재실행한 T 이전 신호가 전체 실행의 T 이전 신호와 동일하다.
    전 구간 일괄 병합(미래에 생길 존과 미리 합침)이면 이 등식이 깨진다.
    """
    params = OrderBlockParams(combine_obs=True)  # 기본 swing_length=10.
    raw_params = OrderBlockParams(combine_obs=False)
    df = make_synthetic_ohlcv(timeframe="1h", bars=3000, seed=7)
    full = OrderBlockDetector(params).run(df)
    raw = OrderBlockDetector(raw_params).run(df)
    times = [int(t) for t in df["open_time"].astype("int64")]

    # WAN-81 §5: 병합이 여러 존의 동시 커버(같은 봉에서 여러 존이 함께 탭)를
    # 하나로 접으므로 여전히 대체로 원본보다 적지만, §5 버그 수정(개별 존 단위
    # entered 추적)으로 예전엔 억눌리던 신규 편입 존의 첫 탭이 되살아나 원본보다
    # 소폭 많아질 수도 있다(이 시드에서 34 vs 32) — "항상 더 적다"는 더 이상
    # 불변식이 아니다. 두 경로 모두 신호가 실제로 나오는지만 확인한다.
    assert full.signals
    assert raw.signals

    full_proj = _entry_projection(full.signals)
    for cut in (1000, 2000, 2800):
        t_cut = times[cut]
        truncated = OrderBlockDetector(params).run(df.iloc[:cut])
        before_full = [s for s in full_proj if s[1] < t_cut]
        before_trunc = [s for s in _entry_projection(truncated.signals) if s[1] < t_cut]
        assert before_full == before_trunc


# --------------------------------------------------------------- WAN-81 §5 (구 WAN-82 버그 흡수)


# 10봉 타임라인. A는 t2 확정, t4에서 탭(첫 진입). B는 t6 확정(A와 겹침 → 병합 클러스터
# 성장). t8에서 가격이 B 고유 구간([98,100))에만 닿는다 — 병합 top/bottom(105/98) 안이지만
# A의 구간([100,105])과는 겹치지 않는다.
_S5_TIMES = list(range(10))
#              t0     t1     t2     t3     t4      t5     t6     t7      t8     t9
_S5_HIGHS = [120.0, 120.0, 120.0, 120.0, 102.0, 120.0, 120.0, 120.0, 99.0, 99.0]
_S5_LOWS = [115.0, 115.0, 115.0, 115.0, 99.0, 115.0, 115.0, 115.0, 97.0, 97.0]
_S5_CLOSES = [117.0] * 10


def test_new_zone_joining_entered_cluster_still_gets_its_own_first_tap() -> None:
    """§5: 이미 진입한 병합 클러스터에 새로 편입된 존은 자신의 첫 탭에서 진입해야 한다.

    구 버그: `entered`를 병합 단위 전체로만 검사해, A 진입 이후 A와 겹치며 새로
    확정된 B가 (한 번도 탭된 적 없음에도) 영구적으로 진입 기회를 잃었다(WAN-82).
    수정 후: B가 자신의 구간에서 처음 탭되는 t8에서 `tap_index=0`으로 진입한다.
    """
    a = _bull(105.0, 100.0, confirmed=2)  # A: [100,105], t4에서 첫 탭.
    b = _bull(103.0, 98.0, confirmed=6)  # B: [98,103], A와 겹쳐 t6에 병합 클러스터로 편입.
    archive = [a, b]

    signals = _generate_merged_signals(archive, _S5_TIMES, _S5_HIGHS, _S5_LOWS, _S5_CLOSES)

    assert [(s.trigger_time, s.tap_index) for s in signals] == [(4, 0), (8, 0)]
    # t4엔 B가 아직 확정 전이라 A 단독 경계, t8엔 병합 경계(top=105, bottom=98)를
    # 쓴다(look-ahead 없음 — 그 시점에 존재하는 존만으로 병합한다, WAN-56 유지).
    by_time = {s.trigger_time: s for s in signals}
    assert (by_time[4].order_block.top, by_time[4].order_block.bottom) == (105.0, 100.0)
    assert (by_time[8].order_block.top, by_time[8].order_block.bottom) == (105.0, 98.0)


def test_new_zone_joining_entered_cluster_does_not_infinitely_reenter() -> None:
    """§5 수정이 같은 존 조합에서 무한 재진입을 만들지 않는다.

    t8 탭 이후 t9에도 가격이 그대로 B 구간에 머물지만(바깥→안 전이 없음), 추가
    시그널이 나오지 않는다.
    """
    a = _bull(105.0, 100.0, confirmed=2)
    b = _bull(103.0, 98.0, confirmed=6)
    signals = _generate_merged_signals([a, b], _S5_TIMES, _S5_HIGHS, _S5_LOWS, _S5_CLOSES)
    assert [s.trigger_time for s in signals] == [4, 8]  # t9엔 추가 시그널 없음.


def test_retap_path_uses_merged_zone_boundary_for_new_member_tap() -> None:
    """WAN-81 갭B: `include_retaps=True` 재탭 경로도 병합 존 경계를 그대로 쓴다.

    §5와 같은 시나리오에서 재탭 포함 생성기를 호출해도(every_tap 경로), B의 첫
    탭(t8)이 병합 경계(top=105, bottom=98)로 나온다 — 재탭 경로가 원본 존 단위로
    되돌아가지 않는다.
    """
    a = _bull(105.0, 100.0, confirmed=2)
    b = _bull(103.0, 98.0, confirmed=6)
    signals = _generate_merged_signals(
        [a, b], _S5_TIMES, _S5_HIGHS, _S5_LOWS, _S5_CLOSES, include_retaps=True
    )
    by_time = {s.trigger_time: s for s in signals}
    assert by_time[8].order_block.top == 105.0
    assert by_time[8].order_block.bottom == 98.0
    assert by_time[8].tap_index == 0


def test_zone_key_shared_across_taps_of_same_non_merged_zone() -> None:
    """WAN-83: 비병합 경로에서 `zone_key`는 같은 존의 모든 탭에서 동일하고, 다른
    존과는 다르다 — 아카이브 인덱스가 안정적 그룹핑 식별자로 쓰인다."""
    a = _bull(105.0, 100.0, confirmed=2)
    a = a.model_copy(update={"tapped_times": (4, 6)})
    b = _bull(85.0, 80.0, confirmed=2)
    b = b.model_copy(update={"tapped_times": (5,)})
    archive = [a, b]
    times = [0, 1, 2, 3, 4, 5, 6, 7]
    highs = [200.0] * len(times)
    lows = [0.0] * len(times)
    closes = [150.0] * len(times)

    signals = _generate_signals(archive, times, highs, lows, closes, include_retaps=True)
    by_time = {s.trigger_time: s for s in signals}

    assert by_time[4].zone_key == frozenset({0})
    assert by_time[6].zone_key == frozenset({0})
    assert by_time[4].zone_key == by_time[6].zone_key
    assert [by_time[4].tap_index, by_time[6].tap_index] == [0, 1]
    assert by_time[5].zone_key == frozenset({1})
    assert by_time[5].zone_key != by_time[4].zone_key


def test_zone_key_reflects_merged_membership() -> None:
    """WAN-83: 병합 경로의 `zone_key`는 그 순간의 구성 존 집합(아카이브 인덱스)이다.

    §5 시나리오(A 단독 첫 탭 t4 → B가 편입돼 병합 클러스터 첫 탭 t8)에서, t4는 A만의
    키(`{0}`)를, t8은 병합 멤버 전체의 키(`{0,1}`)를 가져야 한다."""
    a = _bull(105.0, 100.0, confirmed=2)  # 아카이브 인덱스 0
    b = _bull(103.0, 98.0, confirmed=6)  # 아카이브 인덱스 1
    signals = _generate_merged_signals([a, b], _S5_TIMES, _S5_HIGHS, _S5_LOWS, _S5_CLOSES)
    by_time = {s.trigger_time: s for s in signals}

    assert by_time[4].zone_key == frozenset({0})
    assert by_time[8].zone_key == frozenset({0, 1})
    assert by_time[4].zone_key != by_time[8].zone_key


def test_retap_signals_are_look_ahead_free_at_scale() -> None:
    """`retap_signals`(모든 탭 재생, WAN-81)도 look-ahead가 없다.

    `signals`(첫 탭만)에 이미 있는 불변식(`test_merged_signals_are_look_ahead_free_at_scale`)을
    재탭 포함 뷰에도 동일하게 고정한다 — 이 뷰는 `retap_mode="every_tap"`이 소비하므로
    (WAN-81 갭B) 별도로 검증이 필요하다.
    """
    params = OrderBlockParams(combine_obs=True)
    df = make_synthetic_ohlcv(timeframe="1h", bars=3000, seed=7)
    full = OrderBlockDetector(params).run(df)
    times = [int(t) for t in df["open_time"].astype("int64")]

    assert full.retap_signals
    assert len(full.retap_signals) >= len(full.signals)

    full_proj = _entry_projection(full.retap_signals)
    for cut in (1000, 2000, 2800):
        t_cut = times[cut]
        truncated = OrderBlockDetector(params).run(df.iloc[:cut])
        before_full = [s for s in full_proj if s[1] < t_cut]
        before_trunc = [s for s in _entry_projection(truncated.retap_signals) if s[1] < t_cut]
        assert before_full == before_trunc
