"""WAN-370: 수수료가 모델을 얼마나 잡아먹는가 — 비용 분해(§1) + 익절 메이커 전환(§2).

## 한 줄

인과 엔진의 채택 북은 거래당 net R이 **−0.1798R**인데(WAN-366 `L4`), 그 숫자는 **이미
수수료를 뺀 뒤**라 「시장에서 졌다」와 「시장에선 비겼는데 수수료가 다 먹었다」가 한 덩어리로
뭉쳐 있다. 이 모듈은 그 한 덩어리를 **gross / 진입 수수료 / 익절 수수료 / 손절 수수료 /
슬리피지 / 펀딩**으로 쪼갠다.

## 왜 새 격자가 아니라 분해인가

도구는 이미 있었다 — `common.costs.CostBreakdown`(WAN-37)이 거래를 `gross / slippage / fee /
net`으로 쪼개는 산식을 들고 있고, 이 모듈은 그 산식을 **북 거래 하나하나**에 적용해 R 단위로
평균낼 뿐이다. 새로 잰 것은 없고 **이미 잰 것을 갈라 본다**.

🚨 **비용을 0으로 만들어도 gross R이 천장이다.** 그 값이 0 근처면 어떤 비용 처방도 0 근처를
못 벗어난다 — §1-3의 갈림((가) 시장에서 졌다 / 0 근처 / (나) 비용이 먹었다)이 이 표의 산출물이고,
처방은 그 뒤에 정한다.

## 두 팔 — 같은 후보, 다른 비용 회계

| 팔 | 익절 청산 | 손절·만료 | 진입 |
| -- | -- | -- | -- |
| `taker_tp` (전) | 테이커 4bp ＋ 슬리피지 5bp | 테이커 (동일) | 메이커 2bp |
| `maker_tp` (후, **채택**) | **메이커 2bp · 슬리피지 0** | 테이커 (동일) | 메이커 2bp |

📌 **비용은 후보 집합을 바꾸지 않는다**(`build_zone_limit_candidates`는 수수료를 읽지 않는다) —
그래서 **후보를 한 번만 만들고**(북 한 팔의 비용 거의 전부) 배치만 두 번 돈다. 두 팔의
`num_candidates`가 같은 것이 검산 (d)다.

⚠️ **그렇다고 거래가 같지는 않다** — 공유 자본 지갑이라 손익이 바뀌면 뒤쪽 사이징·명목 상한이
움직여 배치가 갈릴 수 있다(WAN-312가 관찰한 성질). 그래서 거래 수도 열로 낸다.

## 축 셋 (`axis` 열)

* `overall` — 구간 전체(`full`·`is`·`oos_warm`·`oos`, **주 수치는 `oos_warm`**).
* `timeframe` — 15m·1h·2h·4h. 🚨 **이 축이 중요하다**: 15분봉은 손절폭이 좁아(WAN-328 실측
  중앙값 0.458%) **같은 bp가 R로는 더 크게** 잡아먹는다.
* `stop_width` — 손절폭 구간별 버킷. 가드 하한(0.3%) 바로 위 거래의 비용 비중을 따로 본다
  (WAN-154 §3이 옛 엔진에서 *「가장 좁은 손절 구간은 생존율이 최고인데 돈은 잃는다」*를 낸 자리).

## 자 — 거래당 R (총수익 %가 아니다)

총수익 %는 6년 복리라 판정 자가 아니다(WAN-169/213 — 이 좌표에서는 아예 −100%로 포화한다).
모든 비용 성분은 **그 거래의 리스크 금액**으로 나눠(= `book_cli.net_r`과 같은 분모) 평균낸다.
그래서 다음 항등식이 **거래 단위로** 성립하고, 그것이 검산 (c)다:

```
gross_r − slippage_r − entry_fee_r − take_profit_fee_r − stop_fee_r − other_fee_r − funding_r
    == net_r
```

⚠️ ±0.005R 안은 「0과 구분되지 않는다」로 읽는다(WAN-366 규약).

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 4TF(15m·1h·2h·4h) 한 지갑 · 못 박은 6년 창 · cap_only 5배 ·
재진입 ON(band) · 유동성 한도 채택값 · `baseline` 렌즈 · **인과 취소**(WAN-365).

## 검산

* **(a) `taker_tp` 팔 ≡ WAN-366 `L4`** — 적재된 `wan366_causal_ablation.csv`의 그 행과 대조한다.
  이 등식이 서면 「전」 팔이 실제로 **WAN-370 이전의 채택 북**이다(라벨이 아니라 숫자로).
* **(b) `maker_tp` 팔 ≡ 채택 북 회계** — `book_cli.build_book_rows`에
  `ADOPTED_TAKE_PROFIT_LIQUIDITY`를 **명시로** 넘긴 행과 대조한다. ⚠️ 「인자 없는」이
  아니다 — `build_book_rows`의 기본값은 옛 회계(중앙 핀, `taker`)이고, 채택 북
  (`run_book` = `backtest.run --oos-warm`)이 그 값을 **항상 명시**하므로 이 호출이 곧
  그 회계다(§2-2가 경고한 함정 그대로 — 서술이 코드와 갈라지면 다음 사람이
  「인자 없이 부르면 채택」이라고 읽는다).
* **(c) 분해 항등식** — 거래마다 위 식의 최대 절대차. 0이 아니면 분해가 손익과 갈라진 것이다.
* **(d) 두 팔이 같은 후보를 본다** — `num_candidates`가 같아야 한다. 다르면 비용이 후보 생성에
  샌 것이다.

재현:

```
uv run python -m backtest.wan370_cost_decomposition --jobs 4
uv run python -m backtest.wan370_cost_decomposition --from-csv    # 요약만
```
"""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import BacktestConfig, ExitReason, Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, _segment_cells, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from common.costs import Liquidity
from data.models import timeframe_to_ms

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan370_cost_decomposition.csv"
SUMMARY_PATH = REPORTS_DIR / "wan370_cost_decomposition_summary.md"
LADDER_CSV_PATH = REPORTS_DIR / "wan366_causal_ablation.csv"

