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
        return self.recomputed_weight is not None and self.recomputed_weight == self.ledger_weight

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
    def ok(self) -> bool:
        return (
            self.error is None
            and self.weight_ok
            and self.signal_ok
            and self.price_ok
            and self.notional_ok
        )


@dataclass(frozen=True)
class TsmomParityReport:
    checks: list[RowCheck] = field(default_factory=list)
    skipped_days: dict[str, list[int]] = field(default_factory=dict)
    """자산 → 확정 일봉은 있는데 원장 행이 없는 날(`open_time`) — 러너 공백(실패 아님)."""

    @property
    def mismatches(self) -> list[RowCheck]:
        return [c for c in self.checks if not c.ok]

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
    lines = ["## 엔진 C 페이퍼 ↔ 다시 계산 대조", ""]
    if not report.checks:
        lines.append(
            "원장(`tsmom_rebalances`)에 행이 없습니다 — 엔진 C가 아직 리밸런싱하지 않았습니다."
        )
        return "\n".join(lines)
    n_ok = sum(1 for c in report.checks if c.ok)
    lines.append(f"행 {len(report.checks)}개 중 일치 {n_ok}개 · 불일치 {len(report.mismatches)}개")
    lines.append("")
    lines.append(
        "| 기준일(KST) | 자산 | 배율(원장) | 배율(재계산) | 신호 | 체결가=종가 | 목표 명목 | 판정 |"
    )
    lines.append("| -- | -- | --: | --: | -- | -- | -- | -- |")
    for c in report.checks:
        recomputed = "—" if c.recomputed_weight is None else f"{c.recomputed_weight:+.6f}"
        lines.append(
            f"| {format_kst(c.as_of_ms + DAY_MS)} | {c.asset.split('/')[0]} | "
            f"{c.ledger_weight:+.6f} | {recomputed} | {'✓' if c.signal_ok else '✗'} | "
            f"{'✓' if c.price_ok else '✗'} | {'✓' if c.notional_ok else '✗'} | "
            f"{'일치' if c.ok else '🔴 불일치'} |"
        )
        if c.error:
            lines.append(f"|  | ↳ {c.error} |  |  |  |  |  |  |")
    if report.skipped_days:
        lines.append("")
        lines.append("건너뛴 날(러너 공백 — 소급 거래하지 않는 설계라 실패가 아니다):")
        for asset, days in sorted(report.skipped_days.items()):
            shown = ", ".join(format_kst(d + DAY_MS)[:10] for d in days[:10])
            more = "" if len(days) <= 10 else f" 외 {len(days) - 10}일"
            lines.append(f"- {asset.split('/')[0]}: {len(days)}일 — {shown}{more}")
    if report.mismatches:
        lines.append("")
        lines.append(
            "🔴 같은 함수 · 같은 저장 데이터라 갈라질 이유가 없다 — 일봉이 나중에"
            " 고쳐졌거나(백필 복구), 러너가 다른 데이터를 봤거나, 배선이 틀렸다."
        )
    return "\n".join(lines)
