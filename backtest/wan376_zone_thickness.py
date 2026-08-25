"""WAN-376: 「존의 두께」 층을 인과 엔진에서 다시 잰다 — §0 생존 지도 ＋ §1a 지름길 검산.

## 한 줄

존폭 필터 **1.28**(WAN-159)과 손절폭 가드 **0.3%**(WAN-76/79)는 둘 다 **소급 취소 버그가
있던 엔진**의 표를 비교해 골라졌다. WAN-365가 그 버그를 고친 뒤 인과 엔진에서 잰 것은 **그
값에서의 켬/끔뿐**(WAN-366/368)이고 **눈금을 흔들어 본 적이 없다.** 이 모듈은 그 재측정의
**앞 두 단**을 낸다 — 비싼 격자(§1b)를 돌리기 전에 답이 나와야 하는 것들이다.

🚨 **이 PR은 §0 ＋ §1a까지다.** 이슈가 *「단계별로 갑니다. PR을 나눕니다」*라고 못 박았다:
§0의 답이 §1b의 격자 점을 바꾸고, §1a의 답이 §1b의 비용을 **N배** 바꾼다. ★결정(§1b 점
확정)은 **사용자 몫**이고 개발자가 임의로 고르지 않는다.

## §0 — 생존 지도 (탐지 층 · 1분봉 안 읽음)

두 문턱이 보는 값은 **전부 탐지 층에서 나온다**:

| 재는 것 | 계산 | 1분봉 |
| -- | -- | -- |
| 존폭 필터 | 존높이 ÷ ATR14(탭 봉 **직전 확정봉**) | ❌ |
| 가드 (`proximal`) | (진입가 − 존 원단) ÷ 진입가, 진입가 = 존 근단 ＋ 2bp | ❌ |
| 가드 (`mid`) | 같은 식, 진입가 = 존 중앙 ＋ 2bp | ❌ |
| 가드 (볼린저) | 밴드가 필요 | ✅ **못 함** |

🔑 **한 탭마다 두 스칼라를 한 번 구해 두면 문턱은 그 위의 컷이라 공짜다** — 그래서 4점이
아니라 **연속 지도**(분위 + 격자)를 낸다.

⚠️ **§0는 「탭」을 세지 「거래」를 안 센다.** 볼린저 기각·체결 실패·북 용량이 뒤에서 또
깎으므로 이 표는 **상한이고 분류(triage)이지 측정이 아니다.** ⚠️ **볼린저 팔은 지도에
안 나온다**(진입가가 봉 안에서 정해져 탐지 층에서 계산되지 않는다).

**왜 미리 보나** — 붕괴 전례가 둘이다: WAN-154 §3(TRX 15m 필터 후보의 **92.6%를 가드가 잘라
16거래**) · WAN-161(문턱 1.15에서 TRX 15m **12거래**). 비싼 격자를 돌리고 나서 「절반이 판정
불가였다」를 알면 낭비다.

## §1a — 지름길이 성립하는가 (이 이슈의 갈림)

> 필터 **끔**으로 후보를 1번 만들고 → 「존폭÷ATR > f」인 후보를 **빼고 재배치**한 것
> ≡ 처음부터 **필터 켜고** 만든 것

|  | §1b 후보 생성 |
| -- | -- |
| 성립 ❌ | **3N패스**(진입가 3팔 × 문턱 N점) |
| 성립 ✅ | **3패스** ＋ 문턱을 §0처럼 촘촘히 |

🚨 **선례는 칸 단위지 셋업 단위가 아니다** — WAN-316이 보인 것은 *「4TF 후보에서 15m **칸**만
빼고 배치하면 3TF와 비트 일치」*다. 여기서 하려는 건 **칸 안에서 셋업을 빼는 것**이라 **더
강한 주장**이고, 그래서 §1a는 형식이 아니라 진짜 관문이다.

⚠️ **가장 의심스러운 자리는 재진입이다** — 재진입 후보는 base 후보에서 파생되므로(WAN-261)
컷을 재진입 **뒤에** 걸면 「빠진 셋업의 재진입이 살아남는」 잡종이 된다. 그래서 엔진 쪽
지름길 훅(`run_cells(post_filter_zone_width=)`)은 컷을 **base 직후 · 재진입 파생 앞**에
건다. 이 모듈은 그 파생까지 **집합으로** 대조한다.

## 엔진 추가 — 관측 필드 하나 (WAN-376, 옵트인)

`_Candidate`도 `SetupDiagnostic`도 존폭÷ATR를 안 싣고 있었다. `observe_zone_width_atr=True`면
**엔진이 필터에 실제로 쓰는 그 비율**을 그대로 싣는다 — `mfe_r`(WAN-90)·`exit_extreme`
(WAN-276)·`path_fill_price`(WAN-328)와 같은 **순수 관측 필드**라 값을 싣는 것만으로는 어떤
수치도 안 움직인다. 🚨 **밖에서 ATR을 다시 계산하지 말 것**(WAN-77의 사본이 실제로 엔진과
갈라졌다) — §0의 지도만은 후보가 되기 **전** 단계라 탐지 층에서 직접 계산하되, **엔진과 같은
산식·같은 봉 위치**(`pos-1`)를 쓰고 그 사실을 §1a가 실데이터로 교차 검산한다.

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 못 박은 6년 창 · cap_only 5배 · 오프셋 2bp · 재진입
ON(band) · 유동성 한도 채택값 · **익절 청산 유동성 채택값**(`harness.ADOPTED_TAKE_PROFIT_
LIQUIDITY` — 후보 생성 · 배치 **둘 다에 명시**, WAN-370/373) · `baseline` 렌즈 · 취소 시점은
인자를 안 줘 채택(인과 `bar_close`, WAN-365)을 물려받는다.

🚨 **판단은 북에서 낸다**(WAN-341). ⚠️ **북은 이어붙일 수 없다**(WAN-316) — TF를 더하려면
통째로 다시 돈다. §1a의 TF 집합이 곧 그 지갑의 정체이므로 행에 `scope`로 적는다.

## 재현

```
uv run python -m backtest.wan376_zone_thickness --part census                 # §0 (몇 분)
uv run python -m backtest.wan376_zone_thickness --part shortcut --timeframes 4h,2h,1h --jobs 4
uv run python -m backtest.wan376_zone_thickness --part summary                # 요약만
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import LeverageBookParams
from backtest.run import parse_date_ms
from backtest.wan143_zone_height_tp import MIN_TRADES_PER_SYMBOL
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.zone_limit_backtest import _Candidate
from strategy.confluence import entry_candidate_signals
from strategy.indicators import atr
from strategy.models import ConfluenceParams, OrderBlockDirection

REPORTS_DIR = Path("backtest/reports")
MAP_CSV_PATH = REPORTS_DIR / "wan376_survival_map.csv"
QUANTILE_CSV_PATH = REPORTS_DIR / "wan376_thickness_quantiles.csv"
PARITY_CSV_PATH = REPORTS_DIR / "wan376_shortcut_parity.csv"
SUMMARY_PATH = REPORTS_DIR / "wan376_zone_thickness_summary.md"

#: 채택 존폭 문턱(WAN-159)과 손절폭 가드(WAN-79) — 지도의 「지금 여기」 점이다.
ADOPTED_ZONE_WIDTH = 1.28
ADOPTED_STOP_GUARD = 0.003

#: §0 지도의 존폭 문턱 점 — 채택값을 **가운데** 두고 양쪽으로 벌린다. `None` = 필터 끔.
WIDTH_POINTS: tuple[float | None, ...] = (
    None,
    2.60,
    1.80,
    1.55,
    ADOPTED_ZONE_WIDTH,
    1.15,
    1.00,
    0.90,
    0.80,
    0.60,
)

#: §0 지도의 손절폭 가드 점(분수) — `0.0` = 가드 끔. 이슈가 적어 둔 0.2~0.5%를 덮는다.
GUARD_POINTS: tuple[float, ...] = (0.0, 0.0020, 0.0025, ADOPTED_STOP_GUARD, 0.0040, 0.0050)

#: §0 진입가 팔 — 볼린저는 진입가가 봉 안에서 정해져 **탐지 층에 안 나온다**(WAN-119/132).
MAP_ARMS: tuple[str, ...] = ("proximal", "mid")

#: 분위 지도의 눈금 — 「연속」을 표로 옮기는 자다(문턱은 이 위의 컷이라 공짜다).
QUANTILES: tuple[float, ...] = (0.05, 0.10, 0.25, 0.33, 0.50, 0.67, 0.75, 0.90, 0.95)

MAP_KEYS: tuple[str, ...] = ("arm", "symbol", "timeframe", "width_label", "guard")
QUANTILE_KEYS: tuple[str, ...] = ("arm", "symbol", "timeframe", "metric", "quantile")
PARITY_KEYS: tuple[str, ...] = ("scope", "level", "segment")

_TAPS_NOT_TRADES = (
    "⚠️ **이 표는 「탭」을 세지 「거래」를 안 센다** — 볼린저 기각·체결 실패·북 용량이 "
    "뒤에서 또 깎는다. **상한이고 분류(triage)이지 측정이 아니다.**"
)


def width_label(threshold: float | None) -> str:
    """문턱을 CSV 키로 — `None`(끔)과 숫자를 **문자로 가른다**(WAN-159 규약과 같은 부류)."""
    return "off" if threshold is None else f"{threshold:.2f}"


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class SurvivalRow(BaseModel):
    """§0 — (진입가 팔, 칸, 존폭 문턱, 가드) 하나의 생존 탭 수."""

    model_config = ConfigDict(frozen=True)

    arm: str
    symbol: str
    timeframe: str
    width_label: str
    width_threshold: float | None
    guard: float
    total_taps: int
    """이 칸의 진입 후보 탭 전부(인과 엔진이 받는 것 — 무효화 봉 탭 포함, WAN-365)."""
    surviving_taps: int
    survival_share: float
    adopted_point: bool
    """이 점이 오늘의 채택 좌표(1.28 × 0.3%)인가."""
    below_sample_gate: bool
    """생존 탭이 표본 게이트(20건, `wan143.MIN_TRADES_PER_SYMBOL`) 미만인가.

    🚨 탭은 거래의 **상한**이므로 여기서 걸리면 그 칸은 **확실히** 판정 불가다. 반대는
    성립하지 않는다 — 탭이 20건을 넘어도 거래는 그 아래일 수 있다."""


class QuantileRow(BaseModel):
    """§0 — 칸 하나의 두 스칼라 분포(문턱은 이 위의 컷이라 어느 점이든 읽힌다)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    symbol: str
    timeframe: str
    metric: str
    """`zone_width_atr`(진입가 팔과 무관) 또는 `stop_fraction`(팔마다 다르다)."""
    quantile: float
    value: float
    sample: int


