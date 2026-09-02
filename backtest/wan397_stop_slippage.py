"""WAN-397: 슬리피지 5bp가 현실적인가 — 손절 봉 조건부 분포(§1) · 체결내역 실측(§2) · 민감도(§3).

## 한 줄

`BacktestConfig.slippage = 0.0005`(5bp)는 **한 번도 실측된 적 없는 가정값**이다. 이 모듈은
그 값을 세 자로 잰다 — 손절이 실제로 난 1분봉의 변동폭(§1) · 그 분의 체결내역을 우리 주문
크기만큼 걸어 본 체결 단가(§2) · 요율을 흔들었을 때 채택 좌표가 어디로 가는가(§3).

🚨 **요율을 바꾸지 않는다.** 전환은 **재-베이스라인 = 사용자 결정**(WAN-92 소관)이고 이
모듈은 **숫자만** 낸다. `BacktestConfig.slippage`·`ConfluenceParams()`·`LeverageBookParams()`
전부 불변이고, 슬리피지 축은 `iter_book_segments(slippage=)`라는 **배치 인자**로만 흔든다.

## 슬리피지가 붙는 자리는 청산 쪽뿐이다 (WAN-396)

진입은 지정가(메이커)라 엔진이 슬리피지를 **안 붙이고**(WAN-396이 잡은 버그가 정확히
「분해가 붙지도 않은 진입 슬리피지를 계상」이었다) 익절도 메이커다(WAN-370). 남는 것은
**테이커 청산 = 손절 + 데이터 종료**뿐이다. 그래서 §1·§2의 대상이 「손절 봉」으로 좁혀지고,
§3의 팔 사이 차이는 통째로 그 청산의 몫이다.

## §1 — 두 자로 잰다 (봉 변동폭 · 손절가 아래 이탈)

* `bar_range_bp` = `(고가 − 저가) / ((고가+저가)/2)` — 이슈 본문 표와 **같은 양**이고,
  손절 봉으로 **조건 잡은** 판을 종목 전체 분포와 나란히 낸다.
* `adverse_bp` = 롱이면 `(손절가 − 저가) / 손절가` — **그 1분에 손절가 아래로 얼마나 더
  갔나**. 1분봉 해상도에서 시장가 청산이 받을 수 있는 **최악**이라(그 분에 저가보다 나쁜
  가격은 거래되지 않았다) 이쪽이 슬리피지에 훨씬 가까운 자다.

⚠️⚠️ **둘 다 슬리피지가 아니다.** 앞엣것은 *「그 1분 동안 가격이 움직인 폭」*, 뒤엣것은
*「그 1분의 최악 가격까지의 거리」*이고 슬리피지는 *「내 주문이 호가창을 먹으며 밀린 폭」*
이다. §1은 **자릿수와 조건부 이동**을 말하고, 「얼마인가」는 §2가 답한다.

## §2 — 체결내역으로 시장충격 (하한 추정)

손절이 난 그 1분의 체결내역(`data.agg_trade_archive`, WAN-347/348이 뚫어 둔 유일한 길)에서
**손절가에 처음 닿은 체결부터** 우리 주문 수량만큼 누적해 체결 단가를 낸다. 크기를 넣는
것이 핵심이다 — 거래의 43.7%가 ADV 0.5% 한도에 걸리므로(WAN-346) 크기를 빼고 재면 「5bp가
맞다」는 답이 나오는데 그건 **우리가 낼 주문이 아니다**.

🚨 **하한이다.** 인쇄된 체결량을 **우리가 전부 먹을 수 있다고** 보는데 실제로는 다른 시장가
주문과 경쟁하고, 호가 깊이·큐 우선순위는 체결내역이 답하지 못한다(WAN-98 Canceled).
실제 슬리피지는 이 값보다 **크지 작지 않다**.

## §3 — 민감도는 산술이 아니라 배치로 잰다

비용은 후보 집합을 바꾸지 않으므로(WAN-370) **후보를 한 번 만들고 슬리피지마다 배치만**
다시 한다. 그래서 이 표는 선형 외삽이 아니라 **실제 지갑 재배치**를 거친 값이고, 같은 행에
선형 예측을 나란히 실어 **외삽이 얼마나 틀리는지**까지 낸다(WAN-359가 *「북 위에서는 두 극단
사이 보간이 깨진다」*를 실측한 뒤로 이 대조는 공짜로 얻을 수 있으면 반드시 낸다).

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목 × 4TF 한 지갑 · 못 박은 6년 창 · 존폭 필터 **끔**(WAN-384) · 인과 취소(WAN-365) ·
재진입 ON(band, WAN-273) · cap_only 5배 · 익절 메이커(WAN-370) · `baseline` 렌즈.

📌 **판정 자는 거래당 net R이다** — 이 좌표에서 총수익 %·MDD는 포화하거나 정의를 잃는다
(WAN-378/386/395). 복리는 끈다(`compound_sizing=False`, WAN-395와 같은 판).

## 검산

* **(a) 기준 팔 ≡ 적재된 채택 좌표 행** — 슬리피지 5bp 팔의 `oos_warm`이
  `wan395_exit_multiple_grid.csv`의 채택 점(`기준`·가드 0.003·배수 1.5·`baseline`)과
  거래 수·승률·거래당 net R까지 **비트 일치**해야 한다. 이 모듈은 그 표와 **다른 코드
  경로**로 배치하므로(팔 후보가 아니라 엔진 base+재진입 목록) 같은 숫자가 나오는 것이
  자격 증명이다.
* **(b) 슬리피지가 후보를 안 바꾼다** — 모든 팔의 `num_candidates`가 같아야 한다.
* **(c) 0bp 팔의 슬리피지 비용이 정확히 0** — 팔이 라벨이 아니라 실제로 걸렸다는 직접 증거.
* **(d) §2 틱 고·저가 ≡ 저장 1분봉** — 출처가 다른 두 자료가 어긋나면 엉뚱한 파일을 편 것이라
  §2 판정 전체가 무효다(WAN-348/359 관행).

재현::

    uv run python -m backtest.wan397_stop_slippage --jobs 4              # §1 + §3
    uv run python -m backtest.wan397_stop_slippage --part ticks          # §2 (네트워크)
    uv run python -m backtest.wan397_stop_slippage --from-csv            # 요약만
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.confirmation_arm import ARM_BASE, ARM_C_OFFSET
from backtest.leverage_book import LeverageBookParams
from backtest.models import BacktestConfig, ExitReason
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan370_cost_decomposition import decompose_trade
from data import agg_trade_archive
from data.storage import OhlcvStore

__all__ = [
    "ADOPTED_SLIPPAGE",
    "ChecksumRow",
    "PRIMARY_SEGMENT",
    "SLIPPAGE_GRID",
    "StopExit",
    "adverse_bp",
    "bar_range_bp",
    "build_payloads",
    "census_rows",
    "linear_slippage_r",
    "main",
    "on_adopted_coordinates",
    "place",
    "sample_exits",
    "segment_windows",
    "sensitivity_rows",
    "taker_exits",
    "walk_tape",
]

REPORTS_DIR = Path("backtest/reports")
DETAIL_CSV = REPORTS_DIR / "wan397_stop_bar_detail.csv"
CENSUS_CSV = REPORTS_DIR / "wan397_stop_bar_census.csv"
SENSITIVITY_CSV = REPORTS_DIR / "wan397_slippage_sensitivity.csv"
TICK_CSV = REPORTS_DIR / "wan397_tick_slippage.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan397_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan397_slippage_summary.md"

#: 적재된 채택 좌표 행 — 검산 (a)의 기준(같은 좌표를 다른 코드 경로로 낸 표).
ADOPTED_REFERENCE_CSV = REPORTS_DIR / "wan395_exit_multiple_grid.csv"

#: 채택 기본값(`BacktestConfig.slippage`). **이 모듈은 이 값을 바꾸지 않는다.**
ADOPTED_SLIPPAGE = 0.0005

#: §3 팔. 0bp는 대조군(검산 (c))이고 나머지는 이슈 완료기준 3의 5 → 10 → 15bp에 20bp를 더한 것.
SLIPPAGE_GRID: tuple[float, ...] = (0.0, 0.0005, 0.0010, 0.0015, 0.0020)

#: 주 구간 — 판정은 여기서 낸다(WAN-166 따뜻한 연속 OOS).
PRIMARY_SEGMENT = "oos_warm"

#: §2 표본 — WAN-348 선례(TF 층화 무작위 · 시드 고정) 그대로.
TICK_SAMPLE_SIZE = 100
TICK_SEED = 397

#: §2 주문 크기 배수 — **슬리피지는 사이즈에 의존한다**(이슈 본문). 이 좌표의 실제 주문은
#: 복리를 끈 판이라 명목이 ~1만 달러에 머무는데, 계좌가 커지면 같은 규칙이 훨씬 큰 주문을
#: 낸다. 같은 테이프를 배수만큼 더 먹어 보면 그 의존이 숫자로 나온다(파일은 이미 받아 둔
#: 것이라 공짜다).
SIZE_MULTIPLES: tuple[float, ...] = (1.0, 10.0, 100.0)

#: 「0과 구분되지 않는다」 선(WAN-366 규약).
NOISE_R = 0.005

#: 분위를 낼 지점.
QUANTILES: tuple[float, ...] = (0.25, 0.50, 0.75, 0.90, 0.99)


def taker_exit_reasons(cfg: BacktestConfig) -> frozenset[ExitReason]:
    """이 설정에서 **테이커로 청산되는** 사유 — 슬리피지를 무는 자리다.

    🚨 **여기서 사유를 다시 정하지 않는다** — 그 분기의 단일 소스는
    `BacktestConfig.exit_liquidity`(WAN-370)이고 이 함수는 그것에 **물어본다**. 목록을 손으로
    적으면 익절 유동성을 바꾼 팔에서 라벨과 동작이 갈린다(WAN-91/95/112/123/159 부류).
    """
    from common.costs import Liquidity

    return frozenset(r for r in ExitReason if cfg.exit_liquidity(r) is Liquidity.TAKER)


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def _cell_kwargs() -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계)."""
    return {
        **ADOPTED_CELL_KWARGS,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    }


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    cache: PayloadCache | None = None,
) -> list[CellPayload]:
    """무거운 패스는 **여기 한 번**이다 — 슬리피지는 배치 인자라 후보를 안 바꾼다(WAN-370).

    📌 **`confirmation_arms=(기준,)`을 요청하는 것은 순수한 컴퓨트 결정이다.** 그 인자는
    `payload.arm_candidates`를 **더할 뿐** `payload.candidates`·`reentry_candidates`(이 모듈이
    실제로 배치하는 목록)를 한 글자도 안 바꾸는데, WAN-394/395가 같은 모양의 `_Task`로
    캐시를 채워 두어 이 좌표가 **디스크에서 그대로 나온다**(안 맞추면 후보 생성만 5시간 —
    WAN-386 실측 4시간 40분). 기준 팔만 요청하므로 확인 트리거 **관측**은 켜지지 않는다
    (WAN-394 §0이 좁혀 둔 조건). 그래도 이 선택이 숫자를 안 바꾼다는 것은 주장이 아니라
    **검산 (a)**다 — 적재된 채택 좌표 행과 비트 일치해야 한다.
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=True,
        engine_check=False,
        confirmation_arms=(ARM_BASE,),
        confirmation_multiples=(1.5,),
        confirmation_offset=ARM_C_OFFSET,
        payload_cache=cache,
        **_cell_kwargs(),  # type: ignore[arg-type]
    )


def place(
    payloads: Sequence[CellPayload],
    *,
    slippage: float,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    compound: bool = False,
) -> list[BookSegment]:
    """채택 북 배치 — 흔드는 축은 `slippage` 하나다.

    🚨 **익절 청산 유동성을 여기에도 명시한다**(한 표가 한 회계) — 익절은 메이커라 이 팔
    사이에서 슬리피지를 안 물고, 그래서 팔 차이가 통째로 **테이커 청산의 몫**이다.

    `compound=False`가 이 좌표의 판이다 — 복리 총수익은 −100%에 포화해 점을 구분하지 못한다
    (WAN-346 §2 · WAN-395). 검산 (a)는 이 판으로 적재된 표와 대조한다(그쪽도 같은 판이다).
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        slippage=slippage,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def _cfg(slippage: float) -> BacktestConfig:
    """비용 분해가 쓰는 설정 — 🚨 배치와 **같은** 요율이라야 항등식이 닫힌다."""
    return harness.build_config(
        harness.DEFAULT_TIMEFRAMES[0],
        slippage=slippage,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# §1 — 손절 봉 하나하나
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StopExit:
    """테이커 청산 체결 하나 — §1의 원자이고 §2의 표본 단위다."""

    segment: str
    symbol: str
    timeframe: str
    reason: str
    exit_ms: int
    """청산 체결 **1분 스텝**의 `open_time`(엔진의 서브스텝 단위 그대로)."""
    is_long: bool
    stop_price: float
    """이 거래의 손절 참조가(배치 기록 그대로) — 시장가 청산의 **기준가**다."""
    exit_price: float
    """엔진이 실제로 물린 체결가(슬리피지 반영)."""
    quantity: float
    notional: float
    """이 청산의 명목(기준가 × 수량) — §2가 「얼마짜리 주문인가」로 쓴다."""


def taker_exits(segment: BookSegment, cfg: BacktestConfig) -> list[StopExit]:
    """그 구간 지갑의 **테이커 청산** 체결을 전부 낸다(사유는 `exit_liquidity`가 정한다)."""
    reasons = taker_exit_reasons(cfg)
    out: list[StopExit] = []
    for trade, placement in segment.trades_with_placements():
        for fill in trade.exits:
            if fill.reason not in reasons:
                continue
            out.append(
                StopExit(
                    segment=segment.segment,
                    symbol=placement.cell[0],
                    timeframe=placement.cell[1],
                    reason=fill.reason.value,
                    exit_ms=fill.time,
                    is_long=trade.side.sign > 0,
                    stop_price=placement.stop_price,
                    exit_price=fill.price,
                    quantity=fill.quantity,
                    notional=placement.stop_price * fill.quantity,
                )
            )
    return out


def bar_range_bp(high: float, low: float) -> float:
    """`(고가 − 저가) / 중간값`, bp. 이슈 본문 표와 같은 양(분모만 중간값으로 못 박았다)."""
    mid = (high + low) / 2.0
    return float("nan") if mid <= 0 else (high - low) / mid * 10_000.0


def adverse_bp(stop_price: float, high: float, low: float, *, is_long: bool) -> float:
    """손절가에서 그 분의 **불리한 극값**까지의 거리, bp (롱이면 저가 · 숏이면 고가).

    1분봉 해상도에서 시장가 청산이 받을 수 있는 **최악**이다 — 그 분에 이 값보다 나쁜 가격은
    거래되지 않았다. 손절가가 극값보다 이미 불리하면(스텝 안에서 손절가를 안 뚫은 경우) 0이다.
    """
    if stop_price <= 0:
        return float("nan")
    gap = (stop_price - low) if is_long else (high - stop_price)
    return max(gap, 0.0) / stop_price * 10_000.0


def attach_bars(exits: Sequence[StopExit], *, db_path: str = harness.DB_PATH) -> pd.DataFrame:
    """청산마다 **그 1분봉**의 고가·저가를 붙인다(종목마다 한 번씩만 읽는다).

    붙지 않은 행은 **지우지 않고** `NaN`으로 남겨 요약이 몇 건이 빠졌는지 밝히게 한다
    (조용히 표본을 줄이지 않는다 — WAN-362 관행).
    """
    frame = pd.DataFrame([asdict(e) for e in exits])
    if frame.empty:
        return frame
    store = OhlcvStore(db_path)
    parts: list[pd.DataFrame] = []
    try:
        for symbol, group in frame.groupby("symbol"):
            bars = store.load(
                str(symbol),
                "1m",
                start_ms=int(group["exit_ms"].min()),
                end_ms=int(group["exit_ms"].max()) + 60_000,
            )
            if bars.empty:
                parts.append(group.assign(bar_high=math.nan, bar_low=math.nan))
                continue
            indexed = bars.set_index("open_time")[["high", "low"]]
            indexed.columns = ["bar_high", "bar_low"]
            parts.append(group.join(indexed, on="exit_ms"))
    finally:
        store.close()
    out = pd.concat(parts).sort_index()
    out["bar_range_bp"] = [
        bar_range_bp(h, low) for h, low in zip(out["bar_high"], out["bar_low"], strict=True)
    ]
    out["adverse_bp"] = [
        adverse_bp(s, h, low, is_long=bool(is_long))
        for s, h, low, is_long in zip(
            out["stop_price"], out["bar_high"], out["bar_low"], out["is_long"], strict=True
        )
    ]
    out["charged_slippage_bp"] = (
        (out["stop_price"] - out["exit_price"]).abs() / out["stop_price"] * 10_000.0
    )
    return out


def _quantiles(values: Sequence[float]) -> dict[str, float]:
    arr = np.asarray([v for v in values if not math.isnan(v)], dtype=float)
    if arr.size == 0:
        return {f"p{int(q * 100)}": float("nan") for q in QUANTILES}
    return {f"p{int(q * 100)}": float(np.quantile(arr, q)) for q in QUANTILES}


def _percentile_of(values: Sequence[float], target: float) -> float:
    """`target`이 이 분포의 몇 분위인가(0~1). 완료기준 1의 답이 이 열이다."""
    arr = np.asarray([v for v in values if not math.isnan(v)], dtype=float)
    if arr.size == 0:
        return float("nan")
    return float((arr <= target).mean())


class CensusRow(BaseModel):
    """§1 한 축(전체·종목·TF)의 손절 봉 분포 + 5bp의 분위."""

    model_config = ConfigDict(frozen=True)

    segment: str
    axis: str
    bucket: str
    metric: str
    """`bar_range_bp`(봉 변동폭) 또는 `adverse_bp`(손절가 아래 이탈)."""
    conditional: bool
    """`True`면 **손절 봉으로 조건 잡은** 판, `False`면 그 종목 1분봉 전체(대조군)."""
    num_bars: int
    p25: float
    p50: float
    p75: float
    p90: float
    p99: float
    share_above_5bp: float
    """5bp를 넘는 봉의 비율 — 이슈 본문 표의 마지막 열과 같은 자."""
    percentile_of_5bp: float
    """5bp가 이 분포의 몇 분위인가 — **완료기준 1의 답**."""


UNCONDITIONAL_BUCKET = "(그 종목 1분봉 전체)"


def segment_windows(detail: pd.DataFrame) -> dict[str, tuple[int, int]]:
    """구간마다 **그 구간 거래가 실제로 산 달력 창** — 대조군을 같은 기간으로 맞추는 자.

    6년 전체 분포와 OOS 손절 봉을 나란히 놓으면 「손절이 격렬한가」와 「그 시기가 격렬한가」가
    섞인다. 그래서 대조군은 **그 구간의 창**에서 낸다(구간 경계를 다시 계산하지 않고 그
    구간이 실제로 청산한 시각의 최소·최대를 쓴다 — 배치가 정한 것을 그대로 물려받는다).
    """
    out: dict[str, tuple[int, int]] = {}
    for segment, group in detail.groupby("segment"):
        out[str(segment)] = (int(group["exit_ms"].min()), int(group["exit_ms"].max()) + 60_000)
    return out


def unconditional_ranges(
    symbols: Sequence[str],
    windows: dict[str, tuple[int, int]],
    *,
    db_path: str = harness.DB_PATH,
) -> dict[tuple[str, str], dict[str, float]]:
    """`(구간, 종목)`마다 그 창 **1분봉 전체**의 `bar_range_bp` 분위 — 조건부 판의 대조군.

    종목마다 **한 번만** 읽고(가장 넓은 창) 구간마다 잘라 쓴다 — 구간별로 다시 읽으면 6년치
    1분봉을 네 번 판다.

    ⚠️ 이슈 본문 표(최근 20,000분)와 **같은 수가 아니다** — 그 표는 약 2주 표본이고 이쪽은
    구간의 창 전체다(본문이 스스로 「구간에 민감하다」고 경고한 자리).
    """
    if not windows:
        return {}
    lo = min(w[0] for w in windows.values())
    hi = max(w[1] for w in windows.values())
    store = OhlcvStore(db_path)
    out: dict[tuple[str, str], dict[str, float]] = {}
    try:
        for symbol in symbols:
            name = harness.normalize_symbol(symbol)
            bars = store.load(name, "1m", start_ms=lo, end_ms=hi)
            if bars.empty:
                continue
            times = bars["open_time"].to_numpy(dtype="int64")
            high = bars["high"].to_numpy(dtype=float)
            low = bars["low"].to_numpy(dtype=float)
            mid = (high + low) / 2.0
            with np.errstate(divide="ignore", invalid="ignore"):
                ranges = np.where(mid > 0, (high - low) / mid * 10_000.0, np.nan)
            for segment, (start_ms, end_ms) in windows.items():
                mask = (times >= start_ms) & (times < end_ms)
                window = ranges[mask]
                window = window[~np.isnan(window)]
                if window.size == 0:
                    continue
                stats = {f"p{int(q * 100)}": float(np.quantile(window, q)) for q in QUANTILES}
                stats["num_bars"] = float(window.size)
                stats["share_above_5bp"] = float((window > 5.0).mean())
                stats["percentile_of_5bp"] = float((window <= 5.0).mean())
                out[(segment, name)] = stats
    finally:
        store.close()
    return out


def census_rows(
    detail: pd.DataFrame, unconditional: dict[tuple[str, str], dict[str, float]]
) -> list[CensusRow]:
    """§1 표 — 구간 × 축(전체·종목·TF) × 자(봉 변동폭·손절가 아래 이탈)."""
    rows: list[CensusRow] = []
    if detail.empty:
        return rows
    for segment, seg_frame in detail.groupby("segment"):
        axes: list[tuple[str, str, pd.DataFrame]] = [("overall", "전체", seg_frame)]
        axes += [("symbol", str(s), g) for s, g in seg_frame.groupby("symbol")]
        axes += [("timeframe", str(t), g) for t, g in seg_frame.groupby("timeframe")]
        for axis, bucket, group in axes:
            for metric in ("bar_range_bp", "adverse_bp"):
                values = list(group[metric])
                stats = _quantiles(values)
                clean = [v for v in values if not math.isnan(v)]
                rows.append(
                    CensusRow(
                        segment=str(segment),
                        axis=axis,
                        bucket=bucket,
                        metric=metric,
                        conditional=True,
                        num_bars=len(clean),
                        share_above_5bp=(
                            float(np.mean([v > 5.0 for v in clean])) if clean else float("nan")
                        ),
                        percentile_of_5bp=_percentile_of(values, 5.0),
                        **stats,
                    )
                )
            stats_all = unconditional.get((str(segment), bucket)) if axis == "symbol" else None
            if stats_all is not None:
                rows.append(
                    CensusRow(
                        segment=str(segment),
                        axis=axis,
                        bucket=bucket,
                        metric="bar_range_bp",
                        conditional=False,
                        num_bars=int(stats_all["num_bars"]),
                        p25=stats_all["p25"],
                        p50=stats_all["p50"],
                        p75=stats_all["p75"],
                        p90=stats_all["p90"],
                        p99=stats_all["p99"],
                        share_above_5bp=stats_all["share_above_5bp"],
                        percentile_of_5bp=stats_all["percentile_of_5bp"],
                    )
                )
    return rows


# --------------------------------------------------------------------------- #
# §3 — 슬리피지 민감도
# --------------------------------------------------------------------------- #


def linear_slippage_r(base_slippage_r: float, *, base: float, target: float) -> float:
    """`base`bp에서 잰 `slippage_r`을 `target`bp로 **선형 외삽**한다.

    슬리피지는 체결가에 곱셈으로 붙으므로(`가격 × slip/(1∓slip)`) 요율에 거의 비례한다 —
    이 함수는 그 비례를 그대로 쓴다. 🚨 **북에서는 이 외삽이 깨질 수 있다**(손익이 바뀌면
    사이징·명목 상한을 통해 뒤쪽 배치가 통째로 달라진다 — WAN-359가 실측한 자리). 그래서
    이 값은 예측이고, 같은 행의 실측과 나란히 놓아 **얼마나 틀리는지**를 낸다.
    """
    if base <= 0:
        return float("nan")
    scale = (target / (1.0 - target)) / (base / (1.0 - base))
    return base_slippage_r * scale


class SensitivityRow(BaseModel):
    """§3 한 (슬리피지, 구간)의 채택 북 성적."""

    model_config = ConfigDict(frozen=True)

    slippage_bp: float
    segment: str
    adopted: bool
    """이 팔이 채택 요율(5bp)인가."""
    num_cells: int
    num_candidates: int
    num_trades: int
    win_rate: float
    mean_net_r: float
    net_r_stderr: float
    gross_r: float
    cost_r: float
    slippage_r: float
    stop_fee_r: float
    entry_fee_r: float
    take_profit_fee_r: float
    funding_r: float
    breakeven_win_rate: float
    """`(1 + cost_r) / (1 + 1.5)` — 비용을 물고 본전이 되는 승률(WAN-395와 같은 자)."""
    linear_mean_net_r: float | None
    """5bp 팔에서 선형 외삽한 예측. 5bp 팔 자신은 `None`."""
    linear_gap: float | None
    """`실측 − 예측` — 북 재배치가 외삽을 얼마나 틀리게 하는가."""


def _segment_candidates(payloads: Sequence[CellPayload], segment: str) -> int:
    from backtest.wan169_leverage_book import _segment_cells

    cells = _segment_cells(payloads, segment, "", include_reentry=True)
    return sum(len(c.candidates) for c in cells)


def _aggregate_arm(
    segment: BookSegment, cfg: BacktestConfig, *, num_candidates: int
) -> dict[str, float]:
    """한 (팔, 구간)의 거래당 R 분해 — 비용 산식은 `wan370.decompose_trade` 그대로 쓴다."""
    pairs = segment.trades_with_placements()
    nets: list[float] = []
    totals = dict.fromkeys(("gross", "slippage", "entry", "tp", "stop", "other", "funding"), 0.0)
    for trade, placement in pairs:
        risk = placement.risk_amount
        if risk <= 0:
            continue
        parts = decompose_trade(trade, cfg)
        nets.append(net_r(trade, placement))
        totals["gross"] += parts.gross / risk
        totals["slippage"] += parts.slippage / risk
        totals["entry"] += parts.entry_fee / risk
        totals["tp"] += parts.take_profit_fee / risk
        totals["stop"] += parts.stop_fee / risk
        totals["other"] += parts.other_fee / risk
        totals["funding"] += parts.funding / risk
    n = max(len(nets), 1)
    cost_r = sum(totals[k] for k in ("slippage", "entry", "tp", "stop", "other", "funding")) / n
    return {
        "num_trades": float(segment.row.num_trades),
        "win_rate": segment.row.win_rate,
        "mean_net_r": sum(nets) / n,
        "net_r_stderr": statistics.stdev(nets) / (len(nets) ** 0.5) if len(nets) > 1 else 0.0,
        "gross_r": totals["gross"] / n,
        "cost_r": cost_r,
        "slippage_r": totals["slippage"] / n,
        "stop_fee_r": (totals["stop"] + totals["other"]) / n,
        "entry_fee_r": totals["entry"] / n,
        "take_profit_fee_r": totals["tp"] / n,
        "funding_r": totals["funding"] / n,
        "breakeven_win_rate": (1.0 + cost_r) / (1.0 + 1.5),
        "num_candidates": float(num_candidates),
    }


def sensitivity_rows(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str] = SEGMENT_ORDER,
    grid: Sequence[float] = SLIPPAGE_GRID,
    log: bool = True,
) -> tuple[list[SensitivityRow], dict[str, list[StopExit]]]:
    """§3 격자 — 배치만 반복한다(후보는 이미 있다). 기준 팔의 테이커 청산도 함께 낸다."""
    measured: dict[tuple[float, str], dict[str, float]] = {}
    exits: dict[str, list[StopExit]] = {}
    num_cells = len(payloads)
    for slippage in grid:
        started = time.monotonic()
        cfg = _cfg(slippage)
        book = place(
            payloads, slippage=slippage, start_ms=start_ms, end_ms=end_ms, segments=segments
        )
        for seg in book:
            measured[(slippage, seg.segment)] = _aggregate_arm(
                seg, cfg, num_candidates=_segment_candidates(payloads, seg.segment)
            )
            if slippage == ADOPTED_SLIPPAGE:
                exits[seg.segment] = taker_exits(seg, cfg)
        if log:
            print(
                f"[wan397] 슬리피지 {slippage * 10_000:.0f}bp 배치 "
                f"{time.monotonic() - started:.0f}s",
                flush=True,
            )
    rows: list[SensitivityRow] = []
    for slippage in grid:
        for segment in segments:
            stats = measured[(slippage, segment)]
            base = measured.get((ADOPTED_SLIPPAGE, segment))
            linear: float | None = None
            if base is not None and slippage != ADOPTED_SLIPPAGE:
                predicted_slip = linear_slippage_r(
                    base["slippage_r"], base=ADOPTED_SLIPPAGE, target=slippage
                )
                linear = base["mean_net_r"] - (predicted_slip - base["slippage_r"])
            rows.append(
                SensitivityRow(
                    slippage_bp=slippage * 10_000.0,
                    segment=segment,
                    adopted=slippage == ADOPTED_SLIPPAGE,
                    num_cells=num_cells,
                    num_candidates=int(stats["num_candidates"]),
                    num_trades=int(stats["num_trades"]),
                    win_rate=stats["win_rate"],
                    mean_net_r=stats["mean_net_r"],
                    net_r_stderr=stats["net_r_stderr"],
                    gross_r=stats["gross_r"],
                    cost_r=stats["cost_r"],
                    slippage_r=stats["slippage_r"],
                    stop_fee_r=stats["stop_fee_r"],
                    entry_fee_r=stats["entry_fee_r"],
                    take_profit_fee_r=stats["take_profit_fee_r"],
                    funding_r=stats["funding_r"],
                    breakeven_win_rate=stats["breakeven_win_rate"],
                    linear_mean_net_r=linear,
                    linear_gap=None if linear is None else stats["mean_net_r"] - linear,
                )
            )
    return rows, exits


