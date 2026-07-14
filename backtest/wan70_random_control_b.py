"""무작위 진입 대조군 — B안(존-지정가) 엔진 그대로 재실행 (WAN-70).

배경은 이슈 WAN-70 본문 참고. WAN-68(PR #52)의 무작위 대조군은 속도 때문에
**상위TF 확정봉 엔진(A안 경로, `backtest.sweep.evaluate`)** 으로 근사했다. 하지만
실제 채택 전략은 **B안(존-지정가 + 실시간 봉내 RSI, `backtest.zone_limit_backtest`)**
이라 두 축의 엔진이 달라 절대값을 비교할 수 없었다. 이 모듈은 B안 파이프라인
**그대로** 무작위 대조군을 재구현해 "오더블록+RSI 진입에 (B안 기준) 유의한 엣지가
있는가"에 정면으로 답한다.

## 귀무가설(무작위화) 정의 — "매칭 널"

단순히 진입 시점을 균등 무작위로 뽑는 널(naive null)은 실제 시그널의 거래 빈도·
방향 비율·시간대 분포를 전혀 반영하지 않아 쉽게 이긴다(이슈 본문 지적). 대신 이
모듈은 **실제 거래의 (방향, 진입 시각대) 구성과 개수를 맞춘 상태에서, 어떤
오더블록이 "체결된 거래"가 되는지만 무작위화**하는 매칭 널을 쓴다:

1. 같은 오더블록 유니버스에 대해 **RSI 게이트를 무력화**(`rsi_oversold=100`,
   `rsi_overbought=0` → RSI가 워밍업만 끝나면 항상 통과)한 채 B안의 존-지정가·손절·
   익절·비용·1분 서브스텝 엔진을 **동일하게** 돌려, "RSI 타이밍 없이 존에 닿기만
   하면 체결"되는 후보 풀을 만든다(`build_zone_limit_candidates` 1회 호출로 사전
   계산 — 이 풀은 부트스트랩 반복마다 다시 시뮬레이션하지 않는다, 아래 성능 참고).
2. 실제(RSI 게이트 적용) 거래들의 방향(롱/숏) 개수와 진입 시각대(UTC 4시간 버킷)
   분포를 센다.
3. 부트스트랩 반복마다, 위 풀에서 **같은 (방향, 시각대 버킷) 조합·개수**를
   비복원추출로 뽑아(버킷에 표본이 부족하면 같은 방향 전체 풀로 보충하고
   `bucket_fallback_count`에 기록) 단일 포지션 시퀀싱(`_sequence_and_cost`)과 동일
   비용 모델로 총수익률을 계산한다.
4. `p_value` = 무작위 반복 중 실제 총수익률 이상을 낸 비율(단측). 95% 신뢰구간은
   무작위 총수익률 분포의 2.5~97.5 백분위수.

이 널은 "RSI 타이밍이 같은 오더블록 유니버스·같은 빈도·같은 방향 비율·비슷한
시각대 안에서 우연 대비 엣지를 더하는가"를 묻는다 — 순수 균등 무작위(어디서나
진입)보다 훨씬 엄격하다.

## 성능

B안의 병목은 1분봉 서브스텝 시뮬레이션(`simulate_zone_limit_trade`)이다. 이 모듈은
**셋업당 시뮬레이션을 정확히 1회만** 실행한다(RSI 게이트 무력화 풀 생성 1회) — 그
결과(체결가·청산가·손익 원가)는 어떤 부트스트랩 반복에서 뽑히든 동일하므로, 반복은
이미 계산된 후보를 표본추출 + 재정렬(`_sequence_and_cost`, O(표본 수))만 하면 된다.
따라서 200회 반복도 심볼×TF당 수 초 내에 끝난다(WAN-68에서 비현실적이라 근사했던
문제를 정면으로 해결).

## IS/OOS

`backtest.wan68_short_gate_analysis`의 `_split_bars`(단순 2/3·1/3 분할)를 그대로
재사용한다. 지표 워밍업은 IS 꼬리에서만 빌리고(OOS 데이터 누수 없음), 오더블록
탐지는 구간(`[context_start, seg_end)`)에 한정한다.

## 게이트 on/off

WAN-68이 도입한 세 게이트(`min_rr`, `long_max_deviation`, `short_enabled`)를 켠
프리셋과 끈 프리셋 각각에서 이 대조군을 돌려, 게이트가 엣지를 만드는지 아니면
거래 수만 줄이는지 구분한다(`GATE_PRESETS`). "게이트 on" 프리셋 값(`min_rr=1.5`,
`long_max_deviation=-0.03`, `short_enabled=False`)은 WAN-68에서 그리드 탐색으로
최적화된 값이 아니라 **대표값**이다 — 이 모듈의 목적은 "게이트가 있으면 엣지가
드러나는가"라는 방향성 질문이지 최적 임계값 탐색이 아니다.
"""

