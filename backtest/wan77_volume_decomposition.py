"""거래량 성분 분해 — `ob_volume` 3축 × 분위별 성과, 단조성 판정, 조건부 OOS+매칭 널 (WAN-77).

배경은 이슈 WAN-77 본문 참고. `strategy/order_blocks.py`는 존 형성 시 `ob_volume`/
`ob_high_volume`/`ob_low_volume`을 계산해 저장하지만, 어떤 탐지·진입 판정에도 쓰이지
않는다(대시보드 표시와 `combine_obs` 합산에만 쓰임). 이 모듈은 **필터를 먼저 만들지
않고** 기존 백테스트 거래를 거래량 축으로 사후 분해해 "거래량이 실린 존과 실리지 않은
존의 성과가 갈리는가"를 먼저 기술통계로 본다(1단계). WAN-75가 겪은 "필터를 먼저 만들고
나중에 검정" 함정(다중검정 인공물)을 반복하지 않는다.

## 3축 정의

* **상대 거래량**(``relative_volume``) = ``ob_volume / (직전 20봉 거래량 이동평균 × 3)``.
  기준 창은 존 형성 3봉(``t-2..t``) **바로 이전** 20봉(``t-22..t-3``)이다. "평소 3봉 합
  대비 몇 배인가"를 나타낸다. 워밍업이 부족한 존(창을 채울 20봉이 없음)은 이 축에서
  제외한다(``None``).
* **거래량 분위수**(``volume_percentile``) = 이 거래가 진입한 (심볼, TF, 엔진) 셀 안에서
  **실제로 체결된 거래의 근거 존들** 사이의 ``ob_volume`` 순위 백분위(0~1). 탐지된 전체
  아카이브(체결 여부와 무관한 모든 존)가 아니라 **체결된 존 모집단**을 쓴다 — 병합존
  (``combine_obs=True``, 기본값)은 구성 존 ``ob_volume``의 합이라 원본 아카이브(비병합
  단위 보존, WAN-56)와 스케일이 달라 섞으면 척도가 어긋난다.
* **불균형도**(``imbalance``) = ``min(ob_high_volume, ob_low_volume) / max(...)``. 0에
  가까울수록 한쪽으로 쏠린(방향성 뚜렷) 존, 1에 가까울수록 매수/매도측 거래량이 균형.

## 엔진

* **B안**(``backtest.zone_limit_backtest``, 채택 전략) — 각 셋업의 근거 오더블록을
  ``_Candidate.order_block``(WAN-77이 추가한 조인 전용 필드, 체결·청산 로직에는 쓰이지
  않음)으로 직접 따라간다.
* **A안**(``backtest.engine.BacktestEngine``) — ``ConfluenceResult.order_block_signals``가
  이미 각 확정 진입에 ``order_block``을 실어 보내므로, 엔진이 낸 ``Trade``를 진입
  시각+방향으로 그 시그널에 역매칭한다. 동일 시각·동일 방향 시그널이 여럿이면 첫 매치를
  취한다(엔진의 진입 루프와 같은 순회 순서라 실사용 사례 대부분에서 정확하다).

## R 배수

저장소에 기존 "R" 정의가 없어, 리스크 기반 사이징(`execution.sizing.position_size`)이
전제하는 정의를 그대로 쓴다: 진입 시점 자본(직전까지 실현손익 반영) × `risk_per_trade`를
"계획된 손실 1R"로 보고, `realized_pnl / risk_amount`를 그 거래의 R 배수로 계산한다.

## 단조성 판정

각 (심볼, TF, 엔진, 축) 셀과, 축별 **전 심볼·TF 풀링**(상대거래량·불균형도는 이미 정규화
축이라 직접 풀링 가능, 거래량 분위수는 원래 셀 내부 순위라 풀링해도 척도가 흔들리지
않음) 양쪽에서 분위별 평균 R이 Q1→Q4로 단조 증가/감소/비단조인지 판정한다.

## 2단계(조건부) 발동 규칙

1단계 **풀링** 결과에서 어떤 (엔진=B, 축)이 "단조 증가"이고 Q4(최상위 분위) 표본이
`_STAGE2_MIN_TRADES`(20) 이상이면 그 축에 한해 2단계(OOS 분리 + 매칭 널)를 돌린다.
그 조건을 만족하는 축이 없으면 2단계를 생략하고 "거래량이 실린 오더블록조차 무작위와
구분되지 않는다"는 결론을 명시한다(3단계 구현도 하지 않는다).

재현: ``python -m backtest.wan77_volume_decomposition``.
"""

from __future__ import annotations

import argparse
import bisect
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import BacktestEngine
from backtest.harness import pin_band_bar
from backtest.models import BacktestConfig, PositionSide, Trade
from backtest.sweep import default_backtest_config
from backtest.wan68_short_gate_analysis import _split_bars
from backtest.wan70_random_control_b import _bucket_key, _segment_window
from backtest.zone_limit_backtest import (
    _Candidate,
    _sequence_and_cost,
    _to_trade,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from strategy.confluence import ConfluenceStrategy
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
    OrderBlockSignal,
)
from strategy.order_blocks import OrderBlockDetector

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
#: 이슈 본문이 지정한 TF 3종(15m 제외 — 다른 wan7x 리포트와 달리 이 분석은 여기 한정).
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "1d")
DEFAULT_YEARS: float = 3.0
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)

Engine = Literal["A", "B"]
Axis = Literal["relative_volume", "volume_percentile", "imbalance"]

