"""WAN-164 — 오늘 엔진(분리 존 + 존폭 필터 1.28)에서 롱+숏을 같이 잰다.

사용자 질문(2026-07-22): *"우리가 현재 존필터 걸린 상태에서 롱숏 같이 적용한 거는 아직
안 돌려봤잖아 그치."* 맞다. 숏을 실제로 켠 측정은 **딱 둘**뿐이고 둘 다 **병합 존**
(`combine_obs=True`) · **존폭 필터 없음**이다:

* [WAN-89](../docs/decisions/wan89.md) 숏 부검 — 병합 존, 필터 없음.
* [WAN-145](../docs/decisions/wan145.md) §3 숏 축 매칭 널 — 밴드는 오늘 것(`intrabar_live`)
  이지만 `harness.LEGACY_OB_PARAMS`로 **병합 존 고정**, 필터 없음.

존폭 필터 스위치(`max_zone_width_atr`) 자체가 [WAN-158](../docs/decisions/wan158.md)에서야
처음 생겼으므로, 필터를 켠 채 숏을 돌린 표는 애초에 존재할 수 없었다. 어제 WAN-159가 그
필터를 기본값(`1.28`)으로 올렸고, 분리 존에서 엣지를 다시 잰 WAN-151은 **숏 축을 범위에서
뺐다**(롱만 쟀다). 그래서 **오늘의 채택 엔진 위에서 롱+숏을 같이 돌린 표가 존재하지 않는다** —
이 모듈이 그 표를 낸다.

## 🚨 측정 전용 — 숏 켜기가 아니다

`short_enabled` 기본값은 `False` **유지**, `ALPHABLOCK_LIVE_TRADING=false` 유지. 숏 팔은
옵트인으로만 돈다(WAN-89/145와 같은 패턴). 이 표가 (a) 판정을 내도 숏 실제 활성화는 토대를
갈아엎는 **재-베이스라인 = 사용자 결정**이다(개발자 임의 착수 금지).

## WAN-145와 무엇이 같고 무엇이 다른가

| 축 | WAN-145(`wan145_new_band_null`) | 이 모듈 |
| -- | -- | -- |
| 존 병합(`combine_obs`) | `True`(`LEGACY_OB_PARAMS` 병합) | **`False`(분리 = 채택 기본값)** |
| 존폭 필터(`max_zone_width_atr`) | 없음(당시 미존재) | **`1.28`(채택 기본값 — 좁은 존만)** |
| 밴드(`band_bar`) | `intrabar_live` | `intrabar_live`(같음) |
| 렌즈 | `baseline` 단독 | `baseline` 단독(같음) |
| 팔 | `long_only`·`short_only`·`both` | 같음(WAN-89 정의 재사용) |
| 무력화 축 | 볼린저 | 볼린저(같음 — 게이트가 없으므로) |
| 자·창·심볼·TF·시드 | 20건 · 못 박은 창 · 6심볼 · 15m·1h · 124 | 전부 같음 |

즉 **움직인 축은 존 정의(병합→분리) + 존폭 필터(off→1.28)** 둘뿐이다. 나머지는 WAN-145와
같게 둬야 두 표를 맞댈 수 있다.

## 성과 표 + 매칭 널을 한 셀에서

이슈 §작업범위 2·3을 한 실행으로 낸다:

* **성과 표**(`PnlRow`) — 심볼 × TF × 구간 × 팔로 `total_return`·승률·거래 수·체결률·MDD
  (`harness.run_once`, 분리 OB). `both`−`long_only`로 숏의 기여를 낸다(⚠️ 동시 1포지션
  제약 때문에 `short_only` ≠ `both`의 숏 부분 — WAN-89 §주의).
* **매칭 널**(`NullRow`) — 숏 축이 무작위 진입과 구분되는지(WAN-145 §1 골격 그대로,
  무력화 축은 볼린저). 옵트인 `pool_params` 퇴화 가드는 `run_random_control_b_segment`가
  동작으로 막는다(WAN-124).

## 검산 — 다른 엔진을 돌린 게 아님을 증명

`long_only` 팔의 **널 실제 다리**(`run_random_control_b_segment`의 real 경로)와 **성과
표**(`harness.run_once`)의 `total_return`이 같아야 한다 — 둘은 독립 코드 경로인데 같은
채택 엔진(분리 존 + 필터 1.28 + 봉내 라이브)을 태우므로, 일치가 곧 "오늘 엔진을 돌렸다"는
증거다(WAN-151 검산과 같은 자리). 요약이 **일치·잡음·불일치를 다르게 찍는다**.

## 재현

```
uv run python -m backtest.wan164_short_today_engine --tf 1h --jobs 6
uv run python -m backtest.wan164_short_today_engine --tf 15m --jobs 6 --append
uv run python -m backtest.wan164_short_today_engine --from-csv    # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan70_random_control_b import run_random_control_b_segment
from backtest.wan89_short_autopsy import ARMS_BY_NAME, Arm, _buy_hold
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_PNL_CSV = REPORTS_DIR / "wan164_short_today_engine.csv"
DEFAULT_NULL_CSV = REPORTS_DIR / "wan164_short_null.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan164_short_today_engine_summary.md"

#: 못 박은 창 — WAN-111/114/145와 동일(`--years N`은 미끄러진다).
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 6심볼(WAN-111).
ALL_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "TRXUSDT",
)

#: WAN-107 공동 작업 TF.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 팔은 **WAN-89에서 가져온다**(§작업범위 1). 이름을 여기서 다시 정의하면 두 리포트가
#: 같은 라벨로 다른 설정을 돌 수 있다.
ARM_NAMES: tuple[str, ...] = ("long_only", "short_only", "both")

#: 검산·기준선 팔 — 채택 기본값 그 자체다.
LONG_ARM = "long_only"

#: 🚨 오늘 엔진 = **분리 존**(`combine_obs=False`, WAN-149). WAN-145가 `LEGACY_OB_PARAMS`
#: (병합)로 고정한 자리를 여기서 **채택 기본값**으로 되돌린 것이 이 모듈의 존재 이유다.
#: `OrderBlockParams()`의 기본값이 곧 채택 탐지 설정이라 명시하지 않는다(고정하면 WAN-149
#: 전환을 따라가지 못한다). frozen이라 모듈 상수로 공유해도 안전하다.
ADOPTED_OB_PARAMS = OrderBlockParams()

#: 공식 렌즈는 `baseline` 단독(WAN-128).
OFFICIAL_LENS = harness.BASELINE_FILL.name

#: 자 — WAN-70/84/88/124/145와 **같은 값**이라야 「판정이 바뀌었다」와 「자를 바꿨다」가 갈린다.
MIN_TRADES_FOR_VERDICT = 20
ALPHA = 0.05

#: 부트스트랩 — WAN-145와 같은 반복 수·시드(밴드·존만 다른 두 표를 맞대려면 나머지가 같아야).
BOOTSTRAP_ITERATIONS = 200
BOOTSTRAP_SEED = 124

#: 널 풀의 무력화 축 = 볼린저 off(WAN-124/145 `NEUTRALIZED_POOL_UPDATES`와 같은 값).
NEUTRALIZED_POOL_UPDATES: dict[str, object] = {"deviation_filter": None}

#: 편중 진단에서 빼 볼 심볼 — WAN-89/145에서 숏 OOS 플러스가 전부 이 심볼에 기대고 있었다.
LEAVE_OUT_SYMBOL = "ETH"

SEGMENT_ORDER: tuple[str, ...] = (harness.SEGMENT_IS, harness.SEGMENT_OOS)

#: 널 실제 다리 ≡ 성과 표 검산의 허용오차. 두 독립 경로의 부동소수 끝자리 차이는 잡음이고,
#: 그보다 크면 다른 엔진을 돌렸다는 뜻이라 갈라 찍는다(WAN-151 패턴).
CROSSCHECK_NOISE_TOL = 1e-9


# --------------------------------------------------------------------------- #
# 파라미터
# --------------------------------------------------------------------------- #


def arm_of(name: str) -> Arm:
    """WAN-89의 팔 정의를 이름으로 가져온다(정의를 이 모듈에서 다시 쓰지 않는다)."""
    return ARMS_BY_NAME[name]


def real_params(arm: Arm) -> ConfluenceParams:
    """검정 대상 = **지금 채택된 기본값 + 팔의 롱/숏 스위치**.

    ⚠️ `band_bar`·`max_zone_width_atr`을 **고정하지 않는다** — 팔은 `ConfluenceParams()`에서
    출발하므로 봉내 라이브 밴드·필터 1.28을 그대로 물려받는다(그게 오늘 엔진이다).
    """
    return arm.params()


def pool_params(arm: Arm) -> ConfluenceParams:
    """널 풀 = 실제에서 **볼린저만 끈 것**(= 존 근단 지정가에 무조건 진입).

    롱/숏 스위치·렌즈·오프셋·**존폭 필터**는 실제와 같게 둔다 — 다르면 널이 볼린저가 아니라
    팔이나 필터를 재게 된다. 필터가 남아 있으므로 널은 「좁은 존 유니버스 안에서 볼린저가
    무작위 진입 대비 값을 더하는가」를 묻는다.
    """
    return real_params(arm).model_copy(update=NEUTRALIZED_POOL_UPDATES)


def describe_engine() -> str:
    """이 리포트가 검정한 엔진의 지문 — 산출물만 봐도 어떤 엔진으로 돌았는지 드러나게."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={ADOPTED_OB_PARAMS.combine_obs}, "
        f"max_zone_width_atr={p.max_zone_width_atr}"
    )


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class _NanIsNone(BaseModel):
    """CSV 왕복에서 빈 칸(→ `NaN`)을 `None`으로 되돌리는 공통 베이스(`harness.RunRow` 가드)."""

    model_config = ConfigDict(frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _nan_to_none(cls, value: object) -> object:
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


class PnlRow(_NanIsNone):
    """한 (심볼, TF, 구간, 팔)의 성과(`harness.run_once`, 분리 OB)."""

    symbol: str
    timeframe: str
    segment: str
    arm: str
    num_bars: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    sharpe: float | None
    mean_r: float | None
    fill_rate: float | None
    eligible_setups: int | None
    num_filled: int | None
    buy_hold: float
    """구간 바이앤홀드 = 장세 라벨(WAN-89 `_buy_hold`와 **같은 함수**)."""


class NullRow(_NanIsNone):
    """한 (심볼, TF, 구간, 팔)의 매칭 널(WAN-145 `NullRow`와 같은 열)."""

    symbol: str
    timeframe: str
    segment: str
    arm: str
    fill: str
    real_total_return: float
    real_num_trades: int
    real_long: int
    real_short: int
    pool_size: int
    """볼린저 무력화 풀(체결된 후보) 크기 — 표본추출 대상 전체."""
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int
    bucket_fallback_count: int
    buy_hold: float


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    """fan-out 한 단위 = (심볼, TF) — 워커가 자기 데이터를 자기가 로드한다."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    iterations: int
    arm_names: tuple[str, ...]


@dataclass(frozen=True)
class _CellResult:
    pnl: list[PnlRow]
    null: list[NullRow]


def run_cell(task: _Task, *, log: bool = True) -> _CellResult:
    """한 (심볼, TF)의 IS/OOS × 팔 성과 + 매칭 널을 낸다.

    분리 OB(`ADOPTED_OB_PARAMS`)를 **구간마다 한 번** 탐지해 성과·널이 같은 오더블록
    유니버스를 공유한다 — 두 경로가 다른 존을 보면 검산이 무의미해진다.
    """
    market = harness.load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=True,
    )
    if market.empty or market.df_1m.empty:
        return _CellResult([], [])

    pnl_rows: list[PnlRow] = []
    null_rows: list[NullRow] = []
    for segment in harness.segments_for(oos=True):
        if segment.name not in SEGMENT_ORDER:
            continue  # 전 구간은 두 구간의 혼합이라 새 정보를 주지 않는다.
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        ob_result = harness.detect_order_blocks(window, ADOPTED_OB_PARAMS)
        buy_hold = _buy_hold(window.htf_df)

        for arm_name in task.arm_names:
            arm = arm_of(arm_name)
            cfg = arm.config(task.timeframe)
            params = real_params(arm)

            # 성과 표 — 분리 OB로 채택 엔진 그대로.
            outcome = harness.run_once(window, params=params, cfg=cfg, order_block_result=ob_result)
            m = outcome.result.metrics
            stats = outcome.stats
            pnl_rows.append(
                PnlRow(
                    symbol=task.symbol,
                    timeframe=task.timeframe,
                    segment=segment.name,
                    arm=arm_name,
                    num_bars=len(window.htf_df),
                    num_trades=m.num_trades,
                    win_rate=m.win_rate,
                    total_return=m.total_return,
                    max_drawdown=m.max_drawdown,
                    sharpe=m.sharpe,
                    mean_r=harness.mean_r(outcome.result, params.take_profit_r),
                    fill_rate=stats.fill_rate if stats else None,
                    eligible_setups=stats.eligible if stats else None,
                    num_filled=stats.filled if stats else None,
                    buy_hold=buy_hold,
                )
            )

            # 매칭 널 — 같은 OB 유니버스, 볼린저 무력화 풀.
            result = run_random_control_b_segment(
                window.htf_df,
                window.df_1m,
                task.timeframe,
                symbol=task.symbol,
                segment="IS" if segment.name == harness.SEGMENT_IS else "OOS",
                gate=arm_name,
                confluence_params=params,
                backtest_config=cfg,
                order_block_result=ob_result,
                iterations=task.iterations,
                seed=BOOTSTRAP_SEED,
                funding_rates=window.funding_rates,
                pool_params=pool_params(arm),
            )
            null_rows.append(
                NullRow(
                    symbol=task.symbol,
                    timeframe=task.timeframe,
                    segment=segment.name,
                    arm=arm_name,
                    fill=OFFICIAL_LENS,
                    real_total_return=result.real_total_return,
                    real_num_trades=result.real_num_trades,
                    real_long=result.real_long,
                    real_short=result.real_short,
                    pool_size=result.pool_size,
                    random_mean_return=result.random_mean_return,
                    random_ci_low=result.random_ci_low,
                    random_ci_high=result.random_ci_high,
                    random_p_value=result.random_p_value,
                    iterations=result.iterations,
                    bucket_fallback_count=result.bucket_fallback_count,
                    buy_hold=buy_hold,
                )
            )
            if log:
                print(
                    f"[wan164] {task.symbol} {task.timeframe} {segment.name} {arm_name}: "
                    f"pnl={m.total_return:+.4f} n={m.num_trades} "
                    f"null_real={result.real_total_return:+.4f} pool={result.pool_size} "
                    f"p={result.random_p_value}",
                    flush=True,
                )
    return _CellResult(pnl_rows, null_rows)


def _run_task_logged(task: _Task) -> _CellResult:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    arm_names: Sequence[str] = ARM_NAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    iterations: int = BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    log: bool = True,
) -> tuple[list[PnlRow], list[NullRow]]:
    """6심볼 × 2TF × IS/OOS × 3팔 격자를 돈다.

    `jobs`는 **성능 노브이지 결과 축이 아니다**(WAN-121) — (심볼, TF) 단위로만 갈라
    제출 순서대로 모으므로 직렬과 행·순서가 같다.
    """
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            iterations=iterations,
            arm_names=tuple(arm_names),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        results = [run_cell(task, log=log) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            results = list(executor.map(_run_task_logged, tasks))
    pnl: list[PnlRow] = []
    null: list[NullRow] = []
    for res in results:
        pnl.extend(res.pnl)
        null.extend(res.null)
    return pnl, null


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def pnl_to_frame(rows: Sequence[PnlRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(PnlRow.model_fields))


def null_to_frame(rows: Sequence[NullRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(NullRow.model_fields))


def pnl_from_csv(path: Path) -> list[PnlRow]:
    frame = pd.read_csv(path)
    return [PnlRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def null_from_csv(path: Path) -> list[NullRow]:
    frame = pd.read_csv(path)
    return [NullRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 집계 · 판정 (널)
# --------------------------------------------------------------------------- #


def is_significant(row: NullRow, alpha: float = ALPHA) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제>무작위평균(WAN-70/84/88/124/145와 같은 자)."""
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def eligible_rows(rows: Sequence[NullRow], *, arm: str | None = None) -> list[NullRow]:
    """유효 셀 = p값이 나왔고 실제 거래가 `MIN_TRADES_FOR_VERDICT`건 이상."""
    return [
        r
        for r in rows
        if (arm is None or r.arm == arm)
        and r.random_p_value is not None
        and r.real_num_trades >= MIN_TRADES_FOR_VERDICT
    ]


def significance_counts(rows: Sequence[NullRow], *, arm: str) -> tuple[int, int]:
    eligible = eligible_rows(rows, arm=arm)
    return sum(1 for r in eligible if is_significant(r)), len(eligible)


def per_timeframe_counts(rows: Sequence[NullRow], *, arm: str) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for timeframe in DEFAULT_TIMEFRAMES:
        scoped = [r for r in rows if r.timeframe == timeframe]
        out[timeframe] = significance_counts(scoped, arm=arm)
    return out


def verdict(rows: Sequence[NullRow], *, arm: str) -> str:
    """한 팔의 판정 문장 — (a) 구분된다 / (b) 구분 안 됨 / (c) TF·구간에 갈림.

    숫자는 전부 행에서 계산한다 — 문장에 숫자를 박아 두면 재실행 뒤 리포트가 거짓말을 한다.
    """
    sig, total = significance_counts(rows, arm=arm)
    if total == 0:
        return (
            f"`{arm}`: **⚠️ 판정 불가** — 거래 {MIN_TRADES_FOR_VERDICT}건 이상인 유효 셀이 "
            "하나도 없다(표본 부족)."
        )
    by_tf = per_timeframe_counts(rows, arm=arm)
    tf_note = " · ".join(f"{tf} {s}/{t}" for tf, (s, t) in by_tf.items() if t)
    if sig == 0:
        head = "**(b) 무작위와 구분되지 않는다**"
    elif sig == total:
        head = "**(a) 무작위와 구분된다**"
    else:
        head = "**(c) 일부 셀에만 유의성이 있다 — TF·구간에 갈린다**"
    return f"`{arm}`: 유효 셀 {total}개 중 유의 {sig}개({tf_note}) → {head}"


def short_axis_verdict(rows: Sequence[NullRow]) -> str:
    """이슈 완료기준의 (a)/(b)/(c) — **숏 축** 판정을 `short_only`·`both`를 함께 읽어 낸다."""
    counts = {arm: significance_counts(rows, arm=arm) for arm in ("short_only", "both")}
    eligible_total = sum(t for _s, t in counts.values())
    sig_total = sum(s for s, _t in counts.values())
    if eligible_total == 0:
        return "**⚠️ 판정 불가** — 숏 팔에 유효 셀이 없다(표본 부족)."
    if sig_total == 0:
        return "**(b) 숏은 오늘 엔진에서 값을 더하지 않는다** — 두 숏 팔 어디에도 유의 셀이 없다."
    if sig_total == eligible_total:
        return "**(a) 숏은 오늘 엔진에서 값을 더한다** — 숏 팔의 모든 유효 셀이 유의하다."
    return "**(c) TF·구간에 갈린다** — 일부 숏 셀에만 유의성이 있다."


# --------------------------------------------------------------------------- #
# 집계 (성과)
# --------------------------------------------------------------------------- #


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pnl_cell(frame: pd.DataFrame, timeframe: str, segment: str, arm: str) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe) & (frame["segment"] == segment) & (frame["arm"] == arm)
    ]


