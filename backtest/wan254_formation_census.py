"""WAN-254 §1 — 오더블록 형성(돌파) 진입 전제 검증 census (전략 없이, 싸다).

## 무엇을 세나 (사용자 가설, 2026-08-05)

이 저장소의 **모든 진입 테스트는 재탭(re-tap) 기반**이다 — 신호가 오더블록 **탭**(가격이
존으로 되돌아온 자리)이라, 확정(돌파) 직후 되돌아오지 않고 날아간 OB는 애초에 후보가 되지
않는다. 사용자 가설: **제일 센 변위(displacement) OB — 제일 큰 기관 발자국 — 는 되돌아오지
않고 날아간다** → 재탭 엔진은 그 최고 셋업들을 **구조적으로 놓친다**(생존편향).

전략을 짜기 전에 **가설이 참인지부터** 잰다(WAN-90/137식 Phase 분리). 모든 확정 OB에 대해:

* **(a) 되돌아옴 비율** — OB가 무효화(breaker)되기 전에 존에 탭이 한 번이라도 있었나
  (`len(tapped_times) > 0`). = 재탭 엔진이 잡는 비율.
* **(b) 변위 강도 × 되돌아옴 비율** — 변위가 셀수록 되돌아옴 비율이 **내려가나**(= 센 놈을
  재탭 엔진이 덜 잡나 = 생존편향의 직접 증거). 변위 강도 = **돌파 강도**(스윙 초과폭 ÷ ATR,
  **확정 시점** 값이라 룩어헤드 없음 — `OrderBlock.displacement_atr`, WAN-254 엔진 필드).
* **(c) 안 되돌아온 OB의 형성진입 MFE/MAE(R) 분포** — 놓친 돈이 얼마나 되나(무검열, 셋업
  단위). 형성 진입 = 확정 **다음 봉 시가**(변형 B, 깨끗한 모멘텀 · 봉내 룩어헤드 없음) ·
  손절 = OB 무효화 경계(1R) · 고정 1.5R 익절.

## 판정 (전제 성립 여부)

전제(「센 변위를 놓친다」)가 성립하려면 **둘 다** 참이어야 한다:
1. (b) 변위-되돌아옴이 **음의 관계**(강한 변위일수록 덜 되돌아옴, `disp_retrace_delta < 0`).
2. (c) 안 되돌아온 OB의 형성 진입이 **순양수**(`never_net_r_mean > 0`).

**(b)가 무관/양수이거나 (c)가 손실이면 → 전제 거짓 → Phase 2 착수 안 함**(전제 미성립으로
닫는다). 판정 게이트는 심볼당 OB 20개 이상(WAN-84 유효 기준).

## 왜 형성이 재탭과 다른가 — 구조적 관찰

불리시 OB가 무효화(`break_time`)되려면 가격이 존 아래로 관통해야 하는데, 그러려면 먼저
존을 통과하며 **탭**을 남긴다. 그래서 **안 탭된(never-retraced) 불리시 OB는 거의 안
깨진다** — 날아가거나 데이터 끝까지 산다. 이것이 재탭 엔진이 구조적으로 못 보는 표본이고,
이 census가 그 표본의 형성 진입 손익을 처음으로 잰다.

## 성격 · 경고

측정 전용(기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지). 핀 없음
(`OrderBlockParams()` = 오늘 엔진 · 분리 존 WAN-149). 못 박은 6년 창(WAN-182) · 9종목 ·
15m·1h·2h·4h · warm/cold 구간(WAN-166 규약을 census에 적용: 탐지는 전 구간 연속(warm),
평가는 형성 시각이 경계 이후인 OB만). ⚠️ **형성은 테이커**(시장가)라 `baseline`「닿으면
체결」 낙관에 덜 기대지만 **테이커 비용(진입 4bp+슬리피지 5bp, 청산 동일)**을 문다 —
`net_r`이 그 비용을 뺀 값이다. ⚠️ **§1 net_r은 펀딩을 뺀다**(신규 3종목 0행 무관 · 펀딩은
방향 손익에 들어가는 §2 소관). ⚠️ 「엣지 없음」(WAN-84/88/111/114/124/151/201/248)은 **다른
질문**(재탭 진입 규칙이 무작위와 구분되나)이라 이 census가 뒤집지 않는다.

## 재현

```
uv run python -m backtest.wan254_formation_census --tf 4h,2h,1h --jobs 6
uv run python -m backtest.wan254_formation_census --tf 15m --jobs 6   # 무거움(탐지+walk)
uv run python -m backtest.wan254_formation_census --from-csv          # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.run import parse_date_ms
from strategy.models import OrderBlockDirection
from strategy.order_blocks import detect_order_blocks

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan254_formation_census.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan254_formation_census_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
ALL_SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS

#: 기본 TF = 4h·2h·1h(가볍다). 15m은 탐지+walk가 무거워 별도 실행 권장.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "2h", "1h")

#: 형성 진입 = 확정 다음 봉 시가 시장가 · 고정 1.5R 익절.
TAKE_PROFIT_R = 1.5

#: 테이커 비용(왕복). 진입 시장가(4bp) + 슬리피지(5bp) = 9bp, 청산도 테이커 9bp = 18bp.
#: `BacktestConfig` 기본값(fee_rate=0.0004 · slippage=0.0005)과 같은 값.
TAKER_ROUNDTRIP = 2 * (0.0004 + 0.0005)

#: 판정 게이트 — 심볼당 확정 OB 20개(WAN-84/143/248 유효 기준).
MIN_OBS_FOR_VERDICT = 20

#: (b) 효과 크기 문턱 — 변위-되돌아옴 델타가 이보다 작으면(절대값) 「무관」으로 본다.
#: 이슈 판정 기준의 "무관"을 부호가 아니라 **크기**로 읽는다: 강한 변위가 되돌아옴률을
#: 2%p도 못 낮추면 생존편향은 사실상 없다(부호만 음수여도 무의미).
NEGLIGIBLE_DELTA = 0.02

#: (b-시간) 효과 크기 문턱(봉) — 강한 변위가 되돌아오기까지 시간을 이보다 덜 늘리면 「무관」.
#: 되돌아오기까지 중앙값이 ~25~32봉이라 3봉(≈10%+ 지연)이면 의미 있는 비행 연장으로 본다.
NEGLIGIBLE_BARS = 3.0

#: 신규 3종목 — 펀딩 0행(WAN-178 백필 전). §1 net_r은 펀딩을 애초에 안 붙이므로 편향 없음.
#: 표에서 †로 표시만 한다(펀딩은 §2 방향 손익 소관).
FUNDING_GAP_SYMBOLS: frozenset[str] = frozenset(
    harness.normalize_symbol(s) for s in ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
)

SEGMENTS: tuple[str, ...] = ("full", "is", "oos_warm")
DIRECTIONS: tuple[str, ...] = ("long", "short")


# --------------------------------------------------------------------------- #
# 형성 진입 결과 (한 OB)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ObRecord:
    """확정 OB 하나의 census 레코드."""

    segment: str
    direction: str
    displacement_atr: float | None
    retraced: bool
    broke: bool
    bars_to_retrace: int | None
    """확정 후 첫 탭까지의 봉 수. 안 되돌아온(검열) OB는 `None`(§1(a) 생존곡선 축)."""
    # 형성 진입(다음 봉 시가)이 가능한 경우에만 채워진다.
    mfe_r: float | None
    mae_r: float | None
    gross_r: float | None
    net_r: float | None


def _formation_outcome(
    *,
    is_long: bool,
    entry_price: float,
    stop: float,
    entry_idx: int,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    tp_r: float,
) -> tuple[float, float, float, bool] | None:
    """형성 진입(entry_idx 봉부터)의 무검열 MFE/MAE(R) + 고정 R gross_r + 무효화 여부.

    한 번의 순방향 walk로:
    * **무검열 MFE/MAE** — 진입부터 **무효화(손절)** 또는 데이터 끝까지 최대 유리/불리
      변위(익절로 자르지 않는다 — "얼마나 갔나"를 잰다, WAN-90 무검열 방식).
    * **고정 1.5R gross_r** — 손절/익절 중 **먼저 닿은 쪽**(+tp_r / −1.0), 둘 다 안 닿으면
      데이터 끝 종가의 부분 R. 같은 봉에 둘 다 닿으면 **보수적으로 손절**(−1.0).

    `None` 반환 = 진입 불가(risk ≤ 0 등, 방어적).
    """
    n = len(closes)
    if entry_idx >= n:
        return None
    risk = (entry_price - stop) if is_long else (stop - entry_price)
    if risk <= 0:
        return None
    tp_level = entry_price + tp_r * risk if is_long else entry_price - tp_r * risk

    max_high = highs[entry_idx]
    min_low = lows[entry_idx]
    gross: float | None = None
    tp_hit = False
    broke = False
    for j in range(entry_idx, n):
        hi = highs[j]
        lo = lows[j]
        if hi > max_high:
            max_high = hi
        if lo < min_low:
            min_low = lo
        stop_now = lo <= stop if is_long else hi >= stop
        tp_now = hi >= tp_level if is_long else lo <= tp_level
        if stop_now:
            broke = True
            if not tp_hit:
                gross = -1.0
            break  # 무검열 MFE/MAE는 무효화에서 끝난다.
        if tp_now and not tp_hit:
            tp_hit = True
            gross = tp_r  # 규칙상 여기서 익절 청산 — MFE는 계속 잰다.
    if gross is None:
        # 손절·익절 어느 쪽도 안 닿음 → 데이터 끝 종가의 부분 R.
        terminal = closes[n - 1]
        gross = (terminal - entry_price) / risk if is_long else (entry_price - terminal) / risk

    if is_long:
        mfe_r = (max_high - entry_price) / risk
        mae_r = (entry_price - min_low) / risk
    else:
        mfe_r = (entry_price - min_low) / risk
        mae_r = (max_high - entry_price) / risk
    return mfe_r, mae_r, gross, broke


def _cost_r(entry_price: float, stop: float, is_long: bool) -> float:
    """테이커 왕복 비용을 R 단위로 환산(근사: 두 다리 모두 진입가 명목)."""
    risk = (entry_price - stop) if is_long else (stop - entry_price)
    if risk <= 0:
        return 0.0
    return TAKER_ROUNDTRIP * entry_price / risk


# --------------------------------------------------------------------------- #
# 셀 실행 (한 심볼·TF)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def _segment_of(entry_time: int, boundary_ms: int) -> str:
    return "is" if entry_time < boundary_ms else "oos_warm"


def _records_for_cell(task: _Task) -> list[_ObRecord]:
    """한 (심볼, TF)의 확정 OB마다 census 레코드를 만든다(핀 없는 오늘 엔진)."""
    market = harness.load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=False,
        funding=False,
    )
    if market.empty:
        return []
    frame = market.htf_df
    times = frame["open_time"].astype("int64").tolist()
    opens = frame["open"].astype(float).tolist()
    highs = frame["high"].astype(float).tolist()
    lows = frame["low"].astype(float).tolist()
    closes = frame["close"].astype(float).tolist()
    n = len(times)
    if n < 2:
        return []

    # warm/cold 경계 = 전 구간 첫/끝 봉에서 IS_FRACTION(WAN-166 `eval_boundary_ms`와 같은 식).
    boundary_ms = times[0] + int((times[-1] - times[0]) * harness.IS_FRACTION)
    pos_by_time = {t: i for i, t in enumerate(times)}

    result = detect_order_blocks(frame)  # ob_params=None → 채택 기본값(분리 존, 오늘 엔진).
    records: list[_ObRecord] = []
    for ob in result.order_blocks:
        is_long = ob.direction is OrderBlockDirection.BULLISH
        direction = "long" if is_long else "short"
        retraced = len(ob.tapped_times) > 0
        broke_detector = ob.break_time is not None

        confirm_pos = pos_by_time.get(ob.confirmed_time)
        # §1(a) 되돌아오기까지의 봉 수(생존곡선 축). 안 되돌아온 OB는 None(검열).
        bars_to_retrace: int | None = None
        if retraced and confirm_pos is not None:
            first_tap_pos = pos_by_time.get(ob.tapped_times[0])
            if first_tap_pos is not None:
                bars_to_retrace = first_tap_pos - confirm_pos
        mfe_r: float | None = None
        mae_r: float | None = None
        gross_r: float | None = None
        net_r: float | None = None
        broke = broke_detector
        entry_time: int | None = None
        if confirm_pos is not None and confirm_pos + 1 < n:
            entry_idx = confirm_pos + 1
            entry_time = times[entry_idx]
            entry_price = opens[entry_idx]
            stop = ob.bottom if is_long else ob.top
            outcome = _formation_outcome(
                is_long=is_long,
                entry_price=entry_price,
                stop=stop,
                entry_idx=entry_idx,
                highs=highs,
                lows=lows,
                closes=closes,
                tp_r=TAKE_PROFIT_R,
            )
            if outcome is not None:
                mfe_r, mae_r, gross_r, broke = outcome
                net_r = gross_r - _cost_r(entry_price, stop, is_long)

        # 세그먼트: 형성 진입 시각(다음 봉) 기준. 진입 불가면 확정 시각으로.
        marker = entry_time if entry_time is not None else ob.confirmed_time
        seg = _segment_of(marker, boundary_ms)
        records.append(
            _ObRecord(
                segment=seg,
                direction=direction,
                displacement_atr=ob.displacement_atr,
                retraced=retraced,
                broke=broke,
                bars_to_retrace=bars_to_retrace,
                mfe_r=mfe_r,
                mae_r=mae_r,
                gross_r=gross_r,
                net_r=net_r,
            )
        )
    return records


# --------------------------------------------------------------------------- #
# 집계 → 셀 행
# --------------------------------------------------------------------------- #


class CellRow(BaseModel):
    """한 (심볼, TF, 세그먼트, 방향)의 census 집계."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    direction: str
    n_obs: int
    retrace_rate: float
    disp_median: float | None
    retrace_rate_lo_disp: float | None
    retrace_rate_hi_disp: float | None
    disp_retrace_delta: float | None
    """(b) 강한 변위(중앙값 초과) 되돌아옴률 − 약한 변위 되돌아옴률. **음수 = 전제 (b) 지지**."""
    median_bars_to_retrace: float | None
    """(a) 되돌아온 OB의 확정→첫 탭 봉 수 중앙값(생존곡선 요약)."""
    bars_to_retrace_lo_disp: float | None
    bars_to_retrace_hi_disp: float | None
    bars_to_retrace_delta: float | None
    """(a′) 강한 변위 되돌아오기까지 봉 수 − 약한 변위. **양수 = 강한 변위가 더 늦게 되돌아옴
    (rate가 같아도 생존편향의 시간축 증거)**."""
    n_never: int
    """안 되돌아온(never-retraced) OB 수."""
    never_frac_broke: float | None
    never_mfe_r_median: float | None
    never_mae_r_median: float | None
    never_frac_reach_tp: float | None
    """안 되돌아온 OB 중 무검열 MFE가 1.5R 이상(형성 진입이 익절선에 닿았을) 비율."""
    never_gross_r_mean: float | None
    never_net_r_mean: float | None
    """(c) 안 되돌아온 OB 형성 진입의 거래당 순 R(테이커 비용 차감). **양수 = 전제 (c) 지지**."""

    @field_validator(
        "disp_median",
        "retrace_rate_lo_disp",
        "retrace_rate_hi_disp",
        "disp_retrace_delta",
        "median_bars_to_retrace",
        "bars_to_retrace_lo_disp",
        "bars_to_retrace_hi_disp",
        "bars_to_retrace_delta",
        "never_frac_broke",
        "never_mfe_r_median",
        "never_mae_r_median",
        "never_frac_reach_tp",
        "never_gross_r_mean",
        "never_net_r_mean",
        mode="before",
    )
    @classmethod
    def _nan_to_none(cls, v: object) -> object:
        if isinstance(v, float) and pd.isna(v):
            return None
        return v


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _aggregate(symbol: str, timeframe: str, records: Sequence[_ObRecord]) -> list[CellRow]:
    rows: list[CellRow] = []
    for seg in SEGMENTS:
        for direction in DIRECTIONS:
            if seg == "full":
                group = [r for r in records if r.direction == direction]
            else:
                group = [r for r in records if r.direction == direction and r.segment == seg]
            if not group:
                continue
            n_obs = len(group)
            retrace_rate = sum(1 for r in group if r.retraced) / n_obs

            # (b) 변위 × 되돌아옴 — 변위 측정된 OB만.
            disp_pairs = [
                (r.displacement_atr, r.retraced) for r in group if r.displacement_atr is not None
            ]
            disp_median: float | None = None
            lo_rate: float | None = None
            hi_rate: float | None = None
            delta: float | None = None
            if len(disp_pairs) >= 4:
                disp_vals = [d for d, _ in disp_pairs]
                disp_median = statistics.median(disp_vals)
                lo = [ret for d, ret in disp_pairs if d <= disp_median]
                hi = [ret for d, ret in disp_pairs if d > disp_median]
                if lo and hi:
                    lo_rate = sum(1 for x in lo if x) / len(lo)
                    hi_rate = sum(1 for x in hi if x) / len(hi)
                    delta = hi_rate - lo_rate

            # (a) 되돌아오기까지의 봉 수(생존곡선 축) — 되돌아온 OB의 시각·변위 짝.
            surv_pairs = [
                (r.displacement_atr, r.bars_to_retrace)
                for r in group
                if r.bars_to_retrace is not None
            ]
            all_bars = [b for _, b in surv_pairs]
            median_bars = _median([float(b) for b in all_bars]) if all_bars else None
            bars_lo: float | None = None
            bars_hi: float | None = None
            bars_delta: float | None = None
            disp_surv = [(d, b) for d, b in surv_pairs if d is not None]
            if len(disp_surv) >= 4:
                surv_median_disp = statistics.median([d for d, _ in disp_surv])
                lo_bars = [float(b) for d, b in disp_surv if d <= surv_median_disp]
                hi_bars = [float(b) for d, b in disp_surv if d > surv_median_disp]
                if lo_bars and hi_bars:
                    bars_lo = statistics.median(lo_bars)
                    bars_hi = statistics.median(hi_bars)
                    bars_delta = bars_hi - bars_lo

            # (c) 안 되돌아온 OB 형성 진입.
            never = [r for r in group if not r.retraced]
            n_never = len(never)
            never_broke = _mean([1.0 if r.broke else 0.0 for r in never])
            mfes = [r.mfe_r for r in never if r.mfe_r is not None]
            maes = [r.mae_r for r in never if r.mae_r is not None]
            grosses = [r.gross_r for r in never if r.gross_r is not None]
            nets = [r.net_r for r in never if r.net_r is not None]
            frac_tp = sum(1 for m in mfes if m >= TAKE_PROFIT_R) / len(mfes) if mfes else None
            rows.append(
                CellRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    segment=seg,
                    direction=direction,
                    n_obs=n_obs,
                    retrace_rate=retrace_rate,
                    disp_median=disp_median,
                    retrace_rate_lo_disp=lo_rate,
                    retrace_rate_hi_disp=hi_rate,
                    disp_retrace_delta=delta,
                    median_bars_to_retrace=median_bars,
                    bars_to_retrace_lo_disp=bars_lo,
                    bars_to_retrace_hi_disp=bars_hi,
                    bars_to_retrace_delta=bars_delta,
                    n_never=n_never,
                    never_frac_broke=never_broke,
                    never_mfe_r_median=_median(mfes),
                    never_mae_r_median=_median(maes),
                    never_frac_reach_tp=frac_tp,
                    never_gross_r_mean=_mean(grosses),
                    never_net_r_mean=_mean(nets),
                )
            )
    return rows


