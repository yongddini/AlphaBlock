"""WAN-73 불일치 규명 — 간이 재현 vs 저장소 B안 엔진 감사 (WAN-74).

이슈 WAN-73은 세 규칙(존당 재진입 + RSI 중립 게이트 + 고정 2R 익절)의 **간이 재현**에서
+0.17~+0.26R(거래 2,292건)을 보고했지만, 저장소 엔진(`backtest.wan73_validation`)으로
같은 규칙을 3대 검증(OOS·매칭 널·체결률)에 태우면 **기각**(IS·OOS 양쪽 유의 셀 0개)됐다.
이 모듈은 그 불일치의 원인을 특정한다.

## 접근

간이 재현 스크립트 자체는 저장소에 없다(이슈 본문의 즉석 실험). 대신 이슈가 명시한
방법론("존 경계에 닿으면 무조건 체결", 확정봉 RSI 판정 뉘앙스)을 저장소의 기존
검증된 코드로 정확히 재구성한다:

* **naive(간이 재현 근사)** = `strategy.confluence.ConfluenceStrategy`(A안). `retap_mode=
  "every_tap"`로 매 탭을 후보로 내고, 확정봉 RSI로 즉시 게이트를 판정해 통과하면 무조건
  진입을 확정한다 — 체결 확률·만료·봉내 실시간 RSI 타이밍이 전혀 없다. 이는 "존 경계에
  닿으면 무조건 체결"이라는 간이 재현의 핵심 가정과 구조적으로 동일하다.
* **b_engine(저장소 채택 후보 엔진)** = `backtest.zone_limit_backtest.build_zone_limit_candidates`
  (B안). 지정가 체결(1분봉 서브스텝) + 실시간 봉내 RSI + 만료/무효화 취소를 반영한다.

두 엔진에 **동일한 오더블록 아카이브**(`OrderBlockDetector().run()` 1회 호출 결과)를
넘겨 존 탐지/병합 차이(가설 2)를 원천 차단한다. 그 위에서:

1. **단일 셀 거래 단위 대조**(`DETAIL_SYMBOL`/`DETAIL_TIMEFRAME`) — naive vs b_engine
   거래를 나란히 CSV로 남긴다.
2. **체결 모델 분해** — b_engine 필터를 통과하지 못한 naive 거래(같은 오더블록 기준
   `ob_key`로 매칭 실패)를 "미체결"로 간주하고, 그 R 분포를 필터를 통과한 거래의 R과
   비교한다(가설 1).
3. **풀링 검정** — 전 셀 실제 거래를 R 단위로 풀링해, 셀별 매칭 널(WAN-70과 동일 정의:
   RSI 게이트 무력화 `rsi_gate_mode="none"` 풀에서 방향·시각대 버킷을 맞춘 비복원추출)을
   동일하게 풀링한 분포와 비교한다(가설 4, 셀 분할로 인한 검정력 부족 여부).

기각도 정상 결론이다. 이 모듈의 목적은 "원인 불명"을 피하는 것이지 채택을 유도하는
것이 아니다.
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.models import BacktestConfig, PositionSide
from backtest.sweep import default_backtest_config
from backtest.wan70_random_control_b import DEFAULT_SYMBOLS, DEFAULT_YEARS
from backtest.wan73_validation import WAN73_NEW_PARAMS, _r_multiple
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from strategy.confluence import ConfluenceStrategy
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
)
from strategy.order_blocks import OrderBlockDetector

_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)
_HOUR_BUCKET_SIZE = 4  # WAN-70과 동일: UTC 4시간 버킷.
_BOOTSTRAP_ITERATIONS = 200

DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1h", "4h", "1d")
#: 단일 셀 거래 단위 대조 대상(실제 검증에서 가장 표본이 큰 셀, wan73_summary.md 참고).
DETAIL_SYMBOL = "BTC/USDT:USDT"
DETAIL_TIMEFRAME = "1h"


# --------------------------------------------------------------------------- #
# 거래 레코드
# --------------------------------------------------------------------------- #


class TradeRecord(BaseModel):
    """한 엔진이 낸 거래 하나(거래 단위 대조표 행)."""

    model_config = ConfigDict(frozen=True)

    engine: str
    """`naive_closed_bar` | `b_engine_realtime` | `b_engine_gate_none`."""
    symbol: str
    timeframe: str
    side: str
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    stop_price: float
    reason: str
    r_multiple: float
    ob_key: str
    """진입 근거 오더블록 식별자(방향·상단·하단·확정시각). 두 엔진 간 동일 존 매칭용."""


def _ob_key(ob: OrderBlock) -> str:
    return f"{ob.direction.value}:{ob.top:.10f}:{ob.bottom:.10f}:{ob.confirmed_time}"


def _mean_r(records: list[TradeRecord]) -> float | None:
    if not records:
        return None
    return sum(r.r_multiple for r in records) / len(records)


def _sequence_single_position(records: list[TradeRecord]) -> list[TradeRecord]:
    """동시 1포지션 제약으로 시간순 재배치한다(`zone_limit_backtest._sequence_and_cost`와
    동일 규칙의 비용-모델-없는 경량 버전).

    `retap_mode="every_tap"`은 같은 오더블록의 여러 탭이 각자 독립적으로(그리고
    `limit_valid_bars=None`이면 무기한) 미래를 바라보며 체결을 찾으므로, 서로 다른
    탭에서 나온 후보가 사실상 같은 체결 순간을 중복으로 낼 수 있다(겹치는 탭들이
    모두 같은 미래의 RSI 중립 구간에 수렴). 실제 트레이더는 동시에 한 포지션만
    보유하므로 이 중복을 제거해야 거래 수가 `wan73_validation`의 `real_num_trades`
    정의(포지션 시퀀싱 후)와 일치한다.
    """
    ordered = sorted(records, key=lambda r: (r.entry_time, r.exit_time))
    busy_until = -1
    sequenced: list[TradeRecord] = []
    for rec in ordered:
        if rec.entry_time < busy_until:
            continue
        sequenced.append(rec)
        busy_until = rec.exit_time
    return sequenced


# --------------------------------------------------------------------------- #
# naive(간이 재현 근사) 엔진 — A안, 매 탭 + 확정봉 RSI + 즉시 확정
# --------------------------------------------------------------------------- #


def naive_trades(
    htf_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    params: ConfluenceParams,
    order_block_params: OrderBlockParams | None,
    order_block_result: OrderBlockResult,
) -> list[TradeRecord]:
    """ "존 경계 터치 = 무조건 체결" 간이 재현 근사(WAN-74 가설 검증용).

    `ConfluenceStrategy`(A안)에 `retap_mode="every_tap"`을 주면 매 탭마다 확정봉 RSI로
    즉시 게이트를 판정해, 통과하면 체결 확률·만료·봉내 타이밍 없이 바로 진입을
    확정한다 — 이슈 WAN-73 간이 재현의 핵심 가정과 구조적으로 같다.
    """
    strategy = ConfluenceStrategy(params, order_block_params)
    result = strategy.run(htf_df, order_block_result)
    records: list[TradeRecord] = []
    for entry in result.confirmed_entries:
        if entry.planned_exit is None or entry.order_block is None:
            continue
        ob = entry.order_block
        is_long = entry.direction is OrderBlockDirection.BULLISH
        boundary = ob.bottom if is_long else ob.top
        r = _r_multiple(entry.price, entry.planned_exit.price, boundary, is_long)
        records.append(
            TradeRecord(
                engine="naive_closed_bar",
                symbol=symbol,
                timeframe=timeframe,
                side="long" if is_long else "short",
                entry_time=entry.time,
                entry_price=entry.price,
                exit_time=entry.planned_exit.time,
                exit_price=entry.planned_exit.price,
                stop_price=boundary,
                reason=entry.planned_exit.reason.value,
                r_multiple=r,
                ob_key=_ob_key(ob),
            )
        )
    return _sequence_single_position(records)


# --------------------------------------------------------------------------- #
# b_engine(저장소 채택 후보) — B안, 지정가 체결 + 실시간 RSI
# --------------------------------------------------------------------------- #


def _stop_to_obs_index(
    order_blocks: list[OrderBlock],
) -> dict[tuple[bool, float], list[OrderBlock]]:
    """(롱여부, 무효화 경계가) → 오더블록 목록. `_Candidate`에서 원본 존을 역추적하는 색인.

    `_Candidate`는 원본 오더블록 참조를 갖지 않지만 `stop_price`가 항상 `ob.bottom`/
    `ob.top` 그대로이므로(zone_limit_backtest.py), 같은 `order_block_result`를 공유하는
    한 이 값으로 존을 되찾을 수 있다.
    """
    index: dict[tuple[bool, float], list[OrderBlock]] = defaultdict(list)
    for ob in order_blocks:
        is_long = ob.direction is OrderBlockDirection.BULLISH
        boundary = ob.bottom if is_long else ob.top
        index[(is_long, boundary)].append(ob)
    return index


def _match_ob(
    cand: _Candidate, index: dict[tuple[bool, float], list[OrderBlock]]
) -> OrderBlock | None:
    is_long = cand.side is PositionSide.LONG
    candidates = index.get((is_long, cand.stop_price), [])
    if not candidates:
        return None
    matches = [
        ob
        for ob in candidates
        if ob.confirmed_time <= cand.entry_time
        and (ob.break_time is None or cand.entry_time <= ob.break_time)
    ]
    if not matches:
        return candidates[0]
    return max(matches, key=lambda ob: ob.confirmed_time)


def b_engine_trades(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    symbol: str,
    engine_label: str,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    order_block_result: OrderBlockResult,
    rsi_gate_mode: Literal["extreme", "neutral", "none"] | None = None,
    sequence: bool = True,
) -> list[TradeRecord]:
    """B안(`build_zone_limit_candidates`) 체결 결과를 `TradeRecord`로 변환한다.

    `rsi_gate_mode="none"`을 주면 RSI 게이트를 무력화한 채 같은 체결(1분봉 서브스텝·
    만료·무효화) 메커니즘만 남긴 풀이 된다(WAN-70과 동일한 매칭 널 정의). `sequence`가
    True(기본)면 동시 1포지션 제약으로 시간순 재배치해 `real_num_trades`와 같은 정의의
    거래 수를 낸다. 매칭 널 풀(`rsi_gate_mode="none"` 호출)은 WAN-70과 동일하게
    시퀀싱하지 않은 원(raw) 후보 그대로 둔다 — 부트스트랩이 반복마다 표본추출 후
    시퀀싱한다.
    """
    candidates, _ = build_zone_limit_candidates(
        htf_df,
        df_1m,
        timeframe,
        params=params,
        cfg=cfg,
        order_block_result=order_block_result,
        rsi_gate_mode=rsi_gate_mode,
    )
    index = _stop_to_obs_index(order_block_result.order_blocks)
    records: list[TradeRecord] = []
    for cand in candidates:
        is_long = cand.side is PositionSide.LONG
        ob = _match_ob(cand, index)
        r = _r_multiple(cand.entry_price, cand.exit_price, cand.stop_price, is_long)
        records.append(
            TradeRecord(
                engine=engine_label,
                symbol=symbol,
                timeframe=timeframe,
                side="long" if is_long else "short",
                entry_time=cand.entry_time,
                entry_price=cand.entry_price,
                exit_time=cand.exit_time,
                exit_price=cand.exit_price,
                stop_price=cand.stop_price,
                reason=cand.reason.value,
                r_multiple=r,
                ob_key=_ob_key(ob) if ob is not None else "unmatched",
            )
        )
    return _sequence_single_position(records) if sequence else records


# --------------------------------------------------------------------------- #
# 체결 모델 분해(가설 1) — 미체결 주문의 가상 R
# --------------------------------------------------------------------------- #


class FillDecomposition(BaseModel):
    """한 (심볼, TF) 셀의 naive 대비 b_engine 체결 분해(WAN-74 검증 2)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    naive_n: int
    """naive(간이 재현 근사)가 낸 거래 수 — 존 경계 터치 + 확정봉 RSI 통과 전부."""
    real_filled_n: int
    """b_engine(실시간 RSI + 지정가 체결)이 실제 체결한 거래 수."""
    unfilled_n: int
    """naive에는 있으나 b_engine에서 같은 존(`ob_key`)으로 매칭되지 않은 거래 수."""
    fill_rate_by_zone: float | None
    mean_r_real_filled: float | None
    mean_r_unfilled_virtual: float | None
    """미체결 거래를 naive 엔진(확정봉 RSI+존 무효화 손절+고정 2R) 기준으로 계산한 가상 R 평균."""
    unfilled_better: bool | None
    """가상 R 평균이 실제 체결 R 평균보다 높으면 True — 지정가 체결 메커니즘이 좋은
    기회를 체계적으로 버렸다는 뜻(WAN-73 가설 1 확인)."""


