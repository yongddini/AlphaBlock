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

    # 병합이 실제로 시그널을 줄였는지(테스트 유효성): 병합 진입 < 원본 진입.
    # (원본은 겹치는 존을 각각 세어 진입이 부풀려진다 — R1 병합이 그걸 제거한다.)
    assert len(full.signals) < len(raw.signals), "병합이 겹치는 진입을 실제로 줄여야 한다"

    full_proj = _entry_projection(full.signals)
    for cut in (1000, 2000, 2800):
        t_cut = times[cut]
        truncated = OrderBlockDetector(params).run(df.iloc[:cut])
        before_full = [s for s in full_proj if s[1] < t_cut]
        before_trunc = [s for s in _entry_projection(truncated.signals) if s[1] < t_cut]
        assert before_full == before_trunc