class ParityRow(BaseModel):
    """§1a — 한 (스코프, 팔, 구간)의 북 집계. 두 팔의 행이 **비트 일치**해야 지름길이다."""

    model_config = ConfigDict(frozen=True)

    scope: str
    """이 지갑의 정체 = TF 집합(북은 이어붙일 수 없다, WAN-316)."""
    level: str
    """`straight`(처음부터 필터 켜고) 또는 `shortcut`(끄고 만들고 밖에서 컷)."""
    segment: str
    num_cells: int
    num_candidates: int
    num_trades: int
    win_rate: float
    total_return: float
    mean_net_r: float
    max_drawdown: float
    peak_concurrency: int
    liquidation_events: int


# --------------------------------------------------------------------------- #
# §0 — 생존 지도 (탐지 층)
# --------------------------------------------------------------------------- #


class TapThickness(BaseModel):
    """탭 하나의 두 스칼라 — 문턱은 이 위의 컷이다."""

    model_config = ConfigDict(frozen=True)

    zone_width_atr: float | None
    """존폭 ÷ ATR14(직전 확정봉). `None` = 판정 불가 → 엔진은 **기각**한다(WAN-158)."""
    stop_fraction: dict[str, float]
    """진입가 팔 → (진입가 − 존 원단) ÷ 진입가. 가드가 재는 그 양이다."""


