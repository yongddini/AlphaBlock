"""탐지 결과 vs TV fixture 패리티 리포트.

각 TV 정답 오더블록(`TvOrderBlock`)에 대해 같은 방향·가장 가까운
`start_time`을 가진 탐지 오더블록을 후보로 골라, 허용 오차 이내인지
비교한다. 허용 오차를 벗어나거나 대응하는 탐지 결과가 없으면 불일치로
기록한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy.models import OrderBlock, OrderBlockResult
from strategy.parity.fixtures import TvFixture, TvOrderBlock

DEFAULT_PRICE_TOLERANCE_PCT = 0.05
"""top/bottom 허용 오차: TV 값 대비 상대 오차 비율(%)."""

DEFAULT_TIME_TOLERANCE_MS = 0
"""start_time 허용 오차(ms). 기본 0 = 정확히 일치해야 함."""


@dataclass(frozen=True)
class ParityMatch:
    """TV 정답 오더블록 하나에 대한 비교 결과."""

    tv_ob: TvOrderBlock
    our_ob: OrderBlock | None
    top_diff_pct: float | None
    bottom_diff_pct: float | None
    start_time_diff_ms: int | None
    invalidated_match: bool
    matched: bool
    """가격·시각·무효화 여부가 모두 허용 오차 이내인지."""


@dataclass(frozen=True)
class ParityReport:
    """fixture 하나에 대한 전체 패리티 리포트."""

    symbol: str
    timeframe: str
    price_tolerance_pct: float
    time_tolerance_ms: int
    matches: list[ParityMatch]
    extra_our_obs: list[OrderBlock]
    """TV 정답에는 없지만 탐지 결과에만 존재하는 오더블록(과탐지)."""

    @property
    def match_rate(self) -> float:
        """TV 정답 오더블록 중 일치한 비율(0.0~1.0). 정답이 없으면 1.0."""
        if not self.matches:
            return 1.0
        return sum(1 for m in self.matches if m.matched) / len(self.matches)

    def to_table(self) -> str:
        """사람이 읽는 텍스트 표를 반환한다."""
        matched_count = sum(1 for m in self.matches if m.matched)
        lines = [
            f"패리티 리포트: {self.symbol} {self.timeframe} "
            f"(허용오차: 가격 {self.price_tolerance_pct}%, 시각 {self.time_tolerance_ms}ms)",
            f"일치율: {self.match_rate * 100:.1f}% ({matched_count}/{len(self.matches)})",
            "",
            f"{'방향':<6}{'TV top':>10}{'TV bottom':>12}{'top오차%':>10}{'bottom오차%':>12}"
            f"{'시각오차(ms)':>14}{'무효화일치':>10}{'결과':>6}",
        ]
        for m in self.matches:
            top_diff = f"{m.top_diff_pct:.3f}" if m.top_diff_pct is not None else "-"
            bottom_diff = f"{m.bottom_diff_pct:.3f}" if m.bottom_diff_pct is not None else "-"
            time_diff = str(m.start_time_diff_ms) if m.start_time_diff_ms is not None else "-"
            lines.append(
                f"{m.tv_ob.direction.value:<6}{m.tv_ob.top:>10.2f}{m.tv_ob.bottom:>12.2f}"
                f"{top_diff:>10}{bottom_diff:>12}{time_diff:>14}"
                f"{'Y' if m.invalidated_match else 'N':>10}"
                f"{'PASS' if m.matched else 'FAIL':>6}"
            )
        if self.extra_our_obs:
            lines.append("")
            lines.append(f"과탐지(TV 정답에 없는 탐지 결과): {len(self.extra_our_obs)}건")
            for ob in self.extra_our_obs:
                lines.append(
                    f"  {ob.direction.value} top={ob.top:.2f} bottom={ob.bottom:.2f} "
                    f"start_time={ob.start_time}"
                )
        return "\n".join(lines)


def _pct_diff(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else float("inf")
    return abs(actual - expected) / abs(expected) * 100.0


def _find_candidate(
    tv_ob: TvOrderBlock, our_obs: list[OrderBlock], time_tolerance_ms: int
) -> OrderBlock | None:
    same_direction = [ob for ob in our_obs if ob.direction == tv_ob.direction]
    if not same_direction:
        return None
    closest = min(same_direction, key=lambda ob: abs(ob.start_time - tv_ob.start_time))
    if abs(closest.start_time - tv_ob.start_time) > time_tolerance_ms:
        return None
    return closest


def compare_to_fixture(
    result: OrderBlockResult,
    fixture: TvFixture,
    *,
    price_tolerance_pct: float = DEFAULT_PRICE_TOLERANCE_PCT,
    time_tolerance_ms: int = DEFAULT_TIME_TOLERANCE_MS,
) -> ParityReport:
    """탐지 결과를 TV fixture와 비교해 `ParityReport`를 만든다.

    WAN-47: 비교 대상은 마지막 봉 시점의 **렌더링 뷰**(`rendered_order_blocks`,
    트레이딩뷰가 실제로 그리는 박스 집합)다. `result.order_blocks`는 이제 삭제되지
    않은 전체 생애주기 아카이브이므로, 그것과 직접 비교하면 TV가 지운 존까지 잡혀
    과탐지가 된다. 패리티는 어디까지나 "지금 그릴 박스"에 대한 것이다.
    """
    remaining = list(result.rendered_order_blocks)
    matches: list[ParityMatch] = []

    for tv_ob in fixture.tv_order_blocks:
        candidate = _find_candidate(tv_ob, remaining, time_tolerance_ms)
        if candidate is None:
            matches.append(
                ParityMatch(
                    tv_ob=tv_ob,
                    our_ob=None,
                    top_diff_pct=None,
                    bottom_diff_pct=None,
                    start_time_diff_ms=None,
                    invalidated_match=False,
                    matched=False,
                )
            )
            continue

        remaining.remove(candidate)
        top_diff = _pct_diff(candidate.top, tv_ob.top)
        bottom_diff = _pct_diff(candidate.bottom, tv_ob.bottom)
        time_diff = abs(candidate.start_time - tv_ob.start_time)
        invalidated_match = candidate.breaker == tv_ob.invalidated
        matched = (
            top_diff <= price_tolerance_pct
            and bottom_diff <= price_tolerance_pct
            and time_diff <= time_tolerance_ms
            and invalidated_match
        )
        matches.append(
            ParityMatch(
                tv_ob=tv_ob,
                our_ob=candidate,
                top_diff_pct=top_diff,
                bottom_diff_pct=bottom_diff,
                start_time_diff_ms=time_diff,
                invalidated_match=invalidated_match,
                matched=matched,
            )
        )

    return ParityReport(
        symbol=fixture.symbol,
        timeframe=fixture.timeframe,
        price_tolerance_pct=price_tolerance_pct,
        time_tolerance_ms=time_tolerance_ms,
        matches=matches,
        extra_our_obs=remaining,
    )
