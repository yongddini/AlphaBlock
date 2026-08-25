"""부분 봉의 전 이력 스캔 + 4h 백테 영향 크기 실측 (WAN-327 §1·§2).

## 무엇을 재나

`alphablock verify`가 저장 4h·1d 봉이 같은 구간 1분봉 합과 다르다는 것을 잡았다(2026-08-18).
거래량이 절반이고 high·close까지 다른 봉이 있어 **형성 도중에 확정 라벨을 달고 저장된 봉**
(WAN-314 §2가 진단·수정한 그 서명)으로 읽혔다. 4h는 채택 작업 TF이고(WAN-182/252) 백테스트는
저장 4h 행을 **그대로** 읽으므로(1분봉에서 다시 만들지 않는다) 그 봉의 high/close가 틀리면
오더블록 탐지·진입가·손절 위치가 달라진다. **그런데 크기가 안 쟀다** — 이 모듈이 잰다.

* **§1 스캔** — 전 이력에서 손상 봉이 몇 개·언제·어느 종목인지, 가격까지 틀린 봉은 몇
  개인지(`data.partial_bars`). ⚠️ 판정자는 가격이 아니라 **거래량**이다(그 모듈 도크스트링).
* **§2 영향** — 같은 좌표를 **고치기 전/후로 두 번** 돌려 얼마나 움직이는지.
  「영향 없음」이 답이어도 그것이 결론이다(완료기준 2).

## 반사실은 비파괴다 — DB를 쓰지 않는다

「고친 판」은 저장 봉 중 **손상된 봉만** 그 구간 1분봉 합으로 갈아끼운 **메모리 사본**이다
(`data.partial_bars.repair_frame`). 거래량 노이즈 봉은 손대지 않는다(저장이 정본에 더 가깝다).
실제 수정은 사람이 하는 거래소 재수집이고 이 모듈은 아무것도 쓰지 않는다(WAN-194 원칙).

⚠️ **1분봉이 정본이라는 주장이 아니다.** 손상 구간 밖에서는 저장 4h가 1분봉 합보다 오히려
0.0~0.3% 크다(우리 1분봉 쪽이 조금 모자라다) — 그래서 전부 덮지 않고 **손상 봉만** 갈아끼운다.
이 팔은 「그 봉들이 온전했다면」의 **근사**이지 정답이 아니다.

## 두 축을 병기한다 (WAN-305 · 재진입 켬)

* **per-cell**(칸별 격리, 동시 1포지션) — 종목별로 얼마나 움직이는지 보이는 축.
* **채택 북**(cap_only 5배 · 재진입 band 켬 · 유동성 한도 채택값) — 실제 회계.
  per-cell 표만 내면 재진입이 빠진 판이라(사용자 지시 2026-08-18: 모든 측정은 재진입 켬을
  전제) 북 판을 **같이** 낸다.

## 좌표

채택 좌표 그대로 — 12종목 × **4h** × 못 박은 6년 창(2020-09-15~2026-07-22) · `baseline` 단독 ·
**핀 하나도 없음**(WAN-305). 1d는 채택 TF가 아니라 스캔(§1)에만 나온다.

🚨 **로컬 DB의 한계를 먼저 읽을 것** — 이 저장소의 로컬 `ohlcv.db`는 상위TF가 채택 창 끝
(2026-07-22)에서 멈춘다. 손상은 07-12에 시작해 **07-24까지** 이어졌으므로(서버 실측, 이슈
코멘트) **07-23·24의 손상은 로컬에서 보이지 않는다**. 그 이틀은 채택 창 **밖**이라 백테
영향 측정에는 빠져도 되지만, §1 스캔의 「총 몇 개」는 **창 안 기준**으로 읽어야 한다.

재현:
    uv run python -m backtest.wan327_partial_bar_impact --part scan
    uv run python -m backtest.wan327_partial_bar_impact --part cell
    uv run python -m backtest.wan327_partial_bar_impact --part book --jobs 3
    uv run python -m backtest.wan327_partial_bar_impact --from-csv   # 요약만 재생성
"""