def tap_thickness(symbol: str, timeframe: str, *, start_ms: int, end_ms: int) -> list[TapThickness]:
    """한 칸의 탭마다 존폭÷ATR와 팔별 손절폭 — 1분봉을 안 읽는다(탐지 층에서 끝난다).

    🚨 **엔진과 같은 산식·같은 봉 위치**를 쓴다: 비율은 `(top − bottom) ÷ ATR14[pos−1]`
    (탭 봉 자신의 ATR은 그 봉 종가를 알아야 나오므로 룩어헤드), 진입가는
    `ConfluenceParams.zone_limit_price` → `apply_zone_limit_offset`(볼린저 없는 두 팔은 그
    사슬이 전부다). 사본을 손으로 적으면 갈라진다(WAN-77) — 그래서 **엔진의 메서드를
    그대로 부른다.**

    받는 탭은 **인과 엔진이 받는 것과 같은 집합**이다(`status`가 `active`이거나
    `cancelled` — 후자가 무효화 봉에서 난 탭이고 채택 기본값은 그것을 받는다, WAN-365).
    """
    market = harness.load_market_data(
        harness.normalize_symbol(symbol),
        timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        need_1m=False,
        funding=False,
    )
    if market.empty:
        return []
    result = harness.detect_order_blocks(market)
    frame = market.htf_df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    frame = frame.sort_values("open_time").reset_index(drop=True)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    closes = [float(c) for c in frame["close"].astype(float).tolist()]
    time_to_pos = {t: i for i, t in enumerate(times)}

    base = ConfluenceParams()
    arms = {arm: base.model_copy(update={"zone_limit_ref": arm}) for arm in MAP_ARMS}
    atr_vals = [float(v) for v in atr(frame, length=base.zone_width_atr_length).tolist()]

    rows: list[TapThickness] = []
    for signal in entry_candidate_signals(result, base, times, closes, time_to_pos):
        if signal.status not in ("active", "cancelled"):
            continue
        pos = time_to_pos.get(signal.trigger_time)
        if pos is None or pos < 1:
            continue
        ob = signal.order_block
        is_long = ob.direction is OrderBlockDirection.BULLISH
        if not is_long and not base.short_enabled:
            continue  # 채택 기본값은 롱 온리(WAN-87).
        atr_value = atr_vals[pos - 1]
        width = (
            None if math.isnan(atr_value) or atr_value <= 0.0 else (ob.top - ob.bottom) / atr_value
        )
        stop_reference = ob.bottom if is_long else ob.top
        fractions: dict[str, float] = {}
        for arm, params in arms.items():
            entry = params.apply_zone_limit_offset(params.zone_limit_price(ob), is_long=is_long)
            fractions[arm] = abs(entry - stop_reference) / entry if entry > 0.0 else 0.0
        rows.append(TapThickness(zone_width_atr=width, stop_fraction=fractions))
    return rows


def survival_rows(
    symbol: str, timeframe: str, taps: Sequence[TapThickness]
) -> tuple[list[SurvivalRow], list[QuantileRow]]:
    """탭 스칼라 → (문턱 × 가드) 격자 ＋ 분위 — 한 번 구한 값 위의 컷이라 공짜다."""
    symbol = harness.normalize_symbol(symbol)
    total = len(taps)
    grid: list[SurvivalRow] = []
    for arm in MAP_ARMS:
        for threshold in WIDTH_POINTS:
            # 판정 불가(`None`)는 필터가 켜져 있으면 **기각**이고 꺼져 있으면 통과다 —
            # 엔진의 `zone_width_filter_passes`와 같은 규칙이다(WAN-158).
            passes_width = [
                t
                for t in taps
                if threshold is None
                or (t.zone_width_atr is not None and t.zone_width_atr <= threshold)
            ]
            for guard in GUARD_POINTS:
                alive = sum(1 for t in passes_width if t.stop_fraction[arm] >= guard)
                grid.append(
                    SurvivalRow(
                        arm=arm,
                        symbol=symbol,
                        timeframe=timeframe,
                        width_label=width_label(threshold),
                        width_threshold=threshold,
                        guard=guard,
                        total_taps=total,
                        surviving_taps=alive,
                        survival_share=alive / total if total else 0.0,
                        adopted_point=(
                            threshold == ADOPTED_ZONE_WIDTH and guard == ADOPTED_STOP_GUARD
                        ),
                        below_sample_gate=alive < MIN_TRADES_PER_SYMBOL,
                    )
                )

    quantiles: list[QuantileRow] = []
    widths = sorted(t.zone_width_atr for t in taps if t.zone_width_atr is not None)
    for arm in MAP_ARMS:
        series: dict[str, list[float]] = {
            "zone_width_atr": widths,
            "stop_fraction": sorted(t.stop_fraction[arm] for t in taps),
        }
        for metric, values in series.items():
            for q in QUANTILES:
                if not values:
                    continue
                quantiles.append(
                    QuantileRow(
                        arm=arm,
                        symbol=symbol,
                        timeframe=timeframe,
                        metric=metric,
                        quantile=q,
                        value=float(pd.Series(values).quantile(q)),
                        sample=len(values),
                    )
                )
    return grid, quantiles


