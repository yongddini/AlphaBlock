"""WAN-430 — 스토캐스틱 × 오더블록 팔의 **15m**, 그리고 TF별 분해.

## 한 줄

WAN-423 §6·§7과 WAN-424 §1이 낸 「오더블록 × 스토캐스틱은 곱해야 값이 있다」 표는 **1h~1w 아홉
TF**만 돌았다 — **15m이 통째로 빠져 있었다.** 그런데 **채택 북 거래의 66.4%가 15m**이고
(WAN-312) 15m은 이 저장소가 반복해 「가장 취약하다」고 잰 축이다(WAN-124/330/348 낙관 체결 ·
WAN-370/423 §2 비용 R 비중 최대). 그래서 **부호가 뒤집힐 수 있는 유일하게 남은 축**이다.

## 🚨 `TIMEFRAMES`에 15m을 그냥 더할 수는 없다 — 그러면 재현 검산이 깨진다

`wan424.ArmRow`에는 **TF 열이 없다**(아홉 TF를 한 지갑에 넣은 **집계 행**이다). 그러니 상수에
15m을 더하면 그 집계가 움직여 **WAN-423 §7 기준값 재현(검산 a′)이 통째로 실패한다** — 그리고
그건 「15m이 나쁘다」가 아니라 「다른 걸 재고 있다」는 뜻이다. 게다가 이슈 완료기준 1·2가
**TF별 행**을 요구한다(「TF 9개 중 8개 뒷구간 양수」 관문을 10TF로 다시 찍어야 한다).

그래서 이 모듈은 **스코프 축**을 더한다(WAN-312 → WAN-316이 쓴 바로 그 패턴):

- `__all__` — **10TF 한 지갑**(이 표의 새 헤드라인).
- `__no15m__` — **아홉 TF 한 지갑** = WAN-424 §1이 낸 그 판. 🚨 **공개 CSV와 비트 일치해야
  한다**(검산 a′) — 그게 「15m을 더해도 기존 칸이 안 움직였다」의 직접 증거다.
- 각 TF 하나씩 — 그 TF만의 지갑.

🚨 **스코프는 라벨 필터가 아니라 재배치다** — 북은 칸이 **한 지갑을 공유**하므로(WAN-213/316)
칸 집합을 줄이면 자본·슬롯 경합이 달라져 **다른 지갑**이 된다. 거래를 라벨로 골라 더하면
그 재배치가 빠진다(WAN-316/389가 못 박은 구분). 그래서 스코프마다 `place`를 다시 부른다.

## 배선은 새로 짜지 않는다

후보 생성·팔 후보·필터·배치·손익 식은 전부 `wan424_stoch_ob_arm`의 그 함수다
(`build_base_payloads`/`build_arm_cells`/`filtered_payloads`/`place`). 이 모듈이 더하는 것은
**TF 하나와 스코프 축**뿐이고, 사본을 만들면 두 표가 조용히 갈라진다(WAN-95/112/123).

## 재현

    uv run python scripts/wan430_warm_15m_payloads.py 4     # 15m 후보만 캐시에 적재(선행)
    uv run python -m backtest.wan430_stoch_arm_15m --jobs 4  # 격자
    uv run python -m backtest.wan430_stoch_arm_15m --from-csv

측정 전용 · 엔진·기본값·토대 불변(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로) · 핀 없음(WAN-305) · 판단은 북에서(WAN-341) ·
`ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import net_r
from backtest.models import BacktestConfig
from backtest.wan151_split_zone_null import MIN_TRADES_FOR_VERDICT
from backtest.wan169_leverage_book import CellPayload
from backtest.wan370_cost_decomposition import decompose_trade, stop_width_fraction
from backtest.wan424_stoch_ob_arm import (
    DEFAULT_PAYLOAD_DIR,
    FLOORS,
    HOLD_BARS,
    REPORT_DIR,
    SEGMENTS,
    SYMBOLS,
    THRESHOLDS,
    TIMEFRAMES,
    ArmCell,
    ArmRow,
    build_arm_cells,
    build_base_payloads,
    filtered_payloads,
    place,
    place_segments,
    reference_check,
)

__all__ = [
    "FLOOR_CSV",
    "FLOOR_SWEEP",
    "FLOOR_SWEEP_MIN",
    "FloorRow",
    "GRID_CSV",
    "MIN_TRADES_FOR_VERDICT",
    "render_floor_summary",
    "floor_rows",
    "INHERITED_TAKE_PROFIT_LIQUIDITY",
    "NEW_TIMEFRAME",
    "SCOPE_ALL",
    "SCOPE_NO_15M",
    "SUMMARY_MD",
    "TIMEFRAMES_10",
    "ScopedRow",
    "build_arm_cells",
    "build_base_payloads",
    "filtered_payloads",
    "_sign_is_decided",
    "gate_lines",
    "place",
    "place_segments",
    "render_summary",
    "run_grid",
    "scopes_for",
]

#: 이 이슈가 더하는 TF — **하나뿐이다**(축을 하나만 흔든다, WAN-394).
NEW_TIMEFRAME = "15m"

#: 10TF. 🚨 `wan424.TIMEFRAMES`를 **고치지 않고** 앞에 붙인다 — 그 상수가 움직이면 공개
#: `wan424_stoch_ob_arm.csv`가 재현되지 않는다(이 모듈의 검산 a′가 그것을 대조한다).
TIMEFRAMES_10: tuple[str, ...] = (NEW_TIMEFRAME, *TIMEFRAMES)

#: 10TF 한 지갑 — 새 헤드라인.
SCOPE_ALL = "__all__"

#: 아홉 TF 한 지갑 = WAN-424 §1 판. **비트 일치해야 한다**(검산 a′).
SCOPE_NO_15M = "__no15m__"

# 🚨 **모듈 상대 경로다** — `wan424.REPORT_DIR`를 그대로 쓴다. CWD 상대(`Path("backtest/...")`)로
# 두면 저장소 루트 밖에서 돌릴 때 **엉뚱한 자리에 쓰거나 조용히 실패한다**(WAN-354가 `.env`에서
# 겪은 그 부류). 두 모듈의 리포트가 **같은 디렉터리**에 나와야 재현 검산이 서로를 찾는다.
GRID_CSV = REPORT_DIR / "wan430_stoch_arm_15m.csv"
SUMMARY_MD = REPORT_DIR / "wan430_stoch_arm_15m_summary.md"

#: 「0과 구분되지 않는다」 규약 폭(WAN-366/370).
NOISE_R = 0.005

#: 익절 청산 유동성 — 이 모듈은 **정하지 않고 물려받는다**(`wan424.place`가 넘기는 그 값).
#:
#: 🚨 리터럴을 다시 적으면 채택 회계가 두 곳에 살아 갈라진다(WAN-370/373이 못 박은 자리). 그래서
#: 이것은 **인용이지 정의가 아니고**, 빌려 쓴 `place`가 실제로 그 값을 넘기는지는 `wan424` 쪽
#: 테스트가 확인한다(그쪽이 `harness.ADOPTED_TAKE_PROFIT_LIQUIDITY`를 명시한다).
INHERITED_TAKE_PROFIT_LIQUIDITY = harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


def scopes_for(timeframes: Sequence[str]) -> tuple[str, ...]:
    """낼 스코프 — 집계 둘 + TF 하나씩.

    `__no15m__`은 15m이 실제로 있을 때만 낸다(없으면 `__all__`과 같은 지갑이라 라벨만 둘이
    되고, 그러면 검산 a′가 「같은 걸 두 번 쟀다」를 통과로 보고한다 — WAN-367 부류).
    """
    scopes = [SCOPE_ALL]
    if NEW_TIMEFRAME in timeframes:
        scopes.append(SCOPE_NO_15M)
    return (*scopes, *sorted(timeframes, key=lambda tf: TIMEFRAMES_10.index(tf)))


def _cells_in_scope(payloads: Sequence[CellPayload], scope: str) -> list[CellPayload]:
    if scope == SCOPE_ALL:
        return list(payloads)
    if scope == SCOPE_NO_15M:
        return [p for p in payloads if p.timeframe != NEW_TIMEFRAME]
    return [p for p in payloads if p.timeframe == scope]


@dataclass(frozen=True)
class ScopedRow:
    """`wan424.ArmRow` + **스코프 하나**. 열 순서·이름을 그쪽과 맞춰 두 CSV를 나란히 읽는다."""

    scope: str
    hold: int
    floor: float
    threshold: float | None
    segment: str
    num_trades: int
    mean_net_r: float
    se_net_r: float
    fixed_return: float
    fixed_mdd: float
    compound_return: float
    compound_mdd: float
    compound_ruined: bool

    @classmethod
    def of(cls, scope: str, row: ArmRow) -> ScopedRow:
        return cls(scope, **dataclasses.asdict(row))

    def as_arm_row(self) -> ArmRow:
        """스코프를 떼고 `wan424.ArmRow`로 — 재현 검산이 그 타입을 받는다."""
        fields = {f.name: getattr(self, f.name) for f in dataclasses.fields(ArmRow)}
        return ArmRow(**fields)


def run_grid(
    payloads: Sequence[CellPayload],
    cells: Sequence[ArmCell],
    *,
    timeframes: Sequence[str],
    log: bool = True,
) -> list[ScopedRow]:
    """조합 × 스코프 × 구간. 🚨 스코프마다 `place`를 **다시** 부른다(재배치)."""
    rows: list[ScopedRow] = []
    scopes = scopes_for(timeframes)
    for floor in FLOORS:
        for thr in THRESHOLDS:
            for hold in HOLD_BARS:
                t0 = time.monotonic()
                filtered = filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=thr)
                for scope in scopes:
                    scoped = _cells_in_scope(filtered, scope)
                    if not scoped:
                        continue
                    rows.extend(
                        ScopedRow.of(scope, row)
                        for row in place(scoped, hold=hold, floor=floor, threshold=thr)
                    )
                if log:
                    k = "끔" if thr is None else f"<{thr:.0f}"
                    print(
                        f"[wan430] ts{hold} 하한{floor:.0%} %K{k} × {len(scopes)}스코프: "
                        f"{time.monotonic() - t0:.0f}s",
                        flush=True,
                    )
    return rows


# --------------------------------------------------------------------------- #
# 관문 — WAN-423 §6의 넷을 10TF로 다시 찍는다
# --------------------------------------------------------------------------- #


def _index(rows: Sequence[ScopedRow]) -> dict[tuple[str, int, float, float | None, str], ScopedRow]:
    return {(r.scope, r.hold, r.floor, r.threshold, r.segment): r for r in rows}


def _sign_is_decided(row: ScopedRow) -> bool:
    """부호가 **정해졌는가** — WAN-381/412가 쓴 그 관문(`|평균| > 2σ` ∧ 잡음선 밖).

    🚨 거래 수 하한만으로는 부족하다. 이 표의 15m은 `하한4%·%K<25·ts24`에서 **+0.6207R**인데
    2σ가 **±0.6821**이라 0과 구분되지 않는다 — 20거래가 하한(`MIN_TRADES_FOR_VERDICT`)을
    정확히 통과하므로 그 자를 쓰면 **그 칸이 「양수」로 세어진다**. 풀링된 31종목 지갑에서
    올바른 자는 건수가 아니라 **표준오차**다. 두 관문을 **함께** 건다(하나는 「그 칸을 볼
    자격이 있나」, 하나는 「본 값이 0과 다른가」).
    """
    if math.isnan(row.mean_net_r) or math.isnan(row.se_net_r):
        return False
    return abs(row.mean_net_r) > max(2 * row.se_net_r, NOISE_R)


def gate_lines(rows: Sequence[ScopedRow], *, timeframes: Sequence[str]) -> list[str]:
    """관문 ③ — 「TF 몇 개 중 몇 개가 뒷구간 양수」를 **10TF 기준**으로 다시 낸다.

    🚨 관문 ①(문턱 단조)·②(하한 구간 전체 양수)·④(종목 LOO)는 **WAN-423 §6이 자기 좌표에서
    낸 것**이고 이 모듈은 그 셋을 다시 돌리지 않는다(축이 TF 하나라 격자를 늘리면 축이 둘
    움직인다, WAN-394). ③만 TF에 직접 걸리므로 여기서 갱신한다 — 안 하면 「9개 중 8개」라는
    문장이 10TF 표 옆에 낡은 채로 남는다.

    🚨 **자를 둘 다 찍는다 — 이 줄이 이 함수의 핵심이다.** WAN-423 §6의 「9개 중 8개」는
    **`net R > 0` 하나만 보는 자**이고, 이 모듈이 기본으로 쓰는 자는 그 위에 **표본 하한과
    2σ**를 더 건다(`_sign_is_decided`). **두 자는 같은 표에서 자릿수가 다른 답을 낸다** —
    이 격자의 헤드라인 팔(`ts4·하한4%·%K<25`)이 원래 자로는 **9/9 → 10/10**인데 엄격한 자로는
    **1**이다. 엄격한 자만 찍고 WAN-423 §6 옆에 놓으면 「15m이 관문을 무너뜨렸다」로 읽히는데
    **사실은 15m이 아니라 자가 바뀐 것**이다(그리고 엄격한 자에서는 **아홉 TF 판도 원래부터**
    거의 전부 미결정이었다). 이 저장소가 반복해 데인 자리다(WAN-403 `pool_k` · WAN-341).
    """
    index = _index(rows)
    lines: list[str] = []
    for floor in FLOORS:
        for thr in THRESHOLDS:
            for hold in HOLD_BARS:
                per_tf = [index.get((tf, hold, floor, thr, "oos_warm")) for tf in timeframes]
                got = [r for r in per_tf if r is not None]
                if not got:
                    continue
                # 🚨 **표본 미달 칸은 분자에도 분모에도 넣지 않는다**(WAN-143 `verdict()` 관행).
                # 그러지 않으면 20거래짜리 칸 하나가 「10/10 TF 양수」라는 헤드라인을 만든다 —
                # 이 표의 첫 판이 실제로 그렇게 찍혔고, 하필 그 칸이 15m이었다.
                judged = [r for r in got if r.num_trades >= MIN_TRADES_FOR_VERDICT]
                thin = [r for r in got if r.num_trades < MIN_TRADES_FOR_VERDICT]
                positive = [r for r in judged if _sign_is_decided(r) and r.mean_net_r > 0]
                # WAN-423 §6의 자 — `net R > 0` 하나뿐이다. 15m을 뺀 판을 나란히 찍어야
                # 「관문이 10TF에서 어떻게 됐나」가 **자를 바꾸지 않고** 읽힌다.
                raw = [r for r in got if not math.isnan(r.mean_net_r) and r.mean_net_r > 0]
                nine = [r for r in got if r.scope != NEW_TIMEFRAME]
                raw_nine = [r for r in nine if not math.isnan(r.mean_net_r) and r.mean_net_r > 0]
                fifteen = index.get((NEW_TIMEFRAME, hold, floor, thr, "oos_warm"))
                k = "끔" if thr is None else f"<{thr:.0f}"
                if fifteen is None:
                    mark = "—"
                elif fifteen.num_trades < MIN_TRADES_FOR_VERDICT:
                    mark = "⚠️ 판정 불가(표본)"
                elif not _sign_is_decided(fifteen):
                    mark = "⚠️ 부호 미결정"
                elif fifteen.mean_net_r > 0:
                    mark = "양수"
                else:
                    mark = "음수"
                lines.append(
                    f"- ts{hold} · 하한{floor:.0%} · %K{k}: "
                    f"**WAN-423 §6의 자**(`net R > 0`) {len(raw_nine)}/{len(nine)} TF → "
                    f"**{len(raw)}/{len(got)} TF**(15m 포함) · "
                    f"**엄격한 자**(표본 ≥{MIN_TRADES_FOR_VERDICT} ∧ 2σ) "
                    f"{len(positive)}/{len(judged)} TF"
                    + (f" (표본 미달 {len(thin)}TF 제외)" if thin else "")
                    + f" · 15m **{mark}**"
                    + (
                        ""
                        if fifteen is None
                        else f"({fifteen.mean_net_r:+.4f}R ± {2 * fifteen.se_net_r:.4f} · "
                        f"{fifteen.num_trades}거래 = 종목당 "
                        f"{fifteen.num_trades / len(SYMBOLS):.1f})"
                    )
                )
    return lines


def checksum_lines(rows: Sequence[ScopedRow]) -> list[str]:
    """검산 — (a′) `__no15m__` ≡ 공개 `wan424_stoch_ob_arm.csv` · (b) TF 합 = 스코프 집계.

    🚨 (b)는 **같아야 하는 게 아니다** — 북은 한 지갑이라 TF별 지갑 거래 수의 합이 10TF 한
    지갑과 다를 수 있고(재배치) 그 **차가 곧 경합의 크기**다. 그래서 관측으로 찍는다.
    """
    from backtest.wan424_stoch_ob_arm import CSV_PATH as WAN424_CSV

    lines: list[str] = []
    nine = [r for r in rows if r.scope == SCOPE_NO_15M]
    if not nine:
        lines.append("- ⚠️ (a′) 아홉 TF 스코프가 없어 재현 검산을 **건너뜀**(15m만 돈 판).")
    elif not WAN424_CSV.exists():
        lines.append(f"- ⚠️ (a′) `{WAN424_CSV}`가 없어 **건너뜀**.")
    else:
        published = pd.read_csv(WAN424_CSV)
        pub = {
            (
                int(r["hold"]),
                float(r["floor"]),
                None if pd.isna(r["threshold"]) else float(r["threshold"]),
                str(r["segment"]),
            ): r
            for r in published.to_dict("records")
        }
        worst_n = worst_r = 0.0
        missing = 0
        for row in nine:
            key = (row.hold, row.floor, row.threshold, row.segment)
            ref = pub.get(key)
            if ref is None:
                missing += 1
                continue
            worst_n = max(worst_n, abs(row.num_trades - int(ref["num_trades"])))
            worst_r = max(worst_r, abs(row.mean_net_r - float(ref["mean_net_r"])))
        mark = "✅" if missing == 0 and worst_n == 0 and worst_r < 1e-9 else "❌"
        lines.append(
            f"- {mark} (a′) `__no15m__` ≡ 공개 `wan424_stoch_ob_arm.csv` — {len(nine)}행 · "
            f"거래 수 최대차 **{worst_n:.0f}** · 거래당 net R 최대차 **{worst_r:.2e}**"
            + (f" · 짝 없는 행 {missing}" if missing else "")
        )
        lines += [f"- {line}" for line in reference_check([r.as_arm_row() for r in nine])]
    index = _index(rows)
    deltas: list[float] = []
    for floor in FLOORS:
        for thr in THRESHOLDS:
            for hold in HOLD_BARS:
                whole = index.get((SCOPE_ALL, hold, floor, thr, "oos_warm"))
                if whole is None:
                    continue
                per_tf = sum(
                    r.num_trades
                    for tf in TIMEFRAMES_10
                    if (r := index.get((tf, hold, floor, thr, "oos_warm"))) is not None
                )
                if whole.num_trades:
                    deltas.append((per_tf - whole.num_trades) / whole.num_trades)
    if deltas:
        lines.append(
            "- 📌 (b) 관측 · TF별 지갑 거래 수 합 ↔ 10TF 한 지갑: 최대 "
            f"**{max(abs(d) for d in deltas) * 100:.2f}%p** 차 — 0이면 **용량 미포화**(경합 없음), "
            "0이 아니면 **칸들이 자본·슬롯을 다퉜다**는 증거다(WAN-316/389)."
        )
    return lines


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _cmp(row: ScopedRow | None) -> str:
    if row is None:
        return "—"
    ret = "파산" if row.compound_ruined else f"{row.compound_return * 100:+.0f}%"
    return f"{ret} / {row.compound_mdd * 100:.0f}%"


def render_summary(
    rows: Sequence[ScopedRow], *, timeframes: Sequence[str], elapsed: float | None = None
) -> str:
    index = _index(rows)
    combos = sorted(
        {(r.floor, r.threshold, r.hold) for r in rows}, key=lambda x: (x[0], x[1] or 0, x[2])
    )
    has15 = NEW_TIMEFRAME in timeframes
    out = [
        "# WAN-430 — 스토캐스틱 × 오더블록 팔의 15m (그리고 TF별 분해)",
        "",
        f"롱 · 31종목 × **{len(timeframes)}TF** · 첫 탭 · 재진입 없음 · `pen_5bp` × 같은 분 익절"
        " 금지 · 채택 북 · 복리 끔 · 가드 끔(손절폭 하한이 대신) · 판정 자 = **거래당 net R**.",
        "",
        "🚨 **스코프는 라벨 필터가 아니라 재배치다** — 북은 칸이 한 지갑을 공유하므로 칸 집합을"
        " 줄이면 자본·슬롯 경합이 달라져 **다른 지갑**이 된다(WAN-213/316). TF별 행은 그 TF만의"
        " 지갑이고, `__all__`은 10TF 한 지갑이다.",
        "",
        "## 검산",
        "",
        *checksum_lines(rows),
        "",
        "## 집계 (10TF 한 지갑 vs 아홉 TF 한 지갑)",
        "",
        "| 팔 | 스코프 | full | is | oos_warm | full 복리 수익/MDD | oos 복리 수익/MDD |",
        "| -- | -- | --: | --: | --: | --: | --: |",
    ]
    for floor, thr, hold in combos:
        k = "끔" if thr is None else f"<{thr:.0f}"
        for scope, label in ((SCOPE_ALL, f"{len(timeframes)}TF"), (SCOPE_NO_15M, "9TF")):
            cells = [
                (
                    "—"
                    if (r := index.get((scope, hold, floor, thr, s))) is None
                    else f"{r.mean_net_r:+.3f} ({r.num_trades})"
                )
                for s in SEGMENTS
            ]
            if all(c == "—" for c in cells):
                continue
            out.append(
                f"| ts{hold} · 하한{floor:.0%} · %K{k} | {label} | "
                + " | ".join(cells)
                + f" | {_cmp(index.get((scope, hold, floor, thr, 'full')))}"
                f" | {_cmp(index.get((scope, hold, floor, thr, 'oos_warm')))} |"
            )
    out += [
        "",
        f"## {NEW_TIMEFRAME} 단독 (이 이슈가 처음 내는 행)",
        "",
        "| 팔 | full | is | oos_warm |",
        "| -- | --: | --: | --: |",
    ]
    if has15:
        for floor, thr, hold in combos:
            k = "끔" if thr is None else f"<{thr:.0f}"
            cells = [
                (
                    "—"
                    if (r := index.get((NEW_TIMEFRAME, hold, floor, thr, s))) is None
                    else f"{r.mean_net_r:+.3f} ({r.num_trades})"
                )
                for s in SEGMENTS
            ]
            out.append(f"| ts{hold} · 하한{floor:.0%} · %K{k} | " + " | ".join(cells) + " |")
    else:
        out.append("| — | — | — | — |")
    out += [
        "",
        "## 관문 ③ — 「TF 몇 개 중 몇 개가 뒷구간 양수」",
        "",
        "🚨 **자가 둘이고 답이 다르다 — 나란히 읽을 것.** WAN-423 §6의 「9개 중 8개」는"
        " **`net R > 0` 하나만** 보는 자다. 이 표는 그 자로도 찍고(왼쪽), 그 위에 **표본"
        f" ≥{MIN_TRADES_FOR_VERDICT}와 2σ**를 더 건 엄격한 자로도 찍는다(오른쪽)."
        " ⚠️ **엄격한 자의 숫자를 WAN-423 §6 옆에 단독으로 놓지 말 것** — 「15m이 관문을"
        " 무너뜨렸다」로 읽히는데 **무너뜨린 것은 15m이 아니라 자**다(아홉 TF 판도 그 자에서는"
        " 원래부터 거의 전부 미결정이다). WAN-403이 `pool_k`에서 겪은 그 부류다.",
        "",
        *gate_lines(rows, timeframes=timeframes),
        "",
        "📌 **원래 자로는 15m이 관문을 안 깬다** — 15m을 더하면 분모가 하나 늘고 분자도 대체로"
        " 같이 는다(헤드라인 팔 `ts4·하한4%·%K<25`는 **9/9 → 10/10**). 📌 **엄격한 자로는 어느"
        " 팔도 서지 않는다** — 10TF 30팔 전부 **0~1개**다. 즉 이 표가 말하는 것은 *「15m이"
        " 나쁘다」*가 아니라 ***「이 관문은 애초에 부호가 정해진 적이 없다」***이다.",
        "",
        "🚨 관문 ①(문턱 단조)·②(하한 구간 전체 양수)·④(종목 LOO)는 **WAN-423 §6이 자기 좌표에서**"
        " 낸 것이고 이 표는 다시 돌리지 않았다(축이 TF 하나다 — WAN-394). ③만 TF에 직접 걸린다.",
        "",
        "## 알려진 한계",
        "",
        "- ⚠️ **표에서 최선 칸을 고르지 말 것**(WAN-161) — 읽을 것은 모양이다.",
        "- ⚠️ **15m은 이 저장소가 반복해 「가장 취약하다」고 잰 축이다** — 낙관 체결 의존이 가장"
        " 깊고(WAN-124/330/348) 손절폭이 좁아 **비용의 R 비중이 최대**다(WAN-370 · WAN-423 §2:"
        " 손절폭 0~1% 구간 −0.52R). **양수여도 그 경고를 함께 읽는다.**",
        "- ⚠️ **매칭 널 미측정**(WAN-423이 「다음 관문」으로 남긴 별개 축) ·"
        " 추가 체결 보수화 미측정.",
        "- ⚠️ **재탭·재진입 켠 판은 범위 밖**(WAN-423 §10이 사용자 결정으로 중단 · 수치 인용 금지).",
        "- 🚨 **채택 권고가 아니다** — 이 팔 자체가 WAN-423/424에서 **채택 권고 없음**으로 닫혔다."
        " 전환은 **재-베이스라인 = 사용자 결정**이고 개발자 임의 착수 금지다.",
    ]
    if elapsed is not None:
        out += [
            "",
            f"실측 {elapsed:.0f}초 — ⚠️ **다른 모듈의 칸 비용과 섞지 말 것**(WAN-203 →"
            " WAN-312/383).",
        ]
    return "\n".join(out) + "\n"


def rows_to_frame(rows: Sequence[ScopedRow]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(r) for r in rows])


def frame_to_rows(frame: pd.DataFrame) -> list[ScopedRow]:
    out: list[ScopedRow] = []
    for rec in frame.to_dict("records"):
        thr = rec["threshold"]
        missing = thr is None or (isinstance(thr, float) and math.isnan(thr))
        rec["threshold"] = None if missing else float(thr)
        rec["compound_ruined"] = bool(rec["compound_ruined"])
        out.append(ScopedRow(**rec))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--timeframes", default=",".join(TIMEFRAMES_10))
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--from-csv", action="store_true")
    parser.add_argument("--pilot", action="store_true", help="BTC 15m+4h · 한 조합")
    parser.add_argument(
        "--part",
        choices=("grid", "floor-sweep"),
        default="grid",
        help="grid = 10TF 격자(§1) · floor-sweep = 15m 손절폭 하한 스윕(§2)",
    )
    args = parser.parse_args(argv)

    if args.part == "floor-sweep":
        return _main_floor_sweep(args)

    timeframes = tuple(args.timeframes.split(","))
    if args.from_csv:
        rows = frame_to_rows(pd.read_csv(GRID_CSV))
        timeframes = tuple(tf for tf in TIMEFRAMES_10 if any(r.scope == tf for r in rows))
        elapsed = None
    else:
        symbols = tuple(args.symbols.split(","))
        if args.pilot:
            symbols, timeframes = ("BTC/USDT:USDT",), ("15m", "4h")
        t0 = time.monotonic()
        payloads = build_base_payloads(
            jobs=args.jobs,
            payload_dir=args.payload_dir,
            symbols=symbols,
            timeframes=timeframes,
        )
        print(f"[wan430] base 후보 {len(payloads)}칸: {time.monotonic() - t0:.0f}s", flush=True)
        t1 = time.monotonic()
        cells = build_arm_cells(payloads, jobs=args.jobs)
        print(f"[wan430] 팔 후보 {len(cells)}칸: {time.monotonic() - t1:.0f}s", flush=True)
        rows = run_grid(payloads, cells, timeframes=timeframes)
        elapsed = time.monotonic() - t0
        if not args.pilot:
            GRID_CSV.parent.mkdir(parents=True, exist_ok=True)
            rows_to_frame(rows).to_csv(GRID_CSV, index=False)

    summary = render_summary(rows, timeframes=timeframes, elapsed=elapsed)
    if not args.pilot:
        SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_MD.write_text(summary, encoding="utf-8")
    print(summary)
    return 0


# --------------------------------------------------------------------------- #
# §2 — 15m 손절폭 하한 스윕 (사용자 질문 2026-09-24 「15m은 손절퍼센트 낮추면 어떻게 돼?」)
# --------------------------------------------------------------------------- #

#: 팔 후보를 **이 하한으로 한 번만** 만든다 — `filtered_payloads`가 그 위에서 올려 거르므로
#: 하한 점을 늘리는 것이 **공짜**다(실측: 배치가 조합당 2초). 🚨 반대로 이 값보다 낮은 하한은
#: 스윕에 넣을 수 없다(후보가 애초에 없다) — 넣으면 조용히 이 값의 결과가 나온다.
FLOOR_SWEEP_MIN = 0.005

#: 스윕할 하한 — 아래 둘(3%·4%)은 **기존 표의 재현 검산**이고 위 넷이 새로 여는 점이다.
FLOOR_SWEEP: tuple[float, ...] = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04)

FLOOR_CSV = REPORT_DIR / "wan430_15m_floor_sweep.csv"
FLOOR_SUMMARY_MD = REPORT_DIR / "wan430_15m_floor_sweep_summary.md"


class FloorRow(BaseModel):
    """15m 한 칸의 (하한 × 보유 × %K × 구간)."""

    model_config = ConfigDict(frozen=True)

    floor: float
    hold: int
    threshold: float | None
    segment: str
    num_trades: int
    mean_net_r: float
    se_net_r: float
    """2σ 관문용 — 🚨 이 표에서 거래 수는 **하한을 내리면 커지므로** 건수만 보면 항상 낮은
    하한이 이겨 보인다. 부호는 표준오차가 정한다(`_sign_is_decided`와 같은 자)."""
    mean_cost_r: float
    """거래당 비용 R(수수료＋슬리피지＋펀딩 ÷ 리스크 금액) — **이 표의 핵심 열**이다.

    WAN-423 §2가 *「손절폭 0~1% 구간은 비용만 −0.52R」*이라 쟀고, 15m 손절폭 중앙값이
    **0.398%**(BTC 실측)라 대부분이 그 구간에 들어간다. 그래서 하한을 내리는 것은 **표본을
    사고 비용을 무는 교환**이고, 그 교환비를 숫자로 내는 것이 이 열의 존재 이유다."""
    mean_gross_r: float
    """비용 전 — `mean_net_r + mean_cost_r`. 🚨 시장이 주는 몫과 비용을 갈라야 「하한을 내리면
    나빠지는 것이 **비용 때문인지 신호가 나빠서인지**」를 답할 수 있다(WAN-396 교훈)."""
    median_stop_width: float


def _cost_cfg() -> BacktestConfig:
    """비용 분해가 쓰는 설정 — 🚨 배치와 **같은** 익절 유동성이라야 항등식이 닫힌다(WAN-370)."""
    return harness.build_config("15m", take_profit_liquidity=INHERITED_TAKE_PROFIT_LIQUIDITY)


def floor_rows(
    payloads: Sequence[CellPayload],
    cells: Sequence[ArmCell],
    *,
    floors: Sequence[float] = FLOOR_SWEEP,
    log: bool = True,
) -> list[FloorRow]:
    """15m 전용 하한 스윕 — 팔 후보는 한 번만 만들고 하한마다 **거르고 재배치**한다."""
    cfg = _cost_cfg()
    rows: list[FloorRow] = []
    for floor in floors:
        for thr in THRESHOLDS:
            for hold in HOLD_BARS:
                t0 = time.monotonic()
                scoped = filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=thr)
                for seg in place_segments(scoped):
                    pairs = seg.trades_with_placements()
                    nets = [net_r(trade, placement) for trade, placement in pairs]
                    costs = [
                        decompose_trade(trade, cfg).total_cost / placement.risk_amount
                        if placement.risk_amount > 0
                        else 0.0
                        for trade, placement in pairs
                    ]
                    widths = [stop_width_fraction(t, p) for t, p in pairs]
                    n = len(nets)
                    mean = sum(nets) / n if n else math.nan
                    se = (
                        math.sqrt(sum((r - mean) ** 2 for r in nets) / (n - 1) / n)
                        if n > 1
                        else math.nan
                    )
                    cost = sum(costs) / n if n else math.nan
                    rows.append(
                        FloorRow(
                            floor=floor,
                            hold=hold,
                            threshold=thr,
                            segment=seg.segment,
                            num_trades=n,
                            mean_net_r=mean,
                            se_net_r=se,
                            mean_cost_r=cost,
                            mean_gross_r=mean + cost if n else math.nan,
                            median_stop_width=(
                                float(statistics.median(widths)) if widths else math.nan
                            ),
                        )
                    )
                if log:
                    k = "끔" if thr is None else f"<{thr:.0f}"
                    print(
                        f"[wan430-floor] 하한{floor:.1%} ts{hold} %K{k}: "
                        f"{time.monotonic() - t0:.0f}s",
                        flush=True,
                    )
    return rows


def _floor_decided(row: FloorRow) -> bool:
    if math.isnan(row.mean_net_r) or math.isnan(row.se_net_r):
        return False
    return abs(row.mean_net_r) > max(2 * row.se_net_r, NOISE_R)


def render_floor_summary(rows: Sequence[FloorRow]) -> str:
    index = {(r.floor, r.hold, r.threshold, r.segment): r for r in rows}
    out = [
        "# WAN-430 §2 — 15m 손절폭 하한을 내리면 (사용자 질문 2026-09-24)",
        "",
        "🚨 **왜 물었나**: 채택 하한(3~4%)에서 15m은 **6년 × 31종목에 13~20거래**(종목당 0.6)뿐이고"
        " **BTC는 0건**이다. BTC 15m 손절폭은 **중앙 0.398% · p99 2.15%**라(실측) 하한 4%는 그"
        " 분포의 **상위 1%보다도 바깥**이다 — 「선별」이 아니라 **사실상 꺼진 상태**다."
        " ⚠️ **「최댓값보다 위」는 아니다** — 4%를 넘는 15m 존이 실제로 있고(아래 표의 하한 4% 행)"
        " 그것이 이 격자의 20거래다. 두 말은 다르다.",
        "",
        "📌 **팔 후보는 하한 0.5%로 한 번만 만들고 위로 올려 걸렀다** — 그래서 하한 점을 늘리는"
        " 것이 공짜다(배치 조합당 2초). 🚨 0.5%보다 낮은 하한은 이 표에 넣을 수 없다(후보가 애초에"
        " 없어 조용히 0.5% 결과가 나온다).",
        "",
        "🚨 **판정 자는 거래당 net R ± 2σ이고 거래 수가 아니다** — 하한을 내리면 거래는 반드시"
        " 늘므로 건수만 보면 항상 낮은 하한이 이겨 보인다.",
        "",
    ]
    for segment in ("full", "is", "oos_warm"):
        part = [r for r in rows if r.segment == segment]
        if not part:
            continue
        out += [
            f"## `{segment}`",
            "",
            "| 하한 | 보유 | %K | 거래 | 손절폭 중앙 | 거래당 net R | 2σ | 부호"
            " | 비용 R | gross R |",
            "| --: | -- | -- | --: | --: | --: | --: | -- | --: | --: |",
        ]
        for floor in sorted({r.floor for r in part}):
            for thr in THRESHOLDS:
                for hold in HOLD_BARS:
                    row = index.get((floor, hold, thr, segment))
                    if row is None or not row.num_trades:
                        continue
                    k = "끔" if thr is None else f"<{thr:.0f}"
                    sign = "—" if not _floor_decided(row) else ("＋" if row.mean_net_r > 0 else "−")
                    out.append(
                        f"| {floor:.1%} | ts{hold} | {k} | {row.num_trades:,} | "
                        f"{row.median_stop_width:.3%} | {row.mean_net_r:+.4f} | "
                        f"±{2 * row.se_net_r:.4f} | {sign} | {row.mean_cost_r:.4f} | "
                        f"{row.mean_gross_r:+.4f} |"
                    )
        out.append("")
    decided = [r for r in rows if r.segment == "oos_warm" and _floor_decided(r)]
    positive = [r for r in decided if r.mean_net_r > 0]
    out += [
        "## 판정",
        "",
        f"- 뒷구간 **{len(decided)}줄**에서 부호가 정해졌고 그중 **양수 {len(positive)}줄**이다"
        f" (전체 {len([r for r in rows if r.segment == 'oos_warm'])}줄).",
        "- 🚨 **채택 권고가 아니다** — **TF마다 다른 하한은 새 자유 파라미터**이고, 이 저장소가"
        " 반복해 경계한 자리다(WAN-108/143/161). 이 표는 *「하한이 15m을 끄고 있다」*와 *「내리면"
        " 그 대가가 얼마인가」*까지만 답한다.",
        "- ⚠️ **argmax를 고르지 말 것**(WAN-161) · 전부 `pen_5bp` × 같은 분 익절 금지 위 값 ·"
        " 매칭 널 미측정 · 팔 자체가 WAN-423/424에서 **채택 권고 없음**으로 닫혔다.",
        "",
    ]
    return "\n".join(out) + "\n"


def _main_floor_sweep(args: argparse.Namespace) -> int:
    """§2 — 15m 전용. 🚨 팔 후보를 `FLOOR_SWEEP_MIN`으로 **한 번만** 만든다."""
    if args.from_csv:
        rows = [FloorRow(**r) for r in pd.read_csv(FLOOR_CSV).to_dict("records")]
    else:
        symbols = ("BTC/USDT:USDT",) if args.pilot else tuple(SYMBOLS)
        t0 = time.monotonic()
        payloads = build_base_payloads(
            jobs=args.jobs,
            payload_dir=args.payload_dir,
            symbols=symbols,
            timeframes=(NEW_TIMEFRAME,),
        )
        print(
            f"[wan430-floor] base 후보 {len(payloads)}칸: {time.monotonic() - t0:.0f}s", flush=True
        )
        t1 = time.monotonic()
        cells = build_arm_cells(payloads, jobs=args.jobs, min_width=FLOOR_SWEEP_MIN)
        print(
            f"[wan430-floor] 팔 후보 {len(cells)}칸(하한 {FLOOR_SWEEP_MIN:.1%}): "
            f"{time.monotonic() - t1:.0f}s",
            flush=True,
        )
        rows = floor_rows(payloads, cells)
        if not args.pilot:
            FLOOR_CSV.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([r.model_dump() for r in rows]).to_csv(FLOOR_CSV, index=False)
    summary = render_floor_summary(rows)
    if not args.pilot:
        FLOOR_SUMMARY_MD.write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
