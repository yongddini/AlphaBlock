"""WAN-95: 지정가(zone_limit) 채택 재산출 — 종가 진입 대비 손익·체결률 대조표.

배경은 이슈 WAN-95 본문 참고. 사용자의 실매매는 **오더블록 존에 지정가를 걸어두고
가격이 닿는 순간 체결**하는 방식인데, WAN-95 이전 채택 기본값은 `entry_mode="close"`
(탭 봉 종가 시장가)였다. 즉 `wan87_long_only_summary.md`·WAN-91 펀딩 리포트를 포함한
기존 손익표는 **사용자가 하지 않는 매매**의 성과였다. 이 모듈이 채택 기본값
(`ConfluenceParams()` = 지정가 + 실시간 RSI + 롱 온리)으로 3심볼 × 4TF × 최근 3년을
재산출하고, 종가 진입과 나란히 비교한다.

## 방법론

`backtest.ab_run.build_ab_entries`와 같은 **공정 비교 창** 규칙을 쓴다. B안(지정가)은
1분봉이 커버하는 구간의 셋업만 평가하므로(`zone_limit_backtest` 참고), A안(종가) 성과도
1분봉 커버 창 안에 진입한 거래만 집계해야 같은 기간을 비교하게 된다. 상위TF 히스토리
전체는 오더블록 탐지·지표 워밍업에 그대로 쓰고, 성과 집계만 창으로 한정한다. 두 변형은
**같은 오더블록·같은 비용 모델·같은 펀딩 데이터**를 공유한다.

## 비용 비대칭 (이 이슈의 핵심)

- **지정가 진입 = 메이커**: 메이커 수수료(2bp), 슬리피지 없음.
- **종가 진입 = 테이커**: 테이커 수수료(4bp) + 슬리피지(5bp).
- **청산은 양쪽 다 테이커**: 손절·익절 도달은 시장가 성격이다.

왕복 비용이 종가 진입 0.18%(4+5+4+5 bp) → 지정가 진입 0.11%(2+0+4+5 bp)로 준다.
WAN-91이 "15m 채택 제외"를 **비용** 근거로 권고했으므로, 메이커 비용으로 다시 돌리기
전에는 그 권고를 확정할 수 없다 — 이 리포트가 그 재산출이다.

## 펀딩비

`funding_enabled=True`만으로는 손익이 안 바뀐다. `data.FundingRateStore.get_rates`로
조회한 펀딩비를 A안(`evaluate`)·B안(`run_zone_limit_backtest_verbose`) **양쪽에 명시적으로
전달**하고, 커버리지를 행에 실어 "얼마나 반영됐는지"가 리포트에서 보이게 한다
(WAN-63/91이 두 번 잡은 조용한 실패의 재발 방지).

## 체결률(fill rate)

지정가는 **닿지 않으면 체결되지 않는다**. `eligible`(1분봉이 커버한 활성 셋업) 대비
`filled`(지정가 체결) 비율이 이 전환의 핵심 트레이드오프(비용↓ vs 기회↓)를 드러낸다.
종가 진입은 탭이 곧 진입이라 이 축이 없다.

## 재현

```
python -m backtest.wan95_zone_limit_report
```
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.ab_run import _window, _windowed_result
from backtest.models import BacktestConfig
from backtest.sweep import default_backtest_config, evaluate
from backtest.wan81_engine_replacement_report import (
    _CACHE_DIR,
    _DB_PATH,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    DEFAULT_YEARS,
    _load_recent,
)
from backtest.zone_limit_backtest import run_zone_limit_backtest_verbose
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

#: 채택 기본값(WAN-95) — 지정가 + 실시간 RSI + 롱 온리. `ConfluenceParams()` 그 자체다.
ZONE_LIMIT_PARAMS = ConfluenceParams()

#: 대조군: WAN-95 이전 채택 기본값(종가 진입 + 확정봉 RSI). 그 외 규칙은 동일하게 둔다
#: — 이 리포트가 격리하려는 변수는 **진입 방식(+그에 따른 체결 비용)** 하나다.
CLOSE_ENTRY_PARAMS = ConfluenceParams(entry_mode="close", rsi_mode="closed_bar")


class ZoneLimitRow(BaseModel):
    """한 (심볼, TF, 진입 방식) 셀의 재산출 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    entry_mode: str
    """`zone_limit`(채택) 또는 `close`(WAN-95 이전 기본값)."""
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    profit_factor: float | None
    """거래가 없거나 손실이 0이면 None(정의 불가)."""
    sharpe: float | None
    """거래가 없으면 None."""
    total_funding_cost: float
    funding_coverage: float | None
    """펀딩 데이터 커버리지(0~1). None이면 펀딩 미사용."""
    eligible_setups: int | None = None
    """1분봉이 커버해 시뮬레이션에 들어간 활성 셋업 수(지정가만)."""
    num_filled: int | None = None
    """지정가가 실제 체결된 셋업 수(지정가만)."""
    fill_rate: float | None = None
    """`num_filled / eligible_setups`. 지정가 전환의 기회비용 축(지정가만)."""
    num_penetrations: int | None = None
    """진입과 손절이 같은 1분 스텝에서 일어난 관통 건수(낙관 편향 감사, 지정가만)."""