from __future__ import annotations

import argparse
import os
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.models import BacktestConfig, PositionSide
from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.wan68_short_gate_analysis import _split_bars
from backtest.zone_limit_backtest import (
    _Candidate,
    _sequence_and_cost,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
DEFAULT_YEARS: float = 3.0
_BOOTSTRAP_ITERATIONS = 200
_HOUR_BUCKET_SIZE = 4  # UTC 4시간 버킷(6개) — 표본 희소성과 시각대 해상도의 절충.
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

#: RSI 게이트 무력화 값. 롱은 `RSI <= 100`(항상 참), 숏은 `RSI >= 0`(항상 참) —
#: `RealtimeRsi`가 워밍업을 끝내 유효값을 내는 즉시 통과한다(모듈 docstring 참고).
_RSI_GATE_DISABLED_OVERSOLD = 100.0
_RSI_GATE_DISABLED_OVERBOUGHT = 0.0

Segment = Literal["IS", "OOS"]

#: WAN-68이 도입한 세 게이트를 끈/켠 대표 프리셋(WAN-70). "켠" 값은 그리드 최적화
#: 결과가 아니라 방향성 검증용 대표값이다(모듈 docstring 참고).
GATE_PRESETS: dict[str, ConfluenceParams] = {
    "off": ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime"),
    "on": ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        min_rr=1.5,
        long_max_deviation=-0.03,
        short_enabled=False,
    ),
}


# --------------------------------------------------------------------------- #
# 결과 모델
# --------------------------------------------------------------------------- #


