"""WAN-206: 존높이 기준 익절을 **오늘 엔진**에서 재측정 — 2팔 손익 격자.

## 질문 (사용자 요청 2026-07-28)

익절 목표를 "진입가 → 손절까지 거리의 1.5배"가 아니라 **"오더블록(존) 높이 그 자체의
1.5배"** 로 잡으면 어떤가. 이는 WAN-143의 `zone_height` 팔과 **정확히 같은 가설**이다 —
1R을 `진입가 − 손절가`가 아니라 존 높이(top − bottom)로 재고 익절을 그 배수로 둔다.

## 왜 다시 재는가 — WAN-143은 오늘 엔진이 아니다

[docs/decisions/wan143.md](../../docs/decisions/wan143.md)가 이 가설을 이미 쟀지만
(판정 (c): 15m 현행 승·1h 존높이 승), **그 뒤 채택 기본값이 두 번 바뀌었다**:

* **존폭 필터 1.28 채택**(WAN-159, 07-21) — WAN-143은 필터가 **꺼진 채** 쟀다.
* **9종목 · 못 박은 6년 확장**(WAN-182, 07-23) — WAN-143은 **6종목 · 3년**이다.

즉 **지금 매매하는 엔진에서 존높이 익절을 잰 적이 없다.** 필터가 이미 "좁은 존만"
남기므로(존폭 ÷ ATR ≤ 1.28) 존높이와 진입가→손절 거리의 간극이 예전과 다르게 움직일
수 있다. 이 모듈은 WAN-143의 익절 오버라이드·가드·산식을 **그대로 import해** 오늘의
채택 좌표에서 다시 돈다 — 새 파이프라인이 아니다.

## WAN-143과 무엇이 다른가

* **좌표**: 9종목 × 못 박은 6년(`harness.DEFAULT_*`) × 15m·1h·4h (WAN-143은 6종목·3년·1h).
* **엔진**: 채택 기본값 그대로 — 필터 1.28 · `intrabar_live` · `unconditional` · 오프셋
  2bp · `combine_obs=False`. **핀 금지**(`LEGACY_*`·`pin_*` 미사용, `OrderBlockParams()`).
* **가드 축 없음**: 가드는 오늘 기본값 `0.003`으로 **고정**한다(WAN-79 소관 · 익절 축만
  격리). WAN-143의 `guard_on`/`guard_off` 2×2는 여기서 열지 않는다.
* **렌즈**: `baseline`(공식) **+** `pen_5bp`(병기). 존높이 익절은 목표가 멀어 슬롯을 더
  오래 잠그므로 체결 보수화가 핵심이다 — baseline만 보고 판정하지 않는다.
* **OOS**: WAN-166 정본 — `oos_warm`(따뜻한 연속, 주 수치) + `oos`(차가운 절단, 스트레스).

## 안전 (기록)

* ⚠️ **WAN-143 표와 셀을 직접 비교 금지** — 창·유니버스·필터가 통째로 다르다. "전 대비"
  서술 시 명시.
* 🚨 **"존높이가 낫다"·"1h에서 이겼다"로 단독 인용 금지** — WAN-143의 1h 우위는 ETH를
  빼면 사라졌고 IS에선 현행이 이겼다.
* **익절 자는 알파가 아니라 위험의 모양만 바꾼다**(WAN-90) · 전부 `baseline`(낙관 체결)
  위 값 · **「엣지 없음」(WAN-84/88/111/114/124/151/201) 불변**(다른 질문).
* **채택(존높이로 전환)은 재-베이스라인 = 사용자 결정** · 개발자 임의 착수 금지. 이 이슈는
  그 결정을 위한 오늘-엔진 근거를 만드는 **측정 전용**이다.

재현:

```
uv run python -m backtest.wan206_zone_height_tp_today --tf 4h            # 가벼운 것부터
uv run python -m backtest.wan206_zone_height_tp_today --tf 1h --append
uv run python -m backtest.wan206_zone_height_tp_today --tf 15m --append  # 무겁다
uv run python -m backtest.wan206_zone_height_tp_today --tf 1h,15m --r-sweep --append
uv run python -m backtest.wan206_zone_height_tp_today --from-csv         # 요약만 재생성
uv run python -m backtest.wan206_zone_height_tp_today --checksum         # run_once 비트 일치
```
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import (
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    FillPreset,
    MarketData,
    Segment,
    segments_for,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.run import parse_date_ms
from backtest.wan95_zone_limit_report import apply_funding_proxy
from backtest.wan143_zone_height_tp import (
    R_SWEEP,
    TP_ENTRY,
    TP_RULES,
    TP_ZONE,
    make_zone_height_override,
)
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    TakeProfitOverride,
    ZoneLimitStats,
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.models import OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

REPORTS_DIR = Path("backtest/reports")

#: 렌즈 축 — 공식(baseline) + 스트레스(pen_5bp). WAN-128 이후 신규 리포트는 baseline
#: 단독이 원칙이나, 이 이슈는 "존높이 목표가 멀어 마진 체결에 더 취약한가"가 핵심 질문이라
#: 이슈가 명시적으로 `pen_5bp` 병기를 요구했다(§작업 범위).
LENS_BASELINE = "baseline"
LENS_PEN = "pen_5bp"
LENSES: tuple[str, ...] = (LENS_BASELINE, LENS_PEN)

#: 오늘 채택 가드(WAN-79) — 손절 거리가 진입가의 0.3% 미만이면 사이징이 0을 낸다. 이
#: 이슈는 가드를 **축으로 열지 않고** 오늘 기본값으로 고정하므로 `build_config`가 주는
#: 기본값(`min_stop_distance_fraction=0.003`)을 그대로 쓴다.
GUARD = 0.003

SEGMENT_ORDER: tuple[str, ...] = ("full", SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 판정의 주 구간(WAN-166) = 따뜻한 연속 OOS. 차가운 `oos`는 스트레스로 병기한다.
PRIMARY_OOS = SEGMENT_OOS_WARM
STRESS_OOS = SEGMENT_OOS

#: 메인 비교 배수(채택값, WAN-81/90). **이 배수에서만** 전체 격자(2렌즈 × 2팔)를 돌린다.
MAIN_R = 1.5


def sweep_arms(r_multiple: float, lenses: Sequence[str]) -> list[tuple[str, str]]:
    """이 R배수에서 돌릴 (렌즈, 익절 규칙) 목록.

    메인 배수(`MAIN_R`)에서는 **전체 격자**(2렌즈 × entry_r·zone_height)를 낸다 — 렌즈
    병기·현행 대조·후보 집합 검산이 여기서 성립한다. 그 외 스윕 배수(1.0/2.0/3.0)에서는
    **`baseline × zone_height` 한 팔만** 낸다: 요약의 R 스윕 섹션이 실제로 읽는 곡선이
    그것뿐이고(`best_r(..., LENS_BASELINE, TP_ZONE)`), 나머지 팔은 어느 표에도 안 쓰여
    격자를 4배로 부풀리기만 하기 때문이다(사용자 결정 2026-07-29: 스윕 축소). entry_r은
    R배수와 무관하게 같은 청산이라 스윕에서 재도 정보가 없다.
    """
    if r_multiple == MAIN_R:
        return [(lens, rule) for lens in lenses for rule in TP_RULES]
    return [(LENS_BASELINE, TP_ZONE)]


def arm_label(tp_rule: str) -> str:
    return tp_rule


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class TpRow(BaseModel):
    """한 (심볼, TF, 구간, 렌즈, 익절 규칙, R배수) 셀. 가드는 축이 아니라 고정값이다."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    lens: str
    tp_rule: str
    r_multiple: float
    eligible: int
    filled: int
    """체결 셋업 수(시퀀싱 이전). 같은 렌즈·R·구간이면 익절 규칙과 무관하게 같아야 한다."""
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