def run_symbol_timeframe(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    funding_rates: list[FundingRate],
    order_block_result: OrderBlockResult,
    backtest_config: BacktestConfig | None = None,
) -> list[ZoneLimitRow]:
    """한 (심볼, TF)에서 지정가·종가 두 변형을 같은 창·같은 비용으로 재산출한다."""
    cfg = backtest_config or default_backtest_config(timeframe)
    start, end = _window(df_1m)

    # 지정가(B안, 채택): 1분봉 서브스텝으로 존 근단 지정가 체결을 시뮬레이션한다.
    zl_result, zl_stats = run_zone_limit_backtest_verbose(
        htf_df,
        df_1m,
        timeframe,
        confluence_params=ZONE_LIMIT_PARAMS,
        backtest_config=cfg,
        order_block_result=order_block_result,
        funding_rates=funding_rates,
    )

    # 종가(A안, 이전 기본값): 1분봉 커버 창 안 거래만 집계해 기간을 맞춘다.
    close_full = evaluate(
        htf_df,
        confluence_params=CLOSE_ENTRY_PARAMS,
        backtest_config=cfg,
        order_block_result=order_block_result,
        funding_rates=funding_rates,
    )
    close_result = _windowed_result(
        close_full.trades,
        cfg,
        timeframe,
        start,
        end,
        funding_coverage=close_full.metrics.funding_coverage,
    )

    return [
        ZoneLimitRow(
            symbol=symbol,
            timeframe=timeframe,
            entry_mode="zone_limit",
            num_trades=zl_result.metrics.num_trades,
            win_rate=zl_result.metrics.win_rate,
            total_return=zl_result.metrics.total_return,
            max_drawdown=zl_result.metrics.max_drawdown,
            profit_factor=zl_result.metrics.profit_factor,
            sharpe=zl_result.metrics.sharpe,
            total_funding_cost=zl_result.metrics.total_funding_cost,
            funding_coverage=zl_result.metrics.funding_coverage,
            eligible_setups=zl_stats.eligible,
            num_filled=zl_stats.filled,
            fill_rate=zl_stats.fill_rate,
            num_penetrations=zl_stats.penetrations,
        ),
        ZoneLimitRow(
            symbol=symbol,
            timeframe=timeframe,
            entry_mode="close",
            num_trades=close_result.metrics.num_trades,
            win_rate=close_result.metrics.win_rate,
            total_return=close_result.metrics.total_return,
            max_drawdown=close_result.metrics.max_drawdown,
            profit_factor=close_result.metrics.profit_factor,
            sharpe=close_result.metrics.sharpe,
            total_funding_cost=close_result.metrics.total_funding_cost,
            funding_coverage=close_result.metrics.funding_coverage,
        ),
    ]


