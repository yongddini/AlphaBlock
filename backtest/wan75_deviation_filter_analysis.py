"""SMA 대비 이격 필터 — 성분 분해 · OOS · 매칭 널 · 플라시보 검증 (WAN-75).

배경은 `strategy.confluence`(`DeviationFilterParams`, WAN-75 도입)와 이슈 WAN-75 본문
참고. 이 모듈은 그 필터가 실제로 **채택할 가치가 있는지**를 판단하는 실험 하네스다:

1. **성분 분해** — `종가+고정%` / `종가+ATR` / `SMA+σ(볼린저)` / `SMA+ATR` 네 조합을
   무필터 기준선과 나란히 B안(`backtest.zone_limit_backtest`, 채택 전략 엔진) 위에서
   돌려 **기준점(anchor)의 기여**와 **변동성 적응(width)의 기여**를 분리한다.
2. **OOS 분리** — `backtest.wan68_short_gate_analysis._split_bars`(IS 2/3, OOS 1/3)를
   재사용한다. 지표 워밍업은 IS 꼬리에서만 빌리고(OOS 데이터 누수 없음).
3. **매칭 널** — `backtest.wan70_random_control_b`의 (방향, 시각대 버킷) 매칭 부트스트랩을
   재사용한다. 널 모집단은 **이격 필터가 꺼진 B안 RSI 게이트 통과 풀**(무필터 변형의
   체결 후보) — "같은 오더블록 유니버스에서 이격 필터가 어떤 셋업을 골라내고 어떤
   가격에 진입시키는가"가 우연 대비 유의한지를 묻는다.
4. **플라시보** — `sma20_stdev2_bollinger`·`sma20_atr2`의 기준선(SMA)을 **시간축으로
   무작위 셔플**한 변형을 추가로 돌린다(폭 계산은 그대로, 기준선의 시점별 정체성만
   파괴). 실제 SMA 위치가 아니라 "밴드가 하나 있다는 사실 자체"가 효과의 원인이라면
   플라시보도 비슷한 성과를 내야 한다.

## 성능

각 (심볼, TF, 구간)에서 후보 생성(`build_zone_limit_candidates`, 1분봉 서브스텝
시뮬레이션 포함)은 변형당 정확히 1회만 실행한다. 매칭 널은 무필터 변형의 후보 풀을
**재사용**해 부트스트랩 반복마다 재시뮬레이션하지 않는다(`wan70` 방식과 동일) — 따라서
변형 5종(+플라시보 2종) × 매칭 널 100회가 추가 서브스텝 시뮬레이션 없이 끝난다.
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.models import BacktestConfig, BacktestMetrics, PositionSide
from backtest.sweep import default_backtest_config, evaluate
from backtest.wan68_short_gate_analysis import _split_bars
from backtest.wan70_random_control_b import _bucket_key, _segment_window
from backtest.zone_limit_backtest import (
    _Candidate,
    _sequence_and_cost,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from strategy.confluence import ConfluenceStrategy
from strategy.models import (
    ConfluenceParams,
    DeviationFilterParams,
    OrderBlockParams,
    OrderBlockResult,
)
from strategy.order_blocks import OrderBlockDetector

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "1d")
DEFAULT_YEARS: float = 3.0
_BOOTSTRAP_ITERATIONS = 100
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

#: 코멘트(2026-07-13)의 지적: 저장소 기본값 `min_stop_distance_fraction=0`은 손절폭이
#: 0에 수렴하는 극단 거래(레버 수십 배 상당)로 성과를 부풀릴 수 있다. WAN-76이 저장소
#: 기본값 자체를 감사하는 동안, 이 분석은 자체적으로 하한을 적용해 그 아티팩트를
#: 피한다(저장소 기본값 자체는 건드리지 않는다).
_MIN_STOP_DISTANCE_FRACTION = 0.005

#: 성분 분해 4조합 — 이슈 본문의 예비 실험과 동일한 축(기준점 close/sma × 폭
#: 고정%/stdev/atr). `close+stdev`·`sma+pct`는 분해 질문(기준점 vs 변동성 적응)에
#: 필요하지 않아 뺐다.
REAL_VARIANTS: dict[str, DeviationFilterParams | None] = {
    "baseline": None,
    "close_pct2": DeviationFilterParams(anchor="close", width_kind="pct", width_value=0.02),
    "close_atr2": DeviationFilterParams(
        anchor="close", width_kind="atr", width_value=2.0, atr_length=14
    ),
    "sma20_stdev2_bollinger": DeviationFilterParams(
        anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0
    ),
    "sma20_atr2": DeviationFilterParams(
        anchor="sma", sma_length=20, width_kind="atr", width_value=2.0, atr_length=14
    ),
}

#: 플라시보 대상 — SMA 기준선 변형만(고정%·ATR-종가 조합은 기준선이 애초에 "위치"가
#: 아니라 종가 자체라 플라시보 질문이 성립하지 않는다).
PLACEBO_BASE_VARIANTS: tuple[str, ...] = ("sma20_stdev2_bollinger", "sma20_atr2")

#: 파이프라인이 순회할 (라벨, 플라시보 여부) 전체 목록.
PIPELINE_VARIANTS: list[tuple[str, bool]] = [(name, False) for name in REAL_VARIANTS] + [
    (f"{name}_placebo_shuffled_anchor", True) for name in PLACEBO_BASE_VARIANTS
]

_ORIGINAL_DEVIATION_FILTER_COMPONENTS = ConfluenceStrategy.deviation_filter_components
_PLACEBO_SEED = 75


def _placebo_label_to_base(label: str) -> str:
    return label.removesuffix("_placebo_shuffled_anchor")


def _shuffled_deviation_filter_components(
    df: pd.DataFrame, filter_params: DeviationFilterParams, source: str
) -> tuple[list[float], list[float]]:
    """플라시보(WAN-75): 실제 앵커(SMA) 값을 그대로 쓰되 **시간축으로 무작위 셔플**한다.

    폭(width) 계산은 손대지 않는다 — "밴드가 존재한다는 사실 자체"가 아니라 "그
    시점의 SMA 위치"가 효과의 원인인지를 가르는 대조군이다.
    """
    anchor_vals, width_vals = _ORIGINAL_DEVIATION_FILTER_COMPONENTS(df, filter_params, source)
    shuffled = list(anchor_vals)
    random.Random(_PLACEBO_SEED).shuffle(shuffled)
    return shuffled, width_vals


def _params(variant: str) -> ConfluenceParams:
    base = (
        _placebo_label_to_base(variant) if variant.endswith("_placebo_shuffled_anchor") else variant
    )
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", deviation_filter=REAL_VARIANTS[base]
    )


def _config(timeframe: str) -> BacktestConfig:
    cfg = default_backtest_config(timeframe)
    assert cfg.risk_sizing is not None, "settings.effective_risk_sizing가 비어 있음(예상 밖)"
    risk = cfg.risk_sizing.model_copy(
        update={"min_stop_distance_fraction": _MIN_STOP_DISTANCE_FRACTION}
    )
    return cfg.model_copy(update={"risk_sizing": risk})


def _build_variant_candidates(
    htf_seg: pd.DataFrame,
    one_min_seg: pd.DataFrame,
    timeframe: str,
    variant: str,
    is_placebo: bool,
    cfg: BacktestConfig,
    ob_result: OrderBlockResult,
) -> list[_Candidate]:
    params = _params(variant)
    if not is_placebo:
        candidates, _ = build_zone_limit_candidates(
            htf_seg, one_min_seg, timeframe, params=params, cfg=cfg, order_block_result=ob_result
        )
        return candidates
    with patch.object(
        ConfluenceStrategy,
        "deviation_filter_components",
        staticmethod(_shuffled_deviation_filter_components),
    ):
        candidates, _ = build_zone_limit_candidates(
            htf_seg, one_min_seg, timeframe, params=params, cfg=cfg, order_block_result=ob_result
        )
    return candidates


# --------------------------------------------------------------------------- #
# 결과 모델
# --------------------------------------------------------------------------- #

Segment = Literal["IS", "OOS"]


class DecompositionRow(BaseModel):
    """한 (심볼, TF, 변형, 구간)의 B안 성과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    variant: str
    is_placebo: bool
    segment: Segment
    total_return: float
    win_rate: float
    profit_factor: float | None
    num_trades: int


