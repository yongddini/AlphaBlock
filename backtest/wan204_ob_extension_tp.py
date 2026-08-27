"""WAN-204: 익절 변형 — 진입 OB 윗경계까지 승자 연장 `max(1.5R, OB윗경계)` vs 현행 고정 1.5R.

## 질문 (사용자 아이디어 2026-07-28)

진입한 **바로 그 오더블록의 윗경계**를 익절 목표로 쓰되 현행 1.5R과 **MAX로 묶는다**:
롱 기준 `TP = max(진입가 + 1.5R, OB 윗경계)`. 최소 1.5R은 보장하고, OB 윗경계가 더 멀면
거기까지 승자를 연장한다.

📌 **진입 기하 (코드 확인, 이슈 §동기)** — 롱 진입가는 **항상 OB 윗경계 이하**다
(`strategy/models.py:deviation_entry_price` 규칙 1·2: 밴드가 존 위면 근단=OB 상단 진입 ·
존 안이면 밴드가 진입 · 존 아래면 진입 없음). 따라서 "OB 내부 체결"(규칙 2)은
OB 윗경계 > 진입가라 연장 가능하고, "근단 체결"(규칙 1, 진입가=OB 상단)은 OB 윗경계=진입가라
`max`가 자동으로 1.5R을 준다. → **「내부체결만 적용」과 「전체적용」은 같은 결과**다(max가
게이트를 자동 수행). 사용자 원문(내부체결)대로 문서화하되 전체적용과 동치임을 명시한다.

## 선행 측정과 다른 점 (오늘 엔진에서 안 쟀다)

* WAN-137(저항-OB 익절)은 **위쪽 별개 약세 OB**에 판다 — 진입한 그 존이 아니다.
* WAN-143/155(존높이 익절)은 1R=존높이로 잰다 — OB 경계를 **절대 목표가**로 쓰지 않는다.
* 이 변형은 **진입한 그 OB의 윗경계를 절대 목표가로, 1.5R floor와 함께** 쓰는 새 규칙.

## 작업 범위 — 새 파이프라인 금지, `take_profit_override` 재사용

WAN-143 §0이 봉내 라이브 밴드에 배선한 `take_profit_override`(목표가 하나)로
`max(1.5R, OB윗경계)`를 표현한다. `None`이면 기존 엔진과 **비트 단위로 같다**(회귀 테스트가
동작으로 고정). 팔:

* **(A) `fixed_1.5r`** — 현행 고정 1.5R(= 오늘의 채택 기본값 그대로, override=None).
* **(B) `ob_extend`** — `max(1.5R, OB윗경계)`.

9종목 × 못 박은 6년(2020-09-15~2026-07-22) × 15m·1h·4h × 구간 × 렌즈. 오늘 채택 기본값
(필터 1.28 · `intrabar_live` · `unconditional` · 오프셋 2bp · 분리 존)에서 출발 — **핀 금지**
(`harness.build_params`/`detect_order_blocks` 기본값 = CLI와 같은 조립 경로).

## 구간 규약 — 따뜻한 연속 OOS가 정본 (WAN-166)

정본 OOS는 `oos_warm`(따뜻, 주 수치)이고 차가운 `oos`는 과최적화 스트레스로 병기한다. 이
모듈은 하네스의 세그먼트 기계(`segments_for(warm_oos=True)` + `slice_market` +
`eval_boundary_ms`)를 그대로 써서 `full`·`is`·`oos_warm`·`oos` 네 구간을 낸다 — 따뜻한
구간의 후보 필터(`trigger_time >= 평가경계`)는 `run_zone_limit_backtest_verbose`의 내부
로직을 글자 그대로 복제한 것이라, override=None 팔은 표준 CLI(`backtest.run --oos-warm`)와
비트 단위로 같다(회귀 테스트가 실데이터로 고정).

## 검산 (동작으로 고정)

* 익절은 **청산만** 바꾸고 진입·체결 판정에는 안 쓰이므로, 같은 (구간, 렌즈)의 두 팔은
  **체결 셋업(진입 시각) 집합이 비트 단위로 같아야 한다** — 어긋나면 배선 버그
  (`AssertionError`).
* override=None 팔 ≡ `harness.run_once`(= 표준 CLI) — 실데이터 회귀 테스트.

⚠️ 전부 `baseline`(낙관 체결) 위 값이라 상한이다 · `pen_5bp` 체결 보수화를 병기한다 ·
「엣지 없음」(WAN-84/88/111/114/124/151)은 불변(익절 자 변경이지 진입 신호 검정이 아니다) ·
익절 자는 알파가 아니라 **위험의 모양**만 바꾼다(WAN-90) — "이겼다"보다 위험조정·부호로 읽는다.

⚠️ **펀딩 대리 미적용**(널·측정 계열 관행, WAN-201). 신규 3종목(DOGE·LINK·LTC)은 이 창에서
펀딩 0행이라 커버리지 0%다 — 연장 팔(B)은 목표가 멀어 **홀드가 길므로** 그 종목에서 펀딩이
과소 계상돼 A 대비 소폭 유리하다. leave-one-out으로 신규 종목 기여를 갈라 읽는다.

재현:

```
uv run python -m backtest.wan204_ob_extension_tp --tf 4h            # 4h 먼저(가벼움)
uv run python -m backtest.wan204_ob_extension_tp --tf 1h --append
uv run python -m backtest.wan204_ob_extension_tp --tf 15m --append  # 15m 뒤에(무겁다)
uv run python -m backtest.wan204_ob_extension_tp --from-csv         # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    FillPreset,
    MarketData,
    fill_preset,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    TakeProfitContext,
    TakeProfitOverride,
    ZoneLimitStats,
    _Candidate,
    _resolve_take_profit,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockResult

REPORTS_DIR = Path("backtest/reports")

# 채택 좌표 — 9종목 × 못 박은 6년 × 15m·1h·4h (WAN-182). 핀을 쓰지 않는다.
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
DEFAULT_SYMBOLS = harness.LEGACY_NINE_SYMBOLS
DEFAULT_TIMEFRAMES = harness.DEFAULT_TIMEFRAMES
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

ARM_FIXED = "fixed_1.5r"
"""팔 A — 현행 고정 1.5R(override=None = 오늘의 채택 기본값 그대로)."""
ARM_EXTEND = "ob_extend"
"""팔 B — `max(1.5R, OB윗경계)`."""
ARMS: tuple[str, ...] = (ARM_FIXED, ARM_EXTEND)

#: 공식 렌즈(WAN-128) + 체결 보수화 병기(이슈 완료기준). `baseline`이 판정 렌즈다.
DEFAULT_FILLS: tuple[str, ...] = ("baseline", "pen_5bp")
BASELINE = "baseline"

SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

MIN_TRADES_PER_SYMBOL = 20
"""WAN-84 유효 기준 — 이 미만인 (심볼, 셀)은 심볼평균에서 제외한다(제외 수 병기)."""

MIN_SYMBOLS_FOR_VERDICT = 3
"""유효 심볼이 이보다 적으면 (a)/(b)/(c) 대신 「판정 불가」(WAN-142/143/152/155 관행)."""

TRADE_GAP_DEMOTE = 0.05
"""두 팔의 시퀀싱 거래 수 차이가 이 비율을 넘으면 판정을 강등한다 — 연장 팔은 목표가 멀어
동시 1포지션 슬롯을 더 오래 잠가 표본을 갈라놓는다(이슈 §함정)."""


# --------------------------------------------------------------------------- #
# 익절 오버라이드 — max(1.5R floor, OB 윗경계)
# --------------------------------------------------------------------------- #


def make_ob_extension_override(params: ConfluenceParams) -> TakeProfitOverride:
    """익절 = `max(진입가+1.5R, OB 윗경계)`(롱) / `min(진입가−1.5R, OB 아랫경계)`(숏).

    floor(1.5R)는 현행 규칙(`_resolve_take_profit`)을 그대로 불러 계산한다 — 그래야
    OB 경계가 연장을 못 할 때 팔 B의 목표가 팔 A와 **정확히 같아진다**(지어낸 값이 아님).
    floor가 None이면(1R을 못 잼) 연장도 하지 않는다(그 셋업은 익절 목표 없음 = 무효화 홀딩,
    현행과 동일 처리 — WAN-137/143 폴백 관행).
    """

    def resolve(ctx: TakeProfitContext) -> float | None:
        floor = _resolve_take_profit(params, ctx.is_long, ctx.entry_price, ctx.stop_price, [])
        if floor is None:
            return None
        boundary = ctx.order_block.top if ctx.is_long else ctx.order_block.bottom
        return max(floor, boundary) if ctx.is_long else min(floor, boundary)

    return resolve


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class Wan204Row(BaseModel):
    """한 (심볼, TF, 구간, 팔, 렌즈) 셀.

    📌 두 팔의 `mean_net_r`은 직접 비교 가능하다 — 익절 목표만 바뀌고 손절(무효화 경계)이
    두 팔에서 같아 1R(리스크 금액)이 셋업 단위로 동일하다(WAN-155와 같은 상황).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    fill: str
    eligible: int
    filled: int
    """체결 셋업 수(시퀀싱 이전). 같은 (구간, 렌즈)이면 팔과 무관하게 같아야 한다."""
    num_trades: int
    fill_rate: float | None
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float | None
    mean_gross_r: float | None
    mean_net_r: float | None
    net_r_win: float | None
    """승자 거래의 평균 실현 net R — 연장이 평균 이익을 키우는지의 계량(이슈 §분해)."""
    net_r_loss: float | None
    hold_bars_median: float | None
    """거래당 보유 기간(상위TF 봉 수) 중앙값 — 슬롯 점유(이슈 §함정)의 계량."""
    n_take_profit: int
    n_stop_loss: int
    n_end_of_data: int