#: 「0과 구분되지 않는다」 선 — WAN-366 규약 그대로.
NOISE_R = 0.005

#: 손절폭 버킷 경계(진입가 대비 분수). 하한이 가드(0.3%)라 그 아래는 정의상 비어 있고,
#: 그래도 버킷을 남긴다 — 비어 있다는 사실 자체가 가드가 실제로 걸렸다는 증거다.
STOP_WIDTH_EDGES: tuple[float, ...] = (0.0, 0.003, 0.004, 0.005, 0.0075, 0.010, 0.015, math.inf)


@dataclass(frozen=True)
class Arm:
    """비용 회계 한 팔. 후보는 공유하고 배치만 다르다."""

    name: str
    take_profit_liquidity: Liquidity
    label: str

    @property
    def is_adopted(self) -> bool:
        return self.take_profit_liquidity is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


ARMS: tuple[Arm, ...] = (
    Arm("taker_tp", harness.LEGACY_TAKE_PROFIT_LIQUIDITY, "전 — 익절도 테이커(4bp＋5bp)"),
    Arm("maker_tp", harness.ADOPTED_TAKE_PROFIT_LIQUIDITY, "후 — 익절 지정가(메이커 2bp)"),
)
ARM_ORDER: tuple[str, ...] = tuple(a.name for a in ARMS)

AXIS_OVERALL = "overall"
AXIS_TIMEFRAME = "timeframe"
AXIS_STOP_WIDTH = "stop_width"


# --------------------------------------------------------------------------- #
# 거래 하나의 비용 분해
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TradeCosts:
    """한 거래의 비용 성분(전부 **USD**, 비용은 ≥0). 합이 손익과 닫히는 것이 요점이다."""

    gross: float
    """수수료·슬리피지 전 가격 손익(방향 반영) — 「시장에서 얻은 것」."""
    slippage: float
    entry_fee: float
    take_profit_fee: float
    """익절(부분 익절 포함) 청산 수수료."""
    stop_fee: float
    other_fee: float
    """만료·데이터 종료 등 나머지 청산 수수료."""
    funding: float
    net: float
    """`trade.realized_pnl` 그대로 — 아래 `residual`이 0이면 위 성분의 합과 같다."""

    @property
    def total_cost(self) -> float:
        return (
            self.slippage
            + self.entry_fee
            + self.take_profit_fee
            + self.stop_fee
            + self.other_fee
            + self.funding
        )

    @property
    def residual(self) -> float:
        """검산 (c) — 분해가 실현손익과 닫히는가."""
        return self.gross - self.total_cost - self.net


