"""숏 존폐 판단(가/나/다) · 롱 이격도 게이트 강건성 · OOS 검증 · 무작위 대조군 (WAN-68).

배경은 `strategy.confluence`(WAN-68 게이트 추가)와 이슈 WAN-68 본문 참고. 이 모듈은
그 게이트들이 실제로 **채택할 가치가 있는지**를 판단하는 실험 하네스다:

1. **숏 3안 비교** — 같은 IS(앞 2/3)/OOS(뒤 1/3) 분할에서 무게이트 B안 대비
   (가) 숏 유지+레짐 게이트, (나) 숏 완전 제거(`short_enabled=False`),
   (다) 숏 유지+손익비 게이트만(`min_rr`, IS에서 후보 중 최선을 고른 뒤 고정)을
   비교한다. (가)는 **레짐 정의 4종**(A~D) 전부에 대해 별도 행으로 낸다 —
   레짐 정의는 여기서도, 아래 강건성 체크에서도 재사용한다(임계값 최적화에는
   쓰지 않는다).
2. **OOS 채택 판정** — 각 변형의 OOS 총수익률이 무게이트 B안의 OOS 총수익률보다
   우월하지 않으면 `oos_superior_to_baseline=False`로 기록한다(채택 불가).
3. **무작위 진입 대조군** — 같은 오더블록 탭 유니버스에서 RSI 조건 없이 무작위로
   진입을 골라(부트스트랩) 같은 청산 규칙(`ConfluenceStrategy._plan_exit`)으로
   시뮬레이션한 총수익률 분포와 실제 확정 진입 총수익률을 비교해 p-value를 낸다.
   **속도를 위해 상위TF 확정봉 엔진(`backtest.sweep.evaluate`, A안 경로)을 쓴다** —
   B안(1분봉 서브스텝)까지 200회 반복하면 비현실적으로 느리므로, "타이밍에 엣지가
   있는가"라는 통계적 질문에 한해 근사로 허용한다(본 성과 비교는 위 1)이 담당).

## 데이터 누수 방지

IS/OOS 분할은 인덱스 기준 단순 분할(`_split_bars`)이며, OOS 구간의 지표 워밍업은
IS 꼬리에서만 빌려온다(`context_start = max(0, is_end - warmup_bars)`) — OOS 이후
데이터는 어떤 계산에도 들어가지 않는다. 레짐 판정(`_regime_lookup`)은 각 진입
시각 이하의 마지막 확정 봉만 본다(`bisect_right - 1`).
"""

from __future__ import annotations

import argparse
import bisect
import math
import random
from collections.abc import Callable
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import run_backtest
from backtest.harness import LEGACY_OB_PARAMS, pin_band_bar
from backtest.models import BacktestConfig, BacktestMetrics
from backtest.sweep import (
    CLOSE_ENTRY_DEFAULTS,
    default_backtest_config,
    evaluate,
    timeframe_to_ms,
)
from backtest.zone_limit_backtest import run_zone_limit_backtest_verbose
from strategy.confluence import (
    ConfluenceSignal,
    ConfluenceStrategy,
    IndicatorSnapshot,
    SignalKind,
)
from strategy.indicators import emas
from strategy.models import (
    ConfluenceParams,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
)
from strategy.order_blocks import OrderBlockDetector

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "15m", "1d")
DEFAULT_YEARS: float = 3.0
_MIN_RR_GRID: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0)
_BOOTSTRAP_ITERATIONS = 200
_REGIME_SLOPE_LOOKBACK = 20
_LOCAL_REGIME_EMA = 240
_BTC_REGIME_EMA = 200
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

_BTC_SYMBOL = "BTC/USDT:USDT"


# --------------------------------------------------------------------------- #
# 구간 성과
# --------------------------------------------------------------------------- #


class SegmentMetrics(BaseModel):
    """한 구간(IS 또는 OOS)의 한 변형 성과."""

    model_config = ConfigDict(frozen=True)

    total_return: float
    win_rate: float
    profit_factor: float | None
    num_trades: int


def _empty_segment() -> SegmentMetrics:
    return SegmentMetrics(total_return=0.0, win_rate=0.0, profit_factor=None, num_trades=0)


def _segment_from_result(metrics: BacktestMetrics) -> SegmentMetrics:
    return SegmentMetrics(
        total_return=metrics.total_return,
        win_rate=metrics.win_rate,
        profit_factor=metrics.profit_factor,
        num_trades=metrics.num_trades,
    )


def _split_bars(n: int, is_frac: float = 2.0 / 3.0) -> int:
    """`n`봉을 IS `[0, is_end)`/OOS `[is_end, n)`으로 나눈다(단순 분할, WAN-68)."""
    return max(1, min(n - 1, int(round(n * is_frac))))


