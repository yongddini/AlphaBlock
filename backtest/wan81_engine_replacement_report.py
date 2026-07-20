"""WAN-81 메인 엔진 규칙 전면 교체 — 구 엔진 대비 재산출 리포트.

WAN-81이 확정한 새 메인 엔진(볼린저 진입가 재산정 + 첫 탭 무조건/재탭 RSI + 고정
1:1.5R 익절 + 숏 활성화 + 병합 존 재탭)을 3심볼 × 4TF × 3년 실데이터로 돌려, 이
이슈 이전(WAN-73까지) 기본값이던 구 엔진과 비교한다.

## 산출물

1. 거래 수·승률·롱/숏 분해·수익률·최대낙폭을 심볼×TF×엔진(구/신)별로 낸다.
2. **§5(병합 존 `entered` 개별화, 구 WAN-82 버그) 수정으로 되살아난 진입 수**를
   센다 — 구 버그(병합 단위 전체로 `entered` 검사)를 이 스크립트 안에서만 재현해
   (프로덕션 코드에는 없음) 신 로직과의 시그널 수 차이를 잰다.
3. **갭D(`min_stop_distance_fraction=0.003`) 로 기각되는 진입 수**를 신 엔진 확정
   진입 기준으로 센다 — 볼린저 재산정으로 진입가가 존 근단보다 안쪽으로 들어오면
   손절 거리가 짧아져 이 하한에 더 자주 걸린다는 가설을 확인한다.

기존 성과 수치(WAN-19/22/46/50/58/68/70/71/73/74/75/76 등)는 모두 구 엔진
기준이므로 이 리포트 이후로는 무효다(README에 명시).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import run_backtest
from backtest.harness import LEGACY_RSI_GATE_MODE
from backtest.models import BacktestConfig, PositionSide, Trade
from backtest.sweep import default_backtest_config
from data.storage import OhlcvStore
from strategy.confluence import generate_confluence_signals
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    OrderBlockSignal,
)
from strategy.order_blocks import OrderBlockDetector, _build_merged_groups

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
DEFAULT_YEARS: float = 3.0
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)
_DB_PATH = "data/ohlcv.db"
_CACHE_DIR = "data/cache"
_MIN_STOP_DISTANCE_FRACTION = 0.003  # WAN-79 저장소 기본값(execution.sizing).

#: WAN-81 이전(구) 엔진 기본값 — 이번 이슈로 바뀐 필드만 명시적으로 복원한다.
OLD_ENGINE_PARAMS = ConfluenceParams(
    retap_mode="once",
    rsi_gate_mode="extreme",
    take_profit_mode="line",
    take_profit_r=2.0,
    use_line_take_profit=True,
    deviation_filter=None,
    short_enabled=False,
)

#: WAN-81 신 엔진 정의. WAN-87 이전에는 `ConfluenceParams()`의 기본값 자체와 같았으나,
#: 기본값이 그 뒤로 두 번 이 정의에서 멀어져 **바뀐 필드마다 명시 고정**이 쌓였다:
#: `short_enabled`는 WAN-87(WAN-86 결정 1)이 `False`로 되돌렸고, `rsi_gate_mode`는
#: WAN-123(WAN-116 결정 B)이 `unconditional`(게이트 제거)로 옮겼다. 이 리포트가 검증하는
#: "WAN-81 신 엔진"은 **숏 활성화 + 재탭 RSI 게이트**가 그 정의의 일부이므로 둘 다 고정한다.
#: ⚠️ WAN-132(밴드 정본 `tap` → `intrabar_live`)는 **이 리포트를 움직이지 않아 고정하지
#: 않는다** — 이 모듈은 A안(`generate_confluence_signals` + `run_backtest`, 봉 단위)으로
#: 도는데 봉 단위에서는 `intrabar_live`가 `tap`과 정확히 같은 값이다
#: (`ConfluenceStrategy.deviation_band_at`). 고정하면 없는 방어를 흉내 내는 셈이라 뺐다.
NEW_ENGINE_PARAMS = ConfluenceParams(short_enabled=True, rsi_gate_mode=LEGACY_RSI_GATE_MODE)

ENGINE_PRESETS: dict[str, ConfluenceParams] = {
    "old": OLD_ENGINE_PARAMS,
    "new": NEW_ENGINE_PARAMS,
}


class EngineRunMetrics(BaseModel):
    """한 (심볼, TF, 엔진)의 백테스트 성과 요약."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    engine: str
    bars: int
    num_trades: int
    long_trades: int
    short_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float