@dataclass(frozen=True)
class ArmOutcome:
    paired: list[tuple[_Candidate, Trade]]
    eligible: int
    filled: int
    fill_rate: float | None
    entry_times: list[int]
    """평가 창 후보(체결 셋업)의 진입 시각 집합(정렬). 익절은 청산만 바꾸므로 같은 렌즈·R에서
    두 익절 팔의 이 집합은 비트 단위로 같아야 한다(`run_cell`이 대조 — 재빌드 없이 여기서 낸다)."""


def _gross_r(cand: _Candidate) -> float | None:
    """비용 반영 전 실현 R = 부호(청산가 − 진입가) / 1R. 1R을 못 재면 None.

    WAN-143의 동명 헬퍼와 같은 산식이나, 그쪽은 모듈 private(`_gross_r`)이라 import하지
    않고 여기 둔다(같은 정의임을 테스트가 고정한다).
    """
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
    lens: str,
    tp_rule: str,
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
        lens=lens,
        tp_rule=tp_rule,
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
# 팔 실행 — 따뜻한 연속 OOS 필터를 여기서 처리한다
# --------------------------------------------------------------------------- #


def run_arm(
    seg_market: MarketData,
    *,
    params_fill: FillPreset,
    tp_rule: str,
    r_multiple: float,
    obr: OrderBlockResult,
    eval_from_ms: int | None,
) -> ArmOutcome:
    """한 (렌즈, 익절 규칙, R배수) 팔을 seg_market에서 돌린다.

    `eval_from_ms`(따뜻한 연속 OOS, WAN-166)를 주면 `run_zone_limit_backtest_verbose`와
    **글자 그대로 같은 절차**로 후보를 평가 창으로 좁힌다: 탐색·체결은 넘겨받은 창 전체에서
    하되, 탭(`trigger_time`)이 그 시각 이후인 셋업만 신선한 초기자본으로 시퀀싱하고 통계도
    평가 창 기준으로 다시 센다. 그래야 `entry_r` 팔(override=None)이 `harness.run_once`와
    비트 단위로 일치한다(`run_checksum`가 고정).
    """
    # WAN-384 명시 핀: 이 표는 존폭 필터를 켠 채(1.28) 낸 기록이다.
    params = harness.build_params(
        entry_mode="zone_limit",
        take_profit_r=r_multiple,
        fill=params_fill,
        max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
    )
    cfg = harness.legacy_build_config(seg_market.timeframe)
    override: TakeProfitOverride | None = (
        None if tp_rule == TP_ENTRY else make_zone_height_override(params, r_multiple)
    )
    sink: list[SetupDiagnostic] = []
    candidates, stats = build_zone_limit_candidates(
        seg_market.htf_df,
        seg_market.df_1m,
        seg_market.timeframe,
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_result=obr,
        setup_sink=sink,
        take_profit_override=override,
    )
    if eval_from_ms is not None:
        candidates = [c for c in candidates if c.trigger_time >= eval_from_ms]
        kept = [d for d in sink if d.trigger_time >= eval_from_ms]
        stats = ZoneLimitStats(
            eligible=len(kept),
            filled=sum(1 for d in kept if d.filled),
            penetrations=sum(1 for c in candidates if c.penetration),
            dropped=sum(1 for d in kept if d.dropped),
        )
    paired = sequence_with_candidates(candidates, cfg, seg_market.funding_rates)
    return ArmOutcome(
        paired=paired,
        eligible=stats.eligible,
        filled=stats.filled,
        fill_rate=stats.fill_rate,
        entry_times=sorted(c.entry_time for c in candidates),
    )


