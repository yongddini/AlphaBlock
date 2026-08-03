"""진입/미진입 사유 장부 조회의 표시 계층 (WAN-219).

`live.order_journal`이 적재한 진입 깔때기 행(`OrderJournal.ledger_entries`)을 **계산 없이
조회**해 체결률·미진입 사유 분포·진입/미진입 목록으로 바꾼다. WAN-217이 데이터를 넣고
이 층은 라벨만 바꿔 그린다(저장된 거래 탭 WAN-106/199과 같은 원칙 — 화면에서 재계산 금지).

Streamlit에 의존하지 않는다 — 집계·필터·표 변환이 전부 순수 함수라 화면 없이 테스트된다
(`dashboard/saved_trades.py`와 같은 규칙). 사유 코드는 `funnel_counts`와 같은 어휘를 쓰므로,
이 목록을 집계하면 `funnel_counts`와 정확히 같은 수가 나온다(`to_funnel_counts`가 그 다리이고
회귀 테스트가 고정한다).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pandas as pd

from common.timefmt import format_kst
from execution.engine import (
    REJECT_CODE_NOTIONAL,
    REJECT_CODE_SIZING,
)
from live.order_journal import (
    LEDGER_REASON_DEVIATION,
    LEDGER_REASON_ENTERED,
    LEDGER_REASON_NO_FILL,
    LEDGER_REASON_OTHER,
    LEDGER_REASON_UNRECORDED,
    SKIP_REASON_CELL_BUSY,
    SKIP_REASON_RETAP,
    SKIP_REASON_ZONE_WIDTH,
    FunnelCounts,
    LedgerEntry,
)
from strategy.models import OrderBlockDirection

#: 필터의 "전체" 토큰. 표에 실제로 찍히는 값과 같은 문자열을 쓴다 — 두 벌로 갈라지면
#: 사유를 골랐는데 빈 표가 뜬다(WAN-106 저장된 거래 탭의 교훈).
ALL = "전체"

#: 결과 사유 코드 → 화면 라벨. 원문 코드(`zone_width`)는 화면에 그대로 나가면 안 되고,
#: 모르는 코드는 원문을 남긴다(조용히 빈칸으로 만들지 않는다 — 그래야 고칠 수 있다).
REASON_LABELS: dict[str, str] = {
    LEDGER_REASON_ENTERED: "진입",
    LEDGER_REASON_UNRECORDED: "체결(처분 미기록)",
    LEDGER_REASON_NO_FILL: "미체결(안 닿음)",
    LEDGER_REASON_DEVIATION: "밴드 기각",
    SKIP_REASON_ZONE_WIDTH: "존폭 기각",
    SKIP_REASON_CELL_BUSY: "슬롯 참",
    SKIP_REASON_RETAP: "재탭",
    REJECT_CODE_NOTIONAL: "명목 상한",
    REJECT_CODE_SIZING: "사이징 가드",
    LEDGER_REASON_OTHER: "기타 거부",
}

#: 미진입 사유 분포·필터의 표시 순서(체결 깔때기 위→아래). `cell_busy`는 스킵·거부 양쪽에서
#: 오는데 `funnel_counts`가 한 칸으로 합치므로 여기서도 한 줄이다(`REJECT_CODE_CELL_BUSY`와
#: `SKIP_REASON_CELL_BUSY`는 같은 문자열 "cell_busy").
NO_ENTRY_REASON_ORDER: tuple[str, ...] = (
    LEDGER_REASON_NO_FILL,
    LEDGER_REASON_DEVIATION,
    SKIP_REASON_ZONE_WIDTH,
    SKIP_REASON_CELL_BUSY,
    SKIP_REASON_RETAP,
    REJECT_CODE_NOTIONAL,
    REJECT_CODE_SIZING,
    LEDGER_REASON_OTHER,
)


def reason_label(code: str) -> str:
    """사유 코드 → 화면 라벨(모르는 코드는 원문 유지)."""
    return REASON_LABELS.get(code, code)


def to_funnel_counts(entries: Iterable[LedgerEntry]) -> FunnelCounts:
    """목록을 `funnel_counts`와 같은 카운트로 되접는다(조회 = 재계산 아님의 다리).

    화면의 「전체」 지표가 이 값을 쓰고, 회귀 테스트가 이것이 `OrderJournal.funnel_counts`와
    (`placed`를 뺀) 모든 필드에서 **정확히 일치**함을 고정한다 — 목록과 요약이 두 벌로
    갈라지지 않는다는 증거다. ⚠️ `placed`(헤드라인 「예약 N」, WAN-230)는 깔때기 행이 아니라
    `placed_ms` 창 귀속의 헤드라인 카운트라 목록으로 되접히지 않는다(기본값 0으로 둔다).
    """
    counts: dict[str, int] = {}
    filled = 0
    for entry in entries:
        counts[entry.reason] = counts.get(entry.reason, 0) + 1
        if entry.filled:
            filled += 1
    return FunnelCounts(
        filled=filled,
        no_fill=counts.get(LEDGER_REASON_NO_FILL, 0),
        deviation=counts.get(LEDGER_REASON_DEVIATION, 0),
        zone_width=counts.get(SKIP_REASON_ZONE_WIDTH, 0),
        cell_busy=counts.get(SKIP_REASON_CELL_BUSY, 0),
        retap=counts.get(SKIP_REASON_RETAP, 0),
        notional=counts.get(REJECT_CODE_NOTIONAL, 0),
        sizing=counts.get(REJECT_CODE_SIZING, 0),
        other=counts.get(LEDGER_REASON_OTHER, 0),
    )


def fill_rate_by_cell(entries: Iterable[LedgerEntry]) -> pd.DataFrame:
    """칸(심볼·TF)별 체결률 = 체결 ÷ (체결 + no_fill).

    ⚠️ 분모는 **닿았나 vs 안 닿았나**만 본다(`FunnelCounts.fill_rate` 정의, WAN-221) —
    무효화·조건취소는 깔때기 밖이라 목록에 없고, 스킵·거부는 분모에 안 든다. 결말 표본이
    없는 칸(체결·미체결 0)은 체결률을 `None`으로 둔다.
    """
    by_cell: dict[tuple[str, str], list[int]] = {}
    for entry in entries:
        key = (entry.symbol, entry.timeframe)
        filled, no_fill = by_cell.setdefault(key, [0, 0])
        if entry.filled:
            by_cell[key][0] = filled + 1
        elif entry.reason == LEDGER_REASON_NO_FILL:
            by_cell[key][1] = no_fill + 1

    rows: list[dict[str, object]] = []
    for (symbol, timeframe), (filled, no_fill) in sorted(by_cell.items()):
        denom = filled + no_fill
        rows.append(
            {
                "심볼": symbol,
                "TF": timeframe,
                "체결": filled,
                "미체결": no_fill,
                "체결률": "-" if denom == 0 else f"{filled / denom * 100:.1f}%",
            }
        )
    return pd.DataFrame(rows, columns=["심볼", "TF", "체결", "미체결", "체결률"])


def reason_distribution(entries: Iterable[LedgerEntry]) -> pd.DataFrame:
    """미진입 사유 분포 — 각 사유의 건수와 미진입 총계 대비 비율(WAN-219 최소 표시).

    진입(`entered`)·처분 미기록(`unrecorded`)은 미진입이 아니라 여기서 뺀다. 비율의 분모는
    미진입 총계라 사유별 비율의 합이 100%가 된다. 건수 0인 사유도 줄을 남긴다(있어야 할
    사유가 0이라는 것도 정보다) — 단 전부 0이면 호출부가 빈 안내를 낸다.
    """
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.reason in (LEDGER_REASON_ENTERED, LEDGER_REASON_UNRECORDED):
            continue
        counts[entry.reason] = counts.get(entry.reason, 0) + 1
    total = sum(counts.values())

    rows: list[dict[str, object]] = []
    for code in NO_ENTRY_REASON_ORDER:
        n = counts.get(code, 0)
        rows.append(
            {
                "사유": reason_label(code),
                "건수": n,
                "비율": "-" if total == 0 else f"{n / total * 100:.1f}%",
            }
        )
    return pd.DataFrame(rows, columns=["사유", "건수", "비율"])


def cell_options(entries: Iterable[LedgerEntry]) -> list[str]:
    """칸 필터 선택지(`전체` + "심볼 · TF" 정렬)."""
    cells = sorted({f"{e.symbol} · {e.timeframe}" for e in entries})
    return [ALL, *cells]


def reason_options(entries: Iterable[LedgerEntry]) -> list[str]:
    """사유 필터 선택지(`전체` + 목록에 실제로 있는 사유 라벨, 깔때기 순서)."""
    present = {e.reason for e in entries}
    ordered = [LEDGER_REASON_ENTERED, LEDGER_REASON_UNRECORDED, *NO_ENTRY_REASON_ORDER]
    labels = [reason_label(code) for code in ordered if code in present]
    # 순서 목록에 없는 미지의 코드도 빠뜨리지 않는다(원문 라벨로 뒤에 붙인다).
    labels += [reason_label(code) for code in sorted(present) if code not in ordered]
    return [ALL, *labels]


def filter_entries(
    entries: Iterable[LedgerEntry], *, cell: str = ALL, reason: str = ALL
) -> list[LedgerEntry]:
    """칸(심볼·TF)·사유 라벨로 목록을 좁힌다(`전체`는 통과). 기간은 조회 창에서 이미 잘렸다."""
    out = list(entries)
    if cell != ALL:
        out = [e for e in out if f"{e.symbol} · {e.timeframe}" == cell]
    if reason != ALL:
        out = [e for e in out if reason_label(e.reason) == reason]
    return out


def _direction_label(direction: str) -> str:
    """장부의 방향값(`OrderBlockDirection` — 강세 OB=롱, 약세 OB=숏) → 화면 라벨.

    장부는 `order.direction.value`(`"bull"`/`"bear"`)를 저장한다. 모르는 값은 원문을 남긴다.
    """
    return {
        OrderBlockDirection.BULLISH.value: "롱",
        OrderBlockDirection.BEARISH.value: "숏",
    }.get(direction, direction)


def _price(value: float | None) -> str:
    return "-" if value is None else f"{value:.8g}"


LEDGER_COLUMNS: tuple[str, ...] = (
    "시각(KST)",
    "심볼",
    "TF",
    "방향",
    "체결",
    "사유",
    "체결가",
    "지정가",
    "관통(bp)",
)


def ledger_frame(
    entries: Iterable[LedgerEntry], *, to_kst: Callable[[int], str] | None = None
) -> pd.DataFrame:
    """진입/미진입 목록을 사람이 읽는 표로 (WAN-219).

    `체결`은 지정가가 닿았는지(True=닿음), `사유`는 그 결과(진입/미진입 사유)다 — 둘을 함께
    두는 이유는 "닿았는데 거부"(체결됐지만 미진입)와 "안 닿음"(미체결)을 한 표에서 가르기
    위해서다. `to_kst`는 시각 포맷터(테스트·호출부 주입용)이며 주지 않으면 KST를 쓴다.
    """
    fmt = to_kst or format_kst
    rows = list(entries)
    if not rows:
        return pd.DataFrame(columns=list(LEDGER_COLUMNS))
    return pd.DataFrame(
        {
            "시각(KST)": [fmt(e.event_ms) for e in rows],
            "심볼": [e.symbol for e in rows],
            "TF": [e.timeframe for e in rows],
            "방향": [_direction_label(e.direction) for e in rows],
            "체결": ["닿음" if e.filled else "안 닿음" for e in rows],
            "사유": [reason_label(e.reason) for e in rows],
            "체결가": [_price(e.fill_price) for e in rows],
            "지정가": [_price(e.limit_price) for e in rows],
            "관통(bp)": [
                "-" if e.penetration_bps is None else f"{e.penetration_bps:.2f}" for e in rows
            ],
        }
    )