def _reference_price(fill_price: float, *, is_long: bool, slip: float, entry: bool) -> float:
    """체결가에서 **참조가**(슬리피지 미반영)를 되돌린다.

    `CostModel.entry_fill`/`exit_fill`의 정확한 역산이다 — 슬리피지가 곱셈이라 나눗셈 한 번으로
    되돌아온다. 슬리피지가 0(메이커)이면 체결가가 곧 참조가다.
    """
    if slip == 0.0:
        return fill_price
    if entry:
        return fill_price / (1.0 + slip) if is_long else fill_price / (1.0 - slip)
    return fill_price / (1.0 - slip) if is_long else fill_price / (1.0 + slip)


def decompose_trade(trade: Trade, cfg: BacktestConfig) -> TradeCosts:
    """거래 하나를 비용 성분으로 쪼갠다 — 산식은 `CostModel.trade_costs`와 같은 것이다.

    🚨 **어느 청산이 메이커였는지를 이 함수가 다시 정하지 않는다** — `cfg.exit_liquidity`
    (WAN-370의 단일 소스)에 물어본다. 여기서 사유별 분기를 복제하면 엔진과 리포트가 서로 다른
    비용을 말하게 된다(WAN-77의 사본이 실제로 그렇게 갈라졌다).

    🚨 **진입 쪽도 같은 규칙이다 — `trade.entry_liquidity`(엔진이 실제로 쓴 값)를 본다**
    (WAN-396). 예전에는 `cfg.entry_liquidity`를 읽었는데 그 기본값이 **테이커**인 반면 B안
    엔진은 후보의 값(기본 **메이커**)을 쓴다. 그래서 분해가 붙지도 않은 진입 슬리피지 5bp를
    계상했고, `entry_ref`가 그만큼 밀려 **`gross`와 `slippage`가 똑같이 부풀었다** — 두 항이
    상쇄되므로 `net`으로는 안 보인다(판정 (가): 손익은 맞고 진단만 틀렸다).
    """
    costs = cfg.cost_model
    is_long = trade.side.sign > 0
    sign = trade.side.sign
    entry_slip = costs.slippage_for(trade.entry_liquidity)
    entry_ref = _reference_price(trade.entry_price, is_long=is_long, slip=entry_slip, entry=True)

    gross = 0.0
    slippage = abs(trade.entry_price - entry_ref) * trade.quantity
    take_profit_fee = 0.0
    stop_fee = 0.0
    other_fee = 0.0
    for fill in trade.exits:
        liquidity = cfg.exit_liquidity(fill.reason)
        slip = costs.slippage_for(liquidity)
        exit_ref = _reference_price(fill.price, is_long=is_long, slip=slip, entry=False)
        gross += sign * (exit_ref - entry_ref) * fill.quantity
        slippage += abs(exit_ref - fill.price) * fill.quantity
        if fill.reason in (ExitReason.TAKE_PROFIT, ExitReason.PARTIAL_TAKE_PROFIT):
            take_profit_fee += fill.fee
        elif fill.reason is ExitReason.STOP_LOSS:
            stop_fee += fill.fee
        else:
            other_fee += fill.fee
    return TradeCosts(
        gross=gross,
        slippage=slippage,
        entry_fee=trade.entry_fee,
        take_profit_fee=take_profit_fee,
        stop_fee=stop_fee,
        other_fee=other_fee,
        funding=trade.funding_cost,
        net=trade.realized_pnl,
    )


def stop_width_fraction(trade: Trade, placement: PlacedSetup) -> float:
    """진입 체결가 대비 손절 거리(분수) — 손절폭 버킷의 자(WAN-328과 같은 정의)."""
    if trade.entry_price <= 0.0:
        return 0.0
    return abs(trade.entry_price - placement.stop_price) / trade.entry_price