def _risk_per_unit(is_long: bool, entry_price: float, stop_price: float) -> float:
    return entry_price - stop_price if is_long else stop_price - entry_price


def _gross_r(cand: _Candidate) -> float | None:
    """비용 반영 전 실현 R = 부호(청산가 − 진입가) / 1R. 1R을 못 재면 None."""
    risk = _risk_per_unit(cand.side is PositionSide.LONG, cand.entry_price, cand.stop_price)
    if risk <= 0:
        return None
    return cand.side.sign * (cand.exit_price - cand.entry_price) / risk


def _net_r(cand: _Candidate, trade: Trade) -> float | None:
    """거래당 실현 net R = 실현 손익 ÷ 그 거래의 리스크 금액(WAN-154 §1′ 산식).

    리스크 금액 = 수량 × |체결가 − 손절가|. 익절 자만 다르고 손절은 두 팔이 같으므로 두 팔의
    net R은 직접 비교 가능하다.
    """
    risk_per_unit = _risk_per_unit(
        cand.side is PositionSide.LONG, trade.entry_price, cand.stop_price
    )
    risk_amount = risk_per_unit * trade.quantity
    if risk_amount <= 0:
        return None
    return trade.realized_pnl / risk_amount


def build_row(
    market: MarketData,
    segment: str,
    arm: str,
    fill: str,
    paired: list[tuple[_Candidate, Trade]],
    cfg: BacktestConfig,
    *,
    eligible: int,
    filled: int,
    htf_ms: int,
) -> Wan204Row:
    trades = [t for _, t in paired]
    metrics = build_result_from_trades(trades, cfg, market.timeframe).metrics
    reasons = Counter(cand.reason for cand, _ in paired)
    grs = [g for g in (_gross_r(cand) for cand, _ in paired) if g is not None]
    net_rs = [r for r in (_net_r(cand, t) for cand, t in paired) if r is not None]
    net_wins = [r for r in net_rs if r > 0]
    net_losses = [r for r in net_rs if r <= 0]
    holds = [(t.exit_time - t.entry_time) / htf_ms for _, t in paired if t.exit_time > t.entry_time]
    return Wan204Row(
        symbol=market.symbol,
        timeframe=market.timeframe,
        segment=segment,
        arm=arm,
        fill=fill,
        eligible=eligible,
        filled=filled,
        num_trades=metrics.num_trades,
        fill_rate=(filled / eligible) if eligible else None,
        total_return=metrics.total_return,
        max_drawdown=metrics.max_drawdown,
        win_rate=metrics.win_rate,
        sharpe=metrics.sharpe,
        mean_gross_r=statistics.fmean(grs) if grs else None,
        mean_net_r=statistics.fmean(net_rs) if net_rs else None,
        net_r_win=statistics.fmean(net_wins) if net_wins else None,
        net_r_loss=statistics.fmean(net_losses) if net_losses else None,
        hold_bars_median=statistics.median(holds) if holds else None,
        n_take_profit=reasons.get(ExitReason.TAKE_PROFIT, 0),
        n_stop_loss=reasons.get(ExitReason.STOP_LOSS, 0),
        n_end_of_data=reasons.get(ExitReason.END_OF_DATA, 0),
    )