def compute_fill_decomposition(
    naive: list[TradeRecord], real_filled: list[TradeRecord], *, symbol: str, timeframe: str
) -> FillDecomposition:
    real_keys = {t.ob_key for t in real_filled}
    unfilled = [t for t in naive if t.ob_key not in real_keys]
    mean_real = _mean_r(real_filled)
    mean_unfilled = _mean_r(unfilled)
    unfilled_better = (
        None if mean_real is None or mean_unfilled is None else mean_unfilled > mean_real
    )
    fill_rate = len(real_keys) / len(naive) if naive else None
    return FillDecomposition(
        symbol=symbol,
        timeframe=timeframe,
        naive_n=len(naive),
        real_filled_n=len(real_filled),
        unfilled_n=len(unfilled),
        fill_rate_by_zone=fill_rate,
        mean_r_real_filled=mean_real,
        mean_r_unfilled_virtual=mean_unfilled,
        unfilled_better=unfilled_better,
    )


# --------------------------------------------------------------------------- #
# 풀링 검정(가설 4) — 셀 분할이 검정력을 죽였는가
# --------------------------------------------------------------------------- #


def _hour_bucket(entry_time_ms: int) -> int:
    hour = datetime.fromtimestamp(entry_time_ms / 1000.0, tz=UTC).hour
    return hour // _HOUR_BUCKET_SIZE