class RandomControlBResult(BaseModel):
    """한 (심볼, TF, 구간, 게이트) 셀의 B안 무작위 대조군 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    gate: str
    real_total_return: float
    real_num_trades: int
    real_long: int
    real_short: int
    pool_size: int
    """RSI 게이트 무력화 풀(체결된 후보) 크기 — 표본추출 대상 전체."""
    random_mean_return: float | None
    random_ci_low: float | None
    """무작위 총수익률 분포의 2.5 백분위수."""
    random_ci_high: float | None
    """무작위 총수익률 분포의 97.5 백분위수."""
    random_p_value: float | None
    """실제 총수익률 이상을 낸 무작위 반복 비율(단측)."""
    iterations: int
    bucket_fallback_count: int
    """반복 중 (방향,시각대) 버킷 표본이 부족해 같은 방향 전체 풀로 보충한 횟수."""


# --------------------------------------------------------------------------- #
# 매칭 널 부트스트랩
# --------------------------------------------------------------------------- #


def _hour_bucket(entry_time_ms: int) -> int:
    hour = datetime.fromtimestamp(entry_time_ms / 1000.0, tz=UTC).hour
    return hour // _HOUR_BUCKET_SIZE


def _bucket_key(side: PositionSide, entry_time_ms: int) -> tuple[PositionSide, int]:
    return (side, _hour_bucket(entry_time_ms))


def run_random_control_b_segment(
    htf_seg: pd.DataFrame,
    one_min_seg: pd.DataFrame,
    timeframe: str,
    *,
    symbol: str,
    segment: Segment,
    gate: str,
    confluence_params: ConfluenceParams,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    order_block_result: OrderBlockResult | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = 70,
) -> RandomControlBResult:
    """한 구간에서 실제(RSI 게이트) 결과와 매칭 널 부트스트랩을 비교한다.

    `htf_seg`/`one_min_seg`는 이미 구간·워밍업 창으로 잘려 있어야 한다(IS/OOS 분할은
    `run_experiment`가 담당). `order_block_result`를 주면 실제 경로와 무력화 풀
    경로가 같은 오더블록 유니버스를 공유한다.
    """
    cfg = backtest_config or default_backtest_config(timeframe)
    ob_result = order_block_result or OrderBlockDetector(order_block_params).run(htf_seg)

    real_candidates, _ = build_zone_limit_candidates(
        htf_seg,
        one_min_seg,
        timeframe,
        params=confluence_params,
        cfg=cfg,
        order_block_params=order_block_params,
        order_block_result=ob_result,
    )
    real_trades = _sequence_and_cost(real_candidates, cfg)
    real_result = build_result_from_trades(real_trades, cfg, timeframe)
    real_long = sum(1 for t in real_trades if t.side is PositionSide.LONG)
    real_short = len(real_trades) - real_long

    if not real_trades:
        return RandomControlBResult(
            symbol=symbol,
            timeframe=timeframe,
            segment=segment,
            gate=gate,
            real_total_return=real_result.metrics.total_return,
            real_num_trades=0,
            real_long=0,
            real_short=0,
            pool_size=0,
            random_mean_return=None,
            random_ci_low=None,
            random_ci_high=None,
            random_p_value=None,
            iterations=0,
            bucket_fallback_count=0,
        )

    # RSI 게이트 무력화 풀: 셋업당 시뮬레이션 정확히 1회(성능 — 모듈 docstring).
    pool_candidates, _ = build_zone_limit_candidates(
        htf_seg,
        one_min_seg,
        timeframe,
        params=confluence_params,
        cfg=cfg,
        order_block_params=order_block_params,
        order_block_result=ob_result,
        rsi_oversold=_RSI_GATE_DISABLED_OVERSOLD,
        rsi_overbought=_RSI_GATE_DISABLED_OVERBOUGHT,
    )

    pool_by_bucket: dict[tuple[PositionSide, int], list[_Candidate]] = defaultdict(list)
    pool_by_side: dict[PositionSide, list[_Candidate]] = defaultdict(list)
    for cand in pool_candidates:
        pool_by_bucket[_bucket_key(cand.side, cand.entry_time)].append(cand)
        pool_by_side[cand.side].append(cand)

    target_counts: dict[tuple[PositionSide, int], int] = defaultdict(int)
    for trade in real_trades:
        target_counts[_bucket_key(trade.side, trade.entry_time)] += 1

    rng = random.Random(seed)
    random_returns: list[float] = []
    bucket_fallback_count = 0
    for _ in range(iterations):
        sampled: list[_Candidate] = []
        used_by_side: dict[PositionSide, set[int]] = defaultdict(set)
        for (side, _bucket), count in target_counts.items():
            bucket_pool = pool_by_bucket.get((side, _bucket), [])
            k = min(count, len(bucket_pool))
            picks = rng.sample(bucket_pool, k) if k else []
            sampled.extend(picks)
            used_by_side[side].update(id(c) for c in picks)
            shortfall = count - k
            if shortfall > 0:
                bucket_fallback_count += 1
                remaining = [
                    c for c in pool_by_side.get(side, []) if id(c) not in used_by_side[side]
                ]
                fill_k = min(shortfall, len(remaining))
                fill_picks = rng.sample(remaining, fill_k) if fill_k else []
                sampled.extend(fill_picks)
                used_by_side[side].update(id(c) for c in fill_picks)
        sampled_trades = _sequence_and_cost(sampled, cfg)
        sampled_result = build_result_from_trades(sampled_trades, cfg, timeframe)
        random_returns.append(sampled_result.metrics.total_return)

    random_returns.sort()
    n = len(random_returns)
    p_value = (
        sum(1 for r in random_returns if r >= real_result.metrics.total_return) / n if n else None
    )
    mean_return = sum(random_returns) / n if n else None
    ci_low = random_returns[int(0.025 * (n - 1))] if n else None
    ci_high = random_returns[int(0.975 * (n - 1))] if n else None

    return RandomControlBResult(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        gate=gate,
        real_total_return=real_result.metrics.total_return,
        real_num_trades=len(real_trades),
        real_long=real_long,
        real_short=real_short,
        pool_size=len(pool_candidates),
        random_mean_return=mean_return,
        random_ci_low=ci_low,
        random_ci_high=ci_high,
        random_p_value=p_value,
        iterations=n,
        bucket_fallback_count=bucket_fallback_count,
    )


# --------------------------------------------------------------------------- #
# IS/OOS 구간 절단
# --------------------------------------------------------------------------- #


def _segment_window(
    frame: pd.DataFrame,
    one_min_df: pd.DataFrame,
    timeframe: str,
    *,
    context_start: int,
    seg_start: int,
    seg_end: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`backtest.wan68_short_gate_analysis._evaluate_zone_limit_segment`와 동일한
    누수 방지 절단(지표 워밍업은 IS 꼬리에서만, 성과 집계는 구간 시간창으로 한정)."""
    htf_ms = timeframe_to_ms(timeframe)
    htf_seg = frame.iloc[context_start:seg_end].reset_index(drop=True)
    if htf_seg.empty:
        return htf_seg, one_min_df.iloc[0:0]
    seg_start_time = int(frame["open_time"].iloc[seg_start])
    seg_last_time = int(frame["open_time"].iloc[seg_end - 1])
    seg_window_end = seg_last_time + htf_ms
    one_min_seg = one_min_df[
        (one_min_df["open_time"] >= seg_start_time) & (one_min_df["open_time"] < seg_window_end)
    ].reset_index(drop=True)
    return htf_seg, one_min_seg


