"""WAN-96: 체결 가정 보수화 민감도 — "닿으면 체결"은 낙관이다.

배경은 이슈 WAN-96 본문 참고. WAN-95가 기본 진입을 지정가로 바꾸자 15m이 3심볼 전부
마이너스(평균 −18.9%)에서 3심볼 전부 플러스(평균 +15.8%)로 뒤집혔다. 그런데 그 결과를
낸 시뮬레이터(`backtest/substep.py`)는 **가격이 지정가에 닿기만 하면 체결**로 본다.
실거래에서는 내 주문 앞에 줄이 있고(큐 우선순위), 가격이 스치듯 찍고 되돌아가면 체결
없이 지나간다. 15m 체결률이 약 28%라는 것은 **신호 4개 중 1개만 체결된다**는 뜻이고,
그 28%가 좋은 진입만 골라 담는 방향으로 편향돼 있다면 수익률은 통째로 착시다.

이 리포트는 그 낙관 편향의 크기를 재고, 15m 채택/제외 최종 권고를 낸다.

## 보수화 축 두 개

- **관통 요구**(`fill_penetration_bps`): 지정가를 N bp **지나쳐야** 체결로 인정한다.
  "가격이 스치기만 하면 체결되지 않는다"를 가격 경로로 모델링한다.
- **체결률 하향**(`fill_dropout_rate` + `fill_dropout_seed`): 낙관 모델이 체결이라 본
  건을 무작위로 X% 탈락시킨다. 큐 우선순위를 직접 모델링하는 대신 **결론이 체결률에
  얼마나 민감한지**를 본다. 시드를 여러 개 돌려 분포를 본다(단일 시드의 운이 아님을
  보이기 위해).

두 축 모두 **기본값을 바꾸지 않는다** — 이 모듈이 명시적으로 켤 때만 작동하므로
WAN-95 기준선(`baseline` 레벨)이 그대로 재현된다.

## 체결 편향 진단

체결률이 28%면 **나머지 72%는 어떤 셋업이었나**를 물어야 한다. 이 리포트는 eligible
셋업 전부에 **동일한 가상 진입가**(탭 봉 종가 = A안이 실제로 지불했을 가격)를 부여해
같은 손절·같은 1:1.5R 익절로 사후 손익(R 배수)을 내고, **체결된 셋업 vs 미체결 셋업**의
평균 R을 비교한다.

- 체결군의 가상 R이 **더 높으면**: 지정가 터치가 "될 놈"을 골라내고 있다는 뜻 —
  체결 모델이 조금만 틀려도 성과가 무너지는 구조(경고 신호).
- 체결군의 가상 R이 **더 낮으면**: 지정가는 오히려 가격이 더 빠진(불리한) 셋업에서
  체결된다는 뜻 — 낙관 편향이 수익의 원천이 아니라는 방증.

## 재현

```
python -m backtest.wan96_fill_conservatism_report
```

전 심볼×TF×레벨을 다 도는 데 시간이 걸린다. 좁히려면
`--symbols BTC/USDT:USDT --timeframes 15m`처럼 인자를 준다.

## ⚠️ 커밋된 산출물은 WAN-100 이전 엔진 기준 (WAN-97이 재산출)

WAN-100이 지정가 경로의 첫 탭 면제 누락을 고쳐 이 리포트의 입력 엔진이 바뀌었다(체결률
약 29% → 약 50%). 커밋된 `wan96_*` 표와 위 본문의 "약 28%"는 교정 **전** 수치이며, 그
사실을 `wan96_fill_conservatism_summary.md` 헤더에 경고로 적어 뒀다 — **이 모듈이 생성하는
마크다운에는 그 경고가 없다**(전체 재산출을 하면 수치가 최신이 되어 경고가 거짓이 되므로
일부러 넣지 않았다). 결론(15m 제외)은 같은 엔진 안의 상대 비교라 방향이 유지될 개연성이
높지만 검증된 것은 아니다 — 교정 엔진에서의 재판정은 WAN-97 소관이다.
"""

from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import LEGACY_OB_PARAMS, LEGACY_RSI_GATE_MODE, pin_band_bar
from backtest.models import BacktestConfig, PositionSide
from backtest.substep import SubStep, build_substeps
from backtest.sweep import default_backtest_config, timeframe_to_ms
from backtest.wan81_engine_replacement_report import (
    _CACHE_DIR,
    _DB_PATH,
    DEFAULT_SYMBOLS,
    DEFAULT_YEARS,
    _load_recent,
)
from backtest.zone_limit_backtest import SetupDiagnostic, run_zone_limit_backtest_verbose
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