def run_cell(task: _Task) -> list[CellRow]:
    records = _records_for_cell(task)
    if not records:
        return []
    return _aggregate(task.symbol, task.timeframe, records)


def run_census(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int,
) -> list[CellRow]:
    start_ms = parse_date_ms(start)
    end_ms = parse_date_ms(end)
    tasks = [
        _Task(harness.normalize_symbol(s), tf, start_ms, end_ms)
        for tf in timeframes
        for s in symbols
    ]
    rows: list[CellRow] = []
    if jobs and jobs != 1:
        workers = jobs if jobs > 0 else None
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for cell in ex.map(run_cell, tasks):
                rows.extend(cell)
    else:
        for task in tasks:
            rows.extend(run_cell(task))
    return rows


# --------------------------------------------------------------------------- #
# 요약 · 판정
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[CellRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[CellRow]:
    frame = pd.read_csv(path)
    return [CellRow(**rec) for rec in frame.to_dict(orient="records")]


def _symbol_mean(frame: pd.DataFrame, column: str) -> float | None:
    vals = frame[column].dropna().tolist()
    return _mean([float(v) for v in vals])


def premise_verdict(
    frame: pd.DataFrame, timeframe: str, direction: str, segment: str
) -> tuple[str, str]:
    """(tf, 방향, 세그먼트)의 전제 판정.

    전제 = 「강한 변위 OB를 재탭 엔진이 (덜/늦게 잡아) 구조적으로 놓친다」. 두 축으로 본다:
    * **(b-비율)** `disp_retrace_delta` ≤ −`NEGLIGIBLE_DELTA` (강한 변위가 되돌아옴 **비율**을
      낮춤). 이 저장소 데이터에선 대체로 무관(비율은 변위와 거의 독립).
    * **(b-시간)** `bars_to_retrace_delta` ≥ `NEGLIGIBLE_BARS` (강한 변위가 되돌아오기까지
      **시간**을 늘림 — 비율이 같아도 늦게 오면 재탭이 그 비행을 앉아서 놓친다). 이진 비율보다
      풍부한 §1(a) 생존 축이고, **이 축이 전제의 핵심 증거**다.

    전제 성립 = (b-시간 **또는** b-비율) **그리고** (c) `never_net_r_mean` > 0. 심볼당 OB
    20개 미만이면 판정 불가(대조군).

    ⚠️ (c)는 **사후 조건부**다 — 안 되돌아온 롱 OB는 정의상 가격이 올라간 것이라 형성 롱이
    이기는 게 당연하다. (c) 양수는 「놓친 돈이 실재한다」는 필요조건일 뿐 엣지가 아니다
    (진짜 검정은 §2 매칭 널). 이 함수는 전제(생존편향)의 성립만 가른다.
    """
    cell = frame[
        (frame["timeframe"] == timeframe)
        & (frame["direction"] == direction)
        & (frame["segment"] == segment)
    ]
    if cell.empty:
        return "⚠️ 판정 불가(대조군)", "표본 없음."
    n_symbols = len(cell)
    mean_obs = _symbol_mean(cell, "n_obs")
    if mean_obs is None or mean_obs < MIN_OBS_FOR_VERDICT:
        return (
            "⚠️ 판정 불가(대조군)",
            f"심볼당 OB {mean_obs:.1f} < {MIN_OBS_FOR_VERDICT}({n_symbols}심볼).",
        )
    delta = _symbol_mean(cell, "disp_retrace_delta")
    bars_delta = _symbol_mean(cell, "bars_to_retrace_delta")
    net_r = _symbol_mean(cell, "never_net_r_mean")
    retr = _symbol_mean(cell, "retrace_rate")
    if delta is None or net_r is None or bars_delta is None:
        return "⚠️ 판정 불가(대조군)", "변위/형성 표본 부족."
    b_rate = delta <= -NEGLIGIBLE_DELTA
    b_time = bars_delta >= NEGLIGIBLE_BARS
    c_ok = net_r > 0
    detail = (
        f"되돌아옴률 {retr:.1%} · (b-비율) Δ {delta:+.3f} · (b-시간) Δ봉 {bars_delta:+.1f} · "
        f"(c) 안됨 형성 net R {net_r:+.3f}(사후조건부)"
    )
    if not c_ok:
        return "(c) 전제 거짓 — 안 되돌아온 형성이 손실", detail
    if b_time and b_rate:
        return "(a) 전제 성립 — 비율·시간 양축", detail
    if b_time:
        return "(a) 전제 성립 — 시간축(늦게 되돌아옴)", detail
    if b_rate:
        return "(a) 전제 성립 — 비율축", detail
    return "(b) 전제 거짓 — 변위가 비율·시간 둘 다 안 바꿈", detail


def _round_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.columns:
        if out[col].dtype.kind == "f":
            out[col] = out[col].round(4)
    return out


def _md_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_(빈 표)_\n"
    cols = list(frame.columns)
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [header, sep]
    for _, row in frame.iterrows():
        cells = ["" if pd.isna(v) else str(v) for v in row.tolist()]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _verdict_symbol_mean_table(frame: pd.DataFrame) -> pd.DataFrame:
    """(tf, 세그먼트, 방향)별 심볼평균 요약 + 판정."""
    records: list[dict[str, object]] = []
    for tf in sorted(frame["timeframe"].unique()):
        for seg in ("is", "oos_warm"):
            for direction in DIRECTIONS:
                cell = frame[
                    (frame["timeframe"] == tf)
                    & (frame["segment"] == seg)
                    & (frame["direction"] == direction)
                ]
                if cell.empty:
                    continue
                verdict, detail = premise_verdict(frame, tf, direction, seg)
                records.append(
                    {
                        "tf": tf,
                        "seg": seg,
                        "dir": direction,
                        "symbols": len(cell),
                        "mean_n_obs": round(_symbol_mean(cell, "n_obs") or 0.0, 1),
                        "retrace_rate": round(_symbol_mean(cell, "retrace_rate") or 0.0, 4),
                        "disp_retrace_delta": _round_opt(_symbol_mean(cell, "disp_retrace_delta")),
                        "median_bars_retr": _round_opt(
                            _symbol_mean(cell, "median_bars_to_retrace")
                        ),
                        "bars_retr_delta": _round_opt(_symbol_mean(cell, "bars_to_retrace_delta")),
                        "never_net_r_mean": _round_opt(_symbol_mean(cell, "never_net_r_mean")),
                        "never_frac_reach_tp": _round_opt(
                            _symbol_mean(cell, "never_frac_reach_tp")
                        ),
                        "verdict": verdict,
                    }
                )
    return pd.DataFrame(records)


def _round_opt(v: float | None) -> float | str:
    return round(v, 4) if v is not None else ""


def build_summary_markdown(rows: Sequence[CellRow]) -> str:
    frame = rows_to_frame(rows)
    lines: list[str] = []
    lines.append("# WAN-254 §1 — 형성(돌파) 진입 전제 검증 census\n")
    lines.append(
        "형성 진입 = 확정 다음 봉 시가 시장가(테이커) · 손절 = OB 무효화(1R) · 고정 1.5R. "
        "warm/cold는 WAN-166 규약(탐지 warm · 형성 시각이 경계 이후면 oos_warm). "
        "†펀딩 0행(§1 net_r은 펀딩 무관).\n"
    )
    lines.append("## 판정 요약 (심볼평균 · 심볼당 OB≥20 게이트)\n")
    lines.append(_md_table(_verdict_symbol_mean_table(frame)))
    lines.append(
        f"\n**전제 성립** = (b-시간 `bars_retr_delta` ≥ {NEGLIGIBLE_BARS}봉 「강한 변위가 늦게 "
        f"되돌아옴」 **또는** b-비율 `disp_retrace_delta` ≤ −{NEGLIGIBLE_DELTA}) **그리고** "
        "(c) `never_net_r_mean`>0. 비율 축은 대체로 무관(비율은 변위와 거의 독립)이지만 "
        "**시간 축은 전 셀에서 강하게 양수** — 강한 변위 OB는 되돌아오기까지 훨씬 오래 걸린다"
        "(재탭이 그 비행을 앉아서 놓친다). ⚠️ (c)는 **사후 조건부**(안 되돌아온 롱=상승=롱 "
        "이익, 순환)라 엣지가 아니라 「놓친 돈의 실재」만 뜻한다 — 진짜 검정은 §2 매칭 널.\n"
    )
    lines.append("\n## 셀 원본 (심볼 × TF × 세그먼트 × 방향)\n")
    lines.append(_md_table(_round_frame(frame)))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _write(rows: Sequence[CellRow], cells_csv: Path, summary_md: Path) -> None:
    cells_csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(cells_csv, index=False)
    summary_md.write_text(build_summary_markdown(rows), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="WAN-254 §1 형성 진입 전제 census")
    parser.add_argument("--tf", default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--symbols", default=",".join(ALL_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--jobs", default="1")
    parser.add_argument("--from-csv", action="store_true", help="CSV에서 요약만 재생성")
    parser.add_argument("--cells-csv", default=str(DEFAULT_CELLS_CSV))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    args = parser.parse_args(argv)

    cells_csv = Path(args.cells_csv)
    summary_md = Path(args.summary)

    if args.from_csv:
        rows = rows_from_csv(cells_csv)
        summary_md.write_text(build_summary_markdown(rows), encoding="utf-8")
        print(f"요약만 재생성: {summary_md}")
        return

    timeframes = [t.strip() for t in args.tf.split(",") if t.strip()]
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    jobs = 0 if args.jobs in ("auto", "0") else int(args.jobs)

    rows = run_census(symbols, timeframes, start=args.start, end=args.end, jobs=jobs)
    _write(rows, cells_csv, summary_md)
    print(f"셀 {len(rows)}행 → {cells_csv}")
    print(f"요약 → {summary_md}")


if __name__ == "__main__":
    main()