def _load_recent(store: OhlcvStore, symbol: str, timeframe: str, years: float) -> pd.DataFrame:
    """`store`에서 심볼×TF 전체를 로드해 마지막 시각 기준 최근 `years`년만 남긴다."""
    df = store.load(symbol, timeframe)
    if df.empty:
        return df
    last = int(df["open_time"].iloc[-1])
    start = last - int(years * _YEAR_MS)
    return df[df["open_time"] >= start].reset_index(drop=True)


def _side_counts(trades: list[Trade]) -> tuple[int, int]:
    long_n = sum(1 for t in trades if t.side is PositionSide.LONG)
    short_n = sum(1 for t in trades if t.side is PositionSide.SHORT)
    return long_n, short_n


def run_engine(
    df: pd.DataFrame,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    order_block_result: OrderBlockResult,
    *,
    symbol: str,
    timeframe: str,
    engine: str,
) -> EngineRunMetrics:
    result = generate_confluence_signals(df, params, order_block_result=order_block_result)
    bt = run_backtest(df, result.order_block_signals, cfg)
    long_n, short_n = _side_counts(bt.trades)
    return EngineRunMetrics(
        symbol=symbol,
        timeframe=timeframe,
        engine=engine,
        bars=len(df),
        num_trades=bt.metrics.num_trades,
        long_trades=long_n,
        short_trades=short_n,
        win_rate=bt.metrics.win_rate,
        total_return=bt.metrics.total_return,
        max_drawdown=bt.metrics.max_drawdown,
    )


# --------------------------------------------------------------------------- #
# §5(구 WAN-82) 되살아난 진입 수 — 구 버그를 이 스크립트 안에서만 재현
# --------------------------------------------------------------------------- #


