"""WAN-145 §2: 봉내 라이브 밴드에서의 부품 분해 — WAN-114 사다리를 새 밴드로 다시 낸다.

WAN-114는 진입 규칙 셋(재탭 노출 · RSI 게이트 · 볼린저) 중 **값을 더하는 건 볼린저
하나뿐**이고 그것도 15m에서만이라는 결론을 냈다(15m OOS 증분 +16.20%p vs 1h −1.18%p).
그런데 그 사다리는 **`band_bar="tap"`에 고정**돼 있다(WAN-132가 파급을 재산출이 아니라
고정으로 처리했다) — 즉 **밴드 정의를 바꿔 놓고 그 밴드의 주인공인 볼린저의 기여를 옛
밴드에서만 재고 있다.** 이 모듈이 같은 사다리를 **채택 기본값 밴드(`intrabar_live`)** 로
다시 낸다.

## 사다리 — WAN-114의 네 단 + 「채택 기본값」 단 하나

| 단계 | 무엇이 켜지나 | `retap_mode` | `rsi_gate_mode` | `deviation_filter` |
| -- | -- | -- | -- | -- |
| `L0` | 존-단독 (첫 탭만, 무조건) | `once` | `first_tap_free` | `None` |
| `L0r` | + 재탭 (게이트 없음) | `every_tap` | `none` | `None` |
| `L1` | + 재탭 RSI 게이트 | `every_tap` | `first_tap_free` | `None` |
| `L2` | + 볼린저 (WAN-122까지의 채택 기본값) | `every_tap` | `first_tap_free` | 볼린저 |
| **`L2u`** | **게이트 제거 = 오늘의 채택 기본값** | `every_tap` | **`unconditional`** | 볼린저 |

앞 네 단은 **WAN-114에서 그대로 가져온다**(`RUNGS`를 import한다 — 여기서 다시 정의하면
같은 라벨로 다른 설정을 돌 수 있다). 다섯째 단이 이 모듈이 더한 것이다.

🚨 **`L2` ≠ 채택 기본값이다 — 이 표에서 채택 기본값은 `L2u`다.** WAN-123이 RSI 게이트를
빼면서(`rsi_gate_mode="unconditional"`) WAN-114 사다리의 `L2` 라벨은 "WAN-122까지의 채택
기본값"이 됐고, WAN-132가 밴드를 옮기면서 괴리가 한 겹 더 늘었다. 사다리는 `L0r→L1`이라는
단이 존재하려면 게이트를 `first_tap_free`로 **고정**해야 하므로(그게 WAN-114의 설계다) 그
네 단은 건드리지 않고 **오늘의 기본값을 다섯째 단으로 얹었다**. `L2→L2u`가 곧 **게이트
제거의 기여**이고, 이는 WAN-124가 `tap` 위에서 잰 것과 같은 양의 새 밴드 판이다.

## 무엇이 WAN-114와 같고 무엇이 다른가

| 축 | WAN-114 | 이 모듈 |
| -- | -- | -- |
| 밴드 | `tap`(고정) | **`intrabar_live`(채택 기본값 — 고정하지 않는다)** |
| 렌즈 | 3렌즈 | **`baseline` 단독**(WAN-128) |
| 사다리 | `L0`·`L0r`·`L1`·`L2` | 같은 네 단 + **`L2u`(채택 기본값)** |
| 창·심볼·TF·구간 | 2023-07-14~2026-07-15 · 6심볼 · 15m·1h · IS/OOS | 같음 |

## 재현

```
uv run python -m backtest.wan145_entry_ablation --tf 1h --jobs 6
uv run python -m backtest.wan145_entry_ablation --tf 15m --jobs 4 --append
uv run python -m backtest.wan145_entry_ablation --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan114_entry_rule_ablation import RUNGS as WAN114_RUNGS
from backtest.wan114_entry_rule_ablation import Rung
from strategy.models import ConfluenceParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CSV = REPORTS_DIR / "wan145_entry_ablation.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan145_entry_ablation_summary.md"

#: 못 박은 창 · 6심볼 · 공동 작업 TF — §1과 같은 축이라야 두 표를 나란히 읽을 수 있다.
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

#: 오늘의 채택 기본값 단 — WAN-114 사다리에는 없다(그 사다리는 게이트를 고정한다).
ADOPTED_RUNG_NAME = "L2u"

#: 사다리 = WAN-114 네 단 + 채택 기본값 단. **네 단은 import한 정의 그대로**다.
RUNGS: tuple[Rung, ...] = (
    *WAN114_RUNGS,
    Rung(
        name=ADOPTED_RUNG_NAME,
        adds="+ RSI 게이트 제거 (= 오늘의 채택 기본값)",
        # `retap_mode`·`deviation_filter`를 덮어쓰지 않으므로 채택 기본값을 따라간다 —
        # 이 단이 `ConfluenceParams()`와 **완전히 같다**는 것을 테스트가 고정한다.
        updates={},
    ),
)

RUNGS_BY_NAME: dict[str, Rung] = {r.name: r for r in RUNGS}
LADDER: tuple[str, ...] = tuple(r.name for r in RUNGS)

#: ⚠️ **라벨 교정** — WAN-114의 `L2.adds`는 "(= 채택 기본값 = 이슈의 L3)"라 적혀 있는데
#: WAN-123 이후 그 말은 **거짓**이다(게이트가 `first_tap_free`로 고정된 단이다). 설정은
#: 그대로 물려받되(그래야 두 표가 같은 것을 가리킨다) **표에 찍히는 문장만** 여기서
#: 고쳐 쓴다 — 옛 모듈을 고치면 그 CSV·결론 문장이 흔들린다.
ADDS_OVERRIDES: dict[str, str] = {
    "L2": "+ 볼린저 진입가 재산정 (= WAN-122까지의 채택 기본값)",
}


def adds_of(level: str) -> str:
    """표에 찍을 「이 단이 새로 켜는 부품」 문장(위 교정을 적용한 것)."""
    return ADDS_OVERRIDES.get(level, RUNGS_BY_NAME[level].adds)


#: 존-단독(하한선). 판정은 이 단과 채택 기본값 단의 격차를 읽는다.
BASE_RUNG = "L0"

#: 볼린저 한 부품의 증분 — WAN-114의 결론 문장이 걸려 있는 단이다.
BOLLINGER_STEP = ("L1", "L2")


def rung_params(rung: Rung) -> ConfluenceParams:
    """사다리 한 단의 `ConfluenceParams` — **밴드를 고정하지 않는다**.

    WAN-114의 `rung_params`는 `pin_band_bar`로 `tap`에 묶는다(그 표의 결론이 그 밴드
    위에서 났고 `wan111`/`wan115`/`wan119`/`wan120` CSV와의 비트 일치 검산도 그것을
    전제하기 때문이다). 여기서는 그 고정을 **일부러 빼서** 채택 기본값 밴드
    (`intrabar_live`, WAN-132)를 따라가게 한다 — 그게 이 모듈의 존재 이유다.
    """
    base = ConfluenceParams().model_copy(update=dict(rung.updates))
    return harness.build_params(entry_mode="zone_limit", base=base)


def describe_engine() -> str:
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_mode={p.rsi_mode}, "
        f"rsi_gate_mode={p.rsi_gate_mode}, retap_mode={p.retap_mode}, "
        f"zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}"
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


class AblationRow(harness.RunRow):
    """격자 한 셀 — harness 공용 좌표·지표에 사다리 단만 얹는다(WAN-114와 같은 모델)."""

    level: str


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    levels: tuple[str, ...]


def run_cell(task: _Task, *, log: bool = True) -> list[AblationRow]:
    """한 (심볼, TF)의 IS/OOS × 사다리를 돈다.

    구간마다 오더블록을 **한 번만 탐지해 사다리 전체가 공유한다** — 탐지는 컨플루언스
    파라미터와 무관하므로 결과가 바뀌지 않고, 그 공유가 이 표의 전제이기도 하다: 모든
    단이 **같은 존 집합**을 보고 다른 건 오직 그 존에 어떻게 진입하냐다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []
    cfg = harness.build_config(task.timeframe)
    rows: list[AblationRow] = []
    for segment in harness.segments_for(oos=True):
        if segment.name not in SEGMENT_ORDER:
            continue
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        ob_result = harness.detect_order_blocks(window)
        for level in task.levels:
            params = rung_params(RUNGS_BY_NAME[level])
            outcome = harness.run_once(window, params=params, cfg=cfg, order_block_result=ob_result)
            row = harness.build_row(
                outcome, window, segment=segment, params=params, fill_name=OFFICIAL_LENS
            )
            rows.append(AblationRow(level=level, **row.model_dump()))
        if log:
            print(
                f"[wan145-abl] {task.symbol} {task.timeframe} {segment.name}: "
                f"{len(window.htf_df)}봉 · 존 {len(ob_result.order_blocks)}개 → "
                f"{len(task.levels)}단 완료",
                flush=True,
            )
    return rows


