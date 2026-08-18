"""손절폭(1R)을 라이브·백테 **같은 셋업**에서 나란히 본다 (WAN-328 §1·§3).

## 왜 필요한가

`alphablock fills --day 2026-08-17`이 잰 값: 체결 13건 중 **12건이 손절폭 가드(0.3%)에 걸려**
진입이 1건이었다(손절폭 0.03~0.26%). 같은 가드를 백테로 잰 값은 6년 평균 15m 38.5%(WAN-197)라
두 배 넘게 벌어진다. **가드는 같은데 재는 대상(손절폭)이 양쪽에서 다르게 나온다**는 뜻이고,
그 갈림이 **진입가**인지 **무효화 경계**인지 **존 자체**인지가 이 모듈의 질문이다.

세 값이 1R을 각각 움직인다:

* **진입가** — 라이브는 틱마다 봉내 라이브 밴드를 다시 계산해 지정가를 옮긴다(WAN-132/256).
  백테는 1분봉 서브스텝에서 **종가를 표본으로** 낸 지정가를 쓴다.
* **무효화 경계** — 손절 참조가. 같은 오더블록이면 같아야 한다.
* **존 자체** — 라이브가 본 존과 백테가 본 존이 다르면 손절폭 비교 자체가 성립하지 않는다.

그래서 조인은 **존 정체성**(심볼·TF·방향·존 시작·존 확정·탭 순번)으로만 한다 —
`live.setup_compare`가 이미 그 조인을 소유하므로 **재사용한다**(두 벌로 갈라지면 같은 셋업이
두 화면에서 다른 짝을 얻는다). 이 모듈이 더하는 것은 **손절폭 해부와 귀속** 하나다.

## 귀속(attribution) 규칙

두 쪽 다 체결된 셋업에서 손절폭 차이를 이렇게 가른다(허용 오차는 1bp):

* 무효화 경계가 다르면 → **`존/경계`**(같은 존을 봤는지부터 의심할 자리).
* 경계는 같고 진입가만 다르면 → **`진입가`**(체결 모델 차이 = 틱 대 1분봉).
* 둘 다 같으면 → **`동일`**(손절폭이 갈리지 않았다).

📌 **손절폭에는 구조적 천장이 있다** — 진입가는 존 안으로 클램프되고 손절 참조가는 존
무효화 경계라 `손절폭 ≤ 존 높이 + 오프셋`이다. 천장이 가드보다 낮으면 **진입가를 어디에
잡아도** 걸리므로 그 셋업의 탈락은 체결 모델과 무관하다. 백테 쪽 천장 분포는
`backtest.wan328_stop_width_parity`가 낸다(이 모듈은 장부에 있는 값만 쓴다 — 지어내지 않는다).

## 성격

**순수 조회·조인이다.** DB에 아무것도 쓰지 않고(WAN-194 원칙) 엔진·전략·기본값·토대를 건드리지
않는다(가드 0.3%는 WAN-76/79 소관 · 재-베이스라인 = 사용자 결정). 전부 페이퍼이고
`ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from common.timefmt import KST_LABEL, format_kst
from execution.sizing import PositionSizingParams
from live.order_journal import ENTRY_STATUS_REJECTED, OrderJournal, PlacedOrder
from live.setup_compare import SetupComparison, build_setup_comparisons
from live.trade_timeline import TimelineRow

__all__ = [
    "ATTRIBUTION_BOUNDARY",
    "ATTRIBUTION_ENTRY",
    "ATTRIBUTION_SAME",
    "GUARD_FRACTION",
    "LiveStopWidth",
    "PairAttribution",
    "StopWidthReport",
    "build_report",
    "live_stop_widths",
    "pair_attributions",
    "render_report",
]

#: 손절폭 가드 = 채택값을 **읽는다**(리터럴을 다시 적지 않는다 — 값이 바뀌면 이 표가 조용히
#: 옛 가드를 재게 된다). WAN-76/79 소관이고 이 모듈은 관측자다.
GUARD_FRACTION: float = PositionSizingParams().min_stop_distance_fraction

#: 두 쪽 값이 「같다」고 볼 허용 오차(bp). 가격 표현·반올림 잡음만 흡수하는 크기다.
SAME_PRICE_TOLERANCE_BPS = 1.0

ATTRIBUTION_SAME = "동일"
ATTRIBUTION_ENTRY = "진입가"
ATTRIBUTION_BOUNDARY = "존/경계"
_ATTRIBUTION_BOTH = "둘 다"


def _width_pct(entry: float | None, stop: float | None) -> float | None:
    """손절폭(%) = `|진입가 − 손절가| / 진입가 × 100`. 값이 없으면 None(지어내지 않는다)."""
    if entry is None or stop is None or entry <= 0.0:
        return None
    return abs(entry - stop) / entry * 100.0


def _delta_bps(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or right == 0.0:
        return None
    return abs(left - right) / abs(right) * 10_000.0


# --------------------------------------------------------------------------- #
# §3 — 라이브 장부 쪽 분포
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LiveStopWidth:
    """체결된 라이브 주문 하나의 손절폭 (§3의 원자 단위)."""

    symbol: str
    timeframe: str
    fill_ms: int
    fill_price: float
    stop_price: float
    stop_width_pct: float
    guard_passed: bool
    """가드 기준선을 넘었나. ⚠️ **집행이 실제로 진입했는지와는 다른 질문이다** —
    명목 상한·칸 점유 같은 다른 관문도 있다(`entry_rejected`가 실제 결과)."""
    entry_rejected: bool
    entry_reject_reason: str | None
    origin: str | None


def live_stop_widths(orders: Sequence[PlacedOrder]) -> list[LiveStopWidth]:
    """체결됐고 체결가·손절가가 남은 주문만 손절폭 행으로 바꾼다 (순수 함수).

    ⚠️ WAN-234 이전 행은 `fill_price`·`stop_price` 열이 없어 `None`이다 — **빠뜨리지 않고
    조용히 0으로 채우지도 않는다**(판별 불가는 판별 불가로 남긴다, WAN-194 규약). 그 행은
    이 목록에 들어오지 않으므로 호출부가 「체결 수」와 「손절폭이 남은 수」를 함께 읽는다.
    """
    rows: list[LiveStopWidth] = []
    for order in orders:
        width = _width_pct(order.fill_price, order.stop_price)
        if width is None or order.fill_ms is None:
            continue
        assert order.fill_price is not None and order.stop_price is not None
        rows.append(
            LiveStopWidth(
                symbol=order.symbol,
                timeframe=order.timeframe,
                fill_ms=order.fill_ms,
                fill_price=order.fill_price,
                stop_price=order.stop_price,
                stop_width_pct=width,
                guard_passed=width >= GUARD_FRACTION * 100.0,
                entry_rejected=order.entry_status == ENTRY_STATUS_REJECTED,
                entry_reject_reason=order.entry_reject_reason,
                origin=order.origin,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# §1 — 같은 셋업 조인 + 귀속
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PairAttribution:
    """같은 셋업의 라이브·백테 손절폭 한 줄과 **갈리는 지점**."""

    symbol: str
    timeframe: str
    is_long: bool
    focus_ms: int | None
    live_entry: float | None
    live_stop: float | None
    live_width_pct: float | None
    backtest_entry: float | None
    backtest_stop: float | None
    backtest_width_pct: float | None
    entry_delta_bps: float | None
    stop_delta_bps: float | None
    width_delta_pp: float | None
    """Δ = 라이브 손절폭 − 백테 손절폭(%p). 음수면 라이브가 더 좁다(= 가드에 더 잘 걸린다)."""
    attribution: str
    live_guard_passed: bool | None
    backtest_guard_passed: bool | None

    @property
    def guard_verdict_differs(self) -> bool:
        """가드 판정이 갈렸나 — 이 표의 핵심 신호(한쪽만 0.3%를 넘었다)."""
        return (
            self.live_guard_passed is not None
            and self.backtest_guard_passed is not None
            and self.live_guard_passed != self.backtest_guard_passed
        )


def _fill_price(row: TimelineRow | None) -> float | None:
    """그 행이 실제로 체결한 가격. 미체결 백테 셋업은 걸어 둔 지정가(`limit_price`)를 쓴다."""
    if row is None:
        return None
    return row.fill_price if row.fill_price is not None else row.limit_price


def _attribute(entry_delta_bps: float | None, stop_delta_bps: float | None) -> str:
    entry_moved = entry_delta_bps is not None and entry_delta_bps > SAME_PRICE_TOLERANCE_BPS
    stop_moved = stop_delta_bps is not None and stop_delta_bps > SAME_PRICE_TOLERANCE_BPS
    if entry_moved and stop_moved:
        return _ATTRIBUTION_BOTH
    if stop_moved:
        return ATTRIBUTION_BOUNDARY
    if entry_moved:
        return ATTRIBUTION_ENTRY
    return ATTRIBUTION_SAME


def pair_attributions(comparisons: Sequence[SetupComparison]) -> list[PairAttribution]:
    """셋업 대조 결과에서 **양쪽 다 가격이 있는** 짝만 손절폭 해부 행으로 바꾼다.

    조인은 `live.setup_compare`가 이미 한 것을 그대로 받는다 — 존 정체성 키 + 재진입 구제
    조인(WAN-305)이 여기 그대로 적용된다. 한쪽만 있는 셋업은 손절폭을 **비교할 수 없으므로**
    이 표에 넣지 않는다(이슈 §1의 «존이 다르면 비교가 성립하지 않는다»).
    """
    rows: list[PairAttribution] = []
    for comp in comparisons:
        live_entry = _fill_price(comp.live)
        bt_entry = _fill_price(comp.backtest)
        live_stop = comp.live.stop_price if comp.live is not None else None
        bt_stop = comp.backtest.stop_price if comp.backtest is not None else None
        live_width = _width_pct(live_entry, live_stop)
        bt_width = _width_pct(bt_entry, bt_stop)
        if live_width is None or bt_width is None:
            continue
        entry_delta = _delta_bps(live_entry, bt_entry)
        stop_delta = _delta_bps(live_stop, bt_stop)
        rows.append(
            PairAttribution(
                symbol=comp.symbol,
                timeframe=comp.timeframe,
                is_long=comp.is_long,
                focus_ms=comp.focus_ms,
                live_entry=live_entry,
                live_stop=live_stop,
                live_width_pct=live_width,
                backtest_entry=bt_entry,
                backtest_stop=bt_stop,
                backtest_width_pct=bt_width,
                entry_delta_bps=entry_delta,
                stop_delta_bps=stop_delta,
                width_delta_pp=live_width - bt_width,
                attribution=_attribute(entry_delta, stop_delta),
                live_guard_passed=live_width >= GUARD_FRACTION * 100.0,
                backtest_guard_passed=bt_width >= GUARD_FRACTION * 100.0,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 리포트
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StopWidthReport:
    """한 창의 손절폭 해부 — §1(짝) + §3(라이브 분포)."""

    window_label: str
    live_orders: int
    """창 안에서 체결된 라이브 주문 수(손절폭이 안 남은 옛 행 포함)."""
    live: tuple[LiveStopWidth, ...]
    pairs: tuple[PairAttribution, ...]
    backtest_ran: bool
    """백테 대조를 실제로 돌렸는지. 거짓이면 §1 표는 비어 있고 그 사실을 화면이 밝힌다."""


def build_report(
    journal: OrderJournal,
    *,
    start_ms: int,
    end_ms: int,
    window_label: str,
    backtest_rows: Sequence[TimelineRow] | None = None,
    live_rows: Sequence[TimelineRow] | None = None,
) -> StopWidthReport:
    """장부(+ 선택적 백테 타임라인)로 손절폭 해부 리포트를 만든다.

    `backtest_rows`가 없으면 §3(라이브 분포)만 낸다 — 백테 대조는 채택 좌표 48셀 × 워밍업이라
    무겁고, 「오늘 라이브가 얼마나 걸렸나」만 보고 싶은 호출이 더 흔하다.
    """
    orders = journal.orders_placed_between(start_ms=start_ms, end_ms=end_ms)
    filled = [o for o in orders if o.fill_ms is not None]
    live_widths = live_stop_widths(orders)
    pairs: list[PairAttribution] = []
    if backtest_rows is not None and live_rows is not None:
        result = build_setup_comparisons(live_rows, backtest_rows)
        pairs = pair_attributions(result.comparisons)
    return StopWidthReport(
        window_label=window_label,
        live_orders=len(filled),
        live=tuple(live_widths),
        pairs=tuple(pairs),
        backtest_ran=backtest_rows is not None,
    )


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _fmt(value: float | None, digits: int = 3, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def render_report(report: StopWidthReport) -> str:
    """사람이 읽는 표. 시각은 KST 고정(WAN-172)."""
    guard_pct = GUARD_FRACTION * 100.0
    lines: list[str] = [
        f"손절폭 해부 — {report.window_label} ({KST_LABEL}) · 가드 {guard_pct:.1f}%",
        "=" * 72,
        "",
        f"§3 라이브 체결 {report.live_orders}건 중 손절폭이 남은 것 {len(report.live)}건",
    ]
    if len(report.live) < report.live_orders:
        lines.append(
            "  ⚠️ 나머지는 체결가·손절가 열이 없는 옛 행이라 **판별 불가**입니다(WAN-234 이전)."
        )
    if report.live:
        lines += ["", "  TF별 손절폭 · 가드 탈락", "  " + "-" * 68]
        lines.append(
            f"  {'TF':<6}{'체결':>6}{'가드미달':>9}{'미달률':>9}{'p10':>9}{'중앙값':>9}{'p90':>9}"
        )
        for timeframe in sorted({r.timeframe for r in report.live}):
            cell = [r for r in report.live if r.timeframe == timeframe]
            widths = [r.stop_width_pct for r in cell]
            under = sum(1 for r in cell if not r.guard_passed)
            lines.append(
                f"  {timeframe:<6}{len(cell):>6}{under:>9}{under / len(cell) * 100:>8.1f}%"
                f"{_fmt(_quantile(widths, 0.10)):>9}{_fmt(_quantile(widths, 0.50)):>9}"
                f"{_fmt(_quantile(widths, 0.90)):>9}"
            )
        reasons: dict[str, int] = {}
        for row in report.live:
            if row.entry_rejected:
                reasons[row.entry_reject_reason or "(사유 미기록)"] = (
                    reasons.get(row.entry_reject_reason or "(사유 미기록)", 0) + 1
                )
        if reasons:
            lines += ["", "  진입 거부 사유 분포", "  " + "-" * 68]
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {count:>5}건  {reason}")
    else:
        lines.append("  (손절폭을 잰 체결이 없습니다.)")

    lines += ["", "§1 같은 셋업 조인 — 라이브 대 백테 손절폭", "-" * 72]
    if not report.backtest_ran:
        lines.append("  백테 대조를 돌리지 않았습니다(`--with-backtest`로 켭니다).")
        return "\n".join(lines)
    if not report.pairs:
        lines.append(
            "  양쪽 다 가격이 있는 짝이 없습니다 — 조인된 셋업이 없거나 한쪽만 체결했습니다."
        )
        return "\n".join(lines)
    lines.append(
        f"  {'심볼':<14}{'TF':<5}{'시각':<18}{'라이브폭':>9}{'백테폭':>9}{'Δ%p':>9}{'귀속':>8}"
    )
    for pair in sorted(report.pairs, key=lambda p: (p.focus_ms or 0, p.symbol)):
        stamp = format_kst(pair.focus_ms) if pair.focus_ms is not None else "—"
        flag = " 🔴" if pair.guard_verdict_differs else ""
        lines.append(
            f"  {pair.symbol:<14}{pair.timeframe:<5}{stamp:<18}"
            f"{_fmt(pair.live_width_pct):>9}{_fmt(pair.backtest_width_pct):>9}"
            f"{_fmt(pair.width_delta_pp):>9}{pair.attribution:>8}{flag}"
        )
    counts: dict[str, int] = {}
    for pair in report.pairs:
        counts[pair.attribution] = counts.get(pair.attribution, 0) + 1
    differing = sum(1 for p in report.pairs if p.guard_verdict_differs)
    lines += [
        "",
        "  귀속 집계: " + " · ".join(f"{k} {v}건" for k, v in sorted(counts.items())),
        f"  가드 판정이 갈린 셋업: {differing}건 / {len(report.pairs)}건",
        "",
        "  ⚠️ 조인은 존 정체성(존 시작·확정·탭 순번)으로만 한다 — 짝이 안 지어진 셋업은 존이",
        "     다르다는 뜻이라 손절폭 비교가 성립하지 않아 표에서 빠진다.",
    ]
    return "\n".join(lines)
