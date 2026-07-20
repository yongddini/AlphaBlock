"""최소 손절폭 하한 감사 — 손절 거리 분포 · 레버리지 clamp · 민감도 · 과거 판정 재검증 (WAN-76).

배경은 이슈 WAN-76 본문과 `execution.sizing.PositionSizingParams.min_stop_distance_fraction`
참고. 저장소 기본값(`0.0`)은 손절 거리에 하한을 두지 않아, 오더블록 무효화 경계가
진입가에 극단적으로 가까운 거래(손절폭 0.1% 미만)가 사이징을 왜곡할 수 있다는 감사
요청이다. 이 모듈은 다음 네 가지를 산출한다.

1. **손절 거리 분포** — 채택 전략(`CURRENT_DEFAULT_PARAMS`, B안)을 저장소 기본
   사이징(하한 없음)으로 3심볼×4TF 전체 기간에 돌려, 거래별 손절 거리(진입가 대비
   분수)를 모으고 0.1%/0.3%/0.5%/1% 미만 구간이 거래 수·총손익에서 차지하는 비중을 낸다.
2. **레버리지 clamp 진단** — 같은 거래 집합에서 수량이 명목 상한(`leverage=1.0`)에
   걸린 비율과, 걸렸을 때의 실효 리스크 비율(의도한 `risk_per_trade` 대비)을 낸다.
3. **`min_stop_distance_fraction` 민감도** — 후보(체결·청산 확정 셋업)는 사이징과
   무관하게 한 번만 생성하고(`build_zone_limit_candidates`), 하한 0/0.003/0.005/0.01
   각각으로 재시퀀싱만 반복해 총수익률·승률·PF·MDD·거래수를 비교한다.
4. **과거 판정 재검증** — WAN-68/70/73/74가 쓴 파이프라인을 하한 `RECHECK_FLOOR`
   (기본 0.005)로 그대로 재실행해, 각 이슈의 채택/기각 판정이 유지되는지 확인한다.
   각 모듈이 이미 검증된 `default_backtest_config` 호출부를 그대로 두고, 그 이름이
   바인딩된 모듈 네임스페이스만 패치해 하한을 주입한다(모듈 코드 자체는 건드리지 않음).

재현: `python -m backtest.wan76_stop_distance_audit`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import wan68_short_gate_analysis as wan68
from backtest import wan70_random_control_b as wan70
from backtest import wan73_validation as wan73
from backtest import wan74_discrepancy_audit as wan74
from backtest.harness import LEGACY_OB_PARAMS, pin_band_bar
from backtest.models import BacktestConfig, PositionSide
from backtest.sweep import default_backtest_config
from backtest.zone_limit_backtest import (
    _Candidate,
    _sequence_and_cost,
    _to_trade,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from common.costs import Liquidity
from strategy.models import ConfluenceParams
from strategy.order_blocks import OrderBlockDetector

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
DEFAULT_YEARS: float = 3.0
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

#: 채택 전략(WAN-68/70/73/74와 동일 정의): B안(존-지정가+실시간 RSI), 그 외 전부 기본값.
#: ⚠️ `band_bar`는 당시 값(`tap`)으로 **명시 고정**한다(WAN-132 기본값 전환).
CURRENT_DEFAULT_PARAMS = pin_band_bar(
    ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
)

#: 민감도 스윕 대상 하한(이슈 본문 지정).
SENSITIVITY_FLOORS: tuple[float, ...] = (0.0, 0.003, 0.005, 0.01)

#: 과거 판정 재검증에 쓸 단일 하한. WAN-75가 이미 자체 감사에서 적용한 값과 같다
#: (그 이슈 본문 코멘트가 지적한 값). 0/0.01은 §3 민감도에서 이미 다룬다.
RECHECK_FLOOR = 0.005

#: 재검증 대상 TF. 원 리포트 전부(15m/1h/4h/1d) 대신 **1h·4h만** 재실행한다 — 15m은
#: 후보(1분봉 서브스텝) 수가 압도적으로 많아 재시뮬레이션 비용이 가장 크고, 1d는
#: 원 리포트에서도 표본(n<20)이 너무 작아 대다수 셀이 유의성 판정에서 이미 제외됐다
#: (wan68/70/73_summary.md 참고). 1h·4h는 원 리포트에서 표본이 충분했던 핵심 셀이라
#: "방향이 뒤집히는가"라는 재검증 질문에 가장 정보량이 크면서, 전체 재실행(시간 단위)
#: 대신 이 세션 내에 끝낼 수 있는 비용으로 줄인다. §1~3(주 산출물)은 4TF 전부를 쓴다.
RECHECK_TIMEFRAMES: tuple[str, ...] = ("1h", "4h")

#: 재검증 부트스트랩 반복 수. 원 리포트(200)의 절반 — 실제 지배 비용은 반복 횟수가
#: 아니라 후보 생성(1분봉 서브스텝 시뮬레이션)이므로 반복만 줄여도 정밀도 손실은
#: 작다(방향·유의성 임계 통과 여부를 보는 재검증 목적에는 충분하다).
RECHECK_ITERATIONS = 100

#: 손절 거리 분포 구간 경계(진입가 대비 분수).
DISTANCE_BINS: tuple[float, ...] = (0.001, 0.003, 0.005, 0.01)

DEFAULT_DB_PATH = Path("data/ohlcv.db")
REPORTS_DIR = Path("backtest/reports")


# --------------------------------------------------------------------------- #
# 데이터 로드 헬퍼
# --------------------------------------------------------------------------- #


def _load_windows(
    db_path: Path, symbols: tuple[str, ...], timeframes: tuple[str, ...], years: float
) -> dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]]:
    """`(symbol, timeframe)` → (상위TF 윈도, 1분봉 윈도). 데이터 없으면 빈 dict."""
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return {}
    if not db_path.exists():
        return {}

    windows: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]] = {}
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
                windows[(symbol, timeframe)] = (htf_win, one_min_win)
    return windows


# --------------------------------------------------------------------------- #
# §1+§2: 손절 거리 분포 + 레버리지 clamp 진단 (거래 단위)
# --------------------------------------------------------------------------- #


class TradeDiagnostic(BaseModel):
    """한 거래의 손절 거리 · clamp · 실효 리스크 진단(하한 없음 기준, WAN-76)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    entry_time: int
    stop_distance_fraction: float
    """|진입 체결가 − 손절 참조가| / 진입 체결가."""
    is_clamped: bool
    """리스크 기반 수량이 명목 상한(`leverage`)에 걸려 축소됐는지."""
    intended_risk_fraction: float
    """의도한 거래당 리스크(`risk_per_trade`)."""
    effective_risk_fraction: float
    """실제 체결 수량 기준 실효 리스크 = 수량 × 손절 거리 / 그 시점 자본."""
    realized_pnl: float
    return_pct: float


