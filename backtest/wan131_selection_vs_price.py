"""WAN-131: 볼린저의 「선별 대 가격」 분리 — 유일한 플러스 부품이 좋은 존을 고르는가,
그냥 싸게 사는가.

WAN-114/145/151 사다리가 세 번 확인한 것: 진입 규칙 셋(재탭 노출 · RSI 게이트 · 볼린저)
중 **값을 더하는 건 볼린저 하나뿐**이고 그것도 15m에서만이다(분리 존 OOS 증분 15m
+17.65%p vs 1h −3.92%p, WAN-151). 그런데 **그 기여가 「선별」인지 「가격」인지 아직 아무도
못 갈랐다**:

* **선별 가설** — 밴드 조건이 좋은 존과 나쁜 존을 구분한다(= 진짜 규칙, 알파 후보).
* **가격 가설** — 밴드가 진입가를 낮춰 1R이 줄고 고정 1.5R 익절이 가까워진다(= 규칙이
  아니라 **주문 가격 정책**).

WAN-124 §7-2가 이걸 후속으로 남겼다: *"볼린저의 선별과 가격은 같은 연산이라 코드에서
분리되지 않는다."* 이 모듈이 옵트인 파라미터(`DeviationFilterParams.select_only`)로 그
연산을 둘로 쪼갠다.

## 3팔 + 채택 앵커 — WAN-126 3팔 설계를 볼린저 축에 적용

밴드의 세 상태를 사다리로 켠다(게이트는 `first_tap_free`로 고정 — 그래야 선별/가격이
게이트 변화와 섞이지 않는다. 채택 게이트(`unconditional`)는 넷째 단이 따로 얹는다):

| 팔 | 무엇이 켜지나 | `deviation_filter` | 진입가 | wan151 앵커 |
| -- | -- | -- | -- | -- |
| `A` | 볼린저 off | `None` | 존 근단 | `L1` |
| `B` | 볼린저 **선별만** | 볼린저 `select_only=True` | 존 근단 | (신규) |
| `C` | 볼린저 선별+가격 | 볼린저(현행) | 밴드 재산정가 | `L2` |
| `Cadopt` | + 게이트 제거 (= 채택 기본값) | 볼린저(현행) | 밴드 재산정가 | `L2u` |

**분해**(게이트 고정 `first_tap_free` 아래):

* **선별 = B − A** — 밴드가 좋은 존을 고른 몫(진입가는 A·B 둘 다 존 근단이라 가격 효과 0).
* **가격 = C − B** — 밴드가 진입가를 낮춘 몫(선별은 B·C 둘 다 같아 셋업 집합이 동일).
* 볼린저 총합 = C − A = wan151의 `L1→L2` 증분(저장소가 인용하는 그 수). 선별+가격이다.
* 게이트 제거 = Cadopt − C = wan151의 `L2→L2u`(WAN-124가 이미 잰 것 · 참고로만).

🚨 **왜 B와 C의 선별이 같은가(= C−B가 순수 가격인 근거)**: `deviation_entry_price`의
규칙 1(밴드가 존보다 관대)은 두 팔 모두 존 근단, 규칙 3(밴드가 존 전체보다 불리)은 두 팔
모두 **기각**이다. 갈리는 건 규칙 2(밴드가 존 안)뿐이고, 거기서 C는 밴드가·B는 존 근단이다.
봉내 라이브에서 「선별」(규칙 3 기각)은 `order_rested=False`(주문이 안 걸림)로 나타나는데
B·C가 규칙 3을 **동일하게** 판정하므로 **`eligible_setups`(주문이 걸린 셋업 수)가 두 팔
정확히 같다** — 실측 검산(BTC 1h OOS 둘 다 266). 즉 선별은 A→B에서 끝나고 B→C는 규칙 2
셋업의 **진입가 재산정**뿐이다. ⚠️ 단 재산정은 `num_filled`를 바꾼다(밴드가는 존 근단보다
닿기 어렵다 → C가 덜 체결) — 그건 다른 셋업을 매매한 게 아니라 **가격 효과의 체결률
성분**이다. 즉 지정가 엔진에서 「가격」은 진입가 개선과 체결률 감소를 묶는다(WAN-96/124의
체결 차원 「선별 대 가격 못 가름」과 같은 자리). 회귀 테스트가 `eligible_setups` 동등성을
동작으로 고정한다(`tests/test_wan131_selection_vs_price.py`).

## 존폭 필터는 끈다 (사용자 결정 2026-07-22)

세 팔 모두 `max_zone_width_atr=None`(필터 끔)으로 **명시**한다. 근거(사용자 결정):
(1) 볼린저의 성질을 분리하려면 그것 **하나만** 흔들어야 한다. (2) 🚨 **존폭 필터도 「좁은
존 고르기」라 볼린저의 「가격 효과」와 겹친다** — 좁은 존 → 짧은 1R → 가까운 익절인데,
필터를 먼저 켜면 그 이점을 필터가 가져가 버려 볼린저의 몫이 흐려진다(WAN-152가 존폭 축
승률 상승의 과반이 「기하」임을 이미 보였다). (3) 완료기준의 검산(A ≡ wan151 `L1`)이 필터
꺼진 값이라 필터 끔에서만 성립한다. **⚠️ 이 표는 「필터 미적용 격자」다.**

## 밴드·존은 오늘 정본 — 핀을 안 건다

밴드는 `intrabar_live`(WAN-132), 존은 `combine_obs=False`(분리, WAN-149) — 둘 다 채택
기본값이라 고정하지 않는다(따라간다). 그래서 A·C·Cadopt가 **필터 끔 시절 wan151 CSV의
`L1`·`L2`·`L2u`와 비트 일치**한다(그 CSV도 필터 끔 · 분리 존 · 새 밴드다). 검산이 이걸 낸다.

## 격자 · 렌즈

6심볼 · 15m·1h 병기(WAN-107) · 못 박은 창 `2023-07-14~2026-07-15`(WAN-111) · IS/OOS ·
렌즈 `baseline` 단독(WAN-128).

## 재현

```
uv run python -m backtest.wan131_selection_vs_price --tf 1h --jobs 6
uv run python -m backtest.wan131_selection_vs_price --tf 15m --jobs 6 --append
uv run python -m backtest.wan131_selection_vs_price --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan151_split_zone_null import ADOPTED_OB_PARAMS
from strategy.models import ConfluenceParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CSV = REPORTS_DIR / "wan131_selection_vs_price.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan131_selection_vs_price_summary.md"
#: 검산 상대 = 분리 존 사다리(WAN-151). A≡L1 · C≡L2 · Cadopt≡L2u.
ABLATION_CSV = REPORTS_DIR / "wan151_entry_ablation.csv"

#: 못 박은 창 · 6심볼 · 공동 작업 TF — wan151과 같은 축이라야 검산이 성립한다.
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"
ALL_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "TRXUSDT",
)
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

OFFICIAL_LENS = harness.BASELINE_FILL.name
SEGMENT_ORDER: tuple[str, ...] = (harness.SEGMENT_IS, harness.SEGMENT_OOS)

#: 검산에서 「같다」로 볼 부동소수 오차 상한(wan151과 같은 규약).
FLOAT_NOISE = 1e-12


# --------------------------------------------------------------------------- #
# 팔 정의
# --------------------------------------------------------------------------- #

#: 채택 볼린저(SMA20 ± 2σ · `intrabar_live`) — C·Cadopt가 쓰는 진입가 재산정 밴드.
_ADOPTED_BOLLINGER = ConfluenceParams().deviation_filter
assert _ADOPTED_BOLLINGER is not None  # 채택 기본값에 볼린저가 켜져 있다(토대).
#: B팔 밴드 = 같은 볼린저지만 **선별에만** 쓴다(진입가는 존 근단). WAN-131 옵트인.
_SELECT_ONLY_BOLLINGER = _ADOPTED_BOLLINGER.model_copy(update={"select_only": True})

#: 게이트는 `first_tap_free`로 고정한다 — 선별/가격이 게이트 변화와 섞이지 않게. 채택
#: 게이트(`unconditional`)는 `Cadopt`가 따로 얹는다(= wan151 `L2→L2u`).
_HELD_GATE = "first_tap_free"


@dataclass(frozen=True)
class Arm:
    """3팔 + 채택 앵커 한 팔 — 채택 기본값 대비 덮어쓸 필드로 정의한다(Rung과 같은 규약)."""

    name: str
    label: str
    updates: Mapping[str, object]
    #: 이 팔과 비트 일치해야 하는 wan151 사다리 단(신규 팔이면 None).
    ablation_level: str | None


ARMS: tuple[Arm, ...] = (
    Arm(
        name="A",
        label="볼린저 off (존 근단)",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": _HELD_GATE,
            "deviation_filter": None,
        },
        ablation_level="L1",
    ),
    Arm(
        name="B",
        label="볼린저 선별만 (존 근단)",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": _HELD_GATE,
            "deviation_filter": _SELECT_ONLY_BOLLINGER,
        },
        ablation_level=None,
    ),
    Arm(
        name="C",
        label="볼린저 선별+가격 (밴드가)",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": _HELD_GATE,
            "deviation_filter": _ADOPTED_BOLLINGER,
        },
        ablation_level="L2",
    ),
    Arm(
        name="Cadopt",
        label="+ 게이트 제거 (= 채택 기본값 · 필터 끔)",
        updates={"retap_mode": "every_tap"},  # 게이트·밴드·나머지는 채택 기본값을 따라간다.
        ablation_level="L2u",
    ),
)
ARMS_BY_NAME: dict[str, Arm] = {a.name: a for a in ARMS}
ARM_ORDER: tuple[str, ...] = tuple(a.name for a in ARMS)

#: 분해 — (라벨, 이전 팔, 이후 팔). 순서가 곧 표의 순서다.
DECOMPOSITION: tuple[tuple[str, str, str], ...] = (
    ("선별 (B−A)", "A", "B"),
    ("가격 (C−B)", "B", "C"),
    ("볼린저 총합 (C−A)", "A", "C"),
    ("게이트 제거 (Cadopt−C)", "C", "Cadopt"),
)


def arm_params(arm: Arm) -> ConfluenceParams:
    """한 팔의 `ConfluenceParams` — **존폭 필터는 끄고**(사용자 결정) 밴드·존은 안 고정한다.

    밴드(`intrabar_live`)·존(`combine_obs=False`)은 채택 기본값이라 그대로 따라가고,
    `max_zone_width_atr`만 명시적 `None`으로 끈다 — 그래야 A·C·Cadopt가 필터 끔 시절
    wan151 사다리(`L1`·`L2`·`L2u`)와 비트 일치한다(검산). 그리고 이 표가 「볼린저 축」을
    격리한다(존폭 선별이 안 섞인다).
    """
    base = ConfluenceParams(max_zone_width_atr=None).model_copy(update=dict(arm.updates))
    return harness.build_params(entry_mode="zone_limit", max_zone_width_atr=None, base=base)


def describe_engine() -> str:
    p = arm_params(ARMS_BY_NAME["Cadopt"])
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_mode={p.rsi_mode}, "
        f"rsi_gate_mode={p.rsi_gate_mode}, retap_mode={p.retap_mode}, "
        f"zone_limit_offset_bps={p.zone_limit_offset_bps}, take_profit_r={p.take_profit_r}, "
        f"band_bar={band}, max_zone_width_atr={p.max_zone_width_atr}, "
        f"combine_obs={ADOPTED_OB_PARAMS.combine_obs}"
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


class ArmRow(harness.RunRow):
    """격자 한 셀 — harness 공용 좌표·지표에 팔 라벨만 얹는다(wan151과 같은 모델)."""

    arm: str


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    arms: tuple[str, ...]


def run_cell(task: _Task, *, log: bool = True) -> list[ArmRow]:
    """한 (심볼, TF)의 IS/OOS × 팔을 돈다.

    구간마다 오더블록을 **한 번만 탐지해 팔 전체가 공유한다** — 탐지는 컨플루언스
    파라미터와 무관하므로 결과가 바뀌지 않고, 그 공유가 이 표의 전제다: 모든 팔이 **같은
    존 집합**을 보고 다른 건 오직 밴드를 어떻게 쓰냐(off/선별/선별+가격)다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []
    cfg = harness.build_config(task.timeframe)
    rows: list[ArmRow] = []
    for segment in harness.segments_for(oos=True):
        if segment.name not in SEGMENT_ORDER:
            continue
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        # 🚨 `LEGACY_OB_PARAMS`(병합)를 주지 않는다 = 채택 기본값(분리 존, WAN-149).
        ob_result = harness.detect_order_blocks(window, ADOPTED_OB_PARAMS)
        for arm_name in task.arms:
            params = arm_params(ARMS_BY_NAME[arm_name])
            outcome = harness.run_once(window, params=params, cfg=cfg, order_block_result=ob_result)
            row = harness.build_row(
                outcome,
                window,
                segment=segment,
                params=params,
                fill_name=OFFICIAL_LENS,
                order_block=ADOPTED_OB_PARAMS,
            )
            rows.append(ArmRow(arm=arm_name, **row.model_dump()))
        if log:
            print(
                f"[wan131] {task.symbol} {task.timeframe} {segment.name}: "
                f"{len(window.htf_df)}봉 · 존 {len(ob_result.order_blocks)}개 → "
                f"{len(task.arms)}팔 완료",
                flush=True,
            )
    return rows