def _run_task_logged(task: _Task) -> list[AblationRow]:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    levels: Sequence[str] = LADDER,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[AblationRow]:
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            levels=tuple(levels),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_cell(task, log=log)]
    rows: list[AblationRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[AblationRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[AblationRow]:
    frame = pd.read_csv(path)
    return [AblationRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[AblationRow]) -> pd.DataFrame:
    frame = rows_to_frame(rows)
    if frame.empty:
        return frame
    return (
        frame.groupby(["level", "timeframe", "segment", "symbol"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            num_trades=("num_trades", "mean"),
            fill_rate=("fill_rate", "mean"),
            mean_r=("mean_r", "mean"),
        )
        .reset_index(drop=True)
    )


def rung_summary(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """단별 심볼평균 — `positive`(플러스 심볼 수)를 평균 옆에 둔다(WAN-111 규칙)."""
    if symbol_frame.empty:
        return symbol_frame
    grouped = symbol_frame.groupby(["timeframe", "segment", "level"], as_index=False).agg(
        total_return=("total_return", "mean"),
        positive=("total_return", lambda s: float((s > 0).sum())),
        symbols=("total_return", "count"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        num_trades=("num_trades", "mean"),
        fill_rate=("fill_rate", "mean"),
        mean_r=("mean_r", "mean"),
    )
    return _sorted(grouped)


def excluding_symbol(symbol_frame: pd.DataFrame, *, exclude: str = "ETH") -> pd.DataFrame:
    """심볼 하나를 뺀 평균 — 「평균을 누가 만들었나」를 보는 leave-one-out."""
    if symbol_frame.empty:
        return symbol_frame
    view = symbol_frame[~symbol_frame["symbol"].str.startswith(exclude)]
    return rung_summary(view)


def incremental(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """이웃한 두 단의 증분 델타 — 심볼별로 짝지어 뺀 뒤 평균(WAN-114와 같은 계산)."""
    records: list[dict[str, object]] = []
    if symbol_frame.empty:
        return pd.DataFrame(records)
    for (timeframe, segment), view in symbol_frame.groupby(["timeframe", "segment"], sort=False):
        pivots = {
            column: view.pivot_table(index="symbol", columns="level", values=column)
            for column in ("total_return", "num_trades", "max_drawdown", "win_rate")
        }
        returns = pivots["total_return"]
        for prev, cur in zip(LADDER, LADDER[1:], strict=False):
            if prev not in returns.columns or cur not in returns.columns:
                continue
            delta = (returns[cur] - returns[prev]).dropna()
            if delta.empty:
                continue
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "step": f"{prev}→{cur}",
                    "adds": adds_of(cur),
                    "delta_return": float(delta.mean()),
                    "symbols_up": float((delta > 0).sum()),
                    "symbols": float(len(delta)),
                    "delta_trades_pct": _relative(pivots["num_trades"], prev, cur),
                    "delta_win_rate": _mean_delta(pivots["win_rate"], prev, cur),
                    "delta_mdd": _mean_delta(pivots["max_drawdown"], prev, cur),
                }
            )
    return _sorted(pd.DataFrame(records))


def _mean_delta(pivot: pd.DataFrame, prev: str, cur: str) -> float:
    if prev not in pivot.columns or cur not in pivot.columns:
        return float("nan")
    return float((pivot[cur] - pivot[prev]).dropna().mean())


def _relative(pivot: pd.DataFrame, prev: str, cur: str) -> float:
    """거래 수의 상대 변화 — 「선별인가 가격인가」를 읽으려면 수익 델타 옆에 필요하다."""
    if prev not in pivot.columns or cur not in pivot.columns:
        return float("nan")
    before = float(pivot[prev].dropna().mean())
    after = float(pivot[cur].dropna().mean())
    if not before:
        return float("nan")
    return (after - before) / before


_ORDERINGS: dict[str, tuple[str, ...]] = {
    "segment": SEGMENT_ORDER,
    "level": LADDER,
    "timeframe": DEFAULT_TIMEFRAMES,
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
    if "step" in out.columns:
        out["_order_step"] = out["step"].map(
            {f"{a}→{b}": i for i, (a, b) in enumerate(zip(LADDER, LADDER[1:], strict=False))}
        )
        helpers.append("_order_step")
    return out.sort_values(helpers).drop(columns=helpers).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def bollinger_verdict(steps: pd.DataFrame, *, segment: str = harness.SEGMENT_OOS) -> list[str]:
    """볼린저 증분의 **부호·크기**가 새 밴드에서 어떻게 움직였는지(§완료기준 한 문장).

    숫자는 전부 프레임에서 읽는다 — 문장에 박아 두면 재실행 뒤 조용히 거짓말을 한다.
    """
    prev, cur = BOLLINGER_STEP
    lines: list[str] = []
    if steps.empty:
        return lines
    for timeframe in DEFAULT_TIMEFRAMES:
        view = steps[
            (steps["timeframe"] == timeframe)
            & (steps["segment"] == segment)
            & (steps["step"] == f"{prev}→{cur}")
        ]
        if view.empty:
            continue
        record = view.iloc[0]
        delta = float(record["delta_return"])
        lines.append(
            f"- **{timeframe} {segment}** `{prev}→{cur}`(볼린저): **{delta * 100:+.2f}%p** "
            f"({int(record['symbols_up'])}/{int(record['symbols'])}심볼 상승 · "
            f"거래 {float(record['delta_trades_pct']) * 100:+.1f}%)"
        )
    return lines


def ladder_verdict(
    summary: pd.DataFrame, steps: pd.DataFrame, *, segment: str = harness.SEGMENT_OOS
) -> list[str]:
    """존-단독 대비 채택 기본값의 격차 + 단별 증분."""
    lines: list[str] = []
    if summary.empty:
        return lines
    for timeframe in DEFAULT_TIMEFRAMES:
        view = summary[
            (summary["timeframe"] == timeframe) & (summary["segment"] == segment)
        ].set_index("level")
        if not {BASE_RUNG, ADOPTED_RUNG_NAME} <= set(view.index):
            continue
        base = float(view.loc[BASE_RUNG, "total_return"])
        adopted = float(view.loc[ADOPTED_RUNG_NAME, "total_return"])
        gap = adopted - base
        head = "규칙이 값을 더한다" if gap > 0 else "**규칙이 값을 더하지 못한다**"
        lines.append(
            f"- **{timeframe} {segment}**: 존-단독 `{BASE_RUNG}` {base * 100:+.2f}%"
            f"({int(view.loc[BASE_RUNG, 'positive'])}/{int(view.loc[BASE_RUNG, 'symbols'])}) → "
            f"채택 기본값 `{ADOPTED_RUNG_NAME}` {adopted * 100:+.2f}%"
            f"({int(view.loc[ADOPTED_RUNG_NAME, 'positive'])}/"
            f"{int(view.loc[ADOPTED_RUNG_NAME, 'symbols'])}) — 규칙 층 전체 기여 "
            f"**{gap * 100:+.2f}%p** ({head})"
        )
        sub = steps[(steps["timeframe"] == timeframe) & (steps["segment"] == segment)]
        for _, record in sub.iterrows():
            lines.append(
                f"  - `{record['step']}` {record['adds']}: "
                f"**{float(record['delta_return']) * 100:+.2f}%p** "
                f"({int(record['symbols_up'])}/{int(record['symbols'])}심볼 상승 · "
                f"거래 {float(record['delta_trades_pct']) * 100:+.1f}%)"
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


def ladder_table() -> str:
    """사다리 정의를 표로 — 문서와 코드가 갈라지지 않게 실제 파라미터를 찍는다."""
    records = []
    for rung in RUNGS:
        p = rung_params(rung)
        records.append(
            {
                "level": rung.name,
                "adds": adds_of(rung.name),
                "retap_mode": p.retap_mode,
                "rsi_gate_mode": p.rsi_gate_mode,
                "deviation_filter": (
                    f"볼린저({p.deviation_filter.band_bar})" if p.deviation_filter else "off"
                ),
                "채택 기본값": "**예**" if p == ConfluenceParams() else "",
            }
        )
    return _md_table(pd.DataFrame(records))


_SUMMARY_VIEW = (
    "timeframe",
    "segment",
    "level",
    "total_return",
    "positive",
    "symbols",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "fill_rate",
    "mean_r",
)

_STEP_VIEW = (
    "timeframe",
    "segment",
    "step",
    "adds",
    "delta_return",
    "symbols_up",
    "symbols",
    "delta_trades_pct",
    "delta_win_rate",
    "delta_mdd",
)


def build_summary_markdown(rows: Sequence[AblationRow], *, csv_path: Path) -> str:
    symbol_frame = per_symbol(rows)
    summary = rung_summary(symbol_frame)
    steps = incremental(symbol_frame)
    ex_eth = excluding_symbol(symbol_frame)

    lines = [
        "# WAN-145 §2 — 봉내 라이브 밴드에서의 부품 분해 (WAN-114 사다리 재검)",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 × 2TF(15m·1h) × IS/OOS "
        "격자, 렌즈는 **`baseline` 단독**(WAN-128).",
        "",
        f"재현: `uv run python -m backtest.wan145_entry_ablation` (요약만: `--from-csv`). "
        f"원자료: `{csv_path}`.",
        "",
        "## 이 리포트가 검정한 엔진",
        "",
        f"채택 기본값 — `{describe_engine()}`. **밴드를 고정하지 않는다**(WAN-114는 `tap`에 "
        "고정돼 있다) — 그게 이 표의 존재 이유다.",
        "",
        "## 사다리",
        "",
        ladder_table(),
        "",
        f"🚨 **`L2`는 채택 기본값이 아니다 — 이 표에서 채택 기본값은 `{ADOPTED_RUNG_NAME}`다.** "
        "WAN-123이 RSI 게이트를 뺀 뒤 WAN-114 사다리의 `L2` 라벨은 「WAN-122까지의 채택 "
        "기본값」이 됐고, WAN-132가 밴드를 옮기며 괴리가 한 겹 더 늘었다. 앞 네 단은 "
        "WAN-114 정의를 그대로 import했고(라벨과 설정이 갈라지지 않게), 오늘의 기본값을 "
        "다섯째 단으로 얹었다.",
        "",
        "## 판정 — 공식 렌즈(`baseline`) OOS",
        "",
        *ladder_verdict(summary, steps, segment=harness.SEGMENT_OOS),
        "",
        "IS 대조:",
        "",
        *ladder_verdict(summary, steps, segment=harness.SEGMENT_IS),
        "",
        "### 볼린저 증분 — WAN-114 결론이 걸린 단",
        "",
        *bollinger_verdict(steps, segment=harness.SEGMENT_OOS),
        "",
        "WAN-114(`tap` 고정)의 같은 단은 **15m OOS +16.20%p · 1h OOS −1.18%p**였다 — "
        "부호·크기의 이동은 위 두 줄과 맞대어 읽는다.",
        "",
        "## 1. 사다리 본표 — 단별 심볼평균",
        "",
        _md_table(_rounded(summary[list(_SUMMARY_VIEW)])) if not summary.empty else "행이 없다.",
        "",
        "## 2. 증분 델타 — 부품별 한계 기여",
        "",
        "`delta_return` = 심볼별로 짝지어 뺀 뒤 평균한 수익률 차 / `symbols_up` = 그 방향으로 "
        "움직인 심볼 수. 심볼 6개는 서로 상관된 표본이라(크립토 베타) `symbols_up`은 "
        "**유의성이 아니라 방향의 일관성**만 말한다.",
        "",
        _md_table(_rounded(steps[list(_STEP_VIEW)])) if not steps.empty else "행이 없다.",
        "",
        "## 3. 심볼 편중 — ETH 제외 사다리",
        "",
        "⚠️ 「엣지 없음」 계열 판정이 반복해 확인한 것: **평균은 심볼 하나가 만든다.**",
        "",
        _md_table(_rounded(ex_eth[list(_SUMMARY_VIEW)])) if not ex_eth.empty else "행이 없다.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-145 §2 새 밴드 부품 분해")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--levels", type=str, default=",".join(LADDER))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--append", action="store_true", help="기존 CSV에 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="요약만 재생성")
    args = parser.parse_args(argv)

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan145-abl] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            levels=tuple(x.strip() for x in str(args.levels).split(",") if x.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        if args.append and out_csv.exists():
            rows = rows_from_csv(out_csv) + list(rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
        print(f"[wan145-abl] {len(rows)}행 → {out_csv}")

    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(build_summary_markdown(rows, csv_path=out_csv), encoding="utf-8")
    print(f"[wan145-abl] summary → {args.out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
