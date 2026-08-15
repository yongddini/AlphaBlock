"""WAN-276 — 손절 갭-체결 민감도: 시장가 슬리피지 α 스윕 + 지정가 미체결 → 5배 북 청산·MDD 스트레스.

## 배경 — 사용자 논의 (2026-08-09)

채택 북(cap_only 5배, WAN-213)은 못 박은 6년 창에서 **청산 0건 · MDD ~20%**였다. 그런데 그
수치는 **불가능한 체결 가정** 위 값이다: `backtest/substep.py`는 손절 발동 봉에서
`exit_price = active_stop`(손절가 **그 값**)으로 체결한다 — "지정가처럼 정확한 가격 +
시장가처럼 무조건 체결"을 공짜로 가정한 것이라, 급락 갭 날엔 현실에 없는 조합이다. 이 모듈은
그 가정을 두 축으로 보수화해 **5배 북이 최악 경계에서도 버티는지**를 잰다.

## 두 팔 (엔진 옵트인 · 기본값 불변)

* **팔 1 — 시장가 손절 슬리피지 α 스윕**: `exit_price = active_stop − α·(active_stop −
  봉저가)`(롱, 숏 대칭). **α ∈ {0, 0.25, 0.5, 1.0}** · α=0 = 현행(비트 재현) · α=1 = 봉 저가
  = 1분 해상도 안의 최악. α는 손절 후보의 **청산가만** 바꾸므로(진입·체결·청산시각 불변)
  후보를 한 번 생성해 `apply_stop_slippage`로 사후 변환한다 — `_Candidate.exit_extreme`(손절
  봉의 불리 극값)을 엔진이 실어 주므로 재시뮬 없이 α를 얹을 수 있다(비용 절약).
* **팔 2 — 지정가 손절 미체결**: 손절 봉이 손절가를 **갭 관통**하면 미체결로 두고 손절가
  복귀를 기다린다(안 돌아오면 데이터 종료까지 홀드). 청산 시각/홀드가 바뀌므로 α 사후
  변환처럼 싸게 파생되지 않아 `limit_stop_nonfill=True`로 후보를 **별도 생성**한다.

## 좌표 (WAN-213 채택 북 · 재진입 없음)

* 채택 북 = `LeverageBookParams()`(cap_only 5배) · 9종목 · 못 박은 6년 창 · 신규 3종목 펀딩
  대리(WAN-180) · 재진입 없음(WAN-213 기준 — WAN-273 재진입은 직교 축이라 이 스트레스에서
  뺀다). 구간 = `full`(6년 MDD가 사는 곳) · `oos_warm`(주) · `oos`(스트레스), WAN-166 규약.
* **헤드라인**: 청산 트리거 건수(α=0에서 0 → **α=1·팔 2에서도 0인가**) · MDD(20% → ?) ·
  최대 동시 리스크 · 총수익. 보조: 대표 급락일 손절 실현손실이 α에 따라 벌어지는 폭.

## ⚠️ 경고 (그대로 옮길 것)

* **1분봉 경계이지 진값이 아니다** — α=1도 "1분 해상도 상한"이고 호가 깊이는 1분봉이
  과소평가한다. 진값은 틱·호가(WAN-98, Canceled) 소관.
* **진입 쪽 "닿으면 체결" 낙관(WAN-96)은 별개 축** — 이 모듈은 **청산(exit)** 쪽만 잰다.
  전부 `baseline` 렌즈 위 값이다.
* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 위험의 모양(청산·MDD)을 재는
  것이지 수익 엣지가 아니다. 총수익%는 복리 착시(WAN-169/213).
* **채택은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지.**

## 재현

```
uv run python -m backtest.wan276_stop_gap_fill --tf 1h --jobs 6            # 1h 스코프
uv run python -m backtest.wan276_stop_gap_fill --tf 15m --jobs 3 --append  # 15m 이어붙임(무겁다)
uv run python -m backtest.wan276_stop_gap_fill --from-csv                  # 요약만 재생성
```

⚠️ 15m·6년은 셀당 ~37분(봉내 라이브 밴드 `value()` 고유 비용, WAN-203)이라 무겁다 —
가벼운 TF부터 돌리고 15m은 `--append`로 이어붙인다(WAN-262/267 패턴).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_FULL, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.leverage_book import BookCell, LeverageBookParams, run_leverage_book
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    _scope_payloads,
    _segment_cells,
    run_cells,
)
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_END as NEW_END
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_START as NEW_START
from backtest.wan176_nine_symbol_rebaseline import NINE_SYMBOLS
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    sequence_with_candidates,
)

REPORTS_DIR = Path("backtest/reports")
DEFAULT_GRID_CSV = REPORTS_DIR / "wan276_stop_gap_grid.csv"
DEFAULT_CRASH_CSV = REPORTS_DIR / "wan276_crash_days.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan276_stop_gap_summary.md"
DEFAULT_META_JSON = REPORTS_DIR / "wan276_run_meta.json"

#: 팔 1 α 스윕. 0 = 현행(비트 재현) · 1 = 봉 저가 = 1분 해상도 안의 최악.
ALPHAS: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0)

#: 팔 2 라벨(지정가 손절 미체결).
NONFILL_LABEL = "limit_nonfill"

#: 채택 북(WAN-213) — cap_only 5배. `LeverageBookParams()` 기본값이 곧 채택 북이다.
ADOPTED_BOOK = LeverageBookParams()

#: 구간 — `full`에 6년 MDD가 살고, `oos_warm`(주)·`oos`(스트레스)는 WAN-166 규약.
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 대표 급락일(UTC 달력일 창) — 창(2020-07~2026-07)의 알려진 큰 하락일이다. **대표이지
#: 완전한 목록이 아니다**(2025-10·2026-02 등 −20%대 급락일은 이슈가 언급만 하고 정확한
#: 날짜를 못 박지 않았다). 손절 실현손실이 α에 따라 벌어지는 폭을 보는 보조 표에만 쓴다.
CRASH_DAYS: tuple[tuple[str, str], ...] = (
    ("2021-05-19", "2021-05-19 BTC 급락(~−30%)"),
    ("2022-05-12", "2022-05-12 LUNA 붕괴"),
    ("2022-06-13", "2022-06-13 디레버리징"),
    ("2022-11-09", "2022-11-09 FTX 붕괴"),
    ("2024-08-05", "2024-08-05 엔 캐리 청산"),
)

_DAY_MS = 86_400_000


def _day_ms(date_str: str) -> int:
    """`YYYY-MM-DD`(UTC 00:00)의 epoch ms."""
    stamp = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(stamp.timestamp() * 1000)


# --------------------------------------------------------------------------- #
# 팔 1 — α 사후 변환 (손절 후보의 청산가만 바꾼다)
# --------------------------------------------------------------------------- #


def slip_candidate(cand: _Candidate, alpha: float) -> _Candidate:
    """손절 후보의 청산가에 시장가 슬리피지 α를 얹는다(WAN-276 팔 1).

    `exit_price = active_stop − α·(active_stop − 봉저가)`(롱, 숏 대칭). 손절이 아닌 후보
    (익절·데이터종료)나 극값이 없는 후보(구엔진 CSV 재적재 등)는 그대로 둔다. α=0이면
    항등이라 `exit_price`가 불변이다 — `simulate_zone_limit_trade(stop_slippage_alpha=α)`가
    직접 내는 값과 같다(회귀 테스트가 고정)."""
    if cand.reason is not ExitReason.STOP_LOSS or cand.exit_extreme is None:
        return cand
    stop = cand.stop_price
    extreme = cand.exit_extreme
    if cand.side is PositionSide.LONG:
        new_exit = stop - alpha * (stop - extreme)
    else:
        new_exit = stop + alpha * (extreme - stop)
    return dataclasses.replace(cand, exit_price=new_exit)


def apply_stop_slippage(payloads: Sequence[CellPayload], alpha: float) -> list[CellPayload]:
    """전 칸의 후보에 α를 얹은 새 payload 목록. α=0이면 원본을 그대로 돌려준다(항등).

    후보의 청산가만 바뀌고 진입·체결·펀딩·경계는 불변이라, 후보를 한 번 생성해 α마다
    이 변환으로 싸게 파생한다(재시뮬 불필요)."""
    if alpha == 0.0:
        return list(payloads)
    out: list[CellPayload] = []
    for p in payloads:
        new_candidates = {
            seg: tuple(slip_candidate(c, alpha) for c in cands)
            for seg, cands in p.candidates.items()
        }
        # 재진입 후보(WAN-261/273, 옵트인)도 같은 α를 받는다 — 재진입 손절도 갭 체결을
        # 겪는다(WAN-277). 재진입이 없는 payload(빈 dict = WAN-276 재진입 OFF)는 빈 dict로
        # 남아 비트 재현된다.
        new_reentry = {
            seg: tuple(slip_candidate(c, alpha) for c in cands)
            for seg, cands in p.reentry_candidates.items()
        }
        out.append(
            dataclasses.replace(p, candidates=new_candidates, reentry_candidates=new_reentry)
        )
    return out


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class StressRow(BaseModel):
    """한 (시나리오 × 스코프 × 구간)의 채택 북 성과·위험."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    """`alpha_0.00`~`alpha_1.00`(팔 1) · `limit_nonfill`(팔 2)."""
    scope: str
    """`15m`/`1h`/`4h` 단일 TF 또는 `both`(공유 지갑)."""
    segment: str
    num_cells: int
    num_symbols: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    peak_concurrency: int
    max_concurrent_risk: float
    max_open_notional_ratio: float
    liquidation_events: int

    @property
    def return_over_mdd(self) -> float | None:
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown


class CrashRow(BaseModel):
    """한 시나리오의 대표 급락일 손절 실현손실 — α에 따라 벌어지는 폭(보조 표).

    격리(단일 포지션) 시퀀싱의 손절 거래 중 청산이 급락일 창에 든 것만 모은다. `mean_pct`·
    `worst_pct`는 거래당 실현수익률(비복리)이라 사이징·복리와 무관해 α 민감도를 깨끗이 본다."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    num_stop_trades: int
    """급락일 창에서 청산된 손절 거래 수(전 칸·full 구간 격리)."""
    mean_pct: float
    """그 손절 거래들의 평균 실현수익률(음수 = 손실)."""
    worst_pct: float
    """가장 큰 단일 손실(실현수익률 최소)."""
    total_pct: float
    """실현수익률 합 — α가 커질수록 더 음(-)으로 벌어진다."""


