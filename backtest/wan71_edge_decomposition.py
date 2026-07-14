"""엣지의 소재지 분해 — 청산 캐리 · 시장 베타 · 비용 취약성 세 가설 검정 (WAN-71).

배경은 이슈 WAN-71 본문 참고. WAN-70(PR #53)은 B안 엔진(`backtest.zone_limit_backtest`)
그대로, 실제 거래의 방향·시각대 구성을 맞춘 매칭 널로 "오더블록+RSI 진입 타이밍에
유의한 엣지가 있는가"에 **엣지 없다**고 답했다. 그런데 WAN-68 기준 무게이트 B안의
평균 OOS는 +0.78%로 마이너스가 아니다. 이 모듈은 그 플러스가 어디서 오는지 세 가설을
검정한다.

1. **청산 규칙이 캐리한다** — 오더블록 진입 타이밍을 무작위(WAN-70의 매칭 널, RSI
   게이트 무력화 풀에서 실제와 같은 방향·시각대 구성으로 재표본추출)로 바꿔도 현행
   청산 규칙(EMA 60/VWMA 100 익절 + 오더블록 무효화 손절)만으로 비슷한 성과가
   나오는가.
2. **시장 베타** — 같은 기간·심볼·비용의 단순 롱 바이앤홀드가 전략 성과 이상인가.
3. **엣지가 실제로 없다(비용 취약)** — 수수료·슬리피지를 1.5배·2배로 올리면 +성과가
   사라지는가.

## 재사용 범위

무작위 진입 대조군은 **WAN-70의 매칭 널 구현을 그대로 재사용**한다
(`backtest.wan70_random_control_b.run_random_control_b_segment`) — 이 모듈은 새 무작위화
로직을 만들지 않는다. 채택 전략 정의(`CURRENT_DEFAULT_PARAMS`, B안+롱온리)는
`backtest.wan76_stop_distance_audit.CURRENT_DEFAULT_PARAMS`와 동일하다. IS/OOS 분할·
오더블록 탐지 1회 재사용(게이트 무관)도 WAN-70/WAN-78과 동일한 패턴을 따른다.

## min_stop_distance_fraction 기준값 (PM 메모 2026-07-14)

WAN-79 머지로 저장소 기본값이 0 → 0.003으로 바뀌었다. 이 모듈은
`backtest.sweep.default_backtest_config`(→ `settings.effective_risk_sizing`)를 그대로
써서 **0.003을 기본으로 산출**한다. WAN-68(+0.78% OOS)·WAN-70(널 검정)의 기존 수치는
하한 0 기준이라 이 리포트의 절대값과 직접 비교할 수 없다 — 요약 헤더에 각주로 남긴다.

## 비용 민감도의 재계산 비용 절감

동일 후보(`_Candidate`, 1분봉 서브스텝 시뮬레이션까지 끝난 원가 셋업)를 1회만 만들고
비용 배율(1.0/1.5/2.0배)별로 `_sequence_and_cost`만 다시 실행한다(WAN-76의
`min_stop_distance_fraction` 민감도 스윕과 동일한 절감 패턴) — 서브스텝 재시뮬레이션은
없다.
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.models import BacktestConfig
from backtest.sweep import default_backtest_config
from backtest.wan68_short_gate_analysis import _split_bars
from backtest.wan70_random_control_b import (
    RandomControlBResult,
    Segment,
    _cell_table,
    _segment_window,
    run_random_control_b_segment,
)
from backtest.zone_limit_backtest import (
    _sequence_and_cost,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from common.costs import Liquidity
from strategy.models import ConfluenceParams, OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
DEFAULT_YEARS: float = 3.0
_BOOTSTRAP_ITERATIONS = 200
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)
_DEFAULT_SEED = 71

#: 채택 전략(WAN-68/70/73/74/76와 동일 정의): B안(존-지정가+실시간 RSI), 그 외 기본값
#: (`short_enabled=False`, WAN-69 롱온리 채택 반영).
CURRENT_DEFAULT_PARAMS = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")

#: 숏을 허용한 변형(WAN-64/69 숏 문제를 이 매칭 널 프레임에서 재확인, 작업범위 4번째 항목).
SHORT_ENABLED_PARAMS = ConfluenceParams(
    entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True
)

#: 무작위 대조군을 돌릴 게이트. "current"가 채택 전략(가설 1·비용 민감도의 기준),
#: "with_short"는 숏 포함 시에도 엣지 부재가 유지되는지 비교용.
GATES: dict[str, ConfluenceParams] = {
    "current": CURRENT_DEFAULT_PARAMS,
    "with_short": SHORT_ENABLED_PARAMS,
}

#: 비용 민감도 배율(이슈 본문 지정: 1.5배·2배). 1.0배는 기준선 비교용으로 포함.
COST_MULTIPLIERS: tuple[float, ...] = (1.0, 1.5, 2.0)

#: 판정에 포함할 셀의 최소 실제 거래 수(WAN-70과 동일 — 소표본으로 유의성을 얻는 것 방지).
_MIN_TRADES_FOR_AGG = 20


# --------------------------------------------------------------------------- #
# 결과 모델
# --------------------------------------------------------------------------- #


class CostSensitivityRow(BaseModel):
    """한 (심볼, TF, 구간) 셀에서 비용 배율별 현행 전략 성과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    cost_multiplier: float
    total_return: float
    num_trades: int