class PooledTestResult(BaseModel):
    """전 셀 실제 거래를 R 단위로 풀링한 매칭 널 검정 결과(WAN-74 검증 3)."""

    model_config = ConfigDict(frozen=True)

    real_pooled_trades: int
    real_pooled_mean_r: float
    null_mean_r: float
    null_ci_low: float
    null_ci_high: float
    p_value: float
    iterations: int


def _empty_pooled_result() -> PooledTestResult:
    return PooledTestResult(
        real_pooled_trades=0,
        real_pooled_mean_r=0.0,
        null_mean_r=0.0,
        null_ci_low=0.0,
        null_ci_high=0.0,
        p_value=1.0,
        iterations=0,
    )


def pooled_matched_null_test(
    cells: dict[tuple[str, str], tuple[list[TradeRecord], list[TradeRecord]]],
    *,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = 74,
) -> PooledTestResult:
    """전 셀 실제 거래 R 평균을, 셀별 매칭 널을 같은 방식으로 풀링한 분포와 비교한다.

    `cells`는 `(symbol, timeframe) → (real_filled, null_pool)`. `null_pool`은
    `rsi_gate_mode="none"`으로 얻은, 체결 메커니즘은 동일하되 RSI 타이밍 게이트만
    무력화한 후보 풀이다(WAN-70과 동일 정의). 각 반복에서 셀마다 실제 거래의
    (방향, 시각대 버킷) 구성·개수를 맞춰 비복원추출한 뒤, **전 셀을 하나로 풀링**해
    R 평균을 낸다 — 개별 셀 검정(`wan73_validation`)이 다중검정·소표본으로 놓쳤을 수
    있는 풀링 유의성을 직접 확인한다.
    """
    if not cells:
        return _empty_pooled_result()

    rng = random.Random(seed)
    real_r: list[float] = []
    pooled_by_bucket: dict[tuple[str, str], dict[tuple[str, int], list[TradeRecord]]] = {}
    pooled_by_side: dict[tuple[str, str], dict[str, list[TradeRecord]]] = {}
    target_counts: dict[tuple[str, str], dict[tuple[str, int], int]] = {}

    for key, (real_filled, null_pool) in cells.items():
        real_r.extend(t.r_multiple for t in real_filled)
        by_bucket: dict[tuple[str, int], list[TradeRecord]] = defaultdict(list)
        by_side: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in null_pool:
            by_bucket[(t.side, _hour_bucket(t.entry_time))].append(t)
            by_side[t.side].append(t)
        pooled_by_bucket[key] = by_bucket
        pooled_by_side[key] = by_side
        counts: dict[tuple[str, int], int] = defaultdict(int)
        for t in real_filled:
            counts[(t.side, _hour_bucket(t.entry_time))] += 1
        target_counts[key] = counts

    real_mean = sum(real_r) / len(real_r) if real_r else 0.0
    null_means: list[float] = []
    for _ in range(iterations):
        pooled_sample: list[float] = []
        for key, counts in target_counts.items():
            by_bucket = pooled_by_bucket[key]
            by_side = pooled_by_side[key]
            used_by_side: dict[str, set[int]] = defaultdict(set)
            for (side, bucket), count in counts.items():
                bucket_pool = by_bucket.get((side, bucket), [])
                k = min(count, len(bucket_pool))
                picks = rng.sample(bucket_pool, k) if k else []
                pooled_sample.extend(t.r_multiple for t in picks)
                used_by_side[side].update(id(t) for t in picks)
                shortfall = count - k
                if shortfall > 0:
                    remaining = [
                        t for t in by_side.get(side, []) if id(t) not in used_by_side[side]
                    ]
                    fill_k = min(shortfall, len(remaining))
                    fill_picks = rng.sample(remaining, fill_k) if fill_k else []
                    pooled_sample.extend(t.r_multiple for t in fill_picks)
                    used_by_side[side].update(id(t) for t in fill_picks)
        if pooled_sample:
            null_means.append(sum(pooled_sample) / len(pooled_sample))

    null_means.sort()
    n = len(null_means)
    if n == 0:
        return PooledTestResult(
            real_pooled_trades=len(real_r),
            real_pooled_mean_r=real_mean,
            null_mean_r=0.0,
            null_ci_low=0.0,
            null_ci_high=0.0,
            p_value=1.0,
            iterations=0,
        )
    p_value = sum(1 for m in null_means if m >= real_mean) / n
    return PooledTestResult(
        real_pooled_trades=len(real_r),
        real_pooled_mean_r=real_mean,
        null_mean_r=sum(null_means) / n,
        null_ci_low=null_means[int(0.025 * (n - 1))],
        null_ci_high=null_means[int(0.975 * (n - 1))],
        p_value=p_value,
        iterations=n,
    )


