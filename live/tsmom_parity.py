"""엔진 C 페이퍼 ↔ 다시 계산 대조 (WAN-451 · 「백테는 페이퍼와 같은 선상」 WAN-305).

페이퍼 러너가 매일 원장(`tsmom_rebalances`)에 남긴 리밸런싱이, **저장된 일봉으로 같은 단일 소스
(`strategy.tsmom`)를 다시 돌렸을 때** 나오는 값과 같은지 본다. 셋업·체결이 있는 오더블록 대조
(옛 `alphablock parity`/`compare` · WAN-451 ②에서 은퇴)와 달리 엔진 C에는 「셋업」이 없다 —
그래서 하루 한 행씩 그날의 목표를 다시 내 맞대는 것이 대조의 전부다.

행마다 보는 것:

* **배율·신호** — 그날(as_of = 일봉 D)까지의 확정 일봉으로 다시 낸 값과 같은가(비트 단위).
* **체결가** — 원장 가격이 저장된 일봉 D의 종가인가.
* **목표 명목** — `정산 후 자본 × λ·k·0.5·배율`과 같은가.

그리고 자산마다:

* **건너뛴 날** — 첫 리밸런싱 이후 확정 일봉이 있는데 원장 행이 없는 날. 러너가 죽어 있던 날은
  소급 거래하지 않으므로(`live.tsmom_runner` 독스트링) 이것 자체는 **실패가 아니다** — 매일
  리밸런싱하는 백테스트와 다르게 돈 날이 언제인지 보이게 할 뿐이다.

🚨 **값이 다르면 실패다** — 같은 함수 · 같은 저장 데이터라 갈라질 이유가 없다. 갈라졌다면 (1)
일봉이 나중에 고쳐졌거나(백필 복구) (2) 러너가 다른 데이터를 봤거나 (3) 배선이 틀렸다는 뜻이다.

판정은 세 단계다(WAN-459 §3): **일치**(전부 정확히 같음) · **거의 일치**(배율만 부동소수 끝자리가
다르고 나머지는 정확히 같음 — 정보로만 찍고 종료 코드 0) · **🔴 불일치**(그 밖 전부 — 종료 코드 1).
문턱(`WEIGHT_REL_TOL`)은 **배율 하나에만** 건다. 신호·체결가·목표 명목은 예전처럼 정확히 본다 —
문턱을 핑계로 진짜 어긋남을 덮지 않는다. 계기: 서버 실측에서 ETH 2026-10-15 행이 표에는 같은
숫자(소수 6자리)로 찍히는데 「🔴 불일치」였다(배율 ✓/✗ 열도 차이도 없어 원인이 안 보였다).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from common.timefmt import format_kst
from live.tsmom_runner import DEFAULT_LOOKBACK_DAYS, BarSource
from paper.tsmom_ledger import RebalanceRow
from strategy import tsmom

DAY_MS = tsmom.DAY_MS
#: 목표 명목 대조 허용 오차(상대) — 곱셈 순서 차이만 넘긴다.
NOTIONAL_REL_TOL = 1e-9
#: 배율 「거의 일치」 문턱(상대 · WAN-459 §3). 근거: 배율은 수백 개 일봉 종가의 수익률·변동성에서
#: 나오므로, 지나간 일봉 하나가 끝자리만 고쳐져도(백필 복구) 다시 낸 값의 끝자리가 흔들린다.
#: 1e-6이면 배율 0.16 · 자본 $10,000에서 목표 명목 차이가 1센트에 한참 못 미친다(표는 소수 6자리를
#: 찍으므로 이 문턱 아래 차이는 화면에서 같은 숫자로 보인다). 신호가 뒤집히거나 배율이 이만큼 넘게
#: 다르면 그건 끝자리가 아니라 다른 계산이다 — 🔴 불일치로 둔다.
WEIGHT_REL_TOL = 1e-6
#: 배율이 0 근처일 때 상대 비교가 폭발하지 않게 거는 절대 바닥.
WEIGHT_ABS_TOL = 1e-12

VERDICT_MATCH = "일치"
VERDICT_NEAR = "거의 일치"
VERDICT_MISMATCH = "🔴 불일치"


@dataclass(frozen=True)
class RowCheck:
    """원장 한 행의 대조 결과."""

    asset: str
    as_of_ms: int
    ledger_weight: float
    recomputed_weight: float | None
    ledger_signal: float
    recomputed_signal: float | None
    ledger_price: float
    stored_close: float | None
    notional_ok: bool
    error: str | None = None

    @property
    def weight_ok(self) -> bool:
        """배율이 **정확히** 같은가."""
        return self.recomputed_weight is not None and self.recomputed_weight == self.ledger_weight

    @property
    def weight_near(self) -> bool:
        """배율이 정확히 같지는 않지만 끝자리만 다른가(`WEIGHT_REL_TOL` 안)."""
        if self.recomputed_weight is None or self.weight_ok:
            return False
        return math.isclose(
            self.ledger_weight,
            self.recomputed_weight,
            rel_tol=WEIGHT_REL_TOL,
            abs_tol=WEIGHT_ABS_TOL,
        )

    @property
    def weight_diff(self) -> float | None:
        """원장 − 재계산(재계산이 없으면 None)."""
        if self.recomputed_weight is None:
            return None
        return self.ledger_weight - self.recomputed_weight

    @property
    def weight_rel_diff(self) -> float | None:
        """|원장 − 재계산| ÷ |재계산|(재계산이 0이면 None)."""
        diff = self.weight_diff
        if diff is None or self.recomputed_weight == 0.0:
            return None
        assert self.recomputed_weight is not None
        return abs(diff) / abs(self.recomputed_weight)

    @property
    def signal_ok(self) -> bool:
        if self.recomputed_signal is None:
            return False
        both_nan = math.isnan(self.recomputed_signal) and math.isnan(self.ledger_signal)
        return both_nan or self.recomputed_signal == self.ledger_signal

    @property
    def price_ok(self) -> bool:
        return self.stored_close is not None and self.stored_close == self.ledger_price

    @property
    def verdict(self) -> str:
        """일치 / 거의 일치 / 🔴 불일치. 문턱은 배율에만 — 나머지는 정확히 같아야 한다."""
        exact_rest = self.error is None and self.signal_ok and self.price_ok and self.notional_ok
        if exact_rest and self.weight_ok:
            return VERDICT_MATCH
        if exact_rest and self.weight_near:
            return VERDICT_NEAR
        return VERDICT_MISMATCH

    @property
    def ok(self) -> bool:
        """🔴 불일치가 아닌가(거의 일치는 실패가 아니다 — 종료 코드 0)."""
        return self.verdict != VERDICT_MISMATCH


@dataclass(frozen=True)
class TsmomParityReport:
    checks: list[RowCheck] = field(default_factory=list)
    skipped_days: dict[str, list[int]] = field(default_factory=dict)
    """자산 → 확정 일봉은 있는데 원장 행이 없는 날(`open_time`) — 러너 공백(실패 아님)."""

    @property
    def mismatches(self) -> list[RowCheck]:
        """🔴 불일치 행 — 종료 코드 1의 근거."""
        return [c for c in self.checks if c.verdict == VERDICT_MISMATCH]

    @property
    def near_matches(self) -> list[RowCheck]:
        """거의 일치 행 — 정보로만 보여 준다."""
        return [c for c in self.checks if c.verdict == VERDICT_NEAR]

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _closed_daily(
    store: BarSource, asset: str, *, until_ms: int, lookback_days: int
) -> pd.DataFrame:
    frame = store.load(
        asset, "1d", start_ms=until_ms - (lookback_days + 2) * DAY_MS, end_ms=until_ms + 1
    )
    if frame.empty:
        return frame
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    return frame[frame["open_time"].astype("int64") <= until_ms].sort_values("open_time")


def _check_row(store: BarSource, row: RebalanceRow, lookback_days: int) -> RowCheck:
    bars = _closed_daily(store, row.asset, until_ms=row.as_of_ms, lookback_days=lookback_days)
    stored_close: float | None = None
    if not bars.empty and int(bars["open_time"].iloc[-1]) == row.as_of_ms:
        stored_close = float(bars["close"].iloc[-1])
    notional_expected = row.equity_basis * tsmom.notional_fraction(row.weight)
    notional_ok = math.isclose(
        row.target_notional, notional_expected, rel_tol=NOTIONAL_REL_TOL, abs_tol=1e-9
    )
    try:
        target = tsmom.target_from_daily_closes(row.asset, bars["open_time"], bars["close"])
    except (ValueError, KeyError) as exc:
        return RowCheck(
            row.asset,
            row.as_of_ms,
            row.weight,
            None,
            row.signal,
            None,
            row.price,
            stored_close,
            notional_ok,
            error=str(exc),
        )
    if target.as_of_ms != row.as_of_ms:
        return RowCheck(
            row.asset,
            row.as_of_ms,
            row.weight,
            None,
            row.signal,
            None,
            row.price,
            stored_close,
            notional_ok,
            error=f"저장된 마지막 확정 일봉이 {format_kst(target.as_of_ms)} — 원장 기준일과 다르다",
        )
    return RowCheck(
        row.asset,
        row.as_of_ms,
        row.weight,
        target.weight,
        row.signal,
        target.signal,
        row.price,
        stored_close,
        notional_ok,
    )


def check_tsmom_parity(
    store: BarSource,
    rows: Sequence[RebalanceRow],
    *,
    now_ms: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> TsmomParityReport:
    """원장 행마다 다시 계산해 맞대고, 자산마다 건너뛴 날을 센다(읽기 전용)."""
    checks = [_check_row(store, r, lookback_days) for r in rows]
    skipped: dict[str, list[int]] = {}
    by_asset: dict[str, set[int]] = {}
    for r in rows:
        by_asset.setdefault(r.asset, set()).add(r.as_of_ms)
    for asset, days in by_asset.items():
        first = min(days)
        frame = store.load(asset, "1d", start_ms=first, end_ms=now_ms)
        if frame.empty:
            continue
        if "closed" in frame.columns:
            frame = frame[frame["closed"].astype(bool)]
        closed_days = [
            int(t) for t in frame["open_time"].astype("int64") if int(t) + DAY_MS <= now_ms
        ]
        missing = [d for d in closed_days if d not in days]
        if missing:
            skipped[asset] = missing
    return TsmomParityReport(checks=checks, skipped_days=skipped)


def render_tsmom_parity(report: TsmomParityReport) -> str:
    """사람이 읽는 표(평문 · KST)."""
    lines = ["## 추세추종 페이퍼 ↔ 다시 계산 대조", ""]
    if not report.checks:
        lines.append(
            "원장(`tsmom_rebalances`)에 행이 없습니다 — 추세추종이 아직 리밸런싱하지 않았습니다."
        )
        return "\n".join(lines)
    n_match = sum(1 for c in report.checks if c.verdict == VERDICT_MATCH)
    lines.append(
        f"행 {len(report.checks)}개 중 일치 {n_match}개 · 거의 일치 {len(report.near_matches)}개"
        f" · 불일치 {len(report.mismatches)}개"
    )
    lines.append("")
    lines.append(
        "| 기준일(KST) | 자산 | 배율(원장) | 배율(재계산) | 배율 | 신호 | 체결가=종가 | 목표 명목"
        " | 판정 |"
    )
    lines.append("| -- | -- | --: | --: | -- | -- | -- | -- | -- |")
    for c in report.checks:
        recomputed = "—" if c.recomputed_weight is None else f"{c.recomputed_weight:+.6f}"
        weight_mark = "✓" if c.weight_ok else ("≈" if c.weight_near else "✗")
        lines.append(
            f"| {format_kst(c.as_of_ms + DAY_MS)} | {c.asset.split('/')[0]} | "
            f"{c.ledger_weight:+.6f} | {recomputed} | {weight_mark} | "
            f"{'✓' if c.signal_ok else '✗'} | "
            f"{'✓' if c.price_ok else '✗'} | {'✓' if c.notional_ok else '✗'} | "
            f"{c.verdict} |"
        )
        diff = c.weight_diff
        if diff is not None and not c.weight_ok:
            rel = c.weight_rel_diff
            rel_text = "—" if rel is None else f"{rel:.2e}"
            lines.append(
                f"|  | ↳ 배율 차이(원장 − 재계산) {diff:+.3e} · 상대 {rel_text} |"
                "  |  |  |  |  |  |  |"
            )
        if c.error:
            lines.append(f"|  | ↳ {c.error} |  |  |  |  |  |  |  |")
    if report.skipped_days:
        lines.append("")
        lines.append("건너뛴 날(러너 공백 — 소급 거래하지 않는 설계라 실패가 아니다):")
        for asset, days in sorted(report.skipped_days.items()):
            shown = ", ".join(format_kst(d + DAY_MS)[:10] for d in days[:10])
            more = "" if len(days) <= 10 else f" 외 {len(days) - 10}일"
            lines.append(f"- {asset.split('/')[0]}: {len(days)}일 — {shown}{more}")
    if report.near_matches:
        lines.append("")
        lines.append(
            f"ℹ️ 거의 일치 {len(report.near_matches)}개 — 배율의 부동소수 끝자리만 다르다"
            f"(상대 차이 {WEIGHT_REL_TOL:.0e} 이하 · 신호·체결가·목표 명목은 정확히 같다)."
            " 지나간 일봉이 아주 조금 고쳐졌을 때(백필 복구) 나는 모양이라 실패로 세지 않는다."
        )
    if report.mismatches:
        lines.append("")
        lines.append(
            "🔴 같은 함수 · 같은 저장 데이터라 갈라질 이유가 없다 — 일봉이 나중에"
            " 고쳐졌거나(백필 복구), 러너가 다른 데이터를 봤거나, 배선이 틀렸다."
        )
    return "\n".join(lines)
