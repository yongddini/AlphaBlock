"""WAN-388 §1 — 존 병합 × 재탭 인구조사(관문).

§2 격자(네 팔 × 48칸 = 수십 시간)를 돌기 전에 **싼 것부터 재서** 착수 여부를 정한다.
이 파트는 **탐지만** 하므로 1분봉도 서브스텝 시뮬도 필요 없다 — 분 단위로 끝난다.

무엇을 세나(이슈 §1 1·2항):

1. 병합이 **실제로 무는** 탭 비율 · 클러스터 구성원 수 분포 · **폭 배수 분포**
2. 재탭이 전체 탭에서 차지하는 몫

관문(이슈): **유니버스에서 병합이 무는 비율이 5% 미만이면 §2를 돌리지 않는다.**
(나머지 관문 「북 층 재탭 거래가 5% 미만」은 후보·배치가 있어야 답이 나오므로 §1b
`wan388_merge_x_retap.py --part census-book`이 arm 1을 돌 때 함께 낸다 — 그 팔은 §2의
검산 팔이라 어차피 돌려야 한다.)

🚨 **정의를 못 박는다** — 나중에 다른 숫자와 섞이지 않게:

* **탭** = 시그널 한 건. `retap_signals`가 무효화 전까지의 **모든** 탭이고(채택 규칙
  `retap_mode="every_tap"`이 소비한다), `signals`가 존당 **첫 탭 1회**다
  (`retap_mode="once"`가 소비한다). 그래서 **재탭 = 모든 탭 − 첫 탭**이다.
* **병합이 문다** = 그 탭의 존이 구성 존 **2개 이상**을 접은 클러스터다
  (`OrderBlock.num_component_obs > 1`). 겹치는 존이 없으면 병합은 아무 일도 안 한다.
* **폭 배수** = 병합 존 높이 ÷ 그 클러스터 **구성 존 높이의 중앙값**. 분모를 평균이
  아니라 중앙값으로 두는 것은 얇은 존 하나가 배수를 부풀리는 것을 막기 위해서다.
  ⚠️ 무는 탭에서만 잰다(안 무는 탭은 정의상 1.00배라 섞으면 분포가 1로 눌린다).

⚠️ **측정 전용이다** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()`
기본값을 하나도 안 바꾼다. 채택은 재-베이스라인 = 사용자 결정이다(WAN-149 `combine_obs
=False` · WAN-123 `retap_mode="every_tap"` 둘 다 **불변**).

⚠️ 이 표는 **탐지 층**의 수다 — 「탭 −38.5%」가 「북 거래 −38.5%」를 뜻하지 않는다
(상당수 탭은 이미 `skipped_cell_busy`로 버려진다). 손익 판정은 §2 북에서만 낸다(WAN-341).

재현::

    uv run python -m backtest.wan388_merge_retap_census
    uv run python -m backtest.wan388_merge_retap_census --from-csv   # 요약만
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.run import parse_date_ms
from strategy.models import OrderBlock, OrderBlockParams, OrderBlockResult, OrderBlockSignal

REPORTS_DIR = Path("backtest/reports")
CENSUS_CSV_PATH = REPORTS_DIR / "wan388_merge_retap_census.csv"
SUMMARY_PATH = REPORTS_DIR / "wan388_merge_retap_census_summary.md"

#: 관문(이슈 §1): 유니버스에서 병합이 무는 탭 비율이 이 선 미만이면 §2를 돌리지 않는다.
#: 상수 + 테스트로 못 박는다 — 결과를 보고 선을 옮기는 것을 막기 위해서다.
BITE_GATE = 0.05

#: 채택 규칙(불변). 이 모듈은 두 값을 **축으로만** 쓰고 기본값을 안 건드린다.
ADOPTED_COMBINE_OBS = False
ADOPTED_RETAP_MODE = "every_tap"


class CensusRow(BaseModel):
    """칸 하나(심볼 × TF)의 탐지 층 인구조사."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    num_bars: int
    # 분리(채택) 팔
    split_zones: int
    split_first_taps: int
    split_all_taps: int
    split_retaps: int
    split_retap_share: float
    # 병합 팔
    merged_zones: int
    merged_first_taps: int
    merged_all_taps: int
    merged_retaps: int
    merged_retap_share: float
    # 병합이 무는 정도
    bite_rate: float
    """병합 팔의 **모든 탭** 중 구성 존 2개 이상 클러스터에서 난 탭의 비율."""
    bite_taps: int
    cluster_members_p50: float
    cluster_members_p90: float
    cluster_members_max: int
    width_mult_p50: float
    width_mult_p90: float
    # 병합이 탭을 얼마나 줄이나
    all_tap_change: float
    first_tap_change: float
    retap_change: float