# --------------------------------------------------------------------------- #
# §2 — 체결내역으로 걸어 본 체결 단가
# --------------------------------------------------------------------------- #


def sample_exits(
    detail: pd.DataFrame, *, size: int = TICK_SAMPLE_SIZE, seed: int = TICK_SEED
) -> pd.DataFrame:
    """TF 층화 무작위 표본(시드 고정) — WAN-348 선례 그대로.

    층별 배정은 그 TF의 **비중에 비례**하고, 반올림으로 남는 자리는 큰 층부터 채운다.
    """
    if detail.empty:
        return detail
    counts = detail["timeframe"].value_counts()
    total = int(counts.sum())
    quota = {tf: int(size * n / total) for tf, n in counts.items()}
    for tf in counts.index:
        if sum(quota.values()) >= size:
            break
        quota[tf] += 1
    rng = random.Random(seed)
    picked: list[int] = []
    for tf, want in quota.items():
        idx = list(detail.index[detail["timeframe"] == tf])
        rng.shuffle(idx)
        picked += idx[: min(want, len(idx))]
    return detail.loc[sorted(picked)]


def walk_tape(
    ticks: Sequence[agg_trade_archive.Tick],
    *,
    stop_price: float,
    quantity: float,
    is_long: bool,
) -> tuple[float, float, float]:
    """손절가에 처음 닿은 체결부터 `quantity`만큼 먹었을 때의 **체결 단가**를 낸다.

    돌려주는 것은 `(체결 단가, 채운 수량, 첫 체결가)`. 첫 체결가는 **크기 없는** 추정이라
    「슬리피지의 얼마가 크기 탓인가」의 대조군이다.

    🚨 **하한이다** — 인쇄된 체결량을 우리가 **전부** 먹을 수 있다고 본다(실제로는 다른
    시장가 주문과 경쟁한다). 호가 깊이·큐 우선순위는 이 자료가 답하지 못한다(WAN-98).
    """
    triggered = False
    filled = 0.0
    notional = 0.0
    first = float("nan")
    for tick in ticks:
        if not triggered:
            if (is_long and tick.price <= stop_price) or (not is_long and tick.price >= stop_price):
                triggered = True
                first = tick.price
            else:
                continue
        take = min(tick.qty, quantity - filled)
        filled += take
        notional += take * tick.price
        if filled >= quantity:
            break
    if filled <= 0:
        return float("nan"), 0.0, first
    return notional / filled, filled, first