_RELATIVE_VOLUME_WINDOW = 20
_N_QUANTILES = 4
_MIN_TRADES_FOR_VERDICT = 20
"""WAN-70과 동일 기준: 셀당 거래 20건 미만은 유의성 판정에서 제외."""
_STAGE2_MIN_TRADES = 20
"""2단계 발동 최소 표본(풀링 Q4). `_MIN_TRADES_FOR_VERDICT`와 같은 값, 별개 상수로 둔
이유는 발동 규칙과 판정 제외 기준이 개념적으로 다른 결정이기 때문."""

#: 이슈 본문이 지정한 손절폭 하한 민감도 스윕 대상(WAN-76 SENSITIVITY_FLOORS의 부분집합).
SENSITIVITY_FLOORS: tuple[float, ...] = (0.0, 0.005, 0.01)

#: 채택 전략(B안): 존-지정가 + 실시간 RSI. WAN-70/75/76과 동일 정의.
#: ⚠️ `band_bar`는 당시 값(`tap`)으로 **명시 고정**한다(WAN-132 기본값 전환).
B_ENGINE_PARAMS = pin_band_bar(ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime"))
#: A안 교차검증: 확정봉 종가 + closed_bar RSI. WAN-95로 저장소 기본값이 B안(지정가)로
#: 바뀐 뒤로는 기본값이 아니라 **명시 고정**이다.
A_ENGINE_PARAMS = ConfluenceParams(entry_mode="close", rsi_mode="closed_bar")

_BOOTSTRAP_ITERATIONS = 100


# --------------------------------------------------------------------------- #
# 공용 헬퍼: 프레임 준비 · B안 시퀀싱(오더블록·R 배수 동반) · 축 계산
# --------------------------------------------------------------------------- #


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """`OrderBlockDetector._prepare`와 동일한 준비(정렬+확정봉 필터). 존 확정 시각을
    바 인덱스로 되짚을 때 탐지기가 실제로 사용한 프레임과 같은 정렬을 보장한다."""
    frame = df
    if "closed" in df.columns:
        frame = frame[frame["closed"].astype(bool)]
    return frame.sort_values("open_time").reset_index(drop=True)


def _sequence_with_ob(
    candidates: list[_Candidate], cfg: BacktestConfig
) -> list[tuple[_Candidate, Trade, float | None]]:
    """`zone_limit_backtest._sequence_and_cost`와 동일한 시퀀싱이나, 원본 후보(오더블록
    포함)와 진입 시점 R 배수를 함께 반환한다(WAN-77 사후 조인 전용 — 프로덕션 시퀀싱
    로직 자체는 건드리지 않고, 같은 규칙을 여기 한 번 더 복제해 부가 정보를 뽑는다).
    """
    ordered = sorted(candidates, key=lambda c: (c.entry_time, c.exit_time))
    cash = cfg.initial_capital
    busy_until = -1
    paired: list[tuple[_Candidate, Trade, float | None]] = []
    for cand in ordered:
        if cand.entry_time < busy_until:
            continue
        trade = _to_trade(cand, cash, cfg)
        if trade is None:
            continue
        risk_amount = cash * cfg.risk_sizing.risk_per_trade if cfg.risk_sizing is not None else None
        r_multiple = trade.realized_pnl / risk_amount if risk_amount else None
        cash += trade.realized_pnl
        busy_until = cand.exit_time
        paired.append((cand, trade, r_multiple))
    return paired


def _relative_volume(
    times: list[int],
    volumes: list[float],
    confirmed_time: int,
    ob_volume: float,
    *,
    window: int = _RELATIVE_VOLUME_WINDOW,
) -> float | None:
    """`ob_volume / (직전 window봉 거래량 이동평균 × 3)`. 워밍업 부족이면 `None`."""
    pos = bisect.bisect_left(times, confirmed_time)
    window_end = pos - 2  # 존 형성 3봉(t-2..t) 이전까지.
    window_start = window_end - window
    if window_start < 0 or window_end <= window_start:
        return None
    baseline = sum(volumes[window_start:window_end]) / window
    if baseline <= 0:
        return None
    return ob_volume / (baseline * 3)


def _imbalance(ob: OrderBlock) -> float | None:
    lo, hi = ob.ob_low_volume, ob.ob_high_volume
    denom = max(lo, hi)
    if denom <= 0:
        return None
    return min(lo, hi) / denom


@dataclass(frozen=True)
class _JoinedTrade:
    """한 거래 + 근거 오더블록 + 파생 축 값(WAN-77 사후 조인 결과)."""

    trade: Trade
    order_block: OrderBlock
    r_multiple: float | None
    relative_volume: float | None
    imbalance: float | None
    volume_percentile: float | None


_AXES: dict[Axis, Callable[[_JoinedTrade], float | None]] = {
    "relative_volume": lambda jt: jt.relative_volume,
    "volume_percentile": lambda jt: jt.volume_percentile,
    "imbalance": lambda jt: jt.imbalance,
}


def _annotate_percentile(joined: list[_JoinedTrade]) -> list[_JoinedTrade]:
    """셀 내부(체결된 존 모집단) `ob_volume` 순위 백분위(0~1)를 매긴다."""
    if not joined:
        return joined
    volumes = [jt.order_block.ob_volume for jt in joined]
    order = sorted(range(len(volumes)), key=lambda i: volumes[i])
    n = len(volumes)
    percentiles = [0.0] * n
    for rank, idx in enumerate(order):
        percentiles[idx] = rank / (n - 1) if n > 1 else 0.5
    return [replace(jt, volume_percentile=percentiles[i]) for i, jt in enumerate(joined)]


# --------------------------------------------------------------------------- #
# 엔진별 거래-오더블록 조인
# --------------------------------------------------------------------------- #


def _build_b_engine_joined(
    htf_seg: pd.DataFrame,
    one_min_seg: pd.DataFrame,
    timeframe: str,
    cfg: BacktestConfig,
    params: ConfluenceParams,
    ob_result: OrderBlockResult,
) -> list[_JoinedTrade]:
    """B안(존-지정가+실시간 RSI) 체결 거래를 근거 오더블록에 조인한다."""
    candidates, _ = build_zone_limit_candidates(
        htf_seg, one_min_seg, timeframe, params=params, cfg=cfg, order_block_result=ob_result
    )
    frame = _prepare_frame(htf_seg)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    volumes = [float(v) for v in frame["volume"].astype(float).tolist()]

    joined: list[_JoinedTrade] = []
    for cand, trade, r_multiple in _sequence_with_ob(candidates, cfg):
        ob = cand.order_block
        if ob is None:
            continue
        joined.append(
            _JoinedTrade(
                trade=trade,
                order_block=ob,
                r_multiple=r_multiple,
                relative_volume=_relative_volume(times, volumes, ob.confirmed_time, ob.ob_volume),
                imbalance=_imbalance(ob),
                volume_percentile=None,
            )
        )
    return joined


def _build_a_engine_joined(
    htf_seg: pd.DataFrame,
    cfg: BacktestConfig,
    params: ConfluenceParams,
    ob_result: OrderBlockResult,
) -> list[_JoinedTrade]:
    """A안(확정봉+closed_bar RSI) 체결 거래를 근거 오더블록에 조인한다.

    `BacktestEngine`은 진입에 쓰인 시그널을 `Trade`에 남기지 않으므로, 엔진과 동일한
    (진입 시각, 방향)으로 `order_block_signals`를 역매칭한다. 이 셀 안에서 최대 1건의
    동시 포지션만 허용되므로(엔진 규칙), (시각, 방향) 조합은 사실상 유일하다.
    """
    frame = _prepare_frame(htf_seg)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    volumes = [float(v) for v in frame["volume"].astype(float).tolist()]

    strategy = ConfluenceStrategy(params, None)
    confluence = strategy.run(htf_seg, ob_result)
    signals = [s for s in confluence.order_block_signals if s.status == "active"]
    signals_by_time: dict[int, list[OrderBlockSignal]] = defaultdict(list)
    for sig in signals:
        signals_by_time[sig.trigger_time].append(sig)

    engine = BacktestEngine(cfg)
    result = engine.run(htf_seg, signals, None)

    joined: list[_JoinedTrade] = []
    cash = cfg.initial_capital
    for trade in result.trades:
        risk_amount = cash * cfg.risk_sizing.risk_per_trade if cfg.risk_sizing is not None else None
        r_multiple = trade.realized_pnl / risk_amount if risk_amount else None
        cash += trade.realized_pnl

        match: OrderBlockSignal | None = None
        for sig in signals_by_time.get(trade.entry_time, ()):
            side = (
                PositionSide.LONG
                if sig.direction is OrderBlockDirection.BULLISH
                else PositionSide.SHORT
            )
            if side is trade.side:
                match = sig
                break
        if match is None or match.order_block is None:
            continue  # 방어적 스킵 — 엔진이 쓴 시그널을 못 찾으면(이론상 없음) 조인 제외.
        ob = match.order_block
        joined.append(
            _JoinedTrade(
                trade=trade,
                order_block=ob,
                r_multiple=r_multiple,
                relative_volume=_relative_volume(times, volumes, ob.confirmed_time, ob.ob_volume),
                imbalance=_imbalance(ob),
                volume_percentile=None,
            )
        )
    return joined


# --------------------------------------------------------------------------- #
# 1단계: 분위별 성과표 + 단조성 판정
# --------------------------------------------------------------------------- #


class VolumeQuantileRow(BaseModel):
    """한 (심볼, TF, 엔진, 축, 분위) 셀의 성과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    engine: Engine
    axis: Axis
    quantile: str
    quantile_rank: int
    axis_min: float
    axis_max: float
    n: int
    win_rate: float
    avg_r: float | None
    profit_factor: float | None
    total_return_sum: float
    """이 분위에 속한 거래들의 `return_pct` 단순합. 부분집합은 실제 순차 백테스트가
    아니므로(다른 분위 거래와 시간이 교차) 복리 자본곡선이 아니라 단순합이다."""


def _quantile_rows(
    joined: list[_JoinedTrade],
    *,
    symbol: str,
    timeframe: str,
    engine: Engine,
    axis_name: Axis,
    n_quantiles: int = _N_QUANTILES,
) -> list[VolumeQuantileRow]:
    axis_fn = _AXES[axis_name]
    pairs = [(axis_fn(jt), jt) for jt in joined]
    valid = [(v, jt) for v, jt in pairs if v is not None]
    if len(valid) < n_quantiles:
        return []
    values = [v for v, _ in valid]
    try:
        labels = pd.qcut(values, n_quantiles, labels=False, duplicates="drop")
    except ValueError:
        return []

    buckets: dict[int, list[tuple[float, _JoinedTrade]]] = defaultdict(list)
    for label, (v, jt) in zip(labels, valid, strict=True):
        buckets[int(label)].append((v, jt))

    rows: list[VolumeQuantileRow] = []
    for label in sorted(buckets):
        items = buckets[label]
        trades = [jt.trade for _, jt in items]
        vals = [v for v, _ in items]
        r_values = [jt.r_multiple for _, jt in items if jt.r_multiple is not None]
        wins = sum(1 for t in trades if t.is_win)
        gains = sum(t.realized_pnl for t in trades if t.realized_pnl > 0)
        losses = -sum(t.realized_pnl for t in trades if t.realized_pnl < 0)
        rows.append(
            VolumeQuantileRow(
                symbol=symbol,
                timeframe=timeframe,
                engine=engine,
                axis=axis_name,
                quantile=f"Q{label + 1}",
                quantile_rank=label + 1,
                axis_min=min(vals),
                axis_max=max(vals),
                n=len(trades),
                win_rate=wins / len(trades),
                avg_r=(sum(r_values) / len(r_values)) if r_values else None,
                profit_factor=(gains / losses) if losses > 0 else None,
                total_return_sum=sum(t.return_pct for t in trades),
            )
        )
    return rows


def monotonicity_verdict(rows: list[VolumeQuantileRow]) -> str:
    """분위(오름차순) 평균 R 수열의 단조성을 판정한다."""
    ordered = sorted(rows, key=lambda r: r.quantile_rank)
    r_values = [r.avg_r for r in ordered if r.avg_r is not None]
    if len(r_values) < 3:
        return "판정 불가(표본 부족)"
    non_decreasing = all(b >= a - 1e-9 for a, b in zip(r_values, r_values[1:], strict=False))
    non_increasing = all(b <= a + 1e-9 for a, b in zip(r_values, r_values[1:], strict=False))
    if non_decreasing and non_increasing:
        return "평탄"
    if non_decreasing:
        return "단조 증가"
    if non_increasing:
        return "단조 감소"
    return "비단조(들쭉날쭉)"


def collect_cell_joined(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    timeframe: str,
    order_block_params: OrderBlockParams | None = None,
) -> dict[Engine, list[_JoinedTrade]]:
    """한 (심볼, TF)의 A/B 양쪽 엔진 조인 결과(백분위 주석 포함)."""
    cfg = default_backtest_config(timeframe)
    ob_result = OrderBlockDetector(order_block_params).run(htf_df)
    b_joined = _annotate_percentile(
        _build_b_engine_joined(htf_df, one_min_df, timeframe, cfg, B_ENGINE_PARAMS, ob_result)
    )
    a_joined = _annotate_percentile(_build_a_engine_joined(htf_df, cfg, A_ENGINE_PARAMS, ob_result))
    return {"B": b_joined, "A": a_joined}


def quantile_rows_for_cell(
    joined_by_engine: dict[Engine, list[_JoinedTrade]], *, symbol: str, timeframe: str
) -> list[VolumeQuantileRow]:
    rows: list[VolumeQuantileRow] = []
    for engine, joined in joined_by_engine.items():
        for axis_name in _AXES:
            rows.extend(
                _quantile_rows(
                    joined, symbol=symbol, timeframe=timeframe, engine=engine, axis_name=axis_name
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# min_stop_distance_fraction 민감도(WAN-76 스타일 재시퀀싱)
# --------------------------------------------------------------------------- #


class FloorSensitivityRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    min_stop_distance_fraction: float
    total_return: float
    num_trades: int
    win_rate: float
    profit_factor: float | None


def floor_sensitivity_rows(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    order_block_params: OrderBlockParams | None = None,
    floors: tuple[float, ...] = SENSITIVITY_FLOORS,
) -> list[FloorSensitivityRow]:
    """B안 무필터(baseline) 후보를 한 번만 생성하고, 하한별로 재시퀀싱만 반복한다."""
    cfg = default_backtest_config(timeframe)
    if cfg.risk_sizing is None:
        return []
    ob_result = OrderBlockDetector(order_block_params).run(htf_df)
    candidates, _ = build_zone_limit_candidates(
        htf_df, one_min_df, timeframe, params=B_ENGINE_PARAMS, cfg=cfg, order_block_result=ob_result
    )
    rows: list[FloorSensitivityRow] = []
    for floor in floors:
        floor_cfg = cfg.model_copy(
            update={
                "risk_sizing": cfg.risk_sizing.model_copy(
                    update={"min_stop_distance_fraction": floor}
                )
            }
        )
        trades = _sequence_and_cost(candidates, floor_cfg)
        result = build_result_from_trades(trades, floor_cfg, timeframe)
        rows.append(
            FloorSensitivityRow(
                symbol=symbol,
                timeframe=timeframe,
                min_stop_distance_fraction=floor,
                total_return=result.metrics.total_return,
                num_trades=result.metrics.num_trades,
                win_rate=result.metrics.win_rate,
                profit_factor=result.metrics.profit_factor,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 2단계(조건부): IS 임계값 → OOS 매칭 널
# --------------------------------------------------------------------------- #


class MatchedNullRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope: str
    """`"{심볼}/{TF}"` 또는 `"pooled"`."""
    axis: Axis
    threshold: float
    real_total_return: float
    real_num_trades: int
    pool_size: int
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int


def _candidate_axis_value(
    cand: _Candidate,
    axis_name: Axis,
    times: list[int],
    volumes: list[float],
    percentile_lookup: dict[int, float],
) -> float | None:
    ob = cand.order_block
    if ob is None:
        return None
    if axis_name == "relative_volume":
        return _relative_volume(times, volumes, ob.confirmed_time, ob.ob_volume)
    if axis_name == "imbalance":
        return _imbalance(ob)
    return percentile_lookup.get(id(cand))


def _percentile_lookup(candidates: list[_Candidate]) -> dict[int, float]:
    obs = [(id(c), c.order_block.ob_volume) for c in candidates if c.order_block is not None]
    order = sorted(range(len(obs)), key=lambda i: obs[i][1])
    n = len(obs)
    lookup: dict[int, float] = {}
    for rank, idx in enumerate(order):
        cand_id, _ = obs[idx]
        lookup[cand_id] = rank / (n - 1) if n > 1 else 0.5
    return lookup


def _matched_null_bootstrap(
    real_trades: list[Trade],
    pool_candidates: list[_Candidate],
    cfg: BacktestConfig,
    *,
    iterations: int,
    seed: int,
) -> tuple[float, float | None, float | None, float | None, float | None, int]:
    """`wan70_random_control_b`/`wan75`와 같은 (방향, 시각대 버킷) 매칭 부트스트랩.

    널 모집단은 무필터 B안 체결 후보 풀 전체이고, 실제(고거래량 부분집합)의 방향·
    시각대 구성·개수를 맞춰 재표본추출한다.
    """
    pool_by_bucket: dict[tuple[PositionSide, int], list[_Candidate]] = defaultdict(list)
    pool_by_side: dict[PositionSide, list[_Candidate]] = defaultdict(list)
    for cand in pool_candidates:
        pool_by_bucket[_bucket_key(cand.side, cand.entry_time)].append(cand)
        pool_by_side[cand.side].append(cand)

    target_counts: dict[tuple[PositionSide, int], int] = defaultdict(int)
    for trade in real_trades:
        target_counts[_bucket_key(trade.side, trade.entry_time)] += 1

    rng = random.Random(seed)
    random_returns: list[float] = []
    for _ in range(iterations):
        sampled: list[_Candidate] = []
        used_by_side: dict[PositionSide, set[int]] = defaultdict(set)
        for (side, bucket), count in target_counts.items():
            bucket_pool = pool_by_bucket.get((side, bucket), [])
            k = min(count, len(bucket_pool))
            picks = rng.sample(bucket_pool, k) if k else []
            sampled.extend(picks)
            used_by_side[side].update(id(c) for c in picks)
            shortfall = count - k
            if shortfall > 0:
                remaining = [
                    c for c in pool_by_side.get(side, []) if id(c) not in used_by_side[side]
                ]
                fill_k = min(shortfall, len(remaining))
                fill_picks = rng.sample(remaining, fill_k) if fill_k else []
                sampled.extend(fill_picks)
                used_by_side[side].update(id(c) for c in fill_picks)
        sampled_trades = _sequence_and_cost(sampled, cfg)
        random_returns.append(sum(t.return_pct for t in sampled_trades))

    real_return = sum(t.return_pct for t in real_trades)
    random_returns.sort()
    n = len(random_returns)
    if n == 0:
        return real_return, None, None, None, None, 0
    p_value = sum(1 for r in random_returns if r >= real_return) / n
    mean_return = sum(random_returns) / n
    ci_low = random_returns[int(0.025 * (n - 1))]
    ci_high = random_returns[int(0.975 * (n - 1))]
    return real_return, mean_return, ci_low, ci_high, p_value, n


def run_stage2_for_axis(
    windows: dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]],
    *,
    axis_name: Axis,
    order_block_params: OrderBlockParams | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = 77,
) -> list[MatchedNullRow]:
    """IS에서 축 상위 25% 임계값을 고르고, OOS에서 그 임계값을 넘는 거래만 남겨 매칭
    널과 비교한다. 셀별(심볼×TF) + 전체 풀링 둘 다 계산한다."""
    rows: list[MatchedNullRow] = []
    pooled_real_trades: list[Trade] = []
    pooled_pool_candidates: list[_Candidate] = []
    pooled_threshold_values: list[float] = []

    for (symbol, timeframe), (htf_df, one_min_df) in windows.items():
        frame = _prepare_frame(htf_df)
        n = len(frame)
        if n < 30:
            continue
        cfg = default_backtest_config(timeframe)
        is_end = _split_bars(n)
        warmup = min(is_end, max(60, n // 6))
        is_htf, is_1m = _segment_window(
            frame, one_min_df, timeframe, context_start=0, seg_start=0, seg_end=is_end
        )
        oos_htf, oos_1m = _segment_window(
            frame,
            one_min_df,
            timeframe,
            context_start=max(0, is_end - warmup),
            seg_start=is_end,
            seg_end=n,
        )
        if is_htf.empty or oos_htf.empty or is_1m.empty or oos_1m.empty:
            continue

        is_ob = OrderBlockDetector(order_block_params).run(is_htf)
        oos_ob = OrderBlockDetector(order_block_params).run(oos_htf)

        is_candidates, _ = build_zone_limit_candidates(
            is_htf, is_1m, timeframe, params=B_ENGINE_PARAMS, cfg=cfg, order_block_result=is_ob
        )
        oos_candidates, _ = build_zone_limit_candidates(
            oos_htf, oos_1m, timeframe, params=B_ENGINE_PARAMS, cfg=cfg, order_block_result=oos_ob
        )
        if not is_candidates or not oos_candidates:
            continue

        is_frame = _prepare_frame(is_htf)
        is_times = [int(t) for t in is_frame["open_time"].astype("int64").tolist()]
        is_volumes = [float(v) for v in is_frame["volume"].astype(float).tolist()]
        is_pctl = _percentile_lookup(is_candidates)
        is_values = [
            v
            for c in is_candidates
            if (v := _candidate_axis_value(c, axis_name, is_times, is_volumes, is_pctl)) is not None
        ]
        if len(is_values) < 4:
            continue
        is_values.sort()
        threshold = is_values[int(0.75 * (len(is_values) - 1))]
        pooled_threshold_values.append(threshold)

        oos_frame = _prepare_frame(oos_htf)
        oos_times = [int(t) for t in oos_frame["open_time"].astype("int64").tolist()]
        oos_volumes = [float(v) for v in oos_frame["volume"].astype(float).tolist()]
        oos_pctl = _percentile_lookup(oos_candidates)
        real_candidates = [
            c
            for c in oos_candidates
            if (v := _candidate_axis_value(c, axis_name, oos_times, oos_volumes, oos_pctl))
            is not None
            and v >= threshold
        ]
        real_trades = _sequence_and_cost(real_candidates, cfg)
        pooled_real_trades.extend(real_trades)
        pooled_pool_candidates.extend(oos_candidates)

        real_return, mean_r, ci_lo, ci_hi, p, iters = _matched_null_bootstrap(
            real_trades, oos_candidates, cfg, iterations=iterations, seed=seed
        )
        rows.append(
            MatchedNullRow(
                scope=f"{symbol}/{timeframe}",
                axis=axis_name,
                threshold=threshold,
                real_total_return=real_return,
                real_num_trades=len(real_trades),
                pool_size=len(oos_candidates),
                random_mean_return=mean_r,
                random_ci_low=ci_lo,
                random_ci_high=ci_hi,
                random_p_value=p,
                iterations=iters,
            )
        )

    if pooled_pool_candidates and pooled_threshold_values:
        pooled_cfg = default_backtest_config("1h")  # 풀링은 심볼·TF 혼합이라 연율화 불필요.
        pooled_threshold = sum(pooled_threshold_values) / len(pooled_threshold_values)
        real_return, mean_r, ci_lo, ci_hi, p, iters = _matched_null_bootstrap(
            pooled_real_trades, pooled_pool_candidates, pooled_cfg, iterations=iterations, seed=seed
        )
        rows.append(
            MatchedNullRow(
                scope="pooled",
                axis=axis_name,
                threshold=pooled_threshold,
                real_total_return=real_return,
                real_num_trades=len(pooled_real_trades),
                pool_size=len(pooled_pool_candidates),
                random_mean_return=mean_r,
                random_ci_low=ci_lo,
                random_ci_high=ci_hi,
                random_p_value=p,
                iterations=iters,
            )
        )
    return rows


def stage2_qualifying_axes(pooled_rows_by_axis: dict[Axis, list[VolumeQuantileRow]]) -> list[Axis]:
    """풀링 결과가 "단조 증가"이고 Q4 표본이 충분한 축만 2단계 대상으로 고른다."""
    qualifying: list[Axis] = []
    for axis_name, rows in pooled_rows_by_axis.items():
        b_rows = [r for r in rows if r.engine == "B"]
        if not b_rows:
            continue
        verdict = monotonicity_verdict(b_rows)
        q4 = max(b_rows, key=lambda r: r.quantile_rank, default=None)
        if verdict == "단조 증가" and q4 is not None and q4.n >= _STAGE2_MIN_TRADES:
            qualifying.append(axis_name)
    return qualifying


def stage2_verdict(
    matched_null: list[MatchedNullRow],
    axis_name: Axis,
    *,
    alpha: float = 0.05,
    min_trades: int = _MIN_TRADES_FOR_VERDICT,
) -> str:
    """2단계 결과로 (a) 진짜 엣지 / (b) 인샘플 과적합 / (c) 판정 불가를 가른다.

    `wan70_random_control_b.summarize_verdict`와 동일한 원칙: 거래 `min_trades`건
    미만인 셀은 표본이 너무 작아 p-value가 무의미하므로 판정에서 제외한다(풀링 셀은
    보통 이 기준을 만족하지만, 심볼×TF 셀별로는 대개 미달한다 — 그래서 풀링을 함께
    본다는 이슈 지침이 중요하다).
    """
    axis_rows = [r for r in matched_null if r.axis == axis_name]
    eligible = [
        r for r in axis_rows if r.real_num_trades >= min_trades and r.random_p_value is not None
    ]
    excluded = len(axis_rows) - len(eligible)
    if not eligible:
        return (
            f"**{axis_name}**: 판정 불가 — 유효 셀(거래 {min_trades}건 이상) 없음"
            f"(전체 {len(axis_rows)}개 중 {excluded}개 표본 부족으로 제외). 다중검정 인공물과 "
            "진짜 엣지를 구분할 표본이 없다."
        )
    sig = [
        r
        for r in eligible
        if r.random_p_value is not None
        and r.random_p_value <= alpha
        and r.random_mean_return is not None
        and r.real_total_return > r.random_mean_return
    ]
    if sig:
        cells = ", ".join(r.scope for r in sig)
        return (
            f"**{axis_name}**: **(a) 진짜 엣지 가능성** — 유효 셀 {len(eligible)}개 중 "
            f"{len(sig)}개가 p≤{alpha} & 실제총수익>무작위평균({cells})."
        )
    return (
        f"**{axis_name}**: **(b) 인샘플 과적합(유의성 미확인)** — 유효 셀 {len(eligible)}개"
        f"(거래 {min_trades}건 미만 {excluded}개 제외) 중 p≤{alpha}인 셀이 없다. 1단계 풀링에서 "
        "보인 단조 증가 패턴이 OOS+매칭 널로는 재현되지 않았다."
    )


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
REPORTS_DIR = Path("backtest/reports")


def _load_windows(
    db_path: Path, symbols: tuple[str, ...], timeframes: tuple[str, ...], years: float
) -> dict[tuple[str, str], tuple[pd.DataFrame, pd.DataFrame]]:
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


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def _rows_to_frame(rows: list[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def run_experiment(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    iterations: int = _BOOTSTRAP_ITERATIONS,
) -> tuple[
    list[VolumeQuantileRow],
    list[FloorSensitivityRow],
    list[VolumeQuantileRow],
    list[MatchedNullRow],
    list[Axis],
]:
    """1단계(셀별+풀링 분위표) + 손절폭 민감도 + (조건부) 2단계 매칭 널."""
    windows = _load_windows(db_path, symbols, timeframes, years)
    if not windows:
        return [], [], [], [], []

    decomposition: list[VolumeQuantileRow] = []
    sensitivity: list[FloorSensitivityRow] = []
    joined_by_engine_axis: dict[Engine, dict[Axis, list[_JoinedTrade]]] = {
        "A": defaultdict(list),
        "B": defaultdict(list),
    }

    for (symbol, timeframe), (htf_df, one_min_df) in windows.items():
        joined_by_engine = collect_cell_joined(htf_df, one_min_df, timeframe=timeframe)
        decomposition.extend(
            quantile_rows_for_cell(joined_by_engine, symbol=symbol, timeframe=timeframe)
        )
        sensitivity.extend(
            floor_sensitivity_rows(htf_df, one_min_df, symbol=symbol, timeframe=timeframe)
        )
        for engine, joined in joined_by_engine.items():
            for axis_name in _AXES:
                joined_by_engine_axis[engine][axis_name].extend(joined)
        print(f"[wan77] {symbol} {timeframe}: decomposition rows so far={len(decomposition)}")

    pooled_rows: list[VolumeQuantileRow] = []
    pooled_rows_by_axis: dict[Axis, list[VolumeQuantileRow]] = {}
    for engine in ("A", "B"):
        for axis_name in _AXES:
            joined = joined_by_engine_axis[engine][axis_name]
            rows = _quantile_rows(
                joined, symbol="POOLED", timeframe="ALL", engine=engine, axis_name=axis_name
            )
            pooled_rows.extend(rows)
            pooled_rows_by_axis.setdefault(axis_name, []).extend(rows)

    qualifying_axes = stage2_qualifying_axes(pooled_rows_by_axis)
    matched_null: list[MatchedNullRow] = []
    for axis_name in qualifying_axes:
        matched_null.extend(
            run_stage2_for_axis(windows, axis_name=axis_name, iterations=iterations)
        )

    return decomposition, sensitivity, pooled_rows, matched_null, qualifying_axes


def build_summary_markdown(
    decomposition: list[VolumeQuantileRow],
    sensitivity: list[FloorSensitivityRow],
    pooled_rows: list[VolumeQuantileRow],
    matched_null: list[MatchedNullRow],
    qualifying_axes: list[Axis],
    *,
    report_path: Path,
) -> str:
    pooled_by_axis: dict[Axis, list[VolumeQuantileRow]] = defaultdict(list)
    for row in pooled_rows:
        pooled_by_axis[row.axis].append(row)

    lines: list[str] = []
    lines.append("# WAN-77 거래량 성분 분해 — ob_volume 3축 × 분위별 성과\n")
    lines.append(
        "3심볼(BTC/ETH/SOL) × 3TF(4h/1h/1d) × A안/B안, 로컬 `data/ohlcv.db` 실데이터 3년. "
        "재현: `python -m backtest.wan77_volume_decomposition`. "
        f"셀별 원자료(`{len(decomposition)}`행)는 `{report_path}`.\n"
    )
    lines.append("## 풀링 단조성 판정(엔진=B, 채택 전략)\n")
    for axis_name in ("relative_volume", "volume_percentile", "imbalance"):
        b_rows = sorted(
            (r for r in pooled_by_axis.get(axis_name, []) if r.engine == "B"),
            key=lambda r: r.quantile_rank,
        )
        if not b_rows:
            lines.append(f"* **{axis_name}**: 데이터 없음")
            continue
        verdict = monotonicity_verdict(b_rows)
        detail = ", ".join(
            f"{r.quantile}(n={r.n}, avg_r={'—' if r.avg_r is None else f'{r.avg_r:.3f}'})"
            for r in b_rows
        )
        lines.append(f"* **{axis_name}**: {verdict} — {detail}")
    lines.append("")

    lines.append("## 2단계 발동 여부\n")
    if qualifying_axes:
        lines.append(
            f"발동 축: {', '.join(qualifying_axes)} (풀링 단조 증가 + Q4 n≥{_STAGE2_MIN_TRADES}).\n"
        )
    else:
        lines.append(
            "**발동 축 없음.** 어떤 축도 풀링 기준에서 단조 증가 + 충분한 Q4 표본을 만족하지 "
            "못했다 — 거래량이 실린 오더블록조차 무작위와 구분되지 않는다는 결론과 일치한다.\n"
        )

    if matched_null:
        lines.append("## 2단계: OOS 상위분위 vs 매칭 널\n")
        lines.append(
            "| 범위 | 축 | 임계값 | 실제수익합 | n | 풀크기 | 무작위평균 | 95% CI | p |\n"
            "| -- | -- | -- | -- | -- | -- | -- | -- | -- |"
        )
        for r in matched_null:
            ci = (
                f"[{r.random_ci_low:.3f}, {r.random_ci_high:.3f}]"
                if r.random_ci_low is not None
                else "—"
            )
            p = "—" if r.random_p_value is None else f"{r.random_p_value:.3f}"
            mean_r = "—" if r.random_mean_return is None else f"{r.random_mean_return:.3f}"
            lines.append(
                f"| {r.scope} | {r.axis} | {r.threshold:.3f} | {r.real_total_return:.3f} | "
                f"{r.real_num_trades} | {r.pool_size} | {mean_r} | {ci} | {p} |"
            )
        lines.append("")

    lines.append("## min_stop_distance_fraction 민감도(0/0.005/0.01)\n")
    lines.append(
        "| 심볼 | TF | 하한 | 총수익률 | n | 승률 | PF |\n| -- | -- | -- | -- | -- | -- | -- |"
    )
    for s in sorted(
        sensitivity, key=lambda s: (s.symbol, s.timeframe, s.min_stop_distance_fraction)
    ):
        pf = "—" if s.profit_factor is None else f"{s.profit_factor:.3f}"
        lines.append(
            f"| {s.symbol} | {s.timeframe} | {s.min_stop_distance_fraction} | "
            f"{s.total_return:.4f} | {s.num_trades} | {s.win_rate:.3f} | {pf} |"
        )
    lines.append("")

    lines.append("## 결론\n")
    if not qualifying_axes:
        lines.append(
            "**거래량이 실린 오더블록조차 무작위와 구분되지 않는다.** 세 축(상대거래량·"
            "거래량 분위수·불균형도) 어느 쪽도 풀링 기준에서 분위가 오를수록 평균 R이 "
            '일관되게 좋아지는 패턴을 보이지 않았다. WAN-70/71이 이미 밝힌 "진입 타이밍에 '
            '엣지 없음"이 거래량으로 존을 걸러도 사라지지 않는다 — 오더블록 개념 자체('
            '"기관이 대량 물량을 처리한 자리일수록 유효하다")에 대한 반증이다. 이 결론에 '
            "따라 3단계(거래량 필터 파라미터 구현)는 수행하지 않는다.\n"
        )
    else:
        for axis_name in qualifying_axes:
            lines.append(f"* {stage2_verdict(matched_null, axis_name)}")
        lines.append("")
        any_edge = any(
            "(a) 진짜 엣지" in stage2_verdict(matched_null, axis_name)
            for axis_name in qualifying_axes
        )
        if any_edge:
            lines.append(
                "1단계에서 단조 증가를 보인 축이 2단계(OOS+매칭 널)에서도 유의했다. "
                "3단계(거래량 필터 파라미터 구현)를 진행할 근거가 있다.\n"
            )
        else:
            pooled_hints = []
            for axis_name in qualifying_axes:
                pooled_row = next(
                    (
                        r
                        for r in matched_null
                        if r.axis == axis_name
                        and r.scope == "pooled"
                        and r.random_mean_return is not None
                        and r.random_p_value is not None
                    ),
                    None,
                )
                if pooled_row is not None:
                    pooled_hints.append(
                        f"`{axis_name}`(풀링 실제총수익 {pooled_row.real_total_return:.3f} vs "
                        f"무작위평균 {pooled_row.random_mean_return:.3f}, "
                        f"p={pooled_row.random_p_value:.3f})"
                    )
            hint_text = (
                f" 다만 {', '.join(pooled_hints)}은 방향성이 약하게나마 남아 있어 향후 표본이 "
                "늘면(더 긴 기간·더 많은 심볼) 재검정할 가치가 있다."
                if pooled_hints
                else ""
            )
            lines.append(
                "1단계 풀링에서 단조 증가를 보인 축이 2단계(OOS 분리 + 매칭 널)에서는 "
                "유의성을 재현하지 못했다(**(b) 인샘플 과적합** 또는 표본 부족) — 1단계만 "
                "보고 필터를 만들었다면 WAN-75가 겪은 다중검정 함정을 반복했을 것이다. "
                f"이 결론에 따라 3단계(거래량 필터 파라미터 구현)는 수행하지 않는다.{hint_text}\n"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-77 거래량 성분 분해")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--iterations", type=int, default=_BOOTSTRAP_ITERATIONS)
    parser.add_argument(
        "--decomposition-out", type=Path, default=REPORTS_DIR / "wan77_decomposition.csv"
    )
    parser.add_argument("--pooled-out", type=Path, default=REPORTS_DIR / "wan77_pooled.csv")
    parser.add_argument(
        "--sensitivity-out", type=Path, default=REPORTS_DIR / "wan77_sensitivity.csv"
    )
    parser.add_argument(
        "--matched-null-out", type=Path, default=REPORTS_DIR / "wan77_matched_null.csv"
    )
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan77_summary.md")
    args = parser.parse_args(argv)

    decomposition, sensitivity, pooled_rows, matched_null, qualifying_axes = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        iterations=args.iterations,
    )
    write_csv(_rows_to_frame(decomposition), args.decomposition_out)  # type: ignore[arg-type]
    write_csv(_rows_to_frame(pooled_rows), args.pooled_out)  # type: ignore[arg-type]
    write_csv(_rows_to_frame(sensitivity), args.sensitivity_out)  # type: ignore[arg-type]
    write_csv(_rows_to_frame(matched_null), args.matched_null_out)  # type: ignore[arg-type]
    print(f"[wan77] decomposition rows={len(decomposition)} → {args.decomposition_out}")
    print(f"[wan77] pooled rows={len(pooled_rows)} → {args.pooled_out}")
    print(f"[wan77] sensitivity rows={len(sensitivity)} → {args.sensitivity_out}")
    print(f"[wan77] matched null rows={len(matched_null)} → {args.matched_null_out}")
    print(f"[wan77] qualifying axes for stage2: {qualifying_axes}")

    summary = build_summary_markdown(
        decomposition,
        sensitivity,
        pooled_rows,
        matched_null,
        qualifying_axes,
        report_path=args.decomposition_out,
    )
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan77] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