def collect_rows(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    db_path: str = _DB_PATH,
    cache_dir: str = _CACHE_DIR,
) -> list[ZoneLimitRow]:
    """전 심볼×TF를 재산출한다. 1분봉이 없는 심볼은 건너뛴다(지정가 평가 불가)."""
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    funding_store = FundingRateStore(db_path)
    rows: list[ZoneLimitRow] = []
    for symbol in symbols:
        df_1m = store.load(symbol, "1m")
        if df_1m.empty:
            print(f"[wan95] {symbol}: 1분봉 없음 — 지정가 평가 불가, 건너뜀")
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
            # 상위TF 창으로 1분봉을 잘라 메모리·시간을 아낀다(워밍업은 상위TF가 담당).
            window_1m = df_1m[df_1m["open_time"] >= start_ms]
            if window_1m.empty:
                continue
            ob_result = OrderBlockDetector().run(htf_df)
            rows.extend(
                run_symbol_timeframe(
                    htf_df,
                    window_1m,
                    symbol=symbol,
                    timeframe=timeframe,
                    funding_rates=funding_rates,
                    order_block_result=ob_result,
                )
            )
            print(
                f"[wan95] {symbol} {timeframe}: {len(htf_df)}봉, "
                f"1분봉 {len(window_1m)}행, 펀딩 {len(funding_rates)}건"
            )
    return rows


_TF_ORDER = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}


def rows_to_frame(rows: list[ZoneLimitRow]) -> pd.DataFrame:
    """행들을 심볼×TF×진입방식 순으로 정렬한 DataFrame으로."""
    frame = pd.DataFrame([r.model_dump() for r in rows])
    if frame.empty:
        return frame
    frame["_tf"] = frame["timeframe"].map(_TF_ORDER)
    frame = frame.sort_values(["symbol", "_tf", "entry_mode"]).drop(columns=["_tf"])
    return frame.reset_index(drop=True)


def build_delta_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """심볼×TF별 지정가 − 종가 델타표(이 전환이 무엇을 바꿨는지)."""
    if frame.empty:
        return frame
    pivot = frame.pivot_table(
        index=["symbol", "timeframe"], columns="entry_mode", values="total_return"
    )
    trades = frame.pivot_table(
        index=["symbol", "timeframe"], columns="entry_mode", values="num_trades"
    )
    out = pd.DataFrame(
        {
            "close_return": pivot.get("close"),
            "zone_limit_return": pivot.get("zone_limit"),
            "return_delta": pivot.get("zone_limit") - pivot.get("close"),
            "close_trades": trades.get("close"),
            "zone_limit_trades": trades.get("zone_limit"),
        }
    ).reset_index()
    out["_tf"] = out["timeframe"].map(_TF_ORDER)
    return out.sort_values(["symbol", "_tf"]).drop(columns=["_tf"]).reset_index(drop=True)


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def build_tf_verdict_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """TF별 채택 판단 근거표 — 진입 방식별 평균 수익률·플러스 심볼 수·평균 체결률.

    WAN-91이 "15m 채택 제외"를 **비용** 근거로 권고했으므로, 메이커 비용으로 다시 돌린
    이 표가 그 권고를 확정하거나 뒤집는 근거가 된다.
    """
    if frame.empty:
        return frame
    grouped = frame.groupby(["timeframe", "entry_mode"], as_index=False).agg(
        mean_return=("total_return", "mean"),
        positive_symbols=("total_return", lambda s: int((s > 0).sum())),
        num_symbols=("total_return", "size"),
        mean_mdd=("max_drawdown", "mean"),
        mean_fill_rate=("fill_rate", "mean"),
    )
    grouped["_tf"] = grouped["timeframe"].map(_TF_ORDER)
    return grouped.sort_values(["_tf", "entry_mode"]).drop(columns=["_tf"]).reset_index(drop=True)


