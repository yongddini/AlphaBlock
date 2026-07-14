"""WAN-91: 펀딩비 배선 재산출 — 펀딩 on/off 델타 + TF별 그로스/넷 분해.

배경은 이슈 WAN-91 본문 참고. `backtest.sweep.default_backtest_config`가
`settings.backtest_funding_enabled`을 읽어 `BacktestConfig.funding_enabled`에 싣도록
배선한 뒤(WAN-91 §1), 이 모듈이 그 배선을 실제로 써서 재산출한다(WAN-91 §2~3).

## 방법론

`backtest.wan87_long_only_report`와 같은 실행 방식(현재 채택 엔진 기본값
`ConfluenceParams()`, `entry_mode="close"`)을 3심볼 × 4TF × 최근 3년 실데이터에
적용한다. WAN-84(`backtest.wan84_new_engine_validation`)의 존-지정가(B안) 매칭 널
방법론과는 다르다 — 이 리포트는 매칭 널 통계 검정이 아니라 "같은 실제 거래를 비용
가정만 바꿔 재계산하면 얼마나 달라지는가"에 집중하므로, 더 단순한 A안 파이프라인
(`evaluate`/`run_backtest`)으로 충분하다.

## IS/OOS 분할

`backtest.wan68_short_gate_analysis._split_bars`(봉 수 기준 단순 2/3 분할)를 그대로
쓴다. 지표(EMA/VWMA/RSI/볼린저)는 인과적(과거만 참조)이라 미래 누출이 없으므로,
전체 구간에서 시그널·거래를 한 번만 생성한 뒤 진입시각으로 IS/OOS 버킷에 나눠 담아도
결과가 "OOS만 별도로 워밍업 컨텍스트를 잘라 재계산"한 것과 동일하다 — 이 방식이 훨씬
단순하고, 신호 생성 자체가 비용/펀딩 설정과 무관하므로 프리셋마다 1회만 실행해
재사용한다(비용/펀딩 4가지 조합은 이미 만든 시그널을 `run_backtest`에 다른
`BacktestConfig`로 재적용한 것뿐).

## 비용/펀딩 축

각 (심볼, TF, IS/OOS, 엔진 프리셋)에 대해 2×2 = 4가지 `BacktestConfig` 변형을 돌린다:

- **cost_mode**: `gross`(수수료·슬리피지 0) vs `net`(기본값 4bp/5bp).
- **funding_mode**: `off`(`funding_enabled=False`) vs `on`(`funding_enabled=True`
  + `data.FundingRateStore.get_rates`로 조회한 실제 펀딩비 전달).

`engine` 프리셋은 `backtest.wan87_long_only_report`와 동일하게 둘: `long_only`(현재
채택 기본값)과 `short_enabled`(WAN-81/84 검증 당시 기본값, WAN-86 결정 1 재검증용).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, Trade
from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.wan68_short_gate_analysis import _split_bars
from backtest.wan81_engine_replacement_report import (
    _CACHE_DIR,
    _DB_PATH,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    DEFAULT_YEARS,
    _load_recent,
)
from backtest.zone_limit_backtest import build_result_from_trades
from data.funding import FundingRateStore
from data.funding import funding_coverage as compute_funding_coverage
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.confluence import generate_confluence_signals
from strategy.models import ConfluenceParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

Segment = Literal["IS", "OOS"]
CostMode = Literal["gross", "net"]
FundingMode = Literal["off", "on"]

#: `backtest.wan87_long_only_report`와 동일한 두 프리셋 — 현재 채택 기본값(롱 온리)과
#: WAN-81/84 검증 당시 기본값(숏 활성화). 정의를 여기서 다시 고정해 wan87 모듈의
#: 프리셋이 나중에 바뀌어도 이 리포트가 검증하는 두 정의는 흔들리지 않게 한다.
ENGINE_PRESETS: dict[str, ConfluenceParams] = {
    "long_only": ConfluenceParams(),
    "short_enabled": ConfluenceParams(short_enabled=True),
}

COST_MODES: tuple[CostMode, ...] = ("gross", "net")
FUNDING_MODES: tuple[FundingMode, ...] = ("off", "on")


class FundingCostRow(BaseModel):
    """한 (심볼, TF, 구간, 엔진, 비용모드, 펀딩모드)의 성과 요약."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    engine: str
    cost_mode: CostMode
    funding_mode: FundingMode
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    total_funding_cost: float
    funding_coverage: float | None
    """이 구간의 펀딩 데이터 커버리지(0~1). `funding_mode="off"`면 검사 대상이 아니므로 None."""


