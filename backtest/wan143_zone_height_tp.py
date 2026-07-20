"""WAN-143 §1: 익절 목표를 **오더블록 높이** 기준으로 — 4조합 손익 격자.

## 질문

채택 익절은 `take_profit_mode="fixed_r"`이고 **1R = 진입가 − 손절가**다. 볼린저가 진입가를
손절 쪽으로 끌어내리면 1R이 쪼그라들고 **익절 목표도 같이 쪼그라든다** — 손절폭 0.2%면 목표가
0.3%라 왕복비용 0.11%가 1R의 절반을 먹는다. WAN-79 사이징 가드
(`min_stop_distance_fraction=0.003`)는 그런 거래를 **아예 막아** 문제를 회피했는데, 막힌 거래가
15m 26.3% · 1h 7.1%이고 하필 뚫림률이 가장 낮은 구간이다(WAN-133 예비 측정).

가설: **문제는 진입가가 아니라 「목표를 진입가 기준으로 잡은 것」이다.** 1R을 **존 높이
(top − bottom)** 로 두면 볼린저가 준 좋은 가격이 *위험 축소*로만 쓰이고 *목표 축소*로는 안
쓰인다.

## 팔 이름 (이슈 §배너 — `A안`/`B안` 라벨 금지)

* **익절 축**: `익절-진입가기준`(`entry_r`, 현행) vs `익절-존높이기준`(`zone_height`, 제안)
* **가드 축**: `guard_on`(현행 `0.003`) vs `guard_off`(`0.0`)

2 × 2 = **4조합**. 넷을 다 도는 이유: `익절-존높이기준`의 이득은 손절폭 0.3% 미만 구간에서
나오는데 그 거래를 가드가 이미 차단하므로 **`zone_height + guard_on`은 대상이 거의 없고**,
`entry_r + guard_off`가 없으면 `zone_height + guard_off`의 개선이 **익절 덕인지 가드 푼 덕인지**
구분되지 않는다.

## 진입 집합은 익절 축에서 비트 단위로 같다

익절은 **청산만** 바꾸고 진입·체결 판정에는 안 쓰인다(WAN-137 Phase 2와 같은 성질) — 그래서
같은 가드 아래에서 두 익절 팔의 **후보(체결 셋업) 집합은 완전히 같다**. 격자가 그 불변을
**동작으로 검산**한다(어긋나면 배선 버그). ⚠️ 시퀀싱된 거래 수는 갈릴 수 있다 — 목표가 멀면
포지션을 더 오래 들어 동시 1포지션 슬롯이 더 오래 잠긴다. 가드 축은 **사이징 단계**라 후보는
그대로 두고 거래만 늘린다(size=0이면 그 셋업이 거래가 되지 않는다).

## 밴드 — `intrabar_live`(채택 기본값)에서 잰다

WAN-133 예비 표는 옛 밴드(`tap`)에서 나왔다. WAN-132가 진입가 정본을 `intrabar_live`로 옮겼고
진입가가 이동하면 1R 기하가 통째로 움직이므로, 이 격자는 **채택 기본값 그대로** 돈다 — 그
배선이 §0(구 WAN-144)이다. 여기서 `harness.pin_band_bar`를 쓰지 않는 것이 **의도**다.

재현:

```
uv run python -m backtest.wan143_zone_height_tp --tf 1h            # 1h 먼저(가벼움)
uv run python -m backtest.wan143_zone_height_tp --tf 15m --append  # 15m 뒤에 붙임
uv run python -m backtest.wan143_zone_height_tp --tf 1h --r-sweep --append
uv run python -m backtest.wan143_zone_height_tp --from-csv         # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_IS, SEGMENT_OOS, MarketData, Segment, segments_for
from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.run import parse_date_ms
from backtest.wan137_resistance_distance import DEFAULT_END, DEFAULT_START, DEFAULT_SYMBOLS
from backtest.zone_limit_backtest import (
    TakeProfitContext,
    TakeProfitOverride,
    _Candidate,
    _resolve_take_profit,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from execution.sizing import PositionSizingParams
from strategy.models import ConfluenceParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

REPORTS_DIR = Path("backtest/reports")

TP_ENTRY = "entry_r"
TP_ZONE = "zone_height"
TP_RULES: tuple[str, ...] = (TP_ENTRY, TP_ZONE)

GUARD_ON = 0.003
"""WAN-79 채택 가드 — 손절 거리가 진입가의 0.3% 미만이면 사이징이 0을 낸다."""
GUARD_OFF = 0.0
GUARDS: tuple[float, ...] = (GUARD_ON, GUARD_OFF)

SEGMENT_ORDER: tuple[str, ...] = ("full", SEGMENT_IS, SEGMENT_OOS)

#: R 배수 스윕 축(WAN-90이 진입가 축에서 한 것을 존높이 축에서 반복).
R_SWEEP: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0)


def guard_label(guard: float) -> str:
    return "guard_on" if guard > 0 else "guard_off"


def arm_label(tp_rule: str, guard: float) -> str:
    # ⚠️ 구분자로 `|`를 쓰지 않는다 — 요약이 마크다운 표라 셀 안의 `|`가 열을 쪼갠다.
    return f"{tp_rule} + {guard_label(guard)}"


# --------------------------------------------------------------------------- #
# 익절 오버라이드 — 목표를 존 높이로 잰다
# --------------------------------------------------------------------------- #


def make_zone_height_override(params: ConfluenceParams, r_multiple: float) -> TakeProfitOverride:
    """익절 = 진입가 ± `r_multiple` × (존 top − bottom). 손절은 건드리지 않는다.

    존 높이를 못 재면(0 이하 — 오더블록 정의상 실무에서는 안 나온다) 현행 규칙으로 폴백한다.
    지어낸 목표를 넣는 것보다 그 셋업만 대조 팔과 같은 목표를 갖게 두는 편이 정직하다
    (WAN-137 Phase 2의 "저항 없음" 폴백과 같은 관행).
    """

    def resolve(ctx: TakeProfitContext) -> float | None:
        height = ctx.order_block.top - ctx.order_block.bottom
        if height <= 0:
            return _resolve_take_profit(params, ctx.is_long, ctx.entry_price, ctx.stop_price, [])
        signed = height * r_multiple
        return ctx.entry_price + (signed if ctx.is_long else -signed)

    return resolve


def apply_guard(cfg: BacktestConfig, guard: float) -> BacktestConfig:
    """`min_stop_distance_fraction`만 갈아끼운다(다른 사이징 필드는 그대로).

    `risk_sizing`이 없으면(전액 진입 모드) 가드 자체가 없으므로 그대로 돌려준다 — 조용히
    `PositionSizingParams()`를 만들어 끼우면 WAN-65가 막은 "사이징이 사라진" 경로와 반대로
    이번엔 없던 사이징이 생겨 두 축이 섞인다.
    """
    sizing: PositionSizingParams | None = cfg.risk_sizing
    if sizing is None:
        return cfg
    return cfg.model_copy(
        update={"risk_sizing": sizing.model_copy(update={"min_stop_distance_fraction": guard})}
    )


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class TpRow(BaseModel):
    """한 (심볼, TF, 구간, 익절 규칙, 가드, R배수) 셀."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    tp_rule: str
    guard: float
    r_multiple: float
    eligible: int
    filled: int
    """체결 셋업 수(시퀀싱 이전). 같은 R·구간이면 익절 규칙·가드와 무관하게 같아야 한다."""
    num_trades: int
    fill_rate: float | None
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float | None
    mean_gross_r: float | None
    n_take_profit: int
    n_stop_loss: int
    n_end_of_data: int

    @property
    def arm(self) -> str:
        return arm_label(self.tp_rule, self.guard)