def run_map(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    log: bool = True,
) -> tuple[list[SurvivalRow], list[QuantileRow]]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    grid: list[SurvivalRow] = []
    quantiles: list[QuantileRow] = []
    for symbol in symbols:
        for timeframe in timeframes:
            taps = tap_thickness(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
            if not taps:
                if log:
                    print(f"[wan376] {symbol} {timeframe}: 데이터 없음 — 건너뜀", flush=True)
                continue
            cell_grid, cell_q = survival_rows(symbol, timeframe, taps)
            grid.extend(cell_grid)
            quantiles.extend(cell_q)
            if log:
                adopted = next(r for r in cell_grid if r.arm == "proximal" and r.adopted_point)
                print(
                    f"[wan376] {symbol} {timeframe}: 탭 {adopted.total_taps} → 채택 좌표 생존 "
                    f"{adopted.surviving_taps} ({adopted.survival_share * 100:.1f}%)",
                    flush=True,
                )
    return grid, quantiles


# --------------------------------------------------------------------------- #
# §1a — 지름길 검산 (북)
# --------------------------------------------------------------------------- #

#: 두 팔의 이름. `straight` = 처음부터 필터 켜고 · `shortcut` = 끄고 만들고 밖에서 컷.
PARITY_ARMS: tuple[str, ...] = ("straight", "shortcut")


def _cell_kwargs() -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계)."""
    return {
        **ADOPTED_CELL_KWARGS,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        # 관측 필드를 **두 팔 모두** 켠다 — 그래야 후보를 정규화 없이 통째로 대조할 수 있고,
        # 「같은 셋업에 같은 비율이 실렸는가」까지 한 번에 검산된다.
        "observe_zone_width_atr": True,
    }


def arm_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arm: str,
    *,
    start: str,
    end: str,
    jobs: int,
) -> list[CellPayload]:
    """한 팔의 칸 후보 — 두 팔의 차이는 **필터를 어디서 거는가** 하나다."""
    kwargs = _cell_kwargs()
    if arm == "straight":
        # 채택 문턱은 **센티넬로 물려받는다**(핀이 아니다) — `assert_adopted_base`가 그 값이
        # 1.28임을 동작으로 고정하므로 라벨과 엔진이 갈라질 수 없다.
        return run_cells(
            symbols,
            timeframes,
            start=start,
            end=end,
            jobs=jobs,
            engine_check=False,
            max_zone_width_atr=harness.UNSET,
            **kwargs,  # type: ignore[arg-type]
        )
    if arm == "shortcut":
        return run_cells(
            symbols,
            timeframes,
            start=start,
            end=end,
            jobs=jobs,
            engine_check=False,
            max_zone_width_atr=None,
            post_filter_zone_width=ADOPTED_ZONE_WIDTH,
            **kwargs,  # type: ignore[arg-type]
        )
    raise ValueError(f"모르는 팔: {arm!r}")


def assert_adopted_base() -> None:
    """라벨이 **오늘의 채택 기본값**과 같은지 — 어긋나면 시끄럽게 죽는다.

    이 모듈은 핀을 하나도 안 쓰고 센티넬로 채택값을 물려받으므로(WAN-305), 기본값이 움직이면
    표의 라벨(`1.28` · `0.3%`)만 낡고 숫자는 조용히 새 값으로 도는 사고가 난다.
    """
    base = ConfluenceParams()
    if base.max_zone_width_atr != ADOPTED_ZONE_WIDTH:
        raise AssertionError(
            f"채택 존폭 문턱이 움직였습니다({base.max_zone_width_atr!r} != {ADOPTED_ZONE_WIDTH}) "
            "— 이 모듈의 라벨·격자 중심점을 함께 고치세요(WAN-159)."
        )
    if base.invalidation_cancel != "bar_close":
        raise AssertionError(
            f"취소 시점 기본값이 인과가 아닙니다({base.invalidation_cancel!r}) — 이 표의 전제는 "
            "인과 엔진입니다(WAN-365)."
        )


def _normalize(candidate: _Candidate) -> _Candidate:
    """관측 필드를 지운 후보 — 「비율까지 같은가」와 「후보가 같은가」를 갈라 보기 위해서."""
    return replace(candidate, zone_width_atr=None)


class CellParity(BaseModel):
    """§1a — 칸 하나의 후보 집합 대조(구간별). 이 등식이 지름길 판정의 본체다."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    straight_base: int
    shortcut_base: int
    straight_reentry: int
    shortcut_reentry: int
    base_identical: bool
    reentry_identical: bool
    width_identical: bool
    """관측 비율까지 같은가 — 다르면 두 팔이 **다른 ATR**을 본 것이다."""