def stop_width_bucket(fraction: float) -> str:
    """손절폭 버킷 라벨. 경계는 `STOP_WIDTH_EDGES`(퍼센트 표기)."""
    for low, high in zip(STOP_WIDTH_EDGES, STOP_WIDTH_EDGES[1:], strict=False):
        if low <= fraction < high:
            if math.isinf(high):
                return f"≥{low * 100:.2f}%"
            return f"{low * 100:.2f}~{high * 100:.2f}%"
    return "?"


# --------------------------------------------------------------------------- #
# 행 모델 · 집계
# --------------------------------------------------------------------------- #


class CostRow(BaseModel):
    """한 (팔, 구간, 축, 버킷)의 거래당 R 분해. 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    axis: str
    bucket: str
    num_trades: int
    num_candidates: int
    win_rate: float
    median_stop_width: float
    gross_r: float
    slippage_r: float
    entry_fee_r: float
    take_profit_fee_r: float
    stop_fee_r: float
    other_fee_r: float
    funding_r: float
    cost_r: float
    net_r: float
    cost_share_of_net: float | None
    """비용이 net R의 몇 %를 설명하는가 = `cost_r / |net_r|`. net R이 0 근처면 `None`
    (그 셀에서는 비율이 뜻을 잃는다 — WAN-115가 문서화한 부호·0 함정)."""
    identity_max_abs: float
    """검산 (c) — 이 버킷 거래들의 분해 잔차 최대 절대값(R)."""


def _pairs(segment: BookSegment) -> list[tuple[Trade, PlacedSetup]]:
    return segment.trades_with_placements()


def _aggregate(
    pairs: Sequence[tuple[Trade, PlacedSetup]],
    cfg: BacktestConfig,
    *,
    arm: str,
    segment: str,
    axis: str,
    bucket: str,
    num_candidates: int,
) -> CostRow:
    """거래 목록을 거래당 R 분해 한 행으로 접는다."""
    n = len(pairs)
    if n == 0:
        return CostRow(
            arm=arm,
            segment=segment,
            axis=axis,
            bucket=bucket,
            num_trades=0,
            num_candidates=num_candidates,
            win_rate=0.0,
            median_stop_width=0.0,
            gross_r=0.0,
            slippage_r=0.0,
            entry_fee_r=0.0,
            take_profit_fee_r=0.0,
            stop_fee_r=0.0,
            other_fee_r=0.0,
            funding_r=0.0,
            cost_r=0.0,
            net_r=0.0,
            cost_share_of_net=None,
            identity_max_abs=0.0,
        )
    totals = dict.fromkeys(
        ("gross", "slippage", "entry", "tp", "stop", "other", "funding", "net"), 0.0
    )
    worst = 0.0
    wins = 0
    widths: list[float] = []
    for trade, placement in pairs:
        risk = placement.risk_amount
        if risk <= 0.0:
            continue
        parts = decompose_trade(trade, cfg)
        totals["gross"] += parts.gross / risk
        totals["slippage"] += parts.slippage / risk
        totals["entry"] += parts.entry_fee / risk
        totals["tp"] += parts.take_profit_fee / risk
        totals["stop"] += parts.stop_fee / risk
        totals["other"] += parts.other_fee / risk
        totals["funding"] += parts.funding / risk
        totals["net"] += net_r(trade, placement)
        worst = max(worst, abs(parts.residual) / risk)
        wins += 1 if trade.realized_pnl > 0 else 0
        widths.append(stop_width_fraction(trade, placement))
    cost_r = (
        totals["slippage"] + totals["entry"] + totals["tp"] + totals["stop"] + totals["other"]
    ) / n + totals["funding"] / n
    net = totals["net"] / n
    share = cost_r / abs(net) if abs(net) > NOISE_R else None
    return CostRow(
        arm=arm,
        segment=segment,
        axis=axis,
        bucket=bucket,
        num_trades=n,
        num_candidates=num_candidates,
        win_rate=wins / n,
        median_stop_width=float(pd.Series(widths).median()) if widths else 0.0,
        gross_r=totals["gross"] / n,
        slippage_r=totals["slippage"] / n,
        entry_fee_r=totals["entry"] / n,
        take_profit_fee_r=totals["tp"] / n,
        stop_fee_r=totals["stop"] / n,
        other_fee_r=totals["other"] / n,
        funding_r=totals["funding"] / n,
        cost_r=cost_r,
        net_r=net,
        cost_share_of_net=share,
        identity_max_abs=worst,
    )


def rows_for_segment(
    segment: BookSegment, cfg: BacktestConfig, *, arm: str, num_candidates: int
) -> list[CostRow]:
    """한 구간의 세 축(전체·TF·손절폭) 행을 낸다."""
    pairs = _pairs(segment)
    rows = [
        _aggregate(
            pairs,
            cfg,
            arm=arm,
            segment=segment.segment,
            axis=AXIS_OVERALL,
            bucket="전체",
            num_candidates=num_candidates,
        )
    ]
    by_tf: dict[str, list[tuple[Trade, PlacedSetup]]] = {}
    by_width: dict[str, list[tuple[Trade, PlacedSetup]]] = {}
    for trade, placement in pairs:
        by_tf.setdefault(placement.cell[1], []).append((trade, placement))
        bucket = stop_width_bucket(stop_width_fraction(trade, placement))
        by_width.setdefault(bucket, []).append((trade, placement))
    for timeframe, group in sorted(by_tf.items(), key=lambda kv: timeframe_to_ms(kv[0])):
        rows.append(
            _aggregate(
                group,
                cfg,
                arm=arm,
                segment=segment.segment,
                axis=AXIS_TIMEFRAME,
                bucket=timeframe,
                num_candidates=num_candidates,
            )
        )
    for bucket in _ordered_width_buckets():
        group = by_width.get(bucket, [])
        if not group:
            continue
        rows.append(
            _aggregate(
                group,
                cfg,
                arm=arm,
                segment=segment.segment,
                axis=AXIS_STOP_WIDTH,
                bucket=bucket,
                num_candidates=num_candidates,
            )
        )
    return rows


def _ordered_width_buckets() -> list[str]:
    return [
        stop_width_bucket((low + (high if not math.isinf(high) else low + 0.01)) / 2)
        for low, high in zip(STOP_WIDTH_EDGES, STOP_WIDTH_EDGES[1:], strict=False)
    ]


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
    """채택 북의 칸 후보를 **한 번** 만든다(두 팔이 공유 — 비용은 후보를 안 바꾼다).

    ⚠️ 후보 생성 cfg에는 **채택 값**을 넘긴다 — 이 payload의 per-cell 격리 행은 이 표에 쓰지
    않지만, 「이 실행은 채택 좌표」라는 사실이 인자에 드러나야 한다(WAN-305).
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        # WAN-384 명시 핀: 이 표는 존폭 필터를 켠 채(1.28) 낸 기록이다.
        max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )


