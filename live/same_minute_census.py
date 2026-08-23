"""페이퍼 장부의 「같은 분 왕복」 인구조사 (WAN-362 §1).

## 묻는 것 — 한 문장

> 「들어간 그 1분 안에 나온 거래」가 페이퍼에서 얼마나 자주 나고, **익절인가 손절인가?**

백테스트는 채택 북 `oos_warm` 6,336건 중 **같은 분 익절 467 : 같은 분 손절 7**(67:1)인데,
사용자 서버 실측(2026-08-22)의 페이퍼 장부 32건은 **손절 5 : 익절 2**로 방향이 반대였다.
이 모듈은 그 관찰을 **날짜·종목·TF로 갈라** 다시 세고, 표본이 작으므로 신뢰구간과
「몇 건이면 판정이 서는가」를 함께 낸다.

## 정의 — 백테스트와 같은 자여야 대조가 성립한다

백테스트의 「같은 분」은 **같은 1분봉**(진입 서브스텝 == 청산 서브스텝)이다. 페이퍼 장부의
`entry_time`·`exit_time`은 **틱 시각**(ms)이므로 분으로 내림해서 같은 분 버킷인지 본다
(`minute_bucket`). 둘은 같은 자다 — 라이브 러너가 틱으로 돌아도(WAN-256) 사건이 놓이는
1분 칸은 백테스트의 1분봉과 같은 칸이다.

⚠️ **초 단위 순서는 장부에 없다** — 같은 분 안에서 진입이 먼저인지는 정의상 자명하지만
(진입 없이 청산이 없다) 그 사이 가격 경로는 모른다. 이 표는 **빈도와 구성**만 센다.

## ⚠️ 이 표가 답하지 못하는 것

* ❌ **인과** — 라이브와 백테스트가 갈리는 *기계*는 §2(`backtest.wan362_same_minute_roundtrip`)가
  잰다. 여기는 「무엇이 관측됐나」까지다.
* ❌ **독립 표본 가정** — 같은 분 왕복이 한 급락 2분에 몰렸다면 7건은 7개의 독립 시행이
  아니다. 그래서 **날짜 분해와 leave-one-day-out을 함께** 낸다(그게 이 §의 첫 질문이다).
* ❌ **큐 우선순위**(`pen_5bp` · WAN-98 Canceled)와 **틱 대 1분봉**(WAN-256)은 다른 축이다.

순수 조회다 — DB에 아무것도 쓰지 않고(WAN-194 원칙), 엔진·전략·기본값·토대 불변,
`ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from backtest.wan362_same_minute_roundtrip import (
    BACKTEST_REFERENCE,
    BacktestReference,
    Verdict,
    judge,
)
from common.timefmt import format_kst
from paper.store import PaperTradeRecord
from strategy.models import SignalExitReason

MINUTE_MS = 60_000


def minute_bucket(ms: int) -> int:
    """그 시각이 놓인 1분 칸의 시작(ms). 백테스트 서브스텝의 `open_time`과 같은 자다."""
    return (ms // MINUTE_MS) * MINUTE_MS


def is_same_minute(record: PaperTradeRecord) -> bool:
    """진입과 청산이 **같은 1분 칸**에서 났는지."""
    return minute_bucket(record.entry_time) == minute_bucket(record.exit_time)


@dataclass(frozen=True, slots=True)
class CensusRow:
    """한 묶음(전체·날짜·종목·TF)의 같은 분 왕복 인구조사 한 줄."""

    group: str
    label: str
    trades: int
    same_minute: int
    same_tp: int
    same_sl: int
    other_tp: int
    other_sl: int
    same_pnl: float
    other_pnl: float

    @property
    def same_minute_rate(self) -> float:
        return self.same_minute / self.trades if self.trades else float("nan")

    @property
    def take_profit_share(self) -> float:
        """같은 분 왕복 중 익절 비율. 같은 분이 없으면 NaN."""
        return self.same_tp / self.same_minute if self.same_minute else float("nan")


def _blank() -> dict[str, float]:
    return {
        "trades": 0.0,
        "same_minute": 0.0,
        "same_tp": 0.0,
        "same_sl": 0.0,
        "other_tp": 0.0,
        "other_sl": 0.0,
        "same_pnl": 0.0,
        "other_pnl": 0.0,
    }


def _accumulate(bucket: dict[str, float], record: PaperTradeRecord) -> None:
    same = is_same_minute(record)
    is_tp = record.reason is SignalExitReason.TAKE_PROFIT
    pnl = record.realized_pnl if record.realized_pnl is not None else record.net_pct
    bucket["trades"] += 1
    if same:
        bucket["same_minute"] += 1
        bucket["same_pnl"] += pnl
        bucket["same_tp" if is_tp else "same_sl"] += 1
    else:
        bucket["other_pnl"] += pnl
        bucket["other_tp" if is_tp else "other_sl"] += 1


def _row(group: str, label: str, bucket: dict[str, float]) -> CensusRow:
    return CensusRow(
        group=group,
        label=label,
        trades=int(bucket["trades"]),
        same_minute=int(bucket["same_minute"]),
        same_tp=int(bucket["same_tp"]),
        same_sl=int(bucket["same_sl"]),
        other_tp=int(bucket["other_tp"]),
        other_sl=int(bucket["other_sl"]),
        same_pnl=bucket["same_pnl"],
        other_pnl=bucket["other_pnl"],
    )


def day_kst(ms: int) -> str:
    """그 시각의 **KST 날짜**(WAN-172 — 사람이 읽는 출력은 전부 KST)."""
    return format_kst(ms)[:10]


def census(records: Sequence[PaperTradeRecord]) -> list[CensusRow]:
    """전체 · 날짜(KST) · 종목 · TF 묶음으로 인구조사 행을 낸다.

    ⚠️ **손익 합은 `realized_pnl`(달러)이고 없으면 `net_pct`로 접는다** — 옛 행은 달러
    금액이 없다(WAN-207 이전). 두 자가 섞이면 합이 뜻을 잃으므로 요약이 그 사실을 밝힌다.
    """
    groups: dict[tuple[str, str], dict[str, float]] = {}
    for record in records:
        keys = [
            ("전체", "전체"),
            ("날짜(KST)", day_kst(record.entry_time)),
            ("종목", record.symbol),
            ("TF", record.timeframe),
        ]
        for key in keys:
            groups.setdefault(key, _blank())
            _accumulate(groups[key], record)
    order = {"전체": 0, "날짜(KST)": 1, "종목": 2, "TF": 3}
    return [
        _row(group, label, bucket)
        for (group, label), bucket in sorted(
            groups.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])
        )
    ]


def mixed_pnl_units(records: Iterable[PaperTradeRecord]) -> bool:
    """손익 합에 **달러와 %가 섞였는지** — 섞였으면 합을 그대로 읽으면 안 된다."""
    seen = {record.realized_pnl is None for record in records}
    return len(seen) > 1


# --------------------------------------------------------------------------- #
# 판정 — 이 표본으로 갈리는가 (자·대조군은 `backtest.wan362_same_minute_roundtrip`)
# --------------------------------------------------------------------------- #


def verdicts(
    rows: Sequence[CensusRow], reference: BacktestReference = BACKTEST_REFERENCE
) -> list[Verdict]:
    """전체 행에서 두 축(빈도 · 구성)의 판정을 낸다."""
    total_row = next((row for row in rows if row.group == "전체"), None)
    if total_row is None:
        return []
    return [
        judge(
            "빈도(같은 분 왕복 / 전체 거래)",
            total_row.same_minute,
            total_row.trades,
            reference.same_minute_rate,
        ),
        judge(
            "구성(익절 / 같은 분 왕복)",
            total_row.same_tp,
            total_row.same_minute,
            reference.take_profit_share,
        ),
    ]


def leave_one_day_out(records: Sequence[PaperTradeRecord]) -> list[tuple[str, CensusRow]]:
    """날짜를 하나씩 빼고 전체 행을 다시 낸다 — **하루가 만든 결과인지**가 §1의 첫 질문이다."""
    days = sorted({day_kst(record.entry_time) for record in records})
    out: list[tuple[str, CensusRow]] = []
    for day in days:
        kept = [record for record in records if day_kst(record.entry_time) != day]
        if not kept:
            continue
        rows = census(kept)
        total_row = next((row for row in rows if row.group == "전체"), None)
        if total_row is not None:
            out.append((day, total_row))
    return out


# --------------------------------------------------------------------------- #
# 렌더 — `alphablock same-minute`
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return "—" if math.isnan(value) else f"{value * 100:.1f}%"


def render(
    records: Sequence[PaperTradeRecord],
    *,
    label: str,
    reference: BacktestReference = BACKTEST_REFERENCE,
) -> str:
    """사람이 읽는 §1 인구조사 — 표 · 판정 · leave-one-day-out."""
    lines: list[str] = [f"■ 같은 분 왕복 인구조사 (WAN-362 §1) — {label}"]
    if not records:
        lines.append("")
        lines.append("페이퍼 장부에 거래가 없습니다 — 이 표는 서버 장부에서만 뜻이 있습니다.")
        return "\n".join(lines)

    rows = census(records)
    lines.append("")
    lines.append(
        f"대조군(백테스트 채택 북 `oos_warm`, WAN-336): 거래 {reference.trades}건 · "
        f"같은 분 왕복 {reference.same_minute}건({_pct(reference.same_minute_rate)}) · "
        f"익절 {reference.same_minute_take_profit} : 손절 {reference.same_minute_stop_loss} "
        f"(익절 비중 {_pct(reference.take_profit_share)})"
    )
    if mixed_pnl_units(records):
        lines.append(
            "⚠️ 손익 합에 달러와 %가 섞여 있습니다(옛 행은 달러 금액이 없다 — WAN-207 이전). "
            "합계는 그대로 읽지 말 것."
        )
    lines.append("")
    header = (
        "묶음      라벨              거래  같은분  익절  손절   같은분%   익절비중   같은분손익"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        lines.append(
            f"{row.group:<9} {row.label:<16} {row.trades:>5} {row.same_minute:>6} "
            f"{row.same_tp:>5} {row.same_sl:>5} {_pct(row.same_minute_rate):>8} "
            f"{_pct(row.take_profit_share):>9} {row.same_pnl:>12.2f}"
        )

    lines.append("")
    lines.append("■ 판정 — 이 표본으로 갈리는가")
    for verdict in verdicts(rows, reference):
        mark = "판정 섬" if verdict.decided else "판정 안 섬"
        need = "—" if verdict.required is None else f"{verdict.required}건"
        lines.append(
            f"  · {verdict.axis}: {verdict.successes}/{verdict.total} = {_pct(verdict.rate)} "
            f"(Wilson 95% [{_pct(verdict.low)}, {_pct(verdict.high)}]) vs 기준 "
            f"{_pct(verdict.reference)} → {mark} (정확 이항 p={verdict.p_value:.2g} · "
            f"필요 표본 {need})"
        )
    lines.append(
        "  ⚠️ 독립 시행 가정이다 — 같은 분 왕복이 한 급락에 몰렸다면 유효 표본은 건수보다 "
        "작고 위 p·필요 표본은 **낙관적**이다. 아래 leave-one-day-out이 그 검사다."
    )

    loo = leave_one_day_out(records)
    if loo:
        lines.append("")
        lines.append("■ 날짜 하나씩 빼기 — 하루가 만든 결과인가")
        for day, row in loo:
            lines.append(
                f"  · −{day}: 거래 {row.trades:>4} · 같은분 {row.same_minute:>3}"
                f"({_pct(row.same_minute_rate)}) · 익절 {row.same_tp} : 손절 {row.same_sl}"
                f" (익절비중 {_pct(row.take_profit_share)})"
            )
    lines.append("")
    lines.append(
        "📌 이 표는 **무엇이 관측됐나**까지다 — 라이브와 백테스트가 갈리는 기계는 "
        "`uv run python -m backtest.wan362_same_minute_roundtrip`(§2/§3)가 잰다."
    )
    return "\n".join(lines)