# --------------------------------------------------------------------------- #
# 격자 빌드
# --------------------------------------------------------------------------- #


def _book_row(
    cells: Sequence[BookCell],
    scenario: str,
    scope: str,
    segment: str,
    base_cfg: BacktestConfig,
) -> StressRow:
    outcome = run_leverage_book(cells, base_cfg, ADOPTED_BOOK)
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    m = result.metrics
    stats = outcome.stats
    return StressRow(
        scenario=scenario,
        scope=scope,
        segment=segment,
        num_cells=len(cells),
        num_symbols=len({c.symbol for c in cells}),
        num_trades=m.num_trades,
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        peak_concurrency=stats.peak_concurrency,
        max_concurrent_risk=stats.max_concurrent_risk_ratio,
        max_open_notional_ratio=stats.max_open_notional_ratio,
        liquidation_events=len(stats.liquidations),
    )


def build_grid(
    base_payloads: Sequence[CellPayload],
    nonfill_payloads: Sequence[CellPayload],
    scopes: Sequence[str],
    *,
    include_reentry: bool = False,
) -> list[StressRow]:
    """시나리오(α 스윕 + 미체결) × 스코프 × 구간의 채택 북 스트레스 행.

    `include_reentry`(WAN-277, 옵트인)를 켜면 payload에 실린 재진입 후보(WAN-273 채택 band)를
    base와 함께 북에 넣는다 — 끄면(WAN-276 기본) base만 넣어 비트 재현된다."""
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    rows: list[StressRow] = []
    for scope in scopes:
        base_scope = _scope_payloads(base_payloads, scope)
        nonfill_scope = _scope_payloads(nonfill_payloads, scope)
        if not base_scope:
            continue
        for alpha in ALPHAS:
            slipped = apply_stop_slippage(base_scope, alpha)
            for segment in SEGMENTS:
                cells = _segment_cells(slipped, segment, "", include_reentry=include_reentry)
                rows.append(_book_row(cells, f"alpha_{alpha:.2f}", scope, segment, base_cfg))
        if nonfill_scope:
            for segment in SEGMENTS:
                cells = _segment_cells(nonfill_scope, segment, "", include_reentry=include_reentry)
                rows.append(_book_row(cells, NONFILL_LABEL, scope, segment, base_cfg))
    return rows