# --------------------------------------------------------------------------- #
# 레짐 정의 4종 (A~D)
# --------------------------------------------------------------------------- #

RegimeLookup = Callable[[int], bool]
"""진입 시각(ms) -> 그 시점에 숏을 허용할 하락 국면인지."""


def _build_regime_lookup(
    reference_df: pd.DataFrame, *, ema_length: int, use_slope: bool, slope_lookback: int
) -> RegimeLookup:
    """`reference_df` 종가·EMA로 레짐 판정 함수를 만든다.

    `use_slope=False`(정의 A/C): 마지막 확정 봉 종가가 EMA 아래인지(레벨).
    `use_slope=True`(정의 B/D): EMA가 `slope_lookback`봉 전보다 낮은지(기울기).
    """
    frame = reference_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    ema_frame = emas(frame, lengths=(ema_length,), source="close")
    ema_vals = [float(v) for v in ema_frame[f"ema_{ema_length}"]]

    def lookup(entry_time: int) -> bool:
        idx = bisect.bisect_right(times, entry_time) - 1
        if idx < 0:
            return False
        if use_slope:
            prev = idx - slope_lookback
            if prev < 0 or math.isnan(ema_vals[idx]) or math.isnan(ema_vals[prev]):
                return False
            return ema_vals[idx] < ema_vals[prev]
        if math.isnan(ema_vals[idx]):
            return False
        return closes[idx] < ema_vals[idx]

    return lookup


def build_regime_definitions(
    local_htf_df: pd.DataFrame, btc_daily_df: pd.DataFrame | None
) -> dict[str, RegimeLookup]:
    """레짐 정의 A~D를 만든다. `btc_daily_df`가 없으면 C/D는 제외한다."""
    defs: dict[str, RegimeLookup] = {
        "A_local_close_below_ema240": _build_regime_lookup(
            local_htf_df, ema_length=_LOCAL_REGIME_EMA, use_slope=False, slope_lookback=0
        ),
        "B_local_ema240_slope_down": _build_regime_lookup(
            local_htf_df,
            ema_length=_LOCAL_REGIME_EMA,
            use_slope=True,
            slope_lookback=_REGIME_SLOPE_LOOKBACK,
        ),
    }
    if btc_daily_df is not None and not btc_daily_df.empty:
        defs["C_btc_daily_close_below_ema200"] = _build_regime_lookup(
            btc_daily_df, ema_length=_BTC_REGIME_EMA, use_slope=False, slope_lookback=0
        )
        defs["D_btc_daily_ema200_slope_down"] = _build_regime_lookup(
            btc_daily_df,
            ema_length=_BTC_REGIME_EMA,
            use_slope=True,
            slope_lookback=_REGIME_SLOPE_LOOKBACK,
        )
    return defs


def filter_signals_by_regime(
    ob_result: OrderBlockResult, regime_lookup: RegimeLookup
) -> OrderBlockResult:
    """숏(약세) 신호만 레짐이 하락 국면일 때로 제한한다. 롱은 그대로 둔다."""
    kept = [
        s
        for s in ob_result.signals
        if s.direction is OrderBlockDirection.BULLISH or regime_lookup(s.trigger_time)
    ]
    return OrderBlockResult(order_blocks=ob_result.order_blocks, signals=kept)


# --------------------------------------------------------------------------- #
# IS/OOS 구간 평가 (B안 zone-limit 파이프라인)
# --------------------------------------------------------------------------- #


def _evaluate_zone_limit_segment(
    frame: pd.DataFrame,
    one_min_df: pd.DataFrame,
    timeframe: str,
    *,
    context_start: int,
    seg_start: int,
    seg_end: int,
    params: ConfluenceParams,
    order_block_params: OrderBlockParams | None,
    backtest_config: BacktestConfig,
    signal_filter: RegimeLookup | None = None,
) -> SegmentMetrics:
    """구간 `[seg_start, seg_end)`에서 B안을 돌려 성과를 낸다.

    `context_start`는 지표 워밍업용 과거 봉 시작 위치(항상 `seg_start` 이하)이며,
    성과 집계는 1분봉을 `[seg_start_time, seg_end)` 시간창으로 한정해 자연히
    구간 안으로 좁힌다(WAN-50 방식과 동일한 누수 방지).
    """
    htf_ms = timeframe_to_ms(timeframe)
    htf_seg = frame.iloc[context_start:seg_end].reset_index(drop=True)
    if htf_seg.empty:
        return _empty_segment()
    seg_start_time = int(frame["open_time"].iloc[seg_start])
    seg_last_time = int(frame["open_time"].iloc[seg_end - 1])
    seg_window_end = seg_last_time + htf_ms
    one_min_seg = one_min_df[
        (one_min_df["open_time"] >= seg_start_time) & (one_min_df["open_time"] < seg_window_end)
    ].reset_index(drop=True)
    if one_min_seg.empty:
        return _empty_segment()

    ob_result = OrderBlockDetector(order_block_params).run(htf_seg)
    if signal_filter is not None:
        ob_result = filter_signals_by_regime(ob_result, signal_filter)

    result, _ = run_zone_limit_backtest_verbose(
        htf_seg,
        one_min_seg,
        timeframe,
        confluence_params=params,
        order_block_params=order_block_params,
        backtest_config=backtest_config,
        order_block_result=ob_result,
    )
    return _segment_from_result(result.metrics)