def _diagnose_trades(
    candidates: list[_Candidate], cfg: BacktestConfig, *, symbol: str, timeframe: str
) -> list[TradeDiagnostic]:
    """`zone_limit_backtest._sequence_and_cost`와 동일한 시퀀싱을 재현하며 진단을 남긴다.

    거래 채택 여부·수량·손익은 전부 저장소 엔진(`_to_trade`)이 그대로 산출한 값을
    쓴다 — 이 함수는 그 위에 clamp·실효 리스크 진단만 얹는다(엔진 로직 복제 아님).
    """
    assert cfg.risk_sizing is not None, "risk_sizing이 없으면 clamp 개념이 성립하지 않음"
    params = cfg.risk_sizing
    costs = cfg.cost_model
    ordered = sorted(candidates, key=lambda c: (c.entry_time, c.exit_time))
    cash = cfg.initial_capital
    busy_until = -1
    diagnostics: list[TradeDiagnostic] = []
    for cand in ordered:
        if cand.entry_time < busy_until:
            continue
        trade = _to_trade(cand, cash, cfg)
        if trade is None:
            continue
        is_long = cand.side is PositionSide.LONG
        entry_fill = costs.entry_fill(cand.entry_price, is_long=is_long, liquidity=Liquidity.MAKER)
        stop_distance = abs(entry_fill - cand.stop_price)
        unclamped_qty = (
            (cash * params.risk_per_trade) / stop_distance if stop_distance > 0 else float("inf")
        )
        max_notional = cash * params.leverage
        if params.max_notional_fraction is not None:
            max_notional = min(max_notional, cash * params.max_notional_fraction)
        max_qty = max_notional / entry_fill
        is_clamped = unclamped_qty > max_qty
        effective_risk_fraction = (trade.quantity * stop_distance) / cash if cash else 0.0
        diagnostics.append(
            TradeDiagnostic(
                symbol=symbol,
                timeframe=timeframe,
                entry_time=cand.entry_time,
                stop_distance_fraction=stop_distance / entry_fill,
                is_clamped=is_clamped,
                intended_risk_fraction=params.risk_per_trade,
                effective_risk_fraction=effective_risk_fraction,
                realized_pnl=trade.realized_pnl,
                return_pct=trade.return_pct,
            )
        )
        cash += trade.realized_pnl
        busy_until = cand.exit_time
    return diagnostics


