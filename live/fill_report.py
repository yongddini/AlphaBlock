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
            f" 대기중앙값 | 스침%(<{MARGINAL_FILL_BPS:g}bp) |"
        )
        lines.append("| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
        for s in stats:
            wait = "-" if s.median_wait_ms is None else f"{s.median_wait_ms / 60000:.0f}분"
            lines.append(
                f"| {s.symbol} | {s.timeframe} | {s.placed} | {s.pending} | {s.filled} |"
                f" {s.cancelled_expired} | {s.cancelled_invalidated} |"
                f" {s.cancelled_condition_failed} | {s.discarded_restart} |"
                f" {_fmt_rate(s.fill_rate)} | {wait} | {_fmt_rate(s.marginal_fill_share)} |"
            )
        lines.append("")
        lines.append(
            "체결률의 백테스트 대응값은 `baseline` 렌즈 체결률"
            "(`wan95_zone_limit_summary.md`, 낙관 상한)이다. 스침% 비중이 크면 그 상한이"
            " 그만큼 부풀려져 있다는 실측 증거다."
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