@dataclass(frozen=True)
class ArmOutcome:
    candidates: list[_Candidate]
    paired: list[tuple[_Candidate, Trade]]
    eligible: int
    filled: int
    fill_rate: float | None


def _gross_r(cand: _Candidate) -> float | None:
    """비용 반영 전 실현 R = 부호(청산가 − 진입가) / 1R. 1R을 못 재면 None."""
    risk = (
        cand.entry_price - cand.stop_price
        if cand.side is PositionSide.LONG
        else cand.stop_price - cand.entry_price
    )
    if risk <= 0:
        return None
    return cand.side.sign * (cand.exit_price - cand.entry_price) / risk


def build_row(
    market: MarketData,
    segment: str,
    tp_rule: str,
    guard: float,
    r_multiple: float,
    outcome: ArmOutcome,
    cfg: BacktestConfig,
) -> TpRow:
    trades = [t for _, t in outcome.paired]
    metrics = build_result_from_trades(trades, cfg, market.timeframe).metrics
    reasons = Counter(cand.reason for cand, _ in outcome.paired)
    grs = [g for g in (_gross_r(cand) for cand, _ in outcome.paired) if g is not None]
    return TpRow(
        symbol=market.symbol,
        timeframe=market.timeframe,
        segment=segment,
        tp_rule=tp_rule,
        guard=guard,
        r_multiple=r_multiple,
        eligible=outcome.eligible,
        filled=outcome.filled,
        num_trades=metrics.num_trades,
        fill_rate=outcome.fill_rate,
        total_return=metrics.total_return,
        max_drawdown=metrics.max_drawdown,
        win_rate=metrics.win_rate,
        sharpe=metrics.sharpe,
        mean_gross_r=statistics.fmean(grs) if grs else None,
        n_take_profit=reasons.get(ExitReason.TAKE_PROFIT, 0),
        n_stop_loss=reasons.get(ExitReason.STOP_LOSS, 0),
        n_end_of_data=reasons.get(ExitReason.END_OF_DATA, 0),
    )