class MatchedNullRow(BaseModel):
    """한 (심볼, TF, 변형, 구간)의 매칭 널 대비 유의성."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    variant: str
    is_placebo: bool
    segment: Segment
    real_total_return: float
    real_num_trades: int
    pool_size: int
    """무필터(baseline) 변형의 체결 후보 수 — 매칭 널 표본추출 대상."""
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int


def _metrics_to_row(
    m: BacktestMetrics,
    *,
    symbol: str,
    timeframe: str,
    variant: str,
    is_placebo: bool,
    segment: Segment,
) -> DecompositionRow:
    return DecompositionRow(
        symbol=symbol,
        timeframe=timeframe,
        variant=variant,
        is_placebo=is_placebo,
        segment=segment,
        total_return=m.total_return,
        win_rate=m.win_rate,
        profit_factor=m.profit_factor,
        num_trades=m.num_trades,
    )


def _matched_null(
    real_candidates: list[_Candidate],
    pool_candidates: list[_Candidate],
    cfg: BacktestConfig,
    timeframe: str,
    *,
    iterations: int,
    seed: int,
) -> tuple[float, int, int, float | None, float | None, float | None, float | None, int]:
    """실제(변형) 거래 vs 무필터 풀에서 (방향,시각대) 매칭 재표본추출한 널의 비교.

    `backtest.wan70_random_control_b.run_random_control_b_segment`와 동일한 부트스트랩
    로직이나, 널 모집단이 "RSI 게이트 무력화 풀"이 아니라 **무필터(baseline) B안 체결
    후보**라는 점이 다르다(WAN-75가 묻는 질문은 RSI 타이밍이 아니라 이격 필터 자체의
    선택·재산정 효과이므로).
    """
    real_trades = _sequence_and_cost(real_candidates, cfg)
    real_result = build_result_from_trades(real_trades, cfg, timeframe)
    if not real_trades or not pool_candidates:
        return (
            real_result.metrics.total_return,
            len(real_trades),
            len(pool_candidates),
            None,
            None,
            None,
            None,
            0,
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
    for _ in range(iterations):
        sampled: list[_Candidate] = []
        used_by_side: dict[PositionSide, set[int]] = defaultdict(set)
        for (side, bucket), count in target_counts.items():
            bucket_pool = pool_by_bucket.get((side, bucket), [])
            k = min(count, len(bucket_pool))
            picks = rng.sample(bucket_pool, k) if k else []
            sampled.extend(picks)
            used_by_side[side].update(id(c) for c in picks)
            shortfall = count - k
            if shortfall > 0:
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
    return (
        real_result.metrics.total_return,
        len(real_trades),
        len(pool_candidates),
        mean_return,
        ci_low,
        ci_high,
        p_value,
        n,
    )


# --------------------------------------------------------------------------- #
# (심볼, TF) 단위 실행: 성분 분해 + 매칭 널 (B안, IS/OOS)
# --------------------------------------------------------------------------- #


def run_symbol_timeframe(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    order_block_params: OrderBlockParams | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = 75,
) -> tuple[list[DecompositionRow], list[MatchedNullRow]]:
    """한 (심볼, TF)에 대해 IS/OOS × 변형(baseline+4+플라시보2) 성분 분해 + 매칭 널."""
    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    n = len(frame)
    if n < 30:
        return [], []

    is_end = _split_bars(n)
    warmup_bars = min(is_end, max(60, n // 6))
    cfg = _config(timeframe)

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

    decomposition: list[DecompositionRow] = []
    matched_null: list[MatchedNullRow] = []

    for segment_label, seg_htf, seg_1m in (("IS", is_htf, is_1m), ("OOS", oos_htf, oos_1m)):
        if seg_htf.empty or seg_1m.empty:
            continue
        ob_result = OrderBlockDetector(order_block_params).run(seg_htf)

        candidates_by_variant: dict[str, list[_Candidate]] = {}
        for variant, is_placebo in PIPELINE_VARIANTS:
            candidates = _build_variant_candidates(
                seg_htf, seg_1m, timeframe, variant, is_placebo, cfg, ob_result
            )
            candidates_by_variant[variant] = candidates
            trades = _sequence_and_cost(candidates, cfg)
            result = build_result_from_trades(trades, cfg, timeframe)
            decomposition.append(
                _metrics_to_row(
                    result.metrics,
                    symbol=symbol,
                    timeframe=timeframe,
                    variant=variant,
                    is_placebo=is_placebo,
                    segment=segment_label,  # type: ignore[arg-type]
                )
            )

        pool = candidates_by_variant["baseline"]
        for variant, is_placebo in PIPELINE_VARIANTS:
            if variant == "baseline":
                continue
            real_return, real_n, pool_size, mean_r, ci_lo, ci_hi, p, iters = _matched_null(
                candidates_by_variant[variant],
                pool,
                cfg,
                timeframe,
                iterations=iterations,
                seed=seed,
            )
            matched_null.append(
                MatchedNullRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    variant=variant,
                    is_placebo=is_placebo,
                    segment=segment_label,
                    real_total_return=real_return,
                    real_num_trades=real_n,
                    pool_size=pool_size,
                    random_mean_return=mean_r,
                    random_ci_low=ci_lo,
                    random_ci_high=ci_hi,
                    random_p_value=p,
                    iterations=iters,
                )
            )

    return decomposition, matched_null


# --------------------------------------------------------------------------- #
# A안 교차검증 (경량 — 전체 구간 1회, OOS 분리 없음)
# --------------------------------------------------------------------------- #


class AEngineRow(BaseModel):
    """A안(확정봉) 전체 구간 성과 — B안 결과와 방향성이 일치하는지 확인용(경량)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    variant: str
    total_return: float
    win_rate: float
    profit_factor: float | None
    num_trades: int