from __future__ import annotations

import argparse
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from backtest import harness
from backtest.book_cli import build_book_rows
from backtest.leverage_book import LeverageBookParams
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from common import timefmt
from data.partial_bars import SeriesScan, repair_frame, scan_symbol
from data.resample import resample_ohlcv
from data.storage import OhlcvStore

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
SCAN_CSV = REPORTS_DIR / "wan327_partial_bar_scan.csv"
SCAN_BARS_CSV = REPORTS_DIR / "wan327_partial_bar_scan_bars.csv"
IMPACT_CSV = REPORTS_DIR / "wan327_partial_bar_impact.csv"
SUMMARY_MD = REPORTS_DIR / "wan327_partial_bar_summary.md"

#: 스캔 대상 TF — 이슈 (A) 부류(가격까지 틀리는 부분 봉)가 관측된 두 TF.
SCAN_TIMEFRAMES: tuple[str, ...] = ("4h", "1d")
#: 영향 측정 TF — 채택 작업 TF 중 손상이 관측된 축(1d는 채택 TF가 아니다).
IMPACT_TIMEFRAME = "4h"
#: 채택 북(cap_only 5배) — `LeverageBookParams()`가 곧 채택 북이다(WAN-213).
ADOPTED_BOOK = LeverageBookParams()
#: 북이 내는 구간 — 전 구간 + 따뜻한 연속 OOS(WAN-166 주 수치).
#:
#: ⚠️ **차가운 절단(`oos`)은 안 낸다** — 이 표가 재는 것은 두 팔의 **차이**이지 성과 수준이
#: 아니고, per-cell 표(`CELL_SEGMENTS`)도 같은 두 구간이라 나란히 읽힌다. 차가운 구간은
#: 잘린 창에서 탐지부터 다시 해야 해 셀 비용의 큰 몫인데(WAN-301이 같은 이유로 노브를
#: 만들었다) 얻는 것이 델타에 없다. 성과 **수준**을 인용할 표가 아니다.
BOOK_SEGMENTS: tuple[str, ...] = ("full", "oos_warm")
#: per-cell이 내는 구간 — 북과 같은 질문을 칸별로.
CELL_SEGMENTS: tuple[str, ...] = ("full", "oos_warm")

STORED = "stored"
REPAIRED = "repaired"


class ScanRow(BaseModel):
    """§1 — 한 (심볼, TF)의 전 이력 스캔 요약 한 줄."""

    symbol: str
    timeframe: str
    compared: int
    damaged: int
    """손상 봉(부분 봉 · 가격 불일치) — 엔진에 영향을 줄 수 있는 쪽."""
    price_wrong: int
    """손상 봉 중 OHLC까지 틀린 것(§1-2: 「거래량만」과 「가격까지」를 가른다)."""
    noise: int
    """가격은 같고 거래량만 다르되 모자라지 않은 봉 — 무해(엔진은 거래량을 안 읽는다)."""
    first_damaged_ms: int | None
    last_damaged_ms: int | None
    min_volume_ratio: float | None
    max_price_bp: float | None
    bit_identical_ratio: float | None
    """리샘플과 **비트 단위로** 같은 버킷의 비율 — 유래의 지문(§1-3).

    1.0이면 그 시리즈의 상위TF는 1분봉에서 **집계돼 들어온** 것이라(WAN-175/307) 정의상
    1분봉 합과 같다 = **검사가 성립하지 않는다**. 독립 수집분은 거래량 합의 부동소수 누적
    순서 때문에 끝자리가 어긋나는 버킷이 섞여 1.0이 나오지 않는다(실측 BTC·ETH 4h 0.67~0.68).
    「불일치 0」을 「깨끗함」으로 읽기 전에 이 열을 볼 것."""


class BarRow(BaseModel):
    """§1 — 불일치 봉 한 개(손상·노이즈 전부). 「언제」를 세는 입력이다."""

    symbol: str
    timeframe: str
    open_time: int
    open_time_kst: str
    kind: str
    volume_ratio: float
    max_price_bp: float
    price_fields: str