# --------------------------------------------------------------------------- #
# 격자
# --------------------------------------------------------------------------- #


def run_cell(
    market: MarketData,
    segment: Segment,
    *,
    r_multiples: Sequence[float] = (1.5,),
) -> list[TpRow]:
    """한 (심볼, TF, 구간)의 익절 규칙 × 가드 × R배수를 돈다.

    후보 생성(비싼 부분)은 **익절 규칙 × R배수**마다 한 번이고, 가드는 사이징 단계라 같은
    후보를 두 번 시퀀싱하면 된다. 익절 규칙이 후보 집합을 바꾸지 않는다는 불변은 여기서
    진입 시각 집합 대조로 검산한다.
    """
    seg_market = harness.slice_market(market, segment)
    if seg_market.empty or seg_market.df_1m.empty:
        return []
    obr: OrderBlockResult = OrderBlockDetector().run(seg_market.htf_df)
    # ⚠️ 밴드를 고정하지 않는다 — 채택 기본값(`intrabar_live`, WAN-132) 위에서 재는 것이
    # 이 이슈의 요구다(§1-3 경고 3).
    base_params = harness.build_params(entry_mode="zone_limit")
    base_cfg = harness.build_config(seg_market.timeframe)

    rows: list[TpRow] = []
    entry_sets: dict[tuple[float, str], list[int]] = {}
    for r_multiple in r_multiples:
        params = base_params.model_copy(update={"take_profit_r": r_multiple})
        for tp_rule in TP_RULES:
            override = (
                None if tp_rule == TP_ENTRY else make_zone_height_override(params, r_multiple)
            )
            candidates, stats = build_zone_limit_candidates(
                seg_market.htf_df,
                seg_market.df_1m,
                seg_market.timeframe,
                params=params,
                cfg=base_cfg,
                order_block_result=obr,
                take_profit_override=override,
            )
            entry_sets[(r_multiple, tp_rule)] = sorted(c.entry_time for c in candidates)
            for guard in GUARDS:
                cfg = apply_guard(base_cfg, guard)
                paired = sequence_with_candidates(candidates, cfg, seg_market.funding_rates)
                rows.append(
                    build_row(
                        seg_market,
                        segment.name,
                        tp_rule,
                        guard,
                        r_multiple,
                        ArmOutcome(
                            candidates=candidates,
                            paired=paired,
                            eligible=stats.eligible,
                            filled=stats.filled,
                            fill_rate=stats.fill_rate,
                        ),
                        cfg,
                    )
                )
        # 검산: 같은 R에서 두 익절 규칙의 후보(체결 셋업) 집합은 비트 단위로 같아야 한다.
        base_entries = entry_sets[(r_multiple, TP_ENTRY)]
        zone_entries = entry_sets[(r_multiple, TP_ZONE)]
        if base_entries != zone_entries:
            raise AssertionError(
                f"후보 집합 불일치 — {seg_market.symbol} {seg_market.timeframe} "
                f"{segment.name} R={r_multiple}: {len(zone_entries)} vs "
                f"entry_r {len(base_entries)}. 익절이 진입을 바꾸는 배선 버그다."
            )
    return rows


def run_report(
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = ("1h",),
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    r_multiples: Sequence[float] = (1.5,),
    log: bool = True,
) -> list[TpRow]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    segments = segments_for(oos=True)
    rows: list[TpRow] = []
    for timeframe in timeframes:
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=True
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan143] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            for segment in segments:
                t0 = time.time()
                cell = run_cell(market, segment, r_multiples=r_multiples)
                rows.extend(cell)
                if log:
                    print(
                        f"[wan143] {sym} {timeframe} {segment.name}: "
                        f"{len(cell)}행 ({time.time() - t0:.0f}s)",
                        flush=True,
                    )
    return rows


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def _subset(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    tp_rule: str,
    guard: float,
    r_multiple: float = 1.5,
) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["tp_rule"] == tp_rule)
        & (frame["guard"] == guard)
        & (frame["r_multiple"] == r_multiple)
    ]


