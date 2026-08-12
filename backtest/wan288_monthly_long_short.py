"""WAN-288 — 롱/숏 × 종목 × TF별 OOS 월별 수익률 + 월별 합산(롱 북 vs 롱+숏 북).

사용자 요청(2026-08-12): *"월별 수익률을 롱/숏, 심볼별, 타임프레임별로 보여주고, 월별로
합산 수익률도 같이."* WAN-285(저항-존 숏)의 판정 **"숏 수익은 채택 근거 아님 — 상당 부분이
하락장 베타"** 를 **월별로 눈으로 확인**하는 자료다. 숏 수익이 창 전체에 고르게 퍼져 있으면
"규칙이 통한다"에 가깝고, 하락장 몇 개월에만 몰려 있으면 "그냥 하락장에 숏 = 베타"가 보인다.

## 기계 재사용 (새 파이프라인 금지)

* **월별 접기 = WAN-180 `_monthly_rows`** 를 그대로 재사용한다 — 북 자본곡선을 UTC 달력월로
  접어 「월말 자본 ÷ 직전 월말 자본 − 1」을 낸다.
* **숏 = WAN-282 옵트인** — `wan169.run_cells(short_enabled=True)`가 베어리시 OB 숏(롱 모델의
  좌우 반전 거울)을 같이 낸다. `short_enabled`는 **이 모듈 안에서만** 켜고 기본값은 안 바꾼다
  (`ConfluenceParams().short_enabled == False` 유지 — WAN-282/284와 같은 방식).
* **장세 라벨 = WAN-89 `_buy_hold`** (WAN-145/151이 이미 「장세 라벨」로 씀) — 그 달에 그냥
  사서 들고 있었으면 +/−. **음수 = 하락 달, 양수 = 상승 달**. 자의적 손잡이 없음(이동평균
  며칠·낙폭 몇 % 안 고름 = 자유 파라미터 회피, WAN-108).

## 두 팔의 북 — 왜 WAN-180 북(재진입 없음)인가

두 팔 다 **cap_only 5배**. 단 검산(완료기준 4)이 롱 팔을 `wan180_monthly_returns.csv`에
맞추라고 하므로 북 구성은 **WAN-180 그대로**(재진입 없음 · `adv_fraction` 없음)를 쓴다 —
채택 북(WAN-213/273)의 band 재진입은 그 CSV보다 뒤에 생겨 켜면 검산이 깨진다. 즉 여기서
「채택 cap_only 5배」는 **WAN-180 스냅샷 북**을 뜻하고, band 재진입은 별도 축이다.

`short_enabled=True` 한 번으로 롱+숏 후보를 낸 뒤 **롱만 걸러** 롱-온리 팔을 만든다 —
숏 게이트가 롱 후보를 건드리지 않음(WAN-282가 동작으로 고정)이 보장하므로 롱-온리 팔은
`short_enabled=False`(= WAN-180 경로)와 비트 단위로 같다. 컴퓨트를 절반으로 줄인다.

## 두 층위

1. **per-cell 방향 상세** — (심볼 × TF × 방향)마다 격리 시퀀싱으로 월별 순수익 %p 합을 낸다
   (WAN-282 진단 관행: 방향별 후보를 단독 시퀀싱). 실매매가 아니라 「그 방향이 그 달에 돈이
   됐나」의 분포 진단이다.
2. **월별 합산 두 줄** — (a) 롱-온리 북 vs (b) 롱+숏 북 (둘 다 cap_only 5배) + **델타(숏
   기여분)**. 이게 핵심 — 숏을 얹어 그 달 지갑이 실제로 어떻게 바뀌는지.

## ⚠️ 경고 (요약에 그대로 옮긴다)

* 월별 수익률 %는 5배 북을 복리로 굴린 값이라 **실현 수익이 아니다**(WAN-213 복리 착시) —
  방향·분포로만 읽는다.
* 전부 `baseline`(닿으면 체결) **낙관 렌즈** 위 값. 큐 우선순위 실측은 틱·호가(WAN-98,
  Canceled) 소관.
* **"하락장에서 숏이 번다"는 거의 동어반복** — 숏은 가격 내리면 벌게 설계됨. 채택 근거가
  아니다. 진짜 질문은 (a) **무작위 숏보다** 잘 버나(WAN-285 매칭 널이 이미 "아니오"에 가깝게
  답), (b) 하락 달 이득이 **상승 달 손실 + 비용까지 합쳐도** 남나.
* **바이앤홀드는 사후(월말) 라벨** — 월초엔 그 달이 하락장일지 모른다. "하락 달만 골라 숏"은
  미래를 알았을 때의 성적이지 실매매가 아니다.
* **넷팅 안 함** — 다른 TF 간 같은 종목이 롱·숏 양쪽으로 동시에 열릴 수 있고 백테스트 북은
  이를 상계하지 않는다(같은 (종목,TF) 칸 안에서만 롱·숏 동시 불가). 상충 건수를 요약에 적는다.
  넷팅 상계는 실거래 계층(WAN-241/235) 소관이며 이 측정 밖이다.
* 「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변 — 이 표는 숏 수익의 시간 분포를 보는
  것이지 엣지를 뒤집는 게 아니다.

## 재현

```
# WAN-291: WAN-292 펀딩 백필 반영 후 4TF 전체 실 펀딩 재산출(--append 아님). 15m·6년은 무겁다.
uv run python -m backtest.wan288_monthly_long_short --tf 15m,1h,2h,4h --jobs 4
uv run python -m backtest.wan288_monthly_long_short --from-csv                   # 요약만
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_FULL, SEGMENT_OOS_WARM
from backtest.leverage_book import LeverageBookParams, run_leverage_book
from backtest.models import PositionSide
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan89_short_autopsy import _buy_hold
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    _segment_cells,
    _short,
    run_cells,
)
from backtest.wan180_leverage_book_nine import _monthly_rows as wan180_monthly_rows
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    sequence_with_candidates,
)
from data.funding import FundingRateStore

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELL_CSV = REPORTS_DIR / "wan288_monthly_by_cell.csv"
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan288_monthly_book.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan288_monthly_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
ALL_SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS

#: 기본 TF = 4h·1h·2h(컴퓨트 실현 가능). 15m은 셀당 ~37분(WAN-203)이라 `--append`로 뒤에.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "2h")

#: 채택 회계 = cap_only 5배(WAN-180 스냅샷 북 · 재진입 없음 = 검산 대상 `wan180_monthly` 구성).
ADOPTED_MULTIPLE = 5.0
ADOPTED_MODE = "cap_only"

ARM_LONG_ONLY = "long_only"
ARM_LONG_SHORT = "long_short"

DIR_LONG = "long"
DIR_SHORT = "short"

#: WAN-180 채택 롱 북 OOS 월별에서 빨간 달(하락) 세 개 — 숏 수익이 이 근처에 몰리는지가 핵심.
LONG_RED_MONTHS: frozenset[str] = frozenset({"2024-09", "2025-11", "2026-07"})

#: 펀딩 공백 = 이 창에서 심볼 자기 펀딩이 0행이라 순수익이 낙관인가(†). WAN-292 백필 전에는
#: DOGE·LINK·LTC가 여기 걸렸으나, 지금은 하드코딩이 아니라 **실제 펀딩 데이터로 동적 판정**한다
#: (`_dir_monthly_rows`는 셀의 실제 펀딩으로, `--from-csv`는 `_gapped_symbols`의 DB 커버리지로).


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class DirMonthlyRow(BaseModel):
    """(심볼 × TF × 방향)의 한 달 격리 손익 (완료기준 1 상세).

    격리 = 방향별 후보를 per-cell 단독 시퀀싱(WAN-282 진단 관행)해 각 거래의 순수익 %p를
    **청산 달**로 접어 합한다. 실매매가 아니라 「그 방향이 그 달에 돈이 됐나」의 분포 진단이다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: str
    """`long` · `short`."""
    month: str
    """UTC 달력월, `YYYY-MM`."""
    net_pp_sum: float
    """이 달 청산 거래의 순수익 %p 합(진입 명목 대비, 가법)."""
    num_trades: int
    symbol_buy_hold: float
    """이 종목 이 달 바이앤홀드(월초→월말 종가). 음수 = 하락 달(WAN-89 정의)."""
    funding_gap: bool
    """신규 종목(펀딩 0행)이라 순수익 낙관인가(†)."""