class ImpactRow(BaseModel):
    """§2 — 한 팔(stored/repaired)의 성과 한 줄. 스코프가 per-cell이면 심볼별, 북이면 집계."""

    scope: str
    """`cell`(칸별 격리) 또는 `book`(채택 레버리지 북)."""
    symbol: str
    """per-cell이면 종목, 북이면 `ALL`."""
    timeframe: str
    segment: str
    arm: str
    """`stored`(지금 DB) 또는 `repaired`(손상 봉을 1분봉 합으로 갈아끼운 반사실)."""
    repaired_bars: int
    """이 칸에서 갈아끼운 봉 수(북 행은 스코프 전체 합)."""
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    liquidation_events: int | None = None
    """북 전용 — per-cell은 `None`."""


# --------------------------------------------------------------------------- #
# §1 스캔
# --------------------------------------------------------------------------- #


def scan_rows(scans: Sequence[SeriesScan]) -> list[ScanRow]:
    """스캔 결과를 시리즈별 한 줄로 요약한다(순수 함수)."""
    rows: list[ScanRow] = []
    for sc in scans:
        damaged = sc.damaged
        span = sc.damaged_span
        rows.append(
            ScanRow(
                symbol=sc.symbol,
                timeframe=sc.timeframe,
                compared=sc.compared,
                damaged=len(damaged),
                price_wrong=sum(1 for d in damaged if d.price_wrong),
                noise=len(sc.noise),
                first_damaged_ms=span[0] if span else None,
                last_damaged_ms=span[1] if span else None,
                min_volume_ratio=min((d.volume_ratio for d in damaged), default=None),
                max_price_bp=max((d.max_price_bp for d in damaged), default=None),
                bit_identical_ratio=sc.bit_identical_ratio,
            )
        )
    return rows


def run_scan(
    symbols: Sequence[str],
    timeframes: Sequence[str] = SCAN_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    db_path: str = harness.DB_PATH,
    log: bool = True,
) -> tuple[list[ScanRow], list[BarRow]]:
    """채택 창 안에서 전 이력 스캔을 돈다(읽기 전용).

    시리즈별 요약과 **봉 단위 상세**를 함께 낸다 — 「몇 개」는 요약이 답하고 「언제」는
    상세가 답한다(완료기준 1이 둘 다 요구한다).
    """
    store = OhlcvStore(db_path)
    rows: list[ScanRow] = []
    bars: list[BarRow] = []
    try:
        for symbol in symbols:
            scans = scan_symbol(
                store,
                harness.normalize_symbol(symbol),
                timeframes,
                start_ms=parse_date_ms(start),
                end_ms=parse_date_ms(end),
            )
            new = scan_rows(scans)
            rows.extend(new)
            bars.extend(
                BarRow(
                    symbol=d.symbol,
                    timeframe=d.timeframe,
                    open_time=d.open_time,
                    open_time_kst=timefmt.format_kst(d.open_time),
                    kind=d.kind,
                    volume_ratio=d.volume_ratio,
                    max_price_bp=d.max_price_bp,
                    price_fields="|".join(d.price_fields),
                )
                for sc in scans
                for d in sc.discrepancies
            )
            if log:
                for row in new:
                    print(
                        f"[wan327] {row.symbol} {row.timeframe}: {row.compared}버킷 · "
                        f"손상 {row.damaged}(가격 {row.price_wrong}) · 노이즈 {row.noise}",
                        flush=True,
                    )
    finally:
        store.close()
    return rows, bars


# --------------------------------------------------------------------------- #
# §2 영향 — per-cell
# --------------------------------------------------------------------------- #