def _mean_or_none(frame: pd.DataFrame, column: str) -> float | None:
    return None if frame.empty else float(frame[column].mean())


def leave_one_out(
    pnl: pd.DataFrame, timeframe: str, segment: str, arm: str
) -> list[tuple[str, float]]:
    """심볼을 하나씩 빼며 낸 평균 — 한 심볼이 표를 떠받치고 있는지 본다(WAN-89와 같은 자)."""
    cell = _pnl_cell(pnl, timeframe, segment, arm)
    if len(cell) < 2:
        return []
    out: list[tuple[str, float]] = []
    for symbol in sorted(cell["symbol"].unique()):
        rest = cell[cell["symbol"] != symbol]
        out.append((str(symbol), float(rest["total_return"].mean())))
    return sorted(out, key=lambda pair: pair[1])


# --------------------------------------------------------------------------- #
# 검산 (널 실제 다리 ≡ 성과 표)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CrossCheck:
    """`long_only` 널 실제 다리 vs 성과 표 `total_return`의 대조 결과."""

    compared: int
    max_abs_diff: float | None
    mismatches: list[tuple[str, str, str, float, float]]
    """(심볼, TF, 구간, 널실제, 성과) — 잡음 허용오차를 넘은 셀만."""


def cross_check(pnl: Sequence[PnlRow], null: Sequence[NullRow]) -> CrossCheck:
    """두 독립 경로가 같은 채택 엔진을 돌렸는지 확인한다(WAN-151 검산 패턴).

    ⚠️ 검산은 `long_only`만 한다 — 숏 팔은 `run_once`(포지션 회계)와 널 실제 다리
    (`_sequence_and_cost`)가 동시 1포지션 시퀀싱을 정확히 같은 방식으로 처리한다는 보장이
    없어(롱·숏이 슬롯을 다투는 순서 규칙이 두 경로에서 갈릴 여지) 검산 대상이 아니다.
    롱 팔이 일치하면 "분리 존 + 필터 1.28 + 봉내 라이브"를 돌렸다는 증거로 충분하다.
    """
    null_by_key = {(r.symbol, r.timeframe, r.segment): r for r in null if r.arm == LONG_ARM}
    diffs: list[float] = []
    mismatches: list[tuple[str, str, str, float, float]] = []
    compared = 0
    for row in pnl:
        if row.arm != LONG_ARM:
            continue
        match = null_by_key.get((row.symbol, row.timeframe, row.segment))
        if match is None:
            continue
        compared += 1
        diff = abs(row.total_return - match.real_total_return)
        diffs.append(diff)
        if diff > CROSSCHECK_NOISE_TOL:
            mismatches.append(
                (
                    _short(row.symbol),
                    row.timeframe,
                    row.segment,
                    match.real_total_return,
                    row.total_return,
                )
            )
    return CrossCheck(compared, max(diffs) if diffs else None, mismatches)


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value * 100:+.2f}%"