def _p(values: Sequence[float], q: float) -> float:
    """분위수. 표본이 비면 0.0(없는 값을 지어내지 않는다)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    return float(statistics.quantiles(ordered, n=100, method="inclusive")[int(q * 100) - 1])


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _change(new: int, old: int) -> float:
    return (new - old) / old if old else 0.0


def _member_heights(signal: OrderBlockSignal, archive: Sequence[OrderBlock]) -> list[float]:
    """이 시그널이 속한 클러스터의 **구성 존 높이**들.

    `zone_key`는 탐지 아카이브 인덱스 집합이다(WAN-83) — 병합 존 값 객체는 재계산마다
    새로 만들어져 객체 동일성으로 추적할 수 없어서 이 필드가 있다.
    """
    if not signal.zone_key:
        return []
    return [
        archive[i].top - archive[i].bottom for i in sorted(signal.zone_key) if 0 <= i < len(archive)
    ]


def census_for_cell(
    symbol: str,
    timeframe: str,
    *,
    start: str,
    end: str,
) -> CensusRow:
    """한 칸의 인구조사. 탐지만 하므로 1분봉·펀딩을 안 읽는다."""
    market = harness.load_market_data(
        symbol,
        timeframe,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        need_1m=False,
        funding=False,
    )
    if market.empty:
        raise ValueError(f"{symbol} {timeframe}: 데이터가 없습니다(창 확인).")

    split: OrderBlockResult = harness.detect_order_blocks(
        market, OrderBlockParams(combine_obs=False)
    )
    merged: OrderBlockResult = harness.detect_order_blocks(
        market, OrderBlockParams(combine_obs=True)
    )

    split_first, split_all = len(split.signals), len(split.retap_signals)
    merged_first, merged_all = len(merged.signals), len(merged.retap_signals)

    members: list[float] = []
    widths: list[float] = []
    bites = 0
    for sig in merged.retap_signals:
        ob = sig.order_block
        members.append(float(ob.num_component_obs))
        if ob.num_component_obs <= 1:
            continue
        bites += 1
        heights = _member_heights(sig, merged.order_blocks)
        merged_height = ob.top - ob.bottom
        median_member = statistics.median(heights) if heights else 0.0
        if median_member > 0:
            widths.append(merged_height / median_member)

    # 병합 팔의 「존 수」는 클러스터 수다 — 아카이브(병합 전) 길이가 아니라 첫 탭이 붙은
    # 서로 다른 `zone_key` 개수로 센다. 안 그러면 두 팔의 「존 수」가 같은 수가 된다.
    merged_clusters = len({sig.zone_key for sig in merged.signals if sig.zone_key is not None})

    return CensusRow(
        symbol=harness.normalize_symbol(symbol),
        timeframe=timeframe,
        num_bars=len(market.htf_df),
        split_zones=len(split.order_blocks),
        split_first_taps=split_first,
        split_all_taps=split_all,
        split_retaps=split_all - split_first,
        split_retap_share=_share(split_all - split_first, split_all),
        merged_zones=merged_clusters or merged_first,
        merged_first_taps=merged_first,
        merged_all_taps=merged_all,
        merged_retaps=merged_all - merged_first,
        merged_retap_share=_share(merged_all - merged_first, merged_all),
        bite_rate=_share(bites, merged_all),
        bite_taps=bites,
        cluster_members_p50=_p(members, 0.50),
        cluster_members_p90=_p(members, 0.90),
        cluster_members_max=int(max(members)) if members else 0,
        width_mult_p50=_p(widths, 0.50),
        width_mult_p90=_p(widths, 0.90),
        all_tap_change=_change(merged_all, split_all),
        first_tap_change=_change(merged_first, split_first),
        retap_change=_change(merged_all - merged_first, split_all - split_first),
    )


def run_census(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    log: bool = True,
) -> list[CensusRow]:
    rows: list[CensusRow] = []
    cells = [(s, tf) for s in symbols for tf in timeframes]
    for idx, (symbol, timeframe) in enumerate(cells, start=1):
        if log:
            print(f"[{idx}/{len(cells)}] {symbol} {timeframe}", flush=True)
        rows.append(census_for_cell(symbol, timeframe, start=start, end=end))
    return rows


def rows_to_frame(rows: Sequence[CensusRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path = CENSUS_CSV_PATH) -> list[CensusRow]:
    frame = pd.read_csv(path)
    return [CensusRow.model_validate(record) for record in frame.to_dict("records")]


def _weighted(rows: Sequence[CensusRow], part: str, whole: str) -> float:
    """탭 가중 비율 — 칸마다의 비율을 단순 평균하면 얇은 칸이 과대 대표된다."""
    num = sum(getattr(row, part) for row in rows)
    den = sum(getattr(row, whole) for row in rows)
    return _share(num, den)


def gate_verdict(rows: Sequence[CensusRow]) -> tuple[bool, str]:
    """관문 판정. 「돌린다/안 돌린다」를 **코드가** 낸다(사람이 보고 정하지 않는다)."""
    if not rows:
        return False, "표본 없음 — 판정하지 않는다."
    bite = _weighted(rows, "bite_taps", "merged_all_taps")
    if bite < BITE_GATE:
        return (
            False,
            f"병합이 무는 탭이 {bite:.2%}로 관문({BITE_GATE:.0%}) 미만 — §2를 돌리지 않는다.",
        )
    return (
        True,
        f"병합이 무는 탭이 {bite:.2%}로 관문({BITE_GATE:.0%})을 넘는다 — §2 착수 근거가 된다"
        "(나머지 관문 「북 층 재탭 거래 5%」는 arm 1에서 낸다).",
    )


def build_summary_markdown(rows: Sequence[CensusRow], *, elapsed: float | None = None) -> str:
    out: list[str] = []
    out.append("# WAN-388 §1 — 존 병합 × 재탭 인구조사 (탐지 층 · 관문)")
    out.append("")
    out.append(
        "⚠️ **측정 전용** — `OrderBlockParams(combine_obs=False)`(WAN-149) · "
        '`ConfluenceParams(retap_mode="every_tap")`(WAN-123) 채택 기본값 **불변**. '
        "채택은 재-베이스라인 = 사용자 결정."
    )
    out.append("")
    out.append(
        "🚨 이 표는 **탐지 층**의 수다 — 「탭이 N% 준다」가 「북 거래가 N% 준다」를 뜻하지 "
        "않는다(상당수 탭은 이미 슬롯 점유로 버려진다). 손익 판정은 §2 북에서만 낸다(WAN-341)."
    )
    out.append("")

    passed, note = gate_verdict(rows)
    out.append("## 0. 관문 판정")
    out.append("")
    out.append(f"- **{'통과' if passed else '미통과'}** — {note}")
    out.append("")

    out.append("## 1. 유니버스 합계 (탭 가중)")
    out.append("")
    total_split_all = sum(r.split_all_taps for r in rows)
    total_split_first = sum(r.split_first_taps for r in rows)
    total_merged_all = sum(r.merged_all_taps for r in rows)
    total_merged_first = sum(r.merged_first_taps for r in rows)
    out.append("| 관측 | 값 |")
    out.append("| -- | -- |")
    out.append(f"| 칸 수 | {len(rows)} |")
    out.append(f"| 분리 팔 모든 탭 | {total_split_all:,} |")
    out.append(f"| ↳ 첫탭 | {total_split_first:,} |")
    split_retap_count = total_split_all - total_split_first
    out.append(
        f"| ↳ 재탭 | {split_retap_count:,} ({_share(split_retap_count, total_split_all):.2%}) |"
    )
    merged_all_delta = _change(total_merged_all, total_split_all)
    out.append(f"| 병합 팔 모든 탭 | {total_merged_all:,} ({merged_all_delta:+.2%}) |")
    out.append(
        f"| ↳ 첫탭 | {total_merged_first:,} "
        f"({_change(total_merged_first, total_split_first):+.2%}) |"
    )
    merged_retaps = total_merged_all - total_merged_first
    split_retaps = total_split_all - total_split_first
    out.append(f"| ↳ 재탭 | {merged_retaps:,} ({_change(merged_retaps, split_retaps):+.2%}) |")
    out.append(
        f"| **병합이 무는 탭 비율** | **{_weighted(rows, 'bite_taps', 'merged_all_taps'):.2%}** |"
    )
    out.append("")

    out.append("## 2. TF별")
    out.append("")
    out.append(
        "| TF | 분리 모든탭 | 재탭 몫 | 병합 모든탭 | 재탭 변화 | 무는 비율 "
        "| 폭 배수 p50 | 폭 배수 p90 |"
    )
    out.append("| -- | --: | --: | --: | --: | --: | --: | --: |")
    for tf in sorted({row.timeframe for row in rows}, key=harness.DEFAULT_TIMEFRAMES.index):
        sub = [row for row in rows if row.timeframe == tf]
        split_all = sum(r.split_all_taps for r in sub)
        split_re = sum(r.split_retaps for r in sub)
        merged_all = sum(r.merged_all_taps for r in sub)
        merged_re = sum(r.merged_retaps for r in sub)
        w50 = [r.width_mult_p50 for r in sub if r.width_mult_p50 > 0]
        w90 = [r.width_mult_p90 for r in sub if r.width_mult_p90 > 0]
        out.append(
            f"| {tf} | {split_all:,} | {_share(split_re, split_all):.1%} | {merged_all:,} | "
            f"{_change(merged_re, split_re):+.1%} | "
            f"{_weighted(sub, 'bite_taps', 'merged_all_taps'):.1%} | "
            f"{statistics.median(w50) if w50 else 0.0:.2f}배 | "
            f"{statistics.median(w90) if w90 else 0.0:.2f}배 |"
        )
    out.append("")

    out.append("## 3. 클러스터 구성원 수")
    out.append("")
    members_p50 = [r.cluster_members_p50 for r in rows]
    members_p90 = [r.cluster_members_p90 for r in rows]
    out.append(
        f"- 칸별 중앙값의 중앙값 **{statistics.median(members_p50):.2f}** · "
        f"p90의 중앙값 {statistics.median(members_p90):.2f} · "
        f"최대 {max((r.cluster_members_max for r in rows), default=0)}"
    )
    out.append(
        "- 🚨 **겹침이 드물다는 뜻이다** — 클러스터 대부분이 구성원 1개(= 병합이 아무 일도 "
        "안 한다)이고, 그래서 첫탭 수가 두 팔에서 거의 같다."
    )
    out.append("")

    out.append("## 4. 읽는 법 · 경고")
    out.append("")
    out.append(
        "- **병합이 줄이는 것은 재탭이지 첫탭이 아니다** — 클러스터 수가 첫탭 수와 맞먹는다."
    )
    out.append(
        "- ⚠️ **폭 배수를 「손절폭이 그만큼 넓어진다」로 바로 읽지 말 것** — 진입가는 볼린저가 "
        "재산정하고(WAN-95/132) 병합은 클러스터 **상단**에서 사게 만든다. 실제 손절폭·진입가 "
        "이동은 §2가 낸다."
    )
    out.append(
        '- ⚠️ **재탭 차단(`retap_mode="once"`)은 재진입(WAN-273)과 다른 축이다** — 재탭을 꺼도 '
        "「익절 후 같은 존 재무장」은 그대로 돈다."
    )
    out.append(
        "- ⚠️ **WAN-149 §3의 옛 판정과 셀을 비교하지 말 것** — 그 표는 옛 엔진(소급 취소 · 존폭 "
        "필터 켬 · 익절 테이커)이고 `total_return`으로 쟀다."
    )
    if elapsed is not None:
        out.append("")
        out.append(f"실측 소요: {elapsed / 60:.1f}분")
    out.append("")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    import time

    parser = argparse.ArgumentParser(description="WAN-388 §1 존 병합 × 재탭 인구조사")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    args = parser.parse_args(argv)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_csv:
        rows = rows_from_csv()
        elapsed = None
    else:
        started = time.monotonic()
        rows = run_census(
            [s for s in args.symbols.split(",") if s],
            [t for t in args.timeframes.split(",") if t],
            start=args.start,
            end=args.end,
        )
        elapsed = time.monotonic() - started
        rows_to_frame(rows).to_csv(CENSUS_CSV_PATH, index=False)

    SUMMARY_PATH.write_text(build_summary_markdown(rows, elapsed=elapsed), encoding="utf-8")
    passed, note = gate_verdict(rows)
    print(f"\n관문: {'통과' if passed else '미통과'} — {note}")
    print(f"CSV: {CENSUS_CSV_PATH}\n요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
