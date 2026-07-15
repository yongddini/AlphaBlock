"""WAN-108: 다중 포지션 재판정 — 15m 축 위에서, 사이징 2안을 나란히.

[WAN-103](../docs/decisions/wan103.md)은 "다중 포지션은 OOS에서 나쁘다"로 결론냈지만
그 판정은 **1h 단독 전제**였다 — 다중 포지션이 OOS에서 이기던 유일한 유니버스는
[WAN-97](../docs/decisions/wan97.md)이 제외한 15m이 끌고 갔기 때문이다. 그래서 WAN-103은
명시적 단서를 달았다: *"15m 재판정이 15m을 되살리면 이 판정은 그 위에서 다시 해야 한다."*
[WAN-107](../docs/decisions/wan107.md)이 공식 렌즈(`baseline`)에서 「15m 제외」를 번복해
**그 발동 조건이 충족됐다.** 이 모듈이 그 재판정의 숫자를 낸다.

## WAN-103과 무엇이 다른가

1. **15m이 본판정 축**이다(1h와 공동 작업 TF, WAN-107). 4h는 대조 병기, 1d는 층 1에서 제외.
2. **사이징이 축이 됐다**(사용자 확정). WAN-103은 1안(`risk_pct`)만 돌았고, 그래서
   `wan103.md` §5의 경고("이 엔진의 레버리지는 위험 배수가 아니라 명목 천장이다 —
   사용자의 「1/N 시드 × N배」 모델과 다른 물건이다")를 **말로만** 남겼다. 2안
   (`fixed_notional`)이 그 경고를 정면으로 검증한다.
3. **통합 책(multi_tf)에 4h·1d 존을 넣는다**(사용자 지적: *"15분봉에 진입한 상태에서
   4시간봉 존에 닿을 수 있다"*). 4h·1d는 **단독 판정**의 대상이 아니지만(표본 부족),
   하위 TF 포지션 보유 중 상위 TF 존이 닿아 포지션이 추가되는 통합 포트폴리오에서는
   정당한 참여자다.

## 두 층 (사용자 확정)

* **층 1 — per-TF (`series` 범위)**: 15m 존끼리만 / 1h 존끼리만 다중 포지션이 이득인가.
  본판정은 15m·1h, 4h는 대조.
* **층 2 — 통합 포트폴리오 (`pooled` 범위)**: 여러 TF 존을 **한 계좌에 합쳐** 동시 보유.
  `multi_tf` 유니버스가 15m/1h/4h/1d 존을 전부 진입 소스로 받는다.

## 왜 범용 CLI(`backtest.run`)가 아닌가 (WAN-101 규칙)

WAN-103과 같은 이유다: 동시 겹침(peak concurrency)은 격자 한 셀의 성과 지표가 아니라
**실행 도중의 상태**이고, "IS에서 잰 N을 OOS에 적용"은 셀 사이에 의존이 있는 2단계
절차다. 여기에 사이징 축의 청산 계측이 더해진다. 골격(데이터 로딩·구간 분할)과 포트폴리오
경로는 `backtest.harness`·`backtest.wan103_portfolio_leverage_report`에서 **그대로 가져다
쓴다** — 새로 짜는 건 사이징 축과 그 축이 요구하는 열뿐이다.

## 재현

```
uv run python -m backtest.wan108_multi_position_reappraisal
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import DEFAULT_YEARS, mean_r, segments_for
from backtest.harness import load_market_data as _load_market_data
from backtest.models import BacktestConfig, Trade
from backtest.portfolio import C, PortfolioParams, PortfolioStats, ToTrade, sequence_portfolio
from backtest.wan103_portfolio_leverage_report import (
    PARAMS,
    _Cell,
    _pool,
    _tagged_to_trade,
    build_cell,
    measure_concurrency,
    run_scenario,
)
from backtest.zone_limit_backtest import (
    _to_trade,
    apply_portfolio_leverage,
    build_result_from_trades,
    sequence_with_candidates,
)
from data.models import FundingRate
from execution.sizing import SizingMode

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")

SCENARIO_SINGLE = "single"
SCENARIO_PEAK = "peak_N"

SCOPE_SERIES = "series"
SCOPE_POOLED = "pooled"

#: 층 1(per-TF) 축. 15m·1h가 본판정(WAN-107 공동 작업 TF), 4h는 대조 병기.
#: 1d는 제외 — 10거래뿐이라 단일/다중 무관하게 의사결정 가치가 없다(WAN-107).
SERIES_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")

#: 층 2(통합 책) 유니버스. `multi_tf`가 4h·1d 존까지 받는 이유는 모듈 docstring 참고 —
#: "이 TF 단독으로 엣지가 있냐"와 "하위 TF 포지션 보유 중 상위 TF 존이 닿느냐"는 다른
#: 질문이고, 표본 부족은 앞 질문에만 걸리는 제약이다.
#: ⚠️ WAN-103의 `multi_tf`(15m/1h/4h)와 **정의가 다르다** — 두 리포트의 `multi_tf` 행을
#: 나란히 놓고 비교하지 말 것.
UNIVERSES: dict[str, tuple[str, ...]] = {
    "15m_3sym": ("15m",),
    "1h_3sym": ("1h",),
    "multi_tf": ("15m", "1h", "4h", "1d"),
}

#: 후보를 만들어야 하는 TF 전체(층 1 ∪ 층 2).
ALL_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")


@dataclass(frozen=True)
class SizingVariant:
    """사이징 한 축 — 모드·`f`·레버리지 천장을 **한 묶음**으로 들고 다닌다 (WAN-108).

    묶어 두는 이유는 WAN-95/WAN-91이 각각 겪은 조용한 실패와 같은 종류를 막기 위해서다:
    `PortfolioParams(leverage=5)`만 주고 `cfg.risk_sizing.sizing_mode`를 안 바꾸면,
    **1안을 5배로 돌린 결과에 "2안" 라벨이 붙는다.** 두 값이 갈라질 수 없게 한 곳에서
    낸다(`configure` + `leverages`).
    """

    name: str
    mode: SizingMode
    notional_fraction: float
    leverages: tuple[float, ...]
    """다중 포지션 시나리오로 돌 명목 천장들."""
    single_leverage: float
    """단일 포지션 대조군의 명목 천장."""
    peak_scenario: bool
    """`peak_N`(= IS 자연 겹침 최댓값) 시나리오를 함께 돌지."""
    label: str

    def configure(self, cfg: BacktestConfig) -> BacktestConfig:
        """이 사이징을 `cfg`에 싣는다. 리스크 사이징이 없는 설정은 그대로 둔다."""
        if cfg.risk_sizing is None:
            return cfg
        sizing = cfg.risk_sizing.model_copy(
            update={"sizing_mode": self.mode, "notional_fraction": self.notional_fraction}
        )
        return cfg.model_copy(update={"risk_sizing": sizing})


#: 사이징 축(사용자 확정). 1안이 현행 채택 경로이고, 2안이 사용자의 "1/N 시드 × N배"
#: 모델이다. f=0.5는 2안의 대조(노출 절반).
SIZING_VARIANTS: tuple[SizingVariant, ...] = (
    SizingVariant(
        name="risk_pct",
        mode="risk_pct",
        notional_fraction=1.0,
        leverages=(1.0, 2.0, 3.0),
        single_leverage=1.0,
        peak_scenario=True,
        label="1안 — 거래당 리스크 1%(현행 채택 경로)",
    ),
    SizingVariant(
        name="fixed_f1_lev5",
        mode="fixed_notional",
        notional_fraction=1.0,
        leverages=(5.0,),
        single_leverage=5.0,
        peak_scenario=False,
        label="2안 — 명목 = 시드 전액(f=1), 레버리지 천장 5배",
    ),
    SizingVariant(
        name="fixed_f0.5_lev5",
        mode="fixed_notional",
        notional_fraction=0.5,
        leverages=(5.0,),
        single_leverage=5.0,
        peak_scenario=False,
        label="2안 대조 — 명목 = 시드 절반(f=0.5), 레버리지 천장 5배",
    ),
)


class ReappraisalRow(BaseModel):
    """결과 표 한 행 — 좌표(사이징·범위·TF·구간·시나리오)와 성과·위험을 같은 줄에.

    ⚠️ `total_return`이 **1급 열**이다(사용자 지시). 2안은 노출이 크니 위험만이 아니라
    수익도 커지므로, "5배가 위험 대비 값하는가"는 수익과 MDD·청산을 **한 표에서**
    나란히 놓고서만 답할 수 있다.
    """

    model_config = ConfigDict(frozen=True)

    sizing: str
    scope: str
    universe: str
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
    #: 아래 진단은 포트폴리오 시퀀서만 낸다. `single` 행은 그 시퀀서를 타지 않으므로
    #: **None = 계측 안 함**이다(WAN-103과 같은 규칙) — 0으로 채우면 "쟀더니 0이었다"는
    #: 사실 주장이 되는데, 그건 재지 않은 값이다.
    max_open_notional_ratio: float | None
    max_concurrent_risk_ratio: float | None
    clamped_entries: int | None
    skipped_notional: int | None
    liquidations: int | None


ROW_COLUMNS: tuple[str, ...] = tuple(ReappraisalRow.model_fields)


def _fill_rate(cells: Sequence[_Cell]) -> float | None:
    """셀들의 합산 체결률 = Σfilled / Σeligible. 대상 셋업이 없으면 None.

    셀별 체결률의 **평균이 아니라** 합산 비율이다 — 평균은 셋업 수가 적은 셀을 큰 셀과
    같은 무게로 세어, 통합 책의 "지정가가 몇 % 체결됐나"를 왜곡한다.
    """
    eligible = sum(cell.stats.eligible for cell in cells)
    filled = sum(cell.stats.filled for cell in cells)
    return filled / eligible if eligible else None


def _row(
    *,
    sizing: str,
    scope: str,
    universe: str,
    symbol: str,
    timeframe: str,
    segment: str,
    scenario: str,
    leverage: float,
    trades: list[Trade],
    cfg: BacktestConfig,
    stats: PortfolioStats | None,
    fill_rate: float | None,
) -> ReappraisalRow:
    result = build_result_from_trades(trades, cfg, timeframe)
    m = result.metrics
    return ReappraisalRow(
        sizing=sizing,
        scope=scope,
        universe=universe,
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        scenario=scenario,
        leverage=leverage,
        total_return=m.total_return,
        win_rate=m.win_rate,
        max_drawdown=m.max_drawdown,
        num_trades=m.num_trades,
        fill_rate=fill_rate,
        mean_r=mean_r(result, PARAMS.take_profit_r),
        sharpe=m.sharpe,
        # `single`은 정의상 동시 1포지션이라 peak=1이 계측이 아니라 사실이다.
        peak_concurrency=stats.peak_concurrency if stats else 1,
        max_open_notional_ratio=stats.max_open_notional_ratio if stats else None,
        max_concurrent_risk_ratio=stats.max_concurrent_risk_ratio if stats else None,
        clamped_entries=stats.clamped_entries if stats else None,
        skipped_notional=stats.skipped_notional if stats else None,
        liquidations=len(stats.liquidations) if stats else None,
    )


def _scenarios(variant: SizingVariant, peak: int) -> list[tuple[str, float]]:
    """이 사이징에서 돌 다중 포지션 시나리오 — 고정 천장들 + (선택) N배.

    N이 고정 축과 겹치면 `peak_N` 행을 따로 내지 않는다 — 같은 숫자를 두 줄로 내면
    표가 "다른 실행"처럼 읽힌다(WAN-103과 같은 규칙).
    """
    scenarios = [(f"lev_{lev:g}", lev) for lev in variant.leverages]
    if variant.peak_scenario and peak >= 1 and float(peak) not in variant.leverages:
        scenarios.append((SCENARIO_PEAK, float(peak)))
    return scenarios


def series_rows(cell: _Cell, variant: SizingVariant) -> list[ReappraisalRow]:
    """한 (심볼, TF, 구간, 사이징)의 단일 포지션 대조군 + 고정 레버리지 시나리오.

    ⚠️ **series에는 `peak_N` 행이 없다** — N배는 `pooled`에서만 낸다. 이유 둘:

    1. **셀 하나에서 N을 재면 그 N은 자기 구간의 것**이다. OOS 셀에서 잰 N을 그 OOS에
       적용하면 look-ahead다 — "미래에 N+1이 겹치면?"이라는 위험이 정의상 사라진다.
       pooled은 IS에서 N을 재서 OOS에 넘기므로(`peak_override`) 그 규율이 산다.
    2. **심볼마다 N이 달라 평균이 부분집합끼리 섞인다.** N이 고정 축(1·2·3배)과 겹치는
       심볼은 `peak_N` 행을 내지 않으므로(중복 방지), 심볼 평균이 3심볼이 아니라 1~2심볼
       위에서 계산돼 다른 행과 비교가 안 된다.
    """
    cfg = variant.configure(cell.cfg)
    rows: list[ReappraisalRow] = []
    fill = _fill_rate([cell])

    # 대조군: 동시 1포지션 시퀀서 그 자체. 포트폴리오 시퀀서로 흉내 내지 않는다 —
    # 그러면 존 제약·명목 상한이 섞여 "포지션 제약만의 효과"가 아니게 된다(WAN-103).
    single_cfg = apply_portfolio_leverage(cfg, PortfolioParams(leverage=variant.single_leverage))
    single = [
        trade
        for _, trade in sequence_with_candidates(cell.candidates, single_cfg, list(cell.rates))
    ]
    rows.append(
        _row(
            sizing=variant.name,
            scope=SCOPE_SERIES,
            universe=cell.timeframe,
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            segment=cell.segment,
            scenario=SCENARIO_SINGLE,
            leverage=variant.single_leverage,
            trades=single,
            cfg=single_cfg,
            stats=None,
            fill_rate=fill,
        )
    )

    for name, leverage in _scenarios(variant, peak=0):
        trades, stats, scenario_cfg = run_scenario(
            cell.candidates, cfg, _to_trade, leverage=leverage, rates=list(cell.rates)
        )
        rows.append(
            _row(
                sizing=variant.name,
                scope=SCOPE_SERIES,
                universe=cell.timeframe,
                symbol=cell.symbol,
                timeframe=cell.timeframe,
                segment=cell.segment,
                scenario=name,
                leverage=leverage,
                trades=trades,
                cfg=scenario_cfg,
                stats=stats,
                fill_rate=fill,
            )
        )
    return rows


def _peak(
    candidates: Sequence[C],
    cfg: BacktestConfig,
    to_trade: ToTrade[C],
    rates: Sequence[FundingRate] | None = None,
) -> int:
    """명목 상한이 없을 때의 자연 겹침 최댓값.

    ⚠️ 상한을 걸어 둔 채로 재면 상한이 진입을 스킵시켜 N이 작게 나온다 — 그건 "겹칠 수
    있었던 수"가 아니라 "상한이 허락한 수"다(WAN-103 `measure_concurrency`).
    """
    return measure_concurrency(candidates, cfg, to_trade, rates).peak_concurrency


def pooled_rows(
    universe: str,
    tfs: tuple[str, ...],
    cells: Sequence[_Cell],
    segment: str,
    variant: SizingVariant,
    *,
    peak_override: int | None = None,
) -> tuple[list[ReappraisalRow], int]:
    """유니버스 전체가 자본 하나를 공유하는 라이브 조건의 행들. (행, 자연 N)을 낸다.

    `peak_override`를 주면 그 N으로 `peak_N` 시나리오를 돈다 — IS에서 고른 N을 OOS에
    적용하는 검증에 쓴다(OOS에서 잰 N을 OOS에 쓰면 그 자체가 look-ahead다).

    연율화 계수는 **유니버스에서 가장 촘촘한 TF**의 것을 쓴다(`tfs[0]`). 혼합 유니버스는
    "한 TF"가 없어 어떤 선택도 근사인데, 거래 대부분을 내는 TF에 맞추는 편이 Sharpe를
    가장 덜 왜곡한다. 이 선택은 `total_return`·MDD·승률에는 영향이 없다.
    """
    if not cells:
        return [], 0
    pool = _pool(cells)
    cfg = variant.configure(_pooled_cfg(tfs, cells))
    fill = _fill_rate(cells)
    rows: list[ReappraisalRow] = []

    # pooled 대조군: 유니버스 전체가 포지션 1개를 공유(WAN-83 global = 라이브 조건).
    # `max_concurrent=1`이 곧 동시 1포지션 규칙이라 시퀀서 하나로 대조군까지 낼 수 있다
    # (`tests/test_portfolio.py`가 채택 엔진과의 일치를 고정한다).
    single_portfolio = PortfolioParams(leverage=variant.single_leverage, max_concurrent=1)
    single_cfg = apply_portfolio_leverage(cfg, single_portfolio)
    paired, single_stats = sequence_portfolio(
        pool, single_cfg, _tagged_to_trade, portfolio=single_portfolio
    )
    rows.append(
        _row(
            sizing=variant.name,
            scope=SCOPE_POOLED,
            universe=universe,
            symbol="ALL",
            timeframe="ALL",
            segment=segment,
            scenario=SCENARIO_SINGLE,
            leverage=variant.single_leverage,
            trades=[t for _, t in paired],
            cfg=single_cfg,
            stats=single_stats,
            fill_rate=fill,
        )
    )

    measured = _peak(pool, cfg, _tagged_to_trade) if variant.peak_scenario else 0
    peak = peak_override if peak_override is not None else measured
    for name, leverage in _scenarios(variant, peak):
        trades, stats, scenario_cfg = run_scenario(pool, cfg, _tagged_to_trade, leverage=leverage)
        rows.append(
            _row(
                sizing=variant.name,
                scope=SCOPE_POOLED,
                universe=universe,
                symbol="ALL",
                timeframe="ALL",
                segment=segment,
                scenario=name,
                leverage=leverage,
                trades=trades,
                cfg=scenario_cfg,
                stats=stats,
                fill_rate=fill,
            )
        )
    return rows, measured


def _pooled_cfg(tfs: tuple[str, ...], cells: Sequence[_Cell]) -> BacktestConfig:
    """혼합 유니버스의 기준 설정 — 가장 촘촘한 TF의 셀 설정을 쓴다."""
    for tf in tfs:
        for cell in cells:
            if cell.timeframe == tf:
                return cell.cfg
    return cells[0].cfg


def run_report(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    years: float = DEFAULT_YEARS,
    *,
    log: bool = True,
) -> list[ReappraisalRow]:
    """층 1(series) + 층 2(pooled) 전체 격자를 사이징 축까지 돌린다.

    셀(후보 풀)은 (심볼, TF, 구간)마다 **한 번만** 만들고 모든 사이징·시나리오가
    공유한다 — 후보 생성(오더블록 탐지 + 1분 서브스텝)이 실행 시간의 거의 전부이고,
    사이징은 후보를 바꾸지 않는다(체결 시뮬레이션은 수량을 보지 않는다). 그래서 이
    리포트의 모든 행이 **같은 셋업 풀** 위에 서 있고, 열 사이의 차이는 포지션 제약과
    사이징뿐이다.
    """
    segments = [s for s in segments_for(oos=True) if s.name != "full"]
    cells: dict[tuple[str, str, str], _Cell] = {}
    for symbol in symbols:
        for timeframe in ALL_TIMEFRAMES:
            market = _load_market_data(symbol, timeframe, years=years, need_1m=True)
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan108] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
                continue
            for segment in segments:
                cell = build_cell(symbol, timeframe, market, segment)
                if cell is not None:
                    cells[(symbol, timeframe, segment.name)] = cell
            if log:
                print(f"[wan108] {symbol} {timeframe}: 후보 생성 완료")

    rows: list[ReappraisalRow] = []
    for variant in SIZING_VARIANTS:
        for (_symbol, timeframe, _segment), cell in cells.items():
            if timeframe not in SERIES_TIMEFRAMES:
                continue
            rows.extend(series_rows(cell, variant))
        if log:
            print(f"[wan108] series {variant.name}: 완료")

        for universe, tfs in UNIVERSES.items():
            # ⚠️ N은 IS에서 고른다(OOS에서 잰 N을 OOS에 쓰면 look-ahead).
            is_cells = [c for (s, tf, seg), c in cells.items() if tf in tfs and seg == "is"]
            is_rows, is_peak = pooled_rows(universe, tfs, is_cells, "is", variant)
            rows.extend(is_rows)

            oos_cells = [c for (s, tf, seg), c in cells.items() if tf in tfs and seg == "oos"]
            oos_rows, oos_peak = pooled_rows(
                universe, tfs, oos_cells, "oos", variant, peak_override=is_peak or None
            )
            rows.extend(oos_rows)
            if log:
                # 겹침은 `peak_N`을 도는 축에서만 잰다 — 안 잰 축에 0을 찍으면
                # "쟀더니 0이었다"로 읽힌다(리포트의 `—` 규칙과 같은 이유).
                peaks = (
                    f"IS peak N={is_peak}, OOS 자연 겹침={oos_peak}"
                    if variant.peak_scenario
                    else "겹침 미계측(N배 시나리오 없음)"
                )
                print(f"[wan108] pooled {universe} {variant.name}: 완료 ({peaks})")
    return rows


def rows_to_frame(rows: Sequence[ReappraisalRow]) -> pd.DataFrame:
    records = [row.model_dump() for row in rows]
    return pd.DataFrame(records, columns=list(ROW_COLUMNS))


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-103 리포트와 같은 헬퍼)."""
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


