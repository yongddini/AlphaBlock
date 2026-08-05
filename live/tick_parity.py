"""틱 체결 vs 1분봉 체결 파리티 측정 (WAN-246 §2).

이 저장소의 모든 체결 판정은 **1분봉 서브스텝**(`backtest.substep` · 라이브 러너의 확정
1분봉 소비)으로 낸다 — `book.on_price(low, high, close)`가 그 원자 연산이다. WAN-246이
얹는 **옵트인 틱 경로**(`live.price_feed`)는 같은 원자 연산을 **더 촘촘한 가격 열**로
호출한다. 이 모듈은 그 둘이 언제·얼마나 갈리는지를 재는 순수 프리미티브다.

## 왜 대부분의 경우 안 갈리나

체결 *여부*는 1분봉이 이미 그 분의 저가·고가를 담고 있어 봉 단위로 같다: 롱은
`low <= 지정가`, 그 `low`는 그 분의 최저가다. 그래서 **상수 지정가**(볼린저 없는 경로)는
틱을 아무리 촘촘히 넣어도 **결코 갈리지 않는다**(`test_tick_parity`가 이 불변을 고정).

## 어디서 갈리나 — `intrabar_live` 밴드 하나뿐

채택 경로(`band_bar="intrabar_live"`, WAN-132)는 볼린저 밴드의 20번째 표본이 **현재가**라
지정가가 봉 안에서 움직인다. 1분봉 경로는 지정가를 그 분의 **종가**로 한 번 잰다
(`limit(close)`). 틱 경로는 **터치 순간의 현재가**로 잰다(`limit(touch_price)`). 밴드가
표본에 따라 움직이므로 두 지정가가 미세하게 갈리고, 그 갈림이 경계 근처(저관통) 체결의
*여부*를 뒤집을 수 있다. 크기는 봉내 밴드 이동폭(PM 실측 중앙값 1.6~4.1bp)에 유계이고,
하필 그 저관통 체결이 WAN-96이 "스치듯 닿은 체결 = 큐 우선순위상 가장 안 될 체결"이라
지목한 바로 그 부류다.

## 결정 (§3, `docs/decisions/wan246.md`)

**판정 (b)**: 백테스트는 1분봉 유지(토대 불변), 라이브 틱 경로는 **옵트인으로 더 미세함을
명시**한다. 틱 데이터로 큐 우선순위를 모델링하는 것(WAN-98)은 Canceled라, 틱 *가격*만으로는
"닿으면 체결" 낙관이 남는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from live.limit_orders import PendingLimitOrder


@dataclass(frozen=True)
class FillDivergence:
    """한 (대기 주문, 1분봉)에서 1분봉 체결과 틱 체결이 갈렸는지."""

    bar_filled: bool
    tick_filled: bool
    bar_price: float | None
    tick_price: float | None

    @property
    def verdict_differs(self) -> bool:
        """체결 *여부*가 갈렸나(한 경로만 체결)."""
        return self.bar_filled != self.tick_filled

    @property
    def price_delta_bps(self) -> float | None:
        """두 경로 다 체결됐을 때 체결가 차이(bp). 한쪽이라도 미체결이면 None."""
        if self.bar_price is None or self.tick_price is None:
            return None
        if self.bar_price == 0.0:
            return None
        return abs(self.tick_price - self.bar_price) / self.bar_price * 10_000.0


def ohlc_tick_path(open_: float, low: float, high: float, close: float) -> list[float]:
    """1분봉을 표준 봉내 가격 열로 재구성한다(틱의 프록시 — 실제 틱은 WAN-98이 Canceled).

    초록봉(종가≥시가)은 O→L→H→C, 빨강봉은 O→H→L→C로 되짚는다. 이것이 봉 하나가 담은
    극단(저가·고가)을 순서대로 방문하는 최소 열이다. ⚠️ 실제 틱 경로가 아니라 OHLC 4점
    근사임을 분명히 한다 — 진짜 틱·호가는 WAN-98 소관(Canceled).
    """
    if close >= open_:
        return [open_, low, high, close]
    return [open_, high, low, close]


def fill_path_divergence(
    make_order: Callable[[], PendingLimitOrder],
    *,
    open_: float,
    low: float,
    high: float,
    close: float,
    time_ms: int,
) -> FillDivergence:
    """같은 대기 주문을 1분봉 한 방 vs 봉내 틱 열로 각각 체결 판정해 갈림을 잰다.

    `make_order`는 **매번 새 주문**을 만든다 — `on_price`가 주문 상태를 바꾸므로 두 경로가
    독립이어야 한다. 1분봉 경로는 `on_price(low, high, close)` 한 번, 틱 경로는
    `ohlc_tick_path`의 각 점을 단일가 틱(`on_price(p, p, p)`)으로 넣어 첫 체결을 취한다.
    """
    bar_order = make_order()
    bar_fill = bar_order.on_price(low, high, close, now_ms=time_ms)

    tick_order = make_order()
    tick_fill = None
    for price in ohlc_tick_path(open_, low, high, close):
        tick_fill = tick_order.on_price(price, price, price, now_ms=time_ms)
        if tick_fill is not None:
            break

    return FillDivergence(
        bar_filled=bar_fill is not None,
        tick_filled=tick_fill is not None,
        bar_price=None if bar_fill is None else bar_fill.price,
        tick_price=None if tick_fill is None else tick_fill.price,
    )


@dataclass(frozen=True)
class DivergenceSummary:
    """여러 (주문, 봉) 파리티 측정의 집계."""

    total: int
    verdict_differs: int
    max_price_delta_bps: float
    mean_price_delta_bps: float

    @property
    def verdict_differ_rate(self) -> float:
        return self.verdict_differs / self.total if self.total else 0.0


def summarize_divergences(divergences: list[FillDivergence]) -> DivergenceSummary:
    """측정 목록을 집계한다 — 체결 여부가 갈린 비율과 체결가 차이(bp) 분포."""
    total = len(divergences)
    verdict_differs = sum(1 for d in divergences if d.verdict_differs)
    deltas = [d.price_delta_bps for d in divergences if d.price_delta_bps is not None]
    max_delta = max(deltas) if deltas else 0.0
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    return DivergenceSummary(
        total=total,
        verdict_differs=verdict_differs,
        max_price_delta_bps=max_delta,
        mean_price_delta_bps=mean_delta,
    )