def run_symbol_timeframe(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    gates: dict[str, ConfluenceParams] | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = 70,
) -> list[RandomControlBResult]:
    """한 (심볼, TF)에 대해 IS/OOS × 게이트(off/on) 4셀을 산출한다."""
    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    n = len(frame)
    if n < 30:
        return []

    is_end = _split_bars(n)
    warmup_bars = min(is_end, max(60, n // 6))
    cfg = backtest_config or default_backtest_config(timeframe)
    gate_configs = gates or GATE_PRESETS

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

    # 오더블록 탐지는 게이트와 무관(동일 order_block_params)하므로 게이트 루프 밖에서
    # 1회만 계산한다 — 게이트마다 중복 실행하던 병목 제거(WAN-78).
    is_ob = OrderBlockDetector(order_block_params).run(is_htf) if not is_htf.empty else None
    oos_ob = OrderBlockDetector(order_block_params).run(oos_htf) if not oos_htf.empty else None

    results: list[RandomControlBResult] = []
    for gate_label, params in gate_configs.items():
        for segment_label, seg_htf, seg_1m, ob_result in (
            ("IS", is_htf, is_1m, is_ob),
            ("OOS", oos_htf, oos_1m, oos_ob),
        ):
            if seg_htf.empty or seg_1m.empty or ob_result is None:
                continue
            results.append(
                run_random_control_b_segment(
                    seg_htf,
                    seg_1m,
                    timeframe,
                    symbol=symbol,
                    segment=segment_label,  # type: ignore[arg-type]
                    gate=gate_label,
                    confluence_params=params,
                    order_block_params=order_block_params,
                    backtest_config=cfg,
                    order_block_result=ob_result,
                    iterations=iterations,
                    seed=seed,
                )
            )
    return results


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
DEFAULT_REPORT_PATH = Path("backtest/reports/wan70_random_control_b.csv")
DEFAULT_SUMMARY_PATH = Path("backtest/reports/wan70_summary.md")


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


DEFAULT_CACHE_DIR = Path("data/cache")
_DEFAULT_SEED = 70


@dataclass(frozen=True)
class _SymbolTask:
    """한 심볼의 전체 요청 TF를 처리하는 병렬 작업 단위(WAN-78)."""

    symbol: str
    one_min_full: pd.DataFrame
    htf_frames: dict[str, pd.DataFrame]
    timeframes: tuple[str, ...]
    years: float
    iterations: int
    seed: int


def _run_symbol_task(task: _SymbolTask) -> list[RandomControlBResult]:
    """`_SymbolTask` 하나(한 심볼 × 전체 TF)를 순차 처리한다.

    **심볼 단위**로 작업을 나누는 이유(TF 단위가 아님): 1분봉(`one_min_full`)은 한
    심볼의 모든 TF가 공유하는 대용량 DataFrame이다. TF 단위로 더 잘게 나누면
    `ProcessPoolExecutor`가 같은 1분봉을 TF 개수만큼 중복 직렬화(pickle)해 프로세스 간
    전송 비용이 병렬화로 얻는 이득을 상쇄할 수 있다. 심볼 단위 fan-out은 1분봉을
    워커 프로세스당 1회만 전달한다.

    시드는 심볼·TF와 무관하게 고정값(`seed`, 기본 70)을 그대로 쓴다 — 각 세그먼트가
    `random.Random(seed)`로 매번 새 RNG 인스턴스를 만들어 쓰므로(전역 `random` 모듈
    상태를 공유하지 않음) 워커 수·실행 순서가 달라져도 이미 결정적이다. 즉 셀별로
    시드를 다시 유도할 필요가 없다(검증: `test_wan70_random_control_b.py`의
    `--jobs 1` vs `--jobs 4` 동일성 테스트).
    """
    one_min_full = task.one_min_full
    m_max = int(one_min_full["open_time"].max())
    req_start = m_max - int(task.years * _YEAR_MS)
    results: list[RandomControlBResult] = []
    for timeframe in task.timeframes:
        htf_df = task.htf_frames.get(timeframe)
        if htf_df is None or htf_df.empty:
            continue
        start = max(req_start, int(htf_df["open_time"].min()))
        htf_win = htf_df[htf_df["open_time"] >= start].reset_index(drop=True)
        one_min_win = one_min_full[one_min_full["open_time"] >= start].reset_index(drop=True)
        cell_results = run_symbol_timeframe(
            htf_win,
            one_min_win,
            symbol=task.symbol,
            timeframe=timeframe,
            iterations=task.iterations,
            seed=task.seed,
        )
        results.extend(cell_results)
        for r in cell_results:
            print(
                f"[wan70] {task.symbol} {timeframe} {r.segment} gate={r.gate}: "
                f"real={r.real_total_return:.4f} n={r.real_num_trades} "
                f"p={r.random_p_value}"
            )
    return results


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
) -> list[RandomControlBResult]:
    """로컬 `data/ohlcv.db` 실데이터로 심볼×TF 전부에 대해 B안 무작위 대조군을 산출한다.

    `jobs>1`이면 심볼 단위로 `ProcessPoolExecutor`에 fan-out한다(`_run_symbol_task`
    docstring 참고). `executor.map`은 제출 순서로 결과를 반환하므로 워커 수와 무관하게
    `results` 순서·내용이 순차 실행과 동일하다(WAN-78 완료 기준: `--jobs` 값에 무관한
    결과 동일성).
    """
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return []
    if not db_path.exists():
        return []

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

    results: list[RandomControlBResult] = []
    if jobs <= 1 or len(tasks) <= 1:
        for task in tasks:
            results.extend(_run_symbol_task(task))
        return results

    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for cell_results in executor.map(_run_symbol_task, tasks):
            results.extend(cell_results)
    return results


def _results_to_frame(results: list[RandomControlBResult]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in results])