# --------------------------------------------------------------------------- #
# 셀 — 한 (심볼, TF)의 구간 × 렌즈 × 팔
# --------------------------------------------------------------------------- #


def run_cell(
    market: MarketData,
    *,
    fills: Sequence[FillPreset],
    log: bool = True,
) -> list[Wan204Row]:
    """한 (심볼, TF)의 구간 × 렌즈 × 팔.

    후보 생성(비싼 부분)은 (구간, 렌즈, 팔)마다 한 번이다 — 익절 자만 다르면 청산이 달라
    서브스텝 시뮬을 공유할 수 없다. 오더블록 탐지는 구간(창)당 한 번만 하고 렌즈·팔이
    공유한다. 따뜻한 구간(`oos_warm`)의 후보 필터는 `run_zone_limit_backtest_verbose`의
    내부 로직(`trigger_time >= 평가경계`)을 그대로 복제한다.
    """
    htf_ms = timeframe_to_ms(market.timeframe)
    segments = harness.segments_for(warm_oos=True)
    ob_cache: dict[tuple[float, float], OrderBlockResult] = {}
    rows: list[Wan204Row] = []
    # 같은 창(start·end 비율)·렌즈·팔의 후보 빌드를 캐시한다 — `full`(0,1)과 `oos_warm`(0,1,
    # 평가경계)이 **같은 전 구간 빌드**를 공유하므로(따뜻한 구간은 slice_market이 항등이고
    # 평가경계 필터만 뒤에 얹는다), 가장 비싼 서브스텝 시뮬을 두 번 돌리지 않는다. 결과는
    # 비트 단위로 같다(회귀 테스트가 override=None 팔로 고정). `is`·`oos`는 물리 절단이라
    # 창 비율이 달라 각자 빌드한다.
    build_cache: dict[
        tuple[float, float, str, str],
        tuple[list[_Candidate], list[SetupDiagnostic], ZoneLimitStats],
    ] = {}
    for segment in segments:
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        eval_ms = harness.eval_boundary_ms(window, segment)
        ob_key = (segment.start_fraction, segment.end_fraction)
        if ob_key not in ob_cache:
            ob_cache[ob_key] = harness.detect_order_blocks(window)
        obr = ob_cache[ob_key]
        cfg = harness.legacy_build_config(window.timeframe, funding_enabled=True)
        for fill in fills:
            # WAN-384 명시 핀: 이 표는 존폭 필터를 켠 채(1.28) 낸 기록이다.
            params = harness.build_params(
                fill=fill, max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON
            )
            filled_entries: dict[str, list[int]] = {}
            for arm in ARMS:
                t0 = time.time()
                override = None if arm == ARM_FIXED else make_ob_extension_override(params)
                build_key = (segment.start_fraction, segment.end_fraction, fill.name, arm)
                cached = build_cache.get(build_key)
                if cached is None:
                    sink: list[SetupDiagnostic] = []
                    built, stats = build_zone_limit_candidates(
                        window.htf_df,
                        window.df_1m,
                        window.timeframe,
                        params=params,
                        cfg=cfg,
                        order_block_result=obr,
                        take_profit_override=override,
                        setup_sink=sink,
                    )
                    build_cache[build_key] = (built, sink, stats)
                else:
                    built, sink, stats = cached
                candidates = built
                if eval_ms is not None:
                    candidates = [c for c in candidates if c.trigger_time >= eval_ms]
                    kept = [d for d in sink if d.trigger_time >= eval_ms]
                    eligible = len(kept)
                    filled = sum(1 for d in kept if d.filled)
                else:
                    eligible, filled = stats.eligible, stats.filled
                filled_entries[arm] = sorted(c.entry_time for c in candidates)
                paired = sequence_with_candidates(candidates, cfg, window.funding_rates)
                rows.append(
                    build_row(
                        window,
                        segment.name,
                        arm,
                        fill.name,
                        paired,
                        cfg,
                        eligible=eligible,
                        filled=filled,
                        htf_ms=htf_ms,
                    )
                )
                if log:
                    print(
                        f"[wan204] {window.symbol} {window.timeframe} {segment.name} "
                        f"{fill.name} {arm}: 후보 {len(candidates)} ({time.time() - t0:.0f}s)",
                        flush=True,
                    )
            # 검산: 익절 자만 바뀌므로 두 팔의 체결 셋업 집합은 비트 단위로 같아야 한다.
            if filled_entries[ARM_FIXED] != filled_entries[ARM_EXTEND]:
                raise AssertionError(
                    f"후보 집합 불일치 — {window.symbol} {window.timeframe} {segment.name} "
                    f"{fill.name}: {len(filled_entries[ARM_EXTEND])} vs "
                    f"{len(filled_entries[ARM_FIXED])}. 익절이 진입을 바꾸는 배선 버그다."
                )
    return rows