class TickRow(BaseModel):
    """§2 표본 한 건 — 하나의 손절 체결에 대한 체결내역 실측."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    exit_ms: int
    day: str
    stop_price: float
    quantity: float
    notional: float
    num_ticks: int
    minute_volume: float
    fill_price: float
    filled_qty: float
    first_price: float
    slippage_bp: float
    """`|체결 단가 − 손절가| / 손절가`, bp — **크기를 넣은** 실측."""
    first_tick_bp: float
    """첫 체결가만으로 잰 값(크기 없음) — 대조군."""
    size_share_of_minute: float
    """우리 주문이 그 1분 거래량의 몇 배/몇 %인가."""
    slippage_bp_x10: float
    """주문이 10배였다면 — 같은 테이프를 그만큼 더 먹었을 때의 체결 단가."""
    slippage_bp_x100: float
    """주문이 100배였다면. 그 분의 체결량이 모자라면 **채운 데까지의 단가**이고
    `short_fill_x100`이 참이 된다(모자란 것을 조용히 안 채운다)."""
    short_fill_x10: bool
    short_fill_x100: bool
    tick_high_matches_bar: bool
    """검산 (d) — 틱 고·저가가 저장 1분봉과 맞는가."""
    note: str = ""


def tick_rows(
    sample: pd.DataFrame,
    *,
    cache_dir: Path = agg_trade_archive.DEFAULT_CACHE_DIR,
    log: bool = True,
) -> tuple[list[TickRow], dict[str, float]]:
    """표본마다 그 1분의 체결내역을 펼쳐 체결 단가를 낸다."""
    rows: list[TickRow] = []
    stats = {"files": 0.0, "bytes": 0.0, "seconds": 0.0, "failed": 0.0, "cached": 0.0}
    by_day: dict[tuple[str, str], list[int]] = {}
    for idx, row in sample.iterrows():
        day = agg_trade_archive.day_of(int(row["exit_ms"]))
        by_day.setdefault((str(row["symbol"]), day), []).append(int(idx))
    for (symbol, day), indices in sorted(by_day.items()):
        fetch = agg_trade_archive.fetch_day(symbol, day, cache_dir=cache_dir)
        stats["files"] += 1
        stats["bytes"] += fetch.size_bytes
        stats["seconds"] += fetch.seconds
        stats["cached"] += 1.0 if fetch.cached else 0.0
        if not fetch.ok or fetch.path is None:
            stats["failed"] += 1
            for idx in indices:
                src = sample.loc[idx]
                rows.append(_empty_tick_row(src, day, note=fetch.note or "받기 실패"))
            continue
        minutes = agg_trade_archive.minutes_ticks(
            fetch.path, [int(sample.loc[i, "exit_ms"]) for i in indices]
        )
        for idx in indices:
            src = sample.loc[idx]
            ticks = minutes[int(src["exit_ms"])]
            rows.append(_tick_row(src, day, ticks))
        if log:
            print(f"[wan397] {symbol} {day}: {len(indices)}건", flush=True)
    return rows, stats


def _empty_tick_row(src: pd.Series, day: str, *, note: str) -> TickRow:
    return TickRow(
        symbol=str(src["symbol"]),
        timeframe=str(src["timeframe"]),
        exit_ms=int(src["exit_ms"]),
        day=day,
        stop_price=float(src["stop_price"]),
        quantity=float(src["quantity"]),
        notional=float(src["notional"]),
        num_ticks=0,
        minute_volume=0.0,
        fill_price=float("nan"),
        filled_qty=0.0,
        first_price=float("nan"),
        slippage_bp=float("nan"),
        first_tick_bp=float("nan"),
        size_share_of_minute=float("nan"),
        slippage_bp_x10=float("nan"),
        slippage_bp_x100=float("nan"),
        short_fill_x10=False,
        short_fill_x100=False,
        tick_high_matches_bar=False,
        note=note,
    )


def _tick_row(src: pd.Series, day: str, ticks: Sequence[agg_trade_archive.Tick]) -> TickRow:
    stop_price = float(src["stop_price"])
    quantity = float(src["quantity"])
    is_long = bool(src["is_long"])
    fill, filled, first = walk_tape(
        ticks, stop_price=stop_price, quantity=quantity, is_long=is_long
    )
    scaled: dict[float, tuple[float, bool]] = {}
    for multiple in SIZE_MULTIPLES[1:]:
        want = quantity * multiple
        price, got, _first = walk_tape(ticks, stop_price=stop_price, quantity=want, is_long=is_long)
        bps = float("nan") if math.isnan(price) else abs(price - stop_price) / stop_price * 10_000.0
        scaled[multiple] = (bps, got + 1e-12 < want)
    volume = sum(t.qty for t in ticks)
    highs = [t.price for t in ticks]
    matches = bool(highs) and (
        math.isclose(max(highs), float(src["bar_high"]), rel_tol=1e-9)
        and math.isclose(min(highs), float(src["bar_low"]), rel_tol=1e-9)
    )
    return TickRow(
        symbol=str(src["symbol"]),
        timeframe=str(src["timeframe"]),
        exit_ms=int(src["exit_ms"]),
        day=day,
        stop_price=stop_price,
        quantity=quantity,
        notional=float(src["notional"]),
        num_ticks=len(ticks),
        minute_volume=volume,
        fill_price=fill,
        filled_qty=filled,
        first_price=first,
        slippage_bp=(
            float("nan") if math.isnan(fill) else abs(fill - stop_price) / stop_price * 10_000.0
        ),
        first_tick_bp=(
            float("nan") if math.isnan(first) else abs(first - stop_price) / stop_price * 10_000.0
        ),
        size_share_of_minute=(quantity / volume if volume > 0 else float("nan")),
        slippage_bp_x10=scaled[10.0][0],
        slippage_bp_x100=scaled[100.0][0],
        short_fill_x10=scaled[10.0][1],
        short_fill_x100=scaled[100.0][1],
        tick_high_matches_bar=matches,
        note="" if filled >= quantity else "그 분의 체결량이 주문 크기에 못 미침",
    )


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def on_adopted_coordinates(
    symbols: Sequence[str], timeframes: Sequence[str], start: str, end: str
) -> bool:
    """이 실행이 채택 좌표(12종목 × 4TF × 못 박은 6년 창)인가.

    🚨 **검산 (a)는 여기서만 뜻이 있다** — 좁혀 돈 파일럿을 적재된 채택 좌표 행과 대조하면
    좌표 차이가 **배선 오류처럼 보인다**(WAN-381이 실측 `5.63e+04`로 겪은 자리). 아니면
    검산이 「건너뜀」으로 찍힌다.
    """
    return (
        [harness.normalize_symbol(s) for s in symbols]
        == [harness.normalize_symbol(s) for s in harness.DEFAULT_SYMBOLS]
        and list(timeframes) == list(harness.DEFAULT_TIMEFRAMES)
        and start == harness.DEFAULT_START
        and end == harness.DEFAULT_END
    )


def adopted_reference() -> dict[str, float] | None:
    """검산 (a)의 기준 — 적재된 `wan395` 채택 점(`기준`·가드 0.003·1.5R·`baseline`) 행."""
    if not ADOPTED_REFERENCE_CSV.exists():
        return None
    frame = pd.read_csv(ADOPTED_REFERENCE_CSV)
    picked = frame[
        (frame["segment"] == PRIMARY_SEGMENT)
        & (frame["adopted_point"].astype(str) == "True")
        & (frame["lens"] == "baseline")
    ]
    if picked.empty:
        return None
    row = picked.iloc[0]
    return {
        "num_trades": float(row["num_trades"]),
        "win_rate": float(row["win_rate"]),
        "mean_net_r": float(row["mean_net_r"]),
    }


class ChecksumRow(BaseModel):
    """검산 한 줄 — 왼쪽·오른쪽·절대차를 그대로 싣는다(통과 여부를 여기서 판단하지 않는다)."""

    model_config = ConfigDict(frozen=True)

    check: str
    metric: str
    left: float
    right: float
    abs_diff: float


def checksums(
    rows: Sequence[SensitivityRow], *, adopted_coordinates: bool = True
) -> list[ChecksumRow]:
    """검산 (a)~(c). `adopted_coordinates`가 거짓이면 (a)는 「건너뜀」으로 찍는다."""
    out: list[ChecksumRow] = []
    by_key = {(r.slippage_bp, r.segment): r for r in rows}
    base = by_key.get((ADOPTED_SLIPPAGE * 10_000.0, PRIMARY_SEGMENT))
    reference = adopted_reference() if adopted_coordinates else None
    if base is not None and reference is not None:
        for metric, left in (
            ("num_trades", float(base.num_trades)),
            ("win_rate", base.win_rate),
            ("mean_net_r", base.mean_net_r),
        ):
            out.append(
                ChecksumRow(
                    check="(a) 기준 팔 ≡ 적재된 채택 좌표 행(wan395)",
                    metric=metric,
                    left=left,
                    right=reference[metric],
                    abs_diff=abs(left - reference[metric]),
                )
            )
    else:
        out.append(
            ChecksumRow(
                check="(a) 기준 팔 ≡ 적재된 채택 좌표 행(wan395)",
                metric="건너뜀(채택 좌표가 아니거나 적재 CSV 없음)",
                left=float("nan"),
                right=float("nan"),
                abs_diff=float("nan"),
            )
        )
    candidates = {r.num_candidates for r in rows if r.segment == PRIMARY_SEGMENT}
    out.append(
        ChecksumRow(
            check="(b) 슬리피지가 후보를 안 바꾼다",
            metric="num_candidates 서로 다른 값 수",
            left=float(len(candidates)),
            right=1.0,
            abs_diff=float(len(candidates) - 1),
        )
    )
    zero = by_key.get((0.0, PRIMARY_SEGMENT))
    if zero is not None:
        out.append(
            ChecksumRow(
                check="(c) 0bp 팔의 슬리피지 비용이 정확히 0",
                metric="slippage_r",
                left=zero.slippage_r,
                right=0.0,
                abs_diff=abs(zero.slippage_r),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _fmt(value: float, digits: int = 4) -> str:
    return (
        "—"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else (f"{value:.{digits}f}")
    )


def render_summary(
    census: pd.DataFrame,
    sensitivity: pd.DataFrame,
    checks: Sequence[ChecksumRow],
    ticks: pd.DataFrame | None,
    *,
    tick_stats: dict[str, float] | None = None,
) -> str:
    """세 절을 사람이 읽는 표로. 🚨 **「그러니 요율을 바꾸자」로 끝내지 않는다**(완료기준 4)."""
    lines: list[str] = [
        "# WAN-397 — 슬리피지 5bp가 현실적인가",
        "",
        "**측정 전용 · 기본값 불변**(`BacktestConfig.slippage = 0.0005` 그대로 · "
        "`ConfluenceParams()`·`LeverageBookParams()` 불변 · 요율 전환은 **재-베이스라인 = "
        "사용자 결정**, WAN-92 소관).",
        "",
        "좌표: 12종목 × 4TF 한 지갑 · 못 박은 6년 창 · 존폭 필터 끔(WAN-384) · 인과 취소"
        "(WAN-365) · 재진입 ON(band) · cap_only 5배 · 익절 메이커(WAN-370) · `baseline` 렌즈 · "
        "복리 끔. 판정 자는 **거래당 net R**.",
        "",
    ]

    # --- §1 ---------------------------------------------------------------- #
    lines += [
        "## §1 손절 봉으로 조건 잡은 1분 변동폭",
        "",
        "⚠️ **봉 변동폭도 「손절가 아래 이탈」도 슬리피지가 아니다** — 앞엣것은 「그 1분에 "
        "가격이 움직인 폭」, 뒤엣것은 「그 1분의 최악 가격까지의 거리」이고 슬리피지는 "
        "「내 주문이 호가창을 먹으며 밀린 폭」이다. 여기서 읽을 것은 **자릿수와 조건부 "
        "이동**이고, 「얼마인가」는 §2가 답한다.",
        "",
    ]
    primary = census[census["segment"] == PRIMARY_SEGMENT]
    overall = primary[(primary["axis"] == "overall") & primary["conditional"]]
    if not overall.empty:
        lines += [
            "| 자 | 건수 | p25 | 중앙값 | p75 | p90 | p99 | 5bp 초과 | **5bp의 분위** |",
            "| -- | --: | --: | --: | --: | --: | --: | --: | --: |",
        ]
        for _, row in overall.iterrows():
            name = "봉 변동폭" if row["metric"] == "bar_range_bp" else "손절가 아래 이탈"
            lines.append(
                f"| {name} | {int(row['num_bars']):,} | {_fmt(row['p25'], 1)} | "
                f"**{_fmt(row['p50'], 1)}** | {_fmt(row['p75'], 1)} | {_fmt(row['p90'], 1)} | "
                f"{_fmt(row['p99'], 1)} | {row['share_above_5bp'] * 100:.1f}% | "
                f"**{row['percentile_of_5bp'] * 100:.1f}%** |"
            )
        lines.append("")
        lines.append(
            "📌 **완료기준 1의 답이 마지막 열이다** — 5bp가 손절 봉 분포의 그 분위에 앉아 "
            "있다(작을수록 5bp가 분포의 아래쪽 = 낙관)."
        )
        lines.append("")

    by_symbol = primary[(primary["axis"] == "symbol")]
    if not by_symbol.empty:
        lines += [
            "### 종목별 — 손절 봉(조건부) vs 그 종목 1분봉 전체(대조군)",
            "",
            "| 종목 | 손절 봉 중앙값 | 전체 중앙값 | 배수 | 손절가 아래 이탈 중앙값 | "
            "5bp의 분위(손절 봉) |",
            "| -- | --: | --: | --: | --: | --: |",
        ]
        for symbol in sorted(by_symbol["bucket"].unique()):
            grp = by_symbol[by_symbol["bucket"] == symbol]
            cond = grp[(grp["metric"] == "bar_range_bp") & grp["conditional"]]
            uncond = grp[(grp["metric"] == "bar_range_bp") & ~grp["conditional"]]
            adverse = grp[(grp["metric"] == "adverse_bp") & grp["conditional"]]
            if cond.empty:
                continue
            c50 = float(cond.iloc[0]["p50"])
            u50 = float(uncond.iloc[0]["p50"]) if not uncond.empty else float("nan")
            ratio = c50 / u50 if u50 and not math.isnan(u50) and u50 > 0 else float("nan")
            a50 = float(adverse.iloc[0]["p50"]) if not adverse.empty else float("nan")
            lines.append(
                f"| {symbol} | {_fmt(c50, 1)} | {_fmt(u50, 1)} | {_fmt(ratio, 2)}× | "
                f"{_fmt(a50, 1)} | {float(cond.iloc[0]['percentile_of_5bp']) * 100:.1f}% |"
            )
        lines.append("")

    by_tf = primary[(primary["axis"] == "timeframe") & primary["conditional"]]
    if not by_tf.empty:
        lines += [
            "### TF별",
            "",
            "| TF | 자 | 건수 | 중앙값 | p90 | 5bp의 분위 |",
            "| -- | -- | --: | --: | --: | --: |",
        ]
        for _, row in by_tf.sort_values(["bucket", "metric"]).iterrows():
            name = "봉 변동폭" if row["metric"] == "bar_range_bp" else "손절가 아래 이탈"
            lines.append(
                f"| {row['bucket']} | {name} | {int(row['num_bars']):,} | "
                f"{_fmt(row['p50'], 1)} | {_fmt(row['p90'], 1)} | "
                f"{row['percentile_of_5bp'] * 100:.1f}% |"
            )
        lines.append("")

    # --- §2 ---------------------------------------------------------------- #
    lines += ["## §2 체결내역으로 걸어 본 체결 단가 (하한 추정)", ""]
    if ticks is None or ticks.empty:
        lines += [
            "**안 돌렸다** — `--part ticks`가 낸다(네트워크에서 일자별 아카이브를 받는다). "
            "이 절이 비어 있으면 §1의 「자릿수」와 §3의 「민감도」만 읽는다.",
            "",
        ]
    else:
        ok = ticks[~ticks["slippage_bp"].isna()]
        lines += [
            f"표본 **{len(ticks)}건**(TF 층화 무작위 · 시드 {TICK_SEED}) 중 판정 **{len(ok)}건**.",
            "",
        ]
        if tick_stats:
            cached = int(tick_stats.get("cached", 0.0))
            note = (
                f" — ⚠️ 그중 **{cached}개는 캐시 적중**이라 받는 시간에서 빠져 있다"
                if cached
                else ""
            )
            lines.append(
                f"비용 실측: 파일 {int(tick_stats['files'])}개 · "
                f"{tick_stats['bytes'] / 1e6:.0f}MB · 받는 데 {tick_stats['seconds']:.0f}초 · "
                f"실패 {int(tick_stats['failed'])}건{note}."
            )
            lines.append("")
        if not ok.empty:
            lines += [
                "| 자 | 중앙값 | p75 | p90 |",
                "| -- | --: | --: | --: |",
                f"| **주문 크기를 넣은 체결 단가** | **{ok['slippage_bp'].median():.2f}bp** | "
                f"{ok['slippage_bp'].quantile(0.75):.2f}bp | "
                f"{ok['slippage_bp'].quantile(0.90):.2f}bp |",
                f"| 첫 체결가만(크기 없음) | {ok['first_tick_bp'].median():.2f}bp | "
                f"{ok['first_tick_bp'].quantile(0.75):.2f}bp | "
                f"{ok['first_tick_bp'].quantile(0.90):.2f}bp |",
                f"| 주문 10배 | {ok['slippage_bp_x10'].median():.2f}bp | "
                f"{ok['slippage_bp_x10'].quantile(0.75):.2f}bp | "
                f"{ok['slippage_bp_x10'].quantile(0.90):.2f}bp |",
                f"| 주문 100배 | {ok['slippage_bp_x100'].median():.2f}bp | "
                f"{ok['slippage_bp_x100'].quantile(0.75):.2f}bp | "
                f"{ok['slippage_bp_x100'].quantile(0.90):.2f}bp |",
                "",
                f"📌 **주문 명목 중앙값 {ok['notional'].median():,.0f} USD** · 그 1분 체결량의 "
                f"**{ok['size_share_of_minute'].median() * 100:.2f}%**. 이 좌표는 복리를 껐으므로 "
                "명목이 자본을 따라 안 큰다 — **작은 주문의 측정**이고, 계좌가 커지면 「주문 "
                "10배·100배」 줄이 그 방향을 말한다"
                f"(100배에서 그 분의 체결량이 모자란 표본 "
                f"{int(ok['short_fill_x100'].sum())}건).",
                "",
                "🚨 **하한이다** — 인쇄된 체결량을 우리가 **전부** 먹을 수 있다고 본다(실제로는 "
                "다른 시장가 주문과 경쟁한다). 호가 깊이·큐 우선순위는 이 자료가 답하지 "
                "못한다(WAN-98 Canceled). 실제 슬리피지는 이 값보다 **크지 작지 않다**.",
                "",
                f"검산 (d) 틱 고·저가 ≡ 저장 1분봉: "
                f"{int(ticks['tick_high_matches_bar'].sum())}/{len(ticks)}건 일치.",
                "",
            ]

    # --- §3 ---------------------------------------------------------------- #
    lines += [
        "## §3 요율을 흔들면 채택 좌표가 어디로 가나 (실제 배치)",
        "",
        f"주 구간 `{PRIMARY_SEGMENT}` · 거래당 net R. **선형 예측**은 5bp 팔의 `slippage_r`을 "
        "요율에 비례해 늘린 값이고, 마지막 열이 **북 재배치가 그 외삽을 얼마나 틀리게 하는가**다.",
        "",
        "| 슬리피지 | 거래 | 승률 | **거래당 net R** | ± | gross R | 비용 R | 슬리피지 R | "
        "선형 예측 | 실측 − 예측 |",
        "| --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    prim = sensitivity[sensitivity["segment"] == PRIMARY_SEGMENT].sort_values("slippage_bp")
    for _, row in prim.iterrows():
        mark = " (현행)" if bool(row["adopted"]) else ""
        lines.append(
            f"| {row['slippage_bp']:.0f}bp{mark} | {int(row['num_trades']):,} | "
            f"{row['win_rate'] * 100:.2f}% | **{_fmt(row['mean_net_r'])}** | "
            f"{_fmt(row['net_r_stderr'])} | {_fmt(row['gross_r'])} | {_fmt(row['cost_r'])} | "
            f"{_fmt(row['slippage_r'])} | {_fmt(row['linear_mean_net_r'])} | "
            f"{_fmt(row['linear_gap'])} |"
        )
    lines.append("")
    base_row = prim[prim["adopted"].astype(bool)]
    if not base_row.empty and len(prim) > 1:
        base_net = float(base_row.iloc[0]["mean_net_r"])
        worst = prim.iloc[-1]
        lines.append(
            f"📌 **민감도 한 줄(완료기준 3)**: 슬리피지를 "
            f"{base_row.iloc[0]['slippage_bp']:.0f} → {worst['slippage_bp']:.0f}bp로 올리면 "
            f"`{PRIMARY_SEGMENT}` 거래당 net R이 **{base_net:+.4f} → "
            f"{float(worst['mean_net_r']):+.4f}R**이 된다."
        )
        lines.append("")
    lines += [
        "### 구간별",
        "",
        "| 구간 | "
        + " | ".join(f"{s:.0f}bp" for s in sorted(sensitivity["slippage_bp"].unique()))
        + " |",
        "| -- | " + " | ".join("--:" for _ in sorted(sensitivity["slippage_bp"].unique())) + " |",
    ]
    for segment in SEGMENT_ORDER:
        grp = sensitivity[sensitivity["segment"] == segment].sort_values("slippage_bp")
        if grp.empty:
            continue
        cells = " | ".join(_fmt(v) for v in grp["mean_net_r"])
        lines.append(f"| {segment} | {cells} |")
    lines.append("")

    # --- 검산 · 경고 -------------------------------------------------------- #
    lines += [
        "## 검산",
        "",
        "| 검산 | 자 | 왼쪽 | 오른쪽 | 절대차 |",
        "| -- | -- | --: | --: | --: |",
    ]
    for check in checks:
        lines.append(
            f"| {check.check} | {check.metric} | {_fmt(check.left, 8)} | "
            f"{_fmt(check.right, 8)} | {check.abs_diff:.2e} |"
        )
    lines += [
        "",
        "## 읽는 법 · 경고",
        "",
        "* 🚨 **「그러니 요율을 바꾸자」가 이 표의 결론이 아니다** — 전환은 **재-베이스라인 = "
        "사용자 결정**(WAN-92)이고 개발자 임의 착수 금지다. 이 이슈는 **숫자만** 낸다.",
        "* ⚠️ **§1은 슬리피지를 재지 않는다** — 봉 변동폭·손절가 아래 이탈은 슬리피지의 "
        "**상한 쪽 자릿수**이고, 「얼마인가」는 §2의 하한 추정과 함께 읽어야 폭이 된다.",
        "* ⚠️ **전부 `baseline`(닿으면 체결) 렌즈 위 값이다** — 체결 보수화(`pen_5bp`)는 "
        "*「주문이 채워지느냐」*를 묻는 **다른 축**이고 이 표에 없다.",
        "* ⚠️ **총수익 %·MDD는 이 좌표에서 읽지 않는다**(포화·정의 상실 — WAN-378/386/395). "
        "복리를 껐고 판정 자는 거래당 net R 하나다.",
        "* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *이미 잰 "
        "숫자가 얼마나 낙관인가*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 않는다. "
        "**다른 질문이다.**",
        "* ⚠️ **손절을 지정가로 걸어 슬리피지를 없애자는 제안이 아니다** — 별개 축이고 "
        "WAN-276이 낸 「1분봉 해상도에선 지정가 손절도 다 채워진다」는 그 문서 스스로 "
        "**「해상도 한계이지 실거래 보증이 아니다」**라고 못 박았다.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _load_checks() -> list[ChecksumRow]:
    """적재된 검산 CSV를 모델로 되읽는다(요약이 dict를 다루지 않게)."""
    if not CHECKSUM_CSV.exists():
        return []
    return [ChecksumRow(**row) for row in pd.read_csv(CHECKSUM_CSV).to_dict("records")]


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def run_measure(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    grid: Sequence[float] = SLIPPAGE_GRID,
    cache: PayloadCache | None = None,
    db_path: str = harness.DB_PATH,
    log: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[ChecksumRow]]:
    """§1 + §3을 한 번에 — 후보는 한 번 만들고 슬리피지마다 배치만 다시 한다."""
    started = time.monotonic()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = build_payloads(symbols, timeframes, start=start, end=end, jobs=jobs, cache=cache)
    if log:
        print(f"[wan397] 후보 {len(payloads)}칸 · {time.monotonic() - started:.0f}s", flush=True)

    rows, exits_by_segment = sensitivity_rows(
        payloads, start_ms=start_ms, end_ms=end_ms, segments=segments, grid=grid, log=log
    )
    sensitivity = pd.DataFrame([r.model_dump() for r in rows])

    flat = [e for segment in segments for e in exits_by_segment.get(segment, [])]
    detail = attach_bars(flat, db_path=db_path)
    unconditional = unconditional_ranges(symbols, segment_windows(detail), db_path=db_path)
    census = pd.DataFrame([r.model_dump() for r in census_rows(detail, unconditional)])
    if log:
        print(
            f"[wan397] 테이커 청산 {len(detail)}건 · 총 {time.monotonic() - started:.0f}s",
            flush=True,
        )
    return (
        detail,
        census,
        sensitivity,
        checksums(
            rows,
            adopted_coordinates=on_adopted_coordinates(symbols, timeframes, start, end),
        ),
    )


def run_ticks(
    detail: pd.DataFrame,
    *,
    segment: str = PRIMARY_SEGMENT,
    size: int = TICK_SAMPLE_SIZE,
    seed: int = TICK_SEED,
    cache_dir: Path = agg_trade_archive.DEFAULT_CACHE_DIR,
    log: bool = True,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """§2 — 주 구간의 손절 체결에서 표본을 뽑아 체결내역으로 체결 단가를 낸다."""
    pool = detail[(detail["segment"] == segment) & detail["bar_high"].notna()].reset_index(
        drop=True
    )
    sample = sample_exits(pool, size=size, seed=seed)
    if log:
        print(f"[wan397] §2 표본 {len(sample)}건 / 모집단 {len(pool)}건", flush=True)
    rows, stats = tick_rows(sample, cache_dir=cache_dir, log=log)
    return pd.DataFrame([r.model_dump() for r in rows]), stats


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-397 슬리피지 실측")
    parser.add_argument(
        "--part",
        choices=("measure", "ticks", "summary"),
        default="measure",
        help="measure = §1+§3(기본) · ticks = §2(네트워크) · summary = 적재 CSV로 요약만",
    )
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--sample", type=int, default=TICK_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=TICK_SEED)
    parser.add_argument("--no-cache", action="store_true", help="payload 디스크 캐시를 쓰지 않는다")
    parser.add_argument("--from-csv", action="store_true", help="`--part summary`의 별칭")
    args = parser.parse_args(argv)

    part = "summary" if args.from_csv else args.part
    if part == "measure":
        detail, census, sensitivity, checks = run_measure(
            [s.strip() for s in args.symbols.split(",") if s.strip()],
            [t.strip() for t in args.timeframes.split(",") if t.strip()],
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            cache=None if args.no_cache else PayloadCache(),
        )
        _write(detail, DETAIL_CSV)
        _write(census, CENSUS_CSV)
        _write(sensitivity, SENSITIVITY_CSV)
        _write(pd.DataFrame([c.model_dump() for c in checks]), CHECKSUM_CSV)
    elif part == "ticks":
        if not DETAIL_CSV.exists():
            parser.error(f"{DETAIL_CSV}가 없습니다 — `--part measure`를 먼저 돌리세요.")
        detail = pd.read_csv(DETAIL_CSV)
        ticks, stats = run_ticks(detail, size=args.sample, seed=args.seed)
        _write(ticks, TICK_CSV)
        print(
            f"[wan397] §2 파일 {int(stats['files'])}개 · {stats['bytes'] / 1e6:.0f}MB · "
            f"{stats['seconds']:.0f}초 · 실패 {int(stats['failed'])}건",
            flush=True,
        )
        census = pd.read_csv(CENSUS_CSV)
        sensitivity = pd.read_csv(SENSITIVITY_CSV)
        checks = _load_checks()
        SUMMARY_PATH.write_text(
            render_summary(census, sensitivity, checks, ticks, tick_stats=stats), encoding="utf-8"
        )
        print(SUMMARY_PATH)
        return 0
    else:
        if not CENSUS_CSV.exists():
            parser.error(f"{CENSUS_CSV}가 없습니다 — `--part measure`를 먼저 돌리세요.")
        census = pd.read_csv(CENSUS_CSV)
        sensitivity = pd.read_csv(SENSITIVITY_CSV)
        checks = _load_checks()

    ticks = pd.read_csv(TICK_CSV) if TICK_CSV.exists() else None
    SUMMARY_PATH.write_text(render_summary(census, sensitivity, checks, ticks), encoding="utf-8")
    print(SUMMARY_PATH)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