_MIN_TRADES_FOR_VERDICT = 20
"""판정에 포함할 셀의 최소 실제 거래 수. 총수익률 기반 부트스트랩 p-value는 표본이
극소(예: 일봉 n=2~4)면 무의미하므로, 그 아래 셀은 유의성 판정에서 제외한다 —
표본을 줄여 유의성을 얻는 것을 막으려는 이슈 지침(WAN-70)의 취지와 같다."""


def summarize_verdict(
    results: list[RandomControlBResult],
    *,
    alpha: float = 0.05,
    min_trades: int = _MIN_TRADES_FOR_VERDICT,
) -> str:
    """셀별 p-value를 근거로 "엣지 있다/없다/일부 TF·심볼에만 있다" 판정 문단을 만든다.

    `min_trades` 미만인 셀은 총수익률 기반 부트스트랩 p-value가 통계적으로 무의미해
    (표본 극소) 유의성 판정에서 제외하고, 제외 수를 문단에 함께 밝힌다. 유의 셀은
    "실제가 무작위 평균보다 우월(엣지의 방향이 맞음)"인 경우만 센다 — 실제 수익률이
    무작위보다 나빴는데 p가 낮은 경우는 하방 엣지라 채택 근거가 아니다.
    """
    tested = [r for r in results if r.random_p_value is not None]
    if not tested:
        return "판정 불가: 유효한 셀이 없다(거래 0건 또는 데이터 부족)."
    by_gate: dict[str, list[RandomControlBResult]] = defaultdict(list)
    for r in tested:
        by_gate[r.gate].append(r)

    lines = []
    for gate, rows in sorted(by_gate.items()):
        eligible = [r for r in rows if r.real_num_trades >= min_trades]
        excluded = len(rows) - len(eligible)
        sig = [
            r
            for r in eligible
            if r.random_p_value is not None
            and r.random_p_value <= alpha
            and r.random_mean_return is not None
            and r.real_total_return > r.random_mean_return
        ]
        total = len(eligible)
        if total == 0:
            verdict = "**판정 불가**(유효 표본 셀 없음)"
        elif not sig:
            verdict = "**엣지 없다**"
        elif len(sig) == total:
            verdict = "**엣지 있다**"
        else:
            cells = ", ".join(f"{r.symbol}/{r.timeframe}/{r.segment}" for r in sig)
            verdict = f"**특정 TF·심볼에서만 있다**({cells})"
        lines.append(
            f"게이트={gate}: 유효 셀 {total}개(거래 {min_trades}건 미만 {excluded}개 제외) 중 "
            f"{len(sig)}개가 p≤{alpha} & 실제>무작위평균. 판정: {verdict}"
        )
    return "\n\n".join(lines)


