"""WAN-99: 지정가 오프셋 민감도 — 체결 확실성을 사서 진입가를 판다.

배경은 이슈 WAN-99 본문 참고. WAN-96에서 15m이 무너진 이유는 **수익이 "지정가에 스치듯
닿고 되돌아간 체결"에 몰려 있었기 때문**이다 — `pen_5bp`(5bp 관통 요구)는 거래를 4.7%만
줄이는데 수익은 +15.8% → +1.3%로 사라졌다.

그런데 관통 요구를 만족시키는 방법이 하나 더 있다: **애초에 지정가를 그만큼 안쪽(가격이
오는 방향)에 거는 것**이다. 롱이면 존 근단보다 약간 위, 숏이면 약간 아래. 그러면 가격이
내 레벨을 관통해 지나가므로 체결이 사실상 보장된다. 대가는 진입가다 — 롱을 더 높이 잡으면
손절(오더블록 무효화)까지 거리가 늘어 **1R이 커지고** 고정 1.5R 익절 목표도 그만큼 멀어진다.

이 리포트는 그 교환(**체결 확실성 ↔ 진입가**)의 승자를 스윕으로 가리고, 오프셋 채택/미채택
권고를 낸다.

## 두 축

- **오프셋**(`zone_limit_offset_bps`, WAN-99): 0 / 2 / 5 / 10 / 20 bp. 양수 = 체결이
  쉬워지는 방향. 기본값 0.0은 WAN-95/96 그대로다.
- **체결 가정**(`fill_penetration_bps` + `fill_dropout_rate`, WAN-96): `baseline`(닿으면
  체결) / `pen_5bp`(5bp 관통 요구) / `pen_5bp_drop_50`(관통 5bp + 50% 탈락, 최악).

핵심 질문은 하나다: **보수 가정(`pen_5bp_drop_50`)에서 플러스를 유지하는 오프셋이
존재하는가.** 존재하면 WAN-96의 15m·1h 제외 권고가 뒤집힌다.

## 과최적화 방어

보수 가정 **위에서** 파라미터를 고르는 일이라, 감싸지 않으면 "가정에 맞춘 숫자"가 된다.
그래서 세 겹으로 감싼다:

1. **IS/OOS 분할**: 최근 3년을 앞 2/3(IS)·뒤 1/3(OOS)로 나눠, 오프셋을 **IS에서만**
   고르고 OOS로 검증한다. 최고 오프셋을 그냥 채택하지 않는다.
2. **고원(plateau) 진단**: 오프셋별 성과가 평탄한 고원인지 뾰족한 봉우리인지 본다.
   뾰족하면 과최적화 신호다 — 이웃 오프셋이 무너지는 최적값은 신호가 아니라 잡음이다.
3. **시드 분산**: 탈락 가정은 시드를 여러 개 돌려 단일 시드의 운을 배제한다.

## 재현

```
python -m backtest.wan99_zone_limit_offset_report
```

전 심볼×TF×오프셋×가정을 다 도는 데 시간이 걸린다(15m 한 셀이 수십 분). 기본은 셀 단위
병렬 실행이며, 좁히려면 `--symbols BTC/USDT:USDT --timeframes 15m`처럼 인자를 준다.
셀은 서로 독립이고 시드가 고정돼 있어 `--jobs`를 바꿔도 결과는 동일하다.

## ⚠️ 커밋된 산출물은 WAN-100 이전 엔진 기준 (WAN-97이 재산출)

WAN-100이 지정가 경로의 첫 탭 면제 누락을 고쳐 이 리포트의 입력 엔진이 바뀌었다. 재산출이
위 비용 때문에 무거워 커밋된 `wan99_*` 표는 교정 **전** 수치로 남아 있고, 그 사실을
`wan99_zone_limit_offset_summary.md` 헤더에 경고로 적어 뒀다 — **이 모듈이 생성하는
마크다운에는 그 경고가 없다**(전체 재산출을 하면 수치가 최신이 되어 경고가 거짓이 되므로
일부러 넣지 않았다). 따라서 WAN-97이 전체 재산출을 하면 그 헤더 경고는 자연히 사라진다.

예외로 `wan99_zone_limit_offset.csv`의 (BTC 1h, full, offset 0, `pen_5bp`) 한 셀만 이
모듈의 `run_cell`로 재산출해 교체했다 — `tests/test_run_regression_real_data.py`가 CLI
대조로 고정한 셀이기 때문이다. **그래서 그 CSV는 엔진이 섞여 있다**(1셀 교정 후, 389셀
교정 전). 셀 간 비교는 WAN-97 재산출 이후에 할 것.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

# 평균 R 산식은 공용 골격(WAN-101)으로 옮겼다. 이 모듈의 이름으로도 계속 쓸 수 있게
# 명시적으로 재수출한다 — 리포트 재현 커맨드와 기존 참조가 그대로 살아 있어야 한다.
from backtest.harness import LEGACY_OB_PARAMS, LEGACY_RSI_GATE_MODE, pin_band_bar
from backtest.harness import mean_r as mean_r
from backtest.models import BacktestConfig
from backtest.sweep import default_backtest_config
from backtest.wan81_engine_replacement_report import (
    _CACHE_DIR,
    _DB_PATH,
    DEFAULT_SYMBOLS,
    DEFAULT_YEARS,
    _load_recent,
)
from backtest.zone_limit_backtest import run_zone_limit_backtest_verbose
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams
from strategy.order_blocks import OrderBlockDetector

#: 판단 대상 TF. WAN-96이 제외를 권고한 15m이 본안이고, 1h는 같은 최악 가정에서 −1.7%라
#: 같은 잣대를 댄다. 4h·1d는 체결률이 높아 이 축의 쟁점이 작다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 스윕할 오프셋(bp). 이슈가 지정한 격자 그대로이며, 성과를 보기 전에 고정했다.
DEFAULT_OFFSETS_BPS: tuple[float, ...] = (0.0, 2.0, 5.0, 10.0, 20.0)

#: 채택 기본값(WAN-95/87) = 지정가 + 실시간 RSI + 롱 온리 + 볼린저 + 고정 1.5R.
#: 오프셋과 체결 가정만 여기서 덧붙인다.
#:
#: ⚠️ RSI 게이트는 **명시 고정**한다(WAN-123이 기본값을 `unconditional`로 옮겼다). 이 격자의
#: 발표 수치는 게이트가 켜진 거래 집합에서 나왔고, 오프셋 0 행이 WAN-95/96 결과와 비트
#: 단위로 맞물리는 것이 이 리포트의 검산이다 — 기본값을 따라가게 두면 그 검산이 깨진다.
#: ⚠️ 밴드 표본(`band_bar`)도 같은 이유로 고정한다(WAN-132 기본값 전환) — 그 검산은
#: 밴드 정의까지 같아야 성립한다.
BASE_PARAMS = pin_band_bar(ConfluenceParams(rsi_gate_mode=LEGACY_RSI_GATE_MODE))

#: IS 비중. 앞 2/3에서 오프셋을 고르고 뒤 1/3(OOS)로 검증한다.
IS_FRACTION = 2.0 / 3.0

SEGMENT_FULL = "full"
SEGMENT_IS = "is"
SEGMENT_OOS = "oos"


@dataclass(frozen=True)
class FillAssumption:
    """체결 가정 한 단계 (WAN-96 축을 그대로 재사용)."""

    name: str
    penetration_bps: float = 0.0
    dropout_rate: float = 0.0
    seeds: tuple[int, ...] = (0,)
    """탈락 추첨 시드들. 탈락이 있는 가정만 여러 개를 돌려 분포를 본다."""
    note: str = ""

    def params(self, offset_bps: float, seed: int) -> ConfluenceParams:
        return BASE_PARAMS.model_copy(
            update={
                "zone_limit_offset_bps": offset_bps,
                "fill_penetration_bps": self.penetration_bps,
                "fill_dropout_rate": self.dropout_rate,
                "fill_dropout_seed": seed,
            }
        )


#: 체결 가정 격자. `baseline`이 WAN-95 재현, `pen_5bp_drop_50`이 WAN-96의 최악 가정이다.
#: WAN-96은 탈락 시드를 5개 돌렸지만 여기선 오프셋 축(5배)이 곱해지므로 3개로 줄였다 —
#: 시드 분산 확인이 목적이라 3개로도 "단일 시드의 운"은 배제된다.
FILL_ASSUMPTIONS: tuple[FillAssumption, ...] = (
    FillAssumption(name="baseline", note="닿으면 체결 — WAN-95 낙관 가정"),
    FillAssumption(name="pen_5bp", penetration_bps=5.0, note="지정가 5bp 관통 요구"),
    FillAssumption(
        name="pen_5bp_drop_50",
        penetration_bps=5.0,
        dropout_rate=0.5,
        seeds=(0, 1, 2),
        note="관통 5bp + 50% 탈락 — 최악 가정(판단 기준)",
    ),
)

#: 결정이 걸린 가정. 이 이름으로 채택 판단·IS 선택을 한다.
DECISION_ASSUMPTION = "pen_5bp_drop_50"

#: IS/OOS 분할에서 돌릴 가정. 최악 가정에 결정이 걸려 있고 baseline은 대조군이다 —
#: 전 가정을 IS/OOS로 또 돌리면 비용만 2배가 되고 판단에 쓰이지 않는다.
SPLIT_ASSUMPTIONS: tuple[str, ...] = ("baseline", DECISION_ASSUMPTION)


class OffsetRow(BaseModel):
    """한 (심볼, TF, 구간, 오프셋, 가정, 시드) 실행 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    """`full`(3년 전체) / `is`(앞 2/3) / `oos`(뒤 1/3)."""
    offset_bps: float
    assumption: str
    seed: int
    penetration_bps: float
    dropout_rate: float
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    profit_factor: float | None
    sharpe: float | None
    mean_r: float | None
    eligible_setups: int
    num_filled: int
    fill_rate: float | None
    num_dropped: int
    num_penetrations: int