class BuyHoldRow(BaseModel):
    """한 (심볼, TF, 구간) 셀의 단순 롱 바이앤홀드 벤치마크(비용 반영)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    buy_hold_return: float | None
    """구간 시작~끝 종가로 진입·청산 1회, 테이커 수수료·슬리피지 반영. 봉 2개 미만이면 None."""
    num_bars: int


# --------------------------------------------------------------------------- #
# 바이앤홀드 벤치마크
# --------------------------------------------------------------------------- #


def buy_hold_return(closes: pd.Series, cfg: BacktestConfig) -> float | None:
    """구간 첫 종가에 전액 매수, 마지막 종가에 전량 매도(단일 거래) 수익률.

    전략과 같은 비용 모델(`cfg.cost_model`)로 테이커 진입·청산을 가정한다(전략은 B안
    메이커 진입이라 유리하지만, 바이앤홀드는 "아무 때나 사서 들고만 있는" 단순 벤치마크라
    시장가 가정이 자연스럽다 — 지정가로 정확히 구간 시작가에 살 수 있다는 보장이 없다).
    """
    if len(closes) < 2:
        return None
    costs = cfg.cost_model
    entry_price = float(closes.iloc[0])
    exit_price = float(closes.iloc[-1])
    entry_fill = costs.entry_fill(entry_price, is_long=True, liquidity=Liquidity.TAKER)
    exit_fill = costs.exit_fill(exit_price, is_long=True, liquidity=Liquidity.TAKER)
    if entry_fill <= 0:
        return None
    qty = cfg.initial_capital / entry_fill
    entry_fee = costs.fee(entry_fill * qty, Liquidity.TAKER)
    exit_fee = costs.fee(exit_fill * qty, Liquidity.TAKER)
    pnl = (exit_fill - entry_fill) * qty - entry_fee - exit_fee
    return pnl / cfg.initial_capital if cfg.initial_capital else None


# --------------------------------------------------------------------------- #
# 셀 단위 오케스트레이션(한 구간): 무작위 대조군(게이트별) + 비용 민감도 + 바이앤홀드
# --------------------------------------------------------------------------- #


def run_cell(
    seg_htf: pd.DataFrame,
    seg_1m: pd.DataFrame,
    seg_pure: pd.DataFrame,
    timeframe: str,
    *,
    symbol: str,
    segment: Segment,
    order_block_result: OrderBlockResult,
    backtest_config: BacktestConfig,
    order_block_params: OrderBlockParams | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = _DEFAULT_SEED,
    cost_multipliers: tuple[float, ...] = COST_MULTIPLIERS,
) -> tuple[list[RandomControlBResult], list[CostSensitivityRow], BuyHoldRow]:
    """한 구간에서 세 산출물(게이트별 매칭 널, 비용 민감도, 바이앤홀드)을 모두 낸다.

    `seg_pure`는 지표 워밍업용 컨텍스트가 섞이지 않은 구간 그 자체(바이앤홀드 창)다 —
    `seg_htf`는 OOS의 경우 IS 꼬리 워밍업 봉을 포함하므로 바이앤홀드에 쓰면 창이
    부풀어 실제 OOS 구간과 다른 값이 된다.
    """
    cfg = backtest_config

    random_results = [
        run_random_control_b_segment(
            seg_htf,
            seg_1m,
            timeframe,
            symbol=symbol,
            segment=segment,
            gate=gate_label,
            confluence_params=params,
            order_block_params=order_block_params,
            backtest_config=cfg,
            order_block_result=order_block_result,
            iterations=iterations,
            seed=seed,
        )
        for gate_label, params in GATES.items()
    ]

    cost_rows: list[CostSensitivityRow] = []
    if not seg_htf.empty and not seg_1m.empty:
        real_candidates, _ = build_zone_limit_candidates(
            seg_htf,
            seg_1m,
            timeframe,
            params=CURRENT_DEFAULT_PARAMS,
            cfg=cfg,
            order_block_params=order_block_params,
            order_block_result=order_block_result,
        )
        for mult in cost_multipliers:
            mult_cfg = cfg.model_copy(
                update={"fee_rate": cfg.fee_rate * mult, "slippage": cfg.slippage * mult}
            )
            trades = _sequence_and_cost(real_candidates, mult_cfg)
            result = build_result_from_trades(trades, mult_cfg, timeframe)
            cost_rows.append(
                CostSensitivityRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    segment=segment,
                    cost_multiplier=mult,
                    total_return=result.metrics.total_return,
                    num_trades=result.metrics.num_trades,
                )
            )

    closes = seg_pure["close"] if "close" in seg_pure.columns else pd.Series(dtype=float)
    buy_hold_row = BuyHoldRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        buy_hold_return=buy_hold_return(closes, cfg),
        num_bars=len(seg_pure),
    )

    return random_results, cost_rows, buy_hold_row


# --------------------------------------------------------------------------- #
# 심볼×TF 전체: IS/OOS 절단 + 오더블록 탐지 1회 재사용
# --------------------------------------------------------------------------- #


def run_symbol_timeframe(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = _DEFAULT_SEED,
    cost_multipliers: tuple[float, ...] = COST_MULTIPLIERS,
) -> tuple[list[RandomControlBResult], list[CostSensitivityRow], list[BuyHoldRow]]:
    """한 (심볼, TF)에 대해 IS/OOS × 게이트(current/with_short) 무작위 대조군, 비용
    민감도, 바이앤홀드를 모두 산출한다. 오더블록 탐지는 구간당 1회만(WAN-78 패턴)."""
    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    n = len(frame)
    if n < 30:
        return [], [], []

    is_end = _split_bars(n)
    warmup_bars = min(is_end, max(60, n // 6))
    cfg = backtest_config or default_backtest_config(timeframe)

    is_htf, is_1m = _segment_window(
        frame, one_min_df, timeframe, context_start=0, seg_start=0, seg_end=is_end
    )
    oos_htf, oos_1m = _segment_window(
        frame,
        one_min_df,
        timeframe,
        context_start=max(0, is_end - warmup_bars),
        seg_start=is_end,
        seg_end=n,
    )
    is_pure = frame.iloc[0:is_end].reset_index(drop=True)
    oos_pure = frame.iloc[is_end:n].reset_index(drop=True)

    is_ob = OrderBlockDetector(order_block_params).run(is_htf) if not is_htf.empty else None
    oos_ob = OrderBlockDetector(order_block_params).run(oos_htf) if not oos_htf.empty else None

    random_results: list[RandomControlBResult] = []
    cost_rows: list[CostSensitivityRow] = []
    buy_hold_rows: list[BuyHoldRow] = []
    for segment_label, seg_htf, seg_1m, seg_pure, ob_result in (
        ("IS", is_htf, is_1m, is_pure, is_ob),
        ("OOS", oos_htf, oos_1m, oos_pure, oos_ob),
    ):
        if seg_htf.empty or seg_1m.empty or ob_result is None:
            continue
        rr, cr, bh = run_cell(
            seg_htf,
            seg_1m,
            seg_pure,
            timeframe,
            symbol=symbol,
            segment=segment_label,  # type: ignore[arg-type]
            order_block_result=ob_result,
            backtest_config=cfg,
            order_block_params=order_block_params,
            iterations=iterations,
            seed=seed,
            cost_multipliers=cost_multipliers,
        )
        random_results.extend(rr)
        cost_rows.extend(cr)
        buy_hold_rows.append(bh)
    return random_results, cost_rows, buy_hold_rows


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터, 심볼 단위 병렬 fan-out(WAN-78 패턴)
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
REPORTS_DIR = Path("backtest/reports")
DEFAULT_CACHE_DIR = Path("data/cache")


@dataclass(frozen=True)
class _SymbolTask:
    """한 심볼의 전체 요청 TF를 처리하는 병렬 작업 단위(WAN-70/WAN-78 패턴 재사용)."""

    symbol: str
    one_min_full: pd.DataFrame
    htf_frames: dict[str, pd.DataFrame]
    timeframes: tuple[str, ...]
    years: float
    iterations: int
    seed: int


@dataclass(frozen=True)
class _SymbolResult:
    random_results: list[RandomControlBResult]
    cost_rows: list[CostSensitivityRow]
    buy_hold_rows: list[BuyHoldRow]


def _run_symbol_task(task: _SymbolTask) -> _SymbolResult:
    """`_SymbolTask` 하나(한 심볼 × 전체 TF)를 순차 처리한다. 심볼 단위 fan-out 이유는
    `backtest.wan70_random_control_b._run_symbol_task` docstring 참고(1분봉 중복 직렬화 회피)."""
    one_min_full = task.one_min_full
    m_max = int(one_min_full["open_time"].max())
    req_start = m_max - int(task.years * _YEAR_MS)
    random_results: list[RandomControlBResult] = []
    cost_rows: list[CostSensitivityRow] = []
    buy_hold_rows: list[BuyHoldRow] = []
    for timeframe in task.timeframes:
        htf_df = task.htf_frames.get(timeframe)
        if htf_df is None or htf_df.empty:
            continue
        start = max(req_start, int(htf_df["open_time"].min()))
        htf_win = htf_df[htf_df["open_time"] >= start].reset_index(drop=True)
        one_min_win = one_min_full[one_min_full["open_time"] >= start].reset_index(drop=True)
        rr, cr, bh = run_symbol_timeframe(
            htf_win,
            one_min_win,
            symbol=task.symbol,
            timeframe=timeframe,
            iterations=task.iterations,
            seed=task.seed,
        )
        random_results.extend(rr)
        cost_rows.extend(cr)
        buy_hold_rows.extend(bh)
        for r in rr:
            print(
                f"[wan71] {task.symbol} {timeframe} {r.segment} gate={r.gate}: "
                f"real={r.real_total_return:.4f} n={r.real_num_trades} "
                f"random_mean={r.random_mean_return}"
            )
    return _SymbolResult(random_results, cost_rows, buy_hold_rows)


def run_experiment(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    seed: int = _DEFAULT_SEED,
) -> _SymbolResult:
    """로컬 `data/ohlcv.db` 실데이터로 심볼×TF 전부에 대해 세 산출물을 낸다.

    `jobs>1`이면 심볼 단위로 `ProcessPoolExecutor`에 fan-out한다(WAN-70/WAN-78과 동일
    패턴). `executor.map`은 제출 순서로 결과를 반환하므로 워커 수와 무관하게 결과
    순서·내용이 순차 실행과 동일하다.
    """
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return _SymbolResult([], [], [])
    if not db_path.exists():
        return _SymbolResult([], [], [])

    tasks: list[_SymbolTask] = []
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        for symbol in symbols:
            one_min_full = store.load(symbol, "1m")
            if one_min_full.empty:
                continue
            htf_frames = {tf: store.load(symbol, tf) for tf in timeframes}
            tasks.append(
                _SymbolTask(
                    symbol=symbol,
                    one_min_full=one_min_full,
                    htf_frames=htf_frames,
                    timeframes=timeframes,
                    years=years,
                    iterations=iterations,
                    seed=seed,
                )
            )

    random_results: list[RandomControlBResult] = []
    cost_rows: list[CostSensitivityRow] = []
    buy_hold_rows: list[BuyHoldRow] = []
    if jobs <= 1 or len(tasks) <= 1:
        for task in tasks:
            r = _run_symbol_task(task)
            random_results.extend(r.random_results)
            cost_rows.extend(r.cost_rows)
            buy_hold_rows.extend(r.buy_hold_rows)
        return _SymbolResult(random_results, cost_rows, buy_hold_rows)

    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for r in executor.map(_run_symbol_task, tasks):
            random_results.extend(r.random_results)
            cost_rows.extend(r.cost_rows)
            buy_hold_rows.extend(r.buy_hold_rows)
    return _SymbolResult(random_results, cost_rows, buy_hold_rows)


def _default_jobs() -> int:
    cpu = os.cpu_count() or 1
    return max(1, cpu - 1)


# --------------------------------------------------------------------------- #
# 집계 + 세 가설 판정
# --------------------------------------------------------------------------- #


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


class HypothesisEvidence(BaseModel):
    """OOS 유효 셀(거래 `_MIN_TRADES_FOR_AGG`건 이상, 게이트=current) 기준 집계 증거."""

    model_config = ConfigDict(frozen=True)

    num_eligible_cells: int
    real_mean: float | None
    random_mean: float | None
    buy_hold_mean: float | None
    with_short_mean: float | None
    cost_means: dict[float, float | None]


def compute_hypothesis_evidence(
    random_results: list[RandomControlBResult],
    cost_rows: list[CostSensitivityRow],
    buy_hold_rows: list[BuyHoldRow],
    *,
    min_trades: int = _MIN_TRADES_FOR_AGG,
) -> HypothesisEvidence:
    current_oos = [
        r
        for r in random_results
        if r.gate == "current" and r.segment == "OOS" and r.real_num_trades >= min_trades
    ]
    eligible_keys = {(r.symbol, r.timeframe) for r in current_oos}

    real_mean = _mean([r.real_total_return for r in current_oos])
    random_mean = _mean(
        [r.random_mean_return for r in current_oos if r.random_mean_return is not None]
    )
    buy_hold_mean = _mean(
        [
            row.buy_hold_return
            for row in buy_hold_rows
            if row.segment == "OOS"
            and (row.symbol, row.timeframe) in eligible_keys
            and row.buy_hold_return is not None
        ]
    )
    cost_means: dict[float, float | None] = {}
    for mult in COST_MULTIPLIERS:
        rows = [
            row
            for row in cost_rows
            if row.segment == "OOS"
            and row.cost_multiplier == mult
            and (row.symbol, row.timeframe) in eligible_keys
        ]
        cost_means[mult] = _mean([row.total_return for row in rows])

    with_short_oos = [
        r
        for r in random_results
        if r.gate == "with_short" and r.segment == "OOS" and r.real_num_trades >= min_trades
    ]
    with_short_mean = _mean([r.real_total_return for r in with_short_oos])

    return HypothesisEvidence(
        num_eligible_cells=len(current_oos),
        real_mean=real_mean,
        random_mean=random_mean,
        buy_hold_mean=buy_hold_mean,
        with_short_mean=with_short_mean,
        cost_means=cost_means,
    )


def build_hypothesis_table(evidence: HypothesisEvidence) -> str:
    """세 가설 각각의 판정을 근거 수치와 함께 표로 정리한다(완료 기준)."""
    real_mean = evidence.real_mean
    random_mean = evidence.random_mean
    bh_mean = evidence.buy_hold_mean
    cost_means = evidence.cost_means
    m15 = cost_means.get(1.5)
    m20 = cost_means.get(2.0)

    if real_mean is None:
        h1 = h2 = h3 = "판정 불가(OOS 유효 셀 없음)"
    else:
        if random_mean is None:
            h1 = "판정 불가(무작위 대조군 없음)"
        elif random_mean > 0 and random_mean >= real_mean * 0.5:
            h1 = "지지"
        else:
            h1 = "기각/약함"

        if bh_mean is None:
            h2 = "판정 불가(바이앤홀드 없음)"
        elif bh_mean >= real_mean:
            h2 = "지지(전략이 베타를 못 넘음)"
        else:
            h2 = "기각(전략이 베타 상회)"

        if real_mean <= 0:
            h3 = "해당 없음(기준 자체가 음수)"
        elif m15 is not None and m15 <= 0:
            h3 = "지지(1.5배에서 이미 마이너스)"
        elif m20 is not None and m20 <= 0:
            h3 = "부분 지지(2배에서 마이너스)"
        else:
            h3 = "기각(2배까지 양(+) 유지)"

    header = "| 가설 | 근거 수치 | 판정 |\n| -- | -- | -- |"
    rows = [
        (
            "1. 청산 규칙이 캐리",
            f"실제 OOS평균={_fmt(real_mean)}, 무작위진입+현행청산 OOS평균={_fmt(random_mean)}",
            h1,
        ),
        (
            "2. 시장 베타",
            f"전략 OOS평균={_fmt(real_mean)}, 바이앤홀드 OOS평균={_fmt(bh_mean)}",
            h2,
        ),
        (
            "3. 엣지 없음(비용 취약)",
            f"1.0배={_fmt(cost_means.get(1.0))}, 1.5배={_fmt(m15)}, 2.0배={_fmt(m20)}",
            h3,
        ),
    ]
    body = "\n".join(f"| {name} | {basis} | {verdict} |" for name, basis, verdict in rows)
    return header + "\n" + body


def build_conclusion_paragraph(evidence: HypothesisEvidence) -> str:
    """세 가설 판정을 종합해 "플러스는 어디서 오는가" 한 문단 결론을 만든다."""
    real_mean = evidence.real_mean
    random_mean = evidence.random_mean
    bh_mean = evidence.buy_hold_mean
    cost_means = evidence.cost_means
    with_short_mean = evidence.with_short_mean
    n = evidence.num_eligible_cells

    if real_mean is None:
        return (
            f"판정 불가: OOS 유효 셀(거래 {_MIN_TRADES_FOR_AGG}건 이상, 게이트=current)이 "
            "없어 세 가설을 검정할 수 없다."
        )

    h1_supported = random_mean is not None and random_mean > 0 and random_mean >= real_mean * 0.5
    h2_dominant = bh_mean is not None and bh_mean >= real_mean
    m15 = cost_means.get(1.5)
    m20 = cost_means.get(2.0)
    h3_fragile = real_mean > 0 and (
        (m15 is not None and m15 <= 0) or (m20 is not None and m20 <= 0)
    )

    parts = [
        f"OOS 유효 셀 {n}개(거래 {_MIN_TRADES_FOR_AGG}건 이상, 게이트=current) 기준 실제 "
        f"평균 총수익률은 {_fmt(real_mean)}, 무작위 진입+현행 청산 매칭 널 평균은 "
        f"{_fmt(random_mean)}, 바이앤홀드 평균은 {_fmt(bh_mean)}이다."
    ]

    if h2_dominant:
        parts.append(
            "전략의 플러스는 청산 구조가 아니라 시장 베타에서 온다(**가설 2**) — "
            "바이앤홀드가 전략 평균 이상이라, 상승장에서 롱 편향 포지션이 시장을 "
            "따라간 결과와 구분되지 않는다."
        )
    elif h3_fragile:
        parts.append(
            "관측된 플러스는 비용에 취약해 엣지로 보기 어렵다(**가설 3**) — "
            f"비용을 1.5~2배로 올리면 평균이 1.5배={_fmt(m15)}, 2.0배={_fmt(m20)}로 "
            "마이너스에 가까워지거나 전환한다."
        )
    elif h1_supported:
        parts.append(
            "플러스는 진입이 아니라 청산 규칙(EMA 60/VWMA 100 익절 + 오더블록 무효화 "
            "손절)의 비대칭 손익비에서 온다(**가설 1**) — 매칭 널(무작위 진입+현행 청산)이 "
            "실제 성과의 상당 부분을 재현하고, 전략은 바이앤홀드를 상회하며, 비용을 "
            "2배로 올려도 양(+)이 유지된다."
        )
    else:
        parts.append(
            "세 가설이 뚜렷하게 갈리지 않는다 — 진입 타이밍에 매칭 널 대비 유의한 "
            "엣지가 없다는 WAN-70의 결론은 유지되지만, 청산 규칙 기여·시장 베타·비용 "
            "취약성 중 무엇이 지배적인지는 이 집계만으로 단정하기 어렵다. 아래 셀별 "
            "표를 함께 참고할 것."
        )

    if with_short_mean is not None:
        cmp_word = "개선되지 않아" if with_short_mean <= real_mean else "오히려 개선되어"
        agree_word = "일치한다" if with_short_mean <= real_mean else "재검토가 필요할 수 있다"
        parts.append(
            f"숏을 포함(게이트=with_short)해도 평균 OOS는 {_fmt(with_short_mean)}로 "
            f"현행(롱온리, {_fmt(real_mean)}) 대비 {cmp_word}, WAN-64/69의 롱온리 채택 "
            f"판단과 이 프레임의 결과가 {agree_word}."
        )

    return " ".join(parts)


# --------------------------------------------------------------------------- #
# 리포트 산출
# --------------------------------------------------------------------------- #


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def _random_results_to_frame(results: list[RandomControlBResult]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in results])


def _cost_rows_to_frame(rows: list[CostSensitivityRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _buy_hold_rows_to_frame(rows: list[BuyHoldRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _cost_table(rows: list[CostSensitivityRow]) -> str:
    header = "| 심볼 | TF | 구간 | 배율 | 총수익률 | 거래수 |\n| -- | -- | -- | -- | -- | -- |"
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    ordered = sorted(
        rows,
        key=lambda r: (r.symbol, order.get(r.timeframe, 9), r.segment, r.cost_multiplier),
    )
    body = "\n".join(
        f"| {r.symbol} | {r.timeframe} | {r.segment} | {r.cost_multiplier}x | "
        f"{_fmt(r.total_return, 3)} | {r.num_trades} |"
        for r in ordered
    )
    return header + "\n" + body


def _buy_hold_table(rows: list[BuyHoldRow]) -> str:
    header = "| 심볼 | TF | 구간 | 바이앤홀드 수익률 | 봉수 |\n| -- | -- | -- | -- | -- |"
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    ordered = sorted(rows, key=lambda r: (r.symbol, order.get(r.timeframe, 9), r.segment))
    body = "\n".join(
        f"| {r.symbol} | {r.timeframe} | {r.segment} | {_fmt(r.buy_hold_return, 3)} | "
        f"{r.num_bars} |"
        for r in ordered
    )
    return header + "\n" + body


def build_summary_markdown(
    random_results: list[RandomControlBResult],
    cost_rows: list[CostSensitivityRow],
    buy_hold_rows: list[BuyHoldRow],
    *,
    random_report_path: Path,
    cost_report_path: Path,
    buy_hold_report_path: Path,
) -> str:
    evidence = compute_hypothesis_evidence(random_results, cost_rows, buy_hold_rows)
    current_results = [r for r in random_results if r.gate == "current"]
    with_short_results = [r for r in random_results if r.gate == "with_short"]
    return (
        "# WAN-71 엣지의 소재지 분해 — 청산 캐리 · 시장 베타 · 비용 취약성\n\n"
        "3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS, 로컬 `data/ohlcv.db` 실데이터 "
        f"3년. 재현: `python -m backtest.wan71_edge_decomposition`.\n"
        f"원자료: `{random_report_path}`(무작위 대조군), `{cost_report_path}`(비용 민감도), "
        f"`{buy_hold_report_path}`(바이앤홀드).\n\n"
        "> **기준값 각주**: 이 리포트는 `min_stop_distance_fraction=0.003`(WAN-79 저장소\n"
        "> 기본값)으로 산출했다. WAN-68(+0.78% 평균 OOS)·WAN-70(매칭 널 검정)의 기존 수치는\n"
        "> 하한 0 기준이라 이 리포트의 절대값과 직접 비교할 수 없다.\n\n"
        "## 배경 · 세 가설\n\n"
        'WAN-70이 매칭 널로 확인한 "진입 타이밍에 엣지 없음"과 WAN-68의 "무게이트 B안 '
        '평균 OOS +0.78%"는 모순이 아니라 하나의 질문이다: 플러스가 진입에서 오지 않는다면\n'
        "어디서 오는가? 가설은 (1) 청산 규칙이 캐리, (2) 시장 베타, (3) 엣지가 실제로 없고\n"
        "비용에 취약, 세 가지다(자세한 배경은 이 모듈의 docstring과 이슈 WAN-71 본문 참고).\n\n"
        "## 무작위 진입 + 현행 청산 (게이트=current, WAN-70 매칭 널 재사용)\n\n"
        f"{_cell_table(current_results)}\n\n"
        "## 숏 포함 변형 (게이트=with_short, WAN-64/69 재확인)\n\n"
        f"{_cell_table(with_short_results)}\n\n"
        "## 비용 민감도 (1.0x/1.5x/2.0x, 동일 후보 재시퀀싱)\n\n"
        f"{_cost_table(cost_rows)}\n\n"
        "## 바이앤홀드 벤치마크\n\n"
        f"{_buy_hold_table(buy_hold_rows)}\n\n"
        "## 가설별 판정\n\n"
        f"{build_hypothesis_table(evidence)}\n\n"
        f"> 집계는 실제 거래 {_MIN_TRADES_FOR_AGG}건 이상인 OOS 셀(게이트=current)만 "
        "포함한 단순 평균이다.\n\n"
        "## 결론\n\n"
        f"{build_conclusion_paragraph(evidence)}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-71 엣지의 소재지 분해")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--iterations", type=int, default=_BOOTSTRAP_ITERATIONS)
    parser.add_argument(
        "--jobs",
        type=int,
        default=_default_jobs(),
        help="심볼 단위 병렬 워커 수(기본 cpu_count()-1). 1이면 순차 실행.",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="1분봉 parquet 캐시(data/cache/)를 쓰지 않는다."
    )
    parser.add_argument(
        "--random-out", type=Path, default=REPORTS_DIR / "wan71_random_entry_current_exit.csv"
    )
    parser.add_argument("--cost-out", type=Path, default=REPORTS_DIR / "wan71_cost_sensitivity.csv")
    parser.add_argument("--buy-hold-out", type=Path, default=REPORTS_DIR / "wan71_buy_hold.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan71_summary.md")
    args = parser.parse_args(argv)

    result = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        iterations=args.iterations,
        jobs=args.jobs,
        cache_dir=None if args.no_cache else DEFAULT_CACHE_DIR,
    )
    write_csv(_random_results_to_frame(result.random_results), args.random_out)
    write_csv(_cost_rows_to_frame(result.cost_rows), args.cost_out)
    write_csv(_buy_hold_rows_to_frame(result.buy_hold_rows), args.buy_hold_out)
    print(
        f"[wan71] random={len(result.random_results)}행 → {args.random_out}, "
        f"cost={len(result.cost_rows)}행 → {args.cost_out}, "
        f"buy_hold={len(result.buy_hold_rows)}행 → {args.buy_hold_out}"
    )

    summary = build_summary_markdown(
        result.random_results,
        result.cost_rows,
        result.buy_hold_rows,
        random_report_path=args.random_out,
        cost_report_path=args.cost_out,
        buy_hold_report_path=args.buy_hold_out,
    )
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan71] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