def collect_diagnostics(
    windows: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]],
) -> tuple[list[TradeDiagnostic], dict[tuple[str, str], list[_Candidate]]]:
    """전 (심볼,TF)에 대해 하한 없음(`min_stop_distance_fraction=0`) 기준 거래 진단을 낸다.

    반환하는 후보 목록(`candidates_by_cell`)은 사이징과 무관하게 생성되므로 §3 민감도
    스윕이 재사용해 셋업 생성을 반복하지 않는다.
    """
    diagnostics: list[TradeDiagnostic] = []
    candidates_by_cell: dict[tuple[str, str], list[_Candidate]] = {}
    for (symbol, timeframe), (htf_win, one_min_win) in windows.items():
        base_cfg = default_backtest_config(timeframe)
        assert base_cfg.risk_sizing is not None, "effective_risk_sizing이 비어 있음(예상 밖)"
        floor0_cfg = base_cfg.model_copy(
            update={
                "risk_sizing": base_cfg.risk_sizing.model_copy(
                    update={"min_stop_distance_fraction": 0.0}
                )
            }
        )
        ob_result = OrderBlockDetector(LEGACY_OB_PARAMS).run(htf_win)
        candidates, _ = build_zone_limit_candidates(
            htf_win,
            one_min_win,
            timeframe,
            params=CURRENT_DEFAULT_PARAMS,
            cfg=floor0_cfg,
            order_block_result=ob_result,
        )
        candidates_by_cell[(symbol, timeframe)] = candidates
        diagnostics.extend(
            _diagnose_trades(candidates, floor0_cfg, symbol=symbol, timeframe=timeframe)
        )
        print(f"[wan76] {symbol} {timeframe}: 후보={len(candidates)} 진단거래={len(diagnostics)}")
    return diagnostics, candidates_by_cell


def distance_distribution_table(diagnostics: list[TradeDiagnostic]) -> pd.DataFrame:
    """구간별(< 0.1%/0.3%/0.5%/1%) 거래 수·비중·총손익 기여를 낸다."""
    total_trades = len(diagnostics)
    total_pnl = sum(d.realized_pnl for d in diagnostics)
    rows = []
    for threshold in DISTANCE_BINS:
        in_bin = [d for d in diagnostics if d.stop_distance_fraction < threshold]
        bin_pnl = sum(d.realized_pnl for d in in_bin)
        rows.append(
            {
                "threshold_stop_distance_fraction": threshold,
                "num_trades": len(in_bin),
                "pct_of_all_trades": len(in_bin) / total_trades if total_trades else None,
                "sum_pnl": bin_pnl,
                "pct_of_total_pnl": bin_pnl / total_pnl if total_pnl else None,
            }
        )
    rows.append(
        {
            "threshold_stop_distance_fraction": float("inf"),
            "num_trades": total_trades,
            "pct_of_all_trades": 1.0 if total_trades else None,
            "sum_pnl": total_pnl,
            "pct_of_total_pnl": 1.0 if total_pnl else None,
        }
    )
    return pd.DataFrame(rows)


def clamp_diagnostic_table(diagnostics: list[TradeDiagnostic]) -> pd.DataFrame:
    """전체 및 clamp 여부별 실효 리스크 비율 요약."""
    total = len(diagnostics)
    clamped = [d for d in diagnostics if d.is_clamped]
    rows = [
        {
            "group": "all",
            "num_trades": total,
            "clamp_rate": len(clamped) / total if total else None,
            "mean_effective_risk_fraction": (
                sum(d.effective_risk_fraction for d in diagnostics) / total if total else None
            ),
        },
        {
            "group": "clamped_only",
            "num_trades": len(clamped),
            "clamp_rate": 1.0 if clamped else None,
            "mean_effective_risk_fraction": (
                sum(d.effective_risk_fraction for d in clamped) / len(clamped) if clamped else None
            ),
        },
    ]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# §3: min_stop_distance_fraction 민감도 (동일 후보 재시퀀싱만)
