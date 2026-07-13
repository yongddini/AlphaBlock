"""WAN-49: 오더블록 탐지/시그널 성능 최적화의 정확성 불변 + 벤치마크 회귀 테스트.

WAN-47(생존자 편향 제거)의 정확성을 유지한 채 복잡도를 O(봉수 × 존수)에서
되돌린다. 이 테스트는 두 가지를 지킨다:

1. **정확성 불변(최우선)**: 이진 탐색으로 바뀐 `_generate_signals`가 구버전
   선형 스캔 참조 구현과 **완전히 동일한** 시그널을 낸다. 활성/아카이브 분리로
   바뀐 `_invalidate`의 아카이브(존 상태·탭·소멸)는 기존 WAN-47 스위트가 지킨다.
2. **성능 회귀 방지**: 3년치 1시간봉(26,280봉) 탐지+백테스트가 목표 시간 내에
   끝난다. O(n²) 퇴화가 재발하면 이 테스트가 크게 초과하며 잡아낸다.
"""

from __future__ import annotations

import time

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.synthetic import make_synthetic_ohlcv
from strategy.models import OrderBlock, OrderBlockParams, OrderBlockSignal
from strategy.order_blocks import OrderBlockDetector

# 3년치 1시간봉 = 24 * 365 * 3 = 26,280봉 (이슈 성능 목표 기준).
_THREE_YEARS_1H_BARS = 26_280

# 탐지+백테스트 상한(초). 이슈 목표는 2초지만 CI 머신 편차를 감안해 여유를 둔다.
# 최적화 전 O(n²)이면 수십 초가 걸리므로, 이 넉넉한 상한으로도 퇴화는 확실히 잡힌다.
_MAX_SECONDS = 8.0


def _reference_signals(
    order_blocks: list[OrderBlock],
    times: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[tuple[str, int, float, str]]:
    """WAN-49 이전 선형 스캔 알고리즘의 독립 참조 구현.

    이진 탐색으로 대체된 구간 경계(start_pos·break_pos)를 0부터의 선형 루프로
    다시 계산해, 최적화된 `_generate_signals`와 시그널이 1:1로 같은지 검증한다.
    """
    signals: list[tuple[str, int, float, str]] = []
    n = len(times)
    for ob in order_blocks:
        start_pos = 0
        while start_pos < n and times[start_pos] <= ob.confirmed_time:
            start_pos += 1
        break_pos: int | None = None
        if ob.break_time is not None:
            pos = start_pos
            while pos < n and times[pos] < ob.break_time:
                pos += 1
            break_pos = pos
        end_pos = n if break_pos is None else min(n, break_pos + 1)
        for i in range(start_pos, end_pos):
            if lows[i] <= ob.top and highs[i] >= ob.bottom:
                is_break_bar = break_pos is not None and i >= break_pos
                signals.append(
                    (
                        ob.direction.value,
                        times[i],
                        closes[i],
                        "cancelled" if is_break_bar else "active",
                    )
                )
                break
    return signals


def _project(signals: list[OrderBlockSignal]) -> list[tuple[str, int, float, str]]:
    return [(s.direction.value, s.trigger_time, s.price, s.status) for s in signals]


def test_bisect_signals_match_linear_reference() -> None:
    """이진 탐색 시그널이 구버전 선형 스캔 참조와 완전히 동일하다.

    이 테스트는 원본 단위(`_generate_signals`) 경로의 이진 탐색 최적화를 검증하므로
    `combine_obs=False`로 고정한다(WAN-56: 기본값 `True`는 병합 존 경로를 탄다).
    """
    params = OrderBlockParams(combine_obs=False)
    df = make_synthetic_ohlcv(timeframe="1h", bars=1500, seed=11)
    result = OrderBlockDetector(params).run(df)

    frame = df[df["closed"].astype(bool)] if "closed" in df.columns else df
    frame = frame.sort_values("open_time").reset_index(drop=True)
    times = [int(t) for t in frame["open_time"].astype("int64")]
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]

    expected = _reference_signals(result.order_blocks, times, highs, lows, closes)
    assert _project(result.signals) == expected
    # 최적화가 존을 지우지 않았는지(WAN-47 보존): 아카이브·시그널이 비어있지 않다.
    assert result.order_blocks
    assert result.signals


def test_three_year_detection_and_backtest_within_budget() -> None:
    """26,280봉 단일 탐지+백테스트가 시간 예산 내에 끝난다(O(n²) 회귀 방지)."""
    df = make_synthetic_ohlcv(timeframe="1h", bars=_THREE_YEARS_1H_BARS, seed=3)

    start = time.perf_counter()
    detection = OrderBlockDetector(OrderBlockParams()).run(df)
    BacktestEngine(BacktestConfig()).run(df, detection.signals)
    elapsed = time.perf_counter() - start

    # 수백 개 존이 생성되는 규모여야 벤치마크가 유의미하다(순회 대상이 실제로 누적됨).
    assert len(detection.order_blocks) > 100
    assert elapsed < _MAX_SECONDS, (
        f"3년치 탐지+백테스트가 {elapsed:.2f}s 걸림 (상한 {_MAX_SECONDS}s). "
        "O(봉수 × 존수) 퇴화가 재발했을 수 있음."
    )