def run_report(
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = ("4h",),
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    fills: Sequence[str] = DEFAULT_FILLS,
    log: bool = True,
) -> list[Wan204Row]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    fill_presets = [fill_preset(name) for name in fills]
    rows: list[Wan204Row] = []
    for timeframe in timeframes:
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=True
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan204] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            rows.extend(run_cell(market, fills=fill_presets, log=log))
    return rows


def merge_rows(existing: Sequence[Wan204Row], new: Sequence[Wan204Row]) -> list[Wan204Row]:
    """좌표(심볼·TF·구간·팔·렌즈)가 같은 행은 새 행이 이긴다 — `--append`의 병합."""

    def key(r: Wan204Row) -> tuple[str, str, str, str, str]:
        return (r.symbol, r.timeframe, r.segment, r.arm, r.fill)

    new_keys = {key(r) for r in new}
    return [r for r in existing if key(r) not in new_keys] + list(new)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def _subset(
    frame: pd.DataFrame, timeframe: str, segment: str, arm: str, fill: str = BASELINE
) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["arm"] == arm)
        & (frame["fill"] == fill)
    ]


def pooled(
    frame: pd.DataFrame, timeframe: str, segment: str, arm: str, fill: str = BASELINE
) -> dict[str, float | None]:
    """심볼평균 — 거래 20건 미만 셀은 제외하고 제외 수를 병기한다(WAN-84 게이트).

    수익률·MDD 등은 유효 심볼 단순평균, 거래·청산 사유는 유효 심볼 합이다.
    """
    sub = _subset(frame, timeframe, segment, arm, fill)
    if sub.empty:
        return {}
    valid = sub[sub["num_trades"] >= MIN_TRADES_PER_SYMBOL]
    excluded = sub[sub["num_trades"] < MIN_TRADES_PER_SYMBOL]
    if valid.empty:
        return {"n_symbols": 0.0, "n_excluded": float(len(excluded))}

    def avg(col: str) -> float | None:
        vals = valid[col].astype(float).dropna()
        return float(vals.mean()) if len(vals) else None

    ret, mdd = avg("total_return"), avg("max_drawdown")
    tp = int(valid["n_take_profit"].sum())
    sl = int(valid["n_stop_loss"].sum())
    eod = int(valid["n_end_of_data"].sum())
    closed = tp + sl + eod
    return {
        "n_symbols": float(len(valid)),
        "n_excluded": float(len(excluded)),
        "total_return": ret,
        "max_drawdown": mdd,
        "ret_over_mdd": (ret / mdd) if (ret is not None and mdd) else None,
        "win_rate": avg("win_rate"),
        "mean_net_r": avg("mean_net_r"),
        "net_r_win": avg("net_r_win"),
        "net_r_loss": avg("net_r_loss"),
        "mean_gross_r": avg("mean_gross_r"),
        "hold_bars_median": avg("hold_bars_median"),
        "fill_rate": avg("fill_rate"),
        "num_trades": float(valid["num_trades"].sum()),
        "filled": float(valid["filled"].sum()),
        "n_take_profit": float(tp),
        "n_stop_loss": float(sl),
        "n_end_of_data": float(eod),
        "tp_rate": (tp / closed) if closed else None,
        "stop_rate": (sl / closed) if closed else None,
        "eod_rate": (eod / closed) if closed else None,
        "n_positive": float((valid["total_return"].astype(float) > 0).sum()),
    }