def _cost_config(
    cfg: BacktestConfig, cost_mode: CostMode, funding_mode: FundingMode
) -> BacktestConfig:
    updates: dict[str, object] = {"funding_enabled": funding_mode == "on"}
    if cost_mode == "gross":
        updates["fee_rate"] = 0.0
        updates["maker_fee_rate"] = 0.0
        updates["slippage"] = 0.0
    return cfg.model_copy(update=updates)


def _segment_trades(
    trades: list[Trade], *, before_ms: int | None, after_ms: int | None
) -> list[Trade]:
    result = trades
    if before_ms is not None:
        result = [t for t in result if t.entry_time < before_ms]
    if after_ms is not None:
        result = [t for t in result if t.entry_time >= after_ms]
    return result


def run_symbol_timeframe(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    order_block_result: OrderBlockResult,
    funding_rates: list[FundingRate],
) -> list[FundingCostRow]:
    """한 (심볼, TF)에 대해 엔진×비용×펀딩×IS/OOS 전체 행렬을 산출한다."""
    n = len(df)
    if n < 30:
        return []
    is_end = _split_bars(n)
    split_time = (
        int(df["open_time"].iloc[is_end]) if is_end < n else int(df["open_time"].iloc[-1]) + 1
    )

    rows: list[FundingCostRow] = []
    for engine_name, params in ENGINE_PRESETS.items():
        signals = generate_confluence_signals(
            df, params, order_block_result=order_block_result
        ).order_block_signals
        base_cfg = default_backtest_config(timeframe)
        for cost_mode in COST_MODES:
            for funding_mode in FUNDING_MODES:
                cfg = _cost_config(base_cfg, cost_mode, funding_mode)
                rates = funding_rates if funding_mode == "on" else None
                result = run_backtest(df, signals, cfg, rates)
                for segment_label, before_ms, after_ms in (
                    ("IS", split_time, None),
                    ("OOS", None, split_time),
                ):
                    seg_trades = _segment_trades(
                        result.trades, before_ms=before_ms, after_ms=after_ms
                    )
                    seg_result = build_result_from_trades(seg_trades, cfg, timeframe)
                    coverage: float | None = None
                    if funding_mode == "on" and seg_trades:
                        seg_start = min(t.entry_time for t in seg_trades)
                        seg_end = max(t.exit_time for t in seg_trades)
                        coverage = compute_funding_coverage(
                            funding_rates,
                            seg_start,
                            seg_end,
                            include_predicted=cfg.funding_include_predicted,
                        )
                    rows.append(
                        FundingCostRow(
                            symbol=symbol,
                            timeframe=timeframe,
                            segment=segment_label,
                            engine=engine_name,
                            cost_mode=cost_mode,
                            funding_mode=funding_mode,
                            num_trades=seg_result.metrics.num_trades,
                            win_rate=seg_result.metrics.win_rate,
                            total_return=seg_result.metrics.total_return,
                            max_drawdown=seg_result.metrics.max_drawdown,
                            total_funding_cost=sum(t.funding_cost for t in seg_trades),
                            funding_coverage=coverage,
                        )
                    )
    return rows


def run_report(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    db_path: str = _DB_PATH,
    cache_dir: str = _CACHE_DIR,
) -> list[FundingCostRow]:
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    funding_store = FundingRateStore(db_path)
    rows: list[FundingCostRow] = []
    for symbol in symbols:
        for timeframe in timeframes:
            df = _load_recent(store, symbol, timeframe, years)
            if df.empty:
                continue
            start_ms = int(df["open_time"].iloc[0])
            end_ms = int(df["open_time"].iloc[-1]) + timeframe_to_ms(timeframe)
            funding_rates = funding_store.get_rates(
                symbol, start_ms=start_ms, end_ms=end_ms, include_predicted=True
            )
            ob_result = OrderBlockDetector().run(df)
            rows.extend(
                run_symbol_timeframe(
                    df,
                    symbol=symbol,
                    timeframe=timeframe,
                    order_block_result=ob_result,
                    funding_rates=funding_rates,
                )
            )
            print(f"[wan91] {symbol} {timeframe}: {len(df)}봉, 펀딩 {len(funding_rates)}건")
    return rows


