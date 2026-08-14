"""WAN-267 — 익절 후 재진입 완전 분해 (진입가 3팔 × 깊이 분해 + 상한 스윕).

## 무엇을 재나 (사용자 아이디어 2026-08-08, WAN-263/266 (b) 무의 파생)

WAN-263/266이 재진입 후보를 **조건으로 걸러도** 무작위 선별을 못 이긴다((b) 무의)고 냈다.
그 뒤 사용자가 세 갈래를 물었다: (1) 선별 말고 **횟수(깊이)로 보면** 어떤가, (2) **상한을
2번**에 두면 제일 좋은가, (3) 재진입 진입가에 **볼린저를 빼면** 어떤가. 현행 재무장 루프
(`_iter_reentries`, WAN-228)는 지정가를 **첫 진입가에 고정**(팔 3)하는데, 그건 정답이 아니라
**하나의 모델링 가정**(WAN-223 §1 "형성 즉시 예약" 대리)이라 진입가 규칙을 세 팔로 갈라
재는 게 바르게 세운 비교다.

## 진입가 3팔 (재무장 루프 `entry_rule` 훅 — WAN-228에 배선)

* **팔 1 `band`** — 재무장 순간의 봉내 라이브 밴드로 지정가 재산정(`_IntrabarLiveLimit` =
  엔진 본 진입과 같은 사슬). ⚠️ 볼린저 규칙 3 때문에 재진입을 **아예 건너뛰는** 경우가
  생겨 체결 집합이 달라진다(combine_obs 부류).
* **팔 2 `zone`** — 존 근단(`zone_limit_price` + 오프셋), 볼린저 재산정 없음.
* **팔 3 `freeze`** — 첫 진입가 고정(현행 = 검산 기준점). override 안 주면 wan228/231/263
  CSV **비트 재현**(회귀 테스트가 동작으로 고정).

## 깊이 분해 + 상한 스윕

* 재진입마다 **깊이(순번)** 를 라벨로 기록(`_Reentry.depth`, 1-indexed) → 깊이별 승률·
  순손익·청산 사유 + **실측 최대 깊이**.
* 상한 1/2/3/무제한 — 격리 손익(각 재진입 독립 체결)에선 상한 N = 「깊이 ≤ N 행만 재집계」라
  **다시 안 돌리고 재집계만** 하면 나온다(공짜). 곡선 전체를 보고 판정(argmax만 집으면
  과최적화 = WAN-108 자유 파라미터 함정).
* **승자-생존 편향 통제**: 깊이 k 재진입은 직전까지 **이긴** 사슬에서만 나오므로 위험집합이
  깊이마다 다르다(WAN-149 §4 `h(0)` 함정). 깊이별 승률은 **그 깊이에 도달한 사슬 조건부**로
  읽고, 부모 익절 거래(깊이 0)는 다른 모집단이라 이 곡선에 섞지 않는다(depth ≥ 1만).

## 좌표

오늘 엔진(핀 없음 · `ConfluenceParams()` · 9종목 · 못 박은 6년 창 WAN-182) · 작업 TF
1h·2h·4h **먼저** → 15m은 `--append`(셀당 ~37분, WAN-203/262 분할 패턴). 렌즈 `baseline` +
`pen_5bp` **병기**(재진입은 낙관 체결 가정에 가장 크게 의존 — 볼린저/존근단 팔의 체결
견고성 차이를 `pen_5bp`가 가른다). ETH·SOL·DOGE·LINK leave-one-out · 20거래 게이트 자동.

## 성격 · 경고

측정 전용. 손익은 **격리 상한**(동시 1포지션·자본·북 상한 미모델링 = 북은 WAN-261 후속).
전부 `baseline`(닿으면 체결) 낙관 위 값(`pen_5bp` 병기하되 큐 우선순위 미모델 — 틱·호가
WAN-98 Canceled). 「엣지 없음」(WAN-84/88/111/114/124/151/201/248)은 **다른 질문**이라 불변
— 깊이·상한·진입가는 「이미 하기로 한 재진입 중 무엇을 버릴까/어디에 걸까」다. 유의여도
채택 근거 아님. 채택(재무장 기본값화·진입가 규칙 변경)은 재-베이스라인 = 사용자 결정.

## 재현

```
uv run python -m backtest.wan267_reentry_decompose --tf 4h,2h,1h --jobs 6
uv run python -m backtest.wan267_reentry_decompose --tf 15m --append --jobs 9   # 무거움(~37분/셀)
uv run python -m backtest.wan267_reentry_decompose --from-csv                    # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.models import ExitReason
from backtest.run import parse_date_ms
from backtest.substep import build_substeps
from backtest.sweep import timeframe_to_ms
from backtest.wan228_reentry_census import (
    ReentryEntryRule,
    _Reentry,
    reentry_events,
)
from backtest.zone_limit_backtest import (
    _prepare_htf,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan267_reentry_decompose.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan267_reentry_decompose_summary.md"

DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 작업 TF = 1h·2h·4h 먼저(컴퓨트 실현 가능). 15m은 셀당 ~37분(WAN-203)이라 `--append`.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "2h", "1h")

#: 진입가 3팔(WAN-267). 표·CSV에 이 순서로 찍는다.
ARMS: tuple[ReentryEntryRule, ...] = ("freeze", "zone", "band")
ARM_LABEL: dict[str, str] = {
    "freeze": "freeze(첫가격고정·현행)",
    "zone": "zone(존근단)",
    "band": "band(볼린저재계산)",
}

#: 렌즈 병기(WAN-267) — 재진입은 낙관 체결에 가장 크게 기댄다.
LENSES: tuple[str, ...] = ("baseline", "pen_5bp")

#: 상한 스윕 점(WAN-267 §5). 무제한은 sentinel None으로 곡선 끝에 둔다.
CAPS: tuple[int | None, ...] = (1, 2, 3, None)

#: 신규 3종목 — 펀딩 0행이라 순수익 낙관(WAN-178 백필 전). 재진입 수엔 무관. †로 표시.
FUNDING_GAP_SYMBOLS: frozenset[str] = frozenset(
    harness.normalize_symbol(s) for s in ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
)

#: 편중 LOO 대상 — 이 저장소가 반복해 편중원으로 지목한 종목들(WAN-263 배경).
BIAS_SYMBOLS: tuple[str, ...] = ("ETH", "SOL", "DOGE", "LINK")

#: 판정 게이트 — OOS 재진입 표본이 이보다 적으면 판정하지 않는다(WAN-84 유효 기준).
MIN_TRADES_GATE = 20

#: 팔 비교 판정 문턱 — 팔 간 OOS 격리 순수익 심볼평균 차가 이보다 작으면 무승부(%p).
ARM_MATERIAL_DELTA_PCT = 1.0


# --------------------------------------------------------------------------- #
# 행 모델 — (arm, lens, symbol, tf, depth)마다 IS/따뜻OOS 손익 버킷
# --------------------------------------------------------------------------- #


class DepthRow(BaseModel):
    """한 (진입가 팔, 렌즈, 심볼, TF, 깊이)의 재진입 손익 한 줄.

    `adopted_entries`·`tp_entries`는 **base 엔진**(재진입 이전)이라 팔에 무관하되, 렌즈로는
    갈린다(pen_5bp가 base 체결도 바꾼다). 셀 안 모든 깊이 행에 같은 값이 반복 실린다(재집계
    편의). 상한 N 재집계 = 같은 (arm,lens,symbol,tf)에서 `depth <= N` 행 합.
    """

    model_config = ConfigDict(frozen=True)

    arm: str
    lens: str
    symbol: str
    timeframe: str
    window_start: int
    window_end: int
    window_days: float

    adopted_entries: int
    tp_entries: int
    depth: int

    is_n: int
    is_wins: int
    is_stops: int
    is_gross_r_sum: float
    is_net_pp_sum: float
    oos_n: int
    oos_wins: int
    oos_stops: int
    oos_gross_r_sum: float
    oos_net_pp_sum: float

    funding_coverage: float | None


# --------------------------------------------------------------------------- #
# 순수 집계 (테스트가 여기를 고정한다)
# --------------------------------------------------------------------------- #


def _select(rows: Sequence[DepthRow], *, arm: str, lens: str, timeframe: str) -> list[DepthRow]:
    return [r for r in rows if r.arm == arm and r.lens == lens and r.timeframe == timeframe]


def symbols_in(rows: Sequence[DepthRow]) -> list[str]:
    return sorted({r.symbol for r in rows})


def max_depth(rows: Sequence[DepthRow]) -> int:
    """실측 최대 깊이 — n>0인 가장 깊은 순번(전체 또는 이미 필터된 부분집합)."""
    depths = [r.depth for r in rows if (r.is_n + r.oos_n) > 0]
    return max(depths) if depths else 0


def capped_oos_net_by_symbol(
    rows: Sequence[DepthRow], *, arm: str, lens: str, timeframe: str, cap: int | None
) -> dict[str, float]:
    """상한 N에서 심볼별 따뜻OOS 격리 순수익 합(%p) = `depth <= N` 행 합.

    상한이 격리 손익에서 「공짜」인 곳이다 — 다시 안 돌리고 깊이 라벨만 재집계한다.
    """
    scoped = _select(rows, arm=arm, lens=lens, timeframe=timeframe)
    out: dict[str, float] = {}
    for r in scoped:
        if cap is not None and r.depth > cap:
            continue
        out[r.symbol] = out.get(r.symbol, 0.0) + r.oos_net_pp_sum
    return out


def _symbol_mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def capped_oos_net_symbol_mean(
    rows: Sequence[DepthRow], *, arm: str, lens: str, timeframe: str, cap: int | None
) -> float:
    per = capped_oos_net_by_symbol(rows, arm=arm, lens=lens, timeframe=timeframe, cap=cap)
    return _symbol_mean(list(per.values()))


def oos_trade_count(
    rows: Sequence[DepthRow], *, arm: str, lens: str, timeframe: str, cap: int | None = None
) -> int:
    scoped = _select(rows, arm=arm, lens=lens, timeframe=timeframe)
    return sum(r.oos_n for r in scoped if cap is None or r.depth <= cap)


def depth_profile(
    rows: Sequence[DepthRow], *, arm: str, lens: str, timeframe: str
) -> list[tuple[int, int, int, int, float, float]]:
    """깊이별 (depth, n, wins, stops, mean_gross_r, net_pp_sum) — 따뜻OOS 버킷.

    승률은 `wins/(wins+stops)` = 그 깊이에 **도달한 사슬 조건부**(승자-생존 편향 통제상 이게
    맞는 모집단이다 — 깊이 0/기저를 섞지 않는다).
    """
    scoped = _select(rows, arm=arm, lens=lens, timeframe=timeframe)
    by_depth: dict[int, list[DepthRow]] = {}
    for r in scoped:
        by_depth.setdefault(r.depth, []).append(r)
    out: list[tuple[int, int, int, int, float, float]] = []
    for depth in sorted(by_depth):
        group = by_depth[depth]
        n = sum(r.oos_n for r in group)
        wins = sum(r.oos_wins for r in group)
        stops = sum(r.oos_stops for r in group)
        gross_sum = sum(r.oos_gross_r_sum for r in group)
        net_sum = sum(r.oos_net_pp_sum for r in group)
        mean_gross = gross_sum / n if n else 0.0
        out.append((depth, n, wins, stops, mean_gross, net_sum))
    return out


def leave_one_out(
    rows: Sequence[DepthRow], *, arm: str, lens: str, timeframe: str, drop: str
) -> float | None:
    """한 종목을 뺀 뒤 상한 무제한 OOS 격리 순수익 심볼평균(%p). 대상 없으면 None.

    `drop`은 짧은 티커(예: "ETH")다 — 정규화된 심볼("ETH/USDT:USDT")의 부분문자열로 매칭한다.
    """
    per = capped_oos_net_by_symbol(rows, arm=arm, lens=lens, timeframe=timeframe, cap=None)
    token = drop.split("/")[0].replace("USDT", "").upper()
    kept = [v for s, v in per.items() if not _short(s).upper().startswith(token)]
    return _symbol_mean(kept) if kept else None


def win_rate(wins: int, stops: int) -> float | None:
    decided = wins + stops
    return wins / decided if decided else None


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _arm_ranking(
    rows: Sequence[DepthRow], *, lens: str, timeframe: str
) -> list[tuple[str, float, int]]:
    """(arm, OOS 순수익 심볼평균, OOS n) — 순수익 내림차순. 상한 무제한."""
    out = [
        (
            arm,
            capped_oos_net_symbol_mean(rows, arm=arm, lens=lens, timeframe=timeframe, cap=None),
            oos_trade_count(rows, arm=arm, lens=lens, timeframe=timeframe),
        )
        for arm in ARMS
    ]
    return sorted(out, key=lambda t: t[1], reverse=True)


def verdict(rows: Sequence[DepthRow]) -> str:
    """세 물음에 답한다 — (a) 깊이 (b) 상한 (c) 진입가 규칙. 전부 행에서 계산한다."""
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    lens = "baseline"
    lines: list[str] = []

    # (c) 진입가 규칙 — baseline 리더 + pen_5bp 유지 여부.
    for tf in timeframes:
        ranking = _arm_ranking(rows, lens=lens, timeframe=tf)
        gated = [(a, v, n) for a, v, n in ranking if n >= MIN_TRADES_GATE]
        if not gated:
            lines.append(f"- **{tf}**: OOS 재진입 표본 < {MIN_TRADES_GATE} — 판정 불가(대조군).")
            continue
        lead_arm, lead_v, lead_n = gated[0]
        runner = gated[1] if len(gated) > 1 else None
        pen = _arm_ranking(rows, lens="pen_5bp", timeframe=tf)
        pen_lead = pen[0][0] if pen else "—"
        holds = (
            "유지"
            if pen_lead == lead_arm
            else f"뒤집힘(pen_5bp 리더 {ARM_LABEL.get(pen_lead, pen_lead)})"
        )
        margin = (lead_v - runner[1]) if runner else lead_v
        decisive = "명확" if abs(margin) >= ARM_MATERIAL_DELTA_PCT else "무승부(<1%p)"
        # (b) 상한: 무제한 대비 낮은 상한이 이기나(리더 팔에서).
        cap_curve = [
            capped_oos_net_symbol_mean(rows, arm=lead_arm, lens=lens, timeframe=tf, cap=c)
            for c in CAPS
        ]
        best_cap_idx = max(range(len(CAPS)), key=lambda i: cap_curve[i])
        best_cap = CAPS[best_cap_idx]
        cap_gain = cap_curve[best_cap_idx] - cap_curve[-1]  # 무제한 대비
        if best_cap is None:
            cap_txt = "상한 무제한이 최고(자르면 손해)"
        elif cap_gain < ARM_MATERIAL_DELTA_PCT:
            cap_txt = f"상한 {best_cap} 최고지만 무제한 대비 +{cap_gain:.2f}%p로 미미(<1%p)"
        else:
            cap_txt = f"상한 {best_cap}이 무제한보다 +{cap_gain:.2f}%p"
        # (a) 깊이 추세: 리더 팔 깊이별 조건부 승률 방향.
        prof = depth_profile(rows, arm=lead_arm, lens=lens, timeframe=tf)
        wr = [(d, win_rate(w, s)) for d, _n, w, s, _g, _net in prof]
        wr_valid = [(d, v) for d, v in wr if v is not None]
        if len(wr_valid) >= 2:
            trend = (
                "깊어질수록 개선" if wr_valid[-1][1] > wr_valid[0][1] else "깊어질수록 악화/평평"
            )
        else:
            trend = "깊이 표본 부족"
        md = max_depth(_select(rows, arm=lead_arm, lens=lens, timeframe=tf))
        lines.append(
            f"- **{tf}**: 리더 팔 **{ARM_LABEL.get(lead_arm, lead_arm)}** "
            f"(OOS {lead_v:+.2f}%p · n={lead_n} · 차이 {decisive} · pen_5bp {holds}) · "
            f"{cap_txt} · 깊이 {trend}(실측 최대 {md}회)."
        )

    return (
        "판정은 세 물음에 TF별로 답한다 — (c) 진입가 규칙 · (b) 상한 · (a) 깊이:\n\n"
        + "\n".join(lines)
        + "\n\n⚠️ **어느 답도 채택 근거가 아니다** — 전부 `baseline`(닿으면 체결) 낙관 위 값이고 "
        "손익은 **격리 상한**(동시 1포지션·자본·북 상한 미모델링, 북은 WAN-261 후속)이며, "
        "「엣지 없음」(WAN-84/88/111/114/124/151/201/248)은 다른 질문이라 불변이다(깊이·상한·"
        "진입가는 *이미 하기로 한 재진입 중 무엇을 버릴까/어디에 걸까*). 유의여도 채택 근거가 "
        "아니고, 채택(재무장 기본값화·진입가 규칙 변경)은 재-베이스라인 = 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 실행 (칸별)
# --------------------------------------------------------------------------- #


def describe_engine() -> str:
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={OrderBlockParams().combine_obs}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, limit_valid_bars={p.limit_valid_bars}, "
        f"short_enabled={p.short_enabled}"
    )


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    lens: str
    start_ms: int
    end_ms: int


def _bucket_rows(
    reentries: Sequence[_Reentry], boundary: int
) -> dict[int, tuple[int, int, int, float, float, int, int, int, float, float]]:
    """깊이별 (is_n,is_wins,is_stops,is_gross_sum,is_net_sum, oos_...)로 집계한다."""
    acc: dict[int, list[float]] = {}
    for r in reentries:
        slot = acc.setdefault(r.depth, [0, 0, 0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0])
        if r.entry_time < boundary:
            slot[0] += 1
            slot[1] += int(r.is_win)
            slot[2] += int(r.is_stop)
            slot[3] += r.gross_r
            slot[4] += r.net_return_pp
        else:
            slot[5] += 1
            slot[6] += int(r.is_win)
            slot[7] += int(r.is_stop)
            slot[8] += r.gross_r
            slot[9] += r.net_return_pp
    return {
        d: (
            int(v[0]),
            int(v[1]),
            int(v[2]),
            v[3],
            v[4],
            int(v[5]),
            int(v[6]),
            int(v[7]),
            v[8],
            v[9],
        )
        for d, v in acc.items()
    }


def run_cell(task: _Task, *, log: bool = True) -> list[DepthRow]:
    """한 (심볼, TF, 렌즈) 칸을 돌아 세 진입가 팔 × 깊이 손익 행을 낸다.

    base 후보·시퀀싱은 **한 번만** 하고 세 팔의 재무장 루프가 그 위를 공유한다(팔은 재진입
    지정가만 다르다). base 엔진이 렌즈로 갈리므로 렌즈는 칸 축이다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms, need_1m=True
    )
    if market.empty or market.df_1m.empty:
        return []
    ob = harness.detect_order_blocks(market, OrderBlockParams())
    cfg = harness.build_config(task.timeframe)
    params = harness.build_params(fill=harness.fill_preset(task.lens))

    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        task.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=ob,
    )
    paired = sequence_with_candidates(candidates, cfg, market.funding_rates)

    htf_ms = timeframe_to_ms(task.timeframe)
    frame = _prepare_htf(market.htf_df)
    htf_times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    htf_closes = [float(v) for v in frame["close"].astype(float).tolist()]
    substeps = build_substeps(market.df_1m, htf_ms)
    substep_times = [s.time for s in substeps]

    boundary = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    assert boundary is not None

    tp_pairs = [(c, t) for c, t in paired if c.reason is ExitReason.TAKE_PROFIT]
    coverage = harness.run_once(
        market, params=params, cfg=cfg, order_block_result=ob
    ).result.metrics.funding_coverage
    window_days = (task.end_ms - task.start_ms) / 86_400_000.0

    rows: list[DepthRow] = []
    for arm in ARMS:
        reentries: list[_Reentry] = []
        for cand, trade in tp_pairs:
            reentries.extend(
                reentry_events(
                    cand,
                    parent_exit_time=trade.exit_time,
                    substeps=substeps,
                    substep_times=substep_times,
                    htf_times=htf_times,
                    htf_closes=htf_closes,
                    params=params,
                    cfg=cfg,
                    funding_rates=market.funding_rates,
                    entry_rule=arm,
                )
            )
        buckets = _bucket_rows(reentries, boundary)
        for depth in sorted(buckets):
            b = buckets[depth]
            rows.append(
                DepthRow(
                    arm=arm,
                    lens=task.lens,
                    symbol=task.symbol,
                    timeframe=task.timeframe,
                    window_start=task.start_ms,
                    window_end=task.end_ms,
                    window_days=window_days,
                    adopted_entries=len(paired),
                    tp_entries=len(tp_pairs),
                    depth=depth,
                    is_n=b[0],
                    is_wins=b[1],
                    is_stops=b[2],
                    is_gross_r_sum=b[3],
                    is_net_pp_sum=b[4],
                    oos_n=b[5],
                    oos_wins=b[6],
                    oos_stops=b[7],
                    oos_gross_r_sum=b[8],
                    oos_net_pp_sum=b[9],
                    funding_coverage=coverage,
                )
            )
        if log:
            total = sum(b[0] + b[5] for b in buckets.values())
            md = max(buckets) if buckets else 0
            print(
                f"[wan267] {task.symbol} {task.timeframe} {task.lens} {arm}: "
                f"reentries={total} max_depth={md}",
                flush=True,
            )
    return rows