# --------------------------------------------------------------------------- #
# 격자
# --------------------------------------------------------------------------- #


def run_cell(
    market: MarketData,
    segment: Segment,
    *,
    r_multiples: Sequence[float] = (1.5,),
    lenses: Sequence[str] = LENSES,
) -> list[TpRow]:
    """한 (심볼, TF, 구간)의 렌즈 × 익절 규칙 × R배수를 돈다.

    따뜻한 세그먼트(`oos_warm`)는 `slice_market`이 항등이라 전 창에서 탐지·후보 생성을
    하고, `eval_boundary_ms`가 준 평가 경계 이후 탭만 시퀀싱한다(WAN-166). 차가운
    세그먼트(full/is/oos)는 창을 먼저 잘라(`slice_market`) 그 안에서 독립적으로 탐지·평가한다.
    """
    seg_market = harness.slice_market(market, segment)
    if seg_market.empty or seg_market.df_1m.empty:
        return []
    eval_from_ms = harness.eval_boundary_ms(market, segment)
    # ⚠️ 밴드·병합을 고정하지 않는다 — 채택 기본값(`intrabar_live` WAN-132 · `combine_obs=False`
    # WAN-149) 위에서 재는 것이 이 이슈의 요구다(핀 금지). `OrderBlockParams()`가 곧 오늘 기본값.
    obr: OrderBlockResult = OrderBlockDetector(OrderBlockParams()).run(seg_market.htf_df)

    rows: list[TpRow] = []
    cfg = harness.legacy_build_config(seg_market.timeframe)
    for r_multiple in r_multiples:
        # (렌즈, R) 안에서 entry_r·zone_height를 대조하려고 그 둘을 붙여 모은다.
        sets: dict[tuple[str, str], list[int]] = {}
        for lens_name, tp_rule in sweep_arms(r_multiple, lenses):
            outcome = run_arm(
                seg_market,
                params_fill=harness.fill_preset(lens_name),
                tp_rule=tp_rule,
                r_multiple=r_multiple,
                obr=obr,
                eval_from_ms=eval_from_ms,
            )
            rows.append(
                build_row(seg_market, segment.name, lens_name, tp_rule, r_multiple, outcome, cfg)
            )
            sets[(lens_name, tp_rule)] = outcome.entry_times
        # 검산: 같은 렌즈·R에서 두 익절 규칙을 모두 돈 경우(= 메인 배수) 체결 셋업 집합은
        # 비트 단위로 같아야 한다. 스윕 배수는 zone_height 한 팔만 돌아 대조 대상이 없다.
        for lens_name in lenses:
            entry = sets.get((lens_name, TP_ENTRY))
            zone = sets.get((lens_name, TP_ZONE))
            if entry is not None and zone is not None and entry != zone:
                raise AssertionError(
                    f"후보 집합 불일치 — {seg_market.symbol} {seg_market.timeframe} "
                    f"{segment.name} {lens_name} R={r_multiple}: "
                    f"{len(zone)} vs entry_r {len(entry)}. 익절이 진입을 바꾸는 배선 버그다."
                )
    return rows