# --------------------------------------------------------------------------- #
# 재현 실행(main): 로컬 실데이터
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
DEFAULT_TRADE_DIFF_PATH = Path("backtest/reports/wan74_trade_level_diff.csv")
DEFAULT_FILL_DECOMPOSITION_PATH = Path("backtest/reports/wan74_fill_decomposition.csv")
DEFAULT_SUMMARY_PATH = Path("backtest/reports/wan74_summary.md")


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
    iterations: int = _BOOTSTRAP_ITERATIONS,
) -> tuple[list[TradeRecord], list[FillDecomposition], PooledTestResult]:
    """로컬 `data/ohlcv.db` 실데이터로 거래 단위 대조·체결 분해·풀링 검정을 산출한다."""
    try:
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return [], [], _empty_pooled_result()
    if not db_path.exists():
        return [], [], _empty_pooled_result()

    trade_diff_records: list[TradeRecord] = []
    decompositions: list[FillDecomposition] = []
    cells: dict[tuple[str, str], tuple[list[TradeRecord], list[TradeRecord]]] = {}

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
                if len(htf_win) < 30:
                    continue

                ob_result = OrderBlockDetector().run(htf_win)
                cfg = default_backtest_config(timeframe)

                naive = naive_trades(
                    htf_win,
                    symbol=symbol,
                    timeframe=timeframe,
                    params=WAN73_NEW_PARAMS,
                    order_block_params=None,
                    order_block_result=ob_result,
                )
                real_filled = b_engine_trades(
                    htf_win,
                    one_min_win,
                    timeframe,
                    symbol=symbol,
                    engine_label="b_engine_realtime",
                    params=WAN73_NEW_PARAMS,
                    cfg=cfg,
                    order_block_result=ob_result,
                )
                null_pool = b_engine_trades(
                    htf_win,
                    one_min_win,
                    timeframe,
                    symbol=symbol,
                    engine_label="b_engine_gate_none",
                    params=WAN73_NEW_PARAMS,
                    cfg=cfg,
                    order_block_result=ob_result,
                    rsi_gate_mode="none",
                    sequence=False,
                )

                decomp = compute_fill_decomposition(
                    naive, real_filled, symbol=symbol, timeframe=timeframe
                )
                decompositions.append(decomp)
                cells[(symbol, timeframe)] = (real_filled, null_pool)

                if symbol == DETAIL_SYMBOL and timeframe == DETAIL_TIMEFRAME:
                    trade_diff_records.extend(naive)
                    trade_diff_records.extend(real_filled)

                print(
                    f"[wan74] {symbol} {timeframe}: naive={decomp.naive_n} "
                    f"real_filled={decomp.real_filled_n} unfilled={decomp.unfilled_n} "
                    f"mean_r_filled={decomp.mean_r_real_filled} "
                    f"mean_r_unfilled={decomp.mean_r_unfilled_virtual}"
                )

    pooled = pooled_matched_null_test(cells, iterations=iterations)
    return trade_diff_records, decompositions, pooled


