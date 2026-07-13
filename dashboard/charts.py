"""오더블록 생애주기 분류·필터(WAN-52)와 자본곡선(equity curve) 차트.

캔들+오더블록+RSI 메인 차트는 `dashboard.lightweight_chart`(TradingView Lightweight
Charts 임베드, WAN-54)로 이관됐다. 이 모듈에 남은 `ZoneCategory`/`filter_zones`/
`entered_zone_keys`는 렌더 엔진과 무관한 순수 분류 로직이라 그대로 재사용한다.
자본곡선은 점 개수가 적어(거래 수만큼) 성능 문제가 없어 Plotly로 남긴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

import pandas as pd
import plotly.graph_objects as go

from backtest.models import BacktestResult
from strategy.models import OrderBlock, OrderBlockSignal


class ZoneCategory(StrEnum):
    """오더블록의 생애주기 상태 분류(표시 필터용, WAN-52).

    한 존은 여러 범주에 동시에 속할 수 있다(예: 진입이 발생한 활성 존).
    """

    ENTERED = "entered"
    """실제 진입(활성 시그널)이 발생한 존."""
    ACTIVE = "active"
    """아직 무효화·소멸하지 않은 존."""
    TAPPED = "tapped"
    """가격이 닿았고(tap) 아직 깨지지 않은 존 — "지지했던" 존."""
    BROKEN = "broken"
    """breaker로 무효화됐으나 아직 소멸하지 않은 존 — "깨졌던" 존."""
    SWEPT = "swept"
    """되쓸려 완전히 소멸한 존."""


#: 사이드바 표시 필터의 기본 선택(전체가 아닌 "진입한 존 + 활성 존").
DEFAULT_ZONE_CATEGORIES: frozenset[ZoneCategory] = frozenset(
    {ZoneCategory.ENTERED, ZoneCategory.ACTIVE}
)

ZONE_CATEGORY_LABELS: dict[ZoneCategory, str] = {
    ZoneCategory.ENTERED: "진입한 존",
    ZoneCategory.ACTIVE: "활성 존",
    ZoneCategory.TAPPED: "지지한 존(탭)",
    ZoneCategory.BROKEN: "깨진 존(무효화)",
    ZoneCategory.SWEPT: "소멸한 존",
}


def zone_key(ob: OrderBlock) -> tuple[str, int, int, float, float]:
    """존을 식별하는 해시 가능한 키(방향·시작·확정·상하단)."""
    return (str(ob.direction), ob.start_time, ob.confirmed_time, ob.top, ob.bottom)


def entered_zone_keys(
    signals: Iterable[OrderBlockSignal],
) -> set[tuple[str, int, int, float, float]]:
    """진입(활성 시그널)이 발생한 존들의 키 집합을 만든다 (WAN-52).

    사용자의 실제 매매 규칙(존당 첫 탭 1회 + RSI)을 시계열로 재생해 나온 진입
    시그널이 `status="active"`다. cancelled(무효화 봉에서의 탭)는 진입이 아니다.
    """
    return {zone_key(s.order_block) for s in signals if s.status == "active"}


def zone_categories(
    ob: OrderBlock,
    entered_keys: set[tuple[str, int, int, float, float]] | None = None,
) -> set[ZoneCategory]:
    """존이 속한 생애주기 범주 집합을 반환한다 (WAN-52).

    소멸(`swept_time`)이 최우선 종말 상태, 그다음 무효화(`break_time`)다. 둘 다
    없으면 활성이며, 활성이면서 탭 기록이 있으면 "지지"로도 분류한다. 진입 여부는
    `entered_keys`(있으면)로 판정해 독립적으로 추가한다.
    """
    cats: set[ZoneCategory] = set()
    if entered_keys is not None and zone_key(ob) in entered_keys:
        cats.add(ZoneCategory.ENTERED)
    if ob.swept_time is not None:
        cats.add(ZoneCategory.SWEPT)
    elif ob.break_time is not None:
        cats.add(ZoneCategory.BROKEN)
    else:
        cats.add(ZoneCategory.ACTIVE)
        if ob.tapped_times:
            cats.add(ZoneCategory.TAPPED)
    return cats


def filter_zones(
    order_blocks: Sequence[OrderBlock],
    selected: Iterable[ZoneCategory],
    entered_keys: set[tuple[str, int, int, float, float]] | None = None,
) -> list[OrderBlock]:
    """선택된 범주 중 하나라도 해당하는 존만 남긴다 (WAN-52)."""
    wanted = set(selected)
    if not wanted:
        return []
    return [ob for ob in order_blocks if zone_categories(ob, entered_keys) & wanted]


def build_equity_chart(backtest: BacktestResult, *, theme: str = "dark") -> go.Figure:
    """자본곡선(equity curve) Figure.

    `theme`(`"light"`/`"dark"`, 기본 다크)에 맞춰 Plotly 템플릿을 고르고, 배경은
    투명으로 둬 Streamlit 테마와 자연스레 섞이게 한다(WAN-55).
    """
    is_dark = theme != "light"
    template = "plotly_dark" if is_dark else "plotly_white"
    line_color = "#42a5f5" if is_dark else "#1e88e5"
    times = [pd.Timestamp(p.time, unit="ms", tz="UTC") for p in backtest.equity_curve]
    equities = [p.equity for p in backtest.equity_curve]
    fig = go.Figure(
        data=[
            go.Scatter(x=times, y=equities, mode="lines", name="equity", line={"color": line_color})
        ]
    )
    fig.update_layout(
        title="Equity Curve",
        template=template,
        height=300,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig
