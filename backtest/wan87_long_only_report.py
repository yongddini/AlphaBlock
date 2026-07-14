"""WAN-87 롱 온리 기본값 재산출 — WAN-81/WAN-84(숏 활성화) 대비 비교.

배경은 이슈 WAN-87 본문 참고. [WAN-86 결정 1](../docs/decisions/wan86.md)에 따라
`ConfluenceParams.short_enabled` 기본값이 `True`(WAN-81)에서 `False`(WAN-87)로
되돌아갔다. 이 모듈은 3심볼×4TF×3년 실데이터로 **롱 온리 기본값**
(`SHORT_DISABLED_PARAMS` = 현재 `ConfluenceParams()` 기본값)과 **숏 활성화**
(`SHORT_ENABLED_PARAMS`, WAN-81/WAN-84가 검증했던 이전 기본값)를 나란히 재실행해
롱 온리 채택으로 성과가 어떻게 바뀌는지 기록한다.

`backtest.wan81_engine_replacement_report`가 이미 만든 로더(`_load_recent`)와 실행
함수(`run_engine`)를 그대로 재사용한다 — 3년 롤링 창은 실행 시점 기준이라 WAN-81/
WAN-84 당시 커밋된 CSV와 직접 비교하면 데이터 창 불일치가 생기므로, 이 스크립트는
두 프리셋을 **같은 실행에서** 함께 돌려 창을 맞춘다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from backtest.sweep import default_backtest_config
from backtest.wan81_engine_replacement_report import (
    _CACHE_DIR,
    _DB_PATH,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    DEFAULT_YEARS,
    EngineRunMetrics,
    _load_recent,
    run_engine,
)
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams
from strategy.order_blocks import OrderBlockDetector

#: WAN-87 롱 온리 프리셋. **A안(종가 진입) 고정** — 이 리포트는 WAN-95(지정가 채택)
#: 이전에 산출됐으므로, 재현 시에도 당시 엔진(`entry_mode="close"`)을 명시적으로
#: 고정한다. WAN-95 이후 `ConfluenceParams()` 기본값은 `zone_limit`이라 이 값과 다르다
#: — 즉 이 리포트의 수치는 더 이상 "채택 기본값 성과"가 아니다.
SHORT_DISABLED_PARAMS = ConfluenceParams(entry_mode="close", rsi_mode="closed_bar")

#: WAN-81/WAN-84가 검증했던 이전 기본값 — 숏 활성화. 비교 기준선으로 명시 고정한다.
SHORT_ENABLED_PARAMS = ConfluenceParams(
    short_enabled=True, entry_mode="close", rsi_mode="closed_bar"
)

ENGINE_PRESETS: dict[str, ConfluenceParams] = {
    "long_only": SHORT_DISABLED_PARAMS,
    "short_enabled": SHORT_ENABLED_PARAMS,
}


def run_report(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    db_path: str = _DB_PATH,
    cache_dir: str = _CACHE_DIR,
) -> list[EngineRunMetrics]:
    """전 심볼×TF에 대해 롱 온리·숏 활성화 두 프리셋을 재실행한다."""
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    rows: list[EngineRunMetrics] = []
    for symbol in symbols:
        for timeframe in timeframes:
            df = _load_recent(store, symbol, timeframe, years)
            if df.empty:
                continue
            cfg = default_backtest_config(timeframe)
            ob_result = OrderBlockDetector().run(df)
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
    return rows


def _rows_to_frame(rows: list[EngineRunMetrics]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _render_summary(df: pd.DataFrame) -> str:
    lines = ["# WAN-87 롱 온리 기본값 재산출 — 숏 활성화(WAN-81/84) 대비 비교", ""]
    lines.append(
        "WAN-86 결정 1로 `ConfluenceParams.short_enabled` 기본값이 `True`(WAN-81)에서 "
        "`False`(WAN-87, 롱 온리)로 되돌아갔다. 아래 `long_only`가 현재 기본값, "
        "`short_enabled`가 WAN-81/WAN-84 검증 당시의 이전 기본값이다. 두 프리셋을 같은 "
        "실행에서 돌려 데이터 창(최근 3년)을 맞췄다."
    )
    lines.append("")
    lines.append("## 심볼 × TF × 프리셋별 성과")
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

    pivot = df.pivot(index=["symbol", "timeframe"], columns="engine", values="total_return")
    if "long_only" in pivot.columns and "short_enabled" in pivot.columns:
        pivot = pivot.assign(delta=pivot["long_only"] - pivot["short_enabled"])
        lines.append("## 롱 온리 전환에 따른 total_return 변화(long_only − short_enabled)")
        lines.append("")
        lines.append("| symbol | timeframe | long_only | short_enabled | delta |")
        lines.append("| -- | -- | -- | -- | -- |")
        for (symbol, timeframe), r in pivot.iterrows():
            lines.append(
                f"| {symbol} | {timeframe} | {r['long_only']:.4f} | "
                f"{r['short_enabled']:.4f} | {r['delta']:+.4f} |"
            )
        lines.append("")
        mean_long = pivot["long_only"].mean()
        mean_short = pivot["short_enabled"].mean()
        better = int((pivot["delta"] > 0).sum())
        total_cells = len(pivot)
        lines.append(
            f"- 평균 total_return: 롱 온리 {mean_long:.4f} vs 숏 활성화 {mean_short:.4f}\n"
            f"- 롱 온리가 우월한 셀: {better}/{total_cells}"
        )
        lines.append("")

    lines.append(
        "## 관련 기록\n\n"
        "- 결정 근거: [`docs/decisions/wan86.md`](../../docs/decisions/wan86.md)"
        "(WAN-86 결정 1 — WAN-84 OOS 롱/숏 분해에서 롱 +3.07% vs 숏 −3.82%).\n"
        "- 숏 활성화 시절 원본 검증: `backtest/reports/wan81_summary.md`, "
        "`backtest/reports/wan84_summary.md`(둘 다 이제는 롱 온리 기본값과 다른 "
        "설정 기준이므로 현재 기본값 성과로는 무효, 숏 활성화 자체의 검증 기록으로만 "
        "유효).\n"
        "- 이 리포트 이후 채택 기본값(`ConfluenceParams()`) 성과는 위 `long_only` 행 "
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

    rows = run_report(
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
    )
    frame = _rows_to_frame(rows)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_dir / "wan87_long_only_comparison.csv", index=False)
    summary = _render_summary(frame)
    (out_dir / "wan87_long_only_summary.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