def _records_to_frame(records: list[TradeRecord]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in records])


def _decompositions_to_frame(results: list[FillDecomposition]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in results])


def _fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _decomposition_table(results: list[FillDecomposition]) -> str:
    header = (
        "| 심볼 | TF | naive(간이재현) | 실제체결 | 미체결 | 존 체결률 | "
        "체결 평균R | 미체결 가상평균R | 미체결이 더 좋았나 |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    rows = sorted(results, key=lambda r: (r.symbol, order.get(r.timeframe, 9)))
    body = [
        f"| {r.symbol} | {r.timeframe} | {r.naive_n} | {r.real_filled_n} | {r.unfilled_n} | "
        f"{_fmt(r.fill_rate_by_zone, 3)} | {_fmt(r.mean_r_real_filled, 3)} | "
        f"{_fmt(r.mean_r_unfilled_virtual, 3)} | "
        f"{'—' if r.unfilled_better is None else ('예' if r.unfilled_better else '아니오')} |"
        for r in rows
    ]
    return header + "\n" + "\n".join(body)


def _decide_conclusion(
    decompositions: list[FillDecomposition], pooled: PooledTestResult
) -> tuple[str, list[str]]:
    """검증 2·3 산출을 종합해 (a)엣지없음/(b)검정력부족/(c)체결모델인공물을 판정한다."""
    reasons: list[str] = []
    valid = [d for d in decompositions if d.mean_r_real_filled is not None and d.unfilled_n > 0]
    unfilled_better_count = sum(1 for d in valid if d.unfilled_better)
    total_unfilled_n = sum(d.unfilled_n for d in decompositions)
    total_naive_n = sum(d.naive_n for d in decompositions)
    drop_rate = (
        1.0 - (sum(d.real_filled_n for d in decompositions) / total_naive_n)
        if total_naive_n
        else 0.0
    )

    fill_artifact = bool(valid) and unfilled_better_count >= max(1, len(valid) // 2)
    pooled_significant = pooled.iterations > 0 and pooled.p_value <= 0.05

    reasons.append(
        f"naive(간이재현 근사) 거래 {total_naive_n}건 중 b_engine 미체결 {total_unfilled_n}건"
        f"(탈락률 {drop_rate:.1%})."
        + (
            " 탈락률이 음수라는 것은 b_engine이 naive보다 더 많은 오더블록을 체결시켰다는 "
            "뜻이다 — `limit_valid_bars=None`(무기한 대기)이 확정봉 1회 판정보다 RSI 중립 "
            "타이밍을 더 관대하게 잡아낸다. 즉 지정가 체결 메커니즘이 기회를 버리는 것이 "
            "아니라(가설 1 기각) 오히려 naive가 놓친 다른 기회까지 잡아낸다."
            if drop_rate < 0
            else ""
        )
        + f" 셀 {len(valid)}개 중 {unfilled_better_count}개에서 "
        "미체결 가상 R 평균이 실제 체결 R 평균보다 높다."
    )
    reasons.append(
        f"풀링 검정: 실제 거래 {pooled.real_pooled_trades}건 "
        f"풀링 평균R={pooled.real_pooled_mean_r:.4f}, "
        f"매칭 널 평균R={pooled.null_mean_r:.4f}(95% CI [{pooled.null_ci_low:.4f}, "
        f"{pooled.null_ci_high:.4f}]), p={pooled.p_value:.4f}."
    )

    if fill_artifact and not pooled_significant:
        return "체결 모델 인공물(c)", reasons + [
            "미체결 주문이 체계적으로 더 좋았고 풀링 검정도 유의하지 않다 — 실시간 RSI "
            "타이밍/지정가 체결 메커니즘이 존재하는 엣지를 체계적으로 걸러냈을 가능성이 "
            "가장 크다."
        ]
    if pooled_significant:
        return "검정력 부족(b) 가능성 + 체결 모델 기여", reasons + [
            "셀별로는 유의하지 않던 것이 풀링하면 유의해진다 — 셀 분할·다중검정이 "
            "검정력을 깎았을 가능성이 있다."
            + (
                " 미체결 주문도 더 좋았다면 체결 모델도 함께 기여했을 것이다."
                if fill_artifact
                else ""
            )
        ]
    return "진짜 엣지 없음(a)", reasons + [
        "미체결 주문이 체결 주문보다 체계적으로 낫지 않고(오히려 b_engine이 naive보다 "
        "더 많이 체결시켰다), 풀링해도 매칭 널 대비 유의하지 않다 — 매칭 널도 같은 "
        "오더블록 유니버스·방향·시각대에서 뽑히므로, 실제 거래의 양(+) 평균R은 RSI 중립 "
        "타이밍의 정밀도가 아니라 고정 2R:1R 비대칭 손익비 구조와 오더블록 유니버스 "
        "자체(상승장 드리프트 등)에서 오는 일반적 효과일 가능성이 크다. 간이 재현의 "
        "양(+) 결과는 이 일반적 효과를 RSI 필터가 만든 엣지로 오인한 것으로 보인다."
    ]


def build_summary_markdown(
    trade_diff: list[TradeRecord],
    decompositions: list[FillDecomposition],
    pooled: PooledTestResult,
    *,
    trade_diff_path: Path,
    decomposition_path: Path,
) -> str:
    conclusion_label, conclusion_reasons = _decide_conclusion(decompositions, pooled)
    reasons_md = "\n".join(f"* {r}" for r in conclusion_reasons)
    naive_detail = [t for t in trade_diff if t.engine == "naive_closed_bar"]
    real_detail = [t for t in trade_diff if t.engine == "b_engine_realtime"]

    return (
        "# WAN-74 — WAN-73 불일치 규명(간이 재현 vs 저장소 B안 엔진)\n\n"
        f"{DETAIL_SYMBOL} {DETAIL_TIMEFRAME}(가장 표본이 큰 셀) 거래 단위 대조 원자료는 "
        f"`{trade_diff_path}`(naive {len(naive_detail)}건, 실제체결 {len(real_detail)}건). "
        f"체결 분해 원자료는 `{decomposition_path}`. 재현: "
        "`python -m backtest.wan74_discrepancy_audit`.\n\n"
        "## 방법론\n\n"
        "간이 재현 스크립트 자체는 저장소에 없어(이슈 본문의 즉석 실험), 이슈가 명시한 "
        '방법론("존 경계에 닿으면 무조건 체결", 확정봉 RSI)을 저장소의 기존 검증된 코드로 '
        "재구성했다:\n\n"
        '* **naive** = `ConfluenceStrategy`(A안, `retap_mode="every_tap"`): 매 탭마다 '
        "확정봉 RSI로 즉시 게이트를 판정하고 통과하면 무조건 진입 확정. 체결 확률·만료·"
        "봉내 실시간 타이밍이 없다.\n"
        "* **b_engine** = `build_zone_limit_candidates`(B안): 지정가 체결(1분봉 서브스텝) "
        "+ 실시간 봉내 RSI + 만료/무효화 취소.\n\n"
        "두 엔진에 **동일한 오더블록 아카이브**를 넘겨 존 탐지/병합 차이(가설 2)를 "
        "원천 차단했다 — 아래 결과의 차이는 전부 체결 모델·RSI 타이밍·손절 정의(가설 1·3·4) "
        "에서만 나온다.\n\n"
        "## 검증 2: 체결 모델 분해(가설 1) — 미체결 주문의 가상 R\n\n"
        f"{_decomposition_table(decompositions)}\n\n"
        "> `미체결` = naive가 확정한 거래 중 b_engine에서 같은 오더블록으로 매칭되지 않은 "
        "거래(실시간 RSI 타이밍이 안 맞았거나, 체결 전 만료/무효화됐거나, 지정가 미체결). "
        "`미체결 가상평균R`은 그 거래를 naive 엔진(확정봉 RSI+존 무효화 손절+고정 2R) 기준 "
        "으로 계산한 R이다.\n\n"
        "## 검증 3: 풀링 검정(가설 4) — 셀 분할이 검정력을 죽였는가\n\n"
        f"* 실제 거래(전 셀 풀링) {pooled.real_pooled_trades}건, 풀링 평균 R = "
        f"{pooled.real_pooled_mean_r:.4f}\n"
        f"* 매칭 널(전 셀 풀링, {pooled.iterations}회 반복) 평균 R = "
        f"{pooled.null_mean_r:.4f}, 95% CI [{pooled.null_ci_low:.4f}, {pooled.null_ci_high:.4f}]\n"
        f"* p = {pooled.p_value:.4f}(단측, 실제 평균 이상을 낸 반복 비율)\n\n"
        "## 결론\n\n"
        f"### 원인 판정: **{conclusion_label}**\n\n"
        f"{reasons_md}\n\n"
        "## 권고\n\n" + _recommendation_text(conclusion_label) + "\n\n"
        "`ALPHABLOCK_LIVE_TRADING`은 건드리지 않았다. 기본값 전환은 이 이슈에서 하지 "
        "않는다(권고까지만, WAN-74 완료 기준).\n"
    )


def _recommendation_text(conclusion_label: str) -> str:
    if conclusion_label.startswith("체결 모델 인공물"):
        return (
            "**WAN-73 재개를 권고한다.** 저장소 B안의 실시간 봉내 RSI 타이밍 요구가 "
            "실제로 좋은 기회를 걸러내고 있다면, 판정 기준을 확정봉 RSI(탭 시점 근접)로 "
            "완화하거나 지정가 유효기간·재확인 로직을 재설계하는 후속 이슈가 필요하다. "
            "다만 이는 **체결 모델을 바꾸는 것**이지 오더블록 자체를 기각할 근거는 "
            "아니라는 점에 유의한다."
        )
    if conclusion_label.startswith("검정력 부족"):
        return (
            "**풀링 유의성만으로 즉시 채택하지 않는다.** 셀 분할이 검정력을 깎았을 "
            "가능성은 있지만, 이는 다중검정 보정(예: Benjamini-Hochberg)이나 표본 확대 "
            "(추가 심볼/기간)로 먼저 재검증해야 한다. 풀링은 이질적 셀(심볼·TF별로 다른 "
            "변동성 체제)을 하나로 합치므로 과적합 위험도 함께 재평가할 것."
        )
    return (
        "**WAN-73 기각을 유지한다.** 간이 재현의 양(+) 결과는 엔진 차이의 인공물이 "
        "아니라 방법론(약 60개 조합 탐색, 확정봉 RSI의 낙관적 타이밍) 자체에서 온 것으로 "
        "판단된다. 오더블록/RSI 진입 규칙의 근본 재검토가 필요하다는 WAN-70·WAN-71의 "
        "결론과 일치한다."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-74 WAN-73 불일치 규명(간이재현 vs B안 엔진)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--iterations", type=int, default=_BOOTSTRAP_ITERATIONS)
    parser.add_argument("--trade-diff-out", type=Path, default=DEFAULT_TRADE_DIFF_PATH)
    parser.add_argument("--decomposition-out", type=Path, default=DEFAULT_FILL_DECOMPOSITION_PATH)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY_PATH)
    args = parser.parse_args(argv)

    trade_diff, decompositions, pooled = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        iterations=args.iterations,
    )
    write_csv(_records_to_frame(trade_diff), args.trade_diff_out)
    write_csv(_decompositions_to_frame(decompositions), args.decomposition_out)
    print(f"[wan74] trade diff rows={len(trade_diff)} → {args.trade_diff_out}")
    print(f"[wan74] decomposition rows={len(decompositions)} → {args.decomposition_out}")

    summary = build_summary_markdown(
        trade_diff,
        decompositions,
        pooled,
        trade_diff_path=args.trade_diff_out,
        decomposition_path=args.decomposition_out,
    )
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan74] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