#: 이 이슈의 판단 대상 TF. 15m이 본안이고, 1h는 WAN-91이 "비용 가정에 민감 — 모니터링
#: 대상"으로 남긴 TF라 같은 잣대를 댄다. 4h·1d는 체결률이 높아 이 축의 쟁점이 작다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: WAN-95 당시 채택 기본값 = 지정가 + 실시간 RSI + 롱 온리 + **오프셋 0bp**. 보수화는
#: 여기서만 덧붙인다.
#:
#: ⚠️ 오프셋을 **명시 고정**하는 이유(WAN-112): 채택 기본값이 2bp로 바뀌었지만 이 리포트의
#: 발표 수치는 0bp에서 나왔다. `ConfluenceParams()`를 그대로 두면 기본값을 따라 숫자가
#: 조용히 움직여 리포트 본문과 어긋난다 — WAN-95가 `SHORT_DISABLED_PARAMS`에 `entry_mode`를
#: 못 박은 것과 같은 이유다. 이 리포트는 **당시 엔진의 기록**이다.
#:
#: ⚠️ RSI 게이트도 같은 이유로 고정한다(WAN-123이 기본값을 `unconditional`로 옮겼다).
#: 이 리포트의 핵심 관찰인 **거래-수익 비대칭**("`pen_5bp`는 거래를 4.7%만 줄이는데 수익은
#: 사라진다")은 게이트가 켜진 거래 집합에서 잰 값이다. 게이트 제거 뒤의 관통 민감도는
#: `wan123_*`이 새로 낸다.
#: ⚠️ 밴드 표본(`band_bar`)도 같은 이유로 고정한다 — WAN-132가 기본값을 `intrabar_live`로
#: 옮겼고, 그 밴드에서는 진입가가 봉내에 움직여 이 표의 거래 집합 자체가 달라진다.
BASE_PARAMS = pin_band_bar(
    ConfluenceParams(
        zone_limit_offset_bps=0.0, rsi_gate_mode=LEGACY_RSI_GATE_MODE, max_zone_width_atr=None
    )
)


@dataclass(frozen=True)
class ConservatismLevel:
    """체결 가정 보수화 강도 한 단계."""

    name: str
    penetration_bps: float = 0.0
    dropout_rate: float = 0.0
    seeds: tuple[int, ...] = (0,)
    """탈락 추첨 시드들. 탈락이 있는 레벨만 여러 개를 돌려 분포를 본다."""
    note: str = ""

    def params(self, seed: int) -> ConfluenceParams:
        return BASE_PARAMS.model_copy(
            update={
                "fill_penetration_bps": self.penetration_bps,
                "fill_dropout_rate": self.dropout_rate,
                "fill_dropout_seed": seed,
            }
        )


#: 보수화 격자. `baseline`이 WAN-95 재현이고, 아래로 갈수록 체결 가정이 보수적이다.
#: 파라미터 최적화가 아니라 **강건성 확인**이므로 값은 성과와 무관하게 미리 고정했다.
CONSERVATISM_LEVELS: tuple[ConservatismLevel, ...] = (
    ConservatismLevel(name="baseline", note="WAN-95 그대로 — 닿으면 체결(낙관)"),
    ConservatismLevel(name="pen_1bp", penetration_bps=1.0, note="지정가 1bp 관통 요구"),
    ConservatismLevel(name="pen_5bp", penetration_bps=5.0, note="지정가 5bp 관통 요구"),
    ConservatismLevel(
        name="drop_25",
        dropout_rate=0.25,
        seeds=(0, 1, 2, 3, 4),
        note="체결 25% 탈락(큐 근사)",
    ),
    ConservatismLevel(
        name="drop_50",
        dropout_rate=0.5,
        seeds=(0, 1, 2, 3, 4),
        note="체결 50% 탈락(큐 근사)",
    ),
    ConservatismLevel(
        name="pen_5bp_drop_50",
        penetration_bps=5.0,
        dropout_rate=0.5,
        seeds=(0, 1, 2, 3, 4),
        note="관통 5bp + 50% 탈락(최악 가정 조합)",
    ),
)


