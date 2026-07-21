"""신 엔진(WAN-81) OOS·워크포워드 + 무작위 진입 매칭 널 재검정 (WAN-84).

배경은 이슈 WAN-84 본문 참고. WAN-81(PR #63)로 메인 엔진이 볼린저 진입가 재산정 +
첫 탭 무조건/재탭 RSI + 고정 1:1.5R 익절 + 숏 활성화로 전면 교체됐다. 기존 엣지
검정(WAN-70 무작위 진입 매칭 널, WAN-71 엣지 소재지 분해, WAN-22/50 워크포워드)은
전부 구 엔진(`retap_mode="once"`, `rsi_gate_mode="extreme"`, `take_profit_mode="line"`,
`deviation_filter=None`, `short_enabled=False`) 전제라 신 엔진에는 무효하다. 이 모듈은
그 검정들을 **신 엔진 기본값 그대로** 재실행해 "새 규칙에 통계적 엣지가 있는가"에
답한다.

## 재사용 범위

무작위 진입 매칭 널은 **WAN-70의 구현을 그대로 재사용**한다
(`backtest.wan70_random_control_b.run_random_control_b_segment`) — 매칭 널은 실제
거래의 방향·시각대 구성을 맞춘 채 RSI 게이트를 무력화한 동일 오더블록 유니버스에서
재표본추출하므로, `confluence_params`로 신 엔진 파라미터(`NEW_ENGINE_PARAMS`)를 넘기면
청산 규칙도 자동으로 신 엔진 것(고정 1.5R + 오더블록 무효화 손절)이 된다 — 이슈가
요구하는 "신 엔진 청산 규칙 그대로 재실행"을 별도 구현 없이 만족한다. 비용 민감도·
바이앤홀드 벤치마크는 WAN-71의 패턴(`backtest.wan71_edge_decomposition`)을 재사용한다.

## 신 엔진 게이트 정의

WAN-70/71의 `GATE_PRESETS`("off"/"on")는 구 엔진 게이트(`min_rr`, `long_max_deviation`,
`short_enabled=False`) 조합이라 신 엔진과 무관하다. 이 모듈은 게이트를 하나만 둔다:
`NEW_ENGINE_PARAMS = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime",
short_enabled=True)` — `short_enabled`를 제외한 나머지 필드는 전부
`ConfluenceParams()` 기본값이다. `short_enabled=True`는 WAN-87(WAN-86 결정 1) 이후
기본값(`False`)과 달라졌으므로 이 리포트가 검증하는 "숏 활성화 신 엔진" 정의를
보존하기 위해 명시적으로 고정한 값이다
(`backtest.wan81_engine_replacement_report.NEW_ENGINE_PARAMS`와 동일한 정의 방식).

## 롱/숏 분해 (작업범위 4번째 항목)

구 엔진에서는 숏이 전 심볼·전 TF 손실이었다(WAN-64, Canceled). 신 엔진은 숏을 기본
활성화(`short_enabled=True`)하므로, 신 엔진 채택 후에도 숏이 롱 수익을 갉아먹는지
별도로 확인해야 한다. `run_cell`은 매칭 널용 실제 후보(`build_zone_limit_candidates`)를
1회 만든 뒤 비용 배율 1.0x 시퀀싱 결과를 방향(롱/숏)별로 나눠 `build_result_from_trades`를
따로 호출한다 — 후보 재계산 없이 방향별 성과를 뽑는다.

## OOS/워크포워드

`backtest.ab_walkforward`는 A/B 두 변형 모두 `confluence_params` 인자를 생략하면
`ConfluenceParams()` 기본값을 쓴다(`backtest.ab_run.build_ab_entries`). 저장소 기본값이
이미 WAN-81 신 엔진이므로, **코드 변경 없이 그대로 재실행**하면 신 엔진 기준 IS/OOS
윈도우 성과가 나온다. 이 모듈의 `main()`은 `ab_walkforward.run_experiment()`를 호출해
`backtest/reports/wan50_ab_walkforward*.csv`를 신 엔진 기준으로 갱신하고, 변형 B(채택
전략과 동일한 진입 방식) 요약을 이 리포트에 함께 싣는다.

## 판정

`build_verdict`는 WAN-70의 `summarize_verdict`와 동일한 임계값(거래
`_MIN_TRADES_FOR_VERDICT`건 이상 셀만 포함, p≤0.05 & 실제>무작위평균이면 유의)을 신
엔진 단일 게이트에 적용해 "엣지 있다/없다/일부에만 있다"를 판정한다.
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import ab_walkforward
from backtest.harness import LEGACY_OB_PARAMS, LEGACY_RSI_GATE_MODE, pin_band_bar
from backtest.models import BacktestConfig, PositionSide
from backtest.sweep import default_backtest_config
from backtest.wan68_short_gate_analysis import _split_bars
from backtest.wan70_random_control_b import (
    RandomControlBResult,
    Segment,
    _cell_table,
    _segment_window,
    run_random_control_b_segment,
)
from backtest.wan71_edge_decomposition import (
    COST_MULTIPLIERS,
    BuyHoldRow,
    CostSensitivityRow,
    _buy_hold_table,
    _cost_table,
    buy_hold_return,
)
from backtest.zone_limit_backtest import (
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
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)
_DEFAULT_SEED = 84

#: WAN-81 신 엔진 정의(B안 배선). 다른 필드는 `ConfluenceParams()` 기본값(볼린저
#: 진입가, `retap_mode="every_tap"`, `take_profit_mode="fixed_r"`(1.5R))과 같지만,
#: 기본값이 이 정의에서 멀어진 **두 필드는 명시 고정**한다: `short_enabled`는 WAN-87
#: (WAN-86 결정 1)이 `False`로 되돌렸고, `rsi_gate_mode`는 WAN-123(WAN-116 결정 B)이
#: `unconditional`(게이트 제거)로 옮겼다. 이 리포트가 검증하는 "숏 활성화 신 엔진"은
#: **재탭 RSI 게이트**를 포함하는 정의이고, 그 위에서 낸 「엣지 없음」 판정이 문서의
#: 결론이다 — 게이트를 빼면 거래 집합이 달라져 같은 검정이 아니다.
#: WAN-132(밴드 정본 `tap` → `intrabar_live`)가 **세 번째 고정 필드**를 더한다 — 이 표의
#: 「엣지 없음」 판정은 탭 봉 종가 밴드 위에서 나왔다.
NEW_ENGINE_PARAMS = pin_band_bar(
    ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        short_enabled=True,
        rsi_gate_mode=LEGACY_RSI_GATE_MODE,
    )
)

#: 매칭 널 게이트. 신 엔진 하나뿐이다(모듈 docstring "신 엔진 게이트 정의" 참고).
GATES: dict[str, ConfluenceParams] = {"new_engine": NEW_ENGINE_PARAMS}

#: 판정에 포함할 셀의 최소 실제 거래 수(WAN-70과 동일 — 소표본으로 유의성을 얻는 것 방지).
_MIN_TRADES_FOR_VERDICT = 20

#: 롱/숏 분해 판정에 포함할 최소 거래 수(방향별로 표본이 더 작아지므로 완화).
_MIN_TRADES_FOR_SIDE = 10

Side = Literal["long", "short"]


# --------------------------------------------------------------------------- #
# 결과 모델
# --------------------------------------------------------------------------- #


class SideBreakdownRow(BaseModel):
    """한 (심볼, TF, 구간, 방향)의 신 엔진 성과 — 비용 배율 1.0x 기준."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    side: Side
    total_return: float
    num_trades: int
    win_rate: float