class BookMonthlyRow(BaseModel):
    """한 스코프 × 팔의 월별 북 수익률 (완료기준 1 합산).

    ⚠️ 복리 곡선의 월 분해라 높은 배수의 월 수익률은 **절대값 인용 금지**(WAN-213 복리 착시).
    """

    model_config = ConfigDict(frozen=True)

    scope: str
    """`all`(전 TF) 또는 개별 TF(`4h`·`1h`·`2h`·`15m`)."""
    arm: str
    """`long_only` · `long_short`."""
    month: str
    monthly_return: float
    equity_end: float
    num_exits: int
    btc_buy_hold: float
    """BTC 이 달 바이앤홀드(시장 대리). 음수 = 하락 달."""


# --------------------------------------------------------------------------- #
# 방향 후보 · 격리 월별
# --------------------------------------------------------------------------- #


def _direction_candidates(cell: CellPayload, side: PositionSide) -> list[_Candidate]:
    """이 셀의 oos_warm 방향 후보 — base(full)를 칸 경계로 걸러 방향으로 뽑는다.

    oos_warm 규약(WAN-166/282): full 후보 중 탭이 따뜻한 평가 경계 이후(`trigger_time >=
    boundary_ms`)인 것만. 재진입 후보는 이 북 구성(WAN-180, 재진입 없음)에서 안 쓰므로 뺀다.
    """
    base = cell.candidates[SEGMENT_FULL]
    boundary = cell.boundary_ms
    return [c for c in base if c.side is side and c.trigger_time >= boundary]