class ConservatismRow(BaseModel):
    """한 (심볼, TF, 레벨, 시드) 실행 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    level: str
    seed: int
    penetration_bps: float
    dropout_rate: float
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    profit_factor: float | None
    sharpe: float | None
    eligible_setups: int
    num_filled: int
    fill_rate: float | None
    num_dropped: int
    num_penetrations: int


class BiasRow(BaseModel):
    """한 (심볼, TF)의 체결 편향 진단 — 체결군 vs 미체결군의 가상 손익 비교."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    filled_setups: int
    unfilled_setups: int
    filled_mean_r: float | None
    """체결된 셋업에 가상 진입가(탭 봉 종가)를 부여했을 때의 평균 R. 미해결 제외."""
    unfilled_mean_r: float | None
    """미체결 셋업의 같은 기준 평균 R."""
    mean_r_gap: float | None
    """체결군 − 미체결군. 양수면 '될 놈만 골라 담았다'는 신호."""
    filled_resolved: int
    """가상 손익이 손절/익절로 확정된 체결 셋업 수(분모)."""
    unfilled_resolved: int


def _virtual_r(
    setup: SetupDiagnostic,
    substeps: list[SubStep],
    substep_times: list[int],
    *,
    htf_ms: int,
    take_profit_r: float,
) -> float | None:
    """셋업에 **가상 진입가(탭 봉 종가)** 를 부여했을 때의 사후 R 배수.

    체결·미체결 셋업을 같은 잣대로 비교하기 위한 counterfactual이다 — 탭 봉 종가는
    A안(종가 진입)이 실제로 지불했을 가격이므로, "지정가를 안 기다렸으면 어땠나"를
    체결 여부와 무관하게 물을 수 있다. 손절 참조가·익절 배수는 실제 규칙 그대로 쓴다.

    탭 봉이 마감된 **다음** 서브스텝부터 평가한다(종가는 봉이 끝나야 알 수 있으므로).
    손절·익절이 같은 스텝에 동시 충족되면 손절 우선(엔진과 동일한 보수적 규칙).
    확정 없이 데이터가 끝나면 None(미해결)이다.
    """
    is_long = setup.side is PositionSide.LONG
    entry = setup.tap_close
    stop = setup.stop_price
    risk = entry - stop if is_long else stop - entry
    if risk <= 0:
        return None  # 종가가 이미 손절선 너머 — 가상 진입 자체가 성립하지 않는다.
    target = entry + risk * take_profit_r if is_long else entry - risk * take_profit_r

    start = bisect.bisect_left(substep_times, setup.tap_bar_time + htf_ms)
    for step in substeps[start:]:
        stop_hit = step.low <= stop if is_long else step.high >= stop
        tp_hit = step.high >= target if is_long else step.low <= target
        if stop_hit:
            return -1.0
        if tp_hit:
            return take_profit_r
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def build_bias_row(
    setups: list[SetupDiagnostic],
    substeps: list[SubStep],
    *,
    symbol: str,
    timeframe: str,
) -> BiasRow:
    """체결/미체결 셋업의 가상 손익을 비교해 편향 진단 행을 만든다."""
    htf_ms = timeframe_to_ms(timeframe)
    substep_times = [s.time for s in substeps]
    filled_r: list[float] = []
    unfilled_r: list[float] = []
    for setup in setups:
        value = _virtual_r(
            setup,
            substeps,
            substep_times,
            htf_ms=htf_ms,
            take_profit_r=BASE_PARAMS.take_profit_r,
        )
        if value is None:
            continue
        (filled_r if setup.filled else unfilled_r).append(value)

    filled_mean = _mean(filled_r)
    unfilled_mean = _mean(unfilled_r)
    gap = (
        filled_mean - unfilled_mean
        if filled_mean is not None and unfilled_mean is not None
        else None
    )
    return BiasRow(
        symbol=symbol,
        timeframe=timeframe,
        filled_setups=sum(1 for s in setups if s.filled),
        unfilled_setups=sum(1 for s in setups if not s.filled),
        filled_mean_r=filled_mean,
        unfilled_mean_r=unfilled_mean,
        mean_r_gap=gap,
        filled_resolved=len(filled_r),
        unfilled_resolved=len(unfilled_r),
    )