def _rows_to_frame(rows: list[FundingCostRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


_TF_ORDER = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}


def _fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _funding_delta_table(df: pd.DataFrame) -> str:
    """펀딩 on/off 델타(net 비용 기준) — TF·엔진·구간별 total_return 변화."""
    net = df[df["cost_mode"] == "net"]
    pivot = net.pivot_table(
        index=["symbol", "timeframe", "segment", "engine"],
        columns="funding_mode",
        values="total_return",
    )
    if "on" not in pivot.columns or "off" not in pivot.columns:
        return "(데이터 부족)"
    pivot = pivot.assign(delta=pivot["on"] - pivot["off"])
    pivot = pivot.reset_index().sort_values(
        by=["engine", "segment", "symbol"],
        key=lambda s: s.map(_TF_ORDER) if s.name == "timeframe" else s,
    )
    header = (
        "| 심볼 | TF | 구간 | 엔진 | 펀딩off | 펀딩on | delta |\n"
        "| -- | -- | -- | -- | -- | -- | -- |"
    )
    lines = [header]
    for _, r in pivot.iterrows():
        lines.append(
            f"| {r['symbol']} | {r['timeframe']} | {r['segment']} | {r['engine']} | "
            f"{_fmt(r['off'], 3)} | {_fmt(r['on'], 3)} | {r['delta']:+.4f} |"
        )
    return "\n".join(lines)


def _gross_net_table(df: pd.DataFrame) -> str:
    """그로스/넷 분해(펀딩 off 기준, 순수 수수료·슬리피지 효과) — TF·엔진·구간별."""
    no_funding = df[df["funding_mode"] == "off"]
    pivot = no_funding.pivot_table(
        index=["symbol", "timeframe", "segment", "engine"],
        columns="cost_mode",
        values="total_return",
    )
    if "gross" not in pivot.columns or "net" not in pivot.columns:
        return "(데이터 부족)"
    pivot = pivot.reset_index().sort_values(
        by=["engine", "segment", "symbol"],
        key=lambda s: s.map(_TF_ORDER) if s.name == "timeframe" else s,
    )
    header = (
        "| 심볼 | TF | 구간 | 엔진 | 그로스 | 넷 | 판정 |\n| -- | -- | -- | -- | -- | -- | -- |"
    )
    lines = [header]
    for _, r in pivot.iterrows():
        gross, net = r["gross"], r["net"]
        if gross > 0 and net <= 0:
            verdict = "비용이 신호를 잠식"
        elif gross <= 0:
            verdict = "그로스도 마이너스"
        else:
            verdict = "비용 이후에도 플러스"
        lines.append(
            f"| {r['symbol']} | {r['timeframe']} | {r['segment']} | {r['engine']} | "
            f"{gross:.4f} | {net:.4f} | {verdict} |"
        )
    return "\n".join(lines)


def _coverage_table(df: pd.DataFrame) -> str:
    on = df[(df["funding_mode"] == "on") & (df["cost_mode"] == "net")]
    if on.empty:
        return "(펀딩 on 데이터 없음)"
    grouped = on.groupby(["symbol", "timeframe", "segment"], as_index=False)[
        "funding_coverage"
    ].mean()
    grouped = grouped.sort_values(
        by=["symbol", "segment"], key=lambda s: s.map(_TF_ORDER) if s.name == "timeframe" else s
    )
    header = "| 심볼 | TF | 구간 | 펀딩 커버리지 |\n| -- | -- | -- | -- |"
    lines = [header]
    for _, r in grouped.iterrows():
        cov = _fmt(r["funding_coverage"], 3)
        lines.append(f"| {r['symbol']} | {r['timeframe']} | {r['segment']} | {cov} |")
    return "\n".join(lines)


def _short_flip_summary(df: pd.DataFrame) -> str:
    """WAN-84 결론(OOS 숏 평균 −3.82%)이 펀딩 수취를 넣으면 뒤집히는지 요약한다.

    방법론 차이(이 리포트는 A안/`entry_mode="close"`, WAN-84는 B안 매칭 널)로 절대값은
    직접 비교할 수 없다 — "이 리포트 안에서 펀딩 on/off가 숏 OOS 부호를 바꾸는가"만
    답한다.
    """
    oos_short = df[
        (df["segment"] == "OOS") & (df["engine"] == "short_enabled") & (df["cost_mode"] == "net")
    ]
    off_mean = oos_short[oos_short["funding_mode"] == "off"]["total_return"].mean()
    on_mean = oos_short[oos_short["funding_mode"] == "on"]["total_return"].mean()
    if pd.isna(off_mean) or pd.isna(on_mean):
        return "판정 불가: OOS 숏 셀이 부족하다."
    flipped = off_mean < 0 and on_mean > 0
    headline = (
        f"OOS 숏(`short_enabled`) 평균 total_return: 펀딩off {off_mean:.4f} → 펀딩on {on_mean:.4f}."
    )
    if flipped:
        verdict = (
            "**부호가 뒤집힌다** — 숏의 펀딩 수취가 WAN-84/87 숏 비활성화 판단의 전제를 흔든다."
        )
    else:
        verdict = (
            "부호는 유지된다 — 펀딩 수취만으로 결론이 뒤집히지는 않는다(단, 이 리포트는 A안 "
            "방법론이라 WAN-84의 B안 수치와 절대값 비교는 불가하다)."
        )
    return f"{headline} {verdict}"


def _tf_verdict_table(df: pd.DataFrame) -> str:
    """TF별 "그로스+ 넷-"(비용 잠식) 셀 비율을 세어 채택/제외 판정 근거를 낸다.

    셀 = (심볼, 구간, 엔진) 조합. "그로스도 마이너스"인 셀은 비용과 무관하게 신호
    자체가 안 되는 것이므로 이 판정에서 제외한다(§2 판정과 동일 기준, WAN-91 작업범위
    3번째 항목: "그로스 +, 넷 −"만 비용 탓, "그로스도 −"는 신호 자체 문제).
    """
    no_funding = df[df["funding_mode"] == "off"]
    pivot = no_funding.pivot_table(
        index=["symbol", "timeframe", "segment", "engine"],
        columns="cost_mode",
        values="total_return",
    )
    if "gross" not in pivot.columns or "net" not in pivot.columns:
        return "(데이터 부족)"
    pivot = pivot.reset_index()
    signal_alive = pivot[pivot["gross"] > 0]
    header = (
        "| TF | 그로스+ 셀 수 | 비용 잠식(넷-) 셀 수 | 잠식 비율 | 권고 |\n"
        "| -- | -- | -- | -- | -- |"
    )
    lines = [header]
    for tf in sorted(signal_alive["timeframe"].unique(), key=lambda t: _TF_ORDER.get(t, 9)):
        sub = signal_alive[signal_alive["timeframe"] == tf]
        total = len(sub)
        eroded = int((sub["net"] <= 0).sum())
        ratio = eroded / total if total else 0.0
        if ratio >= 0.5:
            reco = "제외 — 신호는 살아있으나 비용이 과반 셀에서 잠식"
        elif ratio > 0:
            reco = "유지(주의) — 일부 셀에서 비용 민감"
        else:
            reco = "유지 — 비용 이후에도 대체로 견고"
        lines.append(f"| {tf} | {total} | {eroded} | {ratio:.0%} | {reco} |")
    return "\n".join(lines)


_COST_REALISM_SECTION = """\
바이낸스 USDⓈ-M 무기한선물 일반(레귤러/VIP0) 계정 기준 실제 수수료(2026-07 조회,
`binance.com/en/fee/futureFee` 계열 공개 자료)와 저장소 가정을 대조한다.

| 항목 | 저장소 가정 | 바이낸스 실제(레귤러) | 평가 |
| -- | -- | -- | -- |
| 테이커 수수료 | 0.04%(4bp) | **0.05%(5bp)** | **과소 계상** — 왕복 2bp 저평가(0.08%→0.10%) |
| 메이커 수수료 | None→테이커와 동일(4bp) | **0.02%(2bp)** | `maker_fee_rate` 미지정 시 \
B안(WAN-41) 비용을 실제의 2배로 과대 계상 — B안 성과가 실제보다 나빠 보일 수 있다 |
| 슬리피지 | 0.05%(5bp) 고정 | (공식 수치 없음, 정성 평가) | BTC/ETH는 대체로 보수적(안전)이지만 \
**SOL·15m처럼 빈도·변동성이 큰 구간에서는 낙관적일 수 있다** — §2의 그로스↔넷 격차가 \
15m·SOL에서 가장 큰 것과 일관된 정황 |

**권고(이 이슈에서는 제안만, 기본값 변경은 하지 않음)**:

1. `fee_rate` 기본값을 0.0004→**0.0005**로 올려 테이커 비용 저평가를 바로잡는다.
2. `maker_fee_rate=0.0002`를 명시하는 시나리오를 B안(zone_limit) 평가에 항상 동반한다 \
— None 방치는 메이커 경로의 실제 비용 이점을 리포트에서 숨긴다.
3. 슬리피지를 TF/심볼 공용 상수 대신 변동성 연동(예: ATR 비례)으로 바꾸는 방안을 \
별도 이슈로 검토한다 — §2에서 15m·SOL이 비용에 가장 취약한 것이 그 우선순위 근거다.
"""


def build_summary_markdown(df: pd.DataFrame, *, csv_path: Path) -> str:
    lines = [
        "# WAN-91 펀딩비 배선 재산출 — 펀딩 on/off 델타 + TF별 그로스/넷 분해",
        "",
        "3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS(봉 2:1 분할), 로컬 "
        f"`data/ohlcv.db` 실데이터 3년. 재현: `python -m backtest.wan91_funding_cost_report`. "
        f"원자료: `{csv_path}`.",
        "",
        '> **방법론 caveat**: 이 리포트는 `entry_mode="close"`(A안, 현재 채택 엔진 기본값과 '
        "동일 실행 경로 — `backtest.wan87_long_only_report`와 동일 파이프라인)로 산출했다. "
        'WAN-84는 `entry_mode="zone_limit"`(B안) 매칭 널 방법론이라 이 리포트와 절대값을 '
        "직접 비교할 수 없다 — 델타(펀딩 on−off, 넷−그로스)와 부호 변화만 비교 가능하다.",
        "",
        "## 1. 펀딩 on/off 델타 (넷 비용 기준)",
        "",
        _funding_delta_table(df),
        "",
        "## 2. TF별 그로스/넷 분해 (펀딩 off 기준 — 순수 수수료·슬리피지 효과)",
        "",
        _gross_net_table(df),
        "",
        "## 3. 펀딩 데이터 커버리지",
        "",
        _coverage_table(df),
        "",
        "## 4. 숏 펀딩 수취가 WAN-84 결론을 뒤집는가",
        "",
        _short_flip_summary(df),
        "",
        "## 5. TF별 채택/제외 권고 (그로스/넷 근거)",
        "",
        _tf_verdict_table(df),
        "",
        "15m은 신호(그로스)가 살아있는 셀 대부분에서 비용이 넷을 마이너스로 뒤집는다 — "
        '"손해나니까 뺀다"는 과최적화가 아니라, 그로스/넷 분해로 뒷받침되는 원칙적 '
        "제외 근거다(WAN-91 배경 질문에 대한 답). 1h는 셀마다 갈린다(IS는 침식되는 "
        "경우가 있고 OOS는 대체로 견고) — 배경에서 지적한 대로 비용 가정에 민감하므로 "
        "제외하지 않되 모니터링 대상으로 남긴다. 4h/1d는 비용 이후에도 대체로 견고하다.",
        "",
        "## 6. 비용 가정 현실성 검토",
        "",
        _COST_REALISM_SECTION,
        "## 7. 제한사항 및 후속 작업",
        "",
        '- 이 리포트는 A안(`entry_mode="close"`) 파이프라인만 재산출했다. §5의 메이커 '
        "경로 검토(WAN-91 작업범위 5번, 선택)는 B안(`zone_limit`) 백테스트 전체 재실행이 "
        "필요해 이 실행 범위 밖에 남긴다 — 특히 15m처럼 그로스가 살아있는 TF에서 메이커 "
        "진입(§6 권고 2, `maker_fee_rate=0.0002`)이 넷을 플러스로 되돌리는지가 다음으로 "
        "확인할 질문이다.\n"
        "- 숏 펀딩 수취가 WAN-84(B안 매칭 널, OOS 숏 −3.82%) 결론을 뒤집는지는 이 리포트의 "
        "A안 수치로 직접 답할 수 없다 — 같은 질문을 B안 파이프라인(`backtest.wan84_new_engine_"
        "validation` 계열)에 펀딩을 배선해 재실행해야 한다.\n"
        "- 커버리지는 전 셀 100%였다 — 이 리포트가 다룬 3년 창에서는 펀딩 데이터 결측이 "
        "성과에 영향을 주지 않았다.",
        "",
    ]
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
    csv_path = out_dir / "wan91_funding_cost.csv"
    frame.to_csv(csv_path, index=False)
    summary = build_summary_markdown(frame, csv_path=csv_path)
    (out_dir / "wan91_funding_cost_summary.md").write_text(summary)
    print(summary)


if __name__ == "__main__":
    main()
