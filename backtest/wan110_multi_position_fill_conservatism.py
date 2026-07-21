"""WAN-110: 15m 다중 포지션의 체결 보수화 재검 — 「15m 다중 승리」는 낙관 위에 서 있나.

[WAN-108](../docs/decisions/wan103.md)이 다중 포지션을 15m 축에서 재판정해 **15m OOS는
다중이 이기고(+23.26% → +38.81%) 1h OOS는 단독이 이긴다(+8.18% → +5.66%)**로 냈고, 두
작업 TF가 정반대라 기본값(동시 1포지션)은 유지했다. 그 판정이 스스로 남긴 단서가 이
모듈의 질문이다:

> "15m의 승리는 `baseline`(낙관) 위에 서 있는데 다중 포지션은 **체결에 더 의존**한다
> (OOS 거래 772→838건). 그게 15m 축 후속 이슈의 질문이다."

즉 15m 다중의 초과 수익이 "지정가에 닿으면 체결"이라는 가정에서 얼마나 나오는지가
아직 안 갈렸다. [WAN-96](../docs/decisions/wan97.md)이 **단일 포지션**에 한 체결 보수화를
**다중 포지션**에 하는 것이 이 모듈이다.

## 왜 다중이 체결에 더 취약하다고 의심하는가

동시 1포지션은 포지션을 들고 있는 동안 새 지정가를 **아예 걸지 않는다** — 즉 체결
가정이 나빠져도 "어차피 안 걸었을 주문"이 많다. 다중은 그 셋업을 전부 실제로 걸므로
체결 모델이 틀리면 **틀리는 면적이 넓다**. WAN-96의 핵심 진단(거래는 조금 주는데 수익은
통째로 사라진다 = 수익이 스치듯 닿은 체결에 몰려 있다)이 다중에서 더 심한지 잰다.

## 축

* **렌즈 3개**(`harness.FILL_PRESETS` 재사용): `baseline`(공식, WAN-104) ·
  `pen_5bp`(민감도) · `pen_5bp_drop_50`(스트레스, 시드 5개).
* **포지션 제약**: `single`(채택 기본값) vs `lev_1/2/3`(다중) — WAN-108 series 축 그대로.
* **TF**: 15m(본안) · 1h(대조 병기). **IS/OOS 분할.**
* **사이징은 축이 아니다** — 1안(`risk_pct`, 현행 채택 경로)만 돈다. 2안은 WAN-108이
  기각했고(업사이드 없음), 그 기각은 체결 축과 독립이다.

## 왜 범용 CLI(`backtest.run`)가 아닌가 (WAN-101 규칙)

CLI에는 **포지션 제약 축이 없다** — `--fill`은 있지만 단일/다중을 가를 수단이 없고,
`harness`에도 포트폴리오 개념이 없다. 이 질문은 정의상 두 축의 **교차**라 CLI로는 한
쪽밖에 못 낸다. 엔진은 새로 짜지 않았다: 후보 생성·시퀀싱·레버리지 배선은 WAN-103/108
경로(`build_cell`·`run_scenario`)를, 렌즈는 WAN-96 노브(`harness.FILL_PRESETS`)를 그대로
가져다 쓴다.

## 재현

```
uv run python -m backtest.wan110_multi_position_fill_conservatism
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    DEFAULT_YEARS,
    LEGACY_BAND_BAR,
    LEGACY_OB_PARAMS,
    FillPreset,
    build_params,
    detect_order_blocks,
    fill_preset,
    load_market_data,
    mean_r,
    pin_band_bar,
    segments_for,
    slice_market,
)
from backtest.models import BacktestConfig, Trade
from backtest.portfolio import PortfolioStats
from backtest.wan103_portfolio_leverage_report import PARAMS, _Cell, build_cell, run_scenario
from backtest.zone_limit_backtest import (
    _to_trade,
    build_result_from_trades,
    sequence_with_candidates,
)
from strategy.models import BandBar, ConfluenceParams

#: 이 리포트가 고정한 오프셋(bp) — **WAN-103과 같은 셋업 풀**을 봐야 하므로 그쪽 엔진에서
#: 그대로 가져온다(WAN-112 이후 채택 기본값 2bp를 따라가면 안 된다). 이 재검의 질문은
#: "같은 풀에 렌즈만 조이면 다중 우위가 남는가"라, 풀이 움직이면 질문 자체가 성립하지 않는다.
PINNED_OFFSET_BPS = PARAMS.zone_limit_offset_bps

#: 같은 이유로 RSI 게이트도 WAN-103 엔진에서 가져온다(WAN-123이 기본값을 `unconditional`로
#: 옮겼다). 게이트 제거는 오프셋과 달리 **셋업 풀 자체를 13~14% 넓히므로**, 따라가게 두면
#: 이 재검이 WAN-103과 다른 풀을 보게 되어 "같은 풀에 렌즈만 조인다"는 전제가 깨진다.
PINNED_RSI_GATE_MODE = PARAMS.rsi_gate_mode

#: 밴드 표본도 마찬가지다(WAN-132가 기본값을 `intrabar_live`로 옮겼다) — 밴드 정의가
#: 다르면 진입가가 달라져 WAN-103과 **다른 셋업 풀**을 보게 된다.
PINNED_BAND_BAR: BandBar = (
    PARAMS.deviation_filter.band_bar if PARAMS.deviation_filter else LEGACY_BAND_BAR
)

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")

#: 본안 15m + 대조 1h. WAN-107 공동 작업 TF이고, 이 이슈의 완료기준이 지정한 축이다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 렌즈 3개 — 공식·민감도·스트레스(CLAUDE.md 토대 2). 이름·값은 `harness`에서 가져오므로
#: CLI(`--fill`)·WAN-96/97/107 표와 **같은 뜻**이 보장된다.
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")

#: 다중 포지션 시나리오의 명목 천장. WAN-108 series 축과 같은 좌표라 두 리포트의 행을
#: 나란히 놓고 읽을 수 있다(WAN-108은 `baseline` 렌즈 한 장 = 이 표의 첫 렌즈).
MULTI_LEVERAGES: tuple[float, ...] = (1.0, 2.0, 3.0)

SCENARIO_SINGLE = "single"

#: WAN-108의 15m 헤드라인이 선 시나리오. 판정 문장이 읽는 다중 대표값이다.
HEADLINE_SCENARIO = "lev_3"


class FillRow(BaseModel):
    """결과 표 한 행 — 좌표(렌즈·시드·심볼·TF·구간·시나리오)와 성과를 같은 줄에."""

    model_config = ConfigDict(frozen=True)

    lens: str
    seed: int
    symbol: str
    timeframe: str
    segment: str
    scenario: str
    leverage: float
    total_return: float
    win_rate: float
    max_drawdown: float
    num_trades: int
    fill_rate: float | None
    mean_r: float | None
    sharpe: float | None
    peak_concurrency: int
    #: 포트폴리오 시퀀서만 내는 진단. `single` 행은 그 시퀀서를 타지 않으므로
    #: **None = 계측 안 함**이다(WAN-103/108과 같은 규칙) — 0으로 채우면 "쟀더니 0이었다"는
    #: 사실 주장이 되는데, 그건 재지 않은 값이다.
    max_concurrent_risk_ratio: float | None
    liquidations: int | None


ROW_COLUMNS: tuple[str, ...] = tuple(FillRow.model_fields)


def _row(
    *,
    lens: str,
    seed: int,
    cell: _Cell,
    scenario: str,
    leverage: float,
    trades: list[Trade],
    cfg: BacktestConfig,
    stats: PortfolioStats | None,
) -> FillRow:
    result = build_result_from_trades(trades, cfg, cell.timeframe)
    m = result.metrics
    return FillRow(
        lens=lens,
        seed=seed,
        symbol=cell.symbol,
        timeframe=cell.timeframe,
        segment=cell.segment,
        scenario=scenario,
        leverage=leverage,
        total_return=m.total_return,
        win_rate=m.win_rate,
        max_drawdown=m.max_drawdown,
        num_trades=m.num_trades,
        # 체결률은 **후보 풀의 성질**이라 시나리오와 무관하다 — 같은 셀을 공유하는
        # `single`·`lev_*` 행의 값이 같은 게 맞다(체결 시뮬레이션은 포지션 제약을 보지 않는다).
        fill_rate=cell.stats.fill_rate,
        mean_r=mean_r(result, build_params().take_profit_r),
        sharpe=m.sharpe,
        peak_concurrency=stats.peak_concurrency if stats else 1,
        max_concurrent_risk_ratio=stats.max_concurrent_risk_ratio if stats else None,
        liquidations=len(stats.liquidations) if stats else None,
    )


def cell_rows(cell: _Cell, lens: str, seed: int) -> list[FillRow]:
    """한 (렌즈, 시드, 심볼, TF, 구간)의 단일 대조군 + 다중 시나리오들.

    같은 셀(= 같은 후보 풀)을 공유하므로 이 행들의 차이는 **오직 포지션 제약**이다.
    체결 렌즈는 후보 풀 자체를 바꾸므로 렌즈가 다른 행끼리는 셋업 수부터 다르다 —
    그 차이가 이 리포트가 재려는 것이다.
    """
    rows: list[FillRow] = []

    # 대조군은 채택 엔진 그 자체(동시 1포지션 시퀀서). 포트폴리오 시퀀서로 흉내 내지
    # 않는다 — 그러면 존 제약·명목 상한이 섞여 "포지션 제약만의 효과"가 아니게 된다
    # (WAN-103/108과 같은 규칙).
    single = [
        trade for _, trade in sequence_with_candidates(cell.candidates, cell.cfg, list(cell.rates))
    ]
    rows.append(
        _row(
            lens=lens,
            seed=seed,
            cell=cell,
            scenario=SCENARIO_SINGLE,
            leverage=1.0,
            trades=single,
            cfg=cell.cfg,
            stats=None,
        )
    )

    for leverage in MULTI_LEVERAGES:
        trades, stats, cfg = run_scenario(
            cell.candidates, cell.cfg, _to_trade, leverage=leverage, rates=list(cell.rates)
        )
        rows.append(
            _row(
                lens=lens,
                seed=seed,
                cell=cell,
                scenario=f"lev_{leverage:g}",
                leverage=leverage,
                trades=trades,
                cfg=cfg,
                stats=stats,
            )
        )
    return rows


def _lens_seeds(preset: FillPreset) -> tuple[int, ...]:
    """이 렌즈에서 돌 시드들.

    탈락이 없는 렌즈는 난수를 아예 뽑지 않으므로(`zone_limit_backtest`) 시드가 결과에
    영향이 없다 — 시드 5개를 돌면 **같은 숫자 5줄**이 나와 표가 "5번 검증했다"로 오독된다.
    """
    return preset.seeds if preset.dropout_rate > 0 else (0,)


def run_report(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    *,
    log: bool = True,
) -> list[FillRow]:
    """렌즈 × 시드 × 포지션 제약 격자를 돈다.

    셀은 **(심볼, TF, 구간, 렌즈, 시드)마다** 새로 만든다 — 사이징과 달리 체결 렌즈는
    후보 풀 자체를 바꾸므로(관통 요구·탈락 추첨이 체결 판정에 들어간다) WAN-108처럼
    셀을 공유할 수 없다. 대신 **오더블록 탐지는 (심볼, TF, 구간)마다 한 번만** 하고 모든
    렌즈가 공유한다 — 탐지는 컨플루언스 파라미터와 무관하다(`harness.detect_order_blocks`).
    """
    segments = [s for s in segments_for(oos=True) if s.name != "full"]
    presets = [fill_preset(name) for name in LENS_NAMES]
    rows: list[FillRow] = []

    for symbol in symbols:
        for timeframe in timeframes:
            market = load_market_data(symbol, timeframe, years=years, need_1m=True)
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan110] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
                continue
            for segment in segments:
                window = slice_market(market, segment)
                if window.empty or window.df_1m.empty:
                    continue
                order_blocks = detect_order_blocks(window, LEGACY_OB_PARAMS)
                for preset in presets:
                    for seed in _lens_seeds(preset):
                        cell = build_cell(
                            symbol,
                            timeframe,
                            market,
                            segment,
                            params=build_params(
                                fill=preset,
                                seed=seed,
                                offset_bps=PINNED_OFFSET_BPS,
                                base=pin_band_bar(
                                    ConfluenceParams(rsi_gate_mode=PINNED_RSI_GATE_MODE),
                                    PINNED_BAND_BAR,
                                ),
                            ),
                            order_blocks=order_blocks,
                        )
                        if cell is None:
                            continue
                        rows.extend(cell_rows(cell, preset.name, seed))
                if log:
                    print(f"[wan110] {symbol} {timeframe} {segment.name}: 완료")
    return rows


def rows_to_frame(rows: Sequence[FillRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows], columns=list(ROW_COLUMNS))


def aggregate(rows: Sequence[FillRow]) -> pd.DataFrame:
    """심볼·시드를 가로질러 평균 낸 판정용 표.

    ⚠️ 시드 평균은 `pen_5bp_drop_50`에서만 여러 값을 섞는다. 시드 분산이 크다는 것이
    WAN-100의 관찰이므로(15m BTC −7.7%~+17.4%) 평균 옆에 **최소·최대**를 함께 낸다 —
    평균만 보면 개별 시드의 마이너스가 안 보인다.
    """
    frame = rows_to_frame(rows)
    by_seed = (
        frame.groupby(["lens", "timeframe", "segment", "scenario", "seed"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            num_trades=("num_trades", "sum"),
            symbols_positive=("total_return", lambda s: int((s > 0).sum())),
            symbols=("symbol", "count"),
        )
        .reset_index(drop=True)
    )
    return (
        by_seed.groupby(["lens", "timeframe", "segment", "scenario"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            return_min=("total_return", "min"),
            return_max=("total_return", "max"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            num_trades=("num_trades", "mean"),
            symbols_positive=("symbols_positive", "mean"),
            symbols=("symbols", "max"),
            seeds=("seed", "count"),
        )
        .sort_values(["timeframe", "segment", "lens", "scenario"])
        .reset_index(drop=True)
    )


def asymmetry(agg: pd.DataFrame) -> pd.DataFrame:
    """WAN-96의 핵심 진단 — 거래는 얼마나 주는데 수익은 얼마나 사라지나.

    렌즈를 보수화하면 체결이 줄어 거래가 준다. 그 감소분보다 **수익 감소가 훨씬 크면**
    수익이 "스치듯 닿은 체결"에 몰려 있다는 뜻이다(WAN-96 §핵심). `single`과 다중을
    나란히 내는 이유는 WAN-108의 단서 — 다중이 체결에 더 의존한다면 이 비대칭이
    다중에서 **더 심해야** 한다.

    `return_kept`는 `baseline` 대비 남은 수익 비율이라 부호가 뒤집히면 음수가 된다.
    """
    base = agg[agg["lens"] == "baseline"].set_index(["timeframe", "segment", "scenario"])
    records: list[dict[str, object]] = []
    for _, row in agg[agg["lens"] != "baseline"].iterrows():
        key = (row["timeframe"], row["segment"], row["scenario"])
        if key not in base.index:
            continue
        ref = base.loc[key]
        base_return = float(ref["total_return"])
        base_trades = float(ref["num_trades"])
        records.append(
            {
                "timeframe": row["timeframe"],
                "segment": row["segment"],
                "scenario": row["scenario"],
                "lens": row["lens"],
                "base_return": base_return,
                "lens_return": float(row["total_return"]),
                "return_kept": (
                    float(row["total_return"]) / base_return if base_return else float("nan")
                ),
                "base_trades": base_trades,
                "lens_trades": float(row["num_trades"]),
                "trades_kept": (
                    float(row["num_trades"]) / base_trades if base_trades else float("nan")
                ),
            }
        )
    return pd.DataFrame(records)


def verdict(agg: pd.DataFrame, timeframe: str = "15m", segment: str = "oos") -> list[str]:
    """렌즈별로 「다중이 단일을 이기는가」를 숫자에서 직접 읽어 문장으로 낸다.

    표를 사람이 눈으로 읽고 결론을 손으로 적으면 재실행 때 문장과 숫자가 갈라진다 —
    WAN-95가 겪은 라벨/실체 불일치와 같은 종류의 사고다. 그래서 판정을 계산한다.
    """
    lines: list[str] = []
    view = agg[(agg["timeframe"] == timeframe) & (agg["segment"] == segment)]
    for lens in LENS_NAMES:
        sub = view[view["lens"] == lens]
        single = sub[sub["scenario"] == SCENARIO_SINGLE]
        multi = sub[sub["scenario"] == HEADLINE_SCENARIO]
        if single.empty or multi.empty:
            continue
        s = float(single.iloc[0]["total_return"])
        m = float(multi.iloc[0]["total_return"])
        winner = "다중" if m > s else "단일"
        lines.append(
            f"- **{lens}**: 단일 {s * 100:+.2f}% vs 다중({HEADLINE_SCENARIO}) {m * 100:+.2f}% "
            f"→ **{winner} 우위** (차 {(m - s) * 100:+.2f}%p)"
        )
    return lines


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-103/108 리포트와 같은 헬퍼)."""
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


