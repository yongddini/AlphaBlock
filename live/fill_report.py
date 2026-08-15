"""체결률 실측 요약 CLI — `python -m live.fill_report` (WAN-45의 1급 산출물).

`live.order_journal`이 누적한 지정가 주문 생애를 심볼·TF별로 요약해, 백테스트의
`baseline`("닿으면 체결") 가정과 나란히 읽을 수 있는 표를 찍는다.

## 표 읽는 법

* **체결률** = 체결 / 결말(체결+만료+무효화+조건취소). 아직 대기 중·재시작 폐기 건은
  분모에서 뺀다 — 결과가 정해지지 않았거나 러너가 죽어 결과를 알 수 없는 표본이다.
* **스침%** = 체결 중 관통 < 5bp("스치듯 닿은 체결")의 비중. 실거래에서 큐 우선순위
  때문에 가장 안 될 부류라(WAN-96), `pen_5bp` 렌즈가 부정하는 체결이 실측에서 얼마나
  나오는지를 잰다 — 이 비중이 크면 `baseline` 낙관 가정의 비용도 크다.
* **가동 구간**: 러너 세션(시작~마지막 하트비트)과 그 사이의 틈(다운타임). 체결률의
  분모는 "러너가 살아 있던 시간"이다 — 로컬 맥 운영이라 구멍이 날 수 있고(사용자 결정
  2026-07-21), 그 구멍을 표에서 걸러 읽을 수 있게 남긴다.
* **진입%**(WAN-194) = 체결 중 페이퍼 포지션이 실제로 열린 비중. **체결률과 곱해 읽는
  값이다** — 체결은 됐는데 집행 계층이 거부하면(대부분 손절폭 가드 0.3%, WAN-79) 거래가
  되지 않는다. 백테스트도 같은 가드로 후보를 버리므로 파리티가 깨진 건 아니지만, "체결률
  81%"를 거래 성립률로 오독하지 않으려면 두 값이 같이 보여야 한다.
* **미기록**: 체결의 처분이 안 남은 건수. 러너가 체결 기록과 포지션 쓰기 **사이에서
  죽으면** 이 칸이 오른다(두 쓰기는 원자적이지 않다) — 0이 아니면 아래 유실 섹션을 본다.
  ⚠️ WAN-194 이전 기록은 열이 없어 전부 여기 잡힌다(유실이 아니라 **판별 불가**).

⚠️ 페이퍼는 실제 주문을 내지 않으므로 "닿았는데 큐에 밀려 안 채워짐"은 직접 관측할 수
없다 — 그 근사가 스침%다(닿기만 하고 관통 없는 체결 = 실제였다면 채워지지 않았을
가능성이 가장 큰 체결).
"""

from __future__ import annotations

import argparse
from datetime import datetime

from common.timefmt import KST, KST_LABEL, format_kst, kst_day_bounds_for_date
from config.settings import get_settings
from live.limit_orders import LimitOrderStatus
from live.order_journal import (
    ENTRY_STATUS_ENTERED,
    ENTRY_STATUS_REJECTED,
    MARGINAL_FILL_BPS,
    ORDER_ORIGIN_REENTRY,
    ORDER_ORIGIN_TAP,
    STATUS_DISCARDED_RESTART,
    STATUS_SKIPPED,
    OrderJournal,
    PlacedOrder,
    SeriesFillStats,
)

#: 당일 조회의 상태 열 라벨 — 장부 원 상태값을 사람이 읽는 한 낱말로 옮긴다(WAN-232).
#: 만료(`CANCELLED_EXPIRED`)만은 `first_rested_ms` 유무로 미체결/밴드기각이 갈려 아래
#: 매핑에 넣지 않고 `_status_label`이 따로 판정한다.
_STATUS_LABELS = {
    LimitOrderStatus.FILLED.value: "체결",
    LimitOrderStatus.CANCELLED_INVALIDATED.value: "무효화",
    LimitOrderStatus.CANCELLED_CONDITION_FAILED.value: "조건취소",
    LimitOrderStatus.PENDING.value: "대기",
    STATUS_DISCARDED_RESTART: "재시작폐기",
    STATUS_SKIPPED: "필터제외",
}