def run_cell(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    funding_rates: list[FundingRate],
    order_block_result: OrderBlockResult,
    levels: tuple[ConservatismLevel, ...] = CONSERVATISM_LEVELS,
    backtest_config: BacktestConfig | None = None,
) -> tuple[list[ConservatismRow], BiasRow | None]:
    """한 (심볼, TF)에서 보수화 레벨 전체를 돌고, 기준선으로 편향 진단을 낸다."""
    cfg = backtest_config or default_backtest_config(timeframe)
    rows: list[ConservatismRow] = []
    bias: BiasRow | None = None

    for level in levels:
        for seed in level.seeds:
            # 편향 진단은 기준선(낙관 체결)에서만 낸다 — 진단 대상이 "WAN-95의 28%"이므로.
            sink: list[SetupDiagnostic] | None = (
                [] if (level.name == "baseline" and bias is None) else None
            )
            result, stats = run_zone_limit_backtest_verbose(
                htf_df,
                df_1m,
                timeframe,
                confluence_params=level.params(seed),
                backtest_config=cfg,
                order_block_result=order_block_result,
                funding_rates=funding_rates,
                setup_sink=sink,
            )
            rows.append(
                ConservatismRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    level=level.name,
                    seed=seed,
                    penetration_bps=level.penetration_bps,
                    dropout_rate=level.dropout_rate,
                    num_trades=result.metrics.num_trades,
                    win_rate=result.metrics.win_rate,
                    total_return=result.metrics.total_return,
                    max_drawdown=result.metrics.max_drawdown,
                    profit_factor=result.metrics.profit_factor,
                    sharpe=result.metrics.sharpe,
                    eligible_setups=stats.eligible,
                    num_filled=stats.filled,
                    fill_rate=stats.fill_rate,
                    num_dropped=stats.dropped,
                    num_penetrations=stats.penetrations,
                )
            )
            if sink is not None:
                bias = build_bias_row(
                    sink,
                    build_substeps(df_1m, timeframe_to_ms(timeframe)),
                    symbol=symbol,
                    timeframe=timeframe,
                )
    return rows, bias


def collect_rows(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    db_path: str = _DB_PATH,
    cache_dir: str = _CACHE_DIR,
    levels: tuple[ConservatismLevel, ...] = CONSERVATISM_LEVELS,
) -> tuple[list[ConservatismRow], list[BiasRow]]:
    """전 심볼×TF×레벨을 돈다. 1분봉이 없는 심볼은 건너뛴다(지정가 평가 불가)."""
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    funding_store = FundingRateStore(db_path)
    rows: list[ConservatismRow] = []
    bias_rows: list[BiasRow] = []
    for symbol in symbols:
        df_1m = store.load(symbol, "1m")
        if df_1m.empty:
            print(f"[wan96] {symbol}: 1분봉 없음 — 지정가 평가 불가, 건너뜀")
            continue
        for timeframe in timeframes:
            htf_df = _load_recent(store, symbol, timeframe, years)
            if htf_df.empty:
                continue
            start_ms = int(htf_df["open_time"].iloc[0])
            end_ms = int(htf_df["open_time"].iloc[-1])
            funding_rates = funding_store.get_rates(
                symbol, start_ms=start_ms, end_ms=end_ms, include_predicted=True
            )
            window_1m = df_1m[df_1m["open_time"] >= start_ms]
            if window_1m.empty:
                continue
            ob_result = OrderBlockDetector(LEGACY_OB_PARAMS).run(htf_df)
            cell_rows, bias = run_cell(
                htf_df,
                window_1m,
                symbol=symbol,
                timeframe=timeframe,
                funding_rates=funding_rates,
                order_block_result=ob_result,
                levels=levels,
            )
            rows.extend(cell_rows)
            if bias is not None:
                bias_rows.append(bias)
            print(f"[wan96] {symbol} {timeframe}: {len(cell_rows)}개 실행 완료")
    return rows, bias_rows


_TF_ORDER = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
_LEVEL_ORDER = {level.name: i for i, level in enumerate(CONSERVATISM_LEVELS)}


def rows_to_frame(rows: list[ConservatismRow]) -> pd.DataFrame:
    frame = pd.DataFrame([r.model_dump() for r in rows])
    if frame.empty:
        return frame
    frame["_tf"] = frame["timeframe"].map(_TF_ORDER)
    frame["_lv"] = frame["level"].map(_LEVEL_ORDER)
    frame = frame.sort_values(["symbol", "_tf", "_lv", "seed"]).drop(columns=["_tf", "_lv"])
    return frame.reset_index(drop=True)