def pooled(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    tp_rule: str,
    guard: float,
    r_multiple: float = 1.5,
) -> dict[str, float | None]:
    """6심볼 심볼평균 — 수익률·MDD 등은 단순평균, 거래·청산 사유는 합."""
    sub = _subset(frame, timeframe, segment, tp_rule, guard, r_multiple)
    if sub.empty:
        return {}

    def avg(col: str) -> float | None:
        vals = sub[col].astype(float).dropna()
        return float(vals.mean()) if len(vals) else None

    ret, mdd = avg("total_return"), avg("max_drawdown")
    tp = int(sub["n_take_profit"].sum())
    sl = int(sub["n_stop_loss"].sum())
    eod = int(sub["n_end_of_data"].sum())
    closed = tp + sl + eod
    return {
        "n_symbols": float(len(sub)),
        "total_return": ret,
        "max_drawdown": mdd,
        "ret_over_mdd": (ret / mdd) if (ret is not None and mdd) else None,
        "win_rate": avg("win_rate"),
        "mean_gross_r": avg("mean_gross_r"),
        "fill_rate": avg("fill_rate"),
        "num_trades": float(sub["num_trades"].sum()),
        "filled": float(sub["filled"].sum()),
        "n_take_profit": float(tp),
        "n_stop_loss": float(sl),
        "n_end_of_data": float(eod),
        "stop_rate": (sl / closed) if closed else None,
        "n_positive": float((sub["total_return"].astype(float) > 0).sum()),
    }