# --------------------------------------------------------------------------- #
# 변형 비교 행
# --------------------------------------------------------------------------- #


class VariantRow(BaseModel):
    """한 (심볼, TF, 변형)의 IS/OOS 성과 + 무게이트 B안 대비 OOS 우월성 판정."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    variant: str
    detail: str
    is_total_return: float
    is_win_rate: float
    is_profit_factor: float | None
    is_num_trades: int
    oos_total_return: float
    oos_win_rate: float
    oos_profit_factor: float | None
    oos_num_trades: int
    oos_superior_to_baseline: bool | None
    """OOS 총수익률이 무게이트 B안의 OOS 총수익률보다 큰지. 기준선 행 자체는 None."""


def _row(
    symbol: str,
    timeframe: str,
    variant: str,
    detail: str,
    is_seg: SegmentMetrics,
    oos_seg: SegmentMetrics,
    oos_superior: bool | None,
) -> VariantRow:
    return VariantRow(
        symbol=symbol,
        timeframe=timeframe,
        variant=variant,
        detail=detail,
        is_total_return=is_seg.total_return,
        is_win_rate=is_seg.win_rate,
        is_profit_factor=is_seg.profit_factor,
        is_num_trades=is_seg.num_trades,
        oos_total_return=oos_seg.total_return,
        oos_win_rate=oos_seg.win_rate,
        oos_profit_factor=oos_seg.profit_factor,
        oos_num_trades=oos_seg.num_trades,
        oos_superior_to_baseline=oos_superior,
    )


def run_variant_comparison(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    btc_daily_df: pd.DataFrame | None,
    *,
    symbol: str,
    timeframe: str,
    order_block_params: OrderBlockParams | None = LEGACY_OB_PARAMS,
    backtest_config: BacktestConfig | None = None,
) -> list[VariantRow]:
    """숏 3안((가)/(나)/(다)) + 무게이트 기준선을 같은 IS/OOS 분할로 비교한다."""
    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    n = len(frame)
    if n < 30:
        return []

    is_end = _split_bars(n)
    warmup_bars = min(is_end, max(60, n // 6))
    cfg = backtest_config or default_backtest_config(timeframe)
    # ⚠️ `band_bar`는 당시 값(`tap`)으로 명시 고정한다(WAN-132 기본값 전환).
    base_params = pin_band_bar(ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime"))

    def eval_is(
        params: ConfluenceParams, signal_filter: RegimeLookup | None = None
    ) -> SegmentMetrics:
        return _evaluate_zone_limit_segment(
            frame,
            one_min_df,
            timeframe,
            context_start=0,
            seg_start=0,
            seg_end=is_end,
            params=params,
            order_block_params=order_block_params,
            backtest_config=cfg,
            signal_filter=signal_filter,
        )

    def eval_oos(
        params: ConfluenceParams, signal_filter: RegimeLookup | None = None
    ) -> SegmentMetrics:
        return _evaluate_zone_limit_segment(
            frame,
            one_min_df,
            timeframe,
            context_start=max(0, is_end - warmup_bars),
            seg_start=is_end,
            seg_end=n,
            params=params,
            order_block_params=order_block_params,
            backtest_config=cfg,
            signal_filter=signal_filter,
        )

    rows: list[VariantRow] = []

    is_base = eval_is(base_params)
    oos_base = eval_oos(base_params)
    rows.append(_row(symbol, timeframe, "baseline_B안", "게이트 없음", is_base, oos_base, None))

    # (나) 숏 완전 제거.
    long_only = base_params.model_copy(update={"short_enabled": False})
    is_na = eval_is(long_only)
    oos_na = eval_oos(long_only)
    rows.append(
        _row(
            symbol,
            timeframe,
            "나_숏제거",
            "short_enabled=False",
            is_na,
            oos_na,
            oos_na.total_return > oos_base.total_return,
        )
    )

    # (다) 숏 유지 + 손익비 게이트만. IS에서 후보 중 IS 총수익률 최선을 고정.
    best_rr = _MIN_RR_GRID[0]
    best_is_return = float("-inf")
    for candidate in _MIN_RR_GRID:
        seg = eval_is(base_params.model_copy(update={"min_rr": candidate}))
        if seg.total_return > best_is_return:
            best_is_return = seg.total_return
            best_rr = candidate
    rr_params = base_params.model_copy(update={"min_rr": best_rr})
    is_da = eval_is(rr_params)
    oos_da = eval_oos(rr_params)
    rows.append(
        _row(
            symbol,
            timeframe,
            "다_RR게이트",
            f"min_rr={best_rr}(IS에서 선택)",
            is_da,
            oos_da,
            oos_da.total_return > oos_base.total_return,
        )
    )

    # (가) 숏 유지 + 레짐 게이트. 레짐 정의 4종 각각 별도 행.
    regime_defs = build_regime_definitions(frame, btc_daily_df)
    for name, lookup in regime_defs.items():
        is_ga = eval_is(base_params, signal_filter=lookup)
        oos_ga = eval_oos(base_params, signal_filter=lookup)
        rows.append(
            _row(
                symbol,
                timeframe,
                f"가_레짐게이트_{name}",
                "레짐 하락 국면에서만 숏 허용",
                is_ga,
                oos_ga,
                oos_ga.total_return > oos_base.total_return,
            )
        )

    return rows


# --------------------------------------------------------------------------- #
# 무작위 진입 대조군 (A안 확정봉 엔진 근사, WAN-68)
# --------------------------------------------------------------------------- #


class RandomControlResult(BaseModel):
    """실제 컨플루언스 진입 vs 무작위 진입(부트스트랩) 총수익률 비교."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    real_total_return: float
    real_num_trades: int
    random_mean_return: float | None
    random_p_value: float | None
    """실제 총수익률 이상인 무작위 반복 비율(단측). 낮을수록 우연이 아닐 확률이 높다."""
    iterations: int