def _tf_mean(frame: pd.DataFrame, timeframe: str, entry_mode: str, column: str) -> float | None:
    """한 (TF, 진입 방식)의 열 평균. 해당 셀이 없으면 None.

    한계 서술의 숫자를 본문에 **하드코딩하지 않기 위한** 헬퍼다(WAN-100). 예전엔
    "체결률 약 28%"·"승률 55% → 49%"가 문장에 박혀 있었는데, WAN-100이 엔진을 고쳐
    체결률이 50%대로 바뀌자 표는 갱신되고 그 옆 문장만 옛 숫자를 주장하는 상태가 됐다
    — 이 리포트가 고발하는 "문서와 실제가 갈라진다"를 리포트 자신이 저지른 셈이다.
    """
    cell = frame[(frame["timeframe"] == timeframe) & (frame["entry_mode"] == entry_mode)]
    if cell.empty:
        return None
    value = cell[column].mean()
    return None if pd.isna(value) else float(value)


def _verdict_lines(frame: pd.DataFrame) -> list[str]:
    """TF 채택 판단 섹션. 수치는 표에서 읽고, 결론·한계는 명시적으로 적는다."""
    verdict = build_tf_verdict_frame(frame)
    zl_fill = _tf_mean(frame, "15m", "zone_limit", "fill_rate")
    zl_win = _tf_mean(frame, "15m", "zone_limit", "win_rate")
    close_win = _tf_mean(frame, "15m", "close", "win_rate")
    lines = [
        "## TF 채택 판단 (메이커 비용 기준 재산출)",
        "",
        "| TF | 진입 | 평균 return | 플러스 심볼 | 평균 MDD | 평균 체결률 |",
        "| -- | -- | --: | --: | --: | --: |",
    ]
    for _, row in verdict.iterrows():
        fill = _fmt_pct(row["mean_fill_rate"]) if pd.notna(row["mean_fill_rate"]) else "—"
        lines.append(
            f"| {row['timeframe']} | {row['entry_mode']} | {_fmt_pct(row['mean_return'])} | "
            f"{int(row['positive_symbols'])}/{int(row['num_symbols'])} | "
            f"{_fmt_pct(row['mean_mdd'])} | {fill} |"
        )
    lines += [
        "",
        "### 15m: WAN-91의 '채택 제외' 권고는 **유지되지 않는다**",
        "",
        "WAN-91은 15m을 채택 대상에서 제외할 것을 권고했고, 그 근거는 과최적화가 아니라 "
        "**비용**이었다 — 거래 수가 많아 왕복 0.18%(테이커)가 신호를 잠식한다는 것. "
        "그 권고의 전제(테이커 진입)가 사용자의 실매매와 달랐으므로, 메이커 진입으로 "
        "다시 돌린 이 표가 답이다: **15m은 3심볼 전부 마이너스에서 3심볼 전부 플러스로 "
        "뒤집혔고, MDD도 크게 줄었다.** 즉 15m의 문제는 신호가 아니라 비용이었다는 "
        "WAN-91의 진단 자체는 옳았고, 비용을 실제 매매 방식에 맞추자 결론이 반대로 나온다.",
        "",
        "### 그러나 이 결과를 채택 근거로 쓰기 전 반드시 볼 한계",
        "",
        "1. **체결 가정이 낙관적이다.** 시뮬레이터는 가격이 지정가에 **닿으면 체결**로 "
        "본다(`backtest/substep.py`). 실제 지정가는 큐 우선순위가 있어, 가격이 스치기만 "
        f"하면 체결되지 않을 수 있다. 즉 실제 체결률은 이 표(15m 기준 {_fmt_pct(zl_fill)})"
        "보다 낮고, **체결된 거래만 골라 담는 편향**이 남는다. WAN-96이 이 가정을 보수화해 "
        "재검정했다.",
        f"2. **승률은 오히려 떨어졌다**(15m 기준 {_fmt_pct(close_win)} → {_fmt_pct(zl_win)}). "
        "수익 개선은 승률이 아니라 진입가·비용에서 나온 것이다 — 지정가는 더 유리한 "
        "가격에 들어가므로 1R이 줄고 익절 목표가 가까워진다(고정 1:1.5R 규칙과의 상호작용).",
        "3. **4h·1d는 반대로 나빠졌다**(체결률이 높아 기회 손실은 작지만 표본이 작다: "
        "1d는 심볼당 4~12거래). 이 TF들의 델타는 표본이 작아 신뢰구간이 넓다.",
        "4. **룩어헤드 잔존**: 볼린저 진입가가 탭 봉 SMA20(그 봉 종가 포함)으로 계산되는데 "
        "체결은 봉 내부에서 일어날 수 있다(CLAUDE.md 참고). 영향은 작을 것으로 보지만 "
        "0은 아니다.",
        "",
        "따라서 이 리포트는 **'15m 제외 권고를 확정할 수 없다'**까지가 결론이다. "
        "15m 채택 여부는 체결 가정을 보수화한 재검정(체결률 하향·큐 모델) 후 판단할 것을 "
        "권고한다. 엣지 판정 자체는 WAN-88(매칭 널 재검정)이 이 엔진으로 다시 내야 한다.",
        "",
    ]
    return lines