def repaired_market(market: harness.MarketData) -> tuple[harness.MarketData, int]:
    """손상 봉만 1분봉 합으로 갈아끼운 **메모리 사본**을 만든다(DB 불변).

    `harness.load_market_data(repair_htf_from_1m=True)`와 같은 연산이되 원본을 이미
    들고 있을 때 두 번 읽지 않기 위한 경로다 — 갈아끼운 봉 수도 함께 돌려준다.
    """
    if market.df_1m.empty:
        raise ValueError(f"{market.symbol} {market.timeframe}: 1분봉이 없어 반사실을 못 만듭니다.")
    resampled = resample_ohlcv(market.df_1m, "1m", market.timeframe)
    fixed, replaced = repair_frame(market.htf_df, resampled)
    return dataclasses.replace(market, htf_df=fixed), replaced


def _cell_rows_for_market(
    market: harness.MarketData, *, arm: str, repaired_bars: int
) -> list[ImpactRow]:
    """한 팔의 per-cell 성과 행(full · oos_warm)."""
    params = harness.build_params()
    cfg = harness.legacy_build_config(market.timeframe)
    ob_result = harness.detect_order_blocks(market)
    boundary = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    rows: list[ImpactRow] = []
    for segment in CELL_SEGMENTS:
        eval_from = None if segment == "full" else boundary
        outcome = harness.run_once(
            market,
            params=harness.pin_invalidation_cancel(params),
            cfg=cfg,
            order_block_result=ob_result,
            eval_from_ms=eval_from,
        )
        metrics = outcome.result.metrics
        rows.append(
            ImpactRow(
                scope="cell",
                symbol=market.symbol,
                timeframe=market.timeframe,
                segment=segment,
                arm=arm,
                repaired_bars=repaired_bars,
                num_trades=metrics.num_trades,
                win_rate=metrics.win_rate,
                total_return=metrics.total_return,
                max_drawdown=metrics.max_drawdown,
            )
        )
    return rows


def run_cell_impact(
    symbols: Sequence[str],
    *,
    timeframe: str = IMPACT_TIMEFRAME,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    log: bool = True,
) -> list[ImpactRow]:
    """칸별 격리 성과를 저장 봉 팔과 반사실 팔로 각각 낸다.

    데이터는 심볼당 **한 번만** 읽고 반사실은 메모리에서 만든다 — 두 팔이 같은 1분봉·같은
    펀딩을 쓰므로 차이는 **상위TF 봉 하나**로 격리된다.
    """
    rows: list[ImpactRow] = []
    for symbol in symbols:
        market = harness.load_market_data(
            harness.normalize_symbol(symbol),
            timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            need_1m=True,
        )
        if market.empty or market.df_1m.empty:
            if log:
                print(f"[wan327] {symbol} {timeframe}: 데이터 없음 — 건너뜀", flush=True)
            continue
        fixed, replaced = repaired_market(market)
        rows.extend(_cell_rows_for_market(market, arm=STORED, repaired_bars=0))
        rows.extend(_cell_rows_for_market(fixed, arm=REPAIRED, repaired_bars=replaced))
        if log:
            pair = {(r.segment, r.arm): r for r in rows if r.symbol == market.symbol}
            before = pair[("full", STORED)].total_return
            after = pair[("full", REPAIRED)].total_return
            print(
                f"[wan327] {market.symbol} {timeframe}: 교정 {replaced}봉 · "
                f"full {before * 100:.2f}% → {after * 100:.2f}%",
                flush=True,
            )
    return rows


# --------------------------------------------------------------------------- #
# §2 영향 — 채택 북
# --------------------------------------------------------------------------- #