def run_report(
    symbols: Sequence[str] = harness.LEGACY_NINE_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    r_multiples: Sequence[float] = (1.5,),
    lenses: Sequence[str] = LENSES,
    funding_proxy: bool = True,
    log: bool = True,
) -> list[TpRow]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    segments = segments_for(warm_oos=True)
    rows: list[TpRow] = []
    for timeframe in timeframes:
        markets: dict[str, MarketData] = {}
        funding_by_symbol: dict[str, list[FundingRate]] = {}
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=True
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan206] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            markets[sym] = market
            funding_by_symbol[sym] = market.funding_rates
        if funding_proxy:
            funding_by_symbol, note = apply_funding_proxy(funding_by_symbol)
            if note and log:
                print(f"[wan206] {note}", flush=True)
            markets = {
                sym: replace(m, funding_rates=funding_by_symbol[sym]) for sym, m in markets.items()
            }
        for sym, market in markets.items():
            for segment in segments:
                t0 = time.time()
                cell = run_cell(market, segment, r_multiples=r_multiples, lenses=lenses)
                rows.extend(cell)
                if log:
                    print(
                        f"[wan206] {sym} {timeframe} {segment.name}: "
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
    lens: str,
    tp_rule: str,
    r_multiple: float = 1.5,
) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["lens"] == lens)
        & (frame["tp_rule"] == tp_rule)
        & (frame["r_multiple"] == r_multiple)
    ]


def pooled(
    frame: pd.DataFrame,
    timeframe: str,
    segment: str,
    lens: str,
    tp_rule: str,
    r_multiple: float = 1.5,
) -> dict[str, float | None]:
    """심볼평균 — 수익률·MDD 등은 단순평균, 거래·청산 사유는 합."""
    sub = _subset(frame, timeframe, segment, lens, tp_rule, r_multiple)
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
    lens: str,
    tp_rule: str,
    segment: str = PRIMARY_OOS,
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인(ETH·SOL, 이슈 필수 축)."""
    sub = _subset(frame, timeframe, segment, lens, tp_rule)
    if sub.empty:
        return "—"
    parts: list[str] = []
    for _, drop in sub.iterrows():
        rest = sub[sub["symbol"] != drop["symbol"]]["total_return"].astype(float)
        if len(rest):
            parts.append(f"−{_bare(str(drop['symbol']))} {rest.mean() * 100:+.2f}%")
    return " · ".join(parts)


MIN_TRADES_PER_SYMBOL = 20
"""WAN-84 유효 기준 — OOS 심볼당 거래가 이보다 적으면 판정하지 않는다(4h·TRX류 게이트).

