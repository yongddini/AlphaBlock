"""WAN-372: 오더블록에 진입한 순간의 MACD 히스토그램 색 — 네 색깔별 성적 (관측 전용).

## 한 줄

이미 하고 있는 채택 북 거래에 「그때 MACD 색이 무엇이었나」를 열로 붙여 성적을 나눠 본다.
**매매를 하나도 바꾸지 않는다** — 색으로 거르는 팔(필터)은 이 이슈 범위 밖이다.

## 왜 새 격자가 아닌가

WAN-370이 채택 북의 **거래별 내역**을 새 비용 회계로 냈다. 각 거래의 체결 시각에 그 시점
MACD 색을 붙이면 되므로 **새 격자를 안 돌린다** — 후보를 한 번 만들고 배치를 한 번 하는,
채택 북 **한 팔**의 비용이 전부다.

## 색 정의 (Pine 원본 그대로 · `strategy.realtime_macd`가 정본)

```
color = hist >= 0 ? (hist[1] < hist ? #26A69A : #B2DFDB)
                  : (hist[1] < hist ? #FFCDD2 : #FF5252)
```

| 색 | 조건 | 뜻 |
| -- | -- | -- |
| 진한 초록 `#26A69A` | `hist ≥ 0` and `hist > hist[1]` | 상승 가속 |
| 연한 초록 `#B2DFDB` | `hist ≥ 0` and `hist ≤ hist[1]` | 상승 둔화 |
| 연한 빨강 `#FFCDD2` | `hist < 0` and `hist > hist[1]` | 하락 둔화 |
| 진한 빨강 `#FF5252` | `hist < 0` and `hist ≤ hist[1]` | **하락 가속** ← 사용자 관심 |

📌 **사용자는 진한 빨강만 물었지만 네 색 전부 낸다** — 하나만 보면 그게 좋은지 나쁜지
비교할 기준이 없다. 📌 **분포 자체가 첫 산출물이다**: 오더블록 롱은 존에 지정가를 걸고
가격이 **내려와야** 체결되므로 체결 순간은 구조적으로 하락 중이다. **진한 빨강이 거의
전부면 선별력이 없다**(색으로 걸러 봐야 아무것도 안 걸러진다).

## 어느 시점의 MACD인가 — **체결 순간**(봉내 라이브, 사용자 확인 2026-08-27)

`strategy.realtime_macd` 모듈 독스트링이 정본이다. 요약: 탭 봉 종가는 **룩어헤드**(그 봉이
어떻게 끝날지 알아야 나온다), 직전 확정봉은 인과적이지만 **가격이 존까지 내려온 그 구간을
통째로 버린다**(볼린저에서 셋 중 제일 나빴다 — WAN-119). 세 번째 = 체결 순간의 현재가가
사용자가 트레이딩뷰에서 그때 실제로 보는 값이고 진입가 정본과 같은 자다(WAN-132).

## 자 — 거래당 net R (총수익 %가 아니다)

총수익 %는 6년 복리라 판정 자가 아니다(WAN-169/213). 색깔 비교는 **거래당 net R**
(= 실현손익 ÷ 그 거래의 리스크 금액, `book_cli.net_r`)로 낸다. ⚠️ ±0.005R 안은 「0과
구분되지 않는다」로 읽는다(WAN-366 규약).

## 🚨 네 색 중 하나는 반드시 좋아 보인다

거래당 −0.13R로 지고 있는 뭉치를 네 조각으로 나누면 **가장 나은 조각은 무조건 존재한다.**
그게 신호인지 그냥 쪼갠 결과인지는 이 표만으로 안 갈린다 — 그래서 **앞구간(`is`)에서 보고
뒷구간(`oos_warm`)에서 확인**한다. **뒷구간은 고르는 축이 아니다.**

후속(필터) 이슈로 넘어가는 기준은 **둘 다** 만족할 때다(사용자 코멘트 2026-08-27):

1. 색깔 간 거래당 net R 격차가 ±0.005R 규약 폭보다 **뚜렷하게** 클 것.
2. 앞구간에서 좋았던 색이 **뒷구간에서도** 좋을 것(뒤집히면 색이 아니라 구간 우연이다).

📌 **둘 중 하나라도 안 되면 「필터로 갈 근거 없음」으로 닫는다 — 그것도 답이다.**

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 4TF(15m·1h·2h·4h) 한 지갑 · 못 박은 6년 창 · cap_only 5배 ·
재진입 ON(band) · 유동성 한도 채택값 · `baseline` 렌즈 · 인과 취소(WAN-365) · 익절 메이커
2bp(WAN-370).

## 검산

* **(a) 관측 팔 ≡ 채택 북** — 관측이 순수하다면 이 실행의 북 행이 `book_cli.build_book_rows`
  에 채택 값을 **명시로** 넘긴 행과 같아야 한다(수익·MDD·거래 수). 0이 아니면 관측이 대상을
  바꾼 것이다.
* **(b) 색 귀속 = 전수** — 색별 거래 수의 합이 그 구간 거래 수와 같아야 한다(워밍업 포함).
* **(c) 재진입 거래도 색을 받았다** — 채택 북은 재진입 ON이라(WAN-273) 한쪽만 배선하면 색
  표가 거래의 상당 부분을 조용히 놓친다(WAN-345가 래더 축에서 겪은 실패의 관측 판). 재진입
  거래 중 색이 안 붙은 건수를 센다.

재현:

```
uv run python -m backtest.wan372_macd_color --jobs 4
uv run python -m backtest.wan372_macd_color --from-csv    # 요약만
```
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from data.models import timeframe_to_ms
from strategy.realtime_macd import COLOR_ORDER, WARMUP_LABEL, MacdColor, macd_color

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan372_macd_color.csv"
SUMMARY_PATH = REPORTS_DIR / "wan372_macd_color_summary.md"

#: 「0과 구분되지 않는다」 선 — WAN-366/370 규약 그대로.
NOISE_R = 0.005

#: 색깔 성적을 판정에 쓸 최소 거래 수(WAN-84 유효 기준). 미달 색은 「표본 미달」로 표시하고
#: 판정에서 뺀다 — 얇은 조각의 극단값이 「최선 색」으로 올라오면 판정이 통째로 흔들린다.
MIN_TRADES = 20

AXIS_OVERALL = "overall"
AXIS_TIMEFRAME = "timeframe"

#: 표·CSV의 색 순서 — 분포가 어떻든 고정이고, 워밍업이 항상 마지막이다.
BUCKET_ORDER: tuple[str, ...] = (*(c.label for c in COLOR_ORDER), WARMUP_LABEL)


# --------------------------------------------------------------------------- #
# 색 라벨
# --------------------------------------------------------------------------- #


def placement_color(placement: PlacedSetup) -> MacdColor | None:
    """이 거래의 체결 순간 색. 워밍업이라 판정 못 하면 `None`(지어내지 않는다).

    판정은 `strategy.realtime_macd.macd_color` **한 곳**에서만 한다 — 부등호를 한 칸
    옮기면 같은 봉이 다른 색이 되므로 규칙 사본을 만들지 않는다.
    """
    if placement.macd_hist is None or placement.macd_hist_prev is None:
        return None
    return macd_color(placement.macd_hist, placement.macd_hist_prev)


def placement_bucket(placement: PlacedSetup) -> str:
    color = placement_color(placement)
    return WARMUP_LABEL if color is None else color.label


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class ColorRow(BaseModel):
    """한 (구간, 축, 버킷, 색)의 분포 + 성적. 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    segment: str
    axis: str
    bucket: str
    """`overall`이면 `"전체"`, `timeframe`이면 TF 이름."""
    color: str
    """색 이름(또는 「워밍업」)."""
    num_trades: int
    share: float
    """이 버킷 안에서 이 색이 차지하는 비율(0~1) — 완료기준 1의 「몇 %」."""
    win_rate: float
    mean_net_r: float
    """거래당 net R — **판정 자**(총수익 %가 아니다)."""
    sum_net_r: float
    """이 색이 만든 net R 합 — 「크기」를 함께 봐야 평균이 얇은 조각의 산물인지 보인다."""
    sample_ok: bool
    """`MIN_TRADES` 이상인가 — 거짓이면 판정에서 뺀다."""


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _aggregate(
    pairs: Sequence[tuple[Trade, PlacedSetup]],
    *,
    segment: str,
    axis: str,
    bucket: str,
) -> list[ColorRow]:
    """한 (구간, 축, 버킷)의 색깔 행들 — 분포는 **이 버킷 안에서** 잰다."""
    total = len(pairs)
    by_color: dict[str, list[tuple[Trade, PlacedSetup]]] = {}
    for trade, placement in pairs:
        by_color.setdefault(placement_bucket(placement), []).append((trade, placement))
    rows: list[ColorRow] = []
    for label in BUCKET_ORDER:
        group = by_color.get(label, [])
        if not group and label == WARMUP_LABEL:
            # 워밍업 행은 **있을 때만** 낸다 — 없는 것이 정상이라 빈 행은 표만 늘린다.
            continue
        num = len(group)
        net_rs = [net_r(t, p) for t, p in group]
        rows.append(
            ColorRow(
                segment=segment,
                axis=axis,
                bucket=bucket,
                color=label,
                num_trades=num,
                share=(num / total) if total else 0.0,
                win_rate=(sum(1 for t, _p in group if t.is_win) / num) if num else 0.0,
                mean_net_r=(sum(net_rs) / num) if num else 0.0,
                sum_net_r=sum(net_rs),
                sample_ok=num >= MIN_TRADES,
            )
        )
    return rows


