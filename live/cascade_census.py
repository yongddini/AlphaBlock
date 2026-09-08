"""WAN-409 §3 — 라이브(페이퍼) 장부의 「같은 존 연속 체결·연속 손절」 인구조사.

백테스트 채택 북에서 **직전 거래가 손절이고 같은 존인** 거래가 손절률 97.4%로 무더기를
이룬다(WAN-409 §1). 이 이슈의 **진짜 질문**은 「필터를 켤까」가 아니라 ***「라이브 러너도 이걸
하는가」*** 다 — 라이브는 무효화를 **한 봉 늦게** 알므로(WAN-353이 서버 실측으로 판정: 짝 없는
라이브 셋업의 82.1%가 그 근사이고 「무효화 봉 = 라이브 탭 봉」이 23/23) 같은 무더기가 나야 하고,
**안 난다면 백테만의 현상**이라 고칠 대상은 필터가 아니라 파리티다.

## 판정은 한 곳에서만 한다

부류를 매기는 술어는 `backtest.wan409_invalidation_cascade.classify` **그 함수**다. 라이브
쪽에 자기 판정을 새로 쓰면 두 표가 서로 다른 자로 세면서 「라이브는 안 한다」를 낼 수 있다
(WAN-333/335가 이름 붙인 실패). 이 모듈이 하는 일은 **장부 두 벌을 그 함수의 입력으로
옮기는 것**뿐이다.

## 존 식별자 — 라이브에도 진짜가 있다

`live_limit_orders`가 `zone_start_time`·`zone_confirmed_time`을 싣는다(WAN-234/295). 백테
쪽 키(`frozenset[int]` = 탐지 아카이브 인덱스 집합)와 타입을 맞추려고 두 시각을
`frozenset({start, confirmed})`로 담는다 — 존 확정은 형성보다 **늦거나 같으므로** 그 2-집합에서
`(min, max) = (start, confirmed)`가 유일하게 복원된다(= 서로 다른 존이 같은 키를 가질 수 없다).
🚨 **두 축의 키를 서로 비교하지는 않는다** — 각 축 **안에서** 「직전 거래와 같은 존인가」만
묻는다(백테 아카이브 인덱스와 라이브 시각은 애초에 다른 이름 공간이다).

## 🚨 라이브에 **없는** 열

`entry_after_invalidation`(체결이 무효화 봉 안이었나)은 백테 관측 필드이고 라이브 장부에는
대응물이 없다. 그래서 이 표는 그 칸을 **0%로 찍지 않고 아예 내지 않는다**(안 잰 것을 0으로
적으면 「라이브는 무효화 봉에서 체결하지 않는다」는 정반대 결론이 만들어진다 — WAN-194 규약).

## 표본이 먼저다

페이퍼 장부는 2026-09-01에 리셋됐고(WAN-392) `live_limit_orders`는 `paper-reset.sh`의
`PROTECTED_TABLES`(= `ohlcv`·`funding_rate`)에 **없어 함께 비워진다**. 백테 비율(2.64%)을 그
표본에 적용하면 기대 건수가 한 자릿수라, 이 모듈은 **세기부터 하고** 문턱(`SAMPLE_GATE`) 미만
이면 판정하지 않고 「표본 부족 · 관측 계속」으로 적는다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from backtest.wan409_invalidation_cascade import (
    GROUP_CASCADE,
    ClassifiedTrade,
    TradeFact,
    classify,
)
from common import timefmt
from live.order_journal import PlacedOrder
from paper.store import PaperTradeRecord
from strategy.models import SignalExitReason

#: 이 문턱 미만이면 **판정하지 않는다**(이슈 §착수-전-확인 3 = 사용자 지시). 상수로 두는 이유는
#: 결과를 보고 선을 옮기지 못하게 하기 위해서다(WAN-388 `BITE_GATE` 관행).
SAMPLE_GATE = 10

#: 백테스트 대조군 — WAN-409 §1 원 관측(채택 북 `oos_warm` · 대리변수 `손절가` 기준).
#: ⚠️ 진짜 존 식별자로 다시 센 값은 `backtest/reports/wan409_cascade_census.csv`에 있다.
BACKTEST_CASCADE_SHARE_PCT = 2.64
BACKTEST_CASCADE_STOP_RATE_PCT = 97.4

ENTRY_STATUS_ENTERED = "entered"
ORIGIN_REENTRY = "reentry"


@dataclass(frozen=True)
class LiveCascadeReport:
    """라이브 인구조사 한 판 — 판정은 `verdict()`가 문장으로 낸다."""

    num_orders: int
    """창 안의 지정가 주문 행 수(체결·미체결 전부)."""
    num_entered: int
    """그중 실제로 포지션이 열린 것(`entry_status='entered'`)."""
    num_joined: int
    """페이퍼 라운드트립과 짝이 지어져 **결과를 아는** 거래."""
    num_classified: int
    """칸 안에 직전 거래가 있어 부류를 매길 수 있었던 거래(= 분모)."""
    num_cascade: int
    num_missing_r: int
    """무더기 중 `r_multiple`이 비어 R을 못 세는 거래 수 — 0이 아니면 R 열은 그만큼 덜 본다."""
    cascade_stop_rate: float
    cascade_mean_r: float
    same_minute_share: float
    reentry_share: float

    @property
    def share_of_classified(self) -> float:
        return self.num_cascade / self.num_classified * 100.0 if self.num_classified else 0.0

    @property
    def has_sample(self) -> bool:
        return self.num_cascade >= SAMPLE_GATE


def _is_stop(record: PaperTradeRecord) -> bool:
    return record.reason is SignalExitReason.STOP_LOSS


def _zone_key(order: PlacedOrder) -> frozenset[int] | None:
    """라이브 존 식별자 — 둘 중 하나라도 없으면 **모른다**(같다고도 다르다고도 하지 않는다)."""
    if order.zone_start_time is None or order.zone_confirmed_time is None:
        return None
    return frozenset({order.zone_start_time, order.zone_confirmed_time})


def live_trade_facts(
    orders: Sequence[PlacedOrder], records: Sequence[PaperTradeRecord]
) -> list[TradeFact]:
    """장부 두 벌을 백테스트와 **같은 분류 입력**으로 옮긴다.

    조인은 `live.trade_timeline`이 이미 쓰는 그 키다 — 주문의 체결 시각(`fill_ms`)과 페이퍼
    라운드트립의 오픈 시각(`entry_time`)이 같은 사건이므로 `(심볼, TF, fill_ms == entry_time)`
    로 붙인다. 짝이 없으면 **결과를 모르는 거래**라 표에서 빠진다(지어내지 않는다).
    """
    roundtrips: dict[tuple[str, str, int], PaperTradeRecord] = {
        (r.symbol, r.timeframe, r.entry_time): r for r in records
    }
    facts: list[TradeFact] = []
    for order in orders:
        if order.entry_status != ENTRY_STATUS_ENTERED or order.fill_ms is None:
            continue
        matched = roundtrips.get((order.symbol, order.timeframe, order.fill_ms))
        if matched is None:
            continue
        facts.append(
            TradeFact(
                cell=(order.symbol, order.timeframe),
                entry_time=order.fill_ms,
                exit_time=matched.exit_time,
                is_stop=_is_stop(matched),
                # WAN-393 §2: 페이퍼 `r_multiple` ↔ 백테 `net R`이 **맞대도 되는 두 자**다
                # (`realized_pnl ÷ risk_amount`는 다른 자라 쓰지 않는다).
                # 🚨 값이 없으면 `NaN`이다 — 0으로 채우면 그 거래가 「본전」으로 평균에 섞인다.
                # 거래 자체는 **사슬에 남긴다**(빼면 「직전 거래」가 딴 것으로 바뀐다).
                net_r=matched.r_multiple if matched.r_multiple is not None else float("nan"),
                zone_key=_zone_key(order),
                stop_price=order.stop_price if order.stop_price is not None else float("nan"),
                # 🚨 라이브에는 대응물이 없다 — 아래 렌더러가 이 칸을 **찍지 않는다**.
                entry_after_invalidation=False,
                is_reentry=order.origin == ORIGIN_REENTRY,
            )
        )
    return facts


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def build_report(
    orders: Sequence[PlacedOrder], records: Sequence[PaperTradeRecord]
) -> LiveCascadeReport:
    facts = live_trade_facts(orders, records)
    classified: list[ClassifiedTrade] = classify(facts)
    cascade = [c for c in classified if c.group_real == GROUP_CASCADE]
    return LiveCascadeReport(
        num_orders=len(orders),
        num_entered=sum(
            1 for o in orders if o.entry_status == ENTRY_STATUS_ENTERED and o.fill_ms is not None
        ),
        num_joined=len(facts),
        num_classified=len(classified),
        num_cascade=len(cascade),
        num_missing_r=sum(1 for c in cascade if not math.isfinite(c.fact.net_r)),
        cascade_stop_rate=_mean([100.0 if c.fact.is_stop else 0.0 for c in cascade]),
        cascade_mean_r=_mean([c.fact.net_r for c in cascade if math.isfinite(c.fact.net_r)]),
        same_minute_share=_mean([100.0 if c.gap_minutes <= 0.0 else 0.0 for c in cascade]),
        reentry_share=_mean([100.0 if c.fact.is_reentry else 0.0 for c in cascade]),
    )


def render(
    orders: Sequence[PlacedOrder], records: Sequence[PaperTradeRecord], *, label: str
) -> str:
    """사람이 읽는 §3 — 🚨 **세기가 먼저이고 판정은 문턱을 넘어야 한다.**"""
    report = build_report(orders, records)
    lines = [
        f"■ 라이브 「같은 존 연속 체결」 인구조사 (WAN-409 §3) — {label}",
        "",
        f"주문 {report.num_orders:,}행 · 진입 {report.num_entered:,} · 결과 확인(짝 지어짐) "
        f"{report.num_joined:,} · 분류 가능 {report.num_classified:,}",
    ]
    if report.num_joined == 0:
        lines += [
            "",
            "장부가 비어 있습니다 — 이 표는 **서버 장부에서만** 뜻이 있습니다(러너는 서버에서"
            " 돕니다). 로컬 DB로는 판정하지 않습니다.",
        ]
        return "\n".join(lines)

    lines += [
        "",
        f"무더기(직전 손절 · 같은 존): **{report.num_cascade:,}건**"
        f" ({report.share_of_classified:.2f}%)",
        f"  · 손절률 {report.cascade_stop_rate:.1f}%"
        f" · 거래당 R {report.cascade_mean_r:+.4f}"
        f" · 같은 1분 {report.same_minute_share:.1f}%"
        f" · 재진입 몫 {report.reentry_share:.1f}%",
        "",
        *(
            [
                f"  ⚠️ 그중 {report.num_missing_r:,}건은 `r_multiple`이 비어 R 평균에서 빠졌다"
                " (지어내지 않는다 — WAN-194)."
            ]
            if report.num_missing_r
            else []
        ),
        f"백테스트 대조군(채택 북 `oos_warm`): 비중 {BACKTEST_CASCADE_SHARE_PCT:.2f}%"
        f" · 손절률 {BACKTEST_CASCADE_STOP_RATE_PCT:.1f}%",
        "",
    ]
    lines += _verdict_lines(report)
    lines += [
        "",
        "⚠️ 이 표에 **「무효화 봉 안 체결」 칸이 없다** — 라이브 장부에 대응 필드가 없어서이고,"
        " 안 잰 것을 0%로 적지 않는다(WAN-194).",
        "⚠️ 페이퍼 `r_multiple` ↔ 백테 `net R`만 맞댄다(WAN-393 §2 — 이름이 비슷한 셋째 자가 있다).",
    ]
    return "\n".join(lines)


def _verdict_lines(report: LiveCascadeReport) -> list[str]:
    """판정 문장은 **코드가 낸다** — 사람이 표를 보고 정하지 않는다(WAN-388 관행)."""
    if not report.has_sample:
        return [
            f"📌 **판정하지 않는다 — 표본 부족**(무더기 {report.num_cascade:,}건 <"
            f" 문턱 {SAMPLE_GATE}건).",
            "페이퍼 장부는 2026-09-01 리셋 이후분뿐이고 `live_limit_orders`도 그때 함께 비워졌다"
            "(`paper-reset.sh`의 보호 대상은 시세뿐). 표본이 쌓이면 다시 센다 —"
            " **「라이브는 안 한다」로 읽지 말 것**(안 났다와 못 봤다는 다르다, WAN-367).",
        ]
    ratio = (
        report.share_of_classified / BACKTEST_CASCADE_SHARE_PCT
        if BACKTEST_CASCADE_SHARE_PCT
        else 0.0
    )
    same_order = 0.1 <= ratio <= 10.0
    verdict = "**(가) 라이브도 한다**" if same_order else "**(나) 라이브는 (거의) 안 한다**"
    return [
        f"📌 {verdict} — 라이브 비중 {report.share_of_classified:.2f}%는 백테"
        f" {BACKTEST_CASCADE_SHARE_PCT:.2f}%의 {ratio:.2f}배다"
        f" (같은 자릿수 = 0.1~10배 규약).",
        "🚨 그래도 이 한 줄로 필터를 켜지 않는다 — 무더기를 지우면 공유 자본·슬롯이 재배치돼"
        " 다른 숫자가 나오고(WAN-316), 전환은 **재-베이스라인 = 사용자 결정**이다.",
    ]


def window_label(start_ms: int | None, end_ms: int | None) -> str:
    """창 라벨 — 시각은 KST로 찍는다(WAN-172)."""
    if start_ms is None or end_ms is None:
        return "장부 전수"
    return f"{timefmt.format_kst(start_ms)} ~ {timefmt.format_kst(end_ms)} KST"