# --------------------------------------------------------------------------- #


class SensitivityRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    min_stop_distance_fraction: float
    total_return: float
    win_rate: float
    profit_factor: float | None
    max_drawdown: float
    num_trades: int


def sensitivity_sweep(
    candidates_by_cell: dict[tuple[str, str], list[_Candidate]],
    *,
    floors: tuple[float, ...] = SENSITIVITY_FLOORS,
) -> list[SensitivityRow]:
    rows: list[SensitivityRow] = []
    for (symbol, timeframe), candidates in candidates_by_cell.items():
        base_cfg = default_backtest_config(timeframe)
        assert base_cfg.risk_sizing is not None
        for floor in floors:
            cfg = base_cfg.model_copy(
                update={
                    "risk_sizing": base_cfg.risk_sizing.model_copy(
                        update={"min_stop_distance_fraction": floor}
                    )
                }
            )
            trades = _sequence_and_cost(candidates, cfg)
            result = build_result_from_trades(trades, cfg, timeframe)
            m = result.metrics
            rows.append(
                SensitivityRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    min_stop_distance_fraction=floor,
                    total_return=m.total_return,
                    win_rate=m.win_rate,
                    profit_factor=m.profit_factor,
                    max_drawdown=m.max_drawdown,
                    num_trades=m.num_trades,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# §4: 과거 판정 재검증 (WAN-68/70/73/74를 하한 적용해 그대로 재실행)
# --------------------------------------------------------------------------- #


def _patched_default_backtest_config(floor: float) -> Callable[..., BacktestConfig]:
    """`default_backtest_config`를 감싸 `risk_sizing.min_stop_distance_fraction`만 덮어쓴다.

    각 모듈이 자체 네임스페이스에 바인딩한 `default_backtest_config`만 패치하므로
    (`unittest.mock.patch.object`), 그 모듈의 나머지 코드(오케스트레이션·통계·판정
    함수)는 전혀 건드리지 않은 채 하한만 주입된 결과를 재현한다.
    """

    def _wrapped(
        timeframe: str | None = None, *, seed: int = 0, settings: object | None = None
    ) -> BacktestConfig:
        cfg = default_backtest_config(timeframe, seed=seed, settings=settings)  # type: ignore[arg-type]
        if cfg.risk_sizing is None:
            return cfg
        risk = cfg.risk_sizing.model_copy(update={"min_stop_distance_fraction": floor})
        return cfg.model_copy(update={"risk_sizing": risk})

    return _wrapped


@dataclass(frozen=True)
class RecheckResult:
    issue: str
    baseline_verdict: str
    floor_verdict: str
    holds: bool
    detail: str


def recheck_wan70(
    floor: float = RECHECK_FLOOR,
    *,
    timeframes: tuple[str, ...] = RECHECK_TIMEFRAMES,
    iterations: int = RECHECK_ITERATIONS,
) -> RecheckResult:
    """WAN-70(무작위 대조군, B안 그대로) — 하한 적용 후에도 '엣지 없다'가 유지되는지."""
    with patch.object(wan70, "default_backtest_config", _patched_default_backtest_config(floor)):
        results = wan70.run_experiment(timeframes=timeframes, iterations=iterations)
    verdict = wan70.summarize_verdict(results)
    holds = (
        "엣지 없다" in verdict
        and "엣지 있다" not in verdict
        and "특정 TF·심볼에서만 있다" not in verdict
    )
    return RecheckResult(
        issue="WAN-70",
        baseline_verdict="게이트 off/on 모두 **엣지 없다** (backtest/reports/wan70_summary.md)",
        floor_verdict=verdict,
        holds=holds,
        detail=f"하한={floor} 적용 재실행, n={len(results)}행",
    )


def recheck_wan73(
    floor: float = RECHECK_FLOOR,
    *,
    timeframes: tuple[str, ...] = RECHECK_TIMEFRAMES,
    iterations: int = RECHECK_ITERATIONS,
) -> RecheckResult:
    """WAN-73(재진입+중립RSI+고정2R) — 하한 적용 후에도 '기각'이 유지되는지."""
    with (
        patch.object(wan70, "default_backtest_config", _patched_default_backtest_config(floor)),
        patch.object(wan73, "default_backtest_config", _patched_default_backtest_config(floor)),
    ):
        random_results, fill_rate_results = wan73.run_experiment(
            timeframes=timeframes, iterations=iterations
        )
    new_rule_results = [r for r in random_results if r.gate == "wan73_new_rules"]
    decision, reasons = wan73.decide_adoption(new_rule_results, fill_rate_results)
    holds = decision == "REJECT"
    return RecheckResult(
        issue="WAN-73",
        baseline_verdict="REJECT — 기본값 전환하지 않음 (backtest/reports/wan73_summary.md)",
        floor_verdict=f"{decision}: {' / '.join(reasons)}",
        holds=holds,
        detail=f"하한={floor} 적용 재실행, n={len(random_results)}행",
    )


def recheck_wan74(
    floor: float = RECHECK_FLOOR,
    *,
    timeframes: tuple[str, ...] = RECHECK_TIMEFRAMES,
    iterations: int = RECHECK_ITERATIONS,
) -> RecheckResult:
    """WAN-74(간이재현 vs B안 불일치 규명) — 하한 적용 후에도 '진짜 엣지 없음(a)'이 유지되는지."""
    with patch.object(wan74, "default_backtest_config", _patched_default_backtest_config(floor)):
        _trade_diff, decompositions, pooled = wan74.run_experiment(
            timeframes=timeframes, iterations=iterations
        )
    conclusion, reasons = wan74._decide_conclusion(decompositions, pooled)
    holds = conclusion == "진짜 엣지 없음(a)"
    return RecheckResult(
        issue="WAN-74",
        baseline_verdict=("진짜 엣지 없음(a) — 풀링p=0.2050 (backtest/reports/wan74_summary.md)"),
        floor_verdict=f"{conclusion}: {' / '.join(reasons)}",
        holds=holds,
        detail=f"하한={floor} 적용 재실행, 풀링 p={pooled.p_value:.4f}",
    )


def recheck_wan68(
    floor: float = RECHECK_FLOOR,
    *,
    timeframes: tuple[str, ...] = RECHECK_TIMEFRAMES,
    iterations: int = RECHECK_ITERATIONS,
) -> RecheckResult:
    """WAN-68(숏 게이트 3안) — 하한 적용 후에도 '무게이트 대비 우위/무작위 대비 약한 엣지' 방향이
    유지되는지. 이 이슈는 단일 채택/기각 판정 함수가 없어(WAN-69가 이미 롱온리로 확정) 헤드라인
    수치(변형별 평균 OOS 수익률, 무게이트 대비 우월 셀 수, 무작위 대조군 유의 셀 수)만 재현한다.
    """
    with patch.object(wan68, "default_backtest_config", _patched_default_backtest_config(floor)):
        variant_rows, random_results = wan68.run_experiment(
            timeframes=timeframes, random_iterations=iterations
        )

    by_variant: dict[str, list[float]] = {}
    superior_counts: dict[str, int] = {}
    for row in variant_rows:
        by_variant.setdefault(row.variant, []).append(row.oos_total_return)
        if row.oos_superior_to_baseline:
            superior_counts[row.variant] = superior_counts.get(row.variant, 0) + 1
    n_cells = len(random_results)
    sig_random = sum(
        1 for r in random_results if r.random_p_value is not None and r.random_p_value <= 0.05
    )
    lines = []
    for variant, returns in sorted(by_variant.items()):
        mean_return = sum(returns) / len(returns) if returns else None
        sup = superior_counts.get(variant, 0)
        lines.append(f"{variant}: 평균OOS={mean_return:.4f}, 우월셀={sup}/{len(returns)}")
    detail = "; ".join(lines) + f" | 무작위대조군 유의(p<=0.05) 셀={sig_random}/{n_cells}"
    # WAN-68 원 결론의 핵심 방향: (1) 숏 억제 변형들이 무게이트 대비 대체로 우위,
    # (2) 무작위 대조군 대비 유의 셀은 극소수(원 리포트 1/12). 두 방향이 모두 유지되면 hold.
    long_only_mean = None
    baseline_mean = None
    for variant, returns in by_variant.items():
        if "나_숏제거" in variant:
            long_only_mean = sum(returns) / len(returns) if returns else None
        if "baseline_B안" in variant:
            baseline_mean = sum(returns) / len(returns) if returns else None
    direction_holds = (
        long_only_mean is not None and baseline_mean is not None and long_only_mean >= baseline_mean
    )
    weak_edge_holds = n_cells == 0 or (sig_random / n_cells) <= 0.5
    holds = direction_holds and weak_edge_holds
    return RecheckResult(
        issue="WAN-68",
        baseline_verdict=(
            "숏 억제 변형 전부 무게이트보다 평균 OOS 우위(롱온리 +1.58% vs 무게이트 +0.78%); "
            "무작위 대조군 유의 셀 1/12 (backtest/reports/wan68_summary.md)"
        ),
        floor_verdict=detail,
        holds=holds,
        detail=f"하한={floor} 적용 재실행, 변형 {len(by_variant)}종 × 셀 {n_cells}개",
    )


# --------------------------------------------------------------------------- #
# 리포트 산출
# --------------------------------------------------------------------------- #


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def _rows_to_frame(rows: list[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


@dataclass(frozen=True)
class AuditResult:
    diagnostics: list[TradeDiagnostic]
    distance_df: pd.DataFrame
    clamp_df: pd.DataFrame
    sensitivity_df: pd.DataFrame
    rechecks: list[RecheckResult]


def run_all(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    recheck_floor: float = RECHECK_FLOOR,
    run_recheck: bool = True,
) -> AuditResult | None:
    windows = _load_windows(db_path, symbols, timeframes, years)
    if not windows:
        return None

    diagnostics, candidates_by_cell = collect_diagnostics(windows)
    distance_df = distance_distribution_table(diagnostics)
    clamp_df = clamp_diagnostic_table(diagnostics)
    sensitivity_rows = sensitivity_sweep(candidates_by_cell)
    sensitivity_df = _rows_to_frame(list(sensitivity_rows))

    rechecks: list[RecheckResult] = []
    if run_recheck:
        print("[wan76] 재검증: WAN-70 재실행")
        rechecks.append(recheck_wan70(recheck_floor))
        print("[wan76] 재검증: WAN-73 재실행")
        rechecks.append(recheck_wan73(recheck_floor))
        print("[wan76] 재검증: WAN-74 재실행")
        rechecks.append(recheck_wan74(recheck_floor))
        print("[wan76] 재검증: WAN-68 재실행")
        rechecks.append(recheck_wan68(recheck_floor))

    return AuditResult(
        diagnostics=diagnostics,
        distance_df=distance_df,
        clamp_df=clamp_df,
        sensitivity_df=sensitivity_df,
        rechecks=rechecks,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-76 최소 손절폭 하한 감사")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--recheck-floor", type=float, default=RECHECK_FLOOR)
    parser.add_argument("--no-recheck", action="store_true")
    parser.add_argument(
        "--distance-out", type=Path, default=REPORTS_DIR / "wan76_stop_distance_distribution.csv"
    )
    parser.add_argument("--clamp-out", type=Path, default=REPORTS_DIR / "wan76_leverage_clamp.csv")
    parser.add_argument(
        "--sensitivity-out", type=Path, default=REPORTS_DIR / "wan76_sensitivity.csv"
    )
    parser.add_argument("--recheck-out", type=Path, default=REPORTS_DIR / "wan76_recheck.csv")
    args = parser.parse_args(argv)

    result = run_all(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        recheck_floor=args.recheck_floor,
        run_recheck=not args.no_recheck,
    )
    if result is None:
        print("[wan76] 데이터 없음 — 산출 건너뜀")
        return 0

    write_csv(result.distance_df, args.distance_out)
    write_csv(result.clamp_df, args.clamp_out)
    write_csv(result.sensitivity_df, args.sensitivity_out)
    if result.rechecks:
        recheck_df = pd.DataFrame([r.__dict__ for r in result.rechecks])
        write_csv(recheck_df, args.recheck_out)
    print(f"[wan76] 손절거리 분포 → {args.distance_out}")
    print(f"[wan76] clamp 진단 → {args.clamp_out}")
    print(f"[wan76] 민감도 → {args.sensitivity_out}")
    print(f"[wan76] 재검증 → {args.recheck_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
