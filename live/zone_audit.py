"""짝 없는 라이브 셋업을 **백테의 존 대장**과 대조한다 — 「존 없음」의 갈래를 가른다 (WAN-343 §2).

## 왜 한 겹 더 필요한가

WAN-337의 `live.unpaired_setups`는 짝 없는 셋업을 조인 키 조각으로 갈라 `(a) 존 없음`을
냈고, WAN-342 서버 실측에서 그 부류가 두 날 모두 **13/17(76.5%)** 로 압도했다. 그런데
`(a)`는 **관찰**이지 원인이 아니다 — 그 부류가 재는 것은 「같은 칸의 백테 **셋업 행**에 그
존 시작이 없다」이고, 백테 셋업 행이 없는 이유는 최소 넷이다:

1. 백테가 그 존을 **아예 만든 적이 없다**(탐지 생성 축).
2. 만들었지만 **워밍업 창 밖**이라 대장에 없다(창 축).
3. 만들었고 창 안인데 라이브 탭 시각 **전에 이미 무효화**했다 — 무효화된 존은 그 뒤 탭을
   시그널로 내지 않으므로 셋업 행이 안 생긴다(무효화 시점 축).
4. 존이 살아 있는데 그 봉에서 **탭을 기록하지 않았다**(탭 판정 축).

**넷은 원인도 후속도 완전히 다르다.** 3번은 이 저장소가 이미 문서화한 **알려진 근사**다 —
`live.limit_engine.on_htf_bars`가 *"백테스트는 `break_time`(무효화 봉 시작)에 취소하지만
라이브는 그 봉이 닫혀 탐지가 확인된 지금이다 — 한 봉 늦는 알려진 근사"* 라고 적어 뒀다.
1번은 그렇지 않다(엔진 파리티 결함). 가르지 않으면 **알려진 근사를 결함으로, 결함을 근사로**
읽는다.

## 무엇을 보는가 — 셋업 행이 아니라 **존 아카이브**

`OrderBlockResult.order_blocks`는 생성된 모든 존의 **전체 생애주기 아카이브**다(트리밍·삭제
없음, WAN-47) — `break_time`·`swept_time`·`tapped_times`를 들고 있다. 셋업 행은 그 아카이브를
소비해 **탭 하나**를 낸 결과물이라, 존이 있었는지조차 셋업 행으로는 알 수 없다. 그래서 이
계층은 아카이브를 직접 본다.

📌 **감사 대상은 라이브 쪽 짝 없는 셋업뿐이다.** 백테만 있는 행을 같은 자로 재려면 **라이브의
존 대장**이 필요한데 러너는 그 대장을 영속화하지 않는다(재파생 가능한 값이라 스냅샷에서 뺐다,
WAN-306). 지어내지 않고 `대상 아님`으로 남긴다(WAN-194 원칙).

## 성격

**순수 함수다**(DB·화면 없이 테스트된다). 엔진·전략·기본값·토대를 건드리지 않는다 — 존폭
필터(1.28)는 WAN-159, 손절폭 가드(0.3%)는 WAN-76/79 소관이고 재-베이스라인 = 사용자 결정이다.
전부 페이퍼이고 `ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

from common.timefmt import KST_LABEL, format_kst
from data.models import timeframe_to_ms
from live.unpaired_setups import BUCKET_NO_KEY, UnpairedSetup
from live.zone_facts import CellZoneFacts, ZoneFact

__all__ = [
    "REASONS",
    "REASON_CONFIRM_DIFFERS",
    "REASON_INVALIDATED",
    "REASON_NOT_APPLICABLE",
    "REASON_NOT_DETECTED",
    "REASON_NO_TAP",
    "REASON_SWEPT",
    "REASON_UNEXPLAINED",
    "REASON_WINDOW",
    "CellZoneFacts",
    "ZoneAuditReport",
    "ZoneFact",
    "ZoneVerdict",
    "audit_unpaired",
    "render_zone_audit",
]

#: 감사 대상이 아니다 — 존 정체성이 없는 행(부류 (0)) · 백테 쪽 행 · 그 칸의 존 대장 미제공.
REASON_NOT_APPLICABLE = "대상 아님"
#: 존 시작이 백테 **탐지 창보다 이르다** — 백테가 그 존을 만들 기회조차 없었다.
REASON_WINDOW = "창 밖"
#: 창 안인데 그 존 시작을 가진 존이 아카이브에 **없다** — 탐지 생성 축(엔진 파리티).
REASON_NOT_DETECTED = "존 미탐지"
#: 존 시작은 같은데 **확정 시각**이 다르다 — 탐지 로직·봉 경계.
REASON_CONFIRM_DIFFERS = "확정 시각"
#: 존은 있고 라이브 탭 시각 **전에(또는 그 봉에) 무효화**됐다 — 무효화 한 봉 지연(알려진 근사).
REASON_INVALIDATED = "무효화 선행"
#: 존이 라이브 탭 전에 **소멸**(swept)했다.
REASON_SWEPT = "소멸 선행"
#: 존이 살아 있는데 백테 아카이브가 그 봉의 **탭을 기록하지 않았다** — 탭 판정 축.
REASON_NO_TAP = "탭 기록 없음"
#: 존도 있고 탭도 있는데 셋업 행이 없다 — 위 어느 것으로도 설명되지 않는다.
REASON_UNEXPLAINED = "설명 안 됨"

#: 표시·집계 순서. 새 사유를 넣으면 여기에도 넣어야 요약에 나온다.
REASONS: tuple[str, ...] = (
    REASON_WINDOW,
    REASON_NOT_DETECTED,
    REASON_CONFIRM_DIFFERS,
    REASON_INVALIDATED,
    REASON_SWEPT,
    REASON_NO_TAP,
    REASON_UNEXPLAINED,
    REASON_NOT_APPLICABLE,
)

_Cell = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ZoneVerdict:
    """짝 없는 라이브 셋업 하나의 감사 결과."""

    setup: UnpairedSetup
    reason: str
    detail: str = ""
    """사람이 읽는 근거 한 조각(무효화 시각·최근접 존 등). 없으면 빈 문자열."""
    invalidation_lag_bars: float | None = None
    """`무효화 선행` 전용 — 라이브 탭 봉 − 무효화 봉(상위TF 봉 수). 0이면 **같은 봉**이고,
    그것이 곧 `on_htf_bars`가 적어 둔 「한 봉 늦는 알려진 근사」의 서명이다."""


def _tap_ref_ms(setup: UnpairedSetup) -> int | None:
    """라이브 셋업의 **탭 시각 기준**. 예약 시각이 곧 탭이고, 없으면 체결/존확정 순으로 민다.

    ⚠️ `UnpairedSetup`은 `focus_ms`(체결 → 예약 → 존확정)만 들고 있다. 체결이 예약보다 늦을
    수 있으므로 이 값은 **탭 시각의 상한**이고, 감사는 그 봉 단위로만 판정한다(초 단위 비교를
    하지 않는다).
    """
    return setup.focus_ms


def _bar_of(time_ms: int, timeframe: str) -> int:
    return (time_ms // timeframe_to_ms(timeframe)) * timeframe_to_ms(timeframe)


def _audit_one(setup: UnpairedSetup, facts: CellZoneFacts | None) -> ZoneVerdict:
    """짝 없는 라이브 셋업 하나를 그 칸의 백테 존 대장과 대조한다 (순수 함수).

    판정 순서가 곧 우선순위다: 창 → 탐지 → 확정 → 무효화/소멸 → 탭 → 설명 안 됨. 앞의 것이
    성립하면 뒤는 묻지 않는다(창 밖 존의 「무효화 시각」은 존재하지 않는다).
    """
    if setup.bucket == BUCKET_NO_KEY or setup.zone_start_time is None:
        return ZoneVerdict(setup, REASON_NOT_APPLICABLE, "존 정체성 없음(부류 (0))")
    if facts is None:
        return ZoneVerdict(setup, REASON_NOT_APPLICABLE, "그 칸의 백테 존 대장 미제공")

    start = setup.zone_start_time
    if start < facts.window_start_ms:
        bars = (facts.window_start_ms - start) / timeframe_to_ms(setup.timeframe)
        return ZoneVerdict(setup, REASON_WINDOW, f"창 시작보다 {bars:.1f}봉 이르다")

    same_dir = [z for z in facts.zones if z.is_long == setup.is_long]
    same_start = [z for z in same_dir if z.start_time == start]
    if not same_start:
        deltas = [abs(z.start_time - start) for z in same_dir]
        if not deltas:
            return ZoneVerdict(setup, REASON_NOT_DETECTED, "그 방향 존이 대장에 하나도 없다")
        near = min(deltas) / timeframe_to_ms(setup.timeframe)
        return ZoneVerdict(setup, REASON_NOT_DETECTED, f"최근접 존 시작 {near:.1f}봉")

    confirmed = setup.zone_confirmed_time or 0
    exact = [z for z in same_start if z.confirmed_time == setup.zone_confirmed_time]
    if not exact:
        nearest = min(abs(z.confirmed_time - confirmed) for z in same_start)
        bars = nearest / timeframe_to_ms(setup.timeframe)
        return ZoneVerdict(setup, REASON_CONFIRM_DIFFERS, f"확정 차이 {bars:.1f}봉")

    tap_ms = _tap_ref_ms(setup)
    if tap_ms is None:
        return ZoneVerdict(setup, REASON_NOT_APPLICABLE, "라이브 탭 시각 미상")
    tap_bar = _bar_of(tap_ms, setup.timeframe)
    tf_ms = timeframe_to_ms(setup.timeframe)

    # 존이 여럿일 수 있다(같은 시작·확정을 가진 존은 사실상 하나지만 계약으로 강제하지
    # 않는다) — **가장 오래 산 존**으로 판정한다. 짧게 산 쪽으로 판정하면 「무효화 선행」이
    # 과대 계상된다.
    zone = max(exact, key=lambda z: (z.break_time is None, z.break_time or 0))
    if zone.break_time is not None and zone.break_time <= tap_bar:
        lag = (tap_bar - zone.break_time) / tf_ms
        return ZoneVerdict(
            setup,
            REASON_INVALIDATED,
            f"백테 무효화 {format_kst(zone.break_time)} · 라이브 탭 봉과 {lag:.0f}봉 차이",
            invalidation_lag_bars=lag,
        )
    if zone.swept_time is not None and zone.swept_time <= tap_bar:
        return ZoneVerdict(setup, REASON_SWEPT, f"백테 소멸 {format_kst(zone.swept_time)}")
    if tap_bar not in zone.tapped_times:
        taps = len(zone.tapped_times)
        return ZoneVerdict(setup, REASON_NO_TAP, f"존 생존 · 아카이브 탭 {taps}건에 그 봉 없음")
    return ZoneVerdict(setup, REASON_UNEXPLAINED, "존 생존 · 그 봉 탭 기록 있음")


@dataclass(frozen=True, slots=True)
class ZoneAuditReport:
    """짝 없는 라이브 셋업의 존 대장 감사 — 행 + 사유별 집계 + 판정 한 줄."""

    verdicts: tuple[ZoneVerdict, ...]

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(REASONS, 0)
        for one in self.verdicts:
            out[one.reason] = out.get(one.reason, 0) + 1
        return out

    @property
    def audited(self) -> int:
        """실제로 판정된 건수(`대상 아님` 제외) — 비율의 분모다."""
        return sum(1 for v in self.verdicts if v.reason != REASON_NOT_APPLICABLE)

    @property
    def same_bar_invalidations(self) -> int:
        """`무효화 선행` 중 **라이브 탭 봉과 같은 봉**에서 무효화된 건수.

        `on_htf_bars`가 적어 둔 「한 봉 늦는 알려진 근사」의 직접 서명이다 — 백테는 그 봉
        시작에 존을 죽이는데 라이브는 그 봉이 닫혀야 안다.
        """
        return sum(1 for v in self.verdicts if v.invalidation_lag_bars == 0.0)

    #: 사유별 후속 — 「이 사유가 과반이면 무엇을 하는가」. 판정 문장이 여기서만 나온다.
    _FOLLOW_UP: ClassVar[dict[str, str]] = {
        REASON_INVALIDATED: (
            "무효화 시점이 갈립니다 — `limit_engine.on_htf_bars`가 적어 둔 **알려진 근사**"
            "(라이브는 무효화를 한 봉 늦게 안다)입니다. 크기를 기록하고 CLAUDE.md 문단으로"
            " 닫습니다(결함 아님)."
        ),
        REASON_NOT_DETECTED: (
            "탐지 **생성**이 갈립니다 — 같은 창 안인데 백테가 그 존을 만든 적이 없습니다."
            " 알려진 근사가 아니라 **엔진 파리티 결함**이라 별도 이슈로 뺍니다."
        ),
        REASON_WINDOW: (
            "백테 **탐지 창**이 짧아 그 존을 만들 기회가 없었습니다 — 대조 도구의 워밍업"
            " 근사(`--warmup-days`)이지 엔진 결함이 아닙니다."
        ),
        REASON_CONFIRM_DIFFERS: (
            "탐지 **로직**·봉 경계가 갈립니다 — 엔진 파리티 결함이라 별도 이슈로 뺍니다."
        ),
        REASON_SWEPT: "존이 백테에서 이미 소멸했습니다 — 무효화 축과 같은 갈래로 읽습니다.",
        REASON_NO_TAP: (
            "존은 양쪽에 있는데 **탭 판정**이 갈립니다 — 틱 대 1분봉 해상도(WAN-256)일 수"
            " 있으나 탭은 1분봉 저가가 봉내 저점을 담으므로 그대로 적용되지 않습니다. 재세요."
        ),
        REASON_UNEXPLAINED: (
            "존도 탭도 있는데 셋업 행이 없습니다 — 이 감사가 못 가르는 자리입니다"
            "(진입 후보 생성·필터 단계를 직접 보세요)."
        ),
        REASON_NOT_APPLICABLE: (
            "판정된 행이 없습니다 — 존 정체성이 없거나 존 대장을 못 받았습니다."
        ),
    }

    @property
    def verdict(self) -> str:
        """「알려진 근사인가 결함인가」 한 줄 (완료 기준 2).

        🚨 **과반이 없거나 동률이면 한 사유로 닫지 않는다** — `unpaired_setups.verdict`와 같은
        규약(argmax만 보고 결론 내기 금지, WAN-161 §곡선 폭).
        """
        if not self.verdicts:
            return "감사할 짝 없는 셋업이 없습니다."
        audited = self.audited
        if audited == 0:
            return "판정된 행이 0건입니다 — 존 정체성이 없거나 존 대장을 못 받았습니다."
        counts = self.counts()
        judged = {r: counts[r] for r in REASONS if r != REASON_NOT_APPLICABLE}
        best = max(judged.values())
        leaders = [r for r in REASONS if r != REASON_NOT_APPLICABLE and judged[r] == best]
        if len(leaders) > 1 or best * 2 <= audited:
            spread = " · ".join(f"{r} {judged[r]}건" for r in REASONS if judged.get(r))
            why = "동률" if len(leaders) > 1 else f"최다도 {best}/{audited}건뿐"
            return (
                f"**과반 사유 없음({why})** — {spread}. 한 사유로 닫지 말고 각 사유의 후속을"
                " 따로 보세요(사유마다 원인·후속이 완전히 다릅니다)."
            )
        top = leaders[0]
        share = best / audited * 100.0
        return f"과반 사유 = {top}({best}/{audited}건 · {share:.1f}%) — {self._FOLLOW_UP[top]}"


def audit_unpaired(
    setups: Sequence[UnpairedSetup],
    zone_facts: Mapping[_Cell, CellZoneFacts],
    *,
    live_side: str = "라이브",
) -> ZoneAuditReport:
    """짝 없는 셋업을 칸별 백테 존 대장과 대조한다 (순수 함수, WAN-343 §2).

    `setups`는 `live.unpaired_setups.attribute_unpaired`가 낸 그 행들이어야 한다 — **다시
    분류하지 않는다**(두 벌로 갈라지면 같은 셋업이 두 블록에서 다른 부류를 얻는다, WAN-333/335
    규약). 백테 쪽 짝 없는 행은 `대상 아님`으로 남긴다(라이브 존 대장이 없어 같은 자로 잴 수
    없다 — 지어내지 않는다).
    """
    verdicts: list[ZoneVerdict] = []
    for setup in setups:
        if setup.side != live_side:
            verdicts.append(
                ZoneVerdict(setup, REASON_NOT_APPLICABLE, "백테 쪽 행(라이브 존 대장 없음)")
            )
            continue
        verdicts.append(_audit_one(setup, zone_facts.get((setup.symbol, setup.timeframe))))
    return ZoneAuditReport(verdicts=tuple(verdicts))


def render_zone_audit(report: ZoneAuditReport) -> str:
    """사람이 읽는 감사 표. 시각은 KST 고정(WAN-172)."""
    lines: list[str] = [
        "",
        f"§1 부록-2 · 짝 없는 라이브 셋업 × 백테 존 대장 — 「존 없음」의 갈래 ({KST_LABEL})",
        "-" * 72,
    ]
    if not report.verdicts:
        lines.append("  감사할 짝 없는 셋업이 없습니다.")
        return "\n".join(lines)

    lines.append(f"  {'심볼':<14}{'TF':<5}{'시각':<18}{'사유':<12}  근거")
    for one in report.verdicts:
        stamp = format_kst(one.setup.focus_ms) if one.setup.focus_ms is not None else "—"
        lines.append(
            f"  {one.setup.symbol:<14}{one.setup.timeframe:<5}{stamp:<18}{one.reason:<12}"
            f"  {one.detail}"
        )

    counts = report.counts()
    lines += ["", "  사유별 집계", "  " + "-" * 68]
    for reason in REASONS:
        if counts[reason]:
            lines.append(f"  {reason:<14}{counts[reason]:>6}건")
    lines.append(f"  {'판정 대상':<14}{report.audited:>6}건 / 전체 {len(report.verdicts)}건")
    if report.same_bar_invalidations:
        lines.append(
            f"  📌 `무효화 선행` 중 **라이브 탭과 같은 봉**: {report.same_bar_invalidations}건 —"
            " `on_htf_bars`의 「한 봉 늦는 알려진 근사」 서명입니다."
        )
    lines += [
        "",
        f"  판정: {report.verdict}",
        "",
        "  ⚠️ 백테 쪽 짝 없는 행은 `대상 아님`입니다 — 러너는 존 대장을 영속화하지 않아",
        "     (재파생 가능한 값이라 스냅샷에서 뺐습니다, WAN-306) 같은 자로 잴 수 없습니다.",
        "  ⚠️ 탭 시각 기준은 `focus_ms`(체결 → 예약 → 존확정)라 **탭 시각의 상한**입니다 —",
        "     판정은 상위TF **봉 단위**로만 하고 초 단위 비교를 하지 않습니다.",
    ]
    return "\n".join(lines)
