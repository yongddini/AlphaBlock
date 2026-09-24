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
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.wan169_leverage_book import CellPayload
from backtest.wan424_stoch_ob_arm import (
    DEFAULT_PAYLOAD_DIR,
    FLOORS,
    HOLD_BARS,
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
    reference_check,
)

__all__ = [
    "GRID_CSV",
    "NEW_TIMEFRAME",
    "SCOPE_ALL",
    "SCOPE_NO_15M",
    "SUMMARY_MD",
    "TIMEFRAMES_10",
    "ScopedRow",
    "build_arm_cells",
    "build_base_payloads",
    "filtered_payloads",
    "gate_lines",
    "place",
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

GRID_CSV = Path("backtest/reports/wan430_stoch_arm_15m.csv")
SUMMARY_MD = Path("backtest/reports/wan430_stoch_arm_15m_summary.md")

#: 「0과 구분되지 않는다」 규약 폭(WAN-366/370).
NOISE_R = 0.005


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


def gate_lines(rows: Sequence[ScopedRow], *, timeframes: Sequence[str]) -> list[str]:
    """관문 ③ — 「TF 몇 개 중 몇 개가 뒷구간 양수」를 **10TF 기준**으로 다시 낸다.

    🚨 관문 ①(문턱 단조)·②(하한 구간 전체 양수)·④(종목 LOO)는 **WAN-423 §6이 자기 좌표에서
    낸 것**이고 이 모듈은 그 셋을 다시 돌리지 않는다(축이 TF 하나라 격자를 늘리면 축이 둘
    움직인다, WAN-394). ③만 TF에 직접 걸리므로 여기서 갱신한다 — 안 하면 「9개 중 8개」라는
    문장이 10TF 표 옆에 낡은 채로 남는다.
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
                positive = [r for r in got if r.mean_net_r > NOISE_R]
                fifteen = index.get((NEW_TIMEFRAME, hold, floor, thr, "oos_warm"))
                k = "끔" if thr is None else f"<{thr:.0f}"
                mark = (
                    "—"
                    if fifteen is None
                    else ("양수" if fifteen.mean_net_r > NOISE_R else "음수/0")
                )
                lines.append(
                    f"- ts{hold} · 하한{floor:.0%} · %K{k}: **{len(positive)}/{len(got)} TF** "
                    f"뒷구간 양수 · 15m **{mark}**"
                    + (
                        ""
                        if fifteen is None
                        else f"({fifteen.mean_net_r:+.4f}R · {fifteen.num_trades}거래)"
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
        *gate_lines(rows, timeframes=timeframes),
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
    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
