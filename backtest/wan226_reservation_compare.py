"""WAN-226 §3 — 현행(24봉 만료) vs 형성-예약(무기한 대기) 대조.

## 무엇을 재나

WAN-223 §1 census가 만료 후 재탭 실패로 놓친 재진입의 **크기**를 쟀다면(전 구간), 이
모듈은 그 만료를 없앤 팔이 **체결률·수익·MDD·거래 수**를 어떻게 바꾸는지를 IS/OOS(따뜻,
WAN-166)로 나눠 대조한다 — 채택 판단이 아니라 크기 조사의 IS/OOS 판(版)이다.

* **현행 팔** = `limit_valid_bars=24`(채택 기본값).
* **형성-예약 팔** = `limit_valid_bars=None`(무기한 대기 = 형성-즉시 예약의 체결 대리,
  WAN-223 §1이 못 박은 정의).

두 팔은 같은 오더블록·같은 셋업 풀을 공유하고 유효기간만 다르다(WAN-223 census와 같은
자). 형성 배치가 탭 배치보다 더 잡는 「형성 시 이미 존 안」 케이스는 census/토대 문서가
의도적으로 대리로 접었으므로, 이 대조의 체결·수익 효과는 새 엔진 코드 없이 `None` 팔로
측정된다(설계 근거는 `docs/decisions/wan226.md`).

## 성격

측정 전용. `backtest.run`의 `--limit-valid-bars 24,none --oos-warm` 축을 그대로 쓴다
(per-cell 단일 포지션으로 자동 접힘) — 이 모듈은 그 CSV를 읽어 팔-대-팔 표로 **피벗만**
한다. 렌즈 `baseline` 단독(WAN-128) · 못 박은 6년 창(WAN-182) · 기본값·토대 불변
(`ALPHABLOCK_LIVE_TRADING=false` 유지). ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이라
「체결률↑ = 좋다」가 **아니다**(WAN-96/222/223: 체결↑ ≠ 수익↑). 「엣지 없음」
(WAN-84/88/111/114/124/151) 불변.

## 재현

```
uv run python -m backtest.run --tf 4h,1h --limit-valid-bars 24,none --oos-warm \
    --format csv --out backtest/reports/wan226_reservation_compare.csv --jobs 6
uv run python -m backtest.wan226_reservation_compare      # CSV → 요약 md
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest.harness import ROW_COLUMNS, RunRow, normalize_symbol
from backtest.sweep import timeframe_to_ms

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CSV = REPORTS_DIR / "wan226_reservation_compare.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan226_reservation_compare_summary.md"

#: 표에 싣는 구간 — IS와 따뜻한 OOS(WAN-166 주 수치). `full`·차가운 `oos`는 CSV에 있어도
#: 표에서 접는다(이슈 완료기준이 IS/OOS이고, 따뜻한 OOS가 정본이다).
SEGMENTS: tuple[str, ...] = ("is", "oos_warm")

#: 채택 유효기간(현행 팔). CSV의 `limit_valid_bars` 값.
CURRENT_LVB = 24

#: 신규 3종목 — 펀딩 0행이라 `total_return` 열이 낙관적(WAN-178 백필 전). 체결·거래 수는
#: 펀딩과 무관하므로 대리를 얹지 않는다(census 관행). 표에서 †로 표시한다.
FUNDING_GAP_SYMBOLS: frozenset[str] = frozenset(
    normalize_symbol(s) for s in ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
)


def _is_current(row: RunRow) -> bool:
    return row.limit_valid_bars == CURRENT_LVB


def _is_reservation(row: RunRow) -> bool:
    return row.limit_valid_bars is None


def rows_from_csv(path: Path) -> list[RunRow]:
    """`backtest.run` CSV를 `RunRow`로 되읽는다.

    빈 칸(→ `NaN`)은 `None`으로 되돌린다 — `limit_valid_bars` 빈 칸이 곧 무기한(형성-예약)
    팔이라 이 왕복이 두 팔을 가르는 핵심이다(`RunRow._empty_leverage_is_none`이 그 필드를
    이미 처리하지만, 다른 선택 열의 `NaN`도 함께 없애 pydantic 정수 필드를 깨지 않는다).
    """
    frame = pd.read_csv(path)
    missing = [c for c in ROW_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"CSV에 필요한 열이 없습니다: {missing}")
    rows: list[RunRow] = []
    for record in frame.to_dict(orient="records"):
        clean = {
            k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in record.items()
        }
        rows.append(RunRow.model_validate(clean))
    return rows


class _ArmPair:
    """한 (심볼, TF, 구간)의 두 팔(현행 24봉 · 형성-예약 무기한)."""

    __slots__ = ("symbol", "timeframe", "segment", "current", "reservation")

    def __init__(self, symbol: str, timeframe: str, segment: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.segment = segment
        self.current: RunRow | None = None
        self.reservation: RunRow | None = None

    @property
    def complete(self) -> bool:
        return self.current is not None and self.reservation is not None


def pair_arms(rows: Sequence[RunRow]) -> dict[tuple[str, str, str], _ArmPair]:
    """행들을 (심볼, TF, 구간) 키로 묶어 두 팔을 채운다."""
    pairs: dict[tuple[str, str, str], _ArmPair] = {}
    for row in rows:
        if row.segment not in SEGMENTS:
            continue
        if not (_is_current(row) or _is_reservation(row)):
            continue
        key = (row.symbol, row.timeframe, row.segment)
        pair = pairs.get(key)
        if pair is None:
            pair = _ArmPair(row.symbol, row.timeframe, row.segment)
            pairs[key] = pair
        if _is_current(row):
            pair.current = row
        else:
            pair.reservation = row
    return pairs


def symbol_mean_return_delta(pairs: Sequence[_ArmPair]) -> float:
    """형성-예약 − 현행의 심볼평균 total_return 델타(%p). §2 핵심: 만료를 없애면 수익이
    느는가. 대상이 없으면 0.0."""
    deltas = [
        (p.reservation.total_return - p.current.total_return) * 100.0
        for p in pairs
        if p.complete and p.current is not None and p.reservation is not None
    ]
    return sum(deltas) / len(deltas) if deltas else 0.0


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "—"


def _segment_table(pairs: Sequence[_ArmPair], timeframe: str, segment: str) -> list[str]:
    scoped = sorted(
        (p for p in pairs if p.timeframe == timeframe and p.segment == segment and p.complete),
        key=lambda p: p.symbol,
    )
    lines = [
        f"#### {timeframe} · {segment}",
        "",
        "| 심볼 | 체결률(24→∞) | 체결(24→∞) | 거래(24→∞) | 수익%(24→∞) | MDD%(24→∞) |",
        "| -- | --: | --: | --: | --: | --: |",
    ]
    for p in scoped:
        cur, res = p.current, p.reservation
        assert cur is not None and res is not None
        fund = "†" if p.symbol in FUNDING_GAP_SYMBOLS else ""
        d_ret = (res.total_return - cur.total_return) * 100.0
        lines.append(
            f"| {_short(p.symbol)}{fund} | {_pct(cur.fill_rate)}→{_pct(res.fill_rate)} | "
            f"{cur.num_filled}→{res.num_filled} | {cur.num_trades}→{res.num_trades} | "
            f"{cur.total_return * 100:.1f}%→{res.total_return * 100:.1f}% "
            f"(**{d_ret:+.1f}%p**) | "
            f"{cur.max_drawdown * 100:.1f}%→{res.max_drawdown * 100:.1f}% |"
        )
    if scoped:
        delta = symbol_mean_return_delta(scoped)
        tot_cur_trades = sum(p.current.num_trades for p in scoped if p.current is not None)
        tot_res_trades = sum(p.reservation.num_trades for p in scoped if p.reservation is not None)
        lines += [
            "",
            f"**{timeframe} · {segment} 합계**: 거래 {tot_cur_trades}→{tot_res_trades}건 · "
            f"심볼평균 total_return 델타(형성-예약 − 현행) **{delta:+.2f}%p**.",
        ]
    return lines


def build_summary_markdown(rows: Sequence[RunRow], *, cells_csv: Path) -> str:
    pairs_map = pair_arms(rows)
    pairs = list(pairs_map.values())
    timeframes = sorted(
        {p.timeframe for p in pairs}, key=lambda t: timeframe_to_ms(t), reverse=True
    )
    lines = [
        "# WAN-226 §3 — 현행(24봉 만료) vs 형성-예약(무기한 대기) 대조",
        "",
        "**성격** 측정 전용. `limit_valid_bars=24`(현행) vs `=None`(형성-즉시 예약의 체결 "
        "대리, WAN-223 §1 정의) 두 팔을 같은 좌표에서 `--oos-warm`(WAN-166)으로 대조한다. "
        "렌즈 `baseline` 단독 · 못 박은 6년 창(WAN-182) · 기본값·토대 불변.",
        "",
        "⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이다 — `None` 팔은 주문을 더 오래 "
        "걸어 두므로 체결이 늘지만, 그 늘어난 체결이 유독 '스치듯 닿은 체결'에 실릴 수 있다"
        "(WAN-96/222/223: 체결↑ ≠ 수익↑). 「엣지 없음」(WAN-84/88/111/114/124/151) 불변. "
        "†=신규 종목(펀딩 0행 → total_return 낙관, 체결·거래 수엔 무관).",
        "",
        f"재현: `uv run python -m backtest.run --tf 4h,1h --limit-valid-bars 24,none "
        f"--oos-warm --format csv --out {cells_csv} --jobs 6` → "
        f"`uv run python -m backtest.wan226_reservation_compare`. 원자료: `{cells_csv}`.",
        "",
    ]
    for tf in timeframes:
        for segment in SEGMENTS:
            lines += _segment_table(pairs, tf, segment)
            lines.append("")
    # 전체 델타(따뜻한 OOS 주 수치)
    oos_pairs = [p for p in pairs if p.segment == "oos_warm" and p.complete]
    is_pairs = [p for p in pairs if p.segment == "is" and p.complete]
    is_delta = symbol_mean_return_delta(is_pairs)
    oos_delta = symbol_mean_return_delta(oos_pairs)
    lines += [
        "## 판정 축 — 만료를 없애면 수익이 느는가",
        "",
        f"심볼평균 total_return 델타(형성-예약 − 현행): **IS {is_delta:+.2f}%p "
        f"· 따뜻한 OOS {oos_delta:+.2f}%p**(전 TF·전 종목 평균).",
        "",
        "⚠️ 이 표는 **크기 조사이지 채택 근거가 아니다** — 형성-예약 채택(주문 생성 시점 "
        "탭→형성 전환)은 재-베이스라인 = 사용자 결정이다(`docs/decisions/wan226.md`). 체결이 "
        "늘어도 수익이 종목에 갈리면(WAN-223 §1의 LINK/BNB/DOGE) GO 근거가 약하다. 「가까운 "
        "주문 우선」 배분(층 2)은 fill-replay 북이 표현 못 하는 resting-order sim이라 이 "
        "이슈에서 구현하지 않는다(WAN-180상 여지 ~nil).",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-226 §3 형성-예약 대조 요약")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[wan226] CSV가 없습니다: {csv_path} — 먼저 backtest.run 격자를 돌리세요.")
        return 1
    rows = rows_from_csv(csv_path)
    if not rows:
        print("[wan226] 행이 없습니다.")
        return 1
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_summary_markdown(rows, cells_csv=csv_path), encoding="utf-8")
    print(f"[wan226] §3 요약 {len(rows)}행 → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