def leave_one_out(
    frame: pd.DataFrame,
    timeframe: str,
    tp_rule: str,
    guard: float,
    segment: str = SEGMENT_OOS,
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인(이슈 필수 축)."""
    sub = _subset(frame, timeframe, segment, tp_rule, guard)
    if sub.empty:
        return "—"
    parts: list[str] = []
    for _, drop in sub.iterrows():
        rest = sub[sub["symbol"] != drop["symbol"]]["total_return"].astype(float)
        if len(rest):
            parts.append(f"−{_bare(str(drop['symbol']))} {rest.mean() * 100:+.2f}%")
    return " · ".join(parts)


def verdict(frame: pd.DataFrame, timeframe: str) -> str:
    """공식 렌즈 OOS 심볼평균으로 4조합을 가른다 — 가드 축을 갈라 읽는 것이 핵심이다."""
    cells = {
        (rule, guard): pooled(frame, timeframe, SEGMENT_OOS, rule, guard)
        for rule in TP_RULES
        for guard in GUARDS
    }
    if any(not c for c in cells.values()):
        return "판정 불가(OOS 데이터 없음)."

    def ret(rule: str, guard: float) -> float:
        value = cells[(rule, guard)].get("total_return")
        return 0.0 if value is None else value

    on_delta = ret(TP_ZONE, GUARD_ON) - ret(TP_ENTRY, GUARD_ON)
    off_delta = ret(TP_ZONE, GUARD_OFF) - ret(TP_ENTRY, GUARD_OFF)
    if on_delta > 0 and off_delta > 0:
        tag = "(a) 익절-존높이기준이 두 가드 모두에서 이긴다"
    elif on_delta <= 0 and off_delta <= 0:
        tag = "(b) 익절-존높이기준이 어느 가드에서도 못 이긴다 → 현행 유지"
    else:
        tag = "(c) 가드에 갈린다"
    return (
        f"{tag} — OOS 심볼평균 total_return: "
        f"가드 켬 {ret(TP_ENTRY, GUARD_ON) * 100:+.2f}% → {ret(TP_ZONE, GUARD_ON) * 100:+.2f}% "
        f"(Δ{on_delta * 100:+.2f}%p) · "
        f"가드 끔 {ret(TP_ENTRY, GUARD_OFF) * 100:+.2f}% → {ret(TP_ZONE, GUARD_OFF) * 100:+.2f}% "
        f"(Δ{off_delta * 100:+.2f}%p)."
    )


def guard_isolation(frame: pd.DataFrame, timeframe: str) -> str:
    """가드만 푼 대조군(②) — 개선이 익절 덕인지 가드 덕인지 가르는 문장."""
    base = pooled(frame, timeframe, SEGMENT_OOS, TP_ENTRY, GUARD_ON)
    guard_only = pooled(frame, timeframe, SEGMENT_OOS, TP_ENTRY, GUARD_OFF)
    both = pooled(frame, timeframe, SEGMENT_OOS, TP_ZONE, GUARD_OFF)
    if not base or not guard_only or not both:
        return "—"
    b, g, z = base.get("total_return"), guard_only.get("total_return"), both.get("total_return")
    if b is None or g is None or z is None:
        return "—"
    return (
        f"기준점 {b * 100:+.2f}% → 가드만 풀면 {g * 100:+.2f}% (Δ{(g - b) * 100:+.2f}%p) → "
        f"익절까지 바꾸면 {z * 100:+.2f}% (Δ{(z - g) * 100:+.2f}%p). "
        "즉 둘 다 바꾼 팔의 개선분 중 앞의 Δ는 **가드의 몫**이고 뒤의 Δ가 **익절의 몫**이다."
    )


def best_r(frame: pd.DataFrame, timeframe: str, segment: str, tp_rule: str, guard: float) -> str:
    """R 배수 스윕에서 그 구간의 최적 R(심볼평균 total_return 기준)."""
    values: list[tuple[float, float]] = []
    for r_multiple in sorted(set(frame["r_multiple"].astype(float))):
        cell = pooled(frame, timeframe, segment, tp_rule, guard, r_multiple)
        ret = cell.get("total_return") if cell else None
        if ret is not None:
            values.append((r_multiple, ret))
    if not values:
        return "—"
    top = max(values, key=lambda kv: kv[1])
    body = " · ".join(f"{r:.1f}R {v * 100:+.2f}%" for r, v in values)
    return f"최적 {top[0]:.1f}R ({top[1] * 100:+.2f}%) — {body}"


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.2f}"


def _num(v: float | None, fmt: str = ".2f") -> str:
    return "—" if v is None else format(v, fmt)


def _grid_table(frame: pd.DataFrame, timeframe: str) -> str:
    headers = [
        "segment",
        "arm",
        "return%",
        "mdd%",
        "ret/mdd",
        "win%",
        "meanR",
        "trades",
        "filled",
        "fill%",
        "stop%",
        "+심볼",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    for segment in SEGMENT_ORDER:
        for rule in TP_RULES:
            for guard in GUARDS:
                c = pooled(frame, timeframe, segment, rule, guard)
                if not c:
                    continue
                fill_rate = c.get("fill_rate")
                stop_rate = c.get("stop_rate")
                n_pos = c.get("n_positive")
                n_sym = c.get("n_symbols")
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            segment,
                            arm_label(rule, guard),
                            _pct(c.get("total_return")),
                            _pct(c.get("max_drawdown")),
                            _num(c.get("ret_over_mdd")),
                            _pct(c.get("win_rate")),
                            _num(c.get("mean_gross_r"), ".3f"),
                            _num(c.get("num_trades"), ".0f"),
                            _num(c.get("filled"), ".0f"),
                            _num(None if fill_rate is None else fill_rate * 100, ".1f"),
                            _num(None if stop_rate is None else stop_rate * 100, ".1f"),
                            "—" if n_pos is None or n_sym is None else f"{int(n_pos)}/{int(n_sym)}",
                        ]
                    )
                    + " |"
                )
    return "\n".join(lines)


def _symbol_table(frame: pd.DataFrame, timeframe: str, segment: str) -> str:
    sub = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["r_multiple"] == 1.5)
    ].copy()
    if sub.empty:
        return "(없음)"
    headers = ["symbol", "arm", "return%", "mdd%", "win%", "meanR", "trades", "TP", "SL", "EOD"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    sub["arm"] = [
        arm_label(str(r), float(g)) for r, g in zip(sub["tp_rule"], sub["guard"], strict=True)
    ]
    for _, r in sub.sort_values(["symbol", "arm"]).iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    _bare(str(r["symbol"])),
                    str(r["arm"]),
                    _pct(float(r["total_return"])),
                    _pct(float(r["max_drawdown"])),
                    _pct(float(r["win_rate"])),
                    _num(float(r["mean_gross_r"]) if pd.notna(r["mean_gross_r"]) else None, ".3f"),
                    str(int(r["num_trades"])),
                    str(int(r["n_take_profit"])),
                    str(int(r["n_stop_loss"])),
                    str(int(r["n_end_of_data"])),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_summary(frame: pd.DataFrame, path: Path) -> None:
    timeframes = sorted(set(frame["timeframe"]))
    has_sweep = len(set(frame["r_multiple"].astype(float))) > 1
    lines = [
        "# WAN-143 §1: 익절 목표를 오더블록 높이 기준으로 — 4조합 손익 격자",
        "",
        "재현: `uv run python -m backtest.wan143_zone_height_tp --tf 1h` "
        "(15m은 `--tf 15m --append`)",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · 6심볼 · 롱 온리 · 공식 렌즈 `baseline`"
        "(WAN-128) · 채택 기본값(존 지정가 + 오프셋 2bp + **봉내 라이브 밴드**, WAN-132). "
        "구간마다 독립 백테스트(초기자본 재시작, WAN-99).",
        "",
        "팔 이름: 익절 축 `entry_r`(익절-진입가기준, 현행) vs `zone_height`(익절-존높이기준, "
        f"제안) × 가드 축 `guard_on`(`min_stop_distance_fraction={GUARD_ON}`, WAN-79 현행) vs "
        "`guard_off`(`0.0`).",
        "",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 손익·체결률은 **상한**이다.",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124)은 불변 — 익절 구조는 알파가 아니라 **위험의 "
        "모양**만 바꾼다(WAN-90).",
        "> ⚠️ **기본값은 바꾸지 않았다**(측정 전용). `take_profit_mode`의 R 정의(WAN-81/90)도 "
        "`min_stop_distance_fraction`(WAN-76/79)도 바꾸려면 **명시적 재-베이스라인이자 사용자 "
        "결정**이다.",
        "",
    ]
    for timeframe in timeframes:
        lines += [
            f"## {timeframe}",
            "",
            f"**판정**: {verdict(frame, timeframe)}",
            "",
            f"**가드 분리(OOS)**: {guard_isolation(frame, timeframe)}",
            "",
            "**Leave-one-out(OOS `zone_height + guard_off`)**: "
            f"{leave_one_out(frame, timeframe, TP_ZONE, GUARD_OFF)}",
            "",
            "**Leave-one-out(OOS `entry_r + guard_on` = 기준점)**: "
            f"{leave_one_out(frame, timeframe, TP_ENTRY, GUARD_ON)}",
            "",
            "### 4조합 × 구간 (6심볼 심볼평균; trades·filled는 합)",
            "",
            _grid_table(frame, timeframe),
            "",
            "### 심볼별 (OOS)",
            "",
            _symbol_table(frame, timeframe, SEGMENT_OOS),
            "",
        ]
        if has_sweep:
            lines += [
                "### R 배수 스윕 (`zone_height + guard_off`)",
                "",
                f"- IS: {best_r(frame, timeframe, SEGMENT_IS, TP_ZONE, GUARD_OFF)}",
                f"- OOS: {best_r(frame, timeframe, SEGMENT_OOS, TP_ZONE, GUARD_OFF)}",
                "",
                "⚠️ IS가 고르는 R이 OOS에서 무너지는지가 관전 포인트다(WAN-90 진입가 축의 재현).",
                "",
            ]
    lines += [
        "## 후보 집합 검산",
        "",
        "익절만 바꾸므로 **같은 R·구간에서 두 익절 팔의 체결 셋업 집합은 비트 단위로 같다** — "
        "격자가 진입시각 집합을 대조해 어긋나면 `AssertionError`로 멈춘다. 표의 `trades`"
        "(시퀀싱 후)가 팔마다 다른 것은 **관찰**이다: 목표가 멀면 슬롯이 더 오래 잠기고, 가드를 "
        "풀면 사이징이 0을 내던 셋업이 거래가 된다.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[TpRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[TpRow]:
    frame = pd.read_csv(path)
    return [TpRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default="1h", help="콤마로 여러 개(예: 1h,15m)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--out-csv", type=str, default=str(REPORTS_DIR / "wan143_zone_height.csv"))
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan143_zone_height_summary.md")
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 새 TF 행을 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument(
        "--r-sweep", action="store_true", help=f"R 배수 스윕까지 돈다({R_SWEEP} — 느리다)"
    )
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan143] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        r_multiples = R_SWEEP if args.r_sweep else (1.5,)
        rows = run_report(
            symbols, timeframes, start=args.start, end=args.end, r_multiples=r_multiples
        )
        if args.append and out_csv.exists():
            existing = rows_from_csv(out_csv)
            keep = [r for r in existing if r.timeframe not in set(timeframes)]
            rows = keep + list(rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows_to_frame(rows), Path(args.out_md))
    print(f"[wan143] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