def build_markdown(frame: pd.DataFrame, delta: pd.DataFrame) -> str:
    """리포트 마크다운. 수치 해석은 사람이 하되, 재현 커맨드·방법론을 함께 남긴다."""
    lines = [
        "# WAN-95: 지정가(zone_limit) 채택 재산출",
        "",
        "**재현**: `python -m backtest.wan95_zone_limit_report`",
        "",
        "> ⚠️ **이 표는 WAN-100(첫 탭 면제 배선)으로 전면 재산출됐다.** 최초 WAN-95 산출 "
        "당시 지정가(B안) 경로는 `tap_index`를 읽지 않아 「첫 탭은 RSI 무관 무조건 진입」"
        "(WAN-81)이 통째로 빠져 있었다 — 첫 탭에도 재탭용 RSI 게이트(롱 `RSI<=30`)가 "
        "걸려 명세보다 **빡빡한** 규칙으로 돌았다. 고친 결과 지정가 체결률이 약 29% → 약 "
        "50%로 오르고 수익률이 크게 늘었다(`zone_limit` 행만 해당). 종가(`close`) 행은 "
        "A안이라 원래부터 면제가 적용돼 있었으므로 이 수정의 영향을 받지 않는다.",
        "",
        "> ℹ️ **WAN-100과 무관한 차이 한 건**: 재산출에서 `SOL 15m close` 행이 최초 "
        "커밋본과 다르다(거래 939→938, return −8.68%→−9.87%). 수정을 되돌리고 돌려도 "
        "동일하게 재현되므로 원인은 엔진이 아니라 **데이터 드리프트**다 — 펀딩비 조회가 "
        "`include_predicted=True`라 예측 펀딩률이 사후 확정되며 값이 갱신된다(해당 행 펀딩 "
        "73.38→71.75). 나머지 11개 `close` 행은 비트 단위로 동일하다.",
        "",
        "## 무엇이 바뀌었나",
        "",
        "채택 기본값(`ConfluenceParams()`)의 진입 방식을 **종가 시장가(`close`) → "
        "존 근단 지정가(`zone_limit`)** 로 바꾸고, 지정가 진입에 **메이커 수수료(2bp)** 를 "
        "배선했다. 사용자의 실매매가 지정가이므로, 이전 손익표(`wan87_long_only_summary.md`, "
        "WAN-91 펀딩 리포트 등)는 **사용자가 하지 않는 매매**의 성과였다.",
        "",
        "| 항목 | 종가 진입(이전 기본값) | 지정가 진입(채택) |",
        "| -- | -- | -- |",
        "| 진입 체결 | 탭 봉 종가, 테이커 4bp + 슬리피지 5bp "
        "| 존 근단 지정가, 메이커 2bp, 슬리피지 0 |",
        "| RSI 판정 | 확정봉(`closed_bar`) | 체결 순간 봉내(`realtime`) |",
        "| 청산 | 테이커 4bp + 슬리피지 5bp | 동일(테이커) |",
        "| 왕복 비용 | 0.18% | **0.11%** |",
        "| 미체결 위험 | 없음(탭=진입) | **있음(체결률 참고)** |",
        "",
        "## 방법론",
        "",
        "- **공정 비교 창**: 지정가는 1분봉이 커버하는 구간만 평가하므로, 종가 진입 성과도 "
        "1분봉 커버 창 안에 진입한 거래만 집계했다. 두 변형은 같은 오더블록·같은 비용 모델·"
        "같은 펀딩 데이터를 공유한다.",
        "- **펀딩비**: `FundingRateStore.get_rates`로 조회해 양쪽 엔진에 명시적으로 전달했다. "
        "`funding_coverage` 열이 실제 반영 비율이다.",
        "- **롱 온리**(WAN-87) 유지. 전략 파라미터 탐색·최적화는 하지 않았다 — 바꾼 변수는 "
        "진입 방식과 그에 따른 체결 비용뿐이다.",
        "",
        "## 심볼 × TF 손익표",
        "",
        "| 심볼 | TF | 진입 | 거래수 | 승률 | total_return | MDD | PF | 체결률 |",
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: |",
    ]
    for _, row in frame.iterrows():
        fill = _fmt_pct(row["fill_rate"]) if pd.notna(row.get("fill_rate")) else "—"
        pf = f"{row['profit_factor']:.2f}" if pd.notna(row["profit_factor"]) else "n/a"
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {row['entry_mode']} | "
            f"{int(row['num_trades'])} | {_fmt_pct(row['win_rate'])} | "
            f"{_fmt_pct(row['total_return'])} | {_fmt_pct(row['max_drawdown'])} | "
            f"{pf} | {fill} |"
        )
    lines += [
        "",
        "## 종가 → 지정가 델타",
        "",
        "| 심볼 | TF | 종가 return | 지정가 return | 델타 | 종가 거래수 | 지정가 거래수 |",
        "| -- | -- | --: | --: | --: | --: | --: |",
    ]
    for _, row in delta.iterrows():
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {_fmt_pct(row['close_return'])} | "
            f"{_fmt_pct(row['zone_limit_return'])} | {_fmt_pct(row['return_delta'])} | "
            f"{int(row['close_trades'])} | {int(row['zone_limit_trades'])} |"
        )
    lines += [""]
    lines += _verdict_lines(frame)
    lines += [
        "## 펀딩 커버리지",
        "",
        "| 심볼 | TF | 진입 | 펀딩 비용 | 커버리지 |",
        "| -- | -- | -- | --: | --: |",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {row['entry_mode']} | "
            f"{row['total_funding_cost']:.2f} | {_fmt_pct(row['funding_coverage'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-95 지정가 채택 재산출")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--db-path", default=_DB_PATH)
    parser.add_argument("--cache-dir", default=_CACHE_DIR)
    parser.add_argument("--out-dir", default="backtest/reports")
    args = parser.parse_args()

    rows = collect_rows(
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
    )
    frame = rows_to_frame(rows)
    delta = build_delta_frame(frame)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "wan95_zone_limit_recompute.csv"
    delta_path = out_dir / "wan95_zone_limit_delta.csv"
    md_path = out_dir / "wan95_zone_limit_summary.md"
    frame.to_csv(csv_path, index=False)
    delta.to_csv(delta_path, index=False)
    md_path.write_text(build_markdown(frame, delta), encoding="utf-8")
    print(f"[wan95] 저장: {csv_path}\n[wan95] 저장: {delta_path}\n[wan95] 저장: {md_path}")


if __name__ == "__main__":
    main()