def _crash_stats(payloads: Sequence[CellPayload], scenario: str) -> CrashRow:
    """전 칸 full 구간의 격리 손절 거래 중 급락일 창에 청산된 것들의 실현손실 집계."""
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    windows = [(_day_ms(d), _day_ms(d) + _DAY_MS) for d, _ in CRASH_DAYS]
    pcts: list[float] = []
    for payload in payloads:
        cfg = harness.build_config(payload.timeframe)
        cands = list(payload.candidates[SEGMENT_FULL])
        for cand, trade in sequence_with_candidates(cands, cfg, payload.funding[SEGMENT_FULL]):
            if cand.reason is not ExitReason.STOP_LOSS:
                continue
            if any(lo <= trade.exit_time < hi for lo, hi in windows):
                pcts.append(trade.return_pct)
    _ = base_cfg  # 시퀀싱은 TF별 cfg를 쓴다(연율화만 다르고 손익은 무관).
    if not pcts:
        return CrashRow(
            scenario=scenario, num_stop_trades=0, mean_pct=0.0, worst_pct=0.0, total_pct=0.0
        )
    return CrashRow(
        scenario=scenario,
        num_stop_trades=len(pcts),
        mean_pct=sum(pcts) / len(pcts),
        worst_pct=min(pcts),
        total_pct=sum(pcts),
    )


def build_crash_rows(
    base_payloads: Sequence[CellPayload],
    nonfill_payloads: Sequence[CellPayload],
) -> list[CrashRow]:
    """시나리오별 대표 급락일 손절 실현손실 — α 스윕 + 미체결."""
    rows: list[CrashRow] = []
    for alpha in ALPHAS:
        slipped = apply_stop_slippage(base_payloads, alpha)
        rows.append(_crash_stats(slipped, f"alpha_{alpha:.2f}"))
    if nonfill_payloads:
        rows.append(_crash_stats(nonfill_payloads, NONFILL_LABEL))
    return rows


# --------------------------------------------------------------------------- #
# 검산 — α=0 사후 변환이 원본 북과 항등인가
# --------------------------------------------------------------------------- #


def verify_alpha0_identity(
    base_payloads: Sequence[CellPayload],
    scopes: Sequence[str],
    *,
    include_reentry: bool = False,
) -> float:
    """α=0 사후 변환 북이 원본 후보 북과 **비트 일치**하는지 — 최대 절대 수익률 차.

    `apply_stop_slippage(·, 0.0)`은 항등이라 원본과 같은 후보를 낸다. 갈라지면 사후 변환이
    후보를 조용히 건드린 것이라 α 스윕 전체가 오염된다(회귀 테스트가 동작으로도 고정).

    `include_reentry`(WAN-277)를 켜면 재진입 ON 북에서 α=0 항등을 확인한다."""
    base_cfg = harness.build_config(BOOK_ANNUALIZATION_TF)
    worst = 0.0
    for scope in scopes:
        scoped = _scope_payloads(base_payloads, scope)
        if not scoped:
            continue
        slipped = apply_stop_slippage(scoped, 0.0)
        for segment in SEGMENTS:
            a = _book_row(
                _segment_cells(scoped, segment, "", include_reentry=include_reentry),
                "base",
                scope,
                segment,
                base_cfg,
            )
            b = _book_row(
                _segment_cells(slipped, segment, "", include_reentry=include_reentry),
                "a0",
                scope,
                segment,
                base_cfg,
            )
            worst = max(worst, abs(a.total_return - b.total_return))
    return worst


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[StressRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(StressRow.model_fields))