def build_sensitivity_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """TF×레벨 요약 — 시드를 평균해 심볼 간 집계를 낸다.

    `positive_symbols`는 **시드 평균 수익률이 양수인 심볼 수**다(시드 하나의 운으로
    심볼이 플러스로 넘어가지 않도록).
    """
    if frame.empty:
        return frame
    per_symbol = frame.groupby(["timeframe", "level", "symbol"], as_index=False).agg(
        total_return=("total_return", "mean"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        fill_rate=("fill_rate", "mean"),
        num_trades=("num_trades", "mean"),
    )
    grouped = per_symbol.groupby(["timeframe", "level"], as_index=False).agg(
        mean_return=("total_return", "mean"),
        worst_symbol_return=("total_return", "min"),
        positive_symbols=("total_return", lambda s: int((s > 0).sum())),
        num_symbols=("total_return", "size"),
        mean_win_rate=("win_rate", "mean"),
        mean_mdd=("max_drawdown", "mean"),
        mean_fill_rate=("fill_rate", "mean"),
        mean_trades=("num_trades", "mean"),
    )
    grouped["_tf"] = grouped["timeframe"].map(_TF_ORDER)
    grouped["_lv"] = grouped["level"].map(_LEVEL_ORDER)
    return grouped.sort_values(["_tf", "_lv"]).drop(columns=["_tf", "_lv"]).reset_index(drop=True)


def build_seed_spread_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """탈락 레벨의 시드별 분포(최소~최대) — 단일 시드의 운이 아님을 보이는 표."""
    if frame.empty:
        return frame
    multi = frame[frame["dropout_rate"] > 0]
    if multi.empty:
        return multi
    grouped = multi.groupby(["timeframe", "level", "symbol"], as_index=False).agg(
        min_return=("total_return", "min"),
        mean_return=("total_return", "mean"),
        max_return=("total_return", "max"),
        num_seeds=("seed", "nunique"),
    )
    grouped["_tf"] = grouped["timeframe"].map(_TF_ORDER)
    grouped["_lv"] = grouped["level"].map(_LEVEL_ORDER)
    return (
        grouped.sort_values(["symbol", "_tf", "_lv"])
        .drop(columns=["_tf", "_lv"])
        .reset_index(drop=True)
    )


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:.2f}%"


def _fmt_r(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:+.3f}R"


def _mechanism_lines(tf15: pd.DataFrame) -> list[str]:
    """수익이 어디에 몰려 있는지 — 관통 요구가 드러낸 구조를 표에서 계산해 적는다.

    `pen_5bp`는 거래 수를 거의 안 줄이는데 수익만 크게 깎는다. 이 비대칭이 "수익이
    스치듯 닿은 체결에 몰려 있다"의 증거이므로, 권고의 핵심 근거로 남긴다.
    """
    base_rows = tf15[tf15["level"] == "baseline"]
    pen_rows = tf15[tf15["level"] == "pen_5bp"]
    if base_rows.empty or pen_rows.empty:
        return []
    base, pen = base_rows.iloc[0], pen_rows.iloc[0]
    trade_drop = 1.0 - (pen["mean_trades"] / base["mean_trades"]) if base["mean_trades"] else 0.0
    return [
        "### 왜 무너지는가: 수익이 '스치듯 닿은' 체결에 몰려 있다",
        "",
        f"`pen_5bp`는 거래 수를 {trade_drop * 100:.1f}%만 줄이는데"
        f"({base['mean_trades']:.0f} → {pen['mean_trades']:.0f}), 평균 수익률은 "
        f"{_fmt_pct(base['mean_return'])} → {_fmt_pct(pen['mean_return'])}로 떨어진다. "
        "거래가 거의 그대로인데 수익만 사라진다는 건 **수익이 지정가를 스치듯 닿고 "
        "되돌아간 체결(관통 없는 체결)에 몰려 있다**는 뜻이다. 그런데 그 체결이야말로 "
        "실거래에서 **가장 안 될 가능성이 높은** 체결이다 — 가격이 내 지정가를 찍고 바로 "
        "돌아섰다면 내 앞 주문들이 먼저 소화되고 내 주문은 큐에 남는다. 즉 이 전략의 "
        "수익원은 실거래에서 재현성이 가장 낮은 체결에 의존한다.",
        "",
        "이는 아래 체결 편향 진단(체결군이 미체결군보다 **나쁘다**)과 모순이 아니다. "
        "그 진단은 **공통 가상 진입가(탭 봉 종가)** 로 셋업의 방향성만 재므로, 지정가가 "
        "더 유리한 가격에 들어가는 이점을 뺀 값이다. 두 결과를 합치면 그림이 하나로 "
        "모인다 — 지정가 진입의 수익은 **셋업이 좋아서가 아니라 더 좋은 가격에 들어가서** "
        "나오고, 그 좋은 가격은 체결 가정을 조금만 현실화하면 사라진다.",
        "",
    ]


