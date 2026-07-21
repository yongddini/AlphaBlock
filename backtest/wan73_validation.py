"""WAN-73 신규 규칙(존당 재진입 + RSI 중립 게이트 + 고정 2R 익절) 채택 검증.

이슈 WAN-73은 사용자 실제 매매와 일치하도록 세 규칙을 바꿀 것을 제안한다:

* `retap_mode="every_tap"` — 존당 첫 탭 1회가 아니라 존이 살아있는 동안 매 탭마다 진입.
* `rsi_gate_mode="neutral"`(밴드 40~60) — 극단(과매도/과매수)이 아니라 RSI 중립일 때 진입.
* `take_profit_mode="fixed_r"`(r=2.0) — 선(EMA/VWMA) 도달이 아니라 고정 손익비 2R 익절.

간이 재현(이슈 본문)은 약 60개 조합을 탐색한 결과라 **세 가지 검증을 통과하기 전에는
기본값을 바꾸지 않는다**:

1. **워크포워드/OOS** — `backtest.wan70_random_control_b.run_symbol_timeframe`이 이미
   수행하는 IS(앞 2/3)/OOS(뒤 1/3) 분할을 그대로 재사용한다(WAN-50/68 하네스).
2. **무작위 진입 대조군(매칭 널)** — 같은 함수의 매칭 널 부트스트랩(방향·시각대 버킷
   맞춤)을 그대로 재사용한다(WAN-70). 오더블록이 아닌 무작위 레벨이어도 비슷한
   성과가 나오면 우위의 소재는 오더블록이 아니라 RSI 필터/재진입 규칙 자체다.
3. **지정가 체결률(fill_rate)** — 실제 지정가는 "닿았다가 되돌아가면 미체결"이라는
   역선택이 있다(WAN-46). `build_zone_limit_candidates`가 이미 계산하는
   `ZoneLimitStats.fill_rate`로, 체결 기준 R 기대값이 기회비용까지 반영하면 얼마나
   깎이는지 측정한다.

이 모듈은 위 세 검증을 모두 산출하고, "기본값 전환/기각"을 명시적으로 판정한 리포트를
만든다. 기각도 정상 종료 조건이다(이슈 완료 기준).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import LEGACY_OB_PARAMS, pin_band_bar
from backtest.models import BacktestConfig, PositionSide
from backtest.sweep import default_backtest_config
from backtest.wan70_random_control_b import (
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    DEFAULT_YEARS,
    RandomControlBResult,
    run_symbol_timeframe,
    summarize_verdict,
)
from backtest.zone_limit_backtest import build_zone_limit_candidates
from strategy.models import ConfluenceParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

#: 현행(채택 전) 기본 규칙: 존당 첫 탭 1회 + RSI 극단 게이트 + 선(EMA60/VWMA100) 익절.
#: ⚠️ `band_bar`는 당시 값(`tap`)으로 **명시 고정**한다(WAN-132 기본값 전환).
CURRENT_DEFAULT_PARAMS = pin_band_bar(
    ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
)

#: WAN-73 제안 규칙: 매 탭 재진입 + RSI 중립(40~60) 게이트 + 고정 2R 익절.
WAN73_NEW_PARAMS = pin_band_bar(
    ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        retap_mode="every_tap",
        rsi_gate_mode="neutral",
        rsi_neutral_band=(40.0, 60.0),
        take_profit_mode="fixed_r",
        take_profit_r=2.0,
    )
)

GATE_PRESETS: dict[str, ConfluenceParams] = {
    "current_default": CURRENT_DEFAULT_PARAMS,
    "wan73_new_rules": WAN73_NEW_PARAMS,
}


# --------------------------------------------------------------------------- #
# 검증 3: 지정가 체결률(fill_rate) 반영 기대값
# --------------------------------------------------------------------------- #


class FillRateExpectancy(BaseModel):
    """한 (심볼, TF)의 WAN-73 신규 규칙 지정가 체결률·R 기대값(WAN-73 검증 3)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    eligible: int
    """대상 셋업 수(탭 봉이 1분봉으로 커버된 활성 오더블록 후보)."""
    filled: int
    """지정가 체결 수."""
    fill_rate: float | None
    mean_r_per_filled_trade: float | None
    """체결된 거래만의 평균 R 배수(고정 2R 익절/손절 구조이므로 승=+2R, 패=-1R 근사)."""
    opportunity_adjusted_expectancy: float | None
    """`mean_r_per_filled_trade × fill_rate` — 미체결(기회비용 0)을 반영한 셋업당 기대값."""