def _buggy_merged_signal_count(
    archive: list[OrderBlock],
    times: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> int:
    """§5 수정 **이전**(구 WAN-82 버그: `entered`를 병합 단위 전체로만 검사) 시그널 수.

    프로덕션 코드(`strategy.order_blocks._generate_merged_signals`)는 이미 개별 존
    단위로 고쳐졌으므로, 그 차이를 재는 이 리포트만을 위해 여기서 버그 있는 버전을
    재구현한다(다른 곳에서 쓰지 않는다).
    """
    n = len(times)
    if n == 0 or not archive:
        return 0
    to_add = sorted(enumerate(archive), key=lambda p: (p[1].confirmed_time, p[0]))
    add_ptr = 0
    alive: list[tuple[int, OrderBlock]] = []
    entered: set[int] = set()
    groups = []
    dirty = True
    count = 0
    for t in range(n):
        now = times[t]
        while add_ptr < len(to_add) and to_add[add_ptr][1].confirmed_time <= now:
            alive.append(to_add[add_ptr])
            add_ptr += 1
            dirty = True
        kept: list[tuple[int, OrderBlock]] = []
        for idx, ob in alive:
            if ob.swept_time is not None and ob.swept_time <= now:
                dirty = True
                continue
            if ob.break_time is not None and ob.break_time == now:
                dirty = True
            kept.append((idx, ob))
        alive = kept
        if dirty:
            groups = _build_merged_groups(alive, now)
            dirty = False
        for g in groups:
            if g.member_indices & entered:
                continue
            if now <= g.latest_confirmed:
                continue
            if g.break_time is not None and now > g.break_time:
                continue
            if lows[t] <= g.top and highs[t] >= g.bottom:
                count += 1
                entered |= g.member_indices
    return count


def count_revived_entries(df: pd.DataFrame, order_block_result: OrderBlockResult) -> int:
    """구 버그(병합 단위 전체 `entered`) 시그널 수. `combine_obs=True` 경로 전용.

    신 로직 시그널 수는 호출부가 이미 갖고 있는 `order_block_result.signals`로 비교한다.
    """
    frame = df[df["closed"].astype(bool)] if "closed" in df.columns else df
    frame = frame.sort_values("open_time").reset_index(drop=True)
    times = [int(t) for t in frame["open_time"].astype("int64")]
    highs = [float(v) for v in frame["high"]]
    lows = [float(v) for v in frame["low"]]
    closes = [float(v) for v in frame["close"]]
    return _buggy_merged_signal_count(order_block_result.order_blocks, times, highs, lows, closes)


# --------------------------------------------------------------------------- #
# 갭D: min_stop_distance_fraction으로 기각되는 진입 수
# --------------------------------------------------------------------------- #


def count_gap_d_rejections(order_block_signals: list[OrderBlockSignal]) -> int:
    """신 엔진 확정 진입 중 손절 거리가 `min_stop_distance_fraction` 미만인 건수."""
    rejected = 0
    for sig in order_block_signals:
        ob = sig.order_block
        is_long = ob.direction is OrderBlockDirection.BULLISH
        boundary = ob.bottom if is_long else ob.top
        entry_price = sig.price
        stop_distance = abs(entry_price - boundary)
        min_distance = _MIN_STOP_DISTANCE_FRACTION * entry_price
        if stop_distance < min_distance:
            rejected += 1
    return rejected


def run_report(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    db_path: str = _DB_PATH,
    cache_dir: str = _CACHE_DIR,
) -> tuple[list[EngineRunMetrics], int, int, int]:
    """전 심볼×TF에 대해 구/신 엔진을 재실행한다.

    Returns:
        (행 목록, 구버그 시그널 총합, 신로직 시그널 총합(= §5로 되살아난 수 계산용),
        갭D 기각 총합).
    """
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    rows: list[EngineRunMetrics] = []
    buggy_total = 0
    fixed_total = 0
    gap_d_total = 0
    for symbol in symbols:
        for timeframe in timeframes:
            df = _load_recent(store, symbol, timeframe, years)
            if df.empty:
                continue
            cfg = default_backtest_config(timeframe)
            ob_result = OrderBlockDetector().run(df)

            buggy = count_revived_entries(df, ob_result)
            buggy_total += buggy
            fixed_total += len(ob_result.signals)

            for engine_name, params in ENGINE_PRESETS.items():
                row = run_engine(
                    df,
                    params,
                    cfg,
                    ob_result,
                    symbol=symbol,
                    timeframe=timeframe,
                    engine=engine_name,
                )
                rows.append(row)
                if engine_name == "new":
                    new_result = generate_confluence_signals(
                        df, params, order_block_result=ob_result
                    )
                    gap_d_total += count_gap_d_rejections(new_result.order_block_signals)
    return rows, buggy_total, fixed_total, gap_d_total


def _rows_to_frame(rows: list[EngineRunMetrics]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _render_summary(df: pd.DataFrame, buggy_total: int, fixed_total: int, gap_d_total: int) -> str:
    lines = ["# WAN-81 메인 엔진 재산출 — 구 엔진 대비 비교", ""]
    lines.append(
        "구 엔진(WAN-73까지 기본값: `retap_mode=once`, `rsi_gate_mode=extreme`, "
        "`take_profit_mode=line`, `deviation_filter=None`, `short_enabled=False`) "
        "vs 신 엔진(WAN-81 기본값: 볼린저 진입가, `retap_mode=every_tap`, "
        "`rsi_gate_mode=first_tap_free`, `take_profit_mode=fixed_r`(1.5R), "
        "`short_enabled=True`)."
    )
    lines.append("")
    lines.append("## 심볼 × TF × 엔진별 성과")
    lines.append("")
    cols = [
        "symbol",
        "timeframe",
        "engine",
        "num_trades",
        "long_trades",
        "short_trades",
        "win_rate",
        "total_return",
        "max_drawdown",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["--"] * len(cols)) + " |")
    for _, row in df[cols].iterrows():
        lines.append(
            "| "
            + " | ".join(
                f"{row[c]:.4f}" if isinstance(row[c], float) else str(row[c]) for c in cols
            )
            + " |"
        )
    lines.append("")
    lines.append("## §5(구 WAN-82 버그) 수정으로 되살아난 진입 수")
    lines.append("")
    lines.append(f"- 구 버그(병합 단위 전체 `entered`) 시그널 총합: {buggy_total}")
    lines.append(f"- 신 로직(개별 존 `entered`) 시그널 총합: {fixed_total}")
    lines.append(f"- 되살아난 진입 수(신 - 구): {fixed_total - buggy_total}")
    lines.append("")
    lines.append("## 갭D: min_stop_distance_fraction(0.003)으로 기각된 진입 수")
    lines.append("")
    lines.append(f"- 신 엔진 확정 진입 중 손절 거리 하한 미만으로 기각된 건수: {gap_d_total}")
    lines.append("")
    lines.append(
        "## 기존 수치 무효 선언\n\n"
        "WAN-19/22/46/50/58/68/70/71/73/74/75/76 등 기존 성과 리포트는 모두 이 "
        "이슈(WAN-81) 이전 구 엔진 기준이다. 이 리포트 이후로는 위 표의 `new` 행을 "
        "기준으로 삼는다."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--db-path", default=_DB_PATH)
    parser.add_argument("--cache-dir", default=_CACHE_DIR)
    parser.add_argument("--out-dir", default="backtest/reports", help="CSV/마크다운 출력 디렉터리")
    args = parser.parse_args()

    rows, buggy_total, fixed_total, gap_d_total = run_report(
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
    )
    frame = _rows_to_frame(rows)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_dir / "wan81_engine_comparison.csv", index=False)
    summary = _render_summary(frame, buggy_total, fixed_total, gap_d_total)
    (out_dir / "wan81_summary.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