def _slice_segment(htf_df: pd.DataFrame, segment: str) -> pd.DataFrame:
    """구간 이름에 해당하는 상위TF 창을 잘라낸다.

    IS/OOS는 **시간 분할**이다 — 거래를 사후에 나누지 않고 창 자체를 갈라 각각 독립
    백테스트를 돌린다. 사후 분할은 사이징 자본이 IS에서 굴러온 상태라 OOS 수익률이
    IS 성과에 오염되기 때문이다(각 구간이 초기자본에서 새로 시작해야 정직하다).
    """
    if segment == SEGMENT_FULL:
        return htf_df
    times = htf_df["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    boundary = start + int((end - start) * IS_FRACTION)
    mask = times < boundary if segment == SEGMENT_IS else times >= boundary
    return htf_df[mask].reset_index(drop=True)


@dataclass(frozen=True)
class CellSpec:
    """한 (심볼, TF) 실행 단위. 프로세스 경계를 넘으므로 데이터가 아니라 좌표만 담는다."""

    symbol: str
    timeframe: str
    years: float
    db_path: str
    cache_dir: str
    offsets_bps: tuple[float, ...]


def _segment_plan(assumptions: tuple[FillAssumption, ...]) -> Iterator[tuple[str, FillAssumption]]:
    """(구간, 가정) 실행 계획. full은 전 가정, IS/OOS는 판단에 쓰는 가정만 돈다."""
    for assumption in assumptions:
        yield SEGMENT_FULL, assumption
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        for assumption in assumptions:
            if assumption.name in SPLIT_ASSUMPTIONS:
                yield segment, assumption


def run_cell(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    funding_rates: list[FundingRate],
    offsets_bps: tuple[float, ...] = DEFAULT_OFFSETS_BPS,
    assumptions: tuple[FillAssumption, ...] = FILL_ASSUMPTIONS,
    backtest_config: BacktestConfig | None = None,
) -> list[OffsetRow]:
    """한 (심볼, TF)에서 구간 × 가정 × 오프셋 × 시드를 전부 돈다."""
    cfg = backtest_config or default_backtest_config(timeframe)
    rows: list[OffsetRow] = []
    for segment, assumption in _segment_plan(assumptions):
        window = _slice_segment(htf_df, segment)
        if window.empty:
            continue
        start_ms = int(window["open_time"].iloc[0])
        window_1m = df_1m[df_1m["open_time"] >= start_ms]
        end_ms = int(window["open_time"].iloc[-1])
        if segment != SEGMENT_FULL:
            window_1m = window_1m[window_1m["open_time"] <= end_ms]
        if window_1m.empty:
            continue
        # 오더블록은 구간마다 그 창에서 새로 탐지한다 — 각 구간을 완전히 독립된
        # 백테스트로 만들기 위해서다(초기자본·존 이력·지표 워밍업 모두 새로 시작).
        # IS 존을 OOS로 물려주는 게 룩어헤드라서가 아니다(IS는 OOS보다 **과거**이므로
        # 물려줘도 미래 정보가 새지 않는다). 대가는 OOS 앞머리의 워밍업 공백인데,
        # 오프셋들이 구간 안에서 **동일한 존**을 공유하므로 이 리포트가 실제로 재는
        # 오프셋 간 상대 비교는 그 공백에 영향받지 않는다.
        ob_result = OrderBlockDetector(LEGACY_OB_PARAMS).run(window)
        segment_rates = [r for r in funding_rates if start_ms <= r.funding_time <= end_ms]
        for offset_bps in offsets_bps:
            for seed in assumption.seeds:
                result, stats = run_zone_limit_backtest_verbose(
                    window,
                    window_1m,
                    timeframe,
                    confluence_params=assumption.params(offset_bps, seed),
                    backtest_config=cfg,
                    order_block_result=ob_result,
                    funding_rates=segment_rates,
                )
                rows.append(
                    OffsetRow(
                        symbol=symbol,
                        timeframe=timeframe,
                        segment=segment,
                        offset_bps=offset_bps,
                        assumption=assumption.name,
                        seed=seed,
                        penetration_bps=assumption.penetration_bps,
                        dropout_rate=assumption.dropout_rate,
                        num_trades=result.metrics.num_trades,
                        win_rate=result.metrics.win_rate,
                        total_return=result.metrics.total_return,
                        max_drawdown=result.metrics.max_drawdown,
                        profit_factor=result.metrics.profit_factor,
                        sharpe=result.metrics.sharpe,
                        mean_r=mean_r(result, BASE_PARAMS.take_profit_r),
                        eligible_setups=stats.eligible,
                        num_filled=stats.filled,
                        fill_rate=stats.fill_rate,
                        num_dropped=stats.dropped,
                        num_penetrations=stats.penetrations,
                    )
                )
    return rows


def _run_cell_spec(spec: CellSpec) -> list[OffsetRow]:
    """워커 진입점: 좌표로 데이터를 로드해 셀을 돈다(프로세스 간 DataFrame 전송 회피)."""
    store = OhlcvStore(spec.db_path, cache_dir=spec.cache_dir)
    funding_store = FundingRateStore(spec.db_path)
    df_1m = store.load(spec.symbol, "1m")
    if df_1m.empty:
        print(f"[wan99] {spec.symbol}: 1분봉 없음 — 지정가 평가 불가, 건너뜀")
        return []
    htf_df = _load_recent(store, spec.symbol, spec.timeframe, spec.years)
    if htf_df.empty:
        return []
    start_ms = int(htf_df["open_time"].iloc[0])
    end_ms = int(htf_df["open_time"].iloc[-1])
    funding_rates = funding_store.get_rates(
        spec.symbol, start_ms=start_ms, end_ms=end_ms, include_predicted=True
    )
    window_1m = df_1m[df_1m["open_time"] >= start_ms]
    if window_1m.empty:
        return []
    rows = run_cell(
        htf_df,
        window_1m,
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        funding_rates=funding_rates,
        offsets_bps=spec.offsets_bps,
    )
    print(f"[wan99] {spec.symbol} {spec.timeframe}: {len(rows)}개 실행 완료")
    return rows


def collect_rows(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    db_path: str = _DB_PATH,
    cache_dir: str = _CACHE_DIR,
    offsets_bps: tuple[float, ...] = DEFAULT_OFFSETS_BPS,
    jobs: int = 1,
) -> list[OffsetRow]:
    """전 심볼×TF 셀을 돈다. 셀은 서로 독립이라 `jobs>1`이면 프로세스로 병렬 실행한다.

    시드가 고정돼 있고 셀 간 상태 공유가 없으므로 `jobs`는 결과에 영향을 주지 않는다
    (벽시계 시간만 줄인다).
    """
    specs = [
        CellSpec(
            symbol=symbol,
            timeframe=timeframe,
            years=years,
            db_path=db_path,
            cache_dir=cache_dir,
            offsets_bps=offsets_bps,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(specs) <= 1:
        return [row for spec in specs for row in _run_cell_spec(spec)]
    rows: list[OffsetRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(specs))) as pool:
        for cell_rows in pool.map(_run_cell_spec, specs):
            rows.extend(cell_rows)
    return rows


_TF_ORDER = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
_ASSUMPTION_ORDER = {a.name: i for i, a in enumerate(FILL_ASSUMPTIONS)}
_SEGMENT_ORDER = {SEGMENT_FULL: 0, SEGMENT_IS: 1, SEGMENT_OOS: 2}


def rows_to_frame(rows: list[OffsetRow]) -> pd.DataFrame:
    frame = pd.DataFrame([r.model_dump() for r in rows])
    if frame.empty:
        return frame
    frame["_tf"] = frame["timeframe"].map(_TF_ORDER)
    frame["_as"] = frame["assumption"].map(_ASSUMPTION_ORDER)
    frame["_sg"] = frame["segment"].map(_SEGMENT_ORDER)
    frame = frame.sort_values(["symbol", "_tf", "_sg", "_as", "offset_bps", "seed"]).drop(
        columns=["_tf", "_as", "_sg"]
    )
    return frame.reset_index(drop=True)


def build_sensitivity_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """구간 × TF × 가정 × 오프셋 요약 — 시드를 심볼별로 평균한 뒤 심볼 간 집계."""
    if frame.empty:
        return frame
    per_symbol = frame.groupby(
        ["segment", "timeframe", "assumption", "offset_bps", "symbol"], as_index=False
    ).agg(
        total_return=("total_return", "mean"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        fill_rate=("fill_rate", "mean"),
        num_trades=("num_trades", "mean"),
        mean_r=("mean_r", "mean"),
    )
    grouped = per_symbol.groupby(
        ["segment", "timeframe", "assumption", "offset_bps"], as_index=False
    ).agg(
        mean_return=("total_return", "mean"),
        worst_symbol_return=("total_return", "min"),
        positive_symbols=("total_return", lambda s: int((s > 0).sum())),
        num_symbols=("total_return", "size"),
        mean_win_rate=("win_rate", "mean"),
        mean_mdd=("max_drawdown", "mean"),
        mean_fill_rate=("fill_rate", "mean"),
        mean_trades=("num_trades", "mean"),
        mean_r=("mean_r", "mean"),
    )
    grouped["_tf"] = grouped["timeframe"].map(_TF_ORDER)
    grouped["_as"] = grouped["assumption"].map(_ASSUMPTION_ORDER)
    grouped["_sg"] = grouped["segment"].map(_SEGMENT_ORDER)
    return (
        grouped.sort_values(["_sg", "_tf", "_as", "offset_bps"])
        .drop(columns=["_tf", "_as", "_sg"])
        .reset_index(drop=True)
    )


class PlateauRow(BaseModel):
    """한 (구간, TF, 가정)의 오프셋 곡선 형태 진단."""

    model_config = ConfigDict(frozen=True)

    segment: str
    timeframe: str
    assumption: str
    best_offset_bps: float
    best_return: float
    neighbour_mean_return: float | None
    """최적 오프셋의 좌우 이웃 평균 수익률. 끝점이면 한쪽만."""
    positive_offsets: int
    """평균 수익률이 양수인 오프셋 수."""
    num_offsets: int
    is_plateau: bool | None
    """이웃이 최적값의 절반 이상을 유지하면 고원, 아니면 뾰족한 봉우리(과최적화 신호)."""


def build_plateau_frame(sensitivity: pd.DataFrame) -> pd.DataFrame:
    """오프셋 곡선이 고원인지 봉우리인지 진단한다.

    최적 오프셋의 **이웃**이 무너지면 그 최적값은 신호가 아니라 잡음일 공산이 크다 —
    파라미터를 한 칸만 움직여도 결과가 뒤집힌다는 뜻이기 때문이다. 반대로 이웃이
    비슷하면(고원) 그 구간 전체가 같은 이야기를 하고 있다는 뜻이라 믿을 만하다.

    판정 규칙(사전 고정): 최적 수익률이 양수이고 이웃 평균이 그 **절반 이상**을
    유지하면 고원. 최적이 음수면 애초에 고를 것이 없어 None.
    """
    if sensitivity.empty:
        return sensitivity
    rows: list[PlateauRow] = []
    keys = ["segment", "timeframe", "assumption"]
    for (segment, timeframe, assumption), group in sensitivity.groupby(keys, sort=False):
        ordered = group.sort_values("offset_bps").reset_index(drop=True)
        best_idx = int(ordered["mean_return"].idxmax())
        best = ordered.loc[best_idx]
        neighbours = [
            float(ordered.loc[i, "mean_return"])
            for i in (best_idx - 1, best_idx + 1)
            if 0 <= i < len(ordered)
        ]
        neighbour_mean = sum(neighbours) / len(neighbours) if neighbours else None
        best_return = float(best["mean_return"])
        plateau: bool | None = None
        if best_return > 0 and neighbour_mean is not None:
            plateau = neighbour_mean >= best_return * 0.5
        rows.append(
            PlateauRow(
                segment=str(segment),
                timeframe=str(timeframe),
                assumption=str(assumption),
                best_offset_bps=float(best["offset_bps"]),
                best_return=best_return,
                neighbour_mean_return=neighbour_mean,
                positive_offsets=int((ordered["mean_return"] > 0).sum()),
                num_offsets=len(ordered),
                is_plateau=plateau,
            )
        )
    return pd.DataFrame([r.model_dump() for r in rows])


class OosRow(BaseModel):
    """IS에서 고른 오프셋의 OOS 성적 — 과최적화 방어의 본체."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    assumption: str
    is_best_offset_bps: float
    """IS 평균 수익률이 가장 높은 오프셋(= 우리가 골랐을 값)."""
    is_best_return: float
    oos_return_at_is_best: float | None
    """그 오프셋의 OOS 평균 수익률. 여기가 마이너스면 IS 선택이 OOS로 넘어오지 못한 것."""
    oos_best_offset_bps: float | None
    """OOS에서 사후적으로 가장 좋았던 오프셋(선택이 아니라 대조용)."""
    oos_best_return: float | None
    oos_return_at_zero: float | None
    """오프셋 0(현행 기본값)의 OOS 평균 수익률 — 오프셋이 실제로 보탬이 됐는지의 기준선."""
    survives_oos: bool | None
    """IS 선택 오프셋이 OOS에서 플러스이고 오프셋 0보다 나은가."""


def build_oos_frame(sensitivity: pd.DataFrame) -> pd.DataFrame:
    """IS에서 오프셋을 고르고 OOS 성적을 붙인다(최고 오프셋을 그냥 채택하지 않기 위해)."""
    if sensitivity.empty:
        return sensitivity
    rows: list[OosRow] = []
    is_frame = _select(sensitivity, segment=SEGMENT_IS)
    oos_frame = _select(sensitivity, segment=SEGMENT_OOS)
    for (timeframe, assumption), group in is_frame.groupby(["timeframe", "assumption"], sort=False):
        best = group.loc[group["mean_return"].idxmax()]
        best_offset = float(best["offset_bps"])
        oos_group = oos_frame[
            (oos_frame["timeframe"] == timeframe) & (oos_frame["assumption"] == assumption)
        ]
        oos_at_best = oos_group[oos_group["offset_bps"] == best_offset]["mean_return"]
        oos_at_zero = oos_group[oos_group["offset_bps"] == 0.0]["mean_return"]
        at_best = float(oos_at_best.iloc[0]) if not oos_at_best.empty else None
        at_zero = float(oos_at_zero.iloc[0]) if not oos_at_zero.empty else None
        if oos_group.empty:
            oos_best_offset, oos_best_return = None, None
        else:
            oos_best = oos_group.loc[oos_group["mean_return"].idxmax()]
            oos_best_offset = float(oos_best["offset_bps"])
            oos_best_return = float(oos_best["mean_return"])
        survives: bool | None = None
        if at_best is not None and at_zero is not None:
            survives = at_best > 0 and at_best > at_zero
        rows.append(
            OosRow(
                timeframe=str(timeframe),
                assumption=str(assumption),
                is_best_offset_bps=best_offset,
                is_best_return=float(best["mean_return"]),
                oos_return_at_is_best=at_best,
                oos_best_offset_bps=oos_best_offset,
                oos_best_return=oos_best_return,
                oos_return_at_zero=at_zero,
                survives_oos=survives,
            )
        )
    return pd.DataFrame([r.model_dump() for r in rows])


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:.2f}%"


def _fmt_r(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value:+.3f}R"


def _offsets_label(offsets_bps: tuple[float, ...]) -> str:
    return ", ".join(f"{v:.0f}bp" for v in offsets_bps)


def _select(frame: pd.DataFrame, *, segment: str, assumption: str | None = None) -> pd.DataFrame:
    """민감도표에서 구간(+가정)을 고른다. 빈 표는 컬럼조차 없으므로 먼저 막는다.

    부분 실행(`--symbols`/`--timeframes`로 좁히거나 데이터 결측)에서 표가 비면 렌더가
    통째로 죽는데, 그러면 나머지 결과까지 못 본다.
    """
    if frame.empty:
        return frame
    rows = frame[frame["segment"] == segment]
    if assumption is not None:
        rows = rows[rows["assumption"] == assumption]
    return rows


def _fmt_bool(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    return "예" if bool(value) else "아니오"


def _answer_lines(sensitivity: pd.DataFrame) -> list[str]:
    """ "보수 가정에서 살아남는 오프셋이 있는가"에 예/아니오로 답한다(완료기준)."""
    lines = ["## 질문: 보수 가정에서 살아남는 오프셋이 있는가", ""]
    decision = _select(sensitivity, segment=SEGMENT_FULL, assumption=DECISION_ASSUMPTION)
    if decision.empty:
        return lines + ["최악 가정 실행 결과가 없어 답할 수 없다.", ""]

    for timeframe, group in decision.groupby("timeframe", sort=False):
        survivors = group[group["mean_return"] > 0]
        best = group.loc[group["mean_return"].idxmax()]
        if survivors.empty:
            lines += [
                f"- **{timeframe}: 아니오.** 최악 가정(`{DECISION_ASSUMPTION}`)에서 "
                f"{len(group)}개 오프셋 **전부** 평균 수익률이 마이너스다. 가장 나은 "
                f"{best['offset_bps']:.0f}bp조차 {_fmt_pct(best['mean_return'])}이다. "
                "체결 확실성을 사는 대가(1R 확대)가 이득보다 크다.",
            ]
        else:
            offsets = ", ".join(f"{v:.0f}bp" for v in survivors["offset_bps"])
            all_positive = bool((survivors["positive_symbols"] == survivors["num_symbols"]).all())
            lines += [
                f"- **{timeframe}: 예.** 최악 가정에서 평균 수익률이 플러스인 오프셋이 "
                f"{len(survivors)}개 있다({offsets}). 최고는 {best['offset_bps']:.0f}bp의 "
                f"{_fmt_pct(best['mean_return'])}"
                + (
                    "이며, 해당 오프셋들은 3심볼 전부 플러스다."
                    if all_positive
                    else "이지만, 심볼별로 고르지 않다(일부 심볼 마이너스)."
                ),
            ]
    if not decision[decision["mean_return"] > 0].empty:
        lines += [
            "",
            "⚠️ **'존재한다'와 '채택한다'는 다르다.** 위 플러스는 3년 전체를 다 보고 뒤에서 "
            "고른 값이라, 미리 고를 수 있었는지는 아직 답하지 않았다. 아래 과최적화 방어 "
            "두 관문(IS→OOS 검증, 고원 여부)을 통과해야 채택 근거가 된다 — 최종 결론은 "
            "**오프셋 채택/미채택 권고** 절에 있다.",
        ]
    lines += [""]
    return lines


def _tradeoff_lines(sensitivity: pd.DataFrame) -> list[str]:
    """오프셋이 실제로 체결을 사고 진입가를 팔았는지 — 표에서 계산해 확인한다.

    오프셋이 체결률을 못 올렸다면 이 실험은 애초에 성립하지 않는다(가설의 전제 확인).
    """
    decision = _select(sensitivity, segment=SEGMENT_FULL, assumption=DECISION_ASSUMPTION)
    if decision.empty:
        return []
    lines = ["### 오프셋은 실제로 무엇을 샀나", ""]
    for timeframe, group in decision.groupby("timeframe", sort=False):
        ordered = group.sort_values("offset_bps")
        low, high = ordered.iloc[0], ordered.iloc[-1]
        lines.append(
            f"- **{timeframe}**: 오프셋 {low['offset_bps']:.0f}bp → {high['offset_bps']:.0f}bp에서 "
            f"체결률 {_fmt_pct(low['mean_fill_rate'])} → {_fmt_pct(high['mean_fill_rate'])}, "
            f"거래 {low['mean_trades']:.0f} → {high['mean_trades']:.0f}건, "
            f"승률 {_fmt_pct(low['mean_win_rate'])} → {_fmt_pct(high['mean_win_rate'])}, "
            f"평균 수익률 {_fmt_pct(low['mean_return'])} → {_fmt_pct(high['mean_return'])}."
        )
    lines += ["", "**교환 자체가 지독하게 불리하다.**", ""]
    for timeframe, group in decision.groupby("timeframe", sort=False):
        ordered = group.sort_values("offset_bps")
        low, high = ordered.iloc[0], ordered.iloc[-1]
        fill_gain = high["mean_fill_rate"] - low["mean_fill_rate"]
        win_drop = low["mean_win_rate"] - high["mean_win_rate"]
        lines.append(
            f"- **{timeframe}**: 오프셋을 0 → {high['offset_bps']:.0f}bp까지 밀어도 체결률은 "
            f"**{fill_gain * 100:+.2f}%p**밖에 못 산다. 그 대가로 승률이 "
            f"**{win_drop * 100:.2f}%p** 무너진다."
        )
    lines += [
        "",
        "왜 이렇게 적게 사는가: **가격은 존에 닿을 때 대개 20bp쯤은 우습게 지나쳐 버린다.** "
        "지정가를 20bp 마중 내보내서 새로 잡히는 체결은 '존 근단에 20bp 이내로 다가왔다가 "
        "되돌아선' 셋업뿐인데, 그런 셋업은 애초에 드물다. 즉 오프셋이 **버는 쪽**은 얇은 "
        "꼬리에만 걸려 있다.",
        "",
        "반면 **내는 쪽은 모든 거래에 걸린다.** 오프셋은 이미 체결됐을 거래의 진입가까지 "
        "전부 나쁘게 만든다 — 1R이 커지고, 고정 1.5R 익절이 그만큼 멀어지고, 그래서 승률이 "
        "떨어진다(위 숫자가 정확히 그 경로다). 소수의 한계 체결을 사려고 **전 거래에 "
        "세금을 매기는 셈**이라, 교환이 성립할 여지 자체가 좁다.",
        "",
    ]
    return lines


def _verdict_lines(
    sensitivity: pd.DataFrame, plateau: pd.DataFrame, oos: pd.DataFrame
) -> list[str]:
    """오프셋 채택/미채택 권고 — 숫자는 표에서 읽는다(재실행하면 결론도 갱신된다)."""
    lines = ["## 오프셋 채택/미채택 권고", ""]
    decision = _select(sensitivity, segment=SEGMENT_FULL, assumption=DECISION_ASSUMPTION)
    if decision.empty:
        return lines + ["판단 근거가 되는 실행 결과가 없다.", ""]

    survives_any = False
    if not oos.empty:
        decision_oos = oos[oos["assumption"] == DECISION_ASSUMPTION]
        survives_any = bool(decision_oos["survives_oos"].fillna(False).any())
    decision_plateau = _select(plateau, segment=SEGMENT_FULL, assumption=DECISION_ASSUMPTION)
    plateau_ok = (
        bool(decision_plateau["is_plateau"].fillna(False).any())
        if not decision_plateau.empty
        else False
    )
    any_positive = bool((decision["mean_return"] > 0).any())

    if any_positive and survives_any and plateau_ok:
        verdict = (
            "**조건부 채택 권고.** 최악 체결 가정에서도 플러스를 유지하는 오프셋이 있고, "
            "그 선택이 IS→OOS로 넘어오며, 오프셋 곡선이 고원이라 한 칸 어긋나도 결론이 "
            "뒤집히지 않는다. 세 관문을 모두 통과했으므로 WAN-96의 제외 권고를 오프셋 "
            "채택을 전제로 재검토할 수 있다. 다만 아래 한계(큐 모델 부재)가 남아 있으므로 "
            "소액·모니터링을 전제로 한다."
        )
    elif any_positive and not survives_any:
        verdict = (
            "**미채택 권고.** 최악 가정에서 플러스인 오프셋이 표에 보이기는 하지만, "
            "**IS에서 고른 오프셋이 OOS에서 살아남지 못한다** — 즉 그 플러스는 전 구간을 "
            "다 보고 뒤에서 고른 값이지, 미리 고를 수 있었던 값이 아니다. 이것이 바로 "
            "과최적화의 정의이므로 채택하지 않는다."
        )
    elif any_positive and not plateau_ok:
        verdict = (
            "**미채택 권고.** 최악 가정에서 플러스인 오프셋이 있으나 오프셋 곡선이 **뾰족한 "
            "봉우리**다 — 이웃 오프셋에서 성과가 무너진다. 파라미터를 한 칸만 움직여도 "
            "결론이 뒤집힌다면 그 최적값은 신호가 아니라 잡음이다."
        )
    else:
        verdict = (
            "**미채택 권고.** 최악 체결 가정에서 **어떤 오프셋도** 평균 수익률을 플러스로 "
            "돌리지 못한다. 체결 확실성을 사는 대가(진입가 악화 → 1R 확대 → 익절 목표 "
            "원거리화)가 늘어난 체결의 이득보다 크다는 뜻이다. WAN-96의 15m 제외 권고는 "
            "오프셋으로 뒤집히지 않으며, 기본값 `zone_limit_offset_bps=0.0`을 유지한다. "
            "🔁 **이 권고는 [WAN-112](../../docs/decisions/wan112.md)가 번복했다 — 채택 "
            "기본값은 이제 2bp다.** 여기서 근거로 쓴 「최악 체결 가정」(`pen_5bp_drop_50`)은 "
            "공식 렌즈가 아니라 스트레스 축이고(WAN-104가 공식을 `baseline`으로 확정), "
            "WAN-112는 그 축의 손익이 아니라 **체결 신뢰성**을 사려고 2bp를 얹었다."
        )
    lines += [verdict, ""]
    lines += _tradeoff_lines(sensitivity)
    return lines


def _sensitivity_table(frame: pd.DataFrame, segment: str) -> list[str]:
    rows = _select(frame, segment=segment)
    if rows.empty:
        return ["해당 구간 결과가 없다.", ""]
    lines = [
        "| TF | 가정 | 오프셋 | 평균 return | 최악 심볼 | 플러스 심볼 | 평균 승률 | "
        "평균 MDD | 평균 체결률 | 평균 거래수 | 평균 R |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for _, row in rows.iterrows():
        lines.append(
            f"| {row['timeframe']} | `{row['assumption']}` | {row['offset_bps']:.0f}bp | "
            f"{_fmt_pct(row['mean_return'])} | {_fmt_pct(row['worst_symbol_return'])} | "
            f"{int(row['positive_symbols'])}/{int(row['num_symbols'])} | "
            f"{_fmt_pct(row['mean_win_rate'])} | {_fmt_pct(row['mean_mdd'])} | "
            f"{_fmt_pct(row['mean_fill_rate'])} | {row['mean_trades']:.1f} | "
            f"{_fmt_r(row['mean_r'])} |"
        )
    return lines + [""]


def build_markdown(sensitivity: pd.DataFrame, plateau: pd.DataFrame, oos: pd.DataFrame) -> str:
    lines = [
        "# WAN-99: 지정가 오프셋 민감도",
        "",
        "**재현**: `python -m backtest.wan99_zone_limit_offset_report`",
        "",
        "## 무엇을 묻는가",
        "",
        "WAN-96에서 15m이 무너진 이유는 수익이 **지정가를 스치듯 닿고 되돌아간 체결**에 "
        "몰려 있었기 때문이다 — 그런 체결은 실거래에서 큐 우선순위 때문에 가장 안 될 "
        "체결이다. 그런데 관통 요구를 만족시키는 방법이 하나 더 있다: **애초에 지정가를 "
        "그만큼 안쪽(가격이 오는 방향)에 거는 것**이다. 롱이면 존 근단보다 위, 숏이면 "
        "아래. 그러면 가격이 내 레벨을 관통해 지나가므로 체결이 사실상 보장된다.",
        "",
        "대가는 진입가다. 롱을 더 높이 잡으면 손절(오더블록 무효화)까지 거리가 늘어 **1R이 "
        "커지고** 고정 1.5R 익절 목표도 그만큼 멀어진다. 즉 **체결 확실성 ↔ 진입가**의 "
        "교환이며, 어느 쪽이 이기는지는 스윕해야 안다.",
        "",
        "## 격자",
        "",
        "| 축 | 값 |",
        "| -- | -- |",
        f"| 오프셋(`zone_limit_offset_bps`) | {_offsets_label(DEFAULT_OFFSETS_BPS)} |",
        f"| 체결 가정 | {', '.join(f'`{a.name}`' for a in FILL_ASSUMPTIONS)} |",
        f"| 심볼 | {', '.join(DEFAULT_SYMBOLS)} |",
        f"| TF | {', '.join(DEFAULT_TIMEFRAMES)} |",
        f"| 구간 | 최근 {DEFAULT_YEARS:.0f}년 (IS = 앞 2/3, OOS = 뒤 1/3) |",
        "",
        "| 가정 | 관통 요구 | 탈락률 | 시드 | 설명 |",
        "| -- | --: | --: | --: | -- |",
    ]
    for assumption in FILL_ASSUMPTIONS:
        seeds = ", ".join(str(s) for s in assumption.seeds)
        lines.append(
            f"| `{assumption.name}` | {assumption.penetration_bps:.0f}bp | "
            f"{assumption.dropout_rate * 100:.0f}% | {seeds} | {assumption.note} |"
        )
    lines += [
        "",
        "오프셋 격자와 체결 가정은 **성과를 보기 전에** 고정했다(이슈가 지정한 값 그대로). "
        "이 이슈는 기본값을 바꾸지 않았다 — 당시 `zone_limit_offset_bps=0.0`이 "
        "`ConfluenceParams()` 그대로였고, 이 격자의 오프셋 0 행이 WAN-95/96 결과를 비트 "
        "단위로 재현한다. 🔁 **그 뒤 [WAN-112](../../docs/decisions/wan112.md)가 채택 "
        "기본값을 2bp로 올렸다**(사용자 판단). 이 격자는 오프셋을 **축으로 명시**해 돌므로 "
        "아래 수치는 그 변경과 무관하게 그대로이나, **오프셋 0 행은 더 이상 「기본값」이 "
        "아니라 「명시적 0bp」**로 읽어야 한다.",
        "",
    ]

    lines += _answer_lines(sensitivity)
    lines += ["## 전 구간(3년) 민감도표: 오프셋 × 체결 가정", ""]
    lines += _sensitivity_table(sensitivity, SEGMENT_FULL)

    lines += [
        "## 과최적화 방어 1: IS에서 고르고 OOS로 검증",
        "",
        "앞 2/3(IS)에서 **평균 수익률이 가장 높은 오프셋을 고른 뒤**, 그 선택을 뒤 1/3(OOS)에 "
        "그대로 적용한다. 오프셋 0(현행 기본값)의 OOS 성적이 비교 기준이다 — 고른 오프셋이 "
        "OOS에서 플러스이고 **오프셋 0보다 나아야** 채택할 이유가 생긴다. 구간은 시간으로 "
        "가르고 각 구간을 독립 백테스트로 돌린다(초기자본·존 이력·지표 워밍업 모두 새로 "
        "시작). 그래서 구간별 절대 수익률에는 워밍업 공백이 섞여 있지만, 오프셋들이 구간 "
        "안에서 동일한 존을 공유하므로 **오프셋 간 비교**는 그 영향을 받지 않는다.",
        "",
    ]
    if oos.empty:
        lines += ["IS/OOS 실행 결과가 없다.", ""]
    else:
        lines += [
            "| TF | 가정 | IS 최적 오프셋 | IS return | OOS return(그 오프셋) | "
            "OOS return(0bp) | OOS 사후 최적 | OOS 생존 |",
            "| -- | -- | --: | --: | --: | --: | --: | --: |",
        ]
        for _, row in oos.iterrows():
            oos_best = (
                "n/a"
                if row["oos_best_offset_bps"] is None or pd.isna(row["oos_best_offset_bps"])
                else f"{row['oos_best_offset_bps']:.0f}bp ({_fmt_pct(row['oos_best_return'])})"
            )
            lines.append(
                f"| {row['timeframe']} | `{row['assumption']}` | "
                f"{row['is_best_offset_bps']:.0f}bp | {_fmt_pct(row['is_best_return'])} | "
                f"{_fmt_pct(row['oos_return_at_is_best'])} | "
                f"{_fmt_pct(row['oos_return_at_zero'])} | {oos_best} | "
                f"{_fmt_bool(row['survives_oos'])} |"
            )
        lines += [""]

    lines += [
        "## 과최적화 방어 2: 고원인가 봉우리인가",
        "",
        "최적 오프셋의 **이웃**이 무너지면 그 최적값은 신호가 아니라 잡음이다 — 파라미터를 "
        "한 칸만 움직여도 결론이 뒤집힌다는 뜻이기 때문이다. 이웃 평균이 최적값의 절반 "
        "이상을 유지하면 고원으로 본다(판정 규칙은 성과를 보기 전에 고정).",
        "",
    ]
    if plateau.empty:
        lines += ["진단 대상이 없다.", ""]
    else:
        lines += [
            "| 구간 | TF | 가정 | 최적 오프셋 | 최적 return | 이웃 평균 | 플러스 오프셋 | 고원? |",
            "| -- | -- | -- | --: | --: | --: | --: | --: |",
        ]
        for _, row in plateau.iterrows():
            lines.append(
                f"| {row['segment']} | {row['timeframe']} | `{row['assumption']}` | "
                f"{row['best_offset_bps']:.0f}bp | {_fmt_pct(row['best_return'])} | "
                f"{_fmt_pct(row['neighbour_mean_return'])} | "
                f"{int(row['positive_offsets'])}/{int(row['num_offsets'])} | "
                f"{_fmt_bool(row['is_plateau'])} |"
            )
        lines += [""]

    lines += ["## IS 구간 민감도표", ""]
    lines += _sensitivity_table(sensitivity, SEGMENT_IS)
    lines += ["## OOS 구간 민감도표", ""]
    lines += _sensitivity_table(sensitivity, SEGMENT_OOS)

    lines += _verdict_lines(sensitivity, plateau, oos)

    lines += [
        "## 한계",
        "",
        "1. **큐 근사는 여전히 큐가 아니다.** 오프셋은 '관통하면 체결된다'를 가정하는데, "
        "실거래에서는 관통해도 내 앞 물량이 크면 체결되지 않을 수 있다. 탈락률로 그 "
        "방향을 감싸지만 호가창 깊이·주문 크기를 모델링하지는 못한다(WAN-96의 한계 "
        "그대로 — 틱·호가 데이터가 필요하다).",
        "2. **오프셋은 슬리피지가 아니다.** 오프셋을 걸어도 체결가는 그 지정가 **그대로**이고 "
        "메이커 수수료가 붙는다(WAN-95 비용 모델). 실거래에서 마중 나간 주문이 테이커로 "
        "전환되는 경우(가격이 이미 내 레벨을 지나 있는 채로 주문이 걸리는 경우)는 "
        "모델링하지 않았다.",
        "3. **룩어헤드 잔존**: 볼린저 진입가가 탭 봉 SMA20(그 봉 종가 포함)으로 계산되는데 "
        "체결은 봉 내부에서 일어날 수 있다(CLAUDE.md 참고). WAN-99는 이 성질을 바꾸지 "
        "않는다 — 오프셋은 그 볼린저 가격 **위에** 얹힐 뿐이다.",
        "4. **평균 R은 1R 확대를 못 본다.** R로 정규화하면 '1R이 얼마짜리인가'가 나눠져 "
        "사라지므로, 오프셋이 지불한 대가는 total_return에서 봐야 한다. 표의 평균 R은 "
        "승/패 구성만 보여주는 보조 지표다.",
        "5. **IS/OOS 구간은 콜드 스타트다.** 구간마다 오더블록을 새로 탐지하므로 구간 "
        "앞머리에는 존·지표 워밍업 공백이 있고, IS 끝에 열린 포지션은 OOS를 넘겨다보지 "
        "않도록 강제 청산된다. 따라서 구간별 **절대** 수익률은 전 구간 수치와 직접 "
        "비교할 수 없다 — 이 표가 재는 것은 같은 구간 안에서의 **오프셋 간 상대 비교**다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-99 지정가 오프셋 민감도")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--offsets-bps", nargs="+", type=float, default=list(DEFAULT_OFFSETS_BPS))
    parser.add_argument("--db-path", default=_DB_PATH)
    parser.add_argument("--cache-dir", default=_CACHE_DIR)
    parser.add_argument("--out-dir", default="backtest/reports")
    parser.add_argument(
        "--jobs",
        type=int,
        default=min(6, os.cpu_count() or 1),
        help="셀(심볼×TF) 병렬 실행 수. 결과에는 영향이 없고 벽시계 시간만 줄인다.",
    )
    args = parser.parse_args()

    rows = collect_rows(
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
        offsets_bps=tuple(args.offsets_bps),
        jobs=args.jobs,
    )
    frame = rows_to_frame(rows)
    sensitivity = build_sensitivity_frame(frame)
    plateau = build_plateau_frame(sensitivity)
    oos = build_oos_frame(sensitivity)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_path = out_dir / "wan99_zone_limit_offset.csv"
    sens_path = out_dir / "wan99_zone_limit_offset_sensitivity.csv"
    plateau_path = out_dir / "wan99_offset_plateau.csv"
    oos_path = out_dir / "wan99_offset_oos.csv"
    md_path = out_dir / "wan99_zone_limit_offset_summary.md"
    frame.to_csv(runs_path, index=False)
    sensitivity.to_csv(sens_path, index=False)
    plateau.to_csv(plateau_path, index=False)
    oos.to_csv(oos_path, index=False)
    md_path.write_text(build_markdown(sensitivity, plateau, oos), encoding="utf-8")
    for path in (runs_path, sens_path, plateau_path, oos_path, md_path):
        print(f"[wan99] 저장: {path}")


if __name__ == "__main__":
    main()