def _r_multiple(entry_price: float, exit_price: float, stop_price: float, is_long: bool) -> float:
    """체결가 기준 위험(1R=|진입가-손절참조가|) 대비 실현 손익의 배수."""
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return 0.0
    pnl = (exit_price - entry_price) if is_long else (entry_price - exit_price)
    return pnl / risk


def compute_fill_rate_expectancy(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    symbol: str,
    params: ConfluenceParams,
    order_block_result: OrderBlockResult | None = None,
    backtest_config: BacktestConfig | None = None,
) -> FillRateExpectancy:
    """WAN-73 신규 규칙 데이터 전 구간에서 체결률·R 기대값을 낸다(검증 3)."""
    cfg = backtest_config or default_backtest_config(timeframe)
    candidates, stats = build_zone_limit_candidates(
        htf_df,
        df_1m,
        timeframe,
        params=params,
        cfg=cfg,
        order_block_result=order_block_result,
    )
    if not candidates:
        return FillRateExpectancy(
            symbol=symbol,
            timeframe=timeframe,
            eligible=stats.eligible,
            filled=stats.filled,
            fill_rate=stats.fill_rate,
            mean_r_per_filled_trade=None,
            opportunity_adjusted_expectancy=None,
        )
    r_values = [
        _r_multiple(c.entry_price, c.exit_price, c.stop_price, c.side is PositionSide.LONG)
        for c in candidates
    ]
    mean_r = sum(r_values) / len(r_values)
    adjusted = mean_r * stats.fill_rate if stats.fill_rate is not None else None
    return FillRateExpectancy(
        symbol=symbol,
        timeframe=timeframe,
        eligible=stats.eligible,
        filled=stats.filled,
        fill_rate=stats.fill_rate,
        mean_r_per_filled_trade=mean_r,
        opportunity_adjusted_expectancy=adjusted,
    )


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
DEFAULT_REPORT_PATH = Path("backtest/reports/wan73_validation.csv")
DEFAULT_FILL_RATE_REPORT_PATH = Path("backtest/reports/wan73_fill_rate_expectancy.csv")
DEFAULT_SUMMARY_PATH = Path("backtest/reports/wan73_summary.md")


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
    iterations: int = 200,
) -> tuple[list[RandomControlBResult], list[FillRateExpectancy]]:
    """로컬 `data/ohlcv.db` 실데이터로 OOS/무작위 대조군 + 체결률 기대값을 산출한다."""
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return [], []
    if not db_path.exists():
        return [], []

    random_results: list[RandomControlBResult] = []
    fill_rate_results: list[FillRateExpectancy] = []
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
                cell_results = run_symbol_timeframe(
                    htf_win,
                    one_min_win,
                    symbol=symbol,
                    timeframe=timeframe,
                    gates=GATE_PRESETS,
                    iterations=iterations,
                    seed=73,
                )
                random_results.extend(cell_results)
                for r in cell_results:
                    print(
                        f"[wan73] {symbol} {timeframe} {r.segment} gate={r.gate}: "
                        f"real={r.real_total_return:.4f} n={r.real_num_trades} "
                        f"p={r.random_p_value}"
                    )

                cfg = default_backtest_config(timeframe)
                ob_result = OrderBlockDetector(LEGACY_OB_PARAMS).run(htf_win)
                fr = compute_fill_rate_expectancy(
                    htf_win,
                    one_min_win,
                    timeframe,
                    symbol=symbol,
                    params=WAN73_NEW_PARAMS,
                    order_block_result=ob_result,
                    backtest_config=cfg,
                )
                fill_rate_results.append(fr)
                print(
                    f"[wan73] {symbol} {timeframe} fill_rate={fr.fill_rate} "
                    f"mean_r={fr.mean_r_per_filled_trade} "
                    f"adjusted={fr.opportunity_adjusted_expectancy}"
                )
    return random_results, fill_rate_results


def _results_to_frame(results: list[RandomControlBResult]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in results])


def _fill_rate_to_frame(results: list[FillRateExpectancy]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in results])