def cell_parity(
    straight: Sequence[CellPayload], shortcut: Sequence[CellPayload]
) -> list[CellParity]:
    """칸·구간마다 두 팔의 후보를 **집합이 아니라 순서까지** 대조한다.

    🚨 개수만 보면 「같은 개수의 다른 셋업」이 통과한다(WAN-161 선례) — 그래서 후보 객체를
    통째로 비교한다. 재진입은 따로 센다: 파생이 컷 **앞**에서 일어나는지가 이 검산의 급소다.
    """
    by_key = {(p.symbol, p.timeframe): p for p in shortcut}
    rows: list[CellParity] = []
    for left in straight:
        right = by_key.get((left.symbol, left.timeframe))
        if right is None:
            raise AssertionError(f"지름길 팔에 없는 칸: {left.symbol} {left.timeframe}")
        for segment in sorted(left.candidates):
            a, b = left.candidates[segment], right.candidates.get(segment, ())
            ra = left.reentry_candidates.get(segment, ())
            rb = right.reentry_candidates.get(segment, ())
            rows.append(
                CellParity(
                    symbol=left.symbol,
                    timeframe=left.timeframe,
                    segment=segment,
                    straight_base=len(a),
                    shortcut_base=len(b),
                    straight_reentry=len(ra),
                    shortcut_reentry=len(rb),
                    base_identical=[_normalize(c) for c in a] == [_normalize(c) for c in b],
                    reentry_identical=[_normalize(c) for c in ra] == [_normalize(c) for c in rb],
                    width_identical=list(a) == list(b),
                )
            )
    return rows