def _fmt_ms(ms: int) -> str:
    """표 안의 시각(KST, WAN-172). 열 이름이 시간대를 밝히므로 표기는 생략한다."""
    return format_kst(ms)


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _render_skip_funnel(stats: list[SeriesFillStats]) -> list[str]:
    """주문 걸기 전 미진입 사유(깔때기 상단) 섹션(WAN-217).

    체결률 표(WAN-45/194)가 "주문이 걸린 뒤" 깔때기를 보여 준다면, 이 표는 그 **위** —
    지정가가 확정되기 전 걸러진 셋업(존폭 필터·슬롯 점유·재탭)과, 걸렸으나 밴드가 한 번도
    유리하지 않아 끝난 볼린저 규칙 3 기각(deviation)을 센다. 데이터가 하나도 없으면
    섹션을 통째로 생략한다(요약 표를 잡음으로 채우지 않는다)."""
    if not any(s.skipped or s.unfilled_no_band for s in stats):
        return []
    lines = ["", "## 주문 걸기 전 미진입 사유 (WAN-217 — 깔때기 상단)", ""]
    lines.append("| 심볼 | TF | 존폭기각 | 슬롯참 | 재탭 | 밴드기각(no_fill중) |")
    lines.append("| -- | -- | --: | --: | --: | --: |")
    for s in stats:
        if not (s.skipped or s.unfilled_no_band):
            continue
        lines.append(
            f"| {s.symbol} | {s.timeframe} | {s.skipped_zone_width} | {s.skipped_cell_busy} |"
            f" {s.skipped_retap} | {s.unfilled_no_band} |"
        )
    lines.append("")
    lines.append(
        "존폭기각·슬롯참·재탭은 주문이 걸리기 **전** 걸러져 체결률 분모 밖이다(주문 생애를"
        " 시작조차 안 했다). **밴드기각**은 만료(no_fill) 중 밴드가 한 번도 유리하지 않아"
        " 주문판에 실린 적 없는 셋업 수다(볼린저 규칙 3 기각) — 걸렸다 안 닿은 순수 미체결과"
        " 구분한다. 존폭기각이 많으면 존폭 필터(WAN-159)가 그만큼 셋업을 쳐내고 있다는"
        " 실측이다."
    )
    return lines


def render_report(journal: OrderJournal) -> str:
    """체결률 요약 표(마크다운)를 렌더한다."""
    lines: list[str] = ["# 지정가 체결률 실측 (WAN-45)", ""]

    stats = journal.fill_stats()
    if not stats:
        lines.append("아직 기록된 주문이 없습니다.")
    else:
        lines.append(
            "| 심볼 | TF | 걸림 | 대기 | 체결 | 만료 | 무효화 | 조건취소 | 폐기 | 체결률 |"
            f" 대기중앙값 | 스침%(<{MARGINAL_FILL_BPS:g}bp) | 진입 | 거부 | 미기록 | 진입% |"
        )
        lines.append(
            "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |"
            " --: | --: | --: | --: |"
        )
        for s in stats:
            wait = "-" if s.median_wait_ms is None else f"{s.median_wait_ms / 60000:.0f}분"
            lines.append(
                f"| {s.symbol} | {s.timeframe} | {s.placed} | {s.pending} | {s.filled} |"
                f" {s.cancelled_expired} | {s.cancelled_invalidated} |"
                f" {s.cancelled_condition_failed} | {s.discarded_restart} |"
                f" {_fmt_rate(s.fill_rate)} | {wait} | {_fmt_rate(s.marginal_fill_share)} |"
                f" {s.entered} | {s.entry_rejected} | {s.entry_unrecorded} |"
                f" {_fmt_rate(s.entry_rate)} |"
            )
        lines.append("")
        lines.append(
            "체결률의 백테스트 대응값은 `baseline` 렌즈 체결률"
            "(`wan95_zone_limit_summary.md`, 낙관 상한)이다. 스침% 비중이 크면 그 상한이"
            " 그만큼 부풀려져 있다는 실측 증거다."
        )
        lines.append("")
        lines.append(
            "⚠️ **거래가 되는 비율은 체결률 × 진입%다**(WAN-194) — 체결된 지정가도 집행"
            " 계층이 거부하면(손절폭 가드 0.3% WAN-79 · 명목 상한 · 이미 오픈 포지션)"
            " 포지션이 열리지 않는다. 백테스트도 같은 가드로 후보를 버리므로 파리티가 깨진"
            " 것은 아니다."
        )

    lines.extend(_render_skip_funnel(stats))

    lines.append("")
    lines.append("## 처분 미기록 체결 (WAN-194 — 유실 후보)")
    orphans = journal.orphan_fills()
    if not orphans:
        lines.append("")
        lines.append("없음 — 모든 체결에 진입/거부 처분이 남아 있습니다.")
    else:
        lines.append("")
        lines.append(
            f"**{len(orphans)}건**. 체결은 남았는데 그 체결이 포지션이 됐는지 거부됐는지"
            " 기록이 없다 — 러너가 두 쓰기 사이에서 죽으면 이 모양이 남는다."
            " ⚠️ WAN-194 이전 체결은 열 자체가 없어 전부 여기 잡히므로,"
            " **도입 이후 행만 유실로 읽을 것**(그 전은 판별 불가)."
        )
        lines.append("")
        lines.append(f"| 장부 id | 심볼 | TF | 체결({KST_LABEL}) | 체결가 | 손절 참조 |")
        lines.append("| --: | -- | -- | -- | --: | --: |")
        for orphan in orphans:
            when = "-" if orphan.fill_ms is None else _fmt_ms(orphan.fill_ms)
            price = "-" if orphan.fill_price is None else f"{orphan.fill_price:.8g}"
            stop = "-" if orphan.stop_price is None else f"{orphan.stop_price:.8g}"
            lines.append(
                f"| {orphan.journal_id} | {orphan.symbol} | {orphan.timeframe} | {when} |"
                f" {price} | {stop} |"
            )

    lines.append("")
    lines.append("## 러너 가동 구간 (체결률의 분모)")
    sessions = journal.sessions()
    if not sessions:
        lines.append("")
        lines.append("기록된 세션이 없습니다.")
    else:
        lines.append("")
        lines.append(f"| 세션 | 시작({KST_LABEL}) | 마지막 하트비트({KST_LABEL}) | 가동 시간 |")
        lines.append("| --: | -- | -- | --: |")
        total_up = 0
        for span in sessions:
            up_ms = max(span.last_seen_ms - span.started_ms, 0)
            total_up += up_ms
            lines.append(
                f"| {span.session_id} | {_fmt_ms(span.started_ms)} | {_fmt_ms(span.last_seen_ms)} |"
                f" {up_ms / 3_600_000:.1f}h |"
            )
        first, last = sessions[0], sessions[-1]
        wall_ms = max(last.last_seen_ms - first.started_ms, 1)
        lines.append("")
        lines.append(
            f"전체 관측 창 {wall_ms / 3_600_000:.1f}h 중 가동 {total_up / 3_600_000:.1f}h"
            f" (커버리지 {total_up / wall_ms * 100:.1f}%) — 세션 사이 틈은 측정 공백이다."
        )
    return "\n".join(lines)