def _verdict_lines(sensitivity: pd.DataFrame, bias: pd.DataFrame) -> list[str]:
    """15m 채택/제외 권고 — 수치는 표에서 읽고 결론과 근거를 명시한다.

    숫자를 문장에 박아 넣지 않고 표에서 계산해 쓴다(재실행하면 결론 문장도 같이 갱신된다).
    """
    lines = ["## 15m 채택/제외 최종 권고", ""]
    tf15 = sensitivity[sensitivity["timeframe"] == "15m"]
    if tf15.empty:
        return lines + ["15m 실행 결과가 없어 권고를 낼 수 없다.", ""]

    base = tf15[tf15["level"] == "baseline"].iloc[0]
    worst = tf15.sort_values("mean_return").iloc[0]
    holds = bool((tf15["mean_return"] > 0).all())
    all_symbols_positive = bool((tf15["positive_symbols"] == tf15["num_symbols"]).all())

    lines += [
        f"- 기준선(WAN-95, 낙관 체결): 평균 {_fmt_pct(base['mean_return'])}, "
        f"플러스 심볼 {int(base['positive_symbols'])}/{int(base['num_symbols'])}, "
        f"평균 체결률 {_fmt_pct(base['mean_fill_rate'])}.",
        f"- 가장 보수적인 레벨(`{worst['level']}`): 평균 {_fmt_pct(worst['mean_return'])}, "
        f"플러스 심볼 {int(worst['positive_symbols'])}/{int(worst['num_symbols'])}, "
        f"평균 체결률 {_fmt_pct(worst['mean_fill_rate'])}, "
        f"최악 심볼 {_fmt_pct(worst['worst_symbol_return'])}.",
        "",
    ]

    if holds and all_symbols_positive:
        verdict = (
            "**채택 권고.** 모든 보수화 레벨에서 15m 평균 수익률이 플러스를 유지하고, "
            "3심볼 전부 플러스라는 성질도 무너지지 않는다. 체결 가정을 최악(관통 5bp + "
            "50% 탈락)까지 밀어도 결론이 뒤집히지 않으므로, WAN-95의 반전은 낙관적 체결 "
            "가정의 산물이 아니다."
        )
    elif holds:
        verdict = (
            "**조건부 채택.** 보수화해도 평균은 플러스를 유지하지만 일부 심볼이 마이너스로 "
            "돌아선다 — 결과가 심볼별로 고르지 않다. 3심볼 전부 플러스라는 WAN-95의 "
            "강한 주장은 체결 가정에 기대고 있었다는 뜻이므로, 채택하되 소액·모니터링을 "
            "전제로 한다."
        )
    else:
        verdict = (
            "**제외 권고.** 체결 가정을 보수화하면 15m 평균 수익률이 마이너스로 무너진다. "
            "WAN-95의 반전은 '닿으면 체결'이라는 낙관 가정에 의존한 결과이므로, 실거래 "
            "체결률로는 재현되지 않을 공산이 크다. WAN-91의 제외 권고를 (비용이 아니라 "
            "체결 근거로) 유지한다."
        )
    lines += [verdict, ""]
    lines += _mechanism_lines(tf15)

    if not bias.empty:
        bias15 = bias[bias["timeframe"] == "15m"]
        if not bias15.empty:
            gaps = bias15["mean_r_gap"].dropna()
            if not gaps.empty:
                mean_gap = float(gaps.mean())
                lines += [
                    "### 체결 편향: 28%는 '좋은 것만' 고른 게 아니다"
                    if mean_gap <= 0
                    else "### ⚠️ 체결 편향: 체결군이 유의하게 좋다",
                    "",
                    f"모든 eligible 셋업에 같은 가상 진입가(탭 봉 종가)를 부여해 사후 R을 "
                    f"내면, 15m 체결군과 미체결군의 평균 R 차이는 **{_fmt_r(mean_gap)}**"
                    f"(체결군 − 미체결군)이다. "
                    + (
                        "체결군이 더 나쁘다 — 지정가는 가격이 더 깊이 빠진(즉 그 시점엔 "
                        "불리해 보이는) 셋업에서 체결되므로, 체결 여부가 '될 놈'을 골라내는 "
                        "선택자로 작동하지 않는다는 뜻이다. 낙관 편향이 수익의 원천이라는 "
                        "가설과는 반대 방향의 증거다."
                        if mean_gap <= 0
                        else "체결군이 더 좋다 — 체결 여부가 성과와 같은 방향으로 상관돼 "
                        "있다는 뜻이므로, 체결 모델의 오차가 성과를 직접 부풀릴 수 있다. "
                        "위 민감도표를 이 관점에서 다시 읽어야 한다."
                    ),
                    "",
                ]
    return lines