def place(
    payloads: Sequence[CellPayload], *, start_ms: int, end_ms: int, segments: Sequence[str]
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성을 명시한다(한 표가 한 회계)."""
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


def _to_parity_row(*, scope: str, level: str, segment: BookSegment) -> ParityRow:
    row = segment.row
    pairs = segment.trades_with_placements()
    rs = [net_r(t, p) for t, p in pairs]
    return ParityRow(
        scope=scope,
        level=level,
        segment=segment.segment,
        num_cells=row.num_cells,
        num_candidates=row.num_trades,  # 배치된 거래 수는 아래 num_trades와 같은 자다.
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        mean_net_r=sum(rs) / len(rs) if rs else 0.0,
        max_drawdown=row.max_drawdown,
        peak_concurrency=row.peak_concurrency,
        liquidation_events=row.liquidation_events,
    )


def run_shortcut(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
    log: bool = True,
) -> tuple[list[ParityRow], list[CellParity]]:
    """§1a 본체 — 두 팔을 만들고 칸 단위 · 북 단위로 대조한다."""
    assert_adopted_base()
    scope = "+".join(timeframes)
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    segments = list(SEGMENT_ORDER)

    payloads: dict[str, list[CellPayload]] = {}
    for arm in PARITY_ARMS:
        if log:
            print(f"[wan376] §1a {arm} 팔 후보 생성 — {scope}", flush=True)
        payloads[arm] = arm_payloads(symbols, timeframes, arm, start=start, end=end, jobs=jobs)

    cells = cell_parity(payloads["straight"], payloads["shortcut"])
    rows: list[ParityRow] = []
    for arm in PARITY_ARMS:
        for segment in place(payloads[arm], start_ms=start_ms, end_ms=end_ms, segments=segments):
            rows.append(_to_parity_row(scope=scope, level=arm, segment=segment))
    return rows, cells


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def map_to_frame(rows: Sequence[SurvivalRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def quantiles_to_frame(rows: Sequence[QuantileRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def parity_to_frame(rows: Sequence[ParityRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def collapse_points(grid: pd.DataFrame, arm: str = "proximal") -> pd.DataFrame:
    """칸마다 「어디서 무너지나」 — 표본 게이트(20 탭) 아래로 처음 떨어지는 점.

    ⚠️ 탭은 거래의 **상한**이라 여기 안 걸려도 안전하지 않다. 여기 걸리면 **확실히** 판정
    불가라는 한 방향의 사실만 읽는다.
    """
    if grid.empty:
        return pd.DataFrame()
    sub = grid[(grid["arm"] == arm)]
    out: list[dict[str, object]] = []
    for (symbol, timeframe), cell in sub.groupby(["symbol", "timeframe"], sort=True):
        adopted = cell[cell["adopted_point"]]
        dead = cell[cell["below_sample_gate"]]
        out.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "total_taps": int(cell["total_taps"].iloc[0]),
                "adopted_survivors": int(adopted["surviving_taps"].iloc[0])
                if not adopted.empty
                else 0,
                "dead_points": int(len(dead)),
                "grid_points": int(len(cell)),
                "worst_alive": int(cell["surviving_taps"].min()),
            }
        )
    return pd.DataFrame(out)


def _render_map(grid: pd.DataFrame, quantiles: pd.DataFrame) -> list[str]:
    lines = ["## §0 — 생존 지도 (탐지 층 · 탭 상한)", "", _TAPS_NOT_TRADES, ""]
    if grid.empty:
        lines += ["_아직 안 돌렸다._", ""]
        return lines

    lines += [
        f"좌표: **{grid['symbol'].nunique()}종목 × "
        f"{grid['timeframe'].nunique()}TF** · 진입가 팔 `proximal`·`mid` "
        "(⚠️ **볼린저 팔은 지도에 안 나온다** — 진입가가 봉 안에서 정해진다).",
        "",
        "### 존폭 문턱 × 손절폭 가드 — 전 칸 합계 생존 탭 (`proximal`)",
        "",
    ]
    prox = grid[grid["arm"] == "proximal"]
    pivot = prox.pivot_table(
        index="width_label", columns="guard", values="surviving_taps", aggfunc="sum"
    )
    order = [width_label(w) for w in WIDTH_POINTS if width_label(w) in pivot.index]
    pivot = pivot.reindex(order)
    total = int(prox[prox["width_label"] == "off"]["total_taps"].sum() / len(GUARD_POINTS))
    header = "| 존폭 문턱 | " + " | ".join(f"가드 {g:.2%}" for g in pivot.columns) + " |"
    lines += [header, "| " + " | ".join(["--"] * (len(pivot.columns) + 1)) + " |"]
    for label, row in pivot.iterrows():
        cells = []
        for guard in pivot.columns:
            alive = int(row[guard])
            mark = (
                " ✅"
                if label == width_label(ADOPTED_ZONE_WIDTH) and guard == (ADOPTED_STOP_GUARD)
                else ""
            )
            cells.append(f"{alive:,} ({alive / total * 100:.1f}%){mark}" if total else str(alive))
        lines.append(f"| `{label}` | " + " | ".join(cells) + " |")
    lines += [
        "",
        f"전체 탭 **{total:,}건** 기준이고 ✅가 오늘의 채택 좌표(1.28 × 0.3%)다. "
        "`off` 행 · `0.00%` 열이 각각 「필터 끔」·「가드 끔」이다.",
        "",
    ]

    lines += ["### `mid` 팔과의 차 — 가드만 다르다(존폭 축은 두 팔이 공유)", ""]
    mid = grid[grid["arm"] == "mid"]
    lines += ["| 가드 | `proximal` 생존 | `mid` 생존 | 차 |", "| -- | -- | -- | -- |"]
    for guard in GUARD_POINTS:
        p = int(
            prox[(prox["width_label"] == "off") & (prox["guard"] == guard)]["surviving_taps"].sum()
        )
        m = int(
            mid[(mid["width_label"] == "off") & (mid["guard"] == guard)]["surviving_taps"].sum()
        )
        lines.append(f"| {guard:.2%} | {p:,} | {m:,} | {m - p:+,} |")
    lines += [
        "",
        "📌 `mid`는 진입가를 존 중앙으로 내려 **손절폭을 절반으로** 만든다 — 가드가 같은 값이면 "
        "`mid`가 훨씬 많이 잘린다. 「싸게 사기」의 대가가 가드 축에서 먼저 나타나는 자리다.",
        "",
    ]

    lines += _render_by_timeframe(grid)

    collapse = collapse_points(grid)
    dead = collapse[collapse["worst_alive"] < MIN_TRADES_PER_SYMBOL]
    lines += [
        "### 붕괴 지점 — 격자에서 표본 게이트(20 탭) 아래로 떨어지는 칸",
        "",
    ]
    if dead.empty:
        lines += [
            f"**격자의 어느 점에서도 20 탭 미만으로 떨어지는 칸이 없다**({len(collapse)}칸 전부). "
            "⚠️ 탭은 상한이라 「거래가 20건을 넘는다」는 뜻은 아니다.",
            "",
        ]
    else:
        lines += [
            "| 칸 | 전체 탭 | 채택 좌표 생존 | 죽는 점 / 전체 점 | 최악 |",
            "| -- | -- | -- | -- | -- |",
        ]
        for _, r in dead.sort_values("worst_alive").iterrows():
            lines.append(
                f"| {r['symbol']} {r['timeframe']} | {int(r['total_taps']):,} | "
                f"{int(r['adopted_survivors']):,} | "
                f"{int(r['dead_points'])}/{int(r['grid_points'])} | "
                f"{int(r['worst_alive'])} |"
            )
        lines += [
            "",
            "🚨 **이 칸들이 §1b에서 「⚠️ 판정 불가」로 빠질 후보다** — WAN-154 §3(TRX 15m 92.6% "
            "절단)·WAN-161(문턱 1.15에서 TRX 15m 12거래)의 자리와 같은 부류다.",
            "",
        ]

    if not quantiles.empty:
        lines += [
            "### 분위 지도 — 문턱은 이 위의 컷이라 어느 점이든 읽힌다",
            "",
            "| TF | 존폭÷ATR p25 | p33 | p50 | p67 | 손절폭 p25(`proximal`) | p50 "
            "| 손절폭 p50(`mid`) |",
            "| -- | -- | -- | -- | -- | -- | -- | -- |",
        ]
        for timeframe in sorted(quantiles["timeframe"].unique(), key=_tf_key):
            tf = quantiles[quantiles["timeframe"] == timeframe]

            def q(metric: str, quant: float, arm: str = "proximal", _tf: pd.DataFrame = tf) -> str:
                sel = _tf[
                    (_tf["metric"] == metric) & (_tf["quantile"] == quant) & (_tf["arm"] == arm)
                ]
                return "—" if sel.empty else f"{sel['value'].median():.3f}"

            def qp(quant: float, arm: str, _tf: pd.DataFrame = tf) -> str:
                sel = _tf[
                    (_tf["metric"] == "stop_fraction")
                    & (_tf["quantile"] == quant)
                    & (_tf["arm"] == arm)
                ]
                return "—" if sel.empty else _pct(float(sel["value"].median()))

            lines.append(
                f"| {timeframe} | {q('zone_width_atr', 0.25)} | {q('zone_width_atr', 0.33)} | "
                f"{q('zone_width_atr', 0.50)} | {q('zone_width_atr', 0.67)} | "
                f"{qp(0.25, 'proximal')} | {qp(0.50, 'proximal')} | {qp(0.50, 'mid')} |"
            )
        lines += [
            "",
            "값은 그 TF의 **종목별 분위의 중앙값**이다(칸마다 다른 분포를 한 줄로 접은 것이라 "
            "칸 단위 판단은 CSV로 할 것). 채택 문턱 **1.28**이 이 분포의 어디에 놓이는지가 "
            "★결정의 첫 입력이다.",
            "",
        ]
    return lines


def _render_by_timeframe(grid: pd.DataFrame) -> list[str]:
    """TF별로 두 축을 **따로** 본다 — 합계 표는 15m(탭의 70%)이 통째로 지배한다."""
    lines = [
        "### TF별 — 두 축이 같은 자리에서 일하지 않는다",
        "",
        "| TF | 전체 탭 | 필터만(1.28) | 가드만 0.3% (`proximal`) "
        "| 가드만 0.3% (`mid`) | 채택 좌표 |",
        "| -- | -- | -- | -- | -- | -- |",
    ]
    for timeframe in sorted(grid["timeframe"].unique(), key=_tf_key):
        tf = grid[grid["timeframe"] == timeframe]

        def alive(arm: str, label: str, guard: float, _tf: pd.DataFrame = tf) -> int:
            sel = _tf[(_tf["arm"] == arm) & (_tf["width_label"] == label) & (_tf["guard"] == guard)]
            return int(sel["surviving_taps"].sum())

        total = alive("proximal", "off", 0.0)
        if not total:
            continue

        def share(n: int, _total: int = total) -> str:
            return f"{n:,} ({n / _total * 100:.1f}%)"

        lines.append(
            f"| {timeframe} | {total:,} | "
            f"{share(alive('proximal', width_label(ADOPTED_ZONE_WIDTH), 0.0))} | "
            f"{share(alive('proximal', 'off', ADOPTED_STOP_GUARD))} | "
            f"{share(alive('mid', 'off', ADOPTED_STOP_GUARD))} | "
            f"{share(alive('proximal', width_label(ADOPTED_ZONE_WIDTH), ADOPTED_STOP_GUARD))} |"
        )
    lines += [
        "",
        "🚨 **가드는 `proximal`에서 사실상 15m 전용 축이다** — 긴 TF는 손절폭이 넓어 0.3%에 "
        "거의 안 걸린다. WAN-197이 손익으로 본 「1h 무영향」이 탭 층에서 그대로 나온다.",
        "📌 **그런데 `mid`로 내려가면 그 성질이 바뀐다** — 진입가를 존 중앙으로 내리면 손절폭이 "
        "절반이 되어 같은 가드가 1h·2h까지 물기 시작한다. **진입가 축과 가드 축이 독립이 아니라는 "
        "직접 증거**이고, 이슈가 「축을 하나만 흔들면 안 된다」고 한 자리(WAN-368의 4.5배)를 "
        "탐지 층에서 다시 본 것이다.",
        "",
    ]
    return lines


def _tf_key(timeframe: str) -> int:
    order = {"15m": 0, "1h": 1, "2h": 2, "4h": 3, "1d": 4}
    return order.get(timeframe, 99)


def _render_parity(parity: pd.DataFrame, cells: pd.DataFrame) -> list[str]:
    lines = ["## §1a — 지름길이 성립하는가", ""]
    if parity.empty:
        lines += ["_아직 안 돌렸다._", ""]
        return lines

    scope = ", ".join(sorted(parity["scope"].unique()))
    lines += [
        f"지갑 스코프: **{scope}** (북은 이어붙일 수 없다 — WAN-316).",
        "",
        "### 북 집계 — 두 팔이 비트 일치해야 한다",
        "",
        "| 구간 | 팔 | 거래 | 승률 | 거래당 net R | MDD | 최대 동시 | 청산 |",
        "| -- | -- | -- | -- | -- | -- | -- | -- |",
    ]
    for segment in SEGMENT_ORDER:
        sub = parity[parity["segment"] == segment]
        for _, r in sub.iterrows():
            lines.append(
                f"| `{segment}` | `{r['level']}` | {int(r['num_trades']):,} | "
                f"{_pct(float(r['win_rate']))} | {float(r['mean_net_r']):+.4f}R | "
                f"{_pct(float(r['max_drawdown']))} | {int(r['peak_concurrency'])} | "
                f"{int(r['liquidation_events'])} |"
            )

    diffs = _book_diffs(parity)
    lines += ["", "### 판정", ""]
    if not diffs:
        lines += [
            "📌 **지름길이 성립한다** — 두 팔의 북 집계가 전 구간에서 "
            "**최대 절대차 0.00e+00**이다.",
            "",
        ]
    else:
        lines += ["🚨 **지름길이 성립하지 않는다** — 어긋난 자리:", ""]
        lines += [f"* `{seg}` · `{col}`: {delta:.3e}" for seg, col, delta in diffs]
        lines += [""]

    if not cells.empty:
        bad_base = cells[~cells["base_identical"]]
        bad_re = cells[~cells["reentry_identical"]]
        bad_w = cells[~cells["width_identical"]]
        lines += [
            "### 칸 단위 — 개수가 아니라 **후보 객체**로 대조했다",
            "",
            f"* 대조한 (칸 × 구간): **{len(cells)}**",
            f"* base 후보가 어긋난 곳: **{len(bad_base)}**",
            f"* 재진입 후보가 어긋난 곳: **{len(bad_re)}** "
            "(🚨 이 검산이 급소다 — 파생이 컷 **앞**에서 일어나야 「빠진 셋업의 재진입」이 "
            "안 남는다)",
            f"* 관측 비율(`zone_width_atr`)까지 어긋난 곳: **{len(bad_w)}** "
            "(두 팔이 같은 ATR을 봤다는 뜻)",
            "",
        ]
        if not bad_base.empty or not bad_re.empty:
            lines += ["어긋난 칸:", ""]
            for _, r in pd.concat([bad_base, bad_re]).drop_duplicates().iterrows():
                lines.append(
                    f"* {r['symbol']} {r['timeframe']} `{r['segment']}` — base "
                    f"{int(r['straight_base'])} vs {int(r['shortcut_base'])} · 재진입 "
                    f"{int(r['straight_reentry'])} vs {int(r['shortcut_reentry'])}"
                )
            lines += [""]

    lines += [
        "### 이 답이 §1b에 주는 것",
        "",
        (
            "* **성립하면** 진입가 3팔 = **3패스**이고 문턱은 §0처럼 촘촘히 둘 수 있다."
            if not diffs
            else "* **성립하지 않으므로** 문턱 점마다 후보를 다시 만들어야 한다(**3N패스**)."
        ),
        "🚨 **컴퓨트가 공짜인 것과 통계적으로 공짜인 건 다르다** — 셀을 무한정 늘리면 「앞구간 "
        "승자를 찾는 기계」가 된다(WAN-366 §0). 점은 §0 지도가 정당화하는 만큼만이고, **점을 "
        "고르는 것은 사용자 결정**이다(★결정).",
        "",
    ]
    return lines


_BOOK_COLS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "total_return",
    "mean_net_r",
    "max_drawdown",
    "peak_concurrency",
    "liquidation_events",
)


def _book_diffs(parity: pd.DataFrame) -> list[tuple[str, str, float]]:
    """두 팔의 북 집계 차 — 0이 아닌 자리만 돌려준다(비트 일치 판정)."""
    diffs: list[tuple[str, str, float]] = []
    for segment in sorted(parity["segment"].unique()):
        sub = parity[parity["segment"] == segment]
        left = sub[sub["level"] == "straight"]
        right = sub[sub["level"] == "shortcut"]
        if left.empty or right.empty:
            continue
        for col in _BOOK_COLS:
            delta = abs(float(left[col].iloc[0]) - float(right[col].iloc[0]))
            if delta != 0.0:
                diffs.append((segment, col, delta))
    return diffs


def build_summary(
    grid: pd.DataFrame, quantiles: pd.DataFrame, parity: pd.DataFrame, cells: pd.DataFrame
) -> str:
    lines = [
        "# WAN-376 — 「존의 두께」 층을 인과 엔진에서 다시 잰다 (§0 지도 ＋ §1a 지름길)",
        "",
        "존폭 필터 **1.28**(WAN-159)과 손절폭 가드 **0.3%**(WAN-76/79)는 둘 다 **소급 취소 "
        "버그가 있던 엔진**의 표를 비교해 골라졌다. WAN-365가 그 버그를 고친 뒤 인과 엔진에서 "
        "잰 것은 **그 값에서의 켬/끔뿐**(WAN-366/368)이고 **눈금을 흔들어 본 적이 없다.**",
        "",
        "🚨 **이 문서는 §0 ＋ §1a까지다** — ★결정(§1b 격자 점 확정)은 **사용자 몫**이고, "
        "§1b·§2는 별도 PR이다.",
        "",
        "⚠️ **측정 전용** — `ConfluenceParams()`·`LeverageBookParams()` 기본값을 하나도 안 "
        "바꿨다. 채택은 **재-베이스라인 = 사용자 결정**이고 개발자 임의 착수 금지다.",
        "",
    ]
    lines += _render_map(grid, quantiles)
    lines += _render_parity(parity, cells)
    lines += [
        "## 경고",
        "",
        "* 전부 `baseline`(닿으면 체결) 렌즈 위 값이고 체결 보수화(`pen_5bp`)는 안 쟀다.",
        "* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 표는 *두 자의 눈금이 "
        "맞나*를 묻지 *진입 규칙이 무작위와 구분되나*를 묻지 않는다.",
        "* 6년 MDD는 2018·2020-03 폭락을 **포함하지 않는** 창이라 천장이 아니라 **바닥선**이다.",
        "* **판단은 북에서**(WAN-341) · **핀 하나도 없다**(WAN-305) · 실거래 보류 유지"
        "(`ALPHABLOCK_LIVE_TRADING=false`).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-376 §0 생존 지도 + §1a 지름길 검산")
    parser.add_argument("--part", choices=("census", "shortcut", "summary"), default="census")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    return parser.parse_args(argv)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    if existing.empty:
        return fresh
    if fresh.empty:
        return existing
    merged = pd.concat([existing, fresh], ignore_index=True)
    return merged.drop_duplicates(subset=list(keys), keep="last").reset_index(drop=True)


_CELLS_CSV_PATH = REPORTS_DIR / "wan376_shortcut_cells.csv"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    if args.part == "census" and not args.from_csv:
        grid_rows, quantile_rows = run_map(symbols, timeframes, start=args.start, end=args.end)
        grid = _merge(_read(MAP_CSV_PATH), map_to_frame(grid_rows), MAP_KEYS)
        quantiles = _merge(
            _read(QUANTILE_CSV_PATH), quantiles_to_frame(quantile_rows), QUANTILE_KEYS
        )
        grid.to_csv(MAP_CSV_PATH, index=False)
        quantiles.to_csv(QUANTILE_CSV_PATH, index=False)
        print(f"[wan376] §0 적재: {MAP_CSV_PATH} · {QUANTILE_CSV_PATH}", flush=True)
    elif args.part == "shortcut" and not args.from_csv:
        parity_rows, cell_rows = run_shortcut(
            symbols, timeframes, start=args.start, end=args.end, jobs=args.jobs
        )
        parity = _merge(_read(PARITY_CSV_PATH), parity_to_frame(parity_rows), PARITY_KEYS)
        cells = pd.DataFrame([r.model_dump() for r in cell_rows])
        parity.to_csv(PARITY_CSV_PATH, index=False)
        cells.to_csv(_CELLS_CSV_PATH, index=False)
        print(f"[wan376] §1a 적재: {PARITY_CSV_PATH} · {_CELLS_CSV_PATH}", flush=True)

    grid = _read(MAP_CSV_PATH)
    quantiles = _read(QUANTILE_CSV_PATH)
    parity = _read(PARITY_CSV_PATH)
    cells = _read(_CELLS_CSV_PATH)
    SUMMARY_PATH.write_text(build_summary(grid, quantiles, parity, cells), encoding="utf-8")
    print(f"[wan376] 요약: {SUMMARY_PATH}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