def _pct(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """비율 열을 % 소수 2자리로. 계측하지 않은 칸은 `—`(0으로 적으면 오독된다)."""
    out = frame.copy()
    for col in columns:
        if col in out.columns:
            out[col] = (out[col] * 100).round(2)
    for col in ("mean_r", "sharpe", "max_concurrent_risk_ratio", "num_trades"):
        if col in out.columns:
            out[col] = out[col].round(2)
    return out.astype(object).where(out.notna(), "—")


_AGG_VIEW = (
    "timeframe",
    "segment",
    "lens",
    "scenario",
    "total_return",
    "return_min",
    "return_max",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "symbols_positive",
    "seeds",
)


def write_summary(rows: Sequence[FillRow], path: Path) -> None:
    agg = aggregate(rows)
    asym = asymmetry(agg)

    agg_view = _pct(
        agg[list(_AGG_VIEW)],
        ("total_return", "return_min", "return_max", "win_rate", "max_drawdown"),
    )
    asym_view = asym.copy()
    for col in ("base_return", "lens_return"):
        asym_view[col] = (asym_view[col] * 100).round(2)
    for col in ("return_kept", "trades_kept"):
        asym_view[col] = (asym_view[col] * 100).round(1)
    for col in ("base_trades", "lens_trades"):
        asym_view[col] = asym_view[col].round(1)

    lines = [
        "# WAN-110: 15m 다중 포지션의 체결 보수화 재검",
        "",
        "재현: `uv run python -m backtest.wan110_multi_position_fill_conservatism`",
        "",
        "[WAN-108](../../docs/decisions/wan103.md)의 「15m OOS는 다중이 이긴다」가 공식 렌즈 "
        "`baseline`(닿으면 체결 = 낙관) 위에 서 있다는 단서를 검증한다. 채택 기본값 "
        "(`ConfluenceParams()`) 위에서 **체결 렌즈와 포지션 제약만** 바꿨다 — 전략 "
        "파라미터도 사이징(1안 `risk_pct`)도 건드리지 않았다.",
        "",
        "> ⚠️ **렌즈가 다른 행끼리는 셋업 풀부터 다르다** — 같은 렌즈 안에서 `single` vs "
        "`lev_*`의 차이만이 **오직 포지션 제약**의 효과다. 렌즈 간 비교는 그 축의 "
        "민감도를 보는 것이지 같은 실험의 반복이 아니다.",
        "",
        "> ⚠️ **`pen_5bp_drop_50`은 시드 5개의 평균**이라 `return_min`/`return_max`를 함께 "
        "읽어야 한다. 나머지 두 렌즈는 난수를 뽑지 않으므로 시드가 1개다(`seeds` 열).",
        "",
        "## 판정 — 15m OOS에서 다중이 단일을 이기는가 (렌즈별)",
        "",
        *verdict(agg, "15m", "oos"),
        "",
        "대조 — 1h OOS:",
        "",
        *verdict(agg, "1h", "oos"),
        "",
        "## 렌즈 × 포지션 제약 × IS/OOS — 심볼(·시드) 평균",
        "",
        "⚠️ 심볼별로 부호가 갈리는 일이 잦다(`symbols_positive` = 플러스 심볼 수 / "
        "`symbols`). 평균만 보지 말고 원본 CSV의 심볼별 행을 볼 것.",
        "",
        _md_table(agg_view),
        "",
        "## WAN-96 비대칭 진단 — 거래는 얼마나 주는데 수익은 얼마나 사라지나",
        "",
        "`baseline` 대비 남은 비율이다. **`return_kept`가 `trades_kept`보다 훨씬 작으면** "
        "수익이 '지정가를 스치듯 닿고 되돌아선 체결'에 몰려 있다는 뜻이다(WAN-96 §핵심). "
        "WAN-108의 단서대로라면 이 비대칭이 다중(`lev_*`)에서 단일보다 **더 심해야** 한다.",
        "",
        _md_table(asym_view),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument(
        "--out-csv",
        type=str,
        default="backtest/reports/wan110_multi_position_fill_conservatism.csv",
    )
    parser.add_argument(
        "--out-md",
        type=str,
        default="backtest/reports/wan110_multi_position_fill_conservatism_summary.md",
    )
    args = parser.parse_args()

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    timeframes = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())
    rows = run_report(symbols=symbols, timeframes=timeframes, years=args.years)
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows, Path(args.out_md))
    print(f"[wan110] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