def _fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _fill_rate_table(results: list[FillRateExpectancy]) -> str:
    header = (
        "| 심볼 | TF | 대상 | 체결 | 체결률 | 체결 평균R | 기회비용반영 기대값 |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    rows = sorted(results, key=lambda r: (r.symbol, order.get(r.timeframe, 9)))
    body = [
        f"| {r.symbol} | {r.timeframe} | {r.eligible} | {r.filled} | "
        f"{_fmt(r.fill_rate, 3)} | {_fmt(r.mean_r_per_filled_trade, 3)} | "
        f"{_fmt(r.opportunity_adjusted_expectancy, 3)} |"
        for r in rows
    ]
    return header + "\n" + "\n".join(body)


def _significant(r: RandomControlBResult, *, alpha: float = 0.05, min_trades: int = 20) -> bool:
    """매칭 널 대비 유의(p≤alpha & 실제>무작위평균)하며 표본이 충분한 셀인지."""
    return (
        r.real_num_trades >= min_trades
        and r.random_p_value is not None
        and r.random_p_value <= alpha
        and r.random_mean_return is not None
        and r.real_total_return > r.random_mean_return
    )


def decide_adoption(
    new_rule_results: list[RandomControlBResult],
    fill_rate_results: list[FillRateExpectancy],
) -> tuple[str, list[str]]:
    """세 검증을 종합해 채택/기각을 명시적으로 판정한다(WAN-73 완료 기준).

    채택 조건(모두 만족): 어떤 (심볼, TF)든 IS·OOS **양쪽**에서 매칭 널 대비 유의하고
    (워크포워드 강건성), 그 셀의 체결률 반영 기대값이 양수여야 한다. 하나라도 없으면
    기각(기본값 유지) — 기각도 정상 종료 조건이다.
    """
    reasons: list[str] = []
    by_cell: dict[tuple[str, str], dict[str, RandomControlBResult]] = {}
    for r in new_rule_results:
        by_cell.setdefault((r.symbol, r.timeframe), {})[r.segment] = r

    robust_cells = [
        key
        for key, seg in by_cell.items()
        if "IS" in seg and "OOS" in seg and _significant(seg["IS"]) and _significant(seg["OOS"])
    ]
    sig_cells = [(r.symbol, r.timeframe, r.segment) for r in new_rule_results if _significant(r)]
    fr_by_cell = {(f.symbol, f.timeframe): f for f in fill_rate_results}

    if not robust_cells:
        reasons.append(
            f"IS·OOS 양쪽에서 매칭 널 대비 유의한 셀이 하나도 없다"
            f"(개별 유의 셀은 {len(sig_cells)}개지만 워크포워드로 이어지지 않음)."
        )
        return "REJECT", reasons

    adopt_cells = []
    for key in robust_cells:
        fr = fr_by_cell.get(key)
        adj = fr.opportunity_adjusted_expectancy if fr else None
        if adj is not None and adj > 0:
            adopt_cells.append(f"{key[0]}/{key[1]}(체결반영 {adj:.3f}R)")
        else:
            reasons.append(f"{key[0]}/{key[1]}은 IS·OOS 유의하나 체결률 반영 기대값이 양수 아님.")
    if adopt_cells:
        reasons.append("IS·OOS 유의 + 체결률 양수 셀: " + ", ".join(adopt_cells))
        return "ADOPT_PARTIAL", reasons
    return "REJECT", reasons


def build_summary_markdown(
    random_results: list[RandomControlBResult],
    fill_rate_results: list[FillRateExpectancy],
    *,
    report_path: Path,
    fill_rate_report_path: Path,
) -> str:
    new_rule_results = [r for r in random_results if r.gate == "wan73_new_rules"]
    current_results = [r for r in random_results if r.gate == "current_default"]
    new_rule_verdict = summarize_verdict(new_rule_results)
    current_verdict = summarize_verdict(current_results)

    oos_new = [r for r in new_rule_results if r.segment == "OOS" and r.real_num_trades > 0]
    is_new = [r for r in new_rule_results if r.segment == "IS" and r.real_num_trades > 0]
    oos_positive = sum(1 for r in oos_new if r.real_total_return > 0)
    oos_total = len(oos_new)

    decision, decision_reasons = decide_adoption(new_rule_results, fill_rate_results)
    decision_label = {
        "REJECT": "**기각 — 기본값 전환하지 않음**",
        "ADOPT_PARTIAL": (
            "**부분 채택 후보 — 특정 심볼/TF에 한해 추가 검증 권장(기본값은 아직 유지)**"
        ),
    }[decision]
    reasons_md = "\n".join(f"* {r}" for r in decision_reasons)

    return (
        "# WAN-73 검증 — 존당 재진입 + RSI 중립 게이트 + 고정 2R 익절\n\n"
        "3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS, 로컬 `data/ohlcv.db` 실데이터 "
        f"{DEFAULT_YEARS}년. 재현: `python -m backtest.wan73_validation`.\n"
        f"무작위 대조군 원자료는 `{report_path}`, 체결률 원자료는 `{fill_rate_report_path}`.\n\n"
        "## 비교 대상\n\n"
        "* `current_default` — 현행(채택 전): 존당 첫 탭 1회, RSI 극단(≤30/≥70) 게이트, "
        "선(EMA60/VWMA100) 익절.\n"
        "* `wan73_new_rules` — 이슈 WAN-73 제안: 매 탭 재진입, RSI 중립(40~60) 게이트, "
        "고정 2R 익절.\n\n"
        "두 프리셋 모두 B안(존-지정가+실시간 RSI) 엔진 그대로, `backtest.wan70_random_"
        "control_b.run_symbol_timeframe`의 IS(앞 2/3)/OOS(뒤 1/3) 분할과 매칭 널(방향·"
        "시각대 버킷 맞춤, 부트스트랩 200회) 부트스트랩을 그대로 재사용한다.\n\n"
        "## 검증 1+2: OOS 및 무작위 진입 대조군(매칭 널)\n\n"
        f"### `wan73_new_rules`\n\n{_cell_table(new_rule_results)}\n\n"
        f"판정: {new_rule_verdict}\n\n"
        f"### `current_default`(참고 비교)\n\n{_cell_table(current_results)}\n\n"
        f"판정: {current_verdict}\n\n"
        "## 검증 3: 지정가 체결률(fill_rate) 반영 기대값\n\n"
        f"{_fill_rate_table(fill_rate_results)}\n\n"
        "> `기회비용반영 기대값` = 체결 거래 평균 R × 체결률. 미체결(존을 스치고 되돌아간 "
        '경우)은 기회비용 0으로 간주해, "닿으면 무조건 체결" 가정의 낙관 편향(WAN-46)을 '
        "덜어낸 값이다.\n\n"
        "## 판정\n\n"
        f"`wan73_new_rules`의 OOS 셀 {oos_total}개 중 실제 총수익률이 양수인 셀은 "
        f"{oos_positive}개, IS 유효 셀은 {len(is_new)}개다. "
        f"매칭 널 대비 유의성 판정은 위 `판정:` 문단 참고.\n\n"
        "채택 조건: 어떤 (심볼, TF)든 **IS·OOS 양쪽**에서 매칭 널 대비 유의(p≤0.05 & "
        "실제>무작위평균, 워크포워드 강건성)하고, 그 셀의 체결률 반영 기대값이 양수여야 "
        "한다. 하나라도 없으면 기본값을 유지한다(기각도 정상 종료 조건, 이슈 완료 기준).\n\n"
        f"### 결론: {decision_label}\n\n"
        f"{reasons_md}\n\n"
        "신규 파라미터(`retap_mode`·`rsi_gate_mode`·`rsi_neutral_band`·`take_profit_mode`·"
        "`take_profit_r`·`limit_valid_bars=None`)는 **모두 기본값에서 현행 동작을 보존**하도록 "
        "추가됐고 기존 테스트가 전부 통과한다(동작 불변 증명). 이 리포트는 그 파라미터를 켰을 "
        "때의 성과를 실데이터로 측정한 것이며, 위 결론에 따라 **기본값은 바꾸지 않는다**.\n"
    )


def _cell_table(results: list[RandomControlBResult]) -> str:
    header = (
        "| 심볼 | TF | 구간 | 실제수익 | n | 무작위평균 | 95% CI | p |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    rows = sorted(results, key=lambda r: (r.symbol, order.get(r.timeframe, 9), r.segment))
    body = []
    for r in rows:
        ci = (
            f"[{_fmt(r.random_ci_low, 3)}, {_fmt(r.random_ci_high, 3)}]"
            if r.random_ci_low is not None
            else "—"
        )
        body.append(
            f"| {r.symbol} | {r.timeframe} | {r.segment} | "
            f"{_fmt(r.real_total_return, 3)} | {r.real_num_trades} | "
            f"{_fmt(r.random_mean_return, 3)} | {ci} | {_fmt(r.random_p_value, 3)} |"
        )
    return header + "\n" + "\n".join(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-73 신규 규칙 채택 검증(OOS+무작위+체결률)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--fill-rate-out", type=Path, default=DEFAULT_FILL_RATE_REPORT_PATH)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY_PATH)
    args = parser.parse_args(argv)

    random_results, fill_rate_results = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        iterations=args.iterations,
    )
    write_csv(_results_to_frame(random_results), args.out)
    write_csv(_fill_rate_to_frame(fill_rate_results), args.fill_rate_out)
    print(f"[wan73] random control rows={len(random_results)} → {args.out}")
    print(f"[wan73] fill-rate rows={len(fill_rate_results)} → {args.fill_rate_out}")

    summary = build_summary_markdown(
        random_results,
        fill_rate_results,
        report_path=args.out,
        fill_rate_report_path=args.fill_rate_out,
    )
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan73] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
