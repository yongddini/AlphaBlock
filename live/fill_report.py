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

from common.timefmt import KST_LABEL, format_kst
from config.settings import get_settings
from live.order_journal import MARGINAL_FILL_BPS, OrderJournal


def _fmt_ms(ms: int) -> str:
    """표 안의 시각(KST, WAN-172). 열 이름이 시간대를 밝히므로 표기는 생략한다."""
    return format_kst(ms)


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


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


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-45 지정가 체결률 실측 요약")
    parser.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    args = parser.parse_args()

    db_path = args.db if args.db is not None else get_settings().db_path
    journal = OrderJournal(db_path)
    try:
        print(render_report(journal))
    finally:
        journal.close()


if __name__ == "__main__":
    main()