def _status_label(order: PlacedOrder) -> str:
    """당일 표의 상태 열 라벨(WAN-232). 만료는 밴드기각(deviation)과 순수 미체결을 가른다."""
    if order.status == LimitOrderStatus.CANCELLED_EXPIRED.value:
        return "미체결" if order.first_rested_ms is not None else "밴드기각"
    return _STATUS_LABELS.get(order.status, order.status)


def _origin_label(order: PlacedOrder) -> str:
    """구분 열(WAN-305): 탭 예약 / 재진입 재무장 / 판별 불가(열 도입 전 행).

    재진입(`origin == "reentry"`)은 익절 후 같은 존에 다시 건 주문(WAN-274)이라 탭이
    아니다 — 이 열이 없으면 사후 수사가 재진입 여부에서 막힌다(WAN-305 §3의 동기).
    """
    if order.origin == ORDER_ORIGIN_REENTRY:
        return "재진입"
    if order.origin == ORDER_ORIGIN_TAP:
        return "탭"
    return "-"  # 열 도입 전 행 — 판별 불가(WAN-194 규약).


def _entry_cell(order: PlacedOrder) -> str:
    """진입결과 열(WAN-194): 진입/거부(사유)/미기록 — 체결이 아니면 빈칸."""
    if order.entry_status == ENTRY_STATUS_ENTERED:
        return "진입"
    if order.entry_status == ENTRY_STATUS_REJECTED:
        return f"거부({order.entry_reject_reason or '사유 미기록'})"
    if order.status == LimitOrderStatus.FILLED.value:
        return "미기록"  # 체결인데 처분이 안 남음(orphan 후보, WAN-194).
    return "-"


def resolve_day_window(day_arg: str) -> tuple[int, int, str]:
    """`--day` 인자를 `[start_ms, end_ms)` KST 하루 창 + 날짜 키로 푼다(WAN-232).

    `"today"`(인자 없이 `--day`)는 지금(KST)이 속한 날, 그 밖은 `YYYY-MM-DD`로 해석한다.
    창 경계는 `common.timefmt.kst_day_bounds_for_date`가 잡아 일일 요약 funnel과 같은 자정
    경계를 공유한다(CTA #2). 잘못된 날짜 문자열은 `ValueError`로 그대로 올린다.
    """
    if day_arg == "today":
        day = datetime.now(tz=KST).date()
    else:
        day = datetime.strptime(day_arg, "%Y-%m-%d").date()
    start_ms, end_ms = kst_day_bounds_for_date(day)
    return start_ms, end_ms, day.isoformat()