def run_book_impact(
    symbols: Sequence[str],
    *,
    timeframe: str = IMPACT_TIMEFRAME,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[ImpactRow]:
    """채택 북(cap_only 5배 · 재진입 band)을 두 팔로 돌린다.

    `book_cli.run_book`을 그대로 못 쓰는 이유는 반사실 팔이 로딩 단계의 옵트인
    (`repair_partial_bars`)을 타야 하기 때문이다 — 나머지(후보 생성 · 펀딩 대리 · 배치
    회계)는 같은 함수를 그대로 쓴다. 저장 팔은 채택 북과 **구성상 같다**.
    """
    rows: list[ImpactRow] = []
    for arm, repair in ((STORED, False), (REPAIRED, True)):
        payloads = run_cells(
            symbols,
            [timeframe],
            start=start,
            end=end,
            jobs=jobs,
            adv_fraction=harness.UNSET,
            repair_partial_bars=repair,
            # 차가운 구간을 안 내므로 그 탐지도 건너뛴다(WAN-301 컴퓨트 노브) — `full`·
            # `oos_warm` 산출은 이 노브와 무관하다(같은 전체 창 후보의 경계 필터).
            cold_segments=False,
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        )
        payloads, note = apply_funding_proxy(payloads)
        if note and log:
            print(f"[wan327] 펀딩 대리({arm}): {note}", flush=True)
        book_rows = build_book_rows(
            payloads,
            book=ADOPTED_BOOK,
            segments=BOOK_SEGMENTS,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for br in book_rows:
            rows.append(
                ImpactRow(
                    scope="book",
                    symbol="ALL",
                    timeframe=timeframe,
                    segment=br.segment,
                    arm=arm,
                    repaired_bars=0,
                    num_trades=br.num_trades,
                    win_rate=br.win_rate,
                    total_return=br.total_return,
                    max_drawdown=br.max_drawdown,
                    liquidation_events=br.liquidation_events,
                )
            )
            if log:
                print(
                    f"[wan327] 북({arm}) {br.segment}: 거래 {br.num_trades} · "
                    f"수익 {br.total_return * 100:.2f}% · MDD {br.max_drawdown * 100:.2f}%",
                    flush=True,
                )
    return rows


# --------------------------------------------------------------------------- #
# CSV 왕복
# --------------------------------------------------------------------------- #


def scan_to_frame(rows: Sequence[ScanRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def impact_to_frame(rows: Sequence[ImpactRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def bars_to_frame(rows: Sequence[BarRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def bars_from_csv(path: Path = SCAN_BARS_CSV) -> list[BarRow]:
    if not path.exists():
        return []
    # `price_fields`는 가격이 멀쩡한 봉에서 빈 문자열인데 pandas가 NaN으로 읽는다 —
    # 문자열 열이라 명시적으로 되돌린다(빈 값과 결측이 같은 뜻인 열이다).
    frame = pd.read_csv(path)
    frame["price_fields"] = frame["price_fields"].fillna("")
    return [BarRow(**row) for row in frame.to_dict("records")]


def scan_from_csv(path: Path = SCAN_CSV) -> list[ScanRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [ScanRow(**row) for row in frame.replace({float("nan"): None}).to_dict("records")]


def impact_from_csv(path: Path = IMPACT_CSV) -> list[ImpactRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [ImpactRow(**row) for row in frame.replace({float("nan"): None}).to_dict("records")]


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _arm(
    rows: Sequence[ImpactRow], scope: str, symbol: str, segment: str, arm: str
) -> ImpactRow | None:
    for row in rows:
        if (
            row.scope == scope
            and row.symbol == symbol
            and row.segment == segment
            and row.arm == arm
        ):
            return row
    return None


def build_summary_markdown(
    scan: Sequence[ScanRow],
    impact: Sequence[ImpactRow],
    bars: Sequence[BarRow] = (),
) -> str:
    """§1 표 + §2 전후 대조를 사람이 읽는 마크다운으로 낸다."""
    lines: list[str] = [
        "# WAN-327 — 부분 봉 스캔(§1)과 4h 백테 영향 크기(§2)",
        "",
        "재현: `uv run python -m backtest.wan327_partial_bar_impact --part scan|cell|book`"
        " (요약만: `--from-csv`)",
        "",
        "⚠️ **로컬 DB는 상위TF가 채택 창 끝(2026-07-22)에서 멈춘다.** 손상은 07-24까지"
        " 이어졌으므로(서버 실측) 07-23·24분은 아래 표에 **없다** — 그 이틀은 채택 창 밖이라"
        " 백테 영향에는 빠져도 되지만 「총 몇 개」는 **창 안 기준**으로 읽어야 한다.",
        "",
        "## §1 — 전 이력 스캔 (채택 창 2020-09-15 ~ 2026-07-22)",
        "",
    ]
    if not scan:
        lines.append("_스캔 결과 없음 (`--part scan`으로 생성)._")
    else:
        lines.append(
            "| 종목 | TF | 비교 버킷 | 손상 | 가격까지 틀림 | 거래량 노이즈 |"
            " 손상 구간(KST) | 최소 거래량% | 최대 가격오차bp | 비트 일치율 |"
        )
        lines.append("| -- | -- | --: | --: | --: | --: | -- | --: | --: | --: |")
        for row in scan:
            span = "—"
            if row.first_damaged_ms is not None and row.last_damaged_ms is not None:
                span = (
                    f"{timefmt.format_kst(row.first_damaged_ms)[:10]}"
                    f" ~ {timefmt.format_kst(row.last_damaged_ms)[:10]}"
                )
            vol = "—" if row.min_volume_ratio is None else f"{row.min_volume_ratio * 100:.1f}"
            bp = "—" if row.max_price_bp is None else f"{row.max_price_bp:.1f}"
            # 소수 둘째 자리까지 찍는다 — 손상이 4봉뿐인 시리즈(12,816버킷 중)는 한 자리로
            # 반올림하면 100.0%가 되어 아래 「100%면 검사 미성립」 경고와 눈으로 충돌한다.
            ident = (
                "—" if row.bit_identical_ratio is None else f"{row.bit_identical_ratio * 100:.2f}%"
            )
            lines.append(
                f"| {row.symbol.split('/')[0]} | {row.timeframe} | {row.compared} | {row.damaged}"
                f" | {row.price_wrong} | {row.noise} | {span} | {vol} | {bp} | {ident} |"
            )
        total_damaged = sum(r.damaged for r in scan)
        total_price = sum(r.price_wrong for r in scan)
        total_noise = sum(r.noise for r in scan)
        total_compared = sum(r.compared for r in scan)
        lines.extend(
            [
                "",
                f"📌 **손상 {total_damaged}봉 · 그중 가격까지 틀린 것 {total_price}봉 ·"
                f" 거래량 노이즈 {total_noise}봉** (비교 {total_compared}버킷 중 손상"
                f" {100.0 * total_damaged / max(total_compared, 1):.2f}%).",
                "",
                "⚠️ **판정자는 가격이 아니라 거래량이다** — 그 버킷의 고가·저가가 잘리기 전에"
                " 이미 찍혀 있으면 부분 봉이어도 가격이 맞는다. 「가격이 틀렸는가」로 가르면"
                " 부분 봉을 놓친다(`data.partial_bars` 도크스트링).",
            ]
        )
        derived = sorted({r.symbol.split("/")[0] for r in scan if r.bit_identical_ratio == 1.0})
        if derived:
            lines.extend(
                [
                    "",
                    "🚨 **비트 일치율 100%인 시리즈는 「깨끗한」 게 아니라 검사가 성립하지"
                    f" 않는다** ({', '.join(derived)}). 그 상위TF 봉은 1분봉에서 **집계돼"
                    " 들어온 것**이라(`data.aggregate`, WAN-175/307) **정의상** 1분봉 합과"
                    " 같다 — 이 스캔도 `verify`의 정합성 검사도 그 시리즈엔 **아무것도"
                    " 물어보지 못한다**(자기 자신과의 비교). 독립 수집분은 거래량 합의"
                    " 부동소수 누적 순서 때문에 **정확히** 100%가 나오지 않는다(위 표의 다른"
                    " 시리즈). ⚠️ 반올림해 100.00%로 보이는 것과 다르다 — 판정은 표시가"
                    " 아니라 비트 동일 여부로 낸다.",
                ]
            )

    damaged_bars = [b for b in bars if b.kind != "volume_noise"]
    if damaged_bars:
        by_day: dict[str, list[BarRow]] = {}
        for bar in damaged_bars:
            by_day.setdefault(bar.open_time_kst[:10], []).append(bar)
        lines.extend(
            [
                "",
                "### 손상 봉 일자별(KST) — 「언제」",
                "",
                "| 날짜 | TF | 봉수 | partial | price_only | 최소 거래량% |"
                " 최대 가격오차bp | 종목 |",
                "| -- | -- | --: | --: | --: | --: | --: | -- |",
            ]
        )
        for day in sorted(by_day):
            for timeframe in sorted({b.timeframe for b in by_day[day]}):
                items = [b for b in by_day[day] if b.timeframe == timeframe]
                partial = sum(1 for b in items if b.kind == "partial")
                syms = ",".join(sorted({b.symbol.split("/")[0] for b in items}))
                lines.append(
                    f"| {day} | {timeframe} | {len(items)} | {partial} | {len(items) - partial}"
                    f" | {min(b.volume_ratio for b in items) * 100:.1f}"
                    f" | {max(b.max_price_bp for b in items):.1f} | {syms} |"
                )
        lines.extend(
            [
                "",
                "🚨 **`verify`가 보던 창(최근 500버킷 = 4h면 83일) 밖에도 무리가 있다** — 위"
                " 표에서 2026-07 이전 날짜가 그것이다. 그 무리는 7월보다 훨씬 얕지만(거래량이"
                " 절반 아래로 떨어지지 않는다) 「한 번의 끝난 사고」가 아니라 **드물게 반복되는"
                " 부류**임을 뜻한다. 전 이력 스캔이 아니면 안 보인다.",
            ]
        )

    lines.extend(["", "## §2 — 고치기 전/후 같은 좌표 (4h · 채택 창)", ""])
    cell_rows = [r for r in impact if r.scope == "cell"]
    book_rows = [r for r in impact if r.scope == "book"]
    if not cell_rows:
        lines.append("_per-cell 결과 없음 (`--part cell`으로 생성)._")
    else:
        symbols = sorted({r.symbol for r in cell_rows})
        lines.append("### per-cell (칸별 격리 · 동시 1포지션)")
        lines.append("")
        lines.append(
            "| 종목 | 구간 | 교정 봉 | 거래(저장→교정) | 수익(저장) | 수익(교정) | Δ%p |"
            " MDD(저장) | MDD(교정) |"
        )
        lines.append("| -- | -- | --: | -- | --: | --: | --: | --: | --: |")
        for symbol in symbols:
            for segment in CELL_SEGMENTS:
                before = _arm(cell_rows, "cell", symbol, segment, STORED)
                after = _arm(cell_rows, "cell", symbol, segment, REPAIRED)
                if before is None or after is None:
                    continue
                delta = (after.total_return - before.total_return) * 100
                lines.append(
                    f"| {symbol.split('/')[0]} | {segment} | {after.repaired_bars}"
                    f" | {before.num_trades}→{after.num_trades} | {_pct(before.total_return)}"
                    f" | {_pct(after.total_return)} | {delta:+.2f} |"
                    f" {_pct(before.max_drawdown)} | {_pct(after.max_drawdown)} |"
                )
        moved: list[tuple[str, str]] = []
        for symbol in symbols:
            for segment in CELL_SEGMENTS:
                before = _arm(cell_rows, "cell", symbol, segment, STORED)
                after = _arm(cell_rows, "cell", symbol, segment, REPAIRED)
                if before is None or after is None:
                    continue
                if (after.total_return, after.num_trades) != (
                    before.total_return,
                    before.num_trades,
                ):
                    moved.append((symbol, segment))
        lines.extend(
            [
                "",
                f"📌 **움직인 (종목 × 구간) 셀 {len(moved)}개 /"
                f" {len(symbols) * len(CELL_SEGMENTS)}개.**"
                " 교정 봉이 0인 칸은 두 팔이 **비트 단위로 같다**(반사실이 항등이라 검산이 된다).",
            ]
        )

    lines.extend(["", "### 채택 북 (cap_only 5배 · 재진입 band · 유동성 한도 채택값)", ""])
    if not book_rows:
        lines.append("_북 결과 없음 (`--part book`으로 생성)._")
    else:
        lines.append(
            "| 구간 | 거래(저장→교정) | 수익(저장) | 수익(교정) | MDD(저장) | MDD(교정) |"
            " 청산(저장/교정) |"
        )
        lines.append("| -- | -- | --: | --: | --: | --: | -- |")
        for segment in BOOK_SEGMENTS:
            before = _arm(book_rows, "book", "ALL", segment, STORED)
            after = _arm(book_rows, "book", "ALL", segment, REPAIRED)
            if before is None or after is None:
                continue
            lines.append(
                f"| {segment} | {before.num_trades}→{after.num_trades}"
                f" | {_pct(before.total_return)} | {_pct(after.total_return)}"
                f" | {_pct(before.max_drawdown)} | {_pct(after.max_drawdown)}"
                f" | {before.liquidation_events}/{after.liquidation_events} |"
            )

    lines.extend(
        [
            "",
            "## 읽는 법 · 경고",
            "",
            "* ⚠️ **「교정 팔이 정답」이 아니다** — 손상 구간 밖에서는 저장 4h가 1분봉 합보다"
            " 오히려 조금 크다(우리 1분봉 쪽이 모자라다). 그래서 **손상 봉만** 갈아끼웠고,"
            " 이 팔은 「그 봉들이 온전했다면」의 **근사**다. 실제 수정은 거래소 재수집이다.",
            "* ⚠️ **옛 리포트 수치를 이 표로 일괄 무효 선언하지 말 것**(이슈 §범위 밖) — 재는"
            " 것은 4h 한 축이고, 움직임의 크기가 곧 이 표의 결론이다.",
            "* ⚠️ 전부 `baseline`(닿으면 체결) 위의 값이고 **「엣지 없음」(WAN-84/88/111/114/"
            "124/151/201) 불변** · 총수익%는 복리 착시(WAN-169/213) · 6년 MDD는 폭락 미포함"
            " **바닥선**.",
            "* **엔진·기본값·토대 불변**(`ConfluenceParams()`·`LeverageBookParams()` 그대로) ·"
            " 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`) · **DB에 아무것도 쓰지 않았다**.",
        ]
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-327 부분 봉 스캔 + 4h 백테 영향 측정")
    parser.add_argument(
        "--part",
        choices=("scan", "cell", "book", "all"),
        default="all",
        help="실행할 부분(기본 all). 무거운 순: book > cell > scan",
    )
    parser.add_argument("--symbols", nargs="+", default=None, help="대상 심볼(기본 채택 12종목)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="북 후보 생성 병렬(성능 노브)")
    parser.add_argument(
        "--from-csv", action="store_true", help="새로 돌리지 않고 기존 CSV로 요약만 재생성"
    )
    args = parser.parse_args(argv)

    symbols = list(args.symbols or harness.DEFAULT_SYMBOLS)
    scan = scan_from_csv()
    bars = bars_from_csv()
    impact = impact_from_csv()

    if not args.from_csv:
        if args.part in ("scan", "all"):
            scan, bars = run_scan(symbols, start=args.start, end=args.end)
            _write(scan_to_frame(scan), SCAN_CSV)
            _write(bars_to_frame(bars), SCAN_BARS_CSV)
            print(f"[wan327] 스캔 CSV: {SCAN_CSV} · {SCAN_BARS_CSV}", flush=True)
        if args.part in ("cell", "all"):
            fresh = run_cell_impact(symbols, start=args.start, end=args.end)
            impact = [r for r in impact if r.scope != "cell"] + fresh
            _write(impact_to_frame(impact), IMPACT_CSV)
            print(f"[wan327] 영향 CSV: {IMPACT_CSV}", flush=True)
        if args.part in ("book", "all"):
            fresh = run_book_impact(symbols, start=args.start, end=args.end, jobs=args.jobs)
            impact = [r for r in impact if r.scope != "book"] + fresh
            _write(impact_to_frame(impact), IMPACT_CSV)
            print(f"[wan327] 영향 CSV: {IMPACT_CSV}", flush=True)

    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text(build_summary_markdown(scan, impact, bars), encoding="utf-8")
    print(f"[wan327] 요약: {SUMMARY_MD}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