def build_markdown(
    frame: pd.DataFrame, sensitivity: pd.DataFrame, spread: pd.DataFrame, bias: pd.DataFrame
) -> str:
    lines = [
        "# WAN-96: 체결 가정 보수화 민감도",
        "",
        "**재현**: `python -m backtest.wan96_fill_conservatism_report`",
        "",
        "## 무엇을 묻는가",
        "",
        "WAN-95가 기본 진입을 지정가로 바꾸자 15m이 3심볼 전부 마이너스에서 3심볼 전부 "
        "플러스로 뒤집혔다. 그 결과를 낸 시뮬레이터는 **가격이 지정가에 닿기만 하면 체결**"
        "로 본다 — 실거래에는 큐 우선순위가 있어 닿아도 체결되지 않는다. 15m 체결률이 "
        "약 28%라는 건 신호 4개 중 1개만 체결된다는 뜻이고, 그 28%가 좋은 진입만 골라 "
        "담는 방향으로 편향돼 있으면 수익률은 통째로 착시다. **이 리포트는 체결 가정을 "
        "보수화해도 15m 결론이 버티는지 본다.**",
        "",
        "## 보수화 레벨",
        "",
        "| 레벨 | 관통 요구 | 탈락률 | 시드 | 설명 |",
        "| -- | --: | --: | --: | -- |",
    ]
    for level in CONSERVATISM_LEVELS:
        seeds = ", ".join(str(s) for s in level.seeds)
        lines.append(
            f"| `{level.name}` | {level.penetration_bps:.0f}bp | "
            f"{level.dropout_rate * 100:.0f}% | {seeds} | {level.note} |"
        )
    lines += [
        "",
        "이 이슈는 기본값을 바꾸지 않았다 — 당시 `baseline`이 `ConfluenceParams()` "
        "그대로였고 WAN-95 결과를 재현했다. 보수화 값은 성과를 보기 전에 고정했다"
        "(파라미터 최적화 금지, 이슈 비고). 🔁 **그 뒤 "
        "[WAN-112](../../docs/decisions/wan112.md)가 채택 기본 오프셋을 2bp로 올렸고, 이 "
        "리포트는 발표 수치를 지키려고 `BASE_PARAMS`에 오프셋 0bp를 명시 고정했다** — "
        "즉 `baseline`은 이제 「채택 기본값」이 아니라 **「WAN-112 이전 엔진」**이다.",
        "",
        "## TF × 레벨 민감도표",
        "",
        "시드가 여러 개인 레벨은 심볼별로 시드를 평균한 뒤 집계했다.",
        "",
        "| TF | 레벨 | 평균 return | 최악 심볼 | 플러스 심볼 | 평균 승률 | 평균 MDD | "
        "평균 체결률 | 평균 거래수 |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for _, row in sensitivity.iterrows():
        lines.append(
            f"| {row['timeframe']} | `{row['level']}` | {_fmt_pct(row['mean_return'])} | "
            f"{_fmt_pct(row['worst_symbol_return'])} | "
            f"{int(row['positive_symbols'])}/{int(row['num_symbols'])} | "
            f"{_fmt_pct(row['mean_win_rate'])} | {_fmt_pct(row['mean_mdd'])} | "
            f"{_fmt_pct(row['mean_fill_rate'])} | {row['mean_trades']:.1f} |"
        )

    lines += ["", "## 체결 편향 진단 (기준선 기준)", ""]
    if bias.empty:
        lines += ["진단 대상 셋업이 없다.", ""]
    else:
        lines += [
            "eligible 셋업 전부에 **같은 가상 진입가(탭 봉 종가)** 를 부여해 같은 손절·같은 "
            "1:1.5R 익절로 사후 R을 낸 뒤, 체결군과 미체결군을 비교한다. 가상 손익이 "
            "확정되지 않은(데이터 종료까지 손절·익절 미도달) 셋업은 제외했다.",
            "",
            "| 심볼 | TF | 체결 | 미체결 | 체결군 평균 R | 미체결군 평균 R | 차이 |",
            "| -- | -- | --: | --: | --: | --: | --: |",
        ]
        for _, row in bias.iterrows():
            lines.append(
                f"| {row['symbol']} | {row['timeframe']} | "
                f"{int(row['filled_setups'])} ({int(row['filled_resolved'])} 확정) | "
                f"{int(row['unfilled_setups'])} ({int(row['unfilled_resolved'])} 확정) | "
                f"{_fmt_r(row['filled_mean_r'])} | {_fmt_r(row['unfilled_mean_r'])} | "
                f"{_fmt_r(row['mean_r_gap'])} |"
            )
        lines += [""]

    if not spread.empty:
        lines += [
            "## 탈락 시드별 분포",
            "",
            "탈락 레벨의 결과가 시드 하나의 운이 아님을 보인다.",
            "",
            "| 심볼 | TF | 레벨 | 최소 | 평균 | 최대 | 시드수 |",
            "| -- | -- | -- | --: | --: | --: | --: |",
        ]
        for _, row in spread.iterrows():
            lines.append(
                f"| {row['symbol']} | {row['timeframe']} | `{row['level']}` | "
                f"{_fmt_pct(row['min_return'])} | {_fmt_pct(row['mean_return'])} | "
                f"{_fmt_pct(row['max_return'])} | {int(row['num_seeds'])} |"
            )
        lines += [""]

    lines += _verdict_lines(sensitivity, bias)
    lines += [
        "## 한계",
        "",
        "1. **큐 근사는 큐가 아니다.** 탈락률은 호가창 깊이·내 주문 크기·앞선 주문량을 "
        "모델링하지 않고 체결률을 일률적으로 깎는다. 이슈의 (b) 항목(거래량 대비 체결 "
        "확률·부분 체결)은 1분봉 거래량만으로는 큐 위치를 복원할 수 없어 구현하지 "
        "않았다 — 틱·호가 데이터가 있어야 제대로 된다. 대신 (a)(c)를 조합한 최악 "
        "레벨로 그 방향의 영향을 감싼다.",
        "2. **관통 요구도 대리 변수다.** N bp 관통이 실제 체결과 일대일 대응하지 않는다. "
        "체결가는 관통 여부와 무관하게 지정가 그대로 두었다(관통했다고 더 유리한 가격을 "
        "받지는 않으므로).",
        "3. **룩어헤드 잔존**: 볼린저 진입가가 탭 봉 SMA20(그 봉 종가 포함)으로 계산되는데 "
        "체결은 봉 내부에서 일어날 수 있다(CLAUDE.md 참고). WAN-96은 이 성질을 바꾸지 "
        "않는다.",
        "4. **엣지 판정은 별건**: 이 리포트는 '체결 가정이 결론을 뒤집는가'만 답한다. "
        "무작위 진입 대비 엣지가 있는지는 WAN-88(매칭 널 재검정)이 새 엔진으로 다시 낸다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-96 체결 가정 보수화 민감도")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--db-path", default=_DB_PATH)
    parser.add_argument("--cache-dir", default=_CACHE_DIR)
    parser.add_argument("--out-dir", default="backtest/reports")
    args = parser.parse_args()

    rows, bias_rows = collect_rows(
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
    )
    frame = rows_to_frame(rows)
    sensitivity = build_sensitivity_frame(frame)
    spread = build_seed_spread_frame(frame)
    bias = pd.DataFrame([r.model_dump() for r in bias_rows])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_path = out_dir / "wan96_fill_conservatism.csv"
    sens_path = out_dir / "wan96_fill_conservatism_sensitivity.csv"
    bias_path = out_dir / "wan96_fill_bias.csv"
    md_path = out_dir / "wan96_fill_conservatism_summary.md"
    frame.to_csv(runs_path, index=False)
    sensitivity.to_csv(sens_path, index=False)
    bias.to_csv(bias_path, index=False)
    md_path.write_text(build_markdown(frame, sensitivity, spread, bias), encoding="utf-8")
    for path in (runs_path, sens_path, bias_path, md_path):
        print(f"[wan96] 저장: {path}")


if __name__ == "__main__":
    main()