def _run_task_logged(task: _Task) -> list[DepthRow]:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    lenses: Sequence[str] = LENSES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[DepthRow]:
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            lens=lens,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
        for lens in lenses
    ]
    if jobs <= 1 or len(tasks) <= 1:
        nested = [run_cell(task, log=log) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            nested = list(executor.map(_run_task_logged, tasks))
    return [row for rows in nested for row in rows]


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[DepthRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(DepthRow.model_fields))


def cells_from_csv(path: Path) -> list[DepthRow]:
    frame = pd.read_csv(path)
    return [DepthRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def merge_append(existing: Sequence[DepthRow], fresh: Sequence[DepthRow]) -> list[DepthRow]:
    """기존 행에 새 행을 병합 — 같은 (arm,lens,symbol,tf,depth)는 새 행이 이긴다(--append)."""

    def key(r: DepthRow) -> tuple[str, str, str, str, int]:
        return (r.arm, r.lens, r.symbol, r.timeframe, r.depth)

    merged = {key(r): r for r in existing}
    for r in fresh:
        merged[key(r)] = r
    return list(merged.values())


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _pp(value: float) -> str:
    return f"{value:+.2f}%p"


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "—"


def _arm_lens_table(rows: Sequence[DepthRow], timeframe: str) -> list[str]:
    """진입가 팔 × 렌즈 대조표 (OOS 격리 순수익 심볼평균 · 상한 무제한)."""
    lines = [
        f"### {timeframe} — 진입가 팔 × 렌즈 (OOS 격리 순수익 심볼평균 · 상한 무제한)",
        "",
        "| 진입가 팔 | baseline OOS | baseline n | pen_5bp OOS | pen_5bp n |",
        "| -- | --: | --: | --: | --: |",
    ]
    for arm in ARMS:
        b = capped_oos_net_symbol_mean(
            rows, arm=arm, lens="baseline", timeframe=timeframe, cap=None
        )
        bn = oos_trade_count(rows, arm=arm, lens="baseline", timeframe=timeframe)
        p = capped_oos_net_symbol_mean(rows, arm=arm, lens="pen_5bp", timeframe=timeframe, cap=None)
        pn = oos_trade_count(rows, arm=arm, lens="pen_5bp", timeframe=timeframe)
        gate_b = "" if bn >= MIN_TRADES_GATE else " ⚠️"
        lines.append(f"| {ARM_LABEL[arm]} | {_pp(b)}{gate_b} | {bn} | {_pp(p)} | {pn} |")
    lines.append("")
    return lines


def _cap_table(rows: Sequence[DepthRow], timeframe: str) -> list[str]:
    """상한 스윕 곡선 (baseline OOS 격리 순수익 심볼평균) — 팔별."""
    header = " | ".join(f"상한 {c if c is not None else '∞'}" for c in CAPS)
    lines = [
        f"### {timeframe} — 상한 스윕 (baseline OOS 격리 순수익 심볼평균 · 팔별)",
        "",
        f"| 진입가 팔 | {header} |",
        "| -- | " + " | ".join("--:" for _ in CAPS) + " |",
    ]
    for arm in ARMS:
        cells = [
            _pp(
                capped_oos_net_symbol_mean(
                    rows, arm=arm, lens="baseline", timeframe=timeframe, cap=c
                )
            )
            for c in CAPS
        ]
        lines.append(f"| {ARM_LABEL[arm]} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "⚠️ 격리 손익이라 상한 N = 「깊이 ≤ N 재집계」다(공짜). 곡선 전체를 보고 판정 — "
        "최고점 argmax만 집으면 과최적화(WAN-108)."
    )
    lines.append("")
    return lines


def _depth_table(rows: Sequence[DepthRow], timeframe: str, arm: str) -> list[str]:
    """리더/현행 팔의 깊이별 분해 (baseline OOS · 승자-생존 편향 통제)."""
    prof = depth_profile(rows, arm=arm, lens="baseline", timeframe=timeframe)
    lines = [
        f"### {timeframe} — 깊이별 분해 · {ARM_LABEL[arm]} (baseline OOS)",
        "",
        "| 깊이 | n(도달) | 조건부 승률 | 평균 gross R | 격리 순수익 |",
        "| --: | --: | --: | --: | --: |",
    ]
    for depth, n, wins, stops, mean_gross, net_sum in prof:
        wr = _pct(win_rate(wins, stops))
        lines.append(f"| {depth} | {n} | {wr} | {mean_gross:+.2f}R | {_pp(net_sum)} |")
    md = max_depth(_select(rows, arm=arm, lens="baseline", timeframe=timeframe))
    lines.append("")
    lines.append(
        f"**실측 최대 깊이 {md}회.** 승률은 그 깊이에 **도달한 사슬 조건부**다(깊이 k는 직전까지 "
        "이긴 사슬에서만 나온다 — 승자-생존 편향, WAN-149 §4). 부모 익절 거래(깊이 0)는 다른 "
        "모집단이라 이 곡선에 없다."
    )
    lines.append("")
    return lines


def _loo_table(rows: Sequence[DepthRow], timeframe: str) -> list[str]:
    """리더 팔의 종목 편중 LOO (baseline OOS 격리 순수익 심볼평균)."""
    ranking = _arm_ranking(rows, lens="baseline", timeframe=timeframe)
    lead = ranking[0][0] if ranking else ARMS[0]
    full = capped_oos_net_symbol_mean(
        rows, arm=lead, lens="baseline", timeframe=timeframe, cap=None
    )
    header = " | ".join(f"−{s}" for s in BIAS_SYMBOLS)
    lines = [
        f"### {timeframe} — 종목 편중 LOO · 리더 팔 {ARM_LABEL[lead]} (baseline OOS)",
        "",
        f"| 전체 | {header} |",
        "| --: | " + " | ".join("--:" for _ in BIAS_SYMBOLS) + " |",
    ]
    loos = []
    for s in BIAS_SYMBOLS:
        v = leave_one_out(rows, arm=lead, lens="baseline", timeframe=timeframe, drop=s)
        loos.append(f"{v:+.2f}%p" if v is not None else "—")
    lines.append(f"| {full:+.2f}%p | " + " | ".join(loos) + " |")
    lines.append("")
    return lines


def build_summary_markdown(rows: Sequence[DepthRow], *, cells_csv: Path) -> str:
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    window = next(iter({(r.window_start, r.window_end) for r in rows}), (0, 0))
    lines = [
        "# WAN-267 — 익절 후 재진입 완전 분해 (진입가 3팔 × 깊이 분해 + 상한 스윕)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`) "
        "돌리며 옛 핀은 하나도 물려받지 않는다. 렌즈 `baseline`+`pen_5bp` 병기(WAN-267) · "
        "못 박은 6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "## 이 분해가 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영.",
        "",
        "## 방법 — 진입가 3팔 × 깊이 × 상한",
        "",
        "채택 엔진으로 전 구간을 돌려 실제 채택 거래를 얻고, 익절(1.5R)로 닫힌 거래마다 익절 "
        "**직후**부터 존 무효화까지 지정가를 재무장한다(`_iter_reentries`). 진입가 규칙 3팔: "
        "**freeze**(첫 진입가 고정 = 현행 검산 기준) · **zone**(존 근단+오프셋) · **band**"
        "(재무장 순간 봉내 라이브 밴드 재산정 = 엔진 본 진입과 같은 사슬). 재진입마다 **깊이"
        "(순번)** 를 라벨로 남겨 깊이별 손익·상한 곡선을 낸다.",
        "",
        "§2 손익은 IS와 **따뜻한 OOS**(WAN-166)로 가른다(재진입 진입 시각이 평가 경계 전/후). "
        "`격리 순수익`은 각 재진입을 기준자본에서 독립 체결시킨 `_to_trade` 순손익 — 동시 "
        "1포지션·자본·슬롯 경합·북 상한을 모델링하지 않는 **격리 상한**이다(북은 WAN-261 후속).",
        "",
        f"재현: `uv run python -m backtest.wan267_reentry_decompose --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` (15m: `--tf 15m --append --jobs 9` · "
        f"요약만: `--from-csv`). 원자료: `{cells_csv}`. 창=[{window[0]}, {window[1]}).",
        "",
    ]
    for tf in timeframes:
        lines += _arm_lens_table(rows, tf)
        lines += _cap_table(rows, tf)
        lead = _arm_ranking(rows, lens="baseline", timeframe=tf)
        lead_arm = lead[0][0] if lead else "freeze"
        lines += _depth_table(rows, tf, lead_arm)
        if lead_arm != "freeze":
            lines += _depth_table(rows, tf, "freeze")
        lines += _loo_table(rows, tf)
    lines += [
        "## 판정 — (a) 깊이 · (b) 상한 · (c) 진입가 규칙",
        "",
        verdict(rows),
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-267 재진입 완전 분해(진입가 3팔 × 깊이 × 상한)"
    )
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--lenses", type=str, default=",".join(LENSES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF, 렌즈) 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 CSV에 이번 실행 행을 병합한다(같은 arm·lens·symbol·tf·depth는 새 행이 이김).",
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_md = Path(args.out_md)

    if args.from_csv:
        rows = cells_from_csv(out_cells)
        print(f"[wan267] CSV에서 {len(rows)}행 로드 — 백테스트 재실행 없음")
    else:
        fresh = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            lenses=tuple(x.strip() for x in str(args.lenses).split(",") if x.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        if args.append and out_cells.exists():
            rows = merge_append(cells_from_csv(out_cells), fresh)
            print(f"[wan267] --append: 병합 후 {len(rows)}행(신규 {len(fresh)}행)")
        else:
            rows = list(fresh)
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(rows).to_csv(out_cells, index=False)
        print(f"[wan267] {len(rows)}행 → {out_cells}")

    if not rows:
        print("[wan267] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_summary_markdown(rows, cells_csv=out_cells), encoding="utf-8")
    print(f"[wan267] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
