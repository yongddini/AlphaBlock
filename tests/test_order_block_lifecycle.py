"""오더블록 생애주기 아카이브 & look-ahead 회귀 테스트 (WAN-47).

`OrderBlockDetector`가 존을 삭제하지 않고 전체 생애주기(생성·탭·무효화·소멸)를
아카이브로 보존하는지, 렌더링 뷰(`active_at`/`rendered_order_blocks`)가 트레이딩뷰
패리티를 유지하는지, 그리고 신호가 look-ahead 없이 산출되는지(미래 데이터로 잘라도
과거 신호 불변)를 검증한다. 이 버그(생존자 편향)가 재발하면 백테스트 수치가
무의미해지므로 회귀 방지가 핵심이다.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from backtest.synthetic import make_synthetic_ohlcv
from strategy.models import OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import OrderBlockDetector, detect_order_blocks

# strategy/test_order_blocks 와 동일한 강세 시나리오: 존은 t11 생성, t12에 무효화(breaker).
_BULL_BARS = [
    (100, 102, 90, 95, 10),
    (95, 100, 93, 98, 10),
    (98, 101, 94, 99, 10),
    (99, 103, 95, 101, 10),
    (101, 110, 100, 108, 10),
    (108, 109, 104, 106, 15),
    (106, 107, 103, 105, 20),
    (105, 106, 102, 104, 25),
    (104, 105, 100, 102, 10),
    (102, 104, 99, 101, 10),
    (101, 103, 98, 100, 10),
    (100, 115, 99, 112, 30),
    (112, 113, 95, 97, 10),
]


def _make_df(bars: Sequence[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * 60_000 for i in range(len(bars))],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [b[4] for b in bars],
        }
    )


def _bull_params(**overrides: object) -> OrderBlockParams:
    base = {
        "swing_length": 3,
        "atr_length": 3,
        "max_atr_mult": 100.0,
        "combine_obs": False,
        "zone_count": "high",
    }
    base.update(overrides)
    return OrderBlockParams.model_validate(base)


def test_broken_then_swept_zone_retains_full_lifecycle() -> None:
    """깨진 뒤 되쓸린 존이 아카이브에 break_time·swept_time과 함께 남는다.

    원본은 이 지점에서 박스를 삭제(`box.delete()`)해 기록이 소실되지만, WAN-47
    아카이브는 존을 보존한다. 무효화(t12)와 소멸(t13) 사이에는 breaker지만 여전히
    '생존'(차트에 breaker 색으로 그려짐)이고, 소멸 이후엔 렌더에서 사라진다.
    """
    bars = [*_BULL_BARS, (97, 105, 96, 100, 10)]  # t13: high=105 > top(103) → sweep
    result = detect_order_blocks(_make_df(bars), _bull_params())

    assert len(result.order_blocks) == 1
    ob = result.order_blocks[0]
    assert ob.direction is OrderBlockDirection.BULLISH
    assert ob.break_time == 12 * 60_000
    assert ob.swept_time == 13 * 60_000

    # 무효화 봉: breaker지만 아직 소멸 전 → 생존(렌더 포함). 렌더 뷰는 그 시점
    # 상태로 클리핑되므로 swept_time은 아직 None이다.
    assert ob.alive_at(12 * 60_000) is True
    view = result.active_at(12 * 60_000, limit=3)
    assert len(view) == 1
    assert view[0].top == ob.top and view[0].bottom == ob.bottom
    assert view[0].breaker is True
    assert view[0].swept_time is None
    # 소멸 봉 이후: 렌더에서 제외(트레이딩뷰가 지운 것과 동일).
    assert ob.alive_at(13 * 60_000) is False
    assert result.active_at(13 * 60_000, limit=3) == []


def test_swept_zone_records_a_tap_before_sweep() -> None:
    """소멸 전 존에 가격이 재진입하면 tapped_times에 기록된다."""
    bars = [*_BULL_BARS, (97, 105, 96, 100, 10)]
    result = detect_order_blocks(_make_df(bars), _bull_params())
    ob = result.order_blocks[0]
    # t12(무효화 봉, low=95~high=113)과 t13(96~105)이 존[98,103]에 겹친다 → 최소 1회 탭.
    assert ob.tapped_times
    assert all(t > ob.confirmed_time for t in ob.tapped_times)


def test_active_at_reproduces_from_scratch_render_at_each_time() -> None:
    """임의 시점 `active_at`가 그 시점까지의 데이터로 새로 탐지한 렌더 뷰와 일치한다.

    즉 아카이브에서 파생한 "시각 t의 그림"이 트레이딩뷰가 t에 실제로 그렸을
    박스 집합과 같다(렌더링 패리티). 데이터가 400봉 < max_distance(1750)이라
    두 실행 모두 0번 봉부터 스캔하므로 동일 비교가 성립한다.
    """
    params = _bull_params(combine_obs=True, zone_count="low")
    df = make_synthetic_ohlcv(timeframe="1h", bars=400, seed=7)
    full = OrderBlockDetector(params).run(df)
    times = [int(t) for t in df["open_time"].astype("int64")]

    limit = params.zone_limit
    for cut in range(50, 400, 37):
        t = times[cut]
        from_scratch = OrderBlockDetector(params).run(df.iloc[: cut + 1])
        derived = full.active_at(t, limit=limit, combine=params.combine_obs)
        # 트레이딩뷰가 cut 시점에 그렸을 박스 == 아카이브에서 파생한 active_at.
        assert derived == from_scratch.rendered_order_blocks


def _entry_projection(result: object) -> list[tuple[str, int, float, str]]:
    signals = result.signals  # type: ignore[attr-defined]
    return sorted((s.direction.value, s.trigger_time, s.price, s.status) for s in signals)


def test_signals_are_look_ahead_free() -> None:
    """데이터를 T에서 잘라도 T 이전 진입 신호(방향·시각·가격·상태)가 불변이다.

    look-ahead가 있으면(미래 무효화 정보가 과거 신호를 바꾸면) 이 등식이 깨진다.
    신호에 실린 order_block의 미래 필드(break_time 등)는 진입 결정과 무관하므로
    진입 결정 투영만 비교한다.
    """
    params = _bull_params(combine_obs=True, zone_count="low")
    df = make_synthetic_ohlcv(timeframe="1h", bars=400, seed=13)
    full = OrderBlockDetector(params).run(df)
    times = [int(t) for t in df["open_time"].astype("int64")]

    for cut in (150, 250, 350):
        t_cut = times[cut]
        truncated = OrderBlockDetector(params).run(df.iloc[:cut])
        full_before = [s for s in _entry_projection(full) if s[1] < t_cut]
        trunc_before = [s for s in _entry_projection(truncated) if s[1] < t_cut]
        assert full_before == trunc_before


def test_archive_removes_survivorship_bias_entries_that_will_stop_out() -> None:
    """아카이브 신호에는 '나중에 무효화될 존'의 정상 진입이 포함된다.

    구버전은 살아남은 존만 반환해 손절 케이스를 구조적으로 배제(생존자 편향)했다.
    이제 무효화 전 탭이 active 신호로 잡혀(→ 백테스트에서 손절 거래로 이어짐)
    한 개 이상 존재해야 한다. 또한 아카이브(전체)는 렌더 뷰(최근 소수)보다 크다.
    """
    params = _bull_params(combine_obs=True, zone_count="low")
    df = make_synthetic_ohlcv(timeframe="1h", bars=600, seed=7)
    result = OrderBlockDetector(params).run(df)

    active_that_break = [
        s for s in result.signals if s.status == "active" and s.order_block.break_time is not None
    ]
    assert active_that_break, "무효화 전 탭(손절로 이어질 진입)이 최소 1개는 있어야 한다"
    # 전체 아카이브가 렌더 뷰(트레이딩뷰가 지금 그리는 소수 박스)보다 훨씬 크다.
    assert len(result.order_blocks) > len(result.rendered_order_blocks)