WAN-107/130이 4h를 「보류(표본 부족)」로 둔 자로, 심볼당 15.7거래(WAN-107)·18.3거래
(WAN-130)가 이 문턱을 못 넘었다. 6년 창에서 4h가 이를 넘을 수 있으나(WAN-176), 게이트는
주의문이 아니라 코드로 강제한다.
"""


def trades_per_symbol(
    frame: pd.DataFrame, timeframe: str, segment: str = PRIMARY_OOS, lens: str = LENS_BASELINE
) -> float:
    """그 (TF, 구간)의 기준점 팔(entry_r) 기준 심볼당 거래 수 — 표본 게이트의 입력."""
    cell = pooled(frame, timeframe, segment, lens, TP_ENTRY)
    trades, symbols = cell.get("num_trades"), cell.get("n_symbols")
    if not cell or trades is None or not symbols:
        return 0.0
    return trades / symbols


def _ret(
    cells: dict[tuple[str, str], dict[str, float | None]], segment: str, tp_rule: str
) -> float:
    cell = cells.get((segment, tp_rule), {})
    value = cell.get("total_return")
    return 0.0 if value is None else value


def verdict(frame: pd.DataFrame, timeframe: str, lens: str = LENS_BASELINE) -> str:
    """존높이 vs 현행(entry_r)을 가른다 — 주 구간 따뜻한 OOS(WAN-166), 차가움은 스트레스.

    ⚠️ 표본이 WAN-84 기준(심볼당 20거래)에 못 미치면 판정하지 않는다(4h·TRX류). 숫자는
    그대로 내되 문장 앞에 「판정 불가(대조군)」를 박는다 — 대조군 열이 판정문을 갖고 그것이
    다음 이슈에서 재인용되는 실패를 코드로 막는다.

    (a) 존높이가 따뜻·차가움 **둘 다** 이긴다 / (b) 어느 쪽도 못 이긴다(현행 유지) /
    (c) 따뜻/차가움에 갈린다.
    """
    cells = {
        (segment, tp_rule): pooled(frame, timeframe, segment, lens, tp_rule)
        for segment in (PRIMARY_OOS, STRESS_OOS)
        for tp_rule in TP_RULES
    }
    if any(not c for c in cells.values()):
        return "판정 불가(OOS 데이터 없음)."

    warm_delta = _ret(cells, PRIMARY_OOS, TP_ZONE) - _ret(cells, PRIMARY_OOS, TP_ENTRY)
    cold_delta = _ret(cells, STRESS_OOS, TP_ZONE) - _ret(cells, STRESS_OOS, TP_ENTRY)
    per_symbol = trades_per_symbol(frame, timeframe, PRIMARY_OOS, lens)
    if per_symbol < MIN_TRADES_PER_SYMBOL:
        tag = (
            f"⚠️ **판정 불가(대조군)** — 따뜻한 OOS 심볼당 {per_symbol:.1f}거래로 WAN-84 유효 "
            f"기준({MIN_TRADES_PER_SYMBOL}건) 미달이다(WAN-107/130의 4h 「보류」와 같은 자리). "
            "아래 숫자는 방향을 보는 참고값이지 채택 근거가 아니다"
        )
    elif warm_delta > 0 and cold_delta > 0:
        tag = "(a) 존높이가 따뜻·차가움 둘 다에서 이긴다"
    elif warm_delta <= 0 and cold_delta <= 0:
        tag = "(b) 존높이가 어느 OOS에서도 못 이긴다 → 현행 유지"
    else:
        tag = "(c) 따뜻/차가움에 갈린다"
    return (
        f"{tag} — 심볼평균 total_return: "
        f"따뜻(주) {_ret(cells, PRIMARY_OOS, TP_ENTRY) * 100:+.2f}% → "
        f"{_ret(cells, PRIMARY_OOS, TP_ZONE) * 100:+.2f}% (Δ{warm_delta * 100:+.2f}%p) · "
        f"차가움(스트레스) {_ret(cells, STRESS_OOS, TP_ENTRY) * 100:+.2f}% → "
        f"{_ret(cells, STRESS_OOS, TP_ZONE) * 100:+.2f}% (Δ{cold_delta * 100:+.2f}%p)."
    )


def lens_note(frame: pd.DataFrame, timeframe: str) -> str:
    """체결 보수화(`pen_5bp`)에서 존높이 우위/부호가 뒤집히는지 — 목표가 먼 팔의 취약성."""
    base = pooled(frame, timeframe, PRIMARY_OOS, LENS_BASELINE, TP_ZONE)
    base_e = pooled(frame, timeframe, PRIMARY_OOS, LENS_BASELINE, TP_ENTRY)
    pen = pooled(frame, timeframe, PRIMARY_OOS, LENS_PEN, TP_ZONE)
    pen_e = pooled(frame, timeframe, PRIMARY_OOS, LENS_PEN, TP_ENTRY)
    if not base or not base_e or not pen or not pen_e:
        return "—"

    def d(z: dict[str, float | None], e: dict[str, float | None]) -> float:
        zv, ev = z.get("total_return"), e.get("total_return")
        return (0.0 if zv is None else zv) - (0.0 if ev is None else ev)

    bd, pd_ = d(base, base_e), d(pen, pen_e)
    flipped = (bd > 0) != (pd_ > 0)
    verb = "부호가 뒤집힌다" if flipped else "부호는 유지된다"
    return (
        f"존높이−현행 Δ(따뜻 OOS): baseline {bd * 100:+.2f}%p → pen_5bp {pd_ * 100:+.2f}%p "
        f"({verb})."
    )


def best_r(frame: pd.DataFrame, timeframe: str, segment: str, lens: str, tp_rule: str) -> str:
    """R 배수 스윕에서 그 구간의 최적 R(심볼평균 total_return 기준)."""
    values: list[tuple[float, float]] = []
    for r_multiple in sorted(set(frame["r_multiple"].astype(float))):
        cell = pooled(frame, timeframe, segment, lens, tp_rule, r_multiple)
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
        "lens",
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
        for lens in LENSES:
            for rule in TP_RULES:
                c = pooled(frame, timeframe, segment, lens, rule)
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
                            lens,
                            arm_label(rule),
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


def _symbol_table(frame: pd.DataFrame, timeframe: str, segment: str, lens: str) -> str:
    sub = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["lens"] == lens)
        & (frame["r_multiple"] == 1.5)
    ].copy()
    if sub.empty:
        return "(없음)"
    headers = ["symbol", "arm", "return%", "mdd%", "win%", "meanR", "trades", "TP", "SL", "EOD"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    for _, r in sub.sort_values(["symbol", "tp_rule"]).iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    _bare(str(r["symbol"])),
                    arm_label(str(r["tp_rule"])),
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
    timeframes = sorted(set(frame["timeframe"]), key=_tf_order)
    has_sweep = len(set(frame["r_multiple"].astype(float))) > 1
    lines = [
        "# WAN-206: 존높이 기준 익절을 오늘 엔진에서 재측정 — 2팔 손익 격자",
        "",
        "재현: `uv run python -m backtest.wan206_zone_height_tp_today --tf 4h` "
        "(1h·15m은 `--tf 1h --append` 등)",
        "",
        f"창 **{harness.DEFAULT_START} ~ {harness.DEFAULT_END}**(못 박은 6년) · 9종목 · 롱 온리 "
        "· 채택 기본값 그대로(존 지정가 + 오프셋 2bp + 봉내 라이브 밴드 + **존폭 필터 1.28** "
        "+ `combine_obs=False` + `unconditional`, 핀 없음). 가드 `min_stop_distance_fraction="
        f"{GUARD}` 고정(축 아님).",
        "",
        "팔: 익절 축 `entry_r`(현행 = 진입가 기준 1.5R) vs `zone_height`(제안 = 존 높이 × "
        "1.5). 렌즈 `baseline`(공식) + `pen_5bp`(스트레스 병기). OOS는 WAN-166 정본 "
        "(`oos_warm` 따뜻한 연속 = 주 수치 · `oos` 차가운 절단 = 과최적화 스트레스).",
        "",
        "> ⚠️ **WAN-143 표와 셀을 직접 비교 금지** — 창·유니버스·필터가 통째로 다르다"
        "(6종목3년 필터끔 vs 9종목6년 필터1.28).",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 손익·체결률은 **상한**이다. 존높이 팔은 "
        "목표가 멀어 슬롯을 더 오래 잠그므로 `pen_5bp`를 함께 읽는다.",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124/151/201)은 불변 — 익절 구조는 알파가 아니라 "
        "**위험의 모양**만 바꾼다(WAN-90).",
        "> ⚠️ **기본값은 바꾸지 않았다**(측정 전용). `take_profit_r=1.5`·진입가 기준 1R **유지**. "
        "존높이로의 전환은 명시적 재-베이스라인이자 **사용자 결정**이다.",
        "",
    ]
    for timeframe in timeframes:
        lines += [
            f"## {timeframe}",
            "",
            f"**판정(baseline)**: {verdict(frame, timeframe, LENS_BASELINE)}",
            "",
            f"**렌즈 민감도**: {lens_note(frame, timeframe)}",
            "",
            "**Leave-one-out(따뜻 OOS `zone_height`, baseline)**: "
            f"{leave_one_out(frame, timeframe, LENS_BASELINE, TP_ZONE)}",
            "",
            "**Leave-one-out(따뜻 OOS `entry_r` = 기준점, baseline)**: "
            f"{leave_one_out(frame, timeframe, LENS_BASELINE, TP_ENTRY)}",
            "",
            "### 렌즈 × 팔 × 구간 (9종목 심볼평균; trades·filled는 합)",
            "",
            _grid_table(frame, timeframe),
            "",
            "### 심볼별 (따뜻 OOS · baseline)",
            "",
            _symbol_table(frame, timeframe, PRIMARY_OOS, LENS_BASELINE),
            "",
        ]
        tf_multiples = set(frame[frame["timeframe"] == timeframe]["r_multiple"].astype(float))
        if has_sweep and len(tf_multiples) > 1:
            lines += [
                "### R 배수 스윕 (`zone_height`, baseline)",
                "",
                f"- IS: {best_r(frame, timeframe, SEGMENT_IS, LENS_BASELINE, TP_ZONE)}",
                f"- 따뜻 OOS: {best_r(frame, timeframe, PRIMARY_OOS, LENS_BASELINE, TP_ZONE)}",
                f"- 차가운 OOS: {best_r(frame, timeframe, STRESS_OOS, LENS_BASELINE, TP_ZONE)}",
                "",
                "⚠️ IS가 고르는 R이 OOS에서 무너지는지가 관전 포인트다(WAN-90 진입가 축의 재현). "
                "1.5가 정점 근처면 오늘 엔진에서도 살아남은 것이다.",
                "",
            ]
        elif has_sweep:
            lines += [
                f"### R 배수 스윕 — {timeframe}은 돌리지 않았다",
                "",
                f"이 TF는 `{sorted(tf_multiples)[0]:.1f}R` 한 배수만 돌았다(비용). 후속 몫이다.",
                "",
            ]
    lines += [
        "## 후보 집합 검산",
        "",
        "익절만 바꾸므로 **같은 렌즈·R·구간에서 두 익절 팔의 체결 셋업 집합은 비트 단위로 "
        "같다** — 격자가 진입시각 집합을 대조해 어긋나면 `AssertionError`로 멈춘다. `entry_r` "
        "팔(override=None)은 `harness.run_once`와 비트 단위로 일치한다"
        "(`--checksum`가 따뜻·차가움 양쪽에서 고정).",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tf_order(tf: str) -> int:
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    return order.get(tf, 99)


# --------------------------------------------------------------------------- #
# 검산 — entry_r 팔(override=None) ≡ harness.run_once
# --------------------------------------------------------------------------- #


def run_checksum(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> tuple[float, int, str]:
    """`entry_r` 팔(override=None)이 `harness.run_once`와 비트 단위로 일치하는지 검산한다.

    따뜻한 OOS(`eval_from_ms`)와 차가운 세그먼트(is/oos) 양쪽을 대조한다 — 이 팔이 실제
    프로덕션 경로와 같은 숫자를 내야 존높이 팔의 델타가 배선 인공물이 아님이 보장된다.
    """
    sym = harness.normalize_symbol(symbol)
    market = harness.load_market_data(
        sym,
        timeframe,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        need_1m=True,
        funding=True,
        db_path=db_path,
    )
    if market.empty or market.df_1m.empty:
        return 0.0, 0, "데이터 없음 — 검산 건너뜀"

    # WAN-384 명시 핀: 이 표는 존폭 필터를 켠 채(1.28) 낸 기록이다.
    params = harness.build_params(
        entry_mode="zone_limit",
        take_profit_r=1.5,
        fill=harness.BASELINE_FILL,
        max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
    )
    max_diff = 0.0
    compared = 0
    for segment in segments_for(warm_oos=True):
        seg_market = harness.slice_market(market, segment)
        if seg_market.empty or seg_market.df_1m.empty:
            continue
        eval_from_ms = harness.eval_boundary_ms(market, segment)
        obr = OrderBlockDetector(OrderBlockParams()).run(seg_market.htf_df)
        cfg = harness.legacy_build_config(seg_market.timeframe)
        # 내 entry_r 팔
        mine = run_arm(
            seg_market,
            params_fill=harness.BASELINE_FILL,
            tp_rule=TP_ENTRY,
            r_multiple=1.5,
            obr=obr,
            eval_from_ms=eval_from_ms,
        )
        mine_ret = build_result_from_trades(
            [t for _, t in mine.paired], cfg, seg_market.timeframe
        ).metrics.total_return
        # 프로덕션 경로
        outcome = harness.run_once(
            seg_market,
            params=harness.pin_invalidation_cancel(params),
            cfg=cfg,
            order_block_result=obr,
            eval_from_ms=eval_from_ms,
        )
        prod_ret = outcome.result.metrics.total_return
        max_diff = max(max_diff, abs(mine_ret - prod_ret))
        compared += 1
    if max_diff < 1e-12:
        verdict_txt = "비트 일치"
    elif max_diff < 1e-9:
        verdict_txt = "일치(부동소수 끝자리)"
    else:
        verdict_txt = "불일치 — 배선 버그"
    return max_diff, compared, verdict_txt


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
    parser.add_argument("--symbols", type=str, default=",".join(harness.LEGACY_NINE_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=harness.DEFAULT_START)
    parser.add_argument("--end", type=str, default=harness.DEFAULT_END)
    parser.add_argument(
        "--out-csv", type=str, default=str(REPORTS_DIR / "wan206_zone_height_today.csv")
    )
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan206_zone_height_today_summary.md")
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 새 TF 행을 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument(
        "--r-sweep", action="store_true", help=f"R 배수 스윕까지 돈다({R_SWEEP} — 느리다)"
    )
    parser.add_argument(
        "--no-funding-proxy", action="store_true", help="신규 3종목 펀딩 대리를 끈다"
    )
    parser.add_argument("--checksum", action="store_true", help="entry_r 팔 ≡ run_once 검산만")
    args = parser.parse_args()

    if args.checksum:
        max_diff, compared, verdict_txt = run_checksum()
        print(f"[wan206] 검산: {compared}셀 비교 · 최대 절대차 {max_diff:.2e} → {verdict_txt}")
        return

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan206] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        r_multiples = R_SWEEP if args.r_sweep else (1.5,)
        rows = run_report(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            r_multiples=r_multiples,
            funding_proxy=not args.no_funding_proxy,
        )
        if args.append and out_csv.exists():
            existing = rows_from_csv(out_csv)
            keep = [r for r in existing if r.timeframe not in set(timeframes)]
            rows = keep + list(rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows_to_frame(rows), Path(args.out_md))
    print(f"[wan206] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