def _fake_entry(
    signal: OrderBlockSignal, pos: int, times: list[int], closes: list[float]
) -> ConfluenceSignal:
    return ConfluenceSignal(
        kind=SignalKind.ENTRY,
        direction=signal.direction,
        time=times[pos],
        price=closes[pos],
        confirmed=True,
        rsi=None,
        order_block=signal.order_block,
        indicators=IndicatorSnapshot(time=times[pos], close=closes[pos], rsi=None, lines={}),
    )


def run_random_entry_control(
    htf_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = LEGACY_OB_PARAMS,
    backtest_config: BacktestConfig | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = 68,
) -> RandomControlResult:
    """같은 청산 규칙으로, 진입만 무작위(부트스트랩)로 바꾼 대조군과 비교한다.

    속도를 위해 상위TF 확정봉 엔진(`backtest.sweep.evaluate`)을 쓴다 — B안의 1분봉
    서브스텝으로 `iterations`회 반복하면 비현실적으로 느리다(모듈 docstring 참고).
    """
    # A안(확정봉 종가) 엔진 경로 — WAN-95로 저장소 기본값이 지정가로 바뀐 뒤에도
    # 이 대조군은 속도 때문에 A안을 쓰므로 명시적으로 선언한다.
    params = confluence_params or CLOSE_ENTRY_DEFAULTS
    cfg = backtest_config or default_backtest_config(timeframe)
    strategy = ConfluenceStrategy(params, order_block_params)

    ob_result = OrderBlockDetector(order_block_params).run(htf_df)
    real = strategy.run(htf_df, ob_result)
    real_backtest = evaluate(
        htf_df,
        confluence_params=params,
        order_block_params=order_block_params,
        backtest_config=cfg,
        order_block_result=ob_result,
    )
    real_long = sum(1 for e in real.confirmed_entries if e.direction is OrderBlockDirection.BULLISH)
    real_short = len(real.confirmed_entries) - real_long

    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    n = len(frame)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    highs = [float(v) for v in frame["high"].astype(float).tolist()]
    lows = [float(v) for v in frame["low"].astype(float).tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    time_to_pos = {t: i for i, t in enumerate(times)}
    line_cols = strategy._line_columns(htf_df)

    universe: dict[OrderBlockDirection, list[int]] = {
        OrderBlockDirection.BULLISH: [],
        OrderBlockDirection.BEARISH: [],
    }
    signal_by_pos: dict[int, OrderBlockSignal] = {}
    for signal in ob_result.signals:
        pos = time_to_pos.get(signal.trigger_time)
        if pos is None:
            continue
        break_pos = ConfluenceStrategy._break_pos(signal.order_block, time_to_pos)
        if break_pos is not None and break_pos <= pos:
            continue
        universe[signal.direction].append(pos)
        signal_by_pos[pos] = signal

    rng = random.Random(seed)
    random_returns: list[float] = []
    for _ in range(iterations):
        sampled: list[OrderBlockSignal] = []
        for direction, count in (
            (OrderBlockDirection.BULLISH, real_long),
            (OrderBlockDirection.BEARISH, real_short),
        ):
            pool = universe[direction]
            if not pool or count <= 0:
                continue
            k = min(count, len(pool))
            for pos in rng.sample(pool, k):
                signal = signal_by_pos[pos]
                break_pos = ConfluenceStrategy._break_pos(signal.order_block, time_to_pos)
                planned = strategy._plan_exit(
                    _fake_entry(signal, pos, times, closes),
                    pos,
                    break_pos,
                    n,
                    times,
                    highs,
                    lows,
                    closes,
                    line_cols,
                )
                sampled.append(
                    OrderBlockSignal(
                        direction=direction,
                        trigger_time=signal.trigger_time,
                        price=signal.price,
                        order_block=signal.order_block,
                        status="active",
                        planned_exit=planned,
                    )
                )
        if not sampled:
            random_returns.append(0.0)
            continue
        random_bt = run_backtest(htf_df, sampled, cfg)
        random_returns.append(random_bt.metrics.total_return)

    p_value = (
        sum(1 for r in random_returns if r >= real_backtest.metrics.total_return)
        / len(random_returns)
        if random_returns
        else None
    )
    return RandomControlResult(
        symbol=symbol,
        timeframe=timeframe,
        real_total_return=real_backtest.metrics.total_return,
        real_num_trades=len(real.confirmed_entries),
        random_mean_return=(sum(random_returns) / len(random_returns)) if random_returns else None,
        random_p_value=p_value,
        iterations=len(random_returns),
    )


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
DEFAULT_VARIANT_REPORT_PATH = Path("backtest/reports/wan68_short_variant_comparison.csv")
DEFAULT_RANDOM_REPORT_PATH = Path("backtest/reports/wan68_random_entry_control.csv")


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
    random_iterations: int = _BOOTSTRAP_ITERATIONS,
) -> tuple[list[VariantRow], list[RandomControlResult]]:
    """로컬 `data/ohlcv.db` 실데이터로 심볼×TF 전부에 대해 두 리포트를 산출한다."""
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return [], []
    if not db_path.exists():
        return [], []

    variant_rows: list[VariantRow] = []
    random_results: list[RandomControlResult] = []
    with OhlcvStore(db_path) as store:
        btc_daily_full = store.load(_BTC_SYMBOL, "1d")
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
                btc_daily_win = (
                    btc_daily_full[btc_daily_full["open_time"] >= start].reset_index(drop=True)
                    if not btc_daily_full.empty
                    else None
                )
                rows = run_variant_comparison(
                    htf_win,
                    one_min_win,
                    btc_daily_win,
                    symbol=symbol,
                    timeframe=timeframe,
                )
                variant_rows.extend(rows)
                print(f"[wan68] {symbol} {timeframe}: variant rows={len(rows)}")

                random_result = run_random_entry_control(
                    htf_win,
                    symbol=symbol,
                    timeframe=timeframe,
                    iterations=random_iterations,
                )
                random_results.append(random_result)
                print(
                    f"[wan68] {symbol} {timeframe}: random control p={random_result.random_p_value}"
                )
    return variant_rows, random_results


def _variant_rows_to_frame(rows: list[VariantRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _random_results_to_frame(results: list[RandomControlResult]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in results])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-68 숏 존폐 판단 + OOS + 무작위 대조군")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--random-iterations", type=int, default=_BOOTSTRAP_ITERATIONS)
    parser.add_argument("--variant-out", type=Path, default=DEFAULT_VARIANT_REPORT_PATH)
    parser.add_argument("--random-out", type=Path, default=DEFAULT_RANDOM_REPORT_PATH)
    args = parser.parse_args(argv)

    variant_rows, random_results = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        random_iterations=args.random_iterations,
    )
    write_csv(_variant_rows_to_frame(variant_rows), args.variant_out)
    write_csv(_random_results_to_frame(random_results), args.random_out)
    print(f"[wan68] variant rows={len(variant_rows)} → {args.variant_out}")
    print(f"[wan68] random control rows={len(random_results)} → {args.random_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