def segment_candidate_count(payloads: Sequence[CellPayload], segment: str) -> int:
    """이 구간에서 북에 들어간 후보 수 — 검산 (d)의 자.

    비용은 후보 집합을 안 바꾸므로 두 팔이 **같은 수**를 봐야 한다. 배치 결과(거래 수)는
    갈릴 수 있다 — 공유 자본 지갑이라 손익이 사이징·명목 상한을 통해 뒤쪽 배치를 움직인다.
    """
    return sum(len(cell.candidates) for cell in _segment_cells(payloads, segment, ""))


def place_arm(
    payloads: Sequence[CellPayload],
    arm: Arm,
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str] = SEGMENT_ORDER,
) -> list[BookSegment]:
    """한 팔의 배치 — 채택 북과 **같은 함수·같은 인자**이고 비용 축만 다르다."""
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        take_profit_liquidity=arm.take_profit_liquidity,
    )


def ladder_l4_rows() -> dict[str, dict[str, float]]:
    """검산 (a)의 기준 — 적재된 WAN-366 사다리의 `L4`(= WAN-370 이전 채택 북) 행."""
    if not LADDER_CSV_PATH.exists():
        return {}
    frame = pd.read_csv(LADDER_CSV_PATH)
    l4 = frame[frame["level"] == "L4"]
    return {
        str(r["segment"]): {
            "num_trades": float(r["num_trades"]),
            "mean_net_r": float(r["mean_net_r"]),
            "max_drawdown": float(r["max_drawdown"]),
        }
        for _, r in l4.iterrows()
    }


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
    """두 팔의 비용 분해 격자 + 검산값을 낸다."""
    started = time.monotonic()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = build_payloads(symbols, timeframes, start=start, end=end, jobs=jobs)
    if log:
        print(f"[wan370] 후보 생성 {time.monotonic() - started:.0f}s", flush=True)

    rows: list[CostRow] = []
    checks: dict[str, float] = {}
    legacy_book: list[BookSegment] = []
    candidate_counts: dict[str, int] = {}
    for arm in ARMS:
        cfg = harness.build_config(
            harness.DEFAULT_TIMEFRAMES[0], take_profit_liquidity=arm.take_profit_liquidity
        )
        book = place_arm(payloads, arm, start_ms=start_ms, end_ms=end_ms, segments=segments)
        for seg in book:
            candidates = segment_candidate_count(payloads, seg.segment)
            candidate_counts.setdefault(f"{arm.name}:{seg.segment}", candidates)
            rows += rows_for_segment(seg, cfg, arm=arm.name, num_candidates=candidates)
        if log:
            print(f"[wan370] {arm.name}: {len(book)}구간 배치", flush=True)
        if arm.is_adopted:
            checks["adopted_identity"] = _verify_adopted(payloads, book, start_ms, end_ms)
        else:
            legacy_book = list(book)
    frame = pd.DataFrame([r.model_dump() for r in rows])
    # 검산 (a)는 **채택 좌표에서만** 뜻이 있다 — 좁혀 돌린 스모크 실행에서 사다리 CSV와
    # 대조하면 「다른 격자의 숫자」를 불일치로 찍는다(조용히 통과시키지도, 헛경보를 내지도
    # 않게 좌표를 먼저 본다).
    if _is_adopted_coordinate(symbols, timeframes, start, end):
        checks["ladder_l4"] = _verify_ladder(legacy_book, frame)
    checks["identity_max_abs"] = float(frame["identity_max_abs"].max())
    checks["candidate_gap"] = float(
        max(
            abs(candidate_counts[f"{ARM_ORDER[0]}:{s}"] - candidate_counts[f"{ARM_ORDER[1]}:{s}"])
            for s in segments
        )
    )
    if log:
        print(f"[wan370] 총 {time.monotonic() - started:.0f}s · 검산 {checks}", flush=True)
    return frame, checks


