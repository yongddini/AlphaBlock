"""페이퍼 성과 주간/일간 다이제스트 텍스트 (WAN-36).

WAN-33의 성과 집계(`paper.performance`)와 패리티 결과(`paper.parity`)를 재사용해, 한
기간의 페이퍼 거래 성과를 폰으로 받아볼 짧은 마크다운 요약으로 만든다. 여기에는
**순수 함수만** 둔다(파일·네트워크·DB I/O 없음) — 합성 성과 객체로 문자열을 그대로
검증할 수 있게 하기 위해서다. 저장소 조회·텔레그램 발송 배선은
`scripts/paper_digest.py`가 담당한다.

다이제스트에 담기는 것:

* 기간 라벨(UTC 날짜 범위)
* 전체 거래 수·승률·순손익률(%)·합계 R·MDD(%)
* 심볼·TF 시리즈별 상위/하위(순손익률 기준)
* 백테스트 패리티에서 `flagged`된(불일치가 큰) 시리즈 요약
* 거래가 0건이면 "거래 없음 + 러너 상태 한 줄"로 짧게(무음 실패 방지)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from paper.performance import PaperPerformance, SeriesPerformance

#: 다이제스트 메시지의 공통 헤더.
HEADER = "📊 *페이퍼 성과 다이제스트*"

#: 상위/하위 시리즈로 보여줄 기본 개수.
DEFAULT_TOP_N = 3


def _fmt_day(ms: int) -> str:
    """벽시계 ms를 UTC 날짜(YYYY-MM-DD)로."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def format_period_label(since_ms: int | None, until_ms: int | None) -> str:
    """집계 기간을 사람이 읽는 라벨로. 양쪽이 없으면 '전체 기간'."""
    if since_ms is None and until_ms is None:
        return "전체 기간"
    start = _fmt_day(since_ms) if since_ms is not None else "처음"
    end = _fmt_day(until_ms) if until_ms is not None else "현재"
    return f"{start} ~ {end} UTC"


def _pnl_emoji(value: float) -> str:
    return "📈" if value >= 0.0 else "📉"


def _series_label(series: SeriesPerformance) -> str:
    return f"{series.symbol} {series.timeframe}"


def _series_line(series: SeriesPerformance) -> str:
    m = series.metrics
    return f"• `{_series_label(series)}` `{m.total_return_pct:+.2f}%` ({m.num_trades}건)"


def _rank_series(perf: PaperPerformance) -> list[SeriesPerformance]:
    """순손익률 내림차순으로 시리즈를 정렬한다(동률은 심볼·TF 오름차순으로 결정적)."""
    return sorted(
        perf.by_series,
        key=lambda s: (-s.metrics.total_return_pct, s.symbol, s.timeframe),
    )


def _split_top_bottom(
    ranked: Sequence[SeriesPerformance], top_n: int
) -> tuple[list[SeriesPerformance], list[SeriesPerformance]]:
    """정렬된 시리즈를 상위 N과 하위 N(겹치지 않게, 최악 먼저)으로 나눈다."""
    top = list(ranked[:top_n])
    tail = ranked[len(top) :]
    bottom = list(reversed(tail[-top_n:])) if tail else []
    return top, bottom


def _empty_digest(period_label: str, runner_line: str | None) -> str:
    """거래가 0건인 기간의 짧은 다이제스트(무음 실패 방지)."""
    lines = [
        HEADER,
        f"기간: `{period_label}`",
        "",
        "기간 내 청산된 페이퍼 거래가 없습니다.",
    ]
    if runner_line:
        lines.append(runner_line)
    return "\n".join(lines)


def build_digest(
    performance: PaperPerformance,
    *,
    period_label: str,
    parity_flagged: Sequence[tuple[str, str]] | None = None,
    runner_line: str | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> str:
    """성과 집계를 텔레그램용 마크다운 다이제스트 문자열로 만든다.

    `parity_flagged`는 3상태다: ``None``=패리티 미실행, ``[]``=실행했고 불일치 없음,
    ``[(symbol, timeframe), ...]``=불일치가 큰 시리즈. `runner_line`은 거래가 0건일 때
    붙일 러너 상태 한 줄이며, 거래가 있으면 무시한다.
    """
    overall = performance.overall
    if overall.num_trades == 0:
        return _empty_digest(period_label, runner_line)

    lines = [
        HEADER,
        f"기간: `{period_label}`",
        "",
        f"거래 *{overall.num_trades}*건 · 승률 *{overall.win_rate * 100:.1f}%*",
        (
            f"순손익: {_pnl_emoji(overall.total_return_pct)} "
            f"`{overall.total_return_pct:+.2f}%` · 합계 R `{overall.total_r:+.2f}`"
        ),
        f"MDD: `{overall.max_drawdown_pct:.2f}%`",
    ]

    # 시리즈가 둘 이상일 때만 상·하위 분해를 보여준다(하나면 전체와 동일).
    ranked = _rank_series(performance)
    if len(ranked) >= 2:
        top, bottom = _split_top_bottom(ranked, top_n)
        lines.append("")
        lines.append("*상위 시리즈*")
        lines.extend(_series_line(s) for s in top)
        if bottom:
            lines.append("*하위 시리즈*")
            lines.extend(_series_line(s) for s in bottom)

    if parity_flagged is not None:
        lines.append("")
        if parity_flagged:
            labels = ", ".join(f"`{sym} {tf}`" for sym, tf in parity_flagged)
            lines.append(f"⚠️ *백테스트 패리티 불일치*: {labels}")
        else:
            lines.append("✅ 백테스트 패리티 정상")

    return "\n".join(lines)
