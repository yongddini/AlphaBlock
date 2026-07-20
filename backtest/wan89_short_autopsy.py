"""WAN-89 숏 부검 — 하락장(OOS)에서 숏이 지는 이유를 현행 엔진 위에서 다시 묻는다.

출발점은 WAN-84의 "OOS 숏 −3.82%"였다. 그런데 그 수치를 만든 엔진은 지금과 **다섯 번**
다르다(WAN-100 첫 탭 면제 배선 · WAN-111 6심볼 · WAN-112 오프셋 2bp · WAN-123 RSI 게이트
제거 · WAN-132 봉내 라이브 밴드). 특히 WAN-84 시절 **모든 숏 진입이 `RSI ≥ 70`을 요구**
했으므로, 그 −3.82%는 "하락장에서 숏을 쳤을 때"가 아니라 **"하락장에서 강한 반등이 났을
때만 골라 숏을 쳤을 때"** 의 성적이다. 그래서 이 모듈은 부검보다 **재측정을 앞에** 둔다
(이슈 코멘트 §4 「0단 게이트」).

## 팔 (arm)

| 팔 | 뜻 |
| -- | -- |
| `long_only` | 채택 기본값(`ConfluenceParams()`) — 기준선 |
| `both` | `short_enabled=True`(롱+숏) |
| `short_only` | 숏만(`cfg.allow_long=False`) — **0단 판정은 이 팔의 OOS 부호** |
| `short_gate_first_tap` | 숏만 + `rsi_gate_mode="first_tap_free"`(WAN-81~122 규칙) — 가설 D |
| `short_gate_extreme` | 숏만 + 극단 게이트(`rsi_gate_mode="extreme"`, WAN-100 이전) — 가설 E |
| `short_once` | 숏만 + `retap_mode="once"` — WAN-138(롱 축) 결론이 숏에서도 성립하는가 |

⚠️ **`short_only`는 `both`의 숏 부분과 같지 않다** — 동시 1포지션 제약 때문에 롱이 슬롯을
잡고 있으면 숏 셋업이 스킵된다. 숏 **단독**의 부호를 보려면 롱을 빼야 하고(그게 이 팔),
롱과 함께 돌릴 때의 실제 기여는 `both` vs `long_only` 델타로 본다. 두 축을 함께 낸다.

## 부검 지표 (롱/숏 대비표)

`both` 팔의 셋업·거래를 롱/숏으로 갈라 이슈 §작업범위 1의 네 지표를 낸다.

* **기각률(가설 A)** — 볼린저를 끈 팔(`deviation_filter=None`)의 eligible 셋업 수를 분모로,
  켠 팔의 eligible 수를 분자로 둔다. 볼린저가 셋업을 **통째로 없애는 경로는 규칙 3**
  (밴드가 존 전체보다 불리 → 진입 없음)뿐이므로 그 차이가 곧 기각률이다(워밍업 몫 포함).
  ⚠️ 봉내 라이브 밴드(WAN-132)에서는 규칙 3 판정이 **탭 봉 한 점이 아니라 봉 내부 경로
  전체**에 대해 일어난다 — "끝까지 주문을 한 번도 걸지 못한 셋업"이 기각이다.
* **진입 경로(가설 B)** — 체결가가 존 근단(+오프셋)이면 규칙 1, 존 안이면 규칙 2(밴드가).
  규칙 2의 **1R 축소 배율**(= 실제 1R / 근단 진입 1R)과 그 역수인 **수량 증폭 배율**을 낸다.
* **손절 사후 추적** — 손절당한 거래가 청산 후 N봉(10·20·50) 안에 **진입가 너머로 되돌아
  왔는지**(숏이면 진입가 아래). 되돌아온 비율이 높으면 방향이 아니라 **손절 위치**가 문제다.
* **비용 분해** — 진입·청산 수수료와 **펀딩비**를 진입 명목가 대비 비율로. 크립토 무기한은
  통상 롱이 지불하므로 숏의 `funding_pct`는 **음수(수취)** 여야 정상이다.

여기에 가설 D를 위한 한 줄을 더한다: 체결 셋업의 **탭 봉 RSI가 옛 게이트를 통과했을
비율**(숏 `RSI≥70` · 롱 `RSI≤30`). 이 값이 작을수록 옛 게이트가 표본을 코너로 몰았다는 뜻이다.
⚠️ 이 RSI는 **탭 봉 확정 RSI**다 — 엔진이 게이트에 쓰는 값은 체결 순간의 봉내 실시간
RSI(`rsi_mode="realtime"`)라 정확히 같지 않다. 크기가 아니라 **자릿수**로만 읽는다.

## 장세 라벨 (WAN-139 Phase 0과 공유)

장세를 새로 정의하지 않는다 — **IS/OOS 분할이 곧 상승장/하락장 분할**이라는 관찰(이슈
코멘트 §3)을 그대로 쓰고, 근거로 각 셀의 **구간 바이앤홀드**(`buy_hold`)를 같은 행에 싣는다.
각자 정의하면 자유 파라미터가 두 배가 되므로 라벨 축은 하나만 둔다.

## 창·렌즈·기본값

창은 WAN-111/114/117/134/137/138과 같게 **2023-07-14~2026-07-15로 못 박고**, 심볼은
6심볼(WAN-111), TF는 15m·1h(WAN-107), 렌즈는 `baseline` 단독(WAN-128)이다. **기본값은
바꾸지 않는다** — `short_enabled` 전환은 사용자 결정이고 이 모듈은 측정 전용이다.

재현:

```
uv run python -m backtest.wan89_short_autopsy --tf 1h            # 1h 먼저(가벼움)
uv run python -m backtest.wan89_short_autopsy --tf 15m --append  # 15m 뒤에 붙임
uv run python -m backtest.wan89_short_autopsy --from-csv         # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import rsi as rsi_series
from strategy.models import ConfluenceParams, OrderBlock, RsiGateMode

REPORTS_DIR = Path("backtest/reports")

#: 못 박은 창 — WAN-111/114/117/134/137/138과 동일(`--years`는 창이 미끄러진다).
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

DEFAULT_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "TRXUSDT",
)
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 손절 사후 추적 창(상위TF 봉 수).
REVERT_HORIZONS: tuple[int, ...] = (10, 20, 50)


# --------------------------------------------------------------------------- #
# 팔 정의
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    """한 팔 = 채택 기본값에서 **명시한 필드만** 바꾼 설정."""

    name: str
    short_enabled: bool
    allow_long: bool
    rsi_gate_mode: RsiGateMode | None = None
    retap_mode: str | None = None

    def params(self) -> ConfluenceParams:
        base = ConfluenceParams()
        if self.rsi_gate_mode is not None:
            base = base.model_copy(update={"rsi_gate_mode": self.rsi_gate_mode})
        return harness.build_params(
            short_enabled=self.short_enabled,
            retap_mode=self.retap_mode,
            base=base,
        )

    def config(self, timeframe: str) -> BacktestConfig:
        cfg = harness.build_config(timeframe)
        if not self.allow_long:
            cfg = cfg.model_copy(update={"allow_long": False})
        return cfg


ARMS: tuple[Arm, ...] = (
    Arm(name="long_only", short_enabled=False, allow_long=True),
    Arm(name="both", short_enabled=True, allow_long=True),
    Arm(name="short_only", short_enabled=True, allow_long=False),
    Arm(
        name="short_gate_first_tap",
        short_enabled=True,
        allow_long=False,
        rsi_gate_mode="first_tap_free",
    ),
    Arm(
        name="short_gate_extreme",
        short_enabled=True,
        allow_long=False,
        rsi_gate_mode="extreme",
    ),
    Arm(name="short_once", short_enabled=True, allow_long=False, retap_mode="once"),
)

ARMS_BY_NAME: dict[str, Arm] = {a.name: a for a in ARMS}

#: 부검 지표를 뽑는 팔 — 롱/숏이 **같은 실행 안에서** 슬롯을 다투는 실제 조건이라야
#: 대비표가 의미를 갖는다.
DIAG_ARM = ARMS_BY_NAME["both"]


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class _NanIsNone(BaseModel):
    """CSV 왕복에서 빈 칸(→ `NaN`)을 `None`으로 되돌리는 공통 베이스.

    `pd.read_csv`는 빈 칸을 `NaN`(float)로 읽는데, 그대로 두면 pydantic이 `float | None`의
    float 가지로 받아 **저장 전 행과 달라진다** — 요약만 다시 그리는 `--from-csv` 경로가
    원본과 어긋나는 그 사고다(`harness.RunRow`가 같은 이유로 같은 가드를 둔다).
    """

    model_config = ConfigDict(frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _nan_to_none(cls, value: object) -> object:
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


class PnlRow(_NanIsNone):
    """한 (심볼, TF, 구간, 팔)의 성과."""

    symbol: str
    timeframe: str
    segment: str
    arm: str
    num_bars: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    sharpe: float | None
    mean_r: float | None
    fill_rate: float | None
    eligible_setups: int | None
    num_filled: int | None
    funding_coverage: float | None
    buy_hold: float
    """구간 바이앤홀드 수익률 — 장세 라벨(WAN-139 Phase 0과 공유하는 유일한 정의)."""


class DiagRow(_NanIsNone):
    """한 (심볼, TF, 구간, 방향)의 부검 지표 (`both` 팔)."""

    symbol: str
    timeframe: str
    segment: str
    side: str
    setups_no_band: int
    """볼린저를 끈 팔의 eligible 셋업 수(= 기각 이전의 분모)."""
    setups_with_band: int
    """채택 기본값(볼린저 켬)의 eligible 셋업 수."""
    reject_rate: float | None
    """`1 - setups_with_band / setups_no_band` — 가설 A(셋업을 **통째로** 없앤 몫)."""
    fill_rate_no_band: float | None
    fill_rate_with_band: float | None
    """볼린저 on/off의 체결률. 봉내 라이브 밴드에서 규칙 3은 셋업을 없애기보다 **주문이
    걸려 있는 시간을 줄인다** — 그 몫은 기각률이 아니라 이 두 값의 차이로 나타난다."""
    filled: int
    path_proximal: int
    """규칙 1(존 근단 + 오프셋)로 체결된 수."""
    path_band: int
    """규칙 2(밴드가 존 안 → 밴드가)로 체결된 수 — 가설 B의 대상."""
    r_shrink_median: float | None
    """규칙 2 체결의 `실제 1R / 근단 진입 1R` 중앙값(1보다 작으면 축소)."""
    qty_amp_median: float | None
    """그 역수 = 리스크 사이징이 키우는 수량 배율."""
    stops: int
    revert_10: float | None
    revert_20: float | None
    revert_50: float | None
    """손절 후 N봉 안에 진입가 너머로 되돌아온 비율."""
    fee_pct: float | None
    """(진입 + 청산 수수료) / 진입 명목가 합."""
    funding_pct: float | None
    """펀딩비 / 진입 명목가 합. 음수 = 수취."""
    funding_total: float
    old_gate_pass_frac: float | None
    """체결 셋업 중 **탭 봉 확정 RSI**가 옛 게이트를 통과했을 비율(숏 ≥70 · 롱 ≤30) — 가설 D."""


# --------------------------------------------------------------------------- #
# 부검 지표 산출
# --------------------------------------------------------------------------- #


def _segment_of(time_ms: int, boundary: int) -> str:
    return harness.SEGMENT_IS if time_ms < boundary else harness.SEGMENT_OOS


def _is_boundary(frame: pd.DataFrame) -> int:
    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    return start + int((end - start) * harness.IS_FRACTION)


def _entry_path(
    params: ConfluenceParams,
    cand_side: PositionSide,
    entry_price: float,
    order_block: OrderBlock,
) -> tuple[str, float]:
    """체결가가 규칙 1(근단)인지 규칙 2(밴드)인지와, 근단 진입가를 함께 낸다.

    가격 확정 순서는 `deviation_entry_price` → `apply_zone_limit_offset`이므로(WAN-99),
    규칙 1이면 체결가가 **오프셋을 얹은 근단가와 정확히 같다**. 부동소수 비교라 상대
    허용오차를 두되, 그 폭(1e-9)은 2bp 오프셋보다 다섯 자릿수 작아 두 경로를 섞지 않는다.
    """
    is_long = cand_side is PositionSide.LONG
    proximal = params.zone_limit_price(order_block)
    proximal_off = params.apply_zone_limit_offset(proximal, is_long=is_long)
    tol = max(1e-9, abs(proximal_off) * 1e-9)
    path = "proximal" if abs(entry_price - proximal_off) <= tol else "band"
    return path, proximal_off


def _revert_rate(
    frame: pd.DataFrame,
    trades: Sequence[tuple[int, float, PositionSide]],
    horizon: int,
) -> float | None:
    """손절 거래가 청산 후 `horizon`봉 안에 진입가 너머로 되돌아온 비율.

    "너머"는 그 방향이 옳았을 때 가는 쪽이다 — 숏이면 저가가 진입가 이하, 롱이면 고가가
    진입가 이상. 창이 데이터 끝을 넘는 거래는 **판정 불가라 제외**한다(짧은 창을
    "안 돌아왔다"로 세면 구간 끝에서 비율이 인위적으로 낮아진다).
    """
    if not trades:
        return None
    times = frame["open_time"].astype("int64").tolist()
    lows = frame["low"].astype(float).tolist()
    highs = frame["high"].astype(float).tolist()
    hits = 0
    judged = 0
    for exit_time, entry_price, side in trades:
        pos = int(pd.Series(times).searchsorted(exit_time, side="right"))
        if pos + horizon > len(times):
            continue
        judged += 1
        window_low = min(lows[pos : pos + horizon])
        window_high = max(highs[pos : pos + horizon])
        if side is PositionSide.SHORT:
            hits += int(window_low <= entry_price)
        else:
            hits += int(window_high >= entry_price)
    return hits / judged if judged else None


def _old_gate_pass(side: PositionSide, rsi_value: float, params: ConfluenceParams) -> bool:
    """탭 봉 확정 RSI가 **옛 극단 게이트**를 통과했을지."""
    if side is PositionSide.SHORT:
        return rsi_value >= params.rsi_overbought
    return rsi_value <= params.rsi_oversold


@dataclass
class _SideBucket:
    """한 (구간, 방향)의 부검 원자료."""

    setups_no_band: int = 0
    setups_with_band: int = 0
    filled_no_band: int = 0
    filled_with_band: int = 0
    filled: int = 0
    path_proximal: int = 0
    path_band: int = 0
    shrinks: list[float] | None = None
    stops: list[tuple[int, float, PositionSide]] | None = None
    fee: float = 0.0
    funding: float = 0.0
    notional: float = 0.0
    gate_pass: int = 0
    gate_seen: int = 0

    def __post_init__(self) -> None:
        self.shrinks = [] if self.shrinks is None else self.shrinks
        self.stops = [] if self.stops is None else self.stops


def diagnose_cell(market: harness.MarketData) -> list[DiagRow]:
    """한 (심볼, TF)의 부검 지표를 롱/숏 × 구간으로 낸다.

    구간은 **거래를 사후에 가른다** — 여기서 세는 것은 셋업·거래 단위 카운트라 복리
    자본과 무관하고, 그래서 WAN-99의 "구간마다 독립 백테스트" 규칙이 요구되지 않는다
    (그 규칙은 `total_return`처럼 자본이 굴러가는 지표의 것이다). 손익 격자(`PnlRow`)는
    그 규칙대로 구간마다 따로 돈다.
    """
    if market.empty or market.df_1m.empty:
        return []
    params = DIAG_ARM.params()
    cfg = DIAG_ARM.config(market.timeframe)
    frame = market.htf_df.reset_index(drop=True)
    boundary = _is_boundary(frame)
    ob_result = harness.detect_order_blocks(market)

    # 분모: 볼린저를 끈 팔. 규칙 3(진입 없음)이 사라지므로 커버된 탭이 전부 eligible이다.
    no_band = params.model_copy(update={"deviation_filter": None})
    sink_off: list[SetupDiagnostic] = []
    build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=no_band,
        cfg=cfg,
        order_block_result=ob_result,
        setup_sink=sink_off,
    )

    sink_on: list[SetupDiagnostic] = []
    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
        setup_sink=sink_on,
    )

    rsi_vals = rsi_series(frame, length=params.rsi_length, source=params.source).tolist()
    time_to_pos = {int(t): i for i, t in enumerate(frame["open_time"].astype("int64"))}

    buckets: dict[tuple[str, str], _SideBucket] = {}

    def bucket(segment: str, side: PositionSide) -> _SideBucket:
        return buckets.setdefault((segment, side.value), _SideBucket())

    for diag in sink_off:
        for seg in (harness.SEGMENT_FULL, _segment_of(diag.trigger_time, boundary)):
            b_off = bucket(seg, diag.side)
            b_off.setups_no_band += 1
            b_off.filled_no_band += int(diag.filled)
    for diag in sink_on:
        for seg in (harness.SEGMENT_FULL, _segment_of(diag.trigger_time, boundary)):
            b_on = bucket(seg, diag.side)
            b_on.setups_with_band += 1
            b_on.filled_with_band += int(diag.filled)

    for cand, trade in sequence_with_candidates(candidates, cfg, market.funding_rates):
        side = cand.side
        if cand.order_block is None:
            continue  # 진단 전용 링크라 이론상 항상 있으나, 없으면 경로를 지어내지 않는다.
        path, proximal_off = _entry_path(params, side, cand.entry_price, cand.order_block)
        base_r = abs(proximal_off - cand.stop_price)
        actual_r = abs(cand.entry_price - cand.stop_price)
        notional = trade.entry_price * trade.quantity
        fee = trade.entry_fee + sum(f.fee for f in trade.exits)
        pos = time_to_pos.get(cand.trigger_time)
        rsi_value = rsi_vals[pos] if pos is not None else float("nan")
        stopped = trade.exits[-1].reason is ExitReason.STOP_LOSS

        for seg in (harness.SEGMENT_FULL, _segment_of(cand.trigger_time, boundary)):
            b = bucket(seg, side)
            b.filled += 1
            b.fee += fee
            b.funding += trade.funding_cost
            b.notional += notional
            if path == "proximal":
                b.path_proximal += 1
            else:
                b.path_band += 1
                if base_r > 0:
                    assert b.shrinks is not None
                    b.shrinks.append(actual_r / base_r)
            if stopped:
                assert b.stops is not None
                b.stops.append((trade.exit_time, trade.entry_price, side))
            if not pd.isna(rsi_value):
                b.gate_seen += 1
                b.gate_pass += int(_old_gate_pass(side, float(rsi_value), params))

    rows: list[DiagRow] = []
    for (segment, side_name), b in sorted(buckets.items()):
        assert b.shrinks is not None and b.stops is not None
        shrink_med = statistics.median(b.shrinks) if b.shrinks else None
        reverts = {h: _revert_rate(frame, b.stops, h) for h in REVERT_HORIZONS}
        rows.append(
            DiagRow(
                symbol=market.symbol,
                timeframe=market.timeframe,
                segment=segment,
                side=side_name,
                setups_no_band=b.setups_no_band,
                setups_with_band=b.setups_with_band,
                reject_rate=(
                    1.0 - b.setups_with_band / b.setups_no_band if b.setups_no_band else None
                ),
                fill_rate_no_band=(
                    b.filled_no_band / b.setups_no_band if b.setups_no_band else None
                ),
                fill_rate_with_band=(
                    b.filled_with_band / b.setups_with_band if b.setups_with_band else None
                ),
                filled=b.filled,
                path_proximal=b.path_proximal,
                path_band=b.path_band,
                r_shrink_median=shrink_med,
                qty_amp_median=(1.0 / shrink_med if shrink_med else None),
                stops=len(b.stops),
                revert_10=reverts[10],
                revert_20=reverts[20],
                revert_50=reverts[50],
                fee_pct=(b.fee / b.notional if b.notional else None),
                funding_pct=(b.funding / b.notional if b.notional else None),
                funding_total=b.funding,
                old_gate_pass_frac=(b.gate_pass / b.gate_seen if b.gate_seen else None),
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 손익 격자
# --------------------------------------------------------------------------- #


def _buy_hold(frame: pd.DataFrame) -> float:
    closes = frame["close"].astype(float)
    first = float(closes.iloc[0])
    return float(closes.iloc[-1]) / first - 1.0 if first else 0.0


def pnl_rows_for_cell(market: harness.MarketData, arms: Sequence[Arm] = ARMS) -> list[PnlRow]:
    """한 (심볼, TF)의 팔 × 구간 손익. 구간마다 초기자본에서 새로 시작한다(WAN-99)."""
    rows: list[PnlRow] = []
    for segment in harness.segments_for(oos=True):
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        ob_result = harness.detect_order_blocks(window)
        buy_hold = _buy_hold(window.htf_df)
        for arm in arms:
            params = arm.params()
            outcome = harness.run_once(
                window,
                params=params,
                cfg=arm.config(window.timeframe),
                order_block_result=ob_result,
            )
            m = outcome.result.metrics
            stats = outcome.stats
            rows.append(
                PnlRow(
                    symbol=market.symbol,
                    timeframe=market.timeframe,
                    segment=segment.name,
                    arm=arm.name,
                    num_bars=len(window.htf_df),
                    num_trades=m.num_trades,
                    win_rate=m.win_rate,
                    total_return=m.total_return,
                    max_drawdown=m.max_drawdown,
                    sharpe=m.sharpe,
                    mean_r=harness.mean_r(outcome.result, params.take_profit_r),
                    fill_rate=stats.fill_rate if stats else None,
                    eligible_setups=stats.eligible if stats else None,
                    num_filled=stats.filled if stats else None,
                    funding_coverage=m.funding_coverage,
                    buy_hold=buy_hold,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _CellTask:
    symbol: str
    timeframe: str
    start: str
    end: str


@dataclass(frozen=True)
class _CellResult:
    pnl: list[PnlRow]
    diag: list[DiagRow]
    log: str


def _run_cell(task: _CellTask) -> _CellResult:
    market = harness.load_market_data(
        harness.normalize_symbol(task.symbol),
        task.timeframe,
        start_ms=parse_date_ms(task.start),
        end_ms=parse_date_ms(task.end),
        need_1m=True,
    )
    if market.empty or market.df_1m.empty:
        return _CellResult([], [], f"[wan89] {task.symbol} {task.timeframe}: 데이터 없음 — 건너뜀")
    pnl = pnl_rows_for_cell(market)
    diag = diagnose_cell(market)
    return _CellResult(
        pnl,
        diag,
        f"[wan89] {market.symbol} {task.timeframe}: {len(market.htf_df)}봉 "
        f"→ 손익 {len(pnl)}행 · 부검 {len(diag)}행",
    )


def run_report(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
) -> tuple[list[PnlRow], list[DiagRow]]:
    """격자를 돌려 손익 행과 부검 행을 낸다.

    `jobs`는 **성능 노브이지 결과 축이 아니다**(WAN-121) — (심볼, TF) 단위로만 갈라
    제출 순서대로 모으므로 직렬과 행·순서가 같다.
    """
    tasks = [_CellTask(s, tf, start, end) for tf in timeframes for s in symbols]
    pnl: list[PnlRow] = []
    diag: list[DiagRow] = []
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(_run_cell, tasks))
    else:
        results = [_run_cell(t) for t in tasks]
    for res in results:
        print(res.log)
        pnl.extend(res.pnl)
        diag.extend(res.diag)
    return pnl, diag


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def pnl_to_frame(rows: Sequence[PnlRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(PnlRow.model_fields))


def diag_to_frame(rows: Sequence[DiagRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(DiagRow.model_fields))


def pnl_from_csv(path: Path) -> list[PnlRow]:
    frame = pd.read_csv(path)
    return [PnlRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def diag_from_csv(path: Path) -> list[DiagRow]:
    frame = pd.read_csv(path)
    return [DiagRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def symbol_mean(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """(TF, 구간, 팔) 심볼평균 — 채택 리포트들이 쓰는 것과 같은 집계 축."""
    return (
        frame.groupby(["timeframe", "segment", "arm"], as_index=False)[column]
        .mean()
        .rename(columns={column: f"mean_{column}"})
    )


def _pct(value: float | None) -> str:
    """부호가 뜻을 갖는 값(수익률·펀딩)용 — 항상 부호를 찍는다."""
    return "—" if value is None or pd.isna(value) else f"{value * 100:+.2f}%"


def _rate(value: float | None) -> str:
    """0 이상으로만 정의되는 비율(승률·MDD·체결률·기각률)용 — 부호를 찍지 않는다."""
    return "—" if value is None or pd.isna(value) else f"{value * 100:.2f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.{digits}f}"


def _pnl_cell(frame: pd.DataFrame, timeframe: str, segment: str, arm: str) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe) & (frame["segment"] == segment) & (frame["arm"] == arm)
    ]


def _mean_or_none(frame: pd.DataFrame, column: str) -> float | None:
    return None if frame.empty else float(frame[column].mean())


def leave_one_out(
    pnl: pd.DataFrame, timeframe: str, segment: str, arm: str
) -> list[tuple[str, float]]:
    """심볼을 하나씩 빼며 낸 평균 — 한 심볼이 표를 떠받치고 있는지 본다.

    이 저장소의 모든 채택 수치가 겪은 편중(ETH·SOL 하나를 빼면 평균이 마이너스)을 숏 축에서도
    같은 자로 확인하기 위한 가드다. 심볼이 둘 미만이면 뺄 것이 없어 빈 목록을 낸다.
    """
    cell = _pnl_cell(pnl, timeframe, segment, arm)
    if len(cell) < 2:
        return []
    out: list[tuple[str, float]] = []
    for symbol in sorted(cell["symbol"].unique()):
        rest = cell[cell["symbol"] != symbol]
        out.append((str(symbol), float(rest["total_return"].mean())))
    return sorted(out, key=lambda pair: pair[1])


def verdict_for_tf(pnl: pd.DataFrame, timeframe: str) -> str:
    """0단 판정 문장 — 현행 엔진에서 숏 단독 OOS 부호."""
    oos = _pnl_cell(pnl, timeframe, harness.SEGMENT_OOS, "short_only")
    mean = _mean_or_none(oos, "total_return")
    if mean is None:
        return f"- **{timeframe}**: 데이터 없음 — 판정 불가"
    positives = int((oos["total_return"] > 0).sum())
    sign = "여전히 마이너스" if mean < 0 else "부호가 뒤집혔다"
    both = _mean_or_none(_pnl_cell(pnl, timeframe, harness.SEGMENT_OOS, "both"), "total_return")
    long_only = _mean_or_none(
        _pnl_cell(pnl, timeframe, harness.SEGMENT_OOS, "long_only"), "total_return"
    )
    delta = (
        f" · 롱과 함께 돌릴 때 기여 `both`−`long_only` = "
        f"**{_pct(both - long_only)}p**({_pct(long_only)} → {_pct(both)})"
        if both is not None and long_only is not None
        else ""
    )
    return (
        f"- **{timeframe}**: 숏 단독 OOS 심볼평균 **{_pct(mean)}** "
        f"({positives}/{len(oos)}심볼 플러스) → **{sign}**{delta}"
    )


def write_summary(pnl: pd.DataFrame, diag: pd.DataFrame, path: Path) -> Path:
    """요약 md를 쓴다. 결론 문장은 **표에서 계산한 값으로만** 만든다."""
    lines: list[str] = []
    lines.append("# WAN-89 — 숏 부검 (현행 엔진 재측정 + 롱/숏 대비표)")
    lines.append("")
    lines.append(
        "**성격** 측정 전용. `short_enabled` 기본값·토대를 바꾸지 않는다"
        "(실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지). 렌즈 `baseline` 단독(WAN-128), "
        f"창 못 박음({DEFAULT_START}~{DEFAULT_END}), 6심볼(WAN-111), 15m·1h(WAN-107)."
    )
    lines.append("")
    lines.append("**재현**")
    lines.append("")
    lines.append("```")
    lines.append("uv run python -m backtest.wan89_short_autopsy --tf 1h")
    lines.append("uv run python -m backtest.wan89_short_autopsy --tf 15m --append")
    lines.append("```")
    lines.append("")
    lines.append(
        "원본: `backtest/reports/wan89_short_autopsy.csv`(손익) · "
        "`backtest/reports/wan89_short_diagnostics.csv`(부검 지표)."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 0단 — 재측정 게이트: 현행 엔진에서 숏 단독 OOS 부호")
    lines.append("")
    for timeframe in sorted(pnl["timeframe"].unique()):
        lines.append(verdict_for_tf(pnl, timeframe))
    lines.append("")
    lines.append(
        "⚠️ **WAN-84의 −3.82%를 그대로 인용하지 말 것** — 그 표의 숏은 "
        "**모든 탭에 `RSI ≥ 70`을 요구**했다(WAN-100 이전 B안 버그). 아래 §「그때와 지금」 참고."
    )
    lines.append("")
    lines.append("### 팔별 심볼평균 (공식 렌즈 `baseline`)")
    lines.append("")
    lines.append("| TF | 구간 | 팔 | return% | MDD% | 승률 | 거래 | 체결률 | 바이앤홀드 |")
    lines.append("| -- | ---- | -- | ------- | ---- | ---- | ---- | ------ | ---------- |")
    for timeframe in sorted(pnl["timeframe"].unique()):
        for segment in (harness.SEGMENT_FULL, harness.SEGMENT_IS, harness.SEGMENT_OOS):
            for arm in ARMS:
                cell = _pnl_cell(pnl, timeframe, segment, arm.name)
                if cell.empty:
                    continue
                lines.append(
                    f"| {timeframe} | {segment} | `{arm.name}` | "
                    f"{_pct(_mean_or_none(cell, 'total_return'))} | "
                    f"{_rate(_mean_or_none(cell, 'max_drawdown'))} | "
                    f"{_rate(_mean_or_none(cell, 'win_rate'))} | "
                    f"{_num(_mean_or_none(cell, 'num_trades'), 1)} | "
                    f"{_rate(_mean_or_none(cell, 'fill_rate'))} | "
                    f"{_pct(_mean_or_none(cell, 'buy_hold'))} |"
                )
    lines.append("")
    lines.append(
        "📌 **장세 라벨은 새로 정의하지 않았다** — `buy_hold` 열이 곧 라벨이고, "
        "IS/OOS 분할을 그대로 쓴다(WAN-139 Phase 0과 공유하는 유일한 정의)."
    )
    lines.append("")
    lines.append("### 심볼 편중 (OOS leave-one-out — 한 심볼을 빼면 얼마나 남나)")
    lines.append("")
    for timeframe in sorted(pnl["timeframe"].unique()):
        for arm_name in ("short_only", "both"):
            drops = leave_one_out(pnl, timeframe, harness.SEGMENT_OOS, arm_name)
            if not drops:
                continue
            worst_symbol, worst_mean = drops[0]
            base = _mean_or_none(
                _pnl_cell(pnl, timeframe, harness.SEGMENT_OOS, arm_name), "total_return"
            )
            negatives = [s for s, v in drops if v < 0]
            if not negatives:
                note = " — 어느 하나를 빼도 부호가 유지된다"
            elif len(negatives) == 1:
                note = f" — **{negatives[0]} 하나만 빼도 마이너스**"
            else:
                note = f" — **{', '.join(negatives)} 중 어느 하나만 빼도 마이너스**"
            lines.append(
                f"- **{timeframe} `{arm_name}`**: {_pct(base)} → 최악 {_pct(worst_mean)}"
                f"({worst_symbol} 제외){note}"
            )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 부검 지표 — 롱/숏 대비표 (`both` 팔)")
    lines.append("")
    lines.append(
        "| TF | 구간 | 방향 | 기각률 | 체결률(밴드off→on) | 규칙1 | 규칙2 | 1R축소(중앙) | "
        "수량배율 | 손절 | 되돌림20 | 수수료% | 펀딩% | 옛게이트통과 |"
    )
    lines.append(
        "| -- | ---- | ---- | ------ | ------------------ | ----- | ----- | ------------ | "
        "-------- | ---- | -------- | ------- | ----- | ------------ |"
    )
    for timeframe in sorted(diag["timeframe"].unique()):
        for segment in (harness.SEGMENT_FULL, harness.SEGMENT_IS, harness.SEGMENT_OOS):
            for side in ("long", "short"):
                cell = diag[
                    (diag["timeframe"] == timeframe)
                    & (diag["segment"] == segment)
                    & (diag["side"] == side)
                ]
                if cell.empty:
                    continue
                lines.append(
                    f"| {timeframe} | {segment} | {side} | "
                    f"{_rate(_mean_or_none(cell, 'reject_rate'))} | "
                    f"{_rate(_mean_or_none(cell, 'fill_rate_no_band'))}"
                    f" → {_rate(_mean_or_none(cell, 'fill_rate_with_band'))} | "
                    f"{int(cell['path_proximal'].sum())} | {int(cell['path_band'].sum())} | "
                    f"{_num(_mean_or_none(cell, 'r_shrink_median'))} | "
                    f"{_num(_mean_or_none(cell, 'qty_amp_median'))} | "
                    f"{int(cell['stops'].sum())} | "
                    f"{_rate(_mean_or_none(cell, 'revert_20'))} | "
                    f"{_pct(_mean_or_none(cell, 'fee_pct'))} | "
                    f"{_pct(_mean_or_none(cell, 'funding_pct'))} | "
                    f"{_pct(_mean_or_none(cell, 'old_gate_pass_frac'))} |"
                )
    lines.append("")
    lines.append(
        "⚠️ 기각률·되돌림·1R축소는 **심볼평균**, 규칙1·규칙2·손절은 **6심볼 합계**다"
        "(비율은 평균이, 개수는 합이 읽힌다)."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 그때와 지금 — 재탭·게이트 규칙 대조 (왜 −3.82%를 인용하면 안 되나)")
    lines.append("")
    lines.append("| | WAN-84 시점(2026-07-14) | 현행 기본값 |")
    lines.append("| -- | -- | -- |")
    lines.append("| `retap_mode` | `every_tap` | `every_tap` — **동일** |")
    lines.append("| 재탭 RSI 게이트 | 롱 ≤30 / 숏 ≥70 요구 | **없음**(`unconditional`, WAN-123) |")
    lines.append("| 첫 탭 면제 | 명세엔 있으나 **B안 코드에 없었다**(WAN-100) | 해당 없음 |")
    lines.append("| 워밍업 구간 탭 | 차단(`rsi is not None`) | 진입 |")
    lines.append("| 밴드 표본 | 탭 봉 종가(`tap`, 룩어헤드) | 봉내 라이브(WAN-132) |")
    lines.append("| 지정가 오프셋 | 0bp | **2bp**(WAN-112) |")
    lines.append("| 심볼 | 3심볼 | **6심볼**(WAN-111) |")
    lines.append("")
    lines.append(
        "즉 WAN-84의 숏 성적은 **「하락장에서 숏을 쳤을 때」가 아니라 "
        "「하락장에서 강한 반등이 났을 때만 골라 숏을 쳤을 때」** 의 것이다."
    )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-89 숏 부검")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-pnl", type=str, default=str(REPORTS_DIR / "wan89_short_autopsy.csv"))
    parser.add_argument(
        "--out-diag", type=str, default=str(REPORTS_DIR / "wan89_short_diagnostics.csv")
    )
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan89_short_autopsy_summary.md")
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 새 TF 행을 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    args = parser.parse_args()

    out_pnl, out_diag, out_md = Path(args.out_pnl), Path(args.out_diag), Path(args.out_md)
    if args.from_csv:
        pnl_rows = pnl_from_csv(out_pnl)
        diag_rows = diag_from_csv(out_diag)
        print(f"[wan89] CSV에서 손익 {len(pnl_rows)}행 · 부검 {len(diag_rows)}행 로드")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        pnl_rows, diag_rows = run_report(
            symbols, timeframes, start=args.start, end=args.end, jobs=args.jobs
        )
        if args.append and out_pnl.exists() and out_diag.exists():
            keep_tfs = set(timeframes)
            pnl_rows = [r for r in pnl_from_csv(out_pnl) if r.timeframe not in keep_tfs] + pnl_rows
            diag_rows = [
                r for r in diag_from_csv(out_diag) if r.timeframe not in keep_tfs
            ] + diag_rows
        out_pnl.parent.mkdir(parents=True, exist_ok=True)
        pnl_to_frame(pnl_rows).to_csv(out_pnl, index=False)
        diag_to_frame(diag_rows).to_csv(out_diag, index=False)
    write_summary(pnl_to_frame(pnl_rows), diag_to_frame(diag_rows), out_md)
    print(f"[wan89] 저장: {out_pnl}, {out_diag}, {out_md}")


if __name__ == "__main__":
    main()