def _verify_adopted(
    payloads: Sequence[CellPayload],
    book: Sequence[BookSegment],
    start_ms: int,
    end_ms: int,
) -> float:
    """검산 (b) — 채택 팔 ≡ `build_book_rows`에 채택 값을 **명시로** 넘긴 행.

    `build_book_rows`의 기본값은 옛 회계(중앙 핀)라 「기본 인자」로는 채택 북이 나오지
    않는다 — 채택 북(`run_book` = `backtest.run --oos-warm`)이 명시하는 것과 같은 값을
    여기서도 명시해, 이 대조가 곧 「그 회계와의 등식」이 된다.
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


def _is_adopted_coordinate(
    symbols: Sequence[str], timeframes: Sequence[str], start: str, end: str
) -> bool:
    """이 실행이 채택 좌표(12종목 × 4TF × 못 박은 6년 창)인가."""
    return (
        [harness.normalize_symbol(s) for s in symbols]
        == [harness.normalize_symbol(s) for s in harness.DEFAULT_SYMBOLS]
        and list(timeframes) == list(harness.DEFAULT_TIMEFRAMES)
        and start == harness.DEFAULT_START
        and end == harness.DEFAULT_END
    )


def _verify_ladder(book: Sequence[BookSegment], frame: pd.DataFrame) -> float:
    """검산 (a) — 옛 회계 팔 ≡ 적재된 WAN-366 `L4` 행. CSV가 없으면 `-1`(검산 못 함).

    거래 수와 **거래당 net R** 둘 다 본다 — MDD는 이 좌표에서 1.0으로 포화해 자가 되지 못한다.
    """
    reference = ladder_l4_rows()
    if not reference:
        return -1.0
    overall = frame[(frame.axis == AXIS_OVERALL) & (frame.arm == ARM_ORDER[0])].set_index("segment")
    worst = 0.0
    for seg in book:
        ref = reference.get(seg.segment)
        if ref is None or seg.segment not in overall.index:
            continue
        worst = max(
            worst,
            float(abs(seg.row.num_trades - ref["num_trades"])),
            abs(float(overall.loc[seg.segment, "net_r"]) - ref["mean_net_r"]),
        )
    return worst


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def verdict(gross_r: float) -> str:
    """§1-3의 갈림 — 「시장에서 얻은 것」의 부호로 다음 행동이 갈린다."""
    if gross_r > NOISE_R:
        return "(나) 시장에선 벌었는데 비용이 먹었다 — 비용·손절폭·체결 방식이 손댈 자리다"
    if gross_r < -NOISE_R:
        return "(가) 시장에서 졌다 — 비용은 논점이 아니고 진입 규칙 자체가 문제다"
    return "(0 근처) 비용을 0으로 만들어도 0 근처다 — 「이 규칙 집합에는 없다」가 한 겹 단단해진다"


def _fmt_r(value: float) -> str:
    return f"{value:+.4f}R"


def render_summary(frame: pd.DataFrame, checks: dict[str, float] | None = None) -> str:
    """요약 md — 주 수치는 `oos_warm`."""
    checks = checks or {}
    lines: list[str] = ["# WAN-370 — 비용 분해 + 익절 메이커 전환", ""]
    lines.append(
        "자는 **거래당 net R**이다(총수익 %는 6년 복리라 판정 자가 아니다 — 이 좌표에서는 "
        "아예 −100%로 포화한다, WAN-169/213). ±0.005R 안은 「0과 구분되지 않는다」로 읽는다."
    )
    lines.append("")

    primary = frame[(frame.axis == AXIS_OVERALL) & (frame.segment == PRIMARY_OOS)]
    if not primary.empty:
        head = primary[primary.arm == ARM_ORDER[1]].iloc[0]
        lines += [
            f"## 판정 — {verdict(float(head['gross_r']))}",
            "",
            f"`{PRIMARY_OOS}` 채택 팔(`maker_tp`) 기준: 시장에서 얻은 것 "
            f"**{_fmt_r(float(head['gross_r']))}** · 총비용 **{float(head['cost_r']):.4f}R** → "
            f"최종 **{_fmt_r(float(head['net_r']))}**"
            + (
                f" (비용이 net R의 **{float(head['cost_share_of_net']) * 100:.0f}%**를 설명)"
                if head["cost_share_of_net"] == head["cost_share_of_net"]
                else ""
            ),
            "",
            "🚨 **비용을 0으로 만들어도 「시장에서 얻은 것」이 천장이다.**",
            "",
        ]

    lines += ["## §1 구간별 (거래당 R)", "", _axis_table(frame, AXIS_OVERALL), ""]
    lines += [
        f"## §1 TF별 — `{PRIMARY_OOS}` (거래당 R)",
        "",
        _axis_table(frame[frame.segment == PRIMARY_OOS], AXIS_TIMEFRAME),
        "",
        "🚨 15분봉은 손절폭이 좁아 **같은 bp가 R로는 더 크게** 잡아먹는다(WAN-328).",
        "",
    ]
    lines += [
        f"## §1 손절폭 버킷별 — `{PRIMARY_OOS}` (거래당 R)",
        "",
        _axis_table(frame[frame.segment == PRIMARY_OOS], AXIS_STOP_WIDTH),
        "",
        "⚠️ 손절폭 가드(0.3%) 아래 버킷은 **정의상 비어 있다** — 가드가 그 부류를 쳐낸다"
        "(WAN-76/79 · 이 표는 가드를 건드리지 않는다).",
        "",
    ]

    delta = _delta_table(frame)
    if delta:
        lines += ["## §2 전·후 대조 (익절 수수료 줄이 얼마나 줄었나)", "", delta, ""]

    if checks:
        lines += [
            "## 검산",
            "",
            f"* (a) 옛 회계 팔 ≡ WAN-366 `L4` 최대차: `{checks.get('ladder_l4', float('nan')):.2e}`"
            + (" — ⚠️ 사다리 CSV 없음" if checks.get("ladder_l4", 0.0) < 0 else ""),
            "* (b) 채택 팔 ≡ 채택 북 회계(`build_book_rows`에 채택 값 명시 = "
            f"`run_book`이 도는 회계) 최대차: `{checks.get('adopted_identity', float('nan')):.2e}`",
            f"* (c) 분해 항등식 최대 절대차: `{checks.get('identity_max_abs', float('nan')):.2e}`R",
            f"* (d) 두 팔의 후보 수 차: `{checks.get('candidate_gap', float('nan')):.0f}`",
            "",
        ]
    lines += [
        "---",
        "",
        "⚠️ **범위·경고** — 전부 `baseline`(닿으면 체결) 위 값이고 **익절 체결 판정은 안 건드렸다**"
        "(수수료는 지정가인데 체결은 낙관 · 사용자 결정 ①). 「엣지 없음」(WAN-84/88/111/114/124/"
        "151/201/248) 불변 · 6년 MDD는 폭락 미포함 **바닥선** · 판단은 북에서만 낸다(WAN-341).",
    ]
    return "\n".join(lines)


_R_COLUMNS: tuple[tuple[str, str], ...] = (
    ("gross_r", "시장에서 얻은 것"),
    ("slippage_r", "− 슬리피지"),
    ("entry_fee_r", "− 진입 수수료"),
    ("take_profit_fee_r", "− 익절 수수료"),
    ("stop_fee_r", "− 손절 수수료"),
    ("other_fee_r", "− 기타 청산"),
    ("funding_r", "− 펀딩"),
    ("net_r", "= 최종 net R"),
)


def _axis_table(frame: pd.DataFrame, axis: str) -> str:
    subset = frame[frame.axis == axis]
    if subset.empty:
        return "_(행 없음)_"
    header = ["팔", "구간" if axis == AXIS_OVERALL else "버킷", "거래"] + [
        label for _, label in _R_COLUMNS
    ]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["--"] * len(header)) + " |"]
    for _, row in subset.iterrows():
        bucket = row["segment"] if axis == AXIS_OVERALL else row["bucket"]
        cells = [str(row["arm"]), str(bucket), f"{int(row['num_trades']):,}"]
        cells += [f"{float(row[key]):+.4f}" for key, _ in _R_COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _delta_table(frame: pd.DataFrame) -> str:
    subset = frame[frame.axis == AXIS_OVERALL]
    if subset.empty:
        return ""
    before = subset[subset.arm == ARM_ORDER[0]].set_index("segment")
    after = subset[subset.arm == ARM_ORDER[1]].set_index("segment")
    shared = [s for s in SEGMENT_ORDER if s in before.index and s in after.index]
    if not shared:
        return ""
    header = ["구간", "익절 수수료 전", "익절 수수료 후", "Δ", "net R 전", "net R 후", "Δ net R"]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["--"] * len(header)) + " |"]
    for seg in shared:
        b, a = before.loc[seg], after.loc[seg]
        lines.append(
            "| "
            + " | ".join(
                [
                    seg,
                    f"{float(b['take_profit_fee_r']):.4f}",
                    f"{float(a['take_profit_fee_r']):.4f}",
                    f"{float(a['take_profit_fee_r']) - float(b['take_profit_fee_r']):+.4f}",
                    f"{float(b['net_r']):+.4f}",
                    f"{float(a['net_r']):+.4f}",
                    f"{float(a['net_r']) - float(b['net_r']):+.4f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-370 비용 분해 + 익절 메이커 전환")
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
    if args.from_csv:
        if not CSV_PATH.exists():
            print(f"CSV가 없습니다: {CSV_PATH}", flush=True)
            return 1
        frame = pd.read_csv(CSV_PATH)
        checks: dict[str, float] = {}
    else:
        frame, checks = run_report(
            [s.strip() for s in args.symbols.split(",") if s.strip()],
            [t.strip() for t in args.timeframes.split(",") if t.strip()],
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        frame.to_csv(CSV_PATH, index=False)
        print(f"[wan370] CSV: {CSV_PATH}", flush=True)
    SUMMARY_PATH.write_text(render_summary(frame, checks), encoding="utf-8")
    print(f"[wan370] 요약: {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