def _filter_long_only(cell: CellPayload) -> CellPayload:
    """롱+숏 셀에서 **롱 후보만** 남긴 셀 — 롱-온리 팔(= WAN-180 경로)을 만든다.

    숏 게이트는 롱 후보를 건드리지 않으므로(WAN-282 고정) 걸러낸 롱은 `short_enabled=False`와
    비트 단위로 같다. 펀딩·경계는 그대로 두고 후보만 방향으로 판다. 재진입 후보는 이 북
    구성에서 안 쓰므로 비운다.
    """
    longs = {
        seg: tuple(c for c in cands if c.side is PositionSide.LONG)
        for seg, cands in cell.candidates.items()
    }
    return CellPayload(
        symbol=cell.symbol,
        timeframe=cell.timeframe,
        boundary_ms=cell.boundary_ms,
        candidates=longs,
        funding=cell.funding,
        rows=cell.rows,
        reentry_candidates={},
    )


def _month_of(exit_time_ms: int) -> str:
    stamp = datetime.fromtimestamp(exit_time_ms / 1000, tz=UTC)
    return f"{stamp.year:04d}-{stamp.month:02d}"


def _dir_monthly_rows(
    cell: CellPayload, side: PositionSide, buy_hold: dict[str, float]
) -> list[DirMonthlyRow]:
    """한 셀 한 방향의 월별 격리 손익 — 청산 달로 순수익 %p를 접는다."""
    cfg = harness.build_config(cell.timeframe)
    funding = cell.funding[SEGMENT_FULL]
    paired = sequence_with_candidates(_direction_candidates(cell, side), cfg, funding)
    pp_sum: dict[str, float] = {}
    counts: dict[str, int] = {}
    for _cand, trade in paired:
        month = _month_of(trade.exit_time)
        pp_sum[month] = pp_sum.get(month, 0.0) + trade.return_pct * 100.0
        counts[month] = counts.get(month, 0) + 1
    direction = DIR_LONG if side is PositionSide.LONG else DIR_SHORT
    # 펀딩 공백 = 이 셀이 자기 펀딩 0행으로 계산됐는가(낙관). 하드코딩 심볼 목록이 아니라
    # 셀이 실제로 실은 펀딩으로 판정한다 — WAN-292 백필 후 신규 3종목은 실 펀딩이라 False.
    gap = not funding
    return [
        DirMonthlyRow(
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            direction=direction,
            month=month,
            net_pp_sum=pp_sum[month],
            num_trades=counts[month],
            symbol_buy_hold=buy_hold.get(month, 0.0),
            funding_gap=gap,
        )
        for month in sorted(pp_sum)
    ]


# --------------------------------------------------------------------------- #
# 북 월별 (WAN-180 _monthly_rows 재사용)
# --------------------------------------------------------------------------- #


def _book_monthly_rows(
    cells: Sequence[CellPayload], scope: str, arm: str, btc_buy_hold: dict[str, float]
) -> list[BookMonthlyRow]:
    """이 팔·스코프의 채택(cap_only 5배 · WAN-180 구성) 북을 돌려 월별 수익률을 낸다.

    WAN-180 `_monthly_rows`를 그대로 써 북 자본곡선을 UTC 달력월로 접는다 — 롱-온리 팔은
    `wan180_monthly_returns.csv`의 (scope, cap_only, 5.0, oos_warm) 행과 비트 재현돼야 한다.
    """
    seg_cells = _segment_cells(list(cells), SEGMENT_OOS_WARM, "")
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    outcome = run_leverage_book(
        seg_cells,
        base_cfg,
        LeverageBookParams(leverage_multiple=ADOPTED_MULTIPLE, leverage_mode=ADOPTED_MODE),
    )
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    folded = wan180_monthly_rows(result, scope, ADOPTED_MODE, ADOPTED_MULTIPLE, SEGMENT_OOS_WARM)
    return [
        BookMonthlyRow(
            scope=scope,
            arm=arm,
            month=m.month,
            monthly_return=m.monthly_return,
            equity_end=m.equity_end,
            num_exits=m.num_exits,
            btc_buy_hold=btc_buy_hold.get(m.month, 0.0),
        )
        for m in folded
    ]


# --------------------------------------------------------------------------- #
# 월별 바이앤홀드 (장세 라벨, WAN-89 _buy_hold)
# --------------------------------------------------------------------------- #