def rows_for_segment(segment: BookSegment) -> list[ColorRow]:
    """한 구간의 두 축(전체·TF) 행을 낸다."""
    pairs = segment.trades_with_placements()
    rows = _aggregate(pairs, segment=segment.segment, axis=AXIS_OVERALL, bucket="전체")
    by_tf: dict[str, list[tuple[Trade, PlacedSetup]]] = {}
    for trade, placement in pairs:
        by_tf.setdefault(placement.cell[1], []).append((trade, placement))
    for timeframe, group in sorted(by_tf.items(), key=lambda kv: timeframe_to_ms(kv[0])):
        rows += _aggregate(group, segment=segment.segment, axis=AXIS_TIMEFRAME, bucket=timeframe)
    return rows


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
) -> list[CellPayload]:
    """채택 북의 칸 후보를 만든다 — **관측만 켠 채택 좌표**(WAN-305).

    `observe_macd=True`가 base 후보와 재진입 후보 **양쪽에** 걸린다(`run_cells` → `_Task`).
    순수 관측이라 후보 집합·손익은 안 켠 실행과 비트 단위로 같다(검산 (a)가 그것을 잰다).
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        observe_macd=True,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )


def place_book(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str] = SEGMENT_ORDER,
) -> list[BookSegment]:
    """채택 북 배치 — `book_cli.run_book`과 **같은 함수·같은 인자**다."""
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def verify_adopted(
    payloads: Sequence[CellPayload],
    book: Sequence[BookSegment],
    *,
    start_ms: int,
    end_ms: int,
) -> float:
    """검산 (a) — 이 실행의 북 ≡ `build_book_rows`에 채택 값을 **명시로** 넘긴 행.

    ⚠️ 「인자 없이」가 아니다 — `build_book_rows`의 기본값은 옛 회계(중앙 핀)이고 채택 북
    (`run_book` = `backtest.run --oos-warm`)이 그 값을 항상 명시하므로, 여기서도 명시해야
    이 대조가 곧 그 회계와의 등식이 된다(WAN-370 §2-2가 경고한 함정 그대로).
    """
    from backtest.book_cli import build_book_rows

    proxied, _note = apply_funding_proxy(payloads)
    reference = {
        r.segment: r
        for r in build_book_rows(
            proxied,
            book=LeverageBookParams(),
            segments=[s.segment for s in book],
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
            take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        )
    }
    worst = 0.0
    for seg in book:
        other = reference[seg.segment]
        worst = max(
            worst,
            abs(seg.row.total_return - other.total_return),
            abs(seg.row.max_drawdown - other.max_drawdown),
            float(abs(seg.row.num_trades - other.num_trades)),
        )
    return worst


def coverage_gap(book: Sequence[BookSegment], frame: pd.DataFrame) -> float:
    """검산 (b) — 색별 거래 수의 합이 그 구간 거래 수와 같은가(최대 절대차)."""
    worst = 0.0
    for seg in book:
        counted = frame[(frame["segment"] == seg.segment) & (frame["axis"] == AXIS_OVERALL)][
            "num_trades"
        ].sum()
        worst = max(worst, float(abs(int(counted) - len(seg.outcome.trades))))
    return worst


def unlabeled_reentries(book: Sequence[BookSegment]) -> int:
    """검산 (c) — 재진입 거래 중 색이 안 붙은 건수 (WAN-345 부류의 동작 가드).

    🚨 **인자를 넘기는 줄을 보는 게 아니라 재진입 거래에 실제로 색이 붙었는지를 센다** —
    넘기는 줄만 보는 테스트는 같은 실패를 또 통과시킨다(WAN-345의 교훈).
    """
    return sum(
        1
        for seg in book
        for _t, p in seg.trades_with_placements()
        if p.is_reentry and p.macd_hist is None
    )


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    log: bool = True,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """색 분포·성적 격자 + 검산값을 낸다."""
    started = time.monotonic()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = build_payloads(symbols, timeframes, start=start, end=end, jobs=jobs)
    if log:
        print(f"[wan372] 후보 생성 {time.monotonic() - started:.0f}s", flush=True)

    book = place_book(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments)
    rows: list[ColorRow] = []
    for seg in book:
        rows += rows_for_segment(seg)
    frame = pd.DataFrame([r.model_dump() for r in rows])

    checks = {
        "adopted_identity": verify_adopted(payloads, book, start_ms=start_ms, end_ms=end_ms),
        "coverage_gap": coverage_gap(book, frame),
        "unlabeled_reentries": float(unlabeled_reentries(book)),
    }
    if log:
        print(f"[wan372] 총 {time.monotonic() - started:.0f}s · 검산 {checks}", flush=True)
    return frame, checks


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _overall(frame: pd.DataFrame, segment: str) -> pd.DataFrame:
    return frame[(frame["segment"] == segment) & (frame["axis"] == AXIS_OVERALL)]


def _judgeable(frame: pd.DataFrame, segment: str) -> pd.DataFrame:
    """판정에 쓰는 행 — 워밍업은 색이 아니고 표본 미달 색은 뺀다."""
    part = _overall(frame, segment)
    return part[(part["color"] != WARMUP_LABEL) & part["sample_ok"]]


def verdict(frame: pd.DataFrame) -> tuple[str, str]:
    """완료기준 3의 두 문장 — ① 색이 성적을 가르는가 ② 앞구간 승자가 뒷구간에도 좋은가."""
    oos = _judgeable(frame, PRIMARY_OOS)
    ins = _judgeable(frame, harness.SEGMENT_IS)
    if oos.empty:
        return (
            f"① **판정 불가** — 뒷구간(`{PRIMARY_OOS}`)에 유효 표본({MIN_TRADES}건 이상)인 "
            "색이 하나도 없다.",
            "② **판정 불가** — 비교할 색이 없다.",
        )

    spread = float(oos["mean_net_r"].max() - oos["mean_net_r"].min())
    best_oos = str(oos.loc[oos["mean_net_r"].idxmax(), "color"])
    worst_oos = str(oos.loc[oos["mean_net_r"].idxmin(), "color"])
    if len(oos) < 2:
        first = (
            f"① **판정 불가** — 뒷구간 유효 색이 **{best_oos} 하나뿐**이라 가를 대상이 없다"
            f"(거래당 {oos['mean_net_r'].iloc[0]:+.4f}R). 분포가 한 색에 쏠렸다는 것 자체가 "
            "「색으로 걸러 봐야 아무것도 안 걸린다」는 답이다."
        )
    elif spread <= NOISE_R:
        first = (
            f"① **아니오** — 뒷구간 색깔 간 거래당 net R 격차가 **{spread:.4f}R**로 규약 폭"
            f"(±{NOISE_R}R) 안이라 0과 구분되지 않는다({best_oos} vs {worst_oos})."
        )
    else:
        first = (
            f"① **격차는 있다** — 뒷구간 최선 **{best_oos}** "
            f"({float(oos.loc[oos['mean_net_r'].idxmax(), 'mean_net_r']):+.4f}R) vs 최악 "
            f"**{worst_oos}** ({float(oos.loc[oos['mean_net_r'].idxmin(), 'mean_net_r']):+.4f}R) "
            f"= **{spread:.4f}R** (규약 폭 ±{NOISE_R}R 초과). ⚠️ 지고 있는 뭉치를 넷으로 "
            "나누면 최선 조각은 **무조건 존재**하므로 이 줄만으로는 신호가 아니다 — ②를 볼 것."
        )

    if ins.empty:
        second = "② **판정 불가** — 앞구간(`is`)에 유효 표본인 색이 없다."
    else:
        best_is = str(ins.loc[ins["mean_net_r"].idxmax(), "color"])
        held = oos[oos["color"] == best_is]
        if held.empty:
            second = (
                f"② **확인 불가** — 앞구간 최선 **{best_is}**이 뒷구간에서 유효 표본에 "
                "못 미친다(그 색으로 갈 근거가 없다)."
            )
        else:
            rank = int((oos["mean_net_r"] > float(held["mean_net_r"].iloc[0])).sum()) + 1
            kept = rank == 1
            second = (
                f"② **{'예' if kept else '아니오'}** — 앞구간 최선은 **{best_is}**이고 "
                f"뒷구간에서 {len(oos)}색 중 **{rank}위**"
                f"({float(held['mean_net_r'].iloc[0]):+.4f}R)다."
                + (
                    " 두 구간이 같은 색을 가리킨다."
                    if kept
                    else " **뒤집혔다** — 색이 아니라 구간 우연으로 읽는다(WAN-161 부류)."
                )
            )
    return first, second


def gate_decision(frame: pd.DataFrame) -> str:
    """후속(필터) 이슈로 갈 근거가 있는가 — 사용자가 정한 두 조건을 **둘 다** 본다."""
    oos = _judgeable(frame, PRIMARY_OOS)
    ins = _judgeable(frame, harness.SEGMENT_IS)
    if oos.empty or ins.empty or len(oos) < 2:
        return "**필터로 갈 근거 없음** — 유효 표본인 색이 둘 미만이라 조건 1을 물을 수 없다."
    spread = float(oos["mean_net_r"].max() - oos["mean_net_r"].min())
    best_is = str(ins.loc[ins["mean_net_r"].idxmax(), "color"])
    best_oos = str(oos.loc[oos["mean_net_r"].idxmax(), "color"])
    cond1 = spread > NOISE_R
    cond2 = best_is == best_oos
    if cond1 and cond2:
        return (
            f"**두 조건을 다 만족한다**(격차 {spread:.4f}R > ±{NOISE_R}R · 앞뒤 구간 최선이 "
            f"모두 **{best_oos}**) — 후속 필터 이슈의 **후보**다. 🚨 단 그 이슈는 반드시 "
            "**북에서 다시 재야 한다**(색으로 거래를 걸러내면 공유 자본 경합이 달라져 이 표의 "
            "숫자가 그대로 나오지 않는다 — WAN-341/323). **개발자 임의 착수 금지 · 사용자 결정.**"
        )
    reasons = []
    if not cond1:
        reasons.append(f"격차 {spread:.4f}R가 규약 폭(±{NOISE_R}R) 안")
    if not cond2:
        reasons.append(f"앞구간 최선({best_is})과 뒷구간 최선({best_oos})이 다름")
    return f"**필터로 갈 근거 없음** — {' · '.join(reasons)}. 📌 그것도 답이다."


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _fmt_r(value: float) -> str:
    return f"{value:+.4f}"


def _color_table(frame: pd.DataFrame, segment: str, axis: str, bucket: str) -> list[str]:
    part = frame[
        (frame["segment"] == segment) & (frame["axis"] == axis) & (frame["bucket"] == bucket)
    ]
    if part.empty:
        return []
    lines = [
        "| 색 | 거래 | 비중 | 승률 | 거래당 net R | net R 합 |",
        "| -- | --: | --: | --: | --: | --: |",
    ]
    order = {label: i for i, label in enumerate(BUCKET_ORDER)}
    for _i, row in part.sort_values("color", key=lambda s: s.map(order)).iterrows():
        gate = "" if row["sample_ok"] else " ⚠️"
        lines.append(
            f"| {row['color']}{gate} | {int(row['num_trades'])} | {row['share'] * 100:.1f}% | "
            f"{row['win_rate'] * 100:.1f}% | {_fmt_r(float(row['mean_net_r']))} | "
            f"{float(row['sum_net_r']):+.1f} |"
        )
    return lines


def render_summary(frame: pd.DataFrame, checks: dict[str, float] | None = None) -> str:
    """요약 md — 분포(완료기준 1) · 색깔별 성적(2) · 판정 두 문장(3)."""
    first, second = verdict(frame)
    segments = [s for s in SEGMENT_ORDER if s in set(frame["segment"])]
    out: list[str] = [
        "# WAN-372 — 진입 순간 MACD 히스토그램 색깔별 성적 (관측 전용)",
        "",
        "채택 북 거래에 **체결 순간의** MACD 히스토그램 색(12/26/9 · 봉내 라이브)을 붙여",
        "성적을 나눠 본 표다. **매매는 하나도 안 바뀐다** — 관측 열만 더했고 거래 집합·손익·",
        "MDD가 비트 단위로 그대로다(검산 (a)).",
        "",
        "## 판정 (완료기준 3)",
        "",
        first,
        "",
        second,
        "",
        gate_decision(frame),
        "",
        "## §1 분포 — 네 색이 각각 몇 건인가 (완료기준 1)",
        "",
        "📌 **분포 자체가 첫 산출물이다.** 오더블록 롱은 존에 지정가를 걸고 가격이 **내려와야**",
        "체결되므로 체결 순간은 구조적으로 하락 중이다 — 한 색에 쏠려 있으면 「그 색만 진입」은",
        "사실상 아무것도 안 거른다.",
        "",
    ]
    for segment in segments:
        out.append(f"### {segment}" + (" (주 수치)" if segment == PRIMARY_OOS else ""))
        out.append("")
        out += _color_table(frame, segment, AXIS_OVERALL, "전체")
        out.append("")
        buckets = sorted(
            set(frame[(frame["segment"] == segment) & (frame["axis"] == AXIS_TIMEFRAME)]["bucket"]),
            key=timeframe_to_ms,
        )
        for bucket in buckets:
            out.append(f"**{bucket}**")
            out.append("")
            out += _color_table(frame, segment, AXIS_TIMEFRAME, bucket)
            out.append("")

    out += [
        "⚠️ `⚠️` 표시는 유효 표본(20건) 미달이라 **판정에서 뺀** 색이다 — 얇은 조각의 극단값이",
        "「최선 색」으로 올라오면 판정이 통째로 흔들린다(WAN-84 게이트와 같은 자).",
        "",
        "## 검산",
        "",
    ]
    if checks:
        out += [
            "| 검산 | 값 |",
            "| -- | --: |",
            "| (a) 관측 팔 ≡ 채택 북(수익·MDD·거래 수 최대차) | "
            f"{checks['adopted_identity']:.2e} |",
            f"| (b) 색 귀속 = 전수(최대차) | {checks['coverage_gap']:.0f} |",
            f"| (c) 색 안 붙은 재진입 거래 | {checks['unlabeled_reentries']:.0f}건 |",
            "",
        ]
    else:
        out += ["(CSV에서 요약만 재생성 — 검산값은 실행 시점 로그를 볼 것.)", ""]

    out += [
        "## 읽는 법 · 경고",
        "",
        "* 🚨 **네 색 중 하나는 반드시 좋아 보인다.** 지고 있는 뭉치를 넷으로 나누면 가장 나은",
        "  조각은 **무조건 존재**한다 — 그래서 앞구간에서 보고 뒷구간에서 확인한다(판정 ②).",
        "  **뒷구간은 고르는 축이 아니다.**",
        f"* 자는 **거래당 net R**이다(총수익 %는 6년 복리라 판정 자가 아니다 — WAN-169/213)."
        f" ±{NOISE_R}R 안은 「0과 구분되지 않는다」로 읽는다(WAN-366 규약).",
        "* ⚠️ **모멘텀 지표는 이미 한 번 실패했다** — RSI 게이트가 거래를 13~14% 쳐냈는데 그",
        "  쳐냄이 순손해라 WAN-123에서 제거됐다. MACD가 같은 운명이라는 뜻은 아니지만(재는",
        "  대상이 다르다 — 과매도 수준 vs 모멘텀의 방향·가속), **「지표를 하나 더 얹으면",
        "  나아질 것」이라는 기대는 이 저장소에서 네 번 배신당했다**(볼린저·존폭 필터·재진입·RSI).",
        "* ❌ **색으로 거르는 팔은 이 이슈에서 만들지 않는다.** 그때는 **북에서 다시 재야",
        "  한다** — 색으로 거래를 걸러내면 자본 경합이 달라져 이 표의 숫자가 그대로 나오지",
        "  않는다(WAN-341 · WAN-323이 겪은 자리).",
        "* ⚠️ 「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변 — 이 표는 *이미 한 거래를",
        "  색으로 나눠 본다*이지 *새 신호를 찾는다*가 아니다.",
        "* ⚠️ 전부 `baseline`(닿으면 체결) 렌즈 위 값 · 색은 **체결 순간**의 것이고 그 봉이",
        "  나중에 어떻게 끝나든 다시 재지 않는다(EMA라 봉 안에서 색이 변한다).",
        "* 📌 MACD 파라미터(12/26/9)는 **스윕 대상이 아니다** — 흔들면 자유 파라미터가 셋 늘어",
        "  앞구간 승자를 찾는 기계가 된다(WAN-161).",
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-372 진입 순간 MACD 색깔별 성적")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument(
        "--from-csv", action="store_true", help="적재된 CSV로 요약만 다시 만든다(격자 미실행)"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    checks: dict[str, float] | None = None
    if args.from_csv:
        if not CSV_PATH.exists():
            print(f"CSV가 없습니다: {CSV_PATH}", flush=True)
            return 1
        frame = pd.read_csv(CSV_PATH)
    else:
        frame, checks = run_report(
            [s.strip() for s in args.symbols.split(",") if s.strip()],
            [t.strip() for t in args.timeframes.split(",") if t.strip()],
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        frame.to_csv(CSV_PATH, index=False)
        print(f"[wan372] CSV 적재: {CSV_PATH} ({len(frame)}행)", flush=True)
    SUMMARY_PATH.write_text(render_summary(frame, checks), encoding="utf-8")
    print(f"[wan372] 요약: {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