def _run_task_logged(task: _Task) -> list[ArmRow]:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    arms: Sequence[str] = ARM_ORDER,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[ArmRow]:
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            arms=tuple(arms),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_cell(task, log=log)]
    rows: list[ArmRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[ArmRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[ArmRow]:
    frame = pd.read_csv(path)
    return [ArmRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[ArmRow]) -> pd.DataFrame:
    frame = rows_to_frame(rows)
    if frame.empty:
        return frame
    return (
        frame.groupby(["arm", "timeframe", "segment", "symbol"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            num_trades=("num_trades", "mean"),
            eligible_setups=("eligible_setups", "mean"),
            fill_rate=("fill_rate", "mean"),
            mean_r=("mean_r", "mean"),
        )
        .reset_index(drop=True)
    )


def arm_summary(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """팔별 심볼평균 — `positive`(플러스 심볼 수)를 평균 옆에 둔다(WAN-111 규칙)."""
    if symbol_frame.empty:
        return symbol_frame
    grouped = symbol_frame.groupby(["timeframe", "segment", "arm"], as_index=False).agg(
        total_return=("total_return", "mean"),
        positive=("total_return", lambda s: float((s > 0).sum())),
        symbols=("total_return", "count"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        num_trades=("num_trades", "mean"),
        eligible_setups=("eligible_setups", "mean"),
        fill_rate=("fill_rate", "mean"),
        mean_r=("mean_r", "mean"),
    )
    return _sorted(grouped)


def decomposition(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """분해 델타 — 심볼별로 짝지어 뺀 뒤 평균(선별=B−A · 가격=C−B · …).

    거래 수 차이(`delta_trades_pct`)를 수익 델타 옆에 둔다 — 「선별인가 가격인가」를
    읽으려면 필수이고(WAN-126 함정), 「가격」팔(C−B)의 거래 수 차가 임계치를 넘으면
    오염된 것이다.
    """
    records: list[dict[str, object]] = []
    if symbol_frame.empty:
        return pd.DataFrame(records)
    for (timeframe, segment), view in symbol_frame.groupby(["timeframe", "segment"], sort=False):
        pivots = {
            column: view.pivot_table(index="symbol", columns="arm", values=column)
            for column in ("total_return", "num_trades", "max_drawdown", "win_rate")
        }
        returns = pivots["total_return"]
        for label, prev, cur in DECOMPOSITION:
            if prev not in returns.columns or cur not in returns.columns:
                continue
            delta = (returns[cur] - returns[prev]).dropna()
            if delta.empty:
                continue
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "effect": label,
                    "delta_return": float(delta.mean()),
                    "symbols_up": float((delta > 0).sum()),
                    "symbols": float(len(delta)),
                    "delta_trades_pct": _relative(pivots["num_trades"], prev, cur),
                    "delta_win_rate": _mean_delta(pivots["win_rate"], prev, cur),
                    "delta_mdd": _mean_delta(pivots["max_drawdown"], prev, cur),
                }
            )
    return _sorted(pd.DataFrame(records))


def decomposition_excluding(symbol_frame: pd.DataFrame, *, exclude: str = "ETH") -> pd.DataFrame:
    """심볼 하나를 뺀 분해 — leave-one-out(이 저장소 15m 플러스는 반복적으로 ETH 하나)."""
    if symbol_frame.empty:
        return symbol_frame
    view = symbol_frame[~symbol_frame["symbol"].str.startswith(exclude)]
    return decomposition(view)


def _mean_delta(pivot: pd.DataFrame, prev: str, cur: str) -> float:
    if prev not in pivot.columns or cur not in pivot.columns:
        return float("nan")
    return float((pivot[cur] - pivot[prev]).dropna().mean())


def _relative(pivot: pd.DataFrame, prev: str, cur: str) -> float:
    if prev not in pivot.columns or cur not in pivot.columns:
        return float("nan")
    before = float(pivot[prev].dropna().mean())
    after = float(pivot[cur].dropna().mean())
    if not before:
        return float("nan")
    return (after - before) / before


_ORDERINGS: dict[str, tuple[str, ...]] = {
    "segment": SEGMENT_ORDER,
    "arm": ARM_ORDER,
    "timeframe": DEFAULT_TIMEFRAMES,
    "effect": tuple(label for label, _, _ in DECOMPOSITION),
}


def _sorted(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    helpers: list[str] = []
    for key, order in _ORDERINGS.items():
        if key not in out.columns:
            continue
        helper = f"_order_{key}"
        out[helper] = out[key].map({name: i for i, name in enumerate(order)})
        helpers.append(helper)
    return out.sort_values(helpers).drop(columns=helpers).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 검산 — A·C·Cadopt ≡ wan151 사다리
# --------------------------------------------------------------------------- #


def crosscheck_against_ablation(
    rows: Sequence[ArmRow], ablation_csv: Path = ABLATION_CSV
) -> tuple[str, float | None]:
    """A≡`L1` · C≡`L2` · Cadopt≡`L2u`(wan151 분리 존 사다리)를 대조한다.

    같은 창·같은 존·같은 밴드·필터 끔을 두 모듈로 돌린 값이라 차이가 0이어야 한다 —
    「다른 엔진을 돌린 게 아님」의 증명(완료기준). 반환은 (문장, 최대 절대차).
    """
    if not ablation_csv.exists():
        return (f"⚠️ 검산 불가 — 사다리 원자료(`{ablation_csv}`)가 없다.", None)
    ablation = pd.read_csv(ablation_csv)
    anchors = {a.name: a.ablation_level for a in ARMS if a.ablation_level is not None}
    diffs: list[float] = []
    matched = 0
    for row in rows:
        level = anchors.get(row.arm)
        if level is None:
            continue
        match = ablation[
            (ablation["symbol"] == row.symbol)
            & (ablation["timeframe"] == row.timeframe)
            & (ablation["segment"] == row.segment)
            & (ablation["level"] == level)
        ]
        if match.empty:
            continue
        matched += 1
        diffs.append(abs(row.total_return - float(match.iloc[0]["total_return"])))
        diffs.append(float(abs(row.num_trades - float(match.iloc[0]["num_trades"]))))
    if not matched:
        return ("⚠️ 검산 불가 — 두 표에 공통 좌표가 없다.", None)
    worst = max(diffs)
    if worst == 0:
        verdict = "차이 0"
    elif worst <= FLOAT_NOISE:
        verdict = f"차이 {worst:.2e}(부동소수 오차 이내 — 문턱 {FLOAT_NOISE:.0e})"
    else:
        verdict = f"**최대 차이 {worst:.2e} — 어긋난다**"
    return (
        f"`A`≡`L1` · `C`≡`L2` · `Cadopt`≡`L2u`(wan151 분리 존 사다리) {matched}행 대조 — "
        f"수익·거래 수 {verdict}. 세 앵커가 필터 끔·분리 존·새 밴드에서 비트 일치하면 "
        "이 표가 오늘 엔진을 돌린 것이다.",
        worst,
    )


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _effect_at(steps: pd.DataFrame, effect: str, timeframe: str, segment: str) -> pd.Series | None:
    view = steps[
        (steps["effect"] == effect)
        & (steps["timeframe"] == timeframe)
        & (steps["segment"] == segment)
    ]
    return None if view.empty else view.iloc[0]


#: 「둘 다 0」으로 볼 크기 상한(%p). 선별·가격 둘 다 이보다 작으면 (c).
VERDICT_EPSILON = 0.01


def verdict_lines(steps: pd.DataFrame) -> list[str]:
    """판정 (a)/(b)/(c) — 선별(B−A)과 가격(C−B)의 **크기**로 낸다(부호 무관).

    숫자는 전부 프레임에서 읽는다(문장에 박으면 재실행 뒤 조용히 거짓말을 한다). 판정은
    **크기 비교**다 — 볼린저 총합(C−A)이 어느 부품에 실려 있냐를 묻지, 부호를 묻지 않는다
    (1h처럼 총합이 음수여도 「무엇이 그 음수를 만드나」는 유효한 질문이다):

    * **(a) 선별이 실재** — 선별이 가격보다 크고(|B−A| > |C−B|), OOS 플러스 · IS 같은 부호.
    * **(b) 가격이 대부분** — 가격이 선별보다 크다(|C−B| ≥ |B−A|). 볼린저 기여의 대부분이
      진입가 재산정에서 온다 = 「좋은 존을 고른 게 아니라 싸게 산 것」.
    * **(c) 둘 다 0** — 선별·가격 모두 `VERDICT_EPSILON` 미만(또는 선별이 크지만 불안정).
    """
    lines: list[str] = []
    if steps.empty:
        return ["행이 없다 — 판정 불가."]
    sel_label, price_label = DECOMPOSITION[0][0], DECOMPOSITION[1][0]
    verdicts: dict[str, str] = {}
    for timeframe in DEFAULT_TIMEFRAMES:
        sel_oos = _effect_at(steps, sel_label, timeframe, harness.SEGMENT_OOS)
        sel_is = _effect_at(steps, sel_label, timeframe, harness.SEGMENT_IS)
        price_oos = _effect_at(steps, price_label, timeframe, harness.SEGMENT_OOS)
        if sel_oos is None or sel_is is None or price_oos is None:
            continue
        s_oos = float(sel_oos["delta_return"])
        s_is = float(sel_is["delta_return"])
        p_oos = float(price_oos["delta_return"])
        mag_s, mag_p = abs(s_oos), abs(p_oos)
        sign_stable = (s_oos > 0) == (s_is > 0)
        total = mag_s + mag_p
        price_share = mag_p / total if total else 0.0
        if mag_s < VERDICT_EPSILON and mag_p < VERDICT_EPSILON:
            tag = "(c) 둘 다 0"
        elif mag_p >= mag_s:
            tag = "(b) 가격이 대부분"
        elif s_oos > 0 and sign_stable:
            tag = "(a) 선별이 실재"
        else:
            tag = "(c) 선별 우세하나 불안정"
        verdicts[timeframe] = tag
        lines.append(
            f"- **{timeframe}**: 선별(B−A) OOS **{s_oos * 100:+.2f}%p**"
            f"(IS {s_is * 100:+.2f}%p, {'같은 부호' if sign_stable else '**부호 뒤집힘**'}) · "
            f"가격(C−B) OOS **{p_oos * 100:+.2f}%p** → **가격이 {price_share * 100:.0f}%** → "
            f"**{tag}**"
        )
    if verdicts:
        uniq = set(verdicts.values())
        if len(uniq) == 1:
            lines.append(f"\n**종합 판정: {next(iter(uniq))}** (두 작업 TF 일치).")
        else:
            joined = " · ".join(f"{tf} {tag}" for tf, tag in verdicts.items())
            lines.append(f"\n**종합 판정: TF에 갈린다 — {joined}.**")
    return lines


def selection_identity(symbol_frame: pd.DataFrame) -> tuple[str, float | None]:
    """B·C의 **eligible_setups(체결 가능 셋업 = 주문이 걸린 셋업)** 가 같은지 대조한다.

    🚨 이것이 「C−B가 순수 가격」의 진짜 근거다(WAN-126 거래 수 게이트의 봉내 라이브 판).
    봉내 밴드에서 「선별」(규칙 3 기각)은 `order_rested=False`로 나타나고, B·C는 규칙 3을
    **동일하게** 판정하므로 **eligible이 같아야 한다**. 같으면: 선별은 A→B에서 끝났고
    B→C는 진입가 재산정(가격)뿐이다. 재산정이 `num_filled`를 바꾸는 것(밴드가는 닿기 더
    어렵다)은 **가격 효과의 체결률 성분**이지 다른 셋업을 매매한 게 아니다.
    """
    if symbol_frame.empty:
        return ("⚠️ 판정 불가 — 행이 없다.", None)
    pivot = symbol_frame.pivot_table(
        index=["timeframe", "segment", "symbol"], columns="arm", values="eligible_setups"
    )
    if "B" not in pivot.columns or "C" not in pivot.columns:
        return ("⚠️ 판정 불가 — B·C 팔이 없다.", None)
    diff = (pivot["B"] - pivot["C"]).dropna().abs()
    if diff.empty:
        return ("⚠️ 판정 불가 — 공통 좌표가 없다.", None)
    worst = float(diff.max())
    if worst == 0:
        verdict = "**모든 셀 차이 0**"
    else:
        verdict = f"**최대 차이 {worst:.2f} — 선별이 갈린다(C−B가 순수 가격이 아님)**"
    return (
        f"B·C의 `eligible_setups`(주문이 걸린 셋업) {len(diff)}셀 대조 — {verdict}. "
        "같으면 선별은 A→B에서 끝났고 B→C는 진입가 재산정(가격)뿐이다 — 봉내 라이브에서 "
        "「선별 = 규칙 3 기각」은 두 팔이 동일하게 판정한다.",
        worst,
    )


def contamination_lines(steps: pd.DataFrame, symbol_frame: pd.DataFrame) -> list[str]:
    """선별 identity + 「가격」팔의 체결률 성분을 명시한다(WAN-126 게이트의 봉내 라이브 판)."""
    lines: list[str] = []
    identity, _ = selection_identity(symbol_frame)
    lines.append(identity)
    lines.append("")
    price_label = DECOMPOSITION[1][0]
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in SEGMENT_ORDER:
            record = _effect_at(steps, price_label, timeframe, segment)
            if record is None:
                continue
            frac = float(record["delta_trades_pct"])
            lines.append(
                f"- **{timeframe} {segment}** 가격(C−B) 체결 수 변화 {frac * 100:+.2f}% "
                "(재산정이 밴드가를 닿기 어렵게 만든 몫 — 가격 효과의 체결률 성분)"
            )
    lines.append(
        "\n⚠️ 즉 「가격」(C−B)은 **진입가 개선 + 체결률 감소**를 묶는다 — 지정가 엔진에서 "
        "재산정은 이 둘을 분리할 수 없다(WAN-96/124의 체결 차원 「선별 대 가격 못 가름」과 "
        "같은 자리). 다만 **선별(A→B)은 깨끗하다**: 같은 진입가(존 근단)에서 셋업만 걸러낸다."
    )
    return lines


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame) -> str:
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


_PERCENT_COLUMNS = (
    "total_return",
    "win_rate",
    "max_drawdown",
    "fill_rate",
    "delta_return",
    "delta_trades_pct",
    "delta_mdd",
    "delta_win_rate",
)


def _rounded(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in _PERCENT_COLUMNS:
        if col in out.columns:
            out[col] = (out[col].astype(float) * 100).round(2)
    for col in ("num_trades", "mean_r", "positive", "symbols", "symbols_up"):
        if col in out.columns:
            out[col] = out[col].astype(float).round(2)
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(lambda s: str(s).split("/")[0])
    return out.astype(object).where(out.notna(), "—")


def arm_table() -> str:
    """팔 정의를 표로 — 문서와 코드가 갈라지지 않게 실제 파라미터를 찍는다."""
    records = []
    for arm in ARMS:
        p = arm_params(arm)
        dev = p.deviation_filter
        band = "off" if dev is None else f"볼린저({'선별만' if dev.select_only else '선별+가격'})"
        records.append(
            {
                "arm": arm.name,
                "label": arm.label,
                "rsi_gate_mode": p.rsi_gate_mode,
                "deviation_filter": band,
                "wan151 앵커": arm.ablation_level or "(신규)",
            }
        )
    return _md_table(pd.DataFrame(records))


_SUMMARY_VIEW = (
    "timeframe",
    "segment",
    "arm",
    "total_return",
    "positive",
    "symbols",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "eligible_setups",
    "fill_rate",
    "mean_r",
)

_STEP_VIEW = (
    "timeframe",
    "segment",
    "effect",
    "delta_return",
    "symbols_up",
    "symbols",
    "delta_trades_pct",
    "delta_win_rate",
    "delta_mdd",
)


def build_summary_markdown(
    rows: Sequence[ArmRow], *, csv_path: Path, ablation_csv: Path = ABLATION_CSV
) -> str:
    symbol_frame = per_symbol(rows)
    summary = arm_summary(symbol_frame)
    steps = decomposition(symbol_frame)
    steps_ex_eth = decomposition_excluding(symbol_frame)
    crosscheck, _ = crosscheck_against_ablation(rows, ablation_csv)

    lines = [
        "# WAN-131 — 볼린저의 「선별 대 가격」 분리",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 × 2TF(15m·1h) × IS/OOS "
        "격자, 렌즈는 **`baseline` 단독**(WAN-128). **⚠️ 존폭 필터 미적용 격자**"
        "(`max_zone_width_atr=None`, 사용자 결정 2026-07-22 — 볼린저 축을 격리한다).",
        "",
        f"재현: `uv run python -m backtest.wan131_selection_vs_price` (요약만: `--from-csv`). "
        f"원자료: `{csv_path}`.",
        "",
        "## 이 리포트가 검정한 엔진",
        "",
        f"채택 기본값(필터 끔) — `{describe_engine()}`. 밴드(`intrabar_live`)·존"
        "(`combine_obs=False`)은 고정하지 않는다(따라간다) — 그래서 A·C·Cadopt가 필터 끔 "
        "시절 wan151 사다리와 비트 일치한다.",
        "",
        "## 팔 정의",
        "",
        arm_table(),
        "",
        "**분해**(게이트 고정 `first_tap_free`): 선별 = **B−A**(진입가는 둘 다 존 근단) · "
        "가격 = **C−B**(선별은 둘 다 같아 셋업 동일). 볼린저 총합 = C−A = wan151 `L1→L2`. "
        "게이트 제거 = Cadopt−C = wan151 `L2→L2u`(참고).",
        "",
        "## 검산",
        "",
        crosscheck,
        "",
        "## 판정 — 공식 렌즈(`baseline`)",
        "",
        *verdict_lines(steps),
        "",
        "### 함정 1 — 거래 수 오염 (WAN-126 게이트, 봉내 라이브 판)",
        "",
        *contamination_lines(steps, symbol_frame),
        "",
        "### 함정 2 — 심볼 편중 (ETH 제외 분해)",
        "",
        "⚠️ 「엣지 없음」 계열이 반복 확인한 것: **평균은 심볼 하나가 만든다.**",
        "",
        _md_table(_rounded(steps_ex_eth[list(_STEP_VIEW)]))
        if not steps_ex_eth.empty
        else "행이 없다.",
        "",
        "## 1. 팔 본표 — 팔별 심볼평균",
        "",
        _md_table(_rounded(summary[list(_SUMMARY_VIEW)])) if not summary.empty else "행이 없다.",
        "",
        "## 2. 분해 델타 — 선별 · 가격 · 총합 · 게이트",
        "",
        "`delta_return` = 심볼별로 짝지어 뺀 뒤 평균한 수익률 차 / `symbols_up` = 그 방향으로 "
        "움직인 심볼 수. 심볼 6개는 서로 상관된 표본이라(크립토 베타) `symbols_up`은 "
        "**유의성이 아니라 방향의 일관성**만 말한다.",
        "",
        _md_table(_rounded(steps[list(_STEP_VIEW)])) if not steps.empty else "행이 없다.",
        "",
        "## ⚠️ 인용 금지 경고",
        "",
        "- **「엣지 찾았다」로 인용 금지** — 판정이 (a)여도 전부 `baseline`(낙관) 렌즈 위의 "
        "값이고, 「엣지 없음」(WAN-84/88/111/114/124/151)은 *진입 규칙이 무작위와 구분되는가*"
        "라는 **다른 질문**이다. 이 표는 볼린저의 **기여의 성질**만 가른다.",
        "- **OOS 플러스는 여전히 ETH가 만들 수 있다** — 위 「함정 2」와 leave-one-out을 함께 "
        "읽을 것(WAN-151: 채택 팔 15m OOS +7.84% → ETH 제외 +0.01%).",
        "- **이 표는 「필터 미적용 격자」다** — 존폭 필터(WAN-159 채택 기본값 1.28)를 껐다. "
        "필터를 켠 채 볼린저가 값을 더하는지(겹쳐도 남나)는 **별도 이슈**다(사용자 결정).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-131 볼린저 선별 대 가격 분리")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--arms", type=str, default=",".join(ARM_ORDER))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--ablation-csv", type=Path, default=ABLATION_CSV, help="검산 대상 wan151")
    parser.add_argument("--append", action="store_true", help="기존 CSV에 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="요약만 재생성")
    args = parser.parse_args(argv)

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan131] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            arms=tuple(x.strip() for x in str(args.arms).split(",") if x.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        if args.append and out_csv.exists():
            rows = rows_from_csv(out_csv) + list(rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
        print(f"[wan131] {len(rows)}행 → {out_csv}")

    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(
        build_summary_markdown(rows, csv_path=out_csv, ablation_csv=Path(args.ablation_csv)),
        encoding="utf-8",
    )
    print(f"[wan131] summary → {args.out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