def leave_one_out(
    frame: pd.DataFrame,
    timeframe: str,
    arm: str,
    segment: str = SEGMENT_OOS_WARM,
    fill: str = BASELINE,
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인(이슈 필수 축).

    게이트(20거래)를 통과한 유효 심볼 안에서만 뺀다.
    """
    sub = _subset(frame, timeframe, segment, arm, fill)
    sub = sub[sub["num_trades"] >= MIN_TRADES_PER_SYMBOL]
    if sub.empty:
        return "—"
    parts: list[str] = []
    for _, drop in sub.iterrows():
        rest = sub[sub["symbol"] != drop["symbol"]]["total_return"].astype(float)
        if len(rest):
            parts.append(f"−{_bare(str(drop['symbol']))} {rest.mean() * 100:+.2f}%")
    return " · ".join(parts)


def trade_gap(
    frame: pd.DataFrame, timeframe: str, segment: str = SEGMENT_OOS_WARM, fill: str = BASELINE
) -> float | None:
    """두 팔의 시퀀싱 거래 수 상대 차이 (연장 − 현행)/현행 — 슬롯 잠금의 계량."""
    a = pooled(frame, timeframe, segment, ARM_FIXED, fill)
    b = pooled(frame, timeframe, segment, ARM_EXTEND, fill)
    ta, tb = a.get("num_trades"), b.get("num_trades")
    if not ta or tb is None:
        return None
    return (tb - ta) / ta


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


class VerdictKind(StrEnum):
    EXTEND = "extend"  # (a) 연장 팔이 이긴다
    FIXED = "fixed"  # (b) 현행이 이긴다
    MIXED = "mixed"  # 지표가 갈린다
    INDETERMINATE = "indeterminate"  # 표본 게이트 미달


class TfVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: VerdictKind
    demoted: bool
    text: str


def tf_verdict(frame: pd.DataFrame, timeframe: str, fill: str = BASELINE) -> TfVerdict:
    """따뜻한 OOS(주 수치) 심볼평균으로 연장 팔 채택 근거를 가른다.

    `total_return`만으로 내지 않는다(WAN-90/137/155 전례 — 「raw만 승, 위험조정하면 증발」):
    수익 · 수익/MDD · `mean_net_r` 세 지표가 **모두 같은 방향**일 때만 (a)/(b)이고 갈리면 (c)다.
    표본 게이트(유효 심볼 3개 미만)면 판정하지 않고, 두 팔의 거래 수 차이가 5%를 넘으면
    강등한다(연장 팔의 슬롯 잠금이 표본을 갈라놓았다는 뜻).
    """
    fixed = pooled(frame, timeframe, SEGMENT_OOS_WARM, ARM_FIXED, fill)
    extend = pooled(frame, timeframe, SEGMENT_OOS_WARM, ARM_EXTEND, fill)
    n_valid = min(fixed.get("n_symbols") or 0.0, extend.get("n_symbols") or 0.0)
    if n_valid < MIN_SYMBOLS_FOR_VERDICT:
        return TfVerdict(
            kind=VerdictKind.INDETERMINATE,
            demoted=False,
            text=(
                f"**{timeframe}**: ⚠️ **판정 불가** — 유효 심볼(거래 "
                f"{MIN_TRADES_PER_SYMBOL}건 이상)이 {n_valid:.0f}개로 "
                f"{MIN_SYMBOLS_FOR_VERDICT}개 미만이다."
            ),
        )
    gap = trade_gap(frame, timeframe, fill=fill)
    demoted = gap is not None and abs(gap) > TRADE_GAP_DEMOTE
    metrics = ("total_return", "ret_over_mdd", "mean_net_r")
    directions = {
        name: (b > f)
        for name in metrics
        if (b := extend.get(name)) is not None and (f := fixed.get(name)) is not None
    }
    numbers = (
        f"OOS(따뜻) 심볼평균({n_valid:.0f}심볼) total_return "
        f"{_fmt_pct(fixed.get('total_return'))} → {_fmt_pct(extend.get('total_return'))} · "
        f"수익/MDD {_fmt_num(fixed.get('ret_over_mdd'))} → "
        f"{_fmt_num(extend.get('ret_over_mdd'))} · "
        f"mean_net_r {_fmt_num(fixed.get('mean_net_r'), '.3f')} → "
        f"{_fmt_num(extend.get('mean_net_r'), '.3f')}"
    )
    gap_txt = "" if gap is None else f" 거래 수 차이 {gap * 100:+.1f}%."
    demote_txt = (
        f" 🚨 **판정 강등** — 두 팔의 거래 수 차이가 {TRADE_GAP_DEMOTE:.0%}를 넘는다"
        "(연장 팔의 슬롯 잠금이 표본을 갈라놓았다). 방향 참고까지만."
        if demoted
        else ""
    )
    if len(directions) < 3:
        kind, head = VerdictKind.MIXED, "(c) **판정 지표 결손**"
    elif all(directions.values()):
        kind, head = VerdictKind.EXTEND, "(a) **연장 팔이 이긴다** — 세 지표 전부"
    elif not any(directions.values()):
        kind, head = VerdictKind.FIXED, "(b) **현행(고정 1.5R)이 이긴다** — 세 지표 전부"
    else:
        won = [k for k, v in directions.items() if v]
        kind, head = (
            VerdictKind.MIXED,
            f"(c) **지표가 갈린다** — 연장 우위는 {', '.join(f'`{w}`' for w in won)}뿐",
        )
    return TfVerdict(
        kind=kind, demoted=demoted, text=f"**{timeframe}**: {head}. {numbers}.{gap_txt}{demote_txt}"
    )


def overall_verdict(frame: pd.DataFrame, timeframes: Sequence[str]) -> str:
    """(a)/(b)/(c) 종합 — 판정 대상은 「연장 팔을 채택할 근거가 있는가」다."""
    verdicts = {tf: tf_verdict(frame, tf) for tf in timeframes}
    kinds = {v.kind for v in verdicts.values()}
    known = {k for k in kinds if k is not VerdictKind.INDETERMINATE}
    if not known:
        head = "⚠️ **판정 불가** — 표본 게이트 미달"
    elif known == {VerdictKind.EXTEND}:
        head = "**(a) 연장 팔 채택 권고 후보** — 작업 TF 전부 세 지표에서 이긴다"
    elif VerdictKind.EXTEND in known:
        head = "**(c) TF에 갈린다** — 하나의 기본값으로 둘 다 좋게 할 수 없다(WAN-143/155 자리)"
    elif VerdictKind.FIXED in known:
        head = (
            "**(b) 현행 유지** — 연장 팔이 어느 TF에서도 이기지 못한다"
            "(세 지표 전부 지거나, 지표가 갈려 우위가 아니다)"
        )
    else:
        head = "**(c) 지표가 갈린다** — 어느 TF에서도 세 지표가 한 방향이 아니다(0 언저리)"
    if VerdictKind.INDETERMINATE in kinds and known:
        head += " · ⚠️ 일부 TF는 표본 게이트로 판정 불가"
    if any(v.demoted for v in verdicts.values()):
        head += " · 🚨 일부 TF는 거래 수 차이 5% 초과로 **강등된 판정**이다"
    return head


def decomposition(frame: pd.DataFrame, timeframe: str, fill: str = BASELINE) -> str:
    """무엇이 움직이나 — 승자 이익·승률·홀드·청산 사유로 A→B 변화를 가른다(이슈 §분해)."""
    a = pooled(frame, timeframe, SEGMENT_OOS_WARM, ARM_FIXED, fill)
    b = pooled(frame, timeframe, SEGMENT_OOS_WARM, ARM_EXTEND, fill)
    if not a or not b or a.get("total_return") is None:
        return "계측 불가(표본 부족)."

    def d(name: str, scale: float = 1.0, fmt: str = ".3f") -> str:
        av, bv = a.get(name), b.get(name)
        if av is None or bv is None:
            return "—"
        return f"{av * scale:{fmt}} → {bv * scale:{fmt}} (Δ{(bv - av) * scale:+{fmt}})"

    return (
        f"승자 net R {d('net_r_win')} · 승률 {d('win_rate', 100, '.2f')}%p · "
        f"홀드(봉) {d('hold_bars_median', 1, '.1f')} · "
        f"청산 사유 익절 {d('tp_rate', 100, '.1f')}%p · 손절 {d('stop_rate', 100, '.1f')}%p · "
        f"EOD {d('eod_rate', 100, '.1f')}%p. 연장이 승자 이익을 키우는 대신 승률·슬롯을 얼마나 "
        "깎는지, 못 닿은 거래가 손절로 끝나는지 EOD로 남는지가 여기서 읽힌다."
    )


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.2f}%"


def _fmt_num(v: float | None, fmt: str = ".2f") -> str:
    return "—" if v is None else format(v, fmt)


def _fmt_rate(cell: dict[str, float | None], name: str) -> str:
    v = cell.get(name)
    return "—" if v is None else f"{v * 100:.1f}"


_GRID_COLS = (
    "segment",
    "arm",
    "return%",
    "mdd%",
    "ret/mdd",
    "win%",
    "netR",
    "net승R",
    "홀드",
    "trades",
    "fill%",
    "TP%",
    "SL%",
    "EOD%",
    "+심볼(제외)",
)


def _grid_table(frame: pd.DataFrame, timeframe: str, fill: str) -> str:
    lines = [
        "| " + " | ".join(_GRID_COLS) + " |",
        "| " + " | ".join("--" for _ in _GRID_COLS) + " |",
    ]
    for segment in SEGMENT_ORDER:
        for arm in ARMS:
            c = pooled(frame, timeframe, segment, arm, fill)
            if not c:
                continue
            n_sym, n_exc = c.get("n_symbols"), c.get("n_excluded")
            if not n_sym:
                sym_txt = f"0({n_exc:.0f} 제외)" if n_exc else "0"
                lines.append(
                    f"| {segment} | {arm} | "
                    + " | ".join("—" for _ in range(len(_GRID_COLS) - 3))
                    + f" | {sym_txt} |"
                )
                continue
            n_pos = c.get("n_positive")
            lines.append(
                "| "
                + " | ".join(
                    [
                        segment,
                        arm,
                        _fmt_pct(c.get("total_return")),
                        _fmt_pct(c.get("max_drawdown")),
                        _fmt_num(c.get("ret_over_mdd")),
                        _fmt_pct(c.get("win_rate")),
                        _fmt_num(c.get("mean_net_r"), ".3f"),
                        _fmt_num(c.get("net_r_win"), ".3f"),
                        _fmt_num(c.get("hold_bars_median"), ".1f"),
                        _fmt_num(c.get("num_trades"), ".0f"),
                        _fmt_rate(c, "fill_rate"),
                        _fmt_rate(c, "tp_rate"),
                        _fmt_rate(c, "stop_rate"),
                        _fmt_rate(c, "eod_rate"),
                        f"{0 if n_pos is None else int(n_pos)}/{int(n_sym)}"
                        + (f"({int(n_exc)} 제외)" if n_exc else ""),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def _symbol_table(frame: pd.DataFrame, timeframe: str, fill: str = BASELINE) -> str:
    sub = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == SEGMENT_OOS_WARM)
        & (frame["fill"] == fill)
    ].copy()
    if sub.empty:
        return "(없음)"
    headers = [
        "symbol",
        "arm",
        "return%",
        "mdd%",
        "win%",
        "netR",
        "홀드",
        "trades",
        "TP",
        "SL",
        "EOD",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    for _, r in sub.sort_values(["symbol", "arm"]).iterrows():
        hold = r["hold_bars_median"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _bare(str(r["symbol"])),
                    str(r["arm"]),
                    _fmt_pct(float(r["total_return"])),
                    _fmt_pct(float(r["max_drawdown"])),
                    _fmt_pct(float(r["win_rate"])),
                    _fmt_num(float(r["mean_net_r"]) if pd.notna(r["mean_net_r"]) else None, ".3f"),
                    _fmt_num(float(hold) if pd.notna(hold) else None, ".1f"),
                    str(int(r["num_trades"])),
                    str(int(r["n_take_profit"])),
                    str(int(r["n_stop_loss"])),
                    str(int(r["n_end_of_data"])),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def build_summary_markdown(rows: Sequence[Wan204Row], *, timeframes: Sequence[str]) -> str:
    frame = rows_to_frame(rows)
    symbols = sorted({_bare(r.symbol) for r in rows}) or ["—"]
    fills_seen = list(dict.fromkeys(r.fill for r in rows))
    lines: list[str] = [
        "# WAN-204: 익절 변형 — OB 윗경계까지 승자 연장 max(1.5R, OB윗경계) vs 현행 고정 1.5R",
        "",
        "재현: `uv run python -m backtest.wan204_ob_extension_tp --tf 4h` → "
        "`--tf 1h --append` → `--tf 15m --append` (요약만: `--from-csv`)",
        "",
        f"{len(symbols)}심볼({'/'.join(symbols)}) × {'·'.join(timeframes)} × 구간, 못 박은 창 "
        f"**{DEFAULT_START} ~ {DEFAULT_END}**, 렌즈 **{'·'.join(fills_seen)}**"
        "(판정은 `baseline`). 오늘의 채택 기본값(오프셋 2bp · `intrabar_live` · "
        "`unconditional` · 롱 온리 · 분리 존 · 존폭 필터 1.28) — **핀 없음**.",
        "",
        "팔: **A `fixed_1.5r`**(현행 고정 1.5R = override 없음) vs **B `ob_extend`**"
        "(`max(진입가+1.5R, OB 윗경계)`). 롱 진입가 ≤ OB 윗경계라 「내부체결만 연장」과 "
        "「전체 적용」이 동치다(max가 게이트를 수행).",
        "",
        "구간: `full`·`is`·**`oos_warm`(따뜻, 주 수치)**·`oos`(차가움, 과최적화 스트레스) "
        "— WAN-166 정본 규약.",
        "",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 손익은 **상한**이다. `pen_5bp`(관통 5bp "
        "요구)를 체결 보수화로 병기했다.",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124/151)은 불변 — 익절 자는 알파가 아니라 "
        '**위험의 모양**만 바꾼다(WAN-90). "이겼다"보다 위험조정·부호로 읽는다.',
        "> ⚠️ **펀딩 대리 미적용**(널·측정 계열 관행) — 신규 3종목(DOGE·LINK·LTC)은 이 창에서 "
        "펀딩 0행이라 커버리지 0%다. 연장 팔은 홀드가 길어 그 종목에서 펀딩이 과소 계상돼 "
        "A 대비 소폭 유리하다(leave-one-out으로 갈라 읽는다).",
        '> ⚠️ **기본값·토대 불변**(측정 전용, `take_profit_r=1.5`·`take_profit_mode="fixed_r"` '
        "유지, `ALPHABLOCK_LIVE_TRADING=false`). **채택은 별도 재-베이스라인 결정이자 사용자 "
        "결정이다.**",
        "",
        "## 종합 판정",
        "",
        overall_verdict(frame, timeframes),
        "",
    ]
    for timeframe in timeframes:
        v = tf_verdict(frame, timeframe)
        v_pen = tf_verdict(frame, timeframe, fill="pen_5bp")
        lines += [
            f"## {timeframe}",
            "",
            f"**판정(baseline)**: {v.text}",
            "",
            f"**무엇이 움직이나(OOS 따뜻·baseline)**: {decomposition(frame, timeframe)}",
            "",
            f"**체결 보수화(pen_5bp) 판정**: {v_pen.text}",
            "",
            "**Leave-one-out(OOS 따뜻·baseline)** — 편중(특히 ETH·SOL) 확인:",
            "",
            f"- `ob_extend`: {leave_one_out(frame, timeframe, ARM_EXTEND)}",
            f"- `fixed_1.5r`: {leave_one_out(frame, timeframe, ARM_FIXED)}",
            "",
            "### 구간 × 팔 (baseline · 6심볼 심볼평균 — 거래 20건 미만 셀 제외)",
            "",
            _grid_table(frame, timeframe, BASELINE),
            "",
            "### 구간 × 팔 (pen_5bp — 체결 보수화)",
            "",
            _grid_table(frame, timeframe, "pen_5bp"),
            "",
            "### 심볼별 (OOS 따뜻 · baseline)",
            "",
            _symbol_table(frame, timeframe),
            "",
        ]
    lines += [
        "## 후보 집합 검산",
        "",
        "익절 자만 청산을 바꾸므로 **같은 (구간, 렌즈)의 두 팔은 체결 셋업 집합이 비트 단위로 "
        "같다** — 격자가 진입 시각 집합을 대조해 어긋나면 `AssertionError`로 멈춘다. 표의 "
        "`trades`(시퀀싱 후)가 팔마다 다른 것은 **관찰**이다: 연장 목표가 멀수록 동시 1포지션 "
        "슬롯이 더 오래 잠긴다(`홀드` 열). override=None 팔이 표준 CLI(`backtest.run "
        "--oos-warm`)와 비트 단위로 같음은 실데이터 회귀 테스트가 고정한다.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[Wan204Row]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[Wan204Row]:
    frame = pd.read_csv(path)
    return [Wan204Row.model_validate(rec) for rec in frame.to_dict(orient="records")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default="4h", help="콤마로 여러 개(예: 4h,1h,15m)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--fills", type=str, default=",".join(DEFAULT_FILLS))
    parser.add_argument("--out-csv", type=str, default=str(REPORTS_DIR / "wan204_ob_extension.csv"))
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan204_ob_extension_summary.md")
    )
    parser.add_argument(
        "--append", action="store_true", help="기존 CSV에 병합(좌표가 같은 행은 새 행이 이긴다)"
    )
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan204] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        fills = tuple(s.strip() for s in args.fills.split(",") if s.strip())
        rows = run_report(symbols, timeframes, start=args.start, end=args.end, fills=fills)
        if args.append and out_csv.exists():
            rows = merge_rows(rows_from_csv(out_csv), rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    timeframes_seen = list(dict.fromkeys(r.timeframe for r in rows))
    Path(args.out_md).write_text(
        build_summary_markdown(rows, timeframes=timeframes_seen), encoding="utf-8"
    )
    print(f"[wan204] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
