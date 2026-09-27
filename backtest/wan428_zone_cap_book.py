"""WAN-428 §2 — 채택 북에 트레이딩뷰 `Zone Count` 캡을 **실제로 걸어** 재배치한다.

## 한 줄

사용자 결정(2026-09-23 「돌린다」)으로 §1 인구조사 위에 §2를 얹는다 — 존 순위 ≤ N인 후보만
시그널 경로에 남기고 **채택 북을 다시 배치**해 거래 수 · 거래당 net R · 수익 · MDD ·
수익/MDD를 낸다. 부록(`wan428_zone_rank_stoch_arm`)이 같은 캡을 **스토캐스틱 팔**에 걸어
MDD를 −31% 낮춘 것을 보고, 그 답이 **채택 북에도 옮겨지는가**를 묻는다.

🚨 **부록의 결론을 채택 북으로 옮겨 읽지 말라던 그 경고가 이 표의 존재 이유다.** 부록 팔은
칸당 2.4거래로 **용량 미포화**라 캡 감소율이 순위 몫과 8/8 정확히 같았는데(재배치 0), 채택
북은 칸당 **309거래** · 재진입 ON · 복리 ON · 재탭 ON이라 **캡이 비운 자본·슬롯을 다른 칸이
가져간다**(WAN-316/389 재배치 채널). 그래서 §1의 1차 근사(「4위 이하를 빼면 거래당 net R이
−0.1207 → −0.097」)는 **여기서 성립할 이유가 없다** — 실측이 정본이다.

## 배선은 하나도 새로 짜지 않았다

- 캡: `wan428_zone_rank_stoch_arm.cap_candidates` **그대로**(두 표가 같은 자로 잘라야 부록 ↔
  채택 북 대조가 성립한다 · 사본을 만들면 조용히 갈라진다 — WAN-95/112/123).
- 후보·배치: `wan408.build_payloads`/`place`(= `book_cli.run_book_segments`가 넘기는 그 인자 ·
  복리 **켬** · 재진입 ON · 익절 메이커). 스파이 테스트가 호출 인자로 고정한다.
- 존 대장·순위: `wan428_zone_rank_census.build_archives`/`rank_at`/`rank_trades`.

## 🚨 지갑 층 열이 정의를 잃을 수 있다 — 그게 답의 일부다

채택 좌표는 거래당 기대값이 음수라 복리 지갑이 **−100%로 포화**한다(WAN-378 「채택 좌표
`total_return −1.000000`」 · WAN-386/388 `wallet_defined`). 그러면 MDD·수익/MDD는 **비율로
내지 않고 「정의 상실」로 찍는다**(WAN-115 관행) — 사용자 질문(*「zone count로 MDD를 낮출 수
있나」*)의 답이 **「이 좌표에서는 그 열로 답할 수 없다」**일 수 있고, 캡이 지갑을 되살리면
그때부터 읽힌다. **거래당 net R은 이 함정에 안 걸린다**(분모가 리스크 금액이라 잔고와 무관).

## 재현

    uv run python -m backtest.wan428_zone_cap_book --jobs 4      # 전체(캐시 적중 시 분 단위)
    uv run python -m backtest.wan428_zone_cap_book --pilot       # BTC 4h 한 칸
    uv run python -m backtest.wan428_zone_cap_book --from-csv    # 요약만

측정 전용 · 엔진·기본값·토대 불변(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 캡은 이 모듈 안에서만 도는 옵트인이고 **기본값을 안 바꾼다**) ·
핀 없음(WAN-305) · 판단은 북에서(WAN-341) · 전부 `baseline`(낙관) 위 값 ·
`ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, net_r
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload
from backtest.wan408_loss_clustering import build_payloads, place
from backtest.wan409_invalidation_cascade import (
    PUBLISHED_OOS_WARM_MEAN_NET_R,
    PUBLISHED_OOS_WARM_TRADES,
)
from backtest.wan428_zone_rank_census import (
    RENDER_LIMIT_LOW,
    CellArchive,
    TradeRank,
    _Ranked,
    build_archives,
    rank_trades,
)
from backtest.wan428_zone_rank_stoch_arm import CAP_NAMES, CAPS, cap_candidates

__all__ = [
    "BOOK_CAP_CSV",
    "BOOK_CHECKSUM_CSV",
    "BOOK_SUMMARY_MD",
    "CAPS",
    "INHERITED_TAKE_PROFIT_LIQUIDITY",
    "PRIMARY_SEGMENT",
    "SATURATION_EPS",
    "SEGMENTS",
    "BookCapRow",
    "CapChecksumRow",
    "build_payloads",
    "cap_candidates",
    "cap_rows",
    "checksum_rows",
    "place",
    "render_summary",
    "run_measure",
    "wallet_defined",
    "wallet_readable",
]

#: 낼 구간 — §1과 같다. 차가운 절단(`is`/`oos`)은 **아카이브 인덱스가 다른 판**이라 아예 안
#: 만든다(`cold_segments=False`) — 부록이 그 구간에 캡을 걸어 쓰레기를 낸 자리다(wan428.md §부록).
SEGMENTS: tuple[str, ...] = ("full", "oos_warm")

#: 주 수치 구간(WAN-166 정본).
PRIMARY_SEGMENT = "oos_warm"

#: 익절 청산 유동성 — 이 모듈은 **정하지 않고 물려받는다**(`wan408.place`가 넘기는 그 값).
#:
#: 🚨 여기에 리터럴을 다시 적으면 채택 회계가 두 곳에 살아 갈라진다(WAN-370/373이 못 박은
#: 자리 · WAN-365 `ADOPTED_INVALIDATION_CANCEL`과 같은 규약). 그래서 이것은 **인용이지 정의가
#: 아니고**, 빌려 쓴 `place`가 실제로 그 값을 넘기는지는 `wan408` 쪽 스파이 테스트가 확인한다
#: (= `book_cli.run_book_segments`가 넘기는 것과 같은 인자).
INHERITED_TAKE_PROFIT_LIQUIDITY = harness.ADOPTED_TAKE_PROFIT_LIQUIDITY

BOOK_CAP_CSV = Path("backtest/reports/wan428_zone_cap_book.csv")
BOOK_CHECKSUM_CSV = Path("backtest/reports/wan428_zone_cap_book_checksum.csv")
BOOK_SUMMARY_MD = Path("backtest/reports/wan428_zone_cap_book_summary.md")

#: 거래당 net R의 「0과 구분되지 않는다」 규약 폭(WAN-366/370).
NOISE_R = 0.005


def wallet_defined(total_return: float, max_drawdown: float) -> bool:
    """이 행의 **지갑 층** 열이 뜻을 갖는가 — WAN-386/388/410과 **같은 술어**다.

    복리 지갑이 0을 뚫으면(총수익 ≤ −100%) 「자본 대비 비율」은 분모가 부호를 바꿔 무의미하다.
    그때는 비율을 내지 않고 「정의 상실」로 찍는다(WAN-115 관행).
    """
    return total_return > -1.0 and max_drawdown < 1.0


#: 포화 판정 폭 — 총수익이 −100%에, MDD가 100%에 이만큼 붙으면 **포화**로 본다.
SATURATION_EPS = 0.01


def wallet_readable(total_return: float, max_drawdown: float) -> bool:
    """🚨 **정의됐다고 읽을 수 있는 게 아니다** — 이 술어가 그 둘을 가른다.

    채택 좌표 실측이 `total_return = −0.9999986` · `max_drawdown = +0.9999991`이다. 부동소수로는
    `wallet_defined`를 **통과하는데**(−1.0보다 크다) 다섯 캡이 전부 수익 −100.00% · MDD 100.00% ·
    수익/MDD **−1.00**으로 찍혀 **완전히 무정보**다. 그대로 두면 읽는 사람이 그 −1.00들을
    **비교한다**(같은 표의 다른 열과 나란히 서 있으면 눈이 알아서 뺀다 — WAN-325 §3열 대조가
    같은 이유로 카드를 통째로 끈 자리).

    WAN-378이 *「이 좌표에서 총수익 %·MDD는 포화한다」*고 이미 적어 둔 그 성질이고, WAN-367의
    *「칼 같은 경계는 안심이 아니라 제약의 지문이다」*가 정확히 이 모양을 가리킨다. 그래서
    **「정의 상실」과 「포화」를 다른 낱말로** 찍고, 판정은 둘 다 **거래당 net R**으로만 낸다.
    """
    return (
        wallet_defined(total_return, max_drawdown)
        and total_return > -1.0 + SATURATION_EPS
        and max_drawdown < 1.0 - SATURATION_EPS
    )


class BookCapRow(BaseModel):
    """캡 × 구간 한 행."""

    model_config = ConfigDict(frozen=True)

    cap: int | None
    cap_name: str
    segment: str
    num_trades: int
    win_rate: float
    mean_net_r: float
    """판정 자 — `book_cli.net_r`와 **같은 자**(WAN-393 규칙: 페이퍼 `r_multiple`과만 맞댄다)."""
    stderr_net_r: float
    """표준오차. **2σ 밖이어야** 부호를 말한다(WAN-381/412 관문)."""
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None
    wallet_defined: bool
    """거짓이면 위 세 열을 **읽지 않는다**(요약이 「정의 상실」로 찍는다)."""
    liquidation_events: int
    """🚨 0이 안심의 근거가 아니다 — MDD 100%인데 0인 자리를 WAN-312가 못 박았다(WAN-367)."""
    peak_concurrency: int
    stop_rate: float


def _stderr(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var / len(values))


def _row_for(cap: int | None, seg: BookSegment) -> BookCapRow:
    pairs = seg.trades_with_placements()
    nets = [net_r(trade, placement) for trade, placement in pairs]
    stops = sum(1 for trade, _ in pairs if trade.realized_pnl < 0)
    row = seg.row
    defined = wallet_defined(row.total_return, row.max_drawdown)
    return BookCapRow(
        cap=cap,
        cap_name=CAP_NAMES[cap],
        segment=seg.segment,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        mean_net_r=sum(nets) / len(nets) if nets else 0.0,
        stderr_net_r=_stderr(nets),
        total_return=row.total_return,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd if defined else None,
        wallet_defined=defined,
        liquidation_events=row.liquidation_events,
        peak_concurrency=row.peak_concurrency,
        stop_rate=stops / len(pairs) if pairs else 0.0,
    )


def cap_rows(
    payloads: Sequence[CellPayload],
    archives: dict[tuple[str, str], CellArchive],
    *,
    start_ms: int,
    end_ms: int,
    caps: Sequence[int | None] = CAPS,
    log: bool = True,
) -> tuple[list[BookCapRow], dict[str, list[TradeRank]]]:
    """캡마다 후보를 거르고 **채택 북을 다시 배치**한다.

    무제한(`cap=None`) 팔의 거래는 순위 라벨까지 달아 함께 돌려준다 — 검산 (d)가 「캡 감소율
    ↔ 순위 몫」을 대조하는 데 그 라벨이 필요하다(재배치가 있으면 두 값이 갈린다).
    """
    ranked_cache: dict[tuple[str, str], _Ranked] = {}
    rows: list[BookCapRow] = []
    base_trades: dict[str, list[TradeRank]] = {}
    for cap in caps:
        t0 = time.monotonic()
        capped = cap_candidates(payloads, archives, cap=cap, ranked_cache=ranked_cache)
        books = place(capped, start_ms=start_ms, end_ms=end_ms, segments=SEGMENTS)
        for seg in books:
            rows.append(_row_for(cap, seg))
            if cap is None:
                base_trades[seg.segment] = rank_trades(
                    seg, archives, arm="adopted", ranked_cache=ranked_cache
                )
        if log:
            print(
                f"[wan428-book] 캡 {CAP_NAMES[cap]}: {time.monotonic() - t0:.0f}s "
                f"(거래 {[r.num_trades for r in rows if r.cap == cap]})",
                flush=True,
            )
    return rows, base_trades


class CapChecksumRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    left: float
    right: float
    abs_diff: float


def checksum_rows(
    rows: Sequence[BookCapRow],
    base_trades: dict[str, list[TradeRank]],
    *,
    adopted_coordinates: bool,
    caps: Sequence[int | None] = CAPS,
) -> list[CapChecksumRow]:
    """검산 — (a) 무제한 팔 ≡ 공개 채택 북 · (d) 캡 감소율 ↔ 순위 몫 (**관측**).

    🚨 **(d)는 실패 판정이 아니다**(`metric`에 「관측」) — 0이면 그 좌표가 **용량 미포화**라
    캡 효과를 그대로 읽을 수 있고, 0이 아니면 **재배치가 일어났다**는 뜻이라 캡이 비운 자리를
    다른 칸이 가져갔다는 증거다(WAN-316/389). 채택 북에서는 0이 아닐 것으로 예상한다.
    """
    checks: list[CapChecksumRow] = []
    base = {r.segment: r for r in rows if r.cap is None}
    if adopted_coordinates and PRIMARY_SEGMENT in base:
        pub = base[PRIMARY_SEGMENT]
        checks.append(
            CapChecksumRow(
                metric="(a) 무제한 팔 거래 수 ≡ 공개 채택 북",
                left=float(pub.num_trades),
                right=float(PUBLISHED_OOS_WARM_TRADES),
                abs_diff=abs(pub.num_trades - PUBLISHED_OOS_WARM_TRADES),
            )
        )
        checks.append(
            CapChecksumRow(
                metric="(a) 무제한 팔 거래당 net R ≡ 공개 채택 북",
                left=pub.mean_net_r,
                right=PUBLISHED_OOS_WARM_MEAN_NET_R,
                abs_diff=abs(pub.mean_net_r - PUBLISHED_OOS_WARM_MEAN_NET_R),
            )
        )
    for cap in caps:
        if cap is None:
            continue
        for segment, trades in sorted(base_trades.items()):
            row = next((r for r in rows if r.cap == cap and r.segment == segment), None)
            ref = base.get(segment)
            if row is None or ref is None or not trades or ref.num_trades == 0:
                continue
            cut = sum(1 for t in trades if t.rank is None or t.rank > cap)
            expected = cut / len(trades)
            actual = (ref.num_trades - row.num_trades) / ref.num_trades
            checks.append(
                CapChecksumRow(
                    metric=f"(d) 관측 · {CAP_NAMES[cap]} × {segment} 캡 감소율 ↔ 순위 몫",
                    left=actual,
                    right=expected,
                    abs_diff=abs(actual - expected),
                )
            )
    return checks


# --------------------------------------------------------------------------- #
# 실행 · 요약
# --------------------------------------------------------------------------- #


def run_measure(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    caps: Sequence[int | None] = CAPS,
    cache: PayloadCache | None = None,
    log: bool = True,
) -> tuple[list[BookCapRow], list[CapChecksumRow]]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    t0 = time.monotonic()
    payloads = build_payloads(
        symbols, timeframes, start=start, end=end, jobs=jobs, cold_segments=False, cache=cache
    )
    t_cand = time.monotonic()
    if log:
        print(f"[wan428-book] 후보 {len(payloads)}칸: {t_cand - t0:.0f}s", flush=True)
    archives = build_archives(symbols, timeframes, start=start, end=end, jobs=jobs)
    if log:
        print(
            f"[wan428-book] 존 대장 {len(archives)}칸: {time.monotonic() - t_cand:.0f}s", flush=True
        )
    rows, base_trades = cap_rows(
        payloads, archives, start_ms=start_ms, end_ms=end_ms, caps=caps, log=log
    )
    adopted = (
        tuple(symbols) == tuple(harness.DEFAULT_SYMBOLS)
        and tuple(timeframes) == tuple(harness.DEFAULT_TIMEFRAMES)
        and start == harness.DEFAULT_START
        and end == harness.DEFAULT_END
    )
    checks = checksum_rows(rows, base_trades, adopted_coordinates=adopted, caps=caps)
    return rows, checks


def _fmt(value: float, digits: int = 4) -> str:
    return f"{value:+.{digits}f}"


def _pct(value: float, digits: int = 2) -> str:
    return f"{value * 100:.{digits}f}%"


def _wallet(row: BookCapRow) -> tuple[str, str, str]:
    """수익 · MDD · 수익/MDD — **「정의 상실」과 「포화」를 다른 낱말로** 찍는다.

    🚨 둘을 한 낱말로 뭉개면 「부동소수로는 정의되는데 −100%/100%에 붙어 무정보」인 행이 멀쩡한
    비율을 달고 표에 선다(채택 좌표가 정확히 그 모양이다 — `wallet_readable` 독스트링).
    """
    if not row.wallet_defined:
        return ("정의 상실", "정의 상실", "—")
    if not wallet_readable(row.total_return, row.max_drawdown):
        return ("포화(≈−100%)", "포화(≈100%)", "—")
    ratio = row.return_over_mdd
    return (
        _pct(row.total_return),
        _pct(row.max_drawdown),
        f"{ratio:.2f}" if ratio is not None else "—",
    )


def render_summary(rows: Sequence[BookCapRow], checks: Sequence[CapChecksumRow]) -> str:
    frame = pd.DataFrame([r.model_dump() for r in rows])
    out: list[str] = [
        "# WAN-428 §2 — 채택 북에 `Zone Count` 캡을 걸면 (재배치 실측)",
        "",
        "존 순위 ≤ N인 후보만 시그널 경로에 남기고 **채택 북을 다시 배치**했다"
        f"(12종목 × 4TF 한 지갑 · 못 박은 6년 · 재진입 ON · 복리 ON · 핀 없음). 주 구간은"
        f" `{PRIMARY_SEGMENT}`(WAN-166).",
        "",
        "🚨 **이 좌표에서 수익·MDD는 포화한다 — 그것이 사용자 질문의 답의 절반이다.**"
        " 거래당 기대값이 음수인데 복리가 켜져 있어 총수익이 −100%, MDD가 100%에 붙는다"
        "(WAN-378이 같은 좌표에서 적어 둔 성질). 다섯 캡이 전부 수익/MDD **−1.00**으로 찍히므로"
        " 그 열로는 캡을 **가를 수 없다** — 판정은 **거래당 net R**으로만 낸다(분모가 리스크"
        " 금액이라 잔고와 무관하다). 🚨 **부록(복리 끔 · 칸당 2.4거래)에서 MDD가 32.8% → 22.5%로"
        " 내려간 것을 여기로 옮겨 읽지 말 것.**",
        "",
        "🚨 **부록(스토캐스틱 팔)의 결론을 여기로 옮겨 읽지 말 것** — 그 팔은 칸당 2.4거래로"
        " **용량 미포화**라 캡 감소율이 순위 몫과 정확히 같았지만(재배치 0), 채택 북은 칸당"
        " 309거래 · 재탭·재진입·복리 ON이라 **캡이 비운 자본·슬롯을 다른 칸이 가져간다**"
        "(WAN-316/389). 그 크기가 검산 (d)다.",
        "",
    ]
    for segment in SEGMENTS:
        part = frame[frame.segment == segment]
        if part.empty:
            continue
        out += [
            f"## `{segment}`",
            "",
            "| 캡 | 거래 | 승률 | 손절률 | 거래당 net R | 2σ | 수익 | MDD | 수익/MDD"
            " | 청산 | 최대 동시 |",
            "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
        ]
        base = part[part.cap.isna()]
        base_r = float(base.iloc[0].mean_net_r) if not base.empty else None
        for row in rows:
            if row.segment != segment:
                continue
            ret, mdd, ratio = _wallet(row)
            out.append(
                f"| {row.cap_name} | {row.num_trades:,} | {_pct(row.win_rate)} | "
                f"{_pct(row.stop_rate)} | {_fmt(row.mean_net_r)} | ±{row.stderr_net_r * 2:.4f} | "
                f"{ret} | {mdd} | {ratio} | {row.liquidation_events} | {row.peak_concurrency} |"
            )
        out.append("")
        if base_r is not None:
            out += [
                "### 무제한 대비 Δ(거래당 net R)",
                "",
                "| 캡 | Δ | 2σ 합성 | 부호 결정? |",
                "| -- | --: | --: | -- |",
            ]
            base_se = float(base.iloc[0].stderr_net_r)
            for row in rows:
                if row.segment != segment or row.cap is None:
                    continue
                delta = row.mean_net_r - base_r
                two_sigma = 2 * math.sqrt(base_se**2 + row.stderr_net_r**2)
                decided = "예" if abs(delta) > two_sigma and abs(delta) > NOISE_R else "아니오"
                out.append(f"| {row.cap_name} | {_fmt(delta)} | ±{two_sigma:.4f} | {decided} |")
            out.append("")
    out += ["## 검산", "", "| 항목 | 실측 | 기준 | 차 |", "| -- | --: | --: | --: |"]
    for check in checks:
        out.append(
            f"| {check.metric} | {check.left:.6f} | {check.right:.6f} | {check.abs_diff:.2e} |"
        )
    out += [
        "",
        "🚨 **(d)는 실패 판정이 아니다** — 0이면 그 좌표가 **용량 미포화**라 캡 효과를 그대로"
        " 읽을 수 있고, 0이 아니면 **재배치가 일어났다**는 증거다(캡이 비운 자리를 다른 칸이"
        " 가져갔다 · WAN-316/389). 채택 북에서는 0이 아닌 것이 **정상**이다.",
        "",
        "## 알려진 한계",
        "",
        f"- ⚠️ **순위로 자르는 것은 나이로 자르는 것이 아니다** — §1 실측에서 순위와 존 나이의"
        " Spearman이 0.753이고 같은 「11위+」가 15m 162일 · 1h 398일이다."
        f" `Low({RENDER_LIMIT_LOW})` 캡은 **TF마다 다른 나이**에서 자른다.",
        "- ⚠️ **거래를 줄여서 좋아 보이는 것과의 구분이 이 표만으로 안 끝난다**(WAN-378) —"
        " 거래 수를 반드시 함께 읽을 것.",
        "- ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 **체결 보수화(`pen_5bp`) 미측정**.",
        "- ⚠️ **차가운 절단(`is`/`oos`)은 없다** — 그 구간은 아카이브 인덱스가 다른 판이라"
        " 순위를 못 매긴다(부록이 그 자리에서 쓰레기를 냈다).",
        "- 🚨 **채택 권고가 아니다** — 캡을 기본값으로 올리는 것은 **재-베이스라인 = 사용자"
        " 결정**이고(WAN-47이 의도한 설계를 되돌리는 것이다) 개발자 임의 착수 금지다.",
        "",
    ]
    return "\n".join(out)


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-428 §2 — 채택 북 Zone Count 캡")
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--pilot", action="store_true", help="BTC 4h 한 칸만")
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 그린다")
    parser.add_argument("--no-cache", action="store_true", help="payload 디스크 캐시를 쓰지 않는다")
    args = parser.parse_args(argv)

    if args.from_csv:
        frame = pd.read_csv(BOOK_CAP_CSV)
        rows = [
            BookCapRow(**{**r, "cap": None if pd.isna(r["cap"]) else int(r["cap"])})
            for r in frame.to_dict("records")
        ]
        cframe = pd.read_csv(BOOK_CHECKSUM_CSV)
        checks = [CapChecksumRow(**r) for r in cframe.to_dict("records")]
    else:
        symbols = ["BTCUSDT"] if args.pilot else list(harness.DEFAULT_SYMBOLS)
        timeframes = ["4h"] if args.pilot else list(harness.DEFAULT_TIMEFRAMES)
        rows, checks = run_measure(
            symbols,
            timeframes,
            jobs=args.jobs,
            cache=None if args.no_cache else PayloadCache(),
        )
        if not args.pilot:
            _write(pd.DataFrame([r.model_dump() for r in rows]), BOOK_CAP_CSV)
            _write(pd.DataFrame([c.model_dump() for c in checks]), BOOK_CHECKSUM_CSV)

    summary = render_summary(rows, checks)
    if not args.pilot or args.from_csv:
        BOOK_SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
        BOOK_SUMMARY_MD.write_text(summary, encoding="utf-8")
    print(summary)
    bad = [c for c in checks if "관측" not in c.metric and c.abs_diff > 1e-6]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