def grid_from_csv(path: Path) -> list[StressRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [StressRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def crash_to_frame(rows: Sequence[CrashRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CrashRow.model_fields))


def crash_from_csv(path: Path) -> list[CrashRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [CrashRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _scenario_label(scenario: str) -> str:
    if scenario == NONFILL_LABEL:
        return "팔2 지정가 미체결"
    return f"팔1 α={scenario.removeprefix('alpha_')}"


def _stress_table(rows: Sequence[StressRow], scope: str, segment: str) -> list[str]:
    lines = [
        "| 시나리오 | 거래 | 승률 | 수익률 | MDD | 수익/MDD | 최대동시리스크 | 최대명목/자본 "
        "| 청산 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    picked = [r for r in rows if r.scope == scope and r.segment == segment]
    order = {f"alpha_{a:.2f}": i for i, a in enumerate(ALPHAS)}
    order[NONFILL_LABEL] = len(ALPHAS)
    for row in sorted(picked, key=lambda r: order.get(r.scenario, 99)):
        lines.append(
            f"| {_scenario_label(row.scenario)} | {row.num_trades} | {_pct(row.win_rate)} | "
            f"{_pct(row.total_return)} | {_pct(row.max_drawdown)} | {_rr(row.return_over_mdd)} | "
            f"{_pct(row.max_concurrent_risk)} | {row.max_open_notional_ratio:.2f} | "
            f"{row.liquidation_events} | {row.peak_concurrency} |"
        )
    return lines


def _crash_table(rows: Sequence[CrashRow]) -> list[str]:
    lines = [
        "| 시나리오 | 손절 거래 | 평균 손익% | 최악 단건% | 손익% 합 |",
        "| -- | --: | --: | --: | --: |",
    ]
    order = {f"alpha_{a:.2f}": i for i, a in enumerate(ALPHAS)}
    order[NONFILL_LABEL] = len(ALPHAS)
    for row in sorted(rows, key=lambda r: order.get(r.scenario, 99)):
        lines.append(
            f"| {_scenario_label(row.scenario)} | {row.num_stop_trades} | {_pct(row.mean_pct)} | "
            f"{_pct(row.worst_pct)} | {_pct(row.total_pct)} |"
        )
    return lines


def _verdict(rows: Sequence[StressRow]) -> list[str]:
    """판정 — 청산이 어디서든 나는가 · MDD가 α=1/미체결에서 얼마나 깊어지나."""
    lines: list[str] = []
    liq = [r for r in rows if r.liquidation_events > 0]
    if liq:
        worst = max(liq, key=lambda r: r.liquidation_events)
        lines.append(
            f"🚨 **청산이 발생한다** — {len(liq)}개 (시나리오×스코프×구간) 셀에서 청산 트리거가 "
            f"났다(최대 {worst.liquidation_events}건 @ {worst.scenario}·{worst.scope}·"
            f"{worst.segment}). 5배 북의 청산 0건은 손절 체결 가정에 기대고 있었다."
        )
    else:
        lines.append(
            "📌 **청산은 전 시나리오에서 0건이다** — α=1(봉 저가 최악 체결)·팔 2(지정가 미체결) "
            "에서도 채택 북의 최악 가정 청산 트리거가 나지 않았다(1분봉 경계 안에서). ⚠️ 이는 "
            "「5배가 안전」이 아니라 「1분봉 해상도의 갭 슬리피지가 이 사이즈(1% 리스크·명목 상한)"
            "를 청산 사거리로 밀지 못한다」는 뜻이다 — 호가 깊이(WAN-98)는 여전히 미측정."
        )

    # MDD 심화: 각 스코프·구간에서 α=0 대비 α=1·미체결 MDD.
    def _find(scope: str, segment: str, scenario: str) -> StressRow | None:
        for r in rows:
            if r.scope == scope and r.segment == segment and r.scenario == scenario:
                return r
        return None

    scopes = sorted({r.scope for r in rows})
    for scope in scopes:
        base = _find(scope, SEGMENT_FULL, "alpha_0.00")
        a1 = _find(scope, SEGMENT_FULL, "alpha_1.00")
        nf = _find(scope, SEGMENT_FULL, NONFILL_LABEL)
        if base is None:
            continue
        parts = [f"α=0 {_pct(base.max_drawdown)}"]
        if a1 is not None:
            parts.append(f"α=1 {_pct(a1.max_drawdown)}")
        if nf is not None:
            parts.append(f"미체결 {_pct(nf.max_drawdown)}")
        lines.append(f"**MDD 심화({scope}·full)**: " + " → ".join(parts) + ".")

    # 팔 2(지정가 미체결)가 α=0과 갈리는 셀이 하나라도 있나 — 없으면 1분봉에서 갭 관통이
    # 사실상 없다는 뜻(진짜 갭은 sub-1분 = WAN-98).
    def _diverges(r: StressRow, b: StressRow | None) -> bool:
        return b is not None and (
            r.total_return != b.total_return or r.max_drawdown != b.max_drawdown
        )

    nonfill = [r for r in rows if r.scenario == NONFILL_LABEL]
    diverged = [r for r in nonfill if _diverges(r, _find(r.scope, r.segment, "alpha_0.00"))]
    if nonfill and not diverged:
        lines.append(
            "📌 **팔 2(지정가 미체결)는 전 셀에서 α=0과 완전히 같다** — 1분 서브스텝 해상도에서 "
            "손절 봉이 손절가 **전체를 갭 관통**(봉 전체가 손절가 너머)하는 일이 사실상 없다"
            "(가격이 어느 1분봉 안에서 손절가를 스치며 정상 체결된다). 즉 이 축은 1분봉으로는 "
            "안 잡힌다 — 진짜 갭(1분 미만·거래정지)은 틱·호가(WAN-98) 소관이고, **15m으로 내려도 "
            "체결은 같은 1분봉이라 갈리지 않는다**."
        )
    elif diverged:
        lines.append(
            f"📌 **팔 2(지정가 미체결)가 {len(diverged)}개 셀에서 α=0과 갈린다** — 갭 관통 봉이 "
            "존재해 손절가 복귀 대기/홀드가 손익을 바꾼다(원자료 참조)."
        )
    return lines


def build_summary_markdown(
    grid_rows: Sequence[StressRow],
    crash_rows: Sequence[CrashRow],
    *,
    grid_csv: Path,
    crash_csv: Path,
    identity_diff: float | None = None,
    funding_note: str = "",
) -> str:
    scopes = sorted({r.scope for r in grid_rows})
    identity_line = (
        f"α=0 사후 변환 북이 원본 후보 북과 **비트 일치**(최대 수익률 차 {identity_diff:.2e})."
        if identity_diff is not None
        else "α=0 사후 변환 항등은 회귀 테스트가 고정한다(이 실행에선 재검산 생략)."
    )
    lines = [
        "# WAN-276 — 손절 갭-체결 민감도: 시장가 슬리피지 α 스윕 + 지정가 미체결 스트레스",
        "",
        "**성격** 측정 + 옵트인 엔진(손절 슬리피지·미체결) 전용 — **기본값·토대·손절폭 가드·"
        "`LeverageBookParams()` 불변**(`ALPHABLOCK_LIVE_TRADING=false` 유지). 채택 북(cap_only "
        f"5배, WAN-213) · 못 박은 창 **{NEW_START} ~ {NEW_END}**(WAN-175/176 6년) · 9종목 · "
        "재진입 없음(WAN-213 기준) · 렌즈 `baseline` 단독.",
        "",
        "**팔 1** 시장가 손절 슬리피지 `exit = active_stop − α·(active_stop − 봉저가)`, "
        "α ∈ {0, 0.25, 0.5, 1.0}(0 = 현행 · 1 = 봉 저가 = 1분 해상도 최악). **팔 2** 지정가 손절 "
        "미체결(갭 관통 봉은 미체결 → 손절가 복귀 봉에서 체결 · 안 돌아오면 데이터종료 홀드).",
        "",
        *([f"📌 {funding_note}", ""] if funding_note else []),
        f"재현: `uv run python -m backtest.wan276_stop_gap_fill --tf 1h --jobs 6` "
        f"(요약만: `--from-csv`). 원자료: `{grid_csv}`(격자) · `{crash_csv}`(급락일).",
        "",
        "## 0. 검산",
        "",
        f"- {identity_line}",
        "- α=0 손절 체결이 현행 엔진과 **비트 재현**되고(회귀 테스트 "
        "`test_stop_slippage_alpha_zero_is_bit_identical`), 팔 2가 실제로 갭 관통 봉을 미체결로 "
        "처리하는지 **동작으로 고정**된다(`test_limit_nonfill_gap_bar_holds_to_end`).",
        "- 손절 후보의 `exit_extreme`(봉 극값)로 얹은 α와 엔진 인자 `stop_slippage_alpha`가 같은 "
        "청산가를 낸다(`test_stop_slippage_alpha_matches_transform_formula`).",
        "",
    ]
    for scope in scopes:
        lines += [f"## 1. 채택 북 스트레스 — {scope}", ""]
        for seg, title in (
            (SEGMENT_FULL, "full (6년 MDD가 사는 구간)"),
            (SEGMENT_OOS_WARM, "oos_warm (주)"),
            (SEGMENT_OOS, "oos (차가운 스트레스)"),
        ):
            lines += [f"### {title}", "", *_stress_table(grid_rows, scope, seg), ""]
    lines += [
        "🚨 **총수익%는 수백~수천 거래의 복리 값이다 — 달성 가능 성과로 인용 금지**(WAN-169/213). "
        "결정에 실질적인 열은 **청산 트리거 · MDD · 최대 동시 리스크**다. **6년 MDD는 창이 "
        "2020-09 시작이라 2018·2020-03 폭락을 안 담은 바닥선**이다(WAN-213).",
        "",
        "## 2. 대표 급락일 손절 실현손실 — α에 따라 벌어지는 폭",
        "",
        *_crash_table(crash_rows),
        "",
        "대표 급락일: " + " · ".join(desc for _, desc in CRASH_DAYS) + ". ⚠️ **대표이지 완전한 "
        "목록이 아니다** — 격리(단일 포지션) full 구간 손절 거래 중 그 창에 청산된 것만 모은 "
        "거래당 실현수익률이다(사이징·복리 무관, α 민감도만 본다). 팔 2는 갭 관통 후 손절가로 "
        "복귀하면 손절가 그대로 체결(슬리피지 없음)이라 급락일 손익이 α=0과 비슷하고, 안 돌아온 "
        "홀드는 데이터종료 청산이라 이 표(손절 거래)에 안 잡힌다.",
        "",
        "## 판정",
        "",
        *_verdict(grid_rows),
        "",
        "⚠️ **1분봉 경계이지 진값이 아니다** — α=1도 1분 해상도 상한이고 호가 깊이는 1분봉이 "
        "과소평가한다(우리 사이즈는 1% 리스크·명목 상한이라 작을 가능성이 크나 미확인). 진값은 "
        "틱·호가(WAN-98, Canceled) 소관. **진입 쪽 낙관(WAN-96)은 별개 축** — 이 표는 청산만 "
        "잰다, 전부 `baseline` 위 값. **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — "
        "위험의 모양을 재는 것이지 알파가 아니다(WAN-90). **채택은 재-베이스라인 = 사용자 결정.**",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _resolve_scopes(timeframes: Sequence[str], existing: Sequence[str]) -> list[str]:
    """이번 실행이 낼 스코프 — 요청 TF 각각 + (전 TF가 2개 이상 모이면) both."""
    tfs = list(dict.fromkeys([*existing, *timeframes]))
    scopes = list(timeframes)
    if len(tfs) >= 2:
        scopes.append("both")
    return scopes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-276 손절 갭-체결 민감도 스트레스")
    parser.add_argument("--symbols", type=str, default=",".join(NINE_SYMBOLS))
    parser.add_argument(
        "--tf", type=str, default="1h", help="쉼표 구분. 15m은 무겁다(후속 append)."
    )
    parser.add_argument("--start", type=str, default=NEW_START)
    parser.add_argument("--end", type=str, default=NEW_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 칸 단위 병렬 워커 수")
    parser.add_argument("--out-grid", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--out-crash", type=Path, default=DEFAULT_CRASH_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--out-meta", type=Path, default=DEFAULT_META_JSON)
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 격자 CSV에 이어붙인다(무거운 TF를 나눠 돌릴 때).",
    )
    parser.add_argument(
        "--from-csv", action="store_true", help="백테스트를 다시 돌리지 않고 CSV에서 요약만 재생성."
    )
    parser.add_argument(
        "--no-funding-proxy", action="store_true", help="신규 종목 펀딩 대리를 끈다."
    )
    parser.add_argument("--skip-verify", action="store_true", help="α=0 항등 재검산을 생략(속도).")
    args = parser.parse_args(argv)

    out_grid = Path(args.out_grid)
    out_crash = Path(args.out_crash)
    out_md = Path(args.out_md)
    out_meta = Path(args.out_meta)

    if args.from_csv:
        grid_rows = grid_from_csv(out_grid)
        crash_rows = crash_from_csv(out_crash) if out_crash.exists() else []
        funding_note = ""
        identity_diff = None
        if out_meta.exists():
            meta = json.loads(out_meta.read_text(encoding="utf-8"))
            funding_note = str(meta.get("funding_note", ""))
            identity_diff = meta.get("identity_diff")
        print(
            f"[wan276] CSV 로드 — 격자 {len(grid_rows)}행 · "
            f"급락일 {len(crash_rows)}행 (재실행 없음)"
        )
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        # WAN-305 명시 핀: wan276 CSV는 재진입 이전 북의 동결 스냅샷이다(재진입 판은 wan277).
        base_payloads = run_cells(
            symbols, timeframes, start=args.start, end=args.end, jobs=args.jobs, reentry=False
        )
        nonfill_payloads = run_cells(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            limit_stop_nonfill=True,
            reentry=False,
        )
        funding_note = ""
        if not args.no_funding_proxy:
            base_payloads, funding_note = apply_funding_proxy(base_payloads)
            nonfill_payloads, _ = apply_funding_proxy(nonfill_payloads)
            if funding_note:
                print(f"[wan276] {funding_note}")

        existing_rows: list[StressRow] = []
        existing_crash: list[CrashRow] = []
        if args.append and out_grid.exists():
            existing_rows = grid_from_csv(out_grid)
            existing_crash = crash_from_csv(out_crash) if out_crash.exists() else []
        existing_scopes = {r.scope for r in existing_rows}
        scopes = _resolve_scopes(timeframes, sorted(existing_scopes))
        # both 스코프는 append 실행에서 전 TF가 한 실행에 모여 있을 때만 낼 수 있다(공유 지갑).
        scopes = [s for s in scopes if s != "both" or len(timeframes) >= 2]

        identity_diff = None if args.skip_verify else verify_alpha0_identity(base_payloads, scopes)
        grid_rows = [*existing_rows, *build_grid(base_payloads, nonfill_payloads, scopes)]
        new_crash = build_crash_rows(base_payloads, nonfill_payloads)
        # 급락일 표는 스코프 무관(전 칸 격리)이라 append 시 새 실행 값으로 대체하되, 이전 실행
        # 것과 시나리오가 같으면 최신 것만 남긴다(TF를 나눠 돌리면 칸 집합이 달라 값이 다르다).
        crash_rows = _merge_crash(existing_crash, new_crash) if args.append else new_crash

        out_grid.parent.mkdir(parents=True, exist_ok=True)
        grid_to_frame(grid_rows).to_csv(out_grid, index=False)
        crash_to_frame(crash_rows).to_csv(out_crash, index=False)
        out_meta.write_text(
            json.dumps(
                {"funding_note": funding_note, "identity_diff": identity_diff},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[wan276] 격자 {len(grid_rows)}행 → {out_grid}")
        print(f"[wan276] 급락일 {len(crash_rows)}행 → {out_crash}")
        if identity_diff is not None:
            print(f"[wan276] α=0 항등 검산: 최대 수익률 차 {identity_diff:.2e}")
            if identity_diff >= 1e-9:
                print("[wan276] 🚨 검산 실패 — α=0 사후 변환이 원본 북과 갈라졌습니다.")
                return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            grid_rows,
            crash_rows,
            grid_csv=out_grid,
            crash_csv=out_crash,
            identity_diff=identity_diff,
            funding_note=funding_note,
        ),
        encoding="utf-8",
    )
    print(f"[wan276] summary → {out_md}")
    return 0


def _merge_crash(existing: Sequence[CrashRow], new: Sequence[CrashRow]) -> list[CrashRow]:
    """append 시 급락일 표를 합친다 — 같은 시나리오는 이전+새 칸 집합이 달라 단순 대체가
    아니라 거래 수·손익 합을 더한다(평균/최악은 다시 계산할 수 없어 근사로 합산 표기)."""
    by_scenario: dict[str, CrashRow] = {r.scenario: r for r in existing}
    out: list[CrashRow] = []
    order = {f"alpha_{a:.2f}": i for i, a in enumerate(ALPHAS)}
    order[NONFILL_LABEL] = len(ALPHAS)
    scenarios = sorted({*by_scenario, *(r.scenario for r in new)}, key=lambda s: order.get(s, 99))
    new_by = {r.scenario: r for r in new}
    for scenario in scenarios:
        old = by_scenario.get(scenario)
        fresh = new_by.get(scenario)
        if old is None:
            assert fresh is not None
            out.append(fresh)
        elif fresh is None:
            out.append(old)
        else:
            n = old.num_stop_trades + fresh.num_stop_trades
            total = old.total_pct + fresh.total_pct
            out.append(
                CrashRow(
                    scenario=scenario,
                    num_stop_trades=n,
                    mean_pct=total / n if n else 0.0,
                    worst_pct=min(old.worst_pct, fresh.worst_pct),
                    total_pct=total,
                )
            )
    return out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