def _rate(value: float | None) -> str:
    return "—" if value is None or pd.isna(value) else f"{value * 100:.2f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.{digits}f}"


def _pnl_table(pnl: pd.DataFrame) -> list[str]:
    lines = [
        "| TF | 구간 | 팔 | return% | MDD% | 승률 | 거래 | 체결률 | 바이앤홀드 |",
        "| -- | ---- | -- | ------- | ---- | ---- | ---- | ------ | ---------- |",
    ]
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in SEGMENT_ORDER:
            for arm in ARM_NAMES:
                cell = _pnl_cell(pnl, timeframe, segment, arm)
                if cell.empty:
                    continue
                lines.append(
                    f"| {timeframe} | {segment} | `{arm}` | "
                    f"{_pct(_mean_or_none(cell, 'total_return'))} | "
                    f"{_rate(_mean_or_none(cell, 'max_drawdown'))} | "
                    f"{_rate(_mean_or_none(cell, 'win_rate'))} | "
                    f"{_num(_mean_or_none(cell, 'num_trades'), 1)} | "
                    f"{_rate(_mean_or_none(cell, 'fill_rate'))} | "
                    f"{_pct(_mean_or_none(cell, 'buy_hold'))} |"
                )
    return lines


def _short_contribution(pnl: pd.DataFrame) -> list[str]:
    """`both` − `long_only` = 롱과 함께 돌릴 때 숏의 기여(WAN-89 §주의대로 명시)."""
    lines = [
        "⚠️ **`short_only` ≠ `both`의 숏 부분** — 동시 1포지션 제약 때문에 롱이 슬롯을 잡고 "
        "있으면 숏 셋업이 스킵된다. 롱과 함께 돌릴 때의 실제 숏 기여는 아래 델타로 본다.",
        "",
    ]
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in SEGMENT_ORDER:
            both = _mean_or_none(_pnl_cell(pnl, timeframe, segment, "both"), "total_return")
            long_only = _mean_or_none(
                _pnl_cell(pnl, timeframe, segment, "long_only"), "total_return"
            )
            if both is None or long_only is None:
                continue
            lines.append(
                f"- **{timeframe} {segment}**: `both`−`long_only` = "
                f"**{_pct(both - long_only)}p** ({_pct(long_only)} → {_pct(both)})"
            )
    return lines