# --------------------------------------------------------------------------- #
# 셀 단위 오케스트레이션(한 구간): 매칭 널 + 비용 민감도 + 롱/숏 분해 + 바이앤홀드
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
) -> tuple[
    list[RandomControlBResult], list[CostSensitivityRow], BuyHoldRow, list[SideBreakdownRow]
]:
    """한 구간에서 네 산출물(매칭 널, 비용 민감도, 바이앤홀드, 롱/숏 분해)을 낸다.

    `seg_pure`는 지표 워밍업용 컨텍스트가 섞이지 않은 구간 그 자체(바이앤홀드 창) —
    `backtest.wan71_edge_decomposition.run_cell`과 동일한 이유(OOS는 `seg_htf`에 IS
    꼬리 워밍업 봉이 섞여 있음).
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
    side_rows: list[SideBreakdownRow] = []
    if not seg_htf.empty and not seg_1m.empty:
        real_candidates, _ = build_zone_limit_candidates(
            seg_htf,
            seg_1m,
            timeframe,
            params=NEW_ENGINE_PARAMS,
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
            if mult == 1.0:
                for side, label in (
                    (PositionSide.LONG, "long"),
                    (PositionSide.SHORT, "short"),
                ):
                    side_trades = [t for t in trades if t.side is side]
                    side_result = build_result_from_trades(side_trades, mult_cfg, timeframe)
                    side_rows.append(
                        SideBreakdownRow(
                            symbol=symbol,
                            timeframe=timeframe,
                            segment=segment,
                            side=label,
                            total_return=side_result.metrics.total_return,
                            num_trades=side_result.metrics.num_trades,
                            win_rate=side_result.metrics.win_rate,
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

    return random_results, cost_rows, buy_hold_row, side_rows


# --------------------------------------------------------------------------- #
# 심볼×TF 전체: IS/OOS 절단 + 오더블록 탐지 1회 재사용
# --------------------------------------------------------------------------- #


def run_symbol_timeframe(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    order_block_params: OrderBlockParams | None = LEGACY_OB_PARAMS,
    backtest_config: BacktestConfig | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = _DEFAULT_SEED,
    cost_multipliers: tuple[float, ...] = COST_MULTIPLIERS,
) -> tuple[
    list[RandomControlBResult], list[CostSensitivityRow], list[BuyHoldRow], list[SideBreakdownRow]
]:
    """한 (심볼, TF)에 대해 IS/OOS 전체 네 산출물을 산출한다(WAN-70/71 패턴 재사용)."""
    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    n = len(frame)
    if n < 30:
        return [], [], [], []

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
    side_rows: list[SideBreakdownRow] = []
    for segment_label, seg_htf, seg_1m, seg_pure, ob_result in (
        ("IS", is_htf, is_1m, is_pure, is_ob),
        ("OOS", oos_htf, oos_1m, oos_pure, oos_ob),
    ):
        if seg_htf.empty or seg_1m.empty or ob_result is None:
            continue
        rr, cr, bh, sr = run_cell(
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
        side_rows.extend(sr)
    return random_results, cost_rows, buy_hold_rows, side_rows


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터, 심볼 단위 병렬 fan-out(WAN-70/71/78 패턴)
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
REPORTS_DIR = Path("backtest/reports")
DEFAULT_CACHE_DIR = Path("data/cache")


@dataclass(frozen=True)
class _SymbolTask:
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
    side_rows: list[SideBreakdownRow]


def _run_symbol_task(task: _SymbolTask) -> _SymbolResult:
    """`_SymbolTask` 하나(한 심볼 × 전체 TF)를 순차 처리한다. fan-out 이유는
    `backtest.wan70_random_control_b._run_symbol_task` docstring 참고."""
    one_min_full = task.one_min_full
    m_max = int(one_min_full["open_time"].max())
    req_start = m_max - int(task.years * _YEAR_MS)
    random_results: list[RandomControlBResult] = []
    cost_rows: list[CostSensitivityRow] = []
    buy_hold_rows: list[BuyHoldRow] = []
    side_rows: list[SideBreakdownRow] = []
    for timeframe in task.timeframes:
        htf_df = task.htf_frames.get(timeframe)
        if htf_df is None or htf_df.empty:
            continue
        start = max(req_start, int(htf_df["open_time"].min()))
        htf_win = htf_df[htf_df["open_time"] >= start].reset_index(drop=True)
        one_min_win = one_min_full[one_min_full["open_time"] >= start].reset_index(drop=True)
        rr, cr, bh, sr = run_symbol_timeframe(
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
        side_rows.extend(sr)
        for r in rr:
            print(
                f"[wan84] {task.symbol} {timeframe} {r.segment}: "
                f"real={r.real_total_return:.4f} n={r.real_num_trades} "
                f"p={r.random_p_value}"
            )
    return _SymbolResult(random_results, cost_rows, buy_hold_rows, side_rows)


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
    """로컬 `data/ohlcv.db` 실데이터로 심볼×TF 전부에 대해 네 산출물을 낸다.

    `jobs>1`이면 심볼 단위로 `ProcessPoolExecutor`에 fan-out한다(WAN-70/71/78과 동일
    패턴, 결과 순서·내용은 워커 수와 무관하게 순차 실행과 동일).
    """
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return _SymbolResult([], [], [], [])
    if not db_path.exists():
        return _SymbolResult([], [], [], [])

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
    side_rows: list[SideBreakdownRow] = []
    if jobs <= 1 or len(tasks) <= 1:
        for task in tasks:
            r = _run_symbol_task(task)
            random_results.extend(r.random_results)
            cost_rows.extend(r.cost_rows)
            buy_hold_rows.extend(r.buy_hold_rows)
            side_rows.extend(r.side_rows)
        return _SymbolResult(random_results, cost_rows, buy_hold_rows, side_rows)

    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for r in executor.map(_run_symbol_task, tasks):
            random_results.extend(r.random_results)
            cost_rows.extend(r.cost_rows)
            buy_hold_rows.extend(r.buy_hold_rows)
            side_rows.extend(r.side_rows)
    return _SymbolResult(random_results, cost_rows, buy_hold_rows, side_rows)


def _default_jobs() -> int:
    cpu = os.cpu_count() or 1
    return max(1, cpu - 1)


# --------------------------------------------------------------------------- #
# 판정: 매칭 널 유의성 + 롱/숏 분해
# --------------------------------------------------------------------------- #


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def build_verdict(
    results: list[RandomControlBResult],
    *,
    alpha: float = 0.05,
    min_trades: int = _MIN_TRADES_FOR_VERDICT,
) -> str:
    """신 엔진 단일 게이트 기준 "엣지 있다/없다/일부에만 있다" 판정(WAN-70 판정 로직 재사용)."""
    tested = [r for r in results if r.random_p_value is not None]
    if not tested:
        return "판정 불가: 유효한 셀이 없다(거래 0건 또는 데이터 부족)."
    eligible = [r for r in tested if r.real_num_trades >= min_trades]
    excluded = len(tested) - len(eligible)
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
    return (
        f"유효 셀 {total}개(거래 {min_trades}건 미만 {excluded}개 제외) 중 "
        f"{len(sig)}개가 p≤{alpha} & 실제>무작위평균. 판정: {verdict}"
    )


def summarize_short_drag(
    side_rows: list[SideBreakdownRow],
    *,
    segment: Segment = "OOS",
    min_trades: int = _MIN_TRADES_FOR_SIDE,
) -> str:
    """OOS 기준 숏이 롱 수익을 갉아먹는지 요약한다(작업범위 "숏이 롱을 까먹는가")."""
    long_rows = [
        r
        for r in side_rows
        if r.segment == segment and r.side == "long" and r.num_trades >= min_trades
    ]
    short_rows = [
        r
        for r in side_rows
        if r.segment == segment and r.side == "short" and r.num_trades >= min_trades
    ]
    long_mean = _mean([r.total_return for r in long_rows])
    short_mean = _mean([r.total_return for r in short_rows])
    if long_mean is None or short_mean is None:
        return (
            f"판정 불가: {segment} 유효 셀(방향별 거래 {min_trades}건 이상)이 부족하다 "
            f"(롱 {len(long_rows)}개, 숏 {len(short_rows)}개)."
        )
    if short_mean < 0 and short_mean < long_mean:
        verdict = "**숏이 롱 수익을 갉아먹는다**"
    elif short_mean < 0:
        verdict = "숏이 손실이지만 롱 대비 갉아먹는 정도는 제한적이다"
    else:
        verdict = "숏도 플러스로, 롱을 갉아먹지 않는다"
    return (
        f"{segment} 기준 롱 평균 총수익률={_fmt(long_mean)}(유효 셀 {len(long_rows)}개), "
        f"숏 평균 총수익률={_fmt(short_mean)}(유효 셀 {len(short_rows)}개). {verdict}"
    )


def _side_table(rows: list[SideBreakdownRow]) -> str:
    header = (
        "| 심볼 | TF | 구간 | 방향 | 총수익률 | 거래수 | 승률 |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    ordered = sorted(rows, key=lambda r: (r.symbol, order.get(r.timeframe, 9), r.segment, r.side))
    body = "\n".join(
        f"| {r.symbol} | {r.timeframe} | {r.segment} | {r.side} | "
        f"{_fmt(r.total_return, 3)} | {r.num_trades} | {_fmt(r.win_rate, 3)} |"
        for r in ordered
    )
    return header + "\n" + body


def _walkforward_summary_table(summary: pd.DataFrame | None) -> str:
    if summary is None:
        return "(워크포워드 재실행을 건너뜀 — `--skip-walkforward` 옵션)"
    if summary.empty:
        return "(데이터 부족으로 워크포워드 윈도우가 생성되지 않았다)"
    b_only = summary[summary["variant"] == "B"]
    cols = [
        "timeframe",
        "num_windows",
        "oos_num_trades",
        "mean_oos_total_return",
        "mean_oos_profit_factor",
        "mean_return_gap",
        "mean_oos_fill_rate",
    ]
    header = "| " + " | ".join(cols) + " |\n| " + " | ".join(["--"] * len(cols)) + " |"
    body_lines = []
    for _, row in b_only[cols].iterrows():
        body_lines.append(
            "| "
            + " | ".join(
                f"{row[c]:.4f}" if isinstance(row[c], float) else str(row[c]) for c in cols
            )
            + " |"
        )
    return header + "\n" + "\n".join(body_lines)


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


def _side_rows_to_frame(rows: list[SideBreakdownRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def build_summary_markdown(
    random_results: list[RandomControlBResult],
    cost_rows: list[CostSensitivityRow],
    buy_hold_rows: list[BuyHoldRow],
    side_rows: list[SideBreakdownRow],
    *,
    random_report_path: Path,
    cost_report_path: Path,
    buy_hold_report_path: Path,
    side_report_path: Path,
    walkforward_summary: pd.DataFrame | None,
) -> str:
    oos_results = [r for r in random_results if r.segment == "OOS"]
    oos_eligible = sum(1 for r in oos_results if r.real_num_trades >= _MIN_TRADES_FOR_VERDICT)
    verdict = build_verdict(random_results)
    short_drag = summarize_short_drag(side_rows)
    return (
        "# WAN-84 신 엔진(WAN-81) OOS·워크포워드 + 무작위 진입 매칭 널 재검정\n\n"
        "3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS, 로컬 `data/ohlcv.db` 실데이터 "
        f"3년. 재현: `python -m backtest.wan84_new_engine_validation`.\n"
        f"원자료: `{random_report_path}`(매칭 널), `{cost_report_path}`(비용 민감도), "
        f"`{buy_hold_report_path}`(바이앤홀드), `{side_report_path}`(롱/숏 분해).\n\n"
        "> **선행 조건**: WAN-81(PR #63) 머지 완료. 이 리포트는 저장소 현재 기본값\n"
        "> (`ConfluenceParams()` = 신 엔진: 볼린저 진입가, 첫탭 무조건/재탭 RSI, 고정\n"
        "> 1:1.5R 익절, 숏 활성화)로 산출했다. WAN-19/22/46/50/58/68/70/71/73/74/75/76 등\n"
        "> 기존 수치는 전부 구 엔진 기준이라 이 리포트의 절대값과 비교할 수 없다\n"
        "> (`backtest/reports/wan81_summary.md` 기존 수치 무효 선언 참고).\n\n"
        "## 무작위 진입 매칭 널 (신 엔진 청산 규칙 그대로, WAN-70 방법론 재사용)\n\n"
        f"{_cell_table(random_results)}\n\n"
        "## 비용 민감도 (1.0x/1.5x/2.0x, 동일 후보 재시퀀싱)\n\n"
        f"{_cost_table(cost_rows)}\n\n"
        "## 롱/숏 분해 (비용 1.0x 기준)\n\n"
        f"{_side_table(side_rows)}\n\n"
        f"{short_drag}\n\n"
        "## 바이앤홀드 벤치마크\n\n"
        f"{_buy_hold_table(buy_hold_rows)}\n\n"
        "## OOS/워크포워드 (`backtest.ab_walkforward`, 변형 B = 신 엔진 채택 진입 방식)\n\n"
        f"{_walkforward_summary_table(walkforward_summary)}\n\n"
        "원자료: `backtest/reports/wan50_ab_walkforward.csv`, "
        "`backtest/reports/wan50_ab_walkforward_summary.csv` (신 기본값으로 갱신됨).\n\n"
        "## 매칭 널 판정\n\n"
        f"{verdict}\n\n"
        f"> OOS 셀만 보면: {oos_eligible}개가 유효 표본"
        f"(거래 {_MIN_TRADES_FOR_VERDICT}건 이상)이다.\n\n"
        "## 결론\n\n"
        f"{_build_conclusion(random_results, short_drag)}\n"
    )


def _build_conclusion(random_results: list[RandomControlBResult], short_drag: str) -> str:
    verdict = build_verdict(random_results)
    edge_found = "**엣지 있다**" in verdict
    edge_absent = "**엣지 없다**" in verdict
    if edge_found:
        headline = (
            "신 엔진(WAN-81)에서는 매칭 널 대비 유의한 엣지가 **있다는 증거가 나왔다** — "
            "구 엔진(WAN-70)의 '엣지 없음' 결론이 신 규칙 도입으로 뒤집혔을 가능성을 시사한다."
        )
    elif edge_absent:
        headline = (
            "신 엔진(WAN-81)에서도 매칭 널 대비 유의한 엣지가 있다는 증거는 **없다** — "
            "볼린저 진입가·첫탭 무조건·고정 1.5R 익절·숏 활성화로 규칙이 크게 바뀌었지만, "
            "WAN-70(구 엔진)의 '진입 타이밍에 엣지 없음' 결론이 신 엔진에서도 유지된다."
        )
    else:
        headline = (
            "신 엔진에서 엣지 유무가 셀마다 갈린다 — 위 판정 문단과 셀별 표를 함께 참고할 것."
        )
    return f"{headline}\n\n{short_drag}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-84 신 엔진(WAN-81) OOS·워크포워드+매칭 널 재검정"
    )
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
        "--skip-walkforward",
        action="store_true",
        help="backtest.ab_walkforward 재실행을 건너뛴다(이미 최신 CSV가 있을 때).",
    )
    parser.add_argument(
        "--random-out", type=Path, default=REPORTS_DIR / "wan84_random_entry_new_engine.csv"
    )
    parser.add_argument("--cost-out", type=Path, default=REPORTS_DIR / "wan84_cost_sensitivity.csv")
    parser.add_argument("--buy-hold-out", type=Path, default=REPORTS_DIR / "wan84_buy_hold.csv")
    parser.add_argument("--side-out", type=Path, default=REPORTS_DIR / "wan84_side_breakdown.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan84_summary.md")
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
    write_csv(_side_rows_to_frame(result.side_rows), args.side_out)
    print(
        f"[wan84] random={len(result.random_results)}행 → {args.random_out}, "
        f"cost={len(result.cost_rows)}행 → {args.cost_out}, "
        f"buy_hold={len(result.buy_hold_rows)}행 → {args.buy_hold_out}, "
        f"side={len(result.side_rows)}행 → {args.side_out}"
    )

    walkforward_summary: pd.DataFrame | None = None
    if not args.skip_walkforward:
        wf_report = ab_walkforward.run_experiment(
            db_path=args.db,
            symbols=tuple(args.symbols),
            timeframes=tuple(args.timeframes),
            years=args.years,
        )
        ab_walkforward.write_csv(wf_report.to_dataframe(), ab_walkforward.DEFAULT_REPORT_PATH)
        walkforward_summary = wf_report.summary_dataframe()
        ab_walkforward.write_csv(walkforward_summary, ab_walkforward.DEFAULT_SUMMARY_PATH)
        print(
            f"[wan84] walkforward rows={len(wf_report.rows)} → {ab_walkforward.DEFAULT_REPORT_PATH}"
        )

    summary = build_summary_markdown(
        result.random_results,
        result.cost_rows,
        result.buy_hold_rows,
        result.side_rows,
        random_report_path=args.random_out,
        cost_report_path=args.cost_out,
        buy_hold_report_path=args.buy_hold_out,
        side_report_path=args.side_out,
        walkforward_summary=walkforward_summary,
    )
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan84] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