def run_a_engine_crosscheck(
    htf_df: pd.DataFrame, *, symbol: str, timeframe: str
) -> list[AEngineRow]:
    """A안(`backtest.sweep.evaluate`, 확정봉 종가 엔진)으로 전체 구간을 1회씩 재현한다.

    B안(1분봉 서브스텝)만큼 정밀하지 않지만(entry_mode="close" 대신 이격 필터가 존
    경계/밴드 가격으로 진입가를 재산정하는 시그널을 그대로 소비), IS/OOS 분리 없이
    빠르게 "방향성이 B안과 일치하는가"만 확인하는 경량 교차검증이다.
    """
    cfg = _config(timeframe)
    ob_result = OrderBlockDetector().run(htf_df)
    rows: list[AEngineRow] = []
    for variant, filter_params in REAL_VARIANTS.items():
        params = ConfluenceParams(deviation_filter=filter_params)
        result = evaluate(
            htf_df, confluence_params=params, backtest_config=cfg, order_block_result=ob_result
        )
        rows.append(
            AEngineRow(
                symbol=symbol,
                timeframe=timeframe,
                variant=variant,
                total_return=result.metrics.total_return,
                win_rate=result.metrics.win_rate,
                profit_factor=result.metrics.profit_factor,
                num_trades=result.metrics.num_trades,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
DEFAULT_DECOMPOSITION_PATH = Path("backtest/reports/wan75_decomposition.csv")
DEFAULT_MATCHED_NULL_PATH = Path("backtest/reports/wan75_matched_null.csv")
DEFAULT_A_ENGINE_PATH = Path("backtest/reports/wan75_a_engine_crosscheck.csv")


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def run_experiment(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    iterations: int = _BOOTSTRAP_ITERATIONS,
) -> tuple[list[DecompositionRow], list[MatchedNullRow], list[AEngineRow]]:
    """로컬 `data/ohlcv.db` 실데이터로 심볼×TF 전부에 대해 세 리포트를 산출한다."""
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return [], [], []
    if not db_path.exists():
        return [], [], []

    decomposition: list[DecompositionRow] = []
    matched_null: list[MatchedNullRow] = []
    a_engine: list[AEngineRow] = []
    with OhlcvStore(db_path) as store:
        for symbol in symbols:
            one_min_full = store.load(symbol, "1m")
            if one_min_full.empty:
                continue
            m_max = int(one_min_full["open_time"].max())
            req_start = m_max - int(years * _YEAR_MS)
            for timeframe in timeframes:
                htf_df = store.load(symbol, timeframe)
                if htf_df.empty:
                    continue
                start = max(req_start, int(htf_df["open_time"].min()))
                htf_win = htf_df[htf_df["open_time"] >= start].reset_index(drop=True)
                one_min_win = one_min_full[one_min_full["open_time"] >= start].reset_index(
                    drop=True
                )
                decomp_rows, null_rows = run_symbol_timeframe(
                    htf_win, one_min_win, symbol=symbol, timeframe=timeframe, iterations=iterations
                )
                decomposition.extend(decomp_rows)
                matched_null.extend(null_rows)
                print(f"[wan75] {symbol} {timeframe}: decomposition rows={len(decomp_rows)}")

                a_rows = run_a_engine_crosscheck(htf_win, symbol=symbol, timeframe=timeframe)
                a_engine.extend(a_rows)
                print(f"[wan75] {symbol} {timeframe}: A안 교차검증 rows={len(a_rows)}")

    return decomposition, matched_null, a_engine


def _rows_to_frame(rows: list[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-75 SMA 대비 이격 필터 검증")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--iterations", type=int, default=_BOOTSTRAP_ITERATIONS)
    parser.add_argument("--decomposition-out", type=Path, default=DEFAULT_DECOMPOSITION_PATH)
    parser.add_argument("--matched-null-out", type=Path, default=DEFAULT_MATCHED_NULL_PATH)
    parser.add_argument("--a-engine-out", type=Path, default=DEFAULT_A_ENGINE_PATH)
    args = parser.parse_args(argv)

    decomposition, matched_null, a_engine = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        iterations=args.iterations,
    )
    write_csv(_rows_to_frame(decomposition), args.decomposition_out)  # type: ignore[arg-type]
    write_csv(_rows_to_frame(matched_null), args.matched_null_out)  # type: ignore[arg-type]
    write_csv(_rows_to_frame(a_engine), args.a_engine_out)  # type: ignore[arg-type]
    print(f"[wan75] decomposition rows={len(decomposition)} → {args.decomposition_out}")
    print(f"[wan75] matched null rows={len(matched_null)} → {args.matched_null_out}")
    print(f"[wan75] A안 교차검증 rows={len(a_engine)} → {args.a_engine_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