def _pct(frame: pd.DataFrame) -> pd.DataFrame:
    """비율 열을 % 소수 2자리로. 계측하지 않은 칸은 `—`로 적는다(0으로 적으면 오독된다)."""
    out = frame.copy()
    for col in ("total_return", "max_drawdown", "win_rate", "fill_rate"):
        out[col] = (out[col] * 100).round(2)
    for col in ("max_open_notional_ratio", "max_concurrent_risk_ratio"):
        out[col] = out[col].round(3)
    for col in ("sharpe", "mean_r"):
        out[col] = out[col].round(2)
    return out.astype(object).where(out.notna(), "—")


_SERIES_VIEW = (
    "sizing",
    "symbol",
    "timeframe",
    "segment",
    "scenario",
    "total_return",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "fill_rate",
    "mean_r",
    "sharpe",
    "peak_concurrency",
    "max_concurrent_risk_ratio",
    "liquidations",
)

_POOLED_VIEW = (
    "sizing",
    "universe",
    "segment",
    "scenario",
    "leverage",
    "total_return",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "fill_rate",
    "mean_r",
    "sharpe",
    "peak_concurrency",
    "max_open_notional_ratio",
    "max_concurrent_risk_ratio",
    "liquidations",
)


def symbol_mean(rows: Sequence[ReappraisalRow], scope: str = SCOPE_SERIES) -> pd.DataFrame:
    """series 행의 심볼 평균 — 판정이 읽는 표(WAN-103 §1과 같은 좌표).

    심볼별로 부호가 갈리는 일이 잦으므로(WAN-103 §2) 평균만 보지 말 것 — 원본 CSV에
    심볼별 행이 그대로 남아 있다.
    """
    frame = rows_to_frame(rows)
    frame = frame[frame["scope"] == scope]
    grouped = (
        frame.groupby(["sizing", "timeframe", "segment", "scenario"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            num_trades=("num_trades", "sum"),
            liquidations=("liquidations", "sum"),
            symbols=("symbol", "count"),
        )
        .sort_values(["sizing", "timeframe", "segment", "scenario"])
    )
    return grouped.reset_index(drop=True)


def write_summary(rows: Sequence[ReappraisalRow], path: Path) -> None:
    frame = rows_to_frame(rows)
    series = _pct(frame[frame["scope"] == SCOPE_SERIES]).reset_index(drop=True)
    pooled = _pct(frame[frame["scope"] == SCOPE_POOLED]).reset_index(drop=True)
    means = symbol_mean(rows)
    for col in ("total_return", "win_rate", "max_drawdown"):
        means[col] = (means[col] * 100).round(2)

    lines = [
        "# WAN-108: 다중 포지션 재판정 (15m 축) + 사이징 1안/2안 대조",
        "",
        "재현: `uv run python -m backtest.wan108_multi_position_reappraisal`",
        "",
        "WAN-112 이전 채택 기본값(롱 온리·지정가·`first_tap_free`·고정 1.5R·**오프셋 0bp**) 위에서 "
        "**포지션 제약과 사이징만** 바꿔 돌린 결과다. 전략 파라미터는 하나도 건드리지 않았다. "
        "결론과 판정은 [`docs/decisions/wan103.md`](../../docs/decisions/wan103.md) "
        "「WAN-108 재판정」 절.",
        "",
        "> ⚠️ **`total_return`은 공식 렌즈 `baseline`(닿으면 체결) 기준**이라 상한으로 읽어야 "
        "한다(WAN-104). 대조표의 모든 열이 같은 렌즈를 쓰므로 **차이**는 유효하지만, "
        "**절대 수익률은 낙관**이다.",
        "",
        "> ⚠️ **사이징 2안(`fixed_notional`)은 청산이 실재한다** — 1안은 손절이 자리당 손실을 "
        "자본의 1%로 묶지만, 2안은 명목이 고정이라 손실이 손절 거리에 비례해 **상한이 없다**. "
        "`liquidations` 열은 최악 가정(열린 포지션이 전부 동시에 손절) 트리거 수다.",
        "",
        "## 심볼 평균 — 판정이 읽는 표 (series 범위)",
        "",
        "⚠️ 심볼별로 부호가 갈리는 일이 잦다 — 평균만 보지 말고 아래 전체 표를 볼 것.",
        "",
        _md_table(means),
        "",
        "## 층 1: series 범위 — (심볼, TF)가 자기 자본으로 도는 현행 백테스트 조건",
        "",
        "`single`이 동시 1포지션(채택 제약)이고 `lev_*`가 같은 셋업 풀을 동시 다중 포지션으로 "
        "배치한 것이다. 같은 `sizing` 안에서 두 행의 차이는 **오직 포지션 제약**이고, "
        "같은 `scenario`의 두 `sizing` 행의 차이는 **오직 사이징**이다.",
        "",
        _md_table(series[list(_SERIES_VIEW)]),
        "",
        "## 층 2: pooled 범위 — 유니버스 전체가 자본 하나를 공유(라이브 조건)",
        "",
        "`multi_tf`는 15m/1h/4h/1d 존을 **한 계좌에 합쳐** 굴린다(하위 TF 포지션 보유 중 "
        "상위 TF 존이 닿으면 포지션이 추가되는 실매매 조건). `peak_N`의 N은 **IS에서 잰 자연 "
        "겹침 최댓값**이며 OOS 행은 그 N을 적용한 검증이다.",
        "",
        _md_table(pooled[list(_POOLED_VIEW)]),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument(
        "--out-csv", type=str, default="backtest/reports/wan108_multi_position_reappraisal.csv"
    )
    parser.add_argument(
        "--out-md",
        type=str,
        default="backtest/reports/wan108_multi_position_reappraisal_summary.md",
    )
    args = parser.parse_args()

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    rows = run_report(symbols=symbols, years=args.years)
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows, Path(args.out_md))
    print(f"[wan108] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