def monthly_buy_hold(symbol: str, start: str, end: str) -> dict[str, float]:
    """이 종목의 UTC 달력월 바이앤홀드 — 월별 (월말 종가 ÷ 월초 종가 − 1).

    상위TF(1d, 1분봉 불필요) 종가를 달력월로 나눠 각 달을 `_buy_hold`(WAN-89)로 잰다.
    장세 라벨은 TF·전략과 무관한 시장 자체의 부호라 한 해상도(1d)로 충분하다.
    """
    market = harness.load_market_data(
        harness.normalize_symbol(symbol),
        "1d",
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        need_1m=False,
    )
    frame = market.htf_df
    if frame.empty:
        return {}
    months = [_month_of(int(t)) for t in frame["open_time"].astype("int64").tolist()]
    out: dict[str, float] = {}
    for month in sorted(set(months)):
        mask = [m == month for m in months]
        out[month] = _buy_hold(frame[mask])
    return out


# --------------------------------------------------------------------------- #
# 격자 조립
# --------------------------------------------------------------------------- #


def build_rows(
    long_short_cells: Sequence[CellPayload],
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> tuple[list[DirMonthlyRow], list[BookMonthlyRow]]:
    """롱+숏 셀에서 per-cell 방향 상세 + 스코프별 두-팔 북 월별을 낸다."""
    symbols = sorted({c.symbol for c in long_short_cells})
    buy_hold = {s: monthly_buy_hold(s, start, end) for s in symbols}
    btc = next((s for s in symbols if _short(s) == "BTC"), None)
    btc_bh = buy_hold.get(btc, {}) if btc else {}

    dir_rows: list[DirMonthlyRow] = []
    for cell in long_short_cells:
        bh = buy_hold.get(cell.symbol, {})
        dir_rows.extend(_dir_monthly_rows(cell, PositionSide.LONG, bh))
        dir_rows.extend(_dir_monthly_rows(cell, PositionSide.SHORT, bh))

    long_only_cells = [_filter_long_only(c) for c in long_short_cells]
    timeframes = sorted({c.timeframe for c in long_short_cells}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    book_rows: list[BookMonthlyRow] = []
    for scope in scopes:
        lo = (
            long_only_cells
            if scope == "all"
            else [c for c in long_only_cells if c.timeframe == scope]
        )
        ls = (
            long_short_cells
            if scope == "all"
            else [c for c in long_short_cells if c.timeframe == scope]
        )
        book_rows.extend(_book_monthly_rows(lo, scope, ARM_LONG_ONLY, btc_bh))
        book_rows.extend(_book_monthly_rows(list(ls), scope, ARM_LONG_SHORT, btc_bh))
    return dir_rows, book_rows


# --------------------------------------------------------------------------- #
# 프레임 · CSV 왕복 · append 병합
# --------------------------------------------------------------------------- #


def dir_to_frame(rows: Sequence[DirMonthlyRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(DirMonthlyRow.model_fields))


def book_to_frame(rows: Sequence[BookMonthlyRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookMonthlyRow.model_fields))


def dir_from_csv(path: Path) -> list[DirMonthlyRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [DirMonthlyRow.model_validate(rec) for rec in frame.to_dict("records")]


def book_from_csv(path: Path) -> list[BookMonthlyRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [BookMonthlyRow.model_validate(rec) for rec in frame.to_dict("records")]


def merge_dir(
    existing: Sequence[DirMonthlyRow], new: Sequence[DirMonthlyRow]
) -> list[DirMonthlyRow]:
    """--append: (심볼, TF, 방향, 월) 키로 덮어쓴다(새 TF를 잇거나 기존 셀 재실행)."""
    keyed = {(r.symbol, r.timeframe, r.direction, r.month): r for r in existing}
    for r in new:
        keyed[(r.symbol, r.timeframe, r.direction, r.month)] = r
    return [keyed[k] for k in sorted(keyed)]


def merge_book(
    existing: Sequence[BookMonthlyRow], new: Sequence[BookMonthlyRow]
) -> list[BookMonthlyRow]:
    """--append: (스코프, 팔, 월) 키로 덮어쓴다.

    ⚠️ `all` 스코프는 전 TF 집계라 15m을 `--append`하면 **다시 계산돼야** 옳다 — 그래서
    `--append` 실행은 항상 새 셀 전체로 `all`을 다시 낸다(단일 TF만 append하면 그 TF의
    `all` 재계산 불가라 기존 `all`을 남긴다). 여기서는 새로 온 스코프만 덮어쓴다.
    """
    keyed = {(r.scope, r.arm, r.month): r for r in existing}
    for r in new:
        keyed[(r.scope, r.arm, r.month)] = r
    return [keyed[k] for k in sorted(keyed)]


# --------------------------------------------------------------------------- #
# 분석 (요약용 · 테스트 대상)
# --------------------------------------------------------------------------- #


def short_delta_by_month(book_rows: Sequence[BookMonthlyRow], scope: str) -> dict[str, float]:
    """이 스코프의 월별 숏 기여분 = (롱+숏 북 월수익률) − (롱-온리 북 월수익률)."""
    lo = {
        r.month: r.monthly_return for r in book_rows if r.scope == scope and r.arm == ARM_LONG_ONLY
    }
    ls = {
        r.month: r.monthly_return for r in book_rows if r.scope == scope and r.arm == ARM_LONG_SHORT
    }
    return {month: ls[month] - lo.get(month, 0.0) for month in sorted(ls)}


def regime_split(book_rows: Sequence[BookMonthlyRow], scope: str) -> tuple[float, float, float]:
    """(하락 달 숏 기여 합, 상승 달 숏 기여 합, 순합) — BTC 바이앤홀드 부호로 달을 가른다."""
    delta = short_delta_by_month(book_rows, scope)
    btc = {
        r.month: r.btc_buy_hold for r in book_rows if r.scope == scope and r.arm == ARM_LONG_SHORT
    }
    down = sum(d for m, d in delta.items() if btc.get(m, 0.0) < 0)
    up = sum(d for m, d in delta.items() if btc.get(m, 0.0) >= 0)
    return down, up, down + up


def short_profit_by_month(dir_rows: Sequence[DirMonthlyRow]) -> dict[str, float]:
    """전 종목·TF 합산 숏 순수익 %p를 월별로 접는다 — 「숏이 몇 달에 몰리나」의 자."""
    out: dict[str, float] = {}
    for r in dir_rows:
        if r.direction == DIR_SHORT:
            out[r.month] = out.get(r.month, 0.0) + r.net_pp_sum
    return out


def conflict_months(dir_rows: Sequence[DirMonthlyRow]) -> list[tuple[str, str]]:
    """같은 (종목, 월)에 롱·숏 양쪽이 잡힌 상충 건 — 넷팅 안 하는 북의 양방향 계상 진단.

    다른 TF 간이라도 같은 종목이 한 달에 롱·숏 양쪽으로 열리면 지갑·명목 상한을 따로 먹는다.
    """
    longs: dict[tuple[str, str], int] = {}
    shorts: dict[tuple[str, str], int] = {}
    for r in dir_rows:
        if r.num_trades <= 0:
            continue
        key = (_short(r.symbol), r.month)
        if r.direction == DIR_LONG:
            longs[key] = longs.get(key, 0) + r.num_trades
        else:
            shorts[key] = shorts.get(key, 0) + r.num_trades
    return sorted(k for k in longs if k in shorts)


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _two_line_table(book_rows: Sequence[BookMonthlyRow], scope: str) -> str:
    lo = {r.month: r for r in book_rows if r.scope == scope and r.arm == ARM_LONG_ONLY}
    ls = {r.month: r for r in book_rows if r.scope == scope and r.arm == ARM_LONG_SHORT}
    delta = short_delta_by_month(book_rows, scope)
    months = sorted(set(lo) | set(ls))
    lines = [
        "| 월 | 장세(BTC BH) | 롱-온리 북 | 롱+숏 북 | 숏 기여(델타) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for month in months:
        lo_r = lo.get(month)
        ls_r = ls.get(month)
        ref = ls_r if ls_r is not None else lo_r
        btc = ref.btc_buy_hold if ref is not None else 0.0
        regime = "🔻하락" if btc < 0 else "🔺상승"
        lo_s = _fmt_pct(lo_r.monthly_return) if lo_r else "—"
        ls_s = _fmt_pct(ls_r.monthly_return) if ls_r else "—"
        d_s = _fmt_pct(delta.get(month, 0.0))
        red = " ⬅빨간달" if month in LONG_RED_MONTHS else ""
        lines.append(f"| {month}{red} | {regime} ({btc * 100:+.1f}%) | {lo_s} | {ls_s} | {d_s} |")
    return "\n".join(lines)


def _verdict_line(dir_rows: Sequence[DirMonthlyRow], book_rows: Sequence[BookMonthlyRow]) -> str:
    profit = short_profit_by_month(dir_rows)
    positive = {m: p for m, p in profit.items() if p > 0}
    total_pos = sum(positive.values())
    if not positive:
        return "**판정**: 숏이 양(+)인 달이 없다 — 하락장 몰림을 논할 표본조차 없다."
    ranked = sorted(positive.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[: min(5, len(ranked))]
    top_share = sum(p for _, p in top) / total_pos if total_pos else 0.0
    red_hit = [m for m, _ in top if m in LONG_RED_MONTHS]
    red_share = (
        sum(p for m, p in positive.items() if m in LONG_RED_MONTHS) / total_pos
        if total_pos
        else 0.0
    )
    top_str = ", ".join(f"{m}({p:+.1f}%p)" for m, p in top)
    hit = (
        f"롱 빨간 달({', '.join(sorted(LONG_RED_MONTHS))}) 중 상위 숏 달과 겹치는 것: "
        f"{', '.join(red_hit) if red_hit else '없음'}"
    )
    return (
        f"**판정 — 숏 수익이 몇 개월에 몰리는가**: 상위 {len(top)}개 달이 전체 양(+)의 "
        f"**{top_share * 100:.0f}%** 를 차지({top_str}). 롱 빨간 3달의 숏 수익 몫은 "
        f"**{red_share * 100:.0f}%**. {hit}. 소수 달 집중일수록 「하락장 베타」 신호이고, "
        "고르게 퍼져 있을수록 「규칙」에 가깝다(WAN-285 판정의 월별 확인)."
    )


def _gapped_symbols(symbols: Sequence[str], *, start: str, end: str) -> frozenset[str]:
    """이 창에서 **자기 펀딩이 0행**인 심볼 집합 — DB 커버리지로 동적 판정한다.

    하드코딩 목록이 아니라 실제 `funding_rate` 테이블을 조회한다. WAN-292 백필 후
    DOGE·LINK·LTC는 실 펀딩이 있어 빠진다(= 대리 자동 해제의 독립 증거). `--from-csv`가
    저장된 `funding_gap` 라벨을 이 값으로 self-heal 하는 데 쓴다.
    """
    start_ms = parse_date_ms(start)
    end_ms = parse_date_ms(end)
    store = FundingRateStore(harness.DB_PATH)
    try:
        gapped = {
            sym for sym in symbols if not store.get_rates(sym, start_ms=start_ms, end_ms=end_ms)
        }
    finally:
        store.close()
    return frozenset(gapped)


def _refresh_funding_gap(
    dir_rows: Sequence[DirMonthlyRow], *, start: str, end: str
) -> list[DirMonthlyRow]:
    """저장된 `funding_gap` 라벨을 실제 펀딩 커버리지로 다시 찍는다(`--from-csv` self-heal).

    옛 CSV는 하드코딩 목록으로 신규 3종목을 `True`로 박아 뒀는데, WAN-292 백필 후 실 펀딩이
    생겨 실제로는 `False`다. 라벨이 아니라 데이터로 고정한다(저장소 관행).
    """
    gapped = _gapped_symbols(sorted({r.symbol for r in dir_rows}), start=start, end=end)
    return [r.model_copy(update={"funding_gap": r.symbol in gapped}) for r in dir_rows]


def _tf_short_profit(dir_rows: Sequence[DirMonthlyRow], tf: str) -> dict[str, float]:
    """한 TF의 월별 숏 순수익 %p 합 — TF 축 판정용."""
    out: dict[str, float] = {}
    for r in dir_rows:
        if r.direction == DIR_SHORT and r.timeframe == tf:
            out[r.month] = out.get(r.month, 0.0) + r.net_pp_sum
    return out


def _tf_verdict_line(
    dir_rows: Sequence[DirMonthlyRow], book_rows: Sequence[BookMonthlyRow], tf: str = "15m"
) -> str:
    """이 TF 축이 집계 판정(숏 = 소수 상승 달 집중 = 하락장 베타)을 유지하는지 한 줄(완료기준 3)."""
    profit = _tf_short_profit(dir_rows, tf)
    positive = {m: p for m, p in profit.items() if p > 0}
    if not positive:
        return f"**{tf} 축 판정**: 숏이 양(+)인 달이 없어 판정 표본이 없다."
    total = sum(positive.values())
    ranked = sorted(positive.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[: min(5, len(ranked))]
    top_share = sum(p for _, p in top) / total if total else 0.0
    btc = {r.month: r.btc_buy_hold for r in book_rows if r.arm == ARM_LONG_SHORT}
    up_share = sum(p for m, p in positive.items() if btc.get(m, 0.0) >= 0) / total if total else 0.0
    # 집계 판정(`_verdict_line`)과 같은 자 — 소수 달 집중도(top_share)로 유지/약화를 가른다.
    # up/down 몫은 게이트가 아니라 맥락으로만 보고한다(경계값 흔들림 방지).
    concentrated = top_share >= 0.50
    tail = (
        f"소수 달 집중(상승 달 몫 {up_share * 100:.0f}%) → 1h·2h·4h의 「숏 = 하락장 베타」 "
        "판정을 **유지**한다"
        if concentrated
        else f"집중도가 낮다(상승 달 몫 {up_share * 100:.0f}%) → 그 판정을 **약화**시킨다"
        "(더 고르게 퍼짐)"
    )
    return (
        f"**{tf} 축 판정**: 상위 {len(top)}개 달이 숏 양(+)의 **{top_share * 100:.0f}%** — {tail}."
    )


def build_summary_markdown(
    dir_rows: Sequence[DirMonthlyRow],
    book_rows: Sequence[BookMonthlyRow],
    *,
    cell_csv: Path = DEFAULT_CELL_CSV,
    book_csv: Path = DEFAULT_BOOK_CSV,
    funding_note: str = "",
) -> str:
    scopes = sorted(
        {r.scope for r in book_rows},
        key=lambda s: (0 if s == "all" else 1, timeframe_to_ms(s) if s != "all" else 0),
    )
    agg_scope = "all" if any(r.scope == "all" for r in book_rows) else (scopes[0] if scopes else "")
    down, up, net = regime_split(book_rows, agg_scope) if agg_scope else (0.0, 0.0, 0.0)
    conflicts = conflict_months(dir_rows)
    conflict_syms = sorted({s for s, _ in conflicts})

    parts: list[str] = []
    parts.append("# WAN-288 — 롱/숏 × 종목 × TF별 OOS 월별 수익률 (숏 = 하락장 베타 확인)\n")
    parts.append(
        "> 사용자 요청(2026-08-12). WAN-285의 판정 **「숏 수익은 채택 근거 아님 — 상당 부분이 "
        "하락장 베타」** 를 월별로 눈으로 확인한다. 측정 전용 · `short_enabled`는 이 모듈 안에서만 "
        "True(`ConfluenceParams().short_enabled == False` 유지) · cap_only 5배 · `baseline` 단독 · "
        "oos_warm 정본(WAN-166).\n"
    )
    parts.append(
        "> **WAN-291 재산출(2026-08-12)**: WAN-292 펀딩 백필 반영 후 **4TF(15m·1h·2h·4h) 전체를 "
        "실 펀딩으로 다시 돌렸다**. 신규 3종목(DOGE·LINK·LTC)의 BTC 대리 펀딩이 자동 해제됐다 — "
        "롱-온리 북의 기존 6종목은 `wan180_monthly_returns.csv`와 거래 수가 비트 일치하고 "
        "(펀딩 불변), 신규 3종목만 실 펀딩으로 월수익률이 미세하게 달라진다(대리 해제의 증거).\n"
    )
    if funding_note:
        parts.append(f"신규 3종목 펀딩 대리: {funding_note}\n")

    parts.append(f"## 월별 합산 — 롱-온리 북 vs 롱+숏 북 (스코프 `{agg_scope}`)\n")
    if agg_scope:
        parts.append(_two_line_table(book_rows, agg_scope) + "\n")

    parts.append("## 하락/상승 달 숏 기여 (핵심 세 줄)\n")
    parts.append(
        f"1. **하락 달(BTC BH<0)의 숏 기여 합**: {_fmt_pct(down)}\n"
        f"2. **상승 달의 숏 기여 합**: {_fmt_pct(up)} (숏이 까먹는 부분)\n"
        f"3. **전체 순합(1+2)**: {_fmt_pct(net)} — 「하락장 헤지로 값을 하나」의 답.\n"
    )
    parts.append(_verdict_line(dir_rows, book_rows) + "\n")
    parts.append(_tf_verdict_line(dir_rows, book_rows, "15m") + "\n")

    parts.append("## 🚨 넷팅 안 함 — 롱·숏 양방향 상충 건수\n")
    parts.append(
        f"같은 (종목, 월)에 롱·숏 양쪽이 잡힌 상충: **{len(conflicts)}건** "
        f"(종목 {len(conflict_syms)}개: {', '.join(conflict_syms) if conflict_syms else '없음'}). "
        "다른 TF 간이라도 같은 종목이 한 달에 롱·숏 양쪽으로 열리면 백테스트 북은 상계하지 않고 "
        "둘 다 지갑·명목 상한을 따로 먹는다(같은 (종목,TF) 칸 안에서만 롱·숏 동시 불가). 넷팅 "
        "상계는 실거래 계층(WAN-241/235) 소관이며 이 측정 밖이다.\n"
    )

    gap_syms = sorted({_short(r.symbol) for r in dir_rows if r.funding_gap})
    if gap_syms:
        proxy = " — 실행이 BTC 대리로 보정(WAN-180 관행)" if funding_note else " — 대리 미적용"
        gap_line = f"- †({', '.join(gap_syms)})는 이 창에서 펀딩 0행이라 순수익 낙관{proxy}.\n"
    else:
        gap_line = (
            "- †펀딩 공백 종목 없음 — 신규 3종목(DOGE·LINK·LTC)은 WAN-292 백필로 이 창에서 "
            "**실 펀딩**을 쓴다(BTC 대리 자동 해제 · WAN-291).\n"
        )
    parts.append("## ⚠️ 경고 (인용 시 함께 옮길 것)\n")
    parts.append(
        "- 월별 수익률 %는 5배 북을 복리로 굴린 값이라 **실현 수익이 아니다**(WAN-213 복리 착시) "
        "— 방향·분포로만 읽는다.\n"
        "- 전부 `baseline`(닿으면 체결) **낙관 렌즈** 위 값. 큐 우선순위는 틱·호가(WAN-98, "
        "Canceled) 소관.\n"
        "- **「하락장에서 숏이 번다」는 거의 동어반복** — 숏은 가격 내리면 벌게 설계됨. 채택 "
        "근거가 아니다. 진짜 질문은 (a) **무작위 숏보다** 잘 버나(WAN-285 매칭 널이 "
        "「아니오」에 가깝게 답), (b) 하락 달 이득이 상승 달 손실 + 비용까지 합쳐도 "
        "남나(위 세 줄).\n"
        "- **바이앤홀드는 사후(월말) 라벨** — 월초엔 그 달이 하락장일지 모른다. 실전용 장세 필터는 "
        "별도 파라미터가 붙는 다른 이슈.\n"
        f"{gap_line}"
        "- 「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변 — 이 표는 숏 수익의 시간 분포를 "
        "보는 것이지 엣지를 뒤집는 게 아니다.\n"
    )

    parts.append("## 재현\n")
    parts.append(
        "```\n"
        "# WAN-292 펀딩 백필 반영 후 4TF 전체 실 펀딩 재산출(--append 아님). 15m·6년은 무겁다.\n"
        "uv run python -m backtest.wan288_monthly_long_short --tf 15m,1h,2h,4h --jobs 4\n"
        "uv run python -m backtest.wan288_monthly_long_short --from-csv   # 요약만 재생성\n"
        "```\n"
        f"원자료: `{cell_csv}`(방향 상세) · `{book_csv}`(두-팔 북).\n"
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-288 월별 롱/숏 수익률")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--jobs",
        type=int,
        default=harness.default_jobs(),
        help="(심볼, TF) 단위 병렬 워커 수(미지정이면 ALPHABLOCK_BACKTEST_JOBS, 기본 4, WAN-294)",
    )
    parser.add_argument("--no-funding-proxy", action="store_true", help="신규 종목 펀딩 대리 끔.")
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument("--append", action="store_true", help="새 TF를 기존 CSV에 이어 붙인다.")
    parser.add_argument("--cell-csv", type=Path, default=DEFAULT_CELL_CSV)
    parser.add_argument("--book-csv", type=Path, default=DEFAULT_BOOK_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    out_cell: Path = args.cell_csv
    out_book: Path = args.book_csv
    out_md: Path = args.summary
    funding_note = ""

    if args.from_csv:
        dir_rows = dir_from_csv(out_cell)
        book_rows = book_from_csv(out_book)
        print(f"[wan288] CSV 로드 — 방향 {len(dir_rows)}행 · 북 {len(book_rows)}행 (재실행 없음)")
        # 저장된 funding_gap 라벨을 실제 펀딩 커버리지로 self-heal(WAN-292 백필 반영).
        healed = _refresh_funding_gap(dir_rows, start=args.start, end=args.end)
        if [r.funding_gap for r in healed] != [r.funding_gap for r in dir_rows]:
            dir_rows = healed
            dir_to_frame(dir_rows).to_csv(out_cell, index=False)
            print(f"[wan288] funding_gap 라벨을 실제 커버리지로 갱신 → {out_cell}")
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        cells = run_cells(
            symbols, timeframes, start=args.start, end=args.end, jobs=args.jobs, short_enabled=True
        )
        if not args.no_funding_proxy:
            cells, funding_note = apply_funding_proxy(cells)
            if funding_note:
                print(f"[wan288] {funding_note}")
        dir_rows, book_rows = build_rows(cells, start=args.start, end=args.end)
        if args.append:
            if out_cell.exists():
                dir_rows = merge_dir(dir_from_csv(out_cell), dir_rows)
            if out_book.exists():
                book_rows = merge_book(book_from_csv(out_book), book_rows)
        out_cell.parent.mkdir(parents=True, exist_ok=True)
        dir_to_frame(dir_rows).to_csv(out_cell, index=False)
        book_to_frame(book_rows).to_csv(out_book, index=False)
        print(f"[wan288] 방향 {len(dir_rows)}행 → {out_cell}")
        print(f"[wan288] 북 {len(book_rows)}행 → {out_book}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            dir_rows, book_rows, cell_csv=out_cell, book_csv=out_book, funding_note=funding_note
        ),
        encoding="utf-8",
    )
    print(f"[wan288] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