def render_day_report(journal: OrderJournal, *, start_ms: int, end_ms: int, day_key: str) -> str:
    """당일(KST) 주문별 「예약 → 체결/미체결」 표 + 한 줄 요약을 렌더한다(WAN-232).

    `진입이 너무 안 됨`을 숫자로 가르는 첫 단계 — 코호트는 그날 **예약된**(`placed_ms`)
    주문이고, 표 아래 두 줄이 (1) 예약이 체결로 가는가 (2) 체결이 진입으로 가는가를 읽는다.
    """
    lines: list[str] = [f"# 당일 주문별 체결 조회 · {day_key} (KST) — WAN-232", ""]

    summary = journal.day_summary(start_ms=start_ms, end_ms=end_ms)
    orders = journal.orders_placed_between(start_ms=start_ms, end_ms=end_ms)

    lines.append(
        f"**예약 {summary.reserved} · 체결 {summary.filled} ·"
        f" 미체결(no_fill) {summary.no_fill} · 재시작폐기 {summary.discarded_restart} ·"
        f" 필터제외 {summary.skipped}**"
    )
    extra: list[str] = []
    if summary.reentry_rearmed:
        # 재무장은 탭이 아니다(WAN-305) — 예약에는 들되 별도 계수로 병기한다.
        extra.append(f"재진입 재무장 {summary.reentry_rearmed}")
    if summary.deviation:
        extra.append(f"밴드기각 {summary.deviation}")
    if summary.pending:
        extra.append(f"대기 {summary.pending}")
    if summary.invalidated:
        extra.append(f"무효화 {summary.invalidated}")
    if summary.condition_failed:
        extra.append(f"조건취소 {summary.condition_failed}")
    if extra:
        lines.append(f"(그 밖 — {' · '.join(extra)})")
    lines.append(
        f"예약→체결(닿음) {_fmt_rate(summary.fill_rate)} ·"
        f" 체결→진입 {_fmt_rate(summary.entry_rate)}"
        f" (진입 {summary.entered} · 거부 {summary.entry_rejected}"
        f" · 미기록 {summary.entry_unrecorded})"
    )
    lines.append("")

    if not orders:
        lines.append("이 날 예약된 주문이 없습니다.")
        return "\n".join(lines)

    lines.append(
        f"| 심볼 | TF | 방향 | 구분 | 예약({KST_LABEL}) | 지정가 | 상태 | 진입결과 | 필터사유 |"
        f" 체결({KST_LABEL}) |"
    )
    lines.append("| -- | -- | -- | -- | -- | --: | -- | -- | -- | -- |")
    for order in orders:
        limit = "-" if order.limit_price is None else f"{order.limit_price:.8g}"
        fill = "-" if order.fill_ms is None else format_kst(order.fill_ms)
        skip = order.skip_reason or "-"
        lines.append(
            f"| {order.symbol} | {order.timeframe} | {order.direction} | {_origin_label(order)} |"
            f" {format_kst(order.placed_ms)} | {limit} | {_status_label(order)} |"
            f" {_entry_cell(order)} | {skip} | {fill} |"
        )
    lines.append("")
    lines.append(
        "1. **예약이 체결로 가는가** — 미체결(no_fill)이 대부분이면 지정가에 가격이 안 와"
        " 만료된 것(시장·필터 탓, 정상 방향). 예약 대비 체결이 정상인데도 적으면 그냥 드문 시기다."
    )
    lines.append(
        "2. **체결이 진입으로 가는가** — 체결인데 진입결과가 거부(대부분 손절폭 가드 0.3%,"
        " WAN-79)가 많으면 진입 손실의 원천은 시장이 아니라 집행 가드다."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-45/232 지정가 체결률·당일 조회")
    parser.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    parser.add_argument(
        "--day",
        nargs="?",
        const="today",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "당일(KST) 주문별 「예약→체결」 표 + 한 줄 요약(WAN-232). 인자 없이 `--day`면"
            " 오늘, 날짜를 주면 그 날. 생략하면 예전처럼 누적 체결률 표(WAN-45)를 낸다."
        ),
    )
    args = parser.parse_args()

    db_path = args.db if args.db is not None else get_settings().db_path
    journal = OrderJournal(db_path)
    try:
        if args.day is not None:
            start_ms, end_ms, day_key = resolve_day_window(args.day)
            print(render_day_report(journal, start_ms=start_ms, end_ms=end_ms, day_key=day_key))
        else:
            print(render_report(journal))
    finally:
        journal.close()


if __name__ == "__main__":
    main()