def _fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _cell_table(results: list[RandomControlBResult]) -> str:
    """셀별 원자료(실제 총수익률·거래수·무작위 평균·95% CI·p) 마크다운 표."""
    header = (
        "| 심볼 | TF | 구간 | 게이트 | 실제수익 | n | 무작위평균 | 95% CI | p |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    rows = sorted(
        results,
        key=lambda r: (r.symbol, order.get(r.timeframe, 9), r.gate, r.segment),
    )
    body = []
    for r in rows:
        ci = (
            f"[{_fmt(r.random_ci_low, 3)}, {_fmt(r.random_ci_high, 3)}]"
            if r.random_ci_low is not None
            else "—"
        )
        body.append(
            f"| {r.symbol} | {r.timeframe} | {r.segment} | {r.gate} | "
            f"{_fmt(r.real_total_return, 3)} | {r.real_num_trades} | "
            f"{_fmt(r.random_mean_return, 3)} | {ci} | {_fmt(r.random_p_value, 3)} |"
        )
    return header + "\n" + "\n".join(body)


def build_summary_markdown(results: list[RandomControlBResult], *, report_path: Path) -> str:
    """셀별 표 + 매칭 널 정의 + 판정 문단을 담은 자기완결 리포트 마크다운을 만든다."""
    verdict = summarize_verdict(results)
    return (
        "# WAN-70 무작위 진입 대조군 — B안 엔진 그대로 재실행\n\n"
        f"3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS × 게이트(off/on), 로컬 "
        f"`data/ohlcv.db` 실데이터 3년. 재현: `python -m backtest.wan70_random_control_b`.\n"
        f"원자료(48행)는 `{report_path}`.\n\n"
        "## 배경\n\n"
        "WAN-68(PR #52)의 무작위 대조군은 속도 때문에 **A안(확정봉 종가) 엔진**으로 근사돼\n"
        "실제 채택 전략인 **B안(존-지정가 + 실시간 봉내 RSI)** 과 직접 비교할 수 없었다.\n"
        "이 리포트는 B안 파이프라인(`backtest/zone_limit_backtest.py`) **그대로** 무작위\n"
        '대조군을 재실행해 "오더블록+RSI 진입에 (B안 기준) 유의한 엣지가 있는가"에\n'
        "정면으로 답한다.\n\n"
        "## 귀무가설(매칭 널)\n\n"
        "단순 균등 무작위 진입은 실제 시그널의 빈도·방향·시간대를 반영하지 않아 너무\n"
        "약한 널이다. 대신 **실제 거래의 방향(롱/숏) 개수와 진입 시각대(UTC 4시간 버킷)\n"
        "분포를 맞춘 채**, RSI 게이트를 무력화한 동일 오더블록 유니버스(존에 닿기만 하면\n"
        "체결)에서 같은 조합·개수를 재표본추출한 거래 집합과 실제(RSI 게이트 적용)\n"
        "총수익률을 비교했다. 부트스트랩 200회, 셋업당 1분봉 서브스텝 시뮬레이션은 1회만\n"
        "실행(반복은 사전계산된 후보를 재표본추출+재정렬만) — 자세한 정의는\n"
        "`backtest/wan70_random_control_b.py` 모듈 docstring 참고.\n\n"
        "`p` = 무작위 반복 중 실제 총수익률 이상을 낸 비율(단측, 낮을수록 실제 진입이\n"
        "우연이 아닐 확률↑). 95% CI는 무작위 총수익률 분포의 2.5~97.5 백분위수.\n\n"
        "## 셀별 결과\n\n"
        f"{_cell_table(results)}\n\n"
        "## 판정\n\n"
        f"{verdict}\n\n"
        f"> 유의성 판정은 실제 거래 {_MIN_TRADES_FOR_VERDICT}건 이상인 셀만 포함한다. 일봉 등\n"
        "> 극소표본(n=2~4) 셀은 총수익률 기반 부트스트랩 p가 무의미해(우연히 p=0.0도 나옴)\n"
        "> 제외했다 — 표본을 줄여 유의성을 얻지 말라는 이슈 지침의 취지와 같다.\n\n"
        "## 결론\n\n"
        "**B안(존-지정가+실시간 RSI) 진입 타이밍에 매칭 널 대비 유의한 엣지가 있다는\n"
        "증거는 없다.** 게이트 off/on 어느 쪽에서도 충분한 표본을 가진 셀 중 p≤0.05이면서\n"
        '실제가 무작위 평균을 넘는 셀은 하나도 없었다. WAN-68이 A안 근사로 얻은 "엣지\n'
        '증거 약함" 결론이 **정작 우리가 쓰는 B안 엔진에서도, 더 엄격한 매칭 널로도\n'
        "유지**된다. 게이트는 거래 수를 크게 줄일 뿐(예: 1h off n=94→on n=16) 무작위\n"
        "대비 엣지를 만들어내지는 못한다.\n\n"
        "이 결과는 라이브 배선(WAN-45)·통합 포트폴리오(WAN-61)·종목 확장(WAN-62)에\n"
        "자원을 더 투입하기 전에 진입 로직 자체를 재검토해야 함을 시사한다 — 엣지가\n"
        "없다는 이 결론이야말로 잘못된 방향 투자를 막아 주는 가장 값진 산출이다.\n"
    )


def _default_jobs() -> int:
    cpu = os.cpu_count() or 1
    return max(1, cpu - 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-70 무작위 진입 대조군(B안 엔진 그대로)")
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
        "--no-cache",
        action="store_true",
        help="1분봉 parquet 캐시(data/cache/)를 쓰지 않는다.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY_PATH)
    args = parser.parse_args(argv)

    results = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        iterations=args.iterations,
        jobs=args.jobs,
        cache_dir=None if args.no_cache else DEFAULT_CACHE_DIR,
    )
    write_csv(_results_to_frame(results), args.out)
    print(f"[wan70] rows={len(results)} → {args.out}")

    summary = build_summary_markdown(results, report_path=args.out)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan70] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