def _leave_one_out_lines(pnl: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        for arm_name in ("short_only", "both"):
            drops = leave_one_out(pnl, timeframe, harness.SEGMENT_OOS, arm_name)
            if not drops:
                continue
            worst_symbol, worst_mean = drops[0]
            base = _mean_or_none(
                _pnl_cell(pnl, timeframe, harness.SEGMENT_OOS, arm_name), "total_return"
            )
            negatives = [s for s, v in drops if v < 0]
            if not negatives:
                note = " — 어느 하나를 빼도 부호가 유지된다"
            elif len(negatives) == 1:
                note = f" — **{_short(negatives[0])} 하나만 빼도 마이너스**"
            else:
                note = (
                    f" — **{', '.join(_short(s) for s in negatives)} 중 어느 하나만 빼도 마이너스**"
                )
            lines.append(
                f"- **{timeframe} `{arm_name}`**: {_pct(base)} → 최악 {_pct(worst_mean)}"
                f"({_short(worst_symbol)} 제외){note}"
            )
    return lines


def cell_table(rows: Sequence[NullRow], *, arm: str) -> str:
    """한 팔의 셀별 널 표(실제/무작위평균/p값/거래수)."""
    header = (
        "| 심볼 | TF | 구간 | 실제수익 | n | L/S | 풀 | 무작위평균 | 95% CI | p | 유의 |\n"
        "| -- | -- | -- | --: | --: | -- | --: | --: | -- | --: | -- |"
    )
    scoped = sorted(
        (r for r in rows if r.arm == arm),
        key=lambda r: (
            DEFAULT_TIMEFRAMES.index(r.timeframe) if r.timeframe in DEFAULT_TIMEFRAMES else 9,
            SEGMENT_ORDER.index(r.segment) if r.segment in SEGMENT_ORDER else 9,
            r.symbol,
        ),
    )
    body = []
    for r in scoped:
        ci = (
            f"[{r.random_ci_low:.3f}, {r.random_ci_high:.3f}]"
            if r.random_ci_low is not None and r.random_ci_high is not None
            else "—"
        )
        thin = r.real_num_trades < MIN_TRADES_FOR_VERDICT
        mark = "표본부족" if thin else ("**✓**" if is_significant(r) else "")
        body.append(
            f"| {_short(r.symbol)} | {r.timeframe} | {r.segment} | "
            f"{r.real_total_return * 100:+.2f}% | {r.real_num_trades} | "
            f"{r.real_long}/{r.real_short} | {r.pool_size} | "
            f"{'—' if r.random_mean_return is None else f'{r.random_mean_return * 100:+.2f}%'} | "
            f"{ci} | "
            f"{'—' if r.random_p_value is None else f'{r.random_p_value:.3f}'} | {mark} |"
        )
    return header + "\n" + "\n".join(body)


def _cross_check_lines(check: CrossCheck) -> list[str]:
    if check.compared == 0:
        return ["검산할 `long_only` 셀이 없다."]
    if not check.mismatches:
        return [
            f"✅ **일치** — `long_only` {check.compared}셀에서 널 실제 다리와 성과 표 "
            f"`total_return`의 최대 절대차 **{check.max_abs_diff:.2e}**(부동소수 잡음, "
            f"허용오차 {CROSSCHECK_NOISE_TOL:.0e} 이내). 두 독립 경로가 같은 채택 엔진"
            "(분리 존 + 필터 1.28 + 봉내 라이브)을 돌렸다는 증거다.",
        ]
    lines = [
        f"🚨 **불일치 {len(check.mismatches)}셀** — 두 경로가 다른 엔진을 돌렸다(최대 절대차 "
        f"{check.max_abs_diff:.2e}). 아래 셀을 확인하라:",
        "",
        "| 심볼 | TF | 구간 | 널실제 | 성과 |",
        "| -- | -- | -- | --: | --: |",
    ]
    for sym, tf, seg, null_v, pnl_v in check.mismatches:
        lines.append(f"| {sym} | {tf} | {seg} | {_pct(null_v)} | {_pct(pnl_v)} |")
    return lines


def build_summary_markdown(
    pnl_rows: Sequence[PnlRow], null_rows: Sequence[NullRow], *, pnl_csv: Path, null_csv: Path
) -> str:
    pnl = pnl_to_frame(pnl_rows)
    check = cross_check(pnl_rows, null_rows)
    lines = [
        "# WAN-164 — 오늘 엔진(분리 존 + 존폭 필터 1.28)에서 롱+숏 재검",
        "",
        "**성격** 측정 전용. `short_enabled` 기본값·토대를 바꾸지 않는다"
        "(실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지). 렌즈 `baseline` 단독(WAN-128), "
        f"창 못 박음({DEFAULT_START}~{DEFAULT_END}), 6심볼(WAN-111), 15m·1h(WAN-107).",
        "",
        "## 이 리포트가 검정한 엔진",
        "",
        f"**지금 채택된 기본값 그대로** — `{describe_engine()}` + 펀딩비 반영. 전략 파라미터는 "
        "하나도 바꾸지 않았다(검증 전용).",
        "",
        "> 🚨 **WAN-145와 다른 두 축**: 존 정의를 **병합(`LEGACY_OB_PARAMS`) → 분리**"
        "(`combine_obs=False`, WAN-149)로, 존폭 필터를 **없음 → `1.28`**(WAN-159)로 되돌렸다. "
        "나머지(밴드·렌즈·팔·자·창·시드)는 WAN-145와 같다.",
        "",
        "> ⚠️ **OOS 수치를 WAN-155/161과 나란히 인용하지 말 것 — 구간 컨벤션이 다르다.** 이 "
        "모듈은 WAN-89/145/151 널 계열처럼 **IS/OOS를 먼저 자르고 각 조각에서 따로 탐지·"
        "체결**한다(`harness.slice_market` — OOS는 차갑게 시작, 누수 없음). 표준 CLI "
        "`backtest.run --oos`와 같은 값이다. 반면 WAN-155/161은 **전체 창에서 후보를 만든 뒤 "
        "시각으로 분리**해 OOS가 IS 시절 존을 물려받는다(더 낙관적 · 거래 수가 더 많다). **IS는 "
        "두 컨벤션이 비트로 같고 OOS만 갈린다**(예: 롱 1h OOS 1.5R — 이 모듈 심볼평균 vs WAN-161 "
        "+10.23%). 어느 쪽도 버그가 아니라 다른 OOS 질문이다.",
        "",
        f"재현: `uv run python -m backtest.wan164_short_today_engine` (요약만: `--from-csv`). "
        f"원자료: `{pnl_csv}`(성과) · `{null_csv}`(널).",
        "",
        "## 1. 성과 표 — 팔 × TF × 구간 (공식 렌즈 `baseline`)",
        "",
        *_pnl_table(pnl),
        "",
        "📌 **장세 라벨은 새로 정의하지 않았다** — `바이앤홀드` 열이 곧 라벨이고, IS/OOS "
        "분할을 그대로 쓴다(WAN-89/139와 공유하는 유일한 정의).",
        "",
        "### 숏의 기여 (`both` − `long_only`)",
        "",
        *_short_contribution(pnl),
        "",
        "## 2. 심볼 편중 — OOS leave-one-out (한 심볼을 빼면 얼마나 남나)",
        "",
        "이 저장소의 플러스는 반복적으로 ETH 하나가 만들었다(WAN-89/145 숏 OOS 플러스도 전부 "
        "ETH였다). 오늘 엔진 숏도 그런지 확인한다.",
        "",
        *(_leave_one_out_lines(pnl) or ["행이 없다."]),
        "",
        "## 3. 매칭 널 — 숏 축이 무작위 진입과 구분되는가",
        "",
        "무력화 축은 **볼린저**(게이트가 없으므로 — WAN-124 제약). `p` = 무작위 반복 중 실제 "
        "총수익률 이상을 낸 비율(단측). `풀` = 볼린저를 끈 존-단독 후보 수(존폭 필터는 실제·널 "
        "양쪽에 **같이** 걸려 있다). 널 정의는 `backtest/wan70_random_control_b.py` 참고.",
        "",
        "### `long_only` (기준선 · 검산 대상)",
        "",
        cell_table(null_rows, arm="long_only"),
        "",
        "### `short_only` (롱을 빼고 숏만)",
        "",
        cell_table(null_rows, arm="short_only"),
        "",
        "### `both` (롱+숏 — 동시 1포지션 슬롯을 다툰다)",
        "",
        cell_table(null_rows, arm="both"),
        "",
        "## 4. 검산 — 다른 엔진을 돌린 게 아님을 증명",
        "",
        *_cross_check_lines(check),
        "",
        "## 결론",
        "",
        "### 롱 축 (기준선) — 새 존/필터에서 「엣지 없음」이 유지되는가",
        "",
        verdict(null_rows, arm=LONG_ARM),
        "",
        "### 숏 축 — 오늘 엔진에서 처음 재는 것이다",
        "",
        verdict(null_rows, arm="short_only"),
        "",
        verdict(null_rows, arm="both"),
        "",
        f"**숏 축 종합 판정**: {short_axis_verdict(null_rows)}",
        "",
        "⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151)을 뒤집는 것으로 인용 금지** — 숏 축 판정은 "
        "롱 축 「엣지 없음」의 반박이 아니다. 저 판정들은 **롱의 RSI 타이밍·볼린저**를 물어 얻은 "
        "답이고, 이 표의 숏 행은 **처음 묻는 질문**이다(다른 질문).",
        "",
        "⚠️ **전부 `baseline`(낙관) 렌즈 위의 값이다** — 「닿으면 체결」 · 큐 우선순위 미모델링. "
        "유의 셀이 나와도 그 상한 위의 값이고, 숏 축 체결 보수화(`pen_5bp`)는 이 표에서 안 쟀다"
        "(필요하면 별도 이슈). 실제 해소는 틱·호가 데이터(WAN-98, Canceled) 소관이다.",
        "",
        "⚠️ **기본값·토대 불변**(`short_enabled=False` 유지 · `ALPHABLOCK_LIVE_TRADING=false` "
        "유지). (a) 판정이 나와도 숏 실제 활성화는 **별도 재-베이스라인 결정 이슈**(사용자 결정)"
        "이고 개발자 임의 착수 금지다.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-164 오늘 엔진 롱+숏 재검")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--arms", type=str, default=",".join(ARM_NAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-pnl", type=Path, default=DEFAULT_PNL_CSV)
    parser.add_argument("--out-null", type=Path, default=DEFAULT_NULL_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 CSV에서 이번 실행 TF만 교체해 덧붙인다(TF를 나눠 돌릴 때).",
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_pnl, out_null, out_md = Path(args.out_pnl), Path(args.out_null), Path(args.out_md)
    if args.from_csv:
        pnl_rows = pnl_from_csv(out_pnl)
        null_rows = null_from_csv(out_null)
        print(f"[wan164] CSV에서 성과 {len(pnl_rows)}행 · 널 {len(null_rows)}행 로드 — 재실행 없음")
    else:
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        pnl_rows, null_rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=timeframes,
            arm_names=tuple(a.strip() for a in str(args.arms).split(",") if a.strip()),
            start=args.start,
            end=args.end,
            iterations=args.iterations,
            jobs=args.jobs,
        )
        if args.append and out_pnl.exists() and out_null.exists():
            keep = set(timeframes)
            pnl_rows = [r for r in pnl_from_csv(out_pnl) if r.timeframe not in keep] + list(
                pnl_rows
            )
            null_rows = [r for r in null_from_csv(out_null) if r.timeframe not in keep] + list(
                null_rows
            )
        out_pnl.parent.mkdir(parents=True, exist_ok=True)
        pnl_to_frame(pnl_rows).to_csv(out_pnl, index=False)
        null_to_frame(null_rows).to_csv(out_null, index=False)
        print(f"[wan164] 성과 {len(pnl_rows)}행 → {out_pnl} · 널 {len(null_rows)}행 → {out_null}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(pnl_rows, null_rows, pnl_csv=out_pnl, null_csv=out_null),
        encoding="utf-8",
    )
    print(f"[wan164] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
